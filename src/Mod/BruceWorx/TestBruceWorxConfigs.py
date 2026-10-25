# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configurations (MOD-03), headless: the table on FreeCAD's own configuration binding, rows and
columns, switching, Check All, CSV, instances in an assembly (Link CopyOnChange), component
suppression, saving and reopening. ``BW_CONFIGS_DIR`` keeps the files."""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_configs as bc


def _rect(body, name, x0, y0, x1, y1):
    import Part
    import Sketcher

    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    pts = [App.Vector(x0, y0, 0), App.Vector(x1, y0, 0), App.Vector(x1, y1, 0), App.Vector(x0, y1, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    return sk


def make_bracket(folder, name="Bracket", pn="BW-1001"):
    """A part like a vault file: App::Part (PartNumber, Description) with a Body: a plate whose length
    is the user parameter ``length`` (Parameters sheet) and a boss on top. Saved in ``folder``."""
    import bruceworx_parameters as bp

    doc = App.newDocument(name)
    part = doc.addObject("App::Part", "Part")
    part.Label = "Bracket"
    for prop in ("PartNumber", "Description"):
        part.addProperty("App::PropertyString", prop, "BruceWorx")
    part.PartNumber = pn
    part.Description = "Bracket"
    body = doc.addObject("PartDesign::Body", "Body")
    part.addObject(body)
    plate = body.newObject("PartDesign::Pad", "Plate")
    plate.Profile = _rect(body, "PlateSketch", 0, 0, 20, 10)
    plate.Length = 80
    doc.recompute()
    boss_sk = _rect(body, "BossSketch", 2, 2, 8, 8)
    boss_sk.AttachmentSupport = (plate, ["Face6"])
    boss = body.newObject("PartDesign::Pad", "Boss")
    boss.Profile = boss_sk
    boss.Length = 6
    doc.recompute()
    params = bp.make_sheet(doc)
    params.set("A2", "length")
    params.set("B2", "80 mm")
    params.setAlias("B2", "length")
    doc.recompute()
    plate.setExpression("Length", "Parameters.length")
    doc.recompute()
    doc.saveAs(os.path.join(folder, name + ".FCStd"))
    return doc, part


def configure_bracket(doc):
    """Short / Standard / Long: 50 / 80 / 120 mm, the boss off in Short, Short and Standard with
    P/Ns of their own (Long is the file's item)."""
    bc.setup(doc, ("Short", "Standard", "Long"))
    bc.add_parameter_column(doc, "length")
    bc.add_suppression_column(doc, doc.getObject("Boss"))
    for name, length, pn, off in (("Short", "50 mm", "BW-1001-S", "1"), ("Standard", "80 mm", "BW-1001-M", "0"),
                                  ("Long", "120 mm", "", "0")):
        bc.set_cell(doc, name, "length", length)
        bc.set_cell(doc, name, "PartNumber", pn)
        bc.set_cell(doc, name, "Description", f"Bracket {length.split()[0]}")
        bc.set_cell(doc, name, "sup_Boss", off)
    bc.activate(doc, "Standard")


def _height(obj):
    return round(obj.Shape.BoundBox.ZLength, 6)


class Helpers(unittest.TestCase):
    def test_columns_and_cells(self):
        self.assertEqual([bc.col_name(i) for i in (0, 1, 25, 26, 27)], ["A", "B", "Z", "AA", "AB"])
        self.assertEqual([bc.col_index(n) for n in ("A", "Z", "AA", "AB")], [0, 25, 26, 27])
        self.assertEqual(bc.cell(1, 3), "B3")
        self.assertEqual(bc.column_kind("PartNumber"), bc.PN_KIND)
        self.assertEqual(bc.column_kind("sup_Boss"), bc.SUPPRESS)
        self.assertEqual(bc.column_kind("cfg_Link"), bc.INSTANCE)
        self.assertEqual(bc.column_kind("length"), bc.PARAM)
        self.assertEqual(bc.header_for("sup_Boss", "Boss"), "Suppress Boss")

    def test_text_and_values(self):
        self.assertEqual(bc.text_cell("80"), "'80")
        self.assertEqual(bc.text_cell("=FilePartNumber"), "=FilePartNumber")
        self.assertEqual(bc.text_cell(""), "=<<>>")
        self.assertEqual(bc.plain("'"), "")
        self.assertEqual(bc.plain("'BW-1"), "BW-1")
        self.assertEqual(bc.literal("=50 mm"), "50 mm")
        self.assertEqual(bc.literal("=a * 2"), "=a * 2")
        self.assertTrue(bc.valid_name("Long"))
        self.assertFalse(bc.valid_name("'x") or bc.valid_name("") or bc.valid_name("a|b"))
        self.assertEqual([bc.suppress_value(v) for v in ("1", "yes", "Suppressed", "0", "", "no")],
                         ["1", "1", "1", "0", "0", "0"])
        self.assertEqual(bc.alias_for("Pad", "Length"), "Pad_Length")
        self.assertIn("hiddenref(Part.Configuration)+4", bc.bind_expression("Part", "E"))

    def test_csv_round_trip(self):
        rows = [("Short", {"length": "50 mm", "PartNumber": "'BW-1"}), ("Long", {"length": "120 mm"})]
        text = bc.rows_to_csv(["PartNumber", "length"], rows)
        cols, back = bc.rows_from_csv(text)
        self.assertEqual(cols, ["PartNumber", "length"])
        self.assertEqual(back[0], ("Short", {"PartNumber": "'BW-1", "length": "50 mm"}))
        self.assertEqual(back[1][1]["PartNumber"], "")
        with self.assertRaises(bc.ConfigError):
            bc.rows_from_csv("Name,length\nx,1\n")


class PartConfigurations(unittest.TestCase):
    def setUp(self):
        self.folder = os.environ.get("BW_CONFIGS_DIR") or tempfile.mkdtemp(prefix="bwcfg")
        os.makedirs(self.folder, exist_ok=True)
        self.doc, self.part = make_bracket(self.folder)

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if not os.environ.get("BW_CONFIGS_DIR"):
            shutil.rmtree(self.folder, ignore_errors=True)

    def test_setup_makes_a_native_table(self):
        sheet = bc.setup(self.doc, ("Short", "Long"))
        self.assertTrue(bc.has_configurations(self.doc))
        self.assertIs(bc.main_object(self.doc), self.part)
        self.assertEqual(self.part.getEnumerationsOfProperty("Configuration"), ["Short", "Long"])
        self.assertIn("CopyOnChange", self.part.getPropertyStatus("Configuration"))
        self.assertEqual(bc.aliases(self.doc), ["PartNumber", "Description"])
        self.assertEqual(bc.file_part_number(self.doc), "BW-1001")
        self.assertEqual(sheet.get("A3"), "Short")
        self.assertEqual(self.part.PartNumber, "BW-1001")  # rows without their own P/N are the file's
        with self.assertRaises(bc.ConfigError):
            bc.setup(self.doc)

    def test_switching_rows(self):
        configure_bracket(self.doc)
        body, boss = self.doc.getObject("Body"), self.doc.getObject("Boss")
        seen = {}
        for name in ("Short", "Standard", "Long"):
            bc.activate(self.doc, name)
            seen[name] = (_height(body), self.part.PartNumber, self.part.Description, bool(boss.Suppressed))
        self.assertEqual(seen["Short"], (50.0, "BW-1001-S", "Bracket 50", True))
        self.assertEqual(seen["Standard"], (86.0, "BW-1001-M", "Bracket 80", False))
        self.assertEqual(seen["Long"], (126.0, "BW-1001", "Bracket 120", False))
        params = self.doc.getObject("Parameters")
        self.assertEqual(params.getContents("B2"), "=Configurations.length")
        self.assertEqual(bc.part_numbers(self.doc), [
            ("Short", "BW-1001-S", "Bracket 50", True), ("Standard", "BW-1001-M", "Bracket 80", True),
            ("Long", "BW-1001", "Bracket 120", False)])

    def test_columns_come_and_go(self):
        configure_bracket(self.doc)
        plate = self.doc.getObject("PlateSketch")
        alias = bc.add_dimension_column(self.doc, self.doc.getObject("Boss"), "Length")
        self.assertEqual(alias, "Boss_Length")
        bc.set_cell(self.doc, "Long", alias, "10 mm")
        bc.activate(self.doc, "Long")
        self.assertEqual(_height(self.doc.getObject("Body")), 130.0)
        with self.assertRaises(bc.ConfigError):  # bound to the table already
            bc.add_dimension_column(self.doc, self.doc.getObject("Plate"), "Length")
        with self.assertRaises(bc.ConfigError):
            bc.add_parameter_column(self.doc, "nothing")
        bc.remove_column(self.doc, "sup_Boss")
        self.assertNotIn("sup_Boss", bc.aliases(self.doc))
        self.assertEqual(bc.aliases(self.doc), ["PartNumber", "Description", "length", "Boss_Length"])
        bc.activate(self.doc, "Short")
        self.assertFalse(self.doc.getObject("Boss").Suppressed)
        self.assertEqual(_height(self.doc.getObject("Body")), 56.0)  # 50 + the row's boss, 6
        bc.remove_column(self.doc, "length")
        self.assertEqual(bc.literal(self.doc.getObject("Parameters").getContents("B2")), "50 mm")
        with self.assertRaises(bc.ConfigError):
            bc.remove_column(self.doc, "PartNumber")
        self.assertIsNotNone(plate)

    def test_rows_come_and_go(self):
        configure_bracket(self.doc)
        bc.add_configuration(self.doc, "Extra Long", copy_from="Long")
        bc.set_cell(self.doc, "Extra Long", "length", "200 mm")
        bc.activate(self.doc, "Extra Long")
        self.assertEqual(_height(self.doc.getObject("Body")), 206.0)
        self.assertEqual(self.part.PartNumber, "BW-1001")
        bc.rename_configuration(self.doc, "Extra Long", "XL")
        self.assertEqual(bc.active(self.doc), "XL")
        bc.remove_configuration(self.doc, "Short")
        self.assertEqual(bc.names(self.doc), ["Standard", "Long", "XL"])
        self.assertEqual(bc.active(self.doc), "XL")
        self.assertEqual(_height(self.doc.getObject("Body")), 206.0)
        with self.assertRaises(bc.ConfigError):
            bc.add_configuration(self.doc, "Long")

    def test_check_all_finds_a_failing_row(self):
        configure_bracket(self.doc)
        bc.add_dimension_column(self.doc, self.doc.getObject("Boss"), "Length")
        bc.set_cell(self.doc, "Long", "Boss_Length", "0 mm")  # a pad of length 0 fails
        bc.activate(self.doc, "Standard")
        report = bc.check_all(self.doc)
        self.assertEqual(report["Short"], [])  # the boss is suppressed there
        self.assertEqual(report["Standard"], [])
        self.assertIn("Boss", report["Long"])
        self.assertEqual(bc.active(self.doc), "Standard")

    def test_csv_export_import(self):
        configure_bracket(self.doc)
        text = bc.export_csv(self.doc)
        self.assertTrue(text.startswith("Configuration,PartNumber,Description,length,sup_Boss\n"))
        text = text.replace("120 mm", "125 mm") + "Huge,=FilePartNumber,'Bracket 300,300 mm,0,extra\n"
        updated, added, unknown = bc.import_csv(self.doc, text)
        self.assertEqual(added, ["Huge"])
        self.assertEqual(unknown, [])
        bc.activate(self.doc, "Long")
        self.assertEqual(_height(self.doc.getObject("Body")), 131.0)
        bc.activate(self.doc, "Huge")
        self.assertEqual(_height(self.doc.getObject("Body")), 306.0)
        self.assertIn("Short", updated)

    def test_save_reopen_and_remove_all(self):
        configure_bracket(self.doc)
        bc.activate(self.doc, "Long")
        path = self.doc.FileName
        self.doc.save()
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        doc.recompute()
        self.assertEqual(bc.names(doc), ["Short", "Standard", "Long"])
        self.assertEqual(bc.active(doc), "Long")
        bc.activate(doc, "Short")
        self.assertEqual(doc.getObject("Part").PartNumber, "BW-1001-S")
        bc.remove_all(doc)
        self.assertIsNone(bc.table(doc))
        self.assertNotIn("Configuration", doc.getObject("Part").PropertiesList)
        self.assertEqual(doc.getObject("Part").PartNumber, "BW-1001-S")
        self.assertEqual(bc.literal(doc.getObject("Parameters").getContents("B2")), "50 mm")
        doc.recompute()
        self.assertEqual(_height(doc.getObject("Body")), 50.0)

    def test_file_part_number_moves_with_the_vault(self):
        configure_bracket(self.doc)
        bc.set_file_part_number(self.doc, "BW-2002")
        bc.activate(self.doc, "Long")
        self.assertEqual(self.part.PartNumber, "BW-2002")
        bc.set_cell(self.doc, "Short", "PartNumber", "BW-2002")  # the file's own number: not its own
        self.assertEqual(bc.contents(self.doc, "Short", "PartNumber"), "=FilePartNumber")


class Instances(unittest.TestCase):
    def setUp(self):
        self.folder = os.environ.get("BW_CONFIGS_DIR") or tempfile.mkdtemp(prefix="bwcfg")
        os.makedirs(self.folder, exist_ok=True)
        self.doc, self.part = make_bracket(self.folder)
        configure_bracket(self.doc)
        self.doc.save()
        self.asm_doc = App.newDocument("Frame")
        self.asm = self.asm_doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.asm_doc.saveAs(os.path.join(self.folder, "Frame.FCStd"))
        self.links = []
        for i in range(4):
            link = self.asm.newObject("App::Link", f"Bracket{i}")
            link.LinkedObject = self.part
            self.links.append(link)
        self.asm_doc.recompute()

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if not os.environ.get("BW_CONFIGS_DIR"):
            shutil.rmtree(self.folder, ignore_errors=True)

    def _seen(self):
        return [(bc.instance_configuration(l), bc.instance_part_number(l), _height(l)) for l in self.links]

    def test_instances_pick_rows(self):
        self.assertEqual(bc.instance_configuration(self.links[0]), ("Standard", True))  # stock link: follows
        for link, name in zip(self.links, ("Short", "Standard", "Long", "Long")):
            bc.set_instance_configuration(link, name)
        seen = self._seen()
        self.assertEqual(seen, [(("Short", False), "BW-1001-S", 50.0), (("Standard", False), "BW-1001-M", 86.0),
                                (("Long", False), "BW-1001", 126.0), (("Long", False), "BW-1001", 126.0)])
        # Standard equals the part's own active row, and still has its own copy
        self.assertIsNotNone(bc.copy_of(self.links[1]))
        self.assertIs(bc.source_of(self.links[1]), self.part)
        # switching the part file does not move the instances; an edit of the part reaches them
        bc.activate(self.doc, "Long")
        bc.set_cell(self.doc, "Short", "length", "40 mm")
        self.doc.recompute()
        self.asm_doc.recompute()
        self.asm_doc.recompute()
        self.assertEqual(self._seen()[0], (("Short", False), "BW-1001-S", 40.0))
        self.assertEqual(self._seen()[1][2], 86.0)
        bc.follow_part(self.links[3])
        self.assertEqual(bc.instance_configuration(self.links[3]), ("Long", True))

    def test_reopen_keeps_rows(self):
        for link, name in zip(self.links, ("Short", "Standard", "Long", "Long")):
            bc.set_instance_configuration(link, name)
        self.asm_doc.save()
        path = self.asm_doc.FileName
        App.closeDocument(self.asm_doc.Name)
        App.closeDocument(self.doc.Name)
        doc = App.openDocument(path)
        doc.recompute()
        self.links = [doc.getObject(f"Bracket{i}") for i in range(4)]
        self.assertEqual([s[1] for s in self._seen()], ["BW-1001-S", "BW-1001-M", "BW-1001", "BW-1001"])
        self.assertEqual([s[2] for s in self._seen()], [50.0, 86.0, 126.0, 126.0])

    def test_assembly_table_drives_instances(self):
        bc.setup(self.asm_doc, ("Compact", "Full"))
        alias = bc.add_instance_column(self.asm_doc, self.links[0])
        self.assertEqual(alias, "cfg_Bracket0")
        bc.set_cell(self.asm_doc, "Compact", alias, "Short")
        bc.set_cell(self.asm_doc, "Full", alias, "Standard")
        with self.assertRaises(bc.ConfigError):
            bc.set_instance_configuration(self.links[0], "Long")
        bc.activate(self.asm_doc, "Compact")
        self.assertEqual(self._seen()[0], (("Short", False), "BW-1001-S", 50.0))
        bc.activate(self.asm_doc, "Full")  # the part's own active row, still a copy of its own
        self.assertEqual(self._seen()[0], (("Standard", False), "BW-1001-M", 86.0))

    def test_component_suppression(self):
        joint = self.asm_doc.addObject("App::FeaturePython", "Joint")
        joint.addProperty("App::PropertyString", "JointType")
        joint.addProperty("App::PropertyXLinkSubHidden", "Reference1")
        joint.addProperty("App::PropertyBool", "Suppressed")
        joint.Reference1 = (self.asm, ["Bracket2.Body.Face1"])
        bc.setup(self.asm_doc, ("With", "Without"))
        alias = bc.add_suppression_column(self.asm_doc, self.links[2])
        self.assertEqual(alias, "sup_Bracket2")
        bc.set_cell(self.asm_doc, "Without", alias, "1")
        bc.activate(self.asm_doc, "Without")
        self.assertTrue(bc.is_suppressed_component(self.links[2]))
        self.assertFalse(self.links[2].Visibility)
        self.assertTrue(joint.Suppressed)
        bc.activate(self.asm_doc, "With")
        self.assertTrue(self.links[2].Visibility)
        self.assertFalse(joint.Suppressed)


class VaultAndBom(unittest.TestCase):
    """P/N per configuration: configuration items in a scratch folder vault, numbered as dash numbers
    of the file, and an assembly BOM with one line per configuration."""

    def setUp(self):
        from bruceworx import vault_setup as vs

        self.tmp = os.environ.get("BW_CONFIGS_DIR") or tempfile.mkdtemp(prefix="bwcfgv")
        self.vault = vs.create_vault(vs.VaultSetup(name="Configs", folder=os.path.join(self.tmp, "vault")),
                                     actor="tester@mac")

        def make(path, pn):
            doc, _part = make_bracket(os.path.dirname(path), name=os.path.splitext(os.path.basename(path))[0], pn=pn)
            App.closeDocument(doc.Name)

        self.pn = vs.new_item(self.vault, "part", "Bracket", make)
        self.path = self.vault.fetch(self.pn, self.tmp)
        self.doc = App.openDocument(self.path)

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        if not os.environ.get("BW_CONFIGS_DIR"):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _configure(self):
        configure_bracket(self.doc)
        short = bc.next_configuration_number(self.vault, self.pn)
        standard = bc.next_configuration_number(self.vault, self.pn)
        bc.set_cell(self.doc, "Short", "PartNumber", short)
        bc.set_cell(self.doc, "Standard", "PartNumber", standard)
        bc.activate(self.doc, "Standard")
        self.doc.save()
        return short, standard

    def test_numbers_are_dash_numbers_of_the_file(self):
        short, standard = self._configure()
        self.assertEqual(bc.file_part_number(self.doc), self.pn)
        self.assertEqual((short[:-3], standard[:-3]), (self.pn[:-3], self.pn[:-3]))
        self.assertEqual((int(short[-3:]), int(standard[-3:])), (int(self.pn[-3:]) + 1, int(self.pn[-3:]) + 2))

    def test_items_bom_and_refusals(self):
        import bruceworx_bom as bb
        from bruceworx import bom

        short, standard = self._configure()
        wanted = bc.configuration_items(self.doc, self.pn)
        self.assertEqual(wanted, [(short, "Short", "Bracket 50"), (standard, "Standard", "Bracket 80")])
        bc.check_configuration_items(self.vault, self.pn, wanted)
        self.assertEqual(bc.register_configuration_items(self.vault, self.pn, wanted), [short, standard])
        self.assertEqual(bc.register_configuration_items(self.vault, self.pn, wanted), [])  # nothing new
        item = self.vault.items[short]
        self.assertEqual((item.configuration_of, item.configuration, item.state), (self.pn, "Short", "WIP"))
        # an assembly with one Short, one Standard and two Long (Long is the file's own item)
        asm_doc = App.newDocument("Frame")
        asm = asm_doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm_doc.saveAs(os.path.join(self.tmp, "Frame.FCStd"))
        part = self.doc.getObject("Part")
        for i, name in enumerate(("Short", "Standard", "Long", "Long")):
            link = asm.newObject("App::Link", f"Bracket{i}")
            link.LinkedObject = part
            asm_doc.recompute()
            bc.set_instance_configuration(link, name)
        plain_link = asm.newObject("App::Link", "Follows")  # a stock link: the part's active row (Standard)
        plain_link.LinkedObject = part
        asm_doc.recompute()
        lines, missing = bom.lines_from_walk(bb.walk(asm_doc), self.vault.items)
        self.assertEqual(missing, [])
        self.assertEqual([(l.child, l.qty) for l in lines], [(short, 1), (standard, 2), (self.pn, 2)])
        # a suppressed component is not a line
        bc.setup(asm_doc, ("All", "No short"))
        alias = bc.add_suppression_column(asm_doc, asm_doc.getObject("Bracket0"))
        bc.set_cell(asm_doc, "No short", alias, "1")
        bc.activate(asm_doc, "No short")
        lines, _missing = bom.lines_from_walk(bb.walk(asm_doc), self.vault.items)
        self.assertEqual([(l.child, l.qty) for l in lines], [(standard, 2), (self.pn, 2)])
        # a P/N that names another item is refused before anything is stored
        self.vault.create_item("XX-01-09999-001", "part", "Other")
        with self.assertRaises(bc.ConfigError):
            bc.check_configuration_items(self.vault, self.pn, [("XX-01-09999-001", "Long", "")])
        bc.set_cell(self.doc, "Long", "PartNumber", short)
        with self.assertRaises(bc.ConfigError):
            bc.configuration_items(self.doc, self.pn)  # two rows with one P/N

    def test_vault_writes_the_file_number_into_the_table(self):
        from bruceworx import freecad_io

        self._configure()
        freecad_io.set_part_number(self.doc.getObject("Part"), "XX-01-04242-001")
        bc.activate(self.doc, "Long")
        self.assertEqual(self.doc.getObject("Part").PartNumber, "XX-01-04242-001")
        self.assertEqual(bc.file_part_number(self.doc), "XX-01-04242-001")


if __name__ == "__main__":
    unittest.main()
