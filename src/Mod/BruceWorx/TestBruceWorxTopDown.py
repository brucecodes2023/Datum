# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of top-down design across files (bruceworx_parameters_topdown, roadmap MOD-02).

Headless: FreeCADCmd -t TestBruceWorxTopDown. The GUI class (automatic rebuild, commands) needs the app:
FreeCAD -t TestBruceWorxTopDown"""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App
import Part
import Sketcher

import bruceworx_parameters as P
import bruceworx_parameters_topdown as td

PARTS = ("TdBracket", "TdPlate", "TdCover")


def pump(n=10):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


class Product(unittest.TestCase):
    """A top file (TdTop: Parameters sheet, Skeleton sketch, an assembly linking the parts) and three part
    files whose Bodies bind the skeleton and read the sheet."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-topdown-"))
        self.before = set(App.listDocuments())
        top = self.top = App.newDocument("TdTop")
        sk = self.skeleton = top.addObject("Sketcher::SketchObject", "Skeleton")
        sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(40, 0, 0)))
        sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40))
        sk.renameConstraint(0, "span")
        sheet = self.sheet = P.make_sheet(top)
        sheet.set("A2", "plate_t")
        sheet.set("B2", "5 mm")
        sheet.setAlias("B2", "plate_t")
        sheet.set("A3", "unused_w")
        sheet.set("B3", "12 mm")
        sheet.setAlias("B3", "unused_w")
        top.recompute()
        top.saveAs(os.path.join(self.tmp, "TdTop.FCStd"))
        self.asm = top.addObject("App::Part", "Product")
        self.parts = {}
        for name in PARTS:
            doc = App.newDocument(name)
            doc.saveAs(os.path.join(self.tmp, f"{name}.FCStd"))
            body = doc.addObject("PartDesign::Body", "Body")
            box = body.newObject("PartDesign::AdditiveBox", "Box")
            box.Width = 10
            box.setExpression("Height", "TdTop#Parameters.plate_t")
            doc.recompute()
            doc.save()
            link = top.addObject("App::Link", f"Link{name}")
            link.setLink(body)
            self.asm.addObject(link)
            self.parts[name] = (doc, body, box, link)
        top.recompute()
        top.save()

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name not in self.before:
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTopDown(Product):
    def link_all(self):
        made = td.link_skeleton_to(self.skeleton, [link for _d, _b, _x, link in self.parts.values()])
        for doc, _body, box, _link in self.parts.values():
            box.setExpression("Length", "TdTop#Skeleton.Constraints.span")
            doc.recompute()
        return made

    def test_skeleton_and_sheet_change_reach_three_part_files_in_one_recompute(self):
        """MOD-02 acceptance: a skeleton change propagates to three linked part files in one recompute."""
        made = self.link_all()
        self.assertEqual(len(made), 3)
        for doc, body, box, _link in self.parts.values():
            binder = doc.getObject("Skeleton")
            self.assertIn(binder, body.Group)
            self.assertAlmostEqual(binder.Shape.BoundBox.XLength, 40.0)
            self.assertAlmostEqual(box.Length.Value, 40.0)
        self.skeleton.setDatum("span", App.Units.Quantity("60 mm"))
        self.sheet.set("B2", "7 mm")
        self.top.recompute()  # the one recompute: FreeCAD 1.1 rebuilds the open files that read the top file
        for doc, _body, box, _link in self.parts.values():
            self.assertAlmostEqual(box.Length.Value, 60.0, msg=doc.Name)
            self.assertAlmostEqual(box.Height.Value, 7.0, msg=doc.Name)
        rebuilt = td.propagate(self.top)  # Rebuild From Top: the same files, forced
        self.assertEqual(rebuilt, sorted(PARTS))
        for doc, _body, box, _link in self.parts.values():
            self.assertAlmostEqual(doc.getObject("Skeleton").Shape.BoundBox.XLength, 60.0, msg=doc.Name)
            self.assertAlmostEqual(box.Length.Value, 60.0, msg=doc.Name)
            self.assertAlmostEqual(box.Height.Value, 7.0, msg=doc.Name)
            self.assertAlmostEqual(box.Shape.Volume, 60 * 10 * 7, places=3, msg=doc.Name)
            self.assertTrue(box.isValid())

    def test_assembly_links_are_not_readers(self):
        self.assertEqual(sorted(o.Document.Name for o in td.readers(self.top)), sorted(PARTS))  # the Box expressions
        doc, body, _box, _link = self.parts["TdPlate"]
        self.assertEqual(td.readers(doc), [])  # the top file's links to the plate do not read it top-down

    def test_link_skeleton_reuses_the_binder_and_needs_saved_files(self):
        _doc, body, _box, link = self.parts["TdBracket"]
        first = td.link_skeleton(self.skeleton, body)
        self.assertIs(td.link_skeleton(self.skeleton, body), first)
        loose = App.newDocument("TdLoose")
        loose_body = loose.addObject("PartDesign::Body", "Body")
        with self.assertRaises(td.TopDownError):
            td.link_skeleton(self.skeleton, loose_body)
        with self.assertRaises(td.TopDownError):
            td.link_skeleton_to(self.skeleton, [self.sheet])

    def test_used_in_column(self):
        self.link_all()
        found = td.used_in(self.sheet)
        self.assertEqual(sorted(found["plate_t"]), sorted(f"{n}: Body (Box)" for n in PARTS))
        self.assertEqual(found["unused_w"], [])
        changed = td.update_used_in(self.sheet)
        self.assertEqual(changed, [1, 2])  # the header and plate_t's row; unused_w stays empty
        self.assertEqual(td._cell_text(self.sheet, "D1"), "Used in")
        self.assertEqual(td._cell_text(self.sheet, "D2"), ", ".join(found["plate_t"]))
        self.assertNotIn("Touched", self.sheet.State)
        self.assertEqual(td.update_used_in(self.sheet), [])  # nothing new
        self.assertEqual([a for _c, a in P.sheet_aliases(self.sheet)], ["plate_t", "unused_w"])  # D is not a parameter

    def test_used_in_names_the_same_file_and_drawings_plainly(self):
        box = self.top.addObject("Part::Box", "Spacer")
        box.setExpression("Height", "Parameters.unused_w")
        self.top.recompute()
        self.assertEqual(td.used_in(self.sheet)["unused_w"], ["Spacer"])

    def test_check_references(self):
        self.link_all()
        doc, _body, box, _link = self.parts["TdCover"]
        self.assertEqual(td.broken_references(doc), [])
        box.setExpression("Width", "TdTop#Parameters.plate_t * 2")
        doc.recompute()
        self.sheet.setAlias("B2", "")  # the parameter is renamed away
        self.top.recompute()
        rows = td.broken_references(doc)
        self.assertTrue(rows)
        self.assertTrue(all(r["object"] == "Box" for r in rows))
        self.assertIn("Width", [r["property"] for r in rows])
        self.assertIn("cannot be read", td.report_text(doc, rows))

    def test_a_missing_top_file_is_reported(self):
        self.link_all()
        for doc, _b, _x, _l in self.parts.values():
            doc.save()
        self.top.save()
        path = self.parts["TdPlate"][0].FileName
        for name in [*PARTS, "TdTop"]:
            App.closeDocument(name)
        os.rename(os.path.join(self.tmp, "TdTop.FCStd"), os.path.join(self.tmp, "Renamed.FCStd"))
        doc = App.openDocument(path)
        rows = td.broken_references(doc)
        reasons = " | ".join(r["reason"] for r in rows)
        self.assertIn("file not found", reasons)
        self.assertIn("its source did not load", reasons)
        self.assertTrue(any(r["property"] == "Height" for r in rows), rows)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestTopDownGui(Product):
    def setUp(self):
        prefs = App.ParamGet(td.PREFS_PATH)
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        super().setUp()
        td.install()
        pump()

    def tearDown(self):
        box = td._state["box"]
        if box is not None:
            box.close()
            td._state["box"] = None
        super().tearDown()

    def test_commands_are_registered(self):
        import FreeCADGui as Gui

        for cmd in (td.CMD_LINK, td.CMD_CHECK, td.CMD_REBUILD):
            self.assertIn(cmd, Gui.listCommands())

    def test_editing_the_top_file_rebuilds_the_parts(self):
        import FreeCADGui as Gui

        for _doc, _body, box, _link in self.parts.values():
            self.assertAlmostEqual(box.Height.Value, 5.0)
        Gui.setActiveDocument(self.top.Name)
        self.sheet.set("B2", "9 mm")
        self.top.recompute()
        pump(15)
        for name, (_doc, _body, box, _link) in self.parts.items():
            self.assertAlmostEqual(box.Height.Value, 9.0, msg=name)

    def test_link_skeleton_command_on_a_selection(self):
        import FreeCADGui as Gui

        Gui.setActiveDocument(self.top.Name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.top.Name, self.skeleton.Name)
        for _doc, _body, _box, link in self.parts.values():
            Gui.Selection.addSelection(self.top.Name, link.Name)
        Gui.runCommand(td.CMD_LINK)
        pump()
        for name, (doc, body, _box, _link) in self.parts.items():
            binder = doc.getObject("Skeleton")
            self.assertIsNotNone(binder, name)
            self.assertIn(binder, body.Group)
            self.assertEqual(doc.UndoNames[0], "Link Skeleton")

    def test_used_in_follows_a_new_expression(self):
        doc, _body, box, _link = self.parts["TdPlate"]
        box.setExpression("Width", "TdTop#Parameters.unused_w")
        pump(20)  # the column is written 300 ms after the edit
        self.assertEqual(td._cell_text(self.sheet, "D3"), "TdPlate: Body (Box)")

    def test_check_references_command(self):
        import FreeCADGui as Gui

        Gui.setActiveDocument(self.top.Name)
        Gui.runCommand(td.CMD_CHECK)
        pump()
        box = td._state["box"]
        self.assertIsNotNone(box)
        self.assertIn("resolves", box.informativeText())
        self.assertEqual(td._cell_text(self.sheet, "D1"), "Used in")


if __name__ == "__main__":
    unittest.main()
