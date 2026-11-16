# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of in-context editing (bruceworx_incontext, roadmap ASM-07).

Three files in a temporary folder: a motor mount with two holes, a bracket and an assembly linking both.
The bracket takes the mount's hole edges as a reference and pockets them; moving the mount in the assembly
moves the bracket's holes. Headless: FreeCADCmd -t TestBruceWorxInContext; the GUI part: FreeCAD -t ..."""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App
import Part
import Sketcher

import bruceworx_incontext as ic

HOLES = [(10.0, 10.0), (50.0, 10.0)]
RADIUS = 1.6  # a clearance hole for M3, as in the mount's sketch here (a test value, not a spec)


def plate(folder, name, holes=()):
    """A 60 x 40 x 5 plate body in its own saved file, with through holes at ``holes``."""
    doc = App.newDocument(name)
    doc.saveAs(os.path.join(folder, name + ".FCStd"))
    body = doc.addObject("PartDesign::Body", "Body")
    box = body.newObject("PartDesign::AdditiveBox", "Box")
    box.Length, box.Width, box.Height = 60, 40, 5
    if holes:
        sketch = body.newObject("Sketcher::SketchObject", "HoleSketch")
        sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
        sketch.MapMode = "FlatFace"
        for x, y in holes:
            i = sketch.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), RADIUS))
            sketch.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, i, 3, x))
            sketch.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, i, 3, y))
        pocket = body.newObject("PartDesign::Pocket", "Holes")
        pocket.Profile = sketch
        pocket.Type = "ThroughAll"
        pocket.SideType = "Symmetric"
    doc.recompute()
    doc.save()
    return doc, body


def hole_centres(body):
    return sorted((round(f.Surface.Center.x, 3), round(f.Surface.Center.y, 3))
                  for f in body.Shape.Faces if f.Surface.TypeId == "Part::GeomCylinder")


class Base(unittest.TestCase):
    def setUp(self):
        self.before = set(App.listDocuments())
        self.tmp = tempfile.mkdtemp()
        self.mount, self.mount_body = plate(self.tmp, "IcMount", HOLES)
        self.bracket, self.bracket_body = plate(self.tmp, "IcBracket")
        self.asm = App.newDocument("IcAsm")
        self.asm.saveAs(os.path.join(self.tmp, "IcAsm.FCStd"))
        self.root = self.asm.addObject("Assembly::AssemblyObject", "Assembly")
        self.mount_link = self.asm.addObject("App::Link", "MountLink")
        self.mount_link.setLink(self.mount_body)
        self.root.addObject(self.mount_link)
        self.mount_link.Placement.Base = App.Vector(0, 0, 5)  # sits on the bracket
        self.bracket_link = self.asm.addObject("App::Link", "BracketLink")
        self.bracket_link.setLink(self.bracket_body)
        self.root.addObject(self.bracket_link)
        self.asm.recompute()

    def tearDown(self):
        for name in set(App.listDocuments()) - self.before:
            App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bottom_hole_edges(self):
        return [f"Edge{i}" for i, e in enumerate(self.mount_body.Shape.Edges, 1)
                if e.Curve.TypeId == "Part::GeomCircle" and abs(e.CenterOfMass.z) < 1e-6]

    def bolt_through(self):
        """The bracket's reference of the mount's holes and a pocket through it."""
        paths = [f"MountLink.Body.{e}" for e in self.bottom_hole_edges()]
        binder = ic.reference(self.root, "BracketLink.Body.", paths)
        pocket = self.bracket_body.newObject("PartDesign::Pocket", "BoltHoles")
        pocket.Profile = binder
        pocket.Type = "ThroughAll"
        pocket.SideType = "Symmetric"
        self.bracket.recompute()
        self.assertTrue(pocket.isValid(), pocket.State)
        return binder, pocket


class TestPaths(Base):
    def test_walk_goes_into_linked_files(self):
        objs = ic.walk(self.root, "MountLink.Body.Edge3")
        self.assertEqual(objs, [self.root, self.mount_link, self.mount_body])

    def test_body_path_of_a_face_or_a_component(self):
        self.assertEqual(ic.body_path(self.root, "BracketLink.Body.Face1"), "BracketLink.Body.")
        self.assertEqual(ic.body_path(self.root, "BracketLink."), "BracketLink.Body.")
        self.assertIs(ic.walk(self.root, ic.body_path(self.root, "BracketLink."))[-1], self.bracket_body)

    def test_split_parts_at_the_component(self):
        obj, sub = ic.split(self.root, "BracketLink.Body.", "MountLink.Body.Edge3")
        self.assertIs(obj, self.mount_link)
        self.assertEqual(sub, "Body.Edge3")
        obj, sub = ic.split(self.root, "BracketLink.Body.", "MountLink.")
        self.assertEqual((obj, sub), (self.mount_link, ""))

    def test_the_part_itself_is_refused(self):
        with self.assertRaises(ic.InContextError):
            ic.split(self.root, "BracketLink.Body.", "BracketLink.Body.Face1")
        second = self.asm.addObject("App::Link", "BracketLink001")
        second.setLink(self.bracket_body)
        self.root.addObject(second)
        with self.assertRaises(ic.InContextError):
            ic.split(self.root, "BracketLink.Body.", "BracketLink001.Body.Face1")


    def test_plain_drops_the_mapped_name(self):
        self.assertEqual(ic.plain("BracketLink.Body.;Face1;:He9f,F.Face1"), "BracketLink.Body.Face1")
        self.assertEqual(ic.plain("MountLink.Body.Edge3"), "MountLink.Body.Edge3")


class TestAcceptance(Base):
    def test_bracket_holes_follow_the_moved_motor_mount_across_files(self):
        binder, _pocket = self.bolt_through()
        self.assertIs(binder.Document, self.bracket)
        self.assertEqual(hole_centres(self.bracket_body), HOLES)
        self.assertEqual(ic.status(binder), (ic.OK, "matches its source"))
        self.mount_link.Placement.Base = App.Vector(4, 2, 5)
        self.asm.recompute()  # the assembly's recompute reaches the bracket's file
        self.assertEqual(hole_centres(self.bracket_body), [(14.0, 12.0), (54.0, 12.0)])
        self.assertEqual(ic.status(binder)[0], ic.OK)

    def test_the_bracket_frame_is_taken_off(self):
        binder, _pocket = self.bolt_through()
        self.bracket_link.Placement.Base = App.Vector(-3, 0, 0)
        self.asm.recompute()
        self.assertEqual(hole_centres(self.bracket_body), [(13.0, 10.0), (53.0, 10.0)])

    def test_holes_moved_in_the_mount_file_follow_too(self):
        self.bolt_through()
        sketch = self.mount.getObject("HoleSketch")
        sketch.setDatum(0, App.Units.Quantity("12 mm"))  # the first hole's x
        self.mount.recompute()
        self.asm.recompute()
        self.assertEqual(hole_centres(self.bracket_body), [(12.0, 10.0), (50.0, 10.0)])

    def test_it_survives_save_and_reopen(self):
        self.bolt_through()
        for doc in (self.bracket, self.mount, self.asm):
            doc.save()
        path = self.asm.FileName
        for name in ("IcAsm", "IcBracket", "IcMount"):
            App.closeDocument(name)
        asm = App.openDocument(path)
        self.assertIn("IcBracket", App.listDocuments())
        link = asm.getObject("MountLink")
        link.Placement.Base = App.Vector(-2, 5, 5)
        asm.recompute()
        body = App.getDocument("IcBracket").getObject("Body")
        self.assertEqual(hole_centres(body), [(8.0, 15.0), (48.0, 15.0)])


class TestStates(Base):
    def test_locked_goes_stale_and_update_keeps_it_locked(self):
        binder, _pocket = self.bolt_through()
        ic.lock(binder, True)
        self.mount_link.Placement.Base = App.Vector(6, 0, 5)
        self.asm.recompute()
        self.assertEqual(hole_centres(self.bracket_body), HOLES)  # locked: the holes stay
        state, note = ic.status(binder)
        self.assertEqual(state, ic.STALE)
        self.assertIn("locked", note)
        self.assertEqual([b for b, _s, _n in ic.stale([self.bracket])], [binder])
        self.assertEqual(ic.update(binder)[0], ic.OK)
        self.assertEqual(str(binder.BindMode), "Frozen")
        self.assertEqual(hole_centres(self.bracket_body), [(16.0, 10.0), (56.0, 10.0)])
        ic.lock(binder, False)
        self.mount_link.Placement.Base = App.Vector(0, 0, 5)
        self.asm.recompute()
        self.assertEqual(hole_centres(self.bracket_body), HOLES)

    def test_break_keeps_the_shape_and_drops_the_source(self):
        binder, _pocket = self.bolt_through()
        ic.break_link(binder)
        self.assertEqual(ic.status(binder)[0], ic.DETACHED)
        self.mount_link.Placement.Base = App.Vector(5, 5, 5)
        self.asm.recompute()
        self.bracket.recompute()
        self.assertEqual(hole_centres(self.bracket_body), HOLES)
        with self.assertRaises(ic.InContextError):
            ic.update(binder)

    def test_deleted_source_is_broken(self):
        binder, _pocket = self.bolt_through()
        self.root.removeObject(self.mount_link)
        self.asm.removeObject("MountLink")
        state, _note = ic.status(binder)
        self.assertEqual(state, ic.BROKEN)

    def test_references_lists_only_binders_with_a_context(self):
        binder, _pocket = self.bolt_through()
        plain = self.bracket_body.newObject("PartDesign::SubShapeBinder", "Plain")
        plain.Support = [(self.bracket.getObject("Box"), ["Face1"])]
        self.bracket.recompute()
        self.assertEqual(ic.references([self.bracket]), [binder])


def pump(n=8):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestGui(Base):
    def setUp(self):
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)  # it drops the selection 400 ms after a document shows
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        super().setUp()
        import FreeCADGui as Gui

        ic.install()
        Gui.setActiveDocument(self.asm.Name)
        pump()

    def tearDown(self):
        import FreeCADGui as Gui

        dialog = ic._state["dialog"]
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            ic._state["dialog"] = None
        Gui.Selection.clearSelection()
        try:
            ic.finish()
        except Exception:  # noqa: BLE001
            pass
        super().tearDown()
        pump()

    def test_commands_are_registered(self):
        import FreeCADGui as Gui

        for name in (ic.CMD_EDIT, ic.CMD_REFERENCE, ic.CMD_REFERENCES):
            self.assertIn(name, Gui.listCommands())

    def test_edit_in_context_then_reference_from_selection(self):
        import FreeCADGui as Gui

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.asm.Name, self.root.Name, "BracketLink.Body.Face1")
        Gui.runCommand(ic.CMD_EDIT)
        pump()
        now = ic.active()
        self.assertIsNotNone(now)
        self.assertIs(now[0], self.bracket_body)
        self.assertIs(now[1], self.root)
        Gui.Selection.clearSelection()
        for edge in self.bottom_hole_edges():
            Gui.Selection.addSelection(self.asm.Name, self.root.Name, f"MountLink.Body.{edge}")
        pump()
        binder = ic.reference_from_selection()
        self.assertIs(binder.Document, self.bracket)
        self.assertEqual(ic.status(binder)[0], ic.OK)
        self.assertEqual(len(binder.Support[0][1]), 2)
        Gui.runCommand(ic.CMD_EDIT)  # again: finish
        pump()
        self.assertIsNone(ic.active())

    def test_stock_activation_through_the_link_is_understood(self):
        import FreeCADGui as Gui

        Gui.getDocument(self.asm.Name).ActiveView.setActiveObject("pdbody", self.root, "BracketLink.")
        self.assertEqual(ic.active(), (self.bracket_body, self.root, "BracketLink.Body."))

    def test_dialog_lists_and_locks(self):
        binder, _pocket = self.bolt_through()
        dialog = ic.show_dialog()
        pump()
        self.assertEqual(dialog.rows, [binder])
        self.assertTrue(dialog.tree.topLevelItem(0).text(4).startswith(ic.OK))
        dialog.tree.topLevelItem(0).setSelected(True)
        dialog.act("lock")
        self.assertEqual(str(binder.BindMode), "Frozen")
        self.mount_link.Placement.Base = App.Vector(3, 0, 5)
        self.asm.recompute()
        dialog.act("refresh")
        self.assertTrue(dialog.tree.topLevelItem(0).text(4).startswith(ic.STALE))
        dialog.tree.topLevelItem(0).setSelected(True)
        dialog.act("update")
        self.assertTrue(dialog.tree.topLevelItem(0).text(4).startswith(ic.OK))
        self.assertEqual(hole_centres(self.bracket_body), [(13.0, 10.0), (53.0, 10.0)])
