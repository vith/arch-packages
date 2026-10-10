"""Temporary read-only hosted proof; never a production build descriptor."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from tools import native, recipe_gate

NAME = 'pr-build-pipeline-smoke'
SOURCE = 'code::git+https://github.com/vith/arch-packages.git#branch=main'
PKGBUILD = '''pkgname=pr-build-pipeline-smoke
pkgver=0
pkgrel=1
pkgdesc='Temporary hosted native build proof'
arch=('x86_64')
url='https://github.com/vith/arch-packages'
license=('MIT')
depends=('glibc')
makedepends=('base-devel' 'git')
source=('code::git+https://github.com/vith/arch-packages.git#branch=main')
sha256sums=('SKIP')
pkgver() {
  printf 'r%s.g%s' "$(git -C "$srcdir/code" rev-list --count HEAD)" "$(git -C "$srcdir/code" rev-parse --short HEAD)"
}
build() {
  local commit
  commit=$(git -C "$srcdir/code" rev-parse HEAD)
  printf '#include <stdio.h>\\nint main(void) { puts("%s"); return 0; }\\n' "$commit" > "$srcdir/main.c"
  gcc ${CFLAGS:-} "$srcdir/main.c" -o "$srcdir/pr-build-pipeline-smoke" ${LDFLAGS:-}
}
check() {
  local observed expected
  observed=$("$srcdir/pr-build-pipeline-smoke")
  expected=$(git -C "$srcdir/code" rev-parse HEAD)
  printf 'native_check_commit=%s\\n' "$observed"
  test "$observed" = "$expected"
}
package() {
  install -Dm755 "$srcdir/pr-build-pipeline-smoke" "$pkgdir/usr/bin/pr-build-pipeline-smoke"
}
'''
SRCINFO = '''pkgbase = pr-build-pipeline-smoke
	pkgdesc = Temporary hosted native build proof
	pkgver = 0
	pkgrel = 1
	url = https://github.com/vith/arch-packages
	arch = x86_64
	license = MIT
	makedepends = base-devel
	makedepends = git
	depends = glibc
	source = code::git+https://github.com/vith/arch-packages.git#branch=main
	sha256sums = SKIP

pkgname = pr-build-pipeline-smoke
'''


def prepare(root, work):
    bundle_root = work / 'bundle'
    recipe = bundle_root / 'recipe'
    recipe.mkdir(parents=True)
    (recipe / 'PKGBUILD').write_text(PKGBUILD)
    (recipe / '.SRCINFO').write_text(SRCINFO)
    head = os.environ['GITHUB_SHA']
    image = (root / 'build-image.txt').read_text().strip()
    harness = recipe_gate.harness_digest(root)
    lock = {'schema': 1, 'version': '0-1', 'sources': [{
        'id': 'code', 'kind': 'git', 'source': SOURCE,
        'url': 'https://github.com/vith/arch-packages.git',
        'ref': 'refs/heads/main', 'commit': 'f5b1f735737944150331d06e0b05506f5c7d6957',
        'tag_object': None, 'peeled_commit': None,
        'git_context': {'object_format': 'sha1', 'version_tag': None},
        'checksums': {'sha256': 'SKIP'},
    }]}
    policy = {'pkgbase': NAME, 'outputs': [{'name': NAME, 'arch': 'x86_64'}]}
    package = {'pkgbase': NAME, 'recipe_commit': head, 'recipe_dir': 'recipe',
               'lock': lock, 'policy': policy, 'expected_srcinfo': SRCINFO,
               'input_digest': recipe_gate.input_digest(recipe, lock, policy, image, harness)}
    bundle = {'schema': 1, 'smoke_only': True, 'repository': 'vith/arch-packages',
              'base': 'f5b1f735737944150331d06e0b05506f5c7d6957', 'head': head,
              'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
              'image': image, 'harness_sha': harness, 'recipe_pins': {NAME: head},
              'packages': [package]}
    (bundle_root / 'bundle.json').write_bytes(native.canonical(bundle))
    native.validate_bundle(bundle_root / 'bundle.json')
    print('Prepared unsigned standalone native smoke input; no production request or signing authority.')


def verify(work):
    output = work / 'native'
    evidence = json.loads((output / 'native-evidence.json').read_text())
    receipt, = evidence['packages']
    source, = receipt['source_lock']['sources']
    package, = receipt['files']
    if not evidence['smoke_only'] or receipt['pkgbase'] != NAME:
        raise ValueError('not standalone smoke output')
    if source['source'] != SOURCE or not re.fullmatch(r'[0-9a-f]{40}', source['commit']):
        raise ValueError('normal branch source/full commit not retained')
    if not re.fullmatch(r'[0-9a-f]{64}', source['git_context']['tag_refs_sha256']):
        raise ValueError('reachable tag context not captured')
    if not receipt['metadata']['dynamic_pkgver'] or not re.fullmatch(r'r[0-9]+\.g[0-9a-f]{7,}-1', receipt['metadata']['version']):
        raise ValueError('normal dynamic pkgver not observed')
    if set(package) != {'filename', 'name', 'version', 'arch'}:
        raise ValueError('native receipt unexpectedly includes package byte hashes')
    archive = output / package['filename']
    identity = native.parse_pkginfo(subprocess.check_output(['bsdtar', '-xOf', str(archive), '.PKGINFO']))
    if identity['pkgname'] != NAME or identity['pkgver'] != receipt['metadata']['version']:
        raise ValueError('archive native identity differs from actual metadata')
    runtime = work / 'runtime'
    runtime.mkdir()
    subprocess.run(['bsdtar', '-xf', str(archive), '-C', str(runtime), 'usr/bin/' + NAME], check=True)
    command = ['sudo', 'setpriv', '--reuid', str(os.getuid()), '--regid', str(os.getgid()),
               '--clear-groups', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
               '--no-new-privs', str(runtime / 'usr/bin' / NAME)]
    observed = subprocess.check_output(command, text=True, timeout=10).strip()
    if observed != source['commit']:
        raise ValueError('installed binary does not report actual compiled Git commit')
    report = {'smoke_only': True, 'source_commit': source['commit'],
              'actual_version': identity['pkgver'], 'declared_version': '0-1',
              'tag_context_sha256': source['git_context']['tag_refs_sha256'],
              'binary_stdout': observed, 'dynamic_pkgver': True}
    (work / 'native-smoke-report.json').write_bytes(native.canonical(report))
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    root = Path.cwd()
    work = Path(os.environ['SMOKE_WORK'])
    if sys.argv[1:] == ['prepare']:
        prepare(root, work)
    elif sys.argv[1:] == ['verify']:
        verify(work)
    else:
        raise SystemExit('usage: python -m tools.hosted_cutover_smoke prepare|verify')
