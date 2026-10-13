import copy
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from tests import test_imports, test_recipe_acceptance
from tools import imports, recipe_acceptance, recipe_intents, recipe_state, sources, update


class CandidateIntentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_imports.PendingImports()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.stage_aur_revision()
        f = self.fixture
        # This accepted predecessor has no declared sources; the new AUR
        # intention is prospective and must not replace that accepted state.
        policy = copy.deepcopy(f.policy)
        policy.update(authority='local', sources=[], native_verification='verified-github-native-probe')
        self.accepted = {'schema': 1, 'packages': [policy], 'imports': []}
        self.oldlock = {'schema': 1, 'version': '1-1', 'sources': []}
        self.oldpro = {'schema': 1, 'pkgbase': 'example', 'aur': None, 'watchers': []}
        prospective = imports.registry(f.new)[2]['example']['policy']
        self.intent = {'schema': 1, 'pkgbase': 'example', 'predecessor': f.commit,
                       'policy': prospective,
                       'lock': update.load(f.new/'inputs/example.json'),
                       'provenance': update.load(f.new/'upstream/example.json')}
        for root in (f.old, f.new):
            update.dump(root/'packages.json', self.accepted)
            update.dump(root/'inputs/example.json', self.oldlock)
            update.dump(root/'upstream/example.json', self.oldpro)
        update.dump(f.new/'intents/example.json', self.intent)
        self.pins = {'example': f.commit}

    def test_authenticated_intent_does_not_replace_accepted_state_or_execute_recipes(self):
        f = self.fixture
        names = recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'intent-proof')
        self.assertEqual(names, ['example'])
        self.assertEqual(update.load(f.new/'inputs/example.json'), {'schema': 1, 'version': '1-1', 'sources': []})
        self.assertEqual(imports.policies(f.new)['example']['authority'], 'local')
        self.assertEqual(imports.policies(f.new)['example']['outputs'], [{'name': 'example', 'arch': 'x86_64'}])
        self.assertEqual(recipe_intents.load(f.new, 'example', f.commit)['policy']['outputs'], [{'name': 'example', 'arch': 'any'}])
        self.assertFalse((f.root/'EXECUTED').exists())
        self.assertFalse((f.root/'AUR_EXECUTED').exists())

    def test_stock_intent_cannot_omit_all_authenticated_native_sources(self):
        f = self.fixture
        intent = copy.deepcopy(self.intent)
        intent['policy']['sources'] = []
        intent['lock']['sources'] = []
        update.dump(f.new/'intents/example.json', intent)
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'omitted-all')

    def test_stock_intent_cannot_omit_one_authenticated_native_source(self):
        f = self.fixture
        aur = f.root/'aur'
        extra = b'another native source'
        (aur/'extra.patch').write_bytes(extra)
        text = (aur/'.SRCINFO').read_text()
        checksum = sources.new_hash('sha512', extra).hexdigest()
        text = text.replace('\tsource = fix.patch\n', '\tsource = fix.patch\n\tsource = extra.patch\n')
        text = text.replace('\npkgname = example', '\n\tsha512sums = '+checksum+'\npkgname = example')
        (aur/'.SRCINFO').write_text(text)
        sources.git('add', '.', cwd=aur)
        sources.git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-m', 'second declared source', cwd=aur)
        commit = sources.git('rev-parse', 'HEAD', cwd=aur)
        sources.git('branch', '-f', 'example', commit, cwd=aur)
        intent = copy.deepcopy(self.intent)
        intent['provenance']['aur']['commit'] = commit
        intent['provenance']['watchers'][0]['baseline_commit'] = commit
        update.dump(f.new/'intents/example.json', intent)
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'omitted-one')

    def test_complete_indexed_stock_source_enrollment_is_authenticated(self):
        f = self.fixture
        intent = copy.deepcopy(self.intent)
        intent['policy']['sources'][0]['native_source_index'] = 0
        update.dump(f.new/'intents/example.json', intent)
        self.assertEqual(recipe_intents.validate_admission(
            f.old, f.new, self.pins, self.pins, f.root/'complete-indexed'), ['example'])

    def test_stock_source_enrollment_cannot_duplicate_native_coverage(self):
        f = self.fixture
        intent = copy.deepcopy(self.intent)
        definition = copy.deepcopy(intent['policy']['sources'][0])
        definition.update(id='duplicate', native_source_index=0)
        source = copy.deepcopy(intent['lock']['sources'][0])
        source['id'] = 'duplicate'
        intent['policy']['sources'][0]['native_source_index'] = 0
        intent['policy']['sources'].append(definition)
        intent['lock']['sources'].append(source)
        update.dump(f.new/'intents/example.json', intent)
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(
                f.old, f.new, self.pins, self.pins, f.root/'duplicate-coverage')

    def test_intent_is_bound_to_exact_accepted_predecessor(self):
        f = self.fixture
        with self.assertRaises(ValueError):
            recipe_intents.load(f.new, 'example', '0'*40)
        changed = {**self.intent, 'predecessor': '0'*40}
        update.dump(f.new/'intents/example.json', changed)
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'wrong-predecessor')

    def test_authenticated_intent_revision_preserves_accepted_state(self):
        f = self.fixture
        revised = f.root/'revised'
        shutil.copytree(f.new, revised)
        intent = copy.deepcopy(self.intent)
        intent['policy']['authority'] = 'maintained'
        update.dump(revised/'intents/example.json', intent)
        self.assertEqual(recipe_intents.validate_admission(
            f.new, revised, self.pins, self.pins, f.root/'intent-revision'), ['example'])
        self.assertEqual(update.load(revised/'inputs/example.json'), self.oldlock)
        self.assertEqual(update.load(revised/'packages.json'), self.accepted)
        self.assertFalse((f.root/'EXECUTED').exists())
        self.assertFalse((f.root/'AUR_EXECUTED').exists())

    def test_aur_release_advance_preserves_authenticated_baseline(self):
        f = self.fixture
        intent = copy.deepcopy(self.intent)
        intent['lock']['version'] = '1-3'
        update.dump(f.new/'intents/example.json', intent)
        self.assertEqual(recipe_intents.validate_admission(
            f.old, f.new, self.pins, self.pins, f.root/'release-advance'), ['example'])
        self.assertEqual(recipe_intents.load(f.new, 'example', f.commit)['lock']['version'], '1-3')

    def test_necessary_maintained_architecture_patch_keeps_aur_baseline(self):
        f = self.fixture
        intent = copy.deepcopy(self.intent)
        intent['policy']['authority'] = 'maintained'
        intent['policy']['outputs'][0]['arch'] = 'x86_64'
        update.dump(f.new/'intents/example.json', intent)
        self.assertEqual(recipe_intents.validate_admission(
            f.old, f.new, self.pins, self.pins, f.root/'native-architecture'), ['example'])
        self.assertFalse((f.root/'EXECUTED').exists())
        self.assertFalse((f.root/'AUR_EXECUTED').exists())

    def test_intent_main_route_is_automatic_metadata_with_zero_workers(self):
        f = self.fixture
        repo = Path(update.__file__).parents[1]
        for root in (f.old, f.new):
            harness_files = {'tools/build.sh', 'tools/native.py', 'tools/sources.py',
                             'tools/recipe_gate.py', 'tools/github_api.py',
                             'tools/dependency_repo.py', 'keys/arch-packages.asc', 'keys/n3t.asc'}
            for filename in set(recipe_state.CONTROLS) | harness_files:
                target = root/filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo/filename, target)
            (root/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'f'*64)
        def checkout(sha, destination, pins, selected=None):
            shutil.copytree(f.old if sha == 'a'*40 else f.new, destination)
            pins.update(self.pins)
            return destination
        pr = {'base': {'ref': 'main'}, 'head': {'repo': {'full_name': 'owner/repo'}}}
        output = f.root/'route'; output.mkdir()
        with patch.object(update, 'checkout_data', side_effect=checkout), \
                patch.object(update, 'status'), \
                patch.object(recipe_acceptance, 'validate_bookkeeping', return_value=None):
            record = update._prepare_source(pr, 1, 'a'*40, 'b'*40, output)
        self.assertEqual(record['packages'], [])
        self.assertEqual(record['merge_policy'], 'automatic')
        self.assertEqual(record['recipe_pins'], self.pins)
        self.assertEqual(record['previous_recipe_pins'], self.pins)
        self.assertFalse((f.root/'EXECUTED').exists())
        self.assertFalse((f.root/'AUR_EXECUTED').exists())

    def test_aur_baseline_must_belong_to_selected_canonical_branch(self):
        f = self.fixture
        # The object exists at the canonical remote, but the selected branch
        # starts a different history. Object availability is not membership.
        sources.git('checkout', '--orphan', 'unrelated', cwd=f.root/'aur')
        sources.git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-m', 'unrelated history', cwd=f.root/'aur')
        sources.git('branch', '-f', 'example', 'HEAD', cwd=f.root/'aur')
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'nonmember')

    def test_load_rejects_symlink_and_pending_name(self):
        f = self.fixture
        target = f.new/'intents/example.json'
        target.unlink()
        target.symlink_to(f.old/'inputs/example.json')
        with self.assertRaises(ValueError):
            recipe_intents.load(f.new, 'example', f.commit)
        target.unlink()
        update.dump(target, self.intent)
        update.dump(f.new/'packages.json', {'schema': 1, 'packages': [], 'imports': []})
        with self.assertRaises(ValueError):
            recipe_intents.load(f.new, 'example', f.commit)

    def test_consumption_replaces_only_policy_and_deletes_only_consumed_intent(self):
        registry = copy.deepcopy(self.accepted)
        registry['packages'].append({'pkgbase': 'other', 'authority': 'local'})
        registry['annotation'] = 'preserved'
        activated = recipe_intents.activate(registry, self.intent)
        self.assertEqual(activated['packages'], [self.intent['policy'], registry['packages'][1]])
        self.assertEqual(activated['annotation'], 'preserved')
        self.assertEqual(registry['packages'][0]['authority'], 'local')
        leaves = {'intents/example.json': ('100644', 'blob', 'a'*40),
                  'intents/other.json': ('100644', 'blob', 'b'*40)}
        receipt = {'pkgbase': 'example', 'accepted': 'c'*40, 'lock': self.intent['lock'],
                   'provenance': self.intent['provenance'], 'applied_intent': self.intent,
                   'activated_registry': activated}
        result = recipe_acceptance.expected_leaves(leaves, receipt)
        self.assertNotIn('intents/example.json', result)
        self.assertEqual(result['intents/other.json'], leaves['intents/other.json'])
        self.assertEqual(result['packages.json'][2], recipe_acceptance.blob_sha(activated))

    def test_authenticated_intent_acceptance_consumes_exact_transition_and_original_output(self):
        f = self.fixture
        C, H, A, T = (value*40 for value in 'cdae')
        B = f.commit
        registry = copy.deepcopy(self.accepted)
        registry['packages'].append({'pkgbase': 'other', 'authority': 'local'})
        registry['annotation'] = 'untouched'
        update.dump(f.new/'packages.json', registry)
        unrelated_intent = {'unrelated': 'preserved verbatim'}
        update.dump(f.new/'intents/other.json', unrelated_intent)
        candidate, evidence = test_recipe_acceptance.SuccessfulOutputAcceptance().fixture()
        srcinfo = (f.root/'aur/.SRCINFO').read_text()
        package = candidate['packages'][0]
        package.update(pkgbase='example', recipe_commit=H, lock=copy.deepcopy(self.intent['lock']),
                       expected_srcinfo=srcinfo, policy=copy.deepcopy(self.intent['policy']))
        candidate.update(pkgbase='example', pr_number=7, request_id='example-original',
                         recipe_base=B, recipe_tree=T, head_branch='proposal/example',
                         recipe_pins={'example': H}, previous_recipe_pins={'example': B},
                         intent=copy.deepcopy(self.intent), predecessor_lock=self.oldlock,
                         predecessor_provenance=self.oldpro, provenance=self.intent['provenance'],
                         proposal_origin='manual', receipt_id='original-receipt', watcher_ids=[],
                         merge_policy='human')
        evidence = {key: candidate[key] for key in
                    ('schema', 'repository', 'base', 'head', 'recipe_pins', 'previous_recipe_pins', 'image')}
        evidence.update(run_id='11', run_attempt='1', packages=[{
            'pkgbase': 'example', 'recipe_commit': H, 'request_input_digest': package['input_digest'],
            'input_digest': 'e'*64, 'source_lock': copy.deepcopy(self.intent['lock']),
            'metadata': {'srcinfo': srcinfo, 'dynamic_pkgver': False},
            'files': [{'name': 'example', 'arch': 'any', 'version': '1-2',
                       'filename': 'example-1-2-any.pkg.tar.zst'}]}])
        producer = {'run_id': '11', 'run_attempt': '1', 'head_sha': C}
        descriptor = {'record': candidate, 'producer': producer, 'evidence': evidence}
        proposal = {'pkgbase': 'example', 'recipe_head': H, 'recipe_base': B,
                    'base': C, 'proposal_origin': 'manual'}
        pending = {'head': H, 'request_id': candidate['request_id'], 'state': 'success'}
        stored = {('proposal', H): proposal, ('pending', 'example'): pending,
                  ('build-request', candidate['request_id']): candidate,
                  ('build-result', candidate['request_id']): descriptor}
        snapshot = SimpleNamespace(load=lambda namespace, key: stored[namespace, key],
                                   load_optional=lambda namespace, key: stored.get((namespace, key)))
        pr = {'number': 7, 'state': 'closed', 'merged': True, 'merged_at': '2026-01-01',
              'merge_commit_sha': A,
              'base': {'ref': 'pkg/example', 'sha': B, 'repo': {'full_name': 'owner/repo'}},
              'head': {'ref': candidate['head_branch'], 'sha': H, 'repo': {'full_name': 'owner/repo'}}}
        worker = {'id': 11, 'run_attempt': 1, 'head_sha': C, 'status': 'completed',
                  'conclusion': 'success', 'path': '.github/workflows/build-package.yml@main',
                  'head_branch': 'main', 'event': 'workflow_dispatch',
                  'repository': {'full_name': 'owner/repo'}}
        def api(route):
            if route.endswith('/pulls/7'):
                return pr
            if route.endswith('/branches/pkg%2Fexample'):
                return {'protected': True}
            if route.endswith('/git/commits/'+H):
                return {'sha': H, 'tree': {'sha': T}}
            if route.endswith('/git/commits/'+A):
                return {'sha': A, 'tree': {'sha': T}, 'parents': [{'sha': B}, {'sha': H}]}
            if '/statuses?' in route:
                return [{'context': context, 'state': 'success'}
                        for context in ('verify', 'candidate-build', 'recipe-policy')]
            if route.endswith('/actions/runs/11/attempts/1'):
                return worker
            self.fail('unexpected GitHub boundary: '+route)
        def checkout(sha, destination, pins, selected=None):
            self.assertEqual(sha, C)
            shutil.copytree(f.new, destination)
            pins.update(self.pins)
            return destination
        with patch.object(update, 'api', side_effect=api), \
                patch.object(update, 'checkout_data', side_effect=checkout), \
                patch.object(update, 'main_sha', return_value=C), \
                patch.object(update, 'trusted_checkout_sha', return_value=C), \
                patch.object(update, 'ref_head', return_value=A), \
                patch.object(recipe_state, 'StateSnapshot', return_value=snapshot):
            receipt = recipe_acceptance.accepted_receipt(7, C)
            self.assertEqual(receipt['policy'], self.intent['policy'])
            self.assertNotEqual(receipt['policy'], registry['packages'][0])
            self.assertEqual(receipt['lock'], self.intent['lock'])
            self.assertNotEqual(receipt['lock']['sources'], self.oldlock['sources'])
            self.assertEqual(receipt['policy']['outputs'], [{'name': 'example', 'arch': 'any'}])
            self.assertEqual(receipt['build'], descriptor)
            self.assertEqual(receipt['build']['producer'], producer)
            self.assertEqual(receipt['native_runs']['package_runs'][0]['run_id'], '11')
            before = {'packages.json': ('100644', 'blob', recipe_acceptance.blob_sha(registry)),
                      'recipes/example': ('160000', 'commit', B),
                      'inputs/example.json': ('100644', 'blob', recipe_acceptance.blob_sha(self.oldlock)),
                      'upstream/example.json': ('100644', 'blob', recipe_acceptance.blob_sha(self.oldpro)),
                      'intents/example.json': ('100644', 'blob', recipe_acceptance.blob_sha(self.intent)),
                      'intents/other.json': ('100644', 'blob', recipe_acceptance.blob_sha(unrelated_intent)),
                      'tools/unrelated.py': ('100644', 'blob', 'f'*40)}
            after = recipe_acceptance.expected_leaves(before, receipt)
            self.assertEqual(after['recipes/example'], ('160000', 'commit', A))
            self.assertEqual(after['inputs/example.json'][2], recipe_acceptance.blob_sha(self.intent['lock']))
            self.assertEqual(after['upstream/example.json'][2], recipe_acceptance.blob_sha(self.intent['provenance']))
            self.assertEqual(after['acceptance/example.json'][2], recipe_acceptance.blob_sha(receipt))
            self.assertEqual(after['packages.json'][2], recipe_acceptance.blob_sha(receipt['activated_registry']))
            self.assertNotIn('intents/example.json', after)
            for path in ('intents/other.json', 'tools/unrelated.py'):
                self.assertEqual(after[path], before[path])
            self.assertEqual(receipt['activated_registry']['packages'][1], registry['packages'][1])
            self.assertEqual(receipt['activated_registry']['annotation'], 'untouched')
            # An authenticated policy revision must not consume the earlier worker.
            revised = copy.deepcopy(self.intent)
            revised['policy']['authority'] = 'maintained'
            update.dump(f.new/'intents/example.json', revised)
            with self.assertRaisesRegex(ValueError, 'policy differs'):
                recipe_acceptance.accepted_receipt(7, C)
            # A valid same-policy intention with a different lock is also stale.
            revised = copy.deepcopy(self.intent)
            revised['lock']['version'] = '1-3'
            update.dump(f.new/'intents/example.json', revised)
            with self.assertRaisesRegex(ValueError, 'current exact intention'):
                recipe_acceptance.accepted_receipt(7, C)
            update.dump(f.new/'intents/example.json', self.intent)
            worker['conclusion'] = 'failure'
            with self.assertRaisesRegex(ValueError, 'entire original worker'):
                recipe_acceptance.accepted_receipt(7, C)
        self.assertEqual(candidate['intent'], self.intent)
        self.assertEqual(descriptor['producer'], producer)
        self.assertFalse((f.root/'EXECUTED').exists())
        self.assertFalse((f.root/'AUR_EXECUTED').exists())

    def test_metadata_admission_cannot_also_modify_accepted_sources_or_code(self):
        f = self.fixture
        update.dump(f.new/'inputs/example.json', self.intent['lock'])
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'rewritten-accepted-input')
        update.dump(f.new/'inputs/example.json', self.oldlock)
        (f.new/'recipes/example/PKGBUILD').write_text('pkgname=example\npkgver=2\npkgrel=1\n')
        with self.assertRaises(ValueError):
            recipe_intents.validate_admission(f.old, f.new, self.pins, self.pins, f.root/'rewritten-recipe')
