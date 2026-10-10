import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import imports, recipe_acceptance, recipe_candidates, recipe_gate, recipe_state, sources, update


class PendingImports(unittest.TestCase):
    def setUp(self):
        work = Path.home()/'.local/state/arch-packages/import-tests'
        work.mkdir(parents=True, exist_ok=True)
        self.session = tempfile.TemporaryDirectory(prefix='fixture-', dir=work)
        self.addCleanup(self.session.cleanup)
        self.root = Path(self.session.name)
        self.origin = self.root/'origin'
        self.origin.mkdir()
        # An imported recipe would leave observable damage if evaluated.
        (self.origin/'PKGBUILD').write_text('pkgname=example\npkgver=1\npkgrel=1\ntouch "'+str(self.root/'EXECUTED')+'"\npackage() { false; }\n')
        (self.origin/'.SRCINFO').write_text('pkgbase = example\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\npkgname = example\n')
        manifest = recipe_gate.tree_manifest(self.origin)
        sources.git('init', self.origin)
        sources.git('add', '.', cwd=self.origin)
        sources.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'original', cwd=self.origin)
        self.commit = sources.git('rev-parse', 'HEAD', cwd=self.origin)
        sources.git('tag', 'v1', cwd=self.origin)
        self.policy = {'pkgbase': 'example', 'native_verification': 'pending-github-native-probe', 'outputs': [{'name': 'example', 'arch': 'x86_64'}], 'automatic': {'version': {'assignment': 'pkgver'}, 'pkgrel': {'assignment': 'pkgrel'}}, 'sources': [{'id': 'patch', 'kind': 'local', 'mutable': False, 'source_template': 'fix.patch', 'checksum_algorithm': 'sha256', 'checksum_index': 0}]}
        self.item = {'pkgbase': 'example', 'policy': self.policy, 'origin': {'url': 'https://git.n3t.work/vith/arch-pkg-example.git', 'commit': self.commit, 'tree': sources.git('rev-parse', 'HEAD^{tree}', cwd=self.origin), 'manifest': manifest, 'pkgbase': 'example'}}
        self.lock = {'schema': 1, 'version': '1-2', 'sources': [{**dict.fromkeys(sources.FIELDS), 'id': 'patch', 'kind': 'local', 'source': 'fix.patch', 'checksums': {'sha256': hashlib.sha256(b'native patch').hexdigest()}}]}
        self.provenance = {'schema': 1, 'pkgbase': 'example', 'aur': None, 'watchers': [{'id': 'software-tag', 'kind': 'tag', 'source_id': 'patch', 'url': self.item['origin']['url'], 'tag_pattern': '^v([0-9]+)$', 'accepted_tag': 'v1', 'accepted_tag_object': self.commit, 'accepted_peeled_commit': self.commit}]}
        self.old, self.new = self.root/'old', self.root/'new'
        self.old.mkdir(); self.new.mkdir()
        environment = patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '11', 'GITHUB_RUN_ATTEMPT': '1'})
        environment.start(); self.addCleanup(environment.stop)
        (self.old/'.gitmodules').write_text('')
        (self.new/'.gitmodules').write_text('[submodule "recipes/example"]\npath = recipes/example\nurl = https://github.com/owner/repo.git\nbranch = pkg/example\n')
        update.dump(self.old/'packages.json', {'schema': 1, 'packages': []})
        update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [self.item]})
        shutil.copytree(self.origin, self.new/'recipes'/'example', ignore=shutil.ignore_patterns('.git'))
        update.dump(self.new/'inputs'/'example.json', self.lock)
        update.dump(self.new/'upstream'/'example.json', self.provenance)
        actual_git = sources.git
        def fixture_git(*args, **kwargs):
            arguments = list(args)
            for index, arg in enumerate(arguments):
                if arg == self.item['origin']['url']:
                    arguments[index] = self.origin.as_uri()
            return actual_git(*arguments, **kwargs)
        patched = patch.object(sources, 'git', side_effect=fixture_git)
        patched.start(); self.addCleanup(patched.stop)

    def admit(self):
        return imports.validate_admission(self.old, self.new, {}, {'example': self.commit}, self.root/'proof')

    def test_authentic_admission_is_nonexecutable_and_activation_is_exact_once(self):
        self.assertEqual(self.admit(), ['example'])
        self.assertFalse((self.root/'EXECUTED').exists())
        self.assertEqual(update.policy_at(self.new), {})
        value = imports.registry(self.new)[0]
        activated = imports.activate(value, self.item)
        self.assertEqual(activated['packages'], [self.policy])
        self.assertEqual(activated['imports'], [])
        self.assertEqual(value['packages'], [])
        with self.assertRaises(ValueError):
            imports.activate(activated, self.item)
        self.assertFalse((self.root/'proof'/'example'/'origin.git'/'FETCH_HEAD').exists())

    def test_main_prepare_and_independent_authorization_select_zero_packages(self):
        repo = Path(update.__file__).parents[1]
        for root in (self.old, self.new):
            harness_files = {'tools/build.sh', 'tools/native.py', 'tools/sources.py',
                             'tools/recipe_gate.py', 'tools/github_api.py',
                             'tools/dependency_repo.py', 'keys/arch-packages.asc', 'keys/n3t.asc'}
            for filename in set(recipe_state.CONTROLS) | harness_files:
                target = root/filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo/filename, target)
            (root/'build-image.txt').write_text('ghcr.io/archlinux/archlinux@sha256:'+'f'*64)
        def checkout(sha, destination, pins, selected=None):
            origin = self.old if sha == 'a'*40 else self.new
            shutil.copytree(origin, destination)
            pins.update({} if sha == 'a'*40 else {'example': self.commit})
            return destination
        git = sources.git
        def controller_git(*args, **kwargs):
            if args == ('rev-parse', 'HEAD') and kwargs.get('cwd') == self.old:
                return 'a'*40
            return git(*args, **kwargs)
        pr = {'base': {'ref': 'main'}, 'head': {'repo': {'full_name': 'owner/repo'}}}
        with patch.object(update, 'ROOT', self.old), patch.object(sources, 'git', side_effect=controller_git), patch.object(update, 'api', return_value=pr), patch.object(update, 'pr_identity', return_value=(pr, 'a'*40, 'b'*40)), patch.object(update, 'checkout_data', side_effect=checkout), patch.object(update, 'status'), patch.object(recipe_acceptance, 'validate_bookkeeping', return_value=None), patch.object(recipe_state, 'save'), patch.object(recipe_state, 'attest'), patch.object(recipe_candidates, 'candidate_provenance_exists', return_value=False):
            record = update.prepare(1, self.root/'candidate')
            self.assertEqual(record['packages'], [])
            self.assertEqual(record['review_environment'], '')
            self.assertTrue(record['auto_merge'])
            recipe_candidates._verify_inputs(record, True, lambda *args: self.fail('import entered executable source transition'), lambda *args, **kwargs: self.fail('import entered native metadata'))
            (self.new/'tools'/'untrusted.py').write_text('raise RuntimeError()')
            with self.assertRaises(ValueError):
                recipe_candidates._verify_inputs(record, True, lambda *args: None, lambda *args, **kwargs: None)
        self.assertFalse((self.root/'EXECUTED').exists())

    def test_mismatched_module_identity_is_not_metadata_admission(self):
        (self.new/'.gitmodules').write_text('[submodule "recipes/example"]\npath = recipes/example\nurl = https://github.com/other/repo.git\nbranch = pkg/example\n')
        with self.assertRaises(ValueError):
            self.admit()

    def test_source_identity_preserves_case_without_allowing_workspace_escape(self):
        identity = 'example-Cargo.lock'
        self.policy['sources'][0]['id'] = identity
        self.lock['sources'][0]['id'] = identity
        self.provenance['watchers'][0]['source_id'] = identity
        update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [self.item]})
        update.dump(self.new/'inputs'/'example.json', self.lock)
        update.dump(self.new/'upstream'/'example.json', self.provenance)
        self.assertEqual(self.admit(), ['example'])
        self.assertEqual(imports.policies(self.new)['example']['sources'][0]['id'], identity)
        self.assertFalse((self.root/'EXECUTED').exists())
        shutil.rmtree(self.root/'proof')
        self.policy['sources'][0]['id'] = '../escaped'
        self.lock['sources'][0]['id'] = '../escaped'
        self.provenance['watchers'][0]['source_id'] = '../escaped'
        update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [self.item]})
        update.dump(self.new/'inputs'/'example.json', self.lock)
        update.dump(self.new/'upstream'/'example.json', self.provenance)
        with self.assertRaises(ValueError):
            self.admit()
        self.assertFalse((self.root/'escaped').exists())

    def test_release_admission_authenticates_related_archive_bytes_without_asset_claims(self):
        runtime, license_data = b'original runtime release', b'original tag-bound license'
        runtime_url = 'https://github.com/example/project/releases/download/v1/runtime.tar.gz'
        license_url = 'https://example.test/project/v1/LICENSE'
        primary = {**dict.fromkeys(sources.FIELDS), 'id': 'runtime', 'kind': 'release',
                   'source': 'runtime.tar.gz::'+runtime_url, 'url': runtime_url,
                   'release_id': 10, 'asset_id': 20,
                   'checksums': {'sha256': hashlib.sha256(runtime).hexdigest()}}
        related = {**dict.fromkeys(sources.FIELDS), 'id': 'LICENSE', 'kind': 'archive',
                   'source': 'LICENSE::'+license_url, 'url': license_url,
                   'checksums': {'sha256': hashlib.sha256(license_data).hexdigest()}}
        self.lock['sources'] = [primary, related]
        self.policy['sources'] = [
            {'id': 'runtime', 'kind': 'release', 'mutable': True,
             'source_template': 'runtime.tar.gz::https://github.com/example/project/releases/download/v{version}/runtime.tar.gz',
             'url_template': 'https://github.com/example/project/releases/download/v{version}/runtime.tar.gz',
             'checksum_algorithm': 'sha256', 'checksum_index': 0},
            {'id': 'LICENSE', 'kind': 'archive', 'mutable': True,
             'source_template': 'LICENSE::https://example.test/project/v{version}/LICENSE',
             'url_template': 'https://example.test/project/v{version}/LICENSE',
             'checksum_algorithm': 'sha256', 'checksum_index': 1}]
        watcher = self.provenance['watchers'][0]
        watcher.update(kind='release', repository='example/project', source_id='runtime',
                       related_source_ids=['LICENSE'], release_id=10, asset_id=20,
                       asset_templates={'runtime': 'runtime.tar.gz'})
        release = {'id': 10, 'draft': False, 'prerelease': False,
                   'assets': [{'id': 20, 'name': 'runtime.tar.gz', 'browser_download_url': runtime_url}]}
        def fetch(url, *args, **kwargs):
            if url == 'https://api.github.com/repos/example/project/releases/tags/v1':
                return json.dumps(release).encode()
            if url == runtime_url:
                return runtime
            if url == license_url:
                return license_data
            self.fail('unexpected source request: '+url)
        def save():
            update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [self.item]})
            update.dump(self.new/'inputs'/'example.json', self.lock)
            update.dump(self.new/'upstream'/'example.json', self.provenance)
        save()
        with patch.object(sources, 'fetch', side_effect=fetch):
            self.assertEqual(self.admit(), ['example'])
            self.assertFalse((self.root/'EXECUTED').exists())
            shutil.rmtree(self.root/'proof')
            related['checksums']['sha256'] = '0'*64
            save()
            with self.assertRaises(ValueError):
                self.admit()
            shutil.rmtree(self.root/'proof')
            related['checksums']['sha256'] = hashlib.sha256(license_data).hexdigest()
            primary['asset_id'] = 999
            save()
            with self.assertRaises(ValueError):
                self.admit()

    def test_false_origin_tree_and_manifest_fail_without_execution(self):
        for field, bad in [('tree', '0'*40), ('manifest', [{'path': 'PKGBUILD', 'mode': '100644', 'kind': 'file', 'sha256': '0'*64}])]:
            with self.subTest(field=field):
                item = copy.deepcopy(self.item); item['origin'][field] = bad
                with self.assertRaises(ValueError):
                    imports.authenticate(item, self.root/field)
        self.assertFalse((self.root/'EXECUTED').exists())

    def test_wrong_public_origin_and_duplicate_active_identity_are_rejected(self):
        for url in ['https://git.n3t.work/other/arch-pkg-example.git', 'https://git.n3t.work/vith/arch-pkg-example.git?x=1', 'https://git.n3t.work/vith/arch-pkg-other.git']:
            item = copy.deepcopy(self.item); item['origin']['url'] = url
            update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [item]})
            with self.assertRaises(ValueError):
                imports.registry(self.new)
        update.dump(self.new/'packages.json', {'schema': 1, 'packages': [self.policy], 'imports': [self.item]})
        with self.assertRaises(ValueError):
            imports.registry(self.new)

    def test_admission_rejects_tooling_and_missing_registration(self):
        (self.new/'tools').mkdir(); (self.new/'tools'/'evil.py').write_text('raise RuntimeError()\n')
        with self.assertRaises(ValueError):
            self.admit()
        shutil.rmtree(self.new/'tools')
        with self.assertRaises(ValueError):
            imports.validate_admission(self.old, self.new, {}, {}, self.root/'proof')

    def test_existing_pending_pin_policy_and_lock_cannot_be_changed_on_main(self):
        shutil.rmtree(self.old); shutil.copytree(self.new, self.old)
        changed = copy.deepcopy(self.item); changed['policy']['outputs'][0]['arch'] = 'any'
        update.dump(self.new/'packages.json', {'schema': 1, 'packages': [], 'imports': [changed]})
        with self.assertRaises(ValueError):
            imports.validate_admission(self.old, self.new, {'example': self.commit}, {'example': self.commit}, self.root/'proof')
        update.dump(self.new/'packages.json', imports.registry(self.old)[0])
        with self.assertRaises(ValueError):
            imports.validate_admission(self.old, self.new, {'example': self.commit}, {'example': '0'*40}, self.root/'proof')
        update.dump(self.new/'inputs'/'example.json', {**self.lock, 'version': '9-1'})
        with self.assertRaises(ValueError):
            imports.validate_admission(self.old, self.new, {'example': self.commit}, {'example': self.commit}, self.root/'proof')

    def test_bookkeeping_activation_changes_registry_and_preserves_original_producer(self):
        activated = imports.activate(imports.registry(self.new)[0], self.item)
        producer = {'native_runs': [{'run_id': '77'}], 'record': {'base': 'a'*40}}
        receipt = {'pkgbase': 'example', 'accepted': 'b'*40, 'lock': self.lock, 'provenance': self.provenance, 'build': producer, 'activated_registry': activated, 'import_predecessor': self.item}
        before = {'packages.json': ('100644', 'blob', 'c'*40), 'recipes/example': ('160000', 'commit', self.commit), 'unrelated': ('100644', 'blob', 'd'*40)}
        after = recipe_acceptance.expected_leaves(before, receipt)
        self.assertEqual(after['packages.json'], ('100644', 'blob', recipe_acceptance.blob_sha(activated)))
        self.assertEqual(after['recipes/example'], ('160000', 'commit', 'b'*40))
        self.assertEqual(after['unrelated'], before['unrelated'])
        self.assertEqual(receipt['build'], producer)

    def test_conversion_code_needs_review_and_native_hash_ignores_registration(self):
        native_root = self.root/'native'; native_root.mkdir()
        (native_root/'PKGBUILD').write_text('pkgname=example\npkgver=1\npkgrel=2\npackage() { install -Dm644 fix.patch "$pkgdir/usr/share/example/fix.patch"; }\n')
        (native_root/'.SRCINFO').write_text('pkgbase = example\n\tpkgver = 1\n\tpkgrel = 2\n\tarch = x86_64\npkgname = example\n')
        (native_root/'fix.patch').write_bytes(b'native patch')
        self.assertTrue(recipe_gate.needs_pkgbuild_review(self.origin, native_root, self.policy))
        pending_policy = imports.policies(self.new)['example']
        activated = imports.activate(imports.registry(self.new)[0], self.item)
        update.dump(self.new/'packages.json', activated)
        active_policy = update.policy_at(self.new)['example']
        self.assertEqual(recipe_gate.input_digest(native_root, self.lock, pending_policy, 'image', 'harness'), recipe_gate.input_digest(native_root, self.lock, active_policy, 'image', 'harness'))
