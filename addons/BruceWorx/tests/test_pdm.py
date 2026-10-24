# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM reference model (BW-PDM-02). These tests are the contract any storage
option chosen in BW-PDM-01 has to pass."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm  # noqa: E402
from bruceworx.pdm import IN_REVIEW, OBSOLETE, RELEASED, WIP, LifecycleError, Vault  # noqa: E402

PART = "LE-01-01001-001"
RISER = "LE-01-01009-001"
DWG = "LE-02-01001-001"
ASSY = "LE-01-02001-001"
BOLT = "COTS-ISO4762-M5x20"


class RevisionCodeTests(unittest.TestCase):
    def test_letters_skip_confusable_ones(self):
        for letter in "IOQSXZ":
            self.assertNotIn(letter, pdm.RELEASE_LETTERS)
        self.assertEqual(len(pdm.RELEASE_LETTERS), 20)

    def test_release_sequence(self):
        self.assertEqual(pdm.first_release(), "A")
        self.assertEqual(pdm.next_release("A"), "B")
        self.assertEqual(pdm.next_release("H"), "J")  # skips I
        self.assertEqual(pdm.next_release("Y"), "AA")  # skips Z
        self.assertEqual(pdm.next_release("AY"), "BA")
        for index in range(1, 500):
            self.assertEqual(pdm.release_index(pdm.release_code(index)), index)

    def test_prerelease_sequence(self):
        self.assertEqual(pdm.first_prerelease(), "01")
        self.assertEqual(pdm.next_prerelease("09"), "10")
        self.assertEqual(pdm.next_prerelease("99"), "100")

    def test_invalid_codes(self):
        for bad in ("I", "Z", "a", "", "A1"):
            with self.assertRaises(ValueError):
                pdm.release_index(bad)

    def test_display_and_migration(self):
        self.assertEqual(pdm.display_part_number(PART, "02"), "LE-01-01001-001-02")
        self.assertEqual(pdm.display_part_number(PART, "A"), "LE-01-01001-001-A")
        self.assertEqual(pdm.strip_revision("LE-01-01001-001-A"), PART)
        self.assertEqual(pdm.strip_revision(PART), PART)
        with self.assertRaises(ValueError):
            pdm.display_part_number(PART, "O")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.vault = Vault()
        self.vault.create_item(PART, "part", "Base plate")

    def test_new_item_starts_at_01_wip(self):
        rev = self.vault.items[PART].latest
        self.assertEqual((rev.code, rev.state), ("01", WIP))
        self.assertTrue(rev.editable)
        self.assertEqual(self.vault.items[PART].state, pdm.INITIAL_STATE)

    def test_reject_moves_to_next_number(self):
        self.vault.submit(PART)
        nxt = self.vault.reject(PART, "hole too close to edge")
        self.assertEqual((nxt.code, nxt.state), ("02", WIP))
        old = self.vault.items[PART].revisions[0]
        self.assertTrue(old.superseded)
        self.assertIn("rejected: hole too close to edge", old.history)

    def test_first_approval_releases_A_read_only(self):
        self.vault.submit(PART)
        self.vault.reject(PART, "not ready")
        self.vault.submit(PART)
        rev = self.vault.approve(PART, note="released")
        self.assertEqual((rev.code, rev.state), ("A", RELEASED))
        self.assertFalse(rev.editable)
        self.assertIn("released from 02", rev.history)

    def test_cannot_skip_review(self):
        with self.assertRaises(LifecycleError):
            self.vault.approve(PART, note="released")

    def test_released_item_changes_only_through_eco(self):
        self._release(PART)
        with self.assertRaises(LifecycleError):
            self.vault.submit(PART)  # A is released, not WIP
        self.vault.raise_ecr("ECR-0001", "M5 clearance too tight", [PART])
        with self.assertRaises(LifecycleError):
            self.vault.open_eco("ECO-0001", ["ECR-0001"])  # ECR not approved yet
        self.vault.decide_ecr("ECR-0001", approved=True, reason="agreed")
        eco = self.vault.open_eco("ECO-0001", ["ECR-0001"])
        item = self.vault.items[PART]
        self.assertEqual((item.latest.code, item.latest.state, item.latest.eco), ("B", WIP, "ECO-0001"))
        self.assertEqual(item.released.code, "A")  # A stays current until B releases
        self.vault.submit(PART)
        with self.assertRaises(LifecycleError):
            self.vault.approve(PART, note="released")  # ECO revisions release with the ECO
        self.vault.release_eco(eco.number, note="released")
        self.assertEqual(item.released.code, "B")
        self.assertTrue(item.revisions[0].superseded)
        self.assertEqual(eco.status, "Released")

    def test_eco_rejection_keeps_the_letter(self):
        self._release(PART)
        self._eco([PART])
        self.vault.submit(PART)
        rev = self.vault.reject(PART, "wrong material")
        self.assertEqual((rev.code, rev.state), ("B", WIP))

    def test_retire_needs_no_released_users(self):
        self._release(PART)
        self.vault.create_item(ASSY, "assembly")
        self.vault.set_bom(ASSY, [(PART, 1)])
        self._release(ASSY)
        with self.assertRaises(LifecycleError):
            self.vault.retire(PART, "replaced")
        self.vault.retire(ASSY, "product discontinued")  # an obsolete user no longer blocks
        with self.assertRaisesRegex(LifecycleError, "frozen"):
            self.vault.set_bom(ASSY, [])  # a released (here obsolete) revision's BOM never changes
        rev = self.vault.retire(PART, "replaced by the welded plate")
        self.assertEqual(self.vault.items[PART].revisions[-1].state, OBSOLETE)
        self.assertEqual(self.vault.items[PART].state, OBSOLETE)
        self.assertIsNone(self.vault.items[PART].released)
        self.assertEqual(rev.record("retire")["reason"], "replaced by the welded plate")
        self.assertIn("retired: replaced by the welded plate", rev.history)

    def test_retire_only_released_items_once(self):
        with self.assertRaises(LifecycleError):
            self.vault.retire(PART, "never released")  # 01 in work
        self._release(PART)
        self.vault.retire(PART, "replaced")
        with self.assertRaisesRegex(LifecycleError, "already obsolete"):
            self.vault.retire(PART, "again")

    def test_retire_waits_for_an_open_eco(self):
        self._release(PART)
        eco = self._eco([PART])
        with self.assertRaisesRegex(LifecycleError, "release ECO-0001 first"):
            self.vault.retire(PART, "replaced")
        self.vault.submit(PART)
        with self.assertRaises(LifecycleError):
            self.vault.retire(PART, "replaced")  # B in review under the ECO
        self.vault.release_eco(eco.number, note="released")
        rev = self.vault.retire(PART, "replaced")
        self.assertEqual((rev.code, rev.state), ("B", OBSOLETE))

    def test_obsolete_items_take_no_changes(self):
        self._release(PART)
        self.vault.raise_ecr("ECR-0001", "change", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        self.vault.retire(PART, "replaced")
        with self.assertRaisesRegex(LifecycleError, "obsolete"):
            self.vault.raise_ecr("ECR-0002", "change", [PART])
        with self.assertRaisesRegex(LifecycleError, "obsolete"):
            self.vault.open_eco("ECO-0001", ["ECR-0001"])  # approved before it was retired
        self.assertEqual(self.vault.ecos, {})
        with self.assertRaises(LifecycleError):
            self.vault.submit(PART)

    # helpers
    def _release(self, pn):
        self.vault.submit(pn)
        self.vault.approve(pn, note="released")

    def _eco(self, items):
        n = len(self.vault.ecos) + 1
        self.vault.raise_ecr(f"ECR-{n:04d}", "change", items)
        self.vault.decide_ecr(f"ECR-{n:04d}", True, "agreed")
        return self.vault.open_eco(f"ECO-{n:04d}", [f"ECR-{n:04d}"])


class DrawingTests(unittest.TestCase):
    def test_drawing_must_name_its_part(self):
        vault = Vault()
        with self.assertRaises(LifecycleError):
            vault.create_item(DWG, "drawing")

    def test_eco_on_part_opens_and_releases_its_drawing(self):
        vault = Vault()
        vault.create_item(PART)
        vault.create_item(DWG, "drawing", drawing_of=PART)
        for pn in (PART, DWG):
            vault.submit(pn)
            vault.approve(pn, note="released")
        vault.raise_ecr("ECR-0001", "thicker plate", [PART])
        vault.decide_ecr("ECR-0001", True, "agreed")
        eco = vault.open_eco("ECO-0001", ["ECR-0001"])
        self.assertEqual(eco.items, [PART, DWG])
        self.assertEqual(vault.items[DWG].latest.code, "B")
        vault.submit(PART)
        with self.assertRaises(LifecycleError):
            vault.release_eco("ECO-0001", note="released")  # drawing not in review yet
        vault.submit(DWG)
        vault.release_eco("ECO-0001", note="released")
        self.assertEqual(vault.items[PART].released.code, "B")
        self.assertEqual(vault.items[DWG].released.code, "B")

    def test_eco_leaves_a_retired_drawing_alone(self):
        vault = Vault()
        vault.create_item(PART)
        vault.create_item(DWG, "drawing", drawing_of=PART)
        for pn in (PART, DWG):
            vault.submit(pn)
            vault.approve(pn, note="released")
        vault.retire(DWG, "redrawn as a new item")
        vault.raise_ecr("ECR-0001", "thicker plate", [PART])
        vault.decide_ecr("ECR-0001", True, "agreed")
        self.assertEqual(vault.open_eco("ECO-0001", ["ECR-0001"]).items, [PART])
        self.assertEqual(vault.items[DWG].state, OBSOLETE)


class BomTests(unittest.TestCase):
    def setUp(self):
        v = self.vault = Vault()
        for pn, kind in ((ASSY, "assembly"), ("SUB", "assembly"), (PART, "part"), (RISER, "part"), (BOLT, "cots")):
            v.create_item(pn, kind)
        v.set_bom("SUB", [(RISER, 1), (BOLT, 4)])
        v.set_bom(ASSY, [(PART, 1), ("SUB", 2), (BOLT, 2)])

    def test_explode_multiplies_along_paths(self):
        totals = self.vault.explode(ASSY)
        self.assertEqual(totals, {PART: 1, "SUB": 2, RISER: 2, BOLT: 2 + 2 * 4})

    def test_flattened_lists_leaves_only(self):
        self.assertEqual(self.vault.flattened(ASSY), {PART: 1, RISER: 2, BOLT: 10})

    def test_where_used(self):
        self.assertEqual(self.vault.where_used(BOLT), {"SUB", ASSY})
        self.assertEqual(self.vault.where_used(ASSY), set())

    def test_cycles_rejected_and_bom_restored(self):
        with self.assertRaises(LifecycleError):
            self.vault.set_bom("SUB", [(ASSY, 1)])
        self.assertEqual(self.vault.bom["SUB"], [(RISER, 1), (BOLT, 4)])


if __name__ == "__main__":
    unittest.main()
