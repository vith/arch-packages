# Maintainer: molivier <martin dot olivier at live dot fr>

pkgname=airgorah
pkgver=0.8.1
pkgrel=2
pkgdesc="A WiFi security auditing software mainly based on aircrack-ng tools suite"
arch=("x86_64")
url="https://github.com/martin-olivier/airgorah"
license=("MIT")

source=("${pkgname}-${pkgver}::${url}/archive/refs/tags/v$pkgver.tar.gz"
        'arch-sysroot.sh')
sha256sums=('e86aaf8c60615050beffedec8880842d7f9e3b86855f9b901210246d4dd8d079'
            '843540eade89f9020cda6456edbcb173c10145c6929b021e8bff74a4d9ea1170')

source "$(dirname "${BASH_SOURCE[0]}")/arch-sysroot.sh"
noextract=()
for _entry in "${_arch_packages[@]}"; do
    read -r _name _archive _sha256 <<< "${_entry}"
    source+=("https://archive.archlinux.org/packages/${_name:0:1}/${_name}/${_archive}")
    sha256sums+=("${_sha256}")
    noextract+=("${_archive}")
done
unset _entry _name _archive _sha256

provides=("${pkgname}=${pkgver}")
conflicts=("${pkgname}")

depends=(
    'bash'
    'dbus'
    'xterm'
    'polkit'
    'gtk4'
    'aircrack-ng'
    'iproute2'
    'iw'
    'macchanger'
    'wireshark-cli'
    'adwaita-icon-theme'
)
optdepends=(
    'mdk4: alternative deauthentication method'
    'crunch: wordlist generation for handshake bruteforce'
)
makedepends=(
    'base-devel'
    'rust'
    'pkgconf'
    'libarchive'
)

prepare() {
    local _entry _name _archive _sha256 _members _prefix
    local -a _includes
    mkdir -p "${srcdir}/arch-sysroot"
    for _entry in "${_arch_packages[@]}"; do
        read -r _name _archive _sha256 <<< "${_entry}"
        _members=$(bsdtar -tf "${srcdir}/${_archive}") || return
        _includes=()
        for _prefix in 'usr/lib/' 'usr/bin/' 'usr/include/' 'usr/share/pkgconfig/'; do
            if [[ $'\n'"${_members}" == *$'\n'"${_prefix}"* ]]; then
                _includes+=(--include "${_prefix}*")
            fi
        done
        if [[ ${#_includes[@]} -eq 0 ]]; then
            continue
        fi
        # Keep cross-prefix hard-link targets; target tools never enter PATH.
        bsdtar -xf "${srcdir}/${_archive}" -C "${srcdir}/arch-sysroot" \
            "${_includes[@]}" || return
    done
}

build() {
    cd "${srcdir}/${pkgname}-${pkgver}"

    local _sysroot="${srcdir}/arch-sysroot" _tool_prefix=
    if [[ "$(uname -m)" != "${CARCH}" ]]; then
        _tool_prefix=x86_64-linux-gnu-
    fi
    export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${srcdir}/target}"
    export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="${_tool_prefix}gcc"
    export CC_x86_64_unknown_linux_gnu="${_tool_prefix}gcc"
    export CXX_x86_64_unknown_linux_gnu="${_tool_prefix}g++"
    export AR_x86_64_unknown_linux_gnu="${_tool_prefix}ar"
    export CFLAGS_x86_64_unknown_linux_gnu="${CFLAGS:-} --sysroot=${_sysroot}"
    export CXXFLAGS_x86_64_unknown_linux_gnu="${CXXFLAGS:-} --sysroot=${_sysroot}"
    export PKG_CONFIG_ALLOW_CROSS_x86_64_unknown_linux_gnu=1
    export PKG_CONFIG_SYSROOT_DIR_x86_64_unknown_linux_gnu="${_sysroot}"
    export PKG_CONFIG_LIBDIR_x86_64_unknown_linux_gnu="${_sysroot}/usr/lib/pkgconfig:${_sysroot}/usr/share/pkgconfig"
    export PKG_CONFIG_PATH_x86_64_unknown_linux_gnu=
    export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS="${RUSTFLAGS:-} -C link-arg=--sysroot=${_sysroot} -C link-arg=-B${_sysroot}/usr/lib/ -C link-arg=-L${_sysroot}/usr/lib -C link-arg=-Wl,-rpath-link,${_sysroot}/usr/lib"
    # Retain upstream's complete GUI and agent, and its locked dependency graph.
    cargo build --locked --release --workspace --target x86_64-unknown-linux-gnu
}

package() {
    cd "${srcdir}/${pkgname}-${pkgver}"

    local _target_dir="${CARGO_TARGET_DIR:-${srcdir}/target}"
    install -Dm755 "${_target_dir}/x86_64-unknown-linux-gnu/release/${pkgname}" -t "${pkgdir}/usr/bin"
    install -Dm755 "${_target_dir}/x86_64-unknown-linux-gnu/release/${pkgname}-agent" -t "${pkgdir}/usr/bin"
    install -Dm644 "crates/gui/icons/app_icon.png" "${pkgdir}/usr/share/pixmaps/${pkgname}.png"

    install -Dm644 "package/.desktop" "${pkgdir}/usr/share/applications/com.molivier.${pkgname}.desktop"
    install -Dm644 "package/.policy" "${pkgdir}/usr/share/polkit-1/actions/org.freedesktop.policykit.${pkgname}.policy"

    install -Dm644 "README.md" -t "${pkgdir}/usr/share/doc/${pkgname}"
    install -Dm644 "LICENSE" -t "${pkgdir}/usr/share/doc/${pkgname}"
}
