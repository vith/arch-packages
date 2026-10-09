#!/usr/bin/env bash
set -euo pipefail
# This launcher is intentionally supported only on disposable GitHub Ubuntu hosts.
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux && ${RUNNER_ENVIRONMENT:-} == github-hosted && $(uname -m) == x86_64 ]] || { echo 'build requires a disposable GitHub-hosted Linux x86_64 runner' >&2; exit 1; }
[[ $# == 2 ]] || { echo 'usage: tools/build.sh bundle.json output-dir' >&2; exit 2; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bundle=$(realpath -- "$1")
mkdir -p -- "$2"
output=$(realpath -- "$2")
shopt -s nullglob dotglob
existing=("$output"/*)
((${#existing[@]} == 0)) || { echo 'build output directory must be empty' >&2; exit 1; }
shopt -u nullglob dotglob
image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "$bundle")
[[ $image =~ ^ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}$ ]] || { echo 'bundle image must pin official Arch GHCR digest' >&2; exit 1; }
[[ $image == "$(<"$root/build-image.txt")" ]] || { echo 'bundle image differs from trusted image pin' >&2; exit 1; }
cache_args=()
if [[ -n ${ARCH_BUILD_CACHE:-} ]]; then
  [[ ${GITHUB_REF:-} == refs/heads/main && ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]] || { echo 'persistent caches are restricted to trusted main package runs' >&2; exit 1; }
  package=$(python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); assert len(value["packages"]) == 1; print(value["packages"][0]["pkgbase"])' "$bundle")
  [[ $package =~ ^[a-z0-9][a-z0-9+_.-]*$ && $ARCH_BUILD_CACHE == "$HOME/.local/state/arch-packages/cache/$package" ]] || { echo 'invalid package cache path' >&2; exit 1; }
  mkdir -p -- "$ARCH_BUILD_CACHE"
  [[ $(realpath -- "$ARCH_BUILD_CACHE") == "$ARCH_BUILD_CACHE" ]] || { echo 'symlinked cache path refused' >&2; exit 1; }
  cache_args=(--mount "type=bind,src=$ARCH_BUILD_CACHE,dst=/ci-cache" --env ARCH_PACKAGE_CACHE=1)
fi
container=$(docker create --platform linux/amd64 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add SYS_CHROOT --security-opt no-new-privileges --env PATH=/usr/local/sbin:/usr/local/bin:/usr/bin "${cache_args[@]}" "$image" /bin/bash -c 'pacman -Syu --noconfirm --needed -- python util-linux && exec /usr/bin/python /harness/tools/native.py build /input/bundle.json /output')
collect_checkpoint() {
  status=$?
  trap - EXIT
  checkpoint="$(dirname -- "$output")/compilation.json"
  # This root-owned container file is outside all recipe-writable paths.
  # A missing checkpoint is unknown, never evidence that compilation failed.
  docker cp "$container:/compilation.json" "$checkpoint" || true
  docker rm -f "$container" >/dev/null || true
  exit "$status"
}
trap collect_checkpoint EXIT
# Only bounded input data and explicitly trusted harness files enter the container.
python3 "$root/tools/native.py" validate "$bundle"
docker cp "$(dirname -- "$bundle")/." "$container:/input"
work="$output/.harness"
mkdir -p "$work/tools" "$work/keys"
for module in native sources recipe_gate github_api dependency_repo; do cp -- "$root/tools/$module.py" "$work/tools/$module.py"; done
cp -- "$root/keys/arch-packages.asc" "$work/keys/arch-packages.asc"
cp -- "$root/keys/n3t.asc" "$work/keys/n3t.asc"
cp -- "$root/tools/build.sh" "$work/tools/build.sh"
docker cp "$work/." "$container:/harness"
rm -r -- "$work"
docker start -a "$container"
docker cp "$container:/output/." "$output"
