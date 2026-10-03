#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
passphrase_file=${ARCH_STATE_PASSPHRASE_FILE:-$HOME/.local/state/arch-packages/tofu-passphrase}
[[ -s "$passphrase_file" ]] || { printf 'Missing state encryption passphrase: %s\n' "$passphrase_file" >&2; exit 1; }
unset GH_TOKEN GITHUB_TOKEN TF_LOG TF_LOG_PATH
export GITHUB_TOKEN
GITHUB_TOKEN=$(gh auth token --hostname github.com)
export TF_VAR_state_passphrase
TF_VAR_state_passphrase=$(<"$passphrase_file")
exec tofu -chdir="$root/opentofu" "$@"
