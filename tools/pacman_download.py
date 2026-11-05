"""Retry only recognized retrieval failures from a download-only pacman transaction."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from tools.github_api import TRANSIENT_HTTP_CODES, retry_download


_RETRIEVAL = re.compile(r"error: failed retrieving file '[^']+' from \S+ : (.+)")
_TRANSIENT_REASONS = re.compile(
    r'(?:The requested URL returned error: (?:' + '|'.join(map(str, TRANSIENT_HTTP_CODES)) + r')'
    r'|Connection timed out after [0-9]+ milliseconds'
    r'|Operation timed out after [0-9]+ milliseconds with [0-9]+ bytes received'
    r'|Recv failure: Connection reset by peer'
    r'|Send failure: Connection reset by peer'
    r'|Failed to connect to \S+ port [0-9]+ after [0-9]+ ms: Timeout was reached'
    r'|Operation too slow\. Less than [0-9]+ bytes/sec transferred the last [0-9]+ seconds)'
)
_SUMMARIES = {
    'error: failed to commit transaction (download library error)',
    'error: failed to commit transaction (failed to retrieve some files)',
}


def transient_output(output):
    """Fail closed unless every error is a transient retrieval or its summary."""
    retrieval = False
    for line in output.splitlines():
        line = line.strip()
        if line in {'Interrupt signal received', 'Hangup signal received'}:
            return False
        if not line.lower().startswith('error:'):
            continue
        if line in _SUMMARIES:
            continue
        match = _RETRIEVAL.fullmatch(line)
        if match is None or _TRANSIENT_REASONS.fullmatch(match[1]) is None:
            return False
        retrieval = True
    return retrieval


def validate_command(command):
    """Accept only the explicit, non-installing prefetch command used by smoke."""
    if not command or command[0] != 'pacman':
        raise ValueError('download command must explicitly invoke pacman')
    seen = set()
    index = 1
    while index < len(command) and command[index] != '--':
        option = command[index]
        if option not in {'--config', '--sync', '--downloadonly', '--noconfirm'} or option in seen:
            raise ValueError('unsupported or duplicate pacman download option')
        seen.add(option)
        index += 1
        if option == '--config':
            if index == len(command) or not command[index] or command[index].startswith('-'):
                raise ValueError('pacman --config requires a path')
            index += 1
    if not {'--sync', '--downloadonly'} <= seen:
        raise ValueError('pacman requires explicit --sync --downloadonly')
    targets = command[index + 1:]
    if index == len(command) or not targets or any(not target or target.startswith('-') for target in targets):
        raise ValueError('pacman download requires explicit targets after --')


class DownloadFailure(Exception):
    def __init__(self, status, output):
        super().__init__('pacman pre-download failed')
        self.status = status
        self.output = output


def transient_failure(error):
    return (isinstance(error, DownloadFailure) and 0 < error.status < 128
            and transient_output(error.output))


def download(command, log_directory):
    """Run the validated prefetch, keeping a distinct merged log for each attempt."""
    validate_command(command)
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, LC_ALL='C')
    attempt = 0

    def run():
        nonlocal attempt
        attempt += 1
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=directory,
                prefix=f'attempt-{attempt:02d}-', suffix='.log', delete=False) as record:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding='utf-8', errors='replace',
                                  env=environment) as process:
                for line in process.stdout:
                    record.write(line)
                    record.flush()
                    print(line, end='', flush=True)
                status = process.wait()
            if status:
                record.flush()
                raise DownloadFailure(status, Path(record.name).read_text(encoding='utf-8'))
        return 0

    try:
        return retry_download(
            run, transient_failure,
            'pacman package pre-download')
    except DownloadFailure as error:
        return error.status


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log_directory', type=Path)
    if '--' not in arguments:
        parser.error('provide the exact pacman download command after --')
    separator = arguments.index('--')
    options = parser.parse_args(arguments[:separator])
    command = arguments[separator + 1:]
    try:
        status = download(command, options.log_directory)
    except ValueError as error:
        parser.error(str(error))
    return status if status >= 0 else 128 - status


if __name__ == '__main__':
    raise SystemExit(main())
