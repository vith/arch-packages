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
- Limit changes to this project. Other software repositories and infrastructure have independent ownership and state.
