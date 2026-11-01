# SPDX-License-Identifier: LGPL-2.1-or-later
"""The .vlsim simulation file (PDM-14): opens and saves as a normal document, keeps its extension."""

import os
import tempfile
import unittest

import FreeCAD

import bruceworx_vlsim as vl


class VlsimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-vlsim-")
        self.doc = FreeCAD.newDocument("VlsimT")
        self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        self.addCleanup(self._close)

    def _close(self):
        for name in list(FreeCAD.listDocuments()):
            if name.startswith(("VlsimT", "a", "b")) or True:
                try:
                    FreeCAD.closeDocument(name)
                except Exception:
                    pass

    def test_registered(self):
        vl.register()
        self.assertEqual(FreeCAD.getImportType("vlsim"), ["bruceworx_vlsim"])
        self.assertIn("vlsim", FreeCAD.getImportType())

    def test_save_as_keeps_extension_and_open(self):
        path = os.path.join(self.tmp, "VL-04-01001-001.vlsim")
        self.doc.saveAs(path)
        self.assertEqual(self.doc.FileName, path)
        self.assertTrue(os.path.isfile(path) and not os.path.exists(path + ".FCStd"))
        FreeCAD.closeDocument(self.doc.Name)
        doc = vl.open(path)
        self.assertEqual([o.Name for o in doc.Objects], ["Box"])
        doc.Box.Length = 20
        doc.recompute()
        doc.save()  # Save keeps the extension
        self.assertEqual(doc.FileName, path)
        self.assertEqual(sorted(n for n in os.listdir(self.tmp) if not n.endswith("FCBak")), ["VL-04-01001-001.vlsim"])
        self.assertIs(vl.open(path), doc)  # already open: the same document


if __name__ == "__main__":
    unittest.main()
