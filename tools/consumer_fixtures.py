"""Reviewed consumer prerequisites, separate from signed package source locks."""

import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import tarfile
import urllib.request


def microsandbox_fixture(package, metadata, target):
    """Select an exact upstream-version/platform fixture; never discover a latest."""
    if not isinstance(metadata, dict) or type(metadata.get('schema')) is not int or metadata['schema'] != 1:
        raise ValueError('unsupported consumer fixture schema')
    version = package['source_lock']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-[0-9]+(?:\.[0-9]+)*', version):
        raise ValueError(f'unsupported microsandbox package version: {version}')
    upstream = version.rsplit('-', 1)[0]
    versions = metadata.get('microsandbox', {})
    targets = versions.get(upstream, {}) if isinstance(versions, dict) else None
    if not isinstance(targets, dict):
        raise ValueError('invalid microsandbox consumer fixture version map')
    fixture = targets.get(target)
    if not isinstance(fixture, dict):
        raise ValueError(f'missing microsandbox consumer fixture: {upstream} {target}')
    if target != 'linux-x86_64':
        raise ValueError(f'unsupported microsandbox consumer platform: {target}')
    expected_url = ('https://github.com/superradcompany/microsandbox/releases/download/'
                    f'v{upstream}/microsandbox-{target}.tar.gz')
    if (fixture.get('url') != expected_url or
            not re.fullmatch('[a-f0-9]{64}', str(fixture.get('sha256', ''))) or
            any(type(fixture.get(key)) is not int or fixture[key] <= 0
                for key in ('release_id', 'asset_id'))):
        raise ValueError(f'invalid microsandbox consumer fixture: {upstream} {target}')
    return fixture


def unpack_runtime(archive, digest, home):
    """Authenticate archive bytes before extracting exactly the genuine runtime pair."""
    with archive.open('rb') as file:
        actual = hashlib.file_digest(file, 'sha256').hexdigest()
    if actual != digest:
        raise ValueError('microsandbox runtime prerequisite digest mismatch')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if (len(members) != 2 or
                {m.name for m in members} != {'msb', 'libkrunfw.so.5.6.1'} or
                not all(m.isfile() for m in members)):
            raise ValueError('unexpected runtime prerequisite archive members')
        for member in members:
            destination = home / ('bin' if member.name == 'msb' else 'lib') / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(tar.extractfile(member).read())
            destination.chmod(0o755)


def prepare(expected, metadata_path, archive, home, evidence):
    packages = json.loads(expected.read_text())['packages']
    package = next((p for p in packages if p['pkgbase'] == 'microsandbox'), None)
    if package is None:
        return
    target = f'{platform.system().lower()}-{platform.machine()}'
    fixture = microsandbox_fixture(package, json.loads(metadata_path.read_text()), target)
    with urllib.request.urlopen(fixture['url'], timeout=120) as response, archive.open('wb') as output:
        while block := response.read(1024 * 1024):
            output.write(block)
    unpack_runtime(archive, fixture['sha256'], home)
    proof = {'fixture': fixture, 'platform': target,
             'package_version': package['source_lock']['version'],
             'sha256': fixture['sha256'],
             'role': 'separately acquired runtime prerequisite, not package payload'}
    evidence.write_text(json.dumps(proof, sort_keys=True) + '\n')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 6:
            raise ValueError('usage: consumer_fixtures.py expected.json fixtures.json archive home evidence.json')
        prepare(*(Path(argument) for argument in sys.argv[1:]))
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(f'consumer prerequisite preparation failed: {error}') from error
