"""Authorize isolated package workers, recover original bytes, and collect without rebuilding."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile

from tools import build_store, github_api, publish, recipe_candidates, recipe_state, recipes, update

WORKFLOW = 'build-package.yml'
REPOSITORY = publish.REPOSITORY


def select(plan, name):
    selected = [p for p in plan['packages'] if p['pkgbase'] == name and not p.get('reuse', False)]
    if len(selected) != 1:
        raise ValueError('package is not an explicitly authorized compilation input')
    return {**plan, 'packages': selected}


def title(plan, name):
    package = select(plan, name)['packages'][0]
    return f"Build {name} / {plan['run_id']}.{plan['run_attempt']} / {package['input_digest']}"


def artifact_name(name, run, attempt):
    return f'package-{name}-{run}-{attempt}'


def rows(path, key):
    for page in range(1, 101):
        separator = '&' if '?' in path else '?'
        result = github_api.api(path + f'{separator}per_page=100&page={page}')[key]
        if not isinstance(result, list) or len(result) > 100:
            raise ValueError('invalid paginated package worker response')
        yield from result
        if len(result) < 100:
            return
    raise ValueError('package worker history exceeds recovery bound; refusing compilation')


class OriginalTransportRecoveryRequired(RuntimeError):
    def __init__(self, record, producer, artifact, directory):
        self.record = record
        self.producer = producer
        self.artifact = artifact
        self.directory = directory
        super().__init__(f"Original compilation {producer['run_id']}.{producer['run_attempt']} requires accepted-main transport attestation; no recompilation permitted")


def download_artifact(run, attempt, name, directory, legacy=False, expected=None):
    artifacts = rows(f'repos/{REPOSITORY}/actions/runs/{run}/artifacts', 'artifacts')
    expected_name = 'package-' + name if legacy else artifact_name(name, run, attempt)
    matches = [a for a in artifacts if a['name'] == expected_name]
    if len(matches) != 1 or matches[0]['expired']:
        raise RuntimeError(f'Original successful compilation {run}.{attempt} has no recoverable archive; refusing recompilation')
    artifact = matches[0]
    if expected is not None and any(artifact.get(key) != value for key, value in expected.items()):
        raise ValueError('transport recovery artifact differs from explicitly selected immutable ID/digest')
    producer_run = github_api.api(f'repos/{REPOSITORY}/actions/runs/{run}/attempts/{attempt}')
    associated = artifact.get('workflow_run', {})
    if (associated.get('id') != int(run) or associated.get('head_sha') != producer_run['head_sha']
            or producer_run['path'] != '.github/workflows/' + WORKFLOW
            or producer_run['head_branch'] != 'main'):
        raise ValueError('original archive artifact is not bound to exact trusted producer')
    digest = artifact.get('digest', '')
    if not digest.startswith('sha256:') or not build_store.DIGEST.fullmatch(digest[7:]):
        raise ValueError('original artifact lacks immutable server SHA256 digest')
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=directory.name + '-', dir=directory.parent))
    archive = directory / 'artifact.zip'
    build_store.download_api(f'repos/{REPOSITORY}/actions/artifacts/{artifact["id"]}/zip', archive, build_store.MAXIMUM + github_api.MAX_JSON, accept='application/vnd.github+json')
    if build_store.sha(archive) != digest[7:] or archive.stat().st_size != artifact['size_in_bytes']:
        raise ValueError('original artifact ZIP digest or size differs')
    allowed = {'unsigned.tar', 'candidate.json', 'attestation.jsonl', 'compilation.json', 'attestation-context.json'}
    with zipfile.ZipFile(archive) as stream:
        entries = stream.infolist()
        if len(entries) > len(allowed) or len({e.filename for e in entries}) != len(entries):
            raise ValueError('duplicate or excessive original artifact ZIP entries')
        total = 0
        for entry in entries:
            mode = entry.external_attr >> 16
            if entry.filename not in allowed or mode & 0o170000 not in (0, 0o100000):
                raise ValueError('unsafe original artifact ZIP entry')
            total += entry.file_size
            if entry.file_size > build_store.MAXIMUM or total > build_store.MAXIMUM + github_api.MAX_JSON:
                raise ValueError('original artifact ZIP exceeds bounds')
        for entry in entries:
            with stream.open(entry) as source, (directory / entry.filename).open('xb') as target:
                shutil.copyfileobj(source, target)
    proof = {'id': artifact['id'], 'name': artifact['name'], 'digest': digest,
             'size': artifact['size_in_bytes'], 'run_id': str(run), 'run_attempt': str(attempt),
             'head_sha': producer_run['head_sha']}
    update.dump(directory / 'original-artifact.json', proof)
    return directory


def recover(plan, name, directory):
    package = select(plan, name)['packages'][0]
    descriptor = build_store._lookup_durable(package, record=plan)
    if descriptor is not None:
        return descriptor
    compatible_inputs = {package['input_digest']} if package.get('input_digest') is not None else set()
    if all(key in package for key in ('recipe_commit', 'previous_recipe_commit', 'tree_sha', 'lock', 'policy', 'expected_srcinfo')):
        for key in recipe_state.keys('candidate', ''):
            candidate = recipe_state.load('candidate', key)
            for original in candidate.get('packages', []):
                if build_store.compatible(package, original):
                    compatible_inputs.add(original['input_digest'])
    runs = rows(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch', 'workflow_runs')
    for run in runs:
        legacy_title = f"Build {name} / {plan.get('run_id')}.{plan.get('run_attempt')}"
        legacy = run['display_title'] == legacy_title
        if not legacy and not any(run['display_title'].endswith(' / ' + digest) for digest in compatible_inputs):
            continue
        if run.get('path') != '.github/workflows/' + WORKFLOW or run.get('head_branch') != 'main':
            continue
        for attempt in range(int(run['run_attempt']), 0, -1):
            jobs = list(rows(f'repos/{REPOSITORY}/actions/runs/{run["id"]}/attempts/{attempt}/jobs', 'jobs'))
            steps = [step for job in jobs if job.get('name') == 'package' for step in job.get('steps', [])]
            marker = 'Build package without credentials' if legacy else 'Authenticate actual compilation success'
            success = any(step.get('name') == marker and step.get('conclusion') == 'success' for step in steps)
            failure = any(step.get('name') in ('Authenticate actual compilation failure', 'Authenticate compilation not started') and step.get('conclusion') == 'success' for step in steps)
            attempted = any(step.get('name') == 'Build package without credentials' and step.get('conclusion') not in (None, 'skipped') for step in steps)
            if failure and not success:
                continue
            if not success:
                if attempted:
                    raise RuntimeError(f'Compilation outcome for {run["id"]}.{attempt} is unprovable; refusing recompilation')
                continue
            artifact = download_artifact(run['id'], attempt, name, directory / f'recovery-{run["id"]}-{attempt}', legacy=legacy)
            archive = artifact / 'unsigned.tar'
            original = plan if legacy else update.load(artifact / 'candidate.json')
            recipe_candidates.verify_authorization(original)
            original_package = select(original, name)['packages'][0]
            if (original_package['input_digest'] != package.get('input_digest')
                    and not build_store.compatible(package, original_package)):
                raise ValueError('original successful artifact has different compilation inputs')
            unpacked = artifact / 'original'
            build_store.unpack_original(archive, unpacked, original, legacy=legacy)
            producer = {'run_id': str(run['id']), 'run_attempt': str(attempt), 'head_sha': run['head_sha'], 'path': '.github/workflows/' + WORKFLOW, 'pkgbase': name}
            if legacy:
                producer['legacy'] = True
            build_store.producer_run(original, producer)
            build_store.validate_outputs(original, unpacked, name)
            if legacy or not (artifact / 'attestation.jsonl').is_file():
                raise OriginalTransportRecoveryRequired(original, producer, update.load(artifact / 'original-artifact.json'), artifact)
            # A genuine original attestation does not need a recovery signer.
            (artifact / 'original-artifact.json').unlink()
            return build_store.persist(original, unpacked, producer)
    return None


def checkpoint(directory, state):
    bundle = update.load(directory / 'input/bundle.json')
    path = directory / 'compilation.json'
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError('missing or unsafe original compilation checkpoint')
    actual = update.load(path)
    package = bundle['packages'][0]
    expected = {'schema': 1, 'state': state, 'input_digest': package['input_digest'],
                'pkgbase': package['pkgbase'], **{key: bundle[key] for key in
                ('image', 'harness_sha', 'run_id', 'run_attempt')}}
    if actual != expected:
        raise ValueError('actual compilation checkpoint identity/outcome differs')


def outputs(values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in values.items():
                output.write(f'{key}={value}\n')


def prepare_recovery(directory, name, parent, attempt):
    package = {'pkgbase': name, 'input_digest': os.environ['INPUT_DIGEST']}
    descriptor = build_store._lookup_durable(package)
    if descriptor is not None:
        build_store.materialize(descriptor, directory / 'output')
        update.dump(directory / 'build-descriptor.json', descriptor)
        outputs({'reused': 'true'})
        return
    run = os.environ.get('ORIGINAL_RUN', '')
    run_attempt = os.environ.get('ORIGINAL_ATTEMPT', '')
    if not run.isdigit() or not run_attempt.isdigit() or min(int(run), int(run_attempt)) < 1:
        raise ValueError('transport recovery requires exact original producer run and attempt')
    legacy = os.environ.get('ARCHIVE_FORMAT') == 'legacy-native-v1'
    expected = json.loads(os.environ.get('ORIGINAL_ARTIFACT', '{}'))
    if set(expected) != {'id', 'digest', 'size_in_bytes', 'name'}:
        raise ValueError('transport recovery requires explicit original immutable artifact binding')
    artifact = download_artifact(run, run_attempt, name, directory / 'original-source', legacy=legacy, expected=expected)
    if legacy:
        key = os.environ.get('ORIGINAL_CANDIDATE_KEY', '')
        record = {**recipe_state.load('candidate', key), 'run_id': parent, 'run_attempt': attempt}
    else:
        record = update.load(artifact / 'candidate.json')
    selected = select(record, name)['packages'][0]
    if (selected['input_digest'] != package['input_digest'] or record['run_id'] != parent
            or record['run_attempt'] != attempt):
        raise ValueError('transport source differs from original authorized candidate')
    recipe_candidates.verify_authorization(record)
    original_run = github_api.api(f'repos/{REPOSITORY}/actions/runs/{run}/attempts/{run_attempt}')
    producer = {'run_id': run, 'run_attempt': run_attempt, 'head_sha': original_run['head_sha'],
                'path': '.github/workflows/' + WORKFLOW, 'pkgbase': name}
    if legacy:
        producer['legacy'] = True
    build_store.producer_run(record, producer)
    original = build_store.unpack_original(artifact / 'unsigned.tar', artifact / 'original', record, legacy=legacy)
    build_store.validate_outputs(record, original, name)
    shutil.copyfile(artifact / 'unsigned.tar', directory / 'unsigned.tar')
    shutil.copytree(original, directory / 'output')
    update.dump(directory / 'candidate.json', record)
    update.dump(directory / 'producer.json', producer)
    update.dump(directory / 'original-artifact.json', update.load(artifact / 'original-artifact.json'))
    outputs({'reused': 'true', 'transport-recovered': 'true'})


def prepare(directory, name, parent, attempt, kind='candidate'):
    if kind != 'candidate':
        raise ValueError('publication is not permitted to dispatch compilation')
    if os.environ.get('RECOVERY_MODE') == 'true':
        prepare_recovery(directory, name, parent, attempt)
        return
    record = github_api.api(f'repos/{REPOSITORY}/actions/runs/{parent}/attempts/{attempt}')
    plan = update.load(directory / 'candidate.json')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if (record['path'] != '.github/workflows/candidate.yml' or record['head_sha'] != plan['base']
            or record['head_branch'] != 'main'
            or plan['run_id'] != parent or plan['run_attempt'] != attempt):
        raise ValueError('authorized parent workflow identity mismatch')
    recipe_candidates.verify_authorization(plan)
    selected = select(plan, name)
    if selected['packages'][0]['input_digest'] != os.environ.get('INPUT_DIGEST'):
        raise ValueError('worker concurrency input digest differs from approved package')
    descriptor = recover(plan, name, directory)
    if descriptor is not None:
        build_store.materialize(descriptor, directory / 'output')
        update.dump(directory / 'build-descriptor.json', descriptor)
        outputs({'reused': 'true'})
        return
    current = github_api.api(f'repos/{REPOSITORY}/git/ref/heads/main')['object']['sha']
    if current != plan['base'] or head != plan['base']:
        raise ValueError('approved control differs from executing worker or moved before compilation admission')
    if plan.get('kind') == 'recipe':
        recipe_state.assert_recipe_identity(plan)
    else:
        update.pr_identity(plan['pr_number'], plan['base'], plan['head'])
    update.extract_tree(directory / 'bundle.tar', directory / 'frozen', {'recipes', 'bundle.json'})
    bundle = update.load(directory / 'frozen/bundle.json')
    for key in ('schema', 'repository', 'base', 'head', 'run_id', 'run_attempt', 'image', 'harness_sha', 'recipe_pins', 'previous_recipe_pins', 'packages'):
        if bundle[key] != plan[key]:
            raise ValueError('frozen bundle differs from approved input: ' + key)
    target = directory / 'input'
    target.mkdir()
    package = selected['packages'][0]
    recipes.copy_recipe(directory / 'frozen' / package['recipe_dir'], target / package['recipe_dir'])
    (target / 'bundle.json').write_bytes(github_api.canonical({**bundle, 'packages': selected['packages']}))
    scope = hashlib.sha256(github_api.canonical({'image': plan['image'], 'harness': plan['harness_sha']})).hexdigest()[:24]
    prefix = f'trusted-build-v1-linux-x86_64-{name}-{scope}-'
    input_prefix = prefix + package['input_digest'] + '-'
    outputs({'reused': 'false', 'cache-prefix': prefix, 'cache-input-prefix': input_prefix, 'cache-key': input_prefix + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']})


def context(directory):
    record = update.load(directory / 'candidate.json')
    evidence = update.load(directory / 'output/native-evidence.json')
    name = evidence['packages'][0]['pkgbase']
    producer_path = directory / 'producer.json'
    producer = update.load(producer_path) if producer_path.exists() else {
        'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'head_sha': record['base'], 'path': '.github/workflows/' + WORKFLOW, 'pkgbase': name}
    signer = {'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
              'head_sha': os.environ['GITHUB_SHA'], 'path': '.github/workflows/' + WORKFLOW}
    source_path = directory / 'original-artifact.json'
    source = update.load(source_path) if source_path.exists() else None
    update.dump(directory / 'attestation-signer.json', signer)
    update.dump(directory / 'attestation-context.json', build_store.attestation_context(record, producer, signer, source))


def persist(directory):
    record = update.load(directory / 'candidate.json')
    producer_path = directory / 'producer.json'
    producer = update.load(producer_path) if producer_path.exists() else None
    descriptor = build_store.persist(record, directory / 'output', producer)
    update.dump(directory / 'build-descriptor.json', descriptor)


def dispatch(plan, package, recovery=None):
    if recovery is not None:
        plan = recovery.record
        package = select(plan, package['pkgbase'])['packages'][0]
    if recovery is None:
        current = github_api.api(f'repos/{REPOSITORY}/git/ref/heads/main')['object']['sha']
        if current != plan['base']:
            raise ValueError('approved control moved before dispatch; recover original bytes or obtain exact current-control authorization')
        if plan.get('kind') == 'recipe':
            recipe_state.assert_recipe_identity(plan)
        else:
            update.pr_identity(plan['pr_number'], plan['base'], plan['head'])
    github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/dispatches', 'POST', {
        'ref': 'main', 'inputs': {'package': package['pkgbase'],
        'publication_run': plan['run_id'], 'publication_attempt': plan['run_attempt'],
        'input_digest': package['input_digest'], 'recovery_mode': 'true' if recovery is not None else 'false',
        'original_run': recovery.producer['run_id'] if recovery is not None else '',
        'original_attempt': recovery.producer['run_attempt'] if recovery is not None else '',
        'original_artifact': json.dumps({'id': recovery.artifact['id'], 'digest': recovery.artifact['digest'],
                                       'size_in_bytes': recovery.artifact['size'], 'name': recovery.artifact['name']},
                                      sort_keys=True, separators=(',', ':')) if recovery is not None else '',
        'archive_format': 'legacy-native-v1' if recovery is not None and recovery.producer.get('legacy') else 'native-v2',
        'original_candidate_key': recipe_state.identity_key(plan) if recovery is not None and recovery.producer.get('legacy') else ''}})
    return title(plan, package['pkgbase'])


def collect(directory, timeout=10800, kind='candidate'):
    if kind != 'candidate':
        raise ValueError('publication must consume original durable builds, never dispatch workers')
    plan = update.load(directory / 'candidate.json')
    recipe_candidates.verify_authorization(plan)
    context_path = recipe_candidates.controller_context(directory / 'candidate.json')
    if context_path is not None:
        recipe_candidates.verify_current_controller(plan, context_path)
    output = directory / 'unsigned'
    output.mkdir()
    evidence = {**{key: value for key, value in plan.items() if key != 'packages'}, 'packages': [], 'package_runs': []}
    pending = {}
    worker_titles = {}
    recovery_workers = set()
    descriptors = {}
    for package in plan['packages']:
        if package.get('reuse', False):
            continue
        name = package['pkgbase']
        recovery_needed = None
        try:
            descriptor = recover(plan, name, directory)
        except OriginalTransportRecoveryRequired as error:
            descriptor = None
            recovery_needed = error
        if descriptor is not None:
            descriptors[name] = descriptor
            continue
        worker_titles[name] = dispatch(plan, package, recovery=recovery_needed)
        if recovery_needed is not None:
            recovery_workers.add(name)
        pending[name] = package
    deadline = time.monotonic() + timeout
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError('approved package workers still pending: ' + ', '.join(sorted(pending)))
        runs = github_api.api(f'repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
        for name in list(pending):
            descriptor = build_store._lookup_durable(pending[name], record=plan)
            if descriptor is not None:
                descriptors[name] = descriptor
                del pending[name]
                continue
            matches = [run for run in runs if run['display_title'] == worker_titles[name]]
            finished = [run for run in matches if run['status'] == 'completed']
            if finished and not any(run['status'] != 'completed' for run in matches):
                try:
                    descriptor = recover(plan, name, directory)
                except OriginalTransportRecoveryRequired as error:
                    if name in recovery_workers:
                        raise RuntimeError('Original-byte transport recovery failed for ' + name + '; retry transport only, never compilation') from error
                    worker_titles[name] = dispatch(plan, pending[name], recovery=error)
                    recovery_workers.add(name)
                    continue
                if descriptor is None:
                    raise RuntimeError('Approved compilation failed for ' + name + '; failed compiler progress remains cached')
                descriptors[name] = descriptor
                del pending[name]
        if pending:
            time.sleep(30)
    for name, descriptor in sorted(descriptors.items()):
        unpacked = build_store.materialize(descriptor, directory / ('verified-' + name))
        original = update.load(unpacked / 'native-evidence.json')
        # Original package proof is never rewritten to the collecting attempt.
        evidence['packages'].extend(original['packages'])
        evidence['package_runs'].append({'pkgbase': name, **{k: descriptor['producer'][k] for k in ('run_id', 'run_attempt')}, 'build': descriptor})
        for file in original['packages'][0]['files']:
            filename = file['filename']
            if (output / filename).exists():
                raise ValueError('duplicate package output')
            shutil.copyfile(unpacked / filename, output / filename)
    (output / 'native-evidence.json').write_bytes(github_api.canonical(evidence))
    with tarfile.open(directory / 'unsigned.tar', 'w') as archive:
        for path in sorted(output.iterdir()):
            archive.add(path, arcname=path.name, recursive=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'collect', 'persist', 'checkpoint', 'context'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--state', choices=('success', 'failure', 'not-started'))
    parser.add_argument('--package')
    parser.add_argument('--publication-run')
    parser.add_argument('--publication-attempt')
    parser.add_argument('--kind', choices=('publication', 'candidate'), default='candidate')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory, args.package, args.publication_run, args.publication_attempt, args.kind)
    elif args.operation == 'context':
        context(args.directory)
    elif args.operation == 'checkpoint':
        checkpoint(args.directory, args.state)
    elif args.operation == 'persist':
        persist(args.directory)
    else:
        collect(args.directory, kind=args.kind)


if __name__ == '__main__':
    main()
