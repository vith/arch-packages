# Maintainer: Omar Pakker <archlinux@opakker.nl>

pkgbase=looking-glass
pkgname=("${pkgbase}"
         "${pkgbase}-module-dkms"
         "obs-plugin-${pkgbase}")
epoch=2
pkgver=B7
pkgrel=9
pkgdesc="An extremely low latency KVMFR (KVM FrameRelay) implementation for guests with VGA PCI Passthrough"
url="https://looking-glass.io/"
arch=('x86_64')
license=('GPL-2.0-or-later')
makedepends=('cmake' 'make' 'pkgconf' 'wayland' 'wayland-protocols' 'spice-protocol'
             'fontconfig' 'libegl' 'libgl' 'libpipewire' 'libpulse' 'libsamplerate'
             'libx11' 'libxcursor' 'libxfixes' 'libxi' 'libxinerama' 'libxkbcommon'
             'libxpresent' 'libxss' 'nettle' 'zlib' 'zstd' 'libdecor' 'obs-studio')
source=("looking-glass-${pkgver}.tar.gz::https://looking-glass.io/artifact/${pkgver}/source"
        "1197-backport.patch"
        "nettle4-compat.patch")
sha256sums=('09e506660ccc1b9691d06caa70179b52ffb4393299895cff3c2f0e74fcd69985'
            '1caacd0984277fa5bcfc7a727ae9ece62ca06b9c16d1d8d9917f9cc80181049d'
            'e7aedcd68532a5c4fddaf5e6c5386f6f37858e1ab81a83d906a93abd1839077e')

_lgdir="${pkgbase}-${pkgver}"

prepare() {
	cd "${srcdir}/${_lgdir}"
	for patch in "${srcdir}"/*.patch; do
		patch -p1 < "${patch}"
	done
}

build() {
	local components=() package
	for package in "${pkgname[@]}"; do
		case "${package}" in
			looking-glass) components+=(client) ;;
			obs-plugin-looking-glass) components+=(obs) ;;
			looking-glass-module-dkms) ;;
		esac
	done
	local component
	for component in "${components[@]}"; do
		cmake -S "${srcdir}/${_lgdir}/${component}" \
			-B "${srcdir}/${_lgdir}/${component}/build" \
			-DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib \
			-DOPTIMIZE_FOR_NATIVE=OFF \
			-DWAYLAND_SCANNER_EXECUTABLE="$(command -v wayland-scanner)"
		cmake --build "${srcdir}/${_lgdir}/${component}/build" --parallel "$(nproc)"
	done
}

_install_notices() {
	local notice
	cd "${srcdir}/${_lgdir}"
	for notice in LICENSE repos/cimgui/LICENSE repos/cimgui/imgui/LICENSE.txt \
		repos/nanosvg/LICENSE.txt repos/wayland-protocols/COPYING \
		repos/PureSpice/LICENSE repos/LGMP/LICENSE; do
		install -Dm644 "${notice}" "${pkgdir}/usr/share/licenses/${pkgname}/${notice}"
	done
}

package_looking-glass() {
	pkgdesc="A client application for accessing the LookingGlass IVSHMEM device of a VM"
	depends=('binutils' 'fontconfig' 'gcc-libs' 'glibc' 'gmp' 'hicolor-icon-theme'
	         'libegl' 'libgl' 'libpipewire' 'libpulse' 'libsamplerate' 'libx11'
	         'libxcursor' 'libxfixes' 'libxi' 'libxinerama' 'libxkbcommon' 'libxpresent'
	         'libxss' 'nettle' 'wayland' 'zlib' 'zstd' 'libdecor')

	cd "${srcdir}/${_lgdir}/client/build"
	make DESTDIR="${pkgdir}" install
	_install_notices
}

package_looking-glass-module-dkms() {
	pkgdesc="A kernel module that implements a basic interface to the IVSHMEM device for when using LookingGlass in VM->VM mode"
	depends=('dkms')

	cd "${srcdir}/${_lgdir}/module"
	install -Dm644 -t "${pkgdir}/usr/src/${pkgbase}-${pkgver}" \
		Makefile \
		dkms.conf \
		kvmfr.{h,c}
	install -Dm644 "${srcdir}/${_lgdir}/LICENSE" \
		"${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}

package_obs-plugin-looking-glass() {
	pkgdesc="Plugin for OBS Studio to stream directly from Looking Glass without having to record the Looking Glass client"
	depends=('glibc' 'obs-studio')

	install -Dm644 -t "${pkgdir}/usr/lib/obs-plugins" \
		"${srcdir}/${_lgdir}/obs/build/liblooking-glass-obs.so"
	_install_notices
}
