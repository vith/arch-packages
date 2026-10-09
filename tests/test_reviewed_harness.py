"""Real API identities, not artifact assertions, authorize the repair harness."""
import copy
import base64
from contextlib import ExitStack
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import github_api, package_runs, recipe_gate, recipes, reviewed_harness, update


def protected_workflows(control=None):
    control = control or reviewed_harness.CONTROL
    result = {}
    for filename, job, name, condition in (
            ('reviewed-harness.yml', 'reviewed-harness', 'Protected reviewed harness',
             "github.ref == 'refs/heads/repair/frozen-git'"),
            ('build-package.yml', 'reviewed-package', 'Protected reviewed package',
             "github.ref == 'refs/heads/repair/frozen-git' && inputs.kind == 'reviewed-harness'")):
        run_name = ('Reviewed frozen Git / C ' + control + ' / H ${{ github.sha }}'
                    if filename == 'reviewed-harness.yml' else
                    'Build ${{ inputs.package }} / ${{ inputs.publication_run }}.${{ inputs.publication_attempt }}')
        trigger = ('on:\n  push:\n    branches: [repair/frozen-git]\n'
                   if filename == 'reviewed-harness.yml' else 'on:\n  workflow_dispatch:\n')
        text = (f'name: Fixture\nrun-name: {run_name}\n{trigger}jobs:\n'
                f'  {job}:\n    name: {name}\n    if: {condition}\n'
                '    environment: code-review\n    runs-on: ubuntu-latest\n    steps:\n'
                '      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n'
                '        with:\n          ref: ${{ github.sha }}\n          persist-credentials: false\n'
                '      - run: echo protected\n')
        if filename == 'build-package.yml':
            text += ("  package:\n    if: github.ref == 'refs/heads/main' && inputs.kind != 'reviewed-harness'\n"
                     "    runs-on: ubuntu-latest\n    steps:\n      - run: echo ordinary\n")
        result[filename] = text.encode()
    return result


class GitInputs:
    """Serve real immutable Git trees and archives at the network boundary."""
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.git('init', '--quiet')
        self.env = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                    'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
                    'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z'}
        self.pins = {}
        policies = []
        files = {}
        modules = []
        for name in sorted(reviewed_harness.PACKAGES):
            version = '1.0.vith.r1.g0123456789ab' if name == 'oh-my-pi-vith-git' else '1.0'
            metadata = f'pkgbase = {name}\npkgver = {version}\npkgrel = 1\narch = x86_64\npkgname = {name}\n'
            recipe = {'.SRCINFO': metadata.encode(), 'PKGBUILD': f'pkgname={name}\npkgver={version}\npkgrel=1\n'.encode()}
            self.pins[name] = self.commit(recipe)
            policies.append({'pkgbase': name, 'outputs': [{'name': name, 'arch': 'x86_64'}], 'sources': []})
            files[f'inputs/{name}.json'] = github_api.canonical({'schema': 1, 'version': version + '-1', 'sources': []})
            modules.append(f'[submodule "recipes/{name}"]\n\tpath = recipes/{name}\n\turl = https://github.com/vith/arch-packages.git\n\tbranch = pkg/{name}\n')
        files['packages.json'] = github_api.canonical({'schema': 1, 'packages': policies})
        files['.gitmodules'] = ''.join(modules).encode()
        files['build-image.txt'] = ('ghcr.io/archlinux/archlinux@sha256:' + 'd' * 64 + '\n').encode()
        for name in ('build.sh', 'native.py', 'sources.py', 'recipe_gate.py', 'github_api.py'):
            files['tools/' + name] = b'# accepted native fixture\n'
        for name in ('tools/update.py', 'tests/test_sources.py'):
            files[name] = b'# accepted source fixture\n'
        self.control = self.commit(files, self.pins)
        repaired = {**files, **{name: b'# reviewed frozen Git repair\n' for name in reviewed_harness.REPAIR_BLOBS}}
        repaired.update({'.github/workflows/' + name: body for name, body in protected_workflows(self.control).items()})
        self.head = self.commit(repaired, self.pins, self.control)
        self.git('update-ref', 'refs/heads/repair/frozen-git', self.head)
        self.git('symbolic-ref', 'HEAD', 'refs/heads/repair/frozen-git')
        self.git('reset', '--hard', '--quiet', self.head)

    def git(self, *arguments, input=None):
        return subprocess.check_output(['git', '-C', str(self.root), *arguments], input=input,
                                       env=getattr(self, 'env', None)).decode().strip()

    def commit(self, files, pins=None, parent=None):
        self.git('read-tree', '--empty')
        for name, body in files.items():
            sha = self.git('hash-object', '-w', '--stdin', input=body)
            self.git('update-index', '--add', '--cacheinfo', f'100644,{sha},{name}')
        for name, sha in (pins or {}).items():
            self.git('update-index', '--add', '--cacheinfo', f'160000,{sha},recipes/{name}')
        tree = self.git('write-tree')
        args = ['commit-tree', tree, '-m', 'immutable fixture']
        if parent:
            args.extend(['-p', parent])
        return self.git(*args)

    def api(self, route, method='GET', data=None):
        if method != 'GET':
            raise AssertionError('fixture is read-only')
        route = route.split('?')[0]
        if '/git/commits/' in route:
            sha = route.rsplit('/', 1)[-1]
            return {'sha': sha, 'tree': {'sha': self.git('rev-parse', sha + '^{tree}')}}
        if '/git/trees/' in route:
            sha = route.rsplit('/', 1)[-1]
            entries = []
            for line in self.git('ls-tree', '-r', sha).splitlines():
                identity, name = line.split('\t')
                mode, kind, object_sha = identity.split()
                entries.append({'path': name, 'mode': mode, 'type': kind, 'sha': object_sha})
            return {'truncated': False, 'tree': entries}
        raise AssertionError('unexpected Git fixture route: ' + route)

    def download(self, url, destination, maximum=None):
        sha = url.rsplit('/', 1)[-1]
        data = subprocess.check_output(['git', '-C', str(self.root), 'archive',
                                        '--format=tar', '--prefix=fixture/', sha])
        if maximum is not None and len(data) > maximum:
            raise AssertionError('fixture exceeds download limit')
        Path(destination).write_bytes(data)
        return Path(destination)


class ReviewedAuthority(unittest.TestCase):
    def setUp(self):
        self.control = reviewed_harness.CONTROL
        self.head = 'b' * 40
        self.digest = 'c' * 64
        self.record = {
            'schema': 1, 'repository': 'vith/arch-packages',
            'kind': 'reviewed-harness', 'base': self.control, 'head': self.head,
            'control_sha': self.control, 'tools_sha': self.head,
            'tools_ref': 'repair/frozen-git', 'pr_number': 15,
            'run_id': '100', 'run_attempt': '2', 'harness_sha': self.digest,
            'image': 'ghcr.io/archlinux/archlinux@sha256:' + 'd' * 64,
            'recipe_pins': {name: 'e' * 40 for name in reviewed_harness.PACKAGES},
            'previous_recipe_pins': {name: 'e' * 40 for name in reviewed_harness.PACKAGES},
            'packages': [{'pkgbase': name} for name in sorted(reviewed_harness.PACKAGES)],
            'review': {'run_id': '100', 'run_attempt': '2', 'job_id': 901,
                       'environment': 'code-review', 'environment_id': 23508833820,
                       'control_sha': self.control, 'tools_sha': self.head,
                       'harness_sha': self.digest},
        }
        self.responses = {
            '/git/ref/heads/main': {'object': {'sha': self.control}},
            '/git/ref/heads/repair/frozen-git': {'object': {'sha': self.head}},
            '/pulls/15': {'number': 15, 'state': 'open',
                         'base': {'ref': 'main', 'sha': self.control, 'repo': {'full_name': 'vith/arch-packages'}},
                         'head': {'ref': 'repair/frozen-git', 'sha': self.head, 'repo': {'full_name': 'vith/arch-packages'}}},
            '/compare/' + self.control + '...' + self.head: {
                'merge_base_commit': {'sha': self.control},
                'files': [{'filename': name, 'sha': sha} for name, sha in reviewed_harness.REPAIR_BLOBS.items()]},
            '/environments/code-review': {
                'id': 23508833820, 'name': 'code-review', 'can_admins_bypass': False,
                'protection_rules': [{'type': 'required_reviewers', 'reviewers': [
                    {'type': 'User', 'reviewer': {'id': 3265539, 'login': 'vith'}}]}],
            },
            '/actions/runs/100/approvals': [
                {'state': 'approved', 'user': {'id': 3265539, 'login': 'vith'},
                 'environments': [{'id': 23508833820, 'name': 'code-review'}]}],
        }
        for filename, content in protected_workflows().items():
            self.responses['/contents/.github/workflows/' + filename] = {
                'encoding': 'base64', 'size': len(content), 'content': base64.b64encode(content).decode()}
        parent = self.run_record(100, 2, '.github/workflows/reviewed-harness.yml',
                          f'Reviewed frozen Git / C {self.control} / H {self.head}', 'push', 'in_progress')
        self.responses['/actions/runs/100'] = copy.deepcopy(parent)
        self.responses['/actions/runs/100/attempts/2'] = parent
        self.responses['/actions/runs/100/attempts/2/jobs'] = {
            'total_count': 1, 'jobs': [self.job(901, 100, 2, 'Protected reviewed harness', 'in_progress')]}

    def run_record(self, run_id, attempt, path, title, event='workflow_dispatch', status='completed'):
        return {'id': run_id, 'run_attempt': attempt, 'head_sha': self.head,
                'head_branch': 'repair/frozen-git', 'path': path,
                'display_title': title, 'event': event, 'status': status,
                'conclusion': 'success' if status == 'completed' else None}

    def job(self, job_id, run_id, attempt, name, status='completed'):
        return {'id': job_id, 'run_id': run_id, 'run_attempt': attempt,
                'head_sha': self.head, 'name': name, 'status': status,
                'conclusion': 'success' if status == 'completed' else None,
                'started_at': '2026-10-09T12:00:00Z',
                'completed_at': '2026-10-09T12:05:00Z' if status == 'completed' else None,
                'labels': ['ubuntu-latest'], 'runner_name': 'GitHub Actions fixture'}

    def api(self, route, method='GET', data=None):
        self.assertEqual(method, 'GET', 'authority checking must not mutate GitHub')
        route = '/' + route.lstrip('/')
        prefix = '/repos/vith/arch-packages'
        self.assertTrue(route.startswith(prefix), route)
        route = route[len(prefix):]
        if route.startswith('/contents/'):
            self.assertEqual(route.split('?', 1)[1], 'ref=' + self.head,
                             'workflow protection must resolve immutable H, never a moving ref')
        route = route.split('?')[0]
        if route not in self.responses:
            self.fail('unexpected authority API route: ' + route)
        return copy.deepcopy(self.responses[route])

    def authorize(self, record=None):
        with patch.object(reviewed_harness, 'api', self.api):
            return reviewed_harness.verify_authority(record or self.record)

    def test_approval_and_job_name_cannot_authorize_unprotected_h_workflow(self):
        for filename in protected_workflows():
            route = '/contents/.github/workflows/' + filename
            original = copy.deepcopy(self.responses[route])
            content = protected_workflows()[filename].replace(b'    environment: code-review\n', b'')
            self.responses[route].update(size=len(content), content=base64.b64encode(content).decode())
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.authorize()
            self.responses[route] = original

    def test_multiline_quoted_fake_gate_cannot_transfer_other_job_approval(self):
        import yaml
        route = '/contents/.github/workflows/reviewed-harness.yml'
        real = protected_workflows()['reviewed-harness.yml'].decode()
        real = real.removeprefix('name: Fixture\n')
        real = real.replace('    environment: code-review\n', '    environment: unprotected\n')
        real += ('  separate-review:\n    name: Separate code review\n'
                 '    environment: code-review\n    runs-on: ubuntu-latest\n'
                 '    steps:\n      - run: echo separately approved\n')
        fake = ('name: "jobs:\n  reviewed-harness:\n'
                '    name: Protected reviewed harness\n'
                "    if: github.ref == 'refs/heads/repair/frozen-git'\n"
                '    environment: code-review\n'
                '    runs-on: ubuntu-latest\n  "\n')
        content = (fake + real).encode()
        yaml.safe_load(content)  # Prove refusal is semantic, not malformed YAML.
        self.responses[route].update(size=len(content), content=base64.b64encode(content).decode())
        self.responses['/actions/runs/100/attempts/2/jobs']['jobs'].append(
            self.job(902, 100, 2, 'Separate code review'))
        self.responses['/actions/runs/100/attempts/2/jobs']['total_count'] = 2
        # Approval history is deliberately genuine and run-scoped. It cannot
        # protect the separate selected job that actually executes H.
        with self.assertRaises(ValueError):
            self.authorize()

    def test_fake_yaml_job_duplicate_protection_and_moving_checkout_cannot_grant_authority(self):
        route = '/contents/.github/workflows/reviewed-harness.yml'
        original = copy.deepcopy(self.responses[route])
        content = protected_workflows()['reviewed-harness.yml']
        attacks = [
            b'name: forged\nnotes: |\n' + b'\n'.join(b'  ' + line for line in content.splitlines()) + b'\n',
            content.replace(b'    environment: code-review\n',
                            b'    environment: code-review\n    environment: unprotected\n'),
            content.replace(b'ref: ${{ github.sha }}', b'ref: main'),
            content.replace(b'persist-credentials: false', b'persist-credentials: true'),
            content.replace(b' / H ${{ github.sha }}', b' / H stale'),
            content.replace(b'    environment: code-review\n', b'    environment: &gate code-review\n')
                   + b'  unprotected:\n    environment: *gate\n',
        ]
        for index, attack in enumerate(attacks):
            self.responses[route].update(size=len(attack), content=base64.b64encode(attack).decode())
            with self.subTest(attack=index), self.assertRaises(ValueError):
                self.authorize()
        self.responses[route] = original

    def test_different_source_repair_blob_cannot_claim_reviewed_repair(self):
        route = '/compare/' + self.control + '...' + self.head
        self.responses[route]['files'][0]['sha'] = 'f' * 40
        with self.assertRaises(ValueError):
            self.authorize()

    def test_current_protected_parent_can_build_before_own_completion(self):
        self.authorize()

    def test_current_main_pr_and_exact_same_repository_head_are_required(self):
        mutations = [('/git/ref/heads/main', ('object', 'sha'), 'd' * 40),
                     ('/git/ref/heads/repair/frozen-git', ('object', 'sha'), 'd' * 40),
                     ('/pulls/15', ('base', 'sha'), 'd' * 40),
                     ('/pulls/15', ('base', 'ref'), 'other'),
                     ('/pulls/15', ('head', 'sha'), 'd' * 40),
                     ('/pulls/15', ('head', 'ref'), 'feature/unreviewed'),
                     ('/pulls/15', ('head', 'repo', 'full_name'), 'attacker/arch-packages'),
                     ('/pulls/15', ('state',), 'closed')]
        for route, keys, value in mutations:
            original = copy.deepcopy(self.responses[route])
            node = self.responses[route]
            for key in keys[:-1]:
                node = node[key]
            node[keys[-1]] = value
            with self.subTest(route=route, keys=keys), self.assertRaises(ValueError):
                self.authorize()
            self.responses[route] = original

    def test_artifact_identity_cannot_relabel_control_tools_or_review(self):
        for field, value in [('base', self.head), ('head', self.control),
                             ('control_sha', self.head), ('tools_sha', self.control),
                             ('tools_ref', 'main'), ('kind', 'candidate')]:
            record = copy.deepcopy(self.record)
            record[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.authorize(record)
        for field, value in [('control_sha', self.head), ('tools_sha', self.control),
                             ('harness_sha', 'e' * 64), ('run_id', '99'),
                             ('run_attempt', '1'), ('job_id', 902),
                             ('environment_id', 23327947729), ('environment', 'recipe-review')]:
            record = copy.deepcopy(self.record)
            record['review'][field] = value
            with self.subTest(review_field=field), self.assertRaises(ValueError):
                self.authorize(record)

    def test_approved_history_without_current_protected_attempt_does_not_authorize_rerun(self):
        route = '/actions/runs/100/attempts/2/jobs'
        for change in ({'run_attempt': 1}, {'run_id': 99}, {'head_sha': 'd' * 40},
                       {'name': 'Unprotected build'}, {'status': 'queued', 'started_at': None},
                       {'status': 'completed', 'conclusion': 'failure'}):
            original = copy.deepcopy(self.responses[route])
            self.responses[route]['jobs'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.authorize()
            self.responses[route] = original
        self.responses[route] = {'total_count': 0, 'jobs': []}
        with self.assertRaises(ValueError):
            self.authorize()

    def test_real_environment_protection_and_human_approval_are_required(self):
        env_route = '/environments/code-review'
        original = copy.deepcopy(self.responses[env_route])
        for change in ({'can_admins_bypass': True}, {'id': 23327947729},
                       {'protection_rules': []},
                       {'protection_rules': [{'type': 'required_reviewers', 'reviewers': [
                           {'type': 'User', 'reviewer': {'id': 123, 'login': 'bot'}}]}]}):
            self.responses[env_route] = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.authorize()
        self.responses[env_route] = original
        route = '/actions/runs/100/approvals'
        original = copy.deepcopy(self.responses[route])
        for approvals in ([], [{**original[0], 'state': 'rejected'}],
                          [{**original[0], 'user': {'id': 123, 'login': 'bot'}}],
                          [{**original[0], 'environments': [{'id': 23327947729, 'name': 'recipe-review'}]}]):
            self.responses[route] = approvals
            with self.subTest(approvals=approvals), self.assertRaises(ValueError):
                self.authorize()

    def children(self):
        evidence = {**copy.deepcopy(self.record), 'package_runs': []}
        for index, package in enumerate(self.record['packages']):
            run_id = 200 + index
            title = f"Build {package['pkgbase']} / 100.2"
            child = self.run_record(run_id, 1, '.github/workflows/build-package.yml', title)
            self.responses[f'/actions/runs/{run_id}'] = copy.deepcopy(child)
            self.responses[f'/actions/runs/{run_id}/attempts/1'] = child
            self.responses[f'/actions/runs/{run_id}/attempts/1/jobs'] = {
                'total_count': 1, 'jobs': [self.job(1000 + index, run_id, 1, 'Protected reviewed package')]}
            self.responses[f'/actions/runs/{run_id}/approvals'] = copy.deepcopy(self.responses['/actions/runs/100/approvals'])
            evidence['package_runs'].append({'pkgbase': package['pkgbase'], 'run_id': str(run_id), 'run_attempt': '1'})
        return evidence

    def native(self, evidence):
        with patch.object(reviewed_harness, 'api', self.api):
            reviewed_harness.verify_native_authority(self.record, evidence)

    def test_seven_distinct_successful_protected_children_are_accepted(self):
        self.native(self.children())

    def test_missing_duplicate_or_shared_child_runs_cannot_cover_seven_packages(self):
        for case in ('missing', 'duplicate-package', 'shared-run'):
            evidence = self.children()
            if case == 'missing':
                evidence['package_runs'].pop()
            elif case == 'duplicate-package':
                evidence['package_runs'][-1] = copy.deepcopy(evidence['package_runs'][0])
            else:
                evidence['package_runs'][-1]['run_id'] = evidence['package_runs'][0]['run_id']
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.native(evidence)

    def test_parent_approval_cannot_substitute_for_child_gate_or_old_child_attempt(self):
        evidence = self.children()
        self.responses['/actions/runs/200/approvals'] = []
        with self.assertRaises(ValueError):
            self.native(evidence)
        evidence = self.children()
        self.responses['/actions/runs/200']['run_attempt'] = 2
        with self.assertRaises(ValueError):
            self.native(evidence)

    def test_child_protected_job_must_itself_complete_successfully(self):
        for change in ({'status': 'in_progress', 'conclusion': None, 'completed_at': None},
                       {'status': 'completed', 'conclusion': 'failure'},
                       {'runner_name': 'self-hosted'}, {'labels': ['self-hosted', 'x86_64']},
                       {'name': 'Unprotected ordinary package'}):
            evidence = self.children()
            self.responses['/actions/runs/200/attempts/1/jobs']['jobs'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.native(evidence)

    def test_native_global_identity_cannot_disagree_with_plan(self):
        for field in reviewed_harness.FIELDS:
            evidence = self.children()
            evidence[field] = None
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.native(evidence)

    def test_changed_package_inputs_and_renamed_paths_are_not_allowed_repairs(self):
        route = '/compare/' + self.control + '...' + self.head
        for filename, previous in [('packages.json', None), ('build-image.txt', None),
                                   ('inputs/carapace.json', None), ('recipes/carapace', None),
                                   ('tools/sources.py', 'inputs/carapace.json')]:
            change = {'filename': filename}
            if previous:
                change['previous_filename'] = previous
            self.responses[route]['files'] = [change]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.authorize()

    def test_latest_rerun_invalidates_old_exact_attempt_and_approval(self):
        self.responses['/actions/runs/100']['run_attempt'] = 3
        with self.assertRaises(ValueError):
            self.authorize()

    def test_child_sha_ref_event_title_attempt_and_success_are_independently_checked(self):
        for field, value in [('head_sha', self.control), ('head_branch', 'main'),
                             ('event', 'push'), ('display_title', 'Build forged / 100.1'),
                             ('run_attempt', 2), ('conclusion', 'failure'),
                             ('status', 'in_progress'), ('path', '.github/workflows/publish.yml')]:
            evidence = self.children()
            self.responses['/actions/runs/200/attempts/1'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.native(evidence)


class ReviewedInputBoundaries(unittest.TestCase):
    def setUp(self):
        state = Path.home() / '.local/state/omp/work/reviewed-harness-tests'
        state.mkdir(parents=True, exist_ok=True)
        self.work = tempfile.TemporaryDirectory(dir=state)
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.git = GitInputs(self.root / 'repository')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(reviewed_harness, 'CONTROL', self.git.control))
        self.stack.enter_context(patch.object(reviewed_harness, 'ROOT', self.git.root))
        self.stack.enter_context(patch.object(reviewed_harness, 'REPAIR_BLOBS', {
            name: self.git.git('rev-parse', self.git.head + ':' + name)
            for name in reviewed_harness.REPAIR_BLOBS}))
        self.stack.enter_context(patch.dict(os.environ, {'GITHUB_REPOSITORY': 'vith/arch-packages'}))
        self.stack.enter_context(patch.object(update, 'download', self.git.download))
        self.stack.enter_context(patch.object(recipes, 'download', self.git.download))
        self.stack.enter_context(patch.object(recipes, 'api', self.git.api))
        self.authority = ReviewedAuthority('test_current_protected_parent_can_build_before_own_completion')
        self.authority.setUp()
        self.authority.head = self.git.head
        self.authority.digest = recipe_gate.harness_digest(self.git.root)
        record = self.authority.record
        old_head = record['head']
        for key in ('head', 'tools_sha'):
            record[key] = self.git.head
        record['harness_sha'] = self.authority.digest
        record['review'].update(tools_sha=self.git.head, harness_sha=self.authority.digest)
        self.authority.responses['/git/ref/heads/repair/frozen-git']['object']['sha'] = self.git.head
        self.authority.responses['/pulls/15']['head']['sha'] = self.git.head
        self.authority.responses.pop('/compare/' + self.git.control + '...' + old_head)
        self.authority.responses['/compare/' + self.git.control + '...' + self.git.head] = {
            'merge_base_commit': {'sha': self.git.control},
            'files': [{'filename': name, 'sha': self.git.git('rev-parse', self.git.head + ':' + name)}
                      for name in self.git.git('diff', '--name-only', self.git.control, self.git.head).splitlines()]}
        for route in ('/actions/runs/100', '/actions/runs/100/attempts/2'):
            self.authority.responses[route]['head_sha'] = self.git.head
            self.authority.responses[route]['display_title'] = reviewed_harness.parent_title(self.git.head)
        self.authority.responses['/actions/runs/100/attempts/2/jobs']['jobs'][0]['head_sha'] = self.git.head
        self.stack.enter_context(patch.object(reviewed_harness, 'api', self.authority.api))
        baseline = self.root / 'baseline'
        baseline.mkdir()
        self.accepted, inputs = reviewed_harness.accepted_inputs(record, baseline)
        record.update(inputs)
        self.record = record

    def test_accepted_recipe_lock_policy_image_use_repaired_native_digest(self):
        self.assertEqual(self.record['recipe_pins'], self.git.pins)
        self.assertEqual(self.record['previous_recipe_pins'], self.git.pins)
        package = self.record['packages'][0]
        recipe = self.accepted / package['recipe_dir']
        accepted_harness = recipe_gate.harness_digest(self.accepted)
        self.assertNotEqual(accepted_harness, self.record['harness_sha'])
        self.assertEqual(package['input_digest'], recipe_gate.input_digest(
            recipe, package['lock'], package['policy'], self.record['image'], self.record['harness_sha']))
        self.assertNotEqual(package['input_digest'], recipe_gate.input_digest(
            recipe, package['lock'], package['policy'], self.record['image'], accepted_harness))
        target = self.root / 'valid-reconstruction'
        target.mkdir()
        reviewed_harness.reconstruct(self.record, target)

    def test_transport_cannot_replace_selected_c_inputs_or_h_native_digest(self):
        mutations = [('recipe_commit', 'e' * 40), ('previous_recipe_commit', 'e' * 40),
                     ('lock', {'schema': 1, 'version': '9-1', 'sources': []}),
                     ('policy', {'pkgbase': 'forged', 'sources': [], 'outputs': []}),
                     ('input_digest', 'e' * 64), ('expected_srcinfo', 'forged'),
                     ('tree_sha', 'e' * 64)]
        for index, (field, value) in enumerate(mutations):
            record = copy.deepcopy(self.record)
            record['packages'][0][field] = value
            target = self.root / f'bad-package-{index}'
            target.mkdir()
            with self.subTest(field=field), self.assertRaises(ValueError):
                reviewed_harness.reconstruct(record, target)
        for index, (field, value) in enumerate([
                ('image', 'ghcr.io/archlinux/archlinux@sha256:' + 'e' * 64),
                ('recipe_pins', {**self.git.pins, 'carapace': 'e' * 40}),
                ('previous_recipe_pins', {}),
                ('harness_sha', recipe_gate.harness_digest(self.accepted))]):
            record = copy.deepcopy(self.record)
            record[field] = value
            target = self.root / f'bad-global-{index}'
            target.mkdir()
            with self.subTest(field=field), self.assertRaises(ValueError):
                reviewed_harness.reconstruct(record, target)

    def child_transport(self, directory, bundle):
        directory.mkdir()
        (directory / 'candidate.json').write_bytes(github_api.canonical(self.record))
        with tarfile.open(directory / 'bundle.tar', 'w') as archive:
            body = github_api.canonical(bundle)
            member = tarfile.TarInfo('bundle/bundle.json')
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        package = self.record['packages'][0]['pkgbase']
        self.authority.children()
        return patch.dict(os.environ, {'GITHUB_RUN_ID': '200', 'GITHUB_RUN_ATTEMPT': '1', 'PACKAGE': package}), package

    def test_child_rejects_entire_bundle_plan_identity_disagreement(self):
        for index, field in enumerate(reviewed_harness.FIELDS + ('packages',)):
            bundle = copy.deepcopy(self.record)
            bundle[field] = None
            directory = self.root / f'transport-{index}'
            context, name = self.child_transport(directory, bundle)
            with self.subTest(field=field), context, self.assertRaises(ValueError):
                package_runs.prepare(directory, name, '100', '2', kind='reviewed-harness')

    def test_child_uses_independently_exported_c_recipe_and_exact_h_checkout(self):
        directory = self.root / 'child'
        context, name = self.child_transport(directory, self.record)
        with context:
            package_runs.prepare(directory, name, '100', '2', kind='reviewed-harness')
        selected = json.loads((directory / 'input/bundle.json').read_text())
        self.assertEqual(selected['packages'], [self.record['packages'][0]])
        self.assertEqual(recipe_gate.tree_manifest(directory / 'input/recipes' / name),
                         recipe_gate.tree_manifest(self.accepted / 'recipes' / name))
        directory = self.root / 'wrong-checkout'
        context, name = self.child_transport(directory, self.record)
        self.git.git('update-ref', 'refs/heads/repair/frozen-git', self.git.control)
        with context, self.assertRaises(ValueError):
            package_runs.prepare(directory, name, '100', '2', kind='reviewed-harness')

    def output(self):
        output = self.root / 'unsigned'
        output.mkdir()
        evidence = self.authority.children()
        evidence['packages'] = []
        for package in self.record['packages']:
            name = package['pkgbase']
            version = package['lock']['version']
            filename = f'{name}-{version}-x86_64.pkg.tar.zst'
            path = output / filename
            payload = f'pkgname = {name}\npkgver = {version}\narch = x86_64\n'.encode()
            with tarfile.open(path, 'w') as archive:
                member = tarfile.TarInfo('.PKGINFO')
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            receipt = {key: package[key] for key in ('pkgbase', 'recipe_commit', 'input_digest', 'tree_sha')}
            receipt.update(source_lock=package['lock'], metadata={**package['metadata'], 'srcinfo': package['expected_srcinfo']},
                           **{key: self.record[key] for key in ('run_id', 'run_attempt', 'image', 'harness_sha')})
            receipt['files'] = [{'filename': filename, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'name': name, 'version': version, 'arch': 'x86_64'}]
            if name == 'oh-my-pi-vith-git':
                runtime = '1.0+vith-fork.1.0123456789ab'
                receipt['runtime'] = {'runtime_identity': runtime, 'cli_version': 'omp/' + runtime,
                                      'dynamic': 'libpipewire-0.3.so.0', 'addon_sha256': 'a' * 64, 'help': 'omp --help'}
            evidence['packages'].append(receipt)
        (output / 'native-evidence.json').write_bytes(github_api.canonical(evidence))
        return output, evidence

    def test_real_minimal_archives_and_complete_receipts_validate_without_signer(self):
        output, _ = self.output()
        reviewed_harness.validate_outputs(self.record, output)

    def test_archive_metadata_is_checked_even_when_attacker_updates_its_hash(self):
        output, evidence = self.output()
        file = evidence['packages'][0]['files'][0]
        path = output / file['filename']
        payload = f"pkgname = forged\npkgver = {file['version']}\narch = x86_64\n".encode()
        with tarfile.open(path, 'w') as archive:
            member = tarfile.TarInfo('.PKGINFO')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        file['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (output / 'native-evidence.json').write_bytes(github_api.canonical(evidence))
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)

    def test_extra_files_and_symlinked_unsigned_archives_are_rejected(self):
        output, evidence = self.output()
        extra = output / 'unreported'
        extra.write_bytes(b'not enrolled')
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)
        extra.unlink()
        path = output / evidence['packages'][0]['files'][0]['filename']
        external = self.root / 'external-archive'
        path.rename(external)
        path.symlink_to(external)
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)

    def test_metadata_hash_runtime_and_parent_receipt_divergence_are_rejected(self):
        output, original = self.output()
        receipt_path = output / 'native-evidence.json'
        cases = [('input_digest', 'e' * 64), ('source_lock', {'schema': 1, 'version': '9-1', 'sources': []}),
                 ('run_attempt', '1'), ('harness_sha', 'e' * 64), ('tree_sha', 'e' * 64),
                 ('metadata', {**original['packages'][0]['metadata'], 'srcinfo': 'pkgbase = forged'})]
        for field, value in cases:
            evidence = copy.deepcopy(original)
            evidence['packages'][0][field] = value
            receipt_path.write_bytes(github_api.canonical(evidence))
            with self.subTest(field=field), self.assertRaises(ValueError):
                reviewed_harness.validate_outputs(self.record, output)
        evidence = copy.deepcopy(original)
        evidence['packages'][0]['files'][0]['sha256'] = 'e' * 64
        receipt_path.write_bytes(github_api.canonical(evidence))
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)
        evidence = copy.deepcopy(original)
        next(p for p in evidence['packages'] if p['pkgbase'] == 'oh-my-pi-vith-git')['runtime']['cli_version'] = 'omp/upstream'
        receipt_path.write_bytes(github_api.canonical(evidence))
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)
        receipt_path.write_bytes(github_api.canonical(original))
        path = output / original['packages'][0]['files'][0]['filename']
        path.write_bytes(path.read_bytes() + b'tampered archive')
        with self.assertRaises(ValueError):
            reviewed_harness.validate_outputs(self.record, output)
