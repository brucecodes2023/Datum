# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for the BruceWorx Start page (bruceworx_start)."""

import datetime as dt
import os
import shutil
import tempfile
import unittest
import zipfile

import bruceworx_start as start

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # header only; the page decodes it, not these tests


def document_xml(objects):
    """A Document.xml like FreeCAD writes: ``objects`` is (name, type, props),
    props a dict of name -> str, or (index, [enum names]) for an enumeration."""
    heads = "".join(f'<Object type="{t}" name="{n}" id="{i}"/>' for i, (n, t, _p) in enumerate(objects))
    data = []
    for name, _type, props in objects:
        body = []
        for key, value in props.items():
            if isinstance(value, tuple):
                index, names = value
                enums = "".join(f'<Enum value="{v}"/>' for v in names)
                body.append(
                    f'<Property name="{key}" type="App::PropertyEnumeration" group="BruceWorx">'
                    f'<Integer value="{index}" CustomEnum="true"/>'
                    f'<CustomEnumList count="{len(names)}">{enums}</CustomEnumList></Property>'
                )
            else:
                body.append(f'<Property name="{key}" type="App::PropertyString"><String value="{value}"/></Property>')
        body.append('<Property name="Label" type="App::PropertyString"><String value="x"/></Property>')
        data.append(f'<Object name="{name}"><Properties Count="{len(body)}">{"".join(body)}</Properties></Object>')
    return (
        '<?xml version="1.0" encoding="utf-8"?><Document SchemaVersion="4" FileVersion="1">'
        '<Properties Count="0"/>'
        f'<Objects Count="{len(objects)}">{heads}</Objects>'
        f'<ObjectData Count="{len(objects)}">{"".join(data)}</ObjectData></Document>'
    ).encode("utf-8")


STATE = (2, ["WIP", "In Review", "Released", "Obsolete"])


class FakeGroup:
    def __init__(self, ints=None, strings=None):
        self.ints, self.strings = ints or {}, strings or {}

    def GetInt(self, name, default=0):
        return self.ints.get(name, default)

    def GetString(self, name, default=""):
        return self.strings.get(name, default)


class TestRecentFiles(unittest.TestCase):
    def test_order_and_stop_at_gap(self):
        group = FakeGroup({"RecentFiles": 4}, {"MRU0": "/a.FCStd", "MRU1": "/b.FCStd", "MRU3": "/d.FCStd"})
        self.assertEqual(start.recent_files(group), ["/a.FCStd", "/b.FCStd"])

    def test_count_limits_and_repeats_dropped(self):
        group = FakeGroup({"RecentFiles": 2}, {"MRU0": "/a", "MRU1": "/a", "MRU2": "/c"})
        self.assertEqual(start.recent_files(group), ["/a"])

    def test_no_count_reads_until_gap(self):
        group = FakeGroup({}, {"MRU0": "/a", "MRU1": "/b"})
        self.assertEqual(start.recent_files(group), ["/a", "/b"])
        self.assertEqual(start.recent_files(FakeGroup()), [])


class TestReadFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, name, objects, thumbnail=True):
        path = os.path.join(self.dir, name)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Document.xml", document_xml(objects))
            if thumbnail:
                zf.writestr(start.THUMBNAIL, PNG)
        return path

    def test_part_properties_and_thumbnail(self):
        path = self.make("Bracket.FCStd", [
            ("Body", "PartDesign::Body", {}),
            ("Part", "App::Part", {"PartNumber": "LE-01-01001", "Revision": "02", "LifecycleState": STATE,
                                   "ItemType": (0, ["part", "assembly", "cots", "drawing"])}),
        ])
        info = start.read_fcstd(path)
        self.assertEqual((info["pn"], info["rev"], info["state"]), ("LE-01-01001", "02", "Released"))
        self.assertEqual(info["kind"], "part")
        self.assertEqual(info["thumbnail"], PNG)

    def test_assembly_wins_and_kind(self):
        path = self.make("Arm.FCStd", [
            ("Body", "PartDesign::Body", {"PartNumber": "LE-01-00001", "Revision": "01"}),
            ("Assembly", "Assembly::AssemblyObject", {"PartNumber": "LE-03-01200", "Revision": "B"}),
        ], thumbnail=False)
        info = start.read_fcstd(path)
        self.assertEqual((info["pn"], info["rev"], info["kind"]), ("LE-03-01200", "B", "assembly"))
        self.assertIsNone(info["thumbnail"])

    def test_empty_part_number_skipped_and_plain_enum(self):
        xml = document_xml([("Part", "App::Part", {"PartNumber": "", "LifecycleState": STATE}),
                            ("Page", "TechDraw::DrawPage", {})])
        xml = xml.replace(b' CustomEnum="true"', b"")
        info = start.parse_document_xml(xml)
        self.assertEqual((info["pn"], info["state"], info["kind"]), ("", "", "drawing"))
        enum_only = document_xml([("P", "App::Part", {"PartNumber": "X", "LifecycleState": (1, [])})])
        self.assertEqual(start.parse_document_xml(enum_only)["state"], "In Review")

    def test_unreadable(self):
        path = os.path.join(self.dir, "bad.FCStd")
        with open(path, "wb") as handle:
            handle.write(b"not a zip")
        info = start.read_fcstd(path)
        self.assertEqual((info["pn"], info["kind"]), ("", "other"))
        self.assertIn("error", info)

    def test_cache_by_mtime(self):
        path = self.make("A.FCStd", [("P", "App::Part", {"PartNumber": "A-1"})])
        calls = []

        def reader(p):
            calls.append(p)
            return start.read_fcstd(p)

        cache = start.InfoCache(reader)
        self.assertIsNone(cache.get(path))
        self.assertEqual(cache.load(path)["pn"], "A-1")
        self.assertEqual(cache.load(path)["pn"], "A-1")
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.get(path)["pn"], "A-1")
        self.make("A.FCStd", [("P", "App::Part", {"PartNumber": "A-2"})])
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 5))
        self.assertIsNone(cache.get(path))
        self.assertEqual(cache.load(path)["pn"], "A-2")
        self.assertEqual(len(calls), 2)
        os.remove(path)
        self.assertIsNone(cache.load(path))


class TestVault(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 23, 15, 0, tzinfo=dt.timezone.utc)  # a Friday

    ITEMS = [
        {"part_number": "A", "state": "WIP", "checked_out_by": "me@mac"},
        {"part_number": "B", "state": "WIP", "checked_out_by": "you@pc"},
        {"part_number": "C", "state": "In Review", "checked_out_by": None},
        {"part_number": "D", "state": "Released", "checked_out_by": None},
        {"part_number": "E", "state": "Released", "checked_out_by": None},
        {"part_number": "F", "state": "WIP", "checked_out_by": "me@mac"},
    ]
    EVENTS = [
        ("create_item", "2026-10-01T10:00:00+00:00", {"part_number": "D"}),
        ("approve", "2026-10-22T09:00:00+00:00", {"part_number": "D"}),
        ("approve", "2026-10-16T09:00:00+00:00", {"part_number": "Z"}),  # last week
        ("release_eco", "2026-10-19T00:30:00+00:00", {"number": "ECO-1"}),  # Monday
        ("approve", "2026-10-24T09:00:00+00:00", {"part_number": "Y"}),  # future: clock skew
        ("approve", "not a date", {"part_number": "W"}),
    ]

    def test_counts(self):
        summary = start.vault_summary(self.ITEMS, self.EVENTS, "me@mac", self.NOW, {"ECO-1": ["E", "D"]})
        self.assertEqual(summary, {"checked_out": 2, "in_review": 1, "released_week": 2, "items": 6})

    def test_empty(self):
        self.assertEqual(start.vault_summary([], [], "me", self.NOW),
                         {"checked_out": 0, "in_review": 0, "released_week": 0, "items": 0})

    def test_week_start(self):
        self.assertEqual(start.week_start(self.NOW), dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc))
        monday = dt.datetime(2026, 10, 19, 8, tzinfo=dt.timezone.utc)
        self.assertEqual(start.week_start(monday).day, 19)

    def test_merge_vault(self):
        info = {"pn": "A", "rev": "01", "state": "WIP"}
        self.assertEqual(start.merge_vault(info), {"pn": "A", "rev": "01", "state": "WIP", "in_vault": None})
        parts = {"A": {"rev": "02", "state": "In Review"}}
        self.assertEqual(start.merge_vault(info, parts)["state"], "In Review")
        self.assertEqual(start.merge_vault(info, parts)["rev"], "02")
        self.assertFalse(start.merge_vault({"pn": ""}, parts)["in_vault"])
        self.assertFalse(start.merge_vault({"pn": "Q"}, parts)["in_vault"])

    def test_find_vault_ref(self):
        folder = tempfile.mkdtemp()
        try:
            self.assertEqual(start.find_vault_ref(folder), "")
            ref = os.path.join(folder, "Shop.bwvault")
            open(ref, "w").close()
            self.assertEqual(start.find_vault_ref(folder), ref)
            self.assertEqual(start.find_vault_ref(ref), ref)
            self.assertEqual(start.find_vault_ref(os.path.join(folder, "nope")), "")
        finally:
            shutil.rmtree(folder)

    def test_storage_name(self):
        icloud = "/Users/x/Library/Mobile Documents/com~apple~CloudDocs/Vault"
        self.assertEqual(start.storage_name(icloud), "iCloud Drive")
        self.assertEqual(start.storage_name("/Users/x/Dropbox/Vault"), "Dropbox")


class TestMyWork(unittest.TestCase):
    class St:  # stands in for pdm_workspace.FileStatus
        part_number = "BW-01-01001-001"
        display = "BW-01-01001-001-02"
        description = "Plate"
        label = "Checked out to me, modified"
        code = "modified"
        path = "/v/work/me/BW-01-01001-001.FCStd"
        item_type = "part"

    def test_work_row(self):
        row = start.work_row(self.St())
        self.assertEqual((row["display"], row["status"], row["kind"]),
                         ("BW-01-01001-001-02", "Checked out to me, modified", "part"))

    def test_sections_skip_empty_groups_and_keep_order(self):
        snap = {"my_work": {"newer": [1], "checked_out": [1, 2], "in_review": []}}
        self.assertEqual([t for t, _r in start.my_work_sections(snap)], ["Checked out to me", "Newer in the vault"])
        self.assertEqual(start.my_work_sections(None), [])
        self.assertEqual(start.my_work_sections({"error": "x"}), [])

    def test_pinned_actor(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ref = os.path.join(tmp, "Acme.bwvault")
        with open(ref, "w") as handle:
            handle.write('{"vault_id": "abc"}')
        saved = {"abc": "bruce@mac"}
        self.assertEqual(start.pinned_actor(ref, lambda v: saved.get(v, "")), "bruce@mac")
        self.assertEqual(start.pinned_actor(os.path.join(tmp, "none"), lambda v: "x"), "")

    def test_signature_follows_work_state(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "journal"))
        before = start.vault_signature(tmp)
        state = os.path.join(tmp, "work", "me", ".bw", "state.json")
        os.makedirs(os.path.dirname(state))
        with open(state, "w") as handle:
            handle.write("{}")
        self.assertNotEqual(start.vault_signature(tmp), before)


class TestText(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 23, 15, 0)

    def ts(self, *args):
        return dt.datetime(*args).timestamp()

    def test_modified(self):
        self.assertEqual(start.format_modified(self.ts(2026, 10, 23, 14, 52), self.NOW), "Today 14:52")
        self.assertEqual(start.format_modified(self.ts(2026, 10, 22, 23, 59), self.NOW), "Yesterday")
        self.assertEqual(start.format_modified(self.ts(2026, 10, 16, 9, 0), self.NOW), "Oct 16")
        self.assertEqual(start.format_modified(self.ts(2025, 3, 2, 9, 0), self.NOW), "Mar 2, 2025")

    def test_edited(self):
        self.assertEqual(start.format_edited(self.ts(2026, 10, 23, 14, 48), self.NOW), "Edited 12 min ago")
        self.assertEqual(start.format_edited(self.ts(2026, 10, 23, 14, 59, 30), self.NOW), "Edited just now")
        self.assertEqual(start.format_edited(self.ts(2026, 10, 23, 9, 5), self.NOW), "Today 09:05")

    def test_badge(self):
        self.assertEqual(start.badge("WIP", "02"), ("wip", "WIP 02"))
        self.assertEqual(start.badge("Released", "A"), ("rel", "Rev A"))
        self.assertEqual(start.badge("In Review", "03"), ("rev", "In review 03"))
        self.assertEqual(start.badge("In Review", "03", short=True), ("rev", "In review"))
        self.assertEqual(start.badge("Obsolete", "B"), ("obs", "Obsolete"))
        self.assertIsNone(start.badge("", "01"))

    def test_location(self):
        self.assertEqual(start.location_text("/v/Fixtures/a.FCStd", "/v"), "Vault / Fixtures")
        self.assertEqual(start.location_text("/v/a.FCStd", "/v"), "Vault")
        self.assertEqual(start.location_text("/home/me/Desktop/a.FCStd", "/v", "/home/me"), "~/Desktop")
        self.assertEqual(start.location_text("/tmp/a.FCStd", "", "/home/me"), "/tmp")
        self.assertEqual(start.location_text("/home/meme/a.FCStd", "", "/home/me"), "/home/meme")

    def test_matches(self):
        self.assertTrue(start.matches("Bracket.FCStd", "LE-01", "part"))
        self.assertTrue(start.matches("Bracket.FCStd", "LE-01", "part", "part", "le-0"))
        self.assertFalse(start.matches("Bracket.FCStd", "LE-01", "part", "assembly"))
        self.assertFalse(start.matches("Bracket.FCStd", "", "part", None, "hinge"))

    def test_example_title(self):
        self.assertEqual(start.example_title("EngineBlock.FCStd"), "Engine block")
        self.assertEqual(start.example_title("FEMExample.FCStd"), "FEM")
        self.assertEqual(start.example_title("AssemblyExample.FCStd"), "Assembly")
        self.assertEqual(start.example_title("draft_test_objects.FCStd"), "Draft test objects")


class TestStockStartView(unittest.TestCase):
    """The page depends on StartView's object name and page order."""

    def test_names_in_source(self):
        here = os.path.dirname(os.path.abspath(__file__))
        source = None
        for up in (here, os.path.join(here, "..", ".."), os.path.join(here, "..", "..", "..")):
            candidate = os.path.join(up, "src", "Mod", "Start", "Gui", "StartView.cpp")
            alt = os.path.join(up, "Mod", "Start", "Gui", "StartView.cpp")
            for path in (candidate, alt):
                if os.path.isfile(path):
                    source = path
        if source is None:
            self.skipTest("StartView.cpp not found (installed build)")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('setObjectName(QLatin1String("StartView"))', text)
        self.assertIn("_contents->setCurrentIndex(1)", text)  # documents page is page 1
        self.assertIn('"ShowOnStartup"', text)


class VaultFolderTests(unittest.TestCase):
    def test_folder_from_ref_file_or_root(self):
        self.assertEqual(start.vault_folder("/v/Vector/Vector.bwvault"), "/v/Vector")
        self.assertEqual(start.vault_folder("/v/Vector"), "/v/Vector")
        self.assertEqual(start.vault_folder("/elsewhere/x.bwvault", "/v/Vector"), "/v/Vector")
        self.assertEqual(start.vault_folder(""), "")

    def test_in_folder(self):
        self.assertTrue(start.in_folder("/v/Vector/work/me/VC-01-01001-001.FCStd", "/v/Vector"))
        self.assertFalse(start.in_folder("/v/VectorOld/a.FCStd", "/v/Vector"))
        self.assertFalse(start.in_folder("/home/a.FCStd", "/v/Vector"))
        self.assertFalse(start.in_folder("/v/Vector/a.FCStd", ""))


class DashboardTests(unittest.TestCase):
    """PDM-10: the Start page's two tabs and the dashboard's text (bruceworx_dashboard)."""

    def test_default_tab(self):
        import bruceworx_dashboard as dash

        self.assertEqual(dash.default_tab(True), "dashboard")
        self.assertEqual(dash.default_tab(False), "recent")
        self.assertEqual(dash.default_tab(True, "recent"), "recent")
        self.assertEqual(dash.default_tab(False, "dashboard"), "dashboard")
        self.assertEqual(dash.default_tab(True, "files"), "files")
        self.assertEqual(dash.default_tab(True, "structure"), "structure")
        self.assertEqual(dash.default_tab(False, "files"), "recent")  # no vault: no file list

    def test_tile_query(self):
        import bruceworx_dashboard as dash

        self.assertEqual(dash.tile_query("part"), "type:part")
        self.assertEqual(dash.tile_query("drawing", "In Review"), "type:drawing state:review")

    def test_header_line(self):
        import bruceworx_dashboard as dash

        snap = {"storage": "iCloud Drive", "actor": "bruce@mac", "settings": {"units": "mm"},
                "health": {"syncing": 0}}
        self.assertEqual(dash.header_line(snap, 75), ("ok", "Up to date \u00b7 read 1 min ago \u00b7 iCloud Drive "
                                                            "\u00b7 you are bruce@mac \u00b7 units mm"))
        snap["health"] = {"syncing": 2, "sync_age": 900}
        level, text = dash.header_line(snap, 5)
        self.assertEqual(level, "warn")
        self.assertTrue(text.startswith("2 versions still syncing \u00b7 read just now"))
        now = dt.datetime.now().astimezone()
        empty = {"empty": True, "created": now.isoformat(), "storage": "iCloud Drive", "actor": "a@b",
                 "settings": {"units": "in"}, "health": {}}
        self.assertTrue(dash.header_line(empty, None, now)[1].startswith("New vault, created today"))

    def test_legend_and_projects(self):
        import bruceworx_dashboard as dash

        self.assertEqual(dash.legend({"WIP": 3, "In Review": 0, "Released": 2, "Obsolete": 1}),
                         [("WIP", "WIP", 3), ("Released", "Released", 2), ("Obsolete", "Obsolete", 1)])
        row = {"part_number": "DM-02-01001-001", "items": 18, "drawings": 13,
               "states": {"Released": 8, "In Review": 2}, "ecos": []}
        self.assertEqual(dash.project_text(row), ("8 of 18 released", "2 in review"))
        self.assertEqual(dash.project_sub(row), "DM-02-01001-001 \u00b7 18 items \u00b7 13 drawings")
        row["ecos"] = ["ECO-0003"]
        self.assertEqual(dash.project_text(row)[1], "ECO-0003 open")

    def test_get_started_and_check(self):
        import bruceworx_dashboard as dash

        snap = {"settings": {"programs": ["DM", "LE"], "example": "DM-01-01001-001", "units": "mm", "sheet": "B"}}
        steps = dash.get_started(snap, False)
        self.assertEqual([s[0] for s in steps], ["settings", "project", "import", "part"])
        self.assertEqual(steps[0][2], "Programs DM, LE \u00b7 numbering DM-01-01001-001 \u00b7 units mm \u00b7 ANSI B drawings")
        self.assertFalse(steps[1][5])  # New Project waits for PDM-09
        snap = {"health": {"stale_locks": [{"pn": "P", "who": "alex", "age": "20 d"}], "syncing": 0,
                           "problems": ["x"], "future_events": [], "conflict_copies": ["a 2.jsonl"]}}
        found = dash.health_problems(snap)
        self.assertEqual([(k, f) for k, _t, f, _p in found], [("lock", "break_lock"), ("journal", ""), ("copy", "")])


class VaultQueryWordTests(unittest.TestCase):
    """PDM-10: the Vault panel's search words the dashboard opens it with."""

    def test_words(self):
        import bruceworx_vaultclient as vc

        part = {"part_number": "DM-01-01001-001", "display": "DM-01-01001-001-01", "description": "Side rail",
                "item_type": "part", "state": "WIP", "code": "checked_out"}
        drawing = dict(part, part_number="DM-03-01001-001", item_type="drawing", state="In Review", code="up_to_date")
        other = dict(part, part_number="DM-01-01002-001", code="checked_out_other")
        change = {"part_number": "ECO-0003", "display": "ECO-0003", "description": "Jaw insert", "item_type": "change",
                  "state": "Open", "kind": "ECO", "code": "change"}
        rows = [part, drawing, other, change]

        def hits(kind, query):
            return [r["part_number"] for r in rows if vc.matches(r, kind, query)]

        self.assertEqual(hits("all", "type:part"), ["DM-01-01001-001", "DM-01-01002-001"])
        self.assertEqual(hits("all", "type:drawing state:review"), ["DM-03-01001-001"])
        self.assertEqual(hits("all", "lock:me"), ["DM-01-01001-001"])
        self.assertEqual(hits("all", "lock:other"), ["DM-01-01002-001"])
        self.assertEqual(hits("all", "lock:any rail"), ["DM-01-01001-001", "DM-01-01002-001"])
        self.assertEqual(hits("changes", "change:eco change:open"), ["ECO-0003"])
        self.assertEqual(hits("all", "rail state:wip"), ["DM-01-01001-001", "DM-01-01002-001"])
        self.assertEqual(hits("all", "http://x"), [])  # an unknown key is plain text


if __name__ == "__main__":
    unittest.main()
