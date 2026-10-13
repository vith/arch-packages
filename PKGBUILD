pkgname=asleap
pkgver=2.5
pkgrel=4
pkgdesc='Actively recover LEAP/PPTP passwords'
arch=('x86_64')
url='https://github.com/OscarAkaElvis/asleap'
license=('GPL-2.0-or-later')
depends=('libpcap' 'openssl')
makedepends=('git')
source=("asleap::git+$url.git#tag=v$pkgver")
sha256sums=('2cf771d1d4fdd79ea25ceb7c50a7aebcf2fedf8ce15f3843d0661bf29c2b043d')

build() {
    cd "$srcdir/asleap"
    make CC=gcc CPPFLAGS="$CPPFLAGS" CFLAGS="$CFLAGS" LDFLAGS="$LDFLAGS"
}

package() {
    make -C asleap DESTDIR="$pkgdir" PREFIX=/usr DOCDIR=/usr/share/doc/asleap install
}
