# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every BruceWorx test module inside a GUI FreeCAD and write the results as JSON.

    run-freecad.sh -u <config copy> package/bruceworx/tests/run_gui_tests.py

Environment: BW_TEST_OUT (the JSON file, default ./bw-gui-tests.json), BW_TEST_ONLY (comma-separated
module names), BW_TEST_EXTRA (more modules, e.g. upstream TestAssemblyWorkbench). FreeCAD quits when done.
"""

import glob
import io
import json
import os
import time
import traceback
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore

OUT = os.environ.get("BW_TEST_OUT", os.path.abspath("bw-gui-tests.json"))


def modules():
    only = [m for m in os.environ.get("BW_TEST_ONLY", "").split(",") if m]
    if only:
        return only
    here = os.path.join(App.getHomePath(), "Mod", "BruceWorx")
    names = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(here, "TestBruceWorx*.py")))
    return names + [m for m in os.environ.get("BW_TEST_EXTRA", "").split(",") if m]


def say(text):
    try:
        import bruceworx_testwindow as tw

        tw.say(text)
    except Exception:
        pass


def run():
    results = {}
    for name in modules():
        say(f"GUI test run: {name}")
        stream = io.StringIO()
        start = time.time()
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(name)
            res = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
            results[name] = {
                "ran": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
                "skipped": len(res.skipped), "seconds": round(time.time() - start, 1),
                "problems": [(str(t), tb[-1500:]) for t, tb in res.failures + res.errors],
            }
        except Exception:
            results[name] = {"ran": 0, "failures": 0, "errors": 1, "problems": [("import", traceback.format_exc()[-1500:])]}
        for doc in list(App.listDocuments()):
            try:
                App.closeDocument(doc)
            except Exception:
                pass
        with open(OUT, "w") as f:
            json.dump(results, f, indent=1)
    say("GUI test run: done")
    os._exit(0)


QtCore.QTimer.singleShot(3000, run)
