# SPDX-License-Identifier: LGPL-2.1-or-later
"""Threads, hole presets and the hardware catalogue (bruceworx.hardware, MOD-05): ISO metric
thread maths, the heat-set / tapped / clearance presets and the Hole values they set, hole
callouts, fastener keys and descriptions, recognising the COTS parts a vault already has, and
the vault's hardware.json."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import hardware as hw  # noqa: E402


class Threads(unittest.TestCase):
    def test_m3_coarse(self):
        t = hw.thread("M3")
        self.assertEqual((t.size, t.pitch, t.major, t.tap_drill), ("M3", 0.5, 3.0, 2.5))
        self.assertAlmostEqual(t.minor, 2.459, places=3)  # D1 = D - 1.082532 P
        self.assertAlmostEqual(t.minor_external, 2.387, places=3)  # d3 = d - 1.226869 P
        self.assertEqual(t.designation, "M3x0.5")

    def test_designations(self):
        self.assertEqual(hw.thread("M3x0.5").pitch, 0.5)
        self.assertEqual(hw.thread("m8 x 1").pitch, 1.0)  # a fine pitch is kept
        self.assertEqual(hw.thread("M2.5").tap_drill, 2.05)
        self.assertEqual(hw.thread("M4×0.7").major, 4.0)
        for bad in ("", "3", "UNC 1/4", "M7"):
            with self.assertRaises(ValueError, msg=bad):
                hw.thread(bad)

    def test_callouts(self):
        self.assertEqual(hw.thread("M3").callout(6), "M3x0.5-6H ↧6")
        self.assertEqual(hw.thread("M5").callout(10, internal=False), "M5x0.8-6g ↧10")
        self.assertEqual(hw.thread("M3").callout(), "M3x0.5-6H")

    def test_clearance_iso_273(self):
        self.assertEqual(hw.clearance("M3"), 3.4)
        self.assertEqual(hw.clearance("M3", "Fine"), 3.2)
        self.assertEqual(hw.clearance("M5", "Coarse"), 5.8)
        with self.assertRaises(ValueError):
            hw.clearance("M3", "Loose")

    def test_thread_size_enum(self):
        self.assertEqual(hw.thread_size_enum("M3", ["M2x0.4", "M2.5x0.45", "M3x0.5", "M3.5x0.6"]), "M3x0.5")
        self.assertEqual(hw.thread_size_enum("M3", ["M2", "M3", "M4"]), "M3")
        with self.assertRaises(ValueError):
            hw.thread_size_enum("M3", ["M2x0.4"])


class Presets(unittest.TestCase):
    def setUp(self):
        self.presets = hw.load_presets()

    def test_heatset_m2_to_m5(self):
        heat = [p for p in self.presets.values() if p.kind == "heatset"]
        self.assertEqual(sorted({hw.thread(p.thread).size for p in heat}), ["M2", "M2.5", "M3", "M4", "M5"])
        m3 = self.presets["Heat-set M3x5.7"]
        self.assertEqual((m3.diameter, m3.depth, m3.insert), (4.0, 6.7, "M3x5.7"))
        self.assertIn("ruthex RX-M3x5.7", m3.inserts)
        for p in heat:
            # the printed hole is smaller than the insert (it melts in), and deeper
            self.assertLess(p.diameter, p.insert_od, p.name)
            self.assertGreaterEqual(p.depth, p.insert_length + 1.0 - 1e-9, p.name)
            self.assertGreater(p.diameter, hw.thread(p.thread).major, p.name)

    def test_every_kind_is_there(self):
        self.assertEqual({p.kind for p in self.presets.values()}, set(hw.KINDS))

    def test_heatset_hole_values(self):
        values = dict(hw.hole_properties(self.presets["Heat-set M3x5.7"]))
        self.assertEqual(values["ThreadType"], "None")
        self.assertEqual((values["Diameter"], values["Depth"], values["DrillPoint"]), (4.0, 6.7, "Flat"))
        self.assertFalse(values["Threaded"])

    def test_tapped_hole_values_and_order(self):
        props = hw.hole_properties(self.presets["Tapped M3"])
        names = [n for n, _v in props]
        self.assertEqual(names[:2], ["ThreadType", "ThreadSize"])  # type first: it rebuilds the lists
        values = dict(props)
        self.assertTrue(values["Threaded"])
        self.assertFalse(values["ModelThread"])
        self.assertEqual((values["ThreadSize"], values["Depth"], values["ThreadDepth"]), ("M3", 8.0, 6.0))
        self.assertEqual(dict(hw.hole_properties(self.presets["Tapped M3"], depth=12))["Depth"], 12)

    def test_clearance_and_counterbore_values(self):
        values = dict(hw.hole_properties(self.presets["Clearance M3"]))
        self.assertEqual((values["ThreadFit"], values["DepthType"], values["HoleCutType"]),
                         ("Medium", "ThroughAll", "None"))
        values = dict(hw.hole_properties(self.presets["Counterbore M3 (ISO 4762)"]))
        self.assertEqual(values["HoleCutType"], "ISO 4762")

    def test_describe(self):
        self.assertIn("Ø4 ↧6.7 for a M3x5.7 insert", self.presets["Heat-set M3x5.7"].describe())
        self.assertIn("drill Ø2.5", self.presets["Tapped M3"].describe())
        self.assertEqual(self.presets["Clearance M3"].describe(), "Ø3.4 medium clearance")

    def test_vault_layer_replaces_and_adds(self):
        tmp = tempfile.mkdtemp(prefix="bw-hw-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, hw.PRESETS_FILE), "w", encoding="utf-8") as handle:
            json.dump({"schema": hw.PRESETS_SCHEMA, "presets": [
                {"name": "Heat-set M3x5.7", "kind": "heatset", "thread": "M3", "diameter": 4.1, "depth": 7,
                 "insert_length": 5.7},
                {"name": "Shop M3 PLA", "kind": "heatset", "thread": "M3", "diameter": 3.9, "depth": 6}]}, handle)
        presets = hw.load_presets(tmp)
        self.assertEqual(presets["Heat-set M3x5.7"].diameter, 4.1)
        self.assertEqual(presets["Heat-set M3x5.7"].layer, "vault")
        self.assertIn("Shop M3 PLA", presets)
        self.assertIn("Tapped M3", presets)

    def test_bad_presets_are_refused(self):
        for raw in ({"kind": "heatset", "thread": "M3"},
                    {"name": "x", "kind": "drilled", "thread": "M3"},
                    {"name": "x", "kind": "heatset", "thread": "M3", "diameter": 4},
                    {"name": "x", "kind": "heatset", "thread": "M3", "diameter": 4, "depth": 5, "insert_length": 5.7},
                    {"name": "x", "kind": "tapped", "thread": "M3", "depth": 4, "thread_depth": 6},
                    {"name": "x", "kind": "counterbore", "thread": "M3"},
                    {"name": "x", "kind": "clearance", "thread": "M3", "fit": "Loose"}):
            with self.assertRaises(ValueError, msg=raw):
                hw._preset(raw, "test")


class Callouts(unittest.TestCase):
    def test_tapped_blind(self):
        values = {"Threaded": True, "ThreadType": "ISOMetricProfile", "ThreadSize": "M3x0.5", "ThreadClass": "6H",
                  "Diameter": 3.0, "DepthType": "Dimension", "Depth": 8.0, "ThreadDepthType": "Dimension",
                  "ThreadDepth": 6.0, "HoleCutType": "None"}
        self.assertEqual(hw.hole_callout(values), "M3x0.5-6H ↧6\nØ2.5 ↧8")
        self.assertEqual(hw.hole_callout(values, count=4).split("\n")[0], "4X M3x0.5-6H ↧6")

    def test_tapped_through(self):
        values = {"Threaded": True, "ThreadType": "ISOMetricProfile", "ThreadSize": "M4x0.7",
                  "DepthType": "ThroughAll", "HoleCutType": "None"}
        self.assertEqual(hw.hole_callout(values), "M4x0.7-6H THRU")

    def test_heatset(self):
        preset = hw.load_presets()["Heat-set M3x5.7"]
        values = dict(hw.hole_properties(preset))
        self.assertEqual(hw.hole_callout(values, preset=preset), "Ø4 ↧6.7\nHEAT-SET INSERT M3x5.7")

    def test_counterbore_and_countersink(self):
        cb = {"Threaded": False, "ThreadType": "ISOMetricProfile", "ThreadSize": "M3x0.5", "Diameter": 3.4,
              "DepthType": "ThroughAll", "HoleCutType": "ISO 4762", "HoleCutDiameter": 6.5, "HoleCutDepth": 3.4}
        self.assertEqual(hw.hole_callout(cb), "Ø3.4 THRU\n⌴ Ø6.5 ↧3.4")
        cs = dict(cb, HoleCutType="ISO 10642", HoleCutDiameter=6.72, HoleCutCountersinkAngle=90)
        self.assertEqual(hw.hole_callout(cs), "Ø3.4 THRU\n⌵ Ø6.72 x 90°")


class Fasteners(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(hw.fastener_key("ISO4762", "M3", "40"), "ISO4762-M3x40")
        self.assertEqual(hw.fastener_key("ISO4762", "M3", 6.0), "ISO4762-M3x6")
        self.assertEqual(hw.fastener_key("ISO4032", "M3"), "ISO4032-M3")
        self.assertEqual(hw.fastener_key("ISO4762", "M3", "40 mm"), "ISO4762-M3x40")
        with self.assertRaises(ValueError):
            hw.fastener_key("", "M3")

    def test_descriptions(self):
        self.assertEqual(hw.fastener_description("ISO4762", "M3", 40), "Socket head cap screw ISO 4762 M3x40")
        self.assertEqual(hw.fastener_description("ISO4032", "M3"), "Hex nut ISO 4032 M3")
        self.assertEqual(hw.fastener_description("ISO1234", "M3", 10, "ISO 1234 Slotted cheese head screw"),
                         "Slotted cheese head screw ISO 1234 M3x10")

    def test_round_trip(self):
        for args in (("ISO4762", "M3", 40), ("ISO4762", "M3", 6), ("ISO4032", "M4", None), ("ISO7089", "M3", None)):
            text = hw.fastener_description(*args)
            self.assertEqual(hw.match_description(text), hw.fastener_key(*args), text)

    def test_gearbox_descriptions(self):
        cases = {
            "Socket head cap screw M3x40 (COTS)": "ISO4762-M3x40",
            "Socket head cap screw M3x8 (COTS)": "ISO4762-M3x8",
            "Socket head cap screw M3x6 (COTS)": "ISO4762-M3x6",
            "Ball bearing 683ZZ 3×7×3 (COTS)": "BEARING-683ZZ",
            "Ball bearing 688ZZ 8×16×5 (COTS)": "BEARING-688ZZ",
            "Dowel pin Ø3×10 (COTS)": "ISO8734-3x10",
            "NEMA 17 stepper motor (COTS)": "",
            "Housing with ring gear 48T": "",
            "Planet gear 18T m1": "",
        }
        for text, key in cases.items():
            self.assertEqual(hw.match_description(text), key, text)
        self.assertTrue(hw.looks_cots("NEMA 17 stepper motor (COTS)"))
        self.assertFalse(hw.looks_cots("Planet carrier with output shaft"))
        self.assertFalse(hw.looks_cots("Sun gear 12T m1"))


class Catalogue(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bw-hw-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_empty_save_load(self):
        self.assertEqual(hw.load_catalogue(self.root), {})
        cat = {}
        hw.record(cat, "ISO4762-M3x40", "LE-01-01017-001", "Socket head cap screw ISO 4762 M3x40",
                  vendor="", standard="ISO 4762")
        hw.save_catalogue(self.root, cat)
        again = hw.load_catalogue(self.root)
        self.assertEqual(again["ISO4762-M3x40"]["part_number"], "LE-01-01017-001")
        self.assertEqual(again["ISO4762-M3x40"]["standard"], "ISO 4762")
        self.assertNotIn("vendor", again["ISO4762-M3x40"])
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".hardware")], [])

    def test_wrong_format_and_newer_version(self):
        with open(hw.catalogue_path(self.root), "w", encoding="utf-8") as handle:
            json.dump({"format": "something"}, handle)
        with self.assertRaises(ValueError):
            hw.load_catalogue(self.root)
        with open(hw.catalogue_path(self.root), "w", encoding="utf-8") as handle:
            json.dump({"format": hw.CATALOGUE_FORMAT, "version": 99}, handle)
        with self.assertRaises(ValueError):
            hw.load_catalogue(self.root)

    def test_find_item(self):
        items = {
            "LE-01-01014-001": SimpleNamespace(description="Socket head cap screw M3x40 (COTS)", item_type="part",
                                               obsolete=False),
            "LE-01-01020-001": SimpleNamespace(description="Socket head cap screw ISO 4762 M3x6", item_type="cots",
                                               obsolete=True),
            "LE-02-01002-001": SimpleNamespace(description="Socket head cap screw M3x6 kit", item_type="assembly",
                                               obsolete=False),
        }
        self.assertEqual(hw.find_item("ISO4762-M3x40", {}, items), "LE-01-01014-001")  # by description
        self.assertEqual(hw.find_item("ISO4762-M3x6", {}, items), "")  # obsolete, and an assembly
        cat = {"ISO4762-M3x6": {"part_number": "LE-01-01020-001"}}
        self.assertEqual(hw.find_item("ISO4762-M3x6", cat, items), "")  # the catalogue's item is obsolete
        cat = {"ISO4762-M3x40": {"part_number": "LE-01-09999-001"}}
        self.assertEqual(hw.find_item("ISO4762-M3x40", cat, items), "LE-01-01014-001")  # stale entry

    def test_cots_candidates(self):
        items = {
            "A": SimpleNamespace(description="Socket head cap screw M3x40 (COTS)", item_type="part", obsolete=False),
            "B": SimpleNamespace(description="NEMA 17 stepper motor (COTS)", item_type="part", obsolete=False),
            "C": SimpleNamespace(description="End cap", item_type="part", obsolete=False),
            "D": SimpleNamespace(description="Hex nut ISO 4032 M3", item_type="cots", obsolete=False),
        }
        self.assertEqual(hw.cots_candidates(items),
                         [("A", "ISO4762-M3x40", "Socket head cap screw M3x40 (COTS)"),
                          ("B", "", "NEMA 17 stepper motor (COTS)")])


if __name__ == "__main__":
    unittest.main()
