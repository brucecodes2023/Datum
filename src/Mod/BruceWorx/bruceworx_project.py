# SPDX-License-Identifier: LGPL-2.1-or-later
"""New Project (BW-PDM-09, plan section 4; built 26 Oct 2026): the file writer and the task panel.

The engine is the hooks add-on's bruceworx/project_setup.py (parse, check, number plan, order of
work). This module does what Create does, in the plan's order, so a failure leaves nothing half-made
in the vault:

1. Check (the preview; nothing written).
2. Write the files in staging (<vault>/.import/project-<id>), children first: a part is an App::Part
   with a Body and the vault's part properties; an assembly is an Assembly::AssemblyObject saved
   *before* its links are made (spike), one App::Link per instance placed in a row, the first child
   grounded; a drawing is its own file with a hidden link to its model and a blank page from the
   vault's template for the project's units (sheet and filled title block; answer 8), with the
   front, top and right views only when asked (the panel's tick box, off by default). Every document
   gets the project's UnitSystem; backups are off.
3. Verify: reopen the top from staging; every link resolves and every count matches the structure.
4. Allocate the numbers (flat, one parent each; drawings as the drawing document type, question 11),
   rename the staged files to <P/N>.FCStd, rewrite their links, write the P/N into each file, then
   create_item for every item and drawing, create_project, and one check-in per file as WIP 01 with
   the comment (parts with their fingerprint, drawings with made_from). A file that fails here leaves
   its number unused and is listed.
5. Open the top assembly and show its Structure.

Runs headless too (``bw-hooks project new --structure cart.txt`` in FreeCADCmd). Entry points in the
window: File > New Project..., BruceWorx > New Project..., the Start page, Cmd-K, the Structure view's
empty state. Insert Views (BruceWorx_InsertViews) adds the three views to a blank drawing later.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass

COMMAND = "BruceWorx_NewProject"
VIEWS_COMMAND = "BruceWorx_InsertViews"
UNIT_CHOICES = (("mm", "Millimetres (mm)"), ("in", "Inches (in)"))
INCH_SHEET = "ANSI_B_Landscape"  # inch drawings go on ANSI B (plan question 4)
MODEL_PROP = "BWModel"  # on a drawing page: the link to the model it shows
LINK_SPACING = 50.0  # mm between the instances of one child in a new assembly (empty parts)
DOCUMENT_PATH = "User parameter:BaseApp/Preferences/Document"
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"

_state = {"installed": False, "panel": None, "file_menu": False}
_classes = None


# ----- units ----------------------------------------------------------------------------------------


def apply_units(doc, units):
    """Give a new document the project's units. The units agent's helper (bruceworx_defaults.apply_units)
    when it is there; else Document.UnitSystem by index (0 Standard mm, 3 Imperial decimal)."""
    units = "in" if str(units or "").lower().startswith("in") else "mm"
    try:
        import bruceworx_defaults

        return bruceworx_defaults.apply_units(doc, units)
    except Exception:
        pass
    try:
        from heuretes_freecad.freecad_io import set_units

        set_units(doc, units)
    except Exception:
        if "UnitSystem" in doc.PropertiesList:
            doc.UnitSystem = 3 if units == "in" else 0
    return units


def sheet_for(units, settings=None):
    """The sheet of a new drawing: inch -> ANSI B; mm -> the vault's default sheet."""
    if str(units).lower().startswith("in"):
        return INCH_SHEET
    sheet = getattr(settings, "default_sheet", "") if settings is not None else ""
    return sheet or "ANSI_B_Landscape"


def library_root(project_root) -> str:
    """Where bought parts are filed: BruceWorx/LibraryPath, else the Standard Libraries vault
    beside this project vault."""
    try:
        import FreeCAD

        path = FreeCAD.ParamGet(PREFS_PATH).GetString("LibraryPath", "").strip()
        if path and os.path.isdir(os.path.expanduser(path)):
            return os.path.expanduser(path)
    except Exception:
        pass
    try:
        from heuretes_freecad import vault_setup

        return vault_setup.library_beside(project_root)
    except Exception:
        return ""


def _library_vault(root):
    path = library_root(root)
    if not path:
        return None
    try:
        from heuretes_freecad.pdm_folder import FolderVault

        return FolderVault(path)
    except Exception:
        return None


def vault_units(root) -> str:
    """The vault's default units (drawing_standards.json), "mm" when unknown."""
    try:
        from heuretes_freecad import drawing_standards as ds

        return ds.load(root).units or "mm"
    except Exception:
        return "mm"


class backups_off:
    """FreeCAD's backup files off while the files are written (the import does the same)."""

    def __enter__(self):
        import FreeCAD

        self.group = FreeCAD.ParamGet(DOCUMENT_PATH)
        present = any(kind == "Boolean" and name == "CreateBackupFiles" for kind, name, _v in (self.group.GetContents() or []))
        self.saved = (present, bool(self.group.GetBool("CreateBackupFiles", True)))
        self.group.SetBool("CreateBackupFiles", False)
        return self

    def __exit__(self, *exc):
        present, value = self.saved
        if present:
            self.group.SetBool("CreateBackupFiles", value)
        else:
            self.group.RemBool("CreateBackupFiles")
        return False


# ----- file writers (FreeCAD, headless-capable) ---------------------------------------------------------


def _modules():
    """TechDraw and Assembly loaded, so their object types exist in FreeCADCmd."""
    for name in ("Part", "TechDraw", "Assembly", "AssemblyGui" if _gui_up() else ""):
        if not name:
            continue
        try:
            __import__(name)
        except Exception:
            pass


def _gui_up():
    import FreeCAD

    return bool(getattr(FreeCAD, "GuiUp", False))


def _schema(vault_root):
    from heuretes_freecad import partprops

    return partprops.load_schema(extra_layers=partprops.vault_layers(vault_root))


def _props(main, schema, description, kind):
    report = schema.apply(main)
    clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
    if clash:
        raise ValueError("; ".join(clash))
    main.Label = description
    if "Description" in main.PropertiesList:
        main.Description = description
    if kind == "cots" and "ItemType" in main.PropertiesList:
        try:
            main.ItemType = "cots"
        except Exception:
            pass


def _stem(path):
    return re.sub(r"[^A-Za-z0-9_]+", "_", os.path.splitext(os.path.basename(path))[0]) or "Document"


def write_part(path, name, units, vault_root, kind="part"):
    """A new part (or bought part): an App::Part holding a Body, the vault's part properties, the
    project's units; saved and closed."""
    import FreeCAD

    _modules()
    doc = FreeCAD.newDocument(_stem(path), hidden=True)
    try:
        doc.Label = name
        apply_units(doc, units)
        main = doc.addObject("App::Part", "Part")
        main.addObject(doc.addObject("PartDesign::Body", "Body"))
        _props(main, _schema(vault_root), name, kind)
        doc.recompute()
        doc.saveAs(path)
    finally:
        FreeCAD.closeDocument(doc.Name)
    return path


def _open_hidden(path):
    import FreeCAD

    real = os.path.realpath(path)
    for doc in FreeCAD.listDocuments().values():
        if doc.FileName and os.path.realpath(doc.FileName) == real:
            return doc, False
    try:
        return FreeCAD.openDocument(path, hidden=True), True
    except TypeError:
        return FreeCAD.openDocument(path), True


def _main(doc):
    from heuretes_freecad import freecad_io

    obj = freecad_io.main_object(doc)
    if obj is None:
        raise ValueError(f"{os.path.basename(doc.FileName)} has no assembly, part or body to link")
    return obj


def write_assembly(path, name, children, units, vault_root):
    """A new assembly: saved first, then one App::Link per instance of each child (``children`` are
    (file path, quantity)), placed in a row, the first child grounded. Saved and closed."""
    import FreeCAD

    _modules()
    doc = FreeCAD.newDocument(_stem(path), hidden=True)
    opened = []
    try:
        doc.Label = name
        apply_units(doc, units)
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        joints = asm.newObject("Assembly::JointGroup", "Joints")
        _props(asm, _schema(vault_root), name, "assembly")
        doc.recompute()
        doc.saveAs(path)  # before the links: a link made before the first save does not resolve (spike)
        offset, first = 0.0, None
        for child_path, qty in children:
            cdoc, was_opened = _open_hidden(child_path)
            if was_opened:
                opened.append(cdoc)
            target = _main(cdoc)
            stem = _stem(child_path)
            for i in range(int(qty)):
                link = asm.newObject("App::Link", f"{stem}_{i + 1}" if qty > 1 else stem)
                link.LinkedObject = target
                link.Label = f"{target.Label} {i + 1}" if qty > 1 else target.Label
                link.Placement = FreeCAD.Placement(FreeCAD.Vector(offset, 0, 0), FreeCAD.Rotation())
                offset += LINK_SPACING
                if first is None:
                    first = link
        if first is not None:
            _ground(joints, first)
        doc.recompute()
        doc.save()
    finally:
        FreeCAD.closeDocument(doc.Name)
        for cdoc in opened:
            try:
                FreeCAD.closeDocument(cdoc.Name)
            except Exception:
                pass
    return path


def _ground(joints, link):
    """Ground the first child, as Insert Link does (Assembly's GroundedJoint)."""
    try:
        import JointObject

        ground = joints.newObject("App::FeaturePython", "GroundedJoint")
        JointObject.GroundedJoint(ground, link)
        if _gui_up() and getattr(ground, "ViewObject", None) is not None:
            JointObject.ViewProviderGroundedJoint(ground.ViewObject)
    except Exception as exc:  # noqa: BLE001 -- an ungrounded first part still assembles
        import FreeCAD

        FreeCAD.Console.PrintLog(f"New Project: first child not grounded: {exc}\n")


def write_drawing(path, model_path, name, units, vault_root, insert_views=False, sheet=None):
    """A drawing in its own file: a hidden link to the model, a page from the vault's template for
    the units (blank: sheet and title block; with ``insert_views`` the front, top and right views).
    Saved and closed."""
    import FreeCAD

    import bruceworx_drawing

    _modules()
    doc = FreeCAD.newDocument(_stem(path), hidden=True)
    opened = []
    try:
        doc.Label = f"Drawing {name}"
        apply_units(doc, units)
        doc.saveAs(path)  # before the link: a link to another file needs the owner saved (spike)
        mdoc, was_opened = _open_hidden(model_path)
        if was_opened:
            opened.append(mdoc)
        target = _main(mdoc)
        link = doc.addObject("App::Link", "Model")
        link.LinkedObject = target
        link.Label = f"{target.Label} (model)"
        doc.recompute()
        settings = bruceworx_drawing.standards(vault_root)
        sheet = sheet or sheet_for(units, settings)
        if insert_views:
            page = bruceworx_drawing.new_drawing(link, sheet, vault_root)
        else:
            page = blank_page(doc, sheet, name, vault_root)
        page.addProperty("App::PropertyLink", MODEL_PROP, "BruceWorx", "The model this drawing shows")
        page.setEditorMode(MODEL_PROP, 2)
        setattr(page, MODEL_PROP, link)
        doc.recompute()
        doc.save()
    finally:
        FreeCAD.closeDocument(doc.Name)
        for mdoc in opened:
            try:
                FreeCAD.closeDocument(mdoc.Name)
            except Exception:
                pass
    return path


def blank_page(doc, sheet, name, vault_root):
    """A page with our template and a filled title block, no views (answer 8)."""
    import bruceworx_drawing

    page = doc.addObject("TechDraw::DrawPage", "Drawing")
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = bruceworx_drawing.template_path(sheet)
    page.Template = template
    page.Label = f"Drawing {name}"
    doc.recompute()
    try:
        bruceworx_drawing.fill_page(page, vault_root)
    except Exception as exc:  # noqa: BLE001 -- the title block fills again when the drawing opens
        import FreeCAD

        FreeCAD.Console.PrintLog(f"New Project: title block not filled: {exc}\n")
    return page


def add_views(page, settings=None, vault_root=None):
    """Insert Views on a blank drawing: the front, top and right views of its model (the page's
    BWModel link), centred above the title block as New Drawing places them. Returns the group."""
    import FreeCAD

    import bruceworx_drawing
    from heuretes_freecad import drawing_standards as ds

    model = getattr(page, MODEL_PROP, None)
    if model is None:
        raise ValueError("this drawing does not name its model (no BWModel link)")
    if any(v.isDerivedFrom("TechDraw::DrawProjGroup") for v in (page.Views or [])):
        raise ValueError("the drawing has its views already")
    doc = page.Document
    root = vault_root if vault_root is not None else bruceworx_drawing.vault_root()
    settings = settings or bruceworx_drawing.standards(root)
    group = doc.addObject("TechDraw::DrawProjGroup", "ProjGroup")
    page.addView(group)
    group.ProjectionType = "First angle" if settings.projection == "first" else "Third angle"
    group.Source = [model]
    group.addProjection("Front")
    anchor = group.Anchor
    anchor.Direction = FreeCAD.Vector(0, -1, 0)
    if "XDirection" in anchor.PropertiesList:
        anchor.XDirection = FreeCAD.Vector(1, 0, 0)
    group.addProjection("Top")
    group.addProjection("Right")
    sheet = ds.sheet_of_template(page.Template.Template) or ds.DEFAULT_SHEET
    _label, width, height = ds.SHEETS[sheet]
    group.X = width / 2.0
    group.Y = (height - 10.0 + 66.0) / 2.0
    if "ScaleType" in group.PropertiesList:
        group.ScaleType = "Automatic"
    doc.recompute()
    try:
        bruceworx_drawing.fill_page(page, root)
    except Exception:
        pass
    doc.recompute()
    return group


def number_file(path, pn, units, vault_root):
    """Write the P/N (and revision 01, the units) into a staged file; a drawing's title block gets
    the number too. Opens the file hidden, saves and closes it."""
    import FreeCAD

    from heuretes_freecad import freecad_io, pdm, release_check

    doc, _opened = _open_hidden(path)
    try:
        obj = freecad_io.main_object(doc)
        if obj is None:
            raise ValueError(f"{os.path.basename(path)} has no assembly, part, body or drawing page to number")
        report = _schema(vault_root).apply(obj)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise ValueError(f"{obj.Name}: {'; '.join(clash)}")
        freecad_io.set_part_number(obj, pn)
        obj.Revision = pdm.first_prerelease()
        apply_units(doc, units)
        for page in [o for o in doc.Objects if o.isDerivedFrom("TechDraw::DrawPage")]:
            template = getattr(page, "Template", None)
            if template is None or "EditableTexts" not in template.PropertiesList:
                continue
            texts = dict(template.EditableTexts)
            for key in list(texts):
                if release_check._norm(key) in release_check.PN_FIELDS:
                    texts[key] = pn
            if texts != dict(template.EditableTexts):
                template.EditableTexts = texts
        doc.recompute()
        doc.save()
    finally:
        FreeCAD.closeDocument(doc.Name)
    return pn


# ----- fingerprints ---------------------------------------------------------------------------------


def fingerprint_of_document(doc):
    """The geometry fingerprint of a part document (product_tree.make_fingerprint), or None when it
    holds no solid yet."""
    from heuretes_freecad import numbering, product_tree

    shapes = []
    for obj in numbering.part_objects(doc):
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull() and shape.Solids:
            shapes.append(shape)
    if not shapes:
        return None
    import Part

    compound = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
    box = compound.BoundBox
    centre = compound.CenterOfMass if hasattr(compound, "CenterOfMass") else compound.CenterOfGravity
    return product_tree.make_fingerprint(compound.Volume, compound.Area, (centre.x, centre.y, centre.z),
                                         (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax),
                                         sum(len(s.Solids) for s in shapes))


def fingerprint_of_file(path):
    import FreeCAD

    doc, opened = _open_hidden(path)
    try:
        return fingerprint_of_document(doc)
    finally:
        if opened:
            FreeCAD.closeDocument(doc.Name)


def checkin_extras(s, pn):
    """What a check-in of ``pn`` records besides the file (PDM-09): for a drawing, made_from over the
    files its work copy links; for a part or bought part, its geometry fingerprint. {} for the rest.
    bruceworx_vaultclient.run_checkin calls this; a failure here never stops the check-in."""
    item = s.vault.items.get(pn)
    if item is None:
        return {}
    if item.item_type == "drawing":
        made_from = s.work.made_from(pn)
        return {"made_from": made_from} if made_from else {}
    if item.item_type in ("part", "cots"):
        path = s.work.path(pn)
        import FreeCAD

        real = os.path.realpath(path)
        doc = next((d for d in FreeCAD.listDocuments().values()
                    if d.FileName and os.path.realpath(d.FileName) == real), None)
        print_ = fingerprint_of_document(doc) if doc is not None else fingerprint_of_file(path)
        return {"fingerprint": print_} if print_ else {}
    return {}


# ----- the whole thing --------------------------------------------------------------------------------


@dataclass
class Options:
    name: str
    program: str
    units: str = "mm"
    drawing_profile: str = ""  # "" follows the units
    insert_views: bool = False
    code: str = ""
    description: str = ""
    sheet: str = ""  # "" = the units' sheet


class Report:
    def __init__(self):
        self.project = ""
        self.top = ""
        self.items = []  # [(name, P/N, kind)]
        self.drawings = []  # [(name, P/N)]
        self.failed = []  # [(name, why)]
        self.staging = ""

    @property
    def ok(self):
        return bool(self.top) and not self.failed

    def text(self):
        lines = [f"Project {self.project}: top assembly {self.top}",
                 f"{len(self.items)} item{'s' if len(self.items) != 1 else ''} and "
                 f"{len(self.drawings)} drawing{'s' if len(self.drawings) != 1 else ''} checked in as WIP 01"]
        if self.failed:
            lines.append("Not checked in: " + "; ".join(f"{n}: {why}" for n, why in self.failed))
        return "\n".join(lines)


def _say(progress, text, done, total):
    if progress is not None and progress(text, done, total) is False:
        raise RuntimeError("stopped")


def verify(top_path, plan, staged, work_path):
    """Reopen the top from staging: every assembly's links resolve and the counts match the structure.
    Raises ValueError with what differs."""
    import FreeCAD

    import bruceworx_bom

    def expected(node):
        out = {}
        for child, qty in node.children:
            path = staged.get(child.key) or work_path(child.pn)
            out[os.path.basename(path)] = out.get(os.path.basename(path), 0) + qty
        return out

    problems = []
    for node in [n for n in plan.nodes.values() if n.kind == "assembly" and not n.existing]:
        path = staged[node.key]
        doc, opened = _open_hidden(path)
        try:
            found = {}
            for row in bruceworx_bom.walk(doc):
                found[row["name"]] = found.get(row["name"], 0) + row["qty"]
            want = expected(node)
            if found != want:
                problems.append(f"{node.name}: holds {found or 'nothing'}, the structure says {want}")
            broken = [o.Label for o in doc.Objects if o.isDerivedFrom("App::Link") and o.LinkedObject is None]
            if broken:
                problems.append(f"{node.name}: links did not resolve: {', '.join(broken)}")
        finally:
            if opened:
                FreeCAD.closeDocument(doc.Name)
    if problems:
        raise ValueError("The staged files do not match the structure; nothing was put in the vault.\n"
                         + "\n".join(problems))


def create_project(vault, work, plan, options: Options, progress=None) -> Report:
    """Steps 2-4 of the plan on a checked plan (``plan.ok``). ``vault`` is a FolderVault, ``work`` its
    pdm_workspace.Workspace. ``progress(text, done, total)`` may return False to stop (before anything
    is in the vault; after the numbers are taken it runs to the end)."""
    from heuretes_freecad import numbering, pdm, product_tree, project_setup as ps, vault_setup
    from heuretes_freecad.pdm_workspace import Workspace, clear_link_errors, rewrite_links
    from heuretes_freecad.sources import JsonNumberSource

    if not plan.ok:
        raise ValueError("; ".join(i.text for i in plan.errors) or "the structure is not complete")
    report = Report()
    program = numbering.normalize_program(options.program)
    units = "in" if str(options.units).lower().startswith("in") else "mm"
    root = vault.root
    lib_vault = _library_vault(root)
    if lib_vault is not None and os.path.abspath(lib_vault.root) == os.path.abspath(root):
        lib_vault = None
    lib_work = Workspace(lib_vault) if lib_vault is not None else None
    order = ps.order_of_work(plan)
    drawings = [n for n in order if n.wants_drawing]
    total = len(order) + len(drawings) * 2 + len(order) + 3
    done = 0
    staging = os.path.join(root, vault_setup.STAGING_DIR, f"project-{uuid.uuid4().hex[:8]}")
    os.makedirs(staging, exist_ok=True)
    report.staging = staging
    staged, dwg_staged, stems = {}, {}, set()

    def work_path(pn):
        path = work.path(pn)
        if os.path.exists(path):
            return path
        if vault.latest_version(pn) is not None:
            work.get_latest(pn)
            return work.path(pn)
        if lib_work is not None and lib_vault.latest_version(pn) is not None:
            lib_work.get_latest(pn)
            return lib_work.path(pn)
        return path

    try:
        with backups_off():
            # 2. write in staging, children first
            for node in order:
                done += 1
                _say(progress, f"Writing {node.name}", done, total)
                stem = ps.safe_stem(node.name, stems)
                stems.add(stem)
                path = os.path.join(staging, stem + ".FCStd")
                if node.kind == "assembly":
                    children = [(staged[c.key] if not c.existing else work_path(c.pn), qty) for c, qty in node.children]
                    write_assembly(path, node.name, children, units, root)
                else:
                    write_part(path, node.name, units, root, node.kind)
                staged[node.key] = path
            sheet = options.sheet or None
            for node in drawings:
                done += 1
                _say(progress, f"Drawing of {node.name}", done, total)
                stem = ps.safe_stem(node.name + "_drawing", stems)
                stems.add(stem)
                path = os.path.join(staging, stem + ".FCStd")
                write_drawing(path, staged[node.key], node.name, units, root, options.insert_views, sheet)
                dwg_staged[node.key] = path
            # 3. verify
            done += 1
            _say(progress, "Checking the files", done, total)
            verify(staged[plan.root.key], plan, staged, work_path)
            # 4. allocate, rename, relink, number
            done += 1
            _say(progress, "Taking the numbers", done, total)
            source = JsonNumberSource(vault_setup.numbering_path(root))
            data = source._load()
            types = vault_setup._vault_document_types(source, data)
            codes = {k: vault_setup.document_type_for_item("part" if k == "cots" else k, types, data)
                     for k in ps.KINDS}
            codes["drawing"] = vault_setup.document_type_for_item("drawing", types, data)
            lib_source = lib_program = lib_part_code = None
            if lib_vault is not None:
                lib_source = JsonNumberSource(vault_setup.numbering_path(lib_vault.root))
                lib_data = lib_source._load()
                programs = list((lib_data.get("programs") or {}).keys())
                if programs:
                    lib_program = programs[0]
                    lib_part_code = vault_setup.document_type_for_item(
                        "part", vault_setup._vault_document_types(lib_source, lib_data), lib_data)
            numbers, dwg_numbers, cots_pns = {}, {}, set()
            for node in order:
                if node.kind == "cots" and lib_program:
                    numbers[node.key] = pdm.strip_revision(lib_source.allocate(lib_program, lib_part_code))
                    cots_pns.add(numbers[node.key])
                else:
                    numbers[node.key] = pdm.strip_revision(source.allocate(program, codes[node.kind]))
            for node in drawings:
                dwg_numbers[node.key] = pdm.strip_revision(source.allocate(program, codes["drawing"]))
            # number the files while their links still resolve (stems in staging, work copies in work/),
            # then rename them and point every link at <P/N>.FCStd, as the import does
            for node in order:
                done += 1
                _say(progress, f"Numbering {node.name}", done, total)
                number_file(staged[node.key], numbers[node.key], units, root)
            for node in drawings:
                done += 1
                _say(progress, f"Numbering the drawing of {node.name}", done, total)
                number_file(dwg_staged[node.key], dwg_numbers[node.key], units, root)
            renamed = {}  # old basename -> new basename
            final, dwg_final = {}, {}
            for key, path in staged.items():
                new = os.path.join(staging, numbers[key] + ".FCStd")
                os.replace(path, new)
                renamed[os.path.basename(path)] = os.path.basename(new)
                final[key] = new
            for key, path in dwg_staged.items():
                new = os.path.join(staging, dwg_numbers[key] + ".FCStd")
                os.replace(path, new)
                renamed[os.path.basename(path)] = os.path.basename(new)
                dwg_final[key] = new

            def mapper(old):
                base = os.path.basename(old)
                if base in renamed:
                    new_base = renamed[base]
                    pn = new_base[:-6] if new_base.lower().endswith(".fcstd") else new_base
                    # ponytail: library files live in the sibling vault; the link is relative from the
                    # project work folder, which is where this assembly is checked out.
                    if pn in cots_pns and lib_work is not None:
                        return os.path.relpath(lib_work.path(pn), work.root)
                    return new_base
                stem = base[:-6] if base.lower().endswith(".fcstd") else base
                return base if base != old and stem in vault.items else None  # a work copy: by name alone

            for path in list(final.values()) + list(dwg_final.values()):
                rewrite_links(path, mapper)
                clear_link_errors(path)
        # the vault: items, the project, one check-in per file
        done += 1
        _say(progress, "Checking in", done, total)
        for node in order:
            home = lib_vault if numbers[node.key] in cots_pns else vault
            home.create_item(numbers[node.key], node.kind, node.name)
        for node in drawings:
            vault.create_item(dwg_numbers[node.key], "drawing", f"Drawing of {node.name}", drawing_of=numbers[node.key])
        top_pn = numbers[plan.root.key]
        code = options.code or ps.project_code(options.name, vault.projects)
        vault.create_project(code, options.name, program, top_pn, units, options.drawing_profile or units,
                             options.description)
        report.project, report.top = code, top_pn

        def pn_of(node):
            return node.pn if node.existing else numbers[node.key]

        def closure_pns(node):
            out, todo, seen = [], [node], {node.key}
            while todo:
                current = todo.pop(0)
                out.append(pn_of(current))
                for child, _q in current.children:
                    if child.key not in seen:
                        seen.add(child.key)
                        todo.append(child)
            return out

        for node in order:
            pn = numbers[node.key]
            home = lib_vault if pn in cots_pns else vault
            try:
                extras = {}
                if node.kind in ("part", "cots"):
                    print_ = fingerprint_of_file(final[node.key])
                    if print_:
                        extras["fingerprint"] = print_
                if pn in cots_pns and pn not in vault.items:
                    vault.create_item(pn, "cots", node.name)  # catalog row; the file is in the library
                home.checkout(pn)
                try:
                    version = home.checkin(pn, final[node.key], comment=ps.comment_for(node, options.name), **extras)
                except Exception:
                    home.undo_checkout(pn)
                    raise
                if node.kind == "assembly":  # the structure as typed is this revision's BOM (PDM-06), as a check-in records it
                    vault.set_bom(pn, [(pn_of(child), qty) for child, qty in node.children], revision="01",
                                  sha256=version.sha256)
                report.items.append((node.name, pn, node.kind))
            except Exception as exc:  # noqa: BLE001 -- listed, the rest goes on
                report.failed.append((node.name, str(exc) or type(exc).__name__))
        for node in drawings:
            pn = dwg_numbers[node.key]
            try:
                made_from = {}
                for x in closure_pns(node):
                    latest = vault.latest_version(x)
                    if latest is not None:
                        made_from[x] = latest.sha256
                vault.checkout(pn)
                try:
                    vault.checkin(pn, dwg_final[node.key], comment=ps.drawing_comment(node, options.name),
                                  made_from=made_from or None)
                except Exception:
                    vault.undo_checkout(pn)
                    raise
                report.drawings.append((node.name, pn))
            except Exception as exc:  # noqa: BLE001
                report.failed.append((f"drawing of {node.name}", str(exc) or type(exc).__name__))
        # work copies, read-only, so the links resolve when the top opens
        for _name, pn, _kind in report.items:
            try:
                target_vault, target_work = (lib_vault, lib_work) if pn in cots_pns else (vault, work)
                if target_vault is not None and target_vault.latest_version(pn) is not None:
                    target_work.get_latest(pn)
            except Exception as exc:  # noqa: BLE001
                report.failed.append((pn, f"checked in, but no work copy: {exc}"))
        for _name, pn in report.drawings:
            try:
                if vault.latest_version(pn) is not None:
                    work.get_latest(pn)
            except Exception as exc:  # noqa: BLE001
                report.failed.append((pn, f"checked in, but no work copy: {exc}"))
        product_tree._pages.clear()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.rmdir(os.path.dirname(staging))
        except OSError:
            pass
    return report


def new_drawing_item(vault, work, pn, units=None, insert_views=False, vault_root=None):
    """Create drawing (Structure view): a drawing file for an item that has none, numbered with the
    drawing document type, checked in as WIP 01 with made_from. Returns the drawing's P/N."""
    from heuretes_freecad import pdm, product_tree, vault_setup
    from heuretes_freecad.pdm_workspace import clear_link_errors, rewrite_links
    from heuretes_freecad.sources import JsonNumberSource

    item = vault.items[pn]
    if item.item_type not in ("part", "assembly"):
        raise ValueError(f"{pn} is a {item.item_type}; only parts and assemblies get a drawing")
    root = vault.root
    model = work.path(pn)
    if not os.path.exists(model):
        work.get_latest(pn)
    if units is None:
        projects = [p for p in vault.projects.values() if p.status == "Open"]
        units = projects[0].units if len(projects) == 1 else vault_units(root)
    program = vault_setup.program_of(pn, root)
    source = JsonNumberSource(vault_setup.numbering_path(root))
    data = source._load()
    code = vault_setup.document_type_for_item("drawing", vault_setup._vault_document_types(source, data), data)
    staging = os.path.join(root, vault_setup.STAGING_DIR, f"drawing-{uuid.uuid4().hex[:8]}")
    os.makedirs(staging, exist_ok=True)
    try:
        with backups_off():
            path = os.path.join(staging, "drawing.FCStd")
            write_drawing(path, model, item.description or pn, units, root, insert_views)
            dwg = pdm.strip_revision(source.allocate(program, code))
            number_file(path, dwg, units, root)  # while its link to the work copy still resolves
            final = os.path.join(staging, dwg + ".FCStd")
            os.replace(path, final)
            rewrite_links(final, lambda old: os.path.basename(old) if os.path.basename(old) != old else None)
            clear_link_errors(final)
        vault.create_item(dwg, "drawing", f"Drawing of {item.description or pn}", drawing_of=pn)
        bom = vault.bom
        made_from = {}
        for x in product_tree.closure({k: list(v) for k, v in bom.items()}, pn):
            latest = vault.latest_version(x)
            if latest is not None:
                made_from[x] = latest.sha256
        vault.checkout(dwg)
        try:
            vault.checkin(dwg, final, comment=f"New drawing of {pn}", made_from=made_from or None)
        except Exception:
            vault.undo_checkout(dwg)
            raise
        work.get_latest(dwg)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.rmdir(os.path.dirname(staging))
        except OSError:
            pass
    return dwg


# ----- the window --------------------------------------------------------------------------------------


def _vc():
    import bruceworx_vaultclient

    return bruceworx_vaultclient


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _message(title, text, kind="information"):
    _vc()._message(title, text, kind)


def _programs(root):
    """(code, description) of the vault's programs that are not retired."""
    try:
        import bruceworx_vaultwizard as vw
        from heuretes_freecad import vault_setup as vs

        return vw._vault_programs(vs, root)
    except Exception:
        return []


def _retired(root):
    try:
        from heuretes_freecad import vault_setup as vs

        return vs.load_settings(root).retired
    except Exception:
        return {}


def _make_classes():
    from PySide import QtCore, QtGui, QtWidgets

    from heuretes_freecad import drawing_standards as ds
    from heuretes_freecad import project_setup as ps

    def box(title, icon=None):
        """A task box: the widget's title and icon become its header (as the vault wizard's)."""
        w = QtWidgets.QWidget()
        w.setWindowTitle(title)
        if icon:
            w.setWindowIcon(QtGui.QIcon(icon))
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(6)
        return w

    def hint(text):
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("bwVaultHint")
        lab.setWordWrap(True)
        return lab

    class Panel:
        """The task-dialog protocol FreeCAD's Control.showDialog expects, as the vault wizard has it."""

        title = ""

        def __init__(self):
            self.buttons = None
            self.released = False

        def release(self):
            _state["panel"] = None
            self.released = True
            for value in list(vars(self).values()):
                if isinstance(value, QtWidgets.QComboBox) and _alive(value):
                    value.hide()
                    value.setParent(None)
            return True

        def modifyStandardButtons(self, buttons):
            self.buttons = buttons
            self.setup_buttons()

        def setup_buttons(self):
            pass

        def ok_button(self):
            return self.buttons.button(QtWidgets.QDialogButtonBox.Ok) if self.buttons is not None else None

        def getStandardButtons(self):
            return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

        def isAllowedAlterSelection(self):
            return True

        def isAllowedAlterView(self):
            return True

        def isAllowedAlterDocument(self):
            return True

        def reject(self):
            return self.release()

    class NewProjectPanel(Panel):
        """New Project as a task panel: the project, its structure, the preview; Create."""

        title = "New Project"

        def __init__(self, s):
            super().__init__()
            self.s = s
            self.root = s.root
            self.plan = None
            self.report = None
            self.vault_default = vault_units(self.root)
            self.form = [self._build_project(), self._build_structure(), self._build_preview()]
            self._preview_timer = QtCore.QTimer()
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(250)
            self._preview_timer.timeout.connect(self.preview)
            self.preview()

        # --- boxes
        def _build_project(self):
            w = box("Project", ":/icons/folder.svg")
            lay = QtWidgets.QFormLayout()
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            self.name_edit = QtWidgets.QLineEdit()
            self.name_edit.setPlaceholderText("Shop cart")
            self.name_edit.textChanged.connect(self._name_changed)
            lay.addRow("Name", self.name_edit)
            self.code_edit = QtWidgets.QLineEdit()
            self.code_edit.setPlaceholderText("From the name")
            self.code_edit.setToolTip("The project's code in the vault (letters and digits); made from the name")
            self.code_edit.textEdited.connect(lambda _t: setattr(self, "_code_typed", True))
            self._code_typed = False
            lay.addRow("Code", self.code_edit)
            self.program = QtWidgets.QComboBox()
            for code, description in _programs(self.root):
                self.program.addItem(f"{code}  {description}".strip(), code)
            if self.program.count() == 0:
                self.program.addItem("BW", "BW")
            self.program.currentIndexChanged.connect(lambda _i: self.schedule())
            lay.addRow("Program", self.program)
            self.units = QtWidgets.QComboBox()
            for code, text in UNIT_CHOICES:
                self.units.addItem(text + ("  (vault default)" if code == self.vault_default else ""), code)
            self.units.setCurrentIndex(max(0, self.units.findData(self.vault_default)))
            self.units.currentIndexChanged.connect(self._units_changed)
            lay.addRow("Units", self.units)
            self.sheet = QtWidgets.QComboBox()
            for key, (label, _w, _h) in ds.SHEETS.items():
                self.sheet.addItem(label, key)
            lay.addRow("Drawing sheet", self.sheet)
            self.views = QtWidgets.QCheckBox("Insert the front, top and right views now")
            self.views.setToolTip("Off: blank drawings (sheet and title block); add the views with Insert Views once "
                                  "the part has a shape")
            lay.addRow("", self.views)
            w.layout().addLayout(lay)
            self._units_changed()
            return w

        def _build_structure(self):
            w = box("Structure", ":/icons/document-new.svg")
            w.layout().addWidget(hint("One line per item, two spaces per level; x2 is the quantity per parent; "
                                      "'cots' marks a bought part (filed in Standard Libraries when that vault is "
                                      "next to this one); a P/N reuses a vault item. The same name twice "
                                      "is one item used twice."))
            self.text = QtWidgets.QPlainTextEdit()
            self.text.setObjectName("bwProjectStructure")
            self.text.setPlaceholderText(ps.example_text())
            self.text.setTabChangesFocus(False)
            self.text.setMinimumHeight(150)
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self.text.setFont(font)
            self.text.textChanged.connect(self.schedule)
            w.layout().addWidget(self.text)
            row = QtWidgets.QHBoxLayout()
            load = QtWidgets.QPushButton("Load CSV…")
            load.setToolTip("A CSV or spreadsheet file with columns level, name, qty, type")
            load.clicked.connect(self.load_csv)
            example = QtWidgets.QPushButton("Example")
            example.clicked.connect(lambda: self.text.setPlainText(ps.example_text()))
            row.addWidget(load)
            row.addWidget(example)
            row.addStretch(1)
            w.layout().addLayout(row)
            return w

        def _build_preview(self):
            w = box("What will be created", ":/icons/view-refresh.svg")
            self.tree = QtWidgets.QTreeWidget()
            self.tree.setObjectName("bwProjectPreview")
            self.tree.setHeaderLabels(["Item", "P/N", "Drawing"])
            self.tree.setRootIsDecorated(True)
            self.tree.setIndentation(14)
            self.tree.setMinimumHeight(140)
            self.tree.header().setStretchLastSection(False)
            self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            w.layout().addWidget(self.tree)
            self.summary = QtWidgets.QLabel("")
            self.summary.setObjectName("bwVaultCount")
            self.summary.setWordWrap(True)
            w.layout().addWidget(self.summary)
            self.problems = QtWidgets.QLabel("")
            self.problems.setObjectName("bwVaultIssue")
            self.problems.setWordWrap(True)
            self.problems.setStyleSheet("color: #E5484D;")
            w.layout().addWidget(self.problems)
            w.layout().addWidget(hint("Nothing is reserved or written before Create project."))
            return w

        # --- behaviour
        def _name_changed(self, text):
            if not self._code_typed:
                taken = list(self.s.vault.projects) if self.s is not None else []
                self.code_edit.setText(ps.project_code(text, taken) if text.strip() else "")
            self.schedule()

        def _units_changed(self, *_a):
            units = self.units.currentData()
            try:
                settings = ds.load(self.root)
            except Exception:
                settings = None
            self.sheet.setCurrentIndex(max(0, self.sheet.findData(sheet_for(units, settings))))

        def schedule(self):
            if not self.released:
                self._preview_timer.start()

        def options(self):
            name = self.name_edit.text().strip()
            return Options(name=name, program=self.program.currentData() or "BW", units=self.units.currentData(),
                           drawing_profile=self.units.currentData(), insert_views=self.views.isChecked(),
                           code=self.code_edit.text().strip(), sheet=self.sheet.currentData() or "")

        def preview(self):
            if self.released:
                return
            text = self.text.toPlainText()
            plan = ps.parse_structure(text)
            program = self.program.currentData() or "BW"
            if plan.root is not None:
                try:
                    ps.plan_numbers(plan, self.root, program, library_root=library_root(self.root))
                except Exception as exc:  # noqa: BLE001 -- shown, not raised
                    plan.issues.append(ps.Issue("error", f"No numbers: {exc}"))
            ps.check(plan, self.s.vault, program, _retired(self.root), _library_vault(self.root))
            self.plan = plan
            self.fill_preview(plan)
            self.setup_buttons()

        def fill_preview(self, plan):
            self.tree.clear()
            stack = []
            for row in ps.preview_rows(plan):
                while stack and stack[-1][0] >= row["depth"]:
                    stack.pop()
                parent = stack[-1][1] if stack else None
                words = row["name"]
                if row["qty"] not in (None, 1):
                    words += f"  ×{row['qty']}"
                if row["kind"] == "cots":
                    words += "  · bought"
                if row["existing"]:
                    words += "  · in the vault"
                if row["duplicate"]:
                    words += f"  ● used in {row['uses']} places"
                item = QtWidgets.QTreeWidgetItem([words, row["pn"], row["drawing"] or ("—" if not row["wants_drawing"] else "")])
                if row["duplicate"]:
                    item.setForeground(0, QtGui.QBrush(QtGui.QColor("#D97706")))
                    item.setToolTip(0, f"{row['name']} is typed in {row['uses']} places: one item, used {row['uses']} "
                                       "times. Rename a line to make two items.")
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                item.setExpanded(True)
                stack.append((row["depth"], item))
            if plan.root is not None:
                s = ps.summary(plan)
                bits = [s["text"], f"{s['parts']} part{'s' if s['parts'] != 1 else ''}",
                        f"{s['assemblies']} assembl{'ies' if s['assemblies'] != 1 else 'y'}"]
                if s["cots"]:
                    bits.append(f"{s['cots']} bought")
                if s["existing"]:
                    bits.append(f"{s['existing']} from the vault")
                bits.append(f"{s['drawings']} drawing{'s' if s['drawings'] != 1 else ''}")
                bits.append(f"{s['files']} files, checked in as WIP 01 in {self.units.currentData()}")
                self.summary.setText("  ·  ".join(bits))
            else:
                self.summary.setText("")
            problems = [i.text for i in plan.issues if i.level in ("error", "warning")]
            self.problems.setText("\n".join(problems[:6]))
            self.problems.setVisible(bool(problems))

        def load_csv(self):
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                None, "Load structure", "", "Structure files (*.csv *.tsv *.txt);;All files (*)")
            if path:
                try:
                    self.text.setPlainText(ps.read_structure_file(path))
                except Exception as exc:  # noqa: BLE001
                    _message("Load CSV", f"{os.path.basename(path)} could not be read: {exc}", "warning")

        def setup_buttons(self):
            ok = self.ok_button()
            if ok is None:
                return
            ok.setText("Create project")
            ready = bool(self.plan is not None and self.plan.ok and self.name_edit.text().strip())
            ok.setEnabled(ready)
            ok.setToolTip("" if ready else "Name the project and type a complete structure first")

        def accept(self):
            self.preview()
            if self.plan is None or not self.plan.ok or not self.name_edit.text().strip():
                _message("New Project", "Name the project and type a complete structure first.", "warning")
                return False
            options = self.options()
            if options.code in self.s.vault.projects:
                _message("New Project", f"A project {options.code} exists already; change the code.", "warning")
                return False
            progress = QtWidgets.QProgressDialog("Writing the files…", "Stop", 0, 100, _vc()._window())
            progress.setWindowTitle("New Project")
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setValue(0)

            def step(text, done, total):
                progress.setLabelText(text)
                progress.setMaximum(total)
                progress.setValue(done)
                QtWidgets.QApplication.processEvents()
                return not progress.wasCanceled()

            try:
                self.report = create_project(self.s.vault, self.s.work, self.plan, options, step)
            except RuntimeError as exc:
                progress.close()
                if str(exc) == "stopped":
                    return False
                _message("New Project", str(exc), "warning")
                return False
            except Exception as exc:  # noqa: BLE001
                progress.close()
                _message("New Project", f"Nothing was put in the vault: {exc}", "warning")
                self.s.invalidate()
                _vc().changed()
                return False
            progress.close()
            self.release()
            self.s.refresh()
            _vc().changed()
            report = self.report
            _vc()._console("BruceWorx vault: " + report.text().replace("\n", "; "), "msg")
            if report.failed:
                _message("New Project", report.text(), "warning")
            QtCore.QTimer.singleShot(0, lambda: _after_create(report))
            return True

    return {"NewProjectPanel": NewProjectPanel}


def _after_create(report):
    """Step 5: open the top assembly and show its Structure."""
    vc = _vc()
    try:
        vc.open_from_vault(report.top, ask=False)
    except Exception as exc:  # noqa: BLE001
        vc._console(f"New Project: the top did not open: {exc}", "warn")
    try:
        import bruceworx_structure

        bruceworx_structure.show_root("project:" + report.project)
    except Exception as exc:  # noqa: BLE001
        vc._console(f"New Project: the Structure view did not open: {exc}", "warn")


def classes():
    global _classes
    if _classes is None:
        _classes = _make_classes()
    return _classes


def new_project():
    """New Project...: the task panel, for the open vault."""
    import FreeCADGui

    s = _vc().session()
    if s is None:
        _message("New Project", "Open a vault first (the Start page, or Preferences > Heuretes FreeCAD).")
        return False
    if FreeCADGui.Control.activeDialog():
        _message("New Project", "Another command is open in the task panel. Close it (OK or Cancel) first.")
        return False
    try:
        panel = classes()["NewProjectPanel"](s)
    except ImportError as exc:
        _message("New Project", f"New Project needs the Heuretes FreeCAD hooks add-on: {exc}", "warning")
        return False
    _state["panel"] = panel
    FreeCADGui.Control.showDialog(panel)
    try:
        import bruceworx_vaultwizard

        bruceworx_vaultwizard._repaint_title()
    except Exception:
        pass
    panel.name_edit.setFocus()
    return True


def insert_views():
    """Insert Views: the three views on the active document's blank drawing page."""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    pages = [o for o in (doc.Objects if doc else []) if o.isDerivedFrom("TechDraw::DrawPage")
             and MODEL_PROP in o.PropertiesList]
    if not pages:
        _message("Insert Views", "Open a drawing made by New Project first (its page names its model).")
        return False
    try:
        doc.openTransaction("Insert views")
        add_views(pages[0])
        doc.commitTransaction()
    except Exception as exc:  # noqa: BLE001
        doc.abortTransaction()
        _message("Insert Views", str(exc), "warning")
        return False
    return True


class _NewProjectCommand:
    def GetResources(self):
        return {"MenuText": "New Project…", "Pixmap": ":/icons/folder.svg",
                "ToolTip": "Name a project and type its structure: every part, assembly and drawing is numbered, "
                           "written and checked in as WIP 01 (PDM-09)"}

    def IsActive(self):
        try:
            return _vc().session() is not None
        except Exception:
            return False

    def Activated(self):
        new_project()


class _InsertViewsCommand:
    def GetResources(self):
        return {"MenuText": "Insert Views", "Pixmap": ":/icons/TechDraw_ProjectionGroup.svg",
                "ToolTip": "Add the front, top and right views to a blank drawing made by New Project"}

    def IsActive(self):
        try:
            import FreeCAD

            doc = FreeCAD.ActiveDocument
            return doc is not None and any(o.isDerivedFrom("TechDraw::DrawPage") and MODEL_PROP in o.PropertiesList
                                           for o in doc.Objects)
        except Exception:
            return False

    def Activated(self):
        insert_views()


FILE_ACTION = "BruceWorxFileNewProject"


def _file_menu_entry():
    """File > New Project... right after New. FreeCAD rebuilds the menu bar on every workbench
    activation, so this runs after each one (idempotent)."""
    try:
        import FreeCADGui
        from PySide import QtGui, QtWidgets

        window = FreeCADGui.getMainWindow()
        for menu in window.menuBar().findChildren(QtWidgets.QMenu):
            if menu.objectName() != "&File":
                continue
            if any(a.objectName() == FILE_ACTION for a in menu.actions()):
                return
            anchor = next((a for a in menu.actions() if a.objectName() == "Std_New"), None)
            action = QtGui.QAction("New Project…", window)
            action.setObjectName(FILE_ACTION)
            action.setToolTip(_NewProjectCommand().GetResources()["ToolTip"])
            action.triggered.connect(lambda _c=False: FreeCADGui.runCommand(COMMAND))
            if anchor is not None:
                following = menu.actions()[menu.actions().index(anchor) + 1:] or [None]
                menu.insertAction(following[0], action) if following[0] is not None else menu.addAction(action)
            else:
                menu.addAction(action)
            return
    except Exception:
        pass


def install():
    """The commands, the menu entries, and the vault client's check-in extras."""
    if _state["installed"]:
        return
    import FreeCADGui
    from PySide import QtCore

    FreeCADGui.addCommand(COMMAND, _NewProjectCommand())
    FreeCADGui.addCommand(VIEWS_COMMAND, _InsertViewsCommand())
    _state["installed"] = True
    try:
        import bruceworx_menu
        from PySide import QtGui

        window = FreeCADGui.getMainWindow()
        action = QtGui.QAction(QtGui.QIcon(":/icons/folder.svg"), "New Project…", window)
        action.triggered.connect(lambda _c=False: FreeCADGui.runCommand(COMMAND))
        bruceworx_menu.add_action(action, "BruceWorxMenu_" + COMMAND)
        window.workbenchActivated.connect(lambda _n: QtCore.QTimer.singleShot(0, _file_menu_entry))
        QtCore.QTimer.singleShot(0, _file_menu_entry)
    except Exception as exc:  # pragma: no cover - FreeCAD console only
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"BruceWorx: New Project menu entries not added: {exc}\n")
    try:
        vc = _vc()
        if checkin_extras not in getattr(vc, "CHECKIN_EXTRAS", []):
            vc.CHECKIN_EXTRAS.append(checkin_extras)
    except Exception:
        pass
