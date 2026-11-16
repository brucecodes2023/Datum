# SPDX-License-Identifier: LGPL-2.1-or-later
"""The project pages (BW-PDM-15): cards, counts, structure, attention, recent files, changes, history."""

import datetime as dt
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from heuretes_freecad import project_pages as pp  # noqa: E402
from heuretes_freecad import vault_dashboard as vd  # noqa: E402
from test_vault_dashboard import BOLT, DWG, ME, NOW, PART, SUB, TOP, _Vault  # noqa: E402


class ProjectPages(_Vault):
    def build(self, **kw):
        return pp.build(self.vault, NOW, ME, **kw)

    def test_card_and_page_counts(self):
        self.structure()
        data = self.build()
        self.assertEqual([t["code"] for t in data["tabs"]], ["DM"])
        card, page = data["cards"][0], data["pages"]["DM"]
        self.assertEqual((card["parts"], card["assemblies"], card["drawings"]), (1 + 2, 2, 1))
        counts = {c["key"]: c for c in page["counts"]}
        self.assertEqual(counts["parts"]["value"], 3)
        self.assertIn("1 made", counts["parts"]["note"])
        self.assertIn("2 bought", counts["parts"]["note"])
        # TOP and SUB have no drawing; PART has one.
        self.assertEqual(counts["missing"]["value"], 2)
        self.assertEqual(card["missing"], 2)
        self.assertEqual(page["tab_counts"]["files"], 6)
        self.assertEqual(page["tab_counts"]["drawings"], 1)

    def test_structure_is_a_tree_with_pictures_and_drawing_status(self):
        self.structure()
        self.checkin(TOP)
        page = self.build()["pages"]["DM"]
        rows = page["structure"]
        self.assertEqual([(r["pn"], r["depth"]) for r in rows], [(TOP, 0), (SUB, 1)])
        self.assertEqual(rows[0]["drawing"], "No drawing")
        self.assertEqual(rows[0]["drawing_level"], "warn")
        self.assertTrue(rows[0]["picture"]["sha"])
        self.assertIsNone(rows[1]["picture"])  # never checked in

    def test_needs_attention(self):
        self.structure()
        page = self.build()["pages"]["DM"]
        keys = [a["key"] for a in page["attention"]]
        self.assertIn("missing", keys)
        self.assertNotIn("nothing_released", keys)  # PART is released
        self.vault.retire(PART, reason="mis-typed import, do not use")
        page = self.build()["pages"]["DM"]
        void = [a for a in page["attention"] if a["key"] == "voided"]
        self.assertTrue(void and "mis-typed import" in void[0]["detail"])

    def test_nothing_released_suggests_the_smallest_assembly(self):
        self.structure()
        v = self.vault
        s = pp.structure_of(v)
        # PART is released in the fixture; fake an all-WIP vault by hiding the released state.
        for info in s["items"].values():
            if info["state"] == "Released":
                info["state"] = "WIP"
        page = pp.build(v, NOW, ME, structure=s)["pages"]["DM"]
        row = next(a for a in page["attention"] if a["key"] == "nothing_released")
        self.assertIn("Submit", row["detail"])
        self.assertIn(SUB[-9:], row["detail"])  # SUB is smaller than TOP

    def test_recent_files_newest_first(self):
        self.structure()
        data = self.build()
        recent = data["pages"]["DM"]["recent"]
        self.assertTrue(recent)
        self.assertEqual(len({r["pn"] for r in recent}), len(recent))
        ats = [r["at"] for r in recent]
        self.assertEqual(ats, sorted(ats, reverse=True))
        self.assertTrue(data["cards"][0]["last"])

    def test_history_is_this_projects_only(self):
        self.structure()
        data = self.build()
        lines = [l for g in data["pages"]["DM"]["history"]["groups"] for l in g["rows"]]
        self.assertTrue(lines)
        self.assertTrue(all(pp.vt.program_code(l["target"]) in ("DM", "") for l in lines))

    def test_empty_project_card(self):
        self.structure()
        data = {"first_parent": 1001, "programs": {"OW": {"description": "Overwatch", "counters": {}}}}
        with open(os.path.join(self.vault.root, "numbering.json"), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        out = self.build()
        card = next(c for c in out["cards"] if c["code"] == "OW")
        self.assertTrue(card["empty"])
        self.assertEqual(card["first_number"], "OW-01-01001")
        self.assertEqual(out["pages"]["OW"]["structure"], [])
        self.assertEqual(out["pages"]["OW"]["tab_counts"]["files"], 0)

    def test_no_simulations_when_the_vault_has_none(self):
        self.structure()
        page = self.build()["pages"]["DM"]
        self.assertEqual(page["simulations"], [])
        self.assertEqual(page["tab_counts"]["simulations"], 0)

    def test_simulations_from_a_fake_api_and_out_of_date_attention(self):
        self.structure()
        rows = [{"pn": "DM-04-00001-001", "description": "Plate stress", "state": "WIP", "of": PART, "out_of_date": True},
                {"pn": "DM-04-00002-001", "description": "Frame modes", "state": "WIP", "of": SUB, "changed": False}]
        seen = []
        data = self.build(simulations=lambda s, scope=None: seen.append(scope) or rows)
        page = data["pages"]["DM"]
        self.assertEqual(seen, [{PART, SUB, TOP, BOLT, DWG, "DM-05-01002-001"}])
        self.assertEqual([r["description"] for r in page["simulations"]], ["Plate stress", "Frame modes"])
        self.assertEqual(page["tab_counts"]["simulations"], 2)
        old = [a for a in page["attention"] if a["key"] == "sims_old"]
        self.assertEqual(len(old), 1)
        self.assertIn("Plate stress", old[0]["detail"])
        self.assertIn("1 simulation out of date", old[0]["title"])

    def test_a_failing_simulations_api_shows_none(self):
        self.structure()

        def boom(s, scope=None):
            raise RuntimeError("api changed")

        page = self.build(simulations=boom)["pages"]["DM"]
        self.assertEqual(page["simulations"], [])

    def test_changes_touching_the_project(self):
        self.structure()
        self.vault.raise_ecr("ECR-0001", "Bolt too short", [PART])
        page = self.build()["pages"]["DM"]
        self.assertEqual([c["number"] for c in page["changes"]], ["ECR-0001"])
        self.assertEqual(page["tab_counts"]["changes"], 1)

    def test_snapshot_carries_the_pages_and_tags_the_feed(self):
        self.structure()
        snap = vd.snapshot(self.vault, None, now=NOW, days=3650, limit=50)
        self.assertIn("project_pages", snap)
        self.assertNotIn("project_pages_error", snap)
        lines = [l for g in snap["activity"]["groups"] for l in g["rows"]]
        self.assertTrue(any(l.get("project") == "DM" for l in lines))

    def test_tab_labels(self):
        self.assertEqual(pp.tab_label("VL", "Heuretes FreeCAD - Propulsion"), "VL · Propulsion")
        self.assertEqual(pp.tab_label("OW", "Overwatch"), "OW · Overwatch")
        self.assertEqual(pp.split_name("", "ZZ"), ("ZZ", ""))

    def test_speed_on_a_large_vault(self):
        import tempfile
        import shutil
        import vault_fixture

        tmp = tempfile.mkdtemp(prefix="bw-pp-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        v = vault_fixture.make_vault(os.path.join(tmp, "Big"), 400)
        started = time.perf_counter()
        data = pp.build(v, dt.datetime.now().astimezone(), v.actor)
        took = time.perf_counter() - started
        self.assertTrue(data["cards"])
        self.assertLess(took, 2.0)


if __name__ == "__main__":
    unittest.main()
