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
        import ObjectsFem

        hold = ObjectsFem.makeConstraintFixed(doc, "Bore")
        hold.References = [(body, vc.faces_where(solid, vc.cylinder_of(8.0))[0])]
        an.addObject(hold)

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


if __name__ == "__main__":
    unittest.main()
