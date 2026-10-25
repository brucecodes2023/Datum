# SPDX-License-Identifier: LGPL-2.1-or-later
"""PDM-09 A, metric and inch (bruceworx_units): the two profiles, a document's units, the vault
default for new documents (New Part in Vault, import), the readouts, and (GUI) the [BW]
decimals switch in Gui/Application.cpp: an inch document shows three decimals, a mm one two.

Run: FreeCADCmd -t TestBruceWorxUnits; the GUI case runs in run_gui_tests.py.
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD

import bruceworx_defaults
import bruceworx_units as units
from bruceworx import vault_setup as vs


def _scratch_vault(tmp, unit, name="Shop"):
    """A vault with units=mm|in in a scratch folder; returns its ref path."""
    setup = vs.VaultSetup(name=name, folder=os.path.join(tmp, "vault-" + unit),
                          numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("LE")]),
                          standards={"units": unit})
    return vs.create_vault(setup, actor="bruce@mac").ref_path


class TestProfiles(unittest.TestCase):
    def test_two_profiles_only(self):
        self.assertEqual(sorted(units.PROFILES), ["in", "mm"])
        self.assertEqual((units.profile("mm").index, units.profile("mm").decimals), (0, 2))
        self.assertEqual((units.profile("in").index, units.profile("in").decimals), (3, 3))
        # The indices name FreeCAD's own schemas: Standard and Imperial decimal, never US customary.
        self.assertIn("mm", FreeCAD.Units.listSchemas(0))
        self.assertIn("Imperial decimal", FreeCAD.Units.listSchemas(3))

    def test_loose_text(self):
        for text in ("in", "inch", "Inches (in)", "Imperial decimal (in, lb)", "US customary (in, lb)",
                     "Building US (ft-in, sqft, cft)", "Imperial for Civil Eng (ft, lb, mph)"):
            self.assertEqual(units.norm_units(text), "in", text)
        for text in ("mm", "Millimetres (mm)", "Standard (mm, kg, s, °)", "MKS (m, kg, s, °)",
                     "Building Euro (cm, m², m³)", "FEM (mm, N, s)", "", None):
            self.assertEqual(units.norm_units(text), "mm", text)

    def test_every_freecad_schema_maps_by_index_and_by_text(self):
        for index, text in enumerate(FreeCAD.Units.listSchemas()):
            self.assertEqual(units.units_of_schema(index), units.units_of_schema(text), text)
        self.assertEqual(sorted(i for i in range(len(FreeCAD.Units.listSchemas()))
                                if units.units_of_schema(i) == "in"), sorted(units.INCH_SCHEMAS))

    def test_length_text_rounds_to_the_profile(self):
        self.assertEqual(units.length_text(6.35, "in"), "0.250 in")
        self.assertEqual(units.length_text(6.35, "mm"), "6.35 mm")
        self.assertEqual(units.length_text(3.2, "in"), "0.126 in")


class TestDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-pdm09-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.docs = []

    def tearDown(self):
        for doc in self.docs:
            try:
                FreeCAD.closeDocument(doc.Name)
            except Exception:  # noqa: BLE001
                pass

    def _doc(self, name="TestBruceWorxUnits"):
        doc = FreeCAD.newDocument(name, hidden=True)
        self.docs.append(doc)
        return doc

    def test_apply_units_sets_the_document_and_keeps_lengths(self):
        doc = self._doc()
        box = doc.addObject("Part::Box", "Box")
        box.Length = 50.8
        self.assertEqual(bruceworx_defaults.apply_units(doc, "in"), "in")
        self.assertEqual(str(doc.UnitSystem), FreeCAD.Units.listSchemas(3))
        self.assertEqual(units.document_units(doc), "in")
        self.assertAlmostEqual(box.Length.Value, 50.8)  # stored millimetres, untouched
        self.assertEqual(units.apply_units(doc, "mm"), "mm")
        self.assertEqual(str(doc.UnitSystem), FreeCAD.Units.listSchemas(0))
        self.assertEqual(units.document_units(doc), "mm")

    def test_units_survive_save_and_reopen(self):
        doc = self._doc()
        units.apply_units(doc, "in")
        path = os.path.join(self.tmp, "inch.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        self.docs.remove(doc)
        again = FreeCAD.openDocument(path, hidden=True)
        self.docs.append(again)
        self.assertEqual(units.document_units(again), "in")

    def test_vault_default_and_project_hook(self):
        ref_in = _scratch_vault(self.tmp, "in")
        ref_mm = _scratch_vault(self.tmp, "mm", "Metric")
        self.assertEqual(units.vault_units(ref_in), "in")
        self.assertEqual(units.vault_units(ref_mm), "mm")
        self.assertEqual(units.default_units(ref_in), "in")
        self.assertEqual(units.status_suffix(ref_in), " · inch")
        self.assertEqual(units.status_suffix(ref_mm), " · mm")
        self.assertEqual(units.status_suffix(""), "")
        units.DEFAULT_HOOKS.append(lambda _doc: "mm")  # a project's units win over the vault's
        try:
            self.assertEqual(units.default_units(ref_in), "mm")
        finally:
            units.DEFAULT_HOOKS.pop()
        doc = self._doc()
        units.apply_units(doc, "in")
        self.assertEqual(units.badge(doc, ref_mm), "in")
        self.assertEqual(units.badge(doc, ref_in), "")
        self.assertEqual(units.badge(doc, ""), "")

    def test_new_part_in_an_inch_vault_is_inch(self):
        import bruceworx_vaultclient as vc

        ref = _scratch_vault(self.tmp, "in")
        root = os.path.dirname(ref)
        path = os.path.join(self.tmp, "LE-01-00001-001.FCStd")
        vc.make_new_file(path, "LE-01-00001-001", "part", "Inch plate", root)
        doc = FreeCAD.openDocument(path, hidden=True)
        self.docs.append(doc)
        self.assertEqual(units.document_units(doc), "in")
        self.assertEqual(doc.getObject("Part").PartNumber, "LE-01-00001-001")
        ref_mm = _scratch_vault(self.tmp, "mm", "Metric")
        path2 = os.path.join(self.tmp, "LE-01-00002-001.FCStd")
        vc.make_new_file(path2, "LE-01-00002-001", "assembly", "Metric frame", os.path.dirname(ref_mm))
        doc2 = FreeCAD.openDocument(path2, hidden=True)
        self.docs.append(doc2)
        self.assertEqual(units.document_units(doc2), "mm")

    def test_import_writer_takes_the_vault_units(self):
        from bruceworx import freecad_io

        doc = self._doc()
        part = doc.addObject("App::Part", "Part")
        part.addObject(doc.addObject("Part::Box", "Box"))
        path = os.path.join(self.tmp, "imported.FCStd")
        doc.saveAs(path)
        FreeCAD.closeDocument(doc.Name)
        self.docs.remove(doc)
        freecad_io.write_part_number(path, "LE-01-00003-001", units="in")
        again = FreeCAD.openDocument(path, hidden=True)
        self.docs.append(again)
        self.assertEqual(units.document_units(again), "in")
        self.assertEqual(again.getObject("Part").PartNumber, "LE-01-00003-001")


@unittest.skipUnless(FreeCAD.GuiUp, "the decimals switch runs in Gui/Application.cpp")
class TestDecimalsSwitch(unittest.TestCase):
    """[BW] Gui/Application.cpp: activating an inch document shows 3 decimals, a mm one 2."""

    def setUp(self):
        self.group = FreeCAD.ParamGet(units.UNITS_PATH)
        self.saved = {k: self.group.GetInt(k, -1) for k in ("DecimalsMetric", "DecimalsInch", "Decimals")}
        self.saved_ignore = self.group.GetBool("IgnoreProjectSchema", False)
        self.group.SetInt("DecimalsMetric", 2)
        self.group.SetInt("DecimalsInch", 3)
        self.group.SetBool("IgnoreProjectSchema", False)
        self.docs = [FreeCAD.newDocument("BWUnitsMM"), FreeCAD.newDocument("BWUnitsIN")]
        units.apply_units(self.docs[0], "mm")
        units.apply_units(self.docs[1], "in")

    def tearDown(self):
        for doc in self.docs:
            FreeCAD.closeDocument(doc.Name)
        for key, value in self.saved.items():
            if value < 0:
                self.group.RemInt(key)
            else:
                self.group.SetInt(key, value)
        self.group.SetBool("IgnoreProjectSchema", self.saved_ignore)

    def _shown(self):
        return FreeCAD.Units.Quantity(6.35, FreeCAD.Units.Length).UserString.replace("\u202f", " ")

    def test_decimals_follow_the_active_document(self):
        FreeCAD.setActiveDocument(self.docs[1].Name)
        self.assertEqual(FreeCAD.Units.getSchema(), 3)
        self.assertEqual(self._shown(), "0.250 in")
        FreeCAD.setActiveDocument(self.docs[0].Name)
        self.assertEqual(FreeCAD.Units.getSchema(), 0)
        self.assertEqual(self._shown(), "6.35 mm")
        # apply_units on the active document switches at once (no re-activation needed)
        units.apply_units(self.docs[0], "in")
        self.assertEqual(self._shown(), "0.250 in")
        self.assertEqual(units.badge(self.docs[0], ""), "")  # no vault: no badge
