import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import imports, recipe_acceptance, recipe_state, source_review as s, update as u


BASE = 'a'*40
HEAD = 'b'*40
MERGE = 'c'*40
TREE = 'd'*40
IMAGE = 'ghcr.io/archlinux/archlinux@sha256:' + '1'*64
PRODUCER = {'run_id': '8', 'run_attempt': '2', 'head_sha': BASE,
            'path': '.github/workflows/candidate.yml'}


def evidence():
    return {'schema': 1, 'repository': 'owner/repo', 'base': BASE, 'head': HEAD,
            'tree': TREE, 'pr_number': 7, 'producer': dict(PRODUCER), 'image': IMAGE}


def scratch():
    root = Path.home()/'.local/state/omp/work/source-route-tests'
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class SourceEvidenceTests(unittest.TestCase):
    def test_completed_evidence_rejects_stale_sha_tree_and_failed_whole_run(self):
        run = {'id': 8, 'run_attempt': 2, 'head_sha': BASE, 'head_branch': 'main',
               'path': PRODUCER['path'], 'event': 'workflow_dispatch',
               'status': 'completed', 'conclusion': 'success'}
        with patch.object(u, 'repository', return_value='owner/repo'), patch.object(u, 'api', return_value=run):
            self.assertEqual(s.completed_evidence(evidence(), HEAD, TREE), evidence())
            for sha, tree in ((MERGE, TREE), (HEAD, BASE)):
                with self.subTest(sha=sha, tree=tree), self.assertRaisesRegex(ValueError, 'commit/tree'):
                    s.completed_evidence(evidence(), sha, tree)
            for field, bad in [('head_sha', HEAD), ('run_attempt', 1), ('path', '.github/workflows/build-package.yml'),
                               ('conclusion', 'failure'), ('status', 'in_progress'), ('head_branch', 'topic')]:
                before = run[field]
                run[field] = bad
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'producer'):
                    s.completed_evidence(evidence(), HEAD, TREE)
                run[field] = before

    def test_in_process_publication_tests_require_whole_run_success_for_later_reuse(self):
        record = evidence()
        record['pr_number'] = None
        record['producer']['path'] = '.github/workflows/publish.yml'
        record['execution'] = 'in-process-isolated-tests'
        run = {'id': 8, 'run_attempt': 2, 'head_sha': BASE, 'head_branch': 'main',
               'path': record['producer']['path'], 'event': 'push',
               'status': 'in_progress', 'conclusion': None}
        with patch.object(u, 'repository', return_value='owner/repo'), patch.object(u, 'api', return_value=run):
            with self.assertRaisesRegex(ValueError, 'producer'):
                s.completed_evidence(record, HEAD, TREE)
            run.update(status='completed', conclusion='failure')
            with self.assertRaisesRegex(ValueError, 'producer'):
                s.completed_evidence(record, HEAD, TREE)
            run.update(conclusion='success')
            self.assertEqual(s.completed_evidence(record, HEAD, TREE), record)
            record['producer']['path'] = '.github/workflows/verification.yml'
            with self.assertRaisesRegex(ValueError, 'issuer'):
                s.completed_evidence(record, HEAD, TREE)

    def test_protected_merge_reuses_only_identical_tested_tree_and_exact_pr(self):
        accepted = {'tree': {'sha': TREE}, 'parents': [{'sha': BASE}, {'sha': HEAD}]}
        pr = {'merged': True, 'merge_commit_sha': MERGE,
              'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
              'head': {'sha': HEAD, 'repo': {'full_name': 'owner/repo'}}}
        with patch.object(s, 'accepted_main', return_value=accepted), \
                patch.object(recipe_state, 'source_test_evidence', side_effect=lambda sha: evidence() if sha == HEAD else None), \
                patch.object(u, 'repository', return_value='owner/repo'), patch.object(u, 'api', return_value=pr), \
                patch.object(s, 'completed_evidence', return_value=evidence()) as checked, \
                patch.object(s, 'producer', side_effect=ValueError('fallback required')):
            self.assertEqual(s.verify_source_evidence(MERGE), evidence())
            checked.assert_called_once_with(evidence(), HEAD, TREE)
            for field, bad in [('merged', False), ('merge_commit_sha', BASE)]:
                before = pr[field]
                pr[field] = bad
                with self.subTest(field=field), patch.dict(os.environ, {'GITHUB_SHA': MERGE}), \
                        self.assertRaisesRegex(ValueError, 'fallback required'):
                    s.verify_source_evidence(MERGE)
                pr[field] = before

    def test_changed_merge_tree_runs_actual_accepted_tests_not_green_head(self):
        accepted = {'tree': {'sha': BASE}, 'parents': [{'sha': BASE}, {'sha': HEAD}]}
        pr = {'merged': True, 'merge_commit_sha': MERGE,
              'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
              'head': {'sha': HEAD, 'repo': {'full_name': 'owner/repo'}}}
        with scratch() as directory:
            baseline = Path(directory)/'baseline'
            root = Path(directory)/'accepted'
            baseline.mkdir()
            root.mkdir()
            (baseline/'build-image.txt').write_text(IMAGE)
            with patch.dict(os.environ, {'GITHUB_SHA': MERGE, 'ARCH_WORK': directory}), \
                    patch.object(s, 'accepted_main', return_value=accepted), \
                    patch.object(recipe_state, 'source_test_evidence', side_effect=lambda sha: evidence() if sha == HEAD else None), \
                    patch.object(u, 'repository', return_value='owner/repo'), patch.object(u, 'api', return_value=pr), \
                    patch.object(s, 'producer', return_value={**PRODUCER, 'head_sha': MERGE, 'path': '.github/workflows/publish.yml'}), \
                    patch.object(u, 'checkout_data', side_effect=[baseline, root]) as checkout, \
                    patch.object(u, 'run_candidate_tests') as tests, patch.object(s, 'save_evidence') as save:
                result = s.verify_source_evidence(MERGE)
                self.assertEqual(result['head'], MERGE)
                self.assertEqual(result['tree'], BASE)
                self.assertEqual(result['execution'], 'in-process-isolated-tests')
                save.assert_called_once_with(result)
                self.assertEqual(checkout.call_args_list[1].args[0], MERGE)
                tests.assert_called_once_with(root, IMAGE)

    def test_accepted_target_rejects_unprotected_or_unrelated_history(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}, clear=True), \
                patch.object(s, 'commit', return_value={}), patch.object(u, 'main_sha', return_value=MERGE), patch.object(u, 'api', return_value={'status': 'diverged'}):
            with self.assertRaisesRegex(ValueError, 'history'):
                s.accepted_main(HEAD)
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}, clear=True), \
                patch.object(s, 'commit', return_value={}), patch.object(u, 'main_sha', return_value=MERGE), patch.object(u, 'api', return_value={'protected': False}):
            with self.assertRaisesRegex(ValueError, 'protected'):
                s.accepted_main(MERGE)

    def test_package_scope_changes_stop_before_source_execution(self):
        with scratch() as directory:
            old, new = Path(directory)/'old', Path(directory)/'new'
            for root in (old, new):
                root.mkdir()
                (root/'packages.json').write_text('{}')
                (root/'recipes/example').mkdir(parents=True)
                (root/'recipes/example/PKGBUILD').write_text('pkgname=example')
            (new/'recipes/example/PKGBUILD').write_text('pkgname=other')
            pr = {'head': {'repo': {'full_name': 'owner/repo'}}}
            def checkout(sha, destination, pins):
                pins['example'] = BASE
                return old if sha == BASE else new
            with patch.object(u, 'pr_identity', return_value=(pr, BASE, HEAD)), \
                    patch.object(u, 'repository', return_value='owner/repo'), patch.object(s, 'producer', return_value=PRODUCER), \
                    patch.object(s, 'commit', return_value={'tree': {'sha': TREE}}), \
                    patch.object(u, 'checkout_data', side_effect=checkout), patch.object(u, 'policy_at', return_value={}), \
                    patch.object(recipe_acceptance, 'validate_bookkeeping', return_value=None), \
                    patch.object(imports, 'validate_admission', return_value=None), patch.object(u, 'validate_retirement', return_value=None), patch.object(u, 'run_candidate_tests') as tests:
                with self.assertRaisesRegex(ValueError, 'recipe content'):
                    s.validate_source(7, BASE, HEAD, Path(directory)/'evidence')
                tests.assert_not_called()

    def retirement_fixture(self, directory):
        old, new = Path(directory)/'old', Path(directory)/'new'
        for root, names in ((old, ['keep', 'retired']), (new, ['keep'])):
            root.mkdir()
            u.dump(root/'packages.json', {'schema': 1, 'packages': [
                {'pkgbase': name, 'architectures': ['x86_64']} for name in names]})
            (root/'.gitmodules').write_text(''.join(
                '[submodule "recipes/' + name + '"]\n'
                '\tpath = recipes/' + name + '\n'
                '\turl = https://github.com/owner/repo.git\n'
                '\tbranch = pkg/' + name + '\n' for name in names))
            for name in names:
                (root/'recipes'/name).mkdir(parents=True)
                (root/'recipes'/name/'PKGBUILD').write_text('pkgname=' + name)
                u.dump(root/'inputs'/f'{name}.json', {'version': '1-1'})
                u.dump(root/'upstream'/f'{name}.json', {'repository': 'unchanged'})
            u.dump(root/'acceptance/retired.json', {'historical': True})
            (root/'licenses').mkdir()
            (root/'licenses/retired.txt').write_text('retained legal asset')
            (root/'build-image.txt').write_text(IMAGE)
        return old, new, {'keep': BASE, 'retired': HEAD}, {'keep': BASE}

    def test_retirement_preserves_remaining_policies_pins_data_and_history(self):
        with scratch() as directory, patch.object(u, 'repository', return_value='owner/repo'):
            old, new, oldpins, newpins = self.retirement_fixture(directory)
            self.assertEqual(u.validate_retirement(old, new, oldpins, newpins), ['retired'])
            for path in ('recipes/keep/PKGBUILD', 'inputs/keep.json', 'upstream/keep.json',
                         'acceptance/retired.json', 'licenses/retired.txt'):
                target = new/path
                original = target.read_bytes()
                target.write_text('changed')
                with self.subTest(path=path), self.assertRaises(ValueError):
                    u.validate_retirement(old, new, oldpins, newpins)
                target.write_bytes(original)
            with self.assertRaises(ValueError):
                u.validate_retirement(old, new, oldpins, {'keep': MERGE})
            registry = u.load(new/'packages.json')
            registry['packages'][0]['architectures'] = ['aarch64']
            u.dump(new/'packages.json', registry)
            with self.assertRaises(ValueError):
                u.validate_retirement(old, new, oldpins, newpins)
            registry['packages'][0]['architectures'] = ['x86_64']
            registry['packages'].append({'pkgbase': 'added'})
            u.dump(new/'packages.json', registry)
            with self.assertRaises(ValueError):
                u.validate_retirement(old, new, oldpins, {**newpins, 'added': MERGE})

    def test_retirement_deletion_cannot_remove_signed_history_or_legal_assets(self):
        with scratch() as directory, patch.object(u, 'repository', return_value='owner/repo'):
            old, new, oldpins, newpins = self.retirement_fixture(directory)
            for path in ('acceptance/retired.json', 'licenses/retired.txt'):
                target = new/path
                original = target.read_bytes()
                target.unlink()
                with self.subTest(path=path), self.assertRaises(ValueError):
                    u.validate_retirement(old, new, oldpins, newpins)
                target.write_bytes(original)

    def test_retirement_runs_source_tests_without_package_work(self):
        with scratch() as directory, patch.object(u, 'repository', return_value='owner/repo'):
            old, new, oldpins, newpins = self.retirement_fixture(directory)
            pr = {'head': {'repo': {'full_name': 'owner/repo'}}}
            def checkout(sha, destination, pins):
                pins.update(oldpins if sha == BASE else newpins)
                return old if sha == BASE else new
            with patch.object(u, 'pr_identity', return_value=(pr, BASE, HEAD)), \
                    patch.object(s, 'producer', return_value=PRODUCER), \
                    patch.object(s, 'commit', return_value={'tree': {'sha': TREE}}), \
                    patch.object(u, 'checkout_data', side_effect=checkout), \
                    patch.object(recipe_acceptance, 'validate_bookkeeping', return_value=None), \
                    patch.object(u, 'run_candidate_tests') as tests, patch.object(s, 'save_evidence'), \
                    patch.object(u, 'api') as remote:
                result = s.validate_source(7, BASE, HEAD, Path(directory)/'evidence')
                self.assertEqual(result['head'], HEAD)
                tests.assert_called_once_with(new, IMAGE)
                remote.assert_not_called()

    def test_exact_bookkeeping_tests_head_without_package_worker(self):
        with scratch() as directory:
            old, new = Path(directory)/'old', Path(directory)/'new'
            old.mkdir()
            new.mkdir()
            (old/'build-image.txt').write_text(IMAGE)
            pr = {'head': {'repo': {'full_name': 'owner/repo'}}}
            with patch.object(u, 'pr_identity', return_value=(pr, BASE, HEAD)), \
                    patch.object(u, 'repository', return_value='owner/repo'), patch.object(s, 'producer', return_value=PRODUCER), \
                    patch.object(s, 'commit', return_value={'tree': {'sha': TREE}}), \
                    patch.object(u, 'checkout_data', side_effect=[old, new]), \
                    patch.object(recipe_acceptance, 'validate_bookkeeping', return_value={'accepted': MERGE}), \
                    patch.object(imports, 'validate_admission') as admission, patch.object(s, 'unchanged_packages') as unchanged, \
                    patch.object(u, 'run_candidate_tests') as tests, patch.object(s, 'save_evidence') as save, \
                    patch.object(u, 'api') as remote:
                result = s.validate_source(7, BASE, HEAD, Path(directory)/'evidence')
                self.assertEqual((result['head'], result['tree']), (HEAD, TREE))
                tests.assert_called_once_with(new, IMAGE)
                save.assert_called_once_with(result)
                admission.assert_not_called()
                unchanged.assert_not_called()
                remote.assert_not_called()

    def test_fork_or_wrong_controller_cannot_execute_source_tests(self):
        pr = {'head': {'repo': {'full_name': 'fork/repo'}}}
        with patch.object(u, 'pr_identity', return_value=(pr, BASE, HEAD)), \
                patch.object(u, 'repository', return_value='owner/repo'), patch.object(u, 'checkout_data') as checkout:
            with self.assertRaisesRegex(ValueError, 'trusted repository'):
                s.validate_source(7, BASE, HEAD, 'unused')
            checkout.assert_not_called()
        run = {'id': 8, 'run_attempt': 2, 'head_sha': HEAD, 'head_branch': 'main',
               'path': PRODUCER['path'], 'event': 'workflow_dispatch'}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo',
                                     'GITHUB_RUN_ID': '8', 'GITHUB_RUN_ATTEMPT': '2',
                                     'GITHUB_SHA': BASE, 'GITHUB_REF': 'refs/heads/main'}, clear=True), \
                patch.object(u, 'api', return_value=run):
            with self.assertRaisesRegex(ValueError, 'producer'):
                s.producer(BASE)

    def test_head_drift_after_source_tests_cannot_store_evidence(self):
        with scratch() as directory:
            old, new = Path(directory)/'old', Path(directory)/'new'
            old.mkdir()
            new.mkdir()
            (old/'build-image.txt').write_text(IMAGE)
            pr = {'head': {'repo': {'full_name': 'owner/repo'}}}
            with patch.object(u, 'pr_identity', side_effect=[(pr, BASE, HEAD), (pr, BASE, HEAD), ValueError('head drift')]), \
                    patch.object(u, 'repository', return_value='owner/repo'), patch.object(s, 'producer', return_value=PRODUCER), \
                    patch.object(s, 'commit', return_value={'tree': {'sha': TREE}}), \
                    patch.object(u, 'checkout_data', side_effect=[old, new]), patch.object(s, 'unchanged_packages'), \
                    patch.object(recipe_acceptance, 'validate_bookkeeping', return_value=None), \
                    patch.object(imports, 'validate_admission', return_value=None), patch.object(u, 'validate_retirement', return_value=None), patch.object(u, 'run_candidate_tests') as tests, \
                    patch.object(s, 'save_evidence') as save:
                with self.assertRaisesRegex(ValueError, 'head drift'):
                    s.validate_source(7, BASE, HEAD, Path(directory)/'evidence')
                tests.assert_called_once_with(new, IMAGE)
                save.assert_not_called()

    def test_source_sandbox_does_not_inherit_credentials_or_host_mounts(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'AWS_SECRET_ACCESS_KEY': 'secret'}), \
                patch.object(u.subprocess, 'check_output', return_value='container\n') as create, \
                patch.object(u.subprocess, 'run', side_effect=run):
            u.run_candidate_tests(Path('/candidate'), IMAGE)
        argv, options = create.call_args
        self.assertNotIn('GITHUB_TOKEN', options['env'])
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', options['env'])
        self.assertIn('--cap-drop=ALL', argv[0])
        self.assertIn('--security-opt=no-new-privileges', argv[0])
        self.assertNotIn('--privileged', argv[0])
        self.assertFalse(any(arg in {'-v', '--volume', '--mount'} for arg in argv[0]))
        self.assertEqual(calls[-1][0], ['docker', 'rm', '-f', 'container'])
