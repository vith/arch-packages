# Adapted from the upstream-maintained AUR crush source package.
pkgname=crush
pkgver=0.97.1
pkgrel=1
pkgdesc='Terminal-based AI coding assistant'
arch=('x86_64')
url='https://charm.sh/crush'
license=('FSL-1.1-MIT')
depends=('glibc' 'ca-certificates')
makedepends=('go>=1.27' 'git')
optdepends=('git: repository integration'
            'ripgrep: faster file and content search'
            'wl-clipboard: clipboard support on Wayland'
            'xclip: clipboard support on X11'
            'xsel: alternative clipboard support on X11'
            'xdg-utils: open browser authentication links')
conflicts=('crush-bin')
options=('!strip' '!debug')
source=("${pkgname}_${pkgver}.tar.gz::https://github.com/charmbracelet/crush/releases/download/v${pkgver}/crush-${pkgver}.tar.gz")
sha256sums=('9611915bad27ab989ddf5757765f4f757043e41a1796b13a2bd7c61fdcf1ed28')

build() {
  cd "$srcdir"
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  export CGO_ENABLED=0 GOFLAGS='-mod=readonly -modcacherw'
  local ldflags="-s -w -X github.com/charmbracelet/crush/internal/version.Version=v${pkgver}"

  # Only the native helper executes during the build. The installed executable
  # is independently cross-compiled, using upstream's pure-Go release mode.
  GOOS="$(go env GOHOSTOS)" GOARCH="$(go env GOHOSTARCH)" \
    go build -trimpath -ldflags="$ldflags" -o crush-host .
  mkdir -p completions manpages
  ./crush-host completion bash > completions/crush.bash
  ./crush-host completion zsh > completions/crush.zsh
  ./crush-host completion fish > completions/crush.fish
  ./crush-host man > manpages/crush.1
  gzip -n -f manpages/crush.1

  GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -buildmode=pie \
    -ldflags="$ldflags" -o crush .
}

package() {
  cd "$srcdir"
  install -Dm755 crush "$pkgdir/usr/bin/crush"
  install -Dm644 LICENSE.md "$pkgdir/usr/share/licenses/$pkgname/LICENSE.md"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
  install -Dm644 completions/crush.bash "$pkgdir/usr/share/bash-completion/completions/crush"
  install -Dm644 completions/crush.zsh "$pkgdir/usr/share/zsh/site-functions/_crush"
  install -Dm644 completions/crush.fish "$pkgdir/usr/share/fish/vendor_completions.d/crush.fish"
  install -Dm644 manpages/crush.1.gz "$pkgdir/usr/share/man/man1/crush.1.gz"
}
