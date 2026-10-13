import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.native import actual_source_lock, cache_environment, contained, dependency_names, dynamic_pkgver, parse_pkginfo, validate_bundle, validate_output
from tools import sources


class NativeRunTests(unittest.TestCase):
    harness = """
import json
from pathlib import Path
import subprocess
import sys
from tools.native import run

returncode = 0
try:
    argv = [sys.executable, '-c', sys.argv[2]]
    output = run(argv, stream=True) if sys.argv[3] == 'stream' else run(argv)
except subprocess.CalledProcessError as error:
    returncode = error.returncode
    output = error.output
Path(sys.argv[1]).write_text(json.dumps({'output': output, 'returncode': returncode}))
sys.exit(returncode)
"""

    def test_partial_progress_is_live_and_receipt_decodes_on_success_and_failure(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        chunks = [b'prepare \xe2', b'\x82\xac\r', b'\nbuild\rfinished\n']
        expected = 'prepare \u20ac\nbuild\nfinished\n'
        for returncode in (0, 7):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory(dir=state) as directory:
                receipt = Path(directory) / 'receipt.json'
                child = f"""
import os
import sys
chunks = {chunks!r}
for chunk in chunks[:-1]:
    os.write(1, chunk)
    if not os.read(0, 1):
        sys.exit(99)
os.write(1, chunks[-1])
sys.exit({returncode})
"""
                env = {**os.environ, 'PYTHONUTF8': '1'}
                with subprocess.Popen(
                    [sys.executable, '-c', self.harness, str(receipt), child, 'stream'],
                    cwd=Path(__file__).resolve().parent.parent, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                ) as process:
                    try:
                        live = b''
                        for chunk in chunks[:-1]:
                            received = b''
                            deadline = time.monotonic() + 10
                            while len(received) < len(chunk):
                                ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                                self.assertTrue(ready, 'progress blocked until child exit')
                                data = os.read(process.stdout.fileno(), len(chunk) - len(received))
                                self.assertTrue(data, 'stdout closed before partial progress')
                                received += data
                            self.assertEqual(received, chunk)
                            self.assertIsNone(process.poll())
                            self.assertFalse(receipt.exists())
                            live += received
                            process.stdin.write(b'x')
                            process.stdin.flush()
                        rest, _ = process.communicate(timeout=10)
                        self.assertEqual(live + rest, b''.join(chunks))
                        self.assertEqual(process.returncode, returncode)
                        self.assertEqual(json.loads(receipt.read_text()), {'output': expected, 'returncode': returncode})
                    finally:
                        if process.poll() is None:
                            process.stdin.close()
                            process.kill()
                            process.wait()

    def test_metadata_stdout_stays_capture_only(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory:
            receipt = Path(directory) / 'receipt.json'
            child = "import os; os.write(1, b'pkgbase = example\\r\\n\\tpkgver = 1\\r')"
            result = subprocess.run(
                [sys.executable, '-c', self.harness, str(receipt), child, 'quiet'],
                cwd=Path(__file__).resolve().parent.parent,
                check=True, stdout=subprocess.PIPE,
            )
            self.assertEqual(result.stdout, b'')
            self.assertEqual(json.loads(receipt.read_text()), {
                'output': 'pkgbase = example\n\tpkgver = 1\n', 'returncode': 0,
            })


class DynamicPkgverTests(unittest.TestCase):
    def test_loaded_static_and_dynamic_recipes_are_distinguished(self):
        state = Path.home() / '.local/state/omp/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory:
            root = Path(directory)
            def sandbox(argv, cwd, env):
                return subprocess.run(argv, cwd=cwd, env={**os.environ, **env},
                                      check=True, capture_output=True, text=True).stdout
            for code, expected in (
                    ('pkgver=1\n', False),
                    ('pkgver=1\npkgver() { printf 2; }\n', True),
                    ('pkgver=1\n# pkgver() { printf 2; }\n', False),
                    ("pkgver=1\ncomment='pkgver() { printf 2; }'\n", False)):
                (root / 'PKGBUILD').write_text(code)
                with self.subTest(code=code), patch('tools.native.builder', side_effect=sandbox):
                    self.assertEqual(dynamic_pkgver(root, {}), expected)


class ActualSourceTests(unittest.TestCase):
    def setUp(self):
        state = Path.home() / '.local/state/omp/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=state)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / 'src' / 'upstream'
        self.repository.mkdir(parents=True)
        sources.git('init', self.repository)
        sources.git('config', 'user.name', 'Native test', cwd=self.repository)
        sources.git('config', 'user.email', 'native@example.org', cwd=self.repository)
        (self.repository / 'file').write_text('first')
        sources.git('add', 'file', cwd=self.repository)
        sources.git('commit', '-m', 'first', cwd=self.repository)
        sources.git('tag', '-a', 'v1.0', '-m', 'version', cwd=self.repository)
        self.first = sources.git('rev-parse', 'HEAD', cwd=self.repository)
        (self.repository / 'file').write_text('second')
        sources.git('commit', '-am', 'second', cwd=self.repository)
        self.second = sources.git('rev-parse', 'HEAD', cwd=self.repository)
        self.lock = {'schema': 1, 'version': 'stale-1', 'sources': [{
            'id': 'upstream', 'kind': 'git',
            'source': 'upstream::git+https://example.org/repo.git#branch=main',
            'url': 'https://example.org/repo.git', 'ref': 'refs/heads/main',
            'commit': self.first, 'tag_object': None, 'peeled_commit': None,
            'release_id': None, 'asset_id': None, 'checksums': {'sha256': 'SKIP'},
            'git_context': sources.version_context(self.repository, self.first),
        }]}

    def test_records_actual_commit_and_dynamic_version_without_changing_history(self):
        before = sources.refs(self.repository)
        recorded = actual_source_lock(self.lock, self.root, '1.0.r1-1')
        self.assertEqual(recorded['version'], '1.0.r1-1')
        self.assertEqual(recorded['sources'][0]['commit'], self.second)
        self.assertEqual(recorded['sources'][0]['git_context']['version_tag']['commit'], self.first)
        self.assertEqual(self.lock['sources'][0]['commit'], self.first)
        self.assertEqual(sources.git('rev-parse', 'HEAD', cwd=self.repository), self.second)
        self.assertEqual(sources.refs(self.repository), before)
        self.assertEqual(sources.git('rev-list', '--count', 'HEAD', cwd=self.repository), '2')

    def clone_default_source(self):
        sources.git('branch', '-M', 'main', cwd=self.repository)
        origin = self.root/'origin'
        self.repository.rename(origin)
        sources.git('clone', origin, self.repository)
        self.lock['sources'][0]['source'] = 'git+https://example.org/upstream.git'

    def test_unaliased_git_source_uses_native_basename(self):
        self.clone_default_source()
        self.assertEqual(actual_source_lock(self.lock, self.root, 'new-1')['sources'][0]['commit'], self.second)

    def test_default_source_accepts_normal_makepkg_update_branch(self):
        self.clone_default_source()
        sources.git('checkout', '--force', '--no-track', '-B', 'makepkg', 'origin/HEAD', cwd=self.repository)
        before = sources.refs(self.repository)
        recorded = actual_source_lock(self.lock, self.root, 'new-1')
        self.assertEqual(recorded['sources'][0]['commit'], self.second)
        self.assertEqual(sources.refs(self.repository), before)
        self.assertEqual(sources.git('symbolic-ref', 'HEAD', cwd=self.repository), 'refs/heads/makepkg')

    def test_default_source_rejects_other_remote_default_branch(self):
        self.clone_default_source()
        self.lock['sources'][0]['ref'] = 'refs/heads/not-default'
        with self.assertRaises(ValueError):
            actual_source_lock(self.lock, self.root, 'new-1')

    def test_missing_escaping_and_parent_git_working_copies_are_rejected(self):
        for declaration in ('missing::git+https://example.org/repo.git',
                            '../upstream::git+https://example.org/repo.git'):
            self.lock['sources'][0]['source'] = declaration
            with self.subTest(source=declaration), self.assertRaises(ValueError):
                actual_source_lock(self.lock, self.root, 'new-1')
        child = self.repository / 'child'
        child.mkdir()
        self.lock['sources'][0]['source'] = 'upstream/child::git+https://example.org/repo.git'
        with self.assertRaisesRegex(ValueError, 'own working copy'):
            actual_source_lock(self.lock, self.root, 'new-1')

    def test_tag_records_original_object_and_rejects_different_checkout(self):
        self.lock['sources'][0]['ref'] = 'refs/tags/v1.0'
        with self.assertRaisesRegex(ValueError, 'tag source'):
            actual_source_lock(self.lock, self.root, 'new-1')
        sources.git('checkout', '--detach', 'v1.0', cwd=self.repository)
        actual = actual_source_lock(self.lock, self.root, '1.0-1')['sources'][0]
        self.assertEqual(actual['peeled_commit'], self.first)
        self.assertEqual(actual['tag_object'], sources.git('rev-parse', 'refs/tags/v1.0', cwd=self.repository))
        self.assertNotEqual(actual['tag_object'], actual['commit'])


class PackageMetadataTests(unittest.TestCase):
    def test_restored_cache_survives_next_build_setup(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory, patch('tools.native.os.chown') as chown:
            root = Path(directory)
            first = cache_environment(root)
            artifact = Path(first['CARGO_TARGET_DIR']) / 'compiled-library'
            artifact.write_bytes(b'previous compilation')
            second = cache_environment(root)
            self.assertEqual((Path(second['CARGO_TARGET_DIR']) / artifact.name).read_bytes(), b'previous compilation')
            for call in chown.call_args_list:
                self.assertFalse(call.kwargs['follow_symlinks'])

    def test_cache_cannot_redirect_writable_roots(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory, patch('tools.native.os.chown') as chown:
            root = Path(directory)
            external = root / 'external'
            external.mkdir()
            cache = root / 'cache'
            cache.mkdir()
            (cache / 'cargo').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'unsafe build cache directory'):
                cache_environment(cache)
            chown.assert_not_called()
            linked = root / 'linked'
            linked.symlink_to(cache, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'unsafe build cache root'):
                cache_environment(linked)

    def metadata(self, version='2:1.8.0-1', arch='x86_64'):
        return f'# Generated by makepkg\npkgname = carapace\npkgver = {version}\narch = {arch}\ndepend = glibc\ndepend = bash\n'.encode()

    def test_native_epoch_archive_matches_full_package_version(self):
        info = parse_pkginfo(self.metadata())
        self.assertEqual(
            validate_output(info, 'carapace-2:1.8.0-1-x86_64.pkg.tar.zst',
                            '2:1.8.0-1', {('carapace', 'x86_64')}),
            ('carapace', 'x86_64'))
        for filename, version in [
                ('carapace-1.8.0-1-x86_64.pkg.tar.zst', '2:1.8.0-1'),
                ('carapace-2:1.8.0-1-x86_64.pkg.tar.zst', '1.8.0-1')]:
            with self.subTest(filename=filename, version=version), self.assertRaises(ValueError):
                validate_output(info, filename, version, {('carapace', 'x86_64')})

    def test_empty_optional_package_metadata_preserves_identity(self):
        info = parse_pkginfo(self.metadata() + b'url = \npkgdesc = \n')
        self.assertEqual(info, {'pkgname': 'carapace', 'pkgver': '2:1.8.0-1',
                                'arch': 'x86_64'})

    def test_empty_required_package_metadata_rejected(self):
        for key, value in [('pkgname', 'carapace'), ('pkgver', '2:1.8.0-1'),
                           ('arch', 'x86_64')]:
            data = self.metadata().replace(f'{key} = {value}\n'.encode(),
                                           f'{key} = \n'.encode())
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_pkginfo(data)

    def test_duplicate_identity_fields_rejected(self):
        for field, value in [('pkgname', 'carapace'), ('pkgver', '2:1.8.0-1'), ('arch', 'x86_64')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_pkginfo(self.metadata() + f'{field} = {value}\n'.encode())

    def test_missing_and_invalid_identity_rejected(self):
        for data in [b'pkgname = carapace\narch = x86_64\n', self.metadata(version='x:1.8.0-1'), self.metadata(version='1.8.0'), self.metadata(arch='aarch64'), b'pkgname = ../carapace\npkgver = 1-1\narch = any\n']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_pkginfo(data)

    def test_wrong_output_arch_or_name_rejected(self):
        info = parse_pkginfo(self.metadata())
        for expected in [{('carapace', 'any')}, {('other', 'x86_64')}]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                validate_output(info, 'carapace-2:1.8.0-1-x86_64.pkg.tar.zst', '2:1.8.0-1', expected)

    def test_dependency_names_preserve_native_checks_without_options(self):
        text = """pkgbase = carapace
pkgver = 1.8.0
pkgrel = 1
arch = x86_64
makedepends = go>=1.26.2
checkdepends = git
depends_x86_64 = glibc
pkgname = carapace
depends = bash
"""
        self.assertEqual(dependency_names(text), ['bash', 'git', 'glibc', 'go'])
        for invalid in ('--config', 'go;touch /owned', '/absolute'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                dependency_names(text.replace('go>=1.26.2', invalid))

    def test_split_runtime_dependencies_excluded_but_explicit_bootstrap_preserved(self):
        text = """pkgbase = sentencepiece
pkgver = 1.0
pkgrel = 1
arch = x86_64
makedepends = cmake>=3
checkdepends = python-google-genai>=1
pkgname = sentencepiece-tools
depends = sentencepiece-library=1.0
depends = libprotobuf.so=32-64
pkgname = sentencepiece-library
depends_x86_64 = glibc>=2
pkgname = sentencepiece-python
depends_x86_64 = sentencepiece-tools
"""
        external = ['cmake', 'glibc', 'libprotobuf.so', 'python-google-genai']
        self.assertEqual(dependency_names(text), external)
        bootstrap = text.replace('arch = x86_64\n',
                                 'arch = x86_64\nmakedepends = sentencepiece-library>=0.9\n'
                                 'checkdepends_x86_64 = sentencepiece-tools\n')
        self.assertEqual(dependency_names(bootstrap),
                         sorted([*external, 'sentencepiece-library', 'sentencepiece-tools']))

    def test_invalid_harness_or_commit_identity_rejected(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory:
            root = Path(directory)
            recipe = root / 'recipes/carapace'
            recipe.mkdir(parents=True)
            (recipe / 'PKGBUILD').write_text('pkgname=carapace')
            (recipe / '.SRCINFO').write_text('native metadata')
            bundle = {
                'schema': 1, 'repository': 'vith/arch-packages',
                'base': 'a' * 40, 'head': 'b' * 40,
                'harness_sha': 'c' * 64,
                'image': 'ghcr.io/archlinux/archlinux@sha256:' + 'd' * 64,
                'run_id': 1, 'run_attempt': 1,
                'recipe_pins': {'carapace': 'f' * 40},
                'packages': [{'pkgbase': 'carapace', 'recipe_dir': 'recipes/carapace',
                              'recipe_commit': 'f' * 40,
                              'input_digest': 'e' * 64,
                              'lock': {'schema': 1, 'version': '1.8.0-1'}}],
            }
            path = root / 'bundle.json'
            path.write_text(json.dumps(bundle))
            validate_bundle(path)
            altered = json.loads(json.dumps(bundle))
            altered['packages'][0]['recipe_commit'] = '0' * 40
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'gitlink'):
                validate_bundle(path)
            for field, value in [('harness_sha', 'c' * 40), ('base', 'a' * 64), ('head', 'b' * 64)]:
                invalid = dict(bundle, **{field: value})
                path.write_text(json.dumps(invalid))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_bundle(path)

    def test_input_path_does_not_escape_artifact(self):
        state = Path.home() / '.local/state/arch-packages/work/native-test'
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory:
            root = Path(directory)
            (root / 'recipes').mkdir()
            os.symlink(state, root / 'escape')
            self.assertEqual(contained(root, 'recipes/PKGBUILD'), root / 'recipes/PKGBUILD')
            for name in ['../PKGBUILD', '/etc/passwd', 'escape/outside', 'recipes/../../outside']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    contained(root, name)


if __name__ == '__main__':
    unittest.main()
