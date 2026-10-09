# Arch packages

This project is the standalone `vith/arch-packages` GitHub repository.

- Manage GitHub repository settings, environments, permissions, and signing secrets through `opentofu/`. Use `tools/tofu.sh plan -out=github.tfplan`, inspect the plan, then `tools/tofu.sh apply github.tfplan`. Verify a subsequent plan has no changes.
- The wrapper uses the existing GitHub CLI login. Never print credentials. State and saved plans must remain encrypted and gitignored; recovery files live under `~/.local/state/arch-packages/` outside Git.
- Workflow and recipe changes are ordinary version-controlled source files. Do not use repository-file resources to overwrite source history.
- Build each package in its own GitHub-hosted x86_64 workflow run. Publication collects validated outputs and signs the repository; it does not compile packages.
- Never compile packages on the workstation. Local Python tests, shell syntax checks, OpenTofu formatting and validation are appropriate.
- Fetch source Git objects directly from pinned upstream identities. Do not publish Git source bundles.
- Git-package updates follow their configured branch or tag; verification and signing use exact per-build source identities, not permanent update freezes.
- Frozen Git rewrites must use `MAKEPKG_GIT_CONFIG` and matching `GIT_CONFIG_SYSTEM`, with `GIT_CONFIG_GLOBAL=/dev/null`. Do not disable system config: makepkg replaces the global configuration channel. Keep the config and source mirrors root-owned and read-only during recipe execution.
- The scoped `repair/frozen-git` bootstrap keeps accepted-main package inputs separate from the exact human-reviewed repair tools. Its parent and seven independent hosted child runs require protected `code-review` before executing those tools; no caches, signing, package updates or automated human approval/merge belong in this path. Ordinary validation and publication remain controlled by accepted main.
- Limit changes to this project. Other software repositories and infrastructure have independent ownership and state.
