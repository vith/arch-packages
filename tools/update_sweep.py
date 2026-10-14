"""Isolate package failures while preserving fatal shared-batch checks."""
from contextlib import contextmanager
import json
import os
import sys


class BatchFailure(ValueError):
    """The captured shared state is no longer safe for any package."""


class PackageErrors(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('; '.join(
            f"{error['stage']} {error['pkgbase'] or 'PR ' + str(error['pr_number'])}: {error['error']}"
            for error in errors))


class SweepFailures:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.errors = []
        self.failed_packages = set()

    @contextmanager
    def package(self, stage, pkgbase, **identity):
        try:
            yield
        except BatchFailure:
            raise
        except Exception as error:
            if not self.enabled:
                raise
            self.errors.append({'stage': stage, 'pkgbase': pkgbase,
                                **identity, 'error': f'{type(error).__name__}: {error}'})
            self.failed_packages.add(pkgbase)

    def finish(self):
        if self.errors:
            raise PackageErrors(self.errors)


def report_failures(errors):
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a') as stream:
            stream.write(f'package_failures={len(errors)}\n')
    for error in errors:
        message = json.dumps(error, sort_keys=True)
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            message = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print('::error title=Package update failure::' + message, file=sys.stderr)
        else:
            print(message, file=sys.stderr)
