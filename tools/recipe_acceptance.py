"""Exact recipe merge acceptance and protected-main bookkeeping."""
import base64
import hashlib
import json
import re
import tempfile
from pathlib import Path

from tools import recipe_state, sources

SHA = re.compile(r'^[0-9a-f]{40}$')



def scratch_root():
    root = Path.home()/'.local/state/omp/work'
    root.mkdir(parents=True, exist_ok=True)
    return root


def digest(value):
    return hashlib.sha256(sources.canonical(value)).hexdigest()


def verify_merge(record, commit):
    accepted = commit.get('sha', '')
    if not SHA.fullmatch(accepted) or commit.get('tree', {}).get('sha') != record['recipe_tree']:
        raise ValueError('accepted recipe tree differs from reviewed head')
    if [p['sha'] for p in commit.get('parents', [])] != [record['recipe_base'], record['head']]:
        raise ValueError('recipe acceptance requires exact B,H merge parents')
    return accepted


def verify_native_evidence(candidate, evidence):
    for field in ('schema', 'repository', 'base', 'head', 'recipe_pins',
                  'previous_recipe_pins', 'image'):
        if evidence.get(field) != candidate[field]:
            raise ValueError('durable native evidence identity mismatch: ' + field)
    packages = {package['pkgbase']: package for package in candidate['packages']}
    outputs = evidence.get('packages', [])
    if len(outputs) != len(packages) or {output['pkgbase'] for output in outputs} != set(packages):
        raise ValueError('durable native evidence incomplete or duplicated')
    from tools.recipe_gate import metadata_equivalent, parse_srcinfo
    for output in outputs:
        package = packages[output['pkgbase']]
        if (output.get('recipe_commit') != package['recipe_commit']
                or output.get('request_input_digest') != package['input_digest']
                or not metadata_equivalent(package['expected_srcinfo'], output.get('metadata', {}).get('srcinfo', ''), dynamic=output.get('metadata', {}).get('dynamic_pkgver') is True)):
            raise ValueError('durable native evidence differs from exact frozen candidate')
        actual_lock = output['source_lock']
        if actual_lock['version'] != parse_srcinfo(output['metadata']['srcinfo'])['version']:
            raise ValueError('actual source version differs from native metadata')
        previous = candidate.get('predecessor_lock')
        if previous is not None and not candidate.get('import_predecessor') and candidate.get('proposal_origin') != 'legacy-accepted':
            from tools.recipe_gate import _compare
            if _compare(previous['version'], actual_lock['version']) <= 0:
                raise ValueError('actual package version must advance accepted version')
        if len(actual_lock['sources']) != len(package['lock']['sources']):
            raise ValueError('actual source enrollment differs')
        for planned, actual in zip(package['lock']['sources'], actual_lock['sources']):
            mutable = {'commit', 'tag_object', 'peeled_commit', 'git_context'} if planned['kind'] == 'git' else set()
            if {k: v for k, v in planned.items() if k not in mutable} != {k: v for k, v in actual.items() if k not in mutable}:
                raise ValueError('actual source declarations differ')
            if actual['kind'] == 'git' and not sources.HEX.fullmatch(actual.get('commit') or ''):
                raise ValueError('actual Git source lacks full commit')
        files = output.get('files', [])
        wanted = {(item['name'], item['arch']) for item in package['policy']['outputs']}
        if (len(files) != len(wanted) or {(item['name'], item['arch']) for item in files} != wanted
                or any(item['version'] != actual_lock['version'] for item in files)):
            raise ValueError('durable native evidence outputs differ from enrolled package')


def paginate(fetch):
    page = 1
    while True:
        rows = fetch(page)
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('invalid paginated pull response')
        yield from rows
        if len(rows) < 100:
            return
        page += 1


def blob_sha(value):
    content = sources.canonical(value)
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def expected_leaves(previous, receipt):
    result = dict(previous)
    name = receipt['pkgbase']
    result['recipes/' + name] = ('160000', 'commit', receipt['accepted'])
    for path, value in [('inputs/' + name + '.json', receipt['lock']),
                        ('upstream/' + name + '.json', receipt['provenance']),
                        ('acceptance/' + name + '.json', receipt)]:
        result[path] = ('100644', 'blob', blob_sha(value))
    if receipt.get('activated_registry') is not None:
        result['packages.json'] = ('100644', 'blob', blob_sha(receipt['activated_registry']))
    return result


def leaves(sha):
    from tools import update as u
    commit = u.api(u.route('/git/commits/' + sha))
    tree = u.api(u.route('/git/trees/' + commit['tree']['sha'] + '?recursive=1'))
    if tree.get('truncated'):
        raise ValueError('truncated bookkeeping tree')
    return {row['path']: (row['mode'], row['type'], row['sha'])
            for row in tree['tree'] if row['type'] != 'tree'}


def required_checks(head):
    from tools import update as u
    # GitHub returns newest first. Preserve the latest result for each context.
    states = {}
    for row in paginate(lambda page: u.api(u.route('/commits/' + head + '/statuses?per_page=100&page=' + str(page)))):
        states.setdefault(row['context'], row['state'])
    if any(states.get(context) != 'success' for context in ('verify', 'candidate-build', 'recipe-policy')):
        raise ValueError('required exact-head protected checks missing')


def content_compatible(candidate, policy):
    """Compare current policy; exact recipe/source identity is verified separately."""
    package = next(p for p in candidate['packages'] if p['pkgbase'] == candidate['pkgbase'])
    return policy == package['policy']


def acceptance_candidate(pr, C, proposal, policy):
    """Read the exact pending request, never search historical candidates."""
    from tools import recipe_candidates
    name = pr['base']['ref'][4:]
    snapshot = recipe_state.StateSnapshot()
    pending = recipe_state.pending_build(name, snapshot=snapshot)
    if pending is None or pending['head'] != pr['head']['sha'] or pending['state'] != 'success':
        raise ValueError('accepted recipe lacks successful exact-head request')
    candidate = recipe_state.build_request(pending['request_id'], snapshot=snapshot)
    descriptor = snapshot.load('build-result', pending['request_id'])
    if (candidate['pr_number'] != pr['number'] or candidate['recipe_base'] != pr['base']['sha']
            or candidate['pkgbase'] != name or candidate['head_branch'] != pr['head']['ref']
            or not content_compatible(candidate, policy)):
        raise ValueError('accepted recipe request identity or policy differs')
    recipe_state.assert_recipe_identity(candidate, merged=True, control=C)
    recipe_candidates.verify_result(candidate, descriptor)
    built = descriptor['evidence']
    verify_native_evidence(candidate, built)
    producer = descriptor['producer']
    native_runs = {'run_id': str(built['run_id']), 'run_attempt': str(built['run_attempt']),
                   'package_runs': [{'pkgbase': name, 'run_id': str(producer['run_id']),
                                     'run_attempt': str(producer['run_attempt'])}]}
    return candidate, built, native_runs, descriptor


def accepted_receipt(number, control=None):
    from tools import update as u
    pr = u.api(u.route('/pulls/' + str(number)))
    C = control or u.main_sha()
    B, H = pr['base']['sha'], pr['head']['sha']
    name = pr['base']['ref'][4:]
    from tools import recipe_candidates
    proposal = recipe_candidates.proposal_receipt(H, B, name)
    if proposal is None:
        raise ValueError('accepted recipe lacks its directly recorded frozen proposal receipt')
    with tempfile.TemporaryDirectory(prefix='recipe-accept-', dir=scratch_root()) as directory:
        work = Path(directory)
        pins = {}
        old = u.checkout_data(C, work/'control', pins)
        from tools import imports
        policies = imports.policies(old)
        pending = imports.registry(old)[2].get(name)
        if pending is not None:
            imports.verify_predecessor(old, name, pins, work/'import-origin')
        if name not in policies:
            raise ValueError('claimed bookkeeping package is not currently enrolled')
        policy = policies[name]
        adoption = None
        if proposal.get('proposal_origin') == 'legacy-accepted':
            adoption = verify_legacy_adoption(proposal, C, old, pins)
        if pins.get(name) != (adoption['expected_previous_control_pin'] if adoption else B):
            raise ValueError('accepted predecessor pin changed; reconcile history in order')
        candidate, built, native_runs, descriptor = acceptance_candidate(
            pr, C, proposal, policy)
        package = next(p for p in candidate['packages'] if p['pkgbase'] == name)
        if candidate.get('import_predecessor') != pending:
            raise ValueError('accepted import predecessor differs from authentic registration')
        activated = imports.activate(imports.registry(old)[0], pending) if pending is not None else None
        if adoption and (candidate.get('proposal_origin') != 'legacy-accepted'
                or candidate.get('previous_control_pin') != adoption['expected_previous_control_pin']
                or candidate.get('baseline_lock') != adoption['baseline_lock']
                or candidate.get('baseline_provenance') != adoption['baseline_provenance']
                or package['lock'] != proposal['lock'] or candidate['provenance'] != proposal['provenance']):
            raise ValueError('accepted native backfill differs from frozen legacy adoption')
        if not adoption and candidate.get('proposal_origin') == 'legacy-accepted':
            raise ValueError('accepted native backfill lacks frozen legacy adoption')
        required_checks(H)
        A = verify_merge(candidate, u.api(u.route('/git/commits/' + pr['merge_commit_sha'])))
        from tools import recipes
        tip = u.ref_head('pkg/' + name)
        if not tip or tip != A and not recipes.is_ancestor(u.repository(), A, tip):
            raise ValueError('accepted recipe no longer belongs to protected branch history')
        if u.load(old/'inputs'/f'{name}.json') != candidate['predecessor_lock'] or u.load(old/'upstream'/f'{name}.json') != candidate['predecessor_provenance']:
            raise ValueError('accepted predecessor source state changed; reconcile original history')
        lock = built['packages'][0]['source_lock']
        if u.main_sha() != C:
            raise ValueError('main advanced during acceptance; revalidate')
    result = {'schema': 1, 'kind': 'recipe-acceptance', 'repository': u.repository(),
            'base': C, 'candidate_base': candidate['base'], 'proposal_base': proposal['base'], 'recipe_base': B, 'head': H, 'recipe_tree': candidate['recipe_tree'],
            'accepted': A, 'pkgbase': name, 'watcher_id': candidate.get('watcher_id'),
            'watcher_ids': candidate['watcher_ids'],
            'head_branch': candidate['head_branch'], 'proposal_origin': candidate['proposal_origin'],
            'previous_control_pin': adoption['expected_previous_control_pin'] if adoption else B,
            'pr_number': int(number), 'receipt_id': candidate['receipt_id'],
            'candidate_digest': recipe_candidates.candidate_digest(candidate), 'built_digest': digest(built), 'native_runs': native_runs,
            'build': descriptor,
            'predecessor_lock': candidate['predecessor_lock'],
            'predecessor_provenance': candidate['predecessor_provenance'],
            'policy': policy, 'lock': lock, 'actual_srcinfo': built['packages'][0]['metadata']['srcinfo'], 'provenance': candidate['provenance']}
    if pending is not None:
        result['import_predecessor'] = pending
        result['activated_registry'] = activated
    return result


def verify_accepted_build(record, package, accepted_main):
    """Authenticate accepted correspondence without changing producer evidence."""
    from tools import build_store, recipe_candidates, update as u
    if not SHA.fullmatch(accepted_main or ''):
        raise ValueError('accepted build requires exact main commit')
    name = package['pkgbase']
    manifest = leaves(accepted_main)
    receipt_leaf = manifest.get('acceptance/' + name + '.json')
    if not receipt_leaf or receipt_leaf[:2] != ('100644', 'blob'):
        raise ValueError('accepted main lacks durable recipe acceptance')
    blob = u.api(u.route('/git/blobs/' + receipt_leaf[2]))
    if blob.get('encoding') != 'base64':
        raise ValueError('accepted receipt blob encoding differs')
    receipt = json.loads(base64.b64decode(blob['content'], validate=False))
    descriptor = receipt.get('build', {})
    original = descriptor.get('record')
    if not original or record is not None and record != original:
        raise ValueError('accepted build original producer record differs')
    if (receipt.get('kind') != 'recipe-acceptance'
            or receipt.get('repository') != u.repository()
            or receipt.get('pkgbase') != name
            or receipt.get('candidate_digest') != recipe_candidates.candidate_digest(original)
            or any(receipt.get(field) != original.get(field)
                   for field in ('recipe_base', 'head', 'recipe_tree', 'pr_number'))
            or receipt.get('candidate_base') != original.get('base')):
        raise ValueError('accepted receipt original candidate identity differs')
    A = receipt.get('accepted')
    if (package.get('recipe_commit') != A
            or manifest.get('recipes/' + name) != ('160000', 'commit', A)):
        raise ValueError('accepted main recipe pin differs from exact accepted merge')
    if verify_merge(original, u.api(u.route('/git/commits/' + A))) != A:
        raise ValueError('accepted merge commit identity differs')
    matches = [p for p in original['packages'] if p['pkgbase'] == name]
    if len(matches) != 1:
        raise ValueError('accepted original build package mapping differs')
    frozen = matches[0]
    actual = descriptor['evidence']['packages'][0]
    verify_native_evidence(original, descriptor['evidence'])
    if (package.get('lock') != actual['source_lock']
            or package.get('policy') != frozen['policy']
            or receipt.get('lock') != actual['source_lock']
            or receipt.get('policy') != frozen['policy']
            or receipt.get('provenance') != original['provenance']):
        raise ValueError('accepted build source or policy inputs differ')
    for path, value in [('inputs/' + name + '.json', receipt['lock']),
                        ('upstream/' + name + '.json', receipt['provenance'])]:
        if manifest.get(path) != ('100644', 'blob', blob_sha(value)):
            raise ValueError('accepted main source state differs from receipt')
    recipe_candidates.verify_result(original, descriptor)
    verified = build_store.lookup(package, record=original)
    if verified is None or verified != descriptor:
        raise ValueError('accepted original durable descriptor differs or is missing')
    return {'record': original, 'acceptance': receipt,
            'mapping': {'head': original['head'], 'accepted': A,
                        'recipe_tree': original['recipe_tree']}}


def validate_bookkeeping(pr, C, H, old, new):
    from tools import update as u
    before, after = leaves(C), leaves(H)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    claims = sorted(path for path in changed if path.startswith('acceptance/'))
    commit = u.api(u.route('/git/commits/' + H))
    marker = 'Arch-Recipe-Acceptance:' in commit.get('message', '')
    claimed_branch = pr.get('head', {}).get('ref', '').startswith('bookkeeping/')
    if not marker and not claimed_branch:
        return None
    if len(claims) != 1 or not claims[0].endswith('.json'):
        raise ValueError('malformed claimed recipe bookkeeping')
    supplied = u.load(Path(new)/claims[0])
    if claims[0] != 'acceptance/' + supplied['pkgbase'] + '.json' or supplied['base'] != C:
        raise ValueError('bookkeeping acceptance identity mismatch')
    verified = accepted_receipt(supplied['pr_number'], C)
    if supplied != verified or after != expected_leaves(before, verified):
        raise ValueError('bookkeeping is not exact accepted recipe reconstruction')
    if [p['sha'] for p in commit['parents']] != [C] or pr['base']['ref'] != 'main':
        raise ValueError('bookkeeping is not based on exact protected main')
    if u.main_sha() != C:
        raise ValueError('bookkeeping main advanced; revalidate')
    return verified


def write_bookkeeping(receipt):
    from tools import update as u
    C, A, name = receipt['base'], receipt['accepted'], receipt['pkgbase']
    if u.main_sha() != C:
        raise ValueError('main advanced before bookkeeping; revalidate')
    recipe_state.save('acceptance', C + '-' + A, receipt)
    branch = 'bookkeeping/' + name + '/' + A + '/' + C
    message = 'Accept ' + name + ' recipe\n\nArch-Recipe-Acceptance: ' + A
    existing = u.ref_head(branch)
    if existing:
        commit = u.api(u.route('/git/commits/' + existing))
        if (leaves(existing) != expected_leaves(leaves(C), receipt)
                or [p['sha'] for p in commit['parents']] != [C] or commit['message'] != message):
            raise ValueError('existing bookkeeping diverged; refusing overwrite')
    else:
        changes = [{'path': 'recipes/' + name, 'mode': '160000', 'type': 'commit', 'sha': A}]
        data = [('inputs/' + name + '.json', receipt['lock']),
                ('upstream/' + name + '.json', receipt['provenance']),
                ('acceptance/' + name + '.json', receipt)]
        if receipt.get('activated_registry') is not None:
            data.append(('packages.json', receipt['activated_registry']))
        for path, value in data:
            blob = u.api(u.route('/git/blobs'), 'POST', {'content': base64.b64encode(sources.canonical(value)).decode(), 'encoding': 'base64'})
            changes.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        base = u.api(u.route('/git/commits/' + C))
        tree = u.api(u.route('/git/trees'), 'POST', {'base_tree': base['tree']['sha'], 'tree': changes})
        commit = u.api(u.route('/git/commits'), 'POST', {'tree': tree['sha'], 'parents': [C], 'message': message})
        existing = commit['sha']
        if u.main_sha() != C:
            raise ValueError('main advanced before bookkeeping ref creation')
        u.api(u.route('/git/refs'), 'POST', {'ref': 'refs/heads/' + branch, 'sha': existing})
    owner = u.repository().split('/')[0]
    prs = list(paginate(lambda page: u.api(u.route('/pulls?state=open&base=main&head=' + owner + ':' + branch + '&per_page=100&page=' + str(page)))))
    if len(prs) > 1:
        raise ValueError('multiple bookkeeping pull requests')
    pr = prs[0] if prs else u.api(u.route('/pulls'), 'POST', {'head': branch, 'base': 'main', 'title': 'Accept ' + name + ' recipe', 'body': 'Exact accepted recipe bookkeeping for `' + A + '`.'})
    u.dispatch(pr['number'])
    return pr


def verify_bookkeeping_shape(pr, commit, supplied, before, after):
    parents = [parent['sha'] for parent in commit.get('parents', [])]
    if len(parents) != 1:
        raise ValueError('claimed bookkeeping has divergent control parents')
    P, name, A = parents[0], supplied['pkgbase'], supplied['accepted']
    if (not SHA.fullmatch(P) or not SHA.fullmatch(A)
            or not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*', name)
            or supplied['base'] != P or pr['base']['ref'] != 'main'
            or pr['head']['ref'] != 'bookkeeping/' + name + '/' + A + '/' + P
            or commit.get('message') != 'Accept ' + name + ' recipe\n\nArch-Recipe-Acceptance: ' + A
            or after != expected_leaves(before, supplied)):
        raise ValueError('claimed stale bookkeeping is not exact generated work')
    return P


def reconcile_bookkeeping(pr, C, enrolled):
    from tools import update as u
    H = pr['head']['sha']
    u.pr_identity(pr['number'], C, H)
    commit = u.api(u.route('/git/commits/' + H))
    parents = commit.get('parents', [])
    if len(parents) != 1:
        raise ValueError('claimed bookkeeping requires one immutable parent')
    P = parents[0]['sha']
    before, after = leaves(P), leaves(H)
    claims = [path for path in before.keys() | after.keys()
              if path.startswith('acceptance/') and before.get(path) != after.get(path)]
    if len(claims) != 1:
        raise ValueError('claimed bookkeeping requires one exact acceptance record')
    with tempfile.TemporaryDirectory(prefix='bookkeeping-refresh-', dir=scratch_root()) as directory:
        new = u.checkout_data(H, Path(directory)/'claimed')
        supplied = u.load(new/claims[0])
    verify_bookkeeping_shape(pr, commit, supplied, before, after)
    if claims[0] != 'acceptance/' + supplied['pkgbase'] + '.json' or supplied['pkgbase'] not in enrolled:
        raise ValueError('claimed bookkeeping package is not currently enrolled')
    receipt = accepted_receipt(supplied['pr_number'], C)
    if receipt['accepted'] != supplied['accepted'] or receipt['pkgbase'] != supplied['pkgbase']:
        raise ValueError('stale bookkeeping source acceptance differs')
    if P == C:
        if supplied != receipt:
            raise ValueError('current bookkeeping receipt is not independently verified')
        dispatched = u.dispatch(pr['number'])
        return {'pr_number': pr['number'], 'dispatched': dispatched}
    replacement = write_bookkeeping(receipt)
    replacement_head = replacement['head']['sha']
    u.pr_identity(replacement['number'], C, replacement_head)
    if (replacement['number'] == pr['number']
            or leaves(replacement_head) != expected_leaves(leaves(C), receipt)):
        raise ValueError('bookkeeping replacement identity or reconstruction differs')
    u.pr_identity(pr['number'], C, H)
    if u.main_sha() != C:
        raise ValueError('main advanced before stale bookkeeping retirement')
    u.api(u.route('/pulls/' + str(pr['number'])), 'PATCH', {'state': 'closed'})
    return {'pr_number': pr['number'], 'replacement': replacement['number'], 'closed': True}


def reconcile(number=None):
    from tools import update as u
    C = u.main_sha()
    if u.control_checkout()[0] != C:
        raise ValueError('reconciliation requires current trusted main')
    from tools.imports import policies
    enrolled = set(policies(u.ROOT))
    results = []
    snapshot = recipe_state.StateSnapshot()
    known = snapshot.load_many([('pending', name) for name in enrolled])
    requests = snapshot.load_many([('build-request', value['request_id']) for value in known.values() if value])
    open_pulls = u.open_pr_snapshot()
    by_number = {pr['number']: pr for pr in open_pulls}
    numbers = {int(request['pr_number']) for request in requests.values() if request}
    if number is not None:
        numbers = {int(number)}
        pulls = [u.api(u.route('/pulls/' + str(number)))]
    else:
        pulls = open_pulls + [u.api(u.route('/pulls/' + str(n))) for n in sorted(numbers - by_number.keys())]
    for pr in sorted(pulls, key=lambda item: (item.get('merged_at') or '9999', item['number'])):
        target = pr['base']['ref']
        if target == 'main':
            if pr['state'] == 'open':
                if pr['head']['ref'].startswith('bookkeeping/'):
                    parent = u.api(u.route('/git/commits/' + pr['head']['sha']))['parents']
                    if len(parent) == 1 and parent[0]['sha'] != C:
                        results.append(reconcile_bookkeeping(pr, C, enrolled))
                        continue
                u.dispatch(pr['number'], pr=pr)
            continue
        if not target.startswith('pkg/') or target[4:] not in enrolled:
            continue
        name = target[4:]
        active = known.get(('pending', name))
        request = requests.get(('build-request', active['request_id'])) if active else None
        if active and request and int(request['pr_number']) == pr['number'] and active['state'] == 'queued' and active.get('worker') is None and (pr['state'] == 'closed' or active['head'] != pr['head']['sha']):
            active = recipe_state.abandon_build(name, active['request_id'])
            known[('pending', name)] = active
        if active and request and int(request['pr_number']) == pr['number'] and active['state'] in {'queued', 'running'}:
            worker = active.get('worker')
            if worker:
                from tools import build_store
                run = build_store.producer_run(request, worker, completed=False)
                if run.get('status') == 'completed':
                    result = recipe_state.StateSnapshot().load_optional('build-result', active['request_id'])
                    outcome = 'success' if run.get('conclusion') == 'success' and result is not None else 'failure'
                    active = recipe_state.finish_build(name, active['request_id'], outcome, result=result if outcome == 'success' else None)
                    known[('pending', name)] = active
        if pr['state'] == 'open':
            if active and active['head'] == pr['head']['sha'] and active['state'] == 'success' and pr.get('statuses', {}).get('candidate-build') in {None, 'pending'}:
                # A lost coordinator is reconciled from metadata, never another worker.
                request = requests.get(('build-request', active['request_id'])) or recipe_state.build_request(active['request_id'], snapshot=recipe_state.StateSnapshot())
                descriptor = recipe_state.StateSnapshot().load('build-result', active['request_id'])
                with tempfile.TemporaryDirectory(prefix='recipe-finalize-', dir=scratch_root()) as directory:
                    work = Path(directory)
                    u.dump(work/'candidate.json', request)
                    u.dump(work/'build-results.json', {name: descriptor})
                    u.dump(work/'native-evidence.json', descriptor['evidence'])
                    from tools.recipe_candidates import finalize
                    results.append(finalize(pr['number'], request['base'], request['head'], work/'candidate.json', work))
            else:
                u.dispatch(pr['number'], pr=pr, pending=active)
            continue
        if not pr.get('merged_at'):
            results.append({'pr_number': pr['number'], 'closed': True})
            continue
        A = pr['merge_commit_sha']
        pin = u.pin_at(C, name)
        from tools import recipes
        if pin == A or recipes.is_ancestor(u.repository(), A, pin):
            continue
        results.append(write_bookkeeping(accepted_receipt(pr['number'], C)))
    return results


def verify_legacy_receipt(commit, base, name, watcher, statuses):
    """Migration-only proof of the retired control-tree proposal protocol."""
    receipt = digest({'base': base, 'tree': commit['tree']['sha'],
                      'watcher': watcher, 'pkgbase': name})
    message = f'Update {name} via {watcher}\n\nArch-Update-Receipt: {receipt}\nArch-Update-Base: {base}'
    if [p['sha'] for p in commit.get('parents', [])] != [base] or commit.get('message') != message:
        raise ValueError('legacy proposal immutable receipt mismatch')
    latest = next((row for row in statuses if row.get('context') == 'arch-updater-receipt'), None)
    if (not latest or latest.get('state') != 'success' or latest.get('description') != receipt
            or latest.get('creator', {}).get('login') != 'github-actions[bot]'
            or latest.get('creator', {}).get('type') != 'Bot'):
        raise ValueError('legacy receipt lacks exact controller bot ownership')


def verify_legacy_adoption(proposal, C, current, pins, require_attestation=True):
    """Prove explicit backfill of an already accepted legacy control merge."""
    from tools import update as u
    from tools import recipes
    name = proposal['pkgbase']
    L, Q, M = (proposal[field] for field in ('legacy_base', 'legacy_head', 'legacy_accepted'))
    B, H = proposal['recipe_base'], proposal['recipe_head']
    if (proposal.get('proposal_origin') != 'legacy-accepted'
            or proposal.get('repository') != u.repository()
            or proposal.get('expected_previous_control_pin') != H
            or any(not SHA.fullmatch(sha) for sha in (C, L, Q, M, B, H))):
        raise ValueError('invalid accepted legacy proposal identity')
    if require_attestation:
        recipe_state.require_attestation('proposal', H, proposal, H)
    pr = u.api(u.route('/pulls/' + str(proposal['legacy_pr_number'])))
    if (pr.get('state') != 'closed' or not pr.get('merged_at')
            or pr.get('merge_commit_sha') != M or pr['head']['sha'] != Q
            or pr['base']['ref'] != 'main'
            or pr['base']['repo']['full_name'] != u.repository()
            or pr['head']['repo']['full_name'] != u.repository()):
        raise ValueError('accepted legacy pull request identity changed')
    origin = u.api(u.route('/git/commits/' + Q))
    statuses = paginate(lambda page: u.api(u.route('/commits/' + Q + '/statuses?per_page=100&page=' + str(page))))
    verify_legacy_receipt(origin, L, name, proposal['watcher_id'], statuses)
    merged = u.api(u.route('/git/commits/' + M))
    if ([row['sha'] for row in merged.get('parents', [])] != [L, Q]
            or merged.get('tree') != origin.get('tree')
            or not recipes.is_ancestor(u.repository(), M, C)):
        raise ValueError('accepted legacy merge is not the exact supported control merge')
    recipe = u.api(u.route('/git/commits/' + H))
    if (not recipe.get('parents') or recipe['parents'][0]['sha'] != B
            or recipe.get('tree', {}).get('sha') != proposal['recipe_tree']):
        raise ValueError('accepted legacy recipe tuple differs')
    with tempfile.TemporaryDirectory(prefix='legacy-adoption-', dir=scratch_root()) as directory:
        baseline = u.checkout_data(L, Path(directory)/'baseline', selected={name})
        baseline_lock = u.load(baseline/'inputs'/f'{name}.json')
        baseline_provenance = u.load(baseline/'upstream'/f'{name}.json')
        if (proposal.get('baseline_lock') != baseline_lock
                or proposal.get('baseline_provenance') != baseline_provenance
                or u.policy_at(current).get(name) != u.policy_at(baseline).get(name)
                or name not in u.policy_at(baseline)):
            raise ValueError('accepted legacy baseline or policy differs')
    expected = dict(leaves(L))
    if expected.get('recipes/' + name) != ('160000', 'commit', B):
        raise ValueError('accepted legacy baseline recipe pin differs')
    expected['recipes/' + name] = ('160000', 'commit', H)
    for folder, value in [('inputs', proposal['lock']), ('upstream', proposal['provenance'])]:
        expected[folder + '/' + name + '.json'] = ('100644', 'blob', blob_sha(value))
    if leaves(Q) != expected or leaves(M) != expected:
        raise ValueError('accepted legacy merge or proposal has unrelated changes')
    if (pins.get(name) != H
            or u.load(current/'inputs'/f'{name}.json') != proposal['lock']
            or u.load(current/'upstream'/f'{name}.json') != proposal['provenance']):
        raise ValueError('current accepted legacy pin or source state diverged')
    return {'baseline_lock': baseline_lock, 'baseline_provenance': baseline_provenance,
            'predecessor_lock': proposal['lock'], 'predecessor_provenance': proposal['provenance'],
            'expected_previous_control_pin': H}


def legacy_identity(number, control=None, head=None):
    """A stale PR base snapshot is not the current trusted control revision."""
    from tools import update as u
    if not str(number).isdecimal() or int(number) < 1:
        raise ValueError('invalid legacy PR number')
    pr = u.api(u.route('/pulls/' + str(number)))
    C, H = u.main_sha(), pr['head']['sha']
    if (not (pr['state'] == 'open' or pr['state'] == 'closed' and pr.get('merged_at'))
            or pr['base']['ref'] != 'main'
            or pr['base']['repo']['full_name'] != u.repository()
            or pr['head']['repo']['full_name'] != u.repository()
            or not SHA.fullmatch(C) or not SHA.fullmatch(H)
            or control is not None and C != control or head is not None and H != head):
        raise ValueError('legacy proposal identity changed or is not an open/merged same-repository main proposal')
    return pr, C, H


def migrate_existing(number=3):
    from tools import update as u
    pr, C, H = legacy_identity(number)
    legacy = u.api(u.route('/git/commits/' + H))
    if len(legacy.get('parents', [])) != 1:
        raise ValueError('legacy proposal requires one immutable control parent')
    L = legacy['parents'][0]['sha']
    if not SHA.fullmatch(L):
        raise ValueError('invalid legacy control parent')
    from tools import recipes
    if L != C and not recipes.is_ancestor(u.repository(), L, C):
        raise ValueError('legacy control parent is not accepted main ancestry')
    with tempfile.TemporaryDirectory(prefix='recipe-migrate-', dir=scratch_root()) as directory:
        work = Path(directory)
        oldpins, newpins, currentpins = {}, {}, {}
        old = u.checkout_data(L, work/'old', oldpins)
        current = u.checkout_data(C, work/'current', currentpins)
        new = u.checkout_data(H, work/'legacy', newpins)
        changed = [name for name in oldpins if oldpins[name] != newpins.get(name)]
        if len(changed) != 1 or set(oldpins) != set(newpins):
            raise ValueError('legacy migration requires one enrolled recipe transition')
        name = changed[0]
        B, recipe_head = oldpins[name], newpins[name]
        branch = 'pkg/' + name
        recipe_state.protected_branch(name)
        if u.ref_head(branch) != B:
            raise ValueError('migration requires protected exact predecessor recipe branch')
        proposals = list(paginate(lambda page: u.api(u.route('/pulls?state=open&base=pkg%2F' + name + '&per_page=100&page=' + str(page)))))
        if proposals and (len(proposals) != 1 or proposals[0]['head']['sha'] != recipe_head):
            raise ValueError('recipe branch already has a divergent open proposal')
        merged_legacy = bool(pr.get('merged_at'))
        comparison = new if merged_legacy else old
        policy = u.policy_at(current).get(name)
        if (currentpins.get(name) != (recipe_head if merged_legacy else B) or policy != u.policy_at(old)[name]
                or u.load(current/'inputs'/f'{name}.json') != u.load(comparison/'inputs'/f'{name}.json')
                or u.load(current/'upstream'/f'{name}.json') != u.load(comparison/'upstream'/f'{name}.json')):
            raise ValueError('legacy predecessor pin, policy or source state diverged from current main')
        lock = u.load(new/'inputs'/f'{name}.json')
        provenance = u.load(new/'upstream'/f'{name}.json')
        oldpro = u.load(old/'upstream'/f'{name}.json')
        prefix = 'Update ' + name + ' via '
        title = legacy.get('message', '').split('\n', 1)[0]
        watcher = title[len(prefix):] if title.startswith(prefix) else ''
        if watcher not in {row['id'] for row in oldpro['watchers']}:
            raise ValueError('legacy migration receipt watcher is not enrolled')
        u.validate_source_policy(lock, policy)
        u.verify_provenance(oldpro, provenance, lock, policy, watcher)
        expected = dict(leaves(L))
        expected['recipes/' + name] = ('160000', 'commit', recipe_head)
        for folder, value in [('inputs', lock), ('upstream', provenance)]:
            expected[folder + '/' + name + '.json'] = ('100644', 'blob', blob_sha(value))
        if leaves(H) != expected:
            raise ValueError('legacy proposal has unrelated or divergent changes')
        statuses = paginate(lambda page: u.api(u.route('/commits/' + H + '/statuses?per_page=100&page=' + str(page))))
        verify_legacy_receipt(legacy, L, name, watcher, statuses)
        recipe = u.api(u.route('/git/commits/' + recipe_head))
        if not recipe['parents'] or recipe['parents'][0]['sha'] != B:
            raise ValueError('legacy recipe predecessor diverged')
        proposal_branch = 'recipe-updates/' + name + '/' + watcher
        receipt = {'schema': 1, 'repository': u.repository(), 'base': C, 'pkgbase': name,
                   'head_branch': proposal_branch, 'proposal_origin': 'watcher', 'watcher_ids': [watcher],
                   'watcher_id': watcher, 'recipe_commit': B, 'lock': lock, 'provenance': provenance,
                   'recipe_base': B, 'recipe_head': recipe_head, 'recipe_tree': recipe['tree']['sha'],
                   'legacy_pr_number': int(number), 'legacy_head': H, 'legacy_base': L}
        if merged_legacy:
            receipt.update(proposal_origin='legacy-accepted', legacy_accepted=pr['merge_commit_sha'],
                           expected_previous_control_pin=recipe_head,
                           baseline_lock=u.load(old/'inputs'/f'{name}.json'),
                           baseline_provenance=u.load(old/'upstream'/f'{name}.json'))
            verify_legacy_adoption(receipt, C, current, currentpins, require_attestation=False)
        receipt['receipt_id'] = digest(receipt)
        # Original proposal remains immutable and supplies no approval authority.
        original = recipe_state.load_optional('proposal', recipe_head)
        if original:
            fields = ('pkgbase', 'watcher_id', 'watcher_ids', 'head_branch', 'proposal_origin', 'recipe_base', 'recipe_head', 'recipe_tree', 'lock', 'provenance')
            if merged_legacy:
                fields += ('legacy_pr_number', 'legacy_head', 'legacy_base', 'legacy_accepted',
                           'expected_previous_control_pin', 'baseline_lock', 'baseline_provenance')
            for field in fields:
                if original.get(field) != receipt[field]:
                    raise ValueError('legacy original receipt diverged')
        else:
            recipe_state.attest('proposal', recipe_head, receipt, recipe_head)
            recipe_state.save('proposal', recipe_head, receipt)
        recipe_state.require_attestation('proposal', recipe_head, original or receipt, recipe_head)
        refreshed, _, _ = legacy_identity(number, C, H)
        if bool(refreshed.get('merged_at')) != merged_legacy or refreshed.get('merge_commit_sha') != pr.get('merge_commit_sha'):
            raise ValueError('legacy merge state changed during migration')
        if u.ref_head(proposal_branch) != recipe_head or u.main_sha() != C or u.ref_head(branch) != B:
            raise ValueError('migration proposal or predecessor moved')
        replacement = proposals[0] if proposals else u.api(u.route('/pulls'), 'POST', {'head': proposal_branch, 'base': branch,
                            'title': pr['title'], 'body': 'Native recipe replacement for #' + str(number) + '. Fresh exact build required; recipe approval uses human Merge.'})
        recipe_state.recipe_identity(replacement['number'], C, recipe_head)
        u.dispatch(replacement['number'])
        # Closing the legacy PR is intentionally not performed by migration.
        return replacement
