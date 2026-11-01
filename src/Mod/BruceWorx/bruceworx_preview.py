# SPDX-License-Identifier: LGPL-2.1-or-later
"""Iso pictures of parts and assemblies for the vault's Files tab (UAT, 25 Oct 2026).

FreeCAD saves a thumbnail of whatever the 3D view showed at the save, from any angle. The Files tab's
tooltip wants one picture per file: the model in the isometric view, fitted and centred. ``render``
draws a document's visible 3D content that way off screen (the views on screen do not move), from the
open document or one opened hidden for the purpose.

* At every save of a vault work file (PDM-13, 1 Nov: the user's choice, files in a vault work folder only)
  the picture replaces the saved file's thumbnail, with the preview mesh beside it
  (``stamp_saved``; the mesh: bruceworx_previewmesh.py). Nothing on screen moves.
* At check-in the same happens to the work file just before it is stored, so the version carries both
  (``stamp_work_file``): a file saved before PDM-13, or saved from a script, gets them there.
* Files checked in before that keep their old thumbnails in the vault; "Refresh Pictures" in the Files
  tab's menu renders each one once into a cache on this Mac (``cache_path``), never into the vault.

A picture made here carries a PNG text key (``MARK``), so a file whose thumbnail is already one is
not rendered again.
"""

from __future__ import annotations

import os
import shutil
import zipfile

THUMBNAIL = "thumbnails/Thumbnail.png"
ISO = (0.424708, 0.17592, 0.339851, 0.820473)  # Gui::Camera::isometric(), as Std_ViewIsometric turns
SIZE = 512  # sharp on a Retina screen; drawn at twice that and scaled down (smooth edges)
BACKGROUND = (1.0, 1.0, 1.0)
MARK = ("BruceWorx", "iso")
KINDS = ("part", "assembly", "cots", "drawing", "simulation")  # a drawing's picture is its first sheet (PDM-13)
MESH_KINDS = ("part", "assembly", "cots", "simulation")
# Not the model: left out of the picture (only there: the document and its views are untouched).
HELPER_TYPES = ("App::Origin", "App::Line", "App::Plane", "App::Point", "App::LocalCoordinateSystem",
                "Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup")
HELPER_BASES = ("Part::Part2DObject", "PartDesign::Datum", "App::DatumElement")
HELPER_GROUPS = ("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup")  # joints, exploded views


# ----- the picture --------------------------------------------------------------------------------


def _top_roots(doc):
    """The scene roots of the document's objects no other object shows as its child (a body shows
    its features, an assembly its parts): the document's 3D scene without the view around it."""
    import FreeCADGui

    gdoc = FreeCADGui.getDocument(doc.Name)
    if gdoc is None:
        return []
    claimed = set()
    for obj in doc.Objects:
        vo = gdoc.getObject(obj.Name)
        try:
            claimed.update(child.Name for child in vo.claimChildren() if hasattr(child, "Name"))
        except Exception:  # noqa: BLE001 -- a view provider without children
            pass
    roots = []
    for obj in doc.Objects:
        if obj.Name in claimed or obj.TypeId in ("App::Origin",):
            continue
        vo = gdoc.getObject(obj.Name)
        node = getattr(vo, "RootNode", None) if vo is not None else None
        if node is not None:
            roots.append(node)
    return roots


def _helper(obj) -> bool:
    if obj.TypeId in HELPER_TYPES:
        return True
    if any(obj.isDerivedFrom(base) for base in HELPER_BASES):
        return True
    return any(parent.TypeId in HELPER_GROUPS for parent in obj.InList)


class _helpers_hidden:
    """The helpers' scene switches off for the render, then put back as they were (a Coin field
    only: no property changes, the document is not marked modified, no view redraws in between)."""

    def __init__(self, doc):
        self.doc = doc
        self.saved = []

    def __enter__(self):
        import FreeCADGui

        gdoc = FreeCADGui.getDocument(self.doc.Name)
        for obj in self.doc.Objects:
            if not _helper(obj):
                continue
            switch = getattr(gdoc.getObject(obj.Name), "SwitchNode", None) if gdoc is not None else None
            if switch is not None:
                self.saved.append((switch, switch.whichChild.getValue()))
                switch.whichChild = -1
        return self

    def __exit__(self, *_exc):
        for switch, value in self.saved:
            switch.whichChild = value
        return False


def render(doc, size=SIZE):
    """A QImage (``size`` square, white) of ``doc``'s visible 3D content in the isometric view, fitted
    and centred; None when nothing visible has a size (an empty file, a drawing)."""
    from pivy import coin
    import FreeCADGui
    from PySide import QtCore, QtGui
    import tempfile

    with _helpers_hidden(doc):
        return _render(doc, size, coin, FreeCADGui, QtCore, QtGui, tempfile)


def _render(doc, size, coin, FreeCADGui, QtCore, QtGui, tempfile):
    content = coin.SoSeparator()
    for node in _top_roots(doc):
        content.addChild(node)
    big = size * 2
    region = coin.SbViewportRegion(big, big)
    bounds = coin.SoGetBoundingBoxAction(region)
    bounds.apply(content)
    box = bounds.getBoundingBox()
    if box.isEmpty():
        content.removeAllChildren()
        return None
    root = coin.SoSeparator()
    root.ref()
    try:
        camera = coin.SoOrthographicCamera()
        camera.orientation.setValue(coin.SbRotation(*ISO))
        root.addChild(camera)
        # a headlight a little above the eye, as the viewer's, and a soft fill from below
        for direction, intensity in (((0.25, -0.35, -1.0), 1.0), ((-0.4, 0.6, -0.6), 0.35)):
            light = coin.SoDirectionalLight()
            light.intensity = intensity
            light.direction.setValue(camera.orientation.getValue().multVec(coin.SbVec3f(*direction)))
            root.addChild(light)
        root.addChild(content)
        camera.viewAll(content, region, 1.06)
        dx, dy, dz = box.getSize().getValue()
        radius = 0.5 * (dx * dx + dy * dy + dz * dz) ** 0.5
        distance = camera.focalDistance.getValue()
        camera.nearDistance = max(distance * 1e-3, distance - 2.0 * radius)
        camera.farDistance = distance + 2.0 * radius
        renderer = FreeCADGui.SoQtOffscreenRenderer(big, big)
        renderer.setBackgroundColor(*BACKGROUND)
        if not renderer.render(root):
            return None
        handle, path = tempfile.mkstemp(prefix="bw-iso-", suffix=".png")
        os.close(handle)
        try:
            renderer.writeToImage(path)
            image = QtGui.QImage(path)
        finally:
            os.remove(path)
    finally:
        content.removeAllChildren()
        root.unref()
    if image.isNull():
        return None
    image = image.convertToFormat(QtGui.QImage.Format_RGB32).scaled(
        size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    image.setText(*MARK)
    return image


def png_bytes(image) -> bytes:
    from PySide import QtCore

    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def is_iso(png: bytes) -> bool:
    """Whether a thumbnail was made here (carries ``MARK``)."""
    from PySide import QtGui

    if not png:
        return False
    return QtGui.QImage.fromData(png).text(MARK[0]) == MARK[1]


# ----- files --------------------------------------------------------------------------------------


def thumbnail_of(path) -> bytes:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(THUMBNAIL)
    except (OSError, KeyError, zipfile.BadZipFile):
        return b""


def set_thumbnail(path, png: bytes) -> None:
    """Put ``png`` in the file as its thumbnail: the archive rewritten entry by entry, in order, then
    swapped in (a failure leaves the file as it was)."""
    tmp = path + ".bw-thumbnail"
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
            names = set(src.namelist())
            for info in src.infolist():
                dst.writestr(info, png if info.filename == THUMBNAIL else src.read(info.filename))
            if THUMBNAIL not in names:
                dst.writestr(zipfile.ZipInfo(THUMBNAIL), png, compress_type=zipfile.ZIP_DEFLATED)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class opened:
    """``with opened(path) as doc``: the document of ``path``, the one already open or else opened
    hidden (with the files it links to) and closed again afterwards."""

    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.before = set()

    def __enter__(self):
        import FreeCAD

        self.before = set(FreeCAD.listDocuments())
        for doc in FreeCAD.listDocuments().values():
            if doc.FileName and os.path.realpath(doc.FileName) == self.path:
                return doc
        return FreeCAD.openDocument(self.path, hidden=True)

    def __exit__(self, *_exc):
        import FreeCAD

        for name in set(FreeCAD.listDocuments()) - self.before:
            try:
                FreeCAD.closeDocument(name)
            except Exception:  # noqa: BLE001
                pass
        return False


def picture_of(path, size=SIZE):
    """The iso picture of the file at ``path`` as PNG bytes, or b"" when it shows nothing."""
    with opened(path) as doc:
        image = render(doc, size)
    return png_bytes(image) if image is not None else b""


def entries_for(doc) -> dict:
    """{zip entry: bytes}: the iso picture and the preview mesh of the open ``doc``, those it has; for a
    drawing, the picture of its first sheet."""
    import bruceworx_package
    import bruceworx_previewmesh

    if bruceworx_package.pages_of(doc):
        sheet = bruceworx_package.sheet_png(doc)
        return {THUMBNAIL: sheet} if sheet else {}
    entries = {}
    image = render(doc)
    if image is not None:
        entries[THUMBNAIL] = png_bytes(image)
    try:
        mesh = bruceworx_previewmesh.mesh_of(doc)
    except Exception as exc:  # noqa: BLE001 -- a shape the mesher refuses: the picture still goes in
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx: no preview mesh for {doc.Label} ({exc})\n")
        mesh = b""
    if mesh:
        entries[bruceworx_previewmesh.ENTRY] = mesh
    return entries


def extras_for(doc, path, kind="") -> dict:
    """PDM-14: what a save adds to the picture and mesh: the part's geometric fingerprint, or a simulation's
    summary (bruceworx_sim_vault.extra_entries). A failure here never stops the picture."""
    try:
        import bruceworx_sim_vault

        return bruceworx_sim_vault.extra_entries(doc, path, kind)
    except Exception as exc:  # noqa: BLE001
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx: no simulation entries for {os.path.basename(path)} ({exc})\n")
        return {}


def stamp_saved(doc, path, kind="") -> bool:
    """Just after ``doc`` was saved to ``path`` (a vault work file, or any .vlsim): put its iso picture and
    preview mesh into the saved file, with the PDM-14 entries (a part's fingerprint, a simulation's summary).
    True when the file was rewritten."""
    import bruceworx_previewmesh

    entries = entries_for(doc)
    entries.update(extras_for(doc, path, kind))
    if not entries or not os.path.exists(path):
        return False
    bruceworx_previewmesh.rewrite(path, entries)
    return True


def stamp_work_file(s, pn) -> bool:
    """At check-in: give the work copy of ``pn`` the iso picture as its thumbnail, when it is a part
    or assembly about to become a new version (an unchanged file is left alone: a picture is no
    reason for a version). True when the thumbnail was replaced."""
    item = s.vault.items.get(pn)
    if item is None or item.item_type not in KINDS:
        return False
    path = s.work.path(pn)
    if not os.path.exists(path):
        return False
    latest = s.vault.latest_version(pn)
    if latest is not None and s.work.local_hash(path) == latest.sha256:
        return False
    import bruceworx_previewmesh

    with opened(path) as doc:
        entries = entries_for(doc)
        entries.update(extras_for(doc, path, item.item_type))
    if THUMBNAIL not in entries:
        return False
    bruceworx_previewmesh.rewrite(path, entries)
    return True


# ----- the cache for files checked in before -----------------------------------------------------


def cache_dir():
    import FreeCAD

    base = getattr(FreeCAD, "getUserCachePath", FreeCAD.getUserAppDataDir)()
    return os.path.join(base, "BruceWorx", "pictures")


def cache_path(sha) -> str:
    return os.path.join(cache_dir(), f"{sha}.png")


def cached(sha) -> str:
    """The cached picture of the version ``sha``, or ""."""
    path = cache_path(sha) if sha else ""
    return path if path and os.path.exists(path) else ""


def needs_picture(s, pn) -> bool:
    """Whether Refresh Pictures has work for ``pn``: a part or assembly whose latest version lacks a
    picture made here (in the file or cached) or a preview mesh (in the file or cached, PDM-13)."""
    import bruceworx_previewmesh

    item = s.vault.items.get(pn)
    latest = s.vault.latest_version(pn)
    if item is None or item.item_type not in KINDS or latest is None:
        return False
    if not latest.filename.lower().endswith((".fcstd", ".vlsim")):  # a KiCad project (ECD-05): KiCad's render is its picture
        return False
    stored = os.path.join(s.vault.root, latest.object_name)
    picture = bool(cached(latest.sha256)) or is_iso(thumbnail_of(stored))
    if item.item_type not in MESH_KINDS:  # a drawing: its sheet picture only
        return not picture
    mesh = bool(bruceworx_previewmesh.cached(latest.sha256)) or bool(bruceworx_previewmesh.read(stored))
    return not (picture and mesh)


def source_for(s, pn):
    """The file to render the latest version of ``pn`` from: the work copy when it is that version
    (its links find the work copies beside it), else the vault's stored file. An assembly is drawn
    from its work copy whenever there is one, even an older version: the stored file's links look
    for parts beside it that are not there (empty pictures, 25 Oct), the work copy's find the work
    copies."""
    latest = s.vault.latest_version(pn)
    work = s.work.path(pn)
    if os.path.exists(work):
        item = s.vault.items.get(pn)
        if s.work.local_hash(work) == latest.sha256 or (item is not None and item.item_type in ("assembly", "drawing")):
            return work
    return os.path.join(s.vault.root, latest.object_name)


def refresh_one(s, pn) -> bool:
    """Render ``pn``'s latest version into the cache. True when a picture was written."""
    import bruceworx_previewmesh

    latest = s.vault.latest_version(pn)
    with opened(source_for(s, pn)) as doc:
        entries = entries_for(doc)
    mesh = entries.get(bruceworx_previewmesh.ENTRY)
    if mesh:  # the preview mesh (PDM-13), cached beside the pictures
        bruceworx_previewmesh.store_cached(latest.sha256, mesh)
    png = entries.get(THUMBNAIL)
    if not png:
        return False
    os.makedirs(cache_dir(), exist_ok=True)
    target = cache_path(latest.sha256)
    with open(target + ".part", "wb") as out:
        out.write(png)
    os.replace(target + ".part", target)
    return True
