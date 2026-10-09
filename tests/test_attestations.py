import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import attestations

ROOT = Path.home() / '.local/state/omp/work/attestation-tests'


class ExactCertificateIdentity(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.signer = {'repository': 'vith/arch-packages', 'path': '.github/workflows/build-package.yml',
                       'head_sha': 'a' * 40, 'run_id': '77', 'run_attempt': '2'}
        self.invocation = 'https://github.com/vith/arch-packages/actions/runs/77/attempts/2'

    def result(self, invocation, issuer='https://token.actions.githubusercontent.com'):
        return [{'verificationResult': {'signature': {'certificate': {
                    'issuer': issuer, 'runInvocationURI': invocation}},
                    'statement': {'predicate': {'runInvocationURI': self.invocation}}},
                 'attestation': {'bundle': {'verificationMaterial': {'certificate': {
                    'rawBytes': base64.b64encode(b'certificate bytes').decode()}}}}}]

    def invoke(self, result):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            work = Path(temporary)
            (work / 'bundle.jsonl').write_text('{}')
            process = Mock()
            process.stdout = io.BytesIO(json.dumps(result).encode())
            process.wait.return_value = 0
            process.poll.return_value = 0
            with patch('tools.attestations.subprocess.Popen', return_value=process):
                return attestations.verify(work / 'subject', work / 'bundle.jsonl', self.signer)

    def test_exact_verified_certificate_run_attempt_is_required_despite_predicate_claim(self):
        for invocation in ('https://github.com/vith/arch-packages/actions/runs/77/attempts/1',
                           'https://github.com/vith/arch-packages/actions/runs/88/attempts/2',
                           'https://github.com/attacker/packages/actions/runs/77/attempts/2', ''):
            with self.subTest(invocation=invocation), self.assertRaisesRegex(ValueError, 'exact accepted-main'):
                self.invoke(self.result(invocation))
        self.assertEqual(self.invoke(self.result(self.invocation))['runInvocationURI'], self.invocation)

    def test_non_github_issuer_and_missing_certificate_bytes_fail_closed(self):
        with self.assertRaises(ValueError):
            self.invoke(self.result(self.invocation, issuer='https://attacker.example'))
        result = self.result(self.invocation)
        result[0]['attestation']['bundle']['verificationMaterial'] = {}
        with self.assertRaisesRegex(ValueError, 'certificate bytes'):
            self.invoke(result)
