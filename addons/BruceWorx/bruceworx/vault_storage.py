# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault storage migration (PRF-04, 2 Nov 2026): store a vault's FreeCAD files with binary shapes.

Bruce asked for every vault file in binary (PRF-01) without check-outs or a new version per item. Stored
files are write-once and verified by SHA-256 (pdm_folder), so they are not edited: each version's file is
re-encoded into a new object, every shape compared with the original, and one ``migrate_storage`` journal
event maps old SHA-256 -> new for the whole vault. Part numbers, revisions and version numbers do not
change; the old objects stay (a Vector Labs copy that does not know the operation reads them as before).

Re-encoding a ``.FCStd`` is a zip rewrite, not a FreeCAD save: each shape entry ``X.brp`` (text BREP) that
Document.xml names as ``file="X.brp"`` becomes ``X.bin`` (OpenCascade binary) and the reference follows;
every other entry (view data, thumbnails, preview mesh, element maps) is copied byte for byte. FreeCAD reads
a shape by its extension (PropertyPartShape/PropertyTopoShapeList::RestoreDocFile), so the file opens the
same. The shape conversion itself needs OpenCascade: :func:`freecad_converter` in FreeCAD, or any
``convert(text_bytes) -> (binary_bytes, facts_before, facts_after)`` in tests. This module stays free of
FreeCAD.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

from bruceworx.pdm_folder import object_name, sha256_of

FORMAT = "binary-brep"
NOTE = "PRF-04: shapes stored as binary BREP (lossless, checked shape by shape)"
_REF = re.compile(rb'file="([^"]+)\.brp"')


class ShapeMismatch(ValueError):
    """A converted shape is not the same as the original: the migration stops before recording anything."""


@dataclass
class Report:
    files: int = 0            # stored files looked at
    converted: int = 0        # re-encoded
    already: int = 0          # no text shapes (binary already, or no shapes)
    shapes: int = 0           # shape entries converted
    bytes_before: int = 0
    bytes_after: int = 0
    objects: dict = field(default_factory=dict)  # old sha256 -> new record
    skipped: list = field(default_factory=list)  # (P/N, reason)
    recorded: bool = False


def text_shapes(data: bytes, names: list) -> list:
    """The ``.brp`` entries Document.xml references, in archive order (empty ones left alone)."""
    doc = data
    wanted = {m.decode("utf-8") + ".brp" for m in _REF.findall(doc)}
    return [n for n in names if n in wanted]


def convert_fcstd(src: str, dst: str, convert) -> int:
    """Rewrite the archive ``src`` into ``dst`` with every referenced text shape converted by ``convert``;
    returns how many shapes were converted (0: ``dst`` is not written). Raises ShapeMismatch when a converted
    shape's facts differ from the original's."""
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        names = [i.filename for i in infos]
        if "Document.xml" not in names:
            return 0
        doc = zin.read("Document.xml")
        todo = [n for n in text_shapes(doc, names) if zin.getinfo(n).file_size > 0]
        taken = set(names)
        renamed = {}
        for name in todo:
            new = name[:-4] + ".bin"
            if new in taken:  # a name clash: leave that shape as text
                continue
            renamed[name] = new
            taken.add(new)
        if not renamed:
            return 0
        for old, new in renamed.items():
            doc = doc.replace(f'file="{old}"'.encode("utf-8"), f'file="{new}"'.encode("utf-8"))
        tmp = dst + ".part"
        with zipfile.ZipFile(tmp, "w") as zout:
            for info in infos:
                data = zin.read(info.filename)
                if info.filename == "Document.xml":
                    zout.writestr(info, doc)
                elif info.filename in renamed:
                    binary, before, after = convert(data)
                    if before != after:
                        raise ShapeMismatch(f"{os.path.basename(src)} {info.filename}: {before} != {after}")
                    entry = zipfile.ZipInfo(renamed[info.filename], date_time=info.date_time)
                    entry.compress_type = info.compress_type
                    entry.external_attr = info.external_attr
                    zout.writestr(entry, binary)
                else:
                    zout.writestr(info, data)
        os.replace(tmp, dst)
    return len(renamed)


def migrate(vault, convert, dry_run: bool = False, progress=None, note: str = NOTE) -> Report:
    """Re-encode every stored FreeCAD file version of ``vault`` (a FolderVault) and record one
    ``migrate_storage`` event. Files without text shapes are left as they are. With ``dry_run`` nothing is
    written to the vault: the files are converted and checked in a temporary folder and counted.
    ``progress(done, total, part_number)`` may return False to stop (nothing is recorded then)."""
    report = Report()
    versions = [(pn, v) for pn, vs in sorted(vault.versions.items()) for v in vs
                if v.filename.lower().endswith(".fcstd")]
    seen = set()
    work = tempfile.mkdtemp(prefix="bw-migrate-")
    try:
        for done, (pn, version) in enumerate(versions, 1):
            if progress is not None and progress(done, len(versions), pn) is False:
                report.skipped.append((pn, "stopped"))
                return report
            if version.sha256 in seen:
                continue
            seen.add(version.sha256)
            report.files += 1
            source = os.path.join(vault.root, version.object_name)
            if not os.path.exists(source):
                report.skipped.append((pn, "not synced to this device"))
                continue
            if sha256_of(source) != version.sha256:
                report.skipped.append((pn, "stored file does not match its SHA-256"))
                continue
            target = os.path.join(work, f"{version.sha256}", version.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                count = convert_fcstd(source, target, convert)
            except ShapeMismatch:
                raise
            except (zipfile.BadZipFile, KeyError, OSError) as exc:
                report.skipped.append((pn, f"cannot read: {exc}"))
                continue
            if count == 0:
                report.already += 1
                continue
            report.converted += 1
            report.shapes += count
            report.bytes_before += os.path.getsize(source)
            report.bytes_after += os.path.getsize(target)
            if dry_run:
                digest = sha256_of(target)
                report.objects[version.sha256] = {"sha256": digest, "size": os.path.getsize(target),
                                                  "filename": version.filename}
            else:
                report.objects[version.sha256] = vault.store_object(target)
        if report.objects and not dry_run:
            vault.migrate_storage(report.objects, FORMAT, note)
            report.recorded = True
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def refresh_work_copies(workspace) -> list:
    """After a migration: work copies that are unchanged and not checked out are replaced by the binary
    version (Get Latest), so opening them is faster too. Returns the part numbers refreshed."""
    vault = workspace.vault
    refreshed = []
    for pn in sorted(vault.versions):
        base = workspace.base(pn)
        if not base or base not in vault.migrated or vault.locks.get(pn):
            continue
        path = workspace.path(pn)
        if not os.path.exists(path) or workspace.local_hash(path) != base:
            continue
        latest = vault.latest_version(pn)
        if latest is None or latest.sha256 != vault.canonical(base):
            continue
        workspace.get_latest(pn)
        refreshed.append(pn)
    return refreshed


def freecad_converter():
    """The real converter (FreeCAD's Part module): text BREP -> OpenCascade binary, with the facts of both
    shapes (type, counts of solids, shells, faces, edges, vertices; volume, area, bounding box)."""
    import Part  # noqa: PLC0415

    def facts(shape):
        if shape.isNull():
            return ("null",)
        b = shape.BoundBox
        volume = round(shape.Volume, 6) if shape.Solids else 0.0
        return (shape.ShapeType, len(shape.Solids), len(shape.Shells), len(shape.Faces), len(shape.Edges),
                len(shape.Vertexes), volume, round(shape.Area, 6),
                tuple(round(v, 6) for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)) if b.isValid() else ())

    def convert(text: bytes):
        before = Part.Shape()
        before.importBrepFromString(text.decode("utf-8", "replace"), False)
        handle, path = tempfile.mkstemp(suffix=".bin")
        os.close(handle)
        try:
            before.exportBinary(path)
            with open(path, "rb") as fh:
                binary = fh.read()
            after = Part.Shape()
            after.importBinary(path)
        finally:
            os.remove(path)
        return binary, facts(before), facts(after)

    return convert


def copy_preview_cache(objects: dict, cache_dirs) -> int:
    """Previews on this Mac are cached by SHA-256 (PDM-13); give each new SHA-256 the old one's files."""
    copied = 0
    for folder in cache_dirs:
        if not os.path.isdir(folder):
            continue
        names = os.listdir(folder)
        for old, record in objects.items():
            for name in names:
                if name.startswith(old):
                    new = record["sha256"] + name[len(old):]
                    target = os.path.join(folder, new)
                    if not os.path.exists(target):
                        shutil.copyfile(os.path.join(folder, name), target)
                        copied += 1
    return copied


__all__ = ["FORMAT", "Report", "ShapeMismatch", "convert_fcstd", "migrate", "refresh_work_copies",
           "freecad_converter", "copy_preview_cache", "object_name"]
