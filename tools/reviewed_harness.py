"""Human-protected, frozen accepted-input repair bootstrap; never a publisher."""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from tools import github_api, publish, recipes, update
from tools.recipe_gate import harness_digest, input_digest, parse_srcinfo, tree_manifest

api = github_api.api
ROOT = Path(__file__).resolve().parents[1]
CONTROL = 'a7dbfd05495efcbb298a72cdef5106ef3485f88f'
TOOLS_REF = 'repair/frozen-git'
REPOSITORY = 'vith/arch-packages'
PARENT_JOB = 'Protected reviewed harness'
CHILD_JOB = 'Protected reviewed package'
ENVIRONMENT_ID = 23508833820
REVIEWER_ID = 3265539
PACKAGES = {'archive-mounter', 'carapace', 'nasctui', 'cloudflare-speed-cli', 'oh-my-pi-vith-git', 'python-google-genai', 'apexshot'}
FIELDS = ('schema', 'repository', 'base', 'head', 'recipe_pins', 'previous_recipe_pins', 'run_id', 'run_attempt', 'image', 'harness_sha', 'kind', 'control_sha', 'tools_sha', 'tools_ref', 'pr_number', 'review')
ALLOWED = {'tools/native.py', 'tools/sources.py', 'tools/update.py', 'tests/test_sources.py', 'tools/reviewed_harness.py', 'tools/package_runs.py', '.github/workflows/reviewed-harness.yml', '.github/workflows/build-package.yml', '.github/workflows/verification.yml', 'tests/test_reviewed_harness.py', 'tests/test_package_runs.py', 'README.md', 'AGENTS.md'}
# Proven source repair 505539d plus the explicitly approved YAML test dependency.
REPAIR_BLOBS = {'tools/native.py': 'a4adf98903eef54c2df3be89c67bbca8869ddde0', 'tools/sources.py': 'f5ebc65f208d6a3d5a1902b093ea9d42254941f1', 'tools/update.py': 'fb5ebcd9ebfceede7d2316c5646391d7cf7f8704', 'tests/test_sources.py': '9731a9931c0e96533e37df9091778be6c8ff79fc', '.github/workflows/verification.yml': 'ff1309ab85b242bb0ea8d98bcedcfbe6f8f3c459'}


def workflow_protection(head):
    import yaml

    class StrictLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                if key_node.tag == 'tag:yaml.org,2002:merge':
                    raise ValueError('workflow merge keys are forbidden')
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, (str, bool, int)) or key in result:
                    raise ValueError('duplicate or invalid immutable workflow key')
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    # GitHub uses YAML 1.2 booleans: its unquoted `on` is a string, not True.
    StrictLoader.yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in rules if tag != 'tag:yaml.org,2002:bool']
        for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    StrictLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'), list('tTfF'))

    for filename, job_id, name, condition in (
        ('reviewed-harness.yml', 'reviewed-harness', PARENT_JOB, "github.ref == 'refs/heads/repair/frozen-git'"),
        ('build-package.yml', 'reviewed-package', CHILD_JOB, "github.ref == 'refs/heads/repair/frozen-git' && inputs.kind == 'reviewed-harness'"),
    ):
        blob = api(route(f'/contents/.github/workflows/{filename}?ref={head}'))
        if blob.get('encoding') != 'base64' or blob.get('size', 0) > update.MAX_FILE:
            raise ValueError('invalid immutable workflow content')
        text = base64.b64decode(blob['content'], validate=False).decode()
        if len(text.encode()) > update.MAX_FILE:
            raise ValueError('oversized immutable workflow')
        try:
            if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)):
                raise ValueError('workflow aliases and anchors are forbidden')
            workflow = yaml.load(text, Loader=StrictLoader)
        except yaml.YAMLError as error:
            raise ValueError('invalid immutable workflow YAML') from error
        if not isinstance(workflow, dict) or not isinstance(workflow.get('jobs'), dict):
            raise ValueError('missing immutable workflow jobs')
        jobs = workflow['jobs']
        if set(jobs) != ({'reviewed-harness'} if filename == 'reviewed-harness.yml' else {'reviewed-package', 'package'}):
            raise ValueError('unexpected immutable workflow execution jobs')
        job = jobs[job_id]
        if (not isinstance(job, dict) or job.get('name') != name or job.get('if') != condition
                or job.get('environment') != 'code-review' or job.get('runs-on') != 'ubuntu-latest'):
            raise ValueError('H workflow does not protect exact execution job')
        steps = job.get('steps', [])
        if not steps or not isinstance(steps[0], dict) or steps[0].get('uses') != 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1':
            raise ValueError('protected execution must first checkout immutable H')
        if steps[0].get('with') != {'ref': '${{ github.sha }}', 'persist-credentials': False}:
            raise ValueError('protected checkout is moving or retains credentials')
        expected_title = ('Reviewed frozen Git / C ' + CONTROL + ' / H ${{ github.sha }}' if filename == 'reviewed-harness.yml'
                          else 'Build ${{ inputs.package }} / ${{ inputs.publication_run }}.${{ inputs.publication_attempt }}')
        if workflow.get('run-name') != expected_title:
            raise ValueError('immutable workflow title does not bind authority tuple')
        trigger = workflow.get('on')
        if not isinstance(trigger, dict):
            raise ValueError('immutable workflow trigger is missing')
        if filename == 'reviewed-harness.yml':
            if trigger != {'push': {'branches': [TOOLS_REF]}}:
                raise ValueError('parent bootstrap is not restricted to repair branch push')
        else:
            if set(trigger) != {'workflow_dispatch'}:
                raise ValueError('bootstrap child is not a dispatch workflow')
            if not isinstance(jobs['package'], dict) or jobs['package'].get('if') != "github.ref == 'refs/heads/main' && inputs.kind != 'reviewed-harness'":
                raise ValueError('ordinary child execution is not excluded from bootstrap')


def route(suffix):
    return f'/repos/{REPOSITORY}{suffix}'


def parent_title(head):
    return f'Reviewed frozen Git / C {CONTROL} / H {head}'


def positive(value):
    if not str(value).isdecimal() or int(value) < 1:
        raise ValueError('invalid run/job/attempt identity')
    return int(value)


def run_identity(run, record, *, child=None):
    title = parent_title(record['tools_sha']) if child is None else f"Build {child} / {record['run_id']}.{record['run_attempt']}"
    path = '.github/workflows/' + ('reviewed-harness.yml' if child is None else 'build-package.yml')
    if (run.get('path') != path or run.get('head_sha') != record['tools_sha'] or run.get('head_branch') != TOOLS_REF
            or run.get('event') != ('push' if child is None else 'workflow_dispatch') or run.get('display_title') != title):
        raise ValueError('reviewed workflow identity mismatch')


def protected_attempt(record, run_id, attempt, *, child=None, durable=False):
    run_id, attempt = positive(run_id), positive(attempt)
    latest = api(route(f'/actions/runs/{run_id}'))
    exact = api(route(f'/actions/runs/{run_id}/attempts/{attempt}'))
    for run in (latest, exact):
        run_identity(run, record, child=child)
        if run.get('id') != run_id:
            raise ValueError('workflow run ID mismatch')
    # Old run-scoped approval cannot authorize a new attempt or stale transport.
    if latest.get('run_attempt') != attempt or exact.get('run_attempt') != attempt:
        raise ValueError('stale workflow attempt')
    if durable and (exact.get('status') != 'completed' or exact.get('conclusion') != 'success'):
        raise ValueError('workflow attempt did not succeed')
    jobs = api(route(f'/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100'))
    if jobs.get('total_count', 0) > 100:
        raise ValueError('too many review jobs')
    name = PARENT_JOB if child is None else CHILD_JOB
    selected = [job for job in jobs['jobs'] if job.get('name') == name]
    if len(selected) != 1:
        raise ValueError('missing/duplicate protected job')
    job = selected[0]
    positive(job['id'])
    if (job.get('run_id') != run_id or job.get('run_attempt') != attempt or job.get('head_sha') != record['tools_sha']
            or not job.get('started_at') or job.get('status') not in ('in_progress', 'completed')
            or (job.get('status') == 'completed' and job.get('conclusion') != 'success')
            or (durable and (job.get('status') != 'completed' or job.get('conclusion') != 'success'))
            or not str(job.get('runner_name', '')).startswith('GitHub Actions') or 'ubuntu-latest' not in job.get('labels', [])):
        raise ValueError('protected hosted job has not executed successfully')
    environment = api(route('/environments/code-review'))
    rules = [rule for rule in environment.get('protection_rules', []) if rule.get('type') == 'required_reviewers']
    if (environment.get('id') != ENVIRONMENT_ID or environment.get('name') != 'code-review'
            or environment.get('can_admins_bypass') is not False or len(rules) != 1
            or [(reviewer.get('type'), reviewer.get('reviewer', {}).get('id')) for reviewer in rules[0].get('reviewers', [])] != [('User', REVIEWER_ID)]):
        raise ValueError('code-review protection changed')
    approvals = api(route(f'/actions/runs/{run_id}/approvals'))
    if not any(row.get('state') == 'approved' and row.get('user', {}).get('id') == REVIEWER_ID
               and any(env.get('id') == ENVIRONMENT_ID and env.get('name') == 'code-review' for env in row.get('environments', [])) for row in approvals):
        raise ValueError('missing genuine human code-review approval')
    return job


def verify_authority(record: dict, *, executing_child: bool = False) -> dict:
    if (record.get('schema') != 1 or record.get('kind') != 'reviewed-harness' or record.get('repository') != REPOSITORY
            or record.get('base') != CONTROL or record.get('control_sha') != CONTROL
            or record.get('head') != record.get('tools_sha') or not re.fullmatch('[0-9a-f]{40}', record.get('tools_sha', ''))
            or record['tools_sha'] == CONTROL or record.get('tools_ref') != TOOLS_REF
            or not re.fullmatch('[0-9a-f]{64}', record.get('harness_sha', ''))):
        raise ValueError('invalid control/tools identity')
    if api(route('/git/ref/heads/main'))['object']['sha'] != CONTROL:
        raise ValueError('accepted control moved')
    pr = api(route(f"/pulls/{positive(record['pr_number'])}"))
    if (pr.get('state') != 'open' or pr.get('base', {}).get('ref') != 'main' or pr['base']['sha'] != CONTROL
            or pr['base']['repo']['full_name'] != REPOSITORY or pr['head']['repo']['full_name'] != REPOSITORY
            or pr['head']['sha'] != record['tools_sha'] or pr['head']['ref'] != TOOLS_REF
            or api(route('/git/ref/heads/' + TOOLS_REF))['object']['sha'] != record['tools_sha']):
        raise ValueError('repair PR/ref moved or is foreign')
    comparison = api(route(f"/compare/{CONTROL}...{record['tools_sha']}"))
    files = comparison.get('files', [])
    if not files or len(files) >= 100 or comparison.get('merge_base_commit', {}).get('sha') != CONTROL:
        raise ValueError('repair is not a bounded accepted-control descendant')
    if any(row['filename'] not in ALLOWED or row.get('previous_filename', row['filename']) not in ALLOWED for row in files):
        raise ValueError('repair changes accepted package/source/image inputs or unrelated code')
    changed = {row['filename']: row for row in files}
    if any(changed.get(path, {}).get('sha') != sha for path, sha in REPAIR_BLOBS.items()):
        raise ValueError('approved source repair or test dependency changed or is missing')
    workflow_protection(record['tools_sha'])
    job = protected_attempt(record, record['run_id'], record['run_attempt'])
    expected = {'run_id': str(record['run_id']), 'run_attempt': str(record['run_attempt']), 'job_id': job['id'], 'environment': 'code-review', 'environment_id': ENVIRONMENT_ID, 'control_sha': CONTROL, 'tools_sha': record['tools_sha'], 'harness_sha': record['harness_sha']}
    if record.get('review') != expected:
        raise ValueError('review proof differs from actual protected attempt')
    if executing_child:
        if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() != record['tools_sha'] or harness_digest(ROOT) != record['harness_sha']:
            raise ValueError('executing tools differ from immutable reviewed H')
        protected_attempt(record, os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'], child=os.environ['PACKAGE'])
    return expected


def executing_parent(record):
    if (str(record['run_id']) != os.environ.get('GITHUB_RUN_ID') or str(record['run_attempt']) != os.environ.get('GITHUB_RUN_ATTEMPT')
            or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() != record['tools_sha']
            or harness_digest(ROOT) != record['harness_sha']):
        raise ValueError('executing parent differs from reviewed immutable attempt')


def accepted_inputs(record, root):
    pins = {}
    accepted = update.checkout_data(CONTROL, root / 'accepted', pins)
    policies = update.policy_at(accepted)
    if set(policies) != PACKAGES or set(pins) != PACKAGES:
        raise ValueError('accepted enrollment differs from seven repair packages')
    image = (accepted / 'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', image):
        raise ValueError('accepted image is not pinned Arch')
    packages = []
    for name in sorted(policies):
        recipe = accepted / 'recipes' / name
        lock = update.load(accepted / 'inputs' / (name + '.json'))
        update.validate_source_policy(lock, policies[name])
        metadata = parse_srcinfo((recipe / '.SRCINFO').read_text())
        if metadata['version'] != lock['version']:
            raise ValueError('accepted metadata/lock version differs')
        packages.append({'pkgbase': name, 'recipe_commit': pins[name], 'previous_recipe_commit': pins[name], 'recipe_dir': 'recipes/' + name, 'lock': lock, 'policy': policies[name], 'input_digest': input_digest(recipe, lock, policies[name], image, record['harness_sha']), 'expected_srcinfo': (recipe / '.SRCINFO').read_text(), 'metadata': metadata, 'tree_sha': hashlib.sha256(github_api.canonical(tree_manifest(recipe))).hexdigest(), 'reuse': False})
    return accepted, {'image': image, 'recipe_pins': pins, 'previous_recipe_pins': pins, 'packages': packages}


def reconstruct(record, root):
    accepted, expected = accepted_inputs(record, root)
    tools = update.checkout_data(record['tools_sha'], root / 'tools')
    if harness_digest(tools) != record['harness_sha']:
        raise ValueError('immutable H native digest differs')
    for key in ('image', 'recipe_pins', 'previous_recipe_pins'):
        if record.get(key) != expected[key]:
            raise ValueError('accepted input identity differs: ' + key)
    names = [package['pkgbase'] for package in record['packages']]
    if len(names) != len(set(names)) or not set(names) <= PACKAGES or not names:
        raise ValueError('invalid selected package set')
    wanted = [package for package in expected['packages'] if package['pkgbase'] in names]
    if record['packages'] != wanted:
        raise ValueError('transport differs from independently reconstructed C inputs')
    return accepted


def verify_native_authority(record: dict, evidence: dict) -> None:
    verify_authority(record)
    for key in FIELDS:
        if evidence.get(key) != record.get(key):
            raise ValueError('native identity mismatch: ' + key)
    runs = evidence.get('package_runs', [])
    names = {package['pkgbase'] for package in record['packages']}
    if len(runs) != len(names) or {row.get('pkgbase') for row in runs} != names or len({row.get('run_id') for row in runs}) != len(runs):
        raise ValueError('missing/duplicate independent package runs')
    for row in runs:
        if str(row['run_id']) == str(record['run_id']):
            raise ValueError('parent cannot be package child')
        protected_attempt(record, row['run_id'], row['run_attempt'], child=row['pkgbase'], durable=True)


def validate_outputs(record: dict, output: Path) -> None:
    evidence = update.load(output / 'native-evidence.json')
    verify_native_authority(record, evidence)
    with tempfile.TemporaryDirectory(prefix='reviewed-validation-', dir=ROOT.parent) as work:
        reconstruct(record, Path(work))
    if any(path.is_symlink() or not path.is_file() for path in output.iterdir()):
        raise ValueError('unsigned output is not a regular file')
    publish.validate_unsigned(output, record)


def prepare(output: Path):
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if head != os.environ['GITHUB_SHA']:
        raise ValueError('parent checkout differs from run H')
    prs = api(route('/pulls?state=open&base=main&head=vith:repair/frozen-git&per_page=100'))
    if len(prs) != 1:
        raise ValueError('expected exactly one open repair PR')
    record = {'schema': 1, 'repository': REPOSITORY, 'base': CONTROL, 'head': head, 'kind': 'reviewed-harness', 'control_sha': CONTROL, 'tools_sha': head, 'tools_ref': TOOLS_REF, 'pr_number': prs[0]['number'], 'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'harness_sha': harness_digest(ROOT)}
    job = protected_attempt(record, record['run_id'], record['run_attempt'])
    record['review'] = {'run_id': record['run_id'], 'run_attempt': record['run_attempt'], 'job_id': job['id'], 'environment': 'code-review', 'environment_id': ENVIRONMENT_ID, 'control_sha': CONTROL, 'tools_sha': head, 'harness_sha': record['harness_sha']}
    verify_authority(record)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='reviewed-prepare-', dir=ROOT.parent) as work:
        work = Path(work)
        accepted, inputs = accepted_inputs(record, work)
        record.update(inputs)
        tools = update.checkout_data(head, work / 'tools')
        if harness_digest(tools) != record['harness_sha']:
            raise ValueError('source test H native digest differs')
        update.status(head, 'verify', 'pending', 'Testing reviewed immutable repair in pinned Arch')
        try:
            run_tests(tools, record['image'])
            verify_authority(record)
        except Exception:
            update.status(head, 'verify', 'failure', 'Reviewed immutable repair verification failed')
            raise
        update.status(head, 'verify', 'success', 'Reviewed H suite and installed makepkg regression passed')
        bundle = output / 'bundle'
        for package in record['packages']:
            recipes.copy_recipe(accepted / package['recipe_dir'], bundle / package['recipe_dir'])
        update.dump(bundle / 'bundle.json', record)
        with tarfile.open(output / 'bundle.tar', 'w') as archive:
            archive.add(bundle, arcname='bundle')
        shutil.rmtree(bundle)
    update.dump(output / 'candidate.json', record)
    update.status(head, 'candidate-build', 'pending', 'Awaiting seven independent protected hosted builds')
    update.status(head, 'recipe-policy', 'pending', 'Awaiting protected reviewed repair evidence')
    return record


def run_tests(root, image):
    # This is deliberately bootstrap-only: ordinary candidate semantics stay intact.
    runner = "import unittest,sys; s=unittest.defaultTestLoader.discover('tests'); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(not r.wasSuccessful() or bool(r.skipped))"
    command = 'pacman -Syu --noconfirm --needed -- base-devel python python-yaml git gnupg && test -r /usr/share/makepkg/source/git.sh && mkdir -p /verify/.work && export TMPDIR=/verify/.work && cd /verify && python -c ' + __import__('shlex').quote(runner)
    environment = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'DOCKER_HOST', 'TMPDIR'}}
    container = subprocess.check_output(['docker', 'create', '--platform=linux/amd64', '--cap-drop=ALL', '--cap-add=CHOWN', '--cap-add=DAC_OVERRIDE', '--cap-add=FOWNER', '--cap-add=SETUID', '--cap-add=SETGID', '--security-opt=no-new-privileges', image, '/bin/bash', '-c', command], text=True, env=environment).strip()
    try:
        subprocess.run(['docker', 'cp', str(root) + '/.', container + ':/verify'], check=True, env=environment)
        subprocess.run(['docker', 'start', '-a', container], check=True, env=environment)
    finally:
        subprocess.run(['docker', 'rm', '-f', container], check=True, env=environment)


def report(record: dict, output: Path) -> None:
    if {package['pkgbase'] for package in record['packages']} != PACKAGES:
        raise ValueError('report requires all seven packages')
    executing_parent(record)
    validate_outputs(record, output)
    verify_authority(record)
    job = protected_attempt(record, record['run_id'], record['run_attempt'])
    if not any(step.get('name') == 'Verify protected authority and prepare accepted C inputs'
               and step.get('status') == 'completed' and step.get('conclusion') == 'success' for step in job.get('steps', [])):
        raise ValueError('current exact parent did not complete H source verification')
    update.status(record['tools_sha'], 'candidate-build', 'success', 'Seven independent reviewed H builds validated against accepted C')
    update.status(record['tools_sha'], 'recipe-policy', 'success', 'Exact repair approved through protected human code-review; human merge required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'report'))
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory)
    else:
        report(update.load(args.directory / 'candidate.json'), args.directory / 'unsigned')


if __name__ == '__main__':
    main()
