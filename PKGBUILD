# Maintainer: Rafael Dominiquini <rafaeldominiquini at gmail dot com>

# Override download agent to bypass strict user-agent blocking
DLAGENTS=('http::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 --user-agent "PKGBUILD" -o %o %u'
          'https::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 --user-agent "PKGBUILD" -o %o %u')

_pkgauthor=superradcompany
_pkgname=microsandbox
_pkgalias=msb
_cratename=${_pkgname}
pkgname=${_cratename}
pkgdesc="Easy, fast and local-first microVM runtime"

pkgver=0.7.4
pkgrel=3
_pkgvername=${pkgver}

arch=('x86_64')

url="https://github.com/${_pkgauthor}/${_pkgname}"

license=('Apache-2.0')

provides=("${_pkgname}" "${_pkgalias}")

makedepends=('cargo' 'cmake' 'git')
depends=('glibc' 'libgcc' 'libcap-ng')

options=('!strip' '!lto')

source=("microsandbox-${pkgver}.crate::https://crates.io/api/v1/crates/microsandbox/${pkgver}/download"
        "microsandbox-runtime.tar.gz::https://github.com/superradcompany/microsandbox/releases/download/v${pkgver}/microsandbox-linux-x86_64.tar.gz"
        "microsandbox-runtime-source::git+https://github.com/superradcompany/microsandbox.git#tag=v${pkgver}"
        'libkrunfw-source-cf4c22b9.tar.gz::https://github.com/superradcompany/libkrunfw/archive/cf4c22b9f05c680928e6d96a9d198f5845573a87.tar.gz'
        'linux-6.12.109.tar.gz::https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.109.tar.gz'
        'libkrun-source-2bd0f84a.tar.gz::https://github.com/zerocore-ai/libkrun/archive/2bd0f84ad0956f3032e0490d3b8512b6851eca12.tar.gz')
noextract=('microsandbox-runtime.tar.gz' 'libkrunfw-source-cf4c22b9.tar.gz'
           'linux-6.12.109.tar.gz' 'libkrun-source-2bd0f84a.tar.gz')
sha256sums=('d2d4d6223c59456b82cb98cb7a020bf2dc23dc8d3de6d5ed74c04ed0e9bed856'
            'a3689a2fb4cc9dd88e36f7c11883bb2bdf98b1c1ffd2f41a414224e4cd89bf59'
            'SKIP'
            'd4b49702674f08626fd1b9c55eb1e1c3010e7e0fc43d1e14778a5e1144a64eec'
            'e81af28f5941db1c7f356151dfbab2ca5786c8be76474f7d42ea306cdeb445cd'
            '88e95efa9f3883fbc6793641694f75ad64b9e0006509bedafc0fd62b0c822765')
_firmware_commit=cf4c22b9f05c680928e6d96a9d198f5845573a87


_target=x86_64-unknown-linux-gnu

prepare() {
	# A release using different firmware requires a reviewed corresponding-source pin update.
	local firmware_commit
	firmware_commit=$(git -C "$srcdir/microsandbox-runtime-source" rev-parse HEAD:vendor/libkrunfw) || return 1
	[[ $firmware_commit == "$_firmware_commit" ]] || {
		error "Runtime firmware changed; update the corresponding firmware/kernel sources"
		return 1
	}
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	# Upstream build.rs reads this exact profile cache before attempting a download.
	# Keep its default features and matching official msb/libkrunfw pair unchanged.
	install -Dm644 "$srcdir/microsandbox-runtime.tar.gz" \
		"${CARGO_TARGET_DIR:-target}/$_target/release/.microsandbox-runtime-cache/microsandbox-runtime-${pkgver}-linux-x86_64.tar.gz"
	export MSB_HOME="$srcdir/microsandbox-build-home"
	cargo fetch --locked --target "$_target"
}

build() {
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	export MSB_HOME="$srcdir/microsandbox-build-home"
	RUSTFLAGS="--remap-path-prefix=$(pwd)=/build/" cargo build --release --locked --target "$_target"
}

package() {
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	install -Dm755 "${CARGO_TARGET_DIR:-target}/$_target/release/${_cratename}" "${pkgdir}/usr/bin/${_pkgname}"
	ln -sf "/usr/bin/${_pkgname}" "${pkgdir}/usr/bin/${_pkgalias}"

	install -Dm644 "README.md" "${pkgdir}/usr/share/doc/${pkgname}/README.md"
}
