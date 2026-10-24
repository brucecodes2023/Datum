# SPDX-License-Identifier: LGPL-2.1-or-later
"""Joint limits set in the joint dialog, dragging (drag API) against them, keep parts in place."""

import math
import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, V  # noqa: E402
from scene import Scene, make_joint, normal  # noqa: E402


def steps():
    import bruceworx_workspaces as bw
    import JointObject as J
    import UtilsAssembly as U

    bw.activate_workspace("Design")
    yield 800
    s = Scene(L.WORK, [("Pin1", "Pin", (150, 0, 0)), ("BlockS", "Block", (-60, 40, 0)),
                       ("BlockK", "Block", (-60, 0, 0))])
    asm = s.asm
    asm.ViewObject.doubleClicked()
    yield 1000
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    top = s.face("Base", lambda f: abs(f.CenterOfMass.z - 10) < 1e-6 and normal(f).z > 0.9)
    hole1_top = s.edge("Base", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.x - 25) < 1e-6
                       and abs(e.Curve.Center.z - 10) < 1e-6)
    pin_bottom = s.edge("Pin1", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.z) < 1e-6)
    blk_bottom = s.face("BlockS", lambda f: abs(f.CenterOfMass.z) < 1e-6 and normal(f).z < -0.9)

    def drag(part, joint, move, targets):
        asm.preDrag([part])
        refused = 0
        for t in targets:
            move(t)
            if not asm.doDragStep():
                refused += 1
        asm.postDrag()
        return refused

    # ---- Revolute with angle limits
    L.tell("Revolute with angle limits")
    rev = yield from make_joint(s, "Assembly_CreateJointRevolute", ("Pin1", pin_bottom, V(5, 0, 0)),
                                ("Base", hole1_top, V(30, 30, 10)))
    if rev is None:
        return
    pin = s.c["Pin1"]

    def jcs():
        return math.degrees(U.getJointXYAngle(rev))

    def turn_by(degrees):
        plc1 = U.getJcsGlobalPlc(rev.Placement1, rev.Reference1)
        axis = plc1.Rotation.multVec(V(0, 0, 1))
        pin.Placement = App.Placement(V(), App.Rotation(axis, degrees), plc1.Base) * pin.Placement

    def turn(target):
        """Turn the pin so the joint reads `target` (jcs angle)."""
        turn_by(target - jcs())

    start = jcs()
    refused = drag(pin, rev, turn, [start + a for a in range(20, 380, 20)])
    end = jcs()
    L.check("no limits: the pin turns all the way round", refused == 0 and
            abs(((end - start) + 180) % 360 - 180) < 0.01, (start, end, refused))

    rev.ViewObject.doubleClicked()
    yield 1000
    task = J.activeTask
    L.check("double-click opens the joint for editing", task is not None and not task.creating)
    if task is None:
        return
    f = task.jForm
    L.check("Revolute edit: angle limit boxes shown",
            f.limitCheckbox3.isVisible() and f.limitCheckbox4.isVisible(),
            (f.limitCheckbox3.text(), f.limitCheckbox4.text()))
    L.check("Revolute edit: no length limit boxes",
            not f.limitCheckbox1.isVisible() and not f.limitCheckbox2.isVisible())
    L.check("Revolute edit: angle spin boxes greyed until ticked",
            not f.limitRotMinSpinbox.isEnabled() and not f.limitRotMaxSpinbox.isEnabled())
    f.limitCheckbox3.setChecked(True)
    f.limitCheckbox4.setChecked(True)
    yield 300
    L.check("ticking enables the angle spin boxes", f.limitRotMinSpinbox.isEnabled() and f.limitRotMaxSpinbox.isEnabled())
    f.limitRotMinSpinbox.setProperty("rawValue", 0.0)
    f.limitRotMaxSpinbox.setProperty("rawValue", 90.0)
    yield 300
    L.check("keep-in-place box offered", f.keepPositionCheckbox.isVisible(), f.keepPositionCheckbox.text())
    L.grab_task("revolute-limits")
    L.press("OK")
    yield 1000
    L.check("limits stored", rev.EnableAngleMin and rev.EnableAngleMax and abs(rev.AngleMin.Value) < 1e-9
            and abs(rev.AngleMax.Value - 90) < 1e-9, (rev.EnableAngleMin, rev.EnableAngleMax, rev.AngleMin, rev.AngleMax))
    info = L.row_info(rev)
    L.check("Browser shows the angle range on the joint row", "90" in str(info), info)

    def reading():
        return J.limitReading(rev)[0]

    # Which way a turn of the pin moves the reading (the pin is the joint's first part).
    r0 = reading()
    turn_by(1.0)
    sign = 1.0 if reading() > r0 else -1.0
    turn_by(-1.0)

    def to_reading(target):
        turn_by(sign * (target - reading()))

    to_reading(45)
    asm.solve()
    L.say("start reading", reading(), "sign", sign)
    L.check("pin turned inside the limits", abs(reading() - 45) < 0.5, reading())
    drag(pin, rev, to_reading, range(50, 141, 10))
    L.check("drag stops at the 90 deg limit", abs(reading() - 90) < 0.5, reading())
    drag(pin, rev, to_reading, range(80, -61, -20))
    L.check("drag stops at the 0 deg limit", abs(reading()) < 0.5, reading())

    # ---- Slider with length limits
    L.tell("Slider with length limits")
    sl = yield from make_joint(s, "Assembly_CreateJointSlider", ("BlockS", blk_bottom, V(10, 10, 0)),
                               ("Base", top, V(60, 40, 10)))
    if sl is None:
        return
    blk = s.c["BlockS"]
    sl.ViewObject.doubleClicked()
    yield 1000
    task = J.activeTask
    f = task.jForm
    L.check("Slider edit: length limit boxes shown, no angle ones",
            f.limitCheckbox1.isVisible() and f.limitCheckbox2.isVisible() and not f.limitCheckbox3.isVisible())
    f.limitCheckbox1.setChecked(True)
    f.limitCheckbox2.setChecked(True)
    yield 300
    f.limitLenMinSpinbox.setProperty("rawValue", 0.0)
    f.limitLenMaxSpinbox.setProperty("rawValue", 15.0)
    yield 300
    L.grab_task("slider-limits")
    L.press("OK")
    yield 1000
    L.check("slider limits stored", sl.EnableLengthMin and sl.EnableLengthMax and abs(sl.LengthMax.Value - 15) < 1e-9,
            (sl.EnableLengthMin, sl.EnableLengthMax, sl.LengthMin, sl.LengthMax))

    def length():
        return J.limitReading(sl)[1]

    def slide_by(mm):
        plc1 = U.getJcsGlobalPlc(sl.Placement1, sl.Reference1)
        axis = plc1.Rotation.multVec(V(0, 0, 1))
        blk.Placement = App.Placement(axis * mm, App.Rotation()) * blk.Placement

    l0 = length()
    slide_by(1.0)
    lsign = 1.0 if length() > l0 else -1.0
    slide_by(-1.0)

    def slide(target):
        slide_by(lsign * (target - length()))

    slide(5)
    asm.solve()
    drag(blk, sl, slide, [6 + 2 * i for i in range(13)])
    L.check("slider drag stops at 15 mm", abs(length() - 15) < 0.05, length())
    drag(blk, sl, slide, [14 - 2 * i for i in range(13)])
    L.check("slider drag stops at 0 mm", abs(length()) < 0.05, length())

    # ---- Keep parts in place: a Fixed joint that moves nothing
    L.tell("Keep parts in place: a Fixed joint that moves nothing")
    kb = s.c["BlockK"]
    k0 = App.Placement(kb.Placement)
    before = {j.Name for j in s.joints()}
    L.trigger("Assembly_CreateJointFixed")
    yield 900
    task = J.activeTask
    s.pick("BlockK", blk_bottom, V(10, 10, 0))
    yield 300
    s.pick("Base", top, V(20, 20, 10))
    yield 600
    moved = not kb.Placement.isSame(k0, 1e-6)
    L.check("Fixed without keep-in-place moves the block while picking", moved)
    task.jForm.keepPositionCheckbox.setChecked(True)
    yield 600
    L.check("ticking keep-in-place puts the block back", kb.Placement.isSame(k0, 1e-4), kb.Placement)
    L.grab_task("keep-in-place")
    L.press("OK")
    yield 1000
    new = [j for j in s.joints() if j.Name not in before]
    kj = new[0] if new else None
    L.check("keep-in-place joint made, KeepPosition stored", kj is not None and kj.KeepPosition)
    L.check("after OK the block is where it was", kb.Placement.isSame(k0, 1e-4), kb.Placement)
    res = asm.solve()
    L.check("after solve the block is still where it was", res == 0 and kb.Placement.isSame(k0, 1e-4),
            (res, kb.Placement))
    s.doc.save()
    path = s.doc.FileName
    names = (asm.Name, rev.Name)
    Gui.ActiveDocument.resetEdit()
    yield 300
    App.closeDocument(s.doc.Name)
    yield 500
    doc = App.openDocument(path)
    yield 1200
    asm = doc.getObject(names[0])
    kb = doc.getObject("BlockK")
    rev = doc.getObject(names[1])
    L.check("reopen: block still in place", kb.Placement.isSame(k0, 1e-4), kb.Placement)
    L.check("reopen: revolute limits kept", rev.EnableAngleMax and abs(rev.AngleMax.Value - 90) < 1e-9)
    res = asm.solve()
    L.check("reopen: solve keeps the block in place", res == 0 and kb.Placement.isSame(k0, 1e-4), (res, kb.Placement))
    asm.ViewObject.doubleClicked()
    yield 600
    L.expand_trees()
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    yield 500
    L.grab_window("limits")


L.run(steps)
