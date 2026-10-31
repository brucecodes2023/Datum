# SPDX-License-Identifier: LGPL-2.1-or-later
"""Flexible sub-assemblies carry routed items (ASM / ROU-08, 30 Oct).

``Assembly::AssemblyLink::synchronizeComponents`` (C++, ``[BW]``) calls ``sync(link, objmap, assembly)`` at the end of
every update. ``link`` is the AssemblyLink, ``assembly`` the AssemblyObject it finally links; ``objmap`` maps the name of each component of the linked assembly to the
App::Link (or AssemblyLink) that stands for it in ``link.Group``. The C++ side also asks ``is_routed`` so
that route paths, wires, hoses, pipes and bundles are not linked as if they were parts.

A route path and its items are **copied** into the link (a copy per sub-assembly instance), not linked: an
App::Link shows the source object's shape and cannot carry other attachments, whereas the copy is an ordinary
routed object that recomputes in the parent from its re-pointed attachments, so it follows the linked parts
the parent's solver moves. A copy has the dynamic property ``FlexSource`` (the source object's name). Each
update brings the copies in line with the sources: missing ones are made, changed values copied, attachments
re-pointed (``(part, "Pin1.")`` becomes ``(link to part, "Pin1.")``), copies of sources that are gone and all
copies of a rigid link removed.
"""

import FreeCAD as App

PROP = "FlexSource"
# Never copied from the source: the copy has its own shape, place, links and view.
_SKIP = {"Shape", "Placement", "Proxy", "Visibility", "ExpressionEngine", "Attachments", "AttachedTo", PROP,
         "Label2", "AttacherType", "AttacherEngine", "MapMode", "MapPathParameter", "MapReversed", "AttachmentOffset",
         "Support", "_Body", "Group", "BoundingBox", "ReferenceBox"}


def _br():
    import bruceworx_routing

    return bruceworx_routing


def is_routed(obj):
    """A route path or routed item (wire, pipe, hose, bundle)."""
    try:
        br = _br()
        return br.is_path(obj) or br.is_routed(obj)
    except Exception:
        return False


def is_copy(obj):
    return PROP in getattr(obj, "PropertiesList", ())


def copies(link):
    return [o for o in link.Group if is_copy(o)]


def _components(assembly):
    return [o for o in assembly.Group if not is_routed(o)]


def _chain(obj, top):
    """Names from ``top`` (exclusive) down to ``obj``, or None when ``obj`` is not below ``top``."""
    names, cur, seen = [], obj, set()
    while cur is not None and cur.Name not in seen:
        if cur is top:
            return list(reversed(names))
        seen.add(cur.Name)
        names.append(cur.Name)
        try:
            cur = cur.getParentGeoFeatureGroup()
        except Exception:
            return None
    return None


def repoint(obj, sub, components, objmap):
    """``(object, sub)`` of a source attachment as it reads in the parent, or None."""
    for top in components:
        names = _chain(obj, top)
        if names is None:
            continue
        mapped = objmap.get(top.Name)
        if mapped is None:
            return None
        prefix = "".join(n + "." for n in names)
        return mapped, prefix + (sub or "")
    return None


def _is_output(obj, name):
    """A property the object computes itself (read-only or output): the copy computes its own."""
    try:
        status = obj.getPropertyStatus(name)
        if "ReadOnly" in status or "Output" in status or "Hidden" in status and name == "RoutePoints":
            return True
        return "ReadOnly" in obj.getEditorMode(name)
    except Exception:
        return False


def _copy_points(src, dst, attached):
    """The free points of a path; the attached ones are the copy's own (re-read from its links)."""
    want = [App.Vector(p) for p in src.Points]
    have = [App.Vector(p) for p in dst.Points]
    if len(have) != len(want):
        dst.Points = want
        return
    merged = [have[i] if i in attached else want[i] for i in range(len(want))]
    if any((a - b).Length > 1e-9 for a, b in zip(merged, have)):
        dst.Points = merged


def _copy_props(src, dst, links):
    for name in src.PropertiesList:
        if name in _SKIP or name == "Points" or name == "RoutePoints" or _is_output(src, name):
            continue
        kind = src.getTypeIdOfProperty(name)
        if name not in dst.PropertiesList:
            try:
                dst.addProperty(kind, name, src.getGroupOfProperty(name), src.getDocumentationOfProperty(name))
            except Exception:
                continue
        if kind == "App::PropertyLink":
            target = links.get(getattr(getattr(src, name, None), "Name", None))
            if getattr(getattr(dst, name, None), "Name", None) != getattr(target, "Name", None):
                setattr(dst, name, target)
            continue
        if "Link" in kind or "Material" in kind or "Appearance" in kind:
            continue  # links are re-pointed; materials compare by identity and would touch every time
        try:
            value = getattr(src, name)
            if getattr(dst, name) != value:
                setattr(dst, name, value)
        except Exception:
            pass


def _new_copy(link, src):
    br = _br()
    obj = link.Document.addObject("Part::FeaturePython", src.Name)
    type(src.Proxy)(obj)
    obj.addProperty("App::PropertyString", PROP, "BruceWorx", "The routed object of the sub-assembly this copies")
    obj.setEditorMode(PROP, 2)
    obj.__setattr__(PROP, src.Name)
    if App.GuiUp and obj.ViewObject is not None:
        try:
            (br.ViewProviderRoutePath if br.is_path(src) else br.ViewProviderRouted)(obj.ViewObject)
        except Exception:
            pass
    link.addObject(obj)
    return obj


def sync(link, objmap, assembly=None):
    """Bring the routed copies in ``link`` in line with its linked assembly."""
    try:
        br = _br()
    except Exception:
        return
    existing = {o.getPropertyByName(PROP): o for o in copies(link)}
    assembly = assembly if assembly is not None else link.LinkedObject
    sources = []
    if not link.Rigid and assembly is not None and hasattr(assembly, "Group"):
        sources = [o for o in assembly.Group if is_routed(o)]
    wanted = {s.Name for s in sources}
    for name, obj in list(existing.items()):
        if name not in wanted:
            link.Document.removeObject(obj.Name)
            del existing[name]
    if not sources:
        return
    components = _components(assembly)
    by_source = {}
    for src in sorted(sources, key=lambda o: 0 if br.is_path(o) else 1):  # paths first: items link to them
        obj = existing.get(src.Name)
        if obj is None:
            obj = _new_copy(link, src)
            existing[src.Name] = obj
        by_source[src] = obj
        _copy_props(src, obj, {**objmap, **{s.Name: c for s, c in by_source.items()}})
        if br.is_path(src):
            _copy_points(src, obj, {i for i, _t, _s in br.attachments_of(obj)})
            wanted_att = []
            for index, target, sub in br.attachments_of(src):
                found = repoint(target, sub, components, objmap)
                if found is not None:
                    wanted_att.append((index, found[0], found[1]))
            current = [(i, t.Name, s) for i, t, s in br.attachments_of(obj)]
            if current != [(i, t.Name, s) for i, t, s in wanted_att]:
                br.set_attachments(obj, wanted_att)
