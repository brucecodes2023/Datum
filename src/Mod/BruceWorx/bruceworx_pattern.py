# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx Rectangular and Circular Pattern for bodies and components (BW-MOD-01).

A linked pattern is one ``App::Link`` array (``ElementCount`` = generated
instances) that links the seed's geometry. The pattern parameters are dynamic
properties on that link, in the "BruceWorx Pattern" group, so the pattern is
saved with the document and can be edited again. Because the array is a plain
``App::Link``, the Assembly workbench treats every element as a component
(``UtilsAssembly.isLinkGroup``).

The seed itself stays where it is; the array holds the other instances only.
Instance keys are index tuples: ``(i, j)`` for rectangular patterns and
``(k,)`` for circular ones. ``(0, 0)`` / ``(0,)`` is the seed.

* Symmetric directions use indices ``-floor((n-1)/2) .. ceil((n-1)/2)``.
* Suppressed instances keep their element (so indices stay stable) and are
  hidden with ``setElementVisible``, which also drops them from the shape.
* Instance placement is ``g_k * P0`` (world-frame transform times the seed
  placement), computed in the seed's parent coordinate system.
* An instance held by an assembly joint (or grounded) stays where the joint
  holds it; the pattern does not move it. When that is away from its place in
  the pattern, the pattern lists it in ``HeldInstances`` and says so in the
  Report view.

Independent copies are not parametric: bodies and solids become
``Part::Feature`` solids; components and App::Part containers become
separate ``App::Link`` objects.

PartDesign *features* are not handled here: the commands hand them to
PartDesign's own Linear/Polar Pattern.
"""

from __future__ import annotations

GROUP = "BruceWorx Pattern"
RECTANGULAR = "Rectangular"
CIRCULAR = "Circular"
DISTANCE_TYPES = ["Spacing", "Extent"]
CIRCULAR_TYPES = ["Full", "Angle", "Symmetric"]


# ---------------------------------------------------------------------------
# Pure math (no FreeCAD needed).


def index_set(count, symmetric=False):
    """Instance indices along one direction, the seed being 0."""
    count = max(1, int(count))
    if not symmetric:
        return list(range(count))
    low = -((count - 1) // 2)
    high = (count - 1) - (count - 1) // 2
    return list(range(low, high + 1))


def step(count, distance, distance_type):
    """Distance between neighbouring instances along one direction."""
    count = max(1, int(count))
    if distance_type == "Extent":
        return distance / (count - 1) if count > 1 else 0.0
    return float(distance)


def rectangular_offsets(params):
    """``{(i, j): (d1, d2)}`` distances along direction 1 and 2."""
    s1 = step(params["count1"], params["distance1"], params["type1"])
    s2 = step(params["count2"], params["distance2"], params["type2"])
    offsets = {}
    for i in index_set(params["count1"], params["symmetric1"]):
        for j in index_set(params["count2"], params["symmetric2"]):
            offsets[(i, j)] = (i * s1, j * s2)
    return offsets


def circular_angles(params):
    """``{(k,): angle_in_degrees}``.

    Full divides 360 by n (360 is the same as 0); Angle and Symmetric divide
    the total angle by n - 1 so both ends get an instance.
    """
    count = max(1, int(params["count1"]))
    kind = params["circular_type"]
    if kind == "Full":
        return {(k,): 360.0 * k / count for k in range(count)}
    total = float(params["angle"])
    each = total / (count - 1) if count > 1 else 0.0
    symmetric = kind == "Symmetric"
    return {(k,): each * k for k in index_set(count, symmetric)}


def seed_key(kind):
    return (0, 0) if kind == RECTANGULAR else (0,)


def instance_name(key):
    """How messages name an instance: ``#2`` (circular) or ``(1, 0)``."""
    if len(key) == 1:
        return f"#{key[0]}"
    return "(" + ", ".join(str(v) for v in key) + ")"


def held_entry(key, element, joints):
    """One ``HeldInstances`` line: ``#2 Pattern_i1: Joint, Joint001``."""
    return f"{instance_name(key)} {element}: {', '.join(joints)}"


def parse_held_entry(text):
    """``(element name, [joint names])`` from a ``held_entry`` line."""
    head, _sep, tail = text.partition(": ")
    words = head.split()
    return (words[-1] if words else ""), [j for j in tail.split(", ") if j]


def held_changes(old, new):
    """Lines that appeared and lines that went, between two ``HeldInstances``."""
    return [e for e in new if e not in old], [e for e in old if e not in new]


def key_to_text(key):
    return ",".join(str(v) for v in key)


def text_to_key(text):
    return tuple(int(v) for v in text.split(",") if v.strip() != "")


def classify_selection(objects):
    """'features' when every object is a PartDesign feature, 'objects' when none
    is, 'mixed' otherwise, 'empty' for no selection."""
    if not objects:
        return "empty"
    features = [_is_partdesign_feature(o) for o in objects]
    if all(features):
        return "features"
    if not any(features):
        return "objects"
    return "mixed"


def feature_body(features):
    """The one body holding all ``features``, or None (none, or several)."""
    bodies = set()
    for feature in features:
        body = parent_of(feature)
        if body is None or not body.isDerivedFrom("PartDesign::Body"):
            return None
        bodies.add(body.Name)
    if len(bodies) != 1:
        return None
    return parent_of(features[0])


def _is_partdesign_feature(obj):
    try:
        return obj.isDerivedFrom("PartDesign::Feature") and not obj.isDerivedFrom("PartDesign::Body")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# FreeCAD side.


def _app():
    import FreeCAD

    return FreeCAD


def default_params(kind):
    App = _app()
    return {
        "kind": kind,
        "direction1": App.Vector(1, 0, 0),
        "direction2": App.Vector(0, 1, 0),
        "count1": 3 if kind == RECTANGULAR else 6,
        "count2": 1,
        "distance1": 20.0,
        "distance2": 20.0,
        "type1": "Spacing",
        "type2": "Spacing",
        "symmetric1": False,
        "symmetric2": False,
        "axis_base": App.Vector(0, 0, 0),
        "axis_direction": App.Vector(0, 0, 1),
        "circular_type": "Full",
        "angle": 360.0,
        "suppressed": [],
    }


def transforms(params):
    """``[(key, App.Placement g)]`` for every instance, seed included."""
    App = _app()
    out = []
    if params["kind"] == RECTANGULAR:
        u1 = App.Vector(params["direction1"])
        u2 = App.Vector(params["direction2"])
        if u1.Length < 1e-12:
            raise ValueError("Direction 1 has zero length")
        u1.normalize()
        if params["count2"] > 1:
            if u2.Length < 1e-12:
                raise ValueError("Direction 2 has zero length")
            u2.normalize()
            if abs(abs(u1.dot(u2)) - 1.0) < 1e-9:
                raise ValueError("Direction 1 and direction 2 are parallel")
        for key, (d1, d2) in sorted(rectangular_offsets(params).items()):
            out.append((key, App.Placement(u1 * d1 + u2 * d2, App.Rotation())))
        return out
    axis = App.Vector(params["axis_direction"])
    if axis.Length < 1e-12:
        raise ValueError("Axis has zero length")
    axis.normalize()
    centre = App.Vector(params["axis_base"])
    for key, angle in sorted(circular_angles(params).items()):
        # Placement(base, rotation, centre) rotates about ``centre``.
        out.append((key, App.Placement(App.Vector(), App.Rotation(axis, angle), centre)))
    return out


def instance_placements(params, seed_placement):
    """``[(key, placement)]`` for the generated instances (seed excluded)."""
    skip = seed_key(params["kind"])
    return [(key, g.multiply(seed_placement)) for key, g in transforms(params) if key != skip]


def linked_target(seed):
    """What the array links to: the part behind a component link, else the seed."""
    if seed.isDerivedFrom("App::Link") and seed.ElementCount == 0:
        target = seed.LinkedObject
        if target is not None:
            return target
    return seed


def parent_of(seed):
    try:
        return seed.getParentGeoFeatureGroup()
    except Exception:
        return None


# Dynamic properties on the array: (name, type, default-key, tooltip).
_PROPS = [
    ("PatternType", "App::PropertyEnumeration", None, "Rectangular or circular."),
    ("PatternSeed", "App::PropertyLinkHidden", None, "The body or component being patterned."),
    ("Direction1", "App::PropertyVector", "direction1", "Direction 1 (parent coordinates)."),
    ("Count1", "App::PropertyInteger", "count1", "Instances along direction 1, or around the axis, seed included."),
    ("Distance1", "App::PropertyDistance", "distance1", "Spacing or extent along direction 1."),
    ("DistanceType1", "App::PropertyEnumeration", None, "Spacing: gap between instances. Extent: total length."),
    ("Symmetric1", "App::PropertyBool", "symmetric1", "Spread direction 1 on both sides of the seed."),
    ("Direction2", "App::PropertyVector", "direction2", "Direction 2 (parent coordinates)."),
    ("Count2", "App::PropertyInteger", "count2", "Instances along direction 2; 1 turns direction 2 off."),
    ("Distance2", "App::PropertyDistance", "distance2", "Spacing or extent along direction 2."),
    ("DistanceType2", "App::PropertyEnumeration", None, "Spacing: gap between instances. Extent: total length."),
    ("Symmetric2", "App::PropertyBool", "symmetric2", "Spread direction 2 on both sides of the seed."),
    ("AxisBase", "App::PropertyVector", "axis_base", "A point on the circular pattern axis."),
    ("AxisDirection", "App::PropertyVector", "axis_direction", "Circular pattern axis direction."),
    ("CircularType", "App::PropertyEnumeration", None, "Full circle, total angle, or symmetric about the seed."),
    ("Angle", "App::PropertyAngle", "angle", "Total angle for Angle and Symmetric."),
    ("Suppressed", "App::PropertyStringList", None, "Suppressed instance keys, e.g. '1,0'."),
    ("InstanceKeys", "App::PropertyStringList", None, "Key of each array element, in element order."),
]

HELD = "HeldInstances"
HELD_TIP = ("Instances a joint holds away from their place in the pattern, with the joints holding them. "
            "The pattern leaves them where the joints hold them.")


def is_pattern(obj):
    return obj is not None and hasattr(obj, "PropertiesList") and "PatternType" in obj.PropertiesList \
        and obj.getGroupOfProperty("PatternType") == GROUP


def _ensure_props(array):
    present = set(array.PropertiesList)
    for name, typ, _default, tip in _PROPS:
        if name not in present:
            array.addProperty(typ, name, GROUP, tip)
    array.PatternType = [RECTANGULAR, CIRCULAR]
    array.DistanceType1 = DISTANCE_TYPES
    array.DistanceType2 = DISTANCE_TYPES
    array.CircularType = CIRCULAR_TYPES
    array.setEditorMode("InstanceKeys", 1)


def write_params(array, params):
    _ensure_props(array)
    array.PatternType = params["kind"]
    for name, _typ, key, _tip in _PROPS:
        if key is not None:
            setattr(array, name, params[key])
    array.DistanceType1 = params["type1"]
    array.DistanceType2 = params["type2"]
    array.CircularType = params["circular_type"]
    array.Suppressed = [key_to_text(k) for k in params["suppressed"]]


def read_params(array):
    params = default_params(array.PatternType)
    for name, _typ, key, _tip in _PROPS:
        if key is None:
            continue
        value = getattr(array, name)
        if hasattr(value, "Value"):  # Quantity
            value = value.Value
        params[key] = value
    params["type1"] = array.DistanceType1
    params["type2"] = array.DistanceType2
    params["circular_type"] = array.CircularType
    params["suppressed"] = [text_to_key(t) for t in array.Suppressed]
    return params


def apply(array, check=True):
    """Recompute element count, placements and visibility from the parameters.

    Instances held by a joint keep their placement. ``check``: update
    ``HeldInstances`` and report changes (see ``update_held``)."""
    params = read_params(array)
    seed = array.PatternSeed
    if seed is None:
        raise ValueError(f"{array.Label}: the pattern has no seed")
    placed = instance_placements(params, seed.Placement)
    if array.ElementCount != len(placed):
        array.ElementCount = len(placed)
    suppressed = {tuple(k) for k in params["suppressed"]}
    elements = array.ElementList
    for index, (key, placement) in enumerate(placed):
        element = elements[index]
        if not element.Placement.isSame(placement, 1e-9) and not holding_joints(element):
            element.Placement = placement
        visible = key not in suppressed
        if bool(array.isElementVisible(str(index))) != visible:
            array.setElementVisible(str(index), visible)
    keys = [key_to_text(k) for k, _p in placed]
    if list(array.InstanceKeys) != keys:
        array.InstanceKeys = keys
    if check:
        update_held(array)
    return placed


# ---------------------------------------------------------------------------
# Instances held by assembly joints.

# "In its place": within 1e-4 mm (the solver lands within about 1e-5) and about
# 0.01 degrees (Rotation.isSame takes 1 - cos(angle / 2), doubled).
HELD_TOLERANCE = 1e-4
HELD_ROTATION_TOLERANCE = 1e-8


def _holds(joint, element):
    """True when ``joint`` keeps ``element`` where it is: a grounded joint on
    it, or an active joint on it that the solver applies (the element is
    connected to the grounded parts)."""
    try:
        if "Invalid" in joint.State or getattr(joint, "Suppressed", False):
            return False
        if "ObjectToGround" in joint.PropertiesList:
            return joint.ObjectToGround == element
        if "JointType" not in joint.PropertiesList:
            return False
        parts = []
        for name in ("Reference1", "Reference2"):
            ref = getattr(joint, name, None)
            parts.append(ref[0] if ref and len(ref) == 2 else None)
        if element not in parts or parts[0] == parts[1] or None in parts:
            return False
    except Exception:
        return False
    try:
        assembly = joint.Proxy.getAssembly(joint)
        return assembly is None or bool(assembly.isPartConnected(element))
    except Exception:
        return True


def holding_joints(element):
    """The joints that hold ``element`` in place, by name."""
    try:
        users = element.InList
    except Exception:
        return []
    return sorted((o for o in users if _holds(o, element)), key=lambda o: o.Name)


def in_place(placement, slot):
    """True when ``placement`` is ``slot`` within the tolerances above."""
    return (placement.Base - slot.Base).Length <= HELD_TOLERANCE \
        and placement.Rotation.isSame(slot.Rotation, HELD_ROTATION_TOLERANCE)


def find_held(array):
    """``[(key, element, [joints])]``: instances that joints hold away from
    their place in the pattern."""
    try:
        params = read_params(array)
        seed = array.PatternSeed
        placed = instance_placements(params, seed.Placement) if seed is not None else []
    except Exception:
        return []
    found = []
    for (key, placement), element in zip(placed, array.ElementList):
        joints = holding_joints(element)
        if joints and not in_place(element.Placement, placement):
            found.append((key, element, joints))
    return found


def held_message(array, key, element, joints):
    """Report view text for one held instance."""
    word = "joint" if len(joints) == 1 else "joints"
    names = ", ".join(j.Label for j in joints)
    return (f"{array.Label}: instance {instance_name(key)} ({element.Name}) is held by {word} {names} "
            f"and cannot follow the pattern")


def update_held(array, report=True):
    """Store the held instances in ``HeldInstances`` and, with ``report``, say
    in the Report view which appeared and which follow the pattern again.
    Returns ``find_held(array)``."""
    held = find_held(array)
    entries = [held_entry(k, e.Name, [j.Name for j in js]) for k, e, js in held]
    old = list(getattr(array, HELD, None) or [])
    if old == entries:
        return held
    if HELD not in array.PropertiesList:
        array.addProperty("App::PropertyStringList", HELD, GROUP, HELD_TIP)
        array.setEditorMode(HELD, 1)
        array.setPropertyStatus(HELD, "Output")
    array.HeldInstances = entries
    added, gone = held_changes(old, entries)
    if report:
        App = _app()
        for key, element, joints in held:
            if held_entry(key, element.Name, [j.Name for j in joints]) in added:
                App.Console.PrintWarning(held_message(array, key, element, joints) + "\n")
        names = {e.Name for e in array.ElementList}
        for entry in gone:
            element = parse_held_entry(entry)[0]
            if element in names and element not in {e.Name for _k, e, _j in held}:
                # Not when the instance went: the joint reports that itself.
                instance = entry.partition(": ")[0].rpartition(" ")[0]
                App.Console.PrintMessage(f"{array.Label}: instance {instance} ({element}) follows the pattern again\n")
    _refresh_joint_icons(array.Document, old + entries)
    return held


def _refresh_joint_icons(doc, entries):
    """Redraw the tree icons of the joints named in ``entries`` (warning overlay)."""
    for entry in entries:
        for name in parse_held_entry(entry)[1]:
            joint = doc.getObject(name)
            try:
                joint.ViewObject.signalChangeIcon()
            except Exception:  # no GUI, or the joint is gone
                pass


def held_by(joint):
    """``[(pattern, element name)]`` for the instances ``joint`` holds away
    from their place in a pattern (read from ``HeldInstances``)."""
    found = []
    for name in ("Reference1", "Reference2", "ObjectToGround"):
        ref = getattr(joint, name, None)
        obj = ref[0] if isinstance(ref, (list, tuple)) else ref
        if obj is None:
            continue
        try:
            users = obj.InList
        except Exception:
            continue
        for array in users:
            if not is_pattern(array):
                continue
            for entry in getattr(array, HELD, None) or []:
                element, joints = parse_held_entry(entry)
                if element == obj.Name and joint.Name in joints:
                    found.append((array, element))
    return found


def create_linked(seed, params, doc=None):
    """Create a linked pattern of ``seed``. Returns the ``App::Link`` array."""
    doc = doc or seed.Document
    array = doc.addObject("App::Link", "Pattern")
    array.Label = f"{seed.Label} pattern"
    array.LinkedObject = linked_target(seed)
    write_params(array, params)
    array.PatternSeed = seed
    parent = parent_of(seed)
    if parent is not None:
        parent.addObject(array)
    apply(array)
    return array


def create_independent(seed, params, doc=None):
    """Create independent copies. Returns the new objects (not parametric)."""
    doc = doc or seed.Document
    parent = parent_of(seed)
    suppressed = {tuple(k) for k in params["suppressed"]}
    created = []
    # Solids are copied; components and containers (App::Part) become links.
    as_solid = seed.isDerivedFrom("Part::Feature")
    for key, placement in instance_placements(params, seed.Placement):
        if key in suppressed:
            continue
        name = f"{seed.Label} {key_to_text(key)}"
        if not as_solid:
            obj = doc.addObject("App::Link", "PatternInstance")
            obj.LinkedObject = linked_target(seed)
            obj.Placement = placement
        else:
            obj = doc.addObject("Part::Feature", "PatternInstance")
            shape = seed.Shape.copy()
            shape.Placement = placement
            obj.Shape = shape
            _copy_view_style(seed, obj)
        obj.Label = name
        if parent is not None:
            parent.addObject(obj)
        created.append(obj)
    return created


def make_independent(array):
    """Replace a linked pattern by independent copies; returns the copies."""
    params = read_params(array)
    seed = array.PatternSeed
    doc = array.Document
    copies = create_independent(seed, params, doc)
    # Removing the array alone leaves its element objects at the root.
    elements = [e.Name for e in array.ElementList]
    array.ElementCount = 0
    doc.removeObject(array.Name)
    for name in elements:
        if doc.getObject(name) is not None:
            doc.removeObject(name)
    return copies


def _copy_view_style(src, dst):
    try:
        if src.ViewObject is not None and dst.ViewObject is not None:
            dst.ViewObject.ShapeAppearance = src.ViewObject.ShapeAppearance
    except Exception:
        pass


def patterns_of(seed):
    """Linked patterns whose seed is ``seed``."""
    found = []
    for obj in seed.InList:
        if is_pattern(obj) and obj.PatternSeed == seed:
            found.append(obj)
    return found


def describe_key(params, key):
    """Short label for the suppress list."""
    if params["kind"] == RECTANGULAR:
        return f"({key[0]}, {key[1]})"
    angle = circular_angles(params).get(tuple(key), 0.0)
    return f"#{key[0]}  {angle:.1f}°"
