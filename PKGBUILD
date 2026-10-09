# Maintainer: Jason Papakostas <jason.papakostas@gmail.com>
pkgname=captiveportalautologin-vith-git
pkgver=r694.000b352
pkgrel=4
pkgdesc='Automatically detect and log in to supported captive portals (vith fork)'
arch=('any')
url='https://git.n3t.work/vith/CaptivePortalAutoLogin'
license=('unknown')
depends=('java-runtime>=17' 'networkmanager' 'libnotify')
makedepends=('git' 'java-environment>=17')
provides=('captiveportalautologin' 'captiveportalautologin-git')
conflicts=('captiveportalautologin' 'captiveportalautologin-git')
source=("git+${url}.git#branch=main"
        'captiveportalautologin.service')
sha256sums=('SKIP'
            'f89cc5d141c12f2715e8b3dc4bad155190dd5071a1bb3494f3994695d01c937b')

pkgver() {
  cd CaptivePortalAutoLogin
  printf 'r%s.%s' "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}

build() {
  cd CaptivePortalAutoLogin
  # JVM bytecode is architecture-independent; use the execution host's JDK.
  ./gradlew --no-daemon --max-workers="$(nproc)" :linux:shadowJar
}

package() {
  cd CaptivePortalAutoLogin

  install -Dm644 linux/build/libs/linux-shadow.jar \
    "$pkgdir/usr/share/java/captiveportalautologin/captiveportalautologin.jar"
  install -Dm755 /dev/stdin "$pkgdir/usr/bin/captiveportalautologin" <<'EOF'
#!/bin/sh
exec /usr/bin/java -jar /usr/share/java/captiveportalautologin/captiveportalautologin.jar "$@"
EOF
  install -Dm644 "$srcdir/captiveportalautologin.service" \
    "$pkgdir/usr/lib/systemd/user/captiveportalautologin.service"
}
