# Adapted from the AUR carapace source package.
pkgname=carapace
pkgver=1.8.0
pkgrel=2
pkgdesc='Multi-shell multi-command argument completer'
arch=('x86_64')
url='https://carapace.sh/'
license=('MIT')
depends=('glibc')
makedepends=('go>=1.26.2')
conflicts=('carapace-bin')
options=('!strip' '!debug')
source=("$pkgname-$pkgver.tar.gz::https://github.com/carapace-sh/carapace-bin/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=('f29dec6afe57675a01076e94cd3850327b5106b47e557d213958124bc2f3cabb')

build() {
  cd "$srcdir/carapace-bin-$pkgver"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'

  # Generators run on the execution host; Windows-only shim generation is not
  # needed for the Linux binary. Keep the runner's persistent Go caches.
  GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)" \
    go generate ./cmd/carapace/main.go
  GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="-s -w -X main.version=v${pkgver}" -tags=release,force_all \
    -o carapace ./cmd/carapace
}

package() {
  cd "$srcdir/carapace-bin-$pkgver"
  install -Dm755 carapace "$pkgdir/usr/bin/carapace"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
