"""Trusted-main validation of native, recipe-only pull requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

from tools import recipe_gate, recipe_state, recipes
from tools.recipe_gate import classify_recipe_update, harness_digest, input_digest, tree_manifest


def proposal_receipt(head, base, name):
    """Find discovery inputs on the bounded accepted first-parent lineage."""
    from tools import update as u
    pending = [head]
    seen = set()
    while pending:
        current = pending.pop(0)
        if current in seen or current == base:
            continue
        if len(seen) >= 256:
            raise ValueError('recipe proposal ancestry exceeds bounded lookup')
        seen.add(current)
        receipt = recipe_state.load_optional('proposal', current)
        if receipt is not None:
            if receipt.get('recipe_base') != base or receipt.get('pkgbase') != name or receipt.get('recipe_head') != current:
                raise ValueError('durable proposal base/package/head mismatch')
            return receipt
        commit = u.api(u.route('/git/commits/' + current))
        # Bot proposal heads and subsequent edits retain discovery on their
        # first-parent lineage. Secondary parents may be arbitrarily deep AUR
        # history, not discovery authority for this recipe proposal.
        if commit['parents']:
            pending.append(commit['parents'][0]['sha'])
    return None


def source_boundaries(old, new, policy):
    if [s['id'] for s in old['sources']] != [s['id'] for s in new['sources']]:
        raise ValueError('source enrollment/order changed')
    enrolled = {s['id']: s for s in policy['sources']}
    for previous, current in zip(old['sources'], new['sources']):
        if previous == current:
            continue
        if previous['kind'] == current['kind'] == 'local':
            if any(previous.get(key) != current.get(key) for key in previous.keys() | current.keys() if key != 'checksums') or set(previous['checksums']) != set(current['checksums']):
                raise ValueError('human recipe edit changed local source enrollment')
        elif not enrolled[current['id']]['mutable']:
            raise ValueError('human recipe edit changed fixed source')


def edited_provenance(previous_lock, lock, provenance, watcher_id, *, related_changes=False):
    """Resolve the edited native tag, never an unrelated latest release."""
    from tools import update as u
    result = copy.deepcopy(provenance)
    watcher = next(w for w in result['watchers'] if w['id'] == watcher_id)
    changed = {new['id'] for old, new in zip(previous_lock['sources'], lock['sources']) if old != new and new['kind'] != 'local'}
    allowed = {watcher.get('source_id'), *watcher.get('related_source_ids', [])}
    if watcher['kind'] != 'aur' and changed - allowed and not related_changes:
        raise ValueError('human edit changed unrelated enrolled source')
    if not changed.intersection(allowed) or watcher['kind'] == 'aur':
        return result
    if watcher['kind'] not in {'tag', 'release'}:
        return result
    source = next(s for s in lock['sources'] if s['id'] == watcher['source_id'])
    if source['kind'] == 'git':
        if not source['ref'].startswith('refs/tags/'):
            raise ValueError('edited watcher requires exact enrolled tag')
        tag = source['ref'].removeprefix('refs/tags/')
    else:
        match = re.fullmatch(watcher['tag_pattern'], watcher.get('accepted_tag', ''))
        if not match or not match.groups():
            raise ValueError('cannot derive edited enrolled tag')
        version = u.parse_version(lock['version'])
        values = [version] if len(match.groups()) == 1 else version.split('.')
        if len(values) != len(match.groups()):
            raise ValueError('edited version differs from enrolled tag structure')
        tag = match[0]
        for index in reversed(range(1, len(values) + 1)):
            start, end = match.span(index)
            if start < 0:
                raise ValueError('ambiguous enrolled tag structure')
            tag = tag[:start] + values[index - 1] + tag[end:]
    if not re.fullmatch(watcher['tag_pattern'], tag):
        raise ValueError('edited source tag is not enrolled')
    rows = u.sources.git('ls-remote', watcher['url'], 'refs/tags/' + tag, 'refs/tags/' + tag + '^{}').splitlines()
    identities = {row.split()[1]: row.split()[0] for row in rows}
    obj = identities.get('refs/tags/' + tag)
    if not obj or not u.SHA.fullmatch(obj):
        raise ValueError('edited source tag lacks authentic identity')
    watcher.update(accepted_tag=tag, accepted_tag_object=obj, accepted_peeled_commit=identities.get('refs/tags/' + tag + '^{}', obj))
    if watcher['kind'] == 'release':
        release = json.loads(u.sources.fetch('https://api.github.com/repos/' + watcher['repository'] + '/releases/tags/' + tag))
        assets = [asset for asset in release['assets'] if asset['name'] == watcher['asset']]
        if release['draft'] or release['prerelease'] or len(assets) != 1 or source['url'] != assets[0]['browser_download_url']:
            raise ValueError('edited release source differs from exact authentic asset')
        source.update(release_id=release['id'], asset_id=assets[0]['id'])
        watcher.update(release_id=release['id'], asset_id=assets[0]['id'])
    return result


def manual_provenance(previous_lock, lock, provenance):
    """Derive only the watchers covering exact changed remote native inputs."""
    changed = {new['id'] for old, new in zip(previous_lock['sources'], lock['sources'])
               if old != new and new['kind'] != 'local'}
    selected = []
    covered = set()
    for watcher in provenance['watchers']:
        enrolled = {watcher.get('source_id'), *watcher.get('related_source_ids', [])}
        actual = changed.intersection(enrolled)
        if not actual:
            continue
        if watcher['kind'] not in {'tag', 'release'}:
            raise ValueError('changed remote source lacks exact-derived watcher provenance')
        if covered.intersection(actual):
            raise ValueError('changed remote source has ambiguous watcher enrollment')
        selected.append(watcher['id'])
        covered.update(actual)
    if changed - covered:
        raise ValueError('changed remote source is not enrolled in exact-derived watcher provenance')
    result = copy.deepcopy(provenance)
    for watcher_id in selected:
        result = edited_provenance(previous_lock, lock, result, watcher_id, related_changes=True)
    return result, selected


def export_recipe(sha, destination):
    from tools import update as u
    archive = Path(str(destination) + '.tar.gz')
    u.download('https://api.github.com/repos/' + u.repository() + '/tarball/' + sha, archive, maximum=u.MAX_TREE)
    return u.extract_tree(archive, destination)


def required_statuses(head):
    from tools import update as u
    states = {s['context']: s['state'] for s in reversed(u.api(u.route('/commits/' + head + '/statuses')))}
    if any(states.get(key) != 'success' for key in ('verify', 'candidate-build', 'recipe-policy')):
        raise ValueError('required exact-head statuses missing')
    return {key: states[key] for key in ('verify', 'candidate-build', 'recipe-policy')}


def frozen_record(record):
    """Run association belongs to artifacts, not immutable approval identity."""
    return {key: value for key, value in record.items() if key not in {'run_id', 'run_attempt', 'accepted'}}


def candidate_digest(record):
    return recipe_state.digest(frozen_record(record))


def identity(record):
    return {key: record[key] for key in ('kind', 'repository', 'pr_number', 'pkgbase', 'base', 'recipe_base', 'head', 'head_branch', 'recipe_tree', 'receipt_id')}


def durable_build(record):
    built = recipe_state.load('built', recipe_state.identity_key(record))
    if built.get('candidate_digest') != candidate_digest(record) or built.get('native_verified') is not True:
        raise ValueError('durable native build does not match exact candidate')
    if any(built.get(key) != value for key, value in identity(record).items()):
        raise ValueError('durable build identity mismatch')
    return built


def durable_approval(record):
    approved = recipe_state.load('approved', recipe_state.identity_key(record))
    if approved.get('candidate_digest') != candidate_digest(record) or any(approved.get(key) != value for key, value in identity(record).items()):
        raise ValueError('durable approval does not match exact candidate')
    expected = '' if record['mechanical'] else 'recipe-review'
    if approved.get('review_environment') != expected:
        raise ValueError('durable approval environment mismatch')
    return approved


def prepare(number, output):
    from tools import update as u
    metadata = u.api(u.route('/pulls/' + str(number)))
    merged = metadata['state'] == 'closed' and metadata.get('merged') is True
    pr, control, head = recipe_state.recipe_identity(number, merged=merged)
    for context in ('verify', 'candidate-build', 'recipe-policy'):
        u.status(head, context, 'pending', 'Revalidating exact recipe/control identity')
    name = pr['base']['ref'].removeprefix('pkg/')
    base = pr['base']['sha']
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    receipt = proposal_receipt(head, base, name)
    origin = 'manual' if receipt is None else receipt['proposal_origin']
    if origin not in {'manual', 'watcher', 'legacy-accepted'}:
        raise ValueError('unknown durable proposal origin')
    pins = {}
    old = u.checkout_data(control, output/'base-tree', pins)
    policy = u.policy_at(old)[name]
    oldlock = u.load(old/'inputs'/f'{name}.json')
    oldpro = u.load(old/'upstream'/f'{name}.json')
    predecessor_lock, predecessor_provenance = copy.deepcopy(oldlock), copy.deepcopy(oldpro)
    comparison = old
    adoption = origin == 'legacy-accepted'
    previous_control_pin = base
    if adoption:
        from tools.recipe_acceptance import verify_legacy_adoption
        verified = verify_legacy_adoption(receipt, control, old, pins)
        oldlock, oldpro = verified['baseline_lock'], verified['baseline_provenance']
        predecessor_lock = verified['predecessor_lock']
        predecessor_provenance = verified['predecessor_provenance']
        previous_control_pin = verified['expected_previous_control_pin']
        comparison = output/'comparison-tree'
        shutil.copytree(old, comparison, symlinks=True)
        shutil.rmtree(comparison/'recipes'/name)
        baseline = export_recipe(base, output/'recipe-baseline')
        recipes.copy_recipe(baseline, comparison/'recipes'/name)
        u.dump(comparison/'inputs'/f'{name}.json', oldlock)
        u.dump(comparison/'upstream'/f'{name}.json', oldpro)
    elif pins[name] != base:
        raise ValueError('accepted main recipe pin differs from recipe branch base')
    manual = origin == 'manual'
    if receipt is not None and not manual and not any(w['id'] == receipt['watcher_id'] for w in oldpro['watchers']):
        raise ValueError('discovery watcher is not enrolled')
    commit = u.api(u.route('/git/commits/' + head))
    tree = commit['tree']['sha']
    record_identity = {'kind': 'recipe', 'repository': u.repository(), 'base': control, 'head': head, 'head_branch': pr['head']['ref'], 'recipe_base': base, 'recipe_tree': tree, 'pkgbase': name, 'pr_number': int(number)}
    if merged:
        accepted = pr['merge_commit_sha']
        accepted_commit = u.api(u.route('/git/commits/' + accepted))
        if accepted_commit['tree']['sha'] != tree or [parent['sha'] for parent in accepted_commit['parents']] != [base, head]:
            raise ValueError('accepted merge differs from exact approved recipe tree/history')
        record_identity['accepted'] = accepted
    recipe = export_recipe(head, output/'recipe-head')
    recipe_manifest = tree_manifest(recipe)
    for entry in recipe_manifest:
        u.recipe_payload_path(entry['path'])
    lock = copy.deepcopy(oldlock if manual else receipt['lock'])
    provenance = copy.deepcopy(oldpro if manual else receipt['provenance'])
    u.validate_source_policy(lock, policy)
    # Discovery inputs never authorize the native tree. Direct human heads
    # start from accepted C/B and independently derive exact H inputs.
    if manual or head != receipt['recipe_head']:
        metadata = u.align_aur_lock(recipe, lock, policy, output/'edited-freeze')
        lock['version'] = metadata['version']
        if manual:
            provenance, watcher_ids = manual_provenance(oldlock, lock, oldpro)
        else:
            provenance = edited_provenance(receipt['lock'], lock, provenance, receipt['watcher_id'])
    if not manual:
        watcher_ids = [receipt['watcher_id']]
    source_boundaries(oldlock, lock, policy)
    if receipt is not None:
        source_boundaries(receipt['lock'], lock, policy)
    u.validate_source_policy(lock, policy)
    u.verify_provenance(oldpro, provenance, lock, policy, None if manual else receipt['watcher_id'])
    for source in lock['sources']:
        if source['kind'] == 'git':
            u.sources.materialize_sources({'schema': 1, 'version': lock['version'], 'sources': [source]}, output/('frozen-' + source['id']))
        elif source['kind'] == 'local':
            path = recipe/source['source']
            if not path.resolve().is_relative_to(recipe.resolve()):
                raise ValueError('local source escapes recipe')
            for algorithm, expected in source['checksums'].items():
                if expected != 'SKIP' and hashlib.new(algorithm, path.read_bytes()).hexdigest() != expected:
                    raise ValueError('frozen local source checksum mismatch')
        else:
            u.sources.freeze_source(source)
    native = u.native_probe(recipe, lock, policy, output/'native-probe', preserve_pkgrel=True)
    if native['srcinfo'] != (recipe/'.SRCINFO').read_text() or native['sources'] != lock['sources']:
        raise ValueError('native recipe differs from frozen candidate source metadata')
    lock['version'] = native['version']
    u.validate_source_policy(lock, policy)
    if (manual or head != receipt['recipe_head']) and recipe_manifest != tree_manifest(comparison/'recipes'/name):
        if recipe_gate._compare(oldlock['version'], lock['version']) <= 0:
            raise ValueError('manual package changes require a version or pkgrel bump')
    # Synthetic control data exists only in the trusted validator workspace.
    new = output/'synthetic-tree'
    shutil.copytree(old, new, symlinks=True)
    shutil.rmtree(new/'recipes'/name)
    recipes.copy_recipe(recipe, new/'recipes'/name)
    u.dump(new/'inputs'/f'{name}.json', lock)
    u.dump(new/'upstream'/f'{name}.json', provenance)
    decision = classify_recipe_update(comparison/'recipes'/name, recipe, policy)
    if decision['decision'] == 'invalid':
        raise ValueError(decision['reason'])
    transition = u.independent_transition(comparison, new, name, policy, output/'verify-source')
    if transition:
        trusted = {**policy, '_verified_transition': transition}
        decision = classify_recipe_update(comparison/'recipes'/name, recipe, trusted)
    if not recipes.is_ancestor(u.repository(), base, head):
        raise ValueError('recipe head does not retain accepted recipe ancestry')
    if manual:
        proposal = {'schema': 1, 'proposal_origin': 'manual', 'repository': u.repository(),
                    'pkgbase': name, 'base': control, 'recipe_base': base,
                    'recipe_head': head, 'recipe_tree': tree, 'head_branch': pr['head']['ref'],
                    'watcher_id': watcher_ids[0] if len(watcher_ids) == 1 else None,
                    'watcher_ids': watcher_ids, 'lock': lock, 'provenance': provenance,
                    'native_srcinfo': native['srcinfo'], 'recipe_manifest': recipe_manifest}
        if receipt is not None and receipt['recipe_head'] == head:
            # The proposal freezes H's inputs, not the moving control commit.
            # Revalidation under a newer C creates a new candidate tuple while
            # retaining the original immutable source receipt and its digest.
            if {key: value for key, value in receipt.items() if key != 'base'} != {key: value for key, value in proposal.items() if key != 'base'}:
                raise ValueError('immutable manual proposal differs from independently frozen head')
        else:
            receipt = proposal
    record_identity.update(watcher_id=receipt['watcher_id'], watcher_ids=watcher_ids,
                           proposal_origin=origin,
                           previous_control_pin=previous_control_pin,
                           receipt_id=recipe_state.digest(receipt))
    recipe_state.assert_recipe_identity(record_identity)
    image = (old/'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', image):
        raise ValueError('build image is not pinned official Arch')
    harness = harness_digest(old)
    bundled_recipe = output/'bundle'/'recipes'/name
    recipes.copy_recipe(recipe, bundled_recipe)
    package = {'pkgbase': name, 'recipe_commit': head, 'previous_recipe_commit': base, 'recipe_dir': f'recipes/{name}', 'lock': lock, 'policy': policy, 'input_digest': input_digest(recipe, lock, policy, image, harness), 'expected_srcinfo': native['srcinfo']}
    bundle = {'schema': 1, 'repository': u.repository(), 'base': control, 'head': head, 'recipe_pins': {name: head}, 'previous_recipe_pins': {name: base}, 'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'image': image, 'harness_sha': harness, 'packages': [package]}
    mechanical = decision['decision'] == 'mechanical'
    if not mechanical:
        u.require_review_environment('recipe-review')
    record = {**bundle, **record_identity, 'control_digest': recipe_state.control_digest(old), 'mechanical': mechanical, 'review_environment': '' if mechanical else 'recipe-review', 'decisions': [{'pkgbase': name, **decision}], 'predecessor_lock': predecessor_lock, 'predecessor_provenance': predecessor_provenance, 'baseline_lock': oldlock, 'baseline_provenance': oldpro, 'provenance': provenance, 'proposal_head': receipt['recipe_head']}
    recipe_state.assert_recipe_identity(record)
    if manual:
        recipe_state.save('proposal', head, receipt)
    recipe_state.save('candidate', recipe_state.identity_key(record), frozen_record(record))
    u.dump(output/'candidate.json', record)
    u.dump(output/'bundle'/'bundle.json', bundle)
    with tarfile.open(output/'bundle.tar', 'w') as archive:
        archive.add(output/'bundle', arcname='bundle', recursive=True)
    u.status(head, 'verify', 'success', 'Exact trusted-main recipe source/native validation passed')
    u.status(head, 'candidate-build', 'pending', 'Building complete frozen recipe candidate')
    u.status(head, 'recipe-policy', 'pending', 'Source policy verified; awaiting exact build/review')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(f'base={control}\nhead={head}\nmechanical={str(mechanical).lower()}\nreview_environment={record["review_environment"]}\nkind=recipe\n')
    for directory in (old, new, recipe, output/'bundle', output/'native-probe', output/'verify-source', output/'comparison-tree', output/'recipe-baseline'):
        if directory.exists():
            u.remove_verification_tree(directory)
    return record


def validate_build(record, directory, report=True):
    from tools import update as u
    record = u.load(record) if not isinstance(record, dict) else record
    recipe_state.assert_recipe_identity(record)
    stored = recipe_state.load('candidate', recipe_state.identity_key(record))
    if stored != frozen_record(record):
        raise ValueError('candidate differs from durable frozen record')
    evidence = u.validate_build_outputs(record, directory, report=False)
    recipe_state.assert_recipe_identity(record)
    if not report:
        return evidence
    u.status(record['head'], 'candidate-build', 'success', 'Every frozen recipe package output verified')
    if record['mechanical']:
        u.status(record['head'], 'recipe-policy', 'success', 'Independent mechanical recipe transition verified')
    states = {s['context']: s['state'] for s in reversed(u.api(u.route('/commits/' + record['head'] + '/statuses')))}
    if states.get('verify') != 'success' or states.get('candidate-build') != 'success':
        raise ValueError('successful exact-head source/native statuses missing')
    built = {**identity(record), 'candidate_digest': candidate_digest(record), 'native_verified': True, 'evidence': evidence, 'statuses': {key: states[key] for key in ('verify', 'candidate-build')}}
    key = recipe_state.identity_key(record)
    if recipe_state.load_optional('built', key) is None:
        recipe_state.attest('built', key, built, record['head'])
        recipe_state.save('built', key, built)
    else:
        persisted = durable_build(record)
        recipe_state.require_attestation('built', key, persisted, record['head'])
    if record['mechanical']:
        required_statuses(record['head'])
        recipe_state.save('approved', recipe_state.identity_key(record), {**identity(record), 'candidate_digest': candidate_digest(record), 'review_environment': ''})
    return evidence


def approval_run(record):
    from tools import update as u
    run_id = os.environ['GITHUB_RUN_ID']
    attempt = os.environ['GITHUB_RUN_ATTEMPT']
    run = u.api(u.route('/actions/runs/' + run_id))
    if (run.get('head_sha') != record['base'] or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/candidate.yml'
            or run.get('display_title') != f"Candidate PR {record['pr_number']} head {record['head']}"
            or run.get('run_attempt') != int(attempt)):
        raise ValueError('human approval workflow identity mismatch')
    history = u.api(u.route('/actions/runs/' + run_id + '/approvals'))
    if not any(review.get('state') == 'approved' and any(environment.get('name') == 'recipe-review' for environment in review.get('environments', [])) for review in history):
        raise ValueError('human recipe environment approval evidence missing')
    return {'run_id': run_id, 'run_attempt': attempt}


def review(record):
    from tools import update as u
    record = u.load(record) if not isinstance(record, dict) else record
    if record['mechanical'] or record.get('review_environment') != 'recipe-review':
        raise ValueError('only nonmechanical recipe-review candidates use human checkpoint')
    u.require_review_environment('recipe-review')
    recipe_state.assert_recipe_identity(record)
    built = durable_build(record)
    recipe_state.require_attestation('built', recipe_state.identity_key(record), built, record['head'])
    states = {s['context']: s['state'] for s in reversed(u.api(u.route('/commits/' + record['head'] + '/statuses')))}
    if any(states.get(key) != 'success' for key in ('verify', 'candidate-build')):
        raise ValueError('review requires successful exact-head native build')
    recipe_state.assert_recipe_identity(record)
    proof = approval_run(record)
    key = recipe_state.identity_key(record)
    approved = {**identity(record), 'candidate_digest': candidate_digest(record), 'review_environment': 'recipe-review'}
    recipe_state.save('approved', key, approved)
    proof_key = key + '-' + proof['run_id'] + '-' + proof['run_attempt']
    actual_proof = {**approved, **proof}
    recipe_state.attest('approved', proof_key, actual_proof, record['head'])
    recipe_state.save('approved', proof_key, actual_proof)
    u.status(record['head'], 'recipe-policy', 'success', 'Owner approved exact recipe tuple through recipe-review')


def finalize(number, base, head, record_path, directory):
    from tools import update as u
    from tools import recipe_acceptance
    record = u.load(record_path)
    if (record['pr_number'], record['base'], record['head']) != (int(number), base, head):
        raise ValueError('finalize candidate binding mismatch')
    validate_build(record, directory)
    recipe_state.assert_recipe_identity(record)
    durable_build(record)
    durable_approval(record)
    required_statuses(head)
    if record.get('accepted'):
        if not record['mechanical']:
            u.api(u.route('/actions/workflows/update.yml/dispatches'), 'POST', {'ref': 'main', 'inputs': {}})
            return {'merged': True, 'sha': record['accepted'], 'reason': 'Bookkeeping awaits successful approval workflow reconciliation'}
        return recipe_acceptance.reconcile(number)
    if not record['mechanical']:
        return {'merged': False, 'reason': 'Human merge required'}
    recipe_state.assert_recipe_identity(record)
    result = u.api(u.route('/pulls/' + str(number) + '/merge'), 'PUT', {'sha': head, 'merge_method': 'merge'})
    if not result.get('merged') or not u.SHA.fullmatch(result.get('sha', '')):
        raise ValueError('expected-head recipe merge failed')
    recipe_acceptance.reconcile(number)
    return result
