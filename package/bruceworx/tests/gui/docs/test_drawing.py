"""Drawings as a user meets them: the Drawing workspace and its TechDraw toolbars, New Drawing
(ANSI and ISO sheets, title block filled from the part and the vault), adding views (base view,
projection group, section), a dimension, undo/redo, hand-typed title-block text kept, and the page
readable in light and dark. Runs in the GUI; quits itself. See README.md.
"""
import os
import sys

sys.path.insert(0, os.environ.get("BW_TESTDIR") or os.path.dirname(os.path.abspath(__file__)))
import bwdocs as C  # noqa: E402

import FreeCAD  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

V = {}
TOOLBARS = {
    "TechDraw Pages": ["TechDraw_PageDefault", "TechDraw_PageTemplate"],
    "TechDraw Views": ["TechDraw_View", "TechDraw_SectionGroup", "TechDraw_DetailView"],
    "TechDraw Dimensions": ["TechDraw_Balloon"],
    "TechDraw Annotation": ["TechDraw_Annotation", "TechDraw_LeaderLine"],
}
# Toolbars the BruceWorx pack hides (bruceworx_pack.py): their commands must stay in the menu.
PACK_HIDDEN = {
    "TechDraw Decoration": ["TechDraw_ToggleFrame", "TechDraw_Hatch", "TechDraw_GeometricHatch"],
    "TechDraw File Access": ["TechDraw_ExportPageSVG", "TechDraw_ExportPageDXF"],
    "TechDraw Centerlines": ["TechDraw_ExtensionCircleCenterLines"],
}


def menu_commands():
    """Command names reachable from the menu bar (all levels)."""
    out = set()

    def walk(menu, depth=0):
        if depth > 5:
            return
        menu.aboutToShow.emit()
        for a in menu.actions():
            if a.objectName():
                out.add(a.objectName())
            if a.menu() is not None:
                walk(a.menu(), depth + 1)

    for a in C.mw().menuBar().actions():
        if a.menu() is not None:
            walk(a.menu())
    return out


def drw():
    import bruceworx_drawing

    return bruceworx_drawing


def page_views():
    return [w for w in C.mw().findChildren(QtWidgets.QGraphicsView) if w.isVisible() and w.scene() is not None]


def page_view():
    views = page_views()
    return max(views, key=lambda v: v.width() * v.height()) if views else None


def render_page(name):
    """The page as TechDraw draws it (scene render at ~5 px/mm), and the view as shown."""
    view = page_view()
    if view is None:
        C.check(f"{name}: page view shown", False)
        return None
    C.shot(name + "-view", view)
    scene = view.scene()
    rect = scene.itemsBoundingRect()
    img = QtGui.QImage(max(1, int(rect.width() * 0.5)), max(1, int(rect.height() * 0.5)), QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    scene.render(painter, QtCore.QRectF(0, 0, img.width(), img.height()), rect)
    painter.end()
    path = os.path.join(C.SHOTS, f"{C.THEME}-{C.NAME}-{name}-page.png")
    img.save(path)
    C.log("SHOT", path)
    return img, view


def wait_views(doc, ms=6000):
    """TechDraw computes view geometry on a thread: wait until no view is still busy."""
    end = QtCore.QTime.currentTime().addMSecs(ms)
    while QtCore.QTime.currentTime() < end:
        C.pump(100)
        busy = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawViewPart")
                and hasattr(o, "isComputing") and o.isComputing()]
        if not busy:
            break
    C.pump(300)


def press_task_ok(what):
    ok = C.task_button("ok")
    C.check(f"{what}: task panel has an OK button", ok is not None and ok.isVisible())
    if ok is not None:
        C.check(f"{what}: OK enabled", ok.isEnabled())
        ok.click()
    C.pump(500)
    C.check(f"{what}: task panel closed after OK", not Gui.Control.activeDialog())


# ----- steps -----------------------------------------------------------------------------------


@C.step(2500)
def setup():
    from bruceworx import drawing_standards as ds

    C.setup_window()
    vault = C.new_vault("Drawing Test")
    s = ds.load(vault.root)
    ds.set_text(s, "company", "Lumen Engineering")
    ds.set_text(s, "standard", "ASME Y14.5-2018")
    ds.set_text(s, "projection", "third")
    ds.set_text(s, "default_sheet", "ANSI_B_Landscape")
    ds.save(vault.root, s)
    src = os.path.join(C.RUN, "src")
    C.make_part(os.path.join(src, "bracket.FCStd"), "Bracket", "Bracket, angle 80 x 40", 80, 40, 6)
    C.close_all()
    numbers, report = C.import_folder(vault.root, src)
    C.check("sample part imported into the temporary vault", len(numbers) == 1 and not report.failed, numbers)
    V["pn"] = numbers.get("bracket.FCStd", "")
    V["root"] = vault.root
    import bruceworx_vaultclient as vc

    vc.changed(refresh=True)


@C.step(1200)
def drawing_workspace_toolbars():
    import bruceworx_workspaces as wsp

    bar = wsp.bar()
    act = bar.workspace_actions.get("TechDrawWorkbench") if bar is not None else None
    C.check("Workspaces menu has Drawing", act is not None and act.text() == "Drawing",
            act.text() if act else None)
    if act is not None:
        act.trigger()
    else:
        Gui.activateWorkbench("TechDrawWorkbench")
    C.pump(1500)
    wb = Gui.activeWorkbench().name() if Gui.activeWorkbench() else ""
    C.check("Drawing workspace runs TechDraw", wb == "TechDrawWorkbench", wb)
    tabs = C.mw().findChild(QtWidgets.QTabBar, "BruceWorxWorkspaceTabs")
    names = [tabs.tabText(i) for i in range(tabs.count())] if tabs else []
    C.check("Drawing workspace shows its tab", "DRAWING" in [n.upper() for n in names], names)
    for name, wanted in TOOLBARS.items():
        visible, acts = C.toolbar_actions(name)
        C.check(f"toolbar '{name}' shown", bool(visible), visible)
        missing = [c for c in wanted if c not in acts or not acts[c][0]]
        C.check(f"toolbar '{name}' has its buttons", not missing, "missing", missing, "has", sorted(acts)[:12])
    in_menu = menu_commands()
    for name, wanted in PACK_HIDDEN.items():
        visible, acts = C.toolbar_actions(name)
        C.note(f"toolbar '{name}' hidden by the pack" if not visible else f"toolbar '{name}' shown")
        missing = [c for c in wanted if c not in in_menu]
        C.check(f"'{name}' commands reachable from the menu", not missing, "missing", missing)
    visible, acts = C.toolbar_actions("TechDraw Pages")
    order = [k for k in acts]
    C.check("New Drawing is in TechDraw Pages, before the stock Insert Page",
            "BruceWorx_NewDrawing" in order and "TechDraw_PageDefault" in order
            and order.index("BruceWorx_NewDrawing") < order.index("TechDraw_PageDefault"), order[:6])
    C.check("New Drawing disabled with no document open", C.command_enabled("BruceWorx_NewDrawing") is False,
            C.command_enabled("BruceWorx_NewDrawing"))
    # Every toolbar the workbench shows: none empty.
    empty = [t.windowTitle() for t in C.mw().findChildren(QtWidgets.QToolBar)
             if t.isVisible() and not [a for a in t.actions() if not a.isSeparator() and a.isVisible()]]
    C.check("no empty toolbar in the Drawing workspace", not empty, empty)
    C.shot("workspace-window")
    for t in C.mw().findChildren(QtWidgets.QToolBar):
        if t.isVisible() and t.windowTitle().startswith("TechDraw"):
            C.readability(t, name=f"toolbar '{t.windowTitle()}'")
            C.shot("toolbar-" + t.windowTitle().replace(" ", "_"), t)
    C.readability(C.mw().statusBar(), min_ratio=3.0, name="status bar")


@C.step(800)
def open_part():
    import bruceworx_vaultclient as vc

    C.answer("Check Out and Open", match="Check out")
    ok = vc.open_from_vault(V["pn"])
    C.pump(1500)
    doc = FreeCAD.ActiveDocument
    C.check("part opened from the vault (checked out)", ok and doc is not None and
            vc.session().status(V["pn"]).code in ("checked_out", "modified"),
            doc.Name if doc else None, vc.session().status(V["pn"]).code)
    V["doc"] = doc
    C.check("New Drawing enabled with a document open", C.command_enabled("BruceWorx_NewDrawing") is True)


@C.step(800)
def right_panel_properties():
    """Light check of the right panel's live Properties tab with the part selected (in polish)."""
    import bruceworx_rightpanel as rp

    doc = V["doc"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Part")
    C.pump(1200)
    C.check("selecting the part brings Properties forward", rp.current_tab() == rp.PROPERTIES, rp.current_tab())
    d = rp.dock(rp.PROPERTIES)
    texts = [w.text() for w in d.findChildren(QtWidgets.QLabel) if w.isVisible() and w.text()] if d else []
    C.log("properties texts", texts[:30])
    placeholder = [t for t in texts if "Coming in this build" in t]
    C.check("Properties tab is live (no placeholder)", d is not None and not placeholder, placeholder)
    C.check("Properties tab shows the part's number", any(V["pn"] in t for t in texts), texts[:12])
    if d is not None:
        C.shot("rightpanel-properties", d)
        C.readability(d, name="Properties tab")
        C.clipped(d, name="Properties tab")
    v = rp.dock("BWVault")
    vt = [w.text() for w in v.findChildren(QtWidgets.QLabel) if w.text()] if v else []
    C.note("Vault tab content:", vt[:6])
    Gui.Selection.clearSelection()


@C.step(600)
def new_drawing_needs_selection():
    Gui.Selection.clearSelection()
    view = Gui.ActiveDocument.ActiveView
    for kind in ("part", "pdbody"):
        try:
            view.setActiveObject(kind, None)
        except Exception:
            pass
    C.answer("OK", match="Select the part", shot="newdrawing-noselection")
    before = len([o for o in V["doc"].Objects if o.isDerivedFrom("TechDraw::DrawPage")])
    Gui.runCommand("BruceWorx_NewDrawing")
    C.pump(600)
    after = len([o for o in V["doc"].Objects if o.isDerivedFrom("TechDraw::DrawPage")])
    said = [m for m in C.MODALS if "Select the part" in m[1]]
    C.check("New Drawing without a selection says what to select", bool(said) and after == before,
            said[-1][1] if said else C.MODALS[-1:])


@C.step(600)
def new_drawing_ansi():
    doc = V["doc"]
    part = doc.getObject("Part")
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, part.Name)
    Gui.runCommand("BruceWorx_NewDrawing")
    C.pump(2500)
    pages = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]
    C.check("New Drawing made a page", len(pages) == 1, [p.Name for p in pages])
    if not pages:
        return
    page = V["ansi"] = pages[0]
    tmpl = page.Template
    C.check("ANSI B sheet (the vault's default)", "ANSI_B_Landscape" in os.path.basename(tmpl.Template),
            os.path.basename(tmpl.Template))
    group = next((v for v in page.Views if v.isDerivedFrom("TechDraw::DrawProjGroup")), None)
    names = sorted(getattr(v, "Type", "") for v in (group.Views if group else []))
    C.check("front, top and right views", names == ["Front", "Right", "Top"], names)
    C.check("third-angle projection (ASME vault)", group is not None and group.ProjectionType == "Third angle",
            group.ProjectionType if group else None)
    texts = dict(tmpl.EditableTexts)
    V["texts_ansi"] = texts
    C.log("title block", texts)
    C.check("title block: part number from the part", texts.get("part_number") == V["pn"], texts.get("part_number"))
    C.check("title block: title from the description", texts.get("title") == "Bracket, angle 80 x 40",
            texts.get("title"))
    C.check("title block: company from the vault", texts.get("company") == "Lumen Engineering", texts.get("company"))
    C.check("title block: revision from the vault", "01" in (texts.get("revision") or ""),
            texts.get("revision"))
    C.check("title block: projection and units", "THIRD" in (texts.get("projection") or "").upper() and
            bool(texts.get("units")), texts.get("projection"), texts.get("units"))
    C.check("title block: scale and sheet", bool(texts.get("scale")) and texts.get("sheet") == "1 of 1",
            texts.get("scale"), texts.get("sheet"))
    mdi = C.mw().findChild(QtWidgets.QMdiArea)
    titles = [w.windowTitle() for w in mdi.subWindowList()] if mdi else []
    C.check("the page opens in its own tab", any("Drawing" in t for t in titles), titles)


@C.step(2500)
def ansi_page_looks():
    doc = V["doc"]
    wait_views(doc)
    got = render_page("ansi")
    if got is None:
        return
    img, view = got
    # The sheet: mostly paper, with dark lines and text on it.
    ratio = C.text_contrast(img, img.rect())
    C.check("page: lines and text stand out from the paper", ratio is not None and ratio >= 4.5, ratio)
    w, h = img.width(), img.height()
    block = img.copy(int(w * 0.55), int(h * 0.75), int(w * 0.45) - 2, int(h * 0.25) - 2)
    path = os.path.join(C.SHOTS, f"{C.THEME}-{C.NAME}-ansi-titleblock.png")
    block.save(path)
    C.log("SHOT", path)
    ratio = C.text_contrast(block, block.rect())
    C.check("title block readable", ratio is not None and ratio >= 4.5, ratio)
    # What the user sees in the window: the view's own background around the paper.
    vimg = view.grab().toImage()
    corner = QtGui.QColor(vimg.pixel(4, 4))
    centre = QtGui.QColor(vimg.pixel(vimg.width() // 2, vimg.height() // 2))
    C.note("page view background", corner.name(), "centre", centre.name())
    C.shot("ansi-window")


@C.step(600)
def hand_typed_kept():
    page = V["ansi"]
    tmpl = page.Template
    tmpl.EditableTexts = dict(tmpl.EditableTexts, title="My own title")
    V["doc"].recompute()
    part = V["doc"].getObject("Part")
    part.Description = "Bracket, angle 80 x 40 (rev)"
    V["doc"].recompute()
    C.pump(400)
    drw().flush()
    texts = dict(tmpl.EditableTexts)
    C.check("hand-typed title kept when the part changes", texts.get("title") == "My own title", texts.get("title"))
    C.check("page lists the hand-typed field", "title" in list(getattr(page, "BWHandTyped", [])),
            list(getattr(page, "BWHandTyped", [])))
    tmpl.EditableTexts = dict(tmpl.EditableTexts, title="")
    V["doc"].recompute()
    C.pump(300)
    drw().flush()
    C.check("emptied title-block field is filled again at once",
            page.Template.EditableTexts.get("title") == "Bracket, angle 80 x 40 (rev)",
            repr(page.Template.EditableTexts.get("title")))
    drw().refresh_document(V["doc"])
    C.check("emptied field is filled on the next fill", page.Template.EditableTexts.get("title") ==
            "Bracket, angle 80 x 40 (rev)", repr(page.Template.EditableTexts.get("title")))


@C.step(600)
def base_view():
    doc = V["doc"]
    page = V["ansi"]
    body = doc.getObject("Body")
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, page.Name)
    Gui.Selection.addSelection(doc.Name, body.Name)
    before = set(o.Name for o in doc.Objects)
    C.check("Insert View enabled with a page and a body selected", C.command_enabled("TechDraw_View") is True)
    Gui.runCommand("TechDraw_View")
    C.pump(1500)
    new = [doc.getObject(n) for n in set(o.Name for o in doc.Objects) - before]
    views = [o for o in new if o.isDerivedFrom("TechDraw::DrawViewPart")]
    C.check("Insert View added a view of the body", len(views) >= 1, [o.TypeId for o in new])
    if Gui.Control.activeDialog():
        C.task_shot("view-task")
        C.readability(C.task_content(), name="Insert View task")
        press_task_ok("Insert View")
    V["base"] = views[0] if views else None
    if V["base"] is not None:
        V["base"].X, V["base"].Y = 90, 70
        doc.recompute()
        C.check("base view is on the page", V["base"] in page.Views, [v.Name for v in page.Views])


@C.step(800)
def projection_group():
    doc = V["doc"]
    page = V["ansi"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, page.Name)
    Gui.Selection.addSelection(doc.Name, "Body")
    before = set(o.Name for o in doc.Objects)
    C.check("Projection Group enabled", C.command_enabled("TechDraw_ProjectionGroup") is True,
            C.command_enabled("TechDraw_ProjectionGroup"))
    Gui.runCommand("TechDraw_ProjectionGroup")
    C.pump(1500)
    new = [doc.getObject(n) for n in set(o.Name for o in doc.Objects) - before]
    groups = [o for o in new if o.isDerivedFrom("TechDraw::DrawProjGroup")]
    C.check("Projection Group added a group", len(groups) == 1, [o.TypeId for o in new])
    if Gui.Control.activeDialog():
        C.task_shot("projgroup-task")
        C.readability(C.task_content(), name="Projection Group task")
        C.clipped(C.task_content(), name="Projection Group task")
        press_task_ok("Projection Group")
    if groups:
        groups[0].X, groups[0].Y = 320, 200
        doc.recompute()


@C.step(1500)
def section_view():
    doc = V["doc"]
    base = V.get("base")
    if base is None:
        C.check("section view: a base view to cut", False)
        return
    wait_views(doc)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, V["ansi"].Name)
    Gui.Selection.addSelection(doc.Name, base.Name)
    C.check("Section View enabled with a view selected", C.command_enabled("TechDraw_SectionView") is True,
            C.command_enabled("TechDraw_SectionView"))
    Gui.runCommand("TechDraw_SectionView")
    C.pump(1200)
    dock = C.tasks_dock()
    right = dock.findChild(QtWidgets.QPushButton, "pbRight") if dock is not None else None
    C.check("Section View task opens with direction buttons", right is not None and right.isVisible())
    if right is not None:
        right.click()
        C.pump(800)
    C.task_shot("section-task")
    C.readability(C.task_content(), name="Section View task")
    press_task_ok("Section View")
    wait_views(doc)
    sections = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawViewSection")]
    C.check("a section view is on the page", len(sections) == 1 and sections[0] in V["ansi"].Views,
            [o.Name for o in sections])


@C.step(1500)
def dimension():
    doc = V["doc"]
    base = V.get("base")
    if base is None:
        C.check("dimension: a view to dimension", False)
        return
    wait_views(doc)
    edges = base.getVisibleEdges() if hasattr(base, "getVisibleEdges") else []
    C.check("base view has visible edges", len(edges) > 0, len(edges))
    target = None
    for i, e in enumerate(edges):
        if len(e.Vertexes) == 2 and abs(e.Vertexes[0].Point.y - e.Vertexes[1].Point.y) < 1e-6 and e.Length > 5:
            target = i
            break
    if target is None:
        target = 0
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, base.Name, f"Edge{target}")
    before = [o.Name for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawViewDimension")]
    C.check("Horizontal Dimension enabled with an edge selected",
            C.command_enabled("TechDraw_HorizontalDimension") is True, C.command_enabled("TechDraw_HorizontalDimension"))
    Gui.runCommand("TechDraw_HorizontalDimension")
    C.pump(1200)
    if Gui.Control.activeDialog():
        C.task_shot("dimension-task")
        press_task_ok("Dimension")
    doc.recompute()
    dims = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawViewDimension") and o.Name not in before]
    C.check("a dimension was added", len(dims) == 1, [o.Name for o in dims])
    if dims:
        V["dim"] = dims[0].Name
        value = dims[0].getRawValue() if hasattr(dims[0], "getRawValue") else None
        C.check("dimension measures the 80 mm edge", value is not None and abs(value - 80) < 0.5, value)


@C.step(800)
def undo_redo():
    doc = V["doc"]
    name = V.get("dim")
    if not name:
        C.check("undo/redo: a dimension to undo", False)
        return
    Gui.runCommand("Std_Undo")
    C.pump(600)
    C.check("Undo removes the dimension", doc.getObject(name) is None)
    Gui.runCommand("Std_Redo")
    C.pump(600)
    C.check("Redo puts it back", doc.getObject(name) is not None)


@C.step(2500)
def page_with_views_looks():
    wait_views(V["doc"])
    Gui.Selection.clearSelection()
    try:
        V["ansi"].ViewObject.doubleClicked()
    except Exception:
        pass
    C.pump(800)
    Gui.SendMsgToActiveView("ViewFit")
    C.pump(800)
    got = render_page("views")
    if got is not None:
        img, _view = got
        ratio = C.text_contrast(img, img.rect())
        C.check("page with views and dimension readable", ratio is not None and ratio >= 4.5, ratio)
    C.shot("views-window")


@C.step(600)
def new_drawing_iso():
    from bruceworx import drawing_standards as ds

    s = ds.load(V["root"])
    ds.set_text(s, "standard", "ISO GPS")
    ds.set_text(s, "projection", "first")
    ds.set_text(s, "tolerance_mode", "iso2768")
    ds.set_text(s, "default_sheet", "ISO_A3_Landscape")
    ds.save(V["root"], s)
    doc = V["doc"]
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Part")
    Gui.runCommand("BruceWorx_NewDrawing")
    C.pump(2500)
    pages = [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]
    C.check("second New Drawing made a second page", len(pages) == 2, [p.Name for p in pages])
    if len(pages) < 2:
        return
    page = V["iso"] = [p for p in pages if p is not V["ansi"]][0]
    C.check("ISO A3 sheet (the vault's new default)", "ISO_A3_Landscape" in os.path.basename(page.Template.Template),
            os.path.basename(page.Template.Template))
    group = next((v for v in page.Views if v.isDerivedFrom("TechDraw::DrawProjGroup")), None)
    C.check("first-angle projection (ISO vault)", group is not None and group.ProjectionType == "First angle",
            group.ProjectionType if group else None)
    texts = dict(page.Template.EditableTexts)
    C.log("iso title block", texts)
    C.check("ISO title block: part number and sheet 2 of 2",
            texts.get("part_number") == V["pn"] and texts.get("sheet") == "2 of 2", texts.get("part_number"),
            texts.get("sheet"))
    C.check("ISO title block: ISO 2768 tolerances", "2768" in " ".join(texts.get(f"tol{i}", "") for i in range(1, 5)),
            [texts.get(f"tol{i}") for i in range(1, 5)])
    drw().flush()
    C.check("first page's sheet now 1 of 2", V["ansi"].Template.EditableTexts.get("sheet") == "1 of 2",
            V["ansi"].Template.EditableTexts.get("sheet"))


@C.step(3000)
def iso_page_looks():
    wait_views(V["doc"])
    got = render_page("iso")
    if got is not None:
        img, _view = got
        ratio = C.text_contrast(img, img.rect())
        C.check("ISO page readable", ratio is not None and ratio >= 4.5, ratio)
    C.shot("iso-window")


@C.step(500)
def all_templates_load():
    """Every sheet New Drawing can pick loads and has the title-block fields."""
    from bruceworx import drawing_standards as ds

    doc = FreeCAD.newDocument("Sheets")
    bad = []
    for sheet in ds.SHEETS:
        page = doc.addObject("TechDraw::DrawPage", "P")
        tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "T")
        tmpl.Template = drw().template_path(sheet)
        page.Template = tmpl
        doc.recompute()
        keys = set(tmpl.EditableTexts)
        missing = {"part_number", "title", "revision", "company", "scale", "sheet"} - keys
        if missing or not os.path.isfile(tmpl.Template):
            bad.append((sheet, sorted(missing)))
    C.check(f"all {len(ds.SHEETS)} sheets load with their title-block fields", not bad, bad)
    Gui.getDocument(doc.Name).Modified = False
    FreeCAD.closeDocument(doc.Name)


@C.step(500)
def save_drawing():
    doc = V["doc"]
    before = os.path.getmtime(doc.FileName)
    n = len(C.MODALS)
    Gui.getDocument(doc.Name).ActiveView  # noqa: B018
    Gui.ActiveDocument = Gui.getDocument(doc.Name)
    Gui.runCommand("Std_Save")
    C.pump(1500)
    C.check("part with its drawings saves (checked out)", os.path.getmtime(doc.FileName) > before and
            len(C.MODALS) == n, C.MODALS[n:])
    C.note("modified after save:", Gui.getDocument(doc.Name).Modified)


C.SAY.update({'setup': 'Make a temporary vault (ASME, ANSI B) and import a bracket part into it', 'drawing_workspace_toolbars': 'Switch to the Drawing workspace: expect the TechDraw toolbars with New Drawing first', 'open_part': 'Open the bracket from the vault and check it out', 'right_panel_properties': "Select the part: expect the right panel's Properties tab with its part number", 'new_drawing_needs_selection': 'New Drawing with nothing selected: expect a message saying to select a part', 'new_drawing_ansi': 'New Drawing on the part: expect an ANSI B sheet, 3 views and a filled title block', 'ansi_page_looks': 'Look at the drawing page: lines and title block must stand out from the paper', 'hand_typed_kept': 'Type a title by hand, change the part: expect the typed title to stay', 'base_view': 'Insert View of the body and press OK', 'projection_group': 'Insert a Projection Group and press OK', 'section_view': 'Section View of the base view: pick a direction and press OK', 'dimension': 'Select an edge and add a horizontal dimension: expect 80 mm', 'undo_redo': 'Undo and Redo the dimension', 'page_with_views_looks': 'Look at the page with all its views', 'new_drawing_iso': 'Switch the vault to ISO, New Drawing again: expect ISO A3, first angle, sheet 2 of 2', 'iso_page_looks': 'Look at the ISO page', 'all_templates_load': 'Load every sheet template: each must have its title-block fields', 'save_drawing': 'Save the part with its drawings (checked out)'})
C.run()
