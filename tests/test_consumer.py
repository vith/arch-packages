import hashlib
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
                        'files': {self.filename: {'sha256': hashlib.sha256(b'package').hexdigest()}}}
        self.policies = [{'pkgbase': 'example', 'outputs': [{'name': 'example', 'arch': 'any'}]}]
        self.identity = {'pkgname': 'example', 'pkgver': '1-1', 'arch': 'any'}

    def test_expectations_come_from_verified_snapshot_not_run_artifacts(self):
        with patch.object(consumer.publish, 'pkginfo', return_value=self.identity):
            result = consumer.snapshot_inputs(self.catalog, self.directory, self.policies)
        package = result['packages'][0]
        self.assertEqual(package['source_lock'], {'schema': 1, 'version': '1-1', 'sources': []})
        self.assertEqual(package['files'][0]['name'], 'example')

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
