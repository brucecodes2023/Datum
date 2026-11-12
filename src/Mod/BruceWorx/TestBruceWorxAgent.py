# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the agent design interface (roadmap AUT-06): bruceworx_agent and its four bridge tools.

    FreeCADCmd -t TestBruceWorxAgent
"""

import base64
import io
import unittest

import FreeCAD as App
import Part

import bruceworx_agent as ba
import bruceworx_bridge as bb
import bruceworx_bridge_tools as tools


def run_tool(tool, **args):
    return tools.call(tool, bb.validate(tool, args), headless=True)


def pixels(png):
    import numpy as np
    from PIL import Image

    return np.array(Image.open(io.BytesIO(png)).convert("RGB"))


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("AgentTest")
        self.doc.UndoMode = 1
        # a 60 × 40 × 6 plate with four ⌀3.4 holes, and a ⌀3 pin 20 long through one of them
        plate = Part.makeBox(60, 40, 6, App.Vector(-30, -20, 0))
        self.holes = [(sx * 22, sy * 12) for sx in (-1, 1) for sy in (-1, 1)]
        for x, y in self.holes:
            plate = plate.cut(Part.makeCylinder(1.7, 6, App.Vector(x, y, 0)))
        self.plate = self.doc.addObject("Part::Feature", "Plate")
        self.plate.Shape = plate
        self.pin = self.doc.addObject("Part::Feature", "Pin")
        self.pin.Shape = Part.makeCylinder(1.5, 20, App.Vector(22, 12, -7))
        self.doc.recompute()

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class TestParts(Base):
    def test_parts_and_hidden(self):
        self.assertEqual([i.label for i in ba.parts(self.doc)], ["Plate", "Pin"])
        self.pin.Visibility = False
        self.assertEqual([i.label for i in ba.parts(self.doc)], ["Plate"])
        self.assertEqual([i.hidden for i in ba.parts(self.doc, include_hidden=True)], [False, True])

    def test_find_by_any_name(self):
        items = ba.parts(self.doc)
        self.assertIs(ba.find(items, "plate").obj, self.plate)
        with self.assertRaises(ba.AgentError) as ctx:
            ba.find(items, "Lid")
        self.assertIn("Plate, Pin", str(ctx.exception))

    def test_part_containers_are_opened_and_place_their_parts(self):
        holder = self.doc.addObject("App::Part", "Holder")
        holder.addObject(self.pin)
        holder.Placement.Base = App.Vector(0, 0, 50)
        self.doc.recompute()
        item = ba.find(ba.parts(self.doc), "Holder / Pin")
        self.assertEqual(item.key, "Holder.Pin")
        self.assertAlmostEqual(item.shape.BoundBox.ZMin, 43, places=6)
        holder.Visibility = False
        self.assertEqual([i.label for i in ba.parts(self.doc)], ["Plate"])

    def test_folders_are_opened(self):
        folder = self.doc.addObject("App::DocumentObjectGroup", "Folder")
        folder.addObject(self.pin)
        self.assertEqual(sorted(i.label for i in ba.parts(self.doc)), ["Pin", "Plate"])


class TestSummary(Base):
    def test_summary_text(self):
        self.pin.Visibility = False
        out = run_tool("design_summary", doc=self.doc.Name)
        self.assertEqual((out["parts"], out["hidden"], out["errors"]), (1, 1, 0))
        text = out["text"]
        self.assertIn("Plate | plain solid, no parameters | 60×40×6 |", text)
        self.assertIn("hidden: Pin", text)
        self.assertLess(len(text), 400)

    def test_code_part_and_parameters(self):
        try:
            import bruceworx_codecad as cc
        except ImportError:
            self.skipTest("no code-CAD module")
        if cc.available() is not None:
            self.skipTest("build123d is not installed")
        cc.make(self.doc, 'from build123d import *\nresult = Box(param("ag_len", 30), 10, 5)\n', "Bar")
        self.doc.recompute()
        text = ba.summary(self.doc)["text"]
        self.assertIn("Bar | code part, 1 dimensions | 30×10×5 | 1500", text)
        self.assertIn("ag_len=30", text)


class TestRender(Base):
    def test_iso_png(self):
        out = ba.render(self.doc, width=400, height=300)
        img = pixels(out["png"])
        self.assertEqual(img.shape[1], 400)
        self.assertGreaterEqual(img.shape[0], 300)  # the legend is below
        drawn = (img[:300] != 255).any(axis=2).mean()
        self.assertGreater(drawn, 0.15)
        self.assertEqual([p["label"] for p in out["parts"]], ["Plate", "Pin"])
        self.assertTrue(out["views"][0].startswith("iso"))

    def test_top_view_is_to_scale_and_shows_the_holes(self):
        out = ba.render(self.doc, views=["top"], objects=["Plate"], width=600, height=400)
        self.assertEqual(out["views"], ["top  60 x 40 mm"])
        img = pixels(out["png"])[:400]
        scale = min(600 * 0.88 / 60, (400 - 2 * 0.06 * 400) / 40)

        def at(x, y):
            return img[int(round(200 - y * scale)), int(round(300 + x * scale))]

        self.assertFalse((at(0, 0) == 255).all())  # plate
        for x, y in self.holes:
            self.assertTrue((at(x, y) == 255).all(), (x, y))  # a through hole shows the background
        self.assertTrue((at(-29.5, 0) != 255).any())
        self.assertTrue((img[5, 300] == 255).all())

    def test_section_cuts_and_hatches(self):
        whole = pixels(ba.render(self.doc, views=["front"], width=400, height=300)["png"])
        out = ba.render(self.doc, section={"axis": "y", "at": 12}, width=400, height=300)
        self.assertIn("section y = 12", out["note"])
        self.assertTrue(out["views"][0].startswith("front"))
        cut = pixels(out["png"])
        self.assertFalse((whole == cut).all())
        # the cut plate is hatched: two shades of its colour alternate along a row through the cut face
        scale = 400 * 0.88 / 60
        row = cut[int(round(150 - (3 - 3) * scale)) - 2, 40:120]
        self.assertGreater(len({tuple(p) for p in row.tolist()}), 2)

    def test_several_views_in_one_image(self):
        out = ba.render(self.doc, views=["iso", "top", "front"], width=400, height=400)
        self.assertEqual(len(out["views"]), 3)
        self.assertEqual(pixels(out["png"]).shape[1], 400)

    def test_errors_are_plain(self):
        with self.assertRaises(ba.AgentError):
            ba.render(self.doc, views=["sideways"])
        with self.assertRaises(ba.AgentError) as ctx:
            ba.render(self.doc, section={"axis": "z", "at": 500, "keep": "+"})
        self.assertIn("leaves nothing", str(ctx.exception))

    def test_view_tool_returns_an_image(self):
        out = run_tool("view", doc=self.doc.Name, views=["top"], width=320, height=240)
        self.assertTrue(base64.b64decode(out["png_base64"]).startswith(b"\x89PNG"))
        self.assertNotIn("note", out)
        self.assertLess(len(out["png_base64"]), 40000)

    def test_png_without_pil(self):
        import numpy as np

        data = ba._png(np.zeros((4, 5, 3), dtype=np.uint8))
        self.assertEqual(pixels(data).shape, (4, 5, 3))


class TestFit(Base):
    def test_cylinders(self):
        found = ba.cylinders(self.plate.Shape)
        self.assertEqual(len(found), 4)
        self.assertTrue(all(c.hole and abs(c.diameter - 3.4) < 1e-9 and abs(c.length - 6) < 1e-9 for c in found))
        shaft = ba.cylinders(self.pin.Shape)
        self.assertEqual([(c.hole, round(c.diameter, 6), round(c.length, 6)) for c in shaft], [(False, 3.0, 20.0)])

    def test_rounded_corners_are_not_holes_or_shafts(self):
        box = self.doc.addObject("Part::Feature", "Soft")
        solid = Part.makeBox(20, 20, 5, App.Vector(100, 0, 0))
        box.Shape = solid.makeFillet(3, [e for e in solid.Edges if abs(e.Length - 5) < 1e-9])
        self.assertEqual(len([f for f in box.Shape.Faces if type(f.Surface).__name__ == "Cylinder"]), 4)
        self.assertEqual(ba.cylinders(box.Shape), [])

    def test_pin_in_hole_with_clearance(self):
        out = run_tool("fit_report", doc=self.doc.Name, first="Plate", second="Pin")
        self.assertEqual(out["kind"], "clearance")
        self.assertAlmostEqual(out["distance"], 0.2, places=3)
        self.assertEqual(len(out["pairs"]), 1)
        self.assertAlmostEqual(out["pairs"][0]["clearance"], 0.4, places=6)
        self.assertIn("0.4 mm clearance on diameter, 6 mm engaged of a 6 mm hole", out["text"])
        self.assertIn("holes in Plate with nothing in line in the other part: 3 × ⌀3.4", out["text"])

    def test_press_fit_is_a_clash_with_interference(self):
        self.pin.Shape = Part.makeCylinder(1.75, 20, App.Vector(22, 12, -7))
        out = ba.fit(self.doc, "Pin", "Plate")
        self.assertEqual(out["kind"], "clash")
        self.assertGreater(out["volume"], 0.1)
        self.assertIn("0.1 mm interference on diameter", out["text"])

    def test_far_apart(self):
        self.pin.Placement.Base = App.Vector(100, 0, 0)
        out = ba.fit(self.doc, "Plate", "Pin")
        self.assertEqual(out["kind"], "clear")
        self.assertGreater(out["distance"], 50)
        self.assertEqual(out["pairs"], [])

    def test_two_parts_needed(self):
        with self.assertRaises(tools.ToolError):
            run_tool("fit_report", doc=self.doc.Name, first="Plate", second="Plate")


class TestSetParameters(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("AgentParams")
        sheet = self.doc.addObject("Spreadsheet::Sheet", "Parameters")
        for row, (alias, value) in enumerate((("box_l", "40 mm"), ("box_w", "20 mm"), ("fillet_r", "2 mm")), 1):
            sheet.set(f"A{row}", alias)
            sheet.set(f"B{row}", value)
            sheet.setAlias(f"B{row}", alias)
        self.doc.recompute()
        box = self.doc.addObject("Part::Box", "Block")
        box.setExpression("Length", "Parameters.box_l")
        box.setExpression("Width", "Parameters.box_w")
        box.Height = 10
        rounded = self.doc.addObject("Part::Fillet", "Rounded")
        rounded.Base = box
        rounded.setExpression("Edges", None)
        rounded.Edges = [(1, 2.0, 2.0)]
        other = self.doc.addObject("Part::Box", "Other")
        other.Placement.Base = App.Vector(0, 100, 0)
        self.doc.recompute()
        self.sheet, self.box = sheet, box

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_batch_reports_what_changed(self):
        out = run_tool("set_parameters", doc=self.doc.Name, values={"box_l": "50 mm", "box_w": 25})
        self.assertEqual([p["alias"] for p in out["parameters"]], ["box_l", "box_w"])
        self.assertEqual(out["parameters"][0]["from"], "40.00 mm")
        self.assertEqual([c["part"] for c in out["changed"]], ["Rounded"])  # Other did not change
        self.assertEqual(out["changed"][0]["size"], [[40.0, 20.0, 10.0], [50.0, 25.0, 10.0]])
        self.assertAlmostEqual(self.box.Length.Value, 50)
        self.assertEqual(self.doc.UndoNames[0], "Claude: set_parameters")
        self.doc.undo()
        self.doc.recompute()
        self.assertAlmostEqual(self.box.Length.Value, 40)

    def test_unknown_alias_changes_nothing(self):
        with self.assertRaises(tools.ToolError):
            run_tool("set_parameters", doc=self.doc.Name, values={"box_l": 55, "nope": 1})
        self.assertEqual(self.sheet.get("box_l").Value, 40)

    def test_a_breaking_value_is_rolled_back(self):
        with self.assertRaises(tools.ToolError) as ctx:
            run_tool("set_parameters", doc=self.doc.Name, values={"box_w": "1 mm"})  # the 2 mm fillet cannot fit
        self.assertIn("nothing was changed", str(ctx.exception))
        self.doc.recompute()
        self.assertEqual(self.sheet.get("box_w").Value, 20)
        self.assertTrue(self.doc.getObject("Rounded").isValid())
