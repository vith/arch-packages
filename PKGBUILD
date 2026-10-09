# Maintainer: Vitalii Stepchyk <lowercase-name (v) at lowercase-surname (s) dot dev>
pkgname=ttf-ioskeley-mono-unhinted
epoch=1
pkgver=2.0.0
pkgrel=3
pkgdesc="Iosevka configuration to mimic Berkeley Mono - unhinted TTF version"
arch=(any)
url="https://github.com/ahatem/IoskeleyMono"
license=('OFL-1.1')
source=("IoskeleyMono-${pkgver}.zip::https://github.com/ahatem/IoskeleyMono/releases/download/v${pkgver}/IoskeleyMono.zip"
        "IoskeleyMono-${pkgver}-LICENSE::https://raw.githubusercontent.com/ahatem/IoskeleyMono/refs/tags/v${pkgver}/LICENSE")
sha256sums=('dc37763fbb82cbb99611955ee3196c774c164c716c0f659b4bddbe3d370c3204'
            '1084285bd2bddf706d566e11a92fcbae2706da4a8eafac49ed34c871d01fb7fe')

package() {
  install -Dm644 "$srcdir/Normal/Unhinted"/*.ttf -t "$pkgdir/usr/share/fonts/ttf/$pkgname"
  install -Dm644 "$srcdir/IoskeleyMono-${pkgver}-LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
