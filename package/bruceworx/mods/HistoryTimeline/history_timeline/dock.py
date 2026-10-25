# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD dock that shows ``read_timeline`` and rolls ``Body.Tip``.

For an assembly it lists the components and joints in creation order
(``assembly.read_assembly``); a click selects, nothing rolls back. The dock
hides itself while the active document has no history to show and comes back
when it has (``Mod/HistoryTimeline/HideWhenEmpty``, default true).

BruceWorx ships this on by default: ``Mod/HistoryTimeline/Enabled`` defaults to
true, so the dock shows on a normal start. The off switches are the
BruceWorx → History Timeline menu item, the "History timeline" box on
Preferences → BruceWorx, or closing the dock. All three write the preference
back to false; the dock then stays hidden on later starts.
"""

from __future__ import annotations

import FreeCAD

from history_timeline import assembly as asm
from history_timeline import model
from history_timeline.bar import RIBBON_HEIGHT, TimelineBar
from history_timeline.qtcompat import load_qt, qt_enum

QtCore, QtGui, QtWidgets = load_qt()

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/HistoryTimeline"
DOCK_OBJECT_NAME = "HistoryTimelineDock"
COMMAND_NAME = "HistoryTimeline_Toggle"

_state = {
    "dock": None,
    "action": None,
    "observer": None,
    "pref_observer": None,
    "pref_group": None,
    "syncing": False,
    "installed": False,
}


def prefs():
    return FreeCAD.ParamGet(PARAM_PATH)


ENABLED_DEFAULT = True
HIDE_WHEN_EMPTY_DEFAULT = True
# How often the dock checks the active body / assembly / object in edit.
# None of those changes reaches a document observer.
POLL_MS = 400
# Marker drag: Body.Tip follows the marker live (set + recompute on every chip it
# crosses) while the body has at most this many Group entries. Above it the
# marker and the dimming still move live and the tip is set once, at the drop.
LIVE_PREVIEW_MAX = 40


def is_enabled() -> bool:
    return bool(prefs().GetBool("Enabled", ENABLED_DEFAULT))


def hide_when_empty() -> bool:
    return bool(prefs().GetBool("HideWhenEmpty", HIDE_WHEN_EMPTY_DEFAULT))


def _bottom_area():
    return qt_enum(
        QtCore.Qt,
        ("DockWidgetArea", "BottomDockWidgetArea"),
        ("BottomDockWidgetArea",),
    )


def _view_icon(obj):
    if not FreeCAD.GuiUp:
        return None
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return None
    icon = getattr(view, "Icon", None)
    return icon


def _show_only(body, feature):
    """Show ``feature`` and hide the body's other PartDesign features (what Set tip
    leaves on screen). ``feature`` None hides them all."""
    for obj in body.Group:
        try:
            if not obj.isDerivedFrom("PartDesign::Feature"):
                continue
            want = obj == feature
            if bool(obj.Visibility) != want:
                obj.Visibility = want
        except (AttributeError, RuntimeError):
            continue


def _active_object(key):
    """``ActiveView.getActiveObject(key)`` or None (no GUI, no view, a 2D view)."""
    if not FreeCAD.GuiUp:
        return None
    import FreeCADGui

    gui_doc = FreeCADGui.ActiveDocument
    view = getattr(gui_doc, "ActiveView", None) if gui_doc is not None else None
    getter = getattr(view, "getActiveObject", None)
    if getter is None:
        return None
    try:
        obj = getter(key)
    except (RuntimeError, TypeError, AttributeError):
        return None
    if isinstance(obj, tuple):
        obj = obj[0] if obj else None
    return obj


def _in_edit_name():
    if not FreeCAD.GuiUp:
        return None
    import FreeCADGui

    gui_doc = FreeCADGui.ActiveDocument
    try:
        vp = gui_doc.getInEdit() if gui_doc is not None else None
    except (RuntimeError, AttributeError):
        return None
    obj = getattr(vp, "Object", None) if vp is not None else None
    return getattr(obj, "Name", None)


def _active_assembly():
    for key in ("assembly", "part"):
        obj = _active_object(key)
        if asm.is_assembly(obj):
            return obj
    return None


def active_source():
    """What the bar shows: ``(kind, obj, how)``, see ``assembly.pick_source``."""
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None, None, "none"
    body = _active_object("pdbody")
    return asm.pick_source(doc.Objects, body, _active_assembly())


def active_body():
    """Body the bar shows, or None when it shows an assembly or nothing.

    Returns ``(body, source)`` where source is ``"active"``, ``"first"``, or ``"none"``.
    """
    kind, obj, how = active_source()
    if kind != "body":
        return None, "none"
    return obj, how


def _poll_key():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    names = []
    for obj in (_active_object("pdbody"), _active_assembly()):
        names.append(getattr(obj, "Name", None))
    return (doc.Name, tuple(names), _in_edit_name())


class _DocumentObserver:
    def __init__(self, dock):
        self._dock = dock

    def slotChangedObject(self, _obj, _prop):
        self._dock.schedule_refresh()

    def slotCreatedObject(self, _obj):
        self._dock.schedule_refresh()

    def slotDeletedObject(self, _obj):
        self._dock.schedule_refresh()

    def slotActivateDocument(self, _doc):
        self._dock.schedule_refresh()

    def slotDeleteDocument(self, _doc):
        self._dock.schedule_refresh()

    def slotUndoDocument(self, _doc):
        self._dock.schedule_refresh()

    def slotRedoDocument(self, _doc):
        self._dock.schedule_refresh()


class HistoryTimelineDock(QtWidgets.QDockWidget):
    def __init__(self, parent):
        super().__init__("History Timeline", parent)
        self.setObjectName(DOCK_OBJECT_NAME)
        self.setAllowedAreas(_bottom_area())
        self._bar = TimelineBar(self)
        self._bar.tipRequested.connect(self._on_click)
        self._bar.editRequested.connect(self._on_double)
        self._bar.contextMenuRequested.connect(self._on_context)
        self._bar.tipDragStarted.connect(self._on_drag_started)
        self._bar.tipPreviewRequested.connect(self._on_drag_preview)
        self._bar.tipDragFinished.connect(self._on_drag_finished)
        self._bar.tipDragCancelled.connect(self._on_drag_cancelled)
        self._bar.playbackRequested.connect(self._on_playback)
        self._bar.renameRequested.connect(self._rename)
        self._drag = None
        self.setWidget(self._bar)
        # One row, no dock title: the bar is the only header (roadmap §13.3).
        # The menu item and the preference still show and hide it. A restored
        # window state cannot stretch the dock back to the old tall size.
        self.setTitleBarWidget(QtWidgets.QWidget(self))
        frame = self.style().pixelMetric(
            qt_enum(
                QtWidgets.QStyle,
                ("PixelMetric", "PM_DockWidgetFrameWidth"),
                ("PM_DockWidgetFrameWidth",),
            )
        )
        dock_height = RIBBON_HEIGHT + max(frame, 0) * 2
        self.setMinimumHeight(dock_height)
        self.setMaximumHeight(dock_height)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        self._suspend = False
        self._live = False
        self._kind = None
        self._poll_key = None
        self._want_visible = True
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)

    def set_live(self, live):
        """Follow the documents (enabled) or stay hidden (disabled)."""
        self._live = bool(live)
        if self._live:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

    def _poll(self):
        key = _poll_key()
        # A restored window state can show the dock behind our back.
        if key != self._poll_key or self.isHidden() == self._want_visible:
            self.schedule_refresh()

    def schedule_refresh(self):
        if not self._suspend:
            self._refresh_timer.start()

    def refresh(self):
        if self._suspend:
            return
        self._poll_key = _poll_key()
        kind, obj, source = active_source()
        self._kind = kind
        if kind == "assembly":
            timeline = asm.read_assembly(obj, _in_edit_name())
            for entry in timeline.entries:
                entry.icon = _view_icon(entry.obj)
            self._bar.setToolTip("Creation order · click selects · double-click edits a joint")
            self._bar.set_timeline(
                timeline.label, timeline.entries, marker=False, note=timeline.note()
            )
        elif kind == "body":
            timeline = model.read_timeline(obj)
            for entry in timeline.entries:
                entry.icon = _view_icon(entry.obj)
            label = timeline.body_label
            if source == "first":
                label += " (not active)"
            self._bar.setToolTip("Click rolls the tip · double-click edits · right-click to suppress")
            self._bar.set_timeline(label, timeline.entries)
        else:
            self._bar.set_timeline(None, [], "No feature history in this document.")
        self._update_visibility(asm.has_history(kind, obj))

    def _update_visibility(self, has_history):
        if not self._live:
            return
        show = has_history or not hide_when_empty()
        self._want_visible = show
        if show == self.isHidden():
            self.setVisible(show)

    def _assembly_object(self, name):
        kind, assembly, _how = active_source()
        if kind != "assembly":
            return None
        doc = assembly.Document
        obj = doc.getObject(name)
        return obj

    def _select(self, name):
        obj = self._assembly_object(name)
        if obj is None or not FreeCAD.GuiUp:
            return
        import FreeCADGui

        root, sub = asm.selection_path(obj)
        try:
            FreeCADGui.Selection.clearSelection()
            if sub:
                FreeCADGui.Selection.addSelection(root.Document.Name, root.Name, sub)
            else:
                FreeCADGui.Selection.addSelection(obj)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"History timeline: cannot select {name}: {exc}\n")

    def _on_click(self, name):
        if self._kind == "assembly":
            self._select(name)
        else:
            self._roll_to(name)

    def _on_double(self, name):
        if self._kind != "assembly":
            self._edit(name)
            return
        self._select(name)
        obj = self._assembly_object(name)
        if obj is None or not asm.is_joint(obj) or not FreeCAD.GuiUp:
            return
        import FreeCADGui

        try:
            # Same as a double-click on the joint in the tree.
            FreeCADGui.ActiveDocument.setEdit(obj)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"History timeline: cannot edit {name}: {exc}\n")

    def _on_context(self, name, global_pos):
        # Assemblies have nothing to roll back or suppress from here.
        if self._kind != "assembly":
            self._popup(name, global_pos)

    def _feature(self, name):
        body, _source = active_body()
        if body is None:
            return None, None
        for obj in body.Group:
            if obj.Name == name:
                return body, obj
        return body, None

    def _transaction(self, title, fn):
        body, _source = active_body()
        doc = body.Document if body is not None else FreeCAD.ActiveDocument
        if doc is None:
            return
        self._suspend = True
        doc.openTransaction(title)
        try:
            fn(doc, body)
        except Exception:
            doc.abortTransaction()
            self._suspend = False
            raise
        else:
            doc.commitTransaction()
            self._suspend = False
            doc.recompute()
            self.refresh()

    def _roll_to(self, name):
        body, feature = self._feature(name)
        if feature is None:
            return
        ok, reason = model.can_be_tip(body, feature)
        if not ok:
            FreeCAD.Console.PrintWarning(reason + "\n")
            return

        def apply(doc, _body):
            model.set_tip(body, feature)
            # Match PartDesign_MoveTip: show the tip feature and nothing else.
            if FreeCAD.GuiUp:
                _show_only(body, feature)

        self._transaction("Roll history tip", apply)

    def _on_playback(self, action):
        """To start / back / forward / to end: Set tip on the step's solid."""
        body, _source = active_body()
        if body is None or self._drag is not None:
            return
        changed, target = model.playback_target(body, action)
        if not changed:
            return

        def apply(_doc, _body):
            model.set_tip(body, target)
            if FreeCAD.GuiUp:
                _show_only(body, target)

        self._transaction("Roll history tip", apply)

    # ----- marker drag --------------------------------------------------------------------

    def _on_drag_started(self):
        body, _source = active_body()
        if body is None or self._drag is not None:
            return
        doc = body.Document
        self._drag = {
            "body": body,
            "doc": doc,
            "tip": body.Tip,
            "live": len(body.Group) <= LIVE_PREVIEW_MAX,
        }
        self._suspend = True
        doc.openTransaction("Roll history tip")

    def drag_is_live(self):
        """True while a marker drag rolls Body.Tip as it moves (small bodies)."""
        return bool(self._drag and self._drag["live"])

    def _apply_tip(self, name):
        """Set the body's tip to feature ``name`` (None clears), show it, recompute."""
        body = self._drag["body"]
        doc = self._drag["doc"]
        feature = None if name is None else doc.getObject(name)
        if feature is not None:
            ok, reason = model.can_be_tip(body, feature)
            if not ok:
                FreeCAD.Console.PrintWarning(reason + "\n")
                return
        if body.Tip == feature:
            return
        model.set_tip(body, feature)
        if FreeCAD.GuiUp:
            _show_only(body, feature)
        doc.recompute()

    def _on_drag_preview(self, name):
        if self._drag is None or not self._drag["live"]:
            return
        try:
            self._apply_tip(name)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"History timeline: preview failed: {exc}\n")

    def _end_drag(self, keep):
        drag = self._drag
        self._drag = None
        self._suspend = False
        if drag is None:
            return
        doc = drag["doc"]
        body = drag["body"]
        if keep and body.Tip != drag["tip"]:
            doc.commitTransaction()
        else:
            # Nothing changed, or the drag was cancelled: no undo step. Aborting
            # puts the tip and the visibilities back.
            doc.abortTransaction()
            doc.recompute()
        self.refresh()

    def _on_drag_finished(self, name):
        if self._drag is None:
            return
        try:
            self._apply_tip(name)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"History timeline: rollback failed: {exc}\n")
            self._end_drag(keep=False)
            return
        self._end_drag(keep=True)

    def _on_drag_cancelled(self):
        self._end_drag(keep=False)

    def _edit(self, name):
        _body, feature = self._feature(name)
        if feature is None or not FreeCAD.GuiUp:
            return
        import FreeCADGui

        try:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(feature)
            FreeCADGui.ActiveDocument.setEdit(feature)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"History timeline: cannot edit {name}: {exc}\n")

    def _begin_rename(self, name):
        _body, feature = self._feature(name)
        if feature is None:
            return
        self._bar.begin_rename(name, feature.Label)

    def _rename(self, name, label):
        _body, feature = self._feature(name)
        if feature is None or not label or label == feature.Label:
            return

        def apply(_doc, _body):
            feature.Label = label

        self._transaction("Rename feature", apply)

    def _delete(self, name):
        """Delete through Std_Delete: FreeCAD asks when other objects depend on the
        feature ("Object dependencies") and PartDesign's view providers relink the
        chain and show the base again. Headless: model.delete_feature."""
        body, feature = self._feature(name)
        if feature is None:
            return
        if FreeCAD.GuiUp:
            import FreeCADGui

            try:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(feature)
                FreeCADGui.runCommand("Std_Delete")
            except Exception as exc:
                FreeCAD.Console.PrintError(f"History timeline: cannot delete {name}: {exc}\n")
            self.schedule_refresh()
            return

        def apply(doc, _body):
            model.delete_feature(body, feature)

        self._transaction("Delete feature", apply)

    def _set_suppressed(self, feature, value):
        def apply(_doc, _body):
            model.set_suppressed(feature, value)

        title = "Suppress feature" if value else "Unsuppress feature"
        self._transaction(title, apply)

    def _popup(self, name, global_pos):
        body, feature = self._feature(name)
        if feature is None:
            return
        menu = self.build_menu(name, body, feature)
        if hasattr(menu, "exec"):
            menu.exec(global_pos)
        else:
            menu.exec_(global_pos)

    def build_menu(self, name, body, feature):
        """Fusion's chip menu: Edit, Rename, Suppress, Roll to here, Delete, Move earlier/later.
        Returned unshown so tests can read the actions (``menu.actions()``)."""
        menu = QtWidgets.QMenu(self)
        menu.setObjectName("HistoryTimelineMenu")
        edit_action = menu.addAction("Edit feature")
        edit_action.setObjectName("edit")
        edit_action.triggered.connect(lambda _checked=False, n=name: self._edit(n))

        rename_action = menu.addAction("Rename")
        rename_action.setObjectName("rename")
        rename_action.triggered.connect(lambda _checked=False, n=name: self._begin_rename(n))

        if hasattr(feature, "Suppressed"):
            suppressed = model.is_suppressed(feature)
            label = "Unsuppress feature" if suppressed else "Suppress feature"
            suppress_action = menu.addAction(label)
            suppress_action.triggered.connect(
                lambda _checked=False, feat=feature, flag=not suppressed: self._set_suppressed(
                    feat, flag
                )
            )
        else:
            suppress_action = menu.addAction("Suppress feature")
            suppress_action.setEnabled(False)
            suppress_action.setToolTip("This object has no Suppressed property.")
        suppress_action.setObjectName("suppress")

        roll_action = menu.addAction("Roll history to here")
        roll_action.setObjectName("roll")
        ok, reason = model.can_be_tip(body, feature)
        roll_action.setEnabled(ok)
        if not ok:
            roll_action.setToolTip(reason)
        roll_action.triggered.connect(lambda _checked=False, n=name: self._roll_to(n))

        delete_action = menu.addAction("Delete")
        delete_action.setObjectName("delete")
        delete_action.triggered.connect(lambda _checked=False, n=name: self._delete(n))

        menu.addSeparator()
        earlier = menu.addAction("Move earlier")
        earlier.setObjectName("earlier")
        later = menu.addAction("Move later")
        later.setObjectName("later")
        for action in (earlier, later):
            action.setEnabled(False)
            action.setToolTip(
                "Not in this spike. Assigning Body.Group does not rewrite BaseFeature."
            )
        return menu

    def closeEvent(self, event):
        # Closing the dock is the disable gesture. Preference goes back to false.
        set_enabled(False, hide_widget=False)
        super().closeEvent(event)


class _ToggleCommand:
    def GetResources(self):
        return {
            "CmdType": "ForEdit",
            "MenuText": "Toggle history timeline",
            "ToolTip": (
                "Show or hide the history timeline (PartDesign history, assembly order). "
                "On by default. Preference: Mod/HistoryTimeline/Enabled."
            ),
        }

    def Activated(self, *_args):
        set_enabled(not is_enabled())

    def IsActive(self):
        return True


def _ensure_dock():
    import FreeCADGui

    dock = _state["dock"]
    if dock is not None:
        return dock
    window = FreeCADGui.getMainWindow()
    dock = HistoryTimelineDock(window)
    window.addDockWidget(_bottom_area(), dock)
    _state["dock"] = dock
    return dock


def _ensure_observer(dock):
    if _state["observer"] is None:
        observer = _DocumentObserver(dock)
        FreeCAD.addDocumentObserver(observer)
        _state["observer"] = observer


def _drop_observer():
    observer = _state["observer"]
    if observer is not None:
        try:
            FreeCAD.removeDocumentObserver(observer)
        except (AttributeError, RuntimeError, TypeError):
            pass
        _state["observer"] = None


def set_enabled(enabled: bool, hide_widget: bool = True):
    """Persist the preference and show or hide the dock."""
    _state["syncing"] = True
    try:
        prefs().SetBool("Enabled", bool(enabled))
    finally:
        _state["syncing"] = False
    action = _state["action"]
    if action is not None:
        action.blockSignals(True)
        action.setChecked(bool(enabled))
        action.blockSignals(False)
    if not FreeCAD.GuiUp:
        return
    if enabled:
        dock = _ensure_dock()
        _ensure_observer(dock)
        dock.set_live(True)
        dock.show()
        dock.raise_()
        # Hides the dock again when there is no history to show.
        dock.refresh()
    else:
        _drop_observer()
        dock = _state["dock"]
        if dock is not None:
            dock.set_live(False)
        if hide_widget and dock is not None:
            dock.hide()


class _PrefObserver:
    """Follows ``Enabled`` when it is changed elsewhere (the preference page)."""

    def onChange(self, _group, name):
        if name == "HideWhenEmpty":
            dock = _state["dock"]
            if dock is not None:
                QtCore.QTimer.singleShot(0, dock.refresh)
            return
        if name != "Enabled" or _state["syncing"]:
            return
        QtCore.QTimer.singleShot(0, _follow_pref)


def _follow_pref():
    enabled = is_enabled()
    # The dock may be hidden for lack of history while enabled, so compare
    # with the observer, which is attached exactly while enabled.
    live = _state["observer"] is not None
    if enabled != live:
        set_enabled(enabled)


def _menu_fallback(window):
    """Same object name and re-attach rule as BruceWorx's bruceworx_menu, for
    running this add-on on a FreeCAD without the BruceWorx module."""
    menu = window.findChild(QtWidgets.QMenu, "BruceWorxMenu")
    if menu is None:
        menu = QtWidgets.QMenu("BruceWorx", window)
        menu.setObjectName("BruceWorxMenu")

    def attach(*_args):
        bar = window.menuBar()
        if menu.menuAction() not in bar.actions():
            bar.addMenu(menu)

    try:
        window.workbenchActivated.connect(lambda _name: QtCore.QTimer.singleShot(0, attach))
    except Exception:
        pass
    attach()
    return menu


def install():
    """Register the menu command and show the dock unless the preference is off."""
    if _state["installed"]:
        return
    if not FreeCAD.GuiUp:
        return
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND_NAME, _ToggleCommand())

    window = FreeCADGui.getMainWindow()
    action = QtGui.QAction("History Timeline", window)
    action.setObjectName("HistoryTimelineAction")
    action.setCheckable(True)
    action.setChecked(is_enabled())
    action.setToolTip(
        "Bottom history bar: PartDesign features, or assembly components and joints. "
        "On by default. Hidden while there is no history. "
        "Uncheck, close the dock, or turn it off on Preferences → BruceWorx."
    )
    action.toggled.connect(set_enabled)
    try:
        # The menu bar is rebuilt on every workbench switch; bruceworx_menu
        # keeps the BruceWorx menu on it.
        import bruceworx_menu

        bruceworx_menu.add_action(action)
    except ImportError:
        _menu_fallback(window).addAction(action)
    _state["action"] = action

    if _state["pref_observer"] is None:
        observer = _PrefObserver()
        # Keep the group handle: the Python wrapper returned by ParamGet
        # detaches its observers when it is garbage collected.
        group = prefs()
        group.Attach(observer)
        _state["pref_observer"] = observer
        _state["pref_group"] = group

    if is_enabled():
        set_enabled(True)
    _state["installed"] = True
