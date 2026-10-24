# SPDX-License-Identifier: LGPL-2.1-or-later
"""A saved assembly of the fixture parts for the joint checks, plus pick helpers.

Base is grounded at the origin; every other component starts away from the
plate and tilted, so a joint has to move it.
"""

import math
import os

import FreeCAD as App

import asmlib as L
import fixture

V = App.Vector
BODY = {"Base": ("BaseBody", "Pocket"), "Pin": ("PinBody", "Pad"), "Block": ("BlockBody", "Pad")}


class Scene:
    def __init__(self, work, comps):
        """comps: [(name, part 'Pin'|'Block', (x, y, z))] besides the grounded Base."""
        import UtilsAssembly  # noqa: F401

        parts = fixture.make_parts(os.path.join(work, "parts"))
        self.part_docs = {n: App.getDocument(n) for n in parts}
        path = os.path.join(work, "Joints.FCStd")
        if os.path.exists(path):
            os.remove(path)
        self.doc, self.asm = fixture.new_assembly_doc(path)
        App.setActiveDocument(self.doc.Name)
        self.c = {}
        self.kind = {}
        base = fixture.add_link(self.asm, self.part_docs["Base"].getObject("Base"), "Base")
        self.c["Base"] = base
        self.kind["Base"] = "Base"
        fixture.ground(self.asm, base)
        for i, (name, part, pos) in enumerate(comps):
            tilt = App.Rotation(V(1, 0.3, 0), 17 + 7 * i)
            link = fixture.add_link(self.asm, self.part_docs[part].getObject(part), name,
                                    App.Placement(V(*pos), tilt))
            self.c[name] = link
            self.kind[name] = part
        self.doc.recompute()
        self.doc.save()

    # --- geometry in the part's own frame
    def local_shape(self, name):
        body, _tip = BODY[self.kind[name]]
        return self.part_docs[self.kind[name]].getObject(body).Shape

    def sub(self, name, element):
        body, tip = BODY[self.kind[name]]
        return f"{self.c[name].Name}.{body}.{tip}.{element}"

    def global_element(self, name, element):
        """The element in assembly coordinates."""
        return self.asm.getSubObject(self.sub(name, element))

    def pick(self, name, element, local_point):
        L.pick(self.doc, self.asm.Name, self.sub(name, element), self.c[name].Placement.multVec(local_point))

    # --- elements by geometry (local frame)
    def face(self, name, pred):
        return L.find_face(self.local_shape(name), pred)

    def edge(self, name, pred):
        return L.find_edge(self.local_shape(name), pred)

    def vertex(self, name, point):
        shape = self.local_shape(name)
        for i, v in enumerate(shape.Vertexes, 1):
            if (v.Point - point).Length < 1e-6:
                return f"Vertex{i}"
        return None

    def joints(self):
        import UtilsAssembly

        return [j for j in UtilsAssembly.getJointGroup(self.asm).Group if hasattr(j, "JointType")]


def normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def angle_deg(a, b):
    return math.degrees(a.getAngle(b))


def make_joint(scene, command, pick_a, pick_b, setup=None, shot=None):
    """Generator: run a joint command, make two picks, optionally set fields, press OK.

    pick_a / pick_b: (component, element, local point). Returns the new joint (or None)."""
    import JointObject as J
    from asmlib import Gui

    before = {j.Name for j in scene.joints()}
    L.tell(f"{command.replace('Assembly_CreateJoint', '')} joint: pick {pick_a[0]} {pick_a[1]} and "
           f"{pick_b[0]} {pick_b[1]}, press OK; expect one joint and the part moved to fit")
    Gui.Selection.clearSelection()
    L.trigger(command)
    yield 900
    task = J.activeTask
    if not L.check(f"{command}: dialog opens", task is not None and Gui.Control.activeDialog()):
        return None
    scene.pick(*pick_a)
    yield 300
    scene.pick(*pick_b)
    yield 600
    L.check(f"{command}: two picks listed", len(task.refs) == 2 and task.jForm.featureList.count() == 2,
            (len(task.refs), task.jForm.featureList.count()))
    if setup is not None:
        setup(task)
        yield 500
    if shot:
        L.grab_task(shot)
    L.press("OK")
    yield 1000
    L.check(f"{command}: dialog closed by OK", not Gui.Control.activeDialog() and J.activeTask is None)
    new = [j for j in scene.joints() if j.Name not in before]
    L.check(f"{command}: one joint made", len(new) == 1, [j.Name for j in new])
    return new[0] if new else None
