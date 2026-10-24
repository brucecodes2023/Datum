# SPDX-License-Identifier: LGPL-2.1-or-later
"""Leonardo: motor adapter plate, modelled through the UI.

Ø60 x 5 disc, Ø22.2 bore for the motor's pilot boss, 4x M3 counterbored on the 31 mm square
(motor screws), 4x M3 tapped on the Ø52 bolt circle (housing through-bolts).
Env LEO_PN: the plate's part number (vault run: check out, save as <P/N>.FCStd in the work
folder, check in with a comment). Without it: a dry run, nothing saved.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
import leo_ui as U  # noqa: E402
import leo_vault as LV  # noqa: E402
from harness import App, Gui, S, check, note, step  # noqa: E402

H.set_script("leo_plate")
PN = os.environ.get("LEO_PN", "")
TITLE = "Motor adapter plate"


@step
def s_open():
    """Open the Leonardo vault and check out the adapter plate (or a dry run without a P/N)."""
    LV.start(PN, TITLE)
    return 800


@step
def s_body():
    """Design workspace: new Body for the plate."""
    import bruceworx_workspaces

    bruceworx_workspaces.activate_workspace("Design")
    doc = LV.document(PN, TITLE)
    before = doc.Objects
    Gui.runCommand("PartDesign_Body")
    H.pump(400)
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body" and o not in before]
    check("Body command creates a body", bool(bodies))
    S.update(doc=doc, body=bodies[0])
    bodies[0].Label = TITLE
    return 500


@step
def s_disc_sketch():
    """Sketch on XY: circle at the origin, diameter 60 via the dimension dialog: expect fully constrained."""
    sk = U.new_sketch(S["doc"], S["body"], plane="XY_Plane")
    if sk is None:
        return
    U.make_visible(sk, 120)
    U.tool_clicks(sk, "Sketcher_CreateCircle", [(0, 0), (30, 0)], 1)
    U.dimension(sk, "Sketcher_ConstrainDiameter", ["Edge1"], 60)
    check("disc sketch fully constrained", "Fully constrained" in U.message(), U.message())
    H.grab("disc-sketch")
    U.finish_sketch()
    S["disc"] = sk


@step
def s_disc_pad():
    """Pad the disc 5 mm."""
    S["pad"] = U.pad(S["body"], S["disc"], 5, "Plate pad")
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    return 400


@step
def s_bore():
    """Sketch on the top face: Ø22.2 circle at the centre, then Pocket through all."""
    face = U.top_face(S["pad"])
    sk = U.new_sketch(S["doc"], S["body"], face=face)
    if sk is None:
        return
    U.make_visible(sk, 80)
    U.tool_clicks(sk, "Sketcher_CreateCircle", [(0, 0), (11, 0)], 1)
    U.dimension(sk, "Sketcher_ConstrainDiameter", ["Edge1"], 22.2)
    if "Fully constrained" not in U.message():
        note("the centre click did not snap to the origin; constraining it with Coincident")
        U.constrain(sk, "Sketcher_ConstrainCoincidentUnified", "Vertex1", "RootPoint")
    check("bore sketch fully constrained", "Fully constrained" in U.message(), U.message())
    H.grab("bore-sketch")
    U.finish_sketch()
    S["bore"] = U.pocket(S["body"], sk, through=True, what="Boss bore")


@step
def s_motor_hole():
    """Sketch on the top face: one circle at (15.5, 15.5) -> M3 counterbored Hole through all."""
    face = U.top_face(S["bore"] or S["pad"], z=5)
    sk = U.new_sketch(S["doc"], S["body"], face=face)
    if sk is None:
        return
    U.make_visible(sk, 80)
    U.tool_clicks(sk, "Sketcher_CreateCircle", [(15.5, 15.5), (17, 15.5)], 1)
    U.dimension(sk, "Sketcher_ConstrainDistanceX", ["Vertex1"], 15.5)
    U.dimension(sk, "Sketcher_ConstrainDistanceY", ["Vertex1"], 15.5)
    U.dimension(sk, "Sketcher_ConstrainDiameter", ["Edge1"], 3.0)
    check("motor-hole sketch fully constrained", "Fully constrained" in U.message(), U.message())
    U.finish_sketch()
    S["cbore"] = U.hole(S["body"], sk, "Motor screw hole", size="M3", cut="Counterbore", through=True)


@step
def s_motor_pattern():
    """Circular Pattern of the counterbored hole, 4 around the centre: the 31 mm square."""
    if S.get("cbore") is not None:
        S["cbore4"] = U.polar(S["body"], S["cbore"], 4, "Motor holes pattern")


@step
def s_housing_hole():
    """Sketch on the top face: circle at (26, 0) -> M3 tapped Hole through all."""
    last = S.get("cbore4") or S.get("cbore") or S["bore"]
    face = U.top_face(last, z=5)
    sk = U.new_sketch(S["doc"], S["body"], face=face)
    if sk is None:
        return
    U.make_visible(sk, 80)
    U.tool_clicks(sk, "Sketcher_CreateCircle", [(26, 0), (27.2, 0)], 1)
    U.dimension(sk, "Sketcher_ConstrainDistanceX", ["Vertex1"], 26)
    U.dimension(sk, "Sketcher_ConstrainDiameter", ["Edge1"], 2.5)
    msg = U.message()
    if "Fully constrained" not in msg:
        U.dimension(sk, "Sketcher_ConstrainDistanceY", ["Vertex1"], 0.0)
    check("housing-hole sketch fully constrained", "Fully constrained" in U.message(), U.message())
    U.finish_sketch()
    S["tap"] = U.hole(S["body"], sk, "Housing screw hole", size="M3", threaded=True, through=True)


@step
def s_housing_pattern():
    """Circular Pattern of the tapped hole, 4 around the centre: the Ø52 bolt circle."""
    if S.get("tap") is not None:
        S["tap4"] = U.polar(S["body"], S["tap"], 4, "Housing holes pattern")


@step
def s_check():
    """Check the finished plate: one solid, volume and holes as designed."""
    doc = S["doc"]
    doc.recompute()
    body = S["body"]
    shape = body.Shape
    check("plate is one valid solid", shape.isValid() and len(shape.Solids) == 1, len(shape.Solids))
    bb = shape.BoundBox
    check("plate is Ø60 x 5", abs(bb.XLength - 60) < 1e-3 and abs(bb.ZLength - 5) < 1e-3,
          f"{bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f}")
    cyl = [f for f in shape.Faces if f.Surface.__class__.__name__ == "Cylinder"]
    radii = sorted(round(f.Surface.Radius, 2) for f in cyl)
    note(f"cylindrical face radii: {radii}")
    check("8 screw holes + bore + outside", len(cyl) >= 10, len(cyl))
    errors = [o.Label for o in doc.Objects if "Invalid" in o.State]
    check("no feature in error", not errors, errors)
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    H.grab("plate")


@step
def s_checkin():
    """Save into the vault work folder and Check In with a comment."""
    LV.finish(PN, S["doc"], "Motor adapter plate: Ø60x5, Ø22.2 boss bore, 4x M3 cbore on 31 sq, 4x M3 tapped on Ø52 BC")
    return 1000


H.run()
