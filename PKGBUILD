# Based on the AUR fresh-editor recipe by Noam Lewis.

pkgname=fresh-editor
pkgver=0.5.2
pkgrel=1
pkgdesc='A lightweight, fast terminal-based text editor with LSP support and TypeScript plugins'
url='https://sinelaw.github.io/fresh/'
license=('GPL-3.0-or-later')
arch=('x86_64')
depends=('gcc-libs' 'glibc' 'ca-certificates')
makedepends=('rustup' 'pkgconf')
conflicts=('fresh-editor-bin')
options=('!debug' '!lto' '!strip')
source=("fresh-editor-${pkgver}-source.tar.gz::https://github.com/sinelaw/fresh/releases/download/v${pkgver}/fresh-editor-${pkgver}-source.tar.gz")
sha256sums=('1474c67bebb248e6e68c98c54a91a76212cc49d037e30ac5d20a35393ae1a043')

_target=x86_64-unknown-linux-gnu

_cross_env() {
    [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
    # Use the image's installed Rust and target stdlib, not the source tree's
    # rust-toolchain.toml (which would download into root-owned RUSTUP_HOME).
    export RUSTUP_TOOLCHAIN=nightly-2026-08-12
    export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$srcdir/target}"

    # QuickJS proc macros and the syntect/oniguruma syntax-pack generator run
    # on the host. Only target libraries may use the x86_64 cross compiler.
    export HOST_CC=gcc HOST_CXX=g++ HOST_AR=ar
    local prefix=
    if [[ $(uname -m) != x86_64 ]]; then
        prefix=x86_64-linux-gnu-
    fi
    export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="${prefix}gcc"
    export CC_x86_64_unknown_linux_gnu="${prefix}gcc"
    export CXX_x86_64_unknown_linux_gnu="${prefix}g++"
    export AR_x86_64_unknown_linux_gnu="${prefix}ar"
    # cc-rs combines generic and target flags; do not leak makepkg's x86
    # flags into the native ARM build dependencies.
    export CFLAGS_x86_64_unknown_linux_gnu="${CFLAGS_x86_64_unknown_linux_gnu:-${CFLAGS:-} ${CPPFLAGS:-}}"
    export CXXFLAGS_x86_64_unknown_linux_gnu="${CXXFLAGS_x86_64_unknown_linux_gnu:-${CXXFLAGS:-} ${CPPFLAGS:-}}"
    unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS

    # Both crates ship their C sources. Avoid probing host libraries or
    # requiring a target liblzma/oniguruma development sysroot.
    export LZMA_API_STATIC=1
    export RUSTONIG_SYSTEM_LIBONIG=0 RUSTONIG_STATIC_LIBONIG=1
}

prepare() {
    cd "fresh-$pkgver"
    _cross_env
    cargo fetch --locked --target "$_target"
}

build() {
    cd "fresh-$pkgver"
    _cross_env
    export FRESH_BUILD_CHANNEL=aur
    # Keep upstream's default plugins/runtime and the AUR's opt-in web UI.
    cargo build --frozen --release --package fresh-editor --bin fresh \
        --features web --target "$_target"
}

package() {
    cd "fresh-$pkgver"
    local target_dir="${CARGO_TARGET_DIR:-$srcdir/target}"
    install -Dm755 "$target_dir/$_target/release/fresh" "$pkgdir/usr/share/$pkgname/fresh"
    install -dm755 "$pkgdir/usr/bin"
    ln -s "/usr/share/$pkgname/fresh" "$pkgdir/usr/bin/fresh"

    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    cat > "$pkgdir/usr/share/$pkgname/install-receipt.toml" <<EOF
schema = 1
channel = "aur"
version = "$pkgver"
package_name = "fresh-editor"
managed = true
self_update = false

[hints]
aur_pkg = "fresh-editor"
EOF

    cp -r crates/fresh-editor/plugins "$pkgdir/usr/share/$pkgname/"
    cp -r crates/fresh-editor-core/keymaps "$pkgdir/usr/share/$pkgname/"
    install -Dm644 crates/fresh-editor/resources/fresh.desktop \
        "$pkgdir/usr/share/applications/fresh.desktop"
    local icon size
    for icon in docs/icons/linux/hicolor/*/apps/fresh.png; do
        size=${icon%/apps/fresh.png}
        size=${size##*/}
        install -Dm644 "$icon" "$pkgdir/usr/share/icons/hicolor/$size/apps/fresh.png"
    done
}
