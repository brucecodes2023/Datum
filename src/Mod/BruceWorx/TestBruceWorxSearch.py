# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure-logic tests for Cmd-K search (bruceworx_search): ranking and the settings index."""

import unittest

import bruceworx_search as search


class TestScore(unittest.TestCase):
    def test_order_of_match_kinds(self):
        whole = search.score("hole", "Hole")
        start = search.score("hol", "Hole")
        word = search.score("pat", "Circular Pattern")
        inside = search.score("tter", "Circular Pattern")
        letters = search.score("cpat", "Circular Pattern")
        self.assertTrue(whole > start > word > inside > letters > 0)

    def test_tokens_as_word_starts(self):
        self.assertEqual(search.score("circ pat", "Circular Pattern"), 70)

    def test_no_match(self):
        self.assertEqual(search.score("xyz", "Pad"), 0)
        self.assertEqual(search.score("", "Pad"), 0)
        self.assertEqual(search.score("pad", ""), 0)

    def test_secondary_text_counts_half(self):
        self.assertEqual(search.best("fillet", "Round edges", ("Fillet",)), 50)

    def test_secondary_text_needs_a_real_hit(self):
        self.assertEqual(search.best("hole", "Toggle", ("Shows the whole history timeline",)), 30)
        self.assertEqual(search.best("hle", "Toggle", ("Shows the history timeline",)), 0)


class TestRank(unittest.TestCase):
    ITEMS = [
        ("cmd:PartDesign_Hole", "Hole", ("PartDesign_Hole", "Creates a hole")),
        ("cmd:PartDesign_Fillet", "Fillet", ("PartDesign_Fillet", "")),
        ("cmd:Mesh_FillupHoles", "Fill Holes", ("Mesh_FillupHoles", "")),
    ]

    def test_best_first(self):
        keys = [k for _s, k in search.rank("hole", self.ITEMS)]
        self.assertEqual(keys[0], "cmd:PartDesign_Hole")
        self.assertIn("cmd:Mesh_FillupHoles", keys)
        self.assertNotIn("cmd:PartDesign_Fillet", keys)

    def test_recent_breaks_ties_but_not_an_exact_match(self):
        keys = [k for _s, k in search.rank("fill", self.ITEMS)]
        self.assertEqual(keys[0], "cmd:PartDesign_Fillet")
        keys = [k for _s, k in search.rank("fill", self.ITEMS, recent=["cmd:Mesh_FillupHoles"])]
        self.assertEqual(keys[0], "cmd:Mesh_FillupHoles")
        keys = [k for _s, k in search.rank("hole", self.ITEMS, recent=["cmd:Mesh_FillupHoles"])]
        self.assertEqual(keys[0], "cmd:PartDesign_Hole")

    def test_trim_weak(self):
        ranked = [(90, "a"), (60, "b"), (55, "c"), (30, "d")]
        self.assertEqual(search.trim_weak(ranked), ranked[:3])
        self.assertEqual(search.trim_weak(ranked[1:]), ranked[1:])

    def test_push_recent(self):
        self.assertEqual(search.push_recent(["a", "b", "c"], "b"), ["b", "a", "c"])
        self.assertEqual(len(search.push_recent([str(i) for i in range(30)], "x")), search.RECENT_MAX)


class TestSettingsIndex(unittest.TestCase):
    def test_shipped_index(self):
        entries = search.load_settings()
        self.assertGreater(len(entries), 500)
        self.assertTrue(all({"text", "tip", "page", "area"} <= set(e) for e in entries))
        texts = {e["text"] for e in entries}
        self.assertIn("Default profile type for holes", texts)

    def test_missing_file(self):
        self.assertEqual(search.load_settings("/no/such/file.json"), [])

    def test_place_text(self):
        self.assertEqual(search.place_text(("Design", "Solid", "Create")), "Design › Solid › Create")
        self.assertEqual(search.place_text(None), "")


if __name__ == "__main__":
    unittest.main()
