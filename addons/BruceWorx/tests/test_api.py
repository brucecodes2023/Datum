# SPDX-License-Identifier: LGPL-2.1-or-later
"""API tree summary and argument checks. Geometry runs in freecad_api_smoke.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx.api import create_padded_body, tree_summary  # noqa: E402
from fakes import FakeDoc, FakeObj  # noqa: E402


class TreeSummaryTests(unittest.TestCase):
    def test_name_type_children(self):
        pad = FakeObj("Pad", "Pad", "PartDesign::Pad")
        body = FakeObj("Body", "Body", "PartDesign::Body", children=[pad])
        doc = FakeDoc("Part", "Bracket", [body, pad])
        self.assertEqual(
            tree_summary(doc),
            [
                {
                    "name": "Body",
                    "type": "PartDesign::Body",
                    "children": [
                        {"name": "Pad", "type": "PartDesign::Pad", "children": []},
                    ],
                }
            ],
        )

    def test_empty_document(self):
        self.assertEqual(tree_summary(FakeDoc("Empty", "Empty", [])), [])


class CreateArgsTests(unittest.TestCase):
    def test_size_and_name_must_be_usable(self):
        doc = FakeDoc("D", "D", [])
        with self.assertRaises(ValueError):
            create_padded_body(doc, length=0)
        with self.assertRaises(ValueError):
            create_padded_body(doc, width=-1)
        with self.assertRaises(ValueError):
            create_padded_body(doc, height=float("nan"))
        with self.assertRaises(ValueError):
            create_padded_body(doc, name="  ")


if __name__ == "__main__":
    unittest.main()
