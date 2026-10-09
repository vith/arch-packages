# Maintainer: Henry-ZHR <henry-zhr@qq.com>
_name=sentencepiece
pkgbase="${_name}"
pkgname=("${pkgbase}" "python-${pkgbase}")
pkgver=0.2.2
pkgrel=3
pkgdesc="Unsupervised text tokenizer for Neural Network-based text generation"
arch=('x86_64')
url="https://github.com/google/sentencepiece"
license=('Apache-2.0')
makedepends=(
  'git'
  'cmake'
  # For sentencepiece
  'abseil-cpp'
  'gperftools'
  'protobuf'
  # For python-sentencepiece
  'python'
  'python-build'
  'python-setuptools'
  'python-wheel'
  'pybind11'
  'python-installer'
)
checkdepends=(
  'python-pytest'
  'python-protobuf'
)
source=(
  "${_name}::git+${url}.git#tag=v${pkgver}"
  'dont-include-data-files-in-python-pkg.patch'
)
sha256sums=(
  '3b781cc234d419de51245501c02fe8c1dc03eba9b79a8eefaf032046348edf05'
  '85e56e6d2345dddb9717d028ce78eb428d9190a150a1929ca43d60aaf1895b37'
)

prepare() {
  cd "${_name}"

  git clean -dfx

  # Make sure we use system packages
  rm -rf src/builtin_pb third_party/{abseil-cpp,protobuf-lite}

  # The base pkg includes data already
  git apply --verbose ../dont-include-data-files-in-python-pkg.patch

  # Use shared libs for python module
  sed -i 's/libsentencepiece.a/libsentencepiece.so/g' python/setup.py
  sed -i 's/libsentencepiece_train.a/libsentencepiece_train.so/g' python/setup.py
}

build() {
  cd "${_name}"

  cmake -S . -B build \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_BUILD_TYPE=None \
    -DSPM_BUILD_TEST=ON \
    -DSPM_ENABLE_TCMALLOC=ON \
    -DSPM_ENABLE_SHARED=ON \
    -DSPM_DISABLE_EMBEDDED_DATA=OFF \
    -DSPM_PROTOBUF_PROVIDER=package \
    -DSPM_ABSL_PROVIDER=package \
    -Wno-dev
  cmake --build build --parallel "$(nproc)"

  mkdir build/root
  DESTDIR=build/root cmake --install build --prefix /
  cd python
  python -m build --wheel --no-isolation
}

check() {
  cd "${_name}"

  ctest --test-dir build --output-on-failure

  (
    cd python
    local python_version=$(python -c 'import sys; print("".join(map(str, sys.version_info[:2])))')
    export PYTHONPATH="${PWD}/build/lib.linux-${CARCH}-cpython-${python_version}"
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${srcdir}/${_name}/build/root/lib"
    pytest test/
  )
}

package_sentencepiece() {
  depends=('libgcc' 'libstdc++' 'glibc' 'abseil-cpp' 'gperftools' 'protobuf')
  provides=('libsentencepiece.so' 'libsentencepiece_train.so')

  DESTDIR="${pkgdir}" cmake --install "${_name}/build"
}

package_python-sentencepiece() {
  pkgdesc="Python wrapper for SentencePiece"
  depends=("${pkgbase}=${pkgver}-${pkgrel}" 'libgcc' 'libstdc++' 'glibc' 'python')
  optdepends=(
    'python-numpy'
    'python-protobuf'
  )

  cd "${_name}/python"
  python -m installer --destdir="${pkgdir}" dist/*.whl
}
