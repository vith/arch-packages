# Arch packages

Personal x86_64 Arch Linux packages, built on GitHub Actions and distributed
as signed GitHub Releases.

## Packages

| Package | Source |
| --- | --- |
| `archive-mounter` | AUR recipe |
| `carapace` | AUR-derived recipe, updated from upstream tags |
| `nasc-tui-bin` | Upstream release binary and corresponding GPL source |
| `cloudflare-speed-cli` | Upstream source tags |
| `oh-my-pi-vith-git` | [vith/oh-my-pi](https://github.com/vith/oh-my-pi) |
| `python-google-genai` | AUR-derived recipe |
| `apexshot` | [Upstream source tags](https://github.com/apex-shot/apexshot), maintained source-build recipe |

The Google SDK recipe omits the unavailable `python-sentencepiece` build/test
dependency. Upstream already excludes tokenizer tests; the optional runtime
dependency is unchanged.

ApexShot includes its native capture overlay and optional GNOME Shell integration
for Shell 48–50. The extension is installed system-wide but is not enabled
automatically. Log out and back in after installation, then enable **ApexShot**
in GNOME Extensions if desired. The package does not enable desktop autostart.

## Install

Download [the public signing key](https://github.com/vith/arch-packages/releases/latest/download/arch-packages.asc).
Before trusting it, verify its full fingerprint:

```text
9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617
```

```sh
sudo pacman-key --add arch-packages.asc
sudo pacman-key --lsign-key 9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617
```

Add to `/etc/pacman.conf`, then run `sudo pacman -Syu`:

```ini
[arch-packages]
SigLevel = Required
Server = https://github.com/vith/arch-packages/releases/latest/download
```

Packages and repository databases are signed. If a database/signature download
straddles a release update, retry a fresh sync; do not disable signature checking.
For a fixed snapshot, replace `releases/latest/download` with
`releases/download/SNAPSHOT_TAG`.

## Development

- `recipes/`: exact recipe commits, stored as submodules of this repository.
- `pkg/*`: maintained recipe branches; `aur/*`: imported AUR history.
- `packages.json`, `inputs/`, `upstream/`: package policy, source pins and update tracking.
- `tools/`, `tests/`, `.github/workflows/`: builds, updates, publication and tests.
- `opentofu/`: settings for this GitHub repository only.

Clone with `git clone --recurse-submodules https://github.com/vith/arch-packages.git`.
Change recipe code on its `pkg/*` branch, then update its gitlink and source lock
on a main-branch PR. Changed package contents require a version or `pkgrel` bump.

Source updates are checked every six hours. Automatic merges are limited to
verified version/source/checksum changes. Other changes require approval in the
`recipe-review` environment and a human merge. Main requires passing tests,
candidate checks and an up-to-date PR. Candidate validation starts after PR tests;
it can also be started manually:

```sh
gh workflow run candidate.yml --repo vith/arch-packages -f pr_number=NUMBER
gh workflow run update.yml --repo vith/arch-packages
python -m unittest discover -s tests -p 'test_*.py'
```

Each affected package builds in a separate hosted x86_64 workflow run. Documentation
changes do not rebuild packages. Trusted release builds cache dependencies and
compiler output; candidate builds do not read or write those caches. Publication
reuses unchanged signed packages, including across CI-only changes. To rebuild a
package with new tooling, bump its `pkgrel`.

Publication verifies packages, signs the snapshot and checks public downloads
before updating `latest`. Older snapshots remain available. The manual
`consumer-proof.yml` workflow installs a snapshot in a clean Arch container and
exercises the packages; it is separate from publication and needs only the snapshot
tag, not expiring workflow artifacts.
ApexShot consumer checks cover CLI version/help, native messaging, shared-library
resolution, desktop assets and extension files. They do not verify interactive
capture or recording in a live GNOME/Wayland session.

## Administration

Repository settings and signing secrets are managed with OpenTofu:

```sh
tools/tofu.sh init
tools/tofu.sh plan -out=github.tfplan
tools/tofu.sh apply github.tfplan
```

The wrapper uses your GitHub CLI login. State and plans are encrypted and ignored
by Git. Privately back up `~/.local/state/arch-packages/tofu-passphrase` and
`~/.local/state/arch-packages/signing/`. The environment variables
`ARCH_STATE_PASSPHRASE_FILE` and `TF_VAR_signing_directory` override these paths.

To roll back, run `publish.yml` with `operation=rollback`, the retained
`target_tag`, and the current `expected_tag` and `expected_release_id`. It verifies
the retained snapshot before changing `latest`; it does not delete releases.

The preserved Nasc history includes old binaries whose metadata incorrectly said
MIT. The maintained recipe corrects this to GPLv2; [corresponding source and the
upstream license](https://github.com/vith/arch-packages/releases/tag/history-source-nasc-v1.0.4)
remain available with that history.
