"""Dispatch isolated package workflow runs and collect their verified outputs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from tools import github_api, publish, recipes


WORKFLOW = 'build-package.yml'
REPOSITORY = publish.REPOSITORY


def select(plan, name):
    selected = [package for package in plan['packages'] if package['pkgbase'] == name and not package['reuse']]
    if len(selected) != 1:
        raise ValueError('package is not a changed publication input')
    return {**plan, 'packages': selected}


def title(plan, name):
    return f"Build {name} / {plan['run_id']}.{plan['run_attempt']}"


def validate_run(record, plan, name):
    if (record['path'] != '.github/workflows/' + WORKFLOW
            or record['head_sha'] != plan['head'] or record['head_branch'] != 'main'
            or record['event'] != 'workflow_dispatch' or record['display_title'] != title(plan, name)):
        raise ValueError('package workflow identity mismatch')
    if record['conclusion'] != 'success':
        raise ValueError('package workflow did not succeed: ' + name)


def prepare(directory, name, parent, attempt):
    record = github_api.api(f'repos/{REPOSITORY}/actions/runs/{parent}')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if (record['path'] != '.github/workflows/publish.yml' or record['head_sha'] != head
            or record['head_branch'] != 'main' or record['run_attempt'] != int(attempt)):
        raise ValueError('publication workflow identity mismatch')
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


def collect(directory, timeout=10800):
    plan = json.loads((directory / 'publication-plan.json').read_text())['expected']
    pending = {package['pkgbase'] for package in plan['packages'] if not package['reuse']}
    for name in sorted(pending):
        github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches', 'POST', {
            'ref': 'main', 'inputs': {'package': name, 'publication_run': plan['run_id'], 'publication_attempt': plan['run_attempt']}})
        print('Dispatched ' + title(plan, name), flush=True)
    output = directory / 'unsigned'
    output.mkdir()
    evidence = {**{key: value for key, value in plan.items() if key != 'packages'}, 'packages': []}
    deadline = time.monotonic() + timeout
    failures = []
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError('package workflows still pending: ' + ', '.join(sorted(pending)))
        runs = github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&head_sha={plan["head"]}&per_page=100')['workflow_runs']
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
            publish.validate_unsigned(unpacked, select(plan, name))
            evidence['packages'].extend(json.loads((unpacked / 'native-evidence.json').read_text())['packages'])
            for filename in publish.filenames(select(plan, name)['packages'][0]):
                if (output / filename).exists():
                    raise ValueError('duplicate package output')
                shutil.copyfile(unpacked / filename, output / filename)
        if pending:
            time.sleep(30)
    if failures:
        raise RuntimeError('Independent package builds failed: ' + ', '.join(failures))
    (output / 'native-evidence.json').write_bytes(github_api.canonical(evidence))
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
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory, args.package, args.publication_run, args.publication_attempt)
    else:
        collect(args.directory)


if __name__ == '__main__':
    main()
