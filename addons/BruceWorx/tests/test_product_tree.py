# SPDX-License-Identifier: LGPL-2.1-or-later
"""The product tree with drawings (bruceworx/product_tree.py, PDM-09 step 5): when a drawing is out
of date, the Drawing cell of a row, the counts an assembly row carries, the root picker."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm_folder  # noqa: E402
from bruceworx import product_tree as pt  # noqa: E402
from bruceworx import vault_trees as vt  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402

TOP = "DM-02-01001-001"
SUB = "DM-02-01002-001"
RAIL = "DM-01-01001-001"
PLATE = "DM-01-01002-001"
CASTER = "DM-01-01003-001"  # cots
TOP_DWG = "DM-03-01001-001"
SUB_DWG = "DM-03-01002-001"
RAIL_DWG = "DM-03-01003-001"
ME = "bruce@mac"

BOX = pt.make_fingerprint(1000.0, 600.0, (5, 5, 5), (0, 0, 0, 10, 10, 10))
TALLER = pt.make_fingerprint(1200.0, 680.0, (5, 5, 6), (0, 0, 0, 10, 10, 12))


class Cart(unittest.TestCase):
    """A cart: top -> sub (x2) -> rail (x2), plate; the top also holds four casters (COTS)."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-tree-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.v = FolderVault.init(os.path.join(self.tmp, "vault"), "Cart", actor=ME)
        v = self.v
        v.create_item(TOP, "assembly", "Cart")
        v.create_item(SUB, "assembly", "Frame")
        v.create_item(RAIL, "part", "Side rail")
        v.create_item(PLATE, "part", "Deck plate")
        v.create_item(CASTER, "cots", "Caster 4 in")
        v.create_item(TOP_DWG, "drawing", "Cart drawing", drawing_of=TOP)
        v.create_item(SUB_DWG, "drawing", "Frame drawing", drawing_of=SUB)
        v.create_item(RAIL_DWG, "drawing", "Side rail drawing", drawing_of=RAIL)
        self.n = 0

    def checkin(self, pn, comment, **extras):
        self.n += 1
        path = os.path.join(self.src, pn + ".FCStd")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", f"<Document>{pn} {self.n}</Document>")
        if self.v.locks.get(pn) != ME:
            self.v.checkout(pn)
        return self.v.checkin(pn, path, comment=comment, **extras)

    def build(self):
        """Every file once, the drawings made from the parts as they are."""
        v = self.v
        rail = self.checkin(RAIL, "rail", fingerprint=BOX)
        plate = self.checkin(PLATE, "plate", fingerprint=BOX)
        sub = self.checkin(SUB, "frame")
        top = self.checkin(TOP, "cart")
        v.set_bom(SUB, [(RAIL, 2)])
        v.set_bom(TOP, [(SUB, 2), (PLATE, 1), (CASTER, 4)])
        self.checkin(RAIL_DWG, "rail drawing", made_from={RAIL: rail.sha256})
        self.checkin(SUB_DWG, "frame drawing", made_from={SUB: sub.sha256, RAIL: rail.sha256})
        self.checkin(TOP_DWG, "cart drawing", made_from={TOP: top.sha256, SUB: sub.sha256, RAIL: rail.sha256,
                                                         PLATE: plate.sha256})

    def tree(self):
        return pt.Tree(self.v, vt.structure(self.v), holds_page=lambda pn: False)


class GraphTests(Cart):
    def test_closure_and_roll_up(self):
        self.build()
        bom = vt.structure(self.v)["bom"]
        self.assertEqual(pt.closure(bom, TOP), [TOP, SUB, PLATE, CASTER, RAIL])
        self.assertEqual(pt.roll_up(bom, TOP), {SUB: 2, PLATE: 1, CASTER: 4, RAIL: 4})
        self.assertEqual(pt.parents_of(bom)[RAIL], [SUB])

    def test_fingerprints_round_to_a_millionth(self):
        a = pt.make_fingerprint(1000.0000001, 600, (5, 5, 5), (0, 0, 0, 10, 10, 10))
        self.assertTrue(pt.fingerprints_equal(a, BOX))
        self.assertFalse(pt.fingerprints_equal(TALLER, BOX))
        self.assertFalse(pt.fingerprints_equal(None, BOX))


class DrawingStateTests(Cart):
    def test_everything_current_after_the_build(self):
        self.build()
        t = self.tree()
        for pn in (RAIL, SUB, TOP):
            self.assertEqual(t.cell(pn)["state"], pt.CURRENT, pn)
            self.assertEqual(list(t.cell(pn)["states"].values())[0]["basis"], "made_from")

    def test_a_geometry_change_marks_exactly_the_drawings_above_it(self):
        """Acceptance: changing one part marks its drawing and the drawings of the assemblies above it."""
        self.build()
        self.checkin(RAIL, "rail 2 mm longer", fingerprint=TALLER)
        t = self.tree()
        self.assertEqual(t.cell(RAIL)["state"], pt.OUT_OF_DATE)
        self.assertEqual(t.cell(SUB)["state"], pt.OUT_OF_DATE)
        self.assertEqual(t.cell(TOP)["state"], pt.OUT_OF_DATE)
        self.assertEqual(t.cell(PLATE)["state"], pt.MISSING)  # untouched: it never had a drawing
        found = t.cell(TOP)["states"][TOP_DWG]
        self.assertEqual(found["changed"], [(RAIL, "geometry")])
        self.assertIn("older " + RAIL, pt.state_text(found))
        self.assertEqual(t.out_of_date(TOP), [TOP_DWG, SUB_DWG, RAIL_DWG])
        self.assertEqual(t.out_of_date(SUB), [SUB_DWG, RAIL_DWG])

    def test_a_metadata_change_is_grey(self):
        self.build()
        self.checkin(RAIL, "description only", fingerprint=BOX)  # new hash, same geometry
        t = self.tree()
        self.assertEqual(t.cell(RAIL)["state"], pt.METADATA_ONLY)
        self.assertEqual(t.cell(TOP)["state"], pt.METADATA_ONLY)
        self.assertFalse(t.matches(RAIL, "out_of_date"))
        self.checkin(PLATE, "plate thicker", fingerprint=TALLER)
        t = self.tree()
        self.assertEqual(t.cell(TOP)["state"], pt.OUT_OF_DATE)  # one geometry change outranks the metadata ones
        self.assertEqual(t.cell(SUB)["state"], pt.METADATA_ONLY)

    def test_a_drawing_made_from_the_new_version_is_current_again(self):
        self.build()
        rail = self.checkin(RAIL, "rail longer", fingerprint=TALLER)
        self.checkin(RAIL_DWG, "views updated", made_from={RAIL: rail.sha256})
        t = self.tree()
        self.assertEqual(t.cell(RAIL)["state"], pt.CURRENT)
        self.assertEqual(t.cell(SUB)["state"], pt.OUT_OF_DATE)  # the frame drawing still shows the old rail

    def test_a_new_child_the_drawing_never_saw_is_a_change(self):
        self.build()
        self.v.set_bom(SUB, [(RAIL, 2), (PLATE, 1)])
        t = self.tree()
        self.assertEqual(t.cell(SUB)["states"][SUB_DWG]["changed"], [(PLATE, "geometry")])

    def test_time_comparison_for_drawings_before_pdm09(self):
        rail = self.checkin(RAIL, "rail")
        self.checkin(RAIL_DWG, "drawing, no made_from")
        t = self.tree()
        found = t.cell(RAIL)["states"][RAIL_DWG]
        self.assertEqual((found["state"], found["basis"]), (pt.CURRENT, "time"))
        self.assertIn("time comparison", pt.state_text(found))
        self.checkin(RAIL, "rail again")
        found = self.tree().cell(RAIL)["states"][RAIL_DWG]
        self.assertEqual((found["state"], found["basis"], found["changed"]), (pt.OUT_OF_DATE, "time", [(RAIL, "newer")]))
        self.assertTrue(rail.sha256 != self.v.latest_version(RAIL).sha256)

    def test_released_drawing_compares_with_the_released_model(self):
        self.build()
        for pn in (RAIL, RAIL_DWG, PLATE, CASTER, SUB, SUB_DWG, TOP, TOP_DWG):
            self.v.submit(pn)
            self.v.approve(pn, note="released")
        # An ECO on the rail opens the rail and its own drawing at B; the frame's and cart's released
        # drawings compare with the released rail A, so they stay current until their own ECO.
        self.v.raise_ecr(None, "longer rail", [RAIL])
        self.v.decide_ecr("ECR-0001", True, "agreed")
        self.v.open_eco(None, ["ECR-0001"])
        self.checkin(RAIL, "rail B in work", fingerprint=TALLER)
        t = self.tree()
        self.assertEqual(t.cell(RAIL)["state"], pt.OUT_OF_DATE)  # its drawing B is in work and still shows A
        self.assertEqual(t.cell(SUB)["state"], pt.CURRENT)
        self.assertEqual(t.cell(TOP)["state"], pt.CURRENT)


class CellTests(Cart):
    def test_cells_without_a_drawing(self):
        self.build()
        t = self.tree()
        self.assertEqual(t.cell(PLATE)["state"], pt.MISSING)
        self.assertEqual(t.cell(CASTER)["state"], pt.NOT_NEEDED)
        t = pt.Tree(self.v, vt.structure(self.v), holds_page=lambda pn: pn == PLATE)
        self.assertEqual(t.cell(PLATE)["state"], pt.IN_MODEL)

    def test_a_drawing_item_without_a_file(self):
        self.checkin(RAIL, "rail")
        self.assertEqual(self.tree().cell(RAIL)["state"], pt.NO_FILE)
        self.assertTrue(self.tree().matches(RAIL, "needs_drawing"))

    def test_file_holds_page(self):
        path = os.path.join(self.src, "p.FCStd")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", '<Document><Object type="TechDraw::DrawPage" name="Page"/></Document>')
        self.assertTrue(pt.file_holds_page(path))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", '<Document><Object type="App::Part" name="Part"/></Document>')
        self.assertFalse(pt.file_holds_page(path))
        self.assertFalse(pt.file_holds_page(path + ".missing"))


class TreeTests(Cart):
    def test_rows_counts_and_filters(self):
        self.build()
        self.checkin(RAIL, "rail longer", fingerprint=TALLER)
        self.v.checkout(PLATE)
        t = self.tree()
        rows = t.children(TOP)
        self.assertEqual([(r["pn"], r["qty"]) for r in rows], [(SUB, 2), (PLATE, 1), (CASTER, 4)])
        sub = rows[0]
        self.assertEqual((sub["has_children"], sub["source"]), (True, "bom"))
        self.assertEqual(sub["counts"], {"needs_drawing": 0, "out_of_date": 1, "checked_out": 0})
        top = t.row(TOP)
        self.assertEqual(top["counts"], {"needs_drawing": 1, "out_of_date": 2, "checked_out": 1})
        self.assertEqual([r["pn"] for r in t.children(TOP, "out_of_date")], [SUB])
        self.assertEqual([r["pn"] for r in t.children(SUB, "out_of_date")], [RAIL])
        self.assertEqual([r["pn"] for r in t.children(TOP, "needs_drawing")], [PLATE])
        self.assertEqual([r["pn"] for r in t.children(TOP, "checked_out")], [PLATE])
        self.assertEqual(t.children(TOP, "checked_out")[0]["lock"], ME)
        flat = t.flat(TOP)
        self.assertEqual([(r["pn"], r["qty"]) for r in flat], [(RAIL, 4), (PLATE, 1), (CASTER, 4), (SUB, 2)])
        self.assertEqual([r["pn"] for r in t.flat(TOP, "out_of_date")], [RAIL, SUB])

    def test_lines_from_links_are_marked(self):
        self.build()
        v = FolderVault(self.v.root, actor=ME)
        s = vt.structure(v, links=lambda pn: {RAIL: 2} if pn == SUB else {})
        s["bom"].pop(SUB)
        s["sources"][SUB] = "links"
        s["bom"][SUB] = [[RAIL, 2]]
        t = pt.Tree(v, s, holds_page=lambda pn: False)
        self.assertEqual(t.row(SUB)["source"], "links")

    def test_roots_list_programs_then_assemblies(self):
        self.build()
        self.v.create_project("CART", "Shop cart", "DM", TOP, units="in")
        with open(os.path.join(self.v.root, "numbering.json"), "w", encoding="utf-8") as handle:
            handle.write('{"programs": {"DM": {"description": "Demo"}, "OW": {"description": "Overwatch"}}}')
        found = pt.roots(self.v, vt.structure(self.v))
        self.assertEqual([r["kind"] for r in found], ["program", "program", "assembly", "assembly"])
        self.assertEqual(found[0]["key"], "program:DM")
        self.assertEqual(found[0]["text"], "DM  Demo")
        self.assertEqual(found[0]["members"], [TOP])  # the rest sit under the cart
        self.assertEqual(found[0]["units"], "in")
        self.assertEqual(found[1]["key"], "program:OW")
        self.assertEqual(found[1]["members"], [])
        self.assertEqual([r["pn"] for r in found[2:]], [TOP, SUB])
        self.assertEqual(pt.where_used_text(vt.structure(self.v)["bom"], vt.structure(self.v), RAIL),
                         f"used in {SUB}-01")


if __name__ == "__main__":
    unittest.main()
