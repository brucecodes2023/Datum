# SPDX-License-Identifier: LGPL-2.1-or-later
"""The panels around the 3D view: Browser, right panel, navigation pill and cube,
undo toasts, status bar, notifications and the Cmd-K search.
"""

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until


def tree():
    trees = [t for t in bwt.by_class("Gui::TreeWidget") if t.isVisible()]
    return trees[0] if trees else None


def find_item(t, label):
    hits = t.findItems(label, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0)
    return hits[0] if hits else None


def task_buttons():
    """Visible OK / Cancel / Close buttons of the open task dialog."""
    out = []
    for tv in bwt.by_class("Gui::TaskView::TaskView"):
        for box in tv.findChildren(QtWidgets.QDialogButtonBox):
            out += [b for b in box.buttons() if b.isVisible()]
    return out


def tab_titles(ov):
    return [ov.tabText(i) for i in range(ov.count())] if ov is not None else []


def main():
    import bruceworx_browser as br
    import bruceworx_picktrace as pt
    import bruceworx_rightpanel as rp

    m = bwt.model_doc()
    doc = m["doc"]
    wait(1200)

    # ---------------------------------------------------------------- Browser
    step("browser", "Browser: floating card, BROWSER header, part badges and feature summaries")
    left = bwt.overlay("OverlayLeft")
    check("browser.floating-panel-shown", left is not None and left.isVisible())
    dock = bwt.named("Model", QtWidgets.QDockWidget)
    check("browser.header-title", dock is not None and dock.windowTitle() == "Browser",
          dock.windowTitle() if dock else None)
    bars = [b for b in bwt.by_class("Gui::OverlayTitleBar") if left is not None and left.isAncestorOf(b) and b.isVisible()]
    caps = [b.font().capitalization() == QtGui.QFont.AllUppercase for b in bars]
    check("browser.header-small-caps", bars and all(caps), caps)
    meta, marks = br.row_info(m["part"])
    texts = [t for t, _k in marks]
    check("browser.part-badges", "LE-01-01001" in texts and any("02" in t for t in texts), marks)
    meta, _ = br.row_info(m["pad"])
    check("browser.feature-summary", "8" in meta and "mm" in meta, meta)
    t = tree()
    item = find_item(t, m["part"].Label) if t else None
    if item is not None:
        r = t.visualItemRect(item)
        need = QtGui.QFontMetrics(t.font()).horizontalAdvance(m["part"].Label)
        info("browser.part-row", f"row {r.width()} px wide, tree {t.viewport().width()} px, label needs {need} px "
                                 f"+ badges; column 0 = {t.header().sectionSize(0)} px")
    info("browser.panel-width", left.width() if left else None)
    info("browser.transparent-pref", App.ParamGet(bwt.PREFS).GetBool("FloatingModelTreeTransparent", True))
    shot("browser")
    if left is not None:
        shot("browser-panel", left)
        info("browser.panel-contrast", bwt.max_contrast(left))

    # error chip on a failed feature
    step("error-chip-expect-error", "Set the Boss length to 0: expect a red ERROR chip, then fix it")
    m["boss"].Length = 0
    doc.recompute()
    wait(1200)
    in_error = pt.obj_in_error(m["boss"])
    check("browser.failed-feature-detected", in_error, m["boss"].State)
    _meta, marks = br.row_info(m["boss"])
    check("browser.error-chip", any(t == pt.ERROR_CHIP for t, _k in marks), marks)
    if left is not None:
        shot("error-chip-panel", left)
    m["boss"].Length = 14
    doc.recompute()
    wait(800)
    _meta, marks = br.row_info(m["boss"])
    check("browser.error-chip-clears", not any(t == pt.ERROR_CHIP for t, _k in marks), marks)

    # dependency lines on hover, pick trace on a face
    step("dependency-lines", "Hover the Boss row: expect dependency lines; pick a face: expect its feature traced")
    ovs = pt.overlays()
    check("browser.trace-overlay", bool(ovs), len(ovs))
    if ovs:
        ov = ovs[0]
        ov.hover(m["boss"])
        wait(500)
        check("browser.dependency-lines", bool(ov.lines), ov.lines)
        if left is not None:
            shot("dependency-lines-panel", left)
        ov.hover(None)
        shape = m["body"].Shape
        top = max(range(len(shape.Faces)), key=lambda i: shape.Faces[i].CenterOfMass.z)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, m["part"].Name, f"Body.Face{top + 1}")
        wait(900)
        check("browser.pick-trace", ov.traced is not None, ov.traced)
        info("browser.pick-trace-feature", ov.traced)
        if left is not None:
            shot("pick-trace-panel", left)
        Gui.Selection.clearSelection()
        wait(400)

    # ---------------------------------------------------------------- right panel
    step("right-panel", "Select a Pad: expect the right panel with Properties in front; open each tab")
    right = bwt.overlay("OverlayRight")
    Gui.Selection.addSelection(doc.Name, m["part"].Name, "Body.Pad.")
    wait(900)
    check("right.shown-with-selection", right is not None and right.isVisible())
    titles = tab_titles(right)
    info("right.tabs", titles)
    for want in ("Properties", "Vault", "Parameters", "Physical"):
        check(f"right.tab:{want}", want in titles, titles)
    check("right.selection-brings-properties", rp.current_tab() == "BWProperties", rp.current_tab())
    shot("right-properties")
    # Every tab opens without an error (collect.py checks the log)
    for key, title in rp.TABS:
        rp.show_tab(key)
        wait(500)
        check(f"right.tab-opens:{title}", rp.current_tab() == key, rp.current_tab())
        if right is not None:
            shot(f"right-tab-{title.lower()}", right)
    rp.show_tab("BWVault")
    wait(400)

    # A command brings the Tasks tab forward; closing it folds the tab
    step("right-panel-command", "Edit the Pad: expect its command tab in front with OK/Cancel; Cancel folds it")
    pt.open_feature(m["pad"])
    ok = wait_until(lambda: bool(Gui.Control.activeDialog()), 5000)
    wait(900)
    check("right.command-opens-dialog", ok)
    cur = rp.current_tab()
    check("right.command-tab-in-front", cur not in dict(rp.TABS), f"current tab {cur!r}, tabs {tab_titles(right)}")
    btns = task_buttons()
    check("right.task-ok-cancel-visible", any(b.text().replace("&", "") in ("OK", "Cancel") for b in btns),
          [b.text() for b in btns])
    clipped = [b.text() for b in btns if bwt.text_clipped(b)]
    check("right.task-buttons-not-clipped", not clipped, clipped)
    info("right.tabs-during-command", tab_titles(right))
    # A selection while a command is open keeps the command in front
    Gui.Selection.addSelection(doc.Name, m["part"].Name, "Body.Pad001.")
    wait(700)
    check("right.selection-keeps-command-in-front", rp.current_tab() == cur, rp.current_tab())
    shot("right-command-open")
    if right is not None:
        shot("right-command-panel", right)
    cancel = [b for b in btns if b.text().replace("&", "") == "Cancel"]
    if cancel:
        cancel[0].click()
    else:
        Gui.Control.closeDialog()
    ok = wait_until(lambda: not Gui.Control.activeDialog(), 4000)
    wait(900)
    check("right.command-closes", ok)
    after = rp.current_tab()
    check("right.command-tab-folds", after in dict(rp.TABS), f"current {after!r}, tabs {tab_titles(right)}")
    info("right.tabs-after-command", tab_titles(right))
    shot("right-command-closed")

    # A toolbar command (New Sketch asks for a plane in the task panel)
    step("right-panel-new-sketch", "New Sketch: expect the plane picker in the command tab; Cancel folds it")
    Gui.Selection.clearSelection()
    Gui.runCommand("PartDesign_NewSketch")
    ok = wait_until(lambda: bool(Gui.Control.activeDialog()), 5000)
    wait(900)
    check("right.new-sketch-dialog", ok)
    check("right.new-sketch-tab-in-front", rp.current_tab() not in dict(rp.TABS), rp.current_tab())
    shot("right-new-sketch")
    btns = [b for b in task_buttons() if b.text().replace("&", "") == "Cancel"]
    (btns[0].click() if btns else Gui.Control.closeDialog())
    wait_until(lambda: not Gui.Control.activeDialog(), 4000)
    wait(700)
    check("right.new-sketch-folds", rp.current_tab() in dict(rp.TABS), rp.current_tab())

    # Cmd-I hides and shows the panel
    step("cmd-i", "Press Cmd-I: expect the right panel to hide, and to come back on a second Cmd-I")
    Gui.Selection.addSelection(doc.Name, m["part"].Name, "Body.Pad.")
    wait(600)
    r0 = bwt.rect_in(right) if right else QtCore.QRect()
    shown0 = right.isVisible() if right else False
    cmd = Gui.Command.get(rp.COMMAND)
    acts = cmd.getAction() if cmd else []
    check("right.cmd-i-command-has-action", bool(acts),
          f"{rp.COMMAND} registered={cmd is not None}, QActions={len(acts)} (a command's Accel works only through its QAction)")
    owners = [a.text() for a in bwt.mw().findChildren(QtGui.QAction) if a.shortcut().toString() == "Ctrl+I"]
    info("right.cmd-i-owners", owners)
    active = bwt.press(QtCore.Qt.Key_I)
    wait(1500)
    hidden = right is None or not right.isVisible() or bwt.rect_in(right).width() < r0.width() // 2 \
        or bwt.rect_in(right).left() >= bwt.mw().width() - 20
    bwt.check_key("right.cmd-i-hides", hidden and shown0, active,
                  f"before {r0.getRect()} visible={shown0}, after {bwt.rect_in(right).getRect()} visible={right.isVisible()}")
    shot("right-hidden")
    if not hidden:
        # The toggle itself, without the shortcut
        Gui.runCommand(rp.COMMAND)
        wait(1500)
        gone = not right.isVisible() or bwt.rect_in(right).left() >= bwt.mw().width() - 20
        check("right.toggle-command-hides", gone, bwt.rect_in(right).getRect())
    # While hidden, a command still shows its panel
    pt.open_feature(m["pad"])
    wait_until(lambda: bool(Gui.Control.activeDialog()), 5000)
    wait(1200)
    tv = [w for w in bwt.by_class("Gui::TaskView::TaskView") if w.isVisible()]
    check("right.hidden-command-still-shows", bool(tv) and any(b.isVisible() for b in task_buttons()))
    shot("right-hidden-command")
    Gui.Control.closeDialog()
    wait_until(lambda: not Gui.Control.activeDialog(), 4000)
    if Gui.ActiveDocument.getInEdit():
        Gui.ActiveDocument.resetEdit()
    wait(600)
    active = bwt.press(QtCore.Qt.Key_I)
    wait(1500)
    if not acts:
        Gui.runCommand(rp.COMMAND)
        wait(1500)
    back = right is not None and right.isVisible() and bwt.rect_in(right).width() >= r0.width() - 2
    bwt.check_key("right.cmd-i-shows-again", back, active, bwt.rect_in(right).getRect() if right else None)
    if not back:
        Gui.runCommand("BruceWorx_RightPanel")
        wait(1000)
    shot("right-shown-again")
    Gui.Selection.clearSelection()

    # ---------------------------------------------------------------- navigation pill and cube
    step("nav-pill", "Navigation pill and cube: inside the view, clear of the side panels")
    import bruceworx_navpill as nav

    pills = [p for p in nav.pills() if p.isVisible()]
    check("pill.shown", len(pills) == 1, len(pills))
    if pills:
        p = pills[0]
        pr = bwt.rect_in(p)
        vw = p.parentWidget()
        vr = bwt.rect_in(vw)
        check("pill.inside-view", vr.contains(pr), (pr.getRect(), vr.getRect()))
        for name in ("OverlayLeft", "OverlayRight"):
            o = bwt.overlay(name)
            if o is not None and o.isVisible():
                check(f"pill.clear-of-{name}", not pr.intersects(bwt.rect_in(o)), (pr.getRect(), bwt.rect_in(o).getRect()))
        buttons = [b for b in p.findChildren(QtWidgets.QToolButton) if b.isVisible()]
        check("pill.buttons", len(buttons) >= 6, len(buttons))
        notip = [b.objectName() for b in buttons if not b.toolTip()]
        check("pill.buttons-have-tooltips", not notip, notip)
        noicon = [b.objectName() for b in buttons if b.icon().isNull()]
        check("pill.buttons-have-icons", not noicon, noicon)
        info("pill.contrast", bwt.max_contrast(p))
        shot("pill", p)
    nc = App.ParamGet("User parameter:BaseApp/Preferences/View").GetBool("ShowNaviCube", True)
    check("navcube.enabled", nc)
    cube = App.ParamGet("User parameter:BaseApp/Preferences/NaviCube")
    corner = cube.GetInt("CornerNaviCube", 1)
    size = cube.GetInt("CubeSize", 132)
    ox, oy = cube.GetInt("OffsetX", 0), cube.GetInt("OffsetY", 0)
    info("navcube.params", f"corner={corner} size={size} offset=({ox},{oy})")
    vws = bwt._views()
    if vws:
        vr = bwt.rect_in(vws[0])
        # Corner 1 = top right (FreeCAD's NaviCube::Corner order: TopLeft, TopRight, BottomLeft, BottomRight)
        x = vr.right() - ox - size if corner in (1, 3) else vr.left() + ox
        y = vr.top() + oy if corner in (0, 1) else vr.bottom() - oy - size
        cr = QtCore.QRect(x, y, size, size)
        for name in ("OverlayLeft", "OverlayRight"):
            o = bwt.overlay(name)
            if o is not None and o.isVisible():
                orr = bwt.rect_in(o)
                check(f"navcube.clear-of-{name}", not cr.intersects(orr), f"cube ~{cr.getRect()} panel {orr.getRect()}")

    # ---------------------------------------------------------------- undo toast
    step("toast", "New Group: expect a Group created toast with Undo; Undo removes the group")
    import bruceworx_toasts as toasts

    Gui.Selection.clearSelection()
    n0 = len(doc.Objects)
    Gui.runCommand("Std_Group")
    ok = wait_until(lambda: any(t.isVisible() for t in toasts.toasts()), 3000)
    check("toast.shows-after-command", ok,
          f"undo names {doc.UndoNames[:2]}, objects {n0}->{len(doc.Objects)}, in edit {Gui.ActiveDocument.getInEdit()}, "
          f"dialog {bool(Gui.Control.activeDialog())}, toasts {len(toasts.toasts())}")
    live = [t for t in toasts.toasts() if t.isVisible()]
    if live:
        t = live[0]
        check("toast.text", bool(t.label.text()), t.label.text())
        check("toast.undo-button", t.undo.isVisible() and t.undo.isEnabled())
        check("toast.text-not-clipped", not bwt.text_clipped(t.label), t.label.text())
        pills = [p for p in nav.pills() if p.isVisible()]
        if pills:
            check("toast.clear-of-pill", not bwt.rect_in(t).intersects(bwt.rect_in(pills[0])),
                  (bwt.rect_in(t).getRect(), bwt.rect_in(pills[0]).getRect()))
        shot("toast")
        t.undo.click()
        wait(800)
        check("toast.undo-undoes", len(doc.Objects) == n0, f"{n0} -> {len(doc.Objects)}")
        check("toast.hides-after-undo", not t.isVisible() or t.opacity.opacity() < 0.5)
    # It fades by itself
    Gui.runCommand("Std_Group")
    wait(7000)
    check("toast.fades", not any(t.isVisible() and t.opacity.opacity() > 0.5 for t in toasts.toasts()))
    doc.undo()
    wait(300)

    # ---------------------------------------------------------------- status bar
    step("status-bar", "Status bar: readouts readable, a warning bumps the Report count, clicking opens the Report view")
    sb = bwt.mw().statusBar()
    kids = [w for w in sb.findChildren(QtWidgets.QWidget) if w.isVisible() and w.parentWidget() is sb
            and w.width() > 2]
    rects = [bwt.rect_in(w) for w in kids]
    pairs = [(kids[i].objectName() or kids[i].metaObject().className(),
              kids[j].objectName() or kids[j].metaObject().className()) for i, j in bwt.overlaps(rects)]
    check("status.no-overlap", not pairs, pairs)
    for name in ("reportReadout", "unitsReadout", "NavigationIndicator"):
        w = bwt.named(name, QtWidgets.QPushButton)
        check(f"status.{name}", w is not None and w.isVisible() and w.text(), w.text() if w else None)
        if w is not None:
            check(f"status.{name}.not-clipped", not bwt.text_clipped(w), w.text())
    clipped = [w.text() for w in sb.findChildren(QtWidgets.QLabel) if w.isVisible() and w.text() and bwt.text_clipped(w)]
    check("status.labels-not-clipped", not clipped, clipped)
    rep = bwt.named("reportReadout", QtWidgets.QPushButton)
    before = rep.text() if rep else ""
    App.Console.PrintWarning(bwt.expected("a warning made by the chrome test\n"))
    ok = wait_until(lambda: rep.text() != before, 3000)
    check("status.report-counts-warning", ok and "1" in rep.text(), f"{before!r} -> {rep.text()!r}")
    rv = bwt.named("Report view", QtWidgets.QDockWidget)
    if rep is not None:
        rep.click()
        wait(800)
        rvw = [w for w in bwt.by_class("Gui::DockWnd::ReportOutput") if w.isVisible()]
        check("status.report-click-opens-report-view", bool(rvw))
        shot("report-open")
        if rv is not None:
            rv.hide()
        wait(300)
    shot("status-bar", sb)

    # ---------------------------------------------------------------- notifications
    step("notifications", "Send a notification: expect the popup and the count in the status bar")
    area = bwt.named("notificationArea", QtWidgets.QPushButton)
    check("notify.area-shown", area is not None and area.isVisible())
    t0 = area.text() if area else ""
    App.Console.PrintNotification(bwt.expected("chrome test notification\n"))
    wait(1200)
    pops = [w for w in QtWidgets.QApplication.topLevelWidgets()
            if w.objectName() == "NotificationBox_label" and w.isVisible()]
    info("notify.area-text", f"{t0!r} -> {area.text() if area else None!r}, tooltip {area.toolTip() if area else ''!r}")
    if QtWidgets.QApplication.activeWindow() is bwt.mw():
        check("notify.popup-shows", bool(pops))
    else:
        info("notify.popup-shows", "not checked: window not active (FreeCAD only pops notifications for the active window)")
    if pops:
        shot("notification-popup", pops[0])
        info("notify.popup-contrast", bwt.max_contrast(pops[0]))
    if area is not None:
        def close_menu():
            w = QtWidgets.QApplication.activePopupWidget()
            if w is not None:
                shot("notification-menu", w)
                w.close()
        QtCore.QTimer.singleShot(900, close_menu)
        area.click()
        wait(1500)

    # ---------------------------------------------------------------- search
    step("search", "Cmd-K then type pad, Boss, grid strength: expect commands, objects, settings")
    field = bwt.named("BruceWorxSearch", QtWidgets.QLineEdit)
    check("search.field-shown", field is not None and field.isVisible())
    active = bwt.press(QtCore.Qt.Key_K)
    ok = wait_until(lambda: field.hasFocus(), 1500)
    bwt.check_key("search.cmd-k-focuses", ok, active, QtWidgets.QApplication.focusWidget())
    if not field.hasFocus():
        field.setFocus()
        wait(200)
    results = bwt.named("BruceWorxSearchResults")
    lst = bwt.named("BruceWorxSearchList", QtWidgets.QListWidget)

    def items():
        return [lst.item(i).text() for i in range(lst.count())]

    QTest.keyClicks(field, "pad")
    wait(700)
    check("search.results-show", results.isVisible())
    its = items()
    check("search.commands-section", "COMMANDS" in its and any(i == "Pad" for i in its), its[:10])
    rr = bwt.rect_in(results)
    check("search.results-inside-window", bwt.mw().rect().contains(rr), rr.getRect())
    check("search.results-under-field", rr.top() >= bwt.rect_in(field).bottom(), (rr.getRect(), bwt.rect_in(field).getRect()))
    shot("search-pad")
    shot("search-pad-list", results)
    field.clear()
    QTest.keyClicks(field, "Boss")
    wait(700)
    its = items()
    check("search.objects-section", "IN THIS DOCUMENT" in its and "Boss" in its, its)
    lst.setCurrentRow(its.index("Boss") if "Boss" in its else 0)
    QTest.keyClick(field, QtCore.Qt.Key_Return)
    wait(600)
    sel = [o.Name for o in Gui.Selection.getSelection()]
    check("search.return-selects-object", "Pad001" in sel or m["boss"].Name in sel, sel)
    check("search.closes-after-return", not results.isVisible() and not field.text())
    field.setFocus()
    QTest.keyClicks(field, "grid strength")
    wait(700)
    its = items()
    check("search.settings-section", "SETTINGS" in its, its)
    shot("search-settings", results)
    QTest.keyClick(field, QtCore.Qt.Key_Escape)
    wait(400)
    check("search.escape-closes", not results.isVisible() and not field.text())
    field.setFocus()
    QTest.keyClicks(field, "zzqxv")
    wait(600)
    check("search.no-results-row", items() == ["No results"], items())
    QTest.keyClick(field, QtCore.Qt.Key_Escape)
    wait(300)
    # Return on a command runs it (Fit all: harmless)
    field.setFocus()
    QTest.keyClicks(field, "fit all")
    wait(700)
    its = items()
    if "Fit All" in its:
        lst.setCurrentRow(its.index("Fit All"))
    QTest.keyClick(field, QtCore.Qt.Key_Return)
    wait(800)
    check("search.return-runs-command", not results.isVisible(), its[:6])


bwt.start(main, timeout_s=420)
