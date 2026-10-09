pkgname=carapace-spec
pkgver=1.9.0
pkgrel=2
pkgdesc='A multi-shell completion spec'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-spec'
license=('MIT')
depends=('glibc')
makedepends=('git' 'go>=1.24')
conflicts=('carapace-spec-bin')
options=('!strip' '!debug')
source=("$pkgname::git+${url}.git#tag=v${pkgver}")
sha256sums=('84ff9c9bd0f1b6cbc7ead9c9452d45eeebcea8c9fc46d95ae857b9e8a2a0b005')

build() {
  cd "$srcdir/$pkgname"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'
  go generate ./schema.go
  GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="-s -w -X main.version=v${pkgver}" \
    -o carapace-spec ./cmd/carapace-spec
}

package() {
  cd "$srcdir/$pkgname"
  install -Dm755 carapace-spec "$pkgdir/usr/bin/carapace-spec"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
