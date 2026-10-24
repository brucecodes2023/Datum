# SPDX-License-Identifier: LGPL-2.1-or-later
"""The mock model for MOD-14 and UIX-12: a plate with a boss, two holes and filleted corners (PartDesign)."""
import FreeCAD as App
import Part
import Sketcher

V = App.Vector


def _rect(sk, w, h):
    pts = [V(0, 0, 0), V(w, 0, 0), V(w, h, 0), V(0, h, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sk.addConstraint(Sketcher.Constraint("Vertical", 1))
    sk.addConstraint(Sketcher.Constraint("Vertical", 3))
    sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
    sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, w))
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, h))


def build(doc):
    part = doc.addObject("App::Part", "Part")
    part.Label = "Mounting plate"
    body = doc.addObject("PartDesign::Body", "Body")
    part.addObject(body)
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # XY plane
    sk.MapMode = "FlatFace"
    _rect(sk, 80, 50)
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 8
    pad.Label = "Base plate"
    sk.Visibility = False
    doc.recompute()
    # Boss: second pad on the plate's top
    sk2 = body.newObject("Sketcher::SketchObject", "Sketch001")
    sk2.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sk2.MapMode = "FlatFace"
    sk2.AttachmentOffset = App.Placement(V(0, 0, 8), App.Rotation())
    sk2.addGeometry(Part.Circle(V(55, 25, 0), V(0, 0, 1), 12))
    doc.recompute()
    boss = body.newObject("PartDesign::Pad", "Pad001")
    boss.Profile = sk2
    boss.Length = 14
    boss.Label = "Boss"
    sk2.Visibility = False
    doc.recompute()
    # Two holes: one in the boss, one through the plate
    sk3 = body.newObject("Sketcher::SketchObject", "Sketch002")
    sk3.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sk3.MapMode = "FlatFace"
    sk3.AttachmentOffset = App.Placement(V(0, 0, 22), App.Rotation())
    sk3.setExpression(".AttachmentOffset.Base.z", "8 mm + Pad001.Length")  # holes start on the boss top
    sk3.addGeometry(Part.Circle(V(55, 25, 0), V(0, 0, 1), 3))
    sk3.addGeometry(Part.Circle(V(18, 25, 0), V(0, 0, 1), 3))
    doc.recompute()
    hole = body.newObject("PartDesign::Hole", "Hole")
    hole.Profile = sk3
    hole.Diameter = 9
    hole.Depth = 24
    hole.DepthType = "Dimension"
    hole.Threaded = False
    sk3.Visibility = False
    doc.recompute()
    # Fillet the four vertical corner edges of the plate
    shape = hole.Shape
    edges = []
    for i, e in enumerate(shape.Edges):
        p0, p1 = e.Vertexes[0].Point, e.Vertexes[-1].Point if len(e.Vertexes) > 1 else e.Vertexes[0].Point
        if len(e.Vertexes) == 2 and abs(p0.x - p1.x) < 1e-6 and abs(p0.y - p1.y) < 1e-6 and \
                abs(abs(p0.z - p1.z) - 8) < 1e-6 and p0.x in (0, 80) and p0.y in (0, 50):
            edges.append("Edge%d" % (i + 1))
    fil = body.newObject("PartDesign::Fillet", "Fillet")
    fil.Base = (hole, edges)
    fil.Radius = 5
    doc.recompute()
    return part, body, pad, boss, hole, fil
