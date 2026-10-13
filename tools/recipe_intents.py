"""Prospective accepted-package metadata, consumed only by exact acceptance."""
from pathlib import Path
import copy

from tools import imports, sources
from tools.recipe_gate import tree_manifest

FIELDS = {'schema', 'pkgbase', 'predecessor', 'policy', 'lock', 'provenance'}


def load(root, name, predecessor):
    from tools import update as u
    if not isinstance(name, str) or not imports.NAME.fullmatch(name):
        raise ValueError('unsafe intent package name')
    _, accepted, _ = imports.registry(root)
    directory = Path(root)/'intents'
    path = directory/(name+'.json')
    if directory.is_symlink() or path.is_symlink():
        raise ValueError('unsafe intent path')
    if not path.exists():
        return None
    if name not in accepted or not path.is_file():
        raise ValueError('intent is not for an accepted package')
    intent = u.load(path)
    if (not isinstance(intent, dict) or set(intent) != FIELDS or intent['schema'] != 1
            or intent['pkgbase'] != name or not isinstance(intent.get('predecessor'), str)
            or not imports.SHA.fullmatch(intent['predecessor'])
            or intent['predecessor'] != predecessor or not isinstance(intent['policy'], dict)
            or intent['policy'].get('pkgbase') != name or not isinstance(intent['lock'], dict)
            or not isinstance(intent['provenance'], dict)):
        raise ValueError('intent shape/name/predecessor differs')
    u.validate_source_policy(intent['lock'], intent['policy'])
    return intent


def authenticate(root, name, predecessor, work):
    intent = load(root, name, predecessor)
    if intent is not None:
        Path(work).mkdir(parents=True, exist_ok=True)
        imports.validate_intended(name, intent['policy'], intent['lock'], intent['provenance'], work, canonical_aur=True)
    return intent


def activate(value, intent):
    result = copy.deepcopy(value)
    matches = [i for i, p in enumerate(result['packages']) if p['pkgbase'] == intent['pkgbase']]
    if len(matches) != 1 or any(p['pkgbase'] == intent['pkgbase'] for p in result.get('imports', [])):
        raise ValueError('intent activation lacks exact accepted registration')
    result['packages'][matches[0]] = copy.deepcopy(intent['policy'])
    return result


def validate_admission(old, new, oldpins, newpins, work):
    before = {row['path']: row for row in tree_manifest(old)}
    after = {row['path']: row for row in tree_manifest(new)}
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    paths = {p for p in changed if p.startswith('intents/')}
    if not paths:
        return None
    if changed != paths or oldpins != newpins:
        raise ValueError('intent admission changed accepted or unrelated data')
    names = []
    for path in sorted(paths):
        filename = path.removeprefix('intents/')
        if not filename.endswith('.json') or not imports.NAME.fullmatch(filename[:-5]):
            raise ValueError('invalid intent metadata path')
        name = filename[:-5]
        if path not in after or after[path]['kind'] != 'file' or after[path]['mode'] != 0o644:
            raise ValueError('intent admission cannot remove an intent or change its file type')
        if name not in newpins:
            raise ValueError('intent has no accepted recipe predecessor')
        authenticate(new, name, newpins[name], Path(work)/name)
        names.append(name)
    return names
