from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import dependency_repo


class DependencyTrust(unittest.TestCase):
    def test_absolute_and_relative_release_redirects_pin_same_immutable_server(self):
        path = '/vith/arch-packages/releases/tag/snapshot-20261009'
        expected = 'https://github.com/vith/arch-packages/releases/download/snapshot-20261009'
        for location in (path, 'https://github.com' + path, 'tag/snapshot-20261009'):
            with self.subTest(location=location):
                self.assertEqual(dependency_repo.redirect_server(location), expected)

    def test_release_redirect_cannot_escape_public_repository_boundary(self):
        valid = 'https://github.com/vith/arch-packages/releases/tag/snapshot-20261009'
        locations = (None, '', valid + '?query', valid + '#fragment',
                     valid + '?', valid + '#', valid + '\n',
                     valid.replace('https:', 'http:'),
                     valid.replace('github.com', 'evil.example'),
                     valid.replace('github.com', 'github.com:443'),
                     valid.replace('github.com', 'user@github.com'),
                     valid.replace('/vith/', '/other/'),
                     valid.replace('/tag/', '/download/'),
                     valid + '/extra', valid.replace('snapshot-20261009', '%2Fother'),
                     valid.replace('snapshot-20261009', '..'))
        for location in locations:
            with self.subTest(location=location), self.assertRaises(ValueError):
                dependency_repo.redirect_server(location)

    def test_signing_subkey_cannot_impersonate_primary_and_extra_primary_is_rejected(self):
        trusted = f'fpr:::::::::{dependency_repo.FINGERPRINT}:\n'
        wrong = 'fpr:::::::::' + 'A' * 40 + ':\n'
        dependency_repo.verify_primary_key('pub:\n' + trusted + 'sub:\n' + wrong)
        for listing in ('pub:\n' + wrong + 'sub:\n' + trusted,
                        'pub:\n' + trusted + 'pub:\n' + wrong,
                        'pub:\nsub:\n' + trusted, 'pub:\n', 'pub:\nfpr:\n', 'pub:\n' + wrong):
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                dependency_repo.verify_primary_key(listing)

    def test_repository_keys_cannot_cross_authority_boundaries(self):
        gh = f'pub:\nfpr:::::::::{dependency_repo.FINGERPRINT}:\n'
        n3t = f'pub:\nfpr:::::::::{dependency_repo.N3T_FINGERPRINT}:\n'
        dependency_repo.verify_primary_key(n3t, dependency_repo.N3T_FINGERPRINT)
        for listing, expected in ((gh, dependency_repo.N3T_FINGERPRINT),
                                  (n3t, dependency_repo.FINGERPRINT)):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                dependency_repo.verify_primary_key(listing, expected)


class DependencyResolution(unittest.TestCase):
    def setUp(self):
        self.session = tempfile.TemporaryDirectory()
        self.addCleanup(self.session.cleanup)
        self.root = Path(self.session.name)
        self.config = self.root / 'pacman.conf'
        self.config.write_text('[core]\n[extra]\n')
        self.packages = {'core': set(), 'extra': set(), 'vith-gh': set(), 'vith-arch': set()}
        self.providers = {}
        self.query_failure = None
        append = dependency_repo._append_repository

        def append_private(*args):
            return append(*args, config=self.config, gpgdir=self.root / 'gnupg')

        for patcher in (
            patch.object(dependency_repo, '_append_repository', side_effect=append_private),
            patch.object(dependency_repo, 'latest_server', return_value=
                         'https://github.com/vith/arch-packages/releases/download/snapshot-test'),
            patch('subprocess.run', side_effect=self.query),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def repositories(self):
        return [line[1:-1] for line in self.config.read_text().splitlines()
                if line.startswith('[')]

    def execute(self, command, **kwargs):
        if '--show-keys' in command:
            fingerprint = (dependency_repo.N3T_FINGERPRINT if command[-1].endswith('/n3t.asc')
                           else dependency_repo.FINGERPRINT)
            return f'pub:\nfpr:::::::::{fingerprint}:\n'
        return ''

    def query(self, command, **kwargs):
        if self.query_failure is not None:
            return subprocess.CompletedProcess(command, 1, '', self.query_failure)
        available = set().union(*(self.packages[name] for name in self.repositories()))
        targets = command[command.index('--') + 1:]
        missing = [name for name in targets
                   if name not in available and self.providers.get(name) not in available]
        stderr = ''.join(f'error: target not found: {name}\n' for name in missing)
        return subprocess.CompletedProcess(command, bool(missing), '', stderr)

    def test_official_provider_satisfies_dependency_without_fallback_repositories(self):
        self.packages['extra'] = {'cargo', 'mesa'}
        self.providers['libgl'] = 'mesa'
        dependency_repo.configure(self.root, self.root, self.execute, {'cargo', 'libgl'})
        self.assertEqual(self.repositories(), ['core', 'extra'])

    def test_signed_github_satisfies_missing_dependency_without_external_fallback(self):
        self.packages['vith-gh'] = {'carapace'}
        dependency_repo.configure(self.root, self.root, self.execute, {'carapace'})
        self.assertEqual(self.repositories(), ['core', 'extra', 'vith-gh'])
        self.assertIn('SigLevel = Required', self.config.read_text())

    def test_external_fallback_is_used_only_after_github_cannot_resolve(self):
        self.packages['vith-arch'] = {'spotify'}
        dependency_repo.configure(self.root, self.root, self.execute, {'spotify'})
        self.assertEqual(self.repositories(), ['core', 'extra', 'vith-gh', 'vith-arch'])
        self.assertEqual(self.config.read_text().count('SigLevel = Required'), 2)

    def test_missing_dependency_after_all_tiers_is_an_explicit_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'target not found: absent'):
            dependency_repo.configure(self.root, self.root, self.execute, {'absent'})
        self.assertEqual(self.repositories(), ['core', 'extra', 'vith-gh', 'vith-arch'])

    def test_missing_target_does_not_mask_an_operational_database_failure(self):
        self.query_failure = 'error: target not found: cargo\nerror: database is corrupted\n'
        with self.assertRaisesRegex(RuntimeError, 'database is corrupted'):
            dependency_repo.configure(self.root, self.root, self.execute, {'cargo'})
        self.assertEqual(self.repositories(), ['core', 'extra'])

    def test_database_query_error_is_not_treated_as_a_missing_package(self):
        self.query_failure = 'error: database is corrupted\n'
        with self.assertRaisesRegex(RuntimeError, 'database is corrupted'):
            dependency_repo.configure(self.root, self.root, self.execute, {'cargo'})
        self.assertEqual(self.repositories(), ['core', 'extra'])


if __name__ == '__main__':
    unittest.main()
