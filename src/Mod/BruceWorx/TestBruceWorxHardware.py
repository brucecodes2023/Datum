# SPDX-License-Identifier: LGPL-2.1-or-later
"""MOD-05 threads, holes and hardware, in FreeCAD: hole presets on real PartDesign Holes (the
heat-set hole's size, the tapped hole's thread), callouts from the model, the cosmetic thread
object (guessed size, drawn diameters, saved and reopened), callouts on a TechDraw view, and the
bundled Fasteners add-on (loads without network, makes an ISO 4762 M3x40, its vault key).

Run: FreeCADCmd -t TestBruceWorxHardware
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part
import Sketcher  # noqa: F401  -- registers Sketcher::SketchObject

import bruceworx_hardware as bh
from bruceworx import hardware as hw

try:
    import FastenersCmd  # noqa: F401
    import ScrewMaker  # noqa: F401

    HAVE_FASTENERS = True
except ImportError:
    HAVE_FASTENERS = False


def plate(doc, holes=((10, 10),), thickness=10.0, size=40.0):
    """A body with a size x size x thickness pad and a sketch of hole circles on its top face."""
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Outline")
    sk.AttachmentSupport = (doc.getObject("XY_Plane") or body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    pts = [App.Vector(0, 0, 0), App.Vector(size, 0, 0), App.Vector(size, size, 0), App.Vector(0, size, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = thickness
    doc.recompute()
    top = next(f"Face{i + 1}" for i, f in enumerate(pad.Shape.Faces)
               if abs(f.CenterOfMass.z - thickness) < 1e-6)
    hs = body.newObject("Sketcher::SketchObject", "HoleSketch")
    hs.AttachmentSupport = (pad, [top])
    hs.MapMode = "FlatFace"
    for x, y in holes:
        hs.addGeometry(Part.Circle(App.Vector(x, y, 0), App.Vector(0, 0, 1), 1.5))
    hole = body.newObject("PartDesign::Hole", "Hole")
    hole.Profile = hs
    doc.recompute()
    return body, hole


def cylinder_faces(shape, radius):
    return [f"Face{i + 1}" for i, f in enumerate(shape.Faces)
            if type(f.Surface).__name__ == "Cylinder" and abs(f.Surface.Radius - radius) < 1e-4]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BWHardware")
        self.tmp = tempfile.mkdtemp(prefix="bw-hw-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tearDown(self):
        App.closeDocument(self.doc.Name)


class HolePresetTests(Tmp):
    def test_heatset_m3_hole_is_cut_to_size(self):
        body, hole = plate(self.doc)
        bh.apply_preset(hole, "Heat-set M3x5.7")
        self.doc.recompute()
        self.assertTrue(hole.isValid(), hole.getStatusString())
        self.assertAlmostEqual(hole.Diameter.Value, 4.0)
        self.assertAlmostEqual(hole.Depth.Value, 6.7)
        self.assertEqual(hole.DrillPoint, "Flat")
        walls = cylinder_faces(hole.Shape, 2.0)
        self.assertEqual(len(walls), 1)
        bottom = [f for f in hole.Shape.Faces if abs(f.CenterOfMass.z - (10 - 6.7)) < 1e-6]
        self.assertEqual(len(bottom), 1, "a flat bottom 6.7 mm down")
        volume = 40 * 40 * 10 - 3.141592653589793 * 2.0 ** 2 * 6.7
        self.assertAlmostEqual(hole.Shape.Volume, volume, places=3)
        self.assertEqual(hole.HolePreset, "Heat-set M3x5.7")
        self.assertEqual(bh.preset_of(hole).name, "Heat-set M3x5.7")

    def test_tapped_m3_hole(self):
        body, hole = plate(self.doc, holes=((10, 10), (30, 10), (30, 30), (10, 30)))
        bh.apply_preset(hole, "Tapped M3")
        self.doc.recompute()
        self.assertTrue(hole.isValid(), hole.getStatusString())
        self.assertTrue(hole.Threaded)
        self.assertFalse(hole.ModelThread)
        self.assertEqual(hole.ThreadSize, "M3x0.5")
        self.assertEqual(hole.ThreadClass, "6H")
        self.assertEqual(bh.hole_count(hole), 4)
        self.assertEqual(bh.hole_callout(hole), "4X M3x0.5-6H ↧6\nØ2.5 ↧8")

    def test_clearance_and_counterbore(self):
        body, hole = plate(self.doc)
        bh.apply_preset(hole, "Counterbore M3 (ISO 4762)")
        self.doc.recompute()
        self.assertTrue(hole.isValid(), hole.getStatusString())
        self.assertAlmostEqual(hole.Diameter.Value, 3.4, places=3)
        self.assertEqual(hole.DepthType, "ThroughAll")
        self.assertGreater(hole.HoleCutDiameter.Value, 5.4)
        text = bh.hole_callout(hole)
        self.assertTrue(text.startswith("Ø3.4 THRU\n⌴ Ø"), text)

    def test_heatset_callout_and_edit_away(self):
        body, hole = plate(self.doc)
        bh.apply_preset(hole, "Heat-set M3x5.7")
        self.doc.recompute()
        self.assertEqual(bh.hole_callout(hole), "Ø4 ↧6.7\nHEAT-SET INSERT M3x5.7")
        hole.Diameter = 5.0  # no longer the preset's hole
        self.doc.recompute()
        self.assertIsNone(bh.preset_of(hole))
        self.assertEqual(bh.hole_callout(hole), "Ø5 ↧6.7")

    def test_every_builtin_preset_makes_a_valid_hole(self):
        table = bh.presets(vault_root="")
        for name in table:
            with self.subTest(name):
                doc = App.newDocument("BWPreset")
                try:
                    _body, hole = plate(doc)
                    bh.apply_preset(hole, table[name])
                    doc.recompute()
                    self.assertTrue(hole.isValid(), f"{name}: {hole.getStatusString()}")
                    self.assertTrue(bh.hole_callout(hole))
                finally:
                    App.closeDocument(doc.Name)

    def test_not_a_hole(self):
        body, _hole = plate(self.doc)
        with self.assertRaises(ValueError):
            bh.apply_preset(body, "Tapped M3")
        with self.assertRaises(ValueError):
            bh.apply_preset(_hole, "No such preset")


class CosmeticThreadTests(Tmp):
    def shaft(self, radius=4.0, height=20.0):
        part = self.doc.addObject("App::Part", "Part")
        body = self.doc.addObject("PartDesign::Body", "Body")
        part.addObject(body)
        cyl = body.newObject("PartDesign::AdditiveCylinder", "Cylinder")
        cyl.Radius, cyl.Height = radius, height
        self.doc.recompute()
        return part, body, cyl

    def test_external_thread_on_a_shaft(self):
        part, body, cyl = self.shaft()
        face = cylinder_faces(cyl.Shape, 4.0)[0]
        thread = bh.make_cosmetic_thread(cyl, face, length=12)
        self.doc.recompute()
        self.assertEqual(thread.Designation, "M8x1.25")
        self.assertFalse(thread.Internal)
        self.assertEqual(thread.Callout, "M8x1.25-6g ↧12")
        self.assertIn(thread, part.Group)
        self.assertNotIn(thread, body.Group)
        circles = [e for e in thread.Shape.Edges if type(e.Curve).__name__ == "Circle"]
        self.assertEqual(len(circles), 2)
        self.assertAlmostEqual(circles[0].Curve.Radius, hw.thread("M8").minor_external / 2, places=4)
        zs = sorted(round(c.Curve.Center.z, 6) for c in circles)
        self.assertEqual(zs, [8.0, 20.0])  # a free shaft: from its top end
        thread.FromOtherEnd = True
        self.doc.recompute()
        zs = sorted(round(e.Curve.Center.z, 6) for e in thread.Shape.Edges if type(e.Curve).__name__ == "Circle")
        self.assertEqual(zs, [0.0, 12.0])
        self.assertAlmostEqual(body.Shape.Volume, cyl.Shape.Volume)  # the solid is untouched

    def test_internal_thread_on_a_tap_drill_hole(self):
        body, hole = plate(self.doc)
        hole.Diameter = 2.5  # a plain M3 tap drill, not threaded by the Hole
        hole.DepthType = "ThroughAll"
        self.doc.recompute()
        face = cylinder_faces(hole.Shape, 1.25)[0]
        thread = bh.make_cosmetic_thread(hole, face)
        self.doc.recompute()
        self.assertTrue(thread.Internal)
        self.assertEqual(thread.Designation, "M3x0.5")
        self.assertEqual(thread.Callout, "M3x0.5-6H ↧10")
        radii = {round(e.Curve.Radius, 4) for e in thread.Shape.Edges if type(e.Curve).__name__ == "Circle"}
        self.assertEqual(radii, {1.5})  # the major diameter, drawn

    def test_moved_part_keeps_the_thread_on_the_face(self):
        part, body, cyl = self.shaft()
        part.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation(App.Vector(1, 0, 0), 90))
        thread = bh.make_cosmetic_thread(cyl, cylinder_faces(cyl.Shape, 4.0)[0], length=5)
        self.doc.recompute()
        # In the part's frame, the thread is on the shaft's axis.
        bb = thread.Shape.BoundBox
        self.assertAlmostEqual(bb.Center.x, 0.0, places=6)
        self.assertAlmostEqual(bb.Center.y, 0.0, places=6)
        self.assertAlmostEqual(bb.ZMin, 15.0, places=6)
        self.assertAlmostEqual(bb.ZMax, 20.0, places=6)

    def test_saved_and_reopened(self):
        part, body, cyl = self.shaft()
        bh.make_cosmetic_thread(cyl, cylinder_faces(cyl.Shape, 4.0)[0], designation="M8x1", length=10)
        self.doc.recompute()
        path = os.path.join(self.tmp, "shaft.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        thread = self.doc.getObject("CosmeticThread")
        self.assertTrue(bh.is_cosmetic_thread(thread))
        thread.Length = 6
        self.doc.recompute()
        self.assertEqual(thread.Callout, "M8x1-6g ↧6")

    def test_not_a_cylinder(self):
        body, hole = plate(self.doc)
        flat = next(f"Face{i + 1}" for i, f in enumerate(hole.Shape.Faces) if type(f.Surface).__name__ == "Plane")
        with self.assertRaises(ValueError):
            bh.make_cosmetic_thread(hole, flat)


class DrawingCalloutTests(Tmp):
    def test_callouts_on_a_view(self):
        import TechDraw  # noqa: F401

        part = self.doc.addObject("App::Part", "Part")
        body, hole = plate(self.doc)
        part.addObject(body)
        bh.apply_preset(hole, "Heat-set M3x5.7")
        hs2 = body.newObject("Sketcher::SketchObject", "HoleSketch2")
        hs2.AttachmentSupport = hole.Profile[0].AttachmentSupport
        hs2.MapMode = "FlatFace"
        hs2.addGeometry(Part.Circle(App.Vector(30, 30, 0), App.Vector(0, 0, 1), 1.5))
        tapped = body.newObject("PartDesign::Hole", "Tapped")
        tapped.Profile = hs2
        self.doc.recompute()
        bh.apply_preset(tapped, "Tapped M3")
        self.doc.recompute()
        page = self.doc.addObject("TechDraw::DrawPage", "Page")
        page.Template = self.doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        view = self.doc.addObject("TechDraw::DrawViewPart", "View")
        page.addView(view)
        view.Source = [part]
        view.Direction = App.Vector(0, 0, 1)
        self.doc.recompute()
        annos = bh.add_callouts(view)
        texts = sorted(a.AnnoText for a in annos)
        self.assertEqual(len(annos), 2)
        self.assertTrue(any("HEAT-SET INSERT M3x5.7" in t for t in texts), texts)
        self.assertTrue(any("M3x0.5-6H" in t for t in texts), texts)
        leaders = [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawLeaderLine"]
        self.assertEqual(len(leaders), 2)
        # Again after a change: the same objects, the new text.
        bh.apply_preset(tapped, "Tapped M4")
        self.doc.recompute()
        again = bh.add_callouts(view)
        self.assertEqual(sorted(a.Name for a in again), sorted(a.Name for a in annos))
        self.assertTrue(any("M4x0.7-6H" in a.AnnoText for a in again))
        self.assertEqual(len([o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawLeaderLine"]), 2)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class GuiCommandTests(Tmp):
    """The commands as a user runs them: select, run, answer the dialog (its run/exec patched)."""

    def run_command(self, name):
        import FreeCADGui as Gui

        Gui.runCommand(name)
        self.doc.recompute()

    def select(self, obj, sub=""):
        import FreeCADGui as Gui

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.doc.Name, obj.Name, sub)

    def test_hole_preset_on_a_sketch_and_on_a_hole(self):
        import FreeCADGui as Gui

        body, hole = plate(self.doc)
        sketch = hole.Profile[0]
        self.doc.removeObject(hole.Name)
        self.doc.recompute()
        table = bh.presets(vault_root="")
        saved = bh.PresetDialog.run
        try:
            bh.PresetDialog.run = lambda _self: (table["Heat-set M3x5.7"], None)
            self.select(body, f"{sketch.Name}.")
            self.run_command("BruceWorx_HolePreset")
            holes = [o for o in self.doc.Objects if bh.is_hole(o)]
            self.assertEqual(len(holes), 1)
            self.assertEqual((holes[0].HolePreset, holes[0].Label), ("Heat-set M3x5.7", "Heat-set M3x5.7"))
            bh.PresetDialog.run = lambda _self: (table["Tapped M3"], 10.0)
            self.select(body, f"{holes[0].Name}.")
            self.run_command("BruceWorx_HolePreset")
            self.assertTrue(holes[0].Threaded)
            self.assertAlmostEqual(holes[0].Depth.Value, 10.0)
            self.doc.undo()  # one transaction per run
            self.assertFalse(holes[0].Threaded)
        finally:
            bh.PresetDialog.run = saved
            Gui.Selection.clearSelection()

    def test_cosmetic_thread_command_on_a_picked_face(self):
        import FreeCADGui as Gui
        from PySide import QtWidgets

        body = self.doc.addObject("PartDesign::Body", "Body")
        cyl = body.newObject("PartDesign::AdditiveCylinder", "Cylinder")
        cyl.Radius, cyl.Height = 2.5, 12.0
        self.doc.recompute()
        face = cylinder_faces(cyl.Shape, 2.5)[0]
        saved = QtWidgets.QDialog.exec
        try:
            QtWidgets.QDialog.exec = lambda _self: QtWidgets.QDialog.Accepted
            self.select(body, f"{cyl.Name}.{face}")
            self.run_command("BruceWorx_CosmeticThread")
        finally:
            QtWidgets.QDialog.exec = saved
            Gui.Selection.clearSelection()
        threads = [o for o in self.doc.Objects if bh.is_cosmetic_thread(o)]
        self.assertEqual(len(threads), 1)
        self.assertEqual((threads[0].Designation, threads[0].Internal), ("M5x0.8", False))
        self.assertEqual(threads[0].Callout, "M5x0.8-6g \u21a712")

    def test_dialogs_leave_other_connections_alone(self):
        """A plain Python dialog helper with two bound-method connections used to take every function
        and lambda connection in the application with it when it died (PySide 6.8.3; the studio's
        timer went quiet after Hole Preset, 26 Oct)."""
        import gc

        from PySide import QtCore

        fired = []
        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: fired.append(1))
        signal = QtCore.SIGNAL("timeout()")
        table = bh.presets(vault_root="")
        saved = bh.PresetDialog.run
        try:
            bh.PresetDialog.run = lambda _self: (table["Tapped M3"], None)
            dialog = bh.PresetDialog(table, None)
            dialog.run()
            del dialog
        finally:
            bh.PresetDialog.run = saved
        gc.collect()
        self.assertEqual(timer.receivers(signal), 1)
        from PySide import QtWidgets

        saved_exec = QtWidgets.QDialog.exec
        try:
            QtWidgets.QDialog.exec = lambda _self: QtWidgets.QDialog.Rejected
            dialog = bh.FastenerDialog()
            dialog.run()
            del dialog
        except Exception:
            pass  # the Fasteners add-on may be off: the PresetDialog check above is the point
        finally:
            QtWidgets.QDialog.exec = saved_exec
        gc.collect()
        self.assertEqual(timer.receivers(signal), 1)


class _VaultSession:
    """What the vault client's Session gives these functions: .vault, .root, .work, refresh()."""

    def __init__(self, folder):
        from bruceworx import pdm_workspace as ws
        from bruceworx import vault_setup as vs

        setup = vs.VaultSetup(name="Hardware", folder=folder,
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")]))
        self.vault = vs.create_vault(setup, actor="bruce@mac")
        self.work = ws.Workspace(self.vault)

    @property
    def root(self):
        return self.vault.root

    def refresh(self):
        self.vault.refresh()


class MarkCotsTests(Tmp):
    def test_gearbox_style_items_become_cots(self):
        s = _VaultSession(os.path.join(self.tmp, "vault"))
        for pn, text in (("LE-01-01010-001", "Ball bearing 683ZZ 3\u00d77\u00d73 (COTS)"),
                         ("LE-01-01014-001", "Socket head cap screw M3x40 (COTS)"),
                         ("LE-01-01001-001", "Motor adapter plate")):
            s.vault.create_item(pn, "part", text)
        part = self.doc.addObject("App::Part", "Part")
        from bruceworx import partprops

        partprops.load_schema(user_layer=False).apply(part)
        part.PartNumber = "LE-01-01014-001"
        rows = hw.cots_candidates(s.vault.items)
        self.assertEqual([r[0] for r in rows], ["LE-01-01010-001", "LE-01-01014-001"])
        changed = bh.mark_cots(s, [r[0] for r in rows], documents=[self.doc])
        self.assertEqual(changed, ["LE-01-01010-001", "LE-01-01014-001"])
        self.assertEqual(s.vault.items["LE-01-01014-001"].item_type, "cots")
        self.assertEqual(s.vault.items["LE-01-01001-001"].item_type, "part")
        self.assertEqual(part.ItemType, "cots")
        cat = hw.load_catalogue(s.root)
        self.assertEqual(cat["ISO4762-M3x40"]["part_number"], "LE-01-01014-001")
        self.assertEqual(cat["BEARING-683ZZ"]["part_number"], "LE-01-01010-001")
        self.assertEqual(bh.mark_cots(s, ["LE-01-01014-001"], documents=[]), [])  # already


@unittest.skipUnless(HAVE_FASTENERS, "the Fasteners add-on is not on the path")
class FastenersAddonTests(Tmp):
    def test_iso4762_m3x40(self):
        screw = bh.make_fastener(self.doc, "ISO4762", "M3", "40")
        self.doc.recompute()
        self.assertTrue(screw.Shape.isValid())
        bb = screw.Shape.optimalBoundingBox()
        self.assertAlmostEqual(bb.ZLength, 40 + 3.0, delta=0.05)  # shank 40 (a custom length) + head k = d
        self.assertAlmostEqual(max(bb.XLength, bb.YLength), 5.5, delta=0.05)  # head dk = 5.5
        self.assertEqual(screw.Length, "Custom")  # ISO 4762 lists M3 up to 30
        self.assertEqual(bh.fastener_key_of(screw), "ISO4762-M3x40")
        self.assertEqual(bh.fastener_description_of(screw), "Socket head cap screw ISO 4762 M3x40")

    def test_nut_and_insert_have_no_length_in_their_key(self):
        nut = bh.make_fastener(self.doc, "ISO4032", "M3")
        insert = bh.make_fastener(self.doc, "IUTHeatInsert", "M3")
        self.doc.recompute()
        self.assertEqual(bh.fastener_key_of(nut), "ISO4032-M3")
        self.assertEqual(bh.fastener_key_of(insert), "IUTHeatInsert-M3")
        self.assertTrue(nut.Shape.isValid() and insert.Shape.isValid())
        self.assertEqual(bh.length_kind("ISO4762"), "list")
        self.assertEqual(bh.fastener_lengths("ISO4762", "M3")[0], "5")  # ISO 4762 M3: 5 ... 30
        self.assertIn("30", bh.fastener_lengths("ISO4762", "M3"))
        self.assertIn("M3", bh.fastener_sizes("ISO4762"))
        self.assertEqual(bh.fastener_types()[0][0], "ISO4762")

    def test_unknown_sizes_are_refused(self):
        with self.assertRaises(ValueError):
            bh.make_fastener(self.doc, "ISO4762", "M3", "0")
        with self.assertRaises(ValueError):
            bh.make_fastener(self.doc, "ISO4762", "M7", "40")
        with self.assertRaises(ValueError):
            bh.make_fastener(self.doc, "NOPE123", "M3")

    def test_insert_fastener_reuses_the_vaults_item(self):
        from bruceworx import vault_setup as vs

        s = _VaultSession(os.path.join(self.tmp, "vault"))
        # The GUI syncs opened parts from the configured vault (BruceWorx/VaultPath), not this one.
        prefs = App.ParamGet("User parameter:BaseApp/Preferences/BruceWorx")
        sync = prefs.GetBool("VaultStateSync", True)
        prefs.SetBool("VaultStateSync", False)
        self.addCleanup(prefs.SetBool, "VaultStateSync", sync)

        def dummy(path, _pn):
            with open(path, "wb") as handle:
                handle.write(b"not opened")

        vs.new_item(s.vault, "part", "Socket head cap screw M3x40 (COTS)", dummy)
        before = set(App.listDocuments())
        asm_doc = App.newDocument("BWAssembly")
        self.addCleanup(lambda: [App.closeDocument(n) for n in list(App.listDocuments()) if n not in before])
        assembly = asm_doc.addObject("App::Part", "Assembly")  # an App::Part stands in: same addObject
        with self.assertRaises(ValueError):  # not saved yet
            bh.link_part(assembly, os.path.join(self.tmp, "none.FCStd"))
        os.makedirs(s.work.root, exist_ok=True)
        asm_doc.saveAs(os.path.join(s.work.root, "Arm.FCStd"))
        # The existing M3x40 is found by its description: no new number.
        pn, made = bh.ensure_cots_item(s, "ISO4762", "M3", "40")
        self.assertEqual((pn, made), ("LE-01-01001-001", False))
        # An M3x12 is new: numbered as a part, typed cots, its file checked in.
        link, pn12, made = bh.insert_fastener(s, assembly, "ISO4762", "M3", "12")
        self.assertTrue(made)
        self.assertEqual(pn12, "LE-01-01002-001")
        item = s.vault.items[pn12]
        self.assertEqual((item.item_type, item.description), ("cots", "Socket head cap screw ISO 4762 M3x12"))
        self.assertIsNotNone(s.vault.latest_version(pn12))
        self.assertEqual(item.category, "Hardware")  # new bolts go in the Hardware category
        self.assertEqual(s.vault.items["LE-01-01001-001"].category, "")  # an existing item is left as it was
        self.assertEqual(s.vault.categories().get("Hardware"), 1)
        asm_doc.recompute()
        target = link.getLinkedObject(True)
        self.assertEqual((target.PartNumber, target.ItemType, target.HardwareKey),
                         (pn12, "cots", "ISO4762-M3x12"))
        self.assertIn(link, assembly.Group)
        self.assertAlmostEqual(link.Shape.optimalBoundingBox().ZLength, 12 + 3.0, delta=0.05)
        screw = next(o for o in target.Group if bh.is_fastener(o))
        self.assertEqual(screw.getEditorMode("Diameter"), ["ReadOnly"])
        # Again: the same item, no new number.
        _link2, again, made2 = bh.insert_fastener(s, assembly, "ISO4762", "M3", "12")
        self.assertEqual((again, made2), (pn12, False))
        self.assertEqual(hw.load_catalogue(s.root)["ISO4762-M3x12"]["part_number"], pn12)
        import bruceworx_section

        uncut = [o.Name for o in bruceworx_section.cots_links(asm_doc)]
        self.assertEqual(len(uncut), 2)


if __name__ == "__main__":
    unittest.main()
