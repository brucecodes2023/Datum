# SPDX-License-Identifier: LGPL-2.1-or-later
"""Review, release and change control in FreeCAD (BW-PDM-05, plan approved 23 Oct 2026).

Built on the vault client (bruceworx_vaultclient.py) and the hooks add-on's engine
(bruceworx/pdm.py, pdm_folder.py and release_check.py). With one person in the vault you
approve your own work, with a checklist recorded in the journal with the approval.

* Approve...: a task panel with the release checklist (checked in; the drawing and its
  title block; an assembly's components; your "drawing reviewed" tick) and a required
  release note. OK releases rev A.
* Reject...: a reason is required. The rejected number is superseded; work goes on at the next.
* Raise ECR...: the problem (required), the affected released items (from the selection, the
  Browser or the Vault panel) and attachments, copied into the vault. ECR-0001, ECR-0002, ...
* Decide ECR...: approve or reject, with a reason.
* Open ECO...: from approved ECRs; shows the affected items and their drawings with their
  next letter and offers to check them all out.
* Release ECO...: the checklist per item and one release note; everything releases together.
* Retire...: a released item becomes Obsolete, with a reason (required). What uses it is shown;
  a released user blocks it, one in work is confirmed. Its released drawings can retire with it.
* The Vault panel's "Changes" filter (ECRs and ECOs with their state), the Start page's
  "ECOs open", and a released item's check-out explained, with Raise ECR... offered.

The pure helpers at the top are tested in TestBruceWorxChanges.py.
"""

from __future__ import annotations

import datetime as _dt
import os

from bruceworx_slots import slot

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
CHANGE_KIND = "change"  # item_type of a change row in the Vault panel
ECR, ECO = "ECR", "ECO"

# Change states -> the Vault panel chip (text, colour role).
CHANGE_CHIPS = {
    "Open": ("OPEN", "warn"),
    "Approved": ("APPROVED", "accent"),
    "Rejected": ("REJECTED", "dim"),
    "Released": ("RELEASED", "ok"),
    "Cancelled": ("CANCELLED", "dim"),
}

# Checklist marks: line state -> glyph.
MARKS = {"ok": "✓", "fail": "✕", "skip": "–", "todo": "○", "warn": "!"}  # warn: PDM-09, shown, never blocks

# Icons drawn like the vault client's (24 x 24 view box; {c} is the colour).
_SVG = {
    "ecr": '<path d="M6 3h8l4 4v14H6z" fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>'
           '<path d="M12 9v5" stroke="{c}" stroke-width="2.1" stroke-linecap="round"/>'
           '<circle cx="12" cy="17.3" r="1.25" fill="{c}"/>',
    "eco": '<rect x="3.5" y="5" width="13" height="15" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/>'
           '<path d="M7.5 3h11a2 2 0 0 1 2 2v13" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>'
           '<path d="M7 11.3l2.3 2.3 4-4.2" fill="none" stroke="{c}" stroke-width="1.9" stroke-linecap="round" '
           'stroke-linejoin="round"/>',
    "reject": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="1.9"/>'
              '<path d="M8.7 8.7l6.6 6.6M15.3 8.7l-6.6 6.6" stroke="{c}" stroke-width="1.9" stroke-linecap="round"/>',
    "decide": '<path d="M12 3.5v17M5 7.5h14" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>'
              '<path d="M5 7.5l-2.5 6a2.6 2.6 0 0 0 5 0zM19 7.5l-2.5 6a2.6 2.6 0 0 0 5 0z" fill="none" '
              'stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>',
    "release": '<path d="M12 2.2l8.3 3.1v6.1c0 5.1-3.5 8.8-8.3 10.4-4.8-1.6-8.3-5.3-8.3-10.4V5.3z" fill="{c}"/>'
               '<path d="M8.2 12.1l2.7 2.7 5-5.1" fill="none" stroke="#fff" stroke-width="2.1" '
               'stroke-linecap="round" stroke-linejoin="round"/>',
    "retire": '<rect x="3" y="4" width="18" height="5" rx="1.2" fill="none" stroke="{c}" stroke-width="1.8"/>'
              '<path d="M5 9v10a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V9" fill="none" stroke="{c}" '
              'stroke-width="1.8" stroke-linejoin="round"/>'
              '<path d="M10 13.2h4" stroke="{c}" stroke-width="2" stroke-linecap="round"/>',
}
_COLOURS = {"accent": "#0A84FF", "error": "#E0383E", "warn": "#C27C00", "ok": "#1F9D55", "secondary": "#8E8E93"}


# ----- pure helpers (unit tested) ---------------------------------------------------------


def is_change_number(text):
    """ECR-0001 / ECO-0001 (any number of digits), as the vault numbers them."""
    head, sep, tail = str(text or "").partition("-")
    return bool(sep) and head in (ECR, ECO) and tail.isdigit()


def line_state(line):
    """"ok", "fail", "skip" or "todo" (your tick, not given yet) for a release_check.Line."""
    if line.skipped:
        return "skip"
    if line.ok:
        return "ok"
    if line.kind == "warn":
        return "warn"
    return "todo" if line.kind == "manual" else "fail"


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _who(actor, me):
    if not actor:
        return ""
    return "you" if actor == me else actor.split("@")[0]


def _when(text, now=None):
    try:
        import bruceworx_vaultclient as vc

        return vc.relative_time(text, now)
    except Exception:
        return ""


def change_status(change, me="", now=None):
    """(text, colour role) for the third line of a change row in the Vault panel."""
    kind, state = change["kind"], change["state"]
    items = plural(len(change.get("items", [])), "item")
    if kind == ECR:
        if state == "Open":
            who = _who(change.get("by"), me)
            when = _when(change.get("at"), now)
            return f"To decide · {items}" + (f" · raised by {who}" if who else "") + (f", {when}" if when else ""), \
                "warn"
        if state == "Approved":
            ecos = change.get("ecos") or []
            return (f"In {', '.join(ecos)} · {items}", "secondary") if ecos else (f"Ready for an ECO · {items}", "accent")
        reason = (change.get("reason") or "").strip().splitlines()
        return (f"Rejected: {reason[0]}" if reason else f"Rejected · {items}"), "secondary"
    if state == "Open":
        pending = change.get("pending") or []
        total = len(change.get("items", []))
        ready = total - len(pending)
        if not pending:
            return f"All {total} in review · ready to release", "accent"
        return f"{ready} of {total} in review · work on {', '.join(pending[:2])}" + \
            ("…" if len(pending) > 2 else ""), "warn"
    when = _when(change.get("released_at"), now)
    return f"Released {when} · {items}" if when else f"Released · {items}", "secondary"


def change_needs_you(change):
    """An ECR to decide, an approved ECR with no ECO yet, or an open ECO."""
    if change["kind"] == ECR:
        return change["state"] == "Open" or (change["state"] == "Approved" and not change.get("ecos"))
    return change["state"] == "Open"


def change_row(change, me="", now=None):
    """A Vault panel row (the dict VaultPanel draws) for a FolderVault.changes() entry."""
    text, role = change_status(change, me, now)
    stamps = [change.get(k) or "" for k in ("at", "decided_at", "released_at")]
    return {
        "part_number": change["number"], "display": change["number"], "revision": "",
        "description": change.get("title") or "", "state": change["state"], "code": CHANGE_KIND,
        "item_type": CHANGE_KIND, "status_text": text, "status_role": role, "sha": "", "object": "",
        "at": max(stamps), "active": False, "kind": change["kind"],
        "chip": CHANGE_CHIPS.get(change["state"], (change["state"].upper(), "secondary")),
        "pending": change_needs_you(change), "items": list(change.get("items", [])),
    }


def change_rows(vault, me="", now=None):
    """{number: row} for every ECR and ECO."""
    return {c["number"]: change_row(c, me, now) for c in vault.changes()}


def my_work_rows(vault):
    """Start page "ECOs open": rows like bruceworx_start.work_row, one per open ECO."""
    rows = []
    for change in vault.changes():
        if change["kind"] != ECO or change["state"] != "Open":
            continue
        text, _role = change_status(change)
        rows.append({"pn": change["number"], "display": change["number"], "description": change.get("title") or "",
                     "status": text, "code": CHANGE_KIND, "path": "", "kind": "other"})
    rows.sort(key=lambda r: r["pn"])
    return rows


def eco_plan(vault, ecr_numbers):
    """What Open ECO would do, without doing it: ([row], [problem]).

    A row is {part_number, item_type, description, current, next, reason}; the same walk as
    pdm.Vault.open_eco (the ECRs' items, then each one's drawings, once each)."""
    from bruceworx import pdm

    rows, problems, seen = [], [], set()
    for number in ecr_numbers:
        ecr = vault.ecrs.get(number)
        if ecr is None:
            problems.append(f"{number} is not in the vault")
            continue
        if ecr.status != "Approved":
            problems.append(f"{number} is {ecr.status}, not Approved")
        for pn in ecr.items:
            drawings = [(d.part_number, f"drawing of {pn}") for d in vault.drawings_of(pn) if not d.obsolete]
            for related, why in [(pn, number)] + drawings:
                if related in seen:
                    continue
                seen.add(related)
                item = vault.items[related]
                base = item.released.code if item.released else None
                rows.append({"part_number": related, "item_type": item.item_type, "description": item.description,
                             "current": base or "", "next": pdm.next_release(base) if base else pdm.first_release(),
                             "reason": why})
                if item.obsolete:
                    problems.append(f"{related} is obsolete")
                elif item.latest.state in (pdm.WIP, pdm.IN_REVIEW):
                    problems.append(f"{related} already has work in progress ({item.latest.code}, {item.latest.state})")
    return rows, problems


def retire_refusal(item):
    """Why an item cannot be retired, in words, or None when it can (released, no ECO in work)."""
    latest = item.latest
    if item.obsolete:
        return f"{item.part_number} is already obsolete."
    if item.released is None:
        return (f"{item.part_number} is not released ({latest.code}, {latest.state}). Only released items are "
                "retired; work that never released can simply stop.")
    if latest.eco is not None and latest.state != "Released":
        return f"{item.part_number} rev {latest.code} is in work under {latest.eco}. Release {latest.eco} first."
    return None


def retire_plan(work, pn):
    """What Retire... would do, without doing it. ``work`` is the session's pdm_workspace.Workspace.

    {"users": where-used rows (release_check.where_used), "blockers": [sentence], "drawings": the
    released drawings that can retire with it, "kept": [(drawing, state)] that cannot,
    "confirm": users still in work (you confirm you know)}."""
    from bruceworx import release_check as rc

    vault = work.vault
    item = vault.items[pn]
    users = rc.where_used(work, pn)
    refusal = retire_refusal(item)
    blockers = ([refusal] if refusal else []) + rc.retire_blockers(work, pn, users)
    drawings, kept = [], []
    for d in vault.drawings_of(pn):
        if d.obsolete:
            continue
        if d.state == "Released":
            drawings.append(d.part_number)
        else:
            kept.append((d.part_number, f"{d.state} {d.latest.code}"))
    confirm = [u for u in users if not u["released"] and u["state"] != "Obsolete"]
    return {"users": users, "blockers": blockers, "drawings": drawings, "kept": kept, "confirm": confirm}


def approvable_ecrs(vault):
    """Approved ECRs that no ECO has taken yet, oldest first."""
    taken = {n for eco in vault.ecos.values() for n in eco.ecrs}
    return [n for n in sorted(vault.ecrs) if vault.ecrs[n].status == "Approved" and n not in taken]


def open_ecos(vault, pn=None):
    """Open ECO numbers (that include pn, when given)."""
    return [n for n in sorted(vault.ecos) if vault.ecos[n].status == "Open" and (pn is None or pn in vault.ecos[n].items)]


def released_items(vault):
    """Items with a released revision that are not being changed already, by part number."""
    out = []
    for pn in sorted(vault.items):
        item = vault.items[pn]
        if item.released is not None and not item.obsolete:
            out.append(pn)
    return out


def refused_checkout_text(display, state, ecos=()):
    """Why a check-out is refused, in words (None when the state allows it)."""
    if state == "Released":
        if ecos:
            return (f"{display} is released and {', '.join(ecos)} is already open for it.\n\n"
                    "Its next revision is checked out from the ECO's items once the ECO opens it.")
        return (f"{display} is released. A released revision never changes, so it cannot be checked out.\n\n"
                "To change it, raise an ECR (what is wrong and why). Once the ECR is approved, an ECO opens "
                "the next revision for you to work on.")
    if state == "In Review":
        return f"{display} is in review. Approve or reject it first; a rejection opens the next number for work."
    if state == "Obsolete":
        return f"{display} is obsolete and no longer changes."
    return None


def svg_icon(kind, colour):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
            + _SVG[kind].replace("{c}", colour) + "</svg>")


# ----- FreeCAD side ------------------------------------------------------------------------

_state = {"installed": False, "panel": None}
_classes_cache = None


def _vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def _session():
    return _vc().session()


def _window():
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _message(title, text, kind="information"):
    _vc()._message(title, text, kind)


def _console(text, level="log"):
    _vc()._console(text, level)


def _display(s, pn):
    item = s.vault.items.get(pn)
    if item is None:
        return pn
    from bruceworx import pdm

    return pdm.display_part_number(pn, item.latest.code)


def _selected_pns(s):
    """The items the selection stands for (Vault panel row, Browser selection, active document)."""
    import FreeCAD
    import FreeCADGui

    vc = _vc()
    target = vc._state.get("target")
    if target:
        return [target]
    out = []
    try:
        for obj in FreeCADGui.Selection.getSelection():
            pn = vc.pn_for_object(obj, s)
            if pn and pn not in out:
                out.append(pn)
    except Exception:
        pass
    if not out:
        pn = vc.pn_for_document(FreeCAD.ActiveDocument, s)
        if pn:
            out.append(pn)
    return out


def _show(title, make):
    """Open a task panel made by make(); refuse politely while another one is open."""
    import FreeCADGui

    if FreeCADGui.Control.activeDialog():
        _message(title, "Another command is open in the task panel. Close it (OK or Cancel) first.")
        return False
    try:
        panel = make()
    except Exception as exc:
        _message(title, f"{title} could not open: {exc}", "warning")
        return False
    _state["panel"] = panel
    FreeCADGui.Control.showDialog(panel)
    from PySide import QtCore

    for ms in (150, 600):
        QtCore.QTimer.singleShot(ms, _settle_title_bar)
    return True


def _settle_title_bar():
    """Lay the task panel's title bar out again. Its layout can keep a stale 8 px width after
    documents open and close, and the overlay then elides the panel's title to nothing."""
    from PySide import QtWidgets

    try:
        dock = _window().findChild(QtWidgets.QDockWidget, "Tasks")
        bar = dock.titleBarWidget() if dock is not None else None
        if bar is not None and bar.layout() is not None and bar.layout().geometry().width() < bar.width() - 4:
            bar.layout().setGeometry(bar.rect())
            bar.update()
    except Exception:
        pass


def _target(pn=None):
    s = _session()
    if s is None:
        return None, ""
    return s, pn or _vc().current_target()


# ----- operations ------------------------------------------------------------------------


def approve(pn=None):
    s, pn = _target(pn)
    if s is None or not pn:
        return False
    return _show("Approve", lambda: _classes()["ApprovePanel"](s, pn))


def reject(pn=None):
    s, pn = _target(pn)
    if s is None or not pn:
        return False
    return _show("Reject", lambda: _classes()["RejectPanel"](s, pn))


def raise_ecr(pns=None):
    s = _session()
    if s is None:
        return False
    if isinstance(pns, str):
        pns = [pns]
    pns = [p for p in (pns or _selected_pns(s)) if p in s.vault.items]
    return _show("Raise ECR", lambda: _classes()["RaiseEcrPanel"](s, pns))


def retire(pn=None):
    s, pn = _target(pn)
    if s is None or not pn:
        return False
    item = s.vault.items.get(pn)
    if item is None:
        return False
    refusal = retire_refusal(item)
    if refusal:
        _message("Retire", refusal)
        return False
    return _show("Retire", lambda: _classes()["RetirePanel"](s, pn))


def decide_ecr(number=None):
    from PySide import QtWidgets

    s = _session()
    if s is None:
        return False
    if not number:
        open_ = [n for n in sorted(s.vault.ecrs) if s.vault.ecrs[n].status == "Open"]
        if not open_:
            _message("Decide ECR", "No ECR is waiting for a decision.")
            return False
        if len(open_) == 1:
            number = open_[0]
        else:
            labels = [f"{n} — {s.vault.ecrs[n].problem.splitlines()[0][:70]}" for n in open_]
            choice, ok = QtWidgets.QInputDialog.getItem(_window(), "Decide ECR", "Decide:", labels, 0, False)
            if not ok:
                return False
            number = open_[labels.index(choice)]
    return _show("Decide ECR", lambda: _classes()["EcrPanel"](s, number))


def open_eco(ecrs=None):
    s = _session()
    if s is None:
        return False
    ready = approvable_ecrs(s.vault)
    if not ready:
        _message("Open ECO", "No approved ECR is waiting for an ECO. Raise an ECR and approve it first.")
        return False
    return _show("Open ECO", lambda: _classes()["OpenEcoPanel"](s, ecrs or ready))


def release_eco(number=None):
    from PySide import QtWidgets

    s = _session()
    if s is None:
        return False
    if number and not is_change_number(number):  # a part number: its open ECO
        found = open_ecos(s.vault, number)
        number = found[0] if found else None
    if not number:
        found = open_ecos(s.vault)
        if not found:
            _message("Release ECO", "No ECO is open.")
            return False
        if len(found) == 1:
            number = found[0]
        else:
            choice, ok = QtWidgets.QInputDialog.getItem(_window(), "Release ECO", "Release:", found, 0, False)
            if not ok:
                return False
            number = choice
    return _show("Release ECO", lambda: _classes()["ReleaseEcoPanel"](s, number))


def show_change(number):
    """Open a change: an ECR in its panel (decide, or read), an ECO to release or to read."""
    s = _session()
    if s is None or not is_change_number(number):
        return False
    if number.startswith(ECR) and number in s.vault.ecrs:
        return _show("ECR", lambda: _classes()["EcrPanel"](s, number))
    eco = s.vault.ecos.get(number)
    if eco is None:
        return False
    if eco.status == "Open":
        return release_eco(number)
    lines = [f"{number} · {eco.status}", "", "ECRs: " + ", ".join(eco.ecrs), "Items: " + ", ".join(
        _display(s, pn) for pn in eco.items)]
    if eco.note:
        lines += ["", eco.note]
    _message(number, "\n".join(lines))
    return True


def explain_refused_checkout(s, pn, saving=False):
    """Before a check-out: a released (or in-review) item gets a message, and Raise ECR... is
    offered for a released one, instead of the engine's error. True when it was refused.
    ``saving``: asked by the save prompt; the message then says the save fails."""
    from PySide import QtWidgets

    item = s.vault.items.get(pn)
    if item is None:
        return False
    text = refused_checkout_text(_display(s, pn), item.state, open_ecos(s.vault, pn))
    if text is None:
        return False
    box = QtWidgets.QMessageBox(_window())
    box.setIcon(QtWidgets.QMessageBox.Information)
    box.setWindowTitle("Save" if saving else "Check Out")
    head, _sep, rest = text.partition("\n\n")
    box.setText(head)
    if saving:
        rest = (rest + "\n\n" if rest else "") + "The file is read-only, so this save fails."
    if rest:
        box.setInformativeText(rest)
    ecr = None
    if item.state == "Released" and not open_ecos(s.vault, pn):
        ecr = box.addButton("Raise ECR…", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(ecr)
    else:
        box.addButton(QtWidgets.QMessageBox.Ok)
    box.exec()
    if ecr is not None and box.clickedButton() is ecr:
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, lambda: raise_ecr([pn]))
    return True


def checkout_all(s, pns):
    """Check out every item that has a file; [(pn, error)] for those that did not go."""
    failed = []
    for pn in pns:
        try:
            s.work.checkout(pn)
        except Exception as exc:
            failed.append((pn, str(exc)))
    _vc().changed()
    _vc()._update_tabs()
    return failed


def open_attachment(s, record):
    from PySide import QtCore, QtGui

    folder = os.path.join(s.work.root, ".bw", "attachments", record["sha256"][:12])
    try:
        path = s.vault.fetch_attachment(record, folder)
    except Exception as exc:
        _message("Attachment", str(exc), "warning")
        return False
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    return True


# ----- commands ----------------------------------------------------------------------------


def _item(s, pn):
    return s.vault.items.get(pn) if (s is not None and pn) else None


def _active_reject(s, pn, st):
    item = _item(s, pn)
    return item is not None and item.state == "In Review"


def _active_raise(s, pn, st):
    item = _item(s, pn)
    return item is not None and item.released is not None and not item.obsolete


def _active_release_item(s, pn, st):
    return bool(_item(s, pn)) and bool(open_ecos(s.vault, pn))


def _active_retire(s, pn, st):
    item = _item(s, pn)
    return item is not None and retire_refusal(item) is None


# Item commands: the vault client's COMMANDS format, so they join its right-click menus.
ITEM_COMMANDS = (
    ("BruceWorx_VaultReject", "Reject…", "Send the revision in review back to work, with a reason. The "
     "number is kept as rejected; work goes on at the next one.", "reject", "error",
     lambda pn=None: reject(pn), _active_reject, ""),
    ("BruceWorx_VaultRaiseEcr", "Raise ECR…", "Ask for a change to released items: what is wrong and why, "
     "with attachments. Numbered ECR-0001, ECR-0002, ... in this vault.", "ecr", "warn",
     lambda pn=None: raise_ecr([pn] if pn else None), _active_raise, ""),
    ("BruceWorx_VaultReleaseEco", "Release ECO…", "Release every item of the open ECO together, after the "
     "release checklist of each.", "release", "ok", lambda pn=None: release_eco(pn), _active_release_item, ""),
    ("BruceWorx_VaultRetire", "Retire…", "Make a released item obsolete, with a reason: it stays in the vault, "
     "read-only, and never changes again. Shows what still uses it first.", "retire", "secondary",
     lambda pn=None: retire(pn), _active_retire, ""),
)
# Vault-wide commands: the BruceWorx > Vault menu and Cmd-K.
VAULT_COMMANDS = (
    ("BruceWorx_VaultDecideEcr", "Decide ECR…", "Approve or reject an ECR, with a reason.", "decide", "accent",
     decide_ecr, lambda s: any(e.status == "Open" for e in s.vault.ecrs.values())),
    ("BruceWorx_VaultOpenEco", "Open ECO…", "Open an ECO from approved ECRs: their items and drawings get "
     "their next letter in work, ready to check out.", "eco", "accent", open_eco,
     lambda s: bool(approvable_ecrs(s.vault))),
)


def _icon_name(kind, colour):
    return f"BruceWorx_Vault_{kind}_{colour}"  # the vault client's naming, so its menus find them


class _ItemCommand:
    def __init__(self, entry):
        self.name, self.text, self.tip, self.icon, self.colour, self.fn, self.active, _accel = entry

    def GetResources(self):
        return {"Pixmap": _icon_name(self.icon, self.colour), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        try:
            s = _session()
            if s is None:
                return False
            if self.name == "BruceWorx_VaultRaiseEcr":
                return bool(released_items(s.vault))
            if self.name == "BruceWorx_VaultReleaseEco":
                return bool(open_ecos(s.vault))
            pn = _vc().current_target()
            return bool(pn) and bool(self.active(s, pn, None))
        except Exception:
            return False

    def Activated(self):
        try:
            self.fn()
        except Exception as exc:
            _message(self.text.rstrip("…"), str(exc), "warning")


class _VaultCommand:
    def __init__(self, entry):
        self.name, self.text, self.tip, self.icon, self.colour, self.fn, self.active = entry

    def GetResources(self):
        return {"Pixmap": _icon_name(self.icon, self.colour), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        try:
            s = _session()
            return s is not None and bool(self.active(s))
        except Exception:
            return False

    def Activated(self):
        try:
            self.fn()
        except Exception as exc:
            _message(self.text.rstrip("…"), str(exc), "warning")


def build_change_menu(parent, number):
    """The Vault panel's right-click menu on an ECR or ECO row."""
    from PySide import QtWidgets

    s = _session()
    menu = QtWidgets.QMenu(parent)
    if s is None:
        return menu
    head = menu.addAction(number)
    head.setEnabled(False)
    if number.startswith(ECR):
        ecr = s.vault.ecrs.get(number)
        act = menu.addAction("Decide…" if ecr and ecr.status == "Open" else "Show ECR")
        act.triggered.connect(lambda _c=False: show_change(number))
        act = menu.addAction("Open ECO…")
        act.setEnabled(bool(ecr) and number in approvable_ecrs(s.vault))
        act.triggered.connect(lambda _c=False: open_eco([number]))
    else:
        eco = s.vault.ecos.get(number)
        act = menu.addAction("Release ECO…")
        act.setEnabled(bool(eco) and eco.status == "Open")
        act.triggered.connect(lambda _c=False: release_eco(number))
        if eco is not None and eco.status == "Open":
            act = menu.addAction("Check Out All Items")
            act.triggered.connect(lambda _c=False: _checkout_eco(number))
    menu.addSeparator()
    items = (s.vault.ecrs.get(number) or s.vault.ecos.get(number))
    for pn in (items.items if items is not None else []):
        act = menu.addAction(f"Show {_display(s, pn)}")
        act.triggered.connect(lambda _c=False, p=pn: _vc().show_in_panel(p))
    return menu


def _checkout_eco(number):
    s = _session()
    eco = s.vault.ecos.get(number) if s is not None else None
    if eco is None:
        return
    todo = [pn for pn in eco.items if s.vault.items[pn].latest.state == "WIP" and not s.vault.locks.get(pn)]
    failed = checkout_all(s, todo)
    if failed:
        _message("Check Out", "Not checked out:\n" + "\n".join(f"{pn}: {why}" for pn, why in failed), "warning")


def thumbnail(row, size, dpr):
    """The Vault panel's picture for a change row: its icon, in the middle of a size x size square."""
    from PySide import QtCore, QtGui

    kind = "ecr" if row.get("kind") == ECR else "eco"
    role = {"warn": "warn", "accent": "accent", "ok": "ok"}.get(row.get("chip", ("", ""))[1], "secondary")
    icon = _svg_pixmap(kind, _COLOURS[role], max(12, size // 2), dpr)
    canvas = QtGui.QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(canvas)
    offset = (size - max(12, size // 2)) / 2.0
    painter.drawPixmap(QtCore.QPointF(offset, offset), icon)
    painter.end()
    return canvas


def _svg_pixmap(kind, colour, size, dpr):
    from PySide import QtCore, QtGui

    data = QtCore.QByteArray(svg_icon(kind, colour).encode("utf-8"))
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.ReadOnly)
    reader = QtGui.QImageReader(buffer, b"svg")
    px = int(round(size * dpr))
    reader.setScaledSize(QtCore.QSize(px, px))
    image = reader.read()
    buffer.close()
    pixmap = QtGui.QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


# ----- task panels -------------------------------------------------------------------------


def _classes():
    global _classes_cache
    if _classes_cache is None:
        _classes_cache = _make_classes()
    return _classes_cache


def _html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    from bruceworx import release_check as rc

    vc = _vc()

    def icon(kind, role):
        return QtGui.QIcon(_svg_pixmap(kind, _COLOURS[role], 16, 2.0))

    def box(title, kind=None, role="accent"):
        w = QtWidgets.QWidget()
        w.setWindowTitle(title)
        if kind is not None:
            w.setWindowIcon(icon(kind, role) if kind in _SVG else QtGui.QIcon(vc._svg_pixmap(kind, _COLOURS[role],
                                                                                            16, 2.0)))
        return w

    def hint(text=""):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        return lab

    def intro(text):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultIntro")
        lab.setWordWrap(True)
        lab.setTextFormat(QtCore.Qt.RichText)
        return lab

    def fixed_height(widget, height):
        """A fixed height that does not make its task box claim spare height (a scroll area's
        policy is Expanding, and the box would spread its lines apart)."""
        widget.setFixedHeight(height)
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return widget

    def text_box(placeholder, height=76, on_change=None):
        edit = QtWidgets.QPlainTextEdit()
        edit.setObjectName("bwVaultComment")
        edit.setPlaceholderText(placeholder)
        edit.setTabChangesFocus(True)
        fixed_height(edit, height)
        if on_change is not None:
            edit.textChanged.connect(on_change)
        return edit

    def mark(state):
        lab = QtWidgets.QLabel(MARKS[state])
        lab.setObjectName("bwCheckMark")
        lab.setProperty("state", state)
        lab.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        lab.setFixedWidth(18)
        return lab

    class Checklist(QtWidgets.QWidget):
        """The release checklist of one item: automatic lines, your tick, the component reason."""

        changed = QtCore.Signal()

        def __init__(self, s, pn, batch=(), parent=None):
            super().__init__(parent)
            self.setObjectName("bwChecklist")
            self.s, self.pn, self.batch = s, pn, tuple(batch)
            self.item = s.vault.items[pn]
            self.no_drawing = False
            self.reviewed = False
            self.waiver = ""
            self.lines = []
            self.grid = QtWidgets.QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.grid.setHorizontalSpacing(6)
            self.grid.setVerticalSpacing(2)
            self.grid.setColumnStretch(1, 1)
            self.check()

        def check(self):
            """Run the automatic lines again (after a check-in, say) and rebuild."""
            self.s.vault.refresh()
            self.s.invalidate()
            self.lines = rc.check_item(self.s.work, self.pn, self.batch, self.no_drawing)
            rc.apply(self.lines, self.reviewed, self.waiver)
            self.rebuild()
            self.changed.emit()

        def rebuild(self):
            while self.grid.count():
                w = self.grid.takeAt(0).widget()
                if w is not None:
                    w.hide()
                    w.deleteLater()
            row = 0
            for line in self.lines:
                state = line_state(line)
                if line.kind == "manual" and not line.skipped:
                    tick = QtWidgets.QCheckBox(line.text)
                    tick.setChecked(self.reviewed)
                    tick.toggled.connect(self.set_reviewed)
                    self.grid.addWidget(tick, row, 0, 1, 2)
                    row += 1
                    self.grid.addWidget(hint("Your tick: open the drawing next to the model and compare."), row, 1)
                    row += 1
                    continue
                self.grid.addWidget(mark(state), row, 0)
                text = QtWidgets.QLabel(line.text)
                text.setWordWrap(True)
                text.setObjectName("bwCheckText")
                text.setProperty("state", state)
                self.grid.addWidget(text, row, 1)
                row += 1
                if line.detail:
                    self.grid.addWidget(hint(line.detail), row, 1)
                    row += 1
                if line.entries:
                    listing = "\n".join(f"• {name} — {why}" for name, why in line.entries)
                    self.grid.addWidget(hint(listing), row, 1)
                    row += 1
                    reason = QtWidgets.QLineEdit(self.waiver)
                    reason.setPlaceholderText("Why release anyway (recorded)")
                    reason.textChanged.connect(self.set_waiver)
                    self.grid.addWidget(reason, row, 1)
                    row += 1
            if self.item.item_type == "assembly":
                none = QtWidgets.QCheckBox("This assembly has no drawing")
                none.setChecked(self.no_drawing)
                none.toggled.connect(self.set_no_drawing)
                self.grid.addWidget(none, row, 0, 1, 2)
                row += 1

        def set_reviewed(self, on):
            self.reviewed = bool(on)
            rc.apply(self.lines, self.reviewed, self.waiver)
            self.changed.emit()

        def set_waiver(self, text):
            self.waiver = text
            rc.apply(self.lines, self.reviewed, self.waiver)
            self.changed.emit()

        def set_no_drawing(self, on):
            self.no_drawing = bool(on)
            QtCore.QTimer.singleShot(0, self.check)

        def blockers(self):
            return rc.blockers(self.lines)

        def record(self):
            latest = self.s.vault.latest_version(self.pn)
            return rc.record(self.lines, self.no_drawing, latest.sha256 if latest else "")

    class Panel:
        """Task-panel plumbing shared by the change panels: form boxes, OK text and state."""

        ok_text = "OK"
        ready_text = ""  # shown in the status line when nothing blocks OK

        def __init__(self):
            self.buttons = None
            self.done = False
            self.form = []

        def ok_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Ok) if self.buttons is not None else None

        def modifyStandardButtons(self, buttons):
            self.buttons = buttons
            self.update_ok()

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True

        def problems(self):
            return []

        def update_ok(self):
            if self.done:
                return
            problems = self.problems()
            status = getattr(self, "status", None)
            if status is not None:
                # Always shown (a line that comes and goes makes the task box jump).
                status.setObjectName("bwVaultIssue")
                status.setText(self.ready_text if not problems else "To go on: " + "; ".join(
                    p[0].lower() + p[1:] if p else p for p in problems) + ".")
                status.setProperty("level", "warning" if problems else "")
                status.style().unpolish(status)
                status.style().polish(status)
            button = self.ok_button()
            if button is not None:
                button.setEnabled(not problems)
                button.setText(self.ok_text)

        def finish(self):
            _state["panel"] = None
            self.done = True
            return True

        def reject(self):
            return self.finish()

        def run(self, title, fn, *args, **kwargs):
            try:
                return True, fn(*args, **kwargs)
            except Exception as exc:
                _message(title, str(exc) or type(exc).__name__, "warning")
                return False, None
            finally:
                vc.changed()

    class ApprovePanel(Panel):
        """Approve: the release checklist and a release note; OK releases rev A."""

        def __init__(self, s, pn):
            super().__init__()
            from bruceworx import pdm

            self.s, self.pn = s, pn
            item = s.vault.items[pn]
            if item.state != "In Review":
                raise ValueError(f"{_display(s, pn)} is {item.state}, not in review. Submit it for review first.")
            self.to = pdm.first_release()
            self.ok_text = f"Release as {self.to}"
            self.ready_text = f"Ready: releasing as {self.to} records the checklist and the note."
            first = box("Approve", "released", "ok")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(
                f"Approving releases {_html(item.description or pn)} <b>{_html(_display(s, pn))}</b> as revision "
                f"<b>{self.to}</b>. A released revision never changes; later changes go through ECR → ECO."))
            checks = box("Release checklist")
            cl = QtWidgets.QVBoxLayout(checks)
            self.checklist = Checklist(s, pn)
            self.checklist.changed.connect(slot(self.update_ok))
            cl.addWidget(self.checklist)
            again = QtWidgets.QPushButton("Check Again")
            again.setToolTip("Run the automatic lines again, after a check-in for example")
            again.clicked.connect(self.checklist.check)
            row = QtWidgets.QHBoxLayout()
            row.addStretch(1)
            row.addWidget(again)
            cl.addLayout(row)
            notes = box("Release note")
            nl = QtWidgets.QVBoxLayout(notes)
            self.note = text_box("What this release is, for the record", 70, self.update_ok)
            nl.addWidget(self.note)
            self.status = hint()
            self.status.setObjectName("bwVaultIssue")
            self.status.setProperty("level", "warning")
            nl.addWidget(self.status)
            self.form = [first, checks, notes]
            self.update_ok()
            QtCore.QTimer.singleShot(0, lambda: vc._alive(self.note) and self.note.setFocus())

        def problems(self):
            return rc.blockers(self.checklist.lines, self.note.toPlainText())

        def accept(self):
            if self.problems():
                self.update_ok()
                return False
            ok, _r = self.run("Approve", self.s.vault.approve, self.pn, note=self.note.toPlainText().strip(),
                              checklist=self.checklist.record())
            if not ok:
                return False
            rc.lock_released(self.s.work, [self.pn])  # released means read-only, on disk too
            _console(f"Vault: {self.pn} released as {self.to}", "msg")
            return self.finish()

    class RejectPanel(Panel):
        """Reject: a reason (required); the number is superseded and work goes on at the next."""

        ok_text = "Reject"
        ready_text = "Ready: the reason is recorded with the rejection."

        def __init__(self, s, pn):
            super().__init__()
            from bruceworx import pdm

            self.s, self.pn = s, pn
            item = s.vault.items[pn]
            rev = item.latest
            if item.state != "In Review":
                raise ValueError(f"{_display(s, pn)} is {item.state}, not in review.")
            if rev.eco is None:
                after = (f"{rev.code} is kept as rejected and work goes on at <b>{pdm.next_prerelease(rev.code)}</b>, "
                         "in work.")
            else:
                after = f"It goes back to work as {rev.code}; {rev.eco} releases it later."
            first = box("Reject", "reject", "error")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(f"Send {_html(item.description or pn)} <b>{_html(_display(s, pn))}</b> back to "
                                f"work. {after}"))
            lay.addSpacing(4)
            lay.addWidget(QtWidgets.QLabel("Reason"))
            self.reason = text_box("What has to change before it can be released", 90, self.update_ok)
            lay.addWidget(self.reason)
            self.status = hint()
            lay.addWidget(self.status)
            self.form = [first]
            self.update_ok()
            QtCore.QTimer.singleShot(0, lambda: vc._alive(self.reason) and self.reason.setFocus())

        def problems(self):
            return [] if self.reason.toPlainText().strip() else ["Give a reason"]

        def checklist(self):
            """The checklist as it stood, recorded with the rejection (as the command line does)."""
            try:
                latest = self.s.vault.latest_version(self.pn)
                return rc.record(rc.check_item(self.s.work, self.pn), False, latest.sha256 if latest else "")
            except Exception:
                return None

        def accept(self):
            if self.problems():
                return False
            ok, rev = self.run("Reject", self.s.vault.reject, self.pn, self.reason.toPlainText().strip(),
                               checklist=self.checklist())
            if not ok:
                return False
            _console(f"Vault: {self.pn} rejected; work goes on at {rev.code}", "msg")
            return self.finish()

    def item_list(rows, checked=()):
        """A checkable list of (key, text, tooltip)."""
        lst = QtWidgets.QListWidget()
        lst.setObjectName("bwChangeItems")
        lst.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        lst.setTextElideMode(QtCore.Qt.ElideRight)
        for key, text, tip in rows:
            it = QtWidgets.QListWidgetItem(text)
            it.setData(QtCore.Qt.UserRole, key)
            it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            it.setCheckState(QtCore.Qt.Checked if key in checked else QtCore.Qt.Unchecked)
            if tip:
                it.setToolTip(tip)
            lst.addItem(it)
        fixed_height(lst, max(1, min(len(rows), 6)) * 26 + 8)
        return lst

    def checked_keys(lst):
        return [lst.item(i).data(QtCore.Qt.UserRole) for i in range(lst.count())
                if lst.item(i).checkState() == QtCore.Qt.Checked]

    class RaiseEcrPanel(Panel):
        """Raise ECR: the problem, the affected released items, attachments."""

        def __init__(self, s, pns):
            super().__init__()
            self.s = s
            self.number = s.vault.next_ecr_number()
            self.ok_text = f"Raise {self.number}"
            self.ready_text = f"Ready: {self.number} is recorded with its problem, items and attachments."
            released = released_items(s.vault)
            if not released:
                raise ValueError("Nothing in the vault is released yet. Before a release, changes are made in the "
                                 "work revision; reject a review to go on to the next number.")
            chosen = [p for p in pns if p in released]
            first = box(f"Raise {self.number}", "ecr", "warn")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(f"<b>{self.number}</b> asks for a change to released items. Once approved, an ECO "
                                "opens their next revision (and their drawings') for work."))
            lay.addSpacing(4)
            lay.addWidget(QtWidgets.QLabel("Problem"))
            self.problem = text_box("What is wrong, where, and why it matters", 90, self.update_ok)
            lay.addWidget(self.problem)
            items = box("Affected items")
            il = QtWidgets.QVBoxLayout(items)
            rows = []
            for pn in sorted(released, key=lambda p: (p not in chosen, p)):
                item = s.vault.items[pn]
                rows.append((pn, f"{_display(s, pn)}  {item.description}".rstrip(), item.item_type))
            self.items = item_list(rows, chosen)
            self.items.itemChanged.connect(lambda *_a: self.update_ok())
            il.addWidget(self.items)
            self.drawings = hint()
            il.addWidget(self.drawings)
            files = box("Attachments")
            fl = QtWidgets.QVBoxLayout(files)
            self.files = QtWidgets.QListWidget()
            self.files.setObjectName("bwChangeItems")
            fixed_height(self.files, 64)
            fl.addWidget(self.files)
            buttons = QtWidgets.QHBoxLayout()
            add = QtWidgets.QPushButton("Add…")
            add.clicked.connect(slot(self.add_files))
            remove = QtWidgets.QPushButton("Remove")
            remove.clicked.connect(slot(self.remove_file))
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addStretch(1)
            fl.addLayout(buttons)
            fl.addWidget(hint("Screenshots, PDFs, test reports: copied into the vault, never changed."))
            self.status = hint()
            lay.addWidget(self.status)
            self.form = [first, items, files]
            self.update_ok()
            QtCore.QTimer.singleShot(0, lambda: vc._alive(self.problem) and self.problem.setFocus())

        def add_files(self, paths=None):
            if paths is None:
                paths, _f = QtWidgets.QFileDialog.getOpenFileNames(_window(), "Attach files")
            for path in paths or ():
                it = QtWidgets.QListWidgetItem(os.path.basename(path))
                it.setData(QtCore.Qt.UserRole, path)
                it.setToolTip(path)
                self.files.addItem(it)

        def remove_file(self):
            for it in self.files.selectedItems():
                self.files.takeItem(self.files.row(it))

        def attachments(self):
            return [self.files.item(i).data(QtCore.Qt.UserRole) for i in range(self.files.count())]

        def problems(self):
            out = []
            if not self.problem.toPlainText().strip():
                out.append("Describe the problem")
            chosen = checked_keys(self.items)
            if not chosen:
                out.append("Tick at least one affected item")
            drawings = [d.part_number for pn in chosen for d in self.s.vault.drawings_of(pn)]
            self.drawings.setText(("Their drawings change too: " + ", ".join(drawings)) if drawings else "")
            self.drawings.setVisible(bool(drawings))
            return out

        def accept(self):
            if self.problems():
                return False
            ok, ecr = self.run("Raise ECR", self.s.vault.raise_ecr, None, self.problem.toPlainText().strip(),
                               checked_keys(self.items), attachments=self.attachments())
            if not ok:
                return False
            _console(f"Vault: raised {ecr.number}", "msg")
            return self.finish()

    class EcrPanel(Panel):
        """An ECR: its problem, items and attachments; decide it (approve or reject, with a reason)."""

        def __init__(self, s, number):
            super().__init__()
            self.s, self.number = s, number
            ecr = s.vault.ecrs[number]
            self.ecr = ecr
            self.open = ecr.status == "Open"
            self.ok_text = "Approve" if self.open else "Close"
            self.ready_text = "Ready: the decision and the reason are recorded."
            raised = ecr.records[0] if ecr.records else {}
            first = box(number, "ecr", "warn")
            lay = QtWidgets.QVBoxLayout(first)
            who = _who(raised.get("by"), s.me)
            when = _when(raised.get("at"))
            lay.addWidget(intro(f"<b>{number}</b> · {ecr.status}" + (f" · raised by {who}" if who else "")
                                + (f", {when}" if when else "")))
            problem = QtWidgets.QLabel(_html(ecr.problem).replace("\n", "<br>"))
            problem.setWordWrap(True)
            problem.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            lay.addWidget(problem)
            items = box("Affected items")
            il = QtWidgets.QVBoxLayout(items)
            for pn in ecr.items:
                item = s.vault.items[pn]
                il.addWidget(QtWidgets.QLabel(f"{_display(s, pn)}  {item.description}".rstrip()))
                for d in s.vault.drawings_of(pn):
                    il.addWidget(hint(f"and its drawing {_display(s, d.part_number)}"))
            self.form = [first, items]
            if ecr.attachments:
                files = box("Attachments")
                fl = QtWidgets.QVBoxLayout(files)
                for record in ecr.attachments:
                    b = QtWidgets.QPushButton(f"{record['filename']}  ({vc.size_text(int(record['size']))})")
                    b.setFlat(True)
                    b.setStyleSheet("text-align: left;")
                    b.setToolTip("Open a copy")
                    b.clicked.connect(lambda _c=False, r=record: open_attachment(s, r))
                    fl.addWidget(b)
                self.form.append(files)
            decision = box("Decision", "decide", "accent")
            dl = QtWidgets.QVBoxLayout(decision)
            if self.open:
                self.approve = QtWidgets.QRadioButton("Approve: an ECO will open the next revision")
                self.reject_ = QtWidgets.QRadioButton("Reject: nothing changes")
                self.approve.setChecked(True)
                self.approve.toggled.connect(lambda _on: self.update_ok())
                dl.addWidget(self.approve)
                dl.addWidget(self.reject_)
                dl.addSpacing(4)
                dl.addWidget(QtWidgets.QLabel("Reason"))
                self.reason = text_box("Why, for the record", 70, self.update_ok)
                dl.addWidget(self.reason)
                self.status = hint()
                dl.addWidget(self.status)
            else:
                text = f"{ecr.status}" + (f": {ecr.reason}" if ecr.reason else "")
                dl.addWidget(QtWidgets.QLabel(_html(text)))
                ecos = [n for n, e in s.vault.ecos.items() if number in e.ecrs]
                if ecos:
                    dl.addWidget(hint("In " + ", ".join(ecos)))
                elif ecr.status == "Approved":
                    dl.addWidget(hint(f"No ECO yet: {_menu_name()} → Vault → Open ECO…"))
            self.form.append(decision)
            self.update_ok()

        def getStandardButtons(self):
            if not self.open:
                return QtWidgets.QDialogButtonBox.Ok
            return super().getStandardButtons()

        def problems(self):
            if not self.open:
                return []
            self.ok_text = "Approve ECR" if self.approve.isChecked() else "Reject ECR"
            return [] if self.reason.toPlainText().strip() else ["Give the reason"]

        def accept(self):
            if not self.open:
                return self.finish()
            if self.problems():
                return False
            approved = self.approve.isChecked()
            ok, _r = self.run("Decide ECR", self.s.vault.decide_ecr, self.number, approved,
                              reason=self.reason.toPlainText().strip())
            if not ok:
                return False
            _console(f"Vault: {self.number} {'approved' if approved else 'rejected'}", "msg")
            self.finish()
            if approved:
                QtCore.QTimer.singleShot(0, lambda: _offer_open_eco(self.number))
            return True

    class OpenEcoPanel(Panel):
        """Open ECO: approved ECRs in, the items and drawings with their next letter, check out all."""

        def __init__(self, s, ecrs):
            super().__init__()
            self.s = s
            self.number = s.vault.next_eco_number()
            self.ok_text = f"Open {self.number}"
            self.ready_text = "Ready: each item gets its next letter, in work."
            ready = approvable_ecrs(s.vault)
            first = box(f"Open {self.number}", "eco", "accent")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(f"<b>{self.number}</b> groups approved ECRs. Their items and drawings get their next "
                                "letter in work; they release together with Release ECO…"))
            rows = [(n, f"{n}  {s.vault.ecrs[n].problem.splitlines()[0][:60]}", s.vault.ecrs[n].problem)
                    for n in ready]
            self.ecrs = item_list(rows, [n for n in ecrs if n in ready])
            self.ecrs.itemChanged.connect(lambda *_a: self.update_ok())
            lay.addWidget(QtWidgets.QLabel("Approved ECRs"))
            lay.addWidget(self.ecrs)
            items = box("Items in work")
            il = QtWidgets.QVBoxLayout(items)
            self.tree = QtWidgets.QTreeWidget()
            self.tree.setObjectName("bwVaultFiles")
            self.tree.setColumnCount(3)
            self.tree.setHeaderLabels(["Item", "Rev", "Description"])
            self.tree.setRootIsDecorated(False)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.tree.setFocusPolicy(QtCore.Qt.NoFocus)
            self.tree.setColumnWidth(0, 150)
            self.tree.setColumnWidth(1, 56)
            il.addWidget(self.tree)
            self.checkout = QtWidgets.QCheckBox("Check out all of them after opening")
            self.checkout.setChecked(True)
            il.addWidget(self.checkout)
            self.status = hint()
            il.addWidget(self.status)
            self.form = [first, items]
            self.update_ok()

        def problems(self):
            chosen = checked_keys(self.ecrs)
            rows, problems = eco_plan(self.s.vault, chosen)
            self.tree.clear()
            for row in rows:
                current = row["current"] or "–"
                it = QtWidgets.QTreeWidgetItem([row["part_number"], f"{current} → {row['next']}",
                                                row["description"] or row["item_type"]])
                it.setToolTip(0, f"{row['item_type']}: {row['reason']}")
                self.tree.addTopLevelItem(it)
            fixed_height(self.tree, self.tree.header().sizeHint().height() + max(1, min(len(rows), 6)) * 24 + 8)
            if not chosen:
                problems = ["Tick at least one approved ECR"] + problems
            return problems

        def accept(self):
            if self.problems():
                return False
            ok, eco = self.run("Open ECO", self.s.vault.open_eco, None, checked_keys(self.ecrs))
            if not ok:
                return False
            _console(f"Vault: opened {eco.number} ({', '.join(eco.items)})", "msg")
            self.finish()
            if self.checkout.isChecked():
                failed = checkout_all(self.s, [pn for pn in eco.items if self.s.vault.versions.get(pn)])
                if failed:
                    _message("Open ECO", "Not checked out:\n" + "\n".join(f"{p}: {w}" for p, w in failed), "warning")
            return True

    class ReleaseEcoPanel(Panel):
        """Release ECO: every item in review, its checklist each, one release note."""

        def __init__(self, s, number):
            super().__init__()
            self.s, self.number = s, number
            eco = s.vault.ecos[number]
            if eco.status != "Open":
                raise ValueError(f"{number} is {eco.status}.")
            self.eco = eco
            self.ok_text = f"Release {plural(len(eco.items), 'Item')}"
            self.ready_text = "Ready: everything releases together, with each checklist and the note."
            first = box(f"Release {number}", "release", "ok")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(
                f"<b>{number}</b> ({_html(', '.join(eco.ecrs))}) releases {plural(len(eco.items), 'item')} together. "
                "Each one's previous letter is kept as superseded."))
            self.form = [first]
            self.checklists = {}
            self.submits = {}
            for pn in eco.items:
                item = s.vault.items[pn]
                w = box(_display(s, pn), "released" if item.state == "In Review" else "history",
                        "ok" if item.state == "In Review" else "secondary")
                wl = QtWidgets.QVBoxLayout(w)
                state = QtWidgets.QHBoxLayout()
                label = hint(f"{item.description}".strip() + (" · " if item.description else "") + item.state)
                state.addWidget(label, 1)
                if item.state == "WIP":
                    sub = QtWidgets.QPushButton("Submit for Review")
                    sub.clicked.connect(lambda _c=False, p=pn: self.submit(p))
                    state.addWidget(sub)
                    self.submits[pn] = (sub, label)
                wl.addLayout(state)
                cl = Checklist(s, pn, eco.items)
                cl.changed.connect(slot(self.update_ok))
                wl.addWidget(cl)
                self.checklists[pn] = cl
                self.form.append(w)
            notes = box("Release note")
            nl = QtWidgets.QVBoxLayout(notes)
            self.note = text_box("What changed in this ECO, for the record", 70, self.update_ok)
            nl.addWidget(self.note)
            self.status = hint()
            self.status.setObjectName("bwVaultIssue")
            self.status.setProperty("level", "warning")
            nl.addWidget(self.status)
            self.form.append(notes)
            self.update_ok()

        def submit(self, pn):
            if self.s.vault.locks.get(pn):
                _message("Submit for Review", f"{_display(self.s, pn)} is checked out. Check it in first.")
                return
            ok, _r = self.run("Submit for Review", self.s.vault.submit, pn)
            if ok and pn in self.submits:
                sub, label = self.submits.pop(pn)
                sub.hide()
                label.setText(label.text().rsplit("·", 1)[0] + "· In Review" if "·" in label.text()
                              else "In Review")
                self.checklists[pn].check()
            self.update_ok()

        def problems(self):
            out = []
            for pn in self.eco.items:
                item = self.s.vault.items[pn]
                if item.latest.state != "In Review":
                    out.append(f"Submit {pn} for review")
                out += [f"{pn}: {b[0].lower() + b[1:]}" for b in self.checklists[pn].blockers()]
            if not self.note.toPlainText().strip():
                out.append("Write a release note")
            return out

        def accept(self):
            if self.problems():
                self.update_ok()
                return False
            checklists = {pn: cl.record() for pn, cl in self.checklists.items()}
            ok, _r = self.run("Release ECO", self.s.vault.release_eco, self.number,
                              note=self.note.toPlainText().strip(), checklists=checklists)
            if not ok:
                return False
            rc.lock_released(self.s.work, self.eco.items)
            _console(f"Vault: released {self.number}", "msg")
            return self.finish()

    class RetirePanel(Panel):
        """Retire: what uses the item, its drawings, a reason (required); OK makes it Obsolete."""

        def __init__(self, s, pn):
            super().__init__()
            self.s, self.pn = s, pn
            item = s.vault.items[pn]
            plan = retire_plan(s.work, pn)
            if plan["blockers"] and retire_refusal(item):
                raise ValueError(plan["blockers"][0])
            self.plan = plan
            self.ready_text = "Ready: the reason is recorded; the item stays in the vault, read-only."
            first = box("Retire", "retire", "secondary")
            lay = QtWidgets.QVBoxLayout(first)
            lay.addWidget(intro(
                f"Retiring makes {_html(item.description or pn)} <b>{_html(_display(s, pn))}</b> obsolete. It stays "
                "in the vault, read-only, and nothing changes it again. There is no way back: a replacement is a "
                "new item."))
            used = box("Where used")
            ul = QtWidgets.QVBoxLayout(used)
            if plan["users"]:
                grid = QtWidgets.QGridLayout()
                grid.setHorizontalSpacing(6)
                grid.setVerticalSpacing(2)
                grid.setColumnStretch(1, 1)
                for i, row in enumerate(plan["users"]):
                    state = "fail" if row["released"] else ("skip" if row["state"] == "Obsolete" else "todo")
                    grid.addWidget(mark(state), i, 0)
                    text = QtWidgets.QLabel(f"{_display(s, row['part_number'])}  ·  {row['state']}  ·  {row['via']}")
                    text.setObjectName("bwCheckText")
                    text.setProperty("state", state)
                    grid.addWidget(text, i, 1)
                ul.addLayout(grid)
                if plan["confirm"]:
                    self.confirm = QtWidgets.QCheckBox(
                        f"Retire anyway: I know {plural(len(plan['confirm']), 'item')} in work still "
                        f"use{'s' if len(plan['confirm']) == 1 else ''} it")
                    self.confirm.toggled.connect(lambda _on: self.update_ok())
                    ul.addWidget(self.confirm)
            else:
                ul.addWidget(hint("Nothing in the vault uses it: no assembly links to its file or lists it in a "
                                  "BOM."))
            self.form = [first, used]
            self.drawings = None
            if plan["drawings"] or plan["kept"]:
                dbox = box("Drawings")
                dl = QtWidgets.QVBoxLayout(dbox)
                if plan["drawings"]:
                    names = ", ".join(_display(s, d) for d in plan["drawings"])
                    self.drawings = QtWidgets.QCheckBox(f"Retire its drawing too: {names}")
                    self.drawings.setChecked(True)
                    self.drawings.toggled.connect(lambda _on: self.update_ok())
                    dl.addWidget(self.drawings)
                for d, state in plan["kept"]:
                    dl.addWidget(hint(f"{d} ({state}) is not released and stays as it is."))
                self.form.append(dbox)
            why = box("Reason")
            wl = QtWidgets.QVBoxLayout(why)
            self.reason = text_box("Why it is retired, and what replaces it", 76, self.update_ok)
            wl.addWidget(self.reason)
            self.status = hint()
            wl.addWidget(self.status)
            self.form.append(why)
            self.update_ok()
            QtCore.QTimer.singleShot(0, lambda: vc._alive(self.reason) and self.reason.setFocus())

        def targets(self):
            more = self.plan["drawings"] if self.drawings is not None and self.drawings.isChecked() else []
            return [self.pn] + list(more)

        def problems(self):
            n = len(self.targets())
            self.ok_text = "Retire" if n == 1 else f"Retire {n} Items"
            out = list(self.plan["blockers"])
            if self.plan["confirm"] and not self.confirm.isChecked():
                out.append("Tick “Retire anyway” to confirm what still uses it")
            if not self.reason.toPlainText().strip():
                out.append("Give the reason")
            return out

        def accept(self):
            if self.problems():
                self.update_ok()
                return False
            reason = self.reason.toPlainText().strip()
            done = []
            for pn in self.targets():
                ok, _r = self.run("Retire", self.s.vault.retire, pn, reason)
                if not ok:
                    break
                done.append(pn)
            if not done:
                return False
            rc.lock_released(self.s.work, done)
            _console(f"Vault: retired {', '.join(done)}", "msg")
            return self.finish()

    return {"Checklist": Checklist, "ApprovePanel": ApprovePanel, "RejectPanel": RejectPanel,
            "RaiseEcrPanel": RaiseEcrPanel, "EcrPanel": EcrPanel, "OpenEcoPanel": OpenEcoPanel,
            "ReleaseEcoPanel": ReleaseEcoPanel, "RetirePanel": RetirePanel}


def _menu_name():
    try:
        import bruceworx_menu

        return bruceworx_menu.MENU_TITLE.replace("&", "")
    except Exception:
        return "Tools"


def _offer_open_eco(number):
    s = _session()
    if s is None or number not in approvable_ecrs(s.vault):
        return
    if _vc()._ask("Open ECO", f"{number} is approved. Open an ECO for it now?", "Open ECO…", "Later"):
        open_eco([number])


# ----- install ---------------------------------------------------------------------------------


def _register_icons():
    import FreeCADGui

    for entry in ITEM_COMMANDS + VAULT_COMMANDS:
        kind, role = entry[3], entry[4]
        FreeCADGui.addIcon(_icon_name(kind, role), svg_icon(kind, _COLOURS[role]).encode("utf-8"), "SVG")


def _extend_vault_menu():
    """Decide ECR... and Open ECO... at the end of the BruceWorx > Vault menu (the item commands
    are in it already, through the vault client's context commands)."""
    import FreeCADGui
    from PySide import QtGui, QtWidgets

    sub = _window().findChild(QtWidgets.QMenu, "BruceWorxVaultMenu")
    if sub is None or sub.property("bwChanges"):
        return
    sub.setProperty("bwChanges", True)
    sub.addSeparator()
    actions = []
    for entry in VAULT_COMMANDS:
        act = sub.addAction(FreeCADGui.getIcon(_icon_name(entry[3], entry[4])) or QtGui.QIcon(), entry[1])
        act.setToolTip(entry[2])
        act.triggered.connect(lambda _c=False, n=entry[0]: FreeCADGui.runCommand(n))
        actions.append((entry, act))

    def update():
        s = _session()
        for entry, act in actions:
            try:
                act.setEnabled(s is not None and bool(entry[6](s)))
            except Exception:
                act.setEnabled(False)

    sub.aboutToShow.connect(update)


def install():
    """Commands, the vault client's menus and approve hook, the Vault panel's Changes rows."""
    if _state["installed"]:
        return
    _state["installed"] = True
    import FreeCADGui
    from PySide import QtCore

    _register_icons()
    for entry in ITEM_COMMANDS:
        FreeCADGui.addCommand(entry[0], _ItemCommand(entry))
    for entry in VAULT_COMMANDS:
        FreeCADGui.addCommand(entry[0], _VaultCommand(entry))
    _vc().add_item_commands(ITEM_COMMANDS, after="BruceWorx_VaultApprove")

    def late():
        try:
            _extend_vault_menu()
        except Exception as exc:
            _console(f"Vault changes: menu not extended: {exc}", "warn")

    QtCore.QTimer.singleShot(0, late)
