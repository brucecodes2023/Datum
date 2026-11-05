# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the vault files' preview mesh (bruceworx_previewmesh, PDM-13)."""

import os
import shutil
import tempfile
import unittest
import zipfile

import bruceworx_previewmesh as pm


def _square():
    return {"name": "square", "color": (0.1, 0.2, 0.3), "points": [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0],
            "triangles": [0, 1, 2, 0, 2, 3]}


class TestBruceWorxPreviewMeshFormat(unittest.TestCase):
    def test_round_trip(self):
        shifted = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 5, 0, 0, 0, 1]
        data = pm.encode([_square()], [{"file": "VL-01-01001-001.FCStd", "object": "Part", "matrix": shifted}])
        mesh = pm.decode(data)
        part = mesh["parts"][0]
        self.assertEqual(list(part["points"]), [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0])
        self.assertEqual(list(part["triangles"]), [0, 1, 2, 0, 2, 3])
        self.assertEqual(part["color"], (0.1, 0.2, 0.3))
        self.assertEqual(mesh["instances"][0]["file"], "VL-01-01001-001.FCStd")
        self.assertEqual(mesh["instances"][0]["matrix"][11], 5)

    def test_transparency_round_trip(self):
        # the clear acrylic panels keep their transparency; an opaque part's mesh is written as before
        clear = pm.decode(pm.encode([dict(_square(), transparency=0.7)]))["parts"][0]
        self.assertAlmostEqual(clear["transparency"], 0.7)
        opaque = pm.encode([dict(_square(), transparency=0.0)])
        self.assertEqual(opaque, pm.encode([_square()]))
        self.assertEqual(pm.decode(opaque)["parts"][0]["transparency"], 0.0)

    def test_refuses_what_is_not_a_mesh(self):
        good = pm.encode([_square()])
        for bad in (b"", b"PK\x03\x04", good[:-8], good.replace(b'"version":1', b'"version":9')):
            with self.assertRaises(ValueError):
                pm.decode(bad)

    def test_index_out_of_range(self):
        part = dict(_square(), triangles=[0, 1, 9])
        with self.assertRaises(ValueError):
            pm.decode(pm.encode([part]))

    def test_flatten_composes_and_stops_at_cycles(self):
        up = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 2, 0, 0, 0, 1]
        files = {
            "A": {"parts": [], "instances": [{"file": "B", "matrix": up}, {"file": "B", "matrix": up}]},
            "B": {"parts": [{"name": "b"}], "instances": [{"file": "C", "matrix": up}]},
            "C": {"parts": [{"name": "c"}], "instances": [{"file": "A", "matrix": up}]},  # a cycle back to A
        }
        out = pm.flatten(files.get, "A")
        self.assertEqual([p["name"] for p, _m in out], ["b", "c", "b", "c"])
        self.assertEqual(out[1][1][11], 4)  # A -> B (+2) -> C (+2)
        self.assertEqual(pm.flatten(files.get, "missing"), [])

    def test_rewrite_replaces_and_adds(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "x.FCStd")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Document.xml", "<doc/>")
                z.writestr("thumbnails/Thumbnail.png", b"old")
            pm.rewrite(path, {"thumbnails/Thumbnail.png": b"new", pm.ENTRY: b"mesh"})
            with zipfile.ZipFile(path) as z:
                self.assertEqual(z.namelist(), ["Document.xml", "thumbnails/Thumbnail.png", pm.ENTRY])
                self.assertEqual(z.read("thumbnails/Thumbnail.png"), b"new")
            self.assertEqual(pm.read(path), b"mesh")
            self.assertEqual(os.listdir(folder), ["x.FCStd"])  # no temporary left behind
        finally:
            shutil.rmtree(folder)


class TestBruceWorxPreviewMeshFreeCAD(unittest.TestCase):
    """Tessellation and instances on real documents (headless)."""

    def setUp(self):
        import FreeCAD

        self.App = FreeCAD
        self.folder = tempfile.mkdtemp()
        self.docs = []

    def tearDown(self):
        for name in self.docs:
            if name in self.App.listDocuments():
                self.App.closeDocument(name)
        shutil.rmtree(self.folder)

    def _part(self, name):
        import Part

        doc = self.App.newDocument(name)
        self.docs.append(doc.Name)
        container = doc.addObject("App::Part", "Part")
        container.Placement.Base = self.App.Vector(0, 0, 3)
        box = doc.addObject("Part::Feature", "Block")
        box.Shape = Part.makeBox(10, 20, 30)
        container.addObject(box)
        doc.recompute()
        doc.saveAs(os.path.join(self.folder, name + ".FCStd"))
        return doc

    def test_part_mesh_is_in_global_coordinates(self):
        doc = self._part("PM_part")
        parts, instances = pm.tessellate(doc)
        self.assertEqual(len(parts), 1)
        self.assertEqual(instances, [])
        zs = parts[0]["points"][2::3]
        self.assertAlmostEqual(min(zs), 3.0, places=4)  # the App::Part placement is applied
        self.assertAlmostEqual(max(zs), 33.0, places=4)
        self.assertEqual(len(parts[0]["triangles"]) // 3, 12)  # a box: 6 faces, 2 triangles each

    def test_assembly_links_become_instances(self):
        self._part("PM_bolt")
        asm = self.App.newDocument("PM_asm")
        self.docs.append(asm.Name)
        asm.saveAs(os.path.join(self.folder, "PM_asm.FCStd"))  # a link to another file needs a saved owner
        bolt = self.App.getDocument("PM_bolt").getObject("Part")
        for i in range(3):
            link = asm.addObject("App::Link", f"Bolt{i}")
            link.setLink(bolt)
            link.Placement.Base = self.App.Vector(100 * i, 0, 0)
        asm.recompute()
        asm.save()
        parts, instances = pm.tessellate(asm)
        self.assertEqual(parts, [])  # no triangles of the bolt in the assembly
        self.assertEqual([i["file"] for i in instances], ["PM_bolt.FCStd"] * 3)
        meshes = {"asm": pm.decode(pm.encode(parts, instances)), "PM_bolt.FCStd": pm.decode(pm.mesh_of(bolt.Document))}
        drawn = pm.flatten(meshes.get, "asm")
        self.assertEqual(len(drawn), 3)
        for (part, m), link in zip(drawn, (asm.getObject(f"Bolt{i}") for i in range(3))):
            xs = [part["points"][k] * m[0] + part["points"][k + 1] * m[1] + part["points"][k + 2] * m[2] + m[3]
                  for k in range(0, len(part["points"]), 3)]
            box = link.Shape.BoundBox
            self.assertAlmostEqual(min(xs), box.XMin, places=3)
            self.assertAlmostEqual(max(xs), box.XMax, places=3)


if __name__ == "__main__":
    unittest.main()
