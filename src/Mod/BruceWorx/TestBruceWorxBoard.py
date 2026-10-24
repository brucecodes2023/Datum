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
    from bruceworx import kicad_bridge as kb
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


if __name__ == "__main__":
    unittest.main()
