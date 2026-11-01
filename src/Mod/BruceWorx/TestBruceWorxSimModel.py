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
        self.assertEqual(names, ["NamedSelection", "References", "Force", "Direction", "Reversed"])
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


# ----- SIM-07 P2: parameters, named selections, local mesh sizes --------------------------------------------------

import bruceworx_sim_defs as defs  # noqa: E402


class TestParameters(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=True)
        self.doc.UndoMode = 1
        fake_result(self.analysis)
        model.record(self.analysis, "solution")
        self.force = node(self.analysis, "phys/Force")

    def tearDown(self):
        close_all()

    def marks(self):
        return model.state(self.analysis, precheck=False)

    def test_table_units_and_expressions(self):
        defs.add_parameter(self.analysis, "t", "3 mm", "mm", "wall thickness")
        defs.add_parameter(self.analysis, "F_load", "2000 N", "N")
        p = defs.add_parameter(self.analysis, "w", "2*t+1 mm", "mm", "derived")
        self.assertAlmostEqual(p.value, 7.0)
        self.assertEqual(p.expression, "2*t+1 mm")
        self.assertEqual([x.name for x in defs.parameters(self.analysis)], ["t", "F_load", "w"])
        defs.add_parameter(self.analysis, "n_legs", "4", "")
        defs.add_parameter(self.analysis, "T_hot", "80", "degC")
        self.assertEqual(defs.parameters(self.analysis)[-1].value, 80.0)
        defs.edit_parameter(self.analysis, "t", "4 mm")
        self.assertAlmostEqual(next(x for x in defs.parameters(self.analysis) if x.name == "w").value, 9.0)  # re-evaluated

    def test_fields_take_expressions_and_follow_parameters(self):
        defs.add_parameter(self.analysis, "F_load", "2000 N", "N")
        defs.add_parameter(self.analysis, "n_legs", "4", "")
        model.set_value(self.force, "Force", "F_load/n_legs")
        self.assertAlmostEqual(model.get_value(self.force, "Force"), 500.0)
        self.assertEqual(model.get_expression(self.force, "Force"), "F_load/n_legs")
        self.assertAlmostEqual(self.doc.getObject("Force").Force.getValueAs("N"), 500.0)  # the object holds the value
        defs.edit_parameter(self.analysis, "F_load", "4000 N")
        self.assertAlmostEqual(model.get_value(self.force, "Force"), 1000.0)
        # a plain value takes the field off the parameter again
        model.set_value(self.force, "Force", "300 N")
        self.assertEqual(model.get_expression(self.force, "Force"), "")
        defs.edit_parameter(self.analysis, "F_load", "1000 N")
        self.assertAlmostEqual(model.get_value(self.force, "Force"), 300.0)

    def test_stale_marks_follow_the_fields_that_use_a_parameter(self):
        defs.add_parameter(self.analysis, "F_load", "2000 N", "N")
        defs.add_parameter(self.analysis, "h", "6 mm", "mm")
        model.set_value(self.force, "Force", "F_load/4")
        model.record(self.analysis, "solution")
        self.assertEqual(self.marks()["step/main"], "ok")
        defs.edit_parameter(self.analysis, "F_load", "3000 N")
        marks = self.marks()
        self.assertEqual((marks["mesh/main"], marks["step/main"], marks["phys/Force"]), ("ok", "stale", "ok"))
        # a mesh size parameter marks the mesh (and so the solution), and never re-meshes
        model.set_value(node(self.analysis, "mesh/main"), "MaxSize", "h")
        model.record(self.analysis, "mesh")
        model.record(self.analysis, "solution")
        nodes = sim.members(self.analysis)["mesh"].FemMesh.NodeCount
        defs.edit_parameter(self.analysis, "h", "5 mm")
        marks = self.marks()
        self.assertEqual((marks["mesh/main"], marks["step/main"]), ("stale", "stale"))
        self.assertEqual(sim.members(self.analysis)["mesh"].FemMesh.NodeCount, nodes)
        self.assertAlmostEqual(model.get_value(node(self.analysis, "mesh/main"), "MaxSize"), 5.0)

    def test_errors_are_clear_and_change_nothing(self):
        defs.add_parameter(self.analysis, "t", "3 mm", "mm")
        defs.add_parameter(self.analysis, "w", "2*t", "mm")
        with self.assertRaises(ValueError) as ctx:
            model.set_value(self.force, "Force", "nothing*2")
        self.assertIn("unknown name 'nothing'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            model.set_value(self.force, "Force", "t")  # a length for a force
        self.assertIn("N", str(ctx.exception))
        self.assertAlmostEqual(model.get_value(self.force, "Force"), 500.0)
        with self.assertRaises(defs.DefsError) as ctx:
            defs.edit_parameter(self.analysis, "t", "w")  # t = w = 2*t: a cycle
        self.assertIn("cycle", str(ctx.exception))
        self.assertAlmostEqual(next(x for x in defs.parameters(self.analysis) if x.name == "t").value, 3.0)
        with self.assertRaises(defs.DefsError):
            defs.add_parameter(self.analysis, "2x", "1", "")  # not a name
        with self.assertRaises(defs.DefsError):
            defs.add_parameter(self.analysis, "x", "1 N", "mm")  # wrong unit for its column
        with self.assertRaises(defs.DefsError):
            defs.add_parameter(self.analysis, "t", "1 mm", "mm")  # exists
        with self.assertRaises(defs.DefsError):
            defs.add_parameter(self.analysis, "y", "bogus+1", "")
        self.assertEqual([x.name for x in defs.parameters(self.analysis)], ["t", "w"])

    def test_used_parameter_cannot_be_removed_and_undo_works(self):
        defs.add_parameter(self.analysis, "F_load", "2000 N", "N")
        model.set_value(self.force, "Force", "F_load/4")
        with self.assertRaises(defs.DefsError) as ctx:
            defs.remove_parameter(self.analysis, "F_load")
        self.assertIn("Force", str(ctx.exception))
        model.set_value(self.force, "Force", "100 N")
        defs.edit_parameter(self.analysis, "F_load", "3000 N")
        self.doc.undo()
        self.assertAlmostEqual(next(x for x in defs.parameters(self.analysis)).value, 2000.0)
        defs.remove_parameter(self.analysis, "F_load")
        self.assertEqual(defs.parameters(self.analysis), [])

    def test_survives_save_and_load(self):
        defs.add_parameter(self.analysis, "F_load", "2000 N", "N")
        model.set_value(self.force, "Force", "F_load/4")
        path = os.path.join(tempfile.mkdtemp(), "p.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        analysis = sim.studies(doc)[0]
        self.assertEqual([p.name for p in defs.parameters(analysis)], ["F_load"])
        n = model.find_node(analysis, "phys/Force")
        self.assertEqual(model.get_expression(n, "Force"), "F_load/4")
        defs.edit_parameter(analysis, "F_load", "8000 N")
        self.assertAlmostEqual(model.get_value(n, "Force"), 2000.0)


class TestNamedSelections(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=True)
        fake_result(self.analysis)
        model.record(self.analysis, "solution")
        self.right = face_at(self.shape, 0, True)
        self.left = face_at(self.shape, 0, False)

    def tearDown(self):
        close_all()

    def second_load(self):
        pull = ObjectsFem.makeConstraintForce(self.doc, "Pull")
        pull.Force = "50 N"
        pull.Direction = (self.cut, [face_at(self.shape, 2, True)])
        self.analysis.addObject(pull)
        self.doc.recompute()
        return pull

    def test_shared_by_two_loads(self):
        sel = defs.make_selection(self.analysis, "Right end", [(self.cut, [self.right])])
        pull = self.second_load()
        n1, n2 = node(self.analysis, "phys/Force"), node(self.analysis, f"phys/{pull.Name}")
        model.set_value(n1, "NamedSelection", "Right end")
        model.set_value(n2, "NamedSelection", "Right end")
        self.assertEqual(model.get_value(n2, "NamedSelection"), "Right end")
        self.assertEqual(model.get_value(n2, "References"), [(self.cut.Name, self.right)])
        sn = node(self.analysis, f"defs/sel/{sel.Name}")
        self.assertIsNotNone(sn)
        self.assertEqual(model.get_value(sn, "users"), "Force, Pull")
        # edit the selection once: both loads follow, and the solution is stale
        bottom = face_at(self.shape, 1, False)
        model.set_value(sn, "References", [(self.cut.Name, [self.right, bottom])])
        for obj in (self.doc.getObject("Force"), pull):
            self.assertEqual(sorted(s for _o, subs in obj.References for s in subs), sorted([self.right, bottom]))
        marks = model.state(self.analysis, precheck=False)
        self.assertEqual((marks["mesh/main"], marks["step/main"]), ("ok", "stale"))
        # a linked load's own list is read only
        with self.assertRaises(ValueError):
            model.set_value(n1, "References", [(self.cut.Name, [self.left])])
        model.set_value(n1, "NamedSelection", "(own list)")
        model.set_value(n1, "References", [(self.cut.Name, [self.left])])
        self.assertEqual(model.get_value(n2, "References")[0][1], self.right)

    def test_survives_a_recompute_of_the_part(self):
        sel = defs.make_selection(self.analysis, "Right end", [(self.cut, [self.right])])
        model.set_value(node(self.analysis, "phys/Force"), "NamedSelection", "Right end")
        self.doc.getObject("Plate").Length = 120
        self.doc.recompute()
        defs.sync(self.analysis)
        refs = [(o.Name, list(s)) for o, s in sel.References]
        self.assertEqual(refs, [(self.cut.Name, [self.right])])
        face = self.cut.Shape.getElement(self.right)
        self.assertAlmostEqual(face.CenterOfMass.x, 120.0, places=3)  # still the far end, which moved
        self.assertEqual([(o.Name, list(s)) for o, s in self.doc.getObject("Force").References], refs)

    def test_remove_keeps_the_faces(self):
        sel = defs.make_selection(self.analysis, "Right end", [(self.cut, [self.right])])
        model.set_value(node(self.analysis, "phys/Force"), "NamedSelection", "Right end")
        defs.remove_selection(self.analysis, sel)
        self.assertIsNone(defs.linked(self.doc.getObject("Force")))
        self.assertEqual(model.get_value(node(self.analysis, "phys/Force"), "References"), [(self.cut.Name, self.right)])
        self.assertEqual(defs.selections(self.analysis), [])


class TestLocalSize(unittest.TestCase):
    def setUp(self):
        self.doc, self.analysis, self.cut, self.shape = plate_study(meshed=True)
        fake_result(self.analysis)
        model.record(self.analysis, "solution")
        self.hole = next(f"Face{i}" for i, f in enumerate(self.shape.Faces, 1) if f.Surface.__class__.__name__ == "Cylinder")

    def tearDown(self):
        close_all()

    def test_local_size_refines_and_marks_the_mesh(self):
        mesh = sim.members(self.analysis)["mesh"]
        base = mesh.FemMesh.NodeCount

        def near_hole():  # nodes within 8 mm of the hole's axis
            return sum(1 for v in mesh.FemMesh.Nodes.values() if (v.x - 50) ** 2 + (v.y - 25) ** 2 < 64)

        base_near = near_hole()
        marks = model.state(self.analysis, precheck=False)
        self.assertEqual(marks["mesh/main"], "ok")
        ls = defs.make_local_size(self.analysis, "1 mm", [(self.cut, [self.hole])], label="Hole")
        n = node(self.analysis, f"mesh/local/{ls.Name}")
        self.assertEqual(n.kind, "localsize")
        marks = model.state(self.analysis, precheck=False)
        self.assertEqual((marks["mesh/main"], marks["step/main"]), ("stale", "stale"))
        self.assertEqual(marks[n.key], "ok")
        sim.mesh_study(self.analysis)
        self.assertGreater(mesh.FemMesh.NodeCount, 1.25 * base)
        self.assertGreater(near_hole(), 2 * base_near)  # the refinement is at the hole
        App.Console.PrintMessage(f"\n[local size] nodes {base} -> {mesh.FemMesh.NodeCount}; near the hole {base_near} -> {near_hole()}\n")
        self.assertTrue(os.path.isfile(mesh.MeshSizeFile))
        model.record(self.analysis, "solution")
        self.assertEqual(model.state(self.analysis, precheck=False)["mesh/main"], "ok")
        model.set_value(n, "Size", "1.5 mm")  # a new size marks the mesh
        self.assertEqual(model.state(self.analysis, precheck=False)["mesh/main"], "stale")
        model.set_value(n, "Size", "1 mm")
        self.assertEqual(model.state(self.analysis, precheck=False)["mesh/main"], "ok")

    def test_a_loads_named_selection_marks_the_mesh_when_a_local_size_uses_it(self):
        right = face_at(self.shape, 0, True)
        sel = defs.make_selection(self.analysis, "Right end", [(self.cut, [right])])
        model.set_value(node(self.analysis, "phys/Force"), "NamedSelection", "Right end")
        ls = defs.make_local_size(self.analysis, "2 mm", selection=sel)
        sim.mesh_study(self.analysis)
        model.record(self.analysis, "solution")
        self.assertEqual(model.state(self.analysis, precheck=False)["mesh/main"], "ok")
        model.set_value(node(self.analysis, f"defs/sel/{sel.Name}"), "References",
                        [(self.cut.Name, [right, face_at(self.shape, 1, False)])])
        marks = model.state(self.analysis, precheck=False)
        self.assertEqual((marks["mesh/main"], marks["step/main"]), ("stale", "stale"))

    def test_no_local_size_leaves_the_mesh_plain(self):
        sim.mesh_study(self.analysis)
        self.assertEqual(sim.members(self.analysis)["mesh"].MeshSizeFile, "")


# ----- SIM-07 P2: study steps -------------------------------------------------------------------------------------

import bruceworx_sim_eigen as eigen  # noqa: E402
import bruceworx_sim_steps as steps  # noqa: E402
import bruceworx_sim_thermal as thermal  # noqa: E402


class TestSteps(unittest.TestCase):
    def tearDown(self):
        close_all()

    def test_steps_are_derived_from_the_study_type(self):
        doc = App.newDocument("Kinds")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        got = {}
        for kind in ("static", "modal", "buckling", "thermal", "thermal_stress"):
            study = sim.new_study(doc, sim.StudyPlan(name=kind, study_type=kind, model=box, material=dict(STEEL)))["analysis"]
            got[kind] = steps.types(study)
        self.assertEqual(got, {"static": ["stationary"], "modal": ["modal"], "buckling": ["buckling"], "thermal": ["heat"],
                               "thermal_stress": ["heat", "stationary"]})

    def test_tree_shows_the_steps_and_settings_split_by_step(self):
        doc = App.newDocument("TS")
        box = doc.addObject("Part::Box", "B")
        doc.recompute()
        study = sim.new_study(doc, sim.StudyPlan(name="ts", study_type="thermal_stress", model=box, material=dict(STEEL)))["analysis"]
        tree = model.model_tree(study)
        main = tree.find("step/main")
        self.assertEqual(main.label, "Heat transfer + Stationary")
        self.assertEqual([c.key for c in main.children], ["step/s1", "step/s2"])
        heat, stress = main.children
        self.assertIn("InitialTemperature", [f.name for sec in model.settings(heat) for f in sec.fields])
        self.assertIn("ReferenceTemperature", [f.name for sec in model.settings(stress) for f in sec.fields])
        self.assertIn("results/temperature", [n.key for n in tree.walk()])
        self.assertEqual(model.state(study, precheck=False)["step/s2"], model.state(study, precheck=False)["step/main"])

    def test_adding_and_removing_steps_converts_the_study(self):
        doc, analysis, cut, shape = plate_study(meshed=False)
        self.assertEqual(steps.additions(analysis), [("modal", "Eigenfrequency")])
        steps.add_step(analysis, "modal")
        self.assertEqual(sim.study_kind(analysis), "modal")
        self.assertTrue(analysis.Prestressed)
        self.assertEqual([s.label for s in steps.steps(analysis)], ["Preload (stationary)", "Eigenfrequency"])
        self.assertEqual(model.step_label(analysis), "Stationary + Eigenfrequency")
        self.assertEqual(len(sim.members(analysis)["loads"]), 1)  # the loads, supports and mesh are kept
        self.assertEqual(steps.additions(analysis), [])
        with self.assertRaises(steps.StepError) as ctx:
            steps.add_step(analysis, "heat")
        self.assertIn("not supported together", str(ctx.exception))
        steps.remove_step(analysis, "s2")
        self.assertEqual((sim.study_kind(analysis), steps.types(analysis)), ("static", ["stationary"]))
        self.assertEqual(sim.members(analysis)["solver"].AnalysisType, "static")
        with self.assertRaises(steps.StepError):
            steps.remove_step(analysis, "s1")  # the only step
        with self.assertRaises(steps.StepError):
            steps.set_steps(analysis, ["modal", "stationary"])  # the preload comes first
        steps.set_steps(analysis, ["heat", "stationary"])
        self.assertEqual(sim.study_kind(analysis), "thermal_stress")
        self.assertIsNotNone(thermal.initial_temperature_object(analysis))
        steps.set_steps(analysis, ["stationary"])
        self.assertIsNone(thermal.initial_temperature_object(analysis))
        doc.undo() if False else None

    def test_one_undo_step(self):
        doc, analysis, cut, shape = plate_study(meshed=False)
        doc.UndoMode = 1
        steps.add_step(analysis, "modal")
        doc.undo()
        self.assertEqual((sim.study_kind(analysis), steps.types(analysis)), ("static", ["stationary"]))


@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestStepSolves(unittest.TestCase):
    def setUp(self):
        close_all()
        self.addCleanup(close_all)
        self.tmp = tempfile.mkdtemp(prefix="bw-steps-")

    def test_preload_then_modal_matches_the_tensioned_beam(self):
        doc, objs, theory = eigen.tensioned_beam(options={"Prestressed": False, "ModeCount": 4})
        analysis = objs["analysis"]
        self.assertEqual(steps.types(analysis), ["modal"])
        sim.solve(analysis, working_dir=self.tmp)
        plain = eigen.modal_rows(analysis)[0]["frequency"]
        self.assertLess(abs(plain - theory["f1_free"]) / theory["f1_free"], 0.03, (plain, theory))
        steps.set_steps(analysis, ["stationary", "modal"])  # Add step: preload, then eigenfrequency
        sim.solve(analysis, working_dir=self.tmp)
        rows = eigen.modal_rows(analysis)
        f1 = rows[0]["frequency"]
        self.assertLess(abs(f1 - theory["f1"]) / theory["f1"], 0.03, (f1, theory))
        self.assertGreater(f1, 1.5 * plain)  # the 10 kN of tension stiffens the beam
        self.assertIsNotNone(eigen.prestress_result(analysis))
        with open(os.path.join(self.tmp, "Mesh.inp"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count("*STEP"), 2)
        self.assertIn("*STEP, PERTURBATION\n*FREQUENCY\n4\n", text)
        App.Console.PrintMessage(f"\n[steps preload+modal] f1 {f1:.2f} Hz vs closed form {theory['f1']:.2f} (unloaded {plain:.2f})\n")

    def solve_held_bar(self, legacy):
        close_all()
        _doc, objs, theory = thermal.heated_steel_bar(length=300.0, held=True, max_size=4.0)
        analysis = objs["analysis"]
        steps.LEGACY_SINGLE_STEP = legacy
        try:
            folder = os.path.join(self.tmp, "legacy" if legacy else "steps")
            result = sim.solve(analysis, working_dir=folder)
        finally:
            steps.LEGACY_SINGLE_STEP = False
        mesh = result.Mesh.FemMesh
        mid = [s for n, s in zip(result.NodeNumbers, result.NodeStressXX) if abs(mesh.getNodeById(n).x - 150.0) < 3.0]
        with open(os.path.join(folder, "Mesh.inp"), encoding="utf-8") as handle:
            text = handle.read()
        return {"mid": sum(mid) / len(mid), "vm": max(result.vonMises), "u": max(result.DisplacementLengths),
                "t": max(result.Temperature), "steps": text.count("\n*STEP"), "theory": theory,
                "sf": result.MinSafetyFactor}

    def test_heat_then_stationary_equals_the_old_thermal_stress_study(self):
        old = self.solve_held_bar(True)
        new = self.solve_held_bar(False)
        self.assertEqual((old["steps"], new["steps"]), (1, 2))
        for key in ("mid", "vm", "u", "t", "sf"):
            self.assertAlmostEqual(new[key], old[key], delta=1e-3 * max(1.0, abs(old[key])), msg=key)
        self.assertLess(abs(new["mid"] - new["theory"]["stress"]) / abs(new["theory"]["stress"]), 0.02, new)
        App.Console.PrintMessage(f"\n[steps heat+stationary] mid {new['mid']:.3f} MPa (old one-block {old['mid']:.3f}, "
                                 f"theory {new['theory']['stress']:.2f}); max vM {new['vm']:.3f} vs {old['vm']:.3f}\n")
