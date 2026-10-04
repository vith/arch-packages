# Maintainer: vith
# Independently maintained recipe; upstream installer/build shell wrappers are not used.
pkgname=apexshot
pkgver=0.2.35
pkgrel=1
pkgdesc='Linux screenshot, annotation, OCR, and screen recording tool'
arch=('x86_64')
url='https://github.com/apex-shot/apexshot'
license=('GPL-3.0-or-later')
depends=(
  'gcc-libs'
  'glibc'
  'gtk4'
  'gtk4-layer-shell'
  'gettext'
  'gstreamer'
  'gst-plugins-base'
  'gst-plugins-good'
  'gst-plugins-bad'
  'gst-libav'
  'gst-plugin-pipewire'
  'pipewire'
  'libpulse'
  'tesseract'
  'tesseract-data-eng'
  'leptonica'
  'qt5-base'
  'qt5-x11extras'
  'qt5-wayland'
  'libx11'
  'libxtst'
  'xdg-desktop-portal'
  'xdg-utils'
  'wl-clipboard'
  'xclip'
  'libnotify'
  'ffmpeg'
  'inter-font'
  'hicolor-icon-theme'
  'curl'
  'wget'
  'unzip'
)
makedepends=('rust' 'git' 'cmake' 'clang' 'pkgconf')
optdepends=(
  'xdg-desktop-portal-gnome: GNOME portal backend'
  'xdg-desktop-portal-kde: KDE portal backend'
  'xdg-desktop-portal-hyprland: Hyprland portal backend'
  'xdg-desktop-portal-wlr: wlroots portal backend'
  'gnome-shell: optional bundled GNOME integration (enable manually)'
  'pipewire-pulse: PulseAudio-compatible server for recording audio'
  'gst-plugin-libcamera: camera capture through GStreamer'
  'grim: wlroots screenshot fallback'
  'slurp: wlroots region selection'
  'wf-recorder: wlroots recording fallback'
  'wtype: Wayland input for scrolling captures'
)
source=("apexshot::git+https://github.com/apex-shot/apexshot.git#tag=v${pkgver}")
sha256sums=('7889090aead5427da303e7c6bb390977d177ae05bc80e07ae6c66a8d3654a85e')
options=('!lto')

prepare() {
  cd apexshot
  export RUSTUP_TOOLCHAIN=stable
  cargo fetch --locked --target x86_64-unknown-linux-gnu
}

build() {
  cd apexshot
  export RUSTUP_TOOLCHAIN=stable
  export CARGO_TARGET_DIR=target
  # build.rs compiles the native Qt5/X11 overlay with CMake and writes both
  # apexshot-capture and the compiled translation catalogs beneath target/.
  cargo build --locked --offline --release --bin apexshot
  test -x target/release/apexshot-capture
}

package() {
  cd apexshot
  install -Dm755 target/release/apexshot "$pkgdir/usr/bin/apexshot"
  install -Dm755 target/release/apexshot-capture "$pkgdir/usr/bin/apexshot-capture"

  # The native messaging implementation is a subcommand of the compiled Rust
  # application, not a separate binary. Do not use upstream's /usr/local wrapper.
  install -d "$pkgdir/usr/bin"
  printf '%s\n' '#!/bin/sh' 'exec /usr/bin/apexshot native-host "$@"' \
    > "$pkgdir/usr/bin/apexshot-native-host"
  chmod 755 "$pkgdir/usr/bin/apexshot-native-host"
  local browser
  for browser in chromium opt/chrome; do
    install -Dm644 native-host/io.github.codegoddy.apexshot.json \
      "$pkgdir/etc/$browser/NativeMessagingHosts/io.github.codegoddy.apexshot.json"
  done

  install -Dm644 packaging/io.github.codegoddy.apexshot.desktop \
    "$pkgdir/usr/share/applications/io.github.codegoddy.apexshot.desktop"
  install -Dm644 packaging/io.github.codegoddy.apexshot.metainfo.xml \
    "$pkgdir/usr/share/metainfo/io.github.codegoddy.apexshot.metainfo.xml"
  install -Dm644 packaging/apexshot.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/apexshot.svg"
  install -Dm644 packaging/apexshot.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/io.github.codegoddy.apexshot.svg"
  install -Dm644 packaging/apexshot.svg "$pkgdir/usr/share/pixmaps/apexshot.svg"

  local asset lang
  for asset in src/capture/editor/background-images/*.jpg; do
    install -Dm644 "$asset" "$pkgdir/usr/share/apexshot/background-images/${asset##*/}"
  done
  for asset in assets/sounds/*.ogg; do
    install -Dm644 "$asset" "$pkgdir/usr/share/apexshot/sounds/${asset##*/}"
  done
  # Explicitly require every source language's compiled catalog rather than
  # silently producing an English-only package if build.rs stops generating them.
  for asset in po/*.po; do
    lang=${asset##*/}
    lang=${lang%.po}
    install -Dm644 "target/locale/$lang/LC_MESSAGES/apexshot.mo" \
      "$pkgdir/usr/share/locale/$lang/LC_MESSAGES/apexshot.mo"
  done

  # System extension only: no autostart file, enable hook, or user configuration.
  local ext_dir="$pkgdir/usr/share/gnome-shell/extensions/apexshot-gnome-integration@apexshot.github.io"
  for asset in metadata.json extension.js cursor-classifier.js shell-overlay.js window-list.js preview-stacking.js; do
    install -Dm644 "gnome-extension/$asset" "$ext_dir/$asset"
  done
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
