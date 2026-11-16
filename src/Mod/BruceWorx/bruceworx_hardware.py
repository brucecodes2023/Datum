# SPDX-License-Identifier: LGPL-2.1-or-later
"""Threads, holes and the hardware library (MOD-05).

* **Hole presets** (BruceWorx_HolePreset). Heat-set insert holes for 3D-printed parts (CNC
  Kitchen and ruthex inserts, M2-M5), tapped, clearance, counterbore and countersink holes,
  from ``bruceworx.hardware``'s preset table (built in, plus the vault's
  ``hole_presets.json``). A preset sets an ordinary PartDesign Hole's properties, so the
  Hole's own task panel edits it afterwards; the preset's name stays on the hole
  (``HolePreset``) for its callout.
* **Cosmetic threads** (BruceWorx_CosmeticThread). A ``CosmeticThread`` object on a
  cylindrical face: designation, internal or external, length, class. It shows the thread
  the way a drawing does (the other diameter as thin dashed circles and lines) and does not
  change the solid. PartDesign Holes carry their thread data already; the object is for
  external threads (studs, shafts) and holes that are not PartDesign Holes (imported STEP).
* **Callouts** (BruceWorx_HoleCallouts). Leader + text on a TechDraw view for every Hole and
  cosmetic thread of the viewed part, written from the model's values; run again to update.
* **Fasteners** (BruceWorx_InsertFastener, BruceWorx_MarkCots): see the fastener section.

Off switch: Preferences/BruceWorx/HardwareCommands (default on). The Fasteners add-on has
its own: Preferences/BruceWorx/FastenersAddon (read by FreeCADInit.py).
"""

from __future__ import annotations

import math
import os

import FreeCAD as App
from bruceworx_slots import slot

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ENABLED_KEY = "HardwareCommands"
ADDON_PREF = "FastenersAddon"
HERE = os.path.dirname(os.path.abspath(__file__))
GROUP = "BruceWorx"
PRESET_PROP = "HolePreset"
CALLOUT_PROP = "BW_Callout"  # on a callout's leader and text: the object it describes
COMMANDS = ("BruceWorx_HolePreset", "BruceWorx_CosmeticThread", "BruceWorx_HoleCallouts",
            "BruceWorx_InsertFastener", "BruceWorx_MarkCots")

_state = {"installed": False}


def _hw():
    from bruceworx import hardware

    return hardware


def enabled() -> bool:
    return bool(App.ParamGet(PREFS_PATH).GetBool(ENABLED_KEY, True))


def _console(text, level="log"):
    getattr(App.Console, {"log": "PrintLog", "msg": "PrintMessage", "warn": "PrintWarning"}[level])(
        f"BruceWorx hardware: {text}\n")


def _vault_root() -> str:
    """The open vault's folder, or "" (no vault, or the client is off)."""
    try:
        import bruceworx_vaultclient as vc

        s = vc.session()
        return s.root if s is not None else ""
    except Exception:  # headless without the client: no vault layer
        return ""


def presets(vault_root=None) -> dict:
    root = _vault_root() if vault_root is None else vault_root
    try:
        return _hw().load_presets(root or None)
    except ValueError as exc:  # a broken vault layer: the built-in presets still work
        _console(f"{exc}; using the built-in presets", "warn")
        return _hw().load_presets(None)


# --- hole presets -------------------------------------------------------------------------


def is_hole(obj) -> bool:
    return getattr(obj, "TypeId", "") == "PartDesign::Hole"


def _set(obj, name, value):
    if name == "ThreadSize":
        value = _hw().thread_size_enum(value, obj.getEnumerationsOfProperty("ThreadSize"))
    if name in ("HoleCutType", "ThreadClass", "ThreadFit", "ThreadType", "DepthType", "DrillPoint",
                "ThreadDepthType"):
        options = obj.getEnumerationsOfProperty(name) or []
        if value not in options:
            raise ValueError(f"{obj.Label}: {name} has no {value!r} (it offers {', '.join(options)})")
    setattr(obj, name, value)


def apply_preset(hole, preset, depth=None):
    """Set ``hole``'s properties from ``preset`` (a hardware.Preset or its name) and remember
    the preset's name on the hole. The caller recomputes."""
    if isinstance(preset, str):
        found = presets().get(preset)
        if found is None:
            raise ValueError(f"no hole preset named {preset!r}")
        preset = found
    if not is_hole(hole):
        raise ValueError(f"{hole.Label} is not a PartDesign Hole")
    for name, value in _hw().hole_properties(preset, depth):
        _set(hole, name, value)
    if PRESET_PROP not in hole.PropertiesList:
        hole.addProperty("App::PropertyString", PRESET_PROP, GROUP, "The BruceWorx hole preset this hole was made from")
    setattr(hole, PRESET_PROP, preset.name)
    return hole


def preset_of(hole, table=None):
    """The hardware.Preset a hole was made from, while its values still match it; else None
    (the hole was edited into something else, so its callout follows the hole)."""
    name = getattr(hole, PRESET_PROP, "") if PRESET_PROP in getattr(hole, "PropertiesList", ()) else ""
    preset = (table or presets()).get(name) if name else None
    if preset is None:
        return None
    for prop, value in _hw().hole_properties(preset):
        if prop in ("ThreadSize", "Depth", "ThreadDepth"):
            continue
        current = getattr(hole, prop, None)
        current = current.Value if hasattr(current, "Value") else current
        if isinstance(value, float) and isinstance(current, float):
            if abs(value - current) > 1e-6:
                return None
        elif current != value:
            return None
    return preset


def hole_values(hole) -> dict:
    """The Hole's values hardware.hole_callout reads, as plain numbers and strings."""
    out = {}
    for name in ("Threaded", "ThreadType", "ThreadSize", "ThreadClass", "Diameter", "DepthType", "Depth",
                 "ThreadDepthType", "ThreadDepth", "HoleCutType", "HoleCutDiameter", "HoleCutDepth",
                 "HoleCutCountersinkAngle"):
        value = getattr(hole, name, None)
        out[name] = value.Value if hasattr(value, "Value") else value
    return out


def hole_count(hole) -> int:
    """How many holes the feature cuts: the circles (and points) of its profile."""
    count = 0
    for prof, subs in getattr(hole, "Profile", None) and [hole.Profile] or []:
        shape = getattr(prof, "Shape", None)
        if shape is None:
            continue
        circles = [e for e in shape.Edges if type(e.Curve).__name__ == "Circle"]
        count += len(circles)
        base = str(getattr(hole, "BaseProfileType", ""))
        if "Point" in base or not circles:
            count += len([v for v in getattr(prof, "Geometry", []) if type(v).__name__ == "Point"])
    return max(count, 1)


def hole_callout(hole) -> str:
    return _hw().hole_callout(hole_values(hole), hole_count(hole), preset_of(hole))


# --- cosmetic threads ---------------------------------------------------------------------


def _cylinder(face):
    surface = face.Surface
    if type(surface).__name__ != "Cylinder":
        raise ValueError("pick a cylindrical face (a hole's wall, or a shaft)")
    return surface


def face_is_internal(face) -> bool:
    """A hole's wall (material outside the cylinder): its normal points at the axis."""
    surface = _cylinder(face)
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    point = face.valueAt(u, v)
    normal = face.normalAt(u, v)
    axis = surface.Axis
    rel = point - surface.Center
    radial = rel - axis * rel.dot(axis)
    return normal.dot(radial) < 0


def guess_thread(face, internal=None):
    """The ISO metric coarse thread a face is cut or turned for: a hole's diameter is the tap
    drill (or minor diameter), a shaft's the major diameter. Returns (Thread, internal)."""
    hw = _hw()
    internal = face_is_internal(face) if internal is None else internal
    dia = 2 * _cylinder(face).Radius
    best, gap = None, None
    for size in hw.METRIC_COARSE:
        t = hw.thread(size)
        for d in ((t.tap_drill, t.minor) if internal else (t.major,)):
            if gap is None or abs(d - dia) < gap:
                best, gap = t, abs(d - dia)
    if best is None or gap > 0.15 * dia:
        raise ValueError(f"Ø{dia:.2f} is not close to an ISO metric thread; type the designation")
    return best, internal


class CosmeticThread:
    """Proxy of a cosmetic thread (Part::FeaturePython): thin geometry, no solid change."""

    def __init__(self, obj):
        self.Type = "BruceWorx::CosmeticThread"
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        props = obj.PropertiesList
        add = [
            ("App::PropertyLinkSubGlobal", "Base", "The cylindrical face the thread is on"),
            ("App::PropertyString", "Designation", "ISO metric thread, e.g. M3 or M3x0.5"),
            ("App::PropertyBool", "Internal", "Internal (tapped hole) or external (bolt, shaft)"),
            ("App::PropertyLength", "Length", "Thread length from the open end (hole entry, free end); 0 = the whole face"),
            ("App::PropertyBool", "FromOtherEnd", "Start the thread at the other end of the face"),
            ("App::PropertyString", "ThreadClass", "Tolerance class: 6H internal, 6g external by default"),
            ("App::PropertyString", "Callout", "The drawing callout, kept up to date"),
        ]
        for kind, name, doc in add:
            if name not in props:
                obj.addProperty(kind, name, "Thread", doc)
        obj.setEditorMode("Callout", 1)

    def onDocumentRestored(self, obj):
        self.setup(obj)

    def dumps(self):
        return {"Type": getattr(self, "Type", "BruceWorx::CosmeticThread")}

    def loads(self, state):
        self.Type = (state or {}).get("Type", "BruceWorx::CosmeticThread")

    def execute(self, obj):
        import Part

        face = thread_face(obj)
        hw = _hw()
        t = hw.thread(obj.Designation)
        surface = _cylinder(face)
        axis = App.Vector(surface.Axis)
        centre = App.Vector(surface.Center)
        # The face's extent along the axis, from its own parameter range.
        _u0, _u1, v0, v1 = face.ParameterRange
        lo, hi = sorted((v0, v1))
        span = hi - lo
        length = obj.Length.Value if obj.Length.Value > 0 else span
        length = min(length, span)
        # The thread starts at the face's open end: the one farther from the solid's centre of
        # mass along the axis (a blind hole's entry, a stud's free end); FromOtherEnd flips it.
        com = _centre_of_mass(obj.Base[0].Shape, face)
        ends = [centre + axis * v for v in (lo, hi)]
        reach = [abs((p - com).dot(axis)) for p in ends]
        if abs(reach[1] - reach[0]) > 1e-6:
            from_hi = reach[1] > reach[0]
        else:  # a free shaft or a through hole: the upper end (then +Y, +X)
            from_hi = (round(ends[1].z, 6), round(ends[1].y, 6), round(ends[1].x, 6)) > (
                round(ends[0].z, 6), round(ends[0].y, 6), round(ends[0].x, 6))
        if obj.FromOtherEnd:
            from_hi = not from_hi
        start, end = (hi, hi - length) if from_hi else (lo, lo + length)
        # The other diameter: a tapped hole's major, a bolt's minor.
        radius = (t.major if obj.Internal else t.minor_external) / 2
        edges = []
        for v in (start, end):
            edges.append(Part.makeCircle(radius, centre + axis * v, axis))
        ref = App.Vector(1, 0, 0) if abs(axis.x) < 0.9 else App.Vector(0, 1, 0)
        side = axis.cross(ref).normalize()
        other = axis.cross(side).normalize()
        for k in range(4):
            angle = k * math.pi / 2
            radial = side * (radius * math.cos(angle)) + other * (radius * math.sin(angle))
            edges.append(Part.makeLine(centre + axis * start + radial, centre + axis * end + radial))
        shape = Part.makeCompound(edges)
        placement = _frame(obj.Base[0], obj)
        if placement is not None:
            shape.Placement = placement.multiply(shape.Placement)
        obj.Shape = shape
        callout = t.callout(length, obj.Internal, obj.ThreadClass or None)
        if obj.Callout != callout:
            obj.Callout = callout


def _frame(base, obj):
    """From the frame ``base``'s shape is in (its parent's) to ``obj``'s container frame: the
    thread sits beside the body, in the App::Part, so links to the part show it too."""
    try:
        to_global = base.getGlobalPlacement().multiply(base.Placement.inverse())
        parent = obj.getParentGeoFeatureGroup()
        if parent is not None:
            return parent.getGlobalPlacement().inverse().multiply(to_global)
        return to_global
    except Exception:
        return None


def _centre_of_mass(shape, face):
    solids = shape.Solids
    if not solids:
        return face.CenterOfMass
    total = sum(sol.Volume for sol in solids)
    acc = App.Vector()
    for sol in solids:
        acc += sol.CenterOfMass * (sol.Volume / total)
    return acc


def thread_face(obj):
    """The Face a cosmetic thread sits on (local to its object)."""
    if not obj.Base or not obj.Base[1]:
        raise ValueError(f"{obj.Label}: no face picked")
    base, subs = obj.Base
    return base.Shape.getElement(subs[0])


class ViewProviderCosmeticThread:
    def __init__(self, vobj):
        vobj.Proxy = self
        self.style(vobj)

    def style(self, vobj):
        try:
            vobj.LineColor = (0.12, 0.44, 0.92)
            vobj.LineWidth = 1.0
            vobj.PointSize = 1.0
            if "LinePattern" in vobj.PropertiesList:
                vobj.LinePattern = 0xF0F0
        except Exception:
            pass

    def attach(self, vobj):
        self.Object = vobj.Object
        # The other diameter lies inside the solid (a bolt's minor) or in the wall (a tapped hole's
        # major): draw it without the depth test, as a drawing shows a hidden thread.
        try:
            from pivy import coin

            node = coin.SoDepthBuffer()
            node.test = False
            vobj.RootNode.insertChild(node, 0)
        except Exception:
            pass

    def getIcon(self):
        return os.path.join(HERE, "BruceWorx_CosmeticThread.svg")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def make_cosmetic_thread(base, sub, designation=None, length=0.0, internal=None, doc=None):
    """A cosmetic thread on face ``sub`` of ``base``; the designation is guessed from the face
    when not given. It goes into the face's body when there is one."""
    import Part  # noqa: F401  -- Part::FeaturePython needs the Part module loaded

    face = base.Shape.getElement(sub)
    guessed, inside = guess_thread(face, internal)
    t = _hw().thread(designation) if designation else guessed
    doc = doc or base.Document
    obj = doc.addObject("Part::FeaturePython", "CosmeticThread")
    CosmeticThread(obj)
    obj.Base = (base, [sub])
    obj.Designation = t.designation
    obj.Internal = inside if internal is None else bool(internal)
    obj.ThreadClass = "6H" if obj.Internal else "6g"
    obj.Length = float(length or 0.0)
    obj.Label = f"Thread {t.size}"
    if App.GuiUp:
        ViewProviderCosmeticThread(obj.ViewObject)
    # Beside the body (in its App::Part), not in it: a body's features form one solid chain.
    body = _body_of(base) or base
    container = body.getParentGeoFeatureGroup() if hasattr(body, "getParentGeoFeatureGroup") else None
    if container is not None and container.TypeId != "PartDesign::Body":
        container.addObject(obj)
    obj.recompute()
    return obj


def _body_of(obj):
    for parent in getattr(obj, "InList", []):
        if parent.TypeId == "PartDesign::Body":
            return parent
    return None


def is_cosmetic_thread(obj) -> bool:
    return getattr(getattr(obj, "Proxy", None), "Type", "") == "BruceWorx::CosmeticThread"


# --- callouts on a drawing ----------------------------------------------------------------


def _source_objects(view):
    """Everything a TechDraw view shows, with what is inside (bodies, their features)."""
    seen, out = set(), []

    def visit(obj):
        if obj is None or obj.Name in seen:
            return
        seen.add(obj.Name)
        out.append(obj)
        target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
        if target is not None and target is not obj:
            visit(target)
        for child in getattr(target, "Group", None) or []:
            visit(child)
        for child in getattr(target, "OutList", None) or []:
            if is_hole(child) or is_cosmetic_thread(child):
                visit(child)

    for obj in list(getattr(view, "Source", []) or []) + list(getattr(view, "XSource", []) or []):
        visit(obj)
    return out


def callout_targets(view):
    """[(object, callout text, 3D anchor)] for the view's Holes and cosmetic threads."""
    out = []
    for obj in _source_objects(view):
        if is_hole(obj):
            anchor = _hole_anchor(obj)
            out.append((obj, hole_callout(obj), anchor))
        elif is_cosmetic_thread(obj):
            face = thread_face(obj)
            bb = obj.Shape.BoundBox if not obj.Shape.isNull() else face.BoundBox
            out.append((obj, obj.Callout, bb.Center))
    return out


def _hole_anchor(hole):
    """A point on the first hole's rim, in the document's frame."""
    try:
        prof = hole.Profile[0]
        circles = [e for e in prof.Shape.Edges if type(e.Curve).__name__ == "Circle"]
        if circles:
            c = circles[0].Curve
            return c.Center + _perp(c.Axis) * (hole.Diameter.Value / 2)
        return prof.Shape.BoundBox.Center
    except Exception:
        return hole.Shape.BoundBox.Center


def _perp(axis):
    ref = App.Vector(1, 0, 0) if abs(axis.x) < 0.9 else App.Vector(0, 1, 0)
    return axis.cross(ref).normalize().cross(axis).normalize()


CALLOUT_PT = 10  # 3.5 mm text, the usual drawing size; TechDraw converts "font-size:NNpt;" to scene units


def _rich(text) -> str:
    lines = "".join(f"<p style=\"margin:0; font-size:{CALLOUT_PT}pt;\">{_html(line)}</p>"
                    for line in text.split("\n"))
    return f"<html><body style=\"font-family:osifont; font-size:{CALLOUT_PT}pt;\">{lines}</body></html>"


def _text_box(text):
    """Rough (width, height) in mm of a callout at CALLOUT_PT (osifont is narrow: 0.5 em)."""
    em = CALLOUT_PT * 0.353
    lines = text.split("\n")
    return max(len(line) for line in lines) * 0.5 * em, len(lines) * 1.25 * em


def _html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def add_callouts(view, offset=(12.0, 10.0)):
    """Leader + rich text for each hole and cosmetic thread of ``view``; existing callouts of
    the same object get the new text (positions stay where they were dragged). Returns the
    text objects."""
    doc = view.Document
    page = _page_of(view)
    existing = {}
    for obj in doc.Objects:
        if CALLOUT_PROP in obj.PropertiesList and getattr(obj, "AnnoParent", None) is not None:
            existing[(obj.AnnoParent.Name if obj.AnnoParent else "", getattr(obj, CALLOUT_PROP))] = obj
    out = []
    for target, text, anchor in callout_targets(view):
        if not text:
            continue
        rich = _rich(text)
        leader = next((o for o in doc.Objects if CALLOUT_PROP in o.PropertiesList
                       and getattr(o, CALLOUT_PROP) == target.Name and getattr(o, "LeaderParent", None) == view),
                      None)
        width, height = _text_box(text)
        if leader is None:
            import bruceworx_drawing_callouts as follow

            # page mm from the view's centre; a view is drawn centred on its shape, not on the model origin
            x, y = follow._arrow(view, anchor)
            side = 1 if x >= 0 else -1  # away from the view's centre, up
            leader = doc.addObject("TechDraw::DrawLeaderLine", "Callout")
            leader.LeaderParent = view
            leader.X, leader.Y = x, y
            # Way points are page deltas from the arrow, Y down (Qt's scene); a 4 mm shoulder.
            leader.WayPoints = [App.Vector(0, 0, 0), App.Vector(side * offset[0], -offset[1], 0),
                                App.Vector(side * (offset[0] + 4), -offset[1], 0)]
            leader.addProperty("App::PropertyString", CALLOUT_PROP, GROUP, "The hole or thread this callout describes")
            setattr(leader, CALLOUT_PROP, target.Name)
            if page is not None:
                page.addView(leader)
        anno = existing.get((leader.Name, target.Name))
        if anno is None:
            anno = doc.addObject("TechDraw::DrawRichAnno", "CalloutText")
            anno.AnnoParent = leader
            anno.ShowFrame = False
            side = 1 if leader.WayPoints[-1].x >= 0 else -1
            # Its X, Y (the text's centre) are from the leader's arrow, Y up: beside the shoulder.
            anno.X = leader.WayPoints[-1].x + side * (1.5 + width / 2)
            anno.Y = -leader.WayPoints[-1].y
            anno.addProperty("App::PropertyString", CALLOUT_PROP, GROUP, "The hole or thread this callout describes")
            setattr(anno, CALLOUT_PROP, target.Name)
            if page is not None:
                page.addView(anno)
        if anno.AnnoText != rich:
            anno.AnnoText = rich
        out.append(anno)
    doc.recompute()
    return out


def _page_of(view):
    """The view's page, also through its projection group (every New Drawing view is in one)."""
    import bruceworx_drawing_callouts as follow

    return follow.page_of(view)


# --- fasteners (the bundled Fasteners add-on) ----------------------------------------------

# Offered first in Insert Fastener: what a mechanical / 3D-printing shop uses most.
COMMON_TYPES = ("ISO4762", "ISO7380-1", "ISO10642", "ISO4017", "ISO4026", "ISO4032", "ISO7040",
                "ISO4035", "ISO7089", "ISO7092", "ISO7093-1", "IUTHeatInsert", "ISO8734", "ISO2338")
FASTENER_LOCKED = ("Type", "Diameter", "Length", "LengthCustom")  # one vault item, one size
KEY_PROP = "HardwareKey"


def addon_available() -> bool:
    """The Fasteners add-on can be imported (bundled as Mod/fasteners, or a user copy)."""
    if not App.ParamGet(PREFS_PATH).GetBool(ADDON_PREF, True):
        return False
    try:
        import FastenersCmd  # noqa: F401
        import ScrewMaker  # noqa: F401
    except Exception:
        return False
    return True


def _fcmd():
    import FastenersCmd

    return FastenersCmd


def _maker():
    import ScrewMaker

    return ScrewMaker.Instance


def fastener_types():
    """[(type, description)]: the common ones first, then the add-on's others by name."""
    table = _fcmd().FSScrewCommandTable
    rows = [(t, _fcmd().FSGetDescription(t)) for t in table]
    order = {t: i for i, t in enumerate(COMMON_TYPES)}
    return sorted(rows, key=lambda row: (order.get(row[0], len(order)), row[0]))


def fastener_sizes(ftype):
    return [d for d in _maker().GetAllDiams(ftype) if d not in ("Auto", "Custom")]


def length_kind(ftype) -> str:
    """"list": a standard length from the table; "free": any length (rods); "": no length in
    the part's identity (nuts, washers, inserts: one item per size)."""
    params = _fcmd().FSGetParams(ftype)
    if "Length" in params or "LenByDiamAndWidth" in params:
        return "list"
    if "lengthArbitrary" in params and "ExternalDiam" not in params:  # an insert's length is its size's
        return "free"
    return ""


def fastener_lengths(ftype, diameter):
    if length_kind(ftype) != "list":
        return []
    return [length for length in _maker().GetAllLengths(ftype, diameter, False) if length != "Custom"]


def is_fastener(obj) -> bool:
    proxy = getattr(obj, "Proxy", None)
    return proxy is not None and type(proxy).__module__ == "FastenersCmd" and hasattr(obj, "Diameter")


def fastener_params(obj):
    """(type, diameter, length or None) of a fastener object."""
    ftype = str(obj.Type)
    length = None
    kind = length_kind(ftype)
    if kind == "list":
        length = str(obj.Length)
        if length == "Custom" and hasattr(obj, "LengthCustom"):
            length = obj.LengthCustom.Value
    elif kind == "free" and hasattr(obj, "Length"):
        length = obj.Length.Value if hasattr(obj.Length, "Value") else obj.Length
    return ftype, str(obj.Diameter), length


def fastener_key_of(obj) -> str:
    return _hw().fastener_key(*fastener_params(obj))


def fastener_description_of(obj) -> str:
    ftype, dia, length = fastener_params(obj)
    return _hw().fastener_description(ftype, dia, length, _fcmd().FSScrewCommandTable.get(ftype, ("",))[0])


def make_fastener(doc, ftype, diameter, length=None, container=None):
    """A fastener object of the add-on (as its own Add command makes one), not attached."""
    fcmd = _fcmd()
    if ftype not in fcmd.FSScrewCommandTable:
        raise ValueError(f"the Fasteners add-on has no type {ftype!r}")
    name = _maker().GetTypeName(ftype)
    obj = doc.addObject("Part::FeaturePython", name if name and name != "None" else "Fastener")
    if container is not None:
        container.addObject(obj)
    fcmd.FSScrewObject(obj, ftype, None)
    if diameter not in obj.getEnumerationsOfProperty("Diameter"):
        raise ValueError(f"{ftype} has no size {diameter!r}")
    obj.Diameter = diameter
    kind = length_kind(ftype)
    if kind == "list" and length is not None:
        value = float(str(length).replace("mm", "").strip())
        text = _hw()._num(value)
        custom = "LengthCustom" in obj.PropertiesList
        options = _maker().GetAllLengths(ftype, diameter, custom)
        obj.Length = options  # the lengths of this size (the object starts with the first size's)
        match = next((o for o in options if o.replace(" ", "") in (text, text + "mm")), None)
        if match is not None:
            obj.Length = match
        elif custom and value > 0:
            obj.Length = "Custom"  # a length the standard's table does not list (M3x40 is DIN 912's)
            obj.LengthCustom = value
        else:
            raise ValueError(f"{ftype} {diameter} has no length {text} (it has {', '.join(options)})")
    elif kind == "free" and length is not None:
        obj.Length = float(length)
    obj.Label = fastener_description_of(obj)
    if App.GuiUp:
        try:
            fcmd.FSViewProviderTree(obj.ViewObject)
        except Exception:
            pass
    return obj


def _steel():
    """A steel material card for a fastener's mass (the first of these the library has)."""
    try:
        import Materials

        manager = Materials.MaterialManager()
        for name in ("Steel-Generic", "Steel-1C45", "Steel-S235JR"):
            for uuid, mat in manager.Materials.items():
                if getattr(mat, "Name", "") == name:
                    return manager.getMaterial(uuid)
    except Exception:
        pass
    return None


def make_fastener_file(path, pn, ftype, diameter, length, description, vault_root):
    """Write the COTS part file of a fastener: an App::Part holding the add-on's object, with
    the vault's part properties (ItemType cots, the description, revision 01) and the
    hardware key; Type, size and length read-only (one item, one size). Saved and closed."""
    from bruceworx import partprops, pdm

    doc = App.newDocument(pn.replace("-", "_"), hidden=True)
    try:
        doc.Label = pn
        main = doc.addObject("App::Part", "Part")
        main.Label = description
        fastener = make_fastener(doc, ftype, diameter, length, container=main)
        steel = _steel()
        if steel is not None and "ShapeMaterial" in fastener.PropertiesList:
            fastener.ShapeMaterial = steel
        report = partprops.load_schema(extra_layers=partprops.vault_layers(vault_root)).apply(main)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise ValueError("; ".join(clash))
        main.PartNumber = pn
        main.Revision = pdm.first_prerelease()
        if "Description" in main.PropertiesList:
            main.Description = description
        if "ItemType" in main.PropertiesList:
            main.ItemType = "cots"
        main.addProperty("App::PropertyString", KEY_PROP, GROUP, "The standard hardware this COTS item is")
        setattr(main, KEY_PROP, _hw().fastener_key(ftype, diameter, length))
        main.setEditorMode(KEY_PROP, 1)
        for prop in FASTENER_LOCKED:
            if prop in fastener.PropertiesList:
                fastener.setEditorMode(prop, 1)
        doc.recompute()
        doc.saveAs(path)
    finally:
        App.closeDocument(doc.Name)


HARDWARE_CATEGORY = "Hardware"


def ensure_cots_item(s, ftype, diameter, length=None, program=""):
    """The vault part number of this fastener, made (numbered, its file checked in as WIP 01)
    when the vault has none; recorded in the vault's hardware.json. ``s``: the vault client's
    Session (or anything with .vault and .root). Returns (part number, made)."""
    from bruceworx import vault_setup

    hw = _hw()
    key = hw.fastener_key(ftype, diameter, length)
    catalogue = hw.load_catalogue(s.root)
    pn = hw.find_item(key, catalogue, s.vault.items)
    made = False
    description = hw.fastener_description(ftype, diameter, length,
                                          _fcmd().FSScrewCommandTable.get(ftype, ("",))[0])
    if pn:
        description = s.vault.items[pn].description or description
    else:
        def make(path, number):
            make_fastener_file(path, number, ftype, diameter, length, description, s.root)

        # A new bolt is filed under the Vault panel's "Hardware" category (an existing item keeps its own).
        pn = vault_setup.new_item(s.vault, "cots", description, make, program, category=HARDWARE_CATEGORY)
        made = True
    hw.record(catalogue, key, pn, description, source="Fasteners",
              standard=hw._standard(hw._type_code(ftype)), size=diameter,
              length=hw._num(float(length)) if length not in (None, "") else "")
    hw.save_catalogue(s.root, catalogue)
    return pn, made


def _part_of(doc):
    """The App::Part (or assembly) a vault part file is about."""
    for obj in doc.Objects:
        if obj.TypeId in ("App::Part", "Assembly::AssemblyObject") and "PartNumber" in obj.PropertiesList:
            return obj
    return next((o for o in doc.Objects if o.TypeId == "App::Part"), None)


def link_part(assembly, path, placement=None, label=""):
    """An App::Link in ``assembly`` to the part of the file at ``path`` (opened if needed). The
    assembly's file must be saved: FreeCAD stores the link relative to it."""
    if not assembly.Document.FileName:
        raise ValueError("Save the assembly first: the fastener is linked from its file")
    doc = next((d for d in App.listDocuments().values() if d.FileName and os.path.samefile(d.FileName, path)), None)
    if doc is None:  # hidden: the part comes in through the link, not as another tab
        doc = App.openDocument(path, hidden=True)
        App.setActiveDocument(assembly.Document.Name)
    part = _part_of(doc)
    if part is None:
        raise ValueError(f"{path} has no part to link")
    link = assembly.Document.addObject("App::Link", "Fastener")
    link.setLink(part)
    if label:
        link.Label = label
    assembly.addObject(link)
    if placement is not None:
        link.Placement = placement
    return link


def insert_fastener(s, assembly, ftype, diameter, length=None, placement=None, program=""):
    """Insert Fastener: the vault's COTS item for this fastener (made if new), its latest file
    fetched to the workspace, linked into ``assembly``. Returns (link, part number, made)."""
    pn, made = ensure_cots_item(s, ftype, diameter, length, program)
    if hasattr(s, "refresh"):
        s.refresh()
    path = s.work.get_latest(pn)
    link = link_part(assembly, path, placement, link_label(ftype, diameter, length, assembly.Document))
    return link, pn, made


def link_label(ftype, diameter, length=None, doc=None) -> str:
    """"M3x40 5": the size and the next number free in ``doc``, as the gearbox's own screws are
    named ("M3x40 1" ... "M3x40 4"); nuts and washers "M3 nut 1", "M3 washer 1"."""
    import re

    key = _hw().fastener_key(ftype, diameter, length)
    size = key.split("-", 1)[1] if "-" in key else key
    if length in (None, ""):
        word = _hw().TYPE_NAMES.get(_hw()._type_code(ftype), "").split(",")[0].split(" ")[-1].lower()
        size = f"{size} {word}" if word else size
    used = [0]
    for obj in getattr(doc, "Objects", []):
        found = re.fullmatch(re.escape(size) + r" (\d+)", obj.Label)
        if found:
            used.append(int(found.group(1)))
    return f"{size} {max(used) + 1}"


def mark_cots(s, pns, documents=None):
    """Mark as COTS: set each vault item's type to cots (set_item_type), record its hardware
    key in hardware.json when its description names one, and set ItemType in the open files.
    Returns the part numbers changed."""
    hw = _hw()
    catalogue = hw.load_catalogue(s.root)
    changed = []
    for pn in pns:
        item = s.vault.items[pn]
        if item.item_type != "cots":
            s.vault.set_item_type(pn, "cots")
            changed.append(pn)
        key = hw.match_description(item.description)
        if key and key not in catalogue:
            hw.record(catalogue, key, pn, item.description, source="vault")
    hw.save_catalogue(s.root, catalogue)
    from bruceworx import partprops

    types = {pn: "cots" for pn in pns}
    for doc in documents if documents is not None else App.listDocuments().values():
        partprops.sync_item_types(doc, types)
    return changed


# --- commands ---------------------------------------------------------------------------


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _window():
    import FreeCADGui as Gui

    return Gui.getMainWindow()


def _message(title, text, kind="information"):
    _QtCore, _QtGui, QtWidgets = _qt()
    getattr(QtWidgets.QMessageBox, kind)(_window(), title, text)


def _session():
    try:
        import bruceworx_vaultclient as vc

        return vc.session()
    except Exception:
        return None


def _transaction(doc, name, fn):
    doc.openTransaction(name)
    try:
        result = fn()
    except Exception:
        doc.abortTransaction()
        raise
    doc.commitTransaction()
    doc.recompute()
    return result


def _selected_ex():
    import FreeCADGui as Gui

    return Gui.Selection.getSelectionEx("", 0)


def _sub_target(sel):
    """(object whose shape has the element, element name) from a SelectionObject: the sub-object
    path's last object for a selection inside a Part or assembly."""
    names = list(sel.SubElementNames)
    if not names:
        return None, ""
    path = names[0]
    obj = sel.Object
    parts = path.split(".")
    for name in parts[:-1]:
        child = obj.Document.getObject(name) if obj.Document.getObject(name) else None
        if child is not None:
            obj = child
    target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
    return target, parts[-1]


class PresetDialog:
    """Pick a hole preset: kind, then the preset, its numbers, and an optional depth."""

    KIND_NAMES = {"heatset": "Heat-set insert (3D print)", "tapped": "Tapped", "clearance": "Clearance",
                  "counterbore": "Counterbore", "countersink": "Countersink"}

    def __init__(self, table, current=None):
        _QtCore, _QtGui, QtWidgets = _qt()
        self.table = table
        self.dialog = QtWidgets.QDialog(_window())
        self.dialog.setWindowTitle("Hole Preset")
        form = QtWidgets.QFormLayout(self.dialog)
        self.kind = QtWidgets.QComboBox()
        for kind in _hw().KINDS:
            if any(p.kind == kind for p in table.values()):
                self.kind.addItem(self.KIND_NAMES.get(kind, kind), kind)
        form.addRow("Kind", self.kind)
        self.preset = QtWidgets.QComboBox()
        form.addRow("Preset", self.preset)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        form.addRow("", self.info)
        self.depth = QtWidgets.QDoubleSpinBox()
        self.depth.setRange(0.0, 1000.0)
        self.depth.setDecimals(2)
        self.depth.setSuffix(" mm")
        self.depth.setSpecialValueText("preset's depth")
        form.addRow("Depth", self.depth)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.dialog.accept)
        buttons.rejected.connect(self.dialog.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(slot(self._fill))
        self.preset.currentIndexChanged.connect(slot(self._show))
        if current is not None:
            self.kind.setCurrentIndex(max(0, self.kind.findData(current.kind)))
        self._fill()
        if current is not None:
            self.preset.setCurrentIndex(max(0, self.preset.findText(current.name)))

    def _fill(self, *_args):
        kind = self.kind.currentData()
        self.preset.clear()
        for name, preset in self.table.items():
            if preset.kind == kind:
                self.preset.addItem(name)
        self._show()

    def _show(self, *_args):
        preset = self.table.get(self.preset.currentText())
        if preset is None:
            self.info.setText("")
            return
        text = preset.describe()
        if preset.kind == "heatset" and not preset.checked:
            text += "\n(Numbers not yet checked against the maker's data sheet.)"
        if preset.layer == "vault":
            text += "\n(From this vault's hole_presets.json.)"
        self.info.setText(text)

    def run(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        try:
            if self.dialog.exec() != QtWidgets.QDialog.Accepted:
                return None
            preset = self.table.get(self.preset.currentText())
            depth = self.depth.value() or None
            return (preset, depth) if preset is not None else None
        finally:
            self.dialog.deleteLater()


class _HolePresetCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_HolePreset.svg"), "MenuText": "Hole Preset",
                "ToolTip": "A hole from a preset: heat-set insert holes for 3D-printed parts (CNC Kitchen, "
                           "ruthex, M2-M5), tapped, clearance, counterbore and countersink holes. Select a Hole "
                           "to change it, or a sketch of circles to make one."}

    def IsActive(self):
        return App.ActiveDocument is not None and enabled()

    def Activated(self):
        import FreeCADGui as Gui

        sel = Gui.Selection.getSelection()  # resolved: the hole or sketch itself, inside its body
        holes = [o for o in sel if is_hole(o)]
        sketches = [o for o in sel if o.TypeId == "Sketcher::SketchObject"]
        if not holes and not sketches:
            _message("Hole Preset", "Select a Hole to change, or a sketch with the hole circles.")
            return
        table = presets()
        answer = PresetDialog(table, preset_of(holes[0], table) if holes else None).run()
        if answer is None:
            return
        preset, depth = answer
        doc = (holes or sketches)[0].Document

        def apply():
            targets = list(holes)
            for sketch in sketches:
                body = _body_of(sketch)
                if body is None:
                    raise ValueError(f"{sketch.Label} is not in a body")
                hole = body.newObject("PartDesign::Hole", "Hole")
                hole.Profile = sketch
                sketch.Visibility = False
                targets.append(hole)
            for hole in targets:
                apply_preset(hole, preset, depth)
                hole.Label = preset.name
            return targets

        try:
            _transaction(doc, "Hole Preset", apply)
        except Exception as exc:
            _message("Hole Preset", str(exc), "warning")


class _CosmeticThreadCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_CosmeticThread.svg"), "MenuText": "Cosmetic Thread",
                "ToolTip": "Record an ISO metric thread on a cylindrical face (a shaft, a stud, a hole that is not "
                           "a PartDesign Hole). Shown dashed, as on a drawing; the solid does not change, and the "
                           "drawing callout comes from it."}

    def IsActive(self):
        return App.ActiveDocument is not None and enabled()

    def Activated(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        picked = None
        for sel in _selected_ex():
            obj, sub = _sub_target(sel)
            if obj is not None and sub.startswith("Face"):
                picked = (obj, sub)
                break
        if picked is None:
            _message("Cosmetic Thread", "Select the cylindrical face of the shaft or hole first.")
            return
        obj, sub = picked
        try:
            guess, internal = guess_thread(obj.Shape.getElement(sub))
        except ValueError as exc:
            guess, internal = None, face_is_internal(obj.Shape.getElement(sub)) if sub else True
            note = str(exc)
        else:
            note = ""
        dialog = QtWidgets.QDialog(_window())
        dialog.setWindowTitle("Cosmetic Thread")
        form = QtWidgets.QFormLayout(dialog)
        size = QtWidgets.QComboBox()
        size.setEditable(True)
        size.addItems([_hw().thread(s).designation for s in _hw().METRIC_COARSE])
        if guess is not None:
            size.setCurrentText(guess.designation)
        form.addRow("Thread", size)
        inside = QtWidgets.QCheckBox("Internal (tapped hole)")
        inside.setChecked(internal)
        form.addRow("", inside)
        length = QtWidgets.QDoubleSpinBox()
        length.setRange(0.0, 10000.0)
        length.setSuffix(" mm")
        length.setSpecialValueText("whole face")
        form.addRow("Length", length)
        if note:
            form.addRow("", QtWidgets.QLabel(note))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            _transaction(obj.Document, "Cosmetic Thread", lambda: make_cosmetic_thread(
                obj, sub, size.currentText(), length.value(), inside.isChecked()))
        except Exception as exc:
            _message("Cosmetic Thread", str(exc), "warning")


class _HoleCalloutsCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_HoleCallouts.svg"), "MenuText": "Hole Callouts",
                "ToolTip": "Callouts for every hole and cosmetic thread of the selected drawing view, written from "
                           "the model (thread, depth, counterbore, heat-set insert). Run again to update them."}

    def IsActive(self):
        return App.ActiveDocument is not None and enabled()

    def Activated(self):
        import FreeCADGui as Gui

        views = [o for o in Gui.Selection.getSelection() if o.isDerivedFrom("TechDraw::DrawViewPart")]
        if not views:
            _message("Hole Callouts", "Select a drawing view of the part first.")
            return
        try:
            made = _transaction(views[0].Document, "Hole Callouts",
                                lambda: [a for v in views for a in add_callouts(v)])
        except Exception as exc:
            _message("Hole Callouts", str(exc), "warning")
            return
        if not made:
            _message("Hole Callouts", "The view shows no Holes or cosmetic threads.")


class FastenerDialog:
    """Type, size and length from the add-on's tables."""

    def __init__(self, ftype="ISO4762", diameter="M3", length="10"):
        _QtCore, _QtGui, QtWidgets = _qt()
        self.dialog = QtWidgets.QDialog(_window())
        self.dialog.setWindowTitle("Insert Fastener")
        form = QtWidgets.QFormLayout(self.dialog)
        self.type = QtWidgets.QComboBox()
        for code, text in fastener_types():
            self.type.addItem(text, code)
        form.addRow("Type", self.type)
        self.size = QtWidgets.QComboBox()
        form.addRow("Size", self.size)
        self.length = QtWidgets.QComboBox()
        self.length.setEditable(True)  # a length the standard does not list (M3x40 is DIN 912's)
        self.length.setToolTip("The standard's lengths; type another one for a custom length")
        self.length_label = QtWidgets.QLabel("Length")
        form.addRow(self.length_label, self.length)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        form.addRow("", self.info)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Insert")
        buttons.accepted.connect(self.dialog.accept)
        buttons.rejected.connect(self.dialog.reject)
        form.addRow(buttons)
        self.type.currentIndexChanged.connect(slot(self._sizes))
        self.size.currentIndexChanged.connect(slot(self._lengths))
        self.length.currentIndexChanged.connect(slot(self._show))
        self.length.editTextChanged.connect(slot(self._show))
        self.type.setCurrentIndex(max(0, self.type.findData(ftype)))
        self._sizes()
        self.size.setCurrentIndex(max(0, self.size.findText(diameter)))
        self._lengths()
        if self.length.findText(length) >= 0:
            self.length.setCurrentIndex(self.length.findText(length))
        elif self.length.count():
            self.length.setEditText(length)

    def _sizes(self, *_args):
        self.size.blockSignals(True)
        self.size.clear()
        self.size.addItems(fastener_sizes(self.type.currentData()))
        self.size.blockSignals(False)
        self._lengths()

    def _lengths(self, *_args):
        self.length.blockSignals(True)
        self.length.clear()
        lengths = fastener_lengths(self.type.currentData(), self.size.currentText())
        self.length.addItems(lengths)
        self.length.setVisible(bool(lengths))
        self.length_label.setVisible(bool(lengths))
        self.length.blockSignals(False)
        self._show()

    def values(self):
        lengths = self.length.count() > 0  # (not isVisible: false until the dialog is shown)
        return self.type.currentData(), self.size.currentText(), (self.length.currentText() if lengths else None)

    def _show(self, *_args):
        try:
            ftype, dia, length = self.values()
            hw = _hw()
            key = hw.fastener_key(ftype, dia, length)
            s = _session()
            pn = hw.find_item(key, hw.load_catalogue(s.root), s.vault.items) if s is not None else ""
            text = hw.fastener_description(ftype, dia, length, _fcmd().FSScrewCommandTable[ftype][0])
            text += f"\nVault item: {pn}" if pn else "\nNew COTS item in the vault"
        except Exception as exc:
            text = str(exc)
        self.info.setText(text)

    def run(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        try:
            return self.values() if self.dialog.exec() == QtWidgets.QDialog.Accepted else None
        finally:
            self.dialog.deleteLater()


def _active_assembly():
    import FreeCADGui as Gui

    try:
        view = Gui.ActiveDocument.ActiveView
        for context in ("assembly", "part"):
            found = view.getActiveObject(context)
            if found is not None:
                return found
    except Exception:
        pass
    doc = App.ActiveDocument
    return next((o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"), None) if doc else None


class _InsertFastenerCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_InsertFastener.svg"), "MenuText": "Insert Fastener",
                "ToolTip": "A standard screw, nut, washer or insert (ISO / DIN, from the bundled Fasteners add-on) "
                           "into the active assembly, as the vault's COTS item for that size: the same size is the "
                           "same part number everywhere. Select a hole's rim to place it there."}

    def IsActive(self):
        return App.ActiveDocument is not None and enabled() and addon_available()

    def Activated(self):
        s = _session()
        if s is None:
            _message("Insert Fastener", "Open a vault first (Preferences > Vector Labs, or the Start page): each "
                     "fastener is a COTS item of the vault.")
            return
        assembly = _active_assembly()
        if assembly is None:
            _message("Insert Fastener", "Open (or activate) the assembly to put the fastener in.")
            return
        placement = None
        for sel in _selected_ex():
            obj, sub = _sub_target(sel)
            if obj is not None and sub.startswith("Edge"):
                try:
                    placement = _edge_placement_in(assembly, sel)
                except ValueError:
                    placement = None
                break
        answer = FastenerDialog().run()
        if answer is None:
            return
        ftype, dia, length = answer
        doc = assembly.Document
        try:
            link, pn, made = _transaction(doc, "Insert Fastener",
                                          lambda: insert_fastener(s, assembly, ftype, dia, length, placement))
        except Exception as exc:
            _message("Insert Fastener", str(exc), "warning")
            return
        try:
            import bruceworx_vaultclient as vc

            vc.changed(refresh=True)
        except Exception:
            pass
        _console(f"{'new COTS item' if made else 'vault item'} {pn} ({link.Label}) inserted", "msg")


def _edge_placement_in(assembly, sel):
    """The fastener placement for a selected circular edge, in the assembly's frame."""
    sub = sel.SubElementNames[0]
    shape = sel.Object.getSubObject(sub)  # in the selection root's (document's) frame
    curve = shape.Curve
    if type(curve).__name__ != "Circle":
        raise ValueError("not a circle")
    axis = App.Vector(curve.Axis)
    place = App.Placement(curve.Center, App.Rotation(App.Vector(0, 0, 1), -axis))
    try:
        frame = assembly.getGlobalPlacement()
        place = frame.inverse().multiply(place)
    except Exception:
        pass
    return place


class _MarkCotsCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_MarkCots.svg"), "MenuText": "Mark as COTS…",
                "ToolTip": "The vault's parts whose description says they are bought (screws, bearings, pins, "
                           "'(COTS)'): tick them to make them COTS items, so BOMs mark them bought and the section "
                           "view leaves them uncut."}

    def IsActive(self):
        return enabled()

    def Activated(self):
        _QtCore, _QtGui, QtWidgets = _qt()
        s = _session()
        if s is None:
            _message("Mark as COTS", "Open a vault first (Preferences > Vector Labs, or the Start page).")
            return
        rows = _hw().cots_candidates(s.vault.items)
        if not rows:
            _message("Mark as COTS", "No part in the vault looks bought: nothing to mark.")
            return
        dialog = QtWidgets.QDialog(_window())
        dialog.setWindowTitle("Mark as COTS")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("These parts look bought. Ticked ones become COTS items:"))
        listing = QtWidgets.QListWidget()
        for pn, key, text in rows:
            item = QtWidgets.QListWidgetItem(f"{pn}   {text}" + (f"   [{key}]" if key else ""))
            item.setData(256, pn)
            item.setCheckState(_QtCore.Qt.Checked)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Mark as COTS")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(640, 360)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        pns = [listing.item(i).data(256) for i in range(listing.count())
               if listing.item(i).checkState() == _QtCore.Qt.Checked]
        try:
            changed = mark_cots(s, pns)
        except Exception as exc:
            _message("Mark as COTS", str(exc), "warning")
            return
        try:
            import bruceworx_vaultclient as vc

            vc.changed(refresh=True)
        except Exception:
            pass
        _console(f"marked as COTS: {', '.join(changed) or 'nothing new'}", "msg")


class _Manipulator:
    """Hole Callouts on TechDraw's annotation toolbar; the Design commands come from
    workspaces.json (Solid > Create, Assembly > Assemble, Utilities > Properties)."""

    def modifyToolBars(self):
        if not enabled():
            return []
        return [{"insert": "BruceWorx_HoleCallouts", "toolItem": "TechDraw_RichTextAnnotation"}]


def install():
    """Register the commands and the TechDraw toolbar entry (once)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_HolePreset", _HolePresetCommand())
    Gui.addCommand("BruceWorx_CosmeticThread", _CosmeticThreadCommand())
    Gui.addCommand("BruceWorx_HoleCallouts", _HoleCalloutsCommand())
    Gui.addCommand("BruceWorx_InsertFastener", _InsertFastenerCommand())
    Gui.addCommand("BruceWorx_MarkCots", _MarkCotsCommand())
    _state["manipulator"] = _Manipulator()
    Gui.addWorkbenchManipulator(_state["manipulator"])
    _state["installed"] = True
