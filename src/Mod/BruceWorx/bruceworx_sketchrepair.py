# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sketch profile repair (roadmap MOD-13, from the Nxt add-on review).

When a PartDesign Pad, Pocket, Revolution, Groove, Loft, Pipe or Helix fails on
a sketch whose profile is open (its profile or one of its loft or sweep
sections), a small card at the top of the 3D view says where: the open ends
(red dots in the view), the gaps between them, the coincidences the sketch is
missing, edges drawn twice and lines that overlap. Check Profile
(BruceWorx_SketchRepair) shows the same card for a selected sketch or feature,
or the sketch being edited, before anything fails. It offers:

* Close gaps: a coincident constraint for two ends closer than the snap
  distance (Preferences/BruceWorx/SketchRepairSnap, 0.1 mm), a short line plus
  two coincidences for ends further apart but within the largest gap
  (SketchRepairMaxGap, 1 mm). Ends further apart are only reported. One undo
  step ("Close sketch gaps"); while the feature's own dialog is open the fix
  joins that dialog's transaction, so Cancel takes it back too. The same step
  deletes an edge drawn twice (the copy with fewer constraints goes) and
  Sketcher's degenerated (zero-length) edges, found as Sketcher > Validate
  sketch finds them: a parameter range under Precision::Confusion().
  Overlapping lines that are not exact copies are only reported: which part
  to keep is the user's call.
* Open sketch: edits the sketch.

Nothing changes without a click. The card goes when the feature recomputes
without the problem, on Escape or on its close button. A dismissed problem
stays quiet until the sketch's geometry changes.

The analysis is pure Python (endpoint graph, gap pairing, fix plan) and unit
tested in TestBruceWorxSketchRepair. Preferences/BruceWorx/SketchRepair
(default on).
"""

from __future__ import annotations

import math
from collections import namedtuple

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF_NAME = "SketchRepair"
SNAP_PREF = "SketchRepairSnap"
MAX_PREF = "SketchRepairMaxGap"
SNAP_DEFAULT = 0.1  # mm: closer ends get a coincident constraint
MAX_DEFAULT = 1.0  # mm: ends up to this far apart get a short line
EPS = 1e-6  # mm: ends this close already touch (the wire builder joins them)

FEATURE_TYPES = ("PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Revolution", "PartDesign::Groove",
                 "PartDesign::AdditiveLoft", "PartDesign::SubtractiveLoft",
                 "PartDesign::AdditivePipe", "PartDesign::SubtractivePipe",
                 "PartDesign::AdditiveHelix", "PartDesign::SubtractiveHelix")
COMMAND = "BruceWorx_SketchRepair"
ICON = "Heuretes_SketchRepair.svg"
OBJECT_NAME = "BruceWorxRepairCard"
TRANSACTION = "Close sketch gaps"
TOP_MARGIN = 12
MAX_LINES = 4  # fixes listed on the card; the rest are counted

# Constraints that join two end points (positions 1 = start, 2 = end).
JOINING = ("Coincident", "Tangent", "Perpendicular")

End = namedtuple("End", "geo pos xy")  # one end of an edge, sketch coordinates
Fix = namedtuple("Fix", "kind a b distance")  # kind "coincident" | "line"; a, b are End
# duplicates: (keep geo, drop geo); overlaps: Overlap; degenerate: geo indices (Sketcher's degenerated geometry)
Report = namedtuple("Report", "open_ends fixes unresolved branches duplicates overlaps degenerate",
                    defaults=((), (), ()))
Curve = namedtuple("Curve", "geo kind points")  # points: open curves (start, end, mid); circles (centre, (r, 0))
Overlap = namedtuple("Overlap", "a b xy length")  # two collinear lines sharing ``length`` mm around ``xy``


# ----- pure logic (unit tested) -----------------------------------------------------------


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def ends_of(edges):
    """[End] for edges given as (geo index, start xy, end xy)."""
    out = []
    for geo, start, end in edges:
        out.append(End(geo, 1, (float(start[0]), float(start[1]))))
        out.append(End(geo, 2, (float(end[0]), float(end[1]))))
    return out


def near_pairs(ends, reach):
    """(distance, i, j) for ends no further apart than ``reach``, nearest first.

    A grid of ``reach``-sized cells keeps this linear for large sketches.
    """
    cell = max(reach, EPS)
    grid = {}
    for i, e in enumerate(ends):
        grid.setdefault((math.floor(e.xy[0] / cell), math.floor(e.xy[1] / cell)), []).append(i)
    pairs = []
    for (cx, cy), members in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    for i in members:
                        if i < j:
                            d = _dist(ends[i].xy, ends[j].xy)
                            if d <= reach:
                                pairs.append((d, i, j))
    pairs.sort()
    return pairs


def analyse(edges, links=(), snap=SNAP_DEFAULT, max_gap=MAX_DEFAULT):
    """Find the open ends of a sketch profile and plan how to close them.

    ``edges``: (geo, start xy, end xy) for each open, non-construction edge.
    ``links``: ((geo, pos), (geo, pos)) pairs of ends a constraint already joins.
    Returns a Report:

    * open_ends: ends nothing else touches, as End;
    * fixes: Fix("coincident", a, b, d) for ends within ``snap`` that no
      constraint joins (also ends that touch already), then Fix("line", ...)
      for open ends up to ``max_gap`` apart, nearest first;
    * unresolved: open ends no fix closes;
    * branches: points where three or more ends meet (reported, not fixed).
    """
    ends = ends_of(edges)
    index = {(e.geo, e.pos): i for i, e in enumerate(ends)}
    constrained = _Union()  # joined by constraints
    touching = _Union()  # joined by constraints or by touching
    for a, b in links:
        ia, ib = index.get(tuple(a)), index.get(tuple(b))
        if ia is not None and ib is not None:
            constrained.union(ia, ib)
            touching.union(ia, ib)
    reach = max(snap, max_gap)
    pairs = near_pairs(ends, reach)
    for d, i, j in pairs:
        if d <= EPS:
            touching.union(i, j)

    def groups(union):
        out = {}
        for i in range(len(ends)):
            out.setdefault(union.find(i), []).append(i)
        return out

    node_of = {i: touching.find(i) for i in range(len(ends))}
    nodes = groups(touching)
    open_set = {i for members in nodes.values() if len(members) == 1 for i in members}
    branches = [ends[m[0]].xy for m in nodes.values() if len(m) > 2]

    fixes = []
    closed = set()  # open ends a fix already uses
    # Coincidences: ends that touch without a constraint, and open ends within the snap.
    for d, i, j in pairs:
        if d > snap or ends[i].geo == ends[j].geo:
            continue
        if constrained.find(i) == constrained.find(j):
            continue
        if d > EPS:
            # Closing a gap: only between two open ends, each used once.
            if i not in open_set or j not in open_set or i in closed or j in closed:
                continue
            closed.update((i, j))
        elif node_of[i] != node_of[j]:
            continue
        constrained.union(i, j)
        fixes.append(Fix("coincident", ends[i], ends[j], d))
    # Short lines between the remaining open ends.
    for d, i, j in pairs:
        if d <= EPS or d > max_gap:
            continue
        if i not in open_set or j not in open_set or i in closed or j in closed:
            continue
        if d <= snap and ends[i].geo != ends[j].geo:
            continue  # handled above (or deliberately left)
        closed.update((i, j))
        fixes.append(Fix("line", ends[i], ends[j], d))
    open_ends = [ends[i] for i in sorted(open_set)]
    unresolved = [ends[i] for i in sorted(open_set - closed)]
    return Report(open_ends, fixes, unresolved, branches)


def _same(p, q, eps=EPS):
    return _dist(p, q) <= eps


def duplicates(curves, weight=None, eps=EPS):
    """(keep, drop) geo pairs for curves drawn twice: same kind, same ends (either way round) and the
    same middle point; circles with the same centre and radius. Of each set the one with the
    highest ``weight`` (constraints on it) stays, then the lowest index."""
    weight = weight or {}
    out, groups = [], []
    for curve in curves:
        for group in groups:
            first = group[0]
            if first.kind != curve.kind or len(first.points) != len(curve.points):
                continue
            a, b = first.points, curve.points
            if len(a) == 3:
                same = _same(a[2], b[2], eps) and (
                    (_same(a[0], b[0], eps) and _same(a[1], b[1], eps))
                    or (_same(a[0], b[1], eps) and _same(a[1], b[0], eps)))
            else:
                same = all(_same(p, q, eps) for p, q in zip(a, b))
            if same:
                group.append(curve)
                break
        else:
            groups.append([curve])
    for group in groups:
        if len(group) < 2:
            continue
        keep = max(group, key=lambda c: (weight.get(c.geo, 0), -c.geo))
        out += [(keep.geo, c.geo) for c in group if c.geo != keep.geo]
    return sorted(out, key=lambda pair: pair[1])


def overlaps(lines, eps=EPS):
    """Overlap for collinear lines (geo, start, end) that share more than ``eps`` of their length
    and are not exact copies (duplicates() finds those)."""
    out = []
    for i in range(len(lines)):
        ga, a0, a1 = lines[i]
        length = _dist(a0, a1)
        if length <= eps:
            continue
        ux, uy = (a1[0] - a0[0]) / length, (a1[1] - a0[1]) / length
        for j in range(i + 1, len(lines)):
            gb, b0, b1 = lines[j]
            if (_same(a0, b0, eps) and _same(a1, b1, eps)) or (_same(a0, b1, eps) and _same(a1, b0, eps)):
                continue
            off = [abs((p[0] - a0[0]) * uy - (p[1] - a0[1]) * ux) for p in (b0, b1)]
            if max(off) > eps:
                continue  # not on a's line
            t = sorted(((p[0] - a0[0]) * ux + (p[1] - a0[1]) * uy) for p in (b0, b1))
            lo, hi = max(0.0, t[0]), min(length, t[1])
            if hi - lo > eps:
                mid = (lo + hi) / 2
                out.append(Overlap(ga, gb, (a0[0] + ux * mid, a0[1] + uy * mid), hi - lo))
    return out


def has_problem(report):
    """True when the profile has open ends, an edge drawn twice or overlapping lines (missing
    coincidences and degenerated edges alone do not count: the wire still closes)."""
    return bool(report.open_ends or report.duplicates or report.overlaps)


def fixable(report):
    """True when Close Gaps has something to do."""
    return bool(report.fixes or report.duplicates or report.degenerate)


def mm(value):
    """0.05 -> "0.05 mm", 1 -> "1 mm"."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return (text if text not in ("", "-0") else "0") + " mm"


def _edge(geo, names=None):
    return (names or {}).get(geo) or f"Edge {geo + 1}"


def end_name(end, names=None):
    """"Line 4 end": the edge's name (1-based, as Sketcher shows it) and which end."""
    edge = (names or {}).get(end.geo) or f"Edge {end.geo + 1}"
    return f"{edge} {'start' if end.pos == 1 else 'end'}"


def describe(report, names=None):
    """(headline, [fix lines], footnote) for the card."""
    count = len(report.open_ends)
    gaps = [f for f in report.fixes if f.kind == "line" or f.distance > EPS]
    if count:
        head = f"{count} open end" + ("s" if count != 1 else "")
        if gaps:
            widest = max(f.distance for f in gaps)
            head += f", gap up to {mm(widest)}"
    elif report.duplicates or report.overlaps:
        head = "Edges on top of each other"
    else:
        head = "Profile is closed"
    lines = []
    for fix in report.fixes[:MAX_LINES]:
        what = "coincident" if fix.kind == "coincident" else "short line"
        lines.append(f"{end_name(fix.a, names)} ↔ {end_name(fix.b, names)}: {mm(fix.distance)}, {what}")
    more = len(report.fixes) - MAX_LINES
    if more > 0:
        lines.append(f"… and {more} more")
    for keep, drop in report.duplicates[:MAX_LINES]:
        lines.append(f"{_edge(drop, names)} is a copy of {_edge(keep, names)}: delete it")
    if len(report.duplicates) > MAX_LINES:
        lines.append(f"… and {len(report.duplicates) - MAX_LINES} more copies")
    if report.degenerate:
        n = len(report.degenerate)
        lines.append(f"{n} zero-length edge{'s' if n != 1 else ''}: delete")
    notes = []
    for ov in report.overlaps[:MAX_LINES]:
        notes.append(f"{_edge(ov.a, names)} and {_edge(ov.b, names)} overlap by {mm(ov.length)}")
    if report.unresolved:
        n = len(report.unresolved)
        notes.append(f"{n} end{'s' if n != 1 else ''} too far from any other to close")
    if report.branches:
        n = len(report.branches)
        notes.append(f"{n} point{'s' if n != 1 else ''} where three or more edges meet")
    return head, lines, "; ".join(notes)


def signature(edges, links, extra=()):
    """A key that changes when the sketch's geometry or joins change (for dismissals)."""
    rounded = tuple((g, round(s[0], 6), round(s[1], 6), round(e[0], 6), round(e[1], 6)) for g, s, e in edges)
    return hash((rounded, tuple(sorted(tuple(map(tuple, link)) for link in links)), tuple(extra)))


# ----- FreeCAD side ------------------------------------------------------------------------


def enabled():
    import FreeCAD

    return bool(FreeCAD.ParamGet(PREFS_PATH).GetBool(PREF_NAME, True))


def tolerances():
    import FreeCAD

    prefs = FreeCAD.ParamGet(PREFS_PATH)
    snap = prefs.GetFloat(SNAP_PREF, SNAP_DEFAULT) or SNAP_DEFAULT
    max_gap = prefs.GetFloat(MAX_PREF, MAX_DEFAULT) or MAX_DEFAULT
    return snap, max(snap, max_gap)


def sketch_edges(sketch):
    """(edges, links, names) of a Sketcher::SketchObject, for analyse()."""
    edges, names = [], {}
    for i, geo in enumerate(sketch.Geometry):
        if not hasattr(geo, "StartPoint"):
            continue  # points, full circles and ellipses
        if getattr(geo, "isPeriodic", None) and geo.isPeriodic():
            continue
        try:
            if sketch.getConstruction(i):
                continue
        except Exception:
            pass
        start, end = geo.StartPoint, geo.EndPoint
        edges.append((i, (start.x, start.y), (end.x, end.y)))
        kind = type(geo).__name__.replace("Segment", "").replace("ArcOf", "Arc of ").replace("BSplineCurve", "B-spline")
        names[i] = f"{kind} {i + 1}"
    links = []
    for c in sketch.Constraints:
        if c.Type not in JOINING:
            continue
        if c.FirstPos in (1, 2) and c.SecondPos in (1, 2) and c.First >= 0 and c.Second >= 0:
            links.append(((c.First, c.FirstPos), (c.Second, c.SecondPos)))
    return edges, links, names


def degenerate_geos(sketch):
    """Geo indices Sketcher counts as degenerated (detectDegeneratedGeometries): a curve whose
    parameter range is under the tolerance Sketcher's Validate sketch uses, Precision::Confusion()."""
    tol = _confusion()
    out = []
    for i, geo in enumerate(sketch.Geometry):
        first, last = getattr(geo, "FirstParameter", None), getattr(geo, "LastParameter", None)
        if first is None or last is None:
            continue
        if abs(last - first) < tol:
            out.append(i)
    return out


def _confusion():
    import Part

    return Part.Precision.confusion()


def _curve(i, geo):
    """Curve for duplicates(), or None (points, ellipses and closed B-splines are not compared)."""
    kind = type(geo).__name__
    if hasattr(geo, "StartPoint") and not (getattr(geo, "isPeriodic", None) and geo.isPeriodic()):
        a, b = geo.StartPoint, geo.EndPoint
        mid = geo.value((geo.FirstParameter + geo.LastParameter) / 2)
        return Curve(i, kind, ((a.x, a.y), (b.x, b.y), (mid.x, mid.y)))
    if kind == "Circle":
        return Curve(i, kind, ((geo.Center.x, geo.Center.y), (geo.Radius, 0.0)))
    return None


def inspect(sketch, snap=None, max_gap=None):
    """(report, names, signature) of a sketch: open ends and gap fixes, edges drawn twice, overlapping
    lines and degenerated edges. Copies to drop and degenerated edges are left out of the gap analysis,
    so its fixes still hold once they are deleted."""
    if snap is None:
        snap, max_gap = tolerances()
    edges, links, names = sketch_edges(sketch)
    degenerate = degenerate_geos(sketch)
    weight = {}
    for c in sketch.Constraints:
        for geo in (c.First, c.Second, getattr(c, "Third", -2000)):
            if geo is not None and geo >= 0:
                weight[geo] = weight.get(geo, 0) + 1
    construction = set()
    for i in range(len(sketch.Geometry)):
        try:
            if sketch.getConstruction(i):
                construction.add(i)
        except Exception:
            pass
    curves = [c for c in (_curve(i, g) for i, g in enumerate(sketch.Geometry)
                          if i not in construction and i not in degenerate) if c is not None]
    dups = duplicates(curves, weight)
    gone = set(degenerate) | {drop for _keep, drop in dups}
    kept = [e for e in edges if e[0] not in gone]
    report = analyse(kept, links, snap, max_gap)
    lines = [(i, (g.StartPoint.x, g.StartPoint.y), (g.EndPoint.x, g.EndPoint.y))
             for i, g in enumerate(sketch.Geometry)
             if type(g).__name__ == "LineSegment" and i not in construction and i not in gone]
    for i, g in enumerate(sketch.Geometry):
        if i not in names and type(g).__name__ == "Circle":
            names[i] = f"Circle {i + 1}"
    report = report._replace(duplicates=tuple(dups), overlaps=tuple(overlaps(lines)), degenerate=tuple(degenerate))
    return report, names, signature(edges, links, (tuple(dups), tuple(degenerate)))


def profile_sketches(feature):
    """The sketches a feature's profile is made of: its Profile, then its loft or sweep Sections."""
    out = []

    def add(obj):
        if obj is not None and getattr(obj, "TypeId", "") == "Sketcher::SketchObject" and obj not in out:
            out.append(obj)

    profile = getattr(feature, "Profile", None)
    if isinstance(profile, (tuple, list)):
        profile = profile[0] if profile else None
    add(profile)
    for section in list(getattr(feature, "Sections", None) or []):
        add(section[0] if isinstance(section, (tuple, list)) else section)
    return out


def profile_sketch(feature):
    sketches = profile_sketches(feature)
    return sketches[0] if sketches else None


def in_error(obj):
    state = list(getattr(obj, "State", []) or [])
    if "Invalid" in state or "Error" in state:
        return True
    try:
        return not obj.isValid()
    except Exception:
        return False


def problem_sketches(feature, snap=None, max_gap=None):
    """[(sketch, report, names, signature)] of the feature's profile sketches that have a problem."""
    if snap is None:
        snap, max_gap = tolerances()
    out = []
    for sketch in profile_sketches(feature):
        report, names, sig = inspect(sketch, snap, max_gap)
        if has_problem(report):
            out.append((sketch, report, names, sig))
    return out


def check_feature(feature, snap=None, max_gap=None):
    """(sketch, report, names, signature) when ``feature`` fails on an open profile, else None.
    With several bad sketches (a loft's sections) the first is given."""
    if getattr(feature, "TypeId", "") not in FEATURE_TYPES or not in_error(feature):
        return None
    found = problem_sketches(feature, snap, max_gap)
    return found[0] if found else None


def check_sketch(sketch, snap=None, max_gap=None):
    """(sketch, report, names, signature) when ``sketch`` has a problem, else None (Check Profile)."""
    report, names, sig = inspect(sketch, snap, max_gap)
    return (sketch, report, names, sig) if has_problem(report) else None


def apply_fixes(sketch, fixes):
    """Add the constraints and lines of ``fixes`` to ``sketch``. Returns the solver's result (0 = solved)."""
    import FreeCAD
    import Part
    import Sketcher

    for fix in fixes:
        a, b = fix.a, fix.b
        if fix.kind == "coincident":
            sketch.addConstraint(Sketcher.Constraint("Coincident", a.geo, a.pos, b.geo, b.pos))
        else:
            line = Part.LineSegment(FreeCAD.Vector(a.xy[0], a.xy[1], 0), FreeCAD.Vector(b.xy[0], b.xy[1], 0))
            new = sketch.addGeometry(line, False)
            sketch.addConstraint(
                [
                    Sketcher.Constraint("Coincident", new, 1, a.geo, a.pos),
                    Sketcher.Constraint("Coincident", new, 2, b.geo, b.pos),
                ]
            )
    return sketch.solve()


def repair(sketch, report):
    """Apply a report to a sketch: the gap fixes (their indices are from before any deletion), then delete
    the copies and Sketcher's degenerated edges. Returns the solver's result (0 = solved)."""
    result = apply_fixes(sketch, report.fixes) if report.fixes else 0
    drops = sorted({drop for _keep, drop in report.duplicates})
    if drops:
        sketch.delGeometries(drops)
    if report.degenerate:
        sketch.removeDegeneratedGeometries(_confusion())
    if drops or report.degenerate:
        result = sketch.solve()
    return result


def _repair_all(doc, found):
    """Repair each (sketch, report, ...) in one undo step. Returns (ok, message)."""
    todo = [(sketch, report) for sketch, report, *_rest in found if fixable(report)]
    if not todo:
        return False, "No gap is small enough to close"
    own = not doc.HasPendingTransaction
    if own:
        doc.openTransaction(TRANSACTION)
    try:
        results = [repair(sketch, report) for sketch, report in todo]
    except Exception as exc:
        if own:
            doc.abortTransaction()
        return False, f"Could not close the gaps: {exc}"
    if any(r != 0 for r in results) and own:
        doc.abortTransaction()
        doc.recompute()
        return False, "The sketch's constraints do not allow it; open the sketch to fix it"
    doc.recompute()
    if own:
        doc.commitTransaction()
    n = sum(len(r.fixes) for _s, r in todo)
    copies = sum(len(r.duplicates) + len(r.degenerate) for _s, r in todo)
    parts = []
    if n:
        parts.append(f"closed {n} gap{'s' if n != 1 else ''}")
    if copies:
        parts.append(f"deleted {copies} extra edge{'s' if copies != 1 else ''}")
    return True, (" and ".join(parts) or "repaired").capitalize()


def close_gaps(feature):
    """Fix every bad sketch of ``feature``'s profile in one undo step. Returns (ok, message)."""
    if getattr(feature, "TypeId", "") not in FEATURE_TYPES or not in_error(feature):
        return True, "Nothing to close"
    found = problem_sketches(feature)
    if not found:
        return True, "Nothing to close"
    return _repair_all(feature.Document, found)


def close_sketch(sketch):
    """Fix ``sketch`` (Check Profile) in one undo step. Returns (ok, message)."""
    found = check_sketch(sketch)
    if found is None:
        return True, "Nothing to close"
    return _repair_all(sketch.Document, [found])


# ----- the card ---------------------------------------------------------------------------

_card = None
_observer = None
_prefs_observer = None
_group = None
_timer = None
_pending = set()  # (doc name, feature name) to check after a recompute
_dismissed = {}  # (doc, feature) -> signature the user dismissed


def _alive(obj):
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:
        return obj is not None


def _view_widget(doc_name):
    """The active 3D view when it shows ``doc_name``, else None."""
    import FreeCADGui
    from PySide import QtWidgets

    gui_doc = FreeCADGui.ActiveDocument
    if gui_doc is None or gui_doc.Document.Name != doc_name:
        return None
    mw = FreeCADGui.getMainWindow()
    mdi = mw.findChild(QtWidgets.QMdiArea) if mw else None
    sub = mdi.activeSubWindow() if mdi else None
    widget = sub.widget() if sub else None
    if widget is None or widget.metaObject().className() != "Gui::View3DInventor":
        return None
    return widget


def _make_card_class():
    from PySide import QtCore, QtGui, QtWidgets

    class Card(QtWidgets.QFrame):
        """Problem card at the top centre of a 3D view, with red dots on the open ends."""

        def __init__(self, view):
            super().__init__(view)
            self.setObjectName(OBJECT_NAME)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            self.key = None
            self.marker_root = None
            self.marker_view = None
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(12, 10, 10, 10)
            layout.setSpacing(4)
            top = QtWidgets.QHBoxLayout()
            self.title = QtWidgets.QLabel(self)
            self.title.setObjectName(OBJECT_NAME + "Title")
            top.addWidget(self.title, 1)
            close = QtWidgets.QToolButton(self)
            close.setObjectName(OBJECT_NAME + "Close")
            close.setText("×")
            close.setAutoRaise(True)
            close.setToolTip("Dismiss (Esc)")
            close.clicked.connect(self.dismiss)
            top.addWidget(close)
            layout.addLayout(top)
            self.body = QtWidgets.QLabel(self)
            self.body.setObjectName(OBJECT_NAME + "Body")
            self.body.setTextFormat(QtCore.Qt.PlainText)
            layout.addWidget(self.body)
            self.note = QtWidgets.QLabel(self)
            self.note.setObjectName(OBJECT_NAME + "Note")
            self.note.setWordWrap(True)
            layout.addWidget(self.note)
            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch(1)
            self.open_button = QtWidgets.QPushButton("Open Sketch", self)
            self.open_button.setObjectName(OBJECT_NAME + "Open")
            self.open_button.clicked.connect(self.open_sketch)
            buttons.addWidget(self.open_button)
            self.fix_button = QtWidgets.QPushButton("Close Gaps", self)
            self.fix_button.setObjectName(OBJECT_NAME + "Fix")
            self.fix_button.setDefault(True)
            self.fix_button.clicked.connect(self.close_gaps)
            buttons.addWidget(self.fix_button)
            layout.addLayout(buttons)
            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 2)
            shadow.setColor(QtGui.QColor(0, 0, 0, 60))
            self.setGraphicsEffect(shadow)
            self.esc = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), view)
            self.esc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            self.esc.activated.connect(self.dismiss)
            view.installEventFilter(self)

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Resize and obj is self.parentWidget():
                self.place()
            return False

        def show_problem(self, feature, sketch, report, names, sig):
            """``feature`` None: Check Profile on the sketch itself (keyed by the sketch)."""
            import FreeCADGui

            owner = feature if feature is not None else sketch
            self.key = (owner.Document.Name, owner.Name)
            self.sketch_name = sketch.Name
            self.sig = sig
            self.feature_label = owner.Label
            head, lines, note = describe(report, names)
            if feature is None:
                self.title.setText(f"{sketch.Label}: the profile will not close")
            elif report.open_ends:
                self.title.setText(f"{feature.Label} fails: {sketch.Label} is open")
            else:
                self.title.setText(f"{feature.Label} fails: {sketch.Label} has edges on top of each other")
            self.body.setText(head + ("\n" + "\n".join("• " + line for line in lines) if lines else ""))
            self.note.setText(note)
            self.note.setVisible(bool(note))
            self.fix_button.setEnabled(fixable(report))
            self.fix_button.setToolTip(
                "Adds the constraints and lines and deletes the copies listed, as one undo step"
                if fixable(report)
                else "No two ends are close enough; open the sketch to fix it"
            )
            busy = FreeCADGui.Control.activeDialog()
            self.open_button.setEnabled(not busy)
            self.open_button.setToolTip(
                f"Finish or cancel the {owner.Label} dialog first" if busy else f"Edit {sketch.Label}"
            )
            self.adjustSize()
            self.place()
            self.show()
            self.raise_()
            self.mark(sketch, report)

        def place(self):
            view = self.parentWidget()
            if view is None:
                return
            self.adjustSize()
            width = min(self.sizeHint().width(), max(240, view.width() - 40))
            self.resize(width, self.sizeHint().height())
            self.move(max(0, (view.width() - self.width()) // 2), TOP_MARGIN)

        def mark(self, sketch, report):
            """Red dots on the open ends, in the 3D view (also while the sketch is edited)."""
            self.unmark()
            try:
                import FreeCAD
                import FreeCADGui
                from pivy import coin

                placement = sketch.getGlobalPlacement()
                spots = [e.xy for e in report.open_ends] + [ov.xy for ov in report.overlaps]
                for _keep, drop in report.duplicates:
                    geo = sketch.Geometry[drop]
                    mid = geo.value((geo.FirstParameter + geo.LastParameter) / 2) if hasattr(geo, "FirstParameter") \
                        else getattr(geo, "Center", None)
                    if mid is not None:
                        spots.append((mid.x, mid.y))
                points = [placement.multVec(FreeCAD.Vector(x, y, 0)) for x, y in spots]
                if not points:
                    return
                root = coin.SoSeparator()
                pick = coin.SoPickStyle()
                pick.style = coin.SoPickStyle.UNPICKABLE
                root.addChild(pick)
                color = coin.SoBaseColor()
                color.rgb = (0.85, 0.23, 0.25)
                root.addChild(color)
                coords = coin.SoCoordinate3()
                coords.point.setValues(0, len(points), [(p.x, p.y, p.z) for p in points])
                root.addChild(coords)
                markers = coin.SoMarkerSet()
                markers.markerIndex = FreeCADGui.getMarkerIndex("CIRCLE_FILLED", 11)
                root.addChild(markers)
                view = FreeCADGui.getDocument(self.key[0]).ActiveView
                view.getSceneGraph().addChild(root)
                self.marker_root, self.marker_view = root, view
            except Exception:
                self.marker_root = None

        def unmark(self):
            if self.marker_root is not None:
                try:
                    self.marker_view.getSceneGraph().removeChild(self.marker_root)
                except Exception:
                    pass
            self.marker_root = self.marker_view = None

        def feature(self):
            import FreeCAD

            if self.key is None or self.key[0] not in FreeCAD.listDocuments():
                return None
            return FreeCAD.getDocument(self.key[0]).getObject(self.key[1])

        def sketch(self):
            obj = self.feature()
            if obj is None:
                return None
            return obj.Document.getObject(getattr(self, "sketch_name", "")) or profile_sketch(obj)

        def close_gaps(self):
            feature = self.feature()
            if feature is None:
                return self.hide_card()
            if feature.TypeId == "Sketcher::SketchObject":
                ok, message = close_sketch(feature)
            else:
                ok, message = close_gaps(feature)
            _status(message)
            if ok:
                self.hide_card()
            else:
                self.note.setText(message)
                self.note.show()
                self.place()

        def open_sketch(self):
            import FreeCADGui

            sketch = self.sketch()
            if sketch is None:
                return self.hide_card()
            try:
                FreeCADGui.getDocument(sketch.Document.Name).setEdit(sketch.Name)
            except Exception as exc:
                _status(f"Could not open {sketch.Label}: {exc}")
                return
            # The dots stay to show where the ends are; the card makes room.
            self.hide()

        def dismiss(self):
            if self.key is not None:
                _dismissed[self.key] = self.sig
            self.hide_card()

        def hide_card(self):
            self.unmark()
            self.key = None
            self.hide()

    return Card


_card_class = None


def _status(text):
    try:
        import FreeCADGui

        FreeCADGui.getMainWindow().statusBar().showMessage(text, 6000)
    except Exception:
        pass


def card():
    """The live card widget, or None (for tests and scripts)."""
    return _card if _alive(_card) else None


def _check():
    """After a recompute: show, update or hide the card."""
    import FreeCAD

    keys = list(_pending)
    _pending.clear()
    if not enabled():
        if _alive(_card):
            _card.hide_card()
        return
    shown = None
    for doc_name, name in keys:
        if doc_name not in FreeCAD.listDocuments():
            continue
        feature = FreeCAD.getDocument(doc_name).getObject(name)
        if feature is None:
            continue
        found = check_feature(feature)
        key = (doc_name, name)
        if found is None:
            _dismissed.pop(key, None)
            if _alive(_card) and _card.key == key:
                _card.hide_card()
            continue
        if _dismissed.get(key) == found[3]:
            continue
        shown = (feature, found)
    if shown is None:
        return
    _show(*shown)


def _show(feature, found):
    """Put the card on the active 3D view of the document. Returns the card, or None (no 3D view)."""
    global _card, _card_class
    sketch, report, names, sig = found
    view = _view_widget(sketch.Document.Name)
    if view is None:
        return None
    if _card_class is None:
        _card_class = _make_card_class()
    if not _alive(_card) or _card.parentWidget() is not view:
        if _alive(_card):
            _card.hide_card()
            _card.deleteLater()
        _card = _card_class(view)
    _card.show_problem(feature, sketch, report, names, sig)
    return _card


def chosen_sketches():
    """The sketches Check Profile looks at: selected sketches, the profile sketches of selected features,
    else the sketch being edited."""
    import FreeCADGui

    out = []
    for obj in FreeCADGui.Selection.getSelection():
        for sketch in ([obj] if obj.TypeId == "Sketcher::SketchObject" else profile_sketches(obj)):
            if sketch not in out:
                out.append(sketch)
    if not out:
        gui_doc = FreeCADGui.ActiveDocument
        vp = gui_doc.getInEdit() if gui_doc is not None else None
        obj = getattr(vp, "Object", None)
        if obj is not None and obj.TypeId == "Sketcher::SketchObject":
            out.append(obj)
    return out


def check_profile(sketches=None):
    """Check Profile: the card for the first chosen sketch with a problem. Returns that
    (sketch, report, names, signature), or None when every profile closes (said in the status bar)."""
    sketches = chosen_sketches() if sketches is None else list(sketches)
    if not sketches:
        _status("Select a sketch or a feature made from one, or edit a sketch")
        return None
    for sketch in sketches:
        found = check_sketch(sketch)
        if found is not None:
            _show(None, found)
            return found
    names = ", ".join(s.Label for s in sketches)
    _status(f"{names}: the profile closes (no open ends, copies or overlaps)")
    return None


class _CheckCommand:
    def GetResources(self):
        import os

        return {"Pixmap": os.path.join(os.path.dirname(os.path.abspath(__file__)), ICON),
                "MenuText": "Check Profile",
                "ToolTip": "Find the open ends, gaps, edges drawn twice and overlapping lines of the selected "
                           "sketch (or the one being edited) and offer to close them. Nothing changes without a click."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        check_profile()


def _queue(obj):
    if getattr(obj, "TypeId", "") in FEATURE_TYPES:
        _pending.add((obj.Document.Name, obj.Name))
        if _timer is not None:
            _timer.start()


class _DocObserver:
    # A single-feature recompute (a task dialog's preview) reports the feature even
    # when it fails; a document recompute reports only the ones that succeed.
    def slotRecomputedObject(self, obj):
        _queue(obj)

    def slotRecomputedDocument(self, doc):
        for obj in doc.Objects:
            _queue(obj)

    def slotDeletedObject(self, obj):
        if _alive(_card) and _card.key == (obj.Document.Name, obj.Name):
            _card.hide_card()

    def slotDeletedDocument(self, doc):
        for key in [k for k in _dismissed if k[0] == doc.Name]:
            del _dismissed[key]  # a file opened again under the same name starts fresh
        if _alive(_card) and _card.key and _card.key[0] == doc.Name:
            _card.hide_card()


class _PrefObserver:
    def onChange(self, _group, name):
        if name == PREF_NAME and not enabled() and _alive(_card):
            _card.hide_card()


def _shutdown():
    global _timer
    import FreeCAD

    if _timer is not None:
        _timer.stop()
        _timer = None
    if _observer is not None:
        try:
            FreeCAD.removeDocumentObserver(_observer)
        except Exception:
            pass


def install():
    """Watch PartDesign recomputes for profiles that fail on open wires."""
    global _observer, _prefs_observer, _group, _timer
    if _observer is not None:
        return
    import FreeCAD
    from PySide import QtCore, QtWidgets

    _timer = QtCore.QTimer()
    _timer.setSingleShot(True)
    _timer.setInterval(0)
    _timer.timeout.connect(_check)
    QtWidgets.QApplication.instance().aboutToQuit.connect(_shutdown)
    _observer = _DocObserver()
    FreeCAD.addDocumentObserver(_observer)
    _group = FreeCAD.ParamGet(PREFS_PATH)  # keep the handle: a collected wrapper detaches
    _prefs_observer = _PrefObserver()
    _group.Attach(_prefs_observer)
    _add_command()


def _add_command():
    import FreeCADGui

    if COMMAND not in FreeCADGui.listCommands():
        FreeCADGui.addCommand(COMMAND, _CheckCommand())
