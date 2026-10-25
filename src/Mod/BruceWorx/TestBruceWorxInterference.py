# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the interference check (bruceworx_interference, ASM-06): the broad phase, the bolt
4 mm too long for its blind hole (the roadmap's acceptance), a thread at the tap-drill size,
contact, clearance, a part buried in another, and fits shared with the section views."""

import math
import random
import unittest

import FreeCAD as App
import Part

import bruceworx_interference as itf

V = App.Vector


def _assembly(doc):
    try:
        import AssemblyApp  # noqa: F401 -- registers Assembly::AssemblyObject

        return doc.addObject("Assembly::AssemblyObject", "Assembly")
    except Exception:  # noqa: BLE001 -- no Assembly module: an App::Part holds the parts as well
        return doc.addObject("App::Part", "Assembly")


def _part(doc, asm, name, shape):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    asm.addObject(obj)
    return obj


class TestCache(unittest.TestCase):
    """A re-check only measures the pairs whose parts changed or moved (left for later, 25 Oct)."""

    def setUp(self):
        import Part

        itf.clear_cache()
        self.doc = App.newDocument("ItfCache")
        self.asm = _assembly(self.doc)
        self.a = _part(self.doc, self.asm, "A", Part.makeBox(10, 10, 10))
        self.b = _part(self.doc, self.asm, "B", Part.makeBox(10, 10, 10, V(8, 0, 0)))  # clashes with A
        self.c = _part(self.doc, self.asm, "C", Part.makeBox(10, 10, 10, V(30, 0, 0)))  # clear of both
        self.d = _part(self.doc, self.asm, "D", Part.makeBox(10, 10, 10, V(30, 10.1, 0)))  # 0.1 from C
        self.doc.recompute()

    def tearDown(self):
        itf.clear_cache()
        App.closeDocument(self.doc.Name)

    def test_second_check_comes_from_the_cache(self):
        first = itf.check(self.asm, gap=0.5)
        self.assertEqual(first.cached, 0)
        self.assertEqual(first.summary()["counts"]["clash"], 1)
        again = itf.check(self.asm, gap=0.5)
        self.assertEqual(again.cached, again.total)
        self.assertEqual([r.as_dict() for r in again.results], [r.as_dict() for r in first.results])
        # a different gap is a different measurement
        self.assertEqual(itf.check(self.asm, gap=0.0).cached, 0)

    def test_a_moved_part_is_measured_again_and_the_rest_not(self):
        itf.check(self.asm, gap=0.5)
        placement = self.d.Placement
        placement.Base = V(0, 0.2, 0)  # the box's own position is in its shape: D now 0.3 from C
        self.d.Placement = placement
        self.doc.recompute()
        again = itf.check(self.asm, gap=0.5)
        pairs_with_d = [(i, j) for i, j in again.pairs if "D" in (again.occurrences[i].label, again.occurrences[j].label)]
        self.assertEqual(again.cached, again.total - len(pairs_with_d))
        self.assertGreater(len(pairs_with_d), 0)
        self.assertAlmostEqual(again.of_kind(itf.CLEARANCE)[0].distance, 0.3, places=3)

    def test_fit_marks_apply_to_cached_pairs(self):
        itf.check(self.asm, gap=0.5)
        itf.set_fit(self.doc, itf.object_key(self.a), itf.object_key(self.b), True, "press fit")
        again = itf.check(self.asm, gap=0.5)
        self.assertEqual(again.cached, again.total)
        self.assertEqual((len(again.of_kind(itf.CLASH)), len(again.of_kind(itf.FIT))), (0, 1))

    def test_an_edited_part_is_measured_again(self):
        import Part

        itf.check(self.asm, gap=0.5)
        self.c.Shape = Part.makeBox(10, 10, 10, V(30, 0, 0)).cut(Part.makeBox(2, 2, 2, V(30, 0, 0)))
        self.doc.recompute()
        again = itf.check(self.asm, gap=0.5)
        self.assertLess(again.cached, again.total)


class TestBroadPhase(unittest.TestCase):
    def test_sweep_finds_the_same_pairs_as_every_pair(self):
        rnd = random.Random(7)
        boxes = []
        for _ in range(60):
            x, y, z = (rnd.uniform(0, 100) for _ in range(3))
            w, d, h = (rnd.uniform(1, 15) for _ in range(3))
            boxes.append((x, y, z, x + w, y + d, z + h))
        for grow in (0.0, 0.5, 3.0):
            brute = [(i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                     if itf.boxes_meet(boxes[i], boxes[j], grow)]
            self.assertEqual(itf.candidate_pairs(boxes, grow), brute)

    def test_boxes_a_gap_apart_meet_only_when_grown(self):
        a, b = (0, 0, 0, 1, 1, 1), (1.1, 0, 0, 2, 1, 1)
        self.assertFalse(itf.boxes_meet(a, b))
        self.assertTrue(itf.boxes_meet(a, b, 0.05))
        self.assertAlmostEqual(itf.box_gap(a, b), 0.1)

    def test_surface_bounds(self):
        plane = ("plane", (0, 0, 1), 5.0)
        self.assertAlmostEqual(itf.surface_gap(plane, (0, 0, 6, 1, 1, 7)), 1.0)
        self.assertEqual(itf.surface_gap(plane, (0, 0, 4, 1, 1, 7)), 0.0)
        bore = ("cylinder", (0, 0, 0), (0, 0, 1), 20.0)
        self.assertAlmostEqual(itf.surface_gap(bore, (-3, -4, 0, 3, 4, 10)), 15.0)
        self.assertEqual(itf.surface_gap(bore, (-30, -4, 0, 3, 4, 10)), 0.0)

    def test_fit_keys_ignore_order(self):
        self.assertEqual(itf.fit_key("D#B", "D#A"), itf.fit_key("D#A", "D#B"))


class TestPairs(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("BwInterference")
        self.asm = _assembly(self.doc)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def result(self, gap=0.0, contacts=True):
        self.doc.recompute()
        return itf.check(self.asm, gap, contacts)

    def test_bolt_too_long_for_its_blind_hole(self):
        """The roadmap's acceptance: an M5 bolt 4 mm too long for its 12 mm blind hole."""
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        _part(self.doc, self.asm, "Block", block)
        _part(self.doc, self.asm, "Bolt", Part.makeCylinder(2.5, 16, V(10, 10, 4)))
        c = self.result()
        clashes = c.of_kind(itf.CLASH)
        self.assertEqual(len(clashes), 1)
        self.assertEqual(len(c.results), 1)
        self.assertAlmostEqual(clashes[0].volume, math.pi * 2.5 ** 2 * 4, places=2)
        self.assertAlmostEqual(clashes[0].depth, 4.0, places=2)
        self.assertEqual(clashes[0].text(), "78.5 mm³, 4.0 mm deep")
        self.assertIsNotNone(clashes[0].overlap)

    def test_bolt_that_fits_only_touches(self):
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        _part(self.doc, self.asm, "Block", block)
        _part(self.doc, self.asm, "Bolt", Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        c = self.result()
        self.assertEqual([r.kind for r in c.results], [itf.CONTACT])
        self.assertEqual(self.result(contacts=False).results, [])

    def test_thread_at_the_tap_drill_size(self):
        block = Part.makeBox(20, 20, 10).cut(Part.makeCylinder(1.25, 6, V(10, 10, 4)))
        _part(self.doc, self.asm, "Block", block)
        _part(self.doc, self.asm, "Screw", Part.makeCylinder(1.5, 8, V(10, 10, 5)))
        (r,) = self.result().results
        self.assertEqual(r.kind, itf.CLASH)
        self.assertAlmostEqual(r.volume, math.pi * (1.5 ** 2 - 1.25 ** 2) * 5, places=3)
        self.assertAlmostEqual(r.depth, 0.25, places=2)

    def test_clearance_below_the_gap(self):
        _part(self.doc, self.asm, "A", Part.makeBox(10, 10, 10))
        _part(self.doc, self.asm, "B", Part.makeBox(10, 10, 10, V(10.1, 0, 0)))
        self.assertEqual(self.result(0.0).results, [])
        (r,) = self.result(0.2).results
        self.assertEqual(r.kind, itf.CLEARANCE)
        self.assertAlmostEqual(r.distance, 0.1, places=6)
        self.assertEqual(r.text(), "0.10 mm apart")
        self.assertEqual(self.result(0.1).results, [], "exactly the gap passes")

    def test_faces_that_touch(self):
        _part(self.doc, self.asm, "A", Part.makeBox(10, 10, 10))
        _part(self.doc, self.asm, "B", Part.makeBox(4, 4, 4, V(10, 3, 3)))
        (r,) = self.result(0.2).results
        self.assertEqual(r.kind, itf.CONTACT)

    def test_part_buried_in_another(self):
        _part(self.doc, self.asm, "Big", Part.makeBox(20, 20, 20))
        _part(self.doc, self.asm, "Small", Part.makeBox(2, 2, 2, V(5, 5, 5)))
        (r,) = self.result().results
        self.assertEqual(r.kind, itf.CLASH)
        self.assertAlmostEqual(r.volume, 8.0, places=6)

    def test_hidden_parts_are_left_out(self):
        _part(self.doc, self.asm, "A", Part.makeBox(10, 10, 10))
        b = _part(self.doc, self.asm, "B", Part.makeBox(10, 10, 10, V(5, 0, 0)))
        self.assertEqual(len(self.result().results), 1)
        b.Visibility = False
        self.assertEqual(self.result().results, [])

    def test_links_are_occurrences_of_their_part(self):
        src = self.doc.addObject("Part::Feature", "Pin")
        src.Shape = Part.makeCylinder(1, 10)
        src.Visibility = False
        _part(self.doc, self.asm, "Plate", Part.makeBox(20, 20, 2).cut(Part.makeCylinder(0.9, 2, V(5, 5, 0))))
        for name, x in (("Pin1", 5), ("Pin2", 15)):
            link = self.doc.addObject("App::Link", name)
            link.LinkedObject = src
            link.Placement.Base = V(x, 5, -4)
            self.asm.addObject(link)
        c = self.result()
        self.assertEqual(len(c.occurrences), 3)
        self.assertEqual({o.part for o in c.occurrences if o.label.startswith("Pin")}, {itf.object_key(src)})
        kinds = sorted((r.pair_label, r.kind) for r in c.results)
        self.assertEqual(kinds, [("Plate / Pin1", itf.CLASH), ("Plate / Pin2", itf.CLASH)])

    def test_fits_are_shared_with_the_sections(self):
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        a = _part(self.doc, self.asm, "Block", block)
        b = _part(self.doc, self.asm, "Bolt", Part.makeCylinder(2.5, 16, V(10, 10, 4)))
        sec = None
        try:
            import bruceworx_section as section

            sec = section.make(self.doc, "XZ")
        except Exception:  # noqa: BLE001 -- the section module needs the GUI for some things
            sec = None
        c = self.result()
        (r,) = c.results
        itf.set_fit(self.doc, r.first.part, r.second.part, True, "intended", self.asm)
        key = itf.fit_key(itf.object_key(a), itf.object_key(b))
        self.assertEqual(list(self.asm.Fits), [key])
        self.assertEqual(dict(self.asm.FitNotes), {key: "intended"})
        if sec is not None:
            self.assertIn(key, list(sec.Fits))
        self.assertEqual(itf.fits(self.doc), {key: "intended"})
        c.refit(itf.fits(self.doc))
        self.assertEqual([x.kind for x in c.results], [itf.FIT])
        self.assertEqual([x.kind for x in self.result().results], [itf.FIT])
        itf.set_fit(self.doc, r.first.part, r.second.part, False, assembly=self.asm)
        self.assertEqual(list(self.asm.Fits), [])
        if sec is not None:
            self.assertNotIn(key, list(sec.Fits))
        self.assertEqual([x.kind for x in self.result().results], [itf.CLASH])

    def test_check_file_for_the_release_check(self):
        import os
        import tempfile

        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        _part(self.doc, self.asm, "Block", block)
        _part(self.doc, self.asm, "Bolt", Part.makeCylinder(2.5, 16, V(10, 10, 4)))
        self.doc.recompute()
        path = os.path.join(tempfile.mkdtemp(), "bolt.FCStd")
        self.doc.saveAs(path)
        s = itf.check_file(path)
        self.assertEqual(s["counts"], {itf.CLASH: 1, itf.FIT: 0, itf.CONTACT: 0, itf.CLEARANCE: 0})
        self.assertEqual(s["results"][0]["volume"], round(math.pi * 2.5 ** 2 * 4, 4))

    def test_stepping_gives_the_same_results(self):
        for i in range(4):
            _part(self.doc, self.asm, f"B{i}", Part.makeBox(10, 10, 10, V(9.5 * i, 0, 0)))
        self.doc.recompute()
        whole = itf.check(self.asm)
        stepped = itf.Checker(self.asm)
        steps = 0
        while not stepped.step(0.0):
            steps += 1
        self.assertEqual(steps + 1, stepped.total)
        self.assertEqual([r.pair_label for r in stepped.results], [r.pair_label for r in whole.results])
        self.assertEqual(whole.summary()["counts"][itf.CLASH], 3)


@unittest.skipUnless(App.GuiUp, "needs the GUI")
class TestPanel(unittest.TestCase):
    """The Interference tab on a bolt too long for its hole and a plate it sits on."""

    def setUp(self):
        import FreeCADGui as Gui

        self.doc = App.newDocument("BwInterferenceGui")
        self.asm = _assembly(self.doc)
        block = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(2.5, 12, V(10, 10, 8)))
        self.block = _part(self.doc, self.asm, "Block", block)
        self.bolt = _part(self.doc, self.asm, "Bolt", Part.makeCylinder(2.5, 16, V(10, 10, 4)))
        self.plate = _part(self.doc, self.asm, "Plate", Part.makeBox(30, 30, 2, V(-5, -5, -2)))
        self.doc.recompute()
        App.setActiveDocument(self.doc.Name)
        Gui.getDocument(self.doc.Name).activeView()
        self.pump(5)

    def tearDown(self):
        import FreeCADGui as Gui
        import bruceworx_interference_gui as gui

        if gui._state["panel"] is not None:
            gui._state["panel"].close()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        App.closeDocument(self.doc.Name)

    def pump(self, n=1):
        import FreeCADGui as Gui

        for _ in range(n):
            Gui.updateGui()

    def wait(self, panel, seconds=30.0):
        import time

        until = time.time() + seconds
        self.pump(3)
        while (panel.running or panel.checker is None) and time.time() < until:
            self.pump()
        self.assertFalse(panel.running)

    def groups(self, panel):
        return [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]

    def item(self, panel, group):
        return panel.tree.topLevelItem(group).child(0)

    def overlay(self, panel):
        import bruceworx_interference_gui as gui

        root = panel.view.getSceneGraph()
        return [root.getChild(i) for i in range(root.getNumChildren())
                if root.getChild(i).getName().getString() == gui.OVERLAY_NAME]

    def test_command_lists_shows_and_marks_the_clash(self):
        import FreeCADGui as Gui
        import bruceworx_interference_gui as gui

        self.assertTrue(Gui.Command.get("BruceWorx_Interference").isActive())
        Gui.runCommand("BruceWorx_Interference")
        panel = gui._state["panel"]
        self.assertIsNotNone(panel)
        panel.clearance.setChecked(True)
        panel.gap.setValue(0.2)
        panel.contacts.setChecked(True)
        panel.start()
        self.wait(panel)
        self.assertEqual(self.groups(panel), ["Clashes (1)", "Fits (0)", "Clearance (0)", "Contacts (1)"])
        clash = self.item(panel, 0)
        self.assertEqual(clash.text(0), "Block / Bolt")
        self.assertEqual(clash.text(1), "78.5 mm³, 4.0 mm deep")
        panel.tree.setCurrentItem(clash)
        self.pump(2)
        self.assertEqual(len(self.overlay(panel)), 1)
        picked = sorted(n for s in Gui.Selection.getSelectionEx("", 0) for n in s.SubElementNames)
        self.assertEqual(picked, ["Block.", "Bolt."])
        self.assertTrue(panel.fit_button.isEnabled())
        panel.note.setText("demo")
        panel.mark_fit(True)
        self.assertEqual(self.groups(panel)[:2], ["Clashes (0)", "Fits (1)"])
        key = itf.fit_key(itf.object_key(self.block), itf.object_key(self.bolt))
        self.assertEqual(list(self.asm.Fits), [key])
        self.assertEqual(self.item(panel, 1).toolTip(0), "Block and Bolt: demo")
        panel.tree.setCurrentItem(self.item(panel, 1))
        panel.mark_fit(False)
        self.assertEqual(self.groups(panel)[:2], ["Clashes (1)", "Fits (0)"])
        panel.tree.setCurrentItem(panel.tree.topLevelItem(3).child(0))  # a contact: its point, no overlap
        self.pump(2)
        self.assertEqual(len(self.overlay(panel)), 1)
        panel.close()
        self.pump(2)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self.overlay(panel), [])

    def test_contacts_and_clearance_can_be_left_out(self):
        import bruceworx_interference_gui as gui

        panel = gui.open_panel(self.asm)
        panel.clearance.setChecked(False)
        panel.contacts.setChecked(False)
        panel.start()
        self.wait(panel)
        self.assertEqual(self.groups(panel), ["Clashes (1)", "Fits (0)"])
        panel.clearance.setChecked(True)
        panel.contacts.setChecked(True)
        panel.start()
        self.wait(panel)


if __name__ == "__main__":
    unittest.main()
