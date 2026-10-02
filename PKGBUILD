# Maintainer: xyzzy
# Contributor: Daniel M. Capella <polyzen@archlinux.org>

pkgname=spotify-adblock
pkgver=1.1.1
pkgrel=2
epoch=1
pkgdesc='Adblocker for Spotify'
arch=('x86_64')
url=https://github.com/abba23/spotify-adblock
license=('GPL3')
depends=('spotify')
makedepends=('rust')
provides=("$pkgname-linux")
replaces=("$pkgname-linux")
backup=('etc/spotify-adblock/config.toml')
options=('!strip')
source=("$url/archive/v$pkgver/$pkgname-$pkgver.tar.gz"
        "$pkgname.desktop")
b2sums=('a1eb5ff68e40defd8ef76f1cd26a8a11190764fcce15ff2ce9d09014a82e7ac91f4580388ef4670bec91cd3c6fac80dc4daa21daaaffebdd93fb528782b15def'
        '39f7b71aa8b6b894513812742b5ecbd4ab9ee60482f455555529be356b93724a663d9b4db21675b51db615594ed73fa7657eeab6d2b1679cd6a5572b2566a68a')

build() {
  case $CARCH in
    x86_64) ;;
    *) printf 'Unsupported target architecture: %s\n' "$CARCH" >&2; return 1 ;;
  esac

  if [[ $(uname -m) == aarch64 ]]; then
    export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc
    export CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc
    export CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++
    export AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar
  fi

  cd "$pkgname-$pkgver"
  local target_dir=${CARGO_TARGET_DIR:-"$srcdir/cargo-target"}
  CARGO_TARGET_DIR="$target_dir" cargo build --locked --release --target x86_64-unknown-linux-gnu
}

package() {
  case $CARCH in
    x86_64) ;;
    *) printf 'Unsupported target architecture: %s\n' "$CARCH" >&2; return 1 ;;
  esac

  cd "$pkgname-$pkgver"
  local target_dir=${CARGO_TARGET_DIR:-"$srcdir/cargo-target"}
  local strip_program=strip
  if [[ $(uname -m) == aarch64 ]]; then
    strip_program=x86_64-linux-gnu-strip
  fi
  install -D --mode=644 --strip --strip-program="$strip_program" \
    "$target_dir/x86_64-unknown-linux-gnu/release/libspotifyadblock.so" \
    "$pkgdir/usr/lib/spotify-adblock.so"
  install -D --mode=644 config.toml "$pkgdir/etc/spotify-adblock/config.toml"
  install -Dm644 -t "$pkgdir/usr/share/applications" "../$pkgname.desktop"
}

# vim:set ts=2 sw=2 et:
