# SPDX-License-Identifier: LGPL-2.1-or-later
"""ParaView and streamlines for airflow results (SIM-06, plan chapter 67e).

Headless where possible: the ParaView script is generated and parsed, seeds are placed, a fake pvbatch times out,
a missing ParaView degrades to text; with ParaView installed pvbatch renders real PNGs of a synthetic case; vtkStreamTracer
on a synthetic field gives lines that follow the flow (and a 1M-cell timing).  The GUI class needs the FreeCAD window.
Runs with FreeCADCmd -t (VTK from the app) or, for the parts that need no VTK, plain python3.
"""

import ast
import json
import os
import shutil
import stat
import tempfile
import time
import unittest

import bruceworx_cfd as cfd
import bruceworx_cfd_paraview as pvm

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import numpy as np
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkFiltersCore import vtkAppendFilter
    from vtkmodules.vtkFiltersSources import vtkSphereSource
    from vtkmodules.vtkCommonDataModel import vtkImageData
    from vtkmodules.vtkIOXML import vtkXMLPolyDataWriter, vtkXMLUnstructuredGridWriter

    HAVE_VTK = True
except ImportError:
    HAVE_VTK = False

needs_vtk = unittest.skipUnless(HAVE_VTK, "the VTK of FreeCAD is not available (run with FreeCADCmd)")
APP = cfd.paraview_app()
needs_paraview = unittest.skipUnless(APP is not None and pvm.paraview_executables()[2], "ParaView is not installed")

SLOPE = 0.3  # dz/dx of the synthetic flow U = (1, 0, SLOPE)


def info_external():
    return {"kind": "external_flow", "air": {"rho": 1.2}, "setup": {"speed": 1.0},
            "geometry": {"bbox": [[-0.05, -0.05, -0.05], [0.05, 0.05, 0.05]],
                         "domain": [[-0.55, -0.55, -0.55], [1.05, 0.55, 0.55]]}}


def info_wash():
    disks = [{"name": f"d{i}", "label": f"Motor {i}", "center": c, "axis": [0.0, 0.0, 1.0], "diameter": 0.1,
              "thrust": 1.0, "v_induced": 5.0} for i, c in enumerate(([0.1, 0.1, 0.1], [-0.1, 0.1, 0.1], [0.1, -0.1, 0.1], [-0.1, -0.1, 0.1]))]
    return {"kind": "prop_wash", "air": {"rho": 1.2}, "setup": {"speed": 0.0}, "disks": disks,
            "geometry": {"bbox": [[-0.2, -0.2, 0.0], [0.2, 0.2, 0.12]], "domain": [[-1.0, -1.0, -1.0], [1.0, 1.0, 0.6]]}}


def synthetic_case(root, info=None, n=(40, 24, 24), time_name="10", tets=False):
    """A case folder with foamToVTK-like output: a uniform inclined flow U = (1, 0, SLOPE) in the domain box (hexahedra, SI
    metres) and a Ball patch (a sphere with p), no OpenFOAM needed."""
    info = info or info_external()
    os.makedirs(os.path.join(root, "VTK", time_name, "boundary"), exist_ok=True)
    dom_lo, dom_hi = pvm.domain_of(info)
    img = vtkImageData()
    img.SetDimensions(*n)
    img.SetOrigin(*dom_lo)
    img.SetSpacing(*[(h - l) / (k - 1) for l, h, k in zip(dom_lo, dom_hi, n)])
    app = vtkAppendFilter()
    app.AddInputData(img)
    app.Update()
    grid = app.GetOutput()
    if tets:  # tetrahedra like snappyHexMesh's unstructured output
        from vtkmodules.vtkFiltersGeneral import vtkDataSetTriangleFilter

        tri = vtkDataSetTriangleFilter()
        tri.SetInputData(grid)
        tri.Update()
        grid = tri.GetOutput()
    count = grid.GetNumberOfPoints()
    u = np.zeros((count, 3), dtype=np.float32)
    u[:, 0], u[:, 2] = 1.0, SLOPE
    for name, values in (("U", u), ("p", np.zeros(count, dtype=np.float32))):
        arr = numpy_to_vtk(values, deep=True)
        arr.SetName(name)
        grid.GetPointData().AddArray(arr)
    w = vtkXMLUnstructuredGridWriter()
    w.SetFileName(os.path.join(root, "VTK", time_name, "internal.vtu"))
    w.SetInputData(grid)
    w.SetDataModeToBinary()
    w.Write()
    lo, hi = info["geometry"]["bbox"]
    sphere = vtkSphereSource()
    sphere.SetRadius((hi[0] - lo[0]) / 2)
    sphere.SetCenter(*[(a + b) / 2 for a, b in zip(lo, hi)])
    sphere.SetThetaResolution(24)
    sphere.SetPhiResolution(24)
    sphere.Update()
    poly = sphere.GetOutput()
    pts = np.array([poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())], dtype=np.float32)
    p = numpy_to_vtk(np.ascontiguousarray(pts[:, 0] * 100.0, dtype=np.float32), deep=True)
    p.SetName("p")
    poly.GetPointData().AddArray(p)
    for name in ("Ball", "inlet"):
        pw = vtkXMLPolyDataWriter()
        pw.SetFileName(os.path.join(root, "VTK", time_name, "boundary", name + ".vtp"))
        pw.SetInputData(poly)
        pw.Write()
    with open(os.path.join(root, "vectorlabs.json"), "w") as f:
        json.dump(info, f)
    return os.path.join(root, "VTK", time_name, "internal.vtu")


class TestScriptAndSeeds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw pv ")  # a space in the path on purpose
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def case(self, info):
        with open(os.path.join(self.tmp, "vectorlabs.json"), "w") as f:
            json.dump(info, f)
        return self.tmp

    def case_in(self, name, info):
        folder = os.path.join(self.tmp, name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "vectorlabs.json"), "w") as f:
            json.dump(info, f)
        return folder

    def test_external_seeds_are_upstream_of_the_body_inside_the_domain(self):
        info = info_external()
        (cloud,) = pvm.seed_plan(info, 150)
        lo, hi = info["geometry"]["bbox"]
        dom_lo, dom_hi = info["geometry"]["domain"]
        self.assertEqual(cloud["n"], 150)
        self.assertLess(cloud["center"][0] + cloud["radius"], lo[0])  # the whole cloud is in front of the body
        self.assertGreater(cloud["center"][0] - cloud["radius"], dom_lo[0])
        self.assertGreaterEqual(cloud["radius"], 0.5 * (hi[1] - lo[1]))  # about as wide as the body
        self.assertLess(cloud["center"][1] + cloud["radius"], dom_hi[1])

    def test_wash_seeds_sit_over_each_disk_on_the_inflow_side(self):
        info = info_wash()
        clouds = pvm.seed_plan(info, 200)
        self.assertEqual(len(clouds), 4)
        for cloud, d in zip(clouds, info["disks"]):
            self.assertGreater(cloud["center"][2], d["center"][2])  # thrust axis +Z: the air arrives from above
            self.assertAlmostEqual(cloud["center"][0], d["center"][0])
            self.assertLessEqual(cloud["radius"], d["diameter"])
        self.assertEqual(sum(c["n"] for c in clouds), 200)

    def test_region_of_interest_holds_the_body_and_a_wake(self):
        lo, hi = pvm.region_of_interest(info_external())
        self.assertLess(lo[0], -0.05)
        self.assertGreater(hi[0], 0.1)  # room downstream
        lo, hi = pvm.region_of_interest(info_wash())
        self.assertLess(lo[2], 0.0)  # the hover wake goes down

    def test_scripts_are_valid_python_with_everything_in_them(self):
        for info, vtk in ((info_external(), True), (info_wash(), False)):
            folder = self.case(info) if vtk else self.case_in("wash", info)
            if vtk:
                os.makedirs(os.path.join(folder, "VTK", "5", "boundary"), exist_ok=True)
                for name in ("internal.vtu", "boundary/Ball.vtp", "boundary/inlet.vtp", "boundary/zmax.vtp"):
                    open(os.path.join(folder, "VTK", "5", name), "w").close()
            for mode in ("gui", "batch"):
                path = pvm.write_script(folder, mode)
                text = open(path).read()
                ast.parse(text)  # parses
                self.assertIn("StreamTracer", text)
                self.assertIn("Slice(", text)
                self.assertIn("Pressure p [Pa]", text)  # legends carry units
                self.assertIn("Speed magnitude [m/s]", text)
                self.assertIn(os.path.basename(path), os.listdir(os.path.join(folder, "paraview")))
                cfg = ast.literal_eval(text[text.index("CFG = ") + 6:text.index("\nMODE =")])
                self.assertEqual(cfg["mode"], mode)
                self.assertEqual(len(cfg["seeds"]), 1 if vtk else 4)
                if vtk:
                    self.assertTrue(cfg["vtu"].endswith("internal.vtu"))
                    self.assertEqual([os.path.basename(p) for p in cfg["patches"]], ["Ball.vtp"])  # far-field patch left out
                else:
                    self.assertEqual(cfg["vtu"], "")  # no foamToVTK: the OpenFOAM reader on case.foam
                    self.assertTrue(os.path.isfile(cfg["foam"]))

    def test_report_figures_are_copied_and_become_latex(self):
        folder = self.case(info_external())
        os.makedirs(pvm.pv_dir(folder), exist_ok=True)
        for kind in ("iso", "cut"):
            with open(pvm.image_path(folder, kind), "wb") as f:
                f.write(b"\x89PNG fake")
        dest = os.path.join(self.tmp, "report")
        figs = pvm.report_figures(folder, dest)
        self.assertEqual([f for f, _c in figs], ["fig_pv_iso.png", "fig_pv_cut.png"])
        tex = pvm.report_figures_tex(figs)
        self.assertIn(r"\includegraphics[width=0.95\linewidth]{fig_pv_iso.png}", tex)
        self.assertEqual(pvm.report_figures_tex([]), "")
        self.assertEqual(pvm.report_figures(self.tmp + "/none", os.path.join(self.tmp, "r2")), [])


class TestDegradation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw pv ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        with open(os.path.join(self.tmp, "vectorlabs.json"), "w") as f:
            json.dump(info_external(), f)
        os.makedirs(os.path.join(self.tmp, "VTK", "3"))
        open(os.path.join(self.tmp, "VTK", "3", "internal.vtu"), "w").close()
        self.saved = (cfd.paraview_app, pvm.paraview_executables)
        self.addCleanup(self.restore)

    def restore(self):
        cfd.paraview_app, pvm.paraview_executables = self.saved

    def test_without_paraview_there_is_text_and_no_crash(self):
        cfd.paraview_app = lambda: None
        self.assertIn("not installed", pvm.precheck_text())
        job = pvm.start_render(self.tmp)
        self.assertEqual(job.state, "unavailable")
        self.assertTrue(job.poll())
        self.assertIn("not installed", job.message)
        self.assertEqual(job.images, {})
        with self.assertRaises(cfd.CfdError) as ctx:
            cfd.open_in_paraview(self.tmp)
        self.assertIn("not installed", str(ctx.exception))

    def test_a_render_that_hangs_is_stopped(self):
        fake = os.path.join(self.tmp, "pvbatch")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nsleep 60\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        pvm.paraview_executables = lambda app=None: (None, None, fake)
        t0 = time.time()
        job = pvm.start_render(self.tmp, timeout=1.0)
        self.assertTrue(job.running())
        self.assertFalse(job.wait())
        self.assertEqual(job.state, "timeout")
        self.assertLess(time.time() - t0, 10)
        self.assertIsNotNone(job.proc.poll())  # the process is gone
        self.assertIn("stopped", job.message)

    def test_a_render_that_fails_says_so(self):
        fake = os.path.join(self.tmp, "pvbatch")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho boom\nexit 3\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        pvm.paraview_executables = lambda app=None: (None, None, fake)
        job = pvm.start_render(self.tmp, timeout=30)
        self.assertFalse(job.wait())
        self.assertEqual(job.state, "failed")
        self.assertIn("boom", job.message)

    def test_a_case_without_results_does_not_start(self):
        shutil.rmtree(os.path.join(self.tmp, "VTK"))
        pvm.paraview_executables = lambda app=None: (None, None, "/bin/true")
        job = pvm.start_render(self.tmp)
        self.assertEqual(job.state, "failed")
        self.assertIn("no results", job.message)


@needs_vtk
class TestStreamlines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw sl ")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def assert_from_seeds(self, lines, info):
        """Every line is one half of a streamline: one of its ends is a seed, inside one of the seed clouds (mm)."""
        clouds = pvm.seed_plan(info)
        for pts, _s in lines:
            ok = any(min(np.linalg.norm(end - np.array(c["center"]) * 1000.0) for end in (pts[0], pts[-1]))
                     <= c["radius"] * 1000.0 + 1e-3 for c in clouds)
            self.assertTrue(ok, "a streamline does not start in a seed cloud")

    def test_lines_follow_the_flow(self):
        synthetic_case(self.tmp)
        t = {}
        path = pvm.compute_streamlines(self.tmp, 60, timings=t)
        self.assertTrue(os.path.isfile(path))
        lines = pvm.read_lines(path)
        self.assertGreaterEqual(len(lines), 60)  # one per seed at least (both ways make two pieces)
        want = (1.0 + SLOPE ** 2) ** 0.5
        info = info_external()
        for pts, speed in lines:
            self.assertGreaterEqual(len(pts), 3)
            d = np.diff(pts, axis=0)
            along = d[:, 2] / d[:, 0]
            self.assertTrue(np.allclose(along, SLOPE, atol=1e-3), "a streamline left the flow direction")
            self.assertTrue(np.allclose(speed, want, atol=1e-4))
            self.assertTrue(np.all(pts[:, 0] >= info["geometry"]["domain"][0][0] * 1000 - 1e-3))  # in mm, inside the box
        self.assert_from_seeds(lines, info)
        self.assertTrue(t["total"] < 10, t)

    def test_second_call_reuses_the_millimetre_copy(self):
        synthetic_case(self.tmp)
        t1, t2 = {}, {}
        pvm.compute_streamlines(self.tmp, 30, timings=t1)
        pvm.compute_streamlines(self.tmp, 30, timings=t2)
        self.assertLess(t2["copy"], 0.1)

    def test_wash_seeds_trace_from_over_the_disks(self):
        info = info_wash()
        synthetic_case(self.tmp, info, n=(24, 24, 24))
        lines = pvm.read_lines(pvm.compute_streamlines(self.tmp, 80))
        self.assertGreater(len(lines), 0)
        self.assert_from_seeds(lines, info)

    def test_a_volume_without_velocity_is_an_error(self):
        synthetic_case(self.tmp)
        vtu = pvm.latest_vtk(self.tmp) + "/internal.vtu"
        from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader

        r = vtkXMLUnstructuredGridReader()
        r.SetFileName(vtu)
        r.Update()
        grid = r.GetOutput()
        grid.GetPointData().RemoveArray("U")
        w = vtkXMLUnstructuredGridWriter()
        w.SetFileName(vtu)
        w.SetInputData(grid)
        w.Write()
        with self.assertRaises(cfd.CfdError):
            pvm.compute_streamlines(self.tmp, 20)

    def test_million_cells_timing(self):
        synthetic_case(self.tmp, n=(61, 61, 61), tets=True)  # 216,000 voxels cut into 1,080,000 tetrahedra
        t = {}
        pvm.compute_streamlines(self.tmp, 200, timings=t)  # includes making the mm copy
        t2 = {}
        pvm.compute_streamlines(self.tmp, 200, timings=t2)
        print(f"\n[pv] 1.08M tetrahedra, 200 seeds: first {t['total']:.2f} s (copy {t['copy']:.2f}, read {t['read']:.2f}, "
              f"trace {t['trace']:.2f}); again {t2['total']:.2f} s (read {t2['read']:.2f}, trace {t2['trace']:.2f})")
        self.assertLess(t2["total"], 15.0)


@needs_vtk
@needs_paraview
class TestParaViewRender(unittest.TestCase):
    def test_pvbatch_renders_the_three_pictures(self):
        tmp = tempfile.mkdtemp(prefix="bw pv render ")
        self.addCleanup(shutil.rmtree, tmp, True)
        synthetic_case(tmp)
        job = pvm.start_render(tmp, timeout=240)
        self.assertTrue(job.wait(), job.message)
        self.assertEqual(sorted(job.images), ["cut", "iso", "side"])
        for kind, path in job.images.items():
            with open(path, "rb") as f:
                head = f.read(8)
            self.assertEqual(head, b"\x89PNG\r\n\x1a\n", kind)
            self.assertGreater(os.path.getsize(path), 5000, kind)
        # the pictures are not blank: more than one colour
        from vtkmodules.vtkIOImage import vtkPNGReader

        r = vtkPNGReader()
        r.SetFileName(job.images["iso"])
        r.Update()
        arr = numpy_to_vtk  # noqa: F841 (numpy_support imported above)
        from vtkmodules.util.numpy_support import vtk_to_numpy

        data = vtk_to_numpy(r.GetOutput().GetPointData().GetScalars())
        self.assertGreater(len(np.unique(data, axis=0)), 50)

    def test_the_foam_reader_path_renders_too(self):
        tmp = tempfile.mkdtemp(prefix="bw pv foam ")
        self.addCleanup(shutil.rmtree, tmp, True)
        install = cfd.find_openfoam()
        if install is None:
            self.skipTest("OpenFOAM is not installed")
        # a real tiny case would be needed: the reader path is exercised by the end-to-end study test when OpenFOAM exists
        self.skipTest("covered by TestBruceWorxCfd's end-to-end run")


@unittest.skipUnless(App is not None and App.GuiUp and HAVE_VTK, "needs the FreeCAD window")
class TestStreamlinesInTheWindow(unittest.TestCase):
    def setUp(self):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        self.addCleanup(lambda: [App.closeDocument(d.Name) for d in list(App.listDocuments().values())])
        self.tmp = tempfile.mkdtemp(prefix="bw slg ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.doc = App.newDocument("PvGui")

    def test_streamlines_show_in_the_3d_view_coloured_by_speed(self):
        import FreeCADGui as Gui

        synthetic_case(self.tmp, n=(30, 20, 20))
        analysis = self.doc.addObject("Fem::FemAnalysis", "Study")
        path = pvm.compute_streamlines(self.tmp, 40)
        obj = pvm.show_streamlines(analysis, path)
        for _ in range(4):
            Gui.updateGui()
        self.assertTrue(obj.ViewObject.Visibility)
        self.assertEqual(obj.ViewObject.Field, "speed")
        out = os.environ.get("BW_PV_SHOT")
        if out:
            view = Gui.ActiveDocument.ActiveView
            view.viewIsometric()
            view.fitAll()
            Gui.updateGui()
            view.saveImage(out, 900, 600, "Current")


@unittest.skipUnless(App is not None and App.GuiUp and HAVE_VTK, "needs the FreeCAD window")
class TestResultsPanel(unittest.TestCase):
    """The Streamlines toggle, the seed count and the pictures in the airflow panel (a synthetic finished case)."""

    def setUp(self):
        import bruceworx_sim as sim
        import bruceworx_sim_gui as sg

        sg.install()
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        self.addCleanup(lambda: [App.closeDocument(d.Name) for d in list(App.listDocuments().values())])
        self.addCleanup(sg.close_dialog)
        self.tmp = tempfile.mkdtemp(prefix="bw panel ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.doc = App.newDocument("PvPanel")
        ball = self.doc.addObject("Part::Sphere", "Ball")
        ball.Radius = 50.0
        self.doc.recompute()
        plan = sim.StudyPlan(name="Wind", study_type="external_flow", model=ball, options={})
        self.analysis = sim.new_study(self.doc, plan)["analysis"]
        synthetic_case(self.tmp)
        self.analysis.CaseFolder = self.tmp
        self.panel = sg.open_study_panel(self.analysis)

    def pump(self, n=4):
        import FreeCADGui as Gui

        for _ in range(n):
            Gui.updateGui()
            time.sleep(0.02)

    def test_streamlines_toggle_and_seed_count(self):
        p = self.panel
        self.pump()
        self.assertFalse(p.streams_check.isChecked())
        p.streams_check.setChecked(True)
        self.pump()
        first = p._streams[0]
        first_name = first.Name
        self.assertEqual(p._streams[1], pvm.SEEDS)
        self.assertTrue(first.ViewObject.Visibility)
        self.assertIn("Streamlines from", p.result_label.text())
        p.streams_check.setChecked(False)
        self.assertFalse(first.ViewObject.Visibility)
        p.streams_check.setChecked(True)  # same seeds: the same object comes back
        self.assertIs(p._streams[0], first)
        p.seeds_spin.setValue(50)
        p._seeds_changed()
        self.assertEqual(p._streams[1], 50)
        self.assertIsNot(self.doc.getObject(first_name), first)  # the old lines are gone (the name is reused)
        self.assertEqual(len([o for o in self.doc.Objects if o.Name.startswith("Pipeline_Streamlines")]), 1)

    @needs_paraview
    def test_pictures_render_in_the_background_and_show_in_the_panel(self):
        p = self.panel
        p.render_pictures()
        self.assertIsNotNone(p.pv_job)
        self.assertFalse(p.pictures_button.isEnabled())  # busy while pvbatch runs
        end = time.time() + 240
        while p.pv_job.running() and time.time() < end:
            self.pump(5)  # the window stays alive meanwhile
            time.sleep(0.2)
        for _ in range(5):
            self.pump(5)
            time.sleep(0.8)
        self.assertEqual(p.pv_job.state, "done", p.pv_job.message)
        self.assertTrue(p.pictures_button.isEnabled())
        self.assertFalse(p.picture.isHidden())
        self.assertFalse(p.picture.pixmap().isNull())
        p.picture_choice.setCurrentIndex(2)
        self.assertTrue(p.picture.toolTip().endswith("pv_cut.png"))

    def test_without_pictures_the_image_is_hidden(self):
        self.assertTrue(self.panel.picture.isHidden())

    def test_the_report_carries_the_pictures(self):
        import bruceworx_sim as sim

        os.makedirs(pvm.pv_dir(self.tmp), exist_ok=True)
        for kind in ("iso", "side", "cut"):
            with open(pvm.image_path(self.tmp, kind), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfake")
        out = os.path.join(self.tmp, "rep")
        data = sim.summary(self.analysis)
        data["airflow"] = {"kind": "x"}  # content of the section is not the point here
        saved = cfd.report_section
        cfd.report_section = lambda summary: "SECTION\n"
        try:
            paths = sim.report_tex(self.analysis, None, out, data)
        finally:
            cfd.report_section = saved
        tex = open(paths["tex"]).read()
        self.assertIn("fig_pv_iso.png", tex)
        self.assertIn("fig_pv_cut.png", tex)
        self.assertTrue(os.path.isfile(os.path.join(paths["dir"], "fig_pv_side.png")))
