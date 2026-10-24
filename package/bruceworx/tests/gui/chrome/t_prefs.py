# SPDX-License-Identifier: LGPL-2.1-or-later
"""Preferences -> BruceWorx: every switch off and on again, live, through the page.

For each check box: click it, press Apply, check its feature went away (where
the effect is visible at once), click it again, Apply, check it is back. The
FreeCAD log is split per switch (step "pref-<name>"), so an error or warning
a switch causes is pinned on it. Switches that act on the next start or the
next workbench switch are only checked for errors.
"""

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until


def visible(name, cls=QtWidgets.QWidget):
    w = bwt.named(name, cls)
    return w is not None and w.isVisible()


def captions():
    return [c for c in bwt.mw().findChildren(QtWidgets.QToolButton)
            if c.objectName() == "BruceWorxGroupCaption" and c.isVisible()]


def tree_overlaid():
    left = bwt.overlay("OverlayLeft")
    dock = bwt.named("Model", QtWidgets.QDockWidget)
    return left is not None and dock is not None and left.isAncestorOf(dock)


def tasks_overlaid():
    right = bwt.overlay("OverlayRight")
    dock = bwt.named("Tasks", QtWidgets.QDockWidget)
    return right is not None and dock is not None and right.isAncestorOf(dock)


def pills():
    import bruceworx_navpill as nav

    return [p for p in nav.pills() if p.isVisible()]


def row_marks(obj):
    hook = getattr(Gui, "BrowserRowInfo", None)
    if hook is None:
        return ("", [])
    try:
        return hook(obj)
    except Exception as exc:
        return ("", [("hook error " + str(exc), "")])


S = {}

# switch -> probe() giving a value that differs between on and off (None: no live effect to check)
PROBES = {
    "HistoryTimelineEnabled": lambda: visible("HistoryTimelineDock", QtWidgets.QDockWidget),
    "GroupCaptions": lambda: len(captions()) > 0,
    "WorkspaceBar": lambda: visible("BruceWorxWorkspaces", QtWidgets.QToolBar),
    "CommandSearch": lambda: visible("BruceWorxSearch"),
    "TabsInTitleBar": lambda: visible("BruceWorxTitleRow", QtWidgets.QToolBar),
    "NavigationPill": lambda: len(pills()) > 0,
    "UndoToasts": lambda: __import__("bruceworx_toasts").enabled(),
    "StatusReadouts": lambda: (bwt.named("NavigationIndicator", QtWidgets.QPushButton).text() or "").startswith("Navigation"),
    "FloatingModelTree": tree_overlaid,
    "FloatingTaskPanel": tasks_overlaid,
    "BrowserHeader": lambda: bwt.named("Model", QtWidgets.QDockWidget).windowTitle() == "Browser",
    "BrowserBadges": lambda: any("LE-01-01001" in t for t, _k in row_marks(S["part"])[1]),
    "BrowserSummary": lambda: "mm" in (row_marks(S["pad"])[0] or ""),
    "BrowserInspector": lambda: __import__("bruceworx_inspector").controller() is not None,
    "StartPage": None,  # checked on the Start tab below
}
# Effect on the next start or the next workbench switch: only errors are checked.
LATER = {"LoadHooks", "UseBruceWorxBar", "PatternCommands", "CompactToolbars", "PreferencePack"}


def qstr(v):
    """A Qt property that may come back as QByteArray."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode()
    if hasattr(v, "data"):
        return bytes(v.data()).decode()
    return str(v)


def find_dialog():
    w = QtWidgets.QApplication.activeModalWidget()
    if w is not None and w.metaObject().className().endswith("DlgPreferencesImp"):
        return w
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.isVisible() and w.metaObject().className().endswith("DlgPreferencesImp"):
            return w
    return None


def button(dlg, text):
    for box in dlg.findChildren(QtWidgets.QDialogButtonBox):
        for b in box.buttons():
            if b.text().replace("&", "") == text:
                return b
    return None


def apply(dlg):
    b = button(dlg, "Apply")
    if b is not None:
        b.click()
    wait(1300)


def run_page():
    """Runs while the modal Preferences dialog is open."""
    dlg = None
    wait_until(lambda: find_dialog() is not None, 6000)
    dlg = find_dialog()
    check("prefs.dialog-opens", dlg is not None)
    if dlg is None:
        return
    boxes = {b.objectName(): b for b in dlg.findChildren(QtWidgets.QCheckBox)
             if b.metaObject().className() == "Gui::PrefCheckBox" and b.property("prefPath")
             and "BruceWorx" in qstr(b.property("prefPath")) or
             b.objectName() in ("HistoryTimelineEnabled", "HistoryTimelineHideWhenEmpty", "ShowTaskWatcher",
                                "UnifiedTitleAndToolBar")}
    page = [b for b in boxes.values() if b.isVisible()]
    check("prefs.bruceworx-page-shown", len(page) >= 30, f"{len(page)} visible switches")
    info("prefs.switches", sorted(boxes))
    shot("page", dlg)
    # Readable labels: contrast of the label text against the page behind it (checkbox glyph excluded)
    low = []
    whole = dlg.grab().toImage()  # as drawn in the dialog, with the page behind the labels
    dpr = whole.devicePixelRatio()
    for b in page[:6]:
        r = bwt.rect_in(b, dlg)
        x0 = r.left() + 26  # past the box itself: the label only
        c = bwt.max_contrast(whole.copy(int(x0 * dpr), int(r.top() * dpr), int(max(2, r.width() - 26) * dpr),
                                        int(r.height() * dpr)))
        if c < 3.0:
            low.append(f"{b.objectName()} {c}")
    check("prefs.labels-readable", not low, low)
    clipped = [b.objectName() for b in page if bwt.text_clipped(b)]
    check("prefs.labels-not-clipped", not clipped, clipped)
    info("prefs.dialog-size", f"{dlg.width()}x{dlg.height()}")
    # The order: harmless first, the preference pack last.
    names = sorted(boxes, key=lambda n: (n == "PreferencePack", n == "FollowSystemAppearance", n))
    for name in names:
        box = boxes[name]
        if name == "FollowSystemAppearance":
            continue  # t_theme.py: it would switch the theme to the system's
        step(f"pref-{name}", f"Preferences > BruceWorx: switch {name} off and on with Apply; expect the feature to go and come back")
        if not bwt.alive(box):
            check(f"pref.{name}.alive", False)
            continue
        if not box.isEnabled():
            info(f"pref.{name}", f"disabled on the page (checked={box.isChecked()}): not toggled")
            continue
        start = box.isChecked()
        probe = PROBES.get(name)
        before = probe() if probe else None
        box.click()
        apply(dlg)
        stored = App.ParamGet("User parameter:BaseApp/Preferences/" + qstr(box.property("prefPath"))).GetBool(
            qstr(box.property("prefEntry")), start)
        check(f"pref.{name}.saved", stored == (not start), f"stored {stored}, box {box.isChecked()}")
        mid = probe() if probe else None
        if name == "StartPage":
            mid = start_page_shown()
            before = not mid if mid is not None else None
        if probe is not None or name == "StartPage":
            check(f"pref.{name}.live-{'off' if start else 'on'}", mid != before, f"{before} -> {mid}")
            shot(f"{name}-{'off' if start else 'on'}")
        if not bwt.alive(box):
            check(f"pref.{name}.box-survives", False)
            continue
        box.click()
        apply(dlg)
        after = probe() if probe else None
        if name == "StartPage":
            after = start_page_shown()
            check(f"pref.{name}.back", after == (not mid), f"{mid} -> {after}")
        elif probe is not None:
            check(f"pref.{name}.back", after == before, f"{before} -> {mid} -> {after}")
        elif name in LATER:
            info(f"pref.{name}", "acts on the next start or workbench switch: errors only")
        else:
            info(f"pref.{name}", "no live effect checked: errors only")
    step("prefs-close")
    ok = button(dlg, "OK")
    (ok.click() if ok else dlg.accept())


def start_page_shown():
    import bruceworx_start as st

    pages = st._pages() if hasattr(st, "_pages") else []
    return any(p.isVisible() for p in pages)


def main():
    m = bwt.model_doc()
    S.update(m)
    wait(1000)
    # The Start tab in front, so StartPage shows its effect; the model stays open behind it.
    mdi = bwt.mw().findChild(QtWidgets.QMdiArea)
    for sub in mdi.subWindowList():
        if sub.windowTitle().startswith("Start"):
            mdi.setActiveSubWindow(sub)
    wait(800)
    before = {n: (p() if p else None) for n, p in PROBES.items() if n != "StartPage"}
    info("prefs.start-state", before)
    bwt.allow_modal(True)
    QtCore.QTimer.singleShot(1500, lambda: _guard(run_page))
    step("open-preferences", "Open Preferences > BruceWorx")
    Gui.showPreferences("BruceWorx")
    bwt.allow_modal(False)
    wait(1500)
    step("after-preferences")
    after = {n: (p() if p else None) for n, p in PROBES.items() if n != "StartPage"}
    changed = {n: (before[n], after[n]) for n in before if before[n] != after[n]}
    check("prefs.everything-back-after-close", not changed, changed)
    for sub in mdi.subWindowList():
        if not sub.windowTitle().startswith("Start"):
            mdi.setActiveSubWindow(sub)
    wait(800)
    shot("after")


def _guard(fn):
    import traceback

    try:
        fn()
    except Exception:
        check("script-error", False, traceback.format_exc().strip().splitlines()[-1])
        bwt.say(traceback.format_exc())
        d = find_dialog()
        if d is not None:
            d.reject()


bwt.start(main, timeout_s=600)
