import email.message
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tools import github_api


class GitHubAPIBoundaries(unittest.TestCase):
    commit = '/repos/owner/repo/git/commits/' + 'a' * 40

    def failure(self, headers):
        message = email.message.Message()
        for name, value in headers.items():
            message[name] = value
        failure = urllib.error.HTTPError(
            github_api.API + '/rate_limit?secret=private-query', 403,
            'private-provider-reason', message, io.BytesIO(b'private-provider-body'))
        self.addCleanup(failure.close)
        opener = Mock()
        opener.open.side_effect = failure
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'private-api-token'}), patch.object(
                github_api.urllib.request, 'build_opener', return_value=opener), self.assertRaises(
                github_api.GitHubError) as caught:
            github_api.api('/rate_limit?secret=private-query')
        self.assertEqual(caught.exception.status, 403)
        return str(caught.exception)

    def test_http_failure_exposes_numeric_quota_but_not_provider_secrets(self):
        text = self.failure({'X-RateLimit-Limit': '1000', 'x-ratelimit-remaining': ' 000\t',
                             'X-RateLimit-Used': '001000', 'X-RateLimit-Reset': '1792000000',
                             'Retry-After': '005', 'Authorization': 'private-api-token',
                             'Location': 'https://example.invalid/private-redirect'})
        for field in ('x-ratelimit-limit=1000', 'x-ratelimit-remaining=0',
                      'x-ratelimit-used=1000', 'x-ratelimit-reset=1792000000', 'retry-after=5'):
            self.assertIn(field, text.lower())
        self.assertNotIn('private', text)

    def test_malformed_header_values_cannot_leak_or_mask_the_http_failure(self):
        text = self.failure({'X-RateLimit-Remaining': '0', 'X-RateLimit-Limit': '-1',
                             'X-RateLimit-Used': '\u0661', 'X-RateLimit-Reset': '9' * 13,
                             'Retry-After': '1\nprivate-header', 'X-Unknown': 'private-header'})
        self.assertIn('x-ratelimit-remaining=0', text.lower())
        for name in ('x-ratelimit-limit', 'x-ratelimit-used', 'x-ratelimit-reset', 'retry-after'):
            self.assertNotIn(name, text.lower())
        self.assertNotIn('private', text)

    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())

    def http_error(self, url, status, reason):
        failure = urllib.error.HTTPError(url, status, reason, {}, None)
        self.addCleanup(failure.close)
        return failure

    def test_immutable_reread_survives_transport_quota_and_does_not_share_mutations(self):
        opener = Mock()
        opener.open.side_effect = [self.response({'sha': 'a' * 40, 'tree': {'sha': 'b' * 40}}),
                                  self.http_error(github_api.API + self.commit, 429, 'quota')]
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'principal-one'}), patch.object(
                github_api.urllib.request, 'build_opener', return_value=opener), github_api.immutable_reads():
            first = github_api.api(self.commit)
            first['tree']['sha'] = 'poisoned-by-consumer'
            second = github_api.api(self.commit)
        self.assertEqual(second['tree']['sha'], 'b' * 40)

    def test_mutable_refs_and_writes_remain_fresh_inside_the_read_scope(self):
        opener = Mock()
        opener.open.side_effect = [self.response({'object': {'sha': 'a' * 40}}),
                                  self.response({'object': {'sha': 'b' * 40}}),
                                  self.response({'sha': 'c' * 40}), self.response({'sha': 'd' * 40})]
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'principal-one'}), patch.object(
                github_api.urllib.request, 'build_opener', return_value=opener), github_api.immutable_reads():
            first = github_api.api('/repos/owner/repo/git/ref/heads/main')
            second = github_api.api('/repos/owner/repo/git/ref/heads/main')
            created = github_api.api('/repos/owner/repo/git/commits', 'POST', {'message': 'same'})
            recreated = github_api.api('/repos/owner/repo/git/commits', 'POST', {'message': 'same'})
        self.assertEqual((first['object']['sha'], second['object']['sha']), ('a' * 40, 'b' * 40))
        self.assertEqual((created['sha'], recreated['sha']), ('c' * 40, 'd' * 40))

    def test_changed_principal_and_new_scope_cannot_reuse_authorized_reads(self):
        opener = Mock()
        opener.open.side_effect = [self.response({'sha': 'a' * 40}),
                                  self.http_error(github_api.API + self.commit, 403, 'denied'),
                                  self.http_error(github_api.API + self.commit, 429, 'quota')]
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'principal-one'}), patch.object(
                github_api.urllib.request, 'build_opener', return_value=opener):
            with github_api.immutable_reads():
                github_api.api(self.commit)
                with patch.dict(os.environ, {'GITHUB_TOKEN': 'principal-two'}), self.assertRaises(
                        github_api.GitHubError) as changed:
                    github_api.api(self.commit)
                self.assertEqual(changed.exception.status, 403)
            with github_api.immutable_reads(), self.assertRaises(github_api.GitHubError) as fresh:
                github_api.api(self.commit)
            self.assertEqual(fresh.exception.status, 429)

    def test_failed_read_does_not_poison_a_later_successful_response(self):
        opener = Mock()
        opener.open.side_effect = [self.http_error(github_api.API + self.commit, 403, 'denied'),
                                  self.response({'sha': 'a' * 40})]
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'principal-one'}), patch.object(
                github_api.urllib.request, 'build_opener', return_value=opener), github_api.immutable_reads():
            with self.assertRaises(github_api.GitHubError):
                github_api.api(self.commit)
            result = github_api.api(self.commit)
        self.assertEqual(result['sha'], 'a' * 40)

    def test_pinned_archive_reuse_outlives_the_first_destination_and_retains_bounds(self):
        root = Path.home() / '.local/state/omp/work/github-api-tests'
        root.mkdir(parents=True, exist_ok=True)
        payload = b'immutable-public-archive-bytes'
        response = io.BytesIO(payload)
        response.url = 'https://codeload.github.com/owner/repo/legacy.tar.gz/' + 'a' * 40
        url = 'https://api.github.com/repos/owner/repo/tarball/' + 'a' * 40
        with tempfile.TemporaryDirectory(dir=root) as temporary, patch.object(
                github_api.urllib.request, 'urlopen', side_effect=[response,
                    self.http_error(url, 429, 'quota')]), github_api.immutable_reads():
            work = Path(temporary)
            first = github_api.download(url, work / 'first', maximum=100)
            first.unlink()
            second = github_api.download(url, work / 'second', maximum=100)
            self.assertEqual(second.read_bytes(), payload)
            with self.assertRaisesRegex(ValueError, 'size|limit'):
                github_api.download(url, work / 'too-small', maximum=4)
            self.assertFalse((work / 'too-small').exists())
