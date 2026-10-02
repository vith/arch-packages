# Maintainer:  Rubin Simons <me@rubin55.org>

pkgname=computer-use-linux
pkgver=0.7.7
pkgrel=2
pkgdesc="Control a real Linux desktop from any MCP host (AT-SPI, portals, multi-compositor window targeting)"
arch=('x86_64' 'aarch64')
url="https://github.com/agent-sh/computer-use-linux"
license=('MIT')
depends=('at-spi2-core' 'gcc-libs' 'glibc')
makedepends=('cargo')
checkdepends=('dbus')
optdepends=(
    'gnome-screenshot: screenshot fallback for background sessions'
    'hyprland: window targeting on Hyprland'
    'i3-wm: window targeting on i3'
    'niri: window targeting on niri'
    'sway: window targeting on Sway'
    'wmctrl: window management on generic X11/EWMH'
    'wtype: text input on wlroots compositors'
    'xdotool: keyboard input on X11 sessions'
    'xorg-xprop: window PID hydration on X11'
    'ydotool: input fallback when the RemoteDesktop portal is unavailable'
)
conflicts=('computer-use-linux-bin')
source=("${pkgname}-${pkgver}.tar.gz::${url}/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=('a34c0f03d14cc1f27d91081dfc8bbe6a721097411c7831f20de6a9c6829ff58c')

# CARCH is the package architecture, not the native ARM builder's host triple.
_target="${CARCH}-unknown-linux-gnu"

_build_env() {
    # Use the builder's installed toolchain; shared RUSTUP_HOME is read-only.
    if [[ $(uname -m) != "$CARCH" ]]; then
        [[ $CARCH == x86_64 ]] || { error "Unsupported cross target: $CARCH"; return 1; }
        export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc
        export CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc
        export CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++
        export AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar
    fi
    # GCC LTO hides mimalloc's C symbols from Rust's linker. Apply this to
    # tests too: they compile mimalloc independently.
    export CFLAGS="${CFLAGS//-flto=auto/}"
    export CXXFLAGS="${CXXFLAGS//-flto=auto/}"
    export CARGO_PROFILE_RELEASE_STRIP=none
    export CARGO_PROFILE_RELEASE_DEBUG=2
}

prepare() {
    cd "${pkgname}-${pkgver}"
    _build_env
    cargo fetch --locked --target "$_target"
}

build() {
    cd "${pkgname}-${pkgver}"
    _build_env
    cargo build --frozen --release --target "$_target"
}

check() {
    if [[ $(uname -m) != "$CARCH" ]]; then
        msg2 "Target execution tests require $CARCH; not emulating them on $(uname -m)"
        return 0
    fi
    cd "${pkgname}-${pkgver}"
    _build_env
    # The KWin backend tests spawn a private dbus-daemon (see checkdepends).
    cargo test --frozen --release --target "$_target"
}

package() {
    cd "${pkgname}-${pkgver}"
    # CI puts Cargo output outside the source tree in a persistent cache.
    install -Dm755 "${CARGO_TARGET_DIR:-target}/$_target/release/${pkgname}" "${pkgdir}/usr/bin/${pkgname}"
    install -Dm755 "${CARGO_TARGET_DIR:-target}/$_target/release/${pkgname}-cosmic" "${pkgdir}/usr/bin/${pkgname}-cosmic"
    install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
