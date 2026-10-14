# Maintainer ArchEnemy
pkgname=i915ovmf
pkgver=1.0.2
pkgrel=1.3
pkgdesc="i915ovmfPkg VBIOS for Intel GPU Passthrough GVT-g/GVT-d"
arch=('x86_64')
url="https://github.com/x78x79x82x79/i915ovmfPkg"
license=('unknown')
depends=(
  'pacman>6.1'
  'git'
  'util-linux'
  'bash'
)
makedepends=(
  'nasm'
)
provides=("i915ovmfpkg")
conflicts=("i915ovmfpkg")
optdepends=(
    'qemu-system-x86'
)
source=(
    "i915ovmf.tar.gz::https://github.com/x78x79x82x79/${pkgname}Pkg/archive/refs/tags/v${pkgver}.tar.gz"
    "edk2::git+https://github.com/tianocore/edk2#commit=b7a715f7c03c45c6b4575bf88596bfd79658b8ce"
    "edk2-platforms.tar.gz::https://github.com/x78x79x82x79/edk2-platforms/archive/refs/tags/v1.0.0.tar.gz"
)
sha256sums=(
    "5580834291cb07a5da9cab8de964c94ecf4754e1a4cb159eff2a371b737ba3ab"
    "SKIP"
    "9e7ab8c60970a9129c230d87d9a8884c22505b7d9c27ba9212185257d5069012"
)

prepare(){
    # Keep the legacy firmware's BOOLEAN typedef compatible with modern GCC.
    printf '%s\r\n' \
        '--- a/i915ovmf.dsc' \
        '+++ b/i915ovmf.dsc' \
        '@@ -54,7 +54,7 @@' \
        '   INTEL:RELEASE_*_*_CC_FLAGS           = /D MDEPKG_NDEBUG' \
        '   MSFT:RELEASE_*_*_CC_FLAGS            = /D MDEPKG_NDEBUG' \
        ' !if $(TOOL_CHAIN_TAG) != "XCODE5"' \
        '-  GCC:*_*_*_CC_FLAGS                   = -mno-mmx -mno-sse -fno-stack-protector' \
        '+  GCC:*_*_*_CC_FLAGS                   = -mno-mmx -mno-sse -fno-stack-protector -std=gnu17' \
        ' #  GCC:*_*_*_LD_FLAGS                   = -lssp -lstack_guard.obj' \
        ' !endif' \
        ' !ifdef $(SOURCE_DEBUG_ENABLE)' \
        | patch --binary --batch --fuzz=0 -Np1 -d "${pkgname}Pkg-${pkgver}"
    cd edk2
    # Preserve upstream CRLF and the DEBUG copy, but not its duplicate option-ROM graph output.
    printf '%s\r\n' \
        '--- a/BaseTools/Conf/build_rule.template' \
        '+++ b/BaseTools/Conf/build_rule.template' \
        '@@ -396,7 +396,6 @@' \
        '     <OutputFile>' \
        '         $(OUTPUT_DIR)(+)$(MODULE_NAME).efi' \
        '         $(OUTPUT_DIR)(+)$(MODULE_NAME).map' \
        '-        $(DEBUG_DIR)(+)$(MODULE_NAME).efi' \
        ' ' \
        '     <Command.MSFT, Command.INTEL, Command.CLANGPDB>' \
        '         "$(GENFW)" -e $(MODULE_TYPE) -o ${dst} ${src} $(GENFW_FLAGS)' \
        | patch --binary --batch --fuzz=0 -Np1
    git init
    echo "checking out edk2 submodules..."
    git submodule update --init --depth 1 --recursive
}

build(){
    BUILD_DIR=$(realpath .)
    export EDK2_PATH="$BUILD_DIR/edk2"
    export EDK2_PLATFORMS_PATH="$BUILD_DIR/edk2-platforms-1.0.0"
    export REPO_PATH="$BUILD_DIR/${pkgname}Pkg-${pkgver}"
    cd "$REPO_PATH"
    source ./config
    ./build.sh
}

package(){
    mkdir -p $pkgdir/var/lib/libvirt/qemu/drivers
    install -m755 $REPO_PATH/@BUILD/Build/i915ovmf/RELEASE_GCC5/X64/i915ovmf.rom $pkgdir/var/lib/libvirt/qemu/drivers/
}
