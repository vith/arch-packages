import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import recipes, update
from tools.recipe_gate import harness_digest, tree_manifest


class ImmutableRecipeTests(unittest.TestCase):
    def setUp(self):
        work = Path.home() / '.local/state/arch-packages/work/recipe-gitlink-tests'
        work.mkdir(parents=True, exist_ok=True)
        self.session = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.session.cleanup)
        self.root = Path(self.session.name)
        self.repo = self.root / 'git'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Recipe fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        (self.payload / 'PKGBUILD').write_text('pkgname=example\npkgver=1.0\npkgrel=1\n')
        (self.payload / '.SRCINFO').write_text('pkgbase = example\n\tpkgver = 1.0\n\tpkgrel = 1\n\tarch = any\npkgname = example\n')
        self.old = self.recipe_commit('1.0')
        self.new = self.recipe_commit('2.0', self.old)
        self.git('update-ref', 'refs/heads/pkg/example', self.new)
        self.control = self.root / 'control'
        self.control.mkdir()
        (self.control / 'packages.json').write_text(json.dumps({'schema': 1, 'packages': [{'pkgbase': 'example'}]}))
        (self.control / '.gitmodules').write_text('[submodule "recipes/example"]\n\tpath = recipes/example\n\turl = https://github.com/owner/repo.git\n\tbranch = pkg/example\n')
        blobs = []
        for path in ('packages.json', '.gitmodules'):
            sha = self.git('hash-object', '-w', '--stdin', data=(self.control / path).read_bytes())
            blobs.append('100644 blob ' + sha + '\t' + path + '\n')
        recipe_tree = self.git('mktree', data=('160000 commit ' + self.old + '\texample\n').encode())
        blobs.append('040000 tree ' + recipe_tree + '\trecipes\n')
        tree = self.git('mktree', data=''.join(sorted(blobs)).encode())
        self.head = self.git('commit-tree', tree, '-m', 'Pinned control fixture')
        for transport in (patch.object(recipes, 'api', side_effect=self.local_api), patch.object(recipes, 'download', side_effect=self.local_download)):
            transport.start()
            self.addCleanup(transport.stop)

    def git(self, *args, data=None, check=True):
        return subprocess.run(['git', '-C', str(self.repo), *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check).stdout.decode().strip()

    def recipe_commit(self, version, parent=None):
        entries = []
        for path in ('PKGBUILD', '.SRCINFO'):
            content = (self.payload / path).read_text().replace('1.0', version).encode()
            sha = self.git('hash-object', '-w', '--stdin', data=content)
            entries.append('100644 blob ' + sha + '\t' + path + '\n')
        tree = self.git('mktree', data=''.join(sorted(entries)).encode())
        return self.git('commit-tree', tree, *(['-p', parent] if parent else []), '-m', 'Recipe ' + version)

    def local_api(self, path):
        if '/git/commits/' in path:
            sha = path.rsplit('/', 1)[1]
            return {'sha': sha, 'tree': {'sha': self.git('rev-parse', sha + '^{tree}')}}
        if '/git/trees/' in path:
            sha = path.rsplit('/', 1)[1].split('?')[0]
            entries = []
            for row in self.git('ls-tree', '-r', sha).splitlines():
                prefix, name = row.split('\t', 1)
                mode, kind, object_sha = prefix.split()
                entries.append({'path': name, 'mode': mode, 'type': kind, 'sha': object_sha})
            return {'tree': entries, 'truncated': False}
        if '/compare/' in path:
            old, new = path.rsplit('/', 1)[1].split('...')
            ancestor = self.git('merge-base', old, new, check=False)
            return {'status': 'ahead' if ancestor == old else 'diverged', 'merge_base_commit': {'sha': ancestor}}
        raise AssertionError('Unexpected transport operation')

    def local_download(self, url, destination, maximum):
        sha = url.rsplit('/', 1)[1]
        data = subprocess.check_output(['git', '-C', str(self.repo), 'archive', '--format=tar', '--prefix=fixture/', sha])
        if len(data) > maximum:
            raise ValueError('fixture exceeds bounded transport')
        Path(destination).write_bytes(data)
        return Path(destination)

    def roster(self, count=33, harness_recipe=None, nested=False):
        """Build a real Git control tree with independently named recipe roots."""
        names = ['example'] + ['fixture-' + str(index) for index in range(1, count)]
        pins = {'example': self.old}
        for name in names[1:]:
            entries = []
            for path in ('PKGBUILD', '.SRCINFO'):
                content = (self.payload / path).read_text().replace('example', name).encode()
                sha = self.git('hash-object', '-w', '--stdin', data=content)
                entries.append('100644 blob ' + sha + '\t' + path + '\n')
            if nested and name == names[-1]:
                entries.append('160000 commit ' + self.old + '\tnested\n')
            tree = self.git('mktree', data=''.join(sorted(entries)).encode())
            pins[name] = self.git('commit-tree', tree, '-m', name)
        (self.control / 'packages.json').write_text(json.dumps(
            {'schema': 1, 'packages': [{'pkgbase': name} for name in names]}))
        (self.control / '.gitmodules').write_text(''.join(
            '[submodule "recipes/' + name + '"]\n\tpath = recipes/' + name
            + '\n\turl = https://github.com/owner/repo.git\n\tbranch = pkg/' + name + '\n'
            for name in names))
        tools = self.control / 'tools'
        tools.mkdir(exist_ok=True)
        files = ['tools/recipe_gate.py']
        if harness_recipe is not None:
            files.append('recipes/' + harness_recipe + '/PKGBUILD')
        # Legacy literal declaration must be parsed, never executed.
        (tools / 'recipe_gate.py').write_text(
            'raise RuntimeError("historical source must not execute")\n'
            'def harness_digest(root):\n    files = ' + repr(files) + '\n')
        recipe_tree = self.git('mktree', data=''.join(
            '160000 commit ' + pins[name] + '\t' + name + '\n'
            for name in sorted(names)).encode())
        tool_blob = self.git('hash-object', '-w', '--stdin', data=(tools / 'recipe_gate.py').read_bytes())
        tool_tree = self.git('mktree', data=('100644 blob ' + tool_blob + '\trecipe_gate.py\n').encode())
        entries = ['040000 tree ' + recipe_tree + '\trecipes\n',
                   '040000 tree ' + tool_tree + '\ttools\n']
        for name in ('packages.json', '.gitmodules'):
            sha = self.git('hash-object', '-w', '--stdin', data=(self.control / name).read_bytes())
            entries.append('100644 blob ' + sha + '\t' + name + '\n')
        tree = self.git('mktree', data=''.join(sorted(entries)).encode())
        self.head = self.git('commit-tree', tree, '-m', 'Complete roster')
        return pins

    def test_selected_body_preserves_complete_pin_roster(self):
        pins = self.roster()
        actual = recipes.materialize(self.control, self.head, 'owner/repo',
                                     update.extract_tree, selected={'example'})
        self.assertEqual(actual, pins)
        self.assertEqual({path.name for path in (self.control / 'recipes').iterdir()}, {'example'})
        self.assertEqual(tree_manifest(self.control / 'recipes/example'), tree_manifest(self.payload))

    def test_invalid_unselected_pins_fail_before_selected_body_installation(self):
        self.roster()
        original = self.local_api
        control_tree = self.git('rev-parse', self.head + '^{tree}')
        endpoint = '/repos/owner/repo/git/trees/' + control_tree + '?recursive=1'
        for field, value in (('sha', 'not-a-sha'), ('mode', '100644'), ('type', 'blob')):
            def altered(path):
                result = copy.deepcopy(original(path))
                if path == endpoint:
                    for entry in result['tree']:
                        if entry['path'] == 'recipes/fixture-1':
                            entry[field] = value
                return result
            with self.subTest(field=field), patch.object(recipes, 'api', side_effect=altered):
                with self.assertRaises(ValueError):
                    recipes.materialize(self.control, self.head, 'owner/repo',
                                        update.extract_tree, selected={'example'})
                self.assertFalse((self.control / 'recipes/example').exists())

    def test_unregistered_selection_is_rejected(self):
        self.roster()
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            recipes.materialize(self.control, self.head, 'owner/repo',
                                update.extract_tree, selected={'missing'})

    def test_full_default_retains_unselected_nested_payload_guard(self):
        pins = self.roster(count=2, nested=True)
        with self.assertRaisesRegex(ValueError, 'nested'):
            recipes.materialize(self.control, self.head, 'owner/repo', update.extract_tree)
        self.assertFalse((self.control / 'recipes/example').exists())
        self.assertEqual(recipes.materialize(self.control, self.head, 'owner/repo',
                                            update.extract_tree, selected={'example'}), pins)

    def test_historical_crossroot_harness_falls_back_without_digest_change(self):
        pins = self.roster(count=2, harness_recipe='fixture-1')
        with patch.object(update, 'repository', return_value='owner/repo'), patch.object(
                update, 'download', side_effect=self.local_download):
            full = update.checkout_data(self.head, self.root / 'full')
            actual = {}
            selected = update.checkout_data(self.head, self.root / 'selected', actual,
                                            selected={'example'})
            self.assertEqual(actual, pins)
            self.assertEqual(harness_digest(selected), harness_digest(full))
            self.assertEqual(tree_manifest(selected / 'recipes'), tree_manifest(full / 'recipes'))
            with self.assertRaisesRegex(ValueError, 'unregistered'):
                update.checkout_data(self.head, self.root / 'invalid', selected={'missing'})

    def test_historical_owned_harness_needs_only_selected_payload(self):
        pins = self.roster(count=2, harness_recipe='example')
        with patch.object(update, 'repository', return_value='owner/repo'), patch.object(
                update, 'download', side_effect=self.local_download):
            full = update.checkout_data(self.head, self.root / 'full')
            actual = {}
            selected = update.checkout_data(self.head, self.root / 'selected', actual,
                                            selected={'example'})
            self.assertEqual(actual, pins)
            self.assertEqual(harness_digest(selected), harness_digest(full))
            self.assertFalse((selected / 'recipes/fixture-1/PKGBUILD').exists())

    def test_moving_branch_cannot_advance_pinned_recipe(self):
        recipes.materialize(self.control, self.head, 'owner/repo', update.extract_tree)
        self.assertIn('pkgver=1.0', (self.control / 'recipes/example/PKGBUILD').read_text())
        self.assertNotIn('pkgver=2.0', (self.control / 'recipes/example/PKGBUILD').read_text())
        self.assertTrue(recipes.is_ancestor('owner/repo', self.old, self.new))
        self.assertFalse(recipes.is_ancestor('owner/repo', self.new, self.old))

    def test_existing_changes_are_not_overwritten_by_materialization(self):
        recipes.materialize(self.control, self.head, 'owner/repo', update.extract_tree)
        path = self.control / 'recipes/example/PKGBUILD'
        path.write_text('local changes must survive\n')
        with self.assertRaisesRegex(ValueError, 'differs'):
            recipes.materialize(self.control, self.head, 'owner/repo', update.extract_tree)
        self.assertEqual(path.read_text(), 'local changes must survive\n')

    def test_wrong_repository_or_module_path_fails_before_materialization(self):
        path = self.control / '.gitmodules'
        original = path.read_text()
        for altered in (original.replace('owner/repo.git', 'attacker/repo.git'), original.replace('path = recipes/example', 'path = ../escape'), original.replace('pkg/example', 'moving-main')):
            path.write_text(altered)
            with self.assertRaises(ValueError):
                recipes.materialize(self.control, self.head, 'owner/repo', update.extract_tree)
            self.assertFalse((self.control / 'recipes/example').exists())
        path.write_text(original)

    def test_submodule_administration_is_not_package_payload(self):
        (self.payload / '.git').write_text('gitdir: ../private-administration\n')
        (self.payload / 'helper').write_text('complete payload')
        (self.payload / 'helper').chmod(0o755)
        (self.payload / 'alias').symlink_to('helper')
        copied = recipes.copy_recipe(self.payload, self.root / 'copy')
        self.assertFalse((copied / '.git').exists())
        self.assertEqual(tree_manifest(copied), tree_manifest(self.payload))
        self.assertTrue((copied / 'alias').is_symlink())
        self.assertEqual((copied / 'helper').stat().st_mode & 0o777, 0o755)
        (self.payload / '.git').unlink()
        (self.payload / '.git').symlink_to('helper')
        with self.assertRaises(ValueError):
            recipes.copy_recipe(self.payload, self.root / 'unsafe-copy')


if __name__ == '__main__':
    unittest.main()
