# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read and write PartDesign history data. No GUI imports.

The body's displayed result is ``Body.Tip`` (see ``PartDesign::Body::execute``).
``Body.Group`` is the tree order. Each solid's ``BaseFeature`` is the object it
actually fuses or cuts. Those two orders match after ``Body.addObject`` /
``insertObject``, and they diverge if only ``Group`` is rewritten.
"""

from __future__ import annotations


def is_solid_feature(obj) -> bool:
    """Python stand-in for ``PartDesign::Body::isSolidFeature``.

    Datums (``App::DatumElement`` / ``Part::Datum``) are not solids. Sketches,
    VarSets and binders are not ``PartDesign::Feature``.

    Not reproduced here: ``Transformed::isMultiTransformChild()`` is C++ only,
    so a pattern nested inside a MultiTransform still looks like a solid.
    """
    if obj is None:
        return False
    try:
        if not obj.isDerivedFrom("PartDesign::Feature"):
            return False
        if obj.isDerivedFrom("App::DatumElement") or obj.isDerivedFrom("Part::Datum"):
            return False
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def is_suppressed(obj) -> bool:
    """``SuppressibleExtension.Suppressed``. Missing property means not suppressed."""
    prop = getattr(obj, "Suppressed", None)
    if prop is None:
        return False
    try:
        return bool(prop)
    except (AttributeError, RuntimeError, TypeError):
        return False


def _link_name(link):
    if not link:
        return None
    try:
        return link.Name
    except (AttributeError, RuntimeError):
        return None


def icon_kind(type_id: str) -> str:
    """Coarse glyph key for the prototype bar. Real sessions prefer ViewObject.Icon."""
    table = {
        "Sketcher::SketchObject": "sketch",
        "PartDesign::Plane": "datum",
        "PartDesign::Line": "datum",
        "PartDesign::Point": "datum",
        "PartDesign::CoordinateSystem": "datum",
        "PartDesign::Pad": "pad",
        "PartDesign::Pocket": "pocket",
        "PartDesign::Revolution": "pad",
        "PartDesign::Groove": "pocket",
        "PartDesign::Fillet": "fillet",
        "PartDesign::Chamfer": "chamfer",
        "PartDesign::Thickness": "feature",
        "PartDesign::AdditiveBox": "box",
        "PartDesign::SubtractiveBox": "box",
        "PartDesign::AdditiveCylinder": "cylinder",
        "PartDesign::SubtractiveCylinder": "cylinder",
        "PartDesign::AdditiveSphere": "sphere",
        "PartDesign::SubtractiveSphere": "sphere",
        "PartDesign::Hole": "pocket",
        "PartDesign::Mirrored": "feature",
        "PartDesign::LinearPattern": "feature",
        "PartDesign::PolarPattern": "feature",
        "PartDesign::MultiTransform": "feature",
        "PartDesign::Boolean": "feature",
        "PartDesign::ShapeBinder": "feature",
        "PartDesign::SubShapeBinder": "feature",
        "App::VarSet": "vars",
    }
    if type_id in table:
        return table[type_id]
    if type_id.startswith("PartDesign::Additive"):
        return "box"
    if type_id.startswith("PartDesign::Subtractive"):
        return "pocket"
    if "Sketch" in type_id:
        return "sketch"
    return "feature"


class TimelineEntry:
    """One ``Body.Group`` child, in Group order."""

    def __init__(self, obj, index, tip, tip_index):
        self.obj = obj
        self.index = index
        self.name = obj.Name
        self.label = obj.Label
        self.type_id = obj.TypeId
        self.kind = icon_kind(self.type_id)
        self.is_solid = is_solid_feature(obj)
        self.suppressed = is_suppressed(obj)
        self.base_name = _link_name(getattr(obj, "BaseFeature", None))
        self.is_tip = tip is not None and obj == tip
        if tip is None or tip_index is None:
            # Tip is unset, or it points outside Group (usually Body.BaseFeature).
            # Either way the marker is in front of this row.
            self.after_tip = True
        else:
            self.after_tip = index > tip_index
        self.icon = None  # optional QIcon, filled in by the dock

    def __repr__(self):
        flags = []
        if self.is_solid:
            flags.append("solid")
        if self.is_tip:
            flags.append("tip")
        if self.after_tip:
            flags.append("after-tip")
        if self.suppressed:
            flags.append("suppressed")
        extra = (" " + ",".join(flags)) if flags else ""
        return f"<TimelineEntry {self.name} {self.type_id}{extra}>"


class Timeline:
    def __init__(self, body, entries, tip, tip_index):
        self.body = body
        self.body_name = body.Name
        self.body_label = body.Label
        self.entries = entries
        self.tip = tip
        self.tip_name = tip.Name if tip is not None else None
        self.tip_index = tip_index
        self.tip_in_group = tip_index is not None
        self.base_feature_name = _link_name(body.BaseFeature)

    def entry(self, name):
        for item in self.entries:
            if item.name == name:
                return item
        return None

    def format_report(self):
        lines = [
            f"body {self.body_label} ({self.body_name})",
            f"tip {self.tip_name!r} index={self.tip_index} in_group={self.tip_in_group}",
            f"base_feature {self.base_feature_name!r}",
        ]
        for item in self.entries:
            lines.append(
                f"  [{item.index}] {item.label} ({item.name}) {item.type_id}"
                f" solid={item.is_solid} suppressed={item.suppressed}"
                f" tip={item.is_tip} after_tip={item.after_tip}"
                f" base={item.base_name}"
            )
        return "\n".join(lines)


def read_timeline(body) -> Timeline:
    """Snapshot ``body.Group`` and where ``body.Tip`` sits in that list."""
    group = list(body.Group)
    tip = body.Tip
    tip_index = None
    if tip is not None:
        for index, obj in enumerate(group):
            if obj == tip:
                tip_index = index
                break
    entries = [TimelineEntry(obj, i, tip, tip_index) for i, obj in enumerate(group)]
    return Timeline(body, entries, tip, tip_index)


def can_be_tip(body, feature):
    """Same rule as ``PartDesign_MoveTip`` (``CmdPartDesignMoveTip``).

    Returns ``(ok, reason)``. ``feature is None`` clears the tip, which the
    command does when the body itself is selected.
    """
    if feature is None:
        return True, ""
    in_group = feature in list(body.Group)
    is_base = body.BaseFeature is not None and feature == body.BaseFeature
    if is_solid_feature(feature) and (in_group or is_base):
        return True, ""
    if is_base:
        return True, ""
    type_id = getattr(feature, "TypeId", type(feature).__name__)
    name = getattr(feature, "Name", repr(feature))
    return (
        False,
        "Only a solid feature, the body's base feature, or None can be the tip "
        f"({name} is {type_id}).",
    )


def set_tip(body, feature):
    """Assign ``Body.Tip``. Does not suppress later features and does not recompute.

    Later features stay in ``Group`` and still recompute when the document does.
    The body's ``Shape`` becomes the tip shape (or null when the tip is cleared).
    """
    ok, reason = can_be_tip(body, feature)
    if not ok:
        raise ValueError(reason)
    body.Tip = feature


PLAYBACK_ACTIONS = ("start", "back", "forward", "end")


def solids_in_group(body):
    """The body's solid features in Group order."""
    return [obj for obj in body.Group if is_solid_feature(obj)]


def playback_target(body, action):
    """Where a playback button moves the tip. Returns ``(changed, feature_or_None)``.

    ``start`` clears the tip (everything rolled back, as Fusion's first button),
    ``back`` steps to the previous solid (or clears it before the first), ``forward``
    to the next solid and ``end`` to the last solid. Suppressed solids count as
    steps: the marker can rest after them.
    """
    if action not in PLAYBACK_ACTIONS:
        raise ValueError(f"unknown playback action {action!r}")
    solids = solids_in_group(body)
    if not solids:
        return False, None
    tip = body.Tip
    index = None
    for i, obj in enumerate(solids):
        if obj == tip:
            index = i
            break
    if action == "start":
        target = None
    elif action == "end":
        target = solids[-1]
    elif action == "back":
        if index is None:
            # Tip unset (or outside Group, the base feature): nothing earlier.
            target = None
        else:
            target = solids[index - 1] if index > 0 else None
    else:  # forward
        if index is None:
            target = solids[0]
        else:
            target = solids[index + 1] if index + 1 < len(solids) else solids[-1]
    return target != tip, target


def set_suppressed(feature, value: bool):
    """Toggle ``Feature.Suppressed``.

    A suppressed solid still recomputes, then its ``Shape`` is replaced with the
    base shape (passthrough). This is independent of ``Body.Tip``.
    """
    if not hasattr(feature, "Suppressed"):
        raise AttributeError(
            f"{getattr(feature, 'Name', feature)} ({getattr(feature, 'TypeId', '?')}) "
            "has no Suppressed property"
        )
    feature.Suppressed = bool(value)


def previous_solid_in_group(body, feature):
    """Previous solid in ``Group`` order. This is what ``getPrevSolidFeature`` walks.

    It is not ``feature.BaseFeature`` once those have diverged.
    """
    group = list(body.Group)
    try:
        index = group.index(feature)
    except ValueError:
        return None
    for obj in reversed(group[:index]):
        if is_solid_feature(obj):
            return obj
    return None


def assign_group_order(body, ordered_objects):
    """Rewrite ``Body.Group`` only.

    This is what the tree does on an in-body drop (``TreeWidget::sortDroppedObjects``).
    ``BaseFeature`` links are left untouched, so the compute chain can disagree
    with the displayed order. Not safe for a Fusion-style reorder.
    """
    body.Group = list(ordered_objects)


def rewire_solid_chain(body, ordered_objects):
    """Point each solid's ``BaseFeature`` at the previous solid, then assign ``Group``.

    ``Tip`` is not moved. Sketches are only moved if the caller placed them in
    ``ordered_objects``. Dress-up features (fillet, chamfer, …) copy the old
    sub-element names onto the new base when ``BaseFeature`` changes, so this
    is still unsafe for topology-based features. Patterns and profile links
    are not updated.
    """
    previous = None
    for obj in ordered_objects:
        if is_solid_feature(obj) and hasattr(obj, "BaseFeature"):
            obj.BaseFeature = previous
            previous = obj
    body.Group = list(ordered_objects)


# ----- reorder -------------------------------------------------------------------------------
#
# PartDesign_MoveFeatureInTree is Body.removeObject + Body.insertObject(after=True)
# per feature, then a dependency check that aborts the command when an earlier
# feature depends on a later one. The timeline does the same through the App
# API, moves a feature's own sketch with it (Fusion folds the sketch into the
# feature), checks the dependencies before the move so the menu action can be
# disabled, and keeps the tip on the same object.

SUBTRACTIVE_HINTS = ("Pocket", "Groove", "Hole", "Subtractive", "Thickness")
# Links that are the solid chain itself, not a modelling dependency.
_CHAIN_PROPS = ("BaseFeature", "_Body")


def _link_objects(value):
    """Document objects inside a link property value (obj, (obj, subs), lists of either)."""
    found = []
    if value is None:
        return found
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and not isinstance(value[0], (list, tuple)) and isinstance(
            value[1], (list, tuple, str)
        ) and hasattr(value[0], "Name"):
            found.append(value[0])
            return found
        for item in value:
            found.extend(_link_objects(item))
        return found
    if hasattr(value, "Name") and hasattr(value, "TypeId"):
        found.append(value)
    return found


def is_dress_up(obj) -> bool:
    try:
        return bool(obj.isDerivedFrom("PartDesign::DressUp"))
    except (AttributeError, RuntimeError, TypeError):
        return False


def is_subtractive(obj) -> bool:
    """Needs material before it: cannot become the body's first solid."""
    if is_dress_up(obj):
        return True
    type_id = getattr(obj, "TypeId", "")
    if any(hint in type_id for hint in SUBTRACTIVE_HINTS):
        return True
    # Transformed features (patterns, mirrored) copy earlier material.
    try:
        return bool(obj.isDerivedFrom("PartDesign::Transformed"))
    except (AttributeError, RuntimeError, TypeError):
        return False


def hard_links(obj, members):
    """Body members (a set of names) that ``obj`` depends on through any link
    property except the solid chain. A dress-up's ``Base`` is the chain while it
    points at ``BaseFeature``; its sub-elements are checked after the move."""
    names = set()
    chain_base = getattr(obj, "BaseFeature", None) if is_dress_up(obj) else None
    for prop in obj.PropertiesList:
        if prop in _CHAIN_PROPS:
            continue
        try:
            type_id = obj.getTypeIdOfProperty(prop)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if "Link" not in type_id:
            continue
        try:
            value = getattr(obj, prop)
        except (AttributeError, RuntimeError, TypeError):
            continue
        for linked in _link_objects(value):
            if prop == "Base" and chain_base is not None and linked == chain_base:
                continue
            if linked.Name in members and linked.Name != obj.Name:
                names.add(linked.Name)
    return names


def feature_group(body, feature):
    """``feature`` plus the sketches only it uses, in Group order (Fusion's fold).

    A sketch is folded when it is a Group member, a ``Sketcher::SketchObject``, linked
    from the feature (Profile, Sections, Spine, ...) and not linked from any other
    solid of the body.
    """
    group = list(body.Group)
    member_names = {obj.Name for obj in group}
    solids = solids_in_group(body)
    mine = set()
    for prop in feature.PropertiesList:
        if prop in _CHAIN_PROPS:
            continue
        try:
            if "Link" not in feature.getTypeIdOfProperty(prop):
                continue
            value = getattr(feature, prop)
        except (AttributeError, RuntimeError, TypeError):
            continue
        for linked in _link_objects(value):
            if linked.Name not in member_names or linked == feature:
                continue
            if getattr(linked, "TypeId", "") != "Sketcher::SketchObject":
                continue
            others = [
                s for s in solids
                if s != feature and linked.Name in hard_links(s, {linked.Name})
            ]
            if not others:
                mine.add(linked.Name)
    return [obj for obj in group if obj == feature or obj.Name in mine]


def group_of(body, feature):
    """Names in ``feature_group``. For a folded sketch, the group of its feature."""
    for solid in solids_in_group(body):
        members = feature_group(body, solid)
        if feature in members:
            return members
    return [feature]


def _neighbour_solid(body, feature, direction):
    solids = solids_in_group(body)
    index = solids.index(feature)
    if direction == "earlier":
        return solids[index - 1] if index > 0 else None
    return solids[index + 1] if index + 1 < len(solids) else None


def can_move(body, feature, direction):
    """Whether ``feature`` can swap places with its neighbour solid.

    Returns ``(ok, reason)``. Legal when no member of the moved group depends on a
    member of the group it crosses (``hard_links``), and the result does not start
    the body with a subtractive feature. A dress-up's edges are only known after
    the recompute: ``move_feature`` callers verify and undo on failure.
    """
    if direction not in ("earlier", "later"):
        return False, f"unknown direction {direction!r}"
    if not is_solid_feature(feature) or feature not in list(body.Group):
        return False, "Only a solid feature of the body can be moved; a sketch moves with its feature."
    neighbour = _neighbour_solid(body, feature, direction)
    if neighbour is None:
        return False, ("Already the first feature." if direction == "earlier" else "Already the last feature.")
    moving = feature_group(body, feature)
    crossed = feature_group(body, neighbour)
    moving_names = {obj.Name for obj in moving}
    crossed_names = {obj.Name for obj in crossed}
    # The group that ends up later must not be needed by the group that ends up earlier.
    early, late = (moving, crossed) if direction == "earlier" else (crossed, moving)
    late_names = crossed_names if direction == "earlier" else moving_names
    for obj in early:
        used = hard_links(obj, late_names)
        if used:
            user = obj.Label
            needed = ", ".join(sorted(body.Document.getObject(n).Label for n in used))
            return False, f"{user} uses {needed}, which would come after it."
    solids = solids_in_group(body)
    first_after = feature if (direction == "earlier" and solids.index(neighbour) == 0) else (
        neighbour if (direction == "later" and solids.index(feature) == 0) else None
    )
    if first_after is not None and is_subtractive(first_after):
        return False, f"{first_after.Label} needs material before it; it cannot be the first feature."
    return True, ""


# Dress-ups (fillet, chamfer, draft, thickness) name their edges and faces on the
# previous solid, with FreeCAD 1.1's mapped element names behind them
# (";#18:1;:H3b1,E.Edge1"). After a move the body points Base at the new previous
# solid but the mapped names still describe the old one, so the dress-up reports
# "Invalid edge link". The timeline rebinds them by geometry instead: before the
# move it notes each referenced element's kind, type, size and centre; after the
# first recompute it finds the same element on the new base and writes plain
# names, which FreeCAD maps afresh on the second recompute.

_GEOM_TOL = 1e-6


def _plain_sub(name):
    """``";#18:1;:H3b1,E.Edge1"`` -> ``"Edge1"``; a plain name is returned as is."""
    return name.rsplit(".", 1)[-1] if ";" in name else name


def _element_signature(shape, subname):
    """Geometry that identifies ``subname`` of ``shape`` independently of its index."""
    plain = _plain_sub(subname)
    kind = plain.rstrip("0123456789")
    try:
        sub = shape.getElement(plain)
    except Exception:
        return None
    try:
        if kind == "Edge":
            centre = sub.CenterOfMass
            return ("Edge", type(sub.Curve).__name__, sub.Length, (centre.x, centre.y, centre.z))
        if kind == "Face":
            centre = sub.CenterOfMass
            return ("Face", sub.Surface.TypeId, sub.Area, (centre.x, centre.y, centre.z))
        if kind == "Vertex":
            point = sub.Point
            return ("Vertex", "", 0.0, (point.x, point.y, point.z))
    except Exception:
        return None
    return None


def _same(a, b):
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= _GEOM_TOL * scale


def _find_element(shape, signature):
    """``"EdgeN"`` / ``"FaceN"`` / ``"VertexN"`` of ``shape`` matching ``signature``, or None."""
    if signature is None:
        return None
    kind, type_name, size, centre = signature
    items = {"Edge": shape.Edges, "Face": shape.Faces, "Vertex": shape.Vertexes}.get(kind, [])
    for index, item in enumerate(items):
        try:
            if kind == "Edge":
                if type(item.Curve).__name__ != type_name or not _same(item.Length, size):
                    continue
                point = item.CenterOfMass
            elif kind == "Face":
                if item.Surface.TypeId != type_name or not _same(item.Area, size):
                    continue
                point = item.CenterOfMass
            else:
                point = item.Point
        except Exception:
            continue
        if all(_same(p, q) for p, q in zip((point.x, point.y, point.z), centre)):
            return f"{kind}{index + 1}"
    return None


def snapshot_dressups(body):
    """``{dressup name: (base name, [(subname, signature), ...])}`` for every dress-up
    of the body whose base has a shape. Taken before a move."""
    found = {}
    for obj in body.Group:
        if not is_dress_up(obj):
            continue
        try:
            base, subs = obj.Base
        except Exception:
            continue
        if base is None:
            continue
        try:
            shape = base.Shape
            if shape.isNull():
                continue
        except Exception:
            continue
        found[obj.Name] = (base.Name, [(sub, _element_signature(shape, sub)) for sub in subs])
    return found


def rebind_dressups(body, snapshot):
    """After a move and a recompute: point each dress-up whose base changed at the
    same geometry on its new base. Returns ``(rebound names, [(name, missing subs)])``."""
    rebound = []
    failed = []
    for obj in body.Group:
        if not is_dress_up(obj) or obj.Name not in snapshot:
            continue
        old_base_name, subs = snapshot[obj.Name]
        base = obj.BaseFeature
        if base is None or base.Name == old_base_name:
            continue
        try:
            shape = base.Shape
        except Exception:
            failed.append((obj.Name, [sub for sub, _sig in subs]))
            continue
        new_subs = []
        missing = []
        for sub, signature in subs:
            name = _find_element(shape, signature)
            if name is None:
                missing.append(_plain_sub(sub))
            else:
                new_subs.append(name)
        if missing:
            failed.append((obj.Name, missing))
            continue
        obj.Base = (base, new_subs)
        rebound.append(obj.Name)
    return rebound, failed


def move_feature(body, feature, direction, recompute=True):
    """Swap ``feature`` (with its folded sketches) with the neighbour solid.

    ``Body.removeObject`` + ``Body.insertObject(after=True)`` per member, as
    PartDesign_MoveFeatureInTree does, so BaseFeature links are rewired by the
    body. The tip stays on the same object, except that a tip at the end stays at
    the end. With ``recompute`` (default) the document is recomputed, dress-ups
    whose base changed are rebound to the same geometry (``rebind_dressups``) and
    the document recomputed again. Returns the names moved. Raises ValueError
    when ``can_move`` says no, RuntimeError when a dress-up's edge or face no
    longer exists on its new base (the caller aborts its transaction).
    """
    ok, reason = can_move(body, feature, direction)
    if not ok:
        raise ValueError(reason)
    snapshot = snapshot_dressups(body) if recompute else {}
    solids_before = solids_in_group(body)
    tip = body.Tip
    tip_was_last = tip is not None and solids_before and tip == solids_before[-1]
    neighbour = _neighbour_solid(body, feature, direction)
    moving = feature_group(body, feature)
    crossed = feature_group(body, neighbour)
    for obj in moving:
        body.removeObject(obj)
    if direction == "earlier":
        # Before the first member of the crossed group: insert the first one before
        # it, then each next one after the previous.
        anchor = crossed[0]
        body.insertObject(moving[0], anchor, False)
    else:
        anchor = crossed[-1]
        body.insertObject(moving[0], anchor, True)
    previous = moving[0]
    for obj in moving[1:]:
        body.insertObject(obj, previous, True)
        previous = obj
    solids_after = solids_in_group(body)
    if tip_was_last:
        body.Tip = solids_after[-1]
    elif tip is not None and body.Tip != tip:
        body.Tip = tip
    moved = [obj.Name for obj in moving]
    if recompute:
        doc = body.Document
        doc.recompute()
        rebound, failed = rebind_dressups(body, snapshot)
        if failed:
            raise RuntimeError(
                "; ".join(
                    f"{doc.getObject(name).Label} no longer finds {', '.join(missing)} on "
                    f"{doc.getObject(name).BaseFeature.Label}"
                    for name, missing in failed
                )
            )
        if rebound:
            doc.recompute()
    return moved


def delete_feature(body, feature):
    """Take ``feature`` out of the body (relinks the chain, moves Tip off it) and
    out of the document. Used headless; the dock prefers Std_Delete in the GUI,
    which asks about dependants first."""
    body.removeObject(feature)
    feature.Document.removeObject(feature.Name)


def remove_feature(body, feature):
    """``Body.removeObject``. Virtual dispatch should run ``Body::removeObject``.

    That C++ method relinks the next solid's ``BaseFeature`` and moves ``Tip``
    off the removed object. ``insertObject`` does not move ``Tip``.
    """
    return body.removeObject(feature)
