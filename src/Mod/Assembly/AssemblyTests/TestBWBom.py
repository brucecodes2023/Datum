# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] BOM quantities for link arrays (e.g. BruceWorx patterns): each visible
# element counts as one of the linked part. ROU-04: the Length column of routed
# items (QuantityUnit, QuantityPer) and objects left out with BomExclude.

import unittest

import FreeCAD as App

import UtilsAssembly


class TestBWBom(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument(self.__class__.__name__)
        self.assembly = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.bolt = self.doc.addObject("Part::Cylinder", "Bolt")
        self.plate = self.assembly.newObject("Part::Box", "Plate")
        self.component = self.assembly.newObject("App::Link", "BoltLink")
        self.component.LinkedObject = self.bolt

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def array(self, count, hidden=()):
        array = self.assembly.newObject("App::Link", "BoltArray")
        array.LinkedObject = self.bolt
        array.ElementCount = count
        for index in hidden:
            array.setElementVisible(str(index), False)
        return array

    def quantities(self):
        bom = UtilsAssembly.getBomGroup(self.assembly).newObject("Assembly::BomObject", "BOM")
        bom.columnsNames = ["Index", "Name", "Quantity"]
        self.doc.recompute()
        rows = {}
        for row in range(2, 20):
            name = bom.getContents(f"B{row}").lstrip("'")
            if not name:
                break
            rows[name] = int(bom.getContents(f"C{row}"))
        return rows

    def test_array_counts_its_elements(self):
        self.array(5)
        self.assertEqual(self.quantities(), {"Plate": 1, "Bolt": 6})

    def test_hidden_elements_are_not_counted(self):
        self.array(5, hidden=(1, 3))
        self.assertEqual(self.quantities(), {"Plate": 1, "Bolt": 4})

    def test_array_first_in_the_list(self):
        self.doc.removeObject(self.component.Name)
        self.array(3)
        self.assertEqual(self.quantities(), {"Plate": 1, "Bolt": 3})

    def test_all_hidden_array_is_left_out(self):
        self.doc.removeObject(self.component.Name)
        self.array(2, hidden=(0, 1))
        self.assertEqual(self.quantities(), {"Plate": 1})

    def metered(self, name, per, unit="m"):
        """A routed item as BruceWorx makes one: QuantityUnit and QuantityPer."""
        obj = self.assembly.newObject("Part::FeaturePython", name)
        obj.addProperty("App::PropertyString", "QuantityUnit", "BruceWorx")
        obj.addProperty("App::PropertyFloat", "QuantityPer", "BruceWorx")
        obj.QuantityUnit = unit
        obj.QuantityPer = per
        return obj

    def rows(self, columns):
        bom = UtilsAssembly.getBomGroup(self.assembly).newObject("Assembly::BomObject", "BOM")
        bom.columnsNames = columns
        self.doc.recompute()
        out = {}
        for row in range(2, 20):
            name = bom.getContents(f"B{row}").lstrip("'")
            if not name:
                break
            out[name] = [bom.getContents(f"{c}{row}").lstrip("'") for c in "CD"[: len(columns) - 2]]
        return out

    def test_length_column(self):
        hose = self.metered("Hose", 0.21)
        twice = self.assembly.newObject("App::Link", "HoseLink")
        twice.LinkedObject = hose
        self.metered("Wire", 0.1)
        self.metered("Odd", 1.0, unit="")  # a counted part with the schema's empty unit
        rows = self.rows(["Index", "Name", "Quantity", "Length"])
        self.assertEqual(rows["Hose"], ["2", "0.42 m"])
        self.assertEqual(rows["Wire"], ["1", "0.1 m"])
        self.assertEqual(rows["Plate"], ["1", ""])
        self.assertEqual(rows["Bolt"], ["1", ""])
        self.assertEqual(rows["Odd"], ["1", ""])

    def test_length_column_of_an_array(self):
        hose = self.metered("Hose", 0.25)
        array = self.assembly.newObject("App::Link", "HoseArray")
        array.LinkedObject = hose
        array.ElementCount = 3
        self.assertEqual(self.rows(["Index", "Name", "Quantity", "Length"])["Hose"], ["4", "1 m"])

    def test_bom_exclude_leaves_an_object_out(self):
        path = self.assembly.newObject("Part::FeaturePython", "RoutePath")
        path.addProperty("App::PropertyBool", "BomExclude", "BruceWorx")
        path.BomExclude = True
        kept = self.assembly.newObject("Part::FeaturePython", "Kept")
        kept.addProperty("App::PropertyBool", "BomExclude", "BruceWorx")
        rows = self.rows(["Index", "Name", "Quantity"])
        self.assertNotIn("RoutePath", rows)
        self.assertIn("Kept", rows)
        self.assertEqual(rows["Plate"], ["1"])
