# SPDX-License-Identifier: LGPL-2.1-or-later
"""Connector starter library parts headless (BW-ROU-05): a generated part has one pin frame per
contact with rou05's frame properties, the pin names of the KiCad footprint's pads, +Z pointing away
from the housing (checked against the body's bounding box), the connector properties and the KiCad
licence note, and bruceworx_routing_ports.pins() reads the frames back after save and reopen.

Needs KiCad's footprint and 3D model library (/Applications/KiCad); skips without it.

Run: FreeCADCmd -t TestBruceWorxConnectorLibrary
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_routing_ports as bp
from bruceworx import connector_library as cl

SAMPLES = ("XHP-4", "B4B-XH-A", "S4B-XH-A", "GHR-03V-S", "43025-0600", "43020-0600", "XT60-M", "XT30PW-M",
           "DT04-6P", "DT06-6S", "BULLET-3.5-M", "HDR254-M-2x03")
ACCESSORY_SAMPLES = ("SXH-001T-P0.6", "114017", "W6P")


@unittest.skipUnless(cl.kicad_available(), "KiCad's footprint library is not installed")
class ConnectorLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bw-connlib-")
        cls.entries = {e.mpn: e for e in cl.catalog() if e.mpn in SAMPLES + ACCESSORY_SAMPLES}
        cls.paths = {}
        for mpn, entry in cls.entries.items():
            cls.paths[mpn] = cl.build_part(entry, os.path.join(cls.tmp, entry.file))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, mpn):
        doc = App.openDocument(self.paths[mpn])
        self.addCleanup(App.closeDocument, doc.Name)
        parts = [o for o in doc.Objects if o.TypeId == "App::Part"]
        self.assertEqual(len(parts), 1)
        return doc, parts[0]

    def body_box(self, part):
        shapes = [o.Shape for o in part.Group if o.TypeId == "Part::Feature"]
        self.assertEqual(len(shapes), 1)
        self.assertGreater(shapes[0].Volume, 0.0)
        return shapes[0].BoundBox

    def test_every_sample_built(self):
        self.assertEqual(set(self.paths), set(SAMPLES + ACCESSORY_SAMPLES))

    def test_pin_frames_and_properties(self):
        for mpn in SAMPLES:
            entry = self.entries[mpn]
            _doc, part = self.open(mpn)
            pins = bp.pins(part)
            self.assertEqual(len(pins), entry.positions, mpn)
            self.assertEqual([p.PinName for p in pins], [p.name for p in entry.pins], mpn)
            for lcs in pins:
                self.assertEqual(lcs.TypeId, "App::LocalCoordinateSystem")
                self.assertEqual(lcs.BWFrame, "Pin")
                self.assertIn(lcs, part.Group)
                self.assertEqual((lcs.GaugeMin, lcs.GaugeMax), (entry.gauge_min, entry.gauge_max), mpn)
                self.assertEqual((lcs.TerminalPN, lcs.TerminalMfr), (entry.terminal, entry.terminal_mfr))
                self.assertEqual((lcs.SealPN, lcs.SealMfr), (entry.seal, entry.seal_mfr))
                self.assertEqual((lcs.TerminalItem, lcs.SealItem), ("", ""))  # filled by the vault import
                self.assertTrue(hasattr(lcs, "StraightLength"))
            self.assertEqual(part.ConnectorFamily, entry.family)
            self.assertEqual(part.ConnectorRole, entry.role)
            self.assertEqual(part.MatingPN, "; ".join(entry.mating))
            self.assertEqual(part.FrameAt, entry.frame_at)
            self.assertEqual(part.ManufacturerPartNumber, mpn)
            self.assertEqual(part.VendorPartNumber, mpn)
            self.assertEqual(part.Description, entry.description)
            self.assertEqual(part.ItemType, "cots")
            self.assertEqual(part.ModelLicense, entry.license)

    def test_accessories(self):
        _doc, dt = self.open("DT04-6P")
        self.assertEqual(list(dt.Accessories), ["W6P|TE Connectivity (Deutsch)||1|Wedgelock"])
        _doc, xh = self.open("XHP-4")
        self.assertEqual(list(xh.Accessories), [])
        for mpn in ACCESSORY_SAMPLES:
            entry = self.entries[mpn]
            _doc, part = self.open(mpn)
            self.assertEqual(bp.pins(part), [])
            self.assertEqual(part.AccessoryKind, entry.role)
            self.assertEqual((part.Manufacturer, part.ManufacturerPartNumber), (entry.manufacturer, mpn))
            self.assertEqual(part.ItemType, "cots")
            self.assertGreater(self.body_box(part).ZLength, 0)

    def test_fill_items(self):
        path = os.path.join(self.tmp, "filled.FCStd")
        shutil.copyfile(self.paths["DT04-6P"], path)
        n = cl.fill_items(path, {"0460-202-16141": "STD-01-01010-001", "114017": "STD-01-01011-001",
                                 "W6P": "STD-01-01012-001"})
        self.assertEqual(n, 6 + 1)  # six terminals and the wedgelock; DT has no per-wire seal
        doc = App.openDocument(path)
        self.addCleanup(App.closeDocument, doc.Name)
        part = [o for o in doc.Objects if o.TypeId == "App::Part"][0]
        self.assertEqual({(p.TerminalItem, p.SealItem) for p in bp.pins(part)}, {("STD-01-01010-001", "")})
        self.assertEqual(list(part.Accessories), ["W6P|TE Connectivity (Deutsch)|STD-01-01012-001|1|Wedgelock"])

    def test_pin_names_from_the_pads(self):
        pads = cl.footprint_pads("Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical")
        for mpn in ("XHP-4", "B4B-XH-A"):
            _doc, part = self.open(mpn)
            for lcs, (number, x, y) in zip(bp.pins(part), pads):
                self.assertEqual(lcs.PinName, number)
                self.assertAlmostEqual(lcs.Placement.Base.x, x, places=6)
                self.assertAlmostEqual(lcs.Placement.Base.y, y, places=6)
        _doc, part = self.open("43025-0600")
        self.assertEqual([p.PinName for p in bp.pins(part)], ["1", "2", "3", "4", "5", "6"])

    def test_z_points_away_from_the_housing(self):
        for mpn in SAMPLES:
            _doc, part = self.open(mpn)
            box = self.body_box(part)
            centre = box.Center
            for lcs in bp.pins(part):
                origin = lcs.Placement.Base
                z = lcs.Placement.Rotation.multVec(App.Vector(0, 0, 1))
                self.assertGreater((origin - centre).dot(z), 0.0, f"{mpn} pin {lcs.PinName}")
                outside = origin + z * 1.0
                self.assertFalse(box.isInside(outside), f"{mpn} pin {lcs.PinName}: +Z runs into the body")
                if part.FrameAt == "":
                    # on the housing: the frame sits on (within 0.1 mm of) the body's box face
                    grown = App.BoundBox(box)
                    grown.enlarge(0.1)
                    self.assertTrue(grown.isInside(origin), f"{mpn} pin {lcs.PinName} is off the housing")
                x = lcs.Placement.Rotation.multVec(App.Vector(1, 0, 0))
                self.assertAlmostEqual(x.dot(z), 0.0, places=9)

    def test_kicad_body_and_licence(self):
        _doc, part = self.open("B4B-XH-A")
        self.assertIn("KiCad 3D library", part.ModelSource)
        self.assertIn("CC-BY-SA 4.0", part.ModelLicense)
        self.assertEqual(part.Footprint, "Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical")
        box = self.body_box(part)
        self.assertAlmostEqual(box.ZMax, 7.0, places=1)  # the header's height above the board
        _doc, part = self.open("XHP-4")
        self.assertEqual(part.ModelSource, cl.PARAMETRIC_SOURCE)
        self.assertAlmostEqual(self.body_box(part).ZMax, 9.8, places=6)  # mated height: wire leaves here

    def test_housing_and_header_mate_at_the_same_placement(self):
        _doc, housing = self.open("XHP-4")
        _doc2, header = self.open("B4B-XH-A")
        self.assertTrue(bp.mates(housing, header))
        for a, b in zip(bp.pins(housing), bp.pins(header)):
            self.assertTrue(a.Placement.isSame(b.Placement, 1e-9))

    def test_vault_sees_a_part(self):
        from bruceworx import vault_setup  # noqa: PLC0415

        candidate = vault_setup.scan_for_import(self.tmp)
        kinds = {os.path.basename(c.path): c.kind for c in candidate}
        self.assertEqual(kinds["XHP-4.FCStd"], "part")


if __name__ == "__main__":
    unittest.main()
