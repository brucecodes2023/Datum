#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# Build "Vector Labs.app" from the working tree and install it in /Applications (roadmap FND-02).
#
# Vector Labs (powered by FreeCAD) is this FreeCAD fork, fully renamed: menu bar and Dock say "Vector Labs";
# the splash, About box, window title and Get Info say "Vector Labs, powered by FreeCAD". It has its own
# settings folders (~/Library/Preferences/Vector Labs, ~/Library/Application Support/Vector Labs), so it runs
# beside the development build (build/release) without sharing or touching its settings. The bundle is
# self-contained: the Python, Qt, OCCT, CalculiX, Tectonic and other libraries it needs are copied from the pixi environment
# and every path into the environment or the build tree is rewritten, so it keeps working after a
# rebuild or `pixi clean`. Built from whatever is checked out, uncommitted changes included; the version
# shows the commit and "-dirty". Ad-hoc signed, not notarized: for this Mac.
#
# On first launch the bundle's launcher (Contents/MacOS/Vector Labs) copies the old Keystone settings folders
# into the Vector Labs ones when those do not exist yet, and rewrites the absolute paths in the copied *.cfg.
#
# Usage: package/bruceworx/make-vectorlabs.sh [--no-build] [--dest DIR] [--dmg]
#   --no-build  use build/release as it is (skip the ninja step)
#   --dest DIR  install into DIR instead of /Applications
#   --dmg       also write dist/Vector-Labs-<version>-macOS-arm64.dmg
# Claude's MCP server finds the Vector Labs bridge with
#   BW_BRIDGE_DIR="$HOME/Library/Application Support/Vector Labs/v1-1/BruceWorx"
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
env="$repo/.pixi/envs/default"
build="$repo/build/release"
dest="/Applications"
app_name="Vector Labs"
display_name="Vector Labs, powered by FreeCAD"
bundle_id="com.vectorlabs.app"
do_build=1
do_dmg=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) do_build=0 ;;
        --dest) dest="$2"; shift ;;
        --dmg) do_dmg=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "make-vectorlabs: unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

say() { printf '\033[1m[vectorlabs]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[vectorlabs] %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || fail "needs macOS on Apple silicon"
[[ -d "$env/lib" ]] || fail "no pixi environment at $env (run 'pixi install' in the repo)"
[[ -f "$build/build.ninja" ]] || fail "no configured build at $build (run 'pixi run configure-release')"
for tool in otool install_name_tool codesign sips iconutil python3 rsync; do
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
say "Vector Labs $version"

# ----- 1. build ---------------------------------------------------------------------------------------
if [[ $do_build == 1 ]]; then
    say "building build/release"
    PATH="$env/bin:$PATH" cmake --build "$build"
fi

# ----- 2. stage the bundle: FreeCAD installed into Contents, the runtime copied from the environment -----
stage="$(mktemp -d "${TMPDIR:-/tmp}/vectorlabs.XXXXXX")"
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
# Tectonic compiles a study's LaTeX report (SIM-01, 30 Oct); its ICU, HarfBuzz, graphite2, FreeType,
# libpng, zlib and iconv dylibs come along through the relocation step. bruceworx_sim.find_tex looks
# for Contents/bin/tectonic. Its TeX files are pre-seeded into Contents/Resources/tectonic-cache below
# (after the bundled Tectonic is checked), so the first report works offline too.
if [[ -x "$env/bin/tectonic" ]]; then
    cp -p "$env/bin/tectonic" "$C/bin/tectonic"
else
    say "warning: no tectonic in the environment (pixi.toml lists it): study reports stay LaTeX unless MacTeX is installed"
fi

# The real executable sits in Contents/MacOS (as "vectorlabs"), so FreeCAD takes its home from the
# executable's parent folder, Contents. macOS sees "Vector Labs.app" (menu bar, Dock, icon) through the
# bundle's Info.plist; the bundle's own executable, MacOS/"Vector Labs", is a small launcher that first
# brings over the old Keystone settings (first launch only), then execs the real one.
cp -p "$C/bin/FreeCAD" "$C/MacOS/vectorlabs"
cat > "$C/MacOS/$app_name" <<'LAUNCHER'
#!/bin/bash
# Vector Labs launcher. First launch: copy the Keystone settings folders into the Vector Labs ones
# (the Keystone folders stay as a backup) and rewrite the absolute paths inside the copied *.cfg files.
# Whatever name started this (also the temporary MacOS/Keystone link), the Vector Labs folders are used:
# the branding.xml ExeName decides the folders, not the name of this file.
here="$(cd "$(dirname "$0")" && pwd)"
if [ -z "${FREECAD_USER_HOME:-}" ] && [ -n "${HOME:-}" ]; then
    for kind in "Library/Preferences" "Library/Application Support"; do
        old="$HOME/$kind/Keystone"
        new="$HOME/$kind/Vector Labs"
        if [ -d "$old" ] && [ ! -e "$new" ]; then
            if /usr/bin/ditto "$old" "$new" 2>/dev/null; then
                /usr/bin/find "$new" -name '*.cfg' -type f -print0 | while IFS= read -r -d '' f; do
                    /usr/bin/sed -i ''                         -e 's#Application Support/Keystone#Application Support/Vector Labs#g'                         -e 's#Library/Preferences/Keystone#Library/Preferences/Vector Labs#g'                         -e 's#Library/Caches/Keystone#Library/Caches/Vector Labs#g'                         -e 's#/Keystone\.app/#/Vector Labs.app/#g' "$f"
                done
            else
                rm -rf "$new"   # a half copy would hide the backup from the next start
            fi
        fi
    done
fi
exec "$here/vectorlabs" "$@"
LAUNCHER
chmod +x "$C/MacOS/$app_name"
# Temporary: old scripts and watchers still start MacOS/Keystone (/Applications/Keystone.app is a link to this app).
ln -sf "$app_name" "$C/MacOS/Keystone"

# Qt finds its plugins from qt.conf, which Qt reads from Contents/Resources in a bundle.
cat > "$C/Resources/qt.conf" <<QTCONF
[Paths]
Prefix = $dest/$app_name.app/Contents
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
  <cachedir>~/Library/Caches/Vector Labs/fontconfig</cachedir>
</fontconfig>
FONTS

# ----- 3. the name: branding.xml renames the application and moves its settings ---------------------
say "branding: Vector Labs, its own settings folders"
mkdir -p "$C/share/vectorlabs"
cat > "$C/bin/branding.xml" <<BRAND
<?xml version="1.0" encoding="utf-8"?>
<!-- Vector Labs.app branding (written by make-vectorlabs.sh): the name everywhere, and ExeName moves the
     settings to ~/Library/Preferences/Vector Labs and ~/Library/Application Support/Vector Labs. -->
<Branding>
  <Application>$app_name</Application>
  <WindowTitle>$display_name</WindowTitle>
  <ExeName>$app_name</ExeName>
  <ExeVendor>BruceWorx</ExeVendor>
  <AppDataSkipVendor>true</AppDataSkipVendor>
  <BuildVersionSuffix>$(ver SUFFIX)+$commit$dirty</BuildVersionSuffix>
  <WindowIcon>share/vectorlabs/vectorlabs.png</WindowIcon>
  <AboutImage>share/vectorlabs/vectorlabs_about.png</AboutImage>
  <CopyrightInfo>Vector Labs, powered by FreeCAD. FreeCAD is LGPL-2.1-or-later software by the FreeCAD contributors.</CopyrightInfo>
  <SplashScreen>vectorlabs_splash</SplashScreen>
  <SplashInfoColor>#00000000</SplashInfoColor>
  <SplashAlignment>Bottom|Left</SplashAlignment>
  <SplashTextColor>#8a97a8</SplashTextColor>
</Branding>
BRAND

# ----- 4. the icon -------------------------------------------------------------------------------------
say "icon"
chrome="$(ls -d "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell 2>/dev/null | tail -1 || true)"
png="$stage/icon_1024.png"
if [[ -n "$chrome" ]]; then
    "$chrome" --headless --hide-scrollbars --default-background-color=00000000 --window-size=1024,1024 \
        --virtual-time-budget=1500 --screenshot="$png" "file://$here/branding/icon.html" >/dev/null 2>&1 || true
fi
[[ -f "$png" ]] || png="$here/branding/vectorlabs_icon_1024.png"
[[ -f "$png" ]] || fail "no icon: install Playwright's headless Chrome or add branding/vectorlabs_icon_1024.png"
iconset="$stage/VectorLabs.iconset"
mkdir -p "$iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$C/Resources/$app_name.icns"
sips -z 256 256 "$png" --out "$C/share/vectorlabs/vectorlabs.png" >/dev/null
cp "$here/branding/vectorlabs_splash_2x.png" "$C/share/vectorlabs/vectorlabs_about.png"

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
  <key>LSEnvironment</key><dict>
    <key>PYTHONHOME</key><string>$dest/$app_name.app/Contents</string>
    <key>PYTHONNOUSERSITE</key><string>1</string>
    <key>FONTCONFIG_FILE</key><string>$dest/$app_name.app/Contents/etc/fonts.conf</string>
    <key>SSL_CERT_FILE</key><string>$dest/$app_name.app/Contents/ssl/cacert.pem</string>
    <key>QT_PLUGIN_PATH</key><string>$dest/$app_name.app/Contents/lib/qt6/plugins</string>
  </dict>
  <key>CFBundleDocumentTypes</key><array><dict>
    <key>CFBundleTypeName</key><string>FreeCAD Document</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Alternate</string>
    <key>CFBundleTypeExtensions</key><array><string>FCStd</string><string>fcstd</string></array>
  </dict></array>
</dict></plist>
PLIST

# ----- 6. self-contained: copy the libraries it needs, rewrite every path into the environment ---------
say "relocating libraries (a few minutes)"
python3 "$here/vectorlabs_relocate.py" "$C" "$env" "$build" "$repo" \
    || fail "some files still point into the environment or the build tree (listed above)"
# Python and the pyvenv-less interpreter look for their home through PYTHONHOME (Info.plist above).

# ----- 7. check it starts, then install -----------------------------------------------------------------
say "checking the staged bundle"
# A throwaway home: the check must not write the real Vector Labs settings (it once applied the theme headless
# and left the staging folder in them).
mkdir -p "$stage/home"
FREECAD_USER_HOME="$stage/home" PYTHONHOME="$C" PYTHONNOUSERSITE=1 "$C/bin/FreeCADCmd" -c "import FreeCAD, Part, sys; print('ok', FreeCAD.Version()[0:3], sys.prefix)" \
    >"$stage/check.log" 2>&1 || { cat "$stage/check.log"; fail "the staged FreeCADCmd does not start"; }
grep -q "^ok" "$stage/check.log" || { cat "$stage/check.log"; fail "the staged FreeCADCmd did not import Part"; }
say "$(grep '^ok' "$stage/check.log")"
if [[ -x "$C/bin/ccx" ]]; then
    # ccx -v exits 201 after printing its version, so judge it by the output, not the status (pipefail).
    ccx_out="$("$C/bin/ccx" -v 2>&1 || true)"
    [[ "$ccx_out" == *Version* ]] || fail "the bundled CalculiX does not start (its dylibs were not relocated?)"
    say "CalculiX $(grep -o 'Version [0-9.]*' <<<"$ccx_out")"
fi
if [[ -x "$C/bin/tectonic" ]]; then
    tectonic_out="$(env -i HOME="$HOME" "$C/bin/tectonic" --version 2>&1 || true)"
    # (0.15 prints its version twice, "tectonic 0.15.0Tectonic 0.15.0")
    [[ "$tectonic_out" == *"Tectonic "* ]] || fail "the bundled Tectonic does not start (its dylibs were not relocated?): $tectonic_out"
    say "$(grep -o 'Tectonic [0-9.]*' <<<"$tectonic_out")"
    # Pre-seed Tectonic's cache (30 Oct): compile tectonic-seed/seed.tex once (it loads every package the
    # study reports use, in every size and shape) with TECTONIC_CACHE_DIR inside the bundle. This run needs
    # the network (about 16 MB). At run time bruceworx_sim.tectonic_cache copies the seed once into
    # ~/Library/Caches/Vector Labs/tectonic (the bundle stays read-only) and points TECTONIC_CACHE_DIR there.
    seed="$C/Resources/tectonic-cache"
    rm -rf "$seed" "$stage/texseed"
    mkdir -p "$seed" "$stage/texseed"
    cp "$here/tectonic-seed/seed.tex" "$stage/texseed/seed.tex"
    cp "$here/branding/vectorlabs_splash_2x.png" "$stage/texseed/seed.png"
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
    [[ "$old_id" == "$bundle_id" ]] || fail "$target exists and is not Vector Labs ($old_id): not replacing it"
    say "replacing the Vector Labs.app installed before"
    rm -rf "$target"
fi
mkdir -p "$dest"
ditto "$app" "$target"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$target" || true
size="$(du -sh "$target" | cut -f1)"
say "installed $target ($size)"

if [[ $do_dmg == 1 ]]; then
    mkdir -p "$repo/dist"
    dmg="$repo/dist/Vector-Labs-$release+$commit$dirty-macOS-arm64.dmg"
    rm -f "$dmg"
    ln -s /Applications "$stage/Applications"
    hdiutil create -volname "$app_name $release" -srcfolder "$app" -srcfolder "$stage/Applications" -ov -format UDZO "$dmg" >/dev/null
    say "wrote $dmg"
fi
say "open it from Applications, or: open -a \"$app_name\""
