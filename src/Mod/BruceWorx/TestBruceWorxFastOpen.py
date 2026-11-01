# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fast Open (PRF-03): the components of an assembly from preview meshes, and when to open fast."""

import unittest

import bruceworx_fastopen as fo
import bruceworx_previewmesh as pm

BOLT = {"name": "bolt", "color": (0.5, 0.5, 0.5), "points": [0, 0, 0, 1, 0, 0, 0, 1, 0], "triangles": [0, 1, 2]}
PLATE = {"name": "plate", "color": (0.2, 0.2, 0.2), "points": [0, 0, 0, 5, 0, 0, 0, 5, 0], "triangles": [0, 1, 2]}


def moved(x):
    m = list(pm.IDENTITY)
    m[3] = x
    return m


def library():
    """TOP places SUB twice and a bolt; SUB is a plate and two bolts."""
    meshes = {
        "BOLT": pm.encode([BOLT]),
        "SUB": pm.encode([PLATE], [{"file": "BOLT.FCStd", "object": "Part", "matrix": moved(1)},
                                   {"file": "BOLT.FCStd", "object": "Part", "matrix": moved(2)}]),
        "TOP": pm.encode([], [{"file": "SUB.FCStd", "object": "Assembly", "matrix": moved(10)},
                              {"file": "SUB.FCStd", "object": "Assembly", "matrix": moved(20)},
                              {"file": "BOLT.FCStd", "object": "Part", "matrix": moved(30)}]),
    }
    return lambda key: pm.decode(meshes[fo._key(key)]) if fo._key(key) in meshes else None


class TestPlan(unittest.TestCase):
    def test_one_component_per_instance_under_the_assembly(self):
        comps = fo.plan(library(), "TOP")
        self.assertEqual([c["pn"] for c in comps], ["SUB", "SUB", "BOLT"])
        self.assertEqual([len(c["draws"]) for c in comps], [3, 3, 1])  # a sub-assembly brings its whole tree
        self.assertEqual(fo.part_count(comps), 7)

    def test_placements_follow_the_levels(self):
        comps = fo.plan(library(), "TOP")
        xs = sorted(m[3] for _part, m in comps[0]["draws"])
        self.assertEqual(xs, [10, 11, 12])  # the plate at 10, its bolts at 10+1 and 10+2

    def test_a_missing_mesh_gives_nothing(self):
        self.assertEqual(fo.plan(library(), "NOPE"), [])

    def test_bruces_rule_more_than_the_limit_opens_fast(self):
        self.assertTrue(fo.should_fast_open(101, 100))
        self.assertFalse(fo.should_fast_open(100, 100))
        self.assertFalse(fo.should_fast_open(5000, 0))  # 0: never

    def test_coin_matrix_is_column_major(self):
        m = list(range(16))
        self.assertEqual(fo._coin_matrix(m), [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15])


if __name__ == "__main__":
    unittest.main()
