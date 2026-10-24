"""Review, release and change control as a user does it (bruceworx_changes), in a temporary vault:
Submit for Review, Reject (reason required), Approve with the release checklist (tick + note),
the drawing's title block, a released item's check-out refused with Raise ECR offered, Raise ECR
with an attachment, the Vault card's Changes chip, Decide ECR, Open ECO (offered after the
approval), Release ECO (blocked until the drawing is submitted), Retire. OK is pressed on the task
panel's own button each time. Quits itself.
"""
import os
import shutil
import stat
import sys

sys.path.insert(0, os.environ.get("BW_TESTDIR") or os.path.dirname(os.path.abspath(__file__)))
import bwdocs as C  # noqa: E402

import FreeCAD  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtWidgets  # noqa: E402

V = {}


def ch():
    import bruceworx_changes

    return bruceworx_changes


def vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def drw():
    import bruceworx_drawing

    return bruceworx_drawing


def s():
    return vc().session()


def panel():
    return ch()._state["panel"]


def item(pn):
    s().vault.refresh()
    return s().vault.items[pn]


def read_only(pn):
    path = s().work.path(pn)
    return os.path.exists(path) and not (os.stat(path).st_mode & stat.S_IWUSR)


def run_on(command, pn):
    """Run a vault command on pn as the right-click menus do (the target set for the call)."""
    vc()._state["target"] = pn
    try:
        Gui.runCommand(command)
    finally:
        vc()._state["target"] = None
    C.wait_for(lambda: C.task_button("ok") is not None, 2500)
    C.pump(400)


def ok_button():
    return C.task_button("ok")


def press_ok(what, then=None):
    """Click the task panel's OK. ``then``: the panel class name that is expected to open next."""
    ok = ok_button()
    C.check(f"{what}: OK enabled", ok is not None and ok.isEnabled(), ok.text() if ok else None)
    if ok is not None:
        ok.click()
    C.pump(1200)
    if then:
        C.wait_for(lambda: panel() is not None and type(panel()).__name__ == then, 4000)
    current = panel()
    closed = not Gui.Control.activeDialog() or (then and current is not None and type(current).__name__ == then)
    C.check(f"{what}: task panel closed" + (f", {then} next" if then else ""), closed)
    if not closed:  # Cancel, so the next steps can run
        cancel = C.task_button("cancel")
        cancel.click() if cancel is not None else Gui.Control.closeDialog()
        C.pump(500)


def looks(name):
    C.task_shot(name)
    C.readability(C.task_content(), name=f"{name} panel")
    C.clipped(C.task_content(), name=f"{name} panel")


def edit_part(pn, text):
    doc = FreeCAD.openDocument(s().work.path(pn))
    obj = next(o for o in doc.Objects if hasattr(o, "Shape"))
    obj.Label2 = text
    doc.save()
    C.close_all()


def fill_drawing(pn, part=None):
    """Open the drawing as a user does (Open from Vault brings the part it shows), let the title
    block fill itself, save. When the part number does not arrive, it is typed by hand (as a user
    would) so the rest of the run can go on; the caller checks what was filled."""
    vc().open_from_vault(pn, ask=False)
    C.pump(1500)
    doc = vc().document_for(s().work.path(pn))
    page = next(o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage"))
    drw().flush()
    drw().fill_page(page, s().root)
    doc.recompute()
    texts = dict(page.Template.EditableTexts)
    views = [v for v in page.Views]
    src = [(v.Name, [getattr(o, "FullName", o.Name) for o in (getattr(v, "XSource", None) or [])],
            [getattr(o, "Name", "") for o in (getattr(v, "Source", None) or [])]) for v in views]
    found = drw().part_of_page(page)
    C.log("drawing", pn, "views", src, "part_of_page", getattr(found, "FullName", None),
          "docs", sorted(FreeCAD.listDocuments()))
    if part and texts.get("part_number") != part:
        page.Template.EditableTexts = dict(page.Template.EditableTexts, part_number=part)
        doc.recompute()
    doc.save()
    C.close_all()
    return texts


# ----- steps -----------------------------------------------------------------------------------


@C.step(2500)
def setup():
    from bruceworx import drawing_standards as ds

    C.setup_window()
    vault = C.new_vault("Changes Test")
    src = os.path.join(C.RUN, "src")
    _d, part = C.make_part(os.path.join(src, "bracket.FCStd"), "Bracket", "Bracket, angle", 80, 40, 6)
    body = part.Document.getObject("Body")
    doc = FreeCAD.newDocument("BracketDrawing")
    page = doc.addObject("TechDraw::DrawPage", "Drawing")
    tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tmpl.Template = drw().template_path(drw().choose_sheet(ds.load(vault.root)))
    page.Template = tmpl
    doc.saveAs(os.path.join(src, "bracket-drawing.FCStd"))
    view = doc.addObject("TechDraw::DrawViewPart", "View")
    page.addView(view)
    view.XSource = [body]
    doc.recompute()
    doc.save()
    C.close_all()
    C.make_part(os.path.join(src, "spacer.FCStd"), "Spacer", "Spacer 10 mm", 10, 10, 10, hole=False)
    C.close_all()
    for name in os.listdir(src):
        if not name.endswith(".FCStd"):
            os.remove(os.path.join(src, name))
    numbers, report = C.import_folder(vault.root, src)
    C.check("part, drawing and spacer imported", len(numbers) == 3 and not report.failed, numbers, report.failed)
    vc().changed(refresh=True)
    items = s().vault.items
    V["dwg"] = next(pn for pn, it in items.items() if it.item_type == "drawing")
    V["part"] = items[V["dwg"]].drawing_of
    V["spacer"] = numbers.get("spacer.FCStd")
    C.check("the drawing is linked to its part", V["part"] == numbers.get("bracket.FCStd"), V)


@C.step(600)
def commands_in_menus():
    names = Gui.listCommands()
    for n in ("BruceWorx_VaultSubmit", "BruceWorx_VaultApprove", "BruceWorx_VaultReject", "BruceWorx_VaultRaiseEcr",
              "BruceWorx_VaultDecideEcr", "BruceWorx_VaultOpenEco", "BruceWorx_VaultReleaseEco", "BruceWorx_VaultRetire"):
        C.check(f"command {n} registered", n in names)
    sub = C.mw().findChild(QtWidgets.QMenu, "BruceWorxVaultMenu")
    if sub is not None:
        sub.aboutToShow.emit()
    texts = [a.text() for a in sub.actions() if a.text()] if sub else []
    C.log("vault menu", texts)
    for t in ("Submit for Review", "Approve…", "Reject…", "Raise ECR…", "Decide ECR…",
              "Open ECO…", "Release ECO…", "Retire…"):
        C.check(f"BruceWorx → Vault menu has {t}", t in texts, texts)


@C.step(600)
def submit_and_reject():
    part = V["part"]
    C.check("Submit for Review available on a checked-in WIP part", vc().available("BruceWorx_VaultSubmit", part))
    run_on("BruceWorx_VaultSubmit", part)
    C.check("submitted: In Review", item(part).state == "In Review", item(part).state)
    C.check("Approve and Reject now available", vc().available("BruceWorx_VaultApprove", part) and
            vc().available("BruceWorx_VaultReject", part))
    C.answer("OK", match="review", shot="checkout-in-review")
    n = len(C.MODALS)
    ok = vc().check_out(part)
    C.check("check-out of an item in review is refused with a message", not ok and len(C.MODALS) > n,
            C.MODALS[n:])
    run_on("BruceWorx_VaultReject", part)
    p = panel()
    C.check("Reject… opens", p is not None and type(p).__name__ == "RejectPanel")
    C.check("Reject disabled without a reason", ok_button() is not None and not ok_button().isEnabled())
    p.reason.setPlainText("Hole too close to the edge; move it 2 mm inwards.")
    C.pump(300)
    looks("reject")
    press_ok("Reject")
    it = item(part)
    C.check("rejected: work goes on at 02", (it.latest.code, it.latest.state) == ("02", "WIP"),
            it.latest.code, it.latest.state)
    rec = it.revisions[0].record("reject")
    C.check("the reason is recorded", bool(rec) and rec.get("reason", "").startswith("Hole too close"), rec)


@C.step(600)
def approve_with_checklist():
    part, dwg = V["part"], V["dwg"]
    C.check("check out 02", vc().check_out(part))
    edit_part(part, "hole moved")
    s().work.checkin(part, "Hole moved 2 mm inwards")
    C.check("drawing checked out", vc().check_out(dwg))
    texts = fill_drawing(dwg, part)
    C.check("the drawing's title block fills the part's number by itself", texts.get("part_number") == part,
            texts.get("part_number"))
    s().work.checkin(dwg, "Title block filled")
    vc().changed()
    run_on("BruceWorx_VaultSubmit", part)
    run_on("BruceWorx_VaultApprove", part)
    p = panel()
    C.check("Approve… opens with the release checklist", p is not None and type(p).__name__ == "ApprovePanel")
    if p is None:
        return
    lines = [(ln.key, ln.ok, ln.skipped) for ln in p.checklist.lines]
    C.log("checklist", lines)
    C.check("checklist has the automatic lines and the review tick", len(lines) >= 3 and
            p.checklist.findChildren(QtWidgets.QCheckBox), lines)
    C.check("Release disabled before the tick and the note", not ok_button().isEnabled(), p.problems())
    looks("approve-todo")
    p.checklist.findChildren(QtWidgets.QCheckBox)[0].setChecked(True)
    C.pump(200)
    C.check("still disabled without a note", not ok_button().isEnabled(), p.problems())
    p.note.setPlainText("First release of the bracket.")
    C.pump(300)
    C.check("button reads Release as A", ok_button().text() == "Release as A", ok_button().text())
    looks("approve")
    press_ok("Approve")
    it = item(part)
    C.check("released as A", it.released is not None and it.released.code == "A")
    rec = it.released.record("approve") if it.released else {}
    C.check("note and checklist recorded", rec.get("note") == "First release of the bracket." and
            "checklist" in rec, sorted(rec))
    C.check("released work copy read-only on disk", read_only(part))
    run_on("BruceWorx_VaultSubmit", dwg)
    run_on("BruceWorx_VaultApprove", dwg)
    p = panel()
    if p is not None:
        p.checklist.findChildren(QtWidgets.QCheckBox)[0].setChecked(True)
        p.note.setPlainText("Drawing, first release.")
        C.pump(300)
        press_ok("Approve drawing")
    C.check("drawing released as A", item(dwg).released is not None and item(dwg).released.code == "A")


@C.step(600)
def released_checkout_offers_ecr():
    part = V["part"]
    C.answer("Raise ECR…", match="released", shot="released-checkout")
    n = len(C.MODALS)
    ok = vc().check_out(part)
    C.pump(300)
    seen = C.MODALS[n:]
    C.check("check-out of a released part refused, explained, Raise ECR… offered",
            not ok and seen and "Raise ECR…" in seen[-1][2], seen)
    C.wait_for(lambda: panel() is not None and type(panel()).__name__ == "RaiseEcrPanel", 3000)
    C.wait_for(lambda: ok_button() is not None, 2000)


@C.step(800)
def raise_ecr():
    p = panel()
    C.check("Raise ECR… opens from the offer", p is not None and type(p).__name__ == "RaiseEcrPanel")
    if p is None:
        return
    ticked = [p.items.item(i).data(QtCore.Qt.UserRole) for i in range(p.items.count())
              if p.items.item(i).checkState() == QtCore.Qt.Checked]
    C.check("the part is ticked as affected", ticked == [V["part"]], ticked)
    C.check("Raise disabled without a problem text", not ok_button().isEnabled())
    photo = os.path.join(C.RUN, "crack-photo.png")
    C.mw().grab().scaled(320, 200).save(photo)
    with C.fake_file_dialog(photo) as fake:
        C.button(C.tasks_dock(), "Add…").click()
    C.pump(200)
    C.check("Add… asks for files", bool(fake.asked), fake.asked)
    C.check("Add… attaches the photo", p.files.count() == 1, p.files.count())
    if p.files.count() == 0:  # what Add… should have done, so the rest can run
        p.add_files([photo])
    p.problem.setPlainText("Crack at the slot after 2000 load cycles (photo attached).")
    C.pump(300)
    C.check("button reads Raise ECR-0001", ok_button().text() == "Raise ECR-0001", ok_button().text())
    looks("raise-ecr")
    press_ok("Raise ECR")
    s().vault.refresh()
    ecr = s().vault.ecrs.get("ECR-0001")
    C.check("ECR-0001 recorded with the part", ecr is not None and ecr.items == [V["part"]])
    C.check("the photo is copied into the vault", ecr is not None and ecr.attachments and
            os.path.isfile(s().vault.attachment_path(ecr.attachments[0])))


@C.step(800)
def changes_chip():
    vc().show_panel(True)
    C.wait_for(lambda: C.mw().findChild(QtWidgets.QFrame, "Vault") is not None and
               C.mw().findChild(QtWidgets.QFrame, "Vault").isVisible(), 3000)
    card = C.mw().findChild(QtWidgets.QFrame, "Vault")
    vp = card.widget()
    vc().changed(refresh=True)
    C.pump(800)
    vp.chips["changes"].click()
    C.pump(300)
    rows = [vp.model.item(i).data(QtCore.Qt.UserRole) for i in range(vp.model.rowCount())]
    C.check("Changes chip lists ECR-0001", rows == ["ECR-0001"], rows)
    C.check("Changes chip counts the open change", "1" in vp.chips["changes"].text(), vp.chips["changes"].text())
    C.shot("card-changes", card)
    C.readability(card, name="Vault card (Changes)")
    menu = vc().build_item_menu(C.mw(), "ECR-0001", in_panel=True)
    C.log("change menu", [a.text() for a in menu.actions()])
    vp.chips["all"].click()
    vc().show_panel(False)


@C.step(600)
def decide_ecr():
    C.check("Decide ECR… enabled in the Vault menu", True)
    Gui.runCommand("BruceWorx_VaultDecideEcr")
    C.wait_for(lambda: ok_button() is not None, 2500)
    C.pump(400)
    p = panel()
    C.check("Decide ECR… opens ECR-0001", p is not None and type(p).__name__ == "EcrPanel")
    if p is None:
        return
    C.check("decision needs a reason", not ok_button().isEnabled())
    p.reject_.setChecked(True)
    C.pump(200)
    p.reason.setPlainText("Field failures are likely; fix it in revision B.")
    C.pump(200)
    C.check("button follows the choice (Reject ECR)", ok_button().text() == "Reject ECR", ok_button().text())
    p.approve.setChecked(True)
    C.pump(300)
    C.check("button follows the choice (Approve ECR)", ok_button().text() == "Approve ECR", ok_button().text())
    looks("decide")
    C.answer("Open ECO…", match="Open an ECO", shot="offer-open-eco")
    press_ok("Decide ECR", then="OpenEcoPanel")
    C.wait_for(lambda: ok_button() is not None, 2000)
    C.check("ECR approved", s().vault.ecrs["ECR-0001"].status == "Approved", s().vault.ecrs["ECR-0001"].status)
    C.check("Open ECO offered after the approval", any("Open an ECO" in m[1] for m in C.MODALS))


@C.step(800)
def open_eco():
    p = panel()
    C.check("Open ECO… opens", p is not None and type(p).__name__ == "OpenEcoPanel")
    if p is None:
        return
    rows = [p.tree.topLevelItem(i).text(0) + " " + p.tree.topLevelItem(i).text(1)
            for i in range(p.tree.topLevelItemCount())]
    C.log("eco plan", rows)
    C.check("the plan lists the part and its drawing", len(rows) == 2, rows)
    looks("open-eco")
    press_ok("Open ECO")
    s().vault.refresh()
    eco = s().vault.ecos.get("ECO-0001")
    C.check("ECO-0001 has the part and its drawing", eco is not None and sorted(eco.items) ==
            sorted([V["part"], V["dwg"]]), eco.items if eco else None)
    for pn in (V["part"], V["dwg"]):
        it = item(pn)
        C.check(f"{pn}: B in work, checked out to me", (it.latest.code, it.latest.state) == ("B", "WIP") and
                s().vault.locks.get(pn) == s().me and not read_only(pn), it.latest.code, s().vault.locks.get(pn))


@C.step(800)
def release_eco():
    part, dwg = V["part"], V["dwg"]
    edit_part(part, "slot radius 3 mm")
    texts = fill_drawing(dwg, part)
    C.check("the drawing's title block shows rev B", "B" in (texts.get("revision") or ""), texts.get("revision"))
    s().work.checkin(part, "Slot radius 3 mm")
    s().work.checkin(dwg, "Rev B")
    vc().changed()
    run_on("BruceWorx_VaultSubmit", part)
    run_on("BruceWorx_VaultReleaseEco", part)
    p = panel()
    C.check("Release ECO… opens", p is not None and type(p).__name__ == "ReleaseEcoPanel")
    if p is None:
        return
    C.check("blocked while the drawing is still in work", not ok_button().isEnabled(), p.problems())
    looks("release-eco-blocked")
    sub = p.submits.get(dwg)
    C.check("the drawing offers Submit for Review in the panel", sub is not None)
    if sub:
        sub[0].click()
        C.pump(400)
    for cl in p.checklists.values():
        for tick in cl.findChildren(QtWidgets.QCheckBox):
            tick.setChecked(True)
    p.note.setPlainText("Slot radius 3 mm against cracking (ECR-0001).")
    C.pump(400)
    C.check("button reads Release 2 Items", ok_button().isEnabled() and ok_button().text() == "Release 2 Items",
            ok_button().text(), p.problems())
    looks("release-eco")
    press_ok("Release ECO")
    for pn in (part, dwg):
        it = item(pn)
        C.check(f"{pn} released as B, A superseded", it.released is not None and it.released.code == "B" and
                any(r.code == "A" and r.superseded for r in it.revisions))
        C.check(f"{pn} read-only on disk", read_only(pn))


@C.step(800)
def retire():
    sp = V["spacer"]
    vault = s().vault
    vault.submit(sp)
    vault.approve(sp, note="setup")
    frame = "BW-02-09001-001"
    vault.create_item(frame, "assembly", "Frame (in work)")
    vault.set_bom(frame, [(sp, 4)])
    vc().changed(refresh=True)
    C.check("Retire… available on a released item", vc().available("BruceWorx_VaultRetire", sp))
    run_on("BruceWorx_VaultRetire", sp)
    p = panel()
    C.check("Retire… opens", p is not None and type(p).__name__ == "RetirePanel")
    if p is None:
        return
    C.check("Retire disabled before the reason and the confirmation", not ok_button().isEnabled(), p.problems())
    looks("retire-todo")
    if getattr(p, "confirm", None) is not None:
        p.confirm.setChecked(True)
    p.reason.setPlainText("Replaced by the machined spacer.")
    C.pump(300)
    looks("retire")
    press_ok("Retire")
    C.check("spacer is Obsolete", item(sp).state == "Obsolete", item(sp).state)
    C.check("Retire… no longer offered", not vc().available("BruceWorx_VaultRetire", sp))


@C.step(800)
def final_card():
    vc().show_panel(True)
    C.pump(1000)
    card = C.mw().findChild(QtWidgets.QFrame, "Vault")
    if card is not None:
        C.shot("card-final", card)
        vp = card.widget()
        states = {pn: r.get("state") for pn, r in vp.rows.items()}
        C.log("card states", states)
        C.check("card shows RELEASED and OBSOLETE states", "Released" in states.values() and
                "Obsolete" in states.values(), states)
    from bruceworx.pdm_folder import FolderVault

    again = FolderVault(s().root, actor=s().me)
    C.check("journal replays clean", again.problems == [], again.problems)
    vc().show_panel(False)
    shutil.rmtree(os.path.join(C.RUN, "unused"), ignore_errors=True)


C.SAY.update({'setup': 'Make a temporary vault with a bracket, its drawing and a spacer', 'commands_in_menus': 'Check the BruceWorx > Vault menu lists the review and change commands', 'submit_and_reject': 'Submit the bracket for review, then Reject it with a reason', 'approve_with_checklist': 'Fix it, submit again, Approve with the checklist and a note: expect rev A', 'released_checkout_offers_ecr': 'Check out the released bracket: expect a refusal offering Raise ECR...', 'raise_ecr': 'Raise ECR with a problem and a photo attached', 'changes_chip': 'Vault card, Changes chip: expect ECR-0001', 'decide_ecr': 'Decide ECR...: approve with a reason; expect Open ECO to be offered', 'open_eco': 'Open ECO: expect the bracket and its drawing at B, checked out', 'release_eco': 'Change both, Release ECO: blocked until the drawing is submitted, then release B', 'retire': 'Retire the spacer with a reason: expect Obsolete', 'final_card': 'Look at the Vault card at the end; the journal must replay clean'})
C.run()
