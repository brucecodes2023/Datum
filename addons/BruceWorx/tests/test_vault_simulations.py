# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulations in the vault (PDM-14): the item type, simulation_of, the .vlsim work file, the document type."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bruceworx import pdm, pdm_folder, vault_setup as vs, vault_simulations as vsim  # noqa: E402
from bruceworx.pdm_folder import FolderVault, LifecycleError  # noqa: E402
from bruceworx.pdm_workspace import Workspace  # noqa: E402

PART = "VL-01-01001-001"


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def vlsim(path, payload="v1"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", "<Document/>")
        archive.writestr("payload.txt", payload)
    return path


class ModelTests(unittest.TestCase):
    def vault(self):
        v = pdm.Vault()
        v.create_item(PART, "part", "Impeller")
        return v

    def test_simulation_names_its_part(self):
        v = self.vault()
        item = v.create_item("VL-04-01001-001", "simulation", "Spin", simulation_of=PART)
        self.assertEqual(item.simulation_of, PART)
        self.assertEqual([i.part_number for i in v.simulations_of(PART)], ["VL-04-01001-001"])
        with self.assertRaises(LifecycleError):
            v.create_item("VL-04-01002-001", "simulation", "x")
        with self.assertRaises(LifecycleError):
            v.create_item("VL-04-01002-001", "simulation", "x", simulation_of="VL-01-09999-001")
        with self.assertRaises(LifecycleError):  # a drawing is not analysed
            v.create_item("VL-03-01001-001", "drawing", "d", drawing_of=PART)
            v.create_item("VL-04-01002-001", "simulation", "x", simulation_of="VL-03-01001-001")
        with self.assertRaises(LifecycleError):
            v.create_item("VL-01-01002-001", "part", "p", simulation_of=PART)

    def test_renumber_keeps_the_link(self):
        v = self.vault()
        v.create_item("VL-04-01001-001", "simulation", "Spin", simulation_of=PART)
        v.renumber(PART, "VL-01-01190-001", "test")
        self.assertEqual(v.items["VL-04-01001-001"].simulation_of, "VL-01-01190-001")


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bw-sim-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        setup = vs.VaultSetup(name="V", folder=os.path.join(self.tmp, "Vault", "V"),
                              numbering=vs.NumberingSetup(programs=[vs.ProgramSetup("VL")]))
        self.v = vs.create_vault(setup, actor="bruce@mac")
        self.v.create_item(PART, "part", "Impeller")

    def reopen(self):
        return FolderVault(self.v.root, actor="bruce@mac")

    def journal(self):
        with open(self.v.journal_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_journal_and_replay(self):
        self.v.create_item("VL-04-01001-001", "simulation", "Spin", simulation_of=PART)
        events = [e for e in self.journal() if e["op"] == "create_item"]
        part_args, sim_args = events[-2]["args"], events[-1]["args"]
        self.assertNotIn("simulation_of", part_args)  # older journals and readers see nothing new on other items
        self.assertEqual(sim_args["simulation_of"], PART)
        again = self.reopen()
        self.assertEqual(again.items["VL-04-01001-001"].simulation_of, PART)
        self.assertEqual(again.problems, [])

    def test_streamed_store(self):
        path = vlsim(os.path.join(self.tmp, "a.vlsim"))
        with mock.patch.object(pdm_folder.shutil, "copyfileobj", wraps=pdm_folder.shutil.copyfileobj) as copy:
            record = self.v.store_object(path)
        self.assertEqual(copy.call_count, 1)
        stored = os.path.join(self.v.root, pdm_folder.object_name(record["sha256"], "a.vlsim"))
        self.assertEqual(pdm_folder.sha256_of(stored), record["sha256"])
        self.assertEqual(self.v.store_object(path), record)  # write-once: the second call copies nothing
        self.assertFalse([n for n in os.listdir(os.path.dirname(stored)) if n.endswith(".tmp")])

    def test_new_vault_has_type_04(self):
        self.assertEqual(vs.load_settings(self.v.root).document_types.get("04"), "Simulation")
        self.assertEqual(vsim.sim_document_type(self.v.root), "04")

    def test_old_vault_gets_type_04(self):
        path = os.path.join(self.v.root, "document_types.json")
        data = _load(path)
        data["types"].pop("04", None)
        _save(path, data)
        num = vs.numbering_path(self.v.root)
        nd = _load(num)
        for entry in nd["programs"].values():
            entry["counters"].pop("04", None)
        _save(num, nd)
        self.assertNotIn("04", vs.load_settings(self.v.root).document_types)
        self.assertEqual(vsim.sim_document_type(self.v.root), "04")
        self.assertEqual(vs.load_settings(self.v.root).document_types["04"], "Simulation")

    def test_new_simulation_and_work_path(self):
        made = []

        def make(path, pn):
            made.append(pn)
            vlsim(path)

        key = vsim.new_simulation(self.v, PART, "Spin stress", make)
        self.assertEqual(key, "VL-04-01001-001")
        self.assertEqual(made, [key])
        item = self.v.items[key]
        self.assertEqual((item.item_type, item.simulation_of), ("simulation", PART))
        version = self.v.latest_version(key)
        self.assertEqual(version.filename, key + ".vlsim")
        self.assertFalse(os.path.exists(os.path.join(self.v.root, ".import")))
        ws = Workspace(self.v)
        self.assertTrue(ws.path(key).endswith(key + ".vlsim"))
        self.assertTrue(ws.path(PART).endswith(PART + ".FCStd"))
        got = ws.checkout(key)
        self.assertTrue(got.endswith(key + ".vlsim") and os.path.isfile(got))
        with self.assertRaises(ValueError):
            vsim.new_simulation(self.v, "VL-01-09999-001", "x", make)

    def test_old_version_keeps_extension(self):
        key = vsim.new_simulation(self.v, PART, "Spin", lambda p, pn: vlsim(p))
        ws = Workspace(self.v)
        path = ws.get_revision(key, self.v.latest_version(key).sha256)
        self.assertTrue(path.endswith(".vlsim"), path)


if __name__ == "__main__":
    unittest.main()
