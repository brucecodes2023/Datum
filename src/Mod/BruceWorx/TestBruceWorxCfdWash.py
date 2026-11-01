# SPDX-License-Identifier: LGPL-2.1-or-later
"""Prop wash with actuator disks (SIM-06 phase 2), headless.

The case writer tests need no OpenFOAM.  The disk benchmark (one disk in free air, T = 1 N, D = 76 mm: v_i = 9.57 m/s at
the disk, 19.1 m/s in the far wake) runs the real solver on a 12 k cell wedge in about 40 s serial and skips when
OpenFOAM-v2606 is not installed.  The X-frame run (4 disks, 90 k cells, about 65 s) runs only with BW_CFD_SLOW=1.
Runs with plain python3 as well as with FreeCADCmd -t; the study tests need FreeCAD.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bruceworx_cfd as cfd
import bruceworx_cfd_wash as wash

INSTALL = cfd.find_openfoam()
needs_foam = unittest.skipUnless(INSTALL is not None, "OpenFOAM is not installed")
slow = unittest.skipUnless(os.environ.get("BW_CFD_SLOW"), "slow (about a minute): set BW_CFD_SLOW=1")

try:
    import FreeCAD as App
except ImportError:
    App = None


def parse_ok(path):
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


def xquad(thrust=0.5, diameter=0.076):
    """A small X-frame in metres: a plate, four arms to four motors at 85 mm, a disk of `diameter` above each."""
    parts = [cfd.Part("plate", cfd.box_triangles((-0.03, -0.03, 0.0), (0.03, 0.03, 0.003)))]
    disks = []
    for k in range(4):
        a = math.radians(45 + 90 * k)
        ca, sa = math.cos(a), math.sin(a)
        arm = cfd.box_triangles((0.02, -0.006, 0.0), (0.085, 0.006, 0.003))
        parts.append(cfd.Part(f"arm{k + 1}", [tuple((p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca, p[2]) for p in t) for t in arm]))
        disks.append(wash.Disk(f"m{k + 1}", (0.085 * ca, 0.085 * sa, 0.03), (0, 0, 1), diameter, thrust, label=f"Motor {k + 1}"))
    return parts, disks


class TestMomentumTheory(unittest.TestCase):
    def test_plan_numbers(self):
        rho = cfd.air_properties(20.0, 0.0)["rho"]
        vi = wash.induced_velocity(1.0, rho, 0.076)
        self.assertAlmostEqual(vi, 9.57, delta=0.01)  # chapter 67
        self.assertAlmostEqual(2 * vi, 19.1, delta=0.05)
        self.assertAlmostEqual(vi, math.sqrt(1.0 / (2 * rho * math.pi * 0.076 ** 2 / 4)), places=9)

    def test_forward_flight_solves_the_thrust_equation(self):
        rho = 1.204
        v = wash.induced_velocity(2.0, rho, 0.1, free_stream=(5.0, 0.0, 0.0), wake_dir=(0.0, 0.0, -1.0))
        lhs = 2 * rho * wash.disk_area(0.1) * v * math.sqrt(5.0 ** 2 + v ** 2)
        self.assertAlmostEqual(lhs, 2.0, places=6)
        self.assertLess(v, wash.induced_velocity(2.0, rho, 0.1))  # the free stream helps

    def test_climbing_slows_the_induced_velocity(self):
        rho = 1.204
        hover = wash.induced_velocity(2.0, rho, 0.1)
        climb = wash.induced_velocity(2.0, rho, 0.1, free_stream=(0.0, 0.0, -3.0), wake_dir=(0.0, 0.0, -1.0))  # the air streams down past a climbing drone
        self.assertLess(climb, hover)

    def test_no_thrust_no_flow(self):
        self.assertEqual(wash.induced_velocity(0.0, 1.2, 0.1), 0.0)

    def test_a_hub_makes_the_disk_a_ring_and_raises_the_induced_velocity(self):
        rho = cfd.air_properties(20.0, 0.0)["rho"]
        D, hub = 0.076, 0.0152
        self.assertAlmostEqual(wash.disk_area(D, hub), math.pi * (D * D - hub * hub) / 4, places=12)
        self.assertAlmostEqual(wash.disk_area(D, hub) / wash.disk_area(D), 0.96, places=12)
        vi = wash.induced_velocity(1.0, rho, D, hub=hub)
        self.assertAlmostEqual(vi, math.sqrt(1.0 / (2 * rho * wash.disk_area(D, hub))), places=9)
        self.assertAlmostEqual(vi / wash.induced_velocity(1.0, rho, D), 1 / math.sqrt(0.96), places=9)


class TestDisks(unittest.TestCase):
    def test_axis_is_normalised_and_the_wake_goes_against_it(self):
        d = wash.Disk("a", (0, 0, 0), (0, 0, 2), 0.076, 1.0)
        self.assertEqual(d.axis, (0.0, 0.0, 1.0))
        self.assertEqual(d.wake_dir, (0.0, 0.0, -1.0))
        self.assertEqual(wash.Disk.from_dict(d.to_dict()).to_dict(), d.to_dict())

    def test_angle_of_attack_turns_the_disks_with_the_model(self):
        d = wash.Disk("a", (0.1, 0, 0.05), (0, 0, 1), 0.076, 1.0)
        (r,) = wash.rotated_disks([d], 90.0, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(r.axis[0], 1.0, places=9)  # Z turns into +X about Y
        self.assertAlmostEqual(r.center[2], -0.1, places=9)
        self.assertEqual(wash.rotated_disks([d], 0.0, (0, 0, 0))[0].center, d.center)

    def test_hub_is_kept_through_dict_and_rotation_and_is_limited(self):
        d = wash.Disk("a", (0.1, 0, 0.05), (0, 0, 1), 0.076, 1.0, hub=0.015)
        self.assertEqual(wash.Disk.from_dict(d.to_dict()).hub, 0.015)
        self.assertEqual(wash.rotated_disks([d], 30.0, (0, 0, 0))[0].hub, 0.015)
        self.assertAlmostEqual(d.area, wash.disk_area(0.076, 0.015))
        self.assertEqual(wash.Disk("b", (0, 0, 0), (0, 0, 1), 0.076, 1.0).hub, 0.0)
        self.assertAlmostEqual(wash.Disk("c", (0, 0, 0), (0, 0, 1), 0.076, 1.0, hub=0.2).hub, 0.9 * 0.076)  # never a closed disk

    def test_rim_points_lie_on_the_disk(self):
        d = wash.Disk("a", (0.1, 0.2, 0.3), (1, 1, 0), 0.08, 1.0)
        for p in wash.disk_corners(d):
            rel = [p[k] - d.center[k] for k in range(3)]
            self.assertAlmostEqual(sum(rel[k] * d.axis[k] for k in range(3)), 0.0, places=12)
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in rel)), 0.04, places=12)


class TestWashCaseWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name="c", thrust=0.5, **kw):
        parts, disks = xquad(thrust)
        setup = wash.WashSetup(preset="coarse", n_procs=1, **kw)
        return wash.write_wash_case(os.path.join(self.tmp, name), parts, disks, setup), disks

    def test_files_and_dictionaries_parse(self):
        info, _disks = self.write()
        folder = info["folder"]
        for rel in ("system/controlDict", "system/fvSchemes", "system/fvSolution", "system/blockMeshDict",
                    "system/snappyHexMeshDict", "system/topoSetDict", "constant/fvOptions", "0/U", "0/p", "0/k", "0/omega",
                    "0/nut", "constant/turbulenceProperties", "Allrun"):
            path = os.path.join(folder, rel)
            self.assertTrue(os.path.isfile(path), rel)
            self.assertTrue(parse_ok(path), rel + " does not parse")
        self.assertIn("stage topoSet", open(os.path.join(folder, "Allrun")).read())

    def test_a_hub_is_an_inner_radius_of_the_cell_zone_and_the_thrust_stays(self):
        parts, disks = xquad(0.5)
        for d in disks:
            d.hub = 0.0152
        setup = wash.WashSetup(preset="coarse", n_procs=1)
        info = wash.write_wash_case(os.path.join(self.tmp, "hub"), parts, disks, setup)
        top = open(os.path.join(info["folder"], "system", "topoSetDict")).read()
        self.assertEqual(top.count("innerRadius 0.0076;"), 4)
        self.assertTrue(parse_ok(os.path.join(info["folder"], "system", "topoSetDict")))
        plain = self.write("plain")[0]
        self.assertNotIn("innerRadius", open(os.path.join(plain["folder"], "system", "topoSetDict")).read())
        rho = info["air"]["rho"]
        self.assertAlmostEqual(info["disk_area"], 4 * wash.disk_area(0.076, 0.0152))
        theory = wash.induced_velocity(0.5, rho, 0.076, hub=0.0152)
        self.assertAlmostEqual(info["disks"][0]["v_induced"], theory, places=9)
        self.assertEqual(info["disks"][0]["hub"], 0.0152)

    def test_each_disk_pushes_the_air_down_with_the_thrust(self):
        info, disks = self.write(thrust=0.5)
        rho = info["air"]["rho"]
        text = open(os.path.join(info["folder"], "constant/fvOptions")).read()
        for name in ("disk_m1", "disk_m2", "disk_m3", "disk_m4"):
            self.assertIn(name, text)
        self.assertEqual(text.count("vectorSemiImplicitSource"), 4)
        self.assertEqual(text.count("volumeMode      absolute"), 4)
        forces = re.findall(r"U \(\(([-\d.eE+]+) ([-\d.eE+]+) ([-\d.eE+]+)\) 0\)", text)
        self.assertEqual(len(forces), 4)
        for fx, fy, fz in forces:  # kinematic force on the air: -T n / rho (n = +Z)
            self.assertAlmostEqual(float(fz), -0.5 / rho, places=6)
            self.assertAlmostEqual(float(fx), 0.0)
        self.assertAlmostEqual(info["thrust_total"], 2.0)

    def test_every_disk_has_a_cell_zone_and_a_mean_velocity_probe(self):
        info, _d = self.write()
        topo = open(os.path.join(info["folder"], "system/topoSetDict")).read()
        ctl = open(os.path.join(info["folder"], "system/controlDict")).read()
        for k in range(1, 5):
            self.assertIn(f"disk_m{k}_cells", topo)
            self.assertIn(f"disk_m{k}_U", ctl)
        self.assertEqual(topo.count("cylinderToCell"), 4)
        self.assertEqual(ctl.count("volAverage"), 4)
        self.assertEqual(topo.count("cellZoneSet"), 4)
        for name in ("plate", "arm1"):
            self.assertIn(f"forces_{name}", ctl)

    def test_snappy_refines_disk_and_wake_cylinders(self):
        info, _d = self.write()
        snappy = open(os.path.join(info["folder"], "system/snappyHexMeshDict")).read()
        self.assertEqual(snappy.count("searchableCylinder"), 8)  # near disk and wake per disk
        self.assertIn("near_m1", snappy)
        self.assertIn("wake_m4", snappy)
        self.assertTrue(parse_ok(os.path.join(info["folder"], "system/snappyHexMeshDict")))

    def test_hover_has_open_faces_and_forward_flight_a_free_stream(self):
        hover, _d = self.write("hover")
        u = open(os.path.join(hover["folder"], "0/U")).read()
        self.assertEqual(u.count("pressureInletOutletVelocity"), 6)
        self.assertEqual(hover["flow_patches"], ["inlet", "outlet", "ymin", "ymax", "zmin", "zmax"])
        fwd, _d = self.write("fwd", speed=6.0)
        u = open(os.path.join(fwd["folder"], "0/U")).read()
        self.assertIn("fixedValue", u)
        self.assertNotIn("pressureInletOutletVelocity", u)
        self.assertEqual(fwd["flow_patches"], ["inlet", "outlet"])

    def test_the_domain_reaches_further_on_the_wake_side(self):
        info, _d = self.write()
        lo, hi = info["geometry"]["domain"]
        bb_lo, bb_hi = info["geometry"]["bbox"]
        below, above = bb_lo[2] - lo[2], hi[2] - bb_hi[2]
        side = bb_lo[0] - lo[0]
        self.assertGreater(below, above)
        self.assertGreater(below, 3.9 * info["geometry"]["length"])  # the "wake" factor, 4
        self.assertAlmostEqual(side, 2.0 * info["geometry"]["length"], delta=0.01)

    def test_disk_velocity_theory_is_stored_for_the_compare(self):
        info, _d = self.write(thrust=1.0)
        rho = info["air"]["rho"]
        self.assertAlmostEqual(info["disks"][0]["v_induced"], math.sqrt(1.0 / (2 * rho * math.pi * 0.076 ** 2 / 4)), places=9)

    def test_presets_refine_the_disks(self):
        parts, disks = xquad()
        geo = wash.wash_geometry(parts, disks, wash.WashSetup(preset="coarse"))
        coarse = wash.disk_level(0.076, geo["dx0"], "coarse")
        fine = wash.disk_level(0.076, geo["dx0"], "fine")
        self.assertGreater(fine, coarse)
        small = wash.estimate_wash(parts, disks, wash.WashSetup(preset="coarse"))["cells"]
        big = wash.estimate_wash(parts, disks, wash.WashSetup(preset="medium"))["cells"]
        self.assertGreater(big, small)

    def test_precheck_messages(self):
        parts, disks = xquad()
        issues, est = wash.precheck_wash(parts, disks, wash.WashSetup(), install=None)
        self.assertTrue(any(l == "error" and "Install OpenFOAM" in t for l, t in issues))
        self.assertTrue(any("induced velocity" in t for _l, t in issues))
        self.assertIsNotNone(est)
        issues, _e = wash.precheck_wash(parts, [], wash.WashSetup(), install=None)
        self.assertTrue(any("No propellers" in t for _l, t in issues))
        low = wash.WashSetup(cell_limit=1000)
        issues, _e = wash.precheck_wash(parts, disks, low, install=None)
        self.assertTrue(any("cells expected" in t and l == "error" for l, t in issues))
        inside = [wash.Disk("x", (0.0, 0.0, 0.0015), (0, 0, 1), 0.076, 0.5)]  # centre inside the plate
        issues, _e = wash.precheck_wash(parts, inside, wash.WashSetup(), install=None)
        self.assertTrue(any("inside plate" in t for _l, t in issues))

    @needs_foam
    def test_openfoam_reads_every_dictionary(self):
        info, _d = self.write()
        files = ["system/controlDict", "system/fvSchemes", "system/fvSolution", "system/blockMeshDict",
                 "system/snappyHexMeshDict", "system/topoSetDict", "constant/fvOptions", "constant/turbulenceProperties",
                 "0/U", "0/p", "0/k", "0/omega", "0/nut"]
        script = "; ".join(f"foamDictionary -expand {f} > /dev/null || echo BAD {f}" for f in files)
        folder = cfd.run_dir(info["folder"])
        out = subprocess.run([INSTALL["launcher"], "-c", f"cd '{folder}'; {script}"], capture_output=True, text=True, timeout=180)
        self.assertNotIn("BAD", out.stdout + out.stderr, out.stdout + out.stderr)


class TestDiskBenchmark(unittest.TestCase):
    """The chapter 67 acceptance run: a single disk in free air."""

    @needs_foam
    def test_single_disk_in_hover(self):
        with tempfile.TemporaryDirectory() as t:
            folder = os.path.join(t, "disk")
            info = wash.write_disk_case(folder, thrust=1.0, diameter=0.076)
            t0 = time.time()
            run = cfd.Runner(folder, INSTALL).start()
            run.wait()
            seconds = time.time() - t0
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))
            v = wash.disk_benchmark_values(folder)
            err_disk = (v["v_disk"] - 9.57) / 9.57
            err_wake = (v["v_wake"] - 19.1) / 19.1
            print(f"\n[disk] v at the disk {v['v_disk']:.2f} m/s (momentum theory 9.57, {err_disk * 100:+.1f} %), "
                  f"wake core at 1 D {v['v_wake']:.2f} m/s (19.1, {err_wake * 100:+.1f} %), axis peak {v['axis_peak']:.2f} m/s "
                  f"at x/D = {v['axis_peak_x_over_D']:.2f}; {sum(info['cells'][0][i] * info['cells'][1][j] for i in range(4) for j in range(2))} "
                  f"cells, {seconds:.0f} s")
            self.assertAlmostEqual(info["v_induced"], 9.57, delta=0.01)
            self.assertLess(abs(err_disk), 0.10)
            self.assertLess(abs(err_wake), 0.15)

    @needs_foam
    def test_disk_with_a_hub_against_the_ring_area_theory(self):
        """B-CF4 with a hub of 0.2 D: the same thrust on the ring (96 % of the area), so v_i = sqrt(T / 2 rho A_ring) =
        9.77 m/s at the disk and 2 v_i = 19.5 m/s in the far wake.  The hole fills in a few radii downstream."""
        with tempfile.TemporaryDirectory() as t:
            folder = os.path.join(t, "ring")
            info = wash.write_disk_case(folder, thrust=1.0, diameter=0.076, hub=0.0152)
            run = cfd.Runner(folder, INSTALL).start()
            run.wait()
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))
            v = wash.disk_benchmark_values(folder)
            err_disk = (v["v_disk"] - info["v_induced"]) / info["v_induced"]
            err_wake = (v["v_wake"] - 2 * info["v_induced"]) / (2 * info["v_induced"])
            print(f"\n[ring] v at the ring {v['v_disk']:.2f} m/s (theory {info['v_induced']:.2f}, {err_disk * 100:+.1f} %), wake core "
                  f"{v['v_wake']:.2f} m/s ({2 * info['v_induced']:.2f}, {err_wake * 100:+.1f} %)")
            self.assertAlmostEqual(info["v_induced"], 9.765, delta=0.01)
            self.assertLess(abs(err_disk), 0.10)
            self.assertLess(abs(err_wake), 0.15)

    @needs_foam
    def test_thrust_scales_the_velocity_as_the_square_root(self):
        """Quarter the thrust, half the speed: a quick check on the source's normalisation (coarse wedge, 12 s)."""
        out = {}
        with tempfile.TemporaryDirectory() as t:
            for thrust in (1.0, 0.25):
                folder = os.path.join(t, f"d{thrust}")
                wash.write_disk_case(folder, thrust=thrust, cells_across=24, iterations=800)
                run = cfd.Runner(folder, INSTALL).start()
                run.wait()
                self.assertEqual(run.result(), "done", cfd._read(os.path.join(folder, "log.run")))
                out[thrust] = wash.disk_benchmark_values(folder)["v_disk"]
        self.assertAlmostEqual(out[0.25] / out[1.0], 0.5, delta=0.04)


class TestWashVerdict(unittest.TestCase):
    """The convergence verdict of a prop-wash study: converged, steady average of an unsteady flow, not converged."""

    def case(self, res, plate, arm, tiny, n=400):
        """A synthetic hover case, thrust 4 N along +Z: forces[i] (Fz) per part as functions of the iteration."""
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for name, fn in (("plate", plate), ("arm1", arm), ("tiny", tiny)):
            d = os.path.join(folder, "postProcessing", "forces_" + name, "0")
            os.makedirs(d)
            open(os.path.join(d, "force.dat"), "w").write("# hdr\n" + "\n".join(f"{i} 0 0 {fn(i)}" for i in range(1, n + 1)) + "\n")
        for patch in ("zmax",):
            d = os.path.join(folder, "postProcessing", "flow_" + patch, "0")
            os.makedirs(d)
            open(os.path.join(d, "surfaceFieldValue.dat"), "w").write("# hdr\n" + "".join(f"{i} 1e-6\n" for i in range(1, n + 1)))
        open(os.path.join(folder, "log.simpleFoam"), "w").write("".join(
            f"Time = {i}\nsmoothSolver:  Solving for Ux, Initial residual = {res:g}, Final residual = 1e-9, No Iterations 1\n"
            for i in range(1, n + 1)))
        info = {"thrust_total": 4.0, "disks": [{"axis": [0, 0, 1]}], "flow_ref": 1.0, "flow_patches": ["zmax"],
                "setup": {"speed": 0.0},
                "parts": {k: {"name": k} for k in ("plate", "arm1", "tiny")}}
        return folder, info

    def test_converged(self):
        folder, info = self.case(5e-4, lambda i: -0.40, lambda i: -0.12, lambda i: -0.003)
        conv = wash.wash_convergence(folder, info)
        self.assertEqual(conv["verdict"], "converged", conv["text"])
        self.assertEqual(conv["window"], 200)
        self.assertEqual([st["name"] for st in conv["listed"]], ["tiny"])  # 0.075 % of the thrust: listed, not judged

    def test_a_small_part_that_swings_does_not_fail_the_verdict(self):
        folder, info = self.case(5e-4, lambda i: -0.40, lambda i: -0.12, lambda i: -0.003 * (1 + 0.3 * (-1) ** i))
        self.assertEqual(wash.wash_convergence(folder, info)["verdict"], "converged")

    def test_residuals_above_the_limit_give_a_steady_average_of_an_unsteady_flow(self):
        folder, info = self.case(4e-3, lambda i: -0.40, lambda i: -0.12, lambda i: -0.003)
        conv = wash.wash_convergence(folder, info)
        self.assertEqual(conv["verdict"], "unsteady")
        self.assertEqual(conv["label"], "steady average of an unsteady flow")
        self.assertAlmostEqual(conv["judged"][0]["mean"], -0.523, places=6)

    def test_a_swinging_part_with_steady_average_is_unsteady(self):
        folder, info = self.case(5e-4, lambda i: -0.40, lambda i: -0.12 * (1 + 0.03 * (-1) ** i), lambda i: -0.003)
        conv = wash.wash_convergence(folder, info)
        self.assertEqual(conv["verdict"], "unsteady")
        arm = [st for st in conv["judged"] if st["name"] == "arm1"][0]
        self.assertAlmostEqual(arm["swing"], 0.06, delta=0.005)

    def test_a_drifting_average_is_not_converged(self):
        folder, info = self.case(5e-4, lambda i: -0.40 - 0.1 * i / 400, lambda i: -0.12, lambda i: -0.003)
        self.assertEqual(wash.wash_convergence(folder, info)["verdict"], "not converged")

    def test_report_carries_the_verdict_and_the_window_table(self):
        folder, info = self.case(4e-3, lambda i: -0.40, lambda i: -0.12, lambda i: -0.003)
        conv = wash.wash_convergence(folder, info)
        tex = "\n".join(wash._window_table(conv))
        self.assertIn("plate", tex)
        self.assertIn("no (under 2", tex)


class TestXFrameRun(unittest.TestCase):
    @needs_foam
    @slow
    def test_four_disks_over_an_x_frame(self):
        with tempfile.TemporaryDirectory() as t:
            parts, disks = xquad(0.5)
            setup = wash.WashSetup(preset="coarse", n_procs=1)
            info = wash.write_wash_case(os.path.join(t, "quad"), parts, disks, setup)
            t0 = time.time()
            run = cfd.Runner(info["folder"], INSTALL).start()
            run.wait()
            seconds = time.time() - t0
            self.assertEqual(run.result(), "done", cfd._read(os.path.join(info["folder"], "log.run")))
            forces = wash.wash_forces(info["folder"], info)
            conv = wash.wash_convergence(info["folder"], info)
            vel = wash.disk_velocities(info["folder"], info)
            arms = [r for r in forces["parts"] if r["part"].startswith("arm")]
            frac = forces["total"]["fraction"]
            print(f"\n[x-frame] download {forces['total']['download']:.4f} N = {frac * 100:.2f} % of {forces['thrust']:.2f} N thrust; "
                  f"disks {[round(v['measured'], 2) for v in vel]} m/s (theory {vel[0]['theory']:.2f}); {seconds:.0f} s; {conv['text']}")
            self.assertTrue(conv["converged"], conv)
            self.assertTrue(all(r["download"] > 0 for r in arms))  # the wake pushes the arms down
            spread = max(r["download"] for r in arms) / min(r["download"] for r in arms)
            self.assertLess(spread, 1.02)  # a symmetric frame loads its four arms alike
            for v in vel:
                self.assertAlmostEqual(v["measured"] / v["theory"], 1.0, delta=0.15)
            self.assertGreater(frac, 0.0)
            self.assertLess(frac, 0.15)


@unittest.skipUnless(App is not None, "needs FreeCAD")
class TestPropWashStudy(unittest.TestCase):
    """The study glue on the ROB-03 X-frame: New Study type, thrust from the balance, pre-check, case, report."""

    def setUp(self):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        import TestBruceWorxBalance as bal
        import bruceworx_massreport as mr

        self.mr = mr
        self.doc = App.newDocument("Wash")
        self.asm = bal.xframe(self.doc, battery_dx=0.0)
        fd, self.csv = tempfile.mkstemp(suffix=".csv")
        os.write(fd, bal.THRUST_CSV.encode())
        os.close(fd)
        mr.mark_motor(self.doc.getObject("Motor"), table_path=self.csv)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        os.remove(self.csv)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def study(self, **opts):
        import bruceworx_sim as sim

        plan = sim.StudyPlan(name="Wash", study_type="prop_wash", model=self.asm, options=opts)
        return sim.new_study(self.doc, plan)["analysis"]

    def test_new_study_is_a_hover_airflow_study(self):
        import bruceworx_sim as sim

        a = self.study(PropDiameter=76.0)
        self.assertEqual(sim.study_kind(a), "prop_wash")
        self.assertIn("prop_wash", [k for k, _l, ok in sim.STUDY_TYPES if ok])
        self.assertTrue(cfd.is_airflow(a))
        self.assertTrue(cfd.is_wash(a))
        self.assertEqual((a.FlowSpeed, a.PropDiameter, a.TypedThrust, a.DiskOffset), (0.0, 76.0, 0.0, 0.0))
        self.assertEqual(a.DomainWake, wash.WASH_FACTORS["wake"])

    def test_external_flow_studies_are_unchanged(self):
        import bruceworx_sim as sim

        plan = sim.StudyPlan(name="Wind", study_type="external_flow", model=self.asm, options={"FlowSpeed": 12.0})
        a = sim.new_study(self.doc, plan)["analysis"]
        self.assertFalse(cfd.is_wash(a))
        self.assertEqual(a.FlowSpeed, 12.0)
        self.assertNotIn("PropDiameter", a.PropertiesList)

    def test_unknown_setting_is_refused(self):
        import bruceworx_sim as sim

        with self.assertRaises(sim.StudyError):
            self.study(Bogus=1)

    def test_thrust_is_the_hover_share_of_the_balance(self):
        a = self.study(PropDiameter=76.0)
        disks, notes = wash.study_disks(a)
        report = self.mr.build_report(self.doc, [self.asm])
        result, _n = self.mr.balance_of(report)
        self.assertEqual(len(disks), 4)
        self.assertAlmostEqual(sum(d.thrust for d in disks), report.mass * 9.80665, places=6)  # N = kg g
        for d, m in zip(disks, result.motors):
            self.assertAlmostEqual(d.thrust, m.share * 1e-3 * 9.80665, places=9)
            self.assertAlmostEqual(d.diameter, 0.076)
            self.assertEqual(d.axis, (0.0, 0.0, 1.0))
            self.assertAlmostEqual(math.hypot(d.center[0], d.center[1]), 0.115, places=6)
        # the disk sits above the motor's top (the motor part spans Z 4 to 22 mm): 22 mm + 0.1 D
        self.assertAlmostEqual(disks[0].center[2], 0.022 + 0.0076, delta=1e-4)

    def test_hub_defaults_to_a_fifth_of_the_diameter_and_the_study_value_replaces_it(self):
        disks, _n = wash.study_disks(self.study(PropDiameter=76.0))
        self.assertTrue(all(abs(d.hub - 0.0152) < 1e-9 for d in disks))
        disks, _n = wash.study_disks(self.study(PropDiameter=76.0, HubDiameter=12.0))
        self.assertTrue(all(abs(d.hub - 0.012) < 1e-9 for d in disks))

    def test_the_motor_part_carries_its_own_propeller_and_hub(self):
        motor = self.doc.getObject("Motor")
        for name in ("ThrustAxis", "PropDiameter", "HubDiameter"):
            self.assertIn(name, motor.PropertiesList)
        self.assertEqual((motor.PropDiameter, motor.HubDiameter), (0.0, 0.0))
        motor.PropDiameter, motor.HubDiameter = 100.0, 14.0
        disks, _n = wash.study_disks(self.study(PropDiameter=76.0, HubDiameter=12.0))
        self.assertTrue(all(abs(d.diameter - 0.1) < 1e-9 and abs(d.hub - 0.014) < 1e-9 for d in disks))
        self.assertAlmostEqual(disks[0].center[2], 0.022 + 0.0100, delta=1e-4)  # the offset follows the motor's own D
        motor.HubDiameter = 0.0
        disks, _n = wash.study_disks(self.study(PropDiameter=76.0, HubDiameter=12.0))
        self.assertAlmostEqual(disks[0].hub, 0.012)  # no hub on the part: the study value
        # a diameter on every motor part makes the study's own blank value fine
        disks, _n = wash.study_disks(self.study())
        self.assertAlmostEqual(disks[0].diameter, 0.1)
        self.assertAlmostEqual(disks[0].hub, 0.02)  # 0.2 D

    def test_typed_thrust_and_explicit_offset_override(self):
        a = self.study(PropDiameter=76.0, TypedThrust=1.5, DiskOffset=20.0)
        disks, _notes = wash.study_disks(a)
        self.assertTrue(all(d.thrust == 1.5 for d in disks))
        self.assertAlmostEqual(disks[0].center[2], 0.013 + 0.020, delta=1e-4)  # motor centre at 13 mm

    def test_a_missing_diameter_or_motor_is_an_error_in_the_precheck(self):
        import bruceworx_sim as sim

        a = self.study()
        self.assertTrue(any(level == "error" and "propeller diameter" in text for level, text in sim.precheck(a)))
        b = self.study(PropDiameter=76.0)
        self.doc.getObject("Motor").removeProperty("ThrustAxis")
        self.assertTrue(any(level == "error" and "no motors" in text for level, text in sim.precheck(b)))

    def test_study_precheck_reports_the_disks(self):
        a = self.study(PropDiameter=76.0)
        issues, est = cfd.study_precheck(a)
        self.assertTrue(any("induced velocity" in t for _l, t in issues))
        self.assertIsNotNone(est)
        self.assertEqual(sum(1 for _l, t in issues if "induced velocity" in t), 4)

    @needs_foam
    def test_prepare_run_writes_the_wash_case(self):
        a = self.study(PropDiameter=76.0)
        folder, info = cfd.prepare_run(a, root=self.tmp)
        self.assertEqual(a.CaseFolder, folder)
        self.assertEqual(info["kind"], "prop_wash")
        self.assertEqual(len(info["disks"]), 4)
        for rel in ("constant/fvOptions", "system/topoSetDict", "system/snappyHexMeshDict"):
            self.assertTrue(os.path.isfile(os.path.join(folder, rel)), rel)
        self.assertGreater(len(info["parts"]), 3)

    def test_the_report_has_a_prop_wash_section(self):
        import bruceworx_sim as sim

        a = self.study(PropDiameter=76.0)
        summary = {
            "kind": "prop_wash", "thrust_total": 3.8, "iterations": 400, "stage": "done",
            "setup": {"speed": 0.0, "temperature_c": 20.0, "altitude_m": 0.0, "preset": "coarse", "turbulence": "kOmegaSST"},
            "air": cfd.air_properties(20.0, 0.0), "estimate": {"cells": 90000}, "refs": {"a_ref": 1.0, "l_ref": 1.0},
            "convergence": {"residuals_ok": True, "forces_ok": True, "flow_ok": True, "converged": True, "flow_balance": 1e-6,
                            "iteration": 400, "text": "x"},
            "disks": [{"name": "m1", "label": "Motor #1", "thrust": 0.95, "diameter": 0.076, "theory": 7.4, "measured": 8.0}],
            "forces": {"parts": [{"part": "Arm", "fx": 0.0, "fy": 0.0, "fz": -0.02, "along": -0.02, "download": 0.02,
                                  "fraction": 0.0053}],
                       "total": {"fx": 0.0, "fy": 0.0, "fz": -0.02, "along": -0.02, "download": 0.02, "fraction": 0.0053},
                       "axis": [0, 0, 1], "thrust": 3.8}}
        a.CfdSummary = json.dumps(summary)
        tex = sim.report_source(sim.summary(a))
        self.assertIn("Prop wash", tex)
        self.assertIn("Download", tex)
        self.assertIn("0.53", tex)
        self.assertIn("Motor \\#1", tex)


@unittest.skipUnless(App is not None and App.GuiUp, "needs the FreeCAD window")
class TestPropWashGui(unittest.TestCase):
    """New Study offers Prop wash with its own fields, and the study panel shows the wash table."""

    def setUp(self):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        import TestBruceWorxBalance as bal
        import bruceworx_massreport as mr
        import bruceworx_sim_gui as sg

        self.sg = sg
        self.doc = App.newDocument("WashGui")
        self.asm = bal.xframe(self.doc, battery_dx=0.0)
        fd, self.csv = tempfile.mkstemp(suffix=".csv")
        os.write(fd, bal.THRUST_CSV.encode())
        os.close(fd)
        mr.mark_motor(self.doc.getObject("Motor"), table_path=self.csv)

    def tearDown(self):
        for doc in list(App.listDocuments().values()):
            App.closeDocument(doc.Name)
        os.remove(self.csv)

    def test_new_study_prop_wash_fields_and_study_panel(self):
        import FreeCADGui as Gui

        import bruceworx_cfd_gui as cg

        panel = self.sg.classes()["NewStudyPanel"](self.doc)
        panel.model_combo.setCurrentIndex(panel.model_combo.findData(self.asm.Name))
        i = panel.type_combo.findData("prop_wash")
        self.assertGreater(i, 0)
        panel.type_combo.setCurrentIndex(i)
        Gui.updateGui()
        box = panel.air_box
        self.assertFalse(box.widget.isHidden())
        self.assertFalse(box.diameter.isHidden() or box.typed.isHidden() or box.offset.isHidden() or box.hub.isHidden())
        self.assertTrue(box.aoa.isHidden() and box.area.isHidden())
        self.assertEqual(box.speed.value(), 0.0)  # hover
        box.diameter.setValue(76.0)
        box.hub.setValue(15.0)
        panel.name_edit.setText("Wash")
        analysis = panel.create()
        self.assertIsNotNone(analysis)
        self.assertEqual((analysis.StudyType, analysis.PropDiameter, analysis.FlowSpeed), ("prop_wash", 76.0, 0.0))
        self.assertEqual(analysis.HubDiameter, 15.0)
        panel.type_combo.setCurrentIndex(panel.type_combo.findData("external_flow"))  # back: the angle returns, speed 10
        Gui.updateGui()
        self.assertFalse(box.aoa.isHidden())
        self.assertTrue(box.diameter.isHidden())
        self.assertEqual(box.speed.value(), 10.0)
        panel.release()
        study = self.sg.open_study_panel(analysis)
        Gui.updateGui()
        self.assertIsInstance(study, cg.classes()["AirflowPanel"])
        self.assertEqual(study.diameter.value(), 76.0)
        issues = study.check()
        self.assertTrue(all(level != "error" or "OpenFOAM" in text for level, text in issues), issues)
        study.diameter.setValue(80.0)
        self.assertEqual(analysis.PropDiameter, 80.0)
        self.assertEqual(study.hub.value(), 15.0)
        study.hub.setValue(0.0)
        self.assertEqual(analysis.HubDiameter, 0.0)
        summary = {
            "kind": "prop_wash", "thrust_total": 3.8,
            "convergence": {"text": "ok", "converged": False, "label": "steady average of an unsteady flow",
                            "judged": [{"name": "Total", "mean": -0.7552, "swing": 0.0075}],
                            "listed": [{"name": "Plate", "mean": -0.003, "swing": 0.11}]},
            "disks": [{"label": "Motor #1", "measured": 8.0, "theory": 7.4}],
            "forces": {"parts": [{"part": "Arm", "fx": 0.0, "fy": 0.0, "fz": -0.02, "download": 0.02, "fraction": 0.0053}],
                       "total": {"fx": 0.0, "fy": 0.0, "fz": -0.02, "download": 0.02, "fraction": 0.0053}}}
        analysis.CfdSummary = json.dumps(summary)
        study._show_summary()
        self.assertEqual(study.table.rowCount(), 2)
        self.assertEqual(study.table.item(0, 0).text(), "Arm")
        self.assertEqual(study.table.item(1, 5).text(), "0.53 %")
        self.assertIn("Induced velocity", study.conv_label.text())
        self.assertIn("steady average of an unsteady flow", study.conv_label.text())
        self.assertIn("Total -0.755 N (0.75 %)", study.conv_label.text())
        self.assertIn("not judged: Plate", study.conv_label.text())
        Gui.Control.closeDialog()


if __name__ == "__main__":
    unittest.main()
