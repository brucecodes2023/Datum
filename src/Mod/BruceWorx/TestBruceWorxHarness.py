# SPDX-License-Identifier: LGPL-2.1-or-later
"""BW-ROU-06: the harness as a vault item, stock material, and the purchasing BOM.

Two harnesses are routed in a parent assembly on a scratch vault (never a real one): a Harness container groups each
one's paths and wires (the parent BOM already reads through it), Make vault item saves it as its own file under a new
part number (category "Harnesses"), the parent links to it, and the path points attach to the parent's parts through
cross-document links. Moving a part in the parent re-routes the harness with both files open; opened alone (parent
file gone) the harness keeps its last solved points. Both wires are "Wire, 22 AWG, red, PVC", one Stock material item;
the purchasing BOM sums their lengths over the harnesses and quantities, adds the 10 % scrap and rounds up to a spool.

Run: FreeCADCmd -t TestBruceWorxHarness
"""

import math
import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part  # noqa: F401  (Part::FeaturePython)

import bruceworx_bom as bb
import bruceworx_harness as bh
import bruceworx_routing as br
import bruceworx_routing_ports as bp
import bruceworx_stock as bs
from bruceworx import bom, stock
from bruceworx import pdm_workspace as pw
from bruceworx import vault_setup as vs

V = App.Vector


class Session:
    """What the vault client's Session offers the harness code."""

    def __init__(self, vault, root):
        self.vault, self.root = vault, root
        self.work = pw.Workspace(vault)

    def refresh(self):
        self.vault.refresh()


def close_all():
    for name in list(App.listDocuments()):
        App.closeDocument(name)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        os.environ["BW_PDM_ACTOR"] = "harness@mac"
        self.tmp = tempfile.mkdtemp(prefix="bwharness")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(close_all)
        setup = vs.VaultSetup(name="Harness", folder=os.path.join(self.tmp, "vault"))
        setup.numbering.programs = [vs.ProgramSetup("LE")]
        setup.numbering.document_types = {"01": "Part", "02": "Assembly", "03": "Drawing", "04": "Cable assembly"}
        self.vault = vs.create_vault(setup, actor="harness@mac")
        self.s = Session(self.vault, self.vault.root)
        self.doc = App.newDocument("BWHarnessParent")
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.links, self.parts = [], []
        for name, x in (("Motor", 0), ("Board", 100)):
            part = self.doc.addObject("App::Part", name)
            box = self.doc.addObject("Part::Box", name + "Body")
            box.Length, box.Width, box.Height = 10, 10, 10
            part.addObject(box)
            bp.make_pin(part, "1", ((0, 0, 0), (0, 0, 1), (1, 0, 0)), GaugeMin=28, GaugeMax=18)
            link = self.doc.addObject("App::Link", name + "Link")
            link.LinkedObject = part
            link.Placement = App.Placement(V(x, 0, 0), App.Rotation())
            self.asm.addObject(link)
            self.links.append(link)
            self.parts.append(part)
        self.doc.recompute()
        self.parent_path = os.path.join(self.s.work.root, "Parent.FCStd")
        os.makedirs(self.s.work.root, exist_ok=True)
        self.doc.saveAs(self.parent_path)

    def pin(self, i):
        return bp.frame_ref(self.links[i], bp.pin(self.parts[i], "1"))

    def route(self, z, colour=(0.85, 0.1, 0.1)):
        """A path from the motor to the board at height z, attached to both links, with a red wire on it."""
        path = br.make_path([(0, 0, z), (50, 10, z + 20), (100, 0, z)], self.doc, radius=5.0,
                            attachments=[(0, *self.pin(0)), (2, *self.pin(1))])
        wire = br.make_wire(path, gauge="22 AWG", colour=colour, slack=0)
        self.doc.recompute()
        return path, wire

    def stock_item(self, wire, unit=0.0):
        pn, made = bs.make_from_item(self.s, wire, "Alpha Wire", "3050 RD005", 10.0, unit)
        return pn, made

    def make_item(self, z, description):
        path, wire = self.route(z)
        stock_pn, _made = self.stock_item(wire, unit=30.0)
        container = bh.make_harness(self.doc, [wire])
        pn, _link = bh.make_vault_item(container, self.s, description)
        return pn, stock_pn

    # ---- container --------------------------------------------------------

    def test_container_groups_and_the_parent_bom_reads_through_it(self):
        path, wire = self.route(0)
        pn, made = self.stock_item(wire)
        self.assertTrue(made)
        self.assertEqual(self.stock_item(wire)[0], pn)  # found again by its key, not numbered twice
        container = bh.make_harness(self.doc, [wire], harness_type="Cable assembly")
        self.assertTrue(bh.is_harness(container))
        self.assertIn(container, self.asm.Group)
        self.assertEqual({o.Name for o in bh.contents(container)}, {path.Name, wire.Name})
        self.assertIs(bh.harness_of(wire), container)
        with self.assertRaises(ValueError):
            bh.make_harness(self.doc, [wire])  # already in one
        rows = [r for r in bb.walk(self.doc) if r["keys"] == [pn]]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["qty"], wire.QuantityPer)
        self.assertEqual((rows[0]["unit"], rows[0]["manufacturer"], rows[0]["mpn"]), ("m", "Alpha Wire", "3050 RD005"))
        self.assertEqual(bh.suggested_type([wire]), "Cable assembly")

    # ---- the vault item, cross-file attachments ----------------------------

    def test_make_vault_item_follows_a_moved_part_and_opens_alone(self):
        path, wire = self.route(0)
        length = float(wire.QuantityPer)
        stock_pn, _ = self.stock_item(wire, unit=30.0)
        self.assertEqual(self.vault.items[stock_pn].category, stock.CATEGORY)
        names = (wire.Name, path.Name)
        container = bh.make_harness(self.doc, [wire])
        pn, link = bh.make_vault_item(container, self.s, "Motor harness")
        item = self.vault.items[pn]
        self.assertEqual((item.item_type, item.category, item.description), ("assembly", "Harnesses", "Motor harness"))
        self.assertEqual(pn.split("-")[1], "04")  # document type "Cable assembly" of this vault
        self.assertEqual([o for o in self.doc.Objects if o.Name in names], [])
        self.assertIn(link, self.asm.Group)
        # the harness BOM was recorded: the stock item by length, with manufacturer and MPN
        lines = self.vault.bom_of(pn)
        self.assertEqual([(l.child, l.unit, l.mpn) for l in lines], [(stock_pn, "m", "3050 RD005")])
        self.assertAlmostEqual(lines[0].qty, length, places=6)
        # the parent file's BOM has the harness as a line of its own
        parent_rows = bb.walk(self.doc)
        self.assertIn(pn, [k for r in parent_rows for k in r["keys"]])
        # both files open: a moved part re-routes the harness
        hdoc = next(d for d in App.listDocuments().values() if d.Label == pn)
        hpath = next(o for o in hdoc.Objects if br.is_path(o))
        self.assertEqual(len(hpath.XAttachments), 2)
        self.assertEqual(sorted(i for i, _t, _s in br.attachments_of(hpath)), [0, 2])
        start = V(hpath.Points[0])
        self.assertAlmostEqual(start.x, 0.0, places=3)
        self.links[0].Placement = App.Placement(V(0, 15, 7), App.Rotation())
        self.doc.recompute()
        hdoc.recompute()
        moved = V(hpath.Points[0])
        self.assertAlmostEqual((moved - V(0, 15, 7)).Length, 0.0, places=3)
        self.assertAlmostEqual(V(hpath.Points[2]).x, 100.0, places=3)  # the board did not move
        # saved, then the harness alone: the parent file is gone, the points stay where they were solved
        self.doc.recompute()
        self.doc.save()
        self.s.work.checkout(pn, with_links=False)  # the checked-in file is read-only until it is checked out again
        hdoc.save()
        solved = [tuple(V(p)) for p in hpath.Points]
        close_all()
        os.rename(self.parent_path, self.parent_path + ".away")
        alone = App.openDocument(self.s.work.path(pn), True)
        apath = next(o for o in alone.Objects if br.is_path(o))
        alone.recompute()
        self.assertEqual([tuple(V(p)) for p in apath.Points], solved)
        self.assertGreater(apath.Length.Value, 100.0)
        self.assertEqual(len(alone.getObject("Harness").Group) > 0, True)
        rows = [r for r in bb.walk(alone) if r["keys"] == [stock_pn]]
        self.assertAlmostEqual(rows[0]["qty"], length, places=2)

    # ---- the purchasing BOM -------------------------------------------------

    def test_purchasing_sums_two_harnesses_with_scrap_and_spool(self):
        close_all()
        self.doc = App.openDocument(self.parent_path)
        self.asm = self.doc.getObject("Assembly")
        self.links = [self.doc.getObject("MotorLink"), self.doc.getObject("BoardLink")]
        self.parts = [self.doc.getObject("Motor"), self.doc.getObject("Board")]
        pn1, stock1 = self.make_item(0, "Motor harness")
        pn2, stock2 = self.make_item(30, "Battery harness")
        self.assertEqual(stock1, stock2)  # one stock item, "22 AWG red PVC"
        lengths = {}
        for pn in (pn1, pn2):
            lines = self.vault.bom_of(pn)
            self.assertEqual([l.child for l in lines], [stock1])
            lengths[pn] = lines[0].qty
        # a product with 2 motor harnesses and 1 battery harness
        top = "LE-02-09001-001"
        self.vault.create_item(top, "assembly", "Drone")
        self.vault.set_bom(top, [(pn1, 2, 1), (pn2, 1, 2)])
        total = 2 * lengths[pn1] + lengths[pn2]
        info = stock.info_from_vault(self.vault, [stock1])[stock1]
        self.assertEqual((info["scrap"], info["unit"]), (10.0, 30.0))
        rows = bom.purchasing_rows(self.vault, top)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["part_number"], stock1)
        self.assertAlmostEqual(row["used"], total, places=6)
        self.assertAlmostEqual(row["buy"], math.ceil(1.1 * total / 30.0) * 30.0)  # one 30 m spool
        self.assertEqual((row["manufacturer"], row["mpn"]), ("Alpha Wire", "3050 RD005"))
        free = bom.purchasing_rows(self.vault, top, info={stock1: {"scrap": 10.0, "unit": 0.0}})
        self.assertAlmostEqual(free[0]["buy"], 1.1 * total, places=5)  # no rounding: 10 % scrap only
        text = bom.purchasing_csv(self.vault, top)
        self.assertIn(f'{stock1},"Wire, 22 AWG, red, PVC",Alpha Wire,3050 RD005', text)
        self.assertIn("Alpha Wire,3050 RD005", text)
        self.assertIn(",10 %,30 m,30 m", text)

    def test_stock_keys_by_kind(self):
        path, wire = self.route(0)
        self.assertEqual(bs.stock_of(wire), ("Wire", "wire|22 awg|red|pvc", "Wire, 22 AWG, red, PVC"))
        pipe = br.make_pipe(path, size="Custom", od=8.0, wall=1.0, material="Nylon")
        self.assertEqual(bs.stock_of(pipe)[0:2], ("Tube", "tube|8 x 1|nylon"))
        hose = br.make_hose(path, inner=6.0, od=10.0)
        self.assertEqual(bs.stock_of(hose)[1], "hose|6 x 10")
        self.assertEqual(bh.suggested_type([pipe, hose]), "Tube assembly")


if __name__ == "__main__":
    unittest.main()
