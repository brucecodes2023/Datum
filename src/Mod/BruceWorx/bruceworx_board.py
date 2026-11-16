# SPDX-License-Identifier: LGPL-2.1-or-later
"""KiCad boards in FreeCAD: Insert Board, board materials and mass (ECD-02, ECD-06).

:func:`insert_board` exports a ``.kicad_pcb`` to STEP through the KiCad bridge
(``bruceworx.kicad_bridge`` in the hooks add-on; cached in
``~/Library/Caches/bruceworx-kicad``), imports it as ONE ``App::Part`` named
by the board's file stem, and

* flattens KiCad's per-component parts into it, each component body labelled
  by its reference (``J1 PinSocket_1x08...``), so the board is one BOM line;
* sets each body's material from KiCad's names (``_PCB`` FR-4; ``_pad``,
  ``_copper``, ``_via`` PCB Copper; mask and silkscreen appearance only) and
  components by footprint library (connectors PBT-GF30, packages epoxy,
  chip R/C ceramic, else plastic), keeping KiCad's colours;
* takes component masses from ``component_masses.csv`` (vault, then user)
  where listed, as the body's ``MassOverride``;
* adds a coordinate system at the board origin and one per mounting hole, on
  the board's bottom face, for joints that survive Update Board;
* records ``BoardSource`` and ``BoardHash`` for that later update;
* sets ``BomDetail`` off, so the Assembly BOM lists the board as one PCBA line.

The materials are BruceWorx's own library (``Materials/Electronics``),
registered with the Material module as a workbench library, so FreeCAD's
upstream material folders are untouched.

KiCad vault items (ECD-05): a board whose KiCad project lives in a vault item's folder
(``bruceworx.ecad_vault``) gets that item's part number, revision and description when it is
inserted, and :func:`build_item_model` makes the item's own 3D model: the board as above plus an
"Electrical design" group whose schematic, board and project entries open in KiCad
(:class:`KiCadDocument`, double-click; bruceworx_board_gui).

Off switch: Preferences > BruceWorx > "KiCad boards"
(``BaseApp/Preferences/BruceWorx/KiCadBoards``).
"""

import json
import os

import FreeCAD as App

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
ENABLED_KEY = "KiCadBoards"
HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_NAME = "BruceWorx"
MATERIAL_DIR = os.path.join(HERE, "Materials")
MODULES_PARAM = "User parameter:BaseApp/Preferences/Mod/Material/Resources/Modules"
GROUP = "Board"

# Bridge material keys -> card UUIDs (Materials/Electronics).
MATERIALS = {
    "fr4": "e4fd295b-7da2-4a82-af69-89659c5139ac",
    "copper": "c72f4c00-ea65-4e2c-94bb-f9c16294c0b6",
    "soldermask": "ea8badac-1ca4-480f-9902-af6eef7271f6",
    "silkscreen": "6b5ac905-9a22-4232-bb9a-0091cbb514f4",
    "pbt": "43606f6b-2585-4011-ba84-73f6e8256775",
    "epoxy": "d09bc83c-3fff-4213-812a-ce41fadd72ce",
    "ceramic": "0cf075f3-cfa6-4712-a28c-421c12950a1f",
    "plastic": "dad79c6d-2d4e-4164-babb-170210af9e65",
    "metal": "8e70419b-4ff3-42c5-99b0-b61ef52cef22",
}
ENIG = "057cfb8f-b29c-4787-b627-42a03d422d93"
HASL = "42226aec-f5df-4ee3-9202-dcc968761a91"
# KiCad stackup copper_finish -> appearance card for pads and vias. Others keep KiCad's colour.
FINISHES = {
    "ENIG": ENIG, "ENEPIG": ENIG, "Hard gold": ENIG, "Immersion gold": ENIG,
    "HAL SnPb": HASL, "HAL lead-free": HASL, "Immersion tin": HASL, "Immersion silver": HASL,
}


def enabled():
    return bool(App.ParamGet(PREFS_PATH).GetBool(ENABLED_KEY, True))


def register_materials():
    """Make Materials/ a workbench material library named BruceWorx (the
    Material module reads Resources/Modules/<name>/ModuleDir). Rewritten on
    every start, so it follows the build or install folder."""
    group = App.ParamGet(f"{MODULES_PARAM}/{LIBRARY_NAME}")
    if not os.path.isdir(MATERIAL_DIR):
        return False
    if group.GetString("ModuleDir", "") != MATERIAL_DIR:
        group.SetString("ModuleDir", MATERIAL_DIR)
    icon = os.path.join(HERE, "preferences-heuretes.svg")
    if group.GetString("ModuleIcon", "") != icon:
        group.SetString("ModuleIcon", icon)
    group.SetBool("ModuleReadOnly", True)
    return True


_cache = {}


def material(key_or_uuid):
    """A BruceWorx material by bridge key (``fr4``) or UUID. Registers and
    reloads the library when the material manager started before it."""
    import Materials

    uuid = MATERIALS.get(key_or_uuid, key_or_uuid)
    if uuid in _cache:
        return _cache[uuid]
    manager = Materials.MaterialManager()
    try:
        found = manager.getMaterial(uuid)
    except Exception:
        register_materials()
        manager.refresh()
        found = manager.getMaterial(uuid)
    _cache[uuid] = found
    return found


def appearance(key_or_uuid):
    """An App.Material (what ShapeAppearance holds) from a card's Basic Rendering."""
    card = material(key_or_uuid)
    out = App.Material()
    for name in ("DiffuseColor", "AmbientColor", "SpecularColor", "EmissiveColor"):
        try:
            text = card.getAppearanceValue(name)
        except Exception:
            continue
        values = tuple(float(v) for v in str(text).strip("()").split(","))
        setattr(out, name, values[:3])
    for name in ("Shininess", "Transparency"):
        try:
            setattr(out, name, float(card.getAppearanceValue(name)))
        except Exception:
            pass
    return out


def bridge():
    try:
        from heuretes_freecad import kicad_bridge
    except ImportError as exc:
        raise RuntimeError("the Heuretes FreeCAD hooks add-on is off (Preferences > Heuretes FreeCAD > Hooks add-on); "
                           "it holds the KiCad bridge") from exc
    return kicad_bridge


# ---------------------------------------------------------------------------
# Import.


def _gui_up():
    return bool(getattr(App, "GuiUp", False))


def import_step(step, doc):
    """Import ``step`` into ``doc``; returns the new top-level App::Part.
    Compounds merged (KiCad's 258 pads are one body, not 258), no link
    groups, so the structure is the same whatever the import preferences say."""
    before = {o.Name for o in doc.Objects}
    if _gui_up():
        import ImportGui

        options = {"merge": True, "useLinkGroup": False, "expandCompound": False,
                   "showProgress": False, "importHidden": True, "mode": 0}
        ImportGui.insert(step, doc.Name, options=options)
    else:
        import Import

        Import.insert(step, doc.Name, importHidden=True, merge=True, useLinkGroup=False, mode=0)
    new = [o for o in doc.Objects if o.Name not in before]
    tops = [o for o in new if o.TypeId == "App::Part" and not any(p.Name in {n.Name for n in new}
                                                                    for p in o.InList)]
    if len(tops) != 1:
        raise RuntimeError(f"expected one top-level part from the STEP import, got {len(tops)}")
    return tops[0]


def _bodies(part):
    return [o for o in part.Group if o.isDerivedFrom("Part::Feature")]


def _colours(obj):
    """Per-face appearance (GUI only), so a material change cannot repaint it."""
    view = getattr(obj, "ViewObject", None)
    if view is None or not hasattr(view, "ShapeAppearance"):
        return None
    return list(view.ShapeAppearance)


def _restore(obj, colours):
    if colours is not None:
        obj.ViewObject.ShapeAppearance = colours


def flatten(part, info):
    """Move each component's bodies out of KiCad's sub-part into ``part``,
    keeping where they are, and label them by footprint reference. Returns
    ``[(body, footprint or None)]`` for the components."""
    kb = bridge()
    doc = part.Document
    out = []
    for sub in [o for o in part.Group if o.TypeId == "App::Part"]:
        base = sub.Placement
        side = "bottom" if base.Base.z < 0 else "top"
        fp = kb.match_footprint(info, base.Base.x, base.Base.y, side) or kb.match_footprint(
            info, base.Base.x, base.Base.y)
        children = list(sub.Group)
        sub.Group = []
        board_body = kb.classify_body(sub.Label) is not None  # e.g. pads split by the import
        model = sub.Label[:-3] if sub.Label[-3:].isdigit() else sub.Label
        for child in children:
            if child.isDerivedFrom("App::Origin") or child.isDerivedFrom("App::DatumElement"):
                continue
            colours = _colours(child)
            child.Placement = base.multiply(child.Placement)
            part.addObject(child)
            _restore(child, colours)
            if board_body:
                child.Label = sub.Label
            elif child.isDerivedFrom("Part::Feature"):
                _add(child, "App::PropertyString", "Reference", "KiCad reference designator")
                _add(child, "App::PropertyString", "Footprint", "KiCad footprint Library:Name")
                if fp is not None:
                    child.Reference, child.Footprint = fp.ref, fp.lib_id
                    child.Label = f"{fp.ref} {model}"
                out.append((child, fp))
        _remove_part(sub)
    return out


def _add(obj, typeid, name, doc, group=GROUP):
    if name not in obj.PropertiesList:
        obj.addProperty(typeid, name, group, doc)


# ---------------------------------------------------------------------------
# Materials and mass.


def apply_materials(part, info, components=None, mass_table=None):
    """Board bodies by KiCad's names, components by footprint library;
    appearance kept. Returns ``{body name: material key}``."""
    kb = bridge()
    finish = FINISHES.get(info.copper_finish)
    keys = {}
    component_bodies = {body.Name: fp for body, fp in (components or [])}
    for body in _bodies(part):
        colours = _colours(body)
        if body.Name in component_bodies:
            fp = component_bodies[body.Name]
            key = kb.classify_component(fp.library if fp else "", fp.name if fp else body.Label)
        else:
            key = kb.classify_body(body.Label)
        if key is None:
            key = kb.classify_component("", body.Label)
        keys[body.Name] = key
        shape = body.Shape
        if key in (kb.MASK, kb.SILK) or not shape.Solids:
            continue  # faces only: appearance, no mass
        body.ShapeMaterial = material(key)
        if colours is not None and key == kb.COPPER and finish and _is_pad(body.Label):
            body.ViewObject.ShapeAppearance = [appearance(finish)]
            continue
        _restore(body, colours)
        if body.Name in component_bodies:
            _component_mass(body, component_bodies[body.Name], key, colours, mass_table)
    return keys


def _is_pad(label):
    return "_pad" in label  # vias are usually tented: under the mask, no finish


def _component_mass(body, fp, key, colours, mass_table):
    """MassOverride from the mass table, else, for a multi-solid model with
    metal-coloured solids (contacts, leads), plastic plus metal by volume."""
    kb = bridge()
    grams = kb.lookup_mass(mass_table or {}, fp, body.Label.split(" ", 1)[-1])
    note = "component_masses.csv"
    if grams is None and colours and len(body.Shape.Solids) > 1:
        grams, note = _mixed_mass(body, key, colours), "plastic + metal contacts by colour"
    if grams is None:
        return
    _add(body, "App::PropertyMass", "MassOverride", "Mass used instead of volume x density")
    _add(body, "App::PropertyString", "MassSource", "Where MassOverride came from")
    body.MassOverride = f"{grams} g"
    body.MassSource = note


def _mixed_mass(body, key, colours):
    kb = bridge()
    faces = body.Shape.Faces
    metal_volume = plastic_volume = 0.0
    for solid in body.Shape.Solids:
        metallic = 0
        for face in solid.Faces:
            index = next((i for i, f in enumerate(faces) if f.isSame(face)), None)
            if index is not None and index < len(colours) and kb.is_metallic(colours[index].DiffuseColor):
                metallic += 1
        if metallic * 2 > len(solid.Faces):
            metal_volume += solid.Volume
        else:
            plastic_volume += solid.Volume
    if metal_volume == 0:
        return None
    # g/cm^3 x mm^3 / 1000 = g
    return (plastic_volume * kb.DENSITY[key] + metal_volume * kb.DENSITY[kb.METAL]) / 1000.0


def _pause_part_observer():
    """Hold the part-properties observer (bruceworx.partprops) while a board is built: it would
    recompute the part's mass over every body after each of the hundreds of body changes, which
    took most of a large board's import (ESC board, 31 Oct: 51 of 53 s). Returns the observer
    to resume, or None."""
    try:
        from heuretes_freecad import partprops

        observer = partprops._OBSERVER
    except Exception:
        return None
    if observer is None or observer.busy:
        return None
    observer.busy = True
    return observer


def _resume_part_observer(observer, part=None):
    """Let the observer go again and refresh ``part``'s computed values once."""
    if observer is None:
        return
    observer.busy = False
    if part is None:
        return
    try:
        from heuretes_freecad import partprops

        for obj in [part] + [p for p in part.InList if part in (getattr(p, "Group", None) or [])]:
            partprops.refresh(obj)
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx board: mass and material not refreshed: {exc}\n")


_densities = {}


def _density_g_per_mm3(mat):
    """The material's density in g/mm^3 (0 when it has none), read from the card's properties:
    ``getPhysicalValue`` writes the material back to the body in FreeCAD 1.1, which touched every
    body and woke the part-properties observer (0.2 s per body on the ESC board)."""
    key = getattr(mat, "UUID", "") or getattr(mat, "Name", "")
    if key in _densities:
        return _densities[key]
    props = getattr(mat, "PhysicalProperties", None)
    text = props.get("Density") if isinstance(props, dict) else None
    try:
        density = App.Units.Quantity(str(text)).getValueAs("g/mm^3").Value if text else 0.0
    except Exception:
        density = 0.0
    _densities[key] = density
    return density


def body_mass_g(body):
    """Grams: MassOverride, else volume x density of a chosen material."""
    if "MassOverride" in body.PropertiesList and body.MassOverride.getValueAs("g").Value > 0:
        return body.MassOverride.getValueAs("g").Value
    mat = body.ShapeMaterial
    if getattr(mat, "Name", "Default") == "Default" or not body.Shape.Solids:
        return 0.0
    return body.Shape.Volume * _density_g_per_mm3(mat)


def mass_report(part):
    """``{"total_g", "by_material": {name: g}, "volume_mm3": {name: mm^3}}``."""
    by_material, volumes = {}, {}
    for body in _bodies(part):
        grams = body_mass_g(body)
        if grams <= 0:
            continue
        name = body.ShapeMaterial.Name
        by_material[name] = by_material.get(name, 0.0) + grams
        volumes[name] = volumes.get(name, 0.0) + body.Shape.Volume
    return {"total_g": sum(by_material.values()), "by_material": by_material, "volume_mm3": volumes}


def load_mass_table(vault=None):
    """The component mass table: ``<vault>/component_masses.csv`` (the open
    vault by default), then the user's ``~/.config/bruceworx/component_masses.csv``."""
    kb = bridge()
    if vault is None:
        try:
            from heuretes_freecad import partprops

            vault = partprops.vault_path() or None
        except Exception:
            vault = None
    return kb.load_mass_table(kb.mass_table_paths(vault))


# ---------------------------------------------------------------------------
# Coordinate systems.


def add_frames(part, info):
    """An LCS at the board origin and one per mounting hole, z on the board's
    bottom face (KiCad's board body starts at z = 0). Returns them."""
    doc = part.Document
    frames = []

    def frame(name, label, x, y):
        lcs = doc.addObject("App::LocalCoordinateSystem", name)
        lcs.Label = label
        lcs.Placement = App.Placement(App.Vector(round(x, 6), round(y, 6), 0), App.Rotation())
        part.addObject(lcs)
        frames.append(lcs)
        return lcs

    frame("BoardOrigin", "Board origin", 0, 0)
    for hole in info.holes:
        x, y = info.to_step(hole.x, hole.y)
        lcs = frame("Hole", f"Hole {hole.ref}" if hole.ref else "Hole", x, y)
        _add(lcs, "App::PropertyLength", "HoleDiameter", "Drill diameter")
        lcs.HoleDiameter = hole.drill
    return frames


# ---------------------------------------------------------------------------
# Insert Board.


def insert_board(path, doc=None, assembly=None, options=None, cache=None, mass_table=None, vault=None,
                 cli=None):
    """Insert the KiCad board ``path`` as one App::Part (see the module doc).

    ``assembly``: put the part in it (its document is used). Otherwise
    ``doc``, or a new document named by the board. No dialogs, so it runs
    headless. Returns the part; masses, frame count and the STEP used are in
    :data:`last_report`."""
    kb = bridge()
    path = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    opts = kb.step_options(options)
    result = kb.cached_step(path, opts, cache=cache, cli=cli)
    info = kb.board_info(path, origin=opts["origin"])
    if assembly is not None:
        doc = assembly.Document
    if doc is None:
        doc = App.newDocument(_doc_name(stem))
    if mass_table is None:
        mass_table = load_mass_table(vault)

    doc.openTransaction("Insert Board")
    paused = _pause_part_observer()
    try:
        imported = import_step(result.step, doc)
        part = doc.addObject("App::Part", _doc_name(stem))
        part.Label = stem
        for child in list(imported.Group):
            if child.isDerivedFrom("App::Origin"):
                continue
            colours = _colours(child)
            imported.removeObject(child)
            child.Placement = imported.Placement.multiply(child.Placement)
            part.addObject(child)
            _restore(child, colours)
        _remove_part(imported)
        components = flatten(part, info)
        for body in _bodies(part):
            kind = kb.classify_body(body.Label)
            if kind is not None and body.Label.startswith(stem):
                continue
            if kind is not None:  # "<copy name>_PCB" -> "<stem>_PCB"
                body.Label = stem + body.Label[body.Label.rfind("_"):]
        apply_materials(part, info, components, mass_table)
        frames = add_frames(part, info)
        _board_props(part, path, info, opts, result)
        if assembly is not None:
            assembly.addObject(part)
        _apply_part_schema(part, info, stem)
        _apply_vault_item(part, path)
        doc.recompute()
    except Exception:
        _resume_part_observer(paused)
        doc.abortTransaction()
        raise
    _resume_part_observer(paused, part)
    doc.commitTransaction()
    report = mass_report(part)
    report.update(part=part.Name, frames=len(frames), holes=len(info.holes), step=result.step,
                  cached=result.cached, missing_models=result.missing_models,
                  components=len(components), width=info.width, height=info.height)
    last_report.clear()
    last_report.update(report)
    return part


last_report = {}


def _doc_name(stem):
    import re

    name = re.sub(r"[^A-Za-z0-9_]", "_", stem) or "Board"
    return name if not name[0].isdigit() else "_" + name


def _remove_part(part):
    """Delete an emptied App::Part with its origin (the origin may go with it)."""
    doc = part.Document
    origin = getattr(part, "Origin", None)
    names = [origin.Name] + [f.Name for f in origin.OriginFeatures] if origin is not None else []
    doc.removeObject(part.Name)
    for name in names:
        if doc.getObject(name) is not None:
            doc.removeObject(name)


def _board_props(part, path, info, opts, result):
    kb = bridge()
    _add(part, "App::PropertyFile", "BoardSource", "The KiCad board this part was made from")
    _add(part, "App::PropertyString", "BoardHash", "sha256 of the board file when it was inserted")
    _add(part, "App::PropertyString", "BoardOptions", "STEP export options (JSON)")
    _add(part, "App::PropertyString", "BoardSize", "Board outline width x height")
    _add(part, "App::PropertyInteger", "CopperLayers", "Number of copper layers")
    _add(part, "App::PropertyBool", "BomDetail", "List the board's bodies in the BOM (off: one PCBA line)")
    _add(part, "App::PropertyString", "BoardDetail",
         "full: copper, pads, solder mask and silkscreen; light: the board and its components only")
    part.BoardSource = path
    part.BoardDetail = "light" if kb.is_light(opts) else "full"
    part.BoardHash = kb.board_hash(path)
    part.BoardOptions = json.dumps(opts, sort_keys=True)
    part.BoardSize = f"{info.width:g} x {info.height:g} mm"
    part.CopperLayers = info.copper_layers
    part.BomDetail = False  # the component BOM stays in KiCad
    for name in ("BoardHash", "BoardOptions", "BoardSize", "CopperLayers", "BoardDetail"):
        part.setEditorMode(name, ["ReadOnly"])


def _apply_part_schema(part, info, stem):
    """Part properties (P/N, Description, MaterialName, Mass ...) as on any part."""
    try:
        from heuretes_freecad import partprops
    except ImportError:
        return
    try:
        partprops.load_schema().apply(part)
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx board: part properties not applied: {exc}\n")
        return
    if "Description" in part.PropertiesList and not part.Description:
        part.Description = f"PCBA {info.title or stem}"


# ---------------------------------------------------------------------------
# KiCad vault items (ECD-05).

ITEM_RECIPE = 1  # bump when build_item_model makes the 3D model differently (old ones are remade)
ELECTRICAL_GROUP = "ElectricalDesign"
ITEM_PROPERTIES = (  # the part's own values from keystone-part.json, beside the schema's
    ("Manufacturer", "Manufacturer of the part (KiCad vault item)"),
    ("ManufacturerPartNumber", "Manufacturer's part number (KiCad vault item)"),
    ("Value", "The part's value (KiCad vault item)"),
)
KINDS = {".kicad_pro": "project", ".kicad_sch": "schematic", ".kicad_pcb": "board"}
KIND_LABELS = {"project": "Project", "schematic": "Schematic", "board": "PCB"}


def _ecad():
    try:
        from heuretes_freecad import ecad_vault
    except ImportError as exc:
        raise RuntimeError("the Heuretes FreeCAD hooks add-on is off (Preferences > Heuretes FreeCAD > Hooks add-on); "
                           "it holds the KiCad vault items") from exc
    return ecad_vault


def item_values(vault, pn, folder=None):
    """The part properties of the KiCad vault item ``pn``: number, revision, state and type from
    the vault, description and the item's own values from its keystone-part.json."""
    item = vault.model.items[pn]
    props = _ecad().read_properties(folder) if folder else {}
    values = {"PartNumber": pn, "Revision": item.latest.code, "LifecycleState": item.state,
              "ItemType": item.item_type, "Description": props.get("Description") or item.description}
    for name, _doc in ITEM_PROPERTIES:
        if props.get(name):
            values[name] = str(props[name])
    return values


def apply_item_values(part, values):
    """Write ``values`` (item_values) into the part's properties; the schema's first."""
    for name, doc in ITEM_PROPERTIES:
        if name in values:
            _add(part, "App::PropertyString", name, doc, group="BruceWorx")
    for name, val in values.items():
        if name not in part.PropertiesList:
            continue
        if name == "LifecycleState":
            try:
                from heuretes_freecad import partprops

                partprops.set_state(part, val)
            except Exception:
                pass
            continue
        try:
            setattr(part, name, val)
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx board: {name} not set: {exc}\n")


def _apply_vault_item(part, path):
    """A board inside a KiCad vault item's folder takes that item's part number, revision and
    description (so an assembly's BOM lists the library part)."""
    try:
        ev = _ecad()
        found = ev.item_for_path(path)
        if found is None:
            return
        from heuretes_freecad.pdm_folder import FolderVault

        root, _actor, pn = found
        vault = FolderVault(root)
        if pn in vault.model.items and ev.is_ecad(vault, pn):
            apply_item_values(part, item_values(vault, pn, os.path.join(root, "work", _actor, pn)))
    except Exception as exc:
        App.Console.PrintWarning(f"BruceWorx board: vault item not read: {exc}\n")


class KiCadDocument:
    """A file of the board's KiCad project (project, schematic or board) in the 3D model's tree;
    bruceworx_board_gui opens it in KiCad on a double-click."""

    def __init__(self, obj, kind, path):
        obj.addProperty("App::PropertyString", "KiCadKind", "KiCad", "project, schematic or board")
        obj.addProperty("App::PropertyString", "KiCadFile", "KiCad",
                        "The file, relative to the KiCad project folder")
        obj.addProperty("App::PropertyPath", "KiCadFolder", "KiCad",
                        "The project folder when the model was made (used when the model is not beside it)")
        obj.KiCadKind = kind
        obj.KiCadFile = os.path.basename(path)
        obj.KiCadFolder = os.path.dirname(path)
        for name in ("KiCadKind", "KiCadFile", "KiCadFolder"):
            obj.setEditorMode(name, ["ReadOnly"])
        obj.Proxy = self

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def kicad_file_path(obj):
    """The file a KiCadDocument stands for: in the project folder the model sits in
    (``<project>/.keystone/<P/N>.FCStd``), else where it was when the model was made."""
    name = getattr(obj, "KiCadFile", "")
    file_name = getattr(obj.Document, "FileName", "") or ""
    folder = os.path.dirname(file_name)
    if file_name and os.path.basename(folder) == _ecad().DERIVED_DIR:
        candidate = os.path.join(os.path.dirname(folder), name)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(getattr(obj, "KiCadFolder", ""), name)


def add_electrical_design(doc, project):
    """The "Electrical design" group with the project's schematic, board and project file."""
    group = doc.addObject("App::DocumentObjectGroup", ELECTRICAL_GROUP)
    group.Label = "Electrical design (KiCad)"
    made = []
    for kind, path in (("schematic", project.sch), ("board", project.pcb), ("project", project.pro)):
        if not path:
            continue
        obj = doc.addObject("App::FeaturePython", "KiCad" + KIND_LABELS[kind])
        KiCadDocument(obj, kind, path)
        obj.Label = f"{KIND_LABELS[kind]}: {os.path.basename(path)}"
        group.addObject(obj)
        if _gui_up():
            try:
                import bruceworx_board_gui

                bruceworx_board_gui.ViewProviderKiCadDocument(obj.ViewObject)
            except Exception as exc:
                App.Console.PrintWarning(f"BruceWorx board: {obj.Label}: no view provider: {exc}\n")
        made.append(obj)
    return group, made


def is_light_part(part):
    """Whether an inserted board is the light model (no copper or print layers)."""
    return getattr(part, "BoardDetail", "") == "light"


# Insert Board offers the light model by default in an assembly with at least this many parts
# (31 Oct; Preferences parameter BruceWorx/KiCadLightAbove). The user's large assemblies (a drone
# with ESCs and a flight controller) pass it; a small enclosure with a board and a few screws does not.
LIGHT_ABOVE = 25
LIGHT_ABOVE_KEY = "KiCadLightAbove"


def assembly_part_count(assembly):
    """How many parts an assembly holds, counting into sub-assemblies and link arrays (joints,
    views and BOMs are not parts)."""
    count, seen = 0, set()

    def walk(container):
        nonlocal count
        for obj in getattr(container, "Group", []) or []:
            if obj is None or obj.Name in seen:
                continue
            seen.add(obj.Name)
            tid = obj.TypeId
            if tid.startswith(("Assembly::JointGroup", "Assembly::ViewGroup", "Assembly::BomGroup", "App::Origin")):
                continue
            if tid == "App::Link":
                count += max(1, int(getattr(obj, "ElementCount", 0) or 0))
            elif tid in ("App::Part", "Assembly::AssemblyLink") or obj.isDerivedFrom("Part::Feature"):
                count += 1
            elif tid == "Assembly::AssemblyObject" or obj.isDerivedFrom("App::DocumentObjectGroup"):
                walk(obj)

    walk(assembly)
    return count


def default_light(assembly, threshold=None):
    """Whether Insert Board should default to the light model for ``assembly``."""
    if assembly is None:
        return False
    if threshold is None:
        threshold = App.ParamGet(PREFS_PATH).GetInt(LIGHT_ABOVE_KEY, LIGHT_ABOVE)
    return assembly_part_count(assembly) >= threshold


def build_item_model(folder, pn, values, path, cli=None, cache=None, light=False):
    """The 3D model of the KiCad vault item ``pn`` from its work folder: a new document named by
    the P/N with the board (insert_board), the item's values (item_values) and the Electrical
    design group, saved at ``path`` (ecad_vault.derived_path), read-only on disk. ``light``: the
    board and its components only (kicad_bridge.LIGHT_OPTIONS). Returns the open document."""
    ev = _ecad()
    project = ev.find_project(folder)
    if not project.pcb:
        raise RuntimeError(f"{pn}: the KiCad project {project.stem} has no board ({project.stem}.kicad_pcb)")
    found = ev.item_for_path(project.pcb)
    doc = App.newDocument(_doc_name(pn))
    try:
        doc.Label = pn
        options = bridge().light_options() if light else None
        part = insert_board(project.pcb, doc=doc, options=options, cli=cli, cache=cache,
                            vault=found[0] if found else None)
        apply_item_values(part, values)
        if values.get("Description"):
            part.Label = values["Description"]
        add_electrical_design(doc, project)
        doc.recompute()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.chmod(path, 0o644)
        doc.saveAs(path)
        os.chmod(path, 0o444)
    except Exception:
        App.closeDocument(doc.Name)
        raise
    return doc
