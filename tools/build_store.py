"""Direct durable transport for original fully successful package workers."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from tools import attestations, github_api, recipe_state, recipes, sources, update

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


def producer_run(record, producer, completed=True):
    run = github_api.api(route(record) + '/actions/runs/' + str(int(producer['run_id'])) + '/attempts/' + str(int(producer['run_attempt'])))
    package = package_record(record, producer.get('pkgbase'))
    if (run['path'] != WORKFLOW
            or run['head_branch'] != 'main' or run['event'] != 'workflow_dispatch'
            or run['display_title'] != f"Build {package['pkgbase']} / {record['request_id']}"
            or run['run_attempt'] != int(producer['run_attempt'])
            or producer['head_sha'] != run['head_sha'] or producer['path'] != WORKFLOW
            or run.get('repository', {}).get('full_name', record['repository']) != record['repository']):
        raise ValueError('exact trusted package worker identity mismatch')
    if completed and (run['status'] != 'completed' or run['conclusion'] != 'success'):
        raise ValueError('entire original worker did not succeed')
    current_main = github_api.api(route(record) + '/git/ref/heads/main')['object']['sha']
    if (not recipes.is_ancestor(record['repository'], record['base'], producer['head_sha'])
            or not recipes.is_ancestor(record['repository'], producer['head_sha'], current_main)):
        raise ValueError('worker controller is not in protected-main lineage')
    return run


def validate_metadata(record, evidence):
    from tools.recipe_gate import metadata_equivalent, parse_srcinfo
    package = package_record(record)
    for key in ('repository', 'base', 'head', 'request_id', 'image'):
        if evidence.get(key) != record[key]:
            raise ValueError('native evidence identity differs: ' + key)
    if len(evidence.get('packages', [])) != 1:
        raise ValueError('worker must produce exactly one package receipt')
    receipt = evidence['packages'][0]
    if any(receipt.get(key) != package[key] for key in ('pkgbase', 'recipe_commit', 'tree_sha')):
        raise ValueError('native receipt differs from requested recipe')
    if (receipt.get('request_input_digest') != package['input_digest']
            or not DIGEST.fullmatch(receipt.get('input_digest', ''))
            or not DIGEST.fullmatch(evidence.get('harness_sha', ''))
            or receipt.get('harness_sha') != evidence['harness_sha']):
        raise ValueError('actual build digest or original request binding differs')
    actual = receipt['metadata']['srcinfo']
    dynamic = receipt['metadata'].get('dynamic_pkgver', False)
    if not isinstance(dynamic, bool):
        raise ValueError('invalid dynamic pkgver declaration')
    if not metadata_equivalent(package['expected_srcinfo'], actual, dynamic=dynamic):
        raise ValueError('native non-version declarations differ from recipe')
    metadata = parse_srcinfo(actual)
    if {k: v for k, v in receipt['metadata'].items() if k not in ('srcinfo', 'dynamic_pkgver')} != metadata:
        raise ValueError('native metadata fields differ from actual SRCINFO')
    actual_lock = receipt['source_lock']
    if actual_lock.get('schema') != 1 or actual_lock['version'] != metadata['version']:
        raise ValueError('actual source lock version differs')
    planned_sources = package['lock']['sources']
    actual_sources = actual_lock['sources']
    if len(planned_sources) != len(actual_sources):
        raise ValueError('actual source declaration set differs')
    for planned, actual_source in zip(planned_sources, actual_sources):
        mutable = {'commit', 'git_context', 'tag_object', 'peeled_commit'} if planned['kind'] == 'git' else set()
        if ({k: v for k, v in planned.items() if k not in mutable}
                != {k: v for k, v in actual_source.items() if k not in mutable}):
            raise ValueError('actual source identity differs')
        if planned['kind'] == 'git' and not sources.HEX.fullmatch(actual_source.get('commit', '')):
            raise ValueError('actual Git source commit is not full')
    wanted = {(row['name'], row['arch']) for row in package['policy']['outputs']}
    found = set()
    for file in receipt['files']:
        identity = (file['name'], file['arch'])
        filename = f"{file['name']}-{metadata['version'].split(':', 1)[-1]}-{file['arch']}.pkg.tar.zst"
        if identity in found or identity not in wanted or file['version'] != metadata['version'] or file['filename'] != filename:
            raise ValueError('native output identity differs')
        found.add(identity)
    if found != wanted:
        raise ValueError('native output set is incomplete')
    return receipt


def publication_record(descriptor):
    receipt = validate_metadata(descriptor['record'], descriptor['evidence'])
    package = package_record(descriptor['record'])
    return {**descriptor['record'], 'harness_sha': receipt['harness_sha'], 'producer': descriptor['producer'],
            'run_id': descriptor['producer']['run_id'],
            'run_attempt': descriptor['producer']['run_attempt'], 'packages': [{**package, 'lock': receipt['source_lock'],
            'input_digest': receipt['input_digest'], 'request_input_digest': package['input_digest'],
            'expected_srcinfo': receipt['metadata']['srcinfo'], 'metadata': receipt['metadata'], 'reuse': False}]}


def lookup(package, record=None):
    descriptor = package.get('build')
    request_id = package.get('request_id') or (record or {}).get('request_id')
    if descriptor is None:
        if request_id is None:
            return None
        descriptor = recipe_state.load_optional('build-result', request_id)
    if descriptor is None:
        return None
    if descriptor.get('schema') != 3 or descriptor['pkgbase'] != package['pkgbase']:
        raise ValueError('invalid direct build descriptor')
    if request_id is not None and descriptor['request_id'] != request_id:
        raise ValueError('direct build request differs')
    if descriptor['record']['request_id'] != descriptor['request_id']:
        raise ValueError('descriptor does not bind original request')
    if record is not None and descriptor['record'] != record:
        raise ValueError('descriptor differs from original request')
    validate_metadata(descriptor['record'], descriptor['evidence'])
    if descriptor['evidence'].get('producer') != descriptor['producer']:
        raise ValueError('native evidence differs from exact descriptor producer')
    return descriptor


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


def attestation_context(record, producer, signer):
    package = package_record(record, producer['pkgbase'])
    return {'schema': 2, 'archive_format': 'native-v3',
            'record_sha256': hashlib.sha256(github_api.canonical(record)).hexdigest(),
            'pkgbase': package['pkgbase'], 'request_id': record['request_id'],
            'input_digest': package['input_digest'], 'producer': producer, 'signer': signer}


def verify_attestation(archive, bundle, record, producer, signer):
    if signer != {key: producer[key] for key in ('run_id', 'run_attempt', 'head_sha', 'path')}:
        raise ValueError('archive attestation is not from original producer')
    context = Path(archive).with_name('attestation-context.json')
    context.write_bytes(github_api.canonical(attestation_context(record, producer, signer)))
    expected = {**signer, 'repository': record['repository']}
    archive_certificate = attestations.verify(Path(archive), bundle, expected)
    context_certificate = attestations.verify(context, bundle, expected)
    if archive_certificate['certificate_sha256'] != context_certificate['certificate_sha256']:
        raise ValueError('archive and context certificates differ')


def unpack_original(archive, destination, record):
    from tools import publish
    publish.safe_extract(archive, destination)
    candidate = destination / 'candidate.json'
    if (not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size > 8 * 1024 * 1024
            or update.load(candidate) != record):
        raise ValueError('archive does not bind exact original request')
    candidate.unlink()
    return destination


def fetch_original(descriptor, archive):
    record = descriptor['record']
    release = github_api.api(route(record) + '/releases/' + str(int(descriptor['release']['id'])))
    if release['draft'] is not True or any(release.get(k) != v for k, v in descriptor['release'].items()):
        raise ValueError('durable draft release identity changed')
    for field, destination, maximum in (
            ('asset', Path(archive), MAXIMUM),
            ('attestation', Path(archive).with_name('attestation.jsonl'), github_api.MAX_JSON)):
        bound = descriptor[field]
        if (not 0 < bound['size'] <= maximum or not DIGEST.fullmatch(bound['sha256'])
                or not any(row['id'] == bound['id'] for row in release['assets'])):
            raise ValueError('invalid durable asset bounds or release membership')
        metadata = github_api.api(route(record) + '/releases/assets/' + str(int(bound['id'])))
        if any(metadata.get(key) != bound[key] for key in ('id', 'name', 'size')):
            raise ValueError('durable archive or attestation asset changed')
        download_asset(record['repository'], bound['id'], destination, bound['size'])
        if destination.stat().st_size != bound['size'] or sha(destination) != bound['sha256']:
            raise ValueError('durable archive or attestation digest differs')
    verify_attestation(archive, Path(archive).with_name('attestation.jsonl'), record,
                       descriptor['producer'], descriptor['attestation']['signer'])


def materialize(descriptor, destination):
    from tools import publish
    lookup({'pkgbase': descriptor['pkgbase'], 'build': descriptor})
    stored = recipe_state.load('build-result', descriptor['request_id'])
    if stored != descriptor or recipe_state.build_request(descriptor['request_id']) != descriptor['record']:
        raise ValueError('inclusion descriptor differs from registered durable result')
    producer_run(descriptor['record'], descriptor['producer'])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = destination.with_name(destination.name + '.tar')
    fetch_original(descriptor, archive)
    unpack_original(archive, destination, descriptor['record'])
    if sha(destination / 'native-evidence.json') != descriptor['evidence_sha256']:
        raise ValueError('original native evidence digest differs')
    if update.load(destination / 'native-evidence.json') != descriptor['evidence']:
        raise ValueError('original native evidence differs from descriptor')
    publish.validate_original_unsigned(destination, publication_record(descriptor))
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
    validate_metadata(record, evidence)
    package = package_record(record)
    producer = producer or {'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'head_sha': os.environ['GITHUB_SHA'], 'path': WORKFLOW, 'pkgbase': package['pkgbase']}
    producer_run(record, producer, completed=False)
    if evidence.get('producer') != producer:
        raise ValueError('native evidence does not identify original producer')
    if any(str(evidence.get(key)) != str(producer[key]) or str(evidence['packages'][0].get(key)) != str(producer[key])
           for key in ('run_id', 'run_attempt')):
        raise ValueError('native evidence is not bound to original worker attempt')
    archive = directory.parent / 'unsigned.tar'
    signer = {key: producer[key] for key in ('run_id', 'run_attempt', 'head_sha', 'path')}
    bundle = directory.parent / 'attestation.jsonl'
    if (not archive.is_file() or archive.is_symlink() or not 0 < archive.stat().st_size <= MAXIMUM
            or not bundle.is_file() or bundle.is_symlink() or not 0 < bundle.stat().st_size <= github_api.MAX_JSON):
        raise ValueError('worker lacks bounded original archive and OIDC attestation')
    verify_attestation(archive, bundle, record, producer, signer)
    tag = 'build-' + record['request_id']
    release = github_api.api(route(record) + '/releases', 'POST', {
        'tag_name': tag, 'target_commitish': record['base'], 'name': tag, 'draft': True,
        'prerelease': False, 'make_latest': 'false', 'body': 'Original unsigned worker transport. Never promote.'})
    asset = upload(record['repository'], release['id'], archive)
    stored_bundle = upload(record['repository'], release['id'], bundle, 'attestation.jsonl')
    for asset_metadata, local, maximum in ((asset, archive, MAXIMUM), (stored_bundle, bundle, github_api.MAX_JSON)):
        readback = directory.parent / ('readback-' + local.name)
        download_asset(record['repository'], asset_metadata['id'], readback, maximum)
        if asset_metadata['size'] != local.stat().st_size or sha(readback) != sha(local):
            raise ValueError('durable original transport readback differs')
    descriptor = {'schema': 3, 'request_id': record['request_id'], 'pkgbase': package['pkgbase'],
        'input_digest': package['input_digest'], 'record': record, 'producer': producer,
        'release': {key: release[key] for key in ('id', 'tag_name', 'target_commitish')},
        'asset': {**{key: asset[key] for key in ('id', 'name', 'size')}, 'sha256': sha(archive)},
        'attestation': {**{key: stored_bundle[key] for key in ('id', 'name', 'size')}, 'sha256': sha(bundle), 'signer': signer},
        'evidence_sha256': sha(directory / 'native-evidence.json'), 'evidence': evidence}
    recipe_state.put_build_result(record['request_id'], descriptor)
    return descriptor
