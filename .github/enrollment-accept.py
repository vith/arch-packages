"""One-time trusted receipt validation; never executes returned recipe code."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import recipes, sources, update
from tools.recipe_gate import parse_srcinfo


def validate(directories, output, omp_commit):
    output.mkdir(parents=True, exist_ok=False)
    base, pins = update.control_checkout()
    policies = update.policy_at(ROOT)
    receipts = {}
    for directory in directories:
        batch = update.load(directory / 'bootstrap.json')
        if (set(batch) != {'schema', 'repository', 'base', 'recipe_pins', 'packages'}
                or batch['schema'] != 1 or batch['repository'] != update.repository()
                or batch['base'] != base or batch['recipe_pins'] != pins):
            raise ValueError('invalid bootstrap manifest')
        for name in batch['packages']:
            if name not in policies or name in receipts:
                raise ValueError('unknown/duplicate enrolled package')
            receipts[name] = directory / name
    if set(receipts) != set(policies):
        raise ValueError('bootstrap receipt scope differs from full enrollment')
    accepted = []
    for name, policy in policies.items():
        work = receipts[name]
        lock = update.load(work / 'lock.json')
        probe = update.load(work / 'probe.json')
        update.validate_source_policy(lock, policy)
        if (set(probe) != {'schema', 'version', 'pkgver', 'pkgrel', 'srcinfo', 'checksums', 'sources', 'runtime_identity', 'recipe_commit'}
                or probe['schema'] != 1 or probe['recipe_commit'] != pins[name]):
            raise ValueError('invalid native receipt')
        metadata = parse_srcinfo(probe['srcinfo'])
        if any(probe[k] != metadata[k] for k in ('version', 'pkgver', 'pkgrel')) or lock['version'] != probe['version'] or lock['sources'] != probe['sources'] or probe['checksums'] != {s['id']: s['checksums'] for s in lock['sources']}:
            raise ValueError('native receipt/lock mismatch')
        original = parse_srcinfo((ROOT / 'recipes' / name / '.SRCINFO').read_text())
        actual_outputs = {(output, arch) for output in metadata['names'] for arch in metadata['scopes'][output].get('arch', metadata['arch'])}
        expected_outputs = {(output['name'], output['arch']) for output in policy['outputs']}
        if metadata['pkgbase'] != name or actual_outputs != expected_outputs:
            raise ValueError('native output identities differ from enrollment')
        if probe['pkgrel'] != original['pkgrel']:
            raise ValueError('bootstrap changed native pkgrel')
        if name == 'oh-my-pi-vith-git':
            source = next(s for s in lock['sources'] if s['id'] == 'omp-git')
            if source['commit'] != omp_commit:
                raise ValueError('OMP does not freeze canonical integration')
            expected_runtime = probe['pkgver'].replace('.vith.r', '+vith-fork.').replace('.g', '.')
            if probe['runtime_identity'] != expected_runtime:
                raise ValueError('OMP runtime identity mismatch')
        elif probe['runtime_identity'] is not None:
            raise ValueError('unexpected runtime identity')
        rendered = output / name / 'recipe'
        rendered.parent.mkdir()
        recipes.copy_recipe(ROOT / 'recipes' / name, rendered)
        update.render_recipe(rendered, policy, probe['pkgver'], probe['checksums'], pkgrel=probe['pkgrel'])
        (rendered / '.SRCINFO').write_text(probe['srcinfo'])
        if update.tree_manifest(rendered) != update.tree_manifest(work / 'recipe'):
            raise ValueError('probe recipe differs from trusted literal rendering')
        for source in lock['sources']:
            if source['kind'] == 'git':
                sources.materialize_sources({'schema':1,'version':lock['version'],'sources':[source]},output / name / ('verify-' + source['id']))
            elif source['kind'] == 'local':
                path = rendered / source['source']
                if not path.resolve().is_relative_to(rendered.resolve()):
                    raise ValueError('local source escapes recipe')
                for algorithm, digest in source['checksums'].items():
                    if digest != 'SKIP' and hashlib.new(algorithm, path.read_bytes()).hexdigest() != digest:
                        raise ValueError('local payload checksum mismatch')
            else:
                sources.freeze_source(source)
        update.dump(output / name / 'lock.json', lock)
        update.dump(output / name / 'probe.json', probe)
        accepted.append({'pkgbase': name, 'recipe_commit': pins[name], 'version': lock['version'], 'input_sha256': hashlib.sha256(sources.canonical(lock)).hexdigest(), 'sources': lock['sources'], 'runtime_identity': probe['runtime_identity']})
    update.dump(output / 'accepted.json', {'schema': 1, 'base': base, 'recipe_pins': pins, 'packages': accepted})
    return accepted


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipts', required=True, nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--omp-commit', required=True)
    args = parser.parse_args()
    validate(args.receipts, args.output, args.omp_commit)
