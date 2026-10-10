from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import shutil
import threading
import zipfile
import unittest
from unittest.mock import patch

from tools import package_runs

ROOT = Path.home() / '.local/state/omp/work/package-run-tests'


class IndependentPackageRuns(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.plan = {'base': 'b' * 40, 'head': 'a' * 40, 'run_id': '123', 'run_attempt': '1',
                     'packages': [{'pkgbase': 'first', 'input_digest': 'd' * 64},
                                  {'pkgbase': 'second', 'input_digest': 'e' * 64},
                                  {'pkgbase': 'unchanged', 'reuse': True}]}

    def test_only_explicit_changed_package_selected_without_mutating_authority(self):
        self.assertEqual(package_runs.select(self.plan, 'first')['packages'], [self.plan['packages'][0]])
        self.assertEqual(len(self.plan['packages']), 3)
        for name in ('unknown', 'unchanged'):
            with self.assertRaises(ValueError):
                package_runs.select(self.plan, name)
        self.plan['packages'].append(self.plan['packages'][0])
        with self.assertRaises(ValueError):
            package_runs.select(self.plan, 'first')

    def test_publication_cannot_dispatch(self):
        with patch('tools.package_runs.github_api.api') as api:
            with self.assertRaisesRegex(ValueError, 'publication'):
                package_runs.collect(Path('unused'), kind='publication')
        api.assert_not_called()

    def test_forged_authority_stops_before_recipe_or_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            (work / 'candidate.json').write_text(json.dumps(self.plan))
            parent = {'path': '.github/workflows/candidate.yml', 'head_sha': self.plan['base'], 'head_branch': 'main'}
            with patch('tools.package_runs.github_api.api', return_value=parent), patch(
                    'tools.package_runs.subprocess.check_output', return_value=self.plan['base']), patch(
                    'tools.package_runs.recipe_candidates.verify_authorization', side_effect=ValueError('forged proof')), patch(
                    'tools.package_runs.recover') as recover, patch('tools.package_runs.recipes.copy_recipe') as copy:
                with self.assertRaisesRegex(ValueError, 'forged proof'):
                    package_runs.prepare(work, 'first', '123', '1')
                recover.assert_not_called()
                copy.assert_not_called()

    def test_queued_admission_reuses_original_success_without_recipe_preparation(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            (work / 'candidate.json').write_text(json.dumps(self.plan))
            parent = {'path': '.github/workflows/candidate.yml', 'head_sha': self.plan['base'], 'head_branch': 'main'}
            descriptor = {'producer': {'run_id': '77', 'run_attempt': '2'}}
            with patch('tools.package_runs.github_api.api', return_value=parent), patch(
                    'tools.package_runs.subprocess.check_output', return_value='c' * 40), patch(
                    'tools.package_runs.recipe_candidates.verify_authorization'), patch(
                    'tools.package_runs.recover', return_value=descriptor), patch(
                    'tools.package_runs.build_store.materialize'), patch('tools.package_runs.recipes.copy_recipe') as copy, patch.dict(
                    os.environ, {'INPUT_DIGEST': 'd' * 64}):
                package_runs.prepare(work, 'first', '123', '1')
                copy.assert_not_called()
            self.assertEqual(json.loads((work / 'build-descriptor.json').read_text()), descriptor)
            self.assertEqual(json.loads((work / 'candidate.json').read_text()), self.plan)

    def history(self, steps, attempt=1):
        run = {'id': 77, 'run_attempt': attempt, 'head_sha': self.plan['base'],
               'head_branch': 'main', 'path': '.github/workflows/build-package.yml',
               'display_title': package_runs.title(self.plan, 'first')}
        def rows(path, key):
            return iter([run] if key == 'workflow_runs' else [{'name': 'package', 'steps': steps}])
        return rows

    def test_failed_transport_after_success_is_missing_bytes_not_new_compile(self):
        steps = [{'name': 'Build package without credentials', 'conclusion': 'failure'},
                 {'name': 'Authenticate actual compilation success', 'conclusion': 'success'}]
        with patch('tools.package_runs.build_store._lookup_durable', return_value=None), patch(
                'tools.package_runs.rows', side_effect=self.history(steps, attempt=2)), patch(
                'tools.package_runs.download_artifact', side_effect=RuntimeError('no recoverable archive')):
            with self.assertRaisesRegex(RuntimeError, 'no recoverable archive'):
                package_runs.recover(self.plan, 'first', ROOT)

    def test_actual_failed_compilation_allows_authorized_retry(self):
        steps = [{'name': 'Build package without credentials', 'conclusion': 'failure'},
                 {'name': 'Authenticate actual compilation failure', 'conclusion': 'success'}]
        with patch('tools.package_runs.build_store._lookup_durable', return_value=None), patch(
                'tools.package_runs.rows', side_effect=self.history(steps)):
            self.assertIsNone(package_runs.recover(self.plan, 'first', ROOT))

    def test_unprovable_or_canceled_started_compilation_never_retries(self):
        for outcome in ('failure', 'cancelled', 'success'):
            steps = [{'name': 'Build package without credentials', 'conclusion': outcome}]
            with self.subTest(outcome=outcome), patch(
                    'tools.package_runs.build_store._lookup_durable', return_value=None), patch(
                    'tools.package_runs.rows', side_effect=self.history(steps)):
                with self.assertRaisesRegex(RuntimeError, 'unprovable'):
                    package_runs.recover(self.plan, 'first', ROOT)

    def test_checkpoint_wrong_input_or_later_validation_failure_cannot_relabel_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            directory = Path(temporary)
            (directory / 'input').mkdir()
            bundle = {**self.plan, 'image': 'image', 'harness_sha': 'harness',
                      'packages': [self.plan['packages'][0]]}
            (directory / 'input/bundle.json').write_text(json.dumps(bundle))
            checkpoint = {'schema': 1, 'state': 'success', 'input_digest': 'd' * 64,
                          'pkgbase': 'first', **{key: bundle[key] for key in
                          ('image', 'harness_sha', 'run_id', 'run_attempt')}}
            (directory / 'compilation.json').write_text(json.dumps(checkpoint))
            package_runs.checkpoint(directory, 'success')
            with self.assertRaises(ValueError):
                package_runs.checkpoint(directory, 'failure')
            checkpoint['input_digest'] = 'e' * 64
            (directory / 'compilation.json').write_text(json.dumps(checkpoint))
            with self.assertRaises(ValueError):
                package_runs.checkpoint(directory, 'success')

    def test_recovery_mode_cannot_fall_through_to_compilation_when_bytes_missing(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            with patch('tools.package_runs.build_store._lookup_durable', return_value=None), patch(
                    'tools.package_runs.download_artifact', side_effect=RuntimeError('missing original archive')), patch(
                    'tools.package_runs.recipes.copy_recipe') as copy, patch.dict(os.environ, {
                    'INPUT_DIGEST': 'd' * 64, 'RECOVERY_MODE': 'true', 'ORIGINAL_RUN': '77',
                    'ORIGINAL_ATTEMPT': '2', 'ORIGINAL_ARTIFACT': json.dumps({
                        'id': 99, 'digest': 'sha256:' + 'a' * 64, 'size_in_bytes': 100, 'name': 'package-first-77-2'})}):
                with self.assertRaisesRegex(RuntimeError, 'missing original archive'):
                    package_runs.prepare(work, 'first', '123', '1')
                copy.assert_not_called()

    def test_original_artifact_digest_and_zip_paths_are_verified_before_recovery(self):
        for unsafe, substitute in ((False, False), (False, True), (True, False)):
            with self.subTest(unsafe=unsafe, substitute=substitute), tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                work = Path(temporary)
                archive = work / 'source.zip'
                with zipfile.ZipFile(archive, 'w') as stream:
                    stream.writestr('../escape' if unsafe else 'unsigned.tar', b'original archive')
                    stream.writestr('candidate.json', json.dumps(self.plan))
                artifact = {'id': 99, 'name': 'package-first-77-2', 'expired': False,
                            'size_in_bytes': archive.stat().st_size,
                            'digest': 'sha256:' + ('a' * 64 if substitute else package_runs.build_store.sha(archive)),
                            'workflow_run': {'id': 77, 'head_sha': self.plan['base']}}
                producer = {'head_sha': self.plan['base'], 'head_branch': 'main',
                            'path': '.github/workflows/build-package.yml'}
                def download(endpoint, destination, maximum, *, accept):
                    shutil.copyfile(archive, destination)
                with patch('tools.package_runs.rows', return_value=iter([artifact])), patch(
                        'tools.package_runs.github_api.api', return_value=producer), patch(
                        'tools.package_runs.build_store.download_api', side_effect=download):
                    if unsafe or substitute:
                        with self.assertRaises(ValueError):
                            package_runs.download_artifact(77, 2, 'first', work / 'recovered')
                    else:
                        recovered = package_runs.download_artifact(77, 2, 'first', work / 'recovered')
                        self.assertEqual((recovered / 'unsigned.tar').read_bytes(), b'original archive')
                        self.assertEqual(json.loads((recovered / 'original-artifact.json').read_text())['digest'], artifact['digest'])
                self.assertFalse((work / 'escape').exists())

    def test_original_zip_transport_negotiates_media_and_preserves_verified_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as archive:
                archive.writestr('unsigned.tar', b'original successful build')
                archive.writestr('candidate.json', json.dumps(self.plan))
            zip_bytes = payload.getvalue()
            source = work / 'original.zip'
            source.write_bytes(zip_bytes)
            artifact = {'id': 99, 'name': 'package-first-77-2', 'expired': False,
                        'size_in_bytes': len(zip_bytes),
                        'digest': 'sha256:' + package_runs.build_store.sha(source),
                        'workflow_run': {'id': 77, 'head_sha': self.plan['base']}}
            producer = {'head_sha': self.plan['base'], 'head_branch': 'main',
                        'path': '.github/workflows/build-package.yml'}
            zip_endpoint = f'repos/{package_runs.REPOSITORY}/actions/artifacts/99/zip'
            release_endpoint = f'repos/{package_runs.REPOSITORY}/releases/assets/22'
            representations = {
                '/' + zip_endpoint: ('application/vnd.github+json', zip_bytes),
                '/' + release_endpoint: ('application/octet-stream', b'original release bytes')}
            class Transport(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path not in representations:
                        self.send_error(404)
                        return
                    media, content = representations[self.path]
                    if self.headers.get('Authorization') != 'Bearer fixture-token':
                        self.send_error(401)
                        return
                    if self.headers.get('Accept') != media:
                        self.send_error(415)
                        return
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

                def log_message(self, *args):
                    pass

            server = HTTPServer(('127.0.0.1', 0), Transport)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                api = f'http://127.0.0.1:{server.server_port}'
                with patch('tools.package_runs.github_api.API', api), patch.dict(
                        os.environ, {'GITHUB_TOKEN': 'fixture-token'}), patch(
                        'tools.package_runs.rows', return_value=iter([artifact])), patch(
                        'tools.package_runs.github_api.api', return_value=producer):
                    with self.assertRaises(package_runs.github_api.GitHubError) as failure:
                        package_runs.build_store.download_api(
                            zip_endpoint, work / 'wrong-media.zip', len(zip_bytes),
                            accept='application/octet-stream')
                    self.assertEqual(failure.exception.status, 415)
                    self.assertFalse((work / 'wrong-media.zip').exists())
                    recovered = package_runs.download_artifact(77, 2, 'first', work / 'recovered')
                    release = package_runs.build_store.download_asset(
                        package_runs.REPOSITORY, 22, work / 'release.tar', 100)
                self.assertEqual((recovered / 'artifact.zip').read_bytes(), zip_bytes)
                self.assertEqual((recovered / 'unsigned.tar').read_bytes(), b'original successful build')
                self.assertEqual(json.loads((recovered / 'candidate.json').read_text()), self.plan)
                proof = json.loads((recovered / 'original-artifact.json').read_text())
                self.assertEqual((proof['id'], proof['digest'], proof['size']),
                                 (artifact['id'], artifact['digest'], artifact['size_in_bytes']))
                self.assertEqual(release.read_bytes(), b'original release bytes')
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_transport_dispatch_preserves_original_digest_and_producer_after_control_advance(self):
        original = {**self.plan, 'packages': [self.plan['packages'][0]]}
        current = {**self.plan, 'base': 'c' * 40,
                   'packages': [{**self.plan['packages'][0], 'input_digest': 'e' * 64}]}
        producer = {'run_id': '77', 'run_attempt': '2'}
        artifact = {'id': 99, 'digest': 'sha256:' + 'a' * 64, 'size': 100, 'name': 'package-first-77-2'}
        required = package_runs.OriginalTransportRecoveryRequired(original, producer, artifact, ROOT)
        requests = []
        def api(path, method, body):
            requests.append(body)
        with patch('tools.package_runs.github_api.api', side_effect=api):
            package_runs.dispatch(current, current['packages'][0], recovery=required)
        dispatched = requests[0]['inputs']
        self.assertEqual(dispatched['recovery_mode'], 'true')
        self.assertEqual(dispatched['input_digest'], original['packages'][0]['input_digest'])
        self.assertEqual(dispatched['original_run'], '77')
        self.assertEqual(dispatched['original_attempt'], '2')
        self.assertEqual(json.loads(dispatched['original_artifact'])['digest'], artifact['digest'])

    def test_empty_bookkeeping_does_not_dispatch_workers(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            plan = {**self.plan, 'packages': []}
            (work / 'candidate.json').write_text(json.dumps(plan))
            with patch('tools.package_runs.recipe_candidates.verify_authorization'), patch('tools.package_runs.github_api.api') as api:
                package_runs.collect(work)
                api.assert_not_called()
            receipt = json.loads((work / 'unsigned/native-evidence.json').read_text())
            self.assertEqual(receipt['packages'], [])
            self.assertEqual((receipt['base'], receipt['head']), (plan['base'], plan['head']))
