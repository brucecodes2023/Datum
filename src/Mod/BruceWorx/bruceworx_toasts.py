# SPDX-License-Identifier: LGPL-2.1-or-later
"""Undo toasts (roadmap UIX-10, from the Nxt add-on review).

After each finished undoable command a small card shows at the bottom centre
of the 3D view, just above the navigation pill: "Pad created", "Fillet
created", "Deleted", with an Undo button. It fades after about four seconds
(hovering keeps it), and a new one replaces it.

The hook is the App document observer's slotCommitTransaction: it runs once
per committed undo step, after the step is on the undo stack, so the toast's
name is the document's top undo name. Undo runs Std_Undo, and only while that
step is still the top of the stack (nothing was done or undone since);
otherwise the toast says so and goes.

Not shown for:

* property edits in the property editor (one step per typed value; the
  transaction is named "Pad.Length");
* steps committed while a sketch is open for editing: every constraint and
  line is a step there, which is too noisy;
* names in QUIET_TRANSACTIONS, BruceWorx's own bookkeeping (other modules
  may add to it).

Selection and view changes are not undo steps, so they never reach the hook.
Preferences/BruceWorx/UndoToasts (default on).
"""

from __future__ import annotations

import re

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "UndoToasts"
OBJECT_NAME = "BruceWorxToast"
SHOW_MS = 4000
FADE_MS = 350
PILL_GAP = 8  # between the toast and the navigation pill
UNDO_TEXT = "Undo"

# Transactions that never get a toast. Other modules may add their bookkeeping names.
QUIET_TRANSACTIONS = {"Recompute", "Sketch recompute"}
# The property editor names its steps "<object>.<property>" or "property <name>".
_PROPERTY_EDIT = re.compile(r"^(?:[A-Za-z_][\w]*\.[A-Za-z_]\w*|property \w+)(?:\.\.\.)?$")
_CREATE = re.compile(r"^(?:Make|Create|Add|New|Insert)\s+(?:(?:a|an|additive|subtractive)\s+)*(.+)$", re.I)
_PAST = {"Delete": "Deleted", "Paste": "Pasted", "Duplicate": "Duplicated", "Move": "Moved", "Mirror": "Mirrored"}


# ----- pure helpers (unit tested) ---------------------------------------------------------


def toast_text(name):
    """The toast's words for a transaction: "Make Pad" -> "Pad created", "Delete" -> "Deleted"."""
    name = (name or "").strip()
    if not name:
        return ""
    if name in _PAST:
        return _PAST[name]
    match = _CREATE.match(name)
    if match:
        what = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", match.group(1).strip())  # LinearPattern -> Linear Pattern
        return (what[:1].upper() + what[1:] + " created") if what else name
    return name


def should_toast(name, sketch_editing=False, quiet=None):
    """True when a committed step named ``name`` gets a toast."""
    name = (name or "").strip()
    if not name or sketch_editing:
        return False
    if name in (QUIET_TRANSACTIONS if quiet is None else quiet):
        return False
    return not _PROPERTY_EDIT.match(name)


def can_undo(stamp, undo_count, undo_names, pending=False):
    """True while the step a toast shows is still the top of the undo stack."""
    count, name = stamp
    return not pending and undo_count == count and bool(undo_names) and undo_names[0] == name


def toast_place(view_w, view_h, w, h, pill=None, bottom_margin=12):
    """Top-left (x, y) of the toast: centred above the pill (x, y, w, h), or where the pill would sit."""
    if pill is not None:
        px, py, pw, _ph = pill
        x = px + (pw - w) // 2
        y = py - PILL_GAP - h
    else:
        x = (view_w - w) // 2
        y = view_h - bottom_margin - h
    x = max(0, min(x, view_w - w))
    return int(x), int(max(0, y))


# ----- FreeCAD side ------------------------------------------------------------------------


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def sketch_editing(doc_name):
    """True while a sketch of ``doc_name`` is open for editing."""
    try:
        import FreeCADGui

        vp = FreeCADGui.getDocument(doc_name).getInEdit()
        obj = getattr(vp, "Object", None) if vp is not None else None
        return getattr(obj, "TypeId", "") == "Sketcher::SketchObject"
    except Exception:
        return False


def _active_view():
    import FreeCADGui
    from PySide import QtWidgets

    mw = FreeCADGui.getMainWindow()
    mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
    sub = mdi.activeSubWindow() if mdi else None
    return sub.widget() if sub else None


def _make_toast_class():
    from PySide import QtCore, QtWidgets

    class Toast(QtWidgets.QFrame):
        """One card per view; reused for the next step."""

        def __init__(self, view):
            super().__init__(view)
            self.setObjectName(OBJECT_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.stamp = None
            self.doc_name = None
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(12, 4, 4, 4)
            layout.setSpacing(10)
            self.label = QtWidgets.QLabel(self)
            self.label.setObjectName(OBJECT_NAME + "Text")
            layout.addWidget(self.label)
            self.undo = QtWidgets.QToolButton(self)
            self.undo.setObjectName(OBJECT_NAME + "Undo")
            self.undo.setText(UNDO_TEXT)
            self.undo.setFocusPolicy(QtCore.Qt.NoFocus)
            self.undo.setAutoRaise(True)
            self.undo.clicked.connect(self.run_undo)
            layout.addWidget(self.undo)
            self.opacity = QtWidgets.QGraphicsOpacityEffect(self)
            self.opacity.setOpacity(1.0)
            self.setGraphicsEffect(self.opacity)
            self.fade = QtCore.QPropertyAnimation(self.opacity, b"opacity", self)
            self.fade.setDuration(FADE_MS)
            self.fade.setStartValue(1.0)
            self.fade.setEndValue(0.0)
            self.fade.finished.connect(self.hide)
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(SHOW_MS)
            self.timer.timeout.connect(self.fade.start)
            view.installEventFilter(self)

        def popup(self, doc_name, name, count):
            self.doc_name = doc_name
            self.stamp = (count, name)
            self.label.setText(toast_text(name))
            self.undo.setEnabled(True)
            self.undo.setToolTip(f"Undo {name}")
            self.fade.stop()
            self.opacity.setOpacity(1.0)
            self.adjustSize()
            self.place()
            self.show()
            self.raise_()
            self.timer.start()

        def place(self):
            view = self.parentWidget()
            if view is None:
                return
            pill = None
            for child in view.findChildren(QtWidgets.QFrame, "BruceWorxNavPill"):
                if child.isVisible():
                    g = child.geometry()
                    pill = (g.x(), g.y(), g.width(), g.height())
                    break
            x, y = toast_place(view.width(), view.height(), self.width(), self.height(), pill)
            self.move(x, y)
            self.raise_()

        def eventFilter(self, obj, event):
            if obj is self.parentWidget() and event.type() == QtCore.QEvent.Resize and self.isVisible():
                QtCore.QTimer.singleShot(0, self.place)
            return False

        def enterEvent(self, event):
            self.timer.stop()
            self.fade.stop()
            self.opacity.setOpacity(1.0)
            super().enterEvent(event)

        def leaveEvent(self, event):
            if self.isVisible():
                self.timer.start(SHOW_MS // 2)
            super().leaveEvent(event)

        def run_undo(self):
            import FreeCAD
            import FreeCADGui

            doc = FreeCAD.listDocuments().get(self.doc_name)
            if doc is None or self.stamp is None:
                return self.hide()
            if not can_undo(self.stamp, doc.UndoCount, doc.UndoNames, doc.HasPendingTransaction):
                self.label.setText("Changed since; use Edit › Undo")
                self.undo.setEnabled(False)
                self.timer.start(1500)
                return
            self.stamp = None
            if FreeCAD.ActiveDocument is doc:
                FreeCADGui.runCommand("Std_Undo")
            else:
                doc.undo()
            self.timer.stop()
            self.hide()

        def dismiss(self):
            self.timer.stop()
            self.fade.stop()
            self.stamp = None
            self.hide()

    return Toast


_toast_class = None
_toasts = {}  # view pointer -> Toast
_observer = None
_group = None
_installed = False


def _cpp(obj):
    try:
        import shiboken6

        return shiboken6.getCppPointer(obj)[0]
    except Exception:
        return id(obj)


def toasts():
    """The live toast widgets (for tests and scripts)."""
    return [t for t in _toasts.values() if _alive(t)]


def hide_all():
    for toast in toasts():
        toast.dismiss()


def show(doc_name, name, count):
    """Show the toast for the step ``name`` (``count`` = the stack size after it)."""
    global _toast_class
    view = _active_view()
    if view is None:
        return None
    hide_all()
    key = _cpp(view)
    toast = _toasts.get(key)
    if not _alive(toast):
        if _toast_class is None:
            _toast_class = _make_toast_class()
        toast = _toast_class(view)
        _toasts[key] = toast
        view.destroyed.connect(lambda _o=None, k=key: _toasts.pop(k, None))
    toast.popup(doc_name, name, count)
    return toast


def _on_commit(doc_name):
    import FreeCAD

    doc = FreeCAD.listDocuments().get(doc_name)
    if doc is None or not enabled():
        return
    names = doc.UndoNames
    if not names:
        return
    name = names[0]
    if not should_toast(name, sketch_editing(doc_name)):
        hide_all()  # the shown step is no longer the last one
        return
    show(doc_name, name, doc.UndoCount)


class _DocObserver:
    def slotCommitTransaction(self, doc):
        from PySide import QtCore

        # Read the stack once the command has finished (recompute, dialog closed).
        QtCore.QTimer.singleShot(0, lambda n=doc.Name: _on_commit(n))

    def slotUndoDocument(self, _doc):
        hide_all()

    def slotRedoDocument(self, _doc):
        hide_all()

    def slotDeletedDocument(self, doc):
        for toast in toasts():
            if toast.doc_name == doc.Name:
                toast.dismiss()


class _PrefObserver:
    def onChange(self, _group, name):
        if name == PREF_NAME and not enabled():
            hide_all()


_prefs_observer = None


def _shutdown():
    import FreeCAD

    for toast in toasts():
        toast.timer.stop()
        toast.fade.stop()
    if _observer is not None:
        try:
            FreeCAD.removeDocumentObserver(_observer)
        except Exception:
            pass


def install():
    """Show a toast after each committed undo step."""
    global _observer, _prefs_observer, _group, _installed
    if _installed:
        return
    import FreeCAD
    from PySide import QtWidgets

    _observer = _DocObserver()
    FreeCAD.addDocumentObserver(_observer)
    _prefs_observer = _PrefObserver()
    _group = FreeCAD.ParamGet(PREFS_PATH)  # keep the handle: a collected wrapper detaches
    _group.Attach(_prefs_observer)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _installed = True
