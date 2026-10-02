# Maintainer: hexchain <i at hexchain dot org>

pkgname=mdevctl
pkgver=1.4.0
pkgrel=2
pkgdesc="A mediated device management utility for Linux"
url="https://github.com/mdevctl/mdevctl"
arch=('x86_64')
license=('LGPL-2.1-only')
depends=('glibc' 'gcc-libs')
makedepends=('rust' 'cargo' 'python-docutils')
source=("$pkgname-$pkgver.tar.gz::https://github.com/mdevctl/mdevctl/archive/v$pkgver.tar.gz"
        'Cargo.lock')
sha256sums=('0b3a36fc8412ec5a5cf58ad3ce514e79d58dcac71a133f313759c8c0793377a2'
            '110927d5c841a1f7cbac1928327d182fea402213e505f133e0d18facdecc7634')
options+=(emptydirs)

_target=x86_64-unknown-linux-gnu

_cross_env() {
    [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
    if [[ $(uname -m) != "$CARCH" ]]; then
        export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc
        export CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc
        export CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++
        export AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar
    fi
}

prepare() {
    cd "$pkgname-$pkgver"
    _cross_env
    install -m 644 "$srcdir/Cargo.lock" Cargo.lock
    # The generated Makefile otherwise assumes the host-default target/release path.
    sed -i 's|@@mdevctl@@|$(MDEVCTL_BIN)|' Makefile.in
    cargo fetch --locked --target "$_target"
}

build() {
    cd "$pkgname-$pkgver"
    _cross_env
    cargo build --frozen --release --all-features --target "$_target"
    mv Makefile Makefile.release
}

check() {
    if [[ $(uname -m) != "$CARCH" ]]; then
        msg2 "Target execution tests require $CARCH; not emulating them on $(uname -m)"
        return 0
    fi

    cd "$pkgname-$pkgver"
    _cross_env
    cargo test --frozen --all-features --target "$_target"
}

package() {
    cd "$pkgname-$pkgver"
    make -f Makefile.release DESTDIR="$pkgdir" SBINDIR="/usr/bin" \
        UDEVDIR=/usr/lib/udev \
        MDEVCTL_BIN="${CARGO_TARGET_DIR:-target}/$_target/release/mdevctl" install
}
