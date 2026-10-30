# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Keystone material library, headless (SIM-01).

Every card loads through FreeCAD's Materials API from the library named
"Keystone" (registered by Init.py), has density, E, nu, yield and UTS with
yield <= UTS and 0 < nu < 0.5, a colour and thermal values; the folder and the
permanent UUID table agree; no Keystone UUID is used by any other card.
"""

import glob
import math
import os
import unittest

import FreeCAD as App

import bruceworx_materials as km


class TestKeystoneLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mm = km.manager()
        cls.cards = km.cards()

    def test_library_registered_as_keystone(self):
        libraries = {lib[0]: lib[1] for lib in self.mm.MaterialLibraries}
        self.assertIn("Keystone", libraries)
        self.assertEqual(os.path.realpath(libraries["Keystone"]), os.path.realpath(km.LIBRARY_DIR))
        group = App.ParamGet(f"{km.MODULES_PARAM}/Keystone")
        self.assertTrue(group.GetBool("ModuleReadOnly", False))

    def test_folder_matches_uuid_table(self):
        on_disk = sorted(os.path.relpath(p, km.LIBRARY_DIR).replace(os.sep, "/")
                         for p in glob.glob(os.path.join(km.LIBRARY_DIR, "*", "*.FCMat")))
        self.assertEqual(on_disk, sorted(km.CARDS.values()))
        self.assertEqual(len(set(km.CARDS.values())), len(km.CARDS))

    def test_every_card_loads_from_the_keystone_library(self):
        in_library = {uuid for uuid, mat in self.mm.Materials.items() if mat.LibraryName == "Keystone"}
        self.assertEqual(in_library, set(km.CARDS))
        self.assertEqual(len(self.cards), len(km.CARDS))
        for card in self.cards:
            self.assertEqual(card["library"], "Keystone", card["name"])
            mat = self.mm.getMaterial(card["uuid"])
            self.assertEqual(mat.Name + ".FCMat", os.path.basename(km.CARDS[card["uuid"]]))

    def test_uuids_unique_across_all_libraries(self):
        names = {}
        for path in glob.glob(os.path.join(km.LIBRARY_DIR, "*", "*.FCMat")):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("UUID:") and line.startswith("  UUID"):
                        uuid = line.split(":", 1)[1].strip().strip("\"'")
                        self.assertNotIn(uuid, names, f"{path} repeats {names.get(uuid)}")
                        names[uuid] = path
                        break
        self.assertEqual(set(names), set(km.CARDS))
        others = {uuid for uuid, mat in self.mm.Materials.items() if mat.LibraryName != "Keystone"}
        self.assertFalse(others & set(km.CARDS), "a Keystone UUID is used by another library's card")

    def test_engineering_values(self):
        for c in self.cards:
            with self.subTest(card=c["name"]):
                for key in ("density", "E", "nu", "yield", "uts", "elongation", "k", "cp", "alpha"):
                    self.assertIsNotNone(c[key], key)
                    self.assertFalse(math.isnan(c[key]), key)
                    self.assertGreater(c[key], 0, key)
                self.assertTrue(300 < c["density"] < 9000)
                self.assertTrue(0 < c["nu"] < 0.5)
                self.assertLessEqual(c["yield"], c["uts"])
                if c["elongation"] < 300:  # catches unit slips; an elastomer (TPU) is stronger than its modulus
                    self.assertLess(c["yield"], c["E"] / 20)
                self.assertIn(c["basis"], ("typical", "minimum", "approximate"))
                self.assertTrue(c["source"])
                self.assertIn(c["family"], km.FAMILIES)
                if c["family"] == "Printed":
                    self.assertIsNotNone(c["z_strength"])
                    self.assertLessEqual(c["z_strength"], c["yield"])
                if c["family"] == "Metals":
                    self.assertIsNotNone(c["G"])
                if c["brittle"]:
                    self.assertEqual(c["yield"], c["uts"])

    def test_every_card_has_a_colour(self):
        for uuid in km.CARDS:
            mat = self.mm.getMaterial(uuid)
            with self.subTest(card=mat.Name):
                self.assertTrue(mat.hasAppearanceModel("f006c7e4-35b7-43d5-bbf9-c5d572309e6e"))
                rgba = [float(v) for v in str(mat.getAppearanceValue("DiffuseColor")).strip("()").split(",")]
                self.assertEqual(len(rgba), 4)
                self.assertTrue(all(0.0 <= v <= 1.0 for v in rgba))

    def test_lookup_by_name_and_uuid(self):
        alu = km.card("aluminium 6061-t6")
        self.assertIsNotNone(alu)
        self.assertEqual(alu["uuid"], "6c52eca0-5547-4e8f-b587-bf487bb48f6b")
        self.assertAlmostEqual(alu["E"], 68900.0, places=0)
        self.assertAlmostEqual(alu["density"], 2700.0, places=3)
        self.assertEqual(alu["yield"], 241.0)
        self.assertEqual(alu["basis"], "minimum")
        petg = km.card("b4e991ce-42af-48ad-acbc-40e7f1de3a52")
        self.assertEqual(petg["name"], "PETG printed")
        self.assertEqual(petg["z_strength"], 39.0)
        self.assertIsNone(km.card("no such material"))

    def test_a_body_with_a_keystone_material_has_mass(self):
        import Part

        doc = App.newDocument("KeystoneMass")
        try:
            body = doc.addObject("Part::Feature", "Block")
            body.Shape = Part.makeBox(100, 50, 10)
            body.ShapeMaterial = km.material("Aluminium 6061-T6")
            doc.recompute()
            density = body.ShapeMaterial.getPhysicalValue("Density").getValueAs("kg/mm^3")
            self.assertAlmostEqual(float(density) * body.Shape.Volume, 0.135, places=4)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
