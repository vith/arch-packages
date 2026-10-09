import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
import shutil

from tools import recipe_candidates as candidates, recipe_state, update


class RecipeCandidateBoundaries(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_no_discovery_receipt_returns_none_but_lookup_remains_bounded(self):
        with patch.object(recipe_state, 'load_optional', return_value=None), patch.object(update, 'api', return_value={'parents': [{'sha': 'c'*40}]}):
            self.assertIsNone(candidates.proposal_receipt('a'*40, 'c'*40, 'example'))
        with patch.object(recipe_state, 'load_optional', return_value=None), patch.object(update, 'api', side_effect=lambda route: {'parents': [{'sha': str(int(route.rsplit('/', 1)[-1]) + 1)}]}):
            with self.assertRaisesRegex(ValueError, 'bounded'):
                candidates.proposal_receipt('1', 'base', 'example')

    def prepare_manual(self, work, invalid=False, adoption=False, control='c'*40, existing_receipt=None, pkgrel='2'):
        root = Path(work)
        old = root/'accepted'
        recipe = root/'human'
        for directory in (old/'recipes'/'example', old/'inputs', old/'upstream', recipe):
            directory.mkdir(parents=True)
        before = b'old patch\n'
        after = b'human patch\n'
        def native(directory, data, dependency):
            checksum = hashlib.sha256(data).hexdigest()
            (directory/'fix.patch').write_bytes(data)
            (directory/'PKGBUILD').write_text("pkgname=example\npkgver=1\npkgrel=1\narch=('x86_64')\nsource=('fix.patch')\nsha256sums=('"+checksum+"')\ndepends=('"+dependency+"')\npackage() { install -Dm644 fix.patch \"$pkgdir/usr/share/example/fix.patch\"; }\n")
            text = 'pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\n\tsource = '+('other.patch' if invalid and directory == recipe else 'fix.patch')+'\n\tsha256sums = '+checksum+'\npkgname = example\n\tdepends = '+dependency+'\n'
            (directory/'.SRCINFO').write_text(text)
        native(old/'recipes'/'example', before, 'original')
        native(recipe, after, 'human-dependency')
        for filename in ('PKGBUILD', '.SRCINFO'):
            path = recipe/filename
            path.write_text(path.read_text().replace('pkgrel=1', 'pkgrel='+pkgrel).replace('pkgrel = 1', 'pkgrel = '+pkgrel))
        source = {key: None for key in update.sources.FIELDS}
        source.update(id='patch', kind='local', source='fix.patch', checksums={'sha256': hashlib.sha256(before).hexdigest()})
        lock = {'schema': 1, 'version': '1-1', 'sources': [source]}
        provenance = {'aur': None, 'watchers': [{'id': 'release', 'kind': 'tag', 'source_id': 'patch', 'url': 'https://example.test/project.git', 'tag_pattern': r'^v(.+)$', 'accepted_tag': 'v1', 'accepted_tag_object': 'e'*40, 'accepted_peeled_commit': 'e'*40}]}
        policy = {'pkgbase': 'example', 'sources': [{'id': 'patch', 'kind': 'local', 'mutable': False, 'source_template': 'fix.patch', 'checksum_algorithm': 'sha256', 'checksum_index': 0}], 'outputs': [{'name': 'example', 'arch': 'x86_64'}]}
        update.dump(old/'packages.json', {'schema': 1, 'packages': [policy]})
        update.dump(old/'inputs'/'example.json', lock)
        update.dump(old/'upstream'/'example.json', provenance)
        (old/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'f'*64)
        repo = Path(candidates.__file__).parents[1]
        for filename in set(recipe_state.CONTROLS) | {'tools/build.sh', 'tools/native.py', 'tools/sources.py', 'tools/recipe_gate.py', 'tools/github_api.py'}:
            destination = old/filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo/filename, destination)
        pr = {'state': 'open', 'head': {'ref': 'feature/human-recipe', 'sha': 'a'*40, 'repo': {'full_name': 'owner/repo'}}, 'base': {'ref': 'pkg/example', 'sha': 'b'*40, 'repo': {'full_name': 'owner/repo'}}}
        stored = {} if existing_receipt is None else {('proposal', 'a'*40): copy.deepcopy(existing_receipt)}
        self.manual_stored = stored
        baseline = root/'baseline'
        proposal = None
        leafsets = {}
        commits = {}
        if adoption:
            from tools import recipe_acceptance as acceptance
            shutil.copytree(old, baseline)
            accepted_lock = copy.deepcopy(lock)
            accepted_lock['version'] = '1-'+pkgrel
            accepted_lock['sources'][0]['checksums']['sha256'] = hashlib.sha256(after).hexdigest()
            update.dump(old/'inputs'/'example.json', accepted_lock)
            shutil.rmtree(old/'recipes'/'example')
            shutil.copytree(recipe, old/'recipes'/'example')
            L, Q, M = (value*40 for value in '123')
            proposal = {'schema': 1, 'repository': 'owner/repo', 'base': 'c'*40,
                        'pkgbase': 'example', 'proposal_origin': 'legacy-accepted',
                        'legacy_pr_number': 3, 'legacy_base': L, 'legacy_head': Q,
                        'legacy_accepted': M, 'recipe_base': 'b'*40, 'recipe_head': 'a'*40,
                        'recipe_tree': 'd'*40, 'watcher_id': 'release', 'watcher_ids': ['release'],
                        'head_branch': pr['head']['ref'], 'expected_previous_control_pin': 'a'*40,
                        'baseline_lock': lock, 'baseline_provenance': provenance,
                        'lock': accepted_lock, 'provenance': provenance}
            stored['proposal', 'a'*40] = proposal
            before = {'recipes/example': ('160000', 'commit', 'b'*40),
                      'inputs/example.json': ('100644', 'blob', acceptance.blob_sha(lock)),
                      'upstream/example.json': ('100644', 'blob', acceptance.blob_sha(provenance))}
            after_leaves = {**before, 'recipes/example': ('160000', 'commit', 'a'*40),
                            'inputs/example.json': ('100644', 'blob', acceptance.blob_sha(accepted_lock))}
            leafsets = {'5'*40: before, '9'*40: after_leaves}
            receipt_hash = acceptance.digest({'base': L, 'tree': '9'*40, 'watcher': 'release', 'pkgbase': 'example'})
            commits = {L: {'tree': {'sha': '5'*40}, 'parents': []},
                       Q: {'tree': {'sha': '9'*40}, 'parents': [{'sha': L}],
                           'message': f'Update example via release\n\nArch-Update-Receipt: {receipt_hash}\nArch-Update-Base: {L}'},
                       M: {'tree': {'sha': '9'*40}, 'parents': [{'sha': L}, {'sha': Q}]}}
        def api(route, *args):
            if route.endswith('/pulls/3') and adoption:
                return {'state': 'closed', 'merged': True, 'merged_at': 'now', 'merge_commit_sha': M,
                        'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}},
                        'head': {'sha': Q, 'repo': {'full_name': 'owner/repo'}}}
            if '/pulls/' in route:
                return pr
            if '/statuses?' in route and adoption:
                if '/commits/'+Q+'/' in route:
                    return [{'context': 'arch-updater-receipt', 'state': 'success', 'description': receipt_hash,
                             'creator': {'login': 'github-actions[bot]', 'type': 'Bot'}}]
                return [{'context': 'arch-receipt/proposal/'+'a'*40, 'state': 'success',
                         'description': recipe_state.digest(proposal), 'creator': {'login': 'github-actions[bot]'}}]
            if '/git/trees/' in route and adoption:
                rows = leafsets[route.rsplit('/', 1)[-1].split('?')[0]]
                return {'truncated': False, 'tree': [{'path': path, 'mode': row[0], 'type': row[1], 'sha': row[2]} for path, row in rows.items()]}
            if route.rsplit('/', 1)[-1] in commits:
                return commits[route.rsplit('/', 1)[-1]]
            if '/branches/pkg%2F' in route:
                return {'protected': True}
            if route.endswith('a'*40):
                return {'tree': {'sha': 'd'*40}, 'parents': [{'sha': 'b'*40}]}
            raise AssertionError(route)
        def checkout(control, destination, pins=None):
            if pins is not None:
                pins['example'] = 'a'*40 if adoption else 'b'*40
            shutil.copytree(baseline if adoption and control == L else old, destination)
            return destination
        def export(head, destination):
            shutil.copytree(baseline/'recipes'/'example' if adoption and head == 'b'*40 else recipe, destination)
            return destination
        def probe(directory, frozen, policy, destination, **kwargs):
            # Native execution boundary; complete metadata reflects the actual
            # exported tree, while all source/policy/controller checks stay real.
            return {'srcinfo': (directory/'.SRCINFO').read_text(), 'sources': frozen['sources'], 'version': '1-'+pkgrel}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}))
            for target, attribute, kwargs in (
                (update, 'api', {'side_effect': api}), (update, 'status', {}),
                (update, 'main_sha', {'return_value': control}),
                (update, 'trusted_checkout_sha', {'return_value': control}),
                (update, 'ref_head', {'return_value': 'b'*40}),
                (recipe_state, 'load_optional', {'side_effect': lambda namespace, key: stored.get((namespace, key))}),
                (recipe_state, 'save', {'side_effect': lambda namespace, key, value: stored.setdefault((namespace, key), copy.deepcopy(value))}),
                (update, 'checkout_data', {'side_effect': checkout}),
                (candidates, 'export_recipe', {'side_effect': export}),
                (update, 'native_probe', {'side_effect': probe}),
                (candidates.recipes, 'is_ancestor', {'return_value': True}),
                (update, 'require_review_environment', {}),
            ):
                stack.enter_context(patch.object(target, attribute, **kwargs))
            result = candidates.prepare(1, root/'candidate')
        return result, stored

    def test_direct_human_dependency_and_local_patch_freezes_exact_native_candidate(self):
        with tempfile.TemporaryDirectory() as work:
            record, stored = self.prepare_manual(work)
            proposal = stored['proposal', record['head']]
            self.assertEqual(proposal['proposal_origin'], 'manual')
            self.assertEqual(proposal['head_branch'], 'feature/human-recipe')
            self.assertIsNone(record['watcher_id'])
            self.assertEqual(record['watcher_ids'], [])
            self.assertEqual(proposal['lock']['sources'][0]['checksums']['sha256'], hashlib.sha256(b'human patch\n').hexdigest())
            self.assertIn('depends = human-dependency', record['packages'][0]['expected_srcinfo'])
            self.assertFalse(record['mechanical'])
            self.assertEqual(record['review_environment'], 'recipe-review')

    def test_manual_payload_changes_require_native_version_or_pkgrel_advancement(self):
        for pkgrel in ('1', '0'):
            with self.subTest(pkgrel=pkgrel), tempfile.TemporaryDirectory() as work:
                with self.assertRaisesRegex(ValueError, 'version or pkgrel bump'):
                    self.prepare_manual(work, pkgrel=pkgrel)
                self.assertNotIn(('proposal', 'a'*40), self.manual_stored)

    def test_same_manual_head_revalidates_under_new_control_without_rewriting_source_receipt(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as retry:
            initial, initial_state = self.prepare_manual(first)
            source_receipt = initial_state['proposal', initial['head']]
            updated, updated_state = self.prepare_manual(retry, control='f'*40, existing_receipt=source_receipt)
            self.assertEqual(initial['recipe_base'], updated['recipe_base'])
            self.assertEqual(initial['head'], updated['head'])
            self.assertNotEqual(initial['base'], updated['base'])
            self.assertNotEqual(candidates.candidate_digest(initial), candidates.candidate_digest(updated))
            self.assertEqual(initial['receipt_id'], updated['receipt_id'])
            self.assertEqual(updated_state['proposal', updated['head']], source_receipt)
            self.assertEqual(updated_state['proposal', updated['head']]['base'], initial['base'])

    def test_same_manual_head_rejects_drift_in_frozen_native_metadata(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as retry:
            initial, state = self.prepare_manual(first)
            receipt = copy.deepcopy(state['proposal', initial['head']])
            receipt['native_srcinfo'] = receipt['native_srcinfo'].replace('human-dependency', 'different-dependency')
            with self.assertRaisesRegex(ValueError, 'immutable manual proposal'):
                self.prepare_manual(retry, control='f'*40, existing_receipt=receipt)

    def test_accepted_legacy_main_pin_uses_attested_B_baseline_for_real_native_candidate(self):
        with tempfile.TemporaryDirectory() as work:
            record, stored = self.prepare_manual(work, adoption=True)
            self.assertEqual(record['proposal_origin'], 'legacy-accepted')
            self.assertEqual(record['recipe_base'], 'b'*40)
            self.assertEqual(record['previous_control_pin'], 'a'*40)
            self.assertNotEqual(record['baseline_lock'], record['predecessor_lock'])
            self.assertEqual(record['packages'][0]['lock'], record['predecessor_lock'])
            self.assertIn('depends = human-dependency', record['packages'][0]['expected_srcinfo'])
            self.assertEqual(record['review_environment'], 'recipe-review')
            self.assertEqual(record['decisions'][0]['reason'], 'local payload changed')

    def test_direct_human_unenrolled_source_does_not_create_manual_receipt(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, 'unenrolled'):
                self.prepare_manual(work, invalid=True)
            self.assertEqual(self.manual_stored, {})

    def test_fixed_source_cannot_be_changed_by_human_edit(self):
        old = {'schema': 1, 'version': '1-1', 'sources': [{'id': 'aux', 'kind': 'archive', 'checksums': {'sha256': 'a'}}]}
        new = copy.deepcopy(old)
        new['sources'][0]['checksums']['sha256'] = 'b'
        with self.assertRaisesRegex(ValueError, 'fixed'):
            candidates.source_boundaries(old, new, {'sources': [{'id': 'aux', 'mutable': False}]})

    def test_local_payload_checksum_changes_preserve_enrolled_path_and_algorithm(self):
        source = {'id': 'patch', 'kind': 'local', 'source': 'fix.patch', 'checksums': {'sha256': 'old'}}
        old = {'sources': [source]}
        new = copy.deepcopy(old)
        new['sources'][0]['checksums']['sha256'] = 'new'
        policy = {'sources': [{'id': 'patch', 'mutable': False}]}
        candidates.source_boundaries(old, new, policy)
        for field, value in [('source', 'replacement.patch'), ('checksums', {'sha512': 'new'})]:
            invalid = copy.deepcopy(new)
            invalid['sources'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'enrollment'):
                candidates.source_boundaries(old, invalid, policy)

    def test_malformed_existing_proposal_is_not_treated_as_absence(self):
        with patch.object(recipe_state, 'load_optional', side_effect=ValueError('malformed durable record')):
            with self.assertRaisesRegex(ValueError, 'malformed'):
                candidates.proposal_receipt('a'*40, 'b'*40, 'example')

    def test_multiple_exact_changed_remote_watchers_are_derived_without_discovery(self):
        previous = {'version': '1-1', 'sources': [
            {'id': 'one', 'kind': 'git', 'ref': 'refs/tags/v1'},
            {'id': 'two', 'kind': 'git', 'ref': 'refs/tags/v1'}]}
        edited = copy.deepcopy(previous)
        edited['version'] = '2-1'
        for source in edited['sources']:
            source['ref'] = 'refs/tags/v2'
        provenance = {'aur': None, 'watchers': [
            {'id': name, 'kind': 'tag', 'source_id': name, 'url': 'https://example.test/'+name+'.git',
             'tag_pattern': r'^v(.+)$', 'accepted_tag': 'v1',
             'accepted_tag_object': 'a'*40, 'accepted_peeled_commit': 'a'*40}
            for name in ('one', 'two')]}
        rows = 'b'*40+'\trefs/tags/v2\n'
        with patch.object(update.sources, 'git', return_value=rows) as git:
            result, identities = candidates.manual_provenance(previous, edited, provenance)
        self.assertEqual(identities, ['one', 'two'])
        self.assertEqual([watcher['accepted_tag'] for watcher in result['watchers']], ['v2', 'v2'])
        self.assertEqual([call.args[-2:] for call in git.call_args_list], [('refs/tags/v2', 'refs/tags/v2^{}')]*2)
        edited['sources'].append({'id': 'unwatched', 'kind': 'archive', 'url': 'new'})
        previous['sources'].append({'id': 'unwatched', 'kind': 'archive', 'url': 'old'})
        with self.assertRaisesRegex(ValueError, 'not enrolled'):
            candidates.manual_provenance(previous, edited, provenance)

    def test_source_enrollment_and_order_are_not_editable(self):
        old = {'sources': [{'id': 'one'}, {'id': 'two'}]}
        with self.assertRaisesRegex(ValueError, 'enrollment'):
            candidates.source_boundaries(old, {'sources': list(reversed(old['sources']))}, {'sources': []})

    def test_delayed_edited_head_finds_original_durable_receipt(self):
        head, original, base = 'a'*40, 'b'*40, 'c'*40
        receipt = {'recipe_head': original, 'recipe_base': base, 'pkgbase': 'example'}
        def stored(namespace, key):
            return receipt if key == original else None
        with patch.object(recipe_state, 'load_optional', side_effect=stored), patch.object(update, 'api', return_value={'parents': [{'sha': original}, {'sha': base}]}):
            self.assertEqual(candidates.proposal_receipt(head, base, 'example'), receipt)

    def test_direct_human_aur_merge_does_not_search_secondary_repository_ancestry(self):
        head, base, aur = 'a'*40, 'b'*40, 'c'*40
        def api(route):
            if route.endswith(head):
                return {'parents': [{'sha': base}, {'sha': aur}]}
            raise AssertionError('secondary AUR history must not be queried')
        with patch.object(recipe_state, 'load_optional', return_value=None), patch.object(update, 'api', side_effect=api):
            self.assertIsNone(candidates.proposal_receipt(head, base, 'example'))

    def test_receipt_for_different_base_is_rejected(self):
        with patch.object(recipe_state, 'load_optional', return_value={'recipe_head': 'a'*40, 'recipe_base': 'd'*40, 'pkgbase': 'example'}):
            with self.assertRaisesRegex(ValueError, 'base'):
                candidates.proposal_receipt('a'*40, 'c'*40, 'example')

    def record(self, mechanical=False):
        return {'kind': 'recipe', 'repository': 'owner/repo', 'pkgbase': 'example', 'watcher_id': 'release', 'head_branch': 'recipe-updates/example/release', 'receipt_id': 'e'*64, 'pr_number': 1, 'base': 'c'*40, 'recipe_base': 'b'*40, 'head': 'a'*40, 'recipe_tree': 'd'*40, 'mechanical': mechanical, 'review_environment': '' if mechanical else 'recipe-review'}

    def test_review_refuses_build_from_another_tuple(self):
        record = self.record()
        with patch.object(update, 'require_review_environment'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value={'candidate_digest': 'wrong', 'native_verified': True}), patch.object(update, 'api', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'durable'):
                candidates.review(record)

    def test_finalize_does_not_merge_human_recipe(self):
        record = self.record()
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'record.json'
            update.dump(path, record)
            with patch.object(candidates, 'validate_build'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(candidates, 'required_statuses', return_value={'verify':'success', 'candidate-build':'success', 'recipe-policy':'success'}), patch.object(candidates, 'durable_build', return_value={}), patch.object(candidates, 'durable_approval', return_value={}), patch.object(update, 'api') as api:
                result = candidates.finalize(1, record['base'], record['head'], path, work)
            self.assertFalse(result['merged'])
            api.assert_not_called()

    def test_retry_preserves_approval_identity_but_head_edits_invalidate_it(self):
        first = {**self.record(), 'run_id': '10', 'run_attempt': '1'}
        retry = {**first, 'run_id': '20', 'run_attempt': '2'}
        self.assertEqual(candidates.candidate_digest(first), candidates.candidate_digest(retry))
        retry['head'] = 'f'*40
        self.assertNotEqual(candidates.candidate_digest(first), candidates.candidate_digest(retry))

    def test_original_successful_build_remains_usable_after_artifacts_expire(self):
        record = self.record()
        built = {**candidates.identity(record), 'candidate_digest': candidates.candidate_digest(record), 'native_verified': True, 'evidence': {'run_id': '10', 'run_attempt': '1'}, 'statuses': {'verify': 'success', 'candidate-build': 'success'}}
        with patch.object(recipe_state, 'load', return_value=built):
            self.assertEqual(candidates.durable_build(record)['evidence'], {'run_id': '10', 'run_attempt': '1'})
            changed = {**record, 'recipe_base': 'f'*40}
            with self.assertRaisesRegex(ValueError, 'durable'):
                candidates.durable_build(changed)

    def build_fixture(self, directory):
        srcinfo = 'pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\npkgname = example\n'
        lock = {'schema': 1, 'version': '1-1', 'sources': []}
        package = {'pkgbase': 'example', 'recipe_commit': 'a'*40, 'previous_recipe_commit': 'b'*40, 'input_digest': 'e'*64, 'lock': lock, 'policy': {'outputs': [{'name': 'example', 'arch': 'x86_64'}]}, 'expected_srcinfo': srcinfo}
        record = {**self.record(), 'schema': 1, 'recipe_pins': {'example': 'a'*40}, 'previous_recipe_pins': {'example': 'b'*40}, 'run_id': '10', 'run_attempt': '1', 'image': 'image', 'harness_sha': 'f'*64, 'packages': [package]}
        filename = 'example-1-1-x86_64.pkg.tar.zst'
        (directory/filename).write_bytes(b'native package bytes')
        output = {'name': 'example', 'arch': 'x86_64', 'filename': filename, 'version': '1-1', 'sha256': hashlib.sha256(b'native package bytes').hexdigest()}
        evidence = {**record, 'packages': [{'pkgbase': 'example', 'recipe_commit': 'a'*40, 'input_digest': 'e'*64, 'source_lock': lock, 'metadata': {'srcinfo': srcinfo}, 'files': [output]}]}
        update.dump(directory/'native-evidence.json', evidence)
        return record, evidence, filename

    def test_read_only_collector_validates_bytes_without_creating_receipts(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, evidence, _ = self.build_fixture(directory)
            with patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)), patch.object(recipe_state, 'save') as save, patch.object(update, 'status') as status:
                self.assertEqual(candidates.validate_build(record, directory, report=False), evidence)
            save.assert_not_called()
            status.assert_not_called()

    def test_new_build_does_not_launder_unattested_existing_receipt(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, _, _ = self.build_fixture(directory)
            forged = {**candidates.identity(record), 'candidate_digest': candidates.candidate_digest(record), 'native_verified': True, 'evidence': {'forged': True}}
            def load(namespace, key):
                return candidates.frozen_record(record) if namespace == 'candidate' else forged
            statuses = [{'context': 'verify', 'state': 'success'}, {'context': 'candidate-build', 'state': 'success'}]
            with patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', side_effect=load), patch.object(recipe_state, 'load_optional', return_value=forged), patch.object(recipe_state, 'require_attestation', side_effect=ValueError('unattested durable evidence')), patch.object(recipe_state, 'attest') as attest, patch.object(update, 'api', return_value=statuses), patch.object(update, 'status'):
                with self.assertRaisesRegex(ValueError, 'unattested'):
                    candidates.validate_build(record, directory)
            attest.assert_not_called()

    def test_native_build_rejects_corrupted_package_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, _, filename = self.build_fixture(directory)
            (directory/filename).write_bytes(b'forged bytes')
            with patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)):
                with self.assertRaisesRegex(ValueError, 'bytes/version'):
                    candidates.validate_build(record, directory, report=False)

    def test_native_build_rejects_extra_package_outputs(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, evidence, _ = self.build_fixture(directory)
            extra = {**evidence['packages'][0]['files'][0], 'name': 'unenrolled'}
            evidence['packages'][0]['files'].append(extra)
            update.dump(directory/'native-evidence.json', evidence)
            with patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)):
                with self.assertRaisesRegex(ValueError, 'extra'):
                    candidates.validate_build(record, directory, report=False)

    def test_merged_recipe_finalize_reconciles_without_merging_again(self):
        record = {**self.record(True), 'accepted': 'f'*40}
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)/'record.json'
            update.dump(path, record)
            with patch.object(candidates, 'validate_build'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(candidates, 'required_statuses', return_value={}), patch.object(candidates, 'durable_build', return_value={}), patch.object(candidates, 'durable_approval', return_value={}), patch('tools.recipe_acceptance.reconcile', return_value={'reconciled': True}) as reconcile, patch.object(update, 'api') as api:
                result = candidates.finalize(1, record['base'], record['head'], path, work)
            self.assertTrue(result['reconciled'])
            api.assert_not_called()
            reconcile.assert_called_once_with(1)

    def test_approval_evidence_rejects_other_control_workflow(self):
        record = self.record()
        run = {'head_sha': 'f'*40, 'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml', 'run_attempt': 1}
        with patch.dict('os.environ', {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(update, 'api', return_value=run):
            with self.assertRaisesRegex(ValueError, 'workflow'):
                candidates.approval_run(record)

    def test_approval_from_other_recipe_head_is_not_transferable(self):
        record = self.record()
        run = {'head_sha': record['base'], 'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml', 'run_attempt': 1, 'display_title': 'Candidate PR 1 head ' + 'f'*40}
        with patch.dict('os.environ', {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(update, 'api', return_value=run):
            with self.assertRaisesRegex(ValueError, 'workflow'):
                candidates.approval_run(record)

    def test_approval_evidence_requires_real_environment_approval(self):
        record = self.record()
        run = {'head_sha': record['base'], 'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml', 'run_attempt': 1, 'display_title': f"Candidate PR {record['pr_number']} head {record['head']}"}
        with patch.dict('os.environ', {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}), patch.object(update, 'api', side_effect=[run, []]):
            with self.assertRaisesRegex(ValueError, 'approval'):
                candidates.approval_run(record)

    def test_edited_enrolled_tag_derives_authentic_new_provenance(self):
        previous = {'schema': 1, 'version': '1.0-1', 'sources': [{'id': 'code', 'kind': 'git', 'ref': 'refs/tags/v1.0'}]}
        edited = {'schema': 1, 'version': '2.0-1', 'sources': [{'id': 'code', 'kind': 'git', 'ref': 'refs/tags/v2.0'}]}
        provenance = {'aur': None, 'watchers': [{'id': 'release', 'kind': 'tag', 'source_id': 'code', 'url': 'https://example.test/repo.git', 'tag_pattern': r'^v(.+)$', 'accepted_tag': 'v1.0', 'accepted_tag_object': 'a'*40, 'accepted_peeled_commit': 'a'*40}]}
        rows = 'b'*40 + '\trefs/tags/v2.0\n' + 'c'*40 + '\trefs/tags/v2.0^{}\n'
        with patch.object(update.sources, 'git', return_value=rows):
            result = candidates.edited_provenance(previous, edited, provenance, 'release')
        self.assertEqual(result['watchers'][0]['accepted_tag'], 'v2.0')
        self.assertEqual(result['watchers'][0]['accepted_tag_object'], 'b'*40)
        self.assertEqual(result['watchers'][0]['accepted_peeled_commit'], 'c'*40)
        self.assertEqual(provenance['watchers'][0]['accepted_tag'], 'v1.0')

    def test_retried_human_review_retains_separate_immutable_run_proofs(self):
        record = self.record()
        key = recipe_state.identity_key(record)
        stored = {('built', key): {**candidates.identity(record), 'candidate_digest': candidates.candidate_digest(record), 'native_verified': True}}
        def api(route, *args):
            if route.endswith('/statuses'):
                return [{'context': 'verify', 'state': 'success'}, {'context': 'candidate-build', 'state': 'success'}]
            if route.endswith('/approvals'):
                return [{'state': 'approved', 'environments': [{'name': 'recipe-review'}]}]
            return {'head_sha': record['base'], 'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml', 'run_attempt': 1, 'display_title': f"Candidate PR {record['pr_number']} head {record['head']}"}
        def save(namespace, path, value):
            if (namespace, path) in stored and stored[namespace, path] != value:
                raise ValueError('immutable state conflict')
            stored[namespace, path] = value
        with patch.object(update, 'require_review_environment'), patch.object(update, 'api', side_effect=api), patch.object(update, 'status'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', side_effect=lambda namespace, path: stored[namespace, path]), patch.object(recipe_state, 'load_optional', side_effect=lambda namespace, path: stored.get((namespace, path))), patch.object(recipe_state, 'save', side_effect=save), patch.object(recipe_state, 'attest'), patch.object(recipe_state, 'require_attestation'):
            for run in ('10', '20'):
                with patch.dict('os.environ', {'GITHUB_RUN_ID': run, 'GITHUB_RUN_ATTEMPT': '1'}):
                    candidates.review(record)
        self.assertNotIn('run_id', stored['approved', key])
        self.assertEqual(stored['approved', key+'-10-1']['run_id'], '10')
        self.assertEqual(stored['approved', key+'-20-1']['run_id'], '20')
        self.assertEqual(stored['approved', key+'-10-1']['candidate_digest'], stored['approved', key+'-20-1']['candidate_digest'])

    def test_review_requires_actual_recipe_environment(self):
        record = self.record()
        record['review_environment'] = 'code-review'
        with self.assertRaisesRegex(ValueError, 'recipe-review'):
            candidates.review(record)
