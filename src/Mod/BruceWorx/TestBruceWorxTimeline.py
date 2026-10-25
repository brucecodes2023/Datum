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
