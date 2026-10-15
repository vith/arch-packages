import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import consumer_fixtures


class MicrosandboxFixtureSelection(unittest.TestCase):
    def setUp(self):
        self.metadata = json.loads((Path(__file__).resolve().parents[1] /
                                    'tools/consumer-fixtures.json').read_text())
        # Stock AUR has no runtime source; the fixture must not depend on one.
        self.package = {'pkgbase': 'microsandbox', 'source_lock': {
            'version': '0.7.8-2', 'sources': [{'id': 'microsandbox-0'}]}}

    def test_selects_upstream_version_and_platform_without_mutating_lock(self):
        before = copy.deepcopy(self.package)
        selected = consumer_fixtures.microsandbox_fixture(
            self.package, self.metadata, 'linux-x86_64')
        self.assertEqual(selected['asset_id'], 625593699)
        self.assertEqual(selected['sha256'],
                         '86f9f72dc3e639c7175bc07909b4b63ce412517c1ef8a2e1921171af5682fded')
        self.assertEqual(self.package, before)
        self.package['source_lock']['version'] = '0.7.8-3'
        self.assertEqual(consumer_fixtures.microsandbox_fixture(
            self.package, self.metadata, 'linux-x86_64'), selected)

    def test_unknown_version_or_platform_has_no_fallback(self):
        for version, platform in [('0.7.9-1', 'linux-x86_64'),
                                  ('0.7.8-2', 'linux-aarch64'),
                                  ('0.7.8-2', 'darwin-x86_64')]:
            with self.subTest(version=version, platform=platform):
                self.package['source_lock']['version'] = version
                with self.assertRaises(ValueError):
                    consumer_fixtures.microsandbox_fixture(self.package, self.metadata, platform)

    def test_missing_or_malformed_fixture_fails_explicitly(self):
        fixture = self.metadata['microsandbox']['0.7.8']['linux-x86_64']
        for field, invalid in [('sha256', 'not-a-digest'), ('asset_id', 0),
                               ('release_id', True), ('url', 'http://github.com/file'),
                               ('url', fixture['url'].replace('v0.7.8', 'v0.7.4')),
                               ('url', fixture['url'] + '?redirect=other')]:
            metadata = copy.deepcopy(self.metadata)
            metadata['microsandbox']['0.7.8']['linux-x86_64'][field] = invalid
            with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                consumer_fixtures.microsandbox_fixture(self.package, metadata, 'linux-x86_64')
        for metadata in [[], {}, {'schema': 1, 'microsandbox': {}},
                         {'schema': 1, 'microsandbox': []},
                         {'schema': 1, 'microsandbox': {'0.7.8': []}},
                         {'schema': True, 'microsandbox': self.metadata['microsandbox']},
                         {'schema': 2, 'microsandbox': self.metadata['microsandbox']}]:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                consumer_fixtures.microsandbox_fixture(self.package, metadata, 'linux-x86_64')


class RuntimeArchiveBoundary(unittest.TestCase):
    def setUp(self):
        self.session = tempfile.TemporaryDirectory()
        self.addCleanup(self.session.cleanup)
        self.directory = Path(self.session.name)
        self.archive = self.directory / 'runtime.tar.gz'
        self.home = self.directory / 'home'

    def archive_with(self, names, link=False):
        with tarfile.open(self.archive, 'w:gz') as tar:
            for name in names:
                member = tarfile.TarInfo(name)
                content = name.encode()
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/outside'
                else:
                    member.size = len(content)
                tar.addfile(member, io.BytesIO(content) if not link else None)
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_preparation_records_fixture_not_synthetic_package_sources(self):
        self.archive_with(['msb', 'libkrunfw.so.5.6.1'])
        data = self.archive.read_bytes()
        fixture = {'release_id': 408139101, 'asset_id': 625593699,
                   'url': 'https://github.com/superradcompany/microsandbox/releases/download/'
                          'v0.7.8/microsandbox-linux-x86_64.tar.gz',
                   'sha256': hashlib.sha256(data).hexdigest()}
        package = {'pkgbase': 'microsandbox', 'source_lock': {
            'version': '0.7.8-2', 'sources': [{'id': 'microsandbox-0'}]}}
        expected = self.directory / 'expected.json'
        expected.write_text(json.dumps({'packages': [package]}))
        metadata = self.directory / 'fixtures.json'
        metadata.write_text(json.dumps({'schema': 1, 'microsandbox': {
            '0.7.8': {'linux-x86_64': fixture}}}))
        evidence = self.directory / 'evidence.json'
        with patch.object(consumer_fixtures.platform, 'system', return_value='Linux'), \
                patch.object(consumer_fixtures.platform, 'machine', return_value='x86_64'), \
                patch.object(consumer_fixtures.urllib.request, 'urlopen',
                             return_value=io.BytesIO(data)):
            consumer_fixtures.prepare(expected, metadata, self.archive, self.home, evidence)
        proof = json.loads(evidence.read_text())
        self.assertEqual(proof['fixture'], fixture)
        self.assertEqual(proof['platform'], 'linux-x86_64')
        self.assertEqual(proof['package_version'], '0.7.8-2')
        self.assertEqual((self.home / 'bin/msb').read_bytes(), b'msb')
        self.assertEqual(json.loads(expected.read_text())['packages'], [package])

    def test_unenrolled_package_needs_no_fixture_or_download(self):
        expected = self.directory / 'expected.json'
        expected.write_text('{"packages": []}')
        consumer_fixtures.prepare(expected, self.directory / 'missing.json', self.archive,
                                  self.home, self.directory / 'evidence.json')
        self.assertEqual(list(self.directory.iterdir()), [expected])

    def test_missing_fixture_file_fails_before_preparation(self):
        expected = self.directory / 'expected.json'
        expected.write_text('{"packages": [{"pkgbase": "microsandbox"}]}')
        with self.assertRaises(FileNotFoundError):
            consumer_fixtures.prepare(expected, self.directory / 'missing.json', self.archive,
                                      self.home, self.directory / 'evidence.json')
        self.assertFalse(self.home.exists())
        self.assertFalse(self.archive.exists())

    def test_exact_pair_extracts_into_separate_home(self):
        digest = self.archive_with(['msb', 'libkrunfw.so.5.6.1'])
        consumer_fixtures.unpack_runtime(self.archive, digest, self.home)
        self.assertEqual((self.home / 'bin/msb').read_bytes(), b'msb')
        self.assertEqual((self.home / 'lib/libkrunfw.so.5.6.1').read_bytes(), b'libkrunfw.so.5.6.1')
        self.assertEqual((self.home / 'bin/msb').stat().st_mode & 0o777, 0o755)

    def test_digest_mismatch_writes_nothing(self):
        self.archive_with(['msb', 'libkrunfw.so.5.6.1'])
        with self.assertRaises(ValueError):
            consumer_fixtures.unpack_runtime(self.archive, '0' * 64, self.home)
        self.assertFalse(self.home.exists())

    def test_unexpected_missing_duplicate_or_link_members_write_nothing(self):
        for names, link in [(['msb'], False),
                            (['msb', 'libkrunfw.so.5.6.1', '../escape'], False),
                            (['msb', 'msb', 'libkrunfw.so.5.6.1'], False),
                            (['msb', 'libkrunfw.so.5.6.1'], True)]:
            with self.subTest(names=names, link=link):
                digest = self.archive_with(names, link)
                with self.assertRaises(ValueError):
                    consumer_fixtures.unpack_runtime(self.archive, digest, self.home)
                self.assertFalse(self.home.exists())
