# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketch on a base plane, the way a user does it.

Design workspace -> Create > New Sketch on XY -> the SKETCH tab, Finish Sketch,
the sketch toolbars and their buttons, the grid, the view normal to the sketch,
the task panel in the right panel (and readable) -> draw line, rectangle,
circle, arc and polyline with the tools and mouse clicks -> undo / redo inside
the sketch -> Escape keeps the sketch open -> Finish Sketch -> back in Design.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402
from harness import App, Gui, QtCore, QtWidgets, S, check, mw, note, step  # noqa: E402

H.set_script("sketch_plane")

SKETCH_TOOLBARS = ("Edit Mode", "Geometries", "Constraints", "Sketcher Tools", "Visual Helpers")
GEOMETRY_BUTTONS = (
    "Sketcher_CreatePoint", "Sketcher_CreatePolyline", "Sketcher_CreateLine", "Sketcher_CompCreateArc",
    "Sketcher_CompCreateConic", "Sketcher_CompCreateRectangles", "Sketcher_CompCreateRegularPolygon",
    "Sketcher_CompSlot", "Sketcher_CompCreateBSpline", "Sketcher_ToggleConstruction",
)
CONSTRAINT_BUTTONS = (
    "Sketcher_CompDimensionTools", "Sketcher_ConstrainCoincidentUnified", "Sketcher_CompHorVer",
    "Sketcher_ConstrainParallel", "Sketcher_ConstrainPerpendicular", "Sketcher_ConstrainTangent",
    "Sketcher_ConstrainEqual", "Sketcher_ConstrainSymmetric", "Sketcher_ConstrainBlock",
)
TOOL_BUTTONS = ("Sketcher_CompCreateFillets", "Sketcher_CompCurveEdition", "Sketcher_CompExternal")
EDIT_BUTTONS = ("Sketcher_LeaveSketch", "Sketcher_ViewSketch", "Sketcher_ViewSection")


def sketch():
    return S["doc"].getObject(S["sk"])


@step
def s_design():
    """Switch to the Design workspace."""
    import bruceworx_workspaces

    bruceworx_workspaces.activate_workspace("Design")
    return 1200


@step
def s_new_doc():
    """New document with a Body: expect the New Sketch button in Solid > Create."""
    wb = Gui.activeWorkbench()
    check("design workspace active (PartDesignWorkbench)", wb and wb.name() == "PartDesignWorkbench",
          wb.name() if wb else None)
    doc = App.newDocument("SketchPlane")
    body = doc.addObject("PartDesign::Body", "Body")
    doc.recompute()
    S.update(doc=doc, body=body)
    Gui.SendMsgToActiveView("ViewFit")
    bar = H.toolbar("Solid · Create")
    names = [n for n, a, w in H.toolbar_buttons(bar)] if bar else []
    check("Solid · Create toolbar visible", bar is not None and bar.isVisible())
    check("New Sketch button in Solid · Create", "PartDesign_NewSketch" in names, names[:4])
    return 800


@step
def s_model_view():
    """Note the view size before sketching."""
    S["model_view"] = H.view_size_text()


@step
def s_new_sketch():
    """Select the XY plane and click New Sketch: expect the sketch to open for editing."""
    body = S["body"]
    acts = [a for n, a, w in H.toolbar_buttons(H.toolbar("Solid · Create")) if n == "PartDesign_NewSketch"]
    check("New Sketch button enabled", acts and acts[0].isEnabled())
    xy = [f for f in body.Origin.OriginFeatures if f.Name.startswith("XY_Plane")][0]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, xy.Name)
    Gui.runCommand("PartDesign_NewSketch")
    H.wait_for(lambda: H.in_edit() is not None, 4000)
    return 1200


@step
def s_in_edit():
    """Check the sketch is on the XY plane, inside the body, Sketcher active."""
    obj = H.in_edit()
    check("New Sketch on XY plane opens the sketch for editing", obj is not None and obj.TypeId == "Sketcher::SketchObject",
          getattr(obj, "TypeId", None))
    if obj is None:
        return
    S["sk"] = obj.Name
    sup = obj.AttachmentSupport
    check("sketch attached to XY_Plane, FlatFace", sup and (sup[0][0].Name.startswith("XY_Plane") or any(x.startswith("XY_Plane") for x in sup[0][1]))
          and obj.MapMode == "FlatFace", f"{[(o.Name, s) for o, s in sup]} {obj.MapMode}")
    check("sketch is inside the body", obj in S["body"].Group)
    wb = Gui.activeWorkbench()
    check("Sketcher workbench active in edit", wb.name() == "SketcherWorkbench", wb.name())


@step
def s_workspace_bar():
    """Expect a SKETCH tab, a FINISH SKETCH button and the Solid toolbars hidden."""
    bar = H.workspace_bar()
    tabs = H.tab_names()
    check("SKETCH tab added to workspace tabs", "Sketch" in tabs, tabs)
    check("SKETCH tab is current", bar is not None and bar.current_tab() == "Sketch", bar.current_tab() if bar else None)
    check("workspace button still says DESIGN", bar is not None and "DESIGN" in bar.button.text(), bar.button.text())
    check("FINISH SKETCH button visible", bar is not None and bar.finish.isVisible() and bar.finish.isEnabled())
    solid = [t.objectName() for t in H.visible_toolbars() if t.objectName().startswith("Solid ·")]
    check("Solid toolbars hidden while sketching", not solid, solid)


@step
def s_toolbars():
    """Check the sketch toolbars and their buttons are there, visible, enabled, with icons."""
    for name in SKETCH_TOOLBARS:
        tb = H.toolbar(name)
        check(f"toolbar '{name}' visible", tb is not None and tb.isVisible())
    for tbname, wanted, must_enable in (("Geometries", GEOMETRY_BUTTONS, True),
                                        ("Constraints", CONSTRAINT_BUTTONS, True),
                                        ("Sketcher Tools", TOOL_BUTTONS, True),
                                        ("Edit Mode", EDIT_BUTTONS, True)):
        tb = H.toolbar(tbname)
        if tb is None:
            check(f"{tbname}: buttons", False, "toolbar missing")
            continue
        have = {n: (a, w) for n, a, w in H.toolbar_buttons(tb)}
        missing = [n for n in wanted if n not in have]
        check(f"{tbname}: expected buttons present", not missing, f"missing {missing}")
        hidden = [n for n in wanted if n in have and (have[n][1] is None or not have[n][1].isVisible()
                                                     or have[n][1].width() < 8)]
        check(f"{tbname}: buttons visible on screen", not hidden, hidden)
        if must_enable:
            off = [n for n in wanted if n in have and not have[n][0].isEnabled()]
            check(f"{tbname}: buttons enabled", not off, off)
        noicon = [n for n in wanted if n in have and have[n][0].icon().isNull()]
        check(f"{tbname}: buttons have icons", not noicon, noicon)
    # Toolbars the workspace definition lists but that are not shown (info only).
    for name in ("B-Spline Tools", "Sketcher Edit Tools"):
        tb = H.toolbar(name)
        note(f"toolbar {name!r}: exists={tb is not None} visible={tb.isVisible() if tb else None}")
    # Each sketch toolbar should sit in the second toolbar row with a group caption.
    try:
        import bruceworx_groups

        caps = {n: bruceworx_groups.caption_for(n) for n in SKETCH_TOOLBARS}
        note(f"captions: {caps}")
    except Exception as exc:
        note(f"captions: {exc}")


@step
def s_grid_nav():
    """Check the grid, its colours, the view normal to the sketch and the nav pill."""
    import bruceworx_grid

    sk = sketch()
    vo = sk.ViewObject
    check("sketch grid shown in edit", getattr(vo, "ShowGrid", False))
    prefs = App.ParamGet(bruceworx_grid.PREFS_PATH)
    dark = bruceworx_grid._is_dark()
    want = bruceworx_grid.colors(prefs.GetInt(bruceworx_grid.PREF_NAME, bruceworx_grid.DEFAULT_STRENGTH), dark)
    sp = App.ParamGet(bruceworx_grid.SKETCHER_PATH)
    got = (sp.GetUnsigned("GridLineColor", 0), sp.GetUnsigned("GridDivLineColor", 0))
    check("BruceWorx grid colours applied for this theme", got == want,
          f"got {[hex(x) for x in got]} want {[hex(x) for x in want]} dark={dark}")
    v = Gui.ActiveDocument.ActiveView
    d = v.getViewDirection()
    check("view looks straight at the sketch (normal to XY)", abs(d.z) > 0.999, d)
    pill = mw.findChild(QtWidgets.QWidget, "BruceWorxNavPill")
    check("navigation pill visible in sketch", pill is not None and pill.isVisible())
    for cmd in ("Std_ViewFitAll", "Std_ViewFitSelection"):
        a = H.action_for(cmd)
        check(f"{cmd} enabled in sketch", a is not None and a.isEnabled())
    note(f"status bar view size in sketch: {H.view_size_text()} (before New Sketch: {S.get('model_view')})")
    # Stray widgets at the top-left of the 3D view (a clipped 'E' shows there in screenshots).
    vw = H.view_widget()
    stray = []
    for w in mw.findChildren(QtWidgets.QWidget):
        if w.isVisible() and vw is not None and w is not vw and not w.isAncestorOf(vw):
            g = w.mapTo(mw, QtCore.QPoint(0, 0)) - vw.mapTo(mw, QtCore.QPoint(0, 0))
            if 0 <= g.x() < 300 and 0 <= g.y() < 60 and w.width() < 40 and w.height() < 40:
                stray.append(f"{w.metaObject().className()} {w.objectName()!r} {g.x()},{g.y()} {w.width()}x{w.height()}"
                             f" text={getattr(w, 'text', lambda: '')()!r}")
    note(f"small widgets near the view's top-left: {stray}")


@step
def s_task_panel():
    """Check the sketch task panel sits in the right panel and is readable."""
    tv = H.task_view()
    right = H.overlay("OverlayRight")
    check("task panel shows", tv is not None and tv.isVisible())
    check("task panel is in the right panel", right is not None and tv is not None and right.isAncestorOf(tv))
    if right is not None:
        title = right.tabText(right.currentIndex())
        check("right panel's current tab is Tasks", title == "Tasks", title)
        # texts in the right panel outside the task view (header / title row)
        heads = [l.text() for l in right.findChildren(QtWidgets.QLabel)
                 if l.isVisible() and l.text() and not (tv and tv.isAncestorOf(l))]
        note(f"right panel header texts: {heads}")
        if heads:
            check("right panel header names the current tab", title in heads or not any(
                t in heads for t in ("Properties", "Vault", "Parameters", "Physical")), heads)
    texts = H.labels_text(tv) if tv else []
    check("task panel says 'Empty sketch'", "Empty sketch" in texts, texts)
    boxes = [w for w in tv.findChildren(QtWidgets.QWidget) if w.metaObject().className() in (
        "SketcherGui::TaskSketcherMessages", "SketcherGui::TaskSketcherConstraints",
        "SketcherGui::TaskSketcherElements")] if tv else []
    check("task panel has messages, constraints and elements boxes", len(boxes) >= 3,
          [b.metaObject().className() for b in boxes])
    if tv is not None:
        H.check_readable("task panel text readable", tv)
        if right is not None:
            ok = tv.width() >= 240
            check("task panel wide enough (>= 240 px)", ok, tv.width())
    H.grab("edit-empty")


def tool_widget_texts():
    tv = H.task_view()
    return H.labels_text(tv) if tv else []


def draw(cmd, fracs, expect, end_with_escape=True):
    sk = sketch()
    before = sk.GeometryCount
    a = H.action_for(cmd)
    Gui.runCommand(cmd)
    H.pump(250)
    tool_box = [w for w in H.task_view().findChildren(QtWidgets.QWidget)
                if w.metaObject().className() == "SketcherGui::TaskSketcherTool" and w.isVisible()]
    check(f"{cmd}: tool settings box appears in task panel", bool(tool_box))
    for fx, fy in fracs:
        H.click_frac(fx, fy)
    H.pump(250)
    S["doc"].recompute()
    got = sketch().GeometryCount - before
    check(f"{cmd}: mouse clicks draw {expect} geometr{'y' if expect == 1 else 'ies'}", got == expect, f"added {got}")
    if end_with_escape:
        H.escape()
    check(f"{cmd}: Escape ends the tool, sketch stays open", H.in_edit() is not None)
    return got


@step
def s_draw_line():
    """Line tool: two clicks in the view should draw one line."""
    S["counts"] = []
    S["counts"].append(draw("Sketcher_CreateLine", [(0.20, 0.30), (0.40, 0.35)], 1))


@step
def s_draw_rect():
    """Rectangle tool: two clicks should draw four lines."""
    S["counts"].append(draw("Sketcher_CreateRectangle", [(0.20, 0.50), (0.35, 0.75)], 4))


@step
def s_view_scale():
    """Measure how big the sketch view is (a new sketch should not open at micrometre zoom)."""
    # The rectangle spans 15 % of the view's width: how big is it in the sketch?
    sk = sketch()
    xs = [g.StartPoint.x for g in sk.Geometry[1:5]] + [g.EndPoint.x for g in sk.Geometry[1:5]]
    w = max(xs) - min(xs) if len(xs) == 8 else 0
    S["view_w"] = w / 0.15
    check("a new sketch opens zoomed to a usable size (view >= 20 mm wide)", S["view_w"] >= 20,
          f"view about {S['view_w']:.2f} mm wide; a rectangle across 15% of it is {w:.3f} mm")


@step
def s_draw_circle():
    """Circle tool: centre + rim click should draw a circle."""
    S["counts"].append(draw("Sketcher_CreateCircle", [(0.55, 0.30), (0.60, 0.30)], 1))


@step
def s_draw_arc():
    """Arc tool: centre, start, end clicks should draw an arc."""
    S["counts"].append(draw("Sketcher_CreateArc", [(0.55, 0.65), (0.62, 0.65), (0.55, 0.55)], 1))


@step
def s_draw_polyline():
    """Polyline tool: three clicks + Escape should draw two lines, sketch stays open."""
    # Polyline: three clicks make two segments; Escape finishes it (and a second Escape ends the tool).
    sk = sketch()
    before = sk.GeometryCount
    Gui.runCommand("Sketcher_CreatePolyline")
    H.pump(250)
    for fx, fy in [(0.72, 0.30), (0.85, 0.32), (0.80, 0.50)]:
        H.click_frac(fx, fy)
    H.escape()
    H.pump(200)
    got = sketch().GeometryCount - before
    check("Sketcher_CreatePolyline: three clicks + Escape draw 2 lines", got == 2, f"added {got}")
    if H.in_edit() is not None and Gui.Control.activeDialog():
        # The polyline tool may still be waiting for a new start: end it.
        tool_box = [w for w in H.task_view().findChildren(QtWidgets.QWidget)
                    if w.metaObject().className() == "SketcherGui::TaskSketcherTool" and w.isVisible()]
        if tool_box:
            H.escape()
    check("polyline: sketch still open after Escape", H.in_edit() is not None)
    tool_box = [w for w in H.task_view().findChildren(QtWidgets.QWidget)
                if w.metaObject().className() == "SketcherGui::TaskSketcherTool" and w.isVisible()]
    check("polyline: tool ended after Escape (no tool box left)", not tool_box)
    H.grab("after-polyline")
    S["counts"].append(got)
    Gui.SendMsgToActiveView("ViewFit")
    return 400


@step
def s_after_draw():
    """Check the panel shows degrees of freedom and lists every edge."""
    sk = sketch()
    bb = sk.Shape.BoundBox
    note(f"drawn geometry spans {bb.XLength:.3f} x {bb.YLength:.3f} mm (view was {S['view_w']:.3f} mm wide)")
    texts = tool_widget_texts()
    check("task panel shows the DoF message after drawing", any("Degrees of Freedom" in t or "constrained" in t.lower()
                                                               for t in texts), texts[:6])
    els = [w for w in H.task_view().findChildren(QtWidgets.QListWidget) if w.metaObject().className() ==
           "SketcherGui::ElementView"]
    if els:
        check("elements list shows each drawn edge", els[0].count() == sk.GeometryCount,
              f"list {els[0].count()} geometry {sk.GeometryCount}")
    H.check_readable("task panel text readable after drawing", H.task_view())
    H.grab("drawn")


@step
def s_undo_redo():
    """Cmd-Z inside the sketch should remove the last segment; Redo puts it back."""
    sk = sketch()
    n = sk.GeometryCount
    names = S["doc"].UndoNames[:3]
    note(f"undo stack top: {names}")
    undo = H.action_for("Std_Undo")
    check("Undo enabled in sketch", undo is not None and undo.isEnabled())
    # One Cmd-Z at a time until the last drawn segment goes; a user expects one.
    steps = []
    for _i in range(4):
        top = S["doc"].UndoNames[0] if S["doc"].UndoNames else None
        Gui.runCommand("Std_Undo")
        H.pump(300)
        steps.append((top, sketch().GeometryCount))
        if sketch().GeometryCount < n:
            break
    S["undos"] = len(steps)
    after_undo = sketch().GeometryCount
    check("Undo inside the sketch removes the last drawn segment", after_undo < n, f"{n} -> {after_undo}: {steps}")
    check("first Undo after drawing removes the drawn segment (not only its auto-constraints)",
          len(steps) == 1, steps)
    check("Undo keeps the sketch open", H.in_edit() is not None)
    redo = H.action_for("Std_Redo")
    check("Redo enabled after undo", redo is not None and redo.isEnabled())
    for _i in range(S["undos"]):
        Gui.runCommand("Std_Redo")
        H.pump(300)
    check("Redo puts it back", sketch().GeometryCount == n, f"{after_undo} -> {sketch().GeometryCount}")
    check("Redo keeps the sketch open", H.in_edit() is not None)


@step
def s_finish():
    """Click FINISH SKETCH."""
    bar = H.workspace_bar()
    bar.finish.click()
    H.wait_for(lambda: H.in_edit() is None, 3000)
    return 1000


@step
def s_after_finish():
    """Expect Design toolbars back, SKETCH tab gone, sketch in the Browser."""
    check("FINISH SKETCH closes the sketch", H.in_edit() is None)
    wb = Gui.activeWorkbench()
    check("back in the Design workspace", wb.name() == "PartDesignWorkbench", wb.name())
    tabs = H.tab_names()
    check("SKETCH tab removed", "Sketch" not in tabs, tabs)
    bar = H.workspace_bar()
    check("FINISH SKETCH button hidden", bar is not None and not bar.finish.isVisible())
    solid = [t.objectName() for t in H.visible_toolbars() if t.objectName().startswith("Solid ·")]
    check("Solid toolbars back", len(solid) >= 5, solid)
    sk_bars = [t.objectName() for t in H.visible_toolbars() if t.objectName() in SKETCH_TOOLBARS]
    check("sketch toolbars gone", not sk_bars, sk_bars)
    S["doc"].recompute()
    sk = sketch()
    check("sketch valid after finishing", "Invalid" not in sk.State and "Error" not in sk.State, sk.State)
    check("task panel closed", not Gui.Control.activeDialog())
    trees = [t for t in mw.findChildren(QtWidgets.QTreeWidget) if t.metaObject().className() == "Gui::TreeWidget"]
    labels = []
    for t in trees:
        it = QtWidgets.QTreeWidgetItemIterator(t)
        while it.value():
            labels.append(it.value().text(0))
            it += 1
    check("sketch listed in the Browser", sk.Label in labels, labels[:10])
    H.grab("finished")


H.run()
