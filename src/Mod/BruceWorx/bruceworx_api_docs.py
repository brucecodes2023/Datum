# SPDX-License-Identifier: LGPL-2.1-or-later
"""What a document holds, for agents (AUT-02): the census of solids and the difference of two.

A census lists every solid of an assembly (or a document), wherever it is nested, so that "did anything move or
go missing" after a mating run, a save or a restructure is one comparison instead of a look at the screen.

    census(obj_or_doc)                  -> [{"file", "path", "label", "solid", "bbox", "volume"}]
    census_diff(a, b, tol=1e-3)         -> {"added", "removed", "moved", "changed", "renamed", "count_a", "count_b"}
    save_census(records, path), load_census(path)

* file: the file the solid is modelled in (a linked part's own file); the document's Name when it has none.
* path: object Names from the given object down to the solid's feature ("SubAsm.Link.Body"); "" for the object
  itself. A link array's elements are numbered ("Array.0").
* bbox: [xmin, ymin, zmin, xmax, ymax, zmax] rounded to 1e-3 mm. Of an object: in that object's own frame (its
  own Placement is left out, so the census of an assembly does not depend on where the assembly sits). Of a
  document: in global coordinates.
* volume: mm3 rounded to 1e-3. solid: its number in the feature's shape (0 unless the feature has several).

Hidden features are skipped (a part made parametric keeps its imported solid hidden beside the Body), except the
components of an assembly themselves: a hidden component is still part of the assembly.

census_diff needs no FreeCAD.
"""

from __future__ import annotations

import json
import os

ROUND = 3
MAX_DEPTH = 32
_ASSEMBLY_TYPES = ("Assembly::AssemblyObject", "Assembly::AssemblyLink")


# ----- census ------------------------------------------------------------------------------------------------------


def _is_document(target):
    return hasattr(target, "Objects") and hasattr(target, "FileName")


def _file_of(obj):
    doc = obj.Document
    return os.path.basename(doc.FileName) if doc.FileName else doc.Name


def _resolved(obj):
    """What a link (or a link's element) shows; the object itself otherwise."""
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        target = None
    return target if target is not None else obj


def _walk(obj, path, outer, hidden_counts, out, depth=0):
    """Collect (path, feature, label, placed shape) for the solid-bearing features under ``obj``.

    Placements are multiplied down by hand, the way the proven scratch census did (bw_mates.features). An earlier
    version asked Part.getShape for each element path; where a path did not resolve it handed back the whole
    container, and the top assembly of TL-P01 counted 21,408 solids for 1,796 (10 Nov 2026)."""
    import FreeCAD as App

    if depth > MAX_DEPTH:
        return
    if obj.TypeId == "Assembly::AssemblyLink":  # a grouping: its members are its own links, in its frame
        inner = outer.multiply(obj.Placement)
        for child in getattr(obj, "Group", []) or []:
            _walk(child, f"{path}{child.Name}.", inner, True, out, depth + 1)
        return
    target = _resolved(obj)
    linked = target is not obj
    solid_feature = target.isDerivedFrom("Part::Feature")  # a Body is one: its result, not each feature
    if solid_feature and not hidden_counts and not getattr(obj, "Visibility", True):
        return
    if linked:
        base = outer.multiply(obj.Placement)
        if getattr(obj, "LinkTransform", False):
            base = base.multiply(target.Placement)
        places = [base]
        if getattr(obj, "ElementCount", 0):
            places = [base.multiply(p) for p in obj.PlacementList]
        for i, place in enumerate(places):
            sub = path if len(places) == 1 else f"{path}{i}."
            if solid_feature:
                shape = target.Shape.copy()
                shape.Placement = place
                out.append((sub, target, obj.Label, shape))
            else:
                _group(target, sub, place, out, depth)
        return
    if solid_feature:
        shape = obj.Shape.copy()
        shape.Placement = outer.multiply(shape.Placement)
        out.append((path, obj, obj.Label, shape))
        return
    _group(obj, path, outer.multiply(getattr(obj, "Placement", App.Placement())), out, depth)


def _group(holder, path, place, out, depth):
    group = getattr(holder, "Group", None)
    if not group:
        return
    components = holder.TypeId in _ASSEMBLY_TYPES  # a hidden component is still part of the assembly
    for child in group:
        _walk(child, f"{path}{child.Name}.", place, components, out, depth + 1)


def _records(leaves):
    out = []
    for path, feature, label, shape in leaves:
        if shape is None or shape.isNull():
            continue
        for i, solid in enumerate(shape.Solids):
            b = solid.BoundBox
            out.append({
                "file": _file_of(feature),
                "path": path.rstrip("."),
                "label": label,
                "solid": i,
                "bbox": [round(v, ROUND) + 0.0 for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)],
                "volume": round(solid.Volume, ROUND),
            })
    return out


def _roots(doc):
    """The objects nothing else holds; an object only links point at still stands on its own."""
    out = []
    for obj in doc.Objects:
        holders = [p for p in obj.InList if not p.isDerivedFrom("App::Link")]
        if not holders:
            out.append(obj)
    return out


def census(target) -> list:
    """Every solid of an object (an assembly, a part, a body) or of a document. See the module text."""
    import FreeCAD as App

    out = []
    if _is_document(target):
        for root in _roots(target):
            leaves = []
            _walk(root, "", App.Placement(), False, leaves)
            for rec in _records(leaves):
                rec["path"] = root.Name + ("." + rec["path"] if rec["path"] else "")
                out.append(rec)
        return out
    leaves = []
    if target.isDerivedFrom("Part::Feature"):
        leaves.append(("", target, target.Label, target.Shape.copy()))
    else:  # in the object's own frame: its placement is left out
        _group(target, "", App.Placement(), leaves, 0)
    return _records(leaves)


# ----- files -------------------------------------------------------------------------------------------------------


def save_census(records, path, source="") -> str:
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"census": 1, "source": source, "count": len(records), "solids": records}, handle, indent=1)
    os.replace(tmp, path)
    return path


def load_census(path) -> list:
    with open(os.path.expanduser(path), encoding="utf-8") as handle:
        data = json.load(handle)
    records = data.get("solids") if isinstance(data, dict) else data
    if not isinstance(records, list) or not all(isinstance(r, dict) and "bbox" in r for r in records):
        raise ValueError(f"{path} is not a census (save_census writes one)")
    return records


# ----- the difference (no FreeCAD) ---------------------------------------------------------------------------------


def _key(rec):
    return (rec.get("file", ""), rec.get("path", ""), rec.get("solid", 0))


def _shift(a, b):
    return max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"]))


def _brief(rec):
    return {k: rec.get(k) for k in ("file", "path", "label", "solid", "bbox", "volume")}


def census_diff(a, b, tol=1e-3) -> dict:
    """What changed from census ``a`` to census ``b``.

    * removed / added: a solid only ``a`` / only ``b`` has (matched by file, path and solid number).
    * moved: the same solid with a box that differs by more than ``tol`` mm; "shift" is the largest difference.
    * changed: the same solid in the same place with another volume (more than tol mm3 and one part in a million).
    * renamed: a solid whose path changed and nothing else (same file, box and volume): not added or removed.
    """
    tol = float(tol) + 1e-9
    left = {}
    for rec in a:
        left.setdefault(_key(rec), []).append(rec)
    added, removed, moved, changed = [], [], [], []
    for rec in b:
        twins = left.get(_key(rec))
        if not twins:
            added.append(rec)
            continue
        old = twins.pop(0)
        shift = _shift(old, rec)
        dv = abs(old.get("volume", 0.0) - rec.get("volume", 0.0))
        if shift > tol:
            moved.append({**_brief(rec), "was": old["bbox"], "shift": round(shift, ROUND)})
        elif dv > tol and dv > 1e-6 * max(abs(old.get("volume", 0.0)), 1.0):
            changed.append({**_brief(rec), "was_volume": old.get("volume")})
    for twins in left.values():
        removed.extend(twins)
    renamed = []
    for rec in list(added):  # the same solid under another path
        for old in removed:
            if (old.get("file") == rec.get("file") and _shift(old, rec) <= tol
                    and abs(old.get("volume", 0.0) - rec.get("volume", 0.0)) <= tol):
                renamed.append({**_brief(rec), "was_path": old.get("path")})
                removed.remove(old)
                added.remove(rec)
                break
    return {"added": [_brief(r) for r in added], "removed": [_brief(r) for r in removed], "moved": moved,
            "changed": changed, "renamed": renamed, "count_a": len(a), "count_b": len(b)}


__all__ = ["census", "census_diff", "save_census", "load_census"]
