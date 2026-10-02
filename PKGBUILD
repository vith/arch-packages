# Maintainer: Omar Pakker <archlinux@opakker.nl>

pkgbase=looking-glass
pkgname=("${pkgbase}"
         "${pkgbase}-module-dkms"
#         "${pkgbase}-host"
         "obs-plugin-${pkgbase}")
epoch=2
pkgver=B7
pkgrel=8
pkgdesc="An extremely low latency KVMFR (KVM FrameRelay) implementation for guests with VGA PCI Passthrough"
url="https://looking-glass.io/"
arch=('x86_64')
license=('GPL-2.0-or-later')
makedepends=('cmake' 'make' 'pkgconf' 'wayland' 'gawk' 'libarchive')
source=("looking-glass-${pkgver}.tar.gz::https://looking-glass.io/artifact/${pkgver}/source"
        "1197-backport.patch"
        "nettle4-compat.patch")
sha256sums=('09e506660ccc1b9691d06caa70179b52ffb4393299895cff3c2f0e74fcd69985'
            '1caacd0984277fa5bcfc7a727ae9ece62ca06b9c16d1d8d9917f9cc80181049d'
            'e7aedcd68532a5c4fddaf5e6c5386f6f37858e1ab81a83d906a93abd1839077e')

# Official Arch target compile/link closure, pinned from repository metadata.
# GPU drivers and target commands are runtime dependencies, not build tools.
# Never put this sysroot's usr/bin on PATH: generators must run on the host.
_target_packages=(
  'alsa-lib-1.2.16.1-1-x86_64.pkg.tar.zst|2d2f2b3966cebd6ec75abbbdb4709a1093849207ada6624cdfe36f38d0f5c811'
  'alsa-topology-conf-1.2.5.1-4-any.pkg.tar.zst|a76b237e24973099b3cee9d3c89f4f8b46d9133411f0992bd46a5131e4427a0c'
  'alsa-ucm-conf-1.2.16.1-1-any.pkg.tar.zst|73e3a6cf819dfcedf884616c18d2206dea77e98311717dca8135256fabe55fbc'
  'audit-4.2.1-1-x86_64.pkg.tar.zst|6b2f7e400336d05bf9a4b1b65015e4a7ac3a3842fbc67790913f9748513b964e'
  'binutils-2.47-4-x86_64.pkg.tar.zst|d9508ad848fd6c31473ac0925cf8c3ec0d2a71bbbb5c7e39274186338a285660'
  'brotli-1.2.0-1-x86_64.pkg.tar.zst|4a0c95d5967476d0efdaf76d344b61e3eee02cd7920a315e457f3fd96311b7ec'
  'bzip2-1.0.8-6-x86_64.pkg.tar.zst|8779003d659c441b952095c19907603a738c1366f25cc51be3fd139fa4e95748'
  'cairo-1.18.6-1-x86_64.pkg.tar.zst|0df806e0edf067d62b417c77d978a7577c4d6e11a724db3c12adea1a425666a1'
  'dbus-1.16.2-1-x86_64.pkg.tar.zst|740fb8d02f6a5798984f17bf09c4f9f62f42a8339499c283867d9c35cda0d8af'
  'e2fsprogs-1.47.4-1-x86_64.pkg.tar.zst|a78971bbbe0ae89d15a0cbbc952e64462edc5a6f991c1bb9cefd0539ca548bd4'
  'expat-2.8.5-1-x86_64.pkg.tar.zst|25b7b51074df3c851b315a116e3a7a30a5d37b63cbbbc6e18e3a6563ba4e3f27'
  'flac-1.5.0-1-x86_64.pkg.tar.zst|7c8dce6bde402b9d243fd240847722a57b94df1dbf53e0cabc9119219dd04735'
  'fontconfig-2:2.18.3-2-x86_64.pkg.tar.zst|c3d961568e10d0bc035b52554333535d31853310b9b7936eedf5d026425d3adf'
  'freetype2-2.14.3-1-x86_64.pkg.tar.zst|fcaa410420dea42779d02aa76f1cc95d8430bdc52071ac6219d33306899b8655'
  'fribidi-1.0.17-1-x86_64.pkg.tar.zst|7e033aa73bb62ee3782d4d4943cafbf455f077c0ab60442c2b36add53c3588d5'
  'gdbm-1.26-2-x86_64.pkg.tar.zst|7bb8768fc7f617c580ae4c7902aed5413984a8374915da42beeecbb085c2a29e'
  'glib2-2.88.3-1-x86_64.pkg.tar.zst|9569884e1f670d46e40ea0b8ca4ea2a1a29689be297fed1390bc5af9a03967d2'
  'glibc-2.44+r50+g1848099f063e-1-x86_64.pkg.tar.zst|e8e4d50d45cb2bd21c9f7eabacc6a01bd5eb93dbd476f3f44d44038fc1eba417'
  'gmp-6.3.0-3-x86_64.pkg.tar.zst|2969061e117d2a8c19d89427b0b88e1c956a6269bc0602087d41aecd15097064'
  'gnutls-3.8.13-3-x86_64.pkg.tar.zst|a61d5d908aa83ad2ed6ddffda70ae2ddb5a81852dddcbf68ec42c1744e8a8ee9'
  'graphite-1:1.3.15-1-x86_64.pkg.tar.zst|b3bd9b30b0715c36b5cfd997ca35ada9c559b1f89f3c386cae7847714c7a5ea2'
  'harfbuzz-14.5.0-1-x86_64.pkg.tar.zst|0d34449b6875e6402615785543054b850d9b91cde755db0fd3a1681faf37d274'
  'icu-78.3-1-x86_64.pkg.tar.zst|118fe41efa6c550f3fe67893946dcea2c6cbb5272299a6ddf4160fb17f3e4bb0'
  'jansson-2.15.1-1-x86_64.pkg.tar.zst|ff1ff9da9ec0520b30cc4add6160ba5a171d9a478408863a2d99b61f5bb9866b'
  'json-c-0.19-1-x86_64.pkg.tar.zst|a2ebb9395bfc18a29abd21b6dd756519114e4e6241d6321588ada285ded4d63f'
  'keyutils-1.6.3-4-x86_64.pkg.tar.zst|e3e5d4226bb7d213a6baddb997bf679a2b1106d95b5c2845b79bf8df1c3ef5fe'
  'krb5-1.22.2-1-x86_64.pkg.tar.zst|38dcaf86512559c47e9b084ce78f417b5e8b0634feb8cb80b5edeab2c40b704f'
  'lame-4.0-1-x86_64.pkg.tar.zst|2c7bf7150e41a12b7b2ef62fec6ab5a071e20b537ac734490922fa64c204af77'
  'leancrypto-1.9.0-1-x86_64.pkg.tar.zst|e87627077ef83a0edeca19b60971c5bd8dba753a7fe8607d20f86cd0ba1965a8'
  'libasyncns-1:0.8+r3+g68cd5af-3-x86_64.pkg.tar.zst|de65e601391ace5c126e863ad67245b03cfe87f46c12b509668f3da2419bbf15'
  'libcap-ng-0.9.6-1-x86_64.pkg.tar.zst|05b0a8356f898e251845b2e5ffa5966be21ca85f3eaaf5aeb8a9e8df4148ba5c'
  'libdatrie-0.2.14-1-x86_64.pkg.tar.zst|f5ad651ed99afa29d44c662da4f2c6e93764e8a1d47487f41837be1c94ddd38e'
  'libdecor-0.2.5-1-x86_64.pkg.tar.zst|79fb92af5bf5358da4dc418fc784554284c09cdf4e8aa2cdc95197752d1df115'
  'libelf-0.196-1-x86_64.pkg.tar.zst|0f75f1f363c60d73f563fddc82964acd01cdc5167dbe2309d5d90e833388842b'
  'libevent-2.1.13-2-x86_64.pkg.tar.zst|9fd8563e3aa1c64d80257b7b4f60d37cf8589260ba3eb015f8257e2cfd118b04'
  'libffi-3.8.0-1-x86_64.pkg.tar.zst|5d21227f2a1d10db60d0cf5bb02b36a1801ae61dc7dae8c3bc1afa548afd8601'
  'libgcc-16.2.1+r23+gd564253eb6c8-1-x86_64.pkg.tar.zst|7367dad49fc3229bde804816d412ab77306d433b1f92e4126b8b3ba3502c67d6'
  'libgcrypt-1.12.4-1-x86_64.pkg.tar.zst|25ca27f53e0862e706b325d05893e43585be2bb0a0a4a750b8faa6e0e253e136'
  'libglvnd-1.7.0-3-x86_64.pkg.tar.zst|633c2e95d7798d0ce7ee1b7fe970a22325f7f6d35b9f923bb61b223a80934437'
  'libgpg-error-1.61-1-x86_64.pkg.tar.zst|7d5a5b39f588b275558f5e13bd792bff84cf89abb6d48e3e494d6c30e8ea9ca4'
  'libidn2-2.3.8-1-x86_64.pkg.tar.zst|1749bdd4c395913df18c57fdd42820d3de88738b7163b7aa254d13e3da7b1d9d'
  'libldap-2.7.1-1-x86_64.pkg.tar.zst|bc83324fccf39b11cad9b5d672db02eef9db7fa406d335169f7732c75ac7906f'
  'libnghttp2-1.70.0-1-x86_64.pkg.tar.zst|332e2cb2d953dab326f97a3257594f8031ec0fce6305e2bcd5d64d9832c84778'
  'libnghttp3-1.18.0-1-x86_64.pkg.tar.zst|4a474a6c8c2970bead13cbdf4ab47662039d43dcc48aa811ee58becd778cf890'
  'libngtcp2-1.25.0-1-x86_64.pkg.tar.zst|e4a728d9f647f3c52a3aedd67c589be5f43e7dd31ebde6867ad535ba29ec9511'
  'libogg-1.3.6-1-x86_64.pkg.tar.zst|b6d4724c1ed16b4806fa596cd823a2930efeeddeb95f7d8a869644b665a9ba37'
  'libp11-kit-0.26.5-1-x86_64.pkg.tar.zst|8ddc055b304a98c2434fa5d25550c94c0bbcdb22388f752e05e619b2ee98027c'
  'libpipewire-1:1.6.9-1-x86_64.pkg.tar.zst|f2bf96acd15f145a4a2ae37f73f9426cc67a3f215a4e74a1414b4f797f233cb3'
  'libpng-1.6.59-1-x86_64.pkg.tar.zst|c25e3f039aa598e65d01ee4404cb4a83b383b53b483a7b8627e391cc2c036de1'
  'libpsl-0.21.5-2-x86_64.pkg.tar.zst|e07925c2487bd7deffdd57808b19561e80a4cb25a33e51dbd94efb673bc81944'
  'libpulse-17.0+r98+gb096704c0-1-x86_64.pkg.tar.zst|79e91509611c537755a264673e6101975ef1aaf047f381be0be2e4d348ca48c6'
  'libsamplerate-0.2.2-3-x86_64.pkg.tar.zst|6641b016f15c73f90ee0741f0d14db1ea7ec988a2b8c74c5fce0182239bbbf00'
  'libsasl-2.1.28-5-x86_64.pkg.tar.zst|113e1676371544e5142a46ee046a6179ace201c4f0a0d56b618a984b5c764463'
  'libsndfile-1.2.2-4-x86_64.pkg.tar.zst|9b580a25a4d38f688c3561bacdd1d8c2d3451eee657f317220a38cffe41fa512'
  'libssh2-1.11.1-7-x86_64.pkg.tar.zst|462cca91bff394c2719b7e8d1951f0c0a2db979d71521ac93dba6b6b6974f6b9'
  'libstdc++-16.2.1+r23+gd564253eb6c8-1-x86_64.pkg.tar.zst|15dc6bd2f3a2ee17fcd79a14325e9e0037722a592b2bdc55e1665d92112eaa51'
  'libsysprof-capture-50.0-6-x86_64.pkg.tar.zst|4467ddc2c476f9a6249737ff1ef5714f4b376e55719968c168f3b8a86c098bbb'
  'libtasn1-4.21.0-1-x86_64.pkg.tar.zst|00e50489d3c00951380d578cf89ebd7d7a38d97269e7ca9ccc9cde146f1e21da'
  'libthai-0.1.30-1-x86_64.pkg.tar.zst|890798f30ac72d0ca3b59dc5fe8eebe8f923bf09bdb1bc2ec75bc200d7aa70a2'
  'libunistring-1.4.2-1-x86_64.pkg.tar.zst|582625bc02e710b587f4ed319e5c7ad29fd5d4d424b6384f125fc094f07ce590'
  'libverto-0.3.2-6-x86_64.pkg.tar.zst|d0965b1b256e330e300c2bc3c699afa24f2d800dc476541039d452b606309496'
  'libvorbis-1.3.7-4-x86_64.pkg.tar.zst|3aea2a31212fe7aa9f3d8922e8f71c2608d6260e8af0b7c1b5da790786763089'
  'libx11-1.8.13-1-x86_64.pkg.tar.zst|251f58e0a9bc2cd69b8e708f239914e48e3a91cd1822220806d273be873d026f'
  'libxau-1.0.12-1-x86_64.pkg.tar.zst|605c8b059c36792f4e0cc235acadf39d0762df6c7878825a1be01a00ae7ea21e'
  'libxcb-1.17.0-1-x86_64.pkg.tar.zst|2b2e7ac64b1d56c08a227c10bcab179605f2773f31db0a8c89f49f4e5b2f1292'
  'libxcursor-1.2.3-1-x86_64.pkg.tar.zst|2a90267877c2f5ffe6c41a8ef91e6def3b7720de7b4b2628b329373ce20c1b99'
  'libxdmcp-1.1.5-2-x86_64.pkg.tar.zst|623c957c2fd4b427a0f5a531da44931f9f66521391ee0bd0e635479947036b65'
  'libxext-1.3.7-1-x86_64.pkg.tar.zst|ac56905dc51bb652eca5f706fd7e7bb7ea81d4e057a236139fc769ce5ea10cf1'
  'libxfixes-6.0.2-1-x86_64.pkg.tar.zst|d58ab2dbf326e36cf84792fe0dc12c34239ea04ac34159006a566103203db272'
  'libxft-2.3.9-1-x86_64.pkg.tar.zst|a7841ed8e67dc3f94eca4672c8961329dfdd6b67836f8ac720a246d3aab02ecb'
  'libxi-1.8.3-1-x86_64.pkg.tar.zst|5ac5541f58978a3a5fab08c24f03da43200a7e9b25098e3e6da7bad7a0892cf0'
  'libxinerama-1.1.6-1-x86_64.pkg.tar.zst|7a91e2e649d9b24946b2b8088c3d7f70fa0673a96d3bae80497f403e57fb7654'
  'libxkbcommon-1.13.2-1-x86_64.pkg.tar.zst|18d8dc90958eda2edaab9b671ee96c2c1f2b1a322618eca97c7c04605d738581'
  'libxml2-2.15.4-1-x86_64.pkg.tar.zst|a6b5c30f51a7ca7c85348de8e83280e6825a8f3e193cc6916ecd8e695d4a24bb'
  'libxpresent-1.0.2-1-x86_64.pkg.tar.zst|78552cf8ae3218508dfd32317dd0d9bb99376266a372738bb4eaed4d745c41bc'
  'libxrandr-1.5.5-1-x86_64.pkg.tar.zst|49d3a3596311477f8ad2e1735dae612013118802822f21ce22886f65103dd899'
  'libxrender-0.9.12-1-x86_64.pkg.tar.zst|fed0389073d5b107074eaab48cefcc2716e607865142cde5b579c8ceeefea142'
  'libxss-1.2.5-1-x86_64.pkg.tar.zst|79fd8fc47e77f479e6e4229a6957f26a6dd549bac7e047bafecdcaf5fde58889'
  'linux-api-headers-7.2-1-x86_64.pkg.tar.zst|d8d3483363e70b353ae31bbf8773df77780724eaeaa140faf4e4111bdb87588f'
  'lmdb-0.9.35-1-x86_64.pkg.tar.zst|50ae8083860e1ffadf4fab3c173a043fd97be41f5ceda7d2e77c528cd84d2386'
  'lz4-1:1.10.0-2-x86_64.pkg.tar.zst|c6200c776440678fe8c26adae6c104194b425d9393a9e3fc09f934363f0c39a6'
  'lzo-2.10-5-x86_64.pkg.tar.zst|dd5ec871aea5ddfd38d4a25539e966dc05e75d6969ebc31917c8d25b97ccd69e'
  'mpg123-1.33.7-1-x86_64.pkg.tar.zst|af9bbea4f7c003f2c4b604430582d58b7b232c5adcda673d15401c3faf1943f2'
  'ncurses-6.6-2-x86_64.pkg.tar.zst|9b80390fd681121443a45a51b74e2e2ade245ce22af8769915d63165b727e27c'
  'nettle-4.0-1-x86_64.pkg.tar.zst|679138a8405ca383aba7836d54fdc282db9394b7dc23c097b8965f70119adf13'
  'openssl-3.6.4-1-x86_64.pkg.tar.zst|2a3744b5ea31fcb0ac50ca89a2d6a9886eadfb51b63d76bcbd7952794b03631c'
  'opus-1.6.1-1-x86_64.pkg.tar.zst|191b1f303950e184bdbe12105d8cb06e69e727e8f9160cfdcf33105f70d05b46'
  'pango-1:1.58.2-1-x86_64.pkg.tar.zst|9b74c73056ce6b2aca83bf1647f79f0a6bf53f204c5273b03530e034472db13c'
  'pcre2-10.49-1-x86_64.pkg.tar.zst|e37f4152ff869a6ba050ad08307d7b6b474f4d48cbe0c6ccc01ce9118a774dbf'
  'pixman-0.46.4-1-x86_64.pkg.tar.zst|e87e665d25a2230a178d8490202e1d677fde5908f68ebedf7d987308fc26fcb6'
  'readline-8.3.6-1-x86_64.pkg.tar.zst|016b2aeebfbe9dfb89283c52bb49487d95ea23a875d477da42d000a793613020'
  'spice-protocol-0.14.5-1-any.pkg.tar.zst|aa9df517c035390c648a98156f48831535534b0ce28d4714050f8a253cd6a5d4'
  'sqlite-3.53.4-1-x86_64.pkg.tar.zst|910e7e59acc3a51c7e3468bb45c7ca20d5e987eaf2c26e4e8ad9f0624bef76d2'
  'systemd-libs-262-1-x86_64.pkg.tar.zst|da81ea2710c37b493df9c8d670c9801b2fe691f06131aba27d1dacf07c522fe5'
  'util-linux-libs-2.42.4-1-x86_64.pkg.tar.zst|56075bc7da9be4470da36a040195f98aac0afbc1edcc7e4f27e6d267dcaf9f37'
  'wayland-1.26.0-1-x86_64.pkg.tar.zst|0bf83ec9dd5b9e4037e7fd7d5ded70838bb50f82aa43e8280c8c0025c92804a9'
  'wayland-protocols-1.49-1-any.pkg.tar.zst|79efa08167c423c6bdbba0efeb05ccc0ef9172c930be96a5b93f8208bdab04a4'
  'xcb-proto-1.17.0-4-any.pkg.tar.zst|98f661ef7c7e05eb7a687e859cf123a92806ceed8f55c0d70ddbac799988239a'
  'xorgproto-2025.1-1-any.pkg.tar.zst|f7bf3eed570618511fb53cc1bd32c2f1ee82e02662075436a59e6e50436f30de'
  'xz-5.8.4-1-x86_64.pkg.tar.zst|89b8e777c8f7a39413e1b223d4c212caec6ddd9470b3d1ea1d5161bcc6215c04'
  'zlib-1:1.3.2-3-x86_64.pkg.tar.zst|41cf0bb5df14e18f7fb868a97da3feb7c4127fba99bb332ad54b14322faac1b1'
  'zlib-ng-compat-2.3.3-1-x86_64.pkg.tar.zst|da8163c2fe78864cb476fcedbbd1bd1c4a9808c290e2b266b3cd10cbbf1a40c2'
  'zstd-1.5.7-3-x86_64.pkg.tar.zst|d4cf0049137124c8a025eedfad267a3e8a02310c9efb9d1ae4a61aa1d02789fc'
)
if [[ " ${pkgname[*]} " == *" obs-plugin-${pkgbase} "* ]]; then
  _target_packages+=(
    'obs-studio-32.2.2-1-x86_64.pkg.tar.zst|160a66705327d25c617965bc2abbd92101443072a186ab682ee3f39d6c49746b'
)
fi
noextract=()
for _target_package in "${_target_packages[@]}"; do
  _target_file=${_target_package%%|*}
  source+=("${_target_file}::https://archive.archlinux.org/packages/${_target_file:0:1}/${_target_file%%-[0-9]*}/${_target_file}")
  sha256sums+=("${_target_package#*|}")
  noextract+=("${_target_file}")
done
unset _target_package _target_file

_lgdir="${pkgbase}-${pkgver}"

prepare() {
	cd "${srcdir}/${_lgdir}"
	for patch in "${srcdir}"/*.patch; do
		patch -p1 < "${patch}"
	done
}

build() {
	local sysroot="${srcdir}/arch-x86_64-sysroot"
	local entry
	mkdir -p "${sysroot}"
	for entry in "${_target_packages[@]}"; do
		bsdtar -xf "${srcdir}/${entry%%|*}" -C "${sysroot}" \
			--exclude='.PKGINFO' --exclude='.BUILDINFO' \
			--exclude='.MTREE' --exclude='.INSTALL'
	done

	local tool_prefix=
	if [[ "$(uname -m)" != "${CARCH}" ]]; then
		tool_prefix=x86_64-linux-gnu-
	fi

	# Find headers/libraries only in Arch; executable generators only on host.
	cat > "${srcdir}/x86_64-arch.cmake" <<EOF
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER ${tool_prefix}gcc)
set(CMAKE_CXX_COMPILER ${tool_prefix}g++)
set(CMAKE_LINKER ${tool_prefix}ld)
set(CMAKE_AR ${tool_prefix}ar)
set(CMAKE_RANLIB ${tool_prefix}ranlib)
set(CMAKE_OBJCOPY ${tool_prefix}objcopy)
set(CMAKE_SYSROOT "${sysroot}")
set(CMAKE_FIND_ROOT_PATH "${sysroot}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(CMAKE_C_FLAGS_INIT "-B${sysroot}/usr/lib/")
set(CMAKE_CXX_FLAGS_INIT "-B${sysroot}/usr/lib/")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-L${sysroot}/usr/lib -Wl,-rpath-link,${sysroot}/usr/lib")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-L${sysroot}/usr/lib -Wl,-rpath-link,${sysroot}/usr/lib")
set(CMAKE_SKIP_RPATH ON)
EOF
	export PKG_CONFIG_SYSROOT_DIR="${sysroot}"
	export PKG_CONFIG_LIBDIR="${sysroot}/usr/lib/pkgconfig:${sysroot}/usr/share/pkgconfig"
	unset PKG_CONFIG_PATH

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
			-DCMAKE_TOOLCHAIN_FILE="${srcdir}/x86_64-arch.cmake" \
			-DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib \
			-DOPTIMIZE_FOR_NATIVE=OFF \
			-DWAYLAND_SCANNER_EXECUTABLE="$(command -v wayland-scanner)"
		cmake --build "${srcdir}/${_lgdir}/${component}/build" --parallel "$(nproc)"
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
}

package_looking-glass-module-dkms() {
	pkgdesc="A kernel module that implements a basic interface to the IVSHMEM device for when using LookingGlass in VM->VM mode"
	depends=('dkms')

	cd "${srcdir}/${_lgdir}/module"
	install -Dm644 -t "${pkgdir}/usr/src/${pkgbase}-${pkgver}" \
		Makefile \
		dkms.conf \
		kvmfr.{h,c}
}

#package_looking-glass-host() {
#	pkgdesc="Linux host application for pushing frame data to the LookingGlass IVSHMEM device"
#	depends=('binutils' 'gcc-libs' 'glib2' 'glibc'
#	         'libpipewire' 'libxcb' 'zlib' 'zstd')
#	install="host.install"
#
#	cd "${srcdir}/${_lgdir}/host/build"
#	make DESTDIR="${pkgdir}" install
#}

package_obs-plugin-looking-glass() {
	pkgdesc="Plugin for OBS Studio to stream directly from Looking Glass without having to record the Looking Glass client"
	depends=('glibc' 'obs-studio')

	install -Dm644 -t "${pkgdir}/usr/lib/obs-plugins" \
		"${srcdir}/${_lgdir}/obs/build/liblooking-glass-obs.so"
}
