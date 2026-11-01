# SPDX-License-Identifier: LGPL-2.1-or-later
"""ParaView and streamlines for the airflow results (SIM-06, 31 Oct 2026; built record in plan chapter 67e).

Three pieces, all working from a finished case folder (vectorlabs.json, the OpenFOAM time directories and, when
foamToVTK has run, VTK/<time>/internal.vtu plus the boundary .vtp files):

1. ``open_in_paraview``: writes ``<case>/paraview/view.py`` (a ParaView Python script: the case loaded, surface pressure on
   the parts, a mid cut plane coloured by |U|, streamlines seeded upstream of the body or round each propeller disk,
   a camera and legends with units) and starts ParaView with ``--script``.
2. ``start_render``: runs ``pvbatch`` on ``<case>/paraview/render.py`` (the same script, offscreen) in a child process and
   leaves iso / side / cut PNGs in ``<case>/paraview``; ``RenderJob`` is polled by the GUI, times out and degrades when
   ParaView is missing.  ``report_figures`` gives the report its LaTeX.
3. ``compute_streamlines``: vtkStreamTracer from the VTK that ships with the app, on the millimetre copy of the volume,
   written as ``streamlines.vtp`` beside it and shown in the 3D view by ``show_streamlines``.

ParaView cannot be embedded (different Qt and VTK): it is always a separate process.  The script writers need no
FreeCAD and no VTK, so they can be tested with plain Python.
"""

import glob
import json
import os
import pprint
import subprocess
import time

import bruceworx_cfd as cfd

PV_DIR = "paraview"
VIEW_SCRIPT = "view.py"
RENDER_SCRIPT = "render.py"
RENDER_LOG = "render.log"
IMAGES = (("iso", "Isometric view: pressure on the part and streamlines coloured by speed"),
          ("side", "Side view: pressure on the part and streamlines coloured by speed"),
          ("cut", "Mid cut plane coloured by the speed $|U|$, with the pressure on the part"))
RENDER_TIMEOUT = 300.0  # seconds; pvbatch needs about 10 s to start and 20 s for a 500k-cell case
WASH_KINDS = (("axis", "View along the thrust axis from the wake side: the speed on a plane half a propeller diameter below the disks"),
              ("wake", "The wake as a surface of constant speed at half the induced velocity ($%s$\\,m/s)"))
SEEDS = 200  # default number of streamlines
SIZE = (1400, 900)


def _json(folder):
    return json.load(open(os.path.join(folder, "vectorlabs.json")))


def pv_dir(folder):
    return os.path.join(folder, PV_DIR)


def image_path(folder, kind):
    return os.path.join(pv_dir(folder), f"pv_{kind}.png")


def perpendicular(v):
    """A unit vector at right angles to v."""
    n = sum(c * c for c in v) ** 0.5 or 1.0
    v = [c / n for c in v]
    a = (1.0, 0.0, 0.0) if abs(v[0]) < 0.9 else (0.0, 1.0, 0.0)
    w = [v[1] * a[2] - v[2] * a[1], v[2] * a[0] - v[0] * a[2], v[0] * a[1] - v[1] * a[0]]
    m = sum(c * c for c in w) ** 0.5
    return [c / m for c in w]


def disk_pairs(info):
    """[(disk, opposite disk or None)] for the vertical cuts of a prop wash: every disk is paired with the one nearest to its
    mirror image about the centroid of all disks; a pair appears once.  A single disk, or one with no partner, stands alone."""
    disks = list(info.get("disks") or [])
    if not disks:
        return []
    cen = [sum(d["center"][k] for d in disks) / len(disks) for k in range(3)]
    used, pairs = set(), []
    for i, d in enumerate(disks):
        if i in used:
            continue
        used.add(i)
        mirror = [2 * cen[k] - d["center"][k] for k in range(3)]
        best, dist = None, None
        for j, e in enumerate(disks):
            if j in used:
                continue
            dd = sum((e["center"][k] - mirror[k]) ** 2 for k in range(3)) ** 0.5
            if dist is None or dd < dist:
                best, dist = j, dd
        if best is not None and dist <= 0.5 * d["diameter"]:
            used.add(best)
            pairs.append((d, disks[best]))
        else:
            pairs.append((d, None))
    return pairs


def wash_plan(info):
    """The prop-wash picture set: the thrust axis, the wake plane, the vertical cuts and the speed of the wake surface; {} for
    an external-flow case.  Lengths in metres, case frame."""
    disks = info.get("disks") or []
    if not disks:
        return {}
    axis = [sum(d["axis"][k] for d in disks) for k in range(3)]
    n = sum(a * a for a in axis) ** 0.5 or 1.0
    axis = [a / n for a in axis]
    cen = [sum(d["center"][k] for d in disks) / len(disks) for k in range(3)]
    dia = sum(d["diameter"] for d in disks) / len(disks)
    plane = [cen[k] - 0.5 * dia * axis[k] for k in range(3)]  # half a diameter downstream of the disks
    cuts = []
    for i, (a, b) in enumerate(disk_pairs(info)):
        line = [(b["center"][k] if b else cen[k]) - a["center"][k] for k in range(3)]
        along = sum(line[k] * axis[k] for k in range(3))
        line = [line[k] - along * axis[k] for k in range(3)]
        if sum(c * c for c in line) ** 0.5 < 1e-9:
            line = perpendicular(axis)
        m = sum(c * c for c in line) ** 0.5
        line = [c / m for c in line]
        normal = [axis[1] * line[2] - axis[2] * line[1], axis[2] * line[0] - axis[0] * line[2], axis[0] * line[1] - axis[1] * line[0]]
        names = (a.get("label") or a["name"]) + ((" and " + (b.get("label") or b["name"])) if b else "")
        cuts.append({"kind": f"cut{i + 1}", "origin": list(a["center"]), "normal": normal, "title": f"Cut through {names}",
                     "caption": f"Vertical cut through {cfd._tex(names)}: speed $|U|$ in the plane of the thrust axis"})
    vi = sum(d.get("v_induced", 0.0) for d in disks) / len(disks)
    return {"axis": axis, "plane_origin": plane, "up": perpendicular(axis), "cuts": cuts, "iso_speed": 0.5 * vi, "vi": vi}


def picture_kinds(folder_or_info=None):
    """[(kind, caption)] of the pictures a case gets: the three generic ones, and for a prop wash the thrust-axis view, a cut
    through each pair of opposite disks and the wake surface."""
    info = folder_or_info
    try:
        if isinstance(info, str):
            info = _json(info)
        plan = wash_plan(info) if info else {}
    except Exception:
        plan = {}
    if not plan:
        return list(IMAGES)
    out = list(IMAGES) + [WASH_KINDS[0]] + [(c["kind"], c["caption"]) for c in plan["cuts"]]
    out.append((WASH_KINDS[1][0], WASH_KINDS[1][1] % f"{plan['iso_speed']:.3g}"))
    return out


def picture_titles(folder_or_info=None):
    """{kind: short name} for the picture list: the motors' own labels in the cuts ("Cut through Motor 1 and Motor 3")."""
    info = folder_or_info
    try:
        if isinstance(info, str):
            info = _json(info)
        plan = wash_plan(info) if info else {}
    except Exception:
        plan = {}
    titles = {"iso": "Isometric", "side": "Side", "cut": "Cut plane", "axis": "Along the thrust axis", "wake": "Wake surface"}
    titles.update({c["kind"]: c["title"] for c in plan.get("cuts", [])})
    return titles


def images(folder):
    """{kind: png} of the background renders that exist (non-empty) for a case."""
    out = {}
    for kind, _caption in picture_kinds(folder):
        p = image_path(folder, kind)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            out[kind] = p
    return out


# ----- what is in the case -------------------------------------------------------------------------------------


def latest_vtk(folder):
    """The newest VTK/<time> directory with an internal.vtu, or None."""
    dirs = sorted(glob.glob(os.path.join(folder, "VTK", "*", "internal.vtu")), key=os.path.getmtime)
    return os.path.dirname(dirs[-1]) if dirs else None


def far_field_patch(name):
    return os.path.splitext(os.path.basename(name))[0] in cfd.FARFIELD_PATCHES


def domain_of(info):
    """(lo, hi) of the flow domain: from the case, else the body's box grown by three body lengths each way."""
    geo = info["geometry"]
    if geo.get("domain"):
        return geo["domain"]
    lo, hi = geo["bbox"]
    length = max(h - l for l, h in zip(lo, hi)) or 1.0
    return [[l - 3 * length for l in lo], [h + 3 * length for h in hi]]


def seed_plan(info, count=SEEDS):
    """Where the streamlines start, in metres in the case frame: a list of {"center", "radius", "n"} point clouds.

    External flow: one cloud upstream of the body (free stream is +X), as wide as the body.  Prop wash: one cloud over
    each disk, on the side the air is drawn in from (against the wake), as wide as the disk.  Clamped to the domain."""
    geo = info["geometry"]
    lo, hi = geo["bbox"]
    dom_lo, dom_hi = domain_of(info)
    ext = [h - l for l, h in zip(lo, hi)]
    centre = [(l + h) / 2 for l, h in zip(lo, hi)]
    clouds = []
    disks = info.get("disks") or []
    if disks:
        n = max(20, int(count) // len(disks))
        for d in disks:
            dia = d["diameter"]
            c = [d["center"][k] + 0.3 * dia * d["axis"][k] for k in range(3)]  # above: the side the air comes from
            clouds.append({"center": c, "radius": 0.6 * dia, "n": n, "label": d.get("label") or d.get("name")})
    else:
        length = max(ext)
        r = 0.6 * max(ext[1], ext[2], 0.1 * length)
        r = min(r, 0.8 * (dom_hi[1] - dom_lo[1]) / 2, 0.8 * (dom_hi[2] - dom_lo[2]) / 2)
        x = max(lo[0] - r - 0.2 * length, dom_lo[0] + 0.5 * (lo[0] - dom_lo[0]))  # the whole cloud in front of the body
        clouds.append({"center": [x, centre[1], centre[2]], "radius": r, "n": int(count), "label": "upstream"})
    return clouds


def region_of_interest(info):
    """(lo, hi) metres of what the cameras look at: the body (and the disks) with room for the wake behind it."""
    geo = info["geometry"]
    lo, hi = [list(v) for v in geo["bbox"]]
    for d in info.get("disks") or []:
        r = d["diameter"] / 2
        for k in range(3):
            lo[k] = min(lo[k], d["center"][k] - r)
            hi[k] = max(hi[k], d["center"][k] + r)
    length = max(h - l for l, h in zip(lo, hi))
    speed = (info.get("setup") or {}).get("speed", 1.0) or 0.0
    hover = bool(info.get("disks")) and speed <= 1e-9
    wake = geo.get("wake_axis")
    out_lo, out_hi = list(lo), list(hi)
    if hover and info.get("disks"):  # the wake goes along the disks' wake direction (down)
        ax = [sum(-d["axis"][k] for d in info["disks"]) for k in range(3)]
        n = sum(a * a for a in ax) ** 0.5 or 1.0
        for k in range(3):
            reach = 2.5 * length * ax[k] / n
            if reach > 0:
                out_hi[k] += reach
            else:
                out_lo[k] += reach
        for k in range(3):
            out_lo[k] -= 0.3 * length
            out_hi[k] += 0.3 * length
    else:
        out_lo[0] -= 0.5 * length
        out_hi[0] += 2.0 * length
        for k in (1, 2):
            out_lo[k] -= 0.8 * length
            out_hi[k] += 0.8 * length
    del wake
    dom_lo, dom_hi = domain_of(info)
    return [max(a, b) for a, b in zip(out_lo, dom_lo)], [min(a, b) for a, b in zip(out_hi, dom_hi)]


def speed_range(info):
    """A guess of the largest speed for the colour bars (m/s) when nothing is known better: the data range is used
    by the script, this only sets a floor so a 0.01 m/s test does not show noise."""
    setup = info.get("setup") or {}
    vmax = max([setup.get("speed") or 0.0] + [2 * d.get("v_induced", 0.0) for d in info.get("disks") or []])
    return vmax


# ----- the ParaView script ---------------------------------------------------------------------------------------

SCRIPT = r'''# Vector Labs airflow view for ParaView (written by bruceworx_cfd_paraview; edit freely, it is rewritten on each open).
# Run: paraview --script=view.py    or    pvbatch render.py   (the second one saves pictures and exits).
import math
import os
from paraview.simple import *

CFG = __CFG__
MODE = CFG["mode"]  # "gui" or "batch"
paraview.simple._DisableFirstRenderCameraReset()


def lookup(path):
    return path if os.path.exists(path) else None


# --- the data: foamToVTK output when it exists, else the OpenFOAM reader on case.foam ---------------------------
internal = None
parts = []
t_last = 0.0
if CFG["vtu"] and lookup(CFG["vtu"]):
    internal = XMLUnstructuredGridReader(registrationName="Volume", FileName=[CFG["vtu"]])
    internal.UpdatePipeline()
    for p in CFG["patches"]:
        if lookup(p):
            parts.append(XMLPolyDataReader(registrationName=os.path.basename(p), FileName=[p]))
else:
    foam = OpenFOAMReader(registrationName="case.foam", FileName=CFG["foam"])
    foam.MeshRegions = ["internalMesh"]
    foam.CellArrays = ["U", "p"]
    foam.UpdatePipelineInformation()
    times = list(foam.TimestepValues) if foam.TimestepValues else []
    t_last = times[-1] if times else 0.0
    foam.UpdatePipeline(t_last)
    internal = CellDatatoPointData(registrationName="Volume", Input=MergeBlocks(Input=foam))
    # the stream tracer misreads a time-dependent reader pipeline in this ParaView: work from a plain file instead
    flat = os.path.join(os.path.dirname(os.path.abspath(CFG["foam"])), "paraview", "volume_%s.vtu" % ("gui" if MODE == "gui" else "batch"))
    os.makedirs(os.path.dirname(flat), exist_ok=True)
    scene = GetAnimationScene()
    scene.UpdateAnimationUsingDataTimeSteps()
    scene.GoToLast()
    SaveData(flat, proxy=internal, PointDataArrays=["U", "p"], CellDataArrays=[], ChooseArraysToWrite=1)
    internal = XMLUnstructuredGridReader(registrationName="Volume", FileName=[flat])
    avail = [r for r in foam.GetProperty("MeshRegions").Available if r.startswith("patch/")]
    wanted = [r for r in avail if r.split("/", 1)[1] not in CFG["farfield"]]
    if wanted:
        pf = OpenFOAMReader(registrationName="Parts", FileName=CFG["foam"])
        pf.MeshRegions = wanted
        pf.CellArrays = ["U", "p"]
        pf.UpdatePipeline(t_last)
        parts = [CellDatatoPointData(registrationName="Surface", Input=MergeBlocks(Input=pf))]

view = GetActiveViewOrCreate("RenderView")
view.OrientationAxesVisibility = 1
view.Background = [1.0, 1.0, 1.0] if CFG.get("light", True) else [0.12, 0.13, 0.15]
if hasattr(view, "UseColorPaletteForBackground"):
    view.UseColorPaletteForBackground = 0
text_rgb = [0.1, 0.1, 0.1]
if MODE == "batch":
    view.ViewSize = CFG["size"]

# --- colour maps: speed in m/s, pressure in Pa (OpenFOAM's p is kinematic: times the density) -----------------------
speed_lut = GetColorTransferFunction("U")


def preset(lut, *names):
    for name in names:
        try:
            if lut.ApplyPreset(name, True):
                return
        except RuntimeError:
            continue


preset(speed_lut, "Viridis", "Viridis (matplotlib)", "Turbo")
speed_lut.VectorMode = "Magnitude"
pressure_lut = GetColorTransferFunction("Pressure")
preset(pressure_lut, "Cool to Warm")


def legend(display, lut, title, where):
    display.SetScalarBarVisibility(view, True)
    bar = GetScalarBar(lut, view)
    bar.Title = title
    bar.ComponentTitle = ""
    bar.TitleColor = text_rgb
    bar.LabelColor = text_rgb
    bar.TitleFontSize = 16
    bar.LabelFontSize = 14
    bar.RangeLabelFormat = "{:.3g}"
    bar.WindowLocation = where
    bar.ScalarBarLength = 0.32
    bar.TextPosition = "Ticks left/bottom, annotations right/top" if "Right" in where else "Ticks right/top, annotations left/bottom"
    return bar


# --- surface pressure on the parts ----------------------------------------------------------------------------------
surface = None
if parts:
    merged = MergeBlocks(registrationName="Parts", Input=parts[0] if len(parts) == 1 else GroupDatasets(Input=parts))
    surface = Calculator(registrationName="Pressure (Pa)", Input=merged)
    surface.Function = "p*%r" % CFG["rho"]
    surface.ResultArrayName = "Pressure"
    surface_display = Show(surface, view)
    surface_display.Representation = "Surface"
    ColorBy(surface_display, ("POINTS", "Pressure"))
    surface.UpdatePipeline(t_last)
    lo, hi = surface.PointData["Pressure"].GetRange()
    m = max(abs(lo), abs(hi), 1e-12)
    pressure_lut.AutomaticRescaleRangeMode = "Never"
    pressure_lut.RescaleTransferFunction(-m, m)  # symmetric about zero: suction blue, stagnation red
    legend(surface_display, pressure_lut, "Pressure p [Pa]", "Upper Left Corner")

# --- the mid cut plane coloured by |U| ----------------------------------------------------------------------------
cut = Slice(registrationName="Cut plane (XZ)", Input=internal)
cut.SliceType = "Plane"
cut.SliceType.Origin = CFG["cut_origin"]
cut.SliceType.Normal = [0.0, 1.0, 0.0]
cut_display = Show(cut, view)
cut_display.Representation = "Surface"
ColorBy(cut_display, ("POINTS", "U", "Magnitude"))
cut_display.Opacity = CFG["cut_opacity"]
cut_display.Ambient = 0.55
cut_display.Diffuse = 0.5
cut.UpdatePipeline(t_last)
try:  # the 99.5th percentile: a few wild cells next to a wall must not squash the colours
    from vtkmodules.util.numpy_support import vtk_to_numpy
    import numpy as np
    sample = servermanager.Fetch(cut).GetPointData().GetArray("U")
    vmax = float(np.percentile(np.linalg.norm(vtk_to_numpy(sample), axis=1), 99.5))
except Exception:
    vmax = cut.PointData["U"].GetRange(-1)[1]
vmax = max(vmax, CFG["speed_floor"], 1e-9)
speed_lut.AutomaticRescaleRangeMode = "Never"  # the streamlines must not rescale the colours
speed_lut.RescaleTransferFunction(0.0, vmax)
speed_bar = legend(cut_display, speed_lut, "Speed magnitude [m/s]", "Upper Right Corner")

# --- streamlines seeded upstream of the body (or over each propeller disk) ------------------------------------------
lines = []
for i, cloud in enumerate(CFG["seeds"]):
    st = StreamTracer(registrationName="Streamlines %d" % (i + 1), Input=internal, SeedType="Point Cloud")
    st.Vectors = ["POINTS", "U"]
    st.SeedType.Center = cloud["center"]
    st.SeedType.Radius = cloud["radius"]
    st.SeedType.NumberOfPoints = cloud["n"]
    st.IntegrationDirection = "BOTH"
    st.MaximumStreamlineLength = CFG["max_length"]
    tube = Tube(registrationName="Stream tubes %d" % (i + 1), Input=st)
    tube.Radius = CFG["tube_radius"]
    tube.NumberofSides = 8
    d = Show(tube, view)
    d.Representation = "Surface"
    ColorBy(d, ("POINTS", "U", "Magnitude"))
    d.SetScalarBarVisibility(view, False)
    lines.append(d)

speed_lut.RescaleTransferFunction(0.0, vmax)  # again: adding the tubes must not change the colours
if surface is not None:
    pressure_lut.RescaleTransferFunction(-m, m)

# --- the prop-wash set: a plane across the wake, a cut through each pair of opposite disks, the wake surface -----------
wash_displays = {}
WASH = CFG.get("wash")
if WASH:
    plane = Slice(registrationName="Wake plane", Input=internal)
    plane.SliceType = "Plane"
    plane.SliceType.Origin = WASH["plane_origin"]
    plane.SliceType.Normal = WASH["axis"]
    wash_displays["axis"] = Show(plane, view)
    for c in WASH["cuts"]:
        cp = Slice(registrationName="Cut " + c["kind"], Input=internal)
        cp.SliceType = "Plane"
        cp.SliceType.Origin = c["origin"]
        cp.SliceType.Normal = c["normal"]
        wash_displays[c["kind"]] = Show(cp, view)
    for d in wash_displays.values():
        d.Representation = "Surface"
        ColorBy(d, ("POINTS", "U", "Magnitude"))
        d.SetScalarBarVisibility(view, False)
        d.Visibility = 0
    speed = Calculator(registrationName="Speed", Input=internal)
    speed.Function = "mag(U)"
    speed.ResultArrayName = "speed"
    wake = Contour(registrationName="Wake surface", Input=speed)
    wake.ContourBy = ["POINTS", "speed"]
    wake.Isosurfaces = [WASH["iso_speed"]]
    wake_display = Show(wake, view)
    wake_display.Representation = "Surface"
    wake_display.ColorArrayName = ["POINTS", ""]
    wake_display.AmbientColor = wake_display.DiffuseColor = [0.2, 0.5, 0.85]
    wake_display.Opacity = 0.55
    wake_display.Visibility = 0
    wash_displays["wake"] = wake_display
    speed_lut.RescaleTransferFunction(0.0, vmax)

# --- cameras --------------------------------------------------------------------------------------------------------
lo_roi, hi_roi = CFG["roi"]


def camera(direction, up=(0.0, 0.0, 1.0)):
    focus = [(a + b) / 2 for a, b in zip(lo_roi, hi_roi)]
    radius = 0.5 * sum((b - a) ** 2 for a, b in zip(lo_roi, hi_roi)) ** 0.5
    norm = sum(d * d for d in direction) ** 0.5
    dist = 0.75 * radius / math.sin(math.radians(15))  # the region's bounding sphere fills most of a 30 degree view
    view.CameraFocalPoint = focus
    view.CameraPosition = [f + dist * d / norm for f, d in zip(focus, direction)]
    view.CameraViewUp = list(up)
    view.CameraViewAngle = 30
    view.CameraParallelProjection = 0


def show_only(cut_on, lines_on, opacity):
    for d in wash_displays.values():
        d.Visibility = 0
        d.SetScalarBarVisibility(view, False)
    cut_display.Visibility = 1 if cut_on else 0
    cut_display.Opacity = opacity
    for d in lines:
        d.Visibility = 1 if lines_on else 0
    if cut_on:
        speed_bar.Visibility = 1
    else:
        cut_display.SetScalarBarVisibility(view, False)
        if lines:
            lines[0].SetScalarBarVisibility(view, lines_on)
            if lines_on:
                legend(lines[0], speed_lut, "Speed magnitude [m/s]", "Upper Right Corner")


ISO = CFG["iso_dir"]
if MODE == "batch":
    for kind, name in CFG["pictures"]:
        if kind == "iso":
            show_only(False, True, 1.0)
            camera(ISO)
        elif kind == "side":
            show_only(False, True, 1.0)
            camera((0.0, -1.0, 0.0))
        elif WASH and kind == "axis":  # from the wake side, looking up the thrust axis, through the translucent plane
            show_only(False, False, 1.0)
            wash_displays["axis"].Visibility = 1
            wash_displays["axis"].Opacity = 0.85
            legend(wash_displays["axis"], speed_lut, "Speed magnitude [m/s]", "Upper Right Corner")
            camera([-a for a in WASH["axis"]], WASH["up"])
        elif WASH and kind == "wake":
            show_only(False, False, 1.0)
            wash_displays["wake"].Visibility = 1
            camera(ISO)
        elif WASH and kind in wash_displays:
            c = [c for c in WASH["cuts"] if c["kind"] == kind][0]
            show_only(False, False, 1.0)
            wash_displays[kind].Visibility = 1
            legend(wash_displays[kind], speed_lut, "Speed magnitude [m/s]", "Upper Right Corner")
            camera(c["normal"], WASH["axis"])
        else:
            show_only(True, False, 1.0)
            camera(ISO)
        Render(view)
        SaveScreenshot(name, view, ImageResolution=CFG["size"], TransparentBackground=0)
else:
    show_only(True, True, 0.55)
    camera(ISO)
    Render(view)
'''


def foam_file(folder):
    foam = os.path.join(folder, "case.foam")
    if not os.path.exists(foam):
        open(foam, "a").close()
    return foam


def script_config(folder, mode="gui", prefer="vtk", info=None):
    """The data the script is generated from (all lengths in metres, the case frame)."""
    info = info or _json(folder)
    geo = info["geometry"]
    lo, hi = geo["bbox"]
    roi = region_of_interest(info)
    length = max(h - l for l, h in zip(lo, hi))
    vtk = latest_vtk(folder) if prefer == "vtk" else None
    patches = []
    if vtk:
        patches = sorted(p for p in glob.glob(os.path.join(vtk, "boundary", "*.vtp")) if not far_field_patch(p))
    span = max(b - a for a, b in zip(*roi))
    dom_lo, dom_hi = domain_of(info)
    return {
        "mode": mode, "foam": foam_file(folder), "vtu": os.path.join(vtk, "internal.vtu") if vtk else "",
        "patches": patches, "farfield": list(cfd.FARFIELD_PATCHES), "rho": float((info.get("air") or {}).get("rho", 1.2)),
        "cut_origin": [(l + h) / 2 for l, h in zip(*geo["bbox"])], "cut_opacity": 1.0,
        "speed_floor": 0.0, "seeds": seed_plan(info, 80), "roi": [list(roi[0]), list(roi[1])],
        "max_length": 1.5 * max(b - a for a, b in zip(dom_lo, dom_hi)), "tube_radius": 0.0012 * span,
        "iso_dir": [-0.75, -1.0, 0.7], "size": list(SIZE), "light": True,
        "pictures": [[k, image_path(folder, k)] for k, _c in picture_kinds(info)],
        "wash": wash_plan(info) or None,
    }, length


def write_script(folder, mode="gui", prefer="vtk", info=None):
    """Writes ``paraview/view.py`` (mode "gui") or ``paraview/render.py`` ("batch"); returns its path."""
    cfg, _length = script_config(folder, mode, prefer, info)
    os.makedirs(pv_dir(folder), exist_ok=True)
    path = os.path.join(pv_dir(folder), VIEW_SCRIPT if mode == "gui" else RENDER_SCRIPT)
    with open(path, "w") as f:
        f.write(SCRIPT.replace("__CFG__", pprint.pformat(cfg, width=110)))
    return path


def has_results(folder):
    """True when the case has a finished time to look at (a time directory, or foamToVTK output)."""
    if latest_vtk(folder):
        return True
    return any(d.isdigit() or d.replace(".", "", 1).isdigit()
               for d in os.listdir(folder) if d not in ("0", "constant", "system") and os.path.isdir(os.path.join(folder, d)))


def paraview_executables(app=None):
    """(gui, pvpython, pvbatch) inside the ParaView app bundle, each None when missing."""
    app = app or cfd.paraview_app()
    if not app:
        return None, None, None
    gui = os.path.join(app, "Contents", "MacOS", "paraview")
    pvp = os.path.join(app, "Contents", "bin", "pvpython")
    pvb = os.path.join(app, "Contents", "bin", "pvbatch")
    return tuple(p if os.path.isfile(p) else None for p in (gui, pvp, pvb))


def open_in_paraview(folder):
    """Opens ParaView with the case ready to view (see the module doc); returns the script path.  Without results
    to show it opens the bare case.foam."""
    gui, _pvp, _pvb = paraview_executables()
    app = cfd.paraview_app()
    if app is None or gui is None:
        raise cfd.CfdError(precheck_text())
    foam = foam_file(folder)
    try:
        script = write_script(folder, "gui")
    except Exception as exc:  # no results yet, or an old case without geometry: the plain open still works
        subprocess.Popen(["open", "-a", app, foam])
        raise cfd.CfdError(f"ParaView opened with the bare case (no view script: {exc})")
    subprocess.Popen([gui, f"--script={script}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return script


def precheck_text():
    if cfd.paraview_app() is None:
        return ("ParaView is not installed, so there are no flow pictures or streamline views: install it with "
                "'brew install --cask paraview' or from paraview.org (optional).")
    if paraview_executables()[2] is None:
        return "ParaView is installed but its pvbatch was not found, so no pictures are made."
    return ""


# ----- background pictures ---------------------------------------------------------------------------------------


class RenderJob:
    """pvbatch running the render script in a child process.  Poll ``poll()`` from a timer: it returns True when
    finished (done, failed, timed out, or never started); ``state`` is then "done", "failed", "timeout" or
    "unavailable" and ``message`` says why."""

    def __init__(self, folder, timeout=RENDER_TIMEOUT, prefer="vtk"):
        self.folder = folder
        self.timeout = timeout
        self.state = "running"
        self.message = ""
        self.proc = None
        self.started = time.time()
        self.images = {}
        _gui, _pvp, pvb = paraview_executables()
        if pvb is None:
            self._end("unavailable", precheck_text())
            return
        try:
            info = _json(folder)
            if not (info.get("geometry") and has_results(folder)):
                raise cfd.CfdError("the case has no results yet")
            script = write_script(folder, "batch", prefer, info)
            for p in glob.glob(os.path.join(pv_dir(folder), "pv_*.png")):  # an old picture must not stand in for this run
                os.remove(p)
            self.log = open(os.path.join(pv_dir(folder), RENDER_LOG), "w")
            self.proc = subprocess.Popen([pvb, "--force-offscreen-rendering", script], stdout=self.log,
                                         stderr=subprocess.STDOUT, cwd=pv_dir(folder), start_new_session=True)
        except Exception as exc:
            self._end("failed", f"could not start the pictures: {exc}")

    def _end(self, state, message=""):
        self.state, self.message = state, message
        self.images = images(self.folder) if state == "done" else {}
        log = getattr(self, "log", None)
        if log:
            log.close()

    def running(self):
        return self.state == "running"

    def poll(self):
        if self.state != "running":
            return True
        code = self.proc.poll()
        if code is None:
            if time.time() - self.started > self.timeout:
                self.proc.kill()
                self.proc.wait()
                self._end("timeout", f"ParaView took more than {self.timeout:.0f} s for the pictures and was stopped")
                return True
            return False
        if code == 0 and images(self.folder):
            self._end("done", f"{len(images(self.folder))} pictures in {pv_dir(self.folder)}")
        else:
            tail = ""
            try:
                tail = open(os.path.join(pv_dir(self.folder), RENDER_LOG)).read()[-300:]
            except OSError:
                pass
            self._end("failed", f"pvbatch ended with code {code} and made no picture. {tail}")
        return True

    def wait(self, step=0.2):
        while not self.poll():
            time.sleep(step)
        return self.state == "done"

    def stop(self):
        if self.state == "running" and self.proc:
            self.proc.kill()
            self.proc.wait()
            self._end("failed", "stopped")


def start_render(folder, timeout=RENDER_TIMEOUT, prefer="vtk"):
    return RenderJob(folder, timeout, prefer)


def report_figures(folder, dest_dir):
    """Copies the pictures beside report.tex as fig_pv_<kind>.png; returns [(file, caption)] for those that exist."""
    import shutil

    out = []
    os.makedirs(dest_dir, exist_ok=True)
    present = images(folder) if folder else {}
    kinds = picture_kinds(folder) if folder else list(IMAGES)
    for stale in glob.glob(os.path.join(dest_dir, "fig_pv_*.png")):  # a picture this case does not make any more
        if os.path.basename(stale)[len("fig_pv_"):-4] not in dict(kinds):
            os.remove(stale)
    for kind, caption in kinds:
        target = os.path.join(dest_dir, f"fig_pv_{kind}.png")
        if kind in present:
            shutil.copyfile(present[kind], target)
            out.append((os.path.basename(target), caption))
        elif os.path.isfile(target):
            os.remove(target)
    return out


def report_figures_tex(figures):
    """LaTeX for the pictures (empty when there are none)."""
    if not figures:
        return ""
    lines = [r"\subsection*{Flow pictures}"]
    for name, caption in figures:
        lines += [r"\begin{figure}[ht]\centering", rf"\includegraphics[width=0.95\linewidth]{{{name}}}",
                  rf"\caption{{{caption}}}", r"\end{figure}"]
    return "\n".join(lines) + "\n"


# ----- streamlines with the VTK of the app ----------------------------------------------------------------------


def seed_points(info, count=SEEDS, factor=1000.0):
    """Seed coordinates (n x 3 numpy, mm by default) filling the point clouds of seed_plan (fixed random seed)."""
    import numpy as np

    rng = np.random.default_rng(1)
    pts = []
    for cloud in seed_plan(info, count):
        n = cloud["n"]
        v = rng.normal(size=(n, 3))
        v /= np.linalg.norm(v, axis=1)[:, None]
        r = cloud["radius"] * rng.random(n) ** (1 / 3)
        pts.append(np.array(cloud["center"]) + v * r[:, None])
    return np.vstack(pts) * factor


def scaled_copy_path(path):
    return os.path.join(os.path.dirname(path), "mm", os.path.basename(path))


def streamline_path(vtu):
    return os.path.join(os.path.dirname(scaled_copy_path(vtu)), "streamlines.vtp")


def compute_streamlines(folder, count=SEEDS, vtu=None, info=None, timings=None, progress=None):
    """Streamlines of the latest foamToVTK volume with vtkStreamTracer, in mm (the model's units).

    Reads the mm copy of the volume (made when missing or older than the original), seeds as seed_plan says, integrates
    both ways, and writes ``streamlines.vtp`` beside the copy with the point arrays U and ``speed`` (m/s).  Returns the
    .vtp path; ``timings`` (a dict) gets the seconds of each step; ``progress(fraction)`` is called while tracing (the tracer
    holds the GIL, so a Python thread would freeze the window: ``StreamJob`` runs this in a separate process)."""
    import numpy as np
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
    from vtkmodules.vtkFiltersFlowPaths import vtkStreamTracer
    from vtkmodules.vtkIOXML import vtkXMLPolyDataWriter, vtkXMLUnstructuredGridReader

    t = {} if timings is None else timings
    t0 = time.time()
    info = info or _json(folder)
    if vtu is None:
        vdir = latest_vtk(folder)
        if vdir is None:
            vdir = os.path.dirname(cfd.convert_vtk(folder))
        vtu = os.path.join(vdir, "internal.vtu")
    mm = scaled_copy_path(vtu)
    if not os.path.isfile(mm) or os.path.getmtime(mm) < os.path.getmtime(vtu):
        mm = cfd.scaled_copy(vtu)
    t["copy"] = time.time() - t0
    t1 = time.time()
    reader = vtkXMLUnstructuredGridReader()
    reader.SetFileName(mm)
    reader.UpdateInformation()
    for sel in (reader.GetPointDataArraySelection(), reader.GetCellDataArraySelection()):
        sel.DisableAllArrays()
    reader.GetPointDataArraySelection().EnableArray("U")
    reader.Update()
    grid = reader.GetOutput()
    if grid.GetPointData().GetArray("U") is None:
        raise cfd.CfdError("the volume has no point velocity U to follow")
    t["read"] = time.time() - t1
    t1 = time.time()
    all_seeds = np.ascontiguousarray(seed_points(info, count))

    def seed_cloud(chunk):
        seeds = vtkPolyData()
        pts = vtkPoints()
        pts.SetData(numpy_to_vtk(np.ascontiguousarray(chunk), deep=True))
        seeds.SetPoints(pts)
        verts = vtkCellArray()
        for i in range(pts.GetNumberOfPoints()):
            verts.InsertNextCell(1)
            verts.InsertCellPoint(i)
        seeds.SetVerts(verts)
        return seeds

    dom_lo, dom_hi = info["geometry"]["domain"]
    tracer = vtkStreamTracer()
    tracer.SetInputData(grid)
    tracer.SetInputArrayToProcess(0, 0, 0, 0, "U")  # vtkDataObject.FIELD_ASSOCIATION_POINTS == 0
    tracer.SetIntegrationDirectionToBoth()
    tracer.SetIntegratorTypeToRungeKutta45()
    tracer.SetMaximumPropagation(1.5 * 1000.0 * max(b - a for a, b in zip(dom_lo, dom_hi)))
    tracer.SetInitialIntegrationStep(0.2)
    tracer.SetMinimumIntegrationStep(0.01)
    tracer.SetMaximumIntegrationStep(1.0)  # at most one cell per step: smooth lines
    tracer.SetIntegrationStepUnit(2)  # cell length
    tracer.SetMaximumNumberOfSteps(4000)
    tracer.SetTerminalSpeed(1e-9)
    # the lines do not depend on each other, so a progress report only needs the seeds in batches (the tracer's own
    # ProgressEvent does not fire)
    batches = 1 if progress is None else max(1, min(10, len(all_seeds) // 20))
    parts = []
    for i in range(batches):
        tracer.SetSourceData(seed_cloud(all_seeds[i * len(all_seeds) // batches:(i + 1) * len(all_seeds) // batches]))
        tracer.Update()
        piece = vtkPolyData()
        piece.DeepCopy(tracer.GetOutput())
        parts.append(piece)
        if progress is not None:
            progress((i + 1) / batches)
    if len(parts) == 1:
        out = parts[0]
    else:
        from vtkmodules.vtkFiltersCore import vtkAppendPolyData

        app = vtkAppendPolyData()
        for piece in parts:
            app.AddInputData(piece)
        app.Update()
        out = app.GetOutput()
    t["trace"] = time.time() - t1
    u = out.GetPointData().GetArray("U")
    if out.GetNumberOfPoints() == 0 or u is None:
        raise cfd.CfdError("no streamline could be traced from the seeds")
    speed = numpy_to_vtk(np.linalg.norm(vtk_to_numpy(u), axis=1).astype("float32"), deep=True)
    speed.SetName("speed")
    out.GetPointData().AddArray(speed)
    out.GetPointData().SetActiveScalars("speed")
    path = streamline_path(vtu)
    w = vtkXMLPolyDataWriter()
    w.SetFileName(path)
    w.SetInputData(out)
    w.SetDataModeToBinary()
    w.Write()
    t["total"] = time.time() - t0
    return path


def read_lines(path):
    """[(points n x 3, speed n)] of each polyline in a streamlines .vtp (for the fallback and the tests)."""
    import numpy as np
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkIOXML import vtkXMLPolyDataReader

    r = vtkXMLPolyDataReader()
    r.SetFileName(path)
    r.Update()
    poly = r.GetOutput()
    pts = vtk_to_numpy(poly.GetPoints().GetData())
    speed = vtk_to_numpy(poly.GetPointData().GetArray("speed"))
    cells = poly.GetLines()
    ids = vtk_to_numpy(cells.GetData() if not hasattr(cells, "GetConnectivityArray") else cells.GetConnectivityArray())
    offs = vtk_to_numpy(cells.GetOffsetsArray()) if hasattr(cells, "GetOffsetsArray") else None
    out = []
    if offs is not None:
        for a, b in zip(offs[:-1], offs[1:]):
            sel = ids[a:b]
            out.append((np.asarray(pts[sel]), np.asarray(speed[sel])))
    return out


def show_streamlines(analysis, path, label="Streamlines"):
    """Adds the streamlines .vtp to the study as a FEM post pipeline and colours it by speed in the 3D view.  Returns
    the object."""
    import FreeCAD

    doc = analysis.Document
    obj = doc.addObject("Fem::FemPostPipeline", "Pipeline_Streamlines")
    obj.read(path)
    obj.Label = label
    analysis.addObject(obj)
    doc.recompute()
    if FreeCAD.GuiUp:
        vo = obj.ViewObject
        vo.DisplayMode = "Surface"
        try:
            vo.Field = "speed"
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[cfd] streamlines not coloured by speed: {exc}\n")
        try:
            vo.LineWidth = 2
        except Exception:
            pass
    return obj


# ----- the same, off the GUI thread: a worker process ------------------------------------------------------------

STREAM_WORKER = ("import sys, json; sys.path.insert(0, sys.argv[1]); import bruceworx_cfd_paraview as p; "
                 "p.worker_main(sys.argv[2], int(sys.argv[3]))")


def worker_main(folder, count):
    """Entry of the worker process: traces, printing a ``@PROGRESS f`` line after each tenth of the seeds and ``@DONE path
    {timings}`` at the end."""
    def progress(f):
        print(f"@PROGRESS {f:.3f}", flush=True)

    t = {}
    path = compute_streamlines(folder, count, timings=t, progress=progress)
    print("@DONE " + json.dumps({"path": path, "timings": t}), flush=True)


def worker_python():
    """The Python of the app (it carries the VTK), or None."""
    import sys

    for p in (os.path.join(sys.prefix, "bin", "python3"), os.path.join(sys.exec_prefix, "bin", "python3")):
        if os.path.isfile(p):
            return p
    return None


class StreamJob:
    """vtkStreamTracer in a child process, so the window stays alive: ``poll()`` from a timer (True when finished),
    ``progress`` 0..1, ``cancel()``.  ``state`` is "running", "done", "failed", "cancelled" or "unavailable"; ``path`` and
    ``timings`` are set when done.  vtkStreamTracer does not let go of the GIL, so a Python thread would not help."""

    def __init__(self, folder, count=SEEDS):
        import queue
        import threading

        self.folder, self.count = folder, count
        self.state, self.message, self.progress = "running", "", 0.0
        self.path, self.timings, self.proc = None, {}, None
        self.started = time.time()
        self._lines = queue.Queue()
        py = worker_python()
        if py is None:
            self.state, self.message = "unavailable", "no Python with VTK was found next to the app"
            return
        try:
            self.proc = subprocess.Popen([py, "-I", "-c", STREAM_WORKER, os.path.dirname(os.path.abspath(__file__)), folder, str(count)],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        except OSError as exc:
            self.state, self.message = "unavailable", str(exc)
            return
        self._tail = []

        def pump():
            for line in self.proc.stdout:
                self._lines.put(line.rstrip("\n"))
            self._lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        self._eof = False

    def running(self):
        return self.state == "running"

    def poll(self):
        if self.state != "running":
            return True
        while True:
            try:
                line = self._lines.get_nowait()
            except Exception:
                break
            if line is None:
                self._eof = True
            elif line.startswith("@PROGRESS "):
                self.progress = float(line.split()[1])
            elif line.startswith("@DONE "):
                data = json.loads(line[6:])
                self.path, self.timings, self.progress = data["path"], data["timings"], 1.0
            else:
                self._tail = (self._tail + [line])[-6:]
        if self._eof:
            code = self.proc.wait()
            if code == 0 and self.path:
                self.state = "done"
            else:
                self.state = "failed"
                msg = [l for l in self._tail if l.strip()]
                self.message = msg[-1] if msg else f"the worker ended with code {code}"
            return True
        return False

    def cancel(self):
        if self.state == "running" and self.proc:
            self.proc.kill()
            self.proc.wait()
            self.state, self.message = "cancelled", "stopped"

    def wait(self, step=0.05, timeout=300.0):
        t0 = time.time()
        while not self.poll():
            if time.time() - t0 > timeout:
                self.cancel()
                break
            time.sleep(step)
        return self.state == "done"


def start_streamlines(folder, count=SEEDS):
    return StreamJob(folder, count)
