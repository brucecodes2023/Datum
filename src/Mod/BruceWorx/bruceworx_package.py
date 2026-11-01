# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drawing packages and drawing sheet pictures for the vault's Files tab (PDM-13, 1 Nov 2026).

* **Sheet pictures.** FreeCAD saves a drawing with the 3D view's thumbnail, which for a drawing is
  blank. The Files tab wants the sheet: ``sheet_png`` renders the first page through TechDraw's SVG
  export into a PNG, and bruceworx_preview puts it in the file as its thumbnail at save, check-in and
  Make Previews, as it does the ISO picture of a part.
* **Packages.** Bruce's answers: a package is a PDF of each drawing, WIP drawings included and marked
  IN PROGRESS, and he can build his own from any rows he selects. ``export`` writes the PDFs and a
  ``package.csv`` (P/N, revision, state, description, what the drawing is of, file) into a new folder.
  The vault records nothing: a package is a copy for sending.

TechDraw computes a view's lines in the background after a document opens (an exported page can come
out empty, 1 Nov spike), so ``settle`` waits until every view has its edges before a page is exported.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
import re
import tempfile
import time

WATERMARK = "IN PROGRESS"
RELEASED = "Released"
SHEET_WIDTH = 1400  # px: sharp in the preview pane on a Retina screen


# ----- pure helpers (unit tested) -----------------------------------------------------------------


def package_drawings(structure, pns) -> list:
    """The drawings a package of ``pns`` holds, each once, in P/N order: a selected drawing itself, a
    part's drawings, and for an assembly the drawings of the assembly and of everything it uses."""
    from bruceworx import vault_trees

    out = set()
    for pn in pns:
        info = structure["items"].get(pn) or {}
        if info.get("type") == "drawing":
            out.add(pn)
            continue
        reach = vault_trees.contents(structure, pn) if info.get("type") == "assembly" else {pn}
        for p in reach:
            if (structure["items"].get(p) or {}).get("type") == "drawing":
                out.add(p)
            out.update(structure["drawings"].get(p, ()))
    return sorted(out)


def watermarked(state) -> bool:
    """Every drawing that is not released carries the IN PROGRESS mark."""
    return (state or "") != RELEASED


def safe_name(text) -> str:
    """A folder or file name from free text: no slashes, colons or control characters."""
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", str(text or "")).strip()
    return re.sub(r"\s+", " ", cleaned)[:120] or "Package"


def default_name(pns, today=None) -> str:
    day = (today or _dt.date.today()).isoformat()
    head = pns[0] if len(pns) == 1 else f"{len(pns)} drawings"
    return safe_name(f"{head} package {day}")


def csv_rows(lines) -> list:
    """The package.csv rows: a header, then one row per exported sheet."""
    out = [["part_number", "revision", "state", "description", "drawing_of", "file"]]
    for line in lines:
        out.append([line["pn"], line["revision"], line["state"], line["description"], line["of"], line["file"]])
    return out


# ----- TechDraw (FreeCAD GUI) ---------------------------------------------------------------------


def pages_of(doc) -> list:
    return [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]


def _views(page):
    out = []
    for view in page.Views:
        out += list(view.Views) if view.isDerivedFrom("TechDraw::DrawProjGroup") else [view]
    return out


def settle(page, timeout=30.0) -> bool:
    """Wait until every view of ``page`` has its lines (TechDraw computes them in the background).
    A view with none after the document opened is recomputed once."""
    from PySide import QtWidgets

    empty = [v for v in _views(page) if hasattr(v, "getVisibleEdges") and not v.getVisibleEdges()]
    if empty:
        for v in empty:
            v.touch()
        page.Document.recompute()
    end = time.time() + timeout
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if not any(hasattr(v, "getVisibleEdges") and not v.getVisibleEdges() for v in _views(page)):
            return True
        time.sleep(0.1)
    return False


def sheet_png(doc, width=SHEET_WIDTH) -> bytes:
    """The first page of a drawing document as a PNG (white sheet), or b"" when it has none."""
    import TechDrawGui
    from PySide import QtCore, QtGui, QtSvg

    pages = pages_of(doc)
    if not pages:
        return b""
    page = pages[0]
    settle(page)
    handle, path = tempfile.mkstemp(prefix="bw-sheet-", suffix=".svg")
    os.close(handle)
    try:
        TechDrawGui.exportPageAsSvg(page, path)
        renderer = QtSvg.QSvgRenderer(path)
        if not renderer.isValid():
            return b""
        size = renderer.defaultSize()
        height = max(1, int(width * size.height() / max(size.width(), 1)))
        image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        renderer.render(painter)
        painter.end()
    finally:
        os.remove(path)
    import bruceworx_preview

    image.setText(*bruceworx_preview.MARK)
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def export_pdf(page, path, mark=False):
    """Export ``page`` to ``path``; with ``mark`` an IN PROGRESS stamp across the sheet. The stamp is
    removed again afterwards and the document's modified flag put back, so it is left as it was."""
    import FreeCADGui
    import TechDrawGui

    doc = page.Document
    settle(page)
    gdoc = FreeCADGui.getDocument(doc.Name)
    was_modified = bool(getattr(gdoc, "Modified", False))
    stamp = None
    if mark:
        stamp = doc.addObject("TechDraw::DrawViewAnnotation", "BWInProgress")
        stamp.Text = [WATERMARK]
        stamp.TextSize = 36
        stamp.TextColor = (0.88, 0.25, 0.22)
        stamp.Rotation = 30
        template = page.Template
        width = float(getattr(template, "Width", 420) or 420)
        height = float(getattr(template, "Height", 297) or 297)
        stamp.X, stamp.Y = width / 2, height / 2
        page.addView(stamp)
        doc.recompute()
    try:
        TechDrawGui.exportPageAsPdf(page, path)
    finally:
        if stamp is not None:
            page.removeView(stamp)
            doc.removeObject(stamp.Name)
            doc.recompute()
            if gdoc is not None and not was_modified:
                gdoc.Modified = False


def export(session, drawing_pns, folder, name, progress=None) -> dict:
    """Write the package ``name`` into ``folder``: one PDF per page of each drawing (WIP ones marked) and
    package.csv. Returns {"folder", "files", "failed": [(pn, reason)]}."""
    import bruceworx_preview

    target = os.path.join(folder, safe_name(name))
    n = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{safe_name(name)} ({n})")
        n += 1
    os.makedirs(target)
    vault = session.vault
    lines, failed = [], []
    for i, pn in enumerate(drawing_pns):
        if progress is not None:
            progress(i, len(drawing_pns), pn)
        item = vault.items.get(pn)
        latest = vault.latest_version(pn)
        if item is None or latest is None:
            failed.append((pn, "not in the vault"))
            continue
        code = latest.revision if hasattr(latest, "revision") else ""
        try:
            with bruceworx_preview.opened(bruceworx_preview.source_for(session, pn)) as doc:
                pages = pages_of(doc)
                if not pages:
                    failed.append((pn, "no drawing page"))
                    continue
                for k, page in enumerate(pages, 1):
                    stem = f"{pn}-{code}" + (f" sheet {k}" if len(pages) > 1 else "")
                    path = os.path.join(target, safe_name(stem) + ".pdf")
                    export_pdf(page, path, mark=watermarked(item.state))
                    lines.append({"pn": pn, "revision": code, "state": item.state, "description": item.description or "",
                                  "of": item.drawing_of or "", "file": os.path.basename(path)})
        except Exception as exc:  # noqa: BLE001 -- one drawing that will not export does not stop the package
            failed.append((pn, str(exc)))
    with open(os.path.join(target, "package.csv"), "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(csv_rows(lines))
    return {"folder": target, "files": [line["file"] for line in lines], "failed": failed}


# ----- the dialog ---------------------------------------------------------------------------------


def export_dialog(panel, pns):
    """Build Package / Export Package: pick the drawings (all ticked), name the package and the folder."""
    from PySide import QtCore, QtWidgets

    session = panel.session()
    if session is None:
        return
    drawings = package_drawings(panel.structure, pns)
    parent = panel.window()
    if not drawings:
        QtWidgets.QMessageBox.information(parent, "Build Package", "None of the selected files has a drawing.")
        return
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Build Package")
    lay = QtWidgets.QVBoxLayout(dialog)
    lay.addWidget(QtWidgets.QLabel("A PDF of each ticked drawing, WIP ones marked IN PROGRESS, and package.csv."))
    lst = QtWidgets.QListWidget()
    for pn in drawings:
        row = panel.rows.get(pn) or {}
        item = QtWidgets.QListWidgetItem(f"{row.get('display') or pn}   {row.get('state') or ''}\n"
                                         f"{row.get('description') or ''}")
        item.setData(QtCore.Qt.UserRole, pn)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked)
        lst.addItem(item)
    lay.addWidget(lst, 1)
    form = QtWidgets.QFormLayout()
    name = QtWidgets.QLineEdit(default_name(pns))
    folder = QtWidgets.QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop"))
    pick = QtWidgets.QPushButton("Choose…")
    pick.clicked.connect(lambda: folder.setText(QtWidgets.QFileDialog.getExistingDirectory(
        dialog, "Package folder", folder.text()) or folder.text()))
    where = QtWidgets.QHBoxLayout()
    where.addWidget(folder, 1)
    where.addWidget(pick)
    form.addRow("Package name", name)
    form.addRow("Into", where)
    lay.addLayout(form)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
    ok = buttons.addButton("Export", QtWidgets.QDialogButtonBox.AcceptRole)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    lay.addWidget(buttons)
    dialog.resize(520, 460)

    def chosen():
        return [lst.item(i).data(QtCore.Qt.UserRole) for i in range(lst.count())
                if lst.item(i).checkState() == QtCore.Qt.Checked]

    lst.itemChanged.connect(lambda _i: ok.setEnabled(bool(chosen())))
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return
    picked = chosen()

    def progress(i, total, pn):
        panel.footer.setText(f"Building package · {i + 1} of {total} · {pn}")
        QtWidgets.QApplication.processEvents()

    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        result = export(session, picked, folder.text(), name.text(), progress)
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()
    text = f"Package written: {len(result['files'])} PDF{'s' if len(result['files']) != 1 else ''} in {result['folder']}"
    if result["failed"]:
        text += f" · {len(result['failed'])} not exported ({', '.join(p for p, _r in result['failed'][:3])})"
    panel.footer.setText(text)
    from PySide import QtGui

    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(result["folder"]))
