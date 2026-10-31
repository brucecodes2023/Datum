# SPDX-License-Identifier: LGPL-2.1-or-later
"""The harness as a vault item (BW-ROU-06, 30 Oct 2026; plan chapters/74-routing-next-plan.tex, Step 2).

A harness is bought or built as one thing with one part number, but it is routed *in context*: its paths attach to
the motor, the board and the frame of the parent assembly. So it is made in two steps, as a SolidWorks virtual
component is saved out later:

1. **Harness** (:func:`make_harness`) groups routed items (wires, tubes, hoses, bundles, their route paths) and the
   connector links in an ``App::Part`` carrying ``HarnessType`` ("Cable assembly" or "Tube assembly") inside the
   current assembly. It is an ordinary container: the BOM walker reads through it, so the parent's BOM already has
   the stock material (length per ``StockItem``) and the terminals.
2. **Make vault item** (:func:`make_vault_item`) saves the container out as its own file under a new part number
   (a vault assembly item, category "Harnesses", document type named like its ``HarnessType`` when the vault has
   one), checks it in, and replaces the container in the parent with an ``App::Link`` to it. The routed objects are
   copied (:mod:`bruceworx_flexsub` property copy); a path point attached to a part of the parent becomes a
   cross-document link (``XAttachments``, ``App::PropertyXLinkSubList``), so with both files open a moved part
   re-routes the harness. The points keep their last solved positions, so the harness opened alone (parent file
   missing or closed) still shows, draws and totals itself.

The harness file is placed at the parent's origin and must stay there: its paths are in the parent's frame.
"""

import os

import FreeCAD as App

from bruceworx import stock as st

HARNESS_TYPES = ["Cable assembly", "Tube assembly"]
HARNESS_PROP = "HarnessType"
CATEGORY = st.HARNESS_CATEGORY


def _br():
    import bruceworx_routing

    return bruceworx_routing


def is_harness(obj):
    try:
        return obj.isDerivedFrom("App::Part") and HARNESS_PROP in obj.PropertiesList
    except Exception:
        return False


def harnesses(doc):
    return [o for o in doc.Objects if is_harness(o)]


def harness_of(obj):
    """The Harness container ``obj`` is in (directly or deeper), or None."""
    seen = set()
    while obj is not None and obj.Name not in seen:
        seen.add(obj.Name)
        if is_harness(obj):
            return obj
        try:
            obj = obj.getParentGeoFeatureGroup()
        except Exception:
            return None
    return None


def contents(container):
    """The objects in a harness, without its origin."""
    return [o for o in container.Group if not o.isDerivedFrom("App::Origin")]


def suggested_type(items):
    """"Tube assembly" when the routed items are tubes and hoses only, else "Cable assembly"."""
    br = _br()
    kinds = {br.kind_of(o) for o in items if br.is_routed(o)}
    return HARNESS_TYPES[1] if kinds and kinds <= {br.PIPE, br.HOSE} else HARNESS_TYPES[0]


def _top_assembly(doc):
    for obj in doc.Objects:
        if obj.isDerivedFrom("Assembly::AssemblyObject") and not any(
                o.isDerivedFrom("Assembly::AssemblyObject") for o in obj.InList):
            return obj
    return None


def _rank(obj):
    """Dependency order: paths, then wires, tubes and hoses, then bundles, then the rest."""
    br = _br()
    if br.is_path(obj):
        return 0
    if br.is_routed(obj):
        return 2 if br.kind_of(obj) == br.BUNDLE else 1
    return 3


def _expand(objects):
    """The selected routed items with the paths they follow, the wires of a bundle, and the other objects (connector
    links), each once and in dependency order (paths first)."""
    br = _br()
    out, seen = [], set()

    def add(obj):
        if obj is not None and obj.Name not in seen:
            seen.add(obj.Name)
            out.append(obj)

    for obj in objects:
        if br.is_routed(obj):
            if br.kind_of(obj) == br.BUNDLE:
                for wire in obj.Wires:
                    add(wire.Path)
                    add(wire)
            add(obj.Path)
        add(obj)
    out.sort(key=_rank)
    return out


def _move(obj, container):
    old = None
    try:
        old = obj.getParentGeoFeatureGroup()
    except Exception:
        pass
    if old is container:
        return
    if old is not None:
        old.removeObject(obj)
    container.addObject(obj)


def make_harness(doc, objects, harness_type=None, label="Harness", into=None):
    """A Harness container holding ``objects`` (routed items, route paths, connector links), put in the document's
    assembly (``into`` overrides). Returns the container."""
    objects = _expand(objects)
    if not objects:
        raise ValueError("Select the wires, tubes or hoses (and the connector links) of the harness first.")
    for obj in objects:
        owner = harness_of(obj)
        if owner is not None:
            raise ValueError(f"{obj.Label} is already in {owner.Label}.")
    container = doc.addObject("App::Part", "Harness")
    container.Label = label
    container.addProperty("App::PropertyEnumeration", HARNESS_PROP, "Harness",
                          "Cable assembly (wires, bundles, connectors) or Tube assembly (tubes, hoses, fittings)")
    container.HarnessType = HARNESS_TYPES
    container.HarnessType = harness_type if harness_type in HARNESS_TYPES else suggested_type(objects)
    assembly = into if into is not None else _top_assembly(doc)
    if assembly is not None:
        assembly.addObject(container)
    for obj in objects:
        _move(obj, container)
    doc.recompute()
    return container


# ---------------------------------------------------------------------------------------------
# Copy into a file of its own.


def _flex():
    import bruceworx_flexsub

    return bruceworx_flexsub


def _build(path, pn, container, description, vault_root):
    """Write the harness file: a copy of ``container`` with the vault's part properties. Returns the plan for the
    second step: {"paths": [(copy name, [(index, parent object name, sub)])], "links": [(copy name, target doc path,
    target object name)]}. Saved and closed."""
    from bruceworx import partprops, pdm

    br, flex = _br(), _flex()
    doc = App.newDocument(pn.replace("-", "_"), hidden=True)
    plan = {"paths": [], "links": [], "unkept": []}
    try:
        doc.Label = pn
        main = doc.addObject("App::Part", "Harness")
        main.Label = description
        main.addProperty("App::PropertyEnumeration", HARNESS_PROP, "Harness", "Cable assembly or Tube assembly")
        main.HarnessType = HARNESS_TYPES
        main.HarnessType = container.HarnessType
        report = partprops.load_schema(extra_layers=partprops.vault_layers(vault_root)).apply(main)
        clash = [t for t in report["conflicts"] if t.split(" ", 1)[0] in ("PartNumber", "Revision")]
        if clash:
            raise ValueError("; ".join(clash))
        main.PartNumber = pn
        main.Revision = pdm.first_prerelease()
        if "Description" in main.PropertiesList:
            main.Description = description
        if "ItemType" in main.PropertiesList:
            main.ItemType = "assembly"
        copies = {}
        sources = sorted(contents(container), key=_rank)
        for src in sources:  # paths first (contents are in dependency order)
            if br.is_path(src):
                dst = doc.addObject("Part::FeaturePython", src.Name)
                type(src.Proxy)(dst)
                dst.Label = src.Label
                flex._copy_props(src, dst, {})
                dst.Points = [App.Vector(p) for p in src.Points]  # the last solved points
                copies[src.Name] = dst
                main.addObject(dst)
            elif br.is_routed(src):
                dst = doc.addObject("Part::FeaturePython", src.Name)
                type(src.Proxy)(dst)
                dst.Label = src.Label
                copies[src.Name] = dst
                main.addObject(dst)
                flex._copy_props(src, dst, copies)
                if br.kind_of(src) == br.BUNDLE:
                    dst.Wires = [copies[w.Name] for w in src.Wires if w.Name in copies]
                if src.Path is not None and src.Path.Name in copies:
                    dst.Path = copies[src.Path.Name]
            elif src.isDerivedFrom("App::Link"):
                dst = doc.addObject("App::Link", src.Name)
                dst.Label = src.Label
                dst.Placement = src.Placement
                copies[src.Name] = dst
                main.addObject(dst)
                target = src.getLinkedObject(True)
                if target is not None and target.Document is not None and target.Document.FileName:
                    plan["links"].append((dst.Name, target.Document.FileName, target.Name))
                else:
                    plan["unkept"].append(src.Label)
            else:
                plan["unkept"].append(src.Label)
        for src in sources:
            if not br.is_path(src):
                continue
            local, foreign = [], []
            for index, target, sub in br.attachments_of(src):
                if target.Name in copies:
                    local.append((index, copies[target.Name], sub))
                else:
                    foreign.append((index, target.Name, sub))
            if local:
                br.set_attachments(copies[src.Name], local)
            if foreign:
                plan["paths"].append((src.Name, foreign))
        doc.recompute()
        doc.saveAs(path)
    finally:
        App.closeDocument(doc.Name)
    return plan


def document_type_code(vault_root, name):
    """The vault's document type code named ``name`` ("Cable assembly"), or "" when it has none."""
    import json

    try:
        with open(os.path.join(vault_root, "document_types.json"), encoding="utf-8") as handle:
            types = json.load(handle).get("types", {})
    except (OSError, ValueError):
        return ""
    for code, entry in types.items():
        label = entry.get("name", "") if isinstance(entry, dict) else str(entry)
        retired = isinstance(entry, dict) and entry.get("retired")
        if label.strip().lower() == name.strip().lower() and not retired:
            return str(code)
    return ""


def _open(path):
    for doc in App.listDocuments().values():
        if doc.FileName and os.path.exists(doc.FileName) and os.path.samefile(doc.FileName, path):
            return doc
    return App.openDocument(path, True)


def make_vault_item(container, s, description="", program="", category=CATEGORY, comment=""):
    """Save the Harness container out as a vault item and link it into its document. ``s``: the vault client's
    Session (or anything with .vault, .work and .root). The document must be saved: the cross-document links are
    stored relative to its file. Returns (part number, link in the parent)."""
    from bruceworx import vault_setup

    br = _br()
    doc = container.Document
    if not is_harness(container):
        raise ValueError("Select a Harness container.")
    if not doc.FileName:
        raise ValueError("Save the assembly first: the harness paths attach to its parts through links stored "
                         "relative to its file.")
    description = (description or container.Label).strip()
    code = document_type_code(s.root, container.HarnessType)
    plan = {}

    def make(path, pn):
        plan.update(_build(path, pn, container, description, s.root))

    pn = vault_setup.new_item(s.vault, "assembly", description, make, program, code, category)
    if hasattr(s, "refresh"):
        s.refresh()
    work_path = s.work.checkout(pn, with_links=False)
    hdoc = _open(work_path)
    hpart = hdoc.getObject("Harness")
    for name, foreign in plan["paths"]:  # the points follow the parent's parts again
        path = hdoc.getObject(name)
        keep = list(br.attachments_of(path))
        add = []
        for index, target_name, sub in foreign:
            target = doc.getObject(target_name)
            if target is not None:
                add.append((index, target, sub))
        br.set_attachments(path, keep + add)
    for name, file, target_name in plan["links"]:
        link = hdoc.getObject(name)
        other = _open(file)
        target = other.getObject(target_name)
        if target is not None:
            link.setLink(target)
    hdoc.recompute()
    hdoc.save()
    from bruceworx_bom import CheckinBom

    hook, notes = CheckinBom(), {}
    data = hook.prepare(s, pn, notes)
    s.work.checkin(pn, comment or f"Routed in {doc.Label}: {description}")
    hook.record(s, pn, data, notes)
    link = _replace(container, hpart)
    return pn, link


def _replace(container, hpart):
    """Remove the container from its document and put a link to the harness file where it was."""
    doc = container.Document
    parent = container.getParentGeoFeatureGroup()
    label = container.Label
    for obj in reversed(contents(container)):
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass
    doc.removeObject(container.Name)
    link = doc.addObject("App::Link", "HarnessLink")
    link.setLink(hpart)
    link.Label = label
    if parent is not None:
        parent.addObject(link)
    doc.recompute()
    return link
