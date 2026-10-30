# SPDX-License-Identifier: LGPL-2.1-or-later
"""Insert Board… command (ECD-02): a KiCad board into the active assembly.

Design > Solid > Assemble (the group's More list) and the BruceWorx menu.
Picks a ``.kicad_pcb``, then :func:`bruceworx_board.insert_board` does the
work. Target: the active assembly; else the active document's only
assembly; else a new document named by the board.

KiCad vault items (ECD-05, bruceworx.ecad_vault): an electrical part whose KiCad project lives
in the vault (Standard Libraries) opens two ways:

* Open from Vault (double-click in the Data panel) shows the board in 3D: the item's model,
  made from the project's .kicad_pcb (:func:`open_board_item`; remade when the board, the
  item's revision or values change).
* Open 3D (light) (vault right-click menus): the same part as the board and its components only,
  no copper or print layers, for large assemblies (a model of its own, kept beside the full one).
* Open in KiCad (vault right-click menus; double-click on the Schematic, PCB or Project entry
  under "Electrical design" in the 3D model's Browser) starts KiCad on the project in the
  item's folder, after offering a check-out (:func:`open_in_kicad`). Without one the KiCad
  files are read-only; check the edits in with Check In... as for any vault file.

Import KiCad Project as Standard Part... (BruceWorx menu) makes the item in the Standard
Libraries vault beside the open vault (or the open vault, when that is Standard Libraries).

Off switch: Preferences > BruceWorx > "KiCad boards"
(``BaseApp/Preferences/BruceWorx/KiCadBoards``).
"""

import os

import FreeCAD as App

import bruceworx_board as bb

COMMAND = "BruceWorx_InsertBoard"
IMPORT_COMMAND = "BruceWorx_ImportKiCadProject"
OPEN_KICAD_COMMAND = "BruceWorx_VaultOpenKiCad"
OPEN_LIGHT_COMMAND = "BruceWorx_VaultOpenBoardLight"
KICAD_ICON = "BruceWorx_Vault_kicad_accent"  # the vault client's icon naming (kind kicad, role accent)
KICAD_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24">'
             '<rect x="6" y="6" width="12" height="12" rx="1.6" fill="none" stroke="#0A84FF" stroke-width="1.9"/>'
             '<rect x="9.5" y="9.5" width="5" height="5" rx="0.6" fill="#0A84FF"/>'
             '<path d="M9 2.5V6M12 2.5V6M15 2.5V6M9 18v3.5M12 18v3.5M15 18v3.5M2.5 9H6M2.5 12H6M2.5 15H6'
             'M18 9h3.5M18 12h3.5M18 15h3.5" stroke="#0A84FF" stroke-width="1.6" stroke-linecap="round"/></svg>')
LAST_PROJECT_DIR_KEY = "KiCadLastProjectDir"
LAST_DIR_KEY = "KiCadLastDir"
_state = {"installed": False}


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui

        QtWidgets = QtGui
    return QtCore, QtGui, QtWidgets


def target_assembly(doc=None):
    """The assembly to insert into: the active one, else the document's only one."""
    try:
        import UtilsAssembly

        active = UtilsAssembly.activeAssembly()
        if active is not None:
            return active
    except Exception:
        pass
    doc = doc or App.ActiveDocument
    if doc is None:
        return None
    found = [o for o in doc.Objects if o.isDerivedFrom("Assembly::AssemblyObject")]
    return found[0] if len(found) == 1 else None


def summary(report):
    """One line for the status bar and Report view."""
    parts = ", ".join(f"{name} {grams:.2f} g" for name, grams in sorted(report["by_material"].items()))
    text = (f"Board {report['width']:g} x {report['height']:g} mm, {report['total_g']:.2f} g ({parts}); "
            f"{report['frames']} coordinate systems")
    if report.get("missing_models"):
        text += f"; {len(report['missing_models'])} 3D models not found"
    return text


class _InsertBoardCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(bb.HERE, COMMAND + ".svg"),
            "MenuText": "Insert Board…",
            "ToolTip": (
                "Insert a KiCad board (.kicad_pcb) as one part: exported to STEP by KiCad, with FR-4, "
                "copper and component materials, its mass, and coordinate systems at the board origin "
                "and at each mounting hole for joints."
            ),
        }

    def IsActive(self):
        return bb.enabled()

    def Activated(self):
        import FreeCADGui as Gui

        _QtCore, _QtGui, QtWidgets = _qt()
        window = Gui.getMainWindow()
        prefs = App.ParamGet(bb.PREFS_PATH)
        start = prefs.GetString(LAST_DIR_KEY, os.path.expanduser("~"))
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window, "Insert Board", start, "KiCad boards (*.kicad_pcb)")
        if not path:
            return
        prefs.SetString(LAST_DIR_KEY, os.path.dirname(path))
        assembly = target_assembly()
        light = ask_detail(assembly)
        if light is None:
            return
        run(path, assembly, light=light)


def ask_detail(assembly):
    """Full or light model for Insert Board: True (light), False (full) or None (cancel). The
    default is light in an assembly of bruceworx_board.LIGHT_ABOVE parts or more."""
    import FreeCADGui as Gui

    _QtCore, _QtGui, QtWidgets = _qt()
    count = bb.assembly_part_count(assembly) if assembly is not None else 0
    light_default = bb.default_light(assembly)
    box = QtWidgets.QMessageBox(Gui.getMainWindow())
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle("Insert Board")
    box.setText("Insert the full board or the light model?")
    box.setInformativeText(
        "Full: copper, pads, solder mask and silkscreen, as KiCad shows it. Light: the board and its components "
        "only, many times fewer faces, for large assemblies."
        + (f"\n\nThis assembly has {count} parts, so light is the default." if light_default else ""))
    full = box.addButton("Full Model", QtWidgets.QMessageBox.AcceptRole)
    light = box.addButton("Light Model (No Copper Detail)", QtWidgets.QMessageBox.AcceptRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(light if light_default else full)
    box.exec()
    if box.clickedButton() is light:
        return True
    return False if box.clickedButton() is full else None


def run(path, assembly=None, light=False):
    """Insert ``path`` (the light model with ``light``) with a wait cursor; errors go to a message box."""
    import FreeCADGui as Gui

    _QtCore, QtGui, QtWidgets = _qt()
    assembly = assembly or target_assembly()
    QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(_QtCore.Qt.WaitCursor))
    try:
        options = bb.bridge().light_options() if light else None
        part = bb.insert_board(path, assembly=assembly, options=options)
    except Exception as exc:
        QtWidgets.QApplication.restoreOverrideCursor()
        App.Console.PrintError(f"Insert Board: {exc}\n")
        QtWidgets.QMessageBox.warning(Gui.getMainWindow(), "Insert Board", str(exc))
        return None
    QtWidgets.QApplication.restoreOverrideCursor()
    text = summary(bb.last_report)
    App.Console.PrintMessage(f"Insert Board: {part.Label}: {text}\n")
    try:
        Gui.getMainWindow().statusBar().showMessage(f"{part.Label}: {text}", 15000)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(part)
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return part


# ---------------------------------------------------------------------------
# KiCad vault items (ECD-05).


def _vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def _ev():
    return bb._ecad()


def is_kicad_item(s, pn):
    try:
        return s is not None and bool(pn) and _ev().is_ecad(s.vault, pn)
    except Exception:
        return False


def _work_copy(s, pn, title):
    """The item's work folder, fetched read-only (Get Latest) when there is none; "" on failure."""
    folder = s.work.path(pn)
    if not os.path.isdir(folder):
        ok, _result = _vc()._run(title, s.work.get_latest, pn)
        if not ok:
            return ""
    return folder


def _status_message(text, ms=12000):
    try:
        import FreeCADGui as Gui

        Gui.getMainWindow().statusBar().showMessage(text, ms)
    except Exception:
        pass
    App.Console.PrintMessage(text + "\n")


def open_board_item(pn=None, light=False):
    """Open from Vault for a KiCad item: its 3D model, made from the board in its work folder
    when there is none or the board, the revision or the item's values changed. ``light``: the
    light model (board and components), a document of its own."""
    import FreeCADGui as Gui

    vc = _vc()
    ev = _ev()
    s = vc.session()
    pn = pn or vc.current_target()
    if not is_kicad_item(s, pn):
        return False
    folder = _work_copy(s, pn, "Open Board")
    if not folder:
        return False
    values = bb.item_values(s.vault, pn, folder)
    path = ev.derived_path(folder, pn, light)
    try:
        stamp = ev.derived_stamp(folder, dict(values, recipe=bb.ITEM_RECIPE), light)
    except Exception as exc:
        vc._message("Open Board", f"{pn}: {exc}", "warning")
        return False
    fresh = ev.derived_fresh(folder, pn, stamp, light)
    doc = vc.document_for(path)
    if doc is not None and fresh:
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
        return True
    if doc is not None:
        App.closeDocument(doc.Name)
    if fresh:
        vc._load(path)
    else:
        _QtCore, QtGui, QtWidgets = _qt()
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(_QtCore.Qt.WaitCursor))
        try:
            doc = bb.build_item_model(folder, pn, values, path, light=light)
            ev.record_derived(folder, pn, stamp, light)
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            App.Console.PrintError(f"Open Board: {pn}: {exc}\n")
            vc._message("Open Board", f"{pn}: the board could not be shown in 3D: {exc}", "warning")
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        vc._set_unmodified(doc)
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    _status_message(f"{pn}: the board in 3D{' (light: board and components)' if light else ''}, made from "
                    f"{os.path.basename(ev.find_project(folder).pcb)}. Open in KiCad (right-click) edits it.")
    return True


def _ask_checkout(vc, s, pn, st, title):
    """Check out (True), open read-only (False), or cancel (None)."""
    from PySide import QtWidgets

    box = QtWidgets.QMessageBox(vc._window())
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(f"Check out {st.display} to edit it in KiCad?")
    box.setInformativeText("Opened without a check-out, the KiCad files are read-only. After editing, "
                           "check the item in from the vault menu.")
    out = box.addButton("Check Out and Open", QtWidgets.QMessageBox.AcceptRole)
    ro = box.addButton("Open Read-Only", QtWidgets.QMessageBox.NoRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(out)
    box.exec()
    if box.clickedButton() is out:
        return True
    return False if box.clickedButton() is ro else None


def open_in_kicad(pn=None, which="project", ask=True):
    """Start KiCad on the KiCad item's project (or its schematic or board) in the vault's work
    folder, offering a check-out first when the item could be checked out."""
    vc = _vc()
    ev = _ev()
    s = vc.session()
    pn = pn or vc.current_target()
    if s is None or not pn:
        return False
    if not is_kicad_item(s, pn):
        vc._message("Open in KiCad", f"{pn} is not a KiCad item.")
        return False
    folder = _work_copy(s, pn, "Open in KiCad")
    if not folder:
        return False
    st = s.status(pn)
    if ask and st.code in (vc.UP_TO_DATE, vc.NEWER) and st.state == vc.WIP and not s.vault.locks.get(pn):
        choice = _ask_checkout(vc, s, pn, st, "Open in KiCad")
        if choice is None:
            return False
        if choice and not vc.check_out(pn):
            return False
    try:
        target = ev.editor_file(ev.find_project(folder), which)
        ev.launch(target)
    except Exception as exc:
        vc._message("Open in KiCad", f"{pn}: {exc}", "warning")
        return False
    st = s.status(pn)
    _status_message(f"{st.display}: {os.path.basename(target)} opened in KiCad"
                    + ("" if st.writable else " (read-only: not checked out)"))
    return True


def open_kicad_file(path):
    """A KiCad file from the 3D model's Electrical design group: through the vault (check-out
    offer) when it is the open vault's item, else straight into KiCad."""
    ev = _ev()
    which = bb.KINDS.get(os.path.splitext(path)[1].lower(), "project")
    found = ev.item_for_path(path)
    try:
        vc = _vc()
        s = vc.session()
    except Exception:
        s = None
    if found is not None and s is not None and os.path.realpath(found[0]) == os.path.realpath(s.root) \
            and found[1] == s.me and found[2] in s.vault.model.items:
        return open_in_kicad(found[2], which)
    try:
        ev.launch(path)
    except Exception as exc:
        App.Console.PrintError(f"Open in KiCad: {exc}\n")
        return False
    return True


class ViewProviderKiCadDocument:
    """Schematic, PCB and Project under "Electrical design": double-click opens them in KiCad."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        kind = getattr(getattr(self, "Object", None), "KiCadKind", "board")
        name = "BruceWorx_InsertBoard.svg" if kind == "board" else "preferences-bruceworx.svg"
        return os.path.join(bb.HERE, name)

    def doubleClicked(self, vobj):
        open_kicad_file(bb.kicad_file_path(vobj.Object))
        return True

    def setupContextMenu(self, vobj, menu):
        _QtCore, QtGui, _QtWidgets = _qt()
        action = QtGui.QAction(QtGui.QIcon(os.path.join(bb.HERE, COMMAND + ".svg")), "Open in KiCad", menu)
        action.triggered.connect(lambda _c=False, o=vobj.Object: open_kicad_file(bb.kicad_file_path(o)))
        menu.addAction(action)

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def library_for(root):
    """The Standard Libraries vault for an open vault: itself when it is that vault, else its
    neighbour (vault_setup.library_beside), else ""."""
    from bruceworx import vault_setup as vs

    if not root:
        return ""
    if os.path.basename(os.path.abspath(root)) == vs.LIBRARY_DIR:
        return os.path.abspath(root)
    return vs.library_beside(root)


def run_import(library, source, comment, **values):
    """Import the KiCad project ``source`` into the vault ``library`` (ecad_vault.import_project),
    as the actor pinned for that vault. Returns the ImportResult."""
    vc = _vc()
    s = vc.session()
    if s is not None and os.path.realpath(s.root) == os.path.realpath(library):
        vault, work = s.vault, s.work
    else:
        other = vc.Session(library, App.ParamGet(vc.ACTORS_PATH))
        vault, work = other.vault, other.work
    try:
        return _ev().import_project(vault, source, comment, workspace=work, **values)
    finally:
        vc.changed(refresh=True)


def _import_form(window, project, title):
    """The import dialog: description, manufacturer, MPN, value and the (required) comment."""
    _QtCore, _QtGui, QtWidgets = _qt()
    dialog = QtWidgets.QDialog(window)
    dialog.setWindowTitle("Import KiCad Project as Standard Part")
    form = QtWidgets.QFormLayout(dialog)
    note = QtWidgets.QLabel(f"{os.path.basename(project.pro)} becomes a part in the Standard Libraries vault. "
                            "The project is copied; the original folder is not changed.")
    note.setWordWrap(True)
    form.addRow(note)
    fields = {}
    for key, label, text in (("description", "Description", title or project.stem), ("manufacturer", "Manufacturer", ""),
                             ("mpn", "Manufacturer P/N", ""), ("value", "Value", ""), ("comment", "Check-in comment", "")):
        edit = QtWidgets.QLineEdit(text)
        form.addRow(label, edit)
        fields[key] = edit
    fields["comment"].setPlaceholderText("Required")
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
    ok.setText("Import")
    ok.setEnabled(False)
    fields["comment"].textChanged.connect(lambda text: ok.setEnabled(bool(text.strip())))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    return dialog, fields


def import_project_dialog():
    """Import KiCad Project as Standard Part...: pick a .kicad_pro, fill in the part, import."""
    import FreeCADGui as Gui

    vc = _vc()
    ev = _ev()
    _QtCore, QtGui, QtWidgets = _qt()
    window = Gui.getMainWindow()
    s = vc.session()
    library = library_for(s.root) if s is not None else ""
    if not library:
        vc._message("Import KiCad Project", "Open a vault first: the part goes into the Standard Libraries "
                    "vault beside it (Vault/Standard Libraries).")
        return None
    prefs = App.ParamGet(bb.PREFS_PATH)
    start = prefs.GetString(LAST_PROJECT_DIR_KEY, os.path.expanduser("~"))
    path, _filter = QtWidgets.QFileDialog.getOpenFileName(window, "Import KiCad Project", start,
                                                          "KiCad projects (*.kicad_pro)")
    if not path:
        return None
    prefs.SetString(LAST_PROJECT_DIR_KEY, os.path.dirname(os.path.dirname(path)))
    try:
        project = ev.find_project(os.path.dirname(path), path)
    except Exception as exc:
        vc._message("Import KiCad Project", str(exc), "warning")
        return None
    dialog, fields = _import_form(window, project, ev.title_of(project))
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return None
    values = {k: fields[k].text().strip() for k in ("description", "manufacturer", "mpn", "value")}
    QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(_QtCore.Qt.WaitCursor))
    try:
        result = run_import(library, path, fields["comment"].text().strip(), **values)
    except Exception as exc:
        QtWidgets.QApplication.restoreOverrideCursor()
        vc._message("Import KiCad Project", f"Not imported: {exc}", "warning")
        return None
    QtWidgets.QApplication.restoreOverrideCursor()
    text = f"{result.part_number}: {result.description} is in Standard Libraries ({result.files} files)."
    if result.warnings:
        text += "\n\n" + "\n".join(result.warnings)
    same = s is not None and os.path.realpath(s.root) == os.path.realpath(library)
    question = "Open its 3D model?" if same else "Switch to Standard Libraries and open its 3D model?"
    if vc._ask("Import KiCad Project", text + "\n\n" + question, "Open", "Close"):
        if not same:
            vc.use_place(library)
        open_board_item(result.part_number)
    return result


def _active_kicad(s, pn, _st):
    return is_kicad_item(s, pn)


ITEM_COMMANDS = (
    (OPEN_LIGHT_COMMAND, "Open 3D (light)", "Open this electrical part's light 3D model: the board and its "
     "components, no copper, pads, solder mask or silkscreen. Much smaller, for large assemblies.", "open",
     "secondary", lambda pn=None: open_board_item(pn, light=True), _active_kicad, ""),
    (OPEN_KICAD_COMMAND, "Open in KiCad", "Open this electrical part's KiCad project, kept in the vault, in "
     "KiCad. Check it out to edit; Open from Vault shows the board in 3D.", "kicad", "accent",
     lambda pn=None: open_in_kicad(pn), _active_kicad, ""),
)


class _OpenKiCadCommand:
    """A KiCad item command from ITEM_COMMANDS (Open in KiCad, Open 3D (light))."""

    def __init__(self, entry):
        self.entry = entry

    def GetResources(self):
        icon = KICAD_ICON if self.entry[3] == "kicad" else f"BruceWorx_Vault_{self.entry[3]}_{self.entry[4]}"
        return {"Pixmap": icon, "MenuText": self.entry[1], "ToolTip": self.entry[2]}

    def IsActive(self):
        try:
            vc = _vc()
            s = vc.session()
            return s is not None and is_kicad_item(s, vc.current_target())
        except Exception:
            return False

    def Activated(self):
        self.entry[5]()


class _ImportKiCadCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(bb.HERE, COMMAND + ".svg"),
            "MenuText": "Import KiCad Project as Standard Part…",
            "ToolTip": ("Make a KiCad project an electrical part in the Standard Libraries vault: the project is "
                        "copied into the part's folder in the vault and checked in. Open in KiCad edits it; "
                        "Open from Vault shows the board in 3D."),
        }

    def IsActive(self):
        try:
            return bb.enabled() and _vc().session() is not None
        except Exception:
            return False

    def Activated(self):
        import_project_dialog()


def _install_vault_items():
    """Open in KiCad in the vault's right-click menus; Import KiCad Project... in the BruceWorx menu."""
    import FreeCADGui as Gui

    try:
        Gui.addIcon(KICAD_ICON, KICAD_SVG.encode("utf-8"), "SVG")
    except AssertionError:  # registered already (a reload)
        pass
    for entry in ITEM_COMMANDS:
        Gui.addCommand(entry[0], _OpenKiCadCommand(entry))
    Gui.addCommand(IMPORT_COMMAND, _ImportKiCadCommand())
    try:
        _vc().add_item_commands(ITEM_COMMANDS, after="BruceWorx_VaultOpen")
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx: Open in KiCad not in the vault menus: {exc}\n")
    try:
        import bruceworx_menu

        _QtCore, QtGui, _QtWidgets = _qt()
        action = QtGui.QAction(QtGui.QIcon(os.path.join(bb.HERE, COMMAND + ".svg")),
                               "Import KiCad Project as Standard Part…", Gui.getMainWindow())
        action.triggered.connect(lambda _checked=False: Gui.runCommand(IMPORT_COMMAND))
        bruceworx_menu.add_action(action, f"BruceWorxMenu_{IMPORT_COMMAND}")
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx: Import KiCad Project menu entry not added: {exc}\n")


def install():
    """Register the command and its BruceWorx menu entry (the Assemble group
    lists it in workspaces.json)."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    Gui.addCommand(COMMAND, _InsertBoardCommand())
    _state["installed"] = True
    try:
        _install_vault_items()
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx: KiCad vault items not installed: {exc}\n")
    try:
        import bruceworx_menu

        _QtCore, QtGui, _QtWidgets = _qt()
        action = QtGui.QAction(QtGui.QIcon(os.path.join(bb.HERE, COMMAND + ".svg")), "Insert Board…",
                               Gui.getMainWindow())
        action.triggered.connect(lambda _checked=False: Gui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, f"BruceWorxMenu_{COMMAND}")
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx Insert Board: menu entry not added: {exc}\n")
