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

from bruceworx import pdm, pdm_folder, vault_setup as vs, vault_simulations as vsim, vault_trees  # noqa: E402
from bruceworx.pdm_folder import FolderVault, LifecycleError  # noqa: E402
from bruceworx.pdm_workspace import Workspace  # noqa: E402

PART = "VL-01-01001-001"


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def vlsim(path, payload="v1", summary=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", "<Document/>")
        archive.writestr("payload.txt", payload)
        if summary is not None:
            archive.writestr(vsim.SUMMARY_ENTRY, json.dumps(summary))
    return path


def part_file(path, fingerprint=None, payload="p1"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", "<Document/>")
        archive.writestr("payload.txt", payload)
        if fingerprint:
            archive.writestr(vsim.GEOMETRY_ENTRY, json.dumps({"fingerprint": fingerprint}))
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

    def test_check_in_without_results_stores_the_changed_copy(self):
        key = vsim.new_simulation(self.v, PART, "Spin", lambda p, pn: vlsim(p, payload="small"))
        ws = Workspace(self.v)
        path = ws.checkout(key)
        vlsim(path, payload="BIG" * 1000, summary={"has_results": True})  # the user solved: a big file with results

        def strip(src, staging):
            os.makedirs(staging, exist_ok=True)
            out = os.path.join(staging, os.path.basename(src))
            vlsim(out, payload="small2", summary={"has_results": False, "results_stripped": True})
            return out

        ws.checkin(key, "draft", transform=strip)
        stored = os.path.join(self.v.root, self.v.latest_version(key).object_name)
        self.assertEqual(vsim.read_entry(stored, vsim.SUMMARY_ENTRY)["has_results"], False)
        self.assertLess(self.v.latest_version(key).size, 2000)
        self.assertEqual(ws.status(key).code, "up_to_date")  # the work copy is the stored copy
        kept = os.path.join(ws.root, ".results", key + ".vlsim")
        self.assertEqual(vsim.read_entry(kept, vsim.SUMMARY_ENTRY), {"has_results": True})
        self.assertFalse(os.path.exists(os.path.join(ws.root, ".bw", "stripped", key + ".vlsim")))

    def test_old_version_keeps_extension(self):
        key = vsim.new_simulation(self.v, PART, "Spin", lambda p, pn: vlsim(p))
        ws = Workspace(self.v)
        path = ws.get_revision(key, self.v.latest_version(key).sha256)
        self.assertTrue(path.endswith(".vlsim"), path)


class ShowTests(FolderTests):
    """Cards, rows and the "part changed" judgement, read from inside the files."""

    def setUp(self):
        super().setUp()
        os.environ["BW_SIM_CACHE"] = os.path.join(self.tmp, "cache")
        self.addCleanup(os.environ.pop, "BW_SIM_CACHE", None)

    def put(self, pn, path, comment="x"):
        self.v.checkout(pn)
        self.v.checkin(pn, path, comment)

    def make(self, geometry="aaa", results=True):
        summary = {"study": "Spin", "study_type": "static", "geometry": geometry, "has_results": results,
                   "max_vm": 252.5, "min_sf": 1.307, "mesh": {"nodes": 514619}, "material": {"name": "Al 2618"}}
        self.put(PART, part_file(os.path.join(self.tmp, PART + ".FCStd"), "aaa"))
        return vsim.new_simulation(self.v, PART, "Spin", lambda p, pn: vlsim(p, summary=summary))

    def test_current_then_part_changed(self):
        sim = self.make()
        s = vault_trees.structure(self.v, links=lambda pn: {})
        rows = vault_trees.simulations(s)
        self.assertEqual([(r["pn"], r["of"], r["judgement"], r["changed"]) for r in rows],
                         [(sim, PART, "current", False)])
        self.assertIn("252.5 MPa", rows[0]["result"])
        self.assertIn("514,619 nodes", rows[0]["study"])
        self.assertEqual(vault_trees.parts_with_changed_simulation(s), {})
        self.put(PART, part_file(os.path.join(self.tmp, PART + ".FCStd"), "bbb", "p2"))
        s = vault_trees.structure(self.v, links=lambda pn: {})
        self.assertEqual(vault_trees.simulations(s)[0]["judgement"], "part changed")
        self.assertEqual(vault_trees.parts_with_changed_simulation(s), {PART: [sim]})
        self.assertEqual(len(vault_trees.simulations(s, {PART})), 1)
        self.assertEqual(vault_trees.simulations(s, {"other"}), [])

    def test_unknown_without_a_part_fingerprint_and_without_results(self):
        self.v.checkout(PART)
        self.v.checkin(PART, part_file(os.path.join(self.tmp, "p.FCStd")), "old part")
        sim = vsim.new_simulation(self.v, PART, "Spin", lambda p, pn: vlsim(p, summary={"geometry": "aaa", "has_results": False,
                                                                                           "results_stripped": True, "max_vm": 10.0}))
        s = vault_trees.structure(self.v, links=lambda pn: {})
        row = vault_trees.simulations(s)[0]
        self.assertEqual(row["judgement"], "unknown")
        self.assertIn("results not included", row["result"])
        self.assertEqual(vsim.results_by_version(self.v, sim)[0][1], False)

    def test_simulation_hangs_under_its_part_in_the_tree(self):
        sim = self.make()
        s = vault_trees.structure(self.v, links=lambda pn: {})
        tree = vault_trees.file_tree(s)
        flat = [n["pn"] for top in tree for n in [top] + top["children"]] + [c["pn"] for top in tree for n in top["children"] for c in n["children"]]
        self.assertIn(sim, flat)
        self.assertIn(sim, vault_trees.contents(s, PART))

    def test_cache_serves_a_version_not_on_this_mac(self):
        sim = self.make()
        vsim.card(self.v, sim)  # fills the cache
        version = self.v.latest_version(sim)
        os.remove(os.path.join(self.v.root, version.object_name))
        self.assertEqual(vsim.card(self.v, sim)["summary"]["max_vm"], 252.5)


if __name__ == "__main__":
    unittest.main()
