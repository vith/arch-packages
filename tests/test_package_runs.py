import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import package_runs

ROOT = Path.home() / '.local/state/omp/work/package-run-tests'


class ExactPackageWorkers(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.package = {'pkgbase': 'first', 'recipe_commit': 'a' * 40}
        self.plan = {'repository': 'vith/arch-packages', 'request_id': 'request-one',
                     'pr_number': 12, 'head': 'a' * 40, 'packages': [self.package]}
        self.producer = {'run_id': '77', 'run_attempt': '2'}
        self.pending = {'pkgbase': 'first', 'request_id': 'request-one', 'head': 'a' * 40,
                        'state': 'running', 'worker': self.producer}

    def test_existing_reservation_does_not_redispatch(self):
        with patch('tools.package_runs.recipe_state.reserve_build', return_value=(self.pending, False)), patch(
                'tools.package_runs.github_api.api') as api:
            self.assertEqual(package_runs.dispatch(self.plan, self.package), (self.pending, False))
            api.assert_not_called()

    def collect(self, run, descriptor=None):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        (work / 'candidate.json').write_text(json.dumps(self.plan))
        patches = [patch('tools.package_runs.dispatch', return_value=(self.pending, False)),
                   patch('tools.package_runs.recipe_state.pending_build', return_value=self.pending),
                   patch('tools.package_runs.recipe_state.build_request', return_value=self.plan),
                   patch('tools.package_runs.build_store.producer_run', return_value=run),
                   patch('tools.package_runs.build_store.lookup', return_value=descriptor),
                   patch('tools.package_runs.recipe_state.finish_build'),
                   patch('tools.package_runs.recipe_state.StateSnapshot')]
        mocks = [p.start() for p in patches]
        mocks[6].return_value.load_optional.return_value = descriptor
        for p in patches:
            self.addCleanup(p.stop)
        return work, mocks

    def test_late_worker_failure_cannot_use_existing_output(self):
        work, mocks = self.collect({'status': 'completed', 'conclusion': 'failure'}, {'producer': self.producer})
        with self.assertRaisesRegex(RuntimeError, 'Entire package worker failed'):
            package_runs.collect(work)
        mocks[4].assert_not_called()
        mocks[5].assert_called_once_with('first', 'request-one', 'failure')
        self.assertFalse((work / 'build-results.json').exists())

    def test_success_without_durable_result_fails_closed(self):
        work, mocks = self.collect({'status': 'completed', 'conclusion': 'success'})
        with self.assertRaisesRegex(RuntimeError, 'lacks durable output'):
            package_runs.collect(work)
        mocks[5].assert_called_once_with('first', 'request-one', 'failure')

    def test_success_collection_is_metadata_only(self):
        descriptor = {'producer': self.producer, 'evidence': {'packages': [{'pkgbase': 'first', 'files': []}]}}
        work, _ = self.collect({'status': 'completed', 'conclusion': 'success'}, descriptor)
        with patch('tools.package_runs.build_store.materialize', side_effect=AssertionError('package download')):
            self.assertEqual(package_runs.collect(work), {'first': descriptor})
        self.assertEqual(json.loads((work / 'build-results.json').read_text()), {'first': descriptor})
        self.assertEqual(json.loads((work / 'native-evidence.json').read_text())['packages'], descriptor['evidence']['packages'])
        self.assertFalse((work / 'unsigned.tar').exists())

    def test_duplicate_collector_uses_reserved_original_request(self):
        descriptor = {'producer': self.producer, 'evidence': {'packages': [{'pkgbase': 'first', 'files': []}]}}
        work, _ = self.collect({'status': 'completed', 'conclusion': 'success'}, descriptor)
        competing = {**self.plan, 'request_id': 'second-coordinator-request'}
        (work / 'candidate.json').write_text(json.dumps(competing))
        package_runs.collect(work)
        self.assertEqual(json.loads((work / 'candidate.json').read_text()), self.plan)
        self.assertEqual(json.loads((work / 'native-evidence.json').read_text())['request_id'], 'request-one')

    def test_failed_same_input_is_not_automatically_retried(self):
        self.pending['state'] = 'failure'
        work, mocks = self.collect({'status': 'completed', 'conclusion': 'failure'})
        with self.assertRaisesRegex(RuntimeError, 'explicit retry builds fresh'):
            package_runs.collect(work)
        mocks[3].assert_not_called()
        mocks[4].assert_not_called()
        self.assertFalse((work / 'build-results.json').exists())

    def accepted_cache(self, work, image='official-image', harness='h' * 64, pkgbase='first'):
        descriptor = {'schema': 3, 'pkgbase': pkgbase, 'record': self.plan,
                      'producer': self.producer, 'evidence': {'image': image, 'harness_sha': harness,
                                                             'packages': [{'input_digest': 'd' * 64}]}}
        (work / 'acceptance').mkdir()
        (work / 'acceptance/first.json').write_text(json.dumps({'pkgbase': pkgbase, 'build': descriptor}))
        return descriptor

    def test_cache_restore_uses_exact_main_accepted_attempt_key(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            descriptor = self.accepted_cache(work)
            with patch('tools.package_runs.build_store.lookup', return_value=descriptor), patch(
                    'tools.package_runs.build_store.producer_run'):
                selected = package_runs.accepted_cache_key(work, 'first', 'official-image', 'h' * 64)
            scope = package_runs.hashlib.sha256(package_runs.github_api.canonical(
                {'image': 'official-image', 'harness': 'h' * 64})).hexdigest()[:24]
            self.assertEqual(selected['key'], 'pr-build-v2-first-12-' + 'a' * 40 + '-' + scope + '-' + 'd' * 64 + '-77-2')
            self.assertEqual(Path(selected['path']).name, 'a' * 40)

    def test_cache_without_main_acceptance_stays_cold(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            self.assertIsNone(package_runs.accepted_cache_key(Path(temporary), 'first', 'official-image', 'h' * 64))

    def test_accepted_cache_requires_matching_actual_image_and_harness(self):
        for image, harness in [('different-image', 'h' * 64), ('official-image', 'x' * 64)]:
            with self.subTest(image=image, harness=harness), tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                work = Path(temporary)
                descriptor = self.accepted_cache(work)
                with patch('tools.package_runs.build_store.lookup', return_value=descriptor):
                    self.assertIsNone(package_runs.accepted_cache_key(work, 'first', image, harness))

    def test_other_package_acceptance_cannot_authorize_cache(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            self.accepted_cache(work, pkgbase='other')
            with self.assertRaisesRegex(ValueError, 'another package'):
                package_runs.accepted_cache_key(work, 'first', 'official-image', 'h' * 64)

    def test_failed_original_worker_cannot_authorize_accepted_cache(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            descriptor = self.accepted_cache(work)
            with patch('tools.package_runs.build_store.lookup', return_value=descriptor), patch(
                    'tools.package_runs.build_store.producer_run', side_effect=ValueError('entire original worker did not succeed')):
                with self.assertRaisesRegex(ValueError, 'entire original worker'):
                    package_runs.accepted_cache_key(work, 'first', 'official-image', 'h' * 64)

    def test_accepted_success_with_missing_save_does_not_admit_failed_attempt_cache(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            descriptor = self.accepted_cache(work)
            with patch('tools.package_runs.build_store.lookup', return_value=descriptor), patch(
                    'tools.package_runs.build_store.producer_run'):
                selected = package_runs.accepted_cache_key(work, 'first', 'official-image', 'h' * 64)
            fake_home = work / 'home'
            source = fake_home / '.local/state/arch-packages/cache/pr/first' / ('a' * 40)
            destination = source.with_name('b' * 40)
            source.mkdir(parents=True)
            (source / 'unaccepted-progress').write_text('failed attempt A')
            failed_key = selected['key'].removesuffix('-77-2') + '-77-1'
            with patch('tools.package_runs.Path.home', return_value=fake_home):
                self.assertFalse(package_runs.admit_cache(source, destination, selected['key'], failed_key))
            self.assertFalse(source.exists())
            self.assertFalse(destination.exists())

    def test_failed_restore_discards_partial_accepted_payload(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            home = Path(temporary)
            source = home / '.local/state/arch-packages/cache/pr/first' / ('a' * 40)
            destination = source.with_name('b' * 40)
            source.mkdir(parents=True)
            (source / 'partial-progress').write_text('incomplete restoration')
            with patch('tools.package_runs.Path.home', return_value=home):
                self.assertFalse(package_runs.admit_cache(source, destination, 'accepted-key', ''))
            self.assertFalse(source.exists())
            self.assertFalse(destination.exists())

    def test_exact_accepted_cache_is_admitted_to_current_head(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            home = Path(temporary)
            source = home / '.local/state/arch-packages/cache/pr/first' / ('a' * 40)
            destination = source.with_name('b' * 40)
            source.mkdir(parents=True)
            (source / 'accepted-progress').write_text('successful B')
            with patch('tools.package_runs.Path.home', return_value=home):
                self.assertTrue(package_runs.admit_cache(source, destination, 'accepted-key', 'accepted-key'))
            self.assertEqual((destination / 'accepted-progress').read_text(), 'successful B')
            self.assertFalse(source.exists())
