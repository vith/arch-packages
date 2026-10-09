#!/usr/bin/env bash
# Trusted harness, run only as the capability-free offline consumer.
set -euo pipefail
name=${1:?package proof required}
consumer_scratch=/fresh/data/public-proofs/$name
mkdir -p "$consumer_scratch"
cd "$consumer_scratch"
export TMPDIR="$consumer_scratch"
case "$name" in
  carapace-bridge)
carapace-bridge _carapace export carapace-bridge ba > completion.json
bash --noprofile --norc -c 'source <(carapace-bridge _carapace bash); COMP_LINE="carapace-bridge ba"; COMP_POINT=${#COMP_LINE}; COMP_TYPE=9; _carapace-bridge_completion; printf "%s\n" "${COMPREPLY[@]}"' > completion.txt
[[ $(<completion.txt) == bash ]]
python - <<'PY'
import json
assert {x['value'] for x in json.load(open('completion.json'))['values']} == {'bash'}
PY
;;
  regclient-regctl)
[[ $(regctl ref localhost:5000/consumer/proof:offline --format '{{.Registry}}|{{.Repository}}|{{.Tag}}') == 'localhost:5000|consumer/proof|offline' ]]
;;
  regclient-regsync)
cat > config.yaml <<'YAML'
version: 1
defaults:
  skipDockerConfig: true
sync: []
YAML
regsync config --config config.yaml > parsed.yaml
python - <<'PY'
import re
text=open('parsed.yaml').read()
for pattern in (r'^version: 1$',r'^sync: \[\]$',r'^\s+skipDockerConfig: true$'):
    assert re.search(pattern,text,re.M), pattern
PY
regsync check --config config.yaml
if regsync check --config - > invalid.txt 2>&1 <<<'version: 999'; then exit 1; fi
[[ $(<invalid.txt) == *'unsupported config version'* ]]
;;
  regclient-regbot)
cat > config.yaml <<'YAML'
version: 1
defaults:
  skipDockerConfig: true
scripts: []
YAML
regbot once --dry-run --config config.yaml
if regbot once --dry-run --config - > invalid.txt 2>&1 <<<'version: 999'; then exit 1; fi
[[ $(<invalid.txt) == *'unsupported config version'* ]]
;;
  carapace-spec-man)
mkdir -p man/man1
cat > man/man1/aurproof.1 <<'ROFF'
.TH AURPROOF 1 "2026-01-01" "consumer proof"
.SH NAME
aurproof \- demonstrate offline completion conversion
.SH SYNOPSIS
.B aurproof
.RI [ options ]
.SH OPTIONS
.TP
.B \-\-offline
Use local files only.
ROFF
MANPATH="$PWD/man" mandb --quiet --create "$PWD/man"
MANPATH="$PWD/man" MANWIDTH=80 carapace-spec-man aurproof > parsed.yaml
python - <<'PY'
import re
text=open('parsed.yaml').read()
for pattern in (r'^name: aurproof$', r'^description: demonstrate offline completion conversion$', r'^    --offline: Use local files only\.?$'):
    assert re.search(pattern,text,re.M), pattern
PY
;;
  airgorah)
python - <<'PY'
import os, subprocess
assert os.geteuid() != 0
p = subprocess.run(['airgorah-agent'], capture_output=True, text=True, timeout=5)
assert p.returncode == 1 and 'airgorah-agent must run as root' in p.stderr, p
PY

;;
  asleap)
python - <<'PY'
import pathlib, subprocess
p = subprocess.run(['genkeys'], capture_output=True, text=True, timeout=5)
assert p.returncode == 1 and 'Must supply -r -f and -n' in p.stdout and 'Input dictionary file' in p.stdout, p
assert list(pathlib.Path.cwd().iterdir()) == []
PY

;;
  captiveportalautologin-vith-git)
python - <<'PY'
import subprocess
p = subprocess.run(['captiveportalautologin', '--n3t-invalid-option'], text=True, capture_output=True, timeout=20)
assert p.returncode != 0
assert 'no such option' in (p.stdout + p.stderr).lower()
assert '--n3t-invalid-option' in p.stdout + p.stderr
assert 'CaptivePortalAutoLogin for Linux' not in p.stdout + p.stderr
PY
;;
  carapace-spec)
python - <<'PY'
import pathlib, tempfile, subprocess
scratch = pathlib.Path.home() / '.local/state/arch-packages/consumer-smoke'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as directory:
    spec = pathlib.Path(directory) / 'invalid.yaml'
    spec.write_text('name: [unterminated\n')
    result = subprocess.run(['carapace-spec', 'codegen', str(spec)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'yaml:' in result.stderr and ('expected' in result.stderr or 'did not find' in result.stderr), result
PY
;;
  computer-use-linux)
python - <<'PY'
import os, subprocess
p = subprocess.run(['computer-use-linux','__arch_smoke_invalid__'], env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS='unix:path=/fresh/data/nonexistent-bus'), capture_output=True, text=True, timeout=10)
assert p.returncode != 0 and "unknown command '__arch_smoke_invalid__'" in p.stderr, p
assert 'Expected one of: mcp, doctor, setup, guard-accessibility, apps, state, screenshot, windows, setup-window-targeting' in p.stderr, p
PY

;;
  crush)
python - <<'PY'
import json, subprocess
schema = json.loads(subprocess.check_output(['crush', 'schema'], text=True))
assert schema['$schema'] == 'https://json-schema.org/draft/2020-12/schema'
definitions = schema['$defs']
assert 'providers' in definitions['Config']['properties']
assert 'options' in definitions['Config']['properties']
provider = definitions['ProviderConfig']['properties']['type']
assert 'openai' in provider['enum']
PY
;;
  fresh-editor)
python3 - <<'PY'
import json, os, pathlib, subprocess, tempfile
scratch = pathlib.Path.home() / '.local/state/arch-packages/consumer-smoke'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as d:
    config = pathlib.Path(d) / 'config.json'
    config.write_text(json.dumps({'check_for_updates': False, 'self_update': False, 'orchestrator_mode': False}))
    env = dict(os.environ, HOME=d, XDG_CONFIG_HOME=d, XDG_DATA_HOME=d, XDG_CACHE_HOME=d, XDG_STATE_HOME=d)
    result = subprocess.run(['fresh', '--no-upgrade-check', '--safe', '--config', str(config), '--cmd', 'config', 'show'], cwd=d, env=env, capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output['check_for_updates'] is False and output['self_update'] is False and output['orchestrator_mode'] is False, output
PY
;;
  i915ovmf)
python - <<'PY'
from pathlib import Path
import struct
rom = Path('/var/lib/libvirt/qemu/drivers/i915ovmf.rom').read_bytes()
u16 = lambda n: struct.unpack_from('<H', rom, n)[0]
u32 = lambda n: struct.unpack_from('<I', rom, n)[0]
assert rom[:2] == b'\x55\xaa', 'PCI expansion ROM signature'
assert len(rom) == u16(2) * 512, 'complete image with 512-byte ROM alignment'
assert u32(4) == 0x0ef1, 'EFI option ROM signature'
assert u16(8) == 11 and u16(10) == 0x8664, 'X64 EFI boot-service driver'
assert u16(12) == 0, 'uncompressed EFI payload as declared by upstream INF'
pcir = u16(24)
assert rom[pcir:pcir+4] == b'PCIR', 'PCI data structure'
assert u16(pcir+4) == 0x8086 and u16(pcir+6) == 0x1926, 'declared Intel GPU identity'
assert rom[pcir+13:pcir+16] == b'\x00\x00\x03', 'display controller class'
assert u16(pcir+16) * 512 == len(rom), 'PCI image length'
assert rom[pcir+20] == 3 and rom[pcir+21] & 0x80, 'single final EFI image'
image = u16(22)
assert pcir + u16(pcir+10) <= image < len(rom), 'payload follows PCI headers'
pe = image + u32(image+60) if rom[image:image+2] == b'MZ' else image
assert rom[pe:pe+4] == b'PE\x00\x00', 'loadable PE/COFF payload'
assert u16(pe+4) == 0x8664 and u16(pe+24) == 0x20b, 'AMD64 PE32+ image'
assert u16(pe+24+68) == 11, 'PE EFI boot-service subsystem'
assert 0 < u32(pe+24+16) < u32(pe+24+56), 'entrypoint within loaded image'
print('i915ovmf: validated native X64 Intel graphics EFI option ROM')
PY
;;
  looking-glass)
python - <<'PY'
import pathlib, subprocess
missing = str(pathlib.Path.cwd() / 'does-not-exist.ini')
p = subprocess.run(['looking-glass-client', 'app:configFile='+missing], capture_output=True, text=True, timeout=10)
assert p.returncode != 0 and 'app:configFile set to invalid file: '+missing in p.stdout+p.stderr, p
source = pathlib.Path('/usr/src/looking-glass-B7')
config = (source/'dkms.conf').read_text()
assert 'kvmfr' in config and 'BUILT_MODULE_NAME' in config
assert (source/'kvmfr.c').is_file() and (source/'kvmfr.h').is_file()
symbols = subprocess.check_output(['readelf','-Ws','/usr/lib/obs-plugins/liblooking-glass-obs.so'], text=True)
for name in ['obs_module_load','obs_module_description']:
    assert any(row.split()[-1] == name and row.split()[-2] != 'UND' for row in symbols.splitlines() if len(row.split()) >= 8), name
PY

;;
  mdevctl)
python3 - <<'PY'
import subprocess
for binary in ('mdevctl', 'lsmdev'):
    args = [binary, 'list', '--uuid', 'not-a-uuid'] if binary == 'mdevctl' else [binary, '--uuid', 'not-a-uuid']
    result = subprocess.run(args, text=True, capture_output=True)
    assert result.returncode == 2, (binary, result.returncode, result.stderr)
    assert 'invalid value' in result.stderr and 'not-a-uuid' in result.stderr, result.stderr
PY
;;
  pi)
node --input-type=module <<'JS'
import assert from 'node:assert/strict';
import { parseArgs } from '/usr/lib/node_modules/pi/packages/coding-agent/dist/cli/args.js';
const args = parseArgs(['--offline', '--mode', 'json', '--tools', 'read, bash', '--', '--literal', '@input.txt']);
assert.equal(args.offline, true);
assert.equal(args.mode, 'json');
assert.deepEqual(args.tools, ['read', 'bash']);
assert.deepEqual(args.messages, ['--literal']);
assert.deepEqual(args.fileArgs, ['input.txt']);
assert.deepEqual(parseArgs(['--mode', 'invalid']).diagnostics, [{type:'error', message:'Invalid mode "invalid". Valid values: text, json, rpc'}]);
JS
;;
  podcheck)
# Run in the hosted disposable consumer container: upstream EXIT cleanup removes /tmp/podcheck-*.
HOME="$consumer_scratch/home" podcheck -Z > "$consumer_scratch/podcheck-error" 2>&1 && exit 1 || status=$?
[[ "$status" == 2 ]]
grep -F 'invalid option --' "$consumer_scratch/podcheck-error"
grep -F 'Syntax:     podcheck.sh [OPTION]' "$consumer_scratch/podcheck-error"
# -Z exits during getopts, before registry/container/network discovery.

;;
  prek)
python3 - <<'PY'
import os, pathlib, subprocess, tempfile
scratch = pathlib.Path.home() / '.local/state/arch-packages/consumer-smoke'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as d:
    config = pathlib.Path(d) / '.pre-commit-config.yaml'
    env = dict(os.environ, HOME=d, PREK_HOME=d, XDG_CONFIG_HOME=d, XDG_CACHE_HOME=d, XDG_STATE_HOME=d)
    config.write_text('repos: []\n')
    args = ['prek', '--no-log-file', 'validate-config', str(config)]
    good = subprocess.run(args, cwd=d, env=env, capture_output=True, text=True)
    assert good.returncode == 0 and 'All configs are valid' in good.stderr, good
    config.write_text('repos:\n  - repo: local\n    hooks:\n      - name: missing-required-id\n')
    bad = subprocess.run(args, cwd=d, env=env, capture_output=True, text=True)
    assert bad.returncode == 1 and 'missing field' in bad.stderr and 'id' in bad.stderr, bad
PY
;;
  sentencepiece)
test "$(printf 'ＡＢＣ  test\n' | spm_normalize --normalization_rule_name=nfkc)" = 'ABC test'
python - <<'PY'
import sentencepiece as spm
n = spm.SentencePieceNormalizer(norm_map=[('foo', 'bar'), ('apple', 'orange')])
assert n.normalize('foo apple') == 'bar orange'
assert n.decompile() == [('apple', 'orange'), ('foo', 'bar')]
PYpython - <<'PY'
from pathlib import Path
import sentencepiece as spm
corpus = Path('corpus.txt')
corpus.write_text(('alpha beta gamma\nalpha beta delta\ngamma delta alpha\n') * 30)
spm.SentencePieceTrainer.train(input=str(corpus), model_prefix='proof', vocab_size=32, model_type='bpe', character_coverage=1.0, shuffle_input_sentence=False, num_threads=1, hard_vocab_limit=False)
p = spm.SentencePieceProcessor(model_file='proof.model')
for text in ['alpha beta', 'gamma delta alpha']:
    ids = p.encode(text, out_type=int)
    assert p.decode(ids) == text
    assert p.encode(text, out_type=str) == [p.id_to_piece(i) for i in ids]
PY

;;
  spotify-adblock)
XDG_CONFIG_HOME="$consumer_scratch/empty-config" LD_PRELOAD=/usr/lib/spotify-adblock.so python - <<'PY'
import socket
try:
    socket.getaddrinfo('migration-denied.invalid', 443)
except socket.gaierror as error:
    assert error.errno == socket.EAI_FAIL, error
else:
    raise AssertionError('deny-by-default domain unexpectedly resolved')
addresses={row[4][0] for row in socket.getaddrinfo('localhost',443)}
assert addresses & {'127.0.0.1','::1'} and addresses <= {'127.0.0.1','::1'}, addresses
PY
;;
  spotify-remove-ad-banner)
# Exercise installed scripts unchanged, virtualizing only their hardcoded cd into a private fixture.
export spotify_fixture="$consumer_scratch/spotify-apps"
mkdir -p "$spotify_fixture"
printf 'before;adsEnabled:!0;after' > "$spotify_fixture/xpui-snapshot.js"
(cd "$spotify_fixture" && zip -q xpui.spa xpui-snapshot.js && rm xpui-snapshot.js)
cp "$spotify_fixture/xpui.spa" "$spotify_fixture/original.spa"
cd() { [[ "$1" == /opt/spotify/Apps ]] || return 99; builtin cd "$spotify_fixture"; }
export -f cd
bash /usr/share/spotify-remove-ad-banner/remove.sh
[[ "$(unzip -p "$spotify_fixture/xpui.spa" xpui-snapshot.js)" == 'before;adsEnabled:false;after' ]]
cmp "$spotify_fixture/original.spa" "$spotify_fixture/xpui.spa.bak"
bash /usr/share/spotify-remove-ad-banner/restore.sh
cmp "$spotify_fixture/original.spa" "$spotify_fixture/xpui.spa"
[[ ! -e "$spotify_fixture/xpui.spa.bak" ]]
unset -f cd

;;
  surge-cli)
python3 - <<'PY'
import subprocess
result = subprocess.run(['surge', 'completion', 'zsh'], capture_output=True, text=True, check=True)
assert '#compdef surge' in result.stdout and '_surge' in result.stdout and 'config' in result.stdout, result.stdout
PY
;;
  swag)
python - <<'PY'
import json, pathlib, subprocess, tempfile, os
scratch = pathlib.Path.home() / '.local/state/arch-packages/consumer-smoke'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as directory:
    root = pathlib.Path(directory)
    (root / 'main.go').write_text('package main\n// @title Native Smoke API\n// @version 1.0\n// @description Offline parser verification\nfunc main() {}\n// ping godoc\n// @Summary Ping\n// @Success 200 {string} string "pong"\n// @Router /ping [get]\nfunc ping() {}\n')
    env = dict(os.environ, GOPROXY='off', GOSUMDB='off')
    subprocess.run(['swag', 'init', '--parseGoList=false', '--outputTypes=json', '--output', 'docs'], cwd=root, env=env, check=True)
    schema = json.loads((root / 'docs/swagger.json').read_text())
    assert schema['swagger'] == '2.0'
    assert schema['info']['title'] == 'Native Smoke API'
    assert schema['paths']['/ping']['get']['summary'] == 'Ping'
    assert schema['paths']['/ping']['get']['responses']['200']['schema']['type'] == 'string'
PY
;;
  ttf-ioskeley-mono)
python - <<'PY'
from pathlib import Path
import struct
HINTED = True
for filename, expected_family in [('/usr/share/fonts/TTF/IoskeleyMono-Regular.ttf', 'Ioskeley Mono'), ('/usr/share/fonts/TTF/IoskeleyMonoTerm-Regular.ttf', 'Ioskeley Mono Term'), ('/usr/share/fonts/TTF/IoskeleyMonoNL-Regular.ttf', 'Ioskeley Mono NL')]:
    data = Path(filename).read_bytes()
    assert data[:4] == b'\x00\x01\x00\x00', filename
    count = struct.unpack_from('>H', data, 4)[0]
    tables = {data[12+i*16:16+i*16].decode('ascii'): struct.unpack_from('>II', data, 20+i*16) for i in range(count)}
    assert {'cmap','glyf','name'} <= tables.keys()
    assert HINTED == {'fpgm','prep','cvt '}.issubset(tables), filename
    start, size = tables['name']; _, n, strings = struct.unpack_from('>HHH', data, start)
    families = []
    for i in range(n):
        platform, encoding, language, nameid, length, offset = struct.unpack_from('>HHHHHH', data, start+6+i*12)
        if nameid == 1 and platform in (0,3):
            families.append(data[start+strings+offset:start+strings+offset+length].decode('utf-16-be'))
    assert expected_family in families, (filename, families)
PY
;;
  ttf-ioskeley-mono-unhinted)
python - <<'PY'
from pathlib import Path
import struct
for filename, expected_family in [('/usr/share/fonts/ttf/ttf-ioskeley-mono-unhinted/IoskeleyMono-Regular.ttf', 'Ioskeley Mono')]:
    data = Path(filename).read_bytes()
    assert data[:4] == b'\x00\x01\x00\x00', filename
    count = struct.unpack_from('>H', data, 4)[0]
    tables = {data[12+i*16:16+i*16].decode('ascii'): struct.unpack_from('>II', data, 20+i*16) for i in range(count)}
    assert {'cmap','glyf','name'} <= tables.keys()
    assert not {'fpgm','prep','cvt '} & tables.keys(), filename
    start, size = tables['name']; _, n, strings = struct.unpack_from('>HHH', data, start)
    families = []
    for i in range(n):
        platform, encoding, language, nameid, length, offset = struct.unpack_from('>HHHHHH', data, start+6+i*12)
        if nameid == 1 and platform in (0,3):
            families.append(data[start+strings+offset:start+strings+offset+length].decode('utf-16-be'))
    assert expected_family in families, (filename, families)
PY
;;
  vet)
python - <<'PY'
import subprocess
result = subprocess.run(['vet'], capture_output=True, text=True)
assert result.returncode == 1, result
assert 'ERROR: No URL provided.' in result.stderr
assert 'vet [OPTIONS] <URL> [SCRIPT_ARGUMENTS...]' in result.stdout
result = subprocess.run(['vet', '--not-an-option'], capture_output=True, text=True)
assert result.returncode == 1
assert 'Unknown option: --not-an-option' in result.stderr
PY
;;
  zig0.15)
# Run inside the hosted offline consumer scratch directory, not on the workstation.
cat > factorial.zig <<'ZIG'
const std = @import("std");
fn factorial(n: u32) u32 {
    var result: u32 = 1;
    var i: u32 = 2;
    while (i <= n) : (i += 1) result *= i;
    return result;
}
pub fn main() !void {
    var buffer: [64]u8 = undefined;
    const text = try std.fmt.bufPrint(&buffer, "factorial(6)={d}\n", .{factorial(6)});
    try std.fs.File.stdout().writeAll(text);
}
ZIG
zig-0.15 build-exe factorial.zig -target x86_64-linux-musl -lc -O Debug -femit-bin=factorial --cache-dir "$PWD/zig-local-cache" --global-cache-dir "$PWD/zig-global-cache"
test "$(./factorial)" = 'factorial(6)=720'

;;
  virtio-win)
python - <<'PY'
import hashlib,json,pathlib,struct,subprocess,urllib.parse
package=next(p for p in json.load(open('/expected.json'))['packages'] if p['pkgbase']=='virtio-win')
sources=package['source_lock']['sources']
iso_source=next(s for s in sources if s['id']=='virtio-win-iso')
iso=pathlib.Path('/var/lib/libvirt/images/virtio-win.iso')
def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file,'sha256').hexdigest()
assert digest(iso)==iso_source['checksums']['sha256']
members=subprocess.check_output(['bsdtar','-tf',str(iso)],text=True).splitlines()
# The other viostor paths are ISO hardlink aliases: extract the physical first files.
required={'amd64/w11/viostor.inf','amd64/w11/viostor.sys',
          'guest-agent/qemu-ga-x86_64.msi','virtio-win_license.txt'}
assert required <= set(members), required-set(members)
def content(member):
    return subprocess.check_output(['bsdtar','-xOf',str(iso),member])
inf=content('amd64/w11/viostor.inf')
text=inf.decode('utf-16' if inf.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
assert 'PCI\\VEN_1AF4' in text.upper()
driver=content('amd64/w11/viostor.sys')
assert driver[:2]==b'MZ'
pe=struct.unpack_from('<I',driver,60)[0]
assert driver[pe:pe+4]==b'PE\0\0'
assert struct.unpack_from('<H',driver,pe+4)[0]==0x8664
assert content('guest-agent/qemu-ga-x86_64.msi')[:8]==bytes.fromhex('d0cf11e0a1b11ae1')
source=next(s for s in sources if s['id']=='virtio-win-source-rpm')
# The installed original SRPM filename is the upstream basename, not the recipe alias.
filename=urllib.parse.urlsplit(source['url']).path.rsplit('/',1)[-1]
srpm=pathlib.Path('/usr/share/doc/virtio-win/sources')/filename
assert digest(srpm)==source['checksums']['sha256']
source_members=subprocess.check_output(['bsdtar','-tf',str(srpm)],text=True).splitlines()
assert 'mingw-qemu-ga-win-110.2.3-2.el10.src.rpm' in source_members
pathlib.Path('iso-members.txt').write_text('\n'.join(members)+'\n')
pathlib.Path('srpm-members.txt').write_text('\n'.join(source_members)+'\n')
pathlib.Path('viostor.inf').write_text(text)
license_bytes=content('virtio-win_license.txt')
assert license_bytes==pathlib.Path('/usr/share/licenses/virtio-win/virtio-win_license.txt').read_bytes()
pathlib.Path('virtio-win_license.txt').write_bytes(license_bytes)
PY
;;
  microsandbox)
python - <<'PY'
import hashlib,json,os,pathlib,re,subprocess
home=pathlib.Path.cwd()/'home'
pair=[home/'bin/msb',home/'lib/libkrunfw.so.5.6.1']
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pair}
env=dict(os.environ,MSB_HOME=str(home),MSB_CONFIG_PATH=str(pathlib.Path.cwd()/'absent-config.json'))
package=next(p for p in json.load(open('/expected.json'))['packages'] if p['pkgbase']=='microsandbox')
version=package['source_lock']['version'].rsplit('-',1)[0]
assert not pathlib.Path('/dev/kvm').exists()
for binary in ['microsandbox','msb']:
    result=subprocess.run([binary,'doctor'],env=env,capture_output=True,text=True,timeout=30)
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',result.stdout+result.stderr)
    pathlib.Path(binary+'-doctor.txt').write_text(text)
    assert result.returncode==1 and 'v'+version in text and '/dev/kvm is not present' in text, result
    for label,path in zip(['msb','libkrunfw'],pair):
        assert any('✓' in row and label in row and str(path) in row for row in text.splitlines()), text
    assert any('✗' in row and 'KVM device missing' in row for row in text.splitlines()), text
assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pair}
absent=pathlib.Path.cwd()/'absent-home'
badenv=dict(os.environ,MSB_HOME=str(absent),MSB_CONFIG_PATH=str(pathlib.Path.cwd()/'absent-config.json'),
            MSB_PATH=str(pathlib.Path.cwd()/'absent-msb'),
            MSB_LIBKRUNFW_PATH=str(pathlib.Path.cwd()/'absent-libkrunfw.so.5.6.1'))
result=subprocess.run(['microsandbox','doctor'],env=badenv,capture_output=True,text=True,timeout=10)
text=result.stdout+result.stderr
assert result.returncode==127 and 'microsandbox: failed to prepare runtime:' in text and 'expected both' in text, result
assert badenv['MSB_PATH'] in text and badenv['MSB_LIBKRUNFW_PATH'] in text
assert not absent.exists()
assert not (home/'images').exists()
PY
;;
  *) echo "Unknown public consumer proof: $name" >&2; exit 2 ;;
esac
case "$name" in
  airgorah) echo 'Scope: non-root privilege refusal only; no GUI, polkit, wireless capture or audit exercised.' ;;
  asleap) echo 'Scope: required dictionary-argument rejection only; no password recovery or capture/attack exercised.' ;;
  computer-use-linux) echo 'Scope: unknown-command rejection only; no desktop, accessibility, screenshot, input or MCP backend exercised.' ;;
  captiveportalautologin-vith-git) echo 'Scope: invalid-option rejection only; no real captive portal or VPN/network-state exercise.' ;;
  fresh-editor) echo 'Scope: safe offline config load/show only; no interactive TUI editing exercised.' ;;
  looking-glass) echo 'Scope: pre-run config rejection, DKMS source and OBS exported symbols; no graphical rendering, VM transport, OBS launch or module build/load.' ;;
  i915ovmf) echo 'Scope: PCI/EFI option-ROM and AMD64 PE loader structure; no Intel GPU passthrough or firmware execution.' ;;
  virtio-win) echo 'Scope: ISO driver/source/license content; no Windows VM boot or driver installation.' ;;
  microsandbox) echo 'Scope: genuine separately verified runtime doctor and fail-closed configuration; missing KVM is expected, no VM or image boot.' ;;
  ttf-ioskeley-mono|ttf-ioskeley-mono-unhinted) echo 'Scope: TrueType data, family and hint-table invariants; no GUI rendering.' ;;
  pi) echo 'Scope: installed offline CLI argument parser; no provider request or interactive session.' ;;
  spotify-adblock|spotify-remove-ad-banner) echo 'Scope: socket-filter behavior or private archive patch/restore fixture; signed Spotify dependency installed, no Spotify GUI or playback.' ;;
esac
printf '%s: offline consumer assertions passed\n' "$name"
