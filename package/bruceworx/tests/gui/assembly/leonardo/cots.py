# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simple models of the Leonardo gearbox's bought parts, made through PartDesign's own dialogs.

Each builder is a generator for asmlib.run: it adds a Body to the given App::Part, draws
the sketches with the sketcher API (the sketch geometry is not what is under test here), then
runs PartDesign_Pad / PartDesign_Pocket / PartDesign_Chamfer the way a user does: select,
run the command, type the value in the task panel, press OK.

* bearing(part, d, D, B)   683ZZ: 3 x 7 x 3, 688ZZ: 8 x 16 x 5 (a ring with 0.2 chamfers)
* dowel(part, d, L)        dowel pin 3 x 10 (0.3 chamfers)
* shcs(part, L)            M3 socket head cap screw: head 5.5 x 3, 2.5 hex socket 1.5 deep
"""

import math

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtWidgets

import asmlib as L

V = App.Vector


def _sketch(body, name, z=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # XY plane
    sk.MapMode = "FlatFace"
    if z:
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
    return sk


def _circle(sk, r, c=V(0, 0, 0)):
    sk.addGeometry(Part.Circle(c, V(0, 0, 1), r))


def _hexagon(sk, af):
    r = af / math.sqrt(3)  # corner radius for this width across flats
    pts = [V(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)), 0) for i in range(6)]
    for i in range(6):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 6]))


def _spin(name):
    tv = L.task_view()
    if tv is None:
        return None
    for w in tv.findChildren(QtWidgets.QWidget, name):
        return w
    return None


def _feature(doc, before, type_id):
    new = [o for o in doc.Objects if o.Name not in before and o.TypeId == type_id]
    return new[-1] if new else None


def ui_extrude(body, sketch, command, length, reversed_=False, through=False, label=""):
    """Select the sketch, run PartDesign_Pad/Pocket, type the length, press OK. Returns the feature."""
    doc = body.Document
    before = {o.Name for o in doc.Objects}
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, sketch.Name)
    Gui.runCommand(command)
    yield 900
    kind = "PartDesign::Pad" if command.endswith("Pad") else "PartDesign::Pocket"
    feat = _feature(doc, before, kind)
    L.check(f"{label or kind}: {command} dialog opens", feat is not None and Gui.Control.activeDialog())
    if feat is None:
        return None
    if through:
        feat.Type = "ThroughAll"
    else:
        box = _spin("lengthEdit")
        if box is not None:
            box.setProperty("rawValue", float(length))
        else:
            feat.Length = length
    if reversed_:
        feat.Reversed = True
    yield 400
    L.press("OK")
    yield 900
    doc.recompute()
    ok = not Gui.Control.activeDialog() and feat.isValid() and feat.Shape.isValid()
    L.check(f"{label or kind}: OK gives a valid solid", ok, feat.State)
    return feat


def ui_chamfer(body, feature, edges, size, label=""):
    """Select edges of the tip, run PartDesign_Chamfer, set the size, press OK."""
    doc = body.Document
    before = {o.Name for o in doc.Objects}
    Gui.Selection.clearSelection()
    for e in edges:
        Gui.Selection.addSelection(doc.Name, feature.Name, e)
    Gui.runCommand("PartDesign_Chamfer")
    yield 900
    feat = _feature(doc, before, "PartDesign::Chamfer")
    L.check(f"{label}: chamfer dialog opens", feat is not None and Gui.Control.activeDialog())
    if feat is None:
        return None
    feat.Size = size
    yield 300
    L.press("OK")
    yield 900
    doc.recompute()
    L.check(f"{label}: chamfer valid", feat.isValid() and feat.Shape.isValid(), feat.State)
    return feat


def _circular_edges(shape, radii):
    out = []
    for i, e in enumerate(shape.Edges, 1):
        r = getattr(e.Curve, "Radius", None)
        if r is not None and any(abs(r - x) < 1e-6 for x in radii):
            out.append(f"Edge{i}")
    return out


def _new_body(part, name):
    doc = part.Document
    body = doc.addObject("PartDesign::Body", name)
    part.addObject(body)
    Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", body)
    return body


def bearing(part, d, D, B, name="Bearing"):
    body = _new_body(part, name + "Body")
    sk = _sketch(body, "Sketch")
    _circle(sk, D / 2)
    _circle(sk, d / 2)
    part.Document.recompute()
    L.tell(f"{name}: pad the ring {d} x {D} by {B} mm through the Pad dialog; expect a valid ring")
    pad = yield from ui_extrude(body, sk, "PartDesign_Pad", B, label=f"{name} ring")
    if pad is None:
        return body
    edges = _circular_edges(pad.Shape, (d / 2, D / 2))
    L.tell(f"{name}: chamfer the {len(edges)} ring edges 0.2 mm")
    yield from ui_chamfer(body, pad, edges, 0.2, label=f"{name}")
    return body


def dowel(part, d, length, name="Dowel"):
    body = _new_body(part, name + "Body")
    sk = _sketch(body, "Sketch")
    _circle(sk, d / 2)
    part.Document.recompute()
    L.tell(f"{name}: pad a Ø{d} circle {length} mm; expect a valid pin")
    pad = yield from ui_extrude(body, sk, "PartDesign_Pad", length, label=f"{name}")
    if pad is None:
        return body
    edges = _circular_edges(pad.Shape, (d / 2,))
    L.tell(f"{name}: chamfer both ends 0.3 mm")
    yield from ui_chamfer(body, pad, edges, 0.3, label=f"{name}")
    return body


def shcs(part, length, name="Screw"):
    """M3 SHCS standing on the origin: head z 0..3, shank z 0..-length."""
    body = _new_body(part, name + "Body")
    sk = _sketch(body, "HeadSketch")
    _circle(sk, 2.75)
    part.Document.recompute()
    L.tell(f"{name}: pad the head Ø5.5 x 3")
    head = yield from ui_extrude(body, sk, "PartDesign_Pad", 3, label=f"{name} head")
    sk2 = _sketch(body, "ShankSketch")
    _circle(sk2, 1.5)
    part.Document.recompute()
    L.tell(f"{name}: pad the shank Ø3 x {length}, reversed (down from the head)")
    yield from ui_extrude(body, sk2, "PartDesign_Pad", length, reversed_=True, label=f"{name} shank")
    sk3 = _sketch(body, "SocketSketch", 3)
    _hexagon(sk3, 2.5)
    part.Document.recompute()
    L.tell(f"{name}: pocket the 2.5 mm hex socket 1.5 deep into the head")
    yield from ui_extrude(body, sk3, "PartDesign_Pocket", 1.5, label=f"{name} socket")
    if head is not None:
        bb = body.Shape.BoundBox
        L.check(f"{name}: overall length {length + 3} mm", abs(bb.ZLength - (length + 3)) < 1e-6, bb.ZLength)
    return body
