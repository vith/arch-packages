# Maintainer: Eric Torres <eric.torres@its-et.me>
# Contributor: Michael Gebetsroither <m.gebetsr@gmail.com>
# Contributor: kemelzaidan
pkgbase=regclient
pkgname=(
  regclient-regctl
  regclient-regsync
  regclient-regbot
)
pkgver=0.11.2
pkgrel=2
pkgdesc='Docker and OCI Registry tooling - regctl / regsync / regbot'
arch=('x86_64' 'aarch64')
url='https://github.com/regclient/regclient'
license=('Apache-2.0')
makedepends=('go')
depends=('glibc')
source=("https://github.com/regclient/regclient/archive/v$pkgver/$pkgbase-$pkgver.tar.gz")
sha256sums=('f09ccd1a9e9872cc3bff957a4a54729643f0869491932d2a074c2974f8e2cb70')
build() {
  local target host package bin shell
  case "$CARCH" in
    x86_64) target=amd64 ;;
    aarch64) target=arm64 ;;
    *) return 1 ;;
  esac
  host="$(go env GOHOSTARCH)"
  export CGO_ENABLED=0
  export GOFLAGS="-trimpath -mod=readonly -modcacherw"
  mkdir -p "$srcdir/build"
  cd "$srcdir/$pkgbase-$pkgver"

  for package in "${pkgname[@]}"; do
    bin="${package#regclient-}"
    GOOS=linux GOARCH="$host" go build -o "$srcdir/build/$bin-host" "./cmd/$bin"
    for shell in bash fish zsh; do
      "$srcdir/build/$bin-host" completion "$shell" > "$srcdir/build/$bin.$shell"
    done
    if [[ "$target" == "$host" ]]; then
      mv "$srcdir/build/$bin-host" "$srcdir/build/$bin"
    else
      GOOS=linux GOARCH="$target" go build -o "$srcdir/build/$bin" "./cmd/$bin"
      rm "$srcdir/build/$bin-host"
    fi
  done
}

_pkgcommon() {
  _pkg="$1"
  cd build/
  install -Dm755 "$_pkg" -t   "$pkgdir/usr/bin"
  install -Dm644 "$_pkg.bash" "$pkgdir/usr/share/bash-completion/completions/$_pkg"
  install -Dm644 "$_pkg.fish" "$pkgdir/usr/share/fish/vendor_completions.d/$_pkg.fish"
  install -Dm644 "$_pkg.zsh"  "$pkgdir/usr/share/zsh/site-functions/_$_pkg"
  install -Dm644 "../$pkgbase-$pkgver/LICENSE" "$pkgdir/usr/share/licenses/$pkgbase-$_pkg/LICENSE"
}

package_regclient-regctl() {
  pkgdesc="Utility for accessing docker registries"
  _pkgcommon "regctl"
}

package_regclient-regsync() {
  pkgdesc="Utility for mirroring docker repositories"
  _pkgcommon "regsync"
}

package_regclient-regbot() {
  pkgdesc="Utility for automating repository actions"
  _pkgcommon "regbot"
}
