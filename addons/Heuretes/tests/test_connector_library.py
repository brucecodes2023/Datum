# SPDX-License-Identifier: LGPL-2.1-or-later
"""Connector starter library (ROU-05): the tables, the pins from KiCad pads, the manifest and the
vault import (FreeCAD-free; the KiCad cases need KiCad's footprint library and skip without it)."""

import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import connector_library as cl  # noqa: E402
from heuretes_freecad import vault_setup as vs  # noqa: E402

ME = "bruce@mac"
NEEDS_KICAD = unittest.skipUnless(cl.kicad_available(), "KiCad's footprint library is not installed")


class Tables(unittest.TestCase):
    """The family tables themselves (no KiCad needed)."""

    def test_jst_tables(self):
        for key, t in cl.JST.items():
            self.assertEqual(t["family"], "JST-" + key)
            gmin, gmax = cl.awg(t["awg"])
            self.assertGreater(gmin, gmax, key)  # AWG: the thinner wire has the larger number
            self.assertGreater(t["z0"] + t["H"], 0)
            self.assertGreater(t["side"], t["H"], key)
            for k in ("housing", "vertical", "horizontal"):
                self.assertIn("{n", t[k])
        self.assertEqual(cl.JST["XH"]["terminal"], "SXH-001T-P0.6")
        self.assertAlmostEqual(cl.JST["XH"]["z0"] + cl.JST["XH"]["H"], 9.8)  # JST: mated height 9.8 mm
        self.assertEqual(list(cl.JST_COUNTS), [2, 3, 4, 5, 6, 7, 8])

    def test_awg(self):
        self.assertEqual(cl.awg("28-22"), (28, 22))
        self.assertEqual(cl.awg("22-28"), (28, 22))

    def test_grid_pads_u_order(self):
        pads = cl.grid_pads(2, 2, 5.0, order="u")
        self.assertEqual([p[0] for p in pads], ["1", "2", "3", "4"])
        self.assertEqual([(p[1], p[2]) for p in pads], [(0, 0), (5.0, 0), (5.0, -5.0), (0, -5.0)])

    def test_dt_and_power_tables(self):
        self.assertEqual(cl.DT["counts"], (2, 3, 4, 6, 8, 12))
        for n in cl.DT["counts"]:
            pads = cl.dt_pads(n)
            self.assertEqual([p[0] for p in pads], [str(k) for k in range(1, n + 1)])
            if n != 3:
                cols, rows = cl.DT["layout"][n]
                self.assertEqual(cols * rows, n)
        tri = cl.dt_pads(3)  # 3-way is triangular (TE)
        self.assertAlmostEqual(math.dist(tri[0][1:], tri[2][1:]), math.dist(tri[0][1:], tri[1][1:]))
        self.assertEqual(cl.DT["pitch"][2], 5.46)
        self.assertEqual(cl.dupont_mpn(2, 4), "65043-033LF")  # FCI drawing 65043 rev BG
        self.assertEqual(cl.DUPONT["terminal"], "48235-000LF")
        self.assertAlmostEqual(cl.MICROFIT["mated"], 17.64)  # Molex 43045 vertical: mated height
        self.assertEqual(set(cl.POWER), {"XT30", "XT60", "XT90", "MR30"})
        self.assertEqual(cl.MICROFIT["counts"], (2, 4, 6, 8, 10, 12))

    def test_check_finds_problems(self):
        a = cl.Entry("A-1", "F", "Plug", "M", "a", 1, 1.0, mating=["B-1"], gauge_min=22, gauge_max=28,
                     pins=[cl.Pin("1", (0, 0, 0), (0, 0, 2))])
        b = cl.Entry("B-1", "F", "Header", "M", "b", 2, 1.0, mating=[], pins=[cl.Pin("1", (0, 0, 0), (0, 0, 1))])
        problems = "\n".join(cl.check([a, b]))
        for text in ("backwards", "not a unit vector", "does not list A-1", "1 pins for 2 positions",
                     "B-1: no mating part"):
            self.assertIn(text, problems)


@NEEDS_KICAD
class Catalogue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = cl.catalog()
        cls.by_mpn = {e.mpn: e for e in cls.entries}

    def test_sound(self):
        self.assertEqual(cl.check(self.entries), [])

    def test_unique_mpns_and_files(self):
        mpns = [e.mpn for e in self.entries]
        self.assertEqual(len(mpns), len(set(mpns)))
        self.assertEqual(len({e.file.lower() for e in self.entries}), len(self.entries))
        descriptions = [e.description.lower() for e in self.entries]
        self.assertEqual(len(descriptions), len(set(descriptions)))  # the import tells items apart by it

    def test_every_family_present(self):
        families = {e.family for e in self.entries}
        self.assertEqual(families, set(cl.FAMILY_ORDER))
        rows = {r["family"]: r for r in cl.summary(self.entries)}
        self.assertEqual(rows["JST-XH"]["positions"], [2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(rows["Deutsch DT"]["positions"], [2, 3, 4, 6, 8, 12])
        self.assertEqual(rows["Molex Micro-Fit 3.0"]["positions"], [2, 4, 6, 8, 10, 12])
        connectors = [e for e in self.entries if not cl.is_accessory(e)]
        self.assertEqual(sum(r["parts"] for r in rows.values()), len(connectors))
        self.assertEqual(sum(r["accessories"] for r in rows.values()), len(cl.ACCESSORIES))

    def test_terminals_seals_wedgelocks_are_parts(self):
        accessories = {e.mpn: e for e in self.entries if cl.is_accessory(e)}
        self.assertEqual(set(accessories), set(cl.ACCESSORIES))
        for e in self.entries:
            if cl.is_accessory(e):
                self.assertTrue(e.manufacturer and e.mpn, e.mpn)
                self.assertEqual((e.pins, e.mating), ([], []))
                continue
            for mpn, mfr in ((e.terminal, e.terminal_mfr), (e.seal, e.seal_mfr), (e.accessory, e.accessory_mfr)):
                if mpn:
                    self.assertIn(mpn, accessories, e.mpn)
                    self.assertEqual(mfr, accessories[mpn].manufacturer, e.mpn)
        xh = self.by_mpn["XHP-3"]
        self.assertEqual((xh.terminal, xh.terminal_mfr), ("SXH-001T-P0.6", "JST"))
        self.assertEqual(accessories["SXH-001T-P0.6"].role, "Terminal")
        self.assertEqual(accessories["114017"].role, "Seal")
        self.assertEqual(self.by_mpn["DT04-4P"].accessory, "W4P")
        self.assertEqual(accessories["W4P"].role, "Wedgelock")

    def test_mating_symmetric(self):
        for e in self.entries:
            for mate in e.mating:
                self.assertIn(e.mpn, self.by_mpn[mate].mating, f"{e.mpn} -> {mate}")
                self.assertEqual(self.by_mpn[mate].positions, e.positions)
        self.assertEqual(self.by_mpn["XHP-4"].mating, ["B4B-XH-A", "S4B-XH-A"])
        self.assertEqual(sorted(self.by_mpn["43025-0600"].mating), ["43020-0600", "43045-0612"])
        self.assertEqual(self.by_mpn["DT04-6P"].mating, ["DT06-6S"])

    def test_gauge_ranges_and_terminals(self):
        for e in self.entries:
            if e.role in ("Wire housing", "Receptacle", "Plug", "Bullet"):
                self.assertTrue(e.gauge_min and e.gauge_max, e.mpn)
                self.assertGreaterEqual(e.gauge_min, e.gauge_max, e.mpn)
                self.assertTrue(8 <= e.gauge_max <= e.gauge_min <= 32, e.mpn)
        self.assertEqual((self.by_mpn["XHP-3"].gauge_min, self.by_mpn["XHP-3"].gauge_max), (28, 22))
        self.assertEqual(self.by_mpn["PHR-5"].terminal, "SPH-002T-P0.5S")
        self.assertEqual(self.by_mpn["GHR-04V-S"].terminal, "SSHL-002T-P0.2")
        self.assertEqual(self.by_mpn["SHR-04V-S-B"].terminal, "SSH-003T-P0.2-H")
        self.assertEqual(self.by_mpn["DT04-2P"].terminal, "0460-202-16141")
        self.assertEqual(self.by_mpn["DT06-2S"].terminal, "0462-201-16141")
        self.assertEqual(self.by_mpn["DT06-2S"].seal, "")  # DT seals by its one-piece grommet, no wire seal
        self.assertIn("114017", self.by_mpn["DT06-2S"].notes)
        self.assertEqual(self.by_mpn["DT06-12S"].accessory, "W12S")
        self.assertEqual(self.by_mpn["XT60-M"].terminal, "")  # solder cup

    def test_pins_come_from_the_pads(self):
        pads = cl.footprint_pads("Connector_JST:JST_GH_BM04B-GHS-TBT_1x04-1MP_P1.25mm_Vertical")
        self.assertEqual([p[0] for p in pads], ["1", "2", "3", "4"])  # the MP pegs are not pins
        housing, header = self.by_mpn["GHR-04V-S"], self.by_mpn["BM04B-GHS-TBT"]
        self.assertEqual([p.name for p in housing.pins], ["1", "2", "3", "4"])
        for pin, (_n, x, y) in zip(housing.pins, pads):
            self.assertAlmostEqual(pin.pos[0], x)
            self.assertAlmostEqual(pin.pos[1], y)
        # KiCad's y points down: pad (x, 1.95) is at (x, -1.95) in the part
        self.assertAlmostEqual(housing.pins[0].pos[1], -1.95)
        self.assertEqual([p.pos for p in header.pins], [p.pos for p in housing.pins])  # mated: same frames
        self.assertEqual(header.frame_at, cl.FRAME_MATED)
        mf = cl.footprint_pads("Connector_Molex:Molex_Micro-Fit_3.0_43045-0412_2x02_P3.00mm_Vertical")
        self.assertEqual([(n, x, y) for n, x, y in mf], [("1", 0.0, 0.0), ("2", 3.0, 0.0), ("3", 0.0, -3.0),
                                                          ("4", 3.0, -3.0)])  # the two pegs (np holes) left out

    def test_wire_leaves_the_housing(self):
        xh = self.by_mpn["XHP-4"]
        self.assertTrue(all(p.dir == (0, 0, 1) and abs(p.pos[2] - 9.8) < 1e-9 for p in xh.pins))
        side = self.by_mpn["S4B-XH-A"]
        self.assertTrue(all(p.dir == (0, -1, 0) for p in side.pins))
        self.assertAlmostEqual(side.pins[0].pos[1], 2.30 - 14.3)  # 14.3 mm from the header's rear edge
        plug = self.by_mpn["DT06-4S"]
        self.assertTrue(all(p.dir == (0, 0, -1) for p in plug.pins))
        top = max(op[1][2] + op[2][2] for op in plug.body if op[0] == "box")
        self.assertLess(plug.pins[0].pos[2], top)

    def test_models_from_kicad(self):
        self.assertTrue(self.by_mpn["B4B-XH-A"].model.endswith("JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical.step"))
        self.assertTrue(self.by_mpn["XT60-M"].model.endswith("AMASS_XT60-M_1x02_P7.2mm_Vertical.step"))
        self.assertEqual(self.by_mpn["XHP-4"].model, "")  # KiCad has no wire housings
        self.assertEqual(self.by_mpn["XHP-4"].source, "parametric")
        self.assertIn("CC-BY-SA 4.0", self.by_mpn["B4B-XH-A"].license)
        for e in self.entries:
            self.assertTrue(e.model or e.body, e.mpn)
            if e.model:
                self.assertTrue(os.path.isfile(os.path.join(cl.models_dir(), e.model)), e.model)

    def test_manifest_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="bw-connlib-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = cl.write_manifest(self.entries, tmp)
        back = cl.read_manifest(path)
        self.assertEqual([e.as_dict() for e in back], [e.as_dict() for e in self.entries])
        with open(os.path.join(tmp, "manifest.csv"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(len(lines), len(self.entries) + 1)
        self.assertIn("B4B-XH-A; S4B-XH-A", "\n".join(lines))


def make_library(tmp):
    setup = vs.VaultSetup(name="Standard Libraries", folder=os.path.join(tmp, "Vault", "Standard Libraries"))
    setup.numbering.programs = [vs.ProgramSetup("STD", "Standard parts")]
    return vs.create_vault(setup, actor=ME)


def fake_fcstd(path, label, extra=""):
    """A minimal .FCStd (Document.xml with one App::Part carrying its MPN) so the vault can check it in."""
    xml = ('<?xml version="1.0" encoding="utf-8"?><Document SchemaVersion="4"><Objects Count="1">'
           '<Object type="App::Part" name="Connector"/></Objects><ObjectData Count="1"><Object name="Connector">'
           f'<Properties Count="2"><Property name="Label" type="App::PropertyString"><String value="{label}"/>'
           '</Property><Property name="ManufacturerPartNumber" type="App::PropertyString">'
           f'<String value="{label}"/></Property></Properties></Object></ObjectData>{extra}</Document>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Document.xml", xml)


class BergStikAndStrip(unittest.TestCase):
    def test_bergstik_mpns(self):
        self.assertEqual(cl.bergstik_mpn("v", 1, 2), "68000-102HLF")
        self.assertEqual(cl.bergstik_mpn("r", 1, 2), "68016-102HLF")
        self.assertEqual(cl.bergstik_mpn("v", 2, 4), "67996-108HLF")
        self.assertEqual(cl.bergstik_mpn("r", 2, 4), "68020-108HLF")
        by = {e.mpn: e for e in cl.catalog()}
        self.assertNotIn("HDR254-M-1x04", by)
        self.assertEqual(by["68000-104HLF"].previous, ["HDR254-M-1x04"])
        self.assertEqual(by["68000-104HLF"].manufacturer, "Amphenol FCI")
        self.assertIn("NOT individually confirmed", by["68000-104HLF"].notes)
        self.assertNotIn("NOT individually", by["68000-103HLF"].notes)
        right = by["68020-108HLF"]
        self.assertEqual(len(right.pins), 8)
        self.assertEqual(right.pins[0].dir, (1, 0, 0))

    def test_strip_length(self):
        by = {e.mpn: e for e in cl.catalog()}
        self.assertEqual(by["DT04-4P"].strip_length, 7.1)
        self.assertEqual(by["XHP-4"].strip_length, 0.0)  # no published value on hand


class VaultImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-connlib-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = make_library(self.tmp)
        self.lib = os.path.join(self.tmp, "library")
        os.makedirs(self.lib)
        pin = [cl.Pin("1", (0, 0, 1), (0, 0, 1))]
        self.entries = [
            cl.Entry("A-1", "F", "Plug", "M", "Connector A (A-1)", 1, 1.0, mating=["B-1"], pins=pin, file="A-1.FCStd"),
            cl.Entry("B-1", "F", "Header", "M", "Connector B (B-1)", 1, 1.0, mating=["A-1"], pins=pin, file="B-1.FCStd"),
            cl.Entry("C-1", "F", "Header", "M", "Connector C (C-1)", 1, 1.0, pins=pin, file="C-1.FCStd"),
        ]
        for e in self.entries[:2]:  # C-1 is not built
            fake_fcstd(os.path.join(self.lib, e.file), e.mpn)

    def numbering(self):
        with open(vs.numbering_path(self.vault.root), encoding="utf-8") as handle:
            return handle.read()

    def test_dry_run_numbers_and_writes_nothing(self):
        before = self.numbering()
        plans = cl.plan_import(self.vault, self.entries, self.lib)
        self.assertEqual([p.part_number for p in plans], ["STD-01-01001-001", "STD-01-01002-001", ""])
        self.assertEqual(plans[2].skip, "part file not built")
        self.assertEqual(self.numbering(), before)
        self.assertEqual(self.vault.items, {})

    def test_import_as_cots_items_once(self):
        written = []
        plans = cl.import_library(self.vault, self.entries, self.lib, writer=lambda p, k: written.append(k))
        self.assertEqual([p.part_number for p in plans], ["STD-01-01001-001", "STD-01-01002-001", ""])
        self.assertEqual(written, ["STD-01-01001-001", "STD-01-01002-001"])
        item = self.vault.model.items["STD-01-01001-001"]
        self.assertEqual((item.item_type, item.description), ("cots", "Connector A (A-1)"))
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "A-1.FCStd")))  # the library is only read
        self.assertFalse(os.path.exists(os.path.join(self.vault.root, vs.STAGING_DIR)))
        again = cl.import_library(self.vault, self.entries, self.lib)
        self.assertEqual([p.part_number for p in again], ["", "", ""])
        self.assertIn("already in the vault as STD-01-01001-001", again[0].skip)
        # the dry run agrees once the parts are in
        self.assertEqual([p.part_number for p in cl.plan_import(self.vault, self.entries, self.lib)], ["", "", ""])

    def test_accessories_first_and_their_numbers_reach_the_pins(self):
        term = cl.Entry("T-1", "F", "Terminal", "Acme", "Crimp terminal T (T-1)", 0, 0.0, file="T-1.FCStd")
        fake_fcstd(os.path.join(self.lib, term.file), "T-1")
        self.entries[0].terminal = "T-1"
        seen = []
        plans = cl.import_library(self.vault, self.entries + [term], self.lib,
                                  item_writer=lambda path, items: seen.append(dict(items)))
        self.assertEqual([(p.mpn, p.part_number) for p in plans][:3],
                         [("T-1", "STD-01-01001-001"), ("A-1", "STD-01-01002-001"), ("B-1", "STD-01-01003-001")])
        self.assertEqual(seen, [{"T-1": "STD-01-01001-001"}] * 2)
        report = cl.write_numbers(plans, self.entries + [term], os.path.join(self.tmp, "numbers.csv"))
        with open(report, encoding="utf-8") as handle:
            rows = handle.read().splitlines()
        self.assertEqual(rows[0], "part_number,manufacturer,mpn,description,role,status")
        self.assertIn("STD-01-01001-001,Acme,T-1,Crimp terminal T (T-1),Terminal,new", rows)
        # a second run finds the terminal in the vault and still hands its number on
        seen.clear()
        cl.import_library(self.vault, self.entries + [term], self.lib, item_writer=lambda p, i: seen.append(i))
        self.assertEqual(seen, [])  # nothing new to write

    def test_restore_view_data(self):
        original = os.path.join(self.lib, "A-1.FCStd")
        with zipfile.ZipFile(original, "a") as zf:
            zf.writestr("GuiDocument.xml", "<Document/>")
            zf.writestr("ShapeAppearance", "x")
        staged = os.path.join(self.tmp, "staged.FCStd")
        fake_fcstd(staged, "A-1")
        self.assertTrue(cl.restore_view_data(original, staged))
        with zipfile.ZipFile(staged) as zf:
            self.assertIn("GuiDocument.xml", zf.namelist())
            self.assertIn("ShapeAppearance", zf.namelist())
        self.assertFalse(cl.restore_view_data(original, staged))  # already there

    def test_update_path(self):
        import copy

        cl.import_library(self.vault, self.entries, self.lib)  # A-1, B-1 in as WIP 01
        self.assertEqual(cl.vault_mpns(self.vault), {"A-1": "STD-01-01001-001", "B-1": "STD-01-01002-001"})
        previous = copy.deepcopy(self.entries)
        new = copy.deepcopy(self.entries)
        new[0].mfr_model = "F/A-1.step"  # a manufacturer body for A-1
        new[1].mpn, new[1].previous, new[1].file = "B-2", ["B-1"], "B-2.FCStd"  # B-1 renamed to B-2
        fake_fcstd(os.path.join(self.lib, "A-1.FCStd"), "A-1", extra="<!-- new body -->")
        fake_fcstd(os.path.join(self.lib, "B-2.FCStd"), "B-2")
        plans = {p.mpn: p for p in cl.plan_update(self.vault, new, previous)}
        self.assertEqual((plans["A-1"].action, plans["A-1"].changes), ("update", ["mfr_model"]))
        self.assertEqual((plans["B-2"].part_number, plans["B-2"].action), ("STD-01-01002-001", "update"))
        self.assertEqual(plans["B-2"].changes, ["mpn"])
        self.assertEqual(plans["C-1"].action, "new")
        self.assertEqual(self.vault.versions["STD-01-01001-001"].__len__(), 1)  # the plan changed nothing
        written = []
        done = cl.update_library(self.vault, new, previous, self.lib,
                                 writer=lambda path, pn, rev: written.append((pn, rev)))
        self.assertEqual(sorted(written), [("STD-01-01001-001", "01"), ("STD-01-01002-001", "01")])
        self.assertEqual(len(self.vault.versions["STD-01-01001-001"]), 2)  # a new version of WIP 01
        latest = self.vault.latest_version("STD-01-01001-001")
        self.assertIn("update: mfr_model", latest.comment)
        self.assertEqual(self.vault.locks, {})
        self.assertEqual(cl.vault_mpns(self.vault)["B-2"], "STD-01-01002-001")  # found by its new MPN now
        self.assertEqual({p.mpn: p.action for p in done}["C-1"], "new")
        # an import after the rename does not add B-2 again
        self.assertIn("already in the vault as STD-01-01002-001",
                      {p.mpn: p.skip for p in cl.plan_import(self.vault, new, self.lib)}["B-2"])
        # a released item is refused: a change needs an ECR and an ECO
        self.vault.submit("STD-01-01001-001")
        self.vault.approve("STD-01-01001-001", note="released for the test")
        new[0].notes = "changed again"
        plan = {p.mpn: p for p in cl.plan_update(self.vault, new, previous)}["A-1"]
        self.assertEqual(plan.action, "refused")
        self.assertIn("ECR and an ECO", plan.why)

    def test_cli_dry_run(self):
        path = cl.write_manifest(self.entries, self.lib)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["format"], "bruceworx.connector-library")
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cl.main(["import", "--vault", self.vault.root, "--manifest", path, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("2 of 3 parts would be imported", out.getvalue())
        self.assertEqual(self.vault.items, {})


if __name__ == "__main__":
    unittest.main()
