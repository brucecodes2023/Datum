# SPDX-License-Identifier: LGPL-2.1-or-later
"""Airflow studies with OpenFOAM (SIM-06 phase 1), headless.

Case writer tests need no OpenFOAM. The benchmark tests (laminar pipe, sphere at Re = 100) run the real solver at
the coarse preset and skip when OpenFOAM-v2606 is not installed. Typical times on an M-series Mac: pipe 20 s,
sphere 1 minute; neither needs the slow marker. Runs with plain python3 as well as with FreeCADCmd -t.
"""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

import bruceworx_cfd as cfd

INSTALL = cfd.find_openfoam()
needs_foam = unittest.skipUnless(INSTALL is not None, "OpenFOAM is not installed")


def sphere_parts(diameter=0.1):
    return [cfd.Part("ball", cfd.sphere_triangles(diameter / 2))]


def parse_ok(path):
    """A dictionary parses when it has the FoamFile header and balanced braces, brackets and quotes."""
    text = open(path, errors="replace").read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    stack = []
    pairs = {")": "(", "}": "{", "]": "["}
    for ch in re.sub(r'"[^"]*"', '""', text):
        if ch in "({[":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack and text.count('"') % 2 == 0


class TestAir(unittest.TestCase):
    def test_sea_level_20c(self):
        a = cfd.air_properties(20.0, 0.0)
        self.assertAlmostEqual(a["rho"], 1.204, delta=0.002)
        self.assertAlmostEqual(a["nu"], 1.51e-5, delta=0.02e-5)

    def test_altitude_thins_the_air(self):
        a0, a2 = cfd.air_properties(15.0, 0.0), cfd.air_properties(15.0, 2000.0)
        self.assertAlmostEqual(a2["p"] / a0["p"], 0.7846, delta=0.003)  # ISA at 2000 m
        self.assertLess(a2["rho"], a0["rho"] * 0.80)
        self.assertGreater(a2["nu"], a0["nu"])


class TestDetection(unittest.TestCase):
    def fake_app(self, root, name):
        app = os.path.join(root, name)
        launcher = os.path.join(app, "Contents", "Resources", "etc", "openfoam")
        os.makedirs(os.path.dirname(launcher))
        open(launcher, "w").write("#!/bin/sh\n")
        os.chmod(launcher, 0o755)
        return app

    def test_found_and_version_warning(self):
        with tempfile.TemporaryDirectory() as t:
            ok = cfd.find_openfoam(pref=self.fake_app(t, "OpenFOAM-v2606.app"))
            self.assertTrue(ok["tested"])
            self.assertEqual(ok["warning"], "")
            other = cfd.find_openfoam(pref=self.fake_app(t, "OpenFOAM-v2512.app"))
            self.assertFalse(other["tested"])
            self.assertIn("v2606", other["warning"])

    def test_missing_openfoam_fails_the_precheck_with_install_text(self):
        issues, _est = cfd.precheck(sphere_parts(), cfd.Setup(), install=None)
        errors = [t for lvl, t in issues if lvl == "error"]
        self.assertTrue(any("Install OpenFOAM" in t and "brew install gerlero/openfoam/openfoam@2606" in t for t in errors))

    def test_never_installs(self):
        self.assertIn("brew install", cfd.INSTALL_COMMAND)
        self.assertTrue(cfd.INSTALL_URL.startswith("https://github.com/gerlero/openfoam-app"))


class TestRunDir(unittest.TestCase):
    @needs_foam
    def test_a_case_path_with_a_space_runs(self):
        with tempfile.TemporaryDirectory() as t:
            folder = os.path.join(t, "Heuretes FreeCAD", "cfd", "case")
            os.makedirs(folder)
            alias = cfd.run_dir(folder)
            self.assertNotIn(" ", alias)
            self.assertEqual(os.path.realpath(alias), os.path.realpath(folder))
            cfd.write_pipe_case(folder, n_axial=60, n_radial=8)
            run = cfd.Runner(folder, INSTALL).start()
            run.wait()
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))

    @needs_foam
    def test_a_parallel_run_under_a_path_with_a_space(self):
        # mpirun hands the ranks the physical folder (with the space) unless PWD is passed on; and PMIx needs the
        # loopback settings (MPI_ENV). The small sphere is forced parallel below the usual cell threshold.
        saved = cfd.MPI_MIN_CELLS, cfd.mpi_known_broken
        cfd.MPI_MIN_CELLS, cfd.mpi_known_broken = 0, lambda *a: False
        try:
            with tempfile.TemporaryDirectory() as t:
                folder = os.path.join(t, "Heuretes FreeCAD", "cfd", "sphere")
                info = cfd.write_case(folder, sphere_parts(), cfd.Setup(speed=0.015, turbulence="laminar",
                                                                         preset="coarse", n_procs=2))
                self.assertEqual(info["n_procs"], 2)
                run = cfd.Runner(folder, INSTALL).start()
                run.wait()
                log = cfd._read(os.path.join(folder, "log.run"))
                self.assertEqual(run.result(), "done", log)
                self.assertNotIn("running serial", log)
                self.assertIn("nProcs : 2", cfd._read(os.path.join(folder, "log.simpleFoam")))
        finally:
            cfd.MPI_MIN_CELLS, cfd.mpi_known_broken = saved


class TestGeometry(unittest.TestCase):
    def test_watertight(self):
        self.assertEqual(cfd.open_edges(cfd.sphere_triangles(0.05)), 0)
        box = cfd.box_triangles((0, 0, 0), (1, 1, 1))
        self.assertEqual(cfd.open_edges(box), 0)
        self.assertGreater(cfd.open_edges(box[:-1]), 0)
        self.assertAlmostEqual(cfd.volume(box), 1.0, places=9)

    def test_precheck_flags_an_open_part(self):
        box = cfd.box_triangles((0, 0, 0), (0.1, 0.1, 0.1))[:-1]
        issues, _ = cfd.precheck([cfd.Part("lid", box)], cfd.Setup(), install={"warning": ""})
        self.assertTrue(any(l == "error" and "not watertight" in t for l, t in issues))

    def test_angle_of_attack_rotates_the_body_into_the_flow_frame(self):
        tris = cfd.box_triangles((0, -0.1, -0.01), (1, 0.1, 0.01))  # a flat plate along X
        rot = cfd.rotate_y(tris, 10.0, (0.5, 0, 0))
        lo, hi = cfd.bounds(rot)
        self.assertAlmostEqual(hi[2] - lo[2], math.sin(math.radians(10)) * 1.0 + math.cos(math.radians(10)) * 0.02, delta=1e-6)


class TestCaseWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, parts=None, name="c", **kw):
        setup = cfd.Setup(speed=kw.pop("speed", 10.0), **kw)
        info = cfd.write_case(os.path.join(self.tmp, name), parts or sphere_parts(), setup)
        return info

    def test_files_and_dictionaries_parse(self):
        info = self.write()
        folder = info["folder"]
        for rel in ("system/controlDict", "system/fvSchemes", "system/fvSolution", "system/decomposeParDict",
                    "system/blockMeshDict", "system/snappyHexMeshDict", "0/U", "0/p", "0/k", "0/omega", "0/nut",
                    "constant/transportProperties", "constant/turbulenceProperties", "constant/triSurface/ball.stl", "Allrun"):
            path = os.path.join(folder, rel)
            self.assertTrue(os.path.isfile(path), rel)
            if rel != "constant/triSurface/ball.stl":
                self.assertTrue(parse_ok(path), rel + " does not parse")
        self.assertTrue(os.access(os.path.join(folder, "Allrun"), os.X_OK))
        self.assertIn("kOmegaSST", open(os.path.join(folder, "constant/turbulenceProperties")).read())

    @needs_foam
    def test_openfoam_reads_every_dictionary(self):
        folder = self.write(turbulence="kOmegaSST", farfield="symmetry")["folder"]
        files = ["system/controlDict", "system/fvSchemes", "system/fvSolution", "system/blockMeshDict",
                 "system/snappyHexMeshDict", "system/decomposeParDict", "constant/turbulenceProperties",
                 "constant/transportProperties", "0/U", "0/p", "0/k", "0/omega", "0/nut"]
        script = "; ".join(f"foamDictionary -expand {f} > /dev/null || echo BAD {f}" for f in files)
        out = subprocess.run([INSTALL["launcher"], "-c", f"cd '{folder}'; {script}"], capture_output=True, text=True, timeout=120)
        self.assertNotIn("BAD", out.stdout + out.stderr, out.stdout + out.stderr)

    def test_laminar_has_no_turbulence_fields(self):
        folder = self.write(turbulence="laminar")["folder"]
        self.assertFalse(os.path.exists(os.path.join(folder, "0", "k")))
        self.assertIn("laminar", open(os.path.join(folder, "constant/turbulenceProperties")).read())

    def test_domain_follows_the_bounding_box(self):
        small = self.write(sphere_parts(0.1), "small")
        big = self.write(sphere_parts(0.2), "big")
        a, b = small["geometry"], big["geometry"]
        f = cfd.DOMAIN_FACTORS
        (lo, hi), (dlo, dhi) = a["bbox"], a["domain"]
        L = a["length"]
        self.assertAlmostEqual(L, 0.1, places=6)
        self.assertAlmostEqual(lo[0] - dlo[0], f["upstream"] * L, places=6)
        self.assertAlmostEqual(dhi[0] - hi[0], f["downstream"] * L, places=6)
        self.assertAlmostEqual(dhi[1] - hi[1], f["lateral"] * L, places=6)
        self.assertEqual(a["cells"], b["cells"])  # twice the body: same cell counts, twice the size
        self.assertAlmostEqual(b["dx0"], 2 * a["dx0"], places=6)
        self.assertAlmostEqual(b["domain"][1][0] - b["domain"][0][0], 2 * (a["domain"][1][0] - a["domain"][0][0]), places=6)

    def test_domain_factors_are_editable(self):
        info = self.write(factors={"upstream": 2.0, "downstream": 3.0, "lateral": 1.5})
        g = info["geometry"]
        self.assertAlmostEqual(g["bbox"][0][0] - g["domain"][0][0], 2.0 * g["length"], places=6)
        self.assertAlmostEqual(g["domain"][1][0] - g["bbox"][1][0], 3.0 * g["length"], places=6)
        text = open(os.path.join(info["folder"], "system/blockMeshDict")).read()
        self.assertIn("inlet", text)

    def test_presets_grow_the_mesh(self):
        counts = []
        for preset in ("coarse", "medium", "fine"):
            counts.append(cfd.estimate(sphere_parts(), cfd.Setup(preset=preset))["cells"])
        self.assertLess(counts[0], counts[1])
        self.assertLess(counts[1], counts[2])
        text = open(os.path.join(self.write(preset="medium")["folder"], "system/snappyHexMeshDict")).read()
        self.assertIn("level (5 5)", text)

    def test_boundary_conditions(self):
        folder = self.write(speed=12.0, farfield="slip")["folder"]
        u = open(os.path.join(folder, "0/U")).read()
        self.assertIn("uniform (12 0 0)", u)
        self.assertIn("slip", u)
        self.assertIn("noSlip", u)
        self.assertIn("grp_ball", u)
        p = open(os.path.join(folder, "0/p")).read()
        self.assertRegex(p, r"outlet\s*\{\s*type\s+fixedValue")
        self.assertIn("omegaWallFunction", open(os.path.join(folder, "0/omega")).read())
        self.assertIn("kqRWallFunction", open(os.path.join(folder, "0/k")).read())
        self.assertIn("nutkWallFunction", open(os.path.join(folder, "0/nut")).read())
        sym = self.write(farfield="symmetry", name="sym")["folder"]
        self.assertIn("symmetryPlane", open(os.path.join(sym, "0/U")).read())

    def test_forces_per_part_with_reference_values(self):
        parts = [cfd.Part("body", cfd.box_triangles((0, -0.05, -0.05), (0.2, 0.05, 0.05))),
                 cfd.Part("arm", cfd.box_triangles((0.05, -0.2, -0.01), (0.1, 0.2, 0.01)))]
        info = self.write(parts, a_ref=0.0123, l_ref=0.45, speed=8.0)
        text = open(os.path.join(info["folder"], "system/controlDict")).read()
        for n in ("body", "arm"):
            self.assertIn(f"forces_{n}", text)
            self.assertIn(f"forceCoeffs_{n}", text)
            self.assertTrue(os.path.isfile(os.path.join(info["folder"], "constant/triSurface", n + ".stl")))
        self.assertIn("Aref            0.0123", text)
        self.assertIn("lRef            0.45", text)
        self.assertIn("magUInf         8", text)
        self.assertIn("flow_inlet", text)

    def test_air_in_the_case_follows_temperature_and_altitude(self):
        info = self.write(temperature_c=-10.0, altitude_m=3000.0)
        nu = float(re.search(r"nu \[[^\]]*\] ([\d.e+-]+);", open(os.path.join(info["folder"], "constant/transportProperties")).read()).group(1))
        self.assertAlmostEqual(nu, cfd.air_properties(-10.0, 3000.0)["nu"], delta=1e-10)

    def test_angle_of_attack_is_in_the_stl(self):
        parts = [cfd.Part("plate", cfd.box_triangles((0, -0.1, -0.005), (0.5, 0.1, 0.005)))]
        info = self.write(parts, aoa=15.0)
        tris = cfd.read_stl(os.path.join(info["folder"], "constant/triSurface/plate.stl"))
        lo, hi = cfd.bounds(tris)
        self.assertGreater(hi[2] - lo[2], 0.12)

    def test_allrun_stages(self):
        real = cfd.mpi_known_broken
        cfd.mpi_known_broken = lambda *a: False
        try:
            text = open(os.path.join(self.write(n_procs=4, cell_limit=10**9, preset="medium")["folder"], "Allrun")).read()
        finally:
            cfd.mpi_known_broken = real
        for word in ("blockMesh", "snappyHexMesh", "decomposePar", "mpirun", "simpleFoam", "reconstructPar"):
            self.assertIn(word, text)

    def test_log_reader_and_convergence_rule(self):
        folder = os.path.join(self.tmp, "logs")
        os.makedirs(os.path.join(folder, "postProcessing", "forces_ball", "0"))
        os.makedirs(os.path.join(folder, "postProcessing", "forceCoeffs_ball", "0"))
        os.makedirs(os.path.join(folder, "postProcessing", "flow_inlet", "0"))
        os.makedirs(os.path.join(folder, "postProcessing", "flow_outlet", "0"))
        log = []
        forces, coeffs, fin, fout = ["# hdr"], ["# hdr"], ["# hdr"], ["# hdr"]
        for i in range(1, 301):
            log += [f"Time = {i}", f"smoothSolver:  Solving for Ux, Initial residual = {1e-3 / i:g}, Final residual = 1e-9, No Iterations 1",
                    f"GAMG:  Solving for p, Initial residual = {2e-3 / i:g}, Final residual = 1e-9, No Iterations 2"]
            fx = 1.0 + 0.5 / i
            forces.append(f"{i} ({fx} 0 0) (0 0 0) (0 0 0)".replace("(", "").replace(")", ""))
            coeffs.append(f"{i} {fx} 0 0 0 0 0 0")
            fin.append(f"{i} -1.0")
            fout.append(f"{i} 1.0")
        open(os.path.join(folder, "log.simpleFoam"), "w").write("\n".join(log) + "\n")
        open(os.path.join(folder, "postProcessing/forces_ball/0/force.dat"), "w").write("\n".join(forces) + "\n")
        open(os.path.join(folder, "postProcessing/forceCoeffs_ball/0/coefficient.dat"), "w").write("\n".join(coeffs) + "\n")
        open(os.path.join(folder, "postProcessing/flow_inlet/0/surfaceFieldValue.dat"), "w").write("\n".join(fin) + "\n")
        open(os.path.join(folder, "postProcessing/flow_outlet/0/surfaceFieldValue.dat"), "w").write("\n".join(fout) + "\n")
        info = {"parts": {"ball": {"name": "ball"}}, "refs": {"a_ref": 1.0, "l_ref": 1.0},
                "air": {"rho": 1.0}, "setup": {"speed": 1.0}}
        reader = cfd.LogReader(folder).update()
        self.assertEqual(reader.iteration, 300)
        self.assertLess(reader.latest()["Ux"], 1e-4)
        conv = cfd.convergence(folder, info, reader)
        self.assertTrue(conv["converged"], conv)
        # a run still drifting by more than 0.5 % is not converged
        forces2 = ["# hdr"] + [f"{i} {1.0 + 0.02 * i / 300} 0 0" for i in range(1, 301)]
        open(os.path.join(folder, "postProcessing/forces_ball/0/force.dat"), "w").write("\n".join(forces2) + "\n")
        self.assertFalse(cfd.convergence(folder, info, reader)["converged"])


class TestBenchmarks(unittest.TestCase):
    """The plan's acceptance runs at the coarse preset (chapter 67)."""

    @needs_foam
    def test_laminar_pipe_pressure_gradient(self):
        with tempfile.TemporaryDirectory() as t:
            folder = os.path.join(t, "pipe")
            cfd.write_pipe_case(folder)
            t0 = time.time()
            run = cfd.Runner(folder, INSTALL).start()
            run.wait()
            seconds = time.time() - t0
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))
            ref = cfd.pipe_reference()
            dpdx = -cfd.pipe_pressure_gradient(folder)
            err = abs(dpdx - ref["dpdx"]) / ref["dpdx"]
            print(f"\n[pipe] dp/dx = {dpdx:.4f} Pa/m, Hagen-Poiseuille {ref['dpdx']:.4f} (plan 2.91), error {err * 100:.2f} %, "
                  f"{seconds:.0f} s, entrance length {ref['entrance']:.3f} m, measured at x = 0.35 to 0.55 m")
            self.assertLess(err, 0.02)
            self.assertAlmostEqual(dpdx, 2.91, delta=2.91 * 0.02)

    @needs_foam
    def test_sphere_drag_at_re_100(self):
        D = 0.1
        air = cfd.air_properties(20.0, 0.0)
        speed = 100.0 * air["nu"] / D
        with tempfile.TemporaryDirectory() as t:
            folder = os.path.join(t, "sphere")
            setup = cfd.Setup(speed=speed, turbulence="laminar", preset="coarse", a_ref=math.pi * D * D / 4, l_ref=D)
            info = cfd.write_case(folder, sphere_parts(D), setup)
            t0 = time.time()
            run = cfd.Runner(folder, INSTALL).start()
            run.wait()
            seconds = time.time() - t0
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))
            conv = cfd.convergence(folder, info)
            table = cfd.forces_table(folder, info)
            cd = table["total"]["Cd"]
            ref = 24.0 / 100 * (1 + 0.15 * 100 ** 0.687)
            err = abs(cd - ref) / ref
            cells = re.findall(r"cells:\s+(\d+)", cfd._read(os.path.join(folder, "log.checkMesh")))
            print(f"\n[sphere] Cd = {cd:.4f}, Schiller-Naumann {ref:.4f}, error {err * 100:.2f} %, {seconds:.0f} s, "
                  f"cells {cells[-1] if cells else '?'}, procs {info['n_procs']}, converged: {conv['text']}")
            self.assertLess(err, 0.05)
            self.assertTrue(conv["converged"], conv)
            self.assertLess(abs(table["total"]["Cl"]), 0.01)


try:
    import FreeCAD as App
except ImportError:  # plain python3: the document tests below are skipped
    App = None


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


@unittest.skipUnless(App is not None, "needs FreeCAD")
class TestAirflowStudy(unittest.TestCase):
    """The study glue: New Study type, parts, pre-check, case, run, results, report."""

    def setUp(self):
        close_all()
        self.doc = App.newDocument("Air")
        self.ball = self.doc.addObject("Part::Sphere", "Ball")
        self.ball.Radius = 50.0  # mm
        self.doc.recompute()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        close_all()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def study(self, **opts):
        import bruceworx_sim as sim

        plan = sim.StudyPlan(name="Wind", study_type="external_flow", model=self.ball, options=opts)
        return sim.new_study(self.doc, plan)["analysis"]

    def test_new_study_makes_an_airflow_study_with_settings(self):
        import bruceworx_sim as sim

        a = self.study(FlowSpeed=12.5, AngleOfAttack=4.0, MeshPreset="medium")
        self.assertTrue(sim.is_study(a))
        self.assertEqual(sim.study_kind(a), "external_flow")
        self.assertEqual((a.FlowSpeed, a.AngleOfAttack, a.MeshPreset), (12.5, 4.0, "medium"))
        self.assertIn("external_flow", [k for k, _l, ok in sim.STUDY_TYPES if ok])
        self.assertIs(a.Model, self.ball)

    def test_parts_are_scaled_to_metres_and_watertight(self):
        a = self.study()
        parts = cfd.study_parts(a)
        self.assertEqual(len(parts), 1)
        lo, hi = cfd.bounds(parts[0].tris)
        self.assertAlmostEqual(hi[0] - lo[0], 0.1, delta=0.001)
        self.assertEqual(cfd.open_edges(parts[0].tris), 0)

    def test_precheck_through_the_study_frame(self):
        import bruceworx_sim as sim

        a = self.study()
        issues = sim.precheck(a)
        self.assertTrue(all(level in ("error", "warning") for level, _t in issues))
        if INSTALL is None:
            self.assertTrue(any("Install OpenFOAM" in t for _l, t in issues))

    def test_prepare_run_writes_the_case_under_the_study_folder(self):
        a = self.study(Turbulence="laminar", FlowSpeed=0.015)
        folder, info = cfd.prepare_run(a, root=self.tmp) if INSTALL else (None, None)
        if INSTALL is None:
            self.skipTest("OpenFOAM is not installed")
        self.assertTrue(folder.startswith(self.tmp))
        self.assertIn("Air", folder)
        self.assertTrue(os.path.isfile(os.path.join(folder, "system", "snappyHexMeshDict")))
        self.assertEqual(a.CaseFolder, folder)

    @needs_foam
    def test_end_to_end_run_results_and_report(self):
        import bruceworx_sim as sim

        a = self.study(Turbulence="laminar", FlowSpeed=0.0150593, ReferenceLength=0.1)
        a.ReferenceArea = math.pi * 0.05 ** 2
        runner, info = cfd.start_run(a, root=self.tmp)
        runner.wait()
        self.assertEqual(runner.result(), "done", cfd._read(os.path.join(info["folder"], "log.run")))
        summary = cfd.save_summary(a, info["folder"], "done")
        cd = summary["forces"]["total"]["Cd"]
        self.assertAlmostEqual(cd, 1.09, delta=0.06)
        self.assertTrue(summary["convergence"]["converged"], summary["convergence"])
        # results through the FEM post pipeline
        out = cfd.load_results(a, info["folder"])
        self.assertTrue(os.path.isfile(out["vtu"]))
        self.assertIn(out["result"], a.Group)
        self.assertIsNotNone(out["cut"])
        # the LaTeX report has the airflow section
        data = sim.summary(a)
        tex = sim.report_source(data)
        self.assertIn("Airflow", tex)
        self.assertIn("Convergence", tex)
        self.assertIn(r"$C_d$", tex)
        print(f"\n[study] end-to-end Cd = {cd:.4f}; cut plane: {out['cut'] is not None}")


_VTP = """<?xml version="1.0"?>
<VTKFile type="PolyData" version="0.1" byte_order="LittleEndian">
<PolyData><Piece NumberOfPoints="3" NumberOfPolys="1">
<PointData Scalars="p"><DataArray type="Float32" Name="p" format="ascii">-1 0 1</DataArray></PointData>
<Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">-0.1 -0.1 -0.1  0.1 -0.1 -0.1  0 0.1 -0.1</DataArray></Points>
<Polys><DataArray type="Int32" Name="connectivity" format="ascii">0 1 2</DataArray>
<DataArray type="Int32" Name="offsets" format="ascii">3</DataArray></Polys>
</Piece></PolyData></VTKFile>
"""


def fake_case(root):
    """A case folder with the foamToVTK output of a tiny flow (internal mesh and a part patch), no OpenFOAM needed."""
    os.makedirs(os.path.join(root, "VTK", "10", "boundary"))
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkDelaunay3D
    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridWriter
    import numpy as np

    axis = np.linspace(-0.2, 0.2, 5)
    xyz = np.array([[x, y + 0.013 * x, z] for x in axis for y in axis for z in axis], dtype=np.float32)
    cloud = vtkPolyData()
    pts = vtkPoints()
    pts.SetData(numpy_to_vtk(xyz, deep=True))
    cloud.SetPoints(pts)
    mesh = vtkDelaunay3D()
    mesh.SetInputData(cloud)
    mesh.Update()
    grid = mesh.GetOutput()
    for name, values in (("p", xyz[:, 0] * 10.0), ("U", np.stack([1.0 + xyz[:, 2], 0.0 * xyz[:, 0], xyz[:, 1]], axis=1))):
        array = numpy_to_vtk(np.ascontiguousarray(values, dtype=np.float32), deep=True)
        array.SetName(name)
        grid.GetPointData().AddArray(array)
    writer = vtkXMLUnstructuredGridWriter()
    writer.SetFileName(os.path.join(root, "VTK", "10", "internal.vtu"))
    writer.SetInputData(grid)
    writer.Write()
    for name in ("Ball", "inlet"):
        with open(os.path.join(root, "VTK", "10", "boundary", name + ".vtp"), "w") as f:
            f.write(_VTP)
    with open(os.path.join(root, "vectorlabs.json"), "w") as f:
        json.dump({"geometry": {"bbox": [[-0.2, -0.2, -0.2], [0.2, 0.2, 0.2]]}}, f)
    return os.path.join(root, "VTK", "10", "internal.vtu")


class TestPressureScale(unittest.TestCase):
    """The colour range of the surface pressure: the run's own min and max for every part, or a fixed range."""

    def setUp(self):
        try:
            import vtkmodules.vtkIOXML  # noqa: F401
        except ImportError:
            self.skipTest("needs vtk")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = os.path.join(self.tmp, "A.vtp")
        self.b = os.path.join(self.tmp, "B.vtp")
        for path, text in ((self.a, _VTP), (self.b, _VTP.replace("-1 0 1", "2 5 9"))):
            with open(path, "w") as f:
                f.write(text)

    @staticmethod
    def read(path):
        from vtkmodules.vtkIOXML import vtkXMLPolyDataReader

        r = vtkXMLPolyDataReader()
        r.SetFileName(path)
        r.Update()
        return r.GetOutput()

    def test_range_over_all_part_patches(self):
        self.assertEqual(cfd.pressure_range([self.a, self.b]), (-1.0, 9.0))
        self.assertEqual(cfd.pressure_range([self.a]), (-1.0, 1.0))
        self.assertIsNone(cfd.pressure_range([]))

    def test_pinned_copy_has_exactly_the_range_and_the_same_surface(self):
        out = cfd.scaled_copy(self.b, pin=(0.0, 4.0))
        data = self.read(out)
        self.assertEqual(data.GetPointData().GetArray("p").GetRange(), (0.0, 4.0))  # 2 5 9 clamped; the anchors give 0 and 4
        self.assertEqual(data.GetNumberOfPolys(), 1)
        self.assertEqual(data.GetNumberOfPoints(), 5)  # two unused anchor points
        self.assertEqual([data.GetPointData().GetArray("p").GetValue(i) for i in range(3)], [2.0, 4.0, 4.0])
        self.assertAlmostEqual(data.GetPoint(1)[0], 100.0)  # still scaled to mm
        plain = self.read(cfd.scaled_copy(self.b))
        self.assertEqual((plain.GetNumberOfPoints(), plain.GetPointData().GetArray("p").GetRange()), (3, (2.0, 9.0)))

    def test_the_study_asks_for_a_fixed_range_only_when_it_is_valid(self):
        class Stub:
            PropertiesList = ["PressureFixed", "PressureMin", "PressureMax"]
            PressureFixed, PressureMin, PressureMax = True, -50.0, 200.0

        self.assertEqual(cfd.pressure_scale(Stub()), (-50.0, 200.0))
        Stub.PressureFixed = False
        self.assertIsNone(cfd.pressure_scale(Stub()))
        Stub.PressureFixed, Stub.PressureMax = True, -50.0  # max not above min: ignored
        self.assertIsNone(cfd.pressure_scale(Stub()))
        self.assertIsNone(cfd.pressure_scale(object()))  # an older study without the properties


def pump(n=4):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(App is not None and App.GuiUp, "needs the FreeCAD window")
class TestAirflowGui(unittest.TestCase):
    """New Study offers External flow under Airflow; the study panel pre-checks, runs with live plots and shows
    the forces table."""

    def setUp(self):
        import bruceworx_sim_gui as sg

        sg.install()
        close_all()
        self.addCleanup(close_all)
        self.addCleanup(sg.close_dialog)
        self.sg = sg
        self.doc = App.newDocument("AirGui")
        self.ball = self.doc.addObject("Part::Sphere", "Ball")
        self.ball.Radius = 50.0
        self.doc.recompute()
        pump()

    def test_fem_post_pipeline_does_not_crash_the_gui(self):
        # Regression: every Fem::FemPostPipeline crashed in SoFCColorGradient::setMarkerLabel (objects compiled against
        # the old Coin headers kept the old SbString size after the Coin 4.0.10 update).
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        vtu = fake_case(tmp)
        pipe = self.doc.addObject("Fem::FemPostPipeline", "P")
        pipe.read(vtu)
        self.doc.recompute()
        self.assertIsNotNone(pipe.ViewObject)
        self.assertGreater(pipe.ViewObject.getEnumerationsOfProperty("Field").count("p") + 1, 0)

    def test_results_in_the_3d_view(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        vtu = fake_case(tmp)
        a = self.doc.addObject("Fem::FemAnalysis", "Study")
        saved = cfd.convert_vtk
        cfd.convert_vtk = lambda folder, install=None: vtu
        try:
            out = cfd.load_results(a, tmp)
        finally:
            cfd.convert_vtk = saved
        self.assertEqual(len(out["surfaces"]), 1)  # the Ball patch, not the inlet
        self.assertIsNotNone(out["cut"])
        self.assertFalse(out["result"].ViewObject.Visibility)
        self.assertTrue(out["surfaces"][0].ViewObject.Visibility)
        self.assertEqual(out["surfaces"][0].ViewObject.Field, "p")
        self.assertEqual(out["cut"].ViewObject.Field, "U")
        self.assertEqual(out["cut"].ViewObject.Component, "Magnitude")

    def test_surface_pressure_uses_the_runs_own_range_or_a_fixed_one(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        vtu = fake_case(tmp)
        a = self.doc.addObject("Fem::FemAnalysis", "Study")
        a.addProperty("App::PropertyString", "StudyType", "BruceWorx", "")
        a.StudyType = "external_flow"
        cfd.ensure_properties(a)
        saved = cfd.convert_vtk
        cfd.convert_vtk = lambda folder, install=None: vtu
        try:
            own = cfd.load_results(a, tmp)
            self.assertEqual(own["p_range"], (-1.0, 1.0))  # the fake patch's own p
            self.assertFalse(own["p_fixed"])
            a.PressureFixed, a.PressureMin, a.PressureMax = True, -50.0, 200.0
            fixed = cfd.load_results(a, tmp)
        finally:
            cfd.convert_vtk = saved
        self.assertEqual(fixed["p_range"], (-50.0, 200.0))
        self.assertTrue(fixed["p_fixed"])
        out = os.path.join(tmp, "surface.vtp")
        fixed["surfaces"][0].writeVTK(out)
        self.assertEqual(TestPressureScale.read(out).GetPointData().GetArray("p").GetRange(), (-50.0, 200.0))

    def test_new_study_airflow_panel_and_run(self):
        import FreeCADGui as Gui
        from PySide import QtWidgets

        import bruceworx_cfd_gui as cg

        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        i = panel.type_combo.findData("external_flow")
        self.assertGreater(i, 0)
        panel.type_combo.setCurrentIndex(i)
        pump()
        self.assertFalse(panel.air_box.widget.isHidden())
        self.assertTrue(panel.form[2].isHidden() and panel.form[3].isHidden())  # no material or CalculiX mesh
        ab = panel.air_box
        ab.speed.setValue(0.0151)
        ab.aoa.setValue(2.0)
        ab.area.setValue(math.pi * 0.05 ** 2)
        ab.preset.setCurrentIndex(ab.preset.findData("coarse"))
        ab.turbulence.setCurrentIndex(ab.turbulence.findData("laminar"))
        panel.name_edit.setText("Wind")
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        panel.modifyStandardButtons(buttons)
        analysis = panel.create()
        self.assertIsNotNone(analysis)
        self.assertEqual((analysis.FlowSpeed, analysis.AngleOfAttack, analysis.Turbulence), (0.0151, 2.0, "laminar"))
        panel.release()
        study = self.sg.open_study_panel(analysis)
        pump()
        self.assertIsInstance(study, cg.classes()["AirflowPanel"])
        self.assertIsNotNone(Gui.Control.activeDialog())
        issues = study.check()
        if INSTALL is None:
            self.assertTrue(any("Install OpenFOAM" in t for _l, t in issues))
            self.assertFalse(study.install_button.isHidden())
            self.assertFalse(study.run_button.isEnabled())
            return
        self.assertFalse(any(l == "error" for l, _t in issues), issues)
        self.assertTrue(study.run_button.isEnabled())
        study.run()
        self.assertFalse(study.run_button.isEnabled())
        self.assertTrue(study.stop_button.isEnabled())
        t0 = time.time()
        while study._running() and time.time() - t0 < 300:
            pump(10)
            time.sleep(0.25)
        for _ in range(5):  # let the timer see the end of the run
            pump(10)
            time.sleep(1.1)
        self.assertEqual(study.runner.result(), "done", cfd._read(os.path.join(study.folder, "log.run")))
        self.assertFalse(study.stop_button.isEnabled())
        self.assertGreater(len(study.res_plot.series.get("Ux", [])), 10)  # the live residual plot filled
        self.assertGreaterEqual(study.table.rowCount(), 2)  # the ball and the total
        total = [study.table.item(study.table.rowCount() - 1, c).text() for c in range(6)]
        self.assertEqual(total[0], "Total")
        self.assertAlmostEqual(float(total[4]), 1.09, delta=0.08)  # Cd, with the 2 degrees of incidence
        out = study.load_results()
        self.assertIsNotNone(out)
        self.assertTrue(os.path.isfile(out["vtu"]))
        self.assertIsNotNone(out["result"])  # the flow is shown in the 3D view
        self.assertIn("Showing the flow", study.result_label.text())

    def test_pressure_scale_fields_are_remembered_per_study(self):
        import bruceworx_sim as sim

        a = sim.new_study(self.doc, sim.StudyPlan(name="S", study_type="external_flow", model=self.ball))["analysis"]
        study = self.sg.open_study_panel(a)
        pump()
        self.assertFalse(study.pfix_check.isChecked())
        self.assertFalse(a.PressureFixed)
        study.pmin.setValue(-80.0)
        study.pmax.setValue(120.0)
        study.pfix_check.setChecked(True)
        self.assertEqual((a.PressureFixed, a.PressureMin, a.PressureMax), (True, -80.0, 120.0))
        study.p_range = (-3.5, 7.5)  # what the last Show in 3D reported
        study.fill_pressure_range()
        self.assertEqual((a.PressureMin, a.PressureMax), (-3.5, 7.5))
        self.assertEqual(cfd.pressure_scale(a), (-3.5, 7.5))
        study.release()
        again = self.sg.open_study_panel(a)  # a second panel on the same study shows the saved values
        pump()
        self.assertTrue(again.pfix_check.isChecked())
        self.assertEqual((again.pmin.value(), again.pmax.value()), (-3.5, 7.5))

    def test_stop_button_ends_a_run(self):
        if INSTALL is None:
            self.skipTest("OpenFOAM is not installed")
        import bruceworx_sim as sim

        a = sim.new_study(self.doc, sim.StudyPlan(name="S", study_type="external_flow", model=self.ball,
                                                  options={"Turbulence": "laminar", "FlowSpeed": 0.015}))["analysis"]
        study = self.sg.open_study_panel(a)
        study.run()
        t0 = time.time()
        while study.runner.stage() not in ("snappyHexMesh", "simpleFoam") and time.time() - t0 < 60:
            pump(5)
            time.sleep(0.2)
        study.stop()
        self.assertFalse(study.runner.running())
        self.assertEqual(study.runner.result(), "stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
