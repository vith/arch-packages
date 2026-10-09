import unittest
from unittest.mock import patch
from pathlib import Path
import json
import tempfile
import tarfile

from tools.package_runs import select, title, validate_run, prepare, collect


class IndependentPackageRuns(unittest.TestCase):
    def setUp(self):
        self.plan = {'head': 'a' * 40, 'run_id': '123', 'run_attempt': '1', 'packages': [
            {'pkgbase': 'first', 'reuse': False},
            {'pkgbase': 'second', 'reuse': False},
            {'pkgbase': 'unchanged', 'reuse': True},
        ]}

    def test_exactly_one_package_per_run(self):
        selected = select(self.plan, 'first')
        self.assertEqual([package['pkgbase'] for package in selected['packages']], ['first'])
        self.assertEqual(len(self.plan['packages']), 3)

    def test_unchanged_or_unknown_package_is_not_built(self):
        for name in ('unchanged', 'unknown'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                select(self.plan, name)

    def test_duplicate_package_is_rejected(self):
        self.plan['packages'].append(self.plan['packages'][0])
        with self.assertRaises(ValueError):
            select(self.plan, 'first')

    def test_candidate_run_uses_trusted_base_not_candidate_head(self):
        plan = {**self.plan, 'base': 'b'*40}
        record = {'path': '.github/workflows/build-package.yml', 'head_sha': plan['base'],
                  'head_branch': 'main', 'event': 'workflow_dispatch', 'conclusion': 'success',
                  'display_title': title(plan, 'first')}
        validate_run(record, plan, 'first')
        with self.assertRaises(ValueError):
            validate_run({**record, 'head_sha': plan['head']}, plan, 'first')

    def candidate_input(self, directory, changed_identity=None):
        bundle = {'schema': 1, 'repository': 'vith/arch-packages',
                  'base': 'b' * 40, 'head': 'c' * 40, 'run_id': '123',
                  'run_attempt': '1', 'image': 'image@sha256:123',
                  'harness_sha': 'd' * 64, 'recipe_pins': {'first': 'c' * 40},
                  'previous_recipe_pins': {'first': 'e' * 40},
                  'packages': [{'pkgbase': 'first', 'recipe_dir': 'recipes/first',
                                'recipe_commit': 'c' * 40}]}
        plan = {**bundle, 'kind': 'recipe', 'pr_number': 7,
                'recipe_base': 'e' * 40, 'recipe_tree': 'f' * 40}
        (directory / 'candidate.json').write_text(json.dumps(plan))
        source = directory / 'source'
        recipe = source / 'recipes/first'
        recipe.mkdir(parents=True)
        (recipe / 'PKGBUILD').write_text('pkgname=first\npkgver=2\n')
        (source / 'bundle.json').write_text(json.dumps({**bundle, **(changed_identity or {})}))
        with tarfile.open(directory / 'bundle.tar', 'w') as archive:
            archive.add(source, arcname='bundle', recursive=True)
        return plan

    def test_recipe_candidate_prepares_frozen_head_with_trusted_control_base(self):
        with tempfile.TemporaryDirectory() as session:
            directory = Path(session)
            plan = self.candidate_input(directory)
            parent = {'path': '.github/workflows/candidate.yml', 'head_sha': plan['base'],
                      'head_branch': 'main', 'run_attempt': 1}
            with patch('tools.package_runs.github_api.api', return_value=parent), patch(
                    'tools.package_runs.subprocess.check_output', return_value=plan['base'] + '\n'):
                prepare(directory, 'first', '123', '1', kind='candidate')
            prepared = json.loads((directory / 'input/bundle.json').read_text())
            self.assertEqual((prepared['base'], prepared['head']), (plan['base'], plan['head']))
            self.assertEqual((directory / 'input/recipes/first/PKGBUILD').read_text(),
                             'pkgname=first\npkgver=2\n')

    def test_candidate_bundle_cannot_substitute_base_or_recipe_head(self):
        for key in ('base', 'head', 'harness_sha', 'image', 'recipe_pins', 'previous_recipe_pins'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as session:
                directory = Path(session)
                plan = self.candidate_input(directory, {key: 'substituted'})
                parent = {'path': '.github/workflows/candidate.yml', 'head_sha': plan['base'],
                          'head_branch': 'main', 'run_attempt': 1}
                with patch('tools.package_runs.github_api.api', return_value=parent), patch(
                        'tools.package_runs.subprocess.check_output', return_value=plan['base'] + '\n'):
                    with self.assertRaises(ValueError):
                        prepare(directory, 'first', '123', '1', kind='candidate')

    def test_candidate_receipt_preserves_exact_child_attempt(self):
        with tempfile.TemporaryDirectory() as session:
            directory = Path(session)
            plan = {**self.plan, 'base': 'b' * 40,
                    'packages': [{'pkgbase': 'first', 'reuse': False}]}
            (directory / 'candidate.json').write_text(json.dumps(plan))
            receipt = directory / 'native-evidence.json'
            receipt.write_text(json.dumps({'packages': [{'pkgbase': 'first', 'files': []}]}))
            artifact = directory / 'download-first'
            artifact.mkdir()
            with tarfile.open(artifact / 'unsigned.tar', 'w') as archive:
                archive.add(receipt, arcname='native-evidence.json')
            child = {'id': 987, 'run_attempt': 2, 'path': '.github/workflows/build-package.yml',
                     'head_sha': plan['base'], 'head_branch': 'main', 'event': 'workflow_dispatch',
                     'display_title': title(plan, 'first'), 'status': 'completed',
                     'conclusion': 'success', 'html_url': 'https://example.invalid/runs/987'}
            with patch('tools.package_runs.github_api.api', side_effect=[None, {'workflow_runs': [child]}]), patch(
                    'tools.package_runs.subprocess.run'), patch(
                    'tools.package_runs.update.validate_build'), patch.dict(
                    'os.environ', {'GITHUB_TOKEN': 'test'}):
                collect(directory, kind='candidate')
            child['run_attempt'] = 3
            evidence = json.loads((directory / 'unsigned/native-evidence.json').read_text())
            self.assertEqual(evidence['package_runs'],
                             [{'pkgbase': 'first', 'run_id': '987', 'run_attempt': '2'}])
            self.assertEqual((evidence['base'], evidence['head']), (plan['base'], plan['head']))

    def test_only_successful_exact_workflow_is_accepted(self):
        record = {'path': '.github/workflows/build-package.yml', 'head_sha': self.plan['head'],
                  'head_branch': 'main', 'event': 'workflow_dispatch', 'conclusion': 'success',
                  'display_title': title(self.plan, 'first')}
        validate_run(record, self.plan, 'first')
        for key, value in {'path': '.github/workflows/candidate.yml', 'head_sha': 'b' * 40,
                           'head_branch': 'other', 'event': 'pull_request', 'conclusion': 'failure',
                           'display_title': title(self.plan, 'second')}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_run({**record, key: value}, self.plan, 'first')
