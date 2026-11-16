# SPDX-License-Identifier: LGPL-2.1-or-later
"""Publish drawings as vector PDF and DXF, one page or everything an assembly needs (roadmap DRW-08).

* export_page(page, folder) writes <stem>.pdf and <stem>.dxf of one TechDraw page. The stem is the
  drawn part's "P/N-REV" (as the title block prints it), else the document's name; a file with
  several pages adds each page's label.
* publish(doc, folder) does that for every page of ``doc`` and of every file it depends on (an
  assembly's parts and sub-assemblies, through its links), and for drawings kept in files of
  their own beside them (a file in the same folders with a TechDraw page that links one of
  them). Files that are not open are opened hidden and closed again. Nothing is saved.
* Publish Drawings (BruceWorx_PublishDrawings) asks for the folder and shows what was written.

PDF comes from TechDraw's own page export (vector; it needs the window, so a headless run writes
DXF only and says so). DXF comes from TechDraw.writeDXFPage.

Off switch: Preferences/BruceWorx/PublishDrawings (default on); off, the command is not registered.
"""

from __future__ import annotations

import os
import re
import zipfile

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "PublishDrawings"
COMMAND = "BruceWorx_PublishDrawings"
ICON = "Heuretes_PublishDrawings.svg"
FORMATS = ("pdf", "dxf")
FOLDER_NAME = "published"


class PublishError(Exception):
    """Something to tell the user in one sentence."""


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF, True))


def pages(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]


def safe(text):
    """``text`` as a file name part."""
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", str(text)).strip("_") or "drawing"


def stem(page, count=None):
    """The file name of a published page, without extension."""
    import bruceworx_drawing as drawing

    base = ""
    try:
        part = drawing.part_of_page(page)
        if part is not None:
            values = drawing.part_values(part, drawing.vault_root())
            if values["part_number"]:
                base = values["part_number"] + (f"-{values['revision']}" if values["revision"] else "")
    except Exception:  # noqa: BLE001 -- a page with no part still publishes
        base = ""
    doc = page.Document
    if not base:
        base = os.path.splitext(os.path.basename(doc.FileName))[0] if doc.FileName else doc.Label
    count = len(pages(doc)) if count is None else count
    return safe(base) if count <= 1 else f"{safe(base)}_{safe(page.Label)}"


def export_page(page, folder, formats=FORMATS, name=None):
    """{format: path} written for one page; a format that cannot be written raises PublishError."""
    import FreeCAD
    import TechDraw

    os.makedirs(folder, exist_ok=True)
    name = name or stem(page)
    out = {}
    for fmt in formats:
        path = os.path.join(folder, f"{name}.{fmt}")
        if fmt == "pdf":
            if not FreeCAD.GuiUp:
                raise PublishError("PDF needs the Heuretes FreeCAD window (TechDraw draws the page there)")
            import TechDrawGui

            TechDrawGui.exportPageAsPdf(page, path)
        elif fmt == "dxf":
            TechDraw.writeDXFPage(page, path)
        else:
            raise PublishError(f"cannot publish as {fmt}")
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise PublishError(f"{os.path.basename(path)} was not written")
        out[fmt] = path
    return out


def is_vector_pdf(path):
    """True when the PDF has drawing operators' content and no embedded raster image."""
    with open(path, "rb") as handle:
        data = handle.read()
    return data.startswith(b"%PDF") and b"/Subtype /Image" not in data and b"/Subtype/Image" not in data


def dependency_files(doc):
    """The saved files ``doc`` needs, itself first: its own and those its links reach, however deep."""
    out, seen = [], set()

    def add(d):
        path = os.path.realpath(d.FileName) if d.FileName else ""
        if path and path not in seen:
            seen.add(path)
            out.append(path)

    add(doc)
    try:
        for other in doc.getDependentDocuments():
            add(other)
    except Exception:  # noqa: BLE001 -- an unsaved document has no dependency list
        pass
    return out


def _mentions(path, names):
    """Does the .FCStd at ``path`` hold a TechDraw page and name one of the files ``names``?"""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("Document.xml").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 -- not a FreeCAD file
        return False
    return "TechDraw::DrawPage" in xml and any(name in xml for name in names)


def drawing_files(model_files):
    """Files beside ``model_files`` that are drawings of them: they hold a page and link one of them."""
    known = {os.path.realpath(p) for p in model_files}
    names = {os.path.basename(p) for p in known}
    out = []
    for folder in sorted({os.path.dirname(p) for p in known}):
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for entry in entries:
            path = os.path.realpath(os.path.join(folder, entry))
            if entry.lower().endswith(".fcstd") and path not in known and _mentions(path, names):
                known.add(path)
                out.append(path)
    return out


def publish(doc, folder=None, formats=FORMATS, progress=None):
    """Publish every drawing ``doc`` needs. Returns [{"file", "page", "pdf", "dxf", "error"}]:
    one entry per page; ``error`` is "" when every format was written."""
    import FreeCAD

    if not doc.FileName:
        files, unsaved = [], [doc]
    else:
        files, unsaved = dependency_files(doc), []
        files += drawing_files(files)
    if folder is None:
        if not doc.FileName:
            raise PublishError("save the document first, or say where to publish")
        folder = os.path.join(os.path.dirname(os.path.realpath(doc.FileName)), FOLDER_NAME)
    report, used = [], set()
    open_by_path = {os.path.realpath(d.FileName): d for d in FreeCAD.listDocuments().values() if d.FileName}
    jobs = [(d, None) for d in unsaved] + [(open_by_path.get(path), path) for path in files]
    for target, path in jobs:
        opened = []
        if target is None:
            before = set(FreeCAD.listDocuments())
            try:
                target = FreeCAD.openDocument(path, True)
            except Exception as exc:  # noqa: BLE001
                report.append({"file": path, "page": "", "error": f"could not be opened: {exc}"})
                continue
            opened = [n for n in FreeCAD.listDocuments() if n not in before]
        try:
            found = pages(target)
            for page in found:
                name = stem(page, len(found))
                while name in used:  # two files drawing the same revision: keep both
                    name += "_2"
                used.add(name)
                entry = {"file": path or target.Label, "page": page.Label, "error": ""}
                if progress is not None:
                    progress(f"{name}")
                for fmt in formats:
                    try:
                        entry.update(export_page(page, folder, (fmt,), name))
                    except Exception as exc:  # noqa: BLE001 -- the other format and pages still publish
                        entry["error"] = (entry["error"] + "; " if entry["error"] else "") + f"{fmt}: {exc}"
                report.append(entry)
        finally:
            for name in reversed(opened):
                try:
                    FreeCAD.closeDocument(name)
                except Exception:  # noqa: BLE001
                    pass
    return report


def summary(report, folder):
    """A few lines for the user."""
    done = [r for r in report if r.get("page") and not r["error"]]
    failed = [r for r in report if r["error"]]
    lines = [f"{len(done)} drawing{'s' if len(done) != 1 else ''} published to {folder}."]
    if not report:
        lines = ["No drawings were found in this document or the files it links."]
    for r in failed[:12]:
        what = r.get("page") or os.path.basename(str(r["file"]))
        lines.append(f"{what}: {r['error']}")
    if len(failed) > 12:
        lines.append(f"... and {len(failed) - 12} more")
    return "\n".join(lines)


class _PublishCommand:
    def GetResources(self):
        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Publish Drawings…",
                "ToolTip": "Write every drawing of this document and of the parts it links as PDF and DXF "
                           "(vector), named by part number and revision."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        import FreeCAD
        import FreeCADGui as Gui
        from PySide import QtWidgets

        doc = FreeCAD.ActiveDocument
        window = Gui.getMainWindow()
        start = os.path.join(os.path.dirname(doc.FileName), FOLDER_NAME) if doc.FileName else os.path.expanduser("~")
        folder = QtWidgets.QFileDialog.getExistingDirectory(window, "Publish drawings to", os.path.dirname(start))
        if not folder:
            return
        try:
            report = publish(doc, folder, progress=lambda text: window.statusBar().showMessage(f"Publishing {text}…"))
        except PublishError as exc:
            QtWidgets.QMessageBox.warning(window, "Publish Drawings", str(exc))
            return
        window.statusBar().clearMessage()
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "Publish Drawings", summary(report, folder),
                                    QtWidgets.QMessageBox.Ok, window)
        box.setAttribute(__import__("PySide").QtCore.Qt.WA_DeleteOnClose)
        box.open()


def install():
    if not enabled():
        return
    import FreeCADGui

    FreeCADGui.addCommand(COMMAND, _PublishCommand())
