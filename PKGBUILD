# Maintainer: Eric Torres <eric.torres@its-et.me>
pkgname=podcheck
pkgver=1.2.0
pkgrel=2
pkgdesc="CLI tool to automate podman image updates. Selective, notifications, autoprune, no pre-pulling."
arch=('any')
url="https://github.com/sudo-kraken/podcheck"
license=('GPL-3.0-only')
depends=(bash podman podman-compose jq regclient-regctl)
source=("${pkgname}-${pkgver}.tar.gz::https://github.com/sudo-kraken/podcheck/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=('2010eaa5918fbe4f771d28603a63d9330ea353235852e187c9238552afd4f7f1')

package() {
    cd "$pkgname-$pkgver"
    # readlink -f locates these runtime files relative to the script itself.
    install -Dm755 podcheck.sh "$pkgdir/usr/lib/$pkgname/podcheck.sh"
    install -Dm644 podcheck.config default.config -t "$pkgdir/usr/lib/$pkgname/"
    cp -r notify_templates addons "$pkgdir/usr/lib/$pkgname/"
    install -dm755 "$pkgdir/usr/bin"
    ln -s "../lib/$pkgname/podcheck.sh" "$pkgdir/usr/bin/$pkgname"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
