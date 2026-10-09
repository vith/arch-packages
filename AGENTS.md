# Arch packages

This project is the standalone `vith/arch-packages` GitHub repository.

- Manage GitHub repository settings, environments, permissions, and signing secrets through `opentofu/`. Use `tools/tofu.sh plan -out=github.tfplan`, inspect the plan, then `tools/tofu.sh apply github.tfplan`. Verify a subsequent plan has no changes.
- The wrapper uses the existing GitHub CLI login. Never print credentials. State and saved plans must remain encrypted and gitignored; recovery files live under `~/.local/state/arch-packages/` outside Git.
- Workflow and recipe changes are ordinary version-controlled source files. Do not use repository-file resources to overwrite source history.
- Statically freeze exact control/base/head/tree, source lock, policy and metadata claims without executing recipes. Persist the original candidate JSON with accepted-main OIDC prebuild provenance independently of compilation or artifact expiry. Only non-trivial PKGBUILD changes require human approval; independently validate unchanged/enrolled-literal PKGBUILD transitions automatically. Before any recipe invocation, workers must independently verify that automatic authorization or genuine protected `recipe-review` approval bound to the exact candidate/run/attempt; stored booleans or passing statuses are not authority.
- Build each authorized input once in its own independent GitHub-hosted x86_64 workflow run with accepted-main tools and no per-worker environment approval. Candidate coordination dispatches approved build workers and must print child links and state transitions live; worker build stdout must stream while remaining in the receipt. Publication never dispatches any worker, including transport recovery, and never compiles.
- All approved build workers restore/save compatible trusted dependency/compiler caches, including failed-compilation progress; do not exclude candidates or split caches by publication kind. Unapproved code must never populate trusted caches. Compiler caches are not durable package output.
- Protect the original compilation outcome with a root-owned checkpoint beginning before the first recipe execution. A successful compilation remains successful after metadata/runtime/copy/upload/attestation/storage failure. Recover its exact original bytes; if missing, refuse concretely rather than rebuilding. Only an authenticated actual failed compilation permits a retry; canceled, started or unknown outcomes require recovery or refusal.
- Retain raw output, canonical context and bound GitHub OIDC attestation bundle in durable draft `build-<inputdigest>` storage. These attestations are not pacman signatures, and GitHub assets are not platform-immutable: verify bound asset IDs, size/hash, certificate/workflow/run/attempt, original source/tree/metadata and runtime evidence before use. Explicit transport recovery executes no recipe, uses no compiler cache and has a distinct signer while preserving the original producer. Publication consumes authenticated existing bytes or unchanged signed content, never recasts original build evidence.
- Never compile packages on the workstation. Local Python tests, shell syntax checks, OpenTofu formatting and validation are appropriate.
- Isolated source tests must receive the complete exact tracked-source snapshot, including workflow/control files, never `.git`, ignored state or credentials. Main verification exports `GITHUB_SHA` with `git archive`; container test fixtures use the explicitly created `/verify/.work/tmp`, not `/tmp`.
- Fetch source Git objects directly from pinned upstream identities. Do not publish Git source bundles.
- Recipe proposals target protected `pkg/*` branches and contain only package-root payload. Only new/non-trivial PKGBUILD code requires the exact-head `recipe-review` checkpoint before execution. Unchanged PKGBUILD and enrolled literal version/pkgrel/checksum changes use independently reconstructed automatic authorization; auxiliary payload, metadata, policy or retirement alone must not add human gates. Invalid source/metadata transitions fail rather than ask for irrelevant approval. After genuine authorization and successful validation, merge automatically through normal exact-head branch protection; no separate human merge. Never approve recipe-review on the user's behalf.
- `main` records exact accepted recipe merges through independently reconstructed automatic bookkeeping, verifying reviewed-head-to-accepted-merge parents/tree without recipe execution or worker dispatch. Controller, workflow, policy, infrastructure and documentation changes validate and merge automatically, without code-review or bootstrap-admission approval. Source-only validation binds exact control/head/PR/workflow/run/attempt, proves zero changed package inputs, then runs exact-head tests isolated without credentials. Preserve the retired code-review environment ID and actual historical approval history for original producer verification, but never issue new code-review authority.
- Automatic non-bookkeeping main source candidates must have an independently observed same-repository head. Reject external, missing or null head repositories before saving candidate authority and again when reconstructing automatic authorization; do not add a human source gate. Native recipe and independently proven bookkeeping paths retain their distinct validation.
- New candidate preparation uses current trusted `main` tools; reuse of compatible approved output preserves its original control SHA. Keep control SHA, recipe base/head/tree and accepted merge identities distinct; durable receipts alone are not build or approval authority.
- Git packages follow their configured branch or tag when discovering updates; freeze the resolved commit only for that build's verification and publication, never as a permanent update freeze. Frozen Git rewrites must use `MAKEPKG_GIT_CONFIG` and matching `GIT_CONFIG_SYSTEM`, with `GIT_CONFIG_GLOBAL=/dev/null`: makepkg replaces the global configuration channel, so do not disable system config. Keep the config and source mirrors root-owned and read-only during recipe execution.
- Run legacy reconciliation through trusted `update.yml` dispatch with `migrate_pr`. Fresh recipe execution still requires exact authorization and must not repeat successful compilation; never transfer old approvals, directly synchronize protected recipe tips, or sign with a human token pretending to be the Actions bot.
- Package payload changes need a version or `pkgrel` bump. Bookkeeping, documentation, tooling, image and controller-only changes select zero package compilations. Pending unchanged approved base/head inputs must reuse successful original bytes and producer provenance across compatible control advancement; ordinary upstream/AUR ref advancement must not invalidate frozen approved inputs. A deliberate `pkgrel` bump requests a new authorized build.
- Never apply a copied local OpenTofu state from another checkout; use the canonical encrypted state and inspect saved plans before applying them.
- Limit changes to this project. Other software repositories and infrastructure have independent ownership and state.

## Initial automatic source-workflow cutover

Freeze accepted main as `C` and the final source-only PR head as `H`. Create or
update the PR against an owned staging base pinned to `C`. Mark it ready while
still targeting staging, then retarget main without changing `H`: the old
dispatcher reacts to opened/synchronize/reopened/ready events even for drafts,
but not edited events. Keep the exact-PR dispatch guard; do not bypass history or
branch protection.

Dispatch `verification.yml` with the exact PR/base/head and `bootstrap=true`
only from the direct-commit `source-review-<H>` tag. The managed
`source-review-immutable` ruleset permits creation but forbids update/deletion,
without bypass actors. Bootstrap selects the immutable initial controller, not
a human approval requirement. Git-object scope and actual workflow identity are
checked before checkout, and unchanged package content is verified before and
after isolated exact-head tests. Successful real checks permit ordinary protected
automatic merge and explicit publication dispatch; Actions-token merges do not
trigger push workflows. Publication reuses existing bytes and never compiles.
Never fabricate statuses, impersonate automation, or treat local tests as hosted
deployment acceptance.
