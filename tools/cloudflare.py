"""Signed-catalog Worker publishing. CI never needs OpenTofu state.

CLI: python3 -m tools.cloudflare {generate,deploy,bootstrap,rollback,current}
Deployment requires CLOUDFLARE_ACCOUNT_ID, scoped CLOUDFLARE_API_TOKEN,
--repository, --fingerprint, --public-key and --work-dir. Bootstrap instead
uses CLOUDFLARE_BOOTSTRAP_TOKEN, TF_VAR_account_id, TF_VAR_zone_id and
TF_VAR_state_passphrase (recovery secret), and native tofu >= 1.9.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKER = 'n3t-arch-packages'
COMPATIBILITY_DATE = '2026-10-02'
API = 'https://api.cloudflare.com/client/v4'
DATABASES = {'n3t-arch.db', 'n3t-arch.db.sig', 'n3t-arch.files', 'n3t-arch.files.sig', 'n3t-arch.asc'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]*\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n'


def release_url(repository, snapshot, filename):
    return f'https://github.com/{repository}/releases/download/{urllib.parse.quote(snapshot, safe="")}/{urllib.parse.quote(filename, safe="")}'


def validate_catalog(catalog, repository, fingerprint):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    if catalog.get('schema') != 1 or catalog.get('repository') != repository:
        raise ValueError('Wrong catalog schema/repository')
    if not re.fullmatch(r'[0-9A-F]{40,64}', fingerprint) or catalog.get('key_fingerprint') != fingerprint:
        raise ValueError('Wrong signing fingerprint')
    if not re.fullmatch(r'[0-9a-f]{40}', catalog.get('accepted_sha', '')):
        raise ValueError('Invalid accepted SHA')
    snapshot = catalog.get('snapshot', '')
    if not NAME.fullmatch(snapshot) or not snapshot.startswith('snapshot-'):
        raise ValueError('Invalid snapshot')
    if not isinstance(catalog.get('recipes'), dict) or not catalog['recipes']:
        raise ValueError('Incomplete recipe manifest')
    for key in ('files', 'source_assets', 'retained_packages', 'retained_snapshots'):
        if not isinstance(catalog.get(key), dict):
            raise ValueError('Missing catalog map: ' + key)
    if not DATABASES <= catalog['files'].keys():
        raise ValueError('Incomplete database/key assets')
    if {'catalog.json', 'catalog.json.sig'} & catalog['files'].keys():
        raise ValueError('Self-referential catalog')
    def files(entries, expected_snapshot=None, allow_source_bundle=False):
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
            source_bundle = (allow_source_bundle and re.fullmatch(r'source-[0-9a-f]{64}', tag)
                             and name == tag[7:] + '.bundle' and entry['sha256'] == tag[7:])
            if not NAME.fullmatch(tag) or not (tag.startswith('snapshot-') or source_bundle) or (expected_snapshot and tag != expected_snapshot):
                raise ValueError('Wrong asset snapshot')
            if url != release_url(repository, tag, name):
                raise ValueError('Noncanonical asset destination')
    files(catalog['files'])
    files({name: catalog['files'][name] for name in DATABASES}, snapshot)
    files(catalog['source_assets'], allow_source_bundle=True)
    files(catalog['retained_packages'])
    for old, entry in catalog['retained_snapshots'].items():
        if not NAME.fullmatch(old) or not old.startswith('snapshot-') or not isinstance(entry, dict):
            raise ValueError('Invalid retained snapshot')
        files(entry['files'])
        files({name: value for name, value in entry['files'].items() if name in DATABASES}, old)
    for name in catalog['files'].keys() & catalog['retained_packages'].keys():
        if catalog['files'][name] != catalog['retained_packages'][name]:
            raise ValueError('Retained filename was retargeted')
    all_packages = {**catalog['retained_packages'], **catalog['files']}
    if not any(name.endswith('.pkg.tar.zst') for name in catalog['files']):
        raise ValueError('Snapshot contains no packages')
    for name in all_packages:
        if name not in DATABASES and not re.fullmatch(r'.+\.pkg\.tar\.(?:zst|xz|gz)(?:\.sig)?', name):
            raise ValueError('Non-package repo asset')
        if name.endswith(('.zst', '.xz', '.gz')) and name + '.sig' not in all_packages:
            raise ValueError('Missing package signature')
    return catalog


def generate_module(catalog, repository, fingerprint):
    validate_catalog(catalog, repository, fingerprint)
    # Parsing a serialized JSON string also prevents __proto__ object-literal semantics.
    binding = 'const CATALOG = JSON.parse(' + json.dumps(canonical(catalog), ensure_ascii=True) + ');\n'
    module = binding + (ROOT / 'worker/redirect.mjs').read_text()
    if len(module.encode()) > 3 * 1024 * 1024:
        raise ValueError('Catalog exceeds Workers Free module size limit; do not upgrade automatically')
    return module


def download(url, maximum=32 * 1024 * 1024):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('HTTPS required')
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'n3t-arch-publisher'}), timeout=90) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Download too large')
    return data


def verify_signed_catalog(catalog_url, repository, fingerprint, public_key, work_dir):
    work = Path(work_dir).resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = work / ('verify-' + uuid.uuid4().hex)
    session.mkdir(mode=0o700)
    try:
        keyring = session / 'keyring.gpg'
        subprocess.run(['gpg', '--no-options', '--homedir', str(session), '--batch', '--yes', '--dearmor', '--output', str(keyring), str(public_key)], check=True, capture_output=True)
        catalog_bytes = download(catalog_url)
        signature = download(catalog_url + '.sig', 1024 * 1024)
        def verify(data, sig):
            (session / 'data').write_bytes(data)
            (session / 'signature').write_bytes(sig)
            result = subprocess.run(['gpgv', '--homedir', str(session), '--status-fd', '1', '--keyring', str(keyring), str(session / 'signature'), str(session / 'data')], check=True, capture_output=True)
            valid = [line.split() for line in result.stdout.decode().splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
            if len(valid) != 1 or fingerprint not in (valid[0][2], valid[0][-1]):
                raise ValueError('Unexpected signature fingerprint')
        verify(catalog_bytes, signature)
        catalog = validate_catalog(json.loads(catalog_bytes), repository, fingerprint)
        if catalog_url != release_url(repository, catalog['snapshot'], 'catalog.json'):
            raise ValueError('Wrong catalog URL')
        # Complete public readback before any Cloudflare write; verify hashes and signed pairs.
        entries = {**catalog['retained_packages'], **catalog['source_assets'], **catalog['files']}
        for snapshot in catalog['retained_snapshots'].values():
            for name, entry in snapshot['files'].items():
                entries.setdefault(entry['url'], entry)
        by_url = {}
        for entry in entries.values():
            if entry['url'] in by_url and entry != by_url[entry['url']]:
                raise ValueError('Conflicting hashes for an immutable public URL')
            by_url[entry['url']] = entry
        for url, entry in by_url.items():
            data = download(url, 768 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Public asset hash mismatch')
            if url + '.sig' in by_url:
                sig = download(url + '.sig', 1024 * 1024)
                if hashlib.sha256(sig).hexdigest() != by_url[url + '.sig']['sha256']:
                    raise ValueError('Signature hash mismatch')
                verify(data, sig)
        return catalog
    finally:
        for path in session.iterdir():
            path.unlink()
        session.rmdir()


class NoAPIRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError('Refusing Cloudflare API redirect')


class Cloudflare:
    def __init__(self, account, token, base=API):
        if not re.fullmatch(r'[0-9a-f]{32}', account) or not token:
            raise ValueError('Cloudflare account/token required')
        self.token = token
        self.base = base
        self.path = f'/accounts/{account}/workers/scripts/{WORKER}'
        self.account = account

    def request(self, path, method='GET', data=None, content_type='application/json'):
        body = canonical(data).encode() if isinstance(data, (dict, list)) else data
        request = urllib.request.Request(self.base + path, data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': content_type})
        try:
            with urllib.request.build_opener(NoAPIRedirect).open(request, timeout=90) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Cloudflare HTTP {error.code}') from None
        if not result.get('success'):
            raise RuntimeError('Cloudflare API rejected request')
        return result['result']

    def current(self):
        result = self.request(self.path + '/deployments')
        deployments = result if isinstance(result, list) else result['deployments']
        if not deployments:
            return None
        versions = deployments[0]['versions']
        if len(versions) != 1 or versions[0]['percentage'] != 100:
            raise ValueError('Refusing mixed deployment')
        return versions[0]['version_id']

    def upload(self, module):
        boundary = 'n3t-' + uuid.uuid4().hex
        metadata = canonical({'main_module': 'redirect.mjs', 'compatibility_date': COMPATIBILITY_DATE, 'bindings': []})
        parts = []
        for name, filename, mime, content in [('metadata', None, 'application/json', metadata), ('redirect.mjs', 'redirect.mjs', 'application/javascript+module', module)]:
            disposition = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
            parts.append(f'--{boundary}\r\n{disposition}\r\nContent-Type: {mime}\r\n\r\n{content}\r\n')
        body = (''.join(parts) + f'--{boundary}--\r\n').encode()
        return self.request(self.path + '/versions', 'POST', body, 'multipart/form-data; boundary=' + boundary)['id']

    def activate(self, version):
        if not re.fullmatch(r'[A-Za-z0-9-]+', version):
            raise ValueError('Invalid version ID')
        try:
            self.request(self.path + '/deployments', 'POST', {'strategy': 'percentage', 'versions': [{'version_id': version, 'percentage': 100}]})
        except (TimeoutError, socket.timeout, urllib.error.URLError, RuntimeError):
            # A lost response is not evidence of failure. Never blindly undo a successful switch.
            if self.current() == version:
                return version
            raise
        if self.current() != version:
            raise RuntimeError('Deployment readback differs from requested version')
        return version

    def deploy(self, module):
        previous = self.current()
        version = self.upload(module)
        try:
            self.activate(version)
        except Exception:
            current = self.current()  # Failure to read is ambiguous: do not mutate.
            if previous and current != previous and current != version:
                self.activate(previous)
            raise
        return {'previous_version': previous, 'version': version}

    def check_domain(self, zone):
        hostname = 'arch.packages.n3t.work'
        records = self.request(f'/zones/{zone}/dns_records?name={hostname}')
        domains = self.request(f'/accounts/{self.account}/workers/domains')
        matching = [entry for entry in domains if entry['hostname'] == hostname]
        owned = len(matching) == 1 and matching[0]['service'] == WORKER and matching[0]['zone_id'] == zone
        if (matching and not owned) or (records and not owned):
            raise ValueError('arch.packages.n3t.work is occupied by an unrelated resource')


def bootstrap(client, module):
    zone = os.environ['TF_VAR_zone_id']
    if os.environ.get('TF_VAR_account_id') != client.account:
        raise ValueError('OpenTofu account differs from publishing account')
    client.check_domain(zone)
    env = dict(os.environ, CLOUDFLARE_API_TOKEN=client.token)
    # Identity is declarative; neither this client nor Terraform owns active code.
    subprocess.run(['tofu', '-chdir=' + str(ROOT / 'opentofu'), 'init', '-input=false'], env=env, check=True)
    subprocess.run(['tofu', '-chdir=' + str(ROOT / 'opentofu'), 'apply', '-input=false', '-auto-approve', '-var=attach_domain=false'], env=env, check=True)
    result = client.deploy(module)
    client.check_domain(zone)
    subprocess.run(['tofu', '-chdir=' + str(ROOT / 'opentofu'), 'apply', '-input=false', '-auto-approve', '-var=attach_domain=true', '-var=verified_version_id=' + result['version']], env=env, check=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['generate', 'deploy', 'bootstrap', 'rollback', 'current'])
    parser.add_argument('--catalog-url')
    parser.add_argument('--repository', default='vith/arch-packages')
    parser.add_argument('--fingerprint')
    parser.add_argument('--public-key', default=str(ROOT / 'keys/n3t-arch.asc'))
    parser.add_argument('--work-dir')
    parser.add_argument('--output')
    parser.add_argument('--version')
    args = parser.parse_args()
    if args.command in ('generate', 'deploy', 'bootstrap'):
        if not args.catalog_url or not args.fingerprint or not args.work_dir:
            parser.error('--catalog-url, --fingerprint and --work-dir required')
        catalog = verify_signed_catalog(args.catalog_url, args.repository, args.fingerprint, args.public_key, args.work_dir)
        module = generate_module(catalog, args.repository, args.fingerprint)
        if args.command == 'generate':
            if not args.output:
                parser.error('--output required')
            Path(args.output).write_text(module)
            return
    token_name = 'CLOUDFLARE_BOOTSTRAP_TOKEN' if args.command == 'bootstrap' else 'CLOUDFLARE_API_TOKEN'
    client = Cloudflare(os.environ['CLOUDFLARE_ACCOUNT_ID'], os.environ[token_name])
    if args.command == 'current':
        result = {'version': client.current()}
    elif args.command == 'rollback':
        if not args.version:
            parser.error('--version required')
        result = {'version': client.activate(args.version)}
    elif args.command == 'bootstrap':
        result = bootstrap(client, module)
    else:
        result = client.deploy(module)
    print(canonical(result), end='')


if __name__ == '__main__':
    main()
