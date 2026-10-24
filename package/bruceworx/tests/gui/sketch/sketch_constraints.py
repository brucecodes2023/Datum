# SPDX-License-Identifier: LGPL-2.1-or-later
"""Constraints, solver messages, construction, trim / extend, Python geometry.

In a sketch on XY: a line made in Python is constrained with the toolbar
commands (horizontal, coincident to the origin, a distance through the
dimension dialog) until the panel says "Fully constrained"; a redundant and a
conflicting constraint must show in the panel; construction toggle; trim and
extend with the tools and mouse clicks and from Python; undo of a trim; leave
with the Edit Mode toolbar's Leave Sketch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
from harness import App, Gui, QtCore, QtWidgets, S, check, mw, note, step  # noqa: E402

import Part  # noqa: E402
import Sketcher  # noqa: E402

H.set_script("sketch_constraints")
V = App.Vector


def sk():
    return S["doc"].getObject(S["sk"])


def message():
    tv = H.task_view()
    for w in tv.findChildren(QtWidgets.QWidget) if tv else []:
        if w.metaObject().className() == "SketcherGui::TaskSketcherMessages":
            return " ".join(l.text() for l in w.findChildren(QtWidgets.QLabel) if l.isVisible() and l.text())
    return ""


def select(*subs):
    Gui.Selection.clearSelection()
    for sub in subs:
        Gui.Selection.addSelection(S["doc"].Name, S["sk"], sub)
    H.pump(100)


def refresh():
    S["doc"].recompute()
    H.pump(250)


@step
def s_setup():
    """New document with a Body in Design."""
    import bruceworx_workspaces

    bruceworx_workspaces.activate_workspace("Design")
    doc = App.newDocument("SketchCons")
    body = doc.addObject("PartDesign::Body", "Body")
    doc.recompute()
    S.update(doc=doc, body=body)
    return 800


@step
def s_new_sketch():
    """New Sketch on the XY plane."""
    xy = [f for f in S["body"].Origin.OriginFeatures if f.Name.startswith("XY_Plane")][0]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, xy.Name)
    Gui.runCommand("PartDesign_NewSketch")
    H.wait_for(lambda: H.in_edit() is not None, 4000)
    return 800


@step
def s_python_line():
    """Add a line from Python: expect 4 degrees of freedom in the panel."""
    obj = H.in_edit()
    check("sketch open", obj is not None)
    S["sk"] = obj.Name
    s = sk()
    s.addGeometry(Part.LineSegment(V(5, 5, 0), V(35, 10, 0)), False)
    refresh()
    Gui.SendMsgToActiveView("ViewFit")
    check("Python addGeometry line", s.GeometryCount == 1)
    check("panel: under-constrained, 4 DoF", "Under-constrained" in message() and "4 Degrees" in message(), message())


@step
def s_horizontal():
    """Select the line, click Horizontal: expect 3 degrees of freedom."""
    select("Edge1")
    a = H.action_for("Sketcher_ConstrainHorizontal")
    note(f"Sketcher_ConstrainHorizontal action enabled={a.isEnabled() if a else None}")
    Gui.runCommand("Sketcher_ConstrainHorizontal")
    refresh()
    types = [c.Type for c in sk().Constraints]
    check("Horizontal command on a selected line adds Horizontal", "Horizontal" in types, types)
    check("panel: 3 DoF after horizontal", "3 Degrees" in message(), message())


@step
def s_coincident():
    """Select line start + origin, click Coincident: expect 1 degree of freedom."""
    select("Vertex1", "RootPoint")
    Gui.runCommand("Sketcher_ConstrainCoincidentUnified")
    refresh()
    types = [c.Type for c in sk().Constraints]
    check("Coincident command (line start + origin) adds Coincident", "Coincident" in types, types)
    st = sk().Geometry[0].StartPoint
    check("line start moved onto the origin", st.Length < 1e-6, st)
    check("panel: 1 DoF after coincident", "1 Degree" in message(), message())


def _dimension_dialog(result):
    """Runs while the dimension dialog's exec() blocks: check, screenshot, type 25 mm, OK."""
    dlgs = [w for w in QtWidgets.QApplication.topLevelWidgets()
            if isinstance(w, QtWidgets.QDialog) and w.isVisible()]
    result["dialogs"] = [f"{type(d).__name__}:{d.windowTitle()}" for d in dlgs]
    if not dlgs:
        return
    d = dlgs[0]
    result["title"] = d.windowTitle()
    spins = [w for w in d.findChildren(QtWidgets.QWidget) if "QuantitySpinBox" in w.metaObject().className()]
    result["spin"] = bool(spins)
    try:
        result["shot"] = os.path.join(H.OUT, f"{H.TAG}-{H.SCRIPT}-dimension-dialog.png")
        d.grab().save(result["shot"])
        img = d.grab().toImage()
        lows = []
        for w in d.findChildren(QtWidgets.QWidget):
            if isinstance(w, (QtWidgets.QLabel, QtWidgets.QCheckBox, QtWidgets.QPushButton)) and w.isVisible() \
                    and w.text().strip():
                c = H.text_contrast(w, img, d)
                if c is not None and c < 3.0:
                    lows.append(f"{w.text()!r}={c}")
        result["low"] = lows
        result["size"] = (d.width(), d.height())
    except Exception as exc:
        result["err"] = str(exc)
    if spins:
        spins[0].setProperty("rawValue", 25.0)
        H.pump(50)
    boxes = d.findChildren(QtWidgets.QDialogButtonBox)
    if boxes and H.press(boxes[0], QtWidgets.QDialogButtonBox.Ok):
        result["ok"] = True
    else:
        d.accept()
        result["ok"] = "accept()"


@step
def s_dimension_dialog():
    """Select the line, click Distance: the dimension dialog opens, type 25 mm, OK: fully constrained."""
    result = {}
    S["dim"] = result
    select("Edge1")
    S["expect_dialog"] = True
    QtCore.QTimer.singleShot(800, lambda: _dimension_dialog(result))
    Gui.runCommand("Sketcher_ConstrainDistance")  # blocks in the dialog's exec()
    S["expect_dialog"] = False
    refresh()
    check("Distance command opens the dimension dialog", bool(result.get("dialogs")), result.get("dialogs"))
    check("dimension dialog has a value field", result.get("spin"), result)
    check("dimension dialog text readable", result.get("dialogs") and not result.get("low"), result.get("low"))
    note(f"dimension dialog: title={result.get('title')!r} size={result.get('size')} shot={result.get('shot')}")
    cons = [c for c in sk().Constraints if c.Type == "Distance"]
    check("dimension 25 mm applied from the dialog", cons and abs(cons[0].Value - 25) < 1e-6,
          [c.Value for c in cons])
    check("line is 25 mm long", abs(sk().Geometry[0].length() - 25) < 1e-6, sk().Geometry[0].length())
    check("panel: Fully constrained", "Fully constrained" in message(), message())
    H.check_readable("task panel readable (fully constrained)", H.task_view())
    H.grab("fully-constrained")


@step
def s_redundant():
    """Add a second Horizontal: expect "Redundant constraints" in the panel."""
    s = sk()
    S["ncons"] = s.ConstraintCount
    s.addConstraint(Sketcher.Constraint("Horizontal", 0))
    refresh()
    check("panel shows redundant constraints", "Redundant" in message(), message())
    H.grab("redundant")
    s.delConstraint(s.ConstraintCount - 1)
    refresh()
    check("panel back to Fully constrained after removing it", "Fully constrained" in message(), message())


@step
def s_conflicting():
    """Add a conflicting horizontal distance: expect "Over-constrained" in the panel."""
    s = sk()
    s.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 30.0))
    refresh()
    msg = message()
    check("panel shows conflicting (over-constrained) constraints", "Over-constrained" in msg or "onflict" in msg, msg)
    H.grab("conflicting")
    s.delConstraint(s.ConstraintCount - 1)
    refresh()
    check("panel back to Fully constrained after removing the conflict", "Fully constrained" in message(), message())


@step
def s_construction():
    """Toggle the line to construction and back."""
    select("Edge1")
    Gui.runCommand("Sketcher_ToggleConstruction")
    refresh()
    check("Toggle construction makes the line construction geometry", sk().getConstruction(0))
    H.grab("construction")
    select("Edge1")
    Gui.runCommand("Sketcher_ToggleConstruction")
    refresh()
    check("Toggle construction again makes it normal geometry", not sk().getConstruction(0))
    Gui.Selection.clearSelection()


@step
def s_python_shapes():
    """Add rectangle, circle, arc, polyline from Python."""
    s = sk()
    n = s.GeometryCount
    pts = [V(0, 20, 0), V(20, 20, 0), V(20, 35, 0), V(0, 35, 0)]
    for i in range(4):
        s.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        s.addConstraint(Sketcher.Constraint("Coincident", n + i, 2, n + (i + 1) % 4, 1))
    s.addGeometry(Part.Circle(V(40, 28, 0), V(0, 0, 1), 5), False)
    s.addGeometry(Part.ArcOfCircle(Part.Circle(V(60, 28, 0), V(0, 0, 1), 6), 0.0, 2.0), False)
    a = s.addGeometry(Part.LineSegment(V(70, 20, 0), V(80, 30, 0)), False)
    b = s.addGeometry(Part.LineSegment(V(80, 30, 0), V(90, 20, 0)), False)
    s.addConstraint(Sketcher.Constraint("Coincident", a, 2, b, 1))
    refresh()
    kinds = [type(g).__name__ for g in s.Geometry[n:]]
    check("Python: rectangle, circle, arc and polyline added", kinds == ["LineSegment"] * 4 + [
        "Circle", "ArcOfCircle", "LineSegment", "LineSegment"], kinds)
    check("sketch solves after Python geometry", "Under-constrained" in message(), message())
    Gui.SendMsgToActiveView("ViewFit")
    H.pump(200)
    H.grab("python-shapes")


@step
def s_trim_tool():
    """Trim tool: click the right half of a crossed line, expect it cut at the crossing."""
    s = sk()
    # Two crossing lines; trim the right-hand half of the horizontal one by clicking it.
    h = s.addGeometry(Part.LineSegment(V(0, -20, 0), V(40, -20, 0)), False)
    v = s.addGeometry(Part.LineSegment(V(20, -30, 0), V(20, -10, 0)), False)
    refresh()
    Gui.SendMsgToActiveView("ViewFit")
    H.pump(250)
    S["trim"] = (h, v)
    n = s.GeometryCount
    Gui.runCommand("Sketcher_Trimming")
    H.pump(250)
    H.click_vec(V(32, -20, 0))
    note(f"trim click at/preselection: {S.get('last_presel')} view {H._gv().viewport().size()}")
    H.pump(300)
    H.escape()
    refresh()
    s = sk()
    g = s.Geometry[h]
    xs = sorted([g.StartPoint.x, g.EndPoint.x])
    check("Trim tool: clicking the right half of a crossed line trims it at the crossing",
          abs(xs[1] - 20) < 1e-3 and s.GeometryCount == n, f"line x {xs}, geometry {n} -> {s.GeometryCount}")
    check("Trim tool: sketch still open after Escape", H.in_edit() is not None)
    H.grab("trimmed")


@step
def s_trim_undo():
    """Cmd-Z: expect the trimmed line back."""
    h, _v = S["trim"]
    Gui.runCommand("Std_Undo")
    refresh()
    g = sk().Geometry[h]
    xs = sorted([g.StartPoint.x, g.EndPoint.x])
    check("Undo restores the trimmed line", abs(xs[1] - 40) < 1e-3, xs)
    check("Undo keeps the sketch open", H.in_edit() is not None)


@step
def s_trim_python():
    """Trim from Python."""
    h, v = S["trim"]
    s = sk()
    s.trim(h, V(5, -20, 0))
    refresh()
    g = sk().Geometry[h]
    xs = sorted([g.StartPoint.x, g.EndPoint.x])
    check("Python trim() cuts the left half at the crossing", abs(xs[0] - 20) < 1e-3, xs)


@step
def s_extend_tool():
    """Extend tool: click a short line end, then the wall: expect it to reach the wall."""
    s = sk()
    short = s.addGeometry(Part.LineSegment(V(0, -50, 0), V(10, -50, 0)), False)
    wall = s.addGeometry(Part.LineSegment(V(30, -60, 0), V(30, -40, 0)), False)
    refresh()
    Gui.SendMsgToActiveView("ViewFit")
    H.pump(250)
    S["ext"] = (short, wall)
    Gui.runCommand("Sketcher_Extend")
    H.pump(250)
    H.click_vec(V(8, -50, 0))  # pick the end to extend
    note(f"extend first click/preselection: {S.get('last_presel')}")
    H.click_vec(V(30, -45, 0))  # pick the boundary
    note(f"extend second click/preselection: {S.get('last_presel')}")
    H.pump(300)
    H.escape()
    refresh()
    g = sk().Geometry[short]
    xs = sorted([g.StartPoint.x, g.EndPoint.x])
    check("Extend tool: line end extended to the boundary line", abs(xs[1] - 30) < 1e-3, xs)
    check("Extend tool: sketch still open after Escape", H.in_edit() is not None)
    H.grab("extended")


@step
def s_extend_python():
    """Extend from Python by 5 mm."""
    short, _wall = S["ext"]
    s = sk()
    g = s.Geometry[short]
    before = g.length()
    s.extend(short, 5.0, 1)  # 5 mm beyond the start
    refresh()
    after = sk().Geometry[short].length()
    check("Python extend() lengthens the line by 5 mm", abs(after - before - 5) < 1e-3, f"{before} -> {after}")


@step
def s_leave():
    """Click Leave Sketch in the Sketch toolbar group."""
    tb = H.toolbar("Edit Mode")
    acts = [a for n, a, w in H.toolbar_buttons(tb) if n == "Sketcher_LeaveSketch"] if tb else []
    check("Leave Sketch button present and enabled", acts and acts[0].isEnabled())
    if acts:
        acts[0].trigger()
    H.wait_for(lambda: H.in_edit() is None, 3000)
    return 800


@step
def s_after_leave():
    """Expect Design back and the sketch valid."""
    check("Leave Sketch closes the sketch", H.in_edit() is None)
    wb = Gui.activeWorkbench()
    check("back in Design workspace", wb.name() == "PartDesignWorkbench", wb.name())
    check("SKETCH tab removed", "Sketch" not in H.tab_names(), H.tab_names())
    S["doc"].recompute()
    check("sketch recomputes cleanly", "Invalid" not in sk().State, sk().State)


H.run()
