import copy
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tools import build_store

ROOT = Path.home() / '.local/state/omp/work/build-store-tests'


class DurableBuildStore(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        environment = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'vith/arch-packages', 'GITHUB_TOKEN': 'fixture-token'})
        environment.start()
        self.addCleanup(environment.stop)
        self.record = {'repository': 'vith/arch-packages', 'base': 'a' * 40, 'head': 'b' * 40,
                       'run_id': '123', 'run_attempt': '1',
                       'packages': [{'pkgbase': 'first', 'input_digest': 'd' * 64}]}
        self.producer = {'run_id': '77', 'run_attempt': '2', 'head_sha': 'a' * 40,
                         'path': build_store.WORKFLOW, 'pkgbase': 'first'}
        self.signer = {k: self.producer[k] for k in ('run_id', 'run_attempt', 'head_sha', 'path')}
        self.descriptor = {'schema': 2, 'pkgbase': 'first', 'input_digest': 'd' * 64,
                           'record': self.record, 'producer': self.producer,
                           'release': {'id': 11, 'tag_name': 'build-' + 'd' * 64, 'target_commitish': 'a' * 40},
                           'asset': {'id': 22, 'name': 'unsigned.tar', 'size': 10240, 'sha256': 'e' * 64},
                           'attestation': {'id': 23, 'name': 'attestation.jsonl', 'size': 2, 'sha256': 'c' * 64,
                                           'signer': self.signer, 'original_artifact': None},
                           'evidence_sha256': 'f' * 64}

    def test_replaced_renamed_resized_promoted_or_retargeted_asset_fails(self):
        release = {**self.descriptor['release'], 'draft': True, 'assets': [{'id': 22}, {'id': 23}]}
        asset = {k: self.descriptor['asset'][k] for k in ('id', 'name', 'size')}
        for kind, change in [('release', {'draft': False}), ('release', {'target_commitish': 'c' * 40}),
                             ('release', {'assets': [{'id': 33}]}), ('asset', {'id': 33}),
                             ('asset', {'name': 'other.tar'}), ('asset', {'size': 1})]:
            modified_release = {**release, **(change if kind == 'release' else {})}
            modified_asset = {**asset, **(change if kind == 'asset' else {})}
            with self.subTest(kind=kind, change=change), patch(
                    'tools.build_store.recipe_candidates.verify_authorization'), patch(
                    'tools.build_store.producer_run'), patch('tools.build_store.recipe_state.require_attestation'), patch(
                    'tools.build_store.recipe_state.load', return_value=self.descriptor), patch(
                    'tools.build_store.github_api.api', side_effect=[modified_release, modified_asset]):
                with self.assertRaises(ValueError):
                    build_store.verify(self.descriptor)

    def test_descriptor_cannot_claim_different_approved_source_input(self):
        forged = copy.deepcopy(self.descriptor)
        forged['record']['packages'][0]['input_digest'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'input mismatch'):
            build_store.verify(forged)

    def archive(self, work, candidate=None):
        evidence = work / 'native-evidence.json'
        evidence.write_text(json.dumps({'packages': [{'pkgbase': 'first', 'files': []}]}))
        original_candidate = work / 'candidate.json'
        original_candidate.write_bytes(build_store.github_api.canonical(self.record if candidate is None else candidate))
        archive = work / 'original.tar'
        with tarfile.open(archive, 'w') as stream:
            stream.add(evidence, arcname=evidence.name)
            stream.add(original_candidate, arcname=original_candidate.name)
        descriptor = copy.deepcopy(self.descriptor)
        descriptor['asset']['size'] = archive.stat().st_size
        descriptor['asset']['sha256'] = build_store.sha(archive)
        descriptor['evidence_sha256'] = build_store.sha(evidence)
        return archive, descriptor

    def test_materialization_rejects_archive_hash_substitution(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            destination = Path(temporary) / 'output'
            def download(repository, asset_id, path, maximum):
                Path(path).write_bytes(b'wrong')
            with patch('tools.build_store.verify'), patch('tools.build_store.github_api.api', return_value=self.descriptor['asset']), patch(
                    'tools.build_store.download_asset', side_effect=download):
                with self.assertRaisesRegex(ValueError, 'archive or attestation bytes differ'):
                    build_store.materialize(self.descriptor, destination)
            self.assertFalse(destination.exists())

    def test_durable_archive_needs_no_ephemeral_actions_artifact(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            archive, descriptor = self.archive(work)
            bundle = work / 'bundle.jsonl'
            bundle.write_text('{}')
            descriptor['attestation']['sha256'] = build_store.sha(bundle)
            def api(path):
                self.assertIn('/releases/assets/', path)
                return descriptor['asset'] if path.endswith('/22') else descriptor['attestation']
            def download(repository, asset_id, path, maximum):
                shutil.copyfile(archive if asset_id == 22 else bundle, path)
            with patch('tools.build_store.verify'), patch('tools.build_store.github_api.api', side_effect=api), patch(
                    'tools.build_store.download_asset', side_effect=download), patch(
                    'tools.build_store.verify_attestation'), patch('tools.build_store.validate_outputs'):
                destination = build_store.materialize(descriptor, work / 'recovered')
            self.assertEqual((destination / 'native-evidence.json').read_bytes(), (work / 'native-evidence.json').read_bytes())
            self.assertFalse((destination / 'candidate.json').exists())

    def test_original_archive_cannot_substitute_approved_candidate(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            forged = {**self.record, 'head': 'f' * 40}
            archive, descriptor = self.archive(work, candidate=forged)
            with self.assertRaisesRegex(ValueError, 'original approved candidate'):
                build_store.unpack_original(archive, work / 'recovered', self.record)

    def test_safe_tar_rejects_link_or_path_escape_even_with_bound_hash(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                work = Path(temporary)
                archive = work / 'original.tar'
                with tarfile.open(archive, 'w') as stream:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.linkname = '/etc/passwd'
                    stream.addfile(info)
                def fetch(descriptor, path):
                    shutil.copyfile(archive, path)
                with patch('tools.build_store.verify'), patch('tools.build_store.fetch_original', side_effect=fetch):
                    with self.assertRaises(ValueError):
                        build_store.materialize(self.descriptor, work / 'recovered')

    def test_raw_archive_and_context_require_same_verified_signing_certificate(self):
        signer_run = {'path': build_store.WORKFLOW, 'head_sha': 'a' * 40, 'head_branch': 'main',
                      'event': 'workflow_dispatch', 'run_attempt': 2,
                      'display_title': 'Build first / 123.1 / ' + 'd' * 64}
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            with patch('tools.build_store.github_api.api', return_value=signer_run), patch(
                    'tools.build_store.attestations.verify', side_effect=[{'certificate_sha256': 'a'}, {'certificate_sha256': 'b'}]):
                with self.assertRaisesRegex(ValueError, 'different signing certificates'):
                    build_store.verify_attestation(work / 'unsigned.tar', work / 'bundle', self.record, self.producer, self.signer)

    def test_compatible_original_lookup_ignores_tooling_but_not_recipe_source_or_policy(self):
        original = {'pkgbase': 'first', 'input_digest': 'd' * 64, 'recipe_commit': 'b' * 40,
                    'previous_recipe_commit': 'c' * 40, 'tree_sha': 'f' * 64,
                    'lock': {'version': '2-1', 'sources': [{'commit': 'a' * 40}]},
                    'policy': {'outputs': [{'name': 'first', 'arch': 'any'}]},
                    'expected_srcinfo': 'pkgbase = first\\npkgver = 2\\npkgrel = 1\\n'}
        descriptor = copy.deepcopy(self.descriptor)
        descriptor['record']['packages'] = [original]
        current = {**original, 'input_digest': 'e' * 64}
        with patch('tools.build_store.recipe_state.load_optional', return_value=None), patch(
                'tools.build_store.recipe_state.keys', return_value=['d' * 64]), patch(
                'tools.build_store.recipe_state.load', return_value=descriptor), patch(
                'tools.build_store.verify', return_value=descriptor):
            result = build_store._lookup_durable(current)
            self.assertEqual(result['record']['base'], 'a' * 40)
            self.assertEqual(result['input_digest'], 'd' * 64)
            for field, changed in (
                    ('recipe_commit', 'c' * 40), ('previous_recipe_commit', 'e' * 40),
                    ('tree_sha', 'e' * 64), ('lock', {'version': '3-1'}),
                    ('policy', {'outputs': [{'name': 'other', 'arch': 'any'}]}),
                    ('expected_srcinfo', 'pkgbase = other\\n')):
                with self.subTest(field=field):
                    self.assertIsNone(build_store._lookup_durable({**current, field: changed}))

    def test_existing_descriptor_cannot_replace_validated_original_raw_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            directory = work / 'output'
            directory.mkdir()
            (directory / 'native-evidence.json').write_text(json.dumps({'packages': [{'pkgbase': 'first', 'files': []}]}))
            (work / 'attestation.jsonl').write_text('{}')
            with patch('tools.build_store.validate_outputs'), patch(
                    'tools.build_store.producer_run'), patch('tools.build_store.verify_attestation'), patch(
                    'tools.build_store._lookup_durable', return_value=self.descriptor), patch(
                    'tools.build_store.github_api.api') as api:
                with self.assertRaisesRegex(ValueError, 'validated original producer bytes'):
                    build_store.persist(self.record, directory, self.producer)
                api.assert_not_called()

    def test_api_redirect_never_forwards_token_to_storage(self):
        for accept in ('application/octet-stream', 'application/vnd.github+json'):
            with self.subTest(accept=accept), tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                response = io.BytesIO(b'API redirect body')
                error = urllib.error.HTTPError('https://api.github.com/asset', 302, 'Found',
                                               {'Location': 'https://storage.example/archive'}, response)
                opener = Mock()
                opener.open.side_effect = error
                storage = io.BytesIO(b'original storage bytes')
                storage.url = 'https://storage.example/archive'
                def download(request, timeout):
                    self.assertTrue(response.closed)
                    self.assertEqual(request.full_url, storage.url)
                    self.assertIsNone(request.get_header('Authorization'))
                    self.assertIsNone(request.get_header('Accept'))
                    return storage
                with patch('tools.build_store.urllib.request.build_opener', return_value=opener), patch(
                        'tools.build_store.github_api.urllib.request.urlopen', side_effect=download):
                    destination = Path(temporary) / 'download'
                    if accept == 'application/octet-stream':
                        build_store.download_asset('vith/arch-packages', 22, destination, 100)
                    else:
                        build_store.download_api('repos/vith/arch-packages/actions/artifacts/99/zip',
                                                 destination, 100, accept=accept)
                self.assertEqual(destination.read_bytes(), b'original storage bytes')
                self.assertTrue(storage.closed)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-token')
                self.assertEqual(request.get_header('Accept'), accept)
        self.assertIsNone(build_store.AssetAPIRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://storage.example'))

    def test_failed_or_malformed_asset_api_response_always_closes_before_raising(self):
        for code, headers, error_type in ((403, {}, build_store.github_api.GitHubError), (302, {}, KeyError)):
            with self.subTest(code=code):
                response = io.BytesIO(b'API error body')
                error = urllib.error.HTTPError('https://api.github.com/asset', code, 'Error', headers, response)
                opener = Mock()
                opener.open.side_effect = error
                with patch('tools.build_store.urllib.request.build_opener', return_value=opener), patch(
                        'tools.build_store.github_api.download') as anonymous:
                    with self.assertRaises(error_type):
                        build_store.download_asset('vith/arch-packages', 22, ROOT / 'download', 100)
                    self.assertTrue(response.closed)
                    anonymous.assert_not_called()
