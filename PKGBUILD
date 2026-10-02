pkgname=carapace-spec
pkgver=1.9.0
pkgrel=1
pkgdesc='A multi-shell completion spec'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-spec'
license=('MIT')
depends=('glibc')
makedepends=('git' 'go>=1.24')
conflicts=('carapace-spec-bin')
options=('!strip' '!debug')
source=("$pkgname::git+${url}.git#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "$srcdir/$pkgname"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'
  # The schema generator must execute natively, before selecting the target.
  GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)" go generate ./schema.go
  GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="-s -w -X main.version=v${pkgver}" \
    -o carapace-spec ./cmd/carapace-spec
}

package() {
  cd "$srcdir/$pkgname"
  install -Dm755 carapace-spec "$pkgdir/usr/bin/carapace-spec"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
