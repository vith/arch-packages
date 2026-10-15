"""Trusted GitHub API calls and bounded public asset downloads."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
MAX_JSON = 16 * 1024 * 1024
_IMMUTABLE_JSON = re.compile(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/git/(?:commits/[0-9a-f]{40}|trees/[0-9a-f]{40}(?:\?recursive=1)?)")
_IMMUTABLE_ARCHIVE = re.compile(r"https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/tarball/[0-9a-f]{40}")
_READ_SCOPE = ContextVar("github_immutable_reads", default=None)


@contextmanager
def immutable_reads():
    """Reuse successful immutable reads only within the outermost operation."""
    if _READ_SCOPE.get() is not None:
        yield
        return
    scope = {"json": {}, "archives": {}, "directory": None}
    token = _READ_SCOPE.set(scope)
    try:
        yield
    finally:
        _READ_SCOPE.reset(token)
        if scope["directory"] is not None:
            scope["directory"].cleanup()


def _archive_directory(scope):
    if scope["directory"] is None:
        from tools.recipe_acceptance import scratch_root
        scope["directory"] = tempfile.TemporaryDirectory(prefix="github-immutable-", dir=scratch_root())
    return scope["directory"].name


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


class NoAPIRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("GitHub API redirect refused")


class GitHubError(RuntimeError):
    def __init__(self, status, method, path, headers):
        self.status = status
        fields = {}
        allowed = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-used",
                   "x-ratelimit-reset", "retry-after")
        for name, value in (headers.items() if headers is not None else ()):
            name = name.lower()
            if name in allowed and isinstance(value, str):
                value = value.strip(" \t")
                if re.fullmatch(r"[0-9]{1,12}", value):
                    fields[name] = str(int(value))
        diagnostic = ", ".join(f"{name}={fields[name]}" for name in allowed if name in fields)
        text = f"GitHub HTTP {status} for {method} {urllib.parse.urlsplit(path).path}"
        super().__init__(text + (f" ({diagnostic})" if diagnostic else ""))


def _request(url, method="GET", data=None, content_type="application/json", authenticated=False, maximum=MAX_JSON):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("credential-free HTTPS required")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "arch-packages", "X-GitHub-Api-Version": "2022-11-28"}
    if data is not None:
        headers["Content-Type"] = content_type
    if authenticated:
        if parsed.hostname not in ("api.github.com", "uploads.github.com"):
            raise ValueError("API credential destination refused")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise RuntimeError("GITHUB_TOKEN required for trusted API call")
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoAPIRedirect()).open(req, timeout=120) as response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise ValueError("API response exceeds bounded size")
            return body
    except urllib.error.HTTPError as error:
        # Never return provider bodies that may echo credentials or submitted data.
        raise GitHubError(error.code, method, parsed.path, error.headers) from None


def api(path, method="GET", data=None, *, authenticated=True):
    if path.startswith("/"):
        path = path[1:]
    if "://" in path or path.startswith(("..", "?")):
        raise ValueError("API relative path required")
    scope = _READ_SCOPE.get()
    key = None
    if scope is not None and method == "GET" and data is None and _IMMUTABLE_JSON.fullmatch("/" + path):
        identity = hashlib.sha256(os.environ.get("GITHUB_TOKEN", "").encode()).digest() if authenticated else None
        key = (path, identity)
        if key in scope["json"]:
            body = scope["json"][key]
            return json.loads(body) if body else None
    body = _request(API + "/" + path, method, canonical(data) if data is not None else None, authenticated=authenticated)
    value = json.loads(body) if body else None
    if key is not None:
        scope["json"][key] = body
    return value


def _copy_bounded(source, output, maximum):
    count = 0
    while chunk := source.read(1024 * 1024):
        count += len(chunk)
        if count > maximum:
            raise ValueError("asset exceeds size limit")
        output.write(chunk)


def download(url, path, maximum=805306368):
    """Anonymous streamed download; no API headers or credentials follow redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("public credential-free HTTPS asset required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".part")
    scope = _READ_SCOPE.get()
    cacheable = scope is not None and _IMMUTABLE_ARCHIVE.fullmatch(url)
    cached = scope["archives"].get(url) if cacheable else None
    owned = None
    try:
        if cached is not None:
            with cached.open("rb") as source, staging.open("xb") as output:
                _copy_bounded(source, output, maximum)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "arch-packages"}), timeout=120) as response, staging.open("xb") as output:
                final = urllib.parse.urlsplit(response.url)
                if final.scheme != "https":
                    raise ValueError("asset redirect downgraded transport")
                _copy_bounded(response, output, maximum)
            if cacheable:
                with staging.open("rb") as source, tempfile.NamedTemporaryFile(
                        dir=_archive_directory(scope), delete=False) as output:
                    owned = Path(output.name)
                    _copy_bounded(source, output, maximum)
        staging.replace(path)
        if owned is not None:
            scope["archives"][url] = owned
            owned = None
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    finally:
        if owned is not None:
            owned.unlink(missing_ok=True)
    return path


def upload_asset(repository, release_id, path, name=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 805306368:
        raise ValueError("invalid release asset")
    name = path.name if name is None else name
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", name):
        raise ValueError("invalid release asset name")
    url = f"https://uploads.github.com/repos/{repository}/releases/{int(release_id)}/assets?" + urllib.parse.urlencode({"name": name})
    return json.loads(_request(url, "POST", path.read_bytes(), "application/octet-stream", authenticated=True))


def pages(path):
    separator = "&" if "?" in path else "?"
    for page in range(1, 101):
        values = api(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(values, list):
            raise ValueError("paginated API returned non-list")
        yield from values
        if len(values) < 100:
            return
    raise ValueError("pagination bound exhausted")
