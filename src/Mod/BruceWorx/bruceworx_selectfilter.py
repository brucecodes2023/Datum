# SPDX-License-Identifier: LGPL-2.1-or-later
"""Selection filter, like Fusion 360's (Bruce 2 Nov: "add a selection type (face, body, edge, etc.)").

A "Select: <mode>" box in the top bar, a command per mode (so each can get a shortcut) and a readout in the
status bar. The mode is Preferences/BruceWorx/SelectionFilter (a string) and stays across sessions.

* Auto: unchanged. A pick in an assembly selects the top component first, a pick inside it goes one level
  deeper, Option-click picks the face (bruceworx_picktrace, pref AssemblyPick).
* Component: always the component directly under the open assembly (the AssemblyPick rule, even when that
  pref is off, and no drilling down).
* Body: the whole solid that was clicked (its body, else the deepest object), no face, edge or vertex.
* Face, Edge, Vertex: only elements of that kind can be pre-selected and selected. FreeCAD's selection gate
  does it (Gui.Selection.addSelectionGate); tree clicks name no element, so they are ignored too.

Component and Body rewrite the pick in the picktrace selection observer (it asks this module which rule
applies, see rewrite_active); Face, Edge and Vertex leave that observer idle so the two never fight.
FreeCAD allows one selection gate at a time: a task panel that sets its own gate replaces this one, and the
filter is back at the next mode change (or restart).
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "SelectionFilter"
BAR_NAME = "BruceWorxSelectFilter"

AUTO, COMPONENT, BODY, FACE, EDGE, VERTEX = "Auto", "Component", "Body", "Face", "Edge", "Vertex"
MODES = (AUTO, COMPONENT, BODY, FACE, EDGE, VERTEX)
ELEMENT_MODES = (FACE, EDGE, VERTEX)  # the modes the selection gate enforces
REWRITE_MODES = (COMPONENT, BODY)  # the modes the picktrace observer enforces
COMMAND_PREFIX = "BruceWorx_Select"  # BruceWorx_SelectFace, ...
LABEL = "Select: {mode}"
READOUT = "Select {mode}"


# ----- pure helpers (unit tested) ---------------------------------------------------------


def parse_mode(value):
    """The mode a stored string names (any case, spaces ignored); Auto for anything else."""
    text = str(value or "").strip().lower()
    for mode in MODES:
        if mode.lower() == text:
            return mode
    return AUTO


def get_mode(param):
    """The saved mode. ``param`` is a FreeCAD parameter group (GetString)."""
    try:
        return parse_mode(param.GetString(PREF_NAME, AUTO))
    except Exception:
        return AUTO


def set_mode(param, mode):
    """Save a mode (an unknown name saves Auto); returns what was saved."""
    mode = parse_mode(mode)
    param.SetString(PREF_NAME, mode)
    return mode


def element_type(sub):
    """"Face" for "Face12" or "Body.Pad.Face12"; "" for an object ("", "Body.Pad.") or None."""
    last = (sub or "").rsplit(".", 1)[-1]
    head = last.rstrip("0123456789")
    return head if head in ELEMENT_MODES and last[len(head):].isdigit() else ""


def allows(mode, sub):
    """Whether a pick of ``sub`` may be selected in ``mode``: the gate's decision."""
    if mode in ELEMENT_MODES:
        return element_type(sub) == mode
    return True


def rewrite_active(mode, assembly_pick_on):
    """Whether the picktrace observer rewrites 3D picks: Auto as the AssemblyPick pref says, Component and
    Body always, the element modes never (the gate does their work)."""
    if mode in REWRITE_MODES:
        return True
    return mode == AUTO and bool(assembly_pick_on)


def body_pick(names, types):
    """The subname that selects the whole solid of a pick. ``names`` is the path to the picked object
    ("Link", "Body" for "Link.Body.Face3" under an assembly) and ``types`` the type ids of the root object
    followed by those path objects (one more than ``names``). The first PartDesign body wins, else the
    deepest object; "" is the root object itself."""
    if not types:
        return ""
    index = len(types) - 1
    for i, type_id in enumerate(types):
        if type_id == "PartDesign::Body":
            index = i
            break
    index = min(index, len(names))
    return ".".join(names[:index]) + "." if index else ""


def retarget(mode, sub, last=None, types=None):
    """The subname a 3D pick ``sub`` (path from the selected root, ending in an element) becomes in ``mode``,
    or None to leave it. ``last`` is the component the rule selected before (Auto drills down from it);
    ``types`` the type ids for body_pick."""
    import bruceworx_picktrace as pt

    if not sub or sub.endswith("."):
        return None  # a tree click names no element
    if mode == AUTO:
        return pt.component_pick(sub, last)
    if mode == COMPONENT:
        return pt.component_pick(sub)
    if mode == BODY:
        new = body_pick(sub.split(".")[:-1], types or [])
        return new if new != sub else None
    return None


# ----- FreeCAD side ------------------------------------------------------------------------


class Gate:
    """Selection gate (Gui.Selection.addSelectionGate): lets only elements of the mode's kind through."""

    def __init__(self, mode):
        self.mode = mode

    def allow(self, doc, obj, sub):
        return allows(self.mode, sub)


_installed = False
_gate = None  # the gate this module put up; None when none is
_group = None  # the pref handle the observer hangs on (a collected wrapper detaches)
_prefs_observer = None
_combo = None
_label = None
_bar = None


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def mode():
    """The current mode."""
    return get_mode(_prefs())


def rewrite_on(assembly_pick_on):
    """picktrace asks this before it rewrites a pick (see rewrite_active)."""
    return rewrite_active(mode(), assembly_pick_on)


def chain_types(root, sub):
    """Type ids of ``root`` and of each object along the path in ``sub`` ("Link.Body.Face3"), for body_pick."""
    types = [root.TypeId]
    path = sub.split(".")[:-1]
    for i in range(len(path)):
        try:
            obj = root.getSubObject(".".join(path[:i + 1]) + ".", 1)
        except Exception:
            obj = None
        types.append(obj.TypeId if obj is not None else "")
    return types


def apply_gate(new_mode):
    """Put up the gate for an element mode; take ours down for any other. Another module's gate is left."""
    global _gate
    import FreeCADGui

    if new_mode in ELEMENT_MODES:
        _gate = Gate(new_mode)
        FreeCADGui.Selection.addSelectionGate(_gate)  # replaces any earlier gate
    elif _gate is not None:
        _gate = None
        FreeCADGui.Selection.removeSelectionGate()


def apply(new_mode=None):
    """Bring the gate, the top bar box and the status readout in line with the saved mode (or ``new_mode``)."""
    new_mode = parse_mode(new_mode) if new_mode is not None else mode()
    try:
        apply_gate(new_mode)
    except Exception:
        pass
    if _combo is not None and _alive(_combo):
        index = MODES.index(new_mode)
        if _combo.currentIndex() != index:
            _combo.blockSignals(True)
            _combo.setCurrentIndex(index)
            _combo.blockSignals(False)
    _update_readout(new_mode)


def restore():
    """Take the gate down (shutdown, or tests); the saved mode is kept."""
    global _gate
    import FreeCADGui

    if _gate is not None:
        _gate = None
        try:
            FreeCADGui.Selection.removeSelectionGate()
        except Exception:
            pass


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _update_readout(current):
    """The status bar readout, beside the others (bruceworx_statusbar); only while StatusReadouts is on."""
    global _label
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    bar = mw.statusBar() if mw else None
    if bar is None:
        return
    if _label is None or not _alive(_label):
        _label = QtWidgets.QLabel(bar)
        _label.setObjectName("selectFilterReadout")
        bar.addPermanentWidget(_label)
    _label.setText(READOUT.format(mode=current))
    _label.setToolTip("Selection filter: what a click in the 3D view can select")
    _label.setVisible(bool(_prefs().GetBool("StatusReadouts", False)))


def _choose(index):
    set_mode(_prefs(), MODES[index])  # the pref observer then calls apply()


def _make_bar():
    global _bar, _combo
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    if mw is None:
        return False
    if _bar is not None and _alive(_bar):
        return True
    _bar = QtWidgets.QToolBar(mw)
    _bar.setObjectName(BAR_NAME)
    _bar.setWindowTitle("Selection filter")
    _bar.setMovable(False)
    # Hidden toggle: ToolBarManager then leaves the bar alone on every switch.
    _bar.toggleViewAction().setVisible(False)
    _combo = QtWidgets.QComboBox(_bar)
    _combo.setObjectName("BruceWorxSelectFilterBox")
    _combo.setToolTip("Selection filter: Auto, Component, Body, Face, Edge or Vertex")
    _combo.setFocusPolicy(QtCore.Qt.NoFocus)  # keep the shortcuts on the 3D view
    for item in MODES:
        _combo.addItem(LABEL.format(mode=item), item)
    _combo.activated.connect(_choose)
    _bar.addWidget(_combo)
    mw.addToolBar(QtCore.Qt.TopToolBarArea, _bar)
    try:
        import bruceworx_toolbars

        bruceworx_toolbars.relayout()
    except Exception:
        pass
    return True


class _ModeCommand:
    def __init__(self, target):
        self.target = target

    def GetResources(self):
        return {
            "MenuText": LABEL.format(mode=self.target),
            "ToolTip": f"Selection filter: {self.target}",
        }

    def Activated(self):
        set_mode(_prefs(), self.target)

    def IsActive(self):
        return True


class _PrefObserver:
    def onChange(self, _group, name):
        if name == PREF_NAME:
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, apply)


def _shutdown():
    restore()


def install():
    """Register the commands, build the top bar box, restore the saved mode (InitGui)."""
    global _installed, _group, _prefs_observer
    if _installed:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    for item in MODES:
        FreeCADGui.addCommand(COMMAND_PREFIX + item, _ModeCommand(item))
    _prefs_observer = _PrefObserver()
    _group = FreeCAD.ParamGet(PREFS_PATH)
    _group.Attach(_prefs_observer)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)

    def _late(tries=50):
        if not _make_bar() and tries > 0:
            QtCore.QTimer.singleShot(200, lambda: _late(tries - 1))
            return
        apply()

    QtCore.QTimer.singleShot(0, _late)
    _installed = True
