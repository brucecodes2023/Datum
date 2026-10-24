# SPDX-License-Identifier: LGPL-2.1-or-later
"""New assembly, components from other files, grounding, Browser badges, save/reopen."""

import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, QtWidgets, QtCore  # noqa: E402
import fixture  # noqa: E402


def items(panel, label):
    """Part rows (not document rows) of that label, with or without the '(n inserted)' counter."""
    found = panel.form.partList.findItems(label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0) + \
        panel.form.partList.findItems(label + " : ", QtCore.Qt.MatchStartsWith | QtCore.Qt.MatchRecursive, 0)
    return [i for i in found if i.data(0, QtCore.Qt.UserRole) is not None]


def click(panel, label):
    found = items(panel, label)
    if not found:
        L.say("no part list item", label, [panel.form.partList.topLevelItem(i).text(0)
                                           for i in range(panel.form.partList.topLevelItemCount())])
        return False
    panel.form.partList.itemClicked.emit(found[0], 0)
    return True


def links(asm):
    return [o for o in asm.Group if o.isDerivedFrom("App::Link")]


def grounded(asm):
    import UtilsAssembly

    return [j for j in UtilsAssembly.getJointGroup(asm).Group if hasattr(j, "ObjectToGround")]


def steps():
    import bruceworx_workspaces as bw

    bw.activate_workspace("Design")
    yield 800
    parts_dir = os.path.join(L.WORK, "parts")
    paths = fixture.make_parts(parts_dir)
    for name in ("Base", "Pin", "Block"):
        App.closeDocument(name)
    yield 300
    # File > Open the three part files, as a user would before inserting them.
    L.tell("File > Open the three part files, as a user would before inserting them.")
    for name in ("Base", "Pin", "Block"):
        App.openDocument(paths[name])
    yield 800
    L.check("part files open", all(n in App.listDocuments() for n in ("Base", "Pin", "Block")),
            list(App.listDocuments()))

    Gui.runCommand("Std_New")
    yield 800
    doc = App.ActiveDocument
    L.trigger("Assembly_CreateAssembly")
    yield 1000
    import UtilsAssembly

    asm = UtilsAssembly.activeAssembly()
    L.check("New Assembly gives an active assembly", asm is not None)
    if asm is None:
        return

    # Unsaved assembly: inserting from another file asks to save first. Cancel it.
    L.tell("Unsaved assembly: inserting from another file asks to save first. Cancel it.")
    L.trigger("Assembly_InsertLink")
    yield 1000
    panel = L.python_panel("TaskAssemblyInsertLink")
    L.check("Component dialog opens", panel is not None and Gui.Control.activeDialog())
    ok, why = L.task_in_right_panel()
    L.check("Component dialog in the right panel", ok, why)
    labels = []
    for i in range(panel.form.partList.topLevelItemCount()):
        top = panel.form.partList.topLevelItem(i)
        labels.append(top.text(0))
        labels.extend(top.child(j).text(0) for j in range(top.childCount()))
    L.check("part list shows the open part files and their parts",
            all(x in labels for x in ("Base", "Base plate", "Pin", "Block")), labels)
    L.grab_task("insert-list")
    modals = []
    L.answer_modal("Cancel", name="save-first", log_to=modals)
    click(panel, "Base plate")
    yield 600
    L.check("unsaved assembly: asks to save first", modals and "saved" in " ".join(modals[0][1]).lower(), modals)
    L.check("unsaved assembly: Cancel inserts nothing", not links(asm), [o.Name for o in links(asm)])
    L.press("Cancel")
    yield 800
    doc.saveAs(os.path.join(L.WORK, "MainAsm.FCStd"))
    yield 500

    L.trigger("Assembly_InsertLink")
    yield 1000
    panel = L.python_panel("TaskAssemblyInsertLink")
    modals = []
    L.answer_modal("Yes", name="ground-first", log_to=modals)
    click(panel, "Base plate")
    yield 900
    L.check("first part: asks to ground it", modals and "ground" in " ".join(modals[0][1]).lower(), modals)
    click(panel, "Pin")
    yield 400
    click(panel, "Pin")
    yield 400
    click(panel, "Block")
    yield 500
    counters = [i.text(0) for i in items(panel, "Pin")]
    L.check("part list counts inserts ('Pin : 2 inserted')", any(t.endswith("2 inserted") for t in counters), counters)
    L.grab_task("insert-picked")
    L.press("OK")
    yield 1200
    L.check("Component dialog closed by OK", not Gui.Control.activeDialog())
    comps = links(asm)
    L.check("four components inserted", len(comps) == 4, [c.Label for c in comps])
    ext = [c for c in comps if c.LinkedObject is not None and c.LinkedObject.Document is not doc]
    L.check("components link to the other files", len(ext) == 4,
            [(c.Label, c.LinkedObject and c.LinkedObject.Document.Name) for c in comps])
    base = next((c for c in comps if c.LinkedObject and c.LinkedObject.Name == "Base"), None)
    pins = [c for c in comps if c.LinkedObject and c.LinkedObject.Name == "Pin"]
    block = next((c for c in comps if c.LinkedObject and c.LinkedObject.Name == "Block"), None)
    g = grounded(asm)
    L.check("first part grounded", base is not None and any(j.ObjectToGround == base for j in g),
            [(j.Name, j.ObjectToGround and j.ObjectToGround.Name) for j in g])
    if len(pins) == 2:
        apart = (pins[0].Placement.Base - pins[1].Placement.Base).Length
        L.check("second Pin placed apart from the first", apart > 1e-6, pins[1].Placement)
    L.check("insert is one undo step", doc.UndoNames[:1] == ["Insert Component"], doc.UndoNames[:3])
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    L.expand_trees()
    yield 600
    L.grab_window("inserted")

    # Browser: part numbers on components (quiet text), nothing on the block.
    L.tell("Browser: part numbers on components (quiet text), nothing on the block.")
    for comp, want in ((base, "BW-100-001"), (pins[0] if pins else None, "BW-200-001")):
        info = L.row_info(comp) if comp is not None else None
        L.check(f"Browser row of {comp and comp.Label} shows {want}",
                info is not None and want in str(info), info)
    info = L.row_info(block) if block is not None else None
    L.check("Browser row of Block shows no part number", info is not None and "BW-" not in str(info), info)
    rows = {c.Label: bool(L.tree_items(c.Label)) for c in comps}
    L.check("each component has a Browser row", all(rows.values()), rows)
    L.grab_browser("browser")

    # Ground / unground the block from the toolbar (Toggle Grounded in the More list).
    L.tell("Ground / unground the block from the toolbar (Toggle Grounded in the More list).")
    Gui.Selection.clearSelection()
    L.pick(doc, asm.Name, f"{block.Name}.BlockBody.Face1", block.Placement.Base)
    yield 300
    n0 = len(grounded(asm))
    L.trigger("Assembly_ToggleGrounded")
    yield 800
    L.check("Toggle Grounded grounds the selected block", len(grounded(asm)) == n0 + 1 and
            any(j.ObjectToGround == block for j in grounded(asm)))
    gj = [j for j in grounded(asm) if j.ObjectToGround == block]
    if gj:
        L.check("grounded joint shows in the Browser", bool(L.tree_items(gj[0].Label)), gj[0].Label)
    Gui.Selection.clearSelection()
    L.pick(doc, asm.Name, f"{block.Name}.BlockBody.Face1", block.Placement.Base)
    yield 300
    L.trigger("Assembly_ToggleGrounded")
    yield 800
    L.check("Toggle Grounded again un-grounds it", len(grounded(asm)) == n0, len(grounded(asm)))
    doc.undo()
    yield 500
    L.check("Undo brings the grounding back", len(grounded(asm)) == n0 + 1, len(grounded(asm)))
    doc.redo()
    yield 500
    L.check("Redo removes it again", len(grounded(asm)) == n0, len(grounded(asm)))
    Gui.Selection.clearSelection()

    # Save, close everything, reopen only the assembly: links come back.
    L.tell("Save, close everything, reopen only the assembly: links come back.")
    placements = {c.Name: App.Placement(c.Placement) for c in comps}
    doc.save()
    path = doc.FileName
    Gui.ActiveDocument.resetEdit()
    yield 300
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    yield 800
    doc = App.openDocument(path)
    yield 1500
    asm = [o for o in doc.Objects if o.isDerivedFrom("Assembly::AssemblyObject")][0]
    comps = links(asm)
    resolved = [(c.Label, c.LinkedObject is not None and not c.LinkedObject.Shape.isNull()
                 if hasattr(c.LinkedObject, "Shape") else c.LinkedObject is not None) for c in comps]
    L.check("reopen: four components", len(comps) == 4, [c.Label for c in comps])
    L.check("reopen: every link resolves to its part file", all(r for _n, r in resolved), resolved)
    L.check("reopen: part files loaded with the assembly",
            all(n in App.listDocuments() for n in ("Base", "Pin", "Block")), list(App.listDocuments()))
    moved = [c.Label for c in comps if not c.Placement.isSame(placements.get(c.Name, c.Placement), 1e-6)]
    L.check("reopen: components where they were", not moved, moved)
    L.check("reopen: grounding kept", any(j.ObjectToGround and j.ObjectToGround.LinkedObject.Name == "Base"
                                          for j in grounded(asm)))
    bad = [o.Name for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
    L.check("reopen: no object in error", not bad, bad)
    base = next(c for c in comps if c.LinkedObject.Name == "Base")
    info = L.row_info(base)
    L.check("reopen: Browser still shows BW-100-001", "BW-100-001" in str(info), info)
    asm.ViewObject.doubleClicked()
    yield 800
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    L.expand_trees()
    yield 500
    L.grab_window("reopened")

    # Right panel, Properties tab (light check: it is still being polished)
    L.tell("Right panel, Properties tab (light check: it is still being polished)")
    try:
        import bruceworx_rightpanel as rp
    except ImportError:
        rp = None
    if rp is not None and rp.enabled():
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, asm.Name, base.Name + ".")
        yield 1000
        L.check("selecting a component brings Properties forward", rp.current_tab() == rp.PROPERTIES,
                rp.current_tab())
        d = rp.dock(rp.PROPERTIES)
        texts = [w.text() for w in d.findChildren(QtWidgets.QLabel) if w.isVisible() and w.text()] if d else []
        edits = [w.text() for w in d.findChildren(QtWidgets.QLineEdit) if w.isVisible()] if d else []
        L.say("properties texts", texts[:30], edits[:20])
        L.check("Properties tab shows the component's part number or name",
                any("BW-100-001" in t or "Base plate" in t for t in texts + edits), (texts[:12], edits[:8]))
        right = L.overlay("OverlayRight")
        if right is not None:
            L.grab(right, "properties-component")
        gj = grounded(asm)
        if gj:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, gj[0].Name)
            yield 900
            texts = [w.text() for w in d.findChildren(QtWidgets.QLabel) if w.isVisible() and w.text()] if d else []
            L.check("Properties tab follows the selection to a joint", rp.current_tab() == rp.PROPERTIES and texts,
                    texts[:8])
            if right is not None:
                L.grab(right, "properties-joint")
        Gui.Selection.clearSelection()


L.run(steps)
