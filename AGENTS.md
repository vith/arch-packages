# Arch packages

This project is the standalone `vith/arch-packages` GitHub repository.

- Manage GitHub repository settings, environments, permissions, and signing secrets through `opentofu/`. Use `tools/tofu.sh plan -out=github.tfplan`, inspect the plan, then `tools/tofu.sh apply github.tfplan`. Verify a subsequent plan has no changes.
- The wrapper uses the existing GitHub CLI login. Never print credentials. State and saved plans must remain encrypted and gitignored; recovery files live under `~/.local/state/arch-packages/` outside Git.
- Workflow and recipe changes are ordinary version-controlled source files. Do not use repository-file resources to overwrite source history.
- Statically freeze exact control/base/head/tree, source lock, policy and metadata claims without executing recipes. Persist the original candidate JSON with accepted-main OIDC prebuild provenance independently of compilation or artifact expiry. Before any recipe invocation, workers must independently verify strict mechanical authorization or genuine protected `recipe-review` approval bound to that exact candidate/run/attempt; stored booleans or passing statuses are not authority.
- Build each authorized input once in its own independent GitHub-hosted x86_64 workflow run with accepted-main tools and no per-worker environment approval. Candidate coordination dispatches approved build workers and must print child links and state transitions live; worker build stdout must stream while remaining in the receipt. Publication never dispatches any worker, including transport recovery, and never compiles.
- All approved build workers restore/save compatible trusted dependency/compiler caches, including failed-compilation progress; do not exclude candidates or split caches by publication kind. Unapproved code must never populate trusted caches. Compiler caches are not durable package output.
- Protect the original compilation outcome with a root-owned checkpoint beginning before the first recipe execution. A successful compilation remains successful after metadata/runtime/copy/upload/attestation/storage failure. Recover its exact original bytes; if missing, refuse concretely rather than rebuilding. Only an authenticated actual failed compilation permits a retry; canceled, started or unknown outcomes require recovery or refusal.
- Retain raw output, canonical context and bound GitHub OIDC attestation bundle in durable draft `build-<inputdigest>` storage. These attestations are not pacman signatures, and GitHub assets are not platform-immutable: verify bound asset IDs, size/hash, certificate/workflow/run/attempt, original source/tree/metadata and runtime evidence before use. Explicit transport recovery executes no recipe, uses no compiler cache and has a distinct signer while preserving the original producer. Publication consumes authenticated existing bytes or unchanged signed content, never recasts original build evidence.
- Never compile packages on the workstation. Local Python tests, shell syntax checks, OpenTofu formatting and validation are appropriate.
- Fetch source Git objects directly from pinned upstream identities. Do not publish Git source bundles.
- Recipe proposals target protected `pkg/*` branches and contain only package-root payload. Mechanical updates use independently verified automatic acceptance; non-mechanical updates require the `recipe-review` environment and human merge, bound to the exact PR head and diff. Keep this meaningful checkpoint, not repeated empty package gates. Never approve that review or bypass protection on the user's behalf.
- `main` records exact accepted recipe merges through independently reconstructed automatic bookkeeping, verifying reviewed-head-to-accepted-merge parents/tree without recipe execution or worker dispatch. Other controller, policy or infrastructure changes require human `code-review` before proposed code/tests execute and a human merge; generated bookkeeping does not grant arbitrary code approval. Source-only validation dispatches accepted-main tools, gates the exact control/head/PR, proves zero changed package inputs, then runs approved-head tests isolated without credentials.
- New candidate preparation uses current trusted `main` tools; reuse of compatible approved output preserves its original control SHA. Keep control SHA, recipe base/head/tree and accepted merge identities distinct; durable receipts alone are not build or approval authority.
- Git packages follow their configured branch or tag when discovering updates; freeze the resolved commit only for that build's verification and publication, never as a permanent update freeze. Frozen Git rewrites must use `MAKEPKG_GIT_CONFIG` and matching `GIT_CONFIG_SYSTEM`, with `GIT_CONFIG_GLOBAL=/dev/null`: makepkg replaces the global configuration channel, so do not disable system config. Keep the config and source mirrors root-owned and read-only during recipe execution.
- Run legacy reconciliation through trusted `update.yml` dispatch with `migrate_pr`. Fresh recipe execution still requires exact authorization and must not repeat successful compilation; never transfer old approvals, directly synchronize protected recipe tips, or sign with a human token pretending to be the Actions bot.
- Package payload changes need a version or `pkgrel` bump. Bookkeeping, documentation, tooling, image and controller-only changes select zero package compilations. Pending unchanged approved base/head inputs must reuse successful original bytes and producer provenance across compatible control advancement; ordinary upstream/AUR ref advancement must not invalidate frozen approved inputs. A deliberate `pkgrel` bump requests a new authorized build.
- Never apply a copied local OpenTofu state from another checkout; use the canonical encrypted state and inspect saved plans before applying them.
- Limit changes to this project. Other software repositories and infrastructure have independent ownership and state.

## Initial source-review bootstrap

Use this only for the initial source-only workflow cutover, not routine approval.
Freeze accepted main as `C` and the final PR head as `H`. Create the PR against an
owned staging base pinned to `C`, then retarget it to main without changing `H`;
the old dispatcher reacts to opened/synchronize/reopened/ready events even for
drafts, but not edited events. Keep the exact-PR dispatch guard; do not bypass
history or branch protection.

Generate the exact admission body with:

```sh
env -u GH_TOKEN -u GITHUB_TOKEN GITHUB_REPOSITORY=vith/arch-packages \
  python3 -m tools.source_review --pr N --base C --head H --admission-body
```

The configured required human must submit that canonical `decision=approve-bootstrap`
body as a COMMENTED or APPROVED PR review bound to `H` before dispatch. The
assistant may propose the body but must never submit the review. COMMENTED with
this explicit body is separate authorization, not native GitHub PR approval.

Dispatch only from the direct-commit `source-review-<H>` tag, protected by the
managed `source-review-immutable` ruleset: creation allowed, update/deletion denied,
no bypass actors. Genuine protected `code-review` environment approval and human
main merge remain mandatory. Never fabricate required statuses, use a personal
token to impersonate automation, or treat local tests or transport smoke as live
deployment acceptance.
