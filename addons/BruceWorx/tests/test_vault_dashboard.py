# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault dashboard snapshot (BW-PDM-10, plan section 7.11)."""

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bruceworx import pdm_folder  # noqa: E402
from bruceworx import vault_dashboard as vd  # noqa: E402
from bruceworx.pdm_folder import Event, FolderVault  # noqa: E402
from bruceworx.pdm_workspace import Workspace  # noqa: E402

ME, MBP, ALEX = "bruce@mac", "bruce@mbp", "alex@mbp"
TOP, SUB, PART, BOLT, NUT, DWG = ("DM-02-01001-001", "DM-02-01002-001", "DM-01-01001-001", "DM-05-01001-001",
                                  "DM-05-01002-001", "DM-03-01001-001")
NOW = dt.datetime(2026, 10, 24, 15, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))


def ev(at, op, actor=ME, **args):
    """An Event at minutes before NOW."""
    when = (NOW - dt.timedelta(minutes=at)).astimezone(dt.timezone.utc).isoformat()
    return Event(f"{op}-{at}-{actor}", actor, 1, when, op, args)


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-dash-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "Workshop"), "Workshop", actor=ME)
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def checkin(self, pn, text="1", vault=None):
        vault = vault or self.vault
        path = os.path.join(self.src, pn + ".FCStd")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(pn + text)
        vault.checkout(pn)
        return vault.checkin(pn, path, "first")

    def structure(self):
        """TOP holds SUB (2) and BOLT (40); SUB holds PART (2) and BOLT (4); DWG documents PART."""
        v = self.vault
        for pn, kind in ((PART, "part"), (BOLT, "cots"), (NUT, "cots"), (SUB, "assembly"), (TOP, "assembly")):
            v.create_item(pn, kind, pn[-9:])
        v.create_item(DWG, "drawing", "Plate drawing", drawing_of=PART)
        v.set_bom(SUB, [(PART, 2), (BOLT, 4)])
        v.set_bom(TOP, [(SUB, 2), (BOLT, 40)])
        for pn in (PART, DWG):
            self.checkin(pn)
            v.submit(pn)
        v.approve(PART, note="first")


class CountsAndProjects(_Vault):
    def test_tiles(self):
        self.structure()
        tiles = {t["type"]: t for t in vd.tiles(self.vault)}
        self.assertEqual(tiles["part"]["total"], 1)
        self.assertEqual(tiles["part"]["states"]["Released"], 1)
        self.assertEqual(tiles["cots"]["total"], 2)
        self.assertEqual(tiles["drawing"]["states"]["In Review"], 1)
        self.assertEqual(sum(t["total"] for t in tiles.values()), len(self.vault.items))

    def test_project_rolls_up_each_item_once_with_drawings(self):
        self.structure()
        projects = vd.projects(self.vault)
        self.assertEqual((projects["label"], projects["total"], projects["hidden"]), ("program", 1, False))
        row = projects["rows"][0]
        self.assertEqual(row["program"], "DM")
        # Every DM item once, including NUT, which no assembly uses.
        self.assertEqual((row["items"], row["drawings"]), (6, 1))
        self.assertEqual(row["states"], {"WIP": 4, "In Review": 1, "Released": 1, "Obsolete": 0})

    def test_memoised_closure_matches_explode(self):
        self.structure()
        closure = vd.closures(self.vault, [TOP, SUB])
        for top in (TOP, SUB):
            self.assertEqual(closure[top], {top} | set(self.vault.explode(top)))

    def test_programs_group_every_item_whatever_the_top_count(self):
        """A project is the program, the first field of the part number, not each top assembly."""
        self.structure()
        for i in range(vd.MAX_PROJECTS + 1):
            self.vault.create_item(f"DM-02-0{2000 + i}-001", "assembly")
        self.vault.create_item("LE-01-01001-001", "part", "Arm link")
        projects = vd.projects(self.vault)
        self.assertEqual((projects["label"], projects["hidden"]), ("program", False))
        self.assertEqual([r["program"] for r in projects["rows"]], ["DM", "LE"])
        dm = projects["rows"][0]
        self.assertEqual(dm["items"], 6 + vd.MAX_PROJECTS + 1)
        self.assertEqual(projects["rows"][1]["items"], 1)

    def test_many_assemblies_stay_one_program(self):
        for i in range(vd.MAX_PROJECTS + 1):
            self.vault.create_item(f"DM-02-0{2000 + i}-001", "assembly")
        projects = vd.projects(self.vault)
        self.assertEqual((projects["total"], projects["hidden"]), (1, False))
        self.assertEqual(projects["rows"][0]["items"], vd.MAX_PROJECTS + 1)


class MyWorkTests(_Vault):
    def test_queues_are_capped_with_full_counts(self):
        for i in range(5):
            self.vault.create_item(f"DM-01-0{1100 + i}-001")
        work = Workspace(self.vault)
        for i in range(5):
            work.checkout(f"DM-01-0{1100 + i}-001")
        self.vault.create_item(PART)
        self.checkin(PART)
        self.vault.submit(PART)
        reader = FolderVault(self.vault.root, actor=ME)
        snap = vd.my_work(reader, Workspace(reader, read_only=True), ME, NOW, vd.last_events(reader.events))
        queues = {q["key"]: q for q in snap["queues"]}
        self.assertEqual((queues["checked_out"]["total"], len(queues["checked_out"]["rows"])), (5, 3))
        self.assertEqual(queues["in_review"]["total"], 1)
        self.assertTrue(queues["in_review"]["rows"][0]["status"].startswith("In review since"))
        self.assertEqual(snap["total"], 6)

    def test_my_other_mac_counts_as_me(self):
        self.vault.create_item(PART)
        self.vault.create_item(BOLT, "cots")
        FolderVault(self.vault.root, actor=MBP).checkout(PART)
        FolderVault(self.vault.root, actor=ALEX).checkout(BOLT)
        reader = FolderVault(self.vault.root, actor=ME)
        rows = vd.my_work(reader, Workspace(reader, read_only=True), ME, NOW)["queues"][0]["rows"]
        self.assertEqual([(r["pn"], r["status"]) for r in rows], [(PART, "Checked out to you on mbp")])
        health = vd.health(reader, ME, NOW)
        locks = next(line for line in health["lines"] if line["key"] == "locks")
        self.assertEqual(locks["text"], "Locks: 1 held by you, 1 by others")


class ChangesTests(_Vault):
    def test_what_each_change_waits_for(self):
        self.vault.create_item(PART)
        self.vault.create_item(BOLT, "cots")
        for pn in (PART, BOLT):
            self.checkin(pn)
            self.vault.submit(pn)
            self.vault.approve(pn, note="first")
        ecr1 = self.vault.raise_ecr(None, "Jaw insert wears fast", [PART]).number
        ecr2 = self.vault.raise_ecr(None, "Handle too low", [BOLT]).number
        ecr3 = self.vault.raise_ecr(None, "Caster bracket cracks", [BOLT]).number
        self.vault.decide_ecr(ecr1, True, "agreed")
        self.vault.decide_ecr(ecr2, True, "agreed")
        eco = self.vault.open_eco(None, [ecr1]).number
        rows = vd.changes(self.vault)["rows"]
        self.assertEqual([(r["number"], r["waits"]) for r in rows],
                         [(eco, "0 of 1 in review"), (ecr3, "to decide"), (ecr2, "no ECO yet")])


class ActivityTests(unittest.TestCase):
    def test_fold_runs_of_one_actor_and_operation(self):
        events = [ev(60, "checkin", part_number="A", comment="slot"), ev(55, "checkin", part_number="B", comment="slot"),
                  ev(50, "checkin", part_number="C", comment="slot"), ev(30, "checkin", part_number="D"),
                  ev(29, "checkin", ALEX, part_number="E"), ev(20, "open_eco", number="ECO-0001", ecr_numbers=[]),
                  ev(19, "open_eco", number="ECO-0002", ecr_numbers=[])]
        self.assertEqual([len(g) for g in vd.fold(events)], [3, 1, 1, 1, 1])

    def test_feed_hides_check_outs_folds_and_names_people(self):
        events = [ev(60 * 24 * 20, "checkin", part_number="OLD", comment="x"),  # outside 14 days
                  ev(60 * 26, "raise_ecr", ALEX, number="ECR-0007", problem="Caster bracket cracks", items=["A"]),
                  ev(120, "checkout", part_number="A"),
                  ev(100, "checkin", part_number="A", comment="Side rail: slot 2 mm wider"),
                  ev(95, "checkin", MBP, part_number="B", comment="Side rail: slot 2 mm wider"),
                  ev(94, "checkin", MBP, part_number="C", comment="Side rail: slot 2 mm wider"),
                  ev(10, "approve", part_number="A", note="ok")]
        feed = vd.activity(events, ME, NOW, days=14)
        self.assertEqual(len(feed["bars"]), 14)
        self.assertEqual(feed["bars"][-1], 4)  # today: the check-ins and the release, not the check-out
        lines = [(g["label"], r["who"], r["text"], r["detail"]) for g in feed["groups"] for r in g["rows"]]
        self.assertEqual(lines, [
            ("Today", "You", "released A rev A", ""),
            ("Today", "You", "checked in 2 files", "“Side rail: slot 2 mm wider”"),
            ("Today", "You", "checked in A", "“Side rail: slot 2 mm wider”"),
            ("Yesterday", "alex", "raised ECR-0007", "Caster bracket cracks"),
        ])
        everything = vd.activity(events, ME, NOW, days=30, show_all=True)
        self.assertIn("checked out A", [r["text"] for g in everything["groups"] for r in g["rows"]])
        mine = vd.activity(events, ME, NOW, mine_only=True)
        self.assertNotIn("alex", [r["who"] for g in mine["groups"] for r in g["rows"]])
        paged = vd.activity(events, ME, NOW, limit=2)
        self.assertEqual((sum(len(g["rows"]) for g in paged["groups"]), paged["older"]), (2, True))

    def test_vault_created_line(self):
        created = (NOW - dt.timedelta(hours=2)).astimezone(dt.timezone.utc).isoformat()
        feed = vd.activity([], ME, NOW, created=created)
        self.assertEqual(feed["groups"][0]["rows"][0]["text"], "Vault created")


class HealthTests(_Vault):
    def test_old_lock_future_event_and_conflict_copy(self):
        now = dt.datetime.now().astimezone()  # the vault's own events are dated now
        self.vault.create_item(PART)
        self.vault.create_item(BOLT, "cots")
        self.vault.checkout(PART)
        self.vault.lock_times[PART] = (now - dt.timedelta(days=20)).astimezone(dt.timezone.utc).isoformat()
        journal = os.path.join(self.vault.root, "journal")
        shutil.copyfile(os.path.join(journal, f"{ME}.jsonl"), os.path.join(journal, f"{ME} 2.jsonl"))
        ahead = (now + dt.timedelta(days=1)).astimezone(dt.timezone.utc).isoformat()  # a Mac a day ahead
        self.vault.events.append(Event("ahead", ALEX, 1, ahead, "checkout", {"part_number": BOLT}))
        health = vd.health(self.vault, ME, now, read_ms=12)
        lines = {line["key"]: line for line in health["lines"]}
        self.assertEqual(lines["locks"]["note"], "oldest 20 d")
        self.assertEqual(lines["locks"]["level"], "warn")
        self.assertEqual([s["pn"] for s in health["stale_locks"]], [PART])
        self.assertEqual(health["conflict_copies"], [f"{ME} 2.jsonl"])
        self.assertIn("1 event dated in the future", lines["journal"]["text"])
        self.assertIn("from 1 Mac", lines["journal"]["text"])
        self.assertEqual(lines["journal"]["note"], "0.01 s to read")

    def test_still_syncing_turns_orange_after_ten_minutes(self):
        self.vault.create_item(PART)
        version = self.checkin(PART)
        os.remove(os.path.join(self.vault.root, version.object_name))
        late = dt.datetime.fromisoformat(version.at) + dt.timedelta(minutes=11)
        health = vd.health(FolderVault(self.vault.root, actor=ME), ME, late)
        syncing = next(line for line in health["lines"] if line["key"] == "syncing")
        self.assertEqual((syncing["level"], syncing["note"]), ("warn", "since 11 min"))
        self.assertEqual(syncing["text"], "1 version still syncing from another Mac")

    def test_icloud_line_counts_stand_ins_as_in_the_cloud(self):
        root = os.path.join(self.tmp, "Mobile Documents", "com~apple~CloudDocs", "Shop")
        vault = FolderVault.init(root, "Shop", actor=ME)
        for pn in (PART, BOLT):
            vault.create_item(pn)
            version = self.checkin(pn, pn, vault)
        folder, name = os.path.split(os.path.join(root, version.object_name))
        os.replace(os.path.join(folder, name), os.path.join(folder, "." + name + ".icloud"))
        health = vd.health(FolderVault(root, actor=ME), ME, dt.datetime.now().astimezone())
        line = health["lines"][0]
        self.assertEqual((health["storage"], line["text"], line["note"]),
                         ("iCloud Drive", "iCloud: all 2 files listed, 1 on this Mac", "1 in the cloud only"))
        self.assertEqual(health["syncing"], 0)

    def test_conflict_copy_names(self):
        for name in ("bruce@mac 2.jsonl", "bruce@mac (Bruce's conflicted copy 2026-10-24).jsonl",
                     "bruce@mac.sync-conflict-20261024-1200.jsonl"):
            self.assertTrue(vd.is_conflict_copy(name), name)
        self.assertFalse(vd.is_conflict_copy("bruce@mac.jsonl"))


class SnapshotTests(_Vault):
    def test_empty_vault(self):
        snap = vd.read(self.vault.root, ME)
        self.assertTrue(snap["empty"])
        self.assertEqual(snap["activity"]["groups"][0]["rows"][0]["text"], "Vault created")
        texts = [line["text"] for line in snap["health"]["lines"]]
        self.assertEqual(texts[-1], "Journal: empty, ready")
        json.dumps(snap)  # JSON-ready

    def test_snapshot_is_json_and_writes_nothing(self):
        self.structure()
        Workspace(self.vault).checkout(SUB)
        before = {p: os.stat(os.path.join(dp, p)).st_mtime_ns for dp, _d, fs in os.walk(self.vault.root) for p in fs}
        snap = vd.read(self.vault.root, ME)
        after = {p: os.stat(os.path.join(dp, p)).st_mtime_ns for dp, _d, fs in os.walk(self.vault.root) for p in fs}
        self.assertEqual(before, after)
        self.assertFalse(snap["empty"])
        self.assertEqual(snap["parts"][PART], {"rev": "A", "state": "Released"})
        json.dumps(snap)


class GeneratedVaultBudget(unittest.TestCase):
    """The spike's 500-item vault: the page's data in well under 0.1 s (plan acceptance)."""

    @classmethod
    def setUpClass(cls):
        import vault_fixture

        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-dash500-"))
        cls.root = os.path.join(cls.tmp, "w500")
        vault_fixture.make_vault(cls.root, 500, name="Workshop")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_time_budget_and_counts(self):
        best = None
        for _ in range(3):
            started = time.perf_counter()
            snap = vd.read(self.root, ME)
            took = time.perf_counter() - started
            best = took if best is None else min(best, took)
        self.assertLess(best, 0.1)
        self.assertEqual(sum(t["total"] for t in snap["tiles"]), 500)
        self.assertEqual((snap["projects"]["total"], snap["projects"]["hidden"]), (1, False))
        self.assertEqual(snap["projects"]["rows"][0]["program"], "VX")
        self.assertTrue(all(len(q["rows"]) <= vd.QUEUE_ROWS for q in snap["my_work"]["queues"]))
        self.assertLess(len(json.dumps({k: v for k, v in snap.items() if k != "parts"})), 32000)  # about 20 kB, 50 feed lines


if __name__ == "__main__":
    unittest.main()
