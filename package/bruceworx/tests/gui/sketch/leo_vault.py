# SPDX-License-Identifier: LGPL-2.1-or-later
"""Vault steps for the Leonardo parts: open the vault, check out, save the work copy, check in.

Without a part number (LEO_PN unset) this is a dry run: a plain new document, saved to SK_OUT.
"""

import os
import re

import harness as H
from harness import App, Gui, QtCore, QtWidgets, S, check, note


def _doc_name(text):
    return re.sub(r"[^A-Za-z0-9_]", "_", text) or "Part"


def start(pn, title):
    if not pn:
        note(f"dry run for {title}: no part number, nothing goes to the vault")
        return
    import bruceworx_vaultclient as vc

    s = vc.session()
    check("vault open (Preferences VaultPath)", s is not None, App.ParamGet(vc.PREFS_PATH).GetString("VaultPath", ""))
    if s is None:
        return
    note(f"vault {s.name} at {s.root}, me={s.me}, work folder {s.work.root}")
    check(f"{pn} is an item in the vault", pn in s.vault.model.items)
    # The Vault panel, as a user opens it, with the item found by its search box.
    try:
        vc.show_panel(True, focus_search=True)
        H.pump(600)
        H.grab("vault-panel")
    except Exception as exc:
        note(f"Vault panel: {exc}")
    st = s.status(pn)
    note(f"{pn} status before: {st.code} {getattr(st, 'state', '')} lock={s.vault.locks.get(pn)}")
    S["vault_target"] = pn
    ok = vc.check_out(pn)
    s.refresh()
    st = s.status(pn)
    check(f"Check Out {pn}", ok and s.vault.locks.get(pn) == s.me, f"{st.code} lock={s.vault.locks.get(pn)}")


def document(pn, title):
    """The part's document: the checked-out work copy, or a new document saved there later."""
    if pn:
        import bruceworx_vaultclient as vc

        s = vc.session()
        path = s.work.path(pn) if s is not None else ""
        if path and os.path.exists(path):
            Gui.loadFile(path) if hasattr(Gui, "loadFile") else App.openDocument(path)
            H.pump(500)
            doc = App.ActiveDocument
            note(f"opened the work copy {path}")
            S["path"] = path
            return doc
        S["path"] = path
    doc = App.newDocument(_doc_name(pn or title))
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    return doc


def finish(pn, doc, comment):
    if not pn:
        out = os.path.join(H.OUT, f"{_doc_name(doc.Name)}-dry.FCStd")
        doc.saveAs(out)
        note(f"dry run saved to {out}")
        return
    import bruceworx_vaultclient as vc

    s = vc.session()
    path = S.get("path") or s.work.path(pn)
    if os.path.normcase(doc.FileName or "") != os.path.normcase(path):
        doc.saveAs(path)  # Save As into the work folder, named by P/N
    else:
        doc.save()
    check(f"saved as {os.path.basename(path)} in the work folder", os.path.exists(path), path)
    s.refresh()
    st = s.status(pn)
    note(f"{pn} status after save: {st.code}")
    # Check In… task panel: type the comment, press its OK / Check In button.
    Gui.Selection.clearSelection()
    ok = vc.check_in(pn)
    check("Check In panel opens", ok and H.wait_for(lambda: bool(Gui.Control.activeDialog()), 3000))
    tv = H.task_view()
    edits = [w for w in tv.findChildren(QtWidgets.QPlainTextEdit) if w.isVisible()] if tv else []
    check("Check In panel has a comment box", bool(edits))
    if edits:
        edits[0].setPlainText(comment)
        H.pump(200)
    H.check_readable("Check In panel readable", tv) if tv else None
    H.grab("checkin-panel")
    box = H.button_box()
    buttons = [b.text() for b in box.buttons()] if box else []
    note(f"Check In panel buttons: {buttons}")
    H.check_buttons_on_screen("Check In buttons visible on screen", box)
    if box is not None:
        H.press(box, QtWidgets.QDialogButtonBox.Ok)
    H.wait_for(lambda: not Gui.Control.activeDialog(), 8000)
    s.refresh()
    versions = s.vault.versions.get(pn, [])
    check(f"{pn} checked in (a version with the comment)", bool(versions) and
          any(comment[:20] in (getattr(v, "comment", "") or "") for v in versions),
          [getattr(v, "comment", "")[:40] for v in versions][-3:])
    check(f"{pn} lock released", not s.vault.locks.get(pn), s.vault.locks.get(pn))
    H.grab("checked-in")
