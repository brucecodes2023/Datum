# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Files tab as a file browser (PDM-13, 1 Nov 2026): its navigation pane, its preview pane and
the 3D preview.

The list in the middle is the Vault panel's (bruceworx_vaultclient.VaultPanel), now one row per file.
Around it:

* **Navigation** (left): projects (the programs of the vault's numbering) with every assembly of each,
  saved views (drawing packages, missing a drawing, checked out to me, work in progress) and the
  categories. Picking one sets the list's *scope*; the chips and the search still apply on top.
* **Preview** (right): the selected file. *Preview* (3D, ISO picture, drawing sheet), *Properties*,
  *Drawings* (its drawing package), *Uses*, *Where used* and *History*.
* **3D**: the file's preview mesh (bruceworx_previewmesh), drawn off screen with the same renderer as
  the ISO pictures and shown as an image, about 20 frames a second while dragging. pivy's Quarter
  widget, the obvious choice, crashes in this build (GL errors, 1 Nov), so the view draws its frames
  itself. Nothing is opened: the mesh is read from the file.

The pure helpers at the top are tested in TestBruceWorxVaultBrowser.py.
"""

from __future__ import annotations

import os
import tempfile

ALL = "all"
VIEWS = (
    ("view:drawings", "Drawing packages"),
    ("view:missing", "Missing a drawing"),
    ("view:checked_out", "Checked out to me"),
    ("view:wip", "Work in progress"),
    ("view:simout", "Simulation out of date"),
)
TABS = (("preview", "Preview"), ("props", "Properties"), ("drawings", "Drawings"), ("simulations", "Simulations"),
        ("uses", "Uses"), ("used", "Where used"), ("history", "History"))
WRITABLE = ("modified", "checked_out")  # bruceworx_vaultclient.WRITABLE


# ----- pure helpers (unit tested) -----------------------------------------------------------------


def in_scope(scope, pn, row, structure, contents_of=None) -> bool:
    """Whether the list row ``pn`` belongs to the navigation's ``scope``."""
    from heuretes_freecad import vault_trees

    if scope in (ALL, "", None):
        return True
    if row.get("item_type") == "change":
        return False
    key, _sep, value = scope.partition(":")
    if key == "program":
        return vault_trees.program_code(pn) == value
    if key == "asm":
        reach = contents_of(value) if contents_of is not None else vault_trees.contents(structure, value)
        return pn in reach
    if key == "cat":
        return (row.get("category") or "") == value
    if scope == "view:drawings":
        return row.get("item_type") == "drawing"
    if scope == "view:missing":
        return vault_trees.missing_drawing(structure, pn)
    if scope == "view:simout":  # PDM-14: simulations made from an older version of their part
        return bool(((structure.get("sims") or {}).get(pn) or {}).get("judgement") == "part changed")
    if scope == "view:checked_out":
        return row.get("code") in WRITABLE
    if scope == "view:wip":
        return row.get("state") == "WIP"
    return True


def nav_entries(structure, rows, programs) -> list:
    """The navigation's rows: [(key, label, count, depth, kind)]. ``kind``: "section" (a heading),
    "program", "assembly", "view" or "category". ``programs``: {code: description}."""
    from heuretes_freecad import vault_trees

    files = {pn: r for pn, r in rows.items() if r.get("item_type") != "change"}
    out = [("", "Projects", None, 0, "section")]
    codes = sorted({vault_trees.program_code(pn) for pn in files} - {""})
    for code in codes:
        mine = [pn for pn in files if vault_trees.program_code(pn) == code]
        name = programs.get(code) or code
        out.append((f"program:{code}", f"{code} · {name}" if programs.get(code) else code, len(mine), 0, "program"))
        for pn in sorted(p for p in mine if files[p].get("item_type") == "assembly"):
            desc = files[pn].get("description") or pn
            out.append((f"asm:{pn}", desc, len(vault_trees.contents(structure, pn) & set(files)), 1, "assembly"))
    out.append(("", "Views", None, 0, "section"))
    for key, label in VIEWS:
        count = sum(1 for pn, r in files.items() if in_scope(key, pn, r, structure))
        out.append((key, label, count, 0, "view"))
    out.append((ALL, "Everything", len(files), 0, "view"))
    cats = {}
    for r in files.values():
        cats[r.get("category") or ""] = cats.get(r.get("category") or "", 0) + 1
    if any(cats):
        out.append(("", "Categories", None, 0, "section"))
        for name in sorted(cats, key=lambda c: (c == "", c.casefold())):
            out.append((f"cat:{name}", name or "Uncategorized", cats[name], 0, "category"))
    return out


def scope_title(scope, rows, programs) -> list:
    """The breadcrumb for ``scope``: [vault-relative labels]."""
    from heuretes_freecad import vault_trees

    if scope in (ALL, "", None):
        return ["Everything"]
    key, _sep, value = scope.partition(":")
    if key == "program":
        return [f"{value} · {programs[value]}" if programs.get(value) else value]
    if key == "asm":
        code = vault_trees.program_code(value)
        head = f"{code} · {programs[code]}" if programs.get(code) else code
        return [head, f"{(rows.get(value) or {}).get('description') or value} and what it uses"]
    if key == "cat":
        return [value or "Uncategorized"]
    return [dict(VIEWS).get(scope, scope)]


def used_in_text(lines) -> str:
    """The list's Used in cell: "2 asm" (assemblies, not quantities), "" when none."""
    n = len({parent for parent, _q in lines})
    return f"{n} asm" if n else ""


def drawing_cell(structure, pn, row) -> tuple:
    """(text, colour role) of the list's Drawing cell."""
    from heuretes_freecad import vault_trees

    kind = row.get("item_type")
    if kind not in ("part", "assembly", "cots"):
        return "", "secondary"
    info = structure["items"].get(pn) or {"type": kind, "description": row.get("description")}
    if structure["drawings"].get(pn):
        n = len(structure["drawings"][pn])
        return ("Yes" if n == 1 else f"{n} sheets"), "ok"
    if vault_trees.bought(info):
        return "COTS", "secondary"
    return "None", "warn"


def history_rows(versions, results=None) -> list:
    """[(version label, comment, when, size)] newest first, from FileVersion records. ``results`` (PDM-14):
    {sha256: True|False|None} for a simulation's versions: "with results" or "no results" follows the size."""
    out = []
    for i, v in enumerate(versions, 1):
        comment = (getattr(v, "comment", "") or "Checked in").strip()
        if comment.startswith("Imported from "):
            comment = "Imported from " + os.path.basename(comment[len("Imported from "):].strip())
        when = (getattr(v, "at", "") or "")[:16].replace("T", " ")
        size = getattr(v, "size", 0) or 0
        size_text = f"{size / 1024:.0f} kB" if size else ""
        if size and size >= 1024 * 1024:
            size_text = f"{size / 1048576:.1f} MB"
        mark = {True: "with results", False: "no results"}.get((results or {}).get(getattr(v, "sha256", "")))
        out.append((f"v{i}", comment, when, " · ".join(t for t in (size_text, mark) if t)))
    return list(reversed(out))


def coin_matrix(m):
    """A row-major 4x4 (FreeCAD, column vectors) as SbMatrix's 16 values (Coin multiplies row vectors)."""
    return [m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14], m[3], m[7], m[11], m[15]]


# ----- reading a file's preview mesh through the session ------------------------------------------


def mesh_source(s, pn):
    """The bytes of ``pn``'s preview mesh: the work copy's when there is one (what you have), else the
    latest version's stored file, else the cache Refresh Pictures made. b"" when there is none."""
    import bruceworx_previewmesh as pm

    work = s.work.path(pn) if pn in s.vault.items else ""
    if work and os.path.exists(work):
        data = pm.read(work)
        if data:
            return data
    latest = s.vault.latest_version(pn) if pn in s.vault.items else None
    if latest is None:
        return b""
    return pm.read(os.path.join(s.vault.root, latest.object_name)) or pm.cached(latest.sha256)


def meshes_for(s, pn, cache):
    """[(part, matrix)] to draw for ``pn``, following assemblies into their files. ``cache`` keeps
    decoded meshes by P/N for the session's life (cleared when the vault changes)."""
    import bruceworx_previewmesh as pm

    def load(key):
        p = key[:-6] if key.lower().endswith(".fcstd") else key
        if p not in cache:
            try:
                data = mesh_source(s, p)
                cache[p] = pm.decode(data) if data else None
            except ValueError:
                cache[p] = None
        return cache[p]

    return pm.flatten(load, pn)


# ----- widgets ------------------------------------------------------------------------------------


def make_classes(vc):
    """The browser's widgets. ``vc`` is bruceworx_vaultclient's class namespace (icon_pixmap, soft)."""
    import FreeCADGui
    from PySide import QtCore, QtGui, QtWidgets

    icon_pixmap, soft = vc["icon_pixmap"], vc["soft"]

    class _Tabs(QtWidgets.QWidget):
        """The preview's tabs as a row of chips that wraps when the pane is narrow (a tab bar scrolls)."""

        currentChanged = QtCore.Signal(int)

        def __init__(self, flow_class):
            super().__init__()
            self.setObjectName("bwVaultPreviewTabs")
            flow = flow_class(4)
            self.setLayout(flow)
            self.group = QtWidgets.QButtonGroup(self)
            self.group.setExclusive(True)
            self.buttons = []
            for i, (_key, text) in enumerate(TABS):
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.setCheckable(True)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                b.clicked.connect(lambda _c=False, n=i: self.setCurrentIndex(n))
                self.group.addButton(b, i)
                flow.addWidget(b)
                self.buttons.append(b)
            self.buttons[0].setChecked(True)
            self._index = 0

        def setCurrentIndex(self, index):
            self.buttons[index].setChecked(True)
            if index != self._index:
                self._index = index
                self.currentChanged.emit(index)

        def currentIndex(self):
            return self._index

        def setTabText(self, index, text):
            self.buttons[index].setText(text)

    class MeshView(QtWidgets.QWidget):
        """A file's preview mesh in 3D: drag to orbit, scroll to zoom, right-drag to pan, double-click to
        fit. Frames are drawn off screen and painted as an image."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwVaultMeshView")
            self.setMinimumHeight(220)
            self.setMouseTracking(False)
            self.image = None
            self.message = ""
            self.root = None
            self.content = None
            self.camera = None
            self.centre = None
            self.section_on = False
            self.renderer = None
            self._size = None
            self._drag = None
            self._fast = False
            self._timer = QtCore.QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._render)
            self._frame = os.path.join(tempfile.gettempdir(), f"bw-preview-{os.getpid()}.bmp")
            self.node_cache = {}
            self.background = QtGui.QColor("#FFFFFF")
            self.message_colour = QtGui.QColor("#6E6E73")
            self.border_colour = QtGui.QColor("#D9D9DE")

        # scene
        def set_colours(self, background, text, border):
            self.background, self.message_colour, self.border_colour = background, text, border

        def show_meshes(self, drawn):
            from pivy import coin

            self.clear()
            if not drawn:
                self.message = "No 3D preview yet. It is made when the file is next saved, or now with Make Previews."
                self.update()
                return False
            self.renderer = None  # a new scene: the off-screen renderer keeps what it drew before (1 Nov)
            root = coin.SoSeparator()
            root.ref()
            camera = coin.SoOrthographicCamera()
            root.addChild(camera)
            environment = coin.SoEnvironment()
            environment.ambientIntensity = 0.9
            root.addChild(environment)
            self.lights = []
            # As the ISO pictures: a headlight a little above the eye and a soft fill from below, both turned
            # with the camera at every frame (_render), so the side you look at is always lit.
            for direction, intensity in (((0.25, -0.35, -1.0), 1.0), ((-0.4, 0.6, -0.6), 0.4)):
                light = coin.SoDirectionalLight()
                light.intensity = intensity
                root.addChild(light)
                self.lights.append((light, direction))
            self.section_on = False
            content = coin.SoSeparator()
            self.drawn = drawn
            self._fill_content(content, None)
            root.addChild(content)
            self.root, self.content, self.camera = root, content, camera
            self.fit()
            return True

        def _fill_content(self, content, cut):
            """The parts into ``content``; with ``cut`` = (point, normal), only the triangles behind the plane
            through ``point`` facing ``normal`` (the section: the off-screen renderer ignores Coin clip planes
            and sets its own near plane, 1 Nov)."""
            from pivy import coin

            content.removeAllChildren()
            hints = coin.SoShapeHints()
            hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
            hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE  # two-sided: a section shows insides
            hints.creaseAngle = 0.6
            content.addChild(hints)
            for part, matrix in self.drawn:
                sep = coin.SoSeparator()
                transform = coin.SoMatrixTransform()
                transform.matrix.setValue(coin.SbMatrix(*coin_matrix(list(matrix))))
                sep.addChild(transform)
                node = self._part_node(part) if cut is None else self._cut_node(part, matrix, cut)
                if node is not None:
                    sep.addChild(node)
                    content.addChild(sep)

        def _cut_node(self, part, matrix, cut):
            import numpy as np
            from pivy import coin

            self._part_node(part)  # its material and points
            whole = self.node_cache[id(part)][0]
            pts = np.frombuffer(part["points"], dtype=np.float32).reshape(-1, 3).astype(np.float64)
            tri = np.frombuffer(part["triangles"], dtype=np.uint32).reshape(-1, 3)
            m = np.array(matrix, dtype=np.float64).reshape(4, 4)
            world = pts @ m[:3, :3].T + m[:3, 3]
            point, normal = cut
            side = (world - np.array(point)) @ np.array(normal)  # > 0: in front of the cut, towards you
            keep = side[tri].max(axis=1) <= 0.0
            if not keep.any():
                return None
            index = np.hstack([tri[keep], -np.ones((int(keep.sum()), 1), dtype=np.int64)]).ravel().tolist()
            faces = coin.SoIndexedFaceSet()
            faces.coordIndex.setValues(0, len(index), index)
            node = coin.SoSeparator()
            node.addChild(whole.getChild(0))  # material
            node.addChild(whole.getChild(1))  # points
            node.addChild(faces)
            return node

        def _part_node(self, part):
            from pivy import coin

            key = id(part)
            if key not in self.node_cache:
                pts, tri = part["points"], part["triangles"]
                node = coin.SoSeparator()
                material = coin.SoMaterial()
                material.diffuseColor.setValue(*part["color"])
                material.ambientColor.setValue(*[0.45 * c for c in part["color"]])
                material.specularColor.setValue(0.25, 0.25, 0.25)
                material.shininess = 0.35
                coords = coin.SoCoordinate3()
                coords.point.setValues(0, len(pts) // 3, [pts[i:i + 3] for i in range(0, len(pts), 3)])
                faces = coin.SoIndexedFaceSet()
                index = []
                for i in range(0, len(tri), 3):
                    index += (tri[i], tri[i + 1], tri[i + 2], -1)
                faces.coordIndex.setValues(0, len(index), index)
                for child in (material, coords, faces):
                    node.addChild(child)
                self.node_cache[key] = (node, part)  # keep the part alive: id() stays unique
            return self.node_cache[key][0]

        def clear(self):
            if self.root is not None:
                self.root.unref()
            self.root = self.content = self.camera = None
            self.section_on = False
            self.image = None
            self.message = ""
            self.update()

        def forget(self):
            """Drop the cached nodes (the vault changed)."""
            self.clear()
            self.node_cache = {}

        # camera
        def fit(self):
            from pivy import coin
            import bruceworx_preview

            if self.camera is None:
                return
            self.camera.orientation.setValue(coin.SbRotation(*bruceworx_preview.ISO))
            w, h = max(self.width(), 10), max(self.height(), 10)
            region = coin.SbViewportRegion(w, h)
            self.camera.viewAll(self.content, region, 1.08)
            box = coin.SoGetBoundingBoxAction(region)
            box.apply(self.content)
            bb = box.getBoundingBox()
            self.centre = bb.getCenter()
            dx, dy, dz = bb.getSize().getValue()
            self.radius = 0.5 * max((dx * dx + dy * dy + dz * dz) ** 0.5, 1e-6)
            self._clip_range()
            self.request()

        def _clip_range(self):
            d = self.camera.focalDistance.getValue()
            self.camera.nearDistance = max(d * 1e-3, d - 2.2 * self.radius)
            self.camera.farDistance = d + 2.2 * self.radius

        def set_section(self, on):
            """Cut the model through its centre, square to the way you look at it now: the half towards you
            goes, the inside shows. The cut stays put while you orbit; press again to close it."""
            from pivy import coin

            self.section_on = bool(on)
            if self.camera is None:
                return
            cut = None
            if self.section_on:
                back = self.camera.orientation.getValue().multVec(coin.SbVec3f(0, 0, 1))
                cut = (list(self.centre.getValue()), list(back.getValue()))
            fresh = coin.SoSeparator()  # a new node and a new renderer: the renderer keeps what it drew
            self._fill_content(fresh, cut)
            self.root.replaceChild(self.content, fresh)
            self.content = fresh
            self.renderer = None
            self.request()

        def _orbit(self, dx, dy):
            from pivy import coin

            cam = self.camera
            rot = cam.orientation.getValue()
            right = rot.multVec(coin.SbVec3f(1, 0, 0))
            turn = coin.SbRotation(right, -dy * 0.01) * coin.SbRotation(coin.SbVec3f(0, 0, 1), -dx * 0.01)
            new = rot * turn
            cam.orientation.setValue(new)
            back = new.multVec(coin.SbVec3f(0, 0, 1))
            dist = cam.focalDistance.getValue()
            cam.position.setValue(self.centre + back * dist)

        def _pan(self, dx, dy):
            from pivy import coin

            cam = self.camera
            rot = cam.orientation.getValue()
            scale = cam.height.getValue() / max(self.height(), 1)
            move = rot.multVec(coin.SbVec3f(-dx * scale, dy * scale, 0))
            cam.position.setValue(cam.position.getValue() + move)
            self.centre = self.centre + move

        # drawing
        def request(self, fast=False):
            self._fast = fast
            if not self._timer.isActive():
                self._timer.start(0)

        def _render(self):
            if self.root is None or not self.isVisible():
                return
            dpr = 1.0 if self._fast else self.devicePixelRatioF()
            w, h = max(int(self.width() * dpr), 16), max(int(self.height() * dpr), 16)
            if self.renderer is None or self._size != (w, h):
                self.renderer = FreeCADGui.SoQtOffscreenRenderer(w, h)
                self._size = (w, h)
            c = self.background
            self.renderer.setBackgroundColor(c.redF(), c.greenF(), c.blueF())
            from pivy import coin

            turn = self.camera.orientation.getValue()
            for light, direction in self.lights:
                light.direction.setValue(turn.multVec(coin.SbVec3f(*direction)))
            if self.renderer.render(self.root):
                self.renderer.writeToImage(self._frame)
                image = QtGui.QImage(self._frame)
                image.setDevicePixelRatio(dpr)
                self.image = image
                self.update()

        def paintEvent(self, _event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            card = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QtGui.QPainterPath()
            path.addRoundedRect(card, 8, 8)
            painter.fillPath(path, self.background)
            if self.image is not None:
                painter.setClipPath(path)
                painter.drawImage(QtCore.QRectF(self.rect()), self.image)
            elif self.message:
                painter.setPen(self.message_colour)
                painter.drawText(self.rect().adjusted(12, 12, -12, -12), QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                                 self.message)
            painter.setClipping(False)
            painter.setPen(QtGui.QPen(self.border_colour, 1))
            painter.drawPath(path)
            painter.end()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if self.camera is not None:
                self.camera.aspectRatio = max(self.width(), 1) / max(self.height(), 1)
            self.request()

        def showEvent(self, event):
            super().showEvent(event)
            self.request()

        def mousePressEvent(self, event):
            self._drag = (event.position(), event.button())

        def mouseMoveEvent(self, event):
            if self._drag is None or self.camera is None:
                return
            start, button = self._drag
            delta = event.position() - start
            self._drag = (event.position(), button)
            if button == QtCore.Qt.LeftButton:
                self._orbit(delta.x(), delta.y())
            else:
                self._pan(delta.x(), delta.y())
            self._clip_range()
            self.request(fast=True)

        def mouseReleaseEvent(self, _event):
            self._drag = None
            self.request()  # a sharp frame where the drag stopped

        def mouseDoubleClickEvent(self, _event):
            self.fit()

        def wheelEvent(self, event):
            if self.camera is None:
                return
            step = event.angleDelta().y() / 120.0
            self.camera.height = self.camera.height.getValue() * (0.88 ** step)
            self.request()

    class NavPane(QtWidgets.QTreeWidget):
        """Projects, views and categories; a click sets the list's scope."""

        scopeChosen = QtCore.Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("bwVaultNav")
            self.setColumnCount(2)
            self.setHeaderHidden(True)
            self.setRootIsDecorated(False)
            self.setIndentation(14)
            self.setFrameShape(QtWidgets.QFrame.NoFrame)
            self.setUniformRowHeights(True)
            self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.setFocusPolicy(QtCore.Qt.NoFocus)
            self.header().setStretchLastSection(False)
            self.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            self.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            self.itemClicked.connect(self._clicked)
            self._sig = None

        def fill(self, entries, current):
            sig = (tuple(entries), current)
            if sig == self._sig:
                return
            self._sig = sig
            self.blockSignals(True)
            self.clear()
            small = QtGui.QFont(self.font())
            small.setPixelSize(10)
            small.setWeight(QtGui.QFont.DemiBold)
            small.setLetterSpacing(QtGui.QFont.PercentageSpacing, 108)
            for key, label, count, depth, kind in entries:
                item = QtWidgets.QTreeWidgetItem([label.upper() if kind == "section" else label,
                                                  "" if count is None else str(count)])
                item.setData(0, QtCore.Qt.UserRole, key)
                item.setToolTip(0, label)
                if kind == "section":
                    item.setFlags(QtCore.Qt.NoItemFlags)
                    item.setFont(0, small)
                    item.setSizeHint(0, QtCore.QSize(10, 26))
                else:
                    if depth:
                        item.setText(0, "    " + label)
                    item.setForeground(1, self.palette().color(QtGui.QPalette.PlaceholderText))
                    item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.addTopLevelItem(item)
                if key == current and kind != "section":
                    item.setSelected(True)
            self.blockSignals(False)

        def _clicked(self, item, _column):
            key = item.data(0, QtCore.Qt.UserRole)
            if key:
                self.scopeChosen.emit(key)

    class PreviewPane(QtWidgets.QWidget):
        """The selected file: header, tabs (Preview, Properties, Drawings, Uses, Where used, History)."""

        def __init__(self, panel, parent=None):
            super().__init__(parent)
            self.panel = panel
            self.setObjectName("bwVaultPreview")
            self.pn = None
            self.mode = "3d"
            self.tab = "preview"
            self.mesh_cache = {}
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(8)
            head = QtWidgets.QHBoxLayout()
            head.setSpacing(8)
            self.icon = QtWidgets.QLabel()
            self.icon.setFixedSize(24, 24)
            titles = QtWidgets.QVBoxLayout()
            titles.setSpacing(0)
            self.title = QtWidgets.QLabel("")
            self.title.setObjectName("bwVaultPreviewTitle")
            self.title.setWordWrap(True)
            title_font = QtGui.QFont(self.font())
            title_font.setPixelSize(14)
            title_font.setWeight(QtGui.QFont.DemiBold)
            self.title.setFont(title_font)
            self.sub = QtWidgets.QLabel("")
            self.sub.setObjectName("bwVaultPreviewSub")
            self.sub.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            titles.addWidget(self.title)
            titles.addWidget(self.sub)
            self.state = QtWidgets.QLabel("")
            self.state.setObjectName("bwVaultPreviewState")
            head.addWidget(self.icon, 0, QtCore.Qt.AlignTop)
            head.addLayout(titles, 1)
            head.addWidget(self.state, 0, QtCore.Qt.AlignTop)
            lay.addLayout(head)
            self.tabs = _Tabs(vc["ChipFlow"])
            self.tabs.currentChanged.connect(self._tab_changed)
            lay.addWidget(self.tabs)
            self.pages = QtWidgets.QStackedWidget()
            lay.addWidget(self.pages, 1)

            # Preview
            page = QtWidgets.QWidget()
            pl = QtWidgets.QVBoxLayout(page)
            pl.setContentsMargins(0, 4, 0, 0)
            pl.setSpacing(8)
            bar = QtWidgets.QHBoxLayout()
            bar.setSpacing(4)
            self.modes = {}
            group = QtWidgets.QButtonGroup(self)
            for key, text in (("3d", "3D"), ("iso", "ISO"), ("sheet", "Drawing")):
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.setCheckable(True)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
                group.addButton(b)
                bar.addWidget(b)
                self.modes[key] = b
            bar.addSpacing(8)
            self.section = QtWidgets.QPushButton("Section")
            self.section.setObjectName("bwVaultChip")
            self.section.setCheckable(True)
            self.section.toggled.connect(lambda on: self.view.set_section(on))
            self.fit_button = QtWidgets.QPushButton("Fit")
            self.fit_button.setObjectName("bwVaultChip")
            self.fit_button.clicked.connect(lambda: self.view.fit())
            bar.addWidget(self.section)
            bar.addWidget(self.fit_button)
            bar.addStretch(1)
            self.make = QtWidgets.QPushButton("Make Previews")
            self.make.setToolTip("Make the ISO picture and 3D preview of every file that has none yet "
                                 "(kept on this Mac; the vault is not changed)")
            self.make.clicked.connect(lambda: self.panel.refresh_pictures())
            self.make.hide()
            bar.addWidget(self.make)
            pl.addLayout(bar)
            self.stage = QtWidgets.QStackedWidget()
            self.stage.setMinimumHeight(240)
            self.view = MeshView()
            self.picture = QtWidgets.QLabel()
            self.picture.setAlignment(QtCore.Qt.AlignCenter)
            self.picture.setMinimumHeight(220)
            self.stage.addWidget(self.view)
            self.stage.addWidget(self.picture)
            pl.addWidget(self.stage, 3)
            self.hint = QtWidgets.QLabel("Drag to orbit · scroll to zoom · right-drag to pan · double-click to fit")
            self.hint.setObjectName("bwVaultPreviewHint")
            pl.addWidget(self.hint)
            self.facts = QtWidgets.QFormLayout()
            self.facts.setLabelAlignment(QtCore.Qt.AlignLeft)
            self.facts.setHorizontalSpacing(14)
            self.facts.setVerticalSpacing(4)
            pl.addLayout(self.facts)
            pl.addStretch(1)
            self.pages.addWidget(page)

            # Properties
            self.props_page = QtWidgets.QWidget()
            self.props = QtWidgets.QFormLayout(self.props_page)
            self.props.setContentsMargins(0, 8, 0, 0)
            self.props.setHorizontalSpacing(14)
            self.props.setVerticalSpacing(6)
            self.pages.addWidget(self.props_page)

            # Drawings, Uses, Where used, History
            self.lists = {}
            for key in ("drawings", "simulations", "uses", "used", "history"):
                if key == "simulations":  # PDM-14: a card per simulation
                    self.sim_page = QtWidgets.QScrollArea()
                    self.sim_page.setWidgetResizable(True)
                    self.sim_page.setFrameShape(QtWidgets.QFrame.NoFrame)
                    inner = QtWidgets.QWidget()
                    self.sim_box = QtWidgets.QVBoxLayout(inner)
                    self.sim_box.setContentsMargins(0, 6, 0, 0)
                    self.sim_box.setSpacing(8)
                    self.sim_page.setWidget(inner)
                    self.pages.addWidget(self.sim_page)
                    continue
                page = QtWidgets.QWidget()
                v = QtWidgets.QVBoxLayout(page)
                v.setContentsMargins(0, 6, 0, 0)
                v.setSpacing(6)
                lst = QtWidgets.QListWidget()
                lst.setObjectName("bwVaultPreviewList")
                lst.setFrameShape(QtWidgets.QFrame.NoFrame)
                lst.setWordWrap(True)
                lst.setIconSize(QtCore.QSize(16, 16))
                lst.itemActivated.connect(self._go)
                lst.itemClicked.connect(self._go)
                note = QtWidgets.QLabel("")
                note.setObjectName("bwVaultPreviewHint")
                note.setWordWrap(True)
                v.addWidget(lst, 1)
                v.addWidget(note)
                if key == "drawings":
                    row = QtWidgets.QHBoxLayout()
                    self.export_button = QtWidgets.QPushButton("Export Package…")
                    self.export_button.setToolTip("A PDF of each drawing (WIP ones marked IN PROGRESS) and package.csv, "
                                                  "into a folder you choose")
                    self.export_button.clicked.connect(self._export)
                    row.addWidget(self.export_button)
                    row.addStretch(1)
                    v.addLayout(row)
                self.lists[key] = (lst, note)
                self.pages.addWidget(page)
            self.show_item(None)

        # --- data
        def _row(self, pn):
            return self.panel.rows.get(pn) or {}

        def _structure(self):
            return self.panel.structure

        def show_item(self, pn):
            self.pn = pn
            row = self._row(pn) if pn else {}
            kind = row.get("item_type") or ""
            if not pn or not row or kind == "change":
                self.icon.clear()
                self.title.setText("Select a file")
                self.sub.setText("Its 3D preview, drawings, where it is used and its history show here.")
                self.state.setText("")
                self.view.clear()
                self.picture.clear()
                for k in ("drawings", "uses", "used", "history"):
                    self.lists[k][0].clear()
                    self.lists[k][1].setText("")
                self._clear_form(self.facts)
                self._clear_form(self.props)
                self.tabs.setVisible(False)
                self.pages.setVisible(False)
                return
            self.tabs.setVisible(True)
            self.pages.setVisible(True)
            self.icon.setPixmap(icon_pixmap(self._icon_kind(kind), self.panel.type_colour(kind), 22,
                                            self.devicePixelRatioF()))
            desc = row.get("description") or ""
            self.title.setText(desc or row.get("display") or pn)
            self.sub.setText(row.get("display") or pn)
            self.state.setText(row.get("state") or "")
            self._update_counts()
            self.tabs.setCurrentIndex([k for k, _t in TABS].index(self.tab))
            self._fill_tab()

        @staticmethod
        def _icon_kind(kind):
            return {"assembly": "assembly", "drawing": "drawing", "simulation": "simulation"}.get(kind, "part")

        def _update_counts(self):
            pn, s = self.pn, self._structure()
            from heuretes_freecad import vault_trees

            counts = {"drawings": len(self._drawings()), "simulations": len(self._sims()), "uses": len(s["bom"].get(pn, ())),
                      "used": len(vault_trees.used_in(s).get(pn, ())), "history": len(self._versions())}
            for i, (key, text) in enumerate(TABS):
                self.tabs.setTabText(i, f"{text} {counts[key]}" if key in counts else text)

        def _versions(self):
            session = self.panel.session()
            if session is None or not self.pn:
                return []
            return list(session.vault.versions.get(self.pn, []))

        def _drawings(self):
            from heuretes_freecad import vault_trees

            s, pn = self._structure(), self.pn
            if self._row(pn).get("item_type") == "drawing":
                return [pn]
            out = list(s["drawings"].get(pn, ()))
            if self._row(pn).get("item_type") == "assembly":  # the package of an assembly: everything below
                for p in sorted(vault_trees.contents(s, pn) - {pn}):
                    if s["items"].get(p, {}).get("type") == "drawing" and p not in out:
                        out.append(p)
            return out

        def _tab_changed(self, index):
            self.tab = TABS[index][0] if 0 <= index < len(TABS) else "preview"
            self.pages.setCurrentIndex(index)
            if self.pn:
                self._fill_tab()

        def _fill_tab(self):
            fill = {"preview": self._fill_preview, "props": self._fill_props, "drawings": self._fill_drawings,
                    "simulations": self._fill_simulations, "uses": self._fill_uses, "used": self._fill_used, "history": self._fill_history}[self.tab]
            fill()

        @staticmethod
        def _clear_form(form):
            while form.rowCount():
                form.removeRow(0)

        def _fact(self, form, label, text):
            name = QtWidgets.QLabel(label)
            name.setObjectName("bwVaultPreviewHint")
            value = QtWidgets.QLabel(text)
            value.setWordWrap(True)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            form.addRow(name, value)

        def set_mode(self, mode):
            self.mode = mode
            self._fill_preview()

        def _fill_preview(self):
            pn, row = self.pn, self._row(self.pn)
            kind = row.get("item_type")
            sheets = [d for d in self._drawings() if d]
            has_3d = kind in ("part", "assembly", "cots", "simulation")
            mode = self.mode
            if kind == "drawing":
                mode = "sheet"
            elif mode == "sheet" and not sheets:
                mode = "3d"
            self.modes["3d"].setVisible(has_3d)
            self.modes["iso"].setVisible(has_3d)
            self.modes["sheet"].setVisible(bool(sheets))
            self.modes[mode].setChecked(True)
            self.section.setVisible(mode == "3d")
            self.fit_button.setVisible(mode == "3d")
            self.hint.setVisible(mode == "3d")
            if mode == "3d":
                self.stage.setCurrentWidget(self.view)
                session = self.panel.session()
                drawn = meshes_for(session, pn, self.mesh_cache) if session is not None else []
                self.section.blockSignals(True)
                self.section.setChecked(False)
                self.section.blockSignals(False)
                lc = self.panel.list.colour
                self.view.set_colours(lc("card"), lc("secondary"), lc("border"))
                shown = self.view.show_meshes(drawn)
                self.make.setVisible(not shown and self.panel.pictures is None)
                self.section.setEnabled(shown)
                self.fit_button.setEnabled(shown)
            else:
                self.make.hide()
                self.view.clear()
                self.stage.setCurrentWidget(self.picture)
                target = row if mode == "iso" else self._row(sheets[0])
                path = self.panel.thumb_file(target) if target else ""
                pixmap = QtGui.QPixmap(path) if path else QtGui.QPixmap()
                if pixmap.isNull():
                    self.picture.setText("No picture yet." if mode == "iso" else "No sheet picture yet.\n"
                                         "Save the drawing once to make it.")
                else:
                    size = self.stage.size() * self.devicePixelRatioF()
                    scaled = pixmap.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    scaled.setDevicePixelRatio(self.devicePixelRatioF())
                    self.picture.setPixmap(scaled)
            self._clear_form(self.facts)
            versions = self._versions()
            self._fact(self.facts, "Type", (kind or "").capitalize())
            self._fact(self.facts, "Revision", f"{row.get('revision') or '—'} · {len(versions)} version"
                                               f"{'' if len(versions) == 1 else 's'}")
            if versions:
                self._fact(self.facts, "Last check-in", history_rows(versions)[0][2] + " UTC")
            if kind != "drawing":
                text, _role = drawing_cell(self._structure(), pn, row)
                self._fact(self.facts, "Drawing", text or "—")

        def _fill_props(self):
            from heuretes_freecad import vault_trees

            pn, row = self.pn, self._row(self.pn)
            self._clear_form(self.props)
            for label, text in (("Part number", pn), ("Revision", row.get("revision") or "—"),
                                ("Description", row.get("description") or "—"), ("Type", row.get("item_type") or "—"),
                                ("State", row.get("state") or "—"), ("Category", row.get("category") or "Uncategorized"),
                                ("Project", vault_trees.program_code(pn) or "—"), ("Status", row.get("status_text") or "—"),
                                ("Former numbers", row.get("former") or "—"), ("File", f"{pn}.vlsim" if row.get("item_type") == "simulation" else f"{pn}.FCStd")):
                self._fact(self.props, label, text)

        def _item(self, lst, pn, extra=""):
            row = self._row(pn)
            kind = row.get("item_type") or "part"
            text = f"{row.get('display') or pn}\n{row.get('description') or ''}"
            item = QtWidgets.QListWidgetItem(text + (f"   {extra}" if extra else ""))
            item.setData(QtCore.Qt.UserRole, pn)
            item.setIcon(QtGui.QIcon(icon_pixmap(self._icon_kind(kind), self.panel.type_colour(kind), 16,
                                                 self.devicePixelRatioF())))
            lst.addItem(item)

        def _fill_drawings(self):
            lst, note = self.lists["drawings"]
            lst.clear()
            sheets = self._drawings()
            for d in sheets:
                self._item(lst, d, self._row(d).get("state") or "")
            self.export_button.setEnabled(bool(sheets))
            row = self._row(self.pn)
            if sheets:
                note.setText("Export Package writes a PDF of each drawing, WIP drawings marked IN PROGRESS, "
                             "and package.csv. To build your own, select rows in the list and right-click.")
            elif row.get("item_type") in ("part", "assembly"):
                note.setText("No drawing yet. Bought-in (COTS) parts use the supplier's drawing.")
            else:
                note.setText("")

        # --- PDM-14: simulations
        def _sims(self):
            """The simulation rows (vault_trees.simulations) of the selected part, or of a simulation itself."""
            info = self.panel.sim_info
            if self._row(self.pn).get("item_type") == "simulation":
                return [info[self.pn]] if self.pn in info else []
            return [r for r in info.values() if r["of"] == self.pn]

        def _report_for(self, sim_pn):
            """The newest <P/N>_*_report.pdf of a simulation in this user's work folder, or ""."""
            session = self.panel.session()
            if session is None:
                return ""
            from heuretes_freecad import sim_reports

            found = sim_reports.reports_beside(session.work.path(sim_pn))
            return found[0] if found else ""

        def _fill_simulations(self):
            while self.sim_box.count():
                item = self.sim_box.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()  # at once: deleteLater alone leaves it drawn until the next event loop turn
                    widget.setParent(None)
                    widget.deleteLater()
            sims = self._sims()
            row = self._row(self.pn)
            can_make = row.get("item_type") in ("part", "assembly", "cots")
            if can_make:
                make = QtWidgets.QPushButton("New simulation of this part")
                make.setObjectName("bwVaultChip")
                make.setToolTip("Number a simulation of this part, copy its solid into a new .vlsim file and "
                                "check it out to you")
                make.clicked.connect(lambda: self._new_simulation())
                bar = QtWidgets.QHBoxLayout()
                bar.addWidget(make)
                imp = QtWidgets.QPushButton("Import study file…")
                imp.setObjectName("bwVaultChip")
                imp.setToolTip("Bring in an existing study (.FCStd or .vlsim) as a simulation of this part")
                imp.clicked.connect(lambda: self._import_simulation())
                bar.addWidget(imp)
                bar.addStretch(1)
                holder = QtWidgets.QWidget()
                holder.setLayout(bar)
                self.sim_box.addWidget(holder)
            for sim in sims:
                self.sim_box.addWidget(self._card(sim))
            if not sims:
                note = QtWidgets.QLabel("No simulation of this part yet." if can_make else "")
                note.setObjectName("bwVaultPreviewHint")
                note.setWordWrap(True)
                self.sim_box.addWidget(note)
            self.sim_box.addStretch(1)

        def _card(self, sim):
            """A simulation's card: its picture, summary, whether it is current, and the actions. Read from the
            summary inside the file; the study file itself is fetched only by Open study."""
            lc = self.panel.list.colour
            frame = QtWidgets.QFrame()
            frame.setObjectName("bwVaultSimCard")
            frame.setStyleSheet(f"#bwVaultSimCard {{ border: 1px solid {lc('border').name()}; border-radius: 8px; }}")
            lay = QtWidgets.QHBoxLayout(frame)
            lay.setContentsMargins(8, 8, 8, 8)
            sim_row = self._row(sim["pn"])
            pic = QtWidgets.QLabel()
            pic.setFixedSize(120, 90)
            pic.setAlignment(QtCore.Qt.AlignCenter)
            path = self.panel.thumb_file(sim_row) if sim_row else ""
            pix = QtGui.QPixmap(path) if path else QtGui.QPixmap()
            if pix.isNull():
                pic.setText("no picture")
            else:
                pic.setPixmap(pix.scaled(120, 90, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            lay.addWidget(pic)
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(2)
            title = QtWidgets.QLabel(f"<b>{sim['display']}</b>  {sim['description']}")
            title.setWordWrap(True)
            col.addWidget(title)
            for text in (sim["study"], sim["result"]):
                if text:
                    line = QtWidgets.QLabel(text)
                    line.setObjectName("bwVaultPreviewHint")
                    line.setWordWrap(True)
                    col.addWidget(line)
            role = {"current": "ok", "part changed": "warn"}.get(sim["judgement"], "secondary")
            chip = QtWidgets.QLabel(sim["judgement"].upper() if sim["judgement"] != "unknown" else "NOT CHECKED")
            chip.setStyleSheet(f"color: {lc(role).name()}; font-weight: 600;")
            chip.setToolTip({"current": "The study's copy of the solid matches the part's latest version.",
                             "part changed": "The part was checked in with different geometry after this study "
                                             "was made. Open the study and refresh its copy, or re-solve.",
                             "unknown": "Either the part or the study has no geometry fingerprint yet (a file "
                                        "from before PDM-14, or not saved since)."}[sim["judgement"]])
            col.addWidget(chip)
            buttons = QtWidgets.QHBoxLayout()
            for text, fn in (("Open study", lambda _c=False, p=sim["pn"]: self._open_study(p)),
                             ("Check out", lambda _c=False, p=sim["pn"]: self._check_out(p))):
                b = QtWidgets.QPushButton(text)
                b.setObjectName("bwVaultChip")
                b.clicked.connect(fn)
                buttons.addWidget(b)
            report = self._report_for(sim["pn"])
            if report:
                b = QtWidgets.QPushButton("Report PDF")
                b.setObjectName("bwVaultChip")
                b.clicked.connect(lambda _c=False, p=report: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p)))
                buttons.addWidget(b)
            buttons.addStretch(1)
            col.addLayout(buttons)
            lay.addLayout(col, 1)
            return frame

        def _open_study(self, pn):
            import bruceworx_vaultclient as vcl

            QtCore.QTimer.singleShot(0, lambda: vcl.open_from_vault(pn))

        def _check_out(self, pn):
            import bruceworx_vaultclient as vcl

            QtCore.QTimer.singleShot(0, lambda: vcl.run_for("BruceWorx_VaultCheckOut", pn))

        def _new_simulation(self):
            import bruceworx_sim_vault_ui as ui

            QtCore.QTimer.singleShot(0, lambda: ui.new_simulation_dialog(self.pn))

        def _import_simulation(self):
            import bruceworx_sim_vault_ui as ui

            QtCore.QTimer.singleShot(0, lambda: ui.import_study_dialog(self.pn))

        def _fill_uses(self):
            lst, note = self.lists["uses"]
            lst.clear()
            for child, qty in self._structure()["bom"].get(self.pn, ()):
                self._item(lst, child, f"×{qty:g}" if isinstance(qty, float) else f"×{qty}")
            kind = self._row(self.pn).get("item_type")
            note.setText("" if lst.count() else ("This assembly has no lines yet." if kind == "assembly"
                                                 else "Only an assembly uses other files."))

        def _fill_used(self):
            from heuretes_freecad import vault_trees

            lst, note = self.lists["used"]
            lst.clear()
            if self._row(self.pn).get("item_type") == "drawing":
                owner = vault_trees.drawing_owner(self._structure(), self.pn)
                if owner:
                    self._item(lst, owner, "drawing of")
                note.setText("" if owner else "The drawing's part is not in the vault.")
                return
            for parent, qty in vault_trees.used_in(self._structure()).get(self.pn, ()):
                self._item(lst, parent, f"×{qty:g}" if isinstance(qty, float) else f"×{qty}")
            note.setText("" if lst.count() else "Not used in any assembly.")

        def _fill_history(self):
            lst, note = self.lists["history"]
            lst.clear()
            results = None
            session = self.panel.session()
            if session is not None and self._row(self.pn).get("item_type") == "simulation":
                from heuretes_freecad import vault_simulations as vsim

                results = {v.sha256: flag for v, flag in vsim.results_by_version(session.vault, self.pn)}
            for label, comment, when, size in history_rows(self._versions(), results):
                item = QtWidgets.QListWidgetItem(f"{label}  {comment}\n{when} UTC" + (f" · {size}" if size else ""))
                item.setFlags(QtCore.Qt.ItemIsEnabled)
                lst.addItem(item)
            note.setText("" if lst.count() else "No versions yet.")

        def _go(self, item):
            pn = item.data(QtCore.Qt.UserRole)
            if pn:
                self.panel.select(pn)

        def _export(self):
            try:
                import bruceworx_package

                bruceworx_package.export_dialog(self.panel, self._drawings())
            except ImportError:
                pass

        def forget(self):
            self.mesh_cache = {}
            self.view.forget()

    return {"MeshView": MeshView, "NavPane": NavPane, "PreviewPane": PreviewPane}
