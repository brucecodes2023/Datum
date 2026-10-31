# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drag checks for Assembly drags (BW-ROU-08, 30 Oct 2026).

``AssemblyObject::preDrag`` / ``validateNewPlacements`` / ``postDrag`` call ``pre_drag``, ``check``
and ``post_drag`` here (marked ``[BW]`` in the C++). ``check`` gets the placements the solver
proposes for every part it moves and answers ``True`` (go ahead) or ``False``: the step is then cut
back by the bisection that already stops a joint at its limit. A Python error never blocks a drag.

The one check shipped is the flexible wires' (``bruceworx_rope``): the wires on the moved parts
are solved at the proposed pin positions, warm-started from the last accepted shape, and a wire
that would be pulled taut or bent past its minimum radius refuses the step. While dragging each
wire is drawn as a light tube (a Coin line set in the 3D view, the wire's colour and width) in place
of its solid, which is built again on release.
"""

import FreeCAD as App

_CHECKS = []


def register(check):
    """Add a drag check: an object with ``pre_drag(assembly, parts)``, ``check(assembly, proposals)`` and ``post_drag(assembly)``."""
    if check not in _CHECKS:
        _CHECKS.append(check)


def unregister(check):
    if check in _CHECKS:
        _CHECKS.remove(check)


def _ensure_rope_check():
    if not any(isinstance(c, RopeDragCheck) for c in _CHECKS):
        register(RopeDragCheck())


def pre_drag(assembly, parts):
    _ensure_rope_check()
    for check in list(_CHECKS):
        try:
            check.pre_drag(assembly, parts)
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx drag check: {exc}\n")
    return True


def check(assembly, proposals):
    ok = True
    for c in list(_CHECKS):
        try:
            if not c.check(assembly, proposals):
                ok = False
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx drag check: {exc}\n")
    return ok


def post_drag(assembly, parts=None):
    for c in list(_CHECKS):
        try:
            c.post_drag(assembly)
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx drag check: {exc}\n")
    return True


# ---------------------------------------------------------------------------------------------


class RopeDragCheck:
    """Flexible wires during a drag."""

    def __init__(self):
        self.items = []
        self.base = {}
        self.assembly = None
        self.nodes = {}
        self.hidden = {}
        self.last_message = ""

    # ----- helpers -----

    def _children(self, assembly):
        return [c for c in getattr(assembly, "Group", []) if "Placement" in c.PropertiesList]

    def _owner_of(self, obj):
        """The assembly child ``obj`` is, or lies in."""
        names = {c.Name: c for c in self._children(self.assembly)}
        seen = set()
        cur = obj
        while cur is not None and cur.Name not in seen:
            if cur.Name in names:
                return names[cur.Name]
            seen.add(cur.Name)
            try:
                cur = cur.getParentGeoFeatureGroup()
            except Exception:
                cur = None
        return None

    def deltas(self, proposals):
        """{child name: App.Placement} in the document frame for each child that moved since the drag began."""
        out = {}
        assembly_plc = self.assembly.getGlobalPlacement()
        inverse = assembly_plc.inverse()
        current = {}
        for obj, plc in proposals:
            current[obj.Name] = plc
        for name, base in self.base.items():
            obj = self.assembly.Document.getObject(name)
            if obj is None:
                continue
            plc = current.get(name, obj.Placement)
            if plc.isSame(base, 1e-9):
                continue
            out[name] = assembly_plc.multiply(plc.multiply(base.inverse())).multiply(inverse)
        return out

    @staticmethod
    def _worry(res):
        """How far a result is from fine: the strain plus the bend ratio over the limit."""
        return float(res.strain) + max(float(res.ratio) - 1.0, 0.0)

    # ----- the three calls -----

    def pre_drag(self, assembly, parts):
        import bruceworx_rope as brope

        self.assembly = assembly
        self.items = []
        self.base = {c.Name: App.Placement(c.Placement) for c in self._children(assembly)}
        if not brope.available():
            return
        br = brope._br()
        doc = assembly.Document
        for obj in br.routed(doc, (br.WIRE, br.HOSE, br.BUNDLE)):
            if br._container_of(obj) is not assembly and brope.top_assembly(obj) is not assembly:
                continue
            try:
                item = brope.RopeItem(obj)
            except Exception as exc:
                App.Console.PrintWarning(f"BruceWorx rope: {obj.Label}: {exc}\n")
                continue
            for e in item.ends:
                e.owner = self._owner_of(e.owner) if e.owner is not None else None
            item.exclude = {e.owner.Name for e in item.ends if e.owner is not None}
            item.records = brope.box_records(obj, item.exclude)
            item.state = item.stored()
            item.base = self._worry(item.solve(state=item.state, max_steps=6, tol=0.02))  # where it starts
            item.touch_names = set(item.exclude) | {t.Name for _i, _p, t in item.clips if t is not None}
            self.items.append(item)
        self._show()

    def check(self, assembly, proposals):
        if self.assembly is not assembly or not self.items:
            return True
        deltas = self.deltas(proposals)
        ok = True
        for item in self.items:
            moved = item.touch_names & set(deltas)
            moving_box = any(c[0].Name in deltas for c in (item.records or []))
            if not moved and not moving_box and item.state is not None:
                continue
            res = item.solve(drag_deltas=deltas, state=item.state, max_steps=10, tol=0.02)
            # refused when it is taut or bent past the limit and worse than where the drag began (a wire
            # that is already too tight is not frozen: only a step that tightens it further is refused)
            if res.taut and self._worry(res) > item.base + 1e-4:
                ok = False
                self.last_message = f"{item.obj.Label}: {res.message}"
                continue
            item.state = res.x.copy()
            self._draw(item, res.x)
        return ok

    def post_drag(self, assembly):
        if self.assembly is not assembly:
            return
        import bruceworx_rope as brope

        self._hide_tubes()
        items, self.items = self.items, []
        for item in items:
            if item.state is not None:
                try:
                    item.obj.RopePoints = [brope._vec(p) for p in item.state]
                except Exception:
                    pass
        doc = assembly.Document
        try:
            for item in items:
                item.obj.touch()
            doc.recompute()
        except Exception as exc:
            App.Console.PrintWarning(f"BruceWorx rope: {exc}\n")
        self.assembly = None

    # ----- the light tube -----

    def _view(self):
        if not App.GuiUp:
            return None
        try:
            import FreeCADGui as Gui

            return Gui.ActiveDocument.ActiveView.getSceneGraph()
        except Exception:
            return None

    def _show(self):
        """Hide the solids and put a line set for each wire in the 3D view."""
        scene = self._view()
        if scene is None:
            return
        try:
            from pivy import coin
        except Exception:
            return
        for item in self.items:
            obj = item.obj
            vobj = getattr(obj, "ViewObject", None)
            if vobj is None:
                continue
            colour = tuple(obj.Colour)[:3] if "Colour" in obj.PropertiesList else (0.5, 0.5, 0.5)
            sep = coin.SoSeparator()
            style = coin.SoDrawStyle()
            style.lineWidth = max(2.0, min(8.0, item.od * 1.6))
            light = coin.SoLightModel()
            light.model = coin.SoLightModel.BASE_COLOR
            rgb = coin.SoBaseColor()
            rgb.rgb = colour
            coords = coin.SoCoordinate3()
            line = coin.SoLineSet()
            for node in (style, light, rgb, coords, line):
                sep.addChild(node)
            scene.addChild(sep)
            self.nodes[obj.Name] = (sep, coords, line)
            self.hidden[obj.Name] = bool(vobj.Visibility)
            vobj.Visibility = False
            if item.state is not None:
                self._draw(item, item.state)

    def _draw(self, item, x):
        entry = self.nodes.get(item.obj.Name)
        if entry is None:
            return
        _sep, coords, line = entry
        coords.point.setValues(0, len(x), [tuple(map(float, p)) for p in x])
        line.numVertices.setValue(len(x))

    def _hide_tubes(self):
        scene = self._view()
        for name, (sep, _coords, _line) in list(self.nodes.items()):
            if scene is not None:
                try:
                    scene.removeChild(sep)
                except Exception:
                    pass
            obj = self.assembly.Document.getObject(name) if self.assembly is not None else None
            vobj = getattr(obj, "ViewObject", None) if obj is not None else None
            if vobj is not None and self.hidden.get(name):
                vobj.Visibility = True
        self.nodes.clear()
        self.hidden.clear()
