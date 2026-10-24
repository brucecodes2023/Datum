# SPDX-License-Identifier: LGPL-2.1-or-later
"""Window checks shared by t_resize and t_leonardo: clipping, overflow, overlaps."""

import bwt
from bwt import QtCore, QtWidgets, check, info


def strict(w):
    return w >= 1280


def soft(cid, ok, detail, w):
    if strict(w):
        return check(cid, ok, detail)
    if not ok:
        info(cid, f"(small window) {detail}")
    return ok


def check_window(tag, w):
    mw = bwt.mw()
    W = mw.width()
    # title row
    row = bwt.named("BruceWorxTitleRow", QtWidgets.QToolBar)
    field = bwt.named("BruceWorxSearch", QtWidgets.QLineEdit)
    tabs = bwt.named("BruceWorxTitleTabs", QtWidgets.QTabBar)
    plus = bwt.named("BruceWorxNewTab", QtWidgets.QToolButton)
    if row is not None and row.isVisible():
        fr = bwt.rect_in(field) if field is not None and field.isVisible() else None
        check(f"{tag}.search-shown", fr is not None and fr.right() <= W and fr.width() >= 150,
              fr.getRect() if fr else "hidden")
        if fr is not None:
            check(f"{tag}.tabs-clear-of-search", not bwt.rect_in(tabs).intersects(fr) and not bwt.rect_in(plus).intersects(fr),
                  (bwt.rect_in(tabs).getRect(), bwt.rect_in(plus).getRect(), fr.getRect()))
        check(f"{tag}.plus-shown", plus.isVisible() and bwt.rect_in(plus).right() <= W, bwt.rect_in(plus).getRect())
        ext = bwt.ext_button(row)
        soft(f"{tag}.title-row-fits", not (ext and ext.isVisible()), "title row overflows (») ", w)
    # workspace bar
    bar = bwt.named("BruceWorxWorkspaces", QtWidgets.QToolBar)
    if bar is not None and bar.isVisible():
        wt = bwt.named("BruceWorxWorkspaceTabs", QtWidgets.QTabBar)
        hidden = [wt.tabText(i) for i in range(wt.count()) if bwt.rect_in(wt).left() + wt.tabRect(i).right() > W]
        check(f"{tag}.workspace-tabs-shown", not hidden and wt.isVisible(), hidden)
        btn = bwt.named("BruceWorxWorkspaceButton", QtWidgets.QToolButton)
        check(f"{tag}.workspace-button-not-clipped", not bwt.text_clipped(btn), btn.text())
    # toolbars
    tbs = bwt.top_toolbars()
    off = [tb.objectName() for tb in tbs if bwt.rect_in(tb).right() > W + 1]
    check(f"{tag}.toolbars-inside-window", not off, off)
    rows = sorted({tb.geometry().y() for tb in tbs})
    soft(f"{tag}.toolbar-rows", len(rows) <= 3, f"{len(rows)} rows", w)
    over = {}
    for tb in tbs:
        ext = bwt.ext_button(tb)
        if ext is not None and ext.isVisible():
            n = sum(1 for a in bwt.visible_actions(tb)
                    if tb.widgetForAction(a) is None or not tb.widgetForAction(a).isVisible())
            over[tb.windowTitle()] = n
    soft(f"{tag}.toolbar-buttons-fit", not over, f"hidden behind »: {over}", w)
    flat = [tb.windowTitle() for tb in tbs if "·" in tb.windowTitle() and any(
        tb.widgetForAction(a) is not None and tb.widgetForAction(a).height() < 8 for a in bwt.visible_actions(tb))]
    check(f"{tag}.toolbar-buttons-drawn", not flat, flat)
    caps = [c for tb in tbs for c in tb.findChildren(QtWidgets.QToolButton)
            if c.objectName() == "BruceWorxGroupCaption" and c.isVisible()]
    cl = [c.text() for c in caps if bwt.text_clipped(c)]
    soft(f"{tag}.captions-not-clipped", not cl, cl, w)
    ov = bwt.overlaps([bwt.rect_in(c) for c in caps])
    check(f"{tag}.captions-no-overlap", not ov, [(caps[i].text(), caps[j].text()) for i, j in ov])
    # panels over the view
    left, right = bwt.overlay("OverlayLeft"), bwt.overlay("OverlayRight")
    lr = bwt.rect_in(left) if left is not None and left.isVisible() else None
    rr = bwt.rect_in(right) if right is not None and right.isVisible() else None
    info(f"{tag}.panels", f"browser {lr.getRect() if lr else None} right {rr.getRect() if rr else None}")
    if lr is not None and rr is not None:
        check(f"{tag}.browser-and-right-panel-apart", not lr.intersects(rr), (lr.getRect(), rr.getRect()))
    import bruceworx_navpill as nav

    pills = [p for p in nav.pills() if p.isVisible()]
    if pills:
        pr = bwt.rect_in(pills[0])
        vr = bwt.rect_in(pills[0].parentWidget())
        check(f"{tag}.pill-inside-view", vr.contains(pr), (pr.getRect(), vr.getRect()))
        for name, r in (("browser", lr), ("right-panel", rr)):
            if r is not None:
                check(f"{tag}.pill-clear-of-{name}", not pr.intersects(r), (pr.getRect(), r.getRect()))
    # status bar
    sb = mw.statusBar()
    kids = [k for k in sb.findChildren(QtWidgets.QWidget) if k.isVisible() and k.parentWidget() is sb and k.width() > 2]
    ov = bwt.overlaps([bwt.rect_in(k) for k in kids])
    check(f"{tag}.status-no-overlap", not ov,
          [(kids[i].objectName() or kids[i].metaObject().className(), kids[j].objectName() or kids[j].metaObject().className()) for i, j in ov])
    clipped = [k.text() for k in sb.findChildren(QtWidgets.QAbstractButton) + sb.findChildren(QtWidgets.QLabel)
               if k.isVisible() and hasattr(k, "text") and k.text() and "<" not in k.text()
               and not (isinstance(k, QtWidgets.QToolButton) and k.toolButtonStyle() == QtCore.Qt.ToolButtonIconOnly)
               and bwt.text_clipped(k)]
    soft(f"{tag}.status-text-not-clipped", not clipped, clipped, w)
    offsb = [k.objectName() or k.metaObject().className() for k in kids if bwt.rect_in(k).right() > W + 1 or bwt.rect_in(k).left() < 0]
    check(f"{tag}.status-inside-window", not offsb, offsb)
