# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless checks for the assembly order and for when the bar has anything to show.

Run with FreeCAD's console binary::

    FreeCADCmd package/bruceworx/mods/HistoryTimeline/tests/test_assembly_timeline.py

Set ``BW_CHECKLIST`` to a folder with the BruceWorx checklist files to also
read ``10-15-bolt-assembly.FCStd`` and ``05-13-patterns.FCStd`` (copies are
opened from a temporary folder, the originals are never saved). Exits 0 when
every check passes and 1 otherwise.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import FreeCAD as App

from history_timeline import assembly as asm

FAILURES = []
PASSED = 0


def emit(message):
    # FreeCADCmd exits with os._exit, which skips the stdout flush.
    print(message, flush=True)


def check(condition, message):
    global PASSED
    if condition:
        PASSED += 1
        emit(f"PASS  {message}")
    else:
        FAILURES.append(message)
        emit(f"FAIL  {message}")


def names(items):
    return [getattr(item, "name", None) or item.Name for item in items]


def add_joint(jointgroup, name, grounded_on=None):
    import JointObject

    joint = jointgroup.newObject("App::FeaturePython", name)
    if grounded_on is not None:
        JointObject.GroundedJoint(joint, grounded_on)
    else:
        JointObject.Joint(joint, 0)
    return joint


def build_assembly(doc):
    """Assembly whose Group order differs from the creation order on purpose."""
    assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
    jointgroup = assembly.newObject("Assembly::JointGroup", "Joints")
    base = assembly.newObject("Part::Box", "Base")
    ground = add_joint(jointgroup, "GroundBase", base)
    arm = assembly.newObject("Part::Box", "Arm")
    hinge = add_joint(jointgroup, "Hinge")
    pin = assembly.newObject("Part::Cylinder", "Pin")
    doc.recompute()
    return assembly, jointgroup, [base, ground, arm, hinge, pin]


def test_assembly_order():
    doc = App.newDocument("TimelineAssemblyOrder")
    try:
        assembly, jointgroup, created = build_assembly(doc)
        expected = [obj.Name for obj in created]

        timeline = asm.read_assembly(assembly)
        check(names(timeline.entries) == expected, f"creation order {names(timeline.entries)}")
        roles = [entry.role for entry in timeline.entries]
        check(
            roles == ["component", "joint", "component", "joint", "component"],
            f"roles {roles}",
        )
        check("Joints" not in names(timeline.entries), "joint folder is not a chip")
        check(
            all(not e.after_tip and not e.is_tip for e in timeline.entries),
            "no tip, nothing rolled back",
        )
        check(timeline.note() == "5 items in the order they were added", timeline.note())

        # Reordering the tree does not change the order: it is creation order.
        group = list(assembly.Group)
        pin = doc.getObject("Pin")
        group.remove(pin)
        assembly.Group = [pin] + group
        check(
            names(asm.read_assembly(assembly).entries) == expected,
            "tree reorder keeps creation order",
        )

        # A joint also listed directly in the assembly appears once.
        hinge = doc.getObject("Hinge")
        assembly.Group = list(assembly.Group) + [hinge]
        listed = names(asm.read_assembly(assembly).entries)
        check(listed.count("Hinge") == 1, "joint listed in two groups shows once")

        # The object in edit is lit; a suppressed joint is struck through.
        lit = asm.read_assembly(assembly, current="Hinge")
        check([e.name for e in lit.entries if e.is_tip] == ["Hinge"], "object in edit is lit")
        hinge.Suppressed = True
        off = asm.read_assembly(assembly).entry("Hinge")
        check(off.suppressed, "suppressed joint is drawn suppressed")
        check("suppressed" in off.tooltip, "tooltip says suppressed")
        hinge.Suppressed = False
        check(not asm.read_assembly(assembly).entry("Base").suppressed, "component not suppressed")

        # Folders that are not components stay out.
        try:
            assembly.newObject("Assembly::ViewGroup", "Exploded Views")
            listed = names(asm.read_assembly(assembly).entries)
            check(not any(n.startswith("Exploded") for n in listed), "view folder is not a chip")
        except Exception as exc:  # type not built
            emit(f"NOTE  ViewGroup not available: {exc}")

        root, sub = asm.selection_path(hinge)
        check((root.Name, sub) in (("Assembly", "Joints.Hinge."), ("Assembly", "Hinge.")),
              f"joint selection path {root.Name} {sub!r}")
        root, sub = asm.selection_path(doc.getObject("Arm"))
        check((root.Name, sub) == ("Assembly", "Arm."), f"component selection path {root.Name} {sub!r}")
        root, sub = asm.selection_path(assembly)
        check((root.Name, sub) == ("Assembly", ""), "top-level selection path")
    finally:
        App.closeDocument(doc.Name)


def test_pick_source_and_visibility():
    check(asm.pick_source([]) == (None, None, "none"), "no objects: nothing to show")
    check(not asm.has_history(None, None), "no document: hidden")

    doc = App.newDocument("TimelinePick")
    try:
        empty_body = doc.addObject("PartDesign::Body", "Empty")
        doc.recompute()
        kind, obj, how = asm.pick_source(doc.Objects)
        check(kind is None and how == "none", "body without features: nothing to show")
        kind, obj, how = asm.pick_source(doc.Objects, active_body=empty_body)
        check((kind, obj.Name, how) == ("body", "Empty", "active"), "active empty body is picked")
        check(not asm.has_history(kind, obj), "active empty body: hidden")

        body = doc.addObject("PartDesign::Body", "Body")
        box = doc.addObject("PartDesign::AdditiveBox", "Box")
        body.addObject(box)
        doc.recompute()
        kind, obj, how = asm.pick_source(doc.Objects)
        check((kind, obj.Name, how) == ("body", "Body", "first"), "first body with features")
        check(asm.has_history(kind, obj), "body with features: shown")

        assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
        jointgroup = assembly.newObject("Assembly::JointGroup", "Joints")
        doc.recompute()
        kind, obj, how = asm.pick_source(doc.Objects)
        check((kind, obj.Name) == ("assembly", "Assembly"), "assembly beats a body that is not active")
        check(not asm.has_history(kind, obj), "empty assembly: hidden")
        part = assembly.newObject("Part::Box", "Plate")
        check(asm.has_history(kind, obj), "assembly with a component: shown")

        kind, obj, how = asm.pick_source(doc.Objects, active_body=body, active_assembly=assembly)
        check((kind, obj.Name, how) == ("body", "Body", "active"), "active body beats active assembly")

        sub = assembly.newObject("Assembly::AssemblyObject", "SubAssembly")
        kind, obj, how = asm.pick_source(doc.Objects, active_assembly=sub)
        check((kind, obj.Name, how) == ("assembly", "SubAssembly", "active"), "active sub-assembly")
        kind, obj, how = asm.pick_source(doc.Objects)
        check(obj.Name == "Assembly", "fallback takes the top-level assembly, not the sub-assembly")
        listed = names(asm.read_assembly(assembly).entries)
        check(listed == ["Plate", "SubAssembly"], f"sub-assembly is one component {listed}")

        # Non-body, non-assembly objects never count.
        kind, _obj, _how = asm.pick_source([part, jointgroup])
        check(kind is None, "loose objects: nothing to show")
        check(not asm.has_history("other", part), "unknown kind: hidden")
    finally:
        App.closeDocument(doc.Name)


def test_checklist_files():
    folder = os.environ.get("BW_CHECKLIST", "")
    if not folder or not os.path.isdir(folder):
        emit("NOTE  BW_CHECKLIST not set, checklist files skipped")
        return
    scratch = tempfile.mkdtemp(prefix="timeline-")
    try:
        path = os.path.join(scratch, "bolt.FCStd")
        shutil.copy(os.path.join(folder, "10-15-bolt-assembly.FCStd"), path)
        doc = App.openDocument(path)
        try:
            kind, obj, how = asm.pick_source(doc.Objects)
            check((kind, obj.Name, how) == ("assembly", "Assembly", "first"), "bolt file: assembly")
            listed = names(asm.read_assembly(obj).entries)
            check(listed == ["Plate", "Bolt", "GroundPlate"], f"bolt file order {listed}")
            ids = [e.object_id for e in asm.read_assembly(obj).entries]
            check(ids == sorted(ids), "bolt file ids ascend")
        finally:
            App.closeDocument(doc.Name)

        path = os.path.join(scratch, "patterns.FCStd")
        shutil.copy(os.path.join(folder, "05-13-patterns.FCStd"), path)
        doc = App.openDocument(path)
        try:
            kind, obj, how = asm.pick_source(doc.Objects)
            check((kind, obj.Name, how) == ("body", "Block", "first"), "patterns file: first body")
            check(asm.has_history(kind, obj), "patterns file: shown")
        finally:
            App.closeDocument(doc.Name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main():
    emit("=== History timeline: assembly order and visibility ===")
    for test in (test_assembly_order, test_pick_source_and_visibility, test_checklist_files):
        try:
            test()
        except Exception:
            traceback.print_exc()
            sys.stderr.flush()
            FAILURES.append(f"{test.__name__}: unhandled exception (see traceback)")
    emit(f"=== {PASSED} passed, {len(FAILURES)} failed ===")
    if FAILURES:
        for message in FAILURES:
            emit(f"  - {message}")
        os._exit(1)
    os._exit(0)


main()
