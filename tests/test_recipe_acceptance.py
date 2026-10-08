import copy
import os
from contextlib import ExitStack
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from tools import update

from tools import recipe_acceptance as acceptance


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

    def test_evidence_cannot_transplant_identity_or_approval(self):
        candidate = {**self.record, 'kind': 'recipe', 'mechanical': False}
        digest = acceptance.digest(candidate)
        built = {'candidate_digest': digest, 'native_verified': True}
        approved = {'candidate_digest': digest, 'review_environment': 'recipe-review'}
        acceptance.verify_evidence(candidate, built, approved)
        with self.assertRaises(ValueError):
            acceptance.verify_evidence({**candidate, 'base': 'f'*40}, built, approved)
        with self.assertRaises(ValueError):
            acceptance.verify_evidence(candidate, built, None)
        with self.assertRaises(ValueError):
            acceptance.verify_evidence(candidate, {**built, 'native_verified': False}, approved)
        with self.assertRaises(ValueError):
            acceptance.verify_evidence(candidate, built, {**approved, 'review_environment': 'code-review'})

    def test_missing_frozen_proposal_redispatches_without_accepting(self):
        from tools import recipe_candidates
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40},
              'head': {'sha': 'd'*40}}
        with patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_candidates, 'proposal_receipt', return_value=None), \
                patch.object(update, 'dispatch') as dispatch, \
                patch.object(update, 'checkout_data') as checkout:
            with self.assertRaisesRegex(ValueError, 'frozen proposal receipt'):
                acceptance.accepted_receipt(7, 'c'*40)
        dispatch.assert_called_once_with(7)
        checkout.assert_not_called()

    def test_manual_acceptance_recovers_reviewed_intermediate_control_without_inventing_watcher(self):
        from tools import recipe_candidates, recipe_state
        C0, C1, C2, B, H, T, A = (value*40 for value in '123bdea')
        branch = 'recipe-updates/demo/dependency-change'
        policy = {'outputs': []}
        lock = {'version': '1-2', 'sources': []}
        provenance = {'watchers': []}
        candidate = {'schema': 1, 'kind': 'recipe', 'repository': 'owner/repo',
                     'base': C1, 'recipe_base': B, 'head': H, 'recipe_tree': T,
                     'pkgbase': 'demo', 'pr_number': 7, 'receipt_id': 'f'*64,
                     'head_branch': branch, 'proposal_origin': 'manual',
                     'watcher_id': None, 'watcher_ids': [], 'mechanical': False,
                     'image': 'image', 'harness_sha': 'harness', 'control_digest': 'controller',
                     'predecessor_lock': lock, 'predecessor_provenance': provenance,
                     'provenance': provenance,
                     'packages': [{'pkgbase': 'demo', 'policy': policy, 'lock': lock,
                                   'expected_srcinfo': 'dependency = new\n'}]}
        key = C1 + '-' + B + '-' + H
        built = {'candidate_digest': acceptance.digest(candidate), 'native_verified': True}
        stable = {**candidate, 'candidate_digest': acceptance.digest(candidate),
                  'review_environment': 'recipe-review'}
        proof = {**stable, 'run_id': '42', 'run_attempt': '1'}
        records = {('candidate', key): candidate, ('built', key): built,
                   ('approved', key): stable, ('approved', key + '-42-1'): proof}
        pr = {'number': 7, 'base': {'ref': 'pkg/demo', 'sha': B},
              'head': {'ref': branch, 'sha': H}, 'merge_commit_sha': A}
        run = {'head_sha': C1, 'head_branch': 'main', 'event': 'workflow_dispatch',
               'path': '.github/workflows/candidate.yml', 'run_attempt': 1,
               'status': 'completed', 'conclusion': 'success',
               'display_title': 'Candidate PR 7 head ' + H}
        environment = {'id': 9, 'can_admins_bypass': False,
                       'protection_rules': [{'type': 'required_reviewers', 'reviewers': [1]}]}
        def response(path):
            if '/pulls/' in path:
                return pr
            if '/git/commits/' in path:
                return {'sha': A, 'tree': {'sha': T}, 'parents': [{'sha': B}, {'sha': H}]}
            if path.endswith('/environments/recipe-review'):
                return environment
            if path.endswith('/approvals'):
                return [{'state': 'approved', 'environments': [{'id': 9, 'name': 'recipe-review'}]}]
            return run
        def checkout(sha, destination, pins):
            destination.mkdir()
            (destination/'build-image.txt').write_text('image')
            pins['demo'] = B
            return destination
        def extract(archive, destination):
            destination.mkdir()
            (destination/'.SRCINFO').write_text('dependency = new\n')
            return destination
        read_json = update.load
        def load(path):
            if '/inputs/' in str(path):
                return lock
            if '/upstream/' in str(path):
                return provenance
            return read_json(path)
        with ExitStack() as stack:
            for context in (
                patch.object(update, 'api', side_effect=response),
                patch.object(update, 'repository', return_value='owner/repo'),
                patch.object(update, 'main_sha', return_value=C2),
                patch.object(update, 'ref_head', return_value=A),
                patch.object(update, 'checkout_data', side_effect=checkout),
                patch.object(update, 'policy_at', return_value={'demo': policy}),
                patch.object(update, 'harness_digest', return_value='harness'),
                patch.object(recipe_state, 'control_digest', return_value='controller'),
                patch.object(update, 'load', side_effect=load),
                patch.object(update, 'download'),
                patch.object(update, 'extract_tree', side_effect=extract),
                patch.object(update, 'validate_source_policy'),
                patch.object(update, 'native_probe', return_value={'sources': [], 'version': '1-2', 'srcinfo': 'dependency = new\n'}),
                patch.object(recipe_candidates, 'proposal_receipt', return_value={'base': C0}),
                patch.object(recipe_state, 'load_optional', side_effect=lambda kind, key: records.get((kind, key))),
                patch.object(recipe_state, 'load', side_effect=lambda kind, key: records[(kind, key)]),
                patch.object(recipe_state, 'keys', side_effect=lambda kind, prefix: [key for (record_kind, key) in records if record_kind == kind and key.startswith(prefix)]),
                patch.object(recipe_state, 'assert_recipe_identity'),
                patch.object(recipe_state, 'require_attestation'),
                patch.object(acceptance, 'verify_native_authority', return_value={'run_id': '41'}),
                patch.object(acceptance, 'required_checks'),
            ):
                stack.enter_context(context)
            verify_provenance = stack.enter_context(patch.object(update, 'verify_provenance'))
            dispatch = stack.enter_context(patch.object(update, 'dispatch'))
            receipt = acceptance.accepted_receipt(7)
            self.assertEqual((receipt['base'], receipt['candidate_base'], receipt['proposal_base']), (C2, C1, C0))
            self.assertEqual((receipt['recipe_base'], receipt['head'], receipt['recipe_tree'], receipt['accepted']), (B, H, T, A))
            self.assertEqual((receipt['head_branch'], receipt['proposal_origin'], receipt['watcher_id'], receipt['watcher_ids']), (branch, 'manual', None, []))
            self.assertEqual(receipt['approved_digest'], acceptance.digest(proof))
            self.assertEqual(receipt['lock'], lock)
            self.assertEqual(receipt['provenance'], provenance)
            self.assertEqual(acceptance.expected_leaves({}, receipt)['acceptance/demo.json'],
                             ('100644', 'blob', acceptance.blob_sha(receipt)))
            verify_provenance.assert_called_once_with(provenance, provenance, lock, policy, None)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                update.dump(root/'acceptance/demo.json', receipt)
                before = {'recipes/demo': ('160000', 'commit', B)}
                after = acceptance.expected_leaves(before, receipt)
                bookkeeping = {'base': {'ref': 'main'}, 'head': {'ref': 'bookkeeping/demo/' + A + '/' + C2}}
                commit = {'parents': [{'sha': C2}], 'message': 'Arch-Recipe-Acceptance: ' + A}
                with patch.object(acceptance, 'leaves', side_effect=[before, after]), \
                        patch.object(acceptance, 'accepted_receipt', return_value=receipt), \
                        patch.object(update, 'api', return_value=commit):
                    self.assertEqual(acceptance.validate_bookkeeping(bookkeeping, C2, 'f'*40, root, root), receipt)
            dispatch.assert_not_called()
            environment['protection_rules'] = []
            with self.assertRaisesRegex(ValueError, 'human approval proof'):
                acceptance.accepted_receipt(7)
            environment['protection_rules'] = [{'type': 'required_reviewers', 'reviewers': [1]}]
            del records[('built', key)]
            with self.assertRaisesRegex(ValueError, 'revalidation dispatched'):
                acceptance.accepted_receipt(7)
            dispatch.assert_called_once_with(7)

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
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory)/'current'
            def checkout(sha, destination, recipe_pins=None):
                destination.mkdir()
                (destination/'build-image.txt').write_text('image')
                if recipe_pins is not None:
                    recipe_pins['demo'] = B if sha == L else current_pin if sha == C else H
                return destination
            def load(path):
                baseline = '/baseline/' in str(path) or '/old/' in str(path)
                if '/inputs/' in str(path):
                    return oldlock if baseline else current_lock if '/current/' in str(path) else lock
                return oldpro if baseline else provenance
            with ExitStack() as stack:
                for context in (
                    patch.object(update, 'api', side_effect=response),
                    patch.object(update, 'repository', return_value='owner/repo'),
                    patch.object(update, 'checkout_data', side_effect=checkout),
                    patch.object(update, 'policy_at', return_value={'demo': {'policy': 'exact'}}),
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
                from tools import recipe_candidates
                record = {'schema': 1, 'kind': 'recipe', 'repository': 'owner/repo',
                          'base': C, 'recipe_base': B, 'head': H, 'recipe_tree': T,
                          'pr_number': 7, 'pkgbase': 'demo', 'receipt_id': acceptance.digest(frozen),
                          'head_branch': frozen['head_branch'], 'proposal_origin': 'legacy-accepted',
                          'watcher_id': 'release', 'watcher_ids': ['release'], 'mechanical': True,
                          'previous_control_pin': H, 'baseline_lock': oldlock, 'baseline_provenance': oldpro,
                          'predecessor_lock': lock, 'predecessor_provenance': provenance,
                          'provenance': provenance, 'image': 'image', 'harness_sha': 'harness',
                          'control_digest': 'controller',
                          'packages': [{'pkgbase': 'demo', 'policy': {'policy': 'exact'}, 'lock': lock,
                                        'expected_srcinfo': 'pkgver = 2\n'}]}
                key = C + '-' + B + '-' + H
                built = {'candidate_digest': acceptance.digest(record), 'native_verified': True}
                records = {('candidate', key): record, ('built', key): built}
                def extract(archive, destination):
                    destination.mkdir()
                    (destination/'.SRCINFO').write_text('pkgver = 2\n')
                    return destination
                with ExitStack() as acceptance_stack:
                    for context in (
                        patch.object(update, 'main_sha', return_value=C),
                        patch.object(recipe_candidates, 'proposal_receipt', return_value=frozen),
                        patch.object(recipe_state, 'keys', return_value=[key]),
                        patch.object(recipe_state, 'load_optional', side_effect=lambda kind, key: records.get((kind, key))),
                        patch.object(recipe_state, 'assert_recipe_identity'),
                        patch.object(update, 'harness_digest', return_value='harness'),
                        patch.object(recipe_state, 'control_digest', return_value='controller'),
                        patch.object(acceptance, 'verify_native_authority', return_value={'run_id': '42'}),
                        patch.object(acceptance, 'required_checks'),
                        patch.object(update, 'ref_head', return_value=A),
                        patch.object(update, 'download'),
                        patch.object(update, 'extract_tree', side_effect=extract),
                        patch.object(update, 'validate_source_policy'),
                        patch.object(update, 'verify_provenance'),
                        patch.object(update, 'native_probe', return_value={'sources': [], 'version': '2', 'srcinfo': 'pkgver = 2\n'}),
                    ):
                        acceptance_stack.enter_context(context)
                    receipt = acceptance.accepted_receipt(7)
                    self.assertEqual((receipt['previous_control_pin'], receipt['accepted']), (H, A))
                    self.assertEqual(receipt['predecessor_lock'], receipt['lock'])
                    self.assertEqual(receipt['predecessor_provenance'], receipt['provenance'])
                    self.assertEqual(acceptance.expected_leaves(accepted, receipt)['recipes/demo'],
                                     ('160000', 'commit', A))
                    self.assertIsNone(receipt['approved_digest'])

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

    def test_human_approval_requires_real_successful_protected_workflow(self):
        candidate = {'base': 'c'*40, 'pr_number': 7, 'head': 'd'*40}
        approved = {'run_id': '42', 'run_attempt': '1'}
        run = {'head_sha': 'c'*40, 'head_branch': 'main',
               'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml',
               'run_attempt': 1, 'conclusion': 'success', 'status': 'completed',
               'display_title': 'Candidate PR 7 head ' + 'd'*40}
        exact = dict(run)
        environment = {'id': 9, 'can_admins_bypass': False,
                       'protection_rules': [{'type': 'required_reviewers',
                                             'reviewers': [{'type': 'User', 'reviewer': {'id': 5}}]}]}
        history = [{'state': 'approved', 'environments': [{'id': 9, 'name': 'recipe-review'}]}]
        def response(path):
            if path.endswith('/environments/recipe-review'):
                return environment
            if path.endswith('/approvals'):
                return history
            if '/attempts/' in path:
                return exact
            return run
        with patch.object(update, 'api', side_effect=response), \
                patch.object(update, 'repository', return_value='owner/repo'):
            acceptance.verify_human_authority(candidate, approved)
            run['run_attempt'] = 2
            run['conclusion'] = 'failure'
            acceptance.verify_human_authority(candidate, approved)
            exact['status'] = 'in_progress'
            exact['conclusion'] = None
            with self.assertRaises(acceptance.PendingApproval):
                acceptance.verify_human_authority(candidate, approved)
            exact['status'] = 'completed'
            exact['conclusion'] = 'failure'
            with self.assertRaises(ValueError):
                acceptance.verify_human_authority(candidate, approved)
            exact['conclusion'] = 'success'
            history[0]['environments'][0]['name'] = 'code-review'
            with self.assertRaises(ValueError):
                acceptance.verify_human_authority(candidate, approved)

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

    def test_failed_first_human_run_does_not_block_later_successful_proof(self):
        candidate = {'kind': 'recipe', 'repository': 'owner/repo', 'pr_number': 7,
                     'pkgbase': 'demo', 'base': 'c'*40, 'recipe_base': 'b'*40,
                     'head': 'd'*40, 'recipe_tree': 'e'*40, 'receipt_id': 'f'*64,
                     'head_branch': 'recipe-updates/demo/release'}
        stable = {**candidate, 'candidate_digest': acceptance.digest(candidate),
                  'review_environment': 'recipe-review'}
        first = {**stable, 'run_id': '10', 'run_attempt': '1'}
        second = {**stable, 'run_id': '11', 'run_attempt': '1'}
        proofs = {'tuple-10-1': first, 'tuple-11-1': second}
        def authority(record, proof):
            if proof['run_id'] == '10':
                raise ValueError('workflow failed')
        with patch.object(acceptance.recipe_state, 'keys', return_value=list(proofs), create=True), \
                patch.object(acceptance.recipe_state, 'load', side_effect=lambda namespace, key: proofs[key]), \
                patch.object(acceptance, 'verify_human_authority', side_effect=authority), \
                patch.object(acceptance.recipe_state, 'require_attestation', return_value=None):
            self.assertEqual(acceptance.human_proof(candidate, 'tuple', stable), second)
            second['head'] = 'a'*40
            with self.assertRaises(ValueError):
                acceptance.human_proof(candidate, 'tuple', stable)

    def test_native_evidence_must_match_exact_recipe_frozen_inputs_and_metadata(self):
        package = {'pkgbase': 'demo', 'recipe_commit': 'd'*40,
                   'input_digest': 'f'*64, 'lock': {'version': '2'},
                   'expected_srcinfo': 'pkgbase = demo',
                   'policy': {'outputs': [{'name': 'demo', 'arch': 'x86_64'}]}}
        candidate = {'schema': 1, 'repository': 'owner/repo', 'pr_number': 7, 'base': 'c'*40,
                     'recipe_base': 'b'*40, 'head': 'd'*40,
                     'recipe_pins': {'demo': 'd'*40}, 'previous_recipe_pins': {'demo': 'b'*40},
                     'image': 'pinned', 'harness_sha': 'a'*64, 'packages': [package]}
        output = {'pkgbase': 'demo', 'recipe_commit': 'd'*40, 'input_digest': 'f'*64,
                  'source_lock': {'version': '2'}, 'metadata': {'srcinfo': 'pkgbase = demo'},
                  'files': [{'name': 'demo', 'arch': 'x86_64', 'version': '2'}]}
        evidence = {**candidate, 'packages': [output], 'run_id': '42', 'run_attempt': '1',
                    'package_runs': [{'pkgbase': 'demo', 'run_id': '43', 'run_attempt': '1'}]}
        acceptance.verify_native_evidence(candidate, evidence)
        parent = {'path': '.github/workflows/candidate.yml', 'head_sha': 'c'*40,
                  'head_branch': 'main', 'event': 'workflow_dispatch', 'run_attempt': 1,
                  'display_title': 'Candidate PR 7 head ' + 'd'*40, 'conclusion': 'failure'}
        child = {'id': 43, 'run_attempt': 1, 'path': '.github/workflows/build-package.yml',
                 'head_sha': 'c'*40, 'head_branch': 'main', 'event': 'workflow_dispatch',
                 'display_title': 'Build demo / 42.1', 'status': 'completed', 'conclusion': 'success'}
        def response(path):
            if '/actions/runs/42/attempts/1' in path:
                return parent
            if '/actions/runs/43/attempts/1' in path:
                return child
            raise AssertionError('authority must query immutable recorded attempt: ' + path)
        with patch.object(update, 'api', side_effect=response), \
                patch.object(update, 'repository', return_value='owner/repo'), \
                patch.object(acceptance.recipe_state, 'require_attestation', return_value=None) as attestation:
            proof = acceptance.verify_native_authority(candidate, {'evidence': evidence})
            self.assertEqual(proof['package_runs'][0]['run_id'], '43')
            attestation.side_effect = ValueError('forged stored build lacks controller attestation')
            with self.assertRaisesRegex(ValueError, 'controller attestation'):
                acceptance.verify_native_authority(candidate, {'evidence': evidence})
            attestation.side_effect = None
            parent['display_title'] = 'Candidate PR 7 head ' + 'a'*40
            with self.assertRaises(ValueError):
                acceptance.verify_native_authority(candidate, {'evidence': evidence})
            parent['display_title'] = 'Candidate PR 7 head ' + 'd'*40
            child['conclusion'] = 'failure'
            with self.assertRaises(ValueError):
                acceptance.verify_native_authority(candidate, {'evidence': evidence})
            child['conclusion'] = 'success'
            child['run_attempt'] = 2
            with self.assertRaises(ValueError):
                acceptance.verify_native_authority(candidate, {'evidence': evidence})
        for field, value in [('recipe_commit', 'e'*40), ('input_digest', 'a'*64),
                             ('source_lock', {'version': '3'}), ('metadata', {'srcinfo': 'forged'})]:
            with self.assertRaises(ValueError):
                acceptance.verify_native_evidence(candidate, {**evidence, 'packages': [{**output, field: value}]})

    def test_controller_logic_drift_invalidates_original_candidate_compatibility(self):
        candidate = {'pkgbase': 'demo', 'image': 'image', 'harness_sha': 'harness',
                     'control_digest': 'trusted-controller',
                     'packages': [{'pkgbase': 'demo', 'policy': {'authority': 'local'}}]}
        args = ({'authority': 'local'}, 'image', 'harness', 'trusted-controller')
        self.assertTrue(acceptance.control_compatible(candidate, *args))
        self.assertFalse(acceptance.control_compatible(candidate, *args[:-1], 'changed-controller'))
        self.assertFalse(acceptance.control_compatible(candidate, {'authority': 'aur'}, *args[1:]))

    def test_reconcile_ignores_retired_recipe_history_without_blocking_enrolled_packages(self):
        retired = {'number': 7, 'base': {'ref': 'pkg/retired'},
                   'head': {'ref': 'recipe-updates/retired/release'},
                   'state': 'closed', 'merged_at': '2026-10-01', 'merge_commit_sha': 'a'*40}
        with ExitStack() as stack:
            for context in (
                patch.object(update, 'main_sha', return_value='c'*40),
                patch.object(update, 'control_checkout', return_value=('c'*40, {})),
                patch.object(update, 'policy_at', return_value={'active': {}}),
                patch.object(update, 'api', return_value=[retired]),
                patch.object(update, 'repository', return_value='owner/repo'),
            ):
                stack.enter_context(context)
            self.assertEqual(acceptance.reconcile(), [{'pr_number': 7, 'retired': True}])

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
