# Maintainer ArchEnemy
pkgname=i915ovmf
pkgver=1.0.2
pkgrel=3
pkgdesc="i915ovmfPkg VBIOS for Intel GPU Passthrough GVT-g/GVT-d"
arch=('x86_64')
url="https://github.com/x78x79x82x79/i915ovmfPkg"
license=('unknown')
makedepends=(
  'nasm'
  'python'
)
provides=("i915ovmfpkg")
conflicts=("i915ovmfpkg")
optdepends=(
    'qemu-system-x86'
)
source=(
    "i915ovmf.tar.gz::https://github.com/x78x79x82x79/${pkgname}Pkg/archive/refs/tags/v${pkgver}.tar.gz"
    "edk2.tar.gz::https://github.com/x78x79x82x79/edk2/archive/0d61f52fe31c86936c5b4268effddad7241c811e.tar.gz"
    "edk2-platforms.tar.gz::https://github.com/x78x79x82x79/edk2-platforms/archive/refs/tags/v1.0.0.tar.gz"
    "basetools-unused-counter.patch"
    "brotli-f4153a09f87cbb9c826d8fc12c74642bb2d879ea.tar.gz::https://github.com/google/brotli/archive/f4153a09f87cbb9c826d8fc12c74642bb2d879ea.tar.gz"
    "mbedtls-8c89224991adff88d53cd380f42a2baa36f91454.tar.gz::https://github.com/ARMmbed/mbedtls/archive/8c89224991adff88d53cd380f42a2baa36f91454.tar.gz"
    "openssl-98acb6b02839c609ef5b837794e08d906d965335.tar.gz::https://github.com/openssl/openssl/archive/98acb6b02839c609ef5b837794e08d906d965335.tar.gz"
    "oniguruma-abfc8ff81df4067f309032467785e06975678f0d.tar.gz::https://github.com/kkos/oniguruma/archive/abfc8ff81df4067f309032467785e06975678f0d.tar.gz"
    "pylibfdt-cfff805481bdea27f900c32698171286542b8d3c.tar.gz::https://github.com/devicetree-org/pylibfdt/archive/cfff805481bdea27f900c32698171286542b8d3c.tar.gz"
    "public-mipi-sys-t-370b5944c046bab043dd8b133727b2135af7747a.tar.gz::https://github.com/MIPI-Alliance/public-mipi-sys-t/archive/370b5944c046bab043dd8b133727b2135af7747a.tar.gz"
    "pugixml-c53fdab93af76106b963216d85897614b996f8b6.tar.gz::https://github.com/zeux/pugixml/archive/c53fdab93af76106b963216d85897614b996f8b6.tar.gz"
    "googletest-a6f06bf2fd3b832822cd4e9e554b7d47f32ec084.tar.gz::https://github.com/google/googletest/archive/a6f06bf2fd3b832822cd4e9e554b7d47f32ec084.tar.gz"
    "jansson-e9ebfa7e77a6bee77df44e096b100e7131044059.tar.gz::https://github.com/akheron/jansson/archive/e9ebfa7e77a6bee77df44e096b100e7131044059.tar.gz"
    "libspdm-98ef964e1e9a0c39c7efb67143d3a13a819432e0.tar.gz::https://github.com/DMTF/libspdm/archive/98ef964e1e9a0c39c7efb67143d3a13a819432e0.tar.gz"
    "openssl-de90e54bbe82e5be4fb9608b6f5c308bb837d355.tar.gz::https://github.com/openssl/openssl/archive/de90e54bbe82e5be4fb9608b6f5c308bb837d355.tar.gz"
    "mbedtls-107ea89daaefb9867ea9121002fbbdf926780e98.tar.gz::https://github.com/ARMmbed/mbedtls/archive/107ea89daaefb9867ea9121002fbbdf926780e98.tar.gz"
    "mbedtls-framework-94599c0e3b5036e086446a51a3f79640f70f22f6.tar.gz::https://github.com/Mbed-TLS/mbedtls-framework/archive/94599c0e3b5036e086446a51a3f79640f70f22f6.tar.gz"
    "cmocka-a01cc69ee9536f90e57c61a198f2d1944d3d4313.tar.gz::https://gitlab.com/cmocka/cmocka/-/archive/a01cc69ee9536f90e57c61a198f2d1944d3d4313/cmocka-a01cc69ee9536f90e57c61a198f2d1944d3d4313.tar.gz"
    "edk2-cmocka-1cc9cde3448cdd2e000886a26acf1caac2db7cf1.tar.gz::https://github.com/tianocore/edk2-cmocka/archive/1cc9cde3448cdd2e000886a26acf1caac2db7cf1.tar.gz"
    "googletest-86add13493e5c881d7e4ba77fb91c1f57752b3a4.tar.gz::https://github.com/google/googletest/archive/86add13493e5c881d7e4ba77fb91c1f57752b3a4.tar.gz"
    "edk2-subhook-83d4e1ebef3588fae48b69a7352cc21801cb70bc.tar.gz::https://github.com/tianocore/edk2-subhook/archive/83d4e1ebef3588fae48b69a7352cc21801cb70bc.tar.gz"
)
sha256sums=(
    "5580834291cb07a5da9cab8de964c94ecf4754e1a4cb159eff2a371b737ba3ab"
    "fdbda189d20acb1a28714e79d37e992c33ffd44be70f8b379849eee8a667ad50"
    "9e7ab8c60970a9129c230d87d9a8884c22505b7d9c27ba9212185257d5069012"
    "dc505c76fb01f4891b9736013406fb39667a629f435097e2075b7fa1f2311ef8"
    "6d6cacce05086b7debe75127415ff9c3661849f564fe2f5f3b0383d48aa4ed77"
    "b5c7e7c54e013c168f4aae036e59912785f11b4aeebd57f6165a14e879b9a82c"
    "32f22a54c0119c9c7b7e812342d12d22b39bde61c46038bf401e7a356e4c7454"
    "eea977380ebb1871d5de38c4f7f15442ee690c90bdf790590d930a6bbf347f28"
    "1193910f475fde07f3cd4fe1c1a353d69b8cedb574967134838fcdc8208d224e"
    "9fda3b9a78343ab2be6f06ce6396536e7e065abac29b47c8eb2e42cbb4c4f00b"
    "981ab3e9634cb7c041b484cc1876f22a743dc0ae53a970117ca1b5700670a964"
    "4049ab4cdfae20c376c33b139c34a805a0074dc0d6fe8f47491cd2ab3c3eba98"
    "e7935c0d91d6d22f6dee710a26b23e228ecc4fe8ef7e8f756558c3599f68c3b4"
    "634cbdf10bcaf32f3446b1bf8be7e8a60cecd1e9500e512f01e3c15b74cbcfd3"
    "dbfc74f14091d66b95edab229cff9ef8f1f0ab40da30efec36ca3546a3482b76"
    "7dc3219b00ab2d33d5108a6cfb1b6d11c1476f882493acdf0be5bcc9a181336b"
    "632d5aa4dd2e4179f9d648be12af9a9c3158d7f503a93e70e651751b5f31257c"
    "97a0e47ae225fe45090925125eb711943bf66584ac5fd9c831d14014443124cb"
    "59cd4b81abafae35d94ac5d91cf4ae5b05122e688713cd6db51e5e4cef471d8f"
    "3c3095488b936b14538dca64d7e68bcde09a8a18d2a32a47b59877eff0340403"
    "f65b2e6dd304e19185d751652bd5ebc72a81d503bf542037acb3450e43abc095"
)

prepare(){
    ln -sfn edk2-0d61f52fe31c86936c5b4268effddad7241c811e edk2
    # Populate every recorded recursive gitlink from verified upstream archives.
    cp -a "$srcdir/brotli-f4153a09f87cbb9c826d8fc12c74642bb2d879ea/." "edk2/BaseTools/Source/C/BrotliCompress/brotli/"
    cp -a "$srcdir/mbedtls-8c89224991adff88d53cd380f42a2baa36f91454/." "edk2/CryptoPkg/Library/MbedTlsLib/mbedtls/"
    cp -a "$srcdir/openssl-98acb6b02839c609ef5b837794e08d906d965335/." "edk2/CryptoPkg/Library/OpensslLib/openssl/"
    cp -a "$srcdir/brotli-f4153a09f87cbb9c826d8fc12c74642bb2d879ea/." "edk2/MdeModulePkg/Library/BrotliCustomDecompressLib/brotli/"
    cp -a "$srcdir/oniguruma-abfc8ff81df4067f309032467785e06975678f0d/." "edk2/MdeModulePkg/Universal/RegularExpressionDxe/oniguruma/"
    cp -a "$srcdir/pylibfdt-cfff805481bdea27f900c32698171286542b8d3c/." "edk2/MdePkg/Library/BaseFdtLib/libfdt/"
    cp -a "$srcdir/public-mipi-sys-t-370b5944c046bab043dd8b133727b2135af7747a/." "edk2/MdePkg/Library/MipiSysTLib/mipisyst/"
    cp -a "$srcdir/pugixml-c53fdab93af76106b963216d85897614b996f8b6/." "edk2/MdePkg/Library/MipiSysTLib/mipisyst/external/pugixml/"
    cp -a "$srcdir/googletest-a6f06bf2fd3b832822cd4e9e554b7d47f32ec084/." "edk2/MdePkg/Library/MipiSysTLib/mipisyst/external/googletest/"
    cp -a "$srcdir/jansson-e9ebfa7e77a6bee77df44e096b100e7131044059/." "edk2/RedfishPkg/Library/JsonLib/jansson/"
    cp -a "$srcdir/libspdm-98ef964e1e9a0c39c7efb67143d3a13a819432e0/." "edk2/SecurityPkg/DeviceSecurity/SpdmLib/libspdm/"
    cp -a "$srcdir/openssl-de90e54bbe82e5be4fb9608b6f5c308bb837d355/." "edk2/SecurityPkg/DeviceSecurity/SpdmLib/libspdm/os_stub/openssllib/openssl/"
    cp -a "$srcdir/mbedtls-107ea89daaefb9867ea9121002fbbdf926780e98/." "edk2/SecurityPkg/DeviceSecurity/SpdmLib/libspdm/os_stub/mbedtlslib/mbedtls/"
    cp -a "$srcdir/mbedtls-framework-94599c0e3b5036e086446a51a3f79640f70f22f6/." "edk2/SecurityPkg/DeviceSecurity/SpdmLib/libspdm/os_stub/mbedtlslib/mbedtls/framework/"
    cp -a "$srcdir/cmocka-a01cc69ee9536f90e57c61a198f2d1944d3d4313/." "edk2/SecurityPkg/DeviceSecurity/SpdmLib/libspdm/unit_test/cmockalib/cmocka/"
    cp -a "$srcdir/edk2-cmocka-1cc9cde3448cdd2e000886a26acf1caac2db7cf1/." "edk2/UnitTestFrameworkPkg/Library/CmockaLib/cmocka/"
    cp -a "$srcdir/googletest-86add13493e5c881d7e4ba77fb91c1f57752b3a4/." "edk2/UnitTestFrameworkPkg/Library/GoogleTestLib/googletest/"
    cp -a "$srcdir/edk2-subhook-83d4e1ebef3588fae48b69a7352cc21801cb70bc/." "edk2/UnitTestFrameworkPkg/Library/SubhookLib/subhook/"
    cd edk2
    patch --binary -Np1 -i "$srcdir/basetools-unused-counter.patch"
}

build(){
    BUILD_DIR=$(realpath .)
    export EDK2_PATH="$BUILD_DIR/edk2"
    export EDK2_PLATFORMS_PATH="$BUILD_DIR/edk2-platforms-1.0.0"
    export REPO_PATH="$BUILD_DIR/${pkgname}Pkg-${pkgver}"
    # BaseTools and X64 firmware both use the native x86_64 host toolchain.
    cd "$REPO_PATH"
    source ./config
    ./build.sh
}

package(){
    install -Dm644 "$REPO_PATH/@BUILD/Build/i915ovmf/RELEASE_GCC5/X64/i915ovmf.rom" \
        "$pkgdir/var/lib/libvirt/qemu/drivers/i915ovmf.rom"
}
