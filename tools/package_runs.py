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
    if kind == 'reviewed-harness':
        from tools import reviewed_harness
        plan = update.load(directory / 'candidate.json')
        if str(plan['run_id']) != str(parent) or str(plan['run_attempt']) != str(attempt):
            raise ValueError('reviewed parent transport mismatch')
        reviewed_harness.verify_authority(plan, executing_child=True)
        update.extract_tree(directory / 'bundle.tar', directory / 'frozen', {'recipes', 'bundle.json'})
        bundle = update.load(directory / 'frozen/bundle.json')
        if bundle != plan:
            raise ValueError('reviewed bundle differs from plan')
        selected = select(plan, name)
        import tempfile
        with tempfile.TemporaryDirectory(prefix='reviewed-child-', dir=reviewed_harness.ROOT.parent) as work:
            accepted = reviewed_harness.reconstruct(plan, Path(work))
            target = directory / 'input'
            target.mkdir()
            package = selected['packages'][0]
            # Transport recipes are not authority: copy independently exported C.
            recipes.copy_recipe(accepted / package['recipe_dir'], target / package['recipe_dir'])
        update.dump(target / 'bundle.json', selected)
        for variable in ('ARCH_BUILD_CACHE', 'ARCH_PACKAGE_CACHE', 'ARCH_NATIVE_CACHE'):
            os.environ.pop(variable, None)
        return
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
        if bundle['packages'] != plan['packages']:
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
    if kind == 'reviewed-harness':
        return collect_reviewed(directory, timeout)
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
    deadline = time.monotonic() + timeout
    failures = []
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError('package workflows still pending: ' + ', '.join(sorted(pending)))
        runs = github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&head_sha={plan["base"]}&per_page=100')['workflow_runs']
        for name in sorted(pending):
            matches = [record for record in runs if record['display_title'] == title(plan, name)]
            if len(matches) > 1:
                raise ValueError('duplicate package workflow dispatch')
            if not matches or matches[0]['status'] != 'completed':
                continue
            record = matches[0]
            print(name + ': ' + record['html_url'] + ' (' + record['conclusion'] + ')', flush=True)
            pending.remove(name)
            if record['conclusion'] != 'success':
                failures.append(name)
                continue
            validate_run(record, plan, name)
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


def collect_reviewed(directory, timeout):
    from tools import reviewed_harness
    plan = update.load(directory / 'candidate.json')
    reviewed_harness.executing_parent(plan)
    reviewed_harness.verify_authority(plan)
    if {package['pkgbase'] for package in plan['packages']} != reviewed_harness.PACKAGES:
        raise ValueError('reviewed collection requires seven packages')
    pending = set(reviewed_harness.PACKAGES)
    for name in sorted(pending):
        reviewed_harness.verify_authority(plan)
        github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches', 'POST', {
            'ref': reviewed_harness.TOOLS_REF,
            'inputs': {'package': name, 'publication_run': str(plan['run_id']), 'publication_attempt': str(plan['run_attempt']), 'kind': 'reviewed-harness'}})
    output = directory / 'unsigned'
    output.mkdir()
    evidence = {**{key: value for key, value in plan.items() if key != 'packages'}, 'packages': [], 'package_runs': []}
    deadline = time.monotonic() + timeout
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError('reviewed package runs still pending')
        runs = github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&head_sha={plan["tools_sha"]}&per_page=100')['workflow_runs']
        if len(runs) >= 100:
            raise ValueError('ambiguous bounded workflow listing')
        for name in sorted(pending):
            matches = [run for run in runs if run['display_title'] == title(plan, name)]
            if len(matches) > 1:
                raise ValueError('duplicate reviewed package dispatch')
            if not matches or matches[0]['status'] != 'completed':
                continue
            run = matches[0]
            mapping = {'pkgbase': name, 'run_id': str(run['id']), 'run_attempt': str(run['run_attempt'])}
            reviewed_harness.protected_attempt(plan, mapping['run_id'], mapping['run_attempt'], child=name, durable=True)
            artifact = directory / ('download-' + name)
            artifact_name = f"reviewed-package-{name}-{mapping['run_attempt']}"
            subprocess.run(['gh', 'run', 'download', mapping['run_id'], '--repo', REPOSITORY, '--name', artifact_name, '--dir', str(artifact)],
                           env={**os.environ, 'GH_TOKEN': os.environ['GITHUB_TOKEN']}, check=True)
            unpacked = directory / ('verified-' + name)
            publish.safe_extract(artifact / 'unsigned.tar', unpacked)
            selected = select(plan, name)
            receipt = update.load(unpacked / 'native-evidence.json')
            receipt['package_runs'] = [mapping]
            update.dump(unpacked / 'native-evidence.json', receipt)
            reviewed_harness.validate_outputs(selected, unpacked)
            evidence['packages'].extend(receipt['packages'])
            evidence['package_runs'].append(mapping)
            for package in receipt['packages']:
                for file in package['files']:
                    target = output / file['filename']
                    if target.exists():
                        raise ValueError('colliding reviewed package output')
                    shutil.copyfile(unpacked / file['filename'], target)
            pending.remove(name)
        if pending:
            time.sleep(30)
    update.dump(output / 'native-evidence.json', evidence)
    reviewed_harness.validate_outputs(plan, output)
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
    parser.add_argument('--kind', choices=('publication', 'candidate', 'reviewed-harness'), default='publication')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory, args.package, args.publication_run, args.publication_attempt, args.kind)
    else:
        collect(args.directory, kind=args.kind)


if __name__ == '__main__':
    main()
