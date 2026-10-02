"""Trusted GitHub API calls and bounded public asset downloads."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
MAX_JSON = 16 * 1024 * 1024


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _request(url, method="GET", data=None, content_type="application/json", authenticated=False, maximum=MAX_JSON):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("credential-free HTTPS required")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "n3t-arch-packages", "X-GitHub-Api-Version": "2022-11-28"}
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
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise ValueError("API response exceeds bounded size")
            return body
    except urllib.error.HTTPError as error:
        # Never return provider bodies that may echo credentials or submitted data.
        raise RuntimeError(f"GitHub HTTP {error.code} for {method} {parsed.path}") from None


def api(path, method="GET", data=None):
    if path.startswith("/"):
        path = path[1:]
    if "://" in path or path.startswith(("..", "?")):
        raise ValueError("API relative path required")
    body = _request(API + "/" + path, method, canonical(data) if data is not None else None, authenticated=True)
    return json.loads(body) if body else None


def download(url, path, maximum=805306368):
    """Anonymous streamed download; no API headers or credentials follow redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("public credential-free HTTPS asset required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "n3t-arch-packages"}), timeout=120) as response, staging.open("xb") as output:
            final = urllib.parse.urlsplit(response.url)
            if final.scheme != "https":
                raise ValueError("asset redirect downgraded transport")
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise ValueError("asset exceeds size limit")
                output.write(chunk)
        staging.replace(path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
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
