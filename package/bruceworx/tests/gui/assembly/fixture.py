# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small part files for the assembly checks, each in its own .FCStd.

* Base: a 100 x 60 x 10 plate with two through holes of 10 mm (at x 25 and 75,
  y 30), part number BW-100-001 rev 01.
* Pin: a 10 mm pin, 30 long, standing on the origin, BW-200-001 rev A.
* Block: a 20 x 20 x 10 block, no part properties.
* SubAsm: an assembly of Pin + Block (a sub-assembly for the BOM).

Every part is an App::Part holding a PartDesign body, as a user would make it.
"""

import os

import FreeCAD as App
import Part
import Sketcher

V = App.Vector


def _rect(sk, x0, y0, w, h):
    pts = [V(x0, y0, 0), V(x0 + w, y0, 0), V(x0 + w, y0 + h, 0), V(x0, y0 + h, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))


def _body(doc, part, label):
    body = doc.addObject("PartDesign::Body", label.replace(" ", "") + "Body")
    part.addObject(body)
    return body


def _sketch(body, name, z=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]  # XY plane
    sk.MapMode = "FlatFace"
    if z:
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
    return sk


def _props(part, pn, rev, desc):
    try:
        from heuretes_freecad.partprops import load_schema

        load_schema().apply(part)
    except Exception as exc:  # the add-on is optional for these checks
        App.Console.PrintWarning(f"fixture: part properties not applied: {exc}\n")
        for name in ("PartNumber", "Revision", "Description"):
            if name not in part.PropertiesList:
                part.addProperty("App::PropertyString", name, "Part")
    part.PartNumber = pn
    part.Revision = rev
    part.Description = desc


def make_base(path):
    doc = App.newDocument("Base")
    part = doc.addObject("App::Part", "Base")
    part.Label = "Base plate"
    body = _body(doc, part, "Base")
    sk = _sketch(body, "Sketch")
    _rect(sk, 0, 0, 100, 60)
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    sk.Visibility = False
    doc.recompute()
    sk2 = _sketch(body, "Sketch001", 10)
    sk2.addGeometry(Part.Circle(V(25, 30, 0), V(0, 0, 1), 5))
    sk2.addGeometry(Part.Circle(V(75, 30, 0), V(0, 0, 1), 5))
    doc.recompute()
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = sk2
    pocket.Type = "ThroughAll"
    sk2.Visibility = False
    doc.recompute()
    _props(part, "BW-100-001", "01", "Base plate 100 x 60")
    doc.recompute()
    doc.saveAs(path)
    return doc


def make_pin(path):
    doc = App.newDocument("Pin")
    part = doc.addObject("App::Part", "Pin")
    body = _body(doc, part, "Pin")
    sk = _sketch(body, "Sketch")
    sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 5))
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 30
    sk.Visibility = False
    doc.recompute()
    _props(part, "BW-200-001", "A", "Pin 10 x 30")
    doc.recompute()
    doc.saveAs(path)
    return doc


def make_block(path):
    doc = App.newDocument("Block")
    part = doc.addObject("App::Part", "Block")
    body = _body(doc, part, "Block")
    sk = _sketch(body, "Sketch")
    _rect(sk, 0, 0, 20, 20)
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    sk.Visibility = False
    doc.recompute()
    doc.saveAs(path)
    return doc


def make_parts(folder):
    """Base, Pin and Block files in folder; returns {name: path}. Docs stay open."""
    os.makedirs(folder, exist_ok=True)
    paths = {n: os.path.join(folder, n + ".FCStd") for n in ("Base", "Pin", "Block")}
    for name, maker in (("Base", make_base), ("Pin", make_pin), ("Block", make_block)):
        old = App.listDocuments().get(name)
        if old is not None:
            App.closeDocument(old.Name)
        if os.path.exists(paths[name]):
            os.remove(paths[name])
        maker(paths[name])
    return paths


def new_assembly_doc(path, name="Assembly"):
    """A saved document with one assembly (as Assembly_CreateAssembly makes it)."""
    import UtilsAssembly  # noqa: F401  (loads the Assembly module)

    doc = App.newDocument(os.path.splitext(os.path.basename(path))[0])
    asm = doc.addObject("Assembly::AssemblyObject", name)
    asm.Type = "Assembly"
    asm.newObject("Assembly::JointGroup", "Joints")
    doc.recompute()
    doc.saveAs(path)
    return doc, asm


def add_link(asm, target, name, placement=None):
    link = asm.newObject("App::Link", name)
    link.LinkedObject = target
    link.Label = target.Label if name is None else name
    if placement is not None:
        link.Placement = placement
    return link


def ground(asm, obj):
    """A grounded joint on obj (as Toggle Grounded makes it; works with the assembly not in edit)."""
    import JointObject
    import UtilsAssembly

    group = UtilsAssembly.getJointGroup(asm)
    joint = group.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(joint, obj)
    if App.GuiUp:
        JointObject.ViewProviderGroundedJoint(joint.ViewObject)
    asm.Document.recompute()
    return joint


def make_subasm(folder, pin_doc, block_doc):
    """SubAsm.FCStd: Pin standing on Block, Block grounded, a Fixed-like placement."""
    path = os.path.join(folder, "SubAsm.FCStd")
    old = App.listDocuments().get("SubAsm")
    if old is not None:
        App.closeDocument(old.Name)
    if os.path.exists(path):
        os.remove(path)
    doc, asm = new_assembly_doc(path, "SubAssembly")
    asm.Label = "Pin on block"
    blk = add_link(asm, block_doc.getObject("Block"), "Block")
    pin = add_link(asm, pin_doc.getObject("Pin"), "Pin", App.Placement(V(10, 10, 10), App.Rotation()))
    ground(asm, blk)
    doc.recompute()
    doc.save()
    return doc, asm
