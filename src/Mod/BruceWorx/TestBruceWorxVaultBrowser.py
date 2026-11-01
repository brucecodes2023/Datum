# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the Files tab as a file browser (bruceworx_vaultbrowser, PDM-13): scopes, navigation,
the Used in and Drawing cells, history lines, and the list cut by type."""

import unittest
from types import SimpleNamespace

import bruceworx_vaultbrowser as vb
import bruceworx_vaultclient as vc


def turbojet():
    """The VL-P01 turbojet, cut down: the test stand uses the engine; the engine and a rotor
    assembly use the impeller; the impeller has a drawing; a glow plug is bought in."""
    items = {
        "VL-02-01001-001": {"type": "assembly", "description": "VL-P01-100 Engine assembly", "obsolete": False},
        "VL-02-01002-001": {"type": "assembly", "description": "VL-P01-190 Test stand assembly", "obsolete": False},
        "VL-02-01003-001": {"type": "assembly", "description": "VL-P01-105 Rotor assembly", "obsolete": False},
        "VL-01-01001-001": {"type": "part", "description": "VL-P01-110 Impeller", "obsolete": False},
        "VL-01-01022-001": {"type": "part", "description": "VL-P01-136 Glow plug (COTS)", "obsolete": False},
        "VL-03-01001-001": {"type": "drawing", "description": "VL-P01-110-DRW Impeller drawing", "obsolete": False},
        "LE-02-01001-001": {"type": "assembly", "description": "NEMA 17 arm", "obsolete": False},
    }
    structure = {
        "items": items,
        "bom": {"VL-02-01002-001": [["VL-02-01001-001", 1]],
                "VL-02-01001-001": [["VL-01-01001-001", 1], ["VL-01-01022-001", 1]],
                "VL-02-01003-001": [["VL-01-01001-001", 1]]},
        "drawings": {"VL-01-01001-001": ["VL-03-01001-001"]},
        "sources": {},
    }
    rows = {pn: {"part_number": pn, "item_type": info["type"], "description": info["description"],
                 "state": "WIP", "code": "up_to_date", "category": ""} for pn, info in items.items()}
    rows["VL-01-01022-001"]["code"] = "checked_out"
    rows["ECR-0001"] = {"part_number": "ECR-0001", "item_type": "change", "description": "an ECR"}
    return structure, rows


class TestScope(unittest.TestCase):
    def setUp(self):
        self.s, self.rows = turbojet()

    def scoped(self, scope):
        return sorted(pn for pn, r in self.rows.items() if vb.in_scope(scope, pn, r, self.s))

    def test_everything_and_changes(self):
        self.assertEqual(len(self.scoped(vb.ALL)), 8)
        self.assertNotIn("ECR-0001", self.scoped("program:VL"))

    def test_program(self):
        self.assertEqual(self.scoped("program:LE"), ["LE-02-01001-001"])
        self.assertEqual(len(self.scoped("program:VL")), 6)

    def test_an_assembly_and_everything_it_uses(self):
        self.assertEqual(self.scoped("asm:VL-02-01002-001"),
                         ["VL-01-01001-001", "VL-01-01022-001", "VL-02-01001-001", "VL-02-01002-001", "VL-03-01001-001"])

    def test_views(self):
        self.assertEqual(self.scoped("view:drawings"), ["VL-03-01001-001"])
        self.assertEqual(self.scoped("view:checked_out"), ["VL-01-01022-001"])
        # Missing a drawing: the assemblies count (1 Nov), the bought-in glow plug does not.
        self.assertEqual(self.scoped("view:missing"), ["LE-02-01001-001", "VL-02-01001-001", "VL-02-01002-001",
                                                       "VL-02-01003-001"])


class TestNavigation(unittest.TestCase):
    def test_entries(self):
        s, rows = turbojet()
        entries = vb.nav_entries(s, rows, {"VL": "Vector Labs propulsion"})
        keys = [e[0] for e in entries]
        self.assertEqual(entries[0][4], "section")
        self.assertLess(keys.index("program:LE"), keys.index("program:VL"))
        vl = keys.index("program:VL")
        self.assertEqual(entries[vl][1], "VL · Vector Labs propulsion")
        self.assertEqual(keys[vl + 1:vl + 4], ["asm:VL-02-01001-001", "asm:VL-02-01002-001", "asm:VL-02-01003-001"])
        self.assertEqual(entries[keys.index("asm:VL-02-01002-001")][2], 5)  # the stand and what it uses
        self.assertEqual(entries[keys.index(vb.ALL)][2], 7)  # files only: the ECR is not one
        self.assertNotIn("Categories", [e[1] for e in entries])  # no categories in this vault

    def test_breadcrumb(self):
        _s, rows = turbojet()
        self.assertEqual(vb.scope_title("asm:VL-02-01001-001", rows, {}),
                         ["VL", "VL-P01-100 Engine assembly and what it uses"])
        self.assertEqual(vb.scope_title("view:missing", rows, {}), ["Missing a drawing"])
        self.assertEqual(vb.scope_title(vb.ALL, rows, {}), ["Everything"])


class TestCells(unittest.TestCase):
    def test_used_in_counts_assemblies_not_quantities(self):
        self.assertEqual(vb.used_in_text([("A", 6), ("B", 1)]), "2 asm")
        self.assertEqual(vb.used_in_text([]), "")

    def test_drawing_cell(self):
        s, rows = turbojet()
        self.assertEqual(vb.drawing_cell(s, "VL-01-01001-001", rows["VL-01-01001-001"]), ("Yes", "ok"))
        self.assertEqual(vb.drawing_cell(s, "VL-01-01022-001", rows["VL-01-01022-001"]), ("COTS", "secondary"))
        self.assertEqual(vb.drawing_cell(s, "VL-02-01001-001", rows["VL-02-01001-001"]), ("None", "warn"))
        self.assertEqual(vb.drawing_cell(s, "VL-03-01001-001", rows["VL-03-01001-001"]), ("", "secondary"))

    def test_history_newest_first_with_import_paths_shortened(self):
        versions = [SimpleNamespace(comment="Imported from /tmp/x/VL-P01-100.FCStd", at="2026-10-31T03:03:52+00:00",
                                    size=7168),
                    SimpleNamespace(comment="Rev E: stator parts", at="2026-10-31T04:09:58+00:00", size=12288)]
        rows = vb.history_rows(versions)
        self.assertEqual(rows[0], ("v2", "Rev E: stator parts", "2026-10-31 04:09", "12 kB"))
        self.assertEqual(rows[1][1], "Imported from VL-P01-100.FCStd")

    def test_coin_matrix_is_the_transpose(self):
        m = list(range(16))
        self.assertEqual(vb.coin_matrix(m), [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15])


class TestGroupByType(unittest.TestCase):
    def test_assemblies_parts_drawings_then_the_rest(self):
        _s, rows = turbojet()
        nodes = [{"pn": pn, "qty": None, "children": []} for pn in sorted(rows)]
        groups = vc.group_by_type(nodes, rows)
        self.assertEqual([g.get("kind") for g in groups], ["assembly", "part", "drawing", None])
        self.assertEqual(groups[0]["group"], "Assemblies  ·  4")
        self.assertEqual([n["pn"] for n in groups[1]["children"]], ["VL-01-01001-001", "VL-01-01022-001"])
        self.assertEqual(groups[-1]["pn"], "ECR-0001")
        self.assertEqual(vc.group_by_type(nodes, rows, "cat:X/")[0]["key"], "cat:X/type:assembly")

    def test_empty_groups_are_left_out(self):
        _s, rows = turbojet()
        only = [{"pn": "VL-03-01001-001", "qty": None, "children": []}]
        self.assertEqual([g["kind"] for g in vc.group_by_type(only, rows)], ["drawing"])


if __name__ == "__main__":
    unittest.main()
