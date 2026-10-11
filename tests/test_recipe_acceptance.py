import copy
import os
from contextlib import ExitStack
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from tools import update

from tools import recipe_acceptance as acceptance
from tools import build_store


class AcceptanceInvariants(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'})
        environment.start()
        self.addCleanup(environment.stop)
        self.record = {'base': 'c'*40, 'recipe_base': 'b'*40,
                       'head': 'd'*40, 'recipe_tree': 'e'*40,
                       'head_branch': 'recipe-updates/demo/release'}
        self.merge = {'sha': 'a'*40, 'tree': {'sha': 'e'*40},
                      'parents': [{'sha': 'b'*40}, {'sha': 'd'*40}]}

    def test_merge_preserves_exact_reviewed_tree_and_both_parents(self):
        self.assertEqual(acceptance.verify_merge(self.record, self.merge), 'a'*40)
        for change in ({'tree': {'sha': 'f'*40}},
                       {'parents': [{'sha': 'd'*40}]},
                       {'parents': [{'sha': 'd'*40}, {'sha': 'b'*40}]},
                       {'parents': [{'sha': 'b'*40}, {'sha': 'd'*40}, {'sha': 'f'*40}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                acceptance.verify_merge(self.record, {**self.merge, **change})

    def test_same_accepted_merge_can_refresh_bookkeeping_after_main_advances(self):
        from tools import recipe_state
        A, C1, C2 = (value*40 for value in 'abc')
        stored = {}
        def save(kind, key, value):
            previous = stored.get((kind, key))
            if previous is not None and previous != value:
                raise ValueError('immutable controller receipt changed')
            stored[kind, key] = copy.deepcopy(value)
        def response(path, method='GET', value=None):
            if '/git/commits/' in path:
                return {'tree': {'sha': 'd'*40}}
            if path.endswith('/git/blobs'):
                return {'sha': 'e'*40}
            if path.endswith('/git/trees'):
                return {'sha': 'f'*40}
            if path.endswith('/git/commits'):
                return {'sha': '1'*40}
            if '/pulls?' in path:
                return []
            if path.endswith('/pulls'):
                return {'number': 8}
            return {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(recipe_state, 'save', side_effect=save))
            stack.enter_context(patch.object(update, 'api', side_effect=response))
            stack.enter_context(patch.object(update, 'repository', return_value='owner/repo'))
            stack.enter_context(patch.object(update, 'ref_head', return_value=None))
            stack.enter_context(patch.object(update, 'dispatch'))
            for control in (C1, C2):
                receipt = {'base': control, 'accepted': A, 'pkgbase': 'demo',
                           'lock': {'version': '2'}, 'provenance': {'watchers': []}}
                with patch.object(update, 'main_sha', return_value=control):
                    acceptance.write_bookkeeping(receipt)
        self.assertEqual(stored['acceptance', C1 + '-' + A]['base'], C1)
        self.assertEqual(stored['acceptance', C2 + '-' + A]['base'], C2)
        self.assertEqual(stored['acceptance', C1 + '-' + A]['accepted'],
                         stored['acceptance', C2 + '-' + A]['accepted'])

    def test_bookkeeping_reconstruction_retains_every_unrelated_leaf(self):
        old = {'packages.json': ('100644', 'blob', 'f'*40),
               'recipes/demo': ('160000', 'commit', 'b'*40),
               'inputs/demo.json': ('100644', 'blob', '1'*40)}
        receipt = {'pkgbase': 'demo', 'accepted': 'a'*40,
                   'lock': {'version': '2'}, 'provenance': {'watchers': []}}
        new = acceptance.expected_leaves(old, receipt)
        self.assertEqual(new['packages.json'], old['packages.json'])
        self.assertEqual(new['recipes/demo'], ('160000', 'commit', 'a'*40))
        self.assertEqual(set(new)-set(old), {'upstream/demo.json', 'acceptance/demo.json'})
        mutated = copy.deepcopy(receipt)
        mutated['lock']['version'] = '3'
        self.assertNotEqual(acceptance.expected_leaves(old, mutated)['inputs/demo.json'], new['inputs/demo.json'])

    def test_missing_frozen_proposal_fails_without_dispatching(self):
        from tools import recipe_candidates
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40},
              'head': {'sha': 'd'*40}}
        with patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_candidates, 'proposal_receipt', return_value=None), \
                patch.object(update, 'dispatch') as dispatch, \
                patch.object(update, 'checkout_data') as checkout:
            with self.assertRaisesRegex(ValueError, 'frozen proposal receipt'):
                acceptance.accepted_receipt(7, 'c'*40)
        dispatch.assert_not_called()
        checkout.assert_not_called()



    def test_bookkeeping_rejects_unrelated_code_even_with_valid_acceptance(self):
        C, H = 'c'*40, 'd'*40
        receipt = {'pkgbase': 'demo', 'base': C, 'accepted': 'a'*40,
                   'pr_number': 7, 'lock': {'version': '2'},
                   'provenance': {'watchers': []}}
        before = {'recipes/demo': ('160000', 'commit', 'b'*40),
                  'tools/update.py': ('100644', 'blob', '1'*40)}
        after = acceptance.expected_leaves(before, receipt)
        pr = {'base': {'ref': 'main'}}
        commit = {'message': 'Arch-Recipe-Acceptance: ' + 'a'*40,
                  'parents': [{'sha': C}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update.dump(root/'acceptance/demo.json', receipt)
            with ExitStack() as stack:
                for context in (
                    patch.object(acceptance, 'leaves', side_effect=lambda sha: before if sha == C else after),
                    patch.object(acceptance, 'accepted_receipt', return_value=receipt),
                    patch.object(update, 'api', return_value=commit),
                    patch.object(update, 'repository', return_value='owner/repo'),
                    patch.object(update, 'main_sha', return_value=C),
                ):
                    stack.enter_context(context)
                self.assertEqual(acceptance.validate_bookkeeping(pr, C, H, root, root), receipt)
                after['tools/update.py'] = ('100644', 'blob', '2'*40)
                with self.assertRaisesRegex(ValueError, 'exact accepted'):
                    acceptance.validate_bookkeeping(pr, C, H, root, root)

    def test_merged_legacy_adoption_requires_exact_accepted_main_tree_and_sources(self):
        from tools import recipe_state, recipes
        C, L, Q, M, P, B, H, T = (value*40 for value in 'c1234bde')
        oldlock, lock = {'version': '1', 'sources': []}, {'version': '2', 'sources': []}
        policy = {'pkgbase': 'demo', 'policy': 'exact', 'outputs': [{'name': 'demo', 'arch': 'x86_64'}]}
        original_load = update.load
        oldpro = {'watchers': [{'id': 'release', 'tag': '1'}]}
        provenance = {'watchers': [{'id': 'release', 'tag': '2'}]}
        proposal = {'schema': 1, 'repository': 'owner/repo', 'base': C,
                    'pkgbase': 'demo', 'proposal_origin': 'legacy-accepted',
                    'legacy_pr_number': 3, 'legacy_base': L, 'legacy_head': Q,
                    'legacy_accepted': M, 'recipe_base': B, 'recipe_head': H,
                    'recipe_tree': T, 'watcher_id': 'release', 'watcher_ids': ['release'],
                    'head_branch': 'recipe-updates/demo/release',
                    'expected_previous_control_pin': H,
                    'baseline_lock': oldlock, 'baseline_provenance': oldpro,
                    'lock': lock, 'provenance': provenance}
        before = {'recipes/demo': ('160000', 'commit', B),
                  'inputs/demo.json': ('100644', 'blob', acceptance.blob_sha(oldlock)),
                  'upstream/demo.json': ('100644', 'blob', acceptance.blob_sha(oldpro))}
        accepted = {**before, 'recipes/demo': ('160000', 'commit', H),
                    'inputs/demo.json': ('100644', 'blob', acceptance.blob_sha(lock)),
                    'upstream/demo.json': ('100644', 'blob', acceptance.blob_sha(provenance))}
        leafsets = {L: before, Q: accepted, M: dict(accepted)}
        receipt_digest = acceptance.digest({'base': L, 'tree': '9'*40,
                                            'watcher': 'release', 'pkgbase': 'demo'})
        pr = {'state': 'closed', 'merged': True, 'merged_at': 'now', 'title': 'Update demo',
              'merge_commit_sha': M, 'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
              'head': {'sha': Q, 'repo': {'full_name': 'owner/repo'}}}
        commits = {Q: {'tree': {'sha': '9'*40}, 'parents': [{'sha': L}],
                       'message': f'Update demo via release\n\nArch-Update-Receipt: {receipt_digest}\nArch-Update-Base: {L}'},
                   M: {'tree': {'sha': '9'*40}, 'parents': [{'sha': L}, {'sha': Q}]},
                   H: {'tree': {'sha': T}, 'parents': [{'sha': B}]}}
        A = 'a'*40
        native_pr = {'number': 7, 'base': {'sha': B, 'ref': 'pkg/demo'},
                     'head': {'sha': H, 'ref': 'recipe-updates/demo/release'},
                     'merge_commit_sha': A}
        commits[A] = {'sha': A, 'tree': {'sha': T}, 'parents': [{'sha': B}, {'sha': H}]}
        calls = []
        def response(path, method='GET', body=None):
            calls.append((path, method, body))
            if '/pulls?' in path:
                return []
            if path.endswith('/pulls') and method == 'POST':
                return {'number': 7, 'head': {'sha': H}}
            if path.endswith('/pulls/7'):
                return native_pr
            if '/pulls/' in path:
                return pr
            if '/statuses?' in path:
                return [{'context': 'arch-updater-receipt', 'state': 'success',
                         'description': receipt_digest,
                         'creator': {'login': 'github-actions[bot]', 'type': 'Bot'}}]
            return commits[path.rsplit('/', 1)[-1]]
        pins = {'demo': H}
        current_lock = lock
        current_pin = H
        with tempfile.TemporaryDirectory(prefix='legacy-adoption-', dir=acceptance.scratch_root()) as directory:
            current = Path(directory)/'current'
            current.mkdir()
            update.dump(current/'packages.json', {'schema': 1, 'packages': [policy]})
            def checkout(sha, destination, recipe_pins=None, selected=None):
                destination.mkdir()
                (destination/'build-image.txt').write_text('image')
                update.dump(destination/'packages.json', {'schema': 1, 'packages': [policy]})
                if recipe_pins is not None:
                    recipe_pins['demo'] = B if sha == L else current_pin if sha == C else H
                return destination
            def load(path):
                if Path(path).name == 'native-evidence.json':
                    return original_load(path)
                baseline = '/baseline/' in str(path) or '/old/' in str(path)
                if '/inputs/' in str(path):
                    return oldlock if baseline else current_lock if '/current/' in str(path) else lock
                return oldpro if baseline else provenance
            with ExitStack() as stack:
                for context in (
                    patch.object(update, 'api', side_effect=response),
                    patch.object(update, 'repository', return_value='owner/repo'),
                    patch.object(update, 'checkout_data', side_effect=checkout),
                    patch.object(update, 'load', side_effect=load),
                    patch.object(recipes, 'is_ancestor', return_value=True),
                    patch.object(acceptance, 'leaves', side_effect=lambda sha: leafsets[sha]),
                    patch.object(recipe_state, 'require_attestation'),
                ):
                    stack.enter_context(context)
                verified = acceptance.verify_legacy_adoption(proposal, C, current, pins)
                self.assertEqual(verified['baseline_lock'], oldlock)
                self.assertEqual(verified['predecessor_lock'], lock)
                self.assertEqual(verified['expected_previous_control_pin'], H)
                pins['demo'] = B
                with self.assertRaisesRegex(ValueError, 'current accepted legacy'):
                    acceptance.verify_legacy_adoption(proposal, C, current, pins)
                pins['demo'] = H
                current_lock = oldlock
                with self.assertRaisesRegex(ValueError, 'current accepted legacy'):
                    acceptance.verify_legacy_adoption(proposal, C, current, pins)
                current_lock = lock
                leafsets[M] = {**accepted, 'unrelated': ('100644', 'blob', '8'*40)}
                with self.assertRaisesRegex(ValueError, 'legacy merge'):
                    acceptance.verify_legacy_adoption(proposal, C, current, pins)
                leafsets[M] = dict(accepted)
                saved = {}
                with ExitStack() as migration_stack:
                    for context in (
                        patch.object(update, 'main_sha', return_value=C),
                        patch.object(recipe_state, 'protected_branch'),
                        patch.object(update, 'ref_head', side_effect=lambda branch: B if branch == 'pkg/demo' else H),
                        patch.object(update, 'validate_source_policy'),
                        patch.object(update, 'verify_provenance'),
                        patch.object(recipe_state, 'load_optional', return_value=None),
                        patch.object(recipe_state, 'save', side_effect=lambda kind, key, value: saved.update({(kind, key): value})),
                        patch.object(recipe_state, 'attest'),
                        patch.object(recipe_state, 'recipe_identity'),
                        patch.object(update, 'dispatch'),
                    ):
                        migration_stack.enter_context(context)
                    replacement = acceptance.migrate_existing(3)
                    self.assertEqual(replacement['number'], 7)
                    frozen = saved[('proposal', H)]
                    self.assertEqual(frozen['proposal_origin'], 'legacy-accepted')
                    self.assertEqual(frozen['legacy_accepted'], M)
                    self.assertEqual(frozen['baseline_lock'], oldlock)
                    self.assertEqual(frozen['lock'], lock)
                    self.assertEqual(frozen['expected_previous_control_pin'], H)
                    writes = [(path, body) for path, method, body in calls if method != 'GET']
                    self.assertEqual([path for path, body in writes], [update.route('/pulls')])
                    self.assertEqual((writes[0][1]['head'], writes[0][1]['base']),
                                     ('recipe-updates/demo/release', 'pkg/demo'))
                    # A moving enrolled git branch may change the recipe lock
                    # while the enrollment provenance remains exactly unchanged.
                    provenance = oldpro
                    current_lock, current_pin = oldlock, B
                    pr.update(state='open', merged_at=None)
                    leafsets[Q] = {**accepted, 'upstream/demo.json': before['upstream/demo.json']}
                    acceptance.migrate_existing(3)
                    open_frozen = saved[('proposal', H)]
                    self.assertEqual(open_frozen['watcher_id'], 'release')
                    self.assertEqual(open_frozen['provenance'], oldpro)
                    self.assertEqual(open_frozen['proposal_origin'], 'watcher')
                    provenance = {'watchers': [{'id': 'release', 'tag': '2'}]}
                    current_lock, current_pin = lock, H
                    pr.update(state='closed', merged_at='now')
                    leafsets[Q] = dict(accepted)

    def test_retirement_acceptance_deletion_requires_ordinary_code_review(self):
        before = {'acceptance/demo.json': ('100644', 'blob', '1'*40),
                  'recipes/demo': ('160000', 'commit', 'b'*40)}
        with patch.object(acceptance, 'leaves', side_effect=[before, {}]), \
                patch.object(update, 'api', return_value={'message': 'Retire demo'}), \
                patch.object(update, 'repository', return_value='owner/repo'), \
                patch.object(update, 'load') as load:
            self.assertIsNone(acceptance.validate_bookkeeping(
                {'head': {'ref': 'retire-demo'}, 'base': {'ref': 'main'}},
                'c'*40, 'd'*40, '.', '.'))
            load.assert_not_called()

    def test_legacy_migration_accepts_stale_pr_base_only_with_compatible_current_predecessor(self):
        from tools import recipe_state, recipes
        C, L, H, B, R = (value*40 for value in 'c1dbe')
        pr = {'state': 'open', 'base': {'ref': 'main', 'sha': L,
                                      'repo': {'full_name': 'owner/repo'}},
              'head': {'sha': H, 'repo': {'full_name': 'owner/repo'}}}
        def response(path):
            if '/pulls?' in path:
                return []
            if '/pulls/' in path:
                return pr
            return {'parents': [{'sha': L}], 'message': 'Update demo via release'}
        diverged = False
        def checkout(sha, destination, pins):
            pins['demo'] = R if sha == H else ('x'*40 if sha == C and diverged else B)
            return destination
        with ExitStack() as stack:
            for context in (
                patch.object(update, 'api', side_effect=response),
                patch.object(update, 'repository', return_value='owner/repo'),
                patch.object(update, 'main_sha', return_value=C),
                patch.object(recipes, 'is_ancestor', return_value=True),
                patch.object(update, 'checkout_data', side_effect=checkout),
                patch.object(recipe_state, 'protected_branch'),
                patch.object(update, 'ref_head', return_value=B),
                patch.object(update, 'policy_at', return_value={'demo': {}}),
                patch.object(update, 'load', return_value={'watchers': [{'id': 'release'}]}),
                patch.object(update, 'validate_source_policy', side_effect=RuntimeError('compatible migration reached native source validation')),
            ):
                stack.enter_context(context)
            with self.assertRaisesRegex(RuntimeError, 'reached native source'):
                acceptance.migrate_existing(3)
            diverged = True
            with self.assertRaisesRegex(ValueError, 'predecessor pin, policy or source state diverged'):
                acceptance.migrate_existing(3)

    def test_arbitrary_main_code_does_not_receive_bookkeeping_authority(self):
        before = {'tools/update.py': ('100644', 'blob', '1'*40)}
        after = {'tools/update.py': ('100644', 'blob', '2'*40)}
        with patch.object(acceptance, 'leaves', side_effect=[before, after]), \
                patch.object(update, 'api', return_value={'message': 'Change controller'}), \
                patch.object(update, 'repository', return_value='owner/repo'):
            self.assertIsNone(acceptance.validate_bookkeeping({}, 'c'*40, 'd'*40, '.', '.'))

    def test_legacy_migration_receipt_requires_exact_bot_owned_message_and_parent(self):
        base = 'c'*40
        tree = 'd'*40
        receipt = acceptance.digest({'base': base, 'tree': tree,
                                     'watcher': 'release', 'pkgbase': 'demo'})
        commit = {'tree': {'sha': tree}, 'parents': [{'sha': base}],
                  'message': f'Update demo via release\n\nArch-Update-Receipt: {receipt}\nArch-Update-Base: {base}'}
        statuses = [{'context': 'arch-updater-receipt', 'state': 'success',
                     'description': receipt, 'creator': {'login': 'github-actions[bot]', 'type': 'Bot'}}]
        acceptance.verify_legacy_receipt(commit, base, 'demo', 'release', statuses)
        for changes in ({'parents': [{'sha': 'f'*40}]},
                        {'message': commit['message'] + '\nextra'}):
            with self.assertRaises(ValueError):
                acceptance.verify_legacy_receipt({**commit, **changes}, base, 'demo', 'release', statuses)
        with self.assertRaises(ValueError):
            acceptance.verify_legacy_receipt(commit, base, 'demo', 'release',
                                             [{**statuses[0], 'creator': {'login': 'human', 'type': 'User'}}])



    def test_stale_bookkeeping_shape_rejects_unrelated_work_before_replacement_or_close(self):
        P, A = 'c'*40, 'a'*40
        supplied = {'pkgbase': 'demo', 'base': P, 'accepted': A,
                    'lock': {'version': '2'}, 'provenance': {'watchers': []}}
        pr = {'base': {'ref': 'main'}, 'head': {'ref': f'bookkeeping/demo/{A}/{P}'}}
        commit = {'parents': [{'sha': P}],
                  'message': 'Accept demo recipe\n\nArch-Recipe-Acceptance: ' + A}
        before = {'tools/update.py': ('100644', 'blob', '1'*40)}
        after = acceptance.expected_leaves(before, supplied)
        self.assertEqual(acceptance.verify_bookkeeping_shape(pr, commit, supplied, before, after), P)
        after['tools/update.py'] = ('100644', 'blob', '2'*40)
        with self.assertRaises(ValueError):
            acceptance.verify_bookkeeping_shape(pr, commit, supplied, before, after)

    def test_pagination_recovers_later_pages(self):
        calls = []
        def fetch(page):
            calls.append(page)
            return [{'number': page}] * 100 if page == 1 else [{'number': 101}]
        rows = list(acceptance.paginate(fetch))
        self.assertEqual(calls, [1, 2])
        self.assertEqual(rows[-1]['number'], 101)


if __name__ == '__main__':
    unittest.main()


class SuccessfulOutputAcceptance(unittest.TestCase):
    def fixture(self, dynamic=False):
        srcinfo = 'pkgbase = demo\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\npkgname = demo\n'
        lock = {'schema': 1, 'version': '1-1', 'sources': []}
        package = {'pkgbase': 'demo', 'recipe_commit': 'd'*40, 'input_digest': 'f'*64,
                   'lock': lock, 'expected_srcinfo': srcinfo, 'policy': {'outputs': [{'name': 'demo', 'arch': 'x86_64'}]}}
        candidate = {'schema': 1, 'repository': 'owner/repo', 'base': 'c'*40, 'head': 'd'*40,
                     'recipe_pins': {'demo': 'd'*40}, 'previous_recipe_pins': {'demo': 'b'*40},
                     'image': 'pinned', 'harness_sha': 'f'*64, 'packages': [package]}
        actual = srcinfo.replace('pkgver = 1', 'pkgver = 2') if dynamic else srcinfo
        evidence = {key: value for key, value in candidate.items() if key != 'packages'}
        evidence['packages'] = [{'pkgbase': 'demo', 'recipe_commit': 'd'*40, 'input_digest': 'e'*64, 'request_input_digest': 'f'*64,
                                 'source_lock': {'schema': 1, 'version': '2-1' if dynamic else '1-1', 'sources': []},
                                 'metadata': {'srcinfo': actual, 'dynamic_pkgver': dynamic},
                                 'files': [{'name': 'demo', 'arch': 'x86_64', 'version': '2-1' if dynamic else '1-1',
                                            'filename': 'demo.pkg.tar.zst'}]}]
        return candidate, evidence

    def test_dynamic_actual_version_is_accepted_without_package_hashes(self):
        candidate, evidence = self.fixture(dynamic=True)
        acceptance.verify_native_evidence(candidate, evidence)
        changed = copy.deepcopy(evidence)
        changed['packages'][0]['metadata']['srcinfo'] += '\tdepends = undeclared\n'
        with self.assertRaisesRegex(ValueError, 'frozen candidate'):
            acceptance.verify_native_evidence(candidate, changed)

    def test_actual_dynamic_pkgrel_bump_must_advance_prior_actual_version(self):
        candidate, evidence = self.fixture(dynamic=True)
        candidate['predecessor_lock'] = {'version': '2-1', 'sources': []}
        candidate['proposal_origin'] = 'manual'
        candidate['packages'][0]['expected_srcinfo'] = candidate['packages'][0]['expected_srcinfo'].replace('pkgrel = 1', 'pkgrel = 2')
        candidate['packages'][0]['lock']['version'] = '1-2'
        output = evidence['packages'][0]
        output['metadata']['srcinfo'] = output['metadata']['srcinfo'].replace('pkgrel = 1', 'pkgrel = 2')
        output['source_lock']['version'] = '2-2'
        output['files'][0]['version'] = '2-2'
        acceptance.verify_native_evidence(candidate, evidence)
        candidate['predecessor_lock']['version'] = '2-2'
        with self.assertRaisesRegex(ValueError, 'advance accepted version'):
            acceptance.verify_native_evidence(candidate, evidence)
        candidate['predecessor_lock']['version'] = '3-1'
        with self.assertRaisesRegex(ValueError, 'advance accepted version'):
            acceptance.verify_native_evidence(candidate, evidence)

    def test_actual_version_progression_preserves_epoch_ordering(self):
        candidate, evidence = self.fixture(dynamic=True)
        candidate['proposal_origin'] = 'manual'
        candidate['predecessor_lock'] = {'version': '1:1-1', 'sources': []}
        with self.assertRaisesRegex(ValueError, 'advance accepted version'):
            acceptance.verify_native_evidence(candidate, evidence)
        candidate['packages'][0]['expected_srcinfo'] = candidate['packages'][0]['expected_srcinfo'].replace('pkgrel = 1', 'epoch = 2\n\tpkgrel = 1')
        output = evidence['packages'][0]
        output['metadata']['srcinfo'] = output['metadata']['srcinfo'].replace('pkgrel = 1', 'epoch = 2\n\tpkgrel = 1')
        output['source_lock']['version'] = '2:2-1'
        output['files'][0]['version'] = '2:2-1'
        candidate['predecessor_lock']['version'] = '1:99-9'
        acceptance.verify_native_evidence(candidate, evidence)

    def test_actual_git_commits_support_both_source_object_formats(self):
        candidate, evidence = self.fixture()
        planned = {'id': 'source', 'kind': 'git', 'source': 'git+https://example.test/repo#branch=main',
                   'commit': 'a'*40, 'git_context': {'object_format': 'sha1'}}
        candidate['packages'][0]['lock']['sources'] = [planned]
        evidence['packages'][0]['source_lock']['sources'] = [copy.deepcopy(planned)]
        actual = evidence['packages'][0]['source_lock']['sources'][0]
        for length, object_format in ((40, 'sha1'), (64, 'sha256')):
            with self.subTest(object_format=object_format):
                actual['commit'] = 'b'*length
                actual['git_context'] = {'object_format': object_format}
                acceptance.verify_native_evidence(candidate, evidence)
        actual['commit'] = 'b'*63
        with self.assertRaisesRegex(ValueError, 'full commit'):
            acceptance.verify_native_evidence(candidate, evidence)


    def test_static_version_mismatch_is_rejected(self):
        candidate, evidence = self.fixture(dynamic=True)
        evidence['packages'][0]['metadata']['dynamic_pkgver'] = False
        with self.assertRaisesRegex(ValueError, 'frozen candidate'):
            acceptance.verify_native_evidence(candidate, evidence)

    def test_direct_request_acceptance_ignores_controller_movement(self):
        from tools import recipe_candidates, recipe_state
        candidate, evidence = self.fixture(dynamic=True)
        candidate.update(request_id='demo-request', recipe_base='b'*40, pkgbase='demo', pr_number=7,
                         head_branch='recipe-updates/demo', recipe_tree='e'*40)
        evidence.update(run_id='11', run_attempt='1')
        descriptor = {'record': candidate, 'evidence': evidence, 'producer': {'run_id': '11', 'run_attempt': '1'}}
        pr = {'number': 7, 'base': {'ref': 'pkg/demo', 'sha': 'b'*40},
              'head': {'sha': 'd'*40, 'ref': 'recipe-updates/demo'}}
        with patch.object(recipe_state, 'pending_build', return_value={'head': 'd'*40, 'state': 'success', 'request_id': 'demo-request'}), \
                patch.object(recipe_state, 'build_request', return_value=candidate), \
                patch.object(recipe_state, 'StateSnapshot') as snapshot, \
                patch.object(recipe_state, 'assert_recipe_identity'), \
                patch.object(recipe_candidates, 'verify_result'), \
                patch.object(recipe_state, 'keys', side_effect=AssertionError('no historical lookup')):
            snapshot.return_value.load.return_value = descriptor
            selected, actual, runs, stored = acceptance.acceptance_candidate(pr, '9'*40, {}, candidate['packages'][0]['policy'])
            self.assertEqual(selected['base'], 'c'*40)
            self.assertEqual(actual['packages'][0]['source_lock']['version'], '2-1')
            self.assertEqual(stored, descriptor)

    def test_behind_main_pr_does_not_block_actionable_recipe_in_sweep(self):
        from tools import imports, recipe_state
        source = {'number': 1, 'state': 'open', 'base': {'ref': 'main', 'sha': 'b'*40},
                  'head': {'ref': 'feature/controller', 'sha': 'a'*40}}
        recipe = {'number': 2, 'state': 'open', 'base': {'ref': 'pkg/demo', 'sha': 'e'*40},
                  'head': {'ref': 'recipe-updates/demo', 'sha': 'd'*40}}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}), \
                patch.object(update, 'main_sha', return_value='c'*40), \
                patch.object(update, 'control_checkout', return_value=('c'*40, None)), \
                patch.object(imports, 'policies', return_value={'demo': {}}), \
                patch.object(recipe_state, 'StateSnapshot') as snapshot, \
                patch.object(recipe_state, 'pending_build', return_value=None), \
                patch.object(update, 'open_pr_snapshot', return_value=[source, recipe]), \
                patch.object(update, 'api', return_value={}) as api:
            snapshot.return_value.load_many.side_effect = [{('pending', 'demo'): None}, {}]
            self.assertEqual(acceptance.reconcile(), [])
            api.assert_called_once_with(
                update.route('/actions/workflows/candidate.yml/dispatches'), 'POST',
                {'ref': 'main', 'inputs': {'pr_number': '2', 'expected_head': 'd'*40}})

    def test_failed_pending_cannot_accept_even_if_output_exists(self):
        from tools import recipe_state
        pr = {'number': 7, 'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'head': {'sha': 'd'*40}}
        with patch.object(recipe_state, 'StateSnapshot'), patch.object(recipe_state, 'pending_build', return_value={'head': 'd'*40, 'state': 'failure'}):
            with self.assertRaisesRegex(ValueError, 'successful exact-head'):
                acceptance.acceptance_candidate(pr, 'c'*40, {}, {})

    def test_bookkeeping_advances_inputs_to_actual_dynamic_output_only_after_exact_merge(self):
        from tools import imports, recipe_candidates
        candidate, evidence = self.fixture(dynamic=True)
        policy = candidate['packages'][0]['policy']
        predecessor = candidate['packages'][0]['lock']
        provenance = {'watchers': []}
        candidate.update(pkgbase='demo', pr_number=7, recipe_base='b'*40, recipe_tree='e'*40,
                         head_branch='feature/demo', proposal_origin='manual', watcher_id=None, watcher_ids=[],
                         receipt_id='f'*64, predecessor_lock=predecessor, predecessor_provenance=provenance,
                         provenance=provenance)
        descriptor = {'record': candidate, 'evidence': evidence}
        proposal = {'base': 'c'*40, 'proposal_origin': 'manual'}
        pr = {'number': 7, 'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'head': {'sha': 'd'*40}, 'merge_commit_sha': 'a'*40}
        merge = {'sha': 'a'*40, 'tree': {'sha': 'e'*40}, 'parents': [{'sha': 'b'*40}, {'sha': 'd'*40}]}
        def checkout(sha, destination, pins):
            pins['demo'] = 'b'*40
            update.dump(destination/'inputs/demo.json', predecessor)
            update.dump(destination/'upstream/demo.json', provenance)
            return destination
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'}), \
                patch.object(update, 'api', side_effect=[pr, merge]), patch.object(update, 'main_sha', return_value='c'*40), \
                patch.object(update, 'checkout_data', side_effect=checkout), patch.object(update, 'ref_head', return_value='a'*40), \
                patch.object(imports, 'policies', return_value={'demo': policy}), \
                patch.object(imports, 'registry', return_value=({}, {'demo': policy}, {})), \
                patch.object(recipe_candidates, 'proposal_receipt', return_value=proposal), \
                patch.object(acceptance, 'required_checks'), \
                patch.object(acceptance, 'acceptance_candidate', return_value=(candidate, evidence, {}, descriptor)):
            receipt = acceptance.accepted_receipt(7, 'c'*40)
            self.assertEqual(receipt['lock']['version'], '2-1')
            self.assertEqual(receipt['predecessor_lock']['version'], '1-1')
            self.assertEqual(receipt['accepted'], 'a'*40)
            self.assertEqual(receipt['actual_srcinfo'], evidence['packages'][0]['metadata']['srcinfo'])
            actual_leaf = acceptance.expected_leaves({}, receipt)['inputs/demo.json']
            self.assertNotEqual(actual_leaf[2], acceptance.blob_sha(predecessor))
