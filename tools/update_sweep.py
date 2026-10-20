"""Isolate package failures while preserving fatal shared-batch checks."""
from contextlib import contextmanager
from html import escape
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


def _print_failure(error):
    message = json.dumps(error, sort_keys=True)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        message = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::error title=Package update failure::' + message, file=sys.stderr)
    else:
        print(message, file=sys.stderr)


def report_failures(errors):
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a') as stream:
            stream.write(f'package_failures={len(errors)}\n')
            stream.write('package_errors=' + json.dumps(errors, sort_keys=True) + '\n')
    for error in errors:
        _print_failure(error)


def _summary_cell(value):
    return escape(str(value)).replace('|', '&#124;').replace('\r', '').replace('\n', '<br>')


def report_workflow_failures(needs):
    """Surface deferred errors in the final failed job, not only green stages."""
    failures = []
    for job, stage in needs.items():
        outputs = stage.get('outputs', {})
        errors = json.loads(outputs.get('package_errors') or '[]')
        count = int(outputs.get('package_failures') or 0)
        for error in errors:
            failures.append({**error, 'job': job})
        if count != len(errors):
            failures.append({
                'job': job, 'stage': job, 'pkgbase': '',
                'error': f'Expected {count} package failures but received {len(errors)} diagnostic '
                         f'records; inspect the {job} job logs.'})
    if not failures:
        return 0
    for error in failures:
        _print_failure(error)
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as stream:
            stream.write('## Package update failures\n\n'
                         'Other packages were allowed to finish. Details from the stage jobs:\n\n'
                         '| Job | Stage | Package | Watcher / PR | Error |\n'
                         '| --- | --- | --- | --- | --- |\n')
            for error in failures:
                identity = error.get('watcher_id', '')
                if error.get('pr_number') is not None:
                    identity = f"{identity} PR #{error['pr_number']}".strip()
                cells = [error['job'], error['stage'], error['pkgbase'], identity, error['error']]
                stream.write('| ' + ' | '.join(_summary_cell(cell) for cell in cells) + ' |\n')
    return 1


if __name__ == '__main__':
    raise SystemExit(report_workflow_failures(json.loads(os.environ['UPDATE_NEEDS'])))
