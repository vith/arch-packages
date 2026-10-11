import copy
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tools import build_store

ROOT = Path.home() / '.local/state/omp/work/build-store-tests'


class DirectDurableStore(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.record = {'repository': 'vith/arch-packages', 'request_id': 'request-one', 'base': 'a' * 40,
                       'packages': [{'pkgbase': 'first', 'input_digest': 'd' * 64}]}
        self.producer = {'run_id': '77', 'run_attempt': '2', 'head_sha': 'a' * 40,
                         'path': build_store.WORKFLOW, 'pkgbase': 'first'}
        self.run = {'path': build_store.WORKFLOW, 'head_sha': 'a' * 40, 'head_branch': 'main',
                    'event': 'workflow_dispatch', 'display_title': 'Build first / request-one',
                    'run_attempt': 2, 'status': 'completed', 'conclusion': 'success'}
        self.descriptor = {'schema': 3, 'pkgbase': 'first', 'request_id': 'request-one',
                           'record': self.record, 'producer': self.producer, 'evidence': {'producer': self.producer}}

    def source_evidence(self, commit):
        from tools.recipe_gate import parse_srcinfo
        srcinfo = 'pkgbase = first\n\tpkgver = 1\n\tpkgrel = 1\n\tarch = x86_64\n\tsource = git+https://example.com/source.git\n\tsha256sums = SKIP\n\npkgname = first\n'
        source = {'id': 'source', 'kind': 'git', 'source': 'git+https://example.com/source.git',
                  'url': 'https://example.com/source.git', 'ref': 'refs/heads/main', 'commit': 'a' * 40}
        package = {**self.record['packages'][0], 'recipe_commit': 'b' * 40, 'tree_sha': 'e' * 64,
                   'expected_srcinfo': srcinfo, 'lock': {'schema': 1, 'version': '1-1', 'sources': [source]},
                   'policy': {'outputs': [{'name': 'first', 'arch': 'x86_64'}]}}
        record = {**self.record, 'head': 'b' * 40, 'image': 'official-image', 'packages': [package]}
        receipt = {'pkgbase': 'first', 'recipe_commit': package['recipe_commit'], 'tree_sha': package['tree_sha'],
                   'request_input_digest': package['input_digest'], 'input_digest': 'f' * 64, 'harness_sha': 'c' * 64,
                   'metadata': {**parse_srcinfo(srcinfo), 'srcinfo': srcinfo, 'dynamic_pkgver': False},
                   'source_lock': {'schema': 1, 'version': '1-1', 'sources': [{**source, 'commit': commit}]},
                   'files': [{'name': 'first', 'arch': 'x86_64', 'version': '1-1',
                              'filename': 'first-1-1-x86_64.pkg.tar.zst'}]}
        evidence = {**{key: record[key] for key in ('repository', 'base', 'head', 'request_id', 'image')},
                    'harness_sha': 'c' * 64, 'packages': [receipt]}
        return record, evidence

    def test_actual_source_evidence_accepts_sha256_git_commit(self):
        record, evidence = self.source_evidence('d' * 64)
        receipt = build_store.validate_metadata(record, evidence)
        self.assertEqual(receipt['source_lock']['sources'][0]['commit'], 'd' * 64)

    def test_actual_source_evidence_rejects_abbreviated_git_commit(self):
        record, evidence = self.source_evidence('d' * 12)
        with self.assertRaisesRegex(ValueError, 'source commit is not full'):
            build_store.validate_metadata(record, evidence)

    def test_whole_run_success_is_required_even_when_output_exists(self):
        for conclusion in ('failure', 'cancelled', 'timed_out', None):
            with self.subTest(conclusion=conclusion), patch.dict(os.environ, {'GITHUB_REPOSITORY': self.record['repository']}), patch(
                    'tools.build_store.github_api.api', return_value={**self.run, 'conclusion': conclusion}):
                with self.assertRaisesRegex(ValueError, 'entire original worker'):
                    build_store.producer_run(self.record, self.producer)

    def test_exact_attempt_and_request_title_are_required(self):
        for change in ({'run_attempt': 3}, {'display_title': 'Build first / another-request'}, {'head_branch': 'other'}):
            with self.subTest(change=change), patch.dict(os.environ, {'GITHUB_REPOSITORY': self.record['repository']}), patch(
                    'tools.build_store.github_api.api', return_value={**self.run, **change}):
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    build_store.producer_run(self.record, self.producer)

    def test_new_trusted_controller_can_produce_original_request(self):
        producer = {**self.producer, 'head_sha': 'c' * 40}
        run = {**self.run, 'head_sha': 'c' * 40}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': self.record['repository']}), patch(
                'tools.build_store.github_api.api', side_effect=[run, {'object': {'sha': 'e' * 40}}]), patch(
                'tools.build_store.recipes.is_ancestor', return_value=True):
            self.assertEqual(build_store.producer_run(self.record, producer), run)
        self.assertEqual(self.record['base'], 'a' * 40)

    def test_unprotected_controller_cannot_produce_request(self):
        producer = {**self.producer, 'head_sha': 'c' * 40}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': self.record['repository']}), patch(
                'tools.build_store.github_api.api', side_effect=[{**self.run, 'head_sha': 'c' * 40},
                                                               {'object': {'sha': 'e' * 40}}]), patch(
                'tools.build_store.recipes.is_ancestor', return_value=False):
            with self.assertRaisesRegex(ValueError, 'protected-main lineage'):
                build_store.producer_run(self.record, producer)

    def test_lookup_reads_only_exact_metadata(self):
        with patch('tools.build_store.recipe_state.load_optional', return_value=self.descriptor), patch(
                'tools.build_store.validate_metadata'), patch('tools.build_store.github_api.api', side_effect=AssertionError('network scan')), patch(
                'tools.build_store.download_asset', side_effect=AssertionError('package bytes')):
            self.assertEqual(build_store.lookup({'pkgbase': 'first', 'request_id': 'request-one'}), self.descriptor)

    def test_lookup_cannot_substitute_another_request(self):
        forged = {**self.descriptor, 'request_id': 'other'}
        with patch('tools.build_store.recipe_state.load_optional', return_value=forged):
            with self.assertRaisesRegex(ValueError, 'request differs'):
                build_store.lookup({'pkgbase': 'first', 'request_id': 'request-one'})

    def test_failed_producer_stops_materialization_before_fetch(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary, patch('tools.build_store.lookup'), patch(
                'tools.build_store.recipe_state.load', return_value=self.descriptor), patch(
                'tools.build_store.recipe_state.build_request', return_value=self.record), patch(
                'tools.build_store.producer_run', side_effect=ValueError('entire original worker did not succeed')), patch(
                'tools.build_store.fetch_original') as fetch:
            destination = Path(temporary) / 'output'
            with self.assertRaisesRegex(ValueError, 'entire original worker'):
                build_store.materialize(self.descriptor, destination)
            fetch.assert_not_called()
            self.assertFalse(destination.exists())

    def test_asset_redirect_does_not_forward_api_credentials(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            opener = Mock()
            headers = {'Location': 'https://release-assets.githubusercontent.com/original'}
            opener.open.side_effect = urllib.error.HTTPError('https://api.github.com/asset', 302, 'redirect', headers, None)
            destination = Path(temporary) / 'archive'
            with patch.dict(os.environ, {'GITHUB_TOKEN': 'private-api-token'}), patch(
                    'tools.build_store.urllib.request.build_opener', return_value=opener), patch(
                    'tools.build_store.github_api.download', return_value=destination) as public_download:
                self.assertEqual(build_store.download_api('repos/vith/arch-packages/releases/assets/1', destination, 100,
                                 accept='application/octet-stream'), destination)
            self.assertEqual(public_download.call_args.args, (headers['Location'], destination))
            self.assertEqual(public_download.call_args.kwargs, {'maximum': 100})
            self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer private-api-token')

    def test_archive_digest_substitution_stops_before_extraction(self):
        descriptor = copy.deepcopy(self.descriptor)
        descriptor.update({'release': {'id': 11, 'tag_name': 'build-request-one'},
                           'asset': {'id': 22, 'name': 'unsigned.tar', 'size': 5, 'sha256': 'e' * 64},
                           'attestation': {'id': 23, 'name': 'attestation.jsonl', 'size': 2, 'sha256': 'f' * 64}})
        release = {**descriptor['release'], 'draft': True, 'assets': [{'id': 22}, {'id': 23}]}
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary, patch.dict(os.environ, {'GITHUB_REPOSITORY': self.record['repository']}), patch(
                'tools.build_store.github_api.api', side_effect=[release, descriptor['asset']]), patch(
                'tools.build_store.download_asset', side_effect=lambda repo, asset, path, maximum: Path(path).write_bytes(b'wrong')):
            with self.assertRaisesRegex(ValueError, 'digest differs'):
                build_store.fetch_original(descriptor, Path(temporary) / 'archive.tar')
