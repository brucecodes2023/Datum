# SPDX-License-Identifier: LGPL-2.1-or-later
"""Fast Open (PRF-03, Bruce 2 Nov 2026): big assemblies in seconds, from the vault's preview meshes.

Bruce: "for assemblies, can we make them open faster and be able to look through them when they are larger
like the printer? Fast open or something?" The TL-P01 printer (364 files, 4983 objects) took 25.8 s to open in
Heuretes FreeCAD: 9.6 s reading the files, the rest building the exact display. Fast Open reads no FreeCAD document:
it draws the light preview mesh every vault file already carries (PDM-13; in the file, or in this Mac's cache
for versions made before), each part's triangles once and placed as often as the assembly uses it.

* Bruce's choice: assemblies of more than ``FastOpenLimit`` parts (default 100; 0 = never) open fast from the
  Files tab; smaller ones open as before. "Open full" loads the exact assembly; picking a component and "Open"
  loads that file alone.
* A pick selects the part that was clicked (Bruce 9 Nov: "when i select a panel it selects an entire assembly.
  I want it to select that part"): the deepest vault file the clicked body belongs to, as Assembly pick does
  in a full assembly. "Select parent" goes up a level, to the whole component at the top.
* The view is a FreeCAD 3D view of its own (orbit, zoom, the navigation cube), with a panel listing the
  components; choosing one highlights it and fits it in view. The panel folds to its title (the chevron, or
  Esc in the view) to get it out of the way.

``plan`` and ``part_count`` are FreeCAD-free and tested; the rest needs the GUI.
"""

from __future__ import annotations

import os
import time

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
LIMIT_PREF = "FastOpenLimit"
DEFAULT_LIMIT = 100
COMMAND = "BruceWorx_FastOpen"
HIGHLIGHT = (0.20, 0.55, 1.00)
CLICK_SLOP = 4  # pixels a click may move and still be a click


# ----- pure helpers (unit tested) -----------------------------------------------------------------


def _key(file_name):
    return file_name[:-6] if file_name.lower().endswith(".fcstd") else file_name


def _owned(load, key, matrix, seen, owner=(), depth=0):
    """bruceworx_previewmesh.flatten, keeping who owns each body: ([(part, matrix)], [owner], {owner: P/N}).
    An owner is the path of instance indices from the component down to the file the body is in (() for the
    component's own file), so the same part placed twice is two owners."""
    import bruceworx_previewmesh as pm

    mesh = load(key)
    if mesh is None or key in seen or depth > 12:
        return [], [], {}
    draws = [(part, matrix) for part in mesh["parts"]]
    owners = [owner] * len(draws)
    files = {owner: _key(key)}
    for i, inst in enumerate(mesh["instances"]):
        d, o, f = _owned(load, inst["file"], pm.multiply(list(matrix), inst["matrix"]), (*seen, key), (*owner, i), depth + 1)
        draws += d
        owners += o
        files.update(f)
    return draws, owners, files


def plan(load, pn):
    """The components of assembly ``pn``: [{"pn", "object", "matrix", "draws": [(part, matrix)], "owners",
    "files"}], one per instance directly under it (its own bodies, if any, first under its own number).
    ``owners[j]`` is the owner of draw j and ``files`` the P/N of each owner (see _owned). ``load(key)``
    returns a decoded preview mesh or None (bruceworx_previewmesh.decode)."""
    import bruceworx_previewmesh as pm

    top = load(pn)
    if top is None:
        return []
    out = []
    if top["parts"]:
        out.append({"pn": pn, "object": "", "matrix": list(pm.IDENTITY),
                    "draws": [(part, list(pm.IDENTITY)) for part in top["parts"]],
                    "owners": [()] * len(top["parts"]), "files": {(): pn}})
    for inst in top["instances"]:
        draws, owners, files = _owned(load, inst["file"], inst["matrix"], (pn,))
        out.append({"pn": _key(inst["file"]), "object": inst.get("object", ""), "matrix": inst["matrix"],
                    "draws": draws, "owners": owners, "files": files})
    return out


def owner_at(owners, j):
    """The owner a pick on draw ``j`` selects: the file that body is in (the part)."""
    return tuple(owners[j])


def draws_of(owners, owner, deep=True):
    """Indices of the draws ``owner`` covers: its own bodies and, when ``deep``, everything placed under it
    (a pick takes only the clicked file's own bodies, a sub-assembly's included)."""
    n = len(owner)
    if not deep:
        return [j for j, o in enumerate(owners) if tuple(o) == tuple(owner)]
    return [j for j, o in enumerate(owners) if tuple(o[:n]) == tuple(owner)]


def part_count(components) -> int:
    """How many parts the assembly places (every drawn body, through every level)."""
    return sum(len(c["draws"]) for c in components)


def should_fast_open(count, limit) -> bool:
    return bool(limit) and count > limit


def limit() -> int:
    import FreeCAD

    return int(FreeCAD.ParamGet(PREFS_PATH).GetInt(LIMIT_PREF, DEFAULT_LIMIT))


# ----- loading from the vault ---------------------------------------------------------------------


def loader(session):
    """load(key) for :func:`plan`: a P/N's preview mesh from its work copy, its vault version or the cache."""
    import bruceworx_previewmesh as pm
    import bruceworx_vaultbrowser as vb

    cache = {}

    def load(key):
        p = _key(key)
        if p not in cache:
            try:
                data = vb.mesh_source(session, p)
                cache[p] = pm.decode(data) if data else None
            except ValueError:
                cache[p] = None
        return cache[p]

    return load


# ----- the view (FreeCAD GUI) ---------------------------------------------------------------------


def _coin_matrix(m):
    return [m[c * 4 + r] for r in range(4) for c in range(4)]  # row-major -> Coin's column vectors


def build_scene(components):
    """(root, per-component [SoMaterial]) for the components; each body's triangles are one node,
    shared by every place it is drawn."""
    from pivy import coin

    root = coin.SoSeparator()
    hints = coin.SoShapeHints()
    hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
    hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE
    hints.creaseAngle = 0.6
    root.addChild(hints)
    shared = {}
    materials = []
    for index, comp in enumerate(components):
        csep = coin.SoSeparator()
        csep.setName(f"bwFast{index}")
        mats = []
        for part, matrix in comp["draws"]:
            geo = shared.get(id(part))
            if geo is None:
                geo = coin.SoSeparator()
                pts = part["points"]
                coords = coin.SoCoordinate3()
                coords.point.setValues(0, len(pts) // 3, [(pts[i], pts[i + 1], pts[i + 2]) for i in range(0, len(pts), 3)])
                tri = part["triangles"]
                faces = coin.SoIndexedFaceSet()
                idx = []
                for i in range(0, len(tri), 3):
                    idx += (tri[i], tri[i + 1], tri[i + 2], -1)
                faces.coordIndex.setValues(0, len(idx), idx)
                geo.addChild(coords)
                geo.addChild(faces)
                shared[id(part)] = geo
            sep = coin.SoSeparator()
            sep.setName(f"bwFastD{index}_{len(mats)}")
            transform = coin.SoMatrixTransform()
            transform.matrix.setValue(coin.SbMatrix(*_coin_matrix(matrix)))
            material = coin.SoMaterial()
            material.diffuseColor.setValue(*part["color"])
            material.transparency.setValue(part.get("transparency", 0.0))  # the clear panels stay clear
            material.setName(f"bwFastMat{index}")
            sep.addChild(transform)
            sep.addChild(material)
            sep.addChild(geo)
            draw_switch = coin.SoSwitch()  # Hide a part
            draw_switch.whichChild = coin.SO_SWITCH_ALL
            draw_switch.addChild(sep)
            csep.addChild(draw_switch)
            mats.append((material, tuple(part["color"])))
        switch = coin.SoSwitch()  # Hide / Show a component (look inside: hide the panels)
        switch.whichChild = coin.SO_SWITCH_ALL
        switch.addChild(csep)
        root.addChild(switch)
        materials.append(mats)
    return root, materials, len(shared)


class FastView:
    """One Fast Open view: the 3D view, its scene, and the panel over it."""

    def __init__(self, session, pn, components, seconds):
        import FreeCADGui
        from pivy import coin

        self.session, self.pn, self.components = session, pn, components
        t = time.time()
        self.root, self.materials, self.meshes = build_scene(components)
        self.build_s = round(time.time() - t, 1)
        item = session.vault.items.get(pn)
        self.title = f"{pn} · Fast Open"
        self.description = item.description if item is not None else pn
        # A temporary document's own 3D view (never modified, so closing its tab asks nothing): the
        # standard view, with the navigation cube, image export and the window's view commands.
        import FreeCAD

        self.doc = FreeCAD.newDocument("FastOpen")
        self.doc.Label = self.title
        gdoc = FreeCADGui.getDocument(self.doc.Name)
        self.view = gdoc.activeView()
        self.view.getSceneGraph().addChild(self.root)
        self.view.viewIsometric()
        self.view.fitAll()
        self.selected = None
        self.hidden = set()
        self._cb = self.view.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self._on_click)
        self.load_s = seconds
        self._panel()

    # ----- picking

    def _pick_at(self, pos):
        """(component index, owner, False) of the body under ``pos``: the part file it is in, its own bodies;
        None for empty space."""
        from pivy import coin

        region = self.view.getViewer().getSoRenderManager().getViewportRegion()
        pick = coin.SoRayPickAction(region)
        pick.setPoint(coin.SbVec2s(*pos))
        pick.apply(self.view.getViewer().getSoRenderManager().getSceneGraph())  # with the camera
        point = pick.getPickedPoint()
        if point is None:
            return None
        path = point.getPath()
        for i in range(path.getLength()):
            name = path.getNode(i).getName().getString()
            if name.startswith("bwFastD"):
                index, _sep, j = name[7:].partition("_")
                if index.isdigit() and j.isdigit():
                    index, j = int(index), int(j)
                    return index, owner_at(self.components[index]["owners"], j), False
        return None

    def _on_click(self, cb):
        """A click picks the part under it (empty space clears the pick). Only a click: the press
        that starts an orbit or a pan, and its release somewhere else, change nothing (Bruce 5 Nov: turning
        the view lost the component chosen in the list)."""
        from pivy import coin

        event = cb.getEvent()
        if not isinstance(event, coin.SoMouseButtonEvent):
            return
        if event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
            return
        pos = event.getPosition()
        pos = (int(pos[0]), int(pos[1]))
        if event.getState() == coin.SoButtonEvent.DOWN:
            self._press = pos
            return
        press, self._press = getattr(self, "_press", None), None
        if press is None or abs(pos[0] - press[0]) > CLICK_SLOP or abs(pos[1] - press[1]) > CLICK_SLOP:
            return
        self.select(self._pick_at(pos))

    def _draws(self, sel):
        """The draw indices of a selection (component index, owner, deep)."""
        index, owner, deep = sel
        return draws_of(self.components[index]["owners"], owner, deep)

    def select(self, sel, fit=False):
        """Select ``sel``: (component index, owner, deep) (see draws_of), a bare component index (the whole
        component, from the list) or None."""
        if isinstance(sel, int):
            sel = (sel, (), True)
        if self.selected is not None:
            index = self.selected[0]
            for j in self._draws(self.selected):
                material, colour = self.materials[index][j]
                material.diffuseColor.setValue(*colour)
                material.emissiveColor.setValue(0, 0, 0)
        self.selected = sel
        if sel is not None:
            for j in self._draws(sel):
                material, _colour = self.materials[sel[0]][j]
                material.diffuseColor.setValue(*HIGHLIGHT)
                material.emissiveColor.setValue(0.05, 0.12, 0.25)
            if fit:
                self._fit(sel)
        self._show_selection()

    def select_parent(self):
        """Up a level: a picked file's own bodies -> the file with all under it -> the assembly it sits in."""
        if self.selected is None:
            return
        index, owner, deep = self.selected
        if not deep and len(self._draws(self.selected)) != len(self._draws((index, owner, True))):
            self.select((index, owner, True))
        elif owner:
            self.select((index, owner[:-1], True))

    def _draw_switch(self, index, j):
        csep = self.root.getChild(index + 1).getChild(0)  # after the shape hints; the component's switch
        return csep.getChild(j)

    def _fit(self, sel):
        from pivy import coin

        index = sel[0]
        node = coin.SoSeparator()  # a temporary parent for the bounding box (the scene keeps its own)
        for j in self._draws(sel):
            node.addChild(self._draw_switch(index, j))
        region = self.view.getViewer().getSoRenderManager().getViewportRegion()
        box = coin.SoGetBoundingBoxAction(region)
        box.apply(node)
        node.removeAllChildren()
        bb = box.getBoundingBox()
        lo, hi = bb.getMin().getValue(), bb.getMax().getValue()
        try:
            import FreeCAD

            centre = FreeCAD.Vector((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
            cam = self.view.getCameraNode()
            size = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1.0)
            direction = cam.orientation.getValue().multVec(coin.SbVec3f(0, 0, -1))
            d = coin.SbVec3f(*direction.getValue())
            pos = coin.SbVec3f(centre.x, centre.y, centre.z) - d * (size * 2.5)
            cam.position.setValue(pos)
            if hasattr(cam, "height"):
                cam.height.setValue(size * 1.6)
            cam.focalDistance.setValue(size * 2.5)
        except Exception:
            pass

    def set_shown(self, sel, shown):
        """Hide or show ``sel`` (component index, owner, deep): the whole component at the top, else its draws."""
        from pivy import coin

        index, owner, deep = sel
        which = coin.SO_SWITCH_ALL if shown else coin.SO_SWITCH_NONE
        if owner or not deep:
            for j in self._draws(sel):
                self._draw_switch(index, j).whichChild = which
        else:
            self.root.getChild(index + 1).whichChild = which  # after the shape hints
        self.hidden.discard(sel) if shown else self.hidden.add(sel)
        self._show_selection()

    def show_all(self):
        for sel in list(self.hidden):
            self.set_shown(sel, True)

    # ----- the panel

    def _panel(self):
        from PySide import QtCore, QtGui, QtWidgets

        import FreeCADGui

        host = None
        for sub in FreeCADGui.getMainWindow().findChildren(QtWidgets.QMdiSubWindow):
            if sub.windowTitle().startswith(self.title):
                host = sub.widget()
        if host is None:
            return
        panel = QtWidgets.QFrame(host)
        panel.setObjectName("bwFastPanel")
        panel.setStyleSheet("#bwFastPanel { background: palette(window); border: 1px solid palette(mid);"
                            " border-radius: 10px; } QLabel#bwFastTitle { font-weight: 600; }")
        outer = QtWidgets.QVBoxLayout(panel)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(f"{self.pn}  {self.description}")
        title.setObjectName("bwFastTitle")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        self.fold_button = QtWidgets.QToolButton()
        self.fold_button.setAutoRaise(True)
        self.fold_button.setArrowType(QtCore.Qt.UpArrow)
        self.fold_button.setToolTip("Fold the panel out of the way (Esc in the view)")
        self.fold_button.clicked.connect(lambda _c=False: self.fold(not self.folded))
        head.addWidget(self.fold_button, 0, QtCore.Qt.AlignTop)
        outer.addLayout(head)
        self.body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        outer.addWidget(self.body, 1)
        self.folded = False
        count = part_count(self.components)
        info = QtWidgets.QLabel(f"Fast Open · {count:,} parts, {len(self.components)} components, "
                                f"{self.meshes} unique meshes · {self.load_s + self.build_s:.1f} s")
        info.setWordWrap(True)
        lay.addWidget(info)
        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Find a component")
        search.setClearButtonEnabled(True)
        lay.addWidget(search)
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumHeight(220)
        for index, comp in enumerate(self.components):
            item = self.session.vault.items.get(comp["pn"])
            text = f"{comp['pn']}  {item.description if item is not None else comp['object']}"
            row = QtWidgets.QListWidgetItem(text)
            row.setData(QtCore.Qt.UserRole, index)
            self.list.addItem(row)
        self.list.currentItemChanged.connect(
            lambda cur, _prev: self.select(cur.data(QtCore.Qt.UserRole), fit=True) if cur is not None else None)
        # A click on the row that is already current changes no current item: choose it all the same.
        self.list.itemClicked.connect(
            lambda row: self.select(row.data(QtCore.Qt.UserRole), fit=True)
            if (row.data(QtCore.Qt.UserRole), (), True) != self.selected else None)
        search.textChanged.connect(lambda text: [self.list.item(i).setHidden(text.lower() not in self.list.item(i).text().lower())
                                                 for i in range(self.list.count())])
        lay.addWidget(self.list, 1)
        self.picked = QtWidgets.QLabel("Click a part, or choose a component above.")
        self.picked.setWordWrap(True)
        lay.addWidget(self.picked)
        row = QtWidgets.QHBoxLayout()
        self.open_part = QtWidgets.QPushButton("Open")
        self.open_part.setToolTip("Open the selected part or component's own file")
        self.open_part.setEnabled(False)
        self.open_part.clicked.connect(lambda _c=False: self._open_component())
        self.parent_button = QtWidgets.QPushButton("Select parent")
        self.parent_button.setToolTip("Select the assembly the selected part sits in")
        self.parent_button.setEnabled(False)
        self.parent_button.clicked.connect(lambda _c=False: self.select_parent())
        full = QtWidgets.QPushButton("Open full")
        full.setToolTip("Load the exact assembly (every file, every shape)")
        full.clicked.connect(lambda _c=False: self._open_full())
        row.addWidget(self.open_part)
        row.addWidget(self.parent_button)
        row.addStretch(1)
        row.addWidget(full)
        lay.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        self.hide_button = QtWidgets.QPushButton("Hide")
        self.hide_button.setToolTip("Hide the selection to look inside (e.g. a panel)")
        self.hide_button.setEnabled(False)
        self.hide_button.clicked.connect(
            lambda _c=False: self.set_shown(self.selected, self.selected in self.hidden)
            if self.selected is not None else None)
        show_all = QtWidgets.QPushButton("Show all")
        show_all.clicked.connect(lambda _c=False: self.show_all())
        row2.addWidget(self.hide_button)
        row2.addStretch(1)
        row2.addWidget(show_all)
        lay.addLayout(row2)
        panel.setFixedWidth(340)
        self.panel = panel
        self.host = host
        host.installEventFilter(self._filter())
        self._esc = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), host)
        self._esc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self._esc.activated.connect(lambda: self.fold(not self.folded))
        self._place()
        panel.show()
        panel.raise_()

    def _left_edge(self):
        """Where the panel starts: right of the left overlay (the Browser floats over the 3D view)."""
        from PySide import QtCore, QtWidgets

        import FreeCADGui

        x = 12
        for w in FreeCADGui.getMainWindow().findChildren(QtWidgets.QWidget, "OverlayLeft"):
            if w.isVisible():
                right = w.mapTo(FreeCADGui.getMainWindow(), QtCore.QPoint(w.width(), 0)).x()
                host_left = self.host.mapTo(FreeCADGui.getMainWindow(), QtCore.QPoint(0, 0)).x()
                x = max(x, right - host_left + 12)
        return x

    def _place(self):
        x = self._left_edge()
        self.panel.move(x, 12)
        if self.folded:
            self.panel.resize(340, self.panel.sizeHint().height())
        else:
            self.panel.resize(340, max(260, min(620, self.host.height() - 24)))

    def fold(self, folded):
        """Fold the panel to its title row (and back), so it is out of the way of the model."""
        from PySide import QtCore

        self.folded = bool(folded)
        self.body.setVisible(not self.folded)
        self.fold_button.setArrowType(QtCore.Qt.DownArrow if self.folded else QtCore.Qt.UpArrow)
        self.fold_button.setToolTip("Unfold the panel (Esc in the view)" if self.folded
                                    else "Fold the panel out of the way (Esc in the view)")
        self.panel.adjustSize()
        self._place()

    def _filter(self):
        from PySide import QtCore

        view = self

        class _Resize(QtCore.QObject):
            def eventFilter(self, _obj, event):
                if event.type() == QtCore.QEvent.Resize:
                    view._place()
                return False

        self._resize_filter = _Resize(self.panel)
        return self._resize_filter

    def _show_selection(self):
        if not hasattr(self, "picked"):
            return
        if self.selected is None:
            self.picked.setText("Click a part, or choose a component above.")
            self.open_part.setEnabled(False)
            self.parent_button.setEnabled(False)
            self.hide_button.setEnabled(False)
            self.list.blockSignals(True)  # no row looks chosen when nothing is
            self.list.setCurrentRow(-1)
            self.list.clearSelection()
            self.list.blockSignals(False)
            return
        index, owner, deep = self.selected
        comp = self.components[index]
        pn = comp["files"].get(owner, comp["pn"])
        n = len(self._draws(self.selected))
        lines = [f"{pn}  {self._description(pn)}",
                 f"{n} part{'s' if n != 1 else ''}" if deep else f"{n} bod{'ies' if n != 1 else 'y'}"]
        if owner:
            parent = comp["files"].get(owner[:-1], comp["pn"])
            lines.append(f"in {parent}  {self._description(parent)}")
        self.picked.setText("\n".join(lines))
        self.open_part.setEnabled(True)
        self.parent_button.setEnabled(bool(owner) or (not deep and n != len(self._draws((index, owner, True)))))
        self.hide_button.setEnabled(True)
        self.hide_button.setText("Show" if self.selected in self.hidden else "Hide")
        from PySide import QtCore

        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == index:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.scrollToItem(self.list.item(i))
                self.list.blockSignals(False)
                break

    def _description(self, pn):
        item = self.session.vault.items.get(pn)
        return item.description if item is not None else ""

    def _open_file(self, pn):
        import FreeCAD

        path = self.session.work.path(pn)
        if not os.path.exists(path):
            path = self.session.work.get_latest(pn)
        FreeCAD.openDocument(path)

    def _open_component(self):
        if self.selected is not None:
            index, owner, _deep = self.selected
            comp = self.components[index]
            self._open_file(comp["files"].get(owner, comp["pn"]))

    def _open_full(self):
        self._open_file(self.pn)


_views = []


def open_fast(session, pn):
    """Open ``pn`` in a Fast Open view. Returns the view, or None when it has no preview meshes."""
    t = time.time()
    components = plan(loader(session), pn)
    if not components:
        return None
    view = FastView(session, pn, components, round(time.time() - t, 1))
    _views.append(view)
    return view


def maybe_fast_open(session, pn):
    """The Files tab's Open for an assembly (Bruce's choice): Fast Open above the part limit. Returns the
    view when it opened fast, None when the caller should open the file as before."""
    item = session.vault.items.get(pn)
    if item is None or item.item_type != "assembly":
        return None
    cap = limit()
    if not cap:
        return None
    components = plan(loader(session), pn)
    if not should_fast_open(part_count(components), cap):
        return None
    t = time.time()
    view = FastView(session, pn, components, round(time.time() - t, 1))
    _views.append(view)
    return view


class _FastOpenCommand:
    def GetResources(self):
        return {"MenuText": "Fast Open…",
                "ToolTip": "Open a vault assembly from its preview meshes: seconds instead of minutes for big ones. "
                           "Open full loads the exact model."}

    def Activated(self):
        import bruceworx_vaultclient as vc
        from PySide import QtWidgets

        import FreeCADGui

        session = vc.session()
        if session is None:
            QtWidgets.QMessageBox.information(FreeCADGui.getMainWindow(), "Fast Open", "Open a vault first.")
            return
        rows = sorted(pn for pn, item in session.vault.items.items() if item.item_type == "assembly")
        labels = [f"{pn}  {session.vault.items[pn].description}" for pn in rows]
        choice, ok = QtWidgets.QInputDialog.getItem(FreeCADGui.getMainWindow(), "Fast Open", "Assembly", labels, 0, False)
        if ok and choice:
            open_fast(session, choice.split("  ", 1)[0])

    def IsActive(self):
        return True


def install():
    """The command (Cmd-K finds it) and an entry in the BruceWorx menu."""
    import FreeCADGui
    from PySide import QtGui

    FreeCADGui.addCommand(COMMAND, _FastOpenCommand())
    try:
        import bruceworx_menu

        action = QtGui.QAction("Fast Open\u2026", FreeCADGui.getMainWindow())
        action.setToolTip(_FastOpenCommand().GetResources()["ToolTip"])
        action.triggered.connect(lambda _c=False: FreeCADGui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, "BruceWorxFastOpenAction")
    except Exception:
        pass
