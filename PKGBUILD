pkgname=carapace-spec-man
pkgver=1.0.0
pkgrel=1
pkgdesc='Generate completion specs from manpages'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-spec-man'
license=('MIT')
depends=('glibc' 'man-db')
makedepends=('git' 'go>=1.21')
conflicts=('carapace-spec-man-bin')
options=('!strip' '!debug')
source=("$pkgname::git+${url}.git#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "$srcdir/$pkgname"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 \
    go build -mod=readonly -modcacherw -trimpath -buildmode=pie -tags=release \
    -ldflags="-s -w -X main.version=v${pkgver}" \
    -o carapace-spec-man ./cmd/carapace-spec-man
}

package() {
  cd "$srcdir/$pkgname"
  install -Dm755 carapace-spec-man "$pkgdir/usr/bin/carapace-spec-man"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
