# Maintainer: rsteube <rsteube@users.noreply.github.com>

pkgname=carapace-bridge
pkgver=1.8.0_alpha
pkgrel=1
_upstream_version="${pkgver//_/-}"
pkgdesc='A multi-shell completion bridge'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-bridge'
license=('MIT')
makedepends=('go' 'git')
provides=('carapace-bridge-bin')
conflicts=('carapace-bridge-bin')
source=("$pkgname::git+$url.git#tag=v$_upstream_version")
sha256sums=('9c237f9dabe4891d92448f8b561e980c775b7f826dad000df0680a9d87e96f19')

prepare() {
    cd "$srcdir/$pkgname"
    export GOPATH="$srcdir"
    go mod download -modcacherw
}

build() {
    cd "$srcdir/$pkgname"
    export GOPATH="$srcdir"
    export GOFLAGS='-buildmode=pie -mod=readonly -modcacherw'
    export CGO_ENABLED=0
    go build -trimpath -ldflags="-X main.version=$_upstream_version" -o "$pkgname" "./cmd/$pkgname"
}

package() {
    cd "$srcdir/$pkgname"
    install -Dm755 "$pkgname" "$pkgdir/usr/bin/$pkgname"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
