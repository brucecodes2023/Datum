# SPDX-License-Identifier: LGPL-2.1-or-later
"""Workspace menu and tabs, the toolbar rows and the group captions.

For every workspace and every tab: the toolbars that show are the tab's groups
(no empty toolbar, no toolbar or button twice), they sit on three rows (title
row, workspace bar with File/View, tools), every group has a readable caption,
and every button has an icon. Then More workbenches and the SKETCH tab.
"""

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until

import bruceworx_workspaces as ws_mod


def bar():
    return ws_mod.bar()


def caption_of(tb):
    caps = [c for c in tb.findChildren(QtWidgets.QToolButton) if c.objectName() == "BruceWorxGroupCaption"]
    return caps[0] if caps else None


def tools():
    """Visible top toolbars that are not app toolbars, the title row or the workspace bar."""
    skip = set(bwt.APP_TOOLBARS) | {"BruceWorxTitleRow", "BruceWorxWorkspaces"}
    return [tb for tb in bwt.top_toolbars() if tb.objectName() not in skip]


def action_key(a):
    return a.objectName() or a.text().replace("&", "")


def check_layout(where, expect_groups=None):
    """The checks for one workspace tab (where = 'Design/Solid')."""
    tbs = bwt.top_toolbars()
    names = [tb.objectName() for tb in tbs]
    rows = sorted({tb.geometry().y() for tb in tbs})
    title = bwt.named("BruceWorxTitleRow", QtWidgets.QToolBar)
    want_rows = 3 if title is not None and title.isVisible() else 2
    check(f"rows.{where}", len(rows) <= want_rows,
          f"{len(rows)} rows at y={rows}: " + "; ".join(f"{tb.objectName()}@{tb.geometry().y()}" for tb in tbs))
    wbar = bwt.named("BruceWorxWorkspaces", QtWidgets.QToolBar)
    if wbar is not None and title is not None and title.isVisible():
        same = [tb.objectName() for tb in tbs if tb.geometry().y() == wbar.geometry().y()]
        check(f"rows.{where}.file-view-share-workspace-row", {"File", "View"} <= set(same), same)
    t = tools()
    empty = [tb.objectName() for tb in t if not bwt.visible_actions(tb)]
    check(f"toolbars.{where}.none-empty", not empty, empty)
    # Buttons that exist but are laid out 0 px tall: the group looks empty.
    flat = {}
    for tb in t:
        ws = [tb.widgetForAction(a) for a in bwt.visible_actions(tb) if tb.widgetForAction(a) is not None]
        n = sum(1 for w in ws if w.height() < 8 or w.width() < 8)
        if n:
            m = tb.layout().contentsRect() if tb.layout() else QtCore.QRect()
            safe = tb.testAttribute(QtCore.Qt.WA_ContentsMarginsRespectsSafeArea)
            flat[tb.objectName()] = f"{n}/{len(ws)} buttons 0 px tall, layout rect y={m.y()} h={m.height()}, safe-area={safe}"
    check(f"toolbars.{where}.buttons-drawn", not flat, flat)
    if not t:
        check(f"toolbars.{where}.has-tools", False, names)
    dup_bars = sorted({n for n in names if names.count(n) > 1})
    titles = [tb.windowTitle() for tb in tbs]
    dup_titles = sorted({n for n in titles if titles.count(n) > 1})
    check(f"toolbars.{where}.no-duplicate-toolbars", not dup_bars and not dup_titles, dup_bars + dup_titles)
    seen = {}
    for tb in tbs:
        if tb.objectName() == "BruceWorxTitleRow":
            continue
        for a in bwt.visible_actions(tb):
            if isinstance(a, QtWidgets.QWidgetAction):
                continue
            seen.setdefault(action_key(a), []).append(tb.objectName())
    dups = {k: v for k, v in seen.items() if len(v) > 1 and k}
    check(f"toolbars.{where}.no-duplicate-buttons", not dups, dups)
    noicon = []
    for tb in t:
        for b in bwt.tool_buttons(tb):
            if b.objectName() == "BruceWorxGroupCaption" or b.defaultAction() is None:
                continue
            if b.icon().isNull() and b.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly:
                noicon.append(f"{tb.objectName()}:{b.defaultAction().text()}")
    check(f"toolbars.{where}.buttons-have-icons", not noicon, noicon)
    hidden = {}
    for tb in t:
        ext = bwt.ext_button(tb)
        if ext is not None and ext.isVisible():
            hidden[tb.objectName()] = sum(1 for a in bwt.visible_actions(tb)
                                          if tb.widgetForAction(a) is None or not tb.widgetForAction(a).isVisible())
    bwt.check_wide(f"toolbars.{where}.all-buttons-fit", not hidden, f"overflow (») on {hidden}")
    off = [tb.objectName() for tb in tbs if bwt.rect_in(tb).right() > bwt.mw().width() + 1]
    check(f"toolbars.{where}.inside-window", not off, off)
    # captions
    caps = []
    missing, clipped, outside = [], [], []
    for tb in t:
        c = caption_of(tb)
        if c is None or not c.isVisible() or not c.text():
            missing.append(tb.objectName())
            continue
        caps.append(c.text().replace("▾", "").strip())
        if bwt.text_clipped(c):
            clipped.append(c.text())
        if c.geometry().right() > tb.width() or c.geometry().left() < 0:
            outside.append(c.text())
    check(f"captions.{where}.every-group", not missing, missing)
    bwt.check_wide(f"captions.{where}.not-clipped", not clipped and not outside, clipped + outside)
    crects = [bwt.rect_in(caption_of(tb)) for tb in t if caption_of(tb) is not None and caption_of(tb).isVisible()]
    check(f"captions.{where}.no-overlap", not bwt.overlaps(crects), bwt.overlaps(crects))
    if expect_groups is not None:
        want = [g.upper() for g in expect_groups]
        check(f"captions.{where}.match-definition", caps == want, f"shown {caps}, defined {want}")
    info(f"layout.{where}", " | ".join(f"{tb.windowTitle()}({len(bwt.visible_actions(tb))})" for tb in tbs))
    return tbs


def main():
    b = bar()
    step("bar", "Workspace menu: Design, Simulation, Manufacture, Drawing with Ctrl+1..4")
    check("bar.installed", b is not None and b.isVisible())
    info("window", f"{bwt.mw().width()}x{bwt.mw().height()}")
    defn = ws_mod.definition()
    menu = b.menu
    entries = [a.text() for a in menu.actions() if not a.isSeparator()]
    info("bar.menu", entries)
    for ws in ws_mod.workspaces(defn):
        acts = [a for a in menu.actions() if a.text() == ws["name"]]
        check(f"menu.{ws['name']}", bool(acts), entries)
        if acts and ws.get("shortcut"):
            sc = acts[0].shortcut().toString()
            check(f"menu.{ws['name']}.shortcut", sc == QtGui.QKeySequence(ws["shortcut"]).toString(), sc)
    check("menu.more-workbenches", "More workbenches" in entries, entries)
    # The workbench selector the bar replaces is gone.
    sel = [tb for tb in bwt.top_toolbars() if tb.objectName() == "Workbench"]
    check("bar.stock-selector-hidden", not sel)

    App.newDocument("WsTest")
    wait(800)
    for ws in ws_mod.workspaces(defn):
        name = ws["name"]
        step(f"ws-{name}", f"Workspace {name}: click each tab, expect its tool groups with captions, nothing empty or twice")
        # Through the menu action, as a click would.
        act = [a for a in menu.actions() if a.text() == name]
        if act:
            act[0].trigger()
        ok = wait_until(lambda: Gui.activeWorkbench().name() == ws["workbench"], 15000)
        wait(1500)
        check(f"ws.{name}.workbench", ok, Gui.activeWorkbench().name())
        text = b.button.text()
        check(f"ws.{name}.button-text", text.startswith(name.upper()), text)
        check(f"ws.{name}.button-not-clipped", not bwt.text_clipped(b.button), text)
        tab_names = [b.tabs.tabData(i) for i in range(b.tabs.count())]
        want = [t["name"] for t in ws["tabs"]]
        check(f"ws.{name}.tabs", tab_names == want, f"{tab_names} vs {want}")
        clipped = [b.tabs.tabText(i) for i in range(b.tabs.count())
                   if b.tabs.tabRect(i).width() < QtGui.QFontMetrics(b.tabs.font()).horizontalAdvance(b.tabs.tabText(i))]
        check(f"ws.{name}.tabs-not-clipped", not clipped, clipped)
        for i, tab in enumerate(ws["tabs"]):
            b.tabs.setCurrentIndex(i)
            wait(900)
            where = f"{name}/{tab['name']}"
            groups = [g.get("caption") for g in tab.get("groups", [])] or None
            tbs = check_layout(where, groups)
            if groups:
                shown = {tb.objectName() for tb in tools()}
                wanted = set(ws_mod.tab_toolbars(ws, tab["name"]))
                check(f"ws.{where}.shows-its-groups", wanted <= shown, f"missing {sorted(wanted - shown)}")
                others = set(ws_mod.group_toolbars(ws)) - wanted
                check(f"ws.{where}.hides-other-groups", not (others & shown), sorted(others & shown))
            shot(f"ws-{name}-{tab['name']}".replace(" ", ""))
        # back to the first tab for the next run
        b.tabs.setCurrentIndex(0)
        wait(300)

    # Ctrl+1..4 switch workspace (the shortcuts live on the main window)
    step("shortcut", "Press Ctrl+4..Ctrl+1: expect each workspace to open")
    for ws in reversed(ws_mod.workspaces(defn)):
        sc = ws.get("shortcut", "")
        if not sc:
            continue
        key = getattr(QtCore.Qt, "Key_" + sc.split("+")[-1])
        active = bwt.press(key)
        ok = wait_until(lambda: Gui.activeWorkbench().name() == ws["workbench"], 12000)
        bwt.check_key(f"shortcut.{sc}-{ws['name']}", ok, active, Gui.activeWorkbench().name())
        wait(1200)
    wait_until(lambda: Gui.activeWorkbench().name() == "PartDesignWorkbench", 3000)

    # More workbenches
    step("more-workbenches", "More workbenches > Part: expect one PART tab with its toolbars")
    more = bwt.named("BruceWorxMoreWorkbenches", QtWidgets.QMenu)
    if more is not None:
        b.fill_more(more)
        names = [a.text() for a in more.actions()]
        info("more.entries", names)
        check("more.lists-workbenches", len(names) >= 5, names)
        part = [a for a in more.actions() if a.text() == "Part"]
        if part:
            part[0].trigger()
            ok = wait_until(lambda: Gui.activeWorkbench().name() == "PartWorkbench", 10000)
            wait(1200)
            check("more.part-activates", ok)
            check("more.part-button", b.button.text().startswith("PART"), b.button.text())
            check("more.part-one-tab", b.tabs.count() == 1, [b.tabs.tabText(i) for i in range(b.tabs.count())])
            check_layout("More/Part")
            shot("more-part")
    menu.actions()[0].trigger()  # Design
    wait_until(lambda: Gui.activeWorkbench().name() == "PartDesignWorkbench", 8000)
    wait(800)

    # SKETCH tab while a sketch is open
    step("sketch-tab", "Open a sketch: expect a SKETCH tab and Finish Sketch; finish: both go away")
    doc = App.ActiveDocument
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]
    sk.MapMode = "FlatFace"
    doc.recompute()
    Gui.ActiveDocument.setEdit(sk.Name)
    ok = wait_until(lambda: any(b.tabs.tabData(i) == "Sketch" for i in range(b.tabs.count())), 8000)
    wait(1200)
    check("sketch.tab-appears", ok, [b.tabs.tabText(i) for i in range(b.tabs.count())])
    check("sketch.tab-is-current", b.current_tab() == "Sketch", b.current_tab())
    check("sketch.tab-accent-property", bool(b.tabs.property("sketch")))
    check("sketch.finish-button", b.finish.isVisible() and not bwt.text_clipped(b.finish), b.finish.text())
    check_layout("Sketch")
    sketch_bars = [tb.windowTitle() for tb in tools()]
    want = ws_mod.definition().get("sketch", {}).get("toolbars", [])
    info("sketch.toolbars", sketch_bars)
    # Toolbars the user hid (MainWindow/Toolbars/<name> = false) or that the workbench does not make are left out.
    hidden = App.ParamGet("User parameter:BaseApp/MainWindow/Toolbars")
    made = {tb.objectName() for tb in bwt.mw().findChildren(QtWidgets.QToolBar)}
    missing = [t for t in want if t not in sketch_bars and t in made and hidden.GetBool(t, True)]
    check("sketch.toolbars-shown", not missing, f"missing {missing}")
    info("sketch.toolbars-not-made-or-user-hidden", [t for t in want if t not in sketch_bars and t not in missing])
    shot("sketch")
    b.finish.click()
    ok = wait_until(lambda: not any(b.tabs.tabData(i) == "Sketch" for i in range(b.tabs.count())), 8000)
    wait(1200)
    check("sketch.finish-closes-tab", ok and not b.finish.isVisible(),
          [b.tabs.tabText(i) for i in range(b.tabs.count())])
    check("sketch.back-in-design", Gui.activeWorkbench().name() == "PartDesignWorkbench", Gui.activeWorkbench().name())
    check_layout("after-sketch")
    shot("after-sketch")


bwt.start(main, timeout_s=480)
