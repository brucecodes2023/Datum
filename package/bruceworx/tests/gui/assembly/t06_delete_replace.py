# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deleting and replacing components: what happens to their joints (refMissing), undo,
deleting a joint, and a broken joint through save/reopen."""

import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, QtWidgets, V  # noqa: E402
from scene import Scene, make_joint, normal  # noqa: E402


def broken(j):
    return "Invalid" in j.State


def steps():
    import bruceworx_workspaces as bw

    bw.activate_workspace("Design")
    yield 800
    s = Scene(L.WORK, [("Pin1", "Pin", (150, 0, 0)), ("BlockF", "Block", (-60, 0, 0)),
                       ("BlockG", "Block", (-60, 60, 0))])
    asm, doc = s.asm, s.doc
    asm.ViewObject.doubleClicked()
    yield 1000
    top = s.face("Base", lambda f: abs(f.CenterOfMass.z - 10) < 1e-6 and normal(f).z > 0.9)
    hole1_top = s.edge("Base", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.x - 25) < 1e-6
                       and abs(e.Curve.Center.z - 10) < 1e-6)
    pin_bottom = s.edge("Pin1", lambda e: hasattr(e.Curve, "Radius") and abs(e.Curve.Center.z) < 1e-6)
    blk_bottom = s.face("BlockF", lambda f: abs(f.CenterOfMass.z) < 1e-6 and normal(f).z < -0.9)
    rev = yield from make_joint(s, "Assembly_CreateJointRevolute", ("Pin1", pin_bottom, V(5, 0, 0)),
                                ("Base", hole1_top, V(30, 30, 10)))
    fix = yield from make_joint(s, "Assembly_CreateJointFixed", ("BlockF", blk_bottom, V(10, 10, 0)),
                                ("Base", top, V(60, 20, 10)))
    fix2 = yield from make_joint(s, "Assembly_CreateJointFixed", ("BlockG", blk_bottom, V(10, 10, 0)),
                                 ("Base", top, V(60, 45, 10)))
    if not (rev and fix and fix2):
        return
    L.check("start: all joints healthy", not any(broken(j) for j in s.joints()),
            [(j.Name, j.State) for j in s.joints()])
    names = {"rev": rev.Name, "fix": fix.Name, "fix2": fix2.Name}

    # ---- Delete a component from the Browser (Std_Delete): its joints go with it
    L.tell("Delete a component from the Browser (Std_Delete): its joints go with it")
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Pin1")
    modals = []
    L.answer_modal("Yes", name="delete-confirm", log_to=modals)
    Gui.runCommand("Std_Delete")
    yield 1200
    L.say("delete modals", modals)
    L.check("Delete removes the component", doc.getObject("Pin1") is None)
    L.check("Delete also removes the component's joint (no dangling joint)", doc.getObject(names["rev"]) is None,
            [j.Name for j in s.joints()])
    L.check("other joints untouched", not broken(doc.getObject(names["fix"])))
    doc.undo()
    yield 1000
    doc.recompute()
    rev = doc.getObject(names["rev"])
    L.check("Undo brings back the component and its joint", doc.getObject("Pin1") is not None and rev is not None)
    if rev is not None:
        L.check("restored joint is healthy", not broken(rev), rev.State)
    L.check("solve after undo", asm.solve() == 0)

    # ---- Removed by a script / pattern (doc.removeObject): the joint stays, marked broken
    L.tell("Removed by a script / pattern (doc.removeObject): the joint stays, marked broken")
    before = L.report_text()
    p_pin = App.Placement(s.c["Pin1"].Placement)
    doc.removeObject("Pin1")
    doc.recompute()
    yield 800
    rev = doc.getObject(names["rev"])
    msg = L.report_text()[len(before):]
    L.check("removed by script: joint kept", rev is not None)
    if rev is not None:
        L.check("removed by script: joint marked broken", broken(rev), rev.State)
        L.check("Report view names the missing part", "Pin1" in msg and "Broken" in msg,
                msg.strip().replace("\n", " | ")[:200])
    res = asm.solve()
    L.check("solve with a broken joint does not crash", res is not None, res)
    L.expand_trees()
    yield 500
    L.grab_browser("broken-joint-browser")
    doc.undo()
    yield 800
    doc.recompute()
    rev = doc.getObject(names["rev"])
    L.check("undo: part back, joint heals", doc.getObject("Pin1") is not None and rev is not None and not broken(rev),
            rev and rev.State)

    # ---- Replace a component's part (LinkedObject Block -> Pin): the Block face is gone
    L.tell("Replace a component's part (LinkedObject Block -> Pin): the Block face is gone")
    blk = doc.getObject("BlockF")
    fix = doc.getObject(names["fix"])
    before = L.report_text()
    blk.LinkedObject = s.part_docs["Pin"].getObject("Pin")
    doc.recompute()
    yield 800
    msg = L.report_text()[len(before):]
    L.check("replace: joint on the replaced part marked broken", broken(fix), fix.State)
    L.check("replace: Report view names the missing element", "BlockF" in msg,
            msg.strip().replace("\n", " | ")[:200])
    L.check("replace: the other joints stay healthy",
            not broken(doc.getObject(names["fix2"])) and not broken(doc.getObject(names["rev"])))
    L.grab_window("replaced")
    blk.LinkedObject = s.part_docs["Block"].getObject("Block")
    doc.recompute()
    yield 600
    L.check("replace back: joint heals", not broken(fix), fix.State)
    L.check("replace back: solves", asm.solve() == 0)

    # ---- Delete a joint: the part stays where it is
    L.tell("Delete a joint: the part stays where it is")
    g = doc.getObject("BlockG")
    g0 = App.Placement(g.Placement)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, names["fix2"])
    L.answer_modal("Yes", name="delete-joint-confirm")
    Gui.runCommand("Std_Delete")
    yield 1000
    L.check("joint deleted", doc.getObject(names["fix2"]) is None)
    L.check("part stays where the joint put it", g.Placement.isSame(g0, 1e-6), g.Placement)
    L.check("part not deleted with its joint", doc.getObject("BlockG") is not None)

    # ---- A broken joint survives save and reopen
    L.tell("A broken joint survives save and reopen")
    doc.removeObject("Pin1")
    doc.recompute()
    doc.save()
    path = doc.FileName
    Gui.ActiveDocument.resetEdit()
    yield 300
    App.closeDocument(doc.Name)
    yield 500
    doc = App.openDocument(path)
    yield 1200
    rev = doc.getObject(names["rev"])
    L.check("reopen: broken joint still there and still marked", rev is not None and broken(rev),
            rev and rev.State)
    bad = [o.Name for o in doc.Objects if "Invalid" in o.State and o.Name != names["rev"]]
    L.check("reopen: nothing else in error", not bad, bad)
    L.check("reopen: still solves", doc.getObject("Assembly").solve() == 0)


L.run(steps)
