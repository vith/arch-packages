# vith-gh

Personal x86_64 Arch Linux packages, built on GitHub Actions and distributed as signed GitHub Releases. All executable packages are built from source, not repackaged upstream binaries. Packages appear as `vith-gh/<package>` in pacman and paru. The source project remains at [vith/arch-packages](https://github.com/vith/arch-packages).

## Packages

| Package | Source |
| --- | --- |
| `archive-mounter` | AUR recipe |
| `carapace` | AUR-derived recipe, updated from upstream tags |
| `nasctui` | Upstream Go/C++ source tags, linked against system libqalculate |
| `cloudflare-speed-cli` | Upstream source tags |
| `oh-my-pi-vith-git` | [vith/oh-my-pi](https://github.com/vith/oh-my-pi) |
| `python-google-genai` | AUR-derived recipe |
| `apexshot` | [Upstream source tags](https://github.com/apex-shot/apexshot), maintained source-build recipe |

`nasctui` replaces `nasc-tui-bin` and keeps the `nasc` command. The archive-mounter package contains a desktop entry rather than a compiled program. Carapace is compiled from Go source; `carapace-bin` is the upstream source repository's name, not a prebuilt package input.

`oh-my-pi-vith-git` follows the fork's `integration` branch. Each signed snapshot records the actual source commit and resulting package version. This is a per-build identity, not a permanent pin preventing later Git updates.

The Google SDK recipe omits the unavailable `python-sentencepiece` build/test dependency. Upstream already excludes tokenizer tests; the optional runtime dependency is unchanged.

ApexShot includes its native capture overlay and optional GNOME Shell integration for Shell 48–50. The extension is installed system-wide but is not enabled automatically. Log out and back in after installation, then enable **ApexShot** in GNOME Extensions if desired. The package does not enable desktop autostart.

## Install

Download [the public signing key](https://github.com/vith/arch-packages/releases/latest/download/arch-packages.asc). Before trusting it, verify its full fingerprint:

```text
9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617
```

```fish
sudo pacman-key --add arch-packages.asc
sudo pacman-key --lsign-key 9C293ABB1F701DA04BA2C0D5711FC9BDDC5AF617
```

Add this section to `/etc/pacman.conf`, then run `sudo pacman -Syu`:

```ini
[vith-gh]
SigLevel = Required
Server = https://github.com/vith/arch-packages/releases/latest/download
```

Packages and repository databases are signed. If a database/signature download straddles a release update, retry a fresh sync; do not disable signature checking.

For a fixed snapshot, replace `releases/latest/download` with `releases/download/SNAPSHOT_TAG`.

If you previously configured `[arch-packages]`, rename that section to `[vith-gh]` after the new database is published. Keep its server URL and `SigLevel = Required`; the signing key is unchanged. Do not rename a separate `[vith-arch]` repository.

Historical snapshots retain their original database filenames. When selecting one explicitly, use `[arch-packages]` if it contains `arch-packages.db`, or `[vith-gh]` if it contains `vith-gh.db`.

## Source and system guide

Source updates are checked every six hours. Recipes and their history are available in this repository. Package contents can change between snapshots; use a fixed snapshot URL when you need a retained release.

See [System flow](docs/system-flow.md) for recipe contributions, workflow contracts, trust boundaries, operator commands, recovery, and the current rollout status. Publication verification, signed installation proof, and installation on your own machine are separate steps. ApexShot's automated checks do not prove interactive capture or recording in a live GNOME/Wayland session.

## Licensing and retained source

Packages retain their upstream licensing. Signed history and corresponding-source/license assets remain available with retained releases.

The preserved Nasc history includes old binaries whose metadata incorrectly said MIT. The maintained recipe corrects this to GPLv2; [corresponding source and the upstream license](https://github.com/vith/arch-packages/releases/tag/history-source-nasc-v1.0.4) remain available with that history.
