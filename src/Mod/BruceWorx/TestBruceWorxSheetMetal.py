# SPDX-License-Identifier: LGPL-2.1-or-later
"""MOD-06 sheet metal, in FreeCAD: the bracket unfolds to the bend-allowance length, the shop
DXF (mm and inch), the rule VarSet and its expressions, and the two vendored patches.

Run: FreeCADCmd -t TestBruceWorxSheetMetal (the new-unfolder cases need networkx on the path,
e.g. -P <folder with networkx>; they are skipped without it).
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App

try:
    import Part
    import SheetMetalBaseCmd
    import SheetMetalCmd

    HAVE_SHEETMETAL = True
except ImportError:
    HAVE_SHEETMETAL = False

try:
    import networkx  # noqa: F401

    HAVE_NETWORKX = True
except ImportError:
    HAVE_NETWORKX = False

from bruceworx import flat_dxf, flat_export as fe, sheetmetal_rules as smr

T, R, K = 1.5, 2.0, 0.44
A, B, D = 40.0, 30.0, 30.0  # outside flange lengths and depth (plan figure)


def expected_flat(k=K):
    return A + B - smr.bend_deduction(90, R, T, k)


def build_bracket(doc, hole=False):
    """Bracket A of the plan: a 40 x 30 base plate and one 90 degree flange, Outer Sharp."""
    sketch = doc.addObject("Sketcher::SketchObject", "PlateSketch")
    pts = [App.Vector(0, 0, 0), App.Vector(A, 0, 0), App.Vector(A, D, 0), App.Vector(0, D, 0)]
    for i in range(4):
        sketch.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    if hole:
        sketch.addGeometry(Part.Circle(App.Vector(12, 15, 0), App.Vector(0, 0, 1), 3))
    base = doc.addObject("Part::FeaturePython", "BaseBend")
    SheetMetalBaseCmd.SMBaseBend(base, sketch)
    base.Thickness = T
    base.Radius = R
    doc.recompute()
    edge = next(f"Edge{i + 1}" for i, e in enumerate(base.Shape.Edges)
                if abs(e.CenterOfMass.x - A) < 1e-6 and abs(e.CenterOfMass.z - T) < 1e-6
                and abs(e.Length - D) < 1e-6)
    wall = doc.addObject("Part::FeaturePython", "Flange")
    SheetMetalCmd.SMBendWall(wall, base, [edge])
    wall.radius = R
    wall.length = B
    wall.angle = 90
    wall.BendType = "Material Inside"
    wall.LengthSpec = "Outer Sharp"
    doc.recompute()
    return base, wall


@unittest.skipUnless(HAVE_SHEETMETAL, "the SheetMetal add-on is not loaded")
class TestFlat(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWSheetMetal")
        self.base, self.wall = build_bracket(self.doc, hole=True)
        self.tmp = tempfile.mkdtemp(prefix="bw-sm-")

    def tearDown(self):
        App.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check_flat(self, pattern, directions=True):
        self.assertAlmostEqual(max(pattern.width, pattern.height), expected_flat(), delta=0.01)
        self.assertAlmostEqual(min(pattern.width, pattern.height), D, delta=0.01)
        self.assertAlmostEqual(pattern.thickness, T, delta=1e-6)
        self.assertEqual(len(pattern.bend_rows()), 1)
        bend = pattern.bends[0]
        # The bend line lies in the bend zone: (A - R - T) .. (A - R - T) + BA from the plate's free end.
        across = 0 if pattern.width > pattern.height else 1
        start = A - R - T
        ba = smr.bend_allowance(90, R, T, K)
        long_side = max(pattern.width, pattern.height)
        for x in (bend.start[across], bend.end[across]):
            self.assertTrue(start - 1e-6 <= x <= start + ba + 1e-6 or
                            start - 1e-6 <= long_side - x <= start + ba + 1e-6, (x, start, ba))
        if directions:
            self.assertEqual(bend.direction, "up")
            self.assertAlmostEqual(bend.angle, 90.0, places=6)
            self.assertAlmostEqual(bend.radius, R, places=6)
            self.assertAlmostEqual(bend.ba, ba, places=6)
        self.assertEqual([e["type"] for e in pattern.inner], ["circle"])

    @unittest.skipUnless(HAVE_NETWORKX, "networkx is not installed (new unfolder)")
    def test_new_unfolder_flat_and_dxf_mm(self):
        pattern = fe.flat_pattern(self.wall, k_factor=K, unfolder="new")
        self.check_flat(pattern)
        path = os.path.join(self.tmp, fe.file_name("BW-01-01001", "A"))
        fe.export_dxf(self.wall, path, etch="BW-01-01001 A", k_factor=K, unfolder="new")
        self.assertTrue(path.endswith("BW-01-01001-A.dxf"))
        data = flat_dxf.read(open(path, encoding="ascii").read())
        self.assertEqual(data["header"]["$ACADVER"], "AC1009")
        self.assertEqual(data["header"]["$INSUNITS"], "4")
        box = flat_dxf.bounding_box(data["entities"], [flat_dxf.CUT])
        self.assertAlmostEqual(box[2] - box[0], expected_flat(), delta=0.01)
        self.assertAlmostEqual(box[3] - box[1], D, delta=0.01)
        layers = {e["layer"] for e in data["entities"]}
        self.assertEqual(layers, {"CUT", "BEND_UP", "ETCH"})
        text = [e for e in data["entities"] if e["type"] == "text"]
        self.assertEqual(text[0]["text"], "BW-01-01001 A")
        rows = fe.bend_table(pattern)
        self.assertEqual(rows[0], smr.BEND_TABLE_HEADER)
        self.assertEqual(rows[1], ("1", "Up", "90°", "2", "0.44", "4.18"))

    @unittest.skipUnless(HAVE_NETWORKX, "networkx is not installed (new unfolder)")
    def test_inch_dxf_and_profile(self):
        path = os.path.join(self.tmp, "flat_in.dxf")
        pattern = fe.export_dxf(self.wall, path, units="in", profile={"bend_layers": False, "etch": False},
                                etch="X 1", k_factor=K, unfolder="new")
        data = flat_dxf.read(open(path, encoding="ascii").read())
        self.assertEqual(data["header"]["$INSUNITS"], "1")
        box = flat_dxf.bounding_box(data["entities"])
        self.assertAlmostEqual((box[2] - box[0]) * 25.4, expected_flat(), delta=0.01)
        self.assertEqual({e["layer"] for e in data["entities"]}, {"CUT"})
        self.assertEqual(fe.bend_table(pattern, "in")[1][5], smr.format_number(smr.bend_allowance(90, R, T, K) / 25.4, 3))

    @unittest.skipUnless(HAVE_NETWORKX, "networkx is not installed (new unfolder)")
    def test_unfold_object_and_k_sweep(self):
        import SheetMetalUnfoldCmd

        unfold = self.doc.addObject("Part::FeaturePython", "Unfold")
        SheetMetalUnfoldCmd.SMUnfold(unfold, self.wall, [fe.stationary_face(self.wall)])
        for k in (0.30, 0.44, 0.50):
            unfold.KFactor = k
            self.doc.recompute()
            pattern = fe.flat_pattern(unfold, unfolder="new")
            self.assertAlmostEqual(max(pattern.width, pattern.height), expected_flat(k), delta=0.01)

    def test_old_unfolder_bends_without_direction(self):
        pattern = fe.flat_pattern(self.wall, k_factor=K, unfolder="old")
        self.assertEqual(pattern.unfolder, "old")
        self.check_flat(pattern, directions=False)
        self.assertEqual({b.direction for b in pattern.bends}, {""})
        layers = {e["layer"] for e in fe.entities(pattern, etch_text="P 1")}
        self.assertIn(flat_dxf.BEND, layers)


@unittest.skipUnless(HAVE_SHEETMETAL, "the SheetMetal add-on is not loaded")
class TestRuleBinding(unittest.TestCase):
    def setUp(self):
        import bruceworx_sheetmetal

        self.sm = bruceworx_sheetmetal
        self.doc = App.newDocument("BWSheetMetalRule")
        self.base, self.wall = build_bracket(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def expressions(self, obj):
        return dict(obj.ExpressionEngine)

    def test_apply_rule_binds_and_updates(self):
        rules = smr.seed_rules("mm")
        rule = smr.find(rules, "AL5052-2.0")
        varset = self.sm.apply_rule(self.doc, rule, units="mm")
        self.assertEqual(varset.TypeId, "App::VarSet")
        self.assertEqual(self.sm.current_rule_name(self.doc), "AL5052-2.0")
        self.assertEqual(self.expressions(self.base)["Thickness"], f"{varset.Name}.Thickness")
        self.assertEqual(self.expressions(self.base)["Radius"], f"{varset.Name}.Radius")
        self.assertEqual(self.expressions(self.wall)["radius"], f"{varset.Name}.Radius")
        self.assertIn("reliefw", self.expressions(self.wall))
        self.assertAlmostEqual(self.base.Thickness.Value, 2.0)
        self.assertAlmostEqual(self.wall.radius.Value, 2.0)
        self.assertEqual(self.base.Stock, "Sheet 2 Al 5052-H32")  # no Part: the feature itself
        self.assertEqual(self.base.SheetMetalRule, "AL5052-2.0")
        # A new rule: one recompute updates every feature.
        self.sm.apply_rule(self.doc, smr.find(rules, "AL5052-3.0"), units="mm")
        self.assertAlmostEqual(self.base.Thickness.Value, 3.0)
        self.assertTrue(self.wall.Shape.isValid())
        # An override (expression cleared) is kept when the rule is applied again.
        self.wall.setExpression("radius", None)
        self.wall.radius = 5.0
        self.sm.apply_rule(self.doc, smr.find(rules, "AL5052-1.5"), units="mm")
        self.assertNotIn("radius", self.expressions(self.wall))
        self.assertAlmostEqual(self.wall.radius.Value, 5.0)
        self.assertAlmostEqual(self.base.Thickness.Value, 1.5)

    def test_part_properties_and_units(self):
        part = self.doc.addObject("App::Part", "Part")
        part.addObject(self.base)
        part.addObject(self.wall)
        rule = smr.find(smr.seed_rules("in"), "CRS-16ga")
        self.sm.apply_rule(self.doc, rule, units="in")
        self.assertEqual(self.sm.part_of(self.wall), part)
        self.assertEqual(part.SheetMetalRule, "CRS-16ga")
        self.assertEqual(part.Stock, "Sheet 16 ga Steel CR 1008")
        self.assertAlmostEqual(self.base.Thickness.Value, 0.0598 * 25.4, places=4)
        self.assertIn(self.sm.document_units(self.doc), ("mm", "in"))

    def test_unfold_k_bound(self):
        import SheetMetalUnfoldCmd

        unfold = self.doc.addObject("Part::FeaturePython", "Unfold")
        SheetMetalUnfoldCmd.SMUnfold(unfold, self.wall, [fe.stationary_face(self.wall)])
        rule = smr.find(smr.seed_rules("mm"), "SS304-1.5")
        varset = self.sm.apply_rule(self.doc, rule, units="mm")
        self.assertEqual(self.expressions(unfold)["KFactor"], f"{varset.Name}.KFactor")
        self.assertAlmostEqual(unfold.KFactor, 0.45)
        self.assertEqual(unfold.MaterialSheet, "_manual")


@unittest.skipUnless(HAVE_SHEETMETAL and HAVE_NETWORKX, "SheetMetal with networkx is needed")
class TestOpenedFileIsNotNew(unittest.TestCase):
    """Objects made while a file loads are not new features (the document's Restoring flag says so;
    FreeCAD passes no start/finish-restore slots to Python observers)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-sm-open-")

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("SmOpen"):
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_observer_skips_a_loading_file_and_takes_a_new_object(self):
        import bruceworx_sheetmetal as sm

        doc = App.newDocument("SmOpen")
        doc.addObject("Part::Box", "Box")
        doc.recompute()
        path = os.path.join(self.tmp, "SmOpen.FCStd")
        doc.saveAs(path)
        App.closeDocument(doc.Name)

        seen = []

        class Watch(sm._DocObserver):
            def slotCreatedObject(self, obj):
                seen.append((obj.Name, sm.restoring(obj.Document)))

        kept = set(sm._state["pending"])
        watch, real = Watch(), sm._DocObserver()
        App.addDocumentObserver(watch)
        try:
            opened = App.openDocument(path)
            self.assertEqual(seen, [("Box", True)])
            self.assertFalse(sm.restoring(opened))
            opened.addObject("Part::Box", "Later")
            self.assertEqual(seen[-1], ("Later", False))
        finally:
            App.removeDocumentObserver(watch)
        sm._state["pending"].clear()

        class Loading:
            Name, Restoring = "SmOpen", True

        class Made:
            Name, Document = "Box", Loading

        real.slotCreatedObject(Made)  # the real handler, on a loading document: nothing is queued
        self.assertEqual(sm._state["pending"], set())
        sm._state["pending"].update(kept)


class TestVendorPatches(unittest.TestCase):
    def test_k_table_interpolates_in_its_segment(self):
        import SheetMetalNewUnfolder

        bac_class = SheetMetalNewUnfolder.BendAllowanceCalculator
        bac = bac_class()
        bac.k_factor_standard = bac_class.KFactorStandard.ANSI
        bac.radius_thickness_values = [1.0, 2.0, 3.0, 5.0]
        bac.k_factor_values = [0.30, 0.40, 0.45, 0.50]
        for ratio, k in ((0.5, 0.30), (1.5, 0.35), (2.5, 0.425), (4.0, 0.475), (6.0, 0.50), (3.0, 0.45)):
            self.assertAlmostEqual(bac.get_k_factor(ratio, 1.0), k, places=9, msg=ratio)

    def test_unfold_sketches_without_gui(self):
        import SheetMetalUnfoldCmd

        doc = App.newDocument("BWSheetMetalHeadless")
        try:
            _base, wall = build_bracket(doc)
            unfold = doc.addObject("Part::FeaturePython", "Unfold")
            SheetMetalUnfoldCmd.SMUnfold(unfold, wall, [fe.stationary_face(wall)])
            unfold.GenerateSketch = True
            unfold.SeparateSketchLayers = True
            unfold.ShowBendAngles = True
            doc.recompute()
            self.assertTrue(unfold.isValid(), unfold.getStatusString())
            self.assertGreaterEqual(len(unfold.UnfoldSketches), 2)
        finally:
            App.closeDocument(doc.Name)


class TestAddon(unittest.TestCase):
    def test_bundled_copy_is_found(self):
        dirs = [d for d in getattr(App, "__ModDirs__", []) if os.path.basename(os.path.normpath(d)).lower() == "sheetmetal"]
        if App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx").GetBool("SheetMetalAddon", True):
            self.assertTrue(dirs, "Mod/SheetMetal is not in this build")

    def test_user_copy_rule(self):
        import bruceworx_sheetmetal as sm

        home = os.path.join(App.getHomePath(), "Mod")
        ours = os.path.join(home, "SheetMetal")
        self.assertIsNone(sm.user_copy([ours], home))
        self.assertIsNone(sm.user_copy([ours, os.path.join(home, "SheetMetal")], home))
        self.assertEqual(sm.user_copy([ours, "/Users/x/Library/FreeCAD/Mod/SheetMetal"], home),
                         "/Users/x/Library/FreeCAD/Mod/SheetMetal")
