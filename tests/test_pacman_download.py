import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from tools import pacman_download


RETRIEVAL = "error: failed retrieving file 'package.pkg.tar.zst' from github.com : "
SUMMARY = 'error: failed to commit transaction (download library error)\n'
COMMAND = ['pacman', '--config', '/fresh/pacman.conf', '--sync',
           '--downloadonly', '--noconfirm', '--', 'core/linux', 'repo/package']


class PacmanFailureClassification(unittest.TestCase):
    def test_recognizes_only_transient_retrieval_reasons(self):
        reasons = [
            'The requested URL returned error: ' + str(code)
            for code in (408, 500, 502, 503, 504)
        ] + [
            'Connection timed out after 10000 milliseconds',
            'Operation timed out after 10000 milliseconds with 0 bytes received',
            'Recv failure: Connection reset by peer',
            'Failed to connect to github.com port 443 after 10000 ms: Timeout was reached',
            'Operation too slow. Less than 1 bytes/sec transferred the last 10 seconds',
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertTrue(pacman_download.transient_output(
                    RETRIEVAL + reason + '\nwarning: failed to retrieve some files\n' +
                    SUMMARY + 'Errors occurred, no packages were upgraded.\n'))

    def test_native_http_retrieval_summary_allows_transient_recovery(self):
        output = (RETRIEVAL + 'The requested URL returned error: 504\n'
                  'warning: failed to retrieve some files\n'
                  'error: failed to commit transaction (failed to retrieve some files)\n'
                  'Errors occurred, no packages were upgraded.\n')
        self.assertTrue(pacman_download.transient_output(output))

    def test_permanent_and_unknown_reasons_never_retry(self):
        reasons = [
            'The requested URL returned error: ' + str(code)
            for code in (401, 403, 404, 429, 501)
        ] + [
            'SSL certificate problem: certificate has expired',
            'SSL connection timeout', 'TLS certificate verification failed',
            'No space left on device', 'Write error', 'unknown retrieval problem',
            'The requested URL returned error: 504 (certificate invalid)',
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertFalse(pacman_download.transient_output(RETRIEVAL + reason + '\n' + SUMMARY))

    def test_mixed_integrity_disk_dependency_and_unknown_errors_fail_closed(self):
        for error in [
            'error: invalid or corrupted package (PGP signature)',
            'error: package: signature is unknown trust',
            'error: invalid or corrupted package (checksum)',
            'error: failed to commit transaction (not enough free disk space)',
            'error: failed to prepare transaction (could not satisfy dependencies)',
            'error: an unexpected error occurred',
            RETRIEVAL + 'The requested URL returned error: 404',
        ]:
            with self.subTest(error=error):
                self.assertFalse(pacman_download.transient_output(
                    RETRIEVAL + 'The requested URL returned error: 504\n' + error + '\n' + SUMMARY))
        for output in ['', SUMMARY, 'warning: failed to retrieve some files\n']:
            with self.subTest(output=output):
                self.assertFalse(pacman_download.transient_output(output))


class PacmanCancellation(unittest.TestCase):
    def test_signalled_download_is_not_retried_even_after_a_transient_error(self):
        output = RETRIEVAL + 'The requested URL returned error: 504\n' + SUMMARY
        self.assertFalse(pacman_download.transient_failure(
            pacman_download.DownloadFailure(-15, output)))


    def test_handled_interrupt_exit_codes_do_not_start_another_download(self):
        output = RETRIEVAL + 'The requested URL returned error: 504\n' + SUMMARY
        for status in (129, 130):
            with self.subTest(status=status):
                self.assertFalse(pacman_download.transient_failure(
                    pacman_download.DownloadFailure(status, output)))

    def test_reported_interrupts_stop_even_with_an_ordinary_error_status(self):
        output = RETRIEVAL + 'The requested URL returned error: 504\n' + SUMMARY
        for message in ('Interrupt signal received', 'Hangup signal received'):
            with self.subTest(message=message):
                self.assertFalse(pacman_download.transient_failure(
                    pacman_download.DownloadFailure(1, output + message + '\n')))


class PacmanCommandBoundary(unittest.TestCase):

    def test_rejects_install_other_operations_and_unsafe_options_before_launch(self):
        commands = [[], ['sh', '-c', 'pacman --sync --downloadonly'],
                    ['pacman', '-Sw', '--', 'package'],
                    ['pacman', '--sync', '--', 'package'],
                    ['pacman', '--downloadonly', '--', 'package'],
                    COMMAND[:4] + ['--remove'] + COMMAND[4:],
                    COMMAND[:4] + ['--refresh'] + COMMAND[4:],
                    COMMAND[:4] + ['--hookdir', '/hooks'] + COMMAND[4:],
                    COMMAND[:-1] + ['--remove'], COMMAND[:-3],
                    ['pacman', '--config', '--sync', '--downloadonly', '--', 'package']]
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                pacman_download.validate_command(command)


class PacmanCLIBoundary(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get('TMPDIR', Path.home() / '.local/state/omp/work/test-pacman-download'))
        base.mkdir(parents=True, exist_ok=True)
        self.session = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.session.cleanup)
        self.directory = Path(self.session.name)
        self.logs = self.directory / 'evidence'

    def test_cli_rejects_installation_before_creating_logs_or_spawning(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            pacman_download.main([str(self.logs), '--', 'pacman', '--sync', '--', 'package'])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.logs.exists())

    def test_cli_requires_command_separator(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            pacman_download.main([str(self.logs)] + COMMAND)
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.logs.exists())


if __name__ == '__main__':
    unittest.main()
