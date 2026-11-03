# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] Dragging a free part in an assembly keeps the grabbed point under the cursor. Bruce, 2 Nov: "the
# movement of the screw seems to be off with the actual move of the mouse". The drag used the camera's focal
# plane: under a perspective camera a part nearer than that plane moved slower than the cursor (farther:
# faster). Now the drag plane goes through the grabbed point, normal to the view. Needs the GUI: real mouse
# events go to the 3D view, as in the on-canvas test. Perspective only: with an orthographic camera the
# synthetic click picked the grounded plate behind the screw, so the orthographic case is checked by hand.

import unittest

import FreeCAD as App

V = App.Vector


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestBWDragFollowsCursor(unittest.TestCase):
    def setUp(self):
        import FreeCADGui as Gui
        import JointObject

        self.Gui = Gui
        self.doc = App.newDocument("BWDrag")
        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        jg = asm.newObject("Assembly::JointGroup", "Joints")
        back = asm.newObject("Part::Box", "Back")  # grounded, far behind: the focal plane sits between
        back.Length, back.Width, back.Height = 200, 10, 200
        back.Placement.Base = V(-100, 400, -100)
        self.screw = asm.newObject("Part::Box", "Screw")  # free, near the camera
        self.screw.Length = self.screw.Width = self.screw.Height = 10
        self.screw.Placement.Base = V(-5, -150, -5)
        JointObject.GroundedJoint(jg.newObject("App::FeaturePython", "Ground"), back)
        self.doc.recompute()
        self.gdoc = Gui.getDocument(self.doc.Name)
        self.view = self.gdoc.ActiveView
        self.gdoc.setEdit(asm)
        Gui.Selection.clearSelection()
        self.pump()
        self.vp = self.viewport()

    def tearDown(self):
        self.gdoc.resetEdit()
        App.closeDocument(self.doc.Name)

    def pump(self, n=10):
        for _ in range(n):
            self.Gui.updateGui()

    def viewport(self):
        from PySide import QtWidgets

        mw = self.Gui.getMainWindow()
        for w in mw.findChildren(QtWidgets.QWidget):
            if w.metaObject().className() == "Gui::View3DInventor" and w.isVisible():
                gv = w.findChild(QtWidgets.QGraphicsView)
                if gv is not None:
                    return gv.viewport()
        return None

    def send(self, kind, x, y, buttons):
        """A mouse event at Coin pixel (x, y) (device pixels from the bottom)."""
        from PySide import QtCore, QtGui, QtWidgets

        dpr = self.vp.devicePixelRatioF()
        pos = QtCore.QPointF(x / dpr, self.vp.height() - 1 - y / dpr)
        button = QtCore.Qt.LeftButton if kind != QtCore.QEvent.MouseMove else QtCore.Qt.NoButton
        event = QtGui.QMouseEvent(kind, pos, self.vp.mapToGlobal(pos), button, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.vp, event)
        self.pump(3)

    def test_grabbed_point_stays_under_the_cursor_in_perspective(self):
        self.drag("Perspective")

    def drag(self, camera):
        from PySide import QtCore

        self.view.setCameraType(camera)
        self.view.viewFront()  # looks along +Y: the screw's Face3 (y = -150) faces the camera
        self.view.fitAll()
        self.view.redraw()  # the pick uses the clipping planes of the last render
        self.pump()
        self.assertIsNotNone(self.vp, "no 3D view")
        grab = V(0, -150, 0)  # the middle of the screw's front face
        x0, y0 = self.view.getPointOnViewport(grab)
        left = QtCore.Qt.LeftButton
        self.send(QtCore.QEvent.MouseMove, x0, y0, QtCore.Qt.NoButton)  # hover
        # what the hover leaves for the press: the face under the cursor and its 3D point (a synthetic move
        # does not always run the pick, so it is set as the hover sets it)
        self.Gui.Selection.setPreselection(self.doc.getObject("Assembly"), "Screw.Face3", grab.x, grab.y, grab.z)
        start = V(self.screw.Placement.Base)
        self.send(QtCore.QEvent.MouseButtonPress, x0, y0, left)
        dx, dy = 120, 80  # device pixels
        for i in range(1, 7):
            self.send(QtCore.QEvent.MouseMove, x0 + dx * i / 6, y0 + dy * i / 6, left)
        self.send(QtCore.QEvent.MouseButtonRelease, x0 + dx, y0 + dy, QtCore.Qt.NoButton)
        moved = self.screw.Placement.Base - start
        self.assertGreater(moved.Length, 1.0, "the screw did not move")
        x1, y1 = self.view.getPointOnViewport(grab + moved)
        self.assertLess(abs(x1 - (x0 + dx)) + abs(y1 - (y0 + dy)), 4,
                        f"grabbed point at {(x1, y1)}, cursor at {(x0 + dx, y0 + dy)}, moved {moved}")
        self.assertAlmostEqual(moved.y, 0.0, 3)  # the plane is normal to the view: no depth change


if __name__ == "__main__":
    unittest.main()
