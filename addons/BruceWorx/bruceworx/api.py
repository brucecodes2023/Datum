# SPDX-License-Identifier: LGPL-2.1-or-later
"""Python API over the BruceWorx headless hooks.

Early slice: a PartDesign body with a sketch and a pad, a tree summary, and
STEP export. FreeCAD is imported only when a call builds or writes geometry.
``tree_summary`` only needs ``Objects`` and ``OutList``.
"""

from __future__ import annotations

import math

from bruceworx.step_export import export_step as _export_step
from bruceworx.tree import document_tree


def create_padded_body(
    doc,
    length: float = 10.0,
    width: float = 10.0,
    height: float = 10.0,
    name: str = "Body",
) -> dict:
    """Add a PartDesign body, a rectangle sketch on its XY plane, and a pad.

    ``length`` and ``width`` are the rectangle in millimetres. ``height`` is
    the pad length. Returns ``{"body", "sketch", "pad"}``.
    """
    name = _require_name(name)
    length = _positive("length", length)
    width = _positive("width", width)
    height = _positive("height", height)
    from bruceworx.freecad_io import require_freecad  # noqa: PLC0415

    require_freecad()
    import Part  # noqa: PLC0415
    import PartDesign  # noqa: F401, PLC0415 — registers PartDesign::Body
    import Sketcher  # noqa: F401, PLC0415 — registers Sketcher::SketchObject

    body = doc.addObject("PartDesign::Body", name)
    if getattr(body, "Origin", None) is None and hasattr(doc, "recompute"):
        doc.recompute()
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    plane = _xy_plane(body)
    sketch.AttachmentSupport = [(plane, "")]
    sketch.MapMode = "FlatFace"
    _rectangle(sketch, length, width, Part)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = height
    doc.recompute()
    shape = getattr(body, "Shape", None)
    solids = getattr(shape, "Solids", None) if shape is not None else None
    is_null = shape is None or (callable(getattr(shape, "isNull", None)) and shape.isNull())
    if is_null or not solids:
        status = getattr(pad, "getStatusString", None)
        detail = status() if callable(status) else ""
        message = f"{pad.Name} produced no solid"
        if detail:
            message = f"{message} ({detail})"
        raise RuntimeError(message)
    return {"body": body, "sketch": sketch, "pad": pad}


def tree_summary(doc) -> list[dict]:
    """Roots of the document as ``name``, ``type`` and ``children``.

    ``type`` is the FreeCAD type id. The walk is
    :func:`bruceworx.tree.document_tree`: a root is an object that is not in
    another object's ``OutList``.
    """
    return [_summary(node) for node in document_tree(doc)["tree"]]


def export_step(doc, path: str, objects=None) -> dict:
    """Write solids from ``doc`` to a ``.step`` or ``.stp`` path.

    Recomputes, then uses :func:`bruceworx.step_export.export_step`. Returns
    ``{"output", "objects"}`` with the exported object names. ``objects``
    limits the export; the default is the solid roots (a body, not its pad).
    """
    return _export_step(doc, path, objects=objects)


def _summary(node: dict) -> dict:
    return {
        "name": node["name"],
        "type": node["typeid"],
        "children": [_summary(child) for child in node["children"]],
    }


def _require_name(name: str) -> str:
    text = str(name).strip()
    if not text:
        raise ValueError("name must not be empty")
    return text


def _positive(label: str, value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number, got {value!r}") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive number, got {value!r}")
    return number


def _xy_plane(body):
    origin = getattr(body, "Origin", None)
    features = list(getattr(origin, "OriginFeatures", None) or [])
    for feature in features:
        if getattr(feature, "Role", "") == "XY_Plane":
            return feature
    # PartDesign origin order is X/Y/Z axes, then the XY plane.
    if len(features) > 3:
        return features[3]
    raise RuntimeError(f"{body.Name} has no XY plane")


def _rectangle(sketch, length: float, width: float, part) -> None:
    import FreeCAD  # noqa: PLC0415

    corners = (
        FreeCAD.Vector(0, 0, 0),
        FreeCAD.Vector(length, 0, 0),
        FreeCAD.Vector(length, width, 0),
        FreeCAD.Vector(0, width, 0),
    )
    for index in range(4):
        sketch.addGeometry(part.LineSegment(corners[index], corners[(index + 1) % 4]))
