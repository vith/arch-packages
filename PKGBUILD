# Based on the AUR prek recipe by Jamison Lahman <jamison+aur@lahman.dev>.

pkgname=prek
pkgver=0.5.4
pkgrel=1
pkgdesc="Better pre-commit, re-engineered in Rust"
arch=('x86_64')
url='https://github.com/j178/prek'
license=('MIT')
depends=('gcc-libs' 'glibc' 'git')
makedepends=('rustup' 'pkgconf' 'cmake')
conflicts=('prek-bin')
options=('!debug' '!lto' '!strip')
_commit=77c4056ce76b600de77d5d425e52844a76652a58
source=("$pkgname-$pkgver.tar.gz::https://codeload.github.com/j178/prek/tar.gz/$_commit"
        'prek.bash' 'prek.zsh' 'prek.fish')
sha256sums=('80e79638123de94614fc1af0f118539492821dc766769f4726dc2d6ffd395792'
            'b162bd604be208dca1cea8b591190043c83e4fd9b7355fafbb666c56bb97aed8'
            '69c6c01e1cf1a6093f0d2482ff72b40fa1f8711689f8cd33d9281141c56ed535'
            'f84ea5786bcfd25d096a7eab94f6f96517b565dc3a699c6dfc4d8decb9b4057c')

_target=x86_64-unknown-linux-gnu

_cross_env() {
    [[ $CARCH == x86_64 ]] || { error "Unsupported package target: $CARCH"; return 1; }
    # Installed in the native image, including the target standard library;
    # bypass upstream's 1.98 pin without mutating the shared Rust installation.
    export RUSTUP_TOOLCHAIN=nightly-2026-08-12
    export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$srcdir/target}"
    export HOST_CC=gcc HOST_CXX=g++ HOST_AR=ar
    local prefix=
    if [[ $(uname -m) != x86_64 ]]; then
        prefix=x86_64-linux-gnu-
    fi
    export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="${prefix}gcc"
    export CC_x86_64_unknown_linux_gnu="${prefix}gcc"
    export CXX_x86_64_unknown_linux_gnu="${prefix}g++"
    export AR_x86_64_unknown_linux_gnu="${prefix}ar"
    export CFLAGS_x86_64_unknown_linux_gnu="${CFLAGS_x86_64_unknown_linux_gnu:-${CFLAGS:-} ${CPPFLAGS:-}}"
    export CXXFLAGS_x86_64_unknown_linux_gnu="${CXXFLAGS_x86_64_unknown_linux_gnu:-${CXXFLAGS:-} ${CPPFLAGS:-}}"
    unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS

    # liblzma and AWS-LC compile their bundled C/assembly sources for TARGET.
    # Pregenerated AWS-LC bindings need neither libclang nor a host libcrypto.
    export LZMA_API_STATIC=1
    export AWS_LC_SYS_USE_SYSTEM=0 AWS_LC_SYS_STATIC=1
}

prepare() {
    cd "$pkgname-$_commit"
    _cross_env
    cargo fetch --locked --target "$_target"
}

build() {
    cd "$pkgname-$_commit"
    _cross_env
    # Do not enable upstream's self-update feature for a managed package.
    cargo build --frozen --release --package prek --bin prek --target "$_target"
}

package() {
    cd "$pkgname-$_commit"
    local target_dir="${CARGO_TARGET_DIR:-$srcdir/target}"
    install -Dm755 "$target_dir/$_target/release/prek" "$pkgdir/usr/bin/prek"

    # Rendered from the locked clap_complete 4.6.11 dynamic registration
    # templates with name/bin/completer=prek and var=COMPLETE. These wrappers
    # ask the installed program for candidates at completion time, so package
    # creation never executes a target binary or builds a second host CLI.
    install -Dm644 "$srcdir/prek.bash" "$pkgdir/usr/share/bash-completion/completions/prek"
    install -Dm644 "$srcdir/prek.zsh" "$pkgdir/usr/share/zsh/site-functions/_prek"
    install -Dm644 "$srcdir/prek.fish" "$pkgdir/usr/share/fish/vendor_completions.d/prek.fish"

    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -m644 licenses/*.txt "$pkgdir/usr/share/licenses/$pkgname/"
}
