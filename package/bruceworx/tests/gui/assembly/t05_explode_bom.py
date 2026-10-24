# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exploded view (one move step) and the bill of materials: contents, quantities,
part-number column, a sub-assembly, detail on/off."""

import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, QtCore, V  # noqa: E402
import fixture  # noqa: E402


def bom_rows(bom):
    """[{column: text}] read from the BOM spreadsheet (header row 1)."""
    cols = list(bom.columnsNames)
    letters = [chr(ord("A") + i) for i in range(len(cols))]
    rows = []
    for r in range(2, 60):
        row = {c: bom.getContents(f"{l}{r}").lstrip("'") for c, l in zip(cols, letters)}
        if not any(row.values()):
            break
        rows.append(row)
    return rows


def steps():
    import bruceworx_workspaces as bw
    import UtilsAssembly

    bw.activate_workspace("Design")
    yield 800
    parts = fixture.make_parts(os.path.join(L.WORK, "parts"))
    docs = {n: App.getDocument(n) for n in parts}
    sub_doc, sub = fixture.make_subasm(os.path.join(L.WORK, "parts"), docs["Pin"], docs["Block"])
    path = os.path.join(L.WORK, "BomAsm.FCStd")
    if os.path.exists(path):
        os.remove(path)
    doc, asm = fixture.new_assembly_doc(path)
    App.setActiveDocument(doc.Name)
    base = fixture.add_link(asm, docs["Base"].getObject("Base"), "Base")
    fixture.ground(asm, base)
    pin1 = fixture.add_link(asm, docs["Pin"].getObject("Pin"), "Pin1", App.Placement(V(25, 30, 10), App.Rotation()))
    fixture.add_link(asm, docs["Pin"].getObject("Pin"), "Pin2", App.Placement(V(75, 30, 10), App.Rotation()))
    fixture.add_link(asm, docs["Block"].getObject("Block"), "Block", App.Placement(V(40, 5, 10), App.Rotation()))
    sl = asm.newObject("Assembly::AssemblyLink", "PinOnBlock")
    sl.LinkedObject = sub
    sl.Rigid = True
    sl.Placement = App.Placement(V(0, 80, 0), App.Rotation())
    doc.recompute()
    doc.save()
    Gui.getDocument(doc.Name).setEdit(asm)
    yield 1000
    L.check("assembly with a sub-assembly is active", UtilsAssembly.activeAssembly() is asm)
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")

    # ---- Exploded view: lift Pin1 by 40 mm
    L.tell("Exploded view: lift Pin1 by 40 mm")
    p0 = App.Placement(pin1.Placement)
    L.trigger("Assembly_CreateView")
    yield 1000
    panel = L.python_panel("TaskAssemblyCreateView")
    L.check("Exploded View dialog opens", panel is not None and Gui.Control.activeDialog())
    if panel is not None:
        L.check("align button says 'Select a part' before a pick",
                not panel.form.btnAlignDragger.isEnabled(), panel.form.btnAlignDragger.text())
        Gui.Selection.addSelection(doc.Name, asm.Name, "Pin1.PinBody.", 25, 30, 20)
        yield 600
        L.check("picking a part enables the dragger", panel.form.btnAlignDragger.isEnabled(),
                panel.form.btnAlignDragger.text())
        plc = App.Placement(asm.ViewObject.DraggerPlacement)
        asm.ViewObject.DraggerPlacement = App.Placement(plc.Base + V(0, 0, 40), plc.Rotation)
        panel.draggerMoved(None)
        step = panel.currentStep
        moved = step.MovementTransform.Base if step is not None else None
        L.check("dragging records a 40 mm lift in the step", moved is not None and (moved - V(0, 0, 40)).Length < 1e-6,
                moved)
        panel.draggerFinished(None)
        yield 600
        L.check("the step is listed", panel.form.stepList.count() == 1, panel.form.stepList.count())
        L.grab_window("explode-step")
        L.grab_task("explode-panel")
        L.press("OK")
        yield 1000
    views = [o for o in doc.Objects if o.Name.startswith("Exploded_View") and hasattr(o, "Proxy")
             and type(o.Proxy).__name__ == "ExplodedView"]
    L.check("Exploded View object made", len(views) == 1, [o.Label for o in views])
    if views:
        # Upstream names it with newObject("…", "Exploded View"): the tree shows "Exploded_View".
        L.check("exploded view reads 'Exploded View' in the Browser (no underscore)",
                "_" not in views[0].Label, views[0].Label)
    L.check("after OK the parts are back in place", pin1.Placement.isSame(p0, 1e-6), pin1.Placement)
    if views:
        ev = views[0]
        L.check("exploded view holds one move", len(ev.Group) == 1, [m.Name for m in ev.Group])
        shape = ev.Proxy.getExplodedShape(ev)
        L.check("exploded shape builds", shape is not None and not shape.isNull())
        L.check("parts are back after building the exploded shape", pin1.Placement.isSame(p0, 1e-6))
        L.check("exploded view row in the Browser", bool(L.tree_items(ev.Label)), ev.Label)
        ev.ViewObject.doubleClicked()
        yield 1000
        panel = L.python_panel("TaskAssemblyCreateView")
        L.check("double-click re-opens it with its step", panel is not None and panel.form.stepList.count() == 1)
        L.press("Cancel")
        yield 800
        L.check("Cancel puts the parts back", pin1.Placement.isSame(p0, 1e-6) and not Gui.Control.activeDialog())

    # ---- BOM through its dialog, with a part number column
    L.tell("BOM through its dialog, with a part number column")
    L.trigger("Assembly_CreateBom")
    yield 1000
    panel = L.python_panel("TaskAssemblyCreateBom")
    L.check("BOM dialog opens", panel is not None and Gui.Control.activeDialog())
    if panel is None:
        return
    cols = [panel.form.columnList.item(i).text() for i in range(panel.form.columnList.count())]
    L.check("default columns listed", len(cols) >= 4 and any("Quantity" in c for c in cols), cols)
    panel.form.CheckBox_detailSubAssemblies.setChecked(True)
    panel.form.CheckBox_detailParts.setChecked(False)
    panel.addColumn()
    yield 200
    item = panel.form.columnList.item(panel.form.columnList.count() - 1)
    panel.form.columnList.closePersistentEditor(item)
    item.setText(".PartNumber")
    yield 300
    L.check("custom column '.PartNumber' added", ".PartNumber" in list(panel.bomObj.columnsNames),
            list(panel.bomObj.columnsNames))
    L.grab_task("bom-panel")
    L.press("OK")
    yield 1500
    boms = [o for o in doc.Objects if o.isDerivedFrom("Assembly::BomObject")]
    L.check("one BOM object", len(boms) == 1)
    if not boms:
        return
    bom = boms[0]
    rows = bom_rows(bom)
    L.say("BOM rows", rows)
    by_name = {r.get("Name"): r for r in rows}
    q = {n: by_name.get(n, {}).get("Quantity") for n in ("Base plate", "Pin", "Block", "Pin on block")}
    L.check("BOM: Base plate x1", q["Base plate"] == "1", q)
    L.check("BOM: Pin x2 at top level (sub-assembly pin counted under it)",
            any(r.get("Name") == "Pin" and r.get("Quantity") == "2" and "." not in r.get("Index", "")
                for r in rows), rows)
    L.check("BOM: sub-assembly row present", "Pin on block" in by_name, list(by_name))
    sub_children = [r for r in rows if r.get("Index", "").count(".") == 1]
    L.check("BOM: sub-assembly detailed (child rows 5.1, 5.2)", len(sub_children) >= 2,
            [(r.get("Index"), r.get("Name")) for r in sub_children])
    L.check("BOM: part number column filled", by_name.get("Base plate", {}).get(".PartNumber") == "BW-100-001"
            and by_name.get("Pin", {}).get(".PartNumber") == "BW-200-001",
            [(r.get("Name"), r.get(".PartNumber")) for r in rows])
    L.check("BOM: file name column names the part file",
            by_name.get("Base plate", {}).get("File Name", "").endswith("Base.FCStd"),
            by_name.get("Base plate", {}).get("File Name"))
    L.check("BOM: no N/A cells", not any("N/A" in v for r in rows for v in r.values()))
    rep = L.report_text()
    L.check("BOM: no warnings in the Report view", "Property not found" not in rep and "Traceback" not in rep,
            [ln for ln in rep.splitlines() if "Property" in ln or "Traceback" in ln][:3])
    L.grab_window("bom-sheet")

    # Detail sub-assemblies off: the sub-assembly is one line
    L.tell("Detail sub-assemblies off: the sub-assembly is one line")
    bom.detailSubAssemblies = False
    bom.recompute()
    rows = bom_rows(bom)
    L.check("detail off: no child rows", not any("." in r.get("Index", "") for r in rows),
            [(r.get("Index"), r.get("Name")) for r in rows])
    L.check("detail off: sub-assembly still one line", any(r.get("Name") == "Pin on block" for r in rows))
    # BomDetail = False on a part: the part's bodies are never listed
    L.tell("BomDetail = False on a part: the part's bodies are never listed")
    bom.detailParts = True
    bom.recompute()
    rows_detail = bom_rows(bom)
    L.check("detail parts on: bodies listed under the Base plate",
            any(r.get("Name") == "BaseBody" for r in rows_detail), [r.get("Name") for r in rows_detail])
    basepart = docs["Base"].getObject("Base")
    if "BomDetail" not in basepart.PropertiesList:
        basepart.addProperty("App::PropertyBool", "BomDetail", "Part")
    basepart.BomDetail = False
    bom.recompute()
    rows_nodetail = bom_rows(bom)
    L.check("BomDetail = False: Base plate is one line, no BaseBody row",
            not any(r.get("Name") == "BaseBody" for r in rows_nodetail), [r.get("Name") for r in rows_nodetail])

    # BOM follows the assembly: delete Pin2 -> Pin x1
    L.tell("BOM follows the assembly: delete Pin2 -> Pin x1")
    bom.detailParts = False
    doc.removeObject("Pin2")
    doc.recompute()
    bom.recompute()
    rows = bom_rows(bom)
    L.check("after deleting a pin the BOM counts 1",
            any(r.get("Name") == "Pin" and r.get("Quantity") == "1" and "." not in r.get("Index", "")
                for r in rows), [(r.get("Index"), r.get("Name"), r.get("Quantity")) for r in rows])


L.run(steps)
