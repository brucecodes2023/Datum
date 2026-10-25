# SPDX-License-Identifier: LGPL-2.1-or-later
"""Interference and clearance between the parts of an assembly (roadmap ASM-06).

Every pair of occurrences is sorted into one of:

* clash      their material overlaps by more than VOLUME_TOL (mm³); reported with the volume and
             the depth (how far the overlap reaches into either part),
* fit        a clash between two parts marked as an intended overlap (a screw in its tapped hole,
             a press fit): the same marks the section views use, so both agree,
* contact    they touch (closer than DISTANCE_TOL) with no volume to speak of,
* clearance  they are apart, but closer than the gap asked for (0.2 mm for FDM prints, say).

Broad phase: the occurrences' bounding boxes, grown by the gap, are swept along X; only pairs whose
boxes meet on all three axes go on. Narrow phase, per pair: OpenCASCADE's proximity search on meshes
of the two parts names the faces that come within the gap (plus twice the meshes' deflection) of the
other part; only those are measured exactly (far faces cannot be nearest; that is what makes it
fast). Apart and neither inside the other: clearance or nothing. Touching: the exact Boolean common
says clash or contact.

A fit is kept per pair of *parts* (the occurrences' link targets, "Doc#Name"), like the section view
keeps it: marking the screw in one tapped hole marks the other three screws of that kind too. They
are stored on the assembly (Fits, and FitNotes for the reason) and copied to every section of the
file, whose Fits the section view reads.

Headless: check(assembly) runs it all; Checker steps through the pairs a few at a time, for the GUI.
"""

from __future__ import annotations

import time

VOLUME_TOL = 1e-3  # mm³: less is a touching face (a gear mesh rounds to 1e-4), not a clash
DISTANCE_TOL = 1e-4  # mm: closer is touching
GAP_EPS = 1e-6  # a gap of exactly the clearance asked for passes
MIN_DEFLECTION, MAX_DEFLECTION = 0.01, 0.2  # mm: the meshes the proximity search runs on (0.1 % of the part)
DEPTH_SAMPLES = 80  # points of an overlap measured for its depth
CLASH, FIT, CONTACT, CLEARANCE = "clash", "fit", "contact", "clearance"
KINDS = (CLASH, FIT, CONTACT, CLEARANCE)
PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx/Interference"
SKIPPED_TYPES = ("App::Origin", "App::Line", "App::Plane", "App::Point", "Assembly::JointGroup",
                 "Assembly::ViewGroup", "Assembly::BomGroup", "Assembly::SimulationGroup",
                 "PartDesign::CoordinateSystem", "App::LocalCoordinateSystem", "Spreadsheet::Sheet")


def _app():
    import FreeCAD

    return FreeCAD


# ----- keys and fits (same keys as bruceworx_section) ----------------------------------------------


def fit_key(a, b):
    """The sorted pair key of two parts, "Doc#A\\tDoc#B" (bruceworx_section.fit_key)."""
    return "\t".join(sorted((a, b)))


def object_key(obj):
    return f"{obj.Document.Name}#{obj.Name}"


def _sections(doc):
    try:
        import bruceworx_section as sec

        return sec.sections(doc)
    except Exception:  # noqa: BLE001 -- no section module: no sections
        return [o for o in doc.Objects if hasattr(o, "Fits") and getattr(o, "Proxy", None).__class__.__name__ == "SectionPlane"]


def _add_fit_properties(holder):
    if not hasattr(holder, "Fits"):
        holder.addProperty("App::PropertyStringList", "Fits", "Interference",
                           "Part pairs whose overlap is intended: left out of the clashes and not red in sections")
    if not hasattr(holder, "FitNotes"):
        holder.addProperty("App::PropertyMap", "FitNotes", "Interference", "Why each fit overlaps")


def fits(doc, assembly=None):
    """{key: note} of every fit in the file: the assembly's and every section's."""
    out = {}
    for sec in _sections(doc):
        for key in getattr(sec, "Fits", []):
            if "\t" in key:
                out.setdefault(fit_key(*key.split("\t", 1)), "")
    holders = [assembly] if assembly is not None else assemblies(doc)
    for holder in holders:
        notes = dict(getattr(holder, "FitNotes", {}) or {})
        for key in getattr(holder, "Fits", []):
            if "\t" in key:
                key = fit_key(*key.split("\t", 1))
                out[key] = notes.get(key, "") or out.get(key, "")
    return out


def set_fit(doc, first, second, on=True, note="", assembly=None):
    """Mark (or unmark) the parts ``first`` and ``second`` ("Doc#Name") as an intended fit, on the
    assembly and in every section of the file, so the section view agrees."""
    key = fit_key(first, second)
    holders = [assembly] if assembly is not None else assemblies(doc)
    for holder in holders:
        _add_fit_properties(holder)
        keys = [k for k in holder.Fits if fit_key(*k.split("\t", 1)) != key] if holder.Fits else []
        notes = {k: v for k, v in dict(holder.FitNotes).items() if k != key}
        if on:
            keys.append(key)
            if note:
                notes[key] = note
        holder.Fits = keys
        holder.FitNotes = notes
    for sec in _sections(doc):
        keys = [k for k in sec.Fits if "\t" not in k or fit_key(*k.split("\t", 1)) != key]
        if on:
            keys.append(key)
        if keys != list(sec.Fits):
            sec.Fits = keys


# ----- occurrences ----------------------------------------------------------------------------------


def assemblies(doc):
    return [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"]


def active_assembly(doc=None):
    """The assembly being edited (Assembly workbench), else the file's top assembly, else None."""
    App = _app()
    doc = doc or App.ActiveDocument
    if doc is None:
        return None
    if App.GuiUp:
        try:
            import FreeCADGui as Gui

            view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
            active = view.getActiveObject("part") if view is not None else None
            if active is not None and active.TypeId == "Assembly::AssemblyObject" and active.Document is doc:
                return active
        except Exception:  # noqa: BLE001
            pass
    found = assemblies(doc)
    tops = [a for a in found if not any(p.TypeId == "Assembly::AssemblyObject" for p in a.InListRecursive)]
    return (tops or found or [None])[0]


class Occurrence:
    """One part placed in the assembly: ``sub`` is its path below ``root`` ("Link." or
    "SubAsm.Link."), ``part`` its link target "Doc#Name", ``shape`` its solids in root's frame
    (a copy, meshed here for the proximity search without touching the file's own meshes)."""

    __slots__ = ("root", "sub", "part", "label", "shape", "box", "faces", "deflection")

    def __init__(self, root, sub, part, label, shape):
        self.root, self.sub, self.part, self.label = root, sub, part, label
        self.shape = shape.copy()
        self.box = self.shape.BoundBox
        self.faces = None
        self.deflection = None

    @property
    def key(self):
        return f"{self.root.Document.Name}#{self.root.Name}.{self.sub}"

    def face_list(self):
        """[(face, box tuple, surface)] for the narrow phase, made once."""
        if self.faces is None:
            self.faces = [(f, _box(f.BoundBox), _surface(f)) for f in self.shape.Faces]
        return self.faces

    def meshed(self):
        """The mesh's deflection (mm), meshing the shape the first time."""
        if self.deflection is None:
            self.deflection = min(MAX_DEFLECTION, max(MIN_DEFLECTION, self.box.DiagonalLength * 1e-3))
            self.shape.tessellate(self.deflection)
        return self.deflection


def _solids(shape):
    import Part

    solids = shape.Solids
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Part.makeCompound(solids)


def _children(container):
    return list(getattr(container, "Group", []) or [])


def occurrences(root, include_hidden=False):
    """The parts placed in ``root`` (an assembly, or a document's top level), sub-assemblies opened."""
    import Part

    out = []

    def visible(obj, parent):
        if include_hidden:
            return True
        try:
            return parent.isElementVisible(obj.Name) != 0 and obj.Visibility
        except Exception:  # noqa: BLE001
            return bool(getattr(obj, "Visibility", True))

    def walk(container, prefix, label_prefix):
        for obj in _children(container):
            if obj.TypeId in SKIPPED_TYPES:
                continue
            if obj.isDerivedFrom("App::DocumentObjectGroup"):  # a folder: its parts
                walk(obj, f"{prefix}{obj.Name}.", label_prefix)
                continue
            if obj.isDerivedFrom("App::FeaturePython") and not hasattr(obj, "Shape"):
                continue  # joints and other data objects
            if not visible(obj, container):
                continue
            target = obj.getLinkedObject(True) if hasattr(obj, "getLinkedObject") else obj
            if target is None:
                continue
            if target.TypeId == "Assembly::AssemblyObject" and target is not root:
                walk_sub(obj, target, prefix, label_prefix)
                continue
            count = getattr(obj, "ElementCount", 0) or 0
            subs = [(f"{prefix}{obj.Name}.{i}.", f"{label_prefix}{obj.Label} [{i + 1}]") for i in range(count)] \
                if count else [(f"{prefix}{obj.Name}.", f"{label_prefix}{obj.Label}")]
            for sub, label in subs:
                try:
                    shape = Part.getShape(root, sub, needSubElement=False, transform=True)
                except Exception:  # noqa: BLE001 -- nothing to check in it
                    continue
                solids = _solids(shape) if not shape.isNull() else None
                if solids is not None:
                    out.append(Occurrence(root, sub, object_key(target), label, solids))

    def walk_sub(link, target, prefix, label_prefix):
        """A sub-assembly: its parts, through the link that places it."""
        sub_prefix = f"{prefix}{link.Name}." if link is not target else prefix
        walk(target, sub_prefix, f"{label_prefix}{link.Label} / ")

    walk(root, "", "")
    return out


# ----- broad phase ----------------------------------------------------------------------------------


def boxes_meet(a, b, grow=0.0):
    """Do the boxes (xmin, ymin, zmin, xmax, ymax, zmax) meet, each grown by ``grow``?"""
    return all(a[i] - grow <= b[i + 3] + grow and b[i] - grow <= a[i + 3] + grow for i in range(3))


def candidate_pairs(boxes, grow=0.0):
    """Index pairs (i < j) whose boxes meet when grown by ``grow``: sort and sweep along X."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    active = []
    out = []
    for i in order:
        lo = boxes[i][0] - grow
        active = [j for j in active if boxes[j][3] + grow >= lo]
        for j in active:
            if boxes_meet(boxes[i], boxes[j], grow):
                out.append((min(i, j), max(i, j)))
        active.append(i)
    return sorted(out)


def _box(bb):
    return (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)


# ----- narrow phase ---------------------------------------------------------------------------------


def _grown(bb, d):
    App = _app()
    out = App.BoundBox(bb)
    out.enlarge(d)
    return out


def box_gap(a, b):
    """The distance between two boxes (xmin, ymin, zmin, xmax, ymax, zmax); 0 when they meet."""
    return sum(max(0.0, a[i] - b[i + 3], b[i] - a[i + 3]) ** 2 for i in range(3)) ** 0.5


def corners(box):
    return [(x, y, z) for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])]


def _surface(face):
    """("plane", normal, offset) or ("cylinder", point, axis, radius) of a face, else None."""
    try:
        surf = face.Surface
        name = type(surf).__name__
        if name == "Plane":
            n, p = surf.Axis, surf.Position
            return ("plane", (n.x, n.y, n.z), n.dot(p))
        if name == "Cylinder":
            a, c = surf.Axis, surf.Center
            return ("cylinder", (c.x, c.y, c.z), (a.x, a.y, a.z), surf.Radius)
    except Exception:  # noqa: BLE001
        pass
    return None


def surface_gap(surface, box):
    """A lower bound of the distance from ``surface``'s face to any point of ``box``: a plane the
    box lies wholly to one side of; a cylinder the box lies wholly inside, off the wall."""
    if surface is None:
        return 0.0
    if surface[0] == "plane":
        _kind, n, offset = surface
        d = [n[0] * x + n[1] * y + n[2] * z - offset for x, y, z in corners(box)]
        return max(0.0, min(d), -max(d))
    _kind, c, a, radius = surface
    far = 0.0  # distance from the axis is convex: its largest value on the box is at a corner
    for x, y, z in corners(box):
        v = (x - c[0], y - c[1], z - c[2])
        t = v[0] * a[0] + v[1] * a[1] + v[2] * a[2]
        far = max(far, (max(0.0, v[0] ** 2 + v[1] ** 2 + v[2] ** 2 - t * t)) ** 0.5)
    return max(0.0, radius - far)


def _coincident(sa, sb, tol):
    """Do two faces lie on the same plane, or the same cylinder? (Where parts sit on each other.)"""
    if sa is None or sb is None or sa[0] != sb[0]:
        return False
    if sa[0] == "plane":
        dot = sum(x * y for x, y in zip(sa[1], sb[1]))
        return abs(abs(dot) - 1.0) < 1e-9 and abs(sa[2] - dot * sb[2]) <= tol
    (_k, ca, aa, ra), (_k2, cb, ab, rb) = sa, sb
    dot = sum(x * y for x, y in zip(aa, ab))
    if abs(abs(dot) - 1.0) > 1e-9 or abs(ra - rb) > tol:
        return False
    v = [y - x for x, y in zip(ca, cb)]
    t = sum(x * y for x, y in zip(v, aa))
    return sum(x * x for x in v) - t * t <= tol * tol


def face_pairs(a, b, reach):
    """[(lower bound, face of a, face of b)] that could come within ``reach``: the faces the
    meshes' proximity search finds, paired where their boxes and surfaces allow. Faces on the same
    plane or cylinder come first (that is where parts touch), then the nearest boxes."""
    tol = reach + 2.0 * max(a.meshed(), b.meshed())
    try:
        ia, ib = a.shape.proximity(b.shape, tol)
    except Exception:  # noqa: BLE001 -- no proximity search: every face is a candidate
        ia, ib = range(1, len(a.face_list()) + 1), range(1, len(b.face_list()) + 1)
    fa = [a.face_list()[k - 1] for k in ia]
    fb = [b.face_list()[k - 1] for k in ib]
    out = []
    for f, boxf, surff in fa:
        for g, boxg, surfg in fb:
            lower = box_gap(boxf, boxg)
            if lower > reach:
                continue
            lower = max(lower, surface_gap(surff, boxg), surface_gap(surfg, boxf))
            if lower <= reach:
                out.append((lower, not _coincident(surff, surfg, DISTANCE_TOL), f, g))
    out.sort(key=lambda p: (p[1], p[0]))
    return [(lower, f, g) for lower, _apart, f, g in out]


QUICK_PAIRS = 12  # face pairs measured one by one for a quick touch before the whole


def near_distance(a, b, reach):
    """(distance, point on a, point on b) between the parts' boundaries when they come within
    ``reach`` of each other, else None. Only the face pairs face_pairs() finds are measured: the
    first few one by one (the first that touches answers it), then all of them at once."""
    import Part

    pairs = face_pairs(a, b, reach)
    if not pairs:
        return None
    for lower, f, g in pairs[:QUICK_PAIRS]:
        if lower > DISTANCE_TOL:
            continue
        touch = _touching_on_surface(f, g)
        if touch is not None:
            return (0.0, touch, touch)
        d, points, _info = f.distToShape(g)
        if d <= DISTANCE_TOL:
            return (d, points[0][0], points[0][1]) if points else (d, None, None)
    fa = list({id(f): f for _l, f, _g in pairs}.values())
    fb = list({id(g): g for _l, _f, g in pairs}.values())
    d, points, _info = Part.makeCompound(fa).distToShape(Part.makeCompound(fb))
    if d > reach:
        return None
    return (d, points[0][0], points[0][1]) if points else (d, None, None)


def _on_face(face, point, tol=DISTANCE_TOL):
    """Is ``point`` on ``face`` (on its surface, inside its edges)?"""
    try:
        u, v = face.Surface.parameter(point)
        return (face.Surface.value(u, v) - point).Length <= tol and face.isPartOfDomain(u, v)
    except Exception:  # noqa: BLE001
        return False


def _touching_on_surface(f, g):
    """A point where two faces on the same plane or cylinder touch: a vertex of one on the other.
    None when no vertex shows it (they may still touch; the distance decides then). Much faster
    than measuring a face with hundreds of edges (a ring gear's end face)."""
    if not _coincident(_surface(f), _surface(g), DISTANCE_TOL):
        return None
    for one, other in ((f, g), (g, f)):
        for vertex in one.Vertexes:
            if _on_face(other, vertex.Point):
                return vertex.Point
    return None


def _inside(outer, inner):
    """Is ``inner`` wholly inside ``outer``? (Only asked when their boundaries do not meet.)"""
    if not outer.box.isInside(inner.box):
        return False
    verts = inner.shape.Vertexes
    point = verts[0].Point if verts else inner.shape.Faces[0].CenterOfMass
    return any(s.isInside(point, 1e-7, False) for s in outer.shape.Solids)


def _sample_points(shape, limit=DEPTH_SAMPLES):
    """Points on the boundary of ``shape``: its vertices, then points along its edges and faces."""
    pts = [v.Point for v in shape.Vertexes]
    for e in shape.Edges:
        try:
            p0, p1 = e.ParameterRange
            pts.extend(e.valueAt(p0 + (p1 - p0) * t) for t in (0.25, 0.5, 0.75))
        except Exception:  # noqa: BLE001
            pass
    try:
        size = shape.BoundBox.DiagonalLength
        mesh_pts, _tris = shape.tessellate(max(size / 20.0, 0.01))
        pts.extend(mesh_pts)
    except Exception:  # noqa: BLE001
        pass
    if len(pts) > limit:
        step = len(pts) / float(limit)
        pts = [pts[int(i * step)] for i in range(limit)]
    return pts


def clash_depth(common, a, b):
    """How deep the overlap reaches: the largest distance from a point of the overlap's boundary
    to the boundary of either part (a bolt 4 mm too long for its blind hole: 4.0; a thread
    modelled at the tap-drill size: the thread's radial overlap)."""
    import Part

    reach = common.BoundBox.DiagonalLength
    box = _box(_grown(common.BoundBox, reach))
    walls = []
    for occ in (a, b):
        faces = [f for f, fbox, _s in occ.face_list() if box_gap(fbox, box) == 0.0]
        if faces:
            walls.append(Part.makeCompound(faces))
    depth = 0.0
    for p in _sample_points(common):
        v = Part.Vertex(p)
        for wall in walls:
            depth = max(depth, wall.distToShape(v)[0])
    return min(depth, reach)


class Result:
    """One pair: kind (clash, fit, contact, clearance), the volume or distance, where."""

    __slots__ = ("first", "second", "kind", "volume", "depth", "distance", "points", "overlap", "seconds")

    def __init__(self, first, second, kind, volume=0.0, depth=0.0, distance=0.0, points=None, overlap=None):
        self.first, self.second, self.kind = first, second, kind
        self.volume, self.depth, self.distance = volume, depth, distance
        self.points, self.overlap, self.seconds = points, overlap, 0.0

    @property
    def fit_key(self):
        return fit_key(self.first.part, self.second.part)

    @property
    def pair_label(self):
        return f"{self.first.label} / {self.second.label}"

    def text(self):
        if self.kind in (CLASH, FIT):
            return f"{self.volume:.1f} mm³, {self.depth:.1f} mm deep" if self.volume >= 0.05 else \
                f"{self.volume:.3f} mm³, {self.depth:.2f} mm deep"
        if self.kind == CLEARANCE:
            return f"{self.distance:.2f} mm apart"
        return "touching"

    def as_dict(self):
        return {"first": self.first.label, "second": self.second.label, "kind": self.kind,
                "firstPath": self.first.sub, "secondPath": self.second.sub,
                "parts": [self.first.part, self.second.part], "volume": round(self.volume, 4),
                "depth": round(self.depth, 4), "distance": round(self.distance, 4)}


def check_pair(a, b, gap=0.0, contacts=True, fit_keys=(), depth=True):
    """The Result for occurrences ``a`` and ``b``, or None when they are clear of each other.

    Boundaries apart: clearance or clear, unless one part is buried in the other (a clash).
    Boundaries touching: the exact Boolean common says clash or contact."""
    near = near_distance(a, b, max(gap, DISTANCE_TOL))
    if near is None or near[0] > DISTANCE_TOL:
        for outer, inner in ((a, b), (b, a)):
            if _inside(outer, inner):
                return _clash(a, b, inner.shape, fit_keys, depth)
        if near is not None and near[0] < gap - GAP_EPS:
            return Result(a, b, CLEARANCE, distance=near[0], points=(near[1], near[2]))
        return None
    common = a.shape.common(b.shape)
    if not common.isNull() and common.Volume > VOLUME_TOL:
        return _clash(a, b, common, fit_keys, depth)
    return Result(a, b, CONTACT, distance=near[0], points=(near[1], near[2])) if contacts else None


def _clash(a, b, overlap, fit_keys, depth):
    kind = FIT if fit_key(a.part, b.part) in fit_keys else CLASH
    d = clash_depth(overlap, a, b) if depth else 0.0
    return Result(a, b, kind, volume=overlap.Volume, depth=d, overlap=overlap)


# ----- the whole check ------------------------------------------------------------------------------


class Checker:
    """Checks an assembly a few pairs at a time: ``step(seconds)`` works until the time is up and
    says whether it is done, so a GUI can keep drawing in between. ``results`` grows as it goes."""

    def __init__(self, root, gap=0.0, contacts=True, fit_keys=None, include_hidden=False):
        self.root = root
        self.gap = max(0.0, float(gap))
        self.contacts = contacts
        self.fit_keys = set(fits(root.Document, root) if fit_keys is None else fit_keys)
        started = time.time()
        self.occurrences = occurrences(root, include_hidden)
        boxes = [_box(o.box) for o in self.occurrences]
        self.pairs = candidate_pairs(boxes, max(self.gap, DISTANCE_TOL))
        self.done = 0
        self.results = []
        self.errors = []
        self.seconds = time.time() - started

    @property
    def total(self):
        return len(self.pairs)

    @property
    def finished(self):
        return self.done >= len(self.pairs)

    def step(self, budget=0.05):
        """Check pairs for about ``budget`` seconds (at least one). True when all are done."""
        until = time.time() + budget
        while not self.finished:
            i, j = self.pairs[self.done]
            a, b = self.occurrences[i], self.occurrences[j]
            started = time.time()
            try:
                r = check_pair(a, b, self.gap, self.contacts, self.fit_keys)
            except Exception as exc:  # noqa: BLE001 -- a bad shape: say so, check the rest
                self.errors.append(f"{a.label} / {b.label}: {exc}")
                r = None
            spent = time.time() - started
            self.seconds += spent
            if r is not None:
                r.seconds = spent
                self.results.append(r)
            self.done += 1
            if time.time() >= until:
                break
        return self.finished

    def run(self):
        while not self.step(1e9):
            pass
        return self

    def of_kind(self, kind):
        return [r for r in self.results if r.kind == kind]

    def refit(self, fit_keys):
        """New fit marks: clashes and fits swap without checking again."""
        self.fit_keys = set(fit_keys)
        for r in self.results:
            if r.kind in (CLASH, FIT):
                r.kind = FIT if r.fit_key in self.fit_keys else CLASH

    def summary(self):
        counts = {k: len(self.of_kind(k)) for k in KINDS}
        return {"assembly": self.root.Label, "parts": len(self.occurrences), "pairs": len(self.occurrences) * (len(self.occurrences) - 1) // 2,
                "candidates": self.total, "checked": self.done, "gap": self.gap, "seconds": round(self.seconds, 3),
                "counts": counts, "errors": list(self.errors),
                "results": [r.as_dict() for r in sorted(self.results, key=_order)]}


def _order(r):
    return (KINDS.index(r.kind), -r.volume, r.distance, r.pair_label)


def check(root, gap=0.0, contacts=True, fit_keys=None, include_hidden=False):
    """Check ``root`` (an assembly) all at once; the finished Checker."""
    return Checker(root, gap, contacts, fit_keys, include_hidden).run()


def check_file(path, gap=0.0):
    """Open ``path``, check its top assembly, close it; the summary (for the release check)."""
    App = _app()
    doc = App.openDocument(path, hidden=True) if App.GuiUp else App.openDocument(path)
    try:
        root = active_assembly(doc)
        if root is None:
            return {"assembly": None, "counts": {k: 0 for k in KINDS}, "results": [], "errors": ["no assembly"]}
        return check(root, gap).summary()
    finally:
        App.closeDocument(doc.Name)


def prefs():
    App = _app()
    p = App.ParamGet(PREFS_PATH)
    return {"gap": p.GetFloat("Gap", 0.2), "clearance": p.GetBool("CheckClearance", True),
            "contacts": p.GetBool("ShowContacts", True)}


def save_prefs(gap=None, clearance=None, contacts=None):
    p = _app().ParamGet(PREFS_PATH)
    if gap is not None:
        p.SetFloat("Gap", float(gap))
    if clearance is not None:
        p.SetBool("CheckClearance", bool(clearance))
    if contacts is not None:
        p.SetBool("ShowContacts", bool(contacts))


__all__ = ["check", "check_file", "check_pair", "Checker", "candidate_pairs", "occurrences", "fits", "set_fit",
           "active_assembly", "clash_depth", "fit_key"]
