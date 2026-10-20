from contextlib import contextmanager, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import imports, recipe_acceptance, recipe_state, recipes, sources, update


class PackageSweepIsolation(unittest.TestCase):
    def setUp(self):
        root = Path.home()/'.local/state/omp/work/update-sweep-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @contextmanager
    def reconciliation_fixture(self, policy_revision=2):
        bad = {'number': 1, 'state': 'closed', 'merged': True, 'merged_at': '2026-10-14',
               'merge_commit_sha': 'e'*40,
               'base': {'ref': 'pkg/bad', 'sha': 'b'*40, 'repo': {'full_name': 'owner/repo'}},
               'head': {'ref': 'recipe-updates/bad', 'sha': 'c'*40, 'repo': {'full_name': 'owner/repo'}}}
        good = {'number': 2, 'state': 'open',
                'base': {'ref': 'pkg/good', 'sha': 'b'*40},
                'head': {'ref': 'recipe-updates/good', 'sha': 'd'*40}}
        pending = {'head': 'c'*40, 'state': 'success', 'request_id': 'bad-request'}
        candidate = {'pr_number': 1, 'recipe_base': 'b'*40, 'pkgbase': 'bad', 'head': 'c'*40,
                     'head_branch': 'recipe-updates/bad',
                     'packages': [{'pkgbase': 'bad', 'policy': {'revision': 1}}]}
        def accept(number, control):
            return recipe_acceptance.acceptance_candidate(bad, control, {}, {'revision': policy_revision})

        def api(path, method='GET', body=None):
            if path.endswith('/pulls/1'):
                return bad
            if method == 'POST':
                dispatched.append(body['inputs'])
                return {}
            raise AssertionError(path)

        dispatched = []
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}), \
                patch.object(update, 'main_sha', return_value='a'*40), \
                patch.object(update, 'control_checkout', return_value=('a'*40, {})), \
                patch.object(imports, 'policies', return_value={'bad': {}, 'good': {}}), \
                patch.object(recipe_state, 'StateSnapshot') as snapshot, \
                patch.object(recipe_state, 'protected_branch'), \
                patch.object(update, 'trusted_checkout_sha', return_value='a'*40), \
                patch.object(update, 'open_pr_snapshot', return_value=[good]), \
                patch.object(update, 'api', side_effect=api), \
                patch.object(update, 'pin_at', return_value='b'*40), \
                patch.object(recipes, 'is_ancestor', return_value=False), \
                patch.object(recipe_state, 'pending_build', side_effect=lambda name, **kwargs: pending if name == 'bad' else None), \
                patch.object(recipe_state, 'build_request', return_value=candidate), \
                patch.object(recipe_acceptance, 'accepted_receipt', side_effect=accept), \
                patch.object(recipe_acceptance, 'write_bookkeeping') as bookkeeping:
            snapshot.return_value.load_many.side_effect = [
                {('pending', 'bad'): pending, ('pending', 'good'): None},
                {('build-request', 'bad-request'): candidate}]
            yield {'dispatched': dispatched, 'bookkeeping': bookkeeping}

    def test_rejected_acceptance_does_not_block_another_recipe_dispatch(self):
        with self.reconciliation_fixture() as observed:
            with self.assertRaises(ValueError) as failed:
                recipe_acceptance.reconcile()
        self.assertEqual(observed['dispatched'], [{'pr_number': '2', 'expected_head': 'd'*40}])
        self.assertEqual(failed.exception.errors[0]['pkgbase'], 'bad')
        self.assertIn('policy differs', failed.exception.errors[0]['error'])
        observed['bookkeeping'].assert_not_called()

    def test_early_shared_main_drift_cannot_be_deferred_or_dispatch_more_recipes(self):
        with self.reconciliation_fixture(policy_revision=1) as observed, \
                patch.object(update, 'main_sha', side_effect=['a'*40, 'b'*40]), \
                patch.object(sys, 'argv', ['update', 'reconcile', '--defer-package-failures']), \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(ValueError):
                update.cli()
        self.assertEqual(observed['dispatched'], [])
        observed['bookkeeping'].assert_not_called()

    def test_explicit_pr_policy_failure_stays_fatal_in_deferred_workflow_mode(self):
        with self.reconciliation_fixture() as observed, \
                patch.object(sys, 'argv', ['update', 'reconcile', '--pr', '1', '--defer-package-failures']):
            with self.assertRaisesRegex(ValueError, 'policy differs'):
                update.cli()
        self.assertEqual(observed['dispatched'], [])
        observed['bookkeeping'].assert_not_called()

    def test_readonly_failed_discovery_keeps_healthy_receipts_and_excludes_partial_work(self):
        for name in ('bad', 'good'):
            update.dump(self.root/'control/upstream'/f'{name}.json',
                        {'watchers': [{'id': 'release', 'kind': 'tag'}]})
            update.dump(self.root/'control/inputs'/f'{name}.json', {'sources': []})
        before = {name: (self.root/'control/inputs'/f'{name}.json').read_bytes()
                  for name in ('bad', 'good')}

        def discover(configured, identity):
            if Path(configured['work_dir']).parent.name == 'bad':
                mirror = Path(configured['work_dir'])/'mirror'
                mirror.mkdir()
                (mirror/'frozen-source').write_text('frozen input')
                mirror.chmod(0o555)
                raise ValueError('invalid upstream identity')
            return None
        with patch.object(update, 'ROOT', self.root/'control'), \
                patch.object(update, 'control_checkout', return_value=('a'*40, {'bad': 'b'*40, 'good': 'c'*40})), \
                patch.object(update, 'policy_at', return_value={'bad': {}, 'good': {}}), \
                patch.object(sources, 'discover_release_tag', side_effect=discover):
            with self.assertRaises(Exception) as failed:
                update.discover(self.root/'discovery')
        try:
            batch = update.load(self.root/'discovery/receipts.json')
        except FileNotFoundError:
            self.fail('The failed package prevented healthy discovery receipts from being written')
        self.assertEqual([r['pkgbase'] for r in batch['receipts']], ['good'])
        self.assertFalse((self.root/'discovery/bad').exists())
        self.assertEqual(failed.exception.errors[0]['pkgbase'], 'bad')
        for name, content in before.items():
            self.assertEqual((self.root/'control/inputs'/f'{name}.json').read_bytes(), content)

    def test_divergent_recipe_does_not_block_other_pending_proposal(self):
        control = self.root/'control'
        receipts = []
        for name, pin in [('bad', 'b'*40), ('good', 'c'*40)]:
            update.dump(control/'upstream'/f'{name}.json', {'watchers': [{'id': 'release', 'kind': 'tag'}]})
            receipts.append({'pkgbase': name, 'watcher_id': 'release', 'base': 'a'*40,
                             'recipe_commit': pin, 'unchanged': True})
        update.dump(self.root/'discovery/receipts.json', {'schema': 1, 'base': 'a'*40, 'receipts': receipts})
        good = {'number': 2, 'state': 'open', 'base': {'ref': 'pkg/good', 'sha': 'c'*40},
                'head': {'ref': 'recipe-updates/good/candidate', 'sha': 'd'*40}}
        dispatched = []
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}), \
                patch.object(update, 'ROOT', control), \
                patch.object(update, 'main_sha', return_value='a'*40), \
                patch.object(update, 'control_checkout', return_value=('a'*40, {'bad': 'b'*40, 'good': 'c'*40})), \
                patch.object(update, 'policy_at', return_value={'bad': {}, 'good': {}}), \
                patch.object(recipe_state, 'protected_branch'), \
                patch.object(update, 'ref_head', side_effect=lambda ref: 'e'*40 if ref == 'pkg/bad' else 'c'*40), \
                patch.object(update, 'open_pr_snapshot', return_value=[good]), \
                patch.object(recipe_state, 'load_many', side_effect=[{}, {}]), \
                patch.object(recipe_state, 'pending_build', return_value=None), \
                patch.object(update, 'api', side_effect=lambda path, method, body: dispatched.append(body['inputs'])):
            with self.assertRaises(ValueError) as failed:
                update.write_proposals(self.root/'discovery')
        self.assertEqual(dispatched, [{'pr_number': '2', 'expected_head': 'd'*40}])
        self.assertEqual(failed.exception.errors[0]['pkgbase'], 'bad')
        self.assertIn('divergent work', failed.exception.errors[0]['error'])

    def test_cli_defers_package_errors_without_losing_counts_during_packing(self):
        for name in ('bad', 'good'):
            update.dump(self.root/'control/upstream'/f'{name}.json',
                        {'watchers': [{'id': 'release', 'kind': 'tag'}]})
            update.dump(self.root/'control/inputs'/f'{name}.json', {'sources': []})
        for deferred in (False, True):
            with self.subTest(deferred=deferred):
                directory = self.root/('deferred' if deferred else 'strict')
                output = self.root/('deferred-output' if deferred else 'strict-output')
                argv = ['update', 'discover', '--directory', str(directory)]
                if deferred:
                    argv.append('--defer-package-failures')
                with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output), 'GITHUB_ACTIONS': 'false'}), \
                        patch.object(sys, 'argv', argv), \
                        patch.object(update, 'ROOT', self.root/'control'), \
                        patch.object(update, 'control_checkout', return_value=('a'*40, {'bad': 'b'*40, 'good': 'c'*40})), \
                        patch.object(update, 'policy_at', return_value={'bad': {}, 'good': {}}), \
                        patch.object(sources, 'discover_release_tag', side_effect=[ValueError('invalid upstream identity'), None]), \
                        redirect_stderr(io.StringIO()):
                    if deferred:
                        update.cli()
                    else:
                        with self.assertRaises(SystemExit) as failed:
                            update.cli()
                        self.assertEqual(failed.exception.code, 2)
                    archive = self.root/('deferred.tar' if deferred else 'strict.tar')
                    with patch.object(sys, 'argv', ['update', 'pack', '--directory', str(directory),
                                                  '--archive', str(archive)]):
                        update.cli()
                outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(outputs['package_failures'], '1')
                errors = json.loads(outputs['package_errors'])
                self.assertEqual([(error['pkgbase'], error['stage']) for error in errors],
                                 [('bad', 'discover')])
                self.assertEqual([r['pkgbase'] for r in update.load(directory/'receipts.json')['receipts']],
                                 ['good'])

    def test_changed_shared_base_remains_fatal_even_with_keep_going(self):
        update.dump(self.root/'discovery/receipts.json', {'schema': 1, 'base': 'a'*40, 'receipts': []})
        with patch.object(update, 'main_sha', return_value='b'*40), \
                patch.object(update, 'api') as remote, \
                self.assertRaisesRegex(ValueError, 'discovery base changed'):
            update.write_proposals(self.root/'discovery')
        remote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
