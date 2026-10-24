# SPDX-License-Identifier: LGPL-2.1-or-later
"""Design > Solid > Assemble group: buttons present, captioned, enabled when they should be."""

import sys, os  # noqa: E401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asmlib as L  # noqa: E402
from asmlib import App, Gui, QtWidgets, QtCore  # noqa: E402

BAR = "Solid · Assemble"
ROW = ["Assembly_CreateAssembly", "Assembly_InsertLink", "Assembly_CreateJointFixed",
       "Assembly_CreateJointRevolute", "Assembly_CreateJointSlider"]


def states(names):
    return {n: L.action_enabled(n) for n in names}


def steps():
    import bruceworx_workspaces as bw

    bw.activate_workspace("Design")
    yield 1500
    wb = Gui.activeWorkbench()
    L.check("design workspace runs PartDesign", wb and wb.name() == "PartDesignWorkbench", wb and wb.name())
    b = bw.bar()
    L.check("workspace bar present", b is not None)
    if b is not None and b.current_tab() != "Solid":
        b.select_tab("Solid")
        yield 500
    L.check("Solid tab current", b is not None and b.current_tab() == "Solid", b and b.current_tab())
    bar = L.toolbar(BAR)
    L.check("Assemble toolbar exists", bar is not None)
    if bar is None:
        return
    L.check("Assemble toolbar visible", bar.isVisible())
    ws = bw.workspace(bw.definition(), "Design")
    group = [g for g in bw.tab(ws, "Solid")["groups"] if g["caption"] == "Assemble"][0]
    L.check("row commands match workspaces.json", group["commands"] == ROW, group["commands"])
    for name in ROW:
        act = L.command_action(bar, name)
        w = bar.widgetForAction(act) if act else None
        ok = act is not None and act.isVisible() and w is not None and w.isVisible()
        L.check(f"button {name} shown", ok, act and (act.isVisible(), w and w.isVisible()))
        if act is not None:
            L.check(f"button {name} has icon", not act.icon().isNull())
            L.check(f"button {name} has tooltip", bool(act.toolTip().strip()), act.toolTip()[:60])
            if w is not None:
                inside = bar.rect().contains(w.geometry())
                L.check(f"button {name} not clipped", inside, (w.geometry(), bar.rect()))
    caps = [w for w in bar.findChildren(QtWidgets.QToolButton) if w.objectName() == "BruceWorxGroupCaption"]
    cap = caps[0] if caps else None
    L.check("caption present and visible", cap is not None and cap.isVisible())
    if cap is not None:
        L.check("caption reads ASSEMBLE", cap.text().startswith("ASSEMBLE"), repr(cap.text()))
        L.check("caption not clipped", cap.width() >= cap.sizeHint().width() - 1, (cap.width(), cap.sizeHint().width()))
        menu = cap.menu()
        menu.aboutToShow.emit()
        yield 100
        names = [a.objectName() or a.text() for a in menu.actions() if not a.isSeparator()]
        L.say("caption menu", names)
        texts = [a.text().replace("&", "") for a in menu.actions()]
        for name in group["more"]:
            cmd = Gui.Command.get(name)
            if cmd is None:
                L.check(f"more: {name} is a registered command", False, "Gui.Command.get is None")
                continue
            menu_text = cmd.getInfo().get("menuText", "").replace("&", "")
            found = any(a.objectName() == name for a in menu.actions()) or menu_text in texts
            L.check(f"more: {name} in caption menu", found, menu_text)
        menu.popup(cap.mapToGlobal(QtCore.QPoint(0, cap.height())))
        yield 400
        L.grab(menu, "caption-menu")
        menu.hide()
        # "More" entries are stand-in actions: do they grey out, and is clicking one safe
        # with nothing open?
        L.tell("ASSEMBLE caption menu, no document: More entries should be grey; clicking one must do nothing harmful")
        more = {a.text().replace("&", ""): a for a in menu.actions() if not a.objectName() and a.text()}
        live = [t for t, a in more.items() if a.isEnabled()]
        L.check("no document: More entries greyed out like the row buttons", not live, live)
        for text in ("Cylindrical Joint", "Toggle Grounded", "Solve Assembly", "Exploded View",
                     "Bill of Materials", "New Part"):
            act = more.get(text)
            if act is None:
                continue
            before = L.report_text()
            act.trigger()
            yield 600
            m = QtWidgets.QApplication.activeModalWidget()
            if m is not None:
                L.grab(m, "more-modal")
                m.close()
                yield 200
            dlg = Gui.Control.activeDialog()
            if dlg:
                Gui.Control.closeDialog()
                yield 300
            new = L.report_text()[len(before):]
            bad = "Traceback" in new or "Error" in new or "exception" in new.lower()
            L.check(f"no document: More > {text} does nothing harmful",
                    not bad and not dlg and m is None,
                    (new.strip().replace(chr(10), " | ")[:300], dlg, m is not None))

    # No document (Start page): nothing to assemble yet.
    L.tell("No document (Start page): nothing to assemble yet.")
    s = states(ROW)
    L.say("no document", s)
    L.check("no document: Component/joints disabled",
            not any(s[n] for n in ROW[1:]), s)
    # Upstream: New Assembly needs an open document; the Start page offers New > Assembly.
    L.check("no document: New Assembly disabled (Start page has New > Assembly)",
            not s["Assembly_CreateAssembly"], s["Assembly_CreateAssembly"])
    L.grab_window("start")

    doc = App.newDocument("ToolbarCheck")
    yield 800
    s = states(ROW)
    L.check("empty document: New Assembly enabled", s["Assembly_CreateAssembly"], s)
    L.check("empty document: Component and joints disabled", not any(s[n] for n in ROW[1:]), s)

    L.trigger("Assembly_CreateAssembly")
    yield 1200
    asm = [o for o in doc.Objects if o.isDerivedFrom("Assembly::AssemblyObject")]
    L.check("New Assembly button made an assembly", len(asm) == 1, [o.Name for o in doc.Objects])
    import UtilsAssembly

    L.check("new assembly is active (in edit)", UtilsAssembly.activeAssembly() is not None)
    wb = Gui.activeWorkbench()
    L.check("still in the Design workspace while assembling", wb.name() == "PartDesignWorkbench", wb.name())
    L.check("Assemble toolbar still visible in assembly edit", bar.isVisible())
    s = states(ROW)
    L.check("active empty assembly: Component enabled", s["Assembly_InsertLink"], s)
    # Upstream 1.1: New Assembly inside an active assembly makes a sub-assembly.
    L.check("active empty assembly: New Assembly enabled (sub-assembly)", s["Assembly_CreateAssembly"], s)
    L.check("active empty assembly: joints disabled (no parts)",
            not any(s[n] for n in ROW[2:]), s)
    box = doc.addObject("Part::Box", "Box")
    asm[0].addObject(box)
    doc.recompute()
    yield 800
    s = states(ROW + ["Assembly_ToggleGrounded", "Assembly_CreateBom", "Assembly_SolveAssembly",
                      "Assembly_CreateView"])
    L.check("assembly with a part: joints enabled", all(s[n] for n in ROW[2:]), s)
    L.check("assembly with a part: Ground, BOM, Solve, Exploded view enabled",
            all(s[n] is not False for n in ("Assembly_ToggleGrounded", "Assembly_CreateBom",
                                              "Assembly_SolveAssembly", "Assembly_CreateView")), s)
    Gui.ActiveDocument.ActiveView.viewIsometric()
    Gui.SendMsgToActiveView("ViewFit")
    L.grab_window("assembly-active")
    L.grab(bar, "toolbar")
    # Joint task panel: opens on the right, joint buttons go grey while it is open.
    L.tell("Revolute joint dialog: expect it in the right panel; New Assembly and Component go grey")
    L.trigger("Assembly_CreateJointRevolute")
    yield 1200
    L.check("revolute dialog opens", Gui.Control.activeDialog())
    ok, why = L.task_in_right_panel()
    L.check("task panel shows in the right panel", ok, why)
    s = states(ROW)
    # Joint buttons stay live to switch the joint type; the others go grey.
    L.check("while the joint dialog is open: New Assembly and Component disabled",
            not s["Assembly_CreateAssembly"] and not s["Assembly_InsertLink"], s)
    L.check("while the joint dialog is open: joint buttons live (switch type)", all(s[n] for n in ROW[2:]), s)
    L.trigger("Assembly_CreateJointSlider")
    yield 1200
    import JointObject

    task = JointObject.activeTask
    L.check("Slider button switches the open dialog to Slider",
            task is not None and task.jType == "Slider" and Gui.Control.activeDialog(),
            task and task.jType)
    L.grab_window("joint-dialog-empty")
    L.press("Cancel")
    yield 800
    L.check("Cancel closes the joint dialog", not Gui.Control.activeDialog())
    s = states(ROW)
    L.check("joint buttons enabled again after Cancel", all(s[n] for n in ROW[2:]), s)
    Gui.ActiveDocument.resetEdit()
    yield 1000
    s = states(ROW)
    L.check("assembly left: joints and Component disabled", not any(s[n] for n in ROW[1:]), s)
    L.check("assembly left: New Assembly disabled (one root assembly rule)", not s["Assembly_CreateAssembly"], s)
    asm[0].ViewObject.doubleClicked()
    yield 1000
    L.check("double-click re-activates the assembly", UtilsAssembly.activeAssembly() is asm[0])
    wb = Gui.activeWorkbench().name()
    # Upstream switches to the Assembly workbench here (Mod/Assembly SwitchToWB, default on),
    # which is not one of the BruceWorx workspaces.
    L.tell("Double-click the assembly: expect to stay in the Design workspace")
    L.check("double-click on the assembly keeps the Design workspace", wb == "PartDesignWorkbench",
            f"{wb}; Mod/Assembly/SwitchToWB="
            f"{App.ParamGet('User parameter:BaseApp/Preferences/Mod/Assembly').GetBool('SwitchToWB', True)}")
    yield 600
    L.grab_window("after-double-click")


L.run(steps)

