# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Properties tab of the right panel (roadmap UIX-12; plan "Redesign (24 Oct)").

A tab in the right panel (bruceworx_rightpanel), in the style of a Fusion
command dialog: folding sections with compact fields.

* A feature: its key values (Pad: Distance, Direction, Extent) with Edit and
  ⋯ (Rename, Suppress, All properties); the rest under a folded More.
* A face, edge or vertex: what it is, the feature that made it and its
  sketch (bruceworx_picktrace traces it), Edit <feature>, Measure and, for a
  flat face, Sketch on face.
* Then the part the selection belongs to: part number and state, description,
  material, appearance, mass; other part data and the position folded.
* Several objects of one type: the rows they share; different values read
  "mixed"; one edit sets all of them in one undo step.
* Nothing selected: a hint.

Which property goes where is ``plan_rows`` (a curated table for the types used
daily, then rules), and which rows are key is KEY_ROWS. Labels are friendly
("Distance"), with FreeCAD's property name and documentation as the tooltip.
Every edit is one transaction named "<object>: <field>" (the undo toast shows
it); typed values apply on Enter or on leaving the field. Folded sections are
remembered. FreeCAD's View/Data editor stays under the Browser ("All
properties").
"""

from __future__ import annotations

import re

from bruceworx_slots import slot

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
FOLD_PREF = "InspectorOpen"  # sections opened (and InspectorOpenClosed: folded) by the user
MODEL_DOCK = "Model"
PART_GROUP = "BruceWorx"  # the part-property group (bruceworx/partprops.py)

SIZE, OPTIONS, POSITION, APPEARANCE, PART_DATA, FEATURE = (
    "SIZE", "OPTIONS", "POSITION", "APPEARANCE", "PART DATA", "FEATURE")
GROUP_ORDER = (SIZE, OPTIONS, POSITION, APPEARANCE, PART_DATA, FEATURE)
MIXED = "mixed"
EMPTY_HINT = "Select a feature, face, body or part to see its properties here."

QUANTITY_TYPES = (
    "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle", "App::PropertyQuantity",
    "App::PropertyQuantityConstraint", "App::PropertyPosition", "App::PropertySpeed",
    "App::PropertyAcceleration", "App::PropertyArea", "App::PropertyVolume", "App::PropertyMass",
)
INTEGER_TYPES = ("App::PropertyInteger", "App::PropertyIntegerConstraint")
FLOAT_TYPES = ("App::PropertyFloat", "App::PropertyFloatConstraint", "App::PropertyPrecision")
LINK_TYPES = (
    "App::PropertyLink", "App::PropertyLinkSub", "App::PropertyLinkList", "App::PropertyLinkSubList",
    "App::PropertyXLink", "App::PropertyXLinkSub", "App::PropertyXLinkSubList",
    "App::PropertyLinkChild", "App::PropertyLinkGlobal", "App::PropertyLinkSubChild", "App::PropertyLinkSubGlobal",
)
TEXT_TYPES = ("App::PropertyString", "App::PropertyFont", "App::PropertyUUID")
# Links the inspector never shows: the tree shows them already.
STRUCTURE_LINKS = ("Group", "BaseFeature", "Tip", "Origin", "AttachmentSupport", "Material", "ExpressionEngine")

# Editor kinds.
QUANTITY, INTEGER, FLOAT, CHOICE, SWITCH, LINK, TEXT, READONLY = (
    "quantity", "integer", "float", "choice", "switch", "link", "text", "readonly")

# The curated table: type -> [(group, property, label)] in display order. Rows whose
# property is missing, hidden or read-only (an inactive option) are left out.
_EXTRUDE = [(OPTIONS, "Type", "Extent"), (OPTIONS, "SideType", "Direction"), (OPTIONS, "Reversed", "Flip"),
            (FEATURE, "Profile", "Sketch"), (FEATURE, "UpToFace", "Up to face"), (FEATURE, "Refine", "Refine")]
_REVOLVE = [(SIZE, "Angle", "Angle"), (SIZE, "Angle2", "Angle 2"), (OPTIONS, "Type", "Extent"),
            (OPTIONS, "Midplane", "Symmetric"), (OPTIONS, "Reversed", "Flip"), (FEATURE, "Profile", "Sketch"),
            (FEATURE, "ReferenceAxis", "Axis"), (FEATURE, "UpToFace", "Up to face"), (FEATURE, "Refine", "Refine")]
_PATTERN = [(FEATURE, "Originals", "Features"), (FEATURE, "Refine", "Refine")]
CURATED = {
    "PartDesign::Pad": [(SIZE, "Length", "Distance"), (SIZE, "Length2", "Distance 2"), (SIZE, "Offset", "Offset"),
                        (SIZE, "TaperAngle", "Taper"), (SIZE, "TaperAngle2", "Taper 2")] + _EXTRUDE,
    "PartDesign::Pocket": [(SIZE, "Length", "Depth"), (SIZE, "Length2", "Depth 2"), (SIZE, "Offset", "Offset"),
                           (SIZE, "TaperAngle", "Taper"), (SIZE, "TaperAngle2", "Taper 2")] + _EXTRUDE,
    "PartDesign::Revolution": _REVOLVE,
    "PartDesign::Groove": _REVOLVE,
    "PartDesign::Hole": [(SIZE, "Diameter", "Diameter"), (SIZE, "Depth", "Depth"),
                         (SIZE, "DrillPointAngle", "Tip angle"), (SIZE, "HoleCutDiameter", "Head Ø"),
                         (SIZE, "HoleCutDepth", "Head depth"), (OPTIONS, "DepthType", "Extent"),
                         (OPTIONS, "DrillPoint", "Tip"), (OPTIONS, "HoleCutType", "Head"),
                         (OPTIONS, "Threaded", "Threaded"), (OPTIONS, "ThreadType", "Thread"),
                         (OPTIONS, "ThreadSize", "Size"), (OPTIONS, "ModelThread", "Model thread"),
                         (OPTIONS, "Reversed", "Flip"), (FEATURE, "Profile", "Sketch"), (FEATURE, "Refine", "Refine")],
    "PartDesign::Fillet": [(SIZE, "Radius", "Radius"), (OPTIONS, "UseAllEdges", "All edges"),
                           (FEATURE, "Base", "Edges of"), (FEATURE, "Refine", "Refine")],
    "PartDesign::Chamfer": [(SIZE, "Size", "Distance"), (SIZE, "Size2", "Distance 2"), (SIZE, "Angle", "Angle"),
                            (OPTIONS, "ChamferType", "Type"), (OPTIONS, "FlipDirection", "Flip"),
                            (OPTIONS, "UseAllEdges", "All edges"), (FEATURE, "Base", "Edges of"),
                            (FEATURE, "Refine", "Refine")],
    "PartDesign::Draft": [(SIZE, "Angle", "Angle"), (OPTIONS, "Reversed", "Flip"), (FEATURE, "Base", "Faces of"),
                          (FEATURE, "NeutralPlane", "Neutral plane"), (FEATURE, "PullDirection", "Pull direction"),
                          (FEATURE, "Refine", "Refine")],
    "PartDesign::Thickness": [(SIZE, "Value", "Thickness"), (OPTIONS, "Mode", "Mode"), (OPTIONS, "Join", "Join"),
                              (OPTIONS, "Reversed", "Inward"), (OPTIONS, "Intersection", "Intersection"),
                              (FEATURE, "Base", "Faces of"), (FEATURE, "Refine", "Refine")],
    "PartDesign::LinearPattern": [(SIZE, "Length", "Extent"), (SIZE, "Offset", "Spacing"),
                                  (SIZE, "Occurrences", "Count"), (SIZE, "Length2", "Extent 2"),
                                  (SIZE, "Offset2", "Spacing 2"), (SIZE, "Occurrences2", "Count 2"),
                                  (OPTIONS, "Mode", "Size by"), (OPTIONS, "Reversed", "Flip"),
                                  (FEATURE, "Direction", "Direction")] + _PATTERN,
    "PartDesign::PolarPattern": [(SIZE, "Angle", "Angle"), (SIZE, "Offset", "Spacing"),
                                 (SIZE, "Occurrences", "Count"), (OPTIONS, "Mode", "Size by"),
                                 (OPTIONS, "Reversed", "Flip"), (FEATURE, "Axis", "Axis")] + _PATTERN,
    "PartDesign::Mirrored": [(FEATURE, "MirrorPlane", "Plane")] + _PATTERN,
    "PartDesign::MultiTransform": _PATTERN,
    "PartDesign::Body": [(POSITION, "Placement", "Position")],
    "App::Part": [(POSITION, "Placement", "Position")],
    "App::Link": [(POSITION, "Placement", "Position"), (FEATURE, "LinkedObject", "Part")],
    "Sketcher::SketchObject": [(POSITION, "AttachmentOffset", "Offset"), (OPTIONS, "MapMode", "Attachment"),
                               (OPTIONS, "MapReversed", "Flip side")],
}
# Choices shown with other words than FreeCAD's (per property name).
CHOICE_WORDS = {
    "Length": "Distance", "UpToLast": "To last", "UpToFirst": "To first", "UpToFace": "To face",
    "UpToShape": "To shape", "ThroughAll": "Through all", "TwoLengths": "Two distances", "TwoAngles": "Two angles",
    "Dimension": "Distance", "One side": "One side", "Two sides": "Two sides",
}
SURFACE_WORDS = {
    "Plane": "Flat", "Cylinder": "Cylinder", "Cone": "Cone", "Sphere": "Sphere", "Toroid": "Torus",
    "BSplineSurface": "Freeform", "BezierSurface": "Freeform", "SurfaceOfExtrusion": "Extruded",
    "SurfaceOfRevolution": "Revolved", "OffsetSurface": "Offset",
}
CURVE_WORDS = {"Line": "Straight", "Circle": "Circle", "Ellipse": "Ellipse", "BSplineCurve": "Spline",
               "BezierCurve": "Spline", "Hyperbola": "Hyperbola", "Parabola": "Parabola"}
# Placement as six fields: (part, label, unit kind).
PLACEMENT_PARTS = (("x", "X", "Length"), ("y", "Y", "Length"), ("z", "Z", "Length"),
                   ("yaw", "Rotation Z", "Angle"), ("pitch", "Rotation Y", "Angle"), ("roll", "Rotation X", "Angle"))
CONTAINER_TYPES = ("App::Part", "PartDesign::Body", "App::DocumentObjectGroup", "App::Origin",
                   "Assembly::AssemblyObject", "App::Link")


# ----- pure helpers (unit tested) ---------------------------------------------------------


class Row:
    """One field of the card: a property (or one part of a placement) with its label and editor kind."""

    __slots__ = ("group", "name", "label", "kind", "prop_type", "part", "choices", "readonly", "tip")

    def __init__(self, group, name, label, kind, prop_type="", part="", choices=(), readonly=False, tip=""):
        self.group, self.name, self.label, self.kind = group, name, label, kind
        self.prop_type, self.part, self.choices, self.readonly, self.tip = prop_type, part, tuple(choices), readonly, tip

    @property
    def key(self):
        return (self.name, self.part)

    def signature(self):
        return (self.group, self.name, self.part, self.kind, self.choices, self.readonly)

    def __repr__(self):
        return f"Row({self.group!r}, {self.name!r}{', ' + repr(self.part) if self.part else ''}, {self.label!r})"


def friendly(name):
    """"TaperAngle" -> "Taper angle", "UseAllEdges" -> "Use all edges", "Length2" -> "Length 2"."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name or "").split()
    if not words:
        return ""
    words = [words[0]] + [w if (w.isupper() and len(w) > 1) else w.lower() for w in words[1:]]
    return " ".join(words)


def choice_text(value):
    """The words shown for an enumeration choice: "UpToFirst" -> "To first"."""
    value = str(value)
    if value in CHOICE_WORDS:
        return CHOICE_WORDS[value]
    return friendly(value) if " " not in value else value


def visible_choices(choices):
    """FreeCAD marks retired choices with a leading "?"; they are not offered."""
    return [c for c in choices or () if not str(c).startswith("?")]


SEGMENT_CHARS = 18  # the words of a segmented control must fit a narrow Browser


def segmented(choices):
    """Three or fewer short choices read best as a segmented control; more (or long ones) as a menu."""
    return 0 < len(choices) <= 3 and sum(len(choice_text(c)) for c in choices) <= SEGMENT_CHARS


def editor_kind(prop_type):
    if prop_type in QUANTITY_TYPES:
        return QUANTITY
    if prop_type in INTEGER_TYPES:
        return INTEGER
    if prop_type in FLOAT_TYPES:
        return FLOAT
    if prop_type == "App::PropertyEnumeration":
        return CHOICE
    if prop_type == "App::PropertyBool":
        return SWITCH
    if prop_type in LINK_TYPES:
        return LINK
    if prop_type in TEXT_TYPES:
        return TEXT
    if prop_type == "App::PropertyPlacement":
        return "placement"
    return READONLY


def hidden(mode, status, name):
    """Rule 1: never in the card (hidden, private, or bookkeeping)."""
    return "Hidden" in (mode or ()) or "Hidden" in (status or ()) or name.startswith("_")


def classify(name, prop_type, group, mode=()):
    """The inspector group of a property no curated row covers (rules 3 to 9), or None."""
    readonly = "ReadOnly" in (mode or ())
    if group == PART_GROUP:
        return PART_DATA
    if prop_type == "App::PropertyPlacement" or name in ("AttachmentOffset", "MapMode"):
        return None if readonly else POSITION
    if readonly:
        return None
    if prop_type in QUANTITY_TYPES or prop_type in ("App::PropertyIntegerConstraint", "App::PropertyFloatConstraint"):
        return None if group in ("Part Design", "Base") else SIZE
    if prop_type in ("App::PropertyEnumeration", "App::PropertyBool"):
        if group in ("Base", "Attachment", "Part Design", "SketchBased", "Link", "") and name != "Refine":
            return None
        return FEATURE if name == "Refine" else OPTIONS
    if prop_type in LINK_TYPES and name in ("Profile", "Base", "Originals"):
        return FEATURE
    return None


def _meta(obj, name):
    mode = tuple(obj.getEditorMode(name) or ())
    try:
        status = tuple(obj.getPropertyStatus(name) or ())
    except Exception:
        status = ()
    return obj.getTypeIdOfProperty(name), obj.getGroupOfProperty(name), mode, status


def _doc_of(obj, name):
    try:
        return obj.getDocumentationOfProperty(name) or ""
    except Exception:
        return ""


def _tip(obj, name, part=""):
    doc = _doc_of(obj, name)
    head = name + ("." + part if part else "")
    return head + (": " + doc if doc else "")


def _make_rows(obj, group, name, label, prop_type, mode):
    """The rows for one property: one, or six for a placement."""
    kind = editor_kind(prop_type)
    readonly = "ReadOnly" in mode
    if kind == "placement":
        return [Row(group, name, part_label, QUANTITY, unit, part, readonly=readonly, tip=_tip(obj, name, part))
                for part, part_label, unit in PLACEMENT_PARTS]
    choices = ()
    if kind == CHOICE:
        try:
            choices = visible_choices(obj.getEnumerationsOfProperty(name))
        except Exception:
            choices = ()
    if kind == CHOICE and len(choices) < 2 and group != PART_DATA:
        return []  # nothing to choose (a thread size while the hole has no thread type)
    if readonly or (kind == CHOICE and len(choices) < 2):
        kind = READONLY
    return [Row(group, name, label, kind, prop_type, choices=choices, readonly=readonly, tip=_tip(obj, name))]


def plan_rows(obj, part_order=None):
    """The card's rows for ``obj``, in group order.

    Curated types use their table (plus part data and any placement the table
    leaves out); other types follow the rules. ``part_order``: the part-property
    names in schema order (bruceworx/partprops.py), for PART DATA.
    """
    props = list(getattr(obj, "PropertiesList", ()) or ())
    rows = []
    seen = set()
    curated = CURATED.get(getattr(obj, "TypeId", ""))
    if curated:
        for group, name, label in curated:
            if name not in props:
                continue
            prop_type, _grp, mode, status = _meta(obj, name)
            if hidden(mode, status, name) or ("ReadOnly" in mode and group != POSITION):
                continue
            rows += _make_rows(obj, group, name, label, prop_type, mode)
            seen.add(name)
    for name in props:
        if name in seen or name in STRUCTURE_LINKS or name == "Label":
            continue
        prop_type, group, mode, status = _meta(obj, name)
        if hidden(mode, status, name):
            continue
        target = classify(name, prop_type, group, mode)
        if target is None or (curated and target not in (PART_DATA, POSITION)):
            continue
        rows += _make_rows(obj, target, name, friendly(name), prop_type, mode)
    order = {n: i for i, n in enumerate(part_order or ())}
    group_rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda ir: (group_rank.get(ir[1].group, 99),
                                 order.get(ir[1].name, len(order)) if ir[1].group == PART_DATA else 0, ir[0]))
    return [r for _i, r in indexed]


def common_rows(row_lists):
    """Rows present (same name, part and kind) on every object: the multi-selection card."""
    if not row_lists:
        return []
    first, rest = row_lists[0], row_lists[1:]
    keys = [{r.signature()[1:4] for r in rows} for rows in rest]
    return [r for r in first if all(r.signature()[1:4] in k for k in keys)]


def merged(values, text=str):
    """(value, False) when all ``values`` are equal (by ``text``), else (first value, True)."""
    if not values:
        return None, False
    first = text(values[0])
    return values[0], any(text(v) != first for v in values[1:])


def opacity(transparency):
    """FreeCAD's Transparency (0 to 100) as opacity in percent."""
    return max(0, min(100, 100 - int(transparency or 0)))


def number(value, digits=2):
    text = f"{round(float(value), digits):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def placement_part(parts, part):
    """One part of a placement given as {"x", "y", "z", "yaw", "pitch", "roll"}."""
    return parts[part]


# Key values per type, shown open; every other row goes under More.
KEY_ROWS = {
    "PartDesign::Pad": ("Length", "SideType", "Type"),
    "PartDesign::Pocket": ("Length", "SideType", "Type"),
    "PartDesign::Revolution": ("Angle", "Type"),
    "PartDesign::Groove": ("Angle", "Type"),
    "PartDesign::Hole": ("Diameter", "Depth", "DepthType"),
    "PartDesign::Fillet": ("Radius",),
    "PartDesign::Chamfer": ("Size", "ChamferType"),
    "PartDesign::Draft": ("Angle",),
    "PartDesign::Thickness": ("Value", "Mode"),
    "PartDesign::LinearPattern": ("Occurrences", "Length", "Offset"),
    "PartDesign::PolarPattern": ("Occurrences", "Angle", "Offset"),
    "PartDesign::Mirrored": ("MirrorPlane",),
    "PartDesign::Body": (),
    "App::Part": (),
    "App::Link": ("LinkedObject",),
    "Sketcher::SketchObject": ("MapMode",),
}
KEY_DEFAULT = 3  # other types: the first three size and option rows


def split_key_rows(type_id, rows):
    """(key rows, other rows): the values a type shows open, in KEY_ROWS order; the rest under More."""
    keys = KEY_ROWS.get(type_id)
    if keys is None:
        firsts = [r for r in rows if r.group in (SIZE, OPTIONS) and not r.part][:KEY_DEFAULT]
        return firsts, [r for r in rows if r not in firsts]
    key = [r for name in keys for r in rows if r.name == name and not r.part]
    return key, [r for r in rows if r not in key]


def group_rows(rows):
    """[(group, [rows])] in group order, empty groups left out."""
    out = []
    for group in GROUP_ORDER:
        members = [r for r in rows if r.group == group]
        if members:
            out.append((group, members))
    return out


def caption(group):
    """The caption text of a group (drawn in small caps)."""
    return group.title() if group != PART_DATA else "Part data"


def element_words(kind, surface="", curve=""):
    """"Face" + "Plane" -> ("Face", "Flat")."""
    if kind == "Face":
        return "Face", SURFACE_WORDS.get(surface, friendly(surface) or "Face")
    if kind == "Edge":
        return "Edge", CURVE_WORDS.get(curve, friendly(curve) or "Edge")
    return "Vertex", "Point"


def split_element(element):
    """"Face14" -> ("Face", 14)."""
    match = re.match(r"^(Face|Edge|Vertex)(\d+)$", element or "")
    return (match.group(1), int(match.group(2))) if match else ("", 0)


def selection_kind(items):
    """What the card shows for [(obj, element)]: "none", "element", "elements", "object", "objects" or "mixed"."""
    if not items:
        return "none"
    with_element = [e for _o, e in items if e]
    if with_element:
        return "element" if len(items) == 1 else "elements"
    if len(items) == 1:
        return "object"
    types = {getattr(o, "TypeId", "") for o, _e in items}
    return "objects" if len(types) == 1 else "mixed"


def transaction_name(labels, field):
    """"Boss: Distance"; "2 objects: Radius" for several."""
    labels = list(labels)
    who = labels[0] if len(labels) == 1 else f"{len(labels)} objects"
    return f"{who}: {field}"


def location_text(type_word, parent_label=""):
    """"Pad · in Body"."""
    return f"{type_word} · in {parent_label}" if parent_label else type_word


# ----- FreeCAD side ------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _obj_alive(obj):
    try:
        return obj is not None and obj.Document is not None and obj.Name is not None and \
            obj.Document.getObject(obj.Name) is obj
    except Exception:
        return False


def part_order():
    """The part-property names in schema order, [] without the add-on."""
    try:
        from bruceworx.partprops import load_schema

        return list(load_schema().properties)
    except Exception:
        return []


def type_word(obj):
    try:
        import bruceworx_browser as bb

        kind = bb.type_name(obj)
        return bb.TYPE_WORDS.get(kind, friendly(kind))
    except Exception:
        return str(getattr(obj, "TypeId", "")).split("::")[-1]


def parent_of(obj):
    try:
        parent = obj.getParentGeoFeatureGroup()
        if parent is not None:
            return parent
    except Exception:
        pass
    try:
        return obj.getParentGroup()
    except Exception:
        return None


def resolve_selection():
    """[(top object, subname, resolved object, element)] for the current selection."""
    import FreeCADGui

    out = []
    for sel in FreeCADGui.Selection.getSelectionEx("", 0):
        top = sel.Object
        subs = list(sel.SubElementNames) or [""]
        for sub in subs:
            obj = top
            if sub:
                try:
                    found = top.getSubObject(sub, retType=1)
                    obj = found if found is not None else top
                except Exception:
                    obj = top
            element = ""
            last = sub.rsplit(".", 1)[-1] if sub else ""
            if split_element(last)[0]:
                element = last
            out.append((top, sub, obj, element))
    return out


def value_of(obj, row):
    """The current value of a row on ``obj``."""
    value = getattr(obj, row.name)
    if row.part:
        import FreeCAD

        if row.part in ("x", "y", "z"):
            return FreeCAD.Units.Quantity(getattr(value.Base, row.part), FreeCAD.Units.Length)
        yaw, pitch, roll = value.Rotation.toEuler()
        return FreeCAD.Units.Quantity({"yaw": yaw, "pitch": pitch, "roll": roll}[row.part], FreeCAD.Units.Angle)
    return value


def set_value(obj, row, value):
    """Set a row on ``obj``; a placement part changes that part only."""
    import FreeCAD

    if row.part:
        pl = FreeCAD.Placement(getattr(obj, row.name))
        number_value = float(getattr(value, "Value", value))
        if row.part in ("x", "y", "z"):
            base = FreeCAD.Vector(pl.Base)
            setattr(base, row.part, number_value)
            pl.Base = base
        else:
            angles = dict(zip(("yaw", "pitch", "roll"), pl.Rotation.toEuler()))
            angles[row.part] = number_value
            pl.Rotation = FreeCAD.Rotation(angles["yaw"], angles["pitch"], angles["roll"])
        setattr(obj, row.name, pl)
        return
    setattr(obj, row.name, value)


def value_text(value):
    """A value as the card shows it read-only or compares it."""
    if value is None:
        return ""
    if hasattr(value, "UserString"):
        return "0" if getattr(value, "Value", None) == 0 else value.UserString
    if hasattr(value, "Label") and hasattr(value, "Name"):
        return value.Label
    if isinstance(value, float):
        return number(value, 4)
    if isinstance(value, (list, tuple)):
        return ", ".join(value_text(v) for v in value)
    return str(value)


def link_targets(value):
    """[(object, element names)] from any link property value."""
    out = []

    def add(item):
        if item is None:
            return
        if hasattr(item, "Name") and hasattr(item, "Document"):
            out.append((item, []))
        elif _is_link_sub(item):
            subs = item[1]
            subs = [subs] if isinstance(subs, str) else list(subs or [])
            out.append((item[0], [s for s in subs if s]))
        elif isinstance(item, (tuple, list)):
            for sub in item:
                add(sub)

    add(value)
    return out


def _is_link_sub(item):
    """(object, "Face1") or (object, ["Edge1", ...]): one link with its elements."""
    if not isinstance(item, (tuple, list)) or len(item) != 2 or not hasattr(item[0], "Name"):
        return False
    subs = item[1]
    return isinstance(subs, str) or (isinstance(subs, (tuple, list)) and all(isinstance(x, str) for x in subs))


def link_text(obj, subs):
    """"Hole · 4 edges"; "Sketch"."""
    if not subs:
        return obj.Label
    kinds = {split_element(s.rsplit(".", 1)[-1])[0] for s in subs}
    if len(kinds) == 1 and "" not in kinds:
        word = kinds.pop().lower()
        if len(subs) != 1:
            word += "s" if word != "vertex" else "es"
        return f"{obj.Label} · {len(subs)} {word}" if len(subs) > 1 else f"{obj.Label} · {subs[0].rsplit('.', 1)[-1]}"
    return f"{obj.Label} · {len(subs)} items"


def appearance_owner(obj):
    """(view provider owner, "from body") for colour: a PartDesign feature takes its body's look."""
    try:
        import PartDesign  # noqa: F401

        body = obj.getParentGeoFeatureGroup()
    except Exception:
        body = None
    if body is not None and getattr(body, "TypeId", "") == "PartDesign::Body" and obj is not body:
        return body, True
    return obj, False


def has_appearance(obj):
    vp = getattr(obj, "ViewObject", None)
    return vp is not None and "ShapeAppearance" in vp.PropertiesList


def face_facts(shape):
    """[(label, text)] for a face, edge or vertex shape (global coordinates)."""
    import FreeCAD

    def length(v):
        return FreeCAD.Units.Quantity(v, FreeCAD.Units.Length).UserString

    facts = []
    kind = shape.ShapeType
    if kind == "Face":
        surface = type(shape.Surface).__name__
        facts.append(("Shape", element_words("Face", surface)[1]))
        facts.append(("Area", FreeCAD.Units.Quantity(shape.Area, FreeCAD.Units.Area).UserString))
        if surface == "Plane":
            try:
                u0, u1, v0, v1 = shape.ParameterRange
                n = shape.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                facts.append(("Normal", f"{number(n.x)}, {number(n.y)}, {number(n.z)}"))
            except Exception:
                pass
        radius = getattr(shape.Surface, "Radius", None)
        if radius is not None and surface in ("Cylinder", "Sphere"):
            facts.append(("Diameter", "Ø " + length(2 * radius)))
    elif kind == "Edge":
        curve = type(shape.Curve).__name__
        facts.append(("Shape", element_words("Edge", curve=curve)[1]))
        facts.append(("Length", length(shape.Length)))
        radius = getattr(shape.Curve, "Radius", None)
        if radius is not None and curve == "Circle":
            facts.append(("Diameter", "Ø " + length(2 * radius)))
    elif kind == "Vertex":
        p = shape.Point
        facts += [("X", length(p.x)), ("Y", length(p.y)), ("Z", length(p.z))]
    return facts


def body_facts(obj):
    """[(label, text)] for a body or part: extent, volume, mass, material."""
    import FreeCAD

    import Part

    facts = []
    try:
        shape = Part.getShape(obj)
    except Exception:
        return facts
    if shape.isNull():
        return facts
    box = shape.BoundBox
    if box.isValid():
        dims = [FreeCAD.Units.Quantity(v, FreeCAD.Units.Length).getUserPreferred() for v in
                (box.XLength, box.YLength, box.ZLength)]
        unit = dims[0][2]
        facts.append(("Extent", " × ".join(number(v / d[1]) for v, d in
                                            zip((box.XLength, box.YLength, box.ZLength), dims)) + f" {unit}"))
    if shape.Solids:
        facts.append(("Volume", FreeCAD.Units.Quantity(shape.Volume, FreeCAD.Units.Volume).UserString))
    try:
        from bruceworx.partprops import computed_mass, computed_material

        mass = computed_mass(obj)
        material = computed_material(obj)
    except Exception:
        mass, material = None, None
    if mass:
        kg = FreeCAD.Units.Quantity(mass)
        facts.append(("Mass", kg.UserString if kg.Value else "no material"))
    if material is not None:
        facts.append(("Material", material or "none"))
    return facts


# ----- widgets -----------------------------------------------------------------------------


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    def no_safe_area(root):
        """macOS full-size content view: a widget made after the title bar moved would keep the old title
        strip's safe area as contents margins (fields land 230 px to the right). None may."""
        for widget in [root] + root.findChildren(QtWidgets.QWidget):
            if widget.testAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea):
                widget.setAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea, False)

    def quiet(label, alpha=0.6):
        pal = label.palette()
        colour = QtGui.QColor(pal.color(QtGui.QPalette.WindowText))
        colour.setAlphaF(alpha)
        pal.setColor(QtGui.QPalette.WindowText, colour)
        label.setPalette(pal)

    class Switch(QtWidgets.QAbstractButton):
        """A small on/off switch drawn in the accent colour."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setCheckable(True)
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setFocusPolicy(QtCore.Qt.TabFocus)
            self.setObjectName("bwInspectorSwitch")
            self.mixed = False

        def sizeHint(self):
            return QtCore.QSize(30, 18)

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            pal = self.palette()
            r = QtCore.QRectF(0.5, (self.height() - 16) / 2 + 0.5, 28, 16)
            on = self.isChecked() and not self.mixed
            track = QtGui.QColor(pal.color(QtGui.QPalette.Highlight)) if on else QtGui.QColor(
                pal.color(QtGui.QPalette.Mid))
            if not self.isEnabled():
                track.setAlpha(90)
            elif not on:
                track.setAlpha(150)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(r, 8, 8)
            knob = r.adjusted(2, 2, -2, -2)
            knob.setWidth(knob.height())
            if self.mixed:
                knob.moveLeft(r.center().x() - knob.width() / 2)
            elif on:
                knob.moveRight(r.right() - 2)
            p.setBrush(QtGui.QColor(255, 255, 255))
            p.drawEllipse(knob)
            if self.hasFocus():
                p.setPen(QtGui.QPen(pal.color(QtGui.QPalette.Highlight), 1.2))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 9, 9)
            p.end()

    class Segmented(QtWidgets.QFrame):
        """Two or three short choices side by side; the current one is marked."""

        chosen = QtCore.Signal(str)

        def __init__(self, choices, parent=None):
            super().__init__(parent)
            self.setObjectName("bwInspectorSegments")
            lay = QtWidgets.QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            self.group = QtWidgets.QButtonGroup(self)
            self.group.setExclusive(True)
            self.buttons = {}
            for i, choice in enumerate(choices):
                b = QtWidgets.QToolButton(self)
                b.setObjectName("bwInspectorSegment")
                b.setText(choice_text(choice))
                b.setCheckable(True)
                b.setAutoRaise(True)
                b.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
                b.setMinimumWidth(12)
                b.setProperty("position", "first" if i == 0 else "last" if i == len(choices) - 1 else "middle")
                b.clicked.connect(lambda _c=False, v=choice: self.chosen.emit(v))
                self.group.addButton(b)
                lay.addWidget(b)
                self.buttons[choice] = b

        def set_value(self, value, mixed=False):
            self.group.setExclusive(False)
            for choice, b in self.buttons.items():
                b.setChecked(not mixed and choice == str(value))
            self.group.setExclusive(True)

    class Section(QtWidgets.QWidget):
        """A folding section, as in a Fusion command dialog: ▾ title  sub  [actions]."""

        def __init__(self, key, title, sub="", actions=(), opened=True, summary="", parent=None):
            super().__init__(parent)
            self.setObjectName("bwInspectorSection")
            self.key = key
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            head = QtWidgets.QWidget(self)
            head.setObjectName("bwInspectorSectionHead")
            h = QtWidgets.QHBoxLayout(head)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(6)
            self.arrow = QtWidgets.QToolButton(head)
            self.arrow.setObjectName("bwInspectorFold")
            self.arrow.setAutoRaise(True)
            self.arrow.setIconSize(QtCore.QSize(9, 9))
            self.arrow.setCursor(QtCore.Qt.PointingHandCursor)
            self.arrow.clicked.connect(self.toggle)
            h.addWidget(self.arrow)
            self.title = QtWidgets.QLabel(title, head)
            self.title.setObjectName("bwInspectorTitle")
            font = self.title.font()
            font.setBold(True)
            self.title.setFont(font)
            h.addWidget(self.title)
            self.sub = QtWidgets.QLabel(sub, head)
            self.sub.setObjectName("bwInspectorSub")
            quiet(self.sub)
            h.addWidget(self.sub)
            self.summary_text = summary
            self.summary = QtWidgets.QLabel(summary, head)
            self.summary.setObjectName("bwInspectorSummary")
            self.summary.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            self.summary.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            quiet(self.summary, 0.45)
            h.addWidget(self.summary, 1)
            for widget in actions:
                h.addWidget(widget)
            head.mousePressEvent = lambda _e: self.toggle()
            lay.addWidget(head)
            self.body = QtWidgets.QWidget(self)
            self.body.setObjectName("bwInspectorSectionBody")
            self.grid = QtWidgets.QGridLayout(self.body)
            self.grid.setContentsMargins(18, 0, 0, 4)
            self.grid.setHorizontalSpacing(10)
            self.grid.setVerticalSpacing(3)
            self.grid.setColumnStretch(1, 1)
            self.grid.setColumnMinimumWidth(0, 78)
            self.r = 0
            lay.addWidget(self.body)
            self.set_open(opened)

        def set_open(self, opened):
            self.opened = bool(opened)
            self.body.setVisible(self.opened)
            self.arrow.setArrowType(QtCore.Qt.DownArrow if self.opened else QtCore.Qt.RightArrow)
            # The summary keeps its stretch when open (empty), so the title and actions stay put.
            self.summary.setText("" if self.opened else self.summary_text)

        def toggle(self):
            self.set_open(not self.opened)
            remember_fold(self.key, self.opened)

        def add_row(self, label, widget, tip=""):
            if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox,
                                   QtWidgets.QFrame)) and not isinstance(widget, QtWidgets.QLabel):
                widget.setMinimumWidth(36)
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
                widget.setSizePolicy(policy)
                widget.setFixedHeight(22)
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.setCursorPosition(0)
            if widget.sizePolicy().horizontalPolicy() == QtWidgets.QSizePolicy.Fixed:
                # A column of fixed-size widgets cannot grow: the labels would take the room.
                holder = QtWidgets.QWidget(self.body)
                h = QtWidgets.QHBoxLayout(holder)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(widget)
                h.addStretch(1)
                if widget.toolTip() == "" and tip:
                    widget.setToolTip(tip)
                widget = holder
            lab = QtWidgets.QLabel(label, self.body)
            lab.setObjectName("bwInspectorLabel")
            lab.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
            quiet(lab, 0.72)
            if tip:
                lab.setToolTip(tip)
                widget.setToolTip(tip)
            self.grid.addWidget(lab, self.r, 0)
            self.grid.addWidget(widget, self.r, 1)
            self.r += 1
            return widget

        def add_full(self, widget):
            self.grid.addWidget(widget, self.r, 0, 1, 2)
            self.r += 1

    class Inspector(QtWidgets.QWidget):
        """The Properties tab: folding sections over a scroll area, and a footer."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("BruceWorxInspector")
            self.controller = None
            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            self.scroll = QtWidgets.QScrollArea(self)
            self.scroll.setObjectName("bwInspectorScroll")
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            outer.addWidget(self.scroll, 1)
            foot = QtWidgets.QWidget(self)
            foot.setObjectName("bwInspectorFoot")
            f = QtWidgets.QHBoxLayout(foot)
            f.setContentsMargins(12, 4, 8, 6)
            self.physical = QtWidgets.QToolButton(foot)
            self.physical.setObjectName("bwInspectorMore")
            self.physical.setText("Show in Physical")
            self.physical.setAutoRaise(True)
            self.physical.setCursor(QtCore.Qt.PointingHandCursor)
            self.physical.clicked.connect(show_physical)
            f.addWidget(self.physical)
            f.addStretch(1)
            self.more = QtWidgets.QToolButton(foot)
            self.more.setObjectName("bwInspectorMore")
            self.more.setText("All properties  ›")
            self.more.setAutoRaise(True)
            self.more.setCursor(QtCore.Qt.PointingHandCursor)
            self.more.setToolTip("FreeCAD's property editor (the Data tab under the Browser)")
            self.more.clicked.connect(show_all_properties)
            f.addWidget(self.more)
            outer.addWidget(foot)
            self.wheel = _WheelGuard(self)
            self.clear()

        def clear(self):
            old = self.scroll.takeWidget()
            if old is not None:
                old.deleteLater()
            self.body = QtWidgets.QWidget()
            self.body.setObjectName("bwInspectorBody")
            self.column = QtWidgets.QVBoxLayout(self.body)
            self.column.setContentsMargins(12, 8, 10, 8)
            self.column.setSpacing(2)
            self.scroll.setWidget(self.body)

        def section(self, key, title, sub="", actions=(), opened=True, summary=""):
            s = Section(key, title, sub, actions, fold_state(key, opened), summary, self.body)
            self.column.addWidget(s)
            return s

        def separator(self):
            line = QtWidgets.QFrame(self.body)
            line.setObjectName("bwInspectorRule")
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFixedHeight(9)
            self.column.addWidget(line)

        def hint(self, text):
            lab = QtWidgets.QLabel(text, self.body)
            lab.setObjectName("bwInspectorHint")
            lab.setWordWrap(True)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            quiet(lab)
            lab.setContentsMargins(4, 24, 4, 24)
            self.column.addWidget(lab)

        def finish(self):
            self.column.addStretch(1)
            no_safe_area(self)

        def showEvent(self, event):
            no_safe_area(self)
            super().showEvent(event)

        def selection_changed(self):
            if self.controller is not None:
                self.controller.schedule()

    class _WheelGuard(QtCore.QObject):
        """Fields scroll the tab, not their value, unless they have the focus."""

        def __init__(self, inspector):
            super().__init__(inspector)
            self.inspector = inspector

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Wheel and not obj.hasFocus():
                QtWidgets.QApplication.sendEvent(self.inspector.scroll.verticalScrollBar(), event)
                return True
            return False

    return Switch, Segmented, Inspector


_classes = None


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


def fold_state(key, default):
    opened = _prefs().GetString(FOLD_PREF, "")
    closed = _prefs().GetString(FOLD_PREF + "Closed", "")
    if key in opened.split(","):
        return True
    if key in closed.split(","):
        return False
    return default


def remember_fold(key, opened):
    for name, add in ((FOLD_PREF, opened), (FOLD_PREF + "Closed", not opened)):
        keys = [k for k in _prefs().GetString(name, "").split(",") if k and k != key]
        if add:
            keys.append(key)
        _prefs().SetString(name, ",".join(keys))


def show_physical():
    try:
        import bruceworx_rightpanel

        bruceworx_rightpanel.show_tab("BWPhysical")
    except Exception:
        pass


def show_all_properties():
    """FreeCAD's property editor: the Data tab under the Browser."""
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, MODEL_DOCK)
    tabs = dock.findChild(QtWidgets.QTabWidget, "propertyTab") if dock is not None else None
    if tabs is None:
        return
    for i in range(tabs.count()):
        if tabs.tabText(i).replace("&", "") == "Data":
            tabs.setCurrentIndex(i)
    tabs.setFocus()


# ----- the controller: fills the tab from the selection -----------------------------------


class Controller:
    """Fills the Properties tab from the selection and keeps it current."""

    def __init__(self, inspector):
        from PySide import QtCore

        self.inspector = inspector
        inspector.controller = self
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(0)
        self.timer.timeout.connect(slot(self.refresh))
        self.busy = False
        self.signature = None
        self.editors = []

    def schedule(self):
        if self.timer is not None and _alive(self.inspector):
            self.timer.start()

    def stop(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer = None

    # -- refresh -----------------------------------------------------------------------------
    def refresh(self):
        if self.busy or not _alive(self.inspector):
            return
        if not self.inspector.isVisible() and self.signature is not None:
            return  # caught up when the tab is shown
        try:
            self._refresh()
        except Exception:
            import traceback

            import FreeCAD

            FreeCAD.Console.PrintError("BruceWorx Properties:\n" + traceback.format_exc())

    def _refresh(self):
        items = resolve_selection()
        pairs = [(obj, element) for _top, _sub, obj, element in items]
        kind = selection_kind(pairs)
        objs = [obj for obj, _e in pairs]
        rows = None
        if kind in ("object", "objects"):
            order = part_order() if any(PART_GROUP in _groups(o) for o in objs) else []
            rows = common_rows([plan_rows(o, order) for o in objs])
        part = part_of(objs[0]) if objs and len({id(part_of(o)) for o in objs}) == 1 else None
        signature = (kind, tuple((o.Document.Name, o.Name, o.Label) for o in objs),
                     tuple(e for _o, e in pairs), tuple(r.signature() for r in rows or ()),
                     (part.Name, part.Label) if part is not None else None)
        if signature == self.signature:
            self.update_values()
            return
        self.signature = signature
        insp = self.inspector
        self.editors = []
        insp.clear()
        if kind == "none":
            insp.hint(EMPTY_HINT)
        elif kind == "mixed":
            names = sorted({type_word(o) for o in objs})
            s = insp.section("mixed", f"{len(objs)} objects", ", ".join(names))
            s.add_full(self._value_label("Select objects of one type to edit them together."))
        elif kind in ("element", "elements"):
            self.build_elements(items)
        else:
            self.build_objects(objs, rows)
        if part is not None and kind != "mixed":
            insp.separator()
            self.build_part(part, objs)
        insp.finish()

    # -- object sections ---------------------------------------------------------------------
    def build_objects(self, objs, rows):
        from PySide import QtWidgets

        insp = self.inspector
        first = objs[0]
        if first is part_of(first) and first.TypeId == "App::Part":
            return  # the part section below says it all
        vp = getattr(first, "ViewObject", None)
        if len(objs) == 1:
            title, sub = first.Label, type_word(first)
        else:
            title, sub = f"{len(objs)} {type_word(first)}s", ", ".join(o.Label for o in objs[:3]) + (
                "…" if len(objs) > 3 else "")
        actions = []
        if len(objs) == 1 and first.TypeId not in CONTAINER_TYPES and vp is not None:
            edit = QtWidgets.QToolButton()
            edit.setObjectName("bwInspectorAction")
            edit.setText("Edit")
            edit.setToolTip(f"Open {first.Label} in its dialog")
            edit.clicked.connect(lambda: self.open_feature(first))
            actions.append(edit)
        actions.append(self._menu_button(objs))
        key_rows, more_rows = split_key_rows(first.TypeId, [r for r in rows if r.group != PART_DATA])
        s = insp.section("feature", title, sub, actions)
        if len(objs) == 1:
            self.add_configuration(s, first)
        for row in key_rows:
            self.add_editor(s, objs, row)
        if not key_rows and not more_rows:
            s.add_full(self._value_label(location_text(type_word(first), getattr(parent_of(first), "Label", ""))))
        if more_rows:
            names = []
            for r in more_rows:
                word = "Position" if r.part else r.label
                if word not in names:
                    names.append(word)
            more = insp.section("more", "More", "", (), opened=False, summary=", ".join(names))
            for row in more_rows:
                self.add_editor(more, objs, row)

    def _menu_button(self, objs):
        from PySide import QtWidgets

        first = objs[0]
        button = QtWidgets.QToolButton()
        button.setObjectName("bwInspectorMenu")
        button.setText("⋯")
        button.setAutoRaise(True)
        button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(button)
        if len(objs) == 1:
            menu.addAction("Rename…", lambda: self.rename(first))
        if all("Suppressed" in o.PropertiesList for o in objs):
            suppressed = all(bool(o.Suppressed) for o in objs)
            word = "Unsuppress" if suppressed else "Suppress"
            menu.addAction(word, lambda: self.commit(objs, word, lambda o: setattr(o, "Suppressed", not suppressed),
                                                     word))
        menu.addAction("All properties", show_all_properties)
        button.setMenu(menu)
        return button

    def build_part(self, part, objs):
        """The part the selection belongs to: identity, material, appearance, mass."""
        from PySide import QtWidgets

        insp = self.inspector
        is_part = PART_GROUP in _groups(part)
        title = "Part" if part.TypeId == "App::Part" else type_word(part)
        s = insp.section("part", title, part.Label)
        order = part_order() if is_part else []
        data = [r for r in plan_rows(part, order) if r.group == PART_DATA] if is_part else []
        by_name = {r.name: r for r in data}
        shown = set()
        if "PartNumber" in by_name:
            state = ""
            for name in ("LifecycleState", "State"):
                if name in part.PropertiesList and getattr(part, name):
                    state = str(getattr(part, name))
            rev = str(getattr(part, "Revision", "") or "")
            text = str(part.PartNumber or "—")
            badge = " · ".join(x for x in (state.upper() if state else "", rev) if x)
            label = self._value_label(f"{text}  ·  {badge}" if badge else text)
            s.add_row("Part number", label, by_name["PartNumber"].tip)
            self.editors.append((by_name["PartNumber"], [part], None, None))
            shown |= {"PartNumber", "Revision", "LifecycleState", "State"}
        if "Description" in by_name:
            self.add_editor(s, [part], by_name["Description"])
            shown.add("Description")
        self.add_configuration(s, part)
        body = body_of(part)
        material = None
        try:
            from bruceworx.partprops import computed_material

            material = computed_material(part)
        except Exception:
            pass
        mat = QtWidgets.QToolButton()
        mat.setObjectName("bwInspectorField")
        mat.setText((material or "Choose…") + "   ▾")
        mat.setToolTip("The body's material (FreeCAD's material editor)")
        if body is not None:
            mat.clicked.connect(lambda: self.run_on(body, "", "Std_SetMaterial"))
        else:
            mat.setEnabled(False)
        s.add_row("Material", mat)
        shown |= {"MaterialName", "Material"}
        if body is not None and has_appearance(body):
            self.add_appearance(s, [body])
        facts = dict(body_facts(part))
        if "Mass" in facts:
            s.add_row("Mass", self._value_label(facts["Mass"]))
            shown.add("Mass")
        rest = [r for r in data if r.name not in shown]
        if rest:
            more = insp.section("morepart", "More part data", "", (), opened=False,
                                summary=", ".join(r.label for r in rest[:4]))
            for row in rest:
                self.add_editor(more, [part], row)
        if part.TypeId == "App::Part" and "Placement" in part.PropertiesList:
            pos = insp.section("position", "Position", "", (), opened=False, summary="X, Y, Z, rotation")
            for row in plan_rows(part, []):
                if row.group == POSITION:
                    self.add_editor(pos, [part], row)

    def add_configuration(self, section, obj):
        """MOD-03: the active configuration of a configured part, or the one an instance shows."""
        try:
            import bruceworx_configs_gui

            box = bruceworx_configs_gui.combo(obj)
        except Exception:
            box = None
        if box is not None:
            section.add_row("Configuration", box, box.toolTip())

    def add_appearance(self, section, owners):
        """Colour and opacity of ``owners`` (bodies)."""
        from PySide import QtCore, QtGui, QtWidgets

        vps = [o.ViewObject for o in owners]
        colours = [tuple(vp.ShapeAppearance[0].DiffuseColor[:3]) for vp in vps]
        colour, mixed = merged(colours)
        swatch = QtWidgets.QToolButton()
        swatch.setObjectName("bwInspectorSwatch")
        swatch.setCursor(QtCore.Qt.PointingHandCursor)
        swatch.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        swatch.setAutoRaise(True)

        def paint(rgb, is_mixed):
            if not _alive(swatch):
                return
            pix = QtGui.QPixmap(26, 14)
            pix.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pix)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setPen(QtGui.QPen(QtGui.QColor(128, 128, 128, 160), 1))
            p.setBrush(QtGui.QColor.fromRgbF(*rgb) if not is_mixed else QtGui.QColor(0, 0, 0, 0))
            p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, 25, 13), 4, 4)
            p.end()
            swatch.setIcon(QtGui.QIcon(pix))
            swatch.setIconSize(pix.size())
            swatch.setText(MIXED if is_mixed else QtGui.QColor.fromRgbF(*rgb).name().upper())

        paint(colour, mixed)

        def choose():
            picked = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*colour), self.inspector, "Colour")
            if not picked.isValid():
                return
            rgb = (picked.redF(), picked.greenF(), picked.blueF())

            def apply(owner):
                vp = owner.ViewObject
                materials = list(vp.ShapeAppearance)
                for m in materials:
                    alpha = m.DiffuseColor[3] if len(m.DiffuseColor) > 3 else 1.0
                    m.DiffuseColor = rgb + (alpha,)
                vp.ShapeAppearance = materials

            self.commit(owners, "Colour", apply, "Colour", recompute=False)

        swatch.clicked.connect(choose)
        section.add_row("Appearance", swatch, "The body's colour; its features follow it")
        self.editors.append(("colour", owners, swatch, lambda: paint(
            *merged([tuple(o.ViewObject.ShapeAppearance[0].DiffuseColor[:3]) for o in owners]))))

    # -- editors -----------------------------------------------------------------------------
    def _value_label(self, text):
        from PySide import QtCore, QtWidgets

        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwInspectorValue")
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        # No word wrap: wrapped labels upset the grid's columns; a long value is clipped, the tooltip has it all.
        lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lab.setMinimumWidth(20)
        lab.setToolTip(text)
        return lab

    def add_editor(self, section, objs, row):
        import FreeCADGui
        from PySide import QtCore, QtWidgets

        Switch, Segmented, _Inspector = classes()
        wheel = self.inspector.wheel
        values = [value_of(o, row) for o in objs]
        value, mixed = merged(values, value_text)
        label, tip = row.label, row.tip
        if row.kind == READONLY or row.readonly:
            section.add_row(label, self._value_label(MIXED if mixed else value_text(value)), tip)
            self.editors.append((row, objs, None, None))
            return
        widget = None
        binding = None
        if row.kind == QUANTITY:
            widget = FreeCADGui.UiLoader().createWidget("Gui::QuantitySpinBox")
            widget.setObjectName("bwInspectorQuantity")
            widget.setProperty("keyboardTracking", False)
            widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            if row.part:
                widget.setProperty("unit", "mm" if row.prop_type == "Length" else "deg")
            if row.prop_type == "App::PropertyLength":
                widget.setProperty("minimum", 0.0)
            elif row.part or row.prop_type in ("App::PropertyDistance", "App::PropertyAngle",
                                               "App::PropertyQuantity"):
                widget.setProperty("minimum", -1e12)
            widget.setProperty("maximum", 1e12)
            self._set_quantity(widget, value, mixed)
            if len(objs) == 1:
                try:
                    path = row.name if not row.part else (
                        f"{row.name}.Base.{row.part}" if row.part in ("x", "y", "z") else "")
                    if path:
                        binding = FreeCADGui.ExpressionBinding(widget)
                        binding.bind(objs[0], path)
                except Exception:
                    binding = None
            widget.valueChanged.connect(lambda _v, r=row, w=widget: self._typed(objs, r, w))
            widget.installEventFilter(wheel)
            widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        elif row.kind == INTEGER:
            widget = QtWidgets.QSpinBox()
            widget.setObjectName("bwInspectorInteger")
            widget.setKeyboardTracking(False)
            widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            widget.setRange(-(2 ** 31) + 1, 2 ** 31 - 1)
            if row.prop_type == "App::PropertyIntegerConstraint":
                widget.setMinimum(1 if row.name.startswith("Occurrences") else 0)
            widget.setValue(int(value or 0))
            if mixed:
                widget.setSpecialValueText(MIXED)
            widget.valueChanged.connect(lambda v, r=row: self.set_rows(objs, r, int(v)))
            widget.installEventFilter(wheel)
            widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        elif row.kind == FLOAT:
            widget = QtWidgets.QLineEdit(MIXED if mixed else value_text(value))
            widget.setObjectName("bwInspectorText")
            widget.editingFinished.connect(lambda r=row, w=widget: self._float_typed(objs, r, w))
        elif row.kind == CHOICE:
            if segmented(row.choices):
                widget = Segmented(row.choices)
                widget.set_value(value, mixed)
                widget.chosen.connect(lambda v, r=row: self.set_rows(objs, r, v))
            else:
                widget = QtWidgets.QComboBox()
                widget.setObjectName("bwInspectorChoice")
                if mixed:
                    widget.addItem(MIXED, None)
                for choice in row.choices:
                    widget.addItem(choice_text(choice), choice)
                index = widget.findData(str(value))
                widget.setCurrentIndex(0 if mixed or index < 0 else index)
                widget.activated.connect(
                    lambda i, r=row, w=widget: w.itemData(i) is not None and self.set_rows(objs, r, w.itemData(i)))
                widget.installEventFilter(wheel)
                widget.setFocusPolicy(QtCore.Qt.StrongFocus)
        elif row.kind == SWITCH:
            holder = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(holder)
            h.setContentsMargins(0, 1, 0, 1)
            switch = Switch()
            switch.setChecked(bool(value))
            switch.mixed = mixed
            switch.toggled.connect(lambda on, r=row: self.set_rows(objs, r, bool(on)))
            h.addWidget(switch)
            h.addStretch(1)
            section.add_row(label, holder, tip)
            self.editors.append((row, objs, switch, None))
            return
        elif row.kind == LINK:
            section.add_row(label, self._chips(value if not mixed else None, mixed), tip)
            self.editors.append((row, objs, None, None))
            return
        elif row.kind == TEXT:
            widget = QtWidgets.QLineEdit("" if mixed else str(value))
            widget.setObjectName("bwInspectorText")
            if mixed:
                widget.setPlaceholderText(MIXED)
            widget.editingFinished.connect(
                lambda r=row, w=widget: w.isModified() and self.set_rows(objs, r, w.text()))
        if widget is None:
            section.add_row(label, self._value_label(value_text(value)), tip)
            return
        section.add_row(label, widget, tip)
        self.editors.append((row, objs, widget, binding))

    def _set_quantity(self, widget, value, mixed):
        from PySide import QtWidgets

        widget.blockSignals(True)
        try:
            widget.setProperty("value", value)
        except Exception:
            widget.setProperty("rawValue", float(getattr(value, "Value", value or 0)))
        line = widget.findChild(QtWidgets.QLineEdit)
        if mixed and line is not None:
            line.clear()
            line.setPlaceholderText(MIXED)
        widget.blockSignals(False)

    def _chips(self, value, mixed):
        from PySide import QtCore, QtWidgets

        holder = QtWidgets.QWidget()
        flow = QtWidgets.QHBoxLayout(holder)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(4)
        targets = link_targets(value) if not mixed else []
        if mixed:
            flow.addWidget(self._value_label(MIXED))
        elif not targets:
            flow.addWidget(self._value_label("none"))
        for obj, subs in targets[:3]:
            chip = QtWidgets.QToolButton()
            chip.setObjectName("bwInspectorChip")
            chip.setText(link_text(obj, subs))
            chip.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            chip.setCursor(QtCore.Qt.PointingHandCursor)
            chip.setToolTip(f"Select {obj.Label}")
            vp = getattr(obj, "ViewObject", None)
            if vp is not None:
                chip.setIcon(vp.Icon)
            chip.clicked.connect(lambda _c=False, o=obj: self.select(o))
            flow.addWidget(chip)
        if len(targets) > 3:
            flow.addWidget(self._value_label(f"+{len(targets) - 3}"))
        flow.addStretch(1)
        return holder

    def update_values(self):
        """Same tab, new values (a recompute, an undo, a drag in the view)."""
        Switch, Segmented, _Inspector = classes()
        from PySide import QtWidgets

        for row, objs, widget, binding in self.editors:
            if callable(binding) and not isinstance(row, Row):
                if _alive(widget):
                    binding()  # appearance rows carry their own update
                continue
            if widget is None or not _alive(widget) or widget.hasFocus():
                continue
            if any(not _obj_alive(o) for o in objs):
                self.signature = None
                self.schedule()
                return
            try:
                value, mixed = merged([value_of(o, row) for o in objs], value_text)
            except Exception:
                continue
            widget.blockSignals(True)
            try:
                if row.kind == QUANTITY:
                    if value_text(widget.property("value")) != value_text(value) or mixed:
                        self._set_quantity(widget, value, mixed)
                elif row.kind == INTEGER:
                    widget.setValue(int(value or 0))
                elif isinstance(widget, Segmented):
                    widget.set_value(value, mixed)
                elif isinstance(widget, QtWidgets.QComboBox):
                    index = widget.findData(str(value))
                    if index >= 0 and not mixed:
                        widget.setCurrentIndex(index)
                elif isinstance(widget, Switch):
                    widget.setChecked(bool(value))
                    widget.mixed = mixed
                    widget.update()
                elif isinstance(widget, QtWidgets.QLineEdit):
                    widget.setText("" if mixed else value_text(value) if row.kind == FLOAT else str(value))
            finally:
                widget.blockSignals(False)

    # -- changes -----------------------------------------------------------------------------
    def _typed(self, objs, row, widget):
        value = widget.property("rawValue")  # in FreeCAD's internal units (mm, degrees)
        if value is None:
            return
        self.set_rows(objs, row, float(value))

    def _float_typed(self, objs, row, widget):
        if not widget.isModified():
            return
        try:
            value = float(widget.text().replace(",", "."))
        except ValueError:
            self.update_values()
            return
        self.set_rows(objs, row, value)

    def set_rows(self, objs, row, value):
        self.commit(objs, row.label, lambda o: set_value(o, row, value), row.label)

    def commit(self, objs, field, change, label=None, recompute=True):
        """One undo step: ``change(obj)`` for each object, then a recompute."""
        import FreeCAD

        objs = [o for o in objs if _obj_alive(o)]
        if not objs:
            return False
        doc = objs[0].Document
        name = transaction_name([o.Label for o in objs], label or field)
        self.busy = True
        doc.openTransaction(name)
        try:
            for obj in objs:
                change(obj)
            if recompute:
                doc.recompute()
        except Exception as exc:
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"Properties: {name}: {exc}\n")
            ok = False
        else:
            doc.commitTransaction()
            ok = True
        finally:
            self.busy = False
        self.schedule()
        return ok

    def rename(self, obj):
        from PySide import QtWidgets

        text, ok = QtWidgets.QInputDialog.getText(self.inspector, "Rename", "Name:", text=obj.Label)
        if ok and text.strip() and text != obj.Label:
            self.signature = None
            self.commit([obj], "Rename", lambda o: setattr(o, "Label", text.strip()), "Rename", recompute=False)

    # -- actions -----------------------------------------------------------------------------
    def select(self, obj):
        import FreeCADGui

        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(obj.Document.Name, obj.Name)

    def open_feature(self, obj):
        try:
            import bruceworx_picktrace

            bruceworx_picktrace.open_feature(obj)
        except Exception:
            import FreeCADGui

            FreeCADGui.getDocument(obj.Document.Name).setEdit(obj.Name)

    def run_on(self, obj, sub, command):
        """Select ``obj`` and run ``command``."""
        import FreeCADGui

        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(obj.Document.Name, obj.Name, sub)
        FreeCADGui.runCommand(command)

    def sketch_on_face(self, top, sub, owner):
        """PartDesign's New Sketch on the picked face, in the face's body."""
        import FreeCADGui

        try:
            import PartDesignGui  # noqa: F401
        except Exception:
            pass
        body = owner if getattr(owner, "TypeId", "") == "PartDesign::Body" else body_of(owner)
        view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
        if body is not None and view is not None:
            try:
                view.setActiveObject("pdbody", body)
            except Exception:
                pass
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(top.Document.Name, top.Name, sub)
        FreeCADGui.runCommand("PartDesign_NewSketch")

    # -- element sections --------------------------------------------------------------------
    def build_elements(self, items):
        import FreeCADGui
        import Part
        from PySide import QtWidgets

        insp = self.inspector
        if len(items) > 1:
            kinds = {split_element(e)[0] for _t, _s, _o, e in items if e}
            word = kinds.pop().lower() + "s" if len(kinds) == 1 else "elements"
            s = insp.section("element", f"{len(items)} {word}", "")
            s.add_full(self._action("Measure", lambda: FreeCADGui.runCommand("Std_Measure")))
            return
        top, sub, owner, element = items[0]
        kind, index = split_element(element)
        try:
            shape = Part.getShape(top, sub, needSubElement=True)
        except Exception:
            shape = None
        facts = face_facts(shape) if shape is not None and not shape.isNull() else []
        shape_word = facts[0][1] if facts else kind
        maker = None
        try:
            import bruceworx_picktrace

            maker = bruceworx_picktrace.trace(owner, element)
        except Exception:
            maker = None
        s = insp.section("element", f"{kind} {index}", shape_word)
        for label, text in facts[1:]:
            s.add_row(label, self._value_label(text))
        if maker is not None:
            s.add_row("Made by", self._chips(maker, False))
            profile = getattr(maker, "Profile", None)
            sketch = profile[0] if isinstance(profile, (tuple, list)) and profile else profile
            if sketch is not None and hasattr(sketch, "Name"):
                s.add_row("Sketch", self._chips(sketch, False))
        actions = []
        if maker is not None:
            actions.append(self._action(f"Edit {maker.Label}", lambda: self.open_feature(maker)))
        actions.append(self._action("Measure", lambda: FreeCADGui.runCommand("Std_Measure")))
        if kind == "Face" and shape is not None and type(getattr(shape, "Surface", None)).__name__ == "Plane":
            actions.append(self._action("Sketch on face", lambda: self.sketch_on_face(top, sub, owner)))
        buttons = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(buttons)  # two a row: the panel is narrow
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setSpacing(6)
        for i, button in enumerate(actions):
            button.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            button.setMinimumWidth(40)
            grid.addWidget(button, i // 2, i % 2)
        s.add_full(buttons)

    def _action(self, text, slot):
        from PySide import QtWidgets

        b = QtWidgets.QPushButton(text)
        b.setObjectName("bwInspectorAction")
        b.clicked.connect(slot)
        return b


def _groups(obj):
    try:
        return {obj.getGroupOfProperty(n) for n in obj.PropertiesList}
    except Exception:
        return set()


def body_of(obj):
    """The PartDesign body of ``obj`` (itself, its parent, or the one body of a part)."""
    if obj is None:
        return None
    if getattr(obj, "TypeId", "") == "PartDesign::Body":
        return obj
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:
        parent = None
    if parent is not None and parent.TypeId == "PartDesign::Body":
        return parent
    bodies = [o for o in getattr(obj, "Group", None) or [] if getattr(o, "TypeId", "") == "PartDesign::Body"]
    return bodies[0] if len(bodies) == 1 else None


def part_of(obj):
    """The item the selection belongs to: the nearest object carrying part data (an App::Part), else its
    body, else None."""
    seen = 0
    cur = obj
    while cur is not None and seen < 20:
        if PART_GROUP in _groups(cur):
            return cur
        try:
            cur = cur.getParentGeoFeatureGroup()
        except Exception:
            cur = None
        seen += 1
    return body_of(obj)


# ----- install -----------------------------------------------------------------------------

_controller = None
_installed = False
_doc_observer = None
_gui_observer = None


def controller():
    return _controller


def _schedule(*_args):
    if _controller is not None:
        _controller.schedule()


class _DocObserver:
    """App and Gui document changes: the tab follows recomputes, undo, redo and drags."""

    def slotChangedObject(self, obj, prop):
        _schedule()

    def slotRecomputedDocument(self, doc):
        _schedule()

    def slotUndoDocument(self, doc):
        _schedule()

    def slotRedoDocument(self, doc):
        _schedule()

    def slotDeletedObject(self, obj):
        if _controller is not None:
            _controller.signature = None
        _schedule()


def _factory(parent):
    global _controller
    _Switch, _Segmented, Inspector = classes()
    if _controller is not None:
        _controller.stop()
    inspector = Inspector(parent)
    _controller = Controller(inspector)
    return inspector


def _shutdown():
    global _controller
    import FreeCAD
    import FreeCADGui

    for remove, observer in ((FreeCAD.removeDocumentObserver, _doc_observer),
                             (FreeCADGui.removeDocumentObserver, _gui_observer)):
        if observer is not None:
            try:
                remove(observer)
            except Exception:
                pass
    if _controller is not None:
        _controller.stop()
        _controller = None


def install():
    """The Properties tab of the right panel (bruceworx_rightpanel), and the observers that keep it current."""
    global _installed, _doc_observer, _gui_observer
    if _installed:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    import bruceworx_rightpanel

    if not bruceworx_rightpanel.enabled():
        return
    bruceworx_rightpanel.register(bruceworx_rightpanel.PROPERTIES, _factory)
    _doc_observer = _DocObserver()
    FreeCAD.addDocumentObserver(_doc_observer)
    _gui_observer = _DocObserver()
    try:
        FreeCADGui.addDocumentObserver(_gui_observer)
    except Exception:
        _gui_observer = None
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _installed = True
