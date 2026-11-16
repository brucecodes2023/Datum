# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of 3D-print prep (roadmap MFG-01): bruceworx_printprep, and bruceworx_printprep_gui in the window.

    FreeCADCmd -t TestBruceWorxPrintPrep        (the GUI class is skipped)
    FreeCAD -t TestBruceWorxPrintPrep           (all of it)
"""

import importlib.util
import json
import os
import plistlib
import tempfile
import time
import unittest
import zipfile

import FreeCAD as App
import Part

import bruceworx_printprep as pp

HAVE_LIB3MF = importlib.util.find_spec("lib3mf") is not None


def tee():
    """A T: a 40 x 10 x 4 flange on the ground with a 4 x 10 x 20 web standing on it. Upright it needs no
    support; upside down the flange overhangs."""
    flange = Part.makeBox(40, 10, 4, App.Vector(-20, -5, 0))
    web = Part.makeBox(4, 10, 20, App.Vector(-2, -5, 4))
    return flange.fuse(web).removeSplitter()


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("PrintPrepTest")
        self.doc.UndoMode = 1
        self.part = self.doc.addObject("Part::Feature", "Tee")
        self.part.Shape = tee()
        self.doc.recompute()
        self.tmp = tempfile.mkdtemp()
        self.prefs = App.ParamGet(pp.PREFS_PATH)
        self.saved = {n: self.prefs.GetFloat(n, 0.0) for n in pp.BED_PREFS}
        self.saved_slicer = self.prefs.GetString(pp.SLICER_PREF, "")

    def tearDown(self):
        for name, value in self.saved.items():
            self.prefs.SetFloat(name, value)
        self.prefs.SetString(pp.SLICER_PREF, self.saved_slicer)
        App.closeDocument(self.doc.Name)


class TestOrientation(Base):
    def test_default_is_z_up_and_nothing_is_stored(self):
        self.assertFalse(pp.has_up(self.part))
        self.assertAlmostEqual(pp.up_of(self.part).z, 1.0)

    def test_place_on_a_face_turns_it_down(self):
        pp.place_on_face(self.part, App.Vector(1, 0, 0))  # the +X end of the flange goes on the bed
        up = pp.up_of(self.part)
        self.assertAlmostEqual(up.x, -1.0, places=9)
        shape = pp.oriented_shape(self.part)
        box = shape.BoundBox
        self.assertAlmostEqual(box.ZMin, 0.0, places=6)
        self.assertAlmostEqual(box.ZLength, 40.0, places=6)  # the 40 mm length stands up
        self.assertAlmostEqual(box.Center.x, 0.0, places=6)
        self.assertAlmostEqual(box.Center.y, 0.0, places=6)
        self.assertAlmostEqual(shape.Volume, self.part.Shape.Volume, places=6)

    def test_up_is_kept_in_the_parts_own_frame(self):
        self.part.Placement = App.Placement(App.Vector(50, 0, 9), App.Rotation(App.Vector(1, 0, 0), 90))
        pp.set_up_global(self.part, App.Vector(0, 0, 1))  # up in the document: the part's own +Y, turned up
        own = pp.up_of(self.part)
        self.assertAlmostEqual(own.y, 1.0, places=9)
        back = pp.up_global(self.part)
        self.assertAlmostEqual(back.z, 1.0, places=9)
        moved = pp.oriented_shape(self.part).BoundBox
        self.assertAlmostEqual(moved.ZMin, 0.0, places=6)
        self.assertAlmostEqual(moved.ZLength, 10.0, places=6)  # the part's Y length

    def test_part_of_a_feature_is_its_body(self):
        body = self.doc.addObject("PartDesign::Body", "Body")
        box = body.newObject("PartDesign::AdditiveBox", "Box")
        self.doc.recompute()
        self.assertIs(pp.part_of(box), body)
        self.assertIs(pp.part_of(body), body)
        self.assertIs(pp.part_of(self.part), self.part)

    def test_clear_and_bad_direction(self):
        pp.set_up(self.part, (0, 1, 0))
        pp.clear_up(self.part)
        self.assertFalse(pp.has_up(self.part))
        with self.assertRaises(pp.PrintPrepError):
            pp.set_up(self.part, (0, 0, 0))

    def test_suggest_prefers_the_flange_down(self):
        best = pp.suggest(self.part)
        self.assertAlmostEqual(best.up.z, 1.0, places=6)
        self.assertAlmostEqual(best.support_area, 0.0, places=6)
        self.assertAlmostEqual(best.bed_area, 400.0, places=3)  # the flange's 40 x 10 underside
        ranked = pp.candidates(self.part)
        upside_down = next(c for c in ranked if abs(c.up.z + 1) < 1e-6)
        self.assertAlmostEqual(upside_down.support_area, 360.0, places=3)  # the flange around the web: 400 - 40
        self.assertGreater(ranked.index(upside_down), 0)


class TestPlate(Base):
    def test_layout_rows_do_not_overlap(self):
        sizes = [(30, 20), (30, 20), (30, 20), (10, 40)]
        centres = pp.layout(sizes, gap=5, row_width=70)
        boxes = [(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2) for (cx, cy), (w, d) in zip(centres, sizes)]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                apart = a[2] + 5 <= b[0] + 1e-9 or b[2] + 5 <= a[0] + 1e-9 or a[3] + 5 <= b[1] + 1e-9 \
                    or b[3] + 5 <= a[1] + 1e-9
                self.assertTrue(apart, (a, b))
        self.assertEqual(centres[0], (15.0, 10.0))
        self.assertGreater(centres[2][1], centres[0][1])  # the third part starts a new row

    def test_mesh_is_watertight(self):
        points, triangles, watertight = pp.mesh_of(pp.oriented_shape(self.part))
        self.assertTrue(watertight)
        self.assertGreaterEqual(len(triangles), 12)
        self.assertEqual(max(max(t) for t in triangles), len(points) - 1)


class Test3MF(Base):
    def write(self, objects):
        path = os.path.join(self.tmp, "plate.3mf")
        return path, pp.write_3mf(path, objects)

    def test_one_part_in_print_orientation_on_the_bed(self):
        pp.place_on_face(self.part, App.Vector(1, 0, 0))
        self.part.Placement.Base = App.Vector(123, 45, 67)  # where it stands in the model does not matter
        path, report = self.write([self.part])
        self.assertTrue(report[0]["watertight"])
        with zipfile.ZipFile(path) as package:
            self.assertEqual(sorted(package.namelist()), ["3D/3dmodel.model", "[Content_Types].xml", "_rels/.rels"])
        read = pp.read_3mf(path)
        self.assertEqual(read["unit"], "millimeter")
        obj, = read["objects"]
        self.assertEqual(obj["name"], "Tee")
        self.assertEqual(obj["transform"], [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])  # baked in: nothing to apply
        self.assertAlmostEqual(obj["min"][2], 0.0, places=5)
        self.assertAlmostEqual(obj["max"][2], 40.0, places=5)
        size = sorted(obj["max"][k] - obj["min"][k] for k in range(2))
        self.assertAlmostEqual(size[0], 10.0, places=5)
        self.assertAlmostEqual(size[1], 24.0, places=5)

    def test_several_parts_are_one_plate(self):
        other = self.doc.addObject("Part::Feature", "Block")
        other.Shape = Part.makeBox(15, 15, 15)
        path, report = self.write([self.part, other])
        self.assertEqual([r["label"] for r in report], ["Tee", "Block"])
        a, b = pp.read_3mf(path)["objects"]
        for obj in (a, b):
            self.assertAlmostEqual(obj["min"][2], 0.0, places=5)
        apart = a["max"][0] <= b["min"][0] or b["max"][0] <= a["min"][0] or a["max"][1] <= b["min"][1] \
            or b["max"][1] <= a["min"][1]
        self.assertTrue(apart)

    def test_a_name_with_xml_characters(self):
        self.part.Label = 'Bracket <A> & "B"'
        path, _report = self.write([self.part])
        self.assertEqual(pp.read_3mf(path)["objects"][0]["name"], 'Bracket <A> & "B"')

    def test_nothing_to_export(self):
        with self.assertRaises(pp.PrintPrepError):
            pp.write_3mf(os.path.join(self.tmp, "none.3mf"), [])
        sketch = self.doc.addObject("Part::Feature", "Wire")
        sketch.Shape = Part.makePolygon([App.Vector(0, 0, 0), App.Vector(1, 0, 0), App.Vector(1, 1, 0)])
        with self.assertRaises(pp.PrintPrepError):
            pp.write_3mf(os.path.join(self.tmp, "wire.3mf"), [sketch])

    @unittest.skipUnless(HAVE_LIB3MF, "lib3mf is not installed")
    def test_lib3mf_reads_it_the_same(self):
        """An independent reader (the 3MF consortium's library) sees the same mesh, unit and box."""
        import lib3mf

        pp.place_on_face(self.part, App.Vector(1, 0, 0))
        path, report = self.write([self.part])
        wrapper = lib3mf.get_wrapper() if hasattr(lib3mf, "get_wrapper") else lib3mf.Wrapper(
            os.path.join(os.path.dirname(lib3mf.get_library_path()), "lib3mf"))
        model = wrapper.CreateModel()
        model.QueryReader("3mf").ReadFromFile(path)
        self.assertEqual(model.GetUnit(), lib3mf.ModelUnit.MilliMeter)
        meshes = model.GetMeshObjects()
        self.assertTrue(meshes.MoveNext())
        mesh = meshes.GetCurrentMeshObject()
        self.assertEqual(mesh.GetTriangleCount(), report[0]["triangles"])
        self.assertTrue(mesh.IsManifoldAndOriented())
        zs = [mesh.GetVertex(i).Coordinates[2] for i in range(mesh.GetVertexCount())]
        self.assertAlmostEqual(min(zs), 0.0, places=4)
        self.assertAlmostEqual(max(zs), 40.0, places=4)
        self.assertFalse(meshes.MoveNext())


class TestSlicers(Base):
    def fake_app(self, folder, name, extensions, version="1.2"):
        contents = os.path.join(folder, name + ".app", "Contents")
        os.makedirs(contents)
        with open(os.path.join(contents, "Info.plist"), "wb") as handle:
            plistlib.dump({"CFBundleName": name, "CFBundleShortVersionString": version, "CFBundleExecutable": name,
                           "CFBundleDocumentTypes": [{"CFBundleTypeExtensions": [e]} for e in extensions]}, handle)
        return os.path.join(folder, name + ".app")

    def test_found_by_what_the_app_says_it_opens(self):
        apps = os.path.join(self.tmp, "Applications")
        slicer_app = self.fake_app(apps, "SliceIt", ["stl", "3MF", "gcode"])
        self.fake_app(apps, "Viewer", ["3mf", "stl"])  # opens 3MF but makes no G-code: not a slicer
        self.fake_app(apps, "Editor", ["txt"])
        nested = self.fake_app(os.path.join(apps, "Vendor"), "Other Slicer", ["3mf", "gcode"], "9")
        found = pp.slicers([apps, os.path.join(self.tmp, "missing")])
        self.assertEqual([f["path"] for f in found], [slicer_app, nested])
        self.assertEqual(found[0]["name"], "SliceIt")
        self.assertTrue(found[0]["executable"].endswith("Contents/MacOS/SliceIt"))
        self.assertEqual(pp.slicer(found)["path"], slicer_app)
        pp.set_slicer(nested)
        self.assertEqual(pp.slicer(found)["path"], nested)
        pp.set_slicer("/gone.app")
        self.assertEqual(pp.slicer(found)["path"], slicer_app)
        self.assertIsNone(pp.slicer([]))

    def test_send_opens_the_file_in_the_app(self):
        calls = []
        app = {"name": "SliceIt", "path": "/Applications/SliceIt.app"}
        self.assertIs(pp.send("/tmp/a.3mf", app, calls.append), app)
        self.assertEqual(calls, [["/usr/bin/open", "-a", "/Applications/SliceIt.app", "/tmp/a.3mf"]])

    def settings(self, machine=None):
        root = os.path.join(self.tmp, "Support")
        folder = os.path.join(root, "OrcaSlicer")
        os.makedirs(os.path.join(folder, "user", "default", "machine"))
        os.makedirs(os.path.join(folder, "system", "Maker", "machine"))
        with open(os.path.join(folder, "OrcaSlicer.conf"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"presets": {"machine": "Mine"}}) + "\n# MD5 checksum 00\n")
        with open(os.path.join(folder, "user", "default", "machine", "Mine.json"), "w", encoding="utf-8") as handle:
            json.dump(machine or {"name": "Mine", "inherits": "Stock 0.4"}, handle)
        with open(os.path.join(folder, "system", "Maker", "machine", "Stock 0.4.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"name": "Stock 0.4", "printable_area": ["0x0", "220x0", "220x210", "0x210"],
                       "printable_height": "250"}, handle)
        return root

    def test_bed_from_the_slicers_selected_printer(self):
        for name in pp.BED_PREFS:
            self.prefs.SetFloat(name, 0.0)
        self.assertEqual(pp.bed_size(self.settings()), (220.0, 210.0, 250.0, "OrcaSlicer: Mine"))
        self.assertIsNone(pp.bed_size(os.path.join(self.tmp, "nowhere")))

    def test_bed_preferences_win(self):
        root = self.settings()
        for name, value in zip(pp.BED_PREFS, (180.0, 180.0, 180.0)):
            self.prefs.SetFloat(name, value)
        self.assertEqual(pp.bed_size(root), (180.0, 180.0, 180.0, "Preferences"))

    def test_fits(self):
        self.assertTrue(pp.fits((200, 100, 50), (220, 210, 250)))
        self.assertTrue(pp.fits((100, 215, 50), (220, 210, 250)))  # turned a quarter
        self.assertFalse(pp.fits((215, 215, 50), (220, 210, 250)))
        self.assertFalse(pp.fits((10, 10, 251), (220, 210, 250)))


class TestSummary(Base):
    def test_summary(self):
        for name, value in zip(pp.BED_PREFS, (30.0, 30.0, 100.0)):
            self.prefs.SetFloat(name, value)
        info = pp.summary(self.part)
        self.assertFalse(info["up_set"])
        self.assertAlmostEqual(info["size"][2], 24.0, places=6)
        self.assertAlmostEqual(info["support_area"], 0.0, places=6)
        self.assertFalse(info["fits"])  # 40 long on a 30 bed
        text = pp.summary_text(info)
        self.assertIn("Tee: 40 × 10 × 24 mm as printed (no orientation set: +Z up)", text)
        self.assertIn("Bed 30 × 30 × 100 mm (Preferences): does NOT fit", text)
        self.assertIn("Prints without support", text)
        pp.set_up(self.part, (0, 0, -1))
        text = pp.summary_text(pp.summary(self.part, walls=True))
        self.assertIn("Needs support under 360 mm²", text)
        self.assertIn("No wall thinner than 2.4 mm", text)  # the wall map's cap: the 4 mm walls are past it
        thin = self.doc.addObject("Part::Feature", "Fin")
        thin.Shape = Part.makeBox(20, 0.6, 10)
        self.assertIn("Thinnest wall 0.6 mm", pp.summary_text(pp.summary(thin, walls=True)))


def pump(n=8):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestPrintPrepGui(Base):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        import bruceworx_printprep_gui as gui

        cls.Gui, cls.gui = Gui, gui
        gui.install()

    def setUp(self):
        super().setUp()
        self.Gui.Selection.clearSelection()
        pump()

    def tearDown(self):
        import bruceworx_analysis_gui as bag

        if self.gui._state["dialog"] is not None:
            self.gui._state["dialog"].close()
        bag.close()
        super().tearDown()
        pump()

    def face(self, normal):
        for i, f in enumerate(self.part.Shape.Faces, 1):
            if (f.normalAt(0, 0) - App.Vector(*normal)).Length < 1e-6:
                return f"Face{i}"
        raise AssertionError(normal)

    def test_commands_are_registered(self):
        names = self.Gui.listCommands()
        for name in self.gui.COMMANDS:
            self.assertIn(name, names)
            self.assertTrue(os.path.isfile(self.gui._Command(name).GetResources()["Pixmap"]))

    def test_place_on_bed_from_a_face_is_one_undo_step_and_turns_the_map(self):
        self.Gui.Selection.addSelection(self.doc.Name, self.part.Name, self.face((1, 0, 0)))
        pump()
        self.assertIs(self.gui.place_on_bed(), self.part)
        pump(12)
        self.assertAlmostEqual(pp.up_of(self.part).x, -1.0, places=9)
        self.assertEqual(self.doc.UndoNames[0], "Place on bed")
        import bruceworx_analysis_gui as bag

        session = bag.session()
        self.assertIsNotNone(session)
        self.assertEqual(session.mode, "overhang")
        self.assertEqual(session.params["overhang"]["direction"], "face")
        for got, want in zip(session.direction(), (-1.0, 0.0, 0.0)):
            self.assertAlmostEqual(got, want, places=9)
        self.doc.undo()
        self.assertFalse(pp.has_up(self.part))

    def test_place_on_bed_needs_a_flat_face(self):
        self.assertIsNone(self.gui.place_on_bed())
        self.assertIn("Select a flat face", self.gui._state["message"])
        self.assertFalse(pp.has_up(self.part))

    def test_window_shows_the_summary_and_best_orientation(self):
        pp.set_up(self.part, (0, 0, -1))
        self.Gui.Selection.addSelection(self.part)
        dialog = self.gui.show()
        pump()
        self.assertTrue(dialog.isVisible())
        self.assertIn("Needs support under 360 mm²", dialog.text.text())
        dialog.best.click()
        pump()
        self.assertAlmostEqual(pp.up_of(self.part).z, 1.0, places=6)
        self.assertIn("Prints without support", dialog.text.text())
        self.assertEqual(self.doc.UndoNames[0], "Print orientation")
        dialog.reset.click()
        self.assertFalse(pp.has_up(self.part))

    def test_export_and_send(self):
        path = os.path.join(self.tmp, "out.3mf")
        self.assertEqual(self.gui.export_3mf(path, [self.part]), path)
        self.assertEqual(len(pp.read_3mf(path)["objects"]), 1)
        fake = os.path.join(self.tmp, "Applications")
        TestSlicers.fake_app(self, fake, "SliceIt", ["3mf", "gcode"])
        calls = []
        real = pp.slicers
        pp.slicers = lambda folders=None: real([fake])
        try:
            sent, app = self.gui.send_to_slicer([self.part], calls.append)
        finally:
            pp.slicers = real
        self.assertTrue(os.path.isfile(sent) and sent.endswith("Tee.3mf"))
        self.assertEqual(calls, [["/usr/bin/open", "-a", app["path"], sent]])
        os.remove(sent)
