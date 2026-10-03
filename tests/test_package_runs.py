import unittest

from tools.package_runs import select, title, validate_run


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
