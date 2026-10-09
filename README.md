# vith-gh

Personal x86_64 Arch Linux packages, built on GitHub Actions and distributed
as signed GitHub Releases. All executable packages are built from source, not
repackaged upstream binaries. Packages appear as `vith-gh/<package>` in pacman and
paru. The source project remains at `vith/arch-packages` on GitHub.

## Packages

| Package | Source |
| --- | --- |
| `archive-mounter` | AUR recipe |
| `carapace` | AUR-derived recipe, updated from upstream tags |
| `nasctui` | Upstream Go/C++ source tags, linked against system libqalculate |
| `cloudflare-speed-cli` | Upstream source tags |
| `oh-my-pi-vith-git` | [vith/oh-my-pi](https://github.com/vith/oh-my-pi) |
| `python-google-genai` | AUR-derived recipe |
| `apexshot` | [Upstream source tags](https://github.com/apex-shot/apexshot), maintained source-build recipe |

`nasctui` replaces `nasc-tui-bin` and keeps the `nasc` command. The archive-mounter
package contains a desktop entry rather than a compiled program. Carapace is
compiled from Go source; `carapace-bin` is the upstream source repository's name,
not a prebuilt package input.

`oh-my-pi-vith-git` follows the fork's `integration` branch. Each validated build
and signed snapshot records an exact source commit for reproducibility; that
per-build pin does not permanently freeze future Git updates.

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
[vith-gh]
SigLevel = Required
Server = https://github.com/vith/arch-packages/releases/latest/download
```

Packages and repository databases are signed. If a database/signature download
straddles a release update, retry a fresh sync; do not disable signature checking.
For a fixed snapshot, replace `releases/latest/download` with
`releases/download/SNAPSHOT_TAG`.

If you previously configured `[arch-packages]`, rename that section to
`[vith-gh]` after the new database is published. Keep its server URL and
`SigLevel = Required`; the signing key is unchanged. Do not rename a separate
`[vith-arch]` repository.

Historical snapshots retain their original database filenames. When selecting
one explicitly, use `[arch-packages]` if it contains `arch-packages.db`, or
`[vith-gh]` if it contains `vith-gh.db`.

## Development

- `recipes/`: exact recipe commits, stored as submodules of this repository.
- `pkg/*`: maintained recipe branches; `aur/*`: imported AUR history.
- `packages.json`, `inputs/`, `upstream/`: package policy, source pins and update tracking.
- `tools/`, `tests/`, `.github/workflows/`: builds, updates, publication and tests.
- `opentofu/`: settings for this GitHub repository only.

Clone with `git clone --recurse-submodules https://github.com/vith/arch-packages.git`.
Propose recipe changes in a PR targeting `pkg/<package>`. GitHub's **Files changed**
tab contains the actual `PKGBUILD`, patches, install scripts and other recipe
files, not generated main-branch records or copied description diffs. Changed
package contents require a version or `pkgrel` bump.

Source updates are checked every six hours. Verified mechanical
version/source/checksum changes merge automatically on the recipe branch.
Non-mechanical recipe changes require approval in the `recipe-review` environment
and a human recipe-branch merge. Recipe branches require passing validation and
an up-to-date PR; merge commits preserve the reviewed and upstream histories.

After a recipe merge, automation validates the accepted recipe against its exact
source and build evidence, then records its gitlink, source lock and provenance
on protected main. This generated bookkeeping merges automatically without a
second human approval. Changes to main's automation or policy instead require
the separate `code-review` approval and a human merge.

Validation uses trusted-main tools and frozen recipe inputs in disposable
containers without credentials or host mounts. Metadata-only events and explicit
dispatches start the pipeline; scheduled reconciliation recovers missed events.
A newer candidate run supersedes an older run, including an obsolete review wait.
Retiring a package requires human review. Candidate validation excludes retired
enrollments from builds and requires the proposed package list to match its recipe
pins. New enrollments need a separate hosted build and consumer proof before merge,
because candidate build policies come from trusted main.

Validation can also be started manually:

```fish
set head (gh pr view NUMBER --repo vith/arch-packages --json headRefOid --jq .headRefOid)
gh workflow run candidate.yml --repo vith/arch-packages -f pr_number=NUMBER -f expected_head=$head
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
tag, not expiring workflow artifacts. Historical consumer checks use the package
enrollment at the signed snapshot's accepted commit, not today's package list.
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
Rolling back across the repository rename also requires clients to use the
repository section name matching that snapshot's database.

The preserved Nasc history includes old binaries whose metadata incorrectly said
MIT. The maintained recipe corrects this to GPLv2; [corresponding source and the
upstream license](https://github.com/vith/arch-packages/releases/tag/history-source-nasc-v1.0.4)
remain available with that history.
