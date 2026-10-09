"""Verify accepted-main GitHub OIDC artifact subjects, never predicate identities."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from tools import github_api


def verify(path, bundle, signer):
    repository = signer['repository']
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or signer['path'] not in ('.github/workflows/candidate.yml', '.github/workflows/build-package.yml')
            or not re.fullmatch(r'[0-9a-f]{40}', signer['head_sha'])
            or not all(str(signer[k]).isdigit() and int(signer[k]) > 0 for k in ('run_id', 'run_attempt'))):
        raise ValueError('invalid exact accepted-main attestation signer')
    bundle = Path(bundle)
    if not bundle.is_file() or bundle.is_symlink() or not 0 < bundle.stat().st_size <= github_api.MAX_JSON:
        raise ValueError('missing or oversized OIDC attestation bundle')
    command = ['gh', 'attestation', 'verify', str(path), '--bundle', str(bundle), '--repo', repository,
               '--signer-workflow', repository + '/' + signer['path'], '--signer-digest', signer['head_sha'],
               '--source-digest', signer['head_sha'], '--source-ref', 'refs/heads/main',
               '--deny-self-hosted-runners', '--format', 'json']
    environment = dict(os.environ)
    if os.environ.get('GITHUB_TOKEN'):
        environment['GH_TOKEN'] = os.environ['GITHUB_TOKEN']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, env=environment)
    try:
        result = process.stdout.read(github_api.MAX_JSON + 1)
        if len(result) > github_api.MAX_JSON:
            raise ValueError('oversized verified attestation result')
        returncode = process.wait()
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
    verified = json.loads(result)
    invocation = f"https://github.com/{repository}/actions/runs/{signer['run_id']}/attempts/{signer['run_attempt']}"
    for row in verified if isinstance(verified, list) else []:
        certificate = row.get('verificationResult', {}).get('signature', {}).get('certificate', {})
        if (certificate.get('runInvocationURI') == invocation
                and certificate.get('issuer') == 'https://token.actions.githubusercontent.com'):
            material = row.get('attestation', {}).get('bundle', {}).get('verificationMaterial', {})
            encoded = material.get('certificate', {}).get('rawBytes')
            if not encoded:
                chain = material.get('x509CertificateChain', {}).get('certificates', [])
                encoded = chain[0].get('rawBytes') if chain else None
            if not encoded:
                raise ValueError('verified result lacks original signing certificate bytes')
            raw = base64.b64decode(encoded, validate=True)
            if not raw:
                raise ValueError('empty verified signing certificate')
            return {**certificate, 'certificate_sha256': hashlib.sha256(raw).hexdigest()}
    raise ValueError('verified certificate does not bind exact accepted-main run and attempt')
