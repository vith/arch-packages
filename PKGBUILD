pkgname=asleap
pkgver=2.5
pkgrel=3
pkgdesc='Actively recover LEAP/PPTP passwords'
arch=('x86_64')
url='https://github.com/OscarAkaElvis/asleap'
license=('GPL-2.0-or-later')
depends=('libpcap' 'openssl')
makedepends=('git' 'flex' 'bison')
_commit=aa434293b6108bce5bf6bb29629ad0be6dc881ec
_pcapver=1.11.0
source=("asleap::git+$url.git#commit=$_commit"
        "https://www.tcpdump.org/release/libpcap-$_pcapver.tar.xz")
# Git content is pinned by the full commit above and checked against dispatch.
sha256sums=('SKIP'
            '9237f5bae9dcf3a91823d9963ec43b7c0e2e3374ef2ad57d92c8cd39530f4723')

build() {
    local cc=gcc cppflags="$CPPFLAGS" ldflags="$LDFLAGS"
    if [[ $(uname -m) != "$CARCH" ]]; then
        cc=x86_64-linux-gnu-gcc
        # Debian patches libpcap's SONAME to .so.0.8; Arch uses upstream .so.1.
        # Build the upstream link-time library, but ship no bundled libpcap.
        cd "$srcdir/libpcap-$_pcapver"
        ./configure --host=x86_64-linux-gnu --build="$(./config.guess)" \
            --prefix=/usr --enable-shared --disable-dbus --disable-rdma \
            --without-libnl --disable-bluetooth --disable-usb --disable-netmap \
            --without-dag --without-snf CC="$cc"
        make install-shared DESTDIR="$srcdir/pcap-link"
        cppflags+=" -I$srcdir/libpcap-$_pcapver -I/usr/include/x86_64-linux-gnu"
        ldflags=" -L$srcdir/pcap-link/usr/lib -L/usr/lib/x86_64-linux-gnu $ldflags"
    fi
    cd "$srcdir/asleap"
    make CC="$cc" CPPFLAGS="$cppflags" CFLAGS="$CFLAGS" LDFLAGS="$ldflags"
}

package() {
    make -C asleap DESTDIR="$pkgdir" PREFIX=/usr DOCDIR=/usr/share/doc/asleap install
}
