# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault dashboard on the Start page (BW-PDM-10, roadmap section 7.11, approved 24 Oct 2026).

With a vault open, the Start page's main area has two tabs: the vault's dashboard (the default
while a vault is open) and Recent files (the files on this Mac; the default when no vault is
open). The dashboard shows vault items only, never local files:

* header: the vault's name, sync state ("Up to date · read 1 min ago"), where it lives, who you
  are in it, its units; Import Files..., Vault panel, New Project... (PDM-09; greyed until then)
* tiles: parts, assemblies, drawings and bought items by lifecycle state
* Projects (top assemblies until PDM-09 records projects), My work (3 rows per queue),
  Changes (open ECRs and ECOs), Activity (folded, 7/14/30 days), Vault health
* Recent in this vault: the recent-file cards kept to the vault folder (bruceworx_start)

The data is one snapshot dict from bruceworx/vault_dashboard.py, read on the Start page's worker
thread; this module only draws it. Every tile, row and health line opens the matching filtered
list in the Vault panel (bruceworx_vaultclient.show_filtered). Check Vault... lists each problem
with its fix. An empty vault shows "Get started" instead of tiles.

Preferences/BruceWorx/VaultDashboard (default on). Off keeps the page as it was before PDM-10.
"""

from __future__ import annotations

import datetime as _dt
import os

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "VaultDashboard"
# The Start page's tabs: the vault is browsed here only (the user's choice, 25 Oct): its dashboard, its files,
# its structure (drawing, product and where-used trees), and the recent files on this Mac.
TABS = (("dashboard", "Vault dashboard"), ("files", "Files"), ("structure", "Structure"), ("recent", "Recent files"))
VAULT_TABS = ("dashboard", "files", "structure")
RANGES = (7, 14, 30)
FEED_FIRST, FEED_PAGE = 8, 50  # feed lines at first, and how many Show older adds (vault_dashboard)
STATE_KEYS = (("WIP", "wip", "WIP"), ("In Review", "rev", "Review"), ("Released", "rel", "Released"),
              ("Obsolete", "obs", "Obsolete"))
STATE_WORDS = {"WIP": "wip", "In Review": "review", "Released": "released", "Obsolete": "obsolete"}
# My work queue -> (Vault panel chip, search words) for "n more".
QUEUE_FILTERS = {"checked_out": ("checked_out", ""), "in_review": ("all", "state:review"),
                 "newer": ("all", "status:newer"), "ecos_open": ("changes", "change:eco change:open")}
NEW_PROJECT_COMMAND = "BruceWorx_NewProject"  # PDM-09


# ----- pure helpers (unit tested) ----------------------------------------------------------------


def enabled() -> bool:
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def default_tab(vault_open: bool, chosen: str = "") -> str:
    """The tab to show: the one picked by hand, else the dashboard while a vault is open."""
    if chosen in dict(TABS) and (vault_open or chosen not in VAULT_TABS[1:]):
        return chosen
    return "dashboard" if vault_open else "recent"


def tile_query(item_type: str, state: str = "") -> str:
    """Vault panel search words for a tile ("type:part") or one of its legend entries."""
    query = f"type:{item_type}"
    return query + (f" state:{STATE_WORDS[state]}" if state in STATE_WORDS else "")


def read_text(seconds) -> str:
    if seconds is None:
        return "reading…"
    if seconds < 60:
        return "read just now"
    if seconds < 3600:
        return f"read {int(seconds // 60)} min ago"
    return f"read {int(seconds // 3600)} h ago"


def header_line(snap: dict, checked_seconds=None, now=None) -> tuple:
    """(level, text) for the line under the vault's name."""
    health = snap.get("health") or {}
    parts = []
    if snap.get("empty"):
        created = _local_date(snap.get("created", ""))
        today = (now or _dt.datetime.now().astimezone()).date()
        if created == today:
            parts.append("New vault, created today")
        elif created is not None:
            parts.append(f"New vault, created {created.day} {created.strftime('%b')}")
        else:
            parts.append("New vault")
    elif health.get("syncing"):
        n = health["syncing"]
        parts.append(f"{n} version{'s' if n != 1 else ''} still syncing")
        parts.append(read_text(checked_seconds))
    else:
        parts.append("Up to date")
        parts.append(read_text(checked_seconds))
    parts.append(snap.get("storage") or "")
    if snap.get("actor"):
        parts.append(f"you are {snap['actor']}")
    units = (snap.get("settings") or {}).get("units")
    if units:
        parts.append(f"units {units}")
    level = "warn" if health.get("syncing") and health.get("sync_age", 0) > 600 else "ok"
    return level, " · ".join(p for p in parts if p)


def _local_date(text):
    try:
        when = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.astimezone().date()


def legend(states: dict) -> list:
    """[(state, label, count)] for a tile's legend: the states that have items, in lifecycle order."""
    return [(state, text, states.get(state, 0)) for state, _key, text in STATE_KEYS if states.get(state)]


def project_text(row: dict) -> tuple:
    """("8 of 18 released", second line) for a project row."""
    states, total = row.get("states") or {}, row.get("items", 0)
    main = f"{states.get('Released', 0)} of {total} released"
    if row.get("ecos"):
        sub = f"{', '.join(row['ecos'][:2])} open"
    elif states.get("In Review"):
        sub = f"{states['In Review']} in review"
    else:
        sub = ""
    return main, sub


def project_sub(row: dict) -> str:
    """"DM-02-01001-001 · 18 items · 13 drawings"."""
    parts = [row.get("part_number", ""), f"{row.get('items', 0)} items"]
    if row.get("drawings"):
        parts.append(f"{row['drawings']} drawing{'s' if row['drawings'] != 1 else ''}")
    return " · ".join(parts)


def get_started(snap: dict, has_new_project: bool) -> list:
    """The Get started steps of an empty vault: [(key, title, text, done, button, enabled)]."""
    settings = snap.get("settings") or {}
    bits = []
    if settings.get("programs"):
        bits.append(f"Program{'s' if len(settings['programs']) != 1 else ''} {', '.join(settings['programs'])}")
    if settings.get("example"):
        bits.append(f"numbering {settings['example']}")
    if settings.get("units"):
        bits.append(f"units {settings['units']}")
    if settings.get("sheet"):
        bits.append(f"{settings['sheet']} drawings" if len(settings["sheet"]) > 1
                    else f"ANSI {settings['sheet']} drawings")
    return [
        ("settings", "Vault settings", " · ".join(bits) or "Numbering, properties and drawing standards",
         True, "Change…", True),
        ("project", "Start a project",
         "Type its structure; every part, assembly and drawing is numbered and checked in as WIP 01"
         + ("" if has_new_project else " (comes with projects, PDM-09)"), False, "New Project…",
         has_new_project),
        ("import", "Or bring files in", "FreeCAD, STEP or Fusion exports: they get numbers and their links are kept",
         False, "Import Files…", True),
        ("part", "Or make one part", "New Part numbers it when you first save it into the vault", False,
         "New Part", True),
    ]


def health_problems(snap: dict) -> list:
    """Check Vault...: [(kind, text, fix, target)] for each problem; fix is "break_lock" or ""."""
    health = snap.get("health") or {}
    out = []
    for lock in health.get("stale_locks", []):
        out.append(("lock", f"{lock['pn']} checked out by {lock['who']} for {lock['age']}", "break_lock", lock["pn"]))
    if health.get("syncing"):
        out.append(("sync", f"{health['syncing']} version(s) have not arrived from another Mac yet. Keep the Mac "
                            "online and signed in to iCloud; they arrive by themselves.", "", ""))
    for name in health.get("missing", [])[:20]:
        out.append(("sync", f"Not here yet: {name}", "", ""))
    for text in health.get("problems", [])[:50]:
        out.append(("journal", text, "", ""))
    for event in health.get("future_events", []):
        out.append(("clock", f"{event['actor']} {event['op']} is dated {event['at']}, in the future: that Mac's "
                             "clock is wrong. Set it to automatic time.", "", ""))
    for name in health.get("conflict_copies", []):
        out.append(("copy", f"Conflict copy {name}: read and merged, harmless. It can be deleted once every Mac "
                            "has synced.", "", ""))
    return out


# ----- icons ----------------------------------------------------------------------------------------------

_SVG = {
    "released": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/>'
                '<path d="M7.5 12.3l3 3 6-6.3" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"'
                ' stroke-linejoin="round"/>',
    "up": '<path d="M12 20V5M6 11l6-6 6 6" fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round"'
          ' stroke-linejoin="round"/>',
    "review": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/>'
              '<path d="M12 7v5l3.5 2" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "change": '<path d="M6 3h8l4 4v14H6z" fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>'
              '<path d="M12 9v5" stroke="{c}" stroke-width="2.1" stroke-linecap="round"/>'
              '<circle cx="12" cy="17.3" r="1.25" fill="{c}"/>',
    "eco": '<rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/>'
           '<path d="M8 12.5l3 3 5-6" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round"'
           ' stroke-linejoin="round"/>',
    "plus": '<path d="M12 4v16M4 12h16" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/>'
            '<path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "reject": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/>'
              '<path d="M9 9l6 6M15 9l-6 6" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "retire": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/>'
              '<path d="M6 18L18 6" stroke="{c}" stroke-width="2"/>',
    "bom": '<path d="M5 6h14M5 12h14M5 18h9" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
    "cots": '<circle cx="12" cy="12" r="8" fill="{c}" opacity="0.55"/><circle cx="12" cy="12" r="3.2" fill="{c}"/>',
    "dot": '<circle cx="12" cy="12" r="4" fill="{c}"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2h7.5A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5'
              ' 1.5 0 0 1 3 18.5z" fill="{c}"/>',
}
# Activity icon -> colour role.
ICON_ROLES = {"released": "ok", "up": "accent", "review": "accent", "change": "warn", "eco": "accent",
              "plus": "secondary", "lock": "secondary", "reject": "error", "retire": "secondary", "bom": "secondary",
              "dot": "secondary"}


def svg_pixmap(kind, colour, size, dpr):
    from PySide import QtCore, QtGui

    body = _SVG.get(kind, _SVG["dot"]).replace("{c}", colour)
    data = QtCore.QByteArray(('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
                              + body + "</svg>").encode("utf-8"))
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(buffer, b"svg")
    px = int(round(size * dpr))
    reader.setScaledSize(QtCore.QSize(px, px))
    image = reader.read()
    buffer.close()
    pixmap = QtGui.QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


# ----- actions ------------------------------------------------------------------------------------------------


def _vc():
    try:
        import bruceworx_vaultclient

        return bruceworx_vaultclient
    except ImportError:
        return None


def open_filtered(kind="all", query="", select=None):
    vc = _vc()
    if vc is not None:
        vc.show_filtered(kind, query, select)


def open_panel():
    vc = _vc()
    if vc is not None:
        vc.show_panel(True)


def has_new_project() -> bool:
    try:
        import FreeCADGui

        return FreeCADGui.Command.get(NEW_PROJECT_COMMAND) is not None
    except Exception:
        return False


def new_project():
    import FreeCADGui

    if has_new_project():
        FreeCADGui.runCommand(NEW_PROJECT_COMMAND)


def break_lock(pn, parent=None):
    vc = _vc()
    if vc is not None:
        vc.break_lock(pn, parent)


# ----- widgets ---------------------------------------------------------------------------------------------------

_classes = None


def classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtGui, QtWidgets

    def label(text, name, parent=None, wrap=False):
        lab = QtWidgets.QLabel(text, parent)
        lab.setObjectName(name)
        if wrap:
            lab.setWordWrap(True)
        return lab

    def link(text, slot, parent=None):
        button = QtWidgets.QPushButton(text, parent)
        button.setObjectName("bwStartLink")
        button.setFlat(True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.clicked.connect(lambda _c=False: QtCore.QTimer.singleShot(0, slot))
        return button

    def clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                clear(item.layout())

    class Colours(QtWidgets.QWidget):
        """Theme colours for the painted parts, set from BruceWorx.qss (qproperty-*)."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwDashColours")
            self.setFixedSize(0, 0)
            self._c = {"wip": QtGui.QColor("#C27C00"), "rev": QtGui.QColor("#0A84FF"),
                       "rel": QtGui.QColor("#1F9D55"), "obs": QtGui.QColor("#A1A1A6"),
                       "track": QtGui.QColor("#EBEBEF"), "accent": QtGui.QColor("#0A84FF"),
                       "secondary": QtGui.QColor("#6E6E73"), "ok": QtGui.QColor("#1F9D55"),
                       "warn": QtGui.QColor("#C27C00"), "error": QtGui.QColor("#D93A3F"),
                       "bar": QtGui.QColor("#0A84FF")}
            self.listeners = []

        def colour(self, role):
            return self._c.get(role, self._c["secondary"])

        def _getter(key):
            return lambda self: self._c[key]

        def _setter(key):
            def setter(self, value):
                self._c[key] = QtGui.QColor(value)
                for fn in list(self.listeners):
                    fn()
            return setter

        wipColor = QtCore.Property(QtGui.QColor, _getter("wip"), _setter("wip"))
        reviewColor = QtCore.Property(QtGui.QColor, _getter("rev"), _setter("rev"))
        releasedColor = QtCore.Property(QtGui.QColor, _getter("rel"), _setter("rel"))
        obsoleteColor = QtCore.Property(QtGui.QColor, _getter("obs"), _setter("obs"))
        trackColor = QtCore.Property(QtGui.QColor, _getter("track"), _setter("track"))
        accentColor = QtCore.Property(QtGui.QColor, _getter("accent"), _setter("accent"))
        secondaryColor = QtCore.Property(QtGui.QColor, _getter("secondary"), _setter("secondary"))
        okColor = QtCore.Property(QtGui.QColor, _getter("ok"), _setter("ok"))
        warnColor = QtCore.Property(QtGui.QColor, _getter("warn"), _setter("warn"))
        errorColor = QtCore.Property(QtGui.QColor, _getter("error"), _setter("error"))
        barColor = QtCore.Property(QtGui.QColor, _getter("bar"), _setter("bar"))

    class StateBar(QtWidgets.QWidget):
        """A stacked bar of lifecycle states, as the tiles and projects show it."""

        def __init__(self, colours, height=6, parent=None):
            super().__init__(parent)
            self.colours = colours
            self.counts = []
            self.setFixedHeight(height)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        def set_counts(self, states):
            self.counts = [(key, states.get(state, 0)) for state, key, _t in STATE_KEYS]
            self.update()

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            rect = QtCore.QRectF(self.rect())
            radius = rect.height() / 2
            path = QtGui.QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.setClipPath(path)
            p.fillRect(rect, self.colours.colour("track"))
            total = sum(n for _k, n in self.counts)
            x = rect.left()
            for key, n in self.counts:
                if not n or not total:
                    continue
                w = rect.width() * n / total
                p.fillRect(QtCore.QRectF(x, rect.top(), max(w - 1.5, 1), rect.height()), self.colours.colour(key))
                x += w
            p.end()

    class Bars(QtWidgets.QWidget):
        """Events per day above the feed; today is drawn in the accent colour."""

        def __init__(self, colours, parent=None):
            super().__init__(parent)
            self.colours = colours
            self.values = []
            self.setFixedHeight(28)

        def set_values(self, values):
            self.values = list(values)
            self.update()

        def paintEvent(self, _event):
            if not self.values:
                return
            p = QtGui.QPainter(self)
            rect = self.rect()
            top = max(self.values) or 1
            n = len(self.values)
            gap = 2.0
            w = (rect.width() - gap * (n - 1)) / n
            for i, v in enumerate(self.values):
                h = max(2.0, (rect.height() - 2) * v / top) if v else 1.0
                colour = QtGui.QColor(self.colours.colour("accent" if i == n - 1 else "bar"))
                if i != n - 1:
                    colour.setAlphaF(0.45)  # earlier days lighter, today in full
                p.fillRect(QtCore.QRectF(i * (w + gap), rect.height() - h, w, h), colour)
            p.end()

    class Click(QtWidgets.QFrame):
        """A row or tile that runs ``action`` when clicked."""

        def __init__(self, name, action=None, parent=None, tip=""):
            super().__init__(parent)
            self.setObjectName(name)
            self.action = action
            self.setAttribute(QtCore.Qt.WA_Hover)
            if action is not None:
                self.setCursor(QtCore.Qt.PointingHandCursor)
            if tip:
                self.setToolTip(tip)

        def mouseReleaseEvent(self, event):
            if self.action is not None and event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.pos()):
                QtCore.QTimer.singleShot(0, self.action)
            super().mouseReleaseEvent(event)

    class Elided(QtWidgets.QLabel):
        """One line of text that ends in "..." when it does not fit (the full text is the tool tip)."""

        def __init__(self, text, name, parent=None):
            super().__init__(parent)
            self.setObjectName(name)
            self.full = text
            self.setToolTip(text)
            self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            self.setText(text)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            metrics = QtGui.QFontMetrics(self.font())
            self.setText(metrics.elidedText(self.full, QtCore.Qt.ElideRight, max(self.width(), 10)))

    class Section(QtWidgets.QFrame):
        """A card: title, a muted count, and a link at the right; rows below."""

        def __init__(self, title, parent=None):
            super().__init__(parent)
            self.setObjectName("bwDashCard")
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            head = QtWidgets.QWidget(self)
            head.setObjectName("bwDashHead")
            h = QtWidgets.QHBoxLayout(head)
            h.setContentsMargins(16, 10, 14, 8)
            h.setSpacing(8)
            self.title = label(title, "bwDashTitle", head)
            self.count = label("", "bwDashCount", head)
            h.addWidget(self.title)
            h.addWidget(self.count)
            h.addStretch(1)
            self.head_tools = h
            lay.addWidget(head)
            self.body = QtWidgets.QVBoxLayout()
            self.body.setContentsMargins(0, 0, 0, 0)
            self.body.setSpacing(0)
            lay.addLayout(self.body)
            self._link = None

        def set_link(self, text, slot):
            if self._link is not None:
                self._link.deleteLater()
            self._link = link(text, slot, self) if text else None
            if self._link is not None:
                self.head_tools.addWidget(self._link)

    class Dashboard(QtWidgets.QWidget):
        """The vault's dashboard; ``page`` is the Start page (feed options, recent cards, actions)."""

        def __init__(self, page, parent=None):
            super().__init__(parent)
            self.page = page
            self.setObjectName("bwDash")
            self.snap = None
            self.colours = Colours(self)
            self.colours.listeners.append(self._recolour)
            self.feed = {"days": 14, "show_all": False, "mine_only": False, "limit": FEED_FIRST}
            self._build()

        # layout
        def _build(self):
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(8)
            names = QtWidgets.QVBoxLayout()
            names.setSpacing(2)
            self.name = label("", "bwDashName", self)
            self.sync = label("", "bwDashSync", self)
            self.sync.setTextFormat(QtCore.Qt.RichText)
            names.addWidget(self.name)
            names.addWidget(self.sync)
            head.addLayout(names, 1)
            self.import_button = self._button("Import Files…", "bwDashButton", self._import)
            self.panel_button = self._button("Files", "bwDashButton", open_panel)
            self.project_button = self._button("New Project…", "bwDashPrimary", new_project)
            for b in (self.import_button, self.panel_button, self.project_button):
                head.addWidget(b, 0, QtCore.Qt.AlignTop)
            lay.addLayout(head)

            self.tiles_box = QtWidgets.QWidget(self)
            self.tiles = QtWidgets.QGridLayout(self.tiles_box)
            self.tiles.setContentsMargins(0, 0, 0, 0)
            self.tiles.setHorizontalSpacing(14)
            lay.addWidget(self.tiles_box)

            cols = QtWidgets.QHBoxLayout()
            cols.setSpacing(14)
            self.left = QtWidgets.QVBoxLayout()
            self.left.setSpacing(14)
            self.right = QtWidgets.QVBoxLayout()
            self.right.setSpacing(14)
            cols.addLayout(self.left, 3)
            cols.addLayout(self.right, 2)
            lay.addLayout(cols)

            self.start_card = Section("Get started", self)
            self.projects_card = Section("Projects", self)
            self.work_card = Section("My work", self)
            self.work_card.set_link("Open in Files ›", open_panel)
            self.changes_card = Section("Changes", self)
            self.changes_card.set_link("All changes ›", lambda: open_filtered("changes"))
            self.activity_card = Section("Activity", self)
            self.health_card = Section("Vault health", self)
            self.health_card.set_link("Check vault…", lambda: show_check(self.snap))
            for card in (self.start_card, self.projects_card, self.work_card):
                self.left.addWidget(card)
            self.left.addStretch(1)
            for card in (self.changes_card, self.activity_card, self.health_card):
                self.right.addWidget(card)
            self.right.addStretch(1)

            # Activity tools: 7 d / 14 d / 30 d and a menu (only mine, show check-outs).
            seg = QtWidgets.QFrame(self.activity_card)
            seg.setObjectName("bwStartSegments")
            sl = QtWidgets.QHBoxLayout(seg)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(0)
            self.range_group = QtWidgets.QButtonGroup(self)
            for i, days in enumerate(RANGES):
                b = QtWidgets.QPushButton(f"{days} d", seg)
                b.setObjectName("bwStartSegment")
                b.setCheckable(True)
                b.setChecked(days == self.feed["days"])
                b.setProperty("position", "first" if i == 0 else ("last" if i == len(RANGES) - 1 else "mid"))
                b.clicked.connect(lambda _c=False, d=days: self._set_feed(days=d, limit=FEED_FIRST))
                self.range_group.addButton(b)
                sl.addWidget(b)
            self.feed_menu_button = QtWidgets.QToolButton(self.activity_card)
            self.feed_menu_button.setObjectName("bwDashMore")
            self.feed_menu_button.setText("⋯")
            self.feed_menu_button.setToolTip("Whose activity, and which events")
            self.feed_menu_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu = QtWidgets.QMenu(self.feed_menu_button)
            self.mine_action = menu.addAction("Only mine")
            self.mine_action.setCheckable(True)
            self.mine_action.toggled.connect(lambda on: self._set_feed(mine_only=bool(on)))
            self.all_action = menu.addAction("Show check-outs and BOM records")
            self.all_action.setCheckable(True)
            self.all_action.toggled.connect(lambda on: self._set_feed(show_all=bool(on)))
            self.feed_menu_button.setMenu(menu)
            self.activity_card.head_tools.addWidget(seg)
            self.activity_card.head_tools.addWidget(self.feed_menu_button)
            self.bars = Bars(self.colours, self.activity_card)
            wrap = QtWidgets.QHBoxLayout()
            wrap.setContentsMargins(16, 0, 16, 4)
            wrap.addWidget(self.bars)
            self.activity_card.body.addLayout(wrap)
            self.feed_rows = QtWidgets.QVBoxLayout()
            self.feed_rows.setContentsMargins(0, 0, 0, 8)
            self.feed_rows.setSpacing(0)
            self.activity_card.body.addLayout(self.feed_rows)

            # Recent in this vault: the Start page puts its cards here.
            self.recent_box = QtWidgets.QWidget(self)
            self.recent = QtWidgets.QVBoxLayout(self.recent_box)
            self.recent.setContentsMargins(0, 6, 0, 0)
            self.recent.setSpacing(10)
            lay.addWidget(self.recent_box)

        def _button(self, text, name, slot):
            b = QtWidgets.QPushButton(text, self)
            b.setObjectName(name)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False: QtCore.QTimer.singleShot(0, slot))
            return b

        def _import(self):
            import bruceworx_start

            bruceworx_start.import_files(self)

        def _set_feed(self, **changes):
            self.feed.update(changes)
            self.page.request_feed(dict(self.feed))

        def _recolour(self):
            if self.snap is not None:
                QtCore.QTimer.singleShot(0, lambda: self.fill(self.snap, self.page.checked_seconds()))

        def icon(self, kind, role, size=16):
            colour = self.colours.colour(role).name()
            return svg_pixmap(kind, colour, size, self.devicePixelRatioF())

        # data
        def fill(self, snap, checked_seconds=None):
            self.snap = snap
            self.name.setText(snap.get("name", ""))
            self.update_header(checked_seconds)
            project = has_new_project()
            self.project_button.setEnabled(project)
            self.project_button.setToolTip("" if project else "Projects come with PDM-09")
            empty = bool(snap.get("empty"))
            self.panel_button.setVisible(not empty)  # an empty vault: Get started says what to do
            self.tiles_box.setVisible(not empty)
            self.start_card.setVisible(empty)
            self.projects_card.setVisible(not empty)
            self.work_card.setVisible(not empty)
            self.changes_card.setVisible(not empty and bool(snap["changes"]["total"]))
            if empty:
                self._fill_start(snap, project)
            else:
                self._fill_tiles(snap)
                self._fill_projects(snap)
                self._fill_work(snap)
                self._fill_changes(snap)
            self._fill_activity(snap)
            self._fill_health(snap)

        def update_header(self, checked_seconds=None):
            if self.snap is None:
                return
            level, text = header_line(self.snap, checked_seconds)
            dot = self.colours.colour(level).name()
            self.sync.setText(f'<span style="color:{dot}">●</span> {text}')
            self.sync.setToolTip(self.snap.get("root", ""))

        def _fill_tiles(self, snap):
            clear(self.tiles)
            kinds = {"part": "part", "assembly": "assembly", "drawing": "drawing"}
            for col, tile in enumerate(snap["tiles"]):
                t = Click("bwDashTile", lambda ty=tile["type"]: open_filtered("all", tile_query(ty)), self.tiles_box,
                          f"Show the {tile['title'].lower()} in the Vault panel")
                v = QtWidgets.QVBoxLayout(t)
                v.setContentsMargins(16, 12, 14, 12)
                v.setSpacing(6)
                top = QtWidgets.QHBoxLayout()
                top.setSpacing(6)
                icon = QtWidgets.QLabel(t)
                if tile["type"] in kinds:
                    import bruceworx_start

                    icon.setPixmap(bruceworx_start._kind_icon(kinds[tile["type"]]).pixmap(16, 16))
                else:
                    icon.setPixmap(self.icon("cots", "secondary"))
                top.addWidget(icon)
                top.addWidget(label(tile["title"], "bwDashTileTitle", t))
                top.addStretch(1)
                top.addWidget(label("›", "bwDashChevron", t))
                v.addLayout(top)
                v.addWidget(label(f"{tile['total']:,}".replace(",", " "), "bwDashNumber", t))
                bar = StateBar(self.colours, 6, t)
                bar.set_counts(tile["states"])
                v.addWidget(bar)
                rows = QtWidgets.QGridLayout()
                rows.setHorizontalSpacing(12)
                rows.setVerticalSpacing(2)
                items = legend(tile["states"])
                for i, (state, text, n) in enumerate(items):
                    key = dict((s, k) for s, k, _t in STATE_KEYS)[state]
                    colour = self.colours.colour(key).name()
                    entry = Click("bwDashLegend", lambda ty=tile["type"], st=state: open_filtered(
                        "all", tile_query(ty, st)), t, f"Show {text.lower()} {tile['title'].lower()}")
                    el = QtWidgets.QHBoxLayout(entry)
                    el.setContentsMargins(0, 0, 0, 0)
                    lab = label(f'<span style="color:{colour}">■</span> {text} <b>{n:,}</b>'.replace(",", " "),
                                "bwDashLegendText", entry)
                    lab.setTextFormat(QtCore.Qt.RichText)
                    el.addWidget(lab)
                    # Obsolete goes on a second line, as in the mock.
                    if state == "Obsolete":
                        rows.addWidget(entry, 1, 0)
                    else:
                        rows.addWidget(entry, 0, i)
                rows.setColumnStretch(max(len(items), 1), 1)
                v.addLayout(rows)
                if not items:
                    v.addWidget(label("None yet", "bwStartMuted", t))
                v.addStretch(1)
                self.tiles.addWidget(t, 0, col)
                self.tiles.setColumnStretch(col, 1)

        def _fill_projects(self, snap):
            card, projects = self.projects_card, snap["projects"]
            clear(card.body)
            label_text = "Top assemblies" if projects.get("label") == "top assembly" else "Projects"
            card.title.setText(label_text)
            card.count.setText(str(projects["total"]) if projects["total"] else "")
            card.set_link("All assemblies ›", lambda: open_filtered("assemblies"))
            if projects["hidden"]:
                card.body.addWidget(self._note(
                    f"{projects['total']} assemblies are not used by another one. They show here once the "
                    "assemblies' BOMs are recorded (check them in), or as projects once PDM-09 is in."))
                return
            if not projects["rows"]:
                card.body.addWidget(self._note("No assemblies yet. A project's top assembly shows here with how "
                                               "much of it is released."))
                return
            for i, row in enumerate(projects["rows"]):
                r = Click("bwDashRow", lambda pn=row["part_number"]: open_filtered("all", pn, pn), card,
                          f"{row['display']}: show it in the Vault panel")
                r.setProperty("first", i == 0)
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 9, 16, 9)
                h.setSpacing(10)
                import bruceworx_start

                icon = QtWidgets.QLabel(r)
                icon.setPixmap(bruceworx_start._kind_icon("assembly").pixmap(20, 20))
                h.addWidget(icon, 0, QtCore.Qt.AlignTop)
                names = QtWidgets.QVBoxLayout()
                names.setSpacing(1)
                names.addWidget(label(row["description"] or row["part_number"], "bwDashRowName", r))
                names.addWidget(label(project_sub(row), "bwDashRowSub", r))
                h.addLayout(names, 3)
                bar = StateBar(self.colours, 8, r)
                bar.set_counts(row["states"])
                bar.setMinimumWidth(120)
                h.addWidget(bar, 2)
                main, sub = project_text(row)
                right = QtWidgets.QVBoxLayout()
                right.setSpacing(1)
                done = row["states"].get("Released", 0)
                m = label(f"<b>{done} of {row['items']}</b> released", "bwDashRowRight", r)
                m.setTextFormat(QtCore.Qt.RichText)
                m.setAlignment(QtCore.Qt.AlignRight)
                right.addWidget(m)
                s = label(sub, "bwDashRowSub", r)
                s.setAlignment(QtCore.Qt.AlignRight)
                right.addWidget(s)
                h.addLayout(right, 2)
                card.body.addWidget(r)

        def _fill_work(self, snap):
            card, work = self.work_card, snap["my_work"]
            clear(card.body)
            card.count.setText(str(work["total"]) if work["total"] else "")
            if not work["total"]:
                card.body.addWidget(self._note("Nothing is checked out to you, waiting for review or newer in "
                                               "the vault."))
                return
            header = QtWidgets.QWidget(card)
            header.setObjectName("bwDashTableHead")
            hl = QtWidgets.QHBoxLayout(header)
            hl.setContentsMargins(12, 5, 12, 5)
            for text, stretch in (("PART NUMBER", 3), ("DESCRIPTION", 3), ("STATUS", 3)):
                hl.addWidget(label(text, "bwDashColumn", header), stretch)
            card.body.addWidget(header)
            import bruceworx_start

            for queue in work["queues"]:
                if not queue["total"]:
                    continue
                sec = QtWidgets.QWidget(card)
                sec.setObjectName("bwDashGroup")
                sl = QtWidgets.QHBoxLayout(sec)
                sl.setContentsMargins(12, 6, 12, 6)
                sl.setSpacing(8)
                lab = label(f"{queue['title'].upper()} · <b>{queue['total']}</b>", "bwDashGroupText", sec)
                lab.setTextFormat(QtCore.Qt.RichText)
                sl.addWidget(lab)
                more = queue["total"] - len(queue["rows"])
                if more > 0:
                    kind, query = QUEUE_FILTERS.get(queue["key"], ("all", ""))
                    sl.addWidget(link(f"+ {more} more", lambda k=kind, q=query: open_filtered(k, q), sec))
                sl.addStretch(1)
                card.body.addWidget(sec)
                for row in queue["rows"]:
                    if row.get("kind") == "change":
                        action = (lambda n=row["pn"]: open_filtered("changes", "", n))
                    else:
                        action = (lambda pn=row["pn"], path=row.get("path", ""): bruceworx_start.open_vault_item(pn, path))
                    r = Click("bwDashRow", action, card, row.get("path") or "Not in your work folder yet: opening fetches it")
                    h = QtWidgets.QHBoxLayout(r)
                    h.setContentsMargins(12, 6, 12, 6)
                    h.setSpacing(6)
                    icon = QtWidgets.QLabel(r)
                    if row.get("kind") == "change":
                        icon.setPixmap(self.icon("eco", "accent"))
                    elif row.get("kind") == "cots":
                        icon.setPixmap(self.icon("cots", "secondary"))
                    else:
                        icon.setPixmap(bruceworx_start._kind_icon(row.get("kind") or "part").pixmap(16, 16))
                    h.addWidget(icon)
                    h.addWidget(label(row["display"], "bwDashCell", r), 3)
                    h.addWidget(label(row.get("description", ""), "bwDashCell", r), 3)
                    status = QtWidgets.QHBoxLayout()
                    status.setSpacing(5)
                    if row.get("chip") == "rev":
                        chip = label("IN REVIEW", "bwStartBadge", r)
                        chip.setProperty("state", "rev")
                        status.addWidget(chip)
                        text = row["status"].replace("In review", "").strip()
                    else:
                        text = row["status"]
                    status.addWidget(label(text, "bwDashCell", r))
                    status.addStretch(1)
                    h.addLayout(status, 3)
                    card.body.addWidget(r)

        def _fill_changes(self, snap):
            card, changes = self.changes_card, snap["changes"]
            clear(card.body)
            card.count.setText(f"{changes['total']} open" if changes["total"] else "")
            for row in changes["rows"][:6]:
                r = Click("bwDashRow", lambda n=row["number"]: open_filtered("changes", "", n), card,
                          f"{row['number']}: show it in the Vault panel")
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(14, 7, 14, 7)
                h.setSpacing(10)
                icon = QtWidgets.QLabel(r)
                icon.setPixmap(self.icon("eco" if row["kind"] == "ECO" else "change",
                                         "accent" if row["kind"] == "ECO" else "warn"))
                h.addWidget(icon)
                h.addWidget(label(row["number"], "bwDashRowName", r))
                title = Elided(row["title"], "bwDashCell", r)
                h.addWidget(title, 1)
                chip = label(row["state"].upper(), "bwStartBadge", r)
                chip.setProperty("state", "rev" if row["state"] == "Approved" else "wip")
                h.addWidget(chip)
                h.addWidget(label(row["waits"], "bwDashCell", r))
                card.body.addWidget(r)
            if changes["total"] > 6:
                card.body.addWidget(self._note(f"and {changes['total'] - 6} more"))

        def _fill_activity(self, snap):
            feed = snap["activity"]
            self.bars.set_values(feed["bars"])
            self.bars.setVisible(any(feed["bars"]))
            clear(self.feed_rows)
            for b in self.range_group.buttons():
                b.setChecked(b.text() == f"{feed['days']} d")
            for group in feed["groups"]:
                self.feed_rows.addWidget(label(group["label"].upper(), "bwDashDay", self.activity_card))
                for line in group["rows"]:
                    target = line.get("target", "")
                    action = None
                    if target:
                        kind = "changes" if target.startswith(("ECR-", "ECO-")) else "all"
                        action = (lambda k=kind, t=target: open_filtered(k, "", t))
                    r = Click("bwDashFeedRow", action, self.activity_card, line.get("actor", ""))
                    h = QtWidgets.QHBoxLayout(r)
                    h.setContentsMargins(14, 4, 14, 4)
                    h.setSpacing(8)
                    icon = QtWidgets.QLabel(r)
                    icon.setPixmap(self.icon(line["icon"], ICON_ROLES.get(line["icon"], "secondary")))
                    h.addWidget(icon, 0, QtCore.Qt.AlignTop)
                    who = f"<b>{line['who']}</b> " if line["who"] else ""
                    detail = f' <span style="color:{self.colours.colour("secondary").name()}">· ' \
                             f'{_esc(line["detail"])}</span>' if line["detail"] else ""
                    text = label(f"{who}{_esc(line['text'])}{detail}", "bwDashFeedText", r, wrap=True)
                    text.setTextFormat(QtCore.Qt.RichText)
                    text.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                    h.addWidget(text, 1)
                    h.addWidget(label(line["time"], "bwDashTime", r), 0, QtCore.Qt.AlignTop)
                    self.feed_rows.addWidget(r)
            if not feed["groups"]:
                self.feed_rows.addWidget(self._note(f"Nothing in the last {feed['days']} days."))
            if feed.get("older"):
                more = link("Show older ›", lambda: self._set_feed(limit=self.feed["limit"] + FEED_PAGE),
                            self.activity_card)
                wrap = QtWidgets.QHBoxLayout()
                wrap.setContentsMargins(14, 2, 14, 2)
                wrap.addWidget(more)
                wrap.addStretch(1)
                self.feed_rows.addLayout(wrap)

        def _fill_health(self, snap):
            card, health = self.health_card, snap["health"]
            clear(card.body)
            for line in health["lines"]:
                key = line["key"]
                action = None
                if key == "locks" and snap.get("items"):
                    action = (lambda: open_filtered("all", "lock:any"))
                elif key in ("syncing", "journal") and line["level"] == "warn":
                    action = (lambda: show_check(self.snap))
                r = Click("bwDashHealthRow", action, card)
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 7, 16, 7)
                h.setSpacing(10)
                dot = label("●", "bwDashDot", r)
                dot.setStyleSheet(f"color: {self.colours.colour(line['level']).name()};")
                h.addWidget(dot)
                text = label(line["text"], "bwDashCell", r)
                text.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                h.addWidget(text, 1)
                if line.get("note"):
                    h.addWidget(label(line["note"], "bwDashRowSub", r))
                card.body.addWidget(r)

        def _fill_start(self, snap, project):
            card = self.start_card
            clear(card.body)
            steps = get_started(snap, project)
            done = sum(1 for s in steps if s[3])
            card.count.setText(f"{done} of {len(steps)} done")
            import bruceworx_start

            actions = {"settings": lambda: bruceworx_start.vault_settings(self),
                       "project": new_project,
                       "import": lambda: bruceworx_start.import_files(self),
                       "part": bruceworx_start.NEW_ITEMS[0][2]}
            for i, (key, title, text, finished, button, ok) in enumerate(steps):
                r = QtWidgets.QFrame(card)
                r.setObjectName("bwDashRow")
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 10, 16, 10)
                h.setSpacing(12)
                mark = label("✓" if finished else str(i + 1), "bwDashStep", r)
                mark.setProperty("done", finished)
                mark.setAlignment(QtCore.Qt.AlignCenter)
                mark.setFixedSize(28, 28)
                h.addWidget(mark)
                names = QtWidgets.QVBoxLayout()
                names.setSpacing(1)
                names.addWidget(label(title, "bwDashRowName", r))
                names.addWidget(label(text, "bwDashRowSub", r, wrap=True))
                h.addLayout(names, 1)
                if key == "settings":
                    b = link(button, actions[key], r)
                else:
                    b = self._button(button, "bwDashPrimary" if key == "project" else "bwDashButton", actions[key])
                    b.setEnabled(ok)
                    if not ok:
                        b.setToolTip("Projects come with PDM-09")
                h.addWidget(b)
                card.body.addWidget(r)

        def _note(self, text):
            lab = label(text, "bwDashNote", self, wrap=True)
            lab.setContentsMargins(16, 6, 16, 12)
            return lab

    class CheckPanel:
        """Check Vault...: every problem the dashboard found, with its fix (a task panel)."""

        def __init__(self, snap):
            self.form = QtWidgets.QWidget()
            self.form.setWindowTitle("Check Vault")
            lay = QtWidgets.QVBoxLayout(self.form)
            problems = health_problems(snap or {})
            if not problems:
                lay.addWidget(QtWidgets.QLabel("No problems: every file is here or in iCloud, no lock is older than "
                                               "14 days, and the journal reads cleanly."))
            for kind, text, fix, target in problems:
                row = QtWidgets.QHBoxLayout()
                lab = QtWidgets.QLabel(text)
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                row.addWidget(lab, 1)
                if fix == "break_lock":
                    b = QtWidgets.QPushButton("Break Lock…")
                    b.clicked.connect(lambda _c=False, pn=target: break_lock(pn, self.form))
                    row.addWidget(b)
                lay.addLayout(row)
            lay.addStretch(1)

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Close

        def reject(self):
            import FreeCADGui

            FreeCADGui.Control.closeDialog()
            return True

    class StructureView(QtWidgets.QWidget):
        """The Structure tab (UAT, 25 Oct): the vault as trees. Drawings (every drawing under what it
        documents, then what has none), Product (top assemblies down to every part, with quantities)
        and Where used (a part up to every top assembly). Double-click opens a file."""

        MODES = (("drawings", "Drawings"), ("product", "Product"), ("whereused", "Where used"))
        COLUMNS = ("Part number", "Description", "Qty", "State", "Drawings")

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwStructure")
            self.structure = None
            self.mode = "product"
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            bar = QtWidgets.QHBoxLayout()
            bar.setSpacing(6)
            self.mode_group = QtWidgets.QButtonGroup(self)
            self.mode_buttons = {}
            for key, text in self.MODES:
                b = QtWidgets.QPushButton(text, self)
                b.setObjectName("bwDashButton")
                b.setCheckable(True)
                b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
                self.mode_group.addButton(b)
                self.mode_buttons[key] = b
                bar.addWidget(b)
            self.part = QtWidgets.QComboBox(self)
            self.part.setObjectName("bwStructurePart")
            self.part.setMinimumWidth(320)
            self.part.currentIndexChanged.connect(lambda _i: self.render())
            bar.addWidget(self.part)
            bar.addStretch(1)
            self.note = label("", "bwStartMuted", self)
            bar.addWidget(self.note)
            lay.addLayout(bar)
            self.tree = QtWidgets.QTreeWidget(self)
            self.tree.setObjectName("bwStructureTree")
            self.tree.setColumnCount(len(self.COLUMNS))
            self.tree.setHeaderLabels(self.COLUMNS)
            self.tree.setAlternatingRowColors(True)
            self.tree.setUniformRowHeights(True)
            self.tree.setMinimumHeight(420)
            self.tree.itemDoubleClicked.connect(self._open)
            header = self.tree.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            for col in (0, 2, 3, 4):
                header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
            lay.addWidget(self.tree, 1)
            self.set_mode(self.mode)

        def set_structure(self, structure):
            if structure == self.structure:
                return
            self.structure = structure or {"items": {}, "bom": {}, "sources": {}, "drawings": {}}
            current = self.part.currentData()
            self.part.blockSignals(True)
            self.part.clear()
            items = self.structure["items"]
            for pn in sorted(items, key=lambda p: (items[p]["type"] == "assembly", p)):
                info = items[pn]
                if info["type"] != "drawing":
                    self.part.addItem(f"{info['display']}  {info['description']}", pn)
            index = self.part.findData(current)
            self.part.setCurrentIndex(index if index >= 0 else 0)
            self.part.blockSignals(False)
            self.render()

        def set_mode(self, mode):
            self.mode = mode
            for key, b in self.mode_buttons.items():
                b.setChecked(key == mode)
            self.part.setVisible(mode == "whereused")
            self.render()

        def _row(self, parent, node, top=False):
            qty = "" if node.get("qty") is None else str(node["qty"])
            dwgs = ", ".join(self.structure["items"].get(d, {}).get("display", d) for d in node.get("drawings", ()))
            texts = (node["display"], node["description"], qty, node["state"], dwgs)
            item = QtWidgets.QTreeWidgetItem(parent, list(texts))
            item.setData(0, QtCore.Qt.UserRole, node["pn"])
            item.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            tips = []
            if node.get("source") == "links":
                tips.append("Lines from the file's links: no BOM recorded at check-in yet")
            if node.get("cycle"):
                tips.append("Uses itself: the walk stops here")
            if tips:
                for col in range(len(texts)):
                    item.setToolTip(col, "; ".join(tips))
            if top:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            for child in node.get("children", ()):
                self._row(item, child)
            return item

        def _group(self, title, nodes):
            head = QtWidgets.QTreeWidgetItem(self.tree, [f"{title} · {len(nodes)}"])
            head.setFlags(QtCore.Qt.ItemIsEnabled)
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            for node in nodes:
                self._row(head, node)
            return head

        def render(self):
            from bruceworx import vault_trees as vt

            self.tree.clear()
            s = self.structure
            if not s or not s["items"]:
                self.note.setText("The vault is empty.")
                return
            if self.mode == "product":
                tops = vt.product_tree(s)
                for node in tops:
                    self._row(self.tree, node, top=True)
                links = sum(1 for v in s["sources"].values() if v == "links")
                self.note.setText(f"{len(tops)} top assembl{'y' if len(tops) == 1 else 'ies'}"
                                  + (f" · {links} read from file links (no BOM recorded yet)" if links else ""))
                self.tree.expandAll()
            elif self.mode == "drawings":
                t = vt.drawing_tree(s)
                self._group("Documented", t["documented"]).setExpanded(True)
                self._group("No drawing yet", t["undrawn"]).setExpanded(not t["documented"])
                if t["orphans"]:
                    self._group("Drawing of a missing part", t["orphans"]).setExpanded(True)
                self.note.setText(f"{sum(len(n['children']) for n in t['documented'])} drawings · "
                                  f"{len(t['undrawn'])} parts and assemblies without one")
            else:
                pn = self.part.currentData()
                if not pn:
                    self.note.setText("")
                    return
                users = vt.where_used(s, pn)
                for node in users:
                    self._row(self.tree, node)
                self.tree.expandAll()
                self.note.setText(f"Used in {len(users)} assembl{'y' if len(users) == 1 else 'ies'}"
                                  if users else "Not used in any assembly")

        def _open(self, item, _column):
            pn = item.data(0, QtCore.Qt.UserRole)
            vc = _vc()
            if pn and vc is not None:
                QtCore.QTimer.singleShot(0, lambda: vc.run_for("BruceWorx_VaultOpen", pn))

    _classes = {"Dashboard": Dashboard, "StateBar": StateBar, "Bars": Bars, "Section": Section,
                "CheckPanel": CheckPanel, "Click": Click, "StructureView": StructureView}
    return _classes


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def show_check(snap):
    """The Check Vault... task panel for the snapshot on screen."""
    import FreeCADGui

    if FreeCADGui.Control.activeDialog():
        FreeCADGui.Control.closeDialog()
    FreeCADGui.Control.showDialog(classes()["CheckPanel"](snap))
