# BW-WIR-01: adopt the Cables workbench, or build?

**Decision: build.** 10 November 2026.

## What was looked at

The community Cables workbench, `sargo-devel/Cables`, version 0.4.1
(commit `9d68276`, 27 October 2026), LGPL-3.0-or-later.

This is a reading of its source, not a trial on screen. The epic asked for a
trial on an assembly with two moving links. The workbench needs the BIM
module and the GUI, and the machine this was done on has a GUI-less build
without BIM. The points below are the ones that can be settled from the
code. A 20-minute trial on a Mac would confirm the last row of the table;
it would not change the first four, and it would settle the open point in the first.

## Findings

| Need (roadmap §WIR) | Cables 0.4.1 | Source |
|---|---|---|
| Ends on datum ports | No. Ends and mid points attach to model vertices (`App::PropertyLinkSub`, element type `Vertex` only). Vertex names change when a part is edited, which is why the roadmap asks for datums. Whether a vertex reached through a link to another file works was not settled by reading. | `wireFlex.py` (`Vrtx_start`, `Vrtx_end`, `Vrtxs_mid`), `wireutils.getVector` |
| Hold the minimum bend radius | No. `MinimumRadius` is read-only: it reports the tightest curvature of the path it drew. Nothing stops a tighter bend. Paths are polylines with a fillet radius, or B-splines. | `wireFlex.py`, `archCable.py` |
| Python only, small footprint | The cable solid subclasses `ArchPipe`, so it needs BIM/Arch and Draft. BruceWorx hides BIM from the workbench list. | `archCableMainShape.py` |
| Cut length across a joint's range | Not there. One `Length`, for the current shape. | `archCable.py` |
| Follow an Assembly joint | No Assembly code anywhere in the workbench. A point attached to a vertex of a link in the same document would move when that link is recomputed, but the solver runs after the cable in a recompute and nothing listens during a drag. Not run. | `grep Assembly` finds nothing |

It is a good tool for what it is for: building wiring, with cable boxes,
conduits, connectors, light points and terminals, and multi-core cables
with profiles. That is a different job from a servo lead across a hinge.

## Licence

LGPL-3.0-or-later can ship beside FreeCAD's LGPL-2.1-or-later code as a
separate add-on, with its notice (BW-FND-04). It was not a reason either
way.

## What was built instead

`package/bruceworx/mods/WireRouting`, about 900 lines of Python: see the
README. Nothing from Cables was copied.
