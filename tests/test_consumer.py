import base64
import binascii
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import consumer


class SignedConsumerInputs(unittest.TestCase):
    def setUp(self):
        self.session = tempfile.TemporaryDirectory()
        self.addCleanup(self.session.cleanup)
        self.directory = Path(self.session.name)
        self.filename = 'example-1-1-any.pkg.tar.zst'
        (self.directory / self.filename).write_bytes(b'package')
        self.catalog = {'recipes': {'example': {'version': '1-1', 'sources': []}},
                        'files': {self.filename: {'sha256': hashlib.sha256(b'package').hexdigest()},
                                  'vith-gh.db': {'sha256': 'a' * 64}}}
        self.policies = [{'pkgbase': 'example', 'outputs': [{'name': 'example', 'arch': 'any'}]}]
        self.identity = {'pkgname': 'example', 'pkgver': '1-1', 'arch': 'any'}

    def test_expectations_come_from_verified_snapshot_not_run_artifacts(self):
        with patch.object(consumer.publish, 'pkginfo', return_value=self.identity):
            result = consumer.snapshot_inputs(self.catalog, self.directory, self.policies)
        package = result['packages'][0]
        self.assertEqual(package['source_lock'], {'schema': 1, 'version': '1-1', 'sources': []})
        self.assertEqual(package['files'][0]['name'], 'example')

    def test_consumer_selects_the_repository_from_the_signed_snapshot(self):
        with patch.object(consumer.publish, 'pkginfo', return_value=self.identity):
            current = consumer.snapshot_inputs(self.catalog, self.directory, self.policies)
            self.catalog['files']['arch-packages.db'] = self.catalog['files'].pop('vith-gh.db')
            historical = consumer.snapshot_inputs(self.catalog, self.directory, self.policies)
        self.assertEqual(current['pacman_repository'], 'vith-gh')
        self.assertEqual(historical['pacman_repository'], 'arch-packages')

    def test_prepare_uses_enrollment_at_signed_commit_before_package_rename(self):
        accepted_sha = 'a' * 40
        snapshot = f"snapshot-{'b' * 40}-1-1"
        self.catalog['accepted_sha'] = accepted_sha
        (self.directory / 'packages.json').write_text(json.dumps({'packages': [
            {'pkgbase': 'renamed', 'outputs': [{'name': 'renamed', 'arch': 'any'}]},
        ]}))
        content = base64.encodebytes(json.dumps({'packages': self.policies}).encode()).decode()

        def api(path, authenticated=False):
            if path == f'repos/vith/arch-packages/releases/tags/{snapshot}':
                return {'id': 1, 'tag_name': snapshot, 'draft': False, 'prerelease': False}
            if path == f'repos/vith/arch-packages/contents/packages.json?ref={accepted_sha}':
                return {'encoding': 'base64', 'content': content}
            raise AssertionError(f'unexpected GitHub endpoint: {path}')

        def verified_snapshot(release, directory, ring):
            complete = directory / 'complete'
            complete.mkdir(parents=True)
            (complete / self.filename).write_bytes(b'package')
            return self.catalog

        work = self.directory / 'consumer'
        with patch.object(consumer.publish, 'ROOT', self.directory), \
                patch.object(consumer.publish, 'keyring', return_value=object()), \
                patch.object(consumer.publish, 'verified_snapshot', side_effect=verified_snapshot), \
                patch.object(consumer.publish, 'pkginfo', return_value=self.identity), \
                patch.object(consumer.github_api, 'api', side_effect=api):
            consumer.prepare(snapshot, work)

        result = json.loads((work / 'expected.json').read_text())
        self.assertEqual(result['pacman_repository'], 'vith-gh')
        self.assertEqual(result['packages'], [{
            'pkgbase': 'example',
            'source_lock': {'schema': 1, 'version': '1-1', 'sources': []},
            'files': [{'name': 'example', 'version': '1-1', 'arch': 'any', 'filename': self.filename}],
        }])

    def test_prepare_rejects_invalid_base64_enrollment(self):
        snapshot = f"snapshot-{'a' * 40}-1-1"
        self.catalog['accepted_sha'] = 'a' * 40
        content = base64.b64encode(json.dumps({'packages': self.policies}).encode()).decode()
        work = self.directory / 'consumer'
        with patch.object(consumer.publish, 'keyring', return_value=object()), \
                patch.object(consumer.publish, 'verified_snapshot', return_value=self.catalog), \
                patch.object(consumer.github_api, 'api', side_effect=[
                    {'id': 1, 'tag_name': snapshot, 'draft': False, 'prerelease': False},
                    {'encoding': 'base64', 'content': content[:4] + '%' + content[4:]},
                ]), self.assertRaises(binascii.Error):
            consumer.prepare(snapshot, work)
        self.assertFalse((work / 'expected.json').exists())

    def test_corrupt_package_is_rejected(self):
        (self.directory / self.filename).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            consumer.snapshot_inputs(self.catalog, self.directory, self.policies)

    def test_wrong_package_identity_is_rejected(self):
        with patch.object(consumer.publish, 'pkginfo', return_value={**self.identity, 'pkgver': '2-1'}), self.assertRaisesRegex(ValueError, 'identity mismatch'):
            consumer.snapshot_inputs(self.catalog, self.directory, self.policies)

    def test_missing_enrolled_package_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'package set'):
            consumer.snapshot_inputs(self.catalog, self.directory, [])
