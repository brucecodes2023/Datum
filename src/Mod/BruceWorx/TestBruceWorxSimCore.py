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
            sim.new_study(self.doc, sim.StudyPlan(study_type="thermal", model=self.box))
        self.assertEqual([k for k, _l, ok in sim.STUDY_TYPES if ok], ["static", "modal", "buckling"])
        with self.assertRaises(sim.StudyError):  # a static study takes no modal options
            sim.new_study(self.doc, sim.StudyPlan(model=self.box, options={"ModeCount": 3}))

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



# ----- modal and buckling (SIM-01 next, 30 Oct) ---------------------------------------------------------------------

import bruceworx_sim_eigen as eigen  # noqa: E402

DAT = """
                        S T E P       2


     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      4   0.1011061E-03   0.1005515E-01   0.1600327E-02   0.0000000E+00
      5   0.1042396E+10   0.3228615E+05   0.5138501E+04   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

      4   0.1156635E-02  -0.7301111E-02   0.2625940E-05   0.1744805E-01   0.4043798E-01  -0.3484241E+00
      5  -0.4237390E-03   0.4789567E-02   0.9532608E-03  -0.4816347E-01   0.8609741E-02   0.2371366E+00

     E F F E C T I V E   M O D A L   M A S S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

      4   0.1337804E-05   0.5330622E-04   0.6895562E-11   0.3044346E-03   0.1635230E-02   0.1213994E+00
      5   0.1795547E-06   0.2293995E-04   0.9087061E-06   0.2319720E-02   0.7412764E-04   0.5623377E-01
TOTAL     0.7850000E-04   0.7850000E-04   0.7850000E-04   0.5233333E-02   0.2642833E+00   0.2642833E+00

     T O T A L   E F F E C T I V E   M A S S

           X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

          0.7850000E-04   0.7850000E-04   0.7850000E-04   0.5233333E-02   0.2642833E+00   0.2642833E+00

     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
                FACTOR

      1  -0.1243110E+06
      2   0.1729197E+01
"""

FRD = """    1PSTEP                         1           1           1
  100CL  101 0.00000E+00         2                     4    1           1
 -4  DISP        4    1
 -5  D1          1    2    1    0
 -1         1 1.00000E+00 0.00000E+00 0.00000E+00
 -1         2 2.00000E+00 0.00000E+00 0.00000E+00
 -3
    1PSTEP                         2           1           1
  100CL  102-1.24311E+05         2                     4    2           1
 -4  DISP        4    1
 -1         1 0.00000E+00-5.00000E-01 0.00000E+00
 -1         2 0.00000E+00 1.00000E+00 0.00000E+00
 -3
  100CL  102-1.24311E+05         2                     4    2           1
 -4  STRESS      6    1
 -1         1 9.00000E+00 9.00000E+00 9.00000E+00 9.00000E+00 9.00000E+00 9.00000E+00
 -3
 9999
"""

INP = """*Node, NSET=Nall
1, 0, 0, 0
2, 10, 0, 0
*MATERIAL, NAME=Material
*ELASTIC
210000,0.3
*EXPANSION, ZERO=0
*STEP
*BUCKLE
4,0.01
*CLOAD
** node loads on shape: Col:Face2
2,1,-250
*DLOAD
Eall,GRAV,9810,0,0,-1
7,P3,0.5
*NODE FILE
U
*EL FILE
S, E
*OUTPUT, FREQUENCY=1
*END STEP
"""


class TestEigenParts(unittest.TestCase):
    """The modal and buckling helpers without a solver: CalculiX's output, the input changes, the checks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-eigen-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_read_dat(self):
        data = eigen.read_dat(self.write("a.dat", DAT))
        self.assertEqual([n for n, _e, _f in data["eigen"]], [4, 5])
        self.assertAlmostEqual(data["eigen"][1][2], 5138.501)
        self.assertEqual(sorted(data["mass"]), [4, 5])  # the TOTAL row is not a mode
        self.assertAlmostEqual(data["mass"][4][1], 0.5330622e-4)
        self.assertEqual(data["total"][:3], [0.785e-4] * 3)
        self.assertEqual(data["buckling"], [(1, -0.1243110e6), (2, 1.729197)])

    def test_read_frd_sets_fixed_columns(self):
        sets = eigen.read_frd_sets(self.write("a.frd", FRD))
        self.assertEqual([s["set"] for s in sets], [101, 102])
        self.assertEqual(sets[1]["value"], -1.24311e5)  # glued to the set number
        self.assertEqual(sets[1]["disp"][1], (0.0, -0.5, 0.0))
        self.assertNotIn("stress", sets[1])

    def test_input_changes(self):
        dense = eigen.add_density(INP, 7.85e-9)
        self.assertIn("*ELASTIC\n210000,0.3\n*DENSITY\n7.85E-09\n", dense)
        self.assertEqual(eigen.add_density(dense, 1.0), dense)  # once
        step = eigen.perturbation_step(8, 10, 500)
        self.assertIn("*STEP, PERTURBATION\n*FREQUENCY\n8,10,500\n", step)
        self.assertEqual(eigen.frequency_card(6), "6")
        scaled = eigen.scale_loads(INP, 2.0)
        self.assertIn("2,1,-500", scaled)
        self.assertIn("Eall,GRAV,19620,0,0,-1", scaled)
        self.assertIn("7,P3,1", scaled)
        self.assertIn("** node loads on shape: Col:Face2", scaled)
        moved = eigen.offset_nodes(INP, {2: (0.0, 0.1, -0.2)})
        self.assertIn("2, 10, 0.1, -0.2", moved)
        self.assertIn("1, 0, 0, 0", moved)
        text = eigen.nonlinear_input(INP, {1: (0, 0.5, 0)}, 3.0, 2)
        self.assertIn("*NSET, NSET=BWMON\n2\n*STEP, NLGEOM, INC=1000\n*STATIC\n0.02,1.0,1E-06,0.02\n", text)
        self.assertIn("*NODE PRINT, NSET=BWMON, FREQUENCY=1\nU\n*END STEP", text)
        self.assertIn("2,1,-750", text)
        self.assertNotIn("*BUCKLE", text)
        self.assertNotIn("*EL FILE", text)
        self.assertNotIn("*OUTPUT", text)

    def test_collapse_point_and_curve(self):
        # an imperfect strut: u = e0 / (1 - lambda / lambda_cr), lambda_cr = 2, e0 = 0.1
        curve = [(0.1 / (1 - lam / 2.0) - 0.1, lam) for lam in [0.02 * i for i in range(1, 100)]]
        lam, basis = eigen.collapse_point(curve, False)
        self.assertTrue(1.7 < lam < 2.0, lam)  # slope 1 % of the start: lambda / lambda_cr = 1 - 0.1
        self.assertIn("knee", basis)
        self.assertEqual(eigen.collapse_point([(0.1, 0.5), (0.2, 1.0), (0.3, 1.5)], True)[0], 1.5)  # stiff, stopped
        self.assertIsNone(eigen.collapse_point([(0.1, 0.5), (0.2, 1.0), (0.3, 1.5)], False)[0])
        kept, note = eigen.clean_curve([(0.1, 0.5), (0.3, 1.0), (5.6, 1.7), (-12.0, 1.74), (-10.0, 1.75)])
        self.assertEqual(kept[-1], (5.6, 1.7))
        self.assertIn("left the equilibrium path", note)
        self.assertEqual(eigen.clean_curve(curve), (curve, ""))

    def test_status_and_bands(self):
        self.assertEqual([eigen.blf_status(f, 3.0) for f in (-2.0, 0.5, 1.0, 2.9, 3.0)],
                         ["grey", "red", "amber", "amber", "green"])
        bands = [("blade pass", 100.0, 400.0), ("1x rpm", 50.0, 200.0)]
        self.assertEqual(eigen.band_of(300.0, bands), "blade pass")
        self.assertEqual(eigen.band_of(60.0, bands), "1x rpm")
        self.assertEqual(eigen.band_of(500.0, bands), "")
        self.assertAlmostEqual(eigen.phase_factor(5, 20), 1.0)

    def test_theory(self):
        f = [eigen.euler_bernoulli(b, 100, 210000, 7850, 10, 10) for b in eigen.CANTILEVER_BETA_L]
        self.assertAlmostEqual(f[0], 835.5, delta=1.0)
        self.assertAlmostEqual(f[1] / f[0], (4.6941 / 1.8751) ** 2, places=6)
        self.assertLess(eigen.timoshenko(4.6941, 100, 210000, 0.3, 7850, 10, 10), f[1])
        self.assertAlmostEqual(eigen.euler_column(500, 10, 210000, 1000), 1.7272, places=3)
        self.assertAlmostEqual(eigen.tensioned_frequency(1, 500, 10, 210000, 7850, 0.0),
                               eigen.euler_bernoulli(math.pi, 500, 210000, 7850, 10, 10), places=6)


class TestEigenStudy(unittest.TestCase):
    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.addCleanup(eigen.reset_default_target)
        self.doc = App.newDocument("EigenCore")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 100, 10, 10
        self.doc.recompute()

    def test_modal_study_defaults_and_precheck(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(study_type="modal", model=self.box, material=dict(eigen.STEEL)))
        analysis = objs["analysis"]
        self.assertEqual(analysis.ModeCount, 8)
        self.assertEqual((analysis.FrequencyMin, analysis.FrequencyMax), (0.0, 0.0))
        self.assertFalse(analysis.Prestressed)
        self.assertFalse(analysis.ExcitationCheck)
        self.assertAlmostEqual(analysis.ModeScale, 0.1)
        self.assertEqual(objs["solver"].AnalysisType, "frequency")
        self.assertEqual(objs["solver"].EigenmodesCount, 14)  # free-free: 8 + the six rigid modes
        issues = sim.precheck(analysis)
        self.assertTrue(sim.ready(issues), issues)  # free-free and no load: warnings only
        texts = [t for _k, t in issues]
        self.assertTrue(any("free-free" in t for t in texts), texts)
        self.assertFalse(any("yield" in t for t in texts), texts)  # the strength is a static matter
        analysis.Prestressed = True
        texts = [t for k, t in sim.precheck(analysis) if k == "error"]
        self.assertTrue(any("static step" in t for t in texts), texts)
        self.assertTrue(any("prestress with" in t for t in texts), texts)
        # no density: an error for a modal study
        values = dict(eigen.STEEL, density=None)
        objs = sim.new_study(self.doc, sim.StudyPlan(study_type="modal", model=self.box, material=values,
                                                     options={"ModeCount": 3, "FrequencyMin": 10.0, "FrequencyMax": 900.0}))
        self.assertTrue(any("no density" in t for k, t in sim.precheck(objs["analysis"]) if k == "error"))
        eigen.configure_solver(objs["analysis"], objs["solver"])
        self.assertEqual(objs["solver"].EigenmodesCount, 3)  # a range above 1 Hz has no rigid modes
        self.assertAlmostEqual(objs["solver"].EigenmodeHighLimit.getValueAs("Hz").Value, 900.0)
        with self.assertRaises(sim.StudyError):
            sim.new_study(self.doc, sim.StudyPlan(study_type="modal", model=self.box, options={"TargetLoadFactor": 2}))

    def test_excitation_bands(self):
        objs = sim.new_study(self.doc, sim.StudyPlan(study_type="modal", model=self.box, material=dict(eigen.STEEL)))
        analysis = objs["analysis"]
        self.assertEqual(eigen.excitation_bands(analysis), [])  # off by default
        analysis.ExcitationCheck = True
        analysis.MotorRpmMin, analysis.MotorRpmMax, analysis.BladeCount = 6000.0, 12000.0, 3
        self.assertEqual(eigen.excitation_bands(analysis),
                         [("blade pass (3 blades)", 300.0, 600.0), ("1x rpm", 100.0, 200.0)])
        analysis.ExcitationSource = "FrequencyBand"
        analysis.BandMin, analysis.BandMax = 900.0, 700.0
        self.assertEqual(eigen.excitation_bands(analysis), [("band", 700.0, 900.0)])

    def test_buckling_study_defaults_and_target(self):
        eigen.save_default_target(2.5)
        objs = sim.new_study(self.doc, sim.StudyPlan(study_type="buckling", model=self.box, material=dict(eigen.STEEL)))
        analysis = objs["analysis"]
        self.assertEqual(analysis.ModeCount, 4)
        self.assertAlmostEqual(analysis.TargetLoadFactor, 2.5)  # the preference
        self.assertFalse(analysis.Nonlinear)
        self.assertEqual(objs["solver"].AnalysisType, "buckling")
        self.assertEqual(objs["solver"].BucklingFactors, 4)
        texts = [t for k, t in sim.precheck(analysis) if k == "error"]
        self.assertTrue(any("reference load" in t for t in texts), texts)
        self.assertTrue(any(t.startswith("No support") for t in texts), texts)
        self.assertAlmostEqual(eigen.reset_default_target(), 3.0)
        with self.assertRaises(eigen.EigenError):
            eigen.save_default_target(0)

    def test_section_size_and_imperfection(self):
        self.assertAlmostEqual(eigen.section_size(self.box.Shape), 4 * 10000 / self.box.Shape.Area, places=6)
        plate = self.doc.addObject("Part::Box", "Plate")
        plate.Length, plate.Width, plate.Height = 100, 100, 1
        self.doc.recompute()
        self.assertAlmostEqual(eigen.section_size(plate.Shape), 1.0)
        objs = sim.new_study(self.doc, sim.StudyPlan(study_type="buckling", model=plate, options={"Nonlinear": True}))
        amplitude, how = eigen.imperfection_amplitude(objs["analysis"])
        self.assertAlmostEqual(amplitude, 0.01)
        self.assertIn("1 %", how)
        objs["analysis"].ImperfectionAmplitude = 0.25
        self.assertEqual(eigen.imperfection_amplitude(objs["analysis"]), (0.25, "set on the study"))


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestModalBenchmarks(unittest.TestCase):
    """SIM-05: the cantilever's modes, a free-free bar, the excitation check, a report."""

    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.tmp = tempfile.mkdtemp(prefix="bw-modal-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_cantilever_modes(self):
        doc, objs, _theory = sim.cantilever(name="Modes", study_type="modal")
        analysis = objs["analysis"]
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.modal_rows(analysis)
        self.assertEqual(len(rows), 8)
        f = [r["frequency"] for r in rows]
        eb = [eigen.euler_bernoulli(b, 100, 210000, 7850, 10, 10) for b in eigen.CANTILEVER_BETA_L]
        tim = [eigen.timoshenko(b, 100, 210000, 0.3, 7850, 10, 10) for b in eigen.CANTILEVER_BETA_L]
        bending = [f[0], f[1], f[2], f[3], f[6], f[7]]  # 5: torsion, 6: axial
        for a, b in zip(bending[::2], bending[1::2]):
            self.assertLess(abs(a - b) / a, 0.005)  # a square section: the modes come in pairs
        self.assertLess(abs(f[0] - eb[0]) / eb[0], 0.03, f)
        # this stubby bar (L/h = 10) is a Timoshenko beam from mode 2 on: shear and rotary inertia lower f2 by
        # about 4 % and f3 by about 10 % against Euler-Bernoulli, so modes 2 and 3 are checked against those
        self.assertLess(abs(f[2] - tim[1]) / tim[1], 0.03, f)
        self.assertLess(abs(f[6] - tim[2]) / tim[2], 0.08, f)
        self.assertLess(f[2], eb[1])
        # effective modal mass: the bending pair in Y and Z, the axial mode in X
        self.assertGreater(rows[0]["mass"][1] + rows[0]["mass"][2], 50.0)
        self.assertGreater(rows[5]["mass"][0], 50.0)
        self.assertLess(rows[4]["mass"][0] + rows[4]["mass"][1] + rows[4]["mass"][2], 1.0)  # torsion moves no mass
        self.assertEqual(eigen.rigid_count(analysis, rows), 0)
        # the excitation check: 25 000 rpm x 2 blades = 833 Hz
        analysis.ExcitationCheck = True
        analysis.MotorRpmMin, analysis.MotorRpmMax, analysis.BladeCount = 20000.0, 30000.0, 2
        excited = [r["index"] for r in eigen.modal_rows(analysis) if r["band"]]
        self.assertEqual(excited, [1, 2])
        part = sim.summary(analysis)["eigen"]
        self.assertEqual(part["excited"], [1, 2])
        out = sim.write_report(analysis, folder=self.tmp, compile=False)
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Modal frequencies", text)
        self.assertIn("in the blade pass (2 blades) band", text)
        self.assertIn("Modes inside a band: 1, 2", text)
        self.assertNotIn("Safety factor", text)
        App.Console.PrintMessage(
            "\n[modal benchmark] f = " + ", ".join(f"{x:.1f}" for x in f) + f" Hz; Euler-Bernoulli {eb[0]:.1f}, "
            f"{eb[1]:.0f}, {eb[2]:.0f}; Timoshenko {tim[0]:.1f}, {tim[1]:.0f}, {tim[2]:.0f}\n")

    def test_free_free_bar(self):
        doc, objs, theory = eigen.free_bar()
        analysis = objs["analysis"]
        self.assertTrue(sim.ready(sim.precheck(analysis)))
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.modal_rows(analysis)
        self.assertEqual(eigen.rigid_count(analysis, rows), 6)
        elastic = [r for r in rows if not r["rigid"]]
        self.assertGreaterEqual(len(elastic), 8)
        error = abs(elastic[0]["frequency"] - theory["f1"]) / theory["f1"]
        self.assertLess(error, 0.03, (elastic[0]["frequency"], theory))
        out = sim.write_report(analysis, folder=self.tmp, compile=False)
        with open(out["tex"], encoding="utf-8") as handle:
            self.assertIn("6 rigid-body modes", handle.read())
        App.Console.PrintMessage(f"\n[free-free] f1 {elastic[0]['frequency']:.1f} Hz vs {theory['f1']:.1f} "
                                 f"(beta L 4.7300), {100 * error:.2f} %\n")

    def test_prestressed_modal_tensioned_beam(self):
        doc, objs, theory = eigen.tensioned_beam()
        analysis = objs["analysis"]
        self.assertTrue(sim.ready(sim.precheck(analysis)), sim.precheck(analysis))
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.modal_rows(analysis)
        f1 = rows[0]["frequency"]
        self.assertLess(abs(f1 - theory["f1"]) / theory["f1"], 0.03, (f1, theory))
        self.assertLess(abs(rows[1]["frequency"] - theory["f1"]) / theory["f1"], 0.03)  # the other plane
        self.assertLess(abs(rows[2]["frequency"] - theory["f2"]) / theory["f2"], 0.03)
        self.assertIsNotNone(eigen.prestress_result(analysis))
        with open(os.path.join(self.tmp, "Mesh.inp"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("*DENSITY", text)
        self.assertIn("*STEP, PERTURBATION\n*FREQUENCY\n4\n", text)
        # the same beam unloaded: the plain frequency
        analysis.Prestressed = False
        sim.solve(analysis, working_dir=self.tmp)
        plain = eigen.modal_rows(analysis)[0]["frequency"]
        self.assertLess(abs(plain - theory["f1_free"]) / theory["f1_free"], 0.03, (plain, theory))
        App.Console.PrintMessage(f"\n[prestressed] f1 {f1:.2f} Hz vs {theory['f1']:.2f} under 10 kN, "
                                 f"{plain:.2f} Hz vs {theory['f1_free']:.2f} unloaded\n")


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestBucklingBenchmarks(unittest.TestCase):
    """SIM-05: the Euler column, reversed load, the nonlinear run."""

    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.tmp = tempfile.mkdtemp(prefix="bw-buckle-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tip_direction(self, result):
        """The unit displacement at the free end's corner of a mode."""
        vectors = dict(zip(result.NodeNumbers, result.DisplacementVectors))
        nodes = result.Mesh.FemMesh.Nodes
        tip = max(vectors, key=lambda n: nodes[n].x + 1e-6 * vectors[n].Length)
        v = vectors[tip]
        return v * (1.0 / v.Length)

    def test_euler_column(self):
        doc, objs, theory = eigen.column(max_size=8.0)
        analysis = objs["analysis"]
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.buckling_rows(analysis)
        self.assertEqual(len(rows), 4)
        lam1 = eigen.first_factor(analysis)
        self.assertTrue(rows[0]["first"])
        self.assertLess(abs(lam1 - theory["lambda1"]) / theory["lambda1"], 0.03, (lam1, theory))
        self.assertEqual(rows[0]["status"], "amber")  # 1.73: between 1 and the target 3
        first = self.tip_direction(rows[0]["result"])
        same = max(rows[1:], key=lambda r: abs(first.dot(self.tip_direction(r["result"]))) * (r["factor"] > lam1 * 2))
        ratio = same["factor"] / lam1
        self.assertLess(abs(ratio - 9.0) / 9.0, 0.03, ratio)
        out = sim.write_report(analysis, folder=self.tmp, compile=False)
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("non-conservative", text)
        self.assertIn(r"$\lambda_1=$", text)
        App.Console.PrintMessage(f"\n[Euler column] lambda1 {lam1:.4f} vs {theory['lambda1']:.4f}, "
                                 f"same-plane ratio {ratio:.3f}\n")
        # the load reversed: tension only, no positive factor
        load = [o for o in analysis.Group if o.Name == "TipLoad"][0]
        load.Reversed = False
        doc.recompute()
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.buckling_rows(analysis)
        self.assertTrue(rows and all(r["factor"] < 0 for r in rows), rows)
        self.assertIsNone(eigen.first_factor(analysis))
        self.assertTrue(all(r["status"] == "grey" and not r["first"] for r in rows))

    def test_nonlinear_buckling_below_linear(self):
        doc, objs, theory = eigen.column(max_size=8.0, options={"Nonlinear": True})
        analysis = objs["analysis"]
        lines = []
        sim.solve(analysis, working_dir=self.tmp, progress=lines.append)
        lam1 = eigen.first_factor(analysis)
        curve = eigen.nonlinear_data(eigen.first_buckling_result(analysis))
        self.assertIsNotNone(curve, lines)
        self.assertGreater(len(curve["factors"]), 10)
        self.assertEqual(curve["factors"], sorted(curve["factors"]))
        self.assertAlmostEqual(curve["amplitude"], 0.01 * 4 * 50000 / 20200, places=4)  # 1 % of 4V/S
        collapse = curve["collapse"]
        self.assertIsNotNone(collapse)
        self.assertLess(collapse, lam1)  # a small imperfection: near and below the linear factor
        self.assertGreater(collapse, 0.8 * lam1)
        out = sim.write_report(analysis, folder=self.tmp, compile=bool(sim.find_tex()[0]))
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(r"\usepackage{pgfplots}", text)
        self.assertIn("Collapse load factor", text)
        if sim.find_tex()[0]:
            self.assertTrue(out["pdf"] and os.path.isfile(out["pdf"]), out["message"])
        App.Console.PrintMessage(f"\n[nonlinear buckling] collapse {collapse:.4f} = {collapse / lam1:.3f} lambda1 "
                                 f"({curve['basis']}), {len(curve['factors'])} increments\n")

if __name__ == "__main__":
    unittest.main()
