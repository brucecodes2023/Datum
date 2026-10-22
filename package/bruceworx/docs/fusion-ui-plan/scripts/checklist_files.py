# SPDX-License-Identifier: LGPL-2.1-or-later
"""Build the starting files for the test checklist (section "Test session").

Run in the GUI on a copy of the user config (FreeCAD saves user.cfg on exit); the GUI
saves each file's view (camera, visibility), which FreeCADCmd cannot:

    BW_CHECKLIST_OUT=~/Developer/BruceWorx-checklist \\
        package/bruceworx/run-freecad.sh -u /tmp/copy.cfg checklist_files.py

FreeCAD quits when the files are written.

Each file holds what a group of steps starts from; the steps themselves are done by hand.
"""

import os
import zipfile

import FreeCAD as App

OUT = os.path.expanduser(os.environ.get("BW_CHECKLIST_OUT", "~/Developer/BruceWorx-checklist"))
V = App.Vector


def new_doc(name):
    return App.newDocument(name)


def save(doc, filename):
    path = os.path.join(OUT, filename)
    if App.GuiUp:
        import FreeCADGui as Gui

        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body" and obj.Tip and obj.ViewObject:
                obj.ViewObject.Visibility = True
                obj.Tip.ViewObject.Visibility = True
        view = Gui.getDocument(doc.Name).ActiveView
        view.viewIsometric()
        view.fitAll()
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    return path


def add(container, type_id, name):
    """New object in a document or inside a part/body/assembly."""
    if hasattr(container, "newObject"):
        return container.newObject(type_id, name)
    return container.addObject(type_id, name)


def body_with_box(container, name, length, width, height, at=V(0, 0, 0)):
    body = add(container, "PartDesign::Body", name)
    box = body.newObject("PartDesign::AdditiveBox", name + "Box")
    box.Length, box.Width, box.Height = length, width, height
    box.Placement = App.Placement(at, App.Rotation())  # unattached: Placement applies
    return body


def hole(body, name, x, y, radius, depth):
    cut = body.newObject("PartDesign::SubtractiveCylinder", name)
    cut.Radius, cut.Height = radius, depth
    cut.Placement = App.Placement(V(x, y, 0), App.Rotation())
    return cut


def old_two_direction_pattern():
    """Step 4: a 4 x 3 pattern as files saved before the Spacings2 fix carry it."""
    doc = new_doc("Step04")
    body = body_with_box(doc, "Body", 10, 10, 10)
    box = body.Group[-1]
    doc.recompute()
    pattern = body.newObject("PartDesign::LinearPattern", "LinearPattern")
    pattern.Originals = [box]
    pattern.Direction = (body.Origin.OriginFeatures[0], [""])  # X axis
    pattern.Direction2 = (body.Origin.OriginFeatures[1], [""])  # Y axis
    pattern.Mode = pattern.Mode2 = "Spacing"
    pattern.Occurrences, pattern.Offset = 4, 10.0
    pattern.Offset2 = 10.0
    pattern.Refine = True
    body.Tip = pattern
    # Saved as the old code showed it: a row on top of the original, so 4 x 2 boxes.
    pattern.Occurrences2 = 2
    doc.recompute()
    pattern.Occurrences2 = 3
    path = save(doc, "04-old-two-direction-pattern.FCStd")
    # The old default: the first gap of direction 2 is 0, so a row lands on the original.
    # Written into the file, because the fixed code repairs it as soon as it is set.
    _zero_first_spacing2(path)
    return path


def _zero_first_spacing2(path):
    """Set the first value of the pattern's Spacings2 list (a binary entry in the zip) to 0."""
    import struct

    with zipfile.ZipFile(path) as src:
        xml = src.read("Document.xml").decode("utf-8")
        start = xml.index('<Property name="Spacings2"')
        name = xml[xml.index('file="', start) + 6 :]
        name = name[: name.index('"')]
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == name:
                # uint32 count, then little-endian doubles
                data = data[:4] + struct.pack("<d", 0.0) + data[12:]
            dst.writestr(item, data)
    os.replace(tmp, path)


def pattern_bodies():
    """Steps 5-9, 11-13: a block with a pocket, and a plate with a hole off to the side."""
    doc = new_doc("Steps05to13")
    block = body_with_box(doc, "Block", 20, 20, 10)
    hole(block, "Pocket", 10, 10, 3, 10)
    plate = body_with_box(doc, "Plate", 100, 100, 5, at=V(40, -40, 0))
    hole(plate, "AxisHole", 110, 10, 4, 5)
    doc.recompute()
    return save(doc, "05-13-patterns.FCStd")


def _assembly(doc):
    import JointObject

    assembly = add(doc, "Assembly::AssemblyObject", "Assembly")
    joints = assembly.newObject("Assembly::JointGroup", "Joints")
    return assembly, joints, JointObject


def _ground(joints, JointObject, obj):
    ground = joints.newObject("App::FeaturePython", "Ground" + obj.Name)
    JointObject.GroundedJoint(ground, obj)


def bolt_assembly():
    """Steps 10 and 15: a grounded plate with six holes and one bolt in the first hole."""
    doc = new_doc("Steps10and15")
    assembly, joints, JointObject = _assembly(doc)
    plate = assembly.newObject("App::Part", "Plate")
    body = body_with_box(plate, "PlateBody", 80, 80, 6, at=V(-40, -40, 0))
    first = hole(body, "BoltHole", 25, 0, 3.3, 6)
    doc.recompute()
    holes = body.newObject("PartDesign::PolarPattern", "BoltHoles")
    holes.Originals = [first]
    holes.Axis = (body.Origin.OriginFeatures[2], [""])  # Z axis
    holes.Occurrences = 6
    body.Tip = holes
    bolt = assembly.newObject("App::Part", "Bolt")
    bolt_body = bolt.newObject("PartDesign::Body", "BoltBody")
    shank = bolt_body.newObject("PartDesign::AdditiveCylinder", "Shank")
    shank.Radius, shank.Height = 3, 20
    head = bolt_body.newObject("PartDesign::AdditiveCylinder", "Head")
    head.Radius, head.Height = 5, 4
    head.Placement = App.Placement(V(0, 0, 20), App.Rotation())
    bolt.Placement = App.Placement(V(25, 0, -14), App.Rotation())
    _ground(joints, JointObject, plate)
    doc.recompute()
    return save(doc, "10-15-bolt-assembly.FCStd")


def joint_parts(filename, docname):
    """Steps 14 and 16: a grounded base and a loose arm, no joints yet."""
    doc = new_doc(docname)
    assembly, joints, JointObject = _assembly(doc)
    base = assembly.newObject("App::Part", "Base")
    body_with_box(base, "BaseBody", 40, 40, 10)
    arm = assembly.newObject("App::Part", "Arm")
    body_with_box(arm, "ArmBody", 60, 10, 5)
    arm.Placement = App.Placement(V(10, 60, 25), App.Rotation(V(0, 0, 1), 20))
    _ground(joints, JointObject, base)
    doc.recompute()
    return save(doc, filename)


def part_properties():
    """Step 17: a part named Part (the console line uses App.ActiveDocument.Part) and an
    assembly that uses it."""
    doc = new_doc("Step17")
    part = add(doc, "App::Part", "Part")
    part.Label = "Bracket"
    body_with_box(part, "BracketBody", 50, 30, 8)
    assembly, joints, JointObject = _assembly(doc)
    link = assembly.newObject("App::Link", "BracketLink")
    link.LinkedObject = part
    link.Label = "Bracket"
    _ground(joints, JointObject, link)
    part.Visibility = False
    doc.recompute()
    return save(doc, "17-part-properties.FCStd")


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):  # only files this script writes
        if name.endswith((".FCStd", ".FCBak", ".tmp")) and name[:2].isdigit():
            os.remove(os.path.join(OUT, name))
    made = [
        old_two_direction_pattern(),
        pattern_bodies(),
        bolt_assembly(),
        joint_parts("14-revolute-limits.FCStd", "Step14"),
        joint_parts("16-keep-position.FCStd", "Step16"),
        part_properties(),
    ]
    for path in made:
        App.Console.PrintMessage("CHECKLIST " + path + "\n")


if App.GuiUp:
    from PySide import QtCore, QtWidgets

    def _run():
        try:
            main()
        finally:
            QtWidgets.QApplication.quit()

    QtCore.QTimer.singleShot(3000, _run)  # once the main window is up
else:
    main()
