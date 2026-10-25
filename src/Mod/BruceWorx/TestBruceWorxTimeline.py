# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests for the history timeline (package/bruceworx/mods/HistoryTimeline): marker drag
through synthetic mouse events on the bar, playback, the context-menu actions and reorder.

Runs under package/bruceworx/tests/run_gui_tests.py (a GUI FreeCAD); the headless checks are
tests/test_timeline_access.py and tests/test_assembly_timeline.py in the mod."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets


def _say(text):
    try:
        import bruceworx_testwindow as tw

        tw.say(text)
    except Exception:
        pass


def _pump(ms=0):
    QtWidgets.QApplication.processEvents()
    if ms:
        deadline = QtCore.QTime.currentTime().addMSecs(ms)
        while QtCore.QTime.currentTime() < deadline:
            QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()


def _mouse(widget, kind, local, button=QtCore.Qt.LeftButton, buttons=None):
    """Send one mouse event to ``widget`` at ``local`` (a QPoint in widget coordinates)."""
    if buttons is None:
        buttons = button if kind != QtCore.QEvent.MouseButtonRelease else QtCore.Qt.NoButton
    local_f = QtCore.QPointF(local)
    global_f = QtCore.QPointF(widget.mapToGlobal(local))
    event = QtGui.QMouseEvent(kind, local_f, global_f, button, buttons, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, event)
    _pump()


def build_primitive_body(doc, name="Body"):
    """Box, Cylinder, Sphere: three solids, no sketches."""
    body = doc.addObject("PartDesign::Body", name)
    box = doc.addObject("PartDesign::AdditiveBox", "Box")
    box.Length = box.Width = box.Height = 10
    body.addObject(box)
    cyl = doc.addObject("PartDesign::AdditiveCylinder", "Cylinder")
    cyl.Radius = 2
    cyl.Height = 20
    body.addObject(cyl)
    sphere = doc.addObject("PartDesign::AdditiveSphere", "Sphere")
    sphere.Radius = 5
    body.addObject(sphere)
    doc.recompute()
    return body


class TimelineGuiCase(unittest.TestCase):
    """A document with one body, the body active, the timeline dock showing it."""

    def setUp(self):
        from history_timeline import dock as hd

        self.hd = hd
        self.doc = App.newDocument("TimelineGui")
        self.body = self.make_body(self.doc)
        view = Gui.ActiveDocument.ActiveView
        view.setActiveObject("pdbody", self.body)
        hd.set_enabled(True)
        self.dock = hd._state["dock"]
        self.dock.refresh()
        _pump(50)
        self.bar = self.dock._bar

    def tearDown(self):
        App.closeDocument(self.doc.Name)
        _pump()

    def make_body(self, doc):
        return build_primitive_body(doc)

    # helpers -------------------------------------------------------------------------------

    def chip(self, name):
        button = self.bar.button_widget(name)
        self.assertIsNotNone(button, f"no chip for {name}")
        return button

    def row_x_before(self, name):
        """Row x just left of chip ``name``."""
        return self.chip(name).x() - 2

    def row_x_after(self, name):
        button = self.chip(name)
        return button.x() + button.width() + 2

    def drag_marker(self, row_x, cancel=False):
        """Press the marker, move it to ``row_x`` (row coordinates), release (or Escape)."""
        marker = self.bar.marker_widget()
        self.assertIsNotNone(marker, "the body timeline has no marker")
        row = self.bar.row_widget()
        start = QtCore.QPoint(marker.width() // 2, marker.height() // 2)
        _mouse(marker, QtCore.QEvent.MouseButtonPress, start)
        self.assertTrue(marker.is_dragging())
        target_global = row.mapToGlobal(QtCore.QPoint(int(row_x), row.height() // 2))
        local = marker.mapFromGlobal(target_global)
        _mouse(marker, QtCore.QEvent.MouseMove, local)
        if cancel:
            key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(marker, key)
            _pump()
        else:
            _mouse(marker, QtCore.QEvent.MouseButtonRelease, local)
        self.assertFalse(marker.is_dragging())
        _pump(50)


class TestMarkerDrag(TimelineGuiCase):
    def test_bar_shows_the_body_with_the_marker_after_the_tip(self):
        self.assertEqual(self.body.Tip.Name, "Sphere")
        for name in ("Box", "Cylinder", "Sphere"):
            self.assertIsNotNone(self.bar.button_widget(name))
        marker = self.bar.marker_widget()
        row = self.bar._row
        self.assertEqual(row.indexOf(marker), row.indexOf(self.chip("Sphere")) + 1)
        self.assertEqual(self.bar.preview_tip_name(), "Sphere")

    def test_tip_for_x_walks_the_chips(self):
        self.assertIsNone(self.bar.tip_for_x(self.row_x_before("Box")))
        self.assertEqual(self.bar.tip_for_x(self.row_x_after("Box")), "Box")
        self.assertEqual(self.bar.tip_for_x(self.row_x_after("Cylinder")), "Cylinder")
        self.assertEqual(self.bar.tip_for_x(self.row_x_after("Sphere") + 500), "Sphere")

    def test_drag_rolls_back_and_forward(self):
        _say("Timeline: drag the marker back to after Box, expect Body.Tip = Box")
        undo_before = self.doc.UndoCount
        volume_full = self.body.Shape.Volume
        self.drag_marker(self.row_x_after("Box"))
        self.assertEqual(self.body.Tip.Name, "Box")
        self.assertAlmostEqual(self.body.Shape.Volume, 1000.0, places=3)
        self.assertEqual(self.doc.UndoCount, undo_before + 1, "one undo step per drag")
        self.assertTrue(self.bar.button_widget("Sphere").entry.after_tip)
        self.assertFalse(self.bar.button_widget("Box").entry.after_tip)
        # Only the tip is shown, as Set tip leaves it.
        self.assertTrue(self.doc.getObject("Box").Visibility)
        self.assertFalse(self.doc.getObject("Sphere").Visibility)

        _say("Timeline: drag the marker forward to the end, expect Body.Tip = Sphere")
        self.drag_marker(self.row_x_after("Sphere") + 300)
        self.assertEqual(self.body.Tip.Name, "Sphere")
        self.assertAlmostEqual(self.body.Shape.Volume, volume_full, places=3)
        self.assertEqual(self.doc.UndoCount, undo_before + 2)

    def test_drag_to_the_front_clears_the_tip(self):
        self.drag_marker(self.row_x_before("Box"))
        self.assertIsNone(self.body.Tip)
        self.assertTrue(self.body.Shape.isNull())
        self.assertTrue(all(b.entry.after_tip for b in self.bar._buttons.values()))
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(self.body.Tip.Name, "Sphere")

    def test_escape_restores_the_tip_without_an_undo_step(self):
        undo_before = self.doc.UndoCount
        self.drag_marker(self.row_x_after("Box"), cancel=True)
        self.assertEqual(self.body.Tip.Name, "Sphere")
        self.assertEqual(self.doc.UndoCount, undo_before)
        self.assertFalse(self.bar.button_widget("Sphere").entry.after_tip)
        self.assertTrue(self.doc.getObject("Sphere").Visibility)

    def test_drop_on_the_same_place_adds_no_undo_step(self):
        undo_before = self.doc.UndoCount
        self.drag_marker(self.row_x_after("Sphere") + 100)
        self.assertEqual(self.body.Tip.Name, "Sphere")
        self.assertEqual(self.doc.UndoCount, undo_before)

    def test_live_preview_sets_the_tip_as_the_marker_moves(self):
        self.assertTrue(len(self.body.Group) <= self.hd.LIVE_PREVIEW_MAX)
        marker = self.bar.marker_widget()
        row = self.bar.row_widget()
        _mouse(marker, QtCore.QEvent.MouseButtonPress, QtCore.QPoint(3, 10))
        self.assertTrue(self.dock.drag_is_live())
        target = marker.mapFromGlobal(row.mapToGlobal(QtCore.QPoint(self.row_x_after("Cylinder"), 10)))
        _mouse(marker, QtCore.QEvent.MouseMove, target)
        self.assertEqual(self.body.Tip.Name, "Cylinder", "the tip follows the marker before the drop")
        key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(marker, key)
        _pump(50)
        self.assertEqual(self.body.Tip.Name, "Sphere")

    def test_big_body_waits_for_the_drop(self):
        self.hd.LIVE_PREVIEW_MAX, saved = 2, self.hd.LIVE_PREVIEW_MAX
        try:
            marker = self.bar.marker_widget()
            row = self.bar.row_widget()
            _mouse(marker, QtCore.QEvent.MouseButtonPress, QtCore.QPoint(3, 10))
            self.assertFalse(self.dock.drag_is_live())
            target = marker.mapFromGlobal(row.mapToGlobal(QtCore.QPoint(self.row_x_after("Box"), 10)))
            _mouse(marker, QtCore.QEvent.MouseMove, target)
            self.assertEqual(self.body.Tip.Name, "Sphere", "no recompute while dragging a big body")
            self.assertTrue(self.bar.button_widget("Cylinder").entry.after_tip, "the chips dim anyway")
            _mouse(marker, QtCore.QEvent.MouseButtonRelease, target)
            _pump(50)
            self.assertEqual(self.body.Tip.Name, "Box")
        finally:
            self.hd.LIVE_PREVIEW_MAX = saved


class TestPlayback(TimelineGuiCase):
    def press(self, name):
        strip = self.bar.playback_widget()
        self.assertTrue(strip.isVisibleTo(self.bar), "playback strip hidden for a body")
        button = strip.buttons[name]
        self.assertTrue(button.isEnabled(), f"{name} disabled")
        button.click()
        _pump(50)

    def test_buttons_step_through_the_history(self):
        _say("Timeline: playback back, back, forward, to end, to start")
        undo_before = self.doc.UndoCount
        self.press("back")
        self.assertEqual(self.body.Tip.Name, "Cylinder")
        self.press("back")
        self.assertEqual(self.body.Tip.Name, "Box")
        self.press("forward")
        self.assertEqual(self.body.Tip.Name, "Cylinder")
        self.press("end")
        self.assertEqual(self.body.Tip.Name, "Sphere")
        self.press("start")
        self.assertIsNone(self.body.Tip)
        self.assertEqual(self.doc.UndoCount, undo_before + 5)
        strip = self.bar.playback_widget()
        self.assertFalse(strip.buttons["start"].isEnabled())
        self.assertFalse(strip.buttons["back"].isEnabled())
        self.assertTrue(strip.buttons["forward"].isEnabled())
        self.press("forward")
        self.assertEqual(self.body.Tip.Name, "Box")
        self.assertTrue(self.doc.getObject("Box").Visibility)

    def test_end_buttons_disabled_at_the_end(self):
        strip = self.bar.playback_widget()
        self.assertFalse(strip.buttons["end"].isEnabled())
        self.assertFalse(strip.buttons["forward"].isEnabled())
        self.assertTrue(strip.buttons["back"].isEnabled())
        for name, button in strip.buttons.items():
            self.assertFalse(button.icon().isNull(), f"{name} has no icon")

    def test_hidden_for_an_assembly(self):
        _say("Timeline: an assembly shows creation order, no playback and no marker")
        asm_doc = App.newDocument("TimelineAsm")
        try:
            assembly = asm_doc.addObject("Assembly::AssemblyObject", "Assembly")
            assembly.newObject("Assembly::JointGroup", "Joints")
            assembly.newObject("Part::Box", "Base")
            asm_doc.recompute()
            self.dock.refresh()
            _pump(50)
            self.assertEqual(self.dock._kind, "assembly")
            self.assertFalse(self.bar.playback_widget().isVisibleTo(self.bar))
            self.assertIsNone(self.bar.marker_widget())
        finally:
            App.closeDocument(asm_doc.Name)
            _pump()


def _answer_message_boxes(button, log):
    """Click ``button`` (a QMessageBox.StandardButton) on any message box that opens
    within the next second or so; records the titles seen."""

    def tick():
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible():
                log.append(widget.windowTitle())
                found = widget.button(button)
                if found is not None:
                    found.click()
                else:
                    widget.reject()

    timer = QtCore.QTimer()
    timer.setInterval(40)
    timer.timeout.connect(tick)
    timer.start()
    return timer


class TestContextMenu(TimelineGuiCase):
    def menu(self, name):
        body, feature = self.dock._feature(name)
        menu = self.dock.build_menu(name, body, feature)
        return menu, {a.objectName(): a for a in menu.actions() if a.objectName()}

    def test_menu_has_fusion_entries(self):
        menu, actions = self.menu("Cylinder")
        self.assertEqual(
            [a.objectName() for a in menu.actions() if a.objectName()],
            ["edit", "rename", "suppress", "roll", "delete", "earlier", "later"],
        )
        self.assertTrue(actions["roll"].isEnabled())
        self.assertEqual(actions["suppress"].text(), "Suppress feature")

    def test_roll_to_here_and_suppress(self):
        _say("Timeline menu: Roll history to here on Box, then Suppress Cylinder")
        _menu, actions = self.menu("Box")
        actions["roll"].trigger()
        _pump(50)
        self.assertEqual(self.body.Tip.Name, "Box")
        _menu, actions = self.menu("Cylinder")
        actions["suppress"].trigger()
        _pump(50)
        self.assertTrue(self.doc.getObject("Cylinder").Suppressed)
        _menu, actions = self.menu("Cylinder")
        self.assertEqual(actions["suppress"].text(), "Unsuppress feature")
        actions["suppress"].trigger()
        _pump(50)
        self.assertFalse(self.doc.getObject("Cylinder").Suppressed)

    def test_rename_inline(self):
        _say("Timeline menu: Rename Cylinder to Boss through the inline editor")
        _menu, actions = self.menu("Cylinder")
        actions["rename"].trigger()
        _pump(50)
        edit = self.bar.rename_editor()
        self.assertIsNotNone(edit, "no inline editor")
        self.assertEqual(edit.text(), "Cylinder")
        undo_before = self.doc.UndoCount
        edit.setText("Boss")
        key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(edit, key)
        _pump(80)
        self.assertEqual(self.doc.getObject("Cylinder").Label, "Boss")
        self.assertEqual(self.doc.UndoCount, undo_before + 1)
        self.assertIsNone(self.bar.rename_editor())
        self.assertIn("Boss", self.bar.button_widget("Cylinder").toolTip())

    def test_rename_escape_cancels(self):
        _menu, actions = self.menu("Box")
        actions["rename"].trigger()
        _pump(50)
        edit = self.bar.rename_editor()
        edit.setText("Nope")
        key = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(edit, key)
        _pump(50)
        self.assertEqual(self.doc.getObject("Box").Label, "Box")
        self.assertIsNone(self.bar.rename_editor())

    def test_delete_last_feature_needs_no_confirm(self):
        _say("Timeline menu: Delete the Sphere (nothing depends on it: no dialog)")
        seen = []
        timer = _answer_message_boxes(QtWidgets.QMessageBox.No, seen)
        try:
            _menu, actions = self.menu("Sphere")
            actions["delete"].trigger()
            _pump(300)
        finally:
            timer.stop()
        self.assertEqual(seen, [], "no dependency dialog for the last feature")
        self.assertIsNone(self.doc.getObject("Sphere"))
        self.assertEqual(self.body.Tip.Name, "Cylinder")
        self.assertEqual([o.Name for o in self.body.Group], ["Box", "Cylinder"])
        self.assertIsNone(self.bar.button_widget("Sphere"))

    def test_delete_middle_feature_asks_first(self):
        _say("Timeline menu: Delete the Cylinder; FreeCAD asks (Sphere depends on it); answer No")
        seen = []
        timer = _answer_message_boxes(QtWidgets.QMessageBox.No, seen)
        try:
            _menu, actions = self.menu("Cylinder")
            actions["delete"].trigger()
            _pump(300)
        finally:
            timer.stop()
        # macOS leaves a QMessageBox's window title empty, so count the boxes.
        self.assertEqual(len(seen), 1, f"one dependency dialog expected, saw {seen!r}")
        self.assertIsNotNone(self.doc.getObject("Cylinder"), "No keeps the feature")


def build_pad_hole_fillet(doc):
    """Pad, a through hole (Pocket) from a circle on XY, a fillet on the pad's vertical edges."""
    import Part

    V = App.Vector
    body = doc.addObject("PartDesign::Body", "Body")
    doc.recompute()
    xy = body.Origin.OriginFeatures[3]
    sk = doc.addObject("Sketcher::SketchObject", "PadSketch")
    sk.AttachmentSupport = [(xy, "")]
    sk.MapMode = "FlatFace"
    pts = [V(0, 0, 0), V(30, 0, 0), V(30, 10, 0), V(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    body.addObject(sk)
    pad = doc.addObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 10
    body.addObject(pad)
    doc.recompute()
    sk2 = doc.addObject("Sketcher::SketchObject", "HoleSketch")
    sk2.AttachmentSupport = [(xy, "")]
    sk2.MapMode = "FlatFace"
    sk2.addGeometry(Part.Circle(V(22, 5, 0), V(0, 0, 1), 2))
    body.addObject(sk2)
    hole = doc.addObject("PartDesign::Pocket", "Hole")
    hole.Profile = sk2
    hole.Type = 1
    hole.Midplane = True
    body.addObject(hole)
    doc.recompute()
    vertical = [
        f"Edge{i + 1}"
        for i, edge in enumerate(hole.Shape.Edges)
        if isinstance(edge.Curve, Part.Line) and abs(abs(edge.tangentAt(edge.FirstParameter).z) - 1) < 1e-6
    ]
    fillet = doc.addObject("PartDesign::Fillet", "Fillet")
    fillet.Base = (hole, vertical)
    fillet.Radius = 1
    body.addObject(fillet)
    doc.recompute()
    return body


class TestReorder(TimelineGuiCase):
    def make_body(self, doc):
        return build_pad_hole_fillet(doc)

    def menu(self, name):
        body, feature = self.dock._feature(name)
        menu = self.dock.build_menu(name, body, feature)
        return {a.objectName(): a for a in menu.actions() if a.objectName()}

    def solids(self):
        from history_timeline import model

        return [o.Name for o in model.solids_in_group(self.body)]

    def test_fillet_moves_before_the_hole_and_back(self):
        _say("Timeline: Move earlier on the Fillet (past an independent hole), then Move later")
        self.assertTrue(all(o.isValid() for o in self.body.Group))
        volume = self.body.Shape.Volume
        undo_before = self.doc.UndoCount
        actions = self.menu("Fillet")
        self.assertTrue(actions["earlier"].isEnabled())
        self.assertFalse(actions["later"].isEnabled(), "last feature cannot move later")
        actions["earlier"].trigger()
        _pump(80)
        self.assertIsNone(self.dock._last_move_error)
        self.assertEqual(self.solids(), ["Pad", "Fillet", "Hole"])
        self.assertTrue(all(o.isValid() for o in self.body.Group),
                        [(o.Name, o.getStatusString()) for o in self.body.Group])
        self.assertAlmostEqual(self.body.Shape.Volume, volume, places=3)
        self.assertEqual(self.doc.UndoCount, undo_before + 1)
        self.assertEqual(self.body.Tip.Name, "Hole", "tip stays at the end")
        # The bar follows the new order.
        order = [n for n in self.bar._order]
        self.assertLess(order.index("Fillet"), order.index("Hole"))
        actions = self.menu("Fillet")
        actions["later"].trigger()
        _pump(80)
        self.assertEqual(self.solids(), ["Pad", "Hole", "Fillet"])
        self.assertAlmostEqual(self.body.Shape.Volume, volume, places=3)
        self.doc.undo()
        self.doc.undo()
        self.doc.recompute()
        self.assertEqual(self.solids(), ["Pad", "Hole", "Fillet"], "two undos give the start order")

    def test_illegal_moves_are_disabled(self):
        actions = self.menu("Hole")
        self.assertFalse(actions["earlier"].isEnabled(), "a pocket cannot be the first feature")
        self.assertIn("material", actions["earlier"].toolTip())
        self.assertTrue(actions["later"].isEnabled())
        actions = self.menu("Pad")
        self.assertFalse(actions["earlier"].isEnabled())
        self.assertFalse(actions["later"].isEnabled())
        actions = self.menu("HoleSketch")
        self.assertFalse(actions["earlier"].isEnabled())
        self.assertIn("sketch", actions["earlier"].toolTip())

    def test_sketch_on_a_face_blocks_the_move(self):
        import Part

        fillet = self.doc.getObject("Fillet")
        top = next(
            f"Face{i + 1}" for i, face in enumerate(fillet.Shape.Faces)
            if abs(face.CenterOfMass.z - 10) < 1e-6 and face.Surface.TypeId == "Part::GeomPlane"
        )
        sk = self.doc.addObject("Sketcher::SketchObject", "TopSketch")
        sk.AttachmentSupport = [(fillet, top)]
        sk.MapMode = "FlatFace"
        sk.addGeometry(Part.Circle(App.Vector(8, 5, 0), App.Vector(0, 0, 1), 1.5))
        self.body.addObject(sk)
        pocket = self.doc.addObject("PartDesign::Pocket", "Pocket2")
        pocket.Profile = sk
        pocket.Length = 5
        self.body.addObject(pocket)
        self.doc.recompute()
        self.dock.refresh()
        _pump(50)
        actions = self.menu("Pocket2")
        self.assertFalse(actions["earlier"].isEnabled())
        self.assertIn("TopSketch", actions["earlier"].toolTip())
        self.assertIn("Fillet", actions["earlier"].toolTip())
