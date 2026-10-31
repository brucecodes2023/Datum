# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Simulation > Validation command in a GUI FreeCAD (SIM-05), under run_gui_tests.py.

One smoke test: the command is registered and in the Simulation ribbon, its window opens, the Run button starts
the background run (a headless FreeCAD child) for two quick cases, the progress reaches 100 %, and the LaTeX report
(compiled when a compiler is there) lists both cases.
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_sim as sim


def pump(n=4):
    import time

    import FreeCADGui as Gui

    for _ in range(n):
        Gui.updateGui()
        time.sleep(0.02)


@unittest.skipUnless(App.GuiUp, "needs the FreeCAD window")
@unittest.skipUnless(sim.find_ccx(), "CalculiX (ccx) is not installed")
class TestValidationGui(unittest.TestCase):
    def setUp(self):
        import bruceworx_validation_gui as vg

        vg.install()
        self.vg = vg
        self.tmp = tempfile.mkdtemp(prefix="bw-validation-gui-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        dialog = self.vg._state.get("dialog")
        if dialog is not None and self.vg._alive(dialog):
            dialog.close()
        self.vg._state.update({"ids": None, "folder": None, "dialog": None})

    def test_command_dialog_background_run_and_report(self):
        import json

        import FreeCADGui as Gui

        vg = self.vg
        self.assertIn(vg.COMMAND, Gui.listCommands())
        with open(os.path.join(App.getHomePath(), "Mod", "BruceWorx", "workspaces.json"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(vg.COMMAND, [c for g in json.loads(text)["workspaces"][1]["tabs"][0]["groups"] for c in g["commands"]])
        self.assertIsNotNone(vg.freecadcmd())
        Gui.runCommand(vg.COMMAND)
        pump()
        dialog = vg._state["dialog"]
        self.assertTrue(vg._alive(dialog) and dialog.isVisible())
        parts = dialog._parts
        self.assertTrue(parts["fast"].isChecked())
        self.assertFalse(parts["slow"].isChecked())
        self.assertFalse(parts["open"].isEnabled())
        vg._state["ids"], vg._state["folder"] = ["B-TH2", "B-ST2"], self.tmp
        parts["run"].click()
        pump()
        job = vg._state["job"]
        self.assertIsNotNone(job)
        self.assertFalse(parts["run"].isEnabled())
        self.assertTrue(parts["cancel"].isEnabled())
        info = vg.wait(job, 600)
        self.assertIsNotNone(info, "the validation job did not finish")
        pump()
        self.assertEqual(job.fraction, 1.0)
        run = info["run"]
        self.assertIsNotNone(run, info["message"])
        self.assertEqual(sorted(r["id"] for r in run["results"]), ["B-ST2", "B-TH2"])
        self.assertEqual(run["counts"].get("PASS"), 2, run["counts"])
        self.assertEqual(parts["bar"].value(), 1000)
        self.assertTrue(parts["run"].isEnabled())
        self.assertFalse(parts["cancel"].isEnabled())
        self.assertTrue(parts["open"].isEnabled())
        files = info["files"]
        with open(files["tex"], encoding="utf-8") as handle:
            tex = handle.read()
        self.assertIn("B-ST2", tex)
        self.assertIn("B-TH2", tex)
        self.assertIn("Code\\_Aster", tex)
        if sim.find_tex()[0]:
            self.assertTrue(files.get("pdf") and os.path.isfile(files["pdf"]), info["message"])
        self.assertEqual(os.path.dirname(files["dir"]), self.tmp)
        App.Console.PrintMessage(f"\n[validation gui] {info['message']}\n")
