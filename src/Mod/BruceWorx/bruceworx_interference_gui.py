# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inspect > Interference (roadmap ASM-06): the command and its tab in the right panel.

The tab checks the active assembly as soon as it opens, a few pairs at a time between redraws
(OpenCASCADE keeps Python's lock, so a thread would not keep the window responsive), and lists
clashes, fits, clearance and contacts. Picking a row selects both parts and draws the overlap, or
the nearest points, over the model. Mark as fit / Not a fit change the fits the section views use
too (bruceworx_interference.set_fit). Close takes the drawing away; the file keeps only the fits.
"""

from __future__ import annotations

import os

import bruceworx_interference as itf
from bruceworx_slots import slot

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "Heuretes_Interference.svg")
OVERLAY_NAME = "BruceWorxInterference"
STEP_SECONDS = 0.05  # work per timer tick; the view redraws in between
GROUPS = ((itf.CLASH, "Clashes"), (itf.FIT, "Fits"), (itf.CLEARANCE, "Clearance"), (itf.CONTACT, "Contacts"))
CLASH_RGB = (0.86, 0.13, 0.13)
NEAR_RGB = (1.0, 0.55, 0.0)

_state = {"installed": False, "panel": None, "manipulator": None}


def _app():
    import FreeCAD

    return FreeCAD


# ----- drawing on the canvas --------------------------------------------------------------------------


def _view(doc):
    import FreeCADGui as Gui

    gdoc = Gui.getDocument(doc.Name) if doc is not None else None
    view = gdoc.activeView() if gdoc is not None else None
    return view if view is not None and hasattr(view, "getSceneGraph") else None


def clear_overlay(view):
    if view is None:
        return
    root = view.getSceneGraph()
    for i in range(root.getNumChildren() - 1, -1, -1):
        if root.getChild(i).getName().getString() == OVERLAY_NAME:
            root.removeChild(i)


def _material(coin, rgb, transparency=0.0):
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*rgb)
    mat.emissiveColor.setValue(rgb[0] * 0.45, rgb[1] * 0.45, rgb[2] * 0.45)
    mat.transparency.setValue(transparency)
    return mat


def overlay_node(result, placement=None):
    """A Coin node that shows ``result`` on top of the model: the overlap of a clash or fit in red
    (faces and their edges), or the nearest points of a contact or clearance and the line between."""
    from pivy import coin

    App = _app()
    plc = placement or App.Placement()
    top = coin.SoAnnotation()  # drawn after the model, so an overlap inside a housing still shows
    top.setName(OVERLAY_NAME)
    pick = coin.SoPickStyle()
    pick.style = coin.SoPickStyle.UNPICKABLE
    top.addChild(pick)
    if result.overlap is not None:
        size = result.overlap.BoundBox.DiagonalLength
        points, triangles = result.overlap.tessellate(max(size / 200.0, 0.005))
        faces = coin.SoSeparator()
        faces.addChild(_material(coin, CLASH_RGB, 0.25))
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        faces.addChild(hints)
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, len(points), [tuple(plc.multVec(p)) for p in points])
        faces.addChild(coords)
        tris = coin.SoIndexedFaceSet()
        index = []
        for t in triangles:
            index.extend((t[0], t[1], t[2], -1))
        tris.coordIndex.setValues(0, len(index), index)
        faces.addChild(tris)
        top.addChild(faces)
        lines = coin.SoSeparator()
        lines.addChild(_material(coin, (0.55, 0.0, 0.0)))
        style = coin.SoDrawStyle()
        style.lineWidth = 2
        lines.addChild(style)
        for edge in result.overlap.Edges:
            pts = [tuple(plc.multVec(p)) for p in edge.discretize(Deflection=max(size / 400.0, 0.002))]
            if len(pts) < 2:
                continue
            c = coin.SoCoordinate3()
            c.point.setValues(0, len(pts), pts)
            lines.addChild(c)
            line = coin.SoLineSet()
            line.numVertices.setValue(len(pts))
            lines.addChild(line)
        top.addChild(lines)
    elif result.points and result.points[0] is not None:
        a, b = (plc.multVec(p) for p in result.points)
        sep = coin.SoSeparator()
        sep.addChild(_material(coin, NEAR_RGB))
        style = coin.SoDrawStyle()
        style.lineWidth = 3
        style.pointSize = 9
        sep.addChild(style)
        c = coin.SoCoordinate3()
        c.point.setValues(0, 2, [tuple(a), tuple(b)])
        sep.addChild(c)
        marks = coin.SoMarkerSet()
        marks.markerIndex = coin.SoMarkerSet.CIRCLE_FILLED_9_9
        sep.addChild(marks)
        if (a - b).Length > 1e-9:
            sep.addChild(coin.SoLineSet())
        top.addChild(sep)
    return top


def parent_placement(root):
    """Where the occurrences' shapes are drawn: Part.getShape(root, sub) already holds root's own
    placement, so only the placements of the groups above it are left."""
    App = _app()
    try:
        return root.getGlobalPlacement().multiply(root.Placement.inverse())
    except Exception:  # noqa: BLE001
        return App.Placement()


def show_result(view, result):
    """Select the pair and draw it (one pair at a time)."""
    import FreeCADGui as Gui

    clear_overlay(view)
    Gui.Selection.clearSelection()
    if result is None:
        return None
    root = result.first.root
    for occ in (result.first, result.second):
        Gui.Selection.addSelection(root.Document.Name, root.Name, occ.sub)
    if view is None:
        return None
    node = overlay_node(result, parent_placement(root))
    view.getSceneGraph().addChild(node)
    return node


# ----- the tab ------------------------------------------------------------------------------------------


class InterferencePanel:
    """The Interference tab: options, the four groups of pairs, and what to do with the one picked."""

    def __init__(self, assembly):
        from PySide import QtCore, QtWidgets

        self.assembly = assembly
        self.doc = assembly.Document
        self.view = _view(self.doc)
        self.checker = None
        self.results = []
        prefs = itf.prefs()
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Interference")
        self.form.setObjectName("bwInterferencePanel")
        lay = QtWidgets.QVBoxLayout(self.form)
        lay.addWidget(QtWidgets.QLabel(f"<b>{assembly.Label}</b>"))
        row = QtWidgets.QHBoxLayout()
        self.clearance = QtWidgets.QCheckBox("Clearance closer than")
        self.clearance.setChecked(prefs["clearance"])
        self.gap = QtWidgets.QDoubleSpinBox()
        self.gap.setDecimals(2)
        self.gap.setRange(0.01, 100.0)
        self.gap.setSingleStep(0.05)
        self.gap.setSuffix(" mm")
        self.gap.setValue(prefs["gap"])
        self.gap.setEnabled(prefs["clearance"])
        self.clearance.toggled.connect(self.gap.setEnabled)
        row.addWidget(self.clearance)
        row.addWidget(self.gap)
        row.addStretch(1)
        lay.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.contacts = QtWidgets.QCheckBox("List touching contacts")
        self.contacts.setChecked(prefs["contacts"])
        row.addWidget(self.contacts)
        row.addStretch(1)
        self.again = QtWidgets.QPushButton("Check again")
        self.again.setObjectName("bwInterferenceCheck")
        self.again.clicked.connect(slot(self.start))
        row.addWidget(self.again)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(slot(self.stop))
        self.stop_button.hide()
        row.addWidget(self.stop_button)
        lay.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        lay.addWidget(self.progress)
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("bwInterferenceStatus")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("bwInterferenceList")
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setMinimumHeight(220)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(slot(self.picked))
        self.tree.itemDoubleClicked.connect(lambda *_a: self.zoom())
        lay.addWidget(self.tree, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.fit_button = QtWidgets.QPushButton("Mark as fit")
        self.fit_button.setToolTip("The overlap is intended (a screw in its tapped hole, a press fit): every pair of "
                                   "these two parts leaves the clashes, here and in the section views")
        self.fit_button.clicked.connect(lambda: self.mark_fit(True))
        self.unfit_button = QtWidgets.QPushButton("Not a fit")
        self.unfit_button.clicked.connect(lambda: self.mark_fit(False))
        self.zoom_button = QtWidgets.QPushButton("Zoom to pair")
        self.zoom_button.clicked.connect(slot(self.zoom))
        for b in (self.fit_button, self.unfit_button, self.zoom_button):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.note = QtWidgets.QLineEdit()
        self.note.setPlaceholderText("Why it overlaps (optional), e.g. thread at the tap-drill size")
        lay.addWidget(self.note)
        self.timer = QtCore.QTimer()
        self.timer.setInterval(0)
        self.timer.timeout.connect(slot(self._tick))
        self._update_buttons()
        QtCore.QTimer.singleShot(0, self.start)

    # checking
    def start(self):
        self.stop()
        clear_overlay(self.view)
        gap = self.gap.value() if self.clearance.isChecked() else 0.0
        itf.save_prefs(self.gap.value(), self.clearance.isChecked(), self.contacts.isChecked())
        try:
            self.checker = itf.Checker(self.assembly, gap, self.contacts.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not check: {exc}")
            return
        self.progress.setRange(0, max(1, self.checker.total))
        self.progress.setValue(0)
        self.progress.show()
        self.again.hide()
        self.stop_button.show()
        self.status.setText(f"Checking {len(self.checker.occurrences)} parts…")
        self.fill()
        self.timer.start()

    def _tick(self):
        if self.checker is None:
            self.timer.stop()
            return
        done = self.checker.step(STEP_SECONDS)
        self.progress.setValue(self.checker.done)
        if done:
            self.stop()
            self.fill()

    def stop(self):
        self.timer.stop()
        self.stop_button.hide()
        self.again.show()
        self.progress.hide()
        if self.checker is not None and not self.checker.finished:
            self.fill()

    @property
    def running(self):
        return self.timer.isActive()

    # the list
    def fill(self):
        from PySide import QtCore, QtWidgets

        c = self.checker
        picked = self.current()
        picked_key = (picked.first.sub, picked.second.sub) if picked else None
        expanded = {self.tree.topLevelItem(i).data(0, QtCore.Qt.UserRole): self.tree.topLevelItem(i).isExpanded()
                    for i in range(self.tree.topLevelItemCount())}
        self.tree.blockSignals(True)
        self.tree.clear()
        self.results = []
        if c is None:
            self.tree.blockSignals(False)
            return
        restore = None
        notes = itf.fits(self.doc, self.assembly)
        for kind, title in GROUPS:
            if kind == itf.CONTACT and not c.contacts:
                continue
            if kind == itf.CLEARANCE and c.gap <= 0.0:
                continue
            rows = sorted(c.of_kind(kind), key=itf._order)
            group = QtWidgets.QTreeWidgetItem([f"{title} ({len(rows)})", ""])
            group.setData(0, QtCore.Qt.UserRole, kind)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            group.setFirstColumnSpanned(True)
            for r in rows:
                item = QtWidgets.QTreeWidgetItem([r.pair_label, r.text()])
                item.setData(0, QtCore.Qt.UserRole, len(self.results))
                note = notes.get(r.fit_key, "") if kind == itf.FIT else ""
                item.setToolTip(0, f"{r.first.label} and {r.second.label}" + (f": {note}" if note else ""))
                group.addChild(item)
                self.results.append(r)
                if picked_key == (r.first.sub, r.second.sub):
                    restore = item
            group.setExpanded(expanded.get(kind, kind != itf.CONTACT))
        self.tree.blockSignals(False)
        if restore is not None:
            self.tree.setCurrentItem(restore)
        s = c.summary() if c.finished else None
        if s is not None:
            counts = s["counts"]
            self.status.setText(f"{s['parts']} parts, {s['candidates']} pairs near each other, checked in "
                                f"{s['seconds']:.1f} s: {counts[itf.CLASH]} clashes, {counts[itf.FIT]} fits"
                                + (f", {counts[itf.CLEARANCE]} closer than {c.gap:.2f} mm" if c.gap > 0 else "")
                                + (f", {counts[itf.CONTACT]} touching" if c.contacts else "")
                                + (f". {len(s['errors'])} pairs could not be checked." if s["errors"] else "."))
        elif c is not None:
            self.status.setText(f"Stopped after {c.done} of {c.total} pairs.")
        self._update_buttons()

    def current(self):
        from PySide import QtCore

        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return None
        index = item.data(0, QtCore.Qt.UserRole)
        return self.results[index] if index is not None and index < len(self.results) else None

    def picked(self, *_args):
        show_result(self.view, self.current())
        self._update_buttons()

    def _update_buttons(self):
        r = self.current()
        self.fit_button.setEnabled(r is not None and r.kind == itf.CLASH)
        self.unfit_button.setEnabled(r is not None and r.kind == itf.FIT)
        self.zoom_button.setEnabled(r is not None)

    def zoom(self):
        import FreeCADGui as Gui

        if self.current() is not None:
            Gui.SendMsgToActiveView("ViewSelection")

    def mark_fit(self, on):
        r = self.current()
        if r is None:
            return
        self.doc.openTransaction("Mark as fit" if on else "Not a fit")
        itf.set_fit(self.doc, r.first.part, r.second.part, on, self.note.text().strip(), self.assembly)
        self.doc.commitTransaction()
        self.note.clear()
        if self.checker is not None:
            self.checker.refit(itf.fits(self.doc, self.assembly))
        self.fill()

    # task dialog
    def getStandardButtons(self):
        from PySide import QtWidgets

        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        return self.close()

    def accept(self):
        return self.close()

    def close(self):
        import FreeCADGui as Gui

        self.timer.stop()
        clear_overlay(self.view)
        try:
            Gui.Selection.clearSelection()
        except Exception:  # noqa: BLE001
            pass
        _state["panel"] = None
        Gui.Control.closeDialog()
        return True


def open_panel(assembly=None):
    import FreeCADGui as Gui

    assembly = assembly or itf.active_assembly()
    if assembly is None:
        return None
    if _state["panel"] is not None:
        _state["panel"].close()
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    panel = InterferencePanel(assembly)
    _state["panel"] = panel
    Gui.Control.showDialog(panel)
    return panel


class _Interference:
    def GetResources(self):
        return {"Pixmap": ICON, "MenuText": "Interference",
                "ToolTip": "Check the assembly for parts that overlap (clashes), touch, or come closer than a gap. "
                           "Pick a pair to see it; mark intended overlaps, such as threads, as fits."}

    def IsActive(self):
        import FreeCADGui as Gui

        doc = _app().ActiveDocument
        if doc is None or (Gui.Control.activeDialog() and _state["panel"] is None):
            return False
        return bool(itf.assemblies(doc))

    def Activated(self):
        open_panel()


class _Manipulator:
    """Interference in the Assembly menu, after the BOM."""

    def modifyMenuBar(self):
        return [{"insert": "BruceWorx_Interference", "menuItem": "Assembly_CreateBom", "after": ""}]


def install():
    """Register the command (InitGui)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand("BruceWorx_Interference", _Interference())
    manipulator = _Manipulator()
    Gui.addWorkbenchManipulator(manipulator)
    _state["manipulator"] = manipulator
    _state["installed"] = True
