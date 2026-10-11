# Arch packages: prerequisite operational instructions

This is the standalone `vith/arch-packages` repository. This prerequisite changes only main-PR source routing and the end of source-only verification. It does not install the core build-before-merge worker model. The old recipe approval, worker, cache, checkpoint, and output-recovery contracts remain in force until the later coherent cutover.

## Ownership and administration

- Limit changes to this project. The OMP fork, n3t infrastructure, other repositories, and client machines have independent ownership.
- Use the assigned worktree; do not switch/reset/edit the user-owned primary checkout. Persistent task scratch belongs under `~/.local/state/omp/work/`, not `/tmp`. Administrative recovery files remain under their established `~/.local/state/arch-packages/` paths outside Git.
- Never compile packages on the workstation. Local Python tests, shell syntax/diff checks, and applicable OpenTofu checks are appropriate. Task agents skip checks mid-flight; the parent runs checks after integration.
- Task agents do not commit, push, mutate remotes, dispatch workflows, approve environments, or apply infrastructure. Never fabricate statuses or impersonate automation with a human token.
- Manage repository settings, environments, permissions, and signing secrets through `opentofu/`. Use the canonical encrypted state and inspected saved plans. Never apply copied state from another checkout or print credentials.
- The wrapper uses the existing GitHub CLI login. State and plans stay encrypted/gitignored; private signing and passphrase files stay outside Git. Workflow and recipe changes are ordinary version-controlled files, not repository-file resources.
- All Git fetches disable recursive submodules. No new source-submodule scheme, dependency scheduler, source-reference rewriting just for tracking, or unrelated repository changes.
- Write documentation slowly against the interim source. Update existing files; do not add a changelog or describe the later core contract as already active.

## Narrow source-routing prerequisite

- Main-target `pull_request_target` events are metadata only. `candidate-dispatch.yml` checks out the protected event base `${{ github.sha }}` with `persist-credentials: false`, never the proposed PR checkout.
- The `dispatch` job skips event drafts. Its fresh PR read also requires open, non-draft, same-repository, main-target metadata. If the captured PR base is not current main, log the skip and dispatch nothing. Do not rebase the human branch automatically.
- `unchanged_git_scope(base, head)` compares exact Git tree scope before candidate code executes. Truncated/incomplete trees, missing enrollment/workflow data, and altered package scope cannot use source-only verification.
- Unchanged package scope dispatches the existing `verification.yml` flow on main with exact PR, head, and base. Package-changing scope retains the existing `candidate.yml` route. Recipe-root branches still require writer/reconciliation/manual handoffs because they contain no workflows.
- Source-only verification binds the actual workflow run/attempt/ref/path/title, exact same-repository PR, current main base, head, and unchanged package scope before checkout. Normal source control comes from the exact protected base; only the narrowly defined bootstrap uses the immutable head tag.
- Source tests receive the complete exact tracked snapshot, including workflows/control files, but no `.git`, ignored state, or credentials. Test in the pinned baseline Arch sandbox. Recheck authority before and after execution.
- Successful source validation writes evidence and the existing `verify`, `candidate-build`, and `recipe-policy` contexts. It ends there and waits for the human's ordinary protected **Merge**. It does not merge, dispatch publication, or run a source finalizer. The source CLI only validates, with `--pr`, `--base`, `--head`, `--directory`, and optional `--bootstrap`.
- Do not add a source-merge job or helper. Do not fabricate passing checks to install the prerequisite. Local tests are not hosted acceptance.

## Old recipe and worker contracts retained

- Statically freeze exact control/base/head/tree, source lock, policy, and metadata without executing recipes. Retain original candidate JSON and accepted-main OIDC prebuild provenance independently of compilation/artifact expiry.
- Before recipe invocation, independently verify genuine exact candidate/run/attempt protected `recipe-review` approval for new/non-trivial PKGBUILD changes, or reconstruct eligible automatic authorization. Stored booleans and passing statuses are not authority. Never approve recipe-review on the user's behalf.
- Recipe PRs target protected `pkg/*` and contain package-root payload. Unchanged executable code and enrolled literal version/`pkgrel`/checksum transitions can qualify automatically after independent source/metadata checks. Invalid inputs fail, not prompt for irrelevant approval. Existing authorized recipe merges remain automatic through normal protection; this prerequisite does not change that policy.
- Build each authorized input once in an independent hosted x86_64 worker with accepted-main tools and no per-worker environment approval. The coordinator does not compile; it prints child links/state transitions, and worker stdout streams while remaining in the receipt.
- Retain the old root-owned compilation checkpoint before first recipe execution. A successful compilation followed by metadata/runtime/copy/upload/attestation/storage failure still needs original-byte recovery, not another compilation. Only authenticated actual failed compilation permits retry. Started/canceled/unknown outcomes require recovery or refusal.
- Keep old durable draft `build-<inputdigest>` output, context, and bound OIDC bundle. Verify asset descriptors, size/hash, certificate/workflow/run/attempt, original source/tree/metadata/runtime evidence before reuse. GitHub assets are not platform-immutable and attestations are not pacman signatures.
- Old explicit transport recovery executes no recipe, uses no compiler cache, and preserves the original producer with its distinct signer. Do not replace old recovery behavior with the later whole-worker model in this prerequisite.
- Keep compatible trusted dependency/compiler cache restore/save, including failed compilation progress, for approved workers. Unapproved code must not populate trusted caches. Cache is not durable package output.
- Keep existing generic and OMP runtime checks in this interim worker/consumer system. Their removal belongs to the later atomic core cutover, not this prerequisite.

## Isolation, acceptance, and publication retained

- API credentials/OIDC stay in trusted host tools outside recipe containers. Run recipes unprivileged in disposable pinned Arch containers. No host root/home/checkout/credentials or Docker socket mounts; only an authenticated job-owned package cache may bind.
- Dependencies resolve official Arch first, then signed `vith-gh`, then signed n3t `vith-arch`. Both custom databases and packages require signatures. Freeze latest-release redirects to fixed dependency snapshots; consumer proof uses its selected snapshot. No publication signing credentials enter either.
- Git updates follow configured branches/tags. Per-build frozen identity is not a permanent update freeze. Retain the interim frozen Git configuration: `MAKEPKG_GIT_CONFIG` and matching `GIT_CONFIG_SYSTEM`, with `GIT_CONFIG_GLOBAL=/dev/null`; keep config and mirrors root-owned/read-only. Do not disable the system channel that makepkg needs.
- Accepted recipe bookkeeping verifies reviewed head against accepted merge parents/tree, without another recipe execution or worker. Documentation, tooling, image, controller, and bookkeeping select zero package compilations. Preserve exact successful output/provenance across compatible controller movement; bump `pkgrel` to deliberately request new package contents.
- Legacy reconciliation uses trusted `update.yml` with `migrate_pr`. Do not transfer old approvals or directly synchronize protected recipe tips. Targeted `reconcile_pr` skips unrelated discovery/proposal work.
- Publication retains its existing byte collection/recovery checks, relay, signatures, public readback, and rollback. It never dispatches workers or compiles, including transport recovery. Preserve original provenance, signed snapshots, filenames, source/license assets, and historical enrollment.
- Manual signed consumer proof remains separate from publication/client installation. Its success does not prove interactive live GNOME/Wayland capture or recording.
- Provision `libarchive-tools` before archive approval/recovery handling. Negotiate Actions ZIP and release bytes separately. Storage redirects never receive API authorization tokens.
- The existing historical lookup/scan behavior is still an interim caveat. Do not claim the prerequisite fixes state batching, duplicate dispatch, failed-worker lifecycle, or scan removal.

## Immutable initial installation

Freeze accepted main as `C` and the final source-only PR head as `H`. Keep unchanged package scope. If staging is needed, create/update against an owned staging base pinned to `C`, mark ready there, then retarget main without changing `H`. The dispatcher subscribes to opened/synchronize/reopened/ready events, not edited; do not rely on retargeting to dispatch. Do not bypass protected history or the exact-PR guard.

For initial installation only, dispatch `verification.yml` with exact PR/base/head and `bootstrap=true` from the direct-commit tag `source-review-<H>`. The managed `source-review-immutable` ruleset permits creation but forbids update/deletion with no bypass actors. The actual tag must point directly to `H`; a moving branch or annotated tag is not a substitute.

The tag selects the initial controller, not a human environment approval. Read-only object/run identity checks occur before checkout; package scope is checked before and after isolated tests. Source validation then waits for human normal Merge. Keep this immutable bootstrap path intact until the later coherent core cutover.

## Evidence checkpoint and delivery boundary

Prerequisite checks observed: 284 tests passed in 35.155 seconds with one skip; shell syntax and diff checks passed. These are local checks, not a protected hosted merge or rollout claim.

The selected sequence is this small prerequisite, then one atomic core PR with reviewable component commits. Do not apply the old-gate removal plan during this prerequisite. OpenTofu fmt/validate passed and an encrypted saved plan was inspected with 0 additions, 0 changes, and 3 destructions for the `recipe_review` and `code_review` environments and the `source_review` ruleset. It is **not applied**. Removal waits until coherent protected core code is installed, followed by a no-change plan.

User-token API access is not App quota recovery. Read-only smoke is not state migration, signing, promotion, or live-controller acceptance. Keep deployment and acceptance claims separate and remove stale dated observations rather than presenting them as current state.


## Repository prerequisites and safety

Write documentation slowly and carefully. Use the historical client setup record without claiming a fresh check of the current laptop. That record used `/etc/pacman.conf`, the `[arch-packages]` database name, and the latest-release URL, with `Required` trusted signatures for both packages and databases. The recorded fingerprint is `9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617`. The README may name `vith-gh` for newer snapshots; keep the historical database name when describing the recorded setup.

Other existing repositories supply more packages. Never remove them or infer that this repository replaces them. Installed packages must not be downgraded to match it. Adding this repository grants no permission to upgrade, downgrade, or remove laptop packages. The recorded setup synced only this repository and changed no installed packages. The original configuration and inventory backup is recorded at `~/.local/state/arch-packages/laptop-setup/`. These are historical facts, not newly verified workstation state.
