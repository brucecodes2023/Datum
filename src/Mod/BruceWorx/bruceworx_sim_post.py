# SPDX-License-Identifier: LGPL-2.1-or-later
"""SIM-04: rotating machinery in the static study engine (centrifugal load, cylindrical stress, contours).

The logic lives here, free of the GUI, so the study panel, the planned simulation workspace (chapter 69) and a
design script in FreeCADCmd all call the same functions:

* ``add_spin`` -- a spin (centrifugal) load: FEM's ``Fem::ConstraintCentrif`` about a picked axis, the speed in rpm
  (stored as FEM's ``RotationFrequency`` in 1/s).  CalculiX gets ``*DLOAD  <set>,CENTRIF,omega^2,point,direction``.
* ``cylindrical_stress`` -- radial, hoop, axial and shear stress per node about an axis (the spin axis by default),
  from the CalculiX stress tensor; stored on the result object as ``StressRadial`` / ``StressHoop`` / ``StressAxial`` /
  ``StressRTheta`` so they colour like von Mises.
* ``rotating_disc`` -- the closed form of a rotating solid / annular disc (Timoshenko), the validation reference.
* ``export_vtu`` / ``radial_profile`` / ``contour_png`` -- the result as a .vtu with every field, line plots of a
  field along a ray, and off-screen pictures (pvbatch on the .vtu, so it works in FreeCADCmd).

Units follow the study: mm, MPa, rpm.  Nothing here changes the solver or the other study types.
"""

import math
import os

import bruceworx_sim as sim

SPIN_NAME = "Spin"
AXIS_NAME = "SpinAxis"
RPM_TO_HZ = 1.0 / 60.0

# the contour fields of a static result: key -> (label, unit, result property)
FIELDS = {
    "vonMises": ("von Mises", "MPa", "vonMises"),
    "displacement": ("Displacement", "mm", "DisplacementLengths"),
    "radial": ("Radial stress σrr", "MPa", "StressRadial"),
    "hoop": ("Hoop stress σθθ", "MPa", "StressHoop"),
    "axial": ("Axial stress σzz", "MPa", "StressAxial"),
    "shear": ("Shear stress σrθ", "MPa", "StressRTheta"),
    "safety": ("Safety factor", "", "SafetyFactor"),
}
CYLINDRICAL = ("radial", "hoop", "axial", "shear")
FIELD_ALIASES = {"vm": "vonMises", "von_mises": "vonMises", "stress": "vonMises", "disp": "displacement",
                 "sf": "safety", "sigma_rr": "radial", "sigma_tt": "hoop", "sigma_theta": "hoop", "sigma_zz": "axial",
                 "sigma_rt": "shear", "rtheta": "shear"}


class PostError(Exception):
    """A rotor setup or post-processing problem, in words for the user."""


def field_key(name):
    key = FIELD_ALIASES.get(str(name).lower(), name)
    if key not in FIELDS:
        raise PostError(f"unknown field {name!r}: use one of {', '.join(FIELDS)}")
    return key


# ----- vectors without FreeCAD ---------------------------------------------------------------------------------


def _vec(v):
    return (float(v.x), float(v.y), float(v.z)) if hasattr(v, "x") else (float(v[0]), float(v[1]), float(v[2]))


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise PostError("the axis direction is zero")
    return tuple(c / n for c in v)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def point_axis_distance(point, axis):
    """Distance from a point to the axis line ((point, direction))."""
    p0, d = axis
    d = _unit(d)
    rel = _sub(_vec(point), p0)
    along = _dot(rel, d)
    perp = (rel[0] - along * d[0], rel[1] - along * d[1], rel[2] - along * d[2])
    return math.sqrt(_dot(perp, perp))


# ----- finding an axis ---------------------------------------------------------------------------------------------


def axis_from_ref(obj, sub):
    """((x, y, z), (dx, dy, dz)) in global coordinates of a picked straight edge, circular edge or cylindrical
    (conical, revolved) face of ``obj`` -- the axis of the circle or surface."""
    import FreeCAD

    shape = sim.shape_of(obj)
    if shape is None:
        raise PostError(f"{getattr(obj, 'Label', obj)} has no shape to take an axis from")
    element = shape.getElement(sub)
    placement = FreeCAD.Placement()
    try:  # the shape already carries obj.Placement; what is missing is the parents' part (an App::Part around it)
        placement = obj.getGlobalPlacement().multiply(obj.Placement.inverse())
    except Exception:
        pass
    if sub.startswith("Edge"):
        curve = element.Curve
        kind = curve.TypeId
        if kind == "Part::GeomLine":
            point, direction = curve.Location, curve.Direction
        elif hasattr(curve, "Axis"):
            point, direction = curve.Center, curve.Axis
        else:
            raise PostError(f"{sub}: only a straight or circular edge gives an axis (this one is {kind.split('::')[-1]})")
    elif sub.startswith("Face"):
        surface = element.Surface
        kind = surface.TypeId
        if kind in ("Part::GeomCylinder", "Part::GeomCone", "Part::GeomTorus"):
            point, direction = surface.Center, surface.Axis
        elif kind == "Part::GeomSurfaceOfRevolution":
            point, direction = surface.Location, surface.Direction
        else:
            raise PostError(f"{sub}: pick a cylindrical, conical or revolved face, or a circular edge")
    else:
        raise PostError(f"{sub}: pick an edge or a cylindrical face for the axis")
    return _vec(placement.multVec(point)), _unit(_vec(placement.Rotation.multVec(direction)))


def model_axis(analysis):
    """The axis of the model's largest circular edge or cylindrical face (a wheel's bore or rim), or None."""
    model = getattr(analysis, "Model", None) if "Model" in analysis.PropertiesList else None
    if model is None:
        mesh = sim.members(analysis)["mesh"]
        model = getattr(mesh, "Shape", None) if mesh is not None else None
    shape = sim.shape_of(model) if model is not None else None
    if shape is None:
        return None
    best, best_r = None, 0.0
    for i, edge in enumerate(shape.Edges, 1):
        try:
            curve = edge.Curve
            if curve.TypeId in ("Part::GeomCircle", "Part::GeomArcOfCircle") and curve.Radius > best_r:
                best, best_r = "Edge%d" % i, curve.Radius
        except Exception:
            continue
    for i, face in enumerate(shape.Faces, 1):
        try:
            surface = face.Surface
            if surface.TypeId == "Part::GeomCylinder" and surface.Radius > best_r:
                best, best_r = "Face%d" % i, surface.Radius
        except Exception:
            continue
    if best is None:
        return None
    try:
        return axis_from_ref(model, best)
    except PostError:
        return None


def resolve_axis(axis, analysis=None):
    """An axis from what a script or the panel gives: ((p), (d)), (object, 'Edge3'), 'x'/'y'/'z' (through the origin), a
    line object, or None (the model's own, see ``model_axis``)."""
    if axis is None:
        found = model_axis(analysis) if analysis is not None else None
        if found is None:
            raise PostError("no axis: pick a circular edge or a cylindrical face of the rotor, or give point and direction")
        return found
    if isinstance(axis, str):
        table = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
        if axis.lower() in table:
            return (0.0, 0.0, 0.0), table[axis.lower()]
        raise PostError(f"unknown axis {axis!r}")
    if hasattr(axis, "TypeId"):  # a line object
        return axis_from_ref(axis, "Edge1")
    if len(axis) == 2 and hasattr(axis[0], "TypeId"):
        return axis_from_ref(axis[0], axis[1])
    point, direction = axis
    return _vec(point), _unit(_vec(direction))


# ----- the spin load -----------------------------------------------------------------------------------------------


def is_spin(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", "") == "Fem::ConstraintCentrif" or obj.TypeId == "Fem::ConstraintCentrif"


def spin_loads(analysis):
    return [o for o in getattr(analysis, "Group", []) or [] if is_spin(o)]


def spin_rpm(spin):
    """The speed of a spin load in rpm."""
    return float(spin.RotationFrequency.getValueAs("1/s")) * 60.0


def spin_axis(spin):
    """((x, y, z), (dx, dy, dz)) of a spin load's rotation axis, global coordinates; None when it has no axis."""
    refs = list(getattr(spin, "RotationAxis", []) or [])
    if not refs or not refs[0][1]:
        return None
    return axis_from_ref(refs[0][0], refs[0][1][0])


def _axis_object(doc, axis, size):
    """A hidden ``Part::Line`` along the axis: the CalculiX writer reads its edge (FEM wants a line, not a circle)."""
    import FreeCAD

    point, direction = axis
    obj = doc.addObject("Part::Line", AXIS_NAME)
    half = max(float(size), 1.0) * 0.6
    obj.X1, obj.Y1, obj.Z1 = (point[i] - half * direction[i] for i in range(3))
    obj.X2, obj.Y2, obj.Z2 = (point[i] + half * direction[i] for i in range(3))
    obj.Label = "Spin axis"
    doc.recompute()
    if FreeCAD.GuiUp and getattr(obj, "ViewObject", None) is not None:
        obj.ViewObject.Visibility = False
    return obj


def set_spin_speed(spin, rpm):
    """Sets a spin load's speed in rpm and keeps its label in step."""
    spin.RotationFrequency = f"{float(rpm) * RPM_TO_HZ} 1/s"
    spin.Label = f"{SPIN_NAME} {float(rpm):g} rpm"
    return spin


def add_spin(analysis, rpm, axis=None, name=SPIN_NAME):
    """Adds a spin (centrifugal) load of ``rpm`` revolutions per minute about ``axis`` to the study.

    ``axis``: ((x, y, z), (dx, dy, dz)) in mm; or (object, 'Edge3' / 'Face2') -- a circular edge or cylindrical face
    gives its axis, a straight edge its line; or 'x' / 'y' / 'z' through the origin; None takes the model's largest
    circular edge or cylindrical face.  Every element of the model spins.
    Returns the ``Fem::ConstraintCentrif`` (FEM's own object; ``RotationFrequency`` holds rpm/60 in 1/s).
    """
    import ObjectsFem

    if not sim.is_study(analysis) or sim.study_kind(analysis) in sim.AIRFLOW_KINDS:
        raise PostError("a spin load belongs to a structural study")
    rpm = float(rpm)
    if not rpm:
        raise PostError("the speed is zero")
    resolved = resolve_axis(axis, analysis)
    doc = analysis.Document
    mesh = sim.members(analysis)["mesh"]
    shape = sim.shape_of(getattr(mesh, "Shape", None)) if mesh is not None else None
    size = shape.BoundBox.DiagonalLength if shape is not None else 100.0
    line = _axis_object(doc, resolved, size)
    spin = ObjectsFem.makeConstraintCentrif(doc, name)
    spin.RotationAxis = [(line, ["Edge1"])]
    set_spin_speed(spin, rpm)
    analysis.addObject(spin)
    doc.recompute()
    return spin


# ----- the pre-check of a spinning study -----------------------------------------------------------------------------


def precheck_spin(analysis, m=None):
    """Warnings and errors for the spin loads of a study: [(level, text)]. Called by ``sim.precheck``."""
    spins = spin_loads(analysis)
    if not spins:
        return []
    issues = []
    m = m or sim.members(analysis)
    for spin in spins:
        try:
            axis = spin_axis(spin)
        except Exception as exc:
            axis = None
            issues.append(("error", f"{spin.Label}: {exc}"))
        else:
            if axis is None:
                issues.append(("error", f"{spin.Label}: no rotation axis chosen"))
        if not spin_rpm(spin):
            issues.append(("error", f"{spin.Label}: the speed is zero"))
        if axis is None:
            continue
        mesh = m["mesh"]
        shape = sim.shape_of(getattr(mesh, "Shape", None)) if mesh is not None else None
        if shape is not None and shape.Solids:
            try:
                vol = sum(so.Volume for so in shape.Solids)
                cm = [sum(so.CenterOfMass[i] * so.Volume for so in shape.Solids) / vol for i in range(3)]
                off = point_axis_distance(cm, axis)
                size = shape.BoundBox.DiagonalLength
                if off > 0.05 * size:
                    issues.append(("warning", f"{spin.Label}: the spin axis misses the part's centre of mass by {off:.3g} mm "
                                              f"({100 * off / size:.0f} % of its size): an unbalanced rotor, the supports carry "
                                              "the net centrifugal force"))
            except Exception:
                pass
    for mat in m["materials"]:
        if not sim._density_kg_m3((mat.Material or {}).get("Density")):
            issues.append(("error", f"{mat.Label}: no density: a spin load needs the mass"))
    if any(sub.startswith("Vertex") for s in m["supports"] for _o, subs in (getattr(s, "References", None) or [])
           for sub in subs):
        issues.append(("warning", "Point supports pin the growth of the rotor there: read the stress away from them "
                                  "(symmetry planes or a bore face avoid it)"))
    return issues


# ----- the closed form of a rotating disc -----------------------------------------------------------------------


def rotating_disc(r, a, b, rho, omega, nu):
    """(sigma_r, sigma_theta) of a thin rotating disc in plane stress (Timoshenko and Goodier, Theory of Elasticity,
    3rd ed., section 32), free at both radii.  ``a`` = 0 is the solid disc.  Any consistent units (rho in
    mass per volume, omega in rad/s, r in length): with kg/m^3 and m the stress is in Pa.

      annular:  sigma_r     = (3+nu)/8 rho w^2 (a^2 + b^2 - a^2 b^2 / r^2 - r^2)
                sigma_theta = (3+nu)/8 rho w^2 (a^2 + b^2 + a^2 b^2 / r^2 - (1+3 nu)/(3+nu) r^2)
      solid:    sigma_r     = (3+nu)/8 rho w^2 (b^2 - r^2)
                sigma_theta = (3+nu)/8 rho w^2 (b^2 - (1+3 nu)/(3+nu) r^2)

    At the bore sigma_theta = rho w^2 / 4 [(3+nu) b^2 + (1-nu) a^2]; at a = 0 the centre has (3+nu)/8 rho w^2 b^2 in both."""
    k = (3.0 + nu) / 8.0 * rho * omega * omega
    c = (1.0 + 3.0 * nu) / (3.0 + nu)
    if a <= 0.0:
        return k * (b * b - r * r), k * (b * b - c * r * r)
    ab = a * a * b * b / (r * r)
    return k * (a * a + b * b - ab - r * r), k * (a * a + b * b + ab - c * r * r)


# ----- cylindrical stress components ----------------------------------------------------------------------------


def axis_frame(axis):
    """(point, a, u, v): the axis point, its unit direction and two unit vectors across it (u: the global X, or Y when the
    axis is along X, taken out of the axis; v = a x u), the frame of the angular positions and of the quarter cut."""
    import numpy as np

    point, direction = axis
    a = np.array(_unit(direction))
    ref = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = ref - a * float(ref @ a)
    u /= np.linalg.norm(u)
    v = np.cross(a, u)
    return np.array(point, dtype=float), a, u, v


def cylindrical_components(xyz, tensor, axis):
    """Radial, hoop, axial and shear components of the stress tensors at points about an axis (numpy, no FreeCAD).

    xyz: (n, 3) positions; tensor: (n, 3, 3) stress; returns {"r", "radial", "hoop", "axial", "shear"} arrays.
    On the axis itself (r ~ 0) the radial and hoop directions are undefined: both get the mean of the two in-plane
    normal stresses and the shear 0 (the right limit for an axisymmetric field)."""
    import numpy as np

    point, a, _u, _v = axis_frame(axis)
    rel = np.asarray(xyz, dtype=float) - point
    along = rel @ a
    perp = rel - np.outer(along, a)
    r = np.linalg.norm(perp, axis=1)
    scale = max(float(r.max()) if len(r) else 1.0, 1e-9)
    on_axis = r < 1e-7 * scale
    er = np.where(on_axis[:, None], 0.0, perp / np.where(on_axis, 1.0, r)[:, None])
    et = np.cross(a, er)
    ea = np.broadcast_to(a, er.shape)

    def comp(x, y):
        return np.einsum("ni,nij,nj->n", x, tensor, y)

    radial, hoop, axial, shear = comp(er, er), comp(et, et), comp(ea, ea), comp(er, et)
    if on_axis.any():
        _p, _a, u, v = axis_frame(axis)
        uu = np.broadcast_to(u, er.shape)
        vv = np.broadcast_to(v, er.shape)
        mean = 0.5 * (comp(uu, uu) + comp(vv, vv))
        radial = np.where(on_axis, mean, radial)
        hoop = np.where(on_axis, mean, hoop)
        shear = np.where(on_axis, 0.0, shear)
    return {"r": r, "radial": radial, "hoop": hoop, "axial": axial, "shear": shear}


def node_positions(result):
    """(ids, xyz): the result's node numbers (the order of its value lists) and their positions, numpy arrays."""
    import numpy as np

    nodes = result.Mesh.FemMesh.Nodes
    ids = list(result.NodeNumbers)
    xyz = np.array([[nodes[i].x, nodes[i].y, nodes[i].z] for i in ids], dtype=float).reshape(-1, 3)
    return np.array(ids), xyz


def stress_tensors(result):
    """(n, 3, 3) numpy array of the nodal stress tensors of a CalculiX result, MPa."""
    import numpy as np

    xx, yy, zz, xy, xz, yz = (np.array(list(getattr(result, f"NodeStress{c}")), dtype=float)
                              for c in ("XX", "YY", "ZZ", "XY", "XZ", "YZ"))
    if not len(xx):
        raise PostError("this result has no stress tensor (a modal or buckling mode has none)")
    t = np.empty((len(xx), 3, 3))
    t[:, 0, 0], t[:, 1, 1], t[:, 2, 2] = xx, yy, zz
    t[:, 0, 1] = t[:, 1, 0] = xy
    t[:, 0, 2] = t[:, 2, 0] = xz
    t[:, 1, 2] = t[:, 2, 1] = yz
    return t


def study_axis(analysis):
    """The axis the user picked for the study's cylindrical stress (``StressAxisPoint`` / ``StressAxisDirection``), or None."""
    if analysis is not None and "StressAxisPoint" in analysis.PropertiesList and analysis.StressAxisSet:
        return _vec(analysis.StressAxisPoint), _unit(_vec(analysis.StressAxisDirection))
    return None


def set_study_axis(analysis, axis):
    """Stores a picked axis (anything ``resolve_axis`` takes) for the study's cylindrical stress; None clears it, so the spin
    axis (else the model's own) applies again.  Returns the axis or None."""
    import FreeCAD

    if "StressAxisSet" not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyBool", "StressAxisSet", "Results", "[BW] A picked axis for the cylindrical stress")
        analysis.addProperty("App::PropertyVector", "StressAxisPoint", "Results", "[BW] A point of that axis (mm)")
        analysis.addProperty("App::PropertyVector", "StressAxisDirection", "Results", "[BW] Its direction")
    if axis is None:
        analysis.StressAxisSet = False
        return None
    point, direction = resolve_axis(axis, analysis)
    analysis.StressAxisPoint = FreeCAD.Vector(*point)
    analysis.StressAxisDirection = FreeCAD.Vector(*direction)
    analysis.StressAxisSet = True
    return point, direction


def default_axis(analysis):
    """The axis a study's cylindrical stress is about: the one picked for the study, else its first spin load's, else the
    model's own (bore or rim)."""
    picked = study_axis(analysis)
    if picked is not None:
        return picked
    if analysis is not None:
        for spin in spin_loads(analysis):
            axis = spin_axis(spin)
            if axis is not None:
                return axis
    return resolve_axis(None, analysis)


_STORED = (("StressRadial", "radial"), ("StressHoop", "hoop"), ("StressAxial", "axial"), ("StressRTheta", "shear"))


def _same_axis(result, axis):
    try:
        p, d = tuple(result.CylAxisPoint), tuple(result.CylAxisDirection)
    except Exception:
        return False
    q, e = _vec(axis[0]), _unit(_vec(axis[1]))
    return all(abs(x - y) < 1e-9 for x, y in zip(p, q)) and all(abs(x - y) < 1e-9 for x, y in zip(d, e))


def cylindrical_stress(result, axis=None, analysis=None, force=False):
    """Radial, hoop, axial and shear stress per node of a static result about ``axis`` (see ``resolve_axis``; None: the
    study's spin axis, else the model's bore or rim).  The values are stored on the result as the float lists
    ``StressRadial``, ``StressHoop``, ``StressAxial``, ``StressRTheta`` (MPa, in NodeNumbers order) with the axis in
    ``CylAxisPoint`` / ``CylAxisDirection``, and recomputed only when the axis changes.
    Returns {"r", "radial", "hoop", "axial", "shear"} (lists)."""
    import FreeCAD

    analysis = analysis or sim.study_of(result)
    resolved = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
    props = result.PropertiesList
    if not force and "StressHoop" in props and len(result.StressHoop) == len(result.NodeNumbers) and _same_axis(result, resolved):
        ids, xyz = node_positions(result)
        point, a, _u, _v = axis_frame(resolved)
        rel = xyz - point
        r = ((rel - (rel @ a)[:, None] * a) ** 2).sum(axis=1) ** 0.5
        return {"r": list(r), "radial": list(result.StressRadial), "hoop": list(result.StressHoop),
                "axial": list(result.StressAxial), "shear": list(result.StressRTheta)}
    ids, xyz = node_positions(result)
    values = cylindrical_components(xyz, stress_tensors(result), resolved)
    if "StressHoop" not in props:
        for name, _key in _STORED:
            result.addProperty("App::PropertyFloatList", name, "Result", "[BW] cylindrical stress component (MPa) about CylAxis")
        result.addProperty("App::PropertyVector", "CylAxisPoint", "Result", "[BW] a point of the axis the cylindrical stress is about (mm)")
        result.addProperty("App::PropertyVector", "CylAxisDirection", "Result", "[BW] the direction of that axis")
    for name, key in _STORED:
        setattr(result, name, [float(x) for x in values[key]])
    result.CylAxisPoint = FreeCAD.Vector(*_vec(resolved[0]))
    result.CylAxisDirection = FreeCAD.Vector(*_unit(_vec(resolved[1])))
    return {k: [float(x) for x in v] for k, v in values.items()}


def field_values(result, key, analysis=None, axis=None):
    """The per-node values of a contour field (FIELDS keys) in NodeNumbers order; a cylindrical one is computed on demand."""
    key = field_key(key)
    if key in CYLINDRICAL:
        cylindrical_stress(result, axis, analysis)
    values = list(getattr(result, FIELDS[key][2], []) or [])
    if not values:
        raise PostError(f"this result has no {FIELDS[key][0]}")
    return values


def field_range(result, key, analysis=None, axis=None):
    values = field_values(result, key, analysis, axis)
    return min(values), max(values)


# ----- colour maps and the fixed colour range -------------------------------------------------------------------------

COLORMAPS = ("Rainbow", "Turbo", "Jet")
DEFAULT_COLORMAP = "Rainbow"


def colormap_rgb(name, t):
    """(r, g, b) in 0..1 of a colour map at t in 0..1.  Rainbow: FEM's blue to red through cyan, green and yellow (the look
    of the other fields); Turbo: Google's polynomial approximation; Jet: the classic piecewise-linear map."""
    t = min(1.0, max(0.0, float(t)))
    key = str(name or DEFAULT_COLORMAP).lower()
    if key == "turbo":
        r = 0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))))
        g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))))
        b = 0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))))
        return tuple(min(1.0, max(0.0, c)) for c in (r, g, b))
    if key == "jet":
        def ramp(x):
            return min(1.0, max(0.0, 1.5 - abs(4.0 * t - x)))
        return ramp(3.0), ramp(2.0), ramp(1.0)
    if key == "rainbow":
        import colorsys

        return colorsys.hsv_to_rgb((1.0 - t) * 2.0 / 3.0, 1.0, 1.0)
    raise PostError(f"unknown colour map {name!r}: use one of {', '.join(COLORMAPS)}")


def colormap_points(name, count=33):
    """[(t, r, g, b)] samples of a colour map, for a ParaView colour table."""
    return [(i / (count - 1),) + colormap_rgb(name, i / (count - 1)) for i in range(count)]


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in rgb)


def legend_chips(name, lo, hi, unit="", count=7):
    """HTML for a panel legend: `count` colour chips from lo to hi."""
    chips = []
    for i in range(count):
        t = i / (count - 1)
        color = _hex(colormap_rgb(name, t))
        light = sum(w * c for w, c in zip((0.299, 0.587, 0.114), colormap_rgb(name, t))) > 0.6
        chips.append(f"<span style='background:{color};color:{'#000' if light else '#fff'};padding:1px 5px;margin-right:2px'>"
                     f"{lo + t * (hi - lo):.4g}</span>")
    return "".join(chips) + (f" {unit}" if unit else "")


def fixed_range(analysis, key):
    """The (min, max) a study pins a field's colour bar to, or None (each run uses its own).  Stored on the study as
    ``RangeFixed_<key>`` / ``RangeMin_<key>`` / ``RangeMax_<key>`` so it is remembered per study and per field."""
    if analysis is None:
        return None
    props = analysis.PropertiesList
    if f"RangeFixed_{key}" in props and getattr(analysis, f"RangeFixed_{key}") and \
            getattr(analysis, f"RangeMax_{key}") > getattr(analysis, f"RangeMin_{key}"):
        return float(getattr(analysis, f"RangeMin_{key}")), float(getattr(analysis, f"RangeMax_{key}"))
    return None


def set_fixed_range(analysis, key, lo=None, hi=None):
    """Pins (lo, hi) as the colour range of a field for the study; ``lo`` None clears the pin (the range is kept)."""
    key = field_key(key)
    props = analysis.PropertiesList
    if f"RangeFixed_{key}" not in props:
        analysis.addProperty("App::PropertyBool", f"RangeFixed_{key}", "Results", "[BW] Use the fixed colour range of this field")
        analysis.addProperty("App::PropertyFloat", f"RangeMin_{key}", "Results", "[BW] Fixed colour range, minimum")
        analysis.addProperty("App::PropertyFloat", f"RangeMax_{key}", "Results", "[BW] Fixed colour range, maximum")
    if lo is None:
        setattr(analysis, f"RangeFixed_{key}", False)
        return None
    if not hi > lo:
        raise PostError("the colour range needs max > min")
    setattr(analysis, f"RangeMin_{key}", float(lo))
    setattr(analysis, f"RangeMax_{key}", float(hi))
    setattr(analysis, f"RangeFixed_{key}", True)
    return float(lo), float(hi)


def study_colormap(analysis):
    if analysis is not None and "ColorMap" in analysis.PropertiesList and analysis.ColorMap in COLORMAPS:
        return analysis.ColorMap
    return DEFAULT_COLORMAP


def set_study_colormap(analysis, name):
    if name not in COLORMAPS:
        raise PostError(f"unknown colour map {name!r}")
    if "ColorMap" not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyString", "ColorMap", "Results", "[BW] Colour map of the contour views")
    analysis.ColorMap = name


def node_colors(values, lo, hi, name, bins=64, ids=None):
    """{(node id, ...): (r, g, b)} -- the nodes grouped into `bins` colours of the map between lo and hi, the form the result
    mesh's NodeColor takes.  `ids` are the node numbers of the values (default: their positions)."""
    groups = {}
    span = (hi - lo) or 1.0
    for i, value in enumerate(values):
        b = int(min(bins - 1, max(0, math.floor((value - lo) / span * bins))))
        groups.setdefault(b, []).append(i if ids is None else ids[i])
    return {tuple(members): colormap_rgb(name, (b + 0.5) / bins) for b, members in groups.items()}


# ----- section views of the result mesh (the 3D view) --------------------------------------------------------------------

SECTIONS = ("none", "half", "quarter")


def study_section(analysis):
    if analysis is not None and "SectionView" in analysis.PropertiesList and analysis.SectionView in SECTIONS:
        return analysis.SectionView
    return "none"


def set_study_section(analysis, mode):
    if mode not in SECTIONS:
        raise PostError(f"unknown section {mode!r}: use one of {', '.join(SECTIONS)}")
    if "SectionView" not in analysis.PropertiesList:
        analysis.addProperty("App::PropertyString", "SectionView", "Results", "[BW] Section of the result view: none, half or quarter")
    analysis.SectionView = mode


def kept_elements(result, mode, axis):
    """The volume element ids of the result mesh that stay in a section view: 'half' drops the elements whose centre has
    v > 0 in the axis frame, 'quarter' those with u > 0 and v > 0 (a quarter cut away, as in a turbine drawing)."""
    import numpy as np

    fem = result.Mesh.FemMesh
    nodes = fem.Nodes
    point, _a, u, v = axis_frame(axis)
    kept = []
    for vid in fem.Volumes:
        corners = fem.getElementNodes(vid)[:4]
        c = np.mean([[nodes[n].x, nodes[n].y, nodes[n].z] for n in corners], axis=0) - point
        x, y = float(c @ u), float(c @ v)
        if mode == "quarter" and x > 0 and y > 0:
            continue
        if mode == "half" and y > 0:
            continue
        kept.append(vid)
    return kept


def section_mesh(analysis, result, mode, axis=None):
    """A mesh object (outside the study) with the elements of the result mesh that stay in the section; the nodes keep their
    numbers so the result values and displacements apply unchanged.  Returns (object, set of node numbers)."""
    import Fem

    axis = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
    fem = result.Mesh.FemMesh
    kept = kept_elements(result, mode, axis)
    new = Fem.FemMesh()
    used = set()
    lists = []
    for vid in kept:
        enodes = list(fem.getElementNodes(vid))
        lists.append(enodes)
        used.update(enodes)
    nodes = fem.Nodes
    for n in sorted(used):
        new.addNode(nodes[n].x, nodes[n].y, nodes[n].z, n)
    for enodes in lists:
        new.addVolume(enodes)
    doc = analysis.Document
    name = "SectionMesh_" + analysis.Name
    obj = doc.getObject(name) or doc.addObject("Fem::FemMeshObject", name)
    obj.FemMesh = new
    obj.Label = f"Section ({mode}) of {analysis.Label}"
    return obj, used


def remove_section(analysis):
    doc = analysis.Document
    obj = doc.getObject("SectionMesh_" + analysis.Name)
    if obj is not None:
        doc.removeObject(obj.Name)


# ----- the result as VTK ---------------------------------------------------------------------------------------------

_VTK_MID_PAIRS = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))  # the mid-edge nodes 4..9 of a VTK quadratic tetra


def _tet10_order(fem, xyz_of):
    """The permutation (list of 10 positions in FEM's node list) that gives VTK's quadratic-tetra node order, found by
    where the mid-edge nodes of the first tet10 lie."""
    import numpy as np

    for vid in fem.Volumes:
        nodes = fem.getElementNodes(vid)
        if len(nodes) != 10:
            continue
        p = [np.array(xyz_of[n]) for n in nodes]
        order = [0, 1, 2, 3]
        for i, j in _VTK_MID_PAIRS:
            mid = 0.5 * (p[i] + p[j])
            order.append(min(range(4, 10), key=lambda k: float(np.linalg.norm(p[k] - mid))))
        if sorted(order) != list(range(10)):
            raise PostError("the second-order tetrahedra have an unrecognised node order")
        return order
    return list(range(10))


def vtk_grid(result, arrays=None):
    """A vtkUnstructuredGrid of the result mesh (quadratic or linear tetrahedra, hexahedra) with the node values as point
    data: the displacement, the stress tensor, von Mises, the principal stresses, the safety factor and every
    ``arrays`` entry ({name: list in NodeNumbers order, or (n, 3) / (n, 9)})."""
    import numpy as np
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkUnstructuredGrid

    fem = result.Mesh.FemMesh
    nodes = fem.Nodes
    ids = list(result.NodeNumbers)
    index = {n: i for i, n in enumerate(ids)}
    xyz = np.array([[nodes[n].x, nodes[n].y, nodes[n].z] for n in ids], dtype=float)
    xyz_of = {n: (nodes[n].x, nodes[n].y, nodes[n].z) for n in ids}
    kinds = {4: (10, None), 10: (24, "tet10"), 8: (12, None), 20: (25, None)}
    order10 = _tet10_order(fem, xyz_of)
    types, conn, offsets = [], [], [0]
    for vid in fem.Volumes:
        enodes = fem.getElementNodes(vid)
        if len(enodes) not in kinds:
            raise PostError(f"element type with {len(enodes)} nodes is not exported")
        cell_type, tag = kinds[len(enodes)]
        if tag == "tet10":
            enodes = [enodes[k] for k in order10]
        types.append(cell_type)
        conn.extend(index[n] for n in enodes)
        offsets.append(len(conn))
    points = vtkPoints()
    points.SetData(numpy_to_vtk(xyz, deep=True))
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(np.array(offsets, dtype=np.int64), deep=True),
                  numpy_to_vtkIdTypeArray(np.array(conn, dtype=np.int64), deep=True))
    grid = vtkUnstructuredGrid()
    grid.SetPoints(points)
    grid.SetCells(numpy_to_vtk(np.array(types, dtype=np.uint8), deep=True), cells)
    data = grid.GetPointData()

    def add(name, values):
        arr = np.asarray(values, dtype=np.float64)
        if len(arr) != len(ids):
            return
        vt = numpy_to_vtk(arr, deep=True)
        vt.SetName(name)
        data.AddArray(vt)

    add("NodeID", np.array(ids, dtype=np.float64))
    vectors = list(result.DisplacementVectors)
    if len(vectors) == len(ids):
        add("Displacement", np.array([[v.x, v.y, v.z] for v in vectors]))
    for name, prop in (("DisplacementMagnitude", "DisplacementLengths"), ("vonMises", "vonMises"),
                       ("MaxPrincipal", "PrincipalMax"), ("MinPrincipal", "PrincipalMin"), ("MaxShear", "MaxShear"),
                       ("SafetyFactor", "SafetyFactor")):
        add(name, list(getattr(result, prop, []) or []))
    try:
        t = stress_tensors(result)
        add("Stress", t.reshape(len(ids), 9))
    except PostError:
        pass
    for name, values in (arrays or {}).items():
        add(name, values)
    return grid


def export_vtu(analysis, path, result=None, axis=None):
    """Writes the study's latest (or the given) static result to a .vtu: node values of the displacement, the stress tensor,
    von Mises, principal stresses, the safety factor and -- about the spin axis (or ``axis``) -- the radial, hoop, axial and
    shear stress (``Radial``, ``Hoop``, ``Axial``, ``RTheta``), with the axis as field data.  Returns the path."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridWriter

    result = result or sim.latest_result(analysis)
    if result is None:
        raise PostError("no results yet: solve the study first")
    arrays = {}
    resolved = None
    try:
        resolved = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
        values = cylindrical_stress(result, resolved, analysis)
        arrays = {"Radial": values["radial"], "Hoop": values["hoop"], "Axial": values["axial"], "RTheta": values["shear"]}
    except PostError:
        pass  # a part with no recognisable axis: the other fields still go
    grid = vtk_grid(result, arrays)
    if resolved is not None:
        import numpy as np

        for name, values in (("AxisPoint", resolved[0]), ("AxisDirection", _unit(resolved[1]))):
            vt = numpy_to_vtk(np.array(values, dtype=np.float64), deep=True)
            vt.SetName(name)
            grid.GetFieldData().AddArray(vt)
    for name, text in (("Study", getattr(analysis, "Label", "")), ):
        from vtkmodules.vtkCommonCore import vtkStringArray

        sa = vtkStringArray()
        sa.SetName(name)
        sa.InsertNextValue(str(text))
        grid.GetFieldData().AddArray(sa)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    writer = vtkXMLUnstructuredGridWriter()
    writer.SetFileName(path)
    writer.SetInputData(grid)
    writer.SetDataModeToBinary()
    if not writer.Write():
        raise PostError(f"could not write {path}")
    return path


def read_vtu(path):
    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader

    reader = vtkXMLUnstructuredGridReader()
    reader.SetFileName(path)
    reader.Update()
    return reader.GetOutput()


# ----- radial line plots ---------------------------------------------------------------------------------------------


def radial_profile(result, field="hoop", axis=None, analysis=None, angle=0.0, z=None, count=60, csv=None):
    """The field along a ray out from the axis: [(r_mm, value)] at `count` points, r from 0 to the part's outer radius, at the
    azimuth `angle` degrees from the frame's u direction (the global X across the axis) and the axial position `z` (mm
    along the axis from the axis point; None: the middle of the part).  Points outside the material are left out, values
    are interpolated in the mesh (linear on the corner nodes).  `csv` also writes the columns r_mm and the field."""
    import numpy as np
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkProbeFilter

    key = field_key(field)
    analysis = analysis or sim.study_of(result)
    resolved = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
    values = field_values(result, key, analysis, resolved)
    grid = vtk_grid(result, {"_f": values})
    ids, xyz = node_positions(result)
    point, a, u, v = axis_frame(resolved)
    rel = xyz - point
    along = rel @ a
    radius = np.linalg.norm(rel - np.outer(along, a), axis=1)
    if z is None:
        z = 0.5 * (float(along.min()) + float(along.max()))
    th = math.radians(angle)
    direction = math.cos(th) * u + math.sin(th) * v
    rs = np.linspace(0.0, float(radius.max()), int(count))
    line = point + a * float(z) + np.outer(rs, direction)
    pts = vtkPoints()
    for p in line:
        pts.InsertNextPoint(*p)
    poly = vtkPolyData()
    poly.SetPoints(pts)
    probe = vtkProbeFilter()
    probe.SetInputData(poly)
    probe.SetSourceData(grid)
    probe.Update()
    out = probe.GetOutput()
    from vtkmodules.util.numpy_support import vtk_to_numpy

    got = vtk_to_numpy(out.GetPointData().GetArray("_f"))
    valid = vtk_to_numpy(out.GetPointData().GetArray("vtkValidPointMask")).astype(bool)
    rows = [(float(r), float(g)) for r, g, ok in zip(rs, got, valid) if ok]
    if csv:
        with open(csv, "w", encoding="utf-8") as handle:
            handle.write(f"r_mm,{key}_{FIELDS[key][1] or 'value'}\n")
            for r, g in rows:
                handle.write(f"{r:.6g},{g:.6g}\n")
    return rows


# ----- pictures: pvbatch on the .vtu ------------------------------------------------------------------------------------

VIEWS = ("iso", "axial", "side", "quarter", "half")
PV_TIMEOUT = 300


def _paraview():
    import bruceworx_cfd_paraview as pv

    return pv


def pvbatch_path():
    """The pvbatch of the ParaView app, or None."""
    try:
        return _paraview().paraview_executables()[2]
    except Exception:
        return None


PV_ARRAYS = {"vonMises": "vonMises", "displacement": "DisplacementMagnitude", "radial": "Radial", "hoop": "Hoop",
             "axial": "Axial", "shear": "RTheta", "safety": "SafetyFactor"}


def pv_script(vtu, png, field_name, title, lo, hi, cmap, view, size, axis, profile_png=None, profile_line=None,
              profile_title="", gui=False):
    """The pvbatch script (text) that draws one picture of a .vtu: the field on the surface with a colour bar, the part cut
    by the view ('quarter' removes the wedge u > 0, v > 0 of the axis frame; 'half' the half v > 0), the camera for the
    view.  The colour table is sampled from ``colormap_points`` so ParaView and the 3D view agree."""
    point, a, u, v = axis_frame(axis)
    cfg = {
        "vtu": vtu, "png": png, "field": field_name, "title": title, "range": [lo, hi], "view": view, "size": list(size),
        "point": list(point), "a": list(a), "u": list(u), "v": list(v),
        "rgb": [list(p) for p in colormap_points(cmap)],
        "profile_png": profile_png, "profile_line": profile_line, "profile_title": profile_title, "gui": gui,
    }
    import json

    return "import json\nCONFIG = json.loads(%r)\n" % json.dumps(cfg) + _PV_BODY


_PV_BODY = r'''
from paraview.simple import *
import math
C = CONFIG
reader = XMLUnstructuredGridReader(FileName=[C["vtu"]])
reader.UpdatePipeline()
origin, a, u, v = C["point"], C["a"], C["u"], C["v"]
neg = lambda x: [-c for c in x]

def clip(src, normal, keep_negative):
    f = Clip(Input=src)
    f.ClipType = "Plane"
    f.ClipType.Origin = origin
    f.ClipType.Normal = normal
    f.Invert = 1 if keep_negative else 0
    f.Crinkleclip = 0
    return f

view = C["view"]
if view == "quarter":
    keep = AppendDatasets(Input=[clip(reader, u, True), clip(clip(reader, u, False), v, True)])
elif view == "half":
    keep = clip(reader, v, True)
else:
    keep = reader
rv = GetActiveViewOrCreate("RenderView") if C.get("gui") else CreateView("RenderView")
if not C.get("gui"):
    rv.ViewSize = C["size"]
rv.Background = [1.0, 1.0, 1.0]
try:
    rv.UseColorPaletteForBackground = 0
except Exception:
    pass
rv.OrientationAxesVisibility = 1
disp = Show(keep, rv)
ColorBy(disp, ("POINTS", C["field"]))
lut = GetColorTransferFunction(C["field"])
lo, hi = C["range"]
lut.RGBPoints = [x for p in C["rgb"] for x in (lo + p[0] * (hi - lo), p[1], p[2], p[3])]
lut.ColorSpace = "RGB"
lut.AutomaticRescaleRangeMode = "Never"
lut.NumberOfTableValues = 256
disp.SetScalarBarVisibility(rv, True)
bar = GetScalarBar(lut, rv)
bar.Title = C["title"]
bar.ComponentTitle = ""
bar.TitleColor = [0, 0, 0]
bar.LabelColor = [0, 0, 0]
bar.RangeLabelFormat = "%-#6.4g"
bar.WindowLocation = "Any Location"
bar.Position = [0.86, 0.15]
bar.ScalarBarLength = 0.7
disp.Representation = "Surface"
box = keep.GetDataInformation().GetBounds()
centre = [(box[0] + box[1]) / 2, (box[2] + box[3]) / 2, (box[4] + box[5]) / 2]
diag = math.sqrt((box[1] - box[0]) ** 2 + (box[3] - box[2]) ** 2 + (box[5] - box[4]) ** 2) or 1.0
add = lambda p, q, k: [p[i] + k * q[i] for i in range(3)]
if view == "axial":
    direction, up = neg(a), v
elif view == "side":
    direction, up = neg(u), a
elif view == "quarter":
    d = add(add([0, 0, 0], u, 0.6), v, 0.6)
    direction, up = add(d, a, 0.55), a
elif view == "half":
    direction, up = add(add([0, 0, 0], v, 1.0), u, 0.25), a
    direction = add(direction, a, 0.45)
else:
    d = add(add([0, 0, 0], u, 0.7), v, 0.45)
    direction, up = add(d, a, 0.6), a
n = math.sqrt(sum(c * c for c in direction))
direction = [c / n for c in direction]
rv.CameraFocalPoint = centre
rv.CameraPosition = add(centre, direction, 2.2 * diag)
rv.CameraViewUp = up
rv.CameraViewAngle = 30
rv.ResetCamera(False)
Render(rv)
if not C.get("gui"):
    SaveScreenshot(C["png"], rv, ImageResolution=C["size"], TransparentBackground=0)
if C.get("profile_png"):
    p1, p2 = C["profile_line"]
    pol = PlotOverLine(Input=reader)
    pol.Point1, pol.Point2, pol.Resolution = p1, p2, 120
    cv = CreateView("XYChartView")
    cv.ViewSize = [1000, 600]
    d2 = Show(pol, cv)
    d2.UseIndexForXAxis = 0
    d2.XArrayName = "arc_length"
    d2.SeriesVisibility = [C["field"]]
    d2.SeriesLineThickness = [C["field"], "3"]
    cv.ChartTitle = C["profile_title"]
    cv.LeftAxisTitle = C["title"]
    cv.BottomAxisTitle = "r, mm"
    SaveScreenshot(C["profile_png"], cv, ImageResolution=[1000, 600])
'''


def _run_pvbatch(script, work, log="pvbatch.log"):
    import subprocess

    pvbatch = pvbatch_path()
    if pvbatch is None:
        raise PostError("ParaView (pvbatch) is not installed: the pictures are made by ParaView in the background "
                        "(/Applications/ParaView-*.app); without it, export the .vtu and look at it elsewhere")
    path = os.path.join(work, "view.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    logfile = os.path.join(work, log)
    with open(logfile, "w", encoding="utf-8") as out:
        try:
            code = subprocess.run([pvbatch, "--force-offscreen-rendering", path], stdout=out, stderr=subprocess.STDOUT,
                                  timeout=PV_TIMEOUT, cwd=work).returncode
        except subprocess.TimeoutExpired:
            raise PostError(f"pvbatch did not finish in {PV_TIMEOUT} s (see {logfile})")
    return code, logfile


def contour_png(analysis, field="hoop", axis=None, view="quarter", path=None, range=None, cmap=None, size=(1200, 800),
                result=None, vtu=None):  # noqa: A002 -- `range` is the documented argument name
    """One off-screen picture of a field of the study's latest result, drawn by ParaView's pvbatch on the exported .vtu, so
    it runs in FreeCADCmd (the GUI's own 3D view is not needed).

    field: "vonMises", "displacement", "radial", "hoop", "axial", "shear", "safety" (the aliases of FIELD_ALIASES work).
    axis: None (the spin axis, else the model's bore), ((x, y, z), (dx, dy, dz)), (object, 'Edge3'), 'x' / 'y' / 'z'.
    view: "quarter" (a quarter of the part cut away about the axis), "half", "iso", "axial" (along the axis), "side".
    path: the PNG to write (default: <field>_<view>.png beside the document, else the temp folder).
    range: (min, max) to pin the colour bar, else the study's fixed range for the field, else the data's own range.
    cmap: "Rainbow", "Turbo" or "Jet" (default: the study's, else Rainbow).  size: pixels (w, h).
    Returns the PNG path; the .vtu it used sits beside it (``vtu`` to reuse one already written).
    Raises PostError (never hangs) when ParaView is missing or fails."""
    key = field_key(field)
    if view not in VIEWS:
        raise PostError(f"unknown view {view!r}: use one of {', '.join(VIEWS)}")
    result = result or sim.latest_result(analysis)
    if result is None:
        raise PostError("no results yet: solve the study first")
    resolved = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
    cmap = cmap or study_colormap(analysis)
    if path is None:
        import tempfile

        folder = os.path.dirname(analysis.Document.FileName) if analysis.Document.FileName else tempfile.mkdtemp(prefix="vl-contour-")
        path = os.path.join(folder, f"{key}_{view}.png")
    path = os.path.abspath(path)
    work = os.path.dirname(path)
    os.makedirs(work, exist_ok=True)
    if vtu is None:
        vtu = export_vtu(analysis, os.path.splitext(path)[0] + ".vtu", result, resolved)
    values = field_values(result, key, analysis, resolved)
    pinned = tuple(range) if range else fixed_range(analysis, key)
    lo, hi = pinned if pinned else (min(values), max(values))
    if not hi > lo:
        lo, hi = lo - 0.5 * (abs(lo) or 1.0), hi + 0.5 * (abs(hi) or 1.0)
    label, unit, _prop = FIELDS[key]
    title = f"{label} [{unit}]" if unit else label
    script = pv_script(vtu, path, PV_ARRAYS[key], title, lo, hi, cmap, view, size, resolved)
    code, logfile = _run_pvbatch(script, work, os.path.splitext(os.path.basename(path))[0] + ".log")
    if code or not os.path.isfile(path):
        tail = ""
        try:
            tail = open(logfile, encoding="utf-8", errors="replace").read()[-400:]
        except OSError:
            pass
        raise PostError(f"pvbatch failed (exit {code}): {tail}")
    return path


def radial_plot(analysis, field="hoop", axis=None, angle=0.0, z=None, csv=None, png=None, count=60, result=None):
    """The field along a ray out from the axis (see ``radial_profile``), as the rows [(r_mm, value)], written to `csv` when
    given and drawn to `png` (a line chart, by pvbatch) when given."""
    result = result or sim.latest_result(analysis)
    if result is None:
        raise PostError("no results yet: solve the study first")
    key = field_key(field)
    resolved = resolve_axis(axis, analysis) if axis is not None else default_axis(analysis)
    rows = radial_profile(result, key, resolved, analysis, angle, z, count, csv)
    if png:
        import numpy as np

        png = os.path.abspath(png)
        work = os.path.dirname(png)
        os.makedirs(work, exist_ok=True)
        vtu = export_vtu(analysis, os.path.splitext(png)[0] + ".vtu", result, resolved)
        point, a, u, v = axis_frame(resolved)
        ids, xyz = node_positions(result)
        rel = xyz - point
        along = rel @ a
        zz = 0.5 * (float(along.min()) + float(along.max())) if z is None else float(z)
        rmax = float(np.linalg.norm(rel - np.outer(along, a), axis=1).max())
        th = math.radians(angle)
        direction = math.cos(th) * u + math.sin(th) * v
        p1 = list(point + a * zz)
        p2 = list(point + a * zz + direction * rmax)
        label, unit, _prop = FIELDS[key]
        script = pv_script(vtu, os.path.splitext(png)[0] + "_view.png", PV_ARRAYS[key], f"{label} [{unit}]" if unit else label,
                           0.0, 1.0, "Rainbow", "iso", (400, 300), resolved, png, [p1, p2], f"{label} along r")
        code, logfile = _run_pvbatch(script, work, os.path.splitext(os.path.basename(png))[0] + ".log")
        if code or not os.path.isfile(png):
            raise PostError(f"pvbatch failed drawing the plot (exit {code}; see {logfile})")
    return rows


def write_paraview_script(analysis, vtu, field, path, result=None):
    """A ParaView GUI script (``paraview --script=``) that opens the exported .vtu coloured by `field` with the study's colour
    map and range, cut as the study's section says.  Returns the path."""
    key = field_key(field)
    result = result or sim.latest_result(analysis)
    values = field_values(result, key, analysis)
    pinned = fixed_range(analysis, key)
    lo, hi = pinned or (min(values), max(values))
    if not hi > lo:
        lo, hi = lo - 0.5 * (abs(lo) or 1.0), hi + 0.5 * (abs(hi) or 1.0)
    label, unit, _prop = FIELDS[key]
    section = study_section(analysis)
    script = pv_script(vtu, "", PV_ARRAYS[key], f"{label} [{unit}]" if unit else label, lo, hi, study_colormap(analysis),
                       section if section != "none" else "iso", (1200, 800), default_axis(analysis), gui=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(script)
    return path
