# Maintainer: Amin Vakil <info AT aminvakil DOT com>

pkgname=pi
pkgver=0.99.1
pkgrel=2
pkgdesc="AI coding agent for the terminal — minimal, extensible and optimized for tool use"
arch=('x86_64' 'aarch64')
url="https://github.com/earendil-works/pi"
license=('MIT')
depends=('nodejs>=22.19.0')
makedepends=('npm')
optdepends=(
  'tmux: for background bash capabilities'
  'fd: system-provided backend for the find tool'
  'ripgrep: system-provided backend for the grep tool'
  'libxcb: native X11 clipboard support'
)
options=('!strip' '!debug')

source=("${pkgname}-${pkgver}.tar.gz::${url}/archive/refs/tags/v${pkgver}.tar.gz"
        "pi-ai-${pkgver}.tgz::https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-${pkgver}.tgz")
sha256sums=('f2cd629b7678e6b2880cf1ca8aa3cf3badc9a4089bf4ebdbd7ab8b83aa0653eb'
            'f9f44692157d0bf5679c4a17304a310028231d7daaeaaea3b73252f4b7a264d3')

prepare() {
  rm -rf "${srcdir}/${pkgname}-${pkgver}/packages/ai/src/providers/data"
  cp -a "${srcdir}/package/dist/providers/data" \
    "${srcdir}/${pkgname}-${pkgver}/packages/ai/src/providers/"
}

build() {
  cd "${pkgname}-${pkgver}"

  export npm_config_cache="${npm_config_cache-"${srcdir}/npm-cache"}"

  local _npm_cpu _other_cpu
  case "$CARCH" in
    x86_64) _npm_cpu=x64; _other_cpu=arm64 ;;
    aarch64) _npm_cpu=arm64; _other_cpu=x64 ;;
    *) echo "Unsupported architecture: $CARCH" >&2; return 1 ;;
  esac

  # Build with native tooling (notably the host-architecture tsgo binary).
  npm ci --ignore-scripts --no-audit --no-fund
  npm run build:offline

  # A clean, production-only install from the same lockfile selects target
  # optional binaries without retaining or reinstalling host build tools.
  npm ci --omit=dev --cpu="$_npm_cpu" --os=linux --libc=glibc \
    --ignore-scripts --no-audit --no-fund

  # This dependency ships both architectures inside one npm tarball rather
  # than platform-specific optional packages. Its loader selects process.arch;
  # retain the target's helper/BPF files, not the other architecture's copy.
  local _vendor
  for _vendor in node_modules/@anthropic-ai/sandbox-runtime/{vendor,dist/vendor}/seccomp; do
    [[ -x "$_vendor/$_npm_cpu/apply-seccomp" ]] || return 1
    rm -rf "$_vendor/$_other_cpu"
  done
}

package() {
  cd "${pkgname}-${pkgver}"

  local mod_dir="/usr/lib/node_modules/$pkgname"

  install -dm755 "$pkgdir/$mod_dir/node_modules" \
                 "$pkgdir/usr/bin" \
                 "$pkgdir/usr/share/doc/$pkgname"

  cp -a node_modules/. "$pkgdir/$mod_dir/node_modules/"

  local _pkg
  for _pkg in ai agent tui telemetry chord coding-agent codemode mcp; do
    install -dm755 "$pkgdir/$mod_dir/packages/$_pkg"
    cp -a "packages/$_pkg/dist" "packages/$_pkg/package.json" "packages/$_pkg/README.md" \
      "$pkgdir/$mod_dir/packages/$_pkg/"
  done

  local _npm_cpu
  case "$CARCH" in
    x86_64) _npm_cpu=x64 ;;
    aarch64) _npm_cpu=arm64 ;;
    *) echo "Unsupported architecture: $CARCH" >&2; return 1 ;;
  esac
  install -Dm755 "packages/tui/native/linux/prebuilds/linux-$_npm_cpu/linux-platform-x11.node" \
    "$pkgdir/$mod_dir/packages/tui/native/linux/prebuilds/linux-$_npm_cpu/linux-platform-x11.node"

  # Copy the additional files for coding-agent
  cp -a packages/coding-agent/{docs,examples,CHANGELOG.md} \
    "$pkgdir/$mod_dir/packages/coding-agent/"

  ln -s "$mod_dir/packages/coding-agent/dist/bundle/cli.js" "$pkgdir/usr/bin/pi"

  # Copy coding-agent docs and README and CHANGELOG into /usr/share/doc/pi to align it with Arch packages
  cp -r packages/coding-agent/docs/* packages/coding-agent/examples "$pkgdir/usr/share/doc/$pkgname/"
  install -m644 packages/coding-agent/{README,CHANGELOG}.md "$pkgdir/usr/share/doc/$pkgname/"

  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
