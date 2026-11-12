# SPDX-License-Identifier: LGPL-2.1-or-later
"""Saving for agents (AUT-02): a headless save that keeps the display data and refuses a moved origin.

FreeCADCmd writes Document.xml and the shapes only: GuiDocument.xml, the colour files it names, the thumbnail and
the bruceworx/ folder are left out, so a part saved headless opens grey and without its picture (2 Nov 2026). And
a solve can move an assembly's own origin, which shifts every part of the assemblies that use it.

    safe_save(doc, path=None, backup=False, check_origin=True)
        -> {"path", "kept_entries", "dropped_entries", "origin_ok", "mode", "backup"}
    grab(path)                 -> {entry name: (ZipInfo, bytes)}     the display data of a saved file
    readd(path, grabbed)       -> the names put back (the file is rewritten beside itself, then swapped in)
    disk_origins(path)         -> {assembly Name: (x, y, z, q0, q1, q2, q3)}   from the file's Document.xml
    moved_origins(doc, path)   -> [{"assembly", "label", "was", "now"}]
    OriginMoved                raised by safe_save; nothing is written

grab, readd and disk_origins need no FreeCAD. In a FreeCAD window safe_save is a normal save (the window writes
its own display data); the origin is checked there too.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import zipfile

GUI_FILE = "GuiDocument.xml"
# What a headless save leaves out. The colour files are also found by name in GuiDocument.xml (grab).
KEEP = (GUI_FILE, "ShapeAppearance", "thumbnails/", "bruceworx/", "DiffuseColor", "LineColorArray",
        "PointColorArray", "MaterialList", "ColourList")
ASSEMBLY_TYPE = "Assembly::AssemblyObject"
ORIGIN_TOL_MM = 1e-7
ORIGIN_TOL_Q = 1e-9
_FILE_ATTR = re.compile(rb'\bfile="([^"]+)"')


class OriginMoved(RuntimeError):
    """An assembly's own origin is not where the file on disk has it. Nothing was saved."""


# ----- the zip side (no FreeCAD) -------------------------------------------------------------------------------


def grab(path) -> dict:
    """The display data of a saved file: GuiDocument.xml, every file it names, the thumbnail, bruceworx/."""
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        named = set()
        if GUI_FILE in names:
            named = {m.decode("utf-8", "replace") for m in _FILE_ATTR.findall(z.read(GUI_FILE))}
        for name in names:
            if name.startswith(KEEP) or name in named:
                out[name] = (z.getinfo(name), z.read(name))
    return out


def readd(path, grabbed) -> list:
    """Put the grabbed entries the file at ``path`` lacks back into it. The file is written again beside itself
    and swapped in (os.replace), so a reader sees the old file or the new one, never half of one. Entries the
    file already has are left alone; the order of the ones it has is kept (Document.xml stays first)."""
    with zipfile.ZipFile(path) as src:
        have = set(src.namelist())
        missing = [n for n in grabbed if n not in have]
        if not missing:
            return []
        if "Document.xml" not in have:
            raise ValueError(f"{path} has no Document.xml: not a saved document")
        tmp = f"{path}.regui-{os.getpid()}"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
                for info in src.infolist():
                    dst.writestr(info, src.read(info.filename))
                for name in missing:
                    info, data = grabbed[name]
                    dst.writestr(info, data)
            with open(tmp, "rb") as handle:
                os.fsync(handle.fileno())
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    return missing


def disk_origins(path) -> dict:
    """Where the file has each assembly's own origin: {Name: (x, y, z, q0, q1, q2, q3)}."""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        if "Document.xml" not in z.namelist():
            return {}
        with z.open("Document.xml") as handle:
            root = ET.parse(handle).getroot()
    wanted = {o.get("name") for o in root.findall("./Objects/Object") if o.get("type") == ASSEMBLY_TYPE}
    out = {}
    for obj in root.findall("./ObjectData/Object"):
        name = obj.get("name")
        if name not in wanted:
            continue
        node = obj.find("./Properties/Property[@name='Placement']/PropertyPlacement")
        if node is None:
            continue
        try:
            out[name] = tuple(float(node.get(k)) for k in ("Px", "Py", "Pz", "Q0", "Q1", "Q2", "Q3"))
        except (TypeError, ValueError):
            continue
    return out


def same_origin(a, b, tol_mm=ORIGIN_TOL_MM, tol_q=ORIGIN_TOL_Q) -> bool:
    """Two (x, y, z, q0, q1, q2, q3): the same place and turn (q and -q are one rotation)."""
    if any(abs(a[i] - b[i]) > tol_mm for i in range(3)):
        return False
    plus = max(abs(a[i] - b[i]) for i in range(3, 7))
    minus = max(abs(a[i] + b[i]) for i in range(3, 7))
    return min(plus, minus) <= tol_q


def backup_name(path, when=None) -> str:
    """The dated copy kept beside ``path``: Plate.FCStd -> Plate.20261110-142501.bak.FCStd"""
    when = when or _dt.datetime.now()
    stem, ext = os.path.splitext(path)
    return f"{stem}.{when.strftime('%Y%m%d-%H%M%S')}.bak{ext or '.FCStd'}"


# ----- the FreeCAD side ------------------------------------------------------------------------------------------


def _placement_tuple(placement):
    base, q = placement.Base, placement.Rotation.Q
    return (base.x, base.y, base.z, q[0], q[1], q[2], q[3])


def moved_origins(doc, path) -> list:
    """The assemblies of ``doc`` whose own origin differs from the file at ``path``. An assembly the file does
    not have yet has nothing to be compared with and is not listed."""
    if not path or not os.path.isfile(path):
        return []
    on_disk = disk_origins(path)
    out = []
    for obj in doc.Objects:
        if obj.TypeId != ASSEMBLY_TYPE or obj.Name not in on_disk:
            continue
        now = _placement_tuple(obj.Placement)
        if not same_origin(on_disk[obj.Name], now):
            out.append({"assembly": obj.Name, "label": obj.Label,
                        "was": [round(v, 9) for v in on_disk[obj.Name]], "now": [round(v, 9) for v in now]})
    return out


def _target(doc, path):
    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".fcstd"):
            path += ".FCStd"
        return path
    if not doc.FileName:
        raise ValueError(f"{doc.Name} has never been saved; pass a path")
    return os.path.abspath(doc.FileName)


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def safe_save(doc, path=None, backup=False, check_origin=True) -> dict:
    """Save ``doc`` (to ``path``, or where it was opened from) without losing what a headless save drops.

    * Refused with OriginMoved, the file untouched, when an assembly's own origin is not where the document's
      file has it (check_origin=False saves anyway and reports origin_ok False).
    * Headless: the display data of the document's file is read first and put back after the save. If the save
      or the putting back fails, the file that was there is restored.
    * backup=True keeps a dated copy of the file that is being replaced beside it.
    * In a FreeCAD window: a normal save; "mode" says "gui".

    kept_entries are the entries put back; dropped_entries are entries of the old file that the new one does
    not have and that were not display data (shapes of objects that are gone, for example)."""
    import FreeCAD as App

    target = _target(doc, path)
    source = os.path.abspath(doc.FileName) if doc.FileName and os.path.isfile(doc.FileName) else None
    moved = moved_origins(doc, source)
    if moved and check_origin:
        first = moved[0]
        raise OriginMoved(
            f"{doc.Name}: the origin of assembly {first['label']} moved (file {first['was']}, now {first['now']}); "
            "not saved. Put the assembly's Placement back, or save with check_origin=False if it is meant.")
    existed = os.path.isfile(target)
    save_as = source is None or not _same_file(source, target)
    out = {"path": target, "kept_entries": [], "dropped_entries": [], "origin_ok": not moved,
           "mode": "gui" if App.GuiUp else "headless", "backup": None}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if backup and existed:
        out["backup"] = backup_name(target)
        shutil.copy2(target, out["backup"])

    if App.GuiUp:
        doc.saveAs(target) if save_as else doc.save()
        out["path"] = os.path.abspath(doc.FileName)
        return out

    grabbed = grab(source) if source else {}
    old_names = []
    if source:
        with zipfile.ZipFile(source) as z:
            old_names = z.namelist()
    undo = None  # the file as it was, to put back if anything below fails
    if existed:
        undo = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.presave-{os.getpid()}")
        shutil.copy2(target, undo)
    try:
        doc.saveAs(target) if save_as else doc.save()
        out["kept_entries"] = readd(target, grabbed)
        with zipfile.ZipFile(target) as z:
            have = set(z.namelist())
        out["dropped_entries"] = [n for n in old_names if n not in have]
    except BaseException:
        if undo is not None:
            os.replace(undo, target)
            undo = None
        elif os.path.exists(target):
            os.remove(target)
        raise
    finally:
        if undo is not None and os.path.exists(undo):
            os.remove(undo)
    return out


__all__ = ["safe_save", "grab", "readd", "disk_origins", "moved_origins", "same_origin", "backup_name",
           "OriginMoved", "KEEP"]
