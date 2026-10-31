# SPDX-License-Identifier: LGPL-2.1-or-later
"""Parametric fittings with port frames (BW-ROU-05, 30 Oct 2026).

A fitting is an ``App::Part`` (the BOM line, P/N-ready: the part-properties schema, a generated
``Description`` and a generic ``FittingCode``) holding

* ``FittingBody``, a ``Part::FeaturePython`` whose ``Family`` / ``Style`` / ``Size`` / ``Thread``
  choose a row of the size tables (``bruceworx/fittings.py``: push-to-connect, hose barbs, NPT /
  BSPP threaded, compression) and whose shape is the table's simplified geometry, and
* one port frame per port (``bruceworx_routing_ports``: an LCS with ``BWFrame`` "Port", its
  ``PortType``, ``PortSize``, ``TubeOD`` and ``Engagement``), +Z out of the fitting along the
  tube. A route path picked on a port attaches to it and leaves straight, like on a connector pin.

Changing the family, style, size or thread rebuilds the shape, moves the ports and adds or
removes one (a tee has three). ``place_fitting`` puts a fitting's mount port (its thread, else
port 1) on a point facing a direction: on a picked face, hole edge or another port.
"""

import math
import os

import FreeCAD as App

FITTING = "BruceWorx::Fitting"
GROUP = "Fitting"
HERE = os.path.dirname(os.path.abspath(__file__))
COLOURS = {"Push-to-connect": (0.30, 0.31, 0.34), "Hose barb": (0.80, 0.64, 0.30), "Threaded": (0.80, 0.64, 0.30),
           "Compression": (0.75, 0.76, 0.78)}
THREAD_TYPES = ("NPT", "BSPP", "Metric thread")


def _ft():
    from bruceworx import fittings

    return fittings


def _bp():
    import bruceworx_routing_ports

    return bruceworx_routing_ports


def _part():
    import Part

    return Part


def V(p):
    return App.Vector(float(p[0]), float(p[1]), float(p[2]))


def is_body(obj):
    proxy = getattr(obj, "Proxy", None)
    return getattr(proxy, "Type", "") == FITTING


def body_of(obj):
    """The FittingBody of a fitting (the App::Part, a link to it, or the body itself)."""
    if obj is None:
        return None
    if is_body(obj):
        return obj
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        target = obj
    return next((o for o in getattr(target, "Group", []) or [] if is_body(o)), None)


def is_fitting(obj):
    return obj is not None and not is_body(obj) and body_of(obj) is not None


def fittings(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("App::Part") and is_fitting(o)]


def _restoring(obj):
    try:
        return "Restore" in obj.State or bool(getattr(obj.Document, "Restoring", False))
    except Exception:
        return False


def _set_enum(obj, name, choices, prefer=None):
    """Set the enumeration's choices, keeping its value when still valid (else ``prefer``, else
    the first)."""
    current = getattr(obj, name, "")
    if list(obj.getEnumerationsOfProperty(name) or []) != list(choices):
        setattr(obj, name, list(choices))
    want = prefer if prefer in choices else (current if current in choices else choices[0])
    if getattr(obj, name) != want:
        setattr(obj, name, want)


# ---------------------------------------------------------------------------------------------
# Shapes.


def _hex(af, base, direction, length):
    Part = _part()
    r = af / math.sqrt(3.0)
    pts = [App.Vector(r * math.cos(math.radians(60 * k + 30)), r * math.sin(math.radians(60 * k + 30)), 0) for k in range(6)]
    face = Part.Face(Part.makePolygon(pts + [pts[0]]))
    prism = face.extrude(App.Vector(0, 0, length))
    prism.Placement = App.Placement(V(base), App.Rotation(App.Vector(0, 0, 1), V(direction)))
    return prism


def _primitive(p):
    Part = _part()
    kind = p[0]
    if kind == "cyl":
        _k, base, direction, d, length = p
        return Part.makeCylinder(d / 2.0, length, V(base), V(direction))
    if kind == "cone":
        _k, base, direction, d1, d2, length = p
        return Part.makeCone(d1 / 2.0, d2 / 2.0, length, V(base), V(direction))
    if kind == "hex":
        _k, base, direction, af, length = p
        return _hex(af, base, direction, length)
    if kind == "sphere":
        _k, centre, d = p
        return Part.makeSphere(d / 2.0, V(centre))
    raise ValueError(f"unknown primitive {kind!r}")


def layout_shape(lay):
    """The solid of a ``fittings.Layout``: its solids fused, its bores cut."""
    solids = [_primitive(p) for p in lay.solids]
    shape = solids[0].multiFuse(solids[1:]) if len(solids) > 1 else solids[0]
    if lay.bores:
        bores = [_primitive(p) for p in lay.bores]
        cutter = bores[0].multiFuse(bores[1:]) if len(bores) > 1 else bores[0]
        shape = shape.cut(cutter)
    try:
        shape = shape.removeSplitter()
    except Exception:
        pass
    return shape


def port_placement(port):
    """The port frame's placement in the fitting: origin, +Z = the port's direction."""
    z = V(port.direction)
    x = App.Vector(1, 0, 0) if abs(z.x) < 0.9 else App.Vector(0, 1, 0)
    return _bp().as_placement((port.origin, port.direction, (x.x, x.y, x.z)))


# ---------------------------------------------------------------------------------------------
# The body feature.


class Fitting:
    def __init__(self, obj):
        self.Type = FITTING
        obj.Proxy = self
        self.setup(obj)

    def setup(self, obj):
        ft = _ft()
        self._quiet = True
        try:
            for name, doc in (("Family", "Push-to-connect, hose barb, threaded (NPT/BSPP) or compression"),
                              ("Style", "Straight, elbow, tee, bulkhead, adapter, plug, union"),
                              ("Size", "Tube OD, hose ID or thread size"),
                              ("Thread", "Male thread (push-to-connect and barb straights and elbows)")):
                if name not in obj.PropertiesList:
                    obj.addProperty("App::PropertyEnumeration", name, GROUP, doc)
            if not obj.getEnumerationsOfProperty("Family"):
                obj.Family = list(ft.FAMILIES)
            if "FittingCode" not in obj.PropertiesList:
                obj.addProperty("App::PropertyString", "FittingCode", GROUP, "Generic type code (not a vendor's P/N)")
                obj.setEditorMode("FittingCode", 1)
            if "BomExclude" not in obj.PropertiesList:
                obj.addProperty("App::PropertyBool", "BomExclude", "BruceWorx",
                                "The fitting (its App::Part) is the BOM line, not its shape")
                obj.BomExclude = True
                obj.setEditorMode("BomExclude", 2)
        finally:
            self._quiet = False

    def onDocumentRestored(self, obj):
        self.Type = FITTING
        self.setup(obj)

    def dumps(self):
        return {"Type": FITTING}

    def loads(self, state):
        self.Type = FITTING

    def onChanged(self, obj, prop):
        if prop not in ("Family", "Style", "Size", "Thread") or getattr(self, "_quiet", True) or _restoring(obj):
            return
        self.sync(obj)

    def choose(self, obj, family=None, style=None, size=None, thread=None):
        """Set the four choices at once (no intermediate rebuilds), then sync."""
        ft = _ft()
        self._quiet = True
        try:
            if family is not None:
                _set_enum(obj, "Family", list(ft.FAMILIES), family)
            fam = obj.Family
            _set_enum(obj, "Style", ft.styles(fam), style)
            _set_enum(obj, "Size", ft.sizes(fam), size)
            choices = ft.threads(fam, obj.Style, obj.Size) or [ft.NO_THREAD]
            _set_enum(obj, "Thread", choices, thread or ft.default_thread(fam, obj.Style, obj.Size))
        finally:
            self._quiet = False
        self.sync(obj)

    def layout(self, obj):
        ft = _ft()
        thread = obj.Thread if ft.has_thread(obj.Family, obj.Style) else None
        return ft.layout(obj.Family, obj.Style, obj.Size, thread)

    def sync(self, obj):
        """Enumerations consistent, then the ports and the fitting's properties from the layout."""
        ft = _ft()
        self._quiet = True
        try:
            fam = obj.Family
            _set_enum(obj, "Style", ft.styles(fam), obj.Style)
            _set_enum(obj, "Size", ft.sizes(fam), obj.Size)
            choices = ft.threads(fam, obj.Style, obj.Size) or [ft.NO_THREAD]
            thread = obj.Thread if obj.Thread in choices else ft.default_thread(fam, obj.Style, obj.Size)
            _set_enum(obj, "Thread", choices, thread)
        finally:
            self._quiet = False
        lay = self.layout(obj)
        obj.FittingCode = lay.code
        sync_ports(obj, lay)
        obj.touch()

    def execute(self, obj):
        lay = self.layout(obj)
        obj.Shape = layout_shape(lay)
        if obj.FittingCode != lay.code:
            obj.FittingCode = lay.code


class ViewProviderFitting:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def updateData(self, obj, prop):
        if prop == "Shape":
            colour(obj)

    def getIcon(self):
        return os.path.join(HERE, "BruceWorx_RouteFitting.svg")

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def colour(body):
    try:
        vobj = body.ViewObject
        if vobj is None:
            return
        material = App.Material()
        material.DiffuseColor = COLOURS.get(body.Family, (0.7, 0.7, 0.7))
        vobj.ShapeAppearance = [material]
    except Exception:
        pass


def container_of(body):
    try:
        return body.getParentGeoFeatureGroup()
    except Exception:
        return None


def _same(current, value):
    current = getattr(current, "Value", current)
    if isinstance(value, float):
        return abs(float(current) - value) <= 1e-9
    return current == value


def sync_ports(body, lay):
    """The container's port frames match ``lay``: placed, typed and sized; missing ones made, extra
    ones removed. The container's description, family and code follow."""
    bp = _bp()
    part = container_of(body)
    if part is None:
        return
    existing = {f.PortName: f for f in bp.ports(part)}
    for port in lay.ports:
        frame = existing.pop(port.name, None)
        props = dict(PortType=port.type, PortSize=port.size, TubeOD=float(port.tube_od), Engagement=float(port.engagement))
        if frame is None:
            bp.make_port(part, port.name, port_placement(port), **props)
            continue
        placement = port_placement(port)
        if not frame.Placement.isSame(placement, 1e-9):
            frame.Placement = placement
        for key, value in props.items():
            if not _same(getattr(frame, key), value):
                setattr(frame, key, value)
    for frame in existing.values():
        try:
            part.Document.removeObject(frame.Name)
        except Exception:
            pass
    describe(part, lay)


def _schema_apply(part):
    """The part-properties schema on the fitting (PartNumber, Description, Vendor, ...); plain
    strings when the schema cannot be loaded."""
    try:
        from bruceworx import partprops

        partprops.load_schema().apply(part, compute=False)
    except Exception:
        pass
    for name in ("PartNumber", "Description", "Vendor", "VendorPartNumber"):
        if name not in part.PropertiesList:
            part.addProperty("App::PropertyString", name, "BruceWorx", name)


def describe(part, lay):
    """Description (while it is empty or still the generated one), family, role and code."""
    bp = _bp()
    bp.set_connector(part, family=lay.family, role="Fitting")
    if "FittingCode" not in part.PropertiesList:
        part.addProperty("App::PropertyString", "FittingCode", "BruceWorx", "Generic type code (not a vendor's P/N)")
    if "AutoDescription" not in part.PropertiesList:
        part.addProperty("App::PropertyString", "AutoDescription", "BruceWorx", "The description last generated")
        part.setEditorMode("AutoDescription", 2)
    if "BomDetail" not in part.PropertiesList:
        part.addProperty("App::PropertyBool", "BomDetail", "BruceWorx", "Its shape is not a BOM line of its own")
        part.BomDetail = False
        part.setEditorMode("BomDetail", 2)
    if "Description" in part.PropertiesList and part.Description in ("", part.AutoDescription):
        part.Description = lay.description
    part.AutoDescription = lay.description
    part.FittingCode = lay.code


def make_fitting(doc=None, family="Push-to-connect", style="Straight", size="6 mm", thread=None, placement=None,
                 container=None, name="Fitting"):
    """A fitting (App::Part with its FittingBody and port frames). ``placement`` of the fitting;
    ``container`` an App::Part or assembly to put it in. Returns the App::Part."""
    _part()
    doc = doc or App.ActiveDocument
    part = doc.addObject("App::Part", name)
    _schema_apply(part)
    body = doc.addObject("Part::FeaturePython", "FittingBody")
    Fitting(body)
    part.addObject(body)
    if App.GuiUp and body.ViewObject is not None:
        ViewProviderFitting(body.ViewObject)
    body.Proxy.choose(body, family, style, size, thread)
    if placement is not None:
        part.Placement = placement
    if container is not None and hasattr(container, "addObject"):
        container.addObject(part)
    return part


def set_choice(fitting, family=None, style=None, size=None, thread=None):
    """Change a fitting's family, style, size or thread (None keeps it)."""
    body = body_of(fitting)
    body.Proxy.choose(body, family if family is not None else body.Family, style if style is not None else body.Style,
                      size if size is not None else body.Size, thread if thread is not None else body.Thread)
    return fitting


def mount_port(fitting):
    """The frame the fitting is placed by: its thread port, else port 1."""
    frames = _bp().ports(fitting)
    return next((f for f in frames if f.PortType in THREAD_TYPES), frames[0] if frames else None)


def place_fitting(fitting, point, direction, port=None, angle=0.0):
    """Move ``fitting`` so ``port`` (default: the mount port) sits on ``point`` (document frame)
    with its +Z along ``-direction``: ``direction`` is the outward normal of the face, hole or port
    it goes on, so a thread points into the part and two ports face each other. ``angle`` (degrees)
    turns it about that port's axis (an elbow's outlet round to where the tube goes)."""
    port = port or mount_port(fitting)
    if port is None:
        return fitting
    z = V(direction) * -1.0
    if z.Length < 1e-12:
        z = App.Vector(0, 0, -1)
    # The smallest rotation taking the port's +Z (in the fitting's frame) to -direction, so a
    # fitting placed on a top face stands as drawn and its tube port keeps its heading.
    local = port.Placement
    rotation = App.Rotation(local.Rotation.multVec(App.Vector(0, 0, 1)), z)
    if angle:
        rotation = App.Rotation(z, float(angle)).multiply(rotation)
    world = App.Placement(V(point) - rotation.multVec(local.Base), rotation)
    try:
        parent = fitting.getParentGeoFeatureGroup()
    except Exception:
        parent = None
    if parent is not None:
        world = parent.getGlobalPlacement().inverse().multiply(world)
    fitting.Placement = world
    return fitting
