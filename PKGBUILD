# Based on the AUR zig0.15 recipe by Vitalii Kuzhdin and zig-bootstrap's build script.
# CARCH is the package target. The executable bootstrap compiler runs on ARM only.
pkgname=zig0.15
pkgver=0.15.2
pkgrel=2
pkgdesc='General-purpose programming language and toolchain for maintaining robust, optimal, and reusable software'
arch=('x86_64')
url='https://ziglang.org'
license=('MIT' 'Apache-2.0 WITH LLVM-exception' 'BSD-3-Clause' 'Zlib')
makedepends=('cmake>=3.19' 'ninja' 'python' 'ccache')
conflicts=('zig0.15-bin')
options=('emptydirs' '!buildflags' '!lto' '!strip' '!debug')
source=(
  "https://ziglang.org/download/${pkgver}/zig-bootstrap-${pkgver}.tar.xz"
  "https://ziglang.org/download/${pkgver}/zig-aarch64-linux-${pkgver}.tar.xz"
)
sha256sums=(
  'a6845459501df3c3264ebc587b02a7094ad14f4f3f7287c48f04457e784d0d85'
  '958ed7d1e00d0ea76590d27666efbf7a932281b3d7ba0c6b01b0ff26498f667f'
)

build() {
  if [[ $(uname -m) != aarch64 ]]; then
    error 'This recipe requires native aarch64 Linux build tools, targeting x86_64.'
    return 1
  fi

  local root="${srcdir}/zig-bootstrap-${pkgver}"
  local zig="${srcdir}/zig-aarch64-linux-${pkgver}/zig"
  local host="${root}/out/host-tools"
  local target=x86_64-linux-musl
  local prefix="${root}/out/${target}"

  # Never pass the package's x86 makepkg flags to the ARM host compiler.
  # Two compile jobs, one tablegen/link job, no LTO or debug info keep LLVM's
  # peak memory down on the 2-OCPU/12-GiB builder. Build Zig separately at -j1.
  unset CPPFLAGS CFLAGS CXXFLAGS LDFLAGS
  export CMAKE_BUILD_PARALLEL_LEVEL=2
  export CCACHE_BASEDIR="${srcdir}"
  export CCACHE_COMPILERCHECK=content
  export ZIG_GLOBAL_CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/zig"
  # Also compile libc/libc++ and compiler-rt from the verified source bundle.
  export ZIG_LIB_DIR="${root}/zig/lib"

  local llvm_options=(
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    '-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG'
    '-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG'
    -DLLVM_ENABLE_LTO=OFF
    -DLLVM_PARALLEL_COMPILE_JOBS=2
    -DLLVM_PARALLEL_LINK_JOBS=1
    -DLLVM_PARALLEL_TABLEGEN_JOBS=1
    -DLLVM_ENABLE_BACKTRACES=OFF
    -DLLVM_ENABLE_BINDINGS=OFF
    -DLLVM_ENABLE_CRASH_OVERRIDES=OFF
    -DLLVM_ENABLE_LIBEDIT=OFF
    -DLLVM_ENABLE_LIBPFM=OFF
    -DLLVM_ENABLE_LIBXML2=OFF
    -DLLVM_ENABLE_OCAMLDOC=OFF
    -DLLVM_ENABLE_PLUGINS=OFF
    '-DLLVM_ENABLE_PROJECTS=lld;clang'
    -DLLVM_ENABLE_Z3_SOLVER=OFF
    -DLLVM_BUILD_UTILS=OFF
    -DLLVM_BUILD_TOOLS=OFF
    -DLLVM_BUILD_STATIC=ON
    -DLLVM_INCLUDE_UTILS=OFF
    -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF
    -DLLVM_INCLUDE_DOCS=OFF
    -DLLVM_TOOL_LLVM_LTO2_BUILD=OFF
    -DLLVM_TOOL_LLVM_LTO_BUILD=OFF
    -DLLVM_TOOL_LTO_BUILD=OFF
    -DLLVM_TOOL_REMARKS_SHLIB_BUILD=OFF
    -DCLANG_BUILD_TOOLS=OFF
    -DCLANG_INCLUDE_DOCS=OFF
    -DCLANG_INCLUDE_TESTS=OFF
    -DCLANG_ENABLE_ARCMT=ON
    -DCLANG_TOOL_CLANG_IMPORT_TEST_BUILD=OFF
    -DCLANG_TOOL_CLANG_LINKER_WRAPPER_BUILD=OFF
    -DCLANG_TOOL_C_INDEX_TEST_BUILD=OFF
    -DCLANG_TOOL_ARCMT_TEST_BUILD=OFF
    -DCLANG_TOOL_C_ARCMT_TEST_BUILD=OFF
    -DCLANG_TOOL_LIBCLANG_BUILD=OFF
    -DLLD_BUILD_TOOLS=OFF
  )

  # Only the source-matched TableGen programs must run on the host. The pinned
  # native Zig input is a build tool, not a package payload. This avoids building
  # a second complete LLVM/Clang/LLD and Zig just to obtain a cross compiler.
  cmake -S "${root}/llvm" -B "${host}" "${llvm_options[@]}" \
    -DCMAKE_C_COMPILER=cc \
    -DCMAKE_CXX_COMPILER=c++ \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache \
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DLLVM_TARGETS_TO_BUILD=Native \
    -DLLVM_ENABLE_ZLIB=OFF \
    -DLLVM_ENABLE_ZSTD=OFF
  cmake --build "${host}" --parallel 2 --target llvm-tblgen clang-tblgen

  # Zig supplies the target libc/libc++ sysroot. The image's native cross-binutils
  # archive x86 objects without ever executing them. No target system LLVM is used.
  local cross_options=(
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    '-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG'
    '-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG'
    "-DCMAKE_INSTALL_PREFIX=${prefix}"
    "-DCMAKE_PREFIX_PATH=${prefix}"
    -DCMAKE_SYSTEM_NAME=Linux
    -DCMAKE_SYSTEM_PROCESSOR=x86_64
    "-DCMAKE_C_COMPILER=${zig};cc;-fno-sanitize=all;-s;-target;${target};-mcpu=baseline"
    "-DCMAKE_CXX_COMPILER=${zig};c++;-fno-sanitize=all;-s;-target;${target};-mcpu=baseline"
    "-DCMAKE_ASM_COMPILER=${zig};cc;-fno-sanitize=all;-s;-target;${target};-mcpu=baseline"
    -DCMAKE_C_COMPILER_LAUNCHER=ccache
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
    -DCMAKE_LINK_DEPENDS_USE_LINKER=OFF
    -DCMAKE_AR=/usr/bin/x86_64-linux-gnu-ar
    -DCMAKE_RANLIB=/usr/bin/x86_64-linux-gnu-ranlib
  )

  # zig cc/c++ use Clang, but ccache cannot infer that from the executable name.
  export CCACHE_COMPILERTYPE=clang
  cmake -S "${root}/zlib" -B "${root}/out/build-zlib" "${cross_options[@]}"
  cmake --build "${root}/out/build-zlib" --parallel 2 --target install

  # These are precisely the source groups used by upstream zig-bootstrap;
  # the bootstrap archive deliberately omits zstd's separate build system.
  install -Dm644 "${root}/zstd/lib/zstd.h" "${prefix}/include/zstd.h"
  cd "${prefix}/lib"
  "${zig}" build-lib --name zstd -target "${target}" -mcpu=baseline \
    -fstrip -OReleaseFast -lc \
    "${root}"/zstd/lib/{common,compress,decompress,deprecated,dictBuilder}/*.c \
    "${root}/zstd/lib/decompress/huf_decompress_amd64.S"

  # Keep every upstream LLVM backend: this x86 compiler must still be able to
  # cross-compile user programs for architectures other than x86.
  cmake -S "${root}/llvm" -B "${root}/out/build-llvm-target" \
    "${llvm_options[@]}" "${cross_options[@]}" \
    -DLLVM_TARGETS_TO_BUILD=all \
    "-DLLVM_DEFAULT_TARGET_TRIPLE=${target}" \
    "-DLLVM_TABLEGEN=${host}/bin/llvm-tblgen" \
    "-DCLANG_TABLEGEN=${host}/bin/clang-tblgen" \
    -DLLVM_ENABLE_ZLIB=FORCE_ON \
    -DLLVM_ENABLE_ZSTD=FORCE_ON \
    -DLLVM_USE_STATIC_ZSTD=ON
  cmake --build "${root}/out/build-llvm-target" --parallel 2 --target install

  # This is the only compiler installed in the package: compiled from the Zig
  # sources and source-built target libraries. Documentation generators run on
  # the native host; upstream doctest skips execution of foreign-target examples.
  cd "${root}/zig"
  "${zig}" build -j1 \
    --prefix "${root}/out/zig-${target}" \
    --search-prefix "${prefix}" \
    -Dflat -Dstatic-llvm -Doptimize=ReleaseFast -Dstrip \
    -Dtarget="${target}" -Dcpu=baseline -Dversion-string="${pkgver}"
}

package() {
  local root="${srcdir}/zig-bootstrap-${pkgver}"
  local output="${root}/out/zig-x86_64-linux-musl"

  install -Dm755 "${output}/zig" "${pkgdir}/opt/${pkgname}/zig"
  cp -a --no-preserve=ownership "${output}/lib" "${pkgdir}/opt/${pkgname}/"
  install -Dm644 "${output}/README.md" "${pkgdir}/usr/share/doc/${pkgname}/README.md"
  cp -a --no-preserve=ownership "${output}/doc/." "${pkgdir}/usr/share/doc/${pkgname}/"
  install -Dm644 "${root}/zig/LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
  install -Dm644 "${root}/llvm/LICENSE.TXT" "${pkgdir}/usr/share/licenses/${pkgname}/LLVM-LICENSE"
  install -Dm644 "${root}/clang/LICENSE.TXT" "${pkgdir}/usr/share/licenses/${pkgname}/Clang-LICENSE"
  install -Dm644 "${root}/lld/LICENSE.TXT" "${pkgdir}/usr/share/licenses/${pkgname}/LLD-LICENSE"
  install -Dm644 "${root}/zlib/LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/zlib-LICENSE"
  install -Dm644 "${root}/zstd/LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/zstd-LICENSE"

  install -dm755 "${pkgdir}/usr/bin" "${pkgdir}/usr/lib"
  ln -s "/opt/${pkgname}/zig" "${pkgdir}/usr/bin/zig-0.15"
  ln -s "/opt/${pkgname}/lib" "${pkgdir}/usr/lib/${pkgname}"
}
