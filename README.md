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

`oh-my-pi-vith-git` follows the fork's `integration` branch. Updates resolve its
current commit and derive the package version from that checkout. Each signed
snapshot records the exact commit built; this is a per-build identity, not a
permanent pin preventing later Git updates.

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

New recipe roots can be registered as authenticated pending imports without
executing their recipes. Pending imports are not published or included in routine
source-update discovery. Their first conversion is reviewed in a package-root PR
and uses the same exact-input recipe approval as other new code. Only a successful
authorized build and accepted recipe merge activate the package, through automatic
bookkeeping that retains the original build rather than compiling it again.

Source updates are checked every six hours. Before any recipe execution,
trusted-main tools statically freeze the exact review head, recipe tree, source
inputs, metadata claims and build policy. Only non-trivial `PKGBUILD` changes,
including a newly introduced recipe, require your approval in the `recipe-review`
environment for that exact proposal. Unchanged recipe code and enrolled literal
version, `pkgrel` and checksum edits are authorized automatically after independent
source and metadata validation. Unsupported or inconsistent inputs fail validation;
they do not create another approval prompt. After validation succeeds, recipe PRs
merge automatically through the normal branch protections, with no separate merge
approval. Merge commits preserve the reviewed and upstream histories.

An unchanged source receipt with no open proposal PR is a no-op: automation leaves
any retained proposal branch untouched and continues with other source updates.
Changed proposals and refreshes of open PRs still require authenticated watcher
ownership before automation can update or dispatch them.
Post-build validation runs after either authorization route; a skipped inactive
review or automatic-authorization job does not skip validation.

Only after authorization does each affected package execute its recipe and build
in a disposable container without credentials or host mounts. Validation checks
the resulting metadata and runtime behavior against the frozen inputs.
Build dependencies resolve from the official Arch repositories first, then the
signed `vith-gh` repository and the signed `vith-arch` repository at n3t. Both
custom repositories require trusted database and package signatures. Workers
resolve GitHub's latest-release redirect to a fixed snapshot before installing
dependencies; consumer verification keeps its explicitly selected snapshot.
Neither step receives publication signing credentials.
Metadata-only events and explicit dispatches start the pipeline; scheduled
reconciliation recovers missed events. A changed review head needs new
authorization, but bookkeeping or controller movement must not cancel or repeat
an already authorized compilation.

After a recipe merge, automation verifies the exact correspondence between the
reviewed head and accepted merge, then records its gitlink, source lock and
original build provenance on protected main. This generated bookkeeping merges
automatically without a second human approval or another build. Automation,
workflow, policy and documentation changes also validate and merge automatically.
Source-only changes run isolated exact-revision tests and verify unchanged package
inputs, with zero package compilations and no approval prompt.
Automatic main-source merging is limited to branches in this repository; external
forks are rejected by that route rather than given another approval gate.

Validation can also be started manually:

```fish
set head (gh pr view NUMBER --repo vith/arch-packages --json headRefOid --jq .headRefOid)
gh workflow run candidate.yml --repo vith/arch-packages -f pr_number=NUMBER -f expected_head=$head
gh workflow run update.yml --repo vith/arch-packages
python -m unittest discover -s tests -p 'test_*.py'
```

Each authorized package input builds once in its own independent hosted x86_64
workflow run, using accepted-main worker tools without a per-worker environment
approval. The candidate coordinator dispatches those runs and collects verified
outputs; it does not compile packages. Its live log links each child run and
reports state transitions. Open a child run for live package build stdout; the
same output remains in its build receipt. All approved build workers restore and
save compatible trusted dependency/compiler caches, including progress from
failed compilation attempts.

A successful compilation is not repeated if later validation, collection,
storage or publication fails. Recovery uses the exact original bytes and
authenticated provenance, retained in durable draft build storage independently
of expiring workflow artifacts. If those bytes cannot be recovered, automation
refuses with a missing-byte error rather than recompiling. An actual failed
compilation may retry using its trusted cache; a cache is not a substitute for
original package output.

Retained builds authenticate the ordered build-harness manifest declared in their
own immutable controller source, including that declaring source's bytes and
mode. Recovery parses the literal declaration without executing historical
Python, so later harness additions do not invalidate the original build proof.

Workers provision `libarchive-tools` before approval or recovery preparation;
candidate authorization also provisions it before validating accepted original
archives. `bsdtar` is required for persistence, recovery and bookkeeping proof.
Downloads negotiate the GitHub Actions ZIP media type separately from release
asset bytes; storage redirects never receive the API authorization token.

Documentation, tooling, image and controller-only changes, and acceptance
bookkeeping, select no package compilations. Unchanged approved inputs reuse
their original output and provenance across compatible controller changes and
ordinary upstream-ref advancement. To request a rebuild, bump the package's
`pkgrel`.

Publication never dispatches workers or compiles packages. It verifies and reuses
existing signed packages or authenticated durable unsigned build output,
preserving the original producer's provenance, then signs the snapshot and
checks public downloads before updating `latest`. Older snapshots remain
available. The manual
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
