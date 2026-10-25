# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for on-canvas editing (bruceworx_oncanvas): the drag maths, snapping, names, where the
handles of a Pad, Pocket, Hole and Fillet go on a real model (headless), and in a GUI FreeCAD the
MOD-14 acceptance: a pad's length, a fillet's radius and a hole's depth edited in the view."""

import math
import unittest

import bruceworx_oncanvas as oc


class TestRay(unittest.TestCase):
    def test_nearest_point_on_the_arrow(self):
        # arrow up the z axis; a ray along x at height 5 crosses it at t = 5
        t = oc.ray_param((0, 0, 0), (0, 0, 1), (-10, 0, 5), (1, 0, 0))
        self.assertAlmostEqual(t, 5.0)

    def test_skew_ray(self):
        # ray along y at x = 3, z = 2: the nearest arrow point is z = 2
        self.assertAlmostEqual(oc.ray_param((0, 0, 0), (0, 0, 1), (3, -5, 2), (0, 1, 0)), 2.0)

    def test_oblique_ray(self):
        d = (1 / math.sqrt(2), 0, 1 / math.sqrt(2))
        # the ray from (-4, 0, 0) heading up and right crosses the arrow at z = 4
        self.assertAlmostEqual(oc.ray_param((0, 0, 0), (0, 0, 1), (-4, 0, 0), d), 4.0)

    def test_near_parallel_is_none(self):
        self.assertIsNone(oc.ray_param((0, 0, 0), (0, 0, 1), (0, 0, 50), (0, 0, -1)))
        d = (math.sin(math.radians(5)), 0, -math.cos(math.radians(5)))
        self.assertTrue(oc.near_parallel((0, 0, 1), d))
        d = (math.sin(math.radians(20)), 0, -math.cos(math.radians(20)))
        self.assertFalse(oc.near_parallel((0, 0, 1), d))


class TestSnap(unittest.TestCase):
    def test_step_follows_zoom(self):
        self.assertEqual(oc.snap_step(800, 8.0), 1.0)  # 100 mm across 800 px
        self.assertAlmostEqual(oc.snap_step(800, 80.0), 0.1)  # zoomed in ten times
        self.assertEqual(oc.snap_step(800, 0.8), 10.0)

    def test_step_modes(self):
        self.assertEqual(oc.step_for(oc.SNAP_AUTO, 1.0), 1.0)
        self.assertAlmostEqual(oc.step_for(oc.SNAP_AUTO, 1.0, fine=True), 0.1)
        self.assertEqual(oc.step_for(oc.SNAP_FREE, 1.0), 0.0)
        self.assertEqual(oc.step_for(oc.SNAP_GRID, 1.0, grid=5), 5.0)
        self.assertEqual(oc.step_for(oc.SNAP_GRID, 1.0, grid=None), 1.0)  # no grid: the zoom's step

    def test_round_numbers(self):
        self.assertEqual(oc.round_to(19.6, 1.0), 20.0)
        self.assertAlmostEqual(oc.round_to(19.64, 0.1), 19.6)
        self.assertEqual(oc.round_to(19.64, 0), 19.64)

    def test_value_stops_at_one_step(self):
        self.assertEqual(oc.drag_value(14, 1, -20, 1.0), 1.0)
        self.assertEqual(oc.drag_value(14, 1, 5.6, 1.0), 20.0)
        self.assertEqual(oc.drag_value(14, 2, 3, 1.0), 20.0)  # symmetric pad: both caps move
        self.assertAlmostEqual(oc.drag_value(5, 1, -9, 0.0), oc.MIN_VALUE)

    def test_face_snap_with_hysteresis(self):
        ppm = 10.0  # px per mm
        snapped, held = oc.face_snap(7.6, [8.0], ppm)  # 4 px away: caught
        self.assertEqual((snapped, held), (8.0, 8.0))
        snapped, held = oc.face_snap(8.9, [8.0], ppm, held)  # 9 px: still held
        self.assertEqual(snapped, 8.0)
        snapped, held = oc.face_snap(9.2, [8.0], ppm, held)  # 12 px: released
        self.assertIsNone(snapped)
        self.assertIsNone(held)
        self.assertEqual(oc.face_snap(8.7, [8.0], ppm)[0], None)  # 7 px, not held: free
        self.assertEqual(oc.drag_value(14, 1, 7.6, 1.0, snapped_dt=8.0), 22.0)

    def test_shown_offset(self):
        self.assertEqual(oc.shown_offset(20, 14, 1), 6)
        self.assertEqual(oc.shown_offset(20, 14, 2), 3)


class TestFeatureRules(unittest.TestCase):
    def test_one_side(self):
        s = oc.extrude_sides("One side", "Length")
        self.assertEqual(s["front"], ("Length", 1, 1.0))
        self.assertIsNone(s["back"])

    def test_symmetric_and_legacy_midplane(self):
        for s in (oc.extrude_sides("Symmetric", "Length"), oc.extrude_sides("One side", "Length", midplane=True)):
            self.assertEqual(s["front"], ("Length", 1, 2.0))
            self.assertEqual(s["back"], ("Length", -1, 2.0))

    def test_two_sides(self):
        s = oc.extrude_sides("Two sides", "Length", "UpToFace")
        self.assertEqual(s["front"], ("Length", 1, 1.0))
        self.assertEqual(s["back"], "Up to face")
        s = oc.extrude_sides("One side", "TwoLengths")
        self.assertEqual(s["back"], ("Length2", -1, 1.0))

    def test_no_arrow_types(self):
        for kind, word in (("UpToFace", "Up to face"), ("ThroughAll", "Through all"), ("UpToFirst", "Up to first")):
            self.assertEqual(oc.extrude_sides("One side", kind)["front"], word)

    def test_fillet_geometry(self):
        right = math.pi / 2
        self.assertAlmostEqual(oc.fillet_factor(right), 1.0)
        self.assertAlmostEqual(oc.fillet_offset(5, right), 5.0)
        # 120 degree corner: the fillet meets each face closer to the edge
        a = math.radians(120)
        self.assertAlmostEqual(oc.fillet_offset(5, a), 5 / math.tan(a / 2))
        self.assertAlmostEqual(oc.fillet_offset(5, a) * oc.fillet_factor(a), 5.0)

    def test_labels_and_names(self):
        self.assertEqual(oc.field_label("PartDesign::Pad", "Length"), "Distance")
        self.assertEqual(oc.field_label("PartDesign::Pocket", "Length"), "Depth")
        self.assertEqual(oc.field_label("PartDesign::Fillet", "Radius"), "Radius")
        self.assertEqual(oc.field_label("PartDesign::Hole", "Depth"), "Depth")
        self.assertEqual(oc.transaction_name("Boss", "Distance"), "Boss: Distance")


class TestText(unittest.TestCase):
    def test_format(self):
        self.assertEqual(oc.format_value(20.0, 1.0), "20 mm")
        self.assertEqual(oc.format_value(20.5, 0.1), "20.5 mm")
        self.assertEqual(oc.format_value(20.25, 0.0), "20.25 mm")
        self.assertEqual(oc.format_value(25.4, 1.0, "in", 25.4), "1 in")

    def test_parse(self):
        self.assertEqual(oc.parse_entry(" 20 "), ("value", "20"))
        self.assertEqual(oc.parse_entry("= Plate.T * 2"), ("expr", "Plate.T * 2"))
        self.assertIsNone(oc.parse_entry("  "))
        self.assertIsNone(oc.parse_entry("="))

    def test_paths(self):
        self.assertEqual(oc.feature_subname("Body.Fillet.Face5", "Pad001"), "Body.Pad001.")
        self.assertEqual(oc.feature_subname("Face5", "Pad001"), "Pad001.")
        self.assertEqual(oc.feature_subname("Body.Fillet.;#5:1;:H1,F.Face5", "Pad"), "Body.Pad.")
        self.assertEqual(oc.body_subname("Body.Fillet.Face5", "Body"), "Body.")
        self.assertEqual(oc.body_subname("Link.Body.Pad.Face1", "Body"), "Link.Body.")
        self.assertIsNone(oc.body_subname("Face5", "Body"))

    def test_viewport_and_bar(self):
        self.assertEqual(oc.viewport_to_qt(200, 1198, 600, 2.0), (100.0, 0.0))
        x, y = oc.viewport_to_qt(200, 0, 600, 2.0)
        self.assertEqual((x, y), (100.0, 599.0))
        self.assertEqual(oc.bar_place(800, 600, 300, 30, above=(300, 550, 200, 38)), (250, 512))
        self.assertEqual(oc.bar_place(800, 600, 300, 30), (250, 558))
        self.assertEqual(oc.next_index(1, 2), 0)
        self.assertEqual(oc.next_index(0, 2, back=True), 1)


class TestLatestWins(unittest.TestCase):
    def test_one_at_a_time_latest_wins(self):
        queue = []
        applied = []

        def apply(v):
            applied.append(v)
            # values pushed during a recompute replace the pending one
            if v == 1:
                t.push(2)
                t.push(3)
            return 20.0

        t = oc.LatestWins(apply, queue.append)
        t.push(1)
        self.assertEqual(applied, [])  # runs later
        while queue:
            queue.pop(0)()
        self.assertEqual(applied, [1, 3])
        self.assertEqual(t.times, [20.0, 20.0])

    def test_flush(self):
        applied = []
        t = oc.LatestWins(lambda v: applied.append(v), lambda fn: None)
        t.push(5)
        t.push(6)
        t.flush()
        self.assertEqual(applied, [6])

    def test_preview_threshold(self):
        self.assertFalse(oc.preview_due([20, 22, 19], 80))
        self.assertTrue(oc.preview_due([90, 95, 100], 80))
        self.assertFalse(oc.preview_due([200], 0))  # 0: off
        self.assertTrue(oc.preview_due([10, 10, 300, 300], 80))  # the last three


def _plate():
    """The plan's plate: base pad 8, boss 14 on top, two holes (one in the boss), four corner fillets."""
    import FreeCAD as App
    import Part
    import Sketcher

    V = App.Vector
    doc = App.newDocument("OnCanvasTest")
    part = doc.addObject("App::Part", "Part")
    body = doc.addObject("PartDesign::Body", "Body")
    part.addObject(body)

    def sketch(name, z):
        sk = body.newObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
        return sk

    sk = sketch("Sketch", 0)
    pts = [V(0, 0, 0), V(80, 0, 0), V(80, 50, 0), V(0, 50, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 8
    pad.Label = "Base plate"
    doc.recompute()
    sk2 = sketch("Sketch001", 8)
    sk2.addGeometry(Part.Circle(V(55, 25, 0), V(0, 0, 1), 12))
    doc.recompute()
    boss = body.newObject("PartDesign::Pad", "Pad001")
    boss.Profile = sk2
    boss.Length = 14
    boss.Label = "Boss"
    doc.recompute()
    sk3 = sketch("Sketch002", 22)
    sk3.addGeometry(Part.Circle(V(55, 25, 0), V(0, 0, 1), 3))
    sk3.addGeometry(Part.Circle(V(18, 25, 0), V(0, 0, 1), 3))
    doc.recompute()
    hole = body.newObject("PartDesign::Hole", "Hole")
    hole.Profile = sk3
    hole.Diameter = 9
    hole.Depth = 18
    hole.DepthType = "Dimension"
    hole.Threaded = False
    doc.recompute()
    edges = []
    for i, e in enumerate(hole.Shape.Edges):
        if len(e.Vertexes) != 2:
            continue
        p0, p1 = e.Vertexes[0].Point, e.Vertexes[1].Point
        if (p0 - p1).cross(V(0, 0, 1)).Length < 1e-6 and abs(abs(p0.z - p1.z) - 8) < 1e-6 \
                and p0.x in (0, 80) and p0.y in (0, 50):
            edges.append("Edge%d" % (i + 1))
    fil = body.newObject("PartDesign::Fillet", "Fillet")
    fil.Base = (hole, edges)
    fil.Radius = 5
    doc.recompute()
    return doc, part, body, pad, boss, hole, fil


class TestHandles(unittest.TestCase):
    """Where the arrows go on the plate (no GUI needed)."""

    @classmethod
    def setUpClass(cls):
        import FreeCAD

        cls.FreeCAD = FreeCAD
        cls.doc, cls.part, cls.body, cls.pad, cls.boss, cls.hole, cls.fil = _plate()

    @classmethod
    def tearDownClass(cls):
        cls.FreeCAD.closeDocument(cls.doc.Name)

    def close(self, a, b, tol=1e-6):
        self.assertLess((a - self.FreeCAD.Vector(*b)).Length, tol, "%s != %s" % (a, b))

    def test_boss_top_face(self):
        top = next(f for f in self.boss.Shape.Faces
                   if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z - 22) < 1e-6)
        seed = oc.seed_for(self.boss, top)
        self.assertEqual(seed["side"], "front")
        (h,) = oc.handles_for(self.boss, oc.body_matrix(self.boss), seed)
        self.assertEqual((h.prop, h.label, h.k, h.v0), ("Length", "Distance", 1.0, 14.0))
        self.assertAlmostEqual(h.base.z, 22.0)
        self.close(h.u, (0, 0, 1))
        self.assertTrue(h.draggable)
        # the outline moves with the value
        self.assertAlmostEqual(h.outline(20.0)[0][0].z, 28.0)
        self.assertAlmostEqual(h.reference[0][0].z, 22.0)

    def test_side_face_has_no_handle(self):
        side = next(f for f in self.boss.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"
                    and abs(f.Surface.Radius - 12) < 1e-6)
        self.assertIsNone(oc.seed_for(self.boss, side))

    def test_browser_path_uses_the_cap(self):
        (h,) = oc.handles_for(self.pad, oc.body_matrix(self.pad), oc.seed_for(self.pad, None))
        self.assertAlmostEqual(h.base.z, 8.0)
        self.assertTrue(h.moving)  # the cap face's outline

    def test_global_placement(self):
        FreeCAD = self.FreeCAD
        self.part.Placement = FreeCAD.Placement(FreeCAD.Vector(100, 0, 0), FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90))
        self.doc.recompute()
        try:
            m = oc.body_matrix(self.boss, self.part, "Body.Fillet.Face5")
            (h,) = oc.handles_for(self.boss, m, {"side": "front"})
            self.close(h.u, (0, -1, 0))  # z turned to -y
            self.assertAlmostEqual(h.base.x, 155.0, places=5)
            self.assertAlmostEqual(h.base.y, -22.0, places=5)
        finally:
            self.part.Placement = FreeCAD.Placement()
            self.doc.recompute()

    def test_symmetric_and_reversed(self):
        self.boss.SideType = "Symmetric"
        self.doc.recompute()
        try:
            front, = oc.handles_for(self.boss, oc.body_matrix(self.boss), {"side": "front"})
            back, = oc.handles_for(self.boss, oc.body_matrix(self.boss), {"side": "back"})
            self.assertAlmostEqual(front.base.z, 15.0)
            self.assertAlmostEqual(back.base.z, 1.0)
            self.close(back.u, (0, 0, -1))
            self.assertEqual(front.k, 2.0)
        finally:
            self.boss.SideType = "One side"
            self.doc.recompute()

    def test_up_to_face_shows_the_type(self):
        self.boss.Type = "UpToFirst"
        try:
            (h,) = oc.handles_for(self.boss, oc.body_matrix(self.boss), {"side": "front"})
            self.assertFalse(h.draggable)
            self.assertEqual(h.reason, "Up to first")
        finally:
            self.boss.Type = "Length"
            self.doc.recompute()

    def test_expression_shows_no_arrow(self):
        self.boss.setExpression("Length", "Pad.Length * 2")
        self.doc.recompute()
        try:
            (h,) = oc.handles_for(self.boss, oc.body_matrix(self.boss), {"side": "front"})
            self.assertFalse(h.draggable)
            self.assertEqual(h.reason, "= Pad.Length * 2")
        finally:
            self.boss.setExpression("Length", None)
            self.boss.Length = 14
            self.doc.recompute()

    def test_pocket_floor(self):
        import Part

        FreeCAD = self.FreeCAD
        doc, body = self.doc, self.body
        sk = body.newObject("Sketcher::SketchObject", "SketchPocket")
        sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sk.MapMode = "FlatFace"
        sk.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, 8), FreeCAD.Rotation())
        sk.addGeometry(Part.Circle(FreeCAD.Vector(30, 25, 0), FreeCAD.Vector(0, 0, 1), 4))
        doc.recompute()
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sk
        pocket.Length = 3
        doc.recompute()
        try:
            floor = next(f for f in pocket.Shape.Faces if f.Surface.TypeId == "Part::GeomPlane"
                         and abs(f.CenterOfMass.z - 5) < 1e-6)
            seed = oc.seed_for(pocket, floor)
            (h,) = oc.handles_for(pocket, oc.body_matrix(pocket), seed)
            self.assertEqual(h.label, "Depth")
            self.assertAlmostEqual(h.base.z, 5.0)
            self.close(h.u, (0, 0, -1))  # into the material
            # the plate's bottom is 5 mm further: a face height to snap to
            self.assertTrue(any(abs(t - 5.0) < 1e-6 for t in h.snaps))
        finally:
            body.removeObject(pocket)
            doc.removeObject(pocket.Name)
            body.removeObject(sk)
            doc.removeObject(sk.Name)
            body.Tip = self.fil
            doc.recompute()

    def test_hole_depth_and_diameter(self):
        wall = next(f for f in self.hole.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"
                    and abs(f.Surface.Radius - 4.5) < 1e-6 and f.CenterOfMass.x < 30)
        seed = oc.seed_for(self.hole, wall)
        self.assertEqual(seed["hole"], 1)  # the plate hole at x = 18
        depth, diameter = oc.handles_for(self.hole, oc.body_matrix(self.hole), seed)
        self.assertEqual((depth.prop, diameter.prop), ("Depth", "Diameter"))
        self.close(depth.base, (18, 25, 4))
        self.close(depth.u, (0, 0, -1))
        self.assertEqual(diameter.k, 2.0)
        self.assertAlmostEqual((diameter.base - self.FreeCAD.Vector(18, 25, 22)).Length, 4.5)
        self.assertAlmostEqual(len(diameter.outline(12.0)[0]), 49)

    def test_fillet_radius(self):
        fface = next(f for f in self.fil.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder"
                     and abs(f.Surface.Radius - 5) < 1e-6)
        seed = oc.seed_for(self.fil, fface)
        (h,) = oc.handles_for(self.fil, oc.body_matrix(self.fil), seed)
        self.assertEqual((h.prop, h.label), ("Radius", "Radius"))
        self.assertAlmostEqual(h.k, 1.0)  # a box corner
        # the arrow starts where the fillet meets a face: 5 mm from the sharp edge
        edge = self.hole.Shape.getElement(seed["edge"])
        d = edge.distToShape(__import__("Part").Vertex(h.base))[0]
        self.assertAlmostEqual(d, 5.0, places=5)
        self.assertAlmostEqual(abs(h.u.z), 0.0)
        self.assertEqual(len(h.reference), 1)  # the old sharp edge, dashed


def _gui_up():
    try:
        import FreeCAD

        return bool(FreeCAD.GuiUp)
    except ImportError:
        return False


@unittest.skipUnless(_gui_up(), "needs a GUI FreeCAD")
class TestInTheView(unittest.TestCase):
    """MOD-14 acceptance: double-click a face, drag the arrow or type; no task panel opens."""

    def setUp(self):
        import FreeCAD
        import FreeCADGui

        self.FreeCAD, self.Gui = FreeCAD, FreeCADGui
        oc.install()
        self.doc, _part, self.body, self.pad, self.boss, self.hole, self.fil = _plate()
        self.doc.UndoMode = 1
        self.view = FreeCADGui.getDocument(self.doc.Name).ActiveView
        self.view.viewIsometric()
        self.view.fitAll()
        self.pump()
        oc._manager.sync()
        self.vp = oc._graphics_viewport(oc._view_widget(self.view))

    def tearDown(self):
        oc.end_session(quiet=True)
        self.FreeCAD.closeDocument(self.doc.Name)

    def pump(self, n=10):
        for _ in range(n):
            self.Gui.updateGui()

    def drag(self, session, mm):
        """Drag the arrow ``mm`` along itself, as the mouse would (pointer positions in Coin pixels)."""
        h = session.handle
        session.pointer = self.view.getPointOnViewport(h.base)
        session.on_start()
        session.pointer = self.view.getPointOnViewport(h.base + h.u * mm)
        session.on_motion()
        self.pump(3)
        session.on_finish()
        self.pump(5)

    def type(self, session, text):
        session.field.edit.setText(text)
        session.typed()
        self.pump(3)

    def test_double_click_drag_and_type(self):
        from PySide import QtCore, QtGui, QtWidgets

        # a real double-click on the boss's cap (its viewport's event filter starts the edit)
        x, y = self.view.getPointOnViewport(self.FreeCAD.Vector(55, 33, 22))
        dpr = self.vp.devicePixelRatioF()
        pos = QtCore.QPointF(x / dpr, self.vp.height() - 1 - y / dpr)
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick, pos, self.vp.mapToGlobal(pos),
                                  QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.vp, event)
        self.pump(10)
        s = oc.session()
        self.assertIsNotNone(s, "the double-click started no edit")
        self.assertEqual((s.obj, s.handle.label), (self.boss, "Distance"))
        self.drag(s, 5)
        self.assertAlmostEqual(self.boss.Length.Value, 19.0)
        self.type(s, "18 mm")
        self.assertAlmostEqual(self.boss.Length.Value, 18.0)
        oc.end_session()
        s = oc.start(self.fil)
        self.type(s, "3 mm")
        self.assertAlmostEqual(self.fil.Radius.Value, 3.0)
        self.drag(s, 1.0)
        self.assertAlmostEqual(self.fil.Radius.Value, 4.0)
        oc.end_session()
        s = oc.start(self.hole)
        self.assertEqual(s.handle.label, "Depth")
        self.type(s, "12 mm")
        self.assertAlmostEqual(self.hole.Depth.Value, 12.0)
        oc.end_session()
        self.assertEqual(s.callbacks, [])  # the arrow's callbacks went with it
        self.assertFalse(self.Gui.Control.activeDialog())
        self.assertTrue(self.body.Shape.isValid())
        self.assertEqual(self.doc.UndoNames[:5], ["Hole: Depth", "Fillet: Radius", "Fillet: Radius",
                                                  "Boss: Distance", "Boss: Distance"])


if __name__ == "__main__":
    unittest.main()
