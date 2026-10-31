# SPDX-License-Identifier: LGPL-2.1-or-later
"""New Project's engine (bruceworx/project_setup.py, PDM-09 step 6): the parser (indented text, a
spreadsheet paste, CSV), the checks, the number plan, the order of work, duplicates and the roll-up."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import project_setup as ps  # noqa: E402
from bruceworx import vault_setup as vs  # noqa: E402

CART = """Shop cart
  Frame x2
    Side rail x2
    Cross bar
  Deck plate
  Caster 4 in x4 cots
"""


class ParserTests(unittest.TestCase):
    def test_the_cart(self):
        plan = ps.parse_structure(CART)
        self.assertTrue(plan.ok, [i.text for i in plan.issues])
        root = plan.root
        self.assertEqual((root.name, root.kind), ("Shop cart", "assembly"))
        self.assertEqual([(c.name, q) for c, q in root.children], [("Frame", 2), ("Deck plate", 1), ("Caster 4 in", 4)])
        frame = root.children[0][0]
        self.assertEqual((frame.kind, [(c.name, q, c.kind) for c, q in frame.children]),
                         ("assembly", [("Side rail", 2, "part"), ("Cross bar", 1, "part")]))
        self.assertEqual(plan.nodes["caster 4 in"].kind, "cots")
        self.assertEqual([n.name for n in ps.order_of_work(plan)],
                         ["Side rail", "Cross bar", "Frame", "Deck plate", "Caster 4 in", "Shop cart"])
        self.assertEqual(ps.roll_up(plan), {"frame": 2, "side rail": 4, "cross bar": 2, "deck plate": 1, "caster 4 in": 4})
        self.assertEqual(ps.summary(plan)["text"], "11 parts in all")
        self.assertEqual(ps.summary(plan)["files"], 6 + 5)  # six items, five drawings (no drawing for the caster)

    def test_quantity_forms_tabs_and_blank_lines(self):
        plan = ps.parse_structure("Top\n\tRail x 2\n\n\tBar 3x\n  # a comment\n\tNut ×5 cots\n")
        self.assertTrue(plan.ok, [i.text for i in plan.issues])
        self.assertEqual([(c.name, q, c.kind) for c, q in plan.root.children],
                         [("Rail", 2, "part"), ("Bar", 3, "part"), ("Nut", 5, "cots")])

    def test_same_name_is_one_item_used_twice(self):
        plan = ps.parse_structure("Top\n  Left\n    Bolt x4\n  Right\n    Bolt x4\n")
        self.assertTrue(plan.ok)
        self.assertEqual(plan.duplicates, ["Bolt"])
        self.assertEqual(plan.nodes["bolt"].uses, 2)
        self.assertEqual(ps.roll_up(plan)["bolt"], 8)
        rows = ps.preview_rows(plan)
        bolts = [r for r in rows if r["name"] == "Bolt"]
        self.assertEqual(len(bolts), 2)  # shown where typed, both marked
        self.assertTrue(all(r["duplicate"] and r["uses"] == 2 for r in bolts))
        self.assertEqual([i.level for i in plan.issues], ["info"])
        self.assertEqual(len(ps.order_of_work(plan)), 4)  # the bolt is made once

    def test_existing_part_number_reuses_the_item(self):
        plan = ps.parse_structure("Top\n  LE-01-01001-001 Side rail x2\n  LE-09-00001-001\n")
        self.assertTrue(plan.ok, [i.text for i in plan.issues])
        rail = plan.root.children[0][0]
        self.assertEqual((rail.pn, rail.name, rail.existing, rail.wants_drawing), ("LE-01-01001-001", "Side rail", True, False))
        self.assertEqual(plan.root.children[1][0].name, "LE-09-00001-001")
        self.assertEqual([n.name for n in ps.order_of_work(plan)], ["Top"])

    def test_errors(self):
        texts = {
            "": "Type the structure",
            "Top\n": "at least one item",
            "Top\n  A\nOther\n  B\n": "One top assembly only",
            "  A\n": "nothing above it",
            "Top\n  Top\n": "cannot hold itself",
            "Top\n  Box cots\n    Lid\n": "bought part",
            "LE-01-01001-001\n  A\n": "must be new",
            "Top\n  A x0\n": "at least 1",
        }
        for text, expected in texts.items():
            plan = ps.parse_structure(text)
            self.assertFalse(plan.ok, text)
            self.assertTrue(any(expected in i.text for i in plan.errors), (text, [i.text for i in plan.issues]))

    def test_deeper_indent_is_read_as_one_level(self):
        plan = ps.parse_structure("Top\n      Rail\n")
        self.assertTrue(plan.ok)
        self.assertEqual([i.level for i in plan.issues], ["warning"])
        self.assertEqual(plan.root.children[0][0].name, "Rail")


class TableTests(unittest.TestCase):
    def test_spreadsheet_paste(self):
        text = "level\tname\tqty\ttype\n1\tShop cart\t1\tassembly\n2\tFrame\t2\tassembly\n3\tSide rail\t2\tpart\n2\tCaster\t4\tcots\n"
        self.assertTrue(ps.looks_like_table(text))
        plan = ps.parse_structure(text)
        self.assertTrue(plan.ok, [i.text for i in plan.issues])
        self.assertEqual([(c.name, q, c.kind) for c, q in plan.root.children], [("Frame", 2, "assembly"), ("Caster", 4, "cots")])
        self.assertEqual(plan.root.children[0][0].children[0][0].name, "Side rail")
        self.assertFalse(ps.looks_like_table(CART))

    def test_csv_file(self):
        tmp = tempfile.mkdtemp(prefix="bw-ps-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "cart.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('Level,Name,Qty,Type\n0,"Shop cart",1,assembly\n1,"Frame, welded",2,assembly\n2,Side rail,2,part\n1,Caster,4,COTS\n')
        text = ps.read_structure_file(path)
        self.assertEqual(text.splitlines(), ["Shop cart", "  Frame, welded x2", "    Side rail x2", "  Caster x4 cots"])
        plan = ps.parse_structure(text)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.nodes["caster"].kind, "cots")
        txt = os.path.join(tmp, "cart.txt")
        with open(txt, "w", encoding="utf-8") as handle:
            handle.write(CART)
        self.assertEqual(ps.read_structure_file(txt), CART)


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-ps-vault-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setup = vs.VaultSetup(name="Cart", folder=os.path.join(self.tmp, "vault"))
        setup.numbering.programs = [vs.ProgramSetup("DM")]
        self.vault = vs.create_vault(setup, actor="bruce@mac")

    def test_number_plan_is_flat_and_writes_nothing(self):
        plan = ps.parse_structure(CART)
        ps.plan_numbers(plan, self.vault.root, "dm")
        self.assertEqual(plan.numbers, {
            "side rail": "DM-01-01001-001", "cross bar": "DM-01-01002-001", "frame": "DM-02-01001-001",
            "deck plate": "DM-01-01003-001", "caster 4 in": "DM-01-01004-001", "shop cart": "DM-02-01002-001",
        })
        self.assertEqual(plan.drawing_numbers, {
            "side rail": "DM-03-01001-001", "cross bar": "DM-03-01002-001", "frame": "DM-03-01003-001",
            "deck plate": "DM-03-01004-001", "shop cart": "DM-03-01005-001",
        })
        self.assertEqual(plan.document_types["drawing"], "03")
        again = ps.plan_numbers(ps.parse_structure(CART), self.vault.root, "DM")
        self.assertEqual(again.numbers["side rail"], "DM-01-01001-001")  # nothing was reserved
        rows = ps.preview_rows(plan)
        self.assertEqual((rows[0]["name"], rows[0]["pn"], rows[0]["drawing"]), ("Shop cart", "DM-02-01002-001", "DM-03-01005-001"))
        caster = next(r for r in rows if r["name"] == "Caster 4 in")
        self.assertEqual((caster["drawing"], caster["wants_drawing"], caster["qty"]), ("", False, 4))

    def test_check_against_the_vault(self):
        self.vault.create_item("DM-01-00900-001", "part", "Existing rail")
        self.vault.create_item("DM-03-00900-001", "drawing", "Its drawing", drawing_of="DM-01-00900-001")
        plan = ps.parse_structure("Top\n  DM-01-00900-001 x2\n  DM-01-00999-001\n  DM-03-00900-001\n")
        ps.check(plan, self.vault, "DM")
        texts = [i.text for i in plan.errors]
        self.assertEqual(len(texts), 2, texts)
        self.assertTrue(any("DM-01-00999-001 is not an item" in t for t in texts))
        self.assertTrue(any("is a drawing" in t for t in texts))
        self.assertEqual(plan.nodes["DM-01-00900-001"].kind, "part")
        ps.check(plan, self.vault, "DM")  # again: the earlier findings are replaced, not doubled
        self.assertEqual(len(plan.errors), 2)
        plan = ps.parse_structure(CART)
        ps.plan_numbers(plan, self.vault.root, "DM")
        ps.check(plan, self.vault, "DM", retired={"program": {"DM"}, "document_type": {"03"}})
        self.assertEqual(len(plan.errors), 2)

    def test_bought_parts_take_the_library_numbers(self):
        lib_setup = vs.VaultSetup(name="Standard Libraries", folder=os.path.join(self.tmp, "Standard Libraries"))
        lib_setup.numbering.programs = [vs.ProgramSetup("STD", "Standard parts")]
        library = vs.create_vault(lib_setup, actor="bruce@mac")
        # sit the project vault beside it, the way Vault/Projects does
        beside = os.path.join(self.tmp, "Projects")
        os.rename(self.vault.root, beside)
        from bruceworx.pdm_folder import FolderVault
        self.vault = FolderVault(beside)
        self.assertEqual(vs.library_beside(beside), library.root)
        places = vs.neighbour_vaults(beside)
        self.assertEqual(places[0][1], os.path.abspath(beside))
        self.assertEqual(places[1], ("Standard Libraries", os.path.abspath(library.root)))
        self.assertEqual(vs.neighbour_vaults(library.root)[1][1], os.path.abspath(beside))
        plan = ps.parse_structure(CART)
        ps.plan_numbers(plan, beside, "DM", library_root=library.root)
        self.assertEqual(plan.numbers["caster 4 in"], "STD-01-01001-001")
        self.assertEqual(plan.numbers["side rail"], "DM-01-01001-001")
        self.assertNotIn("caster 4 in", plan.drawing_numbers)
        library.create_item("STD-01-01001-001", "cots", "Caster")
        plan = ps.parse_structure("Top\n  STD-01-01001-001 x4\n")
        ps.check(plan, self.vault, "DM", library=library)
        self.assertTrue(plan.ok, [i.text for i in plan.issues])

    def test_codes_and_stems(self):
        self.assertEqual(ps.project_code("Shop cart (2026)"), "SHOP-CART-2026")
        self.assertEqual(ps.project_code("Shop cart", taken=["SHOP-CART"]), "SHOP-CART-2")
        self.assertEqual(ps.project_code(""), "PROJECT")
        self.assertEqual(ps.safe_stem("Side rail / left"), "Side_rail_left")
        self.assertEqual(ps.safe_stem("Side rail", taken={"Side_rail"}), "Side_rail_2")


if __name__ == "__main__":
    unittest.main()
