# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCADCmd checks for wire routing (BW-WIR-02..06).

    FreeCADCmd package/bruceworx/mods/WireRouting/tests/freecad_wire.py

Prints ``BW_WIRE PASS`` at the end; raises on the first failure. Needs the
Assembly module for the joint checks.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

WORK = os.environ.get("BW_WIRE_DIR", "/tmp/bruceworx-wire")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def close(a, b, tol, message):
    if abs(a - b) > tol:
        raise RuntimeError(f"{message}: {a} vs {b} (tolerance {tol})")


def centerline_ends(wire):
    """End points of the wire's shape along its centerline: the centres of
    the two end faces of the tube, or the ends of the bare centerline."""
    import FreeCAD as App  # noqa: PLC0415

    shape = wire.Shape
    if not wire.ShowTube:
        vertexes = shape.Wires[0].OrderedVertexes
        return vertexes[0].Point, vertexes[-1].Point
    planes = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomPlane"]
    check(len(planes) == 2, f"tube should have two flat end faces, has {len(planes)}")
    return tuple(App.Vector(f.CenterOfMass) for f in planes)


def on_ports(wire, ports, tol=1e-6):
    ends = centerline_ends(wire)
    for port in ports:
        check(min((e - port).Length for e in ends) < tol, f"{wire.Label}: end is off its port {port} {ends}")


def test_port_and_wire():
    """WIR-02: three corners, analytic length, tube, tight corner fails."""
    import FreeCAD as App  # noqa: PLC0415

    from wire_routing import objects  # noqa: PLC0415

    doc = App.newDocument("WirePart")
    up = App.Rotation()
    a = objects.make_port(doc, "A", App.Placement(App.Vector(0, 0, 0), up))
    b = objects.make_port(doc, "B", App.Placement(App.Vector(120, 50, 0), up))
    c1 = objects.make_clip(doc, "C1", App.Placement(App.Vector(60, 0, 30), up))
    c2 = objects.make_clip(doc, "C2", App.Placement(App.Vector(60, 50, 30), up))
    wire = objects.make_wire(doc, a, b, [c1, c2], Diameter=2.0, MinBendRadius=8.0, LeadOut=30.0)
    doc.recompute()
    check(wire.Status == "ok" and wire.isValid(), f"wire not built: {wire.Status}")
    # Path: up 30, across 60, over 50, across 60, down 30 = four right angles.
    expected = (30 + 60 + 50 + 60 + 30) - 4 * 2 * 8 + 4 * 8 * math.pi / 2
    close(wire.Length.Value, expected, 0.01, "length vs line-plus-arc")
    check(len(wire.Shape.Solids) == 1 and wire.Shape.isValid(), "tube is not one valid solid")
    close(wire.Shape.Volume, math.pi * 1.0**2 * expected, 0.01 * math.pi * expected, "tube volume")
    on_ports(wire, [a.Placement.Base, b.Placement.Base])
    close(wire.CutLength.Value, math.ceil(expected + 10), 1e-9, "cut length")

    wire.ShowTube = False
    doc.recompute()
    check(not wire.Shape.Solids and len(wire.Shape.Edges) == 9, "centerline should be 5 lines + 4 arcs")
    close(wire.Shape.Length, expected, 1e-6, "centerline shape length")
    wire.ShowTube = True

    # A corner tighter than the radius: no solid, and the reason is named.
    c2.Placement.Base = App.Vector(60, 10, 30)  # C1-C2 is now 10 mm; needs 16
    doc.recompute()
    check(not wire.isValid(), "tight wire should be in error")
    check(wire.Shape.isNull(), "tight wire must have no shape")
    # Corner at C1 is still 90 degrees (tangent 8); at C2 the wire now turns by
    # atan2(60, 40), so that corner needs 8 tan(half of it).
    need = 8 + 8 * math.tan(math.atan2(60, 40) / 2)
    check("segment 2" in wire.Status and f"{need - 10:.3f} mm short" in wire.Status, f"status: {wire.Status}")
    check(wire.Length.Value == 0, "tight wire must have no length")
    c2.Placement.Base = App.Vector(60, 50, 30)
    doc.recompute()
    check(wire.isValid() and wire.Status == "ok", "wire should recover")

    wire.MinBendRadius = 0.5  # less than half the 2 mm diameter
    doc.recompute()
    check(not wire.isValid() and "half the wire diameter" in wire.Status, wire.Status)
    wire.MinBendRadius = 8.0
    doc.recompute()

    path = os.path.join(WORK, "wire.FCStd")
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    doc = App.openDocument(path)
    wire = doc.getObject("Wire")
    check(objects.is_wire(wire), "wire proxy not restored")
    doc.recompute()
    close(wire.Length.Value, expected, 0.01, "length after reload")
    App.closeDocument(doc.Name)
    print("port and wire ok: length", round(expected, 4))


def build_arm():
    """Base and arm as separate part files linked into an assembly, joined
    by a revolute joint with 0..90 degree limits. One wire from a port on
    the base to a port on the arm."""
    import FreeCAD as App  # noqa: PLC0415
    import JointObject  # noqa: PLC0415

    from wire_routing import objects  # noqa: PLC0415

    def part_file(name, size, port_at, port_rotation=None):
        pdoc = App.newDocument(name)
        part = pdoc.addObject("App::Part", "Part")
        box = part.newObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = size
        port = objects.make_port(part, "P", App.Placement(port_at, port_rotation or App.Rotation()))
        pdoc.recompute()
        pdoc.saveAs(os.path.join(WORK, "arm", name + ".FCStd"))
        return part, port

    os.makedirs(os.path.join(WORK, "arm"), exist_ok=True)
    doc = App.newDocument("ArmAsm")
    doc.saveAs(os.path.join(WORK, "arm", "ArmAsm.FCStd"))
    base_part, base_port = part_file("Base", (40, 40, 10), App.Vector(10, 20, 10))
    arm_part, arm_port = part_file("Arm", (80, 10, 10), App.Vector(60, 5, 10))

    assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
    assembly.Type = "Assembly"
    joints = assembly.newObject("Assembly::JointGroup", "Joints")
    base = assembly.newObject("App::Link", "Base")
    base.setLink(base_part)
    arm = assembly.newObject("App::Link", "Arm")
    arm.setLink(arm_part)
    arm.Placement = App.Placement(App.Vector(30, 15, 10), App.Rotation())
    doc.recompute()

    ground = joints.newObject("App::FeaturePython", "Ground")
    JointObject.GroundedJoint(ground, base)
    joint = joints.newObject("App::FeaturePython", "Elbow")
    JointObject.Joint(joint, JointObject.JointTypes.index("Revolute"))
    # Top face of the base to the bottom face of the arm: the arm swings flat on the base.
    joint.Proxy.setJointConnectors(joint, [[base, ["Box.Face6", "Box.Vertex6"]], [arm, ["Box.Face5", "Box.Vertex1"]]])
    doc.recompute()
    wire = objects.make_wire(
        assembly, objects.port_ref(base, base_port), objects.port_ref(arm, arm_port), Diameter=1.6, MinBendRadius=4.0, LeadOut=6.0, Gauge="22 AWG", WireColor="red"
    )
    doc.recompute()
    return doc, assembly, base, arm, joint, wire


def port_points(base, arm):
    from wire_routing import objects  # noqa: PLC0415

    return [objects.ref_placement((base, "Port.")).Base, objects.ref_placement((arm, "Port.")).Base]


def test_assembly_follow():
    """WIR-03: the wire follows the joint, in a recompute and in a drag."""
    import FreeCAD as App  # noqa: PLC0415
    import JointObject  # noqa: PLC0415
    import UtilsAssembly  # noqa: PLC0415

    from wire_routing import objects  # noqa: PLC0415

    objects.install_follower()
    doc, assembly, base, arm, joint, wire = build_arm()
    check(wire.Status == "ok", f"arm wire: {wire.Status}")
    on_ports(wire, port_points(base, arm))
    first = wire.Length.Value

    def turn_to(degrees):
        plc1 = UtilsAssembly.getJcsGlobalPlc(joint.Placement1, joint.Reference1)
        axis = plc1.Rotation.multVec(App.Vector(0, 0, 1))
        now = math.degrees(UtilsAssembly.getJointXYAngle(joint))
        arm.Placement = App.Placement(App.Vector(), App.Rotation(axis, degrees - now), plc1.Base) * arm.Placement

    # Drag: no recompute between steps; the follower keeps the wire on its ports.
    lengths = []
    assembly.preDrag([arm])
    for angle in (20, 40, 60, 80):
        turn_to(angle)
        assembly.doDragStep()
        on_ports(wire, port_points(base, arm))
        lengths.append(wire.Length.Value)
    assembly.postDrag()
    check(len({round(x, 6) for x in lengths + [first]}) == 5, f"length did not change while dragging: {lengths}")

    # Recompute: the solver runs after the wire in recompute order; one
    # recompute must still leave the wire on the solved ports.
    turn_to(130)
    doc.recompute()
    on_ports(wire, port_points(base, arm))
    check("Touched" not in wire.State, f"wire left touched after recompute: {wire.State}")
    moved = wire.Length.Value
    check(abs(moved - lengths[-1]) > 1e-3, "length did not change on recompute")

    # Sweep: limits 0..90. The assembly is put back afterwards.
    joint.EnableAngleMin = joint.EnableAngleMax = True
    joint.AngleMin, joint.AngleMax = 0, 90
    turn_to(45)
    doc.recompute()
    before = App.Placement(arm.Placement)
    report = objects.sweep(assembly, steps=9)
    entry = report[wire.Name]
    check(arm.Placement.isSame(before, 1e-6), "sweep did not put the arm back")
    check(not entry["unreached"], f"sweep missed poses: {entry['unreached']}")
    check(entry["max"] - entry["min"] > 1.0, f"sweep range too small: {entry}")
    check(entry["min"] - 1e-6 <= wire.Length.Value <= entry["max"] + 1e-6, "current length outside the swept range")
    close(wire.LengthMax.Value, entry["max"], 1e-9, "LengthMax stored")
    close(wire.CutLength.Value, math.ceil(entry["max"] + wire.Allowance.Value), 1e-9, "cut length uses the longest pose")
    check("Elbow" in wire.SweepStatus, wire.SweepStatus)
    on_ports(wire, port_points(base, arm))
    # Independent check of the extremes: measure at each end of the range.
    ends = []
    for angle in (0, 90):
        turn_to(angle)
        doc.recompute()
        ends.append(wire.Length.Value)
    check(entry["min"] <= min(ends) + 1e-6 and entry["max"] >= max(ends) - 1e-6, f"sweep {entry} vs ends {ends}")
    print(f"assembly follow ok: {entry['min']:.2f} to {entry['max']:.2f} mm over 0..90 deg")

    # Kink: a long lead-out makes the wire too tight when the ports come close.
    wire.LeadOut = 2.0
    wire.MinBendRadius = 12.0
    report = objects.sweep(assembly, steps=9)
    kinks = [k for k in report[wire.Name]["kinks"] if k["at"] is not None]
    check(kinks and "kinks at Elbow" in wire.SweepStatus, f"expected kinks: {wire.SweepStatus}")
    wire.LeadOut, wire.MinBendRadius = 6.0, 4.0
    turn_to(45)
    doc.recompute()

    # WIR-04: cut list and the assembly BOM.
    listing = objects.cut_list(doc)
    (row,) = listing["wires"]
    check(listing["ok"] and row["from"] == "Base.P" and row["to"] == "Arm.P", f"cut list row: {row}")
    close(row["length_mm"], wire.Length.Value, 1e-3, "cut list length")
    close(row["cut_mm"], wire.CutLength.Value, 1e-9, "cut list cut length")
    check(listing["totals"] == [{"gauge": "22 AWG", "color": "red", "wires": 1, "total_mm": row["cut_mm"]}], "totals")
    bom = assembly.newObject("Assembly::BomObject", "Bom")
    bom.columnsNames = ["Index", "Name", "Quantity", ".Gauge", ".WireColor", ".CutLength"]
    bom.onlyParts = False
    doc.recompute()
    cells = [[bom.getContents(f"{col}{r}") for col in "ABCDEF"] for r in range(1, 10)]
    wire_rows = [r for r in cells if "Wire" in r[1]]
    check(len(wire_rows) == 1, f"BOM rows: {cells}")
    check("22 AWG" in wire_rows[0][3] and "red" in wire_rows[0][4], f"BOM wire row: {wire_rows[0]}")
    check(str(int(wire.CutLength.Value)) in wire_rows[0][5], f"BOM cut length: {wire_rows[0]}")

    # The solver must not have been disturbed by a wire in the assembly.
    doc.recompute()
    close(JointObject.limitReading(joint)[0], 45, 0.5, "joint angle after everything")

    # Reload: ports are in other files; the wire comes back on them.
    doc.save()
    for d in list(App.listDocuments().values()):
        App.closeDocument(d.Name)
    doc = App.openDocument(os.path.join(WORK, "arm", "ArmAsm.FCStd"))
    doc.recompute()
    wire = doc.getObject("Wire")
    on_ports(wire, port_points(doc.getObject("Base"), doc.getObject("Arm")))
    for d in list(App.listDocuments().values()):
        App.closeDocument(d.Name)
    print("cut list, BOM and reload ok")


def test_clips_and_bundles():
    """WIR-05 and WIR-06."""
    import FreeCAD as App  # noqa: PLC0415

    from wire_routing import objects  # noqa: PLC0415

    doc = App.newDocument("Harness")
    up = App.Rotation()

    def datum(label, x, y, z):
        return objects.make_port(doc, label, App.Placement(App.Vector(x, y, z), up))

    p1, p2 = datum("ESC1", 0, 0, 0), datum("ESC2", 0, 30, 0)
    m1, m2 = datum("Motor1", 200, -20, 0), datum("Motor2", 200, 50, 0)
    c1, c2 = datum("ClipA", 40, 15, 20), datum("ClipB", 160, 15, 20)
    w1 = objects.make_wire(doc, p1, m1, [c1, c2], label="W1", MinBendRadius=5)
    w2 = objects.make_wire(doc, p2, m2, [c1, c2], label="W2", MinBendRadius=5, Diameter=2.0)
    bundle = objects.make_bundle(doc, [w1, w2])
    doc.recompute()
    check(w1.Status == "ok" and w2.Status == "ok", f"{w1.Status} / {w2.Status}")
    check(bundle.Status == "ok" and bundle.isValid(), f"bundle: {bundle.Status}")
    close(bundle.Length.Value, 120.0, 1e-6, "sleeve length is the shared segment")
    close(bundle.InnerDiameter.Value, math.sqrt((1.6**2 + 2.0**2) / 0.75), 1e-9, "sleeve bore")
    check(len(bundle.Shape.Solids) == 1 and bundle.Shape.isValid(), "sleeve is not one solid")
    check(len(objects.cut_list(doc)["wires"]) == 2, "bundle counted as a wire")

    objects.set_route(w2, via=[c2])
    doc.recompute()
    check(not bundle.isValid() and "do not share two clips" in bundle.Status, bundle.Status)

    # WIR-06: three clips. Low gives the shortest path, but it sits below
    # the top of the 10 mm lead-outs, so the wire would have to turn more
    # than 90 degrees in less room than the radius allows. Of the two that
    # can be bent, Mid is shorter than High.
    a, b = datum("A", 0, 100, 0), datum("B", 100, 100, 0)
    low, mid, high = datum("Low", 50, 100, 4), datum("Mid", 50, 100, 40), datum("High", 50, 100, 90)
    wire = objects.make_wire(doc, a, b, label="Routed", MinBendRadius=10, LeadOut=10)
    edges = [("start", 0), (0, "end"), ("start", 1), (1, "end"), ("start", 2), (2, "end")]
    found = objects.route_through_clips(wire, [low, mid, high], edges)
    doc.recompute()
    check(found.path == ["start", 1, "end"], f"picked {found.path}")
    check([path for path, _why in found.rejected] == [["start", 0, "end"]], f"Low should be rejected: {found.rejected}")
    check(wire.Status == "ok", wire.Status)
    close(wire.Length.Value, found.length, 1e-6, "reported length is the wire's length")
    check(wire.Via[0][0] is mid, "wire not routed through Mid")

    # Clip order is the route: it must survive however FreeCAD stores it.
    order = [high, low, mid, c1, low]
    objects.set_route(wire, via=order)
    check([r[0] for r in objects._via_refs(wire)] == order, "Via lost its order")
    App.closeDocument(doc.Name)
    print("clips, bundles and clip graph ok")


def run():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    test_port_and_wire()
    test_clips_and_bundles()
    test_assembly_follow()


def _main():
    try:
        run()
    except Exception:  # noqa: BLE001 — report and fail the process
        traceback.print_exc()
        print("BW_WIRE FAIL", flush=True)
        sys.exit(1)
    print("BW_WIRE PASS", flush=True)
    sys.exit(0)


if __name__ in ("__main__", "freecad_wire"):
    _main()
