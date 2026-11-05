# Maintainer: Zen Wen <zen.8841@gmail.com>
# Contributor: Moritz Bunkus <moritz@bunkus.org>

pkgname="osslsigncode"
pkgver="2.14"
pkgrel=2.1
pkgdesc="OpenSSL based Authenticode signing for PE/MSI/Java CAB files"
arch=('i686' 'x86_64')
url="https://github.com/mtrojnar/osslsigncode"
license=('GPL-3.0-or-later')
depends=('curl' 'openssl' 'zlib')
makedepends=('cmake' 'perl' 'python')
checkdepends=('python-cryptography>=37.0.2')
source=("https://github.com/mtrojnar/${pkgname}/archive/${pkgver}.tar.gz")
sha512sums=('8a7b8118a6f62dee392079964b1aa8d737a881bf8c3e72d5a081980b3084ee683b9e05654db73bf0ce1aec440502f31ed3b2870bb72dc960b1885cceed96462e')

build() {
  cd "$srcdir"
  cmake \
    -B build -S "$srcdir/osslsigncode-${pkgver}" \
    -DCMAKE_BUILD_TYPE=None \
    -DCMAKE_INSTALL_PREFIX=/usr
  cmake --build build
}

check() {
  cd "$srcdir/build"
  ctest -C Release --output-on-failure --no-tests=error
}

package() {
  cd "$srcdir"

  DESTDIR="$pkgdir" cmake --install build
}
