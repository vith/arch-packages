"""Dispatch isolated package workflow runs and collect their verified outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from tools import github_api, publish, recipes, update


WORKFLOW = 'build-package.yml'
REPOSITORY = publish.REPOSITORY


def select(plan, name):
    selected = [package for package in plan['packages'] if package['pkgbase'] == name and not package.get('reuse', False)]
    if len(selected) != 1:
        raise ValueError('package is not a changed publication input')
    return {**plan, 'packages': selected}


def title(plan, name):
    return f"Build {name} / {plan['run_id']}.{plan['run_attempt']}"


def validate_run(record, plan, name):
    if (record['path'] != '.github/workflows/' + WORKFLOW
            or record['head_sha'] != plan.get('base', plan['head']) or record['head_branch'] != 'main'
            or record['event'] != 'workflow_dispatch' or record['display_title'] != title(plan, name)):
        raise ValueError('package workflow identity mismatch')
    if record['conclusion'] != 'success':
        raise ValueError('package workflow did not succeed: ' + name)


def prepare(directory, name, parent, attempt, kind='publication'):
    record = github_api.api(f'repos/{REPOSITORY}/actions/runs/{parent}')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    workflow = 'candidate.yml' if kind == 'candidate' else 'publish.yml'
    if (record['path'] != '.github/workflows/' + workflow or record['head_sha'] != head
            or record['head_branch'] != 'main' or record['run_attempt'] != int(attempt)):
        raise ValueError('publication workflow identity mismatch')
    if kind == 'candidate':
        plan = update.load(directory / 'candidate.json')
        if plan['base'] != head or plan['run_id'] != parent or plan['run_attempt'] != attempt:
            raise ValueError('candidate plan identity mismatch')
        selected = select(plan, name)
        update.extract_tree(directory / 'bundle.tar', directory / 'frozen', {'recipes', 'bundle.json'})
        bundle = update.load(directory / 'frozen/bundle.json')
        identity = ('schema', 'repository', 'base', 'head', 'run_id', 'run_attempt',
                    'image', 'harness_sha', 'recipe_pins', 'previous_recipe_pins', 'packages')
        if any(bundle[key] != plan[key] for key in identity):
            raise ValueError('candidate bundle differs from plan')
        target = directory / 'input'
        target.mkdir()
        package = selected['packages'][0]
        recipes.copy_recipe(directory / 'frozen' / package['recipe_dir'], target / package['recipe_dir'])
        (target / 'bundle.json').write_bytes(github_api.canonical({**bundle, 'packages': selected['packages']}))
        return
    plan = json.loads((directory / 'publication-plan.json').read_text())['expected']
    if plan['head'] != head or plan['run_id'] != parent or plan['run_attempt'] != attempt:
        raise ValueError('publication plan identity mismatch')
    selected = select(plan, name)
    package = selected['packages'][0]
    pins = recipes.materialize(publish.ROOT, head, REPOSITORY, publish.extract_tree)
    if pins != plan['recipe_pins']:
        raise ValueError('publication recipe pins differ')
    target = directory / 'input'
    target.mkdir()
    recipes.copy_recipe(publish.ROOT / package['recipe_dir'], target / package['recipe_dir'])
    selected['packages'] = [{key: package[key] for key in ('pkgbase', 'recipe_commit', 'recipe_dir', 'lock', 'policy', 'input_digest')}]
    (target / 'bundle.json').write_bytes(github_api.canonical(selected))
    scope = hashlib.sha256(github_api.canonical({'image': plan['image'], 'harness': plan['harness_sha']})).hexdigest()[:24]
    prefix = f'trusted-build-v1-linux-x86_64-{name}-{scope}-'
    input_prefix = prefix + package['input_digest'] + '-'
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('cache-prefix=' + prefix + '\n')
        output.write('cache-input-prefix=' + input_prefix + '\n')
        output.write('cache-key=' + input_prefix + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '\n')


def collect(directory, timeout=10800, kind='publication'):
    candidate = kind == 'candidate'
    plan = update.load(directory / 'candidate.json') if candidate else json.loads((directory / 'publication-plan.json').read_text())['expected']
    pending = {package['pkgbase'] for package in plan['packages'] if not package.get('reuse', False)}
    for name in sorted(pending):
        github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches', 'POST', {
            'ref': 'main', 'inputs': {'package': name, 'publication_run': plan['run_id'], 'publication_attempt': plan['run_attempt'], 'kind': kind}})
        print('Dispatched ' + title(plan, name), flush=True)
    output = directory / 'unsigned'
    output.mkdir()
    evidence = {**{key: value for key, value in plan.items() if key != 'packages'}, 'packages': []}
    if candidate:
        evidence['package_runs'] = []
    deadline = time.monotonic() + timeout
    failures = []
    observed = {}
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError('package workflows still pending: ' + ', '.join(sorted(pending)))
        runs = github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&head_sha={plan["base"]}&per_page=100')['workflow_runs']
        for name in sorted(pending):
            matches = [record for record in runs if record['display_title'] == title(plan, name)]
            if len(matches) > 1:
                raise ValueError('duplicate package workflow dispatch')
            if not matches:
                continue
            record = matches[0]
            state = (record['id'], record['run_attempt'], record['status'], record['conclusion'])
            if observed.get(name) != state:
                progress = record['status']
                if progress == 'completed':
                    progress += ': ' + record['conclusion']
                print(name + ': ' + record['html_url'] + ' (' + progress + ')', flush=True)
                observed[name] = state
            if record['status'] != 'completed':
                continue
            pending.remove(name)
            if record['conclusion'] != 'success':
                failures.append(name)
                continue
            validate_run(record, plan, name)
            if candidate:
                evidence['package_runs'].append({
                    'pkgbase': name, 'run_id': str(record['id']),
                    'run_attempt': str(record['run_attempt'])})
            artifact = directory / ('download-' + name)
            env = {**os.environ, 'GH_TOKEN': os.environ['GITHUB_TOKEN']}
            subprocess.run(['gh', 'run', 'download', str(record['id']), '--repo', REPOSITORY, '--name', 'package-' + name, '--dir', str(artifact)], env=env, check=True)
            unpacked = directory / ('verified-' + name)
            publish.safe_extract(artifact / 'unsigned.tar', unpacked)
            if candidate:
                update.validate_build(select(plan, name), unpacked, report=False)
            else:
                publish.validate_unsigned(unpacked, select(plan, name))
            evidence['packages'].extend(json.loads((unpacked / 'native-evidence.json').read_text())['packages'])
            receipt = json.loads((unpacked / 'native-evidence.json').read_text())
            filenames = [file['filename'] for package in receipt['packages'] for file in package['files']]
            for filename in filenames:
                if (output / filename).exists():
                    raise ValueError('duplicate package output')
                shutil.copyfile(unpacked / filename, output / filename)
        if pending:
            time.sleep(30)
    if failures:
        raise RuntimeError('Independent package builds failed: ' + ', '.join(failures))
    (output / 'native-evidence.json').write_bytes(github_api.canonical(evidence))
    if candidate:
        update.validate_build(plan, output, report=False)
    else:
        publish.validate_unsigned(output, plan)
    with tarfile.open(directory / 'unsigned.tar', 'w') as archive:
        for path in sorted(output.iterdir()):
            archive.add(path, arcname=path.name, recursive=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'collect'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--package')
    parser.add_argument('--publication-run')
    parser.add_argument('--publication-attempt')
    parser.add_argument('--kind', choices=('publication', 'candidate'), default='publication')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory, args.package, args.publication_run, args.publication_attempt, args.kind)
    else:
        collect(args.directory, kind=args.kind)


if __name__ == '__main__':
    main()
