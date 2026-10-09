"""Fail-closed mechanical recipe classifier; never evaluates shell recipes."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess

from tools.github_api import canonical

NAME = re.compile(r"[a-z0-9][a-z0-9@+_.-]*\Z")
VERSION = re.compile(r"[A-Za-z0-9._+~]+\Z")
FIELD = re.compile(r"\s*([a-zA-Z][a-zA-Z0-9_]*)\s*=\s*(\S.*)\Z")
SINGLE = {"pkgbase", "pkgver", "pkgrel", "epoch", "pkgdesc", "url", "install", "changelog"}
CHECKSUMS = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128, "b2": 128}


def parse_srcinfo(text):
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 1024 * 1024:
        raise ValueError("missing or oversized .SRCINFO")
    fields, scopes, seen = [], {"base": {}}, set()
    scope, pkgbase = "base", None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = FIELD.fullmatch(raw)
        if not match:
            raise ValueError("malformed .SRCINFO line")
        key, value = match.groups()
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("invalid .SRCINFO value")
        if key == "pkgbase":
            if pkgbase is not None or fields or not NAME.fullmatch(value):
                raise ValueError("duplicate/misplaced pkgbase")
            pkgbase = value
        elif pkgbase is None:
            raise ValueError("pkgbase must precede metadata")
        if key == "pkgname":
            if not NAME.fullmatch(value) or value in scopes or value == "base":
                raise ValueError("duplicate/invalid output scope")
            scope = value
            scopes[scope] = {}
        elif scope != "base" and key in {"pkgbase", "pkgver", "pkgrel", "epoch", "makedepends", "checkdepends", "source", "validpgpkeys"}:
            raise ValueError("base field in output scope")
        if key in SINGLE or key == "pkgname":
            identity = (scope, key)
            if identity in seen:
                raise ValueError("duplicate singleton field")
            seen.add(identity)
        fields.append([scope, key, value])
        scopes[scope].setdefault(key, []).append(value)
    base = scopes["base"]
    names = [name for name in scopes if name != "base"]
    if not pkgbase or not names or any(k not in base for k in ("pkgver", "pkgrel", "arch")):
        raise ValueError("incomplete recipe identity")
    pkgver, pkgrel = base["pkgver"][0], base["pkgrel"][0]
    epoch = base.get("epoch", ["0"])[0]
    if not VERSION.fullmatch(pkgver) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", pkgrel) or not re.fullmatch(r"[0-9]+", epoch):
        raise ValueError("invalid native version")
    if any(not re.fullmatch(r"[a-z0-9_]+", arch) for arch in base["arch"]):
        raise ValueError("invalid architecture")
    for output in names:
        if "arch" in scopes[output] and not set(scopes[output]["arch"]).issubset(base["arch"]):
            raise ValueError("output architecture not enrolled by base")
    version = (epoch + ":" if int(epoch) else "") + pkgver + "-" + pkgrel
    dependencies = {key: [v for _, k, v in fields if k == key or k.startswith(key + "_")] for key in ("depends", "makedepends", "checkdepends")}
    return {"pkgbase": pkgbase, "names": names, "arch": base["arch"], "pkgver": pkgver, "pkgrel": pkgrel, "epoch": int(epoch), "version": version, "fields": fields, "scopes": scopes, "dependencies": dependencies}


def tree_manifest(directory):
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("recipe directory required")
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" and path.is_file() and not path.is_symlink():
            if path.stat().st_size <= 16384 and path.read_bytes().startswith(b"gitdir: "):
                continue
            raise ValueError("invalid recipe Git administration marker")
        if any(part in (".", "..", ".git") for part in Path(relative).parts):
            raise ValueError("invalid recipe path")
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            continue
        record = {"path": relative, "mode": mode}
        if stat.S_ISREG(info.st_mode):
            if info.st_size > 16 * 1024 * 1024:
                raise ValueError("recipe payload exceeds limit")
            record.update(kind="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        elif stat.S_ISLNK(info.st_mode):
            target = path.readlink()
            if target.is_absolute() or not (path.parent / target).resolve().is_relative_to(root.resolve()):
                raise ValueError("escaping recipe symlink")
            record.update(kind="symlink", target=str(target))
        else:
            raise ValueError("special recipe file refused")
        result.append(record)
    return result


def harness_digest(root):
    root = Path(root)
    files = ["tools/build.sh", "tools/native.py", "tools/sources.py",
             "tools/recipe_gate.py", "tools/github_api.py",
             "tools/dependency_repo.py", "keys/arch-packages.asc", "keys/n3t.asc"]
    manifest = []
    for name in files:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("trusted build-harness file missing")
        manifest.append({"path": name, "mode": stat.S_IMODE(path.stat().st_mode),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return hashlib.sha256(canonical(manifest)).hexdigest()


def input_digest(recipe_dir, lock, policy, image, harness_sha):
    trusted_policy = {k: v for k, v in policy.items() if not k.startswith("_")}
    metadata = parse_srcinfo((Path(recipe_dir) / ".SRCINFO").read_text())
    return hashlib.sha256(canonical({"tree": tree_manifest(recipe_dir), "lock": lock, "metadata": metadata, "policy": trusted_policy, "image": image, "harness": harness_sha})).hexdigest()


def _literal(text, name):
    # Standalone assignments only. A second assignment anywhere makes this
    # unsupported, even if only the first is changed. Do not erase prefixes.
    assignments = list(re.finditer(r"(?m)^[ \t]*" + re.escape(name) + r"=", text))
    mentions = list(re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"=", text))
    if len(assignments) != 1 or len(mentions) != 1:
        raise ValueError("ambiguous " + name + " assignment")
    match = re.match(r"([ \t]*" + re.escape(name) + r"=)(?P<quote>['\"]?)(?P<value>[A-Za-z0-9._+~]+)(?P=quote)(?P<end>\r?$)", text[assignments[0].start():], re.MULTILINE)
    if not match:
        raise ValueError("nonliteral/trailing command in " + name)
    start = assignments[0].start() + match.start("value")
    return start, assignments[0].start() + match.end("value"), match.group("value")


def _checksum_tokens(text, algorithm):
    assignment = algorithm + "sums"
    if algorithm not in CHECKSUMS:
        raise ValueError("unknown checksum algorithm")
    hits = list(re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(assignment) + r"=", text))
    if len(hits) != 1:
        raise ValueError("ambiguous checksum assignment")
    start = hits[0].start()
    if start and text[text.rfind("\n", 0, start) + 1:start].strip():
        raise ValueError("checksum assignment not standalone")
    match = re.match(re.escape(assignment) + r"=\((?P<body>[\s'\"A-Fa-f0-9SKIP]*)\)[ \t]*(?:\r?\n|$)", text[start:])
    if not match:
        raise ValueError("unsupported checksum syntax")
    body = match.group("body")
    tokens = list(re.finditer(r"(['\"]?)(SKIP|[A-Fa-f0-9]+)\1", body))
    residue = re.sub(r"(['\"]?)(SKIP|[A-Fa-f0-9]+)\1", "", body)
    if residue.strip() or any(t.group(2) != "SKIP" and len(t.group(2)) != CHECKSUMS[algorithm] for t in tokens):
        raise ValueError("invalid checksum literal")
    offset = start + match.start("body")
    return [(offset + t.start(2), offset + t.end(2), t.group(2)) for t in tokens]


def _compare(old, new):
    if shutil.which("vercmp"):
        command = ["vercmp", new, old]
    else:
        image = (Path(__file__).resolve().parents[1] / "build-image.txt").read_text().strip()
        if not re.fullmatch(r"ghcr\.io/archlinux/archlinux@sha256:[0-9a-f]{64}", image):
            raise ValueError("pinned Arch image required for native version ordering")
        command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   image, "vercmp", new, old]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    if not re.fullmatch(r"-?[0-9]+\s*", result.stdout):
        raise ValueError("invalid native vercmp response")
    return int(result.stdout)


def needs_pkgbuild_review(old_dir, new_dir, policy):
    """Classify recipe code only; payload and metadata still need validation."""
    old_path, new_path = Path(old_dir) / "PKGBUILD", Path(new_dir) / "PKGBUILD"
    if not old_path.is_file() or old_path.is_symlink() or not new_path.is_file() or new_path.is_symlink():
        return True
    if old_path.read_bytes() == new_path.read_bytes():
        return False
    try:
        old_text, new_text = old_path.read_bytes().decode("utf-8"), new_path.read_bytes().decode("utf-8")
        rules = policy["automatic"]
        replacements = []
        for key, name in (("version", "pkgver"), ("pkgrel", "pkgrel")):
            if rules[key]["assignment"] != name:
                return True
            a, b, _ = _literal(old_text, name)
            _, _, value = _literal(new_text, name)
            replacements.append((a, b, value))
        for entry in rules.get("checksums", []):
            old_tokens = _checksum_tokens(old_text, entry["algorithm"])
            new_tokens = _checksum_tokens(new_text, entry["algorithm"])
            index = int(entry["index"])
            if len(old_tokens) != len(new_tokens) or not 0 <= index < len(old_tokens):
                return True
            a, b, _ = old_tokens[index]
            replacements.append((a, b, new_tokens[index][2]))
        rendered = old_text
        for a, b, value in sorted(set(replacements), reverse=True):
            rendered = rendered[:a] + value + rendered[b:]
        return rendered != new_text
    except (ValueError, KeyError, IndexError, OSError, UnicodeError):
        return True


def _own_runtime_equality_transition(key, old_value, new_value, old_meta, new_meta):
    if key not in {"depends", "depends_x86_64"}:
        return False
    target, separator, _ = old_value.partition("=")
    return (bool(separator) and target in old_meta["names"] and target in new_meta["names"]
            and old_value == target + "=" + old_meta["version"]
            and new_value == target + "=" + new_meta["version"])


def verify_automatic_recipe(old_dir, new_dir, policy, oldlock, newlock, transition):
    """Authenticate trivial/unchanged code without relaxing mechanical policy."""
    if needs_pkgbuild_review(old_dir, new_dir, policy):
        raise ValueError("automatic authorization requires unchanged or enrolled literal PKGBUILD")
    old_meta = parse_srcinfo((Path(old_dir) / ".SRCINFO").read_text())
    new_meta = parse_srcinfo((Path(new_dir) / ".SRCINFO").read_text())
    old_text, new_text = (Path(old_dir) / "PKGBUILD").read_bytes().decode("utf-8"), (Path(new_dir) / "PKGBUILD").read_bytes().decode("utf-8")
    if old_text != new_text:
        for name in ("pkgver", "pkgrel"):
            if _literal(old_text, name)[2] != old_meta[name] or _literal(new_text, name)[2] != new_meta[name]:
                raise ValueError("literal recipe version differs from static metadata")
    elif old_meta != new_meta:
        raise ValueError("unchanged PKGBUILD metadata changed")
    if old_meta["epoch"] != new_meta["epoch"] or new_meta["version"] != newlock["version"]:
        raise ValueError("automatic recipe native identity mismatch")
    if old_text != new_text and _compare(old_meta["version"], new_meta["version"]) <= 0:
        raise ValueError("automatic literal changes require native version or pkgrel advancement")
    old_sources, new_sources = oldlock["sources"], newlock["sources"]
    metadata_sources = [value for _, key, value in new_meta["fields"] if key == "source" or key.startswith("source_")]
    if metadata_sources != [source["source"] for source in new_sources]:
        raise ValueError("static expanded sources differ from frozen source lock")
    if [s["id"] for s in old_sources] != [s["id"] for s in new_sources]:
        raise ValueError("automatic source enrollment/order changed")
    remote_changed = any(a != b for a, b in zip(old_sources, new_sources)
                         if a["kind"] != "local" or b["kind"] != "local")
    if remote_changed and (not transition or transition.get("authentic") is not True
                           or transition.get("fast_forward") is not True):
        raise ValueError("automatic remote changes require independent authentic transition")
    if old_meta["pkgver"] != new_meta["pkgver"] and (not transition or transition.get("authentic") is not True or transition.get("fast_forward") is not True):
        raise ValueError("automatic version change requires authentic upstream transition")
    checksum_fields = {}
    for entry in policy.get("automatic", {}).get("checksums", []):
        algorithm, index = entry["algorithm"], int(entry["index"])
        tokens = _checksum_tokens(new_text, algorithm)
        source = next(s for s in new_sources if s["id"] == entry["source_id"])
        expected = source["checksums"][algorithm]
        if not 0 <= index < len(tokens) or tokens[index][2].lower() != expected.lower():
            raise ValueError("literal checksum differs from frozen source bytes")
        old_tokens = _checksum_tokens(old_text, algorithm)
        if tokens[index][2] == "SKIP" and old_tokens[index][2] != "SKIP":
            raise ValueError("automatic checksum change cannot discard byte verification")
        checksum_fields.setdefault(algorithm + "sums", set()).add(index)
    if len(old_meta["fields"]) != len(new_meta["fields"]):
        raise ValueError("automatic metadata layout changed")
    indexes = {}
    for previous, current in zip(old_meta["fields"], new_meta["fields"]):
        scope, key, value = current
        if previous[:2] != current[:2]:
            raise ValueError("automatic metadata scope/order changed")
        index = indexes.get((scope, key), 0)
        indexes[(scope, key)] = index + 1
        if key in checksum_fields and index in checksum_fields[key]:
            if value != _checksum_tokens(new_text, key[:-4])[index][2]:
                raise ValueError("static checksum metadata differs from recipe")
        if previous == current or key in {"pkgver", "pkgrel"}:
            continue
        if key in checksum_fields and index in checksum_fields[key]:
            continue
        if (key == "source" or key.startswith("source_")) and remote_changed and transition.get("source_templates_verified"):
            continue
        if _own_runtime_equality_transition(key, previous[2], value, old_meta, new_meta):
            continue
        raise ValueError("automatic unaffected metadata changed: " + key)
    return True


def classify_recipe_update(old_dir, new_dir, policy):
    version = None
    def verdict(decision, reason):
        return {"decision": decision, "reason": reason, "version": version}
    try:
        old_meta = parse_srcinfo((Path(old_dir) / ".SRCINFO").read_text())
        new_meta = parse_srcinfo((Path(new_dir) / ".SRCINFO").read_text())
        version = new_meta["version"]
        old_tree, new_tree = tree_manifest(old_dir), tree_manifest(new_dir)
    except (ValueError, OSError, UnicodeError) as error:
        return verdict("invalid", str(error))
    if [(f["path"], f["mode"], f["kind"], f.get("target")) for f in old_tree] != [(f["path"], f["mode"], f["kind"], f.get("target")) for f in new_tree]:
        return verdict("manual", "recipe paths, modes or symlink targets changed")
    changed = {a["path"] for a, b in zip(old_tree, new_tree) if a != b}
    if changed - {"PKGBUILD", ".SRCINFO"}:
        return verdict("manual", "local payload changed")
    if old_meta["epoch"] != new_meta["epoch"]:
        return verdict("manual", "epoch changed")
    automatic = policy.get("automatic")
    if not isinstance(automatic, dict) or automatic.get("version", {}).get("assignment") != "pkgver" or automatic.get("pkgrel", {}).get("assignment") != "pkgrel":
        return verdict("manual", "literal version edits are not explicitly enrolled")
    evidence = policy.get("_verified_transition")
    if not isinstance(evidence, dict) or evidence.get("authentic") is not True or evidence.get("fast_forward") is not True:
        return verdict("manual", "independently verified source transition required")
    if evidence.get("srcinfo") != (Path(new_dir) / ".SRCINFO").read_text():
        return verdict("invalid", "proposal metadata differs from frozen native probe")
    try:
        if new_meta["pkgrel"] != "1" or _compare(old_meta["version"], new_meta["version"]) <= 0:
            return verdict("manual", "version must advance natively with pkgrel 1")
        old_text, new_text = (Path(old_dir) / "PKGBUILD").read_text(), (Path(new_dir) / "PKGBUILD").read_text()
        replacements = []
        for name in ("pkgver", "pkgrel"):
            a, b, value = _literal(old_text, name)
            _, _, proposed = _literal(new_text, name)
            if proposed != new_meta[name] or value != old_meta[name]:
                return verdict("manual", "literal version differs from native metadata")
            replacements.append((a, b, proposed))
        for entry in policy.get("automatic", {}).get("checksums", []):
            algorithm, index = entry["algorithm"], int(entry["index"])
            old_tokens, new_tokens = _checksum_tokens(old_text, algorithm), _checksum_tokens(new_text, algorithm)
            if len(old_tokens) != len(new_tokens) or index < 0 or index >= len(old_tokens):
                return verdict("manual", "checksum layout changed")
            a, b, value = old_tokens[index]
            new_value = new_tokens[index][2]
            if value != new_value:
                expected = evidence.get("checksums", {}).get(entry["source_id"], {}).get(algorithm)
                if new_value == "SKIP" or expected != new_value.lower():
                    return verdict("invalid", "changed checksum lacks independently fetched byte evidence")
                replacements.append((a, b, new_value))
        rendered = old_text
        for a, b, value in sorted(replacements, reverse=True):
            rendered = rendered[:a] + value + rendered[b:]
        if rendered != new_text:
            return verdict("manual", "unenrolled recipe bytes changed")
        # Native probe binds all proposed expanded source/checksum values. Only
        # version, exact own-output runtime equalities and explicitly mapped
        # source/checksum fields may differ;
        # retain original order, scope, unknown keys and every other field.
        if len(old_meta["fields"]) != len(new_meta["fields"]):
            return verdict("manual", "metadata layout changed")
        allowed = {"pkgver", "pkgrel"}
        allowed.update(entry["algorithm"] + "sums" for entry in policy.get("automatic", {}).get("checksums", []))
        for (os, ok, ov), (ns, nk, nv) in zip(old_meta["fields"], new_meta["fields"]):
            if (os, ok) != (ns, nk):
                return verdict("manual", "metadata scope/order changed")
            if ov == nv or ok in {"pkgver", "pkgrel"}:
                continue
            if ok == "source" or ok.startswith("source_"):
                if not evidence.get("source_templates_verified"):
                    return verdict("manual", "expanded source transition unenrolled")
            elif ok in allowed:
                # Full independently generated metadata already equals proposal.
                continue
            elif _own_runtime_equality_transition(ok, ov, nv, old_meta, new_meta):
                continue
            else:
                return verdict("manual", "unaffected/unknown metadata changed: " + ok)
        if evidence.get("lock_verified") is not True or evidence.get("auxiliary_inputs_verified") is not True:
            return verdict("manual", "complete input identities not independently verified")
        template = policy.get("automatic", {}).get("version", {}).get("template")
        if template and not re.fullmatch(template, new_meta["pkgver"]):
            return verdict("manual", "version derivation outside enrolled grammar")
    except (ValueError, KeyError, IndexError, OSError, subprocess.SubprocessError) as error:
        return verdict("manual", "unsupported deterministic rendering: " + str(error))
    return verdict("mechanical", "exact enrolled native version/source/checksum transition verified")
