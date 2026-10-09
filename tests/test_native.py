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

from tools.native import cache_environment, contained, dependency_names, parse_pkginfo, runtime_identity, validate_bundle, validate_output


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

    def test_epoch_retained_but_not_in_filename(self):
        info = parse_pkginfo(self.metadata())
        self.assertEqual(validate_output(info, 'carapace-1.8.0-1-x86_64.pkg.tar.zst', '2:1.8.0-1', {('carapace', 'x86_64')}), ('carapace', 'x86_64'))
        for filename, version in [('carapace-2:1.8.0-1-x86_64.pkg.tar.zst', '2:1.8.0-1'), ('carapace-1.8.0-1-x86_64.pkg.tar.zst', '1.8.0-1')]:
            with self.subTest(filename=filename, version=version), self.assertRaises(ValueError):
                validate_output(info, filename, version, {('carapace', 'x86_64')})

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
                validate_output(info, 'carapace-1.8.0-1-x86_64.pkg.tar.zst', '2:1.8.0-1', expected)

    def test_omp_identity_uses_locked_ancestry(self):
        self.assertEqual(runtime_identity('14.2.0.vith.r7.g0123456789ab-2'), '14.2.0+vith-fork.7.0123456789ab')
        for version in ['r25173.c1a969eada61-2', '14.2.0.vith.r7.g0123456-1', '14.2.0-1']:
            with self.subTest(version=version), self.assertRaises(ValueError):
                runtime_identity(version)

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
