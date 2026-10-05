"""Derive clean-install expectations from a verified signed snapshot."""
import argparse
import base64
import json
from pathlib import Path
import re

from tools import github_api, publish


def snapshot_inputs(catalog, directory, policies):
    if {policy['pkgbase'] for policy in policies} != set(catalog['recipes']):
        raise ValueError('snapshot differs from enrolled package set')
    packages = []
    for policy in policies:
        name = policy['pkgbase']
        recipe = catalog['recipes'][name]
        version = recipe['version']
        files = []
        for output in policy['outputs']:
            filename = f"{output['name']}-{version.split(':', 1)[-1]}-{output['arch']}.pkg.tar.zst"
            if not publish.NAME.fullmatch(filename) or filename not in catalog['files']:
                raise ValueError('enrolled package absent from signed snapshot')
            path = directory / filename
            if publish.sha(path) != catalog['files'][filename]['sha256']:
                raise ValueError('snapshot package hash mismatch')
            identity = publish.pkginfo(path)
            if identity != {'pkgname': output['name'], 'pkgver': version, 'arch': output['arch']}:
                raise ValueError('snapshot package identity mismatch')
            files.append({'name': output['name'], 'version': version, 'arch': output['arch'], 'filename': filename})
        packages.append({'pkgbase': name, 'source_lock': {'schema': 1, 'version': version, 'sources': recipe['sources']}, 'files': files})
    return {'pacman_repository': publish.catalog_database_name(catalog), 'packages': packages}


def prepare(snapshot, work):
    if not re.fullmatch(r'snapshot-[0-9a-f]{40}-[1-9][0-9]*-[1-9][0-9]*', snapshot):
        raise ValueError('invalid snapshot tag')
    work.mkdir(parents=True, exist_ok=False)
    ring = publish.keyring(work)
    release = github_api.api(f'repos/{publish.REPOSITORY}/releases/tags/{snapshot}', authenticated=False)
    if release['draft'] or release['prerelease'] or release['tag_name'] != snapshot:
        raise ValueError('snapshot is not a public package release')
    catalog = publish.verified_snapshot({'id': release['id'], 'tag': snapshot}, work / 'snapshot', ring)
    enrollment = github_api.api(
        f"repos/vith/arch-packages/contents/packages.json?ref={catalog['accepted_sha']}",
        authenticated=False,
    )
    content = enrollment['content'].replace('\n', '').replace('\r', '')
    enrollment = json.loads(base64.b64decode(content, validate=True))
    expectations = snapshot_inputs(catalog, work / 'snapshot/complete', enrollment['packages'])
    (work / 'enrollment.json').write_bytes(github_api.canonical(enrollment))
    (work / 'expected.json').write_bytes(github_api.canonical(expectations))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    prepare(args.snapshot, args.directory)
