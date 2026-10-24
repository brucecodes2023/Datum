# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx extras around part modelling.

* BruceWorx Rectangular / Circular Pattern on a body (its own task panel),
* the red ERROR chip on a failing feature,
* pick to feature (a picked face highlights the feature that made it),
* history timeline chips (one per feature, double-click edits),
* Std_SetMaterial and the mass that follows from it,
* the right panel's Properties tab for a selected feature (lightly).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import harness as H
import partmodel as M

S = {}
V = App.Vector


def build():
    """Part > Body: plate 40 x 30 x 8, a pocket, a fillet on one corner edge."""
    doc = App.newDocument("Extras")
    doc.openTransaction("Build")
    part = doc.addObject("App::Part", "Part")
    body = doc.addObject("PartDesign::Body", "Body")
    part.addObject(body)
    doc.commitTransaction()
    sk = M.sketch(body, "XY_Plane", name="SketchBase")
    M.rect(sk, -20, -15, 20, 15)
    M.done(sk)
    doc.openTransaction("Pad")
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 8
    pad.Label = "Base plate"
    sk.Visibility = False
    doc.recompute()
    doc.commitTransaction()
    sk2 = M.sketch(body, "XY_Plane", z=8, name="SketchPocket")
    M.circle(sk2, 6, 4, 4)
    M.done(sk2)
    doc.openTransaction("Pocket")
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = sk2
    pocket.Length = 5
    sk2.Visibility = False
    doc.recompute()
    doc.commitTransaction()
    edges = [i for i, p in M.vertical_edges(pocket.Shape, 8) if p.x > 19 and p.y > 14]
    doc.openTransaction("Fillet")
    fil = body.newObject("PartDesign::Fillet", "Fillet")
    fil.Base = (pocket, ["Edge%d" % edges[0]])
    fil.Radius = 3
    doc.recompute()
    doc.commitTransaction()
    S.update(doc=doc, part=part, body=body, pad=pad, pocket=pocket, fillet=fil)
    Gui.ActiveDocument.ActiveView.setActiveObject("part", part)
    Gui.ActiveDocument.ActiveView.setActiveObject("pdbody", body)
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    return doc


@H.test
def t_build():
    Gui.activateWorkbench("PartDesignWorkbench")
    yield 1200
    build()
    yield 1500
    H.expand_trees()
    ok, detail = M.solid_ok(S["body"])
    H.check("model.valid", ok, detail)
    H.grab_window("model")


# ----- timeline -------------------------------------------------------------------------------


def timeline():
    dock = H.mw().findChild(QtWidgets.QDockWidget, "HistoryTimelineDock")
    bar = dock.widget() if dock is not None else None
    return dock, bar


@H.test
def t_timeline():
    dock, bar = timeline()
    H.check("timeline.dock_shown", dock is not None and dock.isVisible(), "HistoryTimelineDock missing or hidden")
    if bar is None or not hasattr(bar, "button_widget"):
        return
    yield 800
    for key in ("pad", "pocket", "fillet"):
        obj = S[key]
        chip = bar.button_widget(obj.Name)
        H.check(f"timeline.chip.{key}", chip is not None and chip.isVisible(), f"no visible chip for {obj.Name}")
        if chip is not None:
            ok, detail = H.fully_visible(chip)
            H.check(f"timeline.chip_readable.{key}", ok, detail)
    # Order of chips = order in the body.
    chips = [w for w in bar.findChildren(QtWidgets.QWidget) if w.objectName().startswith("HistoryTimeline_") and w.isVisible()]
    chips.sort(key=lambda w: w.mapTo(bar, QtCore.QPoint(0, 0)).x())
    order = [w.objectName()[len("HistoryTimeline_"):] for w in chips]
    feats = [n for n in order if n in (S["pad"].Name, S["pocket"].Name, S["fillet"].Name)]
    H.check("timeline.order", feats == [S["pad"].Name, S["pocket"].Name, S["fillet"].Name], f"order={order}")
    H.grab_widget(dock, "timeline")
    # Double-click the Pocket chip: its dialog opens.
    chip = bar.button_widget(S["pocket"].Name)
    if chip is not None:
        pos = chip.rect().center()
        for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                     QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.MouseButtonRelease):
            from PySide import QtGui
            ev = QtGui.QMouseEvent(kind, QtCore.QPointF(pos), QtCore.QPointF(chip.mapToGlobal(pos)),
                                   QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(chip, ev)
        yield 1800
        vp = Gui.ActiveDocument.getInEdit()
        H.check("timeline.double_click_edits", H.dialog_open() and getattr(vp, "Object", None) is S["pocket"],
                f"dialog={bool(H.dialog_open())} in edit={getattr(getattr(vp, 'Object', None), 'Name', None)}")
        H.grab_window("timeline-edit")
        H._cleanup_dialog()
        yield 1000


# ----- pick to feature ------------------------------------------------------------------------


def pick(sub):
    """A pick in the 3D view on the body inside the Part: (Part, "Body.FaceN")."""
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, S["part"].Name, f"{S['body'].Name}.{sub}")


def traced():
    import bruceworx_picktrace as pt
    keys = {ov.traced for ov in pt.overlays()}
    return keys


@H.test
def t_picktrace():
    import bruceworx_picktrace as pt
    shape = S["body"].Shape
    cases = []
    cyl = [i for i in M.faces_of_kind(shape, "Cylinder") if abs(shape.Faces[i - 1].Surface.Radius - 4) < 1e-6]
    if cyl:
        cases.append(("pocket_wall", f"Face{cyl[0]}", S["pocket"]))
    fil = [i for i in M.faces_of_kind(shape, "Cylinder") if abs(shape.Faces[i - 1].Surface.Radius - 3) < 1e-6]
    if fil:
        cases.append(("fillet_face", f"Face{fil[0]}", S["fillet"]))
    bottom = M.planar_faces(shape, V(0, 0, -1))
    if bottom:
        cases.append(("pad_bottom", f"Face{bottom[0]}", S["pad"]))
    H.check("picktrace.faces_found", len(cases) == 3, [c[0] for c in cases])
    dock, bar = timeline()
    for name, sub, want in cases:
        pick(sub)
        yield 900
        key = (S["doc"].Name, want.Name)
        H.check(f"picktrace.{name}.browser", key in traced(), f"traced={traced()} want {want.Name}")
        chip = bar.button_widget(want.Name) if bar is not None and hasattr(bar, "button_widget") else None
        H.check(f"picktrace.{name}.timeline_ring", chip is not None and bool(chip.property("traced")),
                f"chip traced={chip.property('traced') if chip else None}")
        H.check(f"picktrace.{name}.trace_fn", pt.trace_selection() is want,
                f"trace_selection={getattr(pt.trace_selection(), 'Name', None)}")
        left = H.overlay("OverlayLeft")
        if left is not None:
            H.grab_widget(left, f"trace-{name}")
    Gui.Selection.clearSelection()
    yield 600
    H.check("picktrace.cleared", not any(traced() - {None}), f"traced={traced()}")


# ----- error chip -----------------------------------------------------------------------------


@H.test
def t_error_chip():
    doc, fil = S["doc"], S["fillet"]
    doc.openTransaction("Fillet.Radius")
    fil.Radius = 40  # far too big for an 8 mm plate
    doc.recompute()
    doc.commitTransaction()
    yield 1200
    import bruceworx_picktrace as pt
    H.check("error.feature_failed", pt.obj_in_error(fil), f"State={fil.State}")
    info = H.row_info(fil)
    badges = list(info[1]) if info else []
    H.check("error.chip", ("ERROR", "error") in [tuple(b) for b in badges], f"row info={info}")
    others = [o.Label for o in (S["pad"], S["pocket"]) if ("ERROR", "error") in [tuple(b) for b in (H.row_info(o) or ("", ()))[1]]]
    H.check("error.only_the_failed_row", not others, f"chips also on {others}")
    left = H.overlay("OverlayLeft")
    if left is not None:
        H.grab_widget(left, "error-chip")
    # Clicking the chip opens the feature, like double-clicking the row.
    t, item = H.tree_item(fil)
    opened = False
    if item is not None:
        t.scrollToItem(item)
        yield 300
        ov = [o for o in pt.overlays() if o.tree is t]
        rect = t.visualItemRect(item)
        # The chip is drawn at the right of the row text; scan along the row for it.
        for x in range(rect.left() + 20, t.viewport().width() - 4, 6):
            pos = QtCore.QPoint(x, rect.center().y())
            if ov and ov[0]._error_object(pos) is not None:
                ov[0].chip_click(pos)
                opened = True
                break
    yield 1500
    vp = Gui.ActiveDocument.getInEdit()
    H.check("error.chip_click_opens", opened and H.dialog_open() and getattr(vp, "Object", None) is fil,
            f"chip found={opened} dialog={bool(H.dialog_open())}")
    if H.dialog_open():
        H.grab_window("error-chip-open")
        H._cleanup_dialog()
        yield 1000
    Gui.runCommand("Std_Undo")
    yield 1200
    fil = doc.getObject("Fillet")
    H.check("error.undo_restores_radius", fil is not None and abs(fil.Radius.Value - 3) < 1e-6,
            f"State={getattr(fil, 'State', None)} Radius={getattr(fil, 'Radius', None)}")
    info = H.row_info(fil)
    H.check("error.chip_gone_after_undo", info is not None and ("ERROR", "error") not in [tuple(b) for b in info[1]],
            f"after Undo (no recompute): {info} State={getattr(fil, 'State', None)}")
    Gui.runCommand("Std_Refresh")  # Recompute, as the user would next
    yield 1200
    fil = doc.getObject("Fillet")
    info = H.row_info(fil)
    H.check("error.chip_gone_after_recompute", fil is not None and not pt.obj_in_error(fil) and
            ("ERROR", "error") not in [tuple(b) for b in info[1]], f"{info}")


# ----- material and mass ----------------------------------------------------------------------


def _find_material(tree, words):
    model = tree.model()

    def walk(parent):
        for r in range(model.rowCount(parent)):
            idx = model.index(r, 0, parent)
            text = str(model.data(idx))
            uuid = model.data(idx, QtCore.Qt.UserRole)
            if uuid and any(w.lower() in text.lower() for w in words):
                return idx
            found = walk(idx)
            if found is not None:
                return found
        return None

    return walk(QtCore.QModelIndex())


@H.test
def t_material_mass():
    doc, part, body = S["doc"], S["part"], S["body"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, f"{body.Name}.")
    yield 600
    a = H.action_for("Std_SetMaterial")
    H.say("Std_SetMaterial action", a, a.isEnabled() if a else None)
    Gui.runCommand("Std_SetMaterial")
    yield 1800
    H.check("material.dialog_opens", H.dialog_open(), "no task dialog")
    ok, detail = H.task_in_right_panel()
    H.check("material.dialog_in_right_panel", ok, detail)
    ok, detail = H.fully_visible(H.task_button("close"))
    H.check("material.close_button_readable", ok, detail)
    H.grab_window("material-dialog")
    tv = H.task_view()
    widget = H.by_class("MatGui::MaterialTreeWidget", tv) if tv else []
    H.check("material.tree_widget", bool(widget), "no MaterialTreeWidget")
    if widget:
        trees = widget[0].findChildren(QtWidgets.QTreeView)
        idx = _find_material(trees[0], ["Steel"]) if trees else None
        H.check("material.steel_listed", idx is not None, "no Steel material in the list")
        if idx is not None:
            S["material_text"] = str(trees[0].model().data(idx))
            # A click: the tree's selection is what applies the material (a double-click only names it).
            trees[0].scrollTo(idx)
            trees[0].selectionModel().select(idx, QtCore.QItemSelectionModel.ClearAndSelect)
            trees[0].setCurrentIndex(idx)
            yield 1000
    H.click_task("close")
    yield 1200
    H.check("material.dialog_closed", not H.dialog_open(), "still open")
    H._cleanup_dialog()
    name = body.ShapeMaterial.Name
    H.check("material.set_on_body", "steel" in name.lower(), f"ShapeMaterial={name!r} picked={S.get('material_text')!r}")
    from bruceworx.partprops import computed_mass
    mass = computed_mass(part)
    kg = App.Units.Quantity(mass).getValueAs("kg").Value if mass else 0
    expect = body.Shape.Volume * 7.8e-6  # steel, ~7800 kg/m^3
    H.check("mass.computed", kg > 0 and abs(kg - expect) / expect < 0.1, f"mass={mass} expect≈{expect:.4f} kg")
    if "Mass" in part.PropertiesList:
        H.check("mass.part_property", part.Mass.getValueAs("kg").Value > 0, f"Part.Mass={part.Mass}")
    else:
        H.say("Part has no Mass property (part properties not applied to a new Part)")
    try:
        import bruceworx_inspector as bi
        facts = dict(bi.body_facts(part))
        H.check("mass.shown_in_facts", "Mass" in facts and "kg" in facts["Mass"] or "g" in facts.get("Mass", ""),
                f"facts={facts}")
    except Exception as exc:
        H.say("body_facts", exc)
    H.say("undo after material", doc.UndoNames[:3])
    H.check("material.one_undo_step", doc.UndoNames and "aterial" in doc.UndoNames[0], f"undo top={doc.UndoNames[:2]}")


# ----- right panel Properties (lightly) -----------------------------------------------------


@H.test
def t_properties_tab():
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(S["doc"].Name, S["part"].Name, f"{S['body'].Name}.{S['pocket'].Name}.")
    yield 1500
    right = H.overlay("OverlayRight")
    title = right.tabText(right.currentIndex()) if right is not None else None
    H.check("properties.comes_forward", title == "Properties", f"current tab={title!r}")
    page = right
    texts = []
    if page is not None:
        for w in page.findChildren(QtWidgets.QLabel) + page.findChildren(QtWidgets.QAbstractButton):
            if w.isVisible() and w.text():
                texts.append(w.text())
    H.say("properties texts", texts[:40])
    H.check("properties.not_placeholder", page is not None and not any("Coming in this build" in t for t in texts),
            f"texts={texts[:8]}")
    H.check("properties.shows_feature", any("Pocket" in t for t in texts) and any(t in ("Depth", "Length") for t in texts),
            f"texts={texts[:12]}")
    if right is not None:
        H.grab_widget(right, "properties")
    H.grab_window("properties")


# ----- BruceWorx patterns on a body -------------------------------------------------------------


def _group(title):
    tv = H.task_view()
    for g in tv.findChildren(QtWidgets.QGroupBox) if tv else []:
        if g.title() == title and g.isVisible():
            return g
    return None


@H.test
def t_bw_pattern_body():
    doc, part, body = S["doc"], S["part"], S["body"]
    import bruceworx_pattern as bp
    H.hide_toasts()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, f"{body.Name}.")
    before = {o.Name for o in doc.Objects}
    Gui.runCommand("BruceWorx_RectangularPattern")
    yield 1800
    H.check("bwrect.dialog_opens", H.dialog_open(), "no task dialog")
    ok, detail = H.task_in_right_panel()
    H.check("bwrect.dialog_in_right_panel", ok, detail)
    for which in ("ok", "cancel"):
        ok, detail = H.fully_visible(H.task_button(which))
        H.check(f"bwrect.{which}_button_readable", ok, detail)
    arrays = [o for o in doc.Objects if o.Name not in before and bp.is_pattern(o)]
    H.check("bwrect.pattern_created", len(arrays) == 1, [o.Name for o in arrays])
    g = _group("Direction 1")
    H.check("bwrect.direction_group", g is not None, "no Direction 1 group")
    if g is not None:
        g.findChildren(QtWidgets.QDoubleSpinBox)[0].setValue(50)
        g.findChildren(QtWidgets.QSpinBox)[0].setValue(3)
        yield 1200
    H.grab_window("bwrect-dialog")
    H.click_task("ok")
    seen = []
    for _ in range(15):
        yield 100
        seen += [t for t in H.toast_texts() if t not in seen]
    H.check("bwrect.dialog_closed", not H.dialog_open())
    H._cleanup_dialog()
    if not arrays:
        return
    name = arrays[0].Name
    arr = doc.getObject(name)
    H.check("bwrect.instances", arr is not None and arr.ElementCount == 2, f"3 asked (seed + 2 copies): ElementCount={getattr(arr, 'ElementCount', None)}")
    H.check("bwrect.toast", any(t.strip() for t in seen), f"toasts={seen} undo={doc.UndoNames[:1]}")
    H.say("bwrect toast", seen, doc.UndoNames[:2])
    H.check("bwrect.browser_row", arr is not None and H.tree_item(arr)[1] is not None, "no Browser row")
    H.say("bwrect row info", H.row_info(arr) if arr else None)
    H.grab_window("bwrect-done")
    Gui.runCommand("Std_Undo")
    yield 1200
    H.check("bwrect.undo_removes", doc.getObject(name) is None, "pattern still there after undo")
    Gui.runCommand("Std_Redo")
    yield 1200
    arr = doc.getObject(name)
    H.check("bwrect.redo_restores", arr is not None and arr.ElementCount == 2, f"{arr}")
    if arr is None:
        return
    # Edit again: double-click its row (what a user tries first), then select + command.
    Gui.Selection.clearSelection()
    H.expand_trees()
    yield 300
    H.double_click_row(arr)
    yield 1500
    H.check("bwrect.double_click_edits", bool(H.dialog_open()), "double-click on the pattern row opened nothing")
    H._cleanup_dialog()
    yield 800
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, f"{arr.Name}.")
    Gui.runCommand("BruceWorx_RectangularPattern")
    yield 1500
    H.check("bwrect.reselect_edits", bool(H.dialog_open()), "selecting the pattern + command opened nothing")
    g = _group("Direction 1")
    if g is not None:
        H.check("bwrect.edit_shows_values", g.findChildren(QtWidgets.QSpinBox)[0].value() == 3,
                f"count={g.findChildren(QtWidgets.QSpinBox)[0].value()}")
    H.click_task("cancel")
    yield 1200
    H._cleanup_dialog()
    arr = doc.getObject(name)
    H.check("bwrect.cancel_keeps", arr is not None and arr.ElementCount == 2, f"{getattr(arr, 'ElementCount', None)}")
    # Circular on the same body: own panel, Circular kind shown.
    Gui.runCommand("Std_Undo")  # take the rectangular one out first
    yield 1200
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name, f"{body.Name}.")
    before = {o.Name for o in doc.Objects}
    Gui.runCommand("BruceWorx_CircularPattern")
    yield 1800
    H.check("bwcirc.dialog_opens", H.dialog_open(), "no task dialog")
    axis = _group("Axis")
    H.check("bwcirc.axis_group", axis is not None, "no Axis group (circular controls)")
    if axis is not None:
        axis.findChildren(QtWidgets.QSpinBox)[0].setValue(4)
        yield 1200
    H.grab_window("bwcirc-dialog")
    H.click_task("ok")
    yield 1500
    H._cleanup_dialog()
    arrays = [o for o in doc.Objects if o.Name not in before and bp.is_pattern(o)]
    H.check("bwcirc.instances", len(arrays) == 1 and arrays[0].ElementCount == 3,
            f"{[(o.Name, o.ElementCount) for o in arrays]}")


H.start()
