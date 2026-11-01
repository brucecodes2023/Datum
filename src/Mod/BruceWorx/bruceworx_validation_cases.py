# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry and load builders of the SIM-05 validation cases (structural and thermal).

Each builder takes the case's parameters (cases.json) and a mesh size in mm and returns a ``Setup``: the
document, the study's objects, a function that reads the numbers to compare out of the solved study and,
when the model needs something the FreeCAD CalculiX writer cannot write, a patch of the input file.
Nothing binary is stored: every model is made here from the numbers of its source.

Units: the models are in mm, MPa and kg/m^3 (FreeCAD's CalculiX units); the values a builder returns are in the
units of the case's reference (stated there), converted here.

The closed forms behind the derived references (the gap cases, the thick buckling plate) are functions in this
file, so the numbers in cases.json can be regenerated and checked (TestBruceWorxValidation).
"""

import math
import re

import FreeCAD as App

import bruceworx_sim as sim
import bruceworx_sim_eigen as eigen
import bruceworx_sim_thermal as thermal

KELVIN = 273.15


class Setup:
    """A built case: doc, objs (the study's objects), measure(analysis, result) -> {key: value}, patch(inp path)."""

    def __init__(self, doc, objs, measure, patch=None, nonlinear=False, note=""):
        self.doc, self.objs, self.measure, self.patch = doc, objs, measure, patch
        self.analysis = objs["analysis"]
        self.nonlinear = nonlinear
        self.note = note


def material(E=210000.0, nu=0.3, density=7850.0, k=None, cp=None, alpha=12e-6, name="Benchmark"):
    return {"uuid": "", "name": name, "library": "", "E": E, "nu": nu, "yield": 355.0, "uts": None, "density": density,
            "k": k, "cp": cp, "alpha": alpha, "hdt": None}


# ----- small geometry and node helpers ----------------------------------------------------------------------------


def shape_object(doc, name, shape):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    doc.recompute()
    return obj


def faces_where(shape, test):
    return [f"Face{i}" for i, f in enumerate(shape.Faces, 1) if test(f)]


def edges_where(shape, test):
    return [f"Edge{i}" for i, e in enumerate(shape.Edges, 1) if test(e)]


def vertices_where(shape, test):
    return [f"Vertex{i}" for i, v in enumerate(shape.Vertexes, 1) if test(v)]


def plane_at(axis, value, tol=1e-6):
    """Test for a planar face lying in the plane axis (0,1,2) = value."""

    def test(face):
        bb = face.BoundBox
        lengths = (bb.XLength, bb.YLength, bb.ZLength)
        lows = (bb.XMin, bb.YMin, bb.ZMin)
        return lengths[axis] < tol and abs(lows[axis] - value) < tol

    return test


def sphere_of(radius, tol=1e-6):
    def test(face):
        s = face.Surface
        return type(s).__name__ == "Sphere" and abs(s.Radius - radius) < tol

    return test


def cylinder_of(radius, tol=1e-6):
    def test(face):
        s = face.Surface
        return type(s).__name__ == "Cylinder" and abs(s.Radius - radius) < tol

    return test


def only(names, what):
    if len(names) != 1:
        raise ValueError(f"expected one {what}, found {names}")
    return names[0]


def add_displacement(doc, analysis, obj, subs, fixed="xyz", name="Support"):
    """A displacement constraint holding the listed axes at zero on faces/edges/vertices (subs: names)."""
    import ObjectsFem

    c = ObjectsFem.makeConstraintDisplacement(doc, name)
    c.References = [(obj, s) for s in subs]
    for axis in "xyz":
        free = axis not in fixed
        setattr(c, f"{axis}Free", free)
        if not free:
            setattr(c, f"{axis}Displacement", 0)
    analysis.addObject(c)
    return c


def add_pressure(doc, analysis, obj, subs, mpa, name="Pressure", reversed_=False):
    import ObjectsFem

    p = ObjectsFem.makePressure(doc, name) if hasattr(ObjectsFem, "makePressure") else ObjectsFem.makeConstraintPressure(doc, name)
    p.Pressure = f"{mpa} MPa"
    p.Reversed = reversed_
    p.References = [(obj, s) for s in subs]
    analysis.addObject(p)
    return p


def nodal(result, name):
    return dict(zip(result.NodeNumbers, getattr(result, name)))


def positions(result):
    return {n: (v.x, v.y, v.z) for n, v in result.Mesh.FemMesh.Nodes.items()}


def nearest(pos, point):
    px, py, pz = point
    return min(pos, key=lambda n: (pos[n][0] - px) ** 2 + (pos[n][1] - py) ** 2 + (pos[n][2] - pz) ** 2)


def local_fit(rs, values, r0, half_width, degree=3):
    """The value at r0 of a polynomial fitted to (r, value) pairs within half_width of r0 (a radial profile read
    at an exact radius from the nodal values)."""
    import numpy as np

    pick = [(r, v) for r, v in zip(rs, values) if abs(r - r0) <= half_width]
    if len(pick) < degree + 2:
        raise ValueError(f"only {len(pick)} nodes within {half_width} of r = {r0}")
    x = np.array([p[0] - r0 for p in pick])
    y = np.array([p[1] for p in pick])
    return float(np.polyfit(x, y, degree)[-1])


def merge_cloads(text):
    """Sums repeated (node, dof) entries of the *CLOAD blocks into one block (loads of neighbouring edges share
    their end nodes; CalculiX would let the last entry win)."""
    totals, order, out, mode, first = {}, [], [], None, None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("*") and not stripped.startswith("**"):
            mode = stripped.split(",")[0].strip().upper() == "*CLOAD"
            if mode:
                if first is None:
                    first = len(out)
                    out.append(line)  # the one header kept; the block is written here at the end
                continue
            out.append(line)
            continue
        if mode and stripped.startswith("**"):
            continue
        if mode and stripped:
            f = [x.strip() for x in stripped.split(",")]
            key = (f[0], f[1])
            if key not in totals:
                order.append(key)
                totals[key] = 0.0
            totals[key] += float(f[2])
            continue
        out.append(line)
    if first is not None:
        block = [f"{n},{d},{totals[(n, d)]:.13G}" for n, d in order]
        out[first + 1:first + 1] = block
    return "\n".join(out)


def patch_file(path, fn):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    text = fn(text)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# ----- B-ST1: NAFEMS LE10 --------------------------------------------------------------------------------------------


def le10_shape(t=600.0):
    """The quadrant x, y >= 0 of NAFEMS LE10's thick elliptic plate: the outer ellipse (x/3.25)^2 + (y/2.75)^2 = 1 m,
    the inner (x/2)^2 + y^2 = 1 m, z from 0 to t = 0.6 m. Built as two stacked slabs fused without refining, so the
    outer face is split at the mid-plane z = t/2 and the edge B''C'' of the constraint exists."""
    import Part

    V = App.Vector

    def slab(z0, h):
        out = Part.Face(Part.Wire(Part.Ellipse(V(0, 0, 0), 3250.0, 2750.0).toShape())).extrude(V(0, 0, h))
        inn = Part.Face(Part.Wire(Part.Ellipse(V(0, 0, 0), 2000.0, 1000.0).toShape())).extrude(V(0, 0, h))
        s = out.cut(inn).common(Part.makeBox(4000.0, 4000.0, h))
        s.translate(V(0, 0, z0))
        return s

    return slab(0.0, t / 2).fuse(slab(t / 2, t / 2))


def build_le10(params, size):
    """The pressure 1 MPa on the top face; uy = 0 on the plane y = 0 (DCD'C'), ux = 0 on x = 0 (ABA'B'), ux = uy = 0 on
    the outer ellipse (BCB'C'), uz = 0 on the outer ellipse's mid-plane edge (B''C''). Reads sigma_yy at D (2, 0, 0.6) m."""
    doc = App.newDocument("LE10")
    t = params["thickness_mm"]
    shape = le10_shape(t)
    plate = shape_object(doc, "Plate", shape)
    mat = material(params["E_MPa"], params["nu"], 7850.0, name="NAFEMS LE10")
    objs = sim.new_study(doc, sim.StudyPlan(name="LE10", model=plate, material=mat, max_size=size))
    an = objs["analysis"]
    outer = faces_where(shape, lambda f: type(f.Surface).__name__ != "Plane" and f.BoundBox.XMax > 3000.0)
    if len(outer) != 2:
        raise ValueError(f"LE10 shape: outer faces {outer}")
    add_displacement(doc, an, plate, faces_where(shape, plane_at(1, 0.0)), "y", "SymmetryY0")
    add_displacement(doc, an, plate, faces_where(shape, plane_at(0, 0.0)), "x", "SymmetryX0")
    if params.get("outer_fixed", "xy"):
        add_displacement(doc, an, plate, outer, params.get("outer_fixed", "xy"), "OuterEllipse")
    mid = edges_where(shape, lambda e: abs(e.BoundBox.ZMin - t / 2) < 1e-6 and e.BoundBox.ZLength < 1e-6 and e.BoundBox.XMax > 3000.0
                       and type(e.Curve).__name__ != "Line")
    add_displacement(doc, an, plate, mid, "z", "MidPlaneEdge")
    add_pressure(doc, an, plate, faces_where(shape, plane_at(2, t)), params["pressure_MPa"], "Top pressure",
                 reversed_=bool(params.get("reversed", False)))
    doc.recompute()
    D = (2000.0, 0.0, t)

    def measure(analysis, result):
        pos = positions(result)
        node = nearest(pos, D)
        dz = dict(zip(result.NodeNumbers, result.DisplacementVectors))[node].z
        return {"syy_D": nodal(result, "NodeStressYY")[node], "uz_D": dz, "node": list(pos[node])}

    return Setup(doc, objs, measure)


# ----- B-ST2: Code_Aster SSLV07 --------------------------------------------------------------------------------------


def build_sslv07(params, size):
    """A 1 x 1 x 3 m prism hanging from point A, under its weight and the uniform traction rho g L on its top face;
    the quadrant 0 <= x, y <= 0.5 m with the symmetry planes x = 0, y = 0 (the displacements u = -nu rho g x z / E
    vanish on them). z runs from the free end B (0) to the top A (3 m)."""
    doc = App.newDocument("SSLV07")
    a, L = params["half_width_mm"], params["height_mm"]
    import ObjectsFem
    import Part

    box = doc.addObject("Part::Box", "Prism")
    box.Length, box.Width, box.Height = a, a, L
    doc.recompute()
    shape = box.Shape
    mat = material(params["E_MPa"], params["nu"], params["density"], name="SSLV07 steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="SSLV07", model=box, material=mat, max_size=size))
    an = objs["analysis"]
    add_displacement(doc, an, box, faces_where(shape, plane_at(0, 0.0)), "x", "SymmetryX0")
    add_displacement(doc, an, box, faces_where(shape, plane_at(1, 0.0)), "y", "SymmetryY0")
    A = vertices_where(shape, lambda v: abs(v.X) < 1e-6 and abs(v.Y) < 1e-6 and abs(v.Z - L) < 1e-6)
    add_displacement(doc, an, box, A, "z", "PointA")
    g = ObjectsFem.makeConstraintSelfWeight(doc, "Weight")
    g.GravityAcceleration = f"{params['g'] * 1000.0} mm/s^2"  # the manual's g = 9.81 (FreeCAD's default is 9.80665)
    an.addObject(g)
    top = params["density"] * params["g"] * L * 1e-3 * 1e-6  # Pa -> MPa: rho g L with L in m
    add_pressure(doc, an, box, faces_where(shape, plane_at(2, L)), top, "Top traction", reversed_=True)
    doc.recompute()
    pts = {"B": (0, 0, 0), "C": (a, 0, 0), "D": (a, 0, L), "E": (0, 0, L / 2)}

    def measure(analysis, result):
        pos = positions(result)
        disp = dict(zip(result.NodeNumbers, result.DisplacementVectors))
        out = {}
        for name, key, comp in (("B", "wB", "z"), ("C", "wC", "z"), ("D", "uD", "x"), ("D", "wD", "z"), ("E", "wE", "z")):
            v = disp[nearest(pos, pts[name])]
            out[key] = getattr(v, comp) * 1e-3  # mm -> m
        out["szzE"] = nodal(result, "NodeStressZZ")[nearest(pos, pts["E"])] * 1e6  # MPa -> Pa
        return out

    return Setup(doc, objs, measure)


# ----- B-ST3: Code_Aster SSLV301 -------------------------------------------------------------------------------------


def build_sslv301(params, size):
    """The cantilever cylinder as a 3D solid: R = 6 mm, L = 240 mm along z, clamped at z = 0; the line load q(z) =
    3000 N/m (L - z)/L in -x on the generator at theta = 0, applied as `segments` uniform edge forces (the mean of q
    over each segment; the resultant and its moment arm are exact to O(1/segments^2)). Reads u_x at C = (R, 0, L)
    and sigma_zz at B = (R, 0, 0) (and at 2R from the clamp, for the record)."""
    import ObjectsFem
    import Part

    V = App.Vector
    doc = App.newDocument("SSLV301")
    R, L, n = params["radius_mm"], params["length_mm"], int(params["segments"])
    h = L / n
    pieces = []
    for i in range(n):  # two half discs per segment: the vertices (+-R, 0, z) exist, and so does the generator at theta = 0
        for angle in (0.0, 180.0):
            half = Part.makeCylinder(R, h, V(0, 0, i * h), V(0, 0, 1), 180.0)
            half.rotate(V(0, 0, 0), V(0, 0, 1), angle)
            pieces.append(half)
    shape = pieces[0].multiFuse(pieces[1:])
    beam = shape_object(doc, "Shaft", shape)
    mat = material(params["E_MPa"], params["nu"], 7850.0, name="SSLV301 steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="SSLV301", model=beam, material=mat, max_size=size))
    an = objs["analysis"]
    root = faces_where(shape, plane_at(2, 0.0))
    if params.get("clamp", "plane") == "full":
        add_displacement(doc, an, beam, root, "xyz", "Clamp")
    else:
        # the root plane stays plane (uz = 0: no rotation) but may contract; the rigid motions are removed at two points
        add_displacement(doc, an, beam, root, "z", "RootPlane")
        add_displacement(doc, an, beam, vertices_where(shape, lambda v: abs(v.X + R) < 1e-6 and abs(v.Y) < 1e-6 and abs(v.Z) < 1e-6), "xy", "PointMinusR")
        add_displacement(doc, an, beam, vertices_where(shape, lambda v: abs(v.X - R) < 1e-6 and abs(v.Y) < 1e-6 and abs(v.Z) < 1e-6), "y", "PointB")
    line = doc.addObject("Part::Line", "DirectionX")
    line.X1, line.Y1, line.Z1, line.X2, line.Y2, line.Z2 = 0, 0, 0, 100, 0, 0
    doc.recompute()
    seams = []
    for i, e in enumerate(shape.Edges, 1):
        bb = e.BoundBox
        if bb.XLength < 1e-6 and bb.YLength < 1e-6 and abs(bb.XMin - R) < 1e-6 and abs(bb.YMin) < 1e-6 and bb.ZLength > 1e-6:
            seams.append((bb.ZMin, bb.ZMax, f"Edge{i}"))
    seams.sort()
    if len(seams) != n:
        raise ValueError(f"SSLV301: {len(seams)} seam edges for {n} segments")
    q0 = params["q0_N_per_m"] * 1e-3  # N/mm
    for z0, z1, name in seams:
        zm = (z0 + z1) / 2
        force = q0 * (L - zm) / L * (z1 - z0)
        f = ObjectsFem.makeConstraintForce(doc, f"Line{name}")
        f.References = [(beam, name)]
        f.Force = f"{force} N"
        f.Direction = (line, ["Edge1"])
        f.Reversed = True  # towards -x
        an.addObject(f)
    doc.recompute()
    C, B, B2 = (R, 0.0, L), (R, 0.0, 0.0), (R, 0.0, 2 * R)

    def measure(analysis, result):
        pos = positions(result)
        disp = dict(zip(result.NodeNumbers, result.DisplacementVectors))
        szz = nodal(result, "NodeStressZZ")
        return {"urC": disp[nearest(pos, C)].x * 1e-3, "szzB": szz[nearest(pos, B)] * 1e6,
                "szz_2R": szz[nearest(pos, B2)] * 1e6}

    return Setup(doc, objs, measure, patch=lambda path: patch_file(path, merge_cloads))


def sslv301_theory(R=6.0, L=240.0, E=210000.0, q0=3.0):
    """Beam theory for the triangular load (q0 at the clamp to 0 at the tip), mm, N, MPa: the tip deflection
    q0 L^4 / (30 E I) and the root stress M c / I with M = q0 L^2 / 6. The manual's 1.552e-3 m and 169.8 MPa."""
    I = math.pi * R ** 4 / 4
    return {"tip_m": q0 * L ** 4 / (30 * E * I) * 1e-3, "root_Pa": q0 * L ** 2 / 6 * R / I * 1e6}


# ----- B-MO1: NAFEMS FV52 -------------------------------------------------------------------------------------------


def build_fv52(params, size):
    """The 10 x 10 x 1 m plate held in z along its four edges at the height `support_z_mm` (the plate is two slabs
    fused without refining, so that the side faces carry an edge at mid-height). The first three modes are rigid (x, y,
    rotation about z); the seven elastic ones are compared."""
    import Part

    V = App.Vector
    doc = App.newDocument("FV52")
    a, t = params["side_mm"], params["thickness_mm"]
    zs = params["support_z_mm"]
    if zs < 1e-9:  # supported along the bottom edges: the plain box
        shape = Part.makeBox(a, a, t)
    else:  # two slabs fused without refining, so the side faces carry an edge at the support height
        shape = Part.makeBox(a, a, zs).fuse(Part.makeBox(a, a, t - zs, V(0, 0, zs)))
    plate = shape_object(doc, "Plate", shape)
    mat = material(params["E_MPa"], params["nu"], params["density"], name="FV52 steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="FV52", model=plate, material=mat, max_size=size, study_type="modal",
                                            options={"ModeCount": 13}))
    an = objs["analysis"]
    edges = edges_where(shape, lambda e: abs(e.BoundBox.ZMin - zs) < 1e-6 and e.BoundBox.ZLength < 1e-6

                        and (abs(e.BoundBox.XMin) < 1e-6 or abs(e.BoundBox.XMax - a) < 1e-6
                             or abs(e.BoundBox.YMin) < 1e-6 or abs(e.BoundBox.YMax - a) < 1e-6))
    if len(edges) != 4:
        raise ValueError(f"FV52 support edges: {edges}")
    if params.get("support") == "faces":
        side = faces_where(shape, lambda f: type(f.Surface).__name__ == "Plane" and f.BoundBox.ZLength > 1.0
                           and (f.BoundBox.XLength < 1e-6 or f.BoundBox.YLength < 1e-6))
        add_displacement(doc, an, plate, side, "z", "SupportFaces")
    else:
        add_displacement(doc, an, plate, edges, "z", "SupportEdges")
    doc.recompute()
    return Setup(doc, objs, lambda analysis, result: elastic_frequencies(analysis, int(params["modes"])))


def elastic_frequencies(analysis, count):
    rows = [r for r in eigen.modal_rows(analysis) if not r["rigid"]]
    f = sorted(r["frequency"] for r in rows)[:count]
    if len(f) < count:
        raise ValueError(f"only {len(f)} elastic modes found, {count} asked")
    return {f"f{i}": v for i, v in enumerate(f, 1)}


# ----- B-MO2: Code_Aster SDLV401 ------------------------------------------------------------------------------------


def build_sdlv401(params, size):
    """The free solid sphere R = 10 mm, E = 100 MPa (1e8 Pa), nu = 0.3, rho = 1e4 kg/m^3: its six rigid modes are
    skipped, the next ten frequencies are compared."""
    import Part

    doc = App.newDocument("SDLV401")
    sph = shape_object(doc, "Sphere", Part.makeSphere(params["radius_mm"]))
    mat = material(params["E_MPa"], params["nu"], params["density"], name="SDLV401")
    objs = sim.new_study(doc, sim.StudyPlan(name="SDLV401", model=sph, material=mat, max_size=size, study_type="modal",
                                            options={"ModeCount": int(params["modes"])}))
    return Setup(doc, objs, lambda analysis, result: elastic_frequencies(analysis, int(params["modes"])))


# ----- B-MO3: Code_Aster SDLV100 ------------------------------------------------------------------------------------


def all_nodes_uz(text):
    """Holds uz = 0 at every node (the manual's DDL_IMPO on all nodes) in the model definition."""
    card = "** BruceWorx validation: DZ = 0 at every node (Code_Aster SDLV100)\n*BOUNDARY\nNall,3,3\n"
    out, n = re.subn(r"^\*STEP", card + "*STEP", text, count=1, flags=re.M)
    if not n:
        raise ValueError("no *STEP in the input")
    return out


def build_sdlv100(params, size):
    """The tapered cantilever beam: 1 m long along x, a square section 40 mm at the clamp to 10 mm at the tip,
    steel E = 2e11 Pa, rho = 7800 (the manual gives no Poisson ratio; 0.3, which the SAMCEF frequencies imply, see
    cases.json). uz = 0 at every node as in the manual, so the bending modes are those of the xy plane."""
    import Part

    V = App.Vector
    doc = App.newDocument("SDLV100")
    L, h0, h1 = params["length_mm"], params["h0_mm"], params["h1_mm"]

    def wire(x, h):
        p = [V(x, -h / 2, -h / 2), V(x, h / 2, -h / 2), V(x, h / 2, h / 2), V(x, -h / 2, h / 2)]
        return Part.makePolygon(p + [p[0]])

    solid = Part.makeLoft([wire(0.0, h0), wire(L, h1)], True, True)
    beam = shape_object(doc, "Beam", solid)
    mat = material(params["E_MPa"], params["nu"], params["density"], name="SDLV100 steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="SDLV100", model=beam, material=mat, max_size=size, study_type="modal",
                                            options={"ModeCount": int(params["modes"]) + 2}))
    an = objs["analysis"]
    add_displacement(doc, an, beam, faces_where(solid, plane_at(0, 0.0)), "xyz", "Clamp")
    doc.recompute()
    n = int(params["modes"])
    return Setup(doc, objs, lambda analysis, result: elastic_frequencies(analysis, n),
                 patch=lambda path: patch_file(path, all_nodes_uz))


# ----- B-BU1: Code_Aster SSLV139, a thicker plate --------------------------------------------------------------------


def bessel_j(n, x, terms=40):
    """J_n(x) by its power series (n = 0 or 1; x below about 10)."""
    total, term = 0.0, (x / 2.0) ** n / math.factorial(n)
    for m in range(terms):
        total += term
        term *= -(x / 2.0) ** 2 / ((m + 1) * (m + 1 + n))
    return total


def circular_plate_k2(edge="simple", nu=0.3):
    """k^2 in F_cr = k^2 D / R^2 (thin plate, axisymmetric mode n = 0, uniform radial compression F per unit rim length).
    With w = J_0(k r) + C and k^2 = F / D:
      clamped (w = 0, w' = 0 at r = R):                    J_1(kR) = 0                      -> k^2 = 14.68 = 3.8317^2
      simply supported (w = 0, M_r = w'' + nu w'/r = 0):   kR J_0(kR) = (1 - nu) J_1(kR)    -> k^2 = 4.20 for nu = 0.3
    The clamped root reproduces the manual's coefficient 14.68 (Timoshenko and Gere)."""
    f = (lambda x: bessel_j(1, x)) if edge == "clamped" else (lambda x: x * bessel_j(0, x) - (1.0 - nu) * bessel_j(1, x))
    lo, hi = (3.0, 4.5) if edge == "clamped" else (1.0, 3.0)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    x = 0.5 * (lo + hi)
    return x * x


def circular_plate_fcr(radius, thickness, E, nu, edge="simple", shear=True, kappa=5.0 / 6.0):
    """Critical line load F_cr (N per unit length of the rim) of a circular plate under uniform radial compression,
    axisymmetric mode.

    The manual (SSLV139; Timoshenko and Gere) has the plate clamped (rotation held, radial motion free) and gives
    F_cr = 14.68 D / R^2, D = E h^3 / (12 (1 - nu^2)): 2668.315 N/m for h = 0.5 mm, R = 0.115 m. A 3D solid cannot hold
    the rim's rotation with the radial motion free (its rim face can turn by moving radially), so the plate here has a
    simply supported rim (uz = 0 on the rim face, the rim free to turn): the same derivation with the other boundary
    condition, k^2 = 4.20 for nu = 0.3 (circular_plate_k2).
    The plate is thick (R / h = 12) so that tetrahedra can mesh it, and its shear flexibility lowers the load. For a
    first-order shear (Mindlin) plate the critical load is the thin-plate one reduced by
    F = F_thin / (1 + F_thin / (kappa G h)),  kappa = 5/6, G = E / (2 (1 + nu))   (the exact relation of Wang, Xiang and
    Kitipornchai / Reddy between the classical and the first-order shear buckling loads of a circular plate), about 1 %
    here.
    """
    D = E * thickness ** 3 / (12.0 * (1.0 - nu ** 2))
    thin = circular_plate_k2(edge, nu) * D / radius ** 2
    if not shear:
        return thin
    G = E / (2.0 * (1.0 + nu))
    return thin / (1.0 + thin / (kappa * G * thickness))


def build_sslv139(params, size):
    """A quarter of the circular plate: the rim pressed with 1 MPa (a line load of 1 MPa x h), uz = 0 on the rim face
    (zero rotation and no deflection), symmetry on x = 0 and y = 0. The first buckling factor times 1 MPa x h is F_cr."""
    import Part

    doc = App.newDocument("SSLV139")
    R, h = params["radius_mm"], params["thickness_mm"]
    quarter = Part.makeCylinder(R, h).common(Part.makeBox(R, R, h))
    plate = shape_object(doc, "Plate", quarter)
    mat = material(params["E_MPa"], params["nu"], 7850.0, name="SSLV139")
    objs = sim.new_study(doc, sim.StudyPlan(name="SSLV139", model=plate, material=mat, max_size=size, study_type="buckling",
                                            options={"ModeCount": 3}))
    an = objs["analysis"]
    rim = faces_where(quarter, cylinder_of(R))
    add_displacement(doc, an, plate, rim, "z", "RimNoDeflection")
    add_displacement(doc, an, plate, faces_where(quarter, plane_at(0, 0.0)), "x", "SymmetryX0")
    add_displacement(doc, an, plate, faces_where(quarter, plane_at(1, 0.0)), "y", "SymmetryY0")
    add_pressure(doc, an, plate, rim, 1.0, "Rim pressure")
    doc.recompute()

    def measure(analysis, result):
        lam = eigen.first_factor(analysis)
        if lam is None:
            raise ValueError("no positive buckling factor")
        return {"Fcr": lam * 1.0 * h}  # N/mm of rim

    return Setup(doc, objs, measure)


# ----- B-TH1: Code_Aster TPLV06 -------------------------------------------------------------------------------------


def hollow_sphere_temperature(r, Ri=1.0, Re=2.0, Q=100.0, k=1.0, T0=20.0):
    """The closed form of the manual (Ti = Te = T0): T = T0 + Q/(6k) [(Re^2 - Ri^2)(1/Ri - 1/r)/(1/Ri - 1/Re) - (r^2 - Ri^2)]."""
    return T0 + Q / (6.0 * k) * ((Re ** 2 - Ri ** 2) * (1.0 / Ri - 1.0 / r) / (1.0 / Ri - 1.0 / Re) - (r ** 2 - Ri ** 2))


def octant(radius, inner=0.0):
    import Part

    s = Part.makeSphere(radius)
    if inner:
        s = s.cut(Part.makeSphere(inner))
    return s.common(Part.makeBox(radius, radius, radius))


def build_tplv06(params, size):
    """An octant of the hollow sphere (Ri = 1 m, Re = 2 m), both spherical faces at 20 deg C, heat generation Q =
    100 W/m^3 as the total power Q x V of the octant; the cut planes are adiabatic. Reads the radial profile at 1.25,
    1.5, 1.75 m (a cubic fit of the nodal temperatures within 0.15 m, evaluated at the radius)."""
    doc = App.newDocument("TPLV06")
    Ri, Re = params["Ri_mm"], params["Re_mm"]
    shape = octant(Re, Ri)
    body = shape_object(doc, "Sphere", shape)
    mat = material(200000.0, 0.3, 7850.0, k=params["k"], cp=1000.0, name="TPLV06")
    objs = sim.new_study(doc, sim.StudyPlan(name="TPLV06", model=body, material=mat, max_size=size, study_type="thermal"))
    an = objs["analysis"]
    thermal.add_temperature(an, [(body, n) for n in faces_where(shape, sphere_of(Re))], params["T0"], "Outer face")
    thermal.add_temperature(an, [(body, n) for n in faces_where(shape, sphere_of(Ri))], params["T0"], "Inner face")
    watts = params["Q_W_m3"] * shape.Volume * 1e-9
    thermal.add_heat_source(an, body, watts, "Heat generation")
    doc.recompute()

    def measure(analysis, result):
        pos = positions(result)
        rs = [math.sqrt(sum(c * c for c in pos[n])) for n in result.NodeNumbers]
        temps = list(result.TempC)
        return {f"T_{r:g}": local_fit([x * 1e-3 for x in rs], temps, r, 0.15, 3) for r in params["radii_m"]}

    return Setup(doc, objs, measure)


# ----- B-TH2: Code_Aster TPLV304 ------------------------------------------------------------------------------------


def build_tplv304(params, size):
    """The square bar b = 25.4 mm, L = 203.2 mm: y = 0 at 37.78 deg C, y = L insulated, the four sides in air (h =
    5.678 W/m^2 K, -17.78 deg C). Reads the mean temperature of the end face y = L."""
    doc = App.newDocument("TPLV304")
    b, L = params["b_mm"], params["L_mm"]
    box = doc.addObject("Part::Box", "Bar")
    box.Length, box.Width, box.Height = b, L, b
    doc.recompute()
    shape = box.Shape
    mat = material(200000.0, 0.3, 7850.0, k=params["k"], cp=500.0, name="TPLV304")
    objs = sim.new_study(doc, sim.StudyPlan(name="TPLV304", model=box, material=mat, max_size=size, study_type="thermal"))
    an = objs["analysis"]
    thermal.add_temperature(an, [(box, "Face3")], params["Tw"], "Hot end y = 0")
    thermal.add_convection(an, [(box, n) for n in ("Face1", "Face2", "Face5", "Face6")], params["h"], params["Ta"], "Sides in air")
    doc.recompute()

    def measure(analysis, result):
        pos = positions(result)
        end = [t for n, t in zip(result.NodeNumbers, result.TempC) if abs(pos[n][1] - L) < 1e-6]
        return {"T_end": sum(end) / len(end), "T_end_min": min(end), "T_end_max": max(end)}

    return Setup(doc, objs, measure)


# ----- B-TT1: Code_Aster TTLV01 -------------------------------------------------------------------------------------


def sphere_cooling_series(times, radius, k, cp_vol, h, Te, T0, terms=80):
    """The exact series solution of a sphere with convection (centre, surface) in deg C, for the cross-check of the
    manual's chart readings: theta = sum C_n exp(-zeta_n^2 Fo) [sin(zeta_n r/R)/(zeta_n r/R)], zeta_n cot zeta_n = 1 - Bi."""
    bi = h * radius / k
    alpha = k / cp_vol
    zs = []
    for n in range(1, terms + 1):
        a, b = (n - 1) * math.pi + 1e-9, n * math.pi - 1e-9
        f = lambda z: 1.0 - z / math.tan(z) - bi
        for _ in range(100):
            m = 0.5 * (a + b)
            if f(a) * f(m) <= 0:
                b = m
            else:
                a = m
        zs.append(0.5 * (a + b))
    cs = [4 * (math.sin(z) - z * math.cos(z)) / (2 * z - math.sin(2 * z)) for z in zs]
    out = []
    for t in times:
        fo = alpha * t / radius ** 2
        centre = sum(c * math.exp(-z * z * fo) for c, z in zip(cs, zs))
        surface = sum(c * math.exp(-z * z * fo) * math.sin(z) / z for c, z in zip(cs, zs))
        out.append((Te + (T0 - Te) * centre, Te + (T0 - Te) * surface))
    return out


def build_ttlv01(params, size):
    """An octant of the sphere (the diameter D = 0.2 m, confirmed in cases.json), 20 deg C at the start, convection on
    the spherical face (232.5 W/m^2 K, 1000 deg C), the cut planes adiabatic. Reads the centre (the coolest node) and
    the surface (the hottest) at the times of the manual."""
    doc = App.newDocument("TTLV01")
    R = params["diameter_mm"] / 2.0
    shape = octant(R)
    body = shape_object(doc, "Sphere", shape)
    mat = material(200000.0, 0.3, params["rho"], k=params["k"], cp=params["cp"], name="TTLV01")
    options = {"Transient": True, "EndTime": params["end_s"], "TimeStep": params["step_s"], "InitialTemperature": params["T0"]}
    objs = sim.new_study(doc, sim.StudyPlan(name="TTLV01", model=body, material=mat, max_size=size, study_type="thermal",
                                            options=options))
    an = objs["analysis"]
    thermal.add_convection(an, [(body, n) for n in faces_where(shape, sphere_of(R))], params["h"], params["Te"], "Convection")
    doc.recompute()

    def measure(analysis, result):
        data = thermal.stored(analysis)
        out = {}
        for t in params["times_s"]:
            i = min(range(len(data["times"])), key=lambda j: abs(data["times"][j] - t))
            if abs(data["times"][i] - t) > 0.5:
                raise ValueError(f"no result at t = {t} s (times {data['times'][:5]}...)")
            out[f"Tc_{t:g}"] = data["min"][i]
            out[f"Ts_{t:g}"] = data["max"][i]
        return out

    return Setup(doc, objs, measure)


# ----- gap: thick cylinder with a radial temperature difference ------------------------------------------------------


def lame_thermal(r, a, b, E, nu, alpha, dT):
    """Stresses (sigma_r, sigma_theta, sigma_z) of a long hollow cylinder with free ends, inner radius a at the
    temperature dT above the outer radius b, steady radial conduction T(r) = dT ln(b/r) / ln(b/a)
    (Timoshenko and Goodier, Theory of Elasticity, thermal stress in a long hollow cylinder):

      sigma_r     = c [ -ln(b/r) - a^2/(b^2 - a^2) (1 - b^2/r^2) ln(b/a) ]
      sigma_theta = c [ 1 - ln(b/r) - a^2/(b^2 - a^2) (1 + b^2/r^2) ln(b/a) ]
      sigma_z     = c [ 1 - 2 ln(b/r) - 2 a^2/(b^2 - a^2) ln(b/a) ]
      c = alpha E dT / (2 (1 - nu) ln(b/a))

    They satisfy sigma_r(a) = sigma_r(b) = 0 and, integrated over the section, no net axial force (checked in
    TestBruceWorxValidation)."""
    lb = math.log(b / r)
    lab = math.log(b / a)
    c = alpha * E * dT / (2.0 * (1.0 - nu) * lab)
    k = a * a / (b * b - a * a) * lab
    return (c * (-lb - a * a / (b * b - a * a) * (1.0 - b * b / (r * r)) * lab),
            c * (1.0 - lb - k * (1.0 + b * b / (r * r))),
            c * (1.0 - 2.0 * lb - 2.0 * k))


def build_thick_cylinder(params, size):
    """A quarter of a thick cylinder (a = 50, b = 100 mm), half of its length (symmetry plane z = 0), the ends free:
    the inner face held at T_ref + dT, the outer at T_ref. The mid-plane stresses, hoop and axial, are read through a
    cubic fit over the radius and compared at both surfaces."""
    import Part

    doc = App.newDocument("ThickCylinder")
    a, b, Lh = params["a_mm"], params["b_mm"], params["half_length_mm"]
    solid = Part.makeCylinder(b, Lh).cut(Part.makeCylinder(a, Lh)).common(Part.makeBox(b, b, Lh))
    body = shape_object(doc, "Cylinder", solid)
    mat = material(params["E_MPa"], params["nu"], 7850.0, k=50.0, cp=480.0, alpha=params["alpha"], name="Steel")
    t_ref, dT = params["T_ref"], params["dT"]
    objs = sim.new_study(doc, sim.StudyPlan(name="Thick cylinder", model=body, material=mat, max_size=size,
                                            study_type="thermal_stress",
                                            options={"ReferenceTemperature": t_ref, "InitialTemperature": t_ref}))
    an = objs["analysis"]
    thermal.add_temperature(an, [(body, n) for n in faces_where(solid, cylinder_of(a))], t_ref + dT, "Bore")
    thermal.add_temperature(an, [(body, n) for n in faces_where(solid, cylinder_of(b))], t_ref, "Outside")
    add_displacement(doc, an, body, faces_where(solid, plane_at(0, 0.0)), "x", "SymmetryX0")
    add_displacement(doc, an, body, faces_where(solid, plane_at(1, 0.0)), "y", "SymmetryY0")
    add_displacement(doc, an, body, faces_where(solid, plane_at(2, 0.0)), "z", "MidPlane")
    doc.recompute()

    def measure(analysis, result):
        pos = positions(result)
        nodes = [n for n in result.NodeNumbers if abs(pos[n][2]) < 1e-6]
        sxx, syy, sxy, szz = (nodal(result, k) for k in ("NodeStressXX", "NodeStressYY", "NodeStressXY", "NodeStressZZ"))
        rs, hoop, radial, axial = [], [], [], []
        for n in nodes:
            x, y, _z = pos[n]
            r = math.hypot(x, y)
            if r < 1e-9:
                continue
            c, s = x / r, y / r
            rs.append(r)
            radial.append(sxx[n] * c * c + syy[n] * s * s + 2 * sxy[n] * s * c)
            hoop.append(sxx[n] * s * s + syy[n] * c * c - 2 * sxy[n] * s * c)
            axial.append(szz[n])
        w = 0.35 * (b - a)
        return {"shoop_a": local_fit(rs, hoop, a, w), "shoop_b": local_fit(rs, hoop, b, w),
                "sz_a": local_fit(rs, axial, a, w), "sz_b": local_fit(rs, axial, b, w)}

    return Setup(doc, objs, measure)


# ----- gap: a beam under axial tension (prestressed modal) -----------------------------------------------------------


def build_tensioned_beam(params, size):
    """The pinned-pinned beam under axial tension T (eigen.tensioned_beam): omega_n^2 = omega_n0^2 (1 + T L^2 /
    (n^2 pi^2 E I)). The four lowest modes are the first and second bending mode in each plane."""
    doc, objs, theory = eigen.tensioned_beam(None, params["length_mm"], params["width_mm"], params["tension_N"], size)
    tension, L, w = params["tension_N"], params["length_mm"], params["width_mm"]
    E = eigen.STEEL["E"]
    I = w ** 4 / 12.0
    # the plan's form, checked against eigen.tensioned_frequency (they are the same expression)
    f0 = [eigen.tensioned_frequency(n, L, w, E, eigen.STEEL["density"], 0.0) for n in (1, 2)]
    for n, f in zip((1, 2), f0):
        loaded = f * math.sqrt(1.0 + tension * L * L / (n * n * math.pi ** 2 * E * I))
        assert abs(loaded - eigen.tensioned_frequency(n, L, w, E, eigen.STEEL["density"], tension)) < 1e-6 * loaded

    def measure(analysis, result):
        f = sorted(r["frequency"] for r in eigen.modal_rows(analysis) if not r["rigid"])
        return {"f1": f[0], "f1b": f[1], "f2": f[2], "f2b": f[3]}

    return Setup(doc, objs, measure)


def tensioned_references(length, width, tension):
    E, rho = eigen.STEEL["E"], eigen.STEEL["density"]
    return {"f1": eigen.tensioned_frequency(1, length, width, E, rho, tension),
            "f2": eigen.tensioned_frequency(2, length, width, E, rho, tension)}


# ----- gap: a column with an initial bow (nonlinear buckling, Southwell) --------------------------------------------


def southwell(factors, displacements, lambda1, low=0.3, high=0.85):
    """(lambda_cr, intercept): the Southwell plot of a bowed column. With the bow a0 in the shape of the buckling
    mode, the extra deflection at load factor lam is  d = a0 (lam/lcr) / (1 - lam/lcr), i.e. d / lam = (a0 + d) / lcr:
    a straight line of d / lam against d with the slope 1 / lcr. Fitted by least squares over the points with
    low < lam / lambda1 < high (the range where the bow still behaves as the linear amplification)."""
    pts = [(abs(d), lam) for d, lam in zip(displacements, factors) if low < lam / lambda1 < high and abs(d) > 0]
    if len(pts) < 4:
        raise ValueError(f"only {len(pts)} points of the curve between {low} and {high} of lambda_1")
    xs = [d for d, _ in pts]
    ys = [d / lam for d, lam in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return 1.0 / slope, my - slope * mx


def build_bowed_column(params, size):
    """The cantilever column (eigen.column) with a nonlinear run: the bow is mode 1 at `bow_fraction` of the section.
    Reads the Southwell critical factor and the amplification at lam = lambda_1 / 2 (= a0 exactly for the linear form)."""
    doc, objs, theory = eigen.column(None, params["length_mm"], params["width_mm"], params["load_N"], size,
                                     {"Nonlinear": True})

    def measure(analysis, result):
        lam1 = eigen.first_factor(analysis)
        curve = eigen.nonlinear_data(eigen.first_buckling_result(analysis))
        if not curve:
            raise ValueError("the nonlinear run left no curve")
        lcr, c = southwell(curve["factors"], curve["displacements"], lam1)
        return {"lambda_southwell": lcr, "lambda1_linear": lam1, "bow_mm": curve["amplitude"], "intercept_mm": c * lcr}

    return Setup(doc, objs, measure, nonlinear=True)


# ----- gap: a rotating annular disc (SIM-04) -----------------------------------------------------------------------


def disc_references(a, b, rpm, rho, nu):
    """Bore and rim hoop stress and the radial and hoop stress at mid-radius of a thin rotating annulus, MPa
    (bruceworx_sim_post.rotating_disc, lengths in mm, density in kg/m^3)."""
    import bruceworx_sim_post as post

    omega = rpm * 2.0 * math.pi / 60.0
    mid = 0.5 * (a + b)
    at = lambda r: post.rotating_disc(r / 1000.0, a / 1000.0, b / 1000.0, rho, omega, nu)  # noqa: E731 -- Pa
    return {"shoop_a": at(a)[1] / 1e6, "shoop_b": at(b)[1] / 1e6, "sr_mid": at(mid)[0] / 1e6, "shoop_mid": at(mid)[1] / 1e6}


def build_rotating_disc(params, size):
    """A quarter of a thin steel annulus (a = 10, b = 50 mm, 2 mm thick, so plane stress to a fraction of a percent) with
    symmetry planes on x = 0, y = 0 and the mid-plane, spinning at 30 000 rpm about Z (a spin load, CalculiX
    *DLOAD CENTRIF).  The cylindrical stress components about the spin axis are averaged over the nodes on the bore and
    the rim, and read at mid-radius through a cubic fit over the radius on the mid-plane."""
    import Part

    import bruceworx_sim_post as post

    doc = App.newDocument("RotatingDisc")
    a, b, t = params["a_mm"], params["b_mm"], params["thickness_mm"]
    half = t / 2.0
    solid = Part.makeCylinder(b, half).cut(Part.makeCylinder(a, half)).common(Part.makeBox(b, b, half))
    body = shape_object(doc, "Disc", solid)
    mat = material(params["E_MPa"], params["nu"], params["density"], name="Steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="Rotating disc", model=body, material=mat, max_size=size))
    an = objs["analysis"]
    add_displacement(doc, an, body, faces_where(solid, plane_at(0, 0.0)), "x", "SymmetryX0")
    add_displacement(doc, an, body, faces_where(solid, plane_at(1, 0.0)), "y", "SymmetryY0")
    add_displacement(doc, an, body, faces_where(solid, plane_at(2, 0.0)), "z", "MidPlane")
    post.add_spin(an, params["rpm"], axis=((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
    doc.recompute()

    def measure(analysis, result):
        values = post.cylindrical_stress(result, analysis=analysis)
        pos = positions(result)
        ids = list(result.NodeNumbers)
        rs = [values["r"][i] for i in range(len(ids))]
        bore = [values["hoop"][i] for i in range(len(ids)) if abs(rs[i] - a) < 1e-4]
        rim = [values["hoop"][i] for i in range(len(ids)) if abs(rs[i] - b) < 1e-4]
        mid_plane = [i for i, n in enumerate(ids) if abs(pos[n][2]) < 1e-6]
        w = 0.35 * (b - a)
        r0 = 0.5 * (a + b)
        return {"shoop_a": sum(bore) / len(bore), "shoop_b": sum(rim) / len(rim),
                "sr_mid": local_fit([rs[i] for i in mid_plane], [values["radial"][i] for i in mid_plane], r0, w),
                "shoop_mid": local_fit([rs[i] for i in mid_plane], [values["hoop"][i] for i in mid_plane], r0, w)}

    return Setup(doc, objs, measure)


def build_rotating_disc_full(params, size):
    """G-RD2: the whole thin annulus of G-RD1 (no symmetry planes) held only by the ``hold_for_spin`` point set, a pure
    vertex 3-2-1 (A: both directions across the axis, B: the tangential direction, the axial direction on a ring at A's
    level).  A balanced rotor has no reaction at the points, so the bore and rim hoop stress are the free disc's."""
    import Part

    import bruceworx_sim_post as post

    doc = App.newDocument("RotatingDiscFull")
    a, b, t = params["a_mm"], params["b_mm"], params["thickness_mm"]
    solid = Part.makeCylinder(b, t, App.Vector(0, 0, -t / 2.0)).cut(Part.makeCylinder(a, t, App.Vector(0, 0, -t / 2.0)))
    body = shape_object(doc, "Disc", solid)
    mat = material(params["E_MPa"], params["nu"], params["density"], name="Steel")
    objs = sim.new_study(doc, sim.StudyPlan(name="Rotating disc, whole", model=body, material=mat, max_size=size))
    an = objs["analysis"]
    post.add_spin(an, params["rpm"], axis=((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
    post.hold_for_spin(an)
    doc.recompute()

    def measure(analysis, result):
        values = post.cylindrical_stress(result, analysis=analysis)
        rs = values["r"]
        bore = [h for h, r in zip(values["hoop"], rs) if abs(r - a) < 1e-4]
        rim = [h for h, r in zip(values["hoop"], rs) if abs(r - b) < 1e-4]
        return {"shoop_a": sum(bore) / len(bore), "shoop_b": sum(rim) / len(rim)}

    return Setup(doc, objs, measure)


BUILDERS = {
    "le10": build_le10, "sslv07": build_sslv07, "sslv301": build_sslv301, "fv52": build_fv52,
    "sdlv401": build_sdlv401, "sdlv100": build_sdlv100, "sslv139": build_sslv139, "tplv06": build_tplv06,
    "tplv304": build_tplv304, "ttlv01": build_ttlv01, "thick_cylinder": build_thick_cylinder,
    "tensioned_beam": build_tensioned_beam, "bowed_column": build_bowed_column, "rotating_disc": build_rotating_disc,
    "rotating_disc_full": build_rotating_disc_full,
}
