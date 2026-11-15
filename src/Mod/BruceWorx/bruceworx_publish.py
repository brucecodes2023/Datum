# SPDX-License-Identifier: LGPL-2.1-or-later
"""Publish the TechDraw pages of one document (DRW-08).

``publish_pages(doc, output_dir)`` recomputes the document and writes one PDF
per page into ``output_dir``, named from the page label. With the GUI up, the
PDF is TechDraw's own export (TechDrawGui.exportPageAsPdf). FreeCADCmd cannot
load that module, so headless the PDF is the template (the title block is part
of the template) with each part view's edges (TechDraw.viewPartAsSvg) drawn
on it, painted onto a PDF page through Qt. The view edges are already scaled.
Dimensions, balloons, hatches and annotation text are in the GUI export.

A DXF is written next to the PDF when TechDraw.writeDXFPage runs. It runs
headless. When it fails, the PDF is still written and the DXF path is None.

Publishing every drawing on a BOM (one command for LE-02001) is the rest of
the epic. This function publishes the pages of the document it is given.
"""

import os
import re

_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)
_qt_app = None
_qt_owned = False


def publish_pages(doc, output_dir):
    """One PDF per TechDraw page, and a DXF when TechDraw's exporter accepts the page.

    Returns a list of dicts, document order: name, label, pdf, dxf (dxf is None
    when writeDXFPage did not write a file).
    """
    output_dir = os.path.abspath(os.path.expanduser(output_dir))
    os.makedirs(output_dir, exist_ok=True)
    try:
        doc.recompute()
        written = []
        taken = set()
        for page in _pages(doc):
            stem = _stem(page, taken)
            pdf = _write_pdf(page, os.path.join(output_dir, stem + ".pdf"))
            dxf = _write_dxf(page, os.path.join(output_dir, stem + ".dxf"))
            written.append({"name": page.Name, "label": page.Label, "pdf": pdf, "dxf": dxf})
        return written
    finally:
        # FreeCADCmd ends a -t run with exit(). A QGuiApplication still alive then
        # aborts in static teardown, so the one this module created is destroyed here.
        _drop_qt()


def _pages(doc):
    return [obj for obj in doc.Objects if obj.isDerivedFrom("TechDraw::DrawPage")]


def _stem(page, taken):
    raw = (getattr(page, "Label", "") or page.Name or "page").strip()
    stem = _UNSAFE.sub("_", raw).strip("._") or page.Name
    if stem in taken:
        stem = f"{stem}-{page.Name}"
    base, n = stem, 2
    while stem in taken:
        stem = f"{base}-{n}"
        n += 1
    taken.add(stem)
    return stem


def _write_pdf(page, path):
    import FreeCAD

    if FreeCAD.GuiUp:
        try:
            import TechDrawGui

            TechDrawGui.exportPageAsPdf(page, path)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
        except Exception as exc:  # noqa: BLE001 -- fall back to the headless painter
            FreeCAD.Console.PrintLog(f"BruceWorx publish: GUI PDF export skipped: {exc}\n")
    return _write_pdf_headless(page, path)


def _write_pdf_headless(page, path):
    width, height = _page_size(page)
    svg = _page_svg(page, height)
    tmp = path + ".tmp"
    try:
        _paint_pdf(svg, tmp, width, height)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def _page_size(page):
    try:
        width, height = float(page.PageWidth), float(page.PageHeight)
    except Exception:  # noqa: BLE001 -- a page with no readable template
        width, height = 0.0, 0.0
    if width <= 0 or height <= 0:
        return 297.0, 210.0
    return width, height


def _page_svg(page, height):
    text = _template_text(page, *_page_size(page))
    overlay = _view_overlays(page, height)
    if not overlay:
        return text
    head, sep, tail = text.rpartition("</svg>")
    if not sep:
        return text + overlay
    return head + overlay + sep + tail


def _template_text(page, width, height):
    template = getattr(page, "Template", None)
    for attr in ("PageResult", "Template"):
        path = getattr(template, attr, "") if template is not None else ""
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    return handle.read()
            except OSError:
                continue
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" '
            f'viewBox="0 0 {width} {height}"></svg>')


def _part_views(page):
    """Part views on the page, including the members of a projection group."""
    views = []
    seen = set()

    def take(obj):
        if obj is None or getattr(obj, "Name", None) in seen:
            return
        seen.add(obj.Name)
        if obj.isDerivedFrom("TechDraw::DrawProjGroup"):
            for child in getattr(obj, "Views", None) or []:
                take(child)
            return
        if obj.isDerivedFrom("TechDraw::DrawViewPart"):
            views.append(obj)

    for obj in getattr(page, "Views", None) or []:
        take(obj)
    return views


def _view_overlays(page, height):
    import FreeCAD
    import TechDraw

    chunks = []
    for view in _part_views(page):
        try:
            svg = TechDraw.viewPartAsSvg(view) or ""
        except Exception as exc:  # noqa: BLE001 -- one view must not drop the page
            FreeCAD.Console.PrintWarning(
                f"BruceWorx publish: {getattr(view, 'Label', view)} left off the PDF: {exc}\n")
            continue
        if "<" not in svg:
            continue
        x, y = _page_xy(view)
        # Edges are Y-up, already scaled and rotated. The sheet SVG is Y-down,
        # origin at the top left; a view's X and Y are from the bottom left.
        chunks.append(
            f'<g transform="translate({x:.4f},{height - y:.4f}) scale(1,-1)">{svg}</g>')
    return "".join(chunks)


def _page_xy(view):
    x, y = _num(view, "X"), _num(view, "Y")
    for parent in getattr(view, "InList", None) or []:
        if parent.isDerivedFrom("TechDraw::DrawProjGroup"):
            return x + _num(parent, "X"), y + _num(parent, "Y")
    return x, y


def _num(obj, name):
    value = getattr(obj, name, 0.0)
    value = getattr(value, "Value", value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _paint_pdf(svg, path, width, height):
    _qt()
    from PySide import QtCore, QtGui, QtSvg

    writer = QtGui.QPdfWriter(path)
    # Landscape transposes a custom size. Width and height are already the paper.
    layout = QtGui.QPageLayout(
        QtGui.QPageSize(QtCore.QSizeF(width, height), QtGui.QPageSize.Millimeter),
        QtGui.QPageLayout.Portrait,
        QtCore.QMarginsF(0, 0, 0, 0))
    if hasattr(layout, "setMode"):
        layout.setMode(QtGui.QPageLayout.FullPageMode)
    writer.setPageLayout(layout)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError(f"the page SVG is not valid ({path})")
    painter = QtGui.QPainter(writer)
    try:
        if not painter.isActive():
            raise RuntimeError(f"could not open {path} for PDF")
        renderer.render(painter, QtCore.QRectF(painter.viewport()))
    finally:
        painter.end()
    del painter
    del writer


def _qt():
    """A Qt GUI application, so SVG text can be painted. Offscreen under FreeCADCmd."""
    global _qt_app, _qt_owned
    from PySide import QtGui

    existing = QtGui.QGuiApplication.instance()
    if existing is not None:
        return existing
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # A private argv: Qt must not parse FreeCADCmd's own arguments.
    _qt_app = QtGui.QGuiApplication(["BruceWorxPublish"])
    _qt_owned = True
    return _qt_app


def _drop_qt():
    global _qt_app, _qt_owned
    if not _qt_owned:
        return
    app = _qt_app
    _qt_app = None
    _qt_owned = False
    if app is None:
        return
    import shiboken6

    shiboken6.delete(app)


def _write_dxf(page, path):
    import FreeCAD
    import TechDraw

    try:
        TechDraw.writeDXFPage(page, path)
    except Exception as exc:  # noqa: BLE001 -- the PDF still stands when DXF is refused
        FreeCAD.Console.PrintWarning(f"BruceWorx publish: {page.Label} DXF not written: {exc}\n")
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None
