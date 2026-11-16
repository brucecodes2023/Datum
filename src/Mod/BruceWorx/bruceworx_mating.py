# SPDX-License-Identifier: LGPL-2.1-or-later
"""Smart fastener mating (roadmap ASM-09) and mate inference (ASM-11, its core), headless.

Bolt Up (ASM-09)
    ``bolt(assembly, target)`` finds every hole line through ``target`` (a component; None: the whole assembly)
    that no fastener fills yet, and puts a screw in it: the size from the clearance hole (ISO 273, the table in
    bruceworx.hardware), the head from the hole (a cone: countersunk ISO 10642; else the preference, socket head
    ISO 4762 unless changed), the length from the clamp stack, and a nut on the far side when the line has no
    thread. Each screw (and nut) gets one joint: a Revolute on its seat (concentric + head on the seat; the spin
    stays free). With a vault session each size is the vault's COTS item (bruceworx_hardware.insert_fastener);
    without one the add-on's object goes in the assembly itself (scripting and tests).

    The length rule, stated:
      tapped hole: the LONGEST standard length that does not bottom out (length <= clamp + the threaded bore's
                   depth) and engages at least the preference MatingEngagement x d (a user preference; its
                   default 1.0 is not from a standard: set it for your materials);
      nut:         the SHORTEST standard length that reaches through the nut (length >= clamp + the nut's
                   height, measured from the add-on's ISO 4032 nut, so flush at least).
    Standard lengths are the add-on's table for the type and size. "Clamp" is the distance along the axis from
    the head's seat to where the thread starts (or to the far face, for a nut).

Infer Mates (ASM-11 core)
    ``infer(assembly)`` turns coordinate-placed parts into joints, one sub-assembly at a time:
      touching planar faces       -> Coincident (one per contact direction and pair);
      a shaft in a bore           -> Concentric (a pin in two parts gives a hinge; two contacts give a slider);
      a fastener in a hole        -> Revolute on its seat (ASM-09's recogniser), or "floating" with the gap.
    Every joint is fitted to where the parts are (the connectors' difference goes into Offset2, as Keep Position
    does) and checked before it is made: one whose connectors do not already agree (a screw above its seat) is
    not made. After each joint the assembly is solved and every component must stay where it was (a hard check);
    a joint that moves anything is removed and reported. Parts with no joint are listed with the reason.
    Driving joints for motion axes are not made here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_ENABLED = "MatingCommands"
PREF_ENGAGEMENT = "MatingEngagement"  # minimum thread engagement in a tapped hole, x nominal diameter
PREF_SCREW = "MatingScrewType"  # the head for a plain or counterbored hole
DEFAULT_ENGAGEMENT = 1.0  # a preference default, not a standard: see the module text
DEFAULT_SCREW = "ISO4762"
COUNTERSUNK = "ISO10642"
NUT = "ISO4032"
TOL = 1e-3  # mm: modelling tolerance for "on the axis", "touching", "coplanar"
ANG = 1e-6  # unit-vector tolerance for "parallel"
MOVE_TOL = 1e-4  # mm (and rad) a component may move in the hard check
PIN_TYPES = ("ISO2338", "ISO8734", "ISO8735")
NUT_WASHER_CODES = ("nut", "washer", "insert")


class MatingError(ValueError):
    pass


def _app():
    import FreeCAD

    return FreeCAD


def _prefs():
    return _app().ParamGet(PREFS_PATH)


def enabled() -> bool:
    return bool(_prefs().GetBool(PREF_ENABLED, True))


def engagement() -> float:
    return float(_prefs().GetFloat(PREF_ENGAGEMENT, DEFAULT_ENGAGEMENT))


def screw_type() -> str:
    return _prefs().GetString(PREF_SCREW, DEFAULT_SCREW) or DEFAULT_SCREW


def _bh():
    import bruceworx_hardware

    return bruceworx_hardware


def _hw():
    from bruceworx import hardware

    return hardware


# ----- components and their shapes ----------------------------------------------------------------------------------

_SKIP_TYPES = ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup", "App::Origin",
               "App::DocumentObjectGroup", "Assembly::SimulationGroup")


def components(assembly):
    """The assembly's own components: what joints connect (links, bodies, parts, features)."""
    out = []
    for obj in assembly.Group:
        if obj.TypeId in _SKIP_TYPES or obj.isDerivedFrom("App::Origin"):
            continue
        if hasattr(obj, "JointType") or hasattr(obj, "ObjectToGround"):
            continue
        if obj.isDerivedFrom("App::Link") or obj.isDerivedFrom("App::Part") or hasattr(obj, "Shape"):
            out.append(obj)
    return out


def _target(obj):
    return obj.getLinkedObject(True) if obj.isDerivedFrom("App::Link") else obj


def _leaf_objects(obj, path):
    """[(path from the assembly, object)] of the solids a component is made of."""
    target = _target(obj)
    if target.isDerivedFrom("App::Part") and not target.TypeId.startswith("Assembly::"):
        out = []
        for child in target.Group:
            if child.isDerivedFrom("App::Origin") or child.isDerivedFrom("App::LocalCoordinateSystem") \
                    or child.isDerivedFrom("App::DatumElement"):
                continue
            if not getattr(child, "Visibility", True) and not child.isDerivedFrom("App::Part"):
                continue
            if any(p.isDerivedFrom("PartDesign::Body") for p in child.InList if p is not target):
                continue  # a feature inside a body: the body speaks for it
            out += _leaf_objects(child, f"{path}{child.Name}.")
        return out
    return [(path, obj)]


@dataclass
class Leaf:
    component: object
    path: str  # from the assembly, ends with "."
    obj: object
    shape: object  # in the assembly's frame


def leaves(assembly, component):
    import Part

    out = []
    for path, obj in _leaf_objects(component, f"{component.Name}."):
        try:
            shape = Part.getShape(assembly, path, transform=False)
        except Exception:  # noqa: BLE001
            continue
        if shape is None or shape.isNull() or not shape.Solids:
            continue
        out.append(Leaf(component, path, _target(obj) if obj is not component else obj, shape))
    return out


def is_fastener_leaf(leaf) -> bool:
    try:
        return _bh().is_fastener(leaf.obj)
    except Exception:  # noqa: BLE001
        return False


def fastener_kind(leaf) -> str:
    """"screw", "nut", "washer", "insert", "pin" or "" for a leaf of the Fasteners add-on."""
    if not is_fastener_leaf(leaf):
        return ""
    ftype = str(leaf.obj.Type)
    if ftype in PIN_TYPES:
        return "pin"
    code = _hw()._type_code(ftype)
    word = _hw().TYPE_NAMES.get(code, "").lower()
    for kind in NUT_WASHER_CODES:
        if kind in word:
            return kind
    return "screw"


def _frame(assembly, leaf):
    """The leaf object's placement in the assembly's frame."""
    return assembly.getSubObject(leaf.path, retType=3)


# ----- bores --------------------------------------------------------------------------------------------------------


@dataclass
class Bore:
    """A full (360 degree) cylindrical or conical face; internal: a hole's wall."""

    leaf: Leaf
    face: str  # "Face7"
    kind: str  # "cylinder" or "cone"
    radius: float  # the cylinder's; the cone's larger
    centre: object  # a point on the axis
    axis: object  # unit
    internal: bool
    lo: float = 0.0  # extent along axis from centre
    hi: float = 0.0


def _full_turn(face):
    u0, u1, _v0, _v1 = face.ParameterRange
    return abs((u1 - u0) - 2 * math.pi) < 1e-6


def _internal(face):
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    point = face.valueAt(u, v)
    normal = face.normalAt(u, v)
    surface = face.Surface
    axis = surface.Axis
    rel = point - surface.Center if hasattr(surface, "Center") else point - surface.Apex
    radial = rel - axis * rel.dot(axis)
    return normal.dot(radial) < 0


def bores(leaf):
    out = []
    for i, face in enumerate(leaf.shape.Faces, 1):
        name = type(face.Surface).__name__
        if name not in ("Cylinder", "Cone") or not _full_turn(face):
            continue
        surface = face.Surface
        axis = _app().Vector(surface.Axis)
        axis.normalize()
        centre = _app().Vector(surface.Center if name == "Cylinder" else surface.Apex)
        ts = [(v.Point - centre).dot(axis) for v in face.Vertexes]
        for edge in face.Edges:
            if type(edge.Curve).__name__ == "Circle":
                ts.append((edge.Curve.Center - centre).dot(axis))
        if not ts:
            continue
        if name == "Cylinder":
            radius = surface.Radius
        else:
            radius = max((e.Curve.Radius for e in face.Edges if type(e.Curve).__name__ == "Circle"), default=0.0)
        out.append(Bore(leaf, f"Face{i}", name.lower(), radius, centre, axis, _internal(face), min(ts), max(ts)))
    return out


def _on_axis(point, centre, axis, tol=TOL):
    rel = point - centre
    return (rel - axis * rel.dot(axis)).Length < tol


def _parallel(a, b):
    return abs(abs(a.dot(b)) - 1.0) < 1e-6


def coaxial(b, centre, axis) -> bool:
    return _parallel(b.axis, axis) and _on_axis(b.centre, centre, axis)


def circle_at(shape, point, axis, radius=None):
    """The name of a circular edge of ``shape`` centred at ``point`` on ``axis`` (the largest, or the one of
    ``radius``), or ""."""
    best, best_r = "", -1.0
    for i, edge in enumerate(shape.Edges, 1):
        curve = edge.Curve
        if type(curve).__name__ != "Circle" or not edge.isClosed():
            continue
        if (curve.Center - point).Length > TOL or not _parallel(_app().Vector(curve.Axis), axis):
            continue
        if radius is not None:
            if abs(curve.Radius - radius) < TOL:
                return f"Edge{i}"
            continue
        if curve.Radius > best_r:
            best, best_r = f"Edge{i}", curve.Radius
    return best


# ----- hole lines and screws (ASM-09) -------------------------------------------------------------------------------


@dataclass
class Line:
    """Coaxial hole walls across components, ordered along ``down`` (the way the screw goes in)."""

    centre: object
    down: object
    bores: list
    size: str = ""
    head: str = ""
    seat: float = 0.0  # along down from centre: where the head bears
    seat_leaf: Leaf = None
    thread: Bore = None  # the tapped bore, or None for a nut
    thread_from: float = 0.0
    thread_depth: float = 0.0
    exit: float = 0.0  # the far face, for a nut
    exit_leaf: Leaf = None
    clamp: float = 0.0
    reason: str = ""  # why there is no screw for it

    def at(self, t):
        return self.centre + self.down * t

    @property
    def components(self):
        seen = []
        for b in self.bores:
            if b.leaf.component not in seen:
                seen.append(b.leaf.component)
        return seen


def _span(b, centre, down):
    """(start, end) of a bore along ``down`` from ``centre``."""
    base = (b.centre - centre).dot(down)
    sign = 1.0 if b.axis.dot(down) > 0 else -1.0
    a, z = base + sign * b.lo, base + sign * b.hi
    return min(a, z), max(a, z)


def _clearance_size(dia):
    hw = _hw()
    for size, (fine, _medium, coarse) in hw.CLEARANCE.items():
        if fine - TOL * 10 <= dia <= coarse + TOL * 10:
            return size
    return ""


def _threaded_for(dia, size):
    t = _hw().thread(size)
    return min(t.tap_drill, t.minor) - TOL * 50 <= dia <= t.major + TOL * 10


def _blind(leaf, point):
    try:
        return leaf.shape.isInside(point, TOL, True)
    except Exception:  # noqa: BLE001
        return False


def hole_lines(assembly, comps=None):
    """Every line of coaxial hole walls in the assembly's non-fastener components."""
    comps = components(assembly) if comps is None else comps
    walls = []
    for comp in comps:
        for leaf in leaves(assembly, comp):
            if is_fastener_leaf(leaf):
                continue
            walls += [b for b in bores(leaf) if b.internal]
    lines = []
    for b in sorted(walls, key=lambda w: -w.radius):
        for line in lines:
            if coaxial(b, line.centre, line.down):
                line.bores.append(b)
                break
        else:
            lines.append(Line(b.centre, b.axis, [b]))
    return lines


def plan(line):
    """Fill in the screw for a line: size, head, seat, thread or nut, clamp. Sets ``reason`` when it has none."""
    App = _app()
    cyl = [b for b in line.bores if b.kind == "cylinder"]
    if not cyl:
        line.reason = "no cylindrical hole"
        return line
    # which end does the screw go in? Towards the bottom of a blind (tapped) hole; else from above.
    for down in (line.down, -line.down):
        far = max((_span(b, line.centre, down)[1], b) for b in line.bores)
        if _blind(far[1].leaf, line.centre + down * (far[0] + 10 * TOL)):
            line.down = down
            break
    else:
        line.down = line.down if line.down.dot(App.Vector(0, 0, -1)) >= 0 else -line.down
    spans = sorted(((*_span(b, line.centre, line.down), b) for b in line.bores), key=lambda s: (s[0], -s[2].radius))
    first_leaf = spans[0][2].leaf
    # the size: the clearance hole of the first component (its narrowest wall)
    first = [s for s in spans if s[2].leaf is first_leaf and s[2].kind == "cylinder"]
    if not first:
        line.reason = "the hole starts with a cone only"
        return line
    narrow = min(first, key=lambda s: s[2].radius)
    size = _clearance_size(2 * narrow[2].radius)
    if not size:
        line.reason = f"Ø{2 * narrow[2].radius:.2f} is not an ISO 273 clearance hole"
        return line
    line.size = size
    cone = next((s for s in spans if s[2].leaf is first_leaf and s[2].kind == "cone"), None)
    wider = [s for s in first if s[2].radius > narrow[2].radius + TOL]
    if cone is not None:
        line.head, line.seat = COUNTERSUNK, cone[0]
    elif wider:  # counterbore: the head bears on its floor
        line.head, line.seat = screw_type(), max(s[1] for s in wider)
    else:
        line.head, line.seat = screw_type(), narrow[0]
    line.seat_leaf = first_leaf
    # the thread: the first wall after the clearance holes that is threaded for the size
    for start, end, b in spans:
        if b.kind != "cylinder" or b.leaf is first_leaf:
            continue
        dia = 2 * b.radius
        if _threaded_for(dia, size):
            line.thread, line.thread_from, line.thread_depth = b, start, end - start
            break
        if _clearance_size(dia) != size and dia < _hw().clearance(size, "Fine") - TOL * 10:
            line.reason = f"Ø{dia:.2f} in {b.leaf.component.Label} is neither {size} clearance nor thread"
            return line
    if line.thread is not None:
        line.clamp = line.thread_from - line.seat
    else:
        end = max(spans, key=lambda s: s[1])
        line.exit, line.exit_leaf = end[1], end[2].leaf
        line.clamp = line.exit - line.seat
        if len(line.components) < 2:
            line.reason = f"only {first_leaf.component.Label}: no part to bolt it to"
            return line
    if line.clamp <= 0:
        line.reason = "the thread starts at the seat"
    return line


_NUT_H = {}


def nut_height(size):
    """The ISO 4032 nut's height for ``size``, measured from the add-on's model."""
    App = _app()
    if size in _NUT_H:
        return _NUT_H[size]
    doc = App.newDocument("BwMatingNut", hidden=True)
    try:
        nut = _bh().make_fastener(doc, NUT, size)
        doc.recompute()
        _NUT_H[size] = nut.Shape.BoundBox.ZLength
        return _NUT_H[size]
    finally:
        App.closeDocument(doc.Name)


def choose_length(ftype, size, clamp, thread_depth=None, nut=None, min_engagement=None):
    """(length text, engagement) by the module's stated rule; raises MatingError with the reason."""
    lengths = []
    for text in _bh().fastener_lengths(ftype, size):
        try:
            lengths.append((float(text), text))
        except ValueError:
            continue
    if not lengths:
        raise MatingError(f"{ftype} {size} has no standard lengths")
    lengths.sort()
    if nut is not None:
        need = clamp + nut
        fit = [lt for lt in lengths if lt[0] >= need - TOL]
        if not fit:
            raise MatingError(f"no {ftype} {size} is {need:.1f} mm long or more (clamp {clamp:.1f} + nut {nut:.1f})")
        return fit[0][1], fit[0][0] - clamp
    d = _hw().thread(size).major
    e_min = (engagement() if min_engagement is None else min_engagement) * d
    top = clamp + thread_depth
    fit = [lt for lt in lengths if lt[0] <= top + TOL]
    if not fit:
        raise MatingError(f"the shortest {ftype} {size} is longer than clamp {clamp:.1f} + thread {thread_depth:.1f}")
    best = fit[-1]
    if best[0] - clamp < e_min - TOL:
        raise MatingError(f"{ftype} {size}x{best[1]} engages {best[0] - clamp:.1f} mm, less than "
                          f"{e_min:.1f} mm ({PREF_ENGAGEMENT} {e_min / d:g} x d); the thread is "
                          f"{thread_depth:.1f} mm deep")
    return best[1], best[0] - clamp


def occupied(assembly, line, comps=None):
    """Is a fastener (or any part) already in the line's clearance hole?"""
    probe = line.at(line.seat + max(line.clamp, 0) / 2)
    for comp in (components(assembly) if comps is None else comps):
        if comp in line.components:
            continue
        for leaf in leaves(assembly, comp):
            if leaf.shape.BoundBox.isInside(probe) and leaf.shape.isInside(probe, TOL, True):
                return True
    return False


def _placement(origin, z):
    App = _app()
    return App.Placement(origin, App.Rotation(App.Vector(0, 0, 1), z))


def _add_fastener(assembly, session, ftype, size, length, placement):
    """The fastener component: the vault's COTS item when ``session``, else the add-on's object."""
    bh = _bh()
    if session is not None:
        link, _pn, _made = bh.insert_fastener(session, assembly, ftype, size, length, placement)
        return link
    obj = bh.make_fastener(assembly.Document, ftype, size, length)
    assembly.addObject(obj)
    obj.Placement = placement
    obj.Label = bh.link_label(ftype, size, length, assembly.Document)
    return obj


@dataclass
class Placed:
    line: Line
    screw: object = None
    nut: object = None
    length: str = ""
    engagement: float = 0.0
    joints: list = field(default_factory=list)


@dataclass
class BoltReport:
    placed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (Line, reason)
    moved: list = field(default_factory=list)

    def text(self):
        rows = [f"{len(self.placed)} fastener(s) placed"]
        for p in self.placed:
            what = f"{p.line.head} {p.line.size}x{p.length}"
            what += f" + {NUT} nut" if p.nut is not None else f", {p.engagement:.1f} mm in the thread"
            rows.append(f"  {p.screw.Label}: {what}, clamp {p.line.clamp:.1f} mm")
        for line, reason in self.skipped:
            rows.append(f"  skipped at ({line.centre.x:.1f}, {line.centre.y:.1f}, {line.centre.z:.1f}): {reason}")
        if self.moved:
            rows.append("  MOVED: " + ", ".join(self.moved))
        return "\n".join(rows)


def bolt(assembly, target=None, session=None):
    """Bolt Up: a screw (and a nut where there is no thread) in every free hole line through ``target`` (a
    component; None: all), each jointed on its seat. The caller opens the transaction. Returns a BoltReport."""
    App = _app()
    if session is None and target is None and not components(assembly):
        raise MatingError("the assembly has no components")
    comps = components(assembly)
    before = snapshot(comps)
    report = BoltReport()
    for line in hole_lines(assembly, comps):
        if target is not None and target not in line.components:
            continue
        plan(line)
        if line.reason:
            if len(line.components) > 1 or target is not None:
                report.skipped.append((line, line.reason))
            continue
        if occupied(assembly, line, comps):
            continue
        try:
            if line.thread is None:
                length, engaged = choose_length(line.head, line.size, line.clamp, nut=nut_height(line.size))
            else:
                length, engaged = choose_length(line.head, line.size, line.clamp, line.thread_depth)
        except (MatingError, ValueError) as exc:
            report.skipped.append((line, str(exc)))
            continue
        up = -line.down
        seat = line.at(line.seat)
        screw = _add_fastener(assembly, session, line.head, line.size, length, _placement(seat, up))
        placed = Placed(line, screw, length=length, engagement=engaged)
        assembly.Document.recompute()
        joint, why = seat_joint(assembly, screw, line.seat_leaf, seat, up)
        if joint is None:
            report.skipped.append((line, f"{screw.Label} placed but not jointed: {why}"))
        else:
            placed.joints.append(joint)
        if line.thread is None:
            exit_point = line.at(line.exit)
            nut = _add_fastener(assembly, session, NUT, line.size, None, _placement(exit_point, line.down))
            placed.nut = nut
            assembly.Document.recompute()
            joint, why = seat_joint(assembly, nut, line.exit_leaf, exit_point, line.down)
            if joint is None:
                report.skipped.append((line, f"{nut.Label} placed but not jointed: {why}"))
            else:
                placed.joints.append(joint)
        report.placed.append(placed)
    report.moved = moved(before)
    if report.moved:
        restore(before)
    App.Console.PrintLog(f"BruceWorx Bolt Up: {len(report.placed)} placed\n")
    return report


# ----- joints that keep the parts where they are ----------------------------------------------------------------------


def snapshot(comps):
    return {c: _app().Placement(c.Placement) for c in comps}


def moved(before):
    out = []
    for comp, plc in before.items():
        try:
            now = comp.Placement
        except Exception:  # noqa: BLE001 -- deleted
            continue
        if (now.Base - plc.Base).Length > MOVE_TOL or abs(now.Rotation.multiply(plc.Rotation.inverted()).Angle) > MOVE_TOL:
            out.append(comp.Label)
    return out


def restore(before):
    for comp, plc in before.items():
        try:
            if comp.Placement != plc:
                comp.Placement = plc
        except Exception:  # noqa: BLE001
            pass


def ref_of(assembly, leaf, element):
    """A joint reference ([component, [sub, sub]]) to ``element`` of a leaf, as a 3D pick builds it."""
    import bruceworx_asm_model as am

    ref = am.pick_ref(assembly, assembly.Document.Name, assembly.Name, leaf.path + element)
    if ref is None:
        raise MatingError(f"cannot reference {leaf.path}{element}")
    return ref


def _jcs(joint_type, ref):
    import UtilsAssembly

    plc = UtilsAssembly.findPlacement(ref, joint_type == "Distance")
    return UtilsAssembly.getJcsGlobalPlc(plc, ref)


def residual_ok(key, plc1, plc2):
    """Do two connectors already satisfy mate ``key`` (before any offset)? (ok, why not)."""
    App = _app()
    z1 = plc1.Rotation.multVec(App.Vector(0, 0, 1))
    z2 = plc2.Rotation.multVec(App.Vector(0, 0, 1))
    if not _parallel(z1, z2):
        return False, f"axes {math.degrees(z1.getAngle(z2)):.2f} deg apart"
    gap = plc1.Base - plc2.Base
    along = gap.dot(z2)
    across = (gap - z2 * along).Length
    if key in ("coincident",) and abs(along) > TOL:
        return False, f"faces {abs(along):.3f} mm apart"
    if key in ("concentric",) and across > TOL:
        return False, f"axes {across:.3f} mm apart"
    if key in ("hinge", "lock") and gap.Length > TOL:
        return False, f"{abs(along):.3f} mm off the seat" if across <= TOL else f"{across:.3f} mm off the axis"
    return True, ""


def make_joint(assembly, key, ref1, ref2, label=None):
    """A mate between two references, fitted to where the parts are, solved, and kept only if nothing moved.
    Returns (joint, "") or (None, why)."""
    App = _app()
    import JointObject
    import UtilsAssembly
    import bruceworx_asm_model as am

    jtype = am.joint_type_of(key)
    try:
        plc1, plc2 = _jcs(jtype, ref1), _jcs(jtype, ref2)
    except Exception as exc:  # noqa: BLE001
        return None, f"no connector: {exc}"
    ok, why = residual_ok(key, plc1, plc2)
    if not ok:
        return None, why
    comps = components(assembly)
    before = snapshot(comps)
    group = UtilsAssembly.getJointGroup(assembly)
    joint = group.newObject("App::FeaturePython", "Joint")
    JointObject.Joint(joint, JointObject.JointTypes.index(jtype))
    if App.GuiUp:
        JointObject.ViewProviderJoint(joint.ViewObject)
    am._store_key(joint, key)
    joint.Label = label or am._next_label(assembly, key, ignore=joint)
    if hasattr(joint, "KeepPosition"):
        joint.KeepPosition = True
    if key in ("coincident", "tangent"):
        joint.Distance = 0
    # the connectors' difference goes into Offset2 before the references (setting it later solves first)
    joint.Offset2 = plc2.inverse().multiply(plc1)
    joint.Proxy.setJointConnectors(joint, [am._as_list(ref1), am._as_list(ref2)])
    code = assembly.solve()
    gone = moved(before)
    if code != 0 or gone:
        assembly.Document.removeObject(joint.Name)
        restore(before)
        assembly.solve()
        restore(before)
        return None, (f"solving it moved {', '.join(gone)}" if gone else f"the solver failed ({code})")
    return joint, ""


def seat_joint(assembly, fastener, seat_leaf, point, up):
    """Revolute between a fastener's bearing circle (its z = 0) and the hole's rim at ``point``."""
    fl = next(iter(leaves(assembly, fastener)), None)
    if fl is None:
        return None, "the fastener has no shape"
    mine = circle_at(fl.shape, point, up)
    theirs = circle_at(seat_leaf.shape, point, up)
    if not mine or not theirs:
        return None, "no circular edge at the seat" if mine else "the fastener has no circle at its seat"
    return make_joint(assembly, "hinge", ref_of(assembly, seat_leaf, theirs), ref_of(assembly, fl, mine),
                      label=f"{fastener.Label} on {seat_leaf.component.Label}")


# ----- mate inference (ASM-11) --------------------------------------------------------------------------------------


@dataclass
class InferReport:
    grounded: object = None
    joints: list = field(default_factory=list)  # (joint, what)
    unmated: list = field(default_factory=list)  # (component label, reason)
    refused: list = field(default_factory=list)  # (what, why)
    moved: list = field(default_factory=list)

    def text(self):
        rows = [f"{len(self.joints)} joint(s) made" + (f"; {self.grounded.Label} grounded" if self.grounded else "")]
        rows += [f"  {j.Label}: {what}" for j, what in self.joints]
        rows += [f"  not mated: {label}: {why}" for label, why in self.unmated]
        rows += [f"  not made: {what}: {why}" for what, why in self.refused]
        if self.moved:
            rows.append("  MOVED (hard check failed): " + ", ".join(self.moved))
        else:
            rows.append("  hard check: every part is where it was")
        return "\n".join(rows)


def _grounded(assembly):
    import UtilsAssembly

    out = []
    for obj in UtilsAssembly.getJointGroup(assembly).Group:
        target = getattr(obj, "ObjectToGround", None)
        if target is not None:
            out.append(target)
    return out


def ground(assembly, comp):
    import JointObject
    import UtilsAssembly

    joint = UtilsAssembly.getJointGroup(assembly).newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(joint, comp)
    if _app().GuiUp:
        try:
            JointObject.ViewProviderGroundedJoint(joint.ViewObject)
        except Exception:  # noqa: BLE001
            pass
    return joint


def _volume(assembly, comp):
    return sum(leaf.shape.Volume for leaf in leaves(assembly, comp))


def _planes(leaf):
    out = []
    for i, face in enumerate(leaf.shape.Faces, 1):
        if type(face.Surface).__name__ != "Plane":
            continue
        u0, u1, v0, v1 = face.ParameterRange
        normal = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        out.append((f"Face{i}", face, normal, face.CenterOfMass))
    return out


def planar_contacts(la, lb):
    """[(area, face of a, face of b, normal of a)] of faces of two leaves that touch face to face."""
    out = []
    box = la.shape.BoundBox
    box.enlarge(TOL * 10)
    if not box.intersect(lb.shape.BoundBox):
        return out
    pb = _planes(lb)
    for na, fa, ka, ca in _planes(la):
        fbox = fa.BoundBox
        fbox.enlarge(TOL * 10)
        for nb, fb, kb, cb in pb:
            if ka.dot(kb) > -1 + 1e-6 or not fbox.intersect(fb.BoundBox):
                continue
            if abs((cb - ca).dot(ka)) > TOL:
                continue
            try:
                area = fa.common(fb).Area
            except Exception:  # noqa: BLE001
                continue
            if area > TOL:
                out.append((area, na, nb, ka))
    return sorted(out, key=lambda c: -c[0])


def shaft_contacts(la, lb):
    """[(shaft leaf, shaft face, bore leaf, bore face, axis)]: a cylinder of one inside a coaxial bore of the other."""
    out = []
    ba, bb = bores(la), bores(lb)
    for shafts, holes, ls, lh in ((ba, bb, la, lb), (bb, ba, lb, la)):
        for s in shafts:
            if s.internal or s.kind != "cylinder":
                continue
            for h in holes:
                if not h.internal or h.kind != "cylinder" or not coaxial(h, s.centre, s.axis):
                    continue
                if s.radius > h.radius + TOL:
                    continue
                s0, s1 = _span(s, s.centre, s.axis)
                h0, h1 = _span(h, s.centre, s.axis)
                if min(s1, h1) - max(s0, h0) > TOL:
                    out.append((ls, s.face, lh, h.face, s.axis))
    return out


def _direction_key(vector):
    v = _app().Vector(vector)
    if v.x < -ANG or (abs(v.x) <= ANG and (v.y < -ANG or (abs(v.y) <= ANG and v.z < 0))):
        v = -v
    return (round(v.x, 4), round(v.y, 4), round(v.z, 4))


def fastener_seat(assembly, fl, others):
    """(seat leaf, point, up) where a fastener bears, or (None, gap text) when it floats."""
    frame = _frame(assembly, fl)
    point = frame.Base
    up = frame.Rotation.multVec(_app().Vector(0, 0, 1))
    for leaf in others:
        if circle_at(leaf.shape, point, up):
            return leaf, point, up
    # floating: how far is the nearest coaxial hole's end?
    gaps = []
    for leaf in others:
        for b in bores(leaf):
            if b.internal and coaxial(b, point, up):
                lo, hi = _span(b, point, -up)
                gaps.append((min(abs(lo), abs(hi)), leaf))
    if gaps:
        gap, leaf = min(gaps, key=lambda g: g[0])
        return None, f"floating: {gap:.2f} mm off its seat on {leaf.component.Label}", None
    return None, "floating: in no hole", None


def infer(assembly, ground_comp=None):
    """Mate Inference: joints from the geometry, every part kept where it is. The caller opens the transaction.
    Returns an InferReport."""
    comps = components(assembly)
    if not comps:
        raise MatingError("the assembly has no components")
    before = snapshot(comps)
    report = InferReport()
    all_leaves = {c: leaves(assembly, c) for c in comps}
    fast = {c for c, ls in all_leaves.items() if ls and all(is_fastener_leaf(leaf) for leaf in ls)}
    parts = [c for c in comps if c not in fast and all_leaves[c]]
    grounded = _grounded(assembly)
    if not grounded and parts:
        chosen = ground_comp or max(parts, key=lambda c: _volume(assembly, c))
        ground(assembly, chosen)
        report.grounded = chosen
        assembly.solve()
    mated = set(_grounded(assembly))
    # 1. parts against parts: one coincident per contact direction, one concentric per axis
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            directions, axes = set(), []
            for la in all_leaves[a]:
                for lb in all_leaves[b]:
                    for _area, fa, fb, normal in planar_contacts(la, lb):
                        key = _direction_key(normal)
                        if key in directions:
                            continue
                        joint, why = make_joint(assembly, "coincident", ref_of(assembly, la, fa),
                                                ref_of(assembly, lb, fb), label=f"{a.Label} on {b.Label}")
                        what = f"coincident {a.Label}.{fa} / {b.Label}.{fb}"
                        if joint is None:
                            report.refused.append((what, why))
                            continue
                        directions.add(key)
                        report.joints.append((joint, what))
                        mated.update((a, b))
                    for ls, fs, lh, fh, axis in shaft_contacts(la, lb):
                        centre = ls.shape.getElement(fs).Surface.Center
                        if any(_parallel(ax, axis) and _on_axis(centre, c0, ax) for c0, ax in axes):
                            continue
                        joint, why = make_joint(assembly, "concentric", ref_of(assembly, lh, fh),
                                                ref_of(assembly, ls, fs),
                                                label=f"{ls.component.Label} in {lh.component.Label}")
                        what = f"concentric {ls.component.Label}.{fs} / {lh.component.Label}.{fh}"
                        if joint is None:
                            report.refused.append((what, why))
                            continue
                        axes.append((centre, axis))
                        report.joints.append((joint, what))
                        mated.update((a, b))
    # 2. fasteners: screws, nuts and washers on their seats; pins concentric in what they pass through
    part_leaves = [leaf for c in parts for leaf in all_leaves[c]]
    for comp in sorted(fast, key=lambda c: c.Name):
        fl = all_leaves[comp][0]
        kind = fastener_kind(fl)
        if kind == "pin":
            made = 0
            for lh in part_leaves:
                for ls, fs, lb, fb, _axis in shaft_contacts(fl, lh):
                    if ls is not fl:
                        continue
                    joint, why = make_joint(assembly, "concentric", ref_of(assembly, lb, fb), ref_of(assembly, fl, fs),
                                            label=f"{comp.Label} in {lb.component.Label}")
                    if joint is None:
                        report.refused.append((f"{comp.Label} in {lb.component.Label}", why))
                    else:
                        report.joints.append((joint, f"pin {comp.Label} concentric in {lb.component.Label}"))
                        made += 1
                        mated.update((comp, lb.component))
                    break
            if not made:
                report.unmated.append((comp.Label, "pin: in no bore"))
            continue
        others = part_leaves + [all_leaves[c][0] for c in fast if c is not comp and all_leaves[c]]
        seat_leaf, point, up = fastener_seat(assembly, fl, others)
        if seat_leaf is None:
            report.unmated.append((comp.Label, point))
            continue
        joint, why = seat_joint(assembly, comp, seat_leaf, point, up)
        if joint is None:
            report.unmated.append((comp.Label, why))
        else:
            report.joints.append((joint, f"{kind} {comp.Label} on {seat_leaf.component.Label}"))
            mated.update((comp, seat_leaf.component))
    for comp in parts:
        if comp not in mated:
            report.unmated.append((comp.Label, "touches no other part (no shared face or axis)"))
    report.moved = moved(before)
    if report.moved:
        restore(before)
    return report
