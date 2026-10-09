#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} != --inside ]]; then
  [[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux && ${RUNNER_ENVIRONMENT:-} == github-hosted && $(uname -m) == x86_64 ]] || { echo 'smoke requires disposable GitHub Linux x86_64 runner' >&2; exit 1; }
  [[ $# == 3 || $# == 4 ]] || { echo 'usage: tools/smoke.sh expected.json enrollment.json output-dir [snapshot-id]' >&2; exit 2; }
  root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
  image=$(<"$root/build-image.txt")
  [[ $image =~ ^ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}$ ]] || exit 1
  snapshot=${4:-}
  [[ -z $snapshot || $snapshot =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'invalid snapshot' >&2; exit 1; }
  mkdir -p -- "$3"
  output=$(realpath -- "$3")
  shopt -s nullglob dotglob
  existing=("$output"/*)
  ((${#existing[@]} == 0)) || { echo 'smoke evidence directory must be empty' >&2; exit 1; }
  shopt -u nullglob dotglob
  container=$(docker create --platform linux/amd64 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add SYS_CHROOT --cap-add SYS_ADMIN --cap-add MKNOD --security-opt no-new-privileges --security-opt apparmor=unconfined "$image" /bin/bash /smoke.sh --inside "$snapshot")
  trap 'docker rm -f "$container" >/dev/null' EXIT
  docker cp "$root/tools/smoke.sh" "$container:/smoke.sh"
  docker cp "$root/tools/apexshot-smoke.sh" "$container:/apexshot-smoke.sh"
  docker cp "$root/tools/native.py" "$container:/native.py"
  docker cp "$root/tools/public-smoke.sh" "$container:/public-smoke.sh"
  docker cp "$root/tools/dependency_repo.py" "$container:/dependency_repo.py"
  docker cp "$root/tools/github_api.py" "$container:/github_api.py"
  docker cp "$root/keys/n3t.asc" "$container:/n3t.asc"
  docker cp "$root/keys/arch-packages.asc" "$container:/key.asc"
  docker cp "$root/keys/fingerprint" "$container:/fingerprint.txt"
  docker cp "$1" "$container:/expected.json"
  docker cp "$2" "$container:/enrollment.json"
  status=0
  docker start -a "$container" || status=$?
  docker cp "$container:/evidence/." "$output" || { [[ $status != 0 ]] || exit 1; }
  exit "$status"
fi
snapshot=${2:-}
mkdir -p /evidence /fresh/db /fresh/cache /fresh/gnupg /fresh/root
chmod 700 /fresh/gnupg
# A separate PTY namespace permits the actual TUI without exposing host devices.
mkdir -p /fresh/root/dev /fresh/root/proc
mount -t tmpfs -o mode=755,nosuid tmpfs /fresh/root/dev
mkdir /fresh/root/dev/pts
mount -t devpts -o newinstance,ptmxmode=0666,mode=0620 devpts /fresh/root/dev/pts
ln -s pts/ptmx /fresh/root/dev/ptmx
for spec in 'null 1 3' 'zero 1 5' 'random 1 8' 'urandom 1 9' 'tty 5 0'; do
  read -r name major minor <<<"$spec"
  mknod -m 666 "/fresh/root/dev/$name" c "$major" "$minor"
done
mount -t proc -o nosuid,nodev,noexec proc /fresh/root/proc
exec > >(tee /evidence/smoke.log) 2>&1
# Official tooling setup precedes the deliberately empty proof database/cache.
pacman -Syu --noconfirm --needed -- python tmux desktop-file-utils binutils
pacman-key --gpgdir /fresh/gnupg --init
pacman-key --gpgdir /fresh/gnupg --populate archlinux
expected=$(tr -d '[:space:]' </fingerprint.txt)
actual=$(gpg --batch --homedir /fresh/gnupg --with-colons --import-options show-only --import /key.asc | python -c 'import sys; f=[x.split(":")[9] for x in sys.stdin if x.startswith("fpr:")]; print(f[0] if f else "")')
[[ $expected =~ ^[A-Fa-f0-9]{40}$ && ${actual^^} == ${expected^^} ]] || { echo 'repository public key fingerprint mismatch' >&2; exit 1; }
pacman-key --gpgdir /fresh/gnupg --add /key.asc
pacman-key --gpgdir /fresh/gnupg --lsign-key "$expected"
server=https://github.com/vith/arch-packages/releases/latest/download
[[ -z $snapshot ]] || server="https://github.com/vith/arch-packages/releases/download/$snapshot"
repository=$(python - <<'PY'
import json
name=json.load(open('/expected.json'))['pacman_repository']
if name not in ('vith-gh','arch-packages'):
    raise SystemExit('unsupported snapshot repository name')
print(name)
PY
)
cat >/fresh/pacman.conf <<EOF
[options]
RootDir = /fresh/root
Architecture = auto
DBPath = /fresh/db
CacheDir = /fresh/cache
GPGDir = /fresh/gnupg
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Required
[core]
Server = https://geo.mirror.pkgbuild.com/\$repo/os/\$arch
[extra]
Server = https://geo.mirror.pkgbuild.com/\$repo/os/\$arch
[$repository]
SigLevel = Required
Server = $server
EOF
# Only helper targets need Spotify; its signed n3t repository follows the fixed GH snapshot.
dependency_targets=()
if python - <<'PY'
import json,sys
names={i['name'] for p in json.load(open('/expected.json'))['packages'] for i in p['files']}
sys.exit(0 if names & {'spotify-adblock','spotify-remove-ad-banner'} else 1)
PY
then
  mkdir -p /fresh/n3t /tools /keys
  cp /dependency_repo.py /github_api.py /tools/
  cp /n3t.asc /keys/n3t.asc
  PYTHONPATH=/ python - <<'PY'
import subprocess
from tools.dependency_repo import configure_n3t
def run(command, *, stream=False):
    return subprocess.run(command, check=True, text=True, stdout=None if stream else subprocess.PIPE).stdout
configure_n3t('/fresh/n3t', run=run, config='/fresh/pacman.conf', gpgdir='/fresh/gnupg')
PY
  dependency_targets=(vith-arch/spotify)
fi
cp /fresh/pacman.conf /evidence/pacman.conf
pacman --config /fresh/pacman.conf -Syy --noconfirm
pacman --config /fresh/pacman.conf -Fyy --noconfirm
python - <<'PY' > /evidence/targets.txt
import json,re
enrollment=json.load(open('/enrollment.json'))
expected=json.load(open('/expected.json'))
names=[item['name'] for package in enrollment['packages'] for item in package['outputs']]
observed=[item['name'] for package in expected['packages'] for item in package['files']]
if not names or len(names)!=len(set(names)) or len(observed)!=len(set(observed)) or set(observed)!=set(names):
    raise SystemExit('smoke evidence must cover exactly all enrolled package outputs')
for name in sorted(names):
    if not re.fullmatch(r'[a-z0-9][a-z0-9@+_.-]*',name):
        raise SystemExit('unsafe enrolled package name')
    print(expected['pacman_repository']+'/'+name)
PY
mapfile -t targets < /evidence/targets.txt
pacman --config /fresh/pacman.conf -S --noconfirm -- base python util-linux tmux desktop-file-utils binutils ca-certificates man-db zip unzip "${dependency_targets[@]}" "${targets[@]}"
# Resolver configuration is the only host configuration copied into the disposable root.
mkdir -p /fresh/root/etc /fresh/root/fresh/home /fresh/root/fresh/config /fresh/root/fresh/data /fresh/root/fresh/runtime
cp /etc/resolv.conf /fresh/root/etc/resolv.conf
chmod 700 /fresh/root/fresh/runtime
pacman --config /fresh/pacman.conf -Q > /evidence/installed.txt
python - <<'PY'
import json,subprocess
expected=json.load(open('/expected.json'))
packages=expected['packages']
names=set()
for package in packages:
    for item in package['files']:
        name=item['name']; names.add(name)
        actual=subprocess.check_output(['pacman','--config','/fresh/pacman.conf','-Q',name],text=True).strip()
        if actual != name+' '+item['version']: raise SystemExit('installed version differs: '+actual)
enrolled={item['name'] for package in json.load(open('/enrollment.json'))['packages'] for item in package['outputs']}
if names != enrolled: raise SystemExit('smoke requires every enrolled output')
PY
chroot /fresh/root useradd --uid 1000 --home-dir /fresh/home --shell /bin/bash smoke
chown -R 1000:1000 /fresh/root/fresh/home /fresh/root/fresh/config /fresh/root/fresh/data /fresh/root/fresh/runtime
# Consumers receive no ambient CI environment and no privileges.
consumer_namespace=()
consumer() { "${consumer_namespace[@]}" chroot /fresh/root /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs -- env -i PATH=/usr/bin HOME=/fresh/home XDG_CONFIG_HOME=/fresh/config XDG_DATA_HOME=/fresh/data XDG_RUNTIME_DIR=/fresh/runtime LANG=C.UTF-8 TERM=xterm-256color "$@"; }
offline_consumer() { local -a consumer_namespace=(unshare --net); consumer "$@"; }
desktop=/fresh/root/usr/share/applications/mount-archive.desktop
desktop-file-validate "$desktop"
cp "$desktop" /evidence/mount-archive.desktop
python - <<'PY'
import configparser
p=configparser.ConfigParser(interpolation=None); p.optionxform=str
p.read('/fresh/root/usr/share/applications/mount-archive.desktop')
d=p['Desktop Entry']
if d['Exec'] != '/usr/lib/gvfsd-archive file=%u': raise SystemExit('archive-mounter Exec mismatch')
expected={'application/x-cd-image','application/x-bzip-compressed-tar','application/x-compressed-tar','application/x-tar','application/x-cpio','application/zip','application/x-gzip','application/x-bzip'}
actual=set(filter(None,d['MimeType'].split(';')))
if actual != expected: raise SystemExit('archive-mounter exact MIME mismatch: '+repr(actual))
PY
# Share installed ApexShot checks with the credential-free candidate proof.
cp /apexshot-smoke.sh /fresh/root/apexshot-smoke.sh
# Keep the explicit /fresh/db database, but report paths as seen inside the chroot.
pacman --config /fresh/pacman.conf --root / -Ql apexshot > /fresh/root/apexshot-files.txt
apexshot_version=$(python - <<'PY'
import json
package=next(x for x in json.load(open('/expected.json'))['packages'] if x['pkgbase']=='apexshot')
print(package['source_lock']['version'])
PY
)
apexshot_status=0
consumer bash /apexshot-smoke.sh "$apexshot_version" /fresh/data/apexshot /apexshot-files.txt || apexshot_status=$?
cp -a /fresh/root/fresh/data/apexshot/. /evidence/
[[ $apexshot_status == 0 ]] || exit "$apexshot_status"
consumer carapace --list --names > /evidence/carapace-list.txt
consumer carapace git export git checko > /evidence/carapace-completion.json
python - <<'PY'
import json
if 'git' not in open('/evidence/carapace-list.txt').read().split(): raise SystemExit('carapace git missing')
value=json.load(open('/evidence/carapace-completion.json'))
# Carapace export preserves completion metadata beside its values array.
if not isinstance(value,dict) or not isinstance(value.get('values'),list): raise SystemExit('unexpected carapace export format')
values={x['value'] for x in value['values']}
if not {'checkout','checkout-index'} <= values: raise SystemExit('missing real git checkout completions')
PY
python - <<'PY'
import hashlib,json,pathlib,subprocess
# Historical snapshots retain the old binary package; current snapshots use nasctui.
package=next(x for x in json.load(open('/expected.json'))['packages'] if x['pkgbase'] in {'nasctui','nasc-tui-bin'})
name=package['pkgbase']
sources=[s for s in package['source_lock']['sources'] if s['source'].startswith('nascTUI-') and '::' in s['source']]
if len(sources)!=1: raise SystemExit('nasc corresponding-source lock absent/ambiguous')
source=sources[0]; filename=source['source'].split('::',1)[0]
archive=pathlib.Path('/fresh/root/usr/share/doc')/name/filename
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
if digest!=source['checksums']['sha256']: raise SystemExit('installed nasc corresponding-source hash mismatch')
members=subprocess.check_output(['bsdtar','-tf',str(archive)],text=True).splitlines()
licenses=[m for m in members if m.count('/')==1 and m.endswith('/LICENSE')]
if len(licenses)!=1: raise SystemExit('corresponding source LICENSE absent/ambiguous')
license_bytes=subprocess.check_output(['bsdtar','-xOf',str(archive),licenses[0]])
if license_bytes!=(pathlib.Path('/fresh/root/usr/share/licenses')/name/'LICENSE').read_bytes(): raise SystemExit('installed nasc license differs from corresponding source')
with open('/evidence/nasc-source.json','w') as file:
    print(json.dumps({'filename':filename,'sha256':digest,'source':source},sort_keys=True),file=file)
PY
consumer tmux -S /fresh/runtime/nasc.sock -f /dev/null new-session -d -x 100 -y 30 -s nasc nasc
trap 'consumer tmux -S /fresh/runtime/nasc.sock kill-server || true' EXIT
sleep 1
consumer tmux -S /fresh/runtime/nasc.sock capture-pane -p -t nasc > /evidence/nasc-initial.txt
consumer tmux -S /fresh/runtime/nasc.sock send-keys -t nasc -l '2+2'
sleep 1
consumer tmux -S /fresh/runtime/nasc.sock capture-pane -p -t nasc > /evidence/nasc-first.txt
consumer tmux -S /fresh/runtime/nasc.sock send-keys -t nasc Enter
sleep 1
consumer tmux -S /fresh/runtime/nasc.sock capture-pane -p -t nasc > /evidence/nasc-next-line.txt
consumer tmux -S /fresh/runtime/nasc.sock send-keys -t nasc -l 'ans*3'
sleep 1
consumer tmux -S /fresh/runtime/nasc.sock capture-pane -p -t nasc > /evidence/nasc-second.txt
python - <<'PY'
import re
for file,expression,answer in [('nasc-first.txt','2+2','4'),('nasc-second.txt','ans*3','12')]:
    text=open('/evidence/'+file).read()
    if not any(expression in line.replace(' ','') and re.search(r'(?<![0-9])'+answer+r'(?![0-9])',line) for line in text.splitlines()): raise SystemExit('nasc expression/result absent: '+file)
PY
consumer tmux -S /fresh/runtime/nasc.sock send-keys -t nasc Escape
sleep 1
if consumer tmux -S /fresh/runtime/nasc.sock has-session -t nasc; then
  echo 'nasc did not exit cleanly after Escape' >&2
  exit 1
fi
trap - EXIT
consumer cloudflare-speed-cli --text --idle-latency-duration 1s --download-duration 1s --upload-duration 1s --download-bytes-per-req 4096 --upload-bytes-per-req 4096 --concurrency 1 --export-json /fresh/data/speed.json > /evidence/cloudflare-speed.txt 2>&1
cp /fresh/root/fresh/data/speed.json /evidence/cloudflare-speed.json
python - <<'PY'
import json,math
result=json.load(open('/evidence/cloudflare-speed.json'))
for phase in ('download','upload'):
    n=result[phase]['mbps']
    if not math.isfinite(n) or n <= 0: raise SystemExit('real public network throughput missing: '+phase)
latency=result['idle_latency']['median_ms']
if latency is None or not math.isfinite(latency) or latency <= 0: raise SystemExit('real public network idle latency missing')
text=open('/evidence/cloudflare-speed.txt').read()
for phase in ('IdleLatency','Download','Upload'):
    if '== '+phase+' ==' not in text: raise SystemExit('missing speed phase '+phase)
PY
consumer omp --version > /evidence/omp-version.txt
consumer omp --help > /evidence/omp-help.txt
python - <<'PY'
import importlib.util,json
s=importlib.util.spec_from_file_location('native','/native.py'); n=importlib.util.module_from_spec(s); s.loader.exec_module(n)
p=next(x for x in json.load(open('/expected.json'))['packages'] if x['pkgbase']=='oh-my-pi-vith-git')
expected=n.runtime_identity(p['source_lock']['version'])
if open('/evidence/omp-version.txt').read().strip()!='omp/'+expected: raise SystemExit('OMP exact runtime version mismatch')
if not open('/evidence/omp-help.txt').read().strip(): raise SystemExit('OMP help missing')
if expected.encode() not in open('/fresh/root/usr/bin/omp','rb').read(): raise SystemExit('OMP appended native stamp missing')
proof={'runtime_identity': expected, 'cli_version': open('/evidence/omp-version.txt').read().strip()}
open('/evidence/omp-native.json','w').write(json.dumps(proof,sort_keys=True)+'\n')
PY
consumer python - > /evidence/google-genai.json <<'PY'
import http.server
import importlib.metadata
import json
import threading
from google import genai

requests = []
class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 65536:
            self.send_error(400, 'bounded JSON request required')
            return
        body = json.loads(self.rfile.read(length))
        requests.append({'method': self.command, 'path': self.path, 'body': body})
        response = json.dumps({
            'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'Fixture response decoded by the installed SDK.'}]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 4, 'totalTokenCount': 7},
            'modelVersion': 'fixture-model',
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
client = genai.Client(api_key='nonsecret-fixture', http_options={
    'base_url': f'http://127.0.0.1:{server.server_port}', 'api_version': 'v1',
})
try:
    response = client.models.generate_content(model='fixture-model', contents='Serialize this fixture prompt.')
    if len(requests) != 1:
        raise SystemExit('Google GenAI SDK did not issue exactly one fixture request')
    request = requests[0]
    if request['method'] != 'POST' or request['path'] != '/v1/models/fixture-model:generateContent':
        raise SystemExit('Google GenAI SDK request endpoint mismatch')
    contents = request['body']['contents']
    if contents != [{'role': 'user', 'parts': [{'text': 'Serialize this fixture prompt.'}]}]:
        raise SystemExit('Google GenAI SDK content serialization mismatch')
    if response.text != 'Fixture response decoded by the installed SDK.' or response.usage_metadata.total_token_count != 7:
        raise SystemExit('Google GenAI SDK generated response decoding mismatch')
    print(json.dumps({'sdk_version': importlib.metadata.version('google-genai'), 'request': request,
                      'response_text': response.text, 'total_tokens': response.usage_metadata.total_token_count}, sort_keys=True))
finally:
    client.close()
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
PY
# Actual signed output membership gates new proofs; dependencies never enroll a case.
cp /public-smoke.sh /fresh/root/public-smoke.sh
cp /expected.json /fresh/root/expected.json
python - <<'PY' > /evidence/public-targets.txt
import json
names={i['name'] for p in json.load(open('/expected.json'))['packages'] for i in p['files']}
groups={
    'carapace-bridge': {'carapace-bridge'}, 'carapace-spec-man': {'carapace-spec-man'},
    'regclient-regctl': {'regclient-regctl'}, 'regclient-regsync': {'regclient-regsync'},
    'regclient-regbot': {'regclient-regbot'},
    'looking-glass': {'looking-glass','looking-glass-module-dkms','obs-plugin-looking-glass'},
    'sentencepiece': {'sentencepiece','python-sentencepiece'}, 'surge-cli': {'surge'},
}
single=('pi','podcheck','spotify-adblock','spotify-remove-ad-banner','ttf-ioskeley-mono',
        'ttf-ioskeley-mono-unhinted','vet','airgorah','asleap',
        'computer-use-linux','i915ovmf','mdevctl','microsandbox',
        'captiveportalautologin-vith-git','carapace-spec','crush','fresh-editor','prek','swag','zig0.15')
groups.update({n:{n} for n in single})
for proof,outputs in sorted(groups.items()):
    if names & outputs:
        if not outputs <= names:
            raise SystemExit('incomplete split output proof enrollment: '+proof)
        print(proof)
PY
# Runtime is a separately fetched, fully hash-verified prerequisite, not package payload.
python - <<'PY'
import hashlib,json,pathlib,re,tarfile,urllib.request
packages=json.load(open('/expected.json'))['packages']
package=next((p for p in packages if p['pkgbase']=='microsandbox'),None)
if package is not None:
    source=next(s for s in package['source_lock']['sources'] if s['id']=='microsandbox-runtime')
    url=source['url']; digest=source['checksums']['sha256']
    if not url.startswith('https://github.com/superradcompany/microsandbox/releases/download/') or not re.fullmatch('[a-f0-9]{64}',digest):
        raise SystemExit('unsafe microsandbox runtime prerequisite')
    archive=pathlib.Path('/fresh/microsandbox-runtime.tar.gz')
    with urllib.request.urlopen(url,timeout=120) as response, archive.open('wb') as output:
        while block:=response.read(1024*1024):
            output.write(block)
    with archive.open('rb') as file:
        actual=hashlib.file_digest(file,'sha256').hexdigest()
    if actual!=digest:
        raise SystemExit('microsandbox runtime prerequisite digest mismatch')
    home=pathlib.Path('/fresh/root/fresh/data/public-proofs/microsandbox/home')
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        if {m.name for m in members}!={'msb','libkrunfw.so.5.6.1'} or not all(m.isfile() for m in members):
            raise SystemExit('unexpected runtime prerequisite archive members')
        for member in members:
            destination=home/('bin' if member.name=='msb' else 'lib')/member.name
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(tar.extractfile(member).read())
            destination.chmod(0o755)
    proof={'source':source,'sha256':digest,'role':'separately acquired runtime prerequisite, not package payload'}
    pathlib.Path('/evidence/microsandbox-prerequisite.json').write_text(json.dumps(proof,sort_keys=True)+'\n')
PY
chown -R 1000:1000 /fresh/root/fresh/data
mapfile -t public_targets < /evidence/public-targets.txt
for name in "${public_targets[@]}"; do
  offline_consumer bash /public-smoke.sh "$name" > "/evidence/$name-consumer.txt" 2>&1
done
if [[ -d /fresh/root/fresh/data/public-proofs ]]; then
  cp -a /fresh/root/fresh/data/public-proofs /evidence/
fi
printf 'All enrolled signed package consumer proofs passed (%s).\n' "${snapshot:-active}" > /evidence/result.txt
