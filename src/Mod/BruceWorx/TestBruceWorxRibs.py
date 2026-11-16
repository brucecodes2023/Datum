# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of rib stations (bruceworx_ribs, roadmap MOD-02): a sketch repeated on offset planes, scaled,
driven by the Parameters sheet.

    FreeCADCmd -t TestBruceWorxRibs      (the panel test needs the window: FreeCAD -t TestBruceWorxRibs)
"""

import os
import tempfile
import time
import unittest

import FreeCAD
import Part

import bruceworx_parameters as P
import bruceworx_ribs as br


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("RibTest")
        self.doc.UndoMode = 1
        self.body = self.doc.addObject("PartDesign::Body", "Body")
        yz = next(o for o in self.body.Origin.OriginFeatures if o.Role == "YZ_Plane")
        self.root = self.body.newObject("Sketcher::SketchObject", "Root")
        self.root.Label = "Wing root"
        self.root.AttachmentSupport = [(yz, "")]
        self.root.MapMode = "FlatFace"
        self.ellipse(20, 5)
        self.doc.recompute()
        br.install_observer()

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def ellipse(self, major, minor):
        if self.root.GeometryCount:
            self.root.delGeometry(0)
        self.root.addGeometry(Part.Ellipse(FreeCAD.Vector(major, 0, 0), FreeCAD.Vector(0, minor, 0),
                                           FreeCAD.Vector(0, 0, 0)), False)

    def make(self, **kw):
        args = dict(count=4, span=75.0, taper=0.55)
        args.update(kw)
        self.doc.openTransaction("Rib stations")
        self.set = br.make(self.root, **args)
        self.doc.commitTransaction()
        self.doc.recompute()
        return self.set

    def ribs(self):
        return [self.doc.getObject(n) for n in self.set.Ribs]

    def planes(self):
        return [self.doc.getObject(n) for n in self.set.Planes]

    def loft(self):
        return self.doc.getObject(self.set.Loft)

    def chords(self):
        """Twice the major radius of each rib's ellipse: its size."""
        return [round(2 * r.Shape.Edges[0].Curve.MajorRadius, 6) for r in self.ribs()]

    def set_param(self, alias, value):
        sheet = P.find_sheet(self.doc)
        sheet.set(sheet.getCellFromAlias(alias), str(value))
        self.doc.recompute()
        if FreeCAD.GuiUp:  # with a window, a changed count is acted on from the event loop
            import FreeCADGui as Gui

            for _ in range(10):
                Gui.updateGui()
                time.sleep(0.02)


class TestRibStations(Base):
    def test_stations_planes_scales_and_parameters(self):
        self.make()
        self.assertEqual(self.set.Base, "wing_root")
        sheet = P.find_sheet(self.doc)
        self.assertEqual([a for _c, a in P.sheet_aliases(sheet)], ["wing_root_span", "wing_root_ribs", "wing_root_taper"])
        self.assertEqual([round(p.Placement.Base.x, 6) for p in self.planes()], [25, 50, 75])  # YZ: along X
        self.assertEqual([round(r.Scale, 6) for r in self.ribs()], [0.85, 0.7, 0.55])
        self.assertEqual(self.chords(), [34, 28, 22])
        self.assertTrue(all(r.isValid() and r in self.body.Group for r in self.ribs()))
        self.assertEqual([round(r.Shape.BoundBox.XMin, 6) for r in self.ribs()], [25, 50, 75])
        loft = self.loft()
        self.assertTrue(loft.isValid())
        self.assertEqual(len(loft.Shape.Solids), 1)
        self.assertAlmostEqual(loft.Shape.BoundBox.XLength, 75, places=6)
        self.assertEqual(self.doc.UndoNames[0], "Rib stations")

    def test_ribs_follow_the_root_sketch(self):
        self.make()
        before = self.loft().Shape.Volume
        self.ellipse(30, 5)
        self.doc.recompute()
        self.assertEqual(self.chords(), [51, 42, 33])
        self.assertTrue(self.loft().isValid())
        self.assertAlmostEqual(self.loft().Shape.Volume / before, 1.5, places=3)

    def test_span_and_taper_come_from_the_sheet(self):
        self.make()
        self.set_param("wing_root_span", "150 mm")
        self.assertEqual([round(p.Placement.Base.x, 6) for p in self.planes()], [50, 100, 150])
        self.assertAlmostEqual(self.loft().Shape.BoundBox.XLength, 150, places=6)
        self.set_param("wing_root_taper", "1")
        self.assertEqual(self.chords(), [40, 40, 40])
        self.set_param("wing_root_taper", "0.4")
        self.assertEqual(self.chords(), [32, 24, 16])
        self.assertTrue(self.loft().isValid())

    def test_count_adds_and_removes_stations(self):
        self.make()
        self.set_param("wing_root_ribs", 6)
        self.assertEqual(len(self.ribs()), 5)
        self.assertEqual([round(p.Placement.Base.x, 6) for p in self.planes()], [15, 30, 45, 60, 75])
        self.assertEqual([round(r.Scale, 6) for r in self.ribs()], [0.91, 0.82, 0.73, 0.64, 0.55])
        self.assertEqual([s[0].Name for s in self.loft().Sections], self.set.Ribs)
        self.assertTrue(self.loft().isValid())
        self.assertEqual(self.doc.UndoNames[0], "Rib stations")
        self.set_param("wing_root_ribs", 2)
        self.assertEqual(len(self.ribs()), 1)
        self.assertEqual(len([o for o in self.doc.Objects if br.is_rib(o)]), 1)
        self.assertEqual(len([o for o in self.doc.Objects if o.isDerivedFrom("PartDesign::Plane")]), 1)
        self.assertEqual([round(p.Placement.Base.x, 6) for p in self.planes()], [75])
        self.assertTrue(self.loft().isValid())

    def test_without_a_loft_and_a_second_set_gets_its_own_names(self):
        first = self.make(loft=False)
        self.assertEqual(first.Loft, "")
        self.assertFalse([o for o in self.doc.Objects if o.isDerivedFrom("PartDesign::Loft")])
        second = self.make(count=2, span=-10.0, taper=1.0, loft=False)
        self.assertEqual(second.Base, "wing_root2")
        self.assertAlmostEqual(self.planes()[0].Placement.Base.x, -10, places=6)

    def test_one_ribs_scale_can_be_set_by_hand(self):
        self.make()
        middle = self.ribs()[1]
        middle.setExpression("Scale", None)
        middle.Scale = 0.9
        self.doc.recompute()
        self.assertEqual(self.chords(), [34, 36, 22])
        self.set_param("wing_root_taper", "0.7")
        self.assertEqual(self.chords(), [36, 36, 28])

    def test_refusals(self):
        with self.assertRaises(br.RibError):
            br.make(self.body)
        with self.assertRaises(br.RibError):
            br.make(self.root, count=1)
        with self.assertRaises(br.RibError):
            br.make(self.root, taper=0)
        loose = self.doc.addObject("Sketcher::SketchObject", "Loose")
        with self.assertRaises(br.RibError):
            br.make(loose)
        self.make()
        with self.assertRaises(br.RibError):
            br.make(self.ribs()[0])  # a rib section is not a root

    def test_a_taper_that_inverts_a_rib_fails_plainly(self):
        self.make()
        self.set_param("wing_root_taper", "-0.5")
        bad = [r for r in self.ribs() if not r.isValid()]
        self.assertTrue(bad)
        self.set_param("wing_root_taper", "0.55")
        self.assertTrue(all(r.isValid() for r in self.ribs()))

    def test_saved_file_comes_back_working(self):
        self.make()
        path = os.path.join(tempfile.mkdtemp(), "ribs.FCStd")
        self.doc.saveAs(path)
        name = self.doc.Name
        FreeCAD.closeDocument(name)
        self.doc = FreeCAD.openDocument(path)
        self.set = br.sets(self.doc)[0]
        self.root = self.doc.getObject("Root")
        self.assertTrue(all(br.is_rib(r) for r in self.ribs()))
        self.set_param("wing_root_span", "30 mm")
        self.assertEqual([round(p.Placement.Base.x, 6) for p in self.planes()], [10, 20, 30])
        self.set_param("wing_root_ribs", 3)
        self.assertEqual(len(self.ribs()), 2)
        self.assertTrue(self.loft().isValid())

    def test_alias_base(self):
        self.assertEqual(br.alias_base("Wing root", ()), "wing_root")
        self.assertEqual(br.alias_base("Rib 0 mm", ()), "rib_0_mm")
        self.assertEqual(br.alias_base("0 mm", ()), "ribs_0_mm")
        self.assertEqual(br.alias_base("Wing", {"wing_span"}), "wing2")
        self.assertEqual(br.alias_base("", ()), "ribs")


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestRibStationsPanel(Base):
    def pump(self, n=10):
        import FreeCADGui as Gui

        for _ in range(n):
            Gui.updateGui()
            time.sleep(0.02)

    def tearDown(self):
        import FreeCADGui as Gui

        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        super().tearDown()

    def test_panel_makes_the_set_and_the_count_follows_the_sheet(self):
        import FreeCADGui as Gui

        self.pump(40)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.root)
        panel = br.start()
        self.assertIsNotNone(panel)
        panel.count.setValue(3)
        panel.span.setValue(40)
        panel.taper.setValue(50)
        self.assertTrue(panel.accept())
        self.pump()
        self.set = br.sets(self.doc)[0]
        self.assertEqual(self.chords(), [30, 20])
        self.assertTrue(self.loft().isValid())
        self.set_param("wing_root_ribs", 5)
        self.pump(20)  # the observer adds the stations after the recompute
        self.assertEqual(len(self.ribs()), 4)
        self.assertTrue(self.loft().isValid())
        self.assertEqual(self.ribs()[0].ViewObject.Proxy.__class__.__name__, "RibView")
