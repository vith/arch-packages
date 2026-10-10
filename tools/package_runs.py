"""Reserve exact package workers and collect successful durable metadata."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import time

from tools import build_store, github_api, recipe_state, recipes, update

WORKFLOW = 'build-package.yml'


def select(plan, name):
    packages = [p for p in plan['packages'] if p['pkgbase'] == name and not p.get('reuse', False)]
    if len(packages) != 1:
        raise ValueError('worker requires exactly one requested package')
    return {**plan, 'packages': packages}


def title(plan, name):
    return f"Build {name} / {plan['request_id']}"


def outputs(values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            for key, value in values.items():
                stream.write(f'{key}={value}\n')


def dispatch(plan, package, retry=False):
    request = select(plan, package['pkgbase'])
    pending, created = recipe_state.reserve_build(request, retry=retry)
    if created:
        github_api.api(f"repos/{request['repository']}/actions/workflows/{WORKFLOW}/dispatches", 'POST', {
            'ref': 'main', 'inputs': {'package': package['pkgbase'], 'pr_number': str(request['pr_number']),
                                    'recipe_sha': package['recipe_commit'], 'request_id': request['request_id']}})
    return pending, created


def accepted_cache_key(root, name, image, harness):
    """Restore only a fully successful producer explicitly accepted on trusted main."""
    if not recipes.NAME.fullmatch(name):
        raise ValueError('invalid accepted cache package')
    path = Path(root) / 'acceptance' / (name + '.json')
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('unsafe accepted cache authority')
    acceptance = update.load(path)
    descriptor = acceptance.get('build')
    if descriptor is None or descriptor.get('schema') != 3:
        return None
    if acceptance.get('pkgbase') != name or descriptor.get('pkgbase') != name:
        raise ValueError('accepted cache authority belongs to another package')
    descriptor = build_store.lookup({'pkgbase': name, 'build': descriptor})
    evidence = descriptor['evidence']
    if evidence['image'] != image or evidence['harness_sha'] != harness:
        return None
    build_store.producer_run(descriptor['record'], descriptor['producer'])
    original = descriptor['record']
    if (not str(original['pr_number']).isdigit()
            or not recipes.SHA.fullmatch(original['head'])):
        raise ValueError('invalid accepted cache producer namespace')
    scope = hashlib.sha256(github_api.canonical({'image': image, 'harness': harness})).hexdigest()[:24]
    receipt = evidence['packages'][0]
    key = (f"pr-build-v2-{name}-{original['pr_number']}-{original['head']}-{scope}-"
           f"{receipt['input_digest']}-{descriptor['producer']['run_id']}-{descriptor['producer']['run_attempt']}")
    return {'key': key, 'path': str(Path.home() / '.local/state/arch-packages/cache/pr' / name / original['head'])}


def admit_cache(source, destination, expected_key, matched_key):
    """Do not expose prefix-matched cross-PR caches to the compiler."""
    source, destination = Path(source), Path(destination)
    root = Path.home() / '.local/state/arch-packages/cache/pr'
    for path in (source, destination):
        relative = path.relative_to(root)
        if (len(relative.parts) != 2 or not recipes.NAME.fullmatch(relative.parts[0])
                or not recipes.SHA.fullmatch(relative.parts[1])):
            raise ValueError('unsafe cache admission path')
    if not expected_key or matched_key != expected_key:
        if source.is_symlink():
            source.unlink()
        elif source.exists():
            shutil.rmtree(source)
        return False
    if not source.is_dir() or source.is_symlink():
        raise ValueError('unsafe accepted cache directory')
    if source != destination:
        if destination.exists() or destination.is_symlink():
            raise ValueError('accepted cache would overwrite existing isolated cache')
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
    return True


def prepare(directory, name, pr_number, recipe_sha, request_id):
    directory = Path(directory)
    request = recipe_state.build_request(request_id)
    selected = select(request, name)
    package = selected['packages'][0]
    if (request['repository'] != update.repository() or str(request['pr_number']) != str(pr_number)
            or request['head'] != recipe_sha or package['recipe_commit'] != recipe_sha):
        raise ValueError('worker arguments differ from exact reserved request')
    producer = {'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                'head_sha': os.environ['GITHUB_SHA'], 'path': '.github/workflows/' + WORKFLOW, 'pkgbase': name}
    run = build_store.producer_run(request, producer, completed=False)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if (os.environ.get('GITHUB_REPOSITORY') != request['repository'] or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or head != run['head_sha']):
        raise ValueError('worker is not executing the requested trusted controller')
    current_main = github_api.api(f"repos/{request['repository']}/git/ref/heads/main")['object']['sha']
    if current_main != head:
        raise ValueError('worker controller moved before admission')
    recipe_state.assert_recipe_identity(request, control=head)
    recipe_state.register_build(name, request_id, producer)
    directory.mkdir(parents=True, exist_ok=True)
    update.dump(directory / 'candidate.json', selected)
    update.dump(directory / 'producer.json', producer)
    target = directory / 'input'
    target.mkdir()
    archive = directory / 'recipe.tar.gz'
    github_api.download(f"https://api.github.com/repos/{request['repository']}/tarball/{recipe_sha}", archive, maximum=recipes.MAX_TREE)
    exported = directory / 'exported'
    update.extract_tree(archive, exported)
    recipe = target / package['recipe_dir']
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipes.copy_recipe(exported, recipe)
    if (recipe / '.SRCINFO').read_text() != package['expected_srcinfo']:
        raise ValueError('exported recipe metadata differs from reserved request')
    from tools.recipe_gate import tree_manifest, harness_digest, input_digest
    if hashlib.sha256(github_api.canonical(tree_manifest(recipe))).hexdigest() != package['tree_sha']:
        raise ValueError('exported recipe tree differs from reserved request')
    actual_harness = harness_digest(Path.cwd())
    actual_digest = input_digest(recipe, package['lock'], package['policy'], request['image'], actual_harness)
    actual_package = {**package, 'request_input_digest': package['input_digest'], 'input_digest': actual_digest}
    update.dump(target / 'bundle.json', {**selected, 'harness_sha': actual_harness, 'producer': producer,
                                       'packages': [actual_package], 'run_id': producer['run_id'],
                                       'run_attempt': producer['run_attempt']})
    scope = hashlib.sha256(github_api.canonical({'image': request['image'], 'harness': actual_harness})).hexdigest()[:24]
    trusted = accepted_cache_key(Path.cwd(), name, request['image'], actual_harness)
    isolated = f'pr-build-v2-{name}-{pr_number}-{recipe_sha}-{scope}-'
    outputs({'accepted-cache-key': trusted['key'] if trusted else '',
             'accepted-cache-path': trusted['path'] if trusted else '',
             'cache-input-prefix': isolated + actual_digest + '-',
             'cache-key': isolated + actual_digest + '-' + producer['run_id'] + '-' + producer['run_attempt']})


def context(directory):
    record = update.load(directory / 'candidate.json')
    producer = update.load(directory / 'producer.json')
    signer = {key: producer[key] for key in ('run_id', 'run_attempt', 'head_sha', 'path')}
    update.dump(directory / 'attestation-context.json', build_store.attestation_context(record, producer, signer))


def persist(directory):
    descriptor = build_store.persist(update.load(directory / 'candidate.json'), directory / 'output',
                                     update.load(directory / 'producer.json'))
    update.dump(directory / 'build-descriptor.json', descriptor)


def collect(directory, timeout=10800, kind='candidate', retry=False):
    if kind != 'candidate':
        raise ValueError('publication cannot dispatch compilation')
    directory = Path(directory)
    plan = update.load(directory / 'candidate.json')
    requests = {}
    for package in plan['packages']:
        if not package.get('reuse', False):
            pending, _ = dispatch(plan, package, retry=retry)
            if pending['head'] != plan['head']:
                raise ValueError('another active package request must finish before this head')
            requests[package['pkgbase']] = pending['request_id']
    if len(requests) == 1 and len(plan['packages']) == 1:
        snapshot = recipe_state.StateSnapshot()
        original = recipe_state.build_request(next(iter(requests.values())), snapshot=snapshot)
        if original['head'] != plan['head'] or original['pr_number'] != plan['pr_number']:
            raise ValueError('reserved original request belongs to another recipe PR')
        plan = original
        update.dump(directory / 'candidate.json', plan)
    descriptors = {}
    deadline = time.monotonic() + timeout
    while requests:
        snapshot = recipe_state.StateSnapshot()
        for name, request_id in list(requests.items()):
            pending = recipe_state.pending_build(name, snapshot=snapshot)
            if pending is None or pending['request_id'] != request_id:
                raise ValueError('active build request changed during collection')
            if pending['state'] == 'failure':
                raise RuntimeError('Package worker failed: ' + name + '; explicit retry builds fresh')
            producer = pending.get('worker')
            if producer is None:
                continue
            request = recipe_state.build_request(request_id, snapshot=snapshot)
            run = build_store.producer_run(request, producer, completed=False)
            if run['status'] != 'completed':
                continue
            if run['conclusion'] != 'success':
                recipe_state.finish_build(name, request_id, 'failure')
                raise RuntimeError('Entire package worker failed: ' + name)
            stored = snapshot.load_optional('build-result', request_id)
            descriptor = (build_store.lookup({'pkgbase': name, 'request_id': request_id, 'build': stored}, record=request)
                          if stored is not None else None)
            if descriptor is None:
                recipe_state.finish_build(name, request_id, 'failure')
                raise RuntimeError('Successful worker lacks durable output: ' + name)
            if descriptor['producer'] != producer:
                raise ValueError('durable output belongs to another worker')
            recipe_state.finish_build(name, request_id, 'success', result=descriptor)
            descriptors[name] = descriptor
            del requests[name]
        if requests:
            if time.monotonic() >= deadline:
                raise TimeoutError('Exact package workers still pending: ' + ', '.join(sorted(requests)))
            time.sleep(30)
    update.dump(directory / 'build-results.json', descriptors)
    evidence = {key: value for key, value in plan.items() if key != 'packages'}
    evidence['packages'] = [d['evidence']['packages'][0] for _, d in sorted(descriptors.items())]
    evidence['package_runs'] = [{'pkgbase': name, **d['producer'], 'build': d} for name, d in sorted(descriptors.items())]
    update.dump(directory / 'native-evidence.json', evidence)
    return descriptors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'collect', 'persist', 'context'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--package')
    parser.add_argument('--pr-number')
    parser.add_argument('--recipe-sha')
    parser.add_argument('--request-id')
    parser.add_argument('--retry', action='store_true')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.directory, args.package, args.pr_number, args.recipe_sha, args.request_id)
    elif args.operation == 'context':
        context(args.directory)
    elif args.operation == 'persist':
        persist(args.directory)
    else:
        collect(args.directory, retry=args.retry)


if __name__ == '__main__':
    main()
