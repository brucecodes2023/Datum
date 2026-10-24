# SPDX-License-Identifier: LGPL-2.1-or-later
"""Every common joint through the joint dialog with face/edge/vertex picks, then solve.

Fixed, Revolute, Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, Angle.
"""

import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, V  # noqa: E402
from scene import Scene, make_joint, normal, angle_deg  # noqa: E402

TOL = 1e-3


def steps():
    import bruceworx_workspaces as bw

    bw.activate_workspace("Design")
    yield 800
    s = Scene(L.WORK, [
        ("Pin1", "Pin", (150, 0, 0)), ("Pin2", "Pin", (150, 40, 0)),
        ("BlockF", "Block", (-60, 0, 0)), ("BlockS", "Block", (-60, 40, 0)),
        ("BlockB", "Block", (-60, 80, 0)), ("BlockD", "Block", (0, 120, 0)),
        ("BlockP", "Block", (40, 120, 0)), ("BlockQ", "Block", (80, 120, 0)),
        ("BlockA", "Block", (120, 120, 0)),
    ])
    asm = s.asm
    asm.ViewObject.doubleClicked()
    yield 1000
    import UtilsAssembly

    L.check("assembly active for joints", UtilsAssembly.activeAssembly() is asm)
    L.check("Base is grounded", asm.isPartConnected(s.c["Base"]))
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    L.expand_trees()

    # Elements in each part's own frame
    top = s.face("Base", lambda f: abs(f.CenterOfMass.z - 10) < 1e-6 and normal(f).z > 0.9)
    side = s.face("Base", lambda f: abs(f.CenterOfMass.x - 100) < 1e-6 and normal(f).x > 0.9)
    hole1_top = s.edge("Base", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.x - 25) < 1e-6
                       and abs(e.Curve.Center.z - 10) < 1e-6)
    hole2 = s.face("Base", lambda f: f.Surface.__class__.__name__ == "Cylinder" and abs(f.Surface.Center.x - 75) < 1e-6)
    pin_bottom = s.edge("Pin1", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.z) < 1e-6)
    pin_side = s.face("Pin1", lambda f: f.Surface.__class__.__name__ == "Cylinder")
    blk_bottom = s.face("BlockF", lambda f: abs(f.CenterOfMass.z) < 1e-6 and normal(f).z < -0.9)
    blk_top = s.face("BlockF", lambda f: abs(f.CenterOfMass.z - 10) < 1e-6 and normal(f).z > 0.9)
    blk_side = s.face("BlockF", lambda f: abs(f.CenterOfMass.x) < 1e-6 and normal(f).x < -0.9)
    blk_v = s.vertex("BlockF", V(0, 0, 0))
    base_v = s.vertex("Base", V(100, 60, 10))
    L.say("elements", top, side, hole1_top, hole2, pin_bottom, pin_side, blk_bottom, blk_top, blk_side, blk_v, base_v)
    L.check("test geometry found", all((top, side, hole1_top, hole2, pin_bottom, pin_side, blk_bottom, blk_top,
                                         blk_side, blk_v, base_v)))
    base_top_pt = V(60, 20, 10)

    def axis_of(name):
        return s.c[name].Placement.Rotation.multVec(V(0, 0, 1))

    def solved(joint, label):
        res = asm.solve()
        L.check(f"{label}: solve succeeds", res == 0, res)
        bad = [o.Name for o in s.doc.Objects if "Invalid" in o.State]
        L.check(f"{label}: nothing in error", not bad and "Invalid" not in joint.State, (bad, joint.State))

    # Revolute: pin bottom edge on the first hole's top edge
    L.tell("Revolute: pin bottom edge on the first hole's top edge")
    j = yield from make_joint(s, "Assembly_CreateJointRevolute", ("Pin1", pin_bottom, V(5, 0, 0)),
                              ("Base", hole1_top, V(30, 30, 10)), shot="revolute")
    if j:
        L.check("Revolute: type", j.JointType == "Revolute", j.JointType)
        solved(j, "Revolute")
        p = s.c["Pin1"].Placement.Base
        L.check("Revolute: pin on the hole axis", abs(p.x - 25) < TOL and abs(p.y - 30) < TOL, p)
        L.check("Revolute: pin axis vertical", abs(abs(axis_of("Pin1").z) - 1) < TOL, axis_of("Pin1"))
        L.check("Revolute: Browser row for the joint", bool(L.tree_items(j.Label)), j.Label)

    # Cylindrical: pin side face in the second hole
    L.tell("Cylindrical: pin side face in the second hole")
    j = yield from make_joint(s, "Assembly_CreateJointCylindrical", ("Pin2", pin_side, V(5, 0, 15)),
                              ("Base", hole2, V(80, 30, 5)))
    if j:
        L.check("Cylindrical: type", j.JointType == "Cylindrical", j.JointType)
        solved(j, "Cylindrical")
        p = s.c["Pin2"].Placement.Base
        L.check("Cylindrical: pin on the second hole axis", abs(p.x - 75) < TOL and abs(p.y - 30) < TOL, p)
        L.check("Cylindrical: pin axis vertical", abs(abs(axis_of("Pin2").z) - 1) < TOL, axis_of("Pin2"))

    # Fixed: block bottom on the plate top
    L.tell("Fixed: block bottom on the plate top")
    j = yield from make_joint(s, "Assembly_CreateJointFixed", ("BlockF", blk_bottom, V(10, 10, 0)),
                              ("Base", top, base_top_pt), shot="fixed")
    if j:
        solved(j, "Fixed")
        f = s.global_element("BlockF", blk_bottom)
        L.check("Fixed: block bottom on the plate top (z = 10)", abs(f.CenterOfMass.z - 10) < TOL, f.CenterOfMass)
        L.check("Fixed: faces face each other", normal(f).z < -0.999, normal(f))

    # Slider: block bottom on the plate top, sliding along the face normal
    L.tell("Slider: block bottom on the plate top, sliding along the face normal")
    j = yield from make_joint(s, "Assembly_CreateJointSlider", ("BlockS", blk_bottom, V(10, 10, 0)),
                              ("Base", top, V(60, 40, 10)))
    if j:
        solved(j, "Slider")
        f = s.global_element("BlockS", blk_bottom)
        L.check("Slider: block square to the plate", abs(abs(normal(f).z) - 1) < TOL, normal(f))

    # Ball: block corner on the plate corner
    L.tell("Ball: block corner on the plate corner")
    j = yield from make_joint(s, "Assembly_CreateJointBall", ("BlockB", blk_v, V(0, 0, 0)),
                              ("Base", base_v, V(100, 60, 10)))
    if j:
        solved(j, "Ball")
        pt = s.global_element("BlockB", blk_v).Point
        L.check("Ball: corners meet", (pt - V(100, 60, 10)).Length < TOL, pt)

    # Distance 5 mm between block bottom and plate top
    L.tell("Distance 5 mm between block bottom and plate top")
    def set_distance(task):
        task.jForm.distanceSpinbox.setProperty("rawValue", 5.0)

    j = yield from make_joint(s, "Assembly_CreateJointDistance", ("BlockD", blk_bottom, V(10, 10, 0)),
                              ("Base", top, V(30, 50, 10)), setup=set_distance, shot="distance")
    if j:
        L.check("Distance: 5 mm stored", abs(j.Distance.Value - 5) < 1e-9, j.Distance)
        solved(j, "Distance")
        f = s.global_element("BlockD", blk_bottom)
        L.check("Distance: faces parallel", abs(abs(normal(f).z) - 1) < TOL, normal(f))
        L.check("Distance: 5 mm apart", abs(abs(f.CenterOfMass.z - 10) - 5) < TOL, f.CenterOfMass.z)

    # Parallel: block top parallel to plate top
    L.tell("Parallel: block top parallel to plate top")
    j = yield from make_joint(s, "Assembly_CreateJointParallel", ("BlockP", blk_top, V(10, 10, 10)),
                              ("Base", top, V(50, 50, 10)))
    if j:
        solved(j, "Parallel")
        n = normal(s.global_element("BlockP", blk_top))
        L.check("Parallel: faces parallel", abs(abs(n.z) - 1) < TOL, n)

    # Perpendicular: block side face square to the plate top
    L.tell("Perpendicular: block side face square to the plate top")
    j = yield from make_joint(s, "Assembly_CreateJointPerpendicular", ("BlockQ", blk_side, V(0, 10, 5)),
                              ("Base", top, V(70, 50, 10)))
    if j:
        solved(j, "Perpendicular")
        n = normal(s.global_element("BlockQ", blk_side))
        L.check("Perpendicular: side face square to the top", abs(n.z) < TOL, n)

    # Angle 30 degrees between block top and plate top
    L.tell("Angle 30 degrees between block top and plate top")
    def set_angle(task):
        task.jForm.angleSpinbox.setProperty("rawValue", 30.0)

    j = yield from make_joint(s, "Assembly_CreateJointAngle", ("BlockA", blk_top, V(10, 10, 10)),
                              ("Base", top, V(90, 50, 10)), setup=set_angle, shot="angle")
    if j:
        L.check("Angle: 30 deg stored", abs(j.Angle.Value - 30) < 1e-9, j.Angle)
        solved(j, "Angle")
        a = angle_deg(normal(s.global_element("BlockA", blk_top)), V(0, 0, 1))
        L.check("Angle: faces at 30 deg", abs(a - 30) < 0.01 or abs(a - 150) < 0.01, a)

        # Undo / redo of a joint
        L.tell("Undo / redo of a joint")
        n = len(s.joints())
        s.doc.undo()
        yield 600
        L.check("Undo removes the last joint", len(s.joints()) == n - 1, len(s.joints()))
        s.doc.redo()
        yield 600
        L.check("Redo brings it back", len(s.joints()) == n, len(s.joints()))

    # Pressing OK with one pick: the dialog stays and warns.
    L.tell("Pressing OK with one pick: the dialog stays and warns.")
    import JointObject as J

    n = len(s.joints())
    L.trigger("Assembly_CreateJointFixed")
    yield 900
    s.pick("BlockF", blk_top, V(10, 10, 10))
    yield 400
    L.press("OK")
    yield 700
    L.check("OK with one pick keeps the dialog open", J.activeTask is not None and Gui.Control.activeDialog())
    L.press("Cancel")
    yield 700
    L.check("Cancel leaves no joint behind", len(s.joints()) == n and not Gui.Control.activeDialog(),
            len(s.joints()))

    res = asm.solve()
    L.check("whole assembly solves", res == 0, res)
    s.doc.save()
    L.expand_trees()
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    yield 600
    L.grab_window("joints")
    L.grab_browser("joints-browser")
    yield 200


L.run(steps)
