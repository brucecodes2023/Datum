# SPDX-License-Identifier: LGPL-2.1-or-later
"""Trackpad navigation (NavigationStyle::processTrackpadScroll).

A plain two-finger swipe pans, Shift + swipe zooms at the cursor, Cmd + swipe orbits; a mouse
wheel still zooms. The GUI tests send synthetic wheel events (scroll phase, pixel delta,
modifiers) to the 3D view and read the camera. Headless, only the preference checkbox is checked.

Run in FreeCAD (FreeCADCmd -t TestBruceWorxTrackpad), or in a GUI FreeCAD with
package/bruceworx/tests/run_gui_tests.py (BW_TEST_ONLY=TestBruceWorxTrackpad).
"""

import math
import os
import unittest
import xml.etree.ElementTree as ET

import FreeCAD as App

PREFS = "User parameter:BaseApp/Preferences/BruceWorx"
VIEW_PREFS = "User parameter:BaseApp/Preferences/View"


class TestPreference(unittest.TestCase):
    def test_checkbox_default_on(self):
        here = os.path.dirname(os.path.abspath(__file__))
        ui = ET.parse(os.path.join(here, "preferences-bruceworx.ui"))
        for widget in ui.iter("widget"):
            if widget.get("name") == "TrackpadGestures":
                props = {p.get("name"): p for p in widget.findall("property")}
                self.assertEqual(props["checked"].findtext("bool"), "true")
                self.assertEqual(props["prefPath"].findtext("cstring"), "BruceWorx")
                self.assertIn("Shift + swipe zooms", props["text"].findtext("string"))
                return
        self.fail("TrackpadGestures checkbox missing")


def _dot(a, b):
    return a.x * b.x + a.y * b.y + a.z * b.z


@unittest.skipUnless(App.GuiUp, "needs the FreeCAD window")
class TestTrackpadGui(unittest.TestCase):
    def setUp(self):
        import FreeCADGui as Gui

        self.Gui = Gui
        self.prefs = App.ParamGet(PREFS)
        self.saved = self.prefs.GetBool("TrackpadGestures", True)
        self.prefs.SetBool("TrackpadGestures", True)
        self.view_prefs = App.ParamGet(VIEW_PREFS)
        self.saved_zoom_at_cursor = self.view_prefs.GetBool("ZoomAtCursor", True)
        self.view_prefs.SetBool("ZoomAtCursor", True)
        self.doc = App.newDocument("TrackpadTest")
        box = self.doc.addObject("Part::Box", "Box")
        box.Length = box.Width = box.Height = 40
        self.doc.recompute()
        self.pump()
        self.view = Gui.getDocument(self.doc.Name).activeView()
        self.view.setCameraType("Orthographic")
        self.view.viewIsometric()
        self.view.fitAll()
        self.settle()
        self.vp = self.viewport()
        self.assertIsNotNone(self.vp)

    def tearDown(self):
        self.prefs.SetBool("TrackpadGestures", self.saved)
        self.view_prefs.SetBool("ZoomAtCursor", self.saved_zoom_at_cursor)
        App.closeDocument(self.doc.Name)

    def pump(self, n=5):
        for _ in range(n):
            self.Gui.updateGui()

    def settle(self):
        """Wait for the view animation (isometric, fit all) to finish."""
        import time

        last = None
        for _ in range(100):
            self.pump()
            cam = self.view.getCameraNode()
            now = (cam.orientation.getValue().getValue(), cam.position.getValue().getValue(), cam.height.getValue())
            if now == last:
                return
            last = now
            time.sleep(0.05)

    def viewport(self):
        """The viewer (a QGraphicsView). A real swipe goes to its viewport and, unhandled there,
        on to the viewer, whose event filter reads it; a synthetic (not spontaneous) wheel event
        does not propagate, so the tests send it to the viewer itself."""
        from PySide import QtWidgets

        mdi = self.Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        sub = mdi.activeSubWindow() if mdi else None
        return sub.widget().findChild(QtWidgets.QGraphicsView) if sub else None

    def camera(self):
        cam = self.view.getCameraNode()
        q = cam.orientation.getValue().getValue()
        rot = App.Rotation(q[0], q[1], q[2], q[3])
        return {
            "pos": App.Vector(*cam.position.getValue().getValue()),
            "rot": rot,
            "q": q,
            "height": cam.height.getValue(),
            "right": rot.multVec(App.Vector(1, 0, 0)),
            "up": rot.multVec(App.Vector(0, 1, 0)),
            "dir": rot.multVec(App.Vector(0, 0, -1)),
        }

    def centre(self):
        from PySide import QtCore

        return QtCore.QPointF(self.vp.width() / 2.0, self.vp.height() / 2.0)

    def wheel(self, at, pixel, angle, mods, phase, source=None):
        from PySide import QtCore, QtGui, QtWidgets

        Qt = QtCore.Qt
        glob = QtCore.QPointF(self.vp.mapToGlobal(at.toPoint()))
        if source is None:
            source = Qt.MouseEventSynthesizedBySystem
        event = QtGui.QWheelEvent(
            at, glob, QtCore.QPoint(*pixel), QtCore.QPoint(*angle), Qt.NoButton, mods, phase, True, source
        )
        QtWidgets.QApplication.sendEvent(self.vp, event)

    def swipe(self, dx, dy, mods=None, at=None, steps=4, later_mods=None):
        """Fingers rest, move (dx, dy) logical pixels per step (natural scrolling), lift."""
        from PySide import QtCore

        Qt = QtCore.Qt
        at = at or self.centre()
        mods = Qt.NoModifier if mods is None else mods
        self.wheel(at, (0, 0), (0, 0), mods, Qt.ScrollBegin)
        for i in range(steps):
            now = mods if (later_mods is None or i == 0) else later_mods
            self.wheel(at, (dx, dy), (2 * dx, 2 * dy), now, Qt.ScrollUpdate)
        self.wheel(at, (0, 0), (0, 0), mods, Qt.ScrollEnd)
        self.pump()

    def inventor_pixel(self, at):
        ratio = self.vp.devicePixelRatio()
        return int(round(at.x() * ratio)), int(round((self.vp.height() - at.y() - 1) * ratio))

    def assertSameRotation(self, a, b):
        self.assertTrue(a["rot"].isSame(b["rot"], 1e-6), f"{a['q']} != {b['q']}")

    # --- tests -----------------------------------------------------------------------------

    def test_plain_swipe_pans_with_the_fingers(self):
        before = self.camera()
        self.swipe(20, -10)  # fingers right and up: 80 px right, 40 px up
        after = self.camera()
        self.assertSameRotation(before, after)
        self.assertAlmostEqual(after["height"], before["height"], places=6)
        moved = after["pos"] - before["pos"]
        per_pixel = before["height"] / self.vp.height()
        # The model follows the fingers, so the camera goes the other way.
        self.assertAlmostEqual(_dot(moved, before["right"]), -80 * per_pixel, delta=4 * per_pixel)
        self.assertAlmostEqual(_dot(moved, before["up"]), -40 * per_pixel, delta=4 * per_pixel)
        self.assertAlmostEqual(_dot(moved, before["dir"]), 0.0, delta=1e-4 * before["height"])

    def test_shift_swipe_zooms_at_the_cursor(self):
        from PySide import QtCore

        at = QtCore.QPointF(self.vp.width() * 0.7, self.vp.height() * 0.35)
        x, y = self.inventor_pixel(at)
        viewer = self.view.getViewer()
        under = viewer.getPointOnFocalPlane(x, y)
        before = self.camera()
        self.swipe(0, -15, QtCore.Qt.ShiftModifier, at=at)
        after = self.camera()
        self.assertSameRotation(before, after)
        self.assertNotAlmostEqual(after["height"], before["height"], delta=1e-3 * before["height"])
        # Zoom at cursor: the point under the pointer stays under it.
        still = viewer.getPointOnFocalPlane(x, y)
        moved = still - under
        self.assertLess(abs(_dot(moved, after["right"])), 0.01 * before["height"])
        self.assertLess(abs(_dot(moved, after["up"])), 0.01 * before["height"])

    def test_shift_swipe_zooms_the_way_the_wheel_does(self):
        from PySide import QtCore

        Qt = QtCore.Qt
        h0 = self.camera()["height"]
        self.swipe(0, 15, Qt.ShiftModifier)  # positive delta, as a wheel turned away from you
        h1 = self.camera()["height"]
        self.wheel(self.centre(), (0, 20), (0, 120), Qt.NoModifier, Qt.NoScrollPhase, Qt.MouseEventNotSynthesized)
        self.pump()
        h2 = self.camera()["height"]
        self.assertNotAlmostEqual(h1, h0, delta=1e-3 * h0)
        self.assertEqual(h1 < h0, h2 < h1)

    def test_shift_sideways_swipe_zooms(self):
        from PySide import QtCore

        before = self.camera()
        self.swipe(-15, 0, QtCore.Qt.ShiftModifier)
        after = self.camera()
        self.assertSameRotation(before, after)
        self.assertNotAlmostEqual(after["height"], before["height"], delta=1e-3 * before["height"])

    def test_cmd_swipe_orbits_about_the_mouse_orbit_centre(self):
        from PySide import QtCore

        at = QtCore.QPointF(self.vp.width() / 2.0 + 10, self.vp.height() / 2.0 + 5)
        x, y = self.inventor_pixel(at)
        style = self.view.getViewer().getNavigationStyle()
        expected = style.findRotationCenter(x, y)  # where a mouse orbit from here turns about
        self.assertIsNotNone(expected)
        before = self.camera()
        self.swipe(15, 5, QtCore.Qt.ControlModifier, at=at)  # Cmd on macOS
        after = self.camera()
        self.assertFalse(after["rot"].isSame(before["rot"], 1e-4), "the view did not turn")
        self.assertAlmostEqual(after["height"], before["height"], places=6)
        centre = style.getRotationCenter()
        self.assertLess((centre - expected).Length, 1e-3 * before["height"])
        # Turned about the centre: it keeps its place in camera space, so it stays put on screen.
        rel_before = before["rot"].inverted().multVec(centre - before["pos"])
        rel_after = after["rot"].inverted().multVec(centre - after["pos"])
        self.assertLess((rel_after - rel_before).Length, 1e-3 * before["height"])

    def test_mouse_wheel_still_zooms(self):
        from PySide import QtCore

        Qt = QtCore.Qt
        before = self.camera()
        # macOS gives a mouse wheel an estimated pixel delta but no scroll phase.
        self.wheel(self.centre(), (0, 20), (0, 120), Qt.NoModifier, Qt.NoScrollPhase, Qt.MouseEventNotSynthesized)
        self.pump()
        after = self.camera()
        self.assertSameRotation(before, after)
        self.assertNotAlmostEqual(after["height"], before["height"], delta=1e-3 * before["height"])
        moved = after["pos"] - before["pos"]
        self.assertLess(abs(_dot(moved, before["right"])), 0.01 * before["height"])
        self.assertLess(abs(_dot(moved, before["up"])), 0.01 * before["height"])

    def test_modifier_held_when_the_swipe_starts_holds(self):
        from PySide import QtCore

        before = self.camera()
        self.swipe(20, 0, later_mods=QtCore.Qt.ShiftModifier)  # Shift pressed mid-swipe
        after = self.camera()
        self.assertAlmostEqual(after["height"], before["height"], places=6)
        self.assertGreater((after["pos"] - before["pos"]).Length, 0.0)

    def test_switched_off_a_swipe_zooms_as_stock_freecad(self):
        self.prefs.SetBool("TrackpadGestures", False)
        before = self.camera()
        self.swipe(0, -15)
        after = self.camera()
        self.assertSameRotation(before, after)
        self.assertNotAlmostEqual(after["height"], before["height"], delta=1e-3 * before["height"])


if __name__ == "__main__":
    unittest.main()
