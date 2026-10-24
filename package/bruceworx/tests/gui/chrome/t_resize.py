# SPDX-License-Identifier: LGPL-2.1-or-later
"""The window at smaller sizes: does anything clip or overlap?

Sizes: 1512x945 (14" MacBook default), 1280x800, 1024x700, 800x600. At 1280
and up everything must fit; below that, overflow is reported as INFO
(something has to give) but overlaps are still FAILs.
"""

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until
from windowchecks import check_window, soft

SIZES = [(1512, 945), (1280, 800), (1024, 700), (800, 600)]


def main():
    mw = bwt.mw()
    m = bwt.model_doc()
    wait(1000)
    Gui.Selection.addSelection(m["doc"].Name, m["part"].Name, "Body.Pad.")
    wait(800)
    full = mw.size()
    info("resize.start-size", f"{full.width()}x{full.height()} min hint {mw.minimumSizeHint().width()}x{mw.minimumSizeHint().height()}")
    mdi = mw.findChild(QtWidgets.QMdiArea)
    for w, h in SIZES:
        tag = f"{w}x{h}"
        step(f"size-{tag}", f"Resize the window to {tag}: expect nothing clipped or overlapping")
        if mw.isMaximized() or mw.isFullScreen():
            mw.showNormal()
            wait(600)
        mw.resize(w, h)
        wait(1800)
        got = mw.size()
        check(f"{tag}.window-resizes", abs(got.width() - w) <= 2 and abs(got.height() - h) <= 2,
              f"asked {w}x{h}, got {got.width()}x{got.height()}")
        check_window(tag, w)
        shot(f"model-{tag}")
        # Start page at the same size
        for sub in mdi.subWindowList():
            if sub.windowTitle().startswith("Start"):
                mdi.setActiveSubWindow(sub)
        wait(900)
        page = bwt.named("BruceWorxStart")
        if page is not None and page.isVisible():
            side, main_ = bwt.named("bwStartSide"), bwt.named("bwStartMain")
            check(f"{tag}.start-side-and-main-apart", not bwt.rect_in(side).intersects(bwt.rect_in(main_)),
                  (bwt.rect_in(side).getRect(), bwt.rect_in(main_).getRect()))
            scroll = bwt.named("bwStartScroll", QtWidgets.QScrollArea)
            content = scroll.widget() if scroll else None
            if content is not None:
                soft(f"{tag}.start-no-horizontal-clip", content.width() <= scroll.viewport().width() + 1,
                     f"content {content.width()} px in {scroll.viewport().width()} px", w)
            tables = [t for t in bwt.all_named("bwStartTable") if isinstance(t, QtWidgets.QTreeWidget) and t.isVisible()]
            if tables:
                t = tables[0]
                cols = sum(t.header().sectionSize(i) for i in range(t.columnCount()) if not t.isColumnHidden(i))
                soft(f"{tag}.start-table-columns-fit", cols <= t.viewport().width() + 2,
                     f"columns {cols} px in {t.viewport().width()} px", w)
            cl = [b.text() for b in bwt.all_named("bwStartItem", QtWidgets.QPushButton) if b.isVisible() and bwt.text_clipped(b)]
            check(f"{tag}.start-items-not-clipped", not cl, cl)
            shot(f"start-{tag}")
        for sub in mdi.subWindowList():
            if not sub.windowTitle().startswith("Start"):
                mdi.setActiveSubWindow(sub)
        wait(700)
    step("restore")
    mw.resize(full)
    wait(1000)


bwt.start(main)
