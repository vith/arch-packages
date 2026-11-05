#!/usr/bin/env bash
# Trusted harness, run only as the capability-free offline consumer.
set -euo pipefail
name=${1:?package proof required}
# Dependencies do not enroll proofs. Only signed snapshot outputs do.
python - "$name" <<'PY'
import json, sys
name = sys.argv[1]
names = {i['name'] for p in json.load(open('/expected.json'))['packages'] for i in p['files']}
groups = {
    'looking-glass': {'looking-glass', 'looking-glass-module-dkms', 'obs-plugin-looking-glass'},
    'sentencepiece': {'sentencepiece', 'python-sentencepiece'},
    'surge-cli': {'surge'},
}
if not groups.get(name, {name}) <= names:
    raise SystemExit('consumer proof is not enrolled in the selected signed snapshot')
PY
consumer_scratch=/fresh/data/public-proofs/$name
mkdir -p "$consumer_scratch"
cd "$consumer_scratch"
export TMPDIR="$consumer_scratch"
completion_values() {
  python - "$@" <<'PY'
import json, sys
actual = {item['value'] for item in json.load(open(sys.argv[1]))['values']}
expected = set(sys.argv[2:])
assert actual == expected, (actual, expected)
PY
}
case "$name" in
  arping-th)
python - <<'PY'
import subprocess
p = subprocess.run(['arping-th', '-c', '-1'], capture_output=True, text=True, timeout=5)
assert p.returncode == 1, p
assert '-1' in p.stderr and not p.stdout, p
print(p.stderr, end='')
PY
;;
  batdoc)
python - <<'PY'
import pathlib, subprocess, zipfile
document = pathlib.Path('consumer.docx')
with zipfile.ZipFile(document, 'w') as archive:
    archive.writestr('word/document.xml', '''<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Offline document &amp; Unicode café</w:t></w:r></w:p>
<w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
</w:body></w:document>''')
p = subprocess.run(['batdoc', '--plain', str(document)], capture_output=True, text=True, timeout=20, check=True)
assert p.stdout.splitlines() == ['Offline document & Unicode café', '', 'Second paragraph'], repr(p.stdout)
print(p.stdout, end='')
PY
;;
  ccze)
python - <<'PY'
import re, subprocess
line = 'Jan  2 03:04:05 proofhost consumer[123]: offline log message\n'
p = subprocess.run(['ccze', '-A', '-p', 'syslog', '-o', 'nolookups'], input=line, capture_output=True, text=True, timeout=5, check=True)
assert re.search(r'\x1b\[[0-9;]*m', p.stdout), repr(p.stdout)
plain = re.sub(r'\x1b\[[0-9;]*m', '', p.stdout)
# Terminal padding is not log content.
assert line.rstrip('\n') in plain, repr(plain)
print(p.stdout, end='')
PY
;;
  crunch)
python - <<'PY'
import subprocess
p = subprocess.run(['crunch', '1', '2', 'ab'], capture_output=True, text=True, timeout=10, check=True)
assert p.stdout.splitlines() == ['a', 'b', 'aa', 'ab', 'ba', 'bb'], p
print(p.stdout, end='')
PY
;;
  fish-tide)
mkdir -p home config data cache
HOME="$PWD/home" XDG_CONFIG_HOME="$PWD/config" XDG_DATA_HOME="$PWD/data" XDG_CACHE_HOME="$PWD/cache" fish --no-config > prompt.txt <<'FISH'
set -g fish_function_path /etc/fish/functions $fish_function_path
set -g _tide_side left
set -g _tide_pad ''
set -g _tide_left_items status
set -g tide_left_prompt_prefix '['
set -g tide_status_color white
set -g tide_status_bg_color black
set -g tide_status_color_failure white
set -g tide_status_bg_color_failure red
set -g tide_status_icon OK
set -g tide_status_icon_failure FAIL
set -g _tide_pipestatus 0
set -g _tide_status 0
set -g add_prefix true
_tide_item_status
printf '\n'
set -g _tide_pipestatus 1
set -g _tide_status 1
set -g add_prefix true
_tide_item_status
printf '\n'
FISH
python - <<'PY'
import pathlib, re
text = pathlib.Path('prompt.txt').read_text()
plain = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\([A-Z0-9])', '', text)
assert plain.splitlines() == ['[OK', '[FAIL 1'], repr(plain)
print(text, end='')
PY
;;
  nwipe)
python - <<'PY'
import os, subprocess
assert os.geteuid() != 0
p = subprocess.run(['nwipe'], capture_output=True, text=True, timeout=5)
assert p.returncode == 99, p
print(p.stdout, end='')
PY
;;
  opentracker-cli)
python - <<'PY'
import hashlib, os, pathlib, subprocess
home = pathlib.Path.cwd() / 'home'
config = home / '.config/opentracker/config.json'
config.parent.mkdir(parents=True)
def state():
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.rglob('*') if p.is_file()}
fixture = b'{"providers": @N3T_CONSUMER_INVALID}\n'
config.write_bytes(fixture)
before = state()
p = subprocess.run(['opentracker', 'fetch', 'opencode'], env=dict(os.environ, HOME=str(home)),
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
assert p.returncode == 1 and not p.stdout, p
# The offending byte exists only inside the configuration, not the command or path.
assert '@' in p.stderr, p
assert config.read_bytes() == fixture and state() == before
print(p.stderr, end='')
PY
;;
  terminus-font-ttf)
python - <<'PY'
import pathlib, subprocess
files = pathlib.Path('/terminus-font-ttf-files.txt').read_text().splitlines()
fonts = sorted(line.split(' ', 1)[1] for line in files
               if line.startswith('terminus-font-ttf /') and line.endswith('.ttf'))
assert fonts, 'no packaged fonts available to fontconfig'
for font in fonts:
    assert pathlib.Path(font).is_file(), font
    fields = subprocess.check_output(['fc-scan', '--format', '%{family}\n%{style}\n%{spacing}\n%{charset}\n', font], text=True).splitlines()
    assert len(fields) == 4 and fields[0] == 'Terminus (TTF)', (font, fields)
    assert fields[1] and fields[2] == '100', (font, fields)
    ranges = [tuple(int(n, 16) for n in item.split('-')) for item in fields[3].split()]
    assert all(any(r[0] <= code <= r[-1] for r in ranges) for code in (0x41, 0x61, 0xe9)), (font, fields)
    print(font, fields[0], fields[1], 'monospaced Latin glyphs')
PY
;;
  python-ftfy)
python - <<'PY'
import ftfy
for broken, repaired in [('âœ” No problems', '✔ No problems'), ('l’humanitÃ©', "l'humanité"), ('IL Y MARQUÉ…', 'IL Y MARQUÉ…')]:
    actual = ftfy.fix_text(broken)
    assert actual == repaired, (broken, actual, repaired)
    print(repr(broken), '->', repr(actual))
PY
;;
  carapace-bridge)
carapace-bridge _carapace export carapace-bridge ba > completion.json
bash --noprofile --norc -c 'source <(carapace-bridge _carapace bash); COMP_LINE="carapace-bridge ba"; COMP_POINT=${#COMP_LINE}; COMP_TYPE=9; _carapace-bridge_completion; printf "%s\n" "${COMPREPLY[@]}"' > completion.txt
[[ $(<completion.txt) == bash ]]
completion_values completion.json bash
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
cat > spec.yaml <<'YAML'
name: consumer-proof
flags:
  --format=: output format
completion:
  flag:
    format: [json, text]
YAML
carapace-spec "$PWD/spec.yaml" export consumer-proof --format j > completion.json
completion_values completion.json json
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
print(json.dumps(schema, sort_keys=True))
PY
;;
  fresh-editor)
python3 - <<'PY'
import json, os, pathlib, subprocess, tempfile
scratch = pathlib.Path.home() / '.local/state/arch-packages/consumer-smoke'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as d:
    config = pathlib.Path(d) / 'config.json'
    config.write_text(json.dumps({'check_for_updates': False, 'self_update': False}))
    env = dict(os.environ, HOME=d, XDG_CONFIG_HOME=d, XDG_DATA_HOME=d, XDG_CACHE_HOME=d, XDG_STATE_HOME=d)
    result = subprocess.run(['fresh', '--no-upgrade-check', '--safe', '--config', str(config), '--cmd', 'config', 'show'], cwd=d, env=env, capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output['check_for_updates'] is False and output['self_update'] is False, output
    print(json.dumps(output, sort_keys=True))
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
status=0
HOME="$consumer_scratch/home" podcheck -Z > "$consumer_scratch/podcheck-error" 2>&1 || status=$?
if [[ "$status" != 2 ]]; then
  printf 'podcheck: invalid option returned %s instead of 2\n' "$status" >&2
  cat "$consumer_scratch/podcheck-error" >&2
  exit 1
fi
printf 'podcheck: rejected invalid option with exit status %s\n' "$status"
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
PY
python - <<'PY'
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
# Model an existing archive: zip --update only replaces members with newer timestamps.
touch -t 200001010000 "$spotify_fixture/xpui-snapshot.js"
(cd "$spotify_fixture" && zip -q xpui.spa xpui-snapshot.js && rm xpui-snapshot.js)
cp "$spotify_fixture/xpui.spa" "$spotify_fixture/original.spa"
cd() { [[ "$1" == /opt/spotify/Apps ]] || return 99; builtin cd "$spotify_fixture"; }
export -f cd
bash /usr/share/spotify-remove-ad-banner/remove.sh
[[ "$(unzip -p "$spotify_fixture/xpui.spa" xpui-snapshot.js)" == 'before;adsEnabled:false;after' ]] || {
  printf '%s\n' 'Spotify banner fixture transformation failed; archive inventory follows:' >&2
  unzip -l "$spotify_fixture/xpui.spa" >&2
  exit 1
}
cmp "$spotify_fixture/original.spa" "$spotify_fixture/xpui.spa.bak"
bash /usr/share/spotify-remove-ad-banner/restore.sh
cmp "$spotify_fixture/original.spa" "$spotify_fixture/xpui.spa"
[[ ! -e "$spotify_fixture/xpui.spa.bak" ]] || {
  printf '%s\n' 'Spotify banner fixture backup remains after restoration' >&2
  exit 1
}
unset -f cd

;;
  surge-cli)
python3 - <<'PY'
import subprocess
result = subprocess.run(['surge', '__complete', 'completion', 'z'], capture_output=True, text=True, check=True)
lines = result.stdout.splitlines()
assert lines and lines[-1].startswith(':'), result.stdout
assert not int(lines[-1][1:]) & 1, result.stdout
assert {line.split('\t', 1)[0] for line in lines[:-1]} == {'zsh'}, result.stdout
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
    print(f'{binary} doctor exit={result.returncode}\n{text}',flush=True)
    assert result.returncode==1, result
    assert re.search(r'(?<![0-9A-Za-z.])v?'+re.escape(version)+r'(?![0-9A-Za-z.])',text), text
assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pair}
absent=pathlib.Path.cwd()/'absent-home'
badenv=dict(os.environ,MSB_HOME=str(absent),MSB_CONFIG_PATH=str(pathlib.Path.cwd()/'absent-config.json'),
            MSB_PATH=str(pathlib.Path.cwd()/'absent-msb'),
            MSB_LIBKRUNFW_PATH=str(pathlib.Path.cwd()/'absent-libkrunfw.so.5.6.1'))
result=subprocess.run(['microsandbox','doctor'],env=badenv,capture_output=True,text=True,timeout=10)
text=result.stdout+result.stderr
print(f'microsandbox doctor with invalid explicit runtime exit={result.returncode}\n{text}',flush=True)
assert result.returncode==127, result
assert not absent.exists()
assert not (home/'images').exists()
PY
;;
  *) echo "Unknown public consumer proof: $name" >&2; exit 2 ;;
esac
case "$name" in
  arping-th) echo 'Scope: negative packet-count rejection before network initialization; no ARP traffic or interface enumeration.' ;;
  batdoc) echo 'Scope: local OOXML text extraction with entity/Unicode decoding and paragraph boundaries; no OCR, model download or external viewer.' ;;
  ccze) echo 'Scope: offline syslog ANSI colorization preserving message text; lookups disabled.' ;;
  crunch) echo 'Scope: exact six-entry local alphabet enumeration only.' ;;
  fish-tide) echo 'Scope: installed status prompt item and color renderer for success/failure in isolated noninteractive fish; no interactive shell installation.' ;;
  nwipe) echo 'Scope: non-root refusal before configuration or device discovery; no disk enumeration, benchmark or wipe.' ;;
  opentracker-cli) echo 'Scope: marked malformed local configuration rejection with unchanged input; no credentials, provider/account fetching or usage cache fabrication.' ;;
  terminus-font-ttf) echo 'Scope: installed fontconfig parsing of all four packaged fonts, family, monospacing and Latin glyph coverage; no GUI rendering.' ;;
  python-ftfy) echo 'Scope: real mojibake repair, curly-quote normalization and already-correct Unicode preservation.' ;;
  airgorah) echo 'Scope: non-root privilege refusal only; no GUI, polkit, wireless capture or audit exercised.' ;;
  asleap) echo 'Scope: required dictionary-argument rejection only; no password recovery or capture/attack exercised.' ;;
  computer-use-linux) echo 'Scope: unknown-command rejection only; no desktop, accessibility, screenshot, input or MCP backend exercised.' ;;
  captiveportalautologin-vith-git) echo 'Scope: invalid-option rejection only; no real captive portal or VPN/network-state exercise.' ;;
  fresh-editor) echo 'Scope: safe offline config load/show only; no interactive TUI editing exercised.' ;;
  looking-glass) echo 'Scope: pre-run config rejection, DKMS source and OBS exported symbols; no graphical rendering, VM transport, OBS launch or module build/load.' ;;
  i915ovmf) echo 'Scope: PCI/EFI option-ROM and AMD64 PE loader structure; no Intel GPU passthrough or firmware execution.' ;;
  microsandbox) echo 'Scope: genuine separately verified runtime doctor and fail-closed configuration; missing KVM is expected, no VM or image boot.' ;;
  ttf-ioskeley-mono|ttf-ioskeley-mono-unhinted) echo 'Scope: TrueType data, family and hint-table invariants; no GUI rendering.' ;;
  pi) echo 'Scope: installed offline CLI argument parser; no provider request or interactive session.' ;;
  spotify-adblock|spotify-remove-ad-banner) echo 'Scope: socket-filter behavior or private archive patch/restore fixture; signed Spotify dependency installed, no Spotify GUI or playback.' ;;
esac
printf '%s: offline consumer assertions passed\n' "$name"
