# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly mates for agents (AUT-02): read the joints, check the mate rules, knock every part and see it return.

Promoted from the scripts that mated the TL-P01 toolchanger (7 to 9 Nov 2026). The mate rules (Bruce):

* a joint references geometry (a face, an edge, a vertex) on both sides; it stores no position of its own
  (KeepPosition off, no offset other than a distance along the joint's axis);
* exactly one grounded part in an assembly;
* no second joint between two parts that the first already holds;
* a mated assembly holds: move any part a few millimetres and degrees, solve, and it is back where it was. A
  part on a hinge may have turned about the hinge's axis, a part on a slider may have slid along it, and a
  fastener may spin about its own long axis: that is their freedom, not a fault.

    joints(asm), refs(joint), joint_info(joint)               reading
    parts(asm), grounded(asm), ground(asm, part)              components that move on their own; the fixed one
    solve(asm)                -> {"ok", "code", "seconds", "text"}
    snapshot(asm), restore(asm, snap), moved(asm, snap, tol)  placements
    free_motion(asm)          -> {part: [freedom]}             what a hinge, slider or cylindrical joint leaves free
    rule_check(asm)           -> {"ok", "problems", "warnings", ...}
    knock_test(asm, seed=0, parts=None, tol_mm=1e-4, tol_deg=1e-3, step=None)
                              -> {"ok", "checked", "returned", "failed", "free", "unchecked", "seconds", ...}
    KnockTest(asm, ...)       the same a few parts at a time: step(seconds), cancel(), close(), result()
    stale_joints(asm, fix=False)

Parts and joints are named by their Name in results ("label" beside it). The components of a flexible
sub-assembly (an AssemblyLink that is not Rigid) count as parts of their own, as the solver sees them.

What is already in bruceworx_asm_model is used from there (what a mate is, reading a reference, the solve that
keeps the Mates pane's Result, components, transactions). Mate inference (which joints a placed assembly should
get) is not here; it stays experimental.
"""

from __future__ import annotations

import heapq
import math
import random
import re
import time

import bruceworx_asm_model as am

TOL_MM = 1e-4
TOL_DEG = 1e-3
KNOCK_MM = (2.0, 5.0)  # how far a part is knocked, and
KNOCK_DEG = (1.0, 3.0)  # how far it is turned, about a random axis through its origin
ELEMENT = re.compile(r"(Face|Edge|Vertex)\d+$")
FREE = {"Revolute": "spin", "Slider": "slide", "Cylindrical": "spin+slide"}  # the freedom a joint is there to leave
SOLVE_TEXT = {0: "solved", -1: "solver error", -2: "redundant constraints", -3: "conflicting constraints",
              -4: "over-constrained", -5: "malformed constraints", -6: "no part is grounded"}
# Degrees of freedom a joint takes from a pair of parts; a Distance joint by what it joins (rule_check's warning).
_TAKES = {"Fixed": 6, "Revolute": 5, "Slider": 5, "Cylindrical": 4, "Ball": 3, "Parallel": 2, "Perpendicular": 1,
          "Angle": 1}


def _app():
    import FreeCAD

    return FreeCAD


# ----- reading -------------------------------------------------------------------------------------------------


def joint_group(asm):
    """The assembly's joint group, or None (never made here: reading an assembly does not change it)."""
    return next((o for o in asm.Group if o.TypeId == "Assembly::JointGroup"), None)


def _in_group(asm):
    group = joint_group(asm)
    return list(group.Group) if group is not None else []


def _flexible(asm, depth=0):
    """The flexible sub-assemblies in ``asm``, nested ones too: their parts and joints are solved with it."""
    out = []
    for obj in asm.Group:
        if obj.isDerivedFrom("Assembly::AssemblyLink") and not getattr(obj, "Rigid", True) and depth < 16:
            out.append(obj)
            out.extend(_flexible(obj, depth + 1))
    return out


def joints(asm, suppressed=True, nested=True) -> list:
    """The assembly's joints (not its grounds), with those of its flexible sub-assemblies (nested=False: its
    own only). suppressed=False leaves the switched-off ones out."""
    out = [o for o in _in_group(asm) if am._is_mate(o)]
    if nested:
        for sub in _flexible(asm):
            out.extend(o for o in _in_group(sub) if am._is_mate(o))
    if not suppressed:
        out = [j for j in out if not getattr(j, "Suppressed", False)]
    return out


def _ground_joints(asm):
    return [o for o in _in_group(asm) if hasattr(o, "ObjectToGround")]


def grounded(asm) -> list:
    """The grounded parts, each once."""
    out = []
    for g in _ground_joints(asm):
        obj = g.ObjectToGround
        if obj is not None and obj not in out:
            out.append(obj)
    return out


def ground(asm, part):
    """Ground ``part``; the grounded joint (the one it has already, if it is grounded)."""
    import JointObject

    for g in _ground_joints(asm):
        if g.ObjectToGround == part:
            return g
    with am._transaction(asm.Document, "Ground part"):
        group = joint_group(asm) or asm.newObject("Assembly::JointGroup", "Joints")
        g = group.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(g, part)
        if _app().GuiUp:
            JointObject.ViewProviderGroundedJoint(g.ViewObject)
    return g


def members(asm) -> list:
    """[(part, path prefix, placement of the frame it sits in)]: the assembly's components, and those of each
    flexible sub-assembly in it (they move on their own). The prefix is "" for the assembly's own."""
    App = _app()
    out = []

    def walk(container, prefix, outer, depth):
        for obj in am._components(container):
            if obj.isDerivedFrom("Assembly::AssemblyLink") and not getattr(obj, "Rigid", True) and depth < 16:
                walk(obj, prefix + obj.Name + ".", outer.multiply(obj.Placement), depth + 1)
            else:
                out.append((obj, prefix, outer))

    walk(asm, "", App.Placement(), 0)
    return out


def parts(asm) -> list:
    """The components that move on their own."""
    return [obj for obj, _prefix, _outer in members(asm)]


def _part_of(ref, names):
    """The Name of the part a reference is on: its root, or the first part named in its path when the
    reference is rooted at the assembly."""
    comp, sub = am._ref_parts(ref)
    if comp is None:
        return None
    if comp.Name in names:
        return comp.Name
    for token in sub.split(".")[:-1]:
        if token in names:
            return token
    return None


def _refs(joint, asm, labels):
    out = []
    for index in (1, 2):
        ref = getattr(joint, f"Reference{index}", None)
        comp, sub = am._ref_parts(ref)
        if comp is None:
            continue
        part = _part_of(ref, labels)
        rooted_at_assembly = asm is not None and comp == asm
        out.append({
            "index": index,
            "part": part,
            "label": labels.get(part, "") if part else "",
            "root": comp.Name,
            "sub": sub,
            "path": sub if rooted_at_assembly else (f"{comp.Name}.{sub}" if sub else comp.Name),
            "element": sub.split(".")[-1] if sub else "",
        })
    return out


def _labels(asm):
    return {obj.Name: obj.Label for obj in parts(asm)} if asm is not None else {}


def refs(joint, asm=None) -> list:
    """The joint's references, one dict each: "index" (1 or 2), "part" (Name, None when it is on no part of the
    assembly), "label", "path" (from the assembly's children: "Link.Body.Face3"), "element" ("Face3"; "" for a
    reference to a whole object), "root" and "sub" (as stored). A joint with a missing reference gives fewer
    than two."""
    if asm is None:
        try:
            asm = am._assembly_of(joint)
        except Exception:
            asm = None
    return _refs(joint, asm, _labels(asm))


def _value(prop):
    return round(float(getattr(prop, "Value", prop)), 6)


def _joint_info(joint, asm, labels):
    info = {"joint": joint.Name, "label": joint.Label, "type": joint.JointType,
            "suppressed": bool(getattr(joint, "Suppressed", False)), "refs": _refs(joint, asm, labels)}
    try:
        info["mate"] = am.mate_key(joint)
    except Exception:
        info["mate"] = ""
    if joint.JointType in ("Distance", "RackPinion", "Screw", "Gears", "Belt") and hasattr(joint, "Distance"):
        info["distance"] = _value(joint.Distance)
    if joint.JointType == "Angle" and hasattr(joint, "Angle"):
        info["angle"] = _value(joint.Angle)
    if joint.JointType in ("Fixed", "Revolute") and hasattr(joint, "Offset2"):
        info["offset"] = round(joint.Offset2.Base.z, 6)
    return info


def joint_info(joint, asm=None) -> dict:
    """One joint as plain data: Name, label, type, mate key, references, and the value it carries."""
    if asm is None:
        try:
            asm = am._assembly_of(joint)
        except Exception:
            asm = None
    return _joint_info(joint, asm, _labels(asm))


# ----- solving and placements ------------------------------------------------------------------------------------


def solve(asm, recompute=False) -> dict:
    """Solve; {"ok", "code", "seconds", "text"}. The code is the solver's (0 solved; SOLVE_TEXT names the
    others). recompute=True recomputes the document afterwards (slow on a large assembly, and not needed to
    read placements: the solver sets them)."""
    started = time.time()
    code = am._solve(asm)
    if recompute:
        asm.Document.recompute()
    return {"ok": code == 0, "code": code, "seconds": round(time.time() - started, 3),
            "text": SOLVE_TEXT.get(code, f"code {code}")}


def snapshot(asm) -> dict:
    """{part Name: a copy of its Placement}"""
    App = _app()
    return {obj.Name: App.Placement(obj.Placement) for obj in parts(asm)}


def restore(asm, snap) -> None:
    """Put every part of the snapshot back (parts that are gone are skipped)."""
    doc = asm.Document
    for name, placement in snap.items():
        obj = doc.getObject(name)
        if obj is not None and not obj.Placement.isSame(placement, 1e-12):
            obj.Placement = placement


def _turn(p, q):
    """Degrees between two rotations; 359.999998 is no turn (7 Nov: X Axis T-nuts were flagged for it)."""
    a = math.degrees(q.Rotation.multiply(p.Rotation.inverted()).Angle)
    return min(a, 360.0 - a)


def _between(u, v):
    return math.degrees(math.acos(max(-1.0, min(1.0, u.dot(v)))))


def _off(p, q, freedom=None):
    """(mm, deg) that placement q is off p; with a freedom, only what the freedom does not allow."""
    if freedom is None:
        return (q.Base - p.Base).Length, _turn(p, q)
    kind, point, axis = freedom["kind"], freedom["point"], freedom["axis"]
    delta = q.multVec(point) - p.multVec(point)
    along = p.Rotation.multVec(axis)
    if kind == "spin":  # the point on the axis stays, the axis keeps its direction
        return delta.Length, _between(along, q.Rotation.multVec(axis))
    across = (delta - along * delta.dot(along)).Length
    if kind == "slide":  # no turn at all, no move across the axis
        return across, _turn(p, q)
    return across, _between(along, q.Rotation.multVec(axis))  # spin+slide


def moved(asm, snap, tol=TOL_MM, tol_deg=TOL_DEG, allow=None, names=None) -> list:
    """The parts that are not where the snapshot has them: [{"part", "label", "mm", "deg"}]. ``allow`` is
    free_motion(asm): a part that is off only by a freedom it has is not listed. A part that is gone is listed
    with "gone"."""
    doc = asm.Document
    out = []
    for name, p in snap.items():
        if names is not None and name not in names:
            continue
        obj = doc.getObject(name)
        if obj is None:
            out.append({"part": name, "label": name, "gone": True})
            continue
        q = obj.Placement
        d, a = _off(p, q)
        if d <= tol and a <= tol_deg:
            continue
        if any(max(d2 - tol, a2 - tol_deg) <= 0 for d2, a2 in (_off(p, q, f) for f in (allow or {}).get(name, ()))):
            continue
        out.append({"part": name, "label": obj.Label, "mm": round(d, 6), "deg": round(a, 6)})
    return out


# ----- the freedom a joint leaves ----------------------------------------------------------------------------------


def _pairs(asm, labels):
    """[(joint, part a, part b)] of the joints in force that join two different parts of the assembly."""
    out = []
    for joint in joints(asm, suppressed=False):
        a = _part_of(getattr(joint, "Reference1", None), labels)
        b = _part_of(getattr(joint, "Reference2", None), labels)
        if a and b and a != b:
            out.append((joint, a, b))
    return out


def _axis_frame(asm, joint):
    """The joint's frame (origin on its axis, Z along it) in the assembly's coordinates, or None."""
    import UtilsAssembly

    for index in (1, 2):
        ref = getattr(joint, f"Reference{index}", None)
        if not UtilsAssembly.isRefValid(ref, 1) or UtilsAssembly.getObject(ref) is None:
            continue
        frame = UtilsAssembly.getJcsGlobalPlc(getattr(joint, f"Placement{index}"), ref)
        return asm.Placement.inverse().multiply(frame)
    return None


def free_motion(asm) -> dict:
    """{part Name: [{"kind", "joint", "point", "axis"}]}: what each part may do and still be where it belongs.

    A Revolute joint leaves a spin about its axis, a Slider a slide along it, a Cylindrical joint both. The
    freedom belongs to the parts the joint joins and to every part that hangs on them (reaches the ground
    through that joint; of several ways to the ground, the one with the fewest free joints counts). "point" and
    "axis" are in the part's own coordinates. moved() accepts a part that is off by one of its freedoms; two
    freedoms at once (a hinge on a slider) are not combined."""
    App = _app()
    mem = {obj.Name: (obj, outer) for obj, _prefix, outer in members(asm)}
    labels = {name: obj.Label for name, (obj, _outer) in mem.items()}
    pairs = _pairs(asm, labels)
    near = {}
    for joint, a, b in pairs:
        near.setdefault(a, []).append((joint, b))
        near.setdefault(b, []).append((joint, a))
    # the way to the ground with the fewest free joints, then the fewest joints
    best, via = {}, {}
    heap = [(0, 0, obj.Name) for obj in grounded(asm) if obj.Name in mem]
    for cost, hops, name in heap:
        best[name] = (cost, hops)
    heapq.heapify(heap)
    while heap:
        cost, hops, name = heapq.heappop(heap)
        if best.get(name, (cost, hops)) < (cost, hops):
            continue
        for joint, other in near.get(name, ()):
            step = (cost + (1 if joint.JointType in FREE else 0), hops + 1)
            if other not in best or step < best[other]:
                best[other] = step
                via[other] = (joint, name)
                heapq.heappush(heap, (step[0], step[1], other))
    frames = {}
    out = {}
    z = App.Vector(0, 0, 1)
    for name, (obj, outer) in mem.items():
        free = [joint for joint, _other in near.get(name, ()) if joint.JointType in FREE]
        at = name
        seen = set()
        while at in via and at not in seen:  # and the free joints on its way to the ground
            seen.add(at)
            joint, at = via[at]
            if joint.JointType in FREE and joint not in free:
                free.append(joint)
        found = []
        for joint in free:
            if joint.Name not in frames:
                try:
                    frames[joint.Name] = _axis_frame(asm, joint)
                except Exception:
                    frames[joint.Name] = None
            frame = frames[joint.Name]
            if frame is None:
                continue
            local = outer.multiply(obj.Placement).inverse().multiply(frame)
            found.append({"kind": FREE[joint.JointType], "joint": joint.Name, "point": local.Base,
                          "axis": local.Rotation.multVec(z)})
        if found:
            out[name] = found
    return out


# ----- the rules ---------------------------------------------------------------------------------------------------


def _takes(joint):
    """Degrees of freedom the joint takes from its pair; at least, when that depends on what it joins."""
    kind = joint.JointType
    if kind in _TAKES:
        return _TAKES[kind]
    if kind == "Distance":
        try:
            return 3 if set(am._kinds(joint)) == {"plane"} else 1
        except Exception:
            return 1
    return 1


def _stored_position(joint):
    """Why the joint holds a position of its own (not only geometry), or ""."""
    App = _app()
    o1 = getattr(joint, "Offset1", None) or App.Placement()
    o2 = getattr(joint, "Offset2", None) or App.Placement()
    if not o1.isIdentity():
        return f"Offset1 is not zero ({_text(o1)})"
    if abs(o2.Base.x) > 1e-9 or abs(o2.Base.y) > 1e-9 or not o2.Rotation.isIdentity():
        return f"Offset2 is more than a distance along the axis ({_text(o2)})"
    return ""


def _text(placement):
    b = placement.Base
    axis = placement.Rotation.Axis
    return (f"move {round(b.x, 4)}, {round(b.y, 4)}, {round(b.z, 4)}; turn "
            f"{round(math.degrees(placement.Rotation.Angle), 4)} deg about {round(axis.x, 3)}, {round(axis.y, 3)}, "
            f"{round(axis.z, 3)}")


def rule_check(asm, solve_too=False) -> dict:
    """Check the assembly against the mate rules without changing it.

    {"ok", "problems": [{"rule", "joint" or "part", "label", "detail"}], "warnings": [...], "joints", "parts",
    "grounded"}. Rules:

    * ground: exactly one grounded part (none, or more than one, is a problem; a ground on nothing too);
    * incomplete: a joint with a missing reference, or one that joins a part to itself or to no part;
    * element_refs: both references name a face, an edge or a vertex (not a whole object or a datum);
    * stale: a reference to an element or object that no longer exists;
    * keep_position / stored_offset: the joint holds the parts by a stored position (KeepPosition on, Offset1
      not zero, Offset2 more than a distance along the joint's axis);
    * doubled: a second joint between two parts a Fixed joint already holds, or the same joint twice;
    * unmated: a part that is not grounded and has no joint in force;
    * solve (only with solve_too=True): the assembly does not solve where it stands, or solving moves a part.
      The solve runs on the assembly and the placements are put back.

    Joints between one pair that together take more than six degrees of freedom are a warning, not a problem: a
    concentric and a coincident mate on one screw overlap without harm. A real over-constraint shows with
    solve_too, and in the knock test. Suppressed joints are not checked."""
    import JointObject

    labels = _labels(asm)
    problems, warnings = [], []

    def problem(rule, detail, joint=None, part=None, into=problems):
        rec = {"rule": rule}
        if joint is not None:
            rec.update(joint=joint.Name, label=joint.Label)
        if part is not None:
            rec.update(part=part, label=labels.get(part, part))
        rec["detail"] = detail
        into.append(rec)

    fixed = grounded(asm)
    if not fixed:
        problem("ground", "no part is grounded")
    elif len(fixed) > 1:
        names = ", ".join(o.Label for o in fixed)
        problem("ground", f"{len(fixed)} parts are grounded ({names}); exactly one may be", part=fixed[1].Name)
    for g in _ground_joints(asm):
        if g.ObjectToGround is None:
            problem("ground", "this ground holds no part", joint=g)

    active = joints(asm, suppressed=False)
    all_joints = joints(asm)
    mated = set()
    between = {}
    for joint in active:
        found = _refs(joint, asm, labels)
        if len(found) < 2:
            problem("incomplete", f"{2 - len(found)} of its 2 references is missing", joint=joint)
        for r in found:
            ref = getattr(joint, f"Reference{r['index']}")
            try:
                missing = "?" if "?" in r["sub"] else JointObject.refMissing(ref)
            except Exception as exc:
                missing = f"{r['path']} ({exc})"
            if missing:
                problem("stale", f"Reference{r['index']}: {missing} no longer exists", joint=joint)
            elif not ELEMENT.search(r["element"]):
                problem("element_refs", f"Reference{r['index']} is {r['path'] or r['root']}: not a face, an edge "
                        "or a vertex", joint=joint)
            if r["part"] is None:
                problem("incomplete", f"Reference{r['index']} ({r['path']}) is on no part of the assembly",
                        joint=joint)
        if getattr(joint, "KeepPosition", False):
            problem("keep_position", "KeepPosition is on: the joint holds the parts where they stood", joint=joint)
        why = _stored_position(joint)
        if why:
            problem("stored_offset", why, joint=joint)
        on = [r["part"] for r in found if r["part"]]
        if len(found) == 2 and len(on) == 2:
            if on[0] == on[1]:
                problem("incomplete", f"both references are on {labels.get(on[0], on[0])}", joint=joint)
            else:
                mated.update(on)
                signature = (joint.JointType, tuple(sorted(r["path"] for r in found)))
                between.setdefault(tuple(sorted(on)), []).append((joint, signature))

    for pair, held in between.items():
        if len(held) < 2:
            continue
        names = " and ".join(labels.get(n, n) for n in pair)
        seen = {}
        flagged = False
        for joint, signature in held:
            if signature in seen:
                problem("doubled", f"the same joint as {seen[signature].Label}, between {names}", joint=joint)
                flagged = True
            seen.setdefault(signature, joint)
        fixed_joints = [j for j, _s in held if j.JointType == "Fixed"]
        if fixed_joints:
            for joint, _s in held:
                if joint is not fixed_joints[0]:
                    problem("doubled", f"{fixed_joints[0].Label} already holds {names}", joint=joint)
            flagged = True
        if not flagged:
            total = sum(_takes(j) for j, _s in held)
            if total > 6:
                problem("doubled", f"{len(held)} joints between {names} take at least {total} of 6 degrees of "
                        f"freedom ({', '.join(j.Label for j, _s in held)}); check that they do not fight",
                        joint=held[1][0], into=warnings)

    fixed_names = {o.Name for o in fixed}
    for name in labels:
        if name not in mated and name not in fixed_names:
            problem("unmated", "not grounded and no joint holds it", part=name)

    out = {"assembly": asm.Name, "parts": len(labels), "joints": len(all_joints),
           "suppressed": len(all_joints) - len(active), "grounded": [o.Name for o in fixed]}
    if solve_too:
        snap = snapshot(asm)
        try:
            result = solve(asm)
            off = moved(asm, snap, allow=free_motion(asm))
        finally:
            restore(asm, snap)
        out["solve"] = dict(result, moved=off[:20])
        if not result["ok"]:
            problem("solve", f"does not solve where it stands: {result['text']} (code {result['code']})")
        for rec in off[:20]:
            problem("solve", f"solving moves it {rec.get('mm')} mm and {rec.get('deg')} deg", part=rec["part"])
    out.update(ok=not problems, problems=problems, warnings=warnings)
    return out


# ----- the knock test ------------------------------------------------------------------------------------------------


class KnockTest:
    """Knocks the parts of an assembly one at a time and sees each come back: ``step(seconds)`` works until
    the time is up and says whether it is done, so a job runner can report progress and cancel in between.

    One step of work is one part: the placements are put back, the part is moved KNOCK_MM in a random direction
    and turned KNOCK_DEG about a random axis (random by ``seed`` and the part's Name, so a part is knocked the
    same way whichever parts are asked for), the assembly is solved, and every part is compared with where it
    was. First of all the assembly is solved where it stands; if that fails nothing is knocked. Last of all,
    when all parts were asked for, every part is knocked at once.

    close() puts every placement back; it runs when the work is done, on cancel(), on an error, and when the
    object is dropped. Use ``with KnockTest(asm) as test:`` or knock_test() to be sure of it."""

    def __init__(self, asm, seed=0, parts=None, tol_mm=TOL_MM, tol_deg=TOL_DEG, knock_mm=KNOCK_MM,
                 knock_deg=KNOCK_DEG, settle=True, all_at_once=True):
        self.asm = asm
        self.seed = seed
        self.tol_mm = float(tol_mm)
        self.tol_deg = float(tol_deg)
        self.knock_mm = tuple(knock_mm)
        self.knock_deg = tuple(knock_deg)
        self.settle = settle  # solve once more after putting the placements back, before each knock
        self.closed = False
        self.cancelled = False
        self.restored = None
        self.snap = {}  # set before anything can move, so close() always has it
        self.labels = _labels(asm)
        self.grounded = [o.Name for o in grounded(asm)]
        movable = [n for n in self.labels if n not in self.grounded]
        self.movable = len(movable)
        if parts is None:
            self.names = movable
        else:
            by_label = {label: name for name, label in self.labels.items()}
            self.names = []
            for given in parts:
                name = given if given in self.labels else by_label.get(given)
                if name is None:
                    raise ValueError(f"{given!r} is not a part of {asm.Label}")
                if name not in self.grounded and name not in self.names:
                    self.names.append(name)
        self.snap = snapshot(asm)
        try:
            self.allow = free_motion(asm)
        except Exception:  # noqa: BLE001 -- no freedoms known: a hinged part then shows as free, never as held
            self.allow = {}
        self._queue = [("in_place", None)] + [("part", n) for n in self.names]
        if all_at_once and parts is None and len(self.names) > 1:
            self._queue.append(("all", None))
        self._tested = set()
        self._why = "not reached"
        self.current = ""
        self.returned = []
        self.free = []
        self.failed = []
        self.in_place = None
        self.all_at_once = None
        self.seconds = 0.0
        if not self.grounded:
            # The solver can return 0 with nothing grounded (seen 10 Nov 2026), and the whole assembly then drifts
            # with each knock. Without a fixed part there is nothing to come back to: test nothing, say why.
            self._queue = []
            self._why = "no part is grounded"

    # -- progress

    @property
    def total(self):
        return len(self.names)

    @property
    def done(self):
        return len(self._tested)

    @property
    def finished(self):
        return self.closed

    def progress(self) -> dict:
        return {"done": self.done, "total": self.total, "current": self.current, "returned": len(self.returned),
                "free": len(self.free), "failed": len(self.failed), "seconds": round(self.seconds, 3)}

    # -- work

    def step(self, budget=0.05) -> bool:
        """Work for about ``budget`` seconds (at least one part). True when all is done and put back."""
        if self.closed:
            return True
        until = time.time() + budget
        try:
            while self._queue:
                kind, name = self._queue.pop(0)
                started = time.time()
                try:
                    getattr(self, "_do_" + kind)(name)
                except Exception as exc:  # noqa: BLE001 -- one part that cannot be tested: say so, test the rest
                    self._error(kind, name, f"{type(exc).__name__}: {exc}")
                self.seconds += time.time() - started
                if time.time() >= until:
                    break
        except BaseException:
            self.close()
            raise
        if not self._queue:
            self.close()
        return self.closed

    def run(self):
        while not self.step(1e9):
            pass
        return self

    def cancel(self):
        """Stop: the parts not yet knocked are reported as unchecked, the placements are put back."""
        if not self.closed:
            self.cancelled = True
            self._why = "cancelled"
            self._queue = []
            self.close()

    def close(self):
        """Put every placement back. Safe to call more than once."""
        if self.closed:
            return
        self.closed = True
        try:
            restore(self.asm, self.snap)
            self.restored = not moved(self.asm, self.snap, 1e-9, 1e-9)
        except Exception:  # noqa: BLE001 -- the document is gone
            self.restored = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def _solve(self):
        return am._solve(self.asm)

    def _moved(self):
        return moved(self.asm, self.snap, self.tol_mm, self.tol_deg, self.allow)

    def _knock(self, names, salt=""):
        App = _app()
        doc = self.asm.Document
        for name in names:
            rnd = random.Random(f"{self.seed}/{salt}{name}")
            direction = App.Vector(rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))
            axis = App.Vector(rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))
            if direction.Length < 1e-6:
                direction = App.Vector(1, 0, 0)
            if axis.Length < 1e-6:
                axis = App.Vector(0, 0, 1)
            direction.normalize()
            axis.normalize()
            was = self.snap[name]
            doc.getObject(name).Placement = App.Placement(
                was.Base + direction * rnd.uniform(*self.knock_mm),
                App.Rotation(axis, rnd.uniform(*self.knock_deg)).multiply(was.Rotation))

    def _do_in_place(self, _name):
        self.current = "solve in place"
        code = self._solve()
        off = self._moved()
        self.in_place = {"code": code, "text": SOLVE_TEXT.get(code, f"code {code}"), "moved": off[:20]}
        restore(self.asm, self.snap)
        if code != 0:
            self._why = f"the assembly does not solve where it stands: {self.in_place['text']}"
            self._queue = []

    def _do_part(self, name):
        self.current = self.labels.get(name, name)
        restore(self.asm, self.snap)
        if self.settle:
            self._solve()
        self._knock([name])
        code = self._solve()
        off = self._moved()
        self._tested.add(name)
        rec = {"part": name, "label": self.labels.get(name, name)}
        if code != 0:
            self.failed.append(dict(rec, code=code, detail=SOLVE_TEXT.get(code, f"code {code}"), moved=off[:6]))
        elif off:
            own = next((m for m in off if m["part"] == name), {})
            self.free.append(dict(rec, mm=own.get("mm", 0.0), deg=own.get("deg", 0.0), moved=off[:6]))
        else:
            self.returned.append(name)

    def _do_all(self, _name):
        self.current = "all at once"
        restore(self.asm, self.snap)
        if self.settle:
            self._solve()
        self._knock(self.names, "all/")
        code = self._solve()
        off = self._moved()
        self.all_at_once = {"ok": code == 0 and not off, "code": code, "moved": off[:10]}

    def _error(self, kind, name, text):
        if kind == "part":
            self._tested.add(name)
            self.failed.append({"part": name, "label": self.labels.get(name, name), "code": None, "detail": text,
                                "moved": []})
        elif kind == "in_place":
            self.in_place = {"code": None, "text": text, "moved": []}
            self._why = f"the assembly could not be solved where it stands: {text}"
            self._queue = []
        else:
            self.all_at_once = {"ok": False, "code": None, "moved": [], "error": text}

    # -- the answer

    def result(self) -> dict:
        """What is known so far (all of it once finished).

        ok: the assembly solved where it stood without moving a part, every part asked for was knocked and came
        back, and the placements are back. checked / returned are counts. free: knocked, solved, and something
        did not come back (the mates do not hold it). failed: the solver gave an error with that part knocked.
        unchecked: asked for and not knocked, with the reason. all_at_once is reported beside ok, not in it."""
        unchecked = [{"part": n, "label": self.labels.get(n, n), "reason": self._why}
                     for n in self.names if n not in self._tested]
        rest = self.in_place is not None and self.in_place["code"] == 0 and not self.in_place["moved"]
        ok = bool(rest and not self.free and not self.failed and not unchecked and self.restored is not False)
        return {"ok": ok, "assembly": self.asm.Name, "parts": self.movable, "checked": self.done,
                "returned": len(self.returned), "failed": list(self.failed), "free": list(self.free),
                "unchecked": unchecked, "seconds": round(self.seconds, 3), "seed": self.seed,
                "grounded": list(self.grounded), "in_place": self.in_place, "all_at_once": self.all_at_once,
                "cancelled": self.cancelled, "restored": self.restored,
                "tolerance": {"mm": self.tol_mm, "deg": self.tol_deg}}


def knock_test(asm, seed=0, parts=None, tol_mm=TOL_MM, tol_deg=TOL_DEG, step=None, **options) -> dict:
    """Knock every part that is not grounded (or the ``parts`` named, by Name or Label) and see it come back;
    KnockTest.result() says what the answer holds. Every placement is put back whatever happens.

    ``step`` is called with KnockTest.progress() after the solve in place, after each part and after the
    knock of all at once; returning False cancels (the parts left are reported as unchecked). options: knock_mm=(2, 5), knock_deg=(1, 3), settle=True, all_at_once=True."""
    test = KnockTest(asm, seed, parts, tol_mm, tol_deg, **options)
    try:
        while True:
            done = test.step(0.0 if step is not None else 1e9)
            if step is not None and step(test.progress()) is False and not done:
                test.cancel()
            if done or test.closed:
                break
    finally:
        test.close()
    return test.result()


# ----- joints that point at nothing ------------------------------------------------------------------------------------


def stale_joints(asm, fix=False) -> dict:
    """The joints with a reference that no longer resolves, and the grounds that hold nothing:
    {"joints", "stale": [{"joint", "label", "missing"}], "removed", "moved"}.

    Such a joint raises on every recompute (8 Nov: about 5 s each time on the whole printer). fix=True removes
    them in one undo step. Nothing is solved, so no part should move; "moved" lists any that did, and the
    caller should not save if it is not empty."""
    import JointObject

    found = []
    listed = joints(asm)
    for joint in listed:
        for index in (1, 2):
            ref = getattr(joint, f"Reference{index}", None)
            comp, sub = am._ref_parts(ref)
            if comp is None:
                continue
            try:
                missing = f"{comp.Name}.{sub}" if "?" in sub else JointObject.refMissing(ref)
            except Exception as exc:
                missing = f"{comp.Name}.{sub} ({exc})"
            if missing:
                found.append((joint, f"Reference{index}: {missing}"))
                break
    for g in _ground_joints(asm):
        if g.ObjectToGround is None:
            found.append((g, "ObjectToGround"))
    out = {"assembly": asm.Name, "joints": len(listed),
           "stale": [{"joint": j.Name, "label": j.Label, "missing": why} for j, why in found],
           "removed": [], "moved": []}
    if fix and found:
        doc = asm.Document
        snap = snapshot(asm)
        with am._transaction(doc, "Remove stale joints"):
            for joint, _why in found:
                out["removed"].append(joint.Name)
                doc.removeObject(joint.Name)
        out["moved"] = moved(asm, snap, 1e-9, 1e-9)
    return out


__all__ = ["joints", "refs", "joint_info", "joint_group", "parts", "members", "grounded", "ground", "solve",
           "snapshot", "restore", "moved", "free_motion", "rule_check", "knock_test", "KnockTest", "stale_joints",
           "SOLVE_TEXT", "TOL_MM", "TOL_DEG"]
