# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-04: the CalculiX deck of a multi-solid model is the same with the fast solid element search as with the node search.

The node search (getNodesBySolid) measures every node against every solid and takes minutes on 150k nodes. The fast
path reads the solids from the mesh pieces (the volume mesh cut at its surface triangles, meshtools.get_solid_femelements_fast).
These tests build a small shaft / disc / cap compound, mesh it second order and write it both ways: the element sets, the
node sets, the leftover handling (a mid-edge node pushed off its solid) and the whole .inp must match exactly.
"""

import copy
import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import bruceworx_sim as sim


def build_shape():
    """shaft, disc on the shaft and cap on its end: three solids that share faces (a CompSolid)"""
    import Part
    from BOPTools import SplitAPI

    V = App.Vector
    shaft = Part.makeCylinder(4.0, 30, V(0, 0, 0))
    disc = Part.makeCylinder(15, 5, V(0, 0, 10)).cut(Part.makeCylinder(4.0, 5, V(0, 0, 10)))
    cap = Part.makeCylinder(6, 4, V(0, 0, 30))
    return SplitAPI.booleanFragments([shaft, disc, cap], "CompSolid", 1e-4)


def make_study(doc):
    import ObjectsFem

    feature = doc.addObject("Part::Feature", "Rotor")
    feature.Shape = build_shape()
    doc.recompute()
    study = sim.new_study(doc, sim.StudyPlan(model=feature, max_size=3.0))
    analysis = study["analysis"]
    sim.mesh_study(analysis)
    doc.removeObject(study["material"].Name)
    for k in (1, 2, 3):
        mat = ObjectsFem.makeMaterialSolid(doc, f"Mat{k}")
        mat.Material = {
            "Name": f"M{k}",
            "YoungsModulus": f"{200000 + k * 1000} MPa",
            "PoissonRatio": "0.3",
            "Density": f"{7800 + k} kg/m^3",
        }
        mat.References = [(feature, f"Solid{k}")]
        analysis.addObject(mat)
    fixed = ObjectsFem.makeConstraintFixed(doc, "Fix")
    fixed.References = [(feature, "Face1")]
    analysis.addObject(fixed)
    force = ObjectsFem.makeConstraintForce(doc, "Frc")
    force.References = [(feature, "Face2")]
    force.Force = 100
    analysis.addObject(force)
    doc.recompute()
    return feature, study


class TestDeckWrite(unittest.TestCase):
    def setUp(self):
        from femmesh import meshtools

        self.meshtools = meshtools
        self.doc = App.newDocument("BW_DeckWrite")
        self.feature, self.study = make_study(self.doc)
        self.tmp = tempfile.mkdtemp(prefix="bw_deck_")

    def tearDown(self):
        self.meshtools.FAST_SOLID_SETS = True
        App.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def objs(self):
        return [{"Object": o} for o in self.study["analysis"].Group if o.Name.startswith("Mat")]

    def sets(self, fem, fast):
        mt = self.meshtools
        mt.FAST_SOLID_SETS = fast
        mt.FAST_LAST = {}
        table = mt.get_femelement_table(fem)
        nodes_table = mt.get_femnodes_ele_table(fem.Nodes, table)
        objs = self.objs()
        mt.LAST_UNREFERENCED = {}
        ok = mt.get_femelement_sets(fem, table, objs, nodes_table)
        return ok, [o["FEMElements"] for o in objs], copy.deepcopy(mt.LAST_UNREFERENCED), dict(mt.FAST_LAST)

    def test_element_sets_are_identical(self):
        fem = self.study["mesh"].FemMesh
        self.assertGreater(fem.VolumeCount, 100)
        ok_old, old, left_old, last_old = self.sets(fem, False)
        ok_new, new, left_new, last_new = self.sets(fem, True)
        self.assertTrue(ok_old and ok_new)
        self.assertEqual(last_old, {})
        self.assertEqual(last_new["solids"], 3)  # the fast path ran
        self.assertEqual(old, new)  # same ids in the same order
        self.assertEqual(left_old, left_new)
        self.assertEqual(sum(len(x) for x in new), fem.VolumeCount)
        self.assertTrue(all(len(x) for x in new))

    def test_node_sets_are_identical(self):
        fem = self.study["mesh"].FemMesh
        mt = self.meshtools
        for sol in self.feature.Shape.Solids:
            old = sorted(fem.getNodesBySolid(sol))
            new = mt.get_solid_femnodes_fast(fem, [sol])
            self.assertEqual(old, new)

    def test_leftover_elements_are_handled_alike(self):
        fem0 = self.study["mesh"].FemMesh
        # a mid-edge node on the shaft's outer face moved 0.4 mm away from every solid
        z0 = fem0.Nodes
        stray = [
            nid for nid, v in z0.items()
            if abs((v.x ** 2 + v.y ** 2) ** 0.5 - 4.0) < 1e-6 and 12 < v.z < 28
        ]
        self.assertTrue(stray)
        v = z0[stray[len(stray) // 2]]
        moved = sim.rebuild_mesh(fem0, drop_unused=False, moved={stray[len(stray) // 2]: (v.x * 1.1, v.y * 1.1, v.z)})
        ok_old, old, left_old, _ = self.sets(moved, False)
        ok_new, new, left_new, last_new = self.sets(moved, True)
        self.assertTrue(ok_old and ok_new)
        self.assertGreaterEqual(left_old.get("count", 0), 1)
        self.assertEqual(last_new.get("stray", 0), left_old["count"])
        self.assertEqual(old, new)
        self.assertEqual(left_old["elements"], left_new["elements"])

    def test_the_input_deck_is_identical(self):
        mt = self.meshtools
        texts = []
        for fast in (False, True):
            mt.FAST_SOLID_SETS = fast
            fea = sim.prepare_solve(self.study["analysis"], os.path.join(self.tmp, "fast" if fast else "old"))
            with open(fea.inp_file_name) as f:
                texts.append([ln for ln in f.read().splitlines() if not ln.startswith("**")])  # comments carry the date and the file name
        self.assertGreater(len(texts[0]), 1000)
        self.assertEqual(texts[0], texts[1])


if __name__ == "__main__":
    unittest.main()
