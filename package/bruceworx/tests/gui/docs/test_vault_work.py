"""Day-to-day vault work in the window, in a temporary vault only: open read-only (lock on the tab),
the first-edit offer, the save prompt for a file not checked out (Don't Save, then Check Out),
Browser badges and lock icons, the Vault panel card (filters, search, status lines, right-click
menu), Check In (comment required), Undo Check-Out, a newer version from "another Mac" and Get
Latest, Get Revision, a lock held by someone else, and an assembly check-out. Quits itself.
"""
import os
import sys
import zipfile

sys.path.insert(0, os.environ.get("BW_TESTDIR") or os.path.dirname(os.path.abspath(__file__)))
import bwdocs as C  # noqa: E402

import FreeCAD  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtWidgets  # noqa: E402

V = {}
LOCK = "\U0001F512"


def vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def s():
    return vc().session()


def code(pn):
    s().invalidate()
    return s().status(pn).code


def doc_of(pn):
    return vc().document_for(s().work.path(pn))


def tab_titles():
    mdi = C.mw().findChild(QtWidgets.QMdiArea)
    return [w.windowTitle() for w in mdi.subWindowList()] if mdi else []


def badges(pn):
    import bruceworx_browser

    doc = doc_of(pn)
    if doc is None:
        return None
    part = doc.getObject("Part") or doc.getObject("Assembly")
    return bruceworx_browser.row_info(part)[1]


def card():
    return C.mw().findChild(QtWidgets.QFrame, "Vault")


def vpanel():
    c = card()
    return c.widget() if c is not None else None


def panel_rows():
    p = vpanel()
    return [p.model.item(i).data(0x0100) for i in range(p.model.rowCount())] if p else []


def browser():
    return C.mw().findChild(QtWidgets.QDockWidget, "Model") or C.mw().findChild(QtWidgets.QDockWidget, "Combo View")


def another_mac_checks_in(pn, note):
    from bruceworx.pdm_folder import FolderVault

    dana = FolderVault(s().vault.ref_path, actor="dana@imac")
    dana.checkout(pn)
    path = dana.fetch(pn, os.path.join(C.RUN, "dana"))
    os.chmod(path, 0o644)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("dana.txt", note)
    dana.checkin(pn, path, comment=note)


# ----- steps -----------------------------------------------------------------------------------


@C.step(2500)
def setup():
    C.setup_window()
    vault = C.new_vault("Work Test")
    src = os.path.join(C.RUN, "src")
    C.sample_files(src)
    for name in os.listdir(src):
        if not name.endswith(".FCStd"):
            os.remove(os.path.join(src, name))
    numbers, report = C.import_folder(vault.root, src)
    C.check("3 sample files imported", len(numbers) == 3 and not report.failed, numbers, report.failed)
    V.update(br=numbers["bracket.FCStd"], pin=numbers["pin.FCStd"], hinge=numbers["hinge.FCStd"])
    vc().changed(refresh=True)
    C.check("vault client open on the temporary vault", s() is not None and s().root == vault.root)


@C.step(800)
def open_read_only():
    C.answer("Open Read-Only", match="Check out", shot="open-ask")
    ok = vc().open_from_vault(V["br"])
    C.pump(1200)
    asked = [m for m in C.MODALS if "Check out" in m[1] and "Open Read-Only" in m[2]]
    C.check("Open from Vault asks: check out, or read-only", bool(asked), C.MODALS[-1:])
    doc = doc_of(V["br"])
    C.check("opened read-only", ok and doc is not None and not os.access(s().work.path(V["br"]), os.W_OK))
    C.pump(600)
    titles = tab_titles()
    C.check("the tab shows a lock", any(t.startswith(LOCK) and V["br"] in t for t in titles), titles)
    b = badges(V["br"])
    C.log("badges read-only", b)
    C.check("Browser: revision chip, no lock (checked in)", b is not None and any("01" in x[0] for x in b)
            and not any(LOCK in x[0] for x in b), b)
    C.shot("browser-readonly", browser())
    C.check("Check Out enabled, Check In disabled for a read-only file",
            vc().available("BruceWorx_VaultCheckOut", V["br"]) and not vc().available("BruceWorx_VaultCheckIn", V["br"]))


@C.step(600)
def first_edit_offer():
    doc = doc_of(V["br"])
    FreeCAD.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    C.answer("Not Now", match="not checked out to you", shot="first-edit-offer")
    doc.openTransaction("Change length")
    doc.getObject("Base").Length = 62
    doc.commitTransaction()
    doc.recompute()
    C.pump(800)
    said = [m for m in C.MODALS if "not checked out to you" in m[1]]
    C.check("first edit of a read-only vault file offers Check Out", bool(said), C.MODALS[-1:])
    C.check("Not Now leaves it not checked out", code(V["br"]) == "up_to_date", code(V["br"]))


@C.step(600)
def save_prompt_dont_save():
    doc = doc_of(V["br"])
    before = os.path.getmtime(s().work.path(V["br"]))
    C.answer("Don't Save", match="to save?", shot="save-prompt")
    n = len(C.MODALS)
    Gui.runCommand("Std_Save")
    C.pump(1500)
    seen = C.MODALS[n:]
    C.log("save modals", seen)
    C.check("saving a file not checked out asks to check it out", any("to save?" in m[1] for m in seen), seen)
    C.check("Don't Save: the file is not written", os.path.getmtime(s().work.path(V["br"])) == before)
    C.check("Don't Save: still not checked out", code(V["br"]) == "up_to_date", code(V["br"]))
    later = [m for m in seen if "to save?" not in m[1]]
    C.note("after Don't Save the user also sees:", [(m[0], m[1][:120]) for m in later])
    V["dont_save_followups"] = later
    C.check("Don't Save: at most one plain message follows (no stack of errors)", len(later) <= 1, later)


@C.step(600)
def save_prompt_check_out():
    before = os.path.getmtime(s().work.path(V["br"]))
    C.answer("Check Out", match="to save?")
    n = len(C.MODALS)
    Gui.runCommand("Std_Save")
    C.pump(1500)
    seen = C.MODALS[n:]
    C.check("Check Out in the save prompt saves the file", os.path.getmtime(s().work.path(V["br"])) > before, seen)
    C.check("it is checked out to me and modified", code(V["br"]) in ("modified", "checked_out"), code(V["br"]))
    C.check("no backup file written into the vault", not [f for _r, _d, fs in os.walk(s().root) for f in fs
                                                          if f.endswith(".FCBak")])
    C.pump(600)
    titles = tab_titles()
    C.check("the lock leaves the tab", not any(t.startswith(LOCK) and V["br"] in t for t in titles), titles)
    vc().changed()
    C.pump(800)
    b = badges(V["br"])
    C.log("badges modified", b)
    C.check("Browser: lock and pencil (checked out by me, modified)", b is not None and
            any(LOCK in x[0] and "✎" in x[0] for x in b), b)
    for t in C.mw().findChildren(QtWidgets.QTreeWidget):
        if t.metaObject().className() == "Gui::TreeWidget":
            t.expandAll()
    C.shot("browser-checkedout", browser())
    sb = C.mw().statusBar().findChild(QtWidgets.QLabel, "vaultReadout")
    C.check("status bar: 1 checked out", sb is not None and "1 checked out" in sb.text(), sb.text() if sb else None)


@C.step(800)
def vault_panel_card():
    C.check("Vault Panel command enabled", C.command_enabled("BruceWorx_VaultPanel") is True)
    # Cmd-K runs commands with FreeCADGui.runCommand (bruceworx_search).
    Gui.runCommand("BruceWorx_VaultPanel")
    C.wait_for(lambda: card() is not None and card().isVisible(), 2000)
    C.check("Vault Panel from Cmd-K (runCommand) shows the card", card() is not None and card().isVisible())
    if card() is None or not card().isVisible():
        act = C.command_action("BruceWorx_VaultPanel")
        C.check("View menu has Vault Panel", act is not None)
        if act is not None:
            act.trigger()
        C.wait_for(lambda: card() is not None and card().isVisible(), 2000)
        C.check("Vault Panel from the View menu shows the card", card() is not None and card().isVisible())
    if card() is None or not card().isVisible():
        vc().show_panel(True)
        C.wait_for(lambda: card() is not None and card().isVisible(), 2000)
    c, p = card(), vpanel()
    C.check("the Vault panel card shows", c is not None and c.isVisible())
    if p is None:
        return
    C.pump(500)
    C.check("card lists the 3 items", p.model.rowCount() == 3, p.model.rowCount())
    C.check("card header names the vault", p.name.text() == "Work Test", p.name.text())
    row = p.rows.get(V["br"], {})
    C.check("bracket row: checked out by you, modified", "Checked out by you" in row.get("status_text", ""),
            row.get("status_text"))
    for key, want in (("parts", 2), ("assemblies", 1), ("drawings", 0), ("checked_out", 1), ("all", 3)):
        p.chips[key].click()
        C.pump(200)
        C.check(f"filter chip {p.chips[key].text()!r}: {want}", p.model.rowCount() == want, p.model.rowCount())
    p.search.setText("pin")
    C.pump(300)
    C.check("search 'pin' finds the pin", panel_rows() == [V["pin"]], panel_rows())
    p.search.setText("")
    C.pump(300)
    geo = c.geometry()
    mw_rect = C.mw().rect()
    C.check("card inside the window", mw_rect.contains(geo), geo.getRect(), mw_rect.getRect())
    C.shot("vault-card", c)
    C.shot("vault-card-window")
    C.readability(c, name="Vault panel card")
    C.clipped(c, name="Vault panel card")
    menu = vc().build_item_menu(C.mw(), V["br"], in_panel=True)
    acts = {a.text(): a.isEnabled() for a in menu.actions() if a.text()}
    C.log("card menu (bracket)", acts)
    C.check("right-click on a checked-out item: Check In and Undo enabled, Check Out disabled",
            acts.get("Check In…") and acts.get("Undo Check-Out…") and acts.get("Check Out") is False, acts)
    menu = vc().build_item_menu(C.mw(), V["pin"], in_panel=True)
    acts = {a.text(): a.isEnabled() for a in menu.actions() if a.text()}
    C.check("right-click on a checked-in item: Check Out and Submit enabled, Check In disabled",
            acts.get("Check Out") and acts.get("Submit for Review") and acts.get("Check In…") is False, acts)


@C.step(600)
def check_in_panel():
    doc = doc_of(V["br"])
    Gui.Selection.clearSelection()
    FreeCAD.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    C.check("Check In available on the checked-out file", vc().available("BruceWorx_VaultCheckIn"))
    Gui.runCommand("BruceWorx_VaultCheckIn")
    C.wait_for(lambda: C.task_button("ok") is not None, 3000)
    C.pump(500)
    p = vc()._state.get("panel")
    C.check("Check In opens in the task panel", p is not None and type(p).__name__ == "CheckInPanel")
    ok = C.task_button("ok")
    C.check("Check In disabled without a comment", ok is not None and not ok.isEnabled(), ok.text() if ok else None)
    C.check("it says a comment is required", p is not None and p.comment_hint.isVisible())
    C.check("the vault card steps aside while the task panel is open", card() is None or not card().isVisible())
    C.task_shot("checkin-empty")
    p.comment.setPlainText("   ")
    C.pump(200)
    C.check("spaces are not a comment", not ok.isEnabled())
    p.comment.setPlainText("Base 62 mm long")
    C.pump(300)
    C.check("a comment enables Check In", ok.isEnabled(), ok.text())
    C.log("check-in button", ok.text())
    C.task_shot("checkin-ready")
    C.readability(C.task_content(), name="Check In panel")
    C.clipped(C.task_content(), name="Check In panel")
    ok.click()
    C.pump(1500)
    C.check("Check In closes the panel", not Gui.Control.activeDialog())
    vers = s().vault.versions.get(V["br"], [])
    C.check("version 2 with the comment", len(vers) == 2 and vers[-1].comment == "Base 62 mm long",
            [(v.comment) for v in vers])
    C.check("checked in: up to date and read-only", code(V["br"]) == "up_to_date" and
            not os.access(s().work.path(V["br"]), os.W_OK), code(V["br"]))
    C.check("checked-in file closed (the default)", doc_of(V["br"]) is None)


@C.step(600)
def undo_check_out():
    pin = V["pin"]
    C.check("check out the pin", vc().check_out(pin) and code(pin) == "checked_out", code(pin))
    path = s().work.path(pin)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("edit.txt", "x")
    C.check("pin modified on disk", code(pin) == "modified", code(pin))
    C.answer("Discard Changes", match="Discard the changes", shot="undo-ask")
    ok = vc().undo_check_out(pin)
    C.pump(500)
    C.check("Undo Check-Out asks before discarding", any("Discard the changes" in m[1] for m in C.MODALS))
    C.check("Undo Check-Out: lock released, file back, read-only", ok and code(pin) == "up_to_date" and
            not os.access(path, os.W_OK), code(pin))


@C.step(800)
def newer_and_get_latest():
    br = V["br"]
    vc().open_from_vault(br, ask=False)
    C.pump(800)
    another_mac_checks_in(br, "Slot widened (Dana)")
    vc().changed(refresh=True)
    C.pump(1000)
    C.check("a newer version from another Mac shows as newer", code(br) == "newer", code(br))
    b = badges(br)
    C.check("Browser: newer badge", b is not None and any("newer" in x[0] for x in b), b)
    C.shot("browser-newer", browser())
    sb = C.mw().statusBar().findChild(QtWidgets.QLabel, "vaultReadout")
    C.check("status bar: 1 newer", sb is not None and "1 newer" in sb.text(), sb.text() if sb else None)
    C.shot("statusbar-newer", C.mw().statusBar())
    vc().show_panel(True)
    C.pump(800)
    row = vpanel().rows.get(br, {}) if vpanel() else {}
    C.check("Vault panel row: newer version", "Newer version" in row.get("status_text", ""), row.get("status_text"))
    C.shot("vault-card-newer", card())
    C.check("Get Latest enabled", vc().available("BruceWorx_VaultGetLatest", br))
    ok = vc().get_latest(br)
    C.pump(800)
    C.check("Get Latest: up to date", ok and code(br) == "up_to_date", code(br))
    doc = doc_of(br)
    C.check("the open file is reloaded with the new version", doc is not None)


@C.step(800)
def get_revision():
    br = V["br"]
    vc().show_panel(False)
    C.answer("OK", match="Open an earlier version", item="Version 1", shot="get-revision")
    vc()._state["target"] = br
    try:
        Gui.runCommand("BruceWorx_VaultGetRevision")
    finally:
        vc()._state["target"] = None
    C.pump(1500)
    asked = [m for m in C.MODALS if "Open an earlier version" in m[1]]
    C.check("Get Revision lists the versions", bool(asked) and len(asked[-1][2]) == 3, asked[-1][2] if asked else None)
    rev_docs = [d for d in FreeCAD.listDocuments().values() if os.sep + ".rev" + os.sep in d.FileName]
    C.check("an earlier version opens", len(rev_docs) == 1, [d.FileName for d in FreeCAD.listDocuments().values()])
    if rev_docs:
        C.check("it is read-only on disk", not os.access(rev_docs[0].FileName, os.W_OK))
    C.pump(500)
    titles = tab_titles()
    C.check("its tab shows a lock", sum(1 for t in titles if t.startswith(LOCK)) >= 1, titles)
    C.shot("revision-window")
    C.close_all()


@C.step(800)
def locked_by_someone_else():
    from bruceworx.pdm_folder import FolderVault

    pin = V["pin"]
    FolderVault(s().vault.ref_path, actor="dana@imac").checkout(pin)
    vc().changed(refresh=True)
    C.check("pin: checked out by someone else", code(pin) == "checked_out_other", code(pin))
    vc().open_from_vault(pin, ask=False)
    C.pump(800)
    b = badges(pin)
    C.check("Browser: lock with the holder's name", b is not None and any(LOCK in x[0] and "dana" in x[0] for x in b), b)
    C.shot("browser-other", browser())
    C.check("Check Out disabled while someone else holds it", not vc().available("BruceWorx_VaultCheckOut", pin))
    doc = doc_of(pin)
    FreeCAD.setActiveDocument(doc.Name)
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    doc.getObject("Base").Height = 50
    doc.recompute()
    Gui.getDocument(doc.Name).Modified = True
    n = len(C.MODALS)
    Gui.runCommand("Std_Save")
    C.pump(1500)
    seen = C.MODALS[n:]
    C.check("saving it says who has it", any("dana" in m[1] for m in seen), seen)
    C.close_all()


@C.step(800)
def assembly_check_out():
    from bruceworx.pdm_folder import FolderVault

    FolderVault(s().vault.ref_path, actor="dana@imac").undo_checkout(V["pin"], force=True)
    vc().changed(refresh=True)
    hinge = V["hinge"]
    C.check("check out the assembly", vc().check_out(hinge) and code(hinge) == "checked_out", code(hinge))
    for pn in (V["br"], V["pin"]):
        path = s().work.path(pn)
        C.check(f"linked {pn} fetched read-only", os.path.exists(path) and not os.access(path, os.W_OK))
    vc().open_from_vault(hinge, ask=False)
    C.pump(1500)
    adoc = doc_of(hinge)
    bad = [o.Name for o in adoc.Objects if o.TypeId == "App::Link" and o.getLinkedObject(True) in (None, o)] \
        if adoc else ["(not open)"]
    C.check("assembly opens with its parts linked", not bad, bad)
    for t in C.mw().findChildren(QtWidgets.QTreeWidget):
        if t.metaObject().className() == "Gui::TreeWidget":
            t.expandAll()
    C.pump(500)
    C.shot("browser-assembly", browser())
    FreeCAD.setActiveDocument(adoc.Name)
    Gui.ActiveDocument = Gui.getDocument(adoc.Name)
    vc().check_in(hinge)
    C.wait_for(lambda: C.task_button("ok") is not None, 3000)
    p = vc()._state.get("panel")
    rows = [(r["part_number"], r["enabled"], r["checked"]) for r in p.rows] if p else []
    C.log("assembly check-in rows", rows)
    C.check("assembly Check In lists its parts, greyed (not checked out)",
            len(rows) == 3 and rows[0][0] == hinge and all(not r[1] for r in rows[1:]), rows)
    C.check("assembly unchanged: nothing ticked, Check In disabled", C.task_button("ok") is not None and
            not C.task_button("ok").isEnabled())
    C.task_shot("checkin-assembly")
    C.task_button("cancel").click()
    C.pump(500)
    C.answer("Discard Changes", match="Discard")
    vc().undo_check_out(hinge)
    C.pump(300)
    C.check("assembly lock released", code(hinge) == "up_to_date", code(hinge))


C.SAY.update({'setup': 'Make a temporary vault and import a hinge assembly with its two parts', 'open_read_only': 'Open the bracket read-only: expect a lock on its tab', 'first_edit_offer': 'Edit the read-only bracket: expect an offer to check it out; answer Not Now', 'save_prompt_dont_save': "Save it: expect 'Check out to save?'; answer Don't Save", 'save_prompt_check_out': 'Save again and answer Check Out: expect the save to go through and a lock badge', 'vault_panel_card': 'Show the Vault panel card: filters, search and the right-click menu', 'check_in_panel': 'Check In: OK disabled until a comment is typed, then check in', 'undo_check_out': 'Check out the pin, change it, Undo Check-Out: expect the change discarded', 'newer_and_get_latest': "Another Mac checks in the bracket: expect 'newer' badges, then Get Latest", 'get_revision': 'Get Revision...: pick version 1, expect it to open read-only', 'locked_by_someone_else': 'Another user holds the pin: expect their name on the lock, save refused', 'assembly_check_out': 'Check out the assembly: its parts come read-only; Check In lists them greyed'})
C.run()
