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
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import cloudflare, github_api
from tools.recipe_gate import harness_digest, input_digest, parse_srcinfo, tree_manifest

ROOT = Path(__file__).resolve().parents[1]
MAXIMUM = 805306368
REPOSITORY = 'vith/arch-packages'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]*\Z')
PKG_KEYS = {'pkgname','pkgbase','pkgver','pkgdesc','url','builddate','packager','size','arch','license','replaces','group','depend','optdepend','conflict','provides','backup','xdata','makedepend','checkdepend'}


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
    run(['gpg', '--batch', '--yes', '--dearmor', '--output', str(target), str(ROOT / 'keys/n3t-arch.asc')], env=clean_env())
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
    if url != cloudflare.release_url(REPOSITORY, tag, 'catalog.json') or not tag.startswith('snapshot-'):
        raise ValueError('invalid previous catalog destination')
    path = github_api.download(url, work / 'previous.json', maximum=32*1024*1024)
    signature = github_api.download(url + '.sig', work / 'previous.sig', maximum=65536)
    verify(path, signature, ring)
    return cloudflare.validate_catalog(json.loads(path.read_text()), REPOSITORY, fingerprint())


def active_catalog_url():
    # Redirect discovery is public and carries no credentials. Only the final fixed
    # GitHub URL is accepted by previous_catalog, never arbitrary destinations.
    with urllib.request.urlopen('https://arch.packages.n3t.work/catalog.json', timeout=90) as response:
        return response.url


def expectations(head, run_id, attempt, previous):
    image = (ROOT / 'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', image):
        raise ValueError('pinned official image required')
    harness = harness_digest(ROOT)
    policy = json.loads((ROOT / 'packages.json').read_text())
    packages = []
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
        if lock.get('schema') != 1 or lock.get('version') != metadata['version']:
            raise ValueError('accepted lock/native metadata mismatch: ' + name)
        digest = input_digest(recipe, lock, item, image, harness)
        old = previous['recipes'].get(name) if previous else None
        packages.append({'pkgbase':name,'recipe_dir':'recipes/' + name,'lock':lock,'policy':item,'input_digest':digest,'tree_sha':hashlib.sha256(github_api.canonical(tree_manifest(recipe))).hexdigest(),'metadata':metadata,'aur':aur,'reuse':bool(old and old['input_digest'] == digest)})
    output_names = [output['name'] for package in packages for output in package['policy']['outputs']]
    if len({p['pkgbase'] for p in packages}) != len(packages) or not packages or len(set(output_names)) != len(output_names):
        raise ValueError('duplicate/empty package enrollment')
    return {'schema':1,'repository':REPOSITORY,'base':head,'head':head,'run_id':str(run_id),'run_attempt':str(attempt),'image':image,'harness_sha':harness,'packages':packages}


def prepare(head, run_id, attempt, directory, previous_url, bootstrap):
    current_main(head)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='plan-', dir=directory.parent) as session:
        work = Path(session)
        ring = keyring(work)
        url = previous_url or (None if bootstrap else active_catalog_url())
        previous = previous_catalog(url, work, ring)
        plan = expectations(head, run_id, attempt, previous)
    (directory / 'publication-plan.json').write_bytes(github_api.canonical({'previous_url':url,'expected':plan}))
    bundle_dir = directory / 'bundle'
    bundle_dir.mkdir()
    changed = []
    for package in plan['packages']:
        if not package['reuse']:
            shutil.copytree(ROOT / package['recipe_dir'], bundle_dir / package['recipe_dir'], symlinks=True)
            changed.append({k:package[k] for k in ('pkgbase','recipe_dir','lock','policy','input_digest')})
    bundle = {k:v for k,v in plan.items() if k != 'packages'}
    bundle['packages'] = changed
    (bundle_dir / 'bundle.json').write_bytes(github_api.canonical(bundle))
    return len(changed)


def safe_extract(archive, destination):
    destination.mkdir()
    with tarfile.open(archive, 'r:*') as stream:
        entries = stream.getmembers()
        if len(entries) > 1000:
            raise ValueError('too many unsigned artifact entries')
        names = set()
        total = 0
        for entry in entries:
            if not entry.isfile() or not NAME.fullmatch(entry.name) or entry.name in names or entry.size > MAXIMUM:
                raise ValueError('unsafe unsigned artifact member')
            names.add(entry.name); total += entry.size
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


def validate_unsigned(directory, plan):
    changed = [p for p in plan['packages'] if not p['reuse']]
    expected = {n for p in changed for n in filenames(p)}
    actual = {p.name for p in directory.iterdir()}
    if actual != expected | {'native-evidence.json'}:
        raise ValueError('extra/missing unsigned outputs')
    evidence_path = directory / 'native-evidence.json'
    if evidence_path.stat().st_size > 32*1024*1024:
        raise ValueError('oversized native evidence')
    evidence = json.loads(evidence_path.read_text())
    for key in ('schema','repository','base','head','run_id','run_attempt','image','harness_sha'):
        if evidence.get(key) != plan[key]:
            raise ValueError('native artifact identity mismatch: ' + key)
    receipts = evidence.get('packages', [])
    if len(receipts) != len(changed) or {p.get('pkgbase') for p in receipts} != {p['pkgbase'] for p in changed}:
        raise ValueError('missing/duplicate native receipts')
    for package in changed:
        receipt = next(p for p in receipts if p['pkgbase'] == package['pkgbase'])
        returned_metadata = receipt.get('metadata', {})
        native_text = returned_metadata.get('srcinfo')
        if not isinstance(native_text, str) or parse_srcinfo(native_text) != package['metadata']:
            raise ValueError('native prepared metadata differs from accepted metadata')
        if {k:v for k,v in returned_metadata.items() if k != 'srcinfo'} != package['metadata']:
            raise ValueError('native metadata fields differ')
        for field, wanted in {'input_digest':package['input_digest'],'tree_sha':package['tree_sha'],'source_lock':package['lock'],**{k:plan[k] for k in ('run_id','run_attempt','image','harness_sha')}}.items():
            if receipt.get(field) != wanted:
                raise ValueError('native receipt mismatch: ' + field)
        if package['pkgbase'] == 'oh-my-pi-vith-git':
            match = re.fullmatch(r'(?:[0-9]+:)?(.+)\.vith\.r([0-9]+)\.g([0-9a-f]{12})-[0-9]+(?:\.[0-9]+)*', package['lock']['version'])
            proof = receipt.get('runtime') or {}
            expected_runtime = f'{match[1]}+vith-fork.{match[2]}.{match[3]}' if match else None
            if not expected_runtime or proof.get('runtime_identity') != expected_runtime or proof.get('cli_version') != 'omp/' + expected_runtime or 'libpipewire-0.3.so.0' not in proof.get('dynamic', '') or not re.fullmatch(r'[0-9a-f]{64}', proof.get('addon_sha256', '')) or not proof.get('help'):
                raise ValueError('native OMP runtime proof differs from accepted identity')
        output_receipts = receipt.get('files', [])
        expected_files = filenames(package)
        if len(output_receipts) != len(expected_files) or {f.get('filename') for f in output_receipts} != set(expected_files):
            raise ValueError('receipt output set differs')
        for output in output_receipts:
            path = directory / output['filename']
            info = pkginfo(path)
            if info != expected_files[path.name] or output.get('sha256') != sha(path) or any(output.get(k) != info[v] for k,v in [('name','pkgname'),('version','pkgver'),('arch','arch')]):
                raise ValueError('package metadata/hash differs')


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
    run(['docker','run','--rm','--platform','linux/amd64','--cap-drop=ALL','--security-opt=no-new-privileges','--mount',f'type=bind,src={packages},dst=/packages,readonly','--mount',f'type=bind,src={output},dst=/out',image,'repo-add','--include-sigs','/out/n3t-arch.db.tar.gz', *['/packages/'+p.name for p in sorted(packages.glob('*.pkg.tar.zst'))]], env=clean_env())
    for suffix in ('db','files'):
        path = output / f'n3t-arch.{suffix}.tar.gz'
        if path.is_symlink() or not path.is_file():
            raise ValueError('repo-add archive missing')
        alias = output / f'n3t-arch.{suffix}'
        alias.unlink(missing_ok=True)
        shutil.copyfile(path, alias)
    # Verify the actual database describes exactly the complete package bytes.
    found = {}
    with tarfile.open(output / 'n3t-arch.db', 'r:gz') as archive:
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
    release = github_api.api(f'repos/{REPOSITORY}/releases','POST',{'tag_name':snapshot,'target_commitish':head,'name':snapshot,'draft':True,'prerelease':False,'body':'Complete signed snapshot; retained releases are immutable.'})
    for name in sorted(names):
        github_api.upload_asset(REPOSITORY, release['id'], assets / name)
    remote = list(github_api.pages(f'repos/{REPOSITORY}/releases/{release["id"]}/assets'))
    if len(remote) != len(names) or {a['name'] for a in remote} != names:
        raise ValueError('partial/extra release upload')
    github_api.api(f'repos/{REPOSITORY}/releases/{release["id"]}','PATCH',{'draft':False})
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
        retrieve(entry, name, readback)
    for name in ('catalog.json','catalog.json.sig'):
        path = github_api.download(cloudflare.release_url(REPOSITORY,catalog['snapshot'],name), readback / name, maximum=32*1024*1024)
        if sha(path) != sha(assets / name):
            raise ValueError('public catalog readback differs')
    for name in catalog['files']:
        if name.endswith('.sig'):
            verify(readback / name[:-4], readback / name, ring)
    verify(readback / 'catalog.json', readback / 'catalog.json.sig', ring)


def publish(args):
    current_main(args.accepted_sha)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=False)
    ring = keyring(work)
    plan_path = Path(args.plan)
    if plan_path.is_symlink() or not plan_path.is_file() or plan_path.stat().st_size > 32*1024*1024:
        raise ValueError('invalid bounded publication plan')
    carried = json.loads(plan_path.read_text())
    if args.bootstrap:
        if carried['previous_url'] is not None:
            raise ValueError('bootstrap cannot reuse a prior snapshot')
    elif carried['previous_url'] != active_catalog_url():
        raise ValueError('previous signed catalog no longer active')
    previous = previous_catalog(carried['previous_url'], work, ring)
    plan = expectations(args.accepted_sha, args.run_id, args.run_attempt, previous)
    if carried['expected'] != plan:
        raise ValueError('build plan differs from accepted trusted inputs')
    unsigned = work / 'unsigned'
    safe_extract(Path(args.artifact), unsigned)
    validate_unsigned(unsigned, plan)
    packages = work / 'packages'; packages.mkdir()
    assets = work / 'assets'; assets.mkdir()
    snapshot = f'snapshot-{args.accepted_sha}-{args.run_id}-{args.run_attempt}'
    catalog = {'schema':1,'repository':REPOSITORY,'snapshot':snapshot,'accepted_sha':args.accepted_sha,'key_fingerprint':fingerprint(),'recipes':{},'files':{},'source_assets':dict(previous['source_assets']) if previous else {},'retained_packages':{},'retained_snapshots':dict(previous['retained_snapshots']) if previous else {}}
    if previous:
        catalog['retained_snapshots'][previous['snapshot']] = {'files':previous['files']}
        catalog['retained_packages'] = {**previous['retained_packages'], **{n:e for n,e in previous['files'].items() if '.pkg.tar.' in n}}
    new_names = set()
    for package in plan['packages']:
        name = package['pkgbase']
        lock = package['lock']
        catalog['recipes'][name] = {'tree_sha':package['tree_sha'],'input_digest':package['input_digest'],'version':lock['version'],'aur':package['aur'],'sources':lock['sources']}
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
        for source in lock['sources']:
            if source.get('bundle'):
                entry = source['bundle']
                asset_name = entry.get('filename') or entry['url'].rsplit('/',1)[-1]
                if asset_name != entry['sha256'] + '.bundle' or entry['url'] != cloudflare.release_url(REPOSITORY, 'source-' + entry['sha256'], asset_name):
                    raise ValueError('source bundle outside package repository')
                catalog['source_assets'][asset_name] = {'url':entry['url'],'sha256':entry['sha256']}
        if name == 'nasc-tui-bin':
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
                    catalog['source_assets'][asset] = {'url':cloudflare.release_url(REPOSITORY,snapshot,asset),'sha256':digest}
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
    for name in ('n3t-arch.db','n3t-arch.files'):
        signer.sign(assets / name)
    shutil.copyfile(ROOT / 'keys/n3t-arch.asc', assets / 'n3t-arch.asc')
    upload_names = set(new_names) | cloudflare.DATABASES
    for name in new_names:
        if name not in catalog['source_assets']:
            shutil.copyfile(packages / name, assets / name)
    for name in upload_names:
        if name not in catalog['source_assets']:
            catalog['files'][name] = {'url':cloudflare.release_url(REPOSITORY,snapshot,name),'sha256':sha(assets / name)}
    cloudflare.validate_catalog(catalog, REPOSITORY, fingerprint())
    (assets / 'catalog.json').write_bytes(github_api.canonical(catalog))
    signer.sign(assets / 'catalog.json')
    current_main(args.accepted_sha)
    expected_uploads = upload_names | {'catalog.json','catalog.json.sig'}
    release = upload_release(snapshot, args.accepted_sha, assets, expected_uploads)
    public_readback(catalog, assets, work / 'readback', ring)
    current_main(args.accepted_sha)
    result = {'snapshot':snapshot,'catalog_url':cloudflare.release_url(REPOSITORY,snapshot,'catalog.json'),'bootstrap':args.bootstrap}
    if not args.bootstrap:
        client = cloudflare.Cloudflare(os.environ['CLOUDFLARE_ACCOUNT_ID'], os.environ['CLOUDFLARE_API_TOKEN'])
        result['previous_version'] = client.current()
        result['activation'] = 'pending'
        (work / 'publication-result.json').write_bytes(github_api.canonical(result))
        github_api.api(f'repos/{REPOSITORY}/releases/{release["id"]}','PATCH',{'body':'Verified signed snapshot. Pre-activation rollback evidence: ' + json.dumps(result, sort_keys=True)})
        current_main(args.accepted_sha)
        result.update(client.deploy(cloudflare.generate_module(catalog, REPOSITORY, fingerprint())))
        result['activation'] = 'verified'
        with urllib.request.urlopen('https://arch.packages.n3t.work/healthz', timeout=90) as response:
            if json.load(response).get('snapshot') != snapshot:
                raise ValueError('active Worker health differs')
        github_api.api(f'repos/{REPOSITORY}/releases/{release["id"]}','PATCH',{'body':'Verified signed snapshot. Rollback evidence: ' + json.dumps(result, sort_keys=True)})
    (work / 'publication-result.json').write_bytes(github_api.canonical(result))
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
        p.add_argument('--bootstrap',action='store_true')
        if command == 'plan':
            p.add_argument('--output-dir',required=True)
            p.add_argument('--previous-url')
        else:
            p.add_argument('--plan',required=True)
            p.add_argument('--artifact',required=True)
            p.add_argument('--work-dir',required=True)
    args = parser.parse_args()
    if args.run_id < 1 or args.run_attempt < 1:
        raise ValueError('positive run identity required')
    if args.command == 'plan':
        print(json.dumps({'changed':prepare(args.accepted_sha,args.run_id,args.run_attempt,args.output_dir,args.previous_url,args.bootstrap)}))
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
