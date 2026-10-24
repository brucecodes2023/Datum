# SPDX-License-Identifier: LGPL-2.1-or-later
"""UI helpers for modelling the Leonardo parts the way a user does (commands, task panels, dialogs).

Every helper drives the same widgets a user would: toolbar commands with a selection, the task panel's
fields, its OK button, the dimension dialog. Where a user action cannot be driven (a click on an exact
model coordinate) the helper says so in the log as a workaround.
"""

import math
import os

import harness as H
from harness import App, Gui, QtCore, QtGui, QtWidgets, S, check, mw, note

V = App.Vector


def wait_dialog(open_=True, ms=4000):
    return H.wait_for(lambda: bool(Gui.Control.activeDialog()) == open_, ms)


def ok_dialog(what):
    """Press OK in the task panel (the user's click); report when OK is not on screen."""
    box = H.button_box()
    if box is None:
        check(f"{what}: task panel has OK", False, "no button box")
        return False
    H.check_buttons_on_screen(f"{what}: OK / Cancel visible on screen", box)
    H.press(box, QtWidgets.QDialogButtonBox.Ok)
    closed = wait_dialog(False, 5000)
    check(f"{what}: OK closes the task panel", closed)
    return closed


def task_widget(name, cls=None):
    tv = H.task_view()
    if tv is None:
        return None
    for w in tv.findChildren(QtWidgets.QWidget):
        if w.objectName() == name and (cls is None or cls in w.metaObject().className()):
            return w
    return None


def set_quantity(name, value):
    w = task_widget(name)
    if w is None:
        note(f"no task-panel field {name!r}")
        return False
    w.setProperty("rawValue", float(value))
    H.pump(150)
    return True


def set_combo(name, text):
    w = task_widget(name)
    if w is None or not isinstance(w, QtWidgets.QComboBox):
        note(f"no task-panel combo {name!r}")
        return False
    i = w.findText(text, QtCore.Qt.MatchContains)
    if i < 0:
        note(f"combo {name!r} has no {text!r}: {[w.itemText(k) for k in range(w.count())]}")
        return False
    w.setCurrentIndex(i)
    w.activated.emit(i)
    H.pump(200)
    return True


def zoom_out(notches=8):
    """Mouse-wheel zoom out over the 3D view, as a user does on a fresh sketch."""
    vp = H._gv().viewport()
    centre = QtCore.QPointF(vp.width() / 2, vp.height() / 2)
    for _i in range(notches):
        ev = QtGui.QWheelEvent(centre, QtCore.QPointF(vp.mapToGlobal(centre.toPoint())), QtCore.QPoint(0, 0),
                               QtCore.QPoint(0, -120), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                               QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(vp, ev)
        H.pump(30)


def view_width_mm(sk):
    """Width of the visible sketch plane in mm, measured with two screen points on the sketch."""
    a = H.screen_of(sk.getGlobalPlacement().multVec(V(0, 0, 0)))
    b = H.screen_of(sk.getGlobalPlacement().multVec(V(10, 0, 0)))
    px = abs(b.x() - a.x())
    return H._gv().viewport().width() * 10.0 / px if px else float("inf")


def make_visible(sk, need_mm):
    """Zoom out with the wheel until about need_mm of the sketch is in view (max 40 notches)."""
    start = view_width_mm(sk)
    for _i in range(3):
        if view_width_mm(sk) >= need_mm:
            break
        zoom_out(4)
    wheel = view_width_mm(sk)
    if wheel <= start * 1.01:
        note(f"mouse-wheel events did not zoom the view ({start:.2f} mm); using View > Zoom out instead")
    for _i in range(60):
        if view_width_mm(sk) >= need_mm:
            break
        Gui.runCommand("Std_ViewZoomOut")
        H.pump(30)
    note(f"sketch view: {start:.2f} mm wide at start, {view_width_mm(sk):.1f} mm after zooming out")
    return view_width_mm(sk)


# ----- sketching -----------------------------------------------------------------------------


def new_sketch(doc, body, plane=None, face=None):
    """Select a base plane (name like 'XY_Plane') or a face ('Pad.Face6') and click New Sketch."""
    Gui.Selection.clearSelection()
    if plane:
        feat = [f for f in body.Origin.OriginFeatures if f.Name.startswith(plane)][0]
        Gui.Selection.addSelection(doc.Name, feat.Name)
    else:
        Gui.Selection.addSelection(doc.Name, body.Name, face)
    Gui.runCommand("PartDesign_NewSketch")
    H.wait_for(lambda: H.in_edit() is not None, 5000)
    sk = H.in_edit()
    check(f"New Sketch on {plane or face} opens a sketch", sk is not None)
    return sk


def to_sketch(sk, x, y):
    return sk.getGlobalPlacement().multVec(V(x, y, 0))


def tool_clicks(sk, cmd, pts, expect):
    """Run a geometry tool and click the given sketch points; Escape ends it."""
    n = sk.GeometryCount
    Gui.runCommand(cmd)
    H.pump(250)
    for x, y in pts:
        H.click_vec(to_sketch(sk, x, y))
    H.pump(200)
    H.escape()
    got = sk.GeometryCount - n
    check(f"{cmd}: clicks draw {expect} geometr{'y' if expect == 1 else 'ies'}", got == expect, f"added {got}")
    return list(range(n, sk.GeometryCount))


def select(sk, *subs):
    Gui.Selection.clearSelection()
    for sub in subs:
        Gui.Selection.addSelection(sk.Document.Name, sk.Name, sub)
    H.pump(80)


def constrain(sk, cmd, *subs):
    n = sk.ConstraintCount
    select(sk, *subs)
    Gui.runCommand(cmd)
    H.pump(200)
    ok = sk.ConstraintCount > n
    check(f"{cmd} on {subs}", ok, f"constraints {n} -> {sk.ConstraintCount}")
    Gui.Selection.clearSelection()
    return ok


def dimension(sk, cmd, subs, value):
    """Select, run a dimension command, type the value in the dialog, OK (as a user)."""
    result = {}

    def answer():
        dlgs = [w for w in QtWidgets.QApplication.topLevelWidgets()
                if isinstance(w, QtWidgets.QDialog) and w.isVisible()]
        result["dialog"] = bool(dlgs)
        if not dlgs:
            return
        d = dlgs[0]
        spins = [w for w in d.findChildren(QtWidgets.QWidget) if "QuantitySpinBox" in w.metaObject().className()]
        if spins:
            spins[0].setProperty("rawValue", float(value))
            H.pump(50)
        boxes = d.findChildren(QtWidgets.QDialogButtonBox)
        if not (boxes and H.press(boxes[0], QtWidgets.QDialogButtonBox.Ok)):
            d.accept()

    n = sk.ConstraintCount
    select(sk, *subs)
    S["expect_dialog"] = True
    QtCore.QTimer.singleShot(700, answer)
    Gui.runCommand(cmd)
    S["expect_dialog"] = False
    H.pump(200)
    sk.Document.recompute()
    ok = sk.ConstraintCount > n and abs(sk.Constraints[-1].Value - value) < 1e-6
    check(f"{cmd} {value} on {subs} via the dimension dialog", ok and result.get("dialog"),
          f"dialog={result.get('dialog')} constraints {n} -> {sk.ConstraintCount}")
    Gui.Selection.clearSelection()
    return ok


def message(sk=None):
    tv = H.task_view()
    for w in tv.findChildren(QtWidgets.QWidget) if tv else []:
        if w.metaObject().className() == "SketcherGui::TaskSketcherMessages":
            return " ".join(l.text() for l in w.findChildren(QtWidgets.QLabel) if l.isVisible() and l.text())
    return ""


def finish_sketch():
    bar = H.workspace_bar()
    if bar is not None and bar.finish.isVisible():
        bar.finish.click()
    else:
        Gui.runCommand("Sketcher_LeaveSketch")
    H.wait_for(lambda: H.in_edit() is None, 4000)
    check("FINISH SKETCH closes the sketch", H.in_edit() is None)


# ----- features -----------------------------------------------------------------------------


def _select_sketch(body, sk):
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(sk.Document.Name, body.Name, sk.Name + ".")


def _new_feature(body, cmd, type_id, before):
    after = [o for o in body.Group if o.TypeId == type_id and o not in before]
    check(f"{cmd} creates a {type_id.split('::')[1]}", bool(after))
    return after[0] if after else None


def pad(body, sk, length, what="Pad"):
    before = list(body.Group)
    _select_sketch(body, sk)
    Gui.runCommand("PartDesign_Pad")
    check(f"{what}: Pad task panel opens", wait_dialog(True))
    set_quantity("lengthEdit", length)
    H.grab(f"{what.replace(' ', '-').lower()}-panel")
    ok_dialog(what)
    feat = _new_feature(body, "PartDesign_Pad", "PartDesign::Pad", before)
    if feat is not None:
        check(f"{what}: length {length} mm, valid", abs(feat.Length.Value - length) < 1e-6 and feat.isValid(),
              f"{feat.Length} {feat.State}")
    return feat


def pocket(body, sk, depth=None, through=False, what="Pocket", reverse=False):
    before = list(body.Group)
    _select_sketch(body, sk)
    Gui.runCommand("PartDesign_Pocket")
    check(f"{what}: Pocket task panel opens", wait_dialog(True))
    if through:
        set_combo("changeMode", "Through all")
    elif depth is not None:
        set_quantity("lengthEdit", depth)
    if reverse:
        cb = task_widget("checkBoxReversed")
        if cb is not None:
            cb.setChecked(True)
            H.pump(200)
    H.grab(f"{what.replace(' ', '-').lower()}-panel")
    ok_dialog(what)
    feat = _new_feature(body, "PartDesign_Pocket", "PartDesign::Pocket", before)
    if feat is not None:
        check(f"{what}: valid", feat.isValid() and "Invalid" not in feat.State, feat.State)
    return feat


def hole(body, sk, what="Hole", size="M3", cut=None, threaded=False, through=True, depth=None):
    """PartDesign Hole on the sketch's circles / points via its task panel."""
    before = list(body.Group)
    _select_sketch(body, sk)
    Gui.runCommand("PartDesign_Hole")
    check(f"{what}: Hole task panel opens", wait_dialog(True))
    H.pump(300)
    set_combo("ThreadType", "ISO metric regular")
    set_combo("ThreadSize", size)
    if threaded:
        tb = task_widget("Threaded")
        if tb is not None and hasattr(tb, "setChecked"):
            tb.setChecked(True)
        else:
            set_combo("HoleType", "Tapped")
        H.pump(200)
    if cut:
        set_combo("HoleCutType", cut)
    if through:
        set_combo("DepthType", "Through all")
    elif depth is not None:
        set_combo("DepthType", "Dimension")
        set_quantity("Depth", depth)
    H.grab(f"{what.replace(' ', '-').lower()}-panel")
    H.check_readable(f"{what}: Hole task panel readable", H.task_view())
    ok_dialog(what)
    feat = _new_feature(body, "PartDesign_Hole", "PartDesign::Hole", before)
    if feat is not None:
        check(f"{what}: valid", feat.isValid() and "Invalid" not in feat.State, feat.State)
        note(f"{what}: size {feat.ThreadSize} cut {feat.HoleCutType} threaded {feat.Threaded} "
             f"depth {feat.DepthType} dia {feat.Diameter}")
    return feat


def polar(body, feature, count, what="Circular pattern"):
    """Select a feature, click Circular Pattern (BruceWorx), set the count, OK."""
    before = list(body.Group)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(feature.Document.Name, body.Name, feature.Name + ".")
    cmd = "BruceWorx_CircularPattern" if H.action_for("BruceWorx_CircularPattern") else "PartDesign_PolarPattern"
    Gui.runCommand(cmd)
    check(f"{what}: {cmd} opens a task panel", wait_dialog(True))
    H.pump(400)
    spins = [w for w in H.task_view().findChildren(QtWidgets.QAbstractSpinBox) if w.isVisible()]
    note(f"{what}: spin boxes {[(w.objectName(), w.metaObject().className(), w.text()) for w in spins]}")
    occ = [w for w in spins if "ccurrence" in w.objectName() or "ount" in w.objectName()]
    if occ:
        # Type the number as a user does (UIntSpinBox maps its value; setValue(int) would mean ~2 billion).
        QTest = H._qtest()
        w = occ[0]
        w.setFocus()
        w.selectAll()
        QTest.keyClicks(w, str(count))
        QTest.keyClick(w, QtCore.Qt.Key_Return)
        H.pump(600)
    else:
        check(f"{what}: occurrences field found", False, [w.objectName() for w in spins])
    H.grab(f"{what.replace(' ', '-').lower()}-panel")
    ok_dialog(what)
    feat = _new_feature(body, cmd, "PartDesign::PolarPattern", before)
    if feat is not None:
        occ_val = getattr(feat, "Occurrences", None)
        check(f"{what}: {count} instances, valid", occ_val == count and feat.isValid(), f"{occ_val} {feat.State}")
    return feat


def top_face(feature, z=None, normal_z=1):
    """Name ('Pad.FaceN') of the planar face with the highest (or given) z facing +z / -z."""
    shape = feature.Shape
    best, best_z = None, None
    for i, f in enumerate(shape.Faces):
        if f.Surface.__class__.__name__ != "Plane":
            continue
        n = f.normalAt(0, 0)
        if abs(n.z - normal_z) > 1e-6:
            continue
        cz = f.CenterOfMass.z
        if z is not None and abs(cz - z) > 1e-6:
            continue
        if best is None or (cz > best_z if normal_z > 0 else cz < best_z):
            best, best_z = i, cz
    return f"{feature.Name}.Face{best + 1}" if best is not None else None


# ----- vault ----------------------------------------------------------------------------------


def vault():
    try:
        import bruceworx_vaultclient as vc

        return vc, vc.session()
    except Exception as exc:
        note(f"vault client: {exc}")
        return None, None
