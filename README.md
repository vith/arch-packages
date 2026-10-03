# Arch packages

Native x86-64 Arch builds on standard GitHub-hosted runners and signed complete GitHub Release snapshots. GitHub hosts the recipes, CI and package downloads. Accepted recipe gitlinks on `main`—not moving recipe or AUR branch tips—are the build authority.

## Packages and sources

| Package | Recipe authority | Independent update discovery |
| --- | --- | --- |
| archive-mounter | Unmodified third-party AUR | AUR recipe commits |
| carapace | Maintained AUR-derived recipe | Software tags and deterministic three-way AUR integration; conflicts require a human |
| nasc-tui-bin | Local authoritative recipe | GitHub Release asset plus exact GPLv2 corresponding source |
| cloudflare-speed-cli | Local authoritative recipe | Authentic software tags, native Git checksum |
| oh-my-pi-vith-git | Maintained fork recipe | Exact canonical `vith/oh-my-pi-vith` integration commits |
| python-google-genai | AUR-derived recipe with a preserved local correction | Deterministic three-way AUR recipe updates; conflicts require a human |

The Google SDK recipe removes only the unused `python-sentencepiece` test dependency, which is absent from the official Arch repositories. Its tokenizer tests were already excluded upstream; enabled tests and the optional runtime tokenizer dependency remain unchanged. AUR updates preserve this correction.

The source fork is <https://github.com/vith/oh-my-pi-vith>. There are no source credentials or automatic AUR uploads.

`inputs/` binds exact software identities and native versions. Git inputs pin upstream commits and version tags. Builds fetch those exact objects directly from upstream; this repository does not publish Git source bundles. Builds use verified read-only mirrors instead of resolving moving branches. OMP derives its native package and runtime identities from that frozen ancestry; source-version manifests are not rewritten to create artificial releases.

## Independent recipe histories

This is one GitHub repository, not one repository per package. `main` contains shared policy, source locks, CI and publication tools. Each `recipes/<pkgbase>` is a submodule pointing back to this repository at an exact package-root commit:

The preserved Nasc history contains upstream v1.0.4 binaries and old package metadata that incorrectly claimed MIT. The maintained recipe corrects the license. [Complete corresponding source, upstream GPL license and verified historical binary provenance](https://github.com/vith/arch-packages/releases/tag/history-source-nasc-v1.0.4) accompany those original objects.

- `aur/<pkgbase>` retains authentic upstream AUR history where it exists.
- `pkg/<pkgbase>` retains our maintained recipe history and corrections.
- `recipe-updates/<pkgbase>/<watcher>` anchors proposed recipe commits.
- `updates/<pkgbase>/<watcher>` proposes their gitlinks and source-lock/provenance changes to `main`.

Recipe branches have `PKGBUILD`, `.SRCINFO` and payloads at their root. Existing AUR/maintained history is preserved without rewriting original commits or inventing ancestry. Locally authored recipes with no earlier commits start honestly with a new root. Branch heads do not override accepted gitlinks; CI never uses `submodule update --remote`. The trusted loader verifies same-repository module identity and exports bounded immutable recipe data without executing candidate Git configuration, hooks or scripts.

Candidate review includes the complete underlying recipe diff, modes and history, not just changed SHA values. Rewritten recipe/AUR history fails closed. Maintained branch advancement uses ancestry checks and exact leases and cannot overwrite divergent or pending work. A coordinated `main` commit can pin multiple recipes together, with human approval and every affected package built from that exact candidate.

## Client configuration

After verified publication, GitHub's latest-release download URL exposes the signed catalog, public key, databases and packages. Trust the dedicated key only after independently comparing this full fingerprint:

```text
9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617
```

Public key: <https://github.com/vith/arch-packages/releases/latest/download/arch-packages.asc>. Pacman configuration:

```ini
[arch-packages]
SigLevel = Required
Server = https://github.com/vith/arch-packages/releases/latest/download
```

Strict signatures are never disabled. Separate database/signature requests can straddle latest-release promotion; such a pair fails closed. Retry a fresh sync or use `https://github.com/vith/arch-packages/releases/download/SNAPSHOT_TAG` as the server for a fixed generation. Old snapshot, package, signature and corresponding-source assets are retained. Pacman database/file aliases are uploaded as real signed bytes under the exact requested filenames, not local symlinks.

## Approval and builds

The updater runs every six hours and is also manually dispatchable. Routine proposals are per package/watcher. Narrow, independently verified literal source/version/checksum transitions may merge automatically only after exact-head native validation and the deterministic gate. Changes to functions, dependencies, payloads, licenses, architecture, modes, policy, automation, unknown metadata, or multiple packages require human approval through the secret-free `recipe-review` environment and then human merge. A successful build is not approval. No LLM is involved.

`main` requires `recipe-policy` and `candidate-build`, a PR, and an up-to-date base. Changed head/base identities invalidate evidence and approval. Builds receive no write, signing or model credentials. All packages affected by one candidate use one exact head and its recipe pins; missing or failing outputs prevent acceptance of the entire candidate.

Each changed package builds in its own `build-package.yml` workflow run, with separate logs, status and retries. The publication workflow dispatches those runs and validates their artifacts; it never compiles packages itself. Unchanged packages are reused from the previous signed catalog after verification. A complete snapshot is signed, uploaded, anonymously read back and verified before GitHub latest-release promotion. Reusing a filename for different bytes is refused. A failed or stale run cannot replace the active repository.

Manual operations use GitHub CLI; these commands are fish-compatible:

```fish
gh workflow run update.yml --repo vith/arch-packages
gh workflow run candidate.yml --repo vith/arch-packages -f pr_number=PR_NUMBER
gh workflow run verification.yml --repo vith/arch-packages
```

Native builds and empty-cache installation/runtime proofs run on GitHub, never on the workstation. The official Arch image and third-party Actions are pinned. Native source probes first preserve recipe modes and establish root-owned, read-only frozen Git configuration and mirrors during trusted container setup; recipe execution then drops all capabilities and enables no-new-privileges in a credential-free environment. Initial enrollment preserves the accepted native pkgrel even when makepkg derives a new VCS version. Hardware-detected compilation concurrency is retained. The x86_64 package build verifies the compiled executable identity and native PipeWire linkage.

## Release activation and rollback

`publish.yml` creates a draft snapshot, uploads every expected asset, publishes it with `make_latest=false`, and independently verifies all public bytes/signatures and the complete database. Only then may it promote that exact snapshot with `make_latest=true`. Corresponding-source releases are not package snapshots.

Before promotion, the publisher rechecks current `main` and the previous latest ID/tag. It observes the promoted release and signed stable catalog afterward. An ambiguous API response is reconciled against actual public state; it is not permission to roll back an unrelated activation.

Rollback requires the expected current release ID/tag, independently verifies the retained complete signed snapshot, and promotes that old release without deleting assets or changing the key. From an exact accepted control checkout:

```fish
python3 tools/publish.py rollback --target-tag SNAPSHOT_TAG --expected-release-id CURRENT_ID --expected-tag CURRENT_TAG --work-dir ~/.local/state/omp/work/arch-rollback
```

The workflow's rollback operation provides the same guarded path. Fixed snapshot URLs remain usable without changing latest. No laptop package configuration is changed automatically.

## GitHub configuration

`opentofu/` owns the GitHub repository settings, default branch, Actions permissions, review/publish environments, publication branch restriction, and signing secrets/fingerprint. Workflow and recipe files remain version-controlled source code. Repository settings are changed through OpenTofu, not ad-hoc API writes.

Run from this checkout:

```sh
tools/tofu.sh init
tools/tofu.sh plan -out=github.tfplan
tools/tofu.sh apply github.tfplan
```

The wrapper uses the GitHub CLI login without printing its token. State and saved plans are encrypted and ignored by Git. The independent state passphrase is `~/.local/state/arch-packages/tofu-passphrase`; signing recovery files are under `~/.local/state/arch-packages/signing/`. Back up both privately before moving to another workstation. `ARCH_STATE_PASSPHRASE_FILE` overrides the passphrase path and `TF_VAR_signing_directory` overrides signing recovery location. Never commit these files or decrypted state. `imports.tf` records adoption of existing resources without recreating the repository.

## Signing and recovery

Only `publish` holds the dedicated private signing key and passphrase. The committed public key/fingerprint is checked before signing. Packages, both pacman databases, and the complete catalog have detached signatures. Corresponding-source/license assets and pinned source identities are bound by the signed catalog.

Private signing recovery copies are outside this checkout. Never commit tokens/passphrases, delete old snapshots, or alter client configuration automatically. Rollback selects a previously verified complete GitHub Release without deleting assets or changing the key.

## Verification

`verification.yml` runs the Python gitlink/policy/source/publication boundary suite with native `vercmp` from the pinned Arch image. Publication/candidate builds validate complete prepared `.SRCINFO`, lock identity, expected outputs, and native runtime evidence. `tools/smoke.sh` installs all six with empty databases/cache and required signatures in a disposable Arch environment and exercises their actual consumers. The Google SDK smoke sends a real SDK request to a local HTTP fixture and checks the decoded response, without credentials or a paid API. Verification is complete only when the corresponding Actions runs and live snapshot evidence exist; checked-in automation alone is not proof of a deployed service.
