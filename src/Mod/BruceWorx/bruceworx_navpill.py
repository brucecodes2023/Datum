# SPDX-License-Identifier: LGPL-2.1-or-later
"""Navigation pill: a small bar at the bottom centre of every 3D view.

Roadmap §13.3, marker 10 (UIX-06). Left group: orbit, pan, zoom, fit. Right
group: display style, camera, section, measure, ground grid. The right group and Fit run
their commands (Std_DrawStyle, Std_OrthographicCamera / Std_PerspectiveCamera,
BruceWorx_Section, Std_Measure, Std_ViewFitAll) and show their icons; orbit, pan, zoom and fit use the mock's line glyphs, drawn
in the theme's secondary text colour.

Orbit, pan and zoom are modes, as in Fusion: click one, then drag in the view
with the left button. Click the button again, press Esc or right-click to
stop. The mouse wheel and the navigation style's own buttons keep working
meanwhile. Orbit is a turntable around the world Z axis through a pivot set
by Preferences/BruceWorx/NavPillOrbitCenter: 1 (default) the point under the
pointer where the drag starts (on the model, else on the focal plane), picked
by the navigation style as a mouse orbit does and kept as FreeCAD's rotation
centre (Rotation mode "Object center" is honoured; "Window center" picks at
the pointer, as it would equal 0); 0 the camera's focal point.

The pill sits 12 px above the bottom of the view, centred, or centred in the
space between the floating Browser and task card when the view is too narrow.
It is a child of the 3D view, so it closes with it.

Colours come from BruceWorx.qss (#BruceWorxNavPill block): card, border,
hover, accent and, in dark mode, the icon plate. Other themes get a small
palette-based sheet. Preferences/BruceWorx/NavigationPill (default on).
"""

from __future__ import annotations

import math

from bruceworx_slots import slot

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
THEME_PATH = "User parameter:BaseApp/Preferences/MainWindow"
PREF_NAME = "NavigationPill"
CENTER_PREF = "NavPillOrbitCenter"
CENTER_FOCAL = 0  # the point the camera looks at
CENTER_ROTATION = 1  # FreeCAD's rotation centre, picked where the drag starts
OBJECT_NAME = "BruceWorxNavPill"
VIEW_CLASS = "Gui::View3DInventor"

BOTTOM_MARGIN = 12
PANEL_GAP = 12
BUTTON_SIZE = (28, 26)
GLYPH_SIZE = 16
ICON_SIZE = 20

ORBIT_SPEED = 0.008  # radians per pixel
ZOOM_SPEED = 0.01  # exp(dy * speed)
MODES = ("orbit", "pan", "zoom")

# Line glyphs from the UI mock (scripts/ui_mock/mock.html, G.orbit ...).
GLYPHS = {
    "orbit": '<ellipse cx="8" cy="8" rx="6" ry="2.6"/><path d="M8 2v12"/><circle cx="8" cy="8" r="1.2"/>',
    "pan": '<path d="M8 1.8v12.4M1.8 8h12.4M8 1.8 6.3 3.5M8 1.8l1.7 1.7M8 14.2l-1.7-1.7M8 14.2l1.7-1.7'
    'M1.8 8l1.7-1.7M1.8 8l1.7 1.7M14.2 8l-1.7-1.7M14.2 8l-1.7 1.7"/>',
    "zoom": '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14M5 7h4M7 5v4"/>',
    "fit": '<path d="M2 5.5V2h3.5M10.5 2H14v3.5M14 10.5V14h-3.5M5.5 14H2v-3.5"/>'
    '<rect x="5.5" y="5.5" width="5" height="5" rx="1"/>',
    "grid": '<path d="M2 2h12v12H2zM2 6h12M2 10h12M6 2v12M10 2v12"/>',
    "gravity": '<path d="M8 2v9M4.5 8 8 11.5 11.5 8M3 14h10"/>',
}

TOOLTIPS = {
    "orbit": "Orbit: drag in the view with the left button. Esc to stop.",
    "pan": "Pan: drag in the view with the left button. Esc to stop.",
    "zoom": "Zoom: drag up to zoom in, down to zoom out. Esc to stop.",
    "fit": "Fit all",
    "grid": "Ground grid",
    "gravity": "Gravity",
}

# Used when the theme is not BruceWorx (BruceWorx.qss carries the real block).
FALLBACK_QSS = """
#BruceWorxNavPill { background-color: palette(base); border: 1px solid palette(mid); border-radius: 9px; }
#BruceWorxNavPill QToolButton { color: palette(text); background-color: transparent; border: none;
  border-radius: 6px; padding: 0px; margin: 0px; }
#BruceWorxNavPill QToolButton:hover { background-color: palette(alternate-base); }
#BruceWorxNavPill QToolButton:checked, #BruceWorxNavPill QToolButton:pressed { background-color: palette(midlight); }
#BruceWorxNavPill QToolButton::menu-indicator { image: none; width: 0px; }
#BruceWorxNavPillSeparator { background-color: palette(mid); border: none; }
"""


# --- pure logic --------------------------------------------------------------


def glyph_svg(name, color):
    """SVG text for one of the mock's line glyphs in ``color``."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="%s" '
        'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (color, GLYPHS[name])
    )


def is_dark_theme(theme):
    return "dark" in (theme or "").lower()


def is_bruceworx_theme(theme):
    return (theme or "").startswith("BruceWorx")


def camera_command(camera_type):
    """The command that switches away from ``camera_type``."""
    if str(camera_type).lower().startswith("ortho"):
        return "Std_PerspectiveCamera"
    return "Std_OrthographicCamera"


def style_icon_index(checked, count):
    """Std_DrawStyle action whose icon the style button shows.

    "As is" (0) shows the Shaded icon (5), as in the mock: its own icon is a
    no-entry sign, and most objects display shaded.
    """
    if checked == 0 and count > 5:
        return 5
    return checked


def gravity_button(visible, on):
    """``(visible, checked, tooltip)`` of the gravity button."""
    if not visible:
        return False, False, TOOLTIPS["gravity"]
    if on:
        return True, True, "Gravity on: flexible wires sag. Click to switch off (G, V)."
    return True, False, "Gravity off: flexible wires keep length, bend limit and contacts only. Click to switch on (G, V)."


def next_mode(current, clicked):
    """Mode after clicking a mode button: clicking the active one stops it."""
    return None if current == clicked else clicked


def place(view_w, view_h, pill_w, pill_h, free_left=0, free_right=None, free_bottom=None):
    """Top-left (x, y) of the pill inside a view.

    Centred on the view, BOTTOM_MARGIN above ``free_bottom``. If that would
    reach into the floating panels (``free_left`` / ``free_right`` are the
    panel edges facing the free space), it is centred in the free space
    instead, kept inside the view.
    """
    free_right = view_w if free_right is None else free_right
    free_bottom = view_h if free_bottom is None else free_bottom
    x = (view_w - pill_w) // 2
    lo = free_left + (PANEL_GAP if free_left > 0 else 0)
    hi = free_right - (PANEL_GAP if free_right < view_w else 0)
    if x < lo or x + pill_w > hi:
        x = lo + (hi - lo - pill_w) // 2
    x = max(0, min(x, view_w - pill_w))
    y = max(0, free_bottom - BOTTOM_MARGIN - pill_h)
    return int(x), int(y)


def qmul(a, b):
    """Hamilton product of quaternions (x, y, z, w): rotate by b, then a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qnormalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def qaxis(axis, angle):
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    s = math.sin(angle / 2.0) / n
    return (x * s, y * s, z * s, math.cos(angle / 2.0))


def qrotate(q, v):
    """Rotate vector v by quaternion q."""
    x, y, z, w = q
    r = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), (-x, -y, -z, w))
    return (r[0], r[1], r[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def view_direction(q):
    return qrotate(q, (0.0, 0.0, -1.0))


def focal_point(q, position, focal_distance):
    return _add(position, _scale(view_direction(q), focal_distance))


def orbit(q, position, focal_distance, dx, dy, up=(0.0, 0.0, 1.0), speed=ORBIT_SPEED, pivot=None):
    """Turntable orbit about ``pivot`` (default: the focal point).

    Returns (orientation, position); the focal distance does not change.
    Dragging right turns the model right (about ``up``); dragging down tips its
    top towards the viewer (about the camera's right axis).
    """
    if pivot is None:
        pivot = focal_point(q, position, focal_distance)
    right = qrotate(q, (1.0, 0.0, 0.0))
    turn = qmul(qaxis(up, -dx * speed), qaxis(right, -dy * speed))
    new_q = qnormalize(qmul(turn, q))
    new_pos = _add(pivot, qrotate(turn, _sub(position, pivot)))
    return new_q, new_pos


def orbit_center_mode(value):
    """CENTER_FOCAL or CENTER_ROTATION from the stored preference value."""
    return CENTER_FOCAL if value == CENTER_FOCAL else CENTER_ROTATION


def inventor_pixel(x, y, height, dpr=1.0):
    """Qt widget position (logical, origin top left) to Coin viewport pixels.

    Coin counts device pixels from the bottom left, as the trackpad orbit in
    NavigationStyle::processTrackpadScroll does.
    """
    return int(round(x * dpr)), int(round((height - y - 1) * dpr))


def units_per_pixel(orthographic, size, focal_distance, width, height):
    """World units per screen pixel at the focal plane.

    ``size`` is the orthographic camera's height or the perspective camera's
    height angle. Coin fits that size to the smaller window side.
    """
    side = max(1, min(width, height))
    if orthographic:
        return size / side
    return 2.0 * focal_distance * math.tan(size / 2.0) / side


def pan(q, position, dx, dy, upp):
    """Camera position after dragging the model by (dx, dy) pixels."""
    right = qrotate(q, (1.0, 0.0, 0.0))
    upv = qrotate(q, (0.0, 1.0, 0.0))
    return _add(position, _add(_scale(right, -dx * upp), _scale(upv, dy * upp)))


def zoom_factor(dy, speed=ZOOM_SPEED):
    """Scale for the view size: dragging up (dy < 0) zooms in (< 1)."""
    return math.exp(max(-200.0, min(200.0, dy)) * speed)


def zoom_perspective(q, position, focal_distance, factor):
    """(position, focal distance) after moving towards the focal point."""
    pivot = focal_point(q, position, focal_distance)
    new_fd = max(1e-6, focal_distance * factor)
    return _sub(pivot, _scale(view_direction(q), new_fd)), new_fd


# --- GUI ---------------------------------------------------------------------

_manager = None
_classes = None


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def orbit_center():
    import FreeCAD

    return orbit_center_mode(FreeCAD.ParamGet(PREFS_PATH).GetInt(CENTER_PREF, CENTER_ROTATION))


def _theme():
    import FreeCAD

    return FreeCAD.ParamGet(THEME_PATH).GetString("Theme", "")


def _cpp(obj):
    try:
        import shiboken6

        return shiboken6.getCppPointer(obj)[0]
    except Exception:
        return id(obj)


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _command_actions(name):
    import FreeCADGui

    try:
        return list(FreeCADGui.Command.get(name).getAction())
    except Exception:
        return []


def _glyph_icon(name, color, dpr):
    from PySide import QtCore, QtGui, QtSvg

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(glyph_svg(name, color).encode()))
    side = int(round(GLYPH_SIZE * dpr))
    image = QtGui.QImage(side, side, QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()
    pixmap = QtGui.QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return QtGui.QIcon(pixmap)


def _make_classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtGui, QtWidgets

    class Pill(QtWidgets.QFrame):
        """The bar itself, a child of one View3DInventor."""

        def __init__(self, view):
            super().__init__(view)
            self.mode = None
            self._py_view = None
            self._drag = None
            self._pivot = None
            self._cursor = None
            self._refreshing = False
            self.setObjectName(OBJECT_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(6, 3, 6, 3)
            layout.setSpacing(2)
            self.buttons = {}
            for name in MODES + ("fit",):
                button = self._button(name, TOOLTIPS[name])
                button.setIconSize(QtCore.QSize(GLYPH_SIZE, GLYPH_SIZE))
                if name in MODES:
                    button.setCheckable(True)
                    button.clicked.connect(lambda _c=False, n=name: self.set_mode(next_mode(self.mode, n)))
                else:
                    button.clicked.connect(self.fit)
                layout.addWidget(button)
            sep = QtWidgets.QFrame(self)
            sep.setObjectName(OBJECT_NAME + "Separator")
            sep.setFixedSize(1, 18)
            layout.addSpacing(3)
            layout.addWidget(sep)
            layout.addSpacing(3)

            # Display style: the Std_DrawStyle group as a menu, icon = current style.
            style = self._button("style", "Display style")
            style.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu = QtWidgets.QMenu(style)
            self.style_actions = _command_actions("Std_DrawStyle")
            for action in self.style_actions:
                menu.addAction(action)
                action.toggled.connect(self._refresh_later)
            style.setMenu(menu)
            layout.addWidget(style)

            camera = self._button("camera", "")
            camera.clicked.connect(self.toggle_camera)
            for name in ("Std_OrthographicCamera", "Std_PerspectiveCamera"):
                for action in _command_actions(name):
                    action.toggled.connect(self._refresh_later)
            layout.addWidget(camera)

            # Section and measure mirror their commands' actions (icon, tooltip, enabled). Section is
            # BruceWorx's saved section view (MOD-16); FreeCAD's Clipping dock stays in the View menu.
            for key, command in (("section", "BruceWorx_Section"), ("measure", "Std_Measure")):
                actions = _command_actions(command)
                if not actions:
                    continue
                button = self._button(key, "")
                button.setDefaultAction(actions[0])
                layout.addWidget(button)

            grid = self._button("grid", TOOLTIPS["grid"])
            grid.setCheckable(True)
            grid.setIconSize(QtCore.QSize(GLYPH_SIZE, GLYPH_SIZE))
            grid.clicked.connect(self.toggle_grid)
            layout.addWidget(grid)

            # Gravity of the flexible wires (ROU-08): shown only when the active assembly has some.
            gravity = self._button("gravity", TOOLTIPS["gravity"])
            gravity.setCheckable(True)
            gravity.setIconSize(QtCore.QSize(GLYPH_SIZE, GLYPH_SIZE))
            gravity.clicked.connect(slot(self.toggle_gravity))
            gravity.setVisible(False)
            layout.addWidget(gravity)

            for key in ("style", "camera", "section", "measure"):
                if key in self.buttons:
                    self.buttons[key].setIconSize(QtCore.QSize(ICON_SIZE, ICON_SIZE))

            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 2)
            self.shadow = shadow
            self.setGraphicsEffect(shadow)
            self.apply_theme()

        @property
        def view(self):
            # Not stored: FreeCAD can invalidate the Python wrapper of a view
            # while the view lives on; the parent pointer is always current.
            return self.parentWidget()

        def _button(self, key, tip):
            button = QtWidgets.QToolButton(self)
            button.setObjectName(OBJECT_NAME + "_" + key)
            button.setFixedSize(*BUTTON_SIZE)
            button.setAutoRaise(True)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.setToolTip(tip)
            button.pressed.connect(self.activate_view)
            self.buttons[key] = button
            return button

        # --- theme
        def apply_theme(self):
            theme = _theme()
            dark = is_dark_theme(theme)
            self.setProperty("dark", "true" if dark else "false")
            self.setStyleSheet("" if is_bruceworx_theme(theme) else FALLBACK_QSS)
            self.shadow.setColor(QtGui.QColor(0, 0, 0, 110 if dark else 34))
            self.style().unpolish(self)
            self.style().polish(self)
            for button in self.buttons.values():
                button.style().unpolish(button)
                button.style().polish(button)
            self.refresh()

        def event(self, event):
            if event.type() in (QtCore.QEvent.StyleChange, QtCore.QEvent.PaletteChange):
                self._refresh_later()
            return super().event(event)

        def _refresh_later(self, *_args):
            QtCore.QTimer.singleShot(0, self.refresh)

        def refresh(self):
            """Glyph colours, display-style and camera icons, size and place."""
            if self._refreshing or not _alive(self):
                return
            self._refreshing = True
            try:
                probe = self.buttons["orbit"]
                probe.ensurePolished()
                color = probe.palette().color(QtGui.QPalette.ButtonText).name()
                dpr = self.devicePixelRatioF() or 1.0
                for name in GLYPHS:
                    self.buttons[name].setIcon(_glyph_icon(name, color, dpr))
                index = next((i for i, a in enumerate(self.style_actions) if a.isChecked()), 0)
                if self.style_actions:
                    action = self.style_actions[style_icon_index(index, len(self.style_actions))]
                    self.buttons["style"].setIcon(action.icon())
                    label = self.style_actions[index].text().replace("&", "")[2:]
                    self.buttons["style"].setToolTip("Display style: " + label)
                self._refresh_camera()
                self.refresh_grid()
                self.refresh_gravity()
                self.adjustSize()
                self.reposition()
            finally:
                self._refreshing = False

        def _refresh_camera(self):
            view = self.py_view()
            kind = "Orthographic"
            try:
                kind = view.getCameraType() if view else kind
            except Exception:
                pass
            target = camera_command(kind)
            current = "Std_OrthographicCamera" if target == "Std_PerspectiveCamera" else "Std_PerspectiveCamera"
            actions = _command_actions(current)
            if actions:
                self.buttons["camera"].setIcon(actions[0].icon())
            other = "perspective" if target == "Std_PerspectiveCamera" else "orthographic"
            self.buttons["camera"].setToolTip("%s camera. Click for %s." % (kind, other))

        def refresh_grid(self):
            """The ground-grid button stays down while the grid is drawn."""
            button = self.buttons.get("grid")
            if button is None or not _alive(button):
                return
            try:
                import ground_grid

                shown = ground_grid.prefs_request_grid()
            except Exception:
                shown = False
            button.blockSignals(True)
            button.setChecked(shown)
            button.blockSignals(False)
            button.setToolTip("Ground grid on. Click to hide." if shown else "Ground grid off. Click to show.")

        def refresh_gravity(self):
            """Shown (and lit when on) while the active assembly has flexible wires."""
            button = self.buttons.get("gravity")
            if button is None or not _alive(button):
                return
            try:
                import bruceworx_rope

                visible, on, _assembly = bruceworx_rope.gravity_state()
                bruceworx_rope.sync_command(on)
            except Exception:
                visible, on = False, False
            visible, checked, tip = gravity_button(visible, on)
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
            button.setToolTip(tip)
            if button.isVisibleTo(self) != visible:
                button.setVisible(visible)
                self.adjustSize()
                self.reposition()

        def toggle_gravity(self):
            """The same as the Routing ribbon's Gravity command."""
            import bruceworx_rope

            bruceworx_rope.toggle_gravity()
            self.refresh_gravity()

        def toggle_grid(self):
            import ground_grid

            button = self.buttons["grid"]
            ground_grid.set_shown(button.isChecked())
            self.refresh_grid()

        # --- placement
        def reposition(self):
            if not _alive(self) or not _alive(self.view):
                return
            area = self.view.rect()
            free_left, free_right, free_bottom = 0, area.width(), area.height()
            band_top = area.height() - BOTTOM_MARGIN - self.height()
            mw = _main_window()
            for panel in _overlay_panels(mw):
                if not panel.isVisible():
                    continue
                top_left = self.view.mapFromGlobal(panel.mapToGlobal(QtCore.QPoint(0, 0)))
                rect = QtCore.QRect(top_left, panel.size())
                if not rect.intersects(area):
                    continue
                side = panel.objectName()
                if side == "OverlayBottom":
                    free_bottom = min(free_bottom, rect.top())
                elif rect.bottom() >= band_top:
                    if side == "OverlayLeft":
                        free_left = max(free_left, rect.right() + 1)
                    elif side == "OverlayRight":
                        free_right = min(free_right, rect.left())
            x, y = place(area.width(), area.height(), self.width(), self.height(), free_left, free_right, free_bottom)
            self.move(x, y)
            self.raise_()

        # --- commands
        def activate_view(self):
            """Commands act on the active view: make it this pill's view."""
            sub = self.view.parentWidget()
            mdi = sub.mdiArea() if isinstance(sub, QtWidgets.QMdiSubWindow) else None
            if mdi is not None and mdi.activeSubWindow() is not sub:
                mdi.setActiveSubWindow(sub)

        def py_view(self):
            if self._py_view is not None:
                try:
                    self._py_view.getCameraType()
                    return self._py_view
                except Exception:
                    self._py_view = None
            self._py_view = _find_py_view(self.view)
            return self._py_view

        def fit(self):
            import FreeCADGui

            self.activate_view()
            FreeCADGui.runCommand("Std_ViewFitAll")

        def toggle_camera(self):
            import FreeCADGui

            self.activate_view()
            view = self.py_view()
            kind = view.getCameraType() if view else "Perspective"
            # Checkable commands only act on "checked" (iMsg 1).
            FreeCADGui.runCommand(camera_command(kind), 1)
            self._refresh_later()

        # --- orbit / pan / zoom modes
        def set_mode(self, mode):
            viewport = self.viewport()
            if self.mode and viewport is not None and self._cursor is not None:
                viewport.setCursor(self._cursor)
            self.mode = mode
            self._drag = None
            self._pivot = None
            for name in MODES:
                self.buttons[name].setChecked(name == mode)
            if mode and viewport is not None:
                self.activate_view()
                self._cursor = viewport.cursor()
                cursors = {
                    "orbit": QtCore.Qt.OpenHandCursor,
                    "pan": QtCore.Qt.SizeAllCursor,
                    "zoom": QtCore.Qt.SizeVerCursor,
                }
                viewport.setCursor(cursors[mode])
                _status(TOOLTIPS[mode])
            else:
                _status("")

        def viewport(self):
            gv = self.view.findChild(QtWidgets.QGraphicsView)
            return gv.viewport() if gv is not None else None

        def handle(self, event):
            """Mouse and Esc events from the viewport while a mode is on."""
            kind = event.type()
            if kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
                if event.key() == QtCore.Qt.Key_Escape:
                    if kind == QtCore.QEvent.KeyPress:
                        self.set_mode(None)
                    event.accept()
                    return True
                return False
            if kind == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.RightButton:
                    self.set_mode(None)
                    return True
                if event.button() == QtCore.Qt.LeftButton:
                    self._drag = event.position().toPoint()
                    self._pivot = self.find_pivot(self._drag) if self.mode == "orbit" else None
                    return True
                return False
            if kind == QtCore.QEvent.MouseButtonDblClick and event.button() == QtCore.Qt.LeftButton:
                return True
            if kind == QtCore.QEvent.MouseMove and self._drag is not None:
                pos = event.position().toPoint()
                delta = pos - self._drag
                self._drag = pos
                self.drag(delta.x(), delta.y())
                return True
            if kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                self._drag = None
                self._pivot = None
                return True
            return False

        def find_pivot(self, point):
            """Orbit pivot for a drag starting at ``point`` (viewport position).

            None means the focal point: chosen in the preference, or when the
            navigation style cannot pick (older build, no 3D view).
            """
            if orbit_center() != CENTER_ROTATION:
                return None
            view = self.py_view()
            viewport = self.viewport()
            if view is None or viewport is None:
                return None
            try:
                style = view.getViewer().getNavigationStyle()
                x, y = inventor_pixel(point.x(), point.y(), viewport.height(), viewport.devicePixelRatioF())
                center = style.findRotationCenter(x, y)
            except Exception:
                return None
            return None if center is None else (center.x, center.y, center.z)

        def drag(self, dx, dy):
            """Move the camera of this view by a drag of (dx, dy) pixels."""
            view = self.py_view()
            if view is None or (dx == 0 and dy == 0):
                return
            from pivy import coin

            cam = view.getCameraNode()
            q = tuple(cam.orientation.getValue().getValue())
            pos = tuple(cam.position.getValue().getValue())
            fd = cam.focalDistance.getValue()
            ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
            viewport = self.viewport()
            w, h = (viewport.width(), viewport.height()) if viewport is not None else (800, 600)
            if self.mode == "orbit":
                q, pos = orbit(q, pos, fd, dx, dy, pivot=self._pivot)
                cam.orientation.setValue(coin.SbRotation(*q))
                cam.position.setValue(coin.SbVec3f(*pos))
            elif self.mode == "pan":
                size = cam.height.getValue() if ortho else cam.heightAngle.getValue()
                pos = pan(q, pos, dx, dy, units_per_pixel(ortho, size, fd, w, h))
                cam.position.setValue(coin.SbVec3f(*pos))
            elif self.mode == "zoom":
                factor = zoom_factor(dy)
                if ortho:
                    cam.height.setValue(cam.height.getValue() * factor)
                else:
                    pos, fd = zoom_perspective(q, pos, fd, factor)
                    cam.position.setValue(coin.SbVec3f(*pos))
                    cam.focalDistance.setValue(fd)

    class Manager(QtCore.QObject):
        """Puts a pill on every 3D view and keeps it placed."""

        def __init__(self):
            super().__init__()
            self.pills = {}
            self.viewports = {}
            self.timer = QtCore.QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(0)
            self.timer.timeout.connect(self.sync)
            self.watched_panels = set()
            self._gravity_pending = False

        def schedule(self, *_args):
            self.timer.start()

        def sync(self):
            mw = _main_window()
            mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
            if mdi is None:
                return
            if not enabled():
                self.remove_all()
                return
            for panel in _overlay_panels(mw):
                key = _cpp(panel)
                if key not in self.watched_panels:
                    self.watched_panels.add(key)
                    panel.installEventFilter(self)
            for sub in mdi.subWindowList():
                view = sub.widget()
                if view is None or view.metaObject().className() != VIEW_CLASS:
                    continue
                key = _cpp(view)
                if key in self.pills and _alive(self.pills[key]):
                    continue
                self.attach(view, key)
            self.reposition_all()

        def attach(self, view, key):
            pill = Pill(view)
            self.pills[key] = pill
            view.installEventFilter(self)
            viewport = pill.viewport()
            if viewport is not None:
                viewport.installEventFilter(self)
                viewport.parentWidget().installEventFilter(self)
                self.viewports[_cpp(viewport)] = key
                self.viewports[_cpp(viewport.parentWidget())] = key
            view.destroyed.connect(lambda _obj=None, k=key: self.forget(k))
            pill.show()
            pill.refresh()

        def forget(self, key):
            self.pills.pop(key, None)
            for vp in [vp for vp, k in self.viewports.items() if k == key]:
                del self.viewports[vp]

        def remove_all(self):
            for key, pill in list(self.pills.items()):
                if _alive(pill):
                    pill.set_mode(None)
                    if _alive(pill.view):
                        pill.view.removeEventFilter(self)
                    pill.hide()
                    pill.deleteLater()
                self.forget(key)

        def schedule_gravity(self):
            """One cheap gravity-button refresh after a recompute (several recomputes in a row count once)."""
            if self._gravity_pending:
                return
            self._gravity_pending = True
            QtCore.QTimer.singleShot(200, self._refresh_gravity)

        def _refresh_gravity(self):
            self._gravity_pending = False
            for pill in list(self.pills.values()):
                if _alive(pill):
                    pill.refresh_gravity()

        def reposition_all(self):
            for pill in list(self.pills.values()):
                if _alive(pill):
                    pill.reposition()

        def apply_theme(self):
            for pill in list(self.pills.values()):
                if _alive(pill):
                    pill.apply_theme()

        def eventFilter(self, obj, event):
            kind = event.type()
            key = self.viewports.get(_cpp(obj))
            if key is not None:
                pill = self.pills.get(key)
                if pill is not None and pill.mode and _alive(pill):
                    return pill.handle(event)
                return False
            if kind in (QtCore.QEvent.Resize, QtCore.QEvent.Show, QtCore.QEvent.Hide, QtCore.QEvent.Move):
                pill = self.pills.get(_cpp(obj))
                if pill is not None:
                    if _alive(pill):
                        pill.reposition()
                else:
                    # An overlay panel moved, opened or closed.
                    self.reposition_all()
            return False

    _classes = (Pill, Manager)
    return _classes


def _main_window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _overlay_panels(mw):
    from PySide import QtWidgets

    if mw is None:
        return []
    return [
        w
        for w in mw.findChildren(QtWidgets.QTabWidget)
        if w.metaObject().className() == "Gui::OverlayTabWidget"
    ]


def _status(text):
    try:
        bar = _main_window().statusBar()
        if text:
            bar.showMessage(text, 6000)
        else:
            bar.clearMessage()
    except Exception:
        pass


def _find_py_view(view_widget):
    """The Python 3D view object for a Gui::View3DInventor widget."""
    import FreeCAD
    import FreeCADGui
    from PySide import QtWidgets

    gv = view_widget.findChild(QtWidgets.QGraphicsView)
    if gv is None:
        return None
    target = _cpp(gv)
    for name in FreeCAD.listDocuments():
        doc = FreeCADGui.getDocument(name)
        if doc is None:
            continue
        for view in doc.mdiViewsOfType(VIEW_CLASS):
            try:
                if _cpp(view.graphicsView()) == target:
                    return view
            except Exception:
                continue
    return None


def pills():
    """The live pill widgets (for tests and scripts)."""
    if _manager is None:
        return []
    return [p for p in _manager.pills.values() if _alive(p)]


class _PrefObserver:
    def onChange(self, _group, name):
        if _manager is None:
            return
        if name == PREF_NAME:
            _manager.schedule()
        elif name == "Theme":
            from PySide import QtCore

            # FreeCAD reloads the stylesheet on the same change; follow after it.
            QtCore.QTimer.singleShot(0, _manager.apply_theme)


class _DocObserver:
    def slotCreatedDocument(self, _doc):
        _manager.schedule()

    def slotRecomputedDocument(self, _doc):
        _manager.schedule_gravity()

    def slotActivateDocument(self, _doc):
        _manager.schedule()
        _manager.schedule_gravity()


_observer = None
_doc_observer = None
_groups = []


def install():
    """Attach a pill to every 3D view now and to each new one."""
    global _manager, _observer, _doc_observer
    if _manager is not None:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore, QtWidgets

    mw = FreeCADGui.getMainWindow()
    mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
    if mdi is None:
        QtCore.QTimer.singleShot(500, install)
        return
    _pill, manager_class = _make_classes()
    _manager = manager_class()
    mdi.subWindowActivated.connect(_manager.schedule)
    _doc_observer = _DocObserver()
    FreeCADGui.addDocumentObserver(_doc_observer)
    _observer = _PrefObserver()
    for path in (PREFS_PATH, THEME_PATH):
        # Keep the handles: a collected wrapper detaches its observer.
        group = FreeCAD.ParamGet(path)
        group.Attach(_observer)
        _groups.append(group)
    _manager.schedule()
