# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulation studies, headless (SIM-01; the first SIM-05 benchmark).

A 100 x 10 x 10 mm steel cantilever with a 1000 N tip load: Netgen Tet10 mesh,
CalculiX, the tip deflection within 5 % of P L^3 / (3 E I), the minimum safety
factor = yield / max von Mises. The solver tests skip when ccx is absent.
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_sim as sim


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


class TestMaterials(unittest.TestCase):
    def test_cards_with_e_and_nu_steel_first_has_yield(self):
        cards = sim.material_cards()
        self.assertGreater(len(cards), 50)
        names = {c["name"]: c for c in cards}
        self.assertIn("CalculiX-Steel", names)
        self.assertAlmostEqual(names["CalculiX-Steel"]["E"], 210000.0, places=1)
        self.assertAlmostEqual(names["CalculiX-Steel"]["nu"], 0.3, places=3)
        with_yield = [c for c in cards if c["yield"]]
        self.assertTrue(with_yield)
        self.assertTrue(all(c["library"] for c in cards))

    def test_allowable_stress_yield_then_uts_then_none(self):
        self.assertEqual(sim.allowable_stress({"yield": 355.0, "uts": 500.0}), (355.0, "yield strength"))
        self.assertEqual(sim.allowable_stress({"yield": 355.0}, knockdown=0.5)[0], 177.5)
        allow, basis = sim.allowable_stress({"yield": None, "uts": 500.0})
        self.assertEqual(allow, 500.0)
        self.assertIn("ultimate", basis)
        self.assertIsNone(sim.allowable_stress({"yield": None, "uts": None})[0])
        self.assertIsNone(sim.allowable_stress(None)[0])

    def test_safety_factors_cap_and_divide(self):
        self.assertEqual(sim.safety_factors([0.0, 100.0, 1000.0], 200.0), [sim.SF_CAP, 2.0, 0.2])

    def test_fem_material_dict(self):
        data = sim.fem_material_dict({"name": "S", "E": 210000.0, "nu": 0.3, "density": 7850.0, "yield": 355.0}, {"Name": "x"})
        self.assertEqual(data["YoungsModulus"], "210000 MPa")
        self.assertEqual(data["PoissonRatio"], "0.3")
        self.assertEqual(data["Density"], "7850 kg/m^3")
        self.assertEqual(data["YieldStrength"], "355 MPa")


class TestStudy(unittest.TestCase):
    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.doc = App.newDocument("SimCore")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 100, 10, 10
        self.doc.recompute()

    def test_default_mesh_size_is_the_diagonal_over_30(self):
        diag = self.box.Shape.BoundBox.DiagonalLength
        self.assertAlmostEqual(sim.default_mesh_size(self.box), diag / 30, places=6)

    def test_model_candidates(self):
        self.assertEqual(sim.model_candidates(self.doc), [self.box])
        self.assertEqual(sim.model_candidates(self.doc, [self.box]), [self.box])

    def test_new_study_creates_the_objects_with_defaults(self):
        steel = [c for c in sim.material_cards() if c["name"] == "CalculiX-Steel"][0]
        plan = sim.StudyPlan(name="Bracket", model=self.box, material_uuid=steel["uuid"])
        objs = sim.new_study(self.doc, plan)
        analysis = objs["analysis"]
        self.assertEqual(analysis.Label, "Study: Bracket")
        self.assertEqual(analysis.StudyType, "static")
        self.assertEqual(analysis.Model, self.box)
        self.assertEqual(analysis.MaterialUUID, steel["uuid"])
        self.assertEqual(analysis.Knockdown, 1.0)
        self.assertEqual(objs["solver"].AnalysisType, "static")
        self.assertEqual(objs["material"].Material["YoungsModulus"], "210000 MPa")
        mesh = objs["mesh"]
        self.assertEqual(mesh.TypeId, "Fem::FemMeshShapeNetgenObject")
        self.assertEqual(mesh.Shape, self.box)
        self.assertAlmostEqual(mesh.MaxSize, sim.default_mesh_size(self.box), places=5)
        self.assertEqual(mesh.Fineness, "Moderate")
        self.assertTrue(mesh.SecondOrder)
        self.assertFalse(sim.is_meshed(mesh))
        self.assertEqual(sim.studies(self.doc), [analysis])
        self.assertIs(sim.study_of(mesh), analysis)
        self.assertIs(sim.active_study(self.doc), analysis)
        m = sim.members(analysis)
        self.assertIs(m["mesh"], mesh)
        self.assertEqual(m["materials"], [objs["material"]])

    def test_planned_study_types_are_refused(self):
        with self.assertRaises(sim.StudyError):
            sim.new_study(self.doc, sim.StudyPlan(study_type="modal", model=self.box))
        self.assertEqual([k for k, _l, ok in sim.STUDY_TYPES if ok], ["static"])

    def test_precheck_lists_what_is_missing(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(model=self.box))  # no material card
        issues = sim.precheck(objs["analysis"])
        texts = [t for _k, t in issues]
        self.assertFalse(sim.ready(issues))
        self.assertTrue(any("Young" in t for t in texts), texts)
        self.assertTrue(any(t.startswith("No support") for t in texts), texts)
        self.assertTrue(any(t.startswith("No load") for t in texts), texts)
        self.assertTrue(any("Not meshed" in t for t in texts), texts)
        import ObjectsFem

        force = ObjectsFem.makeConstraintForce(self.doc, "F")
        objs["analysis"].addObject(force)
        texts = [t for _k, t in sim.precheck(objs["analysis"])]
        self.assertTrue(any("no faces" in t for t in texts), texts)
        self.assertTrue(any("force is zero" in t for t in texts), texts)
        disp = ObjectsFem.makeConstraintDisplacement(self.doc, "D")
        disp.References = [(self.box, "Face1")]
        disp.xFree = False
        objs["analysis"].addObject(disp)
        texts = [t for _k, t in sim.precheck(objs["analysis"])]
        self.assertTrue(any("leave y, z free" in t for t in texts), texts)

    def test_mesh_study_makes_tet10(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(model=self.box))
        stats = sim.mesh_study(objs["analysis"])
        self.assertGreater(stats["nodes"], 100)
        self.assertGreater(stats["elements"], 50)
        self.assertEqual(stats["order"], "second")
        self.assertTrue(sim.is_meshed(objs["mesh"]))
        before = stats["elements"]
        self.assertGreater(sim.refine_mesh(objs["analysis"])["elements"], before)

    def test_report_without_results(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(name="Plain", model=self.box))
        tmp = tempfile.mkdtemp(prefix="bw-sim-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = sim.report_html(objs["analysis"], path=os.path.join(tmp, "r.html"))
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Not solved yet", text)
        self.assertIn("No support", text)
        self.assertIn("<title>Plain", text)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestCantileverBenchmark(unittest.TestCase):
    """SIM-05's first benchmark: delta = P L^3 / (3 E I)."""

    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.tmp = tempfile.mkdtemp(prefix="bw-sim-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_tip_deflection_and_safety_factor(self):
        doc, objs, theory = sim.cantilever()
        analysis = objs["analysis"]
        self.assertTrue(sim.ready(sim.precheck(analysis)), sim.precheck(analysis))
        lines = []
        result = sim.solve(analysis, working_dir=self.tmp, progress=lines.append)
        self.assertTrue(any(l.startswith("CalculiX finished") for l in lines), lines)
        ext = sim.result_extrema(result)
        error = abs(ext["max_disp"] - theory["deflection"]) / theory["deflection"]
        self.assertLess(error, 0.05, f"tip {ext['max_disp']:.4f} mm vs theory {theory['deflection']:.4f} mm")
        # the root stress is near beam theory (stress concentrations at the fixed corners push it up)
        self.assertLess(abs(ext["max_vm"] - theory["stress"]) / theory["stress"], 0.15)
        self.assertEqual(len(result.SafetyFactor), len(result.NodeNumbers))
        self.assertEqual(result.AllowableBasis, "yield strength")
        self.assertAlmostEqual(result.AllowableStress, 355.0)
        self.assertAlmostEqual(result.MinSafetyFactor, 355.0 / ext["max_vm"], places=6)
        self.assertAlmostEqual(ext["min_sf"], result.MinSafetyFactor, places=6)
        self.assertLess(ext["min_sf"], 1.0)  # 600 MPa bending stress on 355 MPa steel
        # a probe at the tip reads the tip deflection
        reading = sim.probe(result, App.Vector(100, 5, 5))
        self.assertIsNotNone(reading)
        self.assertLess(reading["distance"], 2.0)
        self.assertGreater(reading["displacement"], 0.9 * ext["max_disp"])
        self.assertIsNotNone(reading["safety_factor"])
        s = sim.summary(analysis)
        self.assertEqual(s["result"]["max_vm"], ext["max_vm"])
        self.assertEqual(s["mesh"]["order"], "second")
        self.assertEqual(len(s["loads"]), 1)
        self.assertEqual(s["loads"][0]["value"], "1000 N (reversed)")
        path = sim.report_html(analysis, path=os.path.join(self.tmp, "report.html"))
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Min safety factor", text)
        self.assertIn("below 1", text)
        App.Console.PrintMessage(
            f"\n[sim benchmark] tip {ext['max_disp']:.4f} mm vs {theory['deflection']:.4f} mm theory "
            f"({100 * error:.2f} %), max vM {ext['max_vm']:.1f} MPa vs {theory['stress']:.1f} MPa, min SF {ext['min_sf']:.3f}, "
            f"{s['mesh']['nodes']} nodes\n")

    def test_solve_refuses_a_study_that_is_not_ready(self):
        doc = App.newDocument("NotReady")
        box = doc.addObject("Part::Box", "Box")
        doc.recompute()
        objs = sim.new_study(doc, sim.StudyPlan(model=box))
        with self.assertRaises(sim.StudyError):
            sim.solve(objs["analysis"], working_dir=self.tmp)


if __name__ == "__main__":
    unittest.main()
