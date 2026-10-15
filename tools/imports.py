"""Non-executable imported roots and narrowly authenticated metadata admission."""
from pathlib import Path
import copy
import json
import re

from tools import sources
from tools.recipe_gate import parse_srcinfo, tree_manifest

NAME = re.compile(r'[a-z0-9][a-z0-9+_.-]*')
SHA = re.compile(r'[0-9a-f]{40}')


def registry(root):
    value = json.loads((Path(root)/'packages.json').read_text())
    if value.get('schema') != 1 or not isinstance(value.get('packages'), list) or not isinstance(value.get('imports', []), list):
        raise ValueError('invalid package registry')
    accepted, pending = {}, {}
    for policy in value['packages']:
        name = policy.get('pkgbase')
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in accepted:
            raise ValueError('invalid/duplicate accepted package')
        accepted[name] = policy
    for item in value.get('imports', []):
        if set(item) != {'pkgbase', 'origin', 'policy'}:
            raise ValueError('invalid pending import fields')
        name = item['pkgbase']
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in accepted or name in pending or item['policy'].get('pkgbase') != name:
            raise ValueError('invalid/duplicate pending import')
        origin = item['origin']
        if set(origin) != {'url', 'commit', 'tree', 'manifest', 'pkgbase'} or origin['pkgbase'] != name:
            raise ValueError('invalid import origin fields')
        sources.public_url(origin['url'])
        if origin['url'] != 'https://git.n3t.work/vith/arch-pkg-' + name + '.git':
            raise ValueError('import origin is not the trusted per-package repository')
        if any(not isinstance(origin[k], str) or not SHA.fullmatch(origin[k]) for k in ('commit', 'tree')) or not isinstance(origin['manifest'], list) or not origin['manifest']:
            raise ValueError('incomplete immutable import origin')
        pending[name] = item
    return value, accepted, pending


def policies(root):
    _, accepted, pending = registry(root)
    return {**accepted, **{name: item['policy'] for name, item in pending.items()}}


def authenticate(item, destination):
    """Fetch the exact public origin object directly, never refs or submodules."""
    from tools import update as u
    origin = item['origin']
    name = item['pkgbase']
    if not isinstance(name, str) or not NAME.fullmatch(name) or origin['url'] != 'https://git.n3t.work/vith/arch-pkg-'+name+'.git' or origin['pkgbase'] != name or any(not SHA.fullmatch(origin[k]) for k in ('commit', 'tree')):
        raise ValueError('invalid trusted import origin')
    sources.public_url(origin['url'])
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    repo = destination/'origin.git'
    sources.git('init', '--bare', repo)
    sources.git('fetch', '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head', '--', origin['url'], origin['commit'], cwd=repo)
    if sources.git('rev-parse', origin['commit']+'^{commit}', cwd=repo) != origin['commit'] or sources.git('rev-parse', origin['commit']+'^{tree}', cwd=repo) != origin['tree']:
        raise ValueError('import origin commit/tree mismatch')
    # Reject gitlinks before archive; git archive silently omits their payloads.
    rows = sources.git('ls-tree', '-r', origin['commit'], cwd=repo).splitlines()
    if any(row.startswith('160000 ') for row in rows):
        raise ValueError('import root contains nested submodules')
    archive = destination/'root.tar'
    sources.git('archive', '--format=tar', '--prefix=import-root/', '--output='+str(archive.resolve()), origin['commit'], cwd=repo)
    if archive.stat().st_size > u.MAX_TREE:
        raise ValueError('import origin exceeds bound')
    root = destination/'root'
    u.extract_tree(archive, root)
    if tree_manifest(root) != origin['manifest'] or parse_srcinfo((root/'.SRCINFO').read_text())['pkgbase'] != item['pkgbase']:
        raise ValueError('import original manifest/pkgbase mismatch')
    if not (root/'PKGBUILD').is_file() or (root/'PKGBUILD').is_symlink():
        raise ValueError('import lacks original regular PKGBUILD')
    return root


def verify_predecessor(root, name, pins, work, expected=None):
    _, accepted, pending = registry(root)
    item = pending.get(name)
    if item is None or expected is not None and item != expected:
        raise ValueError('pending conversion registration changed')
    if pins.get(name) != item['origin']['commit'] or tree_manifest(Path(root)/'recipes'/name) != item['origin']['manifest']:
        raise ValueError('pending conversion original predecessor changed')
    authenticate(item, work)
    return item


def activate(value, item):
    result = copy.deepcopy(value)
    if item not in result.get('imports', []) or any(p['pkgbase'] == item['pkgbase'] for p in result['packages']):
        raise ValueError('pending activation is not exact or already accepted')
    result['imports'].remove(item)
    result['packages'].append(copy.deepcopy(item['policy']))
    return result


def validate_admission(old, new, oldpins, newpins, work):
    """Return admitted/revised pending names, or None for the ordinary route."""
    from tools import update as u
    before, accepted, pending = registry(old)
    after, newaccepted, newpending = registry(new)
    revised = {name for name, item in pending.items() if name in newpending and (
        item != newpending[name] or any(
            not (Path(old)/relative).is_file() or not (Path(new)/relative).is_file()
            or (Path(old)/relative).read_bytes() != (Path(new)/relative).read_bytes()
            for relative in ('inputs/'+name+'.json', 'upstream/'+name+'.json')))}
    if pending == newpending and not revised:
        if before.get('imports', []) != after.get('imports', []):
            raise ValueError('ordinary main route cannot rewrite pending registrations')
        for name in pending:
            if oldpins.get(name) != newpins.get(name):
                raise ValueError('ordinary main route cannot change pending recipe pins')
            for relative in ('recipes/'+name, 'inputs/'+name+'.json', 'upstream/'+name+'.json'):
                a, b = Path(old)/relative, Path(new)/relative
                if a.is_dir():
                    if not b.is_dir() or tree_manifest(a) != tree_manifest(b):
                        raise ValueError('ordinary main route cannot change pending recipe data')
                elif not a.is_file() or not b.is_file() or a.read_bytes() != b.read_bytes():
                    raise ValueError('ordinary main route cannot change pending intended data')
        return None
    added = set(newpending)-set(pending)
    from tools import recipes
    recipes._modules(Path(old), u.repository())
    recipes._modules(Path(new), u.repository())
    if not (added or revised) or accepted != newaccepted or not set(pending) <= set(newpending):
        raise ValueError('admission may only add or revise pending imports')
    if any(newpending[name]['origin'] != item['origin'] for name, item in pending.items()):
        raise ValueError('pending revision cannot change imported origin')
    intended = added | revised
    expected = copy.deepcopy(before)
    expected['imports'] = [newpending[item['pkgbase']] for item in before.get('imports', [])] + [
        item for item in after.get('imports', []) if item['pkgbase'] in added]
    if after != expected:
        raise ValueError('admission changed unrelated registry data')
    if set(newpins) != set(accepted)|set(newpending) or any(newpins.get(n) != pin for n, pin in oldpins.items()):
        raise ValueError('admission changed or omitted existing recipe pins')
    a = {row['path']: row for row in tree_manifest(old)}
    b = {row['path']: row for row in tree_manifest(new)}
    changed = {p for p in a.keys()|b.keys() if a.get(p) != b.get(p)}
    allowed = {'packages.json', '.gitmodules', 'README.md'}
    for name in intended:
        allowed.update({'inputs/'+name+'.json', 'upstream/'+name+'.json'})
    if any(path not in allowed and not any(path.startswith('recipes/'+n+'/') for n in added) for path in changed):
        raise ValueError('admission contains non-metadata or existing package changes')
    for name in sorted(intended):
        item = verify_predecessor(new, name, newpins, Path(work)/name)
        lock = u.load(Path(new)/'inputs'/f'{name}.json')
        provenance = u.load(Path(new)/'upstream'/f'{name}.json')
        canonical_aur = False
        if name in revised:
            previous = u.load(Path(old)/'upstream'/f'{name}.json')
            canonical_aur = previous.get('aur') is not None
            if canonical_aur != (provenance.get('aur') is not None):
                raise ValueError('pending revision cannot switch AUR authority')
            if not canonical_aur:
                validate_maintained_revision(pending[name]['policy'], item['policy'],
                                            u.load(Path(old)/'inputs'/f'{name}.json'),
                                            lock, previous, provenance)
        validate_intended(name, item['policy'], lock, provenance, work,
                          canonical_aur=canonical_aur)
    return sorted(intended)


def validate_maintained_revision(previous_policy, policy, previous_lock, lock,
                                previous_provenance, provenance):
    """Refresh registered maintained inputs without widening their authority."""
    from tools import update as u
    if previous_policy.get('authority') != 'maintained' or policy.get('authority') != 'maintained':
        raise ValueError('non-AUR refresh requires registered maintained authority')
    def semantics(value):
        result = copy.deepcopy(value)
        automatic = result.get('automatic', {})
        for key in ('version', 'pkgrel'):
            automatic.get(key, {}).pop('literal_assignment', None)
        array = automatic.get('checksum_array', {})
        array.pop('literal', None)
        array.pop('values', None)
        for checksum in automatic.get('checksums', []):
            checksum.pop('literal', None)
        return result
    if semantics(previous_policy) != semantics(policy):
        raise ValueError('maintained refresh changed enrolled policy')
    if [s['id'] for s in previous_lock['sources']] != [s['id'] for s in lock['sources']]:
        raise ValueError('maintained refresh changed source enrollment')
    for previous, current, definition in zip(previous_lock['sources'], lock['sources'], policy['sources']):
        if previous != current and not definition.get('mutable'):
            raise ValueError('maintained refresh changed fixed source')
        if previous['kind'] != current['kind'] or previous['kind'] == 'git' and previous['url'] != current['url']:
            raise ValueError('maintained refresh changed source identity')
    covered = set()
    for watcher in previous_provenance['watchers']:
        if watcher.get('kind') not in {'git', 'tag', 'release'}:
            raise ValueError('non-AUR refresh has unsupported watcher')
        identities = [watcher.get('source_id'), *watcher.get('related_source_ids', [])]
        if None in identities or covered.intersection(identities) or len(set(identities)) != len(identities):
            raise ValueError('maintained refresh has ambiguous watcher bindings')
        covered.update(identities)
    if any(source['id'] not in covered for source in lock['sources']
           if source['kind'] != 'local' and next(s for s in policy['sources'] if s['id'] == source['id']).get('mutable')):
        raise ValueError('maintained refresh has unwatched mutable source')
    u.verify_provenance(previous_provenance, provenance, lock, policy)


def validate_intended(name, policy, lock, provenance, work, *, canonical_aur=False):
    """Authenticate prospective data without evaluating a workstation recipe."""
    from tools import update as u
    if policy.get('pkgbase') != name or policy.get('native_verification') != 'pending-github-native-probe' or not isinstance(policy.get('automatic'), dict):
        raise ValueError('incomplete intended native policy')
    outputs = policy.get('outputs')
    if not isinstance(outputs, list) or not outputs or any(
            not isinstance(o, dict) or set(o) != {'name', 'arch'}
            or not NAME.fullmatch(o.get('name', '')) or o.get('arch') not in {'any', 'x86_64'}
            for o in outputs) or len({o['name'] for o in outputs}) != len(outputs):
        raise ValueError('invalid intended native outputs')
    u.validate_source_policy(lock, policy)
    for source in lock['sources']:
        if not isinstance(source['id'], str) or not source['checksums']:
            raise ValueError('incomplete intended source identity/checksums')
        u.safe_path(source['id'])
        for algorithm, checksum in source['checksums'].items():
            if checksum == 'SKIP' and source['kind'] == 'git':
                continue
            try:
                length = sources.new_hash(algorithm).digest_size * 2
            except ValueError as error:
                raise ValueError('invalid intended checksum algorithm') from error
            if not isinstance(checksum, str) or not re.fullmatch('[0-9a-fA-F]{'+str(length)+'}', checksum):
                raise ValueError('incomplete intended source byte identity')
        if source['kind'] == 'local':
            u.recipe_payload_path(source['source'])
    if provenance.get('schema') != 1 or provenance.get('pkgbase') != name or 'aur' not in provenance or not isinstance(provenance.get('watchers'), list) or not provenance['watchers']:
        raise ValueError('incomplete intended upstream provenance')
    if canonical_aur:
        aur, watchers = provenance['aur'], provenance['watchers']
        if policy.get('authority') not in {'aur', 'maintained'} or not isinstance(aur, dict) or len(watchers) != 1 or watchers[0].get('kind') != 'aur':
            raise ValueError('intention requires AUR recipe authority')
        package = aur.get('package')
        if not isinstance(package, str) or not NAME.fullmatch(package):
            raise ValueError('invalid intention AUR package')
        canonical = {('https://github.com/archlinux/aur.git', 'refs/heads/'+package),
                     ('https://aur.archlinux.org/'+package+'.git', 'refs/heads/master')}
        if (aur.get('url'), aur.get('ref')) not in canonical or watchers[0].get('baseline_commit') != aur.get('commit'):
            raise ValueError('intention requires canonical pinned AUR baseline')
        if not sources.HEX.fullmatch(aur.get('commit', '')):
            raise ValueError('invalid AUR baseline commit')
        repository = sources.clone(aur['url'], Path(work)/(name+'-membership.git'), aur['ref'])
        if sources.git('merge-base', '--is-ancestor', aur['commit'], aur['ref'], cwd=repository, check=False).returncode != 0:
            raise ValueError('AUR baseline is not in selected canonical branch history')
    identities = set()
    source_ids = {s['id'] for s in lock['sources']}
    for watcher in provenance['watchers']:
        identity = watcher.get('id')
        if not isinstance(identity, str) or not NAME.fullmatch(identity) or identity in identities or watcher.get('kind') not in {'aur', 'git', 'tag', 'release'}:
            raise ValueError('invalid intended watcher enrollment')
        identities.add(identity)
        sources.public_url(watcher['url'])
        if watcher['kind'] in {'tag', 'release'}:
            if watcher['source_id'] not in source_ids:
                raise ValueError('watcher source is not enrolled')
            tag = watcher.get('accepted_tag')
            match = re.fullmatch(watcher.get('tag_pattern', ''), tag or '')
            if not match or not match.groups():
                raise ValueError('intended watcher lacks an enrolled accepted tag')
            version = sources._version_prefix(watcher) + '.'.join(match.groups())
            asset_ids, asset_names = set(), set()
            for identity in [watcher['source_id'], *watcher.get('related_source_ids', [])]:
                source = next((s for s in lock['sources'] if s['id'] == identity), None)
                definition = next((s for s in policy['sources'] if s['id'] == identity), None)
                native_version = u.parse_version(lock['version'])
                if (source is None or definition is None
                        or sources.format_source('{upstream_version}', definition, native_version) != version
                        or source['source'] != sources.format_source(definition['source_template'], definition, native_version, source['commit'])):
                    raise ValueError('intended watcher tag differs from source policy')
                if source['kind'] == 'git' and (source['ref'] != 'refs/tags/'+tag or source['commit'] != watcher.get('accepted_peeled_commit') or (source['tag_object'] or source['commit']) != watcher.get('accepted_tag_object')):
                    raise ValueError('intended watcher tag differs from Git lock')
                if watcher['kind'] == 'release' and source['kind'] == 'release':
                    asset_name = sources.release_asset_name(watcher, version, identity)
                    if source['asset_id'] in asset_ids or asset_name in asset_names:
                        raise ValueError('release sources share an enrolled asset')
                    asset_ids.add(source['asset_id'])
                    asset_names.add(asset_name)
                if identity == watcher['source_id'] or watcher['kind'] == 'release' and source['kind'] == 'release':
                    u._live_tag_provenance(watcher, watcher, source)
        elif watcher['kind'] == 'git':
            source = next((s for s in lock['sources'] if s['id'] == watcher.get('source_id')), None)
            if source is None or source['kind'] != 'git' or watcher.get('ref') != source['ref'] or watcher['url'] != source['url']:
                raise ValueError('intended Git watcher differs from frozen source')
        elif watcher['kind'] == 'aur':
            if not provenance['aur'] or any(provenance['aur'].get(k) != watcher.get(k) for k in ('url', 'package', 'ref')):
                raise ValueError('intended AUR watcher differs from provenance')
            snapshot = sources.frozen_aur({**watcher, 'work_dir': str(Path(work)/(name+'-aur'))}, provenance['aur'], provenance['aur'])
            if canonical_aur:
                metadata = next((f['content'] for f in snapshot['files'] if f['path'] == '.SRCINFO' and f['mode'] == '100644'), None)
                if metadata is None or parse_srcinfo(metadata)['pkgbase'] != watcher['package']:
                    raise ValueError('intended AUR snapshot has wrong package identity')
                info = parse_srcinfo(metadata)
                from tools.recipe_gate import _compare
                epoch = int(lock['version'].split(':', 1)[0]) if ':' in lock['version'] else 0
                if (u.parse_version(lock['version']) != info['pkgver'] or epoch != info['epoch']
                        or _compare(info['version'], lock['version']) < 0):
                    raise ValueError('intended AUR native version differs or goes backwards')
                names = set(info['names'])
                if policy['authority'] == 'maintained':
                    names.update(v.split('=', 1)[0] for _, key, v in info['fields']
                                 if key in {'provides', 'provides_x86_64'})
                if not {o['name'] for o in outputs} <= names:
                    raise ValueError('intended AUR output identity differs')
                if policy['authority'] == 'aur':
                    if {o['name'] for o in outputs} != set(info['names']):
                        raise ValueError('stock AUR output set differs')
                    for output in outputs:
                        arches = info['scopes'][output['name']].get('arch', info['arch'])
                        if output['arch'] not in arches:
                            raise ValueError('intended AUR output architecture differs')
                native = [v for _, key, v in info['fields'] if key in {'source', 'source_x86_64'}]
                if policy['authority'] == 'aur':
                    from collections import Counter
                    if Counter(native) != Counter(source['source'] for source in lock['sources']):
                        raise ValueError('stock AUR source enrollment is incomplete or duplicated')
                for source in lock['sources']:
                    definition = next(s for s in policy['sources'] if s['id'] == source['id'])
                    algorithm = definition['checksum_algorithm']
                    checksum_index = definition['checksum_index']
                    if type(checksum_index) is not int or checksum_index < 0 or algorithm not in source['checksums']:
                        raise ValueError('invalid intended checksum enrollment')
                    if policy['authority'] == 'aur':
                        index = definition.get('native_source_index')
                        if (index is not None and (type(index) is not int or index < 0
                                                  or index >= len(native) or native[index] != source['source'])
                                or index is None and native.count(source['source']) != 1):
                            raise ValueError('intended AUR source declaration differs')
                        sums = [v for _, key, v in info['fields'] if key in {algorithm+'sums', algorithm+'sums_x86_64'}]
                        if checksum_index >= len(sums) or source['checksums'][algorithm] != sums[checksum_index]:
                            raise ValueError('intended AUR source checksum differs')
                    if source['kind'] == 'local':
                        payload = next((f for f in snapshot['files'] if f['path'] == source['source'] and f['mode'] in {'100644', '100755'}), None)
                        if payload is None or sources.new_hash(algorithm, payload['content'].encode()).hexdigest() != source['checksums'][algorithm]:
                            raise ValueError('intended AUR local source bytes differ')
    for source in lock['sources']:
        if source['kind'] == 'git':
            sources.materialize_sources({'schema': 1, 'version': lock['version'], 'sources': [source]}, Path(work)/(name+'-'+source['id']))
        elif source['kind'] != 'local':
            sources.freeze_source(source)
