# SPDX-License-Identifier: LGPL-2.1-or-later
"""The release checklist (BW-PDM-05, plan approved 23 Oct 2026).

With one person in the vault you approve your own work, with a checklist that is
recorded with the approval. Each line is checked by the tool where it can be and
ticked by you where it cannot:

1. the file is checked in, with no local changes (automatic);
2. a drawing exists, is checked in, and its title block shows this P/N (automatic;
   a COTS item, or an assembly marked "no drawing", skips it);
3. for an assembly, every component is released, or is listed with the reason it
   is not (automatic list; you give the reason);
4. the drawing has been reviewed against the model (your tick);
5. a release note (required text; one per approval or per ECO);
6. (PDM-09, 26 Oct 2026) the drawing is current: made from the model as it is now, by the
   version its check-in recorded (``made_from``); "metadata only" passes. A *warning* line
   (answer 6 of the PDM-09 plan): it shows orange and never blocks the release.

The same lines serve Approve (first release, rev A) and Release ECO (one set per
item; items of the same ECO count as released for each other's component line).
No FreeCAD import: the FreeCAD side (bruceworx_changes.py) shows the lines.
"""

from __future__ import annotations

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

from bruceworx import pdm
from bruceworx import pdm_workspace as pw
from bruceworx.pdm_folder import object_name

CHECKED_IN = "checked_in"
DRAWING = "drawing"
COMPONENTS = "components"
REVIEWED = "drawing_reviewed"
DRAWING_CURRENT = "drawing_current"  # PDM-09
INTERFERENCE = "interference"  # ASM-06
NOTE = "note"

AUTO = "auto"
MANUAL = "manual"
WARN = "warn"  # checked by the tool, shown when not met, never a blocker (PDM-09 answer 6)

TEXTS = {
    CHECKED_IN: "Checked in, no local changes",
    DRAWING: "Drawing checked in, title block shows this P/N",
    COMPONENTS: "Every component released",
    REVIEWED: "Drawing reviewed against the model",
    DRAWING_CURRENT: "Drawing current (made from this model)",
    INTERFERENCE: "No clashes between the parts (interference check)",
}

# Title-block fields that hold the part number, compared without case, "_" or " ".
# "part_number" is the BruceWorx templates' field (DRW-01); "drawing_number" is the
# stock ASME templates'.
PN_FIELDS = ("partnumber", "pn", "drawingnumber", "dwgno", "drawingno")


@dataclass
class Line:
    key: str
    text: str
    kind: str = AUTO
    ok: bool = False
    skipped: bool = False  # not applicable to this item
    detail: str = ""
    entries: list = field(default_factory=list)  # [[P/N or file, reason]] for the component list
    waiver: str = ""  # components: why releasing with unreleased components is fine

    @property
    def passed(self):
        return self.skipped or self.ok or self.kind == WARN

    @property
    def warning(self):
        """A warning line that is not met: shown, not blocking."""
        return self.kind == WARN and not self.skipped and not self.ok


# ---------------------------------------------------------------------------
# Reading files without FreeCAD.


def _norm(key):
    return re.sub(r"[\s_\-/]+", "", key or "").lower()


def title_block_fields(path) -> dict:
    """The EditableTexts of the first TechDraw template in an .FCStd, or {}."""
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read("Document.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    for prop in root.iter("Property"):
        if prop.get("name") != "EditableTexts":
            continue
        mapping = prop.find("Map")
        if mapping is None:
            continue
        return {item.get("key", ""): item.get("value", "") for item in mapping.iter("Item")}
    return {}


def title_block_part_number(fields: dict):
    """(field name, value) of the field holding the P/N, or (None, None)."""
    by_norm = {_norm(k): k for k in fields}
    for name in PN_FIELDS:
        key = by_norm.get(name)
        if key is not None:
            return key, (fields[key] or "").strip()
    return None, None


def shows_part_number(value, part_numbers) -> bool:
    """A title-block value shows the P/N as is or as ``P/N-REV``."""
    value = (value or "").strip()
    for pn in part_numbers:
        if not pn:
            continue
        if value == pn:
            return True
        if value.startswith(pn + "-"):
            try:
                pdm.validate_revision(value[len(pn) + 1:])
                return True
            except ValueError:
                pass
    return False


# ---------------------------------------------------------------------------
# The lines.


def _vault_file(vault, pn):
    version = vault.latest_version(pn)
    if version is None:
        return None
    path = os.path.join(vault.root, object_name(version.sha256, version.filename))
    return path if os.path.exists(path) else None


def _file_problem(ws, pn):
    """Why ``pn``'s file is not cleanly checked in, or "" when it is."""
    vault = ws.vault
    if vault.latest_version(pn) is None:
        return "nothing checked in yet"
    lock = vault.locks.get(pn)
    if lock == vault.actor:
        return "checked out to you; check it in first"
    if lock:
        return f"checked out by {lock}"
    st = ws.status(pn)
    if st.local and st.base and st.local != st.base:
        return "the work copy has changes that are not in the vault"
    if _vault_file(vault, pn) is None:
        return "the checked-in file has not synced to this Mac yet"
    return ""


def _checked_in(ws, pn):
    line = Line(CHECKED_IN, TEXTS[CHECKED_IN])
    problem = _file_problem(ws, pn)
    if problem:
        line.detail = problem
        return line
    version = ws.vault.latest_version(pn)
    line.ok = True
    comment = (version.comment or "").strip().splitlines()[0] if (version.comment or "").strip() else ""
    if len(comment) > 60:
        comment = comment[:59] + "\u2026"
    line.detail = f"version {version.sha256[:7]}" + (f": {comment}" if comment else "")
    return line


def _drawing(ws, pn, no_drawing=False):
    vault = ws.vault
    item = vault.items[pn]
    line = Line(DRAWING, TEXTS[DRAWING])
    if item.item_type == "cots":
        line.skipped, line.detail = True, "COTS item: no drawing"
        return line
    if item.item_type == "assembly" and no_drawing:
        line.skipped, line.detail = True, "marked as having no drawing"
        return line
    if item.item_type == "drawing":
        drawings, accepted = [pn], (pn, item.drawing_of)
        line.text = "Title block shows this P/N"
    else:
        drawings = [d.part_number for d in vault.drawings_of(pn)]
        accepted = (pn,)
        if not drawings:
            line.detail = "no drawing of this P/N in the vault"
            return line
    problems, shown = [], []
    for dwg in drawings:
        problem = "" if dwg == pn else _file_problem(ws, dwg)
        if problem:
            problems.append(f"{dwg}: {problem}")
            continue
        path = _vault_file(vault, dwg)
        fields = title_block_fields(path) if path else {}
        if not fields:
            problems.append(f"{dwg}: no TechDraw title block found")
            continue
        key, value = title_block_part_number(fields)
        if key is None:
            shown.append(f"{dwg}: the title block has no P/N field; check it by eye")
        elif not shows_part_number(value, accepted + ((dwg,) if dwg != pn else ())):
            problems.append(f"{dwg}: the title block shows “{value or 'nothing'}”")
        else:
            shown.append(f"{dwg}: title block shows {value}")
    line.ok = not problems
    line.detail = "; ".join(problems or shown)
    return line


def _file_links(vault, pn) -> list:
    """The part numbers (or file names) the latest checked-in file of pn links to."""
    found = []
    path = _vault_file(vault, pn)
    for name in pw.linked_files(path) if path else ():
        target = os.path.splitext(os.path.basename(name))[0]
        if target != pn and target not in found:
            found.append(target)
    return found


def components_of(ws, pn) -> list:
    """What an assembly uses, as part numbers (or names when a line is not a vault item): the BOM
    lines of its latest revision (PDM-06, read from the assembly at check-in), and the files its
    checked-in version links to only when no BOM has been recorded yet (raw links over-report)."""
    vault = ws.vault
    rev = vault.items[pn].latest
    if rev.bom is None:
        return _file_links(vault, pn)
    found = []
    for name in [line.child for line in rev.bom] + [gap["name"] for gap in rev.bom_missing]:
        if name != pn and name not in found:
            found.append(name)
    return found


def where_used(ws, pn) -> list:
    """What uses ``pn``, as far as the vault can tell (PDM-06). Rows {"part_number", "revision",
    "state", "released", "via", "quantity", "path", "exact"}:

    * "BOM": an assembly revision whose resolved BOM lines hold pn, directly or through a
      sub-assembly (exact; "path" is [P/N-REV, ...] down to pn, "quantity" the total);
    * "BOM, may pick up": a live revision that holds pn only when it floats to the newest
      released child (exact False);
    * "file link": an item whose latest revision has no BOM yet but whose checked-in file links
      to pn, or to an item that uses it (quantity unknown, 0).

    "released" users (a current released revision; a file-link user with a released revision)
    block retiring pn (Retire...). Released users first."""
    vault = ws.vault
    rows, seen = [], set()
    for use in vault.where_used_revisions(pn):
        item = vault.items[use.part_number]
        rows.append({"part_number": use.part_number, "revision": use.revision, "state": use.state,
                     "released": use.exact and use.current and use.state == pdm.RELEASED and not item.obsolete,
                     "via": "BOM" if use.exact else "BOM, may pick up", "quantity": use.quantity,
                     "path": [pdm.display_part_number(p, r) for p, r in use.path], "exact": use.exact})
        if use.exact:
            seen.add(use.part_number)
    users_of = {}  # P/N -> [user], one level of file links, from items without a BOM
    for other, item in vault.items.items():
        if item.latest.bom is not None:
            continue
        for target in _file_links(vault, other):
            users_of.setdefault(target, []).append(other)
    found, stack = [], [pn] + sorted(seen)
    while stack:
        current = stack.pop()
        for user in users_of.get(current, ()):
            if user != pn and user not in seen:
                seen.add(user)
                found.append(user)
                stack.append(user)
    for user in found:
        item = vault.items[user]
        rows.append({"part_number": user, "revision": item.latest.code, "state": item.state,
                     "released": item.released is not None and not item.obsolete, "via": "file link",
                     "quantity": 0, "path": [pdm.display_part_number(user, item.latest.code)], "exact": True})
    rows.sort(key=lambda r: (not r["released"], not r["exact"], r["part_number"]))
    return rows


def retire_blockers(ws, pn, rows=None) -> list:
    """Released items that still use ``pn`` stop it being retired, as sentences ([] when it can go).
    ``rows``: where_used(ws, pn) when already read."""
    rows = where_used(ws, pn) if rows is None else rows
    return [f"{r['part_number']} rev {r['revision']} (released) still uses it ({r['via']}); "
            "change that item through an ECR first" for r in rows if r["released"]]


def lock_released(ws, pns) -> list:
    """Make the work copies of released (or obsolete) items read-only on disk, unless
    checked out to you; returns the paths changed. Released means read-only (plan)."""
    changed = []
    for pn in pns:
        item = ws.vault.items.get(pn)
        path = ws.path(pn)
        if item is None or item.state not in (pdm.RELEASED, pdm.OBSOLETE) or not os.path.exists(path):
            continue
        if ws.vault.locks.get(pn) == ws.vault.actor:
            continue
        if os.access(path, os.W_OK):
            pw._set_writable(path, False)
            changed.append(path)
    return changed


def _components(ws, pn, batch=()):
    vault = ws.vault
    item = vault.items[pn]
    line = Line(COMPONENTS, TEXTS[COMPONENTS])
    if item.item_type != "assembly":
        line.skipped, line.detail = True, "not an assembly"
        return line
    names = components_of(ws, pn)
    for name in names:
        child = vault.items.get(name)
        if child is None:
            line.entries.append([name, "not in the vault"])
        elif name in batch:
            continue  # releases together with this one
        elif child.released is None:
            line.entries.append([name, f"{child.state} {child.latest.code}"])
    line.ok = not line.entries
    if not names:
        line.detail = "no components found"
    elif line.ok:
        line.detail = f"{len(names)} component{'s' if len(names) != 1 else ''}, all released"
    else:
        line.detail = f"{len(line.entries)} of {len(names)} not released"
    return line


def _drawing_current(ws, pn, drawing_line):
    """Line 6 (PDM-09): every drawing of ``pn`` (or ``pn`` itself, for a drawing) was made from the
    model as it is now; "metadata only" counts as current. A drawing from before PDM-09 is judged by
    time and says so. Never blocks (WARN)."""
    from bruceworx import product_tree as pt  # noqa: PLC0415

    vault = ws.vault
    item = vault.items[pn]
    line = Line(DRAWING_CURRENT, TEXTS[DRAWING_CURRENT], kind=WARN)
    if drawing_line.skipped:
        line.skipped, line.detail = True, drawing_line.detail
        return line
    drawings = [pn] if item.item_type == "drawing" else [d.part_number for d in vault.drawings_of(pn)
                                                         if not d.obsolete]
    if not drawings:
        line.skipped, line.detail = True, "no drawing"
        return line
    from bruceworx import vault_trees  # noqa: PLC0415

    bom = vault_trees.structure(vault)["bom"]  # recorded BOMs, else the files' links (an assembly drawing's closure)
    found = {d: pt.drawing_state(vault, d, bom) for d in drawings}
    bad = {d: f for d, f in found.items() if f["state"] not in (pt.CURRENT, pt.METADATA_ONLY)}
    line.ok = not bad
    texts = [f"{d}: {pt.state_text(f)}" for d, f in (bad or found).items()]
    line.detail = "; ".join(texts)
    if not bad and any(f["basis"] == "time" for f in found.values()):
        line.detail += " (time comparison: the drawing was checked in before PDM-09 recorded what it was made from)"
    return line


def _interference(ws, pn):
    """Line 7 (ASM-06): the assembly's parts do not overlap, by bruceworx_interference.check_file on
    the work copy (the vault's stored file when there is none). Marked fits are not clashes. Only
    inside Vector Labs (the check needs its geometry kernel); the CLI skips it. Never blocks (WARN):
    the plan left blocking to a later answer."""
    item = ws.vault.items[pn]
    line = Line(INTERFERENCE, TEXTS[INTERFERENCE], kind=WARN)
    if item.item_type != "assembly":
        line.skipped, line.detail = True, "not an assembly"
        return line
    try:
        import bruceworx_interference as itf  # noqa: PLC0415 -- Vector Labs' module, FreeCAD only
    except ImportError:
        line.skipped, line.detail = True, "checked in Vector Labs, not here"
        return line
    path = ws.path(pn)
    if not os.path.exists(path):
        path = _vault_file(ws.vault, pn)
    if path is None:
        line.skipped, line.detail = True, "no file to check"
        return line
    try:
        summary = itf.check_file(path)
    except Exception as exc:  # noqa: BLE001 -- a file that does not open: say so, never block
        line.detail = f"the check did not run: {exc}"
        return line
    counts = summary.get("counts", {})
    clashes = counts.get("clash", 0)
    line.ok = clashes == 0 and not summary.get("errors")
    parts = [f"{clashes} clash{'es' if clashes != 1 else ''}"] if clashes else []
    if counts.get("fit"):
        parts.append(f"{counts['fit']} marked fit{'s' if counts['fit'] != 1 else ''}")
    if summary.get("errors"):
        parts.append("; ".join(str(e) for e in summary["errors"]))
    worst = [r for r in summary.get("results", []) if r.get("kind") == "clash"][:3]
    if worst:
        parts.append("worst: " + ", ".join(f"{r.get('pair', '')} {r.get('volume', 0):.2f} mm³".strip() for r in worst))
    line.detail = ", ".join(parts) if parts else "no overlap"
    return line


def _reviewed(drawing_line):
    line = Line(REVIEWED, TEXTS[REVIEWED], kind=MANUAL)
    if drawing_line.skipped:
        line.skipped, line.detail = True, "no drawing"
    return line


def check_item(ws, pn, batch=(), no_drawing=False) -> list:
    """Lines 1-4, the PDM-09 drawing-current and ASM-06 interference warning lines for ``pn`` (the review tick unticked). ``batch``: the part numbers releasing
    together (an ECO's items). ``ws`` is a ``pdm_workspace.Workspace``."""
    drawing = _drawing(ws, pn, no_drawing)
    return [_checked_in(ws, pn), drawing, _drawing_current(ws, pn, drawing), _components(ws, pn, batch),
            _interference(ws, pn), _reviewed(drawing)]


def apply(lines, reviewed=False, waiver="") -> list:
    """Your part of the checklist: the review tick, and the reason for releasing an
    assembly whose component list is not all released."""
    for line in lines:
        if line.key == REVIEWED and not line.skipped:
            line.ok = bool(reviewed)
        elif line.key == COMPONENTS and line.entries:
            line.waiver = (waiver or "").strip()
            line.ok = bool(line.waiver)
    return lines


def blockers(lines, note=None) -> list:
    """What stops the release, as short sentences ([] when it can go).
    ``note`` None leaves the note out (an ECO checks its one note once)."""
    out = []
    for line in lines:
        if line.passed:
            continue
        if line.key == COMPONENTS and line.entries:
            out.append("Give the reason for releasing with unreleased components")
        elif line.kind == MANUAL:
            out.append("Tick “" + line.text + "”")
        else:
            out.append(line.text + ": " + (line.detail or "not met"))
    if note is not None and not (note or "").strip():
        out.append("Write a release note")
    return out


def record(lines, no_drawing=False, version="") -> dict:
    """The checklist as recorded in the journal with the approval."""
    out = {}
    for line in lines:
        entry = {"ok": bool(line.ok)}
        if line.skipped:
            entry = {"skipped": True}
        if line.detail:
            entry["detail"] = line.detail
        if line.entries:
            entry["list"] = [list(e) for e in line.entries]
        if line.waiver:
            entry["reason"] = line.waiver
        if line.kind == MANUAL:
            entry["by_hand"] = True
        if line.warning:
            entry["warning"] = True  # shown at release, not a blocker (PDM-09 answer 6)
        out[line.key] = entry
    if no_drawing:
        out["no_drawing"] = True
    if version:
        out["version"] = version
    return out


def as_dicts(lines) -> list:
    return [asdict(line) for line in lines]
