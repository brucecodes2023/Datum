# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drawing title blocks that fill themselves (DRW-01), and New Drawing.

A TechDraw page whose template is one of ours (Templates/, made by
make_templates.py) gets its title-block fields from:

* the part: the first view's source, raised to its root App::Part or body
  (the nearest object carrying a PartNumber wins; an App::Link is followed
  into the model's own file, so a drawing kept in its own file gets its
  P/N): title (Description, else the label), part number, revision,
  material, finish, mass;
* the open vault (Preferences/BruceWorx/VaultPath): company, logo, standard
  note, general tolerances, units and projection
  (bruceworx.drawing_standards), the revision as the vault has it, and who
  drew, checked and approved the item, with dates, from its journal;
* the page: scale and sheet "n of m".

Units (PDM-09): a drawing follows its MODEL's document units. An inch model
gets the vault's inch profile (ANSI B, ASME, X.X ±.1 / X.XX ±.01 /
X.XXX ±.005, dimensions "%.3f"), a mm model the mm profile ("%.2w"). The
filler sets the FormatSpec of dimensions still on a default format, and a
units change of the model's document refills its drawings. A sheet change
(:func:`set_sheet`) keeps every filled and typed field.

Hand-typed values are kept. The page remembers what it wrote last
(``BWFilled``, a hidden map field -> text); a field showing anything else
was typed by the user and is left alone (listed in ``BWHandTyped``). A field
the user empties is filled again.

The logo and the projection symbol are not text: the page uses a copy of the
template with the logo embedded (a data: URI, so the .FCStd carries it) and
the other projection symbol hidden (:func:`customize_svg`). The copy is
written to FreeCAD's cache folder; TechDraw embeds it in the document.

A document observer fills pages when a part's properties, a view's source
or the page change, and when a document is activated (the vault may have
moved on). Switch: Preferences -> BruceWorx -> "Fill drawing title blocks"
(``BruceWorx/DrawingAutoFill``, default on).
"""

import base64
import hashlib
import os
import re

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_AUTOFILL = "DrawingAutoFill"
COMMAND = "BruceWorx_NewDrawing"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Templates")
FILLED_PROP = "BWFilled"
TYPED_PROP = "BWHandTyped"
# Part properties whose change refills the drawings that show the part.
PART_PROPS = ("PartNumber", "Revision", "LifecycleState", "Description", "MaterialName", "Finish", "Mass", "Label")
PAGE_PROPS = ("Views", "Scale", "Template")
VIEW_PROPS = ("Source", "XSource", "Scale")
LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}

_state = {"installed": False, "observer": None, "pending": set(), "scheduled": False}


def _standards_module():
    from bruceworx import drawing_standards

    return drawing_standards


# --- template files ------------------------------------------------------------------------


def template_path(sheet):
    """Our template file for a sheet key (drawing_standards.SHEETS)."""
    return os.path.join(TEMPLATE_DIR, _standards_module().template_name(sheet))


def is_ours(template):
    """Whether a page template object uses one of our templates (or a customised copy)."""
    name = getattr(template, "Template", "") if template is not None else ""
    return _standards_module().sheet_of_template(name) is not None


def customize_svg(svg, logo_href=None, first_angle=False):
    """The template text with the logo slot holding ``logo_href`` (an image href, e.g. a data:
    URI) and the projection symbol switched. Pure; the slot and symbol groups are written by
    make_templates.py, one element per line."""

    def logo(match):
        if not logo_href:
            return match.group(0)
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(0)))
        return (f'<image id="bw-logo" x="{attrs.get("x", "0")}" y="{attrs.get("y", "0")}" '
                f'width="{attrs.get("width", "25")}" height="{attrs.get("height", "10")}" '
                f'preserveAspectRatio="xMaxYMid meet" xlink:href="{logo_href}"/>')

    svg = re.sub(r'<rect id="bw-logo-slot"[^>]*/>', logo, svg, count=1)
    show, hide = ("bw-proj-first", "bw-proj-third") if first_angle else ("bw-proj-third", "bw-proj-first")
    svg = re.sub(rf'<g id="{show}" display="none">', f'<g id="{show}">', svg)
    svg = re.sub(rf'<g id="{hide}">', f'<g id="{hide}" display="none">', svg)
    return svg


def logo_href(path):
    """A data: URI of a logo file, or None when it cannot be read."""
    mime = LOGO_TYPES.get(os.path.splitext(path or "")[1].lower())
    if not mime or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def variant_of(sheet, href, first_angle):
    """A short id of a customised template: sheet, logo and projection."""
    if not href and not first_angle:
        return ""
    digest = hashlib.sha256(f"{sheet}|{href or ''}|{first_angle}".encode()).hexdigest()
    return digest[:12]


def _cache_dir():
    import FreeCAD

    base = getattr(FreeCAD, "getUserCachePath", FreeCAD.getUserAppDataDir)()
    path = os.path.join(base, "BruceWorx", "templates")
    os.makedirs(path, exist_ok=True)
    return path


def variant_path(sheet, href, first_angle):
    """The template file a page with these settings uses (written to the cache when needed)."""
    variant = variant_of(sheet, href, first_angle)
    if not variant:
        return template_path(sheet)
    path = os.path.join(_cache_dir(), f"{sheet}-{variant}.svg")
    if not os.path.isfile(path):
        with open(template_path(sheet), encoding="utf-8") as handle:
            text = customize_svg(handle.read(), href, first_angle)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    return path


def template_defaults(sheet):
    """field -> default text of our template (the text TechDraw starts a page with)."""
    try:
        with open(template_path(sheet), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return {}
    return {name: value for name, value in
            re.findall(r'freecad:editable="([^"]+)"[^>]*><tspan[^>]*>([^<]*)</tspan>', text)}


# --- the vault -------------------------------------------------------------------------------


def vault_root():
    """The open vault's folder, or ""."""
    import FreeCAD

    path = FreeCAD.ParamGet(PREFS_PATH).GetString("VaultPath", "").strip()
    if not path:
        return ""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    try:
        names = os.listdir(path)
    except OSError:
        return ""
    return path if any(n.endswith(".bwvault") for n in names) else ""


def standards(root=None):
    """The open vault's drawing standards (the defaults without a vault)."""
    ds = _standards_module()
    root = vault_root() if root is None else root
    return ds.load(root) if root else ds.DrawingStandards()


def _vault_record(root, part_number):
    """(revision code, state, signatures) of an item in the vault, or None."""
    if not root or not part_number:
        return None
    try:
        from bruceworx.pdm_folder import FolderVault

        vault = FolderVault(root)
        item = vault.items.get(part_number)
        if item is None:
            return None
        sign = _standards_module().signatures(vault._read_events(), part_number)
        return item.latest.code, item.state, sign
    except Exception as exc:  # noqa: BLE001 -- an unreadable vault must not stop the drawing
        import FreeCAD

        FreeCAD.Console.PrintLog(f"BruceWorx drawing: vault not read: {exc}\n")
        return None


# --- the part a page shows ----------------------------------------------------------------------


def _views(page):
    return [v for v in getattr(page, "Views", None) or [] if v is not None]


def _sources(view):
    """Source objects of a view or projection group (Source, then XSource links)."""
    found = list(getattr(view, "Source", None) or [])
    found += [o for o in getattr(view, "XSource", None) or [] if o is not None]
    if not found and view.isDerivedFrom("TechDraw::DrawProjGroup"):
        for child in getattr(view, "Views", None) or []:
            found += list(getattr(child, "Source", None) or [])
    return found


def _chain(obj):
    """obj and the groups above it, innermost first."""
    out = [obj]
    seen = {obj.Name}
    current = obj
    while True:
        parent = None
        try:
            parent = current.getParentGeoFeatureGroup()
        except Exception:  # noqa: BLE001
            parent = None
        if parent is None:
            parents = [p for p in getattr(current, "InList", []) or []
                       if p.isDerivedFrom("App::Part") and current in (getattr(p, "Group", None) or [])]
            parent = parents[0] if parents else None
        if parent is None or parent.Name in seen:
            return out
        out.append(parent)
        seen.add(parent.Name)
        current = parent


def _follow_link(obj):
    """The object a link (or chain of links) stands for, in its own document; obj itself otherwise."""
    seen = set()
    while obj is not None and obj.Name + "@" + obj.Document.Name not in seen:
        seen.add(obj.Name + "@" + obj.Document.Name)
        linked = None
        try:
            if "LinkedObject" in obj.PropertiesList or obj.isDerivedFrom("App::Link"):
                linked = obj.getLinkedObject(True)
        except Exception:  # noqa: BLE001
            linked = None
        if linked is None or linked is obj:
            return obj
        obj = linked
    return obj


def part_of(obj):
    """The object that stands for the part: the nearest one with a PartNumber, else the
    root App::Part, else a PartDesign body, else obj. A link (a drawing in its own file,
    PDM-09) is followed into the model's document first."""
    obj = _follow_link(obj)
    chain = _chain(obj)
    for candidate in chain:
        if "PartNumber" in candidate.PropertiesList:
            return candidate
    parts = [c for c in chain if c.isDerivedFrom("App::Part")]
    if parts:
        return parts[-1]
    bodies = [c for c in chain if c.isDerivedFrom("PartDesign::Body")]
    return bodies[0] if bodies else obj


def part_of_page(page):
    for view in _views(page):
        sources = _sources(view)
        if sources:
            return part_of(sources[0])
    return None


def part_values(obj, root=""):
    """Title-block values of a part (drawing_standards.title_block_fields ``part``), plus
    the vault's signatures under "sign"."""
    props = obj.PropertiesList

    def text(name):
        return str(getattr(obj, name, "") or "").strip() if name in props else ""

    values = {
        "title": text("Description") or obj.Label,
        "part_number": text("PartNumber"),
        "revision": text("Revision"),
        "state": text("LifecycleState"),
        "material": text("MaterialName"),
        "finish": text("Finish"),
    }
    if not values["material"]:
        try:
            from bruceworx import partprops

            values["material"] = partprops.computed_material(obj) or ""
        except Exception:  # noqa: BLE001
            pass
    mass = getattr(obj, "Mass", None) if "Mass" in props else None
    try:
        values["mass_kg"] = mass.getValueAs("kg").Value if mass is not None else None
    except Exception:  # noqa: BLE001
        values["mass_kg"] = None
    record = _vault_record(root, values["part_number"])
    values["sign"] = {}
    if record:
        values["revision"], values["state"], values["sign"] = record
    return values


def model_units(page, part=None):
    """"mm" or "in": the units of the page's model (its document), else of the page's document."""
    import bruceworx_units

    part = part_of_page(page) if part is None else part
    return bruceworx_units.document_units(part.Document if part is not None else page.Document)


def page_values(page):
    """scale and sheet "n of m" of a page."""
    ds = _standards_module()
    scale = getattr(page, "Scale", 1.0)
    views = _views(page)
    if views and hasattr(views[0], "Scale"):
        scale = views[0].Scale
    pages = [o for o in page.Document.Objects if o.isDerivedFrom("TechDraw::DrawPage")]
    index = pages.index(page) + 1 if page in pages else 1
    return {"scale": ds.scale_text(scale), "sheet": f"{index} of {max(len(pages), 1)}"}


# --- filling -------------------------------------------------------------------------------------


def _map_prop(page, name, doc, kind="App::PropertyMap"):
    if name not in page.PropertiesList:
        page.addProperty(kind, name, "BruceWorx", doc)
        page.setEditorMode(name, 2)  # hidden
    return getattr(page, name)


def page_standards(page, root=None, part=None):
    """The vault's standards as this page's model sees them (its units' profile, PDM-09)."""
    ds = _standards_module()
    root = vault_root() if root is None else root
    return ds.profile(standards(root), model_units(page, part))


def wanted_fields(page, root=None):
    """field -> text the vault, part and page give for a page (no writing)."""
    ds = _standards_module()
    root = vault_root() if root is None else root
    part = part_of_page(page)
    settings = page_standards(page, root, part)
    values = part_values(part, root) if part is not None else {}
    return ds.title_block_fields(settings, root or None, values, values.get("sign"), page_values(page))


def _switch_template(template, path):
    """Point a template at another file of ours and keep every text the sheet had (a bare
    ``Template`` change drops them all, PDM-09 spike)."""
    texts = dict(template.EditableTexts)
    template.Template = path
    merged = dict(template.EditableTexts)
    merged.update({k: v for k, v in texts.items() if k in merged})
    template.EditableTexts = merged


def _ensure_variant(page, template, settings, root, sheet=None):
    """Point the template at the copy with this vault's logo and projection (and ``sheet`` when
    given); keeps the texts."""
    ds = _standards_module()
    sheet = sheet or ds.sheet_of_template(template.Template)
    href = logo_href(ds.logo_path(root, settings)) if root and settings.logo else None
    first = settings.projection == "first"
    variant = variant_of(sheet, href, first)
    wanted = f"{sheet}-{variant}.svg" if variant else ds.template_name(sheet)
    if os.path.basename(template.Template or "") == wanted:
        return False
    _switch_template(template, variant_path(sheet, href, first))
    return True


def set_sheet(page, sheet, root=None):
    """Put the page on another of our sheets (ANSI B -> ISO A3): filled and hand-typed fields
    survive, the title block is filled again. Returns the fill report."""
    ds = _standards_module()
    template = getattr(page, "Template", None)
    if template is None or not is_ours(template) or sheet not in ds.SHEETS:
        return None
    root = vault_root() if root is None else root
    _ensure_variant(page, template, page_standards(page, root), root, sheet)
    return fill_page(page, root)


def _dimensions(page):
    return [v for v in _views(page) if v.isDerivedFrom("TechDraw::DrawViewDimension")]


def apply_dimension_formats(page, units):
    """Dimensions on a default format take the profile's: "%.3f" (and "%+.3f" tolerances) on an
    inch drawing, "%.2w" on a mm one. A format the user typed is left alone. Returns the changed."""
    ds = _standards_module()
    changed = []
    defaults = set(ds.DIMENSION_FORMATS) | {_techdraw_default_format(), _techdraw_default_format(True)}
    for dim in _dimensions(page):
        for prop, tolerance in (("FormatSpec", False), ("FormatSpecOverTolerance", True),
                                ("FormatSpecUnderTolerance", True)):
            if prop not in dim.PropertiesList:
                continue
            wanted = ds.dimension_format(units, tolerance)
            have = str(getattr(dim, prop) or "")
            if have != wanted and have in defaults:
                setattr(dim, prop, wanted)
                changed.append(dim.Name)
    return sorted(set(changed))


def _techdraw_default_format(tolerance=False):
    import FreeCAD

    spec = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions").GetString(
        "formatSpec", "%.2w")
    return spec.replace("%", "%+", 1) if tolerance else spec


def fill_page(page, root=None):
    """Fill one page's title block. Returns {"written": [...], "kept": [...]} or None when the
    page does not use one of our templates."""
    ds = _standards_module()
    template = getattr(page, "Template", None)
    if template is None or not template.isDerivedFrom("TechDraw::DrawSVGTemplate") or not is_ours(template):
        return None
    root = vault_root() if root is None else root
    settings = page_standards(page, root)
    _ensure_variant(page, template, settings, root)
    apply_dimension_formats(page, settings.units)
    wanted = wanted_fields(page, root)
    current = dict(template.EditableTexts)
    filled = dict(_map_prop(page, FILLED_PROP, "Title-block fields filled automatically (field -> text written)"))
    sheet = ds.sheet_of_template(template.Template)
    texts, new_filled, kept = ds.merge_fields(current, wanted, filled, template_defaults(sheet))
    if texts != current:
        template.EditableTexts = texts
    if new_filled != filled:
        setattr(page, FILLED_PROP, new_filled)
    if TYPED_PROP not in page.PropertiesList:
        page.addProperty("App::PropertyStringList", TYPED_PROP, "BruceWorx", "Title-block fields typed by hand")
        page.setEditorMode(TYPED_PROP, 2)
    if list(getattr(page, TYPED_PROP)) != kept:
        setattr(page, TYPED_PROP, kept)
    written = sorted(k for k in texts if texts[k] != current.get(k))
    return {"written": written, "kept": kept}


def our_pages(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage") and is_ours(getattr(o, "Template", None))]


def refresh_document(doc):
    """Fill every page of ours in a document; returns the pages changed."""
    changed = []
    for page in our_pages(doc):
        report = fill_page(page)
        if report and report["written"]:
            changed.append(page.Name)
    return changed


def refresh_all():
    """Fill the pages of every open document (after Vault Settings changes the standards)."""
    import FreeCAD

    if not enabled():
        return []
    out = []
    for doc in FreeCAD.listDocuments().values():
        try:
            out += refresh_document(doc)
        except Exception as exc:  # noqa: BLE001
            FreeCAD.Console.PrintWarning(f"BruceWorx drawing: {doc.Label}: {exc}\n")
    try:
        import bruceworx_units

        bruceworx_units.refresh()  # the status bar's " · mm" / " · inch" (PDM-09)
    except Exception:  # noqa: BLE001
        pass
    return out


def enabled():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_AUTOFILL, True)


# --- observer ---------------------------------------------------------------------------------


class _Observer:
    def __init__(self):
        self.busy = False

    def _mark(self, doc):
        _state["pending"].add(doc.Name)
        _schedule()

    def slotChangedObject(self, obj, prop):
        if self.busy or _state.get("flushing"):
            return
        try:
            doc = obj.Document
            if doc.Restoring or doc.Transacting or not enabled():
                return
            if obj.isDerivedFrom("TechDraw::DrawPage"):
                if prop in PAGE_PROPS:
                    self._mark(doc)
            elif obj.isDerivedFrom("TechDraw::DrawView"):
                if prop in VIEW_PROPS:
                    self._mark(doc)
            elif prop in PART_PROPS and not obj.isDerivedFrom("TechDraw::DrawTemplate"):
                if any(True for _p in our_pages(doc)):
                    self._mark(doc)
        except Exception as exc:  # noqa: BLE001 -- never break the edit that triggered this
            import FreeCAD

            FreeCAD.Console.PrintLog(f"BruceWorx drawing: {exc}\n")

    def slotActivateDocument(self, doc):
        if not doc.Restoring and enabled():
            self._mark(doc)

    def slotChangedDocument(self, doc, prop):
        """The model's units changed: its drawings (in this or another open file) refill."""
        if prop != "UnitSystem" or self.busy or _state.get("flushing"):
            return
        try:
            import FreeCAD

            if doc.Restoring or not enabled():
                return
            for other in FreeCAD.listDocuments().values():
                if any(True for _p in our_pages(other)):
                    self._mark(other)
        except Exception as exc:  # noqa: BLE001
            import FreeCAD

            FreeCAD.Console.PrintLog(f"BruceWorx drawing: {exc}\n")


def _schedule():
    import FreeCAD

    if not FreeCAD.GuiUp:
        flush()
        return
    if _state["scheduled"]:
        return
    _state["scheduled"] = True
    from PySide import QtCore

    QtCore.QTimer.singleShot(0, flush)


def flush():
    """Fill the pages of the documents marked since the last flush."""
    import FreeCAD

    _state["scheduled"] = False
    if _state.get("flushing"):
        return
    pending, _state["pending"] = _state["pending"], set()
    _state["flushing"] = True
    try:
        for name in pending:
            doc = FreeCAD.listDocuments().get(name)
            if doc is None or doc.Restoring:
                continue
            try:
                refresh_document(doc)
            except Exception as exc:  # noqa: BLE001
                FreeCAD.Console.PrintWarning(f"BruceWorx drawing: {doc.Label}: {exc}\n")
    finally:
        _state["flushing"] = False


class _VaultPathObserver:
    """Another vault opened (BruceWorx/VaultPath changed): open drawings take its standards."""

    def onChange(self, _group, reason):
        if reason == "VaultPath" and enabled():
            import FreeCAD

            for doc in FreeCAD.listDocuments().values():
                _state["pending"].add(doc.Name)
            _schedule()


def install_observer():
    import FreeCAD

    if _state["observer"] is None:
        _state["observer"] = _Observer()
        FreeCAD.addDocumentObserver(_state["observer"])
        _state["param_observer"] = _VaultPathObserver()
        FreeCAD.ParamGet(PREFS_PATH).Attach(_state["param_observer"])
    return _state["observer"]


# --- New Drawing --------------------------------------------------------------------------------


def choose_sheet(settings, sheet=None):
    """The sheet of a new drawing: the one asked for, else the vault's default, else ANSI B."""
    ds = _standards_module()
    for candidate in (sheet, getattr(settings, "default_sheet", None), ds.DEFAULT_SHEET):
        if candidate in ds.SHEETS:
            return candidate
    return ds.DEFAULT_SHEET


def new_drawing(part, sheet=None, root=None, doc=None):
    """A page with our template and front, top and right views of ``part`` (third angle unless
    the vault says first), on the profile of the part's units: an inch part gets the vault's
    inch sheet, ASME tolerances and "%.3f" dimensions (PDM-09). ``doc``: the drawing's own
    document (default the part's); a part in another file is linked through the group's XSource.
    Returns the page."""
    import FreeCAD

    ds = _standards_module()
    doc = part.Document if doc is None else doc
    root = vault_root() if root is None else root
    import bruceworx_units

    settings = ds.profile(standards(root), bruceworx_units.document_units(part.Document))
    sheet = choose_sheet(settings, sheet)
    cross = doc is not part.Document
    if cross and (not doc.FileName or not part.Document.FileName):
        raise ValueError("A drawing in its own file links the model's file: save both documents first.")
    page = doc.addObject("TechDraw::DrawPage", "Drawing")
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = template_path(sheet)
    page.Template = template
    page.Label = f"Drawing {part.Label}"
    group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
    page.addView(group)
    group.ProjectionType = "First angle" if settings.projection == "first" else "Third angle"
    if cross:
        # An App::Link in Source projects nothing across files; XSource is TechDraw's own
        # cross-document link.
        group.XSource = [part]
    else:
        group.Source = [part]
    group.addProjection("Front")
    anchor = group.Anchor
    anchor.Direction = FreeCAD.Vector(0, -1, 0)
    if "XDirection" in anchor.PropertiesList:
        anchor.XDirection = FreeCAD.Vector(1, 0, 0)
    group.addProjection("Top")
    group.addProjection("Right")
    # Centre the views across the sheet, above the title block (56 mm over the 10 mm margin).
    _label, width, height = ds.SHEETS[sheet]
    group.X = width / 2.0
    group.Y = (height - 10.0 + 66.0) / 2.0
    if "ScaleType" in group.PropertiesList:
        group.ScaleType = "Automatic"
    doc.recompute()
    fill_page(page, root)
    doc.recompute()
    return page


def _selected_part():
    import FreeCADGui as Gui

    selection = Gui.Selection.getSelection()
    if selection:
        return part_of(selection[0])
    view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
    if view is not None and hasattr(view, "getActiveObject"):
        for kind in ("part", "pdbody"):
            found = view.getActiveObject(kind)
            if found is not None:
                return part_of(found)
    return None


class _NewDrawingCommand:
    def GetResources(self):
        return {
            "Pixmap": "actions/TechDraw_PageDefault",
            "MenuText": "New Drawing",
            "ToolTip": "A drawing of the selected part on the vault's default sheet, with front, top and "
                       "right views and a title block that fills itself",
        }

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        import FreeCAD
        import FreeCADGui as Gui

        part = _selected_part()
        if part is None:
            from PySide import QtGui

            QtGui.QMessageBox.information(Gui.getMainWindow(), "New Drawing",
                                          "Select the part or body to draw first.")
            return
        doc = part.Document
        doc.openTransaction("New Drawing")
        try:
            page = new_drawing(part)
        except Exception as exc:  # noqa: BLE001
            doc.abortTransaction()
            FreeCAD.Console.PrintError(f"New Drawing: {exc}\n")
            return
        doc.commitTransaction()
        try:
            page.ViewObject.doubleClicked()
            from PySide import QtCore

            QtCore.QTimer.singleShot(300, lambda: Gui.SendMsgToActiveView("ViewFit"))
        except Exception:  # noqa: BLE001
            pass


class _Manipulator:
    """Puts New Drawing before TechDraw's own new-page commands (toolbar and menu)."""

    def modifyToolBars(self):
        return [{"insert": COMMAND, "toolItem": "TechDraw_PageDefault"}]

    def modifyMenuBar(self):
        return [{"insert": COMMAND, "menuItem": "TechDraw_PageDefault"}]


def install():
    """Register New Drawing, put it in the Drawing workspace and the BruceWorx menu, and start
    the title-block observer."""
    if _state["installed"]:
        return
    import FreeCADGui as Gui

    _state["installed"] = True
    Gui.addCommand(COMMAND, _NewDrawingCommand())
    install_observer()
    try:
        _state["manipulator"] = _Manipulator()
        Gui.addWorkbenchManipulator(_state["manipulator"])
    except Exception:  # noqa: BLE001
        pass
    try:
        import bruceworx_menu
        from PySide import QtGui

        res = _NewDrawingCommand().GetResources()
        action = QtGui.QAction(QtGui.QIcon(":/icons/actions/TechDraw_PageDefault.svg"), res["MenuText"],
                               Gui.getMainWindow())
        action.setToolTip(res["ToolTip"])
        action.triggered.connect(lambda _checked=False: Gui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, "BruceWorxMenu_" + COMMAND)
    except Exception as exc:  # noqa: BLE001 -- FreeCAD console only
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx: New Drawing menu entry not added: {exc}\n")


# --- Vault Settings: the Drawing standards box --------------------------------------------------


class StandardsEditor:
    """The widgets of Vault Settings' "Drawing standards" box (bruceworx_vaultwizard builds the box
    and calls :meth:`texts`, :meth:`show`, :meth:`commit`). ``changed`` is called on every edit."""

    def __init__(self, root, changed=None):
        from PySide import QtCore, QtWidgets

        ds = _standards_module()
        self.ds, self.root, self._changed = ds, root, changed
        self._loading, self._ready = True, False  # no change signals while the widgets are made
        saved = ds.load(root) if root else ds.DrawingStandards()
        self.logo_name = saved.logo
        self.logo_source = None  # a file chosen now; copied into the vault on Save
        self.widget = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.widget)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QtWidgets.QFormLayout()
        outer.addLayout(form)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.company = QtWidgets.QLineEdit()
        self.company.setPlaceholderText(ds.vault_name(root) if root else "")
        self.company.setToolTip("Company name in the title block; empty: the vault's name.")
        form.addRow("Company", self.company)
        logo_row = QtWidgets.QHBoxLayout()
        self.logo_label = QtWidgets.QLabel()
        self.logo_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        choose = QtWidgets.QPushButton("Choose…")
        choose.setToolTip("An SVG or PNG logo; it is copied into the vault folder when you save.")
        choose.clicked.connect(self._choose_logo)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self._clear_logo)
        logo_row.addWidget(self.logo_label, 1)
        logo_row.addWidget(choose)
        logo_row.addWidget(clear)
        form.addRow("Logo", logo_row)

        self.combos = []

        def combo(key, tip):
            box = QtWidgets.QComboBox()
            for value, text in ds.choices(key):
                box.addItem(text, value)
            box.setToolTip(tip)
            box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(8)
            box.currentIndexChanged.connect(self._edited)
            self.combos.append(box)
            return box

        self.standard = combo("standard", "The dimensioning and tolerancing standard the note names.")
        form.addRow("Standard", self.standard)
        self.projection = combo("projection", "Third angle (ASME) or first angle (ISO): the symbol, the "
                                              "note and how New Drawing places the views.")
        form.addRow("Projection", self.projection)
        self.units = combo("units", "Units the title block states.")
        form.addRow("Units", self.units)
        self.units.setToolTip("The vault's default: new documents and drawings are made in these units "
                              "(each document can be switched from the status bar).")
        self.sheet = combo("default_sheet", "The sheet New Drawing uses for a millimetre model.")
        form.addRow("Sheet (mm)", self.sheet)
        self.mode = combo("tolerance_mode", "General tolerances of mm drawings: by decimal places, an ISO 2768 "
                                            "class, or per drawing. Inch drawings use decimal places (ASME).")
        form.addRow("General tolerances", self.mode)

        self.form = form
        self.tols = {}
        for key, caption in (("tol_1_place", "X.X  ± mm"), ("tol_2_place", "X.XX  ± mm"),
                             ("tol_3_place", "X.XXX  ± mm"), ("tol_angle", "Angles  ±")):
            edit = QtWidgets.QLineEdit()
            edit.setMaximumWidth(80)
            edit.textEdited.connect(self._edited)
            edit.setToolTip(ds.DESCRIPTIONS[key])
            self.tols[key] = edit
            form.addRow(caption, edit)
        self.iso_class = combo("iso2768_class", "ISO 2768 tolerance class.")
        form.addRow("ISO 2768 class", self.iso_class)
        self.in_sheet = combo("in_default_sheet", "The sheet New Drawing uses for an inch model (ASME).")
        form.addRow("Sheet (inch)", self.in_sheet)
        for key, caption in (("in_tol_1_place", "X.X  ± in"), ("in_tol_2_place", "X.XX  ± in"),
                             ("in_tol_3_place", "X.XXX  ± in"), ("in_tol_angle", "Angles  ± (inch)")):
            edit = QtWidgets.QLineEdit()
            edit.setMaximumWidth(80)
            edit.textEdited.connect(self._edited)
            edit.setToolTip(ds.DESCRIPTIONS[key])
            self.tols[key] = edit
            form.addRow(caption, edit)
        self.finish = QtWidgets.QLineEdit()
        self.finish.setPlaceholderText("No note")
        self.finish.setToolTip(ds.DESCRIPTIONS["surface_finish"])
        self.finish.textEdited.connect(self._edited)
        form.addRow("Surface finish Ra", self.finish)
        self.edges = QtWidgets.QLineEdit()
        self.edges.setPlaceholderText("No note (e.g. 0.2-0.5)")
        self.edges.setToolTip(ds.DESCRIPTIONS["break_edges"])
        self.edges.textEdited.connect(self._edited)
        form.addRow("Break sharp edges", self.edges)
        self.company.textEdited.connect(self._edited)

        self.preview = QtWidgets.QLabel()  # short lines, one per note: no wrapping
        self.preview.setObjectName("bwVaultHint")
        self.preview.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.preview.setToolTip("What the title block's notes will say.")
        outer.addWidget(self.preview)
        self.issues = QtWidgets.QLabel()
        self.issues.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.issues.setObjectName("bwVaultIssue")
        self.issues.setWordWrap(True)
        outer.addWidget(self.issues)
        self.show(ds.as_texts(saved))
        self._ready = True

    def _row_visible(self, field, visible):
        if hasattr(self.form, "setRowVisible"):  # Qt 6.4+
            self.form.setRowVisible(field, visible)
            return
        field.setVisible(visible)
        label = self.form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def _set_combo(self, box, value):
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)

    def show(self, texts):
        """Show settings (setting -> text, e.g. CSV rows); unknown or bad values are skipped."""
        ds = self.ds
        current = ds.from_texts(self.texts())[0] if self._ready else ds.DrawingStandards()
        merged, _bad = ds.from_texts(texts, current)
        self._loading = True
        try:
            self.company.setText(merged.company)
            if "logo" in texts:
                self.logo_name, self.logo_source = merged.logo, None
            for key, box in (("standard", self.standard), ("projection", self.projection), ("units", self.units),
                             ("default_sheet", self.sheet), ("tolerance_mode", self.mode),
                             ("iso2768_class", self.iso_class), ("in_default_sheet", self.in_sheet)):
                self._set_combo(box, getattr(merged, key))
            for key, edit in self.tols.items():
                edit.setText(ds.to_text(merged, key))
            self.finish.setText(merged.surface_finish)
            self.edges.setText(merged.break_edges)
        finally:
            self._loading = False
        self._refresh()

    def texts(self):
        """setting -> text of what the box shows (VaultSettings.standards)."""
        out = {
            "company": self.company.text().strip(),
            "logo": self._logo_text(),
            "standard": self.standard.currentData(),
            "projection": self.projection.currentData(),
            "units": self.units.currentData(),
            "default_sheet": self.sheet.currentData(),
            "in_default_sheet": self.in_sheet.currentData(),
            "tolerance_mode": self.mode.currentData(),
            "iso2768_class": self.iso_class.currentData(),
            "surface_finish": self.finish.text().strip(),
            "break_edges": self.edges.text().strip(),
        }
        for key, edit in self.tols.items():
            out[key] = edit.text().strip()
        return {k: ("" if v is None else v) for k, v in out.items()}

    def _logo_text(self):
        if self.logo_source:
            ext = os.path.splitext(self.logo_source)[1].lower()
            return self.ds.LOGO_BASENAME + (".jpg" if ext == ".jpeg" else ext)
        return self.logo_name or ""

    def commit(self):
        """Copy a newly chosen logo into the vault (before the settings are saved)."""
        if self.logo_source and self.root:
            self.logo_name = self.ds.import_logo(self.root, self.logo_source)
            self.logo_source = None

    def problems(self):
        settings, issues = self.ds.from_texts(self.texts())
        return issues + self.ds.validate(settings)

    def _choose_logo(self):
        from PySide import QtWidgets

        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self.widget, "Choose a logo", os.path.expanduser("~"), "Logos (*.svg *.png *.jpg *.jpeg)")
        if path:
            self.logo_source = path
            self._edited()

    def _clear_logo(self):
        self.logo_source, self.logo_name = None, ""
        self._edited()

    def _edited(self, *_args):
        if self._loading:
            return
        self._refresh()
        if self._changed is not None:
            self._changed()

    def _refresh(self):
        ds = self.ds
        mode = self.mode.currentData()
        for key, edit in self.tols.items():
            self._row_visible(edit, mode == "decimal" or key.startswith("in_"))
        self._row_visible(self.iso_class, mode == "iso2768")
        if self.logo_source:
            self.logo_label.setText(os.path.basename(self.logo_source) + " (copied on Save)")
        else:
            self.logo_label.setText(self.logo_name or "None")
        settings, _issues = ds.from_texts(self.texts())
        lines = [line for line in ds.tolerance_lines(ds.profile(settings, "mm")) if line]
        inch = [line for line in ds.tolerance_lines(ds.profile(settings, "in"))[:2] if line]
        self.preview.setText("\n".join(["Title block notes:", ds.standard_note(settings),
                                          ds.projection_text(settings) + " projection", "mm drawings:"] + lines
                                         + ["inch drawings (ASME):"] + inch))
        problems = [i for i in self.problems() if i.level in ("error", "warning")]
        self.issues.setProperty("level", "error" if any(i.level == "error" for i in problems) else "warning")
        self.issues.setText("\n".join(i.text for i in problems))
        self.issues.setVisible(bool(problems))
