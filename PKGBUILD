# Maintainer: Your Name <youremail@example.com>
pkgname=cloudflare-speed-cli
_pkgname=cloudflare-speed-cli
pkgver=1.0.9
pkgrel=1
pkgdesc="CLI tool for Cloudflare speed testing with TUI interface"
arch=('x86_64')
url="https://github.com/kavehtehrani/cloudflare-speed-cli"
license=('GPL3')
depends=('gcc-libs')
makedepends=('cargo' 'git')
source=("$_pkgname::git+$url.git#tag=v$pkgver")
sha256sums=('0bd60b9d37b136436dfa340129b8288acbb3f41131b471b7c883ae1b192be8be')
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
