# SPDX-License-Identifier: LGPL-2.1-or-later
"""Model tree "Active document only": an assembly that links two other saved documents opens as three
document items; switched on, only the active one is listed, and activating a linked document shows it."""

import os
import shutil
import tempfile
import unittest

import FreeCAD

PATH = "User parameter:BaseApp/Preferences/TreeView"


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestTreeActiveOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        cls.dir = tempfile.mkdtemp(prefix="bw-treeonly-")
        cls.param = FreeCAD.ParamGet(PATH)
        cls.saved = cls.param.GetBool("ActiveDocumentOnly", False)
        cls.saved_mode = cls.param.GetInt("DocumentMode", 2)
        names = {}
        for n in ("partA", "partB"):
            d = FreeCAD.newDocument(n)
            d.addObject("Part::Box", "Box")
            d.recompute()
            p = os.path.join(cls.dir, n + ".FCStd")
            d.saveAs(p)
            names[n] = d.Name
        asm = FreeCAD.newDocument("asmTop")
        cls.asm_path = os.path.join(cls.dir, "asmTop.FCStd")
        asm.saveAs(cls.asm_path)  # a cross-document link needs a saved owner
        for n, key in (("partA", "LinkA"), ("partB", "LinkB")):
            link = asm.addObject("App::Link", key)
            link.setLink(FreeCAD.getDocument(names[n]).getObject("Box"))
        asm.recompute()
        asm.save()
        for d in list(FreeCAD.listDocuments().values()):
            FreeCAD.closeDocument(d.Name)
        cls.pump()

    @classmethod
    def tearDownClass(cls):
        for d in list(FreeCAD.listDocuments().values()):
            FreeCAD.closeDocument(d.Name)
        cls.param.SetBool("ActiveDocumentOnly", cls.saved)
        cls.param.SetInt("DocumentMode", cls.saved_mode)
        shutil.rmtree(cls.dir, ignore_errors=True)

    @classmethod
    def pump(cls):
        from PySide import QtWidgets

        for _ in range(5):
            QtWidgets.QApplication.processEvents()

    def tree(self):
        from PySide import QtWidgets

        for w in self.Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if w.metaObject().className() == "Gui::TreeWidget":
                return w
        self.fail("no model tree")

    def listed(self):
        """Labels of the document items shown at the top of the tree."""
        self.pump()
        tree = self.tree()
        items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        return [it.text(0) for it in items if not it.isHidden()]

    def set_only(self, on):
        self.param.SetBool("ActiveDocumentOnly", on)
        self.pump()

    def test_switch(self):
        self.set_only(False)
        doc = FreeCAD.openDocument(self.asm_path)
        self.pump()
        self.assertEqual(len(FreeCAD.listDocuments()), 3, list(FreeCAD.listDocuments()))
        self.assertEqual(len(self.listed()), 3)

        self.set_only(True)
        FreeCAD.setActiveDocument(doc.Name)
        self.assertEqual(self.listed(), [doc.Label])
        # the linked objects still appear under the links
        tree = self.tree()
        top = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
               if tree.topLevelItem(i).text(0) == doc.Label][0]
        self.assertGreaterEqual(top.childCount(), 2)

        other = [d for d in FreeCAD.listDocuments().values() if d.Name != doc.Name][0]
        FreeCAD.setActiveDocument(other.Name)
        self.assertEqual(self.listed(), [other.Label])

        FreeCAD.setActiveDocument(doc.Name)
        self.assertEqual(self.listed(), [doc.Label])
        # closing the active document: the next active one shows
        FreeCAD.closeDocument(other.Name)
        self.pump()
        self.assertEqual(len(self.listed()), 1)

        self.set_only(False)
        self.assertEqual(len(self.listed()), len(FreeCAD.listDocuments()))
        FreeCAD.closeDocument(doc.Name)

    def test_new_document_stays_out_of_the_list(self):
        self.set_only(True)
        a = FreeCAD.newDocument("firstDoc")
        b = FreeCAD.newDocument("secondDoc")
        self.pump()
        self.assertEqual(self.listed(), [b.Label])
        FreeCAD.setActiveDocument(a.Name)
        self.assertEqual(self.listed(), [a.Label])
        FreeCAD.closeDocument(a.Name)
        FreeCAD.closeDocument(b.Name)
