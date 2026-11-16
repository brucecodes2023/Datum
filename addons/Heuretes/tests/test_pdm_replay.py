# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM-10 step 1 and 2: the linear replay, the facts a dashboard needs, and reading only the
journal's new lines (plan section "Reading only what is new")."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heuretes_freecad import pdm  # noqa: E402
from heuretes_freecad import pdm_workspace as ws  # noqa: E402
from heuretes_freecad import vault_move  # noqa: E402
from heuretes_freecad.pdm import LifecycleError  # noqa: E402
from heuretes_freecad.pdm_folder import Event, FolderVault  # noqa: E402

TOP = "BW-02-01001-001"
SUB = "BW-02-01002-001"
PART = "BW-01-01001-001"
BOLT = "BW-01-01002-001"
ME = "bruce@mac"
OTHER = "alex@mbp"


def state(vault):
    """Everything a replay builds that a reader shows."""
    return json.dumps({"status": vault.status(), "bom": vault.bom, "changes": vault.changes(),
                       "locks": vault.locks, "lock_times": vault.lock_times,
                       "problems": vault.problems, "events": [e.id for e in vault.events]},
                      sort_keys=True, default=str)


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-replay-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "vault"), "Replay", actor=ME)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def file(self, name, text):
        path = os.path.join(self.src, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def fresh(self, actor=ME):
        return FolderVault(self.vault.root, actor=actor)

    def journal(self, actor):
        return os.path.join(self.vault.root, "journal", f"{actor}.jsonl")


class LinearReplayTests(_Vault):
    def test_model_skips_the_cycle_check_only_while_replaying(self):
        model = pdm.Vault()
        for pn in (TOP, SUB):
            model.create_item(pn, "assembly")
        model.set_bom(TOP, [(SUB, 1)])
        with self.assertRaises(LifecycleError):
            model.set_bom(SUB, [(TOP, 1)])  # strict: refused as before
        self.assertIsNone(model.find_cycle())
        model.strict = False
        model.set_bom(SUB, [(TOP, 1)])  # a replay accepts the line ...
        self.assertEqual(model.find_cycle(), [TOP, SUB, TOP])  # ... and the store checks once

    def test_a_cycle_made_by_two_journals_is_a_problem_after_the_replay(self):
        for pn in (TOP, SUB):
            self.vault.create_item(pn, "assembly")
        other = self.fresh(OTHER)
        self.vault.set_bom(TOP, [(SUB, 1)])
        other.set_bom(SUB, [(TOP, 1)])  # alex's Mac had not seen bruce's line yet
        merged = self.fresh()
        self.assertTrue(any("BOM cycle" in p and TOP in p for p in merged.problems), merged.problems)

    def test_events_are_kept_in_replay_order(self):
        self.vault.create_item(PART)
        self.vault.checkout(PART)
        self.vault.checkin(PART, self.file("p.FCStd", "1"), "first")
        again = self.fresh()
        self.assertEqual([e.id for e in again.events], [e.id for e in again._read_events()])
        self.assertEqual([e.op for e in self.vault.events], ["create_item", "checkout", "checkin"])

    def test_lock_start_time(self):
        self.vault.create_item(PART)
        self.vault.checkout(PART)
        taken = self.vault.events[-1].at
        self.assertEqual(self.vault.lock_times, {PART: taken})
        self.vault.checkout(PART)  # again by the holder: the lock keeps its start
        self.vault.checkin(PART, self.file("p.FCStd", "1"), "first", keep_lock=True)
        self.assertEqual(self.fresh().lock_times, {PART: taken})
        self.vault.checkin(PART, self.file("p.FCStd", "2"), "second")
        self.assertEqual(self.vault.lock_times, {})
        self.vault.checkout(PART)
        self.vault.undo_checkout(PART)
        self.assertEqual(self.fresh().lock_times, {})


class ObjectHealthTests(_Vault):
    def setUp(self):
        super().setUp()
        self.versions = []
        for i, pn in enumerate((PART, BOLT, SUB)):
            self.vault.create_item(pn, "assembly" if pn == SUB else "part")
            self.vault.checkout(pn)
            self.versions.append(self.vault.checkin(pn, self.file(f"{pn}.FCStd", str(i)), "first"))

    def path(self, version):
        return os.path.join(self.vault.root, version.object_name)

    def test_stand_in_is_in_the_cloud_not_missing(self):
        evicted, gone = self.versions[0], self.versions[1]
        folder, name = os.path.split(self.path(evicted))
        os.replace(self.path(evicted), os.path.join(folder, "." + name + ".icloud"))
        os.remove(self.path(gone))
        vault = self.fresh()
        self.assertEqual(vault.missing_objects(), [gone.object_name])
        health = vault.object_health()
        self.assertEqual((health["listed"], health["local"]), (3, 1))
        self.assertEqual(health["cloud"], [evicted.object_name])
        self.assertEqual(health["stand_ins"], [evicted.object_name])
        self.assertEqual(health["missing"], [gone.object_name])
        # Moving the vault still waits for both: a stand-in is not the file.
        os.makedirs(os.path.join(self.tmp, "dest"))
        plan = vault_move.plan_move(vault.root, os.path.join(self.tmp, "dest"))
        self.assertTrue(any("2 file(s)" in text for _level, text in plan.issues), plan.issues)

    def test_dataless_counts_as_cloud(self):
        dataless = self.path(self.versions[2])

        def fake_stat(path):
            real = os.stat(path)
            flags = ws.SF_DATALESS if path == dataless else 0
            return SimpleNamespace(st_flags=flags, st_size=real.st_size)

        health = self.vault.object_health(stat_fn=fake_stat)
        self.assertEqual(health["cloud"], [self.versions[2].object_name])
        self.assertEqual((health["local"], health["stand_ins"], health["missing"]), (2, [], []))


class ReadOnlyWorkspaceTests(_Vault):
    def test_reader_never_writes_state(self):
        self.vault.create_item(PART)
        writer = ws.Workspace(self.vault)
        self.vault.checkout(PART)
        self.vault.checkin(PART, self.file("p.FCStd", "1"), "first")
        writer.get_latest(PART)
        state_path = writer.state_path
        with open(state_path, encoding="utf-8") as handle:
            saved = handle.read()
        os.utime(writer.path(PART), (1, 1))  # new mtime: a reader would re-hash and save
        reader = ws.Workspace(self.vault, read_only=True)
        self.assertEqual(reader.status(PART).code, ws.UP_TO_DATE)
        reader.statuses()
        reader.my_work()
        with open(state_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), saved)
        for call in (lambda: reader.checkout(PART), lambda: reader.get_latest(PART),
                     lambda: reader.checkin(PART, "c"), lambda: reader.undo_checkout(PART),
                     lambda: reader.break_lock(PART)):
            with self.assertRaises(PermissionError):
                call()
        self.assertNotIn(PART, self.vault.locks)


class IncrementalTests(_Vault):
    def setUp(self):
        super().setUp()
        for pn in (PART, BOLT):
            self.vault.create_item(pn)
        self.vault.create_item(TOP, "assembly")
        self.vault.set_bom(TOP, [(PART, 2)])
        self.reader = self.fresh()
        self.full = 0
        refresh = self.reader.refresh

        def counted():
            self.full += 1
            refresh()

        self.reader.refresh = counted

    def test_nothing_new(self):
        self.assertFalse(self.reader.update())
        self.assertEqual(self.full, 0)

    def test_new_lines_are_applied_in_place(self):
        other = self.fresh(OTHER)
        other.checkout(BOLT)
        self.vault.submit(PART)
        self.vault.set_bom(TOP, [(PART, 2), (BOLT, 4)])
        heard = []
        self.reader.add_listener(heard.append)
        self.assertTrue(self.reader.update())
        self.assertEqual(self.full, 0)
        self.assertEqual(state(self.reader), state(self.fresh()))
        self.assertEqual(heard, [{PART: "In Review"}])
        self.assertFalse(self.reader.update())

    def test_own_lines_are_not_applied_twice(self):
        self.reader.checkout(BOLT)  # recorded through the reader itself
        self.assertFalse(self.reader.update())
        self.assertEqual(state(self.reader), state(self.fresh()))

    def test_late_line_from_another_mac_replays_everything(self):
        early = self.vault.events[2].at  # after BOLT exists, before what the reader applied last
        line = Event(str(uuid.uuid4()), OTHER, 1, early, "checkout", {"part_number": BOLT}).to_json()
        with open(self.journal(OTHER), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.assertTrue(self.reader.update())
        self.assertEqual(self.full, 1)
        self.assertEqual(self.reader.locks, {BOLT: OTHER})
        self.assertEqual(state(self.reader), state(self.fresh()))

    def test_half_synced_line_waits_for_its_end(self):
        line = Event(str(uuid.uuid4()), OTHER, 1, "2999-01-01T00:00:00+00:00", "checkout",
                     {"part_number": BOLT}).to_json()
        with open(self.journal(OTHER), "a", encoding="utf-8") as handle:
            handle.write(line[:30])
        self.assertFalse(self.reader.update())
        with open(self.journal(OTHER), "a", encoding="utf-8") as handle:
            handle.write(line[30:] + "\n")
        self.assertTrue(self.reader.update())
        self.assertEqual((self.reader.locks, self.full), ({BOLT: OTHER}, 0))
        self.assertEqual(self.reader.problems, [])

    def test_conflict_copy_changes_nothing(self):
        shutil.copyfile(self.journal(ME), os.path.join(self.vault.root, "journal", f"{ME} 2.jsonl"))
        self.assertFalse(self.reader.update())
        self.assertEqual(self.full, 0)
        self.assertEqual(state(self.reader), state(self.fresh()))

    def test_replaced_or_shrunk_file_replays_everything(self):
        with open(self.journal(ME), encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(self.journal(ME), "w", encoding="utf-8") as handle:
            handle.writelines(lines[:-1])  # the set_bom line is gone
        self.assertTrue(self.reader.update())
        self.assertEqual(self.full, 1)
        self.assertEqual(self.reader.bom, {})


class GeneratedVaultTests(unittest.TestCase):
    """The spike's generator as a fixture: a full replay and an incremental one agree."""

    def test_incremental_matches_full_replay(self):
        import vault_fixture

        tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-gen-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = os.path.join(tmp, "v")
        vault = vault_fixture.make_vault(root, 120)
        reader = FolderVault(root, actor=ME)
        vault_fixture.more_work(vault, os.path.join(tmp, "src-more"))
        self.assertTrue(reader.update())
        self.assertEqual(state(reader), state(FolderVault(root, actor=ME)))


if __name__ == "__main__":
    unittest.main()
