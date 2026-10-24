# SPDX-License-Identifier: LGPL-2.1-or-later
"""Render the 3D content of the MOD-14 and UIX-12 mocks: the plate (plate.py) with the faces being edited selected,
transparent background, plus the screen positions of the points the mock draws its arrows and fields at.

Writes <job>.png and points.js (window.PTS = {job: {w, h, points: {name: [x, y]}}}) into BW_MOCK_OUT.
"""
import json
import math
import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

OUT = os.environ["BW_MOCK_OUT"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
log = open(os.path.join(OUT, "views.log"), "w")
V = App.Vector
PTS = {}


def say(*a):
    log.write(" ".join(str(x) for x in a) + "\n")
    log.flush()


def viewport():
    mw = Gui.getMainWindow()
    for w in mw.findChildren(QtWidgets.QWidget):
        if w.metaObject().className() == "Gui::View3DInventor":
            return w.findChild(QtWidgets.QGraphicsView).viewport()


def faces_where(body, test):
    return ["Face%d" % (i + 1) for i, f in enumerate(body.Shape.Faces) if test(f)]


def select(doc, part, faces):
    Gui.Selection.clearSelection()
    for f in faces:
        Gui.Selection.addSelection(doc.Name, part.Name, "Body." + f)
    say("selected", faces)


def save(job, points, polys=None):
    PENDING.append((job, points, polys or {}))


def _save(job, points, polys):
    view = Gui.ActiveDocument.ActiveView
    vp = viewport()
    dpr = vp.devicePixelRatioF()
    w, h = int(vp.width() * dpr), int(vp.height() * dpr)
    view.saveImage(os.path.join(OUT, job + ".png"), w, h, "Transparent")
    pts = {}
    for name, p in points.items():
        x, y = view.getPointOnViewport(p)
        pts[name] = [x, h - y - 1]
    def scr(p):
        x, y = view.getPointOnViewport(p)
        return [x, h - y - 1]

    PTS[job] = {"w": w, "h": h, "points": pts, "polys": {k: [scr(p) for p in v] for k, v in polys.items()}}
    say("saved", job, w, h, pts)


def ring(cx, cy, r, z, n=48, a0=0.0, a1=360.0):
    return [V(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)), z)
            for i in range(n + 1)]


def build():
    import plate
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    doc = App.newDocument("Plate")
    part, body, pad, boss, hole, fil = plate.build(doc)
    for o in (part, body):
        o.ViewObject.Visibility = True
    v = Gui.ActiveDocument.ActiveView
    v.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    return doc, part, body, boss, hole, fil


def job_pad():
    doc, part, body, boss, hole, fil = build()
    boss.Length = 20
    doc.recompute()
    say("hole top z", hole.Profile[0].Placement.Base.z)
    select(doc, part, faces_where(body, lambda f: f.BoundBox.ZMin > 27.9 and f.Surface.__class__.__name__ == "Plane"))
    pts = {"base": V(55, 25, 28), "base0": V(55, 25, 22), "tipdir": V(55, 25, 29)}
    save("pad", pts, {"top": ring(55, 25, 12, 28), "in": ring(55, 25, 4.5, 28), "was": ring(55, 25, 12, 22)})


def job_fillet():
    doc, part, body, boss, hole, fil = build()
    sel = faces_where(body, lambda f: hasattr(f.Surface, "Radius") and abs(f.Surface.Radius - 5) < 1e-6
                      and f.BoundBox.XMin > 70 and f.BoundBox.YMin > 40)
    select(doc, part, sel)
    save("fillet", polys={"arcT": ring(75, 45, 5, 8, 16, 0, 90), "arcB": ring(75, 45, 5, 0, 16, 0, 90)}, points={"edge": V(80, 50, 4), "t1": V(75, 50, 4), "t2": V(80, 45, 4), "mid": V(80 - 5 + 5 / math.sqrt(2), 50 - 5 + 5 / math.sqrt(2), 4),
                    "etop": V(80, 50, 8), "ebot": V(80, 50, 0)})


def job_hole():
    doc, part, body, boss, hole, fil = build()
    body.ViewObject.Transparency = 55
    for o in body.Group:
        if hasattr(o, "ViewObject") and o.ViewObject is not None and "Transparency" in o.ViewObject.PropertiesList:
            o.ViewObject.Transparency = 55
    sel = faces_where(body, lambda f: hasattr(f.Surface, "Radius") and abs(f.Surface.Radius - 4.5) < 1e-6
                      and abs(f.Surface.Center.x - 55) < 1e-6)
    select(doc, part, sel)
    save("hole", {"top": V(55, 25, 22), "bottom": V(55, 25, -2), "below": V(55, 25, -3)},
         {"rtop": ring(55, 25, 4.5, 22), "rbot": ring(55, 25, 4.5, -2)})


def job_uix12():
    doc, part, body, boss, hole, fil = build()
    sel = faces_where(body, lambda f: f.BoundBox.ZMin > 7.9 and f.BoundBox.XMin > 42.9 and f.BoundBox.XMax < 67.1
                      and f.BoundBox.YMin > 12.9 and f.BoundBox.YMax < 37.1
                      and not (hasattr(f.Surface, "Radius") and f.Surface.Radius < 5))
    select(doc, part, sel)
    save("uix12", {"boss": V(55, 25, 22)},
         {"top": ring(55, 25, 12, 22), "bot": ring(55, 25, 12, 8), "in": ring(55, 25, 4.5, 22)})


JOBS = [job_pad, job_fillet, job_hole, job_uix12]
state = {"i": 0}
PENDING = []


def step():
    try:
        if PENDING:
            _save(*PENDING.pop())
            return
        i = state["i"]
        state["i"] += 1
        if i < len(JOBS):
            JOBS[i]()
            return
        with open(os.path.join(OUT, "points.js"), "w") as f:
            f.write("window.PTS = " + json.dumps(PTS) + ";\n")
    except Exception:
        say(traceback.format_exc())
    timer.stop()
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    QtWidgets.QApplication.quit()


timer = QtCore.QTimer()
timer.timeout.connect(step)
QtCore.QTimer.singleShot(3000, lambda: timer.start(2500))
