# SPDX-License-Identifier: LGPL-2.1-or-later
"""withdraw (2 Nov 2026): a never-released item goes Obsolete without a release (the VL-P01-165 nut)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad.pdm import OBSOLETE, LifecycleError  # noqa: E402
from heuretes_freecad.pdm_folder import FolderVault  # noqa: E402

ME = "bruce@mac"
NUT, NUT_DWG = "VL-01-01008-001", "VL-03-01018-001"
IMPELLER, SHAFT, ROTOR = "VL-01-01001-001", "VL-01-01002-001", "VL-02-01003-001"


class WithdrawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-withdraw-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "v"), "Test", actor=ME)
        for pn, kind, text in ((NUT, "part", "Impeller nut"), (IMPELLER, "part", "Impeller"),
                               (SHAFT, "part", "Shaft"), (ROTOR, "assembly", "Rotor assembly")):
            self.vault.create_item(pn, kind, text)
        self.vault.create_item(NUT_DWG, "drawing", "Impeller nut — detail drawing", NUT)

    def withdraw_both(self):
        self.vault.withdraw(NUT_DWG, "the nut is dropped in rev J")
        return self.vault.withdraw(NUT, "rev J: impeller screws onto the shaft", replaced_by=[IMPELLER, SHAFT])

    def test_the_nut_goes_obsolete_with_its_replacements_and_no_release(self):
        rev = self.withdraw_both()
        item = self.vault.items[NUT]
        self.assertEqual((item.state, rev.code, item.released), (OBSOLETE, "01", None))
        record = rev.record("withdraw")
        self.assertEqual(record["replaced_by"], [IMPELLER, SHAFT])
        self.assertIsNone(rev.record("approve"))
        self.assertIn("replaced by", rev.history[-1])
        self.assertEqual(self.vault.items[NUT_DWG].state, OBSOLETE)

    def test_it_replays(self):
        self.withdraw_both()
        again = FolderVault(self.vault.ref_path, actor=ME)
        self.assertEqual((again.items[NUT].state, again.items[NUT_DWG].state), (OBSOLETE, OBSOLETE))
        self.assertEqual(again.items[NUT].latest.record("withdraw")["replaced_by"], [IMPELLER, SHAFT])
        self.assertEqual(again.problems, [])

    def test_used_by_a_current_assembly_is_refused(self):
        self.vault.set_bom(ROTOR, [(NUT, 1), (IMPELLER, 1)])
        self.vault.withdraw(NUT_DWG, "dropped")
        with self.assertRaises(LifecycleError):
            self.vault.withdraw(NUT, "dropped")
        self.vault.set_bom(ROTOR, [(IMPELLER, 1), (SHAFT, 1)])  # the rev J rotor
        self.vault.withdraw(NUT, "dropped", replaced_by=[IMPELLER, SHAFT])

    def test_checked_out_or_bad_replacement_is_refused_and_writes_nothing(self):
        self.vault.withdraw(NUT_DWG, "dropped")
        events = len(self.vault.events)
        self.vault.checkout(NUT)
        with self.assertRaises(LifecycleError):
            self.vault.withdraw(NUT, "dropped")
        self.vault.undo_checkout(NUT)
        events = len(self.vault.events)
        for bad in (["VL-01-09999-001"], [NUT]):
            with self.assertRaises(LifecycleError):
                self.vault.withdraw(NUT, "dropped", replaced_by=bad)
        self.assertEqual(len(self.vault.events), events)


if __name__ == "__main__":
    unittest.main()
