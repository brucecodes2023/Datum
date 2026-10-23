"""Render the 3D content for the PDM-04 mock: a hinge assembly and its parts, transparent background."""
import os, traceback
import FreeCAD as App, FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

OUT = os.environ["BW_MOCK_OUT"]
log = open(os.path.join(OUT, "views.log"), "w")
V = App.Vector


def say(*a):
    log.write(" ".join(str(x) for x in a) + "\n")
    log.flush()


R_KN, R_BORE, T, W, H = 6.0, 3.0, 3.0, 50.0, 90.0


def leaf(knuckles, side):
    """A hinge leaf: plate on +x (side=1) or -x (side=-1), knuckles on the z axis."""
    x0 = 4.0 if side > 0 else -4.0 - W
    plate = Part.makeBox(W, T, H, V(x0, -T / 2, 0))
    s = plate
    for z0, z1 in knuckles:
        s = s.fuse(Part.makeCylinder(R_KN, z1 - z0, V(0, 0, z0)))
    s = s.cut(Part.makeCylinder(R_BORE, H + 2, V(0, 0, -1)))
    holes = []
    for z in (15, 45, 75):
        holes.append(Part.makeCylinder(2.2, 10, V(side * (4 + W * 0.6), -5, z), V(0, 1, 0)))
        holes.append(Part.makeCone(2.2, 4.4, 2.2, V(side * (4 + W * 0.6), -T / 2 + 2.2 - 0.01, z), V(0, -1, 0)))
    return s.cut(holes).removeSplitter()


def frame():
    return leaf([(0, 29), (61, 90)], 1)


def door(angle=50):
    s = leaf([(30.5, 59.5)], -1)
    s.rotate(V(0, 0, 0), V(0, 0, 1), angle)
    return s


def pin():
    s = Part.makeCylinder(R_BORE - 0.1, H + 4, V(0, 0, -2))
    return s.fuse(Part.makeCylinder(4.6, 3, V(0, 0, H + 2))).removeSplitter()


def screw(shank=True):
    head = Part.makeCone(4.4, 2.2, 2.2, V(0, 0, 0), V(0, 0, -1))
    s = head.fuse(Part.makeCylinder(2.0, 10, V(0, 0, -12))).removeSplitter() if shank else head
    slot = Part.makeBox(9, 0.9, 1.2, V(-4.5, -0.45, -1.1))
    return s.cut(slot)


def screws(side, angle):
    out = []
    for z in (15, 45, 75):
        s = screw(shank=False)
        s.rotate(V(0, 0, 0), V(1, 0, 0), 90)  # head flush on the -y face
        s.translate(V(side * (4 + W * 0.6), -T / 2, z))
        if angle:
            s.rotate(V(0, 0, 0), V(0, 0, 1), angle)
        out.append(s)
    return out


def bracket():
    """The bracket of the UI mock (scripts/ui_mock/views.py)."""
    base = Part.makeBox(90, 60, 8)
    up = Part.makeBox(90, 8, 52, V(0, 52, 0))
    gw = Part.Face(Part.makePolygon([V(0, 52, 8), V(0, 22, 8), V(0, 52, 38), V(0, 52, 8)])).extrude(V(6, 0, 0))
    g1 = gw.copy(); g1.translate(V(4, 0, 0))
    g2 = gw.copy(); g2.translate(V(80, 0, 0))
    s = base.fuse([up, g1, g2]).removeSplitter()
    holes = [Part.makeCylinder(4.5, 20, V(x, y, -5)) for x in (24, 66) for y in (16, 38)]
    holes += [Part.makeCylinder(5, 20, V(x, 45, 32), V(0, 1, 0)) for x in (30, 60)]
    return s.cut(holes)


def washer():
    return Part.makeCylinder(6.5, 1.2).cut(Part.makeCylinder(3.1, 3, V(0, 0, -1)))


def spacer():
    s = Part.makeBox(W, 2, H, V(4, 0, 0))
    return s.cut([Part.makeCylinder(2.2, 10, V(4 + W * 0.6, -5, z), V(0, 1, 0)) for z in (15, 45, 75)])


def style(obj, color, lw=1.5):
    vo = obj.ViewObject
    vo.ShapeAppearance = [App.Material(DiffuseColor=color)]
    vo.LineColor = (0.12, 0.12, 0.14)
    vo.LineWidth = lw


def save(name, w, h):
    v = Gui.ActiveDocument.ActiveView
    v.saveImage(os.path.join(OUT, name), w, h, "Transparent")
    say("saved", name)


def show(doc, items, name, w, h, zoom=1.0):
    for nm, shape, col in items:
        o = doc.addObject("Part::Feature", nm)
        o.Shape = shape
    doc.recompute()
    for nm, shape, col in items:
        style(doc.getObject(nm), col)
    v = Gui.ActiveDocument.ActiveView
    v.viewIsometric()
    v.setCameraType("Perspective")
    Gui.SendMsgToActiveView("ViewFit")
    save(name, w, h)


FRAME_C, DOOR_C, PIN_C, SCREW_C = (0.62, 0.66, 0.72), (0.80, 0.82, 0.85), (0.86, 0.74, 0.47), (0.36, 0.37, 0.40)
JOBS = [
    ("view-asm.png", 2400, 1500, lambda: [("Frame", frame(), FRAME_C), ("Door", door(), DOOR_C), ("Pin", pin(), PIN_C)]
     + [(f"S{i}", s, SCREW_C) for i, s in enumerate(screws(1, 0) + screws(-1, 50))]),
    ("thumb-asm.png", 600, 600, lambda: [("Frame", frame(), FRAME_C), ("Door", door(), DOOR_C), ("Pin", pin(), PIN_C)]),
    ("thumb-frame.png", 600, 600, lambda: [("Frame", frame(), FRAME_C)]),
    ("thumb-door.png", 600, 600, lambda: [("Door", door(0), DOOR_C)]),
    ("thumb-pin.png", 600, 600, lambda: [("Pin", pin(), PIN_C)]),
    ("thumb-screw.png", 600, 600, lambda: [("Screw", screw(), SCREW_C)]),
    ("thumb-bracket.png", 600, 600, lambda: [("Bracket", bracket(), (0.78, 0.80, 0.83))]),
    ("thumb-washer.png", 600, 600, lambda: [("Washer", washer(), PIN_C)]),
    ("thumb-spacer.png", 600, 600, lambda: [("Spacer", spacer(), (0.55, 0.62, 0.70))]),
]
state = {"step": 0}


def step():
    try:
        s = state["step"]
        state["step"] += 1
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if s < len(JOBS):
            name, w, h, make = JOBS[s]
            doc = App.newDocument("Mock%d" % s)
            show(doc, make(), name, w, h)
        else:
            timer.stop()
            QtWidgets.QApplication.quit()
    except Exception:
        say(traceback.format_exc())
        timer.stop()
        QtWidgets.QApplication.quit()


timer = QtCore.QTimer()
timer.timeout.connect(step)
timer.start(2500)
