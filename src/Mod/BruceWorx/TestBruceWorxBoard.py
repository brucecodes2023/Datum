# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad boards in FreeCAD (bruceworx_board): materials, Insert Board, mass, joints.

Runs headless on copies of KiCad's templates in a temporary folder; the board
tests are skipped when KiCad (kicad-cli and its templates) is not installed."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid

import FreeCAD as App

import bruceworx_board as bb

try:
    from heuretes_freecad import kicad_bridge as kb
except ImportError:  # hooks add-on off
    kb = None

HAVE_KICAD = (kb is not None and kb.find_cli() is not None
              and os.path.isfile(os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega", "Arduino_Mega.kicad_pcb")))
FOOTPRINTS = (kb.SHARED_SUPPORT + "/footprints") if kb else ""


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestMaterials(unittest.TestCase):
    def test_library_registered(self):
        group = App.ParamGet(f"{bb.MODULES_PARAM}/{bb.LIBRARY_NAME}")
        self.assertEqual(group.GetString("ModuleDir", ""), bb.MATERIAL_DIR)

    def test_cards(self):
        density = {"fr4": 1.85, "copper": 8.96, "pbt": 1.53, "epoxy": 1.95, "ceramic": 3.9, "metal": 8.86}
        for key, uid in bb.MATERIALS.items():
            card = bb.material(key)
            self.assertEqual(card.UUID, uid)
            self.assertEqual(card.LibraryName, bb.LIBRARY_NAME)
            if key in density:
                value = App.Units.Quantity(str(card.getPhysicalValue("Density"))).getValueAs("g/cm^3").Value
                self.assertAlmostEqual(value, density[key], places=3, msg=key)
        self.assertEqual(bb.material("fr4").Name, "FR-4")
        self.assertEqual(bb.material("copper").Name, "PCB Copper")

    def test_appearance(self):
        enig = bb.appearance(bb.ENIG)
        self.assertGreater(enig.DiffuseColor[0], enig.DiffuseColor[2])  # gold


@unittest.skipUnless(HAVE_KICAD, "KiCad not installed")
class TestInsertBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bw-board-test-")
        cls.cache = os.path.join(cls.tmp, "cache")
        shutil.copytree(os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega"), os.path.join(cls.tmp, "Arduino_Mega"))
        cls.board = os.path.join(cls.tmp, "Arduino_Mega", "Arduino_Mega.kicad_pcb")
        cls.before = _sha(cls.board)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.doc = App.newDocument("BoardTest")
        self.assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.assembly.newObject("Assembly::JointGroup", "Joints")

    def tearDown(self):
        App.closeDocument(self.doc.Name)
        self.assertEqual(_sha(self.board), self.before, "the board file was changed")

    def insert(self, **kw):
        kw.setdefault("mass_table", {})
        return bb.insert_board(self.board, assembly=self.assembly, cache=self.cache, **kw)

    def test_one_part_with_materials_and_mass(self):
        part = self.insert()
        self.assertEqual(part.TypeId, "App::Part")
        self.assertEqual(part.Label, "Arduino_Mega")
        self.assertIn(part, self.assembly.Group)
        self.assertEqual([o for o in part.Group if o.TypeId == "App::Part"], [])  # flattened
        report = bb.last_report
        # Plan check (chapter 82): FR-4 7939.6 mm^3 x 1.85 = 14.7 g, pads 21.6 mm^3 x 8.96 = 0.19 g.
        self.assertAlmostEqual(report["by_material"]["FR-4"], 14.69, delta=0.05)
        self.assertAlmostEqual(report["by_material"]["PCB Copper"], 0.193, delta=0.005)
        self.assertEqual(report["missing_models"], [])
        self.assertEqual(report["components"], 7)
        labels = {o.Label: o for o in part.Group}
        self.assertEqual(labels["Arduino_Mega_PCB"].ShapeMaterial.Name, "FR-4")
        self.assertEqual(labels["Arduino_Mega_pad"].ShapeMaterial.Name, "PCB Copper")
        self.assertEqual(labels["Arduino_Mega_soldermask"].ShapeMaterial.Name, "Default")  # faces only
        self.assertEqual(labels["J1 PinSocket_1x08_P2.54mm_Vertical"].ShapeMaterial.Name, "PBT-GF30")
        self.assertEqual(labels["J1 PinSocket_1x08_P2.54mm_Vertical"].Footprint,
                         "Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
        # Part properties: one BOM line with the board's material names and its mass.
        self.assertIn("FR-4", part.MaterialName)
        self.assertIn("PCB Copper", part.MaterialName)
        self.assertAlmostEqual(part.Mass.getValueAs("g").Value, report["total_g"], places=6)
        self.assertEqual(part.BoardSource, self.board)
        self.assertEqual(part.BoardHash, self.before)
        self.assertEqual(part.CopperLayers, 2)

    def test_bom_lists_one_pcba_line(self):
        import UtilsAssembly

        part = self.insert()
        bom = UtilsAssembly.getBomGroup(self.assembly).newObject("Assembly::BomObject", "BOM")
        bom.columnsNames = ["Index", "Name", "Quantity", ".MaterialName", ".Mass"]
        self.doc.recompute()
        rows = []
        for row in range(2, 30):
            name = bom.getContents(f"B{row}").lstrip("'")
            if not name:
                break
            rows.append((name, bom.getContents(f"D{row}").lstrip("'"), bom.getContents(f"E{row}")))
        self.assertEqual([r[0] for r in rows], ["Arduino_Mega"])
        self.assertIn("FR-4", rows[0][1])
        self.assertIn("PCB Copper", rows[0][1])
        self.assertTrue(rows[0][2], "the BOM shows the board's mass")

    def test_coordinate_systems_at_holes(self):
        part = self.insert()
        frames = [o for o in part.Group if o.isDerivedFrom("App::LocalCoordinateSystem")]
        self.assertEqual(len(frames), 7)  # board origin + 6 mounting holes
        spots = sorted((round(f.Placement.Base.x, 2), round(f.Placement.Base.y, 2)) for f in frames)
        self.assertEqual(spots, [(0, 0), (13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56),
                                 (90.17, 50.8), (96.52, 2.54)])
        # Each hole frame sits on the axis of a 3.2 mm hole through the board body.
        pcb = next(o for o in part.Group if o.Label == "Arduino_Mega_PCB")
        for frame in frames[1:]:
            self.assertAlmostEqual(frame.HoleDiameter.Value, 3.2)
            point = frame.Placement.Base + App.Vector(0, 0, 0.75)
            self.assertFalse(pcb.Shape.isInside(point, 1e-6, True), frame.Label)

    def test_joint_on_a_hole_frame(self):
        import JointObject

        part = self.insert()
        hole = next(o for o in part.Group if o.Label == "Hole MH3")
        joints = self.assembly.Group[0] if self.assembly.Group[0].TypeId == "Assembly::JointGroup" else \
            next(o for o in self.assembly.Group if o.TypeId == "Assembly::JointGroup")
        ground = joints.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, part)
        standoff = self.assembly.newObject("Part::Cylinder", "Standoff")
        standoff.Radius, standoff.Height = 2.5, 6
        standoff.Placement = App.Placement(App.Vector(200, 200, 50), App.Rotation())
        self.doc.recompute()
        joint = joints.newObject("App::FeaturePython", "Fixed")
        JointObject.Joint(joint, 0)
        edges = standoff.Shape.Edges
        top_edge = "Edge%d" % (1 + max((i for i, e in enumerate(edges) if hasattr(e.Curve, "Radius")),
                                       key=lambda i: edges[i].CenterOfMass.z))
        joint.Proxy.setJointConnectors(joint, [[part, [hole.Name + ".", hole.Name + "."]],
                                              [standoff, [top_edge, top_edge]]])  # top circle centre
        self.assembly.solve()
        self.doc.recompute()
        top = standoff.Placement.multVec(App.Vector(0, 0, 6))
        target = part.Placement.multVec(hole.Placement.Base)
        self.assertLess((top - target).Length, 1e-4)
        self.assertNotIn("Invalid", joint.State)  # the reference to the frame resolves

    def test_mass_table_overrides_a_component(self):
        part = self.insert(mass_table={"pinsocket_2x18_p2.54mm_vertical": 4.2})
        j7 = next(o for o in part.Group if o.Label.startswith("J7 "))
        self.assertAlmostEqual(j7.MassOverride.getValueAs("g").Value, 4.2)
        self.assertEqual(j7.MassSource, "component_masses.csv")
        self.assertAlmostEqual(bb.body_mass_g(j7), 4.2)
        self.assertAlmostEqual(part.Mass.getValueAs("g").Value, bb.last_report["total_g"], places=6)

    def test_cache_and_new_document(self):
        bb.insert_board(self.board, cache=self.cache, mass_table={}, doc=self.doc)
        self.assertTrue(bb.last_report["cached"] or os.path.isfile(bb.last_report["step"]))
        again = bb.insert_board(self.board, cache=self.cache, mass_table={})  # headless: a new document
        try:
            self.assertTrue(bb.last_report["cached"])
            self.assertEqual(again.Document.Name, "Arduino_Mega")
        finally:
            App.closeDocument(again.Document.Name)

    def test_tracks_zones_and_components(self):
        board = _synthetic_board(os.path.join(self.tmp, "syn"))
        part = bb.insert_board(board, doc=self.doc, cache=self.cache, mass_table={})
        materials = {o.Label: o.ShapeMaterial.Name for o in part.Group if hasattr(o, "ShapeMaterial")}
        self.assertEqual(materials["syn_copper"], "PCB Copper")  # tracks and the filled zone
        self.assertEqual(materials["syn_via"], "PCB Copper")
        self.assertEqual(materials["R1 R_0603_1608Metric"], "Ceramic Chip")
        self.assertEqual(materials["C1 C_0603_1608Metric"], "Ceramic Chip")
        self.assertEqual(materials["U1 SOIC-8_3.9x4.9mm_P1.27mm"], "Epoxy Mold Compound")
        frames = [o for o in part.Group if o.isDerivedFrom("App::LocalCoordinateSystem")]
        self.assertEqual(len(frames), 3)  # origin + H1 + H2
        # The board's drill origin (10, 60) is the part's origin: H1 at KiCad (14, 24).
        h1 = next(f for f in frames if f.Label == "Hole H1")
        self.assertAlmostEqual(h1.Placement.Base.x, 4)
        self.assertAlmostEqual(h1.Placement.Base.y, 36)


def _footprint(lib, name, x, y, rot, ref, value):
    with open(f"{FOOTPRINTS}/{lib}.pretty/{name}.kicad_mod", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'^\(footprint "[^"]*"', f'(footprint "{lib}:{name}"\n\t(at {x} {y} {rot})', text, count=1)
    text = text.replace('"REF**"', f'"{ref}"', 1)
    text = re.sub(r'\(property "Value" "[^"]*"', f'(property "Value" "{value}"', text, count=1)
    text = re.sub(r'\(version \d+\)\s*\(generator "[^"]*"\)', "", text)
    return re.sub(r'\(generator_version "[^"]*"\)', "", text)


def _synthetic_board(folder):
    """A 40 x 40 mm two-layer board with a track, a via, a filled ground zone,
    R1, C1, U1 (SOIC-8) and two mounting holes, from KiCad's own libraries."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "syn.kicad_pcb")
    body = "".join([
        _footprint("Resistor_SMD", "R_0603_1608Metric", 20, 40, 0, "R1", "10k"),
        _footprint("Capacitor_SMD", "C_0603_1608Metric", 25, 40, 90, "C1", "100n"),
        _footprint("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", 35, 40, 0, "U1", "NE555"),
        _footprint("MountingHole", "MountingHole_3.2mm_M3", 14, 24, 0, "H1", "MountingHole"),
        _footprint("MountingHole", "MountingHole_3.2mm_M3_Pad_Via", 46, 56, 0, "H2", "MountingHole"),
        f'(segment (start 20.8 40) (end 24.2 40) (width 0.25) (layer "F.Cu") (net 0) (uuid "{uuid.uuid4()}"))\n',
        f'(via (at 30 50) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 0) (uuid "{uuid.uuid4()}"))\n',
        f'(zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{uuid.uuid4()}") (hatch edge 0.5)'
        ' (connect_pads (clearance 0.5)) (min_thickness 0.25) (filled_areas_thickness no)'
        ' (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))'
        ' (polygon (pts (xy 12 22) (xy 48 22) (xy 48 58) (xy 12 58))))\n',
    ])
    for (x1, y1), (x2, y2) in (((10, 20), (50, 20)), ((50, 20), (50, 60)), ((50, 60), (10, 60)), ((10, 60), (10, 20))):
        body += (f'(gr_line (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.1) (type solid))'
                 f' (layer "Edge.Cuts") (uuid "{uuid.uuid4()}"))\n')
    head = """(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
 (general (thickness 1.6))
 (paper "A4")
 (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (5 "F.SilkS" user "F.Silkscreen") (7 "B.SilkS" user "B.Silkscreen")
  (1 "F.Mask" user) (3 "B.Mask" user) (13 "F.Paste" user) (15 "B.Paste" user) (25 "Edge.Cuts" user)
  (31 "F.CrtYd" user "F.Courtyard") (29 "B.CrtYd" user "B.Courtyard") (35 "F.Fab" user) (33 "B.Fab" user))
 (setup (stackup (copper_finish "ENIG")) (pad_to_mask_clearance 0) (aux_axis_origin 10 60))
 (net 0 "") (net 1 "GND")
"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(head + body + ")\n")
    # Fill the zone (our own test file, so saving it is fine).
    subprocess.run([kb.find_cli(), "pcb", "drc", "--refill-zones", "--save-board", "-o",
                    os.path.join(folder, "drc.rpt"), path], capture_output=True, timeout=120, check=False)
    return path


@unittest.skipUnless(HAVE_KICAD, "KiCad not installed")
class TestVaultItem(unittest.TestCase):
    """ECD-05: a KiCad project in a Standard Libraries vault, its 3D model and Open in KiCad."""

    @classmethod
    def setUpClass(cls):
        from heuretes_freecad import ecad_vault as ev
        from heuretes_freecad import pdm_workspace as ws
        from heuretes_freecad import vault_setup as vs

        cls.ev = ev
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-board-item-"))
        cls.old_cache = os.environ.get(kb.CACHE_ENV)
        os.environ[kb.CACHE_ENV] = os.path.join(cls.tmp, "cache")
        src = os.path.join(cls.tmp, "src", "Arduino_Mega")
        shutil.copytree(os.path.join(kb.TEMPLATE_DIR, "Arduino_Mega"), src)
        setup = vs.VaultSetup(name="Standard Libraries", folder=os.path.join(cls.tmp, "Vault", "Standard Libraries"))
        setup.numbering.programs = [vs.ProgramSetup("STD", "Standard parts")]
        cls.vault = vs.create_vault(setup, actor="bruce@mac")
        cls.work = ws.Workspace(cls.vault)
        cls.result = ev.import_project(cls.vault, src, "Arduino Mega template", description="Arduino Mega shield",
                                       manufacturer="Arduino", value="Mega", workspace=cls.work)
        cls.pn = cls.result.part_number
        cls.folder = cls.work.path(cls.pn)

    @classmethod
    def tearDownClass(cls):
        if cls.old_cache is None:
            os.environ.pop(kb.CACHE_ENV, None)
        else:
            os.environ[kb.CACHE_ENV] = cls.old_cache
        for dirpath, dirnames, filenames in os.walk(cls.tmp):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o755)
                except OSError:
                    pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        for name, doc in list(App.listDocuments().items()):
            if (doc.FileName or "").startswith(self.tmp) or name.startswith(("STD_", "ItemInsert")):
                App.closeDocument(name)

    def test_item_model(self):
        ev = self.ev
        values = bb.item_values(self.vault, self.pn, self.folder)
        self.assertEqual(values["Description"], "Arduino Mega shield")
        self.assertEqual((values["Revision"], values["ItemType"], values["Manufacturer"]), ("01", "cots", "Arduino"))
        path = ev.derived_path(self.folder, self.pn)
        doc = bb.build_item_model(self.folder, self.pn, values, path)
        self.assertEqual(doc.FileName, path)
        self.assertEqual(doc.Label, self.pn)
        self.assertFalse(os.access(path, os.W_OK), "the 3D model is read-only on disk")
        part = next(o for o in doc.Objects if o.TypeId == "App::Part" and "BoardSource" in o.PropertiesList)
        self.assertEqual((part.PartNumber, part.Revision, part.Label), (self.pn, "01", "Arduino Mega shield"))
        self.assertEqual((part.Manufacturer, part.Value, part.ItemType), ("Arduino", "Mega", "cots"))
        self.assertEqual(part.BoardSource, os.path.join(self.folder, "Arduino_Mega.kicad_pcb"))
        group = doc.getObject(bb.ELECTRICAL_GROUP)
        kinds = {o.KiCadKind: o for o in group.Group}
        self.assertEqual(sorted(kinds), ["board", "project", "schematic"])
        self.assertEqual(bb.kicad_file_path(kinds["schematic"]), os.path.join(self.folder, "Arduino_Mega.kicad_sch"))
        # the model is not part of the vault item: the work copy is still the version checked in
        self.assertEqual(self.work.status(self.pn).code, "up_to_date")
        stamp = ev.derived_stamp(self.folder, dict(values, recipe=bb.ITEM_RECIPE))
        self.assertFalse(ev.derived_fresh(self.folder, self.pn, stamp))
        ev.record_derived(self.folder, self.pn, stamp)
        self.assertTrue(ev.derived_fresh(self.folder, self.pn, stamp))
        self.assertNotEqual(ev.derived_stamp(self.folder, dict(values, Revision="02", recipe=bb.ITEM_RECIPE)), stamp)

    def test_light_item_model(self):
        ev = self.ev
        values = bb.item_values(self.vault, self.pn, self.folder)
        full_path, light_path = ev.derived_path(self.folder, self.pn), ev.derived_path(self.folder, self.pn, True)
        full = bb.build_item_model(self.folder, self.pn, values, full_path)
        light = bb.build_item_model(self.folder, self.pn, values, light_path, light=True)
        self.assertEqual(light.FileName, light_path)
        self.assertEqual(light.Label, self.pn + "-light")  # saveAs names the document by its file

        def board(doc):
            return next(o for o in doc.Objects if o.TypeId == "App::Part" and "BoardSource" in o.PropertiesList)

        def faces(doc):
            return sum(len(o.Shape.Faces) for o in doc.Objects if o.isDerivedFrom("Part::Feature"))

        self.assertEqual((board(full).BoardDetail, board(light).BoardDetail), ("full", "light"))
        self.assertTrue(bb.is_light_part(board(light)))
        self.assertEqual(board(light).PartNumber, self.pn)
        labels = {o.Label for o in board(light).Group}
        self.assertIn("Arduino_Mega_PCB", labels)
        self.assertFalse(any(lbl.endswith(("_pad", "_silkscreen", "_soldermask")) for lbl in labels), labels)
        self.assertLess(faces(light), faces(full))
        self.assertIsNotNone(light.getObject(bb.ELECTRICAL_GROUP))

    def test_light_default_by_assembly_size(self):
        doc = App.newDocument("ItemInsertSize")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        box = doc.addObject("Part::Box", "Box")
        for i in range(3):
            link = doc.addObject("App::Link", f"L{i}")
            link.LinkedObject = box
            asm.addObject(link)
        array = doc.addObject("App::Link", "Array")
        array.LinkedObject = box
        array.ElementCount = 10
        asm.addObject(array)
        doc.recompute()
        self.assertEqual(bb.assembly_part_count(asm), 13)
        self.assertFalse(bb.default_light(asm, threshold=25))
        self.assertTrue(bb.default_light(asm, threshold=13))
        self.assertFalse(bb.default_light(None))

    def test_insert_board_from_the_vault_takes_the_part_number(self):
        doc = App.newDocument("ItemInsert")
        part = bb.insert_board(os.path.join(self.folder, "Arduino_Mega.kicad_pcb"), doc=doc, mass_table={})
        self.assertEqual((part.PartNumber, part.Description), (self.pn, "Arduino Mega shield"))

    @unittest.skipUnless(App.GuiUp, "needs the GUI")
    def test_open_from_vault_and_in_kicad(self):
        import bruceworx_board_gui as bg
        import bruceworx_vaultclient as vc
        import FreeCADGui as Gui

        App.ParamGet(vc.ACTORS_PATH).SetString(self.vault.ref["vault_id"], "bruce@mac")  # the importer
        prefs = vc._prefs()
        old = prefs.GetString("VaultPath", "")
        prefs.SetString("VaultPath", self.vault.ref_path)
        self.addCleanup(prefs.SetString, "VaultPath", old)
        s = vc.session()
        self.assertEqual(s.me, "bruce@mac")
        self.assertTrue(bg.is_kicad_item(s, self.pn))
        self.assertTrue(vc.available(bg.OPEN_KICAD_COMMAND, self.pn))
        self.assertIn(bg.OPEN_KICAD_COMMAND, vc.CONTEXT_COMMANDS)
        menu = vc.build_item_menu(Gui.getMainWindow(), self.pn)
        self.assertIn("Open in KiCad", [a.text() for a in menu.actions()])
        # Open from Vault (the Data panel's double-click): the board in 3D
        self.assertTrue(vc.open_from_vault(self.pn, ask=False))
        path = self.ev.derived_path(self.folder, self.pn)
        doc = vc.document_for(path)
        self.assertIsNotNone(doc, "the 3D model did not open")
        self.assertIs(App.ActiveDocument, doc)
        sch = doc.getObject("KiCadSchematic")
        self.assertIsNotNone(sch.ViewObject.Proxy)
        # again: the same document, not remade
        self.assertTrue(vc.open_from_vault(self.pn, ask=False))
        self.assertEqual(len([d for d in App.listDocuments().values() if d.FileName == path]), 1)
        # Open 3D (light): its own document beside the full one, from the vault menu
        self.assertIn("Open 3D (light)", [a.text() for a in menu.actions()])
        vc.run_for(bg.OPEN_LIGHT_COMMAND, self.pn)
        light_doc = vc.document_for(self.ev.derived_path(self.folder, self.pn, light=True))
        self.assertIsNotNone(light_doc, "the light model did not open")
        self.assertIsNotNone(vc.document_for(path), "the full model stays open")
        # Insert Board, light, into an assembly
        target = App.newDocument("ItemInsertAsm")
        asm = target.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Assembly::JointGroup", "Joints")
        part = bg.run(os.path.join(self.folder, "Arduino_Mega.kicad_pcb"), asm, light=True)
        self.assertEqual((part.BoardDetail, part.PartNumber), ("light", self.pn))
        # Open in KiCad: the project in the vault's work folder (nothing is started: dry run)
        os.environ["BW_KICAD_LAUNCH"] = "dry"
        self.addCleanup(os.environ.pop, "BW_KICAD_LAUNCH", None)
        self.assertTrue(bg.open_in_kicad(self.pn, "schematic", ask=False))
        from unittest import mock

        with mock.patch.object(bg, "_ask_checkout", return_value=False) as asked:  # "Open Read-Only"
            self.assertTrue(bg.open_kicad_file(bb.kicad_file_path(sch)))
        asked.assert_called_once()  # a WIP item nobody holds: the check-out is offered first
        self.assertEqual(s.status(self.pn).code, "up_to_date")
        self.assertEqual(bg.library_for(s.root), os.path.abspath(s.root))
        # Import KiCad Project as Standard Part...: the form wants a comment, run_import makes the item
        project = self.ev.find_project(os.path.join(self.tmp, "src", "Arduino_Mega"))
        dialog, fields = bg._import_form(Gui.getMainWindow(), project, "Arduino Mega")
        from PySide import QtWidgets

        ok = dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok)
        self.assertFalse(ok.isEnabled())
        fields["comment"].setText("Second copy")
        self.assertTrue(ok.isEnabled())
        dialog.deleteLater()
        result = bg.run_import(s.root, project.pro, "Second copy", description="Arduino Mega, second", value="2")
        self.assertEqual(result.part_number, "STD-01-01002-001")
        s = vc.session()
        self.assertTrue(bg.is_kicad_item(s, result.part_number))
        self.assertEqual(s.status(result.part_number).code, "up_to_date")

if __name__ == "__main__":
    unittest.main()
