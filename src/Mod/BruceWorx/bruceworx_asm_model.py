# SPDX-License-Identifier: LGPL-2.1-or-later
"""The assembly Model Builder's model layer (ASM-UI-01, 2 Nov 2026), headless.

A mate is a node in the assembly tree, like a force in a study; its Settings edit the joint live. Nothing here opens
a dialog. The pane code (bruceworx_asm_panes) is built on this API.

    MATES, KINDS, fitting(k1, k2), mate_label(key), joint_type_of(key), mate_group(key)     pure tables
    element_kind(obj, sub)            -> "plane" | "cylinder" | "line" | "circle" | "point" | ...
    pick_ref(assembly, doc, obj, sub) -> [component, [element, element]] or None
    ref_label(ref)                    -> "Carriage · Face4"
    mate_key(joint)                   -> the mate key; BW_MateType when it still agrees with JointType
    create_mate / set_mate_type / set_value / replace_ref / delete_mate     writes, one document transaction each
    tree(assembly)                    -> Node      keys "comp/<Name>", "mate/<Name>", "motion/<Name>", "check/dof"
    settings(joint)                   -> [Section]   Selections, Mate type, Placement, Limits (some types), Result
    new_mate_settings(picks)          -> [Section]   the form for a mate that does not exist yet

A mate is a FreeCAD joint. Coincident and Tangent are Distance joints at 0 (a Distance joint between two cylinders
adds both radii, so Concentric is the Cylindrical joint). The mate key is stored in ``BW_MateType`` on the joint.
Every write re-solves the assembly and keeps the solver's return code for the Result section; the C++ solver's
conflict list is not exposed to Python, so no mate is marked "bad" for conflicts yet.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field as dc_field

MATE_PROP = "BW_MateType"

MATES = (  # key, label, group, joint type (JointObject.JointTypes name), value kind ("distance", "angle" or "")
    ("coincident", "Coincident", "standard", "Distance", ""),
    ("parallel", "Parallel", "standard", "Parallel", ""),
    ("perpendicular", "Perpendicular", "standard", "Perpendicular", ""),
    ("tangent", "Tangent", "standard", "Distance", ""),
    ("concentric", "Concentric", "standard", "Cylindrical", ""),
    ("lock", "Lock", "standard", "Fixed", ""),
    ("distance", "Distance", "standard", "Distance", "distance"),
    ("angle", "Angle", "standard", "Angle", "angle"),
    ("hinge", "Hinge", "mechanical", "Revolute", ""),
    ("slider", "Slider", "mechanical", "Slider", ""),
    ("cylindrical", "Cylindrical", "mechanical", "Cylindrical", ""),
    ("ball", "Ball", "mechanical", "Ball", ""),
    ("gear", "Gear", "mechanical", "Gears", ""),
    ("rack", "Rack and pinion", "mechanical", "RackPinion", ""),
    ("screw", "Screw", "mechanical", "Screw", ""),
    ("belt", "Belt", "mechanical", "Belt", ""),
)
KINDS = ("plane", "cylinder", "line", "circle", "point", "sphere", "cone", "torus", "other")

_BY_KEY = {m[0]: m for m in MATES}
_ROUND = {"cylinder", "circle"}
_LIMITED = {"slider": "length", "cylindrical": "length", "hinge": "angle"}
_ZERO = ("coincident", "tangent")

# ----- the data classes ------------------------------------------------------------------------------------------


@dataclass
class Node:
    key: str
    label: str
    kind: str  # assembly, section, component, mate, motion, check
    obj: str = None  # the document object's Name, or None for a virtual node
    children: list = dc_field(default_factory=list)
    meta: str = ""  # short grey text
    mark: str = ""  # "(f)", "(-)" or ""
    state: str = ""  # "ok", "warn", "stale", "bad" or ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, key):
        return next((n for n in self.walk() if n.key == key), None)


@dataclass
class Field:
    name: str
    label: str
    kind: str  # picks, mategrid, float, bool, action, readonly
    value: object = None
    unit: str = ""
    choices: tuple = ()  # mategrid: every key, standard first, then the mechanical ones
    enabled_choices: tuple = ()
    help: str = ""
    tags: tuple = ()  # picks: the kind of each pick


@dataclass
class Section:
    title: str
    open: bool = True
    fields: list = dc_field(default_factory=list)


# ----- the pure tables ---------------------------------------------------------------------------------------------


def mate_label(key):
    return _BY_KEY[key][1]


def joint_type_of(key):
    return _BY_KEY[key][3]


def mate_group(key):
    return _BY_KEY[key][2]


def _value_kind(key):
    return _BY_KEY[key][4]


def fitting(kind1, kind2):
    """The mate keys that fit two picks, the default first. The order of the picks does not matter."""
    pair = {kind1, kind2}
    both = (kind1, kind2)
    if "point" in pair:
        return ["coincident", "distance"]
    if pair == {"plane"}:
        return ["coincident", "distance", "parallel", "perpendicular", "angle", "lock"]
    if all(k in _ROUND for k in both):
        return ["concentric", "hinge", "distance", "lock", "gear", "belt", "cylindrical", "slider"]
    if pair == {"plane", "cylinder"}:
        return ["tangent", "distance", "parallel", "perpendicular"]
    if pair == {"line"}:
        return ["coincident", "parallel", "perpendicular", "angle", "distance"]
    if pair == {"line", "plane"}:
        return ["coincident", "parallel", "perpendicular", "distance"]
    return ["coincident", "distance", "lock"]


_KEY_OF_TYPE = {
    "Fixed": "lock", "Revolute": "hinge", "Cylindrical": "cylindrical", "Slider": "slider", "Ball": "ball",
    "Parallel": "parallel", "Perpendicular": "perpendicular", "Angle": "angle", "RackPinion": "rack",
    "Screw": "screw", "Gears": "gear", "Belt": "belt",
}

# ----- FreeCAD side: picks -----------------------------------------------------------------------------------------


def _subshape(obj, sub):
    import Part

    try:
        shape = Part.getShape(obj, sub, needSubElement=True, retType=0)
        if shape is not None and not shape.isNull():
            return shape
    except Exception:
        pass
    try:
        return obj.Shape.getElement(sub.split(".")[-1])
    except Exception:
        return None


def element_kind(obj, sub) -> str:
    """KINDS for a picked element: the surface of a face, the curve of an edge, or a vertex."""
    shape = _subshape(obj, sub)
    if shape is None:
        return "other"
    kind = shape.ShapeType
    if kind == "Vertex":
        return "point"
    if kind == "Face":
        name = type(shape.Surface).__name__
        return {"Plane": "plane", "Cylinder": "cylinder", "Sphere": "sphere", "Cone": "cone",
                "Toroid": "torus"}.get(name, "other")
    if kind == "Edge":
        name = type(shape.Curve).__name__
        if name in ("Line", "LineSegment"):
            return "line"
        if name == "Circle":
            return "circle"
    return "other"


def pick_ref(assembly, doc_name, obj_name, sub):
    """A 3D-view selection as a joint reference, as TaskAssemblyCreateJoint.addSelection builds it."""
    import FreeCAD as App
    import UtilsAssembly

    root = App.getDocument(doc_name).getObject(obj_name)
    if root is None:
        return None
    sub = root.resolveSubElement(sub, True)[2]
    sub = UtilsAssembly.fixBodyExtraFeatureInSub(doc_name, sub)
    comp, new_sub = UtilsAssembly.getComponentReference(assembly, root, sub)
    if not comp:
        return None
    # The task panel adds the closest vertex; repeating the element selects the face/edge centre instead.
    return [comp, [new_sub, new_sub]]


def _ref_parts(ref):
    """(component, first element name) from a list or from the tuple a Reference property returns."""
    if ref is None or len(ref) != 2 or ref[0] is None:
        return None, ""
    subs = list(ref[1])
    return ref[0], (subs[0] if subs else "")


def ref_label(ref) -> str:
    comp, sub = _ref_parts(ref)
    if comp is None:
        return ""
    name = comp.Label or comp.Name
    last = sub.split(".")[-1] if sub else ""
    return f"{name} · {last}" if last else name


def _ref_kind(ref):
    comp, sub = _ref_parts(ref)
    if comp is None:
        return "other"
    if not sub:
        return "other"
    target = comp.getLinkedObject(True) if comp.isDerivedFrom("App::Link") else comp
    return element_kind(target, sub)


def _kinds(joint):
    return _ref_kind(joint.Reference1), _ref_kind(joint.Reference2)


# ----- FreeCAD side: reading ---------------------------------------------------------------------------------------


def _is_mate(obj):
    return hasattr(obj, "JointType") and hasattr(obj, "Reference1")


def _assembly_of(joint):
    return joint.Proxy.getAssembly(joint)


def mate_key(joint) -> str:
    """The stored BW_MateType when it still agrees with JointType, else derived from the joint."""
    jt = joint.JointType
    stored = getattr(joint, MATE_PROP, "")
    if stored in _BY_KEY and joint_type_of(stored) == jt:
        return stored
    if jt != "Distance":
        return _KEY_OF_TYPE.get(jt, "coincident")
    if abs(joint.Distance.Value) > 1e-9:
        return "distance"
    return "tangent" if set(_kinds(joint)) == {"plane", "cylinder"} else "coincident"


# ----- FreeCAD side: writing ---------------------------------------------------------------------------------------

_STATUS = {}  # (document, assembly) -> the solver's return code after the last write


@contextmanager
def _transaction(doc, name):
    opened = not doc.HasPendingTransaction
    if opened:
        doc.openTransaction(name)
    try:
        yield
    except BaseException:
        if opened:
            doc.abortTransaction()
        raise
    else:
        if opened:
            doc.commitTransaction()


def _solve(assembly):
    code = assembly.solve()
    _STATUS[(assembly.Document.Name, assembly.Name)] = code
    return code


def _next_label(assembly, key, ignore=None):
    base = mate_label(key)
    top = 0
    for obj in _mates_in(assembly):
        if obj is ignore:
            continue
        m = re.fullmatch(re.escape(base) + r" (\d+)", obj.Label)
        if m:
            top = max(top, int(m.group(1)))
    return f"{base} {top + 1}"


def _mates_in(assembly):
    import UtilsAssembly

    return [o for o in UtilsAssembly.getJointGroup(assembly).Group if _is_mate(o)]


def _store_key(joint, key):
    if not hasattr(joint, MATE_PROP):
        joint.addProperty("App::PropertyString", MATE_PROP, "Joint", "The mate this joint stands for")
    joint.setPropertyStatus(MATE_PROP, "Hidden")
    if getattr(joint, MATE_PROP) != key:
        setattr(joint, MATE_PROP, key)


def create_mate(assembly, ref1, ref2, key=None, value=None, label=None):
    import FreeCAD as App
    import JointObject
    import UtilsAssembly

    if key is None:
        key = fitting(_ref_kind(ref1), _ref_kind(ref2))[0]
    doc = assembly.Document
    with _transaction(doc, "Create mate"):
        group = UtilsAssembly.getJointGroup(assembly)
        joint = group.newObject("App::FeaturePython", "Joint")
        JointObject.Joint(joint, JointObject.JointTypes.index(joint_type_of(key)))
        if App.GuiUp:
            JointObject.ViewProviderJoint(joint.ViewObject)
        _store_key(joint, key)
        joint.Label = label or _next_label(assembly, key, ignore=joint)
        if key in _ZERO:
            joint.Distance = 0
        elif value is not None and _value_kind(key) == "distance":
            joint.Distance = value
        elif value is not None and _value_kind(key) == "angle":
            joint.Angle = value
        joint.Proxy.setJointConnectors(joint, [_as_list(ref1), _as_list(ref2)])
        group.purgeTouched()
        assembly.purgeTouched()
        joint.purgeTouched()
        _solve(assembly)
    return joint


def _as_list(ref):
    return [ref[0], list(ref[1])]


def set_mate_type(joint, key):
    doc = joint.Document
    assembly = _assembly_of(joint)
    with _transaction(doc, "Mate type"):
        joint.Proxy.setJointType(joint, joint_type_of(key))
        if key in _ZERO:
            joint.Distance = 0
        _store_key(joint, key)
        if re.fullmatch(r"(%s) \d+" % "|".join(re.escape(m[1]) for m in MATES), joint.Label):
            joint.Label = _next_label(assembly, key, ignore=joint)
        _solve(assembly)


def set_value(joint, name, value=None):
    doc = joint.Document
    assembly = _assembly_of(joint)
    props = {"limit_enabled_min": None, "limit_enabled_max": None, "limit_min": None, "limit_max": None}
    with _transaction(doc, "Mate " + name):
        if name == "label":
            joint.Label = str(value)
            return
        if name == "distance":
            joint.Distance = float(value)
        elif name == "angle":
            joint.Angle = float(value)
        elif name == "flip":
            joint.Proxy.flipOnePart(joint)
        elif name in props:
            kind = "Angle" if _LIMITED.get(mate_key(joint)) == "angle" else "Length"
            side = "Min" if name.endswith("min") else "Max"
            if name.startswith("limit_enabled"):
                setattr(joint, f"Enable{kind}{side}", bool(value))
            else:
                setattr(joint, f"{kind}{side}", float(value))
        else:
            raise ValueError(f"unknown mate value: {name}")
        _solve(assembly)


def replace_ref(joint, index, ref):
    if index not in (0, 1):
        raise ValueError("index is 0 or 1")
    refs = [_as_list(joint.Reference1), _as_list(joint.Reference2)]
    refs[index] = _as_list(ref)
    assembly = _assembly_of(joint)
    with _transaction(joint.Document, "Replace mate pick"):
        joint.Proxy.setJointConnectors(joint, refs)
        _solve(assembly)


def delete_mate(joint):
    doc = joint.Document
    assembly = _assembly_of(joint)
    with _transaction(doc, "Delete mate"):
        doc.removeObject(joint.Name)
        _solve(assembly)


# ----- FreeCAD side: the tree --------------------------------------------------------------------------------------

_CONTAINERS = ("Assembly::JointGroup", "Assembly::SimulationGroup", "Assembly::BomGroup", "App::Origin")


def _components(assembly):
    out = []
    for obj in assembly.Group:
        if obj.TypeId in _CONTAINERS or _is_mate(obj) or hasattr(obj, "ObjectToGround"):
            continue
        if obj.isDerivedFrom("App::Link") or obj.isDerivedFrom("Part::Feature") or obj.isDerivedFrom(
            "PartDesign::Body"
        ) or obj.isDerivedFrom("Assembly::AssemblyLink") or obj.isDerivedFrom("App::Part"):
            out.append(obj)
    return out


def _joints(assembly):
    import UtilsAssembly

    return list(UtilsAssembly.getJointGroup(assembly).Group)


def _mate_meta(joint):
    names = []
    for ref in (joint.Reference1, joint.Reference2):
        comp, _ = _ref_parts(ref)
        if comp is not None:
            names.append(comp.Label or comp.Name)
    return " · ".join(names)


def tree(assembly) -> Node:
    root = Node(f"asm/{assembly.Name}", assembly.Label, "assembly", assembly.Name)
    grounded = set()
    mates = []
    for joint in _joints(assembly):
        target = getattr(joint, "ObjectToGround", None)
        if hasattr(joint, "ObjectToGround"):
            if target is not None:
                grounded.add(target.Name)
        elif _is_mate(joint):
            mates.append(joint)

    comps = Node("sec/components", "Components", "section")
    for obj in _components(assembly):
        comps.children.append(
            Node(f"comp/{obj.Name}", obj.Label, "component", obj.Name,
                 mark="(f)" if obj.Name in grounded else "")
        )

    mnode = Node("sec/mates", "Mates", "section")
    for joint in mates:
        complete = joint.Reference1 is not None and joint.Reference2 is not None
        mnode.children.append(
            Node(f"mate/{joint.Name}", joint.Label, "mate", joint.Name, meta=_mate_meta(joint),
                 state="ok" if complete else "warn")
        )

    motion = Node("sec/motion", "Motion", "section")
    for group in assembly.Group:
        if group.TypeId == "Assembly::SimulationGroup":
            for sim in group.Group:
                motion.children.append(Node(f"motion/{sim.Name}", sim.Label, "motion", sim.Name))

    checks = Node("sec/checks", "Checks", "section",
                  children=[Node("check/dof", "Degrees of freedom", "check", meta="after B2")])
    root.children = [comps, mnode, motion, checks]
    return root


# ----- FreeCAD side: settings --------------------------------------------------------------------------------------


def _mate_grid(value, enabled):
    standard = tuple(m[0] for m in MATES if m[2] == "standard")
    mechanical = tuple(m[0] for m in MATES if m[2] == "mechanical")
    return Field("mate_type", "Mate type", "mategrid", value, choices=standard + mechanical,
                 enabled_choices=tuple(enabled), help="Mechanical mates sit under the fold")


def settings(joint) -> list:
    key = mate_key(joint)
    k1, k2 = _kinds(joint)
    refs = (joint.Reference1, joint.Reference2)
    sections = [
        Section("Selections", True, [
            Field("picks", "Selections", "picks", [ref_label(r) for r in refs], tags=(k1, k2),
                  help="Click a row, then pick another face, edge or point"),
        ]),
        Section("Mate type", True, [_mate_grid(key, fitting(k1, k2))]),
    ]
    place = []
    vk = _value_kind(key)
    if vk == "distance":
        place.append(Field("distance", "Distance", "float", round(joint.Distance.Value, 6), "mm"))
    elif vk == "angle":
        place.append(Field("angle", "Angle", "float", round(joint.Angle.Value, 6), "deg"))
    place.append(Field("flip", "Flip", "action", help="Turn one part over"))
    sections.append(Section("Placement", True, place))

    if key in _LIMITED:
        kind = "Length" if _LIMITED[key] == "length" else "Angle"
        unit = "mm" if kind == "Length" else "deg"
        on_min = bool(getattr(joint, f"Enable{kind}Min"))
        on_max = bool(getattr(joint, f"Enable{kind}Max"))
        sections.append(Section("Limits", on_min or on_max, [
            Field("limit_enabled_min", "Minimum", "bool", on_min),
            Field("limit_min", "Minimum value", "float", round(getattr(joint, f"{kind}Min").Value, 6), unit),
            Field("limit_enabled_max", "Maximum", "bool", on_max),
            Field("limit_max", "Maximum value", "float", round(getattr(joint, f"{kind}Max").Value, 6), unit),
        ]))

    asm = _assembly_of(joint)
    code = _STATUS.get((asm.Document.Name, asm.Name))
    if code is None:
        text = "Not solved yet"
    elif code == 0:
        text = "Solved"
    else:
        text = f"Did not solve (code {code})"
    sections.append(Section("Result", True, [Field("result", "Status", "readonly", text)]))
    return sections


def new_mate_settings(picks) -> list:
    picks = [p for p in (picks or []) if p is not None]
    prompts = ["Pick a face, edge or point", "then one on another part"]
    values = [ref_label(p) for p in picks] + prompts[len(picks):2]
    tags = tuple(_ref_kind(p) for p in picks)
    standard = [m[0] for m in MATES if m[2] == "standard"]
    enabled = fitting(*tags[:2]) if len(picks) == 2 else standard
    return [
        Section("Selections", True, [Field("picks", "Selections", "picks", values, tags=tags)]),
        Section("Mate type", True, [_mate_grid(enabled[0], enabled if len(picks) == 2 else standard)]),
    ]
