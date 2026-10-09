"""Durable draft-release transport for original approved unsigned package bytes."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
import urllib.error
import urllib.request

from tools import attestations, github_api, recipe_candidates, recipe_state, update

NAMESPACE = 'built-by-input'
MAXIMUM = 805306368
DIGEST = re.compile(r'[0-9a-f]{64}')
WORKFLOW = '.github/workflows/build-package.yml'


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def route(record):
    if record['repository'] != update.repository():
        raise ValueError('build store repository mismatch')
    return 'repos/' + record['repository']


def package_record(record, name=None):
    packages = [p for p in record['packages'] if name is None or p['pkgbase'] == name]
    if len(packages) != 1:
        raise ValueError('build store requires an exact selected package')
    package = packages[0]
    if not DIGEST.fullmatch(package['input_digest']):
        raise ValueError('invalid complete build input digest')
    return package


def producer_run(record, producer):
    run = github_api.api(route(record) + '/actions/runs/' + str(int(producer['run_id'])) + '/attempts/' + str(int(producer['run_attempt'])))
    package = package_record(record, producer.get('pkgbase'))
    expected_title = f"Build {package['pkgbase']} / {record['run_id']}.{record['run_attempt']}"
    if not producer.get('legacy'):
        expected_title += ' / ' + package['input_digest']
    if (run['path'] != WORKFLOW or run['head_sha'] != record['base']
            or run['head_branch'] != 'main' or run['event'] != 'workflow_dispatch'
            or run['display_title'] != expected_title or run['run_attempt'] != int(producer['run_attempt'])
            or producer['head_sha'] != run['head_sha'] or producer['path'] != WORKFLOW):
        raise ValueError('original package producer identity mismatch')
    from tools.package_runs import rows
    jobs = list(rows(route(record) + '/actions/runs/' + str(run['id']) + '/attempts/' + str(run['run_attempt']) + '/jobs', 'jobs'))
    if producer.get('legacy'):
        comparison = github_api.api(route(record) + '/compare/' + run['head_sha'] + '...da060b28eb528971c99c6dd3b08a52df06ce9957')
        if comparison.get('status') not in ('ahead', 'identical'):
            raise ValueError('legacy protocol is restricted to original accepted pre-cutover ancestors')
    marker = 'Build package without credentials' if producer.get('legacy') else 'Authenticate actual compilation success'
    if not any(step.get('name') == marker and step.get('conclusion') == 'success'
               for job in jobs if job.get('name') == 'package' for step in job.get('steps', [])):
        raise ValueError('original package compilation did not succeed')
    return run


def validate_outputs(record, directory, name=None):
    from tools import publish
    recipe_candidates.verify_authorization(record)
    package = package_record(record, name)
    selected = {**record, 'packages': [package]}
    evidence = update.validate_build_outputs(selected, directory, report=False)
    receipt = evidence['packages'][0]
    metadata = update.parse_srcinfo(package['expected_srcinfo'])
    if {k: v for k, v in receipt['metadata'].items() if k != 'srcinfo'} != metadata:
        raise ValueError('native metadata fields differ from approved input')
    tree = package.get('tree_sha')
    if tree is None and record.get('recipe_manifest') is not None:
        tree = hashlib.sha256(github_api.canonical(record['recipe_manifest'])).hexdigest()
    if tree is None:
        proposal = recipe_state.load('proposal', record.get('proposal_head', record['head']))
        tree = hashlib.sha256(github_api.canonical(proposal['recipe_manifest'])).hexdigest()
    plan = {**selected, 'packages': [{**package, 'reuse': False, 'metadata': metadata, 'tree_sha': tree}]}
    publish.validate_original_unsigned(directory, plan)
    return evidence


def verify(descriptor):
    if set(descriptor) != {'schema', 'pkgbase', 'input_digest', 'record', 'producer', 'release', 'asset', 'attestation', 'evidence_sha256'} or descriptor['schema'] != 2:
        raise ValueError('invalid durable build descriptor')
    record = descriptor['record']
    package = package_record(record, descriptor['pkgbase'])
    if descriptor['pkgbase'] != package['pkgbase'] or descriptor['input_digest'] != package['input_digest']:
        raise ValueError('durable build input mismatch')
    recipe_candidates.verify_authorization(record)
    producer_run(record, descriptor['producer'])
    recipe_state.require_attestation(NAMESPACE, descriptor['input_digest'], descriptor, record['head'])
    if recipe_state.load(NAMESPACE, descriptor['input_digest']) != descriptor:
        raise ValueError('durable build descriptor changed')
    release = github_api.api(route(record) + '/releases/' + str(int(descriptor['release']['id'])))
    if (release['draft'] is not True or release['tag_name'] != 'build-' + descriptor['input_digest']
            or any(release.get(k) != v for k, v in descriptor['release'].items())):
        raise ValueError('durable build release identity changed')
    asset = github_api.api(route(record) + '/releases/assets/' + str(int(descriptor['asset']['id'])))
    if (not any(row['id'] == asset['id'] for row in release['assets'])
            or any(asset.get(k) != descriptor['asset'][k] for k in ('id', 'name', 'size'))
            or not 0 < asset['size'] <= MAXIMUM
            or not DIGEST.fullmatch(descriptor['asset']['sha256'])
            or not DIGEST.fullmatch(descriptor['evidence_sha256'])):
        raise ValueError('durable build asset replaced, renamed, or oversized')
    bound = descriptor['attestation']
    if (not any(row['id'] == bound['id'] for row in release['assets'])
            or bound['name'] != 'attestation.jsonl'):
        raise ValueError('durable attestation asset is not in original build release')
    root = Path.home() / '.local/state/omp/work/build-store-verify'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        archive = Path(temporary) / 'unsigned.tar'
        fetch_original(descriptor, archive)
        original = unpack_original(archive, Path(temporary) / 'original', record, descriptor['producer'].get('legacy', False))
        if sha(original / 'native-evidence.json') != descriptor['evidence_sha256']:
            raise ValueError('cryptographically bound original evidence differs')
    return descriptor


def compatible(package, original):
    fields = ('pkgbase', 'recipe_commit', 'previous_recipe_commit', 'tree_sha',
              'lock', 'policy', 'expected_srcinfo')
    return all(key in package and key in original and package[key] == original[key] for key in fields)


def _lookup_durable(package, record=None):
    if not isinstance(package.get('pkgbase'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', package['pkgbase']):
        raise ValueError('invalid package lookup identity')
    if record is None and package.get('build') is not None:
        if package['build']['pkgbase'] != package['pkgbase']:
            raise ValueError('accepted build descriptor belongs to another package')
        return verify(package['build'])
    selected = package if record is None else next(p for p in record['packages'] if p['pkgbase'] == package['pkgbase'])
    digest = selected.get('input_digest')
    if digest is not None and not DIGEST.fullmatch(digest):
        raise ValueError('invalid build input lookup')
    if digest is None and not compatible(selected, selected):
        raise ValueError('compatible original lookup requires complete static recipe/source/policy/metadata identity')
    descriptor = recipe_state.load_optional(NAMESPACE, digest) if digest is not None else None
    if descriptor is None:
        originals = []
        for key in recipe_state.keys(NAMESPACE, ''):
            stored = recipe_state.load(NAMESPACE, key)
            if stored.get('schema') != 2 or stored.get('pkgbase') != selected['pkgbase']:
                continue
            original = package_record(stored['record'], selected['pkgbase'])
            if compatible(selected, original):
                originals.append(stored)
        if not originals:
            return None
        original = min(originals, key=lambda row: (int(row['producer']['run_id']), int(row['producer']['run_attempt'])))
        return verify(original)
    if descriptor['input_digest'] != digest or descriptor['pkgbase'] != selected['pkgbase']:
        raise ValueError('lookup receipt differs from requested complete compilation input')
    return verify(descriptor)


def lookup(package, record=None):
    descriptor = _lookup_durable(package, record)
    if descriptor is not None:
        return descriptor
    from tools import package_runs
    selected = package if record is None else next(p for p in record['packages'] if p['pkgbase'] == package['pkgbase'])
    plan = record if record is not None else {'packages': [selected]}
    root = Path.home() / '.local/state/omp/work/build-store-recovery'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='original-' + selected['pkgbase'] + '-', dir=root) as work:
        return package_runs.recover(plan, selected['pkgbase'], Path(work))


class AssetAPIRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # urllib surfaces the redirect as HTTPError; authenticate no second hop.
        return None


def download_asset(repository, asset_id, destination, maximum):
    return download_api(f'repos/{repository}/releases/assets/{int(asset_id)}', destination, maximum, accept='application/octet-stream')


def download_api(endpoint, destination, maximum, *, accept):
    """Authenticate only the API hop; never forward credentials to storage redirects."""
    if '://' in endpoint or endpoint.startswith('/'):
        raise ValueError('relative GitHub binary endpoint required')
    url = github_api.API + '/' + endpoint
    request = urllib.request.Request(url, headers={'Accept': accept, 'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'User-Agent': 'arch-packages'})
    opener = urllib.request.build_opener(AssetAPIRedirect())
    try:
        response = opener.open(request, timeout=120)
    except urllib.error.HTTPError as error:
        try:
            if error.code not in (301, 302, 303, 307, 308):
                raise github_api.GitHubError(error.code, 'GET', url) from None
            location = error.headers['Location']
        finally:
            error.close()
        return github_api.download(location, destination, maximum=maximum)
    destination = Path(destination)
    staging = destination.with_name(destination.name + '.part')
    try:
        with response, staging.open('xb') as output:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise ValueError('asset exceeds bound')
                output.write(chunk)
        staging.replace(destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return destination


def attestation_context(record, producer, signer, original_artifact=None):
    package = package_record(record, producer['pkgbase'])
    return {'schema': 1, 'archive_format': 'legacy-native-v1' if producer.get('legacy') else 'native-v2',
            'record_sha256': hashlib.sha256(github_api.canonical(record)).hexdigest(),
            'pkgbase': package['pkgbase'], 'input_digest': package['input_digest'],
            'producer': producer, 'signer': signer, 'original_artifact': original_artifact}


def verify_attestation(archive, bundle, record, producer, signer, original_artifact=None):
    repository = record['repository']
    signer_run = github_api.api(route(record) + '/actions/runs/' + str(int(signer['run_id'])) + '/attempts/' + str(int(signer['run_attempt'])))
    package = package_record(record, producer['pkgbase'])
    expected_title = f"Build {package['pkgbase']} / {record['run_id']}.{record['run_attempt']} / {package['input_digest']}"
    title_matches = signer_run['display_title'] == expected_title
    if original_artifact is not None:
        title_matches = (signer_run['display_title'].startswith('Build ' + package['pkgbase'] + ' / ')
                         and signer_run['display_title'].endswith(' / ' + package['input_digest']))
    if (signer_run['path'] != WORKFLOW or signer_run['head_sha'] != signer['head_sha']
            or signer_run['head_branch'] != 'main' or signer_run['event'] != 'workflow_dispatch'
            or not title_matches or signer['path'] != WORKFLOW
            or signer_run['run_attempt'] != int(signer['run_attempt'])):
        raise ValueError('attestation signer is not the exact accepted-main transport worker')
    if producer.get('legacy') and original_artifact is None:
        raise ValueError('legacy bytes require immutable original artifact and separate recovery signer')
    if original_artifact is None and signer != {k: producer[k] for k in ('run_id', 'run_attempt', 'head_sha', 'path')}:
        raise ValueError('different storage signer requires original immutable artifact binding')
    context = Path(archive).with_name('attestation-context.json')
    context.write_bytes(github_api.canonical(attestation_context(record, producer, signer, original_artifact)))
    expected_signer = {**signer, 'repository': repository}
    original_certificate = attestations.verify(Path(archive), bundle, expected_signer)
    context_certificate = attestations.verify(context, bundle, expected_signer)
    if original_certificate['certificate_sha256'] != context_certificate['certificate_sha256']:
        raise ValueError('archive and provenance context have different signing certificates')


def unpack_original(archive, destination, record, legacy=False):
    from tools import publish
    publish.safe_extract(archive, destination)
    candidate = destination / 'candidate.json'
    if legacy and not candidate.exists():
        return destination
    if not candidate.is_file() or candidate.stat().st_size > 8 * 1024 * 1024 or update.load(candidate) != record:
        raise ValueError('raw producer archive does not bind original approved candidate')
    candidate.unlink()
    return destination


def fetch_original(descriptor, archive):
    record = descriptor['record']
    for field, destination, maximum in (
            ('asset', Path(archive), MAXIMUM),
            ('attestation', Path(archive).with_name('attestation.jsonl'), github_api.MAX_JSON)):
        bound = descriptor[field]
        if not 0 < bound['size'] <= maximum or not DIGEST.fullmatch(bound['sha256']):
            raise ValueError('invalid durable archive or attestation bounds')
        metadata = github_api.api(route(record) + '/releases/assets/' + str(int(bound['id'])))
        if any(metadata.get(key) != bound[key] for key in ('id', 'name', 'size')):
            raise ValueError('durable archive or attestation asset replaced')
        download_asset(record['repository'], bound['id'], destination, bound['size'])
        if destination.stat().st_size != bound['size'] or sha(destination) != bound['sha256']:
            raise ValueError('durable build archive or attestation bytes differ')
    attestation = descriptor['attestation']
    verify_attestation(archive, Path(archive).with_name('attestation.jsonl'), record,
                       descriptor['producer'], attestation['signer'], attestation.get('original_artifact'))


def materialize(descriptor, destination):
    verify(descriptor)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = destination.with_name(destination.name + '.tar')
    fetch_original(descriptor, archive)
    unpack_original(archive, destination, descriptor['record'], descriptor['producer'].get('legacy', False))
    if sha(destination / 'native-evidence.json') != descriptor['evidence_sha256']:
        raise ValueError('original native evidence differs')
    validate_outputs(descriptor['record'], destination, descriptor['pkgbase'])
    return destination


def upload(repository, release_id, archive, name='unsigned.tar'):
    """Stream a bounded file; release uploads must not allocate the package archive."""
    size = archive.stat().st_size
    if not 0 < size <= MAXIMUM or archive.is_symlink():
        raise ValueError('invalid build archive')
    connection = http.client.HTTPSConnection('uploads.github.com', timeout=120)
    if name not in ('unsigned.tar', 'attestation.jsonl'):
        raise ValueError('invalid build store asset name')
    path = f'/repos/{repository}/releases/{int(release_id)}/assets?name={name}'
    try:
        connection.putrequest('POST', path)
        for key, value in {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'User-Agent': 'arch-packages', 'Content-Type': 'application/octet-stream', 'Content-Length': str(size), 'Accept': 'application/vnd.github+json'}.items():
            connection.putheader(key, value)
        connection.endheaders()
        with archive.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        body = response.read(github_api.MAX_JSON + 1)
        if response.status != 201:
            raise github_api.GitHubError(response.status, 'POST', path)
        if len(body) > github_api.MAX_JSON:
            raise ValueError('oversized asset API response')
        return json.loads(body)
    finally:
        connection.close()


def persist(record, directory, producer=None):
    directory = Path(directory)
    evidence = update.load(directory / 'native-evidence.json')
    if len(evidence['packages']) != 1:
        raise ValueError('worker archive must contain exactly one package')
    name = evidence['packages'][0]['pkgbase']
    package = package_record(record, name)
    validate_outputs(record, directory, name)
    producer = producer or {'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'head_sha': record['base'], 'path': WORKFLOW, 'pkgbase': name}
    producer_run(record, producer)
    archive = directory.parent / 'unsigned.tar'
    if not archive.exists():
        with tarfile.open(archive, 'w') as stream:
            for path in sorted(directory.iterdir()):
                stream.add(path, arcname=path.name, recursive=False)
            candidate_bytes = github_api.canonical(record)
            candidate = tarfile.TarInfo('candidate.json')
            candidate.size = len(candidate_bytes)
            import io
            stream.addfile(candidate, io.BytesIO(candidate_bytes))
    signer_path = directory.parent / 'attestation-signer.json'
    signer = update.load(signer_path) if signer_path.exists() else {k: producer[k] for k in ('run_id', 'run_attempt', 'head_sha', 'path')}
    source_path = directory.parent / 'original-artifact.json'
    original_artifact = update.load(source_path) if source_path.exists() else None
    bundle = directory.parent / 'attestation.jsonl'
    if not bundle.is_file() or bundle.is_symlink() or bundle.stat().st_size > github_api.MAX_JSON:
        raise ValueError('original archive lacks bounded producer or recovery OIDC attestation')
    verify_attestation(archive, bundle, record, producer, signer, original_artifact)
    existing = _lookup_durable(package, record)
    if existing is not None:
        if (existing['asset']['sha256'] != sha(archive)
                or existing['asset']['size'] != archive.stat().st_size
                or existing['evidence_sha256'] != sha(directory / 'native-evidence.json')):
            raise ValueError('existing durable descriptor differs from validated original producer bytes')
        return existing
    checked = directory.parent / 'archive-validated'
    unpack_original(archive, checked, record, producer.get('legacy', False))
    validate_outputs(record, checked, name)
    if sha(checked / 'native-evidence.json') != sha(directory / 'native-evidence.json'):
        raise ValueError('staged archive evidence differs from original outputs')
    for file in evidence['packages'][0]['files']:
        if sha(checked / file['filename']) != sha(directory / file['filename']):
            raise ValueError('staged archive package differs from original outputs')
    tag = 'build-' + package['input_digest']
    try:
        release = github_api.api(route(record) + '/releases/tags/' + tag)
    except github_api.GitHubError as error:
        if error.status != 404:
            raise
        release = github_api.api(route(record) + '/releases', 'POST', {'tag_name': tag, 'target_commitish': record['base'], 'name': tag, 'draft': True, 'prerelease': False, 'make_latest': 'false', 'body': 'Original approved unsigned build transport; never promote.'})
    if release['draft'] is not True or release['target_commitish'] != record['base']:
        raise ValueError('existing build release identity differs')
    matches = [asset for asset in release['assets'] if asset['name'] == 'unsigned.tar']
    if len(matches) > 1:
        raise ValueError('ambiguous build archive assets')
    asset = matches[0] if matches else upload(record['repository'], release['id'], archive)
    recovered = directory.parent / 'stored-readback.tar'
    download_asset(record['repository'], asset['id'], recovered, MAXIMUM)
    if sha(recovered) != sha(archive) or asset['size'] != archive.stat().st_size:
        raise ValueError('stored build readback differs from original bytes')
    bundles = [entry for entry in release['assets'] if entry['name'] == 'attestation.jsonl']
    if len(bundles) > 1:
        raise ValueError('ambiguous stored attestation bundles')
    stored_bundle = bundles[0] if bundles else upload(record['repository'], release['id'], bundle, 'attestation.jsonl')
    bundle_readback = directory.parent / 'attestation-readback.jsonl'
    download_asset(record['repository'], stored_bundle['id'], bundle_readback, github_api.MAX_JSON)
    if sha(bundle_readback) != sha(bundle) or stored_bundle['size'] != bundle.stat().st_size:
        raise ValueError('stored attestation readback differs from verified producer bundle')
    descriptor = {'schema': 2, 'pkgbase': package['pkgbase'], 'input_digest': package['input_digest'], 'record': record, 'producer': producer, 'release': {key: release[key] for key in ('id', 'tag_name', 'target_commitish')}, 'asset': {**{key: asset[key] for key in ('id', 'name', 'size')}, 'sha256': sha(archive)}, 'attestation': {**{key: stored_bundle[key] for key in ('id', 'name', 'size')}, 'sha256': sha(bundle), 'signer': signer, 'original_artifact': original_artifact}, 'evidence_sha256': sha(directory / 'native-evidence.json')}
    recipe_state.attest(NAMESPACE, package['input_digest'], descriptor, record['head'])
    recipe_state.save(NAMESPACE, package['input_digest'], descriptor)
    return descriptor
