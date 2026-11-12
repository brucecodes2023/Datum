# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the agent API (AUT-02): bruceworx_api_save, bruceworx_api_docs, bruceworx_api_asm, and their
bridge tools. Every fixture is built here; no file is read. Run: FreeCADCmd -t TestBruceWorxApi
"""

import json
import os
import tempfile
import unittest
import zipfile

import FreeCAD as App

import JointObject
import bruceworx_api_asm as asm_api
import bruceworx_api_docs as docs
import bruceworx_api_save as save_api
import bruceworx_asm_model as am
import bruceworx_bridge as bb
import bruceworx_bridge_tools as tools

V = App.Vector
GUI_XML = ('<?xml version="1.0" encoding="utf-8"?>\n<Document SchemaVersion="1"><ViewProviderData Count="0">'
           '<ColorList file="ShapeAppearance"/></ViewProviderData></Document>')


def add_gui_data(path):
    """Make a file saved headless look like one saved from the window. True when something was added."""
    with zipfile.ZipFile(path) as z:
        if save_api.GUI_FILE in z.namelist():
            return False
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr(save_api.GUI_FILE, GUI_XML)
        z.writestr("ShapeAppearance", b"\x01\x00\x00\x00")
        z.writestr("thumbnails/Thumbnail.png", b"\x89PNG not really")
    return True


def names_in(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def run_tool(tool, **args):
    return tools.call(tool, bb.validate(tool, args), headless=True)


# ----- the parts that need no document ---------------------------------------------------------------------------


class TestPure(unittest.TestCase):
    def test_same_origin(self):
        here = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        self.assertTrue(save_api.same_origin(here, (0, 0, 0, 0, 0, 0, -1)))  # q and -q: one rotation
        self.assertFalse(save_api.same_origin(here, (0, 0, 0.001, 0, 0, 0, 1)))
        self.assertFalse(save_api.same_origin(here, (0, 0, 0, 0, 0, 0.01, 0.99995)))

    def test_backup_name(self):
        import datetime

        name = save_api.backup_name("/tmp/Plate.FCStd", datetime.datetime(2026, 11, 10, 14, 25, 1))
        self.assertEqual(name, "/tmp/Plate.20261110-142501.bak.FCStd")

    def test_readd_keeps_order_and_content(self):
        folder = tempfile.mkdtemp()
        old, new = os.path.join(folder, "old.FCStd"), os.path.join(folder, "new.FCStd")
        with zipfile.ZipFile(old, "w") as z:
            z.writestr("Document.xml", "<Document/>")
            z.writestr("Gone.Shape.brp", "shape")
        self.assertTrue(add_gui_data(old))
        with zipfile.ZipFile(new, "w") as z:
            z.writestr("Document.xml", "<Document new='1'/>")
        grabbed = save_api.grab(old)
        self.assertEqual(sorted(grabbed), ["GuiDocument.xml", "ShapeAppearance", "thumbnails/Thumbnail.png"])
        self.assertEqual(sorted(save_api.readd(new, grabbed)), sorted(grabbed))
        self.assertEqual(names_in(new)[0], "Document.xml")
        with zipfile.ZipFile(new) as z:
            self.assertEqual(z.read("Document.xml"), b"<Document new='1'/>")
            self.assertEqual(z.read("GuiDocument.xml").decode(), GUI_XML)
        self.assertEqual(save_api.readd(new, grabbed), [])  # nothing missing: the file is left alone
        self.assertEqual(sorted(os.listdir(folder)), ["new.FCStd", "old.FCStd"])

    def test_census_diff(self):
        def rec(path, box, volume=1000.0, file="a.FCStd"):
            return {"file": file, "path": path, "label": path, "solid": 0, "bbox": box, "volume": volume}

        a = [rec("A", [0, 0, 0, 10, 10, 10]), rec("B", [20, 0, 0, 30, 10, 10]), rec("C", [40, 0, 0, 50, 10, 10]),
             rec("Old.D", [60, 0, 0, 70, 10, 10])]
        b = [rec("A", [0, 0, 0, 10, 10, 10]), rec("B", [20, 0, 5, 30, 10, 15]), rec("New.D", [60, 0, 0, 70, 10, 10]),
             rec("E", [0, 0, 0, 1, 1, 1], 1.0)]
        out = docs.census_diff(a, b)
        self.assertEqual([m["path"] for m in out["moved"]], ["B"])
        self.assertEqual(out["moved"][0]["shift"], 5)
        self.assertEqual([r["path"] for r in out["removed"]], ["C"])
        self.assertEqual([r["path"] for r in out["added"]], ["E"])
        self.assertEqual([r["was_path"] for r in out["renamed"]], ["Old.D"])
        self.assertEqual((out["count_a"], out["count_b"]), (4, 4))
        same = docs.census_diff(a, [dict(r, bbox=[v + 0.001 for v in r["bbox"]]) for r in a])
        self.assertFalse(same["moved"] or same["added"] or same["removed"])

    def test_tools_are_specified(self):
        for name in ("census", "census_diff", "asm_joints", "asm_rule_check", "asm_knock_test"):
            self.assertIn(name, bb.SPECS)
            self.assertTrue(callable(getattr(tools, "t_" + name)))
        args = bb.validate("asm_knock_test", {})
        self.assertEqual((args["seed"], args["tol_mm"], args["tol_deg"]), (0, 1e-4, 1e-3))
        self.assertIsNone(args["parts"])
        with self.assertRaisesRegex(bb.ArgError, "a is required"):
            bb.validate("census_diff", {"b": "x"})


# ----- saving ----------------------------------------------------------------------------------------------------


class TestSafeSave(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.doc = App.newDocument("BWApiSave")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        self.path = os.path.join(self.folder, "Plate.FCStd")

    def tearDown(self):
        if "BWApiSave" in App.listDocuments():
            App.closeDocument("BWApiSave")

    def test_keeps_gui_document(self):
        self.doc.saveAs(self.path)
        added = add_gui_data(self.path)
        self.box.Length = 25
        self.doc.recompute()
        out = save_api.safe_save(self.doc, backup=True)
        self.assertTrue(os.path.samefile(out["path"], self.path))
        self.assertTrue(out["origin_ok"])
        names = names_in(self.path)
        self.assertEqual(names[0], "Document.xml")
        self.assertIn("GuiDocument.xml", names)
        if not App.GuiUp:
            self.assertEqual(out["mode"], "headless")
            self.assertTrue(added)
            for name in ("GuiDocument.xml", "ShapeAppearance", "thumbnails/Thumbnail.png"):
                self.assertIn(name, out["kept_entries"])
                self.assertIn(name, names)
            with zipfile.ZipFile(self.path) as z:
                self.assertEqual(z.read("GuiDocument.xml").decode(), GUI_XML)
        self.assertTrue(os.path.isfile(out["backup"]))
        self.assertEqual([f for f in os.listdir(self.folder) if "presave" in f or "regui" in f], [])
        # the saved file is the changed document
        App.closeDocument("BWApiSave")
        again = App.openDocument(self.path)
        try:
            self.assertAlmostEqual(again.getObject("Box").Length.Value, 25)
        finally:
            App.closeDocument(again.Name)

    def test_first_save_needs_a_path(self):
        with self.assertRaises(ValueError):
            save_api.safe_save(self.doc)
        out = save_api.safe_save(self.doc, os.path.join(self.folder, "sub", "New"))
        self.assertTrue(out["path"].endswith("New.FCStd"))
        self.assertTrue(os.path.isfile(out["path"]))
        self.assertEqual(out["kept_entries"], [])

    def test_save_as_takes_the_gui_data_along(self):
        self.doc.saveAs(self.path)
        add_gui_data(self.path)
        other = os.path.join(self.folder, "Copy.FCStd")
        out = save_api.safe_save(self.doc, other)
        self.assertIn("GuiDocument.xml", names_in(other))
        self.assertTrue(os.path.samefile(self.doc.FileName, other))
        if not App.GuiUp:
            self.assertIn("GuiDocument.xml", out["kept_entries"])

    def test_moved_origin_is_refused(self):
        asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        self.doc.recompute()
        self.doc.saveAs(self.path)
        self.assertEqual(save_api.moved_origins(self.doc, self.path), [])
        self.assertIn("Assembly", save_api.disk_origins(self.path))
        with open(self.path, "rb") as handle:
            before = handle.read()
        asm.Placement = App.Placement(V(0, 0, 3), App.Rotation())
        with self.assertRaises(save_api.OriginMoved):
            save_api.safe_save(self.doc)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        with self.assertRaisesRegex(Exception, "origin"):
            run_tool("save", doc=self.doc.Name)
        out = run_tool("save", doc=self.doc.Name, check_origin=False)
        self.assertFalse(out["origin_ok"])
        self.assertEqual(save_api.moved_origins(self.doc, self.path), [])

    def test_bridge_save(self):
        out = run_tool("save", doc=self.doc.Name, path=self.path)
        self.assertTrue(os.path.samefile(out["file"], self.path))
        self.assertTrue(out["origin_ok"])
        self.assertEqual(run_tool("save", doc=self.doc.Name)["doc"], self.doc.Name)


# ----- the census --------------------------------------------------------------------------------------------------


class TestCensus(unittest.TestCase):
    """One document: a box, a part with a box and a hidden box in it, an inner assembly linking the box, and
    the assembly that links all three (a link to a solid, to a part, and to an assembly with a link in it)."""

    def setUp(self):
        self.doc = App.newDocument("BWApiCensus")
        d = self.doc
        self.inner = d.addObject("Part::Box", "Inner")
        self.sub = d.addObject("App::Part", "Sub")
        sb = self.sub.newObject("Part::Box", "SubBox")
        sb.Length, sb.Width, sb.Height = 2, 3, 4
        sb.Placement = App.Placement(V(0, 0, 20), App.Rotation())
        hidden = self.sub.newObject("Part::Box", "Hidden")
        hidden.Visibility = False
        self.asm2 = d.addObject("Assembly::AssemblyObject", "Asm2")
        self.l3 = self._link(self.asm2, "L3", self.inner, (0, 0, 5))
        self.asm = d.addObject("Assembly::AssemblyObject", "Assembly")
        self.asm.newObject("Assembly::JointGroup", "Joints")
        self.l1 = self._link(self.asm, "L1", self.inner, (100, 0, 0))
        self.l2 = self._link(self.asm, "L2", self.sub, (0, 50, 0))
        self.l4 = self._link(self.asm, "L4", self.asm2, (0, 0, 200))
        d.recompute()

    def tearDown(self):
        App.closeDocument("BWApiCensus")

    def _link(self, parent, name, target, pos):
        link = parent.newObject("App::Link", name)
        link.LinkedObject = target
        link.Placement = App.Placement(V(*pos), App.Rotation())
        return link

    def test_nested_links(self):
        found = {r["path"]: r for r in docs.census(self.asm)}
        self.assertEqual(set(found), {"L1", "L2.SubBox", "L4.L3"})
        self.assertEqual(found["L1"]["bbox"], [100, 0, 0, 110, 10, 10])
        self.assertEqual(found["L2.SubBox"]["bbox"], [0, 50, 20, 2, 53, 24])
        self.assertEqual(found["L4.L3"]["bbox"], [0, 0, 205, 10, 10, 215])
        self.assertAlmostEqual(found["L2.SubBox"]["volume"], 24, places=3)
        self.assertAlmostEqual(found["L4.L3"]["volume"], 1000, places=3)
        self.assertEqual({r["file"] for r in found.values()}, {self.doc.Name})  # not saved: the document's Name
        self.assertEqual({r["solid"] for r in found.values()}, {0})
        json.dumps(list(found.values()))

    def test_own_frame_and_document(self):
        self.asm.Placement = App.Placement(V(1000, 0, 0), App.Rotation())
        self.doc.recompute()
        own = {r["path"]: r for r in docs.census(self.asm)}
        self.assertEqual(own["L1"]["bbox"], [100, 0, 0, 110, 10, 10])  # the assembly's own origin is left out
        whole = {r["path"]: r for r in docs.census(self.doc)}
        self.assertEqual(whole["Assembly.L1"]["bbox"], [1100, 0, 0, 1110, 10, 10])
        self.assertIn("Inner", whole)
        self.assertNotIn("Sub.Hidden", whole)

    def test_diff_finds_moved_and_removed(self):
        before = docs.census(self.asm)
        self.l1.Placement = App.Placement(V(105, 0, 0), App.Rotation())
        self.doc.removeObject(self.l2.Name)
        self.doc.recompute()
        out = docs.census_diff(before, docs.census(self.asm))
        self.assertEqual([m["path"] for m in out["moved"]], ["L1"])
        self.assertAlmostEqual(out["moved"][0]["shift"], 5)
        self.assertEqual([r["path"] for r in out["removed"]], ["L2.SubBox"])
        self.assertEqual(out["added"], [])
        self.assertEqual((out["count_a"], out["count_b"]), (3, 2))

    def test_bridge_tools(self):
        folder = tempfile.mkdtemp()
        a = os.path.join(folder, "a.json")
        first = run_tool("census", doc=self.doc.Name, object="Assembly", path=a)
        self.assertEqual(first["count"], 3)
        self.assertEqual(first["saved"], a)
        self.assertEqual(len(docs.load_census(a)), 3)
        self.l4.Placement = App.Placement(V(0, 0, 300), App.Rotation())
        self.doc.recompute()
        b = os.path.join(folder, "b.json")
        run_tool("census", doc=self.doc.Name, object="Assembly", path=b)
        out = run_tool("census_diff", a=a, b=b)
        self.assertFalse(out["same"])
        self.assertEqual([m["path"] for m in out["moved"]], ["L4.L3"])
        self.assertTrue(run_tool("census_diff", a=a, b=a)["same"])
        self.assertTrue(run_tool("census_diff", a=self.doc.Name, b=self.doc.Name)["same"])
        with self.assertRaises(Exception):
            run_tool("census_diff", a=a, b="no such document")


# ----- mates -------------------------------------------------------------------------------------------------------


class _Assembly(unittest.TestCase):
    """An assembly of 10 mm boxes; A is grounded unless a test says otherwise."""

    NAME = "BWApiAsm"

    def setUp(self):
        self.doc = App.newDocument(self.NAME)
        self.doc.UndoMode = 1
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jg = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.a = self._box("A", (0, 0, 0))
        self.b = self._box("B", (30, 5, 0))
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.NAME)

    def _box(self, name, pos):
        b = self.asm.newObject("Part::Box", name)
        b.Placement = App.Placement(V(*pos), App.Rotation())
        self.doc.recompute()
        return b

    def _cyl(self, name, pos):
        c = self.asm.newObject("Part::Cylinder", name)
        c.Radius, c.Height = 5, 20
        c.Placement = App.Placement(V(*pos), App.Rotation())
        self.doc.recompute()
        return c

    def _ground(self, obj):
        g = self.jg.newObject("App::FeaturePython", "Ground" + obj.Name)
        JointObject.GroundedJoint(g, obj)
        return g

    def _ref(self, obj, el):
        return [obj, [el, el]]

    def _lock(self, one, other):
        """A Fixed joint, face to face."""
        return am.create_mate(self.asm, self._ref(one, "Face2"), self._ref(other, "Face1"), key="lock")

    def _placements(self):
        return {o.Name: App.Placement(o.Placement) for o in asm_api.parts(self.asm)}

    def assertPlacements(self, before):
        for name, was in before.items():
            self.assertTrue(self.doc.getObject(name).Placement.isSame(was, 1e-9), name)


class TestAsmReading(_Assembly):
    def test_joints_refs_and_ground(self):
        self.assertEqual(asm_api.grounded(self.asm), [])
        self.assertEqual(asm_api.joints(self.asm), [])
        g = asm_api.ground(self.asm, self.a)
        self.assertEqual(asm_api.ground(self.asm, self.a).Name, g.Name)  # grounded already: the same ground
        self.assertEqual(asm_api.grounded(self.asm), [self.a])
        j = self._lock(self.a, self.b)
        self.assertEqual(asm_api.joints(self.asm), [j])
        self.assertEqual({o.Name for o in asm_api.parts(self.asm)}, {"A", "B"})
        r1, r2 = asm_api.refs(j)
        self.assertEqual((r1["part"], r1["element"], r1["path"]), ("A", "Face2", "A.Face2"))
        self.assertEqual((r2["part"], r2["element"], r2["index"]), ("B", "Face1", 2))
        info = asm_api.joint_info(j)
        self.assertEqual((info["joint"], info["type"], info["mate"]), (j.Name, "Fixed", "lock"))
        json.dumps(info)

    def test_no_joint_group_is_not_made(self):
        doc = App.newDocument("BWApiBare")
        try:
            bare = doc.addObject("Assembly::AssemblyObject", "Assembly")
            self.assertEqual(asm_api.joints(bare), [])
            self.assertEqual(asm_api.grounded(bare), [])
            self.assertIsNone(asm_api.joint_group(bare))
            self.assertFalse(asm_api.rule_check(bare)["ok"])
            self.assertIsNone(asm_api.joint_group(bare))
        finally:
            App.closeDocument("BWApiBare")

    def test_solve_snapshot_restore_moved(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        out = asm_api.solve(self.asm)
        self.assertTrue(out["ok"])
        self.assertEqual(out["code"], 0)
        self.assertIn("seconds", out)
        snap = asm_api.snapshot(self.asm)
        self.assertEqual(asm_api.moved(self.asm, snap, 1e-6), [])
        self.b.Placement = App.Placement(snap["B"].Base + V(0, 0, 2), snap["B"].Rotation)
        off = asm_api.moved(self.asm, snap, 1e-6)
        self.assertEqual([m["part"] for m in off], ["B"])
        self.assertAlmostEqual(off[0]["mm"], 2, places=5)
        asm_api.restore(self.asm, snap)
        self.assertEqual(asm_api.moved(self.asm, snap, 1e-9, 1e-9), [])

    def test_full_turn_is_no_turn(self):
        self._ground(self.a)
        snap = asm_api.snapshot(self.asm)
        self.b.Placement = App.Placement(snap["B"].Base, App.Rotation(V(0, 0, 1), 359.99998))
        self.assertEqual(asm_api.moved(self.asm, snap, 1e-4, 1e-3), [])
        self.b.Placement = App.Placement(snap["B"].Base, App.Rotation(V(0, 0, 1), 2))
        self.assertAlmostEqual(asm_api.moved(self.asm, snap)[0]["deg"], 2, places=4)


class TestRuleCheck(_Assembly):
    def rules(self, **kw):
        out = asm_api.rule_check(self.asm, **kw)
        return out, [p["rule"] for p in out["problems"]]

    def test_fully_mated_passes(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        before = self._placements()
        out, rules = self.rules()
        self.assertEqual(rules, [], out["problems"])
        self.assertTrue(out["ok"])
        self.assertEqual((out["parts"], out["joints"], out["grounded"]), (2, 1, ["A"]))
        out, rules = self.rules(solve_too=True)
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["solve"]["code"], 0)
        self.assertPlacements(before)
        json.dumps(out)

    def test_missing_ground(self):
        self._lock(self.a, self.b)
        out, rules = self.rules()
        self.assertFalse(out["ok"])
        self.assertIn("ground", rules)
        self.assertIn("no part is grounded", [p["detail"] for p in out["problems"]])

    def test_two_grounds(self):
        self._ground(self.a)
        self._ground(self.b)
        self._lock(self.a, self.b)
        out, rules = self.rules()
        self.assertFalse(out["ok"])
        self.assertEqual(rules.count("ground"), 1)
        self.assertIn("2 parts are grounded", out["problems"][rules.index("ground")]["detail"])
        self.assertEqual(sorted(out["grounded"]), ["A", "B"])

    def test_missing_joint(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        self._box("C", (60, 0, 0))
        out, rules = self.rules()
        self.assertEqual(rules, ["unmated"])
        self.assertEqual(out["problems"][0]["part"], "C")

    def test_doubled_and_stored_position(self):
        self._ground(self.a)
        first = self._lock(self.a, self.b)
        second = am.create_mate(self.asm, self._ref(self.a, "Face4"), self._ref(self.b, "Face4"), key="parallel")
        out, rules = self.rules()
        doubled = [p for p in out["problems"] if p["rule"] == "doubled"]
        self.assertEqual([p["joint"] for p in doubled], [second.Name])
        self.assertIn(first.Label, doubled[0]["detail"])
        self.doc.removeObject(second.Name)
        first.Offset2 = App.Placement(V(1, 0, 0), App.Rotation())
        self.assertIn("stored_offset", self.rules()[1])
        first.Offset2 = App.Placement(V(0, 0, 1.5), App.Rotation())  # a distance along the axis is a value
        self.assertNotIn("stored_offset", self.rules()[1])

    def test_element_names_and_nothing_stale(self):
        self._ground(self.a)
        j = self._lock(self.a, self.b)
        self.assertTrue(asm_api.ELEMENT.search("Face12"))
        self.assertFalse(asm_api.ELEMENT.search("X_Axis"))
        self.assertFalse(asm_api.ELEMENT.search(""))
        self.assertEqual(self.rules()[1], [])
        self.assertEqual(asm_api.stale_joints(self.asm)["stale"], [])
        self.assertEqual(asm_api.stale_joints(self.asm)["joints"], 1)
        self.assertEqual(j.JointType, "Fixed")

    def test_bridge_tools(self):
        self._ground(self.a)
        j = self._lock(self.a, self.b)
        listed = run_tool("asm_joints", doc=self.doc.Name)
        self.assertEqual((listed["count"], listed["grounded"], listed["parts"]), (1, ["A"], 2))
        self.assertEqual(listed["joints"][0]["joint"], j.Name)
        self.assertEqual([r["part"] for r in listed["joints"][0]["refs"]], ["A", "B"])
        checked = run_tool("asm_rule_check", doc=self.doc.Name, assembly="Assembly")
        self.assertTrue(checked["ok"], checked["problems"])
        self.assertEqual(checked["problems_count"], 0)


class TestAddJointTool(_Assembly):
    def test_joint_is_rooted_at_its_components_and_solves(self):
        self._ground(self.a)
        was = App.Placement(self.b.Placement)
        out = run_tool("add_joint", doc=self.doc.Name, type="fixed", ref1="A.Face2", ref2="B.Face1")
        self.assertEqual((out["type"], out["component1"], out["component2"]), ("Fixed", "A", "B"))
        self.assertEqual(out["reference1"], ["Face2", "Face2"])
        self.assertEqual(out["solve_code"], 0)
        joint = self.doc.getObject(out["joint"])
        self.assertEqual(joint.Reference1[0], self.a)  # the component, not the assembly
        self.assertEqual(joint.Reference2[0], self.b)
        self.assertEqual(list(joint.Reference2[1]), ["Face1", "Face1"])
        self.assertFalse(self.b.Placement.isSame(was, 1e-6))  # the solve brought B to A
        checked = asm_api.rule_check(self.asm, solve_too=True)
        self.assertTrue(checked["ok"], checked["problems"])
        self.assertTrue(asm_api.knock_test(self.asm)["ok"])

    def test_vertex_and_bad_references(self):
        self._ground(self.a)
        out = run_tool("add_joint", doc=self.doc.Name, type="Distance", ref1="A.Face2", ref2="B.Face1",
                       vertex1="Vertex6")
        self.assertEqual(out["reference1"], ["Face2", "Vertex6"])
        with self.assertRaisesRegex(Exception, "Component.ElementN"):
            run_tool("add_joint", doc=self.doc.Name, type="Fixed", ref1="Face2", ref2="B.Face1")
        with self.assertRaisesRegex(Exception, "is not in"):
            run_tool("add_joint", doc=self.doc.Name, type="Fixed", ref1="Nobody.Face2", ref2="B.Face1")
        with self.assertRaisesRegex(Exception, "two components"):
            run_tool("add_joint", doc=self.doc.Name, type="Fixed", ref1="A.Face2", ref2="A.Face1")


class TestKnock(_Assembly):
    def test_fully_mated_passes(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        c = self._box("C", (60, 0, 0))
        self._lock(self.b, c)
        before = self._placements()
        out = asm_api.knock_test(self.asm, seed=3)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["parts"], out["checked"], out["returned"]), (2, 2, 2))
        self.assertEqual((out["failed"], out["free"], out["unchecked"]), ([], [], []))
        self.assertEqual(out["in_place"]["code"], 0)
        self.assertEqual(out["in_place"]["moved"], [])
        self.assertTrue(out["all_at_once"]["ok"], out["all_at_once"])
        self.assertTrue(out["restored"])
        self.assertIn("seconds", out)
        self.assertPlacements(before)
        json.dumps(out)

    def test_under_mated_reports_the_free_part(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        self._box("C", (60, 0, 0))
        before = self._placements()
        out = asm_api.knock_test(self.asm)
        self.assertFalse(out["ok"])
        self.assertEqual([f["part"] for f in out["free"]], ["C"])
        self.assertGreater(out["free"][0]["mm"], 1.9)
        self.assertEqual(out["returned"], 1)
        self.assertEqual(out["checked"], 2)
        self.assertEqual(out["failed"], [])
        self.assertPlacements(before)

    def test_hinge_may_turn(self):
        self.doc.removeObject(self.a.Name)  # the two cylinders only
        self.doc.removeObject(self.b.Name)
        ca = self._cyl("CA", (0, 40, 0))
        cb = self._cyl("CB", (30, 40, 0))
        self._ground(ca)
        hinge = am.create_mate(self.asm, self._ref(ca, "Face1"), self._ref(cb, "Face1"), key="hinge")
        self.assertEqual(asm_api.solve(self.asm)["code"], 0)
        free = asm_api.free_motion(self.asm)
        self.assertEqual([f["kind"] for f in free["CB"]], ["spin"])
        self.assertEqual(free["CB"][0]["joint"], hinge.Name)
        self.assertAlmostEqual(abs(free["CB"][0]["axis"].z), 1, places=6)
        before = self._placements()
        out = asm_api.knock_test(self.asm, seed=1)
        self.assertEqual(out["free"], [], out)
        self.assertEqual(out["failed"], [], out)
        self.assertTrue(out["ok"], out)
        self.assertPlacements(before)
        # turned about the hinge it is where it belongs; lifted off the hinge it is not
        snap = asm_api.snapshot(self.asm)
        was = snap["CB"]
        cb.Placement = App.Placement(was.Base, App.Rotation(V(0, 0, 1), 40).multiply(was.Rotation))
        self.assertEqual(asm_api.moved(self.asm, snap, allow=free), [])
        self.assertEqual([m["part"] for m in asm_api.moved(self.asm, snap)], ["CB"])
        cb.Placement = App.Placement(was.Base + V(0.5, 0, 0), was.Rotation)
        self.assertEqual([m["part"] for m in asm_api.moved(self.asm, snap, allow=free)], ["CB"])
        asm_api.restore(self.asm, snap)

    def test_steps_progress_and_cancel(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        c = self._box("C", (60, 0, 0))
        self._lock(self.b, c)
        before = self._placements()
        test = asm_api.KnockTest(self.asm)
        self.assertEqual((test.total, test.done), (2, 0))
        self.assertFalse(test.step(0.0))  # the solve in place
        self.assertFalse(test.step(0.0))  # the first part
        self.assertEqual(test.done, 1)
        self.assertEqual(test.progress()["total"], 2)
        test.cancel()
        self.assertTrue(test.finished)
        self.assertTrue(test.step(0.0))
        out = test.result()
        self.assertFalse(out["ok"])
        self.assertTrue(out["cancelled"])
        self.assertEqual((out["checked"], len(out["unchecked"])), (1, 1))
        self.assertEqual(out["unchecked"][0]["reason"], "cancelled")
        self.assertPlacements(before)
        # the callback: called after each step, False cancels
        seen = []
        out = asm_api.knock_test(self.asm, step=lambda p: seen.append(dict(p)) or len(seen) < 2)
        self.assertEqual(len(seen), 2)
        self.assertTrue(out["cancelled"])
        self.assertEqual(out["checked"], 1)
        self.assertPlacements(before)

    def test_placements_come_back_after_an_error(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        before = self._placements()
        test = asm_api.KnockTest(self.asm)

        def boom():
            raise RuntimeError("solver fell over")

        test.step(0.0)
        test._solve = boom
        test.step(1e9)
        out = test.result()
        self.assertTrue(test.finished)
        self.assertFalse(out["ok"])
        self.assertEqual([f["part"] for f in out["failed"]], ["B"])
        self.assertIn("solver fell over", out["failed"][0]["detail"])
        self.assertTrue(out["restored"])
        self.assertPlacements(before)

    def test_named_parts_and_no_ground(self):
        self._lock(self.a, self.b)
        before = self._placements()
        out = asm_api.knock_test(self.asm)  # nothing grounded: it does not solve, nothing is knocked
        self.assertFalse(out["ok"])
        self.assertEqual(out["checked"], 0)
        self.assertEqual(len(out["unchecked"]), 2)
        self.assertPlacements(before)
        self._ground(self.a)
        c = self._box("C", (60, 0, 0))
        self._lock(self.b, c)
        self.assertTrue(asm_api.solve(self.asm)["ok"])  # at rest before the knocks
        out = asm_api.knock_test(self.asm, parts=["C"])
        self.assertEqual((out["parts"], out["checked"], out["returned"]), (2, 1, 1))
        self.assertIsNone(out["all_at_once"])
        with self.assertRaises(ValueError):
            asm_api.knock_test(self.asm, parts=["Nobody"])

    def test_bridge_tool(self):
        self._ground(self.a)
        self._lock(self.a, self.b)
        before = self._placements()
        out = run_tool("asm_knock_test", doc=self.doc.Name, seed=5)
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["checked"], out["returned"], out["seed"]), (1, 1, 5))
        stopped = run_tool("asm_knock_test", doc=self.doc.Name, max_seconds=0)
        self.assertTrue(stopped["cancelled"])
        self.assertFalse(stopped["ok"])
        self.assertPlacements(before)


class TestStaleJoints(_Assembly):
    def test_list_and_fix(self):
        self._ground(self.a)
        j = self._lock(self.a, self.b)
        j.Reference2 = [self.b, ["Face99", "Face99"]]  # an element the box does not have
        before = self._placements()
        out = asm_api.stale_joints(self.asm)
        self.assertEqual([s["joint"] for s in out["stale"]], [j.Name])
        self.assertIn("Face99", out["stale"][0]["missing"])
        self.assertEqual(out["removed"], [])
        self.assertIn("stale", [p["rule"] for p in asm_api.rule_check(self.asm)["problems"]])
        name = j.Name
        out = asm_api.stale_joints(self.asm, fix=True)
        self.assertEqual(out["removed"], [name])
        self.assertEqual(out["moved"], [])
        self.assertIsNone(self.doc.getObject(name))
        self.assertEqual(asm_api.stale_joints(self.asm)["stale"], [])
        self.assertPlacements(before)


if __name__ == "__main__":
    unittest.main()
