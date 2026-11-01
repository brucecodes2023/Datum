# SPDX-License-Identifier: LGPL-2.1-or-later
"""Prop wash with actuator disks (SIM-06 phase 2, 31 Oct 2026; plan chapter 67, built record 67d).

Headless core on top of bruceworx_cfd (case writer, runner, log reader, convergence), no FreeCAD import at module
level.  A *prop-wash* study puts the model in still air (hover) or a free stream (forward flight, +X of the model)
and replaces every propeller by an actuator disk: a cylinder of cells that carries a constant momentum source.

* Why not OpenFOAM's ``actuationDiskSource``: that is a *turbine* model.  Its thrust is ``2 rho A U^2 a (1-a)`` from the
  upstream speed ``U`` and ``Cp``/``Ct`` curves, so it gives no thrust in hover (``U = 0``) and it takes momentum out
  of the flow.  A propeller has a given thrust, so each disk is a ``vectorSemiImplicitSource`` (``constant/fvOptions``) on
  a cell zone made by ``topoSet``: the force on the air is ``-T n`` (``n`` the motor's ThrustAxis), spread over the
  disk's cells in proportion to their volume (``volumeMode absolute``), so the total is exactly ``T`` whatever the mesh.
* Thrust per motor: the hover share of the ROB-04 balance (weight split over the motors by the CG offset); the thrust
  table gives the throttle and current that deliver it.  A typed thrust overrides it.
* Domain: a box around the model with ``totalPressure`` / ``pressureInletOutletVelocity`` on all six faces in hover
  (air may be drawn in or leave anywhere), the phase-1 free stream (inlet, outlet, slip sides) in forward flight.
* Outputs: force on every part (pressure + viscous, the ``forces`` function object), the download on each part as a
  fraction of the total thrust, and the mean induced velocity in every disk (``volFieldValue``).
* Benchmark: ``write_disk_case`` is one disk in free air as an axisymmetric wedge (momentum theory:
  ``v_i = sqrt(T / 2 rho A)`` at the disk, ``2 v_i`` in the fully contracted wake).

Units: SI (m, kg, s, N); the study glue scales FreeCAD's mm.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil

import bruceworx_cfd as cfd
from bruceworx_cfd import CfdError

WASH_KIND = "prop_wash"
G0 = 9.80665
WASH_FACTORS = {"upstream": 3.0, "downstream": 6.0, "lateral": 2.0, "wake": 4.0}  # in characteristic lengths
CELLS_ACROSS_DISK = {"coarse": 10, "medium": 16, "fine": 24}
DISK_LAYERS = 3  # thickness of the disk's cell zone, in cells of the disk level


# ----- momentum theory ----------------------------------------------------------------------------------


HUB_FRACTION = 0.2  # default hub diameter / propeller diameter (a 3 inch quad prop: 12 to 16 mm hub on 76 mm)


def disk_area(diameter, hub=0.0):
    """Area (m^2) of the disk: a ring when ``hub`` (the hub diameter) is given, where the propeller moves no air."""
    return math.pi * (diameter * diameter - hub * hub) / 4.0


def induced_velocity(thrust, rho, diameter, free_stream=(0.0, 0.0, 0.0), wake_dir=(0.0, 0.0, -1.0), hub=0.0):
    """Induced velocity v_i (m/s) of a disk: T = 2 rho A v_i |V + v_i w| (w the unit wake direction, V the free stream).
    Hover (V = 0) is sqrt(T / 2 rho A); the far wake is 2 v_i.  A is the ring area when the disk has a hub."""
    area = disk_area(diameter, hub)
    if thrust <= 0 or area <= 0:
        return 0.0

    def f(v):
        w = [free_stream[k] + v * wake_dir[k] for k in range(3)]
        return 2.0 * rho * area * v * math.sqrt(sum(c * c for c in w)) - thrust

    lo, hi = 0.0, math.sqrt(thrust / (2.0 * rho * area)) * 2.0 + 1.0
    while f(hi) < 0:
        hi *= 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ----- the disks ------------------------------------------------------------------------------------------


class Disk:
    """One propeller as an actuator disk (metres, model frame): centre, unit thrust axis, diameter, thrust (N), and the
    diameter of the hub (a hole in the disk, 0 = none)."""

    def __init__(self, name, center, axis, diameter, thrust, label=None, motor=None, hub=0.0):
        n = math.sqrt(sum(c * c for c in axis)) or 1.0
        self.name = name
        self.center = tuple(float(c) for c in center)
        self.axis = tuple(c / n for c in axis)
        self.diameter = float(diameter)
        self.thrust = float(thrust)
        self.label = label or name
        self.motor = motor or name
        self.hub = max(0.0, min(float(hub or 0.0), 0.9 * self.diameter))

    @property
    def area(self):
        return disk_area(self.diameter, self.hub)

    @property
    def wake_dir(self):
        return tuple(-c for c in self.axis)

    def to_dict(self):
        return {"name": self.name, "center": list(self.center), "axis": list(self.axis), "diameter": self.diameter,
                "thrust": self.thrust, "label": self.label, "motor": self.motor, "hub": self.hub}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d["center"], d["axis"], d["diameter"], d["thrust"], d.get("label"), d.get("motor"), d.get("hub", 0.0))


def _rotate_vec_y(v, angle_deg):
    """The vector rotated about Y like cfd.rotate_y turns the geometry (without the centre shift)."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return (v[0] * ca + v[2] * sa, v[1], -v[0] * sa + v[2] * ca)


def rotated_disks(disks, aoa, about):
    """The disks turned by the angle of attack the way the geometry is (free stream always +X)."""
    if not aoa:
        return list(disks)
    out = []
    for d in disks:
        rel = tuple(d.center[k] - about[k] for k in range(3))
        c = _rotate_vec_y(rel, aoa)
        out.append(Disk(d.name, tuple(c[k] + about[k] for k in range(3)), _rotate_vec_y(d.axis, aoa), d.diameter,
                        d.thrust, d.label, d.motor, d.hub))
    return out


def disk_corners(disk):
    """Points on the rim of the disk, for the bounding box (metres)."""
    n = disk.axis
    a = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = (n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0])
    m = math.sqrt(sum(c * c for c in u))
    u = tuple(c / m for c in u)
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])
    r = disk.diameter / 2
    pts = []
    for k in range(16):
        t = 2 * math.pi * k / 16
        pts.append(tuple(disk.center[i] + r * (math.cos(t) * u[i] + math.sin(t) * v[i]) for i in range(3)))
    return pts


# ----- the setup -----------------------------------------------------------------------------------------


class WashSetup(cfd.Setup):
    """A prop-wash study: speed 0 is hover, speed > 0 a free stream along +X of the model (with the angle of attack)."""

    def __init__(self, speed=0.0, factors=None, **kw):
        super().__init__(speed=speed, factors=dict(WASH_FACTORS, **(factors or {})), **kw)

    @property
    def hover(self):
        return self.speed <= 1e-9


def disk_level(disk_diameter, dx0, preset_name):
    """Refinement level at which cells are D / (cells across the disk) or finer."""
    want = disk_diameter / CELLS_ACROSS_DISK.get(preset_name, 10)
    return max(1, int(math.ceil(math.log2(max(dx0 / want, 1.0)))))


def wash_geometry(parts, disks, setup):
    """Sizes of the case in the flow frame (like cfd.geometry): bbox of parts and disk rims, the box domain with its
    margins (more on the wake side), the background cell counts, and the refinement box."""
    pts_tris = [t for p in parts for t in p.tris]
    rims = [pt for d in disks for pt in disk_corners(d)]
    if not pts_tris and not rims:
        raise CfdError("the parts have no size")
    xs = [p[0] for t in pts_tris for p in t] + [p[0] for p in rims]
    ys = [p[1] for t in pts_tris for p in t] + [p[1] for p in rims]
    zs = [p[2] for t in pts_tris for p in t] + [p[2] for p in rims]
    lo, hi = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    ext = tuple(b - a for a, b in zip(lo, hi))
    big = max(ext)
    if big <= 0:
        raise CfdError("the parts have no size")
    c = tuple((a + b) / 2 for a, b in zip(lo, hi))
    f = setup.factors
    margin_lo = [f["lateral"] * big] * 3
    margin_hi = [f["lateral"] * big] * 3
    if not setup.hover:
        margin_lo[0], margin_hi[0] = f["upstream"] * big, f["downstream"] * big
    mean_axis = [sum(d.axis[k] for d in disks) for k in range(3)] if disks else [0.0, 0.0, 1.0]
    k = max(range(3), key=lambda i: abs(mean_axis[i]))
    wake_side = (margin_lo, -1) if mean_axis[k] > 0 else (margin_hi, 1)  # wake goes against the thrust axis
    wake_side[0][k] = max(wake_side[0][k], f["wake"] * big)
    dom_lo = tuple(lo[i] - margin_lo[i] for i in range(3))
    dom_hi = tuple(hi[i] + margin_hi[i] for i in range(3))
    pv = setup.preset_values()
    dx0 = big / pv["base_div"]
    cells = tuple(max(1, int(round((b - a) / dx0))) for a, b in zip(dom_lo, dom_hi))
    dx = tuple((b - a) / n for a, b, n in zip(dom_lo, dom_hi, cells))
    # refinement box: the model, a little around it, and the wake side as far as the wake reaches
    r_lo = [lo[i] - 0.4 * big for i in range(3)]
    r_hi = [hi[i] + 0.4 * big for i in range(3)]
    if disks:
        reach = 1.5 * big + 6 * max(d.diameter for d in disks)
        if mean_axis[k] > 0:
            r_lo[k] = max(dom_lo[k] + 0.02 * big, lo[k] - reach)
        else:
            r_hi[k] = min(dom_hi[k] - 0.02 * big, hi[k] + reach)
    r_lo = tuple(max(a, b + 0.01 * big) for a, b in zip(r_lo, dom_lo))
    r_hi = tuple(min(a, b - 0.01 * big) for a, b in zip(r_hi, dom_hi))
    return {"bbox": (lo, hi), "center": c, "extent": ext, "length": big, "domain": (dom_lo, dom_hi), "cells": cells,
            "dx0": max(dx), "dx": dx, "a_ref": max(ext[1] * ext[2], 1e-12), "l_ref": ext[0] or big,
            "refine": (r_lo, r_hi), "wake_axis": k}


def disk_thickness(disk, dx0, preset_name):
    return DISK_LAYERS * dx0 / 2 ** disk_level(disk.diameter, dx0, preset_name)


def estimate_wash(parts, disks, setup, geo=None):
    """Cell count and y+ for the pre-check (cfd.estimate's recipe, with the disk and wake regions added)."""
    geo = geo or wash_geometry(parts, disks, setup)
    pv = setup.preset_values()
    air = cfd.air_properties(setup.temperature_c, setup.altitude_m)
    nbg = geo["cells"][0] * geo["cells"][1] * geo["cells"][2]
    area = sum(cfd.surface_area(p.tris) for p in parts)
    dxs = geo["dx0"] / 2 ** pv["surf"][1]
    n_surf = 4.0 * area / dxs ** 2
    r_lo, r_hi = geo["refine"]
    dxb = geo["dx0"] / 2 ** pv["box"]
    n_box = (r_hi[0] - r_lo[0]) * (r_hi[1] - r_lo[1]) * (r_hi[2] - r_lo[2]) / dxb ** 3
    n_disk = 0.0
    for d in disks:
        lvl = disk_level(d.diameter, geo["dx0"], setup.preset)
        dxd = geo["dx0"] / 2 ** lvl
        n_disk += (math.pi * (0.75 * d.diameter) ** 2 * 2.0 * d.diameter) / dxd ** 3
        n_disk += (math.pi * (1.0 * d.diameter) ** 2 * 5.0 * d.diameter) / (2 * dxd) ** 3
    vmax = max([2 * induced_velocity(d.thrust, air["rho"], d.diameter, hub=d.hub) for d in disks] + [setup.speed, 1e-3])
    layers = pv["layers"]
    first = (0.4 * dxs) / 1.2 ** (layers - 1) if layers else dxs
    length = geo["length"]
    re_l = vmax * length / air["nu"]
    cf = 0.058 * max(re_l, 1.0) ** -0.2
    yplus = (first / 2) * vmax * math.sqrt(cf / 2) / air["nu"]
    return {"cells": int(nbg + n_surf + n_box + n_disk), "background": nbg, "surface_cell_m": dxs,
            "first_cell_m": first / 2, "yplus": yplus, "reynolds": re_l, "area": area, "disk_cells": int(n_disk)}


# ----- dictionaries ---------------------------------------------------------------------------------------


def _vec(v):
    return cfd._vec(v)


def disk_zone_names(disks):
    return {d.name: cfd.safe_name("disk_" + d.name) for d in disks}


def topo_set_dict(disks, geo, setup):
    """cellSet + cellZone per disk: the cells whose centres lie in the cylinder of the disk."""
    zones = disk_zone_names(disks)
    actions = []
    for d in disks:
        t = disk_thickness(d, geo["dx0"], setup.preset)
        p1 = tuple(d.center[k] - d.axis[k] * t / 2 for k in range(3))
        p2 = tuple(d.center[k] + d.axis[k] * t / 2 for k in range(3))
        z = zones[d.name]
        inner = f" innerRadius {d.hub / 2:.9g};" if d.hub > 0 else ""  # the hub: no cells, no force
        actions.append(f"""    {{
        name {z}_cells; type cellSet; action new; source cylinderToCell;
        point1 {_vec(p1)}; point2 {_vec(p2)}; radius {d.diameter / 2:.9g};{inner}
    }}
    {{
        name {z}; type cellZoneSet; action new; source setToCellZone; set {z}_cells;
    }}""")
    return "actions\n(\n" + "\n".join(actions) + "\n);\n"


def fv_options(disks, air, fraction=1.0):
    """One vectorSemiImplicitSource per disk: force on the air -T n, kinematic (/ rho), spread by cell volume.
    ``fraction`` is the part of the disk in the mesh (a wedge holds wedge_deg / 360 of it)."""
    zones = disk_zone_names(disks)
    blocks = []
    for d in disks:
        f = tuple(-d.thrust * fraction / air["rho"] * d.axis[k] for k in range(3))
        z = zones[d.name]
        blocks.append(f"""{z}
{{
    type            vectorSemiImplicitSource;
    selectionMode   cellZone;
    cellZone        {z};
    volumeMode      absolute;
    sources
    {{
        U ({_vec(f)} 0);
    }}
}}""")
    return "\n".join(blocks)


def snappy_extra(text, disks, geo, setup):
    """The phase-1 snappyHexMeshDict with the disk and wake cylinders added as refinement regions, and the refinement
    box replaced by the prop-wash one."""
    pv = setup.preset_values()
    geoms, regions = [], []
    for d in disks:
        lvl = disk_level(d.diameter, geo["dx0"], setup.preset)
        n = d.axis
        D = d.diameter
        up = tuple(d.center[k] + n[k] * 0.5 * D for k in range(3))
        near = tuple(d.center[k] - n[k] * 1.5 * D for k in range(3))
        far = tuple(d.center[k] - n[k] * 6.0 * D for k in range(3))
        zn = cfd.safe_name("near_" + d.name)
        zf = cfd.safe_name("wake_" + d.name)
        geoms.append(f"    {zn}\n    {{\n        type searchableCylinder;\n        point1 {_vec(up)};\n        point2 {_vec(near)};\n"
                     f"        radius {0.75 * D:.9g};\n    }}")
        geoms.append(f"    {zf}\n    {{\n        type searchableCylinder;\n        point1 {_vec(near)};\n        point2 {_vec(far)};\n"
                     f"        radius {1.0 * D:.9g};\n    }}")
        regions.append(f"        {zn} {{ mode inside; levels ((1E15 {lvl})); }}")
        regions.append(f"        {zf} {{ mode inside; levels ((1E15 {max(pv['box'], lvl - 1)})); }}")
    marker = "    refinementBox\n    {\n        type box;"
    if marker not in text:
        raise CfdError("internal: snappyHexMeshDict layout changed")
    text = text.replace(marker, "\n".join(geoms) + "\n" + marker, 1)
    anchor = re.search(r"        refinementBox \{ mode inside; levels \(\(1E15 \d+\)\); \}", text)
    text = text[: anchor.end()] + "\n" + "\n".join(regions) + text[anchor.end():]
    r_lo, r_hi = geo["refine"]
    text = re.sub(r"(refinementBox\s*\{\s*type box;\s*min )\([^)]*\)(;\s*max )\([^)]*\)",
                  lambda m: m.group(1) + _vec(r_lo) + m.group(2) + _vec(r_hi), text, count=1)
    return text


def control_dict_wash(setup, parts, disks, geo, air, iterations, names, groups):
    funcs = []
    for part in parts:
        n, grp = names[part.name], groups[part.name]
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
    }}""")
    zones = disk_zone_names(disks)
    for d in disks:
        z = zones[d.name]
        funcs.append(f"""
    {z}_U
    {{
        type            volFieldValue;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   1;
        log             no;
        writeFields     no;
        regionType      cellZone;
        name            {z};
        operation       volAverage;
        fields          (U);
    }}""")
    for patch in flow_patches(setup):
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


def flow_patches(setup):
    return ["inlet", "outlet"] if not setup.hover else ["inlet", "outlet", "ymin", "ymax", "zmin", "zmax"]


def open_boundary(k0, omega0):
    return {"U": ["type pressureInletOutletVelocity;", "value uniform (0 0 0);"],
            "p": ["type totalPressure;", "p0 uniform 0;", "value uniform 0;"],
            "k": ["type inletOutlet;", f"inletValue uniform {k0:.9g};", f"value uniform {k0:.9g};"],
            "omega": ["type inletOutlet;", f"inletValue uniform {omega0:.9g};", f"value uniform {omega0:.9g};"],
            "nut": ["type calculated;", "value uniform 0;"]}


def hover_fields(groups, turbulence, vref, length):
    """0/U, p, k, omega, nut for still air: all six faces of the box open; walls on the parts."""
    sides = ("inlet", "outlet", "ymin", "ymax", "zmin", "zmax")
    k0 = 1.5 * (0.05 * vref) ** 2
    omega0 = max(math.sqrt(k0) / (0.09 ** 0.25 * 0.07 * max(length, 1e-6)), 1e-3)
    op = open_boundary(k0, omega0)
    walls = {"U": ["type noSlip;"], "p": ["type zeroGradient;"],
             "k": ["type kqRWallFunction;", f"value uniform {k0:.9g};"],
             "omega": ["type omegaWallFunction;", f"value uniform {omega0:.9g};"],
             "nut": ["type nutkWallFunction;", "value uniform 0;"]}
    dims = {"U": ("volVectorField", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)"),
            "p": ("volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0"),
            "k": ("volScalarField", "[0 2 -2 0 0 0 0]", f"uniform {k0:.9g}"),
            "omega": ("volScalarField", "[0 0 -1 0 0 0 0]", f"uniform {omega0:.9g}"),
            "nut": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0")}
    names = ["U", "p"] + ([] if turbulence == "laminar" else ["k", "omega", "nut"])
    out = {}
    for fname in names:
        cls, dim, internal = dims[fname]
        blocks = [cfd._patch_block(s, op[fname]) for s in sides]
        blocks += [cfd._patch_block(g, walls[fname]) for g in groups.values()]
        out[fname] = cfd._field(cls, fname, dim, internal, "\n".join(blocks))
    return out


def with_toposet(allrun):
    """The phase-1 Allrun with a topoSet step after the mesher (the disks' cell zones)."""
    marker = "stage checkMesh"
    if marker not in allrun:
        raise CfdError("internal: Allrun layout changed")
    return allrun.replace(marker, "stage topoSet\ntopoSet > log.topoSet 2>&1 || fail topoSet\n" + marker, 1)


def write_wash_case(folder, parts, disks, setup):
    """Writes the prop-wash case into folder (replacing an old one) and returns the info dict (vectorlabs.json)."""
    if not parts:
        raise CfdError("the study has no parts to put in the flow")
    if not disks:
        raise CfdError("the study has no propellers: mark a motor part with a ThrustAxis")
    if setup.turbulence not in ("kOmegaSST", "laminar"):
        raise CfdError("the turbulence model is k-omega SST or laminar")
    air = cfd.air_properties(setup.temperature_c, setup.altitude_m)
    taken, names, groups = [], {}, {}
    for part in parts:
        names[part.name] = cfd.safe_name(part.name, taken)
        taken.append(names[part.name])
        groups[part.name] = "grp_" + names[part.name]
    # work in the flow frame: parts and disks turned by the angle of attack (forward flight only)
    aoa = 0.0 if setup.hover else setup.aoa
    all_tris = [t for p in parts for t in p.tris]
    lo0, hi0 = cfd.bounds(all_tris)
    about = tuple((a + b) / 2 for a, b in zip(lo0, hi0))
    disks_f = rotated_disks(disks, aoa, about)
    geo = wash_geometry([cfd.Part(p.name, cfd.rotate_y(p.tris, aoa, about) if aoa else p.tris) for p in parts],
                        disks_f, setup)
    refs = {"a_ref": geo["a_ref"], "l_ref": geo["l_ref"]}
    pv = setup.preset_values()
    est = estimate_wash(parts, disks_f, setup, geo)
    n_procs = setup.n_procs or cfd.perf_cores()
    n_procs = max(1, min(n_procs, est["cells"] // 25000 or 1))
    if est["cells"] < getattr(cfd, "MPI_MIN_CELLS", 0) or cfd.mpi_known_broken():  # same rule as cfd.write_case
        n_procs = 1
    iterations = int(pv["iterations"])
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "constant", "triSurface"))
    for part in parts:
        tris = cfd.rotate_y(part.tris, aoa, about) if aoa else part.tris
        cfd.write_stl(os.path.join(folder, "constant", "triSurface", names[part.name] + ".stl"), names[part.name], tris)
    w = cfd._w
    w(folder, "system/controlDict", control_dict_wash(setup, parts, disks_f, geo, air, iterations, names, groups))
    w(folder, "system/fvSchemes", cfd.fv_schemes(setup.turbulence))
    w(folder, "system/fvSolution", cfd.fv_solution(setup.turbulence))
    w(folder, "system/decomposeParDict", cfd.decompose_dict(max(n_procs, 2)))
    dom_lo, dom_hi = geo["domain"]
    w(folder, "system/blockMeshDict", cfd.block_mesh_dict(dom_lo, dom_hi, geo["cells"]))
    snappy = cfd.snappy_dict(setup, parts, geo, names, groups, pv)
    w(folder, "system/snappyHexMeshDict", snappy_extra(snappy, disks_f, geo, setup))
    w(folder, "system/topoSetDict", topo_set_dict(disks_f, geo, setup))
    w(folder, "constant/fvOptions", fv_options(disks_f, air))
    vmax = max(2 * induced_velocity(d.thrust, air["rho"], d.diameter, hub=d.hub) for d in disks_f)
    if setup.hover:
        fields = hover_fields(groups, setup.turbulence, vmax, max(d.diameter for d in disks_f))
    else:
        setup_l = cfd.Setup.from_dict(setup.to_dict())
        setup_l.l_ref = refs["l_ref"]
        fields = cfd.boundary_fields(setup_l, groups, air)
    os.makedirs(os.path.join(folder, "0"), exist_ok=True)
    for fname, body in fields.items():
        with open(os.path.join(folder, "0", fname), "w") as f:
            f.write(body)
    for fname, body in cfd.momentum_properties(setup.turbulence, air["nu"]).items():
        w(folder, "constant/" + fname, body)
    allrun = os.path.join(folder, "Allrun")
    with open(allrun, "w") as f:
        f.write(with_toposet(cfd.allrun_text(n_procs)))
    os.chmod(allrun, 0o755)
    area = sum(d.area for d in disks_f)
    vi = {d.name: induced_velocity(d.thrust, air["rho"], d.diameter, (setup.speed, 0.0, 0.0), d.wake_dir, d.hub)
          for d in disks_f}
    info = {
        "kind": WASH_KIND, "folder": folder,
        "parts": {p.name: {"name": names[p.name], "group": groups[p.name]} for p in parts},
        "disks": [dict(d.to_dict(), zone=disk_zone_names(disks_f)[d.name], v_induced=vi[d.name],
                       thickness=disk_thickness(d, geo["dx0"], setup.preset)) for d in disks_f],
        "thrust_total": sum(d.thrust for d in disks_f), "disk_area": area,
        "flow_ref": sum(vi[d.name] * d.area for d in disks_f),
        "flow_patches": flow_patches(setup),
        "air": air, "setup": setup.to_dict(), "geometry": {k: v for k, v in geo.items()}, "refs": refs,
        "estimate": est, "n_procs": n_procs, "iterations": iterations, "turbulence": setup.turbulence,
    }
    with open(os.path.join(folder, "vectorlabs.json"), "w") as f:
        json.dump(info, f, indent=1, default=list)
    return info


# ----- results ------------------------------------------------------------------------------------------


def _unit(v):
    m = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / m for c in v)


def disk_velocities(folder, info=None):
    """Per disk: the mean velocity in its zone at the last iteration, the part along the wake direction (m/s, the
    induced velocity v_i) and the momentum-theory value."""
    info = info or json.load(open(os.path.join(folder, "vectorlabs.json")))
    out = []
    for d in info["disks"]:
        rows = cfd._latest_dat(folder, d["zone"] + "_U", "volFieldValue.dat")
        if not rows:
            out.append(dict(d, measured=None, velocity=None))
            continue
        u = rows[-1][-3:]
        wake = _unit([-c for c in d["axis"]])
        out.append({"name": d["name"], "label": d.get("label", d["name"]), "thrust": d["thrust"], "diameter": d["diameter"],
                    "hub": d.get("hub", 0.0), "measured": sum(u[k] * wake[k] for k in range(3)), "theory": d["v_induced"], "velocity": u})
    return out


def wash_forces(folder, info=None):
    """Per part and in total: the force on the part (N, flow frame; pressure + viscous) and its component along the
    thrust axis; ``download`` is the force pushing the part with the wake (against the thrust axis), as a fraction of the
    total thrust.  Positive download = thrust lost."""
    info = info or json.load(open(os.path.join(folder, "vectorlabs.json")))
    axis = _unit([sum(d["axis"][k] for d in info["disks"]) for k in range(3)])
    thrust = info["thrust_total"]
    rows, tot = [], [0.0, 0.0, 0.0]
    for part, d in info["parts"].items():
        f = cfd.part_forces(folder, d["name"])
        if not f:
            continue
        fx, fy, fz = f[-1][1:4]
        tot = [tot[0] + fx, tot[1] + fy, tot[2] + fz]
        along = fx * axis[0] + fy * axis[1] + fz * axis[2]
        rows.append({"part": part, "fx": fx, "fy": fy, "fz": fz, "along": along, "download": -along,
                     "fraction": -along / thrust if thrust else None})
    along = tot[0] * axis[0] + tot[1] * axis[1] + tot[2] * axis[2]
    return {"parts": rows, "axis": list(axis), "thrust": thrust,
            "total": {"fx": tot[0], "fy": tot[1], "fz": tot[2], "along": along, "download": -along,
                      "fraction": -along / thrust if thrust else None}}


WASH_RESIDUAL = 1.0e-3  # jets from actuator disks never reach the external-flow limit of 1e-4
SIGNIFICANT_SHARE = 0.02  # a part carrying more than 2 % of the thrust is judged; the others are listed only
VERDICT_LABELS = {"converged": "converged", "unsteady": "steady average of an unsteady flow", "not converged": "not converged"}


def _window_stats(rows, axis, window, change):
    """Mean, swing and drift of a force along `axis` over the last `window` iterations.  swing = (max - min) / |mean|;
    drift = |mean of the window - mean of the window before it| / |mean| (halves of the window on a short run).  None when there are too few iterations."""
    n = len(rows)
    if n < cfd.MIN_WINDOW:
        return None
    w = max(cfd.MIN_WINDOW, min(window, n // 2))
    vals = [r[1] * axis[0] + r[2] * axis[1] + r[3] * axis[2] for r in rows[-w:]]
    mean = sum(vals) / len(vals)
    scale = max(abs(mean), 1e-30)
    if n >= 2 * w:  # the mean of the window against the mean of the window before it
        before = [r[1] * axis[0] + r[2] * axis[1] + r[3] * axis[2] for r in rows[-2 * w:-w]]
        drift = abs(mean - sum(before) / len(before)) / scale
    else:  # a short run: the second half of the window against the first
        h = len(vals) // 2
        drift = abs(sum(vals[h:]) / (len(vals) - h) - sum(vals[:h]) / h) / scale
    swing = (max(vals) - min(vals)) / scale
    return {"mean": mean, "swing": swing, "drift": drift, "window": w, "steady": swing <= change, "averages_ok": drift <= change}


def wash_convergence(folder, info=None, reader=None, residual=WASH_RESIDUAL, window=None, change=None):
    """The verdict of a prop-wash study.  Forces are judged along the thrust axis on the total and on every part that
    carries more than 2 % of the total thrust, over the last ``window`` iterations: the averages must be steady to
    ``change`` (the window's mean against the one before) and the residuals below ``residual``.
    ``converged``: all of it, and the forces swing less than ``change``.  ``unsteady`` (shown as "steady average of an
    unsteady flow"): the averages are steady but the residuals stay above the limit or the forces swing; the mean and
    the swing are reported.  Otherwise ``not converged``.  The mass-flow test of the open box (hover: net flow through
    all faces against the disks' flow; forward flight: inlet against outlet) is part of both positive verdicts."""
    info = info or json.load(open(os.path.join(folder, "vectorlabs.json")))
    window = window or cfd.FORCE_WINDOW
    change = change or cfd.FORCE_CHANGE
    reader = reader or cfd.LogReader(folder).update()
    latest = reader.latest()
    res_ok = bool(latest) and all(v < residual for v in latest.values())
    axis = _unit([sum(d["axis"][k] for d in info["disks"]) for k in range(3)])
    thrust = info["thrust_total"]
    series = {}
    for part, d in info["parts"].items():
        rows = cfd.part_forces(folder, d["name"])
        if rows:
            series[part] = rows
    n = min((len(r) for r in series.values()), default=0)
    total_rows = [(0,) + tuple(sum(r[-n + i][k] for r in series.values()) for k in (1, 2, 3)) for i in range(n)] if n else []
    stats = {"Total": _window_stats(total_rows, axis, window, change)}
    for part, rows in series.items():
        stats[part] = _window_stats(rows, axis, window, change)
    judged, listed = [], []
    for name, st in stats.items():
        if st is None:
            continue
        st["share"] = -st["mean"] / thrust if thrust else 0.0  # download as a share of the thrust, as in wash_forces
        st["name"] = name
        st["judged"] = name == "Total" or abs(st["share"]) > SIGNIFICANT_SHARE
        (judged if st["judged"] else listed).append(st)
    tot = stats["Total"]
    averages_ok = tot is not None and all(st["averages_ok"] for st in judged)
    steady = tot is not None and all(st["steady"] for st in judged)
    balance, flow_ok = None, False
    if info["setup"].get("speed", 0.0) > 1e-9:
        balance = cfd.flow_balance(folder)
        flow_ok = balance is not None and balance < cfd.FLOW_BALANCE
    else:
        net, seen = 0.0, 0
        for p in info.get("flow_patches", []):
            rows = cfd._latest_dat(folder, "flow_" + p, "surfaceFieldValue.dat")
            if rows:
                net += rows[-1][-1]
                seen += 1
        if seen and seen == len(info.get("flow_patches", [])):
            balance = abs(net) / max(info.get("flow_ref", 0.0), 1e-30)
            flow_ok = balance < cfd.FLOW_BALANCE
    if averages_ok and flow_ok and res_ok and steady:
        verdict = "converged"
    elif averages_ok and flow_ok:
        verdict = "unsteady"
    else:
        verdict = "not converged"
    w = tot["window"] if tot else 0
    conv = {"residuals": latest, "residuals_ok": res_ok, "residual_limit": residual, "forces_ok": averages_ok,
            "forces_steady": steady, "force_change": max([st["swing"] for st in judged], default=float("inf")),
            "flow_balance": balance, "flow_ok": flow_ok, "iteration": reader.iteration, "window": w, "verdict": verdict,
            "label": VERDICT_LABELS[verdict], "converged": verdict == "converged", "unsteady": verdict == "unsteady",
            "judged": judged, "listed": listed}
    if tot:
        flow = f"net mass flow through the box {'n/a' if balance is None else f'{balance * 100:.3g} %'} of the disks' flow"
        conv["text"] = (f"residuals {'below' if res_ok else 'not below'} {residual:g}; along the thrust axis over the last {w} "
                        f"iterations the total averages {tot['mean']:.4g} N (swing {tot['swing'] * 100:.2g} %, averages "
                        f"{'steady' if averages_ok else 'not steady'} to {change * 100:g} %); "
                        f"judged parts: {', '.join(st['name'] for st in judged)}; {flow}")
    else:
        conv["text"] = f"not enough iterations yet to judge the forces (at least {cfd.MIN_WINDOW})"
    return conv


def save_summary_wash(analysis, folder, stage_text=""):
    info = json.load(open(os.path.join(folder, "vectorlabs.json")))
    summary = {"kind": WASH_KIND, "convergence": wash_convergence(folder, info), "forces": wash_forces(folder, info),
               "disks": disk_velocities(folder, info), "air": info["air"], "setup": info["setup"],
               "estimate": {k: v for k, v in info["estimate"].items() if k != "geometry"},
               "refs": info["refs"], "iterations": info["iterations"], "stage": stage_text,
               "thrust_total": info["thrust_total"]}
    analysis.CfdSummary = json.dumps(summary, default=str)
    return summary


def _window_table(conv):
    """LaTeX lines of the force statistics over the convergence window (total, judged parts, listed parts)."""
    rows = list(conv.get("judged", [])) + list(conv.get("listed", []))
    if not rows:
        return []
    out = [r"\begin{tabular}{@{}lrrrrl@{}}", r"\toprule",
           r"Part & Mean (N) & Share of thrust & Swing & Drift & Judged\\", r"\midrule"]
    under = r"no (under 2\,\%)"
    for st in rows:
        out.append(f"{cfd._tex(st['name'])} & {st['mean']:.4g} & {st['share'] * 100:.2f}\\,\\% & {st['swing'] * 100:.2g}\\,\\% & "
                   f"{st['drift'] * 100:.2g}\\,\\% & {'yes' if st['judged'] else under}\\\\")
    out += [r"\bottomrule", r"\end{tabular}",
            r"Mean, swing (largest minus smallest over the window) and drift (the window's mean against the mean of the window before it) of the force "
            r"along the thrust axis; parts under 2\,\% of the thrust are listed and do not decide the verdict."]
    return out


def report_section(summary):
    """The airflow section of the LaTeX report for a prop-wash summary."""
    s, a, conv, fr = summary["setup"], summary["air"], summary["convergence"], summary["forces"]
    cfdr = cfd

    def num(v, d=4):
        return "--" if v is None else f"{v:.{d}g}"

    mode = "hover (still air)" if s["speed"] <= 1e-9 else f"forward flight at {s['speed']:.4g}\\,m/s"
    lines = [r"\section*{Prop wash}", r"\subsection*{Setup}", r"\begin{tabular}{@{}ll@{}}",
             f"Case & {mode}, total thrust {summary['thrust_total']:.4g}\\,N\\\\",
             f"Air & {s['temperature_c']:.3g}\\,$^\\circ$C, {s['altitude_m']:.0f}\\,m: $\\rho={a['rho']:.4f}$\\,kg/m$^3$\\\\",
             f"Mesh & {cfdr._tex(s['preset'])} preset, about {summary['estimate']['cells']:,} cells expected\\\\".replace(",", "\\,"),
             r"\end{tabular}", r"\subsection*{Convergence}", r"\begin{itemize}",
             f"\\item Residuals below $10^{{-3}}$: {'yes' if conv['residuals_ok'] else 'no'} (after {conv['iteration']} iterations)",
             f"\\item Forces along the thrust axis over the last {conv.get('window', cfdr.FORCE_WINDOW)} iterations: averages steady to "
             f"0.5\\,\\%: {'yes' if conv['forces_ok'] else 'no'}; swing under 0.5\\,\\%: {'yes' if conv.get('forces_steady', conv['forces_ok']) else 'no'}",
             "\\item Mass flow balance: " + ("not available" if conv["flow_balance"] is None else
                                           f"{conv['flow_balance'] * 100:.3g}\\,\\%"),
             f"\\item Verdict: \\textbf{{{conv.get('label', 'converged' if conv['converged'] else 'not converged')}}}",
             r"\end{itemize}"] + _window_table(conv) + [r"\subsection*{Propellers}", r"\begin{tabular}{@{}lrrrr@{}}", r"\toprule",
             r"Disk & Thrust (N) & $D$ (mm) & $v_i$ momentum (m/s) & $v_i$ CFD (m/s)\\", r"\midrule"]
    for d in summary["disks"]:
        lines.append(f"{cfdr._tex(d['label'])} & {num(d['thrust'])} & {d['diameter'] * 1000:.1f} & {num(d['theory'])} & "
                     f"{num(d['measured'])}\\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\subsection*{Force on the parts}",
              r"\begin{tabular}{@{}lrrrrr@{}}", r"\toprule",
              r"Part & $F_x$ (N) & $F_y$ (N) & $F_z$ (N) & Download (N) & Share of thrust\\", r"\midrule"]
    for r in fr["parts"]:
        pct = "--" if r["fraction"] is None else f"{r['fraction'] * 100:.2f}\\,\\%"
        lines.append(f"{cfdr._tex(r['part'])} & {num(r['fx'])} & {num(r['fy'])} & {num(r['fz'])} & {num(r['download'])} & {pct}\\\\")
    t = fr["total"]
    pct = "--" if t["fraction"] is None else f"{t['fraction'] * 100:.2f}\\,\\%"
    lines += [r"\midrule", f"Total & {num(t['fx'])} & {num(t['fy'])} & {num(t['fz'])} & {num(t['download'])} & {pct}\\\\",
              r"\bottomrule", r"\end{tabular}",
              r"Download is the force on the part along the wake, against the thrust axis; the share is of the total thrust."]
    return "\n".join(lines) + "\n"


# ----- the benchmark: one disk in free air -------------------------------------------------------------------


def _graded(length, d0, ratio):
    """(cells, overall expansion last/first) of a block of `length` whose first cell is d0 and cells grow by `ratio`."""
    if ratio <= 1.0 + 1e-9:
        return max(1, int(round(length / d0))), 1.0
    n = max(1, int(round(math.log(1.0 + length * (ratio - 1.0) / d0) / math.log(ratio))))
    return n, ratio ** (n - 1)


def write_disk_case(folder, thrust=1.0, diameter=0.076, temperature_c=20.0, altitude_m=0.0, turbulence="kOmegaSST",
                    cells_across=48, domain=(4.0, 14.0, 5.0), iterations=1500, wedge_deg=5.0, hub=0.0):
    """A single actuator disk in free air as an axisymmetric wedge about the X axis: the disk at x = 0 pushes the air
    towards +X.  Every face of the domain is open (totalPressure).  The disk's cells are a cellZone made by topoSet (a box
    in the wedge), the source is the same vectorSemiImplicitSource as the study uses.  ``domain`` = (upstream,
    downstream, radius) in diameters; ``hub`` is the hub diameter (m), the hole of the ring.  Ascii output so the benchmark can read the fields."""
    air = cfd.air_properties(temperature_c, altitude_m)
    rho = air["rho"]
    D = diameter
    R = D / 2
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    t = D / 8.0  # the disk's thickness
    dx = D / (cells_across / 2.0)  # near-wake cell
    up_d, down_d, rad_d = domain
    n_up, g_up = _graded(up_d * D - t / 2, dx, 1.08)
    n_s = 4
    near = 5.0 * D
    n_near = max(1, int(round((near - t / 2) / dx)))
    n_far, g_far = _graded(down_d * D - near, dx, 1.05)
    n_in = cells_across // 2
    dr = R / n_in
    n_out, g_out = _graded(rad_d * D - R, dr, 1.08)
    xs = [-up_d * D, -t / 2, t / 2, near, down_d * D]
    rs = [0.0, R, rad_d * D]
    nxs = [n_up, n_s, n_near, n_far]
    gxs = [g_up, 1.0, 1.0, g_far]
    nrs = [n_in, n_out]
    grs = [1.0, g_out]
    a = math.radians(wedge_deg / 2)
    ca, sa = math.cos(a), math.sin(a)
    verts, index = [], {}

    def add(key, p):
        index[key] = len(verts)
        verts.append(p)

    for i, x in enumerate(xs):
        add(("a", i), (x, 0.0, 0.0))
    for j in (1, 2):
        for i, x in enumerate(xs):
            add(("f", j, i), (x, rs[j] * ca, -rs[j] * sa))
            add(("b", j, i), (x, rs[j] * ca, rs[j] * sa))

    def f(j, i):
        return index[("a", i)] if j == 0 else index[("f", j, i)]

    def b(j, i):
        return index[("a", i)] if j == 0 else index[("b", j, i)]

    blocks, front, back, inlet, outlet, top = [], [], [], [], [], []
    for i in range(4):
        for j in range(2):
            blocks.append(f"    hex ({f(j, i)} {f(j, i + 1)} {f(j + 1, i + 1)} {f(j + 1, i)} "
                          f"{b(j, i)} {b(j, i + 1)} {b(j + 1, i + 1)} {b(j + 1, i)}) ({nxs[i]} {nrs[j]} 1) "
                          f"simpleGrading ({gxs[i]:.9g} {grs[j]:.9g} 1)")
            front.append(f"({f(j, i)} {f(j + 1, i)} {f(j + 1, i + 1)} {f(j, i + 1)})")
            back.append(f"({b(j, i)} {b(j, i + 1)} {b(j + 1, i + 1)} {b(j + 1, i)})")
    for j in range(2):
        inlet.append(f"({f(j, 0)} {b(j, 0)} {b(j + 1, 0)} {f(j + 1, 0)})")
        outlet.append(f"({f(j, 4)} {f(j + 1, 4)} {b(j + 1, 4)} {b(j, 4)})")
    for i in range(4):
        top.append(f"({f(2, i)} {b(2, i)} {b(2, i + 1)} {f(2, i + 1)})")
    vtxt = "\n".join(f"    ({p[0]:.9g} {p[1]:.9g} {p[2]:.9g})" for p in verts)
    cfd._w(folder, "system/blockMeshDict", f"""
scale 1;
vertices
(
{vtxt}
);
blocks
(
{chr(10).join(blocks)}
);
edges ();
boundary
(
    inlet  {{ type patch; faces ({' '.join(inlet)}); }}
    outlet {{ type patch; faces ({' '.join(outlet)}); }}
    far    {{ type patch; faces ({' '.join(top)}); }}
    front  {{ type wedge; faces ({' '.join(front)}); }}
    back   {{ type wedge; faces ({' '.join(back)}); }}
);
mergePatchPairs ();
""")
    cfd._w(folder, "system/controlDict", f"""
application simpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime {iterations};
deltaT 1;
writeControl timeStep;
writeInterval {iterations};
purgeWrite 1;
writeFormat ascii;
writePrecision 10;
timeFormat general;
timePrecision 6;
runTimeModifiable true;
""")
    cfd._w(folder, "system/fvSchemes", cfd.fv_schemes(turbulence))
    cfd._w(folder, "system/fvSolution", cfd.fv_solution(turbulence, residual=1e-6, p_residual=1e-6))
    hole = (f"""    {{
        name disk_1_cells; type cellSet; action subtract; source boxToCell;
        box (-{t / 2:.9g} -1 -1) ({t / 2:.9g} {hub / 2:.9g} 1);
    }}
""" if hub > 0 else "")
    cfd._w(folder, "system/topoSetDict", f"""
actions
(
    {{
        name disk_1_cells; type cellSet; action new; source boxToCell;
        box (-{t / 2:.9g} -1 -1) ({t / 2:.9g} {R:.9g} 1);
    }}
{hole}    {{
        name disk_1; type cellZoneSet; action new; source setToCellZone; set disk_1_cells;
    }}
);
""")
    disk = Disk("1", (0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), D, thrust, hub=hub)  # thrust axis -X: the wake goes +X
    cfd._w(folder, "constant/fvOptions", fv_options([disk], air, fraction=wedge_deg / 360.0))
    vi = induced_velocity(thrust, rho, D, hub=hub)
    openb = open_boundary(1.5 * (0.05 * 2 * vi) ** 2, 1.0)
    k0 = 1.5 * (0.05 * 2 * vi) ** 2
    omega0 = max(math.sqrt(k0) / (0.09 ** 0.25 * 0.07 * D), 1e-3)
    openb = open_boundary(k0, omega0)
    dims = {"U": ("volVectorField", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)"),
            "p": ("volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0"),
            "k": ("volScalarField", "[0 2 -2 0 0 0 0]", f"uniform {k0:.9g}"),
            "omega": ("volScalarField", "[0 0 -1 0 0 0 0]", f"uniform {omega0:.9g}"),
            "nut": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0")}
    os.makedirs(os.path.join(folder, "0"), exist_ok=True)
    for fname in ["U", "p"] + ([] if turbulence == "laminar" else ["k", "omega", "nut"]):
        cls, dim, internal = dims[fname]
        body = "\n".join(cfd._patch_block(n, openb[fname]) for n in ("inlet", "outlet", "far")) + "\n" + \
            cfd._patch_block("front", ["type wedge;"]) + "\n" + cfd._patch_block("back", ["type wedge;"])
        with open(os.path.join(folder, "0", fname), "w") as fh:
            fh.write(cfd._field(cls, fname, dim, internal, body))
    for fname, body in cfd.momentum_properties(turbulence, air["nu"]).items():
        cfd._w(folder, "constant/" + fname, body)
    allrun = os.path.join(folder, "Allrun")
    with open(allrun, "w") as fh:
        fh.write(cfd.ALLRUN.format(body="""stage blockMesh
blockMesh > log.blockMesh 2>&1 || fail blockMesh
stage topoSet
topoSet > log.topoSet 2>&1 || fail topoSet
stage simpleFoam
simpleFoam > log.simpleFoam 2>&1 || fail simpleFoam
stage postProcess
postProcess -latestTime -funcs '(writeCellCentres writeCellVolumes)' > log.postProcess 2>&1 || fail postProcess"""))
    os.chmod(allrun, 0o755)
    info = {"kind": "disk_benchmark", "air": air, "thrust": thrust, "diameter": D, "hub": hub, "thickness": t, "v_induced": vi,
            "cells": [nxs, nrs], "iterations": iterations, "turbulence": turbulence}
    with open(os.path.join(folder, "vectorlabs.json"), "w") as fh:
        json.dump(info, fh)
    return info


def disk_benchmark_values(folder, wake_from=0.9, wake_to=1.1, core=0.6):
    """Mean axial velocity (m/s, wedge volume weighted) in the disk's cells, and in the contracted wake (core radius
    ``core`` R) between ``wake_from`` and ``wake_to`` diameters downstream of the disk; also the peak along the axis."""
    info = json.load(open(os.path.join(folder, "vectorlabs.json")))
    D, t = info["diameter"], info["thickness"]
    R = D / 2
    r_hub = info.get("hub", 0.0) / 2
    times = [d for d in os.listdir(folder) if re.fullmatch(r"\d+(\.\d+)?", d) and d != "0"]
    if not times:
        raise CfdError("the disk run wrote no time directory")
    latest = max(times, key=float)
    u = cfd._read_internal_field(os.path.join(folder, latest, "U"), "vector")
    c = cfd._read_internal_field(os.path.join(folder, latest, "C"), "vector")
    v = cfd._read_internal_field(os.path.join(folder, latest, "V"), "scalar")
    sd = vd = sw = vw = 0.0
    axis_peak = (0.0, 0.0)
    for ui, ci, vi_ in zip(u, c, v):
        x, r = ci[0], math.hypot(ci[1], ci[2])
        if abs(x) < t / 2 and r_hub <= r < R:
            sd += ui[0] * vi_
            vd += vi_
        if wake_from * D <= x <= wake_to * D and r < core * R:
            sw += ui[0] * vi_
            vw += vi_
        if r < 0.1 * R and x > t and ui[0] > axis_peak[1]:
            axis_peak = (x / D, ui[0])
    if not vd or not vw:
        raise CfdError("no cells in the disk or the wake window")
    return {"v_disk": sd / vd, "v_wake": sw / vw, "axis_peak": axis_peak[1], "axis_peak_x_over_D": axis_peak[0],
            "v_theory": info["v_induced"], "latest": latest}


# ----- the thrust of every motor (ROB-04) and the pre-check -----------------------------------------------------------


def motor_top(parts, centre, axis):
    """Distance (m) from the motor's centre to the top of its part along the thrust axis, from the smallest part whose
    bounding box holds the centre; None when no part does."""
    best, best_vol = None, None
    for p in parts:
        lo, hi = cfd.bounds(p.tris)
        if all(lo[k] - 1e-3 <= centre[k] <= hi[k] + 1e-3 for k in range(3)):
            vol = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
            if best_vol is None or vol < best_vol:
                best, best_vol = p, vol
    if best is None:
        return None
    return max(sum((pt[k] - centre[k]) * axis[k] for k in range(3)) for t in best.tris for pt in t)


def disks_from_model(model, parts, diameter_mm, offset_mm=0.0, typed_thrust=0.0, report=None, hub_mm=0.0):
    """([Disk], notes): one actuator disk per motor of the assembly (a part with a ThrustAxis).  The thrust is the
    motor's hover share from the Mass & Balance (the weight split by the CG offset), or ``typed_thrust`` N for every
    motor; the disk sits on the motor's axis just above its part, or ``offset_mm`` from the motor's centre.  Raises
    CfdError for a missing diameter or when no motor is marked.

    Each motor's propeller diameter and hub diameter come from properties on the motor part (``PropDiameter``,
    ``HubDiameter``, mm) and fall back to ``diameter_mm`` / ``hub_mm`` of the study; a hub of 0 is HUB_FRACTION of the
    diameter (the disk is a ring: the propeller moves no air inside its hub)."""
    import bruceworx_massreport as mr
    from bruceworx import balance

    report = report or mr.build_report(model.Document, [model])
    try:
        result, notes = mr.balance_of(report)
    except balance.ThrustError as exc:
        raise CfdError(str(exc)) from exc
    motors = mr.motors_of(report)
    if diameter_mm <= 0 and not all(mr.motor_prop_sizes(inst)[0] > 0 for inst, _a, _p in motors):
        raise CfdError("Enter the propeller diameter (mm) in the study: every motor gets a disk of that diameter.")
    notes = list(notes)
    disks = []
    for n, (inst, axis, _path) in enumerate(motors):
        share = result.motors[n]
        thrust = typed_thrust if typed_thrust > 0 else share.share * 1e-3 * G0
        if typed_thrust <= 0 and thrust <= 0:
            raise CfdError(f"{inst.label}: the hover share is zero (the weight is not set or the motors cannot trim it)")
        unit = _unit(axis)
        centre = tuple(c * 1e-3 for c in inst.centre)
        own_d, own_hub = mr.motor_prop_sizes(inst)
        D = (own_d if own_d > 0 else diameter_mm) * 1e-3
        hub = (own_hub if own_hub > 0 else hub_mm if hub_mm > 0 else HUB_FRACTION * D * 1e3) * 1e-3
        if offset_mm > 0:
            off = offset_mm * 1e-3
        else:
            top = motor_top(parts, centre, unit)
            off = (top if top is not None else 0.0) + 0.1 * D
        pos = tuple(centre[k] + unit[k] * off for k in range(3))
        if typed_thrust <= 0 and share.throttle is None:
            notes.append(f"{inst.label}: {share.share:.0f} g is above the thrust table's maximum, so this motor cannot hover")
        disks.append(Disk(f"m{n + 1}", pos, unit, D, thrust, label=f"{inst.label} #{n + 1}", motor=inst.label,
                          hub=hub))
    return disks, notes


def precheck_wash(parts, disks, setup, install="auto", notes=()):
    """Issues as [(level, text)] and the estimate (like cfd.precheck, for a prop-wash study)."""
    issues = []
    if install == "auto":
        install = cfd.find_openfoam()
    if install is None:
        issues.append(("error", "OpenFOAM is not installed. " + cfd.install_text() + " (Install OpenFOAM...)"))
    elif install["warning"]:
        issues.append(("warning", install["warning"]))
    if not parts:
        issues.append(("error", "No parts in the flow. Choose a model with solid bodies."))
        return issues, None
    for p in parts:
        n = cfd.open_edges(p.tris)
        if n:
            issues.append(("error", f"{p.name} is not watertight: {n} open or inconsistent edges. The mesher needs a closed solid."))
    if not disks:
        issues.append(("error", "No propellers: mark a motor part with a ThrustAxis (Mass & Balance)."))
        return issues, None
    for text in notes:
        issues.append(("warning", text))
    try:
        geo = wash_geometry(parts, disks, setup)
        est = estimate_wash(parts, disks, setup, geo)
    except CfdError as exc:
        issues.append(("error", str(exc)))
        return issues, None
    est["geometry"] = geo
    limit = setup.cell_limit or cfd.MAX_CELLS[setup.preset]
    if est["cells"] > limit:
        issues.append(("error", f"About {est['cells']:,} cells expected; the {setup.preset} preset allows {limit:,}. "
                                "Choose a coarser preset or a smaller domain."))
    else:
        issues.append(("info", f"About {est['cells']:,} cells expected (limit {limit:,}), {est['disk_cells']:,} of them "
                               f"around the {len(disks)} disks."))
    air = cfd.air_properties(setup.temperature_c, setup.altitude_m)
    for d in disks:
        vi = induced_velocity(d.thrust, air["rho"], d.diameter, hub=d.hub)
        hub = f" with a {d.hub * 1000:.0f} mm hub" if d.hub > 0 else ""
        issues.append(("info", f"{d.label}: thrust {d.thrust:.3g} N on a {d.diameter * 1000:.0f} mm disk{hub}: induced velocity "
                               f"{vi:.2f} m/s at the disk, {2 * vi:.2f} m/s in the far wake."))
    if est["yplus"] > 300 and setup.turbulence != "laminar":
        issues.append(("warning", f"y+ is about {est['yplus']:.0f}; wall functions want 30 to 300. Choose a finer preset."))
    # a disk inside a part: the zone loses its cells there, and the propeller is probably already modelled
    for d in disks:
        for p in parts:
            lo, hi = cfd.bounds(p.tris)
            if all(lo[k] <= d.center[k] <= hi[k] for k in range(3)) and cfd.volume(p.tris) > 0:
                issues.append(("warning", f"The centre of the disk of {d.label} is inside {p.name}: move the disk up with "
                                          "'Disk offset', or take the propeller out of the model."))
                break
    if setup.speed > 100:
        issues.append(("warning", "Above about 100 m/s (Mach 0.3) the incompressible solver is not valid."))
    return issues, est


# ----- the study (glue) ---------------------------------------------------------------------------------------


WASH_PROPS = (
    ("App::PropertyFloat", "PropDiameter", "Propeller diameter in mm (one disk of this diameter on every marked motor)", 0.0),
    ("App::PropertyFloat", "HubDiameter", "Propeller hub diameter in mm for motors that do not carry their own; "
                                           "0 = 20 % of the propeller diameter (the disk is a ring)", 0.0),
    ("App::PropertyFloat", "DiskOffset", "Distance in mm from the motor's centre along its ThrustAxis to the disk; "
                                          "0 = just above the motor part", 0.0),
    ("App::PropertyFloat", "TypedThrust", "Thrust per motor in N; 0 = the hover share from the Mass & Balance", 0.0),
    ("App::PropertyFloat", "DomainWake", "Distance of the domain on the wake side, in characteristic lengths",
     WASH_FACTORS["wake"]),
)


def ensure_wash_properties(analysis):
    for typ, name, doc, default in WASH_PROPS:
        if name not in analysis.PropertiesList:
            analysis.addProperty(typ, name, "Airflow", "[BW] " + doc)
            setattr(analysis, name, default)


def init_study(analysis):
    """New prop-wash study: hover, the prop-wash domain factors."""
    ensure_wash_properties(analysis)
    analysis.FlowSpeed = 0.0
    analysis.DomainUpstream = WASH_FACTORS["upstream"]
    analysis.DomainDownstream = WASH_FACTORS["downstream"]
    analysis.DomainLateral = WASH_FACTORS["lateral"]
    analysis.DomainWake = WASH_FACTORS["wake"]


def setup_of(analysis):
    g = lambda n, d=None: getattr(analysis, n) if n in analysis.PropertiesList else d
    return WashSetup(speed=g("FlowSpeed", 0.0), aoa=g("AngleOfAttack", 0.0), temperature_c=g("AirTemperature", 20.0),
                     altitude_m=g("AirAltitude", 0.0), preset=g("MeshPreset", cfd.DEFAULT_PRESET) or cfd.DEFAULT_PRESET,
                     turbulence=g("Turbulence", "kOmegaSST") or "kOmegaSST", farfield=g("FarField", "slip") or "slip",
                     factors={"upstream": g("DomainUpstream", WASH_FACTORS["upstream"]),
                              "downstream": g("DomainDownstream", WASH_FACTORS["downstream"]),
                              "lateral": g("DomainLateral", WASH_FACTORS["lateral"]),
                              "wake": g("DomainWake", WASH_FACTORS["wake"])})


def study_disks(analysis, parts=None):
    parts = parts if parts is not None else cfd.study_parts(analysis)
    g = lambda n, d=0.0: getattr(analysis, n) if n in analysis.PropertiesList else d
    return disks_from_model(analysis.Model, parts, g("PropDiameter"), g("DiskOffset"), g("TypedThrust"),
                            hub_mm=g("HubDiameter"))


def study_precheck(analysis):
    parts = cfd.study_parts(analysis)
    setup = setup_of(analysis)
    try:
        disks, notes = study_disks(analysis, parts)
    except CfdError as exc:
        issues, _est = precheck_wash(parts, [], setup)
        return [("error", str(exc))] + [i for i in issues if "No propellers" not in i[1]], None
    return precheck_wash(parts, disks, setup, notes=notes)


def prepare_run(analysis, root=None):
    parts = cfd.study_parts(analysis)
    setup = setup_of(analysis)
    disks, notes = study_disks(analysis, parts)
    issues, _est = precheck_wash(parts, disks, setup, notes=notes)
    errors = [t for lvl, t in issues if lvl == "error"]
    if errors:
        raise CfdError(" ".join(errors))
    folder = cfd.study_folder(analysis, root)
    info = write_wash_case(folder, parts, disks, setup)
    analysis.CaseFolder = folder
    return folder, info


def plot_series(folder, part_name, info, last=2000):
    """[(iteration, force along the thrust axis in N)] of a part, for the live force plot (negative = download)."""
    axis = _unit([sum(d["axis"][k] for d in info["disks"]) for k in range(3)])
    return [(r[0], r[1] * axis[0] + r[2] * axis[1] + r[3] * axis[2]) for r in cfd.part_forces(folder, part_name)[-last:]]


# ----- the validation hook (SIM-05, kind "cfd") ---------------------------------------------------------------------


def run_cfd_case(case, size, work_dir, progress=None):
    """bruceworx_validation runner for the CFD cases: ``size`` is the cell size at the disk in mm (the disk is
    diameter / size cells across).  Returns the same result dict as run_fe_case; never raises."""
    import time

    import bruceworx_validation as val

    out = {"id": case["id"], "title": case.get("title", ""), "kind": "cfd", "size": size, "nodes": None, "elements": None,
           "seconds": 0.0, "values": {}, "rows": [], "error": None, "mark": "ERROR"}
    t0 = time.time()
    try:
        builder = case.get("builder")
        if builder != "actuator_disk":
            out["mark"], out["error"] = "SKIPPED", f"no CFD builder {builder!r}"
            out["rows"] = val.evaluate(case, {})
            return out
        install = cfd.find_openfoam()
        if install is None:
            out["mark"], out["error"] = "SKIPPED", "OpenFOAM is not installed"
            out["rows"] = val.evaluate(case, {})
            return out
        p = case["params"]
        across = max(8, 2 * int(round(p["diameter_mm"] / size / 2.0)))
        folder = os.path.join(work_dir, f"{case['id']}_{size:g}")
        info = write_disk_case(folder, thrust=p["thrust_N"], diameter=p["diameter_mm"] * 1e-3, cells_across=across,
                               iterations=int(p.get("iterations", 1500)))
        run = cfd.Runner(folder, install).start()
        run.wait()
        if run.result() != "done":
            raise CfdError(f"OpenFOAM {run.result()}: {cfd._read(os.path.join(folder, 'log.run'))[-300:]}")
        out["values"] = {k: v for k, v in disk_benchmark_values(folder).items() if isinstance(v, (int, float))}
        cells = sum(info["cells"][0][i] * info["cells"][1][j] for i in range(4) for j in range(2))
        out["nodes"] = out["elements"] = cells
        out["rows"] = val.evaluate(case, out["values"])
        out["mark"] = val.mark(case, out["rows"])
    except Exception as exc:  # a failing case is a result, not a crash of the set
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["rows"] = val.evaluate(case, {})
        out["mark"] = "ERROR"
    out["seconds"] = time.time() - t0
    return out


def register():
    """Hand the "cfd" cases to bruceworx_validation (done on first use; see bruceworx_validation._runner_for)."""
    import bruceworx_validation as val

    val.register_runner("cfd", run_cfd_case)
