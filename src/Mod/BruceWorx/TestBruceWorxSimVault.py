# SPDX-License-Identifier: LGPL-2.1-or-later
"""Simulations in the vault, FreeCAD side (PDM-14): the copy of the solid, the summary at save, results left out."""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_preview as preview
import bruceworx_sim as sim
import bruceworx_sim_model as model
import bruceworx_sim_vault as sv
from bruceworx import vault_simulations as vs


BOX_40_20_10 = "7c4ec29572c5"  # H("geo", geometry_settings) of a 40 x 20 x 10 box, recorded with the code before PDM-14 follow-up 3


def close_all():
    for doc in list(App.listDocuments().values()):
        App.closeDocument(doc.Name)


class SimVaultTests(unittest.TestCase):
    def setUp(self):
        close_all()
        self.tmp = tempfile.mkdtemp(prefix="bw-simvault-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(close_all)

    def part_file(self):
        doc = App.newDocument("PartDoc")
        box = doc.addObject("Part::Box", "Block")
        box.Length, box.Width, box.Height = 40, 20, 10
        hole = doc.addObject("Part::Cylinder", "Hole")
        hole.Radius, hole.Height = 3, 10
        hole.Placement.Base = App.Vector(20, 10, 0)
        cut = doc.addObject("Part::Cut", "Cut")
        cut.Base, cut.Tool = box, hole
        doc.recompute()
        path = os.path.join(self.tmp, "VL-01-01001-001.FCStd")
        doc.saveAs(path)
        return doc, path

    def test_part_stamp_and_copy_agree(self):
        doc, path = self.part_file()
        geo = sv.geometry_of_doc(doc)
        self.assertEqual(len(geo["fingerprint"]), 12)
        self.assertTrue(preview.stamp_saved(doc, path, "part"))
        self.assertEqual(vs.read_entry(path, vs.GEOMETRY_ENTRY)["fingerprint"], geo["fingerprint"])
        out = os.path.join(self.tmp, "VL-04-01001-001.vlsim")
        fp = sv.make_simulation_file(path, out, "VL-01-01001-001", "abc")
        self.assertEqual(fp, geo["fingerprint"])
        summary = vs.read_entry(out, vs.SUMMARY_ENTRY)
        self.assertEqual(summary["source"]["part"], "VL-01-01001-001")
        self.assertEqual(summary["source"]["fingerprint"], geo["fingerprint"])
        self.assertEqual(vs.judge(summary, geo), vs.CURRENT)
        # a picture and the mesh are in the file too
        import zipfile

        names = zipfile.ZipFile(out).namelist()
        self.assertIn("bruceworx/preview.bin", names)
        self.assertIn("thumbnails/Thumbnail.png", names)

    def test_single_solid_fingerprint_is_unchanged(self):
        """One body gives exactly the shape's own numbers; the value below was recorded before the change."""
        close_all()
        doc = App.newDocument("One")
        box = doc.addObject("Part::Box", "Block")
        box.Length, box.Width, box.Height = 40, 20, 10
        doc.recompute()
        self.assertEqual(sv.fingerprint_of_shape(box.Shape), sv.geometry_of_doc(doc)["fingerprint"])
        self.assertEqual(sv.geometry_of_doc(doc)["settings"], model.geometry_settings(box.Shape))
        self.assertEqual(sv.geometry_of_doc(doc)["fingerprint"], BOX_40_20_10)

    def test_several_bodies_are_summed(self):
        close_all()
        doc = App.newDocument("Two")
        a = doc.addObject("Part::Box", "A")
        a.Length, a.Width, a.Height = 40, 20, 10
        b = doc.addObject("Part::Box", "B")
        b.Length, b.Width, b.Height = 10, 10, 10
        b.Placement.Base = App.Vector(100, 0, 0)
        doc.recompute()
        one = sv.geometry_of_doc(doc)
        settings = one["settings"]
        self.assertEqual(settings["solids"], 2)
        self.assertEqual(settings["faces"], 12)
        self.assertEqual(float(settings["volume"]), 8000.0 + 1000.0)
        self.assertEqual(float(settings["area"]), 2 * (800 + 400 + 200) + 600.0)
        self.assertEqual([float(v) for v in settings["bbox"]], [0, 0, 0, 110, 20, 10])
        b.Visibility = False  # headless: no view object, so hiding is only a GUI matter
        # the same two bodies in the other order give the same fingerprint
        self.assertEqual(one["fingerprint"], sv.fingerprint_of_shape(
            __import__("Part").makeCompound([b.Shape, a.Shape])))

    def test_refresh_pictures_records_a_missing_fingerprint(self):
        from types import SimpleNamespace

        doc, path = self.part_file()  # saved without the PDM-14 entry: a part checked in before it
        os.makedirs(os.path.join(self.tmp, "vault", "objects"))
        stored = os.path.join(self.tmp, "vault", "objects", "v1.FCStd")
        shutil.copy(path, stored)
        cache = os.path.join(self.tmp, "cache")
        old = os.environ.get("BW_SIM_CACHE")
        os.environ["BW_SIM_CACHE"] = cache
        self.addCleanup(lambda: os.environ.pop("BW_SIM_CACHE") if old is None else os.environ.update(BW_SIM_CACHE=old))
        self.addCleanup(setattr, preview, "entries_for", preview.entries_for)
        preview.entries_for = lambda _doc: {}  # no 3D view here: only the fingerprint is under test
        latest = SimpleNamespace(sha256="v1sha", object_name="objects/v1.FCStd", filename="P.FCStd")
        vault = SimpleNamespace(root=os.path.join(self.tmp, "vault"), items={"P": SimpleNamespace(item_type="part")},
                                latest_version=lambda pn: latest)
        work = SimpleNamespace(path=lambda pn: os.path.join(self.tmp, "none.FCStd"), local_hash=lambda p: "")
        s = SimpleNamespace(vault=vault, work=work)
        self.assertIsNone(vs.geometry_of_version(vault, latest))
        self.assertTrue(preview._geometry_missing(s, latest))
        self.assertTrue(preview.needs_picture(s, "P"))
        preview.refresh_one(s, "P")
        recorded = vs.geometry_of_version(vault, latest)
        self.assertEqual(recorded["fingerprint"], sv.geometry_of_doc(doc)["fingerprint"])
        self.assertFalse(preview._geometry_missing(s, latest))
        # a study of that part, made before, now reads current instead of unknown
        summary = {"geometry": recorded["fingerprint"]}
        self.assertEqual(vs.judge(summary, recorded), vs.CURRENT)

    def test_study_fingerprint_is_the_parts(self):
        doc, path = self.part_file()
        out = os.path.join(self.tmp, "VL-04-01001-001.vlsim")
        sv.make_simulation_file(path, out, "VL-01-01001-001", "abc")
        sdoc = App.openDocument(out)
        plan = sim.StudyPlan(name="Spin", model=sdoc.getObject("Model"),
                             material={"uuid": "", "name": "Steel", "library": "", "E": 210000.0, "nu": 0.3,
                                       "yield": 355.0, "uts": None, "density": 7850.0}, max_size=6.0)
        analysis = sim.new_study(sdoc, plan)["analysis"]
        self.assertEqual(model.fingerprints(analysis)["geo/part"], sv.geometry_of_doc(doc)["fingerprint"])
        sdoc.save()
        self.assertTrue(preview.stamp_saved(sdoc, out, "simulation"))
        summary = vs.read_entry(out, vs.SUMMARY_ENTRY)
        self.assertEqual((summary["study"], summary["study_type"], summary["has_results"]), ("Spin", "static", False))
        self.assertEqual(summary["geometry"], sv.geometry_of_doc(doc)["fingerprint"])
        self.assertEqual(summary["source"]["part"], "VL-01-01001-001")  # the source survives a FreeCAD save

    def test_changed_part_is_part_changed(self):
        doc, path = self.part_file()
        out = os.path.join(self.tmp, "a.vlsim")
        sv.make_simulation_file(path, out, "P", "abc")
        doc.getObject("Block").Length = 45
        doc.recompute()
        self.assertEqual(vs.judge(vs.read_entry(out, vs.SUMMARY_ENTRY), sv.geometry_of_doc(doc)), vs.CHANGED)

    @unittest.skipUnless(sim.find_ccx(), "CalculiX is not installed")
    def test_results_summary_and_stripped_copy(self):
        doc, _objs, _theory = sim.cantilever()
        analysis = sim.studies(doc)[0]
        sim.mesh_study(analysis)
        sim.solve(analysis, working_dir=os.path.join(self.tmp, "ccx"))
        out = os.path.join(self.tmp, "VL-04-01002-001.vlsim")
        doc.saveAs(out)
        self.assertTrue(preview.stamp_saved(doc, out, "simulation"))
        full = vs.read_entry(out, vs.SUMMARY_ENTRY)
        self.assertTrue(full["has_results"])
        self.assertGreater(full["max_vm"], 0)
        self.assertGreater(full["mesh"]["nodes"], 0)
        staged = sv.stripped_copy(out, os.path.join(self.tmp, "staging"))
        self.assertEqual(os.path.basename(staged), os.path.basename(out))
        small = vs.read_entry(staged, vs.SUMMARY_ENTRY)
        self.assertEqual((small["has_results"], small["results_stripped"], small["max_vm"]), (False, True, full["max_vm"]))
        self.assertLess(os.path.getsize(staged), os.path.getsize(out))
        again = App.openDocument(staged)
        self.assertEqual(sv.result_objects(again), [])
        self.assertTrue(sim.studies(again))
        self.assertTrue(sim.members(sim.studies(again)[0])["mesh"] is not None)
        # a stamp that runs late (the save observer's timer) after a summary was remembered before the copy was stripped
        sv._PREVIOUS[os.path.realpath(staged)] = full
        preview.stamp_saved(again, staged, "simulation")
        late = vs.read_entry(staged, vs.SUMMARY_ENTRY)
        self.assertEqual((late["results_stripped"], late["max_vm"]), (True, full["max_vm"]))
        # saving the stripped document keeps the numbers it had
        sv.remember_previous(staged)  # what the vault observer does just before a save
        again.save()
        preview.stamp_saved(again, staged, "simulation")
        self.assertEqual(vs.read_entry(staged, vs.SUMMARY_ENTRY)["max_vm"], full["max_vm"])


if __name__ == "__main__":
    unittest.main()
