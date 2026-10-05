# Maintainer: vith
pkgname=nasctui
_pkgname=nascTUI
pkgver=1.0.7
pkgrel=1
pkgdesc='The Not a Soulver Clone TUI'
arch=('x86_64')
url='https://github.com/parnoldx/nascTUI'
license=('GPL-2.0-only')
depends=('glibc' 'gcc-libs' 'libqalculate')
makedepends=('go>=1.24' 'gcc' 'pkgconf')
conflicts=('nasc-tui-bin')
replaces=('nasc-tui-bin')
options=('!lto' '!strip' '!debug')
source=("${_pkgname}-${pkgver}.tar.gz::https://github.com/parnoldx/${_pkgname}/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=('db12a62028c986c161b86844920452ab997f208ff45a8b3757f58a7f27ac1219')

build() {
  cd "$srcdir/${_pkgname}-${pkgver}/src"
  export GOTOOLCHAIN=local CGO_ENABLED=1
  export GOFLAGS='-mod=readonly -modcacherw'
  export CGO_CFLAGS="$CFLAGS" CGO_CPPFLAGS="$CPPFLAGS"
  export CGO_CXXFLAGS="$CXXFLAGS" CGO_LDFLAGS="$LDFLAGS"
  go build -trimpath -buildmode=pie -ldflags="-X main.version=v${pkgver}" -o nasc .
}

check() {
  cd "$srcdir/${_pkgname}-${pkgver}/src"
  GOTOOLCHAIN=local CGO_ENABLED=1 go test -mod=readonly ./...
}

package() {
  install -Dm755 "$srcdir/${_pkgname}-${pkgver}/src/nasc" "$pkgdir/usr/bin/nasc"
  install -Dm644 "$srcdir/${_pkgname}-${pkgver}/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 "$srcdir/${_pkgname}-${pkgver}.tar.gz" "$pkgdir/usr/share/doc/$pkgname/${_pkgname}-${pkgver}.tar.gz"
}
