# SPDX-License-Identifier: LGPL-2.1-or-later
"""Project tabs, cards and pages on the vault dashboard (BW-PDM-15, 1 Nov 2026).

The vault area of the Start page has a tab for All projects and one for each project (a program, the first
field of the part number). All projects shows one card per project; a project opens on a page with the tabs
Overview, Files, Drawings, Simulations, Changes and History, as in the user's mockup ("Vault project tabs").

Everything on it is data from ``bruceworx.project_pages`` (the dashboard snapshot's ``project_pages``): the
journal and the structure. Pictures are the ones saved in the files (or cached by Refresh Pictures); no
document is ever opened to fill a page. Files is the Start page's Files tab (the PDM-13 browser) moved into
the page and scoped to the project; leaving the page puts it back.

The pure helpers at the top are unit tested (TestBruceWorxProjectPage); the widgets are made by ``classes()``
and used by ``bruceworx_dashboard.Dashboard``.
"""

from __future__ import annotations

import os
import tempfile
import zipfile

import bruceworx_dashboard as bwdash

PAGE_TABS = (("overview", "Overview"), ("files", "Files"), ("drawings", "Drawings"),
             ("simulations", "Simulations"), ("changes", "Changes"), ("history", "History"))
ALL = ""  # the All projects tab
CARD_COLUMNS = 3
SIM_EMPTY = "No simulations yet. Simulations of this project's parts and assemblies are listed here."


# ----- pure helpers (unit tested) ------------------------------------------------------------------


def tab_text(label: str, count) -> str:
    """"Files 77" (the count after the name; none for Overview and History)."""
    return f"{label} {count}" if count not in (None, "") else label


def project_tab_labels(pages: dict) -> list:
    """[(code, text)] for the tabs along the top, after All projects."""
    return [(t["code"], t["label"]) for t in (pages or {}).get("tabs", [])]


def bar_text(card: dict) -> str:
    """The line under a card's bar: "All 77 WIP · 0 released", "12 of 77 released"."""
    states, total = card.get("states") or {}, card.get("files", 0)
    if not total:
        return ""
    released, wip = states.get("Released", 0), states.get("WIP", 0)
    if wip == total:
        return f"All {total} WIP · 0 released"
    return f"{released} of {total} released" + (f" · {wip} WIP" if wip else "")


def card_lines(card: dict) -> list:
    """[(label, value, level)] of the three lines at the bottom of a project card."""
    return [("Missing a drawing", str(card.get("missing", 0)), "warn" if card.get("missing") else "ok"),
            ("Checked out to me", str(card.get("mine", 0)), "secondary"),
            ("Last check-in", card.get("last") or "none yet", "secondary")]


def empty_note(card: dict) -> str:
    return (f"Part numbers start at {card.get('first_number', '')}. Numbering is set up for parts, assemblies "
            "and drawings.")


def header_sub(page: dict) -> str:
    """"VL-P01 90 N turbojet · part numbers VL-xx-xxxxx · 77 files"."""
    parts = [page.get("description") or "", f"part numbers {page.get('pattern', '')}",
             f"{page.get('files', 0)} file{'s' if page.get('files', 0) != 1 else ''}"]
    return " · ".join(p for p in parts if p)


def attention_target(item: dict) -> tuple:
    """Where a Needs attention line leads: ("tab", name) or ("files", scope, query)."""
    if item.get("tab"):
        return ("tab", item["tab"])
    return ("files", item.get("scope", ""), item.get("query", ""))


def picture_file(ref) -> str:
    """The picture of a version as a PNG on disk: the one Refresh Pictures cached, else the thumbnail saved
    in the file (copied to a temp folder once), else "". Nothing is opened or rendered."""
    if not ref or not ref.get("sha"):
        return ""
    sha = ref["sha"]
    try:
        import bruceworx_preview

        cached = bruceworx_preview.cached(sha)
        if cached:
            return cached
    except Exception:  # noqa: BLE001 -- no preview module (headless tests): the file's own thumbnail
        pass
    folder = os.path.join(tempfile.gettempdir(), "bruceworx-thumbnails")
    target = os.path.join(folder, sha + ".png")
    if os.path.exists(target):
        return target
    try:
        with zipfile.ZipFile(ref["object"]) as archive:
            data = archive.read("thumbnails/Thumbnail.png")
        os.makedirs(folder, exist_ok=True)
        with open(target + ".part", "wb") as out:
            out.write(data)
        os.replace(target + ".part", target)
        return target
    except (OSError, KeyError, zipfile.BadZipFile, TypeError):
        return ""


def visible_rows(rows: list, limit: int) -> tuple:
    """(first ``limit`` rows, how many more)."""
    return rows[:limit], max(0, len(rows) - limit)


# ----- widgets ---------------------------------------------------------------------------------------

_classes = None
_pixmaps = {}


def classes():
    global _classes
    if _classes is not None:
        return _classes
    from PySide import QtCore, QtGui, QtWidgets

    base = bwdash.classes()
    Click, Section, StateBar = base["Click"], base["Section"], base["StateBar"]

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

    def pixmap_for(ref, width, height, dpr):
        path = picture_file(ref)
        if not path:
            return None
        key = (path, width, height, round(dpr, 1))
        if key not in _pixmaps:
            pix = QtGui.QPixmap(path)
            if pix.isNull():
                _pixmaps[key] = None
            else:
                pix = pix.scaled(int(width * dpr), int(height * dpr), QtCore.Qt.KeepAspectRatio,
                                 QtCore.Qt.SmoothTransformation)
                pix.setDevicePixelRatio(dpr)
                _pixmaps[key] = pix
        return _pixmaps[key]

    class Picture(QtWidgets.QLabel):
        """A file's picture in a fixed box; the kind's icon when it has none."""

        def __init__(self, dash, ref, kind, width, height, parent=None):
            super().__init__(parent)
            self.setObjectName("bwProjectPicture")
            self.setFixedSize(width, height)
            self.setAlignment(QtCore.Qt.AlignCenter)
            track = dash.colours.colour("track")
            track.setAlphaF(0.55)
            self.setStyleSheet(f"QLabel#bwProjectPicture {{ background: {track.name(QtGui.QColor.HexArgb)}; "
                               "border-radius: 6px; }")
            pix = pixmap_for(ref, width, height, self.devicePixelRatioF()) if ref else None
            self.has_picture = pix is not None
            if pix is not None:
                self.setPixmap(pix)
            else:
                import bruceworx_start

                icon = bruceworx_start._kind_icon(kind if kind in ("part", "assembly", "drawing") else "part")
                size = min(width, height, 32)
                self.setPixmap(icon.pixmap(size, size))

    def state_badge(info_state, word, parent):
        chip = label(word, "bwStartBadge", parent)
        chip.setProperty("state", {"WIP": "wip", "In Review": "rev", "Released": "rel"}.get(info_state, "wip"))
        return chip

    class ProjectTabs(QtWidgets.QFrame):
        """All projects, then a tab for each project, along the top of the vault area."""

        def __init__(self, dash, parent=None):
            super().__init__(parent)
            self.dash = dash
            self.setObjectName("bwStartTabs")
            self.lay = QtWidgets.QHBoxLayout(self)
            self.lay.setContentsMargins(0, 0, 0, 0)
            self.lay.setSpacing(18)
            self.group = QtWidgets.QButtonGroup(self)
            self.buttons = {}
            self.sig = None

        def fill(self, tabs, vault_name, current):
            sig = (tuple(tabs), vault_name)
            if sig != self.sig:
                self.sig = sig
                clear(self.lay)
                for b in list(self.group.buttons()):
                    self.group.removeButton(b)
                self.buttons = {}
                self.lay.addWidget(label(vault_name, "bwDashTitle", self))
                for code, text in [(ALL, "All projects")] + list(tabs):
                    b = QtWidgets.QPushButton(text, self)
                    b.setObjectName("bwStartTab")
                    b.setCheckable(True)
                    b.setCursor(QtCore.Qt.PointingHandCursor)
                    b.clicked.connect(lambda _c=False, c=code: self.dash.set_project(c))
                    self.group.addButton(b)
                    self.buttons[code] = b
                    self.lay.addWidget(b)
                self.lay.addStretch(1)
                self.lay.addWidget(link("All files ›", lambda: self.dash.open_files(ALL), self))
            for code, b in self.buttons.items():
                b.setChecked(code == current)

    class ProjectCard(Click):
        """One project on the All projects view."""

        def __init__(self, dash, card, parent=None):
            super().__init__("bwDashTile", lambda c=card["code"]: dash.set_project(c), parent,
                             f"Open the {card['title']} project")
            v = QtWidgets.QVBoxLayout(self)
            v.setContentsMargins(16, 14, 16, 14)
            v.setSpacing(10)
            top = QtWidgets.QHBoxLayout()
            top.setSpacing(10)
            badge = label(card["code"], "bwStartBadge", self)
            badge.setProperty("state", "rev")
            top.addWidget(badge, 0, QtCore.Qt.AlignTop)
            names = QtWidgets.QVBoxLayout()
            names.setSpacing(0)
            names.addWidget(label(card["title"], "bwDashRowName", self))
            sub = card["sub"] or ("No files yet" if card["empty"] else "")
            names.addWidget(label(sub, "bwDashRowSub", self))
            top.addLayout(names, 1)
            top.addWidget(label("›", "bwDashChevron", self))
            v.addLayout(top)
            if card["empty"]:
                body = QtWidgets.QVBoxLayout()
                body.setSpacing(6)
                nothing = label("Nothing in this project yet", "bwDashRowName", self)
                nothing.setAlignment(QtCore.Qt.AlignCenter)
                body.addStretch(1)
                body.addWidget(nothing)
                body.addWidget(link(f"Import files into {card['code']}…", dash.import_files, self), 0,
                               QtCore.Qt.AlignHCenter)
                body.addStretch(1)
                holder = QtWidgets.QWidget(self)
                holder.setFixedHeight(150)
                holder.setLayout(body)
                v.addWidget(holder)
                v.addWidget(label(empty_note(card), "bwDashRowSub", self, wrap=True))
                v.addStretch(1)
                return
            v.addWidget(Picture(dash, card.get("picture"), "assembly", 300, 150, self), 0, QtCore.Qt.AlignHCenter)
            nums = QtWidgets.QHBoxLayout()
            for key, title in (("parts", "Parts"), ("assemblies", "Assemblies"), ("drawings", "Drawings")):
                col = QtWidgets.QVBoxLayout()
                col.setSpacing(0)
                col.addWidget(label(str(card[key]), "bwDashRowName", self))
                col.addWidget(label(title, "bwDashRowSub", self))
                nums.addLayout(col, 1)
            v.addLayout(nums)
            bar = StateBar(dash.colours, 6, self)
            bar.set_counts(card["states"])
            v.addWidget(bar)
            v.addWidget(label(bar_text(card), "bwDashRowSub", self))
            for text, value, level in card_lines(card):
                row = QtWidgets.QHBoxLayout()
                row.addWidget(label(text, "bwDashRowSub", self))
                row.addStretch(1)
                val = Elided(value, "bwDashCell", self) if text == "Last check-in" else label(value, "bwDashCell", self)
                if level == "warn":
                    val.setStyleSheet(f"color: {dash.colours.colour('warn').name()}; font-weight: 600;")
                if text == "Last check-in":
                    val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    row.addWidget(val, 1)
                else:
                    row.addWidget(val)
                v.addLayout(row)

    Elided = base.get("Elided")
    if Elided is None:  # not exported by older dashboards: a plain label
        Elided = lambda text, name, parent=None: label(text, name, parent)  # noqa: E731

    class ProjectCards(QtWidgets.QWidget):
        """The grid of project cards."""

        def __init__(self, dash, parent=None):
            super().__init__(parent)
            self.dash = dash
            self.grid = QtWidgets.QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setHorizontalSpacing(14)
            self.grid.setVerticalSpacing(14)

        def fill(self, cards):
            clear(self.grid)
            for i, card in enumerate(cards):
                self.grid.addWidget(ProjectCard(self.dash, card, self), i // CARD_COLUMNS, i % CARD_COLUMNS)
            for col in range(CARD_COLUMNS):
                self.grid.setColumnStretch(col, 1)

    class ProjectPage(QtWidgets.QWidget):
        """One project: header, tabs and the pane of the tab picked."""

        def __init__(self, dash, parent=None):
            super().__init__(parent)
            self.dash = dash
            self.setObjectName("bwProjectPage")
            self.page = None
            self.tab = "overview"
            self.code = ""
            self.sig = None
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(12)
            self.badge = label("", "bwStartBadge", self)
            self.badge.setProperty("state", "rev")
            head.addWidget(self.badge, 0, QtCore.Qt.AlignTop)
            names = QtWidgets.QVBoxLayout()
            names.setSpacing(2)
            self.title = label("", "bwDashName", self)
            self.sub = label("", "bwDashSync", self)
            names.addWidget(self.title)
            names.addWidget(self.sub)
            head.addLayout(names, 1)
            self.package_button = dash._button("Build package…", "bwDashButton", self.build_package)
            self.new_button = dash._button("New part…", "bwDashPrimary", self.new_part)
            head.addWidget(self.package_button, 0, QtCore.Qt.AlignTop)
            head.addWidget(self.new_button, 0, QtCore.Qt.AlignTop)
            lay.addLayout(head)
            self.tabs_frame = QtWidgets.QFrame(self)
            self.tabs_frame.setObjectName("bwStartTabs")
            tl = QtWidgets.QHBoxLayout(self.tabs_frame)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(18)
            self.group = QtWidgets.QButtonGroup(self)
            self.tab_buttons = {}
            for key, text in PAGE_TABS:
                b = QtWidgets.QPushButton(text, self.tabs_frame)
                b.setObjectName("bwStartTab")
                b.setCheckable(True)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(lambda _c=False, k=key: self.set_tab(k))
                self.group.addButton(b)
                self.tab_buttons[key] = b
                tl.addWidget(b)
            tl.addStretch(1)
            lay.addWidget(self.tabs_frame)
            self.panes = {}
            for key, _text in PAGE_TABS:
                pane = QtWidgets.QWidget(self)
                pane.setObjectName(f"bwProjectPane_{key}")
                pl = QtWidgets.QVBoxLayout(pane)
                pl.setContentsMargins(0, 0, 0, 0)
                pl.setSpacing(14)
                lay.addWidget(pane)
                self.panes[key] = pane
            self.files_host = self.panes["files"].layout()

        # ----- actions
        def set_tab(self, key):
            if key != "files":
                self.dash.release_files()
            self.tab = key
            for k, b in self.tab_buttons.items():
                b.setChecked(k == key)
            for k, pane in self.panes.items():
                pane.setVisible(k == key)
            if key == "files":
                self.dash.show_files_in(self.files_host, self.code)

        def to_files(self, scope="", query="", select=None):
            self.set_tab("files")
            self.dash.scope_files(self.code, scope, query, select)

        def build_package(self):
            self.dash.build_package(self.code, [pn for pn in (self.page or {}).get("pns", [])])

        def new_part(self):
            self.dash.new_part(self.code)

        # ----- filling
        def fill(self, page, tab=None):
            self.page = page
            self.code = page["code"]
            self.badge.setText(page["code"])
            self.title.setText(page["full_title"])
            self.sub.setText(header_sub(page))
            self.new_button.setText(f"New part in {page['code']}…")
            counts = page["tab_counts"]
            for key, text in PAGE_TABS:
                self.tab_buttons[key].setText(tab_text(text, counts.get(key)))
            self.package_button.setEnabled(bool(page["drawings"]))
            self._fill_overview(page)
            self._fill_drawings(page)
            self._fill_simulations(page)
            self._fill_changes(page)
            self._fill_history(page)
            self.set_tab(tab or self.tab)

        def _section(self, pane, title):
            card = Section(title, pane)
            pane.layout().addWidget(card)
            return card

        def _clear_pane(self, key):
            lay = self.panes[key].layout()
            if key == "files":
                return
            clear(lay)

        def _note(self, text):
            return self.dash._note(text)

        def _fill_overview(self, page):
            self._clear_pane("overview")
            pane = self.panes["overview"]
            lay = pane.layout()
            tiles = QtWidgets.QHBoxLayout()
            tiles.setSpacing(14)
            for c in page["counts"]:
                tile = Click("bwDashTile", lambda c=c: self._count_action(c), pane, "Show these in the Files tab")
                v = QtWidgets.QVBoxLayout(tile)
                v.setContentsMargins(16, 12, 14, 12)
                v.setSpacing(4)
                v.addWidget(label(c["title"], "bwDashTileTitle", tile))
                number = label(str(c["value"]), "bwDashNumber", tile)
                if c.get("level") == "warn":
                    number.setStyleSheet(f"color: {self.dash.colours.colour('warn').name()};")
                v.addWidget(number)
                v.addWidget(label(c["note"], "bwDashRowSub", tile, wrap=True))
                v.addStretch(1)
                tiles.addWidget(tile, 1)
            lay.addLayout(tiles)
            cols = QtWidgets.QHBoxLayout()
            cols.setSpacing(14)
            left = QtWidgets.QVBoxLayout()
            right = QtWidgets.QVBoxLayout()
            structure = Section("Product structure", pane)
            structure.set_link("All files ›", lambda: self.to_files())
            rows, more = visible_rows(page["structure"], 14)
            if not rows:
                structure.body.addWidget(self._note("No assemblies yet."))
            for i, row in enumerate(rows):
                structure.body.addWidget(self._structure_row(structure, row, i == 0))
            if more:
                structure.body.addWidget(self._note(f"and {more} more assemblies in Files"))
            left.addWidget(structure)
            left.addStretch(1)
            attention = Section("Needs attention", pane)
            if not page["attention"]:
                attention.body.addWidget(self._note("Nothing needs attention."))
            for item in page["attention"]:
                attention.body.addWidget(self._attention_row(attention, item))
            right.addWidget(attention)
            right.addStretch(1)
            cols.addLayout(left, 3)
            cols.addLayout(right, 2)
            lay.addLayout(cols)
            recent = Section(f"Recent in {page['code']}", pane)
            recent.set_link("Project history ›", lambda: self.set_tab("history"))
            strip = QtWidgets.QHBoxLayout()
            strip.setContentsMargins(14, 4, 14, 14)
            strip.setSpacing(12)
            for r in page["recent"]:
                strip.addWidget(self._picture_card(pane, r))
            if not page["recent"]:
                strip.addWidget(self._note("Nothing has been checked in yet."))
            strip.addStretch(1)
            recent.body.addLayout(strip)
            lay.addWidget(recent)

        def _count_action(self, c):
            if c.get("tab"):
                self.set_tab(c["tab"])
            else:
                self.to_files(c.get("scope", ""), c.get("query", ""))

        def _structure_row(self, parent, row, first):
            r = Click("bwDashRow", lambda pn=row["pn"]: self.to_files("", "", pn), parent, row["pn"])
            r.setProperty("first", first)
            h = QtWidgets.QHBoxLayout(r)
            h.setContentsMargins(16 + 18 * row["depth"], 8, 16, 8)
            h.setSpacing(10)
            h.addWidget(Picture(self.dash, row.get("picture"), "assembly", 52, 40, r))
            names = QtWidgets.QVBoxLayout()
            names.setSpacing(1)
            names.addWidget(label(row["name"], "bwDashRowName", r))
            names.addWidget(label(row["sub"], "bwDashRowSub", r))
            h.addLayout(names, 1)
            h.addWidget(state_badge(row["state"], row["state_word"], r))
            drawing = label(row["drawing"], "bwDashCell", r)
            if row["drawing_level"] == "warn":
                drawing.setStyleSheet(f"color: {self.dash.colours.colour('warn').name()};")
            drawing.setMinimumWidth(78)
            h.addWidget(drawing)
            return r

        def _attention_row(self, parent, item):
            kind = attention_target(item)
            if kind[0] == "tab":
                action = (lambda t=kind[1]: self.set_tab(t))
            else:
                action = (lambda s=kind[1], q=kind[2]: self.to_files(s, q))
            r = Click("bwDashRow", action, parent)
            h = QtWidgets.QHBoxLayout(r)
            h.setContentsMargins(16, 9, 16, 9)
            h.setSpacing(10)
            dot = label("●", "bwDashDot", r)
            dot.setStyleSheet(f"color: {self.dash.colours.colour('warn' if item['level'] == 'warn' else 'accent').name()};")
            h.addWidget(dot, 0, QtCore.Qt.AlignTop)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(label(item["title"], "bwDashRowName", r))
            col.addWidget(label(item["detail"], "bwDashRowSub", r, wrap=True))
            h.addLayout(col, 1)
            return r

        def _picture_card(self, parent, r):
            card = Click("bwDashTile", lambda pn=r["pn"]: self.to_files("", "", pn), parent, r["pn"])
            card.setFixedWidth(176)
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(4)
            v.addWidget(Picture(self.dash, r.get("picture"), r["kind"], 158, 100, card))
            v.addWidget(Elided(r["name"], "bwDashRowName", card))
            v.addWidget(label(r["sub"], "bwDashRowSub", card))
            return card

        def _fill_drawings(self, page):
            self._clear_pane("drawings")
            pane = self.panes["drawings"]
            card = self._section(pane, "Drawings")
            card.count.setText(str(len(page["drawings"])) if page["drawings"] else "")
            if not page["drawings"]:
                card.body.addWidget(self._note("This project has no drawings yet."))
                return
            for i, d in enumerate(page["drawings"]):
                r = Click("bwDashRow", lambda pn=d["pn"]: self.to_files("", "", pn), card, d["pn"])
                r.setProperty("first", i == 0)
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 8, 16, 8)
                h.setSpacing(10)
                h.addWidget(Picture(self.dash, d.get("picture"), "drawing", 64, 44, r))
                col = QtWidgets.QVBoxLayout()
                col.setSpacing(1)
                col.addWidget(label(d["name"], "bwDashRowName", r))
                col.addWidget(label(f"of {d['of_name']}" if d["of_name"] else d["pn"], "bwDashRowSub", r))
                h.addLayout(col, 1)
                h.addWidget(label(d["sub"], "bwDashRowSub", r))
                h.addWidget(state_badge(d["state"], d["state_word"], r))
                card.body.addWidget(r)

        def _fill_simulations(self, page):
            self._clear_pane("simulations")
            pane = self.panes["simulations"]
            card = self._section(pane, "Simulations")
            rows = page["simulations"]
            card.count.setText(str(len(rows)) if rows else "")
            if not rows:
                card.body.addWidget(self._note(SIM_EMPTY))
                return
            for i, sim in enumerate(rows):
                r = Click("bwDashRow", lambda pn=sim["pn"]: self.to_files("", "", pn), card, sim["pn"])
                r.setProperty("first", i == 0)
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 9, 16, 9)
                h.setSpacing(10)
                col = QtWidgets.QVBoxLayout()
                col.setSpacing(1)
                col.addWidget(label(sim["description"] or sim["display"], "bwDashRowName", r))
                col.addWidget(label(f"of {sim['of_display']}" if sim["of_display"] else sim["pn"],
                                    "bwDashRowSub", r))
                h.addLayout(col, 1)
                if sim["status"]:
                    h.addWidget(label(str(sim["status"]), "bwDashCell", r))
                if sim["out_of_date"]:
                    old = label("OUT OF DATE", "bwStartBadge", r)
                    old.setProperty("state", "wip")
                    h.addWidget(old)
                if sim["state"]:
                    h.addWidget(state_badge(sim["state"], sim["state"].upper(), r))
                card.body.addWidget(r)

        def _fill_changes(self, page):
            self._clear_pane("changes")
            pane = self.panes["changes"]
            card = self._section(pane, "Changes touching this project")
            rows = page["changes"]
            card.count.setText(f"{page['open_changes']} open" if page["open_changes"] else "")
            if not rows:
                card.body.addWidget(self._note("No engineering change requests or orders touch this project."))
                return
            for i, c in enumerate(rows):
                r = Click("bwDashRow", lambda n=c["number"]: bwdash.open_filtered("changes", "", n), card, c["number"])
                r.setProperty("first", i == 0)
                h = QtWidgets.QHBoxLayout(r)
                h.setContentsMargins(16, 8, 16, 8)
                h.setSpacing(10)
                icon = QtWidgets.QLabel(r)
                icon.setPixmap(self.dash.icon("eco" if c["kind"] == "ECO" else "change",
                                              "accent" if c["kind"] == "ECO" else "warn"))
                h.addWidget(icon)
                h.addWidget(label(c["number"], "bwDashRowName", r))
                h.addWidget(Elided(c["title"], "bwDashCell", r), 1)
                chip = label(c["state"].upper(), "bwStartBadge", r)
                chip.setProperty("state", "rev" if c["state"] == "Approved" else "wip")
                h.addWidget(chip)
                h.addWidget(label(c["sub"], "bwDashRowSub", r))
                card.body.addWidget(r)

        def _fill_history(self, page):
            self._clear_pane("history")
            pane = self.panes["history"]
            card = self._section(pane, "History")
            feed = page["history"]
            card.count.setText(f"{feed['events']} events" if feed["events"] else "")
            if not feed["groups"]:
                card.body.addWidget(self._note("Nothing has happened in this project yet."))
                return
            for group in feed["groups"]:
                card.body.addWidget(label(group["label"].upper(), "bwDashDay", card))
                for line in group["rows"]:
                    target = line.get("target", "")
                    action = None
                    if target:
                        kind = "changes" if target.startswith(("ECR-", "ECO-")) else "all"
                        action = (lambda k=kind, t=target: bwdash.open_filtered(k, "", t))
                    r = Click("bwDashFeedRow", action, card, line.get("actor", ""))
                    h = QtWidgets.QHBoxLayout(r)
                    h.setContentsMargins(14, 4, 14, 4)
                    h.setSpacing(8)
                    icon = QtWidgets.QLabel(r)
                    icon.setPixmap(self.dash.icon(line["icon"], bwdash.ICON_ROLES.get(line["icon"], "secondary")))
                    h.addWidget(icon, 0, QtCore.Qt.AlignTop)
                    who = f"<b>{line['who']}</b> " if line["who"] else ""
                    detail = (f' <span style="color:{self.dash.colours.colour("secondary").name()}">· '
                              f'{bwdash._esc(line["detail"])}</span>') if line["detail"] else ""
                    text = label(f"{who}{bwdash._esc(line['text'])}{detail}", "bwDashFeedText", r, wrap=True)
                    text.setTextFormat(QtCore.Qt.RichText)
                    text.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                    h.addWidget(text, 1)
                    h.addWidget(label(line["time"], "bwDashTime", r), 0, QtCore.Qt.AlignTop)
                    card.body.addWidget(r)

    _classes = {"ProjectTabs": ProjectTabs, "ProjectCards": ProjectCards, "ProjectPage": ProjectPage,
                "Picture": Picture}
    return _classes
