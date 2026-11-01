# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-04: spin loads, cylindrical stress, colour maps, export and pictures of a rotor (headless).

One thin steel annulus (the G-RD1 validation model, a quarter with symmetry planes) is solved once; the tests read the
same result: the CalculiX input, the hoop stress against Timoshenko's closed form, the stored components, the .vtu, the
line plot and, when ParaView is installed, a picture. The maths and the pre-check tests need no solver.
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_sim as sim
import bruceworx_sim_post as post
import bruceworx_validation as val
import bruceworx_validation_cases as vc

PARAMS = {"a_mm": 10.0, "b_mm": 50.0, "thickness_mm": 2.0, "rpm": 30000.0, "E_MPa": 210000.0, "nu": 0.3, "density": 7850.0}
SIZE = 4.0


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


class TestPictureScript(unittest.TestCase):
    def test_quadratic_cells_are_subdivided_and_the_triad_is_off_by_default(self):
        axis = ((0, 0, 0), (0, 0, 1))
        text = post.pv_script("a.vtu", "a.png", "Hoop", "Hoop [MPa]", 0.0, 1.0, "Turbo", "quarter", (400, 300), axis)
        self.assertIn('"axes": false', text)
        self.assertIn('"subdivision": 2', text)
        self.assertIn("NonlinearSubdivisionLevel", text)
        self.assertIn('"axes": true', post.pv_script("a.vtu", "a.png", "Hoop", "Hoop [MPa]", 0.0, 1.0, "Turbo", "iso", (400, 300),
                                                      axis, axes=True))


class TestMaths(unittest.TestCase):
    def test_axisymmetric_field_about_z(self):
        import numpy as np

        # sigma_rr = 3, sigma_tt = 5, sigma_zz = 7, sigma_rt = 1 at four angles -> tensor in x, y, z, back to components
        xyz, tensors = [], []
        for th in (0.0, 0.7, 2.0, 4.0):
            c, s = math.cos(th), math.sin(th)
            er, et, ez = np.array([c, s, 0.0]), np.array([-s, c, 0.0]), np.array([0.0, 0.0, 1.0])
            tensors.append(3 * np.outer(er, er) + 5 * np.outer(et, et) + 7 * np.outer(ez, ez) + 1 * (np.outer(er, et) + np.outer(et, er)))
            xyz.append([20 * c + 1.0, 20 * s - 2.0, 4.0])
        out = post.cylindrical_components(np.array(xyz), np.array(tensors), ((1.0, -2.0, 0.0), (0.0, 0.0, 1.0)))
        for key, value in (("radial", 3), ("hoop", 5), ("axial", 7), ("shear", 1), ("r", 20)):
            self.assertTrue(np.allclose(out[key], value), key)

    def test_a_tilted_axis_gives_the_same_components(self):
        import numpy as np

        a = np.array([1.0, 2.0, 2.0]) / 3.0
        u = np.cross(a, [0, 0, 1.0])
        u /= np.linalg.norm(u)
        v = np.cross(a, u)
        th = 1.1
        er, et = math.cos(th) * u + math.sin(th) * v, -math.sin(th) * u + math.cos(th) * v
        t = 2 * np.outer(er, er) - 4 * np.outer(et, et) + 9 * np.outer(a, a)
        out = post.cylindrical_components(np.array([5 * er + 3 * a]), np.array([t]), ((0, 0, 0), tuple(a)))
        self.assertAlmostEqual(out["radial"][0], 2.0)
        self.assertAlmostEqual(out["hoop"][0], -4.0)
        self.assertAlmostEqual(out["axial"][0], 9.0)
        self.assertAlmostEqual(out["shear"][0], 0.0)

    def test_on_the_axis_radial_and_hoop_agree(self):
        import numpy as np

        t = np.diag([4.0, 6.0, 1.0])[None]
        out = post.cylindrical_components(np.array([[0.0, 0.0, 3.0]]), t, ((0, 0, 0), (0, 0, 1)))
        self.assertAlmostEqual(out["radial"][0], 5.0)
        self.assertAlmostEqual(out["hoop"][0], 5.0)
        self.assertAlmostEqual(out["shear"][0], 0.0)

    def test_colour_maps(self):
        for name in post.COLORMAPS:
            self.assertNotEqual(post.colormap_rgb(name, 0.0), post.colormap_rgb(name, 1.0))
            for t in (0.0, 0.3, 0.5, 1.0, 1.4, -1.0):
                self.assertTrue(all(0.0 <= c <= 1.0 for c in post.colormap_rgb(name, t)), (name, t))
        self.assertEqual(post.colormap_rgb("Rainbow", 0.0), (0.0, 0.0, 1.0))  # blue at the bottom, red at the top
        self.assertEqual(post.colormap_rgb("Rainbow", 1.0), (1.0, 0.0, 0.0))
        self.assertGreater(post.colormap_rgb("Jet", 0.0)[2], 0.4)
        self.assertLess(post.colormap_rgb("Turbo", 0.0)[0], 0.3)
        with self.assertRaises(post.PostError):
            post.colormap_rgb("Plasma", 0.5)
        groups = post.node_colors([0.0, 5.0, 10.0, 10.0], 0.0, 10.0, "Jet", bins=4)
        self.assertEqual(sum(len(k) for k in groups), 4)
        self.assertLessEqual(len(groups), 4)

    def test_field_names(self):
        self.assertEqual(post.field_key("sigma_theta"), "hoop")
        self.assertEqual(post.field_key("vm"), "vonMises")
        with self.assertRaises(post.PostError):
            post.field_key("pressure")


class TestSpinSetup(unittest.TestCase):
    def setUp(self):
        close_all()

    def tearDown(self):
        close_all()

    def study(self, offset=0.0):
        import Part

        doc = App.newDocument("Spin")
        solid = Part.makeCylinder(30.0, 6.0, App.Vector(offset, 0, 0)).cut(Part.makeCylinder(8.0, 6.0, App.Vector(offset, 0, 0)))
        body = vc.shape_object(doc, "Wheel", solid)
        objs = sim.new_study(doc, sim.StudyPlan(name="Wheel", model=body, material=vc.material(), max_size=4.0))
        return doc, body, solid, objs["analysis"]

    def hold_bore(self, doc, body, solid, an):
        """Holds the wheel the way a spinning one should be held (axial face + 3-2-1 points; the name is historic)."""
        return post.hold_for_spin(an)

    def fix_bore(self, doc, body, solid, an, kind="fixed"):
        import ObjectsFem

        face = vc.faces_where(solid, vc.cylinder_of(8.0))[0]
        if kind == "fixed":
            hold = ObjectsFem.makeConstraintFixed(doc, "Bore")
            hold.References = [(body, face)]
            an.addObject(hold)
            return hold
        return vc.add_displacement(doc, an, body, [face], kind, "BoreDisp")

    def test_axis_from_a_cylinder_face_and_a_circle_edge(self):
        _doc, body, solid, _an = self.study(offset=10.0)
        face = vc.faces_where(solid, vc.cylinder_of(30.0))[0]
        point, direction = post.axis_from_ref(body, face)
        self.assertAlmostEqual(abs(direction[2]), 1.0)
        self.assertAlmostEqual(point[0], 10.0)
        self.assertAlmostEqual(point[1], 0.0)
        edge = next(f"Edge{i}" for i, e in enumerate(solid.Edges, 1) if e.Curve.TypeId == "Part::GeomCircle" and abs(e.Curve.Radius - 8.0) < 1e-6)
        point, direction = post.axis_from_ref(body, edge)
        self.assertAlmostEqual(point[0], 10.0)
        self.assertAlmostEqual(abs(direction[2]), 1.0)
        with self.assertRaises(post.PostError):
            post.axis_from_ref(body, vc.faces_where(solid, vc.plane_at(2, 0.0))[0])  # a plane has no axis

    def test_add_spin_stores_rpm_as_frequency_and_counts_as_a_load(self):
        _doc, _body, _solid, an = self.study()
        spin = post.add_spin(an, 30000, axis="z")
        self.assertAlmostEqual(float(spin.RotationFrequency.getValueAs("1/s")), 500.0)
        self.assertAlmostEqual(post.spin_rpm(spin), 30000.0)
        self.assertIn("30000 rpm", spin.Label)
        self.assertIn(spin, sim.members(an)["loads"])
        self.assertIn(spin, an.Group)
        point, direction = post.spin_axis(spin)
        self.assertAlmostEqual(abs(direction[2]), 1.0)
        self.assertAlmostEqual(math.hypot(point[0], point[1]), 0.0)
        post.set_spin_speed(spin, 12000)
        self.assertAlmostEqual(post.spin_rpm(spin), 12000.0)
        self.assertEqual(post.spin_loads(an), [spin])
        with self.assertRaises(post.PostError):
            post.add_spin(an, 0)

    def test_the_model_axis_is_found_when_none_is_given(self):
        _doc, _body, _solid, an = self.study(offset=10.0)
        spin = post.add_spin(an, 1000)  # the largest circle: the rim, centred on x = 10
        point, direction = post.spin_axis(spin)
        self.assertAlmostEqual(point[0], 10.0)
        self.assertAlmostEqual(abs(direction[2]), 1.0)

    def test_a_fixed_bore_under_a_spin_is_an_error_with_the_reason(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        self.fix_bore(doc, body, solid, an)
        errors = [t for k, t in sim.precheck(an) if k == "error"]
        self.assertTrue(any("stops the bore growing" in t and "3-2-1" in t for t in errors), errors)

    def test_a_radially_fixing_displacement_on_the_bore_is_an_error_too(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        self.fix_bore(doc, body, solid, an, "xy")
        self.assertTrue(any("stops the bore growing" in t for k, t in sim.precheck(an) if k == "error"))

    def test_an_axial_only_bore_face_or_a_fixed_bore_without_a_spin_is_fine(self):
        doc, body, solid, an = self.study()
        self.fix_bore(doc, body, solid, an)  # no spin load: a bore fix is an ordinary support
        self.assertFalse([t for k, t in sim.precheck(an) if "stops the bore" in t])
        doc2, body2, solid2, an2 = self.study()
        post.add_spin(an2, 30000, axis="z")
        self.fix_bore(doc2, body2, solid2, an2, "z")  # axial only on the bore face
        self.assertFalse([t for k, t in sim.precheck(an2) if "stops the bore" in t])

    def fixed_dirs(self, c):
        return [i for i, ax in enumerate("xyz") if not getattr(c, f"{ax}Free")]

    def vertex_of(self, body, c):
        return body.Shape.getElement(c.References[0][1][0]).Point

    def test_hold_for_spin_on_an_annulus_is_a_pure_point_set_with_an_axial_ring(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        a_, b_, ring = post.hold_for_spin(an)
        self.assertEqual(self.fixed_dirs(a_), [0, 1])  # both directions across the axis
        self.assertTrue(a_.References[0][1][0].startswith("Vertex"))
        self.assertEqual(self.fixed_dirs(ring), [2])  # axial only, along a circular edge
        self.assertTrue(ring.References[0][1][0].startswith("Edge"))
        # B holds ONE direction and it is tangential at B (never the radial one)
        (j,) = self.fixed_dirs(b_)
        p = self.vertex_of(body, b_)
        radial = [p.x, p.y, 0.0]
        self.assertAlmostEqual(radial[j], 0.0, places=6)
        self.assertGreater(math.hypot(p.x, p.y), 20.0)
        issues = sim.precheck(an)
        self.assertTrue(sim.ready(issues), issues)
        self.assertFalse([t for k, t in issues if "rigid-body" in t or "free" in t or "stops the bore" in t], issues)

    def test_hold_constraints_carry_a_hidden_role_and_old_studies_fall_back_to_the_label(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        made = post.hold_for_spin(an)
        for c in made:
            self.assertEqual(c.BW_Role, "HoldForSpin")
            self.assertTrue(c.getEditorMode("BW_Role"))  # hidden
            self.assertTrue(post.is_hold_for_spin(c))
        import bruceworx_sim_model as model

        self.assertEqual({model.constraint_kind(c) for c in made}, {"hold"})
        plain = vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(2, 0.0)), "z", "Support")
        self.assertFalse(post.is_hold_for_spin(plain))
        self.assertEqual(model.constraint_kind(plain), "displacement")
        old = vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(0, 0.0)), "x", "Hold old study")  # no BW_Role
        self.assertTrue(post.is_hold_for_spin(old))
        self.assertEqual(model.constraint_kind(old), "hold")

    def test_hold_for_spin_with_enough_vertices_is_a_pure_vertex_3_2_1(self):
        import Part

        doc = App.newDocument("Sector")
        solid = Part.makeCylinder(50.0, 4.0, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 70.0).cut(
            Part.makeCylinder(10.0, 4.0, App.Vector(0, 0, 0), App.Vector(0, 0, 1), 70.0))
        body = vc.shape_object(doc, "Sector", solid)
        an = sim.new_study(doc, sim.StudyPlan(name="S", model=body, material=vc.material(), max_size=6.0))["analysis"]
        post.add_spin(an, 30000, axis="z")
        a_, b_, c_ = post.hold_for_spin(an)
        for c in (a_, b_, c_):
            self.assertTrue(c.References[0][1][0].startswith("Vertex"))
        self.assertEqual(self.fixed_dirs(a_), [0, 1, 2])
        self.assertEqual(self.fixed_dirs(c_), [2])
        (j, k) = self.fixed_dirs(b_)
        self.assertEqual(k, 2)
        p = self.vertex_of(body, b_)
        self.assertAlmostEqual([p.x, p.y, 0.0][j], 0.0, places=6)  # the held in-plane direction is tangential at B
        pa, pb, pc = (self.vertex_of(body, c) for c in (a_, b_, c_))
        cross = (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x)
        self.assertGreater(abs(cross), 1.0)  # C is off the line A-B

    def test_hold_for_spin_with_a_face_holds_it_axially_and_refuses_a_cylinder(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        a_, b_, axial = post.hold_for_spin(an, face=vc.faces_where(solid, vc.plane_at(2, 0.0))[0])
        self.assertEqual(self.fixed_dirs(axial), [2])
        self.assertTrue(axial.References[0][1][0].startswith("Face"))
        self.assertEqual(self.fixed_dirs(a_), [0, 1])
        with self.assertRaises(post.PostError):  # a cylindrical face is refused
            post.hold_for_spin(an, face=vc.faces_where(solid, vc.cylinder_of(8.0))[0])

    def test_precheck_a_balanced_spin_held_by_a_face_passes(self):
        doc, body, solid, an = self.study()
        post.add_spin(an, 30000, axis="z")
        self.hold_bore(doc, body, solid, an)
        issues = sim.precheck(an)
        self.assertTrue(sim.ready(issues), issues)
        self.assertFalse([i for i in issues if "centre of mass" in i[1] or "No load" in i[1]], issues)

    def test_precheck_a_balanced_spin_on_a_3_2_1_support_passes_with_a_warning(self):
        doc, body, solid, an = self.study()
        verts = {i: v.Point for i, v in enumerate(solid.Vertexes, 1)}
        a_ = next(f"Vertex{i}" for i, p in verts.items() if abs(p.x - 8.0) < 1e-6 and abs(p.z) < 1e-6)  # bore, bottom
        b_ = next(f"Vertex{i}" for i, p in verts.items() if abs(p.x - 8.0) < 1e-6 and abs(p.z - 6.0) < 1e-6)  # bore, top
        c_ = next(f"Vertex{i}" for i, p in verts.items() if abs(p.x - 30.0) < 1e-6 and abs(p.z) < 1e-6)  # rim, bottom
        vc.add_displacement(doc, an, body, [a_], "xyz", "Pin")
        vc.add_displacement(doc, an, body, [b_], "xy", "Line")
        vc.add_displacement(doc, an, body, [c_], "y", "Plane")
        post.add_spin(an, 30000, axis="z")
        issues = sim.precheck(an)
        self.assertTrue(sim.ready(issues), issues)
        self.assertFalse([t for k, t in issues if "rigid-body" in t or "free" in t], issues)
        self.assertTrue(any("Point supports" in t for k, t in issues), issues)  # warned: they pin the growth locally

    def test_precheck_warns_when_the_axis_misses_the_part(self):
        doc, body, solid, an = self.study(offset=30.0)
        post.add_spin(an, 30000, axis=((0, 0, 0), (0, 0, 1)))
        self.hold_bore(doc, body, solid, an)
        texts = [t for k, t in sim.precheck(an) if k == "warning"]
        self.assertTrue(any("centre of mass" in t and "unbalanced" in t for t in texts), texts)
        self.assertTrue(sim.ready(sim.precheck(an)))  # a warning, not an error

    def test_precheck_without_a_density_is_an_error(self):
        _doc, _body, _solid, an = self.study()
        post.add_spin(an, 1000, axis="z")
        mat = sim.members(an)["materials"][0]
        data = dict(mat.Material)
        data.pop("Density", None)
        mat.Material = data
        self.assertTrue(any("density" in t for k, t in sim.precheck(an) if k == "error"))

    def test_a_spin_alone_without_supports_is_still_told_to_add_one(self):
        _doc, _body, _solid, an = self.study()
        post.add_spin(an, 1000, axis="z")
        self.assertTrue(any("No support" in t for k, t in sim.precheck(an) if k == "error"))

    def test_fixed_range_and_colour_map_are_stored_on_the_study(self):
        _doc, _body, _solid, an = self.study()
        self.assertIsNone(post.fixed_range(an, "hoop"))
        post.set_fixed_range(an, "hoop", -10.0, 200.0)
        self.assertEqual(post.fixed_range(an, "hoop"), (-10.0, 200.0))
        self.assertIsNone(post.fixed_range(an, "radial"))
        post.set_fixed_range(an, "hoop", None)
        self.assertIsNone(post.fixed_range(an, "hoop"))
        self.assertEqual(post.study_colormap(an), "Rainbow")
        post.set_study_colormap(an, "Turbo")
        self.assertEqual(post.study_colormap(an), "Turbo")
        with self.assertRaises(post.PostError):
            post.set_fixed_range(an, "hoop", 5.0, 5.0)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestRotorSolve(unittest.TestCase):
    """The thin annulus at 30 000 rpm, solved once."""

    @classmethod
    def setUpClass(cls):
        close_all()
        cls.tmp = tempfile.mkdtemp(prefix="vl-rotor-")
        cls.setup = vc.build_rotating_disc(PARAMS, SIZE)
        cls.analysis = cls.setup.analysis
        os.makedirs(os.path.join(cls.tmp, "disc"))
        cls.result, cls.stats = val.solve_setup(cls.setup, os.path.join(cls.tmp, "disc"))
        cls.refs = vc.disc_references(10.0, 50.0, 30000.0, 7850.0, 0.3)

    @classmethod
    def tearDownClass(cls):
        close_all()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bore_stress_reports_mean_max_mid_plane_and_von_mises(self):
        got = post.bore_stress(self.result, self.analysis)
        self.assertAlmostEqual(got["radius"], 10.0, delta=1e-3)
        self.assertGreater(got["nodes"], 4)
        self.assertLessEqual(got["hoop_mean"], got["hoop_max"] + 1e-9)
        ref = self.refs["shoop_a"]
        self.assertAlmostEqual(got["hoop_mean"] / ref, 1.0, delta=0.01)
        self.assertAlmostEqual(got["hoop_mid"] / ref, 1.0, delta=0.02)  # a thin disc: the same all along the bore
        self.assertGreaterEqual(got["vonMises_max"], got["hoop_max"] * 0.99)
        self.assertEqual(got["measure"], "vonMises")  # the safety factor divides von Mises, never the hoop stress
        self.assertIn("not the hoop", got["safety_basis"])
        ext = sim.result_extrema(self.result)
        self.assertEqual(ext["bore"]["hoop_mean"], got["hoop_mean"])

    def test_the_input_has_the_centrifugal_load_at_omega_squared(self):
        text = open(os.path.join(self.tmp, "disc", "Mesh.inp"), encoding="utf-8", errors="replace").read()
        self.assertIn("*DLOAD", text)
        fields = next(line for line in text.splitlines() if ",CENTRIF," in line).split(",")
        omega = 30000 * 2 * math.pi / 60
        self.assertAlmostEqual(float(fields[2]) / omega ** 2, 1.0, places=6)
        self.assertAlmostEqual(abs(float(fields[8])), 1.0)  # the axis direction: Z
        self.assertAlmostEqual(float(fields[6]), 0.0)
        self.assertAlmostEqual(float(fields[7]), 0.0)
        self.assertAlmostEqual(float(fields[3]), 0.0)  # a point on the axis
        self.assertAlmostEqual(float(fields[4]), 0.0)
        self.assertEqual(self.stats["order"], "second")

    def test_stress_matches_timoshenko(self):
        got = self.setup.measure(self.analysis, self.result)
        for key in ("shoop_a", "shoop_b", "sr_mid", "shoop_mid"):
            self.assertAlmostEqual(got[key] / self.refs[key], 1.0, delta=0.02, msg=key)
        self.assertAlmostEqual(got["shoop_a"] / self.refs["shoop_a"], 1.0, delta=0.01)

    def test_components_are_stored_and_recomputed_only_for_a_new_axis(self):
        r = self.result
        post.cylindrical_stress(r, analysis=self.analysis)
        n = len(r.NodeNumbers)
        for name in ("StressRadial", "StressHoop", "StressAxial", "StressRTheta"):
            self.assertEqual(len(getattr(r, name)), n, name)
        self.assertAlmostEqual(abs(r.CylAxisDirection.z), 1.0)
        # a thin free disc: axial stress ~ 0 (plane stress), the shear is small against the hoop stress
        self.assertLess(max(abs(x) for x in r.StressAxial), 0.05 * max(r.StressHoop))
        self.assertLess(max(abs(x) for x in r.StressRTheta), 0.05 * max(r.StressHoop))
        # the sum of the normal components is the trace: invariant, equal to the sum of the Cartesian ones
        for i in (0, n // 2, n - 1):
            trace = r.NodeStressXX[i] + r.NodeStressYY[i] + r.NodeStressZZ[i]
            self.assertAlmostEqual(r.StressRadial[i] + r.StressHoop[i] + r.StressAxial[i], trace, places=6)
        first = list(r.StressHoop)
        post.cylindrical_stress(r, analysis=self.analysis)
        self.assertEqual(list(r.StressHoop), first)
        self.assertEqual(post.field_values(r, "hoop", self.analysis), first)
        self.assertEqual(post.field_values(r, "vonMises", self.analysis), list(r.vonMises))
        lo, hi = post.field_range(r, "hoop", self.analysis)
        self.assertGreater(hi, 150.0)
        self.assertLess(lo, 45.0)

    def test_the_vtu_carries_every_field(self):
        path = post.export_vtu(self.analysis, os.path.join(self.tmp, "disc.vtu"), self.result)
        grid = post.read_vtu(path)
        self.assertEqual(grid.GetNumberOfPoints(), len(self.result.NodeNumbers))
        self.assertEqual(grid.GetNumberOfCells(), self.stats["elements"])
        names = [grid.GetPointData().GetArrayName(i) for i in range(grid.GetPointData().GetNumberOfArrays())]
        for needed in ("Displacement", "DisplacementMagnitude", "vonMises", "Stress", "Radial", "Hoop", "Axial", "RTheta", "SafetyFactor"):
            self.assertIn(needed, names)
        self.assertEqual(grid.GetCellType(0), 24)  # a quadratic tetrahedron
        from vtkmodules.util.numpy_support import vtk_to_numpy

        hoop = vtk_to_numpy(grid.GetPointData().GetArray("Hoop"))
        self.assertAlmostEqual(float(hoop.max()), max(self.result.StressHoop), places=3)
        axis = vtk_to_numpy(grid.GetFieldData().GetArray("AxisDirection"))
        self.assertAlmostEqual(abs(float(axis[2])), 1.0)

    def test_radial_profile_follows_the_closed_form(self):
        csv = os.path.join(self.tmp, "hoop.csv")
        rows = post.radial_profile(self.result, "hoop", analysis=self.analysis, angle=45.0, count=80, csv=csv)
        self.assertGreater(len(rows), 40)
        self.assertAlmostEqual(rows[0][0], 10.0, delta=1.6)  # starts at the bore (the first points are in the hole)
        self.assertAlmostEqual(rows[-1][0], 50.0, delta=1.0)
        a, b, rho, w, nu = 0.01, 0.05, 7850.0, 30000 * 2 * math.pi / 60, 0.3
        for r, value in rows:
            if 14.0 < r < 46.0:
                ref = post.rotating_disc(r / 1000.0, a, b, rho, w, nu)[1] / 1e6
                self.assertAlmostEqual(value / ref, 1.0, delta=0.04, msg=f"r={r}")
        lines = open(csv, encoding="utf-8").read().splitlines()
        self.assertTrue(lines[0].startswith("r_mm,hoop"))
        self.assertEqual(len(lines) - 1, len(rows))

    @unittest.skipUnless(post.pvbatch_path(), "ParaView (pvbatch) is not installed")
    def test_a_quarter_cut_picture_by_pvbatch(self):
        path = post.contour_png(self.analysis, "hoop", view="quarter", path=os.path.join(self.tmp, "hoop_quarter.png"),
                                cmap="Turbo", range=(0.0, 170.0), size=(700, 450), result=self.result)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 5000)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(post.PostError):
            post.contour_png(self.analysis, "hoop", view="nonsense", path=os.path.join(self.tmp, "x.png"), result=self.result)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestOffsetBoreAndPartFactor(unittest.TestCase):
    """The real impeller: bore r = 5 spanning z -3 to 23.5, spin axis built by the helper (SpinAxis line starts far below).
    bore_stress used a radius band (found the 36 mid-edge nodes at r = 4.913) and z from the axis line's start (z_mid 68)."""

    @classmethod
    def setUpClass(cls):
        import Part

        close_all()
        cls.tmp = tempfile.mkdtemp(prefix="vl-off-")
        doc = App.newDocument("Offset")
        cx, cy = 7.0, 3.0  # off the origin too
        solid = Part.makeCylinder(20.0, 26.5, App.Vector(cx, cy, -3.0)).cut(Part.makeCylinder(5.0, 26.5, App.Vector(cx, cy, -3.0)))
        body = vc.shape_object(doc, "Wheel", solid)
        mat = vc.material(71700.0, 0.33, 2810.0, name="7075")
        cls.an = sim.new_study(doc, sim.StudyPlan(name="Off", model=body, material=mat, max_size=3.0))["analysis"]
        post.add_spin(cls.an, 60000, axis=((cx, cy, 0.0), (0.0, 0.0, 1.0)))
        post.hold_for_spin(cls.an)
        cls.result = sim.solve(cls.an, working_dir=cls.tmp)

    @classmethod
    def tearDownClass(cls):
        close_all()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_bore_is_found_on_its_face_in_global_z(self):
        got = post.bore_stress(self.result, self.an)
        self.assertAlmostEqual(got["radius"], 5.0, places=6)  # the face's radius, not 4.913 of a mid-edge node
        self.assertAlmostEqual(got["z_min"], -3.0, places=4)
        self.assertAlmostEqual(got["z_max"], 23.5, places=4)
        self.assertAlmostEqual(got["z_mid"], 10.25, places=4)  # inside the part, not measured from the axis line's start
        self.assertAlmostEqual(got["length"], 26.5, places=4)
        self.assertGreater(got["nodes"], 60)  # every node of the face, mid-edge ones included
        self.assertGreater(got["mid_nodes"], 2)
        self.assertGreater(got["hoop_mean"], 0.0)
        self.assertLessEqual(got["hoop_mean"], got["hoop_max"])
        self.assertNotIn("min_sf", got)  # the bore's factor is bore_min_sf, never presented as the part's

    def test_a_solid_shaft_has_no_bore(self):
        import Part

        doc = App.newDocument("Shaft")
        body = vc.shape_object(doc, "Shaft", Part.makeCylinder(10.0, 20.0))
        an = sim.new_study(doc, sim.StudyPlan(name="Sh", model=body, material=vc.material(), max_size=6.0))["analysis"]
        self.assertEqual(post.bore_faces(an, ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))), [])  # a convex journal is not a bore

    def test_the_part_factor_is_the_minimum_over_all_nodes(self):
        ext = sim.result_extrema(self.result)
        factors = list(self.result.SafetyFactor)
        self.assertEqual(ext["min_sf"], min(factors))
        bore = ext["bore"]
        self.assertLessEqual(ext["min_sf"], bore["bore_min_sf"] + 1e-12)
        self.assertIsNotNone(bore["bore_min_sf"])

    def test_point_supports_leave_out_the_nodes_round_them_and_say_so(self):
        ext = sim.result_extrema(self.result)
        self.assertAlmostEqual(ext["support_radius"], 2.0 * 3.0)  # 2 x MaxSize
        self.assertGreater(ext["support_nodes"], 3)  # more than the three pinned nodes
        self.assertGreaterEqual(ext["min_sf_away"], ext["min_sf"])
        out = sim.write_report(self.an, folder=self.tmp, compile=False)
        text = open(out["tex"], encoding="utf-8").read()
        self.assertIn("Min safety factor, whole part", text)
        self.assertIn("Bore only: lowest safety factor", text)
        self.assertIn("within 6 mm of a point support", text)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestHoldForSpin(unittest.TestCase):
    """A full thin wheel (no symmetry planes) held by the helper: the bore grows freely, hoop = Timoshenko."""

    def test_a_full_wheel_held_by_the_point_set_matches_timoshenko_within_a_tenth_of_a_percent(self):
        import Part

        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-hold-")
        try:
            doc = App.newDocument("Full")
            solid = Part.makeCylinder(50.0, 2.0, App.Vector(0, 0, -1)).cut(Part.makeCylinder(10.0, 2.0, App.Vector(0, 0, -1)))
            body = vc.shape_object(doc, "Wheel", solid)
            objs = sim.new_study(doc, sim.StudyPlan(name="Full", model=body, material=vc.material(210000.0, 0.3, 7850.0, name="Steel"),
                                                    max_size=3.0))
            an = objs["analysis"]
            post.add_spin(an, 30000, axis="z")
            post.hold_for_spin(an)
            result = sim.solve(an, working_dir=tmp)
            ref = vc.disc_references(10.0, 50.0, 30000.0, 7850.0, 0.3)["shoop_a"]
            got = post.bore_stress(result, an)
            self.assertAlmostEqual(got["hoop_mean"] / ref, 1.0, delta=0.001)
            self.assertAlmostEqual(got["length"], 2.0, delta=1e-6)
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


class FakeMeshObject:
    """Stands in for the Netgen object: recompute does nothing, FemMesh is what the test set."""

    class _Doc:
        def recompute(self, _objs=None):
            pass

    def __init__(self, fem, second_order):
        self.FemMesh, self.SecondOrder, self.MaxSize, self.Fineness = fem, second_order, 5.0, "Moderate"
        self.Document = self._Doc()

    def touch(self):
        pass


class TestMeshFailures(unittest.TestCase):
    """Netgen on a bladed impeller returned surface-only meshes, or linear tets with ~135k unused nodes (SIM-04 follow-up)."""

    def first_order_mesh(self, orphans=0):
        import Fem
        import Part

        close_all()
        doc = App.newDocument("M")
        solid = Part.makeCylinder(20, 6).cut(Part.makeCylinder(8, 6))
        body = vc.shape_object(doc, "W", solid)
        objs = sim.new_study(doc, sim.StudyPlan(name="W", model=body, material=vc.material(), max_size=6.0, second_order=False))
        sim.mesh_study(objs["analysis"])
        fem = sim.rebuild_mesh(sim.members(objs["analysis"])["mesh"].FemMesh, drop_unused=False)  # the mesh object's is immutable
        top = max(fem.Nodes)
        for k in range(orphans):  # nodes no element uses
            fem.addNode(1.0 + k, 2.0, 3.0, top + 1 + k)
        return objs["analysis"], fem

    def run_study_mesh(self, an, fem, second_order, messages):
        fake = FakeMeshObject(fem, second_order)
        real = sim.members
        sim.members = lambda a: dict(real(a), mesh=fake)
        try:
            return sim.mesh_study(an, messages.append), fake
        finally:
            sim.members = real

    def test_a_surface_only_mesh_is_an_error(self):
        import Fem

        an, fem = self.first_order_mesh()
        surface = Fem.FemMesh()
        for nid, v in fem.Nodes.items():
            surface.addNode(v.x, v.y, v.z, nid)
        for fid in fem.Faces:
            surface.addFace(list(fem.getElementNodes(fid)), fid)
        self.assertEqual(surface.VolumeCount, 0)
        with self.assertRaises(sim.StudyError) as ctx:
            self.run_study_mesh(an, surface, True, [])
        self.assertIn("no volume elements", str(ctx.exception))

    def surface_only(self, fem):
        import Fem

        surface = Fem.FemMesh()
        for nid, v in fem.Nodes.items():
            surface.addNode(v.x, v.y, v.z, nid)
        for fid in fem.Faces:
            surface.addFace(list(fem.getElementNodes(fid)), fid)
        return surface

    def real_study(self, second_order):
        import Part

        close_all()
        doc = App.newDocument("MR")
        body = vc.shape_object(doc, "W", Part.makeCylinder(20, 6).cut(Part.makeCylinder(8, 6)))
        objs = sim.new_study(doc, sim.StudyPlan(name="W", model=body, material=vc.material(), max_size=6.0,
                                                second_order=second_order))
        return objs["analysis"], sim.members(objs["analysis"])["mesh"]

    def test_a_surface_only_result_is_retried_on_a_repaired_copy(self):
        an, mesh = self.real_study(False)
        shape_before = mesh.Shape.Shape.copy()
        real_in_place, tries = sim._mesh_in_place, []
        sim._mesh_in_place = lambda m: self.surface_only(real_in_place(m))
        said = []
        try:
            stats = sim.mesh_study(an, said.append)
        finally:
            sim._mesh_in_place = real_in_place
        self.assertGreater(stats["elements"], 50)
        self.assertEqual(stats["order"], "first")
        self.assertTrue(any("repaired copy" in t for t in stats["notes"]), stats["notes"])
        self.assertTrue(stats["fallback"])
        self.assertTrue(any("retrying on a repaired copy" in t for t in said), said)
        self.assertAlmostEqual(mesh.Shape.Shape.Volume, shape_before.Volume, places=6)  # the user's shape is untouched
        self.assertEqual(len([o for o in mesh.Document.Objects if o.Name.startswith("BW_MeshRetry")]), 0)
        self.assertFalse([d for d in App.listDocuments() if d == "BW_MeshRetry"])

    def test_second_order_falls_back_to_first_order_and_tet10(self):
        an, mesh = self.real_study(True)
        real_in_place, real_copy, orders = sim._mesh_in_place, sim._mesh_on_copy, []
        sim._mesh_in_place = lambda m: self.surface_only(real_in_place(m))

        def copy(m, shape, second):
            orders.append(second)
            fem = real_copy(m, shape, second)
            return self.surface_only(fem) if second else fem  # second order gives no volumes, first order does

        sim._mesh_on_copy = copy
        try:
            stats = sim.mesh_study(an)
        finally:
            sim._mesh_in_place, sim._mesh_on_copy = real_in_place, real_copy
        self.assertEqual(orders, [True, False])  # bounded: one try per order
        self.assertEqual(stats["order"], "second")
        self.assertTrue(any("first order" in t for t in stats["notes"]), stats["notes"])
        self.assertTrue(any("upgraded" in t for t in stats["notes"]), stats["notes"])

    def test_every_retry_failing_still_raises(self):
        an, mesh = self.real_study(True)
        real_in_place, real_copy, calls = sim._mesh_in_place, sim._mesh_on_copy, []
        sim._mesh_in_place = lambda m: self.surface_only(real_in_place(m))
        sim._mesh_on_copy = lambda m, shape, second: (calls.append(second), self.surface_only(real_copy(m, shape, second)))[1]
        try:
            with self.assertRaises(sim.StudyError) as ctx:
                sim.mesh_study(an)
        finally:
            sim._mesh_in_place, sim._mesh_on_copy = real_in_place, real_copy
        self.assertEqual(calls, [True, False])
        self.assertIn("no volume elements", str(ctx.exception))

    def test_unused_nodes_are_dropped(self):
        an, fem = self.first_order_mesh(orphans=40)
        used = len(sim.used_node_ids(fem))
        self.assertEqual(fem.NodeCount, used + 40)
        said = []
        stats, fake = self.run_study_mesh(an, fem, False, said)
        self.assertEqual(fake.FemMesh.NodeCount, used)
        self.assertEqual(stats["nodes"], used)
        self.assertEqual(len(sim.used_node_ids(fake.FemMesh)), used)
        self.assertTrue(any("40 nodes" in t for t in said), said)

    def test_linear_tets_are_upgraded_to_tet10_in_fem_order(self):
        an, fem = self.first_order_mesh(orphans=5)
        n4, elements = len(sim.used_node_ids(fem)), fem.VolumeCount
        said = []
        stats, fake = self.run_study_mesh(an, fem, True, said)
        new = fake.FemMesh
        self.assertEqual(stats["order"], "second")
        self.assertEqual(new.VolumeCount, elements)
        self.assertGreater(new.NodeCount, n4)
        self.assertEqual(len(sim.used_node_ids(new)), new.NodeCount)  # no unused nodes
        self.assertTrue(any("Tet10" in t for t in said), said)
        nodes = new.Nodes
        for vid in new.Volumes:
            ids = list(new.getElementNodes(vid))
            self.assertEqual(len(ids), 10)
            p = [nodes[i] for i in ids]
            for k, (i, j) in enumerate(sim.TET10_EDGES):
                mid = p[4 + k]
                self.assertAlmostEqual(mid.x, 0.5 * (p[i].x + p[j].x), places=9)
                self.assertAlmostEqual(mid.y, 0.5 * (p[i].y + p[j].y), places=9)
                self.assertAlmostEqual(mid.z, 0.5 * (p[i].z + p[j].z), places=9)

    def test_the_fem_tet10_order_is_the_one_netgen_makes(self):
        import Part

        close_all()
        doc = App.newDocument("N")
        solid = Part.makeCylinder(20, 6).cut(Part.makeCylinder(8, 6))
        body = vc.shape_object(doc, "W", solid)
        objs = sim.new_study(doc, sim.StudyPlan(name="W", model=body, material=vc.material(), max_size=8.0, second_order=True))
        sim.mesh_study(objs["analysis"])
        fem = sim.members(objs["analysis"])["mesh"].FemMesh
        nodes = fem.Nodes
        ids = list(fem.getElementNodes(next(iter(fem.Volumes))))
        self.assertEqual(len(ids), 10)
        p = [nodes[i] for i in ids]
        import math as m

        for k in range(6):  # curved edges bend the mid node: it must still be nearest to its own edge's midpoint
            def gap(pair):
                i, j = pair
                return m.dist((p[4 + k].x, p[4 + k].y, p[4 + k].z),
                              (0.5 * (p[i].x + p[j].x), 0.5 * (p[i].y + p[j].y), 0.5 * (p[i].z + p[j].z)))

            self.assertEqual(min(sim.TET10_EDGES, key=gap), sim.TET10_EDGES[k])

    @unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
    def test_an_upgraded_mesh_solves_to_the_closed_form(self):
        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-up-")
        try:
            setup = vc.build_rotating_disc(PARAMS, 5.0)
            mesh = sim.members(setup.analysis)["mesh"]
            mesh.SecondOrder = False
            sim.mesh_study(setup.analysis)
            self.assertEqual(sim.mesh_stats(mesh)["order"], "first")
            mesh.FemMesh = sim.rebuild_mesh(mesh.FemMesh, upgrade=True)
            self.assertEqual(sim.mesh_stats(mesh)["order"], "second")
            result = sim.solve(setup.analysis, working_dir=tmp)
            ref = vc.disc_references(10.0, 50.0, 30000.0, 7850.0, 0.3)
            self.assertAlmostEqual(setup.measure(setup.analysis, result)["shoop_a"] / ref["shoop_a"], 1.0, delta=0.03)
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_elements_touching_the_body_at_a_node_or_an_edge_are_dropped(self):
        an, fem = self.first_order_mesh()
        n_vol = fem.VolumeCount
        top = max(fem.Nodes)
        bodyv = next(iter(fem.Volumes))
        shared = fem.getElementNodes(bodyv)[0]
        hang = fem.Nodes[shared]
        new = sim.rebuild_mesh(fem, drop_unused=False)
        # a tet hanging by ONE node, and a second one hanging by an EDGE (two nodes) of the body's first tet
        n1 = [top + 1, top + 2, top + 3]
        for k, nid in enumerate(n1):
            new.addNode(hang.x + 100 + k, hang.y + 100, hang.z + 100 + (k % 2), nid)
        new.addVolume([shared] + n1, 900001)
        edge = fem.getElementNodes(bodyv)[:2]
        n2 = [top + 4, top + 5]
        for k, nid in enumerate(n2):
            new.addNode(hang.x - 100 - k, hang.y - 90 + 5 * k, hang.z - 100, nid)
        new.addVolume(list(edge) + n2, 900002)
        keep, loose, clusters = sim.face_connected_volumes(new)
        self.assertEqual((loose, clusters), (2, 2))
        self.assertEqual(len(keep), n_vol)
        self.assertNotIn(900001, keep)
        said = []
        stats, fake = self.run_study_mesh(an, new, False, said)
        self.assertEqual(fake.FemMesh.VolumeCount, n_vol)
        self.assertEqual(len(sim.used_node_ids(fake.FemMesh)), fake.FemMesh.NodeCount)
        self.assertTrue(any("2 elements in 2 clusters" in t for t in said), said)
        self.assertTrue(any("2 clusters" in t for t in stats["notes"]))
        # a connected mesh is left alone
        self.assertEqual(sim.face_connected_volumes(fem)[1:], (0, 0))

    def test_a_singular_solution_is_refused(self):
        class R:
            DisplacementLengths = [0.1, 5.0e6]

            class Mesh:
                class FemMesh:
                    class _V:
                        def __init__(self, x):
                            self.x, self.y, self.z = x, 0.0, 0.0

                    Nodes = {1: _V(0.0), 2: _V(100.0)}

        with self.assertRaises(sim.StudyError) as ctx:
            sim.check_displacement(R, None)
        self.assertIn("singular", str(ctx.exception))
        R.DisplacementLengths = [0.1, 5.0]
        sim.check_displacement(R, None)  # a sane one passes


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestWorkingDir(unittest.TestCase):
    """sim.solve(an, working_dir=...) writes the ccx files there, also when the folder does not exist yet."""

    def test_the_files_land_in_the_given_folder(self):
        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-wd-")
        try:
            setup = vc.build_rotating_disc(PARAMS, 8.0)
            target = os.path.join(tmp, "not", "yet", "there")
            result = sim.solve(setup.analysis, working_dir=target)
            self.assertIsNotNone(result)
            names = os.listdir(target)
            self.assertTrue(any(n.endswith(".inp") for n in names), names)
            self.assertTrue(any(n.endswith(".frd") for n in names), names)
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestRimTraction(unittest.TestCase):
    """A reversed Pressure on the rim is an outward traction (no new code): the Lame hoop stress at the rim."""

    def test_reversed_pressure_pulls_the_rim_outward(self):
        import Part

        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-rim-")
        try:
            doc = App.newDocument("Rim")
            a, b, half = 10.0, 50.0, 1.0
            solid = Part.makeCylinder(b, half).cut(Part.makeCylinder(a, half)).common(Part.makeBox(b, b, half))
            body = vc.shape_object(doc, "Ring", solid)
            objs = sim.new_study(doc, sim.StudyPlan(name="Rim", model=body, material=vc.material(), max_size=4.0))
            an = objs["analysis"]
            vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(0, 0.0)), "x", "SymX")
            vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(1, 0.0)), "y", "SymY")
            vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(2, 0.0)), "z", "MidPlane")
            vc.add_pressure(doc, an, body, vc.faces_where(solid, vc.cylinder_of(b)), 10.0, "RimTraction", reversed_=True)
            doc.recompute()
            result, _stats = val.solve_setup(vc.Setup(doc, objs, None), tmp)
            values = post.cylindrical_stress(result, axis="z", analysis=an)
            rim = [h for h, r in zip(values["hoop"], values["r"]) if abs(r - b) < 1e-4]
            radial = [s for s, r in zip(values["radial"], values["r"]) if abs(r - b) < 1e-4]
            expected = 10.0 * (b * b + a * a) / (b * b - a * a)  # Lame, outer tension T: T (b^2 + a^2) / (b^2 - a^2)
            self.assertAlmostEqual(sum(rim) / len(rim) / expected, 1.0, delta=0.03)
            self.assertAlmostEqual(sum(radial) / len(radial), 10.0, delta=0.5)  # sigma_r = +T: outward, tensile
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


BLADES = {"count": 24, "mass_g": 2.0, "r_cg_mm": 55.0}  # the whole wheel's; the model (an eighth) carries 3


class TestBladeMass(unittest.TestCase):
    """A Blade mass load: N m r_cg omega^2 over the rim faces, following the spin speed (SIM-04, 1 Nov)."""

    def setUp(self):
        close_all()

    def tearDown(self):
        close_all()

    def study(self):
        import Part

        doc = App.newDocument("Blades")
        solid = Part.makeCylinder(50.0, 2.0).cut(Part.makeCylinder(10.0, 2.0))
        body = vc.shape_object(doc, "Wheel", solid)
        an = sim.new_study(doc, sim.StudyPlan(name="Wheel", model=body, material=vc.material(), max_size=5.0))["analysis"]
        spin = post.add_spin(an, 30000, axis="z")
        rim = [(body, f) for f in vc.faces_where(solid, vc.cylinder_of(50.0))]
        return doc, body, an, spin, rim

    def test_the_pressure_is_the_total_pull_over_the_rim_area(self):
        _doc, _body, an, _spin, rim = self.study()
        load = post.add_blade_mass(an, rim, 24, 2.0, 55.0)
        w = 30000 * 2 * math.pi / 60
        force = 0.002 * 0.055 * w * w
        self.assertAlmostEqual(load.BladePullN, force, delta=1e-6)
        self.assertAlmostEqual(load.BladeTotalN, 24 * force, delta=1e-5)
        self.assertAlmostEqual(load.BladeArea, 2 * math.pi * 50.0 * 2.0, delta=1e-6)  # the whole rim face
        pressure = load.Pressure.getValueAs("MPa").Value
        self.assertAlmostEqual(pressure * load.BladeArea, 24 * force, delta=1e-3)
        self.assertAlmostEqual(pressure, vc.blade_traction(50.0, 2.0, 30000.0, BLADES), delta=1e-6)
        self.assertTrue(load.Reversed)  # outward
        self.assertIn(load, sim.members(an)["loads"])
        self.assertIn("Blade mass", sim._describe(load)["kind"])
        self.assertTrue(post.is_blade_load(load))

    def test_it_follows_the_spin_speed(self):
        _doc, _body, an, spin, rim = self.study()
        load = post.add_blade_mass(an, rim, 24, 2.0, 55.0)
        before = load.Pressure.getValueAs("MPa").Value
        post.set_spin_speed(spin, 15000)
        self.assertAlmostEqual(load.Pressure.getValueAs("MPa").Value, before / 4.0, delta=1e-6)  # omega squared
        post.set_blade_data(load, mass_g=4.0)
        self.assertAlmostEqual(load.Pressure.getValueAs("MPa").Value, before / 2.0, delta=1e-6)
        spin.RotationFrequency = f"{30000 / 60} 1/s"  # changed behind our back: the pre-check refreshes it
        sim.precheck(an)
        self.assertAlmostEqual(load.Pressure.getValueAs("MPa").Value, before * 2.0, delta=1e-6)

    def test_the_load_needs_a_spin_and_sane_numbers(self):
        _doc, _body, an, spin, rim = self.study()
        with self.assertRaises(post.PostError):
            post.add_blade_mass(an, rim, 0, 2.0, 55.0)
        with self.assertRaises(post.PostError):
            post.add_blade_mass(an, rim, 24, -1.0, 55.0)
        load = post.add_blade_mass(an, rim, 24, 2.0, 55.0)
        self.assertEqual([t for lv, t in post.precheck_blades(an) if lv == "error"], [])
        an.removeObject(spin)
        errors = [t for lv, t in post.precheck_blades(an) if lv == "error"]
        self.assertTrue(any("no spin load" in t for t in errors), errors)
        with self.assertRaises(post.PostError):
            post.add_blade_mass(an, rim, 24, 2.0, 55.0)
        self.assertIsNotNone(load)

    def test_a_face_that_does_not_face_outward_is_warned(self):
        _doc, body, an, _spin, _rim = self.study()
        bore = [(body, f) for f in vc.faces_where(body.Shape, vc.cylinder_of(10.0))]
        post.add_blade_mass(an, bore, 24, 2.0, 55.0)
        warnings = [t for lv, t in post.precheck_blades(an) if lv == "warning"]
        self.assertTrue(any("does not face outward" in t for t in warnings), warnings)


class TestBladeMassSolve(unittest.TestCase):
    """The G-RD3 quarter (an eighth of a 24 blade wheel) against the Timoshenko disc plus Lame's rim traction."""

    def test_the_rim_pull_matches_the_closed_form(self):
        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-blades-")
        try:
            setup = vc.build_rotating_disc_blades(dict(PARAMS, blades=BLADES), SIZE)
            result, _stats = val.solve_setup(setup, tmp)
            refs = vc.disc_references(10.0, 50.0, 30000.0, 7850.0, 0.3, blades=BLADES)
            got = setup.measure(setup.analysis, result)
            for key in ("shoop_a", "shoop_b", "sr_mid", "shoop_mid"):
                self.assertAlmostEqual(got[key] / refs[key], 1.0, delta=0.03, msg=key)
            # the same wheel without the blades: the pull adds about 86 MPa at the bore
            plain = vc.disc_references(10.0, 50.0, 30000.0, 7850.0, 0.3)
            self.assertGreater(got["shoop_a"], plain["shoop_a"] + 80)
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


def hot_spinning_disc(doc, kind, size=4.0):
    """The G-RD1 quarter annulus as a ``kind`` ('static' / 'thermal_stress') study: symmetry planes, a spin at 30 000 rpm and, for
    thermal stress, the bore at 120 deg C and the rim at 20 deg C (the stress-free temperature is 20)."""
    import Part

    import bruceworx_sim_thermal as thermal

    solid = Part.makeCylinder(50.0, 1.0).cut(Part.makeCylinder(10.0, 1.0)).common(Part.makeBox(50.0, 50.0, 1.0))
    body = vc.shape_object(doc, "Disc", solid)
    mat = vc.material(210000.0, 0.3, 7850.0, k=50.0, cp=480.0, alpha=12e-6, name="Steel")
    mat["uts"] = 900.0
    options = {"ReferenceTemperature": 20.0, "InitialTemperature": 20.0} if kind == "thermal_stress" else {}
    plan = sim.StudyPlan(name=kind, model=body, material=mat, max_size=size, study_type=kind, options=options)
    an = sim.new_study(doc, plan)["analysis"]
    vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(0, 0.0)), "x", "SymX")
    vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(1, 0.0)), "y", "SymY")
    vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(2, 0.0)), "z", "MidPlane")
    if kind == "thermal_stress":
        thermal.add_temperature(an, [(body, f) for f in vc.faces_where(solid, vc.cylinder_of(10.0))], 120.0, "Hot bore")
        thermal.add_temperature(an, [(body, f) for f in vc.faces_where(solid, vc.cylinder_of(50.0))], 20.0, "Cool rim")
    doc.recompute()
    return body, an


def ring_hoop(result, an, radius):
    values = post.cylindrical_stress(result, axis="z", analysis=an)
    rows = [h for h, r in zip(values["hoop"], values["r"]) if abs(r - radius) < 1e-4]
    return sum(rows) / len(rows)


class TestSpinWithHeat(unittest.TestCase):
    """The spin load also solves in a thermal stress study: hot disc = thermal stress + centrifugal stress."""

    def test_thermal_plus_spin_is_the_sum_of_the_two(self):
        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-hotspin-")
        try:
            doc = App.newDocument("HotSpin")
            _body, an = hot_spinning_disc(doc, "thermal_stress")
            self.assertEqual([t for lv, t in sim.precheck(an) if lv == "error"], [])
            heat = sim.solve(an, working_dir=os.path.join(tmp, "heat"))
            self.assertTrue(math.isfinite(sum(heat.vonMises)))
            hot = {r: ring_hoop(heat, an, r) for r in (10.0, 50.0)}
            self.assertLess(hot[10.0], -50.0)  # the hot bore is squeezed by the cool rim
            spin = post.add_spin(an, 30000, axis="z")
            self.assertEqual([t for lv, t in sim.precheck(an) if lv == "error"], [])  # a spin is checked in this study too
            both = sim.solve(an, working_dir=os.path.join(tmp, "both"))
            doc2 = App.newDocument("SpinOnly")
            _body2, an2 = hot_spinning_disc(doc2, "static")
            post.add_spin(an2, 30000, axis="z")
            spun = sim.solve(an2, working_dir=os.path.join(tmp, "spin"))
            self.assertIn("CENTRIF", open(os.path.join(tmp, "both", "Mesh.inp"), errors="replace").read())
            for radius in (10.0, 50.0):
                total = ring_hoop(both, an, radius)
                parts = hot[radius] + ring_hoop(spun, an2, radius)
                self.assertAlmostEqual(total, parts, delta=0.005 * abs(parts) + 0.5, msg=f"r = {radius}")
            # the closed forms: Timoshenko's rotating disc plus the thermal disc (steady conduction: T = T_bore ln(b/r)/ln(b/a))
            a, b, E, nu, rho = 0.01, 0.05, 210e9, 0.3, 7850.0
            omega = 30000 * 2 * math.pi / 60
            temperature = lambda r: 100.0 * math.log(b / r) / math.log(b / a)  # noqa: E731
            for radius in (10.0, 50.0):
                r = radius / 1000.0
                expected = (post.thermal_disc(r, a, b, E, 12e-6, temperature)[1] + post.rotating_disc(r, a, b, rho, omega, nu)[1]) / 1e6
                self.assertAlmostEqual(ring_hoop(both, an, radius), expected, delta=0.03 * abs(expected), msg=f"closed form r = {radius}")
            self.assertEqual(post.spin_loads(an), [spin])
            ext = sim.result_extrema(both)
            self.assertTrue(ext["burst"]["burst_rpm"])  # the burst speed is reported for a hot disc too
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


class TestBurstSpeed(unittest.TestCase):
    """n_burst = n sqrt(UTS / average hoop stress over the meridional section) against the closed form of the thin disc."""

    def test_burst_speed_of_the_thin_annulus(self):
        close_all()
        tmp = tempfile.mkdtemp(prefix="vl-burst-")
        try:
            doc = App.newDocument("Burst")
            _body, an = hot_spinning_disc(doc, "static", size=3.0)
            post.add_spin(an, 30000, axis="z")
            result = sim.solve(an, working_dir=tmp)
            a, b, rho, nu = 0.01, 0.05, 7850.0, 0.3
            omega = 30000 * 2 * math.pi / 60
            steps = 2000
            mean = sum(post.rotating_disc(a + (b - a) * (i + 0.5) / steps, a, b, rho, omega, nu)[1] for i in range(steps)) / steps / 1e6
            got = post.burst_speed(result, an)
            self.assertAlmostEqual(got["avg_hoop"] / mean, 1.0, delta=0.01)
            self.assertAlmostEqual(got["burst_rpm"], 30000 * math.sqrt(900.0 / mean), delta=0.006 * got["burst_rpm"])
            self.assertAlmostEqual(got["margin"], got["burst_rpm"] / 30000, delta=1e-9)
            self.assertIn("Steel", got["uts_source"])
            ext = sim.result_extrema(result)
            self.assertEqual(ext["burst"]["burst_rpm"], got["burst_rpm"])
            tex = open(sim.report_tex(an)["tex"], encoding="utf-8").read()
            self.assertIn("Burst speed (Zwicky)", tex)
            self.assertIn("Burst margin", tex)
            # the limit (default 1.2, a study property): below it is an error once results exist, in the panel text and the report
            self.assertEqual(post.burst_limit(an), 1.2)
            self.assertFalse(got["below"])
            self.assertEqual([t for lv, t in sim.precheck(an) if "Burst margin" in t], [])
            an.BurstMarginLimit = 5.0
            errors = [t for lv, t in sim.precheck(an) if lv == "error" and "Burst margin" in t]
            self.assertEqual(len(errors), 1, errors)
            self.assertEqual([t for lv, t in sim.precheck(an, results=False) if "Burst margin" in t], [])  # never blocks a solve
            low = post.burst_speed(result, an)
            self.assertTrue(low["below"])
            import bruceworx_sim_gui as gui

            self.assertIn("ERROR: below the limit 5", gui.burst_html(low))
            self.assertIn("ERROR: margin", open(sim.report_tex(an)["tex"], encoding="utf-8").read())
            an.BurstMarginLimit = 1.2
            # without an ultimate strength there is no burst speed, and the reason is given
            mat = sim.members(an)["materials"][0]
            card = dict(mat.Material)
            card.pop("UltimateTensileStrength", None)
            mat.Material = card
            analysis_uuid = an.MaterialUUID if "MaterialUUID" in an.PropertiesList else ""
            if not analysis_uuid:
                none = post.burst_speed(result, an)
                self.assertIsNone(none["burst_rpm"])
                self.assertIn("ultimate", none["note"])
        finally:
            close_all()
            shutil.rmtree(tmp, ignore_errors=True)


class TestMeshWorker(unittest.TestCase):
    """SIM-04: Netgen in a worker process with a time limit (bruceworx_sim_meshworker); the hang is a worker that sleeps."""

    def setUp(self):
        import bruceworx_sim_meshworker as mw

        self.mw = mw
        self.before = self.leftovers()

    def tearDown(self):
        self.mw.TEST_HOOK = None
        close_all()

    @staticmethod
    def leftovers():
        import glob
        import subprocess

        dirs = set(glob.glob(os.path.join(tempfile.gettempdir(), "bw-meshjob-*")))
        procs = subprocess.run(["pgrep", "-f", "bw-meshjob-"], capture_output=True, text=True).stdout.split()
        return dirs, set(procs)

    def study(self, second_order, limit=0.0):
        import Part

        close_all()
        doc = App.newDocument("MW")
        body = vc.shape_object(doc, "B", Part.makeBox(60, 10, 10))  # the cantilever
        objs = sim.new_study(doc, sim.StudyPlan(name="W", model=body, material=vc.material(), max_size=3.0,
                                                second_order=second_order))
        an = objs["analysis"]
        an.MeshTimeLimit = limit
        return an, sim.members(an)["mesh"]

    def assertClean(self):
        dirs, procs = self.leftovers()
        self.assertEqual(dirs - self.before[0], set(), "temporary job directories were left")
        self.assertEqual(procs - self.before[1], set(), "worker processes were left")

    def test_the_worker_mesh_equals_the_in_process_mesh(self):
        for second in (False, True):
            an, mesh = self.study(second)
            local = sim._mesh_in_place(mesh)
            shape = sim.shape_of(mesh.Shape)
            settings = self.mw.netgen_settings(mesh, sim.NETGEN_COPY_PROPS, second)
            fem, info = self.mw.run_worker(shape, settings, 120.0, "T")
            self.assertEqual(info["status"], "ok", info)
            self.assertEqual((fem.NodeCount, fem.VolumeCount), (local.NodeCount, local.VolumeCount))
            nodes = len(fem.getElementNodes(next(iter(fem.Volumes))))
            self.assertEqual(nodes, 10 if second else 4)
            self.assertAlmostEqual(fem.Volume, local.Volume, places=3)
        self.assertClean()

    def test_a_worker_that_times_out_is_killed_and_the_next_try_runs(self):
        an, mesh = self.study(True, limit=4.0)
        self.mw.TEST_HOOK = lambda job: job.update(test_sleep=120) if job["settings"].get("SecondOrder") else None
        said = []
        self.assertTrue(sim._use_worker(mesh, an))
        stats = sim.mesh_study(an, said.append)
        self.assertGreater(stats["elements"], 100)
        self.assertEqual(stats["order"], "second")  # first order worker mesh, upgraded to Tet10
        self.assertTrue(any("no mesh after 4 s, worker killed" in t for t in stats["notes"]), stats["notes"])
        self.assertTrue(any("upgraded" in t for t in stats["notes"]), stats["notes"])
        self.assertTrue(any("Try 1" in t and "killed" in t for t in said), said)
        self.assertClean()

    def test_every_try_timing_out_is_an_error_listing_each(self):
        an, mesh = self.study(True, limit=3.0)
        real_gmsh, self.mw.gmsh_binary = self.mw.gmsh_binary, lambda: None  # no third try
        self.addCleanup(setattr, self.mw, "gmsh_binary", real_gmsh)
        self.mw.TEST_HOOK = lambda job: job.update(test_sleep=120)
        t0 = __import__("time").time()
        with self.assertRaises(sim.StudyError) as ctx:
            sim.mesh_study(an)
        text = str(ctx.exception)
        self.assertIn("Try 1", text)
        self.assertIn("Try 2", text)
        self.assertLess(__import__("time").time() - t0, 30)
        self.assertClean()

    def test_cancel_kills_the_worker(self):
        import threading

        an, mesh = self.study(False, limit=60.0)
        self.mw.TEST_HOOK = lambda job: job.update(test_sleep=120)
        threading.Timer(2.0, self.mw.cancel).start()
        with self.assertRaises(sim.StudyError) as ctx:
            sim.mesh_study(an)
        self.assertIn("cancelled", str(ctx.exception))
        self.assertClean()

    def solve_cantilever(self, mesher):
        """The cantilever clamped at x = 0 and loaded by 1 MPa on its top face, meshed by ``mesher`` and solved; max displacement."""
        import Part

        close_all()
        doc = App.newDocument("MG")
        solid = Part.makeBox(60, 10, 10)
        body = vc.shape_object(doc, "B", solid)
        objs = sim.new_study(doc, sim.StudyPlan(name="G", model=body, material=vc.material(), max_size=3.0, second_order=True))
        an = objs["analysis"]
        an.Mesher = mesher
        vc.add_displacement(doc, an, body, vc.faces_where(solid, vc.plane_at(0, 0.0)), "xyz")
        vc.add_pressure(doc, an, body, vc.faces_where(solid, vc.plane_at(2, 10.0)), 1.0)
        doc.recompute()
        said = []
        sim.mesh_study(an, said.append)  # the recompute above meshed in-process; this meshes with the study's mesher
        self.assertEqual(any("Gmsh" in t and "Try 1" in t for t in said), mesher == "Gmsh", said)
        tmp = tempfile.mkdtemp(prefix="bw-mg-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fea = sim.prepare_solve(an, tmp)
        self.assertEqual(fea.ccx_run(), 0)
        result = sim.finish_solve(fea)
        stats = sim.mesh_stats(sim.members(an)["mesh"])
        return sim.result_extrema(result)["max_disp"], stats

    def test_gmsh_makes_the_same_second_order_cantilever_mesh(self):
        if not self.mw.gmsh_binary():
            self.skipTest("no gmsh program")
        an, mesh = self.study(True)
        shape = sim.shape_of(mesh.Shape)
        fem, info = self.mw.run_worker(shape, self.mw.netgen_settings(mesh, sim.NETGEN_COPY_PROPS, True), 120.0, "G", kind="gmsh")
        self.assertEqual(info["status"], "ok", info)
        self.assertEqual(len(fem.getElementNodes(next(iter(fem.Volumes)))), 10)
        self.assertAlmostEqual(fem.Volume.Value, 6000.0, places=3)
        # the mid-edge nodes sit at the edge midpoints in FEM's order (1-2, 2-3, 3-1, 1-4, 2-4, 3-4)
        for vid in list(fem.Volumes)[:50]:
            ids = list(fem.getElementNodes(vid))
            pts = [fem.Nodes[i] for i in ids]
            for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))):
                self.assertLess((pts[4 + k] - (pts[a] + pts[b]) * 0.5).Length, 1e-6)
        self.assertClean()

    def test_gmsh_and_netgen_cantilever_displacements_agree(self):
        if not self.mw.gmsh_binary() or not sim.find_ccx():
            self.skipTest("no gmsh or CalculiX")
        netgen, n_stats = self.solve_cantilever("Netgen")
        gmsh, g_stats = self.solve_cantilever("Gmsh")
        self.assertEqual(g_stats["order"], "second")
        self.assertNotEqual(n_stats["nodes"], g_stats["nodes"])  # two different meshes
        self.assertAlmostEqual(gmsh / netgen, 1.0, delta=0.02, msg=f"Netgen {netgen}, Gmsh {gmsh}")

    def test_a_recompute_after_a_worker_mesh_does_not_run_netgen(self):
        an, mesh = self.study(True, limit=60.0)
        sim.mesh_study(an)
        self.assertNotIn("Touched", mesh.State)
        nodes = mesh.FemMesh.NodeCount
        mesh.Document.recompute()
        self.assertEqual(mesh.FemMesh.NodeCount, nodes)

    def test_a_study_set_to_gmsh_meshes_with_gmsh_first(self):
        if not self.mw.gmsh_binary():
            self.skipTest("no gmsh program")
        an, mesh = self.study(True)
        an.Mesher = "Gmsh"
        said = []
        self.assertTrue(sim._use_worker(mesh, an))
        sim.mesh_study(an, said.append)
        self.assertTrue(any("Try 1: Gmsh" in t for t in said), said)
        self.assertClean()

    def test_netgen_failing_falls_through_to_gmsh(self):
        if not self.mw.gmsh_binary():
            self.skipTest("no gmsh program")
        an, mesh = self.study(True, limit=3.0)
        self.mw.TEST_HOOK = lambda job: job.update(test_sleep=120) if job["kind"] == "netgen" else None
        stats = sim.mesh_study(an)
        self.assertEqual(stats["order"], "second")
        self.assertTrue(any("Try 3: Gmsh" in t for t in stats["notes"]), stats["notes"])
        self.assertClean()

    def test_the_time_limit_rule(self):
        mw = self.mw
        self.assertEqual(mw.time_limit(None, 1000), 120.0)
        self.assertAlmostEqual(mw.time_limit(None, 514000), 359.8, places=1)
        self.assertEqual(mw.time_limit(None, 10 ** 9), 3600.0)
        an, mesh = self.study(False, limit=42.0)
        self.assertEqual(mw.time_limit(an, 10 ** 6), 42.0)

    def test_the_watchdog_kills_a_worker_whose_parent_is_gone(self):
        import subprocess
        import time

        parent = subprocess.Popen(["sleep", "1"])
        child = subprocess.Popen(["sleep", "300"], start_new_session=True)
        dog = subprocess.Popen(["/bin/sh", "-c", self.mw._WATCHDOG, "sh", str(parent.pid), str(child.pid)], start_new_session=True)
        try:
            parent.wait()
            for _ in range(80):
                if child.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(child.poll(), "the worker outlived its parent")
        finally:
            for p in (child, dog):
                if p.poll() is None:
                    p.kill()
                p.wait()

    def test_a_small_model_meshes_in_this_process(self):
        an, mesh = self.study(False)
        self.assertFalse(sim._use_worker(mesh, an))


class TestMaterialSetsOutsideSolids(unittest.TestCase):
    """SIM-04: an element with a node off every solid (a curved mid-edge node at a thread) still gets a material."""

    def test_stray_element_goes_to_the_nearest_solid(self):
        import ObjectsFem
        import Part
        from femmesh import meshtools

        doc = App.newDocument("BW_MatSets")
        try:
            feature = doc.addObject("Part::Feature", "Two")
            from BOPTools import SplitAPI

            feature.Shape = SplitAPI.booleanFragments(
                [Part.makeBox(10, 10, 10), Part.makeBox(10, 10, 10, App.Vector(10, 0, 0))], "CompSolid", 1e-4
            )
            left = [i + 1 for i, sol in enumerate(feature.Shape.Solids) if sol.CenterOfMass.x < 10][0]
            order = [left, 3 - left]  # Solid names: the left box first
            doc.recompute()
            study = sim.new_study(doc, sim.StudyPlan(model=feature))
            sim.mesh_study(study["analysis"])
            mesh = study["mesh"]
            fem = mesh.FemMesh
            self.assertGreater(fem.VolumeCount, 20)
            # a mid-edge node on the outer face x = 0 pushed 0.5 mm away from the solid: its elements lie in no solid
            stray = [nid for nid, v in fem.Nodes.items() if abs(v.x) < 1e-9 and 0 < v.y < 10 and 0 < v.z < 10]
            nid = stray[0]
            v = fem.Nodes[nid]
            moved = sim.rebuild_mesh(fem, drop_unused=False, moved={nid: (v.x - 0.5, v.y, v.z)})
            objs = []
            for k in order:
                mat = ObjectsFem.makeMaterialSolid(doc, f"Mat{k}")
                mat.References = [(feature, f"Solid{k}")]
                objs.append({"Object": mat})
            table = meshtools.get_femelement_table(moved)
            nodes_table = meshtools.get_femnodes_ele_table(moved.Nodes, table)
            meshtools.LAST_UNREFERENCED = {}
            self.assertTrue(meshtools.get_femelement_sets(moved, table, objs, nodes_table))
            assigned = set(objs[0]["FEMElements"]) | set(objs[1]["FEMElements"])
            self.assertEqual(assigned, set(table))
            self.assertEqual(len(objs[0]["FEMElements"]) + len(objs[1]["FEMElements"]), len(table))  # none twice
            left = meshtools.LAST_UNREFERENCED
            self.assertGreaterEqual(left["count"], 1)
            for eid in left["elements"]:  # the stray ones are in the left box
                self.assertIn(eid, objs[0]["FEMElements"])
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
