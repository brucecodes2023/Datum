# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-05: the validation cases of Vector Labs, headless.

The fast set (every case on a coarse mesh) runs once and is checked case by case: a case passes when every marked
quantity is within its tolerance of the published reference; a case marked known_gap in cases.json must stay within
the runner's envelope. The closed forms behind the derived references and the numbers in cases.json are checked
without the solver. The fast set skips when ccx is absent.
"""

import json
import math
import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App

import bruceworx_sim as sim
import bruceworx_sim_eigen as eigen
import bruceworx_validation as val
import bruceworx_validation_cases as vc


class TestCases(unittest.TestCase):
    """cases.json and the closed forms, without the solver."""

    @classmethod
    def setUpClass(cls):
        cls.doc = val.load_document()
        cls.cases = {c["id"]: c for c in val.load_cases()}

    def test_every_case_is_complete(self):
        ids = [c["id"] for c in val.load_cases()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), sorted(["B-ST1", "B-ST2", "B-ST3", "B-MO1", "B-MO2", "B-MO3", "B-BU1", "B-TH1", "B-TH2",
                                              "B-TT1", "G-TS1", "G-PM1", "G-NB1"]))
        self.assertEqual(self.doc["cfd"], [])  # the hook for SIM-06
        for case in self.cases.values():
            self.assertIn(case["builder"], vc.BUILDERS, case["id"])
            self.assertTrue(case["licence"] and case["setup"], case["id"])
            cit = case["citation"]
            self.assertTrue(cit["document"] and cit["location"], case["id"])
            if case["tier"] == "B":
                self.assertTrue(cit["url"].startswith("http"), case["id"])
            self.assertGreater(case["tolerance"], 0)
            self.assertTrue(case["references"])
            keys = [r["key"] for r in case["references"]]
            self.assertEqual(len(keys), len(set(keys)), case["id"])
            self.assertIn(case["primary"], keys)
            for r in case["references"]:
                self.assertTrue(r["unit"] and r["label"], (case["id"], r))
                self.assertTrue(math.isfinite(r["value"]))
            self.assertEqual(len(case["mesh"]["slow"]), 3, case["id"])
            self.assertEqual(case["mesh"]["slow"], sorted(case["mesh"]["slow"], reverse=True), case["id"])
            self.assertLessEqual(case["mesh"]["slow"][-1], case["mesh"]["fast"], case["id"])
        self.assertEqual(val.sizes_for(self.cases["B-ST1"], "fast"), [120.0])
        self.assertEqual(len(val.sizes_for(self.cases["B-ST1"], "slow")), 3)

    def test_hollow_sphere_closed_form_reproduces_the_manual(self):
        for r, ref in ((1.25, 30.625), (1.5, 32.5), (1.75, 28.482)):
            self.assertAlmostEqual(vc.hollow_sphere_temperature(r), ref, delta=6e-4)
        self.assertAlmostEqual(vc.hollow_sphere_temperature(1.0), 20.0)
        self.assertAlmostEqual(vc.hollow_sphere_temperature(2.0), 20.0)

    def test_cantilever_cylinder_beam_theory_reproduces_the_manual(self):
        theory = vc.sslv301_theory()
        self.assertAlmostEqual(theory["tip_m"], 1.552e-3, delta=1e-6)
        self.assertAlmostEqual(theory["root_Pa"], 169.8e6, delta=0.2e6)

    def test_thick_cylinder_thermal_stress_closed_form(self):
        a, b, E, nu, alpha, dT = 50.0, 100.0, 210000.0, 0.3, 12e-6, 100.0
        self.assertAlmostEqual(vc.lame_thermal(a, a, b, E, nu, alpha, dT)[0], 0.0, places=9)
        self.assertAlmostEqual(vc.lame_thermal(b, a, b, E, nu, alpha, dT)[0], 0.0, places=9)
        n, force, scale = 4000, 0.0, 0.0
        for i in range(n):  # no net axial force over the section
            r = a + (b - a) * (i + 0.5) / n
            sz = vc.lame_thermal(r, a, b, E, nu, alpha, dT)[2]
            force += sz * 2 * math.pi * r * (b - a) / n
            scale += abs(sz) * 2 * math.pi * r * (b - a) / n
        self.assertLess(abs(force) / scale, 1e-4)
        # the inside is in compression (it wants to grow, the outside holds it), the outside in tension
        self.assertLess(vc.lame_thermal(a, a, b, E, nu, alpha, dT)[1], 0)
        self.assertGreater(vc.lame_thermal(b, a, b, E, nu, alpha, dT)[1], 0)
        refs = {r["key"]: r["value"] for r in self.cases["G-TS1"]["references"]}
        self.assertAlmostEqual(refs["shoop_a"], vc.lame_thermal(a, a, b, E, nu, alpha, dT)[1])

    def test_circular_plate_buckling_reproduces_the_manual_and_the_json(self):
        self.assertAlmostEqual(vc.circular_plate_k2("clamped"), 14.68, delta=0.005)
        manual = vc.circular_plate_fcr(0.115, 0.0005, 2.1e11, 0.3, "clamped", shear=False)  # N/m
        self.assertAlmostEqual(manual, 2668.315, delta=0.5)  # the manual rounds the coefficient to 14.68 (j_{1,1}^2 = 14.682)
        self.assertAlmostEqual(vc.circular_plate_k2("simple", 0.3), 4.198, delta=0.002)
        ref = self.cases["B-BU1"]["references"][0]["value"]
        self.assertAlmostEqual(ref, vc.circular_plate_fcr(60.0, 5.0, 210000.0, 0.3, "simple"), delta=1e-6)
        self.assertLess(ref, vc.circular_plate_fcr(60.0, 5.0, 210000.0, 0.3, "simple", shear=False))
        self.assertAlmostEqual(self.cases["B-BU1"]["source_check"]["value"], 2668.315)

    def test_sphere_series_confirms_the_diameter(self):
        times = [400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400]
        tc = [334, 500, 618, 706, 774, 828, 872, 902, 923, 942, 956]
        ts = [461, 608, 696, 774, 828, 868, 902, 923, 942, 956, 962]
        for radius, expect_close in ((0.1, True), (0.2, False)):
            series = vc.sphere_cooling_series(times, radius, 48.822, 7200 * 669.0, 232.5, 1000.0, 20.0)
            worst = max(max(abs(c - r1) / r1, abs(s - r2) / r2) for (c, s), r1, r2 in zip(series, tc, ts))
            if expect_close:
                self.assertLess(worst, 0.035)  # the manual's chart readings, uncertainty larger below 600 s
            else:
                self.assertGreater(worst, 0.3)
        refs = {r["key"]: r["value"] for r in self.cases["B-TT1"]["references"]}
        self.assertEqual(refs["Tc_400"], 334)
        self.assertEqual(refs["Ts_2400"], 962)

    def test_tensioned_and_bowed_closed_forms(self):
        refs = {r["key"]: r["value"] for r in self.cases["G-PM1"]["references"]}
        E, rho, L, w, T = 210000.0, 7850.0, 500.0, 10.0, 10000.0
        I = w ** 4 / 12.0
        f0 = eigen.tensioned_frequency(1, L, w, E, rho, 0.0)
        self.assertAlmostEqual(refs["f1"], f0 * math.sqrt(1 + T * L * L / (math.pi ** 2 * E * I)), places=6)
        f02 = eigen.tensioned_frequency(2, L, w, E, rho, 0.0)
        self.assertAlmostEqual(refs["f2"], f02 * math.sqrt(1 + T * L * L / (4 * math.pi ** 2 * E * I)), places=6)
        # a synthetic Southwell curve: bow a0 = 0.1, lambda_cr = 1.7 (the linear amplification)
        lcr, a0 = 1.7, 0.1
        lams = [lcr * f for f in (0.2, 0.35, 0.5, 0.6, 0.7, 0.8, 0.9)]
        ds = [a0 * (lam / lcr) / (1 - lam / lcr) for lam in lams]
        got, c = vc.southwell(lams, ds, lcr)
        self.assertAlmostEqual(got, lcr, places=9)
        self.assertAlmostEqual(c * got, a0, places=9)
        refs = {r["key"]: r["value"] for r in self.cases["G-NB1"]["references"]}
        self.assertAlmostEqual(refs["lambda_southwell"], eigen.euler_column(500.0, 10.0, 210000.0, 1000.0), places=9)

    def test_comparison_and_marks(self):
        case = {"tolerance": 0.02, "references": [{"key": "a", "value": 100.0, "unit": "m", "label": "a"},
                                                  {"key": "b", "value": -5.0, "unit": "m", "label": "b", "informative": True}]}
        rows = val.evaluate(case, {"a": 101.0, "b": -9.0})
        self.assertAlmostEqual(rows[0]["error"], 0.01)
        self.assertTrue(rows[0]["passed"])
        self.assertAlmostEqual(rows[1]["error"], -0.8)  # (-9 - (-5)) / 5: below the reference, negative values handled
        self.assertEqual(val.mark(case, rows), "PASS")  # the informative row is not marked
        rows = val.evaluate(case, {"a": 104.0})
        self.assertEqual(val.mark(case, rows), "FAIL")
        gap = dict(case, status="known_gap")
        self.assertEqual(val.mark(gap, rows), "GAP")
        self.assertEqual(val.mark(gap, val.evaluate(case, {"a": 140.0})), "FAIL")
        self.assertEqual(val.mark(case, val.evaluate(case, {}), error="x"), "ERROR")

    def test_cfd_hook_and_unknown_kinds(self):
        case = {"id": "X-CF", "kind": "cfd", "title": "t", "references": [], "mesh": {"fast": 1.0, "slow": [3.0, 2.0, 1.0]}}
        self.assertEqual(val.run_case(case)["mark"], "SKIPPED")

        def fake(c, size, work_dir, progress=None):
            return {"id": c["id"], "title": "", "kind": "cfd", "size": size, "nodes": 10, "elements": 5, "seconds": 0.0,
                    "values": {"cl": 1.0}, "rows": [], "error": None, "mark": "PASS"}

        val.register_runner("cfd", fake)
        try:
            out = val.run_set("slow", cases=[dict(case, references=[{"key": "cl", "value": 1.0, "unit": "-"}], primary="cl")])
            self.assertEqual(out["results"][0]["mark"], "PASS")
            self.assertEqual(len(out["results"][0]["series"]), 3)
        finally:
            val._RUNNERS.pop("cfd", None)

    def test_report_source_packages_are_seeded(self):
        import re

        run = {"set": "slow", "date": "2026-10-31 12:00", "ccx": "CalculiX 2.23", "seconds": 12.0, "counts": {"PASS": 1},
               "cases": {"X": {"citation": {"document": "D", "location": "L", "url": "https://x.y"}, "licence": "FDL", "setup": "s & t"}},
               "results": [{"id": "X", "title": "A & B", "mark": "PASS", "nodes": 100, "size": 3.0, "seconds": 1.0, "error": None,
                            "primary": "q", "rows": val.evaluate({"tolerance": 0.01, "references": [{"key": "q", "value": 2.0e-6, "unit": "m", "label": "q"}]}, {"q": 2.01e-6}),
                            "series": [{"size": 6.0, "nodes": 50, "value": 2.1e-6, "error": 0.05, "mark": "FAIL"},
                                       {"size": 3.0, "nodes": 100, "value": 2.01e-6, "error": 0.005, "mark": "PASS"}]}]}
        text = val.report_source(run)
        self.assertIn(r"\begin{axis}", text)
        self.assertIn("A \\& B", text)
        self.assertIn("Reference & Result & Error", text)
        seed = os.path.normpath(os.path.join(App.getHomePath(), "..", "..", "package", "bruceworx", "tectonic-seed", "seed.tex"))
        if os.path.isfile(seed):
            with open(seed, encoding="utf-8") as handle:
                seeded = set(re.findall(r"\\usepackage(?:\[[^]]*\])?\{([^}]+)\}", handle.read()))
            used = set(re.findall(r"\\usepackage(?:\[[^]]*\])?\{([^}]+)\}", text))
            self.assertTrue(used <= seeded, used - seeded)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestFastSet(unittest.TestCase):
    """Every case on its coarse mesh, solved once; one test per case reads the result."""

    @classmethod
    def setUpClass(cls):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        cls.tmp = tempfile.mkdtemp(prefix="bw-validation-test-")
        cls.lines = []
        t0 = time.time()
        cls.vrun = val.run_set("fast", work_dir=cls.tmp, progress=lambda text, f: cls.lines.append(f"{100 * f:.0f}% {text}"))
        cls.elapsed = time.time() - t0
        cls.by_id = {r["id"]: r for r in cls.vrun["results"]}
        cls.cases = {c["id"]: c for c in val.load_cases()}
        App.Console.PrintMessage("\n" + table(cls.vrun) + f"\n[validation] fast set: {cls.elapsed:.0f} s\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, case_id):
        r = self.by_id[case_id]
        self.assertIsNone(r["error"], r["error"])
        bad = [f"{row['key']}: {row['result']} vs {row['reference']} ({100 * row['error']:+.2f} %, tol {100 * row['tolerance']:g} %)"
               for row in r["rows"] if not row["informative"] and not row["passed"]]
        if self.cases[case_id].get("status") == "known_gap":
            self.assertEqual(r["mark"], "GAP", (r["mark"], bad))  # still a gap, and inside the envelope
        else:
            self.assertEqual(r["mark"], "PASS", bad)

    def test_all_cases_ran(self):
        self.assertEqual(sorted(self.by_id), sorted(self.cases))
        self.assertEqual(self.vrun["counts"].get("ERROR", 0), 0)
        self.assertEqual(len(self.lines) - 1, len(self.cases))  # one line per case and the last

    def test_st1_le10(self):
        self.check("B-ST1")
        self.assertLess(self.by_id["B-ST1"]["values"]["syy_D"], 0)  # compressive, as NAFEMS

    def test_st2_sslv07(self):
        self.check("B-ST2")

    def test_st3_sslv301(self):
        self.check("B-ST3")

    def test_mo1_fv52(self):
        self.check("B-MO1")

    def test_mo2_sdlv401(self):
        self.check("B-MO2")

    def test_mo3_sdlv100(self):
        self.check("B-MO3")

    def test_bu1_sslv139(self):
        self.check("B-BU1")

    def test_th1_tplv06(self):
        self.check("B-TH1")

    def test_th2_tplv304(self):
        self.check("B-TH2")

    def test_tt1_ttlv01(self):
        self.check("B-TT1")

    def test_gap_thick_cylinder(self):
        self.check("G-TS1")

    def test_gap_tensioned_beam(self):
        self.check("G-PM1")

    def test_gap_bowed_column(self):
        self.check("G-NB1")

    def test_report_of_the_run(self):
        out = val.write_report(self.vrun, folder=self.tmp, compile=bool(sim.find_tex()[0]))
        with open(out["tex"], encoding="utf-8") as handle:
            text = handle.read()
        for case_id in self.cases:
            self.assertIn(case_id, text)
        self.assertIn("Source.", text)
        self.assertIn("Licence.", text)
        with open(os.path.join(out["dir"], "results.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["set"], "fast")
        if sim.find_tex()[0]:
            self.assertTrue(out["pdf"] and os.path.isfile(out["pdf"]), out["message"])


def table(run):
    lines = [f"{'case':7} {'mark':5} {'nodes':>7} {'mesh':>6} {'s':>5}  worst error  quantities"]
    for r in run["results"]:
        worst = val.worst_error(r["rows"])
        lines.append(f"{r['id']:7} {r['mark']:5} {r.get('nodes') or 0:7d} {r.get('size') or 0:6g} {r['seconds']:5.1f}  "
                     f"{'--' if worst is None else '%6.2f %%' % (100 * worst):>10}  "
                     + ", ".join(f"{row['key']}={row['result']:.5g}" for row in r["rows"] if row["result"] is not None)[:150])
    return "\n".join(lines)


if __name__ == "__main__":
    unittest.main()
