# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketches and element picks for the part-modelling GUI checks.

Sketching itself is the sketch tests' job; here sketches are made by script
(in an undo step, as PartDesign_NewSketch would) and the feature commands are
then run from the UI.
"""

import FreeCAD as App
import Part
import Sketcher

V = App.Vector


def plane(body, role):
    """The body's origin plane "XY_Plane", "XZ_Plane" or "YZ_Plane"."""
    for f in body.Origin.OriginFeatures:
        if getattr(f, "Role", "") == role:
            return f
    raise LookupError(role)


def sketch(body, role="XY_Plane", z=0.0, name="Sketch"):
    doc = body.Document
    doc.openTransaction("Add sketch")
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(plane(body, role), "")]
    sk.MapMode = "FlatFace"
    if z:
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
    return sk


def done(sk):
    doc = sk.Document
    doc.recompute()
    doc.commitTransaction()
    return sk


def rect(sk, x0, y0, x1, y1):
    pts = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
    first = len(sk.Geometry)
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", first + i, 2, first + (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", first))
    sk.addConstraint(Sketcher.Constraint("Horizontal", first + 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", first + 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", first + 3))


def circle(sk, x, y, r):
    sk.addGeometry(Part.Circle(V(x, y, 0), V(0, 0, 1), r))


def solid_ok(obj):
    """(ok, detail): obj's shape is one valid solid."""
    try:
        shape = obj.Shape
    except Exception as exc:
        return False, str(exc)
    if shape.isNull():
        return False, "null shape"
    ok = shape.isValid() and len(shape.Solids) == 1
    return ok, f"valid={shape.isValid()} solids={len(shape.Solids)} volume={shape.Volume:.2f}"


def vertical_edges(shape, length, tol=1e-4):
    """Indexes (1-based) of straight vertical edges of the given length."""
    out = []
    for i, e in enumerate(shape.Edges):
        if e.Curve.__class__.__name__ not in ("Line", "LineSegment") or len(e.Vertexes) != 2:
            continue
        a, b = e.Vertexes[0].Point, e.Vertexes[1].Point
        if abs(a.x - b.x) < tol and abs(a.y - b.y) < tol and abs(abs(a.z - b.z) - length) < tol:
            out.append((i + 1, a))
    return out


def planar_faces(shape, normal, tol=1e-6):
    """Indexes (1-based) of planar faces with this outward normal, largest first."""
    out = []
    for i, f in enumerate(shape.Faces):
        if f.Surface.__class__.__name__ != "Plane":
            continue
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        if (n - normal).Length < tol:
            out.append((f.Area, i + 1))
    return [i for _a, i in sorted(out, reverse=True)]


def faces_of_kind(shape, kind):
    return [i + 1 for i, f in enumerate(shape.Faces) if f.Surface.__class__.__name__ == kind]
