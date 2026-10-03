# Maintainer: Your Name <youremail@domain.com>
pkgname=nasc-tui-bin
_pkgname=nascTUI
pkgver=1.0.4
pkgrel=1
pkgdesc="The Not a Soulver Clone TUI"
arch=('x86_64')
url="https://github.com/parnoldx/nascTUI"
license=('GPL-2.0-only')
depends=('libqalculate')
source=("https://github.com/parnoldx/${_pkgname}/releases/download/v${pkgver}/nasc-linux-amd64"
        "${_pkgname}-${pkgver}.tar.gz::https://github.com/parnoldx/${_pkgname}/archive/refs/tags/v${pkgver}.tar.gz")
noextract=("${_pkgname}-${pkgver}.tar.gz")
sha256sums=('65a27edfdfd5eb37987397f24b0c83b98dd3cdc79b840e50664dce5c43b491da'
            '55e20b26f48dbc75d500a65abb6566ca5a35a7eb6d73495d4931527af4c5fa47')

package() {
	install -Dm755 "nasc-linux-amd64" "${pkgdir}/usr/bin/nasc"
	install -Dm644 "${_pkgname}-${pkgver}.tar.gz" "${pkgdir}/usr/share/doc/${pkgname}/${_pkgname}-${pkgver}.tar.gz"
	bsdtar -xOf "${_pkgname}-${pkgver}.tar.gz" "${_pkgname}-${pkgver}/LICENSE" > LICENSE || return
	install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
