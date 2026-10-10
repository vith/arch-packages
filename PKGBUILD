# Maintainer: AlphaLynx <alphalynx at alphalynx dot dev>

pkgname=vet
pkgver=1.0.2
pkgrel=2
pkgdesc='A command-line tool that acts as a safety net for the risky curl pipe to bash pattern'
arch=('any')
url="https://getvet.sh/"
license=('MIT')
depends=('bash' 'coreutils' 'curl' 'diffutils' 'less')
checkdepends=('bats' 'bats-assert' 'bats-support')
optdepends=(
    'bat: syntax-highlighting pager for script review'
    'shellcheck: for linting downloaded scripts'
)
source=("$pkgname-$pkgver.tar.gz::https://github.com/vet-run/vet/archive/refs/tags/v$pkgver.tar.gz")
sha256sums=('7f29bf36ebad8ca9cbc6598c63b508f2945fe42621ed4f4ee18adb0a56720b74')

check() {
    cd $pkgname-$pkgver

    # Use system bats helpers (normally git submodules, not present in release tarball)
    rm -rf tests/helpers
    mkdir -p tests/helpers
    ln -sf /usr/lib/bats/bats-assert tests/helpers/bats-assert
    ln -sf /usr/lib/bats/bats-support tests/helpers/bats-support

    bats tests/vet.bats
}

package() {
    cd $pkgname-$pkgver
    install -Dm755 vet -t "$pkgdir/usr/bin"
    install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/vet"
}
