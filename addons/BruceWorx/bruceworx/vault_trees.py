# SPDX-License-Identifier: LGPL-2.1-or-later
"""The vault's structure for the Start page's Files tab (UAT, 25 Oct 2026).

The Files tab lists every file as one tree (``file_tree``): each top assembly down through its parts,
every item with its drawings under it, then the files no assembly uses. Selecting a file there shows
where it is used. Built from one ``structure`` snapshot (no FreeCAD needed).

An assembly's lines are its recorded BOM (PDM-06, at check-in). An assembly checked in without one has
its lines read from the links in its latest file (``file_links``), marked "links", so a vault made
before BOMs were recorded still shows its structure.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

from bruceworx import pdm

_PN_FILE = re.compile(r"^(?P<pn>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)\.FCStd$", re.I)
LINK_TYPES = ("App::Link",)


# ----- reading a file's links ---------------------------------------------------------------------


def file_links(path: str) -> dict:
    """{P/N: occurrences} of the vault files an assembly file links to, from its Document.xml: one per
    App::Link to another file (an array link counts its elements)."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Document.xml"))
    types = {o.get("name"): o.get("type") for o in root.iter("Object") if o.get("type")}
    out = {}
    for obj in root.iter("Object"):
        name = obj.get("name")
        if types.get(name) not in LINK_TYPES:
            continue
        target, count = None, 1
        for prop in obj.iter("Property"):
            pname = prop.get("name")
            if pname == "LinkedObject":
                xlink = prop.find("XLink")
                if xlink is not None and xlink.get("file"):
                    target = os.path.basename(xlink.get("file"))
            elif pname == "ElementCount":
                value = prop.find("Integer")
                if value is not None and int(value.get("value", "0")) > 0:
                    count = int(value.get("value"))
        match = _PN_FILE.match(target or "")
        if match:
            pn = match.group("pn").upper()
            out[pn] = out.get(pn, 0) + count
    return out


# ----- the snapshot -------------------------------------------------------------------------------


def structure(vault, links=None) -> dict:
    """Everything the Files tree and where used need, JSON-ready. ``links(pn)`` gives an assembly's {child: qty}
    from its latest file (default: read it from the vault's object store)."""
    if links is None:
        links = lambda pn: _links_of(vault, pn)  # noqa: E731
    items, drawings = {}, {}
    for pn, item in vault.items.items():
        code = item.latest.code if item.latest else ""
        items[pn] = {"display": pdm.display_part_number(pn, code), "description": item.description,
                     "category": getattr(item, "category", ""), "type": item.item_type, "state": item.state, "revision": code,
                     "obsolete": bool(getattr(item, "obsolete", False))}
        if item.drawing_of:
            drawings.setdefault(item.drawing_of, []).append(pn)
    bom, sources = {}, {}
    recorded = getattr(vault, "bom", {}) or {}
    for pn, item in vault.items.items():
        if item.item_type != "assembly":
            continue
        lines = recorded.get(pn)
        if lines:
            bom[pn] = [[child, qty] for child, qty in lines]
            sources[pn] = "bom"
            continue
        try:
            found = links(pn) or {}
        except Exception:  # noqa: BLE001 -- an unreadable file: no lines, said so
            sources[pn] = "unreadable"
            continue
        found = {c: q for c, q in found.items() if c in vault.items and c != pn}
        if found:
            bom[pn] = [[child, qty] for child, qty in sorted(found.items())]
            sources[pn] = "links"
    for lst in drawings.values():
        lst.sort()
    return {"items": items, "bom": bom, "sources": sources, "drawings": drawings}


def _links_of(vault, pn):
    version = vault.latest_version(pn) if hasattr(vault, "latest_version") else None
    if version is None:
        return {}
    path = os.path.join(vault.root, version.object_name)
    if not os.path.exists(path):
        return {}
    return file_links(path)


# ----- the Files tree (rows: dicts with "children") ------------------------------------------------


def tops(s) -> list:
    """Assemblies no other assembly uses, in P/N order."""
    used = {child for lines in s["bom"].values() for child, _q in lines}
    return sorted(pn for pn, info in s["items"].items()
                  if info["type"] == "assembly" and pn not in used and not info.get("obsolete"))


LOOSE = "Not in an assembly"


def file_tree(s) -> list:
    """The Files tab's tree: [node], every vault item at least once. Each top assembly with its lines
    below it (``qty``), every item with its drawings first among its children; then a group node
    (``pn`` None, ``group`` LOOSE) holding what no tree reaches: parts no assembly uses, drawings whose
    part is missing, obsolete or cyclic assemblies. Nodes are {"pn", "qty", "children"}."""
    shown = set()

    def drawings(pn):
        out = []
        for d in s["drawings"].get(pn, ()):
            shown.add(d)
            out.append({"pn": d, "qty": None, "children": []})
        return out

    def down(pn, qty, path):
        shown.add(pn)
        node = {"pn": pn, "qty": qty, "children": drawings(pn)}
        if pn not in path:
            for child, q in s["bom"].get(pn, ()):
                if child in s["items"]:
                    node["children"].append(down(child, q, path | {pn}))
        return node

    out = [down(top, None, frozenset()) for top in tops(s)]
    loose = []
    for pn in sorted(s["items"]):
        if pn in shown or (s["items"][pn]["type"] == "drawing" and s["items"].get(_owner(s, pn))):
            continue
        loose.append(down(pn, None, frozenset()))
    for pn in sorted(s["items"]):  # a drawing of an item that only sits in a cycle, say
        if pn not in shown:
            shown.add(pn)
            loose.append({"pn": pn, "qty": None, "children": []})
    if loose:
        out.append({"pn": None, "group": LOOSE, "qty": None, "children": loose})
    return out


def _owner(s, drawing):
    return next((owner for owner, dwgs in s["drawings"].items() if drawing in dwgs), None)


# ----- programs (the first field of the part number) -----------------------------------------------


def program_code(part_number: str) -> str:
    """The program, the first field of a part number (``LE`` in ``LE-01-01001-001``). "" when it is not one."""
    text = str(part_number or "").strip()
    if not text:
        return ""
    try:
        from bruceworx import numbering

        return numbering.parse_part_number(pdm.strip_revision(text))["program"]
    except ValueError:
        return ""


def program_names(root: str) -> dict:
    """{code: description} from the vault's numbering.json. A missing or retired program is left out."""
    path = os.path.join(root or "", "numbering.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    from bruceworx import numbering

    out = {}
    for code, entry in (data.get("programs") or {}).items():
        if not isinstance(entry, dict) or entry.get("retired"):
            continue
        try:
            key = numbering.normalize_program(code)
        except ValueError:
            continue
        out[key] = str(entry.get("description") or "")
    return out


def program_roots(s, code: str) -> list:
    """The items that head a program's tree: not a drawing, not obsolete, and not used by an
    assembly of the same program (that assembly shows them)."""
    used = set()
    for parent, lines in s["bom"].items():
        if program_code(parent) != code:
            continue
        for child, _qty in lines:
            if program_code(child) == code:
                used.add(child)
    out = []
    for pn in sorted(s["items"]):
        if program_code(pn) != code or pn in used:
            continue
        info = s["items"][pn]
        if info["type"] == "drawing" or info.get("obsolete"):
            continue
        out.append(pn)
    return out
