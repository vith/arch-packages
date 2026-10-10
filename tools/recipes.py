"""Resolve control gitlinks to bounded, immutable recipe data, never moving refs."""
from __future__ import annotations

import configparser
import json
from pathlib import Path
import re
import shutil
import tempfile

from tools.github_api import api, download
from tools.recipe_gate import parse_srcinfo, tree_manifest

SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[a-z0-9][a-z0-9+_.-]*")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_TREE = 64 * 1024 * 1024


def _identity(repository, sha):
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise ValueError("invalid recipe repository")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("full immutable recipe/control SHA required")
    return "/repos/" + repository


def _tree(repository, sha):
    route = _identity(repository, sha)
    commit = api(route + "/git/commits/" + sha)
    if commit.get("sha") != sha or not SHA.fullmatch(commit.get("tree", {}).get("sha", "")):
        raise ValueError("recipe/control commit identity mismatch")
    tree = api(route + "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    if tree.get("truncated") or not isinstance(tree.get("tree"), list):
        raise ValueError("incomplete recipe/control tree")
    entries = {}
    for entry in tree["tree"]:
        path = entry.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or any(part in {"", ".", "..", ".git"} for part in path.split("/")):
            raise ValueError("unsafe recipe/control tree path")
        if path in entries:
            raise ValueError("duplicate recipe/control tree path")
        entries[path] = entry
    return entries


def _modules(root, repository):
    policy = root / "packages.json"
    modules = root / ".gitmodules"
    if not policy.is_file() or policy.is_symlink() or policy.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("bounded recipe enrollment required")
    from tools.imports import policies
    names = list(policies(root))
    if any(not isinstance(name, str) or not NAME.fullmatch(name) for name in names) or len(names) != len(set(names)):
        raise ValueError("invalid/duplicate enrolled recipe")
    if not modules.is_file() or modules.is_symlink() or modules.stat().st_size > 1024 * 1024:
        raise ValueError("bounded .gitmodules required")
    config = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
    config.read_string(modules.read_text())
    if config.defaults():
        raise ValueError("unexpected .gitmodules defaults")
    expected = {'submodule "recipes/' + name + '"' for name in names}
    if set(config.sections()) != expected:
        raise ValueError("submodules must exactly match recipe enrollment")
    for name in names:
        section = config['submodule "recipes/' + name + '"']
        if dict(section) != {"path": "recipes/" + name, "url": "https://github.com/" + repository + ".git", "branch": "pkg/" + name}:
            raise ValueError("recipe submodule identity/path/branch changed")
    return names


def copy_recipe(source, destination):
    """Copy complete tracked recipe data, excluding only root Git administration."""
    source, destination = Path(source), Path(destination)
    tree_manifest(source)
    shutil.copytree(source, destination, symlinks=True, ignore=lambda directory, names: {".git"} if Path(directory) == source else set())
    return destination


def materialize(root, control_sha, repository, extract_tree, selected=None):
    """Validate every enrolled pin and export selected immutable recipe payloads."""
    root = Path(root)
    _identity(repository, control_sha)
    names = _modules(root, repository)
    selected = set(names) if selected is None else set(selected)
    if not selected <= set(names):
        raise ValueError("unregistered recipe export selection")
    tree = _tree(repository, control_sha)
    links = {path: entry for path, entry in tree.items() if entry.get("mode") == "160000" or entry.get("type") == "commit"}
    if set(links) != {"recipes/" + name for name in names}:
        raise ValueError("control tree must contain exactly the enrolled recipe gitlinks")
    recipes = root / "recipes"
    if recipes.is_symlink() or recipes.exists() and not recipes.is_dir():
        raise ValueError("unsafe recipe container")
    recipes.mkdir(exist_ok=True)
    pins = {}
    for name in names:
        entry = links["recipes/" + name]
        sha = entry.get("sha")
        if entry.get("mode") != "160000" or entry.get("type") != "commit":
            raise ValueError("recipe pin is not a Git commit gitlink")
        _identity(repository, sha)
        pins[name] = sha
    with tempfile.TemporaryDirectory(prefix="gitlink-export-", dir=root.parent) as session:
        work = Path(session)
        # Verify all selected payloads before installing any of them.
        for name in names:
            if name not in selected:
                continue
            sha = pins[name]
            recipe_tree = _tree(repository, sha)
            if any(item.get("mode") == "160000" or item.get("type") == "commit" for item in recipe_tree.values()):
                raise ValueError("nested recipe submodules are not enrolled")
            archive, exported = work / (name + ".tar.gz"), work / name
            download("https://api.github.com/repos/" + repository + "/tarball/" + sha, archive, maximum=MAX_TREE)
            extract_tree(archive, exported)
            manifest = tree_manifest(exported)
            if not manifest or not (exported / "PKGBUILD").is_file() or not (exported / ".SRCINFO").is_file():
                raise ValueError("complete package-root recipe required")
            if parse_srcinfo((exported / ".SRCINFO").read_text())["pkgbase"] != name:
                raise ValueError("gitlink points at another package recipe")
            destination = recipes / name
            if destination.is_symlink() or destination.exists() and not destination.is_dir():
                raise ValueError("unsafe materialized recipe path")
            if destination.exists() and any(destination.iterdir()) and tree_manifest(destination) != manifest:
                raise ValueError("materialized recipe differs from its immutable gitlink")
        for name in names:
            if name not in selected:
                continue
            destination = recipes / name
            if destination.exists():
                if any(destination.iterdir()):
                    continue
                destination.rmdir()
            shutil.move(str(work / name), destination)
    return pins


def is_ancestor(repository, old_sha, new_sha):
    route = _identity(repository, old_sha)
    _identity(repository, new_sha)
    if old_sha == new_sha:
        return True
    comparison = api(route + "/compare/" + old_sha + "..." + new_sha)
    return comparison.get("status") == "ahead" and comparison.get("merge_base_commit", {}).get("sha") == old_sha
