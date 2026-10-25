# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Structure tab's trees (bruceworx/vault_trees.py): drawings, product, where used."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bruceworx import vault_trees as vt  # noqa: E402


def item(kind, description="", drawing_of=None, state="WIP", rev="01"):
    return SimpleNamespace(item_type=kind, description=description, drawing_of=drawing_of, state=state,
                           latest=SimpleNamespace(code=rev), obsolete=False)


def gearbox(bom=None, links=None):
    """The Leonardo gearbox, cut down: an assembly of a housing, a sun and three planets; the housing
    has a drawing. ``bom``: recorded lines; else ``links`` stand in for the assembly file."""
    items = {
        "LE-02-01002-001": item("assembly", "NEMA 17 planetary gearbox 5:1"),
        "LE-01-01003-001": item("part", "Housing"),
        "LE-01-01009-001": item("part", "Sun gear"),
        "LE-01-01008-001": item("part", "Planet gear"),
        "LE-03-01003-001": item("drawing", "Housing drawing", drawing_of="LE-01-01003-001"),
    }
    vault = SimpleNamespace(items=items, bom=bom or {})
    structure = vt.structure(vault, links=lambda pn: (links or {}).get(pn, {}))
    return vault, structure


class TestStructure(unittest.TestCase):
    def test_lines_come_from_the_file_links_without_a_recorded_bom(self):
        _v, s = gearbox(links={"LE-02-01002-001": {"LE-01-01003-001": 1, "LE-01-01009-001": 1,
                                                   "LE-01-01008-001": 3, "XX-99-99999-999": 2}})
        self.assertEqual(s["sources"], {"LE-02-01002-001": "links"})
        # a file that is not a vault item is not a line
        self.assertEqual(dict(map(tuple, s["bom"]["LE-02-01002-001"])),
                         {"LE-01-01003-001": 1, "LE-01-01008-001": 3, "LE-01-01009-001": 1})

    def test_a_recorded_bom_wins(self):
        _v, s = gearbox(bom={"LE-02-01002-001": [("LE-01-01003-001", 1)]},
                        links={"LE-02-01002-001": {"LE-01-01008-001": 3}})
        self.assertEqual(s["sources"], {"LE-02-01002-001": "bom"})
        self.assertEqual(s["bom"]["LE-02-01002-001"], [["LE-01-01003-001", 1]])


class TestTrees(unittest.TestCase):
    def setUp(self):
        _v, self.s = gearbox(links={"LE-02-01002-001": {"LE-01-01003-001": 1, "LE-01-01009-001": 1,
                                                        "LE-01-01008-001": 3}})

    def test_product_tree(self):
        tree = vt.product_tree(self.s)
        self.assertEqual([t["pn"] for t in tree], ["LE-02-01002-001"])
        kids = {c["pn"]: c["qty"] for c in tree[0]["children"]}
        self.assertEqual(kids, {"LE-01-01003-001": 1, "LE-01-01008-001": 3, "LE-01-01009-001": 1})
        housing = next(c for c in tree[0]["children"] if c["pn"] == "LE-01-01003-001")
        self.assertEqual(housing["drawings"], ["LE-03-01003-001"])

    def test_flattened_multiplies_down_the_levels(self):
        s = dict(self.s)
        s["bom"] = {"TOP": [["SUB", 2]], "SUB": [["P", 3], ["Q", 1]]}
        self.assertEqual(vt.flattened(s, "TOP"), {"SUB": 2, "P": 6, "Q": 2})

    def test_drawing_tree(self):
        t = vt.drawing_tree(self.s)
        self.assertEqual([n["pn"] for n in t["documented"]], ["LE-01-01003-001"])
        self.assertEqual([c["pn"] for c in t["documented"][0]["children"]], ["LE-03-01003-001"])
        self.assertEqual({n["pn"] for n in t["undrawn"]}, {"LE-01-01008-001", "LE-01-01009-001", "LE-02-01002-001"})
        self.assertEqual(t["orphans"], [])

    def test_where_used(self):
        up = vt.where_used(self.s, "LE-01-01008-001")
        self.assertEqual([(n["pn"], n["qty"]) for n in up], [("LE-02-01002-001", 3)])
        self.assertEqual(vt.where_used(self.s, "LE-02-01002-001"), [])

    def test_a_cycle_is_cut(self):
        s = dict(self.s)
        s["bom"] = {"A": [["B", 1]], "B": [["A", 1]]}
        s["items"] = {"A": {"display": "A", "description": "", "type": "assembly", "state": "WIP", "revision": ""},
                      "B": {"display": "B", "description": "", "type": "assembly", "state": "WIP", "revision": ""}}
        self.assertEqual(vt.tops(s), [])  # each uses the other: neither is a top
        self.assertTrue(vt.where_used(s, "A")[0]["children"][0].get("cycle"))


class TestFileLinks(unittest.TestCase):
    def test_links_and_arrays_are_counted(self):
        tmp = tempfile.mkdtemp(prefix="bw-trees-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "LE-02-01002-001.FCStd")

        def link(name, target, count=None):
            extra = (f'<Property name="ElementCount" type="App::PropertyInteger"><Integer value="{count}"/></Property>'
                     if count else "")
            return (f'<Object name="{name}"><Properties Count="2">'
                    f'<Property name="LinkedObject" type="App::PropertyXLink"><XLink file="{target}" name="Part"/>'
                    f'</Property>{extra}</Properties></Object>')

        objects = [("Planet1", "App::Link"), ("Planet2", "App::Link"), ("Pins", "App::Link"), ("Body", "PartDesign::Body")]
        xml = ('<Document><Objects Count="4">' + "".join(f'<Object type="{t}" name="{n}"/>' for n, t in objects)
               + '</Objects><ObjectData Count="4">'
               + link("Planet1", "LE-01-01008-001.FCStd") + link("Planet2", "LE-01-01008-001.FCStd")
               + link("Pins", "LE-01-01012-001.FCStd", count=3)
               + '<Object name="Body"><Properties Count="0"/></Object></ObjectData></Document>')
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Document.xml", xml)
        self.assertEqual(vt.file_links(path), {"LE-01-01008-001": 2, "LE-01-01012-001": 3})


if __name__ == "__main__":
    unittest.main()
