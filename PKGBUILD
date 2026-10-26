# Maintainer: Your Name <youremail@example.com>
pkgname=cloudflare-speed-cli
_pkgname=cloudflare-speed-cli
pkgver=1.0.11
pkgrel=1
pkgdesc="CLI tool for Cloudflare speed testing with TUI interface"
arch=('x86_64')
url="https://github.com/kavehtehrani/cloudflare-speed-cli"
license=('GPL3')
depends=('gcc-libs')
makedepends=('cargo' 'git')
source=("$_pkgname::git+$url.git#tag=v$pkgver")
sha256sums=('7103d1511be7bc634b3769921b00b70ac759a3ad7de4bde7ca57ec56be0679d4')
options=('!lto')

prepare() {
  cd "$_pkgname"
  export RUSTUP_TOOLCHAIN=stable
  cargo fetch --locked --target "$(rustc -vV | sed -n 's/host: //p')"
}

build() {
  cd "$_pkgname"
  export RUSTUP_TOOLCHAIN=stable
  export CARGO_TARGET_DIR=target
  cargo build --frozen --release --all-features
}

check() {
  cd "$_pkgname"
  export RUSTUP_TOOLCHAIN=stable
  cargo test --frozen --all-features
}

package() {
  cd "$_pkgname"
  install -Dm755 "target/release/$_pkgname" "$pkgdir/usr/bin/$_pkgname"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
