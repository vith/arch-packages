import contextlib
import http.client
import io
import os
from pathlib import Path
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from tools import github_api


class PublicDownloadRetries(unittest.TestCase):
    url = 'https://github.com/owner/repo/releases/download/snapshot/package.tar.zst'

    def setUp(self):
        root = Path.home() / '.local/state/omp/work/public-download-retry-tests'
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / 'package.tar.zst'
        self.target.write_bytes(b'previous verified bytes')

    def response(self, payload=b'complete replacement', url='https://release-assets.githubusercontent.com/asset'):
        response = io.BytesIO(payload)
        response.url = url
        return response

    def failure(self, status):
        error = urllib.error.HTTPError(self.url, status, 'provider message', {}, None)
        self.addCleanup(error.close)
        return error

    def test_transient_http_failures_eventually_publish_only_complete_bytes(self):
        requests = []
        outcomes = iter([self.failure(503), self.failure(504), self.response()])

        def open_asset(request, **kwargs):
            requests.append(request)
            self.assertEqual(self.target.read_bytes(), b'previous verified bytes')
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.dict(os.environ, {'GITHUB_TOKEN': 'private-token'}), patch.object(
                github_api.urllib.request, 'urlopen', side_effect=open_asset), patch('time.sleep') as sleep:
            github_api.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b'complete replacement')
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertTrue(all(request.get_header('Authorization') is None for request in requests))
        self.assertFalse(self.target.with_name(self.target.name + '.part').exists())

    def test_interrupted_body_restarts_without_partial_prefix_or_target_mutation(self):
        class Interrupted(io.BytesIO):
            url = 'https://release-assets.githubusercontent.com/asset'

            def read(self, amount=-1):
                if self.tell():
                    raise TimeoutError('body stalled')
                return super().read(amount)

        with patch.object(github_api.urllib.request, 'urlopen', side_effect=[
                Interrupted(b'partial prefix'), self.response()]), patch('time.sleep') as sleep:
            github_api.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b'complete replacement')
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1])
        self.assertFalse(self.target.with_name(self.target.name + '.part').exists())

    def test_exhaustion_preserves_target_and_raises_last_original_failure(self):
        failures = [self.failure(504) for _ in range(5)]
        with patch.object(github_api.urllib.request, 'urlopen', side_effect=failures), patch(
                'time.sleep') as sleep, self.assertRaises(urllib.error.HTTPError) as caught:
            github_api.download(self.url, self.target)
        self.assertIs(caught.exception, failures[-1])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8])
        self.assertEqual(self.target.read_bytes(), b'previous verified bytes')
        self.assertFalse(self.target.with_name(self.target.name + '.part').exists())

    def test_permanent_http_errors_stop_without_retrying(self):
        for status in (401, 403, 404, 429):
            with self.subTest(status=status), patch.object(github_api.urllib.request, 'urlopen',
                    side_effect=[self.failure(status), self.response()]), patch('time.sleep') as sleep, self.assertRaises(
                    urllib.error.HTTPError) as caught:
                github_api.download(self.url, self.target)
            self.assertEqual(caught.exception.code, status)
            sleep.assert_not_called()
            self.assertEqual(self.target.read_bytes(), b'previous verified bytes')

    def test_wrapped_timeout_and_incomplete_body_are_transient(self):
        for failure in (urllib.error.URLError(TimeoutError('connection stalled')),
                        http.client.IncompleteRead(b'partial', 100)):
            with self.subTest(failure=type(failure).__name__), patch.object(
                    github_api.urllib.request, 'urlopen', side_effect=[failure, self.response()]), patch('time.sleep') as sleep:
                github_api.download(self.url, self.target)
            self.assertEqual(self.target.read_bytes(), b'complete replacement')
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1])

    def test_certificate_errors_do_not_retry_or_replace_target(self):
        failure = urllib.error.URLError(ssl.SSLCertVerificationError('untrusted certificate'))
        with patch.object(github_api.urllib.request, 'urlopen', side_effect=[failure, self.response()]), patch(
                'time.sleep') as sleep, self.assertRaises(urllib.error.URLError):
            github_api.download(self.url, self.target)
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'previous verified bytes')

    def test_filesystem_timeout_is_not_retried_as_a_network_timeout(self):
        staging = self.target.with_name(self.target.name + '.part')
        original_open = Path.open
        failure = TimeoutError('local filesystem stalled')

        class StalledWriter:
            def write(self, data):
                raise failure

        @contextlib.contextmanager
        def open_file(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as stream:
                yield StalledWriter() if path == staging else stream

        with patch.object(github_api.urllib.request, 'urlopen', return_value=self.response()), patch.object(
                Path, 'open', open_file), patch('time.sleep') as sleep, self.assertRaises(TimeoutError) as caught:
            github_api.download(self.url, self.target)
        self.assertIs(caught.exception, failure)
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'previous verified bytes')
        self.assertFalse(staging.exists())

    def test_cancellation_is_not_retried(self):
        with patch.object(github_api.urllib.request, 'urlopen', side_effect=KeyboardInterrupt), patch(
                'time.sleep') as sleep, self.assertRaises(KeyboardInterrupt):
            github_api.download(self.url, self.target)
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'previous verified bytes')

    def test_retry_diagnostics_do_not_disclose_provider_details_or_urls(self):
        error = urllib.error.HTTPError(self.url + '?private-query', 504,
                                       'private-provider-detail', {}, None)
        self.addCleanup(error.close)
        diagnostics = io.StringIO()
        with patch.object(github_api.urllib.request, 'urlopen', side_effect=[error, self.response()]), patch(
                'time.sleep'), contextlib.redirect_stderr(diagnostics):
            github_api.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b'complete replacement')
        self.assertNotIn('private', diagnostics.getvalue())
        self.assertNotIn('https://', diagnostics.getvalue())

    def test_size_and_transport_boundaries_do_not_retry(self):
        for response, maximum, message in ((self.response(b'oversized'), 3, 'size limit'),
                                           (self.response(url='http://insecure.invalid/asset'), 100, 'downgraded')):
            with self.subTest(message=message), patch.object(github_api.urllib.request, 'urlopen',
                    return_value=response), patch('time.sleep') as sleep, self.assertRaisesRegex(ValueError, message):
                github_api.download(self.url, self.target, maximum=maximum)
            sleep.assert_not_called()
            self.assertEqual(self.target.read_bytes(), b'previous verified bytes')
            self.assertFalse(self.target.with_name(self.target.name + '.part').exists())
