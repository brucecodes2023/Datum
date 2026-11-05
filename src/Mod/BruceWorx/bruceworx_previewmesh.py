# SPDX-License-Identifier: LGPL-2.1-or-later
"""The light preview mesh a vault file carries for the Files tab's 3D preview (PDM-13, 1 Nov 2026).

The Files tab shows a part or an assembly in 3D without opening it. Opening a document costs
0.1-2 s and moves the user's views; the preview mesh is read from the file in milliseconds.

* A part file carries its visible shapes as triangles, one mesh per shape with its colour (and its
  transparency, when it has one: the clear acrylic panels, Bruce 5 Nov).
* An assembly file carries no triangles for the parts it links: it records each link's file and
  placement (an *instance*), and the viewer reads the linked file's own mesh. A bolt used forty
  times is stored once, in the bolt's file (plan, "Why assemblies are instanced").
* The mesh is a zip entry of the ``.FCStd`` (``ENTRY``), written after every save of a vault work
  file, with the iso picture, in one rewrite of the archive (``rewrite``). FreeCAD drops unknown
  entries when it saves, so both are written again each time. Versions checked in before this get
  theirs from Refresh Pictures, into a cache on this Mac (``cache_path``), never into the vault.

The format (``encode``/``decode``) is FreeCAD-free and tested on its own: ``MAGIC``, a little-endian
uint32 header length, a JSON header, then the float32 vertex and uint32 index blocks it points to.
"""

from __future__ import annotations

import array
import json
import os
import shutil
import struct
import sys
import zipfile

ENTRY = "bruceworx/preview.bin"
MAGIC = b"BWPM1\0\0\0"
GREY = (0.75, 0.75, 0.78)
# Tessellation (plan, "How fine the preview mesh is"): chord error 0.4 % of the shape's box diagonal,
# at least 0.05 mm, and at most 0.35 rad (20 degrees) per segment: every circle gets 18 segments or more.
DEFLECTION = 0.004
MIN_DEFLECTION = 0.05
ANGULAR = 0.35
_LITTLE = sys.byteorder == "little"


# ----- the format (no FreeCAD) --------------------------------------------------------------------


def _bytes(values, code):
    block = array.array(code, values)
    if not _LITTLE:
        block.byteswap()
    return block.tobytes()


def _values(data, code, start, count):
    block = array.array(code)
    block.frombytes(data[start:start + count * block.itemsize])
    if not _LITTLE:
        block.byteswap()
    return block


def encode(parts, instances=()) -> bytes:
    """``parts``: [{"name", "color": (r, g, b), "points": [x, y, z, ...], "triangles": [i, j, k, ...]},
    optionally "transparency": 0-1 (left out when 0, so an opaque part's mesh is as before)];
    ``instances``: [{"file": "<P/N>.FCStd", "object": name, "matrix": 16 numbers, row-major}]."""
    header = {"version": 1, "parts": [], "instances": []}
    blocks, offset = [], 0
    for part in parts:
        points = _bytes(part["points"], "f")
        triangles = _bytes(part["triangles"], "I")
        header["parts"].append({"name": part.get("name", ""), "color": [round(c, 4) for c in part.get("color", GREY)],
                                "points": [offset, len(part["points"]) // 3],
                                "triangles": [offset + len(points), len(part["triangles"]) // 3]})
        if part.get("transparency"):
            header["parts"][-1]["transparency"] = round(float(part["transparency"]), 3)
        blocks += [points, triangles]
        offset += len(points) + len(triangles)
    for inst in instances:
        header["instances"].append({"file": inst["file"], "object": inst.get("object", ""),
                                    "matrix": [round(v, 6) for v in inst["matrix"]]})
    text = json.dumps(header, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)  # the blocks start on a 4-byte boundary
    return MAGIC + struct.pack("<I", len(text)) + text + b"".join(blocks)


def decode(data: bytes) -> dict:
    """{"parts": [{"name", "color", "transparency", "points": array('f'), "triangles": array('I')}], "instances": [...]};
    raises ValueError for anything that is not a preview mesh."""
    if not data or not data.startswith(MAGIC) or len(data) < len(MAGIC) + 4:
        raise ValueError("not a preview mesh")
    (size,) = struct.unpack_from("<I", data, len(MAGIC))
    start = len(MAGIC) + 4
    try:
        header = json.loads(data[start:start + size].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"damaged preview mesh header ({exc})") from exc
    if header.get("version") != 1:
        raise ValueError(f"preview mesh version {header.get('version')!r} is not supported")
    base = start + size
    parts = []
    for p in header.get("parts", []):
        (po, pc), (to, tc) = p["points"], p["triangles"]
        if base + to + tc * 12 > len(data) or base + po + pc * 12 > len(data):
            raise ValueError("preview mesh is cut short")
        triangles = _values(data, "I", base + to, tc * 3)
        if triangles and max(triangles) >= pc:
            raise ValueError("preview mesh index out of range")
        parts.append({"name": p.get("name", ""), "color": tuple(p.get("color", GREY)),
                      "transparency": min(max(float(p.get("transparency", 0.0)), 0.0), 1.0),
                      "points": _values(data, "f", base + po, pc * 3), "triangles": triangles})
    return {"parts": parts, "instances": list(header.get("instances", []))}


def read(path) -> bytes:
    """The preview mesh stored in the ``.FCStd`` at ``path``, or b""."""
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(ENTRY)
    except (OSError, KeyError, zipfile.BadZipFile):
        return b""


def rewrite(path, entries) -> None:
    """Put ``entries`` ({name: bytes}) into the archive at ``path``, replacing entries of the same name:
    the archive rewritten entry by entry, in order, then swapped in (a failure leaves it as it was)."""
    tmp = path + ".bw-preview"
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
            names = set(src.namelist())
            for info in src.infolist():
                dst.writestr(info, entries[info.filename] if info.filename in entries else src.read(info.filename))
            for name, data in entries.items():
                if name not in names:
                    dst.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def multiply(a, b):
    """Row-major 4x4 matrices: a @ b."""
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def flatten(load, key, matrix=IDENTITY, seen=(), depth=0):
    """The meshes to draw for ``key``: [(part, matrix)], following instances through ``load(file)``
    (which returns a decoded mesh or None). A file seen above itself (a cycle) or more than 12 levels
    down is skipped. ``key`` is what ``load`` takes for the top file."""
    mesh = load(key)
    if mesh is None or key in seen or depth > 12:
        return []
    out = [(part, matrix) for part in mesh["parts"]]
    for inst in mesh["instances"]:
        out += flatten(load, inst["file"], multiply(list(matrix), inst["matrix"]), (*seen, key), depth + 1)
    return out


# ----- the cache for versions checked in before -------------------------------------------------


def cache_dir():
    import FreeCAD

    base = getattr(FreeCAD, "getUserCachePath", FreeCAD.getUserAppDataDir)()
    return os.path.join(base, "BruceWorx", "meshes")


def cache_path(sha) -> str:
    return os.path.join(cache_dir(), f"{sha}.bin")


def cached(sha) -> bytes:
    path = cache_path(sha) if sha else ""
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def store_cached(sha, data) -> None:
    os.makedirs(cache_dir(), exist_ok=True)
    target = cache_path(sha)
    with open(target + ".part", "wb") as out:
        out.write(data)
    os.replace(target + ".part", target)


# ----- making it (FreeCAD) ------------------------------------------------------------------------


def _colour(obj):
    view = getattr(obj, "ViewObject", None)
    try:
        return tuple(view.ShapeAppearance[0].DiffuseColor[:3])
    except Exception:  # noqa: BLE001 -- headless, or a view provider without appearance
        return GREY


def _transparency(obj) -> float:
    """0 (opaque) to 1 (clear), as the part shows in the 3D view."""
    view = getattr(obj, "ViewObject", None)
    try:
        return float(view.ShapeAppearance[0].Transparency)
    except Exception:  # noqa: BLE001 -- headless, or a view provider without appearance
        pass
    try:
        return int(view.Transparency) / 100.0
    except Exception:  # noqa: BLE001
        return 0.0


def _shown(obj) -> bool:
    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            return bool(view.Visibility)
        except Exception:  # noqa: BLE001
            pass
    return bool(getattr(obj, "Visibility", True))


def _matrix(placement):
    return list(placement.toMatrix().A)


def _instance_target(obj):
    """(file name, object name, its shape) of what an App::Link shows, when that lives in another
    saved file and the link is a single instance; else None (it is meshed as a local shape)."""
    if obj.TypeId != "App::Link" or getattr(obj, "ElementCount", 0):
        return None
    target = obj.getLinkedObject(True)
    doc = getattr(target, "Document", None)
    if target is None or doc is None or doc is obj.Document or not doc.FileName:
        return None
    shape = getattr(target, "Shape", None)
    if shape is None or shape.isNull():  # the linked file did not load: nothing to place
        return False
    return os.path.basename(doc.FileName), target.Name, shape


def tessellate(doc):
    """(parts, instances) for ``doc``: every visible shape that is not a helper, in global coordinates,
    as triangles; every link to another file as an instance."""
    import MeshPart
    from bruceworx_preview import _helper

    parts, instances = [], []
    for obj in doc.Objects:
        if _helper(obj) or not _shown(obj) or obj.TypeId in ("App::Part", "App::Origin"):
            continue
        if obj.isDerivedFrom("Assembly::AssemblyObject") or obj.TypeId == "App::DocumentObjectGroup":
            continue
        try:
            target = _instance_target(obj)
        except Exception:  # noqa: BLE001 -- a broken link: left out of the preview
            continue
        if target is False:
            continue
        if target is not None:
            name, object_name, shape = target
            # The link's shape is the target's shape moved by T: T = link placement * target placement^-1.
            transform = obj.Shape.Placement.multiply(shape.Placement.inverse())
            instances.append({"file": name, "object": object_name, "matrix": _matrix(transform)})
            continue
        if not obj.isDerivedFrom("Part::Feature") and obj.TypeId != "App::Link":
            continue
        if any(p.TypeId != "App::Part" and not p.isDerivedFrom("Assembly::AssemblyObject")
               and p.isDerivedFrom("Part::Feature") and _shown(p) for p in obj.InList):
            continue  # shown through a visible parent (a body's features, a boolean's tools)
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Faces:
            continue
        shape = shape.copy()
        shape.Placement = obj.getGlobalPlacement() if obj.TypeId != "App::Link" else obj.Shape.Placement
        box = shape.BoundBox
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=max(box.DiagonalLength * DEFLECTION, MIN_DEFLECTION),
                                      AngularDeflection=ANGULAR, Relative=False)
        points, facets = mesh.Topology
        if not facets:
            continue
        parts.append({"name": obj.Label, "color": _colour(obj), "transparency": _transparency(obj),
                      "points": [c for p in points for c in (p.x, p.y, p.z)],
                      "triangles": [i for f in facets for i in f]})
    return parts, instances


def mesh_of(doc) -> bytes:
    parts, instances = tessellate(doc)
    return encode(parts, instances) if parts or instances else b""
