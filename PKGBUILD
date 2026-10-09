# Adapted from the AUR surge-cli source package.
pkgbase=surge-cli
pkgname=surge
pkgver=0.12.2
pkgrel=2
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
sha256sums=('f2e0ca5917aed7c9790be212e364025d245fe3387c49d833b406becf5b0ee896')

build() {
  cd "$srcdir/Surge-$pkgver"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'
  local ldflags="-s -w -X github.com/SurgeDM/Surge/cmd.Version=${pkgver}"

  GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="$ldflags" -o surge .
  mkdir -p completions
  ./surge completion bash > completions/surge.bash
  ./surge completion zsh > completions/surge.zsh
  ./surge completion fish > completions/surge.fish
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
