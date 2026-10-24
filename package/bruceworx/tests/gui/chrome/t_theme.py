# SPDX-License-Identifier: LGPL-2.1-or-later
"""Light/dark: every piece of chrome is readable in the run's theme, and
FollowSystemAppearance follows macOS when switched on (macOS is only read).

Readability: the highest contrast between a widget's main colour and any pixel
of it (bwt.max_contrast). Text that disappears into its background scores ~1;
WCAG asks 4.5 for text, 3 for large text and icons. Below 3 is a FAIL.
"""

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until

THEME = "User parameter:BaseApp/Preferences/MainWindow"


def readable(cid, w, floor=3.0):
    if w is None or not w.isVisible() or w.width() < 4:
        info(cid, "not shown")
        return
    c = bwt.max_contrast(w)
    check(cid, c >= floor, f"contrast {c}")


def main():
    import bruceworx_appearance as ap

    mode = bwt.TAG
    step("theme", "Theme: expect the window in the run's light/dark theme")
    theme = App.ParamGet(THEME).GetString("Theme", "")
    dark = bwt.is_dark()
    check("theme.matches-run", ("Dark" in theme) == (mode == "dark") and dark == (mode == "dark"),
          f"Theme={theme!r} window dark={dark} run={mode}")

    # Start page
    step("readable-start")
    for name in ("bwStartSide", "bwStartMain"):
        readable(f"read.start.{name}", bwt.named(name))
    for i, b in enumerate([b for b in bwt.all_named("bwStartItem", QtWidgets.QPushButton) if b.isVisible()][:5]):
        readable(f"read.start.item-{b.text()}", b, 4.5)
    readable("read.start.vault-switch", bwt.named("bwStartVaultSwitch"), 4.5)
    for c in [c for c in bwt.all_named("bwStartCard") if c.isVisible()][:2]:
        readable(f"read.start.card-{getattr(c, 'path', '?').split('/')[-1]}", c, 4.5)
    shot("start")

    m = bwt.model_doc()
    wait(1200)
    Gui.Selection.addSelection(m["doc"].Name, m["part"].Name, "Body.Pad.")
    wait(900)
    step("readable-chrome", "Measure text contrast of tabs, captions, status bar, Browser, right panel")
    readable("read.title-row", bwt.named("BruceWorxTitleRow", QtWidgets.QToolBar), 4.5)
    readable("read.title-tabs", bwt.named("BruceWorxTitleTabs"), 4.5)
    readable("read.search-field", bwt.named("BruceWorxSearch"), 4.5)
    readable("read.workspace-button", bwt.named("BruceWorxWorkspaceButton"), 4.5)
    readable("read.workspace-tabs", bwt.named("BruceWorxWorkspaceTabs"), 4.5)
    for c in [c for c in bwt.mw().findChildren(QtWidgets.QToolButton)
              if c.objectName() == "BruceWorxGroupCaption" and c.isVisible()][:3]:
        readable(f"read.caption-{c.text()}", c, 4.5)
    tools = [tb for tb in bwt.top_toolbars() if "·" in tb.windowTitle()]
    for tb in tools[:2]:
        readable(f"read.toolbar-{tb.windowTitle()}", tb, 3.0)
    for name in ("reportReadout", "unitsReadout", "NavigationIndicator"):
        readable(f"read.status-{name}", bwt.named(name), 4.5)
    left = bwt.overlay("OverlayLeft")
    for t in [t for t in bwt.by_class("Gui::TreeWidget") if t.isVisible()][:1]:
        readable("read.browser-tree", t.viewport(), 4.5)
    readable("read.right-panel", bwt.overlay("OverlayRight"), 4.5)
    import bruceworx_navpill as nav

    for p in [p for p in nav.pills() if p.isVisible()][:1]:
        readable("read.nav-pill", p, 3.0)
    shot("window")
    if left is not None:
        shot("browser", left)
    right = bwt.overlay("OverlayRight")
    if right is not None:
        shot("right", right)

    # Search results, toast
    step("readable-popups")
    field = bwt.named("BruceWorxSearch", QtWidgets.QLineEdit)
    field.setFocus()
    QTest.keyClicks(field, "pad")
    wait(700)
    readable("read.search-results", bwt.named("BruceWorxSearchResults"), 4.5)
    shot("search", bwt.named("BruceWorxSearchResults"))
    QTest.keyClick(field, QtCore.Qt.Key_Escape)
    wait(300)
    Gui.Selection.clearSelection()
    Gui.runCommand("Std_Group")
    wait(700)
    import bruceworx_toasts as toasts

    for t in [t for t in toasts.toasts() if t.isVisible()][:1]:
        readable("read.toast", t, 4.5)
        shot("toast", t)
    m["doc"].undo()

    # FollowSystemAppearance (macOS is only read, never changed)
    step("follow-system", "Turn Follow macOS appearance on: expect the theme to match macOS (macOS itself untouched)")
    system = ap.system_is_dark()
    info("follow.system-is-dark", system)
    App.ParamGet(bwt.PREFS).SetBool("FollowSystemAppearance", True)
    wait(2500)
    now = "Dark" in App.ParamGet(THEME).GetString("Theme", "")
    if system is None:
        info("follow.on", "macOS appearance unknown to Qt")
    else:
        live = now == system
        check("follow.on-takes-system-theme-live", live,
              f"system dark={system}, theme now {App.ParamGet(THEME).GetString('Theme', '')!r}")
        if not live:
            # What a user does next: switch to another app and back (an activation syncs)
            QtWidgets.QApplication.sendEvent(QtWidgets.QApplication.instance(),
                                             QtCore.QEvent(QtCore.QEvent.ApplicationActivate))
            wait(2500)
            now = "Dark" in App.ParamGet(THEME).GetString("Theme", "")
            check("follow.on-takes-system-theme-after-activation", now == system,
                  App.ParamGet(THEME).GetString("Theme", ""))
    wait(1500)
    check("follow.window-matches-theme", bwt.is_dark() == now, f"window dark={bwt.is_dark()} theme dark={now}")
    shot("follow-on")
    App.ParamGet(bwt.PREFS).SetBool("FollowSystemAppearance", False)
    wait(1500)
    kept = "Dark" in App.ParamGet(THEME).GetString("Theme", "")
    check("follow.off-keeps-theme", kept == now, f"{now} -> {kept}")


bwt.start(main)
