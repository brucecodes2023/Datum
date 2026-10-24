# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketch on a face of a solid, external geometry, and using the sketch.

A padded 40 x 30 x 10 block: select its top face -> New Sketch -> the sketch
sits on the face (attachment, height, view normal to it, SKETCH tab) ->
project an edge of the face with the Projection tool (mouse click) and one
from Python (external geometry) -> a circle constrained to the projected edge
-> Finish Sketch -> Pocket with the toolbar command and OK in its task panel.
Also: the Properties tab of the right panel shows the selected sketch.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
from harness import App, Gui, QtCore, QtWidgets, S, check, mw, note, step  # noqa: E402

import Part  # noqa: E402
import Sketcher  # noqa: E402

H.set_script("sketch_face")
V = App.Vector


def ext_count(sk):
    return sum(len(subs) for _o, subs in sk.ExternalGeometry)


@step
def s_setup():
    """Pad a 40 x 30 x 10 block."""
    import bruceworx_workspaces

    bruceworx_workspaces.activate_workspace("Design")
    doc = App.newDocument("SketchFace")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
    xy = [f for f in body.Origin.OriginFeatures if f.Name.startswith("XY_Plane")][0]
    sk.AttachmentSupport = [(xy, "")]
    sk.MapMode = "FlatFace"
    pts = [V(0, 0, 0), V(40, 0, 0), V(40, 30, 0), V(0, 30, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    sk.Visibility = False
    doc.recompute()
    S.update(doc=doc, body=body, pad=pad)
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    shape = pad.Shape
    top = max(range(len(shape.Faces)), key=lambda i: shape.Faces[i].CenterOfMass.z)
    S["top"] = "Face%d" % (top + 1)
    check("block padded", pad.isValid() and abs(shape.Volume - 12000) < 1e-3, shape.Volume)
    return 600


@step
def s_select_face():
    """Select the top face and click New Sketch."""
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, S["body"].Name, "Pad." + S["top"])
    sel = Gui.Selection.getSelectionEx("", 0)
    note(f"selection: {[(s.ObjectName, s.SubElementNames) for s in sel]}")
    H.grab("face-selected")
    Gui.runCommand("PartDesign_NewSketch")
    H.wait_for(lambda: H.in_edit() is not None, 4000)
    return 1200


@step
def s_on_face():
    """Expect the sketch on the top face at z = 10, view normal to it."""
    obj = H.in_edit()
    check("New Sketch on a selected face opens the sketch", obj is not None and obj.TypeId == "Sketcher::SketchObject")
    if obj is None:
        # A plane / face chooser may have opened instead: report and stop here.
        note(f"task panel texts: {H.labels_text(H.task_view()) if H.task_view() else None}")
        return
    S["sk"] = obj
    sup = [(o.Name, s) for o, s in obj.AttachmentSupport]
    check("sketch attached to the top face of the Pad", sup and sup[0][0] == "Pad" and S["top"] in sup[0][1], sup)
    check("sketch sits at the face height (z = 10)", abs(obj.getGlobalPlacement().Base.z - 10) < 1e-6,
          obj.getGlobalPlacement().Base)
    d = Gui.ActiveDocument.ActiveView.getViewDirection()
    check("view turned normal to the face", abs(d.z) > 0.999, d)
    check("SKETCH tab current", "Sketch" in H.tab_names())
    H.grab("on-face")


@step
def s_project_tool():
    """Projection tool: click the front edge of the face, expect one external edge."""
    sk = S["sk"]
    n = ext_count(sk)
    a = H.action_for("Sketcher_Projection")
    check("Projection (external geometry) command available", a is not None)
    Gui.runCommand("Sketcher_Projection")
    H.pump(300)
    # Front edge of the top face: y = 0, z = 10.
    H.click_vec(V(20, 0, 10))
    note(f"projection click/preselection: {S.get('last_presel')}")
    H.pump(300)
    H.escape()
    S["doc"].recompute()
    H.pump(200)
    check("Projection tool + click on a face edge adds external geometry", ext_count(sk) == n + 1,
          f"{n} -> {ext_count(sk)}: {sk.ExternalGeometry}")
    check("sketch still open after Escape", H.in_edit() is not None)
    # FreeCAD 1.1: a projected edge is "defining" (part of the profile) unless construction mode is on.
    S["defining_edges"] = len(sk.Shape.Edges)
    note(f"edges in the sketch shape after projecting (no own geometry yet): {len(sk.Shape.Edges)}")


@step
def s_project_python():
    """Project another edge from Python and constrain a circle to it."""
    sk = S["sk"]
    n = ext_count(sk)
    shape = S["pad"].Shape
    # The right edge of the top face: x = 40, z = 10, along y.
    idx = [i for i, e in enumerate(shape.Edges) if abs(e.BoundBox.XMin - 40) < 1e-6 and abs(e.BoundBox.XMax - 40) < 1e-6
           and abs(e.BoundBox.ZMin - 10) < 1e-6 and abs(e.BoundBox.ZMax - 10) < 1e-6][0]
    sk.addExternal("Pad", "Edge%d" % (idx + 1))
    S["doc"].recompute()
    check("Python addExternal adds external geometry", ext_count(sk) == n + 1, sk.ExternalGeometry)
    # A circle tangent-free but with its centre on the projected right edge (external geo -4 or later).
    c = sk.addGeometry(Part.Circle(V(30, 15, 0), V(0, 0, 1), 5), False)
    ext_id = -3 - (ext_count(sk) - 1)  # last external edge
    sk.addConstraint(Sketcher.Constraint("DistanceX", c, 3, ext_id, 1, 10.0))  # x(edge) - x(centre) = 10
    S["doc"].recompute()
    msg = " ".join(H.labels_text(H.task_view()))
    check("constraint to external geometry solves (no conflict)", "Over-constrained" not in msg and
          "Redundant" not in msg, msg)
    cx = sk.Geometry[c].Center.x
    check("circle centre 10 mm left of the projected edge", abs(cx - 30) < 1e-6, cx)
    S["circle"] = c
    Gui.SendMsgToActiveView("ViewFit")
    H.grab("external")


@step
def s_finish():
    """Click FINISH SKETCH."""
    H.workspace_bar().finish.click()
    H.wait_for(lambda: H.in_edit() is None, 3000)
    return 800


@step
def s_pocket():
    """Pocket the face sketch with the toolbar command and press OK."""
    check("sketch closed", H.in_edit() is None)
    sk = S["sk"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, S["body"].Name, sk.Name + ".")
    Gui.runCommand("PartDesign_Pocket")
    H.wait_for(lambda: Gui.Control.activeDialog(), 3000)
    H.pump(500)
    check("Pocket dialog open", bool(Gui.Control.activeDialog()))
    tv = H.task_view()
    right = H.overlay("OverlayRight")
    check("Pocket task panel opens in the right panel", tv is not None and right.isAncestorOf(tv))
    check("right panel visible with the Pocket task panel", right is not None and right.isVisible()
          and tv is not None and tv.isVisible(), f"panel {right.isVisible() if right else None} "
          f"tasks {tv.isVisible() if tv else None} tab {right.tabText(right.currentIndex()) if right else None}")
    if tv is not None:
        H.check_readable("Pocket task panel readable", tv)
    H.grab("pocket-dialog")
    box = H.button_box()
    check("Pocket dialog has OK", box is not None)
    H.check_buttons_on_screen("Pocket OK / Cancel visible on screen", box)
    if box is not None:
        H.press(box, QtWidgets.QDialogButtonBox.Ok)
    H.wait_for(lambda: not Gui.Control.activeDialog(), 3000)
    return 600


@step
def s_after_pocket():
    """Expect a valid Pocket that removes material."""
    doc = S["doc"]
    doc.recompute()
    pockets = [o for o in S["body"].Group if o.TypeId == "PartDesign::Pocket"]
    check("Pocket made from the face sketch", bool(pockets))
    if pockets:
        p = pockets[0]
        ok = p.isValid() and "Invalid" not in p.State and not Gui.Control.activeDialog()
        check("Pocket of a circle valid although an edge was projected with the Projection tool", ok,
              f"{p.State} dialog open={bool(Gui.Control.activeDialog())}")
        if not ok:
            # The projected (defining) edge is an open wire in the profile; take it out and finish.
            S["sk"].delExternal(0)
            doc.recompute()
            if Gui.Control.activeDialog():
                box = H.button_box()
                if box is not None:
                    H.press(box, QtWidgets.QDialogButtonBox.Ok)
                H.wait_for(lambda: not Gui.Control.activeDialog(), 3000)
            doc.recompute()
        check("Pocket valid and removes material (projected edge removed if needed)",
              p.isValid() and p.Shape.isValid() and p.Shape.Volume < 12000 - 1,
              round(p.Shape.Volume, 2) if p.Shape.isValid() else p.State)
        check("Pocket dialog closed", not Gui.Control.activeDialog())
    Gui.SendMsgToActiveView("ViewFit")
    H.grab("pocketed")


@step
def s_properties_tab():
    """Select the sketch: expect the Properties tab to show it."""
    # Light check of the right panel's Properties tab with the sketch selected (not in edit).
    try:
        import bruceworx_rightpanel as rp
    except Exception as exc:
        note(f"no right panel module: {exc}")
        return
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, S["body"].Name, S["sk"].Name + ".")
    H.pump(600)
    cur = rp.current_tab()
    check("selecting the sketch brings the Properties tab forward", cur == rp.PROPERTIES, cur)
    host = [w for w in mw.findChildren(QtWidgets.QWidget) if w.objectName() == rp.PROPERTIES + "Host"]
    host = host[0] if host else None
    texts = H.labels_text(host) if host is not None else []
    note(f"Properties tab texts: {texts[:15]}")
    check("Properties tab is not a placeholder", host is not None and not any("Coming in this build" in t for t in texts))
    check("Properties tab mentions the sketch", any(S["sk"].Label in t or "Sketch" in t for t in texts), texts[:8])
    if host is not None and host.isVisible() and cur == rp.PROPERTIES:
        H.check_readable("Properties tab readable", host)
    H.grab("properties-tab")


H.run()
