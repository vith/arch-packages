# n3t Arch packages

Native x86-64 Arch builds on standard GitHub-hosted runners, signed immutable GitHub Release snapshots, and originless Cloudflare redirects. The Oracle VM is not a runtime dependency. Accepted recipes in this repository—not a moving upstream recipe branch—are the build authority.

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

The source fork is <https://github.com/vith/oh-my-pi-vith>. Old Forgejo URLs in `upstream/` describe one-time migration provenance only. There are no source credentials or automatic AUR uploads.

`inputs/` binds exact software identities and native versions. Git inputs include complete reachable history and an explicit authentic tag/ref manifest in immutable, hash-addressed GitHub Release bundles. Builds use verified read-only mirrors instead of resolving moving branches. OMP derives its native package and runtime identities from that frozen ancestry; source-version manifests are not rewritten to create artificial releases.

## Client configuration

After publication and domain activation, the setup page at <https://arch.packages.n3t.work/> exposes the signed catalog, key, fingerprint, and immutable snapshot routes. Trust the dedicated key only after independently comparing this full fingerprint:

```text
C557EA3489AC5820B7C019A9D9CFD8271E0C3DD3
```

Public key: <https://arch.packages.n3t.work/repo/n3t-arch.asc>. Pacman configuration:

```ini
[n3t-arch]
SigLevel = Required
Server = https://arch.packages.n3t.work/repo
```

Strict signatures are never disabled. Separate database/signature requests can straddle an activation; such a pair fails closed. Retry a fresh sync or use `https://arch.packages.n3t.work/snapshots/SNAPSHOT_ID` as the server for a fixed generation. Old snapshot, package, signature, and corresponding-source routes are retained. Binary downloads redirect directly to immutable public GitHub assets; the Worker never fetches or buffers them.

## Approval and builds

The updater runs every six hours and is also manually dispatchable. Routine proposals are per package/watcher. Narrow, independently verified literal source/version/checksum transitions may merge automatically only after exact-head native validation and the deterministic gate. Changes to functions, dependencies, payloads, licenses, architecture, modes, policy, automation, unknown metadata, or multiple packages require human approval through the secret-free `recipe-review` environment and then human merge. A successful build is not approval. No LLM is involved.

`main` requires `recipe-policy` and `candidate-build`, a PR, and an up-to-date base. Changed head/base identities invalidate evidence and approval. Builds receive no write, signing, model, or Cloudflare credentials. All packages affected by one candidate use one exact head; missing or failing outputs prevent acceptance of the entire candidate.

Publication builds changed input digests only and reuses unchanged packages from the previous signed catalog after verification. A complete snapshot is signed, uploaded, anonymously read back, and verified before the Worker pointer changes. Reusing a filename for different bytes is refused. A failed or stale run cannot replace the active repository.

Manual operations use GitHub CLI; these commands are fish-compatible:

```fish
gh workflow run update.yml --repo vith/arch-packages
gh workflow run candidate.yml --repo vith/arch-packages -f pr_number=PR_NUMBER
gh workflow run verification.yml --repo vith/arch-packages
```

Native builds and empty-cache installation/runtime proofs run on GitHub, never on the workstation. The official Arch image and third-party Actions are pinned. Hardware-detected compilation concurrency is retained. OMP's temporarily disabled source test suites remain disabled; the retained source gate checks and compiles, then asserts the exact executable identity.

## Cloudflare ownership

Standalone `opentofu/` owns only Worker identity/settings and the `arch.packages.n3t.work` Custom Domain. It uses separate encrypted state and a pinned provider; it does not own uploaded code or the active deployment, so infrastructure applies cannot reset the published catalog. The existing OCI OpenTofu project and production DNS are separate.

The publisher owns verified Worker version uploads and 100% activation. It records the previous version, reconciles ambiguous responses by reading current state, and retains rollback evidence. CI needs only the account-scoped Worker publishing token, not OpenTofu state, domain-bootstrap credentials, or the workstation.

First publication uses `publish.yml` with `bootstrap=true`, which signs/uploads/verifies without activating. `python3 -m tools.cloudflare bootstrap` then verifies that signed catalog, applies the Worker identity with native OpenTofu, deploys the complete module, and binds the domain only after verification. Preserve encrypted infrastructure state, its private recovery passphrase, and the final domain-binding variables outside Git. Never replace an unrelated existing hostname/Worker.

The Worker stays on Free: 100,000 dynamic requests/day and 10 ms CPU/request. There is no KV, R2, paid upgrade, origin service, or Oracle fallback. Quota exhaustion fails rather than weakening signatures or switching origin.

## Signing and recovery

Only `publish` holds the dedicated private signing key, passphrase, and scoped Cloudflare token. The committed public key/fingerprint is checked before signing. Packages, both pacman databases, and the complete catalog have detached signatures. Corresponding-source/license assets and immutable Git bundle identities are bound by the signed catalog.

Private recovery copies are outside this checkout. Never export the production repository key, commit tokens/state/passphrases, blanket-prune volumes, delete old snapshots, or alter client configuration automatically. Rollback selects a previously verified Worker version at 100% without deleting assets or changing the key.

## Verification

`verification.yml` runs the Python policy/source/publication boundary suite and Node Worker tests with native `vercmp` from the pinned Arch image. Publication/candidate builds validate complete prepared `.SRCINFO`, lock identity, expected outputs, and native runtime evidence. `tools/smoke.sh` installs all six with empty databases/cache and required signatures in a disposable Arch environment and exercises their actual consumers. The Google SDK smoke sends a real SDK request to a local HTTP fixture and checks the decoded response, without credentials or a paid API. Verification is complete only when the corresponding Actions runs and live snapshot evidence exist; checked-in automation alone is not proof of a deployed service.
