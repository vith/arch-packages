#!/usr/bin/env python3
"""Trusted native Arch harness; candidate recipes remain unprivileged data."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()



def parse_pkginfo(data: bytes) -> dict:
    fields = {}
    for line in data.decode('utf-8', errors='strict').splitlines():
        if not line or line.startswith('#'):
            continue
        if ' = ' not in line:
            raise ValueError('malformed .PKGINFO line')
        key, value = line.split(' = ', 1)
        if not key or not value:
            raise ValueError('empty .PKGINFO field')
        fields.setdefault(key, []).append(value)
    result = {}
    for key in ('pkgname', 'pkgver', 'arch'):
        if len(fields.get(key, [])) != 1:
            raise ValueError(f'missing/duplicate .PKGINFO {key}')
        result[key] = fields[key][0]
    if not re.fullmatch(r'[a-zA-Z0-9@._+\-]+', result['pkgname']):
        raise ValueError('invalid package name')
    if not re.fullmatch(r'(?:[0-9]+:)?[^\s/:]+-[0-9]+(?:\.[0-9]+)*', result['pkgver']):
        raise ValueError('invalid package version including epoch')
    if result['arch'] not in ('any', 'x86_64'):
        raise ValueError('unexpected package architecture')
    return result


def validate_output(info: dict, filename: str, version: str, expected: set) -> tuple:
    identity = (info['pkgname'], info['arch'])
    native_filename = f"{info['pkgname']}-{version.split(':', 1)[-1]}-{info['arch']}.pkg.tar.zst"
    if identity not in expected or info['pkgver'] != version or filename != native_filename:
        raise ValueError('output name/version/architecture/filename mismatch')
    return identity


def contained(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('unsafe input path')
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('escaping input path')
    return path


def validate_bundle(path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('bundle metadata exceeds 8 MiB')
    bundle = json.loads(path.read_text())
    if bundle.get('schema') != 1 or not bundle.get('packages'):
        raise ValueError('unsupported/empty bundle')
    for field in ('base', 'head'):
        if not re.fullmatch(r'[0-9a-f]{40}', str(bundle.get(field, ''))):
            raise ValueError(f'invalid {field}')
    if not re.fullmatch(r'[0-9a-f]{64}', str(bundle.get('harness_sha', ''))):
        raise ValueError('invalid stable harness digest')
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', bundle.get('image', '')):
        raise ValueError('untrusted build image')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', bundle.get('repository', '')):
        raise ValueError('invalid repository')
    for key in ('run_id', 'run_attempt'):
        if not str(bundle.get(key, '')).isdigit() or int(bundle[key]) < 1:
            raise ValueError(f'invalid {key}')
    pins = bundle.get('recipe_pins')
    if not isinstance(pins, dict) or not pins or any(not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name) or not re.fullmatch(r'[0-9a-f]{40}', str(sha)) for name, sha in pins.items()):
        raise ValueError('immutable recipe pins required')
    root = path.parent.resolve()
    allowed = {path.name}
    names = set()
    recipe_roots = []
    for package in bundle['packages']:
        name = package['pkgbase']
        if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name) or name in names:
            raise ValueError('invalid/duplicate pkgbase')
        names.add(name)
        if package.get('recipe_commit') != pins.get(name):
            raise ValueError('recipe commit differs from bound control gitlink')
        recipe = contained(root, package['recipe_dir'])
        allowed.add(Path(package['recipe_dir']).parts[0])
        recipe_roots.append(recipe)
        if not recipe.is_dir() or not (recipe / 'PKGBUILD').is_file() or not (recipe / '.SRCINFO').is_file():
            raise ValueError('missing recipe')
        for item in recipe.rglob('*'):
            if item.is_symlink():
                if not item.resolve().is_relative_to(recipe.resolve()):
                    raise ValueError('escaping recipe symlink')
            elif not (item.is_file() or item.is_dir()):
                raise ValueError('special input file')
        if not re.fullmatch(r'[0-9a-f]{64}', package['input_digest']):
            raise ValueError('invalid input digest')
        if package['lock'].get('schema') != 1 or not package['lock'].get('version'):
            raise ValueError('invalid source lock')
    if {p.name for p in root.iterdir()} != allowed:
        raise ValueError('unexpected artifact top-level entries')
    total = 0
    entries = list(root.rglob('*'))
    if len(entries) > 10000:
        raise ValueError('artifact has too many entries')
    for item in entries:
        if item == path or item.is_dir() and not item.is_symlink():
            continue
        if not any(item.is_relative_to(recipe) for recipe in recipe_roots):
            raise ValueError('unexpected artifact file outside recipe trees')
        if not item.is_symlink():
            total += item.stat().st_size
    if total > 64 * 1024 * 1024:
        raise ValueError('recipe payload exceeds 64 MiB')
    return bundle


def dependency_names(srcinfo: str) -> list[str]:
    from tools.recipe_gate import parse_srcinfo
    info = parse_srcinfo(srcinfo)
    dependencies = set()
    for scope, key, value in info['fields']:
        if key in ('depends', 'makedepends', 'checkdepends', 'depends_x86_64', 'makedepends_x86_64', 'checkdepends_x86_64'):
            name = re.split(r'[<>=]', value, maxsplit=1)[0]
            if not re.fullmatch(r'[a-zA-Z0-9@._+-]+', name) or name.startswith('-'):
                raise ValueError('unsafe dependency name')
            dependencies.add(name)
    return sorted(dependencies)


def run(argv, *, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, check=True, text=True, stdout=subprocess.PIPE).stdout


def builder(argv, cwd, env):
    return run(['setpriv', '--reuid=1000', '--regid=1000', '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', '--', *argv], cwd=cwd, env=env)


def readonly(root):
    for path in [*root.rglob('*'), root]:
        if path.is_symlink():
            continue
        os.chown(path, 0, 0)
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)


def runtime_identity(version):
    match = re.fullmatch(r'(?:[0-9]+:)?(.+)\.vith\.r([0-9]+)\.g([0-9a-f]{12})-[0-9]+(?:\.[0-9]+)*', version)
    if not match:
        raise ValueError('OMP lock lacks exact fork version context')
    return f'{match[1]}+vith-fork.{match[2]}.{match[3]}'


def omp_proof(cli: Path, addon: Path, version: str, cwd: Path, env: dict) -> dict:
    expected = runtime_identity(version)
    observed = builder([str(cli), '--version'], cwd, env).strip()
    if observed != 'omp/' + expected:
        raise ValueError(f'OMP runtime identity mismatch: expected {expected!r}, got {observed!r}')
    help_text = builder([str(cli), '--help'], cwd, env)
    if not help_text.strip():
        raise ValueError('OMP help absent')
    stamp = expected.encode()
    if stamp not in addon.read_bytes():
        raise ValueError('native addon does not contain exact fork stamp')
    dynamic = run(['readelf', '-d', str(addon)])
    if 'libpipewire-0.3.so.0' not in dynamic:
        raise ValueError('native addon lacks PipeWire linkage')
    return {'runtime_identity': expected, 'cli_version': observed, 'help': help_text, 'addon_sha256': hashlib.sha256(addon.read_bytes()).hexdigest(), 'dynamic': dynamic}


def build(path: Path, output: Path):
    if os.geteuid() != 0 or os.uname().machine != 'x86_64' or not Path('/etc/arch-release').exists():
        raise ValueError('native build requires disposable root Arch x86_64 container')
    from tools.recipe_gate import parse_srcinfo, tree_manifest, input_digest, harness_digest
    from tools.sources import materialize_sources
    bundle = validate_bundle(path)
    if harness_digest(Path(__file__).resolve().parent.parent) != bundle['harness_sha']:
        raise ValueError('executing harness differs from bound stable digest')
    output.mkdir(parents=True)
    work = Path('/build')
    work.mkdir(mode=0o755)
    run(['useradd', '--uid', '1000', '--create-home', '--shell', '/bin/bash', 'builder'])
    dependencies = {'git', 'binutils'}
    for package in bundle['packages']:
        recipe = contained(path.parent, package['recipe_dir'])
        info = parse_srcinfo((recipe / '.SRCINFO').read_text())
        if info['pkgbase'] != package['pkgbase']:
            raise ValueError('recipe pkgbase mismatch')
        outputs = package['policy']['outputs']
        identities = {(item['name'], item['arch']) for item in outputs}
        if len(identities) != len(outputs) or {name for name, arch in identities} != set(info['names']):
            raise ValueError('policy output list differs from complete native metadata')
        # Strip only native version constraints, never interpret dependency names as options.
        dependencies.update(dependency_names((recipe / '.SRCINFO').read_text()))
        digest = input_digest(recipe, package['lock'], package['policy'], bundle['image'], bundle['harness_sha'])
        if digest != package['input_digest']:
            raise ValueError('input digest mismatch')
    run(['pacman', '-S', '--noconfirm', '--needed', '--', *sorted(dependencies)])
    # Image defaults are inherited; do not copy runner-specific tuning or replace OPTIONS.
    config = work / 'makepkg.conf'
    config.write_text('source /etc/makepkg.conf\nCARCH=x86_64\nPKGEXT=.pkg.tar.zst\nMAKEFLAGS="-j$(nproc)"\nOPTIONS=("${OPTIONS[@]/#debug/!debug}")\n_options=(); for _option in "${OPTIONS[@]}"; do [[ $_option == debug || $_option == !debug ]] || _options+=("$_option"); done\nOPTIONS=("${_options[@]}" !debug)\n')
    config.chmod(0o444)
    receipts = []
    for package in bundle['packages']:
        name = package['pkgbase']
        original = contained(path.parent, package['recipe_dir'])
        directory = work / name
        shutil.copytree(original, directory, symlinks=True)
        home = work / (name + '-home')
        home.mkdir()
        mirrors = work / (name + '-mirrors')
        mapping = materialize_sources(package['lock'], mirrors)
        readonly(mirrors)
        gitconfig = home / '.gitconfig'
        gitconfig.write_text('[core]\n hooksPath = /dev/null\n[protocol "file"]\n allow = always\n')
        for url, mirror in mapping.items():
            run(['git', 'config', '--file', str(gitconfig), '--add', f'url.file://{mirror}.insteadOf', url])
            run(['git', 'config', '--file', str(gitconfig), '--add', 'safe.directory', str(mirror)])
        for p in [directory, *directory.rglob('*')]:
            if not p.is_symlink():
                os.chown(p, 1000, 1000)
        for cache in ('.cache', '.cargo', '.rustup', '.bun', '.config', 'go'):
            cache_dir = home / cache
            cache_dir.mkdir()
            os.chown(cache_dir, 1000, 1000)
        gitconfig.chmod(0o444)
        env = {'PATH': '/usr/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'GOTOOLCHAIN': 'local', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(gitconfig)}
        command = ['makepkg', '--config', str(config)]
        prepared_log = builder([*command, '--nobuild', '--noconfirm', '--cleanbuild'], directory, env)
        prepared = builder([*command, '--printsrcinfo'], directory, env)
        if prepared != (original / '.SRCINFO').read_text():
            raise ValueError(f'{name}: complete prepared .SRCINFO differs from accepted metadata')
        metadata = parse_srcinfo(prepared)
        if metadata['version'] != package['lock']['version']:
            raise ValueError(f'{name}: native version differs from frozen lock')
        if name == 'carapace':
            modules = list((directory / 'src').rglob('go.mod'))
            roots = [p for p in modules if p.parent.name.startswith('carapace-bin')]
            if len(roots) != 1:
                raise ValueError('carapace primary go.mod missing/ambiguous')
            requirement = re.search(r'^go ([0-9]+\.[0-9]+(?:\.[0-9]+)?)$', roots[0].read_text(), re.M)
            installed = re.search(r'go([0-9]+\.[0-9]+(?:\.[0-9]+)?)', builder(['go', 'version'], directory, env))
            version_tuple = lambda value: tuple(int(x) for x in value.split('.')) + (0,) * (3-len(value.split('.')))
            if not requirement or not installed or version_tuple(installed[1]) < version_tuple(requirement[1]):
                raise ValueError('installed Go does not meet carapace go.mod (GOTOOLCHAIN=local)')
        build_log = builder([*command, '--noextract', '--noconfirm'], directory, env)
        if builder([*command, '--printsrcinfo'], directory, env) != prepared:
            raise ValueError('native metadata changed during build')
        files = []
        expected = {(x['name'], x['arch']) for x in package['policy']['outputs']}
        found = set()
        for archive in sorted(directory.glob('*.pkg.tar.zst')):
            data = subprocess.check_output(['bsdtar', '-xOf', str(archive), '.PKGINFO'])
            info = parse_pkginfo(data)
            identity = validate_output(info, archive.name, metadata['version'], expected)
            if identity in found:
                raise ValueError(f'duplicate output metadata: {info}')
            found.add(identity)
            target = output / archive.name
            if target.exists():
                raise ValueError('duplicate output filename')
            shutil.copyfile(archive, target)
            files.append({'filename': archive.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'name': info['pkgname'], 'version': info['pkgver'], 'arch': info['arch']})
        if found != expected:
            raise ValueError(f'missing outputs: {expected-found}')
        proof = None
        if name == 'oh-my-pi-vith-git':
            addons = list((directory / 'src').rglob('pi_natives.linux-x64-baseline.node'))
            if len(addons) != 1:
                raise ValueError('OMP native addon missing/ambiguous')
            cli = directory / 'pkg' / name / 'usr/bin/omp'
            proof = omp_proof(cli, addons[0], metadata['version'], directory, env)
        metadata['srcinfo'] = prepared
        receipts.append({'pkgbase': name, 'recipe_commit': package['recipe_commit'], 'files': files, 'metadata': metadata, 'source_lock': package['lock'], 'input_digest': package['input_digest'], 'tree_sha': hashlib.sha256(canonical(tree_manifest(original))).hexdigest(), 'run_id': bundle['run_id'], 'run_attempt': bundle['run_attempt'], 'image': bundle['image'], 'harness_sha': bundle['harness_sha'], 'runtime': proof, 'prepare_log': prepared_log, 'build_log': build_log})
    evidence = {key: value for key, value in bundle.items() if key != 'packages'}
    evidence['packages'] = receipts
    (output / 'native-evidence.json').write_bytes(canonical(evidence))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 3 and sys.argv[1] == 'validate':
            validate_bundle(Path(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == 'build':
            build(Path(sys.argv[2]), Path(sys.argv[3]))
        else:
            raise ValueError('usage: native.py validate bundle.json | build bundle.json output-dir')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stdout:
            print(exc.stdout, file=sys.stderr)
        print(f'native build failed: {exc}', file=sys.stderr)
        sys.exit(1)
