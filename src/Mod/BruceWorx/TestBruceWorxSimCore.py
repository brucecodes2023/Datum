# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulation studies, headless (SIM-01; the first SIM-05 benchmark).

A 100 x 10 x 10 mm steel cantilever with a 1000 N tip load: Netgen Tet10 mesh,
CalculiX, the tip deflection within 5 % of P L^3 / (3 E I), the minimum safety
factor = yield / max von Mises. The solver tests skip when ccx is absent.
"""

import math
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
        # S1 (30 Oct): the Keystone library (yield strengths on every card) first, the stock cards after it
        ranks = [sim.library_rank(c["library"]) for c in cards]
        self.assertEqual(ranks, sorted(ranks))
        keystone = [c for c in cards if c["library"] == sim.KEYSTONE_LIBRARY]
        if keystone:
            self.assertEqual(cards[0]["library"], sim.KEYSTONE_LIBRARY)
            self.assertTrue(all(c["yield"] for c in keystone))
        self.assertEqual(sim.library_rank("System"), 2)

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

    def test_new_study_has_the_measure_and_bands(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(model=self.box))
        analysis = objs["analysis"]
        self.assertEqual(analysis.StressMeasure, "vonMises")
        self.assertEqual(list(analysis.SFThresholds), [1.0, 2.0])
        self.assertEqual(list(analysis.SFColors), list(sim.DEFAULT_SF_COLORS))
        objs = sim.new_study(self.doc, sim.StudyPlan(model=self.box, stress_measure="Tresca",
                                                     sf_thresholds=[1, 1.5, 3], sf_colors=["#ff0000", "#ff8800", "#ffff00", "#00ff00"]))
        self.assertEqual(objs["analysis"].StressMeasure, "Tresca")
        self.assertEqual(sim.study_bands(objs["analysis"]), ([1.0, 1.5, 3.0], ["#FF0000", "#FF8800", "#FFFF00", "#00FF00"]))
        with self.assertRaises(sim.StudyError):
            sim.new_study(self.doc, sim.StudyPlan(model=self.box, stress_measure="Mohr"))

    def test_report_without_results(self):
        """The LaTeX source; compiled to a PDF when Tectonic, latexmk or pdflatex is there."""
        objs = sim.new_study(self.doc, sim.StudyPlan(name="Plain & 50%", model=self.box))
        tmp = tempfile.mkdtemp(prefix="bw-sim-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        kind, _path = sim.find_tex()
        out = sim.write_report(objs["analysis"], folder=tmp, compile=bool(kind))
        self.assertEqual(out["tex"], os.path.join(tmp, "SimCore_Plain___50__report", "report.tex"))
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Not solved yet", text)
        self.assertIn("No support", text)
        self.assertIn(r"{\Large\bfseries Plain \& 50\%}", text)
        self.assertIn(r"\definecolor{bandA}{HTML}{D91C1C}", text)
        if kind:
            self.assertTrue(out["pdf"] and os.path.isfile(out["pdf"]), out["message"])
            self.assertEqual(os.path.dirname(out["pdf"]), tmp)
            with open(out["pdf"], "rb") as handle:
                self.assertEqual(handle.read(5), b"%PDF-")

    def test_tex_escape(self):
        self.assertEqual(sim.tex_escape("a_b & 5% #1 {x} ~ ^"), r"a\_b \& 5\% \#1 \{x\} \~{} \^{}")
        self.assertEqual(sim.tex_escape("ν × σ"), r"$\nu$ $\times$ $\sigma$")
        self.assertEqual(sim.tex_command("tectonic", "/x/tectonic")[-1], "report.tex")
        self.assertIn("-pdf", sim.tex_command("latexmk", "/x/latexmk"))


class TestStressMeasures(unittest.TestCase):
    """S3 (30 Oct): the safety factor divides the study's equivalent stress. Hand-checkable cases:

    node 1, pure shear tau = 50 MPa: s = (50, 0, -50); von Mises sqrt(3) 50 = 86.60, Tresca 100, Rankine 50
    node 2, uniaxial 100 MPa:        s = (100, 0, 0);  every measure 100
    node 3, s = (100, 0, -50):        von Mises sqrt(17500) = 132.29, Tresca 150, Rankine 100
    With a 200 MPa yield the safety factors are 200 / those.
    """

    TENSORS = ((0, 0, 0, 50, 0, 0), (100, 0, 0, 0, 0, 0), (100, 0, -50, 0, 0, 0))

    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.doc = App.newDocument("SimMeasure")
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        values = {"uuid": "", "name": "Test", "library": "", "E": 200000.0, "nu": 0.3, "yield": 200.0, "uts": None,
                  "density": None}
        self.analysis = sim.new_study(self.doc, sim.StudyPlan(model=box, material=values))["analysis"]
        import ObjectsFem

        self.result = ObjectsFem.makeResultMechanical(self.doc, "Result")
        self.analysis.addObject(self.result)
        principals = [sim.principal_stresses(*t) for t in self.TENSORS]
        self.result.NodeNumbers = [1, 2, 3]
        self.result.vonMises = [sim.von_mises(*p) for p in principals]
        self.result.PrincipalMax = [p[0] for p in principals]
        self.result.PrincipalMed = [p[1] for p in principals]
        self.result.PrincipalMin = [p[2] for p in principals]

    def test_principal_stresses_closed_form(self):
        for got, want in zip((sim.principal_stresses(*t) for t in self.TENSORS),
                             ((50, 0, -50), (100, 0, 0), (100, 0, -50))):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=9)
        s = sim.principal_stresses(30, -20, 10, 15, -5, 8)  # a general tensor: the invariants hold
        self.assertAlmostEqual(sum(s), 20, places=9)
        self.assertAlmostEqual(s[0] * s[1] * s[2], 30 * -20 * 10 + 2 * 15 * 8 * -5 - 30 * 64 - -20 * 25 - 10 * 225, places=6)

    def test_equivalent_stress_by_hand(self):
        self.assertAlmostEqual(sim.equivalent_stress("vonMises", 132.29, 100, -50), 132.29)
        self.assertEqual(sim.equivalent_stress("Tresca", 0, 100, -50), 150)
        self.assertEqual(sim.equivalent_stress("MaxPrincipal", 0, 100, -50), 100)
        self.assertEqual(sim.equivalent_stress("MaxPrincipal", 0, -20, -200), 200)  # compression counts by magnitude
        self.assertAlmostEqual(sim.von_mises(100, 0, -50), math.sqrt(17500), places=9)

    def check(self, measure, stresses):
        min_sf = sim.set_study_measure(self.analysis, measure)
        self.assertEqual(self.result.StressMeasure, measure)
        for sf, stress in zip(self.result.SafetyFactor, stresses):
            self.assertAlmostEqual(sf, 200.0 / stress, places=7)
        self.assertAlmostEqual(min_sf, 200.0 / max(stresses), places=7)
        self.assertAlmostEqual(self.result.MinSafetyFactor, min_sf)
        self.assertAlmostEqual(self.result.MaxEquivalentStress, max(stresses), places=6)

    def test_von_mises(self):
        self.check("vonMises", [50 * math.sqrt(3), 100, math.sqrt(17500)])  # min SF 1.512

    def test_tresca(self):
        self.check("Tresca", [100, 100, 150])  # min SF 1.333

    def test_max_principal(self):
        self.check("MaxPrincipal", [50, 100, 100])  # min SF 2.0

    def test_without_principals_falls_back_to_von_mises(self):
        self.result.PrincipalMax = []
        sim.set_study_measure(self.analysis, "Tresca")
        self.assertEqual(self.result.StressMeasure, "vonMises")
        with self.assertRaises(sim.StudyError):
            sim.set_study_measure(self.analysis, "Mohr")


class TestBands(unittest.TestCase):
    """S2 (30 Oct): n thresholds make n + 1 coloured bands; per study, defaults in Mod/BruceWorx/Sim."""

    def setUp(self):
        prefs = App.ParamGet(sim.SIM_PREFS)
        saved = (prefs.GetString("SFThresholds", ""), prefs.GetString("SFColors", ""))

        def restore():
            sim.reset_default_bands()
            if saved[0]:
                prefs.SetString("SFThresholds", saved[0])
                prefs.SetString("SFColors", saved[1])

        self.addCleanup(restore)
        sim.reset_default_bands()

    def test_check_bands(self):
        self.assertEqual(sim.check_bands([1, 2], ["#d91c1c", "f2a007", (64, 163, 77)]),
                         ([1.0, 2.0], ["#D91C1C", "#F2A007", "#40A34D"]))
        for thresholds, colors in (([], ["#000000"]), ([2, 1], ["#000000"] * 3), ([0, 1], ["#000000"] * 3),
                                   ([1, 2], ["#000000"] * 2), ([1], ["red", "#000000"])):
            with self.assertRaises(sim.StudyError):
                sim.check_bands(thresholds, colors)

    def test_band_index_labels_counts(self):
        thresholds = [1.0, 1.5, 3.0]
        self.assertEqual([sim.band_index(sf, thresholds) for sf in (0.2, 1.0, 1.49, 2.0, 3.0, 100)], [0, 1, 1, 2, 3, 3])
        self.assertEqual(sim.band_labels(thresholds), ["below 1", "1 to 1.5", "1.5 to 3", "above 3"])
        self.assertEqual(sim.band_counts([0.5, 1.2, 9, 9], thresholds), [1, 1, 0, 2])
        self.assertEqual(sim.rgb("#FF8000"), (1.0, 128 / 255.0, 0.0))

    def test_default_bands_from_the_preference(self):
        self.assertEqual(sim.default_bands(), ([1.0, 2.0], list(sim.DEFAULT_SF_COLORS)))
        sim.save_default_bands([1, 3], ["#ff0000", "#ffff00", "#00ff00"])
        self.assertEqual(App.ParamGet(sim.SIM_PREFS).GetString("SFThresholds"), "1;3")
        self.assertEqual(sim.default_bands(), ([1.0, 3.0], ["#FF0000", "#FFFF00", "#00FF00"]))
        App.ParamGet(sim.SIM_PREFS).SetString("SFColors", "#ff0000")  # broken: the built-in defaults
        self.assertEqual(sim.default_bands(), ([1.0, 2.0], list(sim.DEFAULT_SF_COLORS)))


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
        self.assertEqual(sum(s["result"]["band_counts"]), len(result.NodeNumbers))
        self.assertGreater(s["result"]["band_counts"][0], 0)  # red at the root
        out = sim.write_report(analysis, folder=self.tmp, compile=False)
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Min safety factor", text)
        self.assertIn("below 1: the part fails", text)
        self.assertIn("No renders", text)
        # the other measures from the same fields: Tresca >= von Mises >= Rankine-ish in bending (near uniaxial)
        vm_sf = result.MinSafetyFactor
        tresca_sf = sim.set_study_measure(analysis, "Tresca")
        tresca = result.MaxEquivalentStress
        rankine_sf = sim.set_study_measure(analysis, "MaxPrincipal")
        rankine = result.MaxEquivalentStress
        self.assertLessEqual(tresca_sf, vm_sf + 1e-9)
        self.assertGreaterEqual(tresca, ext["max_vm"] - 1e-6)
        self.assertLessEqual(tresca, ext["max_vm"] * 2 / math.sqrt(3) + 1e-6)  # Tresca <= 2/sqrt(3) vM
        self.assertGreaterEqual(rankine, tresca / 2 - 1e-6)  # max |s| >= (s1 - s3) / 2 for any state
        self.assertAlmostEqual(rankine_sf, 355.0 / rankine, places=6)
        sim.set_study_measure(analysis, "vonMises")
        App.Console.PrintMessage(
            f"\n[sim benchmark] tip {ext['max_disp']:.4f} mm vs {theory['deflection']:.4f} mm theory "
            f"({100 * error:.2f} %), max vM {ext['max_vm']:.1f} MPa vs {theory['stress']:.1f} MPa, min SF {ext['min_sf']:.3f}; "
            f"Tresca {tresca:.1f} MPa (SF {tresca_sf:.3f}), Rankine {rankine:.1f} MPa (SF {rankine_sf:.3f}); "
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
