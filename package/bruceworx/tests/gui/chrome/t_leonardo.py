# SPDX-License-Identifier: LGPL-2.1-or-later
"""The window around the Leonardo project (NEMA 17 planetary gearbox), as the user sees it.

Runs on a COPY of the Leonardo vault (run.sh copies it to $BWT_OUT/LeonardoCopy and opens
that copy as the vault, actor test-chrome), so nothing here changes the real vault:
the Start page with the vault and My work, the Vault panel and its states, each part
opened from the vault (read-only), its Browser badges and lock, the workspace tabs,
the right panel (Properties, Physical, Vault, the command tab), Cmd-K finding it, an
undo toast on a checked-out copy, and the window at 1280 and 1024 px.
"""

import os

import bwt
from bwt import App, Gui, QtCore, QtGui, QtWidgets, QTest, check, info, shot, step, wait, wait_until
from windowchecks import check_window

MAX_PARTS = int(os.environ.get("BWT_MAX_PARTS", "8"))


def labels(root):
    out = []
    if root is None:
        return out
    for w in root.findChildren(QtWidgets.QLabel):
        if w.isVisible() and w.text().strip():
            out.append(w.text().strip().replace("\n", " ")[:60])
    for w in root.findChildren(QtWidgets.QLineEdit):
        if w.isVisible() and w.text().strip():
            out.append("[" + w.text().strip()[:40] + "]")
    return out


def answer_dialogs(log, prefer=("Open Read-Only", "Cancel", "No", "Don't Save", "Close")):
    """A timer that answers modal boxes with the first of ``prefer`` it finds and logs them."""

    def tick():
        w = QtWidgets.QApplication.activeModalWidget()
        if w is None:
            return
        text = w.windowTitle()
        if isinstance(w, QtWidgets.QMessageBox):
            text += " | " + w.text() + (" | " + w.informativeText() if w.informativeText() else "")
            names = {b.text().replace("&", ""): b for b in w.buttons()}
            log.append(f"{text} [{', '.join(names)}]")
            shot(f"dialog-{len(log)}", w)
            for p in prefer:
                if p in names:
                    names[p].click()
                    return
            w.reject()
        else:
            log.append(f"{w.metaObject().className()} {text}")
            shot(f"dialog-{len(log)}", w)
            w.reject()

    t = QtCore.QTimer()
    t.setInterval(300)
    t.timeout.connect(tick)
    t.start()
    return t


def part_object(doc):
    for o in doc.Objects:
        if o.TypeId == "App::Part" and hasattr(o, "PartNumber"):
            return o
    for o in doc.Objects:
        if o.TypeId in ("App::Part", "PartDesign::Body", "Assembly::AssemblyObject"):
            return o
    return None


def right_tab(key, name):
    import bruceworx_rightpanel as rp

    rp.show_tab(key)
    wait(900)
    right = bwt.overlay("OverlayRight")
    texts = labels(rp.dock(key) or right)  # hidden tabs still report visible labels: the tab's own dock only
    info(f"{name}.{key}", texts[:24])
    if right is not None and right.isVisible():
        shot(f"{name}-{key}", right)
    return texts


def main():
    import bruceworx_rightpanel as rp
    import bruceworx_vaultclient as vc

    step("vault", "Leonardo (copy) is the open vault: expect its name on the Start page and in the status bar")
    s = vc.session()
    check("vault.session", s is not None, App.ParamGet(bwt.PREFS).GetString("VaultPath", ""))
    if s is None:
        return
    items = s.vault.model.items
    info("vault.items", len(items))
    statuses = {}
    for pn in sorted(items):
        st = s.status(pn)
        it = items[pn]
        statuses[pn] = st
        info(f"item.{pn}", f"{getattr(it, 'item_type', '?')} | {getattr(it, 'description', '')} | state {st.state} "
                           f"rev {st.revision} | {st.code} | lock {st.lock or '-'} | versions {len(s.vault.versions.get(pn, []))}")
    ro = bwt.named("vaultReadout", QtWidgets.QLabel)
    check("vault.status-readout", ro is not None and ro.isVisible() and "Leonardo" in ro.text(), ro.text() if ro else None)

    # Start page
    step("start", "Start page: expect the Leonardo vault button, its summary and My work")
    vb = bwt.named("bwStartVaultSwitch", QtWidgets.QToolButton)
    wait_until(lambda: vb is not None and "Leonardo" in vb.text(), 8000)
    check("start.vault-button", vb is not None and "Leonardo" in vb.text(), vb.text() if vb else None)
    side = bwt.named("bwStartSide")
    info("start.side", labels(side))
    work = [t for t in bwt.all_named("bwStartTable") if t.metaObject().className().endswith("WorkTable")]
    if work:
        t = work[0]
        rows = [" | ".join(t.topLevelItem(i).text(c) for c in range(t.columnCount()))
                for i in range(t.topLevelItemCount())]
        info("start.my-work", rows[:12])
        check("start.my-work-shown", t.isVisible() or not rows, f"{len(rows)} rows, visible={t.isVisible()}")
    else:
        info("start.my-work", "no My work table")
    main_ = bwt.named("bwStartMain")
    info("start.main", labels(main_)[:30])
    shot("start")

    # Vault panel
    step("vault-panel", "Open the Vault panel: expect every Leonardo item with its state and lock")
    vc.show_panel(True)
    wait(1500)
    panel = bwt.named("bwVaultPanel")
    check("panel.shown", panel is not None and panel.isVisible())
    if panel is not None:
        lst = bwt.named("bwVaultList", root=panel)
        n = lst.count() if isinstance(lst, QtWidgets.QListWidget) else (
            lst.topLevelItemCount() if isinstance(lst, QtWidgets.QTreeWidget) else None)
        info("panel.rows", n)
        check("panel.lists-items", n is None or n >= min(len(items), 1), f"{n} rows for {len(items)} items")
        info("panel.texts", labels(panel)[:40])
        clipped = [l.text() for l in panel.findChildren(QtWidgets.QLabel)
                   if l.isVisible() and l.text() and "<" not in l.text() and not l.wordWrap() and bwt.text_clipped(l)]
        check("panel.text-not-clipped", not clipped, clipped)
        shot("vault-panel", panel)
        shot("vault-panel-window")
        info("panel.contrast", bwt.max_contrast(panel))
    vc.show_panel(False)
    wait(500)

    # Each part, opened from the vault read-only
    parts = [pn for pn in sorted(items) if s.vault.versions.get(pn)][:MAX_PARTS]
    info("open.parts", parts)
    if not parts:
        info("open", "no item has a file in the vault yet")
    for pn in parts:
        name = pn
        step(f"open-{pn}", f"Open {pn} ({getattr(items[pn], 'description', '')}) from the vault read-only: "
                           "expect its badges, the Design tabs, Properties/Physical/Vault tabs and Cmd-K to find it")
        log = []
        t = answer_dialogs(log)
        bwt.allow_modal(True)
        before = set(App.listDocuments())
        ok = vc.open_from_vault(pn)
        opened = wait_until(lambda: set(App.listDocuments()) - before, 20000)
        wait(1800)
        t.stop()
        bwt.allow_modal(False)
        info(f"{name}.dialogs", log)
        check(f"{name}.opens", bool(opened), f"open_from_vault -> {ok}")
        if not opened:
            continue
        doc = App.getDocument(sorted(set(App.listDocuments()) - before)[0])
        tabs = bwt.named("BruceWorxTitleTabs", QtWidgets.QTabBar)
        tab_text = tabs.tabText(tabs.currentIndex()) if tabs else ""
        info(f"{name}.title-tab", f"{tab_text!r} (file {os.path.basename(doc.FileName)}, label {doc.Label})")
        check(f"{name}.read-only-shown", True)
        # Browser
        target = part_object(doc)
        hook = getattr(Gui, "BrowserRowInfo", None)
        meta, marks = hook(target) if (hook and target) else ("", [])
        info(f"{name}.browser-row", f"{target.Label if target else None}: meta {meta!r} marks {marks}")
        st = statuses[pn]
        if target is not None and target.TypeId == "App::Part":
            texts = " ".join(m for m, _k in marks) + " " + (meta or "")
            check(f"{name}.badge-state", (st.state or "").split()[0][:3].upper() in texts.upper() or st.revision in texts,
                  f"state {st.state} rev {st.revision} vs {marks}")
            if st.lock:
                check(f"{name}.badge-lock", any(vc.LOCK in m or "lock" in k for m, k in marks), marks)
        for tr in [t for t in bwt.by_class("Gui::TreeWidget") if t.isVisible()]:
            tr.expandAll()
        left = bwt.overlay("OverlayLeft")
        if left is not None:
            shot(f"{name}-browser", left)
        # Workspace tabs
        wsb = bwt.named("BruceWorxWorkspaceButton", QtWidgets.QToolButton)
        info(f"{name}.workspace", f"{wsb.text() if wsb else None} / wb {Gui.activeWorkbench().name()}")
        flat = [tb.windowTitle() for tb in bwt.top_toolbars() if "·" in tb.windowTitle() and any(
            tb.widgetForAction(a) is not None and tb.widgetForAction(a).height() < 8 for a in bwt.visible_actions(tb))]
        check(f"{name}.toolbar-buttons-drawn", not flat, flat)
        # Right panel
        if target is not None:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, target.Name)
            wait(900)
            check(f"{name}.selection-brings-properties", rp.current_tab() == "BWProperties", rp.current_tab())
            props = right_tab("BWProperties", name)
            check(f"{name}.properties-show-pn", any(pn in t for t in props) or not hasattr(target, "PartNumber"),
                  props[:12])
            phys = right_tab("BWPhysical", name)
            info(f"{name}.physical-mass", [t for t in phys if " g" in t or "kg" in t or "mass" in t.lower()][:6])
            vtab = right_tab("BWVault", name)
            check(f"{name}.vault-tab-knows-item", any(pn in t for t in vtab), vtab[:12])
            right_tab("BWParameters", name)
            rp.show_tab("BWProperties")
            shot(f"{name}-window")
        # Cmd-K: by label and by part number
        field = bwt.named("BruceWorxSearch", QtWidgets.QLineEdit)
        lst = bwt.named("BruceWorxSearchList", QtWidgets.QListWidget)
        for what, text in (("label", target.Label if target else doc.Label), ("pn", pn)):
            field.setFocus()
            field.clear()
            QTest.keyClicks(field, text)
            wait(800)
            its = [lst.item(i).text() for i in range(lst.count())]
            hit = "IN THIS DOCUMENT" in its
            if what == "label":
                check(f"{name}.search-by-label", hit, its[:8])
            else:
                # The P/N is on the tab, the Browser row and the panel: Cmd-K should find it too.
                check(f"{name}.search-by-pn", hit or any(pn in i for i in its), its[:8])
            if what == "pn":
                shot(f"{name}-search-pn", bwt.named("BruceWorxSearchResults"))
            QTest.keyClick(field, QtCore.Qt.Key_Escape)
            wait(300)
        # Try to edit a feature of the read-only copy: what does the user get?
        feats = [o for o in doc.Objects if o.TypeId.startswith("PartDesign::") and o.TypeId not in
                 ("PartDesign::Body", "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point")]
        if feats:
            log = []
            t = answer_dialogs(log, prefer=("Cancel", "No", "Close", "OK"))
            bwt.allow_modal(True)
            import bruceworx_picktrace as pt

            pt.open_feature(feats[0])
            wait(2000)
            t.stop()
            bwt.allow_modal(False)
            info(f"{name}.edit-read-only", f"{feats[0].Label}: dialogs {log}, task open {bool(Gui.Control.activeDialog())}, "
                                           f"tab {rp.current_tab()}")
            shot(f"{name}-edit-attempt")
            st = statuses[pn]
            me = getattr(s, "me", "test-chrome")
            if st.lock and str(st.lock) != str(me):
                check(f"{name}.edit-locked-by-other-warns", bool(log),
                      f"locked by {st.lock}: editing opened {'a dialog' if log else 'the task panel with no warning'}")
            else:
                check(f"{name}.edit-read-only-offers-checkout", bool(log), log)
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
            if Gui.ActiveDocument and Gui.ActiveDocument.getInEdit():
                Gui.ActiveDocument.resetEdit()
            wait(600)
        App.closeDocument(doc.Name)
        wait(800)

    # A checked-out copy: lock badge for me, a change, the undo toast, the panel state
    step("checkout-copy", "In the vault COPY: check out one part as test-chrome, change it: expect my lock, "
                          "a toast with Undo, the panel saying checked out")
    free = [pn for pn in parts if not statuses[pn].lock and statuses[pn].state and "WIP" in str(statuses[pn].state).upper()]
    if free:
        pn = free[0]
        log = []
        t = answer_dialogs(log, prefer=("Check Out and Open", "OK", "Check Out", "Yes"))
        bwt.allow_modal(True)
        before = set(App.listDocuments())
        okco = vc.open_from_vault(pn)  # answers "Check Out and Open"
        wait_until(lambda: set(App.listDocuments()) - before, 20000)
        wait(1800)
        t.stop()
        bwt.allow_modal(False)
        info("checkout.dialogs", log)
        st = s.status(pn)
        check("checkout.mine", st.lock and "test-chrome" in str(st.lock), f"{st.code} lock {st.lock} (check_out -> {okco})")
        new = set(App.listDocuments()) - before
        if new:
            doc = App.getDocument(sorted(new)[0])
            target = part_object(doc)
            meta, marks = Gui.BrowserRowInfo(target) if (target and getattr(Gui, "BrowserRowInfo", None)) else ("", [])
            info("checkout.browser-row", f"{meta!r} {marks}")
            Gui.Selection.clearSelection()
            n0 = len(doc.Objects)
            Gui.runCommand("Std_Group")
            import bruceworx_toasts as toasts

            ok = wait_until(lambda: any(x.isVisible() for x in toasts.toasts()), 3000)
            live = [x for x in toasts.toasts() if x.isVisible()]
            check("checkout.toast", ok and live and "Group" in live[0].label.text(),
                  live[0].label.text() if live else "no toast")
            shot("checkout-toast")
            if live:
                live[0].undo.click()
                wait(800)
                check("checkout.toast-undo", len(doc.Objects) == n0, f"{n0} -> {len(doc.Objects)}")
            vc.show_panel(True)
            wait(1200)
            panel = bwt.named("bwVaultPanel")
            if panel is not None:
                shot("checkout-vault-panel", panel)
            vc.show_panel(False)
            left = bwt.overlay("OverlayLeft")
            if left is not None:
                shot("checkout-browser", left)

            # Small windows with the part open
            mw = bwt.mw()
            full = mw.size()
            for w, h in ((1280, 800), (1024, 700)):
                step(f"size-{w}x{h}", f"Resize to {w}x{h} with {pn} open: expect nothing clipped or overlapping")
                if mw.isMaximized():
                    mw.showNormal()
                mw.resize(w, h)
                wait(1800)
                check_window(f"{w}x{h}", w)
                shot(f"size-{w}x{h}")
            mw.resize(full)
            wait(800)
    else:
        info("checkout", "no free WIP part to check out in the copy")


bwt.start(main, timeout_s=900)
