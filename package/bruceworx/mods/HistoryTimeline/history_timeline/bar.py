# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bottom history bar. Draws glyphs itself so the mock does not need FreeCAD icons.

Entries are duck-typed: ``name``, ``label``, ``kind``, ``is_tip``, ``after_tip``,
``suppressed``, and optional ``icon`` (QIcon). ``type_id`` and ``base_name`` are
optional tooltip fields; an entry's own ``tooltip`` replaces them.
"""

import os

from history_timeline.qtcompat import load_qt, qt_enum

QtCore, QtGui, QtWidgets = load_qt()

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
PLAYBACK = (
    ("start", "To start: roll everything back (Body.Tip cleared)"),
    ("back", "Back one feature"),
    ("forward", "Forward one feature"),
    ("end", "To end: the last feature is the tip"),
)

_BG = "#323232"
_SLASH = "#ff5d5d"

# One row of 28 px icon chips, as in the roadmap §13.3 mock.
RIBBON_HEIGHT = 36
CHIP_WIDTH = 28
CHIP_ICON = 20


def _palette_colors(widget):
    """Chip colours from the widget's palette, so the bar follows the theme.

    The BruceWorx stylesheet sets them on the chips (``[historyChip="true"]``):
    ``background-color`` is the chip, ``alternate-background-color`` its border, ``selection-background-color``
    the tip chip and ``selection-color`` the accent (tip border and marker).
    Without that sheet the platform palette gives a plain light bar.
    """
    pal = widget.palette()

    def role(name):
        return pal.color(qt_enum(QtGui.QPalette, ("ColorRole", name), (name,)))

    return {
        "fill": role("Window"),
        "border": role("AlternateBase"),
        "tip_fill": role("Highlight"),
        "accent": role("HighlightedText"),
    }


def _align_center():
    return qt_enum(QtCore.Qt, ("AlignmentFlag", "AlignCenter"), ("AlignCenter",))


def _align_vcenter():
    return qt_enum(QtCore.Qt, ("AlignmentFlag", "AlignVCenter"), ("AlignVCenter",))


def _align_left():
    return qt_enum(QtCore.Qt, ("AlignmentFlag", "AlignLeft"), ("AlignLeft",))


def _pointing_hand():
    return qt_enum(QtCore.Qt, ("CursorShape", "PointingHandCursor"), ("PointingHandCursor",))


def _antialias():
    return qt_enum(QtGui.QPainter, ("RenderHint", "Antialiasing"), ("Antialiasing",))


def _no_pen():
    style = qt_enum(QtCore.Qt, ("PenStyle", "NoPen"), ("NoPen",))
    return QtGui.QPen(style)


def _scroll_off():
    return qt_enum(
        QtCore.Qt, ("ScrollBarPolicy", "ScrollBarAlwaysOff"), ("ScrollBarAlwaysOff",)
    )


def _scroll_as_needed():
    return qt_enum(
        QtCore.Qt, ("ScrollBarPolicy", "ScrollBarAsNeeded"), ("ScrollBarAsNeeded",)
    )


def _styled_bg():
    return qt_enum(
        QtCore.Qt, ("WidgetAttribute", "WA_StyledBackground"), ("WA_StyledBackground",)
    )


def glyph(kind: str, size: int = 32) -> QtGui.QPixmap:
    """Small geometric stand-in for a PartDesign command icon."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(_antialias(), True)
    pen = QtGui.QPen(QtGui.QColor("#f4f4f4"))
    pen.setWidthF(1.6)
    painter.setPen(pen)
    inset = 4
    rect = QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    no_brush = qt_enum(QtCore.Qt, ("BrushStyle", "NoBrush"), ("NoBrush",))

    if kind == "sketch":
        painter.setBrush(QtGui.QColor("#5dade2"))
        painter.drawRoundedRect(rect.adjusted(2, 4, -2, -6), 2, 2)
        painter.drawLine(QtCore.QLineF(8, size - 8, size - 8, 10))
    elif kind == "datum":
        painter.setBrush(no_brush)
        pen.setStyle(qt_enum(QtCore.Qt, ("PenStyle", "DashLine"), ("DashLine",)))
        pen.setColor(QtGui.QColor("#f5c16c"))
        painter.setPen(pen)
        painter.drawRect(rect)
    elif kind == "pad":
        painter.setBrush(QtGui.QColor("#f6d365"))
        painter.drawRect(rect.adjusted(2, 8, -6, -2))
        painter.drawPolygon(
            [
                QtCore.QPointF(rect.left() + 2, rect.top() + 8),
                QtCore.QPointF(rect.left() + 8, rect.top() + 2),
                QtCore.QPointF(rect.right() - 0, rect.top() + 2),
                QtCore.QPointF(rect.right() - 6, rect.top() + 8),
            ]
        )
    elif kind == "pocket":
        painter.setBrush(QtGui.QColor("#7dcea0"))
        painter.drawRect(rect.adjusted(1, 6, -1, -2))
        painter.setBrush(QtGui.QColor(_BG))
        painter.drawEllipse(rect.adjusted(8, 10, -8, -6))
    elif kind == "fillet":
        painter.setBrush(QtGui.QColor("#f0b27a"))
        path = QtGui.QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + 10)
        path.quadTo(rect.left(), rect.top(), rect.left() + 10, rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "chamfer":
        painter.setBrush(QtGui.QColor("#d7bde2"))
        path = QtGui.QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + 12)
        path.lineTo(rect.left() + 12, rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "cylinder":
        painter.setBrush(QtGui.QColor("#85c1e9"))
        painter.drawRoundedRect(rect.adjusted(6, 4, -6, -4), 8, 8)
    elif kind == "sphere":
        painter.setBrush(QtGui.QColor("#f1948a"))
        painter.drawEllipse(rect.adjusted(2, 2, -2, -2))
    elif kind == "box":
        painter.setBrush(QtGui.QColor("#f7dc6f"))
        painter.drawRect(rect.adjusted(3, 6, -3, -3))
    elif kind == "vars":
        painter.setBrush(QtGui.QColor("#aed6f1"))
        painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 3, 3)
        painter.setPen(QtGui.QPen(QtGui.QColor("#1a1a1a")))
        painter.drawText(rect, _align_center(), "x")
    else:
        painter.setBrush(QtGui.QColor("#d5d8dc"))
        painter.drawEllipse(rect.adjusted(3, 3, -3, -3))

    painter.end()
    return pixmap


class FeatureButton(QtWidgets.QWidget):
    """One feature cell. Click, double-click, and context menu are separate signals."""

    clicked = QtCore.Signal()
    doubleClicked = QtCore.Signal()
    contextMenu = QtCore.Signal(object)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName(f"HistoryTimeline_{entry.name}")
        self.setProperty("historyChip", True)
        self.setFixedSize(CHIP_WIDTH, RIBBON_HEIGHT - 10)
        self.setCursor(_pointing_hand())
        self.setMouseTracking(True)
        self._suppress_release = False
        type_id = getattr(entry, "type_id", entry.kind)
        base = getattr(entry, "base_name", None)
        state = []
        if entry.is_tip:
            state.append("tip")
        if entry.after_tip:
            state.append("after tip")
        if entry.suppressed:
            state.append("suppressed")
        state_text = ", ".join(state) if state else "in history"
        tooltip = getattr(entry, "tooltip", None)
        self.setToolTip(
            tooltip or f"{entry.label}\n{type_id}\nbase: {base or '—'}\n{state_text}"
        )
        icon = getattr(entry, "icon", None)
        if icon is not None:
            self._pixmap = icon.pixmap(QtCore.QSize(CHIP_ICON, CHIP_ICON))
        else:
            self._pixmap = glyph(entry.kind, CHIP_ICON)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(_antialias(), True)
        if self.entry.after_tip:
            painter.setOpacity(0.42)
        colors = _palette_colors(self)
        if self.entry.is_tip:
            painter.setPen(QtGui.QPen(colors["accent"], 1))
            painter.setBrush(colors["tip_fill"])
        else:
            painter.setPen(QtGui.QPen(colors["border"], 1))
            painter.setBrush(colors["fill"])
        painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        size = self._pixmap.deviceIndependentSize().toSize() if hasattr(
            self._pixmap, "deviceIndependentSize") else self._pixmap.size()
        painter.drawPixmap(
            (self.width() - size.width()) // 2, (self.height() - size.height()) // 2, self._pixmap
        )
        if self.entry.suppressed:
            painter.setOpacity(1.0)
            painter.setPen(QtGui.QPen(QtGui.QColor(_SLASH), 2))
            painter.drawLine(8, self.height() - 6, self.width() - 8, 6)
        if self.property("traced"):
            # [BW] The feature that made the face picked in the 3D view (bruceworx_picktrace).
            painter.setOpacity(1.0)
            painter.setPen(QtGui.QPen(colors["accent"], 2))
            painter.setBrush(qt_enum(QtCore.Qt, ("BrushStyle", "NoBrush"), ("NoBrush",)))
            painter.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        painter.end()

    def mouseReleaseEvent(self, event):
        left = qt_enum(QtCore.Qt, ("MouseButton", "LeftButton"), ("LeftButton",))
        if self._suppress_release:
            self._suppress_release = False
            event.accept()
            return
        if event.button() == left:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        left = qt_enum(QtCore.Qt, ("MouseButton", "LeftButton"), ("LeftButton",))
        if event.button() == left:
            # The second release of a double-click would otherwise roll the tip.
            self._suppress_release = True
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.contextMenu.emit(event.globalPos())
        event.accept()


class MarkerWidget(QtWidgets.QWidget):
    """Rollback marker. It sits after the tip feature (or at the front if the tip is unset).

    Dragging it rolls the history: ``dragStarted`` once on press, ``dragMoved(x)``
    with the pointer's x in the row's coordinates as it moves, ``dragFinished``
    on release and ``dragCancelled`` on Escape. The bar turns x into a tip.
    """

    dragStarted = QtCore.Signal()
    dragMoved = QtCore.Signal(int)
    dragFinished = QtCore.Signal()
    dragCancelled = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HistoryTimelineMarker")
        self.setProperty("historyChip", True)
        self.setFixedWidth(10)
        self.setCursor(
            qt_enum(QtCore.Qt, ("CursorShape", "SizeHorCursor"), ("SizeHorCursor",))
        )
        self.setFocusPolicy(qt_enum(QtCore.Qt, ("FocusPolicy", "StrongFocus"), ("StrongFocus",)))
        self.setToolTip(
            "History marker. Drag it to roll the body back or forward (Body.Tip). "
            "Features to the right are rolled back; they stay in the model."
        )
        self._dragging = False
        self._row = None  # widget whose x coordinates dragMoved reports

    def set_row(self, row):
        self._row = row

    def is_dragging(self):
        return self._dragging

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(_antialias(), True)
        accent = _palette_colors(self)["accent"]
        painter.setPen(_no_pen())
        painter.setBrush(accent)
        x = self.width() / 2.0
        w = 4 if self._dragging else 3
        painter.drawRoundedRect(QtCore.QRectF(x - w / 2.0, 4, w, self.height() - 8), 1.5, 1.5)
        # Fusion's marker has a small head; ours is a grip at the top.
        painter.drawRoundedRect(QtCore.QRectF(x - 3.5, 2, 7, 5), 2, 2)
        painter.end()

    def _row_x(self, event):
        pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
        row = self._row or self.parentWidget()
        if row is None:
            return pos.x()
        return row.mapFromGlobal(pos).x()

    def mousePressEvent(self, event):
        left = qt_enum(QtCore.Qt, ("MouseButton", "LeftButton"), ("LeftButton",))
        if event.button() == left:
            self._dragging = True
            self.grabKeyboard()
            self.update()
            self.dragStarted.emit()
            self.dragMoved.emit(self._row_x(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.dragMoved.emit(self._row_x(event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        left = qt_enum(QtCore.Qt, ("MouseButton", "LeftButton"), ("LeftButton",))
        if self._dragging and event.button() == left:
            self._end_drag()
            self.dragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        escape = qt_enum(QtCore.Qt, ("Key", "Key_Escape"), ("Key_Escape",))
        if self._dragging and event.key() == escape:
            self._end_drag()
            self.dragCancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _end_drag(self):
        self._dragging = False
        self.releaseKeyboard()
        self.update()


def is_dark_palette(widget) -> bool:
    """Dark theme: the window colour is dark. Works without FreeCAD's preferences."""
    pal = widget.palette()
    window = pal.color(qt_enum(QtGui.QPalette, ("ColorRole", "Window"), ("Window",)))
    return window.lightness() < 128


def playback_icon(name, dark) -> QtGui.QIcon:
    """``icons/timeline_<name>-<light|dark>.svg`` from the mod."""
    path = os.path.join(ICON_DIR, f"timeline_{name}-{'dark' if dark else 'light'}.svg")
    return QtGui.QIcon(path)


class PlaybackStrip(QtWidgets.QWidget):
    """Fusion's four playback buttons: to start, back one, forward one, to end.

    ``requested(action)`` carries ``"start"``, ``"back"``, ``"forward"`` or
    ``"end"``. The icons follow the palette (light or dark set).
    """

    requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HistoryTimelinePlayback")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.buttons = {}
        for name, tip in PLAYBACK:
            button = QtWidgets.QToolButton(self)
            button.setObjectName(f"HistoryTimelinePlay_{name}")
            button.setAutoRaise(True)
            button.setToolTip(tip)
            button.setFocusPolicy(qt_enum(QtCore.Qt, ("FocusPolicy", "NoFocus"), ("NoFocus",)))
            button.setIconSize(QtCore.QSize(16, 16))
            button.setFixedSize(22, RIBBON_HEIGHT - 10)
            button.clicked.connect(lambda _checked=False, n=name: self.requested.emit(n))
            layout.addWidget(button)
            self.buttons[name] = button
        self._dark = None
        self.refresh_icons()

    def refresh_icons(self):
        dark = is_dark_palette(self)
        if dark == self._dark:
            return
        self._dark = dark
        for name, button in self.buttons.items():
            button.setIcon(playback_icon(name, dark))

    def set_position(self, at_start, at_end):
        """Grey out the buttons that would not move the marker."""
        self.buttons["start"].setEnabled(not at_start)
        self.buttons["back"].setEnabled(not at_start)
        self.buttons["forward"].setEnabled(not at_end)
        self.buttons["end"].setEnabled(not at_end)

    def event(self, event):
        kind = event.type()
        if kind in (
            qt_enum(QtCore.QEvent, ("Type", "PaletteChange"), ("PaletteChange",)),
            qt_enum(QtCore.QEvent, ("Type", "StyleChange"), ("StyleChange",)),
        ):
            QtCore.QTimer.singleShot(0, self.refresh_icons)
        return super().event(event)


class TimelineBar(QtWidgets.QWidget):
    """Horizontal feature row plus the rollback marker.

    ``tipRequested`` / ``editRequested`` carry the feature's internal name.
    A single click waits briefly so a double-click does not also roll the tip.

    Marker drag: ``tipDragStarted()``, then ``tipPreviewRequested(name)`` each time
    the marker crosses a chip (``name`` is the tip the marker now sits after, or
    None at the front), then ``tipDragFinished(name)`` or ``tipDragCancelled()``.
    The bar moves the marker and dims the chips itself; the dock sets ``Body.Tip``.
    """

    tipRequested = QtCore.Signal(str)
    editRequested = QtCore.Signal(str)
    contextMenuRequested = QtCore.Signal(str, object)
    tipDragStarted = QtCore.Signal()
    tipPreviewRequested = QtCore.Signal(object)
    tipDragFinished = QtCore.Signal(object)
    tipDragCancelled = QtCore.Signal()
    playbackRequested = QtCore.Signal(str)
    renameRequested = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HistoryTimelineBar")
        self.setAttribute(_styled_bg(), True)
        self.setFixedHeight(RIBBON_HEIGHT)
        self.setToolTip("Click rolls the tip · double-click edits · right-click to suppress")

        self._title = QtWidgets.QLabel("History")
        self._title.setObjectName("HistoryTimelineTitle")
        self._hint = QtWidgets.QLabel(
            "Spike · click rolls the tip · double-click edits · right-click to suppress"
        )
        self._hint.setObjectName("HistoryTimelineHint")
        self._hint.hide()

        self._row = QtWidgets.QHBoxLayout()
        self._row.setContentsMargins(4, 0, 4, 0)
        self._row.setSpacing(4)
        row_host = QtWidgets.QWidget()
        row_host.setObjectName("HistoryTimelineRow")
        row_host.setLayout(self._row)
        self._row_host = row_host

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(qt_enum(QtWidgets.QFrame, ("Shape", "NoFrame"), ("NoFrame",)))
        scroll.setHorizontalScrollBarPolicy(_scroll_as_needed())
        scroll.setVerticalScrollBarPolicy(_scroll_off())
        scroll.setWidget(row_host)
        scroll.setObjectName("HistoryTimelineScroll")
        self._scroll = scroll
        self._traced = None

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(8, 2, 4, 2)
        outer.setSpacing(6)
        outer.addWidget(self._title)
        # Playback buttons (PartDesign only; hidden with the marker for assemblies).
        self._playback = PlaybackStrip(self)
        self._playback.requested.connect(self.playbackRequested.emit)
        outer.addWidget(self._playback)
        outer.addWidget(scroll, 1)
        # Right-hand note (assembly order); empty and hidden for PartDesign.
        self._note = QtWidgets.QLabel()
        self._note.setObjectName("HistoryTimelineHint")
        self._note.hide()
        outer.addWidget(self._note)

        self._pending_name = None
        self._click_timer = QtCore.QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self._emit_single_click)
        self._buttons = {}
        self._order = []  # chip names in row order (top level)
        self._entries = {}
        self._marker = None
        self._preview_tip = None
        self._has_marker = False
        self._rename_edit = None
        self._rename_name = None

    # ----- building the row -----------------------------------------------------------------

    def set_timeline(self, body_label, entries, empty_message=None, marker=True, note=None):
        """Rebuild the row. ``marker=False`` drops the rollback marker
        (assemblies have no tip); ``note`` is shown at the right end."""
        self._click_timer.stop()
        self._pending_name = None
        self.cancel_rename()
        self._buttons = {}
        self._order = []
        self._entries = {}
        self._marker = None
        self._preview_tip = None
        self._has_marker = bool(marker) and bool(entries)
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._title.setText(body_label or "History")
        self._note.setText(note or "")
        self._note.setVisible(bool(note))
        self._playback.setVisible(self._has_marker)

        if not entries:
            message = empty_message or "No feature history in this document."
            label = QtWidgets.QLabel(message)
            label.setObjectName("HistoryTimelineHint")
            self._row.addWidget(label)
            self._row.addStretch(1)
            return

        for entry in entries:
            button = FeatureButton(entry)
            button.clicked.connect(lambda name=entry.name: self._schedule_click(name))
            button.doubleClicked.connect(lambda name=entry.name: self._on_double(name))
            button.contextMenu.connect(
                lambda pos, name=entry.name: self.contextMenuRequested.emit(name, pos)
            )
            self._buttons[entry.name] = button
            self._entries[entry.name] = entry
            self._order.append(entry.name)
            self._row.addWidget(button)

        if self._has_marker:
            self._marker = MarkerWidget()
            self._marker.set_row(self._row_host)
            self._marker.dragStarted.connect(self._on_drag_started)
            self._marker.dragMoved.connect(self._on_drag_moved)
            self._marker.dragFinished.connect(self._on_drag_finished)
            self._marker.dragCancelled.connect(self._on_drag_cancelled)
            tip_name = None
            for entry in entries:
                if entry.is_tip:
                    tip_name = entry.name
            if tip_name is None and not all(entry.after_tip for entry in entries):
                # Tip object is missing from the row (not expected). Keep a marker at
                # the end so the bar does not look like history has no playhead.
                tip_name = self._order[-1]
            self._preview_tip = tip_name
            self._row.insertWidget(self._marker_index_for(tip_name), self._marker)
            self._update_playback()
        self._row.addStretch(1)
        self.set_traced(self._traced)

    def button_widget(self, name):
        return self._buttons.get(name)

    def marker_widget(self):
        return self._marker

    def playback_widget(self):
        return self._playback

    def _update_playback(self):
        solids = [n for n in self._order if getattr(self._entries[n], "is_solid", False)]
        tip = self._preview_tip
        at_start = tip is None or not solids
        at_end = bool(solids) and tip == solids[-1]
        self._playback.set_position(at_start, at_end)

    def row_widget(self):
        return self._row_host

    def set_traced(self, name):
        """Ring the chip of feature ``name`` (None clears) and scroll it into view."""
        self._traced = name
        for key, button in self._buttons.items():
            on = key == name
            if bool(button.property("traced")) != on:
                button.setProperty("traced", on)
                button.update()
        button = self._buttons.get(name) if name else None
        if button is not None:
            self._scroll.ensureWidgetVisible(button, 24, 0)

    # ----- inline rename --------------------------------------------------------------------

    def begin_rename(self, name, current_label):
        """Open a line edit over chip ``name``; Enter emits ``renameRequested``."""
        button = self._buttons.get(name)
        if button is None:
            return None
        self.cancel_rename()
        edit = QtWidgets.QLineEdit(self._row_host)
        edit.setObjectName("HistoryTimelineRename")
        edit.setText(current_label)
        edit.selectAll()
        width = max(120, min(260, edit.fontMetrics().horizontalAdvance(current_label) + 40))
        x = min(button.x(), max(0, self._row_host.width() - width))
        edit.setGeometry(x, 2, width, self._row_host.height() - 4)
        edit.editingFinished.connect(self._finish_rename)
        edit.installEventFilter(self)
        edit.show()
        edit.setFocus()
        self._scroll.ensureWidgetVisible(button, 24, 0)
        self._rename_edit = edit
        self._rename_name = name
        return edit

    def rename_editor(self):
        return self._rename_edit

    def eventFilter(self, obj, event):
        if obj is self._rename_edit and event.type() == qt_enum(
            QtCore.QEvent, ("Type", "KeyPress"), ("KeyPress",)
        ):
            escape = qt_enum(QtCore.Qt, ("Key", "Key_Escape"), ("Key_Escape",))
            if event.key() == escape:
                self.cancel_rename()
                return True
        return super().eventFilter(obj, event)

    def cancel_rename(self):
        edit = self._rename_edit
        self._rename_edit = None
        self._rename_name = None
        if edit is not None:
            try:
                edit.editingFinished.disconnect(self._finish_rename)
            except (RuntimeError, TypeError):
                pass
            edit.hide()
            edit.deleteLater()

    def _finish_rename(self):
        edit = self._rename_edit
        name = self._rename_name
        if edit is None or name is None:
            return
        text = edit.text().strip()
        entry = self._entries.get(name)
        self.cancel_rename()
        if text and entry is not None and text != entry.label:
            self.renameRequested.emit(name, text)

    def keyPressEvent(self, event):
        escape = qt_enum(QtCore.Qt, ("Key", "Key_Escape"), ("Key_Escape",))
        if event.key() == escape and self._rename_edit is not None:
            self.cancel_rename()
            event.accept()
            return
        super().keyPressEvent(event)

    # ----- marker position ------------------------------------------------------------------

    def _marker_index_for(self, tip_name):
        """Layout index for the marker: right after chip ``tip_name``, or 0 for None."""
        if tip_name is None or tip_name not in self._buttons:
            return 0
        return self._row.indexOf(self._buttons[tip_name]) + 1

    def tip_for_x(self, x):
        """Tip the marker stands after when it is at ``x`` (row coordinates).

        Chips are visited in row order; the marker falls into the gap before the
        first chip whose centre lies right of ``x``. The tip is the last solid
        chip before that gap, or None when there is none.
        """
        tip = None
        for name in self._order:
            button = self._buttons[name]
            centre = button.x() + button.width() / 2.0
            if x < centre:
                break
            entry = self._entries[name]
            if getattr(entry, "is_solid", False):
                tip = name
        return tip

    def preview_tip(self, tip_name):
        """Move the marker after ``tip_name`` (None: front) and dim the chips after it."""
        if not self._has_marker or self._marker is None:
            return
        self._preview_tip = tip_name
        index = self._marker_index_for(tip_name)
        current = self._row.indexOf(self._marker)
        if current != index:
            self._row.removeWidget(self._marker)
            # Removing a widget before the target shifts the target left by one.
            if current != -1 and current < index:
                index -= 1
            self._row.insertWidget(index, self._marker)
        after = tip_name is None
        for name in self._order:
            entry = self._entries[name]
            button = self._buttons[name]
            is_tip = name == tip_name
            changed = entry.after_tip != after or entry.is_tip != is_tip
            entry.after_tip = after
            entry.is_tip = is_tip
            if is_tip:
                after = True
            if changed:
                button.update()
        self._update_playback()

    def preview_tip_name(self):
        return self._preview_tip

    # ----- marker drag ----------------------------------------------------------------------

    def _on_drag_started(self):
        self._click_timer.stop()
        self._pending_name = None
        self.tipDragStarted.emit()

    def _on_drag_moved(self, x):
        tip = self.tip_for_x(x)
        if tip != self._preview_tip:
            self.preview_tip(tip)
            self.tipPreviewRequested.emit(tip)

    def _on_drag_finished(self):
        self.tipDragFinished.emit(self._preview_tip)

    def _on_drag_cancelled(self):
        self.tipDragCancelled.emit()

    # ----- clicks ---------------------------------------------------------------------------

    def _schedule_click(self, name):
        self._pending_name = name
        self._click_timer.start()

    def _emit_single_click(self):
        name = self._pending_name
        self._pending_name = None
        if name:
            self.tipRequested.emit(name)

    def _on_double(self, name):
        self._click_timer.stop()
        self._pending_name = None
        self.editRequested.emit(name)
