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

from heuretes_freecad import pdm

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
    items, drawings, simulations = {}, {}, {}
    for pn, item in vault.items.items():
        code = item.latest.code if item.latest else ""
        items[pn] = {"display": pdm.display_part_number(pn, code), "description": item.description,
                     "category": getattr(item, "category", ""), "type": item.item_type, "state": item.state, "revision": code,
                     "obsolete": bool(getattr(item, "obsolete", False))}
        if item.drawing_of:
            drawings.setdefault(item.drawing_of, []).append(pn)
        if getattr(item, "simulation_of", None):
            items[pn]["simulation_of"] = item.simulation_of
            simulations.setdefault(item.simulation_of, []).append(pn)
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
    for lst in list(drawings.values()) + list(simulations.values()):
        lst.sort()
    out = {"items": items, "bom": bom, "sources": sources, "drawings": drawings, "simulations": simulations,
           "projects": {code: p.top for code, p in (getattr(vault, "projects", None) or {}).items()}}
    out["sims"] = _sim_cards(vault, items) if hasattr(vault, "latest_version") else {}
    return out


def _sim_cards(vault, items) -> dict:
    """{simulation P/N: card} for the simulation items: what the Files list, the part's Simulations tab and the
    project pages show, read from the summary inside the latest file (never by opening the document)."""
    from heuretes_freecad import vault_simulations  # noqa: PLC0415

    cards = {}
    for pn, info in items.items():
        if info["type"] != "simulation":
            continue
        try:
            cards[pn] = vault_simulations.card(vault, pn)
        except Exception as exc:  # noqa: BLE001 -- an unreadable summary leaves a bare card
            cards[pn] = {"pn": pn, "of": info.get("simulation_of", ""), "state": info["state"], "summary": {},
                         "judgement": "unknown", "has_results": None, "version": "", "error": str(exc)}
    return cards


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
        for d in s.get("simulations", {}).get(pn, ()):
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
        if pn in shown or (s["items"][pn]["type"] == "drawing" and s["items"].get(_owner(s, pn))) or (
                s["items"][pn]["type"] == "simulation" and s["items"].get(s["items"][pn].get("simulation_of"))):
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
        from heuretes_freecad import numbering

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
    from heuretes_freecad import numbering

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
        if info["type"] in ("drawing", "simulation") or info.get("obsolete"):
            continue
        out.append(pn)
    return out


# ----- the flat file list (PDM-13, 1 Nov 2026) -----------------------------------------------------
# Every file is one row; what an assembly uses and where a file is used are shown for the selected
# row, as SOLIDWORKS PDM and Autodesk Vault do (the user's choice: "everything should have a top line").

TYPE_ORDER = ("assembly", "part", "cots", "drawing", "simulation")
TYPE_GROUPS = {"assembly": "Assemblies", "part": "Parts", "cots": "Parts", "drawing": "Drawings",
               "simulation": "Simulations"}


def used_in(s) -> dict:
    """{P/N: [(assembly, qty), ...]}: the assemblies whose lines name each item, sorted."""
    out = {}
    for parent, lines in s["bom"].items():
        for child, qty in lines:
            out.setdefault(child, []).append((parent, qty))
    for lst in out.values():
        lst.sort()
    return out


def drawing_owner(s, drawing):
    """The item a drawing is of, or None."""
    return _owner(s, drawing)


def contents(s, pn) -> set:
    """``pn`` and everything its lines reach at any depth, with every one's drawings (cycles stop)."""
    seen, todo = set(), [pn]
    while todo:
        p = todo.pop()
        if p in seen or p not in s["items"]:
            continue
        seen.add(p)
        seen.update(d for d in s["drawings"].get(p, ()) if d in s["items"])
        seen.update(d for d in s.get("simulations", {}).get(p, ()) if d in s["items"])
        todo.extend(child for child, _q in s["bom"].get(p, ()))
    return seen


def bought(info) -> bool:
    """A bought-in item (COTS): its own type, or an envelope named COTS in its description."""
    return info.get("type") == "cots" or "COTS" in (info.get("description") or "")


def missing_drawing(s, pn) -> bool:
    """A part or assembly that should have a drawing and has none: not bought in, not obsolete."""
    info = s["items"].get(pn) or {}
    if info.get("type") not in ("part", "assembly") or info.get("obsolete") or bought(info):
        return False
    return not s["drawings"].get(pn)


def type_rank(kind) -> int:
    return TYPE_ORDER.index(kind) if kind in TYPE_ORDER else len(TYPE_ORDER)


# ----- simulations (PDM-14, 1 Nov 2026) -----------------------------------------------------------


def _scope_parts(s, scope):
    """The set of items whose simulations a scope wants; None for no limit.

    ``scope``: None (every simulation); ``"asm:<P/N>"`` (that assembly and everything it reaches);
    ``"project:<code>"`` (the project's top assembly and everything it reaches); a set, list or tuple of
    part numbers (the items whose simulations to list)."""
    if scope in (None, "", "all"):
        return None
    if isinstance(scope, (set, frozenset, list, tuple)):
        return set(scope)
    key, _sep, value = str(scope).partition(":")
    if key == "asm":
        return contents(s, value)
    if key == "project":
        top = (s.get("projects") or {}).get(value)
        return contents(s, top) if top else set()
    return set()


def _fmt(value, digits=4):
    return f"{value:.{digits}g}" if isinstance(value, (int, float)) else ""


def result_text(summary, has_results) -> str:
    """The Result column: "max 252.5 MPa, SF 1.31" or why there is none."""
    summary = summary or {}
    if not summary:
        return "not read"
    parts = []
    if summary.get("max_vm") is not None:
        parts.append(f"max {_fmt(summary['max_vm'])} MPa")
    if summary.get("min_sf") is not None:
        parts.append(f"SF {_fmt(summary['min_sf'], 3)}")
    text = ", ".join(parts)
    if not has_results:
        return (text + " (results not included)") if text else ("no results" if summary.get("study") else "no study")
    return text or "solved"


def study_text(summary) -> str:
    summary = summary or {}
    mesh = summary.get("mesh") or {}
    bits = [summary.get("study") or "", summary.get("study_type") or "", (summary.get("material") or {}).get("name", "")]
    if mesh.get("nodes"):
        bits.append(f"{mesh['nodes']:,} nodes")
    return " · ".join(b for b in bits if b)


def simulations(s, scope=None) -> list:
    """Rows for the Simulations lists (PDM-14), sorted by part, then simulation number. One dict per
    non-obsolete simulation item whose part is in ``scope`` (see :func:`_scope_parts`):

    ``pn`` / ``display`` the simulation; ``of`` / ``of_display`` the part or assembly it analyses; ``state``
    its lifecycle state; ``study`` the Study column text; ``result`` the Result column text; ``judgement``
    "current", "part changed" or "unknown" (the Geometry column); ``changed`` True for "part changed";
    ``has_results`` True / False / None; ``summary`` the raw summary dict; ``description``."""
    limit = _scope_parts(s, scope)
    rows = []
    for pn, card in (s.get("sims") or {}).items():
        info = s["items"].get(pn) or {}
        owner = card.get("of") or info.get("simulation_of", "")
        if info.get("obsolete") or (limit is not None and owner not in limit):
            continue
        owner_info = s["items"].get(owner) or {}
        summary = card.get("summary") or {}
        rows.append({"pn": pn, "display": info.get("display", pn), "of": owner,
                     "of_display": owner_info.get("display", owner), "state": info.get("state", ""),
                     "description": info.get("description", ""), "study": study_text(summary),
                     "result": result_text(summary, card.get("has_results")),
                     "judgement": card.get("judgement", "unknown"), "changed": card.get("judgement") == "part changed",
                     "has_results": card.get("has_results"), "summary": summary})
    rows.sort(key=lambda r: (r["of"], r["pn"]))
    return rows


def parts_with_changed_simulation(s) -> dict:
    """{part P/N: [simulation P/N]} for parts that have a simulation marked "part changed" (the bubble flag)."""
    out = {}
    for row in simulations(s):
        if row["changed"]:
            out.setdefault(row["of"], []).append(row["pn"])
    return out
