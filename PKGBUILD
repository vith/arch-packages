# Maintainer: vith
pkgname=oh-my-pi-vith-git
pkgver=r25173.c1a969eada61
pkgrel=2
pkgdesc='Oh My Pi coding agent — vith fork'
arch=('x86_64')
url='https://github.com/vith/oh-my-pi-vith'
license=('MIT')
depends=('glibc' 'gcc-libs' 'alsa-lib' 'libpulse' 'libpipewire')
makedepends=('git' 'bun' 'rustup' 'clang' 'cmake' 'ninja' 'pkgconf' 'libarchive')
optdepends=('git: Git repository operations' 'tmux: background terminal sessions'
            'xdg-desktop-portal: Wayland screen sharing permissions')
provides=('omp' 'oh-my-pi')
conflicts=('omp' 'oh-my-pi' 'oh-my-pi-bin' 'oh-my-pi-git')
# Bun appends embedded assets to the ELF; stripping would corrupt the bundle.
options=('!strip' '!debug')
source=("oh-my-pi::git+${url}.git#branch=integration")
sha256sums=('SKIP')

# Pin target headers/libraries independently of the execution host. Native ARM
# CI cross-compiles this Arch addon; target binaries never enter PATH.
_target_packages=(
  'glibc glibc-2.44+r50+g1848099f063e-1-x86_64.pkg.tar.zst e8e4d50d45cb2bd21c9f7eabacc6a01bd5eb93dbd476f3f44d44038fc1eba417'
  'libgcc libgcc-16.2.1+r23+gd564253eb6c8-1-x86_64.pkg.tar.zst 7367dad49fc3229bde804816d412ab77306d433b1f92e4126b8b3ba3502c67d6'
  'libstdc++ libstdc++-16.2.1+r23+gd564253eb6c8-1-x86_64.pkg.tar.zst 15dc6bd2f3a2ee17fcd79a14325e9e0037722a592b2bdc55e1665d92112eaa51'
  'libpipewire libpipewire-1:1.6.9-1-x86_64.pkg.tar.zst f2bf96acd15f145a4a2ae37f73f9426cc67a3f215a4e74a1414b4f797f233cb3'
  'linux-api-headers linux-api-headers-7.2-1-x86_64.pkg.tar.zst d8d3483363e70b353ae31bbf8773df77780724eaeaa140faf4e4111bdb87588f'
)
noextract=()
for _entry in "${_target_packages[@]}"; do
  read -r _name _archive _sha256 <<< "$_entry"
  source+=("https://archive.archlinux.org/packages/${_name:0:1}/${_name}/${_archive}")
  sha256sums+=("$_sha256")
  noextract+=("$_archive")
done
unset _entry _name _archive _sha256

prepare() {
  local _entry _name _archive _sha256 _members _prefix
  local -a _includes
  mkdir -p "$srcdir/arch-sysroot"
  for _entry in "${_target_packages[@]}"; do
    read -r _name _archive _sha256 <<< "$_entry"
    _members=$(bsdtar -tf "$srcdir/$_archive") || return
    _includes=()
    for _prefix in 'usr/lib/' 'usr/bin/' 'usr/include/' 'usr/share/pkgconfig/'; do
      if [[ $'\n'"$_members" == *$'\n'"$_prefix"* ]]; then
        _includes+=(--include "${_prefix}*")
      fi
    done
    # Keep glibc's getconf hard-link targets, without executing target tools.
    bsdtar -xf "$srcdir/$_archive" -C "$srcdir/arch-sysroot" "${_includes[@]}" || return
  done
}

pkgver() {
  cd oh-my-pi
  local tag
  tag=$(git describe --tags --match 'v[0-9]*' --abbrev=0) || return 1
  printf '%s.vith.r%s.g%s' "${tag#v}" "$(git rev-list --count "$tag"..HEAD)" "$(git rev-parse --short=12 HEAD)"
}

build() {
  cd oh-my-pi
  # The portable Bazel addon compiles out Wayland capture. Build this
  # distro-specific addon with Cargo and the actual PipeWire feature instead.
  [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
  case "$(uname -m)" in x86_64|aarch64) ;; *) return 1 ;; esac
  # Build tools (tsgo, etc.) must match the execution host. Installing x86
  # npm dependencies here would break the ARM-running build tools.
  bun install --frozen-lockfile --ignore-scripts
  local fork_version
  fork_version=$(bun scripts/prepare-fork-build.ts)
  printf 'Building omp/%s\n' "$fork_version"
  local sysroot="$srcdir/arch-sysroot" tool_prefix=
  if [[ $(uname -m) != "$CARCH" ]]; then tool_prefix=x86_64-linux-gnu-; fi
  export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$srcdir/target}"
  export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="${tool_prefix}gcc"
  export CC_x86_64_unknown_linux_gnu="${tool_prefix}gcc"
  export CXX_x86_64_unknown_linux_gnu="${tool_prefix}g++"
  export AR_x86_64_unknown_linux_gnu="${tool_prefix}ar"
  export CFLAGS_x86_64_unknown_linux_gnu="${CFLAGS//-flto=auto/} --sysroot=$sysroot -march=x86-64-v2 -mtune=generic"
  export CXXFLAGS_x86_64_unknown_linux_gnu="${CXXFLAGS//-flto=auto/} --sysroot=$sysroot -march=x86-64-v2 -mtune=generic"
  export PKG_CONFIG_ALLOW_CROSS_x86_64_unknown_linux_gnu=1
  export PKG_CONFIG_SYSROOT_DIR_x86_64_unknown_linux_gnu="$sysroot"
  export PKG_CONFIG_LIBDIR_x86_64_unknown_linux_gnu="$sysroot/usr/lib/pkgconfig:$sysroot/usr/share/pkgconfig"
  export PKG_CONFIG_PATH_x86_64_unknown_linux_gnu=
  export BINDGEN_EXTRA_CLANG_ARGS_x86_64_unknown_linux_gnu="--target=x86_64-unknown-linux-gnu --sysroot=$sysroot"
  export RUSTFLAGS="${RUSTFLAGS:-} -C target-cpu=x86-64-v2 -C link-arg=--sysroot=$sysroot -C link-arg=-B$sysroot/usr/lib/ -C link-arg=-L$sysroot/usr/lib -C link-arg=-Wl,-rpath-link,$sysroot/usr/lib"
  export PCRE2_SYS_STATIC=1
  cargo build --locked --profile ci --target x86_64-unknown-linux-gnu \
    -p pi-natives --features wayland-pipewire

  local addon=packages/natives/native/pi_natives.linux-x64-baseline.node
  install -Dm755 "$CARGO_TARGET_DIR/x86_64-unknown-linux-gnu/ci/libpi_natives.so" "$addon"
  bun scripts/stamp-native-version.ts "$addon" --version "$fork_version"
  # Gate the artifact, not just the requested flags: a feature-less addon
  # must never be bundled into another apparently successful package.
  [[ $(LC_ALL=C readelf -d "$addon") == *'Shared library: [libpipewire-0.3.so.0]'* ]] || {
    error "Native addon lacks the PipeWire capture dependency"; return 1;
  }
  # One executable for this package, not a standalone release artifact/matrix.
  CROSS_TARGET=linux-x64 bun --cwd=packages/coding-agent run build
  if [[ $(uname -m) == x86_64 ]]; then
    [[ $(packages/coding-agent/dist/omp-linux-x64 --version) == "omp/$fork_version" ]]
  fi
}

package() {
  install -Dm755 "$srcdir/oh-my-pi/packages/coding-agent/dist/omp-linux-x64" "$pkgdir/usr/bin/omp"
  install -Dm644 "$srcdir/oh-my-pi/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 "$srcdir/oh-my-pi/THIRD-PARTY-NOTICES.txt" "$pkgdir/usr/share/licenses/$pkgname/THIRD-PARTY-NOTICES.txt"
}
