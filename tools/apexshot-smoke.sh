#!/usr/bin/env bash
set -euo pipefail
[[ $# == 3 ]] || { echo 'usage: apexshot-smoke.sh expected-version evidence-dir package-files' >&2; exit 2; }
version=$1
evidence=$2
package_files=$3
mkdir -p -- "$evidence"
# Headless commands only: no onboarding, daemon, capture helper launch or GUI.
timeout 30s apexshot --version > "$evidence/apexshot-version.txt" 2>&1
timeout 30s apexshot --help > "$evidence/apexshot-help.txt" 2>&1
for binary in apexshot apexshot-capture; do
  ldd "/usr/bin/$binary" > "$evidence/$binary-ldd.txt" 2>&1
done
desktop=/usr/share/applications/io.github.codegoddy.apexshot.desktop
desktop-file-validate "$desktop"
cp "$desktop" "$evidence/io.github.codegoddy.apexshot.desktop"
cp "$package_files" "$evidence/apexshot-files.txt"
gsettings list-schemas > "$evidence/apexshot-gsettings-schemas.txt"
if [[ $(<"$evidence/apexshot-gsettings-schemas.txt") == *org.gnome.shell* ]]; then
  gsettings get org.gnome.shell enabled-extensions > "$evidence/apexshot-enabled-extensions.txt"
else
  printf '%s\n' 'org.gnome.shell schema unavailable in the headless clean root' > "$evidence/apexshot-enabled-extensions.txt"
fi
python - "$version" "$evidence" <<'PY'
import configparser,json,os,pathlib,re,shlex,struct,subprocess,sys
version=sys.argv[1].split(':',1)[-1].rsplit('-',1)[0]
evidence=pathlib.Path(sys.argv[2])
if (evidence/'apexshot-version.txt').read_text().strip() != 'apexshot '+version:
    raise SystemExit('ApexShot exact runtime version mismatch')
help_text=(evidence/'apexshot-help.txt').read_text()
if 'ApexShot' not in help_text or 'capture' not in help_text or '--version' not in help_text:
    raise SystemExit('ApexShot informational CLI help incomplete')
for binary in ('apexshot','apexshot-capture'):
    path=pathlib.Path('/usr/bin')/binary
    if not path.is_file() or not os.access(path,os.X_OK):
        raise SystemExit('ApexShot executable missing: '+binary)
    linkage=(evidence/(binary+'-ldd.txt')).read_text()
    if 'not found' in linkage or 'not a dynamic executable' in linkage or 'statically linked' in linkage:
        raise SystemExit('ApexShot shared-library resolution failed: '+binary)
desktop=pathlib.Path('/usr/share/applications/io.github.codegoddy.apexshot.desktop')
parser=configparser.ConfigParser(interpolation=None); parser.optionxform=str
parser.read(desktop)
entry=parser['Desktop Entry']
if entry['Type'] != 'Application' or shlex.split(entry['Exec']) != ['/usr/bin/apexshot']:
    raise SystemExit('ApexShot desktop launch target mismatch')
if entry.get('Terminal','false').lower() != 'false':
    raise SystemExit('ApexShot desktop unexpectedly requires a terminal')
if entry['Icon'] != 'apexshot':
    raise SystemExit('ApexShot desktop icon identity mismatch')
icons=sorted(pathlib.Path('/usr/share/icons/hicolor').glob('*/apps/apexshot.*'))
icons += sorted(pathlib.Path('/usr/share/pixmaps').glob('apexshot.*'))
if not any(path.is_file() and path.stat().st_size for path in icons):
    raise SystemExit('ApexShot desktop icon asset missing')
uuid='apexshot-gnome-integration@apexshot.github.io'
extension=pathlib.Path('/usr/share/gnome-shell/extensions')/uuid
metadata=json.loads((extension/'metadata.json').read_text())
if metadata.get('uuid') != uuid or metadata.get('name') != 'ApexShot':
    raise SystemExit('ApexShot GNOME extension identity mismatch')
if not isinstance(metadata.get('version'),int) or metadata['version'] <= 0:
    raise SystemExit('ApexShot GNOME extension version missing')
if not metadata.get('shell-version') or not all(isinstance(v,str) and v.isdigit() for v in metadata['shell-version']):
    raise SystemExit('ApexShot GNOME shell compatibility metadata missing')
modules={'extension.js','preview-stacking.js','shell-overlay.js','window-list.js','cursor-classifier.js'}
for name in modules:
    module=extension/name
    if not module.is_file() or not module.stat().st_size:
        raise SystemExit('ApexShot GNOME module missing: '+name)
    # Check installed relative dependencies without evaluating resource:// and
    # gi:// imports, which need the GNOME shell runtime.
    for imported in re.findall(r"""(?:from\s*|import\s*)['"](\.[^'"]+)['"]""",module.read_text()):
        dependency=(module.parent/imported).resolve()
        if not dependency.is_relative_to(extension.resolve()) or not dependency.is_file() or not dependency.stat().st_size:
            raise SystemExit('ApexShot GNOME module dependency missing/unsafe: '+imported)
files=[line.split(' ',1)[1].strip() for line in (evidence/'apexshot-files.txt').read_text().splitlines()]
if '/usr/bin/apexshot' not in files or '/usr/bin/apexshot-capture' not in files:
    raise SystemExit('ApexShot package executable ownership missing')
# A system extension is opt-in: package-owned activation defaults are forbidden.
for path in files:
    if path.startswith(('/etc/xdg/autostart/','/usr/share/autostart/','/etc/dconf/','/home/','/root/')) or path.endswith('.gschema.override'):
        raise SystemExit('ApexShot unexpected automatic activation asset: '+path)
enabled=(evidence/'apexshot-enabled-extensions.txt').read_text()
if uuid in enabled:
    raise SystemExit('ApexShot GNOME extension was automatically enabled')
config=pathlib.Path(os.environ.get('XDG_CONFIG_HOME',str(pathlib.Path.home()/'.config')))
data=pathlib.Path(os.environ.get('XDG_DATA_HOME',str(pathlib.Path.home()/'.local/share')))
for path in (config/'autostart',data/'gnome-shell/extensions',config/'dconf/user'):
    if path.exists():
        raise SystemExit('ApexShot informational CLI created activation state: '+str(path))
request=json.dumps({'cmd':'ping'}).encode()
native=subprocess.run(['/usr/bin/apexshot-native-host'],input=struct.pack('<I',len(request))+request,capture_output=True,check=True,timeout=30)
(evidence/'apexshot-native-host-stderr.txt').write_bytes(native.stderr)
if len(native.stdout)<4 or struct.unpack('<I',native.stdout[:4])[0]!=len(native.stdout)-4:
    raise SystemExit('ApexShot native messaging response frame invalid')
response=json.loads(native.stdout[4:])
if response!={'ok':True,'message':'Pong'}:
    raise SystemExit('ApexShot native messaging ping failed')
proof={
    'runtime_version':version,
    'cli_flags':['--version','--help'],
    'native_messaging_ping':response,
    'shared_libraries_resolved':['/usr/bin/apexshot','/usr/bin/apexshot-capture'],
    'desktop_entry':str(desktop),
    'icons':[str(path) for path in icons if path.is_file()],
    'gnome_extension':metadata,
    'gnome_modules':sorted(modules),
    'enabled_extensions_observation':enabled.strip(),
    'headless_limits':'No live GNOME/compositor session: capture, recording, overlay interaction and GNOME extension activation are not tested.',
}
(evidence/'apexshot.json').write_text(json.dumps(proof,sort_keys=True)+'\n')
print(proof['headless_limits'])
PY
