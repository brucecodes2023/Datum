# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fusion-style kinematic motion for assemblies (BW-ROB-05, 27 Oct 2026).

Three commands on the Design workspace's Motion group, all driving FreeCAD 1.1's kinematic
Simulation (the Ondsel solver, ``assembly.generateSimulation``):

* **Drive Joint** (``BruceWorx_DriveJoint``): pick a joint, move a slider, the model follows. The
  joint is driven through a temporary Simulation whose Motions hold every other movable joint
  where it is and set the driven one to the slider's value; the objects live inside one
  transaction and are removed again, so OK keeps the pose as one undo step and Cancel leaves
  nothing behind. Animate sweeps the range back and forth.
* **Motion Study** (``BruceWorx_MotionStudy``): a Simulation object (``CommandCreateSimulation``)
  with one Motion per joint channel. Keyframes (time in s, value in deg or mm) sit on the Motion
  as ``BWKeyframes`` and are compiled into the solver's ``piecewise(time, functions(...),
  transitions(...))`` formula: a piecewise-linear interpolation, in radians or mm, constant
  before the first and after the last keyframe. Playback: play/pause/stop, once, loop and
  ping-pong, 0.25x-4x, a scrub slider; Save frames writes PNGs and an MP4 through ffmpeg.
* **Motion Link** (``BruceWorx_MotionLink``): two joints and a ratio become the stock joint that
  couples them: revolute+revolute a Gears joint (Belt when reversed), revolute+slider a
  RackPinion (pitch radius from mm per turn, negative when reversed).

Everything above the panels imports no Qt, so ``TestBWMotion`` runs it headless.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess

MOVABLE = ("Revolute", "Slider", "Cylindrical", "Screw")
LINK_TYPES = ("Gears", "Belt", "RackPinion", "Screw")
JOINT_COMMANDS = [
    "Assembly_CreateJointFixed", "Assembly_CreateJointRevolute", "Assembly_CreateJointCylindrical",
    "Assembly_CreateJointSlider", "Assembly_CreateJointBall", "Assembly_CreateJointDistance",
    "Assembly_CreateJointParallel", "Assembly_CreateJointPerpendicular", "Assembly_CreateJointAngle",
    "Assembly_CreateJointRackPinion", "Assembly_CreateJointScrew", "Assembly_CreateJointGears",
    "Assembly_CreateJointBelt",
]
LOOP_MODES = ("Once", "Loop", "Ping-pong")
SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
DEFAULT_RANGE = {"Angular": (-180.0, 180.0), "Linear": (-100.0, 100.0)}
UNITS = {"Angular": "deg", "Linear": "mm"}
SCALE = {"Angular": math.pi / 180.0, "Linear": 1.0}  # UI units to the solver's
STUDY_FPS = 25
HERE = os.path.dirname(os.path.abspath(__file__))

_state = {"installed": False, "driving": None, "study": None}


def _app():
    import FreeCAD

    return FreeCAD


def _log(text):
    _app().Console.PrintLog(f"[BW motion] {text}\n")


# ---------------------------------------------------------------------------
# joints and their channels
# ---------------------------------------------------------------------------


def joint_group(assembly):
    for obj in assembly.OutList:
        if obj.TypeId == "Assembly::JointGroup":
            return obj
    return None


def all_joints(assembly):
    group = joint_group(assembly)
    return [o for o in (group.Group if group else []) if hasattr(o, "JointType")]


def live(joint):
    return not getattr(joint, "Suppressed", False)


def joints_of(assembly):
    """The joints a motion can drive: revolute, slider, cylindrical and screw, not suppressed."""
    return [j for j in all_joints(assembly) if j.JointType in MOVABLE and live(j)]


def kinds_of(joint):
    """The channels of a joint: angle, length or both (cylindrical)."""
    if joint.JointType == "Cylindrical":
        return ("Angular", "Linear")
    if joint.JointType == "Slider":
        return ("Linear",)
    return ("Angular",)


def channels(assembly):
    return [(j, k) for j in joints_of(assembly) for k in kinds_of(j)]


def channel_label(joint, kind):
    if len(kinds_of(joint)) == 1:
        return joint.Label
    return f"{joint.Label} ({'angle' if kind == 'Angular' else 'length'})"


def zero_of(joint, kind):
    """The datum zero of the reading (LimitAngleZero / LimitLengthZero, from 36-joints)."""
    name = "LimitAngleZero" if kind == "Angular" else "LimitLengthZero"
    prop = getattr(joint, name, None)
    return float(getattr(prop, "Value", prop or 0.0))


def reading(joint, kind):
    """The joint's value now, in deg or mm, measured from its limit datums."""
    import JointObject

    angle, length = JointObject.limitReading(joint)
    return angle if kind == "Angular" else length


def joint_range(joint, kind):
    """(low, high) of the channel: its limits when both are on, else -180..180 or -100..100."""
    lo, hi = DEFAULT_RANGE[kind]
    if kind == "Angular":
        on_lo, on_hi = getattr(joint, "EnableAngleMin", False), getattr(joint, "EnableAngleMax", False)
        v_lo, v_hi = getattr(joint, "AngleMin", None), getattr(joint, "AngleMax", None)
    else:
        on_lo, on_hi = getattr(joint, "EnableLengthMin", False), getattr(joint, "EnableLengthMax", False)
        v_lo, v_hi = getattr(joint, "LengthMin", None), getattr(joint, "LengthMax", None)
    if on_lo and v_lo is not None:
        lo = float(v_lo.Value if hasattr(v_lo, "Value") else v_lo)
    if on_hi and v_hi is not None:
        hi = float(v_hi.Value if hasattr(v_hi, "Value") else v_hi)
    if hi <= lo:
        lo, hi = DEFAULT_RANGE[kind]
    return lo, hi


def grounded_parts(assembly):
    return {j.ObjectToGround for j in all_joints(assembly) if getattr(j, "ObjectToGround", None) is not None}


def part_of(ref):
    import UtilsAssembly

    return UtilsAssembly.getMovingPart(ref) if ref else None


def moving_part(joint, assembly=None):
    """The part a joint moves: its second reference's, or the first's when that one is grounded."""
    grounded = grounded_parts(assembly) if assembly is not None else set()
    second, first = part_of(joint.Reference2), part_of(joint.Reference1)
    if second is not None and second not in grounded:
        return second
    return first if first is not None else second


def link_joints(assembly):
    return [j for j in all_joints(assembly) if j.JointType in LINK_TYPES and live(j)]


def linked_parts(assembly):
    parts = set()
    for link in link_joints(assembly):
        for ref in (link.Reference1, link.Reference2):
            part = part_of(ref)
            if part is not None:
                parts.add(part)
    return parts


def is_linked(joint, parts):
    """True when a Gears, Belt, RackPinion or Screw joint couples one of this joint's parts: the
    joint then follows the link and is not held by a study."""
    return any(part_of(ref) in parts for ref in (joint.Reference1, joint.Reference2) if ref)


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------


def fmt(value):
    text = f"{float(value):.10g}"
    return "0" if text in ("-0", "0", "-0.0") else text


def clean_keyframes(keyframes):
    """Sorted by time, one per time (the last given wins), as (time, value) floats."""
    by_time = {}
    for t, v in keyframes:
        by_time[round(float(t), 9)] = float(v)
    return sorted(by_time.items())


def interpolate(keyframes, t):
    """The piecewise-linear value at t, in the keyframes' units; constant outside them."""
    frames = clean_keyframes(keyframes)
    if not frames:
        return None
    if t <= frames[0][0]:
        return frames[0][1]
    for (t0, v0), (t1, v1) in zip(frames, frames[1:]):
        if t < t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return frames[-1][1]


def _segment(t0, v0, t1, v1):
    slope = (v1 - v0) / (t1 - t0)
    if abs(t0) < 1e-12:
        base = "time"
    elif t0 > 0:
        base = f"(time - {fmt(t0)})"
    else:
        base = f"(time + {fmt(-t0)})"
    return f"{fmt(v0)} {'+' if slope >= 0 else '-'} {fmt(abs(slope))}*{base}"


def formula(keyframes, scale=1.0, offset=0.0):
    """The solver's piecewise-linear formula of the keyframes; each value is (v + offset) * scale
    (degrees to radians, the datum zero back on). "" without keyframes."""
    frames = [(t, (v + offset) * scale) for t, v in clean_keyframes(keyframes)]
    if not frames:
        return ""
    if len(frames) == 1:
        return fmt(frames[0][1])
    functions = [fmt(frames[0][1])]
    for (t0, v0), (t1, v1) in zip(frames, frames[1:]):
        functions.append(_segment(t0, v0, t1, v1))
    functions.append(fmt(frames[-1][1]))
    transitions = [fmt(t) for t, _v in frames]
    return f"piecewise(time, functions({', '.join(functions)}), transitions({', '.join(transitions)}))"


def channel_formula(joint, kind, keyframes):
    return formula(keyframes, SCALE[kind], zero_of(joint, kind))


def constant_formula(joint, kind, value):
    return fmt((float(value) + zero_of(joint, kind)) * SCALE[kind])


# ---------------------------------------------------------------------------
# Simulation and Motion objects (CommandCreateSimulation's classes in the GUI, plain
# properties headless: the solver reads the properties, not the proxies)
# ---------------------------------------------------------------------------


def _ccs():
    try:
        import CommandCreateSimulation

        return CommandCreateSimulation
    except Exception:  # headless: the module defines a QObject subclass at import
        return None


def _add(obj, kind, name, group, doc=""):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, group, doc)


def simulation_group(assembly):
    import UtilsAssembly

    return UtilsAssembly.getSimulationGroup(assembly)


def make_simulation(assembly, name="MotionStudy", label=None):
    sim = simulation_group(assembly).newObject("App::FeaturePython", name)
    ccs = _ccs()
    if ccs is not None:
        ccs.Simulation(sim)
        if _app().GuiUp:
            ccs.ViewProviderSimulation(sim.ViewObject)
    else:
        for prop, kind in (("aTimeStart", "App::PropertyTime"), ("bTimeEnd", "App::PropertyTime"),
                           ("cTimeStepOutput", "App::PropertyTime"), ("fGlobalErrorTolerance", "App::PropertyFloat"),
                           ("jFramesPerSecond", "App::PropertyInteger")):
            _add(sim, kind, prop, "Simulation")
        sim.addExtension("App::GroupExtensionPython")
        sim.fGlobalErrorTolerance = 1.0e-6
    sim.aTimeStart = 0.0
    sim.bTimeEnd = 2.0
    sim.jFramesPerSecond = STUDY_FPS
    sim.cTimeStepOutput = 1.0 / STUDY_FPS
    if label:
        sim.Label = label
    return sim


def make_study(assembly, label="Motion study"):
    """A Simulation with the BW study properties and one Motion per channel."""
    sim = make_simulation(assembly, "MotionStudy", label)
    _add(sim, "App::PropertyEnumeration", "BWLoop", "Motion study", "Playback: once, loop or ping-pong")
    sim.BWLoop = list(LOOP_MODES)
    sim.BWLoop = "Loop"
    _add(sim, "App::PropertyFloat", "BWSpeed", "Motion study", "Playback speed, 1 = real time")
    sim.BWSpeed = 1.0
    sync_channels(assembly, sim)
    return sim


def make_motion(assembly, sim, joint, kind, formula_text=""):
    motion = assembly.newObject("App::FeaturePython", "Motion")
    ccs = _ccs()
    if ccs is not None:
        ccs.Motion(motion, kind, joint, formula_text)
        if _app().GuiUp:
            ccs.ViewProviderMotion(motion.ViewObject)
    else:
        _add(motion, "App::PropertyXLinkSub", "Joint", "Motion")
        _add(motion, "App::PropertyString", "Formula", "Motion")
        _add(motion, "App::PropertyEnumeration", "MotionType", "Motion")
        motion.MotionType = ["Angular", "Linear"]
        motion.MotionType = kind
        motion.Joint = joint
        motion.Formula = formula_text
    _add(motion, "App::PropertyFloatList", "BWKeyframes", "Motion study", "Keyframes, time and value in turn")
    _add(motion, "App::PropertyBool", "BWHold", "Motion study",
         "Without keyframes, hold the joint where it is (off: it follows the others)")
    motion.BWHold = True
    motion.Label = f"{channel_label(joint, kind)} motion"
    sim.Group = list(sim.Group) + [motion]
    return motion


def is_study(obj):
    return hasattr(obj, "BWLoop") and hasattr(obj, "aTimeStart")


def studies(assembly):
    return [o for o in simulation_group(assembly).Group if is_study(o)]


def motions_of(sim):
    return [m for m in sim.Group if hasattr(m, "Joint") and hasattr(m, "Formula")]


def motion_joint(motion):
    joint = motion.Joint
    return joint[0] if isinstance(joint, (tuple, list)) else joint


def motion_for(sim, joint, kind):
    for m in motions_of(sim):
        if motion_joint(m) == joint and m.MotionType == kind:
            return m
    return None


def sync_channels(assembly, sim):
    """One Motion per channel of the assembly; channels whose joint is gone lose theirs."""
    parts = linked_parts(assembly)
    for joint, kind in channels(assembly):
        motion = motion_for(sim, joint, kind)
        if motion is None:
            motion = make_motion(assembly, sim, joint, kind)
            motion.BWHold = not is_linked(joint, parts)
    wanted = set(channels(assembly))
    for m in motions_of(sim):
        joint = motion_joint(m)
        if joint is None or (joint, m.MotionType) not in wanted:
            group = list(sim.Group)
            group.remove(m)
            sim.Group = group
            sim.Document.removeObject(m.Name)


def keyframes_of(motion):
    flat = list(getattr(motion, "BWKeyframes", []) or [])
    return clean_keyframes(zip(flat[0::2], flat[1::2]))


def set_keyframes(motion, keyframes):
    frames = clean_keyframes(keyframes)
    motion.BWKeyframes = [x for t, v in frames for x in (t, v)]
    joint = motion_joint(motion)
    motion.Formula = channel_formula(joint, motion.MotionType, frames) if joint is not None else ""


def study_rows(sim):
    """(motion, joint, kind) per channel of a study, in the Group's order."""
    rows = []
    for m in motions_of(sim):
        joint = motion_joint(m)
        if joint is not None:
            rows.append((m, joint, m.MotionType))
    return rows


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


def frame_count(sim):
    """Frames the solver produces after the start pose: t = start, start + step, ..., end."""
    step = float(sim.cTimeStepOutput.Value) or 1.0
    span = float(sim.bTimeEnd.Value) - float(sim.aTimeStart.Value)
    return max(1, int(round(span / step)) + 1)


def time_of_frame(sim, index):
    return float(sim.aTimeStart.Value) + index * float(sim.cTimeStepOutput.Value)


def frame_of_time(sim, t):
    step = float(sim.cTimeStepOutput.Value) or 1.0
    return max(0, min(frame_count(sim) - 1, int(round((t - float(sim.aTimeStart.Value)) / step))))


def compile_study(sim):
    """Every Motion's formula from its keyframes, or a hold at the joint's reading, or free."""
    for motion, joint, kind in study_rows(sim):
        frames = keyframes_of(motion)
        if frames:
            motion.Formula = channel_formula(joint, kind, frames)
        elif motion.BWHold:
            motion.Formula = constant_formula(joint, kind, reading(joint, kind))
        else:
            motion.Formula = ""


def run_study(assembly, sim, start=None):
    """Compile and solve from the start pose (placements saved with
    UtilsAssembly.saveAssemblyPartsPlacements; the pose now when None). Returns (rc, frames): rc 0
    is success, the frames are 1..frames of the solver's output (its frame 0 is the start pose).
    A run that begins far from its first keyframe may not converge: give it the pose the study
    was made in."""
    if start is not None:
        import UtilsAssembly

        UtilsAssembly.restoreAssemblyPartsPlacements(assembly, start)
    compile_study(sim)
    rc = assembly.generateSimulation(sim)
    frames = assembly.numberOfFrames() - 1 if rc == 0 else 0
    return rc, max(0, frames)


def show_frame(assembly, index):
    """Put the assembly at solver output frame `index` (0-based, after the start pose)."""
    return assembly.updateForFrame(index + 1)


def show_time(assembly, sim, t):
    return show_frame(assembly, frame_of_time(sim, t))


def solver_message(rc):
    return {0: "", -1: "the solver could not run (over-constrained or conflicting motions)",
            -6: "no grounded part"}.get(rc, f"solver error {rc}")


class Driver:
    """Drives one channel at a time through a temporary Simulation inside one transaction.

    Every other channel is held at its reading (unless a link couples it); the driven one is set
    to the value. ``finish(keep)`` removes the objects and commits or aborts the transaction."""

    def __init__(self, assembly, label="Drive joint"):
        import UtilsAssembly

        self.assembly = assembly
        self.doc = assembly.Document
        self.initial = UtilsAssembly.saveAssemblyPartsPlacements(assembly)
        self.doc.openTransaction(label)
        had_group = any(o.TypeId == "Assembly::SimulationGroup" for o in assembly.OutList)
        self.sim = make_simulation(assembly, "DriveJoint", "Drive joint (temporary)")
        self.group = None if had_group else simulation_group(assembly)
        self.sim.bTimeEnd = 0.01
        self.sim.cTimeStepOutput = 0.01
        self.parts = linked_parts(assembly)
        self.motions = {}
        for joint, kind in channels(assembly):
            self.motions[(joint, kind)] = make_motion(assembly, self.sim, joint, kind)
        self.open = True
        self.last_rc = 0

    def drive(self, joint, kind, value):
        """Put the channel at value; the others stay. Returns the solver's rc."""
        for (j, k), motion in self.motions.items():
            if (j, k) == (joint, kind):
                motion.Formula = constant_formula(j, k, value)
            elif is_linked(j, self.parts):
                motion.Formula = ""
            else:
                motion.Formula = constant_formula(j, k, reading(j, k))
        rc = self.assembly.generateSimulation(self.sim)
        self.last_rc = rc
        if rc == 0 and self.assembly.numberOfFrames() > 1:
            self.assembly.updateForFrame(1)
        return rc

    def restore(self):
        import UtilsAssembly

        UtilsAssembly.restoreAssemblyPartsPlacements(self.assembly, self.initial)

    def finish(self, keep):
        if not self.open:
            return
        self.open = False
        for motion in self.motions.values():
            self.doc.removeObject(motion.Name)
        self.doc.removeObject(self.sim.Name)
        if self.group is not None and not self.group.Group:
            self.doc.removeObject(self.group.Name)  # the Simulations group was ours
        if keep:
            self.doc.commitTransaction()
        else:
            self.doc.abortTransaction()
        self.doc.recompute()


# ---------------------------------------------------------------------------
# motion links
# ---------------------------------------------------------------------------


def link_kind(j1, j2):
    """The stock joint that couples the two: Gears for two revolutes, RackPinion for a revolute
    and a slider, None otherwise (two sliders have no solver joint)."""
    a, b = j1.JointType, j2.JointType
    if a == "Revolute" and b == "Revolute":
        return "Gears"
    if {a, b} == {"Revolute", "Slider"}:
        return "RackPinion"
    return None


def link_label(j1, j2, ratio, mm_per_turn=None):
    if mm_per_turn is not None:
        return f"Link: {j1.Label} -> {j2.Label} ({mm_per_turn:g} mm/turn)"
    return f"Link: {j1.Label} -> {j2.Label} (1:{ratio:g})"


def link_radii(ratio, base=10.0):
    """Gear radii for J2 = ratio x J1: the solver's ratio is radius1 / radius2."""
    return base * float(ratio), base


def pitch_radius(mm_per_turn):
    return float(mm_per_turn) / (2.0 * math.pi)


def own_reference(joint, assembly):
    """The reference of the joint on the part it moves."""
    part = moving_part(joint, assembly)
    for ref in (joint.Reference2, joint.Reference1):
        if ref and part_of(ref) == part:
            return [ref[0], list(ref[1])]
    return None


def make_link(assembly, j1, j2, ratio=1.0, reverse=False, mm_per_turn=None):
    """The joint that couples j2 to j1 at the ratio. Revolute+revolute: Gears, or Belt (same
    direction) when reversed; revolute+slider: RackPinion, its pitch radius negative when
    reversed. Raises ValueError for pairs the solver has no joint for."""
    import JointObject

    kind = link_kind(j1, j2)
    if kind is None:
        raise ValueError(f"{j1.JointType} and {j2.JointType} joints cannot be linked: the solver "
                         "couples two revolutes (gears, belt) or a revolute and a slider (rack and pinion)")
    group = joint_group(assembly)
    if kind == "Gears":
        jtype = "Belt" if reverse else "Gears"
        r1, r2 = link_radii(ratio)
        refs = [own_reference(j1, assembly), own_reference(j2, assembly)]
        label = link_label(j1, j2, ratio)
    else:
        jtype = "RackPinion"
        if mm_per_turn is None:
            mm_per_turn = 2.0 * math.pi * 10.0 * float(ratio)
        slider, revolute = (j1, j2) if j1.JointType == "Slider" else (j2, j1)
        refs = [own_reference(slider, assembly), own_reference(revolute, assembly)]
        r1 = pitch_radius(mm_per_turn) * (-1.0 if reverse else 1.0)
        r2 = None
        label = link_label(j1, j2, ratio, mm_per_turn)
    if None in refs:
        raise ValueError("a joint has no reference on the part it moves")
    link = group.newObject("App::FeaturePython", "MotionLink")
    JointObject.Joint(link, JointObject.JointTypes.index(jtype))
    if _app().GuiUp:
        JointObject.ViewProviderJoint(link.ViewObject)
    link.Distance = r1
    if r2 is not None:
        link.Distance2 = r2
    link.Proxy.setJointConnectors(link, refs)
    link.Label = label
    assembly.Document.recompute()
    return link


# ---------------------------------------------------------------------------
# frame export
# ---------------------------------------------------------------------------


def save_frames(assembly, sim, frames, folder, view=None, size=(1280, 720), fps=None, on_frame=None):
    """PNGs frame_0001.png ... in folder (the start pose first), and motion.mp4 when ffmpeg is on
    the PATH. Returns (png paths, mp4 path or None)."""
    import FreeCADGui as Gui

    view = view or Gui.ActiveDocument.ActiveView
    os.makedirs(folder, exist_ok=True)
    paths = []
    for index in range(frames):
        show_frame(assembly, index)
        if on_frame:
            on_frame(index)
        path = os.path.join(folder, f"frame_{index + 1:04d}.png")
        view.saveImage(path, size[0], size[1], "Current")
        paths.append(path)
    mp4 = None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and paths:
        fps = fps or int(getattr(sim, "jFramesPerSecond", STUDY_FPS) or STUDY_FPS)
        mp4 = os.path.join(folder, "motion.mp4")
        try:
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps),
                            "-i", os.path.join(folder, "frame_%04d.png"),
                            "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", mp4],
                           check=True, timeout=300)
        except Exception as exc:  # noqa: BLE001
            _app().Console.PrintWarning(f"[BW motion] ffmpeg failed: {exc}\n")
            mp4 = None
    return paths, mp4


# ---------------------------------------------------------------------------
# GUI: the assembly in hand, the Joint menu, the three panels, the commands
# ---------------------------------------------------------------------------


def _assembly():
    """The assembly the commands act on: the active one, else a selected one, else the active
    document's first."""
    try:
        import UtilsAssembly

        active = UtilsAssembly.activeAssembly()
        if active is not None:
            return active
    except Exception:  # noqa: BLE001
        pass
    doc = _app().ActiveDocument
    if doc is None:
        return None
    try:
        import FreeCADGui as Gui

        for obj in Gui.Selection.getSelection():
            if obj.TypeId == "Assembly::AssemblyObject":
                return obj
            for parent in obj.InList:
                if parent.TypeId == "Assembly::AssemblyObject":
                    return parent
    except Exception:  # noqa: BLE001
        pass
    for obj in doc.Objects:
        if obj.TypeId == "Assembly::AssemblyObject":
            return obj
    return None


def joint_menu(parent=None):
    """A QMenu with the 13 joint commands, each with its own icon and text."""
    import FreeCADGui as Gui
    from PySide import QtGui, QtWidgets

    menu = QtWidgets.QMenu(parent)
    menu.setObjectName("bwJointMenu")
    for name in JOINT_COMMANDS:
        command = Gui.Command.get(name)
        if command is None:
            continue
        info = command.getInfo()
        icon = Gui.getIcon(info.get("pixmap", "")) if info.get("pixmap") else QtGui.QIcon()
        action = menu.addAction(icon, info.get("menuText", name).replace("&", ""))
        action.setToolTip(info.get("toolTip", ""))
        action.setObjectName(name)
        action.triggered.connect(lambda _c=False, n=name: Gui.runCommand(n))
        try:
            action.setEnabled(command.isActive())
        except Exception:  # noqa: BLE001
            pass
    menu.setToolTipsVisible(True)
    return menu


def _button_of(command_name):
    """The visible tool button of a command on a toolbar, or None."""
    import FreeCADGui as Gui
    from PySide import QtWidgets

    command = Gui.Command.get(command_name)
    if command is None:
        return None
    for action in command.getAction() or []:
        for widget in action.associatedObjects():
            if isinstance(widget, QtWidgets.QToolButton) and widget.isVisible():
                return widget
    return None


def popup_joint_menu():
    from PySide import QtGui

    menu = joint_menu()
    button = _button_of("BruceWorx_Joint")
    pos = button.mapToGlobal(button.rect().bottomLeft()) if button else QtGui.QCursor.pos()
    _state["joint_menu"] = menu
    menu.exec_(pos)
    _state["joint_menu"] = None


def _slot(method):
    from bruceworx_slots import slot

    return slot(method)


def _heading(text):
    from PySide import QtWidgets

    label = QtWidgets.QLabel(f"<b>{text}</b>")
    label.setContentsMargins(0, 8, 0, 0)
    return label


def _hint(text):
    from PySide import QtWidgets

    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(mid);")
    return label


class DriveJointPanel:
    """Drive Joint: a joint, a slider and a spin box over its range, Animate. The pose follows
    live through a Driver; OK keeps it (one undo step), Cancel puts everything back."""

    STEPS = 2000

    def __init__(self, assembly, joint=None, kind=None):
        from PySide import QtCore, QtWidgets

        self.assembly = assembly
        self.doc = assembly.Document
        self.channels = channels(assembly)
        self.driver = Driver(assembly)
        self.direction = 1
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Drive Joint")
        self.form.setObjectName("bwDriveJointPanel")
        lay = QtWidgets.QVBoxLayout(self.form)
        grid = QtWidgets.QFormLayout()
        lay.addLayout(grid)
        self.combo = QtWidgets.QComboBox()
        self.combo.setObjectName("bwDriveJoint")
        for j, k in self.channels:
            self.combo.addItem(channel_label(j, k))
        grid.addRow("Joint", self.combo)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("bwDriveSlider")
        self.slider.setRange(0, self.STEPS)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setObjectName("bwDriveValue")
        self.spin.setDecimals(2)
        self.spin.setKeyboardTracking(False)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        grid.addRow("Value", row)
        self.range_label = QtWidgets.QLabel()
        grid.addRow("Range", self.range_label)
        row = QtWidgets.QHBoxLayout()
        self.animate = QtWidgets.QPushButton("Animate")
        self.animate.setObjectName("bwDriveAnimate")
        self.animate.setCheckable(True)
        self.speed = QtWidgets.QDoubleSpinBox()
        self.speed.setObjectName("bwDriveSpeed")
        self.speed.setRange(0.1, 10000.0)
        self.speed.setDecimals(1)
        self.back = QtWidgets.QPushButton("Back to start")
        row.addWidget(self.animate)
        row.addWidget(QtWidgets.QLabel("Speed"))
        row.addWidget(self.speed)
        row.addWidget(self.back)
        grid.addRow("", row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addWidget(_hint("Other joints stay where they are; a joint coupled by a gear, belt or rack "
                            "link follows. The range is the joint's limits when both are set. OK keeps "
                            "the pose as one undo step; Cancel puts the parts back."))
        lay.addStretch(1)
        self.timer = QtCore.QTimer()
        self.timer.setInterval(40)
        self.timer.timeout.connect(_slot(self.tick))
        self.combo.currentIndexChanged.connect(_slot(self.select_channel))
        self.slider.valueChanged.connect(_slot(self.slider_moved))
        self.spin.valueChanged.connect(_slot(self.spin_changed))
        self.animate.toggled.connect(_slot(self.set_animating))
        self.back.clicked.connect(_slot(self.back_to_start))
        self.start_values = {ch: reading(*ch) for ch in self.channels}
        if not self.channels:
            self.status.setText("This assembly has no revolute, slider, cylindrical or screw joint.")
            self.combo.setEnabled(False)
            self.slider.setEnabled(False)
            self.spin.setEnabled(False)
            self.animate.setEnabled(False)
        else:
            index = 0
            if joint is not None:
                for i, (j, k) in enumerate(self.channels):
                    if j == joint and (kind is None or k == kind):
                        index = i
                        break
            self.combo.setCurrentIndex(index)
            self.select_channel(index)

    # --- channel
    @property
    def channel(self):
        index = self.combo.currentIndex()
        return self.channels[index] if 0 <= index < len(self.channels) else None

    def select_channel(self, index):
        if not (0 <= index < len(self.channels)):
            return
        joint, kind = self.channels[index]
        self.lo, self.hi = joint_range(joint, kind)
        unit = UNITS[kind]
        self.range_label.setText(f"{self.lo:g} to {self.hi:g} {unit}"
                                 + ("" if (self.lo, self.hi) != DEFAULT_RANGE[kind] else " (no limits set)"))
        self.spin.blockSignals(True)
        self.spin.setRange(self.lo, self.hi)
        self.spin.setSuffix(" " + unit)
        self.spin.setSingleStep(1.0)
        self.spin.blockSignals(False)
        self.speed.blockSignals(True)
        self.speed.setSuffix(f" {unit}/s")
        self.speed.setValue(45.0 if kind == "Angular" else 20.0)
        self.speed.blockSignals(False)
        self.set_value(min(self.hi, max(self.lo, reading(joint, kind))), drive=False)

    def value(self):
        return self.spin.value()

    def set_value(self, value, drive=True):
        value = min(self.hi, max(self.lo, float(value)))
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.slider.blockSignals(True)
        span = (self.hi - self.lo) or 1.0
        self.slider.setValue(int(round((value - self.lo) / span * self.STEPS)))
        self.slider.blockSignals(False)
        if drive:
            self.drive(value)

    def drive(self, value):
        channel = self.channel
        if channel is None:
            return
        rc = self.driver.drive(channel[0], channel[1], value)
        self.status.setText("" if rc == 0 else f"Not moved: {solver_message(rc)}.")
        try:
            import FreeCADGui as Gui

            Gui.updateGui()
        except Exception:  # noqa: BLE001
            pass

    def slider_moved(self, position):
        value = self.lo + (self.hi - self.lo) * position / self.STEPS
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.drive(value)

    def spin_changed(self, value):
        self.set_value(value)

    def back_to_start(self):
        self.animate.setChecked(False)
        if self.channel is not None:
            self.set_value(self.start_values[self.channel])

    # --- animate
    def set_animating(self, on):
        if on:
            self.direction = 1 if self.value() < self.hi else -1
            self.timer.start()
        else:
            self.timer.stop()

    def tick(self):
        step = self.speed.value() * self.timer.interval() / 1000.0
        value = self.value() + self.direction * step
        if value >= self.hi:
            value, self.direction = self.hi, -1
        elif value <= self.lo:
            value, self.direction = self.lo, 1
        self.set_value(value)

    # --- task dialog
    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        return self._close(keep=True)

    def reject(self):
        return self._close(keep=False)

    def _close(self, keep):
        import FreeCADGui as Gui

        self.timer.stop()
        if not keep:
            self.driver.restore()
        self.driver.finish(keep)
        _state["driving"] = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        return True


class MotionStudyPanel:
    """Motion Study: the joints with their keyframes, playback and export. The study is a
    Simulation object kept with the file; OK keeps it, Cancel undoes the edit (and a new study)."""

    def __init__(self, assembly, sim=None):
        import UtilsAssembly
        from PySide import QtCore, QtWidgets

        self.assembly = assembly
        self.doc = assembly.Document
        self.initial = UtilsAssembly.saveAssemblyPartsPlacements(assembly)
        self.created = sim is None
        self.doc.openTransaction("Motion study" if self.created else f"Edit {sim.Label}")
        if sim is None:
            sim = make_study(assembly, f"Motion study {len(studies(assembly)) + 1}")
        else:
            sync_channels(assembly, sim)
        self.sim = sim
        self.rows = study_rows(sim)
        self.frames = 0
        self.frame = 0
        self.dirty = True
        self.direction = 1
        self.playing = False
        self.filling = False
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Motion Study")
        self.form.setObjectName("bwMotionStudyPanel")
        lay = QtWidgets.QVBoxLayout(self.form)
        top = QtWidgets.QFormLayout()
        lay.addLayout(top)
        self.name = QtWidgets.QLineEdit(sim.Label)
        self.name.setObjectName("bwStudyName")
        top.addRow("Study", self.name)
        row = QtWidgets.QHBoxLayout()
        self.duration = QtWidgets.QDoubleSpinBox()
        self.duration.setObjectName("bwStudyDuration")
        self.duration.setRange(0.1, 3600.0)
        self.duration.setDecimals(2)
        self.duration.setSuffix(" s")
        self.duration.setValue(float(sim.bTimeEnd.Value))
        self.fps = QtWidgets.QSpinBox()
        self.fps.setObjectName("bwStudyFps")
        self.fps.setRange(1, 120)
        self.fps.setSuffix(" fps")
        self.fps.setValue(int(sim.jFramesPerSecond or STUDY_FPS))
        row.addWidget(self.duration)
        row.addWidget(self.fps)
        top.addRow("Duration", row)
        # joints
        lay.addWidget(_heading("Joints"))
        self.table = QtWidgets.QTableWidget(len(self.rows), 4)
        self.table.setObjectName("bwStudyJoints")
        self.table.setHorizontalHeaderLabels(["Joint", "Hold", "Keyframes", "Now"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(max(90, 26 * (len(self.rows) + 1) + 4))
        self.fill_joints()
        lay.addWidget(self.table)
        # keyframes of the selected joint
        lay.addWidget(_heading("Keyframes of the selected joint"))
        self.keys = QtWidgets.QTableWidget(0, 2)
        self.keys.setObjectName("bwStudyKeyframes")
        self.keys.setHorizontalHeaderLabels(["Time (s)", "Value"])
        self.keys.verticalHeader().hide()
        self.keys.horizontalHeader().setStretchLastSection(True)
        self.keys.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.keys.setMaximumHeight(140)
        lay.addWidget(self.keys)
        row = QtWidgets.QHBoxLayout()
        self.add = QtWidgets.QPushButton("Add keyframe")
        self.add.setObjectName("bwStudyAdd")
        self.add.setToolTip("A keyframe at the current time with the joint's current value")
        self.remove = QtWidgets.QPushButton("Remove")
        self.remove.setObjectName("bwStudyRemove")
        self.solve_button = QtWidgets.QPushButton("Solve")
        self.solve_button.setObjectName("bwStudySolve")
        row.addWidget(self.add)
        row.addWidget(self.remove)
        row.addStretch(1)
        row.addWidget(self.solve_button)
        lay.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName("bwStudyStatus")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        # playback
        lay.addWidget(_heading("Playback"))
        self.scrub = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scrub.setObjectName("bwStudyScrub")
        self.scrub.setRange(0, 0)
        self.time_label = QtWidgets.QLabel("0.00 s")
        self.time_label.setObjectName("bwStudyTime")
        self.time_label.setMinimumWidth(70)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.scrub, 1)
        row.addWidget(self.time_label)
        lay.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.buttons = {}
        for key, text, tip in (("first", "|<", "To the start"), ("back", "<", "One frame back"),
                               ("play", "Play", "Play / pause (space)"), ("stop", "Stop", "Stop, back to the start"),
                               ("forward", ">", "One frame forward"), ("last", ">|", "To the end")):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setObjectName(f"bwStudy_{key}")
            row.addWidget(b)
            self.buttons[key] = b
        self.buttons["play"].setCheckable(True)
        self.mode = QtWidgets.QComboBox()
        self.mode.setObjectName("bwStudyMode")
        self.mode.addItems(LOOP_MODES)
        self.mode.setCurrentText(sim.BWLoop)
        self.speed = QtWidgets.QComboBox()
        self.speed.setObjectName("bwStudySpeed")
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}x", s)
        self.speed.setCurrentIndex(min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - float(sim.BWSpeed))))
        row.addStretch(1)
        row.addWidget(self.mode)
        row.addWidget(self.speed)
        lay.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.export = QtWidgets.QPushButton("Save frames…")
        self.export.setObjectName("bwStudyExport")
        self.export.setToolTip("PNG frames into a folder, and an MP4 when ffmpeg is installed")
        row.addWidget(self.export)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_hint("Values are degrees or millimetres from the joint's limit datums; between keyframes "
                            "the joint moves at constant speed, before and after it stays. A joint without "
                            "keyframes holds its position (Hold) or follows the others and any gear, belt or "
                            "rack link. OK keeps the study with the file and puts the parts back where they were."))
        lay.addStretch(1)
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(_slot(self.tick))
        self.name.editingFinished.connect(_slot(self.rename))
        self.duration.valueChanged.connect(_slot(self.set_duration))
        self.fps.valueChanged.connect(_slot(self.set_fps))
        self.table.itemSelectionChanged.connect(_slot(self.fill_keys))
        self.table.itemChanged.connect(_slot(self.joint_item_changed))
        self.keys.itemChanged.connect(_slot(self.key_changed))
        self.add.clicked.connect(_slot(self.add_keyframe))
        self.remove.clicked.connect(_slot(self.remove_keyframe))
        self.solve_button.clicked.connect(_slot(self.solve))
        self.scrub.valueChanged.connect(_slot(self.show_frame))
        self.buttons["first"].clicked.connect(lambda _c=False: self.go(0))
        self.buttons["last"].clicked.connect(lambda _c=False: self.go(self.frames - 1))
        self.buttons["back"].clicked.connect(lambda _c=False: self.go(self.frame - 1))
        self.buttons["forward"].clicked.connect(lambda _c=False: self.go(self.frame + 1))
        self.buttons["play"].toggled.connect(_slot(self.set_playing))
        self.buttons["stop"].clicked.connect(_slot(self.stop))
        self.mode.currentTextChanged.connect(_slot(self.set_mode))
        self.speed.currentIndexChanged.connect(_slot(self.set_speed))
        self.export.clicked.connect(_slot(self.save_frames))
        if self.rows:
            self.table.selectRow(0)
        self.fill_keys()

    # --- tables
    def fill_joints(self):
        from PySide import QtCore, QtWidgets

        self.filling = True
        for r, (motion, joint, kind) in enumerate(self.rows):
            item = QtWidgets.QTableWidgetItem(channel_label(joint, kind))
            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 0, item)
            hold = QtWidgets.QTableWidgetItem()
            hold.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
            hold.setCheckState(QtCore.Qt.Checked if motion.BWHold else QtCore.Qt.Unchecked)
            hold.setToolTip("Without keyframes: hold the joint (checked) or let it follow")
            self.table.setItem(r, 1, hold)
            count = QtWidgets.QTableWidgetItem(str(len(keyframes_of(motion))))
            count.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 2, count)
            now = QtWidgets.QTableWidgetItem(f"{reading(joint, kind):.2f} {UNITS[kind]}")
            now.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            self.table.setItem(r, 3, now)
        self.table.resizeColumnsToContents()
        self.filling = False

    def update_now(self):
        for r, (_m, joint, kind) in enumerate(self.rows):
            item = self.table.item(r, 3)
            if item is not None:
                item.setText(f"{reading(joint, kind):.2f} {UNITS[kind]}")

    @property
    def row(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def fill_keys(self):
        from PySide import QtWidgets

        self.filling = True
        self.keys.setRowCount(0)
        row = self.row
        if row is not None:
            motion, _joint, kind = row
            frames = keyframes_of(motion)
            self.keys.setRowCount(len(frames))
            for i, (t, v) in enumerate(frames):
                self.keys.setItem(i, 0, QtWidgets.QTableWidgetItem(f"{t:g}"))
                self.keys.setItem(i, 1, QtWidgets.QTableWidgetItem(f"{v:g} {UNITS[kind]}"))
            self.keys.setHorizontalHeaderLabels(["Time (s)", f"Value ({UNITS[kind]})"])
        self.filling = False
        self.highlight_current()

    def highlight_current(self):
        from PySide import QtGui

        row = self.row
        if row is None:
            return
        t = self.current_time()
        step = float(self.sim.cTimeStepOutput.Value) or 0.04
        frames = keyframes_of(row[0])
        for i, (kt, _v) in enumerate(frames):
            on = abs(kt - t) < step / 2
            brush = QtGui.QBrush(QtGui.QColor(255, 196, 0, 90)) if on else QtGui.QBrush()
            for c in (0, 1):
                item = self.keys.item(i, c)
                if item is not None:
                    item.setBackground(brush)
            if on:
                self.keys.setCurrentCell(i, 0)

    def joint_item_changed(self, item):
        from PySide import QtCore

        if self.filling or item.column() != 1:
            return
        motion = self.rows[item.row()][0]
        motion.BWHold = item.checkState() == QtCore.Qt.Checked
        self.dirty = True

    def key_changed(self, item):
        if self.filling:
            return
        row = self.row
        if row is None:
            return
        motion = row[0]
        frames = []
        for i in range(self.keys.rowCount()):
            try:
                t = float(self.keys.item(i, 0).text().split()[0])
                v = float(self.keys.item(i, 1).text().split()[0])
            except (ValueError, AttributeError, IndexError):
                continue
            frames.append((t, v))
        set_keyframes(motion, frames)
        self.fill_keys()
        self.table.item(self.table.currentRow(), 2).setText(str(len(keyframes_of(motion))))
        self.dirty = True

    def add_keyframe(self, time=None, value=None):
        row = self.row
        if row is None:
            return
        motion, joint, kind = row
        t = self.current_time() if time is None else float(time)
        v = reading(joint, kind) if value is None else float(value)
        set_keyframes(motion, keyframes_of(motion) + [(t, v)])
        self.fill_keys()
        self.table.item(self.table.currentRow(), 2).setText(str(len(keyframes_of(motion))))
        self.dirty = True

    def remove_keyframe(self):
        row = self.row
        i = self.keys.currentRow()
        if row is None or i < 0:
            return
        motion = row[0]
        frames = keyframes_of(motion)
        if i < len(frames):
            del frames[i]
            set_keyframes(motion, frames)
        self.fill_keys()
        self.table.item(self.table.currentRow(), 2).setText(str(len(keyframes_of(motion))))
        self.dirty = True

    # --- settings
    def rename(self):
        text = self.name.text().strip()
        if text:
            self.sim.Label = text

    def set_duration(self, value):
        self.sim.bTimeEnd = float(value)
        self.dirty = True

    def set_fps(self, value):
        self.sim.jFramesPerSecond = int(value)
        self.sim.cTimeStepOutput = 1.0 / int(value)
        self.dirty = True
        self.set_speed()

    def set_mode(self, text):
        self.sim.BWLoop = text

    def set_speed(self, _index=None):
        self.sim.BWSpeed = float(self.speed.currentData())
        self.timer.setInterval(max(5, int(round(1000.0 / (self.fps.value() * self.sim.BWSpeed)))))

    # --- solve and show
    def solve(self):
        import FreeCADGui as Gui

        rc, frames = run_study(self.assembly, self.sim, self.initial)
        self.frames = frames
        self.dirty = rc != 0
        self.scrub.blockSignals(True)
        self.scrub.setRange(0, max(0, frames - 1))
        self.scrub.blockSignals(False)
        if rc == 0:
            self.status.setText(f"{frames} frames, {self.sim.bTimeEnd.Value:g} s at {self.fps.value()} fps.")
            self.show_frame(min(self.frame, frames - 1))
        else:
            self.status.setText(f"Not solved: {solver_message(rc)}.")
        try:
            Gui.updateGui()
        except Exception:  # noqa: BLE001
            pass
        return rc

    def ensure_solved(self):
        return self.solve() == 0 if self.dirty or not self.frames else True

    def current_time(self):
        return time_of_frame(self.sim, self.frame)

    def show_frame(self, index):
        if not self.frames:
            return
        index = max(0, min(self.frames - 1, int(index)))
        self.frame = index
        show_frame(self.assembly, index)
        self.time_label.setText(f"{self.current_time():.2f} s")
        if self.scrub.value() != index:
            self.scrub.blockSignals(True)
            self.scrub.setValue(index)
            self.scrub.blockSignals(False)
        self.update_now()
        self.highlight_current()
        try:
            import FreeCADGui as Gui

            Gui.updateGui()
        except Exception:  # noqa: BLE001
            pass

    def go(self, index):
        if self.ensure_solved():
            self.show_frame(index)

    # --- playback
    def set_playing(self, on):
        if on:
            if not self.ensure_solved():
                self.buttons["play"].setChecked(False)
                return
            self.set_speed()
            self.direction = 1
            if self.frame >= self.frames - 1 and self.mode.currentText() == "Once":
                self.frame = 0
            self.timer.start()
            self.buttons["play"].setText("Pause")
        else:
            self.timer.stop()
            self.buttons["play"].setText("Play")
        self.playing = on

    def stop(self):
        self.buttons["play"].setChecked(False)
        if self.frames:
            self.show_frame(0)

    def tick(self):
        if not self.frames:
            return
        nxt = self.frame + self.direction
        mode = self.mode.currentText()
        if nxt >= self.frames or nxt < 0:
            if mode == "Once":
                self.show_frame(self.frames - 1)
                self.buttons["play"].setChecked(False)
                return
            if mode == "Loop":
                nxt = 0 if nxt >= self.frames else self.frames - 1
            else:  # ping-pong
                self.direction = -self.direction
                nxt = max(0, min(self.frames - 1, self.frame + self.direction))
        self.show_frame(nxt)

    # --- export
    def save_frames(self, folder=None):
        from PySide import QtWidgets

        if not self.ensure_solved():
            return None
        if folder is None:
            folder = QtWidgets.QFileDialog.getExistingDirectory(self.form, "Folder for the frames")
            if not folder:
                return None
        self.buttons["play"].setChecked(False)
        paths, mp4 = save_frames(self.assembly, self.sim, self.frames, folder, fps=self.fps.value(),
                                 on_frame=lambda i: self.show_frame(i))
        self.status.setText(f"{len(paths)} frames saved to {folder}" + (f", and {os.path.basename(mp4)}" if mp4 else
                                                                        " (no ffmpeg on the PATH: no MP4)"))
        return paths, mp4

    # --- task dialog
    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        return self._close(keep=True)

    def reject(self):
        return self._close(keep=False)

    def _close(self, keep):
        import FreeCADGui as Gui
        import UtilsAssembly

        self.timer.stop()
        UtilsAssembly.restoreAssemblyPartsPlacements(self.assembly, self.initial)
        if keep:
            self.rename()
            self.doc.commitTransaction()
        else:
            self.doc.abortTransaction()
        self.doc.recompute()
        _state["study"] = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        return True


class MotionLinkPanel:
    """Motion Link: two joints and a ratio become a Gears, Belt or RackPinion joint."""

    def __init__(self, assembly):
        from PySide import QtWidgets

        self.assembly = assembly
        self.doc = assembly.Document
        self.joints = [j for j in all_joints(assembly) if j.JointType in ("Revolute", "Slider") and live(j)]
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Motion Link")
        self.form.setObjectName("bwMotionLinkPanel")
        lay = QtWidgets.QVBoxLayout(self.form)
        grid = QtWidgets.QFormLayout()
        lay.addLayout(grid)
        self.first = QtWidgets.QComboBox()
        self.first.setObjectName("bwLinkFirst")
        self.second = QtWidgets.QComboBox()
        self.second.setObjectName("bwLinkSecond")
        for combo in (self.first, self.second):
            for j in self.joints:
                combo.addItem(f"{j.Label} ({j.JointType})")
        if len(self.joints) > 1:
            self.second.setCurrentIndex(1)
        grid.addRow("Drives", self.first)
        grid.addRow("Follows", self.second)
        self.ratio = QtWidgets.QDoubleSpinBox()
        self.ratio.setObjectName("bwLinkRatio")
        self.ratio.setRange(0.001, 1000.0)
        self.ratio.setDecimals(3)
        self.ratio.setValue(1.0)
        self.ratio.setPrefix("1 : ")
        grid.addRow("Ratio", self.ratio)
        self.mm = QtWidgets.QDoubleSpinBox()
        self.mm.setObjectName("bwLinkMm")
        self.mm.setRange(0.001, 100000.0)
        self.mm.setDecimals(3)
        self.mm.setSuffix(" mm per turn")
        self.mm.setValue(2.0 * math.pi * 10.0)
        grid.addRow("Travel", self.mm)
        self.reverse = QtWidgets.QCheckBox("Reverse")
        self.reverse.setObjectName("bwLinkReverse")
        grid.addRow("", self.reverse)
        self.preview = QtWidgets.QLabel()
        self.preview.setObjectName("bwLinkPreview")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        lay.addWidget(_hint("Two revolutes become a Gears joint (the follower turns the other way; Reverse makes "
                            "it a Belt, same way). A revolute and a slider become a Rack and Pinion with the "
                            "travel per turn. Both joints should turn or slide on the same carrier part. "
                            "The radii are 10 mm and 10 x ratio; edit the joint's Distance later for real gears."))
        lay.addStretch(1)
        self.first.currentIndexChanged.connect(_slot(self.update))
        self.second.currentIndexChanged.connect(_slot(self.update))
        self.ratio.valueChanged.connect(_slot(self.update))
        self.mm.valueChanged.connect(_slot(self.update))
        self.reverse.toggled.connect(_slot(self.update))
        self.update()

    def pair(self):
        a, b = self.first.currentIndex(), self.second.currentIndex()
        if not (0 <= a < len(self.joints) and 0 <= b < len(self.joints)) or a == b:
            return None
        return self.joints[a], self.joints[b]

    def kind(self):
        pair = self.pair()
        return link_kind(*pair) if pair else None

    def update(self, *_args):
        pair = self.pair()
        kind = self.kind()
        self.ratio.setVisible(kind == "Gears")
        self.mm.setVisible(kind == "RackPinion")
        if pair is None:
            self.preview.setText("Pick two different joints." if len(self.joints) > 1 else
                                 "The assembly needs two revolute or slider joints.")
        elif kind is None:
            self.preview.setText(f"Not supported: {pair[0].JointType} and {pair[1].JointType}. The solver links two "
                                 "revolutes (gears, belt) or a revolute and a slider (rack and pinion).")
        elif kind == "Gears":
            r1, r2 = link_radii(self.ratio.value())
            self.preview.setText(f"{'Belt' if self.reverse.isChecked() else 'Gears'} joint, radii {r1:g} / {r2:g} mm: "
                                 f"{pair[1].Label} turns {self.ratio.value():g} x {pair[0].Label}"
                                 f"{', the same way' if self.reverse.isChecked() else ', the other way'}.")
        else:
            self.preview.setText(f"Rack and pinion, pitch radius {pitch_radius(self.mm.value()):.3f} mm"
                                 f"{' (reversed)' if self.reverse.isChecked() else ''}.")

    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        import FreeCADGui as Gui

        pair = self.pair()
        if pair is None or self.kind() is None:
            self.update()
            return False
        self.doc.openTransaction("Motion link")
        try:
            link = make_link(self.assembly, pair[0], pair[1], self.ratio.value(), self.reverse.isChecked(),
                             self.mm.value() if self.kind() == "RackPinion" else None)
        except Exception as exc:  # noqa: BLE001
            self.doc.abortTransaction()
            self.preview.setText(f"Not linked: {exc}")
            return False
        self.doc.commitTransaction()
        self.link = link
        _state["link"] = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        return True

    def reject(self):
        import FreeCADGui as Gui

        _state["link"] = None
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        return True


def _open(panel, key):
    import FreeCADGui as Gui

    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    _state[key] = panel
    Gui.Control.showDialog(panel)
    return panel


def drive(assembly=None, joint=None, kind=None):
    assembly = assembly or _assembly()
    if assembly is None:
        return None
    return _open(DriveJointPanel(assembly, joint, kind), "driving")


def study(assembly=None, sim=None):
    """Open the study: the given one, the selected one, the latest, or a new one."""
    import FreeCADGui as Gui

    assembly = assembly or _assembly()
    if assembly is None:
        return None
    if sim is None:
        for obj in Gui.Selection.getSelection():
            if is_study(obj):
                sim = obj
                break
    if sim is None:
        existing = studies(assembly)
        sim = existing[-1] if existing else None
    return _open(MotionStudyPanel(assembly, sim), "study")


def link(assembly=None):
    assembly = assembly or _assembly()
    if assembly is None:
        return None
    return _open(MotionLinkPanel(assembly), "link")


class _Command:
    def __init__(self, name, text, tip, run):
        self.name, self.text, self.tip, self.run = name, text, tip, run

    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, self.name + ".svg"), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        import FreeCADGui as Gui

        if Gui.Control.activeDialog() and not any(_state.get(k) for k in ("driving", "study", "link")):
            return False
        return _app().ActiveDocument is not None and _assembly() is not None

    def Activated(self):
        for key in ("driving", "study", "link"):
            panel = _state.get(key)
            if panel is not None:
                panel.accept()
        self.run()


class _JointCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(HERE, "BruceWorx_Joint.svg"), "MenuText": "Joint",
                "ToolTip": "A joint between two parts: fixed, revolute, cylindrical, slider, ball, distance, "
                           "parallel, perpendicular, angle, rack and pinion, screw, gears, belt"}

    def IsActive(self):
        return _app().ActiveDocument is not None

    def Activated(self):
        popup_joint_menu()


COMMANDS = {
    "BruceWorx_Joint": None,
    "BruceWorx_DriveJoint": ("Drive Joint", "Move a joint with a slider, or animate it over its range; "
                                            "OK keeps the pose", drive),
    "BruceWorx_MotionStudy": ("Motion Study", "Keyframes per joint, playback, and frames to PNG and MP4; "
                                              "kept with the file as a Simulation", study),
    "BruceWorx_MotionLink": ("Motion Link", "Couple two joints at a ratio with a gears, belt or rack and "
                                            "pinion joint", link),
}


def install():
    """Register the commands (InitGui)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_Joint", _JointCommand())
    for name, spec in COMMANDS.items():
        if spec is not None:
            Gui.addCommand(name, _Command(name, spec[0], spec[1], spec[2]))
    _state["installed"] = True
