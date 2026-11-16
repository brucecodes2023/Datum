# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the drawing epics built on DRW-01: publish (DRW-08), callouts that follow the hole (DRW-04),
the parts list and balloons (DRW-02) and the first-pass drawing (DRW-06).

Headless: FreeCADCmd -t TestBruceWorxDrawingEpics. The PDF tests need the window:
FreeCAD -t TestBruceWorxDrawingEpics."""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part

import bruceworx_drawing as drawing
import bruceworx_drawing_callouts as follow
import bruceworx_drawing_publish as publish
import bruceworx_hardware as bh
from TestBruceWorxHardware import plate


def close_new(before):
    for name in [n for n in App.listDocuments() if n not in before]:
        App.closeDocument(name)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.before = set(App.listDocuments())
        self.tmp = tempfile.mkdtemp(prefix="bw-drw-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(close_new, self.before)

    def part_file(self, name, shape, pn="", rev=""):
        doc = App.newDocument(name)
        part = doc.addObject("App::Part", name)
        solid = doc.addObject("Part::Feature", name + "Solid")
        solid.Shape = shape
        part.addObject(solid)
        if pn:
            part.addProperty("App::PropertyString", "PartNumber", "BruceWorx")
            part.addProperty("App::PropertyString", "Revision", "BruceWorx")
            part.PartNumber, part.Revision = pn, rev
        doc.recompute()
        doc.saveAs(os.path.join(self.tmp, name + ".FCStd"))
        return doc, part

    def assembly_file(self, name, parts):
        doc = App.newDocument(name)
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        doc.saveAs(os.path.join(self.tmp, name + ".FCStd"))
        for i, (part, at) in enumerate(parts):
            link = asm.newObject("App::Link", f"{part.Name}_{i}")
            link.LinkedObject = part
            link.Placement.Base = App.Vector(*at)
        doc.recompute()
        doc.save()
        return doc, asm


class TestPublish(Tmp):
    def small_assembly(self):
        """A top assembly of a plate (drawn in its own file) and a pin (drawn in a drawing-only file),
        with the assembly's own drawing."""
        plate_doc, plate_part = self.part_file("plate", Part.makeBox(40, 30, 4), "TL-01-00001-001", "A")
        pin_doc, pin_part = self.part_file("pin", Part.makeCylinder(2, 12), "TL-01-00002-001", "02")
        drawing.new_drawing(plate_part, "ANSI_B_Landscape", root="")
        plate_doc.save()
        pin_drawing = App.newDocument("pin_drawing")
        pin_drawing.saveAs(os.path.join(self.tmp, "pin_drawing.FCStd"))
        drawing.new_drawing(pin_part, "ANSI_A_Landscape", root="", doc=pin_drawing)
        pin_drawing.save()
        top, asm = self.assembly_file("top", [(plate_part, (0, 0, 0)), (pin_part, (10, 10, 4)), (pin_part, (30, 10, 4))])
        drawing.new_drawing(asm, "ANSI_B_Landscape", root="")
        top.save()
        unrelated = App.newDocument("unrelated")  # a drawing of something else in the same folder
        box = unrelated.addObject("App::Part", "Other")
        unrelated.saveAs(os.path.join(self.tmp, "unrelated.FCStd"))
        drawing.new_drawing(box, "ANSI_A_Landscape", root="")
        unrelated.save()
        return top

    def test_files_an_assembly_needs_and_its_separate_drawings(self):
        top = self.small_assembly()
        files = publish.dependency_files(top)
        self.assertEqual(os.path.basename(files[0]), "top.FCStd")
        self.assertEqual(sorted(os.path.basename(f) for f in files), ["pin.FCStd", "plate.FCStd", "top.FCStd"])
        self.assertEqual([os.path.basename(f) for f in publish.drawing_files(files)], ["pin_drawing.FCStd"])

    def test_one_command_publishes_every_drawing_as_dxf(self):
        top = self.small_assembly()
        for name in ("plate", "pin", "pin_drawing", "unrelated"):  # closed files are opened and closed again
            App.closeDocument(name) if name in ("pin_drawing", "unrelated") else None
        open_before = set(App.listDocuments())
        out = os.path.join(self.tmp, "out")
        report = publish.publish(top, out, formats=("dxf",))
        self.assertEqual(set(App.listDocuments()), open_before)
        self.assertEqual([r["error"] for r in report], ["", "", ""])
        names = sorted(os.listdir(out))
        self.assertEqual(names, ["TL-01-00001-001-A.dxf", "TL-01-00002-001-02.dxf", "top.dxf"])
        for name in names:
            with open(os.path.join(out, name), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            self.assertIn("ENTITIES", text)
            self.assertIn("LINE", text)  # geometry, not a picture
        self.assertIn("3 drawings published", publish.summary(report, out))

    def test_stem_and_several_pages(self):
        doc, part = self.part_file("two", Part.makeBox(5, 5, 5), "TL-01-00009-001", "B")
        a = drawing.new_drawing(part, "ANSI_A_Landscape", root="")
        b = drawing.new_drawing(part, "ANSI_A_Landscape", root="")
        b.Label = "Sheet 2 / details"
        self.assertEqual(publish.stem(a, 1), "TL-01-00009-001-B")
        self.assertEqual(publish.stem(b), "TL-01-00009-001-B_Sheet_2_details")
        report = publish.publish(doc, os.path.join(self.tmp, "o"), formats=("dxf",))
        self.assertEqual(len({r["dxf"] for r in report}), 2)

    def test_headless_pdf_says_why_and_dxf_still_publishes(self):
        if App.GuiUp:
            self.skipTest("the window can write PDF")
        doc, part = self.part_file("solo", Part.makeBox(5, 5, 5))
        drawing.new_drawing(part, "ANSI_A_Landscape", root="")
        report = publish.publish(doc, os.path.join(self.tmp, "o"))
        self.assertIn("pdf: PDF needs the Vector Labs window", report[0]["error"])
        self.assertTrue(os.path.isfile(report[0]["dxf"]))

    def test_unsaved_document_needs_a_folder(self):
        doc = App.newDocument("Loose")
        with self.assertRaises(publish.PublishError):
            publish.publish(doc)

    @unittest.skipUnless(App.GuiUp, "PDF needs the window")
    def test_pdf_is_vector(self):
        top = self.small_assembly()
        out = os.path.join(self.tmp, "out")
        report = publish.publish(top, out)
        self.assertEqual([r["error"] for r in report], ["", "", ""])
        pdfs = sorted(n for n in os.listdir(out) if n.endswith(".pdf"))
        self.assertEqual(pdfs, ["TL-01-00001-001-A.pdf", "TL-01-00002-001-02.pdf", "top.pdf"])
        for name in pdfs:
            path = os.path.join(out, name)
            self.assertGreater(os.path.getsize(path), 20000, name)  # template and views are in it
            self.assertTrue(publish.is_vector_pdf(path), name)


class TestCalloutsFollow(Tmp):
    def setUp(self):
        super().setUp()
        self.doc = App.newDocument("BWFollow")
        part = self.doc.addObject("App::Part", "Part")
        self.body, self.hole = plate(self.doc)
        part.addObject(self.body)
        bh.apply_preset(self.hole, "Tapped M3")
        self.doc.recompute()
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        page.Template = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        self.view = self.doc.addObject("TechDraw::DrawViewPart", "View")
        page.addView(self.view)
        self.view.Source = [part]
        self.view.Direction = App.Vector(0, 0, 1)
        self.doc.recompute()
        self.anno, = bh.add_callouts(self.view)
        self.leader = self.anno.AnnoParent
        follow.uninstall()
        self.assertIsNotNone(follow.install())
        self.addCleanup(follow.uninstall)

    def test_callout_updates_when_the_hole_changes(self):
        self.assertIn("M3x0.5-6H", self.anno.AnnoText)
        bh.apply_preset(self.hole, "Tapped M4")
        self.doc.recompute()  # nothing else: no command is run again
        if App.GuiUp:
            import FreeCADGui

            FreeCADGui.updateGui()
        self.assertIn("M4x0.7-6H", self.anno.AnnoText)
        self.assertNotIn("M3x0.5", self.anno.AnnoText)

    def test_depth_and_counterbore_follow(self):
        self.hole.Threaded = False
        self.hole.Diameter = 3.4
        self.hole.DepthType = "ThroughAll"
        self.hole.HoleCutType = "Counterbore"
        self.hole.HoleCutDiameter = 6.5
        self.hole.HoleCutDepth = 3.4
        self.doc.recompute()
        if App.GuiUp:
            import FreeCADGui

            FreeCADGui.updateGui()
        self.assertEqual(self.anno.AnnoText, bh._rich(bh.hole_callout(self.hole)))
        self.assertIn("THRU", self.anno.AnnoText)
        self.assertIn("6.5", self.anno.AnnoText)

    def test_arrow_follows_the_hole_but_not_after_it_was_dragged(self):
        text_at = (self.anno.X.Value, self.anno.Y.Value)
        follow.refresh(self.doc)  # adopts the arrow, and puts it on the hole
        x0 = self.leader.X.Value
        # the plate is 40 x 40 with its corner on the origin and the hole at (10, 10): the view is
        # drawn about (20, 20), so the hole's rim is left of and below the view's centre
        self.assertLess(x0, 0)
        self.assertLess(self.leader.Y.Value, 0)
        self.assertLess(abs(x0 + 10 * self.view.getScale()), 2 * self.view.getScale())
        sketch = self.hole.Profile[0]
        sketch.Placement.Base = sketch.Placement.Base + App.Vector(0, 0, 0)
        geo = sketch.Geometry[0]
        sketch.delGeometry(0)
        sketch.addGeometry(Part.Circle(geo.Center + App.Vector(12, 0, 0), App.Vector(0, 0, 1), geo.Radius))
        self.doc.recompute()
        follow.refresh(self.doc)
        self.assertAlmostEqual(self.leader.X.Value - x0, 12 * self.view.getScale(), places=4)
        self.assertEqual((self.anno.X.Value, self.anno.Y.Value), text_at)  # where the text was stays
        self.leader.X = self.leader.X.Value + 3  # the user drags the arrow off the hole
        dragged = self.leader.X.Value
        sketch.delGeometry(0)
        sketch.addGeometry(Part.Circle(geo.Center, App.Vector(0, 0, 1), geo.Radius))
        self.doc.recompute()
        follow.refresh(self.doc)
        self.assertAlmostEqual(self.leader.X.Value, dragged, places=6)

    def test_off_switch(self):
        follow.uninstall()
        prefs = App.ParamGet(follow.PREFS_PATH)
        prefs.SetBool(follow.PREF, False)
        try:
            self.assertIsNone(follow.install())
            bh.apply_preset(self.hole, "Tapped M4")
            self.doc.recompute()
            self.assertIn("M3x0.5-6H", self.anno.AnnoText)
        finally:
            prefs.RemBool(follow.PREF)


import bruceworx_autodrawing as auto  # noqa: E402
import bruceworx_partslist as pl  # noqa: E402


class TestPartsList(Tmp):
    def setUp(self):
        super().setUp()
        self.plate_doc, self.plate = self.part_file("plate", Part.makeBox(40, 30, 4), "TL-01-00001-001", "A")
        self.pin_doc, self.pin = self.part_file("pin", Part.makeCylinder(2, 12), "TL-01-00002-001", "02")
        self.cap_doc, self.cap = self.part_file("cap", Part.makeBox(8, 8, 3), "TL-01-00003-001", "")
        self.top, self.asm = self.assembly_file(
            "top", [(self.plate, (0, 0, 0)), (self.pin, (10, 10, 4)), (self.pin, (30, 10, 4))])
        self.page = drawing.new_drawing(self.asm, "ANSI_B_Landscape", root="")
        self.view = self.page.Views[0].Anchor
        pl.uninstall()
        self.addCleanup(pl.uninstall)

    def test_rows_and_columns(self):
        items = {}
        rows = pl.rows(self.asm, items)
        self.assertEqual([(r["item"], r["part_number"], r["revision"], r["qty"]) for r in rows],
                         [(1, "TL-01-00001-001", "A", 1), (2, "TL-01-00002-001", "02", 2)])
        self.assertEqual([c[1] for c in pl.COLUMNS], ["NO.", "PART NUMBER", "REV", "DESCRIPTION", "MATERIAL", "QTY"])
        text = pl.svg(rows)
        self.assertIn('width="158.0mm"', text)
        for word in ("PART NUMBER", "TL-01-00002-001", ">2<"):
            self.assertIn(word, text)

    def test_list_sits_above_the_title_block(self):
        table = pl.make(self.page, self.asm)
        self.top.recompute()
        w, h = pl.table_size(2)
        self.assertAlmostEqual(table.X.Value + w / 2, 431.8 - 10, places=6)   # right edge on the border
        self.assertAlmostEqual(table.Y.Value - h / 2, 10 + 56 + 2, places=6)  # foot over the title block
        self.assertIn(table, self.page.Views)

    def test_adding_a_part_updates_the_table_and_keeps_balloon_numbers(self):
        table = pl.make(self.page, self.asm)
        made = pl.add_balloons(self.view, table)
        self.top.recompute()
        by_part = {getattr(b, pl.BALLOON_PROP).split("#")[0]: b for b in made}
        self.assertEqual({k: b.Text for k, b in by_part.items()}, {"plate.FCStd": "1", "pin.FCStd": "2"})
        foot = table.Y.Value - pl.table_size(2)[1] / 2
        self.assertIsNotNone(pl.install())
        link = self.asm.newObject("App::Link", "cap_0")  # the observer sees the assembly change
        link.LinkedObject = self.cap
        self.top.recompute()
        if App.GuiUp:
            import FreeCADGui

            FreeCADGui.updateGui()
        self.assertIn("TL-01-00003-001", table.Symbol)
        self.assertEqual(pl.item_numbers(table), {"plate.FCStd#plate": 1, "pin.FCStd#pin": 2, "cap.FCStd#cap": 3})
        self.assertEqual({k: b.Text for k, b in by_part.items()}, {"plate.FCStd": "1", "pin.FCStd": "2"})
        self.assertAlmostEqual(table.Y.Value - pl.table_size(3)[1] / 2, foot, places=6)  # it grew upwards
        more = pl.add_balloons(self.view, table)  # only the new part gets one
        self.assertEqual([b.Text for b in more], ["3"])

    def test_removing_a_part_keeps_the_other_numbers_until_renumbered(self):
        table = pl.make(self.page, self.asm)
        balloons = pl.add_balloons(self.view, table)
        pin_balloon = next(b for b in balloons if getattr(b, pl.BALLOON_PROP).startswith("pin"))
        for obj in [o for o in self.asm.Group if o.Name.startswith("plate")]:
            self.top.removeObject(obj.Name)
        self.top.recompute()
        pl.refresh(self.top)
        self.assertNotIn("TL-01-00001-001", table.Symbol)
        self.assertEqual(pin_balloon.Text, "2")
        link = self.asm.newObject("App::Link", "cap_0")
        link.LinkedObject = self.cap
        self.top.recompute()
        pl.refresh(self.top)
        self.assertEqual(pl.item_numbers(table)["cap.FCStd#cap"], 3)  # 1 is not handed to another part
        pl.renumber(table)
        self.assertEqual(pl.item_numbers(table), {"pin.FCStd#pin": 1, "cap.FCStd#cap": 2})
        self.assertEqual(pin_balloon.Text, "1")

    def test_quantity_follows(self):
        table = pl.make(self.page, self.asm)
        link = self.asm.newObject("App::Link", "pin_9")
        link.LinkedObject = self.pin
        self.top.recompute()
        pl.refresh(self.top)
        self.assertEqual([r["qty"] for r in pl.rows(self.asm, dict(getattr(table, pl.ITEMS_PROP)))], [1, 3])

    def test_assembly_of_page(self):
        self.assertEqual(pl.assembly_of_page(self.page), self.asm)


class TestAutoDrawing(Tmp):
    def test_part_gets_views_dimensions_and_callouts(self):
        doc = App.newDocument("BWAuto")
        part = doc.addObject("App::Part", "Part")
        body, hole = plate(doc, holes=((10, 10), (30, 30)))
        part.addObject(body)
        bh.apply_preset(hole, "Tapped M3")
        doc.recompute()
        made = auto.auto_drawing(part, "ANSI_B_Landscape", root="")
        page = made["page"]
        group = next(v for v in page.Views if v.isDerivedFrom("TechDraw::DrawProjGroup"))
        self.assertEqual(sorted(v.Type for v in group.Views), ["Front", "Right", "Top"])
        self.assertIn(made["isometric"], page.Views)
        values = sorted(round(d.getRawValue(), 3) for d in made["dimensions"])
        self.assertEqual(values, [10.0, 40.0, 40.0])  # height, width, depth of the 40 x 40 x 10 plate
        self.assertEqual(len(made["callouts"]), 1)
        self.assertIn("2X M3x0.5-6H", made["callouts"][0].AnnoText)
        top = next(v for v in group.Views if v.Type == "Top")
        self.assertEqual(made["callouts"][0].AnnoParent.LeaderParent, top)  # the view that looks down the holes
        for item in (made["callouts"][0], made["callouts"][0].AnnoParent):
            self.assertIn(item, page.Views)  # on the page, so it is drawn and published
        self.assertIsNone(made["parts_list"])

    def test_assembly_gets_a_parts_list_and_balloons(self):
        _d1, plate_part = self.part_file("plate", Part.makeBox(40, 30, 4), "TL-01-00001-001", "A")
        _d2, pin = self.part_file("pin", Part.makeCylinder(2, 12), "TL-01-00002-001", "02")
        top, asm = self.assembly_file("top", [(plate_part, (0, 0, 0)), (pin, (10, 10, 4))])
        made = auto.auto_drawing(asm, "ANSI_B_Landscape", root="")
        self.assertIsNotNone(made["parts_list"])
        self.assertEqual(sorted(b.Text for b in made["balloons"]), ["1", "2"])
        self.assertTrue(all(b.SourceView == made["isometric"] for b in made["balloons"]))
        self.assertEqual(len(made["dimensions"]), 3)
        # the arrow of the pin's balloon is on the pin: the plate is 40 x 30 x 4, the pin at (10, 10)
        iso = made["isometric"]
        pin_balloon = next(b for b in made["balloons"] if getattr(b, pl.BALLOON_PROP).startswith("pin"))
        want = follow.view_point(iso, App.Vector(10, 10, 10))
        self.assertAlmostEqual(pin_balloon.OriginX.Value, want[0], places=4)
        self.assertAlmostEqual(pin_balloon.OriginY.Value, want[1], places=4)

    def test_view_point_is_measured_from_the_views_centre(self):
        doc = App.newDocument("BWPoint")
        part = doc.addObject("App::Part", "P")
        box = doc.addObject("Part::Feature", "B")
        box.Shape = Part.makeBox(80, 50, 6)
        part.addObject(box)
        doc.recompute()
        page = drawing.new_drawing(part, "ANSI_B_Landscape", root="")
        top = next(v for v in page.Views[0].Views if v.Type == "Top")
        self.assertEqual(tuple(round(v, 6) for v in follow.view_point(top, App.Vector(0, 0, 0))), (-40.0, -25.0))
        self.assertEqual(tuple(round(v, 6) for v in follow.view_point(top, App.Vector(80, 50, 6))), (40.0, 25.0))

    def test_dimensions_sit_outside_the_outline(self):
        doc = App.newDocument("BWDims")
        part = doc.addObject("App::Part", "P")
        box = doc.addObject("Part::Feature", "B")
        box.Shape = Part.makeBox(80, 50, 6)
        part.addObject(box)
        doc.recompute()
        made = auto.auto_drawing(part, "ANSI_B_Landscape", root="")
        for dim in made["dimensions"]:
            view = dim.References2D[0][0]
            half_w, half_h = auto.half_extent(view)
            outside = dim.Y.Value < -half_h if dim.Type == "DistanceX" else dim.X.Value < -half_w
            self.assertTrue(outside, (dim.Type, dim.X, dim.Y))


class TestHoleCalloutsAtTheSource(Tmp):
    """bruceworx_hardware.add_callouts itself (no follow module installed): on a projection-group view the
    callout reaches the page, and the arrow lands on the hole even though the plate is not centred on the
    origin (its corner is, so the hole is off the view's centre)."""

    def test_projection_group_view(self):
        doc = App.newDocument("CalloutSource")
        part = doc.addObject("App::Part", "Part")
        body, hole = plate(doc)
        part.addObject(body)
        bh.apply_preset(hole, "Tapped M3")
        doc.recompute()
        page = doc.addObject("TechDraw::DrawPage", "Page")
        page.Template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        group = doc.addObject("TechDraw::DrawProjGroup", "Group")
        page.addView(group)
        group.Source = [part]
        group.addProjection("Front")
        doc.recompute()
        view = group.Views[0]
        if not hasattr(view, "projectPoint"):
            self.skipTest("this TechDraw has no projectPoint")
        made = bh.add_callouts(view)
        self.assertTrue(made)
        anno = made[0]
        leader = anno.AnnoParent
        self.assertIs(bh._page_of(view), page)
        self.assertIn(leader, page.Views)
        self.assertIn(anno, page.Views)
        _t, _text, anchor = next(t for t in bh.callout_targets(view) if t[0].Name == hole.Name)
        x, y = follow._arrow(view, anchor)
        self.assertAlmostEqual(leader.X.Value, x, places=4)
        self.assertAlmostEqual(leader.Y.Value, y, places=4)
        raw = follow._raw_arrow(view, anchor)
        self.assertGreater(abs(raw[0] - x) + abs(raw[1] - y), 1.0)  # where the old code put it: off the hole
