# SPDX-License-Identifier: LGPL-2.1-or-later
"""One part per file (ASM-01).

make_external() writes an App::Part or a PartDesign body out of a saved document into its own
FCStd beside that file, and replaces it with an App::Link. The link path is stored relative to
the parent, so the parent still opens after the folder is moved.
"""

import os
import re

import FreeCAD as App

PART_TYPES = ("App::Part", "PartDesign::Body")
_BAD = re.compile(r'[\\/:*?"<>|\x00]+')


def make_external(obj, path=None):
    """Replace ``obj`` with an App::Link to a new FCStd beside the parent document.

    ``obj`` is an App::Part or a PartDesign::Body. The parent must already be saved: FreeCAD
    stores the link relative to that file. The object's placement becomes the link's placement
    and the part file keeps the solid at the origin. Returns the link. The parent is left
    modified; save it to keep the link.
    """
    if obj is None or getattr(obj, "TypeId", "") not in PART_TYPES:
        raise ValueError("make external takes an App::Part or a PartDesign Body")
    parent = obj.Document
    if parent is None or not parent.FileName:
        raise ValueError("Save the file first: the link path is relative to it")

    stem = _BAD.sub("_", (obj.Label or obj.Name or "Part")).strip(" .") or "Part"
    parent_file = os.path.realpath(parent.FileName)
    if path is None:
        path = _beside(os.path.dirname(parent_file), stem, parent_file)
    else:
        path = os.path.realpath(path)
        if os.path.normcase(path) == os.path.normcase(parent_file):
            raise ValueError("The part file cannot replace the parent file")
        if os.path.exists(path):
            raise ValueError(f"{path} already exists")

    name, label = obj.Name, obj.Label
    placement = App.Placement(obj.Placement.Base, obj.Placement.Rotation)
    holders = _holders(obj)

    part_doc = _new_doc(stem)
    App.setActiveDocument(parent.Name)
    try:
        moved = part_doc.moveObject(obj, True)
    except Exception:
        App.closeDocument(part_doc.Name)
        raise
    root = part_doc.getObject(name) or moved
    if root is None:
        App.closeDocument(part_doc.Name)
        raise ValueError(f"Could not move {name}")
    root.Placement = App.Placement()
    part_doc.recompute()
    # FreeCAD canonicalizes the parent directory and not an absolute linked path. On macOS
    # /var is /private/var, so a non-canonical part path is stored as a relative path that
    # still points at the old folder. A canonical part path is stored as "Name.FCStd".
    part_doc.saveAs(path)

    link = parent.addObject("App::Link", name)
    link.Label = label
    link.setLink(root)
    link.Placement = placement
    for group, index in holders:
        _insert(group, link, index)
    parent.recompute()
    return link


def _beside(folder, stem, parent_file):
    for n in range(1, 100):
        name = stem if n == 1 else f"{stem}_{n}"
        path = os.path.realpath(os.path.join(folder, name + ".FCStd"))
        if os.path.normcase(path) != os.path.normcase(parent_file) and not os.path.exists(path):
            return path
    raise ValueError(f"No free file name beside {parent_file}")


def _new_doc(stem):
    base = re.sub(r"[^A-Za-z0-9_]", "_", stem).strip("_") or "Part"
    if base[0].isdigit():
        base = "_" + base
    name, n = base, 2
    while name in App.listDocuments():
        name = f"{base}_{n}"
        n += 1
    return App.newDocument(name, hidden=True)


def _holders(obj):
    spots = []
    for parent in obj.InList:
        group = getattr(parent, "Group", None)
        if not group:
            continue
        for index, child in enumerate(group):
            if child is obj:
                spots.append((parent, index))
                break
    return spots


def _insert(parent, link, index):
    group = [item for item in parent.Group if item is not link]
    group.insert(min(index, len(group)), link)
    parent.Group = group
