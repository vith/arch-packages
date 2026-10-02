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
pkgrel=2
_pkgvername=${pkgver}

arch=('x86_64' 'aarch64')
_barch=('x86_64' 'aarch64')

url="https://github.com/${_pkgauthor}/${_pkgname}"

license=('Apache-2.0')

provides=("${_pkgname}" "${_pkgalias}")

makedepends=('cargo' 'cmake')
depends=('glibc' 'libgcc' 'libcap-ng')

options=('!strip' '!lto')

source=("${_pkgname}-${_pkgvername}.crate::https://crates.io/api/v1/crates/${_cratename}/${_pkgvername}/download"
        "https://people.redhat.com/sgrubb/libcap-ng/libcap-ng-0.8.5.tar.gz")
sha256sums=('d2d4d6223c59456b82cb98cb7a020bf2dc23dc8d3de6d5ed74c04ed0e9bed856'
            '3ba5294d1cbdfa98afaacfbc00b6af9ed2b83e8a21817185dfd844cc8c7ac6ff')


# CARCH selects the package target independently of the native build host.
_target="${CARCH}-unknown-linux-gnu"

_build_env() {
	if [[ $(uname -m) != "$CARCH" ]]; then
		[[ $CARCH == x86_64 ]] || { error "Unsupported cross target: $CARCH"; return 1; }
		export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc
		export CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc
		export CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++
		export AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar
	fi
}

prepare() {
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	_build_env
	cargo fetch --locked --target "$_target"
}

build() {
	local target_flags=()
	# Supply the target link library, not the ARM host's libcap-ng.
	# The installed package uses Arch's declared libcap-ng dependency.
	if [[ $(uname -m) != "$CARCH" ]]; then
		# This release includes configure; no host autotools bootstrap is needed.
		cd "$srcdir/libcap-ng-0.8.5"
		./configure --host=x86_64-linux-gnu --prefix=/usr \
			--disable-static --without-python --without-python3
		make -C src
		target_flags+=("-Lnative=$srcdir/libcap-ng-0.8.5/src/.libs")
	fi
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	_build_env
	RUSTFLAGS="--remap-path-prefix=$(pwd)=/build/ ${target_flags[*]}" cargo build --release --locked --target "$_target"
}

package() {
	cd ${srcdir}/${_cratename}-${_pkgvername} || exit 1

	install -Dm755 "${CARGO_TARGET_DIR:-target}/$_target/release/${_cratename}" "${pkgdir}/usr/bin/${_pkgname}"
	ln -sf "/usr/bin/${_pkgname}" "${pkgdir}/usr/bin/${_pkgalias}"

	install -Dm644 "README.md" "${pkgdir}/usr/share/doc/${pkgname}/README.md"
}
