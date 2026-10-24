# SPDX-License-Identifier: LGPL-2.1-or-later
"""Start-up, the BruceWorx Start page, the macOS title row and several open documents.

Start-up errors and warnings are collected by collect.py from the FreeCAD log
(step "startup" = everything before the first check).
"""

import os

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until

NEW_ITEMS = ["Part", "Assembly", "Drawing", "Sketch", "Simulation study"]
# Start page item -> (workbench afterwards, object type the new document must hold)
NEW_EXPECT = {
    "Part": ("PartDesignWorkbench", "PartDesign::Body"),
    "Assembly": ("AssemblyWorkbench", "Assembly::AssemblyObject"),
    "Drawing": ("TechDrawWorkbench", "TechDraw::DrawPage"),
    "Sketch": ("SketcherWorkbench", "Sketcher::SketchObject"),
    "Simulation study": ("FemWorkbench", "Fem::FemAnalysis"),
}


def mdi():
    return bwt.mw().findChild(QtWidgets.QMdiArea)


def row():
    return bwt.named("BruceWorxTitleRow", QtWidgets.QToolBar)


def title_tabs():
    return bwt.named("BruceWorxTitleTabs", QtWidgets.QTabBar)


def page():
    return bwt.named("BruceWorxStart")


def side_items():
    side = bwt.named("bwStartSide")
    return [b for b in side.findChildren(QtWidgets.QPushButton) if b.objectName() == "bwStartItem"] if side else []


def item(text):
    for b in side_items():
        if b.text() == text:
            return b
    return None


def go_start():
    tabs = title_tabs()
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Start":
            tabs.setCurrentIndex(i)
            break
    wait(500)
    return page() is not None and page().isVisible()


def table():
    return [t for t in bwt.all_named("bwStartTable") if isinstance(t, QtWidgets.QTreeWidget) and t.isVisible()]


def recent_rows():
    tabs = table()
    if not tabs:
        return []
    t = tabs[0]
    return [t.topLevelItem(i) for i in range(t.topLevelItemCount())]


def mirror_ok():
    """Title row tabs == document area tabs (titles without ' : 1*' and the same current)."""
    import bruceworx_titlebar as tb

    src = mdi().findChild(QtWidgets.QTabBar)
    tabs = title_tabs()
    a = [tb.tab_title(src.tabText(i)) for i in range(src.count())]
    b = [tabs.tabText(i) for i in range(tabs.count())]
    return a == b and src.currentIndex() == tabs.currentIndex(), (a, b, src.currentIndex(), tabs.currentIndex())


def main():
    mw = bwt.mw()
    step("window", "Start-up: expect PartDesign, the Start tab and a clean Report view")
    wb = Gui.activeWorkbench().name() if Gui.activeWorkbench() else ""
    check("startup.workbench-is-partdesign", wb == "PartDesignWorkbench", wb)
    subs = mdi().subWindowList()
    check("startup.start-tab-open", any(s.windowTitle().startswith("Start") for s in subs),
          [s.windowTitle() for s in subs])
    rep = bwt.named("reportReadout", QtWidgets.QPushButton)
    check("startup.report-readout-zero", rep is not None and "0" in rep.text(), rep.text() if rep else None)
    rv = [w for w in bwt.by_class("Gui::DockWnd::ReportOutput")]
    text = rv[0].toPlainText() if rv else ""
    bad = [l for l in text.splitlines() if "rror" in l or "arning" in l or "Traceback" in l]
    check("startup.report-view-clean", not bad, bad[:5])
    info("startup.window-size", f"{mw.width()}x{mw.height()} dark={bwt.is_dark()}")
    shot("01-startup")

    # ---------------------------------------------------------------- Start page
    step("start-page", "Start page: New/Open items, recent cards and table, filter and segments")
    p = page()
    check("start.page-shown", p is not None and p.isVisible())
    names = [b.text() for b in side_items()]
    for n in NEW_ITEMS:
        b = item(n)
        check(f"start.new-item:{n}", b is not None and b.isVisible() and not b.icon().isNull(),
              "missing" if b is None else ("no icon" if b.icon().isNull() else ""))
    for n in ("Open file…", "Import STEP / STL…", "Getting started", "Settings"):
        check(f"start.item:{n}", n in names, names)
    clipped = [b.text() for b in side_items() if bwt.text_clipped(b)]
    check("start.side-items-not-clipped", not clipped, clipped)
    key = [l.text() for l in (item("Open file…") or QtWidgets.QWidget()).findChildren(QtWidgets.QLabel)]
    check("start.open-shows-shortcut", any("O" in k for k in key), key)

    # recent files: Plate, Bracket and a missing file (prepcfg.py)
    wait_until(lambda: len(recent_rows()) >= 3, 5000)
    rows = recent_rows()
    texts = [r.text(0) for r in rows]
    check("start.recent-table-rows", texts[:3] == ["Plate.FCStd", "Bracket.FCStd", "Gone.FCStd"], texts)
    plate = rows[0] if rows else None
    wait_until(lambda: recent_rows() and recent_rows()[0].text(1) == "LE-01-01001", 5000)
    rows = recent_rows()
    check("start.recent-part-number", rows and rows[0].text(1) == "LE-01-01001", rows[0].text(1) if rows else None)
    gone = [r for r in rows if r.text(0) == "Gone.FCStd"]
    check("start.recent-missing-file-marked", gone and gone[0].isDisabled() and "Not found" in gone[0].text(4),
          gone[0].text(4) if gone else None)
    cards = [c for c in bwt.all_named("bwStartCard") if c.isVisible()]
    check("start.recent-cards", len(cards) >= 2, len(cards))
    card_text = [" / ".join(l.text() for l in c.findChildren(QtWidgets.QLabel) if l.text()) for c in cards]
    info("start.cards", card_text)
    clipped = []
    for c in cards:
        for l in c.findChildren(QtWidgets.QLabel):
            if l.isVisible() and l.text() and not l.wordWrap() and bwt.text_clipped(l):
                clipped.append(l.text())
    check("start.card-text-not-clipped", not clipped, clipped)
    shot("02-start-recent")
    # segments and filter
    segs = {b.text(): b for b in bwt.all_named("bwStartSegment", QtWidgets.QPushButton)}
    check("start.segments", set(segs) >= {"All", "Parts", "Assemblies", "Drawings"}, list(segs))
    if "Assemblies" in segs:
        segs["Assemblies"].click()
        wait(400)
        empty = [l for l in bwt.mw().findChildren(QtWidgets.QLabel) if l.isVisible() and "matches the filter" in l.text()]
        check("start.segment-assemblies-filters", not recent_rows() or not any(r.text(0) == "Plate.FCStd" for r in recent_rows()),
              [r.text(0) for r in recent_rows()])
        segs["All"].click()
        wait(300)
    flt = bwt.named("bwStartFilter", QtWidgets.QLineEdit)
    if flt is not None:
        flt.setText("LE-01")
        wait(400)
        check("start.filter-by-part-number", [r.text(0) for r in recent_rows()] == ["Plate.FCStd"],
              [r.text(0) for r in recent_rows()])
        flt.setText("")
        wait(300)

    # vault switcher
    step("vault-switcher", "Vault switcher: open the test vault, expect its name and menu, then close it")
    vb = bwt.named("bwStartVaultSwitch", QtWidgets.QToolButton)
    check("vault.button-no-vault", vb is not None and "No vault open" in vb.text(), vb.text() if vb else None)
    acts = [a.text() for a in vb.menu().actions() if not a.isSeparator()] if vb else []
    check("vault.menu-no-vault", acts[:2] == ["Open Vault…", "Create Vault…"], acts)
    vault = os.path.join(bwt.FIXTURES, "ChromeVault")
    if os.path.isdir(vault):
        App.ParamGet(bwt.PREFS).SetString("VaultPath", vault)
        ok = wait_until(lambda: "ChromeVault" in vb.text(), 5000)
        check("vault.button-shows-vault", ok, vb.text())
        acts = [a.text() for a in vb.menu().actions() if not a.isSeparator()]
        check("vault.menu-with-vault", {"Import Files…", "Vault Settings…", "Close Vault"} <= set(acts), acts)
        ro = bwt.named("vaultReadout", QtWidgets.QLabel)
        check("vault.statusbar-readout", ro is not None and ro.isVisible() and "ChromeVault" in ro.text(),
              ro.text() if ro else None)
        shot("03-start-vault")
        close = [a for a in vb.menu().actions() if a.text() == "Close Vault"]
        if close:
            close[0].trigger()
            check("vault.close", wait_until(lambda: "No vault open" in vb.text(), 4000), vb.text())
            check("vault.statusbar-readout-gone", not (ro and ro.isVisible()))
    else:
        check("vault.fixture", False, f"no fixture vault at {vault}")

    # ---------------------------------------------------------------- open a recent file
    step("open-recent", "Click the Plate card: expect Plate.FCStd to open in a new tab")
    def opened():
        return "Plate" in App.listDocuments()

    for _try in range(3):  # the page re-renders its cards after a vault change: take a fresh one
        wait(800)
        plate_card = [c for c in bwt.all_named("bwStartCard")
                      if c.isVisible() and getattr(c, "path", "").endswith("Plate.FCStd")]
        if plate_card:
            c = plate_card[0]
            QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, c.rect().center())
        if wait_until(opened, 6000):
            break
    ok = opened()
    check("start.card-opens-file", ok, list(App.listDocuments()))
    wait(800)
    good, detail = mirror_ok()
    check("titlerow.mirrors-tabs-after-open", good, detail)
    shot("04-plate-open")

    # ---------------------------------------------------------------- New items
    for n in NEW_ITEMS:
        step(f"new-{n}", f"Start page New > {n}: expect a new document in the right workbench")
        if not go_start():
            check(f"start.back-to-start-for:{n}", False)
            continue
        before = set(App.listDocuments())
        b = item(n)
        if b is None:
            continue
        bwt.allow_modal(True)
        dialogs = []

        def close_dialog():
            w = QtWidgets.QApplication.activeModalWidget()
            if w is not None:
                dialogs.append(f"{w.metaObject().className()} '{w.windowTitle()}'")
                ok_btn = [x for x in w.findChildren(QtWidgets.QPushButton) if x.text() in ("OK", "&OK")]
                (ok_btn[0].click() if ok_btn else w.accept())

        t = QtCore.QTimer()
        t.setInterval(500)
        t.timeout.connect(close_dialog)
        t.start()
        b.click()
        wb_want, type_want = NEW_EXPECT[n]
        ok = wait_until(lambda: set(App.listDocuments()) - before, 8000)
        wait_until(lambda: Gui.activeWorkbench().name() == wb_want, 8000)
        wait(1500)
        t.stop()
        bwt.allow_modal(False)
        if dialogs:
            info(f"new.{n}.dialog", dialogs)
        new = [App.getDocument(d) for d in set(App.listDocuments()) - before]
        check(f"new.{n}.document-created", len(new) == 1, [d.Name for d in new])
        wbn = Gui.activeWorkbench().name()
        check(f"new.{n}.workbench", wbn == wb_want, wbn)
        types = [o.TypeId for o in new[0].Objects] if new else []
        check(f"new.{n}.object", type_want in types, types)
        wsb = bwt.named("BruceWorxWorkspaceButton", QtWidgets.QToolButton)
        info(f"new.{n}.workspace-button", wsb.text() if wsb else None)
        good, detail = mirror_ok()
        check(f"new.{n}.title-tab-added", good, detail)
        # A command left open (sketch edit, task dialog) is closed before the next item.
        shot(f"05-new-{n.split()[0].lower()}")
        try:
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
            if Gui.ActiveDocument and Gui.ActiveDocument.getInEdit():
                Gui.ActiveDocument.resetEdit()
        except Exception:
            pass
        wait(500)

    # ---------------------------------------------------------------- title row
    step("title-row", "Title row: Save/Undo/Redo, tabs mirror the documents, clicking a tab activates it")
    r = row()
    check("titlerow.shown-at-top", r is not None and r.isVisible() and r.geometry().y() == 0 and r.height() == 40,
          (r.geometry().getRect() if r else None))
    inset = bwt.named("BruceWorxTitleInset")
    check("titlerow.traffic-light-inset", inset is not None and inset.width() >= 80, inset.width() if inset else None)
    quick = [a.text() for a in r.actions() if a.isVisible() and not a.isSeparator() and a.text()]
    info("titlerow.actions", quick)
    for want in ("Save", "Undo", "Redo"):
        check(f"titlerow.quick:{want}", any(want.lower() in q.lower() for q in quick), quick)
    field = bwt.named("BruceWorxSearch", QtWidgets.QLineEdit)
    check("titlerow.search-in-row", field is not None and r.isAncestorOf(field) and field.isVisible())
    tabs = title_tabs()
    tr = bwt.rect_in(tabs)
    fr = bwt.rect_in(field) if field else QtCore.QRect()
    newb = bwt.named("BruceWorxNewTab", QtWidgets.QToolButton)
    nr = bwt.rect_in(newb)
    check("titlerow.tabs-do-not-overlap-search", not tr.intersects(fr), (tr.getRect(), fr.getRect()))
    check("titlerow.plus-visible", newb.isVisible() and not nr.intersects(fr) and nr.right() <= bwt.mw().width(),
          nr.getRect())
    info("titlerow.tabs", [tabs.tabText(i) for i in range(tabs.count())])
    # Each tab brings its document forward.
    wrong = []
    for i in range(tabs.count()):
        tabs.setCurrentIndex(i)
        wait(350)
        sub = mdi().activeSubWindow()
        want = tabs.tabText(i)
        if sub is None or not sub.windowTitle().startswith(want):
            wrong.append((want, sub.windowTitle() if sub else None))
    check("titlerow.tab-click-activates-document", not wrong, wrong)
    elided = [tabs.tabText(i) for i in range(tabs.count())
              if tabs.tabRect(i).width() < QtGui.QFontMetrics(tabs.font()).horizontalAdvance(tabs.tabText(i)) + 8]
    info("titlerow.elided-tabs", elided)
    shot("06-many-docs")

    # + makes a document; a change shows the dot; the tab's close button closes it
    step("title-row-new-close", "Title row: + makes a document, a change shows the dot, the x closes a tab")
    n0 = len(App.listDocuments())
    newb.click()
    check("titlerow.plus-new-document", wait_until(lambda: len(App.listDocuments()) == n0 + 1, 4000),
          len(App.listDocuments()))
    wait(600)
    doc = App.ActiveDocument
    doc.addObject("App::FeaturePython" if False else "App::DocumentObjectGroup", "Group")
    doc.recompute()
    wait(900)
    idx = tabs.currentIndex()
    btn = tabs.tabButton(idx, QtWidgets.QTabBar.RightSide)
    check("titlerow.modified-dot", btn is not None and getattr(btn, "modified", False),
          getattr(btn, "modified", None))
    shot("07-modified-dot")
    doc.undo()
    App.getDocument(doc.Name)
    # A modified document asks to save: use an unmodified one for the close button.
    blank = App.newDocument("Blank")
    wait(900)
    idx = tabs.currentIndex()
    btn = tabs.tabButton(idx, QtWidgets.QTabBar.RightSide)
    bwt.allow_modal(True)
    asked = []

    def answer():
        w = QtWidgets.QApplication.activeModalWidget()
        if isinstance(w, QtWidgets.QMessageBox):
            asked.append(w.text())
            b = w.button(QtWidgets.QMessageBox.Discard) or w.button(QtWidgets.QMessageBox.No)
            (b.click() if b else w.reject())

    t = QtCore.QTimer()
    t.setInterval(300)
    t.timeout.connect(answer)
    t.start()
    if btn is not None:
        btn.click()
    ok = wait_until(lambda: "Blank" not in App.listDocuments(), 4000)
    t.stop()
    bwt.allow_modal(False)
    check("titlerow.close-button-closes-document", ok, (list(App.listDocuments()), asked))
    good, detail = mirror_ok()
    check("titlerow.mirrors-tabs-after-close", good, detail)
    check("titlerow.mdi-tabbar-hidden", not mdi().findChild(QtWidgets.QTabBar).isVisible())


bwt.start(main)
