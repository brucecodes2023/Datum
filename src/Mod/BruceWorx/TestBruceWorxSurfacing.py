# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of surfacing parity (roadmap MOD-08, bruceworx_surfacing and bruceworx_surfacing_gui).

Headless: FreeCADCmd -t TestBruceWorxSurfacing. The GUI class runs in the app: FreeCAD -t TestBruceWorxSurfacing."""

import math
import time
import unittest

import FreeCAD as App
import Part

import bruceworx_surfacing as bs

V = App.Vector


def plane(w=40, h=30):
    return Part.makePlane(w, h)


def bumpy(z0=12.0, amp=2.0):
    pts = [[V(-10 + i * 15, -10 + j * 12.5, z0 + amp * ((i - 2) ** 2 - (j - 2) ** 2) / 4) for j in range(5)]
           for i in range(5)]
    s = Part.BSplineSurface()
    s.interpolate(pts)
    return s.toShape()


def tilted(z0=6.0):
    pts = [[V(-10 + i * 15, -10 + j * 12.5, z0 + (i - 2)) for j in range(5)] for i in range(5)]
    s = Part.BSplineSurface()
    s.interpolate(pts)
    return s.toShape()


def frustum(a0, a1, h):
    """The volume between two similar sections of areas a0 and a1 a height h apart (a linear taper)."""
    return h * (a0 + math.sqrt(a0 * a1) + a1) / 3


class TestTolerances(unittest.TestCase):
    def test_defaults_are_occts(self):
        p = App.ParamGet(bs.TOL_PATH)
        for name in ("G0Tolerance", "G1Tolerance", "G2Tolerance", "ContinuitySearch"):
            p.RemFloat(name)
        t = bs.tolerances()
        # LocalAnalysis_SurfaceContinuity.hxx: EpsC0 = 0.001 mm, EpsG1 = 0.001 rad, Percent = 0.01, Maxlen = 10000
        self.assertEqual((t["g0"], t["g1"], t["g2"], t["zero"]), (0.001, 0.001, 0.01, 1e-4))

    def test_preference_changes_the_limit(self):
        p = App.ParamGet(bs.TOL_PATH)
        p.SetFloat("G1Tolerance", math.radians(45))
        try:
            rows = bs.check(bs.benchmark()["kink"]["shapes"])
            self.assertEqual(rows[0]["level"], "G2")  # a 30 degree kink passes a 45 degree limit
        finally:
            p.RemFloat("G1Tolerance")


class TestContinuity(unittest.TestCase):
    """The benchmark set: each case is found at the level it was built to."""

    def setUp(self):
        self.cases = bs.benchmark()

    def test_kink_is_g0(self):
        (row,) = bs.check(self.cases["kink"]["shapes"])
        self.assertEqual(row["level"], "G0")
        self.assertAlmostEqual(math.degrees(row["g1"]), 30.0, places=6)
        self.assertLess(row["g0"], 1e-9)

    def test_fillet_is_g1_with_a_curvature_jump(self):
        (row,) = bs.check(self.cases["fillet"]["shapes"])
        self.assertEqual(row["level"], "G1")
        self.assertAlmostEqual(row["g2"], 1.0, places=6)  # 0 against 1/10: all of it

    def test_split_surface_is_g2(self):
        (row,) = bs.check(self.cases["split"]["shapes"])
        self.assertEqual(row["level"], "G2")

    def test_wing_leading_edge_is_g2_and_open_trailing_edge_is_a_gap(self):
        rows = bs.check(self.cases["wing"]["shapes"])
        levels = sorted(r["level"] for r in rows)
        self.assertEqual(levels, ["G2", "none"])
        gap = next(r for r in rows if r["level"] == "none")
        self.assertGreater(gap["g0"], 0.1)  # the trailing edge's thickness, left open

    def test_unsewn_surfaces_are_compared_within_the_search_distance(self):
        upper, lower = self.cases["wing"]["shapes"]
        self.assertEqual(len(bs.pairs([upper, lower], search=1.0)), 2)
        self.assertEqual(len(bs.pairs([upper, lower], search=0.1)), 1)  # the open trailing edge drops out


class TestShapes(unittest.TestCase):
    def test_trim_outside_inside_and_sides(self):
        circle = Part.makeCircle(5, V(20, 15, 0))
        hole = bs.trim(plane(), circle, "outside")
        self.assertAlmostEqual(hole.Area, 1200 - math.pi * 25, places=3)
        self.assertAlmostEqual(bs.trim(plane(), circle, "inside").Area, math.pi * 25, places=3)
        line = Part.makeLine(V(10, -5, 0), V(10, 40, 0))
        self.assertAlmostEqual(bs.trim(plane(), line, "larger").Area, 900, places=6)
        self.assertAlmostEqual(bs.trim(plane(), line, "point", keep_point=V(2, 2, 0)).Area, 300, places=6)
        with self.assertRaises(bs.SurfacingError):
            bs.trim(plane(), line, "outside")  # an open curve has no outside

    def test_trim_projects_a_curve_from_above(self):
        roof = bs.roof()
        self.assertEqual(len(roof.Faces[0].Wires), 2)
        self.assertTrue(roof.isValid())

    def test_untrim(self):
        roof = bs.roof()
        holes = bs.untrim(roof, "holes")
        self.assertEqual(len(holes.Faces[0].Wires), 1)
        self.assertGreater(holes.Area, roof.Area)
        holed = bs.trim(plane(), Part.makeCircle(5, V(20, 15, 0)), "outside")
        self.assertAlmostEqual(bs.untrim(holed, "natural").Area, 1200, places=6)

    def test_extend_natural_is_g2_and_tangent_g1(self):
        face = bs.untrim(bs.roof(), "natural").Faces[0]
        for name in ("Edge1", "Edge2"):  # one u and one v boundary
            natural = bs.extend(face, [name], 10, "natural")
            self.assertEqual(natural.ShapeType, "Shell")
            (row,) = bs.check([natural])
            self.assertEqual(row["level"], "G2")
            self.assertLess(row["g0"], 1e-9)
            tangent = bs.extend(face, [name], 10, "tangent")
            (row,) = bs.check([tangent])
            self.assertIn(row["level"], ("G1", "G2"))
            self.assertLess(row["g1"], bs.tolerances()["g1"])

    def test_extend_reaches_the_distance(self):
        out = bs.extend(plane(), ["Edge1"], 5, "natural")
        self.assertAlmostEqual(out.Area, 1200 + 5 * plane().Edges[0].Length, places=6)
        with self.assertRaises(bs.SurfacingError):
            bs.extend(plane(), ["Edge1"], 0, "natural")

    def test_extend_refuses_a_trimmed_edge(self):
        holed = bs.trim(plane(), Part.makeCircle(5, V(20, 15, 0)), "outside").Faces[0]
        circle = next(f"Edge{i}" for i, e in enumerate(holed.Edges, 1) if e.Curve.TypeId == "Part::GeomCircle")
        with self.assertRaises(bs.SurfacingError):
            bs.extend(holed, [circle], 5, "natural")

    def test_thicken_sides(self):
        out = bs.thicken(plane(), 2.0, "outside")
        self.assertTrue(out.isValid())
        self.assertAlmostEqual(out.Volume, 2400, places=4)
        both = bs.thicken(plane(), 2.0, "both")
        self.assertAlmostEqual(both.BoundBox.ZMin, -1.0, places=6)
        self.assertAlmostEqual(both.Volume, 2400, places=4)

    def test_thicken_refuses_a_fold(self):
        upper, lower = bs.wing_skins()
        skins = bs._sew([upper.Faces[0], lower.Faces[0]], 1e-5)
        with self.assertRaises(bs.SurfacingError):
            bs.thicken(skins, 1.0, "inside")  # the trailing edge is thinner than 2 mm

    def test_stitch_closes_a_box(self):
        box = Part.makeBox(10, 20, 30)
        solid, free = bs.stitch([Part.Face(f.OuterWire) for f in box.Faces])
        self.assertEqual((solid.ShapeType, free), ("Solid", 0))
        self.assertAlmostEqual(solid.Volume, 6000, places=6)
        shell, free = bs.stitch([Part.Face(f.OuterWire) for f in box.Faces[:5]])
        self.assertEqual((shell.ShapeType, free), ("Shell", 4))

    def test_replace_face_up_and_down(self):
        box = Part.makeBox(40, 30, 10)
        top = next(f"Face{i}" for i, f in enumerate(box.Faces, 1) if abs(f.CenterOfMass.z - 10) < 1e-6)
        up = bs.replace_face(box, top, bumpy())
        self.assertTrue(up.isValid())
        self.assertGreater(up.BoundBox.ZMax, 12)
        down = bs.replace_face(box, top, tilted())
        self.assertAlmostEqual(down.Volume, 40 * 30 * 6, places=3)  # the plane z = 4 + (x + 10) / 15 averages 6
        with self.assertRaises(bs.SurfacingError):
            bs.replace_face(box, top, plane(5, 5).translated(V(0, 0, 20)))  # does not cover the face


class TestAcceptance(unittest.TestCase):
    """MOD-08: the benchmark set rebuilt to G1/G2 and thickened into valid solids."""

    def test_wing_closes_and_thickens(self):
        solid, free, thick = bs.wing_solid(1.0)
        self.assertEqual((solid.ShapeType, free), ("Solid", 0))
        self.assertTrue(solid.isValid())
        self.assertEqual(bs.bop_errors(solid), set())
        caps = sorted((f for f in solid.Faces if f.Surface.TypeId == "Part::GeomPlane" and f.Area > 100),
                      key=lambda f: f.CenterOfMass.z)
        expected = frustum(caps[0].Area, caps[-1].Area, 150)
        self.assertLess(abs(solid.Volume - expected) / expected, 0.01)
        self.assertTrue(thick.isValid())
        self.assertEqual(bs.bop_errors(thick), set())
        upper, lower = bs.wing_skins()
        self.assertLess(abs(thick.Volume - (upper.Area + lower.Area)) / thick.Volume, 0.05)  # ~ area x 1 mm

    def test_patch_is_g1_and_thickens(self):
        doc = App.newDocument("SurfPatch")
        try:
            roof, fill = bs.patch_case(doc)
            self.assertEqual(fill.TypeId, "Surface::Filling")
            rows = bs.check([roof.Shape, fill.Shape])
            self.assertTrue(rows)
            self.assertTrue(all(r["level"] in ("G1", "G2") for r in rows), [(r["level"], r["g0"], r["g1"]) for r in rows])
            shell, free = bs.stitch([roof.Shape, fill.Shape])
            self.assertEqual(shell.ShapeType, "Shell")
            self.assertEqual(len(shell.Faces), 2)
            thick = bs.thicken(shell, 1.0)
            self.assertTrue(thick.isValid())
            self.assertEqual(bs.bop_errors(thick), set())
        finally:
            App.closeDocument(doc.Name)


class TestFeatures(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("SurfFeatures")
        self.doc.UndoMode = 1

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def feature(self, shape, name):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        return obj

    def test_trim_follows_its_curve(self):
        sheet = self.feature(plane(), "Sheet")
        circle = self.feature(Part.makeCircle(5, V(20, 15, 0)), "Circle")
        self.doc.recompute()
        trim = bs.make_trim(self.doc, sheet, circle)
        self.assertAlmostEqual(trim.Shape.Area, 1200 - math.pi * 25, places=3)
        self.assertFalse(sheet.Visibility)
        circle.Shape = Part.makeCircle(8, V(20, 15, 0))
        self.doc.recompute()
        self.assertAlmostEqual(trim.Shape.Area, 1200 - math.pi * 64, places=3)
        self.assertEqual(getattr(trim, bs.STATUS), "Up to date")
        trim.Keep = "inside"
        self.doc.recompute()
        self.assertAlmostEqual(trim.Shape.Area, math.pi * 64, places=3)

    def test_untrim_extend_thicken_stitch_replace(self):
        sheet = self.feature(plane(), "Sheet")
        circle = self.feature(Part.makeCircle(5, V(20, 15, 0)), "Circle")
        self.doc.recompute()
        trim = bs.make_trim(self.doc, sheet, circle)
        untrim = bs.make_untrim(self.doc, trim, "Holes")
        self.assertAlmostEqual(untrim.Shape.Area, 1200, places=6)
        side = untrim.Shape.Faces[0].Edges[0].Length
        ext = bs.make_extend(self.doc, (untrim, "Face1"), ["Edge1"], 5.0)
        self.assertAlmostEqual(ext.Shape.Area, 1200 + 5 * side, places=6)
        ext.Distance = 10
        self.doc.recompute()
        self.assertAlmostEqual(ext.Shape.Area, 1200 + 10 * side, places=6)
        thick = bs.make_thicken(self.doc, ext, 2.0)
        self.assertAlmostEqual(thick.Shape.Volume, 2 * (1200 + 10 * side), places=3)
        box = Part.makeBox(10, 20, 30)
        faces = [self.feature(Part.Face(f.OuterWire), f"F{i}") for i, f in enumerate(box.Faces)]
        self.doc.recompute()
        stitch = bs.make_stitch(self.doc, faces)
        self.assertEqual((stitch.Shape.ShapeType, stitch.FreeEdges), ("Solid", 0))
        block = self.feature(Part.makeBox(40, 30, 10), "Block")
        surf = self.feature(tilted(), "Surf")
        self.doc.recompute()
        top = next(f"Face{i}" for i, f in enumerate(block.Shape.Faces, 1) if abs(f.CenterOfMass.z - 10) < 1e-6)
        rep = bs.make_replace_face(self.doc, block, top, surf)
        self.assertAlmostEqual(rep.Shape.Volume, 7200, places=3)
        self.assertFalse(block.Visibility)

    def test_a_feature_that_cannot_be_made_is_not_left_behind(self):
        sheet = self.feature(plane(), "Sheet")
        line = self.feature(Part.makeLine(V(10, -5, 0), V(10, 40, 0)), "Line")
        self.doc.recompute()
        names = {o.Name for o in self.doc.Objects}
        with self.assertRaises(bs.SurfacingError):
            bs.make_trim(self.doc, sheet, line, "outside")
        self.assertEqual({o.Name for o in self.doc.Objects}, names)
        self.assertTrue(sheet.Visibility)

    def test_saved_and_reopened(self):
        import os
        import tempfile

        sheet = self.feature(plane(), "Sheet")
        self.doc.recompute()
        thick = bs.make_thicken(self.doc, sheet, 3.0, "Both")
        path = os.path.join(tempfile.mkdtemp(), "surf.FCStd")
        self.doc.saveAs(path)
        name = thick.Name
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        obj = self.doc.getObject(name)
        obj.Thickness = 4
        self.doc.recompute()
        self.assertAlmostEqual(obj.Shape.Volume, 4800, places=3)


def pump(n=8):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestGui(unittest.TestCase):
    def setUp(self):
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        import bruceworx_surfacing_gui as gui

        self.gui = gui
        gui.install()
        self.doc = App.newDocument("SurfGui")
        self.doc.UndoMode = 1
        pump()

    def tearDown(self):
        import FreeCADGui as Gui

        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        App.closeDocument(self.doc.Name)
        pump()

    def feature(self, shape, name):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        self.doc.recompute()
        return obj

    def test_commands_are_registered_with_icons(self):
        import os

        import FreeCADGui as Gui

        for name, _menu, icon, _tip in self.gui.COMMANDS.values():
            self.assertIn(name, Gui.listCommands())
            self.assertTrue(os.path.isfile(os.path.join(self.gui.HERE, icon)), icon)

    def test_extend_from_the_selection_is_one_undo_step(self):
        import FreeCADGui as Gui

        sheet = self.feature(plane(), "Sheet")
        Gui.Selection.addSelection(self.doc.Name, sheet.Name, "Face1")
        Gui.Selection.addSelection(self.doc.Name, sheet.Name, "Edge1")
        before = len(self.doc.UndoNames)
        obj = self.gui.extend(distance=5.0, mode="Tangent")
        self.assertIsNotNone(obj)
        self.assertAlmostEqual(obj.Shape.Area, 1200 + 5 * sheet.Shape.Edges[0].Length, places=4)
        self.assertEqual(self.doc.UndoNames[0], "Extend")
        self.assertEqual(len(self.doc.UndoNames), before + 1)
        self.assertTrue(obj.ViewObject.Proxy.getIcon().endswith("Heuretes_SurfaceExtend.svg"))
        self.assertIn(sheet, obj.ViewObject.claimChildren())

    def test_patch_from_an_edge(self):
        import FreeCADGui as Gui

        roof = self.feature(bs.roof(), "Roof")
        shape = roof.Shape
        outer = shape.Faces[0].OuterWire
        hole = next(i for i, e in enumerate(shape.Edges, 1) if not any(e.isSame(o) for o in outer.Edges))
        Gui.Selection.addSelection(self.doc.Name, roof.Name, f"Edge{hole}")
        fill = self.gui.patch()
        self.assertEqual(fill.TypeId, "Surface::Filling")
        self.assertEqual(list(fill.BoundaryOrder), [1] * len(fill.BoundaryOrder))
        self.assertEqual(self.doc.UndoNames[0], "Patch")

    def test_continuity_panel_colours_and_closes(self):
        import FreeCADGui as Gui

        for name, case in bs.benchmark().items():
            if name in ("kink", "fillet", "split"):
                self.feature(case["shapes"][0], name.capitalize())
        Gui.Selection.clearSelection()
        panel = self.gui.continuity()
        pump()
        self.assertEqual(sorted(r["level"] for r in panel.rows), ["G0", "G1", "G2"])
        self.assertEqual(panel.table.rowCount(), 3)
        root = panel.view.getSceneGraph()
        self.assertGreaterEqual(root.findChild(panel.node), 0)
        node = panel.node
        panel.reject()
        pump()
        self.assertLess(root.findChild(node), 0)

    def test_a_refusal_is_told_and_undone(self):
        import FreeCADGui as Gui
        from unittest import mock

        sheet = self.feature(plane(), "Sheet")
        Gui.Selection.addSelection(self.doc.Name, sheet.Name, "Face1")
        names = {o.Name for o in self.doc.Objects}
        with mock.patch.object(self.gui, "_tell") as told:
            self.assertIsNone(self.gui.thicken(thickness=0.0, side="Outside"))
        told.assert_called_once()
        self.assertEqual({o.Name for o in self.doc.Objects}, names)
