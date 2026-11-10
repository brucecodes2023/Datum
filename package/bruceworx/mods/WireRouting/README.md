# Wire routing

Wires you can see, measure and move with a joint: servo leads, quad power
leads, board-to-ESC runs. Python only, no C++. Roadmap track WIR
(BW-WIR-01 to 06). On by default; the off switch is **Wire routing** on
Preferences → BruceWorx.

## The model

| Thing | What it is |
|---|---|
| **Port** | A coordinate-system datum on a part. The wire leaves along its +Z. Put ports in the part file, so every assembly that uses the part gets them. |
| **Clip** | The same kind of datum, used as a point the wire passes through. |
| **Wire** | A feature in the assembly. `Start` and `End` point at ports, `Via` at clips. |
| **Bundle** | A sleeve over the run of clips that two or more wires share. |

The centerline is a polyline (port, straight lead-out, clips, lead-out,
port) with every corner rounded to the wire's **minimum bend radius**. At a
corner that turns by θ the arc takes `r·tan(θ/2)` of each neighbouring
segment and is `r·θ` long, so the length is exact:

    length = Σ segments − Σ 2·r·tan(θ/2) + Σ r·θ

A segment shorter than the two tangent lengths at its ends cannot make the
bend. The wire then goes red, has no shape, and `Status` says which segment
and by how much, for example *segment 2 (point 2 to 3) is 10.000 mm long but
the bends at its ends need 12.281 mm at radius 8 mm (2.281 mm short)*. That
is the bend-radius check. The fix is to move a clip, lengthen a lead-out or
use a wire with a smaller bend radius.

Length always comes from the centerline. The tube is display only;
`ShowTube = False` shows the bare centerline and changes no number.

## Wires that cross a joint

A wire is rebuilt whenever a part it hangs on moves, both in a recompute
and while a part is dragged. (The Assembly solver runs after the assembly's
children in a recompute, so without this a wire would be one solve behind.)

A routed path across a joint changes length as the joint moves; a real wire
does not. So the length in the saved pose is not the length to cut.
**Wire Cut List…** (or `objects.sweep(assembly)`) moves every joint that
has both limits set through its range, one joint at a time, and records for
each wire:

* `LengthMin` and `LengthMax`, the shortest and longest path;
* `CutLength` = `LengthMax` + `Allowance`, rounded up to 1 mm;
* `SweepStatus`, for example `Elbow: 56.4 to 100.3 mm`, or
  `kinks at Elbow 70° to 90°` when the wire cannot make its bends there.

The assembly is put back as it was. Limits of a joint are set in the joint
dialog (BW-ASM-03). Not covered: joints without both limits (their range is
not known), and combinations of joints (each is swept with the others where
they are). The difference `LengthMax − LengthMin` is slack the wire has to
take up somewhere when the joint is at its short end.

## Menu (BruceWorx)

* **Add Wire Port**: select a part, or pick a point on a face first to put
  the port there facing out of the face.
* **Add Wire**: select the start port, any clips in order, then the end
  port (Ctrl+click).
* **Wire Cut List…**: sweeps the joints, saves the CSV, shows the cut
  lengths.

## Python

```python
from wire_routing import objects

# In the part files: a port on each part.
objects.make_port(part, "M1", App.Placement(App.Vector(10, 20, 10), App.Rotation()))

# In the assembly: base and arm are links to those part files.
wire = objects.make_wire(
    assembly,
    objects.port_ref(base, base_port),
    objects.port_ref(arm, arm_port),
    Diameter=1.6, MinBendRadius=4, LeadOut=6, Gauge="22 AWG", WireColor="red",
)
doc.recompute()
wire.Length, wire.Status

objects.sweep(assembly)              # LengthMin / LengthMax / CutLength / SweepStatus
objects.cut_list(doc)                # {"wires": [...], "totals": [...]}

objects.make_bundle(assembly, [w1, w2])                       # sleeve on the shared clips
objects.route_through_clips(wire, clips, edges)               # shortest path that can be bent
```

A reference is `(component, "Path.To.Port.")`, the form assembly joints use,
so a port inside a linked part file works. `port_ref` builds it.

**Cut list** rows: wire, gauge, color, from, to, length, min, max,
allowance, cut, status; plus totals per gauge and colour. The assembly BOM
lists wires too when *only parts* is off: add the columns `.Gauge`,
`.WireColor` and `.CutLength`.

**Bundle**: the sleeve covers the longest run of clips all its wires share,
in order. Its length is that run, clip to clip. The bore is estimated from
the wire diameters at a packing density (`Packing`, 0.75 loose).

**Clip graph**: `route_through_clips` tries every path through the clips
you allow (`edges`, or every pair closer than `max_span`), keeps the ones
the wire can bend through, lead-outs included, and sets `Via` to the
shortest. It reports the paths it rejected and why.

## Limits

* A clip is a point. The wire's direction through it is not constrained.
* Wires in a bundle are drawn on one centerline, not side by side.
* No free-space auto-routing around obstacles and no flattened nailboard
  drawing (out of scope for this track). Clearance against parts can use
  BW-ASM-06 once it exists.
* Board pins as ports wait for the ECAD bridge (BW-ECD-04).

## Tests

```sh
# Maths, clip graph, bundles, cut list: plain Python.
python3 -m unittest discover -s package/bruceworx/mods/WireRouting/tests

# Real documents: length, tube, bend check, joint follow, sweep, cut list, BOM, reload.
FreeCADCmd package/bruceworx/mods/WireRouting/tests/freecad_wire.py   # prints BW_WIRE PASS
```

Why this was built and not adopted: [docs/DECISION.md](docs/DECISION.md).
