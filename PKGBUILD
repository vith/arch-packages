# Maintainer: xyzzy
# Contributor: Daniel M. Capella <polyzen@archlinux.org>

pkgname=spotify-adblock
pkgver=1.1.1
pkgrel=3
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
sha256sums=('9c39ad9251503bc45f08a6319b53578f2e7d1ed7ab3187c1f7ed8747f363fe2c'
            '408e5400a57e03a4a4a8da9f56710f6d4437b2d11df43df503443618fb75bcb3')

build() {
  cd "$pkgname-$pkgver"
  local target_dir=${CARGO_TARGET_DIR:-"$srcdir/cargo-target"}
  CARGO_TARGET_DIR="$target_dir" cargo build --locked --release
}

package() {
  cd "$pkgname-$pkgver"
  local target_dir=${CARGO_TARGET_DIR:-"$srcdir/cargo-target"}
  install -D --mode=644 --strip \
    "$target_dir/release/libspotifyadblock.so" \
    "$pkgdir/usr/lib/spotify-adblock.so"
  install -D --mode=644 config.toml "$pkgdir/etc/spotify-adblock/config.toml"
  install -Dm644 -t "$pkgdir/usr/share/applications" "../$pkgname.desktop"
}

# vim:set ts=2 sw=2 et:
