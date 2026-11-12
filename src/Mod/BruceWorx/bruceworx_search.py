# SPDX-License-Identifier: LGPL-2.1-or-later
"""Search: one field on the first toolbar row, focused from anywhere with Cmd-K.

Roadmap §13.4, step C3 (UIX-06). Typing lists three kinds of hit:

* Commands, with the place they live in the workspaces (Design › Solid ›
  Create, from workspaces.json) or their shortcut. Active commands come first.
  Return runs the command; Cmd-Return shows where it lives: it opens the
  workspace and tab and flashes the button (or the group's caption, for a
  command that is only in the caption menu).
* Objects in the active document (label, Label2, type). Return selects one.
* Settings: the labels of the preference pages, from settings_index.json
  (make_settings_index.py builds it from the .ui files). Return opens
  Preferences with the text in its own search box.

Ranking: the whole text, then a word start, then anywhere, then the letters
in order; commands used recently get a lift. Active commands come before
inactive ones among the good matches. The SearchBar add-on's toolbar is
hidden while the field is on (its pointer bar is left alone).

Preferences/BruceWorx/CommandSearch (default on).
"""

from __future__ import annotations

import json
import os
import re

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "CommandSearch"
RECENT_PATH = PREFS_PATH + "/Search"
BAR_NAME = "BruceWorxSearchBar"
FIELD_NAME = "BruceWorxSearch"
SHORTCUT = "Ctrl+K"  # Cmd-K on the Mac
ADDON_TOOLBAR = "SearchBar"
SECTIONS = ("Commands", "In this document", "Settings")
LIMITS = {"Commands": 8, "In this document": 6, "Settings": 6}
RECENT_MAX = 20
DETAIL_ROLE = 0x0100 + 1  # Qt.UserRole + 1
KEY_ROLE = 0x0100 + 2

# --- ranking -----------------------------------------------------------------


def _words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def score(query, text):
    """How well text answers query (higher is better), or 0 for no match."""
    q = query.strip().lower()
    t = (text or "").lower()
    if not q or not t:
        return 0
    if t == q:
        return 100
    if t.startswith(q):
        return 90
    words = _words(t)
    tokens = _words(q)
    if any(w.startswith(q) for w in words):
        return 80
    if tokens and all(any(w.startswith(tok) for w in words) for tok in tokens):
        return 70
    if q in t:
        return 60
    if tokens and all(tok in t for tok in tokens):
        return 50
    # The letters in order ("pd" -> "Pad", "cpat" -> "Circular Pattern").
    position = 0
    for char in q.replace(" ", ""):
        position = t.find(char, position) + 1
        if position == 0:
            return 0
    return 20


def best(query, primary, secondary=()):
    """Score over a primary text and weaker extra texts (tooltip, type, name).

    Extra texts count half, and only for real word or substring hits: letters in
    order over a long tooltip match almost anything.
    """
    found = score(query, primary)
    for extra in secondary:
        hit = score(query, extra)
        if hit > 20:
            found = max(found, hit // 2)
    return found


def rank(query, items, recent=()):
    """items: (key, primary, [secondary]) tuples; returns [(score, key)] best first."""
    out = []
    recent = list(recent)
    for key, primary, secondary in items:
        found = best(query, primary, secondary)
        if not found:
            continue
        if key in recent:
            found += 8 - min(7, recent.index(key))
        out.append((found, len(primary or ""), key))
    out.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(s, key) for s, _length, key in out]


def trim_weak(ranked, strong=50, enough=3):
    """Drop weak hits (letters in order, tooltip words) when there are enough strong ones."""
    good = [hit for hit in ranked if hit[0] >= strong]
    return good if len(good) >= enough else ranked


def push_recent(recent, key, limit=RECENT_MAX):
    """recent with key moved to the front."""
    return [key] + [k for k in recent if k != key][: limit - 1]


def place_text(place):
    return " › ".join(place) if place else ""


def load_settings(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_index.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


# --- GUI ---------------------------------------------------------------------

_classes = None
_field = None
_bar = None
_shortcut = None
_observer = None
_groups = []


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _recent():
    import FreeCAD

    text = FreeCAD.ParamGet(RECENT_PATH).GetString("Recent", "")
    return [k for k in text.split("\n") if k]


def _remember(key):
    import FreeCAD

    FreeCAD.ParamGet(RECENT_PATH).SetString("Recent", "\n".join(push_recent(_recent(), key)))


class Index:
    """Commands and settings, loaded once; commands again when their number changes."""

    def __init__(self):
        self.commands = {}
        self.icons = {}
        self.settings = load_settings()
        self._count = -1

    def refresh_commands(self):
        import FreeCADGui

        names = FreeCADGui.listCommands()
        if len(names) == self._count:
            return
        self._count = len(names)
        try:
            import bruceworx_workspaces

            defn = bruceworx_workspaces.definition()
            place_of = lambda n: bruceworx_workspaces.place_of(defn, n)  # noqa: E731
        except Exception:
            place_of = lambda n: None  # noqa: E731
        for name in names:
            if name in self.commands:
                continue
            command = FreeCADGui.Command.get(name)
            if command is None:
                continue
            try:
                info = command.getInfo()
            except Exception:
                continue
            text = (info.get("menuText") or "").replace("&", "").strip().rstrip(".…").strip()
            if not text:
                continue
            self.commands[name] = {
                "text": text,
                "tip": re.sub(r"<[^>]+>", " ", info.get("toolTip") or ""),
                "pixmap": info.get("pixmap") or "",
                "shortcut": info.get("shortcut") or "",
                "place": place_of(name),
            }

    def icon(self, pixmap):
        import FreeCADGui
        from PySide import QtGui

        if pixmap not in self.icons:
            found = FreeCADGui.getIcon(pixmap) if pixmap else None
            self.icons[pixmap] = found or QtGui.QIcon()
        return self.icons[pixmap]


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    class Delegate(QtWidgets.QStyledItemDelegate):
        """Draws the detail (place, shortcut, page) right-aligned in the secondary colour."""

        def paint(self, painter, option, index):
            detail = index.data(DETAIL_ROLE)
            super().paint(painter, option, index)
            if not detail or not index.flags() & QtCore.Qt.ItemIsSelectable:
                return
            painter.save()
            color = option.palette.color(QtGui.QPalette.PlaceholderText)
            painter.setPen(color)
            font = QtGui.QFont(option.font)
            font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
            painter.setFont(font)
            rect = option.rect.adjusted(0, 0, -12, 0)
            metrics = QtGui.QFontMetrics(font)
            text = metrics.elidedText(detail, QtCore.Qt.ElideLeft, rect.width() // 2)
            painter.drawText(rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, text)
            painter.restore()

    class Results(QtWidgets.QFrame):
        """The pop-up list under the field, a child of the main window."""

        def __init__(self, field, mw):
            super().__init__(mw)
            self.field = field
            self.setObjectName("BruceWorxSearchResults")
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)
            self.list = QtWidgets.QListWidget(self)
            self.list.setObjectName("BruceWorxSearchList")
            self.list.setItemDelegate(Delegate(self.list))
            self.list.setFocusPolicy(QtCore.Qt.NoFocus)
            self.list.setIconSize(QtCore.QSize(18, 18))
            self.list.setUniformItemSizes(False)
            self.list.itemClicked.connect(lambda item: self.field.activate(item))
            layout.addWidget(self.list)
            self.hide()

        def place(self):
            mw = self.parentWidget()
            corner = self.field.mapTo(mw, QtCore.QPoint(0, self.field.height() + 4))
            width = max(self.field.width(), 480)
            x = min(corner.x(), mw.width() - width - 8)
            rows = self.list.count()
            height = min(440, 14 + sum(self.list.sizeHintForRow(r) for r in range(rows)))
            self.setGeometry(max(8, x), corner.y(), width, max(40, height))
            self.raise_()

    class Field(QtWidgets.QLineEdit):
        def __init__(self, parent, mw):
            super().__init__(parent)
            self.setObjectName(FIELD_NAME)
            self.setPlaceholderText("Search commands, settings, Browser")
            self.setClearButtonEnabled(True)
            self.setMinimumWidth(260)
            self.setMaximumWidth(380)
            self.hint = QtWidgets.QLabel("⌘K", self)
            self.hint.setObjectName("BruceWorxSearchHint")
            self.index = Index()
            self.results = Results(self, mw)
            self.textChanged.connect(self.search)
            self.textChanged.connect(lambda text: self.hint.setVisible(not text and not self.hasFocus()))
            self._timer = QtCore.QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.hide_results)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            hint = self.hint.sizeHint()
            self.hint.setGeometry(self.width() - hint.width() - 30, (self.height() - hint.height()) // 2, hint.width(), hint.height())

        def focusInEvent(self, event):
            super().focusInEvent(event)
            self._timer.stop()
            self.hint.hide()
            if self.text():
                self.search(self.text())

        def focusOutEvent(self, event):
            super().focusOutEvent(event)
            self.hint.setVisible(not self.text())
            # A click in the list takes focus away first; give it time to land.
            self._timer.start(200)

        def hide_results(self):
            if _alive(self.results):
                self.results.hide()

        # --- searching
        def search(self, text):
            results = self.results
            results.list.clear()
            if not text.strip():
                results.hide()
                return
            sections = self.find(text)
            for title in SECTIONS:
                hits = sections.get(title, [])
                if not hits:
                    continue
                header = QtWidgets.QListWidgetItem(title.upper())
                header.setFlags(QtCore.Qt.NoItemFlags)
                font = header.font()
                font.setBold(True)
                font.setPointSizeF(max(8.0, font.pointSizeF() - 2))
                header.setFont(font)
                results.list.addItem(header)
                for label, detail, icon, key, active in hits:
                    item = QtWidgets.QListWidgetItem(icon, label)
                    item.setData(DETAIL_ROLE, detail)
                    item.setData(KEY_ROLE, key)
                    if not active:
                        item.setForeground(results.list.palette().color(QtGui.QPalette.PlaceholderText))
                    results.list.addItem(item)
            if results.list.count() == 0:
                empty = QtWidgets.QListWidgetItem("No results")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                results.list.addItem(empty)
            self.move_selection(1, start=-1)
            results.place()
            results.show()

        def find(self, text):
            index = self.index
            index.refresh_commands()
            recent = _recent()
            out = {}
            # Commands: active ones first, then the rest, each in rank order.
            ranked = rank(
                text,
                (
                    ("cmd:" + name, c["text"], (name, c["tip"]))
                    for name, c in index.commands.items()
                ),
                recent,
            )
            ranked = trim_weak(ranked)[:40]
            hits = []
            for _score, key in ranked:
                name = key[4:]
                active = _enabled_now(name)
                info = index.commands[name]
                detail = place_text(info["place"]) or info["shortcut"]
                hits.append((info["text"], detail, index.icon(info["pixmap"]), key, active))
            # Good matches first, active before inactive within each; weak ones last.
            scores = dict((key, s) for s, key in ranked)
            hits.sort(key=lambda h: (scores[h[3]] < 50, not h[4], -scores[h[3]]))
            out["Commands"] = hits[: LIMITS["Commands"]]
            # Objects in the active document.
            doc = FreeCAD.ActiveDocument
            if doc is not None:
                objects = {o.Name: o for o in doc.Objects[:5000]}
                ranked = rank(
                    text,
                    (("obj:" + o.Name, o.Label, (getattr(o, "Label2", ""), o.TypeId.split("::")[-1])) for o in objects.values()),
                )
                hits = []
                for _score, key in ranked[: LIMITS["In this document"]]:
                    obj = objects[key[4:]]
                    icon = obj.ViewObject.Icon if getattr(obj, "ViewObject", None) else QtGui.QIcon()
                    hits.append((obj.Label, obj.TypeId.split("::")[-1], icon, key, True))
                out["In this document"] = hits
            # Settings.
            settings = index.settings
            # Over a thousand labels: one good hit is enough to drop the weak ones.
            ranked = trim_weak(rank(text, (("set:%d" % i, s["text"], (s["tip"],)) for i, s in enumerate(settings)), recent), enough=1)
            hits = []
            gear = FreeCADGui.getIcon("preferences-system") or QtGui.QIcon()
            for _score, key in ranked[: LIMITS["Settings"]]:
                entry = settings[int(key[4:])]
                hits.append((entry["text"], f"{entry['area']} › {entry['page']}", gear, key, True))
            out["Settings"] = hits
            return out

        # --- keys
        def keyPressEvent(self, event):
            key = event.key()
            if key in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                self.move_selection(1 if key == QtCore.Qt.Key_Down else -1)
                return
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                item = self.results.list.currentItem()
                if item is not None and self.results.isVisible():
                    self.activate(item, reveal=bool(event.modifiers() & QtCore.Qt.ControlModifier))
                return
            if key == QtCore.Qt.Key_Escape:
                self.clear()
                self.results.hide()
                FreeCADGui.getMainWindow().setFocus()
                return
            super().keyPressEvent(event)

        def move_selection(self, step, start=None):
            lst = self.results.list
            row = lst.currentRow() if start is None else start
            for _ in range(lst.count()):
                row += step
                if row < 0 or row >= lst.count():
                    return
                if lst.item(row).flags() & QtCore.Qt.ItemIsSelectable:
                    lst.setCurrentRow(row)
                    return

        # --- results
        def activate(self, item, reveal=False):
            key = item.data(KEY_ROLE)
            if not key:
                return
            self.results.hide()
            self.clear()
            FreeCADGui.getMainWindow().setFocus()
            kind, value = key[:3], key[4:]
            if kind == "cmd":
                _remember(key)
                if reveal:
                    reveal_command(value)
                else:
                    FreeCADGui.runCommand(value)
            elif kind == "obj":
                doc = FreeCAD.ActiveDocument
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(doc.Name, value)
            elif kind == "set":
                _remember(key)
                entry = self.index.settings[int(value)]
                open_preferences(entry["text"])

    _classes = (Field,)
    return _classes


def _enabled_now(name):
    """Whether a command can run now, from its QAction (FreeCAD keeps that current).

    Command.isActive() is not used: some commands crash when asked outside the
    context they expect, and FreeCAD itself only asks commands that have an action.
    """
    import FreeCADGui

    command = FreeCADGui.Command.get(name)
    actions = command.getAction() if command else []
    return actions[0].isEnabled() if actions else True


def open_preferences(text):
    """Open Preferences with text in its own search box."""
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    def fill(tries=20):
        dialog = QtWidgets.QApplication.activeModalWidget()
        box = dialog.findChild(QtWidgets.QLineEdit, "searchBox") if dialog else None
        if box is None:
            if tries:
                QtCore.QTimer.singleShot(100, lambda: fill(tries - 1))
            return
        box.setFocus()
        box.setText(text)

    QtCore.QTimer.singleShot(100, fill)
    FreeCADGui.showPreferences()


def reveal_command(name):
    """Open the workspace and tab where a command lives and flash its button."""
    from PySide import QtCore

    try:
        import bruceworx_workspaces as ws
    except Exception:
        return
    place = ws.place_of(ws.definition(), name)
    if place is None:
        return
    workspace, tab, caption = place
    ws.activate_workspace(workspace)
    QtCore.QTimer.singleShot(400, lambda: _show_and_flash(ws, tab, caption, name))


def _show_and_flash(ws, tab, caption, name):
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    bar = ws.bar()
    if bar is not None:
        bar.select_tab(tab)
    mw = FreeCADGui.getMainWindow()
    toolbar = mw.findChild(QtWidgets.QToolBar, ws.toolbar_name(tab, caption))
    if toolbar is None:
        return
    target = None
    command = FreeCADGui.Command.get(name)
    for action in (command.getAction() if command else []):
        widget = toolbar.widgetForAction(action)
        if widget is not None:
            target = widget
            break
    if target is None:
        # Only in the caption menu: flash the caption.
        target = toolbar.findChild(QtWidgets.QToolButton, "BruceWorxGroupCaption")
    if target is None:
        return

    def set_flash(on):
        if _alive(target):
            target.setProperty("bwFlash", on)
            target.style().unpolish(target)
            target.style().polish(target)

    for step in range(6):
        QtCore.QTimer.singleShot(step * 220, lambda on=(step % 2 == 0): set_flash(on))
    QtCore.QTimer.singleShot(6 * 220, lambda: set_flash(False))


def _hide_addon_bar():
    import FreeCADGui
    from PySide import QtWidgets

    addon = FreeCADGui.getMainWindow().findChild(QtWidgets.QToolBar, ADDON_TOOLBAR)
    if addon is not None:
        addon.setVisible(not enabled())


def _after_switch(*_args):
    from PySide import QtCore

    # The add-on shows its bar again on every switch; hide it after it did.
    QtCore.QTimer.singleShot(0, _hide_addon_bar)


def field():
    """The live search field (for tests and scripts)."""
    return _field if _alive(_field) else None


def field_moved():
    """The title row (bruceworx_titlebar) took the field: hide the empty search bar."""
    if _alive(_bar):
        _bar.hide()


def take_field_back():
    """Put the field back in the search bar (the title row was turned off)."""
    if _alive(_bar) and _alive(_field):
        _bar.addWidget(_field)
        _field.show()
        _bar.setVisible(enabled())


def focus():
    if _alive(_field) and _field.isVisible():
        _field.setFocus()
        _field.selectAll()


def _apply():
    global _bar, _field, _shortcut
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    mw = FreeCADGui.getMainWindow()
    if enabled():
        if _bar is None or not _alive(_bar):
            (field_class,) = _make_classes()
            _bar = QtWidgets.QToolBar(mw)
            _bar.setObjectName(BAR_NAME)
            _bar.setWindowTitle("Search")
            _bar.setMovable(False)
            # Hidden toggle: ToolBarManager then leaves the bar alone on every switch.
            _bar.toggleViewAction().setVisible(False)
            _field = field_class(_bar, mw)
            _bar.addWidget(_field)
            mw.addToolBar(QtCore.Qt.TopToolBarArea, _bar)
        if _shortcut is None:
            _shortcut = QtGui.QShortcut(QtGui.QKeySequence(SHORTCUT), mw)
            _shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            _shortcut.activated.connect(focus)
        # The field may live in the title row (bruceworx_titlebar); the bar shows only with it.
        _field.show()
        _bar.setVisible(_field.parentWidget() is _bar)
    else:
        if _alive(_bar):
            _bar.hide()
        if _alive(_field):
            _field.hide()
    _hide_addon_bar()
    try:
        import bruceworx_toolbars

        bruceworx_toolbars.relayout()
    except Exception:
        pass


class _PrefObserver:
    def onChange(self, _group, name):
        if name == PREF_NAME:
            _apply()


def install():
    """Add the field to the first row and bind Cmd-K."""
    global _observer
    if _observer is not None:
        return
    import FreeCAD
    import FreeCADGui

    _apply()
    FreeCADGui.getMainWindow().workbenchActivated.connect(_after_switch)
    _observer = _PrefObserver()
    group = FreeCAD.ParamGet(PREFS_PATH)
    # Keep the handle: a collected wrapper detaches its observer.
    group.Attach(_observer)
    _groups.append(group)
