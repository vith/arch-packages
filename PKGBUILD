# Adapted from the AUR swag source package.
pkgname=swag
pkgver=1.16.6
pkgrel=1
pkgdesc='Generate Swagger 2.0 documentation for Go APIs'
arch=('x86_64')
url='https://github.com/swaggo/swag'
license=('MIT')
depends=('glibc' 'go')
conflicts=('swag-bin')
options=('!strip' '!debug')
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=('d0193f08b829e1088753ff6d66d1205e22a6e7fd07ac28df5ecb001d9eb2c43d')

build() {
  cd "$srcdir/$pkgname-$pkgver"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  # Match the Makefile's build command without its dependency-tidying target.
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 \
    go build -mod=readonly -modcacherw -trimpath -buildmode=pie \
    -ldflags='-s -w' -o swag ./cmd/swag
}

package() {
  cd "$srcdir/$pkgname-$pkgver"
  install -Dm755 swag "$pkgdir/usr/bin/swag"
  install -Dm644 license "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
