# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests of the new-document chooser (bruceworx_newdoc): part, assembly or hybrid, and the tab icon.

kind_from needs no GUI; the rest needs the running app: FreeCAD -t TestBruceWorxNewDocGui"""

import time
import unittest

import FreeCAD

import bruceworx_newdoc as nd


def pump(n=8):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.03)


class TestKind(unittest.TestCase):
    def test_kind_from(self):
        self.assertEqual(nd.kind_from([], False), nd.OTHER)
        self.assertEqual(nd.kind_from(["PartDesign::Body", "Sketcher::SketchObject"], True), nd.PART)
        self.assertEqual(nd.kind_from(["Assembly::AssemblyObject", "App::Link"], False), nd.ASSEMBLY)
        self.assertEqual(nd.kind_from(["Assembly::AssemblyObject", "PartDesign::Body"], True), nd.HYBRID)
        self.assertEqual(nd.kind_from(["TechDraw::DrawPage"], False), nd.DRAWING)
        self.assertEqual(nd.kind_from(["TechDraw::DrawPage", "Part::Box"], True), nd.PART)

    def test_kind_of_a_document(self):
        doc = FreeCAD.newDocument("NdKind")
        try:
            self.assertEqual(nd.kind_of(doc), nd.OTHER)
            doc.addObject("Sketcher::SketchObject", "Sketch")
            self.assertEqual(nd.kind_of(doc), nd.OTHER)  # a loose sketch is not a part yet
            doc.addObject("Part::Box", "Box")
            doc.recompute()
            self.assertEqual(nd.kind_of(doc), nd.PART)
            doc.addObject("Assembly::AssemblyObject", "Assembly")
            self.assertEqual(nd.kind_of(doc), nd.HYBRID)
            doc.removeObject("Box")
            self.assertEqual(nd.kind_of(doc), nd.ASSEMBLY)
        finally:
            FreeCAD.closeDocument(doc.Name)


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestNewDocGui(unittest.TestCase):
    def setUp(self):
        self.before = set(FreeCAD.listDocuments())
        FreeCAD.ParamGet(nd.PREFS_PATH).SetBool(nd.PREF, True)

    def tearDown(self):
        FreeCAD.ParamGet(nd.PREFS_PATH).RemBool(nd.PREF)
        menu = nd._state["menu"]
        if menu is not None:
            menu.close()
        for name in set(FreeCAD.listDocuments()) - self.before:
            FreeCAD.closeDocument(name)
        pump()

    def made(self):
        new = set(FreeCAD.listDocuments()) - self.before
        self.assertEqual(len(new), 1, new)
        return FreeCAD.getDocument(new.pop())

    def test_menu_has_the_three_kinds(self):
        menu = nd.menu()
        self.assertEqual([a.objectName() for a in menu.actions()],
                         ["BruceWorxNew_part", "BruceWorxNew_assembly", "BruceWorxNew_hybrid"])
        self.assertTrue(all(not a.icon().isNull() for a in menu.actions()))

    def test_part(self):
        nd.create(nd.PART)
        pump()
        doc = self.made()
        self.assertEqual([o.TypeId for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")], ["PartDesign::Body"])
        self.assertEqual(nd.kind_of(doc), nd.PART)

    def test_assembly(self):
        nd.create(nd.ASSEMBLY)
        pump()
        self.assertEqual(nd.kind_of(self.made()), nd.ASSEMBLY)

    def test_hybrid_has_a_body_in_the_assembly_and_undoes_in_one_step(self):
        nd.create(nd.HYBRID)
        pump()
        doc = self.made()
        assembly = next(o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject")
        bodies = [o for o in assembly.Group if o.isDerivedFrom("PartDesign::Body")]
        self.assertEqual(len(bodies), 1)
        self.assertEqual(nd.kind_of(doc), nd.HYBRID)
        self.assertEqual(doc.UndoNames[0], "New body")

    def test_the_tab_shows_the_kind_and_follows_the_document(self):
        nd.create(nd.PART)
        pump()
        doc = self.made()
        self.assertEqual(nd.refresh_icons()[doc.Name], nd.PART)
        (_doc, subs), = [w for w in nd._windows() if w[0] is doc]
        self.assertEqual(subs[0].property(nd.KIND_PROPERTY), nd.PART)
        self.assertEqual(subs[0].windowIcon().cacheKey(), nd.icon(nd.PART).cacheKey())
        doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.assertEqual(nd.refresh_icons()[doc.Name], nd.HYBRID)
        self.assertEqual(subs[0].windowIcon().cacheKey(), nd.icon(nd.HYBRID).cacheKey())

    def test_file_new_opens_the_menu(self):
        import FreeCADGui as Gui
        from PySide import QtGui

        self.assertTrue(nd.take_over_new())
        action = Gui.getMainWindow().findChild(QtGui.QAction, "Std_New")
        action.trigger()
        pump()
        self.assertEqual(set(FreeCAD.listDocuments()), self.before)  # no empty document was made
        menu = nd._state["menu"]
        self.assertTrue(menu is not None and menu.isVisible())
        menu.actions()[0].trigger()
        menu.close()
        pump(12)
        self.assertEqual(nd.kind_of(self.made()), nd.PART)

    def test_off_switch_makes_an_empty_document(self):
        FreeCAD.ParamGet(nd.PREFS_PATH).SetBool(nd.PREF, False)
        self.assertIsNone(nd.choose())
        pump()
        self.assertEqual(nd.kind_of(self.made()), nd.OTHER)
        self.assertEqual(nd.refresh_icons(), {})
