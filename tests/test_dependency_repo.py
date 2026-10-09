import unittest

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


if __name__ == '__main__':
    unittest.main()
