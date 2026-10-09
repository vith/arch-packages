# Arch packages

This project is the standalone `vith/arch-packages` GitHub repository.

- Manage GitHub repository settings, environments, permissions, and signing secrets through `opentofu/`. Use `tools/tofu.sh plan -out=github.tfplan`, inspect the plan, then `tools/tofu.sh apply github.tfplan`. Verify a subsequent plan has no changes.
- The wrapper uses the existing GitHub CLI login. Never print credentials. State and saved plans must remain encrypted and gitignored; recovery files live under `~/.local/state/arch-packages/` outside Git.
- Workflow and recipe changes are ordinary version-controlled source files. Do not use repository-file resources to overwrite source history.
- Build each package in its own GitHub-hosted x86_64 workflow run. Publication collects validated outputs and signs the repository; it does not compile packages.
- Never compile packages on the workstation. Local Python tests, shell syntax checks, OpenTofu formatting and validation are appropriate.
- Fetch source Git objects directly from pinned upstream identities. Do not publish Git source bundles.
- Recipe proposals target protected `pkg/*` branches and contain only package-root payload. Mechanical updates use independently verified automatic acceptance; non-mechanical updates require the `recipe-review` environment and human merge. Never approve that review or bypass protection on the user's behalf.
- `main` records exact accepted recipe merges through independently reconstructed automatic bookkeeping. Other controller, policy or infrastructure changes require human `code-review`; generated bookkeeping does not grant arbitrary code approval.
- Candidate tools come from the current trusted `main` revision. Keep control SHA, recipe base/head/tree and accepted merge identities distinct; durable receipts alone are not build or approval authority.
- Run legacy reconciliation through trusted `update.yml` dispatch with `migrate_pr`. It creates fresh native validation; never transfer old approvals, directly synchronize protected recipe tips, or sign with a human token pretending to be the Actions bot.
- Package payload changes need a version or `pkgrel` bump. Same-tree acceptance bookkeeping and CI-only changes retain the original signed package content and build provenance.
- Never apply a copied local OpenTofu state from another checkout; use the canonical encrypted state and inspect saved plans before applying them.
- Git-package updates follow their configured branch or tag; verification and signing use exact per-build source identities, not permanent update freezes.
- Frozen Git rewrites must use `MAKEPKG_GIT_CONFIG` and matching `GIT_CONFIG_SYSTEM`, with `GIT_CONFIG_GLOBAL=/dev/null`. Do not disable system config: makepkg replaces the global configuration channel. Keep the config and source mirrors root-owned and read-only during recipe execution.
- The scoped `repair/frozen-git` bootstrap keeps accepted-main package inputs separate from the exact human-reviewed repair tools. Its parent and seven independent hosted child runs require protected `code-review` before executing those tools; no caches, signing, package updates or automated human approval/merge belong in this path. Ordinary validation and publication remain controlled by accepted main.
- Limit changes to this project. Other software repositories and infrastructure have independent ownership and state.
