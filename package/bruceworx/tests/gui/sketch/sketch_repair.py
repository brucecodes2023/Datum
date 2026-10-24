# SPDX-License-Identifier: LGPL-2.1-or-later
"""The sketch profile repair card (bruceworx_sketchrepair).

A rectangle with one corner 0.05 mm open is padded:
A) from Python: the Pad fails, the card shows at the top of the 3D view with
   the gap, Close Gaps fixes the sketch in one undo step and the Pad recomputes;
   Undo brings the gap (and the card) back; Escape dismisses it.
B) with the Pad command: the card shows while the Pad dialog is open (Open
   Sketch disabled), Close Gaps, then OK in the dialog gives a valid Pad.
C) Open Sketch on the card opens the sketch for editing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
from harness import App, Gui, QtCore, QtWidgets, S, check, mw, note, step  # noqa: E402

import Part  # noqa: E402
import Sketcher  # noqa: E402

H.set_script("sketch_repair")
V = App.Vector
GAP = 0.05


def gap_sketch(body, name):
    sk = body.newObject("Sketcher::SketchObject", name)
    xy = [f for f in body.Origin.OriginFeatures if f.Name.startswith("XY_Plane")][0]
    sk.AttachmentSupport = [(xy, "")]
    sk.MapMode = "FlatFace"
    pts = [V(0, 0, 0), V(40, 0, 0), V(40, 25, 0), V(0, 25, 0)]
    for i in range(4):
        end = pts[(i + 1) % 4]
        if i == 3:
            end = V(0, GAP, 0)  # last edge stops 0.05 mm short of the first corner
        sk.addGeometry(Part.LineSegment(pts[i], end), False)
    for i in range(3):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, i + 1, 1))
    return sk


def card():
    import bruceworx_sketchrepair

    return bruceworx_sketchrepair.card()


def card_shown():
    c = card()
    return c is not None and c.isVisible()


@step
def s_setup():
    """Pad a rectangle sketch with a 0.05 mm gap: expect the Pad to fail."""
    import bruceworx_workspaces

    bruceworx_workspaces.activate_workspace("Design")
    doc = App.newDocument("SketchRepair")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = gap_sketch(body, "GapSketch")
    doc.recompute()
    S.update(doc=doc, body=body, sk=sk)
    check("repair preference on", App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx").GetBool("SketchRepair", True))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 5
    doc.recompute()
    S["pad"] = pad
    Gui.SendMsgToActiveView("ViewFit")
    return 800


@step
def s_card():
    """Expect the repair card at the top of the view naming the 0.05 mm gap."""
    pad = S["pad"]
    check("Pad on the 0.05 mm gap sketch fails", "Invalid" in pad.State or not pad.isValid(), pad.State)
    H.wait_for(card_shown, 3000)
    c = card()
    check("repair card shows", card_shown())
    if c is None:
        return
    title, body = c.title.text(), c.body.text()
    note(f"card title={title!r} body={body!r} note={c.note.text()!r}")
    check("card names the feature and sketch", "Pad" in title and "GapSketch" in title, title)
    check("card shows the gap size (0.05 mm)", "0.05 mm" in body, body)
    check("Close Gaps button enabled", c.fix_button.isVisible() and c.fix_button.isEnabled())
    check("Open Sketch button enabled", c.open_button.isVisible() and c.open_button.isEnabled())
    view = c.parentWidget()
    inside = view is not None and view.rect().contains(c.geometry())
    check("card fits inside the 3D view", inside, f"card {c.geometry().getRect()} view {view.rect().getRect() if view else None}")
    # Not covered by the floating panels
    clashes = []
    for name in ("OverlayLeft", "OverlayRight"):
        ov = H.overlay(name)
        if ov is not None and ov.isVisible():
            r_ov = QtCore.QRect(ov.mapTo(mw, QtCore.QPoint(0, 0)), ov.size())
            r_c = QtCore.QRect(c.mapTo(mw, QtCore.QPoint(0, 0)), c.size())
            if r_ov.intersects(r_c):
                clashes.append(name)
    check("card not under the Browser or the right panel", not clashes, clashes)
    H.check_readable("card text readable", c)
    H.grab("card")


@step
def s_close_gaps():
    """Click Close Gaps: expect a valid Pad and one undo step."""
    c = card()
    if c is None:
        check("Close Gaps", False, "no card")
        return
    c.fix_button.click()
    H.pump(500)
    S["doc"].recompute()
    pad = S["pad"]
    check("Close Gaps makes the Pad valid", pad.isValid() and "Invalid" not in pad.State, pad.State)
    check("card hides after the fix", not card_shown())
    check("Pad has a solid", pad.Shape.isValid() and pad.Shape.Volume > 1, round(pad.Shape.Volume, 2))
    undo = S["doc"].UndoNames[:1]
    check("fix is one undo step 'Close sketch gaps'", undo == ["Close sketch gaps"], undo)
    H.grab("fixed")


@step
def s_undo():
    """Cmd-Z: expect the gap and the card back."""
    Gui.runCommand("Std_Undo")
    H.pump(300)
    S["doc"].recompute()
    H.wait_for(card_shown, 2000)
    check("Undo brings the gap back (Pad fails again)", not S["pad"].isValid() or "Invalid" in S["pad"].State,
          S["pad"].State)
    check("card shows again after Undo", card_shown())


@step
def s_escape():
    """Press Escape in the view: expect the card to go away and stay away."""
    c = card()
    if c is None:
        return
    view = c.parentWidget()
    mw.activateWindow()
    view.setFocus()
    H.pump(100)
    focus = QtWidgets.QApplication.focusWidget()
    H.key(QtCore.Qt.Key_Escape, focus if focus is not None and view.isAncestorOf(focus) else view)
    H.pump(200)
    check("Escape (focus in the 3D view) dismisses the card", not card_shown(),
          f"focus {focus.metaObject().className() if focus else None}")
    if card_shown():
        c.dismiss()  # keep going with a clean state
    S["doc"].recompute()
    H.pump(300)
    check("dismissed card stays away on the next recompute", not card_shown())


@step
def s_command_path():
    """Second gap sketch: click Pad in the toolbar."""
    # B) A second sketch, padded with the toolbar command.
    doc = S["doc"]
    body2 = doc.addObject("PartDesign::Body", "Body001")
    sk2 = gap_sketch(body2, "GapSketch2")
    doc.recompute()
    S.update(body2=body2, sk2=sk2)
    Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", body2)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, sk2.Name)
    Gui.runCommand("PartDesign_Pad")
    H.wait_for(lambda: Gui.Control.activeDialog(), 3000)
    H.wait_for(card_shown, 3000)
    return 600


@step
def s_command_card():
    """Expect the card while the Pad dialog is open; Close Gaps, then OK."""
    check("Pad dialog open", bool(Gui.Control.activeDialog()))
    check("card shows while the Pad dialog is open", card_shown())
    c = card()
    if c is None or not c.isVisible():
        box = H.button_box()
        if box is not None:
            H.press(box, QtWidgets.QDialogButtonBox.Cancel)
        return
    check("card is about the new Pad (Pad001)", c.key is not None and c.key[1] == "Pad001", c.key)
    check("Open Sketch disabled while the Pad dialog is open", not c.open_button.isEnabled())
    tv = H.task_view()
    check("Pad task panel in the right panel", tv is not None and H.overlay("OverlayRight").isAncestorOf(tv))
    H.grab("card-in-pad-dialog")
    c.fix_button.click()
    H.pump(500)
    box = H.button_box()
    check("Pad dialog has OK", box is not None)
    H.check_buttons_on_screen("Pad OK / Cancel visible on screen", box)
    if box is not None:
        H.press(box, QtWidgets.QDialogButtonBox.Ok)
    H.wait_for(lambda: not Gui.Control.activeDialog(), 3000)
    return 500


@step
def s_command_after():
    """Expect a valid Pad and the dialog closed."""
    doc = S["doc"]
    doc.recompute()
    pads = [o for o in S["body2"].Group if o.TypeId == "PartDesign::Pad"]
    check("Pad created through the dialog", bool(pads))
    if pads:
        check("Pad valid after Close Gaps + OK", pads[0].isValid() and "Invalid" not in pads[0].State, pads[0].State)
    check("dialog closed", not Gui.Control.activeDialog())
    check("card gone", not card_shown())
    H.grab("after-pad-dialog")


@step
def s_open_sketch():
    """Bring the first card back and click Open Sketch: expect the sketch to open."""
    # C) The first Pad still has its gap (Undo above) and was dismissed: forget that and recompute.
    import bruceworx_sketchrepair

    bruceworx_sketchrepair._dismissed.clear()
    sk = S["sk"]
    S["pad"].touch()
    S["doc"].recompute()
    H.wait_for(card_shown, 2000)
    c = card()
    check("card back after the dismissal is forgotten", card_shown())
    if c is None:
        return
    c.open_button.click()
    H.wait_for(lambda: H.in_edit() is not None, 3000)
    obj = H.in_edit()
    check("Open Sketch opens the sketch for editing", obj is not None and obj.Name == sk.Name, getattr(obj, "Name", None))
    check("card makes room when the sketch opens", not card_shown())
    H.grab("open-sketch")
    return 400


@step
def s_close():
    """Leave the sketch."""
    if H.in_edit() is not None:
        Gui.runCommand("Sketcher_LeaveSketch")
        H.wait_for(lambda: H.in_edit() is None, 3000)
    check("sketch closed", H.in_edit() is None)


H.run()
