"""Setting a vault up, as a user does it, in a temporary folder only: the Start page's vault switcher,
Create Vault (name and folder, the part-number builder, company properties, CSV template download
and load, Create), Import Files (offered after Create), My work on the Start page, Vault Settings
(add, rename, retire, the delete refusal, Save), Break Lock and Move Vault from Vault Settings,
Close Vault and Open Vault (a vault, and a plain folder). Runs in the GUI; quits itself.
"""
import csv
import os
import sys

sys.path.insert(0, os.environ.get("BW_TESTDIR") or os.path.dirname(os.path.abspath(__file__)))
import bwdocs as C  # noqa: E402

import FreeCAD  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtWidgets  # noqa: E402

V = {}


def wiz():
    import bruceworx_vaultwizard

    return bruceworx_vaultwizard


def vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def panel():
    return wiz()._state["panel"]


def issues_text(issue_list):
    return [w.text() for w in issue_list.findChildren(QtWidgets.QLabel) if w.text()] if issue_list.isVisible() else []


def vault_button(page):
    return page.vault_button.text().replace("▾", "").strip()


# ----- steps -----------------------------------------------------------------------------------


@C.step(2500)
def setup():
    C.setup_window()
    V["src"] = os.path.join(C.RUN, "src")
    C.sample_files(V["src"])
    V["parent"] = os.path.join(C.RUN, "vaults")
    os.makedirs(V["parent"], exist_ok=True)
    C.check("sample files made", len([n for n in os.listdir(V["src"]) if n.endswith(".FCStd")]) == 3,
            os.listdir(V["src"]))


@C.step(800)
def start_page_without_vault():
    page = C.start_page()
    C.check("Start page shown", page is not None)
    if page is None:
        return
    V["page"] = page
    C.check("vault switcher says no vault is open", vault_button(page) == "No vault open", vault_button(page))
    texts = [a.text() for a in page.vault_menu.actions() if a.text()]
    C.check("switcher menu: Open Vault and Create Vault only", texts == ["Open Vault…", "Create Vault…"], texts)
    C.check("My work hidden without a vault", not page.work_box.isVisible())
    C.shot("start-novault", page)
    C.readability(page, name="Start page (no vault)")
    C.clipped(page, name="Start page (no vault)")
    C.check("Import Files and Vault Settings disabled without a vault",
            C.command_enabled("BruceWorx_ImportIntoVault") is False and
            C.command_enabled("BruceWorx_VaultSettings") is False,
            C.command_enabled("BruceWorx_ImportIntoVault"), C.command_enabled("BruceWorx_VaultSettings"))


@C.step(600)
def create_vault_opens():
    page = V["page"]
    act = C.menu_action(page.vault_menu, "Create Vault")
    act.trigger()
    C.wait_for(lambda: wiz()._state["panel"] is not None and Gui.Control.activeDialog(), 3000)
    C.wait_for(lambda: C.task_button("ok") is not None, 3000)
    C.pump(800)
    p = panel()
    dock = C.tasks_dock()
    geo = dock.geometry() if dock is not None else None
    C.check("Create Vault form is on screen (task panel inside the window, tall enough)",
            geo is not None and geo.x() >= 0 and geo.height() > 300, geo.getRect() if geo else None)
    C.check("Create Vault opens in the task panel", p is not None and type(p).__name__ == "CreateVaultPanel",
            type(p).__name__ if p else None)
    if p is None:
        return
    ok = C.task_button("ok")
    C.check("OK button reads Create", ok is not None and ok.text() == "Create", ok.text() if ok else None)
    suggested = p.folder_edit.text()
    C.note("default folder suggested:", suggested)
    C.check("default folder follows the name (My Vault)", suggested.endswith(os.path.join("BruceWorx Vaults", "My Vault")),
            suggested)
    # The user picks where vaults go (Choose...): our temporary folder, never iCloud Drive.
    with C.fake_file_dialog(V["parent"]) as fake:
        C.button(C.tasks_dock(), "Choose…").click()
    C.check("Choose… asks for a folder", bool(fake.asked), fake.asked)
    C.check("picked folder + the name", p.folder_edit.text() == os.path.join(V["parent"], "My Vault"),
            p.folder_edit.text())
    p.name_edit.setText("Acme Products")
    C.pump(400)
    C.check("folder follows the new name", p.folder_edit.text() == os.path.join(V["parent"], "Acme Products"),
            p.folder_edit.text())
    C.task_shot("create-1-name")


@C.step(500)
def create_vault_errors():
    p = panel()
    p.name_edit.setText("")
    C.pump(400)
    C.task_button("ok").click()
    C.pump(400)
    errors = issues_text(p.name_issues)
    C.check("Create with no name stays open and says why", Gui.Control.activeDialog() and
            any("name" in e.lower() for e in errors), errors)
    C.task_shot("create-2-error")
    p.name_edit.setText("Acme Products")
    C.pump(400)
    C.check("the error goes when a name is typed", not any("Give the vault a name" in e for e in issues_text(p.name_issues)),
            issues_text(p.name_issues))


@C.step(500)
def part_number_builder():
    p = panel()
    b = p.builder
    C.check("builder shows a first number", b.number.startswith("BW-01-01001-001"), b.number)
    for key in ("type", "parent", "sub", "rev", "program"):
        seg = b.rev if key == "rev" else b.segments[key]
        C.click(seg)
        C.check(f"clicking the {key.upper()} segment shows its page", b.current == key and
                b.stack.currentWidget() is b.pages[key], b.current)
        if key == "rev":
            C.shot("pn-rev-page", b)
    # Programs: add AC, select it.
    plus = [t for t in b.pages["program"].findChildren(QtWidgets.QToolButton) if t.text() == "+"]
    plus[0].click()
    C.pump(200)
    t = p.programs.widget
    r = t.rowCount() - 1
    QtWidgets.QApplication.processEvents()
    t.closePersistentEditor(t.item(r, 0))
    t.item(r, 0).setText("ac")
    t.item(r, 1).setText("Acme line")
    t.setCurrentCell(r, 0)
    C.pump(400)
    C.check("a typed code becomes upper case", t.item(r, 0).text() == "AC", t.item(r, 0).text())
    C.check("selecting a program shows it in the number", b.number.startswith("AC-"), b.number)
    C.click(b.segments["type"])
    p.types.widget.setCurrentCell(1, 0)
    C.pump(300)
    C.check("selecting a document type shows it in the number", b.number.startswith("AC-02-"), b.number)
    C.click(b.segments["parent"])
    p.parent_spin.setValue(2001)
    C.pump(400)
    C.check("first parent number shows in the number", "-02001-" in b.number, b.number)
    C.shot("pn-builder", b)
    C.readability(b, name="part-number builder")
    C.clipped(b, name="part-number builder")
    # A bad code: an error in the numbering box.
    t.item(r, 0).setText("A C!")
    C.pump(500)
    errs = issues_text(p.numbering_issues)
    C.check("a bad program code shows an error", bool(errs), errs)
    t.item(r, 0).setText("AC")
    C.pump(500)
    C.check("fixed code clears the error", not [e for e in issues_text(p.numbering_issues) if "AC" in e or "code" in e.lower()],
            issues_text(p.numbering_issues))
    C.click(b.segments["program"])


@C.step(500)
def properties_box():
    p = panel()
    t = p.props.widget
    names = [t.item(r, 0).text() for r in range(t.rowCount())]
    C.check("suggested company properties listed", len(names) >= 3, names)
    if names:
        t.item(0, 0).setCheckState(QtCore.Qt.Checked)
    C.button(C.tasks_dock(), "Add Custom").click()
    C.pump(200)
    r = t.rowCount() - 1
    t.closePersistentEditor(t.item(r, 0))
    t.item(r, 0).setText("SupplierCode")
    t.item(r, p.props.doc_col).setText("Supplier's code")
    C.pump(400)
    fixed, custom = p.properties()
    V["ticked"] = [e.get("name") for e in fixed]
    C.check("custom property read", [e.get("name") for e in custom] == ["SupplierCode"], custom)
    C.check("no property errors", not issues_text(p.property_issues), issues_text(p.property_issues))


@C.step(500)
def csv_download_and_load():
    p = panel()
    path = os.path.join(C.RUN, "acme.csv")
    with C.fake_file_dialog(path):
        C.button(C.tasks_dock(), "Download CSV template…").click()
    C.pump(400)
    C.check("Download CSV template… writes the file", os.path.isfile(path))
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    C.log("csv header", header, "rows", len(rows))
    flat = [",".join(r) for r in rows]
    C.check("CSV has the programs, types and the custom property",
            any(r[:2] == ["program", "AC"] for r in rows) and any(r[:2] == ["document_type", "03"] for r in rows)
            and any("SupplierCode" in f for f in flat), flat[:8])
    C.check("CSV saved notice shown", any("Saved" in e for e in issues_text(p.numbering_issues)),
            issues_text(p.numbering_issues))
    # Edit it as in a spreadsheet: one more program, then Load CSV...
    row = [""] * len(header)
    row[header.index("table")], row[header.index("code")] = "program", "ZZ"
    if "description" in header:
        row[header.index("description")] = "Zed line"
    rows.append(row)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle, dialect="excel").writerows(rows)
    with C.fake_file_dialog(path):
        C.button(C.tasks_dock(), "Load CSV…").click()
    C.pump(500)
    codes = [c for c, _t, _r in p.programs.rows()]
    C.check("Load CSV… adds the new program", "ZZ" in codes and "AC" in codes, codes)
    C.check("Loaded notice shown", any("Loaded" in e for e in issues_text(p.numbering_issues)),
            issues_text(p.numbering_issues))
    custom = [e.get("name") for e in p.properties()[1]]
    C.check("custom property kept through the CSV", "SupplierCode" in custom, custom)
    # Load a broken file: nothing changes, an error says so.
    bad = os.path.join(C.RUN, "bad.csv")
    with open(bad, "w", encoding="utf-8") as handle:
        handle.write("table,code\nprogram,!!\n")
    with C.fake_file_dialog(bad):
        C.button(C.tasks_dock(), "Load CSV…").click()
    C.pump(500)
    C.check("a bad CSV is refused with a message", any("Nothing was loaded" in e or "error" in e.lower()
                                                       for e in issues_text(p.numbering_issues))
            and "ZZ" in [c for c, _t, _r in p.programs.rows()], issues_text(p.numbering_issues))
    p.csv_issues = []
    p.validate()


@C.step(800)
def create_vault_looks():
    p = panel()
    C.task_shot("create-3-filled")
    content = C.task_content()
    C.readability(content, name="Create Vault panel")
    C.clipped(content, name="Create Vault panel")
    errs = issues_text(p.name_issues) + issues_text(p.numbering_issues) + issues_text(p.property_issues)
    C.log("issues before Create", errs)


@C.step(500)
def create_vault():
    p = panel()
    if not C._inside_out(p.folder_edit.text()):
        C.check("Create only into the test folder (not clicked)", False, p.folder_edit.text())
        Gui.Control.closeDialog()
        return
    C.answer("Yes", match="Import existing files now", shot="create-offer-import")
    C.task_button("ok").click()
    C.pump(500)
    root = os.path.join(V["parent"], "Acme Products")
    V["root"] = root
    C.check("Create made the vault in the chosen folder", any(n.endswith(".bwvault") for n in os.listdir(root))
            if os.path.isdir(root) else False, os.listdir(root) if os.path.isdir(root) else root)
    vp = C.PREFS.GetString("VaultPath", "")
    C.check("the new vault is the open vault", os.path.realpath(vp).startswith(os.path.realpath(root)), vp)
    C.check("no vault went to iCloud Drive", "Mobile Documents" not in vp)
    from bruceworx import vault_setup as vs

    st = vs.load_settings(root)
    codes = [getattr(x, "code", x) for x in st.programs]
    C.check("vault has the programs from the panel and the CSV", {"BW", "AC", "ZZ"} <= set(codes), codes)
    names = [e.get("name") for e in st.properties]
    C.check("vault has the company properties", "SupplierCode" in names, names)
    V["p_created"] = p


@C.step(1500)
def import_offered():
    said = [m for m in C.MODALS if "Import existing files now" in m[1]]
    C.check("after Create, importing files is offered", bool(said), C.MODALS[-2:])
    p = panel()
    C.check("Yes opens Import Files", p is not None and type(p).__name__ == "ImportPanel",
            type(p).__name__ if p else None)
    if p is None or type(p).__name__ != "ImportPanel":
        return
    C.note("Import's starting folder:", p.folder_edit.text())
    ok = C.task_button("ok")
    C.check("Import disabled before files are found", ok is not None and not ok.isEnabled(), ok.text() if ok else None)
    p.folder_edit.setText(V["src"])
    C.button(C.tasks_dock(), "Find Files").click()
    C.wait_for(lambda: p.candidates, 10000)
    C.pump(500)
    found = [os.path.basename(c.path) for c in p.candidates]
    C.check("Find Files lists the 3 files and no FreeCAD backups (.FCBak)",
            sorted(found) == ["bracket.FCStd", "hinge.FCStd", "pin.FCStd"], found)
    for i, c in enumerate(p.candidates):  # a user would untick a backup row
        if not c.path.endswith(".FCStd"):
            p.files.topLevelItem(i).setCheckState(0, QtCore.Qt.Unchecked)
    C.pump(300)
    t = p.files
    rows = [(t.topLevelItem(i).text(0), t.topLevelItem(i).text(1)) for i in range(t.topLevelItemCount())]
    C.log("import rows", rows)
    C.check("each file gets a proposed number", all(r[1] for r in rows if r[0].endswith(".FCStd")), rows)
    kinds = {os.path.basename(c.path): c.kind for c in p.candidates}
    C.check("the assembly is recognised", kinds.get("hinge.FCStd") == "assembly", kinds)
    ok = C.task_button("ok")
    C.check("button reads Import 3 Files", ok is not None and ok.text() == "Import 3 Files" and ok.isEnabled(),
            ok.text() if ok else None)
    C.task_shot("import-1-found")
    C.readability(C.task_content(), name="Import Files panel")
    C.clipped(C.task_content(), name="Import Files panel")


@C.step(500)
def import_run():
    p = panel()
    if p is None or type(p).__name__ != "ImportPanel":
        C.check("import panel open", False)
        return
    C.task_button("ok").click()
    C.wait_for(lambda: p.report is not None, 30000)
    C.pump(500)
    C.check("import finished", p.report is not None)
    if p.report is None:
        return
    C.check("3 imported, none failed", len(p.report.imported) == 3 and not p.report.failed,
            p.report.imported, p.report.failed)
    V["pns"] = {os.path.basename(path): pn for path, pn in p.report.imported}
    ok = C.task_button("ok")
    C.check("button reads Close after the import", ok is not None and ok.text() == "Close", ok.text() if ok else None)
    C.task_shot("import-2-done")
    C.readability(C.task_content(), name="Import result")
    ok.click()
    C.pump(500)
    C.check("Close closes the panel", not Gui.Control.activeDialog())
    C.check("the originals are untouched", sorted(n for n in os.listdir(V["src"]) if n.endswith(".FCStd")) ==
            ["bracket.FCStd", "hinge.FCStd", "pin.FCStd"], os.listdir(V["src"]))


@C.step(1200)
def start_page_with_vault():
    vc().changed(refresh=True)
    s = vc().session()
    C.check("vault client opened the vault", s is not None and len(s.vault.items) == 3,
            len(s.vault.items) if s else None)
    pin = V["pns"].get("pin.FCStd")
    C.check("check out the pin (for My work)", vc().check_out(pin))
    page = C.start_page()
    C.wait_for(lambda: page.work_box.isVisible(), 6000)
    C.pump(500)
    C.check("vault switcher shows the vault's name", vault_button(page) == "Acme Products", vault_button(page))
    texts = [a.text() for a in page.vault_menu.actions() if a.text()]
    C.check("switcher menu with a vault", texts == ["Open Vault…", "Create Vault…", "Import Files…",
                                                  "Vault Settings…", "Close Vault"], texts)
    t = page.work_table
    rows = [(t.topLevelItem(i).text(0), t.topLevelItem(i).text(2)) for i in range(t.topLevelItemCount())]
    C.log("my work rows", rows)
    C.check("My work lists the checked-out pin", any(pin in r[0] for r in rows) and
            any("CHECKED OUT" in r[0].upper() for r in rows), rows)
    C.shot("start-vault", page)
    C.readability(page, name="Start page (vault)")
    C.clipped(page, name="Start page (vault)")
    V["work_rows"] = rows


@C.step(500)
def my_work_opens_row():
    page = V["page"] = C.start_page()
    t = page.work_table
    pin = V["pns"].get("pin.FCStd")
    item = next((t.topLevelItem(i) for i in range(t.topLevelItemCount()) if pin in t.topLevelItem(i).text(0)), None)
    if item is None:
        C.check("My work row to open", False)
        return
    t.itemActivated.emit(item, 0) if hasattr(t, "itemActivated") else None
    t._open(item)
    C.wait_for(lambda: any(os.path.basename(d.FileName) == pin + ".FCStd" for d in FreeCAD.listDocuments().values()),
               5000)
    docs = [os.path.basename(d.FileName) for d in FreeCAD.listDocuments().values()]
    C.check("a My work row opens its file", pin + ".FCStd" in docs, docs)
    C.close_all()


@C.step(800)
def vault_settings_opens():
    C.check("Vault Settings enabled with a vault", C.command_enabled("BruceWorx_VaultSettings") is True)
    Gui.runCommand("BruceWorx_VaultSettings")
    C.wait_for(lambda: panel() is not None and type(panel()).__name__ == "SettingsPanel", 3000)
    C.wait_for(lambda: C.task_button("ok") is not None, 3000)
    p = panel()
    C.check("Vault Settings opens", p is not None and type(p).__name__ == "SettingsPanel")
    if p is None:
        return
    ok = C.task_button("ok")
    C.check("OK button reads Save", ok is not None and ok.text() == "Save", ok.text() if ok else None)
    rows = p.programs.rows()
    C.check("programs listed with Retired column", [c for c, _t, _r in rows] == ["BW", "AC", "ZZ"] and
            p.programs.widget.columnCount() == 3, rows)
    item = p.programs.widget.item(1, 0)
    C.check("an existing code cannot be edited", not (item.flags() & QtCore.Qt.ItemIsEditable))
    titles = [w.windowTitle() for w in C.tasks_dock().findChildren(QtWidgets.QWidget) if w.windowTitle()]
    C.check("boxes: settings, numbering, properties, drawing standards",
            all(x in titles for x in ("Vault Settings", "Part numbering", "Company part properties", "Drawing standards")),
            sorted(set(titles))[:10])
    C.task_shot("settings-1")
    # The Drawing standards box, scrolled into view as a user would see it.
    box = p.drawing.widget
    area = next((a for a in C.tasks_dock().findChildren(QtWidgets.QScrollArea) if a.isVisible()), None)
    if area is not None:
        area.ensureWidgetVisible(box, 0, 0)
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        C.pump(400)
    C.shot("settings-drawing-standards", C.tasks_dock())
    fields = [w for w in box.findChildren(QtWidgets.QWidget)
              if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QComboBox, QtWidgets.QDoubleSpinBox,
                                QtWidgets.QSpinBox)) and w.isVisibleTo(box)]
    rects = [(w, QtCore.QRect(w.mapTo(box, QtCore.QPoint(0, 0)), w.size())) for w in fields]
    overlaps = [(a.objectName() or type(a).__name__, b.objectName() or type(b).__name__)
                for i, (a, ra) in enumerate(rects) for b, rb in rects[i + 1:]
                if ra.intersects(rb) and not (a.isAncestorOf(b) or b.isAncestorOf(a))]
    C.check("Drawing standards box: fields laid out without overlapping", not overlaps,
            len(overlaps), "overlaps; box height", box.height(), "hint", box.sizeHint().height())
    C.readability(C.task_content(), name="Vault Settings panel")
    C.clipped(C.task_content(), name="Vault Settings panel")


@C.step(500)
def vault_settings_edit():
    p = panel()
    t = p.programs.widget
    # Delete an existing row: refused with a hint.
    t.setCurrentCell(1, 0)
    C.answer("OK", match="cannot be deleted", shot="settings-delete-refused")
    minus = [b for b in p.builder.pages["program"].findChildren(QtWidgets.QToolButton) if b.text() == "−"]
    minus[0].click()
    C.pump(400)
    C.check("removing a row the vault has is refused, Retire suggested",
            any("cannot be deleted" in m[1] for m in C.MODALS) and t.rowCount() == 3, t.rowCount())
    # Rename: AC's description. Retire: ZZ. Add: NX.
    t.item(1, 1).setText("Acme Corporation")
    t.item(2, 2).setCheckState(QtCore.Qt.Checked)
    plus = [b for b in p.builder.pages["program"].findChildren(QtWidgets.QToolButton) if b.text() == "+"]
    plus[0].click()
    C.pump(200)
    r = t.rowCount() - 1
    t.closePersistentEditor(t.item(r, 0))
    t.item(r, 0).setText("NX")
    t.item(r, 1).setText("Next generation")
    # Document type: rename 03.
    tt = p.types.widget
    for i in range(tt.rowCount()):
        if tt.item(i, 0).text() == "03":
            tt.item(i, 1).setText("Drawing sheet")
    # Retire the custom property.
    pt = p.props.widget
    for i in range(pt.rowCount()):
        if pt.item(i, 0).text() == "SupplierCode":
            pt.item(i, p.props.retired_col).setCheckState(QtCore.Qt.Checked)
    C.pump(500)
    errs = issues_text(p.numbering_issues) + issues_text(p.property_issues)
    C.log("settings issues", errs)
    C.check("edits leave no errors", not [e for e in errs if "error" in e.lower() or "cannot" in e.lower()], errs)
    C.task_shot("settings-2-edited")
    C.task_button("ok").click()
    C.pump(600)
    C.check("Save closes the panel", not Gui.Control.activeDialog())
    from bruceworx import vault_setup as vs

    st = vs.load_settings(V["root"])
    progs = {getattr(x, "code", x): getattr(x, "description", "") for x in st.programs}
    C.check("program added (NX)", "NX" in progs, progs)
    C.check("program renamed (AC)", progs.get("AC") == "Acme Corporation", progs.get("AC"))
    C.check("program retired (ZZ)", "ZZ" in st.retired.get("program", set()), st.retired)
    C.check("document type renamed (03)", st.document_types.get("03") == "Drawing sheet", st.document_types)
    C.check("property retired (SupplierCode)", "SupplierCode" in st.retired.get("property", set()), st.retired)
    progs_offered = [c for c, _d in wiz()._vault_programs(vs, V["root"])]
    C.check("a retired program is no longer offered for import", "ZZ" not in progs_offered and "NX" in progs_offered,
            progs_offered)


@C.step(600)
def break_lock_from_settings():
    from bruceworx.pdm_folder import FolderVault

    s = vc().session()
    br = V["pns"].get("bracket.FCStd")
    dana = FolderVault(s.vault.ref_path, actor="dana@imac")
    dana.checkout(br)
    vc().changed(refresh=True)
    Gui.runCommand("BruceWorx_VaultSettings")
    C.wait_for(lambda: panel() is not None and type(panel()).__name__ == "SettingsPanel", 3000)
    C.answer("OK", match="Release the lock on", item=br, shot="breaklock-pick")
    C.answer("Break Lock", match="Break the lock", shot="breaklock-confirm")
    C.button(C.tasks_dock(), "Break Lock…").click()
    C.pump(800)
    s.refresh()
    C.check("Break Lock… lists both locks to pick from", any(m[0] == "Break Lock" and len(m[2]) == 2 for m in C.MODALS),
            [m[:3] for m in C.MODALS if m[0] == "Break Lock"])
    C.check("Break Lock… asks before breaking", any("Break the lock" in m[1] for m in C.MODALS))
    C.check("the bracket's lock is broken", br not in s.vault.locks, dict(s.vault.locks))
    C.check("my own lock is left alone", V["pns"].get("pin.FCStd") in s.vault.locks, dict(s.vault.locks))
    events = [e for e in s.vault._read_events() if e.op == "undo_checkout" and e.args.get("force")]
    C.check("the break is in the journal", bool(events), [getattr(e, "op", "") for e in s.vault._read_events()][-4:])


@C.step(800)
def move_vault_from_settings():
    if not Gui.Control.activeDialog():
        Gui.runCommand("BruceWorx_VaultSettings")
        C.wait_for(lambda: Gui.Control.activeDialog(), 3000)
    old_root = V["root"]
    dest = os.path.join(C.RUN, "moved")
    os.makedirs(dest, exist_ok=True)
    pin = V["pns"].get("pin.FCStd")
    vc().open_from_vault(pin, ask=False)
    C.pump(500)
    C.answer("Move", match="Move Acme Products", shot="move-ask")
    C.answer("Keep It", match="to the Trash")
    C.answer("OK", match="moved to")
    with C.fake_file_dialog(dest):
        C.button(C.tasks_dock(), "Move Vault…").click()
        C.wait_for(lambda: any("moved to" in m[1] for m in C.MODALS), 20000)
    C.pump(800)
    new_root = os.path.join(dest, "Acme Products")
    vp = C.PREFS.GetString("VaultPath", "")
    C.check("Move Vault… copies the vault and switches to it", os.path.realpath(vp).startswith(os.path.realpath(new_root)),
            vp)
    C.check("the old copy is kept (Keep It)", os.path.isdir(old_root))
    C.check("Vault Settings closed for the move", not Gui.Control.activeDialog())
    s = vc().session()
    C.check("vault client follows the move", s is not None and os.path.realpath(s.root) == os.path.realpath(new_root),
            s.root if s else None)
    docs = [d.FileName for d in FreeCAD.listDocuments().values()]
    C.check("the open vault file reopens from the new place", any(p.startswith(new_root) for p in docs) and
            not any(p.startswith(old_root) for p in docs), docs)
    V["root"] = new_root
    C.close_all()


@C.step(800)
def close_and_open_vault():
    page = C.start_page()
    C.menu_action(page.vault_menu, "Close Vault").trigger()
    C.pump(800)
    page.refresh()
    C.pump(300)
    C.check("Close Vault: no vault open", C.PREFS.GetString("VaultPath", "") == "" and
            vault_button(page) == "No vault open", vault_button(page))
    with C.fake_file_dialog(V["root"]):
        C.menu_action(page.vault_menu, "Open Vault").trigger()
    C.pump(800)
    page.refresh()
    C.pump(500)
    C.check("Open Vault… opens the picked vault", os.path.realpath(C.PREFS.GetString("VaultPath", "")) ==
            os.path.realpath(V["root"]) and vault_button(page) == "Acme Products", vault_button(page))


@C.step(600)
def open_plain_folder():
    page = C.start_page()
    plain = os.path.join(C.RUN, "plain")
    os.makedirs(plain, exist_ok=True)
    with C.fake_file_dialog(plain):
        C.menu_action(page.vault_menu, "Open Vault").trigger()
    C.wait_for(lambda: panel() is not None, 3000)
    p = panel()
    C.check("Open Vault… on a plain folder offers Create Vault on it",
            p is not None and type(p).__name__ == "CreateVaultPanel" and p.folder_edit.text() == plain,
            p.folder_edit.text() if p else None)
    hints = [w.text() for w in C.tasks_dock().findChildren(QtWidgets.QLabel) if "not a vault yet" in w.text()]
    C.check("it says the folder is not a vault yet", bool(hints))
    C.task_shot("create-on-plain-folder")
    cancel = C.task_button("cancel")
    C.check("the Create Vault panel is on screen with its buttons", cancel is not None)
    cancel.click() if cancel is not None else Gui.Control.closeDialog()
    C.pump(500)
    C.check("Cancel closes it and keeps the open vault", not Gui.Control.activeDialog() and
            os.path.realpath(C.PREFS.GetString("VaultPath", "")) == os.path.realpath(V["root"]))
    C.check("nothing was written into the plain folder", os.listdir(plain) == [], os.listdir(plain))


C.SAY.update({'setup': 'Make sample part and assembly files in the temporary folder', 'start_page_without_vault': "Start page with no vault: expect 'No vault open' and Open/Create in its menu", 'create_vault_opens': 'Create Vault from the Start page; pick the temporary folder with Choose...', 'create_vault_errors': 'Empty the name and press Create: expect it to stay open with an error', 'part_number_builder': 'Click each part of the number; add program AC; set the first parent number', 'properties_box': 'Tick a suggested property and add a custom one', 'csv_download_and_load': 'Download the CSV template, add a program in it, load it back; then a bad CSV', 'create_vault_looks': 'Look at the filled Create Vault panel', 'create_vault': 'Press Create: expect the vault in the temporary folder and an offer to import files', 'import_offered': 'Answer Yes: Import Files opens; Find Files in the sample folder', 'import_run': 'Press Import 3 Files, then Close', 'start_page_with_vault': 'Check out the pin: Start page shows the vault and My work lists the pin', 'my_work_opens_row': 'Open the pin from the My work table', 'vault_settings_opens': 'Open Vault Settings: programs, types, properties and drawing standards', 'vault_settings_edit': 'Try to delete a program (refused), rename one, retire one, add one; Save', 'break_lock_from_settings': 'Another user locks the bracket; Break Lock... from Vault Settings', 'move_vault_from_settings': 'Move Vault... to another temporary folder; keep the old copy', 'close_and_open_vault': 'Close Vault on the Start page, then Open Vault... again', 'open_plain_folder': 'Open Vault... on an empty folder: expect Create Vault on it; Cancel'})
C.run()
