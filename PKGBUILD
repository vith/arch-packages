# Adapted from the AUR surge-cli source package.
pkgbase=surge-cli
pkgname=surge
pkgver=0.12.2
pkgrel=1
pkgdesc='Fast terminal download manager'
arch=('x86_64')
url='https://github.com/SurgeDM/Surge'
license=('MIT')
depends=('glibc' 'ca-certificates')
makedepends=('go>=1.26')
optdepends=('wl-clipboard: clipboard support on Wayland'
            'xclip: clipboard support on X11'
            'xsel: alternative clipboard support on X11'
            'xdg-utils: open downloads and configuration files')
conflicts=('surge-bin')
options=('!strip' '!debug')
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v${pkgver}.tar.gz")
sha512sums=('ba95c71d9e392b87bc5df1c84227700f24ddd3718a9bac28edce2601cad51d2b4fa78930536ad1349995f7f2e7be2351949199b94ab15779caf0ecadc51f6c6c')

build() {
  cd "$srcdir/Surge-$pkgver"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'
  local ldflags="-s -w -X github.com/SurgeDM/Surge/cmd.Version=${pkgver}"

  # Generate completions with an execution-host binary, never the x86 target.
  GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)" \
    go build -trimpath -ldflags="$ldflags" -o surge-host .
  mkdir -p completions
  ./surge-host completion bash > completions/surge.bash
  ./surge-host completion zsh > completions/surge.zsh
  ./surge-host completion fish > completions/surge.fish
  GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="$ldflags" -o surge .
}

package() {
  cd "$srcdir/Surge-$pkgver"
  install -Dm755 surge "$pkgdir/usr/bin/surge"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
  install -Dm644 completions/surge.bash "$pkgdir/usr/share/bash-completion/completions/surge"
  install -Dm644 completions/surge.zsh "$pkgdir/usr/share/zsh/site-functions/_surge"
  install -Dm644 completions/surge.fish "$pkgdir/usr/share/fish/vendor_completions.d/surge.fish"
}
