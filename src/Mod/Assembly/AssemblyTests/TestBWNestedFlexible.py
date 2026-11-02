# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] A flexible sub-assembly that holds another flexible sub-assembly:
#   Top > Gantry (AssemblyLink, flexible) > XAxis (AssemblyLink, flexible, two parts and a slider joint).
# The links inside the nested AssemblyLink are placed like their sources, the copied joints point at the
# links of the top document, and the top-level solve reaches the parts of the nested link.

import os
import shutil
import tempfile
import unittest

import FreeCAD as App

import JointObject

V = App.Vector


def _close(*names):
    for n in names:
        if n in App.listDocuments():
            App.closeDocument(n)


class TestBWNestedFlexible(unittest.TestCase):
    FRAME = App.Placement(V(500, 200, 0), App.Rotation(V(0, 0, 1), 30))

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bwnested")
        self._build_xaxis()
        self._build_gantry()
        self._build_top()

    def tearDown(self):
        _close("BWNestTop", "BWNestGantry", "BWNestXAxis")
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----- model ------------------------------------------------------------

    def _joint(self, grp, kind, name, refs):
        j = grp.newObject("App::FeaturePython", name)
        JointObject.Joint(j, JointObject.JointTypes.index(kind))
        j.Proxy.setJointConnectors(j, refs)
        return j

    def _box(self, asm, name, size, pos):
        b = asm.newObject("Part::Box", name)
        b.Length, b.Width, b.Height = size
        b.Placement = App.Placement(V(*pos), App.Rotation())
        return b

    def _build_xaxis(self):
        doc = self.docx = App.newDocument("BWNestXAxis")
        self.asmx = doc.addObject("Assembly::AssemblyObject", "XAsm")
        jg = self.asmx.newObject("Assembly::JointGroup", "Joints")
        self.rail = self._box(self.asmx, "Rail", (100, 10, 10), (0, 0, 0))
        self.carriage = self._box(self.asmx, "Carriage", (10, 10, 10), (20, 0, 0))
        g = jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, self.rail)
        # Slider along the x axis: both x+ faces, vertex 5 sits on the axis.
        self._joint(
            jg, "Slider", "Slide",
            [[self.rail, ["Face2", "Vertex5"]], [self.carriage, ["Face2", "Vertex5"]]],
        )
        doc.recompute()
        doc.saveAs(os.path.join(self.tmp, "XAxis.FCStd"))

    def _kid(self, al, source):
        for c in al.Group:
            if c.isDerivedFrom("App::Link") and c.getLinkedObject(False) == source:
                return c
        return None

    def _build_gantry(self):
        doc = self.docg = App.newDocument("BWNestGantry")
        self.asmg = doc.addObject("Assembly::AssemblyObject", "GAsm")
        jg = self.asmg.newObject("Assembly::JointGroup", "Joints")
        self.frame = self._box(self.asmg, "Frame", (200, 20, 20), (0, 0, -30))
        g = jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, self.frame)
        doc.saveAs(os.path.join(self.tmp, "Gantry.FCStd"))
        self.alx = self.asmg.newObject("Assembly::AssemblyLink", "XAxisLink")
        self.alx.LinkedObject = self.asmx
        doc.recompute()
        self.alx.Rigid = False
        doc.recompute()
        self.gl_rail = self._kid(self.alx, self.rail)
        self.gl_car = self._kid(self.alx, self.carriage)
        # The rail sits on the frame (x+ ends flush), fixed.
        self._joint(
            jg, "Fixed", "FrameRail",
            [[self.frame, ["Face6", "Vertex5"]], [self.gl_rail, ["Face6", "Vertex5"]]],
        )
        doc.recompute()
        doc.saveAs(os.path.join(self.tmp, "Gantry.FCStd"))

    def _build_top(self):
        doc = self.doc = App.newDocument("BWNestTop")
        self.asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jg = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.base = self._box(self.asm, "Base", (300, 300, 10), (0, 0, -100))
        g = self.jg.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(g, self.base)
        self.shuttle = self._box(self.asm, "Shuttle", (10, 10, 10), (800, 0, 300))
        doc.saveAs(os.path.join(self.tmp, "Top.FCStd"))
        self.al = self.asm.newObject("Assembly::AssemblyLink", "GantryLink")
        self.al.LinkedObject = self.asmg
        doc.recompute()
        self.al.Placement = self.FRAME
        self.al.Rigid = False  # pushes the frame into the children
        doc.recompute()

    # ----- helpers ----------------------------------------------------------

    def _nested(self):
        return next(c for c in self.al.Group if c.TypeId == "Assembly::AssemblyLink")

    def _all_links(self, al):
        out = []
        for c in al.Group:
            if c.TypeId == "Assembly::AssemblyLink":
                out += self._all_links(c)
            elif c.isDerivedFrom("App::Link"):
                out.append(c)
        return out

    def _joint_groups(self, al):
        for c in al.Group:
            if c.TypeId == "Assembly::JointGroup":
                yield c
            elif c.TypeId == "Assembly::AssemblyLink":
                yield from self._joint_groups(c)

    # ----- bug 1: placements of the nested links ----------------------------

    def test_nested_links_are_placed_like_their_sources(self):
        nested = self._nested()
        self.assertFalse(nested.Rigid)
        for src in (self.gl_rail, self.gl_car):
            link = self._kid(nested, src.getLinkedObject(False))
            self.assertIsNotNone(link)
            want = self.FRAME * src.Placement
            self.assertTrue(
                link.Placement.isSame(want, 1e-6),
                "{}: {} instead of {}".format(link.Name, link.Placement, want),
            )

    # ----- bug 2: copied joints point into the top document -----------------

    def test_copied_joints_point_at_local_links(self):
        local = {l.Name for l in self._all_links(self.al)}
        count = 0
        for jg in self._joint_groups(self.al):
            for j in jg.Group:
                for k in ("Reference1", "Reference2"):
                    ref = getattr(j, k)
                    if not ref or ref[0] is None:
                        continue
                    count += 1
                    self.assertIs(ref[0].Document, self.doc, "{}.{} -> {}".format(j.Name, k, ref[0].Name))
                    self.assertIn(ref[0].Name, local)
        self.assertGreaterEqual(count, 4)
        for j in (o for jg in self._joint_groups(self.al) for o in jg.Group):
            self.assertNotIn("Invalid", j.State, j.Name)
            self.assertNotIn("Error", j.State, j.Name)

    # ----- bug 3: the top-level solve reaches the nested parts --------------

    def _top_links(self):
        nested = self._nested()
        frame = self._kid(self.al, self.frame)
        rail = self._kid(nested, self.rail)
        car = self._kid(nested, self.carriage)
        return frame, rail, car

    def _join_top(self):
        frame, rail, car = self._top_links()
        self._joint(
            self.jg, "Fixed", "BaseFrame",
            [[self.base, ["Face6", "Vertex5"]], [frame, ["Face6", "Vertex5"]]],
        )
        self._joint(
            self.jg, "Fixed", "ShuttleCarriage",
            [[car, ["Face2", "Vertex5"]], [self.shuttle, ["Face2", "Vertex5"]]],
        )
        self.doc.recompute()
        self.asm.solve()
        return frame, rail, car

    def test_top_solve_ties_nested_part(self):
        frame, rail, car = self._join_top()
        gp = car.Placement.inverse() * self.shuttle.Placement  # fixed offset
        # Shuttle and carriage are one body now.
        self.assertTrue(
            (car.Placement.Base - self.shuttle.Placement.Base).Length < 60,
            "carriage {} shuttle {}".format(car.Placement.Base, self.shuttle.Placement.Base),
        )
        # The carriage is on the rail axis of the (rotated) gantry, at the right x.
        axis = rail.Placement.Rotation.multVec(V(1, 0, 0))
        off = car.Placement.Base - rail.Placement.Base
        self.assertLess((off - axis * off.dot(axis)).Length, 1e-3)

    def test_drag_carriage_moves_shuttle_only_with_it(self):
        frame, rail, car = self._join_top()
        rail0, car0, frame0 = rail.Placement.copy(), car.Placement.copy(), frame.Placement.copy()
        shuttle0 = self.shuttle.Placement.copy()
        axis = rail.Placement.Rotation.multVec(V(1, 0, 0))
        self.asm.preDrag([car])
        for _ in range(3):
            p = car.Placement
            car.Placement = App.Placement(p.Base + axis * 15, p.Rotation)
            self.asm.doDragStep()
        self.asm.postDrag()
        moved = (car.Placement.Base - car0.Base).Length
        self.assertGreater(moved, 10)
        # the rest of the gantry stays, the shuttle (fixed to the carriage) went along
        self.assertTrue(rail.Placement.isSame(rail0, 1e-5))
        self.assertTrue(frame.Placement.isSame(frame0, 1e-5))
        step = self.shuttle.Placement.Base - shuttle0.Base
        self.assertLess((step - (car.Placement.Base - car0.Base)).Length, 1e-4)
