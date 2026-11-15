# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of one part per file and sub-assemblies (roadmap ASM-01, ASM-05): bruceworx_partfiles.

    FreeCADCmd -t TestBruceWorxPartFiles
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_partfiles as pf

V = App.Vector


def _joint(group, kind, name, refs):
    import JointObject

    joint = group.newObject("App::FeaturePython", name)
    JointObject.Joint(joint, JointObject.JointTypes.index(kind))
    joint.Proxy.setJointConnectors(joint, refs)
    return joint


def _ground(group, obj):
    import JointObject

    ground = group.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(ground, obj)
    return ground


class Base(unittest.TestCase):
    def setUp(self):
        self.before = set(App.listDocuments())
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-partfiles-"))

    def tearDown(self):
        self.close_all()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def close_all(self):
        for name in set(App.listDocuments()) - self.before:
            App.closeDocument(name)

    def part_file(self, relative, kind="Part::Box", name="Solid"):
        path = os.path.join(self.tmp, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = App.newDocument(os.path.splitext(os.path.basename(path))[0])
        doc.addObject(kind, name)
        doc.recompute()
        doc.saveAs(path)
        return doc, path

    def assembly_with(self, relative, *parts):
        """An assembly file linking the solids of ``parts`` (documents)."""
        path = os.path.join(self.tmp, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = App.newDocument("Asm")
        doc.saveAs(path)
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        for i, part in enumerate(parts):
            link = asm.newObject("App::Link", f"L{i}")
            link.LinkedObject = part.Objects[0]
        doc.recompute()
        doc.save()
        return doc, path

    def links(self, doc):
        return [o for o in doc.Objects if o.TypeId == "App::Link"]


class TestLinksAndMoves(Base):
    def build(self):
        p1, _ = self.part_file("job/parts/P1.FCStd")
        p2, _ = self.part_file("job/P2.FCStd", "Part::Cylinder")
        _doc, path = self.assembly_with("job/Asm.FCStd", p1, p2)
        self.close_all()
        return path

    def test_links_are_stored_relative(self):
        report = pf.link_report(self.build())
        self.assertEqual(sorted(link["file"] for link in report["links"]), ["P2.FCStd", "parts/P1.FCStd"])
        self.assertEqual(report["absolute"], [])
        self.assertEqual(report["missing"], [])

    def test_a_moved_folder_opens_with_no_broken_links(self):
        self.build()
        moved = os.path.join(self.tmp, "elsewhere", "job moved")
        os.makedirs(os.path.dirname(moved))
        shutil.move(os.path.join(self.tmp, "job"), moved)
        doc = App.openDocument(os.path.join(moved, "Asm.FCStd"))
        self.assertEqual(pf.broken_links(doc), [])
        self.assertTrue(all(link.LinkedObject is not None and link.isValid() for link in self.links(doc)))
        self.assertEqual(pf.repair(doc), {"repointed": [], "missing": [], "ambiguous": []})

    def test_repair_finds_a_part_moved_inside_the_folder(self):
        path = self.build()
        job = os.path.dirname(path)
        os.makedirs(os.path.join(job, "bought"))
        shutil.move(os.path.join(job, "parts", "P1.FCStd"), os.path.join(job, "bought", "P1.FCStd"))
        doc = App.openDocument(path)
        self.assertEqual([b["file"] for b in pf.broken_links(doc)], ["parts/P1.FCStd"])
        looked = pf.repair(doc, dry=True)
        self.assertEqual(looked["repointed"][0]["now"], os.path.join("bought", "P1.FCStd"))
        self.assertEqual(len(pf.broken_links(doc)), 1)  # a dry run changes nothing
        report = pf.repair(doc)
        self.assertEqual([(r["object"], r["was"], r["now"], r["how"]) for r in report["repointed"]],
                         [("L0", "parts/P1.FCStd", os.path.join("bought", "P1.FCStd"), "same name")])
        self.assertIn("parts/P1.FCStd is now bought/P1.FCStd", pf.report_text(report))
        link = doc.getObject("L0")
        self.assertEqual(link.LinkedObject.Name, "Solid")
        self.assertAlmostEqual(link.Shape.Volume, 1000, places=6)
        doc.save()
        self.close_all()
        self.assertIn("bought/P1.FCStd", [link["file"] for link in pf.xlinks(path)])
        again = App.openDocument(path)
        self.assertEqual(pf.broken_links(again), [])

    def test_a_part_one_folder_up_is_found(self):
        path = self.build()
        job = os.path.dirname(path)
        shutil.move(os.path.join(job, "P2.FCStd"), os.path.join(self.tmp, "P2.FCStd"))
        doc = App.openDocument(path)
        report = pf.repair(doc)
        self.assertEqual([r["now"] for r in report["repointed"]], [os.path.join("..", "P2.FCStd")])

    def test_missing_and_ambiguous_are_reported_not_guessed(self):
        path = self.build()
        job = os.path.dirname(path)
        os.remove(os.path.join(job, "P2.FCStd"))
        os.makedirs(os.path.join(job, "a"))
        os.makedirs(os.path.join(job, "b"))
        shutil.copy(os.path.join(job, "parts", "P1.FCStd"), os.path.join(job, "a", "P1.FCStd"))
        shutil.move(os.path.join(job, "parts", "P1.FCStd"), os.path.join(job, "b", "P1.FCStd"))
        doc = App.openDocument(path)
        report = pf.repair(doc)
        self.assertEqual(report["repointed"], [])
        self.assertEqual([m["was"] for m in report["missing"]], ["P2.FCStd"])
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertIn("a/P1.FCStd, b/P1.FCStd", report["ambiguous"][0]["why"])
        self.assertIsNone(doc.getObject("L0").LinkedObject)

    def test_check_on_open_runs_once_and_respects_the_switch(self):
        path = self.build()
        job = os.path.dirname(path)
        shutil.move(os.path.join(job, "P2.FCStd"), os.path.join(job, "parts", "P2.FCStd"))
        prefs = App.ParamGet(pf.PREFS_PATH)
        doc = App.openDocument(path)
        try:
            prefs.SetBool(pf.PREF_ON_OPEN, False)
            self.assertIsNone(pf.check_on_open(doc))
            self.assertEqual(len(pf.broken_links(doc)), 1)
            prefs.SetBool(pf.PREF_ON_OPEN, True)
            pf._state["seen"].discard(doc.Name)
            found = pf.check_on_open(doc)
            self.assertIsNotNone(found)
            self.assertEqual(pf.broken_links(doc), [])
            self.assertIsNone(pf.check_on_open(doc))  # once per document
        finally:
            prefs.RemBool(pf.PREF_ON_OPEN)
            pf._state["seen"].discard(doc.Name)
            box = pf._state["box"]
            if box is not None:
                box.close()


class TestMakeExternal(Base):
    def hybrid(self):
        """An assembly with a grounded box and, jointed to it, a body modelled in the same file."""
        doc = self.doc = App.newDocument("Hybrid")
        doc.UndoMode = 1
        doc.saveAs(os.path.join(self.tmp, "Hybrid.FCStd"))
        self.asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.joints = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.base = self.asm.newObject("Part::Box", "Base")
        self.body = self.asm.newObject("PartDesign::Body", "Body")
        self.body.Label = "Arm"
        self.body.newObject("PartDesign::AdditiveBox", "Box")
        doc.recompute()
        _ground(self.joints, self.base)
        self.joint = _joint(self.joints, "Fixed", "Fix", [[self.base, ["Face2", "Vertex5"]],
                                                         [self.body, ["Face1", "Vertex1"]]])
        doc.recompute()
        doc.save()
        return doc

    def test_a_body_in_an_assembly_becomes_a_linked_file(self):
        doc = self.hybrid()
        placement = App.Placement(self.body.Placement)
        box = self.body.Shape.BoundBox
        where = (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)
        undo = len(doc.UndoNames)
        result = pf.make_external(self.body)
        link = result["link"]
        self.assertEqual(result["path"], os.path.join(self.tmp, "Arm.FCStd"))
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertEqual((link.TypeId, link.Label), ("App::Link", "Arm"))
        self.assertIn(link, self.asm.Group)
        self.assertIsNone(doc.getObject("Body"))
        self.assertIsNone(doc.getObject("Box"))
        self.assertTrue(link.Placement.isSame(placement, 1e-9))
        self.assertIs(self.joint.Reference2[0], link)
        self.assertEqual(list(self.joint.Reference2[1]), ["Face1", "Vertex1"])
        self.assertTrue(self.joint.isValid() and link.isValid())
        box = link.Shape.BoundBox
        for got, want in zip((box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax), where):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(self.asm.solve(), 0)
        self.assertEqual(result["also_copied"], [])
        self.assertEqual(len(doc.UndoNames), undo + 1)
        self.assertEqual(doc.UndoNames[0], "Make External")
        # the part file holds the body at its own origin, and the link to it is relative
        part = link.LinkedObject.Document
        self.assertEqual(os.path.realpath(part.FileName), os.path.realpath(result["path"]))
        self.assertTrue(link.LinkedObject.Placement.isIdentity())
        self.assertTrue(link.LinkedObject.isDerivedFrom("PartDesign::Body"))
        doc.save()
        self.assertEqual([x["file"] for x in pf.xlinks(doc.FileName) if x["object"] == link.Name], ["Arm.FCStd"])

    def test_undo_brings_the_body_back_and_keeps_the_file(self):
        doc = self.hybrid()
        result = pf.make_external(self.body)
        doc.undo()
        doc.recompute()
        body = doc.getObject("Body")
        self.assertIsNotNone(body)
        self.assertIs(self.joint.Reference2[0], body)
        self.assertEqual([o for o in doc.Objects if o.TypeId == "App::Link"], [])
        self.assertTrue(os.path.isfile(result["path"]))

    def test_bodies_of_a_multi_body_file(self):
        doc = App.newDocument("Multi")
        doc.saveAs(os.path.join(self.tmp, "Multi.FCStd"))
        first = doc.addObject("PartDesign::Body", "Body")
        first.newObject("PartDesign::AdditiveBox", "Box")
        second = doc.addObject("PartDesign::Body", "Body001")
        second.newObject("PartDesign::AdditiveCylinder", "Cyl")
        second.Placement.Base = V(40, 0, 0)
        loose = doc.addObject("Part::Box", "Loose")
        doc.recompute()
        doc.save()
        made = [pf.make_external(obj) for obj in (first, second, loose)]
        self.assertEqual([os.path.basename(m["path"]) for m in made], ["Body.FCStd", "Body001.FCStd", "Loose.FCStd"])
        self.assertEqual(sorted(o.TypeId for o in doc.Objects), ["App::Link"] * 3)
        self.assertAlmostEqual(made[1]["link"].Shape.BoundBox.Center.x, 40, places=6)
        self.assertEqual(os.path.basename(pf._free_path(self.tmp, "Body")), "Body 2.FCStd")  # never overwrites

    def test_what_cannot_be_made_external(self):
        doc = App.newDocument("Unsaved")
        body = doc.addObject("PartDesign::Body", "Body")
        box = body.newObject("PartDesign::AdditiveBox", "Box")
        doc.recompute()
        self.assertIn("save this document first", pf.can_externalise(body))
        self.assertIn("select the body", pf.can_externalise(box))
        self.assertIn("nothing is selected", pf.can_externalise(None))
        with self.assertRaises(pf.PartFilesError):
            pf.make_external(body)
        self.assertEqual(os.listdir(self.tmp), [])


class TestSubAssemblies(Base):
    def setUp(self):
        super().setUp()
        # the sub-assembly: a rail (grounded) and a carriage on a slider along x
        sub = App.newDocument("Axis")
        self.sub_asm = sub.addObject("Assembly::AssemblyObject", "AxisAsm")
        joints = self.sub_asm.newObject("Assembly::JointGroup", "Joints")
        self.rail = self.sub_asm.newObject("Part::Box", "Rail")
        self.rail.Length, self.rail.Width, self.rail.Height = 100, 10, 10
        self.carriage = self.sub_asm.newObject("Part::Box", "Carriage")
        self.carriage.Placement.Base = V(20, 0, 0)
        sub.recompute()
        _ground(joints, self.rail)
        _joint(joints, "Slider", "Slide", [[self.rail, ["Face2", "Vertex5"]], [self.carriage, ["Face2", "Vertex5"]]])
        sub.recompute()
        self.sub_path = os.path.join(self.tmp, "Axis.FCStd")
        sub.saveAs(self.sub_path)
        _part, self.part_path = self.part_file("Plate.FCStd")
        top = self.top = App.newDocument("Top")
        top.UndoMode = 1
        top.saveAs(os.path.join(self.tmp, "Top.FCStd"))
        self.asm = top.addObject("Assembly::AssemblyObject", "Assembly")
        self.joints = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.base = self.asm.newObject("Part::Box", "Base")
        self.base.Placement.Base = V(0, 0, -50)
        _ground(self.joints, self.base)
        top.recompute()

    def child(self, link, source):
        return next((c for c in link.Group if c.isDerivedFrom("App::Link") and c.getLinkedObject(False) == source), None)

    def test_insert_picks_the_kind_of_link(self):
        plate = pf.insert(self.asm, self.part_path)
        self.assertEqual(plate.TypeId, "App::Link")
        sub = pf.insert(self.asm, self.sub_path, rigid=True)
        self.assertEqual(sub.TypeId, "Assembly::AssemblyLink")
        self.assertTrue(sub.Rigid)
        self.assertTrue(pf.is_sub_assembly(sub) and not pf.is_sub_assembly(plate))
        with self.assertRaises(pf.PartFilesError):
            pf.insert(self.asm, os.path.join(self.tmp, "nope.FCStd"))
        with self.assertRaises(pf.PartFilesError):
            pf.set_flexible(plate)

    def test_flexible_moves_in_the_parent_and_rigid_does_not(self):
        import bruceworx_api_asm as api

        frame = App.Placement(V(0, 50, 0), App.Rotation())
        sub = pf.insert(self.asm, self.sub_path, rigid=False, placement=frame)
        rail, carriage = self.child(sub, self.rail), self.child(sub, self.carriage)
        self.assertIsNotNone(carriage)
        _joint(self.joints, "Fixed", "Hold", [[self.base, ["Face6", "Vertex5"]], [rail, ["Face5", "Vertex1"]]])
        self.top.recompute()
        self.assertIn("Slide", [j.Label for j in api.joints(self.asm)])  # its joint is solved here
        self.assertEqual(self.asm.solve(), 0)
        start = App.Placement(carriage.Placement)
        rail_at = App.Placement(rail.Placement)
        # push the carriage along the rail and off it sideways: the slider keeps the first, undoes the second
        carriage.Placement = App.Placement(start.Base + V(30, 7, 0), start.Rotation)
        self.assertEqual(self.asm.solve(), 0)
        moved = carriage.Placement.Base - start.Base
        self.assertGreater(moved.x, 20)
        self.assertAlmostEqual(moved.y, 0, places=4)
        self.assertAlmostEqual(moved.z, 0, places=4)
        self.assertTrue(rail.Placement.isSame(rail_at, 1e-6))

        # rigid: one body; its parts and its joint are no longer the parent's to move
        self.assertTrue(pf.set_flexible(sub, False))
        self.assertEqual(self.top.UndoNames[0], "Rigid sub-assembly")
        self.assertTrue(sub.Rigid)
        self.assertNotIn("Slide", [j.Label for j in api.joints(self.asm)])
        self.assertFalse(pf.set_flexible(sub, False))  # already rigid: nothing to do
        self.assertTrue(pf.set_flexible(sub, True))
        self.assertIn("Slide", [j.Label for j in api.joints(self.asm)])
