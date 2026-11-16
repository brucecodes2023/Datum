# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the COTS library and insert (roadmap ROB-01, bruceworx_cots).

    FreeCADCmd -t TestBruceWorxCots          (the dialog test needs the GUI: FreeCAD -t TestBruceWorxCots)
"""

import json
import os
import shutil
import tempfile
import time
import unittest

import FreeCAD as App

import bruceworx_cots as bc


def pump(n=6):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


class TestLibrary(unittest.TestCase):
    def test_every_number_has_a_source(self):
        table = bc.load(vault_root="")
        self.assertIn("nema17-42x38", table)
        self.assertIn("emax-eco2-2207", table)
        for item in table.values():
            self.assertTrue(item.values, item.key)
            for value in item.values.values():
                self.assertTrue(value.source.get("title"), f"{item.key}.{value.field}")
                self.assertTrue(value.source.get("read"), f"{item.key}.{value.field}")
                self.assertIsInstance(value.checked, bool)

    def test_patterns(self):
        table = bc.load(vault_root="")
        self.assertEqual(table["nema17-42x38"].holes(), [(-15.5, -15.5), (15.5, -15.5), (15.5, 15.5), (-15.5, 15.5)])
        self.assertEqual(table["emax-eco2-2207"].holes()[2], (8.0, 8.0))
        self.assertEqual(table["nema17-42x38"].thread, "M3")
        self.assertEqual(table["motor-1404-9x9"].thread, "M2")
        self.assertIn("mass_g", table["nema17-42x38"].unchecked())  # read from a search excerpt, so not checked
        self.assertIn("[not checked]", table["nema17-42x38"].describe())

    def write(self, folder, parts, sources=None):
        data = {"schema": bc.SCHEMA, "version": 1, "parts": parts,
                "sources": sources if sources is not None else {"s": {"title": "a drawing", "read": "2026-11-12"}}}
        path = os.path.join(folder, bc.LIBRARY_FILE)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_a_number_without_a_source_is_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            part = {"key": "x", "geometry": {"shape": "box", "size": {"value": [1, 2, 3], "source": "nowhere"}},
                    "mount": {"pitch": {"value": [1, 1], "source": "s"}, "thread": {"value": "M3", "source": "s"}}}
            with self.assertRaises(ValueError) as ctx:
                bc.load(vault_root="", builtin=self.write(tmp, [part]))
            self.assertIn("names no known source", str(ctx.exception))
            part["geometry"]["size"] = [1, 2, 3]  # a bare number
            with self.assertRaises(ValueError):
                bc.load(vault_root="", builtin=self.write(tmp, [part]))
        finally:
            shutil.rmtree(tmp)

    def test_a_sourced_cg_goes_on_the_part(self):
        tmp = tempfile.mkdtemp()
        doc = App.newDocument("CotsCg")
        try:
            v = {"value": [20, 20], "source": "s", "checked": True}
            part = {"key": "cg-test", "name": "Motor with a CG",
                    "mass_g": {"value": 50, "source": "s", "checked": True},
                    "cg": {"value": [0, 0, 12.5], "source": "s", "checked": True},
                    "geometry": {"shape": "cylinder", "size": {"value": [27.9, 31.0], "source": "s", "checked": True}},
                    "mount": {"pitch": v, "thread": {"value": "M3", "source": "s", "checked": True}}}
            self.write(tmp, [part])
            made = bc.make_part(doc, bc.load(vault_root=tmp)["cg-test"])
            self.assertEqual(tuple(made.DatasheetCG), (0.0, 0.0, 12.5))
            plain = bc.make_part(doc, bc.load(vault_root="")["emax-eco2-2207"])
            self.assertNotIn("DatasheetCG", plain.PropertiesList)  # no sourced CG: none is made up
        finally:
            App.closeDocument(doc.Name)
            shutil.rmtree(tmp)

    def test_vault_layer_replaces_and_adds(self):
        tmp = tempfile.mkdtemp()
        try:
            v = {"value": [20, 20], "source": "s", "checked": True}
            part = {"key": "emax-eco2-2207", "name": "Measured 2207",
                    "geometry": {"shape": "cylinder", "size": {"value": [27.9, 31.0], "source": "s", "checked": True}},
                    "mount": {"pitch": v, "thread": {"value": "M3", "source": "s", "checked": True}}}
            self.write(tmp, [part, dict(part, key="other")])
            table = bc.load(vault_root=tmp)
            self.assertEqual(table["emax-eco2-2207"].name, "Measured 2207")
            self.assertEqual(table["emax-eco2-2207"].unchecked(), [])
            self.assertEqual(table["emax-eco2-2207"].layer, "vault")
            self.assertIn("other", table)
            self.assertIn("nema17-42x38", table)
        finally:
            shutil.rmtree(tmp)


class Base(unittest.TestCase):
    def setUp(self):
        # never the user's open vault: the vault tests pass their own session
        real, bc._session = bc._session, lambda: None
        self.addCleanup(setattr, bc, "_session", real)
        self.tmp = tempfile.mkdtemp()
        self.table = bc.load(vault_root="")
        self.doc = App.newDocument("CotsAsm")
        self.doc.UndoMode = 1
        self.name = self.doc.Name
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.asm.newObject("Assembly::JointGroup", "Joints")
        self.body = self.doc.addObject("PartDesign::Body", "Plate")
        self.asm.addObject(self.body)
        self.box = self.body.newObject("PartDesign::AdditiveBox", "Box")
        self.box.Length, self.box.Width, self.box.Height = 120, 60, 4
        self.doc.recompute()
        import JointObject
        import UtilsAssembly

        ground = UtilsAssembly.getJointGroup(self.asm).newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, self.body)
        self.doc.recompute()
        self.opened = set(App.listDocuments())

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name == self.name or name.startswith("Cots_"):
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def top(self):
        return next(f"Face{i}" for i, f in enumerate(self.box.Shape.Faces, 1)  # the box's own face: later holes renumber the body's
                    if abs(f.CenterOfMass.z - 4) < 1e-6 and abs(f.normalAt(0, 0).z - 1) < 1e-6)

    def seat(self, key, offset):
        sketch, hole, seat = bc.make_seat(self.body, (self.box, self.top()), self.table[key], offset=offset)
        self.doc.recompute()
        self.assertTrue(hole.isValid(), hole.State)
        return sketch, hole, seat

    def mount_global(self, component):
        ref = bc.mount_ref(component)
        return component.getSubObject(ref[1][0], retType=3)


class TestPartAndSeat(Base):
    def test_part_has_envelope_mass_and_mount(self):
        part = bc.make_part(self.doc, self.table["nema17-42x38"])
        self.doc.recompute()
        solid = next(o for o in part.Group if o.isDerivedFrom("Part::Feature") and not o.isDerivedFrom("App::LocalCoordinateSystem"))
        box = solid.Shape.BoundBox
        self.assertAlmostEqual(box.XLength, 42.3, places=6)
        self.assertAlmostEqual(box.ZMin, 0, places=6)   # the mounting face is the Mount frame's XY plane
        self.assertAlmostEqual(box.ZMax, 38, places=6)
        self.assertAlmostEqual(part.DatasheetMass.getValueAs("g").Value, 285, places=6)
        self.assertEqual(part.MountThread, "M3")
        self.assertEqual(len(part.MountHoles), 4)
        self.assertTrue(bc.is_cots(part))
        self.assertIn("mass_g", part.CotsUnchecked)
        from bruceworx import partprops

        kg, source = partprops.typed_mass(part)
        self.assertAlmostEqual(kg, 0.285, places=9)
        self.assertEqual(source, "datasheet")
        info = bc.info_of(part)
        self.assertEqual((info["thread"], info["mass_g"], len(info["holes"])), ("M3", 285, 4))
        self.assertIsNone(bc.info_of(self.body))

    def test_seat_makes_the_clearance_holes(self):
        volume = self.body.Shape.Volume
        sketch, hole, seat = self.seat("nema17-42x38", (-25, 0))
        self.assertEqual(hole.HolePreset, "Clearance M3")
        self.assertAlmostEqual(hole.Diameter.Value, 3.4, places=6)
        removed = volume - self.body.Shape.Volume
        self.assertAlmostEqual(removed, 4 * 3.14159265 * 1.7 ** 2 * 4, delta=0.05)
        centres = sorted((round(g.Center.x, 3), round(g.Center.y, 3)) for g in sketch.Geometry)
        self.assertEqual(centres, [(-15.5, -15.5), (-15.5, 15.5), (15.5, -15.5), (15.5, 15.5)])
        self.assertTrue(bc.is_seat(seat))
        self.assertIs(bc.seat_of(hole), seat)
        self.assertIs(bc.seat_of(sketch), seat)
        self.assertAlmostEqual(seat.Placement.Base.z, 4, places=6)
        check = bc.check_seat(seat)
        self.assertTrue(check["ok"], check)
        hole.Diameter = 3.0
        self.doc.recompute()
        self.assertIn("needs 3.4 mm clearance", bc.check_seat(seat)["problems"][0])


class TestAcceptance(Base):
    """ROB-01: "NEMA17 and 2207 motor inserted and jointed by mounting LCS"."""

    def both(self):
        _s1, _h1, seat_a = self.seat("nema17-42x38", (-30, 0))
        _s2, _h2, seat_b = self.seat("emax-eco2-2207", (35, 0))
        out = []
        for key, seat in (("nema17-42x38", seat_a), ("emax-eco2-2207", seat_b)):
            component = bc.insert(self.asm, self.table[key], App.Placement(App.Vector(200, 90, 70), App.Rotation(App.Vector(1, 1, 0), 140)))
            self.doc.recompute()
            joint, code = bc.join(self.asm, component, seat)
            self.doc.recompute()
            out.append((component, seat, joint, code))
        return out

    def check(self, made):
        import bruceworx_api_asm as api

        self.assertEqual(self.asm.solve(), 0)
        for component, seat, joint, code in made:
            self.assertEqual(code, 0, component.Label)
            self.assertEqual(joint.JointType, "Fixed")
            mount = self.mount_global(component)
            want = self.body.Placement.multiply(seat.Placement)
            self.assertLess((mount.Base - want.Base).Length, 1e-6, component.Label)
            self.assertLess(mount.Rotation.multVec(App.Vector(0, 0, 1)).sub(App.Vector(0, 0, 1)).Length, 1e-6)
        nema, eco = made[0][0], made[1][0]
        self.assertLess((self.mount_global(nema).Base - App.Vector(30, 30, 4)).Length, 1e-6)   # face centre (60, 30) - 30
        self.assertLess((self.mount_global(eco).Base - App.Vector(95, 30, 4)).Length, 1e-6)
        self.assertEqual(len(api.joints(self.asm)), 2)
        report = api.rule_check(self.asm, solve_too=True)
        self.assertEqual(report.get("solve", {}).get("code", 0), 0, report)
        # knocked off its seat, the solver brings each motor back
        nema.Placement = App.Placement(App.Vector(5, -40, 60), App.Rotation(App.Vector(0, 1, 0), 25))
        self.assertEqual(self.asm.solve(), 0)
        self.assertLess((self.mount_global(nema).Base - App.Vector(30, 30, 4)).Length, 1e-5)

    def test_in_an_unsaved_assembly_the_parts_are_in_the_document(self):
        made = self.both()
        self.assertTrue(all(c.TypeId == "App::Part" for c, _s, _j, _c in made))
        self.check(made)

    def test_in_a_saved_assembly_each_part_is_its_own_file_and_linked(self):
        self.doc.saveAs(os.path.join(self.tmp, "asm.FCStd"))
        made = self.both()
        self.assertTrue(all(c.TypeId == "App::Link" for c, _s, _j, _c in made))
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, bc.FOLDER))),
                         ["emax-eco2-2207.FCStd", "nema17-42x38.FCStd"])
        self.check(made)
        again = bc.insert(self.asm, self.table["nema17-42x38"])  # the same file is linked again, not remade
        self.assertIs(again.getLinkedObject(True), made[0][0].getLinkedObject(True))
        self.assertEqual(bc.info_of(again)["key"], "nema17-42x38")

    def test_wrong_things_are_refused_in_plain_words(self):
        _s, _h, seat = self.seat("motor-1404-9x9", (0, 0))
        with self.assertRaises(bc.CotsError):
            bc.mount_ref(self.body)
        component = bc.insert(self.asm, self.table["motor-1404-9x9"])
        self.doc.recompute()
        with self.assertRaises(bc.CotsError):
            bc.join(self.asm, component, bc._mount_of(component))  # its own frame is not a seat


class _VaultSession:
    """What the vault client's Session gives bruceworx_cots: .vault, .root, .work, refresh()."""

    def __init__(self, folder):
        from bruceworx import pdm_workspace as ws
        from bruceworx import vault_setup as vs

        setup = vs.VaultSetup(name="Bought", folder=folder,
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")]))
        self.vault = vs.create_vault(setup, actor="test@mac")
        self.work = ws.Workspace(self.vault)

    @property
    def root(self):
        return self.vault.root

    def refresh(self):
        self.vault.refresh()


class TestVaultItem(Base):
    """With a vault open a bought part is a vault COTS item, as a fastener is, and keeps its maker's number."""

    def setUp(self):
        super().setUp()
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        sync = prefs.GetBool("VaultStateSync", True)
        prefs.SetBool("VaultStateSync", False)
        self.addCleanup(prefs.SetBool, "VaultStateSync", sync)
        self.s = _VaultSession(os.path.join(self.tmp, "vault"))
        self.doc.saveAs(os.path.join(self.s.work.root, "asm.FCStd"))

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name not in self.opened and name != self.name:
                App.closeDocument(name)
        super().tearDown()

    def test_insert_makes_one_vault_item_with_the_makers_number(self):
        from bruceworx import hardware as hw

        item = self.table["emax-eco2-2207"]
        first = bc.insert(self.asm, item, session=self.s)
        second = bc.insert(self.asm, item, session=self.s)
        self.doc.recompute()
        cots = [pn for pn, it in self.s.vault.items.items() if it.item_type == "cots"]
        self.assertEqual(len(cots), 1, cots)  # the second insert reuses the item
        pn = cots[0]
        self.assertEqual(self.s.vault.items[pn].description, item.name)
        self.assertEqual(getattr(self.s.vault.items[pn], "category", "Hardware"), "Hardware")
        for link in (first, second):
            self.assertEqual(link.TypeId, "App::Link")
            part = link.LinkedObject
            self.assertEqual(part.PartNumber, pn)
            self.assertEqual(part.ItemType, "cots")
            self.assertEqual(part.Manufacturer, item.manufacturer)
            self.assertEqual(part.ManufacturerPartNumber, item.mpn)
            self.assertEqual(getattr(part, bc.KEY_PROP), item.key)
            self.assertTrue(bc.is_cots(part))
        entry = hw.load_catalogue(self.s.root)[bc.catalogue_key(item)]
        self.assertEqual((entry["part_number"], entry["manufacturer"], entry["mpn"]), (pn, item.manufacturer, item.mpn))

    def test_each_entry_is_its_own_item(self):
        a, _ = bc.ensure_item(self.s, self.table["emax-eco2-2207"])
        b, made = bc.ensure_item(self.s, self.table["nema17-42x38"])
        again, made_again = bc.ensure_item(self.s, self.table["nema17-42x38"])
        self.assertNotEqual(a, b)
        self.assertTrue(made)
        self.assertEqual((again, made_again), (b, False))

    def test_unsaved_assembly_is_refused(self):
        doc = App.newDocument("CotsUnsaved")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        with self.assertRaises(bc.CotsError):
            bc.insert(asm, self.table["emax-eco2-2207"], session=self.s)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestGui(Base):
    def setUp(self):
        # auto-activate drops the selection 400 ms after a document is shown: not under test here
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        auto = prefs.GetBool("AutoActivateAssembly", True)
        prefs.SetBool("AutoActivateAssembly", False)
        self.addCleanup(prefs.SetBool, "AutoActivateAssembly", auto)
        super().setUp()
        bc.install()
        pump()

    def tearDown(self):
        if bc._state["dialog"] is not None:
            bc._state["dialog"].close()
        super().tearDown()
        pump()

    def test_commands_are_registered(self):
        import FreeCADGui as Gui

        for name in bc.COMMANDS:
            self.assertIn(name, Gui.listCommands())

    def test_dialog_lists_the_library_and_says_what_is_not_checked(self):
        dialog = bc.pick("Insert bought part", "Insert", lambda item: None)
        pump()
        self.assertEqual(dialog.items.count(), len(bc.load()))
        dialog.search.setText("2207")
        self.assertEqual(dialog.items.count(), 1)
        self.assertIn("Not checked against the maker's drawing", dialog.details.toPlainText())
        self.assertIn("emax-usa.com", json.dumps(dialog.chosen().as_dict()))
        dialog.reject()

    def test_insert_on_a_selected_seat_is_one_undo_step(self):
        import FreeCADGui as Gui

        _s, hole, seat = self.seat("emax-eco2-2207", (10, 5))
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, self.asm.Name, f"{self.body.Name}.{seat.Name}.")
        pump()
        self.assertIs(bc._selected_seat(), seat)
        before = len(self.doc.UndoNames)
        component, joint, code = bc.insert_chosen(self.table["emax-eco2-2207"], self.asm, bc._selected_seat())
        pump()
        self.assertEqual(code, 0)
        self.assertLess((self.mount_global(component).Base - App.Vector(70, 35, 4)).Length, 1e-6)
        self.assertEqual(self.doc.UndoNames[0], "Insert Brushless motor EMAX ECO II 2207")
        self.assertEqual(len(self.doc.UndoNames), before + 1)
