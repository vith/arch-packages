from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.update_sweep import report_failures


class UpdateFailureReporting(unittest.TestCase):
    def setUp(self):
        root = Path.home()/'.local/state/omp/work/update-reporting-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def stage_outputs(self, errors):
        output = self.root/'output'
        output.unlink(missing_ok=True)
        with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output), 'GITHUB_ACTIONS': 'true'}), \
                redirect_stderr(io.StringIO()):
            report_failures(errors)
        return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def report(self, needs):
        summary = self.root/'summary'
        result = subprocess.run(
            [sys.executable, '-m', 'tools.update_sweep'],
            env={**os.environ, 'UPDATE_NEEDS': json.dumps(needs),
                 'GITHUB_ACTIONS': 'true', 'GITHUB_STEP_SUMMARY': str(summary)},
            text=True, capture_output=True)
        return result, summary.read_text() if summary.exists() else ''

    def test_failed_report_job_retains_package_identity_and_cause_from_green_stage(self):
        error = {'pkgbase': 'computer-use-linux', 'stage': 'write',
                 'watcher_id': 'computer-use-linux-aur',
                 'error': 'ValueError: missing durable proposal receipt'}
        outputs = self.stage_outputs([error])
        result, summary = self.report({
            'reconcile': {'result': 'success', 'outputs': self.stage_outputs([])},
            'discover': {'result': 'skipped', 'outputs': {}},
            'write-proposals': {'result': 'success', 'outputs': outputs}})
        self.assertEqual(result.returncode, 1, result.stderr)
        for value in ['write-proposals', *error.values()]:
            self.assertIn(value, result.stderr)
            self.assertIn(value, summary)

    def test_multiple_stage_errors_are_safe_in_annotations_and_summary(self):
        first = {'pkgbase': 'bad', 'stage': 'discover', 'watcher_id': 'aur',
                 'error': '<broken>|100%\n::notice::not a workflow command'}
        second = {'pkgbase': 'other', 'stage': 'reconcile', 'pr_number': 42,
                  'error': 'rejected recipe'}
        result, summary = self.report({
            'discover': {'outputs': self.stage_outputs([first])},
            'reconcile': {'outputs': self.stage_outputs([second])}})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('100%25', result.stderr)
        self.assertNotIn('\n::notice::', result.stderr)
        self.assertIn('&lt;broken&gt;', summary)
        self.assertNotIn('<broken>', summary)
        for value in ['other', '42', 'rejected recipe']:
            self.assertIn(value, result.stderr)
            self.assertIn(value, summary)

    def test_missing_diagnostic_handoff_cannot_turn_a_failure_green(self):
        result, summary = self.report({
            'write-proposals': {'outputs': {'package_failures': '1'}}})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('write-proposals', result.stderr)
        self.assertIn('diagnostic', result.stderr.lower())
        self.assertIn('write-proposals', summary)

    def test_healthy_and_skipped_stages_do_not_fail_reporting(self):
        result, summary = self.report({
            'discover': {'result': 'success', 'outputs': self.stage_outputs([])},
            'write-proposals': {'result': 'skipped', 'outputs': {}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('::error', result.stderr)
        self.assertEqual(summary, '')


if __name__ == '__main__':
    unittest.main()
