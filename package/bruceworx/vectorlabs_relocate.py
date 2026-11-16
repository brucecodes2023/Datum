# SPDX-License-Identifier: LGPL-2.1-or-later
"""Make a staged Heuretes FreeCAD.app self-contained (used by make-vectorlabs.sh).

The build installs FreeCAD with absolute rpaths into the pixi environment. This walks every
Mach-O file in the bundle and:

* copies the dylibs it needs from the environment's lib/ into Contents/lib (until none is missing);
* rewrites load commands that name the environment or the build tree to @rpath/<name>, and the
  ids of the libraries it holds to @rpath/<name>;
* replaces absolute rpaths with one relative to the file (@loader_path/... to Contents/lib;
  @executable_path/../lib for Contents/MacOS);
* ad-hoc signs every file it changed (arm64 refuses to load a Mach-O whose signature is broken).

    python3 vectorlabs_relocate.py <Contents> <env prefix> [<other prefix to strip> ...]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def is_macho(path: str) -> bool:
    try:
        if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) < 4096:
            return False
        with open(path, "rb") as f:
            return f.read(4) in MAGICS
    except OSError:
        return False


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def load_info(path: str):
    """(id, [dependencies], [rpaths]) of a Mach-O file."""
    out = run("otool", "-l", path)
    lid, deps, rpaths = None, [], []
    cmd = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("cmd "):
            cmd = line.split()[1]
        elif line.startswith("name ") and cmd in ("LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB",
                                                  "LC_ID_DYLIB", "LC_LAZY_LOAD_DYLIB"):
            name = re.sub(r" \(offset \d+\)$", "", line[5:])
            if cmd == "LC_ID_DYLIB":
                lid = name
            else:
                deps.append(name)
        elif line.startswith("path ") and cmd == "LC_RPATH":
            rpaths.append(re.sub(r" \(offset \d+\)$", "", line[5:]))
    return lid, deps, rpaths


def main(contents: str, env: str, strip: list[str]) -> int:
    contents = os.path.realpath(contents)
    lib = os.path.join(contents, "lib")
    env_lib = os.path.join(os.path.realpath(env), "lib")
    prefixes = [os.path.realpath(env)] + [os.path.realpath(p) for p in strip]
    os.makedirs(lib, exist_ok=True)

    def foreign(name: str) -> bool:
        return name.startswith("/") and any(name.startswith(p + "/") for p in prefixes)

    # 1. the closure: every dylib a file needs from the environment is copied into Contents/lib
    copied = 0
    pending = [os.path.join(r, f) for r, _d, fs in os.walk(contents) for f in fs]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen or not is_macho(path):
            continue
        seen.add(path)
        _lid, deps, _rp = load_info(path)
        for dep in deps:
            if dep.startswith("/usr/lib/") or dep.startswith("/System/"):
                continue
            base = os.path.basename(dep)
            if dep.startswith("@loader_path/") or dep.startswith("@executable_path/"):
                continue  # relative to the file: shipped with it (python packages, plugins)
            if not (dep.startswith("@rpath/") or foreign(dep)):
                continue
            target = os.path.join(lib, base)
            if os.path.exists(target):
                continue
            source = os.path.join(env_lib, base)
            if not os.path.exists(source) and foreign(dep) and os.path.exists(dep):
                source = dep
            if os.path.exists(source):
                shutil.copy2(os.path.realpath(source), target)
                os.chmod(target, 0o755)
                pending.append(target)
                copied += 1
    print(f"vectorlabs: {copied} libraries copied from the environment")

    # 2. load commands, ids and rpaths
    changed = 0
    for path in sorted(seen | {os.path.join(lib, f) for f in os.listdir(lib)}):
        if not is_macho(path):
            continue
        lid, deps, rpaths = load_info(path)
        args = []
        if lid and foreign(lid):
            args += ["-id", "@rpath/" + os.path.basename(lid)]
        for dep in deps:
            if foreign(dep):
                args += ["-change", dep, "@rpath/" + os.path.basename(dep)]
        if os.path.dirname(path) == os.path.join(contents, "MacOS"):
            want = "@executable_path/../lib"
        else:
            want = "@loader_path/" + os.path.relpath(lib, os.path.dirname(path))
        for rp in rpaths:
            if rp.startswith("/"):
                args += ["-delete_rpath", rp]
        if want not in rpaths:
            args += ["-add_rpath", want]
        if not args:
            continue
        try:
            os.chmod(path, os.stat(path).st_mode | 0o200)
            subprocess.run(["install_name_tool", *args, path], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            # a duplicate rpath delete or similar: say which file, keep going
            print(f"vectorlabs: install_name_tool on {os.path.relpath(path, contents)}: {exc.stderr.strip()}")
            continue
        subprocess.run(["codesign", "--force", "--sign", "-", path], check=True, capture_output=True)
        changed += 1
    print(f"vectorlabs: {changed} files relocated and re-signed")

    # 3. nothing may still point at the environment or the build tree
    left = []
    for path in sorted(seen):
        if not is_macho(path):
            continue
        lid, deps, rpaths = load_info(path)
        bad = [d for d in deps + rpaths + ([lid] if lid else []) if foreign(d)]
        if bad:
            left.append((os.path.relpath(path, contents), bad[0]))
    for rel, bad in left[:20]:
        print(f"vectorlabs: still absolute: {rel} -> {bad}")
    return 1 if left else 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
