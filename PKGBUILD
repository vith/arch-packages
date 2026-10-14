# Maintainer: rsteube <rsteube@users.noreply.github.com>

pkgname=carapace-spec
pkgver=1.10.0_alpha
pkgrel=1
_upstream_version="${pkgver//_/-}"
pkgdesc='A multi-shell completion spec'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-spec'
license=('MIT')
makedepends=('go' 'git')
provides=('carapace-spec-bin')
conflicts=('carapace-spec-bin')
source=("$pkgname::git+$url.git#tag=v$_upstream_version")
sha256sums=('ff48e5eeae52f48b9dd5273471cee87ff932db3f1817a3bce09bab884c8bf29c')

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
    go generate ./...
    go build -trimpath -ldflags="-X main.version=$_upstream_version" -o "$pkgname" "./cmd/$pkgname"
}

package() {
    cd "$srcdir/$pkgname"
    install -Dm755 "$pkgname" "$pkgdir/usr/bin/$pkgname"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
