# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Model Builder's model layer (SIM-07 P1), headless: tree, settings, fingerprints and states."""

import os
import tempfile
import unittest

import FreeCAD as App
import ObjectsFem

import bruceworx_sim as sim
import bruceworx_sim_model as model

STEEL = {"uuid": "", "name": "Steel (test)", "library": "", "E": 210000.0, "nu": 0.3, "yield": 355.0, "uts": None,
         "density": 7850.0}


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


def face_at(shape, axis, maximum=True):
    """The name of the planar face of ``shape`` furthest along ``axis`` (0, 1, 2)."""
    best = None
    for i, face in enumerate(shape.Faces, 1):
        c = face.CenterOfMass[axis]
        if best is None or (c > best[0] if maximum else c < best[0]):
            best = (c, f"Face{i}")
    return best[1]


def plate_study(meshed=True):
    """A 100 x 50 x 5 mm plate with a 10 mm hole: fixed on the left, a tip force on the right, steel."""
    doc = App.newDocument("PlateModel")
    box = doc.addObject("Part::Box", "Plate")
    box.Length, box.Width, box.Height = 100, 50, 5
    hole = doc.addObject("Part::Cylinder", "Hole")
    hole.Radius, hole.Height = 5, 5
    hole.Placement.Base = App.Vector(50, 25, 0)
    cut = doc.addObject("Part::Cut", "PlateWithHole")
    cut.Base, cut.Tool = box, hole
    doc.recompute()
    shape = cut.Shape
    plan = sim.StudyPlan(name="Plate", model=cut, material=dict(STEEL), max_size=8.0)
    objs = sim.new_study(doc, plan)
    analysis = objs["analysis"]
    fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
    fixed.References = [(cut, face_at(shape, 0, False))]
    analysis.addObject(fixed)
    force = ObjectsFem.makeConstraintForce(doc, "Force")
    force.References = [(cut, face_at(shape, 0, True))]
    force.Force = "500 N"
    force.Direction = (cut, [face_at(shape, 2, True)])
    force.Reversed = True
    analysis.addObject(force)
    doc.recompute()
    if meshed:
        sim.mesh_study(analysis)
    return doc, analysis, cut, shape


def fake_result(analysis):
    result = ObjectsFem.makeResultMechanical(analysis.Document, "Results")
    analysis.addObject(result)
    return result


def node(analysis, key):
    return model.find_node(analysis, key)


class TestTree(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=False)

    def tearDown(self):
        close_all()

    def test_sections_in_solving_order(self):
        tree = model.model_tree(self.analysis)
        self.assertEqual(tree.kind, "study")
        self.assertEqual([c.key for c in tree.children], ["defs", "geo", "mat", "phys", "mesh", "step", "results"])
        self.assertEqual([c.label for c in tree.children],
                         ["Global Definitions", "Geometry", "Materials", "Physics", "Mesh", "Study", "Results"])
        self.assertEqual([c.key for c in tree.find("defs").children], ["defs/params", "defs/sels"])
        self.assertEqual([c.key for c in tree.find("phys").children], ["phys/Fixed", "phys/Force"])
        self.assertEqual(tree.find("phys/Force").icon, "i-force")
        self.assertEqual(tree.find("phys/Fixed").icon, "i-fixed")
        self.assertEqual(tree.find("geo/part").label, self.cut.Label)
        self.assertEqual([c.key for c in tree.find("step").children], ["step/main", "step/solver"])
        self.assertEqual(tree.find("step/main").label, "Stationary")
        keys = [n.key for n in tree.walk()]
        self.assertEqual(len(keys), len(set(keys)))
        plots = [c.key for c in tree.find("results").children]
        self.assertIn("results/stress", plots)
        self.assertIn("results/safety", plots)

    def test_building_the_tree_changes_nothing(self):
        before = ([o.Name for o in self.doc.Objects], list(self.analysis.PropertiesList))
        model.model_tree(self.analysis)
        model.state(self.analysis)
        model.node_messages(self.analysis)
        model.fingerprints(self.analysis)
        self.assertEqual(before, ([o.Name for o in self.doc.Objects], list(self.analysis.PropertiesList)))
        self.assertNotIn(model.FINGERPRINT_PROP, self.analysis.PropertiesList)

    def test_modal_and_thermal_trees(self):
        doc = App.newDocument("Kinds")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        modal = sim.new_study(doc, sim.StudyPlan(name="M", study_type="modal", model=box, material=dict(STEEL)))["analysis"]
        self.assertEqual(node(modal, "step/main").label, "Eigenfrequency")
        self.assertIn("results/modes", [n.key for n in model.model_tree(modal).walk()])
        thermal = sim.new_study(doc, sim.StudyPlan(name="T", study_type="thermal", model=box, material=dict(STEEL)))["analysis"]
        tree = model.model_tree(thermal)
        self.assertIn("results/temperature", [n.key for n in tree.walk()])
        self.assertIn("phys/InitialTemperature", [n.key for n in tree.walk()])

    def test_airflow_tree(self):
        doc = App.newDocument("Air")
        box = doc.addObject("Part::Box", "Body")
        doc.recompute()
        import bruceworx_cfd as cfd

        analysis = cfd.new_airflow_study(doc, box, "wind")
        before = list(analysis.PropertiesList)
        tree = model.model_tree(analysis)
        self.assertEqual(list(analysis.PropertiesList), before)
        self.assertEqual([c.key for c in tree.find("phys").children], ["phys/farfield", "phys/walls", "phys/turbulence"])
        self.assertEqual(tree.find("step/main").label, "Steady CFD")
        self.assertEqual(tree.find("step/solver").label, "OpenFOAM")
        self.assertEqual(tree.find("mesh/main").kind, "mesh")
        self.assertIn("results/forces", [n.key for n in tree.walk()])
        wash = cfd.new_airflow_study(doc, box, "wash", kind="prop_wash")
        self.assertIn("phys/disks", [n.key for n in model.model_tree(wash).walk()])


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=False)
        self.doc.UndoMode = 1

    def tearDown(self):
        close_all()

    def test_force_fields_and_units(self):
        n = node(self.analysis, "phys/Force")
        names = [f.name for s in model.settings(n) for f in s.fields]
        self.assertEqual(names, ["References", "Force", "Direction", "Reversed"])
        self.assertAlmostEqual(model.get_value(n, "Force"), 500.0)
        model.set_value(n, "Force", "2 kN")
        self.assertAlmostEqual(model.get_value(n, "Force"), 2000.0)
        model.set_value(n, "Force", 750)
        self.assertAlmostEqual(model.get_value(n, "Force"), 750.0)
        with self.assertRaises(ValueError) as ctx:
            model.set_value(n, "Force", "2 mm")
        self.assertIn("expected a value in N", str(ctx.exception))
        with self.assertRaises(ValueError):
            model.set_value(n, "Force", "lots")
        self.assertAlmostEqual(model.get_value(n, "Force"), 750.0)
        model.set_value(n, "Reversed", False)
        self.assertFalse(model.get_value(n, "Reversed"))

    def test_one_transaction_and_undo(self):
        n = node(self.analysis, "phys/Force")
        model.set_value(n, "Force", "900 N")
        self.assertAlmostEqual(model.get_value(n, "Force"), 900.0)
        self.doc.undo()
        self.assertAlmostEqual(model.get_value(n, "Force"), 500.0)

    def test_selection_roundtrip(self):
        n = node(self.analysis, "phys/Force")
        refs = model.get_value(n, "References")
        self.assertEqual(len(refs), 1)
        other = face_at(self.shape, 1, True)
        model.set_value(n, "References", [(self.cut.Name, other)])
        self.assertEqual(model.get_value(n, "References"), [(self.cut.Name, other)])
        with self.assertRaises(ValueError):
            model.set_value(n, "References", [(self.cut.Name, "Face99")])
        self.assertEqual(model.get_value(n, "Direction")[0][0], self.cut.Name)

    def test_mesh_fields_never_remesh(self):
        n = node(self.analysis, "mesh/main")
        nodes_before = sim.mesh_stats(sim.members(self.analysis)["mesh"])["nodes"]
        names = [f.name for s in model.settings(n) for f in s.fields]
        self.assertEqual(names[:3], ["MaxSize", "Fineness", "SecondOrder"])
        model.set_value(n, "MaxSize", "2 mm")
        self.assertAlmostEqual(model.get_value(n, "MaxSize"), 2.0)
        model.set_value(n, "MaxSize", "1 cm")
        self.assertAlmostEqual(model.get_value(n, "MaxSize"), 10.0)
        model.set_value(n, "Fineness", "Fine")
        self.assertEqual(model.get_value(n, "Fineness"), "Fine")
        with self.assertRaises(ValueError):
            model.set_value(n, "Fineness", "Gigantic")
        with self.assertRaises(ValueError):
            model.set_value(n, "MaxSize", "0 mm")
        model.set_value(n, "SecondOrder", False)
        self.assertFalse(self.analysis.Document.getObject(n.obj).SecondOrder)
        self.assertEqual(sim.mesh_stats(sim.members(self.analysis)["mesh"])["nodes"], nodes_before)  # not re-meshed

    def test_material_card(self):
        n = node(self.analysis, "mat/" + sim.members(self.analysis)["materials"][0].Name)
        sections = {s.title: s for s in model.settings(n)}
        card_field = [f for f in sections["Material"].fields if f.name == "card"][0]
        self.assertGreater(len(card_field.choices), 10)
        pick = next(c for c in card_field.choices if "Steel" in c)
        model.set_value(n, "card", pick)
        self.assertEqual(model.get_value(n, "card"), pick)
        self.assertGreater(model.get_value(n, "E"), 1000)
        with self.assertRaises(ValueError):
            model.set_value(n, "card", "No such material")
        with self.assertRaises(ValueError):
            model.set_value(n, "E", 1)  # read only

    def test_study_and_solver_nodes(self):
        n = node(self.analysis, "study")
        self.assertEqual(model.get_value(n, "type"), "Static stress")
        model.set_value(n, "Label", "Renamed")
        self.assertEqual(self.analysis.Label, "Study: Renamed")
        model.set_value(n, "Knockdown", 0.5)
        self.assertAlmostEqual(self.analysis.Knockdown, 0.5)
        model.set_value(n, "StressMeasure", "Tresca")
        self.assertEqual(sim.study_measure(self.analysis), "Tresca")
        solver = node(self.analysis, "step/solver")
        names = [f.name for s in model.settings(solver) for f in s.fields]
        self.assertIn("GeometricalNonlinearity", names)
        model.set_value(solver, "GeometricalNonlinearity", "nonlinear")
        self.assertEqual(model.get_value(solver, "GeometricalNonlinearity"), "nonlinear")

    def test_spin_hold_and_thermal_units(self):
        import bruceworx_sim_post as post

        spin = post.add_spin(self.analysis, 1000, "z")
        n = node(self.analysis, "phys/" + spin.Name)
        self.assertEqual(n.kind, "spin")
        self.assertAlmostEqual(model.get_value(n, "rpm"), 1000.0, places=6)
        model.set_value(n, "rpm", "12000 rpm")
        self.assertAlmostEqual(model.get_value(n, "rpm"), 12000.0, places=6)
        self.assertIn("12000", spin.Label)
        model.set_value(n, "rpm", "200 1/s")
        self.assertAlmostEqual(model.get_value(n, "rpm"), 12000.0, places=6)
        with self.assertRaises(ValueError):
            model.set_value(n, "rpm", "5 N")
        import bruceworx_sim_thermal as thermal

        temp = thermal.add_temperature(self.analysis, [(self.cut, face_at(self.shape, 0, False))], 80)
        t = node(self.analysis, "phys/" + temp.Name)
        self.assertAlmostEqual(model.get_value(t, "Temperature"), 80.0)
        model.set_value(t, "Temperature", "300 K")
        self.assertAlmostEqual(model.get_value(t, "Temperature"), 26.85, places=6)
        model.set_value(t, "Temperature", "-5 degC")
        self.assertAlmostEqual(model.get_value(t, "Temperature"), -5.0, places=6)
        conv = thermal.add_convection(self.analysis, [(self.cut, face_at(self.shape, 1, True))], 25.0)
        c = node(self.analysis, "phys/" + conv.Name)
        self.assertEqual(c.kind, "convection")
        model.set_value(c, "FilmCoef", "40 W/m^2/K")
        self.assertAlmostEqual(model.get_value(c, "FilmCoef"), 40.0, places=6)

    def test_step_fields_by_study_type(self):
        doc = App.newDocument("Steps")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        modal = sim.new_study(doc, sim.StudyPlan(name="M", study_type="modal", model=box, material=dict(STEEL)))["analysis"]
        n = node(modal, "step/main")
        names = [f.name for s in model.settings(n) for f in s.fields]
        self.assertIn("ModeCount", names)
        self.assertNotIn("Transient", names)
        model.set_value(n, "ModeCount", 12)
        self.assertEqual(model.get_value(n, "ModeCount"), 12)
        with self.assertRaises(ValueError):
            model.set_value(n, "ModeCount", 2.5)
        model.set_value(n, "MotorRpmMax", "9000 rpm")
        self.assertAlmostEqual(model.get_value(n, "MotorRpmMax"), 9000.0)
        static = node(self.analysis, "step/main")
        self.assertEqual([f.name for s in model.settings(static) for f in s.fields], ["step"])

    def test_airflow_fields(self):
        doc = App.newDocument("AirSet")
        box = doc.addObject("Part::Box", "Body")
        doc.recompute()
        import bruceworx_cfd as cfd

        wash = cfd.new_airflow_study(doc, box, "wash", kind="prop_wash")
        far = node(wash, "phys/farfield")
        names = [f.name for s in model.settings(far) for f in s.fields]
        self.assertEqual(names[:2], ["FlowSpeed", "AngleOfAttack"])
        self.assertIn("DomainWake", names)
        model.set_value(far, "FlowSpeed", "3 m/s")
        self.assertAlmostEqual(wash.FlowSpeed, 3.0)
        model.set_value(far, "FarField", "symmetry")
        self.assertEqual(wash.FarField, "symmetry")
        with self.assertRaises(ValueError):
            model.set_value(far, "FlowSpeed", "3 kg")
        with self.assertRaises(ValueError):
            model.set_value(far, "AngleOfAttack", "120 deg")
        model.set_value(node(wash, "mat/air"), "AirTemperature", "5 degC")
        self.assertAlmostEqual(wash.AirTemperature, 5.0)
        model.set_value(node(wash, "mesh/main"), "MeshPreset", "fine")
        self.assertEqual(wash.MeshPreset, "fine")
        model.set_value(node(wash, "phys/turbulence"), "Turbulence", "laminar")
        self.assertEqual(wash.Turbulence, "laminar")
        model.set_value(node(wash, "phys/disks"), "PropDiameter", "127 mm")
        self.assertAlmostEqual(wash.PropDiameter, 127.0)
        ext = cfd.new_airflow_study(doc, box, "ext")
        ext_names = [f.name for s in model.settings(node(ext, "phys/farfield")) for f in s.fields]
        self.assertNotIn("DomainWake", ext_names)
        walls = [f.name for s in model.settings(node(ext, "phys/walls")) for f in s.fields]
        self.assertIn("ReferenceArea", walls)
        self.assertNotIn("ReferenceArea", [f.name for s in model.settings(node(wash, "phys/walls")) for f in s.fields])


class TestState(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=True)

    def tearDown(self):
        close_all()

    def finish(self):
        fake_result(self.analysis)
        model.record(self.analysis, "solution")

    def marks(self):
        return model.state(self.analysis, precheck=False)

    def test_mesh_hook_records_and_states(self):
        self.assertIn(model.FINGERPRINT_PROP, self.analysis.PropertiesList)
        self.assertIn("mesh", model.stored_fingerprints(self.analysis))
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "ok")
        self.assertEqual(marks["step/main"], "todo")  # nothing solved yet
        self.finish()
        marks = self.marks()
        for key in ("mesh/main", "step/main", "results", "results/stress", "phys/Force", "geo/part"):
            self.assertEqual(marks[key], "ok", key)

    def test_changing_a_force_face_marks_solution_and_results_only(self):
        self.finish()
        n = node(self.analysis, "phys/Force")
        model.set_value(n, "References", [(self.cut.Name, face_at(self.shape, 1, True))])
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "ok")  # no local refinement in P1
        self.assertEqual(marks["step/main"], "stale")
        self.assertEqual(marks["results"], "stale")
        self.assertEqual(marks["results/stress"], "stale")
        for key in ("geo/part", "geo", "mat", "phys/Fixed", "defs"):
            self.assertIn(marks.get(key, "ok"), ("ok",), key)
        self.assertEqual(marks["study"], "stale")

    def test_changing_the_material_marks_solution_not_mesh(self):
        self.finish()
        mat = sim.members(self.analysis)["materials"][0]
        n = node(self.analysis, "mat/" + mat.Name)
        pick = next(c for c in [f for s in model.settings(n) for f in s.fields if f.name == "card"][0].choices if "Steel" in c)
        model.set_value(n, "card", pick)
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "ok")
        self.assertEqual(marks["step/main"], "stale")
        self.assertEqual(marks["results/stress"], "stale")
        self.assertEqual(marks["geo/part"], "ok")

    def test_mesh_settings_mark_mesh_and_everything_after(self):
        self.finish()
        model.set_value(node(self.analysis, "mesh/main"), "MaxSize", "6 mm")
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "stale")
        self.assertEqual(marks["step/main"], "stale")
        self.assertEqual(marks["phys/Force"], "ok")

    def test_display_settings_do_not_invalidate(self):
        self.finish()
        model.set_value(node(self.analysis, "study"), "StressMeasure", "Tresca")
        self.analysis.Label = "Study: other name"
        self.assertEqual(self.marks()["step/main"], "ok")

    def test_geometry_change_marks_the_mesh(self):
        self.finish()
        self.doc.Plate.Length = 120
        self.doc.recompute()
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "stale")
        self.assertEqual(marks["step/main"], "stale")

    def test_remesh_makes_old_results_stale(self):
        self.finish()
        sim.mesh_study(self.analysis)
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "ok")
        self.assertEqual(marks["step/main"], "ok")  # same mesh settings: the fingerprint is unchanged
        # a study without stored fingerprints (made before today) that is re-meshed: the old results go stale
        self.analysis.BW_Fingerprints = {}
        model.record(self.analysis, "mesh")
        self.assertEqual(self.marks()["step/main"], "stale")

    def test_legacy_study_is_up_to_date(self):
        self.finish()
        self.analysis.BW_Fingerprints = {}
        marks = self.marks()
        self.assertEqual(marks["mesh/main"], "ok")
        self.assertEqual(marks["step/main"], "ok")
        self.analysis.removeProperty(model.FINGERPRINT_PROP)
        self.assertEqual(self.marks()["results"], "ok")

    def test_fingerprints_survive_save_and_load(self):
        self.finish()
        before = model.fingerprints(self.analysis)
        path = os.path.join(tempfile.mkdtemp(), "plate.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        analysis = [o for o in doc.Objects if o.TypeId == "Fem::FemAnalysis"][0]
        self.assertEqual(model.fingerprints(analysis), before)
        marks = model.state(analysis, precheck=False)
        self.assertEqual(marks["step/main"], "ok")
        self.assertEqual(marks["mesh/main"], "ok")

    def test_running_mark(self):
        self.finish()
        model.set_running(self.analysis, "solution", True)
        self.assertEqual(self.marks()["step/main"], "run")
        self.assertEqual(self.marks()["study"], "run")
        model.set_running(self.analysis, "solution", False)
        self.assertEqual(self.marks()["step/main"], "ok")

    def test_todo_and_warn_marks_and_messages(self):
        force = node(self.analysis, "phys/Force")
        model.set_value(force, "References", [])
        marks = model.state(self.analysis, precheck=False)
        self.assertEqual(marks["phys/Force"], "todo")
        self.assertEqual(marks["phys"], "todo")
        msgs = model.node_messages(self.analysis)
        self.assertEqual(msgs["phys/Force"][0][0], "error")
        self.assertIn("no faces", msgs["phys/Force"][0][1])
        self.assertEqual(model.state(self.analysis)["phys/Force"], "todo")
        fixed = sim.members(self.analysis)["supports"][0]
        self.analysis.removeObject(fixed)
        msgs = model.node_messages(self.analysis)
        self.assertTrue(any("support" in t for _l, t in msgs.get("phys", [])))
        self.assertEqual(model.state(self.analysis)["phys"], "todo")

    def test_no_material_is_a_todo_on_the_section(self):
        for mat in sim.members(self.analysis)["materials"]:
            self.analysis.removeObject(mat)
        msgs = model.node_messages(self.analysis)
        self.assertIn("mat", msgs)
        self.assertEqual(model.state(self.analysis)["mat"], "todo")

    def test_airflow_state_follows_the_solution(self):
        doc = App.newDocument("AirState")
        box = doc.addObject("Part::Box", "Body")
        doc.recompute()
        import bruceworx_cfd as cfd

        analysis = cfd.new_airflow_study(doc, box, "wind")
        marks = model.state(analysis, precheck=False)
        self.assertEqual(marks["step/main"], "todo")
        analysis.CfdSummary = "{}"
        model.record(analysis, "solution")
        marks = model.state(analysis, precheck=False)
        self.assertEqual((marks["step/main"], marks["mesh/main"]), ("ok", "ok"))
        model.set_value(node(analysis, "phys/farfield"), "FlowSpeed", "20 m/s")
        marks = model.state(analysis, precheck=False)
        self.assertEqual((marks["step/main"], marks["mesh/main"], marks["results/forces"]), ("stale", "stale", "stale"))
        # the colour range of the pressure plot is display only
        model.record(analysis, "solution")
        model.set_value(node(analysis, "results/pressure"), "PressureMax", "50 Pa")
        self.assertEqual(model.state(analysis, precheck=False)["step/main"], "ok")


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestRealSolve(unittest.TestCase):
    def tearDown(self):
        close_all()

    def test_solve_records_and_a_load_edit_marks_stale(self):
        doc, analysis, cut, shape = plate_study(meshed=False)
        sim.solve(analysis, working_dir=tempfile.mkdtemp())
        marks = model.state(analysis, precheck=False)
        for key in ("mesh/main", "step/main", "results", "results/stress"):
            self.assertEqual(marks[key], "ok", key)
        model.set_value(node(analysis, "phys/Force"), "Force", "100 N")
        marks = model.state(analysis, precheck=False)
        self.assertEqual((marks["mesh/main"], marks["step/main"], marks["results/stress"]), ("ok", "stale", "stale"))
