# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI tests for code parts (bruceworx_codecad_gui): New Code Part, the script editor, the tree icon, the
right-click menu and the approval prompt. Needs the running app: FreeCAD -t TestBruceWorxCodeCadGui"""

import importlib.util
import os
import tempfile
import time
import unittest

import FreeCAD

import bruceworx_codecad as cc
import bruceworx_codecad_dims as dims

HAVE_OCP = importlib.util.find_spec("OCP") is not None
BOX = "from build123d import *\nresult = Box(param('gcc_w', 10, 'Width'), 2, 3)\n"


def pump(n=6):
    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(FreeCAD.GuiUp and HAVE_OCP, "needs the GUI and OCP")
class TestCodeCadGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCADGui as Gui

        import bruceworx_codecad_gui as gui

        cls.Gui, cls.gui = Gui, gui
        cls.old_trust = os.environ.get("BW_CODECAD_TRUST_FILE")
        os.environ["BW_CODECAD_TRUST_FILE"] = os.path.join(tempfile.mkdtemp(), "trusted.json")
        gui.install()

    @classmethod
    def tearDownClass(cls):
        if cls.old_trust is None:
            os.environ.pop("BW_CODECAD_TRUST_FILE", None)
        else:
            os.environ["BW_CODECAD_TRUST_FILE"] = cls.old_trust

    def setUp(self):
        cc.clear_cache()
        self.doc = FreeCAD.newDocument("CodeCadGui")
        pump()

    def tearDown(self):
        editor = self.gui.editor()
        editor.clear()
        if self.gui._state["prompt"] is not None:
            self.gui._state["prompt"].reject()
        FreeCAD.closeDocument(self.doc.Name)
        pump()

    def test_commands_and_menu(self):
        commands = self.Gui.listCommands()
        for name in (self.gui.NEW, self.gui.EDIT, self.gui.REBUILD, self.gui.SOLID, self.gui.APPROVE):
            self.assertIn(name, commands)
        import bruceworx_menu

        names = [a.objectName() for a in bruceworx_menu.get_menu().actions()]
        self.assertIn("BruceWorxMenu_" + self.gui.NEW, names)
        self.assertLess(names.index("BruceWorxMenu_BruceWorx_CircularPattern"), names.index("BruceWorxMenu_" + self.gui.NEW))

    def test_new_part_from_template_opens_its_script(self):
        (part,) = self.gui.new_part("plate_holes", self.doc)
        pump()
        self.assertEqual(part.ViewObject.Proxy.__class__.__name__, "CodeCadView")
        self.assertEqual(self.gui.selected_part(), part)
        editor = self.gui.editor()
        self.assertTrue(self.gui._state["dock"].isVisible())
        self.assertEqual(editor.text.toPlainText(), part.CodeCadScript)
        self.assertEqual((editor.title.text(), part.Label), ("Plate with hole grid",) * 2)
        self.assertGreaterEqual(self.gui._state["dock"].height(), 200)
        self.assertIn("Up to date · 8 dimensions", editor.status.text())
        self.assertFalse(editor.dirty())
        self.assertTrue(part.ViewObject.Proxy.getIcon().endswith("Heuretes_CodePart.svg"))
        self.assertEqual(self.doc.UndoNames[0], "New code part: Plate with hole grid")
        dialog = self.gui._classes()["TemplateDialog"]()
        self.assertEqual(dialog.list.count(), 5)
        dialog.list.setCurrentRow(3)
        self.assertEqual(dialog.key(), "enclosure")
        self.assertIn("box_l", dialog.preview.toPlainText())
        dialog.deleteLater()

    def test_run_marks_the_failing_line_and_changes_nothing(self):
        (part,) = cc.make(self.doc, BOX, "Box")[0]
        self.assertTrue(self.gui.edit(part))
        editor = self.gui.editor()
        editor.text.setPlainText(BOX + "oops = 1 / 0\n")
        self.assertTrue(editor.dirty())
        self.assertFalse(editor.run())
        self.assertEqual(editor.text.error_line, 3)
        self.assertIn("line 3: ZeroDivisionError", editor.status.text())
        self.assertIn("Traceback", editor.status.toolTip())
        self.assertEqual(part.CodeCadScript, BOX)
        self.assertAlmostEqual(part.Shape.Volume, 60, places=6)
        editor.text.setPlainText(BOX.replace("2, 3", "2, 5"))
        self.assertTrue(editor.run())
        pump()
        self.assertIsNone(editor.text.error_line)
        self.assertFalse(editor.dirty())
        self.assertAlmostEqual(part.Shape.Volume, 100, places=6)
        self.assertEqual(self.doc.UndoNames[0], "Edit script: Box")
        self.doc.undo()
        pump()
        self.assertAlmostEqual(part.Shape.Volume, 60, places=6)
        self.assertEqual(editor.text.toPlainText(), BOX)  # the editor follows undo
        editor.text.setPlainText("x")
        editor.revert()
        self.assertEqual(editor.text.toPlainText(), BOX)

    def test_editor_follows_selection_but_keeps_unsaved_edits(self):
        (a,) = cc.make(self.doc, BOX, "A")[0]
        (b,) = cc.make(self.doc, BOX.replace("2, 3", "4, 4"), "B")[0]
        self.gui.edit(a)
        editor = self.gui.editor()
        self.Gui.Selection.clearSelection()
        self.Gui.Selection.addSelection(b)
        pump()
        self.assertEqual(editor.part(), b)
        editor.text.setPlainText("# half-written")
        self.Gui.Selection.clearSelection()
        self.Gui.Selection.addSelection(a)
        pump()
        self.assertEqual(editor.part(), b)
        self.assertEqual(editor.text.toPlainText(), "# half-written")
        editor.revert()

    def test_icon_and_menu_follow_the_state(self):
        (part,) = cc.make(self.doc, BOX, "Box")[0]
        self.assertEqual(self.gui.state_of(part), "ok")
        sheet = self.doc.getObject("Parameters")
        sheet.set(sheet.getCellFromAlias("gcc_w"), "0 mm")
        self.doc.recompute()
        pump()
        self.assertEqual(self.gui.state_of(part), "error")
        self.assertTrue(self.gui.icon_for(part).endswith("Heuretes_CodePartError.svg"))
        self.gui.edit(part)
        self.assertTrue(self.gui.editor().status.text().startswith("Error: line 2"))
        sheet.set(sheet.getCellFromAlias("gcc_w"), "10 mm")
        self.doc.recompute()
        pump()
        self.assertEqual(self.gui.state_of(part), "ok")
        from PySide import QtWidgets

        menu = QtWidgets.QMenu()
        part.ViewObject.Proxy.setupContextMenu(part.ViewObject, menu)
        self.assertEqual([a.text() for a in menu.actions()], ["Edit Script", "Rebuild", "Convert to Solid"])
        self.Gui.Selection.clearSelection()
        self.Gui.Selection.addSelection(part)
        self.Gui.runCommand(self.gui.SOLID)
        pump()
        self.assertEqual(cc.code_parts(self.doc), [])
        self.assertEqual(self.doc.UndoNames[0], "Convert to solid: Box")

    def test_script_from_elsewhere_asks_once_and_approve_rebuilds(self):
        (part,) = cc.make(self.doc, BOX, "Box")[0]
        name = part.Name
        path = os.path.join(tempfile.mkdtemp(), "foreign.FCStd")
        self.doc.saveAs(path)
        FreeCAD.closeDocument(self.doc.Name)
        os.environ["BW_CODECAD_TRUST_FILE"] = os.path.join(tempfile.mkdtemp(), "another-mac.json")
        cc.clear_cache()
        self.doc = FreeCAD.openDocument(path)
        pump(12)
        part = self.doc.getObject(name)
        box = self.gui._state["prompt"]
        self.assertIsNotNone(box)
        self.assertIn("a code part whose script was not written on this Mac", box.text())
        self.assertEqual(sorted(b.text() for b in box.buttons()), ["Approve and Rebuild", "Not Now", "Review Scripts"])
        self.assertEqual(self.gui.state_of(part), "locked")
        self.assertTrue(self.gui.icon_for(part).endswith("Heuretes_CodePartLocked.svg"))
        self.assertIsNone(self.gui.ask_approval(self.doc))  # once per document
        sheet = self.doc.getObject("Parameters")
        sheet.set(sheet.getCellFromAlias("gcc_w"), "20 mm")
        self.doc.recompute()
        self.assertAlmostEqual(part.Shape.Volume, 60, places=6)  # not run
        next(b for b in box.buttons() if b.text() == "Approve and Rebuild").click()
        pump(12)
        self.assertIsNone(self.gui._state["prompt"])
        self.assertEqual(self.gui.state_of(part), "ok")
        self.assertAlmostEqual(part.Shape.Volume, 120, places=6)

    def test_dimensions_show_on_the_model_and_edit_in_place(self):
        dims.install()
        dims._verified.clear()
        (part,) = self.gui.new_part("plate_holes", self.doc)
        pump(10)
        overlay = dims.overlay()
        self.assertIsNotNone(overlay)
        self.assertEqual(overlay.part(), part)
        self.assertEqual(list(overlay.chips), [d["name"] for d in cc.dimensions(part)])
        self.assertEqual(overlay.chips["plate_l"].label.text(), "plate_l = 80 mm")
        self.assertEqual(overlay.chips["holes_x"].label.text(), "holes_x = 3")
        overlay.check_all()
        pump()
        self.assertEqual(sorted(overlay.attached), ["corner_r", "hole_d", "plate_l", "plate_t", "plate_w"])
        self.assertEqual(overlay.lines.numVertices.getNum(), 3 + 3 + 3 + 1 + 1)
        listed = [n for n in overlay.chips if n not in overlay.attached]
        self.assertEqual(listed, ["hole_inset", "holes_x", "holes_y"])
        self.assertEqual({overlay.chips[n].y() for n in listed}, {dims.LIST_TOP})   # the rest are a row along the top
        rects = [overlay.chips[n].geometry() for n in overlay.chips]
        for i, rect in enumerate(rects):
            self.assertFalse(any(rect.intersects(other) for other in rects[i + 1:]))  # no chip covers another
        chip = overlay.chips["plate_l"]
        chip.start_edit()
        self.assertTrue(overlay.editing())
        self.assertEqual(chip.field.text(), "80 mm")
        self.assertFalse(chip.commit("abc def"))                     # not a value: nothing changes
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 80, places=6)
        self.assertTrue(chip.commit("120"))
        pump(10)
        self.assertAlmostEqual(part.Shape.BoundBox.XLength, 120, places=6)
        self.assertEqual(self.doc.UndoNames[0], f"{part.Label}: plate_l")
        overlay = dims.overlay()
        self.assertEqual(overlay.chips["plate_l"].label.text(), "plate_l = 120 mm")
        self.assertIn("plate_l", overlay.attached)                   # still attached at the new size, no new check
        self.doc.undo()
        pump(10)
        self.assertEqual(dims.overlay().chips["plate_l"].label.text(), "plate_l = 80 mm")
        self.Gui.Selection.clearSelection()
        pump(10)
        self.assertIsNone(dims.overlay())
