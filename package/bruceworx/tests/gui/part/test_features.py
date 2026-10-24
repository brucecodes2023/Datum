# SPDX-License-Identifier: LGPL-2.1-or-later
"""Part modelling from the UI: new Part / Body, then each PartDesign feature.

For each feature: run the command (as its toolbar button does), check the task
dialog opens in the right panel, set values in the dialog's own widgets, press
OK, then check the solid, the Browser row summary, the undo toast, Undo/Redo
and that double-clicking the Browser row opens the dialog again.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD as App
import FreeCADGui as Gui

import harness as H
import partmodel as M

S = {}
V = App.Vector


def sel_clear():
    Gui.Selection.clearSelection()


def sel(obj, sub=""):
    """Select like the Browser / 3D view do: the path from the top-level Part."""
    doc, part, body = S["doc"], S["part"], S["body"]
    if obj is part:
        Gui.Selection.addSelection(doc.Name, part.Name, "")
    elif obj is body:
        Gui.Selection.addSelection(doc.Name, part.Name, f"{body.Name}.")
    else:
        Gui.Selection.addSelection(doc.Name, part.Name, f"{body.Name}.{obj.Name}.{sub}")


def tip():
    return S["body"].Tip


def volume():
    try:
        return S["body"].Shape.Volume
    except Exception:
        return 0.0


def in_edit():
    vp = Gui.ActiveDocument.getInEdit() if Gui.ActiveDocument else None
    return getattr(vp, "Object", None)


def feature_flow(key, command, type_id, select, fill, expect_summary=None, expect_volume=None,
                 edit_fill=None, edit_check=None, edit_summary=None):
    """Generator: one feature from command to undo/redo and edit again."""
    doc = S["doc"]
    H.hide_toasts()
    sel_clear()
    select()
    before = {o.Name for o in doc.Objects}
    vol0 = volume()
    undo0 = doc.UndoCount
    H.narrate(f"{key}: run {command}, set values in its task panel, OK; expect a valid solid, a Browser summary "
              f"and an undo toast, then Undo/Redo and double-click the row to edit it again")
    names0 = list(doc.UndoNames)
    H.process()
    if isinstance(command, tuple):
        Gui.runCommand(*command)  # a drop-down group: (group command, index)
    else:
        Gui.runCommand(command)
    yield 1500
    new = [o for o in doc.Objects if o.Name not in before and o.TypeId == type_id]
    H.check(f"{key}.dialog_opens", H.dialog_open(), f"no task dialog after {command}")
    ok, detail = H.task_in_right_panel()
    H.check(f"{key}.dialog_in_right_panel", ok, detail)
    H.check(f"{key}.ok_cancel_buttons", H.task_button("ok") is not None and H.task_button("cancel") is not None,
            "OK/Cancel missing in task panel")
    for which in ("ok", "cancel"):
        ok, detail = H.fully_visible(H.task_button(which))
        H.check(f"{key}.{which}_button_readable", ok, detail)
        if not ok and key == "pad":
            H.say("button chain:\n  " + "\n  ".join(H.widget_chain(H.task_button(which))))
    H.check(f"{key}.feature_created", len(new) == 1, f"new {type_id}: {[o.Name for o in new]}")
    if not new or not H.dialog_open():
        H._cleanup_dialog()
        return
    feat = new[0]
    name = feat.Name
    S[key] = name
    H.grab_window(f"{key}-dialog")
    try:
        filled = fill(feat)
    except Exception as exc:
        filled = f"fill raised {exc}"
    H.check(f"{key}.values_set", filled is True, filled)
    yield 900
    H.check(f"{key}.ok_clicked", H.click_task("ok"), "no OK button")
    seen = []
    for _ in range(15):  # what the user sees while the dialog closes and the model recomputes
        yield 100
        seen += [t for t in H.toast_texts() if t not in seen]
    H.check(f"{key}.dialog_closed", not H.dialog_open(), "dialog still open after OK")
    if H.dialog_open():
        H._cleanup_dialog()
        yield 800
    feat = doc.getObject(name)
    H.check(f"{key}.kept_after_ok", feat is not None, "feature gone after OK")
    if feat is None:
        return
    import bruceworx_picktrace as pt
    H.check(f"{key}.no_error", not pt.obj_in_error(feat), f"State={feat.State}")
    ok, detail = M.solid_ok(S["body"])
    H.check(f"{key}.valid_solid", ok, detail)
    H.check(f"{key}.is_tip", tip() is feat, f"tip={tip().Name if tip() else None}")
    if expect_volume is not None:
        vol = volume()
        H.check(f"{key}.volume", expect_volume(vol0, vol), f"before={vol0:.2f} after={vol:.2f}")
    info = H.row_info(feat)
    summary = info[0] if info else None
    if expect_summary is not None:
        H.check(f"{key}.browser_summary", summary == expect_summary, f"got {summary!r}, want {expect_summary!r}")
    else:
        H.say(f"{key} summary {summary!r}")
    H.check(f"{key}.browser_row", H.tree_item(feat)[1] is not None, f"no Browser row for {feat.Label}")
    # One new undo step (the stack is capped, Preferences > General > Undo steps).
    one = doc.UndoCount == undo0 + 1 or (doc.UndoCount == undo0 and list(doc.UndoNames[1:]) == names0[:-1])
    H.check(f"{key}.one_undo_step", one, f"undo steps {undo0} -> {doc.UndoCount}: {doc.UndoNames[:3]}")
    seen += [t for t in H.toast_texts() if t not in seen]
    H.check(f"{key}.undo_toast", any(t.strip() for t in seen), f"no toast seen; undo top={doc.UndoNames[:1]}")
    H.say(f"{key} toast {seen} undo top {doc.UndoNames[:1]}")
    H.grab_window(f"{key}-done")
    # Undo / redo.
    Gui.runCommand("Std_Undo")
    yield 1200
    H.check(f"{key}.undo_removes", doc.getObject(name) is None, f"still there; tip={tip().Name if tip() else None}")
    H.check(f"{key}.undo_restores_volume", abs(volume() - vol0) < 1e-3 * max(1.0, vol0),
            f"vol0={vol0:.2f} now={volume():.2f}")
    Gui.runCommand("Std_Redo")
    yield 1500
    feat = doc.getObject(name)
    H.check(f"{key}.redo_restores", feat is not None and tip() is feat and M.solid_ok(S["body"])[0],
            f"feat={feat is not None} tip={tip().Name if tip() else None} {M.solid_ok(S['body'])[1]}")
    if feat is None:
        return
    # Edit again from the Browser.
    sel_clear()
    H.expand_trees()
    yield 300
    clicked = H.double_click_row(feat)
    yield 1500
    reopened = H.dialog_open() and in_edit() is feat
    H.check(f"{key}.double_click_edits", reopened,
            f"row found={clicked} dialog={bool(H.dialog_open())} in edit={getattr(in_edit(), 'Name', None)}")
    if not H.dialog_open():
        import bruceworx_picktrace as pt
        pt.open_feature(feat)  # carry on from the edit dialog
        yield 1500
    if not H.dialog_open():
        return
    ok, detail = H.task_in_right_panel()
    H.check(f"{key}.edit_in_right_panel", ok, detail)
    if edit_fill is not None:
        try:
            filled = edit_fill(feat)
        except Exception as exc:
            filled = f"edit fill raised {exc}"
        H.check(f"{key}.edit_values_set", filled is True, filled)
        yield 900
    H.grab_window(f"{key}-edit")
    H.click_task("ok")
    yield 1500
    H.check(f"{key}.edit_closed", not H.dialog_open(), "dialog still open after OK")
    H._cleanup_dialog()
    feat = doc.getObject(name)
    if edit_check is not None and feat is not None:
        ok, detail = edit_check(feat)
        H.check(f"{key}.edit_applied", ok, detail)
    if edit_summary is not None and feat is not None:
        info = H.row_info(feat)
        H.check(f"{key}.edit_summary", info and info[0] == edit_summary, f"got {info and info[0]!r}, want {edit_summary!r}")
    ok, detail = M.solid_ok(S["body"])
    H.check(f"{key}.valid_after_edit", ok, detail)


def setq(name, value):
    w = H.find_task_widget(name)
    if w is None:
        return f"no widget {name!r} in the task panel"
    return H.set_quantity(w, value) or f"could not set {name}"


def all_true(*results):
    for r in results:
        if r is not True:
            return r
    return True


# ----- tests --------------------------------------------------------------------------------


@H.test
def t_new_part_body():
    doc = App.newDocument("PartTest")
    S["doc"] = doc
    yield 1000
    Gui.activateWorkbench("PartDesignWorkbench")
    yield 1500
    H.hide_toasts()
    Gui.runCommand("Std_Part")
    yield 1000
    parts = [o for o in doc.Objects if o.TypeId == "App::Part"]
    H.check("new_part.created", len(parts) == 1, [o.Name for o in doc.Objects])
    if not parts:
        return
    part = parts[0]
    S["part"] = part
    view = Gui.ActiveDocument.ActiveView
    H.check("new_part.active", view.getActiveObject("part") is part, "Part not made active")
    H.check("new_part.toast", any("Part" in t for t in H.toast_texts()), H.toast_texts())
    Gui.runCommand("PartDesign_Body")
    yield 1000
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
    H.check("new_body.created", len(bodies) == 1, [o.Name for o in doc.Objects])
    if not bodies:
        return
    body = bodies[0]
    S["body"] = body
    H.check("new_body.in_part", body in part.Group, f"part group={[o.Name for o in part.Group]}")
    H.check("new_body.active", view.getActiveObject("pdbody") is body, "Body not active")
    H.check("new_body.toast", any("Body" in t for t in H.toast_texts()), H.toast_texts())
    H.check("new_body.browser_row", H.tree_item(body)[1] is not None, "no Browser row")
    H.expand_trees()
    H.grab_window("new-body")


@H.test
def t_pad():
    body = S["body"]
    sk = M.sketch(body, "XY_Plane", name="SketchBase")
    M.rect(sk, -20, -15, 20, 15)
    M.done(sk)
    S["sk_base"] = sk
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    yield 400
    yield from feature_flow(
        "pad", "PartDesign_Pad", "PartDesign::Pad",
        select=lambda: sel(sk),
        fill=lambda f: setq("lengthEdit", 8),
        expect_summary="8 mm",
        expect_volume=lambda a, b: abs(b - 40 * 30 * 8) < 1,
        edit_fill=lambda f: setq("lengthEdit", 10),
        edit_check=lambda f: (abs(f.Length.Value - 10) < 1e-6, f"Length={f.Length}"),
        edit_summary="10 mm",
    )
    # Back to 8 mm for the features that follow (a plain property edit).
    pad = S["doc"].getObject(S.get("pad", ""))
    if pad is not None:
        pad.Length = 8
        S["doc"].recompute()


@H.test
def t_pocket():
    sk = M.sketch(S["body"], "XY_Plane", z=8, name="SketchPocket")
    M.circle(sk, 6, 4, 2.5)
    M.done(sk)
    yield 400
    yield from feature_flow(
        "pocket", "PartDesign_Pocket", "PartDesign::Pocket",
        select=lambda: sel(sk),
        fill=lambda f: setq("lengthEdit", 5),
        expect_summary="5 mm",
        expect_volume=lambda a, b: abs((a - b) - 3.14159265 * 2.5 ** 2 * 5) < 0.5,
        edit_fill=lambda f: setq("lengthEdit", 4),
        edit_check=lambda f: (abs(f.Length.Value - 4) < 1e-6, f"Length={f.Length}"),
        edit_summary="4 mm",
    )


@H.test
def t_hole():
    sk = M.sketch(S["body"], "XY_Plane", z=8, name="SketchHole")
    M.circle(sk, 6, -6, 1.5)
    M.done(sk)
    yield 400
    yield from feature_flow(
        "hole", "PartDesign_Hole", "PartDesign::Hole",
        select=lambda: sel(sk),
        fill=lambda f: all_true(setq("Diameter", 6), setq("Depth", 6)),
        expect_summary="Ø6",
        expect_volume=lambda a, b: b < a - 100,
        edit_fill=lambda f: setq("Diameter", 5),
        edit_check=lambda f: (abs(f.Diameter.Value - 5) < 1e-6, f"Diameter={f.Diameter}"),
        edit_summary="Ø5",
    )


@H.test
def t_revolve():
    sk = M.sketch(S["body"], "XZ_Plane", name="SketchRevolve")
    M.rect(sk, 18, 0, 22, 5)
    M.done(sk)
    yield 400

    def fill(f):
        return setq("revolveAngle", 180)

    yield from feature_flow(
        "revolve", "PartDesign_Revolution", "PartDesign::Revolution",
        select=lambda: sel(sk),
        fill=fill,
        expect_summary="Revolve 180°",
        expect_volume=lambda a, b: b > a + 50,
        edit_fill=lambda f: setq("revolveAngle", 120),
        edit_check=lambda f: (abs(f.Angle.Value - 120) < 1e-6, f"Angle={f.Angle}"),
        edit_summary="Revolve 120°",
    )


@H.test
def t_groove():
    sk = M.sketch(S["body"], "XZ_Plane", name="SketchGroove")
    M.rect(sk, 11, 6, 13, 8.5)
    M.done(sk)
    yield 400
    yield from feature_flow(
        "groove", "PartDesign_Groove", "PartDesign::Groove",
        select=lambda: sel(sk),
        fill=lambda f: setq("revolveAngle", 360),
        expect_summary="360°",
        expect_volume=lambda a, b: b < a - 10,
        edit_fill=lambda f: setq("revolveAngle", 270),
        edit_check=lambda f: (abs(f.Angle.Value - 270) < 1e-6, f"Angle={f.Angle}"),
        edit_summary="270°",
    )


@H.test
def t_draft():
    shape = tip().Shape
    faces = M.planar_faces(shape, V(0, -1, 0)) + M.planar_faces(shape, V(0, 1, 0))
    H.check("draft.face_found", bool(faces), "no planar side face")
    # The side the half ring of the revolve does not reach: the one with the bigger plain face.
    best = max(faces, key=lambda i: shape.Faces[i - 1].Area) if faces else None
    S["draft_side"] = shape.Faces[best - 1].normalAt(0, 0).y if best else 0
    yield from feature_flow(
        "draft", "PartDesign_Draft", "PartDesign::Draft",
        select=lambda: sel(tip(), f"Face{best}"),
        fill=lambda f: setq("draftAngle", 2),
        expect_summary="2°",
        edit_fill=lambda f: setq("draftAngle", 3),
        edit_check=lambda f: (abs(f.Angle.Value - 3) < 1e-6, f"Angle={f.Angle}"),
        edit_summary="3°",
    )


def _corner_edges():
    """Straight vertical 8 mm corner edges on the side away from the draft."""
    shape = tip().Shape
    side = S.get("draft_side", -1)
    edges = M.vertical_edges(shape, 8)
    edges = [(i, p) for i, p in edges if abs(abs(p.x) - 20) < 1e-3 and abs(abs(p.y) - 15) < 1e-3 and p.y * side < 0]
    return [i for i, _p in sorted(edges, key=lambda e: e[1].x)]


@H.test
def t_fillet():
    edges = _corner_edges()
    H.check("fillet.edge_found", len(edges) >= 2, f"corner edges {edges}")
    if not edges:
        return
    yield from feature_flow(
        "fillet", "PartDesign_Fillet", "PartDesign::Fillet",
        select=lambda: sel(tip(), f"Edge{edges[0]}"),
        fill=lambda f: setq("filletRadius", 2),
        expect_summary="R2",
        expect_volume=lambda a, b: b < a,
        edit_fill=lambda f: setq("filletRadius", 3),
        edit_check=lambda f: (abs(f.Radius.Value - 3) < 1e-6, f"Radius={f.Radius}"),
        edit_summary="R3",
    )


@H.test
def t_chamfer():
    edges = _corner_edges()
    H.check("chamfer.edge_found", bool(edges), f"corner edges {edges}")
    if not edges:
        return
    yield from feature_flow(
        "chamfer", "PartDesign_Chamfer", "PartDesign::Chamfer",
        select=lambda: sel(tip(), f"Edge{edges[-1]}"),
        fill=lambda f: setq("chamferSize", 1.5),
        expect_summary="C1.5",
        expect_volume=lambda a, b: b < a,
        edit_fill=lambda f: setq("chamferSize", 2),
        edit_check=lambda f: (abs(f.Size.Value - 2) < 1e-6, f"Size={f.Size}"),
        edit_summary="C2",
    )


def _pocket():
    return S["doc"].getObject(S.get("pocket", ""))


def _hole():
    return S["doc"].getObject(S.get("hole", ""))


@H.test
def t_mirror():
    yield from feature_flow(
        "mirror", "PartDesign_Mirrored", "PartDesign::Mirrored",
        select=lambda: sel(_pocket()),
        fill=lambda f: True,
        expect_summary="",
        expect_volume=lambda a, b: b < a - 10,
    )


@H.test
def t_linear_pattern():
    def fill(f):
        return all_true(setq("spinExtent", 10), setq("spinOccurrences", 3))

    yield from feature_flow(
        "linear", "PartDesign_LinearPattern", "PartDesign::LinearPattern",
        select=lambda: sel(_pocket()),
        fill=fill,
        expect_summary="Pattern × 3",
        expect_volume=lambda a, b: b < a - 10,
        edit_fill=lambda f: setq("spinOccurrences", 2),
        edit_check=lambda f: (f.Occurrences == 2, f"Occurrences={f.Occurrences}"),
        edit_summary="Pattern × 2",
    )


@H.test
def t_polar_pattern():
    yield from feature_flow(
        "polar", "PartDesign_PolarPattern", "PartDesign::PolarPattern",
        select=lambda: sel(_hole()),
        fill=lambda f: setq("spinOccurrences", 4),
        expect_summary="Pattern × 4",
        expect_volume=lambda a, b: b < a - 10,
        edit_fill=lambda f: setq("spinOccurrences", 3),
        edit_check=lambda f: (f.Occurrences == 3, f"Occurrences={f.Occurrences}"),
        edit_summary="Pattern × 3",
    )


@H.test
def t_bw_rectangular_on_feature():
    """BruceWorx Rectangular Pattern on a feature hands over to PartDesign's linear pattern."""
    yield from feature_flow(
        "bw_rect_feature", "BruceWorx_RectangularPattern", "PartDesign::LinearPattern",
        select=lambda: sel(_pocket()),
        fill=lambda f: all_true(setq("spinExtent", 8), setq("spinOccurrences", 2)),
        expect_summary="Pattern × 2",
    )


@H.test
def t_bw_circular_on_feature():
    yield from feature_flow(
        "bw_circ_feature", "BruceWorx_CircularPattern", "PartDesign::PolarPattern",
        select=lambda: sel(_pocket()),
        fill=lambda f: setq("spinOccurrences", 3),
        expect_summary="Pattern × 3",
    )


@H.test
def t_cancel_leaves_nothing():
    """Cancel on a new feature's dialog takes it out again (one more user path)."""
    doc = S["doc"]
    sk = M.sketch(S["body"], "XY_Plane", z=8, name="SketchCancel")
    M.circle(sk, -10, 0, 2)
    M.done(sk)
    yield 300
    sel_clear()
    sel(sk)
    names = {o.Name for o in doc.Objects}
    tip0 = tip()
    Gui.runCommand("PartDesign_Pocket")
    yield 1500
    H.check("cancel.dialog_opens", H.dialog_open())
    H.click_task("cancel")
    yield 1200
    left = [o.Name for o in doc.Objects if o.Name not in names]
    H.check("cancel.nothing_left", not left and tip() is tip0, f"left={left} tip={tip().Name}")
    H.check("cancel.dialog_closed", not H.dialog_open())


@H.test
def t_body2_primitive_thickness_datum():
    """A second body: additive box, thickness (shell) and a datum plane."""
    doc = S["doc"]
    H.hide_toasts()
    sel_clear()
    Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", None)
    Gui.runCommand("PartDesign_Body")
    yield 1000
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body" and o is not S["body"]]
    H.check("body2.created", len(bodies) == 1, [o.Name for o in bodies])
    if not bodies:
        return
    S["body1"], S["body"] = S["body"], bodies[0]
    H.check("body2.in_part", bodies[0] in S["part"].Group, "second body not in the active Part")

    def box(f):
        return all_true(setq("boxLength", 12), setq("boxWidth", 10), setq("boxHeight", 6))

    yield from feature_flow(
        "box", ("PartDesign_CompPrimitiveAdditive", 0), "PartDesign::AdditiveBox",
        select=lambda: None,
        fill=box,
        expect_volume=lambda a, b: abs(b - 12 * 10 * 6) < 1e-3,
        edit_fill=lambda f: setq("boxHeight", 8),
        edit_check=lambda f: (abs(f.Height.Value - 8) < 1e-6, f"Height={f.Height}"),
    )
    box_obj = doc.getObject(S.get("box", ""))
    if box_obj is None:
        return
    top = M.planar_faces(box_obj.Shape, V(0, 0, 1))
    yield from feature_flow(
        "thickness", "PartDesign_Thickness", "PartDesign::Thickness",
        select=lambda: sel(tip(), f"Face{top[0]}"),
        fill=lambda f: setq("Value", 1),
        expect_summary="1 mm",
        expect_volume=lambda a, b: b < a * 0.8,
        edit_fill=lambda f: setq("Value", 1.5),
        edit_check=lambda f: (abs(f.Value.Value - 1.5) < 1e-6, f"Value={f.Value}"),
        edit_summary="1.5 mm",
    )
    # Datum plane on the body's XY plane: no solid change, so its own checks.
    sel_clear()
    Gui.Selection.addSelection(doc.Name, S["part"].Name, f"{S['body'].Name}.{S['body'].Origin.Name}.{M.plane(S['body'], 'XY_Plane').Name}.")
    before = {o.Name for o in doc.Objects}
    Gui.runCommand("PartDesign_Plane")
    yield 1500
    new = [o for o in doc.Objects if o.Name not in before and o.TypeId == "PartDesign::Plane"]
    H.check("datum_plane.dialog_opens", H.dialog_open())
    ok, detail = H.task_in_right_panel()
    H.check("datum_plane.dialog_in_right_panel", ok, detail)
    H.check("datum_plane.created", len(new) == 1, [o.Name for o in new])
    H.grab_window("datum-dialog")
    H.click_task("ok")
    yield 1200
    H.check("datum_plane.dialog_closed", not H.dialog_open())
    H._cleanup_dialog()
    if new:
        plane = doc.getObject(new[0].Name)
        H.check("datum_plane.kept", plane is not None and plane in S["body"].Group, "datum plane not in body")
        H.check("datum_plane.attached", plane is not None and plane.MapMode != "Deactivated" and plane.isValid(),
                f"MapMode={getattr(plane, 'MapMode', None)}")
        H.expand_trees()
        yield 300
        H.double_click_row(plane)
        yield 1500
        H.check("datum_plane.double_click_edits", H.dialog_open() and in_edit() is plane,
                f"in edit={getattr(in_edit(), 'Name', None)}")
        H._cleanup_dialog()
        yield 800
    H.grab_window("body2")


@H.test
def t_final_state():
    doc = S["doc"]
    import bruceworx_picktrace as pt
    errors = [o.Label for o in doc.Objects if pt.obj_in_error(o)]
    H.check("final.no_errors", not errors, errors)
    H.expand_trees()
    yield 500
    left = H.overlay("OverlayLeft")
    if left is not None:
        H.grab_widget(left, "browser")
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    yield 500
    H.grab_window("final")


H.start()
