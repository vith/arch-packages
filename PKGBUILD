# Maintainer: Your Name <youremail@domain.com>
pkgname=nasc-tui-bin
_pkgname=nascTUI
pkgver=1.0.7
pkgrel=1
pkgdesc="The Not a Soulver Clone TUI"
arch=('x86_64')
url="https://github.com/parnoldx/nascTUI"
license=('GPL-2.0-only')
depends=('libqalculate')
source=("https://github.com/parnoldx/${_pkgname}/releases/download/v${pkgver}/nasc-linux-amd64"
        "${_pkgname}-${pkgver}.tar.gz::https://github.com/parnoldx/${_pkgname}/archive/refs/tags/v${pkgver}.tar.gz")
noextract=("${_pkgname}-${pkgver}.tar.gz")
sha256sums=('28ea3de1bc4d99e77d0c0908d6c9872a2dc505d72a9b3a0522973ed62843f13a'
            'db12a62028c986c161b86844920452ab997f208ff45a8b3757f58a7f27ac1219')

package() {
	install -Dm755 "nasc-linux-amd64" "${pkgdir}/usr/bin/nasc"
	install -Dm644 "${_pkgname}-${pkgver}.tar.gz" "${pkgdir}/usr/share/doc/${pkgname}/${_pkgname}-${pkgver}.tar.gz"
	bsdtar -xOf "${_pkgname}-${pkgver}.tar.gz" "${_pkgname}-${pkgver}/LICENSE" > LICENSE || return
	install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
