# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pin and port frames on connectors and fittings (BW-ROU-05, 30 Oct 2026).

A connector or fitting is an ordinary part (one ``App::Part``) with a named attachment frame per
pin or port: an ``App::LocalCoordinateSystem`` directly in the part's Group, recognised by its
``BWFrame`` property ("Pin" or "Port"), not by its name. The frame's origin is where the wire or
tube enters (a contact cavity's rear face, a socket mouth, a barb tip); its +Z points out of the
part along the entry direction, which is the way a routed wire or tube leaves.

* A **pin** carries ``PinName``, the accepted gauge range ``GaugeMin``/``GaugeMax`` (AWG numbers:
  the thinnest wire is the larger number), ``TerminalPN``, ``SealPN``, ``DefaultSignal`` and
  ``StraightLength`` (0 = the default rule in the preferences, 2 x the wire's OD).
* A **port** carries ``PortName``, ``PortType`` (push-to-connect, barb, compression, NPT, BSPP,
  ...), ``PortSize``, ``TubeOD`` (the tube it takes; a barb's hose ID), ``StraightLength`` (0 =
  2 x OD, or the bender's clamp length if larger) and ``Engagement`` (tube inside or over the
  fitting beyond the frame, added to the cut length).
* The **connector** (the App::Part) carries ``ConnectorFamily``, ``ConnectorRole``, ``MatingPN``
  (several separated by ";") and ``FrameAt``, beside the part-properties schema (PartNumber, ...).

The schema is the contract with the connector library (``rou05-interface.md`` in the session's
agent notes; the Built chapter of ROU-05 has the same tables). A route path attaches to a frame
like to any other geometry (``frame_ref`` gives the ``(object, subname)`` pair, through an
App::Link in an assembly too); ``frame_at`` resolves such a pair to the frame, its global
placement and the connector it belongs to.

No GUI here: the connector library's generator imports this module headless.
"""

import re

import FreeCAD as App

GROUP = "BruceWorx"
PIN = "Pin"
PORT = "Port"
PORT_TYPES = ["Push-to-connect", "Barb", "Compression", "NPT", "BSPP", "Metric thread", "UNF", "Other"]
LCS_TYPES = ("App::LocalCoordinateSystem", "PartDesign::CoordinateSystem")

# property -> (type, doc, default)
PIN_PROPS = {
    "PinName": ("App::PropertyString", "Pin name as on the datasheet or footprint (1, A3, VBAT)", ""),
    "GaugeMin": ("App::PropertyInteger", "Thinnest wire accepted, AWG (the larger number, e.g. 28); 0 = not stated", 0),
    "GaugeMax": ("App::PropertyInteger", "Thickest wire accepted, AWG (the smaller number, e.g. 22); 0 = not stated", 0),
    "TerminalPN": ("App::PropertyString", "Crimp contact / terminal P/N; one per attached wire end goes to the BOM", ""),
    "SealPN": ("App::PropertyString", "Wire seal P/N (sealed connectors); one per attached wire end", ""),
    "DefaultSignal": ("App::PropertyString", "Signal a wire on this pin gets by default", ""),
    "StraightLength": ("App::PropertyLength", "Straight wire before the first bend; 0 = the default rule (Bend Rules)", 0.0),
}
PORT_PROPS = {
    "PortName": ("App::PropertyString", "Port name (1, 2, IN, A)", ""),
    "PortType": ("App::PropertyEnumeration", "Push-to-connect, barb, compression or a thread", None),
    "PortSize": ("App::PropertyString", "Size: 6 mm, 1/4 in, 1/8 NPT, M5", ""),
    "TubeOD": ("App::PropertyLength", "OD of the tube the port takes (barb: the hose ID; thread: the major diameter)", 0.0),
    "StraightLength": ("App::PropertyLength", "Straight tube before the first bend; 0 = the default rule (Bend Rules)", 0.0),
    "Engagement": ("App::PropertyLength", "Tube or hose inside or over the fitting beyond this frame (added to the cut length)", 0.0),
}
CONNECTOR_PROPS = {
    "ConnectorFamily": "Connector or fitting family (JST-XH, XT60, push-to-connect)",
    "ConnectorRole": "Wire housing, header, receptacle, plug, bullet, fitting",
    "MatingPN": "P/N of the mating half; several separated by ';'",
    "FrameAt": "Where the pin frames sit: '' (on this part), 'Mated plug' or 'Header face'",
}
MATE_KEYS = ("PartNumber", "VendorPartNumber", "ManufacturerPartNumber")


# ---------------------------------------------------------------------------------------------
# Recognising frames.


def frame_kind(obj):
    """"Pin", "Port" or "" for anything that is not a frame."""
    try:
        if obj is None or obj.TypeId not in LCS_TYPES or "BWFrame" not in obj.PropertiesList:
            return ""
        return obj.BWFrame if obj.BWFrame in (PIN, PORT) else ""
    except Exception:
        return ""


def is_frame(obj):
    return bool(frame_kind(obj))


def is_pin(obj):
    return frame_kind(obj) == PIN


def is_port(obj):
    return frame_kind(obj) == PORT


def frame_name(frame):
    """The pin's or port's own name ("1", "VBAT")."""
    if is_pin(frame):
        return str(frame.PinName)
    if is_port(frame):
        return str(frame.PortName)
    return ""


def _natural(text):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


def _children(part):
    if part is None:
        return []
    if is_frame(part):
        return [part]
    try:
        target = part.getLinkedObject(True)
    except Exception:
        target = part
    return list(getattr(target, "Group", []) or [])


def frames(part, kind=None):
    """The frames directly in ``part`` (an App::Part, or a link to one), pins first then ports,
    each in natural order of its name."""
    found = [o for o in _children(part) if is_frame(o) and (kind is None or frame_kind(o) == kind)]
    return sorted(found, key=lambda f: (0 if is_pin(f) else 1, _natural(frame_name(f))))


def pins(part):
    return frames(part, PIN)


def ports(part):
    return frames(part, PORT)


def pin(part, name):
    return next((f for f in pins(part) if f.PinName == str(name)), None)


def port(part, name):
    return next((f for f in ports(part) if f.PortName == str(name)), None)


# ---------------------------------------------------------------------------------------------
# Making frames.


def as_placement(placement):
    """An App.Placement from a Placement, ``(origin, z)`` or ``(origin, z, x)``."""
    if placement is None:
        return App.Placement()
    if isinstance(placement, App.Placement):
        return App.Placement(placement)
    origin = App.Vector(*[float(c) for c in placement[0]])
    z = App.Vector(*[float(c) for c in placement[1]])
    if z.Length < 1e-12:
        raise ValueError("the +Z direction of a frame cannot be zero")
    z.normalize()
    if len(placement) > 2 and placement[2] is not None:
        x = App.Vector(*[float(c) for c in placement[2]])
        x = x - z * x.dot(z)
        if x.Length < 1e-9:
            raise ValueError("+X is parallel to +Z")
        x.normalize()
        y = z.cross(x)
        rotation = App.Rotation(x, y, z, "ZXY")
    else:
        rotation = App.Rotation(App.Vector(0, 0, 1), z)
    return App.Placement(origin, rotation)


def _add(obj, kind, name, doc, default=None):
    if name not in obj.PropertiesList:
        obj.addProperty(kind, name, GROUP, doc)
        if default is not None:
            setattr(obj, name, default)


def add_frame_props(lcs, kind, **props):
    """Make an existing LCS a pin or port frame (adds the properties, sets ``props``)."""
    if kind not in (PIN, PORT):
        raise ValueError(f"a frame is a {PIN} or a {PORT}, not {kind!r}")
    if lcs.TypeId not in LCS_TYPES:
        raise ValueError(f"{lcs.Label} is not a coordinate system")
    _add(lcs, "App::PropertyString", "BWFrame", "Pin or Port: this LCS is a routing attachment frame (ROU-05)")
    lcs.BWFrame = kind
    lcs.setEditorMode("BWFrame", 1)
    table = PIN_PROPS if kind == PIN else PORT_PROPS
    for name, (ptype, doc, default) in table.items():
        new = name not in lcs.PropertiesList
        _add(lcs, ptype, name, doc, default)
        if name == "PortType" and new:
            lcs.PortType = PORT_TYPES
    for name, value in props.items():
        if name not in table:
            raise ValueError(f"{kind} has no property {name!r} (known: {', '.join(table)})")
        setattr(lcs, name, value)
    return lcs


def _object_name(prefix, name):
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    return prefix + (clean or "_")


def _make_frame(part, kind, name, placement, props):
    doc = part.Document
    lcs = doc.addObject("App::LocalCoordinateSystem", _object_name(kind, name))
    part.addObject(lcs)
    lcs.Label = f"{kind} {name}"
    lcs.Placement = as_placement(placement)
    key = "PinName" if kind == PIN else "PortName"
    add_frame_props(lcs, kind, **dict(props, **{key: str(name)}))
    try:
        lcs.ViewObject.Visibility = True
    except Exception:
        pass
    return lcs


def make_pin(part, name, placement=None, **props):
    """A pin frame named ``name`` in ``part`` (an App::Part). ``placement`` in the part's frame:
    an App.Placement, ``(origin, z)`` or ``(origin, z, x)``; +Z out along the wire entry."""
    return _make_frame(part, PIN, name, placement, props)


def make_port(part, name, placement=None, **props):
    """A port frame named ``name`` in ``part``; +Z out of the fitting along the tube."""
    return _make_frame(part, PORT, name, placement, props)


def set_connector(part, family="", mating_pn="", role="", frame_at=""):
    """The connector-level properties on ``part`` (only the arguments given are written)."""
    for name, doc in CONNECTOR_PROPS.items():
        _add(part, "App::PropertyString", name, doc, "")
    for name, value in (("ConnectorFamily", family), ("MatingPN", mating_pn), ("ConnectorRole", role),
                        ("FrameAt", frame_at)):
        if value:
            setattr(part, name, str(value))
    return part


def is_connector(obj):
    """An App::Part (or a link to one) that has pin or port frames or connector properties."""
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        target = obj
    if target is None or not target.isDerivedFrom("App::Part"):
        return False
    return "ConnectorFamily" in target.PropertiesList or bool(frames(target))


# ---------------------------------------------------------------------------------------------
# Resolving attachments and picks.


def _parent_frame(obj):
    try:
        return obj.getGlobalPlacement().multiply(obj.Placement.inverse())
    except Exception:
        return App.Placement()


def _subpath(sub):
    """The object names of a subname ("J1.Pin1.X_Axis001." -> ["J1", "Pin1", "X_Axis001"]),
    without a trailing element name (Face1, Vertex2)."""
    parts = (sub or "").split(".")
    names = parts[:-1] if parts else []
    return [n for n in names if n]


def frame_at(obj, sub=""):
    """``(frame, global placement, connector)`` for an attachment or pick ``(obj, sub)`` that is,
    or is inside, a pin or port frame (an axis or plane of the LCS counts), else None. The
    connector is the nearest App::Link above the frame in the path (an instance in an assembly:
    its label is the reference, J1), else the frame's own App::Part."""
    if obj is None:
        return None
    try:
        if not sub:
            if not is_frame(obj):
                return None
            return obj, obj.getGlobalPlacement(), _owner(obj)
        names = _subpath(sub)
        chain = list(obj.getSubObjectList(".".join(names) + ".")) if names else [obj]
        index = next((i for i in range(len(chain) - 1, -1, -1) if is_frame(chain[i])), None)
        if index is None:
            return None
        frame = chain[index]
        if index == 0:
            return frame, frame.getGlobalPlacement(), _owner(frame)
        frame_sub = ".".join(names[:index]) + "."
        placement = obj.getSubObject(frame_sub, 3)
        if not isinstance(placement, App.Placement):
            return None
        placement = _parent_frame(obj).multiply(placement)
        connector = None
        for k in range(index - 1, -1, -1):
            if chain[k].isDerivedFrom("App::Link"):
                connector = chain[k]
                break
        if connector is None:
            connector = chain[index - 1] if index >= 1 and chain[index - 1].isDerivedFrom("App::Part") else _owner(frame)
        return frame, placement, connector
    except Exception:
        return None


def _owner(frame):
    try:
        return frame.getParentGeoFeatureGroup()
    except Exception:
        return None


def frame_ref(owner, frame):
    """``(object, subname)`` a route path attaches to for ``frame``: ``owner`` is the LCS itself
    (``(frame, "")``), the App::Part holding it, or an App::Link to that part."""
    if owner is None or owner is frame:
        return frame, ""
    return owner, frame.Name + "."


def connector_label(connector):
    return connector.Label if connector is not None else ""


def mate_keys(obj):
    """The P/Ns another connector's MatingPN may name for ``obj`` (a part or a link to one)."""
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        target = obj
    keys = set()
    for holder in (obj, target):
        for name in MATE_KEYS:
            value = str(getattr(holder, name, "") or "").strip() if holder is not None else ""
            if value:
                keys.add(value)
    return keys


def mating_pns(obj):
    try:
        target = obj.getLinkedObject(True)
    except Exception:
        target = obj
    text = str(getattr(target, "MatingPN", "") or getattr(obj, "MatingPN", "") or "")
    return {t.strip() for t in text.split(";") if t.strip()}


def mates(a, b):
    """True when ``a`` and ``b`` are halves of a mating pair (either one's MatingPN names the other)."""
    return bool(mating_pns(a) & mate_keys(b)) or bool(mating_pns(b) & mate_keys(a))


def connectors(doc):
    """Connector instances in ``doc``: links to connector parts, and connector parts not used by a
    link in this document."""
    out, linked = [], set()
    for obj in doc.Objects:
        if obj.isDerivedFrom("App::Link") and is_connector(obj):
            out.append(obj)
            try:
                linked.add(obj.getLinkedObject(True).Name)
            except Exception:
                pass
    for obj in doc.Objects:
        if obj.isDerivedFrom("App::Part") and is_connector(obj) and obj.Name not in linked:
            out.append(obj)
    return out


def _where(obj):
    try:
        return obj.getGlobalPlacement().Base
    except Exception:
        return App.Vector()


def mated_pairs(doc):
    """``{connector name: mate}`` for every mated pair in ``doc``: the candidate pairs (one's
    MatingPN names the other) taken nearest first, each connector in one pair at most, so two
    housings on two identical headers pair with the header each sits on."""
    found = connectors(doc)
    candidates = []
    for i, a in enumerate(found):
        for b in found[i + 1:]:
            if mates(a, b):
                candidates.append(((_where(a) - _where(b)).Length, a, b))
    candidates.sort(key=lambda c: c[0])
    pairs = {}
    for _d, a, b in candidates:
        if a.Name in pairs or b.Name in pairs:
            continue
        pairs[a.Name], pairs[b.Name] = b, a
    return pairs


def mate_of(connector, doc=None):
    """The connector instance mated with ``connector`` in its document (``mated_pairs``), or None."""
    if connector is None:
        return None
    return mated_pairs(doc or connector.Document).get(connector.Name)
