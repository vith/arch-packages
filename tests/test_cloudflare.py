import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.cloudflare import Cloudflare, DATABASES, canonical, generate_module, release_url, validate_catalog, verify_signed_catalog

REPOSITORY = 'vith/arch-packages'
FINGERPRINT = 'A' * 40


def catalog():
    snapshot = 'snapshot-' + 'a' * 40 + '-1-1'
    def entry(name):
        return {'url': release_url(REPOSITORY, snapshot, name), 'sha256': 'b' * 64}
    names = DATABASES | {'pkg-1-1-x86_64.pkg.tar.zst', 'pkg-1-1-x86_64.pkg.tar.zst.sig'}
    return {'schema': 1, 'repository': REPOSITORY, 'snapshot': snapshot,
            'accepted_sha': 'a' * 40, 'key_fingerprint': FINGERPRINT,
            'recipes': {'pkg': {'version': '1-1'}},
            'files': {name: entry(name) for name in names}, 'source_assets': {},
            'retained_packages': {}, 'retained_snapshots': {}}


class CatalogBoundary(unittest.TestCase):
    def test_untrusted_destinations_and_incomplete_snapshots_rejected(self):
        for destination in ['http://github.com/vith/arch-packages/releases/download/snapshot-a/n3t-arch.db',
                            'https://evil.example/n3t-arch.db',
                            'https://github.com/vith/unrelated/releases/download/snapshot-a/n3t-arch.db',
                            'https://github.com/vith/arch-packages/releases/download/snapshot-a/n3t-arch.db?token=secret',
                            'https://github.com/vith/arch-packages/releases/download/snapshot-a/%252e%252e']:
            value = catalog()
            value['files']['n3t-arch.db']['url'] = destination
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                validate_catalog(value, REPOSITORY, FINGERPRINT)
        for missing in ['n3t-arch.db.sig', 'pkg-1-1-x86_64.pkg.tar.zst.sig']:
            value = catalog()
            del value['files'][missing]
            with self.assertRaises(ValueError):
                validate_catalog(value, REPOSITORY, FINGERPRINT)
        value = catalog()
        value['key_fingerprint'] = 'B' * 40
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)

    def test_source_and_old_snapshot_destinations_are_scoped(self):
        value = catalog()
        value['source_assets']['source.tar.gz'] = {'url': 'https://upstream.example/source.tar.gz', 'sha256': 'a' * 64}
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)
        value = catalog()
        value['retained_snapshots']['snapshot-old'] = {'files': copy.deepcopy(value['files'])}
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)


class SourceBundleBoundary(unittest.TestCase):
    def test_content_addressed_bundle_is_allowed_only_at_its_exact_source_route(self):
        digest = 'c' * 64
        name = digest + '.bundle'
        entry = {'url': release_url(REPOSITORY, 'source-' + digest, name), 'sha256': digest}
        value = catalog()
        value['source_assets'][name] = entry
        self.assertEqual(validate_catalog(value, REPOSITORY, FINGERPRINT)['source_assets'][name], entry)
        value['source_assets'][name] = {**entry, 'sha256': 'd' * 64}
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)
        value['source_assets'] = {'other.bundle': entry}
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)
        value['source_assets'] = {}
        value['retained_packages'][name] = entry
        with self.assertRaises(ValueError):
            validate_catalog(value, REPOSITORY, FINGERPRINT)


class SignedReadbackBoundary(unittest.TestCase):
    def test_armored_key_is_dearmored_and_tampered_asset_or_catalog_fails(self):
        root = Path.home() / '.local/state/omp/work/arch-packages-test'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            home = Path(directory)
            home.chmod(0o700)
            command = ['gpg', '--no-options', '--homedir', str(home), '--batch', '--pinentry-mode', 'loopback', '--passphrase', '']
            try:
                subprocess.run(command + ['--quick-generate-key', 'isolated fixture <fixture@example.invalid>', 'ed25519', 'sign', '1d'], check=True, capture_output=True)
                listing = subprocess.run(command + ['--with-colons', '--list-keys'], check=True, capture_output=True).stdout.decode()
                fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
                key = home / 'public.asc'
                key.write_bytes(subprocess.run(command + ['--armor', '--export', fingerprint], check=True, capture_output=True).stdout)
                def sign(data):
                    return subprocess.run(command + ['--detach-sign', '--output', '-'], input=data, check=True, capture_output=True).stdout
                value = catalog()
                value['key_fingerprint'] = fingerprint
                assets = {}
                for name, entry in value['files'].items():
                    if not name.endswith('.sig'):
                        assets[entry['url']] = key.read_bytes() if name.endswith('.asc') else ('real fixture bytes: ' + name).encode()
                for name, entry in value['files'].items():
                    if name.endswith('.sig'):
                        assets[entry['url']] = sign(assets[entry['url'][:-4]])
                for entry in value['files'].values():
                    entry['sha256'] = hashlib.sha256(assets[entry['url']]).hexdigest()
                catalog_url = release_url(REPOSITORY, value['snapshot'], 'catalog.json')
                assets[catalog_url] = canonical(value).encode()
                assets[catalog_url + '.sig'] = sign(assets[catalog_url])
                with patch('tools.cloudflare.download', side_effect=lambda url, maximum=0: assets[url]):
                    verified = verify_signed_catalog(catalog_url, REPOSITORY, fingerprint, key, home / 'verify')
                    self.assertEqual(verified['snapshot'], value['snapshot'])
                    database_url = value['files']['n3t-arch.db']['url']
                    assets[database_url] += b'tampered'
                    with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                        verify_signed_catalog(catalog_url, REPOSITORY, fingerprint, key, home / 'verify')
                    assets[database_url] = assets[database_url][:-len(b'tampered')]
                    signature_url = database_url + '.sig'
                    assets[signature_url] = sign(b'wrong database bytes')
                    value['files']['n3t-arch.db.sig']['sha256'] = hashlib.sha256(assets[signature_url]).hexdigest()
                    assets[catalog_url] = canonical(value).encode()
                    assets[catalog_url + '.sig'] = sign(assets[catalog_url])
                    with self.assertRaises(subprocess.CalledProcessError):
                        verify_signed_catalog(catalog_url, REPOSITORY, fingerprint, key, home / 'verify')
                    assets[catalog_url] += b'tampered'
                    with self.assertRaises(subprocess.CalledProcessError):
                        verify_signed_catalog(catalog_url, REPOSITORY, fingerprint, key, home / 'verify')
            finally:
                subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'gpg-agent'], check=False, capture_output=True)


class APIBoundary(unittest.TestCase):
    def setUp(self):
        self.active = 'old-version'
        self.fail_upload = False
        self.fail_deploy = False
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                owner.requests.append(('GET', self.path))
                self.reply({'deployments': [{'versions': [{'version_id': owner.active, 'percentage': 100}]}]})
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                owner.requests.append(('POST', self.path))
                if self.path.endswith('/versions'):
                    if owner.fail_upload:
                        self.reply(None, 500)
                    else:
                        if b'filename="redirect.mjs"' not in data or b'"main_module":"redirect.mjs"' not in data:
                            self.reply(None, 400)
                        else:
                            self.reply({'id': 'new-version'})
                else:
                    if owner.fail_deploy:
                        self.reply(None, 500)
                    else:
                        body = json.loads(data)
                        if body['strategy'] != 'percentage' or len(body['versions']) != 1 or body['versions'][0]['percentage'] != 100:
                            self.reply(None, 400)
                        else:
                            owner.active = body['versions'][0]['version_id']
                            self.reply({'id': 'deployment'})
            def reply(self, result, status=200):
                data = json.dumps({'success': status == 200, 'result': result}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Cloudflare('a' * 32, 'fixture-token', 'http://127.0.0.1:' + str(self.server.server_port))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_complete_version_activation_and_actual_rollback(self):
        module = generate_module(catalog(), REPOSITORY, FINGERPRINT)
        self.assertEqual(self.client.deploy(module), {'previous_version': 'old-version', 'version': 'new-version'})
        self.assertEqual(self.active, 'new-version')
        self.client.activate('old-version')
        self.assertEqual(self.client.current(), 'old-version')

    def test_failed_upload_or_activation_leaves_previous_active(self):
        self.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.client.deploy('complete-module')
        self.assertEqual(self.active, 'old-version')
        self.assertFalse(any(path.endswith('/deployments') and method == 'POST' for method, path in self.requests))
        self.fail_upload = False
        self.fail_deploy = True
        with self.assertRaises(RuntimeError):
            self.client.deploy('complete-module')
        self.assertEqual(self.active, 'old-version')

    def test_lost_activation_response_reconciles_success_without_rollback(self):
        original = self.client.request
        def lost(path, method='GET', data=None, content_type='application/json'):
            result = original(path, method, data, content_type)
            if method == 'POST' and path.endswith('/deployments'):
                raise TimeoutError('response lost after server applied activation')
            return result
        with patch.object(self.client, 'request', side_effect=lost):
            self.assertEqual(self.client.deploy('complete-module')['version'], 'new-version')
        self.assertEqual(self.active, 'new-version')
        self.assertEqual(sum(method == 'POST' and path.endswith('/deployments') for method, path in self.requests), 1)

    def test_unknown_activation_outcome_never_blindly_rolls_back(self):
        original = self.client.request
        def unreachable(path, method='GET', data=None, content_type='application/json'):
            if method == 'POST' and path.endswith('/deployments'):
                original(path, method, data, content_type)
                raise TimeoutError('lost')
            if method == 'GET' and self.active == 'new-version':
                raise TimeoutError('readback unavailable')
            return original(path, method, data, content_type)
        with patch.object(self.client, 'request', side_effect=unreachable), self.assertRaises(TimeoutError):
            self.client.deploy('complete-module')
        self.assertEqual(self.active, 'new-version')
        self.assertEqual(sum(method == 'POST' and path.endswith('/deployments') for method, path in self.requests), 1)


if __name__ == '__main__':
    unittest.main()
