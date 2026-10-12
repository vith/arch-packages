# Arch packages: operational instructions

This is the standalone `vith/arch-packages` repository. Signed snapshot promotion and hosted installation verification have passed; live rollback and the remaining operational scenarios are still open.

## Ownership and work discipline

- Limit changes to this repository. Other repositories, external infrastructure, and client machines have separate owners.
- Use the assigned worktree. Do not switch, reset, or edit the user-owned primary checkout.
- Persistent task scratch belongs under `~/.local/state/omp/work/`, not `/tmp`. Keep administrative recovery files under their established `~/.local/state/arch-packages/` paths, outside Git. Workflow scratch follows the paths declared in YAML.
- Never build packages or run package consumers on the workstation. Local Python tests, shell syntax checks, diff checks, and applicable OpenTofu checks are allowed. Task agents skip builds, lint, tests, and formatters mid-flight; the parent integrates changes and runs checks afterward.
- Task agents do not commit, push, mutate remotes, dispatch workflows, approve environments, or apply infrastructure. The assistant may make and merge authorized internal repository changes through ordinary protected Merge. Only external PKGBUILD changes require user approval. Never fabricate statuses, impersonate the Actions bot with a human token, or approve external reviews on the user's behalf.
- Check documentation against source and evidence. Update existing documentation rather than adding a changelog or parallel explanation.
- All Git fetches disable recursive submodules. Do not turn source dependencies into new submodules or rewrite source references merely for tracking.
- Keep PR descriptions specific to their changes. Omit personal setup details, unrelated migration plans, and agent/model narration.
- Use and track AUR upstream recipes. Carry only demonstrably necessary patches; exceptions should be rare. Use official Arch dependency packages rather than rebuilding them without a demonstrated need.

## Core execution and merge contract

- Statically prepare an exact same-repository recipe PR before execution. Keep trusted controller identity, recipe predecessor, recipe head/tree, request, actual producer, and accepted merge distinct.
- Build the exact recipe PR before human approval. No new-build `recipe-review`, `code-review`, or `source-review` environment gate belongs in the core route.
- A human-classified PR stays open until ordinary protected Merge, which the assistant may perform for authorized internal changes. External PKGBUILD changes require user approval. Trusted classification alone may select automatic merge for independently eligible literal changes. An arbitrary caller flag, stored boolean, or passing status cannot change merge policy.
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
- Pending intended policy, locks, and provenance may be revised against an authenticated pinned AUR baseline, without changing imported origins, recipe gitlinks, or accepted packages. Metadata admission does not approve the external recipe.
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

- Main documentation, source, controller, image, and bookkeeping PRs use the single candidate source-test route with zero package workers. Generated exact bookkeeping can merge automatically after validation.
- Source tests receive the complete exact tracked snapshot, including workflow/control files, with no `.git`, ignored state, or credentials. Run in the pinned Arch sandbox. Main regression uses `git archive` for the exact SHA and `/verify/.work/tmp` for fixtures, with native Arch `vercmp`.
- Signing requires exact accepted-controller source evidence. Reuse tested PR evidence only after proving its protected merge and identical tested tree; otherwise test exact accepted main. Do not assume an Actions-token merge triggers push workflows.
- Publication selects no workers and compiles nothing. Collection is metadata-only. On the signing runner, directly authenticate and materialize original output once before private-key import/use. Then sign packages, build and sign databases, and sign the catalog.
- Retry failed inclusion with the same successful producer. Preserve unchanged signed bytes, signatures, provenance, historical enrollment, legal assets, and rollback.
- Verify all public snapshot assets before promotion. After promotion, verify the exact active release identity and signed catalog without repeating every asset download. Rollback verifies its target and expected current tag/release ID and neither rebuilds nor re-signs.
- Manual `consumer-proof.yml` installs the selected signed snapshot using enrollment at that snapshot's accepted commit. It is separate from publication and client installation. OMP installation does not imply shared runtime probes.

## Infrastructure

Manage repository settings and signing secrets through `opentofu/`. Use the canonical encrypted state, never a copied state from another checkout. The wrapper uses the existing GitHub CLI login; never print credentials. State, saved plans, signing keys, and the passphrase remain encrypted/gitignored or outside Git as appropriate.

Record verification and remaining acceptance work in [the implementation plan](docs/implementation-plan.md).


## Client safety

Adding this repository does not authorize changing installed packages or removing other repositories. Never downgrade installed packages to match a snapshot. Use the database name and signing requirements documented for the selected snapshot.
