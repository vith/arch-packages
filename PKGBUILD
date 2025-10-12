# Maintainer: Your Name <youremail@domain.com>
pkgname=nasc-tui-bin
_pkgname=nascTUI
pkgver=1.0.4
pkgrel=1
pkgdesc="The Not a Soulver Clone TUI"
arch=('x86_64')
url="https://github.com/parnoldx/nascTUI"
license=('MIT')
depends=('libqalculate')
source=("https://github.com/parnoldx/${_pkgname}/releases/download/v${pkgver}/nasc-linux-amd64")
sha256sums=('65a27edfdfd5eb37987397f24b0c83b98dd3cdc79b840e50664dce5c43b491da')

package() {
	install -Dm755 "nasc-linux-amd64" "${pkgdir}/usr/bin/nasc"
}
