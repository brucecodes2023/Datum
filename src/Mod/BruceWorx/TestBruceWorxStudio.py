# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for studio shading (bruceworx_studio, UIX-08): the finish each material gets, hand colours,
and in a GUI FreeCAD the program on the faces, the off switch back to stock, selection and pictures."""

import os
import shutil
import tempfile
import unittest

import bruceworx_studio as st

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - plain Python
    App = None


class TestLooks(unittest.TestCase):
    def test_metals_take_the_studio_colour(self):
        # FreeCAD's own metal appearances: dark grey aluminium, black steel with a white highlight
        alu = st.look_for("Aluminum-6061-T6", "Metal Standard/Metal/Aluminum", (0.3, 0.3, 0.3))
        steel = st.look_for("Steel-1C45", "Metal Standard/Metal/Steel", (0.0, 0.0, 0.0))
        self.assertEqual((alu.name, alu.metal), ("aluminium", 1.0))
        self.assertEqual(steel.name, "steel")
        self.assertGreater(min(alu.tint), 0.8)
        self.assertLess(max(steel.tint), max(alu.tint))

    def test_stainless_is_not_plain_steel(self):
        self.assertEqual(st.look_for("Stainless Steel X5CrNi18-10", "Metal").name, "stainless steel")

    def test_plastics_without_a_colour_get_one(self):
        self.assertEqual(st.look_for("ABS-Generic", "Thermoplast Standard/Thermoplast").name, "ABS")
        self.assertEqual(st.look_for("PETG (Prusament)").name, "PETG")
        self.assertEqual(st.look_for("PA6-Generic", "Thermoplast").name, "nylon")
        # PET has no Father: its library folder says what it is
        pet = st.look_for("PET-Generic", "Standard/Thermoplast")
        self.assertEqual((pet.name, pet.metal), ("plastic", 0.0))
        self.assertNotEqual(st.look_for("PET-Generic").name, "PETG")

    def test_a_card_colour_is_kept(self):
        # a shop card with its own colour, a bright metal card (gold contacts)
        self.assertIsNone(st.look_for("PETG Orange", "", (0.9, 0.4, 0.1)).tint)
        gold = st.look_for("Contact Metal", "", (0.86, 0.74, 0.40))
        self.assertIsNone(gold.tint)
        self.assertEqual(gold.metal, 1.0)

    def test_no_material(self):
        self.assertIs(st.look_for(""), st.PLAIN)
        self.assertIs(st.look_for("Default"), st.PLAIN)
        self.assertIs(st.look_for("Unobtainium"), st.PLAIN)

    def test_a_hand_colour_is_kept_with_the_finish(self):
        alu = st.look_for("Aluminum-6061-T6", "Metal", (0.3, 0.3, 0.3))
        self.assertEqual(st.resolve(alu, (0.3, 0.3, 0.3), [(0.3, 0.3, 0.3)]), (alu.tint, 1.0, alu.rough))
        tint, metal, rough = st.resolve(alu, (0.8, 0.1, 0.1), [(0.3, 0.3, 0.3)])
        self.assertIsNone(tint)
        self.assertEqual((metal, rough), (0.5, alu.rough))
        abs_ = st.look_for("ABS-Generic", "Thermoplast")
        self.assertEqual(st.resolve(abs_, (0.784, 0.804, 0.839), [st.DEFAULT_DIFFUSE, (0.784, 0.804, 0.839)])[0],
                         abs_.tint)

    def test_colours(self):
        self.assertEqual(st._parse_color("(0.3000, 0.2500, 1.0, 1.0)"), (0.3, 0.25, 1.0))
        self.assertIsNone(st._parse_color("nothing"))
        self.assertTrue(st.close((0.3, 0.3, 0.3, 1.0), (0.302, 0.298, 0.3)))
        self.assertFalse(st.close((0.3, 0.3, 0.3), (0.33, 0.3, 0.3)))

    def test_the_shader_keeps_clip_planes_and_fixed_function_depth(self):
        self.assertIn("gl_ClipVertex", st.VERTEX_SHADER)
        self.assertIn("ftransform()", st.VERTEX_SHADER)
        self.assertIn("coin_light_model == 0", st.FRAGMENT_SHADER)


@unittest.skipIf(App is None, "needs FreeCAD")
class TestReadingTheMaterial(unittest.TestCase):
    def test_the_material_is_read_without_writing_it_back(self):
        # a method call on obj.ShapeMaterial's material writes it back (FreeCAD 1.1): a loop with the observers
        import Materials
        import Part

        doc = App.newDocument("StudioMaterial")
        self.addCleanup(App.closeDocument, doc.Name)
        obj = doc.addObject("Part::Feature", "Plate")
        obj.Shape = Part.makeBox(10, 10, 2)
        manager = Materials.MaterialManager()
        uuid = [u for u, m in manager.Materials.items() if m.Name == "Aluminum-6061-T6"][0]
        obj.ShapeMaterial = manager.getMaterial(uuid)
        doc.recompute()
        changes = []

        class Spy:
            def slotChangedObject(self, changed, prop):
                changes.append(prop)

        spy = Spy()
        App.addDocumentObserver(spy)
        self.addCleanup(App.removeDocumentObserver, spy)
        name, family, card = st.material_of(obj)
        self.assertEqual(changes, [])
        self.assertEqual(name, "Aluminum-6061-T6")
        self.assertIn("Metal", family)
        self.assertTrue(st.close(card, (0.3, 0.3, 0.3)))


@unittest.skipUnless(App is not None and App.GuiUp, "needs a GUI FreeCAD")
class TestStudioInTheView(unittest.TestCase):
    """The program on a part's faces, the off switch, selection and the iso picture."""

    @classmethod
    def setUpClass(cls):
        cls.prefs = App.ParamGet(st.PREFS_PATH)
        cls.saved = (cls.prefs.GetBool("Applied", False), cls.prefs.GetBool(st.PREF, True))
        cls.prefs.SetBool("Applied", True)
        cls.prefs.SetBool(st.PREF, True)
        st.install()
        st.sync()

    @classmethod
    def tearDownClass(cls):
        cls.prefs.SetBool("Applied", cls.saved[0])
        cls.prefs.SetBool(st.PREF, cls.saved[1])
        st.sync()

    def setUp(self):
        import FreeCADGui as Gui
        import Materials
        import Part

        self.tmp = tempfile.mkdtemp(prefix="bw-studio-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.doc = App.newDocument("StudioTest")
        self.addCleanup(lambda: App.closeDocument(self.doc.Name) if self.doc.Name in App.listDocuments() else None)
        manager = Materials.MaterialManager()
        by_name = {m.Name: uuid for uuid, m in manager.Materials.items()}
        self.parts = {}
        for i, name in enumerate(("Aluminum-6061-T6", "ABS-Generic", "Default")):
            obj = self.doc.addObject("Part::Feature", "P%d" % i)
            obj.Shape = Part.makeCylinder(10, 20, App.Vector(i * 30, 0, 0))
            if name in by_name:
                obj.ShapeMaterial = manager.getMaterial(by_name[name])
            self.parts[name] = obj
        self.doc.recompute()
        self.view = Gui.getDocument(self.doc.Name).activeView()
        self.view.viewIsometric()
        self.view.fitAll()
        self.pump()

    def pump(self, n=8):
        import time

        import FreeCADGui as Gui

        for _ in range(n):
            Gui.updateGui()
            time.sleep(0.02)

    def children(self, obj):
        sep = st.flat_root(obj.ViewObject)
        return [sep.getChild(i).getTypeId().getName().getString() for i in range(sep.getNumChildren())]

    def picture(self, name):
        from PySide import QtGui

        path = os.path.join(self.tmp, name + ".png")
        self.view.saveImage(path, 400, 300, "Current")
        return QtGui.QImage(path)

    def test_on_the_faces_of_every_part(self):
        for obj in self.parts.values():
            self.assertEqual(self.children(obj)[0], "ShaderProgram", obj.Name)
        # the aluminium and the ABS have their own looks, the part without a material FreeCAD's colour
        alu = st.finish_of(self.parts["Aluminum-6061-T6"])
        self.assertEqual(alu[0].name, "aluminium")
        self.assertIsNotNone(alu[1])
        self.assertEqual(st.finish_of(self.parts["ABS-Generic"])[0].name, "ABS")
        self.assertIsNone(st.finish_of(self.parts["Default"])[1])

    def test_off_is_stock_freecad(self):
        obj = self.parts["Aluminum-6061-T6"]
        studio = self.children(obj)
        self.prefs.SetBool(st.PREF, False)
        self.addCleanup(self.prefs.SetBool, st.PREF, True)
        self.pump()
        stock = self.children(obj)
        self.assertEqual(stock, studio[1:])
        self.assertEqual(st.installed_count(), 0)
        self.assertNotIn("ShaderProgram", stock)
        self.prefs.SetBool(st.PREF, True)
        self.pump()
        self.assertEqual(self.children(obj), studio)

    def test_a_new_part_and_a_new_material_follow(self):
        import Part

        obj = self.doc.addObject("Part::Feature", "Later")
        obj.Shape = Part.makeBox(10, 10, 10)
        self.doc.recompute()
        self.pump()
        self.assertEqual(self.children(obj)[0], "ShaderProgram")
        self.assertIs(st.finish_of(obj)[0], st.PLAIN)
        obj.ShapeMaterial = self.parts["Aluminum-6061-T6"].ShapeMaterial
        self.pump()
        look, tint, _shown, metal, _rough = st.finish_of(obj)
        self.assertEqual((look.name, tint, metal), ("aluminium", look.tint, 1.0))
        self.assertIsNot(st.flat_root(obj.ViewObject).getChild(0), st._program(None, None, 0.0, st.PLAIN.rough))

    def test_the_picture_changes_and_selection_still_shows(self):
        import FreeCADGui as Gui

        studio = self.picture("studio")
        Gui.Selection.addSelection(self.doc.Name, "P1")
        self.pump()
        selected = self.picture("studio-selected")
        Gui.Selection.clearSelection()
        self.prefs.SetBool(st.PREF, False)
        self.addCleanup(self.prefs.SetBool, st.PREF, True)
        self.pump()
        stock = self.picture("stock")
        self.assertFalse(studio.isNull() or stock.isNull())
        self.assertNotEqual(studio, stock)
        self.assertNotEqual(studio, selected)

    def test_iso_picture_renders(self):
        import bruceworx_preview as pv

        image = pv.render(self.doc, 128)
        self.assertIsNotNone(image)
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main()
