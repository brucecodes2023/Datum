# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Browser: model tree and properties in the left floating card (roadmap §13.3, marker 7).

* BrowserHeader: the card's title reads BROWSER in small caps (the dock is
  titled "Browser"; the overlay title bar draws it in capitals).
* BrowserBadges: a part or assembly carrying the BruceWorx part properties
  (bruceworx/partprops.py) shows its part number and lifecycle state as badges
  after its name ("LE-01-01001", "WIP 02"); a link to one (an assembly
  component) shows them as quiet text ("LE-01-02001 · 01").
* BrowserSummary: a quiet feature summary after the name ("Pad 8 mm", "R5",
  "Ø9 × 4"). The feature's type is named only when the label does not already.
* BrowserIconPlates: a faint plate behind tree icons in dark mode.
* BrowserGroupCaptions: property groups as quiet small-caps captions (drawn in
  C++, Gui/propertyeditor; this module only owns the switch).

The tree draws the extras itself ([BW] in Gui/Tree.cpp). It asks
``FreeCADGui.BrowserRowInfo(obj)`` for (summary, [(text, kind), ...]) after
each change; kind "warn" is drawn in the warning colour, "accent" in the
accent, "dim" faded, anything else neutral. All switches default on.
"""

from __future__ import annotations

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
MODEL_DOCK = "Model"
HEADER_TITLE = "Browser"
SWITCHES = ("BrowserHeader", "BrowserBadges", "BrowserSummary", "BrowserIconPlates", "BrowserGroupCaptions")

# Lifecycle states (bruceworx/pdm.py) -> (badge word, kind).
WIP, IN_REVIEW, RELEASED, OBSOLETE = "WIP", "In Review", "Released", "Obsolete"
STATE_BADGES = {WIP: ("WIP", "warn"), IN_REVIEW: ("REVIEW", "accent"), RELEASED: ("REV", ""), OBSOLETE: ("OBSOLETE", "dim")}
STATE_PROPERTIES = ("State", "LifecycleState")

# Feature type -> the word shown when the label does not already say it.
TYPE_WORDS = {
    "SketchObject": "Sketch",
    "Revolution": "Revolve",
    "LinearPattern": "Pattern",
    "PolarPattern": "Pattern",
    "MultiTransform": "Pattern",
    "Mirrored": "Mirror",
}
# Summaries that carry their own symbol ("R5", "Ø9 × 4") need no type word.
NO_TYPE_WORD = ("Fillet", "Chamfer", "Hole")


# ----- pure helpers (unit tested) ---------------------------------------------------------


def lifecycle_state(revision, state=None):
    """The lifecycle state: the explicit one if given, else read from the revision code.

    Numeric revisions (01, 02, ...) come before release, so they are WIP; letter
    revisions (A, B, ...) are released. Returns None when neither says anything.
    """
    text = (state or "").strip()
    if text:
        for known in (WIP, IN_REVIEW, RELEASED, OBSOLETE):
            if text.lower() == known.lower() or text.lower() == known.split()[-1].lower():
                return known
        return text
    rev = (revision or "").strip()
    if not rev:
        return None
    return WIP if rev.isdigit() else RELEASED


def part_badges(part_number, revision, state=None):
    """[(text, kind)] for a part: its number, then its state and revision."""
    out = []
    pn = (part_number or "").strip()
    rev = (revision or "").strip()
    if pn:
        out.append((pn, ""))
    current = lifecycle_state(rev, state)
    if current:
        word, kind = STATE_BADGES.get(current, (current.upper(), ""))
        out.append(((word + " " + rev).strip(), kind))
    return out


def component_meta(part_number, revision):
    """Quiet text for a link to a part: "LE-01-02001 · 01"."""
    parts = [p.strip() for p in (part_number or "", revision or "") if p and p.strip()]
    return " · ".join(parts)


def number(value):
    """8 -> "8", 52.5 -> "52.5", 0.1 + 0.2 -> "0.3"."""
    text = f"{round(float(value), 3):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def quantity(q):
    """A length in the user's units, compact: "8 mm"."""
    try:
        _text, factor, unit = q.getUserPreferred()
        return f"{number(q.Value / factor)} {unit}"
    except AttributeError:
        return f"{number(getattr(q, 'Value', q))} mm"


def angle(q):
    return f"{number(getattr(q, 'Value', q))}°"


def type_name(obj):
    return str(getattr(obj, "TypeId", "")).split("::")[-1]


def _hole_count(obj):
    sketch = getattr(obj, "Profile", None)
    if isinstance(sketch, (tuple, list)):
        sketch = sketch[0] if sketch else None
    geometry = getattr(sketch, "Geometry", None)
    if not geometry:
        return 0
    count = 0
    for index, geo in enumerate(geometry):
        if type(geo).__name__ not in ("Circle", "ArcOfCircle", "Point"):
            continue
        try:
            if sketch.getConstruction(index):
                continue
        except Exception:
            pass
        count += 1
    return count


def feature_value(obj):
    """The main dimension of a feature ("8 mm", "R5", "Ø9 × 4", "× 6"), or ""."""
    kind = type_name(obj)
    if kind in ("Pad", "Pocket"):
        mode = str(getattr(obj, "Type", "Length"))
        if mode == "Length":
            return quantity(obj.Length)
        if mode == "TwoLengths":
            return quantity(obj.Length) + " + " + quantity(obj.Length2)
        return {"ThroughAll": "through all", "UpToFirst": "to first", "UpToFace": "to face"}.get(mode, "")
    if kind in ("Revolution", "Groove"):
        return angle(obj.Angle) if hasattr(obj, "Angle") else ""
    if kind == "Fillet":
        return "R" + number(getattr(obj.Radius, "Value", obj.Radius))
    if kind == "Chamfer":
        return "C" + number(getattr(obj.Size, "Value", obj.Size))
    if kind == "Hole":
        text = "Ø" + number(getattr(obj.Diameter, "Value", obj.Diameter))
        count = _hole_count(obj)
        return text + (f" × {count}" if count > 1 else "")
    if kind in ("LinearPattern", "PolarPattern") and hasattr(obj, "Occurrences"):
        return f"× {obj.Occurrences}"
    if kind == "Thickness" and hasattr(obj, "Value"):
        return quantity(obj.Value)
    if kind == "Draft" and hasattr(obj, "Angle"):
        return angle(obj.Angle)
    joint = getattr(obj, "JointType", None)
    if joint is not None:
        return _joint_limits(obj)
    return ""


def _joint_limits(obj):
    for prefix, fmt in (("Angle", angle), ("Length", quantity)):
        low = getattr(obj, f"Enable{prefix}Min", False)
        high = getattr(obj, f"Enable{prefix}Max", False)
        if low or high:
            lo = fmt(getattr(obj, f"{prefix}Min")) if low else "…"
            hi = fmt(getattr(obj, f"{prefix}Max")) if high else "…"
            return f"{lo}–{hi}"
    return ""


def feature_summary(obj):
    """"Pad 8 mm" for a pad labelled "Base plate"; "8 mm" for one labelled "Pad"."""
    kind = type_name(obj)
    joint = getattr(obj, "JointType", None)
    word = str(joint) if joint is not None else TYPE_WORDS.get(kind, kind)
    value = feature_value(obj)
    if not value and word not in TYPE_WORDS.values() and joint is None:
        return ""  # plain objects (bodies, parts, origins): nothing to add
    label = str(getattr(obj, "Label", "")).strip().lower()
    if kind in NO_TYPE_WORD or not word or label.startswith(word.lower()):
        return value
    return (word + " " + value).strip()


def _prop(obj, name):
    try:
        return getattr(obj, name) if name in obj.PropertiesList else None
    except Exception:
        return None


def part_values(obj):
    """(part number, revision, state) when ``obj`` carries part properties, else None."""
    if obj is None or not hasattr(obj, "PropertiesList"):
        return None
    pn, rev = _prop(obj, "PartNumber"), _prop(obj, "Revision")
    if pn is None and rev is None:
        return None
    state = next((s for s in (_prop(obj, n) for n in STATE_PROPERTIES) if s), None)
    return str(pn or ""), str(rev or ""), str(state or "") or None


def linked_part(obj):
    """The object an App::Link (assembly component) points at, if any."""
    if type_name(obj) not in ("Link", "LinkElement", "LinkGroup"):
        return None
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        return None
    return target if target is not None and target is not obj else None


def row_info(obj, badges=True, summary=True):
    """(summary text, [(badge, kind)]) for one tree row."""
    values = part_values(obj) if badges else None
    if values:
        return "", part_badges(*values)
    if badges:
        target = linked_part(obj)
        values = part_values(target) if target is not None else None
        if values and (values[0] or values[1]):
            return component_meta(values[0], values[1]), []
    if summary:
        return feature_summary(obj), []
    return "", []


# ----- FreeCAD side --------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


class _Hook:
    """FreeCADGui.BrowserRowInfo. A new instance after a preference change makes
    the tree ask every row again."""

    def __init__(self, badges, summary):
        self.badges, self.summary = badges, summary

    def __call__(self, obj):
        try:
            meta, marks = row_info(obj, self.badges, self.summary)
            return meta, tuple(marks)
        except Exception:
            return "", ()


def _trees():
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    return [t for t in mw.findChildren(QtWidgets.QTreeWidget) if t.metaObject().className() == "Gui::TreeWidget"]


def _is_dark(widget):
    # By the text: a floating (transparent) Browser has no background colour to go by.
    return widget.palette().color(widget.foregroundRole()).lightness() > 128


def apply_rows():
    import FreeCADGui
    from PySide import QtGui

    prefs = _prefs()
    badges, summary = prefs.GetBool("BrowserBadges", True), prefs.GetBool("BrowserSummary", True)
    FreeCADGui.BrowserRowInfo = _Hook(badges, summary) if (badges or summary) else None
    plates = prefs.GetBool("BrowserIconPlates", True)
    for tree in _trees():
        # 10 % white: the same plate the toolbar icons get in dark mode (figure ui-plates).
        plate = QtGui.QColor(255, 255, 255, 26) if plates and _is_dark(tree) else QtGui.QColor()
        tree.setProperty("iconPlateColor", plate)
        tree.viewport().update()
    # Group captions are drawn in C++ and read their switch when painting.
    from PySide import QtWidgets
    import FreeCADGui

    for view in FreeCADGui.getMainWindow().findChildren(QtWidgets.QTreeView):
        if view.metaObject().className() == "Gui::PropertyEditor::PropertyEditor":
            view.viewport().update()


def apply_header():
    import FreeCADGui
    from PySide import QtGui, QtWidgets

    mw = FreeCADGui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, MODEL_DOCK)
    if dock is None:
        return False
    on = _prefs().GetBool("BrowserHeader", True)
    global _model_title
    if _model_title is None:
        _model_title = dock.windowTitle()
    title = HEADER_TITLE if on else _model_title
    if dock.windowTitle() != title:
        dock.setWindowTitle(title)
    for bar in mw.findChildren(QtWidgets.QWidget):
        if bar.metaObject().className() != "Gui::OverlayTitleBar":
            continue
        holder = bar.parentWidget()
        if holder is not dock and (holder is None or holder.objectName() != "OverlayLeft"):
            continue
        font = QtGui.QFont(bar.font())
        font.setCapitalization(QtGui.QFont.AllUppercase if on else QtGui.QFont.MixedCase)
        font.setLetterSpacing(QtGui.QFont.PercentageSpacing, 107 if on else 100)
        bar.setFont(font)
        bar.update()
    return True


def apply_all():
    try:
        apply_rows()
        apply_header()
    except Exception:
        import traceback

        import FreeCAD

        FreeCAD.Console.PrintError("BruceWorx Browser:\n" + traceback.format_exc())


_model_title = None
_observer = None
_group = None
_filter = None
_timer = None
_watched = []


def _schedule():
    if _timer is not None:
        _timer.start()


def _shutdown():
    """On quit: no more callbacks into Python while Qt tears the window down."""
    global _timer
    import FreeCADGui

    if _timer is not None:
        _timer.stop()
        _timer = None
    for widget in _watched:
        try:
            widget.removeEventFilter(_filter)
        except RuntimeError:
            pass
    _watched.clear()
    FreeCADGui.BrowserRowInfo = None


class _PrefObserver:
    def onChange(self, _group, name):
        if name in SWITCHES:
            _schedule()


def install():
    """Set the row hook now; the header and plates once the Model dock exists."""
    global _observer, _group, _filter, _timer
    if _observer is not None:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    _timer = QtCore.QTimer()
    _timer.setSingleShot(True)
    _timer.setInterval(0)
    _timer.timeout.connect(apply_all)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _observer = _PrefObserver()
    _group = FreeCAD.ParamGet(PREFS_PATH)  # keep the handle, see BruceWorx.install
    _group.Attach(_observer)
    apply_rows()

    def _late():
        global _filter
        mw = FreeCADGui.getMainWindow()
        dock = mw.findChild(QtWidgets.QDockWidget, MODEL_DOCK)
        if _timer is None:
            return  # quitting
        if dock is None:
            QtCore.QTimer.singleShot(500, _late)
            return

        class _Filter(QtCore.QObject):
            # Overlay title bars come and go as the panel docks and floats; the
            # theme can switch between light and dark at any time.
            def eventFilter(self, obj, event):
                kind = event.type()
                if kind == QtCore.QEvent.WindowTitleChange and obj is dock:
                    if _prefs().GetBool("BrowserHeader", True) and dock.windowTitle() != HEADER_TITLE:
                        _schedule()
                elif kind in (QtCore.QEvent.ParentChange, QtCore.QEvent.Show, QtCore.QEvent.PaletteChange,
                              QtCore.QEvent.StyleChange):
                    _schedule()
                return False

        _filter = _Filter()
        _watched.extend([dock] + _trees())
        for widget in _watched:
            widget.installEventFilter(_filter)
        apply_all()

    QtCore.QTimer.singleShot(0, _late)
