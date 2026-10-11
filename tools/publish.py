#!/usr/bin/env python3
"""Trusted complete-snapshot publication; native outputs are bounded, unsigned data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import github_api, recipes
from tools.update import extract_tree
from tools.recipe_gate import harness_digest, input_digest, metadata_equivalent, parse_srcinfo, tree_manifest

ROOT = Path(__file__).resolve().parents[1]
MAXIMUM = 805306368
REPOSITORY = 'vith/arch-packages'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]*\Z')
PKG_KEYS = {'pkgname','pkgbase','pkgver','pkgdesc','url','builddate','packager','size','arch','license','replaces','group','depend','optdepend','conflict','provides','backup','xdata','makedepend','checkdepend'}


PACMAN_REPOSITORY = 'vith-gh'
# Historical signed snapshots remain immutable and verifiable under their old name.
SNAPSHOT_REPOSITORIES = (PACMAN_REPOSITORY, 'arch-packages')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def database_assets(repository):
    return {'arch-packages.asc'} | {
        repository + suffix for suffix in (
            '.db', '.db.sig', '.files', '.files.sig',
            '.db.tar.gz', '.db.tar.gz.sig', '.files.tar.gz', '.files.tar.gz.sig')
    }


DATABASES = database_assets(PACMAN_REPOSITORY)


def catalog_database_name(catalog):
    names = [name for name in SNAPSHOT_REPOSITORIES if name + '.db' in catalog['files']]
    if len(names) != 1:
        raise ValueError('Missing or ambiguous pacman repository database')
    return names[0]


def release_url(repository, snapshot, filename):
    return f'https://github.com/{repository}/releases/download/{urllib.parse.quote(snapshot, safe="")}/{urllib.parse.quote(filename, safe="")}'


def validate_catalog(catalog, repository, key_fingerprint):
    if catalog.get('schema') != 1 or catalog.get('repository') != repository:
        raise ValueError('Wrong catalog schema/repository')
    if catalog.get('key_fingerprint') != key_fingerprint:
        raise ValueError('Wrong signing fingerprint')
    if not re.fullmatch(r'[0-9a-f]{40}', catalog.get('accepted_sha', '')):
        raise ValueError('Invalid accepted SHA')
    snapshot = catalog.get('snapshot', '')
    if not NAME.fullmatch(snapshot) or not snapshot.startswith('snapshot-'):
        raise ValueError('Invalid snapshot')
    if not isinstance(catalog.get('recipes'), dict) or not catalog['recipes']:
        raise ValueError('Incomplete recipe manifest')
    for recipe in catalog['recipes'].values():
        if not isinstance(recipe, dict) or not re.fullmatch(r'[0-9a-f]{40}', recipe.get('recipe_commit', '')):
            raise ValueError('Invalid immutable recipe commit')
    for key in ('files', 'source_assets', 'retained_packages', 'retained_snapshots'):
        if not isinstance(catalog.get(key), dict):
            raise ValueError('Missing catalog map: ' + key)
    databases = database_assets(catalog_database_name(catalog))
    if not databases <= catalog['files'].keys():
        raise ValueError('Incomplete database/key assets')
    if {'catalog.json', 'catalog.json.sig'} & catalog['files'].keys():
        raise ValueError('Self-referential catalog')

    def files(entries, expected_snapshot=None):
        for name, entry in entries.items():
            if not NAME.fullmatch(name) or not isinstance(entry, dict) or not SHA256.fullmatch(entry.get('sha256', '')):
                raise ValueError('Invalid asset name/hash')
            url = entry.get('url', '')
            parsed = urllib.parse.urlsplit(url)
            segments = parsed.path.split('/')
            if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
                    or len(segments) != 7 or '/'.join(segments[1:3]) != repository
                    or segments[3:5] != ['releases', 'download']):
                raise ValueError('Asset outside repository release boundary')
            tag = urllib.parse.unquote(segments[5])
            if not NAME.fullmatch(tag) or not tag.startswith('snapshot-') or (expected_snapshot and tag != expected_snapshot):
                raise ValueError('Wrong asset snapshot')
            if url != release_url(repository, tag, name):
                raise ValueError('Noncanonical asset destination')
    files(catalog['files'])
    files({name: catalog['files'][name] for name in databases}, snapshot)
    files(catalog['source_assets'])
    files(catalog['retained_packages'])
    historical_databases = set().union(*(database_assets(name) for name in SNAPSHOT_REPOSITORIES))
    for old, entry in catalog['retained_snapshots'].items():
        if not NAME.fullmatch(old) or not old.startswith('snapshot-') or not isinstance(entry, dict):
            raise ValueError('Invalid retained snapshot')
        files(entry['files'])
        files({name: value for name, value in entry['files'].items() if name in historical_databases}, old)
    for name in catalog['files'].keys() & catalog['retained_packages'].keys():
        if catalog['files'][name] != catalog['retained_packages'][name]:
            raise ValueError('Retained filename was retargeted')
    all_packages = {**catalog['retained_packages'], **catalog['files']}
    if not any(name.endswith('.pkg.tar.zst') for name in catalog['files']):
        raise ValueError('Snapshot contains no packages')
    for name in all_packages:
        if name not in databases and not re.fullmatch(r'.+\.pkg\.tar\.(?:zst|xz|gz)(?:\.sig)?', name):
            raise ValueError('Non-package repo asset')
        if name not in databases and name.endswith(('.zst', '.xz', '.gz')) and name + '.sig' not in all_packages:
            raise ValueError('Missing package signature')
    for name, recipe in catalog['recipes'].items():
        accepted = catalog.get('accepted_recipes', {}).get(name)
        if accepted is not None and (
                not re.fullmatch(r'[0-9a-f]{40}', accepted.get('recipe_commit', ''))
                or accepted.get('tree_sha') != recipe.get('tree_sha')
                or accepted.get('content_digest') != recipe.get('content_digest')):
            raise ValueError('signed reuse differs from accepted recipe content')
        descriptor = recipe.get('build_provenance')
        if descriptor is None:
            # Historical signed catalogs predate durable descriptors.
            continue
        record = descriptor.get('record', {})
        candidates = [p for p in record.get('packages', []) if p.get('pkgbase') == name]
        native = recipe.get('native_evidence', {})
        if descriptor.get('schema') == 3 and native != descriptor.get('evidence'):
            raise ValueError('catalog changes original native evidence')
        receipts = [p for p in native.get('packages', []) if p.get('pkgbase') == name]
        if len(candidates) != 1 or len(receipts) != 1:
            raise ValueError('missing original catalog build provenance')
        original, receipt = candidates[0], receipts[0]
        actual_digest = receipt.get('input_digest') if descriptor.get('schema') == 3 else original.get('input_digest')
        if descriptor.get('schema') == 3 and receipt.get('request_input_digest') != original.get('input_digest'):
            raise ValueError('catalog changes original request identity')
        mapping = recipe.get('accepted_mapping', {})
        if (descriptor.get('pkgbase') != name or descriptor.get('input_digest') != original.get('input_digest')
                or recipe.get('recipe_commit') != original.get('recipe_commit')
                or recipe.get('input_digest') != actual_digest
                or mapping.get('head') != record.get('head')
                or mapping.get('accepted') != recipe.get('accepted_recipe_commit')
                or mapping.get('recipe_tree') != record.get('recipe_tree')):
            raise ValueError('catalog relabels original producer')
        identity = {**record, **descriptor.get('producer', {})} if descriptor.get('schema') == 3 else record
        identity_keys = ('repository', 'head', 'run_id', 'run_attempt') if descriptor.get('schema') == 3 else ('repository','base','head','run_id','run_attempt','image','harness_sha')
        for key in identity_keys:
            if native.get(key) != identity.get(key):
                raise ValueError('catalog original native evidence identity mismatch')
        if (receipt.get('recipe_commit') != original.get('recipe_commit')
                or receipt.get('input_digest') != actual_digest
                or recipe.get('sources') != receipt.get('source_lock', {}).get('sources')):
            raise ValueError('catalog original source provenance mismatch')
        for output in receipt.get('files', []):
            entry = catalog['files'].get(output.get('filename'), {})
            if output.get('sha256') is not None and entry.get('sha256') != output['sha256']:
                raise ValueError('catalog package differs from original build hash')
    return catalog


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, **kwargs).stdout


def clean_env():
    return {k: v for k, v in os.environ.items() if k in {'PATH','LANG','LC_ALL','TMPDIR'}}


def fingerprint():
    value = (ROOT / 'keys/fingerprint').read_text().strip()
    if not re.fullmatch(r'[0-9A-F]{40}', value):
        raise ValueError('invalid committed fingerprint')
    return value


def current_main(head):
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('full accepted SHA required')
    if github_api.api(f'repos/{REPOSITORY}/git/ref/heads/main')['object']['sha'] != head:
        raise ValueError('accepted commit is stale')
    if run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip() != head:
        raise ValueError('control checkout must be exact accepted main')


def keyring(work):
    target = work / 'public.gpg'
    run(['gpg', '--batch', '--yes', '--dearmor', '--output', str(target), str(ROOT / 'keys/arch-packages.asc')], env=clean_env())
    listing = run(['gpg', '--batch', '--show-keys', '--with-colons', str(target)], env=clean_env()).decode()
    primary = []
    want = False
    for line in listing.splitlines():
        fields = line.split(':')
        if fields[0] == 'pub':
            want = True
        elif fields[0] == 'fpr' and want:
            primary.append(fields[9]); want = False
    if primary != [fingerprint()]:
        raise ValueError('committed public key differs from fingerprint')
    return target


def verify(path, signature, ring):
    run(['gpgv', '--keyring', str(ring), str(signature), str(path)], env=clean_env())


def previous_catalog(url, work, ring):
    if not url:
        return None
    prefix = f'https://github.com/{REPOSITORY}/releases/download/'
    if not url.startswith(prefix) or not url.endswith('/catalog.json'):
        raise ValueError('previous catalog must be immutable repository URL')
    tag = url[len(prefix):].split('/')[0]
    if url != release_url(REPOSITORY, tag, 'catalog.json') or not tag.startswith('snapshot-'):
        raise ValueError('invalid previous catalog destination')
    path = github_api.download(url, work / 'previous.json', maximum=32*1024*1024)
    signature = github_api.download(url + '.sig', work / 'previous.sig', maximum=65536)
    verify(path, signature, ring)
    catalog = validate_catalog(json.loads(path.read_text()), REPOSITORY, fingerprint())
    if catalog['snapshot'] != tag:
        raise ValueError('previous catalog snapshot differs from release tag')
    return catalog


def latest_release():
    try:
        release = github_api.api(f'repos/{REPOSITORY}/releases/latest', authenticated=bool(os.environ.get('GITHUB_TOKEN')))
    except github_api.GitHubError as error:
        if error.status == 404:
            return None
        raise
    if release['draft'] or release['prerelease'] or not NAME.fullmatch(release['tag_name']) or not release['tag_name'].startswith('snapshot-'):
        raise ValueError('latest release is not a complete snapshot')
    return {'id': release['id'], 'tag': release['tag_name']}


def catalog_url(release):
    return release_url(REPOSITORY, release['tag'], 'catalog.json') if release else None


def expectations(head, run_id, attempt, previous, pins):
    image = (ROOT / 'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', image):
        raise ValueError('pinned official image required')
    harness = harness_digest(ROOT)
    policy = json.loads((ROOT / 'packages.json').read_text())
    packages = []
    legacy_environment = None
    for item in policy['packages']:
        name = item['pkgbase']
        if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name):
            raise ValueError('invalid enrolled package')
        recipe = ROOT / 'recipes' / name
        lock = json.loads((ROOT / 'inputs' / (name + '.json')).read_text())
        metadata = parse_srcinfo((recipe / '.SRCINFO').read_text())
        from tools.sources import validate_lock
        validate_lock(lock)
        upstream = json.loads((ROOT / 'upstream' / (name + '.json')).read_text())
        aur = upstream.get('aur')
        if aur is not None and (not isinstance(aur, dict) or not re.fullmatch(r'[0-9a-f]{40}', aur.get('commit', '')) or aur.get('url') not in ('https://aur.archlinux.org/' + name + '.git', 'https://github.com/archlinux/aur.git')):
            raise ValueError('invalid accepted optional AUR provenance')
        if lock.get('schema') != 1:
            raise ValueError('accepted lock/native metadata mismatch: ' + name)
        # bind_accepted_builds authenticates actual metadata for changed outputs.
        digest = input_digest(recipe, lock, item, image, harness)
        old = previous['recipes'].get(name) if previous else None
        content_digest = input_digest(recipe, lock, item, 'package-content-v1', '')
        reuse = bool(old and (old['input_digest'] == digest or old.get('content_digest') == content_digest))
        if old and not reuse and 'content_digest' not in old and previous.get('accepted_sha'):
            if legacy_environment is None:
                with tempfile.TemporaryDirectory(prefix='previous-build-', dir=ROOT.parent) as session:
                    archive = Path(session) / 'control.tar.gz'
                    checkout = Path(session) / 'control'
                    github_api.download(f'https://api.github.com/repos/{REPOSITORY}/tarball/{previous["accepted_sha"]}', archive)
                    extract_tree(archive, checkout)
                    legacy_environment = ((checkout / 'build-image.txt').read_text().strip(), harness_digest(checkout))
            reuse = old['input_digest'] == input_digest(recipe, lock, item, *legacy_environment)
        packages.append({'recipe_commit':pins[name],'pkgbase':name,'recipe_dir':'recipes/' + name,'lock':lock,'policy':item,'input_digest':digest,'content_digest':content_digest,'tree_sha':hashlib.sha256(github_api.canonical(tree_manifest(recipe))).hexdigest(),'metadata':metadata,'aur':aur,'reuse':reuse})
    output_names = [output['name'] for package in packages for output in package['policy']['outputs']]
    if len({p['pkgbase'] for p in packages}) != len(packages) or not packages or len(set(output_names)) != len(output_names):
        raise ValueError('duplicate/empty package enrollment')
    active_pins = {package['pkgbase']: pins[package['pkgbase']] for package in packages}
    return {'schema':1,'repository':REPOSITORY,'base':head,'head':head,'run_id':str(run_id),'run_attempt':str(attempt),'image':image,'harness_sha':harness,'recipe_pins':active_pins,'packages':packages}


def bind_accepted_builds(plan):
    """Resolve accepted successful output without downloading package bytes."""
    from tools import build_store, recipe_acceptance
    for package in plan['packages']:
        if package['reuse']:
            continue
        accepted = recipe_acceptance.verify_accepted_build(None, package, plan['head'])
        expected_descriptor = accepted['acceptance']['build']
        descriptor = build_store.lookup({**package, 'build':expected_descriptor}, record=accepted['record'])
        if descriptor is None:
            raise ValueError('missing original successful build output: ' + package['pkgbase'])
        if descriptor != expected_descriptor:
            raise ValueError('durable descriptor differs from accepted original build')
        verified = recipe_acceptance.verify_accepted_build(descriptor['record'], package, plan['head'])
        receipt = descriptor['evidence']['packages'][0]
        recipe_srcinfo = (ROOT / package['recipe_dir'] / '.SRCINFO').read_text()
        if not metadata_equivalent(recipe_srcinfo, receipt['metadata']['srcinfo'], dynamic=receipt['metadata'].get('dynamic_pkgver', False)):
            raise ValueError('accepted native declarations differ from recipe')
        package['metadata'] = parse_srcinfo(receipt['metadata']['srcinfo'])
        if package['metadata']['version'] != package['lock']['version']:
            raise ValueError('accepted native version differs from accepted source lock')
        package['build'] = descriptor
        package['acceptance'] = verified['mapping']


def prepare(head, run_id, attempt, directory):
    current_main(head)
    pins = recipes.materialize(ROOT, head, REPOSITORY, extract_tree)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='plan-', dir=directory.parent) as session:
        work = Path(session)
        ring = keyring(work)
        previous_release = latest_release()
        url = catalog_url(previous_release)
        previous = previous_catalog(url, work, ring)
        plan = expectations(head, run_id, attempt, previous, pins)
    bind_accepted_builds(plan)
    (directory / 'publication-plan.json').write_bytes(github_api.canonical({'previous_release':previous_release,'previous_url':url,'expected':plan}))
    return sum(not package['reuse'] for package in plan['packages'])



def safe_extract(archive, destination):
    """Extract bounded original worker data without links or nested paths."""
    destination.mkdir()
    with tarfile.open(archive, 'r:*') as stream:
        entries = []
        for entry in stream:
            if len(entries) >= 1000:
                raise ValueError('too many unsigned artifact entries')
            entries.append(entry)
        names = set()
        total = 0
        for entry in entries:
            if not entry.isfile() or not NAME.fullmatch(entry.name) or entry.name in names or entry.size > MAXIMUM:
                raise ValueError('unsafe unsigned artifact member')
            names.add(entry.name)
            total += entry.size
            if total > 8 * MAXIMUM:
                raise ValueError('unsigned artifact total size exceeded')
        for entry in entries:
            with stream.extractfile(entry) as source, (destination / entry.name).open('xb') as target:
                shutil.copyfileobj(source, target)


def bounded_metadata(argv, maximum):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=clean_env())
    try:
        data = process.stdout.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('archive metadata exceeds bound')
        if process.wait(timeout=90):
            raise ValueError('archive metadata reader failed')
        return data
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def pkginfo(path):
    data = bounded_metadata(['bsdtar','-xOf',str(path),'.PKGINFO'], 1024*1024)
    if len(data) > 1024*1024:
        raise ValueError('oversized package metadata')
    fields = {}
    for line in data.decode().splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition(' = ')
        if not separator or key not in PKG_KEYS or not value:
            raise ValueError('invalid literal .PKGINFO field')
        fields.setdefault(key, []).append(value)
    for key in ('pkgname','pkgver','arch'):
        if len(fields.get(key, [])) != 1:
            raise ValueError('missing/duplicate package identity')
    return {k:fields[k][0] for k in ('pkgname','pkgver','arch')}


def filenames(package):
    version = package['lock']['version'].split(':',1)[-1]
    return {f"{o['name']}-{version}-{o['arch']}.pkg.tar.zst":{'pkgname':o['name'],'pkgver':package['lock']['version'],'arch':o['arch']} for o in package['policy']['outputs']}


def validate_native_receipt(directory, package, receipt, original_record, original_package):
    returned_metadata = receipt.get('metadata', {})
    native_text = returned_metadata.get('srcinfo')
    if not isinstance(native_text, str) or parse_srcinfo(native_text) != package['metadata']:
        raise ValueError('native prepared metadata differs from accepted metadata')
    if {k:v for k,v in returned_metadata.items() if k not in ('srcinfo', 'dynamic_pkgver')} != package['metadata']:
        raise ValueError('native metadata fields differ')
    wanted = {'recipe_commit':original_package['recipe_commit'],
              'input_digest':original_package['input_digest'], 'tree_sha':package['tree_sha'],
              'source_lock':package['lock'],
              **{k:original_record[k] for k in ('run_id','run_attempt','image','harness_sha')}}
    for field, value in wanted.items():
        if receipt.get(field) != value:
            raise ValueError('native receipt mismatch: ' + field)
    output_receipts = receipt.get('files', [])
    expected_files = filenames(package)
    if len(output_receipts) != len(expected_files) or {f.get('filename') for f in output_receipts} != set(expected_files):
        raise ValueError('receipt output set differs')
    for output in output_receipts:
        path = directory / output['filename']
        info = pkginfo(path)
        if info != expected_files[path.name] or any(output.get(k) != info[v] for k,v in [('name','pkgname'),('version','pkgver'),('arch','arch')]):
            raise ValueError('package metadata/hash differs')


def validate_original_unsigned(directory, plan):
    """Validate an original worker archive without publication transport or remapping."""
    evidence_path = directory / 'native-evidence.json'
    if evidence_path.stat().st_size > 32*1024*1024:
        raise ValueError('oversized native evidence')
    evidence = json.loads(evidence_path.read_text())
    for key in ('schema','repository','base','head','run_id','run_attempt','image','harness_sha','recipe_pins'):
        if evidence.get(key) != plan[key]:
            raise ValueError('native artifact identity mismatch: ' + key)
    receipts = evidence.get('packages', [])
    if len(receipts) != len(plan['packages']) or {p.get('pkgbase') for p in receipts} != {p['pkgbase'] for p in plan['packages']}:
        raise ValueError('missing/duplicate native receipts')
    expected = set()
    for package in plan['packages']:
        receipt = next(p for p in receipts if p['pkgbase'] == package['pkgbase'])
        planned_srcinfo = package['expected_srcinfo']
        if not metadata_equivalent(planned_srcinfo, receipt['metadata']['srcinfo'], dynamic=receipt['metadata'].get('dynamic_pkgver', False)):
            raise ValueError('native declarations differ from requested recipe')
        actual = {**package, 'lock':receipt['source_lock'],
                  'metadata':parse_srcinfo(receipt['metadata']['srcinfo'])}
        expected.update(filenames(actual))
        validate_native_receipt(directory, actual, receipt, plan, package)
    if {p.name for p in directory.iterdir()} != expected | {'native-evidence.json'}:
        raise ValueError('extra/missing unsigned outputs')
    return evidence


def materialize_builds(directory, plan):
    """Fetch and authenticate each original output once, before key import."""
    from tools import build_store, recipe_acceptance
    directory.mkdir()
    originals = {}
    for package in plan['packages']:
        if package['reuse']:
            continue
        descriptor = package['build']
        accepted = recipe_acceptance.verify_accepted_build(descriptor['record'], package, plan['head'])
        if accepted['mapping'] != package['acceptance']:
            raise ValueError('accepted merge mapping changed')
        original = build_store.materialize(descriptor, directory.parent / ('original-' + package['pkgbase']))
        native = json.loads((original / 'native-evidence.json').read_text())
        originals[package['pkgbase']] = native
        for name in filenames(package):
            shutil.move(original / name, directory / name)
    return originals


class Signer:
    def __init__(self, home):
        home.mkdir(mode=0o700)
        self.env = clean_env() | {'GNUPGHOME':str(home)}
        secret = os.environ['ARCH_SIGNING_KEY'].encode()
        run(['gpg','--batch','--import'], input=secret, env=self.env)
        listing = run(['gpg','--batch','--with-colons','--list-secret-keys'], env=self.env).decode()
        primary = []
        want = False
        for line in listing.splitlines():
            fields = line.split(':')
            if fields[0] == 'sec': want = True
            elif fields[0] == 'fpr' and want:
                primary.append(fields[9]); want = False
        if primary != [fingerprint()] or os.environ['ARCH_SIGNING_FINGERPRINT'] != fingerprint():
            raise ValueError('imported dedicated signing key mismatch')

    def sign(self, path):
        run(['gpg','--batch','--yes','--pinentry-mode','loopback','--passphrase-fd','0','--local-user',fingerprint(),'--detach-sign','--output',str(path)+'.sig',str(path)], input=(os.environ['ARCH_SIGNING_PASSPHRASE']+'\n').encode(), env=self.env)


def retrieve(entry, name, destination):
    if not NAME.fullmatch(name):
        raise ValueError('unsafe asset name')
    path = github_api.download(entry['url'], destination / name)
    if sha(path) != entry['sha256']:
        raise ValueError('public asset hash mismatch: ' + name)
    return path


def check_collisions(previous, name, digest, reused=False):
    if not previous:
        return
    entries = {**previous['retained_packages'], **previous['files']}
    if name in entries and (entries[name]['sha256'] != digest or not reused):
        raise ValueError('filename collision requires unchanged signed reuse: ' + name)


def database(image, packages, output):
    # Explicit env and these two mounts are the entire container boundary. The
    # secret home, checkout, host home, socket and tokens are never mounted.
    output.chmod(0o777)
    run(['docker','run','--rm','--platform','linux/amd64','--cap-drop=ALL','--security-opt=no-new-privileges','--mount',f'type=bind,src={packages},dst=/packages,readonly','--mount',f'type=bind,src={output},dst=/out',image,'repo-add','--include-sigs',f'/out/{PACMAN_REPOSITORY}.db.tar.gz', *['/packages/'+p.name for p in sorted(packages.glob('*.pkg.tar.zst'))]], env=clean_env())
    for suffix in ('db','files'):
        path = output / f'{PACMAN_REPOSITORY}.{suffix}.tar.gz'
        if path.is_symlink() or not path.is_file():
            raise ValueError('repo-add archive missing')
        alias = output / f'{PACMAN_REPOSITORY}.{suffix}'
        alias.unlink(missing_ok=True)
        shutil.copyfile(path, alias)

    verify_database(packages, output)


def verify_database(packages, output, repository=PACMAN_REPOSITORY):
    # Verify the actual database describes exactly the complete package bytes.
    found = {}
    with tarfile.open(output / f'{repository}.db', 'r:gz') as archive:
        for entry in archive.getmembers():
            if entry.name.endswith('/desc'):
                values = archive.extractfile(entry).read().decode().splitlines()
                fields = {}
                for index, value in enumerate(values):
                    if value.startswith('%') and value.endswith('%') and index+1 < len(values):
                        fields[value] = values[index+1]
                name = fields.get('%FILENAME%')
                if name in found or name not in {p.name for p in packages.glob('*.pkg.tar.zst')}:
                    raise ValueError('database extra/duplicate package')
                path = packages / name
                info = pkginfo(path)
                if fields.get('%SHA256SUM%') != sha(path) or fields.get('%NAME%') != info['pkgname'] or fields.get('%VERSION%') != info['pkgver'] or fields.get('%ARCH%') != info['arch'] or not fields.get('%PGPSIG%'):
                    raise ValueError('database package identity/hash/signature differs')
                found[name] = fields
    if set(found) != {p.name for p in packages.glob('*.pkg.tar.zst')}:
        raise ValueError('database missing package')


def upload_release(snapshot, head, assets, names):
    release = github_api.api(f'repos/{REPOSITORY}/releases','POST',{'tag_name':snapshot,'target_commitish':head,'name':snapshot,'draft':True,'prerelease':False,'make_latest':'false','body':'Complete signed snapshot; retained releases are immutable.'})
    for name in sorted(names):
        github_api.upload_asset(REPOSITORY, release['id'], assets / name)
    remote = list(github_api.pages(f'repos/{REPOSITORY}/releases/{release["id"]}/assets'))
    if len(remote) != len(names) or {a['name'] for a in remote} != names:
        raise ValueError('partial/extra release upload')
    github_api.api(f'repos/{REPOSITORY}/releases/{release["id"]}','PATCH',{'draft':False,'make_latest':'false'})
    ref = github_api.api(f'repos/{REPOSITORY}/git/ref/tags/{snapshot}')['object']
    for _ in range(8):
        if ref['type'] == 'commit':
            break
        if ref['type'] != 'tag':
            raise ValueError('snapshot tag does not identify a commit')
        ref = github_api.api(f'repos/{REPOSITORY}/git/tags/{ref["sha"]}')['object']
    if ref['type'] != 'commit' or ref['sha'] != head:
        raise ValueError('snapshot release target differs from accepted SHA')
    return release


def public_readback(catalog, assets, readback, ring):
    readback.mkdir()
    for name, entry in {**catalog['files'], **catalog['source_assets']}.items():
        # Each complete snapshot also carries exact copies of reused bytes.
        retrieve({'url':release_url(REPOSITORY,catalog['snapshot'],name),'sha256':entry['sha256']}, name, readback)
    for name in ('catalog.json','catalog.json.sig'):
        path = github_api.download(release_url(REPOSITORY,catalog['snapshot'],name), readback / name, maximum=32*1024*1024)
        if assets is not None and sha(path) != sha(assets / name):
            raise ValueError('public catalog readback differs')
    for name in catalog['files']:
        if name.endswith('.sig'):
            verify(readback / name[:-4], readback / name, ring)
    verify(readback / 'catalog.json', readback / 'catalog.json.sig', ring)
    if json.loads((readback / 'catalog.json').read_text()) != catalog:
        raise ValueError('signed readback catalog differs')
    repository = catalog_database_name(catalog)
    for suffix in ('db','files'):
        if sha(readback / f'{repository}.{suffix}') != sha(readback / f'{repository}.{suffix}.tar.gz'):
            raise ValueError('pacman alias differs from canonical archive')
    verify_database(readback, readback, repository)


def verified_snapshot(release, work, ring):
    work.mkdir()
    catalog = previous_catalog(catalog_url(release), work, ring)
    if catalog['snapshot'] != release['tag']:
        raise ValueError('release/catalog identity differs')
    public_readback(catalog, None, work / 'complete', ring)
    return catalog


def latest_catalog(work, ring):
    stable = work / 'latest.json'
    signature = work / 'latest.json.sig'
    url = f'https://github.com/{REPOSITORY}/releases/latest/download/catalog.json'
    github_api.download(url, stable, maximum=32*1024*1024)
    github_api.download(url + '.sig', signature, maximum=65536)
    verify(stable, signature, ring)
    return json.loads(stable.read_text())


def promote(target, previous, catalog, ring, work):
    if latest_release() != previous:
        raise ValueError('latest changed before promotion')
    try:
        github_api.api(f'repos/{REPOSITORY}/releases/{target["id"]}', 'PATCH', {'make_latest':'true'})
    except Exception:
        # A lost response is not evidence of failure or permission to roll back.
        # Reconcile only the actual public latest pointer and signed bytes.
        if latest_release() != target:
            raise RuntimeError('promotion ambiguous; latest is not the requested snapshot') from None
    for attempt in range(12):
        observed_release = latest_release()
        if observed_release == target:
            break
        if observed_release != previous:
            raise ValueError('latest changed to an unrelated snapshot during promotion')
        time.sleep(5)
    else:
        raise ValueError('promotion did not select requested snapshot')
    work.mkdir()
    active = latest_catalog(work, ring)
    validate_catalog(active, REPOSITORY, fingerprint())
    if active != catalog or active['snapshot'] != target['tag'] or latest_release() != target:
        raise ValueError('active catalog changed during verification')


def rollback(args):
    current_main(run(['git','-C',str(ROOT),'rev-parse','HEAD']).decode().strip())
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=False)
    ring = keyring(work)
    previous = latest_release()
    if previous != {'id':args.expected_release_id,'tag':args.expected_tag}:
        raise ValueError('rollback expected latest differs')
    active = verified_snapshot(previous, work / 'current', ring)
    if args.target_tag not in active['retained_snapshots']:
        raise ValueError('rollback target is not a retained snapshot')
    remote = github_api.api(f'repos/{REPOSITORY}/releases/tags/{urllib.parse.quote(args.target_tag, safe="")}', authenticated=False)
    if remote['draft'] or remote['prerelease'] or remote['tag_name'] != args.target_tag:
        raise ValueError('rollback target is not public complete release')
    target = {'id':remote['id'],'tag':remote['tag_name']}
    catalog = verified_snapshot(target, work / 'target', ring)
    if catalog['files'] != active['retained_snapshots'][args.target_tag]['files']:
        raise ValueError('retained rollback catalog differs')
    result = {'previous_release':previous,'activated_release':target,'activation':'pending','operation':'rollback'}
    receipt = work / 'publication-result.json'
    receipt.write_bytes(github_api.canonical(result))
    promote(target, previous, catalog, ring, work / 'active-readback')
    result['activation'] = 'verified'
    receipt.write_bytes(github_api.canonical(result))
    return result


def publish(args):
    current_main(args.accepted_sha)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=False)
    ring = keyring(work)
    plan_path = Path(args.plan)
    if plan_path.is_symlink() or not plan_path.is_file() or plan_path.stat().st_size > 32*1024*1024:
        raise ValueError('invalid bounded publication plan')
    carried = json.loads(plan_path.read_text())
    if carried['previous_release'] != latest_release() or carried['previous_url'] != catalog_url(carried['previous_release']):
        raise ValueError('previous signed catalog no longer active')
    pins = recipes.materialize(ROOT, args.accepted_sha, REPOSITORY, extract_tree)
    previous = previous_catalog(carried['previous_url'], work, ring)
    plan = expectations(args.accepted_sha, args.run_id, args.run_attempt, previous, pins)
    bind_accepted_builds(plan)
    if carried['expected'] != plan:
        raise ValueError('build plan differs from accepted trusted inputs')
    unsigned = work / 'unsigned'
    from tools.source_review import verify_source_evidence
    source_evidence = verify_source_evidence(args.accepted_sha)
    originals = materialize_builds(unsigned, plan)
    packages = work / 'packages'; packages.mkdir()
    assets = work / 'assets'; assets.mkdir()
    snapshot = f'snapshot-{args.accepted_sha}-{args.run_id}-{args.run_attempt}'
    catalog = {'schema':1,'repository':REPOSITORY,'snapshot':snapshot,'accepted_sha':args.accepted_sha,'key_fingerprint':fingerprint(),'recipes':{},'files':{},'source_assets':dict(previous['source_assets']) if previous else {},'retained_packages':{},'retained_snapshots':dict(previous['retained_snapshots']) if previous else {}}
    catalog['publication'] = {'accepted_main':args.accepted_sha, 'run_id':str(args.run_id), 'run_attempt':str(args.run_attempt), 'source_evidence':source_evidence}
    catalog['accepted_recipes'] = {
        p['pkgbase']:{'recipe_commit':p['recipe_commit'], 'tree_sha':p['tree_sha'],
                      'content_digest':p['content_digest']}
        for p in plan['packages']
    }
    if previous:
        catalog['retained_snapshots'][previous['snapshot']] = {'files':previous['files']}
        catalog['retained_packages'] = {**previous['retained_packages'], **{n:e for n,e in previous['files'].items() if '.pkg.tar.' in n}}
    new_names = set()
    for package in plan['packages']:
        name = package['pkgbase']
        lock = package['lock']
        if package['reuse']:
            # A new publisher/control never impersonates the original signed producer.
            catalog['recipes'][name] = {**previous['recipes'][name], 'content_digest':package['content_digest']}
        else:
            descriptor = package['build']
            original_record = descriptor['record']
            original_package = next(p for p in original_record['packages'] if p['pkgbase'] == name)
            catalog['recipes'][name] = {
                'recipe_commit':original_package['recipe_commit'], 'accepted_recipe_commit':package['recipe_commit'],
                'accepted_mapping':package['acceptance'], 'tree_sha':package['tree_sha'],
                'input_digest':originals[name]['packages'][0]['input_digest'], 'content_digest':package['content_digest'],
                'version':lock['version'], 'aur':package['aur'], 'sources':lock['sources'],
                'build_provenance':descriptor, 'native_evidence':originals[name],
            }
        for filename, identity in filenames(package).items():
            if package['reuse']:
                if previous['recipes'][name]['version'] != lock['version']:
                    raise ValueError('reuse version mismatch')
                for asset in (filename, filename+'.sig'):
                    entry = previous['files'][asset]
                    retrieve(entry, asset, packages)
                    catalog['files'][asset] = entry
                verify(packages / filename, packages / (filename+'.sig'), ring)
                if pkginfo(packages / filename) != identity:
                    raise ValueError('reused package native identity mismatch')
            else:
                check_collisions(previous, filename, sha(unsigned / filename))
                shutil.copyfile(unsigned / filename, packages / filename)
                new_names.update((filename, filename+'.sig'))
        if name == 'nasctui':
            source = next((s for s in lock['sources'] if s['id'] == 'nasc-source' and s['kind'] == 'archive'), None)
            if not source:
                raise ValueError('nasc corresponding source lock required')
            source_name = 'nasc-source-' + source['checksums']['sha256'] + '.tar.gz'
            source_path = github_api.download(source['url'], assets / source_name)
            if sha(source_path) != source['checksums']['sha256']:
                raise ValueError('nasc corresponding source hash mismatch')
            listing = bounded_metadata(['bsdtar','-tf',str(source_path)], 8*1024*1024).decode().splitlines()
            licenses = [entry for entry in listing if entry.endswith('/LICENSE') and len(Path(entry).parts) == 2 and '..' not in Path(entry).parts and not entry.startswith('/')]
            if len(licenses) != 1:
                raise ValueError('nasc source license missing/ambiguous')
            license_bytes = bounded_metadata(['bsdtar','-xOf',str(source_path),licenses[0]], 1024*1024)
            if not license_bytes or len(license_bytes) > 1024*1024:
                raise ValueError('invalid nasc source license')
            license_name = 'nasc-license-' + hashlib.sha256(license_bytes).hexdigest() + '.txt'
            (assets / license_name).write_bytes(license_bytes)
            for asset in (source_name, license_name):
                digest = sha(assets / asset)
                if asset in catalog['source_assets']:
                    if catalog['source_assets'][asset]['sha256'] != digest:
                        raise ValueError('corresponding source filename collision')
                else:
                    catalog['source_assets'][asset] = {'url':release_url(REPOSITORY,snapshot,asset),'sha256':digest}
                    new_names.add(asset)
    # Validation before key import includes signed URL boundaries and all source bytes.
    source_work = work / 'sources'; source_work.mkdir()
    for name, entry in catalog['source_assets'].items():
        if name not in new_names:
            retrieve(entry, name, source_work)
    signer = Signer(work / 'gnupg')
    for name in sorted(new_names):
        if name.endswith('.pkg.tar.zst'):
            signer.sign(packages / name)
            verify(packages / name, packages / (name+'.sig'), ring)
    database(plan['image'], packages, assets)
    for name in sorted(DATABASES - {'arch-packages.asc'}):
        if not name.endswith('.sig'):
            signer.sign(assets / name)
    shutil.copyfile(ROOT / 'keys/arch-packages.asc', assets / 'arch-packages.asc')
    upload_names = set(new_names) | DATABASES | set(catalog['files']) | set(catalog['source_assets'])
    for name in catalog['source_assets']:
        if name not in new_names:
            shutil.copyfile(source_work / name, assets / name)
    for path in packages.iterdir():
        shutil.copyfile(path, assets / path.name)
    for name in upload_names:
        if name not in catalog['source_assets'] and name not in catalog['files']:
            catalog['files'][name] = {'url':release_url(REPOSITORY,snapshot,name),'sha256':sha(assets / name)}
    validate_catalog(catalog, REPOSITORY, fingerprint())
    (assets / 'catalog.json').write_bytes(github_api.canonical(catalog))
    signer.sign(assets / 'catalog.json')
    current_main(args.accepted_sha)
    expected_uploads = upload_names | {'catalog.json','catalog.json.sig'}
    release = upload_release(snapshot, args.accepted_sha, assets, expected_uploads)
    if latest_release() != carried['previous_release']:
        raise ValueError('latest changed during non-latest publication; refusing activation')
    public_readback(catalog, assets, work / 'readback', ring)
    current_main(args.accepted_sha)
    result = {'snapshot':snapshot,'catalog_url':release_url(REPOSITORY,snapshot,'catalog.json'),'previous_release':carried['previous_release'],'activated_release':{'id':release['id'],'tag':snapshot},'activation':'pending'}
    receipt = work / 'publication-result.json'
    receipt.write_bytes(github_api.canonical(result))
    promote(result['activated_release'], result['previous_release'], catalog, ring, work / 'active-readback')
    result['activation'] = 'verified'
    receipt.write_bytes(github_api.canonical(result))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
            summary.write('## Signed publication\n```json\n' + json.dumps(result,indent=2) + '\n```\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('plan','publish'):
        p = sub.add_parser(command)
        p.add_argument('--accepted-sha',required=True)
        p.add_argument('--run-id',required=True,type=int)
        p.add_argument('--run-attempt',required=True,type=int)
        if command == 'plan':
            p.add_argument('--output-dir',required=True)
        else:
            p.add_argument('--plan',required=True)
            p.add_argument('--work-dir',required=True)
    p = sub.add_parser('rollback')
    p.add_argument('--target-tag',required=True)
    p.add_argument('--expected-release-id',required=True,type=int)
    p.add_argument('--expected-tag',required=True)
    p.add_argument('--work-dir',required=True)
    args = parser.parse_args()
    if args.command == 'rollback':
        print(json.dumps(rollback(args),sort_keys=True))
        return
    if args.run_id < 1 or args.run_attempt < 1:
        raise ValueError('positive run identity required')
    if args.command == 'plan':
        print(json.dumps({'changed':prepare(args.accepted_sha,args.run_id,args.run_attempt,args.output_dir)}))
    else:
        try:
            print(json.dumps(publish(args),sort_keys=True))
        finally:
            home = Path(args.work_dir).resolve() / 'gnupg'
            if home.is_dir():
                subprocess.run(['gpgconf','--homedir',str(home),'--kill','gpg-agent'], env=clean_env(), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                shutil.rmtree(home)


if __name__ == '__main__':
    main()
