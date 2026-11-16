#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# Build "Heuretes FreeCAD.app" from the working tree and install it in /Applications (roadmap FND-02).
#
# Heuretes FreeCAD (powered by FreeCAD) is this FreeCAD fork, fully renamed: menu bar and Dock say "Heuretes FreeCAD";
# the splash, About box, window title and Get Info say "Heuretes FreeCAD". It has its own
# settings folders (~/Library/Preferences/Heuretes FreeCAD, ~/Library/Application Support/Heuretes FreeCAD), so it runs
# beside the development build (build/release) without sharing or touching its settings. The bundle is
# self-contained: the Python, Qt, OCCT, CalculiX, Tectonic and other libraries it needs are copied from the pixi environment
# and every path into the environment or the build tree is rewritten, so it keeps working after a
# rebuild or `pixi clean`. Built from whatever is checked out, uncommitted changes included; the version
# shows the commit and "-dirty". Ad-hoc signed, not notarized: for this Mac.
#
# On first launch the bundle's launcher (Contents/MacOS/Heuretes FreeCAD) copies the old Keystone settings folders
# into the Heuretes FreeCAD ones when those do not exist yet, and rewrites the absolute paths in the copied *.cfg.
#
# Usage: package/bruceworx/make-heuretes.sh [--no-build] [--dest DIR] [--dmg]
#   --no-build  use build/release as it is (skip the ninja step)
#   --dest DIR  install into DIR instead of /Applications
#   --dmg       also write dist/Heuretes-FreeCAD-<version>-macOS-arm64.dmg
# Claude's MCP server finds the Heuretes FreeCAD bridge with
#   BW_BRIDGE_DIR="$HOME/Library/Application Support/Heuretes FreeCAD/v1-1/BruceWorx"
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
env="$repo/.pixi/envs/default"
build="$repo/build/release"
dest="/Applications"
app_name="Heuretes FreeCAD"
display_name="Heuretes FreeCAD"
bundle_id="com.heuretes.freecad"
do_build=1
do_dmg=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) do_build=0 ;;
        --dest) dest="$2"; shift ;;
        --dmg) do_dmg=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "make-heuretes: unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

say() { printf '\033[1m[heuretes]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[heuretes] %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || fail "needs macOS on Apple silicon"
[[ -d "$env/lib" ]] || fail "no pixi environment at $env (run 'pixi install' in the repo)"
[[ -f "$build/build.ninja" ]] || fail "no configured build at $build (run 'pixi run configure-release')"
for tool in otool install_name_tool codesign sips iconutil python3 rsync cc; do
    command -v "$tool" >/dev/null || fail "missing $tool (Xcode command line tools)"
done

# ----- version: the release in CMakeLists.txt, the commit, and -dirty for uncommitted work ------------
ver() { sed -n "s/^set(PACKAGE_VERSION_$1 \"\([^\"]*\)\".*/\1/p" "$repo/CMakeLists.txt" | head -1; }
release="$(ver MAJOR).$(ver MINOR).$(ver PATCH)$(ver SUFFIX)"
commit="$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo unknown)"
dirty=""
git -C "$repo" diff --quiet HEAD -- 2>/dev/null || dirty="-dirty"
version="$release+$commit$dirty"
short="$(ver MAJOR).$(ver MINOR).$(ver PATCH)"
say "Heuretes FreeCAD $version"

# ----- 1. build ---------------------------------------------------------------------------------------
if [[ $do_build == 1 ]]; then
    say "building build/release"
    PATH="$env/bin:$PATH" cmake --build "$build"
fi

# ----- 2. stage the bundle: FreeCAD installed into Contents, the runtime copied from the environment -----
stage="$(mktemp -d "${TMPDIR:-/tmp}/heuretes-freecad.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
app="$stage/$app_name.app"
C="$app/Contents"
mkdir -p "$C/MacOS" "$C/Resources"
say "installing into the bundle"
PATH="$env/bin:$PATH" cmake --install "$build" --prefix "$C" >"$stage/install.log" 2>&1 \
    || { tail -20 "$stage/install.log"; fail "cmake --install failed"; }
rm -rf "$C/include" "$C/lib/cmake" "$C/lib/pkgconfig"

say "copying Python, Qt and the rest of the runtime"
mkdir -p "$C/lib" "$C/share" "$C/etc" "$C/bin"
# Python's standard library and packages; not conda, stubs, tests or caches.
rsync -a --delete \
    --exclude '__pycache__' --exclude 'test/' --exclude 'tests/' --exclude 'idlelib/' --exclude 'tkinter/' \
    --exclude 'site-packages/conda*' --exclude 'site-packages/*-stubs' --exclude 'site-packages/mamba*' \
    --exclude 'site-packages/libmambapy*' --exclude 'site-packages/debugpy*' --exclude 'site-packages/pip/_vendor/rich/*.pyi' \
    "$env/lib/python3.11" "$C/lib/"
rsync -a "$env/lib/qt6/plugins" "$C/lib/qt6/"
rsync -a "$env/lib/qt6/qml" "$C/lib/qt6/"
[[ -d "$env/share/opencascade" ]] && rsync -a "$env/share/opencascade" "$C/share/"
[[ -d "$env/ssl" ]] && rsync -a "$env/ssl" "$C/"
cp -p "$env/bin/python3.11" "$C/bin/python3.11"
ln -sf python3.11 "$C/bin/python3"
ln -sf python3.11 "$C/bin/python"

# CalculiX for the Simulation workspace (SIM-01): ccx and, through the relocation step below, its
# ARPACK, LAPACK, BLAS, gfortran and OpenMP dylibs. BruceWorx's Init.py points the FEM preference
# Mod/Fem/Ccx/ccxBinaryPath at Contents/bin/ccx on a profile that has none set.
if [[ -x "$env/bin/ccx" ]]; then
    cp -p "$env/bin/ccx" "$C/bin/ccx"
else
    say "warning: no ccx in the environment (pixi.toml lists calculix): the Simulation workspace cannot solve"
fi
# Gmsh (SIM-04, 1 Nov): the second volume mesher, tried when Netgen fails or times out (a study can also pick it).
# The official self-contained SDK build from gmsh.info (GPL v2+, run as a separate program, like OpenFOAM), checked
# against a pinned SHA-256, copied to Contents/gmsh/{bin,lib,share/doc}: gmsh finds libgmsh through
# @executable_path/../lib, so nothing is installed into the conda environment and no existing package changes.
# bruceworx_sim_meshworker.gmsh_binary looks for Contents/gmsh/bin/gmsh.
gmsh_ver=4.15.2
gmsh_sha=2ebbc7c241a1681fc21e1bd32fe7f6637449bf407dbd642cf4f8751d56d46b25
gmsh_tgz="${HEU_CACHE:-${VL_CACHE:-$HOME/Library/Caches/Heuretes}}/gmsh-$gmsh_ver-MacOSARM-sdk.tgz"
if [[ ! -f "$gmsh_tgz" && -f "$HOME/Library/Caches/VectorLabs/gmsh-$gmsh_ver-MacOSARM-sdk.tgz" ]]; then
    gmsh_tgz="$HOME/Library/Caches/VectorLabs/gmsh-$gmsh_ver-MacOSARM-sdk.tgz"
fi
if [[ ! -f "$gmsh_tgz" ]]; then
    mkdir -p "$(dirname "$gmsh_tgz")"
    curl -fsSL -o "$gmsh_tgz" "https://gmsh.info/bin/macOS/gmsh-$gmsh_ver-MacOSARM-sdk.tgz" || say "warning: could not download Gmsh"
fi
if [[ -f "$gmsh_tgz" ]] && [[ "$(shasum -a 256 "$gmsh_tgz" | cut -d' ' -f1)" == "$gmsh_sha" ]]; then
    gmsh_tmp="$(mktemp -d)"
    tar xzf "$gmsh_tgz" -C "$gmsh_tmp"
    gmsh_src="$gmsh_tmp/gmsh-$gmsh_ver-MacOSARM-sdk"
    mkdir -p "$C/gmsh/bin" "$C/gmsh/lib" "$C/gmsh/share/doc"
    cp -p "$gmsh_src/bin/gmsh" "$C/gmsh/bin/gmsh"
    cp -p "$gmsh_src/lib/libgmsh.$gmsh_ver.dylib" "$gmsh_src/lib/libgmsh.${gmsh_ver%.*}.dylib" "$C/gmsh/lib/" 2>/dev/null \
        || cp -a "$gmsh_src"/lib/libgmsh*.dylib "$C/gmsh/lib/"
    rsync -a "$gmsh_src/share/doc/gmsh" "$C/gmsh/share/doc/"
    rm -rf "$gmsh_tmp"
else
    say "warning: no verified Gmsh archive: the Gmsh mesher is not bundled (checksum $gmsh_sha expected)"
fi
# Tectonic compiles a study's LaTeX report (SIM-01, 30 Oct); its ICU, HarfBuzz, graphite2, FreeType,
# libpng, zlib and iconv dylibs come along through the relocation step. bruceworx_sim.find_tex looks
# for Contents/bin/tectonic. Its TeX files are pre-seeded into Contents/Resources/tectonic-cache below
# (after the bundled Tectonic is checked), so the first report works offline too.
if [[ -x "$env/bin/tectonic" ]]; then
    cp -p "$env/bin/tectonic" "$C/bin/tectonic"
else
    say "warning: no tectonic in the environment (pixi.toml lists it): study reports stay LaTeX unless MacTeX is installed"
fi

# Code-CAD (AUT-05): build123d and CadQuery come with site-packages above, and OCP's libraries through the
# relocation step. lib3mf is the exception: build123d's lib3mf package loads it by name at import
# (ctypes.util.find_library, which looks in Contents/lib), so nothing links to it and it is copied here.
if compgen -G "$env/lib/lib3mf*.dylib" >/dev/null; then
    cp -a "$env"/lib/lib3mf*.dylib "$C/lib/"
else
    say "warning: no lib3mf in the environment (pixi.toml lists build123d): code parts cannot run"
fi

# The real executable sits in Contents/MacOS (as "heuretes-freecad"), so FreeCAD takes its home from the
# executable's parent folder, Contents. macOS sees "Heuretes FreeCAD.app" (menu bar, Dock, icon) through the
# bundle's Info.plist; the bundle's own executable, MacOS/"Heuretes FreeCAD", is a small launcher that first
# brings over the old Keystone settings (first launch only), then execs the real one.
# The launcher must be a Mach-O: Launch Services refuses a bundle whose executable is a shell script
# (open fails with -54), so a tiny C program runs Resources/heuretes-migrate.sh and then execs heuretes-freecad.
cp -p "$C/bin/FreeCAD" "$C/MacOS/heuretes-freecad"
cat > "$C/Resources/heuretes-migrate.sh" <<'MIGRATE'
#!/bin/bash
# Heuretes FreeCAD first launch: copy Vector Labs settings if present, else Keystone.
# The old folders stay. ExeName in branding.xml picks the new folders.
if [ -z "${FREECAD_USER_HOME:-}" ] && [ -n "${HOME:-}" ]; then
    for kind in "Library/Preferences" "Library/Application Support"; do
        new="$HOME/$kind/Heuretes FreeCAD"
        if [ -e "$new" ]; then
            continue
        fi
        old=""
        if [ -d "$HOME/$kind/Vector Labs" ]; then
            old="$HOME/$kind/Vector Labs"
        elif [ -d "$HOME/$kind/Keystone" ]; then
            old="$HOME/$kind/Keystone"
        fi
        if [ -n "$old" ] && /usr/bin/ditto "$old" "$new" 2>/dev/null; then
            /usr/bin/find "$new" -name '*.cfg' -type f -print0 | while IFS= read -r -d '' f; do
                /usr/bin/sed -i '' \
                    -e 's#Application Support/Keystone#Application Support/Heuretes FreeCAD#g' \
                    -e 's#Application Support/Vector Labs#Application Support/Heuretes FreeCAD#g' \
                    -e 's#Library/Preferences/Keystone#Library/Preferences/Heuretes FreeCAD#g' \
                    -e 's#Library/Preferences/Vector Labs#Library/Preferences/Heuretes FreeCAD#g' \
                    -e 's#Library/Caches/Keystone#Library/Caches/Heuretes FreeCAD#g' \
                    -e 's#Library/Caches/Vector Labs#Library/Caches/Heuretes FreeCAD#g' \
                    -e 's#/Keystone\.app/#/Heuretes FreeCAD.app/#g' \
                    -e 's#/Vector Labs\.app/#/Heuretes FreeCAD.app/#g' "$f"
            done
        else
            rm -rf "$new"
        fi
    done
fi
MIGRATE
chmod +x "$C/Resources/heuretes-migrate.sh"
cat > "$stage/heuretes-launcher.c" <<'LAUNCHER'
/* Heuretes FreeCAD launcher: point Python, fonts, certificates and Qt plugins at this bundle, run
   Resources/heuretes-migrate.sh (first-launch settings copy), then exec MacOS/heuretes-freecad with the same
   arguments. Symlinks to this file (MacOS/Keystone) resolve to the bundle. The paths are set here rather than
   in Info.plist so the bundle keeps working after it is moved (it is staged in another folder first). */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char exe[PATH_MAX], dir[PATH_MAX], path[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, dir)) {
        fprintf(stderr, "Heuretes FreeCAD: cannot find its own executable\n");
        return 1;
    }
    char *slash = strrchr(dir, '/');
    if (slash) *slash = '\0';
    char contents[PATH_MAX];
    snprintf(path, sizeof path, "%s/..", dir);
    if (!realpath(path, contents)) {
        fprintf(stderr, "Heuretes FreeCAD: cannot find its Contents folder\n");
        return 1;
    }
    static const char *const env[][2] = {
        {"PYTHONHOME", ""},
        {"FONTCONFIG_FILE", "/etc/fonts.conf"},
        {"SSL_CERT_FILE", "/ssl/cacert.pem"},
        {"QT_PLUGIN_PATH", "/lib/qt6/plugins"},
    };
    for (size_t i = 0; i < sizeof env / sizeof env[0]; i++) {
        snprintf(path, sizeof path, "%s%s", contents, env[i][1]);
        setenv(env[i][0], path, 1);
    }
    setenv("PYTHONNOUSERSITE", "1", 1);
    snprintf(path, sizeof path, "%s/../Resources/heuretes-migrate.sh", dir);
    if (access(path, R_OK) == 0) {
        pid_t pid = fork();
        if (pid == 0) {
            execl("/bin/bash", "bash", path, (char *)NULL);
            _exit(127);
        }
        if (pid > 0) waitpid(pid, NULL, 0);
    }
    snprintf(path, sizeof path, "%s/heuretes-freecad", dir);
    argv[0] = path;
    execv(path, argv);
    perror(path);
    return 1;
}
LAUNCHER
cc -O2 -Wall -mmacosx-version-min=12.0 -o "$C/MacOS/$app_name" "$stage/heuretes-launcher.c" \
    || fail "the launcher did not compile"
# Temporary: old scripts and watchers still start MacOS/Keystone (/Applications/Keystone.app is a link to this app).
ln -sf "$app_name" "$C/MacOS/Keystone"
ln -sf "$app_name" "$C/MacOS/Vector Labs"

# Qt finds its plugins from qt.conf, which Qt reads from Contents/Resources in a bundle.
# Prefix is relative (to the executable's folder, MacOS or bin), so the bundle still works after it is moved:
# the swap-on-quit stages it in another folder before moving it to /Applications.
cat > "$C/Resources/qt.conf" <<QTCONF
[Paths]
Prefix = ..
Libraries = lib
Plugins = lib/qt6/plugins
QmlImports = lib/qt6/qml
QTCONF
cp "$C/Resources/qt.conf" "$C/bin/qt6.conf"

# Fonts from the system, with the cache outside the bundle.
cat > "$C/etc/fonts.conf" <<FONTS
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>/System/Library/Fonts</dir>
  <dir>/System/Library/Fonts/Supplemental</dir>
  <dir>/Library/Fonts</dir>
  <dir>~/Library/Fonts</dir>
  <cachedir>~/Library/Caches/Heuretes FreeCAD/fontconfig</cachedir>
</fontconfig>
FONTS

# ----- 3. the name: branding.xml renames the application and moves its settings ---------------------
say "branding: Heuretes FreeCAD, its own settings folders"
mkdir -p "$C/share/heuretes"
cat > "$C/bin/branding.xml" <<BRAND
<?xml version="1.0" encoding="utf-8"?>
<!-- Heuretes FreeCAD.app branding (written by make-heuretes.sh): the name everywhere, and ExeName moves the
     settings to ~/Library/Preferences/Heuretes FreeCAD and ~/Library/Application Support/Heuretes FreeCAD. -->
<Branding>
  <Application>$app_name</Application>
  <WindowTitle>$display_name</WindowTitle>
  <ExeName>$app_name</ExeName>
  <ExeVendor>Heuretes</ExeVendor>
  <AppDataSkipVendor>true</AppDataSkipVendor>
  <BuildVersionSuffix>$(ver SUFFIX)+$commit$dirty</BuildVersionSuffix>
  <WindowIcon>share/heuretes/heuretes.png</WindowIcon>
  <AboutImage>share/heuretes/heuretes_about.png</AboutImage>
  <CopyrightInfo>Heuretes FreeCAD. FreeCAD is LGPL-2.1-or-later software by the FreeCAD contributors.</CopyrightInfo>
  <SplashScreen>heuretes_splash</SplashScreen>
  <SplashInfoColor>#00000000</SplashInfoColor>
  <SplashAlignment>Bottom|Left</SplashAlignment>
  <SplashTextColor>#8a97a8</SplashTextColor>
</Branding>
BRAND

# ----- 4. the icon -------------------------------------------------------------------------------------
say "icon"
png="$here/branding/heuretes_icon_1024.png"
icns="$here/branding/heuretes-freecad.icns"
[[ -f "$png" ]] || fail "missing $png"
mkdir -p "$C/share/heuretes"
if [[ -f "$icns" ]]; then
    cp "$icns" "$C/Resources/$app_name.icns"
else
    iconset="$stage/Heuretes.iconset"
    mkdir -p "$iconset"
    for s in 16 32 128 256 512; do
        sips -z $s $s "$png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
        sips -z $((s * 2)) $((s * 2)) "$png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$iconset" -o "$C/Resources/$app_name.icns"
fi
sips -z 256 256 "$png" --out "$C/share/heuretes/heuretes.png" >/dev/null
cp "$here/branding/heuretes_splash_2x.png" "$C/share/heuretes/heuretes_about.png"

# ----- 5. Info.plist -----------------------------------------------------------------------------------
cat > "$C/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$app_name</string>
  <key>CFBundleDisplayName</key><string>$app_name</string>
  <key>CFBundleIdentifier</key><string>$bundle_id</string>
  <key>CFBundleExecutable</key><string>$app_name</string>
  <key>CFBundleIconFile</key><string>$app_name</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$short</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>CFBundleGetInfoString</key><string>$display_name $version</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSRequiresAquaSystemAppearance</key><false/>
  <key>CFBundleDocumentTypes</key><array><dict>
    <key>CFBundleTypeName</key><string>FreeCAD Document</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Alternate</string>
    <key>CFBundleTypeExtensions</key><array><string>FCStd</string><string>fcstd</string></array>
  </dict><dict>
    <key>CFBundleTypeName</key><string>Heuretes FreeCAD Simulation</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Owner</string>
    <key>CFBundleTypeExtensions</key><array><string>vlsim</string></array>
  </dict></array>
</dict></plist>
PLIST

# ----- 6. self-contained: copy the libraries it needs, rewrite every path into the environment ---------
say "relocating libraries (a few minutes)"
python3 "$here/heuretes-freecad_relocate.py" "$C" "$env" "$build" "$repo" \
    || fail "some files still point into the environment or the build tree (listed above)"
# Python and the pyvenv-less interpreter look for their home through PYTHONHOME (set by the launcher above).

# ----- 7. check it starts, then install -----------------------------------------------------------------
say "checking the staged bundle"
# A throwaway home: the check must not write the real Heuretes FreeCAD settings (it once applied the theme headless
# and left the staging folder in them).
mkdir -p "$stage/home"
FREECAD_USER_HOME="$stage/home" PYTHONHOME="$C" PYTHONNOUSERSITE=1 "$C/bin/FreeCADCmd" -c "import FreeCAD, Part, sys; print('ok', FreeCAD.Version()[0:3], sys.prefix)" \
    >"$stage/check.log" 2>&1 || { cat "$stage/check.log"; fail "the staged FreeCADCmd does not start"; }
grep -q "^ok" "$stage/check.log" || { cat "$stage/check.log"; fail "the staged FreeCADCmd did not import Part"; }
say "$(grep '^ok' "$stage/check.log")"
codecad_out="$("$C/bin/FreeCADCmd" -c "import build123d, cadquery; print('code-CAD ok: build123d', build123d.__version__)" 2>&1 | grep '^code-CAD ok' || true)"
if [[ -n "$codecad_out" ]]; then say "$codecad_out"; else say "warning: build123d does not import in the bundle: code parts cannot run"; fi
if [[ -x "$C/bin/ccx" ]]; then
    # ccx -v exits 201 after printing its version, so judge it by the output, not the status (pipefail).
    ccx_out="$("$C/bin/ccx" -v 2>&1 || true)"
    [[ "$ccx_out" == *Version* ]] || fail "the bundled CalculiX does not start (its dylibs were not relocated?)"
    say "CalculiX $(grep -o 'Version [0-9.]*' <<<"$ccx_out")"
fi
if [[ -x "$C/gmsh/bin/gmsh" ]]; then
    gmsh_out="$("$C/gmsh/bin/gmsh" -version 2>&1 || true)"
    [[ "$gmsh_out" == "$gmsh_ver"* ]] || fail "the bundled Gmsh does not start: $gmsh_out"
    say "Gmsh $gmsh_out"
fi
if [[ -x "$C/bin/tectonic" ]]; then
    tectonic_out="$(env -i HOME="$HOME" "$C/bin/tectonic" --version 2>&1 || true)"
    # (0.15 prints its version twice, "tectonic 0.15.0Tectonic 0.15.0")
    [[ "$tectonic_out" == *"Tectonic "* ]] || fail "the bundled Tectonic does not start (its dylibs were not relocated?): $tectonic_out"
    say "$(grep -o 'Tectonic [0-9.]*' <<<"$tectonic_out")"
    # Pre-seed Tectonic's cache (30 Oct): compile tectonic-seed/seed.tex once (it loads every package the
    # study reports use, in every size and shape) with TECTONIC_CACHE_DIR inside the bundle. This run needs
    # the network (about 16 MB). At run time bruceworx_sim.tectonic_cache copies the seed once into
    # ~/Library/Caches/Heuretes FreeCAD/tectonic (the bundle stays read-only) and points TECTONIC_CACHE_DIR there.
    seed="$C/Resources/tectonic-cache"
    rm -rf "$seed" "$stage/texseed"
    mkdir -p "$seed" "$stage/texseed"
    cp "$here/tectonic-seed/seed.tex" "$stage/texseed/seed.tex"
    cp "$here/branding/heuretes_splash_2x.png" "$stage/texseed/seed.png"
    if (cd "$stage/texseed" && env -i HOME="$HOME" PATH=/usr/bin:/bin TECTONIC_CACHE_DIR="$seed" \
            "$C/bin/tectonic" --chatter minimal seed.tex) >"$stage/texseed.log" 2>&1 \
       && (cd "$stage/texseed" && env -i HOME="$HOME" PATH=/usr/bin:/bin TECTONIC_CACHE_DIR="$seed" \
            "$C/bin/tectonic" --only-cached --chatter minimal seed.tex) >>"$stage/texseed.log" 2>&1; then
        say "Tectonic cache seeded for offline reports ($(du -sh "$seed" | cut -f1))"
    else
        tail -5 "$stage/texseed.log"
        rm -rf "$seed"
        say "warning: the Tectonic cache could not be seeded (no network?): the first report downloads its TeX files"
    fi
fi

target="$dest/$app_name.app"
if [[ -e "$target" ]]; then
    old_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$target/Contents/Info.plist" 2>/dev/null || true)"
    [[ "$old_id" == "$bundle_id" ]] || fail "$target exists and is not Heuretes FreeCAD ($old_id): not replacing it"
    say "replacing the Heuretes FreeCAD.app installed before"
    rm -rf "$target"
fi
mkdir -p "$dest"
ditto "$app" "$target"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$target" || true
size="$(du -sh "$target" | cut -f1)"
say "installed $target ($size)"

if [[ $do_dmg == 1 ]]; then
    mkdir -p "$repo/dist"
    dmg="$repo/dist/Heuretes-FreeCAD-$release+$commit$dirty-macOS-arm64.dmg"
    rm -f "$dmg"
    ln -s /Applications "$stage/Applications"
    hdiutil create -volname "$app_name $release" -srcfolder "$app" -srcfolder "$stage/Applications" -ov -format UDZO "$dmg" >/dev/null
    say "wrote $dmg"
fi
say "open it from Applications, or: open -a \"$app_name\""
