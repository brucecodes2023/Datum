# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly order for the timeline, and which history the bar shows. No GUI imports.

An assembly has no feature history to roll back. The bar lists its components
and joints in the order they were created. FreeCAD gives every object a
document-wide ``ID`` that only grows, so sorting by ``ID`` is creation order.
"""

from __future__ import annotations

ASSEMBLY_TYPE = "Assembly::AssemblyObject"
JOINT_GROUP_TYPE = "Assembly::JointGroup"
BODY_TYPE = "PartDesign::Body"

# Assembly children that are folders or helpers, not components.
_NOT_COMPONENTS = (
    "Assembly::JointGroup",
    "Assembly::ViewGroup",
    "Assembly::SimulationGroup",
    "Assembly::BomGroup",
    "Assembly::BomObject",
    "App::Origin",
)


def _type_id(obj):
    return getattr(obj, "TypeId", "")


def _derived(obj, type_id):
    try:
        return bool(obj.isDerivedFrom(type_id))
    except (AttributeError, RuntimeError, TypeError):
        return False


def is_assembly(obj) -> bool:
    return obj is not None and _derived(obj, ASSEMBLY_TYPE)


def is_body(obj) -> bool:
    return obj is not None and _type_id(obj) == BODY_TYPE


def proxy_type(obj):
    """``Proxy.Type`` of a Python feature (``Joint``, ``GroundedJoint``), else ``None``."""
    proxy = getattr(obj, "Proxy", None)
    return getattr(proxy, "Type", None) if proxy is not None else None


def is_joint(obj) -> bool:
    return proxy_type(obj) in ("Joint", "GroundedJoint")


def _object_id(obj):
    try:
        return int(obj.ID)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return 0


def _is_suppressed(obj) -> bool:
    try:
        return bool(getattr(obj, "Suppressed", False))
    except (AttributeError, RuntimeError, TypeError):
        return False


def assembly_items(assembly):
    """Components and joints of ``assembly``, oldest first.

    Components are the assembly's direct children that are not one of its
    folders (joints, exploded views, simulations, bill of materials). Joints
    are the children of its joint folder. Sub-assembly contents are not listed.
    Returns ``[(obj, role)]`` with role ``"component"`` or ``"joint"``.
    """
    found = {}
    for obj in list(getattr(assembly, "Group", []) or []):
        if _derived(obj, JOINT_GROUP_TYPE):
            for joint in list(getattr(obj, "Group", []) or []):
                found.setdefault(joint.Name, (joint, "joint"))
            continue
        if _type_id(obj) in _NOT_COMPONENTS or any(_derived(obj, t) for t in _NOT_COMPONENTS):
            continue
        # A joint left directly in the assembly is still a joint.
        role = "joint" if is_joint(obj) else "component"
        found.setdefault(obj.Name, (obj, role))
    return sorted(found.values(), key=lambda item: (_object_id(item[0]), item[0].Name))


class AssemblyEntry:
    """One chip. Same duck type the bar reads from ``TimelineEntry``."""

    def __init__(self, obj, role, index, current=None):
        self.obj = obj
        self.index = index
        self.role = role
        self.name = obj.Name
        self.label = obj.Label
        self.type_id = obj.TypeId
        self.object_id = _object_id(obj)
        self.kind = role
        self.is_solid = False
        # The chip of the object being edited (a joint in its task panel) is lit.
        self.is_tip = current is not None and obj.Name == current
        self.after_tip = False
        # A suppressed joint (or component) is drawn struck through.
        self.suppressed = _is_suppressed(obj)
        self.base_name = None
        self.icon = None
        kind = proxy_type(obj) or self.type_id
        state = "suppressed" if self.suppressed else ("being edited" if self.is_tip else "")
        lines = [self.label, f"{role.capitalize()} · {kind}", f"#{index + 1} in the order added"]
        if state:
            lines.append(state)
        self.tooltip = "\n".join(lines)

    def __repr__(self):
        return f"<AssemblyEntry {self.name} {self.role} id={self.object_id}>"


class AssemblyTimeline:
    def __init__(self, assembly, entries):
        self.assembly = assembly
        self.name = assembly.Name
        self.label = assembly.Label
        self.entries = entries

    def entry(self, name):
        for item in self.entries:
            if item.name == name:
                return item
        return None

    def note(self):
        count = len(self.entries)
        if count == 1:
            return "1 item in the order it was added"
        return f"{count} items in the order they were added"


def read_assembly(assembly, current=None) -> AssemblyTimeline:
    """Snapshot ``assembly`` in creation order. ``current`` is the name of the object in edit."""
    items = assembly_items(assembly)
    entries = [AssemblyEntry(obj, role, i, current) for i, (obj, role) in enumerate(items)]
    return AssemblyTimeline(assembly, entries)


def _has_features(body) -> bool:
    return bool(list(getattr(body, "Group", []) or []))


def _is_root_assembly(obj) -> bool:
    return is_assembly(obj) and not any(is_assembly(parent) for parent in obj.InList)


def pick_source(objects, active_body=None, active_assembly=None):
    """Which history the bar shows. Returns ``(kind, obj, how)``.

    ``kind`` is ``"body"``, ``"assembly"`` or ``None``. ``how`` is
    ``"active"``, ``"first"`` or ``"none"``. The active body wins, then the
    active assembly. With nothing active: the first top-level assembly, then the
    first body that has features. ``objects`` is the document's object list.
    """
    if is_body(active_body):
        return "body", active_body, "active"
    if is_assembly(active_assembly):
        return "assembly", active_assembly, "active"
    objects = list(objects or [])
    for obj in objects:
        if _is_root_assembly(obj):
            return "assembly", obj, "first"
    for obj in objects:
        if is_body(obj) and _has_features(obj):
            return "body", obj, "first"
    return None, None, "none"


def has_history(kind, obj) -> bool:
    """True when the bar has something to show: a body with features, or an
    assembly with at least one component or joint."""
    if obj is None:
        return False
    if kind == "body":
        return _has_features(obj)
    if kind == "assembly":
        return bool(assembly_items(obj))
    return False


def _group_parent(obj):
    for parent in getattr(obj, "InList", []) or []:
        group = getattr(parent, "Group", None)
        if group and obj in list(group):
            return parent
    return None


def selection_path(obj):
    """``(root, subname)`` for ``Gui.Selection.addSelection``.

    Walks up the groups that hold ``obj`` so the pick is made in its assembly,
    as a click in the tree would be. A top-level object gives ``(obj, "")``.
    """
    names = [obj.Name]
    current = obj
    seen = {obj.Name}
    while True:
        parent = _group_parent(current)
        if parent is None or parent.Name in seen:
            break
        seen.add(parent.Name)
        names.insert(0, parent.Name)
        current = parent
    if current is obj:
        return obj, ""
    return current, ".".join(names[1:]) + "."
