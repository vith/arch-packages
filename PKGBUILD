pkgname=arping-th
pkgver=2.28
pkgrel=1.1
pkgdesc="ARP Ping from Thomas Habets (aka Debian arping)"
arch=('i686' 'x86_64')
url="https://www.habets.pp.se/synscan/programs_arping.html"
license=('GPL')
depends=('libnet' 'libcap' 'libpcap')
source=("https://www.habets.pp.se/synscan/files/arping-$pkgver.tar.gz"
        "https://www.habets.pp.se/synscan/files/arping-$pkgver.tar.gz.asc")
sha256sums=('9cb2d9854b30f9b06f0b0e76a896509c9a4ff0e73b4587131e78645a401b17dc'
            '5845e4e016f618a655b21d06e43206e6a0a578e58074d086b22e5735d9edcf2c')
validpgpkeys=('990786988A24F52F1C2E87F639A49EEA460A0169') # Thomas Habets

# makepkg verifies signatures before prepare(), in the isolated build home.
gpg --batch --import-options import-minimal --import \
  "${startdir}/keys/pgp/${validpgpkeys[0]}.asc"

build() {
  cd "arping-$pkgver"
  ./configure --prefix=/usr --program-suffix="-th" --sbindir=/usr/bin
  make
}

package() {
  cd "arping-$pkgver"
  make DESTDIR="$pkgdir/" install
}

# vim: ts=2:sw=2:et
