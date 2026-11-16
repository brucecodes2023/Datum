# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bought parts with their mounting frame (roadmap ROB-01): the COTS library and insert.

* **Library** (cots_library.json beside this file, plus the vault's own cots_library.json on top):
  a bought part's envelope, mass, hole pattern and thread. Every number names the source it was
  read from and whether it was checked against the maker's drawing; a number nobody could source
  is absent, never guessed (the same rule as the hole presets, MOD-05).
* **Part** (make_part): an App::Part with the placeholder solid, the typed mass (DatasheetMass, so
  the mass report uses it) and a coordinate system **Mount**: origin at the centre of the hole
  pattern on the mounting face, +Z into the part.
* **Insert** (insert): the part arrives in the assembly. A saved assembly gets one file per bought
  part in a ``cots`` folder beside it and a link to it; an unsaved one gets the part in the document.
* **Seat** (make_seat): on a flat face of the receiving body, the matching clearance holes (the
  MOD-05 "Clearance Mx" preset for the part's thread) and a coordinate system **Seat** on them.
* **Join** (join): a Fixed joint Mount on Seat, so the part sits on its holes.

Commands: BruceWorx_CotsInsert, BruceWorx_CotsSeat, BruceWorx_CotsJoin.
Off switch: Preferences/BruceWorx/CotsLibrary (default on).
"""

from __future__ import annotations

import json
import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "CotsLibrary"
HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_FILE = "cots_library.json"
SCHEMA = "bruceworx.cots-library"
FOLDER = "cots"  # beside a saved assembly: one file per bought part
GROUP = "BruceWorx"
KEY_PROP = "CotsKey"
SEAT_PROP = "CotsSeat"  # on a Seat coordinate system: the key of the part it is for
MOUNT = "Mount"
SEAT = "Seat"
COMMANDS = ("BruceWorx_CotsInsert", "BruceWorx_CotsSeat", "BruceWorx_CotsJoin")
SHAPES = ("box", "cylinder", "step")
_state = {"installed": False, "dialog": None}


class CotsError(Exception):
    """Something to tell the user in one sentence."""


def _app():
    import FreeCAD

    return FreeCAD


def enabled() -> bool:
    return bool(_app().ParamGet(PREFS_PATH).GetBool(PREF, True))


# ----- the library ----------------------------------------------------------------------------------


class Value:
    """One sourced number (or list of numbers, or a thread name)."""

    __slots__ = ("field", "value", "source", "checked", "note")

    def __init__(self, field, value, source, checked, note=""):
        self.field, self.value, self.source, self.checked, self.note = field, value, source, checked, note


class Entry:
    """A bought part: ``values`` is every sourced number by field name (mass_g, size, pitch, thread, ...)."""

    def __init__(self, key, name, category, manufacturer, mpn, shape, values, raw, layer):
        self.key, self.name, self.category = key, name, category
        self.manufacturer, self.mpn, self.shape = manufacturer, mpn, shape
        self.values, self.raw, self.layer = values, raw, layer

    def get(self, field, default=None):
        found = self.values.get(field)
        return found.value if found is not None else default

    @property
    def mass_g(self):
        return self.get("mass_g")

    @property
    def thread(self):
        return self.get("thread", "")

    def unchecked(self):
        """The fields whose numbers were not read from the maker's own drawing."""
        return [v.field for v in self.values.values() if not v.checked]

    def holes(self):
        """[(x, y)] of the mounting holes in the Mount frame."""
        explicit = self.get("holes")
        if explicit:
            return [(float(x), float(y)) for x, y in explicit]
        pitch = self.get("pitch")
        if not pitch:
            return []
        px, py = float(pitch[0]) / 2, float(pitch[1]) / 2
        return [(-px, -py), (px, -py), (px, py), (-px, py)]

    def describe(self):
        """A few lines for a list or a report: what it is, its numbers, and what is not checked."""
        lines = [self.name + (f" ({self.manufacturer} {self.mpn})".rstrip() if self.manufacturer or self.mpn else "")]
        for v in self.values.values():
            shown = " x ".join(_num(x) for x in v.value) if isinstance(v.value, (list, tuple)) and v.value \
                and not isinstance(v.value[0], (list, tuple)) else str(v.value)
            flag = "" if v.checked else "  [not checked]"
            lines.append(f"  {v.field}: {shown}{flag}  ({v.source.get('title', '?')}"
                         + (f"; {v.note}" if v.note else "") + ")")
        return "\n".join(lines)

    def as_dict(self):
        """What an agent needs: the numbers, and which of them are not checked."""
        return {"key": self.key, "name": self.name, "category": self.category, "manufacturer": self.manufacturer,
                "mpn": self.mpn, "mass_g": self.mass_g, "thread": self.thread,
                "holes": [list(h) for h in self.holes()], "size": self.get("size"), "shape": self.shape,
                "unchecked": self.unchecked(),
                "sources": sorted({v.source.get("url") or v.source.get("title", "") for v in self.values.values()})}


def _num(value):
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _value(field, raw, sources, where):
    if not isinstance(raw, dict) or "value" not in raw:
        raise ValueError(f"{where}: {field} must be {{\"value\": ..., \"source\": ..., \"checked\": ...}}")
    source = sources.get(raw.get("source"))
    if source is None:
        raise ValueError(f"{where}: {field} names no known source ({raw.get('source')!r}); a number needs one")
    return Value(field, raw["value"], source, bool(raw.get("checked", False)), str(raw.get("note", "")))


def _entry(raw, sources, layer):
    key = str(raw.get("key", "")).strip()
    if not key:
        raise ValueError(f"{layer}: a part needs a key")
    where = f"{layer}: {key}"
    values = {}
    if raw.get("mass_g") is not None:
        values["mass_g"] = _value("mass_g", raw["mass_g"], sources, where)
    if raw.get("cg") is not None:
        values["cg"] = _value("cg", raw["cg"], sources, where)
    geometry = raw.get("geometry") or {}
    shape = geometry.get("shape", "")
    if shape not in SHAPES:
        raise ValueError(f"{where}: geometry.shape is one of {', '.join(SHAPES)}")
    if shape == "step":
        if not geometry.get("file"):
            raise ValueError(f"{where}: a step geometry needs \"file\"")
    else:
        values["size"] = _value("size", geometry.get("size"), sources, where)
        need = 3 if shape == "box" else 2
        size = values["size"].value
        if not isinstance(size, list) or len(size) != need or any(float(v) <= 0 for v in size):
            raise ValueError(f"{where}: a {shape} needs {need} sizes above zero")
    mount = raw.get("mount") or {}
    for field in ("pitch", "holes", "thread", "depth", "pilot_diameter", "shaft_diameter"):
        if mount.get(field) is not None:
            values[field] = _value(field, mount[field], sources, where)
    if "pitch" not in values and "holes" not in values:
        raise ValueError(f"{where}: mount needs a pitch or a list of holes")
    if "thread" not in values:
        raise ValueError(f"{where}: mount needs the thread of its screws")
    return Entry(key, str(raw.get("name", key)), str(raw.get("category", "")), str(raw.get("manufacturer", "")),
                 str(raw.get("mpn", "")), shape, values, raw, layer)


def _layer(path, layer):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} file")
    sources = data.get("sources") or {}
    return [_entry(raw, sources, layer) for raw in data.get("parts", [])]


def _vault_root():
    try:
        import bruceworx_vaultclient as vc

        s = vc.session()
        return s.root if s is not None else ""
    except Exception:  # noqa: BLE001 -- headless without the client: no vault layer
        return ""


def load(vault_root=None, builtin=None) -> dict:
    """{key: Entry}: the built-in library, then the vault's cots_library.json over it (same key replaces)."""
    out = {e.key: e for e in _layer(builtin or os.path.join(HERE, LIBRARY_FILE), "built-in")}
    root = _vault_root() if vault_root is None else vault_root
    path = os.path.join(root, LIBRARY_FILE) if root else ""
    if path and os.path.isfile(path):
        for entry in _layer(path, "vault"):
            out[entry.key] = entry
    return out


def entry(key, table=None) -> Entry:
    found = (table or load()).get(key)
    if found is None:
        raise CotsError(f"the library has no bought part {key!r}")
    return found


# ----- the part -------------------------------------------------------------------------------------


def shape_of(item: Entry):
    """The placeholder solid in the Mount frame: the mounting face on z = 0, the part on the +Z side."""
    import Part

    App = _app()
    if item.shape == "box":
        x, y, z = (float(v) for v in item.get("size"))
        return Part.makeBox(x, y, z, App.Vector(-x / 2, -y / 2, 0))
    if item.shape == "cylinder":
        d, length = (float(v) for v in item.get("size"))
        return Part.makeCylinder(d / 2, length)
    path = item.raw["geometry"]["file"]
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    shape = Part.read(path)  # a STEP file is taken as drawn: its origin must already be the Mount frame
    if shape.isNull():
        raise CotsError(f"{path} holds no shape")
    return shape


def _prop(obj, kind, name, doc):
    if name not in obj.PropertiesList:
        obj.addProperty(kind, name, GROUP, doc)


def make_part(doc, item: Entry):
    """The bought part in ``doc``: an App::Part with the solid, its mass and the Mount coordinate system."""
    part = doc.addObject("App::Part", "Cots")
    part.Label = item.name
    solid = doc.addObject("Part::Feature", "CotsBody")
    solid.Label = "Envelope" if item.shape != "step" else "Body"
    solid.Shape = shape_of(item)
    part.addObject(solid)
    mount = doc.addObject("Part::LocalCoordinateSystem", MOUNT)
    part.addObject(mount)
    _prop(part, "App::PropertyString", KEY_PROP, "The bought part's key in the COTS library")
    setattr(part, KEY_PROP, item.key)
    _prop(part, "App::PropertyString", "Manufacturer", "Who makes it")
    part.Manufacturer = item.manufacturer
    _prop(part, "App::PropertyString", "ManufacturerPartNumber", "The maker's part number")
    part.ManufacturerPartNumber = item.mpn
    vendor, vendor_pn = str(item.raw.get("vendor") or ""), str(item.raw.get("vendor_pn") or "")
    if vendor or vendor_pn:  # who it is bought from, when that is not the maker (the BOM's fallback)
        _prop(part, "App::PropertyString", "Vendor", "Who it is bought from")
        part.Vendor = vendor
        _prop(part, "App::PropertyString", "VendorPartNumber", "The seller's part number")
        part.VendorPartNumber = vendor_pn
    _prop(part, "App::PropertyString", "MountThread", "The thread of its mounting screws")
    part.MountThread = item.thread
    _prop(part, "App::PropertyVectorList", "MountHoles", "The mounting holes in the Mount frame")
    part.MountHoles = [_app().Vector(x, y, 0) for x, y in item.holes()]
    _prop(part, "App::PropertyStringList", "CotsUnchecked", "Numbers not yet checked against the maker's drawing")
    part.CotsUnchecked = item.unchecked()
    if item.mass_g:
        _prop(part, "App::PropertyMass", "DatasheetMass", "Mass from the data sheet or listing")
        part.DatasheetMass = f"{float(item.mass_g)} g"
        cg = item.get("cg")
        if cg:  # the maker's centre of gravity, in the Mount frame (the mass report uses it, ROB-06)
            _prop(part, "App::PropertyVector", "DatasheetCG", "Centre of gravity from the data sheet (mm, Mount frame)")
            part.DatasheetCG = _app().Vector(*(float(v) for v in cg))
    if "ItemType" in part.PropertiesList:
        part.ItemType = "cots"
    return part


def is_cots(obj) -> bool:
    return KEY_PROP in getattr(obj, "PropertiesList", ())


def target_of(component):
    """The bought App::Part behind a component (itself, or a link's target), or None."""
    if component is None:
        return None
    target = component.getLinkedObject(True) if hasattr(component, "getLinkedObject") else component
    return target if is_cots(target) else None


def info_of(obj, table=None):
    """The library's data for a component (as a dict), or None when it is not a bought part."""
    target = target_of(obj)
    if target is None:
        return None
    found = (table or load()).get(getattr(target, KEY_PROP))
    if found is None:
        return {"key": getattr(target, KEY_PROP), "name": target.Label, "missing": True}
    return found.as_dict()


def part_file(item: Entry, folder: str) -> str:
    """The bought part's own file in ``folder`` (made when it is not there yet); the path."""
    App = _app()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, item.key + ".FCStd")
    if os.path.isfile(path):
        return path
    active = App.ActiveDocument
    doc = App.newDocument("Cots_" + "".join(c if c.isalnum() else "_" for c in item.key), hidden=True) \
        if App.GuiUp else App.newDocument("Cots_" + "".join(c if c.isalnum() else "_" for c in item.key))
    make_part(doc, item)
    doc.recompute()
    doc.saveAs(path)
    if active is not None:
        App.setActiveDocument(active.Name)
    return path


def _open(path):
    App = _app()
    for doc in App.listDocuments().values():
        if doc.FileName and os.path.exists(doc.FileName) and os.path.samefile(doc.FileName, path):
            return doc
    active = App.ActiveDocument
    doc = App.openDocument(path, hidden=True) if App.GuiUp else App.openDocument(path)
    if active is not None:
        App.setActiveDocument(active.Name)
    return doc


def insert(assembly, item: Entry, placement=None, session=None):
    """Put the bought part in ``assembly``; the component. With a vault open (or ``session`` given) it is the
    vault's COTS item for this entry (made if new), linked from the work folder, so the assembly must be
    saved. Without a vault: a link to a file in a "cots" folder beside a saved assembly, else the part
    itself. The caller opens the transaction and recomputes."""
    doc = assembly.Document
    s = session if session is not None else _session()
    if s is not None:
        if not doc.FileName:
            raise CotsError("Save the assembly first: the bought part is linked from the vault's work folder")
        pn, _made = ensure_item(s, item)
        if hasattr(s, "refresh"):
            s.refresh()
        path = s.work.get_latest(pn)
    elif doc.FileName:
        path = part_file(item, os.path.join(os.path.dirname(doc.FileName), FOLDER))
    else:
        path = ""
    if path:
        part = next((o for o in _open(path).Objects if is_cots(o)), None)
        if part is None:
            raise CotsError(f"{path} holds no bought part")
        component = doc.addObject("App::Link", "CotsLink")
        component.setLink(part)
        component.Label = item.name
    else:
        component = make_part(doc, item)
    assembly.addObject(component)
    if placement is not None:
        component.Placement = placement
    return component


# ----- the vault: one COTS item per library entry, as fasteners are ---------------------------------

CATEGORY = "Hardware"  # the Vault panel's category, the one Insert Fastener files bolts under


def catalogue_key(item: Entry) -> str:
    """The key of the bought part in the vault's hardware.json (fasteners use theirs: "ISO4762-M3x40")."""
    return "COTS-" + item.key


def make_item_file(path, pn, item: Entry, vault_root):
    """Write the vault file of a bought part: the part (make_part) with the vault's part properties, its
    part number, revision 01 and ItemType cots, and the maker's name and part number. Saved and closed."""
    from heuretes_freecad import partprops, pdm

    App = _app()
    doc = App.newDocument(pn.replace("-", "_"), hidden=True) if App.GuiUp else App.newDocument(pn.replace("-", "_"))
    try:
        doc.Label = pn
        part = make_part(doc, item)
        report = partprops.load_schema(extra_layers=partprops.vault_layers(vault_root)).apply(part)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise CotsError("; ".join(clash))
        part.PartNumber = pn
        part.Revision = pdm.first_prerelease()
        if "Description" in part.PropertiesList:
            part.Description = item.name
        if "ItemType" in part.PropertiesList:
            part.ItemType = "cots"
        # the schema may define these too: the library's values win
        part.Manufacturer = item.manufacturer
        part.ManufacturerPartNumber = item.mpn
        for prop in (KEY_PROP, "Manufacturer", "ManufacturerPartNumber"):
            part.setEditorMode(prop, 1)  # one vault item, one bought part
        doc.recompute()
        doc.saveAs(path)
    finally:
        App.closeDocument(doc.Name)


def ensure_item(s, item: Entry, program=""):
    """The vault part number of this bought part, made (numbered, typed cots, its file checked in as WIP 01)
    when the vault has none; recorded in the vault's hardware.json with the maker's name and part number.
    ``s``: the vault client's Session (or anything with .vault and .root). Returns (part number, made)."""
    from heuretes_freecad import hardware as hw
    from heuretes_freecad import vault_setup

    key = catalogue_key(item)
    catalogue = hw.load_catalogue(s.root)
    found = catalogue.get(key, {}).get("part_number", "")
    known = s.vault.items.get(found) if found else None
    made = False
    if known is not None and not getattr(known, "obsolete", False):
        pn = found
    else:
        def make(path, number):
            make_item_file(path, number, item, s.root)

        pn = vault_setup.new_item(s.vault, "cots", item.name, make, program, category=CATEGORY)
        made = True
    hw.record(catalogue, key, pn, item.name, source="COTS library", manufacturer=item.manufacturer, mpn=item.mpn)
    hw.save_catalogue(s.root, catalogue)
    return pn, made


def _session():
    """The vault client's session when a vault is open, else None."""
    try:
        import bruceworx_vaultclient as vc

        s = vc.session()
    except Exception:  # noqa: BLE001 -- headless without the client
        return None
    return s if s is not None and getattr(s, "vault", None) is not None else None


# ----- seats and joints -----------------------------------------------------------------------------


def _mount_of(component):
    target = target_of(component)
    if target is None:
        raise CotsError(f"{component.Label} is not a bought part from the library")
    for obj in target.Group:
        if obj.isDerivedFrom("App::LocalCoordinateSystem") and obj.Name.startswith(MOUNT):
            return obj
    raise CotsError(f"{component.Label} has no Mount coordinate system")


def mount_ref(component):
    """The joint reference of a bought component's Mount frame."""
    name = _mount_of(component).Name
    return [component, [name + ".", name + "."]]


def is_seat(obj) -> bool:
    return SEAT_PROP in getattr(obj, "PropertiesList", ())


def seat_of(obj):
    """The Seat coordinate system for a selection: itself, or the one on a seat's hole feature or sketch."""
    if obj is None:
        return None
    if is_seat(obj):
        return obj
    sketch = obj
    if getattr(obj, "TypeId", "") == "PartDesign::Hole":
        profile = obj.Profile
        sketch = profile[0] if isinstance(profile, (list, tuple)) else profile
    for other in getattr(sketch, "InList", []):
        if is_seat(other):
            return other
    return None


def _component_and_path(assembly, obj):
    """(component, path) for an object somewhere inside one of the assembly's components."""
    for component in assembly.Group:
        target = component.getLinkedObject(True) if hasattr(component, "getLinkedObject") else component
        if target is None:
            continue
        if obj is target:
            return component, ""
        chain, current = [obj.Name], obj
        for _ in range(12):
            parents = [p for p in current.InList if (p.isDerivedFrom("PartDesign::Body") or p.TypeId == "App::Part")
                       and current in getattr(p, "Group", [])]
            if not parents:
                break
            current = parents[0]
            if current is target:
                return component, ".".join(reversed(chain))
            chain.append(current.Name)
    raise CotsError(f"{obj.Label} is not inside a component of {assembly.Label}")


def seat_ref(assembly, seat):
    """The joint reference of a Seat (or any coordinate system) inside a component of the assembly."""
    component, path = _component_and_path(assembly, seat)
    if not path:
        raise CotsError(f"{seat.Label} is a component itself, not a coordinate system inside one")
    return [component, [path + ".", path + "."]]


def make_seat(body, support, item: Entry, offset=(0.0, 0.0), angle=0.0):
    """On ``support`` ((object, "FaceN") or (plane, "")) of ``body``: the part's hole pattern as
    clearance holes and a Seat coordinate system on it. Returns (sketch, hole, seat). The pattern's
    centre is the middle of the face (a plane: its origin), moved by ``offset`` in the sketch's X and Y;
    ``angle`` (degrees) turns it. The sketch's Attachment Offset holds all three, to edit afterwards."""
    import Part

    import bruceworx_hardware as bh

    App = _app()
    holes = item.holes()
    if not holes:
        raise CotsError(f"{item.name} has no mounting holes")
    name = f"Clearance {item.thread}"
    if name not in bh.presets():
        raise CotsError(f"there is no hole preset {name!r} for the screws of {item.name}")
    radius = bh._hw().clearance(item.thread) / 2
    sketch = body.newObject("Sketcher::SketchObject", "SeatSketch")
    sketch.Label = f"Seat sketch {item.name}"
    sketch.AttachmentSupport = [support]
    sketch.MapMode = "FlatFace"
    sketch.recompute()
    x, y = float(offset[0]), float(offset[1])
    if support[1].startswith("Face"):  # a flat face attaches at the feature's origin: start from its middle
        middle = sketch.Placement.inverse().multVec(support[0].Shape.getElement(support[1]).CenterOfMass)
        x, y = x + middle.x, y + middle.y
    sketch.AttachmentOffset = App.Placement(App.Vector(x, y, 0), App.Rotation(App.Vector(0, 0, 1), float(angle)))
    for x, y in holes:
        sketch.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), radius), False)
    sketch.recompute()
    hole = body.newObject("PartDesign::Hole", "SeatHoles")
    hole.Label = f"Mounting holes {item.name}"
    hole.Profile = sketch
    bh.apply_preset(hole, name)
    seat = body.newObject("PartDesign::CoordinateSystem", SEAT)
    seat.Label = f"Seat {item.name}"
    seat.AttachmentSupport = [(sketch, "")]
    seat.MapMode = "ObjectXY"
    _prop(seat, "App::PropertyString", SEAT_PROP, "The bought part this seat is for (COTS library key)")
    setattr(seat, SEAT_PROP, item.key)
    try:
        sketch.Visibility = False
    except Exception:  # noqa: BLE001
        pass
    return sketch, hole, seat


def _global(component, path):
    return component.getSubObject(path + ".", retType=3)


def join(assembly, component, seat):
    """Fix the bought ``component`` on ``seat`` (a Seat, or any coordinate system inside another
    component): Mount on Seat, the part on the side the seat's Z points to. Returns (joint, solve code)."""
    import bruceworx_asm_model as am

    seat = seat_of(seat) or seat
    first = seat_ref(assembly, seat)
    second = mount_ref(component)
    if first[0] is component:
        raise CotsError("the seat is on the bought part itself; pick a seat on the part it is mounted on")
    # Put the part on the seat first: the solver keeps whichever of "matched" and "flipped" is nearer.
    wanted = _global(first[0], first[1][0].rstrip("."))
    mount_now = _global(component, second[1][0].rstrip("."))
    local = component.Placement.inverse().multiply(mount_now)
    component.Placement = wanted.multiply(local.inverse())
    joint = am.create_mate(assembly, first, second, key="lock",
                           label=f"{component.Label} on {seat.Label}")
    return joint, am._STATUS.get((assembly.Document.Name, assembly.Name))


def check_seat(seat, table=None):
    """Does the seat's hole pattern still match the library? {"ok", "thread", "holes", "problems"}."""
    import bruceworx_hardware as bh

    item = entry(getattr(seat, SEAT_PROP), table)
    sketch = seat.AttachmentSupport[0][0]
    drawn = sorted((round(g.Center.x, 4), round(g.Center.y, 4)) for g in sketch.Geometry
                   if type(g).__name__ == "Circle")
    wanted = sorted((round(x, 4), round(y, 4)) for x, y in item.holes())
    problems = []
    if drawn != wanted:
        problems.append(f"the holes are at {drawn}, the library says {wanted}")
    hole = next((o for o in sketch.InList if o.TypeId == "PartDesign::Hole"), None)
    if hole is None:
        problems.append("the hole feature is gone")
    else:
        need = bh._hw().clearance(item.thread)
        have = hole.Diameter.Value
        if have < need - 1e-6:
            problems.append(f"the holes are {_num(have)} mm, an {item.thread} screw needs {_num(need)} mm clearance")
    return {"ok": not problems, "part": item.name, "thread": item.thread, "holes": len(wanted),
            "unchecked": item.unchecked(), "problems": problems}


# ----- GUI ------------------------------------------------------------------------------------------


def _qt():
    from PySide import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _message(text, title="Bought parts"):
    import FreeCADGui as Gui

    _QtCore, _QtGui, QtWidgets = _qt()
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, title, text, QtWidgets.QMessageBox.Ok,
                                Gui.getMainWindow())
    _state["dialog"] = box
    box.open()


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


def _active_assembly():
    import bruceworx_hardware as bh

    found = bh._active_assembly()
    return found if found is not None and found.TypeId == "Assembly::AssemblyObject" else None


def _dialog_class():
    QtCore, _QtGui, QtWidgets = _qt()

    class PickDialog(QtWidgets.QDialog):
        """The library: a list of bought parts and, for the one picked, its numbers with their sources."""

        def __init__(self, title, action, parent=None):
            super().__init__(parent)
            self.setWindowTitle(title)
            self.setObjectName("BruceWorxCotsDialog")
            self.table = load()
            layout = QtWidgets.QVBoxLayout(self)
            self.search = QtWidgets.QLineEdit(self)
            self.search.setPlaceholderText("Search bought parts")
            layout.addWidget(self.search)
            split = QtWidgets.QSplitter(self)
            self.items = QtWidgets.QListWidget(split)
            self.items.setObjectName("BruceWorxCotsList")
            self.details = QtWidgets.QPlainTextEdit(split)
            self.details.setObjectName("BruceWorxCotsDetails")
            self.details.setReadOnly(True)
            split.setStretchFactor(1, 2)
            layout.addWidget(split, 1)
            self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel, self)
            self.ok = self.buttons.addButton(action, QtWidgets.QDialogButtonBox.AcceptRole)
            layout.addWidget(self.buttons)
            self.buttons.accepted.connect(lambda: self.accept())
            self.buttons.rejected.connect(lambda: self.reject())
            self.search.textChanged.connect(lambda _t: self.fill())
            self.items.currentItemChanged.connect(lambda _a, _b: self.show_details())
            self.items.itemDoubleClicked.connect(lambda _i: self.accept())
            self.resize(760, 420)
            self.fill()

        def fill(self):
            needle = self.search.text().strip().lower()
            self.items.clear()
            for item in sorted(self.table.values(), key=lambda e: (e.category, e.name)):
                text = f"{item.name}  [{item.category}]" if item.category else item.name
                if needle and needle not in (text + item.key + item.mpn + item.manufacturer).lower():
                    continue
                row = QtWidgets.QListWidgetItem(text, self.items)
                row.setData(QtCore.Qt.UserRole, item.key)
            if self.items.count():
                self.items.setCurrentRow(0)
            self.show_details()

        def chosen(self):
            row = self.items.currentItem()
            return self.table.get(row.data(QtCore.Qt.UserRole)) if row is not None else None

        def show_details(self):
            item = self.chosen()
            self.ok.setEnabled(item is not None)
            if item is None:
                self.details.setPlainText("")
                return
            text = item.describe()
            loose = item.unchecked()
            if loose:
                text += ("\n\nNot checked against the maker's drawing: " + ", ".join(loose)
                         + ".\nConfirm these (or measure the part) before the design depends on them.")
            self.details.setPlainText(text)

    return PickDialog


def pick(title, action, done):
    """Open the library; ``done(entry)`` runs when one is chosen. Returns the dialog (it is not modal-blocking)."""
    import FreeCADGui as Gui

    dialog = _dialog_class()(title, action, Gui.getMainWindow())
    _state["dialog"] = dialog
    dialog.accepted.connect(lambda: done(dialog.chosen()) if dialog.chosen() is not None else None)
    dialog.open()
    return dialog


def _selected():
    """[(object, element)] of the selection, the object being the one picked (a feature, a datum)."""
    import FreeCADGui as Gui

    out = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        for path in ([s for s in sel.SubElementNames if s] or [""]):
            try:
                picked = sel.Object.getSubObject(path, retType=1) if path else sel.Object
            except Exception:  # noqa: BLE001
                picked = sel.Object
            out.append((picked or sel.Object, path.rsplit(".", 1)[-1] if path else "", sel.Object, path))
    known = {id(row[0]) for row in out}
    for obj in Gui.Selection.getSelection("", 1):  # the resolved objects too: a datum picked inside an assembly
        if id(obj) not in known:
            out.append((obj, "", obj, ""))
    return out


def _selected_seat():
    for picked, _element, _top, _path in _selected():
        seat = seat_of(picked)
        if seat is not None:
            return seat
        if picked.isDerivedFrom("App::LocalCoordinateSystem") or picked.isDerivedFrom("PartDesign::CoordinateSystem"):
            return picked
    return None


def _selected_component(assembly):
    for picked, _element, top, path in _selected():
        for candidate in (picked, top, assembly.Document.getObject(path.split(".", 1)[0]) if path else None):
            if candidate is not None and target_of(candidate) is not None and candidate in assembly.Group:
                return candidate
    return None


def insert_chosen(item, assembly, seat=None):
    """Insert (and, with a seat, join) in one undo step; (component, joint or None, solve code or None)."""

    def work():
        component = insert(assembly, item)
        assembly.Document.recompute()
        if seat is None:
            return component, None, None
        joint, code = join(assembly, component, seat)
        return component, joint, code

    return _transaction(assembly.Document, f"Insert {item.name}", work)


class _InsertCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "Heuretes_CotsInsert.svg"), "MenuText": "Insert Bought Part…",
                "ToolTip": "Insert a bought part (motor, bearing, board) from the library into the assembly, with "
                           "its mounting frame. With a seat selected it is fixed on that seat at once."}

    def IsActive(self):
        return enabled() and _app().ActiveDocument is not None

    def Activated(self):
        assembly = _active_assembly()
        if assembly is None:
            _message("Bought parts go into an assembly. Start one first (New > Assembly or Hybrid).")
            return
        seat = _selected_seat()

        def done(item):
            try:
                insert_chosen(item, assembly, seat)
            except CotsError as exc:
                _message(str(exc))

        pick("Insert bought part", "Insert", done)


class _SeatCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "Heuretes_CotsSeat.svg"), "MenuText": "Mounting Holes for Bought Part…",
                "ToolTip": "On the selected flat face: the mounting holes of a bought part (clearance holes for its "
                           "screws) and a Seat frame to fix the part on."}

    def IsActive(self):
        return enabled() and _app().ActiveDocument is not None

    def Activated(self):
        import bruceworx_plane as bp

        found = None
        for picked, element, _top, _path in _selected():
            reference = bp.flat_reference(picked, element)
            body = bp.body_of(picked) if reference else None
            if reference and body is not None:
                found = (body, reference)
                break
        if found is None:
            _message("Select a flat face of the part the bought part is mounted on, then run this again.")
            return
        body, reference = found

        def done(item):
            try:
                _transaction(body.Document, f"Mounting holes {item.name}", lambda: make_seat(body, reference, item))
            except (CotsError, ValueError) as exc:
                _message(str(exc))

        pick("Mounting holes for a bought part", "Make Holes", done)


class _JoinCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "Heuretes_CotsJoin.svg"), "MenuText": "Fix Bought Part on Seat",
                "ToolTip": "Select a bought part and a seat (its coordinate system or its mounting holes): the part "
                           "is fixed on the seat by its mounting frame."}

    def IsActive(self):
        return enabled() and _app().ActiveDocument is not None

    def Activated(self):
        assembly = _active_assembly()
        component = _selected_component(assembly) if assembly is not None else None
        seat = _selected_seat()
        if assembly is None or component is None or seat is None:
            _message("Select the bought part and the seat it goes on (the Seat frame or its mounting holes).")
            return
        try:
            _transaction(assembly.Document, f"Fix {component.Label}", lambda: join(assembly, component, seat))
        except CotsError as exc:
            _message(str(exc))


def install():
    """Register the three commands (once). The ribbon and menu entries are in workspaces.json."""
    if _state["installed"]:
        return
    import FreeCADGui

    FreeCADGui.addCommand("BruceWorx_CotsInsert", _InsertCommand())
    FreeCADGui.addCommand("BruceWorx_CotsSeat", _SeatCommand())
    FreeCADGui.addCommand("BruceWorx_CotsJoin", _JoinCommand())
    _state["installed"] = True

