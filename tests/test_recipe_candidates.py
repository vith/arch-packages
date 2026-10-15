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

from tools import attestations, build_store, imports, native, recipe_candidates as candidates, recipe_state, update


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


    def prepare_manual(self, work, invalid=False, pkgrel='2', accepted_version='1-1', dynamic=False, count_git=False, executable_pkgver=False, declared_mismatch=False, pending=False, legacy_pkgrel='1', pending_aur=True, conflict_resolution=False):
        root = Path(work)
        old, recipe = root/'accepted', root/'human'
        for directory in (old/'recipes/example', old/'inputs', old/'upstream', recipe):
            directory.mkdir(parents=True)
        before, after = b'old patch\n', b'human patch\n'
        def write_recipe(directory, data, dependency, release):
            checksum = hashlib.sha256(data).hexdigest()
            (directory/'fix.patch').write_bytes(data)
            (directory/'PKGBUILD').write_text("pkgname=example\npkgver=1\npkgrel=" + release + "\narch=('x86_64')\nsource=('fix.patch')\nsha256sums=('" + checksum + "')\ndepends=('" + dependency + "')\npackage() { install -Dm644 fix.patch \"$pkgdir/usr/share/example/fix.patch\"; }\n")
            source = 'other.patch' if invalid and directory == recipe else 'fix.patch'
            text = 'pkgbase = example\n\tpkgver = 1\n\tpkgrel = ' + release + '\n\tarch = x86_64\n\tsource = ' + source + '\n\tsha256sums = ' + checksum + '\npkgname = example\n\tdepends = ' + dependency + '\n'
            (directory/'.SRCINFO').write_text(text)
        write_recipe(old/'recipes/example', before, 'original', legacy_pkgrel)
        write_recipe(recipe, after, 'human-dependency', pkgrel)
        if dynamic:
            for directory in (old/'recipes/example', recipe):
                with (directory/'PKGBUILD').open('a') as handle:
                    handle.write('pkgver() { printf 2; }\n')
        source = {key: None for key in update.sources.FIELDS}
        source.update(id='patch', kind='local', source='fix.patch', checksums={'sha256': hashlib.sha256(before).hexdigest()})
        lock = {'schema': 1, 'version': accepted_version, 'sources': [source]}
        provenance = {'aur': None, 'watchers': []}
        policy = {'pkgbase': 'example', 'sources': [{'id': 'patch', 'kind': 'local', 'mutable': False, 'source_template': 'fix.patch', 'checksum_algorithm': 'sha256', 'checksum_index': 0}], 'outputs': [{'name': 'example', 'arch': 'x86_64'}]}
        if count_git:
            git = update.sources.git
            remote = root/'count-source'
            git('init', remote)
            git('config', 'user.email', 'fixture@example.invalid', cwd=remote)
            git('config', 'user.name', 'Fixture', cwd=remote)
            git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=remote)
            versions = []
            for number in (1, 2):
                (remote/'payload').write_text(str(number))
                git('add', 'payload', cwd=remote)
                git('commit', '-m', str(number), cwd=remote)
                versions.append('r' + str(number) + '.' + git('rev-parse', '--short=7', 'HEAD', cwd=remote))
            native_source = 'git+' + str(remote) + '#branch=main'
            with patch.object(update.sources, 'public_url', lambda value: value):
                git_source = update.sources.freeze_source({'id': 'code', 'kind': 'git', 'source': native_source,
                    'url': str(remote), 'ref': 'refs/heads/main', 'commit': git('rev-parse', 'HEAD', cwd=remote),
                    'checksums': {'sha256': 'SKIP'}, 'work_dir': str(root/'count-freeze')})
            lock.update(version=versions[1] + '-1', sources=[git_source, source])
            policy['sources'][0]['checksum_index'] = 1
            policy['sources'].insert(0, {'id': 'code', 'kind': 'git', 'mutable': True,
                'source_template': native_source, 'url_template': str(remote), 'checksum_algorithm': 'sha256', 'checksum_index': 0})
            policy['automatic'] = {'version': {'derivation': 'frozen-git-revision-count', 'template': r'^r[0-9]+\.[0-9a-f]{7,40}$',
                'assignment': 'pkgver', 'literal_assignment': 'pkgver=' + versions[0]},
                'pkgrel': {'assignment': 'pkgrel', 'literal_assignment': 'pkgrel=1', 'value': '1'}, 'checksums': []}
            for directory, release in ((old/'recipes/example', '1'), (recipe, pkgrel)):
                data = after if executable_pkgver and directory == recipe else before
                checksum = hashlib.sha256(data).hexdigest()
                (directory/'fix.patch').write_bytes(data)
                short = '8' if executable_pkgver and directory == recipe else '7'
                function = 'pkgver() { cd "$srcdir/count-source"; printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short=' + short + ' HEAD)"; }\n'
                (directory/'PKGBUILD').write_text("pkgname=example\npkgver=" + versions[0] + "\npkgrel=" + release
                    + "\narch=('x86_64')\nsource=('" + native_source + "' 'fix.patch')\nsha256sums=('SKIP' '" + checksum + "')\n"
                    + function + 'package() { install -Dm644 fix.patch "$pkgdir/usr/share/example/fix.patch"; }\n')
                (directory/'.SRCINFO').write_text('pkgbase = example\n\tpkgver = ' + versions[0] + '\n\tpkgrel = ' + release
                    + '\n\tarch = x86_64\n\tsource = ' + native_source + '\n\tsource = fix.patch\n\tsha256sums = SKIP\n\tsha256sums = '
                    + checksum + '\npkgname = example\n')
            if declared_mismatch:
                build = recipe/'PKGBUILD'
                build.write_text(build.read_text().replace('pkgrel=' + pkgrel, 'pkgrel=99'))
        baseline = 'b'*40
        remotes = {}
        if pending:
            policy.update(authority='maintained', native_verification='pending-github-native-probe',
                          automatic={'version': {'assignment': 'pkgver'}, 'pkgrel': {'assignment': 'pkgrel'}})
            git = update.sources.git
            origin = root/'origin'
            shutil.copytree(old/'recipes/example', origin)
            aur = root/'aur'
            shutil.copytree(recipe, aur)
            write_recipe(aur, after, 'human-dependency', '1')
            for directory in (origin, aur):
                git('init', directory)
                git('add', '.', cwd=directory)
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-m', 'fixture', cwd=directory)
            baseline = git('rev-parse', 'HEAD', cwd=origin)
            git('branch', 'example', 'HEAD', cwd=aur)
            aur_commit = git('rev-parse', 'HEAD', cwd=aur)
            origin_url = 'https://git.n3t.work/vith/arch-pkg-example.git'
            aur_url = 'https://github.com/archlinux/aur.git'
            remotes = {origin_url: origin.as_uri(), aur_url: aur.as_uri()}
            identity = {'url': aur_url, 'package': 'example', 'ref': 'refs/heads/example',
                        'commit': aur_commit}
            provenance = {'schema': 1, 'pkgbase': 'example', 'aur': identity,
                          'watchers': [{'id': 'aur-recipe', 'kind': 'aur',
                                        **{key: value for key, value in identity.items() if key != 'commit'},
                                        'baseline_commit': aur_commit, 'mode': 'authoritative-import'}]}
            if not pending_aur:
                git('tag', 'v1', cwd=origin)
                provenance = {'schema': 1, 'pkgbase': 'example', 'aur': None,
                              'watchers': [{'id': 'software-tag', 'kind': 'tag', 'source_id': 'patch',
                                            'url': origin_url, 'tag_pattern': '^v([0-9]+)$',
                                            'accepted_tag': 'v1', 'accepted_tag_object': baseline,
                                            'accepted_peeled_commit': baseline}]}
            item = {'pkgbase': 'example', 'policy': policy,
                    'origin': {'url': origin_url, 'commit': baseline,
                               'tree': git('rev-parse', 'HEAD^{tree}', cwd=origin),
                               'manifest': candidates.tree_manifest(old/'recipes/example'), 'pkgbase': 'example'}}
            update.dump(old/'packages.json', {'schema': 1, 'packages': [], 'imports': [item]})
        else:
            update.dump(old/'packages.json', {'schema': 1, 'packages': [policy]})
        if conflict_resolution:
            provenance['watchers'] = [{'id': 'aur-recipe', 'kind': 'aur'}]
        update.dump(old/'inputs/example.json', lock)
        update.dump(old/'upstream/example.json', provenance)
        (old/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:' + 'f'*64)
        repo = Path(candidates.__file__).parents[1]
        for filename in set(recipe_state.CONTROLS) | {'tools/build.sh', 'tools/native.py', 'tools/sources.py', 'tools/recipe_gate.py', 'tools/github_api.py', 'tools/dependency_repo.py', 'keys/arch-packages.asc', 'keys/n3t.asc'}:
            destination = old/filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo/filename, destination)
        pr = {'state': 'open', 'head': {'ref': 'feature/human-recipe', 'sha': 'a'*40, 'repo': {'full_name': 'owner/repo'}}, 'base': {'ref': 'pkg/example', 'sha': baseline, 'repo': {'full_name': 'owner/repo'}}}
        stored = {}
        if conflict_resolution:
            stored['proposal', 'e'*40] = {
                'recipe_head': 'e'*40, 'recipe_base': baseline, 'pkgbase': 'example',
                'proposal_origin': 'watcher', 'watcher_id': 'aur-recipe',
                'lock': copy.deepcopy(lock), 'provenance': copy.deepcopy(provenance),
                'aur_conflicts': ['.SRCINFO', 'PKGBUILD']}
        self.manual_stored = stored
        def checkout(control, destination, pins=None, selected=None):
            if pins is not None:
                pins['example'] = baseline
            shutil.copytree(old, destination)
            return destination
        def export(head, destination):
            shutil.copytree(recipe, destination)
            return destination
        def api(route, *args):
            if '/pulls/' in route:
                return pr
            if route.endswith('a'*40):
                return {'tree': {'sha': 'd'*40}, 'parents': [{'sha': 'e'*40 if conflict_resolution else baseline}]}
            raise AssertionError(route)
        public_url = update.sources.public_url
        actual_git = update.sources.git
        def fixture_git(*args, **kwargs):
            return actual_git(*(remotes.get(str(arg), arg) for arg in args), **kwargs)
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}), \
                patch.object(update.sources, 'git', side_effect=fixture_git), \
                patch.object(update.sources, 'public_url', side_effect=lambda value: value if count_git else public_url(value)), \
                patch.object(update, 'api', side_effect=api), patch.object(update, 'status'), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'c'*40, 'a'*40)), \
                patch.object(recipe_state, 'assert_recipe_identity'), \
                patch.object(recipe_state, 'pending_build', return_value=None), \
                patch.object(recipe_state, 'load_optional', side_effect=lambda namespace, key: stored.get((namespace, key))), \
                patch.object(recipe_state, 'save', side_effect=lambda namespace, key, value: stored.setdefault((namespace, key), copy.deepcopy(value))), \
                patch.object(update, 'checkout_data', side_effect=checkout), \
                patch.object(candidates, 'export_recipe', side_effect=export), \
                patch.object(candidates.recipes, 'is_ancestor', return_value=True), \
                patch.object(native, 'run', side_effect=AssertionError('prepare must not execute recipes')):
            result = candidates.prepare(1, root/'candidate')
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
            self.assertEqual(record['merge_policy'], 'human')


    def test_declared_pkgrel_bump_after_dynamic_acceptance_can_build(self):
        with tempfile.TemporaryDirectory() as work:
            record, _ = self.prepare_manual(work, pkgrel='2', accepted_version='2-1', dynamic=True)
            self.assertEqual(record['predecessor_lock']['version'], '2-1')
            self.assertEqual(record['packages'][0]['lock']['version'], '1-2')
            self.assertEqual(record['merge_policy'], 'human')

    def test_frozen_count_declared_pkgrel_edit_accepts_actual_next_release(self):
        from tools import recipe_acceptance
        with tempfile.TemporaryDirectory() as work:
            record, _ = self.prepare_manual(work, count_git=True)
            package = record['packages'][0]
            declared = candidates.parse_srcinfo(package['expected_srcinfo'])
            previous_actual = record['predecessor_lock']['version']
            actual_version = previous_actual.rsplit('-', 1)[0] + '-2'
            self.assertNotEqual(declared['version'], actual_version)
            self.assertEqual(declared['pkgrel'], '2')
            self.assertEqual(record['merge_policy'], 'human')
            actual_srcinfo = package['expected_srcinfo'].replace('pkgver = ' + declared['pkgver'],
                                                               'pkgver = ' + update.parse_version(actual_version))
            actual_lock = copy.deepcopy(package['lock'])
            actual_lock['version'] = actual_version
            evidence = {key: record[key] for key in ('schema', 'repository', 'base', 'head', 'recipe_pins', 'previous_recipe_pins', 'image')}
            evidence['packages'] = [{'pkgbase': 'example', 'recipe_commit': record['head'],
                'request_input_digest': package['input_digest'], 'source_lock': actual_lock,
                'metadata': {'srcinfo': actual_srcinfo, 'dynamic_pkgver': True},
                'files': [{'name': 'example', 'arch': 'x86_64', 'version': actual_version}]}]
            recipe_acceptance.verify_native_evidence(record, evidence)
            self.assertEqual(package['lock']['version'], declared['version'])

    def test_declared_count_mode_rejects_pkgbuild_srcinfo_disagreement(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, 'declared recipe metadata differs'):
                self.prepare_manual(work, count_git=True, declared_mismatch=True)


    def test_executable_count_pkgver_and_payload_edit_do_not_enter_auto_transition(self):
        with tempfile.TemporaryDirectory() as work, \
                patch.object(update, 'independent_transition', side_effect=ValueError('automatic frozen Git version mismatch')):
            record, _ = self.prepare_manual(work, count_git=True, executable_pkgver=True)
            self.assertEqual(record['merge_policy'], 'human')
            self.assertNotEqual(record['packages'][0]['lock']['version'], record['predecessor_lock']['version'])


    def test_supported_package_names_produce_valid_opaque_request_keys(self):
        keys = []
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1'}):
            for name in ('libc++', 'python3.13'):
                record = {'repository': 'owner/repo', 'pkgbase': name, 'head': 'a'*40,
                          'packages': [{'lock': {'sources': []}}]}
                key = candidates.request_id(record)
                self.assertRegex(key, r'^[0-9a-f]{64}$')
                self.assertTrue(recipe_state.record_path('build-request', key).endswith(key + '.json'))
                self.assertTrue(recipe_state.record_path('build-result', key).endswith(key + '.json'))
                keys.append(key)
        self.assertNotEqual(keys[0], keys[1])


    def test_manual_payload_changes_require_native_version_or_pkgrel_advancement(self):
        for pkgrel in ('1', '0'):
            with self.subTest(pkgrel=pkgrel), tempfile.TemporaryDirectory() as work:
                with self.assertRaisesRegex(ValueError, 'version or pkgrel bump'):
                    self.prepare_manual(work, pkgrel=pkgrel)
                self.assertNotIn(('proposal', 'a'*40), self.manual_stored)


    def test_pending_initial_import_uses_fraction_above_aur_not_legacy_release(self):
        with tempfile.TemporaryDirectory() as work:
            record, stored = self.prepare_manual(work, pending=True, legacy_pkgrel='2',
                                                 accepted_version='1-2', pkgrel='1.1')
            self.assertEqual(record['packages'][0]['lock']['version'], '1-1.1')
            self.assertEqual(record['predecessor_lock']['version'], '1-2')
            self.assertEqual(record['merge_policy'], 'human')
            self.assertEqual(record['import_predecessor']['origin']['commit'], record['recipe_base'])
            self.assertEqual(stored['proposal', record['head']]['lock']['version'], '1-1.1')

    def test_pending_initial_import_cannot_lower_authenticated_aur_floor(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, 'AUR native version differs or goes backwards'):
                self.prepare_manual(work, pending=True, legacy_pkgrel='2',
                                    accepted_version='1-2', pkgrel='0.1')
            self.assertNotIn(('proposal', 'a'*40), self.manual_stored)

    def test_pending_non_aur_import_keeps_legacy_release_floor(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, 'version or pkgrel bump'):
                self.prepare_manual(work, pending=True, pending_aur=False, legacy_pkgrel='2',
                                    accepted_version='1-2', pkgrel='1.1')
            self.assertNotIn(('proposal', 'a'*40), self.manual_stored)

    def test_published_recipe_keeps_legacy_release_floor(self):
        for pkgrel in ('1.1', '2'):
            with self.subTest(pkgrel=pkgrel), tempfile.TemporaryDirectory() as work:
                with self.assertRaisesRegex(ValueError, 'version or pkgrel bump'):
                    self.prepare_manual(work, legacy_pkgrel='2', accepted_version='1-2', pkgrel=pkgrel)
                self.assertNotIn(('proposal', 'a'*40), self.manual_stored)

    def test_first_parent_conflict_resolution_still_freezes_and_validates_human_recipe(self):
        with tempfile.TemporaryDirectory() as work:
            record, _ = self.prepare_manual(work, conflict_resolution=True)
            self.assertEqual(record['head'], 'a'*40)
            self.assertEqual(record['proposal_head'], 'e'*40)
            self.assertEqual(record['packages'][0]['lock']['sources'][0]['checksums']['sha256'],
                             hashlib.sha256(b'human patch\n').hexdigest())
            self.assertEqual(record['merge_policy'], 'human')
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(ValueError):
                self.prepare_manual(work, conflict_resolution=True, invalid=True)

    def test_direct_human_unenrolled_source_does_not_create_manual_receipt(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(ValueError):
                self.prepare_manual(work, invalid=True)
            self.assertEqual(self.manual_stored, {})


    def test_fixed_source_cannot_be_changed_by_human_edit(self):
        old = {'schema': 1, 'version': '1-1', 'sources': [{'id': 'aux', 'kind': 'archive', 'checksums': {'sha256': 'a'}}]}
        new = copy.deepcopy(old)
        new['sources'][0]['checksums']['sha256'] = 'b'
        with self.assertRaisesRegex(ValueError, 'fixed'):
            candidates.source_boundaries(old, new, {'sources': [{'id': 'aux', 'mutable': False}]})


    def test_modern_indexed_version_source_change_preserves_enrollment(self):
        template = 'https://example.invalid/example-{version}.tar.gz'
        old = {'version': '1-1', 'sources': [{'id': 'code', 'kind': 'archive',
            'source': template.format(version='1'), 'url': template.format(version='1'),
            'checksums': {'sha256': 'a'*64}}]}
        new = copy.deepcopy(old)
        new.update(version='2-1')
        new['sources'][0].update(source=template.format(version='2'), url=template.format(version='2'),
                                 checksums={'sha256': 'b'*64})
        policy = {'sources': [{'id': 'code', 'kind': 'archive', 'native_source_index': 0,
                              'source_template': template, 'url_template': template,
                              'checksum_algorithm': 'sha256', 'checksum_index': 0}]}
        candidates.source_boundaries(old, new, policy)
        policy['sources'][0]['mutable'] = False
        with self.assertRaisesRegex(ValueError, 'fixed'):
            candidates.source_boundaries(old, new, policy)

    def test_changed_remote_source_with_malformed_mutability_fails_closed(self):
        old = {'sources': [{'id': 'code', 'kind': 'archive', 'checksums': {'sha256': 'a'*64}}]}
        new = copy.deepcopy(old)
        new['sources'][0]['checksums']['sha256'] = 'b'*64
        for declaration in ({}, {'native_source_index': -1}, {'native_source_index': True},
                            {'native_source_index': '0'}, {'mutable': 'yes', 'native_source_index': 0}):
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                candidates.source_boundaries(old, new, {'sources': [{'id': 'code', **declaration}]})

    def test_unresolved_original_aur_proposal_stops_prepare_before_candidate_registration(self):
        head, base = 'a'*40, 'b'*40
        pr = {'state': 'open', 'base': {'ref': 'pkg/example', 'sha': base}}
        receipt = {'recipe_head': head, 'recipe_base': base, 'pkgbase': 'example',
                   'proposal_origin': 'watcher', 'aur_conflicts': ['.SRCINFO', 'PKGBUILD']}
        with tempfile.TemporaryDirectory() as work, \
                patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'c'*40, head)), \
                patch.object(recipe_state, 'pending_build', return_value=None), \
                patch.object(recipe_state, 'load_optional', return_value=receipt), \
                patch.object(update, 'status'), \
                patch.object(update, 'checkout_data') as checkout, \
                patch.object(recipe_state, 'save') as save:
            with self.assertRaisesRegex(ValueError, 'conflicts require resolution'):
                candidates.prepare(1, Path(work)/'candidate')
            checkout.assert_not_called()
            save.assert_not_called()
            self.assertFalse((Path(work)/'candidate'/'candidate.json').exists())

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


    def test_same_head_reuses_original_request_after_controller_movement(self):
        original = {'request_id': 'demo-original', 'pkgbase': 'demo', 'pr_number': 7,
                    'base': 'c'*40, 'head': 'h'*40, 'recipe_base': 'b'*40,
                    'merge_policy': 'human', 'packages': [{'lock': {'version': '2-1'}}]}
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'state': 'open'}
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'd'*40, 'h'*40)), \
                patch.object(recipe_state, 'pending_build', return_value={'head': 'h'*40, 'request_id': 'demo-original'}), \
                patch.object(recipe_state, 'build_request', return_value=original), \
                patch.object(update, 'checkout_data', return_value=Path(directory)), \
                patch('tools.recipe_intents.load', return_value=None), \
                patch.object(candidates, 'export_recipe', side_effect=AssertionError('successful request must not refreeze')):
            self.assertEqual(candidates.prepare(7, directory), original)

    def test_same_head_intent_request_rejects_stale_current_intent(self):
        original = {'request_id': 'demo-original', 'pkgbase': 'demo', 'pr_number': 7,
                    'head': 'h'*40, 'recipe_base': 'b'*40, 'intent': {'predecessor': 'b'*40}}
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'state': 'open'}
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'd'*40, 'h'*40)), \
                patch.object(recipe_state, 'pending_build', return_value={'head': 'h'*40, 'request_id': 'demo-original'}), \
                patch.object(recipe_state, 'build_request', return_value=original), \
                patch.object(update, 'checkout_data', return_value=Path(directory)), \
                patch('tools.recipe_intents.load', return_value=None), \
                patch.object(candidates, 'export_recipe', side_effect=AssertionError('must not rebuild stale request')):
            with self.assertRaisesRegex(ValueError, 'intent'):
                candidates.prepare(7, directory)

    def test_intent_admitted_after_intent_free_request_prevents_same_head_reuse(self):
        original = {'request_id': 'demo-original', 'pkgbase': 'demo', 'pr_number': 7,
                    'head': 'h'*40, 'recipe_base': 'b'*40, 'base': 'c'*40,
                    'merge_policy': 'automatic'}
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'state': 'open'}
        with tempfile.TemporaryDirectory() as directory, patch.object(update, 'api', return_value=pr), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'd'*40, 'h'*40)), \
                patch.object(recipe_state, 'pending_build', return_value={'head': 'h'*40, 'request_id': 'demo-original'}), \
                patch.object(recipe_state, 'build_request', return_value=original), \
                patch.object(update, 'checkout_data', return_value=Path(directory)), \
                patch('tools.recipe_intents.load', return_value={'predecessor': 'b'*40}), \
                patch.object(candidates, 'export_recipe', side_effect=AssertionError('must not rebuild stale request')):
            with self.assertRaises(ValueError):
                candidates.prepare(7, directory)
            self.assertFalse((Path(directory)/'candidate.json').exists())

    def test_new_intent_prevents_original_automatic_request_from_finalizing_merge(self):
        record = {'pr_number': 7, 'pkgbase': 'demo', 'base': 'c'*40, 'head': 'd'*40,
                  'recipe_base': 'b'*40, 'merge_policy': 'automatic'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(update, 'load', return_value=record), \
                patch.object(candidates, 'validate_build'), \
                patch.object(recipe_state, 'assert_recipe_identity'), \
                patch.object(candidates, 'required_statuses'), \
                patch.object(update, 'main_sha', return_value='c'*40), \
                patch.object(update, 'checkout_data', return_value=Path(directory)), \
                patch('tools.recipe_intents.load', return_value={'predecessor': 'b'*40}), \
                patch.object(update, 'api', return_value={'merged': True, 'sha': 'a'*40}) as api:
            with self.assertRaises(ValueError):
                candidates.finalize(7, 'c'*40, 'd'*40, 'candidate.json', directory)
            self.assertFalse(any(route.endswith('/merge') for (route, *_), _ in api.call_args_list))

    def test_reused_automatic_request_can_finalize_in_the_same_workspace(self):
        original = {'request_id': 'demo-original', 'pkgbase': 'demo', 'pr_number': 7,
                    'base': 'c'*40, 'head': 'd'*40, 'recipe_base': 'b'*40,
                    'merge_policy': 'automatic'}
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'state': 'open'}
        payload = json.dumps({'schema': 1, 'packages': [{'pkgbase': 'demo'}], 'imports': []}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'control.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                entry = tarfile.TarInfo('snapshot/packages.json')
                entry.size = len(payload)
                stream.addfile(entry, io.BytesIO(payload))
            def checkout(sha, destination, pins, selected=None):
                update.extract_tree(archive, destination)
                pins['demo'] = 'b'*40
                return destination
            def api(route, method='GET', data=None):
                if route.endswith('/merge'):
                    return {'merged': True, 'sha': 'a'*40}
                return pr
            with patch.object(update, 'api', side_effect=api), \
                    patch.object(update, 'main_sha', return_value='c'*40), \
                    patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'c'*40, 'd'*40)), \
                    patch.object(recipe_state, 'pending_build', return_value={'head': 'd'*40, 'request_id': 'demo-original'}), \
                    patch.object(recipe_state, 'build_request', return_value=original), \
                    patch.object(update, 'checkout_data', side_effect=checkout), \
                    patch.object(candidates, 'validate_build'), \
                    patch.object(recipe_state, 'assert_recipe_identity'), \
                    patch.object(candidates, 'required_statuses'):
                candidates.prepare(7, root)
                result = candidates.finalize(7, 'c'*40, 'd'*40, root/'candidate.json', root)
            self.assertTrue(result['merged'])
            self.assertEqual(result['sha'], 'a'*40)

    def test_entire_worker_failure_rejects_stored_output(self):
        record = {'request_id': 'demo-original'}
        descriptor = {'record': record, 'producer': {'run_id': '11', 'run_attempt': '1', 'head_sha': 'c'*40}}
        with patch.object(recipe_state, 'StateSnapshot') as snapshot, \
                patch.object(recipe_state, 'build_request', return_value=record), \
                patch.object(update, 'api', return_value={'status': 'completed', 'conclusion': 'failure'}):
            snapshot.return_value.load.return_value = descriptor
            with self.assertRaisesRegex(ValueError, 'worker did not succeed'):
                candidates.verify_result(record, descriptor)

    def test_human_merge_policy_leaves_successful_pr_open(self):
        record = {'pr_number': 7, 'base': 'c'*40, 'head': 'd'*40, 'merge_policy': 'human'}
        with patch.object(update, 'load', return_value=record), \
                patch.object(candidates, 'validate_build'), \
                patch.object(recipe_state, 'assert_recipe_identity'), \
                patch.object(candidates, 'required_statuses'), \
                patch.object(update, 'api', side_effect=AssertionError('human Merge must remain human')):
            result = candidates.finalize(7, 'c'*40, 'd'*40, 'candidate.json', 'outputs')
            self.assertFalse(result['merged'])

    def test_changed_head_prevents_automatic_merge(self):
        record = {'pr_number': 7, 'base': 'c'*40, 'head': 'd'*40, 'merge_policy': 'automatic'}
        with patch.object(update, 'load', return_value=record), patch.object(candidates, 'validate_build'), \
                patch.object(recipe_state, 'assert_recipe_identity', side_effect=ValueError('head changed')), \
                patch.object(update, 'api', side_effect=AssertionError('stale head must not merge')):
            with self.assertRaisesRegex(ValueError, 'head changed'):
                candidates.finalize(7, 'c'*40, 'd'*40, 'candidate.json', 'outputs')

    def test_explicit_retry_replaces_unregistered_request_without_changing_recipe(self):
        original = {'request_id': 'demo-original', 'repository': 'owner/repo', 'pkgbase': 'demo', 'pr_number': 7,
                    'base': 'c'*40, 'head': 'a'*40, 'recipe_base': 'b'*40, 'merge_policy': 'human',
                    'packages': [{'lock': {'version': '2-1', 'sources': []}}]}
        active = {'head': 'a'*40, 'request_id': 'demo-original', 'state': 'queued', 'worker': None}
        failed = {**active, 'state': 'failure'}
        pr = {'base': {'ref': 'pkg/demo', 'sha': 'b'*40}, 'state': 'open'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'GITHUB_RUN_ID': '20', 'GITHUB_RUN_ATTEMPT': '1'}), \
                patch.object(update, 'api', return_value=pr), patch.object(update, 'status'), \
                patch.object(recipe_state, 'recipe_identity', return_value=(pr, 'd'*40, 'a'*40)), \
                patch.object(recipe_state, 'pending_build', return_value=active), \
                patch.object(recipe_state, 'build_request', return_value=original), \
                patch.object(update, 'checkout_data', return_value=Path(directory)), \
                patch('tools.recipe_intents.load', return_value=None), \
                patch.object(recipe_state, 'abandon_build', return_value=failed) as abandon:
            retried = candidates.prepare(7, directory, retry=True)
            self.assertNotEqual(retried['request_id'], original['request_id'])
            self.assertEqual(retried['head'], original['head'])
            self.assertEqual(retried['packages'], original['packages'])
            self.assertEqual(retried['expected_pending'], failed)
            self.assertEqual(retried['merge_policy'], 'human')
            self.assertEqual(abandon.call_args.kwargs, {'retry': True})
