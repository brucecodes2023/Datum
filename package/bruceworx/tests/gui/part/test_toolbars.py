# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Design workspace's Solid tab: groups, captions, buttons and when they are enabled.

Reads workspaces.json for what should be there, then checks the live toolbars:
each group is a toolbar with a readable caption (CREATE ▾ ...), every command
has a visible button with an icon and a tooltip, the caption menu lists the
"more" commands, the row fits the window, and buttons are enabled or disabled
as the model state says (no document / empty body / sketch selected / feature
selected). Also the other workspace tabs switch the groups.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import harness as H
import partmodel as M

S = {}


def defn():
    import bruceworx_workspaces as ws
    return ws.definition()


def design():
    import bruceworx_workspaces as ws
    return ws.workspace(defn(), "Design")


def groups(tab):
    import bruceworx_workspaces as ws
    t = ws.tab(design(), tab)
    return t.get("groups", []) if t else []


def caption_of(bar):
    for c in bar.findChildren(QtWidgets.QToolButton, "BruceWorxGroupCaption"):
        return c
    return None


def enabled_map(commands):
    out = {}
    for c in commands:
        a = H.action_for(c)
        out[c] = None if a is None else a.isEnabled()
    return out


ALL_SOLID = []


@H.test
def t_start_state():
    """No document open: the Design workspace and its Solid tab."""
    for d in list(App.listDocuments().values()):
        App.closeDocument(d.Name)
    import bruceworx_workspaces as ws
    ws.activate_workspace("Design")
    yield 2000
    bar = ws.bar()
    H.check("bar.exists", bar is not None and bar.isVisible(), "workspace bar missing or hidden")
    if bar is None:
        return
    H.check("bar.workspace_button", "DESIGN" in bar.button.text(), bar.button.text())
    names = [bar.tabs.tabData(i) for i in range(bar.tabs.count())]
    want = [t["name"] for t in design()["tabs"]]
    H.check("bar.tabs", names == want, f"{names} != {want}")
    bar.select_tab("Solid")
    yield 600
    H.check("bar.solid_current", bar.current_tab() == "Solid", bar.current_tab())
    for g in groups("Solid"):
        ALL_SOLID.extend(g["commands"])
    S["nodoc"] = enabled_map(ALL_SOLID)
    H.say("enabled, no document:", S["nodoc"])
    # With no document a modelling command has nothing to work on.
    for c in ("PartDesign_Pad", "PartDesign_Pocket", "PartDesign_Fillet", "PartDesign_NewSketch",
              "BruceWorx_RectangularPattern", "PartDesign_Plane"):
        H.check(f"nodoc.disabled.{c}", S["nodoc"].get(c) is False, f"enabled={S['nodoc'].get(c)}")
    H.grab_window("nodoc")


@H.test
def t_groups():
    mw = H.mw()
    bars = []
    for g in groups("Solid"):
        name = f"Solid · {g['caption']}"
        bar = H.toolbar(name)
        key = "group." + g["caption"]
        H.check(key + ".toolbar", bar is not None and bar.isVisible(), f"{name} missing/hidden")
        if bar is None:
            continue
        bars.append(bar)
        cap = caption_of(bar)
        want = f"{g['caption'].upper()} ▾"
        H.check(key + ".caption", cap is not None and cap.isVisible() and cap.text() == want,
                f"caption={cap.text() if cap else None!r}")
        if cap is not None:
            ok, detail = H.fully_visible(cap)
            H.check(key + ".caption_readable", ok and cap.width() >= cap.fontMetrics().horizontalAdvance(cap.text()),
                    detail)
        acts = dict(H.actions_of(bar))
        for c in g["commands"]:
            a = acts.get(c)
            ck = f"{key}.button.{c}"
            if a is None:
                H.check(ck, False, "no button on the toolbar")
                continue
            btn = bar.widgetForAction(a)
            problems = []
            if btn is None or not btn.isVisible():
                problems.append("hidden (overflow?)")
            if a.icon().isNull():
                problems.append("no icon")
            if not (a.toolTip() or a.text()):
                problems.append("no tooltip")
            if btn is not None and btn.isVisible():
                vr = btn.visibleRegion().boundingRect()
                if vr.width() < btn.width() or vr.height() < btn.height():
                    problems.append(f"clipped {vr.width()}x{vr.height()} of {btn.width()}x{btn.height()}")
            H.check(ck, not problems, ", ".join(problems))
        # The caption menu: every command and the "more" ones, by name.
        if cap is not None and cap.menu() is not None:
            menu = cap.menu()
            menu.aboutToShow.emit()
            H.process()
            texts = [a.text().replace("&", "") for a in menu.actions() if a.text()]
            ids = {str(a.data()) for a in menu.actions()} | {a.objectName() for a in menu.actions()}
            missing = []
            for c in g.get("more", []):
                cmd = Gui.Command.get(c)
                if cmd is None:
                    missing.append(c + " (not registered)")
                    continue
                label = cmd.getInfo().get("menuText", c).replace("&", "")
                # A drop-down group shows its current entry's name ("Subtractive Box").
                if c not in ids and not any(label in t or t in label for t in texts):
                    missing.append(c)
            H.check(key + ".caption_menu_more", not missing, f"missing {missing}; menu={texts[:30]}")
            H.say(key, "menu:", texts)
        ext = [w for w in bar.findChildren(QtWidgets.QToolButton) if w.metaObject().className() == "QToolBarExtension"]
        H.check(key + ".no_overflow", not any(e.isVisible() for e in ext), "toolbar overflow chevron shown")
    # One row, no overlaps, inside the window.
    if bars:
        ys = {b.geometry().y() for b in bars}
        H.check("groups.one_row", len(ys) == 1, f"rows at y={sorted(ys)}")
        rects = sorted((b.geometry() for b in bars), key=lambda r: r.x())
        overlap = [i for i in range(len(rects) - 1) if rects[i].right() >= rects[i + 1].left() + 1]
        H.check("groups.no_overlap", not overlap, f"overlapping pairs at {overlap}")
        H.check("groups.inside_window", rects[-1].right() <= mw.width(), f"right edge {rects[-1].right()} > {mw.width()}")
        top = QtCore.QRect(0, 0, mw.width(), max(r.bottom() for r in rects) + 4)
        H.mw().grab(top).save(H.shot_path("toolbar"))


@H.test
def t_states():
    """Enabled state as the model grows: empty body, sketch selected, feature selected."""
    doc = App.newDocument("TB")
    yield 800
    Gui.runCommand("PartDesign_Body")
    yield 1500
    body = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"][0]
    Gui.Selection.clearSelection()
    yield 1200
    st = enabled_map(ALL_SOLID)
    H.say("enabled, empty body:", st)
    for c in ("PartDesign_NewSketch", "PartDesign_Plane", "PartDesign_CompPrimitiveAdditive", "Std_Import"):
        H.check(f"emptybody.enabled.{c}", st.get(c) is True, f"enabled={st.get(c)}")
    sk = M.sketch(body, "XY_Plane")
    M.rect(sk, 0, 0, 20, 10)
    M.done(sk)
    Gui.Selection.addSelection(doc.Name, body.Name, sk.Name + ".")
    yield 1200
    st = enabled_map(ALL_SOLID)
    H.say("enabled, sketch selected:", st)
    for c in ("PartDesign_Pad", "PartDesign_Revolution", "PartDesign_Pocket", "PartDesign_Hole"):
        H.check(f"sketchsel.enabled.{c}", st.get(c) is True, f"enabled={st.get(c)}")
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 5
    doc.recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, body.Name, "Pad.")
    yield 1200
    st = enabled_map(ALL_SOLID)
    H.say("enabled, feature selected:", st)
    for c in ("BruceWorx_RectangularPattern", "BruceWorx_CircularPattern", "PartDesign_Mirrored",
              "PartDesign_Fillet", "PartDesign_Chamfer", "PartDesign_Draft", "PartDesign_Thickness"):
        H.check(f"featsel.enabled.{c}", st.get(c) is True, f"enabled={st.get(c)}")
    H.grab_window("states")


@H.test
def t_tabs_switch():
    import bruceworx_workspaces as ws
    bar = ws.bar()
    if bar is None:
        return
    for tab in [t["name"] for t in design()["tabs"]]:
        for i in range(bar.tabs.count()):
            if bar.tabs.tabData(i) == tab:
                bar.tabs.setCurrentIndex(i)  # as a click does
        yield 800
        shown = [b.objectName() for b in H.mw().findChildren(QtWidgets.QToolBar)
                 if b.isVisible() and " · " in b.objectName()]
        want = ws.tab_toolbars(design(), tab)
        H.check(f"tab.{tab}.shows_its_groups", sorted(shown) == sorted(want), f"shown={shown} want={want}")
        empty = [n for n in want if H.toolbar(n) is not None and not any(
            a.isVisible() for _c, a in H.actions_of(H.toolbar(n)))]
        H.check(f"tab.{tab}.no_empty_group", not empty, f"empty: {empty}")
        mw = H.mw()
        H.mw().grab(QtCore.QRect(0, 0, mw.width(), 160)).save(H.shot_path(f"tab-{tab}"))
    util = H.toolbar("Utilities · Properties")
    if util is not None:
        H.check("utilities.has_set_material", "Std_SetMaterial" in dict(H.actions_of(util)), "no Std_SetMaterial")
    bar.select_tab("Solid")
    yield 600
    H.check("tab.back_to_solid", bar.current_tab() == "Solid", bar.current_tab())


H.start()
