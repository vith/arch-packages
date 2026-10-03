# Maintainer: vith
pkgname=oh-my-pi-vith-git
pkgver=18.4.8.vith.r345.g2f406d28d7e4
pkgrel=3
pkgdesc='Oh My Pi coding agent — vith fork'
arch=('x86_64')
url='https://github.com/vith/oh-my-pi'
license=('MIT')
depends=('glibc' 'gcc-libs' 'alsa-lib' 'libpulse' 'libpipewire' 'oniguruma' 'opus' 'pcre2' 'zstd')
makedepends=('git' 'bun' 'rustup' 'clang' 'pkgconf')
optdepends=('git: Git repository operations' 'tmux: background terminal sessions'
            'xdg-desktop-portal: Wayland screen sharing permissions')
provides=('omp' 'oh-my-pi')
conflicts=('omp' 'oh-my-pi' 'oh-my-pi-bin' 'oh-my-pi-git')
options=('!lto' '!strip' '!debug')
source=("oh-my-pi::git+${url}.git#branch=integration"
        'https://static.crates.io/crates/opus/opus-0.4.0.crate')
sha256sums=('SKIP'
            '33718946cc77d4032911d4efe03a66dbcbfbd2bb16c3da06aaeadcc637c32216')

prepare() {
  cd oh-my-pi
  cargo fetch --locked --target x86_64-unknown-linux-gnu
  sed -i '/^\[dependencies\.opusic-sys\]$/a default-features = false' "$srcdir/opus-0.4.0/Cargo.toml"
  printf '[patch.crates-io]\nopus = { path = "%s" }\n' "$srcdir/opus-0.4.0" > "$srcdir/system-opus.toml"
  cargo fetch --offline --target x86_64-unknown-linux-gnu --config "$srcdir/system-opus.toml"
}

pkgver() {
  cd oh-my-pi
  local tag
  tag=$(git describe --tags --match 'v[0-9]*' --abbrev=0) || return 1
  printf '%s.vith.r%s.g%s' "${tag#v}" "$(git rev-list --count "$tag"..HEAD)" "$(git rev-parse --short=12 HEAD)"
}

build() {
  cd oh-my-pi
  [[ $CARCH == x86_64 && $(uname -m) == x86_64 ]] || return 1
  bun install --frozen-lockfile --ignore-scripts
  local fork_version
  fork_version=$(bun scripts/prepare-fork-build.ts)
  printf 'Building omp/%s\n' "$fork_version"
  export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$srcdir/target}"
  export CFLAGS="${CFLAGS} -fno-strict-aliasing"
  export CXXFLAGS="${CXXFLAGS} -fno-strict-aliasing"
  export RUSTFLAGS="${RUSTFLAGS:-} -C target-cpu=x86-64-v2"
  export PCRE2_SYS_STATIC=0
  export ZSTD_SYS_USE_PKG_CONFIG=1
  export RUSTONIG_SYSTEM_LIBONIG=1
  cargo build --frozen --config "$srcdir/system-opus.toml" --profile ci --target x86_64-unknown-linux-gnu \
    -p pi-natives --features wayland-pipewire

  local addon=packages/natives/native/pi_natives.linux-x64-baseline.node
  install -Dm755 "$CARGO_TARGET_DIR/x86_64-unknown-linux-gnu/ci/libpi_natives.so" "$addon"
  bun scripts/stamp-native-version.ts "$addon" --version "$fork_version"
  local dynamic
  dynamic=$(LC_ALL=C readelf -d "$addon")
  [[ $dynamic == *'Shared library: [libpipewire-0.3.so.0]'* && $dynamic == *'Shared library: [libopus.so.'* ]] || {
    error "Native addon lacks required PipeWire or system Opus linkage"; return 1;
  }
  CROSS_TARGET=linux-x64 bun --cwd=packages/coding-agent run build
  [[ $(packages/coding-agent/dist/omp-linux-x64 --version) == "omp/$fork_version" ]]
}

package() {
  install -Dm755 "$srcdir/oh-my-pi/packages/coding-agent/dist/omp-linux-x64" "$pkgdir/usr/bin/omp"
  install -Dm644 "$srcdir/oh-my-pi/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 "$srcdir/oh-my-pi/THIRD-PARTY-NOTICES.txt" "$pkgdir/usr/share/licenses/$pkgname/THIRD-PARTY-NOTICES.txt"
}
