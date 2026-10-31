# SPDX-License-Identifier: LGPL-2.1-or-later
"""Airflow studies with OpenFOAM (SIM-06 phase 1, 31 Oct 2026; plan chapter 67).

Headless core, no FreeCAD import at module level (the shape export and the study glue import it lazily):

* detection: ``find_openfoam`` looks for OpenFOAM-v2606.app (the tested version) in /Applications, ~/Applications
  or the preference ``Mod/BruceWorx/Sim/OpenFOAMPath``; any other version gets a warning; when none is installed
  the pre-check offers ``Install OpenFOAM...`` (the page of the openfoam-app project, or the Homebrew command).
  Nothing is ever installed from here.
* ``air_properties``: standard atmosphere (pressure from the altitude, density from the study's temperature,
  viscosity from Sutherland's law).
* ``write_case``: the OpenFOAM case of an external-flow study from triangulated parts (metres): STL per part,
  blockMesh background box sized from the bounding box, snappyHexMesh with the coarse/medium/fine presets,
  simpleFoam with k-omega SST or laminar, inlet velocity / pressure outlet / slip or symmetry far field / wall
  functions, forces and forceCoeffs per part plus the inlet and outlet flow rate, and an ``Allrun`` script.
  The geometry is rotated by the angle of attack about its Y axis so the free stream is always +X:
  drag is along +X and lift along +Z of the flow frame.
* ``Runner``: runs ``Allrun`` inside the OpenFOAM environment in the background (own process group, Stop kills
  the group), ``LogReader`` follows the residuals and forces live, ``convergence`` applies the plan's rule
  (residuals < 1e-4, forces change < 0.5 % over the last 200 iterations, flow in = flow out).
* ``precheck``: watertight parts, cell-count estimate, y+ estimate, Reynolds number and the turbulence advice.
* benchmark cases: ``write_pipe_case`` (laminar pipe, axisymmetric wedge) and ``sphere_triangles``.

Units: the case is in SI (m, s, kg); FreeCAD models are in mm and are scaled on export.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time

APP_NAME = "OpenFOAM-v2606.app"
TESTED_VERSION = "v2606"
INSTALL_URL = "https://github.com/gerlero/openfoam-app"
INSTALL_COMMAND = "brew install gerlero/openfoam/openfoam@2606"
PARAVIEW_APPS = ("/Applications/ParaView.app", os.path.expanduser("~/Applications/ParaView.app"))
SIM_PREFS = "User parameter:BaseApp/Preferences/Mod/BruceWorx/Sim"
PREF_PATH = "OpenFOAMPath"
CACHE_ROOT = os.path.expanduser("~/Library/Caches/Vector Labs/cfd")

CONVERGED_RESIDUAL = 1.0e-4
FORCE_WINDOW = 200  # iterations
MIN_WINDOW = 50  # a run stopped early by its residuals is judged on at least this many iterations
CONTROL_MARGIN = 0.1  # the solver stops at residual * margin, so the reported residuals sit well under the limit
FORCE_CHANGE = 0.005  # 0.5 %
FLOW_BALANCE = 0.005

# name -> (cells across the characteristic length in the background mesh, minimum and maximum surface level,
# level of the refinement box, boundary layers, iterations).  Surface cell = background / 2**level.
PRESETS = {
    "coarse": {"base_div": 2.0, "surf": (4, 4), "box": 3, "layers": 2, "iterations": 1500, "label": "Coarse (about 0.3 M cells)"},
    "medium": {"base_div": 3.0, "surf": (5, 5), "box": 4, "layers": 3, "iterations": 2500, "label": "Medium (about 1 M cells)"},
    "fine": {"base_div": 4.0, "surf": (6, 6), "box": 5, "layers": 4, "iterations": 4000, "label": "Fine (about 3 M cells)"},
}
DEFAULT_PRESET = "coarse"
DOMAIN_FACTORS = {"upstream": 5.0, "downstream": 10.0, "lateral": 5.0}  # in characteristic lengths from the bounding box
MAX_CELLS = {"coarse": 1_000_000, "medium": 3_000_000, "fine": 8_000_000}

R_AIR = 287.05
SUTHERLAND = (1.716e-5, 273.15, 110.4)


class CfdError(Exception):
    """An airflow study cannot go on: the message says what is wrong."""


# ----- OpenFOAM detection ------------------------------------------------------------------------------


def _pref_path():
    try:
        import FreeCAD

        return FreeCAD.ParamGet(SIM_PREFS).GetString(PREF_PATH, "")
    except Exception:
        return ""


def _app_candidates(pref=None):
    out = []
    for path in (pref if pref is not None else _pref_path(), ):
        if path:
            out.append(path)
    out += [os.path.join("/Applications", APP_NAME), os.path.join(os.path.expanduser("~/Applications"), APP_NAME)]
    # a different version beside it (warn, but use it)
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        out += sorted(glob.glob(os.path.join(base, "OpenFOAM-v*.app")), reverse=True)
    seen = []
    for path in out:
        if path not in seen:
            seen.append(path)
    return seen


def find_openfoam(pref=None):
    """The installed OpenFOAM as {"app", "launcher", "version", "tested", "warning"}, or None.

    ``pref`` overrides the preference path (tests); a path may be the .app or the launcher script itself.
    """
    for path in _app_candidates(pref):
        path = os.path.expanduser(path)
        app = path
        launcher = os.path.join(app, "Contents", "Resources", "etc", "openfoam")
        if os.path.isfile(path) and os.access(path, os.X_OK):  # the launcher itself
            launcher = path
            app = os.path.abspath(os.path.join(path, "..", "..", "..", ".."))
        if not (os.path.isfile(launcher) and os.access(launcher, os.X_OK)):
            continue
        m = re.search(r"OpenFOAM-(v\d+)", os.path.basename(app))
        version = m.group(1) if m else "unknown"
        tested = version == TESTED_VERSION
        warning = "" if tested else (f"OpenFOAM {version} is installed; Vector Labs is tested with OpenFOAM "
                                     f"{TESTED_VERSION}. Results may differ or the case may not run.")
        return {"app": app, "launcher": launcher, "version": version, "tested": tested, "warning": warning}
    return None


def install_text():
    return (f"OpenFOAM {TESTED_VERSION} is not installed. Download OpenFOAM-v2606.app from {INSTALL_URL} "
            f"or run:  {INSTALL_COMMAND}")


def install_openfoam_action():
    """The 'Install OpenFOAM...' action: opens the project page. Returns the text to show beside it."""
    try:
        import webbrowser

        webbrowser.open(INSTALL_URL)
    except Exception:
        pass
    return install_text()


def paraview_app():
    for path in PARAVIEW_APPS:
        if os.path.isdir(path):
            return path
    return None


def perf_cores():
    """Physical performance cores (Apple silicon), else all physical cores."""
    for key in ("hw.perflevel0.physicalcpu", "hw.physicalcpu", "hw.ncpu"):
        try:
            out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out)
        except Exception:
            pass
    return max(1, os.cpu_count() or 1)


# ----- air ---------------------------------------------------------------------------------------------


def air_properties(temperature_c=20.0, altitude_m=0.0):
    """Standard atmosphere pressure at the altitude, density at the given temperature, Sutherland viscosity."""
    t = temperature_c + 273.15
    h = float(altitude_m)
    p = 101325.0 * (1.0 - 0.0065 * h / 288.15) ** 5.25588 if h < 11000.0 else 22632.0
    rho = p / (R_AIR * t)
    mu0, t0, s = SUTHERLAND
    mu = mu0 * (t / t0) ** 1.5 * (t0 + s) / (t + s)
    return {"T": t, "p": p, "rho": rho, "mu": mu, "nu": mu / rho, "a": math.sqrt(1.4 * R_AIR * t)}


# ----- triangles and STL -----------------------------------------------------------------------------


def sphere_triangles(radius=0.5, n_lat=48, n_lon=96, center=(0.0, 0.0, 0.0)):
    """A UV sphere as a list of triangles ((x, y, z) * 3), outward normals."""
    cx, cy, cz = center
    pts = []
    for i in range(n_lat + 1):
        th = math.pi * i / n_lat
        row = []
        for j in range(n_lon):
            ph = 2 * math.pi * j / n_lon
            row.append((cx + radius * math.sin(th) * math.cos(ph), cy + radius * math.sin(th) * math.sin(ph),
                        cz + radius * math.cos(th)))
        pts.append(row)
    tris = []
    for i in range(n_lat):
        for j in range(n_lon):
            a, b = pts[i][j], pts[i][(j + 1) % n_lon]
            c, d = pts[i + 1][j], pts[i + 1][(j + 1) % n_lon]
            if i > 0:
                tris.append((a, c, b))
            if i < n_lat - 1:
                tris.append((b, c, d))
    return tris


def box_triangles(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


def write_stl(path, name, tris):
    def normal(t):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        m = math.sqrt(sum(c * c for c in n)) or 1.0
        return tuple(c / m for c in n)

    with open(path, "w") as f:
        f.write(f"solid {name}\n")
        for t in tris:
            n = normal(t)
            f.write(f" facet normal {n[0]:.6g} {n[1]:.6g} {n[2]:.6g}\n  outer loop\n")
            for p in t:
                f.write(f"   vertex {p[0]:.9g} {p[1]:.9g} {p[2]:.9g}\n")
            f.write("  endloop\n endfacet\n")
        f.write(f"endsolid {name}\n")


def read_stl(path):
    """Triangles of an ASCII STL file (all solids)."""
    tris, cur = [], []
    with open(path) as f:
        for line in f:
            s = line.split()
            if s and s[0] == "vertex":
                cur.append((float(s[1]), float(s[2]), float(s[3])))
                if len(cur) == 3:
                    tris.append(tuple(cur))
                    cur = []
    return tris


def bounds(tris):
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    if not xs:
        raise CfdError("a part has no triangles")
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def surface_area(tris):
    a = 0.0
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tris:
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        a += 0.5 * math.sqrt((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2)
    return a


def volume(tris):
    v = 0.0
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in tris:
        v += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6.0
    return v


def open_edges(tris, digits=7):
    """The number of edges that do not have exactly two triangles (0 = watertight, consistent)."""
    count = {}

    def key(p):
        return (round(p[0], digits), round(p[1], digits), round(p[2], digits))

    for t in tris:
        k = [key(p) for p in t]
        for i in range(3):
            a, b = k[i], k[(i + 1) % 3]
            if a == b:
                continue
            e = (a, b) if a < b else (b, a)
            d = 1 if a < b else -1
            c = count.setdefault(e, [0, 0])
            c[0] += 1
            c[1] += d
    # a closed, consistently oriented mesh: two faces per edge with opposite directions
    return sum(1 for c in count.values() if c[0] != 2 or c[1] != 0)


def rotate_y(tris, angle_deg, about):
    """Rotates about the Y axis through ``about`` so that a flow (cos a, 0, sin a) becomes +X."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy, cz = about

    def rot(p):
        x, y, z = p[0] - cx, p[1] - cy, p[2] - cz
        return (cx + x * ca + z * sa, p[1], cz - x * sa + z * ca)

    return [tuple(rot(p) for p in t) for t in tris]


class Part:
    """One body of the study: a name and its triangles in metres (model frame)."""

    def __init__(self, name, tris):
        self.name = name
        self.tris = list(tris)
        self.label = name


def safe_name(text, taken=()):
    base = re.sub(r"[^0-9A-Za-z_]", "_", str(text)).strip("_") or "part"
    if base[0].isdigit():
        base = "p_" + base
    name, i = base, 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    return name


# ----- the setup -------------------------------------------------------------------------------------


class Setup:
    """What an external-flow study asks for (SI units)."""

    def __init__(self, speed=10.0, aoa=0.0, temperature_c=20.0, altitude_m=0.0, preset=DEFAULT_PRESET,
                 turbulence="kOmegaSST", a_ref=None, l_ref=None, farfield="slip", factors=None,
                 intensity=0.01, iterations=None, layers=None, n_procs=None, surf=None, box=None, base_div=None,
                 cell_limit=None):
        self.speed = float(speed)
        self.aoa = float(aoa)
        self.temperature_c = float(temperature_c)
        self.altitude_m = float(altitude_m)
        self.preset = preset
        self.turbulence = turbulence  # "kOmegaSST" or "laminar"
        self.a_ref = a_ref  # m^2; None = frontal area of the bounding box
        self.l_ref = l_ref  # m; None = bounding box length along the flow
        self.farfield = farfield  # "slip" or "symmetry"
        self.factors = dict(DOMAIN_FACTORS, **(factors or {}))
        self.intensity = intensity
        self.iterations = iterations
        self.layers = layers
        self.n_procs = n_procs
        # overrides of the preset (tests, expert use)
        self.surf = surf
        self.box = box
        self.base_div = base_div
        self.cell_limit = cell_limit

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data):
        s = cls()
        s.__dict__.update(data)
        return s

    def preset_values(self):
        if self.preset not in PRESETS:
            raise CfdError(f"unknown mesh preset {self.preset!r} (coarse, medium or fine)")
        p = dict(PRESETS[self.preset])
        if self.surf is not None:
            p["surf"] = tuple(self.surf)
        if self.box is not None:
            p["box"] = self.box
        if self.base_div is not None:
            p["base_div"] = self.base_div
        if self.layers is not None:
            p["layers"] = self.layers
        if self.iterations is not None:
            p["iterations"] = self.iterations
        return p


def geometry(parts, setup):
    """Sizes derived from the parts' bounding box (flow frame, metres)."""
    all_tris = [t for p in parts for t in p.tris]
    lo, hi = bounds(all_tris)
    c = tuple((a + b) / 2 for a, b in zip(lo, hi))
    if setup.aoa:
        lo, hi = bounds(rotate_y(all_tris, setup.aoa, c))
    ext = tuple(b - a for a, b in zip(lo, hi))
    big = max(ext)
    if big <= 0:
        raise CfdError("the parts have no size")
    f = setup.factors
    dom_lo = (lo[0] - f["upstream"] * big, c[1] - (ext[1] / 2 + f["lateral"] * big), c[2] - (ext[2] / 2 + f["lateral"] * big))
    dom_hi = (hi[0] + f["downstream"] * big, c[1] + (ext[1] / 2 + f["lateral"] * big), c[2] + (ext[2] / 2 + f["lateral"] * big))
    pv = setup.preset_values()
    dx0 = big / pv["base_div"]
    cells = tuple(max(1, int(round((b - a) / dx0))) for a, b in zip(dom_lo, dom_hi))
    dx = tuple((b - a) / n for a, b, n in zip(dom_lo, dom_hi, cells))
    return {
        "bbox": (lo, hi), "center": c, "extent": ext, "length": big, "domain": (dom_lo, dom_hi), "cells": cells,
        "dx0": max(dx), "dx": dx,
        "a_ref": setup.a_ref or max(ext[1] * ext[2], 1e-12), "l_ref": setup.l_ref or ext[0] or big,
    }


def estimate(parts, setup, geo=None):
    """Cell count and y+ estimates (the plan's pre-check numbers)."""
    geo = geo or geometry(parts, setup)
    pv = setup.preset_values()
    air = air_properties(setup.temperature_c, setup.altitude_m)
    nbg = geo["cells"][0] * geo["cells"][1] * geo["cells"][2]
    area = sum(surface_area(p.tris) for p in parts)
    lvl_max = pv["surf"][1]
    dxs = geo["dx0"] / 2 ** lvl_max
    n_surf = 4.0 * area / dxs ** 2  # bands of 3 cells per level add up to about 4 cells per surface cell
    lo, hi = geo["bbox"]
    L = geo["length"]
    dxb = geo["dx0"] / 2 ** pv["box"]
    bv = (hi[0] - lo[0] + 4.0 * L) * (hi[1] - lo[1] + 1.0 * L) * (hi[2] - lo[2] + 1.0 * L)
    n_box = bv / dxb ** 3
    n_total = int(nbg + n_surf + n_box)
    # first cell height: half the surface cell, or half the first layer when layers are added
    layers = pv["layers"]
    if layers:
        r = 1.2
        t_last = 0.4 * dxs
        first = t_last / r ** (layers - 1)
    else:
        first = dxs
    y1 = first / 2
    re_l = setup.speed * geo["length"] / air["nu"]
    cf = 0.058 * max(re_l, 1.0) ** -0.2
    u_tau = setup.speed * math.sqrt(cf / 2)
    yplus = y1 * u_tau / air["nu"]
    return {"cells": n_total, "background": nbg, "surface_cell_m": dxs, "first_cell_m": y1, "yplus": yplus,
            "reynolds": re_l, "area": area}


# ----- dictionaries ------------------------------------------------------------------------------------

HEADER = """/*--------------------------------*- C++ -*----------------------------------*\\
  Written by Vector Labs (SIM-06). OpenFOAM v2606.
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

"""


def _w(case, rel, body, cls="dictionary", header=True):
    path = os.path.join(case, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    obj = os.path.basename(path)
    with open(path, "w") as f:
        if header:
            f.write(HEADER.format(cls=cls, obj=obj))
        f.write(body.strip("\n") + "\n\n// ************************************************************************* //\n")
    return path


def _vec(v):
    return "(" + " ".join(f"{x:.9g}" for x in v) + ")"


def control_dict(setup, parts, geo, air, iterations, names, groups, refs):
    funcs = []
    for part in parts:
        n = names[part.name]
        grp = groups[part.name]
        funcs.append(f"""
    forces_{n}
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        log             no;
        patches         ({grp});
        rho             rhoInf;
        rhoInf          {air['rho']:.9g};
        CofR            {_vec(geo['center'])};
    }}
    forceCoeffs_{n}
    {{
        type            forceCoeffs;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        log             no;
        patches         ({grp});
        rho             rhoInf;
        rhoInf          {air['rho']:.9g};
        liftDir         (0 0 1);
        dragDir         (1 0 0);
        CofR            {_vec(geo['center'])};
        pitchAxis       (0 1 0);
        magUInf         {setup.speed:.9g};
        lRef            {refs['l_ref']:.9g};
        Aref            {refs['a_ref']:.9g};
    }}""")
    for patch in ("inlet", "outlet"):
        funcs.append(f"""
    flow_{patch}
    {{
        type            surfaceFieldValue;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   1;
        log             no;
        writeFields     no;
        regionType      patch;
        name            {patch};
        operation       sum;
        fields          (phi);
    }}""")
    return f"""
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {iterations};
deltaT          1;
writeControl    timeStep;
writeInterval   {max(100, iterations // 4)};
purgeWrite      2;
writeFormat     binary;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{{''.join(funcs)}
}}
"""


def fv_schemes(turb):
    div_t = "" if turb == "laminar" else """
    turbulence      bounded Gauss upwind;
    div(phi,k)      $turbulence;
    div(phi,omega)  $turbulence;"""
    return f"""
ddtSchemes {{ default steadyState; }}
gradSchemes
{{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
}}
divSchemes
{{
    default         none;
    div(phi,U)      bounded Gauss linearUpwindV grad(U);{div_t}
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}
laplacianSchemes {{ default Gauss linear corrected; }}
interpolationSchemes {{ default linear; }}
snGradSchemes {{ default corrected; }}
wallDist {{ method meshWave; }}
"""


def fv_solution(turb, residual=CONVERGED_RESIDUAL * CONTROL_MARGIN, p_residual=None):
    p_residual = residual if p_residual is None else p_residual
    turb_solvers = "" if turb == "laminar" else """
    "(k|omega)"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-9;
        relTol          0.1;
        nSweeps         1;
    }"""
    res_turb = "" if turb == "laminar" else f'\n        "(k|omega)" {residual:g};'
    relax_turb = "" if turb == "laminar" else "\n        \"(k|omega)\" 0.7;"
    return f"""
solvers
{{
    p
    {{
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-9;
        relTol          0.01;
    }}
    Phi {{ $p; }}
    U
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-9;
        relTol          0.1;
        nSweeps         1;
    }}{turb_solvers}
}}
SIMPLE
{{
    nNonOrthogonalCorrectors 1;
    consistent      yes;
    residualControl
    {{
        p               {p_residual:g};
        U               {residual:g};{res_turb}
    }}
}}
potentialFlow {{ nNonOrthogonalCorrectors 10; }}
relaxationFactors
{{
    equations
    {{
        U               0.9;{relax_turb}
    }}
}}
cache {{ grad(U); }}
"""


def decompose_dict(n):
    return f"numberOfSubdomains {n};\nmethod scotch;\n"


def block_mesh_dict(lo, hi, cells):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    nx, ny, nz = cells
    return f"""
scale 1;
vertices
(
    ({x0:.9g} {y0:.9g} {z0:.9g}) ({x1:.9g} {y0:.9g} {z0:.9g}) ({x1:.9g} {y1:.9g} {z0:.9g}) ({x0:.9g} {y1:.9g} {z0:.9g})
    ({x0:.9g} {y0:.9g} {z1:.9g}) ({x1:.9g} {y0:.9g} {z1:.9g}) ({x1:.9g} {y1:.9g} {z1:.9g}) ({x0:.9g} {y1:.9g} {z1:.9g})
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1) );
edges ();
boundary
(
    inlet   {{ type patch; faces ((0 4 7 3)); }}
    outlet  {{ type patch; faces ((1 2 6 5)); }}
    ymin    {{ type patch; faces ((0 1 5 4)); }}
    ymax    {{ type patch; faces ((3 7 6 2)); }}
    zmin    {{ type patch; faces ((0 3 2 1)); }}
    zmax    {{ type patch; faces ((4 5 6 7)); }}
);
mergePatchPairs ();
"""


def snappy_dict(setup, parts, geo, names, groups, pv):
    lo, hi = geo["bbox"]
    L = geo["length"]
    dom_lo, dom_hi = geo["domain"]
    rlo = (lo[0] - 1.0 * L, lo[1] - 0.5 * L, lo[2] - 0.5 * L)
    rhi = (hi[0] + 3.0 * L, hi[1] + 0.5 * L, hi[2] + 0.5 * L)
    rlo = tuple(max(a, b + 0.01 * L) for a, b in zip(rlo, dom_lo))
    rhi = tuple(min(a, b - 0.01 * L) for a, b in zip(rhi, dom_hi))
    geom, surfs = [], []
    for part in parts:
        n = names[part.name]
        geom.append(f"    {n}\n    {{\n        type triSurfaceMesh;\n        file \"{n}.stl\";\n        name {n};\n    }}")
        surfs.append(f"""        {n}
        {{
            level ({pv['surf'][0]} {pv['surf'][1]});
            patchInfo {{ type wall; inGroups ({groups[part.name]}); }}
        }}""")
    # a point in the fluid: upstream of the bounding box, a little off the axes, never on a cell face
    dx = geo["dx0"]
    loc = (0.5 * (dom_lo[0] + lo[0]) + 0.0137 * dx, geo["center"][1] + 0.0113 * dx, geo["center"][2] + 0.0171 * dx)
    layers = pv["layers"]
    layer_block = ""
    n_layers = layers
    layer_block = f"""
    layers {{ ".*" {{ nSurfaceLayers {n_layers}; }} }}""" if layers else "    layers {}"
    return f"""
castellatedMesh true;
snap            true;
addLayers       {'true' if layers else 'false'};
geometry
{{
{chr(10).join(geom)}
    refinementBox
    {{
        type box;
        min {_vec(rlo)};
        max {_vec(rhi)};
    }}
}}
castellatedMeshControls
{{
    maxLocalCells 1000000;
    maxGlobalCells {int(setup.cell_limit or MAX_CELLS[setup.preset] * 1.5)};
    minRefinementCells 10;
    maxLoadUnbalance 0.10;
    nCellsBetweenLevels 3;
    features ();
    refinementSurfaces
    {{
{chr(10).join(surfs)}
    }}
    resolveFeatureAngle 30;
    refinementRegions
    {{
        refinementBox {{ mode inside; levels ((1E15 {pv['box']})); }}
    }}
    locationInMesh {_vec(loc)};
    allowFreeStandingZoneFaces true;
}}
snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap true;
    explicitFeatureSnap false;
    multiRegionFeatureSnap false;
}}
addLayersControls
{{
    relativeSizes true;
{layer_block}
    expansionRatio 1.2;
    finalLayerThickness 0.4;
    minThickness 0.1;
    nGrow 0;
    featureAngle 60;
    slipFeatureAngle 30;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedialAxisAngle 90;
    nBufferCellsNoExtrude 0;
    nLayerIter 50;
}}
meshQualityControls
{{
    #includeEtc "caseDicts/meshQualityDict"
    minFaceWeight 0.02;
    nSmoothScale 4;
    errorReduction 0.75;
}}
writeFlags ();
mergeTolerance 1e-6;
"""


def _field(cls, obj, dims, internal, boundary):
    return HEADER.format(cls=cls, obj=obj) + f"""dimensions      {dims};
internalField   {internal};
boundaryField
{{
{boundary}
}}

// ************************************************************************* //
"""


def _patch_block(name, body):
    return f"    {name}\n    {{\n" + "".join(f"        {line}\n" for line in body) + "    }"


def boundary_fields(setup, groups, air):
    """0/U, p, k, omega, nut as text by field name."""
    sides = ("ymin", "ymax", "zmin", "zmax")
    turb = setup.turbulence != "laminar"
    v = setup.speed
    k = 1.5 * (v * setup.intensity) ** 2
    omega = max(math.sqrt(k) / (0.09 ** 0.25 * 0.07 * max(setup.l_ref or 1.0, 1e-6)), 1e-3)
    out = {}
    wall_groups = list(groups.values())
    far_type = "symmetryPlane" if setup.farfield == "symmetry" else None

    def build(inlet, outlet, far, wall):
        blocks = [_patch_block("inlet", inlet), _patch_block("outlet", outlet)]
        for s in sides:
            blocks.append(_patch_block(s, far if far_type is None else [f"type {far_type};"]))
        for g in wall_groups:
            blocks.append(_patch_block(g, wall))
        return "\n".join(blocks)

    out["U"] = _field("volVectorField", "U", "[0 1 -1 0 0 0 0]", "uniform (%.9g 0 0)" % v, build(
        ["type fixedValue;", "value uniform (%.9g 0 0);" % v],
        ["type inletOutlet;", "inletValue uniform (0 0 0);", "value uniform (%.9g 0 0);" % v],
        ["type slip;"], ["type noSlip;"]))
    out["p"] = _field("volScalarField", "p", "[0 2 -2 0 0 0 0]", "uniform 0", build(
        ["type zeroGradient;"], ["type fixedValue;", "value uniform 0;"], ["type zeroGradient;"], ["type zeroGradient;"]))
    if turb:
        out["k"] = _field("volScalarField", "k", "[0 2 -2 0 0 0 0]", f"uniform {k:.9g}", build(
            ["type fixedValue;", f"value uniform {k:.9g};"],
            ["type inletOutlet;", f"inletValue uniform {k:.9g};", f"value uniform {k:.9g};"],
            ["type zeroGradient;"], ["type kqRWallFunction;", f"value uniform {k:.9g};"]))
        out["omega"] = _field("volScalarField", "omega", "[0 0 -1 0 0 0 0]", f"uniform {omega:.9g}", build(
            ["type fixedValue;", f"value uniform {omega:.9g};"],
            ["type inletOutlet;", f"inletValue uniform {omega:.9g};", f"value uniform {omega:.9g};"],
            ["type zeroGradient;"], ["type omegaWallFunction;", f"value uniform {omega:.9g};"]))
        out["nut"] = _field("volScalarField", "nut", "[0 2 -1 0 0 0 0]", "uniform 0", build(
            ["type calculated;", "value uniform 0;"], ["type calculated;", "value uniform 0;"],
            ["type calculated;", "value uniform 0;"], ["type nutkWallFunction;", "value uniform 0;"]))
    return out


def momentum_properties(turb, nu):
    if turb == "laminar":
        turb_body = "simulationType laminar;\n"
    else:
        turb_body = "simulationType RAS;\nRAS\n{\n    RASModel kOmegaSST;\n    turbulence on;\n    printCoeffs on;\n}\n"
    return {"turbulenceProperties": turb_body,
            "transportProperties": f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {nu:.9g};\n"}


ALLRUN = """#!/bin/sh
# Written by Vector Labs. Run inside the OpenFOAM environment (openfoam -c ./Allrun) from this folder.
cd "$(dirname "$0")" || exit 1
stage() {{ echo "@@ stage: $1"; }}
fail() {{ echo "@@ failed: $1"; exit 1; }}
{body}
echo "@@ done"
"""


def allrun_text(n_procs, meshing=True, extra_post=""):
    lines = []
    if meshing:
        lines += ['stage blockMesh', 'blockMesh > log.blockMesh 2>&1 || fail blockMesh',
                  'stage snappyHexMesh', 'snappyHexMesh -overwrite > log.snappyHexMesh 2>&1 || fail snappyHexMesh',
                  'stage checkMesh', 'checkMesh -allRegions > log.checkMesh 2>&1 || echo "checkMesh reported problems"']
    if n_procs > 1:
        # MPI can hang at start-up on some Macs (PMIx handshake); if simpleFoam writes nothing within 25 s,
        # fall back to a serial run and leave a note that Python turns into a marker (mpi_broken).
        lines += ['stage decomposePar', 'decomposePar -force > log.decomposePar 2>&1 || fail decomposePar',
                  'stage simpleFoam',
                  f'mpirun -np {n_procs} simpleFoam -parallel > log.simpleFoam 2>&1 &',
                  'mpipid=$!', 'n=0', 'while [ "$n" -lt 25 ] && [ ! -s log.simpleFoam ] && kill -0 $mpipid 2>/dev/null; do sleep 1; n=$((n+1)); done',
                  'if [ ! -s log.simpleFoam ]; then',
                  '  echo "@@ note: mpi did not start, running serial"',
                  '  kill $mpipid 2>/dev/null; sleep 1; kill -9 $mpipid 2>/dev/null; wait $mpipid 2>/dev/null',
                  '  rm -rf processor*',
                  '  simpleFoam > log.simpleFoam 2>&1 || fail simpleFoam',
                  'else',
                  '  wait $mpipid || fail simpleFoam',
                  '  stage reconstructPar', '  reconstructPar -latestTime > log.reconstructPar 2>&1 || fail reconstructPar',
                  'fi']
    else:
        lines += ['stage simpleFoam', 'simpleFoam > log.simpleFoam 2>&1 || fail simpleFoam']
    if extra_post:
        lines.append(extra_post)
    return ALLRUN.format(body="\n".join(lines))


def mpi_marker():
    return os.path.join(CACHE_ROOT, ".mpi_broken")


def mpi_known_broken(max_age_s=86400.0):
    try:
        return time.time() - os.path.getmtime(mpi_marker()) < max_age_s
    except OSError:
        return False


def note_mpi_result(folder):
    """After a run: remember (for a day) that MPI did not start here, so later runs go serial at once."""
    if "@@ note: mpi did not start" in _read(os.path.join(folder, "log.run")):
        os.makedirs(CACHE_ROOT, exist_ok=True)
        open(mpi_marker(), "w").close()
        return True
    return False


def case_folder(doc_name, study_name, root=None):
    return os.path.join(root or CACHE_ROOT, safe_name(doc_name), safe_name(study_name))


def write_case(folder, parts, setup):
    """Writes the OpenFOAM case into folder (created; an old case there is replaced). Returns the info dict."""
    if not parts:
        raise CfdError("the study has no parts to put in the flow")
    if setup.turbulence not in ("kOmegaSST", "laminar"):
        raise CfdError("the turbulence model is k-omega SST or laminar")
    if setup.farfield not in ("slip", "symmetry"):
        raise CfdError("the far field is slip or symmetry")
    air = air_properties(setup.temperature_c, setup.altitude_m)
    taken = []
    names, groups = {}, {}
    for part in parts:
        names[part.name] = safe_name(part.name, taken)
        taken.append(names[part.name])
        groups[part.name] = "grp_" + names[part.name]
    geo = geometry(parts, setup)
    refs = {"a_ref": geo["a_ref"], "l_ref": geo["l_ref"]}
    setup_l = Setup.from_dict(setup.to_dict())
    setup_l.l_ref = refs["l_ref"]
    pv = setup.preset_values()
    est = estimate(parts, setup, geo)
    n_procs = setup.n_procs or perf_cores()
    n_procs = max(1, min(n_procs, est["cells"] // 25000 or 1))
    if mpi_known_broken():
        n_procs = 1
    iterations = int(pv["iterations"])
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "constant", "triSurface"))
    center = geo["center"]
    for part in parts:
        tris = rotate_y(part.tris, setup.aoa, center) if setup.aoa else part.tris
        write_stl(os.path.join(folder, "constant", "triSurface", names[part.name] + ".stl"), names[part.name], tris)
    _w(folder, "system/controlDict", control_dict(setup, parts, geo, air, iterations, names, groups, refs))
    _w(folder, "system/fvSchemes", fv_schemes(setup.turbulence))
    _w(folder, "system/fvSolution", fv_solution(setup.turbulence))
    _w(folder, "system/decomposeParDict", decompose_dict(max(n_procs, 2)))
    dom_lo, dom_hi = geo["domain"]
    _w(folder, "system/blockMeshDict", block_mesh_dict(dom_lo, dom_hi, geo["cells"]))
    _w(folder, "system/snappyHexMeshDict", snappy_dict(setup, parts, geo, names, groups, pv))
    for fname, body in boundary_fields(setup_l, groups, air).items():
        os.makedirs(os.path.join(folder, "0"), exist_ok=True)
        with open(os.path.join(folder, "0", fname), "w") as f:
            f.write(body)
    for fname, body in momentum_properties(setup.turbulence, air["nu"]).items():
        _w(folder, "constant/" + fname, body)
    allrun = os.path.join(folder, "Allrun")
    with open(allrun, "w") as f:
        f.write(allrun_text(n_procs))
    os.chmod(allrun, 0o755)
    info = {
        "kind": "external_flow", "folder": folder, "parts": {p.name: {"name": names[p.name], "group": groups[p.name]} for p in parts},
        "air": air, "setup": setup.to_dict(), "geometry": {k: v for k, v in geo.items()}, "refs": refs,
        "estimate": est, "n_procs": n_procs, "iterations": iterations, "turbulence": setup.turbulence,
    }
    with open(os.path.join(folder, "vectorlabs.json"), "w") as f:
        json.dump(info, f, indent=1, default=list)
    return info


# ----- the pipe benchmark -------------------------------------------------------------------------------


def write_pipe_case(folder, diameter=0.010, speed=0.5, length=0.6, temperature_c=20.0, n_axial=300, n_radial=24,
                    wedge_deg=5.0):
    """Laminar fully developed pipe flow as a 5 degree axisymmetric wedge, same dictionaries as the airflow study."""
    air = air_properties(temperature_c, 0.0)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    R = diameter / 2
    a = math.radians(wedge_deg / 2)
    y, z = R * math.cos(a), R * math.sin(a)
    setup = Setup(speed=speed, turbulence="laminar")
    parts = []
    geo = {"center": (0, 0, 0)}
    _w(folder, "system/controlDict", f"""
application simpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 3000;
deltaT 1;
writeControl timeStep;
writeInterval 3000;
purgeWrite 1;
writeFormat ascii;
writePrecision 10;
timeFormat general;
timePrecision 6;
runTimeModifiable true;
""")
    _w(folder, "system/fvSchemes", fv_schemes("laminar"))
    _w(folder, "system/fvSolution", fv_solution("laminar", residual=1e-8, p_residual=1e-8))
    _w(folder, "system/blockMeshDict", f"""
scale 1;
vertices
(
    (0 0 0) ({length:.9g} 0 0)
    (0 {y:.9g} {-z:.9g}) ({length:.9g} {y:.9g} {-z:.9g})
    (0 {y:.9g} {z:.9g}) ({length:.9g} {y:.9g} {z:.9g})
);
blocks ( hex (0 1 3 2 0 1 5 4) ({n_axial} {n_radial} 1) simpleGrading (1 1 1) );
edges ();
boundary
(
    inlet  {{ type patch; faces ((0 0 4 2)); }}
    outlet {{ type patch; faces ((1 3 5 1)); }}
    wall   {{ type wall; faces ((2 3 5 4)); }}
    front  {{ type wedge; faces ((0 2 3 1)); }}
    back   {{ type wedge; faces ((0 1 5 4)); }}
);
mergePatchPairs ();
""")
    ux = "uniform (%.9g 0 0)" % speed
    blocks = {
        "U": _field("volVectorField", "U", "[0 1 -1 0 0 0 0]", ux, "\n".join([
            _patch_block("inlet", ["type fixedValue;", f"value {ux};"]),
            _patch_block("outlet", ["type zeroGradient;"]),
            _patch_block("wall", ["type noSlip;"]),
            _patch_block("front", ["type wedge;"]), _patch_block("back", ["type wedge;"])])),
        "p": _field("volScalarField", "p", "[0 2 -2 0 0 0 0]", "uniform 0", "\n".join([
            _patch_block("inlet", ["type zeroGradient;"]),
            _patch_block("outlet", ["type fixedValue;", "value uniform 0;"]),
            _patch_block("wall", ["type zeroGradient;"]),
            _patch_block("front", ["type wedge;"]), _patch_block("back", ["type wedge;"])])),
    }
    for fname, body in blocks.items():
        os.makedirs(os.path.join(folder, "0"), exist_ok=True)
        with open(os.path.join(folder, "0", fname), "w") as f:
            f.write(body)
    for fname, body in momentum_properties("laminar", air["nu"]).items():
        _w(folder, "constant/" + fname, body)
    allrun = os.path.join(folder, "Allrun")
    with open(allrun, "w") as f:
        f.write(ALLRUN.format(body="""stage blockMesh
blockMesh > log.blockMesh 2>&1 || fail blockMesh
stage simpleFoam
simpleFoam > log.simpleFoam 2>&1 || fail simpleFoam
stage postProcess
postProcess -latestTime -funcs '(writeCellCentres writeCellVolumes)' > log.postProcess 2>&1 || fail postProcess"""))
    os.chmod(allrun, 0o755)
    info = {"kind": "pipe", "air": air, "diameter": diameter, "speed": speed, "length": length}
    with open(os.path.join(folder, "vectorlabs.json"), "w") as f:
        json.dump(info, f)
    return info


def _read_internal_field(path, kind):
    """Values of an ascii volScalarField/volVectorField internalField list (or uniform), as a list."""
    with open(path) as f:
        text = f.read()
    m = re.search(r"internalField\s+nonuniform\s+List<(\w+)>\s*\n?\s*(\d+)\s*\n?\(", text)
    if not m:
        m2 = re.search(r"internalField\s+uniform\s+([^;]+);", text)
        raise CfdError(f"{os.path.basename(path)} is uniform ({m2.group(1) if m2 else '?'})")
    n = int(m.group(2))
    body = text[m.end():]
    if kind == "vector":
        nums = re.findall(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", body[: body.index("\n)\n") + 3])
        return [tuple(float(x) for x in t) for t in nums[:n]]
    block = body[: body.index("\n)")]
    return [float(x) for x in block.split()[:n]]


def pipe_pressure_gradient(folder, x_from=0.35, x_to=0.55, n_slabs=12):
    """dp/dx in Pa/m from the volume-averaged pressure in axial slabs between x_from and x_to."""
    times = [d for d in os.listdir(folder) if re.fullmatch(r"\d+(\.\d+)?", d) and d != "0"]
    if not times:
        raise CfdError("the pipe run wrote no time directory")
    t = max(times, key=float)
    rho = air_properties(20.0, 0.0)["rho"]
    info = json.load(open(os.path.join(folder, "vectorlabs.json")))
    rho = info["air"]["rho"]
    p = _read_internal_field(os.path.join(folder, t, "p"), "scalar")
    c = _read_internal_field(os.path.join(folder, t, "C"), "vector")
    v = _read_internal_field(os.path.join(folder, t, "V"), "scalar")
    sums = [[0.0, 0.0, 0.0] for _ in range(n_slabs)]  # volume, volume*p, volume*x
    for pi, ci, vi in zip(p, c, v):
        x = ci[0]
        if x_from <= x < x_to:
            k = min(n_slabs - 1, int((x - x_from) / (x_to - x_from) * n_slabs))
            sums[k][0] += vi
            sums[k][1] += vi * pi
            sums[k][2] += vi * x
    xs = [s[2] / s[0] for s in sums if s[0] > 0]
    ps = [s[1] / s[0] for s in sums if s[0] > 0]
    n = len(xs)
    mx, mp = sum(xs) / n, sum(ps) / n
    slope = sum((a - mx) * (b - mp) for a, b in zip(xs, ps)) / sum((a - mx) ** 2 for a in xs)
    return slope * rho  # kinematic -> Pa/m (negative: pressure falls along the flow)


def pipe_reference(diameter=0.010, speed=0.5, temperature_c=20.0):
    """Hagen-Poiseuille: dp/dx = 32 mu U / D^2 (Pa/m) and the entrance length 0.05 Re D."""
    air = air_properties(temperature_c, 0.0)
    re = speed * diameter / air["nu"]
    return {"dpdx": 32 * air["mu"] * speed / diameter ** 2, "Re": re, "entrance": 0.05 * re * diameter}


# ----- run, logs, convergence ----------------------------------------------------------------------------


def _clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("PYTHON", "DYLD_", "QT_", "FREECAD")):
            env.pop(k)
    return env


class Runner:
    """Runs a case's Allrun in the OpenFOAM environment in the background."""

    def __init__(self, folder, install=None):
        self.folder = folder
        self.install = install or find_openfoam()
        if self.install is None:
            raise CfdError(install_text())
        self.process = None
        self.stopped = False
        self.started = None

    def start(self):
        for f in glob.glob(os.path.join(self.folder, "log.*")) + glob.glob(os.path.join(self.folder, "processor*")):
            shutil.rmtree(f) if os.path.isdir(f) else os.remove(f)
        shutil.rmtree(os.path.join(self.folder, "postProcessing"), ignore_errors=True)
        self.log = open(os.path.join(self.folder, "log.run"), "w")
        self.started = time.time()
        self.process = subprocess.Popen([self.install["launcher"], "-c", f"cd '{self.folder}' && ./Allrun"],
                                        stdout=self.log, stderr=subprocess.STDOUT, env=_clean_env(),
                                        start_new_session=True)
        return self

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """Stop button: terminates the whole process group (mpirun and its ranks included)."""
        self.stopped = True
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                self.process.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except Exception:
                    pass

    def wait(self, timeout=None):
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return self.process.returncode

    def stage(self):
        return last_stage(self.folder)

    def result(self):
        """'done', 'failed: <step>', 'stopped' or 'running'."""
        if self.running():
            return "running"
        if self.stopped:
            return "stopped"
        text = _read(os.path.join(self.folder, "log.run"))
        note_mpi_result(self.folder)
        if "@@ done" in text:
            return "done"
        m = re.findall(r"@@ failed: (\S+)", text)
        return f"failed: {m[-1]}" if m else f"failed (exit {self.process.returncode if self.process else '?'})"


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def last_stage(folder):
    m = re.findall(r"@@ stage: (\S+)", _read(os.path.join(folder, "log.run")))
    return m[-1] if m else ""


_RES = re.compile(r"Solving for (\w+), Initial residual = ([-\d.eE+]+)")
_TIME = re.compile(r"^Time = (\S+?)s?$")


class LogReader:
    """Follows log.simpleFoam and the forces files: residual history and forces per iteration."""

    def __init__(self, folder):
        self.folder = folder
        self.offset = 0
        self.iteration = 0
        self.residuals = {}  # field -> [(iteration, value)]
        self.pending = {}

    def update(self):
        path = os.path.join(self.folder, "log.simpleFoam")
        try:
            with open(path, errors="replace") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return self
        if not chunk:
            return self
        cut = chunk.rfind("\n")
        if cut < 0:
            return self
        self.offset += len(chunk[: cut + 1].encode("utf-8", errors="replace"))
        for line in chunk[: cut + 1].splitlines():
            m = _TIME.match(line.strip())
            if m:
                try:
                    self.iteration = int(float(m.group(1)))
                except ValueError:
                    pass
                self.pending = {}
                continue
            m = _RES.search(line)
            if m and m.group(1) not in self.pending:
                self.pending[m.group(1)] = 1
                self.residuals.setdefault(m.group(1), []).append((self.iteration, float(m.group(2))))
        return self

    def latest(self):
        return {f: v[-1][1] for f, v in self.residuals.items() if v}


def read_dat(path):
    """Rows of numbers of an OpenFOAM function-object .dat file (comment lines skipped; brackets stripped)."""
    rows = []
    for line in _read(path).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            rows.append([float(x) for x in line.replace("(", " ").replace(")", " ").split()])
        except ValueError:
            continue
    return rows


def _latest_dat(folder, func, name):
    paths = glob.glob(os.path.join(folder, "postProcessing", func, "*", name))
    if not paths:
        return []
    rows = []
    for p in sorted(paths, key=lambda s: float(os.path.basename(os.path.dirname(s)))):
        rows += read_dat(p)
    return rows


def part_forces(folder, part_func_name):
    """[(iteration, Fx, Fy, Fz)] of a part, from the forces function object (total = pressure + viscous)."""
    rows = _latest_dat(folder, "forces_" + part_func_name, "force.dat")
    return [(r[0], r[1], r[2], r[3]) for r in rows if len(r) >= 4]  # total_x, y, z (pressure + viscous)


def part_coeffs(folder, part_func_name):
    """[(iteration, Cd, Cl)] of a part from forceCoeffs."""
    rows = _latest_dat(folder, "forceCoeffs_" + part_func_name, "coefficient.dat")
    # columns: Time Cd Cd(f) Cd(r) Cl Cl(f) Cl(r) CmPitch ...
    return [(r[0], r[1], r[4]) for r in rows if len(r) >= 5]


def flow_balance(folder):
    """|flow in + flow out| / |flow in| from the patch flow rates at the last iteration, or None."""
    rin = _latest_dat(folder, "flow_inlet", "surfaceFieldValue.dat")
    rout = _latest_dat(folder, "flow_outlet", "surfaceFieldValue.dat")
    if not rin or not rout:
        return None
    qin, qout = rin[-1][-1], rout[-1][-1]
    return abs(qin + qout) / max(abs(qin), 1e-30)


def convergence(folder, info=None, reader=None, residual=CONVERGED_RESIDUAL, window=FORCE_WINDOW, change=FORCE_CHANGE):
    """The plan's three tests as a dict: residuals, forces, flow, converged, text."""
    info = info or json.load(open(os.path.join(folder, "vectorlabs.json")))
    reader = reader or LogReader(folder).update()
    latest = reader.latest()
    res_ok = bool(latest) and all(v < residual for v in latest.values())
    forces_ok, worst = bool(info.get("parts")), 0.0
    for part, d in info.get("parts", {}).items():
        rows = part_forces(folder, d["name"])
        # the last `window` iterations; a run the solver stopped early (residuals met) is judged on its last half,
        # so the start-up transient does not count, but never on fewer than MIN_WINDOW iterations
        n_run = len(rows)
        w = max(MIN_WINDOW, min(window, n_run // 2))
        tail = rows[-w:]
        if n_run < MIN_WINDOW or len(tail) < 2:
            forces_ok = False
            worst = float("inf")
            continue
        mags = [math.sqrt(r[1] ** 2 + r[2] ** 2 + r[3] ** 2) for r in tail]
        scale = max(max(mags), 1e-30)
        for axis in (1, 2, 3):
            vals = [r[axis] for r in tail]
            dev = (max(vals) - min(vals)) / scale
            worst = max(worst, dev)
            if dev > change:
                forces_ok = False
    balance = flow_balance(folder)
    flow_ok = balance is not None and balance < FLOW_BALANCE
    out = {"residuals": latest, "residuals_ok": res_ok, "force_change": worst, "forces_ok": forces_ok,
           "flow_balance": balance, "flow_ok": flow_ok, "iteration": reader.iteration,
           "converged": res_ok and forces_ok and flow_ok}
    out["text"] = (f"residuals {'below' if res_ok else 'not below'} {residual:g}; forces change "
                   f"{'<' if forces_ok else '>='} {change * 100:g} % over the last {window} iterations"
                   f"{'' if worst != float('inf') else ' (not enough iterations yet)'}; mass flow balance "
                   f"{'n/a' if balance is None else f'{balance * 100:.3g} %'}")
    return out


def forces_table(folder, info=None):
    """Per part and in total: Fx, Fy, Fz (N), Cd, Cl, from the last iteration (flow frame: drag +X, lift +Z)."""
    info = info or json.load(open(os.path.join(folder, "vectorlabs.json")))
    rows, total = [], [0.0, 0.0, 0.0]
    for part, d in info["parts"].items():
        f = part_forces(folder, d["name"])
        c = part_coeffs(folder, d["name"])
        if not f:
            continue
        fx, fy, fz = f[-1][1:4]
        total = [total[0] + fx, total[1] + fy, total[2] + fz]
        rows.append({"part": part, "drag": fx, "side": fy, "lift": fz, "Cd": c[-1][1] if c else None, "Cl": c[-1][2] if c else None})
    refs = info["refs"]
    q = 0.5 * info["air"]["rho"] * info["setup"]["speed"] ** 2
    cd = total[0] / (q * refs["a_ref"]) if q else None
    cl = total[2] / (q * refs["a_ref"]) if q else None
    return {"parts": rows, "total": {"drag": total[0], "side": total[1], "lift": total[2], "Cd": cd, "Cl": cl}}


# ----- the pre-check -----------------------------------------------------------------------------------


def precheck(parts, setup, install="auto", geo=None):
    """Issues as [(level, text)] with level 'error', 'warning' or 'info'; an 'Install OpenFOAM...' action is the
    ``install`` error. The estimate dict is the second return value."""
    issues = []
    if install == "auto":
        install = find_openfoam()
    if install is None:
        issues.append(("error", "OpenFOAM is not installed. " + install_text() + " (Install OpenFOAM...)"))
    elif install["warning"]:
        issues.append(("warning", install["warning"]))
    if not parts:
        issues.append(("error", "No parts in the flow. Choose a model with solid bodies."))
        return issues, None
    for p in parts:
        n = open_edges(p.tris)
        if n:
            issues.append(("error", f"{p.name} is not watertight: {n} open or inconsistent edges. "
                                    "The mesher needs a closed solid."))
    est = None
    try:
        geo = geometry(parts, setup)
        est = estimate(parts, setup, geo)
    except CfdError as exc:
        issues.append(("error", str(exc)))
        return issues, None
    est["geometry"] = geo
    limit = setup.cell_limit or MAX_CELLS[setup.preset]
    if est["cells"] > limit:
        issues.append(("error", f"About {est['cells']:,} cells expected; the {setup.preset} preset allows {limit:,}. "
                                "Choose a coarser preset or a smaller domain."))
    else:
        issues.append(("info", f"About {est['cells']:,} cells expected (limit {limit:,})."))
    y = est["yplus"]
    if setup.turbulence != "laminar":
        if y > 300:
            issues.append(("warning", f"y+ is about {y:.0f}; wall functions want 30 to 300. Choose a finer preset."))
        elif y < 1:
            issues.append(("info", f"y+ is about {y:.1f}: the first cell is fine enough to resolve the wall."))
        else:
            issues.append(("info", f"y+ is about {y:.0f} (wall functions want 30 to 300 or below 1)."))
    re = est["reynolds"]
    if setup.turbulence != "laminar" and re < 2000:
        issues.append(("warning", f"Reynolds number {re:.0f} is below 2000: the flow is probably laminar. "
                                  "Choose Laminar."))
    if setup.turbulence == "laminar" and re > 5000:
        issues.append(("warning", f"Reynolds number {re:.0f} is high for laminar flow. Choose k-omega SST."))
    if setup.speed <= 0:
        issues.append(("error", "The speed must be above zero."))
    if setup.speed > 100:
        issues.append(("warning", "Above about 100 m/s (Mach 0.3) the incompressible solver is not valid."))
    return issues, est


# ----- FreeCAD glue (lazy imports) ----------------------------------------------------------------------


def triangles_from_shape(shape, tolerance_mm=0.1):
    """Triangles in metres of a FreeCAD shape (mm)."""
    pts, facets = shape.tessellate(tolerance_mm)
    return [tuple((pts[i].x * 1e-3, pts[i].y * 1e-3, pts[i].z * 1e-3) for i in f) for f in facets]


def parts_from_model(model, tolerance_mm=None):
    """One Part per solid-carrying child of the model (assembly parts), else the model itself."""
    from bruceworx_sim import shape_of

    out, taken = [], []

    def add(obj):
        shape = shape_of(obj)
        if shape is None or not shape.Solids:
            return False
        bb = shape.BoundBox
        tol = tolerance_mm or max(bb.DiagonalLength / 400.0, 0.02)
        name = safe_name(obj.Label, taken)
        taken.append(name)
        part = Part(name, triangles_from_shape(shape, tol))
        part.label = obj.Label
        out.append(part)
        return True

    kids = []
    for child in getattr(model, "Group", []) or []:
        kids.append(child)
    for child in getattr(model, "Links", []) or []:
        kids.append(child)
    done = False
    if kids and model.TypeId in ("App::Part", "Assembly::AssemblyObject", "App::LinkGroup"):
        for k in kids:
            if k.TypeId.startswith(("Sketcher", "App::Origin", "Assembly::Joint")) or k.isDerivedFrom("App::Origin"):
                continue
            done = add(k) or done
    if not done:
        add(model)
    return out


# ----- the study glue -----------------------------------------------------------------------------------

AIR_KIND = "external_flow"
KINDS = (AIR_KIND,)
STUDY_PROPS = (
    ("App::PropertyFloat", "FlowSpeed", "Speed of the free stream in m/s", 10.0),
    ("App::PropertyFloat", "AngleOfAttack", "Angle of attack in degrees (pitch about the model's Y axis, flow along +X)", 0.0),
    ("App::PropertyFloat", "AirTemperature", "Air temperature in degC", 20.0),
    ("App::PropertyFloat", "AirAltitude", "Altitude in m (standard atmosphere)", 0.0),
    ("App::PropertyFloat", "ReferenceArea", "Reference area in m^2 for Cd and Cl; 0 = frontal area of the bounding box", 0.0),
    ("App::PropertyFloat", "ReferenceLength", "Reference length in m; 0 = bounding box length along the flow", 0.0),
    ("App::PropertyString", "MeshPreset", "coarse, medium or fine", DEFAULT_PRESET),
    ("App::PropertyString", "Turbulence", "kOmegaSST or laminar", "kOmegaSST"),
    ("App::PropertyString", "FarField", "slip or symmetry", "slip"),
    ("App::PropertyFloat", "DomainUpstream", "Upstream distance in characteristic lengths", DOMAIN_FACTORS["upstream"]),
    ("App::PropertyFloat", "DomainDownstream", "Downstream distance in characteristic lengths", DOMAIN_FACTORS["downstream"]),
    ("App::PropertyFloat", "DomainLateral", "Lateral distance in characteristic lengths", DOMAIN_FACTORS["lateral"]),
    ("App::PropertyString", "CaseFolder", "The OpenFOAM case folder of the last run", ""),
    ("App::PropertyString", "CfdSummary", "JSON summary of the last run (forces, convergence)", ""),
)


def is_airflow(analysis):
    return analysis is not None and "StudyType" in analysis.PropertiesList and str(analysis.StudyType) in KINDS


def ensure_properties(analysis):
    for typ, name, doc, default in STUDY_PROPS:
        if name not in analysis.PropertiesList:
            analysis.addProperty(typ, name, "Airflow", "[BW] " + doc)
            setattr(analysis, name, default)


def apply_options(analysis, options):
    ensure_properties(analysis)
    names = {n for _t, n, _d, _v in STUDY_PROPS}
    for key, value in (options or {}).items():
        if key not in names:
            raise CfdError(f"unknown airflow setting {key!r}")
        setattr(analysis, key, value)


def new_airflow_study(doc, model, name="Airflow 1", options=None):
    """An external-flow study: a Fem::FemAnalysis with the airflow settings (no CalculiX members)."""
    import ObjectsFem

    from bruceworx_sim import StudyPlan, _add_study_properties

    analysis = ObjectsFem.makeAnalysis(doc, "Study")
    analysis.Label = f"Study: {name}"
    plan = StudyPlan(name=name, study_type=AIR_KIND, model=model)
    _add_study_properties(analysis, plan)
    apply_options(analysis, options)
    return analysis


def setup_of(analysis):
    g = lambda n, d=None: getattr(analysis, n) if n in analysis.PropertiesList else d
    return Setup(speed=g("FlowSpeed", 10.0), aoa=g("AngleOfAttack", 0.0), temperature_c=g("AirTemperature", 20.0),
                 altitude_m=g("AirAltitude", 0.0), preset=g("MeshPreset", DEFAULT_PRESET) or DEFAULT_PRESET,
                 turbulence=g("Turbulence", "kOmegaSST") or "kOmegaSST", a_ref=g("ReferenceArea", 0.0) or None,
                 l_ref=g("ReferenceLength", 0.0) or None, farfield=g("FarField", "slip") or "slip",
                 factors={"upstream": g("DomainUpstream", 5.0), "downstream": g("DomainDownstream", 10.0),
                          "lateral": g("DomainLateral", 5.0)})


def study_parts(analysis):
    return parts_from_model(analysis.Model)


def study_folder(analysis, root=None):
    return case_folder(analysis.Document.Name or "Unnamed", analysis.Label.replace("Study: ", ""), root)


def study_precheck(analysis):
    return precheck(study_parts(analysis), setup_of(analysis))


def prepare_run(analysis, root=None):
    """Pre-check, write the case, and return (folder, info). Raises CfdError on blocking issues."""
    parts = study_parts(analysis)
    setup = setup_of(analysis)
    issues, _est = precheck(parts, setup)
    errors = [t for lvl, t in issues if lvl == "error"]
    if errors:
        raise CfdError(" ".join(errors))
    folder = study_folder(analysis, root)
    info = write_case(folder, parts, setup)
    analysis.CaseFolder = folder
    return folder, info


def start_run(analysis, root=None):
    folder, info = prepare_run(analysis, root)
    return Runner(folder).start(), info


def save_summary(analysis, folder, stage_text=""):
    info = json.load(open(os.path.join(folder, "vectorlabs.json")))
    conv = convergence(folder, info)
    table = forces_table(folder, info)
    summary = {"convergence": conv, "forces": table, "air": info["air"], "setup": info["setup"],
               "estimate": {k: v for k, v in info["estimate"].items() if k != "geometry"},
               "refs": info["refs"], "iterations": info["iterations"], "stage": stage_text}
    analysis.CfdSummary = json.dumps(summary, default=str)
    return summary


# ----- results (foamToVTK and the FEM post pipeline) -------------------------------------------------------


def convert_vtk(folder, install=None):
    """foamToVTK of the latest time; returns the internal-mesh .vtu path."""
    install = install or find_openfoam()
    if install is None:
        raise CfdError(install_text())
    r = subprocess.run([install["launcher"], "-c", f"cd '{folder}' && foamToVTK -latestTime -ascii > log.foamToVTK 2>&1"],
                       env=_clean_env(), capture_output=True, text=True, timeout=600)
    found = sorted(glob.glob(os.path.join(folder, "VTK", "*", "internal.vtu")) + glob.glob(os.path.join(folder, "VTK", "*.vtk")))
    if r.returncode or not found:
        raise CfdError("foamToVTK made no output: " + _read(os.path.join(folder, "log.foamToVTK"))[-400:])
    return found[-1]


def surface_files(folder):
    return sorted(glob.glob(os.path.join(folder, "VTK", "*", "boundary", "grp_*.vtp")) +
                  glob.glob(os.path.join(folder, "VTK", "*", "*.vtp")))


PIPELINE_NOTE = ("FreeCAD's FEM post pipeline crashes in this GUI build, so the flow is converted to VTK "
                 "(foamToVTK) but not shown here; use Open in ParaView.")


def load_results(analysis, folder=None, install=None):
    """Converts the latest time to VTK and adds it to the study as a FEM post-pipeline (scaled to mm so it sits on
    the model, velocity and pressure stay in SI) with a cut plane through the middle (XZ). Returns
    {"result", "cut", "vtu"}.

    Surface pressure is a field of this pipeline (colour the boundary by p); streamlines: this build of FreeCAD's
    FEM module has no streamline filter object, so they are left to ParaView (Open in ParaView).
    """
    import FreeCAD
    import ObjectsFem

    folder = folder or analysis.CaseFolder
    vtu = convert_vtk(folder, install)
    if FreeCAD.GuiUp and not os.environ.get("BW_FEM_PIPELINE"):
        # Creating a Fem::FemPostPipeline in this GUI build crashes in SoFCColorGradient::setMarkerLabel (found
        # 27 Oct 2026, see bruceworx_sim._no_post_pipeline), so the GUI only converts; headless makes the pipeline.
        return {"result": None, "cut": None, "vtu": vtu, "note": PIPELINE_NOTE}
    doc = analysis.Document
    result = doc.addObject("Fem::FemPostPipeline", "Pipeline_Airflow")
    result.read(vtu)
    result.scale(1000.0)  # the case is in metres, the model in mm
    result.Label = "Airflow result"
    analysis.addObject(result)
    cut = None
    try:
        info = json.load(open(os.path.join(folder, "vectorlabs.json")))
        lo, hi = info["geometry"]["bbox"]
        provider = doc.addObject("Fem::FemPostFunctionProvider", "Functions")
        result.addObject(provider)
        plane = doc.addObject("Fem::FemPostPlaneFunction", "CutPlane")
        provider.addObject(plane)
        plane.Origin = FreeCAD.Vector(0, (lo[1] + hi[1]) / 2 * 1000.0, 0)
        plane.Normal = FreeCAD.Vector(0, 1, 0)
        cut = ObjectsFem.makePostVtkFilterCutFunction(doc, result)
        cut.Label = "Cut plane (XZ)"
        cut.Function = plane
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[cfd] cut plane not added: {exc}\n")
        cut = None
    doc.recompute()
    try:
        if FreeCAD.GuiUp:
            result.ViewObject.DisplayMode = "Surface"
    except Exception:
        pass
    return {"result": result, "cut": cut, "vtu": vtu}


def open_in_paraview(folder):
    app = paraview_app()
    if app is None:
        raise CfdError("ParaView is not installed")
    foam = os.path.join(folder, "case.foam")
    open(foam, "a").close()
    subprocess.Popen(["open", "-a", app, foam])
    return foam


# ----- report ----------------------------------------------------------------------------------------


def _tex(text):
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("_", r"\_"), ("#", r"\#"), ("$", r"\$"),
                 ("{", r"\{"), ("}", r"\}")):
        text = text.replace(a, b)
    return text


def report_section(summary):
    """The airflow section of the LaTeX report from a saved summary (a dict, see save_summary)."""
    s = summary["setup"]
    a = summary["air"]
    conv = summary["convergence"]
    t = summary["forces"]
    turb_word = "laminar" if s["turbulence"] == "laminar" else r"$k$--$\omega$ SST"
    flow_word = "ok" if conv["flow_ok"] else r"above 0.5\,\%"
    lines = [r"\section*{Airflow}",
             r"\subsection*{Setup}",
             r"\begin{tabular}{@{}ll@{}}",
             f"Free stream & {s['speed']:.4g}\\,m/s at {s['aoa']:.3g}$^\\circ$ angle of attack\\\\",
             f"Air & {s['temperature_c']:.3g}\\,$^\\circ$C, {s['altitude_m']:.0f}\\,m: $\\rho={a['rho']:.4f}$\\,kg/m$^3$, "
             f"$\\nu={a['nu']:.3e}$\\,m$^2$/s\\\\",
             f"Turbulence & {turb_word}\\\\",
             f"Mesh & {_tex(s['preset'])} preset, about {summary['estimate']['cells']:,} cells expected\\\\".replace(",", "\\,"),
             f"Reference & area {summary['refs']['a_ref']:.4g}\\,m$^2$, length {summary['refs']['l_ref']:.4g}\\,m\\\\",
             r"\end{tabular}",
             r"\subsection*{Convergence}",
             r"\begin{itemize}",
             f"\\item Residuals below $10^{{-4}}$: {'yes' if conv['residuals_ok'] else 'no'} (after {conv['iteration']} iterations)",
             f"\\item Forces change less than 0.5\\,\\% over the last {FORCE_WINDOW} iterations: {'yes' if conv['forces_ok'] else 'no'}",
             "\\item Mass flow balance: " + ("not available" if conv["flow_balance"] is None else
                                           f"{conv['flow_balance'] * 100:.3g}\\,\\% ({flow_word})"),
             f"\\item Verdict: \\textbf{{{'converged' if conv['converged'] else 'not converged'}}}",
             r"\end{itemize}",
             r"\subsection*{Forces}",
             r"\begin{tabular}{@{}lrrrrr@{}}", r"\toprule",
             r"Part & Drag (N) & Side (N) & Lift (N) & $C_d$ & $C_l$\\", r"\midrule"]

    def num(v, d=4):
        return "--" if v is None else f"{v:.{d}g}"

    for r in t["parts"]:
        lines.append(f"{_tex(r['part'])} & {num(r['drag'])} & {num(r['side'])} & {num(r['lift'])} & {num(r['Cd'])} & {num(r['Cl'])}\\\\")
    tot = t["total"]
    lines += [r"\midrule", f"Total & {num(tot['drag'])} & {num(tot['side'])} & {num(tot['lift'])} & {num(tot['Cd'])} & {num(tot['Cl'])}\\\\",
              r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines) + "\n"
