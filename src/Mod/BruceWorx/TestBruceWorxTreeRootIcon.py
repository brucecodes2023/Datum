# SPDX-License-Identifier: LGPL-2.1-or-later
"""Model tree: a document row that stands for one assembly, part or body shows that object's icon instead of
the document page (Bruce, 3 Nov: "the top assemblies in the model tree should actually be assemblies and not
that weird icon ... its still a link, just change the icon"). A spreadsheet beside the assembly does not make
it a multi-root document; several parts keep the page. Links to an assembly show the assembly's icon."""

import os
import shutil
import tempfile
import time
import unittest

import FreeCAD


@unittest.skipUnless(FreeCAD.GuiUp, "needs the GUI")
class TestTreeRootIcon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        cls.Gui = Gui
        cls.dir = tempfile.mkdtemp(prefix="bw-treeicon-")

    @classmethod
    def tearDownClass(cls):
        for d in list(FreeCAD.listDocuments().values()):
            if d.Name.startswith("TI"):
                FreeCAD.closeDocument(d.Name)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def pump(self, n=20):
        for _ in range(n):
            self.Gui.updateGui()
            time.sleep(0.02)

    def tree(self):
        from PySide import QtWidgets

        for w in self.Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if w.metaObject().className() == "Gui::TreeWidget":
                return w
        self.fail("no model tree")

    def row(self, doc):
        self.pump()
        tree = self.tree()
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0) == doc.Label:
                return item
        self.fail(f"no row for {doc.Label}")

    @staticmethod
    def image(icon):
        return icon.pixmap(32, 32).toImage()

    def page(self):
        """The document page icon, from an empty document's row."""
        doc = FreeCAD.newDocument("TIempty")
        try:
            return self.image(self.row(doc).icon(0))
        finally:
            FreeCAD.closeDocument(doc.Name)

    def new(self, name):
        doc = FreeCAD.newDocument(name)
        self.addCleanup(lambda: FreeCAD.closeDocument(doc.Name) if doc.Name in FreeCAD.listDocuments() else None)
        return doc

    def test_single_assembly_beside_a_spreadsheet(self):
        doc = self.new("TIasm")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        asm.newObject("Part::Box", "Box")
        doc.addObject("Spreadsheet::Sheet", "Spreadsheet")
        doc.recompute()
        self.assertEqual(self.image(self.row(doc).icon(0)), self.image(asm.ViewObject.Icon))

    def test_single_body_and_single_part(self):
        doc = self.new("TIbody")
        body = doc.addObject("PartDesign::Body", "Body")
        doc.recompute()
        self.assertEqual(self.image(self.row(doc).icon(0)), self.image(body.ViewObject.Icon))
        doc2 = self.new("TIpart")
        part = doc2.addObject("App::Part", "Part")
        doc2.recompute()
        self.assertEqual(self.image(self.row(doc2).icon(0)), self.image(part.ViewObject.Icon))

    def test_several_roots_keep_the_page(self):
        page = self.page()
        doc = self.new("TIboxes")
        doc.addObject("Part::Box", "Box")
        doc.addObject("Part::Box", "Box001")
        doc.recompute()
        self.assertEqual(self.image(self.row(doc).icon(0)), page)
        # a single plain feature is not an assembly, part or body: the page too
        doc2 = self.new("TIonebox")
        doc2.addObject("Part::Box", "Box")
        doc2.recompute()
        self.assertEqual(self.image(self.row(doc2).icon(0)), page)

    def test_link_to_an_assembly_shows_the_assembly(self):
        page = self.page()
        sub = self.new("TIsub")
        sub_asm = sub.addObject("Assembly::AssemblyObject", "Assembly")
        sub_asm.newObject("Part::Box", "Box")
        sub.saveAs(os.path.join(self.dir, "TIsub.FCStd"))
        top = self.new("TItop")
        top.saveAs(os.path.join(self.dir, "TItop.FCStd"))  # a cross-document link needs both saved
        top_asm = top.addObject("Assembly::AssemblyObject", "Assembly")
        link = top.addObject("App::Link", "SubLink")
        link.LinkedObject = sub_asm
        top_asm.addObject(link)
        top.recompute()
        self.assertEqual(self.image(self.row(top).icon(0)), self.image(top_asm.ViewObject.Icon))
        self.assertNotEqual(self.image(link.ViewObject.Icon), page)
        self.assertNotEqual(self.image(link.ViewObject.Icon), self.image(top_asm.ViewObject.Icon),
                            "a link keeps its arrow")


if __name__ == "__main__":
    unittest.main()
