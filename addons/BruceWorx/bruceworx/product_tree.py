# SPDX-License-Identifier: LGPL-2.1-or-later
"""The product tree with drawings (BW-PDM-09, plan section 3; built 26 Oct 2026).

The Structure view of the right panel's Vault tab reads this: for a root (a project's top
assembly, or any assembly) the rows one level at a time, each with its lifecycle state and the
state of its drawing, and the counts an assembly row carries for the filter chips. The rows come
from :func:`bruceworx.vault_trees.structure`: the per-revision BOM (PDM-06) when one is recorded,
else the checked-in file's links, marked ``"links"``. Drawings never become rows: they are the
``drawing`` cell of their item's row. No FreeCAD import.

When is a drawing out of date? (plan, "Theory".) A drawing D of model m was made from the files it
links, its closure C(D): for a part drawing the part, for an assembly drawing the assembly and
everything under it. Its check-in recorded r_D = {x -> the version of x its work copy held}
(``FileVersion.made_from``). With v(x) the latest version of x now (the released one for a
released drawing):

    state(D) = out of date   if some x in C(D) has r_D(x) != v(x) and the geometry changed,
               metadata only if every such x has the same fingerprint at both versions
                             (a description edit; the title block refreshes itself),
               current       otherwise.

A drawing checked in before PDM-09 has no r_D: it falls back to the time comparison ("D checked
in before a newer version of some x") and says so (``basis`` "time").
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import zipfile

from bruceworx import pdm

# The Drawing cell's states, worst first.
OUT_OF_DATE = "out_of_date"
NO_FILE = "no_file"  # a drawing item exists, nothing is checked in yet
MISSING = "missing"  # the item needs a drawing and has none
METADATA_ONLY = "metadata_only"
CURRENT = "current"
IN_MODEL = "in_model"  # the part's own file holds a TechDraw page (DRW-01's New Drawing today)
NOT_NEEDED = "not_needed"  # a bought part
RANK = (OUT_OF_DATE, NO_FILE, MISSING, METADATA_ONLY, CURRENT, IN_MODEL, NOT_NEEDED)
LABELS = {
    OUT_OF_DATE: "Out of date",
    NO_FILE: "No file yet",
    MISSING: "Missing",
    METADATA_ONLY: "Metadata only",
    CURRENT: "Current",
    IN_MODEL: "In model",
    NOT_NEEDED: "None needed",
}
# The filter chips (plan): key -> text.
CHIPS = (("needs_drawing", "Needs a drawing"), ("out_of_date", "Out of date"), ("checked_out", "Checked out"))
FINGERPRINT_DIGITS = 7  # significant digits kept: about 1e-6 relative
_PAGE = re.compile(rb'type="TechDraw::DrawPage')


# ----- geometry fingerprints ------------------------------------------------------------------------


def round_sig(value, digits=FINGERPRINT_DIGITS):
    """``value`` to ``digits`` significant digits (0.0 stays 0.0)."""
    value = float(value)
    return 0.0 if value == 0 else float(f"{value:.{digits}g}")


def make_fingerprint(volume, area, centre, box, solids=1) -> dict:
    """A part's fingerprint from its mass properties, every number rounded (so two reads of the same
    geometry compare equal). ``centre`` is (x, y, z), ``box`` (xmin, ymin, zmin, xmax, ymax, zmax)."""
    return {"volume": round_sig(volume), "area": round_sig(area), "centre": [round_sig(c) for c in centre],
            "box": [round_sig(b) for b in box], "solids": int(solids)}


def fingerprints_equal(a, b) -> bool:
    """Equal geometry: both present and the same after rounding (a missing one never equals)."""
    if not a or not b:
        return False
    try:
        return (round_sig(a["volume"]) == round_sig(b["volume"]) and round_sig(a["area"]) == round_sig(b["area"])
                and [round_sig(c) for c in a["centre"]] == [round_sig(c) for c in b["centre"]]
                and [round_sig(c) for c in a["box"]] == [round_sig(c) for c in b["box"]])
    except (KeyError, TypeError, ValueError):
        return False


# ----- the graph --------------------------------------------------------------------------------------


def closure(bom, pn) -> list:
    """``pn`` and everything below it through ``bom`` ({parent: [[child, qty], ...]}), breadth first,
    each once; a cycle in a damaged journal is cut."""
    out, queue, seen = [], [pn], {pn}
    while queue:
        current = queue.pop(0)
        out.append(current)
        for child, _qty in bom.get(current, ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return out


def parents_of(bom) -> dict:
    """{child: [parent]} over the structure's lines."""
    out = {}
    for parent, lines in bom.items():
        for child, _qty in lines:
            out.setdefault(child, []).append(parent)
    return out


def _parse_time(text):
    try:
        when = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=_dt.timezone.utc)


# ----- the state of one drawing --------------------------------------------------------------------


def _version_now(vault, pn, frozen):
    item = vault.items.get(pn)
    if item is None:
        return None
    if frozen and item.released is not None:
        released = vault.released_version(pn)
        if released is not None:
            return released
    return vault.latest_version(pn)


def drawing_state(vault, drawing, bom) -> dict:
    """The state of one drawing item: {"state", "basis" ("made_from" | "time" | "none"), "changed":
    [(P/N, "geometry" | "metadata" | "newer")], "model"}. ``bom`` is the structure's lines, so an
    assembly drawing's closure reaches every part under the assembly (plan question 9)."""
    item = vault.items[drawing]
    model = item.drawing_of
    version = vault.latest_version(drawing)
    out = {"state": NO_FILE, "basis": "none", "changed": [], "model": model, "drawing": drawing}
    if version is None or not model:
        return out
    members = closure(bom, model)
    frozen = item.state == pdm.RELEASED
    made_from = version.made_from
    changed = []
    if made_from is None:
        # Before PDM-09: a time comparison, which a note fix after the model change hides (plan).
        when = _parse_time(version.at)
        for x in members:
            now = _version_now(vault, x, frozen)
            then = _parse_time(now.at) if now is not None else None
            if when is not None and then is not None and then > when:
                changed.append((x, "newer"))
        out.update(basis="time", changed=changed, state=OUT_OF_DATE if changed else CURRENT)
        return out
    for x in members:
        now = _version_now(vault, x, frozen)
        if now is None:
            continue
        was = made_from.get(x)
        if was == now.sha256:
            continue
        then = vault.version_with(x, was) if was else None
        same = then is not None and fingerprints_equal(then.fingerprint, now.fingerprint)
        changed.append((x, "metadata" if same else "geometry"))
    if not changed:
        state = CURRENT
    elif all(kind == "metadata" for _x, kind in changed):
        state = METADATA_ONLY
    else:
        state = OUT_OF_DATE
    out.update(basis="made_from", changed=changed, state=state)
    return out


def state_text(found: dict) -> str:
    """The words under the Drawing cell: why it is what it is."""
    state, changed, basis = found["state"], found.get("changed", []), found.get("basis", "")
    if state == NO_FILE:
        return "the drawing item has no file checked in yet"
    if state == CURRENT:
        return "made from the model as it is now" if basis == "made_from" else \
            "checked in after its model (time comparison: made before PDM-09)"
    if state == METADATA_ONLY:
        names = ", ".join(x for x, _k in changed)
        return f"{names}: only the metadata changed since; the geometry is the same"
    if state == OUT_OF_DATE:
        if basis == "time":
            names = ", ".join(x for x, _k in changed)
            return f"{names} checked in after the drawing (time comparison: made before PDM-09)"
        names = ", ".join(x for x, kind in changed if kind == "geometry")
        return f"made from an older {names}"
    return LABELS.get(state, state)


# ----- the Drawing cell of an item -----------------------------------------------------------------


def file_holds_page(path: str) -> bool:
    """True when an .FCStd holds a TechDraw page (DRW-01's New Drawing puts the page in the part)."""
    try:
        with zipfile.ZipFile(path) as archive:
            return bool(_PAGE.search(archive.read("Document.xml")))
    except (OSError, KeyError, zipfile.BadZipFile):
        return False


_pages = {}  # sha256 -> holds a page (an object never changes)


def item_holds_page(vault, pn) -> bool:
    version = vault.latest_version(pn) if hasattr(vault, "latest_version") else None
    if version is None:
        return False
    if version.sha256 not in _pages:
        path = os.path.join(vault.root, version.object_name)
        _pages[version.sha256] = os.path.exists(path) and file_holds_page(path)
    return _pages[version.sha256]


def drawing_cell(vault, pn, bom, holds_page=None) -> dict:
    """The Drawing cell of an item's row: {"state", "text", "drawings": [P/N], "states": {P/N: found}}.
    Several drawings: the worst state shows. ``holds_page(pn)`` says whether the item's own file holds
    a page (default: read the stored file)."""
    item = vault.items[pn]
    if item.item_type == "cots":
        return {"state": NOT_NEEDED, "text": "bought part", "drawings": [], "states": {}}
    drawings = [d.part_number for d in vault.drawings_of(pn) if not getattr(d, "obsolete", False)]
    if not drawings:
        holds = holds_page(pn) if holds_page is not None else item_holds_page(vault, pn)
        if holds:
            return {"state": IN_MODEL, "text": "a page in the part's own file", "drawings": [], "states": {}}
        return {"state": MISSING, "text": "no drawing of this item in the vault", "drawings": [], "states": {}}
    states = {d: drawing_state(vault, d, bom) for d in drawings}
    worst = min(states.values(), key=lambda f: RANK.index(f["state"]))
    text = state_text(worst)
    if len(drawings) > 1:
        text = f"{worst['drawing']}: {text}"
    return {"state": worst["state"], "text": text, "drawings": drawings, "states": states}


# ----- rows and counts --------------------------------------------------------------------------------


class Tree:
    """The rows under one root, computed lazily and memoised for one refresh.

    ``s`` is a ``vault_trees.structure`` snapshot; ``vault`` the FolderVault it came from.
    """

    def __init__(self, vault, s, holds_page=None):
        self.vault = vault
        self.s = s
        self.bom = s["bom"]
        self.holds_page = holds_page
        self._cells = {}
        self._counts = {}

    def cell(self, pn) -> dict:
        if pn not in self._cells:
            self._cells[pn] = drawing_cell(self.vault, pn, self.bom, self.holds_page)
        return self._cells[pn]

    def lock(self, pn) -> str:
        """Who holds the item or one of its drawings."""
        locks = getattr(self.vault, "locks", {})
        for key in [pn] + self.cell(pn)["drawings"]:
            if locks.get(key):
                return locks[key]
        return ""

    def matches(self, pn, chip) -> bool:
        """Does the item itself match a filter chip?"""
        state = self.cell(pn)["state"]
        if chip == "needs_drawing":
            return state in (MISSING, NO_FILE)
        if chip == "out_of_date":
            return state == OUT_OF_DATE
        if chip == "checked_out":
            return bool(self.lock(pn))
        return True

    def counts(self, pn) -> dict:
        """How many items strictly below ``pn`` match each chip (the count an assembly row carries)."""
        if pn not in self._counts:
            below = closure(self.bom, pn)[1:]
            self._counts[pn] = {chip: sum(1 for x in below if x in self.s["items"] and self.matches(x, chip))
                                for chip, _text in CHIPS}
        return self._counts[pn]

    def shows(self, pn, chip) -> bool:
        """A row stays under a chip when it matches or hides a match below it."""
        return not chip or chip == "all" or self.matches(pn, chip) or self.counts(pn)[chip] > 0

    def row(self, pn, qty=None, parent=None) -> dict:
        info = self.s["items"][pn]
        cell = self.cell(pn)
        return {
            "pn": pn, "display": info["display"], "description": info["description"], "type": info["type"],
            "state": info["state"], "revision": info["revision"], "qty": qty, "parent": parent,
            "source": self.s["sources"].get(pn, "") if info["type"] == "assembly" else "",
            "lock": self.lock(pn), "drawing": cell, "has_children": bool(self.bom.get(pn)),
            "counts": self.counts(pn) if self.bom.get(pn) else {chip: 0 for chip, _t in CHIPS},
        }

    def children(self, pn, chip=None) -> list:
        """The rows one level under ``pn`` (lazy: the view asks when a row opens)."""
        return [self.row(child, qty, pn) for child, qty in self.bom.get(pn, ())
                if child in self.s["items"] and self.shows(child, chip)]

    def flat(self, root, chip=None) -> list:
        """Every item under ``root`` once (the List view), with its total quantity, in P/N order."""
        totals = roll_up(self.bom, root)
        return [dict(self.row(pn, totals.get(pn)), parent=None) for pn in sorted(totals)
                if pn in self.s["items"] and (not chip or chip == "all" or self.matches(pn, chip))]

    def out_of_date(self, root) -> list:
        """The drawings under ``root`` (root included) that are out of date, for Update drawings..."""
        found = []
        for pn in closure(self.bom, root):
            if pn not in self.s["items"]:
                continue
            for drawing, state in self.cell(pn)["states"].items():
                if state["state"] == OUT_OF_DATE and drawing not in found:
                    found.append(drawing)
        return found


def roll_up(bom, root) -> dict:
    """Total quantity of every item under ``root`` (the plan's Q_t), ``root`` excluded: the sum
    over paths of the product of quantities, in topological order (PDM-06, O(|R| + |E|))."""
    order, seen = [], set()

    def visit(node, active):
        if node in seen:
            return
        seen.add(node)
        for child, _qty in bom.get(node, ()):
            if child not in active:
                visit(child, active | {child})
        order.append(node)

    visit(root, {root})
    total = {root: 1}
    for node in reversed(order):
        for child, qty in bom.get(node, ()):
            total[child] = total.get(child, 0) + total.get(node, 0) * qty
    total.pop(root, None)
    return total


def roots(vault, s) -> list:
    """What the root picker offers: the open projects first (each at its top), then every assembly
    that is not obsolete. Rows {"key", "text", "pn", "project", "units"}."""
    out = []
    for project in (getattr(vault, "projects", None) or {}).values():
        if project.status == "Open" and project.top in s["items"]:
            out.append({"key": "project:" + project.code, "text": f"{project.name} ({project.code})",
                        "pn": project.top, "project": project.code, "units": project.units})
    for pn in sorted(s["items"]):
        info = s["items"][pn]
        if info["type"] == "assembly" and not info.get("obsolete"):
            text = f"{info['display']}  {info['description']}".strip()
            out.append({"key": "assembly:" + pn, "text": text, "pn": pn, "project": "", "units": ""})
    return out


def where_used_text(bom, s, pn) -> str:
    """One line for a row's tooltip: the assemblies that use it, by display name."""
    users = parents_of(bom).get(pn, [])
    if not users:
        return "not used by an assembly"
    return "used in " + ", ".join(s["items"][u]["display"] for u in sorted(users) if u in s["items"])
