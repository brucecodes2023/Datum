# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] A joint made by a headless FreeCADCmd script has no view provider proxy (ViewObject.Proxy is an int).
# Opening such a file in the GUI and solving the assembly used to raise AttributeError out of
# AssemblyObject::redrawJointPlacement and abort the process. Now the error is caught and logged, the
# joint's own redraw returns quietly, and the GUI gives the joint its view provider when the file opens.

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import FreeCAD as App

MAKE = r"""
import os, FreeCAD as App, JointObject
V = App.Vector
doc = App.newDocument("BWJVP")
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
jg = asm.newObject("Assembly::JointGroup", "Joints")
def box(n, pos):
    b = asm.newObject("Part::Box", n)
    b.Placement = App.Placement(V(*pos), App.Rotation())
    return b
a = box("A", (0, 0, 0))
b = box("B", (30, 0, 0))
g = jg.newObject("App::FeaturePython", "Ground")
JointObject.GroundedJoint(g, a)
doc.recompute()
j = jg.newObject("App::FeaturePython", "Fixed")
JointObject.Joint(j, JointObject.JointTypes.index("Fixed"))
j.Proxy.setJointConnectors(j, [[a, ["Face6", "Vertex7"]], [b, ["Face5", "Vertex1"]]])
doc.recompute()
doc.saveAs(os.environ["BWJVP_FILE"])
"""

OPEN = r"""
import os, FreeCAD as App, FreeCADGui as Gui
from PySide import QtCore
def go():
    try:
        doc = App.openDocument(os.environ["BWJVP_FILE"])
        Gui.ActiveDocument.setEdit(doc.getObject("Assembly"))
        doc.getObject("Assembly").solve()
        text = "OK"
    except BaseException as e:
        import traceback
        text = "EXC " + traceback.format_exc()
    open(os.environ["BWJVP_OUT"], "w").write(text)
    Gui.getMainWindow().close()
QtCore.QTimer.singleShot(1500, go)
"""


def _bin(name):
    return os.path.join(App.getHomePath(), "bin", name)


def _env(tmp, **extra):
    env = dict(os.environ, FREECAD_USER_HOME=os.path.join(tmp, "home"), **extra)
    env.pop("QT_QPA_PLATFORM", None)
    return env


def _make_headless_file(tmp):
    """Write the assembly with a joint using FreeCADCmd, so there is no GuiDocument.xml."""
    path = os.path.join(tmp, "joint.FCStd")
    script = os.path.join(tmp, "make.py")
    with open(script, "w") as f:
        f.write(MAKE)
    subprocess.run(
        [_bin("FreeCADCmd"), script], env=_env(tmp, BWJVP_FILE=path), timeout=240, capture_output=True
    )
    return path


class TestBWJointViewProvider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bwjvp")
        os.makedirs(os.path.join(self.tmp, "home"))

    def tearDown(self):
        if "BWJVP" in App.listDocuments():
            App.closeDocument("BWJVP")
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(App.GuiUp, "needs the GUI")
    def test_headless_joint_gets_view_provider_and_solves(self):
        import FreeCADGui as Gui
        import JointObject

        path = _make_headless_file(self.tmp)
        self.assertTrue(os.path.exists(path))
        doc = App.openDocument(path)
        asm = doc.getObject("Assembly")
        fixed = doc.getObject("Fixed")
        ground = doc.getObject("Ground")
        self.assertIsInstance(fixed.ViewObject.Proxy, JointObject.ViewProviderJoint)
        self.assertIsInstance(ground.ViewObject.Proxy, JointObject.ViewProviderGroundedJoint)
        self.assertFalse(Gui.getDocument(doc.Name).Modified, "opening the file must not modify it")
        Gui.ActiveDocument.setEdit(asm)
        self.assertEqual(asm.solve(), 0)
        Gui.ActiveDocument.resetEdit()

    @unittest.skipUnless(App.GuiUp, "needs the GUI")
    def test_redraw_without_view_proxy_is_quiet(self):
        import JointObject

        doc = App.newDocument("BWJVP")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        j = asm.newObject("Assembly::JointGroup", "Joints").newObject("App::FeaturePython", "J")
        JointObject.Joint(j, JointObject.JointTypes.index("Fixed"))
        self.assertFalse(hasattr(j.ViewObject.Proxy, "redrawJointPlacements"))  # nothing made a view provider
        j.Proxy.redrawJointPlacements(j)  # used to raise AttributeError
        self.assertFalse(JointObject.ensureViewProvider(doc.addObject("Part::Box", "NotAJoint")))

    @unittest.skipUnless(
        App.GuiUp and os.path.exists(_bin("FreeCAD")) and not os.environ.get("BW_NO_SUBPROCESS"),
        "needs the GUI binary",
    )
    def test_crash_repro_subprocess_exits_cleanly(self):
        # The pre-fix failure ended in SIGABRT, so run it in a process of its own.
        path = _make_headless_file(self.tmp)
        self.assertTrue(os.path.exists(path))
        out = os.path.join(self.tmp, "out.txt")
        script = os.path.join(self.tmp, "open.py")
        with open(script, "w") as f:
            f.write(OPEN)
        proc = subprocess.run(
            [_bin("FreeCAD"), script],
            env=_env(self.tmp, BWJVP_FILE=path, BWJVP_OUT=out),
            timeout=240,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace")[-500:])
        with open(out) as f:
            self.assertEqual(f.read(), "OK")


if __name__ == "__main__":
    unittest.main()
