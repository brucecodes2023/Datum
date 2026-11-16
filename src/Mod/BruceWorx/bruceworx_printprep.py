# SPDX-License-Identifier: LGPL-2.1-or-later
"""3D-print prep, the FreeCAD side (roadmap MFG-01). No GUI here; bruceworx_printprep_gui has the commands.

* Print orientation: a part (a body or a solid) carries ``PrintUp``, the direction in its own frame that
  points up from the bed. place_on_face() sets it from a flat face (the part rests on that face);
  suggest() tries the part's flat faces and the six axis directions and ranks them by the area that
  needs support (bruceworx_analysis.overhang, the same numbers the overhang map shows).
* 3MF: write_3mf() writes one or several parts as one plate: each mesh is turned to its print
  orientation and set on z = 0 before it is written (so no reader has to apply a transform to see it
  the right way up), the parts are laid out in rows, units are millimetres. FreeCAD's own 3MF export
  (Mesh.export) writes a part in its own frame where it stands in the model, which is neither.
  read_3mf() reads a 3MF back (objects, build transforms, bounding boxes) for checks.
* Slicers: slicers() finds the installed apps that say they open 3MF (their Info.plist); bed_size()
  reads the selected printer's bed from OrcaSlicer's or Bambu Studio's own settings; send() exports to
  a file and opens it in the slicer.
* summary(): size in print orientation against the bed, support and bed-contact area, thinnest wall.

Lengths are mm. Preferences under BruceWorx: PrintPrep (off switch), PrintSlicer (app path),
PrintBedX / PrintBedY / PrintBedZ (0: ask the slicer), PrintPlateGap.
"""

from __future__ import annotations

import math
import os
import plistlib
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "PrintPrep"
SLICER_PREF = "PrintSlicer"
GAP_PREF = "PrintPlateGap"
BED_PREFS = ("PrintBedX", "PrintBedY", "PrintBedZ")
UP_PROP = "PrintUp"
PROP_GROUP = "Print"
DEFAULT_GAP = 5.0  # mm between parts on a plate: a starting value, not a rule; the slicer re-arranges
MESH_PREFS = "User parameter:BaseApp/Preferences/Mod/Mesh"  # FreeCAD's own "maximum mesh deviation" for export
MAX_CANDIDATE_FACES = 60
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
APP_FOLDERS = ("/Applications", "~/Applications")
SETTINGS_FOLDERS = ("OrcaSlicer", "BambuStudio")  # under ~/Library/Application Support: same settings layout


class PrintPrepError(Exception):
    """Something to tell the user in one sentence."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled():
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- print orientation ----------------------------------------------------------------------------


def part_of(obj):
    """The object that carries the print orientation: the body a feature belongs to, else the object."""
    if obj is None:
        return None
    if obj.isDerivedFrom("PartDesign::Body"):
        return obj
    for parent in obj.InList:
        if parent.isDerivedFrom("PartDesign::Body") and obj in parent.Group:
            return parent
    return obj


def has_up(obj):
    return UP_PROP in obj.PropertiesList


def up_of(obj):
    """The part's up direction in its own frame (unit vector); +Z when none was set."""
    App = _app()
    if has_up(obj):
        vec = App.Vector(getattr(obj, UP_PROP))
        if vec.Length > 1e-9:
            return vec.normalize()
    return App.Vector(0, 0, 1)


def set_up(obj, up):
    """Store the up direction (in the part's own frame). No transaction here."""
    App = _app()
    vec = App.Vector(up)
    if vec.Length < 1e-9:
        raise PrintPrepError("the print direction has no length")
    vec.normalize()
    if not has_up(obj):
        obj.addProperty("App::PropertyVector", UP_PROP, PROP_GROUP,
                        "Which way is up when this part is printed, in the part's own frame")
    setattr(obj, UP_PROP, vec)
    return vec


def clear_up(obj):
    if has_up(obj):
        obj.removeProperty(UP_PROP)


def _global_rotation(obj):
    try:
        return obj.getGlobalPlacement().Rotation
    except Exception:  # noqa: BLE001
        return obj.Placement.Rotation


def up_global(obj):
    """The up direction in the document's frame: what the overhang map takes as its build direction."""
    return _global_rotation(obj).multVec(up_of(obj))


def set_up_global(obj, up):
    """Store an up direction given in the document's frame."""
    return set_up(obj, _global_rotation(obj).inverted().multVec(_app().Vector(up)))


def place_on_face(obj, face_normal_global):
    """The part rests on a face with this outward normal (document frame): up is the opposite."""
    App = _app()
    return set_up_global(obj, App.Vector(face_normal_global) * -1)


def own_shape(obj):
    """The part's solids in its own frame (its Placement taken out)."""
    shape = obj.Shape.copy()
    shape.Placement = _app().Placement()
    if shape.isNull() or not shape.Solids:
        raise PrintPrepError(f"{obj.Label} has no solid to print")
    return shape


def bed_rotation(up):
    """The rotation that turns ``up`` to +Z."""
    App = _app()
    return App.Rotation(App.Vector(up), App.Vector(0, 0, 1))


def oriented_shape(obj):
    """The part as it prints: turned so its up is +Z, its lowest point on z = 0, centred on x = y = 0."""
    App = _app()
    shape = own_shape(obj)
    shape.Placement = App.Placement(App.Vector(), bed_rotation(up_of(obj)))
    box = shape.BoundBox
    shape.translate(App.Vector(-box.Center.x, -box.Center.y, -box.ZMin))
    return shape


# ----- which way up is best -------------------------------------------------------------------------


class Candidate:
    """One way to print the part: ``up`` (own frame), the support and bed areas, the height."""

    __slots__ = ("up", "label", "support_area", "bed_area", "height")

    def __init__(self, up, label, support_area, bed_area, height):
        self.up, self.label, self.support_area, self.bed_area, self.height = up, label, support_area, bed_area, height

    def key(self):
        # a part that rests on a flat face first; then the least support; then the widest footing; then the lowest
        return (self.bed_area <= 1e-6, round(self.support_area, 3), -round(self.bed_area, 3), round(self.height, 3))


def candidates(obj, threshold=None):
    """[Candidate], best first: the part resting on each of its flat faces, and each axis direction up."""
    import numpy as np

    import bruceworx_analysis as ba

    App = _app()
    threshold = ba.OVERHANG_DEFAULT if threshold is None else threshold
    shape = own_shape(obj)
    mesh = ba.surface_mesh(shape)
    ups = []

    def add(vec, label):
        vec = App.Vector(vec)
        if vec.Length < 1e-9:
            return
        vec.normalize()
        if not any((vec - known).Length < 1e-4 for known, _l in ups):
            ups.append((vec, label))

    flat = [(i, f) for i, f in enumerate(shape.Faces, 1) if type(f.Surface).__name__ == "Plane"]
    flat.sort(key=lambda item: -item[1].Area)
    for index, face in flat[:MAX_CANDIDATE_FACES]:
        u0, u1, v0, v1 = face.ParameterRange
        add(face.normalAt((u0 + u1) / 2, (v0 + v1) / 2) * -1, f"on Face{index}")
    for name, axis in (("+Z", (0, 0, 1)), ("-Z", (0, 0, -1)), ("+X", (1, 0, 0)), ("-X", (-1, 0, 0)),
                       ("+Y", (0, 1, 0)), ("-Y", (0, -1, 0))):
        add(axis, f"{name} up")
    out = []
    for vec, label in ups:
        build = (vec.x, vec.y, vec.z)
        stats = ba.overhang(mesh, build, threshold).stats
        heights = mesh.points @ np.array(build)
        out.append(Candidate(vec, label, stats["support_area"], stats["bed_area"],
                             float(heights.max() - heights.min()) if len(heights) else 0.0))
    out.sort(key=Candidate.key)
    return out


def suggest(obj, threshold=None):
    """The best Candidate (see Candidate.key for what best means)."""
    found = candidates(obj, threshold)
    if not found:
        raise PrintPrepError(f"{obj.Label} has no faces to judge")
    return found[0]


# ----- meshes and the plate -------------------------------------------------------------------------


def mesh_deviation():
    """FreeCAD's own export setting (Preferences > Import-Export > Mesh formats), 0.1 mm as shipped."""
    return float(_app().ParamGet(MESH_PREFS).GetFloat("MaxDeviationExport", 0.1))


def mesh_of(shape, deviation=None):
    """(points [(x, y, z)], triangles [(a, b, c)], watertight) of a shape."""
    import MeshPart

    deviation = mesh_deviation() if deviation is None else deviation
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deviation, AngularDeflection=0.5, Relative=False)
    points, facets = mesh.Topology
    return [(p.x, p.y, p.z) for p in points], [tuple(f) for f in facets], bool(mesh.isSolid())


def layout(sizes, gap=None, row_width=None):
    """Where each part goes on the plate: [(x, y)] centres for parts of ``sizes`` [(x length, y length)],
    in rows from the origin, ``gap`` apart, a row no wider than ``row_width`` (default: a square-ish plate)."""
    gap = float(_app().ParamGet(PREFS_PATH).GetFloat(GAP_PREF, DEFAULT_GAP)) if gap is None else gap
    if not sizes:
        return []
    if row_width is None:
        area = sum((w + gap) * (d + gap) for w, d in sizes)
        row_width = max(max(w for w, _d in sizes), math.sqrt(area))
    out, x, y, row_depth = [], 0.0, 0.0, 0.0
    for width, depth in sizes:
        if x > 0 and x + width > row_width + 1e-9:
            x, y, row_depth = 0.0, y + row_depth + gap, 0.0
        out.append((x + width / 2, y + depth / 2))
        x += width + gap
        row_depth = max(row_depth, depth)
    return out


def plate(objects, gap=None):
    """[{"label", "points", "triangles", "watertight", "size"}] for the parts as they print, laid out."""
    items = []
    for obj in objects:
        shape = oriented_shape(obj)
        points, triangles, watertight = mesh_of(shape)
        box = shape.BoundBox
        items.append({"label": obj.Label, "points": points, "triangles": triangles, "watertight": watertight,
                      "size": (box.XLength, box.YLength, box.ZLength)})
    centres = layout([(i["size"][0], i["size"][1]) for i in items], gap) if len(items) > 1 else [(0.0, 0.0)]
    for item, (cx, cy) in zip(items, centres):
        item["points"] = [(x + cx, y + cy, z) for x, y, z in item["points"]]
        item["at"] = (cx, cy)
    return items


# ----- 3MF ------------------------------------------------------------------------------------------

_CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                  ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                  ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
                  '</Types>\n')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
         ' <Relationship Target="/3D/3dmodel.model" Id="rel0" '
         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
         '</Relationships>\n')


def _number(value):
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def model_xml(items, application="Vector Labs"):
    """The 3D/3dmodel.model text for plate items (points already where they print)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS_CORE}">',
             f' <metadata name="Application">{application}</metadata>', ' <resources>']
    for number, item in enumerate(items, 1):
        lines.append(f'  <object id="{number}" type="model" name={quoteattr(item["label"])}>')
        lines.append("   <mesh>")
        lines.append("    <vertices>")
        lines.extend(f'     <vertex x="{_number(x)}" y="{_number(y)}" z="{_number(z)}"/>' for x, y, z in item["points"])
        lines.append("    </vertices>")
        lines.append("    <triangles>")
        lines.extend(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in item["triangles"])
        lines.append("    </triangles>")
        lines.append("   </mesh>")
        lines.append("  </object>")
    lines.append(" </resources>")
    lines.append(" <build>")
    lines.extend(f'  <item objectid="{number}"/>' for number in range(1, len(items) + 1))
    lines.append(" </build>")
    lines.append("</model>")
    return "\n".join(lines) + "\n"


def write_3mf(path, objects, gap=None):
    """Write ``objects`` as one plate. Returns the plate items (without their meshes) for a report."""
    if not objects:
        raise PrintPrepError("nothing to export: select a part")
    items = plate(objects, gap)
    empty = [i["label"] for i in items if not i["triangles"]]
    if empty:
        raise PrintPrepError(f"{', '.join(empty)} could not be meshed")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _RELS)
        package.writestr("3D/3dmodel.model", model_xml(items))
    return [{"label": i["label"], "size": i["size"], "at": i["at"], "triangles": len(i["triangles"]),
             "watertight": i["watertight"]} for i in items]


def read_3mf(path):
    """{"unit", "objects": [{"id", "name", "vertices", "triangles", "transform", "min", "max"}]}: every
    build item of a 3MF with its bounding box after its transform (components are not followed)."""
    with zipfile.ZipFile(path) as package:
        rels = ElementTree.fromstring(package.read("_rels/.rels"))
        target = next(r.get("Target") for r in rels if r.get("Type", "").endswith("/3dmodel"))
        root = ElementTree.fromstring(package.read(target.lstrip("/")))
    ns = {"m": NS_CORE}
    meshes = {}
    for obj in root.findall("m:resources/m:object", ns):
        vertices = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                    for v in obj.findall("m:mesh/m:vertices/m:vertex", ns)]
        triangles = len(obj.findall("m:mesh/m:triangles/m:triangle", ns))
        meshes[obj.get("id")] = (obj.get("name", ""), vertices, triangles)
    out = []
    for item in root.findall("m:build/m:item", ns):
        name, vertices, triangles = meshes.get(item.get("objectid"), ("", [], 0))
        t = [float(v) for v in item.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0").split()]
        moved = [(x * t[0] + y * t[3] + z * t[6] + t[9], x * t[1] + y * t[4] + z * t[7] + t[10],
                  x * t[2] + y * t[5] + z * t[8] + t[11]) for x, y, z in vertices]
        entry = {"id": item.get("objectid"), "name": name, "vertices": len(vertices), "triangles": triangles,
                 "transform": t, "min": None, "max": None}
        if moved:
            entry["min"] = tuple(min(p[k] for p in moved) for k in range(3))
            entry["max"] = tuple(max(p[k] for p in moved) for k in range(3))
        out.append(entry)
    return {"unit": root.get("unit", "millimeter"), "objects": out}


# ----- slicers --------------------------------------------------------------------------------------


def _opens_3mf(info):
    for kind in info.get("CFBundleDocumentTypes", []) or []:
        if "3mf" in [str(e).lower() for e in kind.get("CFBundleTypeExtensions", []) or []]:
            return True
    return False


def slicers(folders=APP_FOLDERS):
    """[{"name", "path", "version", "executable"}]: the installed apps whose Info.plist says they slice:
    they open 3MF and G-code. Found on the machine, not from a list of product names."""
    out = []
    for folder in folders:
        folder = os.path.expanduser(folder)
        if not os.path.isdir(folder):
            continue
        apps = []
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if name.endswith(".app"):
                apps.append(full)
            elif os.path.isdir(full):  # one level down: a vendor's folder
                try:
                    apps.extend(os.path.join(full, n) for n in sorted(os.listdir(full)) if n.endswith(".app"))
                except OSError:
                    pass
        for app in apps:
            try:
                with open(os.path.join(app, "Contents", "Info.plist"), "rb") as handle:
                    info = plistlib.load(handle)
            except Exception:  # noqa: BLE001 -- not a readable bundle
                continue
            extensions = {str(e).lower() for kind in info.get("CFBundleDocumentTypes", []) or []
                          for e in kind.get("CFBundleTypeExtensions", []) or []}
            if "3mf" not in extensions or not extensions & {"gcode", "gco", "g"}:
                continue
            out.append({"name": info.get("CFBundleName") or os.path.basename(app)[:-4], "path": app,
                        "version": info.get("CFBundleShortVersionString", ""),
                        "executable": os.path.join(app, "Contents", "MacOS", info.get("CFBundleExecutable", ""))})
    return out


def slicer(found=None):
    """The slicer to use: the one in the PrintSlicer preference when it is still there, else the first found."""
    found = slicers() if found is None else found
    wanted = _app().ParamGet(PREFS_PATH).GetString(SLICER_PREF, "")
    for entry in found:
        if entry["path"] == wanted:
            return entry
    return found[0] if found else None


def set_slicer(path):
    _app().ParamGet(PREFS_PATH).SetString(SLICER_PREF, path or "")


def _slicer_bed(folder):
    """(x, y, z, printer name) of the printer selected in a slicer's settings folder, or None."""
    import glob
    import json

    try:
        with open(os.path.join(folder, os.path.basename(folder) + ".conf"), encoding="utf-8") as handle:
            text = handle.read()
        conf = json.loads(text[:text.rindex("}") + 1])  # the file ends with a checksum comment line
    except Exception:  # noqa: BLE001
        return None
    name = (conf.get("presets") or {}).get("machine")
    files = glob.glob(os.path.join(folder, "**", "machine", "**", "*.json"), recursive=True)
    selected = name
    area = height = None
    for _depth in range(8):  # a printer inherits from its parent preset
        match = None
        for path in files:
            if os.path.basename(path)[:-5] != name:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:  # noqa: BLE001
                continue
            if data.get("name", name) == name:
                match = data
                break
        if match is None:
            return None
        area = area or match.get("printable_area")
        height = height or match.get("printable_height")
        if area and height:
            break
        name = match.get("inherits")
        if not name:
            break
    if not area or not height:
        return None
    try:
        corners = [tuple(float(v) for v in corner.split("x")) for corner in area]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        return (max(xs) - min(xs), max(ys) - min(ys), float(height), selected)
    except Exception:  # noqa: BLE001
        return None


def bed_size(settings_root=None):
    """(x, y, z, where it came from) of the printer's build volume, or None when nobody says.
    The PrintBed preferences win; else the printer selected in OrcaSlicer or Bambu Studio."""
    prefs = _app().ParamGet(PREFS_PATH)
    typed = [float(prefs.GetFloat(name, 0.0)) for name in BED_PREFS]
    if all(v > 0 for v in typed):
        return (typed[0], typed[1], typed[2], "Preferences")
    root = os.path.expanduser(settings_root or "~/Library/Application Support")
    for folder in SETTINGS_FOLDERS:
        found = _slicer_bed(os.path.join(root, folder))
        if found is not None:
            return (found[0], found[1], found[2], f"{folder}: {found[3]}")
    return None


def send(path, app=None, run=None):
    """Open ``path`` in the slicer (``app``: a slicers() entry; default: the chosen one)."""
    import subprocess

    app = app or slicer()
    if app is None:
        raise PrintPrepError("no slicer was found in Applications (an app that opens 3MF and G-code); "
                             "export the 3MF and open it by hand")
    (run or subprocess.Popen)(["/usr/bin/open", "-a", app["path"], path])
    return app


# ----- printability ---------------------------------------------------------------------------------


def fits(size, bed):
    """Does a part of ``size`` (x, y, z as it prints) fit the bed (x, y, z), turned 90 degrees if need be?"""
    x, y, z = size
    if z > bed[2] + 1e-9:
        return False
    return (x <= bed[0] + 1e-9 and y <= bed[1] + 1e-9) or (y <= bed[0] + 1e-9 and x <= bed[1] + 1e-9)


def summary(obj, threshold=None, walls=False):
    """{"label", "up", "size", "support_area", "bed_area", "bed", "fits", "thinnest", "wall_cap"} for the part as it prints.
    ``walls``: also the thinnest wall (bruceworx_analysis.thickness; slower)."""
    import bruceworx_analysis as ba

    threshold = ba.OVERHANG_DEFAULT if threshold is None else threshold
    shape = oriented_shape(obj)
    mesh = ba.surface_mesh(shape)
    stats = ba.overhang(mesh, (0, 0, 1), threshold).stats
    box = shape.BoundBox
    size = (box.XLength, box.YLength, box.ZLength)
    bed = bed_size()
    up = up_of(obj)
    out = {"label": obj.Label, "up": (up.x, up.y, up.z), "up_set": has_up(obj), "size": size,
           "support_area": stats["support_area"], "bed_area": stats["bed_area"], "threshold": threshold,
           "bed": bed, "fits": fits(size, bed) if bed else None, "thinnest": None, "wall_cap": None}
    if walls:
        try:
            # the wall map only measures walls up to its cap (thicker ones are all "thick enough"): a part
            # with no wall under the cap has no thinnest value, and the cap says how far it looked
            stats = ba.thickness(mesh).stats
            out["thinnest"], out["wall_cap"] = stats.get("thinnest"), stats.get("cap")
        except Exception:  # noqa: BLE001 -- the map is a nicety here
            pass
    return out


def summary_text(info):
    """The summary as a few lines for the panel."""
    def n(value, digits=1):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")

    x, y, z = info["size"]
    lines = [f"{info['label']}: {n(x)} × {n(y)} × {n(z)} mm as printed"
             + ("" if info["up_set"] else " (no orientation set: +Z up)")]
    if info["bed"]:
        bx, by, bz, source = info["bed"]
        verdict = "fits" if info["fits"] else "does NOT fit"
        lines.append(f"Bed {n(bx)} × {n(by)} × {n(bz)} mm ({source}): {verdict}")
    else:
        lines.append("Bed size unknown: set it in Preferences > BruceWorx, or pick a printer in the slicer")
    if info["bed_area"] > 1e-6:
        lines.append(f"Rests on {n(info['bed_area'])} mm² of flat face")
    else:
        lines.append("Does not rest on a flat face in this orientation")
    if info["support_area"] > 1e-6:
        lines.append(f"Needs support under {n(info['support_area'])} mm² (steeper than {n(info['threshold'])}° from vertical)")
    else:
        lines.append("Prints without support")
    if info["thinnest"] is not None:
        lines.append(f"Thinnest wall {n(info['thinnest'], 2)} mm")
    elif info.get("wall_cap"):
        lines.append(f"No wall thinner than {n(info['wall_cap'], 2)} mm")
    return "\n".join(lines)
