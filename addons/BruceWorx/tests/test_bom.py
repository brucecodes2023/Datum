# SPDX-License-Identifier: LGPL-2.1-or-later
"""BOM lines from the assembly walker, BOM and where-used CSV, the outbox CSV (BW-PDM-06)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import bom  # noqa: E402
from bruceworx import pdm  # noqa: E402
from bruceworx import pdm_workspace as pw  # noqa: E402
from bruceworx import release_check as rc  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402

TOP = "LE-02-02001-001"
BRK = "LE-02-02002-001"
PLATE = "LE-01-01001-001"
BOLT = "LE-09-09001-001"


class WalkTests(unittest.TestCase):
    def test_rows_become_lines_and_missing(self):
        found = [{"keys": [PLATE], "name": "plate", "qty": 1},
                 {"keys": ["bolt-old", BOLT], "name": "bolt.FCStd", "qty": 4},
                 {"keys": ["washer"], "name": "washer.FCStd", "qty": 2},
                 {"keys": [BOLT], "name": "bolt.FCStd", "qty": 2},
                 {"keys": [], "name": "washer.FCStd", "qty": 1}]
        lines, missing = bom.lines_from_walk(found, {PLATE, BOLT})
        self.assertEqual([(l.child, l.qty, l.find) for l in lines], [(PLATE, 1, 1), (BOLT, 6, 2)])
        self.assertEqual(missing, [{"name": "washer.FCStd", "qty": 3}])


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-bom-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        v = self.v = FolderVault.init(os.path.join(self.tmp, "vault"), "BOM", actor="me@mac")
        for pn, kind, text in ((TOP, "assembly", "Pedestal"), (BRK, "assembly", "Bracket"),
                               (PLATE, "part", "Plate"), (BOLT, "cots", "Bolt M5x12")):
            v.create_item(pn, kind, text)
        v.set_bom(BRK, [(PLATE, 1, 1), (BOLT, 4, 2)])
        v.set_bom(TOP, [(BRK, 2, 1), (BOLT, 2, 2)], missing=[{"name": "shim.FCStd", "qty": 1}])

    def test_indented_and_flattened_csv(self):
        text = bom.bom_csv(self.v, TOP)
        self.assertEqual(text.splitlines()[0], ",".join(bom.BOM_COLUMNS))
        self.assertIn(f"2,2,{BOLT},01,Bolt M5x12,4,8,WIP,", text)
        self.assertIn("1,,shim.FCStd,,,1,1,,not a vault item", text)
        flat = bom.bom_csv(self.v, TOP, flattened=True)
        self.assertIn(f"{BOLT},01,Bolt M5x12,10,WIP", flat)
        self.assertEqual(bom.default_csv_name(TOP, "A"), f"{TOP}-A-BOM.csv")

    def test_where_used_csv(self):
        rows = rc.where_used(pw.Workspace(self.v), BOLT)
        text = bom.where_used_csv(rows)
        self.assertIn(f"{TOP},01,WIP,,10,{TOP}-01 > {BOLT}-01,exact", text)
        self.assertIn(f"{BRK},01,WIP,,4,{BRK}-01 > {BOLT}-01,exact", text)

    def test_outbox_csv(self):
        self.v.set_bom(TOP, [(BRK, 2, 1), (BOLT, 2, 2)])  # the shim is in the vault now (left out)
        for pn in (PLATE, BOLT, BRK, TOP):
            self.v.submit(pn)
            self.v.approve(pn, note="first release")
        text = bom.outbox_csv(self.v.outbox())
        rows = text.splitlines()
        self.assertEqual(len(rows), 1 + 2 + 2)  # header, bracket's 2 lines, top's 2 lines
        self.assertIn(f"{TOP},A,,", rows[-1])
        self.assertTrue(rows[-1].endswith(f",2,{BOLT},A,Bolt M5x12,2"))

    def test_cycle_path_before_a_check_in(self):
        self.assertIsNone(self.v.cycle_path(TOP, [PLATE]))
        self.assertEqual(self.v.cycle_path(PLATE, [TOP]), [PLATE, TOP, BRK, PLATE])
        self.assertEqual(self.v.cycle_path(BRK, [TOP]), [BRK, TOP, BRK])
        self.assertIsNone(pdm.Vault().cycle_path("x", []))


if __name__ == "__main__":
    unittest.main()
