import base64
import copy
import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack, contextmanager
import shutil

from tools import attestations, build_store, native, recipe_candidates as candidates, recipe_state, update


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

    def prepare_manual(self, work, invalid=False, adoption=False, control='c'*40, existing_receipt=None, pkgrel='2', completed=None, drift=None, current_run=None, recover_transport=False):
        root = Path(work)
        old = root/'accepted'
        recipe = root/'human'
        for directory in (old/'recipes'/'example', old/'inputs', old/'upstream', recipe):
            directory.mkdir(parents=True)
        before = b'old patch\n'
        after = b'human patch\n'
        def write_recipe(directory, data, dependency):
            checksum = hashlib.sha256(data).hexdigest()
            (directory/'fix.patch').write_bytes(data)
            (directory/'PKGBUILD').write_text("pkgname=example\npkgver=1\npkgrel=1\narch=('x86_64')\nsource=('fix.patch')\nsha256sums=('"+checksum+"')\ndepends=('"+dependency+"')\npackage() { install -Dm644 fix.patch \"$pkgdir/usr/share/example/fix.patch\"; }\n")
            text = 'pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\n\tsource = '+('other.patch' if invalid and directory == recipe else 'fix.patch')+'\n\tsha256sums = '+checksum+'\npkgname = example\n\tdepends = '+dependency+'\n'
            (directory/'.SRCINFO').write_text(text)
        write_recipe(old/'recipes'/'example', before, 'original')
        write_recipe(recipe, after, 'human-dependency')
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
        for filename in set(recipe_state.CONTROLS) | {'tools/build.sh', 'tools/native.py', 'tools/sources.py', 'tools/recipe_gate.py', 'tools/github_api.py', 'tools/dependency_repo.py', 'keys/arch-packages.asc', 'keys/n3t.asc'}:
            destination = old/filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo/filename, destination)
        if completed is not None:
            (old/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'9'*64)
            (old/'tools'/'build.sh').write_text('changed trusted harness\n')
        if drift == 'policy':
            changed = copy.deepcopy(policy)
            changed['outputs'][0]['arch'] = 'any'
            update.dump(old/'packages.json', {'schema': 1, 'packages': [changed]})
        elif drift == 'source':
            changed = copy.deepcopy(lock)
            changed['sources'][0]['checksums']['sha256'] = '0'*64
            update.dump(old/'inputs'/'example.json', changed)
        elif drift == 'recipe':
            (recipe/'fix.patch').write_text('changed head payload\n')
        pr = {'state': 'open', 'head': {'ref': 'feature/human-recipe', 'sha': 'a'*40, 'repo': {'full_name': 'owner/repo'}}, 'base': {'ref': 'pkg/example', 'sha': 'b'*40, 'repo': {'full_name': 'owner/repo'}}}
        stored = {} if existing_receipt is None else {('proposal', 'a'*40): copy.deepcopy(existing_receipt)}
        if completed is not None:
            stored['candidate', recipe_state.identity_key(completed)] = candidates.frozen_record(completed)
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
            if '/actions/runs/' in route:
                run = {'head_sha': control, 'head_branch': 'main', 'event': 'workflow_dispatch',
                       'path': '.github/workflows/candidate.yml', 'display_title': 'Candidate PR 1 head '+'a'*40,
                       'run_attempt': 1, 'repository': {'full_name': 'owner/repo'}}
                return {**run, **(current_run or {})}
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
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'GITHUB_RUN_ID': '20' if completed is not None else '10', 'GITHUB_RUN_ATTEMPT': '1'}))
            for target, attribute, kwargs in (
                (update, 'api', {'side_effect': api}), (update, 'status', {}),
                (update, 'main_sha', {'return_value': control}),
                (update, 'trusted_checkout_sha', {'return_value': control}),
                (update, 'ref_head', {'return_value': 'b'*40}),
                (recipe_state, 'load_optional', {'side_effect': lambda namespace, key: stored.get((namespace, key))}),
                (recipe_state, 'save', {'side_effect': lambda namespace, key, value: stored.setdefault((namespace, key), copy.deepcopy(value))}),
                (recipe_state, 'keys', {'side_effect': lambda namespace, prefix: [key for ns, key in stored if ns == namespace and key.startswith(prefix)]}),
                (update, 'checkout_data', {'side_effect': checkout}),
                (candidates, 'export_recipe', {'side_effect': export}),
                (native, 'run', {'side_effect': AssertionError('prepare must not invoke native harness')}),
                (recipe_state, 'attest', {}),
                (candidates.recipes, 'is_ancestor', {'return_value': True}),
                (update, 'require_review_environment', {}),
            ):
                mocked = stack.enter_context(patch.object(target, attribute, **kwargs))
                if target is native and attribute == 'run':
                    native_run = mocked
            if completed is not None:
                descriptor = {'record': completed, 'producer': {'run_id': '11', 'run_attempt': '1'}}
                if recover_transport:
                    from tools.package_runs import OriginalTransportRecoveryRequired
                    artifact = {'id': 7, 'name': 'package-example-11-1', 'digest': 'sha256:'+'4'*64,
                                'size': 100, 'run_id': '11', 'run_attempt': '1', 'head_sha': completed['base']}
                    recovery = OriginalTransportRecoveryRequired(completed, descriptor['producer'], artifact, root/'recovery')
                    stack.enter_context(patch.object(build_store, 'lookup', side_effect=recovery))
                else:
                    stack.enter_context(patch.object(build_store, 'lookup', return_value=descriptor))
                stack.enter_context(patch.object(candidates, 'verify_authorization', return_value={}))
                stack.enter_context(patch.object(candidates, 'verify_candidate_provenance', return_value={}))
                stack.enter_context(patch.object(update, 'align_aur_lock', side_effect=AssertionError('reuse must not refreeze')))
                stack.enter_context(patch.object(candidates, 'classify_recipe_update', side_effect=AssertionError('reuse must not reclassify')))
                stack.enter_context(patch.object(candidates, 'authorize', side_effect=AssertionError('reuse must not reauthorize')))
            result = candidates.prepare(1, root/'candidate')
            if completed is not None:
                candidates.verify_current_controller(result, root/'candidate'/'controller-context.json')
            native_run.assert_not_called()
        return result, stored

    def test_direct_human_dependency_and_local_patch_freezes_static_candidate_without_execution(self):
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

    def test_completed_original_is_selected_before_current_controller_refreeze(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as retry:
            initial, state = self.prepare_manual(first)
            initial_bytes = update.sources.canonical(initial)
            source_receipt = state['proposal', initial['head']]
            selected, stored = self.prepare_manual(retry, control='f'*40,
                                                  existing_receipt=source_receipt, completed=initial)
            self.assertEqual(update.sources.canonical(selected), initial_bytes)
            self.assertEqual(update.load(Path(retry)/'candidate'/'candidate.json'), initial)
            with tarfile.open(Path(retry)/'candidate'/'bundle.tar') as archive:
                transported = json.load(archive.extractfile('bundle/bundle.json'))
            self.assertEqual(transported['base'], initial['base'])
            self.assertEqual(transported['packages'], initial['packages'])
            context = update.load(Path(retry)/'candidate'/'controller-context.json')
            self.assertEqual(context['base'], 'f'*40)
            self.assertEqual(context['candidate_digest'], candidates.candidate_digest(initial))
            self.assertEqual(stored['candidate', recipe_state.identity_key(initial)], candidates.frozen_record(initial))

    def test_completed_original_without_attested_transport_selects_recovery_not_compilation(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as retry:
            initial, state = self.prepare_manual(first)
            selected, _ = self.prepare_manual(retry, control='f'*40, completed=initial,
                                              existing_receipt=state['proposal', initial['head']],
                                              recover_transport=True)
            self.assertEqual(selected, initial)
            context = update.load(Path(retry)/'candidate'/'controller-context.json')
            self.assertIsNone(context['descriptor_digest'])
            self.assertEqual(context['original_transport']['producer']['run_id'], '11')
            self.assertEqual(context['original_transport']['artifact']['digest'], 'sha256:'+'4'*64)
            self.assertEqual(context['run_id'], '20')
            self.assertEqual(selected['run_id'], '10')

    def test_completed_original_rejects_relevant_current_content_drift(self):
        with tempfile.TemporaryDirectory() as first:
            initial, state = self.prepare_manual(first)
            for drift in ('policy', 'source', 'recipe'):
                with self.subTest(drift=drift), tempfile.TemporaryDirectory() as retry:
                    with self.assertRaisesRegex(ValueError, 'baseline changed|payload differs'):
                        self.prepare_manual(retry, control='f'*40, completed=initial, drift=drift,
                                            existing_receipt=state['proposal', initial['head']])

    def test_completed_original_rejects_wrong_current_controller_issuer(self):
        with tempfile.TemporaryDirectory() as first:
            initial, state = self.prepare_manual(first)
            for mutation in ({'head_sha': 'e'*40}, {'head_branch': 'other'},
                             {'event': 'push'}, {'path': '.github/workflows/other.yml'},
                             {'display_title': 'Candidate PR 1 head '+'e'*40},
                             {'run_attempt': 2}):
                with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as retry:
                    with self.assertRaisesRegex(ValueError, 'Actions provenance mismatch'):
                        self.prepare_manual(retry, control='f'*40, completed=initial, current_run=mutation,
                                            existing_receipt=state['proposal', initial['head']])

    def test_same_manual_head_rejects_drift_in_frozen_native_metadata(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as retry:
            initial, state = self.prepare_manual(first)
            receipt = copy.deepcopy(state['proposal', initial['head']])
            receipt['native_srcinfo'] = receipt['native_srcinfo'].replace('human-dependency', 'different-dependency')
            with self.assertRaisesRegex(ValueError, 'immutable manual proposal'):
                self.prepare_manual(retry, control='f'*40, existing_receipt=receipt)

    def test_accepted_legacy_main_pin_uses_attested_B_baseline_for_static_candidate(self):
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

    def candidate_transport(self, record, state, members=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for name, value in members or [('candidate.json', json.dumps(record).encode())]:
                archive.writestr(name, value)
        state['archive'] = data.getvalue()
        state['artifacts'] = [{'id': 81, 'name': 'prepared-candidate-1',
                               'expired': False, 'size_in_bytes': len(state['archive']),
                               'digest': 'sha256:'+hashlib.sha256(state['archive']).hexdigest(),
                               'workflow_run': {'id': 10, 'head_sha': record['base'], 'head_branch': 'main'}}]

    @contextmanager
    def authorization_fixture(self, work):
        root = Path(work)
        record, stored = self.prepare_manual(work)
        key = recipe_state.identity_key(record)
        run = {'head_sha': record['base'], 'head_branch': 'main',
               'event': 'workflow_dispatch', 'path': '.github/workflows/candidate.yml',
               'run_attempt': 1, 'display_title': f"Candidate PR 1 head {record['head']}"}
        job = {'id': 71, 'name': f"Review PR 1 head {record['head']} base {record['base']}",
               'status': 'completed', 'conclusion': 'success'}
        environment = {'id': 31, 'name': 'recipe-review', 'can_admins_bypass': False,
                       'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'id': 11}]}]}
        history = [{'state': 'approved', 'user': {'type': 'User'},
                    'environments': [{'id': 31, 'name': 'recipe-review'}]}]
        state = {'run': run, 'jobs': {'jobs': [job], 'total_count': 1},
                 'environment': environment, 'history': history, 'tree': record['recipe_tree']}
        statuses = [{'context': 'arch-receipt/candidate/'+key, 'state': 'success',
                     'description': recipe_state.digest(stored['candidate', key]),
                     'creator': {'login': 'github-actions[bot]'}}]
        state['statuses'] = statuses
        self.candidate_transport(record, state)
        original = copy.deepcopy(record)
        signer = candidates._candidate_signer(original)
        statement = {'_type': 'https://in-toto.io/Statement/v1',
                     'subject': [{'name': 'candidate.json', 'digest': {
                         'sha256': hashlib.sha256(update.sources.canonical(original)).hexdigest()}}],
                     'predicateType': 'https://slsa.dev/provenance/v1',
                     'predicate': {'runDetails': {'metadata': {
                         'invocationId': 'https://github.com/owner/repo/actions/runs/10/attempts/1'}}}}
        bundle = {'mediaType': 'application/vnd.dev.sigstore.bundle.v0.3+json',
                  'verificationMaterial': {'certificate': {'rawBytes': 'Y2VydGlmaWNhdGU='}},
                  'dsseEnvelope': {'payloadType': 'application/vnd.in-toto+json',
                                   'payload': base64.b64encode(update.sources.canonical(statement)).decode(),
                                   'signatures': [{'sig': 'c2lnbmF0dXJl'}]}}
        stored['candidate-provenance', key] = {'schema': 1, 'candidate': original,
                                              'signer': copy.deepcopy(signer), 'bundle': copy.deepcopy(bundle)}
        signed_subject = update.sources.canonical(original)
        def verify(path, bundle_path, actual_signer):
            # Model only the external identity/signature verifier: all controller
            # lookup, canonical reconstruction, freezing and bounds remain real.
            if (Path(path).read_bytes() != signed_subject
                    or json.loads(Path(bundle_path).read_bytes()) != bundle
                    or actual_signer != signer):
                raise ValueError('external candidate crypto verification failed')
            return {'issuer': 'https://token.actions.githubusercontent.com',
                    'runInvocationURI': 'https://github.com/owner/repo/actions/runs/10/attempts/1'}
        def api(route, method='GET', data=None):
            if route.endswith('/actions/runs/10/artifacts?per_page=100'):
                return {'artifacts': state['artifacts'], 'total_count': len(state['artifacts'])}
            if route.endswith('/actions/artifacts/81'):
                return state.get('exact_artifact', state['artifacts'][0])
            if '/statuses?' in route:
                return statuses
            if '/statuses/' in route and method == 'POST':
                posted = {**data, 'creator': {'login': 'github-actions[bot]'}}
                statuses.append(posted)
                return posted
            if '/environments/' in route:
                return state['environment']
            if route.endswith('/approvals'):
                return state['history']
            if '/jobs?' in route:
                return state['jobs']
            if '/actions/runs/' in route:
                return state['run']
            if '/git/commits/' in route:
                return {'tree': {'sha': state['tree']}}
            if '/pulls/' in route:
                return {'state': 'open', 'base': {'ref': 'pkg/example', 'sha': record['recipe_base'],
                                                'repo': {'full_name': 'owner/repo'}},
                        'head': {'ref': record['head_branch'], 'sha': record['head'],
                                 'repo': {'full_name': 'owner/repo'}}}
            if '/branches/pkg%2F' in route:
                return {'protected': True}
            raise AssertionError(route)
        def download(route, destination):
            self.assertEqual(route, update.route('/actions/artifacts/81/zip'))
            destination.write_bytes(state['archive'])
        def checkout(control, destination, pins=None):
            self.assertEqual(control, record['base'])
            if pins is not None:
                pins['example'] = record['recipe_base']
            shutil.copytree(root/'accepted', destination)
            return destination
        def export(head, destination):
            self.assertEqual(head, record['head'])
            shutil.copytree(root/'human', destination)
            return destination
        def load(namespace, name):
            try:
                return stored[namespace, name]
            except KeyError as error:
                raise ValueError('missing durable receipt') from error
        def save(namespace, name, value):
            if (namespace, name) in stored and stored[namespace, name] != value:
                raise ValueError('immutable state conflict')
            stored[namespace, name] = copy.deepcopy(value)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1',
                                                       'ARCH_WORK': str(root/'authority-work')}))
            for target, attribute, kwargs in (
                (update, 'api', {'side_effect': api}),
                (attestations, 'verify', {'side_effect': verify}),
                (candidates, '_download_candidate_artifact', {'side_effect': download}),
                (update, 'checkout_data', {'side_effect': checkout}),
                (update, 'main_sha', {'return_value': record['base']}),
                (update, 'trusted_checkout_sha', {'return_value': record['base']}),
                (update, 'ref_head', {'return_value': record['recipe_base']}),
                (update, 'status', {}),
                (native, 'run', {'side_effect': AssertionError('authorization must not invoke native harness')}),
                (candidates, 'export_recipe', {'side_effect': export}),
                (recipe_state, 'load', {'side_effect': load}),
                (recipe_state, 'load_optional', {'side_effect': lambda namespace, name: stored.get((namespace, name))}),
                (recipe_state, 'save', {'side_effect': save}),
            ):
                mocked = stack.enter_context(patch.object(target, attribute, **kwargs))
                if target is native and attribute == 'run':
                    native_run = mocked
            yield record, stored, state
            native_run.assert_not_called()

    def test_reviewed_native_candidate_merges_and_defers_receipt_until_real_parent_completion(self):
        record = {'pr_number': 1, 'base': 'c'*40, 'head': 'a'*40,
                  'mechanical': False, 'review_environment': 'recipe-review'}
        with patch.object(update, 'load', return_value=record), patch.object(candidates, 'controller_context', return_value=None), patch.object(candidates, 'validate_build'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(candidates, 'verify_authorization'), patch.object(candidates, 'required_statuses'), patch.object(update, 'api', side_effect=[{'merged': True, 'sha': 'f'*40}, None]) as api, patch('tools.recipe_acceptance.reconcile', side_effect=AssertionError('parent is not completed')):
            result = candidates.finalize(1, record['base'], record['head'], 'candidate.json', 'outputs')
        self.assertTrue(result['merged'])
        self.assertEqual(api.call_args_list[0].args[2], {'sha': record['head'], 'merge_method': 'merge'})
        self.assertIn('/actions/workflows/update.yml/dispatches', api.call_args_list[1].args[0])

    def test_historical_code_review_keeps_actual_environment_history_without_current_reviewers(self):
        record = {'base': 'c'*40, 'head': 'a'*40, 'pr_number': 1, 'review_environment': 'code-review'}
        run = {'head_sha': record['base'], 'head_branch': 'main', 'event': 'workflow_dispatch',
               'path': '.github/workflows/candidate.yml', 'display_title': 'Candidate PR 1 head '+record['head'],
               'run_attempt': 1}
        job = {'id': 71, 'name': f"Review PR 1 head {record['head']} base {record['base']}", 'conclusion': 'success'}
        history = [{'state': 'approved', 'user': {'type': 'User'}, 'environments': [{'id': 31, 'name': 'code-review'}]}]
        proof = {'run_id': '10', 'run_attempt': '1', 'job_id': 71}
        responses = [run, {'jobs': [job], 'total_count': 1}, {'id': 31, 'protection_rules': []}, history]
        with patch.object(update, 'api', side_effect=responses), patch.object(update, 'require_review_environment', side_effect=AssertionError('historical configuration is not authority')):
            self.assertEqual(candidates._authority(record, proof), proof)
        history[0]['environments'][0]['id'] = 99
        with patch.object(update, 'api', side_effect=responses), patch.object(update, 'require_review_environment'), self.assertRaisesRegex(ValueError, 'genuine exact'):
            candidates._authority(record, proof)
        with patch.object(update, 'api', side_effect=[run, {'jobs': [job], 'total_count': 1}]), patch.object(update, 'require_review_environment'), self.assertRaisesRegex(ValueError, 'new human'):
            candidates._authority(record, proof, issuing=True)

    def test_human_review_authorizes_static_inputs_before_any_build(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            state['jobs']['jobs'][0].update(status='in_progress', conclusion=None)
            approved = candidates.review(record)
            self.assertEqual(approved['authorization_kind'], 'human')
            self.assertEqual(approved['job_id'], 71)
            self.assertNotIn(('built', recipe_state.identity_key(record)), stored)
            state['jobs']['jobs'][0].update(status='completed', conclusion='success')
            self.assertEqual(candidates.verify_authorization(record), approved)

    def test_original_approval_survives_artifact_retry_and_current_control_advance(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            approved = candidates.review(record)
            retry = {**record, 'run_id': '20', 'run_attempt': '2'}
            with patch.object(update, 'main_sha', side_effect=AssertionError('original proof must not use current main')), patch.object(update, 'trusted_checkout_sha', side_effect=AssertionError('original proof must not use current checkout')):
                self.assertEqual(candidates.verify_authorization(retry), approved)
            self.assertNotIn(('built', recipe_state.identity_key(record)), stored)

    def test_human_review_retry_preserves_original_immutable_approval(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            original = candidates.review(record)
            with patch.dict(os.environ, {'GITHUB_RUN_ID': '20', 'GITHUB_RUN_ATTEMPT': '2'}):
                self.assertEqual(candidates.review({**record, 'run_id': '20', 'run_attempt': '2'}), original)
            self.assertEqual(stored['approved', recipe_state.identity_key(record)], original)
            self.assertEqual(original['run_id'], '10')
            self.assertEqual(original['run_attempt'], '1')

    def test_authorization_rejects_missing_or_forged_durable_attestation(self):
        for namespace, mutation in [('candidate', 'missing'), ('candidate', 'bot'),
                                    ('approved', 'missing'), ('approved', 'digest')]:
            with self.subTest(namespace=namespace, mutation=mutation), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                context = 'arch-receipt/'+namespace+'/'+recipe_state.identity_key(record)
                row = next(row for row in state['statuses'] if row['context'] == context)
                if mutation == 'missing':
                    state['statuses'].remove(row)
                elif mutation == 'bot':
                    row['creator']['login'] = 'untrusted-user'
                else:
                    row['description'] = 'f'*64
                with self.assertRaisesRegex(ValueError, 'attestation'):
                    candidates.verify_authorization(record)

    def test_original_authorization_rejects_forged_run_and_attempt(self):
        for field, value in [('head_sha', 'f'*40), ('display_title', 'Candidate PR 1 head '+'f'*40),
                             ('run_attempt', 2), ('event', 'pull_request'), ('path', '.github/workflows/other.yml')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                state['run'][field] = value
                with self.assertRaisesRegex(ValueError, 'workflow'):
                    candidates.verify_authorization(record)

    def test_original_authorization_requires_exact_successful_job(self):
        for mutation in ('missing', 'wrong-id', 'failed', 'duplicate'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                job = state['jobs']['jobs'][0]
                if mutation == 'missing':
                    state['jobs']['jobs'] = []
                elif mutation == 'wrong-id':
                    job['id'] = 99
                elif mutation == 'failed':
                    job['conclusion'] = 'failure'
                else:
                    state['jobs']['jobs'].append(copy.deepcopy(job))
                with self.assertRaisesRegex(ValueError, 'job|checkpoint'):
                    candidates.verify_authorization(record)

    def test_original_authorization_requires_genuine_exact_environment(self):
        for mutation in ('missing', 'wrong-id', 'bot', 'unprotected'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                if mutation == 'missing':
                    state['history'] = []
                elif mutation == 'wrong-id':
                    state['history'][0]['environments'][0]['id'] = 99
                elif mutation == 'bot':
                    state['history'][0]['user']['type'] = 'Bot'
                else:
                    state['environment']['protection_rules'] = []
                with self.assertRaisesRegex(ValueError, 'approval|protection'):
                    candidates.verify_authorization(record)

    def test_original_authorization_rechecks_immutable_source_and_tree(self):
        for mutation in ('head', 'source', 'tree', 'payload', 'controller'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                if mutation == 'head':
                    record = {**record, 'head': 'f'*40}
                elif mutation == 'source':
                    stored['proposal', record['proposal_head']]['lock']['version'] = '99-1'
                elif mutation == 'tree':
                    state['tree'] = 'f'*40
                elif mutation == 'payload':
                    (Path(work)/'human'/'fix.patch').write_bytes(b'forged source bytes')
                else:
                    (Path(work)/'accepted'/'build-image.txt').write_text('forged controller image')
                with self.assertRaises(ValueError):
                    candidates.verify_authorization(record)

    def test_automatic_authorization_recomputes_policy_instead_of_trusting_flag(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            record['mechanical'] = True
            record['review_environment'] = ''
            key = recipe_state.identity_key(record)
            stored['candidate', key] = candidates.frozen_record(record)
            state['statuses'][0]['description'] = recipe_state.digest(stored['candidate', key])
            state['jobs']['jobs'][0]['name'] = f"Authorize PR 1 head {record['head']} base {record['base']}"
            self.candidate_transport(record, state)
            # Flags cannot authorize the dependency/build-code edit, even with
            # a genuine automatic checkpoint and independently frozen payload.
            with self.assertRaisesRegex(ValueError, 'PKGBUILD'):
                candidates.authorize(record)
            self.assertNotIn(('approved', key), stored)

    def test_durable_source_rejects_full_subject_signer_and_bundle_substitution(self):
        mutations = [
            ('base', 'f'*40), ('head', 'f'*40), ('run_id', '20'), ('run_attempt', '2'),
            ('repository', 'other/repo'), ('packages', []),
            ('provenance', {'forged': True})]
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                candidates.review(record)
                stored['candidate-provenance', recipe_state.identity_key(record)]['candidate'][field] = value
                with self.assertRaises(ValueError):
                    candidates.verify_authorization(record)
        for field, value in [('path', '.github/workflows/build-package.yml'),
                             ('repository', 'other/repo'), ('head_sha', 'f'*40),
                             ('run_id', '20'), ('run_attempt', '2')]:
            with self.subTest(signer=field), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                stored['candidate-provenance', recipe_state.identity_key(record)]['signer'][field] = value
                with self.assertRaises(ValueError):
                    candidates.review(record)
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            stored['candidate-provenance', recipe_state.identity_key(record)]['bundle']['dsseEnvelope']['payload'] = 'substituted'
            with self.assertRaisesRegex(ValueError, 'crypto verification'):
                candidates.review(record)

    def test_new_authorization_requires_prebuild_crypto_despite_alive_bot_artifact(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            del stored['candidate-provenance', recipe_state.identity_key(record)]
            with self.assertRaisesRegex(ValueError, 'missing durable candidate provenance'):
                candidates.review(record)
            self.assertNotIn(('approved', recipe_state.identity_key(record)), stored)

    def test_persist_original_provenance_and_retry_never_rewrite_original_run(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            key = recipe_state.identity_key(record)
            original = stored.pop(('candidate-provenance', key))
            subject, bundle = Path(work)/'subject.json', Path(work)/'bundle.json'
            update.dump(subject, record)
            update.dump(bundle, original['bundle'])
            proof = candidates.persist_candidate_provenance(subject, bundle)
            self.assertEqual(proof, original)
            retry = {**record, 'run_id': '20', 'run_attempt': '2'}
            update.dump(subject, retry)
            bundle.unlink()
            self.assertEqual(candidates.persist_candidate_provenance(subject, bundle), original)
            self.assertEqual(stored['candidate-provenance', key]['candidate']['run_id'], '10')
            self.assertTrue(candidates.candidate_provenance_exists(retry))

    def test_persist_provenance_rejects_noncanonical_subject_and_unfrozen_inputs(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            original = stored.pop(('candidate-provenance', recipe_state.identity_key(record)))
            subject, bundle = Path(work)/'subject.json', Path(work)/'bundle.json'
            update.dump(bundle, original['bundle'])
            subject.write_text(json.dumps(record, indent=2))
            with self.assertRaisesRegex(ValueError, 'canonical'):
                candidates.persist_candidate_provenance(subject, bundle)
            update.dump(subject, {**record, 'packages': []})
            with self.assertRaisesRegex(ValueError, 'frozen'):
                candidates.persist_candidate_provenance(subject, bundle)
            self.assertNotIn(('candidate-provenance', recipe_state.identity_key(record)), stored)

    def test_provenance_bundle_is_bounded_before_crypto_verification(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            proof = stored['candidate-provenance', recipe_state.identity_key(record)]
            proof['bundle'] = {'oversized': 'x' * candidates.MAX_CANDIDATE_BUNDLE}
            with patch.object(attestations, 'verify', side_effect=AssertionError('oversized bundle must not reach crypto')):
                with self.assertRaisesRegex(ValueError, 'bundle exceeds bound'):
                    candidates.verify_candidate_provenance(record)

    def test_existing_provenance_is_reverified_not_treated_as_transport_authority(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            proof = stored['candidate-provenance', recipe_state.identity_key(record)]
            proof['bundle'] = {}
            with self.assertRaisesRegex(ValueError, 'crypto verification'):
                candidates.candidate_provenance_exists(record)

    def test_copied_genuine_approval_cannot_authorize_forged_frozen_source(self):
        with tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
            original = candidates.review(record)
            forged = {**record, 'decisions': [{'decision': 'human', 'reason': 'forged approval scope'}]}
            key = recipe_state.identity_key(forged)
            stored['candidate', key] = candidates.frozen_record(forged)
            stored['approved', key] = {**original, 'candidate_digest': candidates.candidate_digest(forged)}
            for namespace in ('candidate', 'approved'):
                context = 'arch-receipt/'+namespace+'/'+key
                state['statuses'][:] = [row for row in state['statuses'] if row['context'] != context]
                state['statuses'].append({'context': context, 'state': 'success',
                                          'description': recipe_state.digest(stored[namespace, key]),
                                          'creator': {'login': 'github-actions[bot]'}})
            with self.assertRaisesRegex(ValueError, 'source differs'):
                candidates.verify_authorization(forged)

    def test_durable_prebuild_source_survives_expiry_without_any_built_receipt(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as work, self.authorization_fixture(work) as (record, stored, state):
                approved = candidates.review(record)
                if missing:
                    state['artifacts'] = []
                else:
                    state['artifacts'][0]['expired'] = True
                self.assertNotIn(('built', recipe_state.identity_key(record)), stored)
                with patch.object(candidates, '_download_candidate_artifact', side_effect=AssertionError('durable proof must not download artifacts')):
                    self.assertEqual(candidates.verify_authorization(record), approved)
                del stored['candidate-provenance', recipe_state.identity_key(record)]
                with self.assertRaisesRegex(ValueError, 'provenance missing'):
                    candidates.verify_authorization(record)

    def record(self, mechanical=False):
        return {'kind': 'recipe', 'repository': 'owner/repo', 'pkgbase': 'example', 'watcher_id': 'release', 'head_branch': 'recipe-updates/example/release', 'receipt_id': 'e'*64, 'pr_number': 1, 'base': 'c'*40, 'recipe_base': 'b'*40, 'head': 'a'*40, 'recipe_tree': 'd'*40, 'mechanical': mechanical, 'review_environment': '' if mechanical else 'recipe-review'}

    def test_retry_preserves_approval_identity_but_head_edits_invalidate_it(self):
        first = {**self.record(), 'run_id': '10', 'run_attempt': '1'}
        retry = {**first, 'run_id': '20', 'run_attempt': '2'}
        self.assertEqual(candidates.candidate_digest(first), candidates.candidate_digest(retry))
        retry['head'] = 'f'*40
        self.assertNotEqual(candidates.candidate_digest(first), candidates.candidate_digest(retry))

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
            with patch.object(candidates, 'verify_authorization'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)), patch.object(recipe_state, 'save') as save, patch.object(update, 'status') as status:
                self.assertEqual(candidates.validate_build(record, directory, report=False), evidence)
            save.assert_not_called()
            status.assert_not_called()

    def test_native_build_rejects_corrupted_package_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, _, filename = self.build_fixture(directory)
            (directory/filename).write_bytes(b'forged bytes')
            with patch.object(candidates, 'verify_authorization'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)):
                with self.assertRaisesRegex(ValueError, 'bytes/version'):
                    candidates.validate_build(record, directory, report=False)

    def test_native_build_rejects_extra_package_outputs(self):
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            record, evidence, _ = self.build_fixture(directory)
            extra = {**evidence['packages'][0]['files'][0], 'name': 'unenrolled'}
            evidence['packages'][0]['files'].append(extra)
            update.dump(directory/'native-evidence.json', evidence)
            with patch.object(candidates, 'verify_authorization'), patch.object(recipe_state, 'assert_recipe_identity'), patch.object(recipe_state, 'load', return_value=candidates.frozen_record(record)):
                with self.assertRaisesRegex(ValueError, 'extra'):
                    candidates.validate_build(record, directory, report=False)

    def release_edit_fixture(self, *, grouped=True, prefix=''):
        old_version, new_version = prefix+'1.0', prefix+'2.0'
        source_ids = ['code', 'font'] if grouped else ['code']
        previous = {'version': old_version+'-1', 'sources': [
            {'id': source_id, 'kind': 'release',
             'url': 'https://example.test/'+old_version+'/'+source_id+'.zip',
             'release_id': 10, 'asset_id': index+20}
            for index, source_id in enumerate(source_ids)]}
        edited = copy.deepcopy(previous)
        edited['version'] = '3:'+new_version+'-2'
        for source in edited['sources']:
            source['url'] = 'https://example.test/'+new_version+'/'+source['id']+'.zip'
        watcher = {'id': 'release', 'kind': 'release', 'source_id': 'code',
                   'related_source_ids': source_ids[1:],
                   'url': 'https://example.test/repo.git', 'repository': 'owner/repo',
                   'tag_pattern': r'^v([0-9]+)\.([0-9]+)$',
                   'accepted_tag': 'v1.0', 'accepted_tag_object': 'a'*40,
                   'accepted_peeled_commit': 'a'*40, 'release_id': 10, 'asset_id': 20,
                   'version_prefix': prefix,
                   'asset_templates': {source_id: source_id+'-{version}.zip' for source_id in source_ids}}
        release = {'id': 11, 'draft': False, 'prerelease': False, 'assets': [
            {'id': index+30, 'name': source_id+'-'+new_version+'.zip',
             'browser_download_url': source['url']}
            for index, (source_id, source) in enumerate(zip(source_ids, edited['sources']))]}
        return previous, edited, {'aur': None, 'watchers': [watcher]}, release

    def test_edited_grouped_release_authenticates_distinct_native_version_assets(self):
        previous, edited, provenance, release = self.release_edit_fixture(prefix='B')
        rows = 'b'*40+'\trefs/tags/v2.0\n'+'c'*40+'\trefs/tags/v2.0^{}\n'
        with patch.object(update.sources, 'git', return_value=rows), patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
            result, selected = candidates.manual_provenance(previous, edited, provenance)
            for source in edited['sources']:
                update._live_tag_provenance(provenance['watchers'][0], result['watchers'][0], source)
        self.assertEqual(selected, ['release'])
        self.assertEqual(result['watchers'][0]['accepted_tag'], 'v2.0')
        self.assertEqual(result['watchers'][0]['asset_id'], 30)
        self.assertEqual([(s['release_id'], s['asset_id']) for s in edited['sources']], [(11, 30), (11, 31)])

    def test_edited_related_release_alone_retains_primary_authentication(self):
        previous, edited, provenance, release = self.release_edit_fixture()
        edited['version'] = previous['version']
        edited['sources'][0] = copy.deepcopy(previous['sources'][0])
        release['assets'][0]['browser_download_url'] = edited['sources'][0]['url']
        for asset in release['assets']:
            asset['name'] = asset['name'].replace('2.0', '1.0')
        rows = 'a'*40+'\trefs/tags/v1.0\n'
        with patch.object(update.sources, 'git', return_value=rows), patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
            result = candidates.edited_provenance(previous, edited, provenance, 'release')
        self.assertEqual(result['watchers'][0]['asset_id'], 30)
        self.assertEqual(edited['sources'][1]['asset_id'], 31)

    def test_edited_release_rejects_wrong_or_duplicate_assets_and_urls(self):
        for mutation in ('name', 'url', 'duplicate', 'shared_id', 'draft', 'prerelease'):
            with self.subTest(mutation=mutation):
                previous, edited, provenance, release = self.release_edit_fixture()
                if mutation == 'name':
                    release['assets'][1]['name'] = 'unenrolled.zip'
                elif mutation == 'url':
                    release['assets'][1]['browser_download_url'] = 'https://example.test/wrong.zip'
                elif mutation == 'duplicate':
                    release['assets'].append(copy.deepcopy(release['assets'][1]))
                elif mutation == 'shared_id':
                    release['assets'][1]['id'] = release['assets'][0]['id']
                else:
                    release[mutation] = True
                with patch.object(update.sources, 'git', return_value='b'*40+'\trefs/tags/v2.0\n'), patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
                    with self.assertRaises(ValueError):
                        candidates.edited_provenance(previous, edited, provenance, 'release')

    def test_edited_release_identity_checks_cover_each_related_asset(self):
        previous, edited, provenance, release = self.release_edit_fixture()
        with patch.object(update.sources, 'git', return_value='b'*40+'\trefs/tags/v2.0\n'), patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
            result = candidates.edited_provenance(previous, edited, provenance, 'release')
            for source_index in (0, 1):
                for field in ('release_id', 'asset_id', 'url'):
                    with self.subTest(source=source_index, field=field):
                        forged = copy.deepcopy(edited['sources'][source_index])
                        forged[field] = 'wrong'
                        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                            update._live_tag_provenance(provenance['watchers'][0], result['watchers'][0], forged)

    def test_edited_release_rejects_ambiguous_related_watcher(self):
        previous, edited, provenance, _ = self.release_edit_fixture()
        other = copy.deepcopy(provenance['watchers'][0])
        other.update(id='other', source_id='font', related_source_ids=[])
        provenance['watchers'].append(other)
        for derive in (
                lambda: candidates.manual_provenance(previous, edited, provenance),
                lambda: candidates.edited_provenance(previous, edited, provenance, 'release')):
            with self.assertRaisesRegex(ValueError, 'ambiguous watcher'):
                derive()

    def test_edited_literal_release_asset_contract_is_unchanged(self):
        previous, edited, provenance, release = self.release_edit_fixture(grouped=False)
        watcher = provenance['watchers'][0]
        del watcher['asset_templates']
        watcher['asset'] = 'code.zip'
        release['assets'][0]['name'] = 'code.zip'
        with patch.object(update.sources, 'git', return_value='b'*40+'\trefs/tags/v2.0\n'), patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
            result = candidates.edited_provenance(previous, edited, provenance, 'release')
            update._live_tag_provenance(watcher, result['watchers'][0], edited['sources'][0])
            release['assets'][0]['name'] = 'code-2.0.zip'
            with patch.object(update.sources, 'fetch', return_value=json.dumps(release)):
                with self.assertRaisesRegex(ValueError, 'exact authentic asset'):
                    candidates.edited_provenance(previous, edited, provenance, 'release')

    def test_edited_release_rejects_missing_native_version_prefix(self):
        previous, edited, provenance, _ = self.release_edit_fixture(prefix='B')
        edited['version'] = '2.0-1'
        with self.assertRaisesRegex(ValueError, 'version prefix'):
            candidates.edited_provenance(previous, edited, provenance, 'release')

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
