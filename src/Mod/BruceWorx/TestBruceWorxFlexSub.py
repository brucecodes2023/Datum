# SPDX-License-Identifier: LGPL-2.1-or-later
"""Flexible sub-assemblies carry routed items (ASM, 30 Oct).

A sub-assembly (its own file) holds two housings with pin frames and a wire between two pins. Inserted
flexible into a parent assembly, the AssemblyLink copies the route path and the wire and re-points their
attachments at the links that stand for the housings, so moving a housing in the parent moves the wire's end.
Made rigid again, the copies are removed. Saved and reloaded, nothing doubles.

Run: FreeCADCmd -t TestBruceWorxFlexSub
"""

import os
import shutil
import tempfile
import unittest

import FreeCAD as App
import Part  # noqa: F401  (Part::FeaturePython)

import bruceworx_flexsub as fs
import bruceworx_routing as br
import bruceworx_routing_ports as bp

V = App.Vector


class FlexSubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bwflexsub")
        self.sub = App.newDocument("BWFlexSub")
        self.parent = App.newDocument("BWFlexParent")

    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("BWFlex"):
                App.closeDocument(name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def housing(self, name):
        part = self.sub.addObject("App::Part", name)
        box = self.sub.addObject("Part::Box", name + "_Body")
        box.Length, box.Width, box.Height = 7.3, 5.7, 7.5
        part.addObject(box)
        bp.make_pin(part, "1", ((0, 0, 0), (0, 0, 1), (1, 0, 0)), GaugeMin=28, GaugeMax=22)
        return part

    def build_sub(self):
        doc = self.sub
        a, b = self.housing("HA"), self.housing("HB")
        asm = doc.addObject("Assembly::AssemblyObject", "SubAsm")
        links = []
        for part, x in ((a, 0), (b, 100)):
            link = doc.addObject("App::Link", "L" + part.Name)
            link.LinkedObject = part
            link.Placement = App.Placement(V(x, 0, 0), App.Rotation())
            asm.addObject(link)
            links.append(link)
        ra = bp.frame_ref(links[0], bp.pin(a, "1"))
        rb = bp.frame_ref(links[1], bp.pin(b, "1"))
        path = br.make_path([(0, 0, 0), (50, 10, 40), (100, 0, 0)], doc, radius=5.0,
                            attachments=[(0, ra[0], ra[1]), (2, rb[0], rb[1])], container=asm)
        wire = br.make_wire(path, gauge="22 AWG", name="W1")
        doc.recompute()
        return asm, links, path, wire

    def insert(self, asm, rigid=False):
        self.sub.saveAs(os.path.join(self.tmp, "Sub.FCStd"))
        self.parent.saveAs(os.path.join(self.tmp, "Parent.FCStd"))
        link = self.parent.addObject("Assembly::AssemblyLink", "SubLink")
        top = self.parent.addObject("Assembly::AssemblyObject", "Top")
        top.addObject(link)
        link.LinkedObject = asm
        link.Rigid = rigid
        self.parent.recompute()
        if not rigid:
            # The parent's solver puts the parts where its joints say; here, where the sub-assembly has them.
            self.link_of(link, "HB").Placement = App.Placement(V(100, 0, 0), App.Rotation())
            self.parent.recompute()
        return top, link

    def routed_copies(self, link):
        return [o for o in link.Group if fs.is_copy(o)]

    def link_of(self, link, name):
        return next(o for o in link.Group if o.isDerivedFrom("App::Link") and o.LinkedObject.Name == "L" + name)

    def end(self, copy_path):
        return V(copy_path.Points[-1])

    def test_flexible_copies_and_follows(self):
        asm, links, path, wire = self.build_sub()
        top, link = self.insert(asm)
        copies = {o.getPropertyByName(fs.PROP): o for o in self.routed_copies(link)}
        self.assertEqual(sorted(copies), ["RoutePath", "W1"])
        # The routed items are copied, not linked as if they were parts.
        self.assertEqual([o.LinkedObject.Name for o in link.Group if o.isDerivedFrom("App::Link")], ["LHA", "LHB"])
        cpath, cwire = copies["RoutePath"], copies["W1"]
        self.assertEqual(br.kind_of(cpath), br.PATH)
        self.assertEqual(br.kind_of(cwire), br.WIRE)
        self.assertEqual(cwire.Path, cpath)
        # The attachments point at the links of the parent's AssemblyLink.
        targets = [(i, t.Name, s) for i, t, s in br.attachments_of(cpath)]
        la, lb = self.link_of(link, "HA"), self.link_of(link, "HB")
        self.assertEqual([t[1] for t in targets], [la.Name, lb.Name])
        self.assertTrue(all(t[2].endswith(".") and t[2] != "" for t in targets))
        # The source is untouched.
        self.assertEqual(set(o.Name for o in asm.Group), {"LHA", "LHB", "RoutePath", "W1"})
        self.assertAlmostEqual((V(path.Points[-1]) - V(100, 0, 0)).Length, 0.0, places=6)
        self.assertAlmostEqual((self.end(cpath) - V(100, 0, 0)).Length, 0.0, places=6)
        length = cwire.Length.Value
        # The parent moves a part: the wire's end follows.
        lb.Placement = App.Placement(V(100, 30, 20), App.Rotation())
        self.parent.recompute()
        self.assertAlmostEqual((self.end(cpath) - V(100, 30, 20)).Length, 0.0, places=6)
        self.assertNotAlmostEqual(cwire.Length.Value, length, places=3)
        self.assertTrue(cwire.Shape.isValid())
        self.assertNotIn("Invalid", cwire.State)
        # The source wire did not move.
        self.assertAlmostEqual((V(path.Points[-1]) - V(100, 0, 0)).Length, 0.0, places=6)

    def test_source_change_is_synced(self):
        asm, links, path, wire = self.build_sub()
        top, link = self.insert(asm)
        cwire = next(o for o in self.routed_copies(link) if br.kind_of(o) == br.WIRE)
        wire.Gauge = "18 AWG"
        wire.Colour = (0.0, 0.0, 1.0)
        self.sub.recompute()
        link.touch()
        self.parent.recompute()
        self.assertEqual(cwire.Gauge, "18 AWG")
        # A routed item added to the sub-assembly appears; one deleted disappears.
        extra = br.make_wire(path, gauge="22 AWG", name="W2", colour=None)
        self.sub.recompute()
        link.touch()
        self.parent.recompute()
        self.assertEqual(sorted(o.getPropertyByName(fs.PROP) for o in self.routed_copies(link)),
                         ["RoutePath", "W1", "W2"])
        self.sub.removeObject(extra.Name)
        self.sub.recompute()
        link.touch()
        self.parent.recompute()
        self.assertEqual(sorted(o.getPropertyByName(fs.PROP) for o in self.routed_copies(link)), ["RoutePath", "W1"])

    def test_rigid_removes_the_copies(self):
        asm, links, path, wire = self.build_sub()
        top, link = self.insert(asm)
        self.assertEqual(len(self.routed_copies(link)), 2)
        link.Rigid = True
        self.parent.recompute()
        self.assertEqual(self.routed_copies(link), [])
        self.assertEqual([o for o in self.parent.Objects if fs.is_routed(o)], [])
        link.Rigid = False
        self.parent.recompute()
        self.assertEqual(len(self.routed_copies(link)), 2)
        self.assertEqual(len([o for o in self.parent.Objects if fs.is_routed(o)]), 2)

    def test_save_and_reload(self):
        asm, links, path, wire = self.build_sub()
        top, link = self.insert(asm)
        lb = self.link_of(link, "HB")
        lb.Placement = App.Placement(V(100, 30, 20), App.Rotation())
        self.parent.recompute()
        parentfile = os.path.join(self.tmp, "Parent.FCStd")
        self.parent.saveAs(parentfile)
        App.closeDocument(self.parent.Name)
        App.closeDocument(self.sub.Name)
        doc = App.openDocument(parentfile)
        doc.recompute()
        link = doc.getObject("SubLink")
        copies = self.routed_copies(link)
        self.assertEqual(len(copies), 2)
        cpath = next(o for o in copies if br.kind_of(o) == br.PATH)
        self.assertAlmostEqual((self.end(cpath) - V(100, 30, 20)).Length, 0.0, places=6)
        # Another update does not double them, and the wire still follows.
        link.touch()
        doc.recompute()
        self.assertEqual(len(self.routed_copies(link)), 2)
        lb = self.link_of(link, "HB")
        lb.Placement = App.Placement(V(100, 0, 40), App.Rotation())
        doc.recompute()
        self.assertAlmostEqual((self.end(cpath) - V(100, 0, 40)).Length, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
