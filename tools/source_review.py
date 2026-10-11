"""Exact source tests for main PRs and accepted publication trees."""
import os
from pathlib import Path

from tools import recipe_state, update as u
from tools.recipe_gate import tree_manifest


PATHS = {'.github/workflows/candidate.yml', '.github/workflows/publish.yml'}


def commit(sha):
    if not u.SHA.fullmatch(sha):
        raise ValueError('invalid source-test commit')
    value = u.api(u.route('/git/commits/' + sha))
    if value.get('sha') != sha or not u.SHA.fullmatch(value.get('tree', {}).get('sha', '')):
        raise ValueError('source-test commit identity mismatch')
    return value


def producer(controller, path=None):
    run_id = os.environ['GITHUB_RUN_ID']
    attempt = os.environ['GITHUB_RUN_ATTEMPT']
    run = u.api(u.route('/actions/runs/' + run_id + '/attempts/' + attempt))
    if (run.get('id') != int(run_id) or run.get('run_attempt') != int(attempt)
            or run.get('head_sha') != controller or run.get('head_branch') != 'main'
            or run.get('path') not in PATHS or path and run.get('path') != path
            or run.get('event') not in {'workflow_dispatch', 'push'}
            or os.environ.get('GITHUB_SHA') != controller
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
        raise ValueError('source-test trusted main producer mismatch')
    return {'run_id': run_id, 'run_attempt': attempt,
            'head_sha': controller, 'path': run['path']}


def unchanged_packages(old, new, oldpins, newpins):
    if oldpins != newpins or u.policy_at(old) != u.policy_at(new):
        raise ValueError('source-only route cannot change package pins or enrollment')
    for name in oldpins:
        if tree_manifest(old/'recipes'/name) != tree_manifest(new/'recipes'/name):
            raise ValueError('source-only route cannot change recipe content')
    for directory in ('inputs', 'upstream', 'acceptance'):
        a = old/directory
        b = new/directory
        if a.exists() != b.exists() or a.exists() and tree_manifest(a) != tree_manifest(b):
            raise ValueError('source-only route cannot change package source or acceptance data')
    for filename in ('.gitmodules', 'packages.json'):
        a = old/filename
        b = new/filename
        if a.exists() != b.exists() or a.exists() and a.read_bytes() != b.read_bytes():
            raise ValueError('source-only route cannot change recipe enrollment paths')


def image_at(root):
    image = (root/'build-image.txt').read_text().strip()
    if not u.re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}', image):
        raise ValueError('source-test baseline image is not pinned official Arch')
    return image


def save_evidence(evidence):
    return recipe_state.save_source_test(evidence)


def validate_source(pr_number, base, head, directory):
    """Test an exact same-repository main PR using the trusted base harness."""
    from tools import imports, recipe_acceptance
    pr, base, head = u.pr_identity(pr_number, base, head)
    if pr['head']['repo']['full_name'] != u.repository():
        raise ValueError('source-test PR must belong to the trusted repository')
    identity = producer(base, '.github/workflows/candidate.yml')
    tree = commit(head)['tree']['sha']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    roots = []
    try:
        oldpins, newpins = {}, {}
        old = u.checkout_data(base, directory/'base', oldpins)
        roots.append(old)
        new = u.checkout_data(head, directory/'head', newpins)
        roots.append(new)
        bookkeeping = recipe_acceptance.validate_bookkeeping(pr, base, head, old, new)
        admission = None if bookkeeping is not None else imports.validate_admission(
            old, new, oldpins, newpins, directory/'admission')
        if bookkeeping is None and admission is None:
            retirement = u.validate_retirement(old, new, oldpins, newpins)
            if retirement is None:
                unchanged_packages(old, new, oldpins, newpins)
        image = image_at(old)
        u.pr_identity(pr_number, base, head)
        u.run_candidate_tests(new, image)
        u.pr_identity(pr_number, base, head)
        producer(base, '.github/workflows/candidate.yml')
        evidence = {'schema': 1, 'repository': u.repository(), 'base': base,
                    'head': head, 'tree': tree, 'pr_number': int(pr_number),
                    'producer': identity, 'image': image}
        save_evidence(evidence)
        u.dump(directory/'source-test-evidence.json', evidence)
        return evidence
    finally:
        for root in roots:
            u.remove_verification_tree(root)


def completed_evidence(evidence, sha, tree):
    """Authenticate the exact test record and its entire trusted producer."""
    if (not isinstance(evidence, dict) or evidence.get('schema') != 1
            or evidence.get('repository') != u.repository()
            or evidence.get('head') != sha or evidence.get('tree') != tree):
        raise ValueError('source-test evidence commit/tree mismatch')
    p = evidence['producer']
    number = evidence.get('pr_number')
    if not ((type(number) is int and number > 0 and p['path'] == '.github/workflows/candidate.yml')
            or (number is None and p['path'] == '.github/workflows/publish.yml'
                and evidence.get('execution') == 'in-process-isolated-tests')):
        raise ValueError('source-test evidence issuer is not a trusted source-test route')
    run = u.api(u.route('/actions/runs/' + str(p['run_id']) + '/attempts/' + str(p['run_attempt'])))
    if (run.get('id') != int(p['run_id']) or run.get('run_attempt') != int(p['run_attempt'])
            or run.get('path') != p['path'] or p['path'] not in PATHS
            or run.get('head_sha') != p['head_sha'] or p['head_sha'] != evidence['base']
            or run.get('head_branch') != 'main'
            or run.get('event') not in ({'workflow_dispatch'} if number is not None else {'push', 'workflow_dispatch'})
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        raise ValueError('source-test producer did not complete successfully')
    return evidence


def accepted_main(sha):
    target = commit(sha)
    main = u.main_sha()
    if sha != main:
        comparison = u.api(u.route('/compare/' + sha + '...' + main))
        if comparison.get('status') not in {'ahead', 'identical'}:
            raise ValueError('source-test target is not accepted main history')
    branch = u.api(u.route('/branches/main'))
    if branch.get('protected') is not True:
        raise ValueError('source-test main branch is not protected')
    return target


def verify_source_evidence(accepted_sha):
    """Reuse exact protected-merge tests, or test actual accepted main before signing."""
    accepted = accepted_main(accepted_sha)
    tree = accepted['tree']['sha']
    exact = recipe_state.source_test_evidence(accepted_sha)
    if exact is not None:
        try:
            return completed_evidence(exact, accepted_sha, tree)
        except (ValueError, KeyError, TypeError):
            pass
    parents = [p['sha'] for p in accepted['parents']]
    if len(parents) == 2:
        evidence = recipe_state.source_test_evidence(parents[1])
        if evidence is not None and type(evidence.get('pr_number')) is int and evidence['pr_number'] > 0:
            pr = u.api(u.route('/pulls/' + str(evidence['pr_number'])))
            if (pr.get('merged') is True and pr.get('merge_commit_sha') == accepted_sha
                    and pr['base']['ref'] == 'main'
                    and pr['base']['repo']['full_name'] == u.repository()
                    and pr['head']['repo']['full_name'] == u.repository()
                    and pr['head']['sha'] == parents[1] and evidence.get('base') == parents[0]):
                try:
                    return completed_evidence(evidence, parents[1], tree)
                except (ValueError, KeyError, TypeError):
                    pass
    # No push event is assumed after Actions-token merges.
    controller = os.environ['GITHUB_SHA']
    accepted_main(controller)
    identity = producer(controller, '.github/workflows/publish.yml')
    directory = Path(os.environ.get('ARCH_WORK', str(Path.home()/'.local/state/omp/work/source-tests')))
    directory = directory/'accepted-source'/accepted_sha/str(identity['run_id'])/str(identity['run_attempt'])
    directory.mkdir(parents=True, exist_ok=True)
    roots = []
    try:
        baseline = u.checkout_data(controller, directory/'controller')
        roots.append(baseline)
        root = u.checkout_data(accepted_sha, directory/'accepted')
        roots.append(root)
        image = image_at(baseline)
        u.run_candidate_tests(root, image)
        accepted_main(accepted_sha)
        producer(controller, '.github/workflows/publish.yml')
        # This is synchronous proof for this signing operation, not a claim
        # that the surrounding publication workflow has finished successfully.
        # Later reuse authenticates the whole completed publisher run.
        evidence = {'schema': 1, 'repository': u.repository(), 'base': controller,
                    'head': accepted_sha, 'tree': tree, 'pr_number': None,
                    'producer': identity, 'image': image,
                    'execution': 'in-process-isolated-tests'}
        save_evidence(evidence)
        return evidence
    finally:
        for root in roots:
            u.remove_verification_tree(root)
