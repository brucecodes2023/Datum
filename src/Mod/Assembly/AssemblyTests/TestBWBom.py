# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] BOM quantities for link arrays (e.g. BruceWorx patterns): each visible
# element counts as one of the linked part.

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
