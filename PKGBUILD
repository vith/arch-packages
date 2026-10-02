pkgname=carapace-bridge
pkgver=1.7.0
pkgrel=1
pkgdesc='A multi-shell completion bridge'
arch=('x86_64')
url='https://github.com/carapace-sh/carapace-bridge'
license=('MIT')
depends=('glibc')
makedepends=('git' 'go>=1.24')
optdepends=('bash-completion: bridge Bash completions'
            'fish: bridge Fish completions'
            'zsh: bridge Zsh completions')
conflicts=('carapace-bridge-bin')
options=('!strip' '!debug')
source=("$pkgname::git+${url}.git#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "$srcdir/$pkgname"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  # The upstream workspace includes both the library and the CLI module.
  # CARCH describes the package target, not the native Go execution host.
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 \
    go build -mod=readonly -modcacherw -trimpath -buildmode=pie \
    -ldflags="-s -w -X main.version=v${pkgver}" \
    -o carapace-bridge ./cmd/carapace-bridge
}

package() {
  cd "$srcdir/$pkgname"
  install -Dm755 carapace-bridge "$pkgdir/usr/bin/carapace-bridge"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 third_party/github.com/Valodim/zsh-capture-completion/LICENSE \
    "$pkgdir/usr/share/licenses/$pkgname/zsh-capture-completion.LICENSE"
}
