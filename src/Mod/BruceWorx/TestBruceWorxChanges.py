# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for review, release and change control (bruceworx_changes, PDM-05)."""

import datetime as dt
import os
import shutil
import tempfile
import unittest

import bruceworx_changes as ch
import bruceworx_vaultclient as vc

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 23, 18, 0, tzinfo=UTC)
PART = "BW-01-01001-001"
DWG = "BW-03-01001-001"
BOLT = "BW-01-01002-001"


def _vault(tmp):
    from bruceworx.pdm_folder import FolderVault

    vault = FolderVault.init(os.path.join(tmp, "v"), "Changes", actor="bruce@mac")
    vault.create_item(PART, "part", "Bracket")
    vault.create_item(DWG, "drawing", "Bracket drawing", drawing_of=PART)
    vault.create_item(BOLT, "cots", "Bolt")
    return vault


class TestHelpers(unittest.TestCase):
    def test_change_numbers(self):
        for good in ("ECR-0001", "ECO-12", "ECR-00001"):
            self.assertTrue(ch.is_change_number(good), good)
        for bad in ("ECR-", "ECX-0001", PART, "", None, "eco-0001"):
            self.assertFalse(ch.is_change_number(bad), bad)

    def test_line_states(self):
        try:
            from bruceworx.release_check import Line
        except ImportError:
            self.skipTest("hooks add-on not installed")
        self.assertEqual(ch.line_state(Line("a", "A", ok=True)), "ok")
        self.assertEqual(ch.line_state(Line("a", "A")), "fail")
        self.assertEqual(ch.line_state(Line("a", "A", kind="manual")), "todo")
        self.assertEqual(ch.line_state(Line("a", "A", skipped=True)), "skip")
        self.assertEqual(ch.line_state(Line("a", "A", kind="warn")), "warn")  # PDM-09: shown, never blocks
        self.assertEqual(ch.line_state(Line("a", "A", kind="warn", ok=True)), "ok")
        self.assertEqual(set(ch.MARKS), {"ok", "fail", "skip", "todo", "warn"})

    def test_ecr_status_text(self):
        ecr = {"kind": "ECR", "number": "ECR-0001", "state": "Open", "items": [PART], "by": "bruce@mac",
               "at": "2026-10-23T16:00:00+00:00"}
        self.assertEqual(ch.change_status(ecr, "bruce@mac", NOW), ("To decide · 1 item · raised by you, 2 h ago", "warn"))
        self.assertEqual(ch.change_status(dict(ecr, state="Approved"), "bruce@mac", NOW)[0], "Ready for an ECO · 1 item")
        self.assertEqual(ch.change_status(dict(ecr, state="Approved", ecos=["ECO-0001"]))[0], "In ECO-0001 · 1 item")
        self.assertEqual(ch.change_status(dict(ecr, state="Rejected", reason="Not worth it\nmore"))[0],
                         "Rejected: Not worth it")

    def test_eco_status_text(self):
        eco = {"kind": "ECO", "number": "ECO-0001", "state": "Open", "items": [PART, DWG], "pending": [DWG]}
        self.assertEqual(ch.change_status(eco), (f"1 of 2 in review · work on {DWG}", "warn"))
        self.assertEqual(ch.change_status(dict(eco, pending=[])), ("All 2 in review · ready to release", "accent"))
        done = dict(eco, state="Released", released_at="2026-10-23T17:55:00+00:00")
        self.assertEqual(ch.change_status(done, "", NOW), ("Released 5 min ago · 2 items", "secondary"))

    def test_rows_only_under_the_changes_chip(self):
        row = ch.change_row({"kind": "ECR", "number": "ECR-0002", "state": "Open", "title": "Crack at the slot",
                             "items": [PART], "at": "2026-10-23T16:00:00+00:00"}, "bruce@mac", NOW)
        self.assertEqual((row["item_type"], row["chip"], row["pending"]), ("change", ("OPEN", "warn"), True))
        self.assertTrue(vc.matches(row, "changes"))
        self.assertTrue(vc.matches(row, "changes", "slot"))
        self.assertFalse(vc.matches(row, "all"))
        part = {"part_number": PART, "display": PART + "-A", "description": "Bracket", "item_type": "part"}
        self.assertFalse(vc.matches(part, "changes"))
        self.assertTrue(vc.matches(part, "all"))
        self.assertIn(("changes", "Changes"), vc.FILTERS)

    def test_refused_checkout_words(self):
        self.assertIn("raise an ECR", ch.refused_checkout_text(PART + "-A", "Released"))
        self.assertIn("ECO-0001 is already open", ch.refused_checkout_text(PART + "-A", "Released", ["ECO-0001"]))
        self.assertIn("in review", ch.refused_checkout_text(PART + "-01", "In Review"))
        self.assertIsNone(ch.refused_checkout_text(PART + "-01", "WIP"))

    def test_item_commands_join_the_menus_after_approve(self):
        saved = (vc.COMMANDS, vc.CONTEXT_COMMANDS)
        try:
            vc.add_item_commands(ch.ITEM_COMMANDS)
            vc.add_item_commands(ch.ITEM_COMMANDS)  # twice is once
            names = list(vc.CONTEXT_COMMANDS)
            at = names.index("BruceWorx_VaultApprove")
            self.assertEqual(names[at + 1:at + 1 + len(ch.ITEM_COMMANDS)], [e[0] for e in ch.ITEM_COMMANDS])
            self.assertIn("BruceWorx_VaultRetire", names)
            self.assertEqual(sum(1 for e in vc.COMMANDS if e[0] == "BruceWorx_VaultReject"), 1)
        finally:
            vc.COMMANDS, vc.CONTEXT_COMMANDS = saved


class TestWithVault(unittest.TestCase):
    def setUp(self):
        try:
            import bruceworx.pdm_folder  # noqa: F401
        except ImportError:
            self.skipTest("hooks add-on not installed")
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-changes-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = _vault(self.tmp)
        for pn in (PART, DWG):
            self.vault.submit(pn)
            self.vault.approve(pn, note="A")

    def test_eco_plan_lists_items_and_drawings_with_next_letters(self):
        self.assertEqual(ch.released_items(self.vault), [PART, DWG])
        self.vault.raise_ecr(None, "Crack", [PART])
        rows, problems = ch.eco_plan(self.vault, ["ECR-0001"])
        self.assertEqual([(r["part_number"], r["current"], r["next"]) for r in rows], [(PART, "A", "B"), (DWG, "A", "B")])
        self.assertEqual(problems, ["ECR-0001 is Open, not Approved"])
        self.assertEqual(ch.approvable_ecrs(self.vault), [])
        self.vault.decide_ecr("ECR-0001", True, reason="yes")
        self.assertEqual(ch.eco_plan(self.vault, ["ECR-0001"])[1], [])
        self.assertEqual(ch.approvable_ecrs(self.vault), ["ECR-0001"])

    def test_open_ecos_reach_my_work_and_the_panel(self):
        self.vault.raise_ecr(None, "Crack", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        self.assertEqual(ch.my_work_rows(self.vault), [])
        eco = self.vault.open_eco(None, ["ECR-0001"])
        self.assertEqual(ch.open_ecos(self.vault), [eco.number])
        self.assertEqual(ch.open_ecos(self.vault, DWG), [eco.number])
        self.assertEqual(ch.open_ecos(self.vault, BOLT), [])
        self.assertEqual(ch.approvable_ecrs(self.vault), [])
        rows = ch.my_work_rows(self.vault)
        self.assertEqual([(r["pn"], r["description"]) for r in rows], [("ECO-0001", "Crack")])
        self.assertTrue(rows[0]["status"].startswith("0 of 2 in review"))
        panel = ch.change_rows(self.vault, "bruce@mac", NOW)
        self.assertEqual(sorted(panel), ["ECO-0001", "ECR-0001"])
        self.assertTrue(panel["ECO-0001"]["pending"])
        self.assertFalse(panel["ECR-0001"]["pending"])  # approved and in an ECO: nothing to do

    def test_retire_refusals(self):
        self.assertIsNone(ch.retire_refusal(self.vault.items[PART]))
        self.assertIn("not released (01, WIP)", ch.retire_refusal(self.vault.items[BOLT]))
        self.vault.raise_ecr(None, "Crack", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        self.vault.open_eco(None, ["ECR-0001"])
        self.assertEqual(ch.retire_refusal(self.vault.items[PART]),
                         f"{PART} rev B is in work under ECO-0001. Release ECO-0001 first.")

    def test_retire_plan_shows_users_and_drawings(self):
        from bruceworx import pdm_workspace

        work = pdm_workspace.Workspace(self.vault)
        assy = "BW-02-01001-001"
        self.vault.create_item(assy, "assembly", "Frame")
        self.vault.set_bom(assy, [(PART, 2)])
        plan = ch.retire_plan(work, PART)
        self.assertEqual(([u["part_number"] for u in plan["confirm"]], plan["blockers"], plan["drawings"]),
                         ([assy], [], [DWG]))
        self.vault.submit(assy)
        self.vault.approve(assy, note="released")
        plan = ch.retire_plan(work, PART)
        self.assertEqual((plan["confirm"], len(plan["blockers"])), ([], 1))
        self.assertIn(f"{assy} rev A (released) still uses it (BOM)", plan["blockers"][0])
        self.vault.retire(DWG, "Redrawn")
        self.assertEqual(ch.retire_plan(work, PART)["drawings"], [])
        self.assertIn("already obsolete", ch.retire_plan(work, DWG)["blockers"][0])
        self.assertNotIn(DWG, ch.released_items(self.vault))

    def test_eco_plan_leaves_a_retired_drawing_out(self):
        self.vault.retire(DWG, "Redrawn")
        self.vault.raise_ecr(None, "Crack", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        rows, problems = ch.eco_plan(self.vault, ["ECR-0001"])
        self.assertEqual(([r["part_number"] for r in rows], problems), ([PART], []))


if __name__ == "__main__":
    unittest.main()
