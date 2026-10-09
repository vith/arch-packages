"""Signed public dependency fallback for the disposable trusted Arch root."""
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

FINGERPRINT = '9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617'
N3T_FINGERPRINT = '433D492D0438666BBB7230BB91D0A55E51016A94'


LATEST_URL = 'https://github.com/vith/arch-packages/releases/latest'


class NoReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def redirect_server(location):
    if (not isinstance(location, str) or not location
            or re.search(r'[\s\x00-\x1f\x7f?#]', location)):
        raise ValueError('invalid public dependency release redirect')
    target = urllib.parse.urlsplit(urllib.parse.urljoin(LATEST_URL, location))
    tag = re.fullmatch(r'/vith/arch-packages/releases/tag/([A-Za-z0-9][A-Za-z0-9._+~-]*)',
                       target.path)
    if (target.scheme != 'https' or target.netloc != 'github.com'
            or target.query or target.fragment or not tag):
        raise ValueError('dependency release redirect outside repository boundary')
    return f'https://github.com/vith/arch-packages/releases/download/{tag[1]}'


def latest_server():
    request = urllib.request.Request(LATEST_URL, method='HEAD')
    try:
        with urllib.request.build_opener(NoReleaseRedirect()).open(request, timeout=120):
            raise ValueError('public dependency latest release did not redirect')
    except urllib.error.HTTPError as error:
        try:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            return redirect_server(error.headers.get('Location'))
        finally:
            error.close()


def verify_primary_key(listing, fingerprint=FINGERPRINT):
    primary = []
    want = False
    for line in listing.splitlines():
        fields = line.split(':')
        if fields[0] == 'pub':
            if want:
                raise ValueError('missing primary signing fingerprint')
            want = True
        elif fields[0] == 'sub' and want:
            raise ValueError('missing primary signing fingerprint')
        elif fields[0] == 'fpr' and want:
            if len(fields) <= 9:
                raise ValueError('malformed signing fingerprint')
            primary.append(fields[9])
            want = False
    if want or primary != [fingerprint]:
        raise ValueError('dependency public key differs from primary signing fingerprint')


def _append_repository(name, key, fingerprint, server, work, run,
                       *, config='/etc/pacman.conf', gpgdir=None):
    home = Path(work) / (name + '-key-check')
    home.mkdir(mode=0o700)
    listing = run(['gpg', '--batch', '--no-options', '--homedir', str(home),
                   '--show-keys', '--with-colons', str(key)])
    verify_primary_key(listing, fingerprint)
    config = Path(config)
    existing = config.read_text()
    if re.search(r'^\s*\[' + re.escape(name) + r'\]', existing, re.M):
        raise ValueError('dependency repository already configured')
    # These commands operate only in the disposable container's local keyring.
    key_command = ['pacman-key', '--config', str(config)]
    if gpgdir is not None:
        key_command += ['--gpgdir', str(gpgdir)]
    run([*key_command, '--init'])
    run([*key_command, '--add', str(key)])
    run([*key_command, '--lsign-key', fingerprint])
    config.write_text(existing + f'\n[{name}]\nSigLevel = Required\nServer = {server}\n')


def configure(root, work, run):
    """Append signed GH then n3t fallbacks after the official repositories."""
    root = Path(root)
    _append_repository('vith-gh', root / 'keys/arch-packages.asc',
                       FINGERPRINT, latest_server(), work, run)
    _append_repository('vith-arch', root / 'keys/n3t.asc',
                       N3T_FINGERPRINT, 'https://arch.n3t.work/repo', work, run)
    run(['pacman', '-Sy', '--noconfirm'], stream=True)


def configure_n3t(work, run=None, *, config='/etc/pacman.conf', gpgdir=None):
    """Add only n3t, preserving a consumer's already pinned GH snapshot."""
    if run is None:
        from tools.native import run
    root = Path(__file__).resolve().parent.parent
    _append_repository('vith-arch', root / 'keys/n3t.asc',
                       N3T_FINGERPRINT, 'https://arch.n3t.work/repo', work, run,
                       config=config, gpgdir=gpgdir)
    command = ['pacman', '--config', str(config)]
    if gpgdir is not None:
        command += ['--gpgdir', str(gpgdir)]
    run([*command, '-Sy', '--noconfirm'], stream=True)
