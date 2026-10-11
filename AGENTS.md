# Arch packages: operational instructions

This is the standalone `vith/arch-packages` repository. Core PR #67 merged on protected main; signing and protected merges are unchanged. Live acceptance and publication remain incomplete.

## Ownership and work discipline

- Limit changes to this repository. The OMP fork, n3t infrastructure, other software repositories, and client machines have separate owners.
- Use the assigned worktree. Do not switch, reset, or edit the user-owned primary checkout. The implementation worktree is `/home/vith/.local/state/arch-packages/worktrees/pr-build-pipeline`, on `refactor/pr-build-pipeline`, originally based on main `f5b1f735737944150331d06e0b05506f5c7d6957`.
- Persistent task scratch belongs under `~/.local/state/omp/work/`, not `/tmp`. Keep administrative recovery files under their established `~/.local/state/arch-packages/` paths, outside Git. Workflow scratch follows the paths declared in YAML.
- Never build packages or run package consumers on the workstation. Local Python tests, shell syntax checks, diff checks, and applicable OpenTofu checks are allowed. Task agents skip builds, lint, tests, and formatters mid-flight; the parent integrates changes and runs checks afterward.
- Task agents do not commit, push, mutate remotes, dispatch workflows, approve environments, or apply infrastructure. The human owns protected non-trivial merges. Never approve or merge on the user's behalf, fabricate statuses, or impersonate the Actions bot with a human token.
- Author documentation slowly and check each statement against source and evidence. Update the existing README, guide, plan, and operational instructions; do not add a changelog or parallel live explanation.
- All Git fetches disable recursive submodules. Do not turn source dependencies into new submodules or rewrite source references merely for tracking.

## Core execution and merge contract

- Statically prepare an exact same-repository recipe PR before execution. Keep trusted controller identity, recipe predecessor, recipe head/tree, request, actual producer, and accepted merge distinct.
- Build the exact recipe PR before human approval. No new-build `recipe-review`, `code-review`, or `source-review` environment gate belongs in the core route.
- New recipes, first import conversions, non-trivial changes, and executable recipe changes require the human's ordinary protected **Merge** after a successful build. Trusted classification alone may select automatic merge for independently eligible literal changes. An arbitrary caller flag, stored boolean, or passing status cannot change merge policy.
- Automatic recipe merge needs the whole registered worker attempt to finish successfully and retain its durable original output. Recheck the current PR head before finalization or merge. Files, an attestation, or a successful internal build step do not establish whole-worker success.
- A worker failure anywhere is a failed attempt. Do not salvage its output, scan older runs, use compilation checkpoints, or dispatch transport-recovery workers. An explicit failed retry reserves a fresh request and may compile again. Cancellation, timeout, missing registration, and ambiguous state are not success.
- Successful exact output is reused for acceptance and inclusion. Coordinator, controller, or bookkeeping movement alone does not request another compilation. Missing successful output is an error; inclusion never silently substitutes a rebuild.
- Recipe `check()` remains. Shared infrastructure has no OMP CLI/help/addon/PipeWire probes, including shared signed-snapshot consumer probes.

## Source, metadata, and acceptance

- Use normal native `makepkg` Git checkout, branch, history, and tags. Planned locks detect/classify updates; they are not instructions to alter checkout semantics.
- Record full actual Git commits from the working copies used by the build, reachable tag/version context, and the actual resulting version in a sidecar. Git source object IDs may be 40 or 64 hexadecimal characters. GitHub recipe/controller/base/head identities remain full 40-character SHAs.
- Keep the original recipe commit, tree, and `.SRCINFO` declaration. A recipe with `pkgver()` can legitimately produce a newer version than its original declaration. Do not refresh `.SRCINFO` into post-build commits or create rebuild loops.
- Metadata equivalence may allow the dynamic `pkgver` difference only where the recipe actually provides `pkgver()`. Do not ignore `pkgrel`, epoch, package names, architectures, sources, or dependencies. The actual resulting version must be greater than the accepted predecessor.
- Manual and human-edited heads use `declared_metadata` mode. Automatic discovery and unedited automation proposals retain independent derivation checks. Preserve human edits; do not repair classification by overwriting them.
- Accepted `inputs/`, gitlinks, source locks, upstream records, and acceptance references advance only after accepted recipe merge. Discovery, pending builds, and failures do not move the accepted baseline.
- Verify the normal merge's exact reviewed-head/parents/tree relationship before automatic main bookkeeping. Bookkeeping can activate pending imports and selects zero package workers. Pending imports remain outside routine discovery and publication until accepted.

## Efficient state and dispatch

- Use one operation-level `StateSnapshot`: resolve the state tip once, batch direct-key reads, cache immutable values, and use paginated PR GraphQL metadata. Compare locally and write only actual changes.
- Reserve package requests with exact-lease/CAS state before dispatch. Worker concurrency is not queue deduplication. The worker registers its exact run and attempt; poll that producer, never workflow history or a latest-runs list.
- Unchanged queued, running, successful, and failed inputs cause zero redispatch. A known failure needs explicit retry, not a retry on every sweep. If upstream advances during a build, wait for its result before refreshing the same automation-owned package PR.
- Track directly known PRs after merge or closure. Preserve one update PR per package and authenticated watcher ownership. Do not overwrite human edits or mutate an unowned retained branch.
- Source verification markers bind base and PR; the status SHA supplies the head. A terminal failure is not an implicit retry. Source preparation failures must report failure only under a fresh-identity guard.
- A captured source base behind current main is skipped without blocking other work. Do not rebase human branches automatically. A changed head or base needs fresh exact evidence.
- A queued request may run under a later authenticated trusted-main harness. The original request remains immutable; record the actual producer/harness identity instead of rewriting it or using historical compatibility scans.

## Containers, credentials, caches, and output

- Build one package per independent hosted x86_64 `build-package.yml` run. The candidate coordinator dispatches and collects; it does not compile. Show child links and state transitions live, and preserve streamed build output in its receipt.
- Run recipes as an unprivileged user in a disposable pinned Arch container. Keep API credentials and OIDC in trusted host tools. Never mount host root, home, checkout, credentials, or the Docker socket. Only the checked job-owned cache may be bound.
- Dependencies use official Arch first, then signed `vith-gh`, then signed n3t `vith-arch`. Require package and database signatures. Freeze latest-release redirects to a fixed dependency snapshot before installation.
- Unaccepted cache writes stay isolated by package and recipe head. Failed compilation progress can be saved there; a cache is not durable package output or authority.
- Accepted-cache lookup returns the complete key from the actual receipt `input_digest` and exact original producer run/attempt, together with the original saved path. Admit it only on exact key match. Do not recompute it from new planned inputs, use a broad accepted restore prefix, or treat a miss as an error; a miss builds cold.
- Persist original unsigned output, candidate/request identity, context, asset descriptors, and bound worker OIDC attestation in durable draft build storage. Persistence occurs before run conclusion; consumers must later confirm the complete worker's success.
- Do not compute package-byte SHA fields in native build evidence or download/hash packages for premerge acceptance. Check native output names, output set, version, architecture, and source/dependency declarations. Archive and attestation digests authenticate transport, not an extra premerge package-hashing pass.
- At inclusion, authenticate the original successful producer and request, verify bound archive/bundle assets and attestation, extract safely, and verify package contents/bytes. Preserve the original producer in signed provenance.
- Downloads use the correct media type for Actions ZIP versus release bytes. Storage redirects never receive API tokens. Provision `libarchive-tools` before archive handling; never substitute unsafe extraction.

## Main tests, signing, and history

- Main documentation, source, controller, image, and bookkeeping PRs use the single candidate source-test route with zero package workers. Executable main code still needs human normal Merge. Generated exact bookkeeping can merge automatically after validation.
- Source tests receive the complete exact tracked snapshot, including workflow/control files, with no `.git`, ignored state, or credentials. Run in the pinned Arch sandbox. Main regression uses `git archive` for the exact SHA and `/verify/.work/tmp` for fixtures, with native Arch `vercmp`.
- Signing requires exact accepted-controller source evidence. Reuse tested PR evidence only after proving its protected merge and identical tested tree; otherwise test exact accepted main. Do not assume an Actions-token merge triggers push workflows.
- Publication selects no workers and compiles nothing. Collection is metadata-only. On the signing runner, directly authenticate and materialize original output once before private-key import/use. Then sign packages, build and sign databases, and sign the catalog.
- Retry failed inclusion with the same successful producer. Preserve unchanged signed bytes, signatures, provenance, historical enrollment, legal assets, and rollback.
- Verify all public snapshot assets before promotion. After promotion, verify the exact active release identity and signed catalog without repeating every asset download. Rollback verifies its target and expected current tag/release ID and neither rebuilds nor re-signs.
- Manual `consumer-proof.yml` installs the selected signed snapshot using enrollment at that snapshot's accepted commit. It is separate from publication and client installation. OMP installation does not imply shared runtime probes.

## Infrastructure and delivery checkpoint

Manage repository settings and signing secrets through `opentofu/`. Use the canonical encrypted state, never a copied state from another checkout. The wrapper uses the existing GitHub CLI login; never print credentials. State, saved plans, signing keys, and the passphrase remain encrypted/gitignored or outside Git as appropriate.

Delivery is the selected small prerequisite, then **one atomic core PR** containing reviewable component commits. Do not deploy the components as partially active PRs. Install coherent protected code before removing old environment gates. Settle old work and inventory directly known fully successful outputs; never migrate failed-worker output through an exception.

Observed evidence:

- Core: 305 tests passed in 24.630 seconds. Later targeted suites passed: update 40, package runs 14, recipe acceptance 21, after failure-lifecycle/behind-base fixes and deletion of the obsolete kind parameter.
- Prerequisite: 284 tests passed in 35.155 seconds with one skip. Shell syntax and diff checks passed.
- Canonical encrypted-state OpenTofu apply: 0 added, 0 changed, 3 destroyed, removing only the `code_review` and `recipe_review` environments and the `source_review` ruleset. The next plan reported **No changes**. Canceled 26 old waiting runs; recipe branches and PRs were preserved.
- A real read-only state check resolved the tip once, batch-read seven records, and made zero requests on cached reread. PR GraphQL observed 31 open PRs. This used user-token access, not proof that the App quota recovered.
- Hosted read-only smoke [38081926361](https://github.com/vith/arch-packages/actions/runs/38081926361) passed both jobs: native fixture 57 seconds, historical consumers 2 minutes 40 seconds. It did not deploy the controller or publish anything.

- [Prerequisite PR #66](https://github.com/vith/arch-packages/pull/66) and [core PR #67](https://github.com/vith/arch-packages/pull/67) were human-merged. README-only [PR #68](https://github.com/vith/arch-packages/pull/68) auto-merged: [candidate 38147850295](https://github.com/vith/arch-packages/actions/runs/38147850295) succeeded; the package-worker step was skipped.
- Unmerged descriptor-verifier fix: 3 meaningful RED/GREEN boundary regressions; 308 tests passed in 21.713 seconds. The actual CLI with the fix rejects the old schema-2 descriptor with `ValueError: invalid direct build descriptor`.
- Old OMP [worker 37979975267](https://github.com/vith/arch-packages/actions/runs/37979975267) failed; no salvage.
- Fresh OMP pkgrel 2 [PR #69](https://github.com/vith/arch-packages/pull/69), head `a8be88808739a0a4bbc024e113b88cb075805f80`: [worker 38169157502](https://github.com/vith/arch-packages/actions/runs/38169157502) succeeded through all steps in 15 minutes 29 seconds; [candidate 38168988462](https://github.com/vith/arch-packages/actions/runs/38168988462) succeeded. All three exact-head checks succeeded; the PR remains open.

PR #69 is pkgrel-only but human-classified. Its successful checks do not establish testing of a non-trivial executable recipe change. Source-verifier fix [PR #70](https://github.com/vith/arch-packages/pull/70) requires exact hosted validation followed by a human protected Merge.

No new publication, signed installation, promotion, rollback, or warm-cache proof. Keep all unproved acceptance boundaries open.


## Repository setup safety

Write documentation slowly and carefully. Describe the recorded client setup, not a freshly verified current laptop state. The setup record used `/etc/pacman.conf`, the `[arch-packages]` database name, and the latest-release URL, with `Required` trusted signatures for both packages and databases. The recorded fingerprint is `9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617`. Newer snapshots may use `vith-gh` per the README; preserve the historical database name when documenting the older setup.

Other existing repositories supply more packages. Never remove them or assume this repository replaces them. Never downgrade installed packages to match this repository. Adding it does not authorize upgrading, downgrading, or removing laptop packages. The recorded setup synced only this repository and changed no installed packages. The original configuration and inventory backup is recorded at `~/.local/state/arch-packages/laptop-setup/`. Do not present this older record as a fresh workstation check.
