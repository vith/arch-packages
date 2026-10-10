# Maintainer: coxackie
pkgname=spotify-remove-ad-banner
pkgver=6
pkgrel=3
pkgdesc='Remove Spotify ad banner'
arch=('any')
license=('unknown')
depends=('spotify' 'sed' 'unzip' 'zip')
install="${pkgname}.install"
source=("${pkgname}.hook"
        'remove.sh'
        'restore.sh')
sha256sums=('45e9906fe17a97db3689af89b82d0d9957ebfb8f9799511b8df87e0370490d5f'
            '16672d25db548e580e0ebf99914e635e958f6863590ac411d0c8103236e9d260'
            '59f10ab34e442ff14138ef056d207bf0ee61d6dbd04551a41b8276577a0fc71c')


package() {
  install -Dm 644 "${srcdir}/${pkgname}.hook" "${pkgdir}/usr/share/libalpm/hooks/${pkgname}.hook"
  install -Dm 755 "${srcdir}/remove.sh" "${pkgdir}/usr/share/${pkgname}/remove.sh"
  install -Dm 755 "${srcdir}/restore.sh" "${pkgdir}/usr/share/${pkgname}/restore.sh"
}

