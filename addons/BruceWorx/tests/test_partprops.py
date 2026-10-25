# SPDX-License-Identifier: LGPL-2.1-or-later
"""Schema-driven part properties (BW-ASM-04 / PDM-02) without FreeCAD.
FreeCAD itself is covered by tests/freecad_partprops_smoke.py."""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import partprops, pdm  # noqa: E402
from bruceworx.pdm_folder import FolderVault  # noqa: E402
from bruceworx.partprops import Schema, SchemaError, load_schema  # noqa: E402


class FakePropObj:
    """Just enough of FreeCAD's dynamic-property API."""

    def __init__(self, **existing):
        self._types = {}
        self._modes = {}
        self._enums = {}
        for name, (typeid, value) in existing.items():
            self._types[name] = typeid
            object.__setattr__(self, name, value)

    @property
    def PropertiesList(self):
        return list(self._types)

    def addProperty(self, typeid, name, group="", doc=""):
        if name in self._types:
            raise ValueError(f"{name} exists")
        self._types[name] = typeid
        object.__setattr__(self, name, None)

    def removeProperty(self, name):
        del self._types[name]
        object.__delattr__(self, name)

    def getTypeIdOfProperty(self, name):
        return self._types[name]

    def getEnumerationsOfProperty(self, name):
        return self._enums.get(name)

    def setEditorMode(self, name, mode):
        self._modes[name] = list(mode)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if self._types.get(name) == "App::PropertyEnumeration":
            if isinstance(value, list):
                self._enums[name] = value
                value = value[0]
            elif value not in self._enums.get(name, []):
                raise ValueError(f"{value!r} not in {name}")
        object.__setattr__(self, name, value)


def _layer(*props, renamed=None):
    return {"schema": partprops.SCHEMA_ID, "version": 1, "properties": list(props), "renamed": renamed or {}}


class BuiltinSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema(user_layer=False)

    def test_builtin_layer_loads(self):
        names = list(self.schema.properties)
        for name in ("PartNumber", "Revision", "Description", "MaterialName", "Mass"):
            self.assertIn(name, names)
        self.assertEqual(self.schema.properties["Mass"].freecad_type, "App::PropertyMass")

    def test_bom_columns_are_dot_property_names(self):
        cols = self.schema.bom_columns()
        self.assertEqual(cols[:3], [".PartNumber", ".Revision", ".Description"])
        self.assertIn(".Mass", cols)
        self.assertNotIn(".Vendor", cols)

    def test_apply_adds_everything_once(self):
        obj = FakePropObj()
        report = self.schema.apply(obj, compute=False)
        self.assertEqual(sorted(report["added"]), sorted(self.schema.properties))
        self.assertEqual(obj.ItemType, "part")
        self.assertEqual(obj._modes["PartNumber"], ["ReadOnly"])
        self.assertEqual(obj._modes["Description"], [])
        again = self.schema.apply(obj, compute=False)
        self.assertEqual(again["added"], [])

    def test_missing_by_stage(self):
        obj = FakePropObj()
        self.schema.apply(obj, compute=False)
        self.assertEqual(self.schema.missing(obj, "submit"), ["Description", "MaterialName"])
        obj.Description = "Base plate"
        obj.MaterialName = "Aluminum-6061-T6"
        self.assertEqual(self.schema.missing(obj, "release"), [])


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema(user_layer=False)

    def test_new_property_needs_no_code(self):
        self.schema.add_layer(_layer(
            {"name": "Supplier", "type": "string", "bom": True, "required": "release"},
            {"name": "LeadTimeDays", "type": "int", "default": 10},
        ), "company")
        obj = FakePropObj()
        self.schema.apply(obj, compute=False)
        self.assertEqual(obj.LeadTimeDays, 10)
        self.assertIn(".Supplier", self.schema.bom_columns())
        self.assertIn("Supplier", self.schema.missing(obj, "release"))
        self.assertNotIn("Supplier", self.schema.missing(obj, "submit"))

    def test_layer_may_restyle_but_not_retype(self):
        self.schema.add_layer(_layer({"name": "Vendor", "bom": True, "required": "submit"}), "company")
        self.assertIn(".Vendor", self.schema.bom_columns())
        with self.assertRaises(SchemaError):
            self.schema.add_layer(_layer({"name": "Mass", "type": "float"}), "bad")

    def test_rename_moves_the_value(self):
        obj = FakePropObj(Finsh=("App::PropertyString", "Anodise clear"))
        self.schema.add_layer(_layer(renamed={"Finsh": "Finish"}), "fix-typo")
        report = self.schema.apply(obj, compute=False)
        self.assertEqual(obj.Finish, "Anodise clear")
        self.assertNotIn("Finsh", obj.PropertiesList)
        self.assertEqual(report["renamed"], ["Finsh->Finish"])

    def test_unknown_properties_survive(self):
        obj = FakePropObj(FromNewerSchema=("App::PropertyString", "keep me"))
        self.schema.apply(obj, compute=False)
        self.assertEqual(obj.FromNewerSchema, "keep me")

    def test_type_clash_is_reported_not_changed(self):
        obj = FakePropObj(Mass=("App::PropertyFloat", 1.5))
        report = self.schema.apply(obj, compute=False)
        self.assertEqual(obj.Mass, 1.5)
        self.assertTrue(report["conflicts"][0].startswith("Mass"))

    def test_enum_keeps_a_still_valid_choice(self):
        obj = FakePropObj()
        self.schema.apply(obj, compute=False)
        obj.ItemType = "cots"
        self.schema.add_layer(_layer({"name": "ItemType", "values": ["part", "assembly", "cots", "drawing", "kit"]}), "c")
        self.schema.apply(obj, compute=False)
        self.assertEqual(obj.ItemType, "cots")

    def test_bad_layers(self):
        for bad in (
            {"schema": "other"},
            _layer({"name": "x y", "type": "string"}),
            _layer({"name": "Thing", "type": "colour"}),
            _layer({"name": "Kind", "type": "enum"}),
            _layer({"name": "Stamp", "type": "string", "required": "shipping"}),
        ):
            with self.assertRaises(SchemaError):
                Schema().add_layer(bad, "bad")

    def test_load_schema_reads_extra_layer_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "part_properties.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(_layer({"name": "Coating", "type": "string", "bom": True}), handle)
            schema = load_schema([path], user_layer=False)
        self.assertIn(".Coating", schema.bom_columns())
        self.assertEqual(schema.layers[-1], path)


class FakeDoc:
    def __init__(self, *objects):
        self.Objects = list(objects)


def _part(name, part_number, **existing):
    obj = FakePropObj(PartNumber=("App::PropertyString", part_number), **existing)
    obj.Name = name
    return obj


class LifecycleStateTests(unittest.TestCase):
    """LifecycleState: the vault's states, read-only, written by the vault only."""

    def setUp(self):
        self.schema = load_schema(user_layer=False)

    def test_builtin_property_is_the_vaults_states(self):
        prop = self.schema.properties[partprops.STATE]
        self.assertEqual(prop.freecad_type, "App::PropertyEnumeration")
        self.assertEqual(tuple(prop.values), pdm.STATES)
        self.assertEqual(prop.default, pdm.INITIAL_STATE)
        self.assertEqual((prop.source, prop.readonly, prop.bom), ("pdm", True, False))

    def test_apply_gives_existing_parts_the_initial_state_read_only(self):
        obj = FakePropObj(PartNumber=("App::PropertyString", "P1"))
        report = self.schema.apply(obj, compute=False)
        self.assertIn(partprops.STATE, report["added"])
        self.assertEqual(obj.LifecycleState, pdm.WIP)
        self.assertEqual(obj._modes[partprops.STATE], ["ReadOnly"])
        obj.LifecycleState = pdm.RELEASED  # as the vault left it
        self.schema.apply(obj, compute=False)
        self.assertEqual(obj.LifecycleState, pdm.RELEASED)

    def test_layer_may_restyle_but_not_change_the_states(self):
        self.schema.add_layer(_layer({"name": partprops.STATE, "bom": True, "doc": "Ours"}), "company")
        self.assertIn("." + partprops.STATE, self.schema.bom_columns())
        for bad in ({"name": partprops.STATE, "values": ["Draft", "Done"]},
                    {"name": partprops.STATE, "type": "string"}):
            with self.assertRaises(SchemaError):
                self.schema.add_layer(_layer(bad), "bad")

    def test_property_hiding_a_freecad_attribute_is_refused(self):
        class WithStatus(FakePropObj):
            State = ["Touched"]  # FreeCAD's obj.State

        self.schema.add_layer(_layer({"name": "State", "type": "enum", "values": ["a", "b"]}), "company")
        obj = WithStatus()
        report = self.schema.apply(obj, compute=False)
        self.assertNotIn("State", obj.PropertiesList)
        self.assertTrue(report["conflicts"][0].startswith("State would hide"))

    def test_set_state_adds_writes_and_skips_unchanged(self):
        obj = FakePropObj()
        self.assertTrue(partprops.set_state(obj, pdm.IN_REVIEW, self.schema))
        self.assertEqual(obj.LifecycleState, pdm.IN_REVIEW)
        self.assertEqual(obj._modes[partprops.STATE], ["ReadOnly"])
        self.assertFalse(partprops.set_state(obj, pdm.IN_REVIEW, self.schema))
        with self.assertRaises(ValueError):
            partprops.set_state(obj, "Frozen", self.schema)
        self.assertTrue(partprops.set_state(FakePropObj(), pdm.WIP, self.schema))  # added counts
        clash = FakePropObj(LifecycleState=("App::PropertyString", "x"))
        with self.assertRaises(SchemaError):
            partprops.set_state(clash, pdm.WIP, self.schema)

    def test_set_state_brings_old_choices_up_to_date(self):
        obj = FakePropObj(LifecycleState=("App::PropertyEnumeration", None))
        obj.LifecycleState = ["WIP", "Released"]
        obj.LifecycleState = "Released"
        partprops.set_state(obj, pdm.OBSOLETE, self.schema)
        self.assertEqual(obj._enums[partprops.STATE], list(pdm.STATES))
        self.assertEqual(obj.LifecycleState, pdm.OBSOLETE)

    def test_sync_touches_only_parts_the_vault_knows(self):
        known, unknown, plain = _part("Plate", "P1"), _part("Bolt", "P9"), FakePropObj()
        plain.Name = "Origin"
        doc = FakeDoc(known, unknown, plain)
        self.assertEqual(partprops.sync_states(doc, {"P1": pdm.RELEASED}, self.schema), ["Plate"])
        self.assertEqual(known.LifecycleState, pdm.RELEASED)
        self.assertNotIn(partprops.STATE, unknown.PropertiesList)
        self.assertNotIn(partprops.STATE, plain.PropertiesList)
        self.assertEqual(partprops.sync_states(doc, {"P1": pdm.RELEASED}, self.schema), [])


class VaultWritesStateTests(unittest.TestCase):
    """The folder vault drives LifecycleState: on each transition, and on open."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-state-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = FolderVault.init(os.path.join(self.tmp, "v"), "States", actor="bruce@mac")
        self.part = _part("Plate", "P1")
        self.doc = FakeDoc(self.part)
        # attach_vault writes every open document: a stand-in FreeCAD with one.
        fake = types.SimpleNamespace(listDocuments=lambda: {"Doc": self.doc})
        saved = sys.modules.get("FreeCAD")
        sys.modules["FreeCAD"] = fake
        self.addCleanup(lambda: sys.modules.pop("FreeCAD") if saved is None
                        else sys.modules.__setitem__("FreeCAD", saved))

    def test_transitions_write_the_open_part(self):
        partprops.attach_vault(self.vault)
        self.vault.create_item("P1")
        self.assertEqual(self.part.LifecycleState, pdm.WIP)
        self.vault.submit("P1")
        self.assertEqual(self.part.LifecycleState, pdm.IN_REVIEW)
        self.vault.approve("P1", note="released")
        self.assertEqual(self.part.LifecycleState, pdm.RELEASED)
        self.assertEqual(self.vault.problems, [])

    def test_opening_a_document_takes_the_vaults_record(self):
        self.vault.create_item("P1")
        self.vault.submit("P1")
        self.assertEqual(partprops.sync_from_vault(self.doc, self.vault.root), ["Plate"])
        self.assertEqual(self.part.LifecycleState, pdm.IN_REVIEW)
        self.assertEqual(partprops.sync_from_vault(self.doc, ""), [])  # no vault set
        self.assertEqual(partprops.sync_from_vault(FakeDoc(), self.vault.root), [])


class FakeMaterial:
    def __init__(self, name):
        self.Name = name

    def getPhysicalValue(self, key):
        raise KeyError(key)  # no density: mass needs FreeCAD's units, see the smoke test


class FakeShape:
    Solids = [object()]
    Volume = 1000.0

    def isNull(self):
        return False


class FakeBody:
    PropertiesList = ["ShapeMaterial", "Shape"]

    def __init__(self, material):
        self.ShapeMaterial = FakeMaterial(material)
        self.Shape = FakeShape()


class ComputedTests(unittest.TestCase):
    def setUp(self):
        schema = load_schema(user_layer=False)
        self.computed = [p for p in schema.properties.values() if p.source.startswith("computed:")]

    def part(self, material, **props):
        obj = FakePropObj(**(props or {
            "MaterialName": ("App::PropertyString", "stale"),
            "Mass": ("App::PropertyMass", "12 mg"),
        }))
        obj.Group = [FakeBody(material)]
        return obj

    def test_default_material_counts_as_none(self):
        obj = self.part("Default")
        self.assertEqual(partprops.computed_material(obj), "")
        self.assertEqual(partprops.computed_mass(obj), "0.0 kg")  # not the placeholder's density

    def test_no_bodies_computes_nothing(self):
        obj = FakePropObj()
        obj.Group = []
        self.assertIsNone(partprops.computed_material(obj))
        self.assertIsNone(partprops.computed_mass(obj))

    def test_refresh_follows_the_material(self):
        obj = self.part("Default")
        self.assertEqual(sorted(partprops.refresh(obj, self.computed)), ["Mass", "MaterialName"])
        self.assertEqual((obj.MaterialName, obj.Mass), ("", "0.0 kg"))
        obj.Group[0].ShapeMaterial = FakeMaterial("Aluminum-6061-T6")
        partprops.refresh(obj, self.computed)
        self.assertEqual(obj.MaterialName, "Aluminum-6061-T6")

    def test_mass_override_replaces_volume_times_density(self):
        obj = self.part("Default")
        weighed = FakeBody("Default")
        weighed.PropertiesList = ["ShapeMaterial", "Shape", "MassOverride"]
        weighed.MassOverride = 0.0021  # kg, e.g. from component_masses.csv
        unset = FakeBody("Default")
        unset.PropertiesList = weighed.PropertiesList
        unset.MassOverride = 0.0
        obj.Group += [weighed, unset]
        self.assertEqual(partprops.computed_mass(obj), "0.0021 kg")

    def test_refresh_leaves_other_types_alone(self):
        obj = self.part("Steel", Mass=("App::PropertyFloat", 1.5))
        self.assertEqual(partprops.refresh(obj, self.computed), [])
        self.assertEqual(obj.Mass, 1.5)

    def test_apply_registers_its_computed_properties(self):
        schema = load_schema(user_layer=False)
        schema.apply(FakePropObj(), compute=False)
        self.assertIn("Mass", partprops._WATCHED)
        self.assertEqual(partprops._WATCHED["MaterialName"][1], schema.group)


class DensityTests(unittest.TestCase):
    def test_material_without_density_gives_no_mass(self):
        class Appearance:  # the Appearance library's "Steel": no physical properties
            def getPhysicalValue(self, name):
                return None

        self.assertIsNone(partprops._density_kg_per_mm3(Appearance()))


if __name__ == "__main__":
    unittest.main()
