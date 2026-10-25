# SPDX-License-Identifier: LGPL-2.1-or-later
"""Folder vault (BW-PDM-01). The PDM-02 contract tests run unchanged against a
folder, then the folder-specific rules: ref file, write-once objects,
append-only journal, replay after reopening, and sync-service duplicates."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_pdm  # noqa: E402
from bruceworx.pdm import IN_REVIEW, RELEASED, WIP, LifecycleError  # noqa: E402
from bruceworx.pdm_folder import FolderVault, VaultFormatError, find_ref_file  # noqa: E402

PART = test_pdm.PART
ASSY = test_pdm.ASSY


class _TempDir:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-vault-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        super().setUp()


def _folder_vault_factory(case):
    counter = [0]

    def make():
        counter[0] += 1
        return FolderVault.init(os.path.join(case.tmp, f"v{counter[0]}"), "Contract", actor="tester@mac")

    return make


def _contract(cls):
    """Re-run a test_pdm case with every Vault() replaced by a folder vault."""

    class Folder(_TempDir, cls):
        def setUp(self):
            self.tmp = tempfile.mkdtemp(prefix="bw-vault-")
            self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
            original = test_pdm.Vault
            test_pdm.Vault = _folder_vault_factory(self)
            self.addCleanup(setattr, test_pdm, "Vault", original)
            cls.setUp(self)

    Folder.__name__ = f"Folder{cls.__name__}"
    Folder.__qualname__ = Folder.__name__
    return Folder


FolderLifecycleTests = _contract(test_pdm.LifecycleTests)
FolderDrawingTests = _contract(test_pdm.DrawingTests)
FolderBomTests = _contract(test_pdm.BomTests)


class ReplayTests(_TempDir, unittest.TestCase):
    """Reopening the folder rebuilds exactly the same state."""

    def test_reopen_rebuilds_state(self):
        vault = FolderVault.init(self.tmp, "Pedestal", actor="bruce@mac")
        vault.create_item(PART, "part", "Base plate")
        vault.create_item(ASSY, "assembly")
        vault.set_bom(ASSY, [(PART, 4)])
        vault.submit(PART)
        vault.reject(PART, "edge distance")
        vault.submit(PART)
        vault.approve(PART, note="released")
        again = FolderVault(self.tmp, actor="bruce@mac")
        self.assertEqual(again.status()["items"], vault.status()["items"])
        self.assertEqual(again.bom[ASSY], [(PART, 4)])
        self.assertEqual(again.items[PART].released.code, "A")
        self.assertEqual(again.problems, [])

    def test_item_type_change_replays(self):
        vault = FolderVault.init(self.tmp, "Pedestal", actor="bruce@mac")
        vault.create_item(PART, "part", "Ball bearing 683ZZ (COTS)")
        vault.set_item_type(PART, "cots")
        again = FolderVault(self.tmp, actor="bruce@mac")
        self.assertEqual(again.items[PART].item_type, "cots")
        self.assertEqual(again.problems, [])
        self.assertEqual(json.loads(open(vault.journal_path).read().splitlines()[-1])["args"],
                         {"item_type": "cots", "part_number": PART})

    def test_configurations_replay(self):
        """MOD-03: configuration items come back from the journal sharing their file's state; they
        have no file of their own (check-out goes to the file), and versions resolve to the file."""
        vault = FolderVault.init(self.tmp, "Pedestal", actor="bruce@mac")
        vault.create_item(PART, "part", "Bracket")
        vault.add_configuration(PART + "-S", PART, "Short", "Bracket 50")
        with self.assertRaises(LifecycleError):
            vault.checkout(PART + "-S")
        vault.checkout(PART)
        path = os.path.join(self.tmp, "b.FCStd")
        with open(path, "wb") as handle:
            handle.write(b"bracket")
        vault.checkin(PART, path, "first")
        vault.submit(PART)
        vault.approve(PART, note="released")
        again = FolderVault(self.tmp, actor="bruce@mac")
        config = again.items[PART + "-S"]
        self.assertEqual((config.configuration_of, config.configuration, config.state, config.released.code),
                         (PART, "Short", RELEASED, "A"))
        self.assertEqual(again.latest_version(PART + "-S").sha256, again.latest_version(PART).sha256)
        self.assertEqual(again.file_item(PART + "-S"), PART)
        self.assertEqual(again.problems, [])

    def test_refused_operation_writes_nothing(self):
        vault = FolderVault.init(self.tmp, "Pedestal", actor="bruce@mac")
        vault.create_item(PART)
        size = os.path.getsize(vault.journal_path)
        with self.assertRaises(LifecycleError):
            vault.approve(PART, note="released")  # not in review
        self.assertEqual(os.path.getsize(vault.journal_path), size)
        self.assertEqual(FolderVault(self.tmp, actor="bruce@mac").items[PART].latest.state, WIP)


class RefFileTests(_TempDir, unittest.TestCase):
    def test_open_by_ref_file_or_folder(self):
        vault = FolderVault.init(self.tmp, "Lift Eng", actor="bruce@mac")
        self.assertTrue(vault.ref_path.endswith("Lift-Eng.bwvault"))
        self.assertEqual(find_ref_file(self.tmp), vault.ref_path)
        self.assertEqual(FolderVault(vault.ref_path).ref["vault_id"], vault.ref["vault_id"])

    def test_refuses_foreign_or_newer_files(self):
        with open(os.path.join(self.tmp, "x.bwvault"), "w", encoding="utf-8") as handle:
            json.dump({"format": "something-else"}, handle)
        with self.assertRaises(VaultFormatError):
            FolderVault(self.tmp)
        other = os.path.join(self.tmp, "newer")
        os.makedirs(other)
        with open(os.path.join(other, "n.bwvault"), "w", encoding="utf-8") as handle:
            json.dump({"format": "bruceworx.vault", "version": 99}, handle)
        with self.assertRaises(VaultFormatError):
            FolderVault(other)

    def test_init_twice_refused(self):
        FolderVault.init(self.tmp, "A")
        with self.assertRaises(VaultFormatError):
            FolderVault.init(self.tmp, "B")


class FileTests(_TempDir, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Pedestal", actor="bruce@mac")
        self.vault.create_item(PART, "part", "Base plate")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)

    def _save(self, text):
        path = os.path.join(self.work, "base-plate.FCStd")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_checkin_needs_checkout(self):
        with self.assertRaises(LifecycleError):
            self.vault.checkin(PART, self._save("v1"), "v1")

    def test_versions_are_write_once_objects(self):
        self.vault.checkout(PART)
        v1 = self.vault.checkin(PART, self._save("v1"), "first")
        self.vault.checkout(PART)
        v2 = self.vault.checkin(PART, self._save("v2"), "second")
        self.assertNotEqual(v1.sha256, v2.sha256)
        objects = [os.path.join(self.vault.root, v.object_name) for v in (v1, v2)]
        self.assertTrue(all(os.path.isfile(p) for p in objects))
        stamps = [os.stat(p).st_mtime_ns for p in objects]
        self.vault.checkout(PART)
        self.vault.checkin(PART, self._save("v3"), "v3")
        self.assertEqual([os.stat(p).st_mtime_ns for p in objects], stamps)  # never rewritten
        self.assertEqual(len(self.vault.versions[PART]), 3)
        self.assertIsNone(self.vault.locks.get(PART))
        leftovers = [n for _d, _s, files in os.walk(self.vault.root) for n in files if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_unchanged_checkin_only_unlocks(self):
        self.vault.checkout(PART)
        v1 = self.vault.checkin(PART, self._save("v1"), "v1")
        self.vault.checkout(PART)
        same = self.vault.checkin(PART, self._save("v1"), "v1")
        self.assertEqual(same.sha256, v1.sha256)
        self.assertEqual(len(self.vault.versions[PART]), 1)
        self.assertIsNone(self.vault.locks.get(PART))

    def test_checkout_copies_latest_to_workspace(self):
        self.vault.checkout(PART)
        self.vault.checkin(PART, self._save("v1"), "v1")
        other = os.path.join(self.tmp, "elsewhere")
        path = self.vault.checkout(PART, workspace=other)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "v1")

    def test_released_revision_freezes_its_file(self):
        self.vault.checkout(PART)
        v1 = self.vault.checkin(PART, self._save("v1"), "v1")
        self.vault.submit(PART)
        with self.assertRaises(LifecycleError):
            self.vault.checkout(PART)  # in review: read-only
        self.vault.approve(PART, note="released")
        self.assertEqual(self.vault.released_version(PART).sha256, v1.sha256)
        reopened = FolderVault(self.vault.root, actor="bruce@mac")
        self.assertEqual(reopened.released_version(PART).sha256, v1.sha256)
        self.assertEqual(reopened.items[PART].latest.state, RELEASED)

    def test_damaged_object_is_detected(self):
        self.vault.checkout(PART)
        v1 = self.vault.checkin(PART, self._save("v1"), "v1")
        with open(os.path.join(self.vault.root, v1.object_name), "w", encoding="utf-8") as handle:
            handle.write("tampered")
        with self.assertRaises(LifecycleError):
            self.vault.fetch(PART, self.work)


class SyncServiceTests(_TempDir, unittest.TestCase):
    """What iCloud, Dropbox and friends can do to the folder."""

    def setUp(self):
        super().setUp()
        self.vault = FolderVault.init(self.tmp, "Pedestal", actor="bruce@mac")
        self.vault.create_item(PART)
        self.vault.submit(PART)

    def test_duplicate_journal_copies_neither_lose_nor_double(self):
        journal = self.vault.journal_path
        folder = os.path.dirname(journal)
        # iCloud keeps the older server copy as "name 2", Dropbox and
        # Syncthing use their own names. The newer copy holds an extra event.
        shutil.copyfile(journal, os.path.join(folder, "bruce@mac 2.jsonl"))
        shutil.copyfile(journal, os.path.join(folder, "bruce@mac (conflicted copy 2026-10-19).jsonl"))
        self.vault.approve(PART, note="released")
        shutil.copyfile(journal, os.path.join(folder, "bruce@mac.sync-conflict-20261019-101500-ABCDEF.jsonl"))
        with open(journal, "w", encoding="utf-8"):
            pass  # worst case: the main file was replaced by an empty one
        again = FolderVault(self.tmp, actor="bruce@mac")
        self.assertEqual(again.items[PART].released.code, "A")
        self.assertEqual(again.problems, [])
        # Writing continues without reusing an event number.
        again.raise_ecr("ECR-0001", "thicker", [PART])
        self.assertEqual(len(FolderVault(self.tmp).ecrs), 1)

    def test_half_synced_line_is_ignored(self):
        with open(self.vault.journal_path, "a", encoding="utf-8") as handle:
            handle.write('{"id": "partial", "actor": "bruce@mac", "se')
        again = FolderVault(self.tmp, actor="bruce@mac")
        self.assertEqual(again.items[PART].latest.state, IN_REVIEW)
        self.assertEqual(again.problems, [])

    def test_journal_before_object_is_reported(self):
        work = os.path.join(self.tmp, "..", os.path.basename(self.tmp) + "-work")
        os.makedirs(work, exist_ok=True)
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        vault = FolderVault.init(os.path.join(self.tmp, "v2"), "Second", actor="bruce@mac")
        vault.create_item(PART)
        vault.checkout(PART)
        path = os.path.join(work, "p.FCStd")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("v1")
        version = vault.checkin(PART, path, "first")
        os.remove(os.path.join(vault.root, version.object_name))  # not downloaded yet
        again = FolderVault(vault.root, actor="bruce@mac")
        self.assertEqual(again.missing_objects(), [version.object_name])
        with self.assertRaises(FileNotFoundError):
            again.fetch(PART, work)

    def test_ref_file_duplicate_is_harmless(self):
        shutil.copyfile(self.vault.ref_path, self.vault.ref_path.replace(".bwvault", " 2.bwvault"))
        self.assertEqual(FolderVault(self.tmp).ref["vault_id"], self.vault.ref["vault_id"])


class ListenerTests(_TempDir, unittest.TestCase):
    """Listeners hear state changes; FreeCAD writes the part's LifecycleState from them."""

    def setUp(self):
        super().setUp()
        self.vault = FolderVault.init(os.path.join(self.tmp, "v"), "States", actor="bruce@mac")
        self.heard = []
        self.vault.add_listener(self.heard.append)

    def test_each_transition_is_heard_once(self):
        self.vault.create_item(PART)
        self.vault.submit(PART)
        self.vault.reject(PART, "too thin")
        self.vault.submit(PART)
        self.vault.approve(PART, note="released")
        self.assertEqual(self.heard, [{PART: WIP}, {PART: IN_REVIEW}, {PART: WIP}, {PART: IN_REVIEW},
                                      {PART: RELEASED}])
        self.assertEqual(self.vault.states(), {PART: RELEASED})
        self.assertEqual(self.vault.state_of(PART), RELEASED)
        self.assertIsNone(self.vault.state_of("NOPE"))

    def test_file_operations_change_no_state(self):
        self.vault.create_item(PART)
        del self.heard[:]
        self.vault.checkout(PART)
        model = os.path.join(self.tmp, "plate.FCStd")
        with open(model, "w", encoding="utf-8") as handle:
            handle.write("v1")
        self.vault.checkin(PART, model, "model")
        self.assertEqual(self.heard, [])

    def test_refresh_reports_other_devices_changes(self):
        self.vault.create_item(PART)
        other = FolderVault(self.vault.root, actor="bruce@laptop")
        other.submit(PART)
        del self.heard[:]
        self.vault.refresh()
        self.assertEqual(self.heard, [{PART: IN_REVIEW}])

    def test_failing_listener_does_not_undo_the_event(self):
        def broken(_changes):
            raise RuntimeError("document closed")

        self.vault.add_listener(broken)
        self.vault.create_item(PART)
        self.assertEqual(FolderVault(self.vault.root).state_of(PART), WIP)
        self.assertIn("state listener failed: document closed", self.vault.problems)
        self.vault.remove_listener(broken)
        self.vault.submit(PART)
        self.assertEqual(len(self.vault.problems), 1)

    def test_ops_open_and_release_every_item_they_touch(self):
        self.vault.create_item(PART)
        self.vault.submit(PART)
        self.vault.approve(PART, note="released")
        self.vault.raise_ecr("ECR-1", "thicker", [PART])
        self.vault.decide_ecr("ECR-1", True, "agreed")
        del self.heard[:]
        self.vault.open_eco("ECO-1", ["ECR-1"])
        self.assertEqual(self.heard, [{PART: WIP}])


class ChangeControlTests(_TempDir, unittest.TestCase):
    """PDM-05: checklists, notes and reasons, attachments and change numbers in the journal."""

    DWG = "LE-03-01001-001"

    def setUp(self):
        super().setUp()
        self.vault = FolderVault.init(os.path.join(self.tmp, "v"), "Changes", actor="bruce@mac")
        self.vault.create_item(PART, "part", "Base plate")
        self.vault.create_item(self.DWG, "drawing", drawing_of=PART)

    def reopen(self):
        return FolderVault(self.vault.root, actor="bruce@mac")

    def _journal(self):
        with open(self.vault.journal_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_old_journal_lines_replay_unchanged(self):
        # Lines exactly as written before PDM-05: no checklist, note or attachments.
        lines = [
            ("submit", {"part_number": PART}),
            ("reject", {"part_number": PART, "reason": "edge"}),
            ("submit", {"part_number": PART}),
            ("approve", {"part_number": PART}),
            ("raise_ecr", {"number": "ECR-7", "problem": "thicker", "items": [PART]}),
            ("decide_ecr", {"number": "ECR-7", "approved": True}),
        ]
        with open(self.vault.journal_path, "a", encoding="utf-8") as handle:
            for seq, (op, args) in enumerate(lines, start=10):
                handle.write(json.dumps({"id": f"old-{seq}", "actor": "bruce@mac", "seq": seq,
                                         "at": f"2099-10-20T10:00:{seq:02d}+00:00", "op": op, "args": args}) + "\n")
        again = self.reopen()
        self.assertEqual(again.problems, [])
        item = again.items[PART]
        self.assertEqual((item.released.code, again.ecrs["ECR-7"].status), ("A", "Approved"))
        self.assertEqual(item.released.record("approve"), {"op": "approve", "by": "bruce@mac",
                                                          "at": "2099-10-20T10:00:13+00:00"})
        self.assertEqual(again.ecrs["ECR-7"].reason, "")
        self.assertEqual(again.next_ecr_number(), "ECR-0008")  # after the highest, in the 4-digit form

    def test_unset_optional_fields_stay_out_of_the_journal(self):
        self.vault.submit(PART)
        self.vault.reject(PART, "not ready")
        self.vault.submit(PART)
        self.vault.approve(PART, note="released")
        args = [e["args"] for e in self._journal()[-3:]]
        self.assertEqual(args[0], {"part_number": PART, "reason": "not ready"})  # no checklist, attachments
        self.assertEqual(args[2], {"part_number": PART, "note": "released"})

    def test_approval_records_checklist_and_note(self):
        checklist = {"checked_in": {"ok": True}, "drawing_reviewed": {"ok": True, "by_hand": True}}
        self.vault.submit(PART)
        self.vault.reject(PART, "hole too close to the edge", checklist={"checked_in": {"ok": True}})
        self.vault.submit(PART)
        self.vault.approve(PART, note="First release", checklist=checklist)
        for vault in (self.vault, self.reopen()):
            item = vault.items[PART]
            approval = item.released.record("approve")
            self.assertEqual((approval["note"], approval["checklist"], approval["by"]),
                             ("First release", checklist, "bruce@mac"))
            self.assertEqual(item.revisions[0].record("reject")["reason"], "hole too close to the edge")
        self.assertEqual(self.reopen().problems, [])

    def test_change_numbers_count_per_vault(self):
        self.assertEqual((self.vault.next_ecr_number(), self.vault.next_eco_number()), ("ECR-0001", "ECO-0001"))
        for pn in (PART, self.DWG):
            self.vault.submit(pn)
            self.vault.approve(pn, note="A")
        first = self.vault.raise_ecr(None, "M5 clearance", [PART])
        second = self.vault.raise_ecr(None, "Chamfer", [PART])
        self.assertEqual((first.number, second.number), ("ECR-0001", "ECR-0002"))
        self.assertEqual(self.reopen().next_ecr_number(), "ECR-0003")
        with self.assertRaises(LifecycleError):
            self.vault.raise_ecr("ECR-0001", "again", [PART])
        with self.assertRaises(LifecycleError):
            self.vault.raise_ecr(None, "nothing affected", [])
        with self.assertRaises(LifecycleError):
            self.vault.raise_ecr(None, "  ", [PART])
        self.vault.decide_ecr("ECR-0001", True, reason="Worth it")
        self.vault.decide_ecr("ECR-0002", False, reason="Not needed")
        eco = self.vault.open_eco(None, ["ECR-0001"])
        self.assertEqual(eco.number, "ECO-0001")
        with self.assertRaises(LifecycleError):
            self.vault.open_eco(None, [])
        again = self.reopen()
        self.assertEqual((again.ecrs["ECR-0001"].reason, again.ecrs["ECR-0002"].status), ("Worth it", "Rejected"))
        self.assertEqual(again.next_eco_number(), "ECO-0002")

    def test_attachments_are_write_once_objects(self):
        self.vault.submit(PART)
        self.vault.approve(PART, note="released")
        shot = os.path.join(self.tmp, "crack.png")
        with open(shot, "wb") as handle:
            handle.write(b"\x89PNG fake")
        ecr = self.vault.raise_ecr(None, "Crack at the slot", [PART], attachments=[shot])
        record = ecr.attachments[0]
        self.assertEqual((record["filename"], record["size"]), ("crack.png", 9))
        stored = self.vault.attachment_path(record)
        self.assertTrue(os.path.isfile(stored))
        stamp = os.stat(stored).st_mtime_ns
        os.remove(shot)  # the vault copy does not depend on the original
        out = self.vault.fetch_attachment(record, os.path.join(self.tmp, "out"))
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(), b"\x89PNG fake")
        again = self.reopen()
        self.assertEqual(again.ecrs[ecr.number].attachments, [record])
        self.assertEqual(again.missing_objects(), [])
        self.assertEqual(os.stat(stored).st_mtime_ns, stamp)
        os.remove(stored)
        self.assertEqual(self.reopen().missing_objects(), [os.path.relpath(stored, self.vault.root)])

    def test_eco_release_records_a_checklist_per_item(self):
        for pn in (PART, self.DWG):
            self.vault.submit(pn)
            self.vault.approve(pn, note="A")
        self.vault.raise_ecr(None, "Thicker", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        eco = self.vault.open_eco(None, ["ECR-0001"])
        self.assertEqual(eco.items, [PART, self.DWG])
        for pn in eco.items:
            self.vault.submit(pn)
        checks = {PART: {"checked_in": {"ok": True}}, self.DWG: {"drawing": {"ok": True}}}
        self.vault.release_eco(eco.number, note="Plate 8 mm", checklists=checks)
        again = self.reopen()
        for pn in (PART, self.DWG):
            item = again.items[pn]
            self.assertEqual((item.released.code, item.revisions[0].superseded), ("B", True))
            record = item.released.record("release_eco")
            self.assertEqual((record["eco"], record["note"], record["checklist"]),
                             ("ECO-0001", "Plate 8 mm", checks[pn]))
        self.assertEqual(again.ecos["ECO-0001"].note, "Plate 8 mm")
        rows = {row["number"]: row for row in again.changes()}
        self.assertEqual((rows["ECR-0001"]["state"], rows["ECR-0001"]["ecos"]), ("Approved", ["ECO-0001"]))
        self.assertEqual((rows["ECO-0001"]["state"], rows["ECO-0001"]["title"]), ("Released", "Thicker"))
        self.assertTrue(rows["ECO-0001"]["released_at"])

    def test_changes_list_open_work(self):
        for pn in (PART, self.DWG):
            self.vault.submit(pn)
            self.vault.approve(pn, note="released")
        self.vault.raise_ecr(None, "Thicker", [PART])
        self.vault.decide_ecr("ECR-0001", True, "agreed")
        self.vault.open_eco(None, ["ECR-0001"])
        rows = self.vault.changes()
        self.assertEqual([r["number"] for r in rows], ["ECO-0001", "ECR-0001"])  # newest first
        self.assertEqual((rows[0]["state"], rows[0]["pending"]), ("Open", [PART, self.DWG]))
        self.assertEqual(rows[1]["by"], "bruce@mac")

    def test_retire_records_the_reason_and_replays(self):
        for pn in (PART, self.DWG):
            self.vault.submit(pn)
            self.vault.approve(pn, note="released")
        with self.assertRaisesRegex(LifecycleError, "reason for retiring it is required"):
            self.vault.retire(PART, "  ")
        self.vault.retire(PART, "Replaced by the welded plate")
        self.assertEqual(self._journal()[-1]["args"], {"part_number": PART, "reason": "Replaced by the welded plate"})
        again = self.reopen()
        self.assertEqual(again.problems, [])
        rev = again.items[PART].revisions[-1]
        self.assertEqual((again.items[PART].state, rev.record("retire")["reason"], rev.record("retire")["by"]),
                         ("Obsolete", "Replaced by the welded plate", "bruce@mac"))
        self.assertEqual(again.status()["items"][0]["state"], "Obsolete")

    def test_old_retire_lines_replay(self):
        # A retire written before PDM-05 had no reason.
        lines = [("submit", {"part_number": PART}), ("approve", {"part_number": PART}),
                 ("retire", {"part_number": PART})]
        with open(self.vault.journal_path, "a", encoding="utf-8") as handle:
            for seq, (op, args) in enumerate(lines, start=10):
                handle.write(json.dumps({"id": f"old-{seq}", "actor": "bruce@mac", "seq": seq,
                                         "at": f"2099-10-20T10:00:{seq:02d}+00:00", "op": op, "args": args}) + "\n")
        again = self.reopen()
        self.assertEqual((again.problems, again.items[PART].state), ([], "Obsolete"))

    def test_submit_refused_while_checked_out(self):
        self.vault.checkout(PART)
        with self.assertRaisesRegex(LifecycleError, "check it in first"):
            self.vault.submit(PART)
        self.vault.undo_checkout(PART)
        self.vault.submit(PART)
        self.assertEqual(self.vault.items[PART].state, IN_REVIEW)

    def test_newer_arguments_are_reported_not_fatal(self):
        with open(self.vault.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "future", "actor": "bruce@mac", "seq": 99, "at": "2026-12-01T00:00:00+00:00",
                                     "op": "submit", "args": {"part_number": PART, "from_the_future": 1}}) + "\n")
        again = self.reopen()
        self.assertEqual(len(again.problems), 1)
        self.assertEqual(again.items[PART].latest.state, WIP)


class CliTests(_TempDir, unittest.TestCase):
    def _run(self, *argv):
        import contextlib
        import io

        from bruceworx.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["vault", *argv, "--vault", self.vault_dir, "--actor", "bruce@mac"])
        lines = [ln for ln in out.getvalue().splitlines() if ln.startswith("BW_RESULT ")]
        return code, (json.loads(lines[-1][len("BW_RESULT "):]) if lines else None)

    def test_drive_the_vault_from_the_command_line(self):
        self.vault_dir = os.path.join(self.tmp, "Pedestal")
        code, result = self._run("init")
        self.assertEqual(code, 0)
        self.assertTrue(result["ref"].endswith("Pedestal.bwvault"))
        self._run("create", "--pn", PART, "--description", "Base plate")
        self._run("checkout", "--pn", PART)
        model = os.path.join(self.tmp, "plate.FCStd")
        with open(model, "w", encoding="utf-8") as handle:
            handle.write("v1")
        code, result = self._run("checkin", "--pn", PART, "--file", model, "--comment", "first")
        self.assertEqual((code, len(result["sha256"])), (0, 64))
        self._run("submit", "--pn", PART)
        code, result = self._run("approve", "--pn", PART, "--note", "First release", "--reviewed")
        self.assertEqual(code, 1)  # a part needs its drawing (the release checklist)
        self.assertIn("Drawing checked in, title block shows this P/N: no drawing of this P/N in the vault",
                      result["blockers"])
        self._add_drawing()
        code, result = self._run("approve", "--pn", PART, "--note", "First release", "--reviewed")
        self.assertEqual((code, result["revision"], result["state"]), (0, "A", RELEASED))
        code, status = self._run("status")
        row = status["items"][0]
        self.assertEqual((row["display"], row["versions"], row["file"]), (PART + "-A", 1, "plate.FCStd"))

    # PDM-05: approve, reject and retire take the note and the checklist, as the panels do.
    DWG = "LE-03-01001-001"

    def _add_drawing(self, pn=None, title=None):
        import test_release_check

        pn = pn or self.DWG
        self._run("create", "--pn", pn, "--type", "drawing", "--drawing-of", PART)
        self._run("checkout", "--pn", pn)
        path = test_release_check.make_fcstd(os.path.join(self.tmp, pn + ".FCStd"),
                                             title=title or {"part_number": PART})
        self._run("checkin", "--pn", pn, "--file", path, "--comment", "drawing")

    def _in_review(self):
        self.vault_dir = os.path.join(self.tmp, "Pedestal")
        self._run("init")
        self._run("create", "--pn", PART, "--description", "Base plate")
        self._run("checkout", "--pn", PART)
        model = os.path.join(self.tmp, "plate.FCStd")
        with open(model, "w", encoding="utf-8") as handle:
            handle.write("v1")
        self._run("checkin", "--pn", PART, "--file", model, "--comment", "first")
        self._add_drawing()
        self._run("submit", "--pn", PART)

    def _journal(self):
        vault = FolderVault(self.vault_dir, actor="bruce@mac")
        with open(vault.journal_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_approve_runs_the_release_checklist(self):
        self._in_review()
        code, result = self._run("approve", "--pn", PART)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertEqual(result["blockers"], ["Tick “Drawing reviewed against the model”", "Write a release note"])
        code, result = self._run("approve", "--pn", PART, "--note", "First release", "--reviewed", "--dry-run")
        self.assertEqual((code, result["ok"], result["blockers"], result["state"] if "state" in result else None),
                         (0, True, [], None))
        self.assertEqual(FolderVault(self.vault_dir).items[PART].state, IN_REVIEW)  # dry run: nothing changed
        report = os.path.join(self.tmp, "report.pdf")
        with open(report, "wb") as handle:
            handle.write(b"%PDF")
        code, result = self._run("approve", "--pn", PART, "--note", " First release ", "--reviewed",
                                 "--attach", report)
        self.assertEqual((code, result["revision"]), (0, "A"))
        args = self._journal()[-1]["args"]
        self.assertEqual((args["note"], args["attachments"][0]["filename"]), ("First release", "report.pdf"))
        checklist = args["checklist"]
        self.assertEqual(sorted(checklist), ["checked_in", "components", "drawing", "drawing_reviewed", "version"])
        self.assertEqual(checklist["drawing_reviewed"], {"ok": True, "by_hand": True})
        self.assertEqual(checklist["drawing"]["detail"], f"{self.DWG}: title block shows {PART}")
        self.assertEqual(checklist["components"], {"skipped": True, "detail": "not an assembly"})

    def test_reject_needs_a_reason_and_records_the_checklist(self):
        self._in_review()
        code, result = self._run("reject", "--pn", PART)
        self.assertEqual((code, result), (1, None))
        code, result = self._run("reject", "--pn", PART, "--note", "Hole too close to the edge")
        self.assertEqual((code, result["revision_rejected"], result["revision"], result["state"]), (0, "01", "02", WIP))
        args = self._journal()[-1]["args"]
        self.assertEqual(args["reason"], "Hole too close to the edge")
        self.assertEqual(args["checklist"]["drawing_reviewed"], {"ok": False, "by_hand": True})
        self.assertTrue(args["checklist"]["checked_in"]["ok"])
        self._run("submit", "--pn", PART)
        code, result = self._run("reject", "--pn", PART, "--comment", "Still too close")  # the old option
        self.assertEqual((code, result["revision"]), (0, "03"))
        code, result = self._run("approve", "--pn", PART, "--note", "n", "--reviewed")
        self.assertEqual(code, 1)  # 03 is in work, not in review

    def test_retire_needs_a_reason_and_no_released_users(self):
        self._in_review()
        self._run("approve", "--pn", PART, "--note", "First release", "--reviewed")
        code, result = self._run("retire", "--pn", PART)
        self.assertEqual((code, result["blockers"]), (1, ["Give the reason with --note"]))
        assy = "LE-02-01001-001"
        self._run("create", "--pn", assy, "--type", "assembly")
        self._run("checkout", "--pn", assy)
        import test_release_check

        path = test_release_check.make_fcstd(os.path.join(self.tmp, assy + ".FCStd"), links=[PART + ".FCStd"])
        self._run("checkin", "--pn", assy, "--file", path, "--comment", "assembly")
        code, result = self._run("retire", "--pn", PART, "--note", "Replaced", "--dry-run")
        self.assertEqual((code, result["ok"], [r["part_number"] for r in result["where_used"]]), (0, True, [assy]))
        self._run("submit", "--pn", assy)
        self._run("approve", "--pn", assy, "--note", "First release", "--no-drawing")
        code, result = self._run("retire", "--pn", PART, "--note", "Replaced")
        self.assertEqual(code, 1)
        self.assertIn(f"{assy} rev A (released) still uses it", result["blockers"][0])
        self._run("submit", "--pn", self.DWG)
        self._run("approve", "--pn", self.DWG, "--note", "Drawing rev A", "--reviewed")
        code, result = self._run("retire", "--pn", self.DWG, "--note", "Replaced by a new drawing")
        self.assertEqual((code, result["state"]), (0, "Obsolete"))
        self.assertEqual(self._journal()[-1]["args"], {"part_number": self.DWG, "reason": "Replaced by a new drawing"})


if __name__ == "__main__":
    unittest.main()
