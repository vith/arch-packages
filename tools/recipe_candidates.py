"""Trusted-main validation of native, recipe-only pull requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

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
    keys = ('kind', 'repository', 'pr_number', 'pkgbase', 'base', 'recipe_base', 'head', 'head_branch', 'recipe_tree', 'receipt_id')
    return {key: record[key] for key in keys if key in record}


def durable_approval(record):
    approved = recipe_state.load('approved', recipe_state.identity_key(record))
    if approved.get('candidate_digest') != candidate_digest(record) or any(approved.get(key) != value for key, value in identity(record).items()):
        raise ValueError('durable approval does not match exact candidate')
    expected = record['review_environment']
    if approved.get('review_environment') != expected:
        raise ValueError('durable approval environment mismatch')
    return approved


def _current_content(record, control, old, pins, recipe, work):
    """Compare accepted content, never the moving controller's build machinery."""
    from tools import update as u
    name = record['pkgbase']
    package = record['packages'][0]
    if (pins.get(name) != record['previous_control_pin']
            or u.policy_at(old)[name] != package['policy']
            or u.load(old/'inputs'/f'{name}.json') != record['predecessor_lock']
            or u.load(old/'upstream'/f'{name}.json') != record['predecessor_provenance']):
        raise ValueError('current controller recipe/source/policy baseline changed')
    if recipe_state.digest(tree_manifest(recipe)) != package['tree_sha']:
        raise ValueError('current recipe payload differs from original completed candidate')
    native = u.static_metadata(recipe, package['lock'], package['policy'], work,
                               preserve_pkgrel=True)
    if (native['srcinfo'] != package['expected_srcinfo']
            or native['sources'] != package['lock']['sources']
            or native['version'] != package['lock']['version']
            or (recipe/'.SRCINFO').read_text() != package['expected_srcinfo']):
        raise ValueError('current native metadata differs from original completed candidate')


def verify_current_controller(record, context):
    """Authenticate C2 separately without modifying the original signed C tuple."""
    from tools import update as u
    import tempfile
    if not isinstance(context, dict):
        context = u.load(context)
    expected = {'schema': 1, 'base': context['base'], 'recipe_base': record['recipe_base'],
                'head': record['head'], 'candidate_digest': candidate_digest(record),
                'descriptor_digest': context['descriptor_digest'],
                'run_id': context['run_id'], 'run_attempt': context['run_attempt']}
    if context.get('accepted') is not None:
        expected['accepted'] = context['accepted']
    if context.get('original_transport') is not None:
        expected['original_transport'] = context['original_transport']
        if context['descriptor_digest'] is not None:
            raise ValueError('ambiguous original completed transport identity')
    if context != expected:
        raise ValueError('current controller context binding mismatch')
    pr, control, head = recipe_state.recipe_identity(
        record['pr_number'], context['base'], record['head'],
        merged=bool(context.get('accepted') or record.get('accepted')))
    if (pr['base']['sha'] != record['recipe_base']
            or pr['head']['ref'] != record['head_branch']
            or u.api(u.route('/git/commits/'+head))['tree']['sha'] != record['recipe_tree']):
        raise ValueError('current controller PR recipe identity changed')
    accepted = context.get('accepted') or record.get('accepted')
    if accepted:
        if pr.get('merge_commit_sha') != accepted:
            raise ValueError('current accepted recipe merge changed')
        commit = u.api(u.route('/git/commits/'+accepted))
        if (commit['tree']['sha'] != record['recipe_tree']
                or [parent['sha'] for parent in commit['parents']] != [record['recipe_base'], record['head']]):
            raise ValueError('current accepted recipe history changed')
    run_id, attempt = str(context['run_id']), str(context['run_attempt'])
    if not run_id.isdecimal() or not attempt.isdecimal() or int(attempt) < 1:
        raise ValueError('invalid current controller run identity')
    run = u.api(u.route('/actions/runs/'+run_id+'/attempts/'+attempt))
    expected_title = f'Candidate PR {record["pr_number"]} head {record["head"]}'
    if (run.get('head_sha') != control or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/candidate.yml'
            or run.get('display_title') != expected_title
            or str(run.get('run_attempt')) != attempt
            or run.get('repository', {}).get('full_name') != u.repository()):
        raise ValueError('current controller Actions provenance mismatch')
    from tools import build_store
    from tools.package_runs import OriginalTransportRecoveryRequired
    transport = context.get('original_transport')
    recovery_pending = False
    try:
        descriptor = build_store.lookup(record['packages'][0], record)
    except OriginalTransportRecoveryRequired as recovery:
        if (transport is None or recovery.record != record
                or transport != {'producer': recovery.producer, 'artifact': recovery.artifact}):
            raise ValueError('original completed transport identity changed')
        descriptor = None
        recovery_pending = True
    if descriptor is None and not recovery_pending:
        raise ValueError('original completed build is no longer recoverable')
    if transport is None:
        if descriptor is None or recipe_state.digest(descriptor) != context['descriptor_digest'] or descriptor['record'] != record:
            raise ValueError('original completed build descriptor changed')
    elif descriptor is not None:
        if (descriptor['record'] != record or descriptor['producer'] != transport['producer']
                or descriptor['attestation']['original_artifact'] != transport['artifact']):
            raise ValueError('recovered descriptor differs from original completed transport')
    scratch = Path.home()/'.local/state/omp/work/native-current-controller'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as work:
        root = Path(work)
        pins = {}
        old = u.checkout_data(control, root/'control', pins)
        recipe = export_recipe(head, root/'recipe')
        _current_content(record, control, old, pins, recipe, root/'metadata')
    verify_authorization(record)
    return context


def controller_context(record_path):
    path = Path(record_path).parent/'controller-context.json'
    return path if path.exists() else None


def _completed_candidate(number, control, head, base, tree, old, pins, recipe, output, accepted=None):
    """Select original successful authority before live freezing or classification."""
    from tools import build_store
    from tools import update as u
    for key in recipe_state.keys('candidate', ''):
        if not key.endswith('-'+base+'-'+head):
            continue
        candidate = recipe_state.load('candidate', key)
        if (candidate.get('kind') != 'recipe' or candidate.get('pr_number') != int(number)
                or candidate.get('recipe_tree') != tree):
            continue
        from tools.package_runs import OriginalTransportRecoveryRequired
        transport = None
        try:
            descriptor = build_store.lookup(candidate['packages'][0], candidate)
        except OriginalTransportRecoveryRequired as recovery:
            descriptor = None
            original = recovery.record
            transport = {'producer': recovery.producer, 'artifact': recovery.artifact}
        else:
            if descriptor is None:
                continue
            original = descriptor['record']
        _current_content(original, control, old, pins, recipe, output/'reuse-metadata')
        verify_authorization(original)
        verify_candidate_provenance(original)
        context = {'schema': 1, 'base': control, 'recipe_base': base, 'head': head,
                   'candidate_digest': candidate_digest(original),
                   'descriptor_digest': recipe_state.digest(descriptor) if descriptor is not None else None,
                   'run_id': os.environ['GITHUB_RUN_ID'],
                   'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']}
        if accepted is not None:
            context['accepted'] = accepted
        if transport is not None:
            context['original_transport'] = transport
        for path, value in ((output/'controller-context.json', context),
                            (output/'candidate.json', original)):
            if path.exists():
                if path.read_bytes() != u.sources.canonical(value):
                    raise ValueError('immutable prepared controller/candidate bytes changed')
            else:
                u.dump(path, value)
        bundled = output/'bundle'
        recipes.copy_recipe(recipe, bundled/'recipes'/original['pkgbase'])
        bundle = {key: original[key] for key in ('schema', 'repository', 'base', 'head',
                  'recipe_pins', 'previous_recipe_pins', 'run_id', 'run_attempt',
                  'image', 'harness_sha', 'packages')}
        u.dump(bundled/'bundle.json', bundle)
        with tarfile.open(output/'bundle.tar', 'w') as archive:
            archive.add(bundled, arcname='bundle', recursive=True)
        u.status(head, 'verify', 'success', 'Original frozen sources and current native baseline verified')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
                handle.write(f'base={control}\nhead={head}\nmechanical={str(original["mechanical"]).lower()}\nreview_environment={original["review_environment"]}\nkind=recipe\ncandidate={output / "candidate.json"}\nprovenance_exists=true\nauthorized_reuse=true\n')
        for directory in (old, recipe, bundled, output/'reuse-metadata'):
            if directory.exists():
                u.remove_verification_tree(directory)
        return original
    return None


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
    completed = _completed_candidate(number, control, head, base, tree, old, pins, recipe, output,
                                     accepted=record_identity.get('accepted'))
    if completed is not None:
        return completed
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
    native = u.static_metadata(recipe, lock, policy, output/'static-metadata', preserve_pkgrel=True)
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
    package = {'pkgbase': name, 'recipe_commit': head, 'previous_recipe_commit': base, 'recipe_dir': f'recipes/{name}', 'lock': lock, 'policy': policy, 'input_digest': input_digest(recipe, lock, policy, image, harness), 'tree_sha': recipe_state.digest(recipe_manifest), 'expected_srcinfo': native['srcinfo']}
    bundle = {'schema': 1, 'repository': u.repository(), 'base': control, 'head': head, 'recipe_pins': {name: head}, 'previous_recipe_pins': {name: base}, 'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'image': image, 'harness_sha': harness, 'packages': [package]}
    mechanical = decision['decision'] == 'mechanical'
    needs_review = recipe_gate.needs_pkgbuild_review(comparison/'recipes'/name, recipe, policy)
    if needs_review:
        u.require_review_environment('recipe-review')
    else:
        recipe_gate.verify_automatic_recipe(comparison/'recipes'/name, recipe, policy, oldlock, lock, transition)
    record = {**bundle, **record_identity, 'control_digest': recipe_state.control_digest(old), 'mechanical': mechanical, 'review_environment': 'recipe-review' if needs_review else '', 'decisions': [{'pkgbase': name, **decision}], 'predecessor_lock': predecessor_lock, 'predecessor_provenance': predecessor_provenance, 'baseline_lock': oldlock, 'baseline_provenance': oldpro, 'provenance': provenance, 'proposal_head': receipt['recipe_head']}
    recipe_state.assert_recipe_identity(record)
    if manual:
        recipe_state.save('proposal', head, receipt)
    recipe_state.save('candidate', recipe_state.identity_key(record), frozen_record(record))
    provenance_exists = candidate_provenance_exists(record)
    u.dump(output/'candidate.json', record)
    u.dump(output/'bundle'/'bundle.json', bundle)
    with tarfile.open(output/'bundle.tar', 'w') as archive:
        archive.add(output/'bundle', arcname='bundle', recursive=True)
    recipe_state.attest('candidate', recipe_state.identity_key(record), frozen_record(record), head)
    u.status(head, 'verify', 'success', 'Exact trusted-main static recipe/source validation passed')
    u.status(head, 'candidate-build', 'pending', 'Building complete frozen recipe candidate')
    u.status(head, 'recipe-policy', 'pending', 'Source policy verified; awaiting exact build/review')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(f'base={control}\nhead={head}\nmechanical={str(mechanical).lower()}\nreview_environment={record["review_environment"]}\nkind=recipe\ncandidate={output / "candidate.json"}\nprovenance_exists={str(provenance_exists).lower()}\n')
    for directory in (old, new, recipe, output/'bundle', output/'static-metadata', output/'verify-source', output/'comparison-tree', output/'recipe-baseline', *output.glob('frozen-*')):
        if directory.exists():
            u.remove_verification_tree(directory)
    return record


def validate_build(record, directory, report=True, context=None):
    from tools import update as u
    if not isinstance(record, dict):
        context = context or controller_context(record)
        record = u.load(record)
    if context is not None:
        verify_current_controller(record, context)
    else:
        recipe_state.assert_recipe_identity(record)
    verify_authorization(record)
    evidence = u.validate_build_outputs(record, directory, report=False)
    if context is not None:
        verify_current_controller(record, context)
    else:
        recipe_state.assert_recipe_identity(record)
    if not report:
        return evidence
    u.status(record['head'], 'candidate-build', 'success', 'Every frozen recipe package output verified')
    if record['mechanical']:
        u.status(record['head'], 'recipe-policy', 'success', 'Independent mechanical recipe transition verified')
    states = {s['context']: s['state'] for s in reversed(u.api(u.route('/commits/' + record['head'] + '/statuses')))}
    if states.get('verify') != 'success' or states.get('candidate-build') != 'success':
        raise ValueError('successful exact-head source/native statuses missing')
    return evidence


def _authority(record, proof, issuing=False):
    """Authenticate the original protected/static checkpoint, not a green status."""
    from tools import update as u
    run_id, attempt_text = str(proof.get('run_id', '')), str(proof.get('run_attempt', ''))
    if not run_id.isdecimal() or not attempt_text.isdecimal() or int(run_id) < 1 or int(attempt_text) < 1:
        raise ValueError('approval lacks exact workflow run/attempt')
    if not issuing and (not isinstance(proof.get('job_id'), int) or proof['job_id'] < 1):
        raise ValueError('approval lacks exact protected job identity')
    attempt = int(attempt_text)
    run = u.api(u.route('/actions/runs/' + run_id + '/attempts/' + str(attempt)))
    if (run.get('head_sha') != record['base'] or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/candidate.yml'
            or run.get('display_title') != f"Candidate PR {record['pr_number']} head {record['head']}"
            or run.get('run_attempt') != attempt):
        raise ValueError('approval workflow identity mismatch')
    environment = record['review_environment']
    name = ('Review' if environment else 'Authorize') + f" PR {record['pr_number']} head {record['head']} base {record['base']}"
    jobs = u.api(u.route('/actions/runs/' + run_id + '/attempts/' + str(attempt) + '/jobs?per_page=100'))
    matches = [job for job in jobs['jobs'] if job.get('name') == name]
    if len(matches) != 1 or jobs.get('total_count', len(jobs['jobs'])) > 100:
        raise ValueError('exact approval job missing or ambiguous')
    job = matches[0]
    if proof.get('job_id') is not None and int(proof['job_id']) != job['id']:
        raise ValueError('approval job identity mismatch')
    if not (job.get('conclusion') == 'success' or issuing and job.get('status') == 'in_progress'):
        raise ValueError('approval checkpoint did not succeed')
    if environment:
        if issuing or environment != 'code-review':
            u.require_review_environment(environment)
        if issuing and environment != 'recipe-review':
            raise ValueError('new human authorization requires recipe-review')
        actual = u.api(u.route('/environments/' + environment))
        history = u.api(u.route('/actions/runs/' + run_id + '/approvals'))
        if not any(review.get('state') == 'approved'
                   and review.get('user', {}).get('type') == 'User'
                   and any(item.get('id') == actual['id'] and item.get('name') == environment
                           for item in review.get('environments', [])) for review in history):
            raise ValueError('genuine exact protected environment approval missing')
    return {**proof, 'job_id': job['id']}


MAX_CANDIDATE_ARCHIVE = 16 * 1024 * 1024
MAX_CANDIDATE_JSON = 4 * 1024 * 1024
MAX_CANDIDATE_BUNDLE = 4 * 1024 * 1024


def _candidate_signer(record):
    from tools import update as u
    run_id, attempt = record.get('run_id'), record.get('run_attempt')
    if (record.get('repository') != u.repository()
            or not isinstance(record.get('base'), str) or not u.SHA.fullmatch(record['base'])
            or not isinstance(run_id, str) or not run_id.isdecimal() or int(run_id) < 1
            or not isinstance(attempt, str) or not attempt.isdecimal() or int(attempt) < 1):
        raise ValueError('candidate provenance original repository/control/run identity mismatch')
    return {'repository': record['repository'], 'path': '.github/workflows/candidate.yml',
            'head_sha': record['base'], 'run_id': run_id, 'run_attempt': attempt}


def verify_candidate_provenance(record, proof=None):
    """Verify the signed original PREBUILD subject, never a bot receipt alone."""
    from tools import attestations, sources
    if proof is None:
        proof = recipe_state.load('candidate-provenance', recipe_state.identity_key(record))
    if (not isinstance(proof, dict) or set(proof) != {'schema', 'candidate', 'signer', 'bundle'}
            or type(proof['schema']) is not int or proof['schema'] != 1
            or not isinstance(proof['candidate'], dict)):
        raise ValueError('invalid candidate provenance record')
    original = proof['candidate']
    if frozen_record(original) != frozen_record(record):
        raise ValueError('candidate provenance source differs from original frozen input')
    signer = _candidate_signer(original)
    if proof['signer'] != signer:
        raise ValueError('candidate provenance signer differs from original identity')
    subject = sources.canonical(original)
    if len(subject) > MAX_CANDIDATE_JSON:
        raise ValueError('candidate provenance subject exceeds bound')
    if not isinstance(proof['bundle'], dict) or not 0 < len(sources.canonical(proof['bundle'])) <= MAX_CANDIDATE_BUNDLE:
        raise ValueError('candidate provenance bundle exceeds bound')
    work = Path.home()/'.local/state/omp/work'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-provenance-', dir=work) as temporary:
        path = Path(temporary)/'candidate.json'
        path.write_bytes(subject)
        bundle_path = Path(temporary)/'bundle.json'
        bundle_path.write_bytes(sources.canonical(proof['bundle']))
        attestations.verify(path, bundle_path, signer)
    return proof


def candidate_provenance_exists(record):
    proof = recipe_state.load_optional('candidate-provenance', recipe_state.identity_key(record))
    if proof is None:
        return False
    verify_candidate_provenance(record, proof)
    return True


def persist_candidate_provenance(record_path, bundle_path):
    """Persist verified full original candidate and bounded Sigstore bundle once."""
    from tools import sources
    path = Path(record_path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_CANDIDATE_JSON:
        raise ValueError('candidate provenance subject is not bounded regular data')
    raw = path.read_bytes()
    record = json.loads(raw)
    if not isinstance(record, dict) or raw != sources.canonical(record):
        raise ValueError('candidate provenance requires exact canonical full candidate')
    key = recipe_state.identity_key(record)
    if recipe_state.load('candidate', key) != frozen_record(record):
        raise ValueError('candidate provenance differs from frozen controller input')
    existing = recipe_state.load_optional('candidate-provenance', key)
    if existing is not None:
        return verify_candidate_provenance(record, existing)
    bundle_path = Path(bundle_path)
    if bundle_path.is_symlink() or not bundle_path.is_file() or not 0 < bundle_path.stat().st_size <= MAX_CANDIDATE_BUNDLE:
        raise ValueError('candidate provenance bundle is not bounded regular data')
    proof = {'schema': 1, 'candidate': record, 'signer': _candidate_signer(record),
             'bundle': json.loads(bundle_path.read_bytes())}
    verify_candidate_provenance(record, proof)
    recipe_state.save('candidate-provenance', key, proof)
    return proof


def _download_candidate_artifact(route, destination):
    """Stream the exact authenticated artifact ID without executing ZIP members."""
    with subprocess.Popen(['gh', 'api', route], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            with destination.open('xb') as output:
                size = 0
                while chunk := process.stdout.read(65536):
                    size += len(chunk)
                    if size > MAX_CANDIDATE_ARCHIVE:
                        raise ValueError('prepared candidate artifact exceeds size bound')
                    output.write(chunk)
            if process.wait() != 0:
                raise ValueError('prepared candidate artifact download failed')
        except BaseException:
            process.kill()
            process.wait()
            raise


def _authorization_source(record, approved, issuing=False):
    """Bind authorization to independently signed PREBUILD original source bytes."""
    from tools import update as u
    proof = recipe_state.load_optional('candidate-provenance', recipe_state.identity_key(record))
    if proof is not None:
        verify_candidate_provenance(record, proof)
        return approved
    # Only already-issued legacy approvals may retain exact live ZIP authority.
    # Newly issued authorization always requires independent PREBUILD crypto.
    if issuing:
        raise ValueError('missing durable candidate provenance before authorization')
    run_id, attempt = str(approved['run_id']), str(approved['run_attempt'])
    name = 'prepared-candidate-' + attempt
    listing = u.api(u.route('/actions/runs/' + run_id + '/artifacts?per_page=100'))
    artifacts = listing.get('artifacts', [])
    if listing.get('total_count', len(artifacts)) > 100:
        raise ValueError('prepared candidate artifact lookup exceeds bounded size')
    matches = [item for item in artifacts if item.get('name') == name]
    if len(matches) > 1:
        raise ValueError('prepared candidate artifact is ambiguous')
    if not matches or matches[0].get('expired') is True:
        raise ValueError('original candidate provenance missing and prepared candidate artifact missing or expired')
    artifact = matches[0]
    artifact_id = artifact.get('id')
    digest = artifact.get('digest')
    size = artifact.get('size_in_bytes')
    workflow = artifact.get('workflow_run', {})
    if (type(artifact_id) is not int or artifact_id < 1
            or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
            or type(size) is not int or not 0 < size <= MAX_CANDIDATE_ARCHIVE
            or artifact.get('expired') is not False
            or workflow.get('id') != int(run_id)
            or workflow.get('head_sha') != record['base']
            or workflow.get('head_branch') != 'main'):
        raise ValueError('prepared candidate artifact identity/digest/bounds mismatch')
    if not issuing and (approved.get('candidate_artifact_id') != artifact_id
                        or approved.get('candidate_artifact_digest') != digest):
        raise ValueError('prepared candidate immutable artifact identity mismatch')
    exact = u.api(u.route('/actions/artifacts/' + str(artifact_id)))
    if exact != artifact:
        raise ValueError('prepared candidate exact artifact ID metadata mismatch')
    work = Path.home()/'.local/state/omp/work'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='approval-source-', dir=work) as temporary:
        archive = Path(temporary)/'candidate.zip'
        _download_candidate_artifact(u.route('/actions/artifacts/' + str(artifact_id) + '/zip'), archive)
        if (archive.stat().st_size != size
                or hashlib.sha256(archive.read_bytes()).hexdigest() != digest.removeprefix('sha256:')):
            raise ValueError('prepared candidate artifact ZIP digest/size mismatch')
        try:
            with zipfile.ZipFile(archive) as bundle:
                matches = [item for item in bundle.infolist() if item.filename == 'candidate.json']
                if len(matches) != 1:
                    raise ValueError('prepared candidate ZIP requires unique root candidate.json')
                member = matches[0]
                mode = member.external_attr >> 16
                if (member.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or not 0 < member.file_size <= MAX_CANDIDATE_JSON):
                    raise ValueError('prepared candidate ZIP record is not bounded regular data')
                with bundle.open(member) as handle:
                    raw = handle.read(MAX_CANDIDATE_JSON + 1)
                if len(raw) > MAX_CANDIDATE_JSON:
                    raise ValueError('prepared candidate JSON exceeds size bound')
                source = json.loads(raw)
        except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as error:
            raise ValueError('invalid prepared candidate ZIP record') from error
        if (not isinstance(source, dict) or source.get('run_id') != run_id
                or str(source.get('run_attempt')) != attempt
                or frozen_record(source) != frozen_record(record)):
            raise ValueError('prepared candidate source differs from original frozen input')
    return {**approved, 'candidate_artifact_id': artifact_id, 'candidate_artifact_digest': digest}


def _verify_inputs(record, automatic, transition_verifier, metadata_verifier):
    """Reconstruct C/H from immutable remote objects without evaluating recipes."""
    from tools import update as u
    import tempfile
    work = Path(os.environ.get('ARCH_WORK', str(Path.home()/'.local/state/omp/work')))
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='authorization-', dir=work) as temporary:
        root = Path(temporary)
        pins = {}
        old = u.checkout_data(record['base'], root/'control', pins)
        if record.get('control_digest') != recipe_state.control_digest(old):
            raise ValueError('approved controller source proof mismatch')
        if record['image'] != (old/'build-image.txt').read_text().strip() or record['harness_sha'] != harness_digest(old):
            raise ValueError('approved image/harness source proof mismatch')
        if record.get('kind') == 'recipe':
            head = export_recipe(record['head'], root/'recipe')
            commit = u.api(u.route('/git/commits/' + record['head']))
            if commit['tree']['sha'] != record['recipe_tree']:
                raise ValueError('approved recipe head tree mismatch')
            proposal = recipe_state.load('proposal', record['proposal_head'])
            if recipe_state.digest(proposal) != record['receipt_id']:
                raise ValueError('approved source receipt mismatch')
            if proposal['lock'] != record['packages'][0]['lock'] and record['proposal_head'] == record['head']:
                raise ValueError('approved proposal source lock mismatch')
            new = root/'synthetic'
            shutil.copytree(old, new, symlinks=True)
            name = record['pkgbase']
            shutil.rmtree(new/'recipes'/name)
            recipes.copy_recipe(head, new/'recipes'/name)
            u.dump(new/'inputs'/f'{name}.json', record['packages'][0]['lock'])
            u.dump(new/'upstream'/f'{name}.json', record['provenance'])
        else:
            newpins = {}
            new = u.checkout_data(record['head'], root/'head', newpins)
            if newpins != record['recipe_pins']:
                raise ValueError('approved recipe pins mismatch')
        for package in record['packages']:
            name = package['pkgbase']
            recipe = new/'recipes'/name
            policy = u.policy_at(old if record.get('kind') == 'recipe' else new)[name]
            if policy != package['policy'] or recipe_state.digest(tree_manifest(recipe)) != package['tree_sha']:
                raise ValueError('approved recipe tree/policy mismatch')
            if (recipe/'.SRCINFO').read_text() != package['expected_srcinfo']:
                raise ValueError('approved submitted metadata mismatch')
            u.validate_source_policy(package['lock'], policy)
            if u.load(new/'inputs'/f'{name}.json') != package['lock']:
                raise ValueError('approved source lock differs from frozen tree')
            if input_digest(recipe, package['lock'], policy, record['image'], record['harness_sha']) != package['input_digest']:
                raise ValueError('approved compilation input mismatch')
            derived = metadata_verifier(recipe, package['lock'], policy, root/('freeze-' + name), preserve_pkgrel=True)
            if derived['version'] != package['lock']['version'] or derived['srcinfo'] != package['expected_srcinfo'] or derived['sources'] != package['lock']['sources']:
                raise ValueError('authorization metadata differs from independent static derivation')
            if automatic:
                oldpolicy = u.policy_at(old).get(name)
                if oldpolicy is None:
                    raise ValueError('new recipe enrollment requires recipe-review')
                oldlock = u.load(old/'inputs'/f'{name}.json')
                source_boundaries(oldlock, package['lock'], policy)
                transition = transition_verifier(old, new, name, policy, root/('transition-' + name))
                recipe_gate.verify_automatic_recipe(old/'recipes'/name, recipe, oldpolicy, oldlock, package['lock'], transition)
        if record.get('kind') != 'recipe':
            oldpolicy, newpolicy = u.policy_at(old), u.policy_at(new)
            if set(newpolicy) != set(newpins):
                raise ValueError('candidate package enrollment and recipe pins differ')
            names, _ = u.affected_packages(old, new, newpolicy)
            names = sorted((set(names) | {name for name in newpolicy if pins.get(name) != newpins.get(name)}) & set(newpolicy))
            from tools.recipe_acceptance import validate_bookkeeping
            pr = u.api(u.route('/pulls/' + str(record['pr_number'])))
            bookkeeping = validate_bookkeeping(pr, record['base'], record['head'], old, new)
            if automatic and bookkeeping is None and ((pr.get('head') or {}).get('repo') or {}).get('full_name') != u.repository():
                raise ValueError('automatic main source requires same-repository head')
            if bookkeeping is not None:
                names = []
            if names != sorted(package['pkgbase'] for package in record['packages']):
                raise ValueError('main candidate compilation selection differs from independent scope')
            if not names and bookkeeping is None and oldpolicy == newpolicy:
                from tools.source_review import unchanged_packages
                unchanged_packages(old, new, pins, newpins)
            # Retirement/policy transitions execute no omitted recipe: selection
            # above is reconstructed from all frozen trees, policies and pins.
            for name in names:
                if pins.get(name) and pins[name] != newpins[name] and not recipes.is_ancestor(u.repository(), pins[name], newpins[name]):
                    raise ValueError('recipe history is not a fast-forward of the accepted pin')


def verify_authorization(record):
    """Verify original approval and frozen inputs independently of build success."""
    from tools import update as u
    if record.get('repository') != u.repository():
        raise ValueError('authorization repository mismatch')
    key = recipe_state.identity_key(record)
    stored = recipe_state.load('candidate', key)
    if stored != frozen_record(record):
        raise ValueError('candidate differs from original frozen input proof')
    recipe_state.require_attestation('candidate', key, stored, record['head'])
    approved = durable_approval(record)
    recipe_state.require_attestation('approved', key, approved, record['head'])
    automatic = approved.get('authorization_kind') == 'automatic'
    if automatic != (not bool(record['review_environment'])):
        raise ValueError('authorization kind/environment mismatch')
    _authority(record, approved)
    _authorization_source(record, approved)
    _verify_inputs(record, automatic, u.frozen_transition, u.frozen_metadata)
    return approved


def _approve(record, automatic):
    from tools import update as u
    record = u.load(record) if not isinstance(record, dict) else record
    if bool(record['review_environment']) == automatic:
        raise ValueError('authorization checkpoint type mismatch')
    if record.get('kind') == 'recipe':
        recipe_state.assert_recipe_identity(record)
    else:
        u.pr_identity(record['pr_number'], record['base'], record['head'])
    key = recipe_state.identity_key(record)
    stored = recipe_state.load('candidate', key)
    if stored != frozen_record(record):
        raise ValueError('authorization candidate changed')
    recipe_state.require_attestation('candidate', key, stored, record['head'])
    existing = recipe_state.load_optional('approved', key)
    if existing is not None:
        return verify_authorization(record)
    _verify_inputs(record, automatic, u.independent_transition, u.static_metadata)
    proof = _authority(record, {'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']}, issuing=True)
    proof = _authorization_source(record, proof, issuing=True)
    approved = {**identity(record), **proof, 'candidate_digest': candidate_digest(record),
                'review_environment': record['review_environment'],
                'authorization_kind': 'automatic' if automatic else 'human'}
    recipe_state.attest('approved', key, approved, record['head'])
    recipe_state.save('approved', key, approved)
    u.status(record['head'], 'recipe-policy', 'success', 'Exact frozen inputs authorized before execution')
    return approved


def authorize(record):
    return _approve(record, automatic=True)


def review(record):
    return _approve(record, automatic=False)


def finalize(number, base, head, record_path, directory):
    from tools import update as u
    from tools import recipe_acceptance
    record = u.load(record_path)
    context = controller_context(record_path)
    current = verify_current_controller(record, context) if context else None
    if (record['pr_number'], current['base'] if current else record['base'], record['head']) != (int(number), base, head):
        raise ValueError('finalize candidate binding mismatch')
    validate_build(record, directory, context=context)
    if context:
        verify_current_controller(record, context)
    else:
        recipe_state.assert_recipe_identity(record)
    verify_authorization(record)
    required_statuses(head)
    accepted = (current.get('accepted') if current else None) or record.get('accepted')
    if accepted:
        u.api(u.route('/actions/workflows/update.yml/dispatches'), 'POST', {'ref': 'main', 'inputs': {}})
        return {'merged': True, 'sha': accepted, 'reason': 'Bookkeeping awaits successful authorization workflow reconciliation'}
    if context:
        verify_current_controller(record, context)
    else:
        recipe_state.assert_recipe_identity(record)
    result = u.api(u.route('/pulls/' + str(number) + '/merge'), 'PUT', {'sha': head, 'merge_method': 'merge'})
    if not result.get('merged') or not u.SHA.fullmatch(result.get('sha', '')):
        raise ValueError('expected-head recipe merge failed')
    # The producer run is still in progress; reconciliation consumes its real
    # successful conclusion after completion, never a synthetic parent status.
    u.api(u.route('/actions/workflows/update.yml/dispatches'), 'POST', {'ref': 'main', 'inputs': {}})
    return result
