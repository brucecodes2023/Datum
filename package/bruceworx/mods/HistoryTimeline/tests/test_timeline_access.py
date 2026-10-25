# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless proof that a Python add-on can read PartDesign history.

Run with FreeCAD's console binary (name is ``FreeCADCmd`` in a source build
and ``freecadcmd`` in the conda package)::

    freecadcmd package/bruceworx/mods/HistoryTimeline/tests/test_timeline_access.py

The script exits 0 when every check passes and 1 otherwise. It does not load
the GUI.
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import FreeCAD as App

from history_timeline import model

FAILURES = []
PASSED = 0


def emit(message):
    # FreeCADCmd exits with os._exit, which skips the stdout flush. A pipe
    # would otherwise swallow every result line.
    print(message, flush=True)


def check(condition, message):
    global PASSED
    if condition:
        PASSED += 1
        emit(f"PASS  {message}")
    else:
        FAILURES.append(message)
        emit(f"FAIL  {message}")


def volume(obj):
    try:
        shape = obj.Shape
        if shape.isNull():
            return None
        return float(shape.Volume)
    except Exception:
        return None


def state_of(obj):
    try:
        return list(obj.State)
    except Exception:
        return []


def names(objects):
    return [obj.Name for obj in objects]


def base_map(objects):
    found = {}
    for obj in objects:
        link = getattr(obj, "BaseFeature", None)
        found[obj.Name] = link.Name if link else None
    return found


def find_origin_plane(doc):
    for obj in doc.Objects:
        if obj.Name.startswith("XY_Plane") or obj.Label == "XY_Plane":
            return obj
    return None


def build_body(doc):
    """Sketch, datum plane, three primitives, and a VarSet when the type exists."""
    body = doc.addObject("PartDesign::Body", "Body")
    doc.recompute()
    plane_xy = find_origin_plane(doc)

    sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
    if plane_xy is not None:
        sketch.AttachmentSupport = (plane_xy, [""])
        sketch.MapMode = "FlatFace"
    body.addObject(sketch)

    datum = doc.addObject("PartDesign::Plane", "Datum")
    if plane_xy is not None:
        datum.AttachmentSupport = (plane_xy, [""])
        datum.MapMode = "FlatFace"
    body.addObject(datum)

    box = doc.addObject("PartDesign::AdditiveBox", "Box")
    box.Length = 10
    box.Width = 10
    box.Height = 10
    body.addObject(box)

    cylinder = doc.addObject("PartDesign::AdditiveCylinder", "Cylinder")
    cylinder.Radius = 2
    cylinder.Height = 20
    body.addObject(cylinder)

    sphere = doc.addObject("PartDesign::AdditiveSphere", "Sphere")
    sphere.Radius = 5
    body.addObject(sphere)

    varset = None
    try:
        varset = doc.addObject("App::VarSet", "Vars")
        body.addObject(varset)
    except Exception as exc:
        emit(f"NOTE  VarSet not added: {exc}")
        varset = None

    doc.recompute()
    return {
        "body": body,
        "sketch": sketch,
        "datum": datum,
        "box": box,
        "cylinder": cylinder,
        "sphere": sphere,
        "varset": varset,
        "plane_xy": plane_xy,
    }


def test_read_order_tip_and_chain():
    doc = App.newDocument("TimelineRead")
    try:
        built = build_body(doc)
        body = built["body"]
        emit("--- read_timeline ---")
        timeline = model.read_timeline(body)
        emit(timeline.format_report())

        expected = [
            built["sketch"].Name,
            built["datum"].Name,
            built["box"].Name,
            built["cylinder"].Name,
            built["sphere"].Name,
        ]
        if built["varset"] is not None:
            expected.append(built["varset"].Name)
        check(names(body.Group) == expected, f"Group order is {expected}, got {names(body.Group)}")
        check(timeline.tip_name == built["sphere"].Name, "Tip is the last solid (Sphere)")
        check(timeline.tip_in_group, "Tip object is inside Group")

        check(not model.is_solid_feature(built["sketch"]), "Sketch is not a solid feature")
        check(not model.is_solid_feature(built["datum"]), "Datum plane is not a solid feature")
        check(model.is_solid_feature(built["box"]), "AdditiveBox is a solid feature")
        check(model.is_solid_feature(built["cylinder"]), "AdditiveCylinder is a solid feature")
        check(model.is_solid_feature(built["sphere"]), "AdditiveSphere is a solid feature")
        if built["varset"] is not None:
            check(not model.is_solid_feature(built["varset"]), "VarSet is not a solid feature")
            check(
                not hasattr(built["varset"], "Suppressed"),
                "VarSet has no Suppressed property",
            )

        check(hasattr(built["box"], "Suppressed"), "Solid features expose Suppressed")
        check(not model.is_suppressed(built["box"]), "New solid is not suppressed")

        check(built["box"].BaseFeature is None, "First solid BaseFeature is None")
        check(
            built["cylinder"].BaseFeature == built["box"],
            "Cylinder.BaseFeature is Box (addObject wired the chain)",
        )
        check(
            built["sphere"].BaseFeature == built["cylinder"],
            "Sphere.BaseFeature is Cylinder",
        )
        check(
            model.previous_solid_in_group(body, built["sphere"]) == built["cylinder"],
            "Group-order previous solid of Sphere matches BaseFeature",
        )

        check(built["plane_xy"] is not None, "Body created an XY_Plane on its origin")
        if built["plane_xy"] is not None:
            check(
                built["plane_xy"] not in list(body.Group),
                "Origin planes are not Body.Group entries",
            )

        # addObject hides earlier solids, so the first visible PartDesign
        # feature would be the tip. Force an earlier solid visible: headless
        # has no view-provider hider, so VisibleFeature and Tip can differ.
        built["box"].Visibility = True
        built["sphere"].Visibility = True
        visible = body.VisibleFeature
        visible_name = visible.Name if visible else None
        emit(f"NOTE  VisibleFeature={visible_name} Tip={body.Tip.Name}")
        check(visible_name == built["box"].Name, "VisibleFeature is the first visible solid, not the tip")
        check(visible != body.Tip, "VisibleFeature and Tip are different properties")

        full_volume = volume(body)
        emit(f"NOTE  body volume at tip Sphere = {full_volume}")
        check(full_volume is not None and full_volume > 0, "Body shape at the tip has a volume")
        check("Invalid" not in state_of(body) and body.isValid(), f"Body valid, state={state_of(body)}")
    finally:
        App.closeDocument(doc.Name)


def test_set_tip_and_suppress():
    doc = App.newDocument("TimelineTip")
    try:
        built = build_body(doc)
        body = built["body"]
        box = built["box"]
        cylinder = built["cylinder"]
        sphere = built["sphere"]
        full_volume = volume(body)

        model.set_tip(body, box)
        doc.recompute()
        rolled = model.read_timeline(body)
        emit("--- tip rolled to Box ---")
        emit(rolled.format_report())
        emit(f"NOTE  body volume at tip Box = {volume(body)} box volume = {volume(box)}")
        check(body.Tip == box, "set_tip assigns Body.Tip")
        check(
            volume(body) is not None and abs(volume(body) - volume(box)) < 1e-6,
            "Body shape equals the tip shape",
        )
        check(rolled.entry(cylinder.Name).after_tip, "Cylinder is after the rolled tip")
        check(rolled.entry(sphere.Name).after_tip, "Sphere is after the rolled tip")
        check(not rolled.entry(box.Name).after_tip, "The tip itself is not after the marker")
        check(not rolled.entry(built["sketch"].Name).after_tip, "Sketch before the tip stays before the marker")
        check(cylinder in list(body.Group) and sphere in list(body.Group), "Features past the tip stay in Group")
        check(not model.is_suppressed(cylinder), "Rolling the tip does not suppress later features")

        model.set_tip(body, None)
        doc.recompute()
        cleared = model.read_timeline(body)
        emit(f"NOTE  body volume with Tip=None = {volume(body)} null={body.Shape.isNull()} state={state_of(body)}")
        check(body.Tip is None, "Tip can be cleared")
        check(all(entry.after_tip for entry in cleared.entries), "Cleared tip puts the marker before every entry")
        check(volume(body) in (None, 0) or body.Shape.isNull(), "Cleared tip drops the body solid")

        model.set_tip(body, sphere)
        doc.recompute()
        check(abs(volume(body) - full_volume) < 1e-4, "Restoring the tip restores the body volume")

        ok, reason = model.can_be_tip(body, built["sketch"])
        check(not ok, f"Sketch cannot be the tip ({reason})")
        refused = False
        try:
            model.set_tip(body, built["sketch"])
        except ValueError:
            refused = True
        check(refused, "set_tip rejects a sketch")
        check(body.Tip == sphere, "Rejected set_tip leaves Tip unchanged")

        # The property itself will accept a sketch. Record what execute does.
        body.Tip = built["sketch"]
        doc.recompute()
        emit(
            f"NOTE  raw Tip=Sketch isValid={body.isValid()} state={state_of(body)} "
            f"volume={volume(body)}"
        )
        check(not body.isValid(), "Assigning Tip to a sketch marks the body invalid")
        check(
            "Invalid" in state_of(body),
            f"Body state contains Invalid after a sketch tip, got {state_of(body)}",
        )
        body.Tip = sphere
        doc.recompute()
        check(body.isValid() and volume(body) is not None, "Tip restored to Sphere after the non-solid assignment")

        before_suppress = volume(body)
        model.set_suppressed(cylinder, True)
        doc.recompute()
        emit(
            f"NOTE  suppressed Cylinder volume={volume(cylinder)} box={volume(box)} "
            f"body={volume(body)} before={before_suppress} state={state_of(body)}"
        )
        check(model.is_suppressed(cylinder), "Suppressed flag sticks")
        check(body.isValid(), f"Body still valid with a suppressed middle solid, state={state_of(body)}")
        cyl_vol = volume(cylinder)
        box_vol = volume(box)
        check(
            cyl_vol is not None and box_vol is not None and abs(cyl_vol - box_vol) / box_vol < 0.02,
            "Suppressed solid shape passes the base shape through",
        )
        after_suppress = volume(body)
        check(
            after_suppress is not None and abs(after_suppress - before_suppress) > 1e-3,
            "Suppressing a middle solid changes the tip result",
        )
        model.set_suppressed(cylinder, False)
        doc.recompute()
        check(not model.is_suppressed(cylinder), "Unsuppress clears the flag")
        check(
            abs(volume(body) - before_suppress) < 1e-3,
            "Unsuppress restores the tip volume",
        )
    finally:
        App.closeDocument(doc.Name)


def test_playback_targets():
    doc = App.newDocument("TimelinePlayback")
    try:
        built = build_body(doc)
        body = built["body"]
        box, cylinder, sphere = built["box"], built["cylinder"], built["sphere"]
        check(model.solids_in_group(body) == [box, cylinder, sphere], "solids_in_group lists the three solids")
        check(model.playback_target(body, "end") == (False, sphere), "end at the end: no change")
        check(model.playback_target(body, "forward") == (False, sphere), "forward at the end: no change")
        check(model.playback_target(body, "back") == (True, cylinder), "back steps to the previous solid")
        check(model.playback_target(body, "start") == (True, None), "start clears the tip")
        model.set_tip(body, box)
        check(model.playback_target(body, "back") == (True, None), "back before the first solid clears the tip")
        check(model.playback_target(body, "forward") == (True, cylinder), "forward steps to the next solid")
        model.set_tip(body, None)
        check(model.playback_target(body, "forward") == (True, box), "forward from a cleared tip picks the first solid")
        check(model.playback_target(body, "back") == (False, None), "back from a cleared tip: no change")
        check(model.playback_target(body, "end") == (True, sphere), "end from a cleared tip picks the last solid")
        refused = False
        try:
            model.playback_target(body, "sideways")
        except ValueError:
            refused = True
        check(refused, "unknown playback action is refused")
        model.set_tip(body, sphere)
        doc.recompute()
    finally:
        App.closeDocument(doc.Name)


def test_delete_feature():
    doc = App.newDocument("TimelineDelete")
    try:
        built = build_body(doc)
        body = built["body"]
        box, cylinder, sphere = built["box"], built["cylinder"], built["sphere"]
        model.delete_feature(body, cylinder)
        doc.recompute()
        check(doc.getObject("Cylinder") is None, "delete_feature removes the object from the document")
        check(sphere.BaseFeature == box, "delete_feature relinks the next solid onto the previous one")
        check(body.Tip == sphere, "deleting a middle solid leaves the tip")
        check(body.isValid() and abs(volume(body) - (1000 + volume(sphere) - 1000)) < 1e-3, "body recomputes after the delete")
        model.delete_feature(body, sphere)
        doc.recompute()
        check(body.Tip == box, "deleting the tip moves the tip to the previous solid")
        check(names(body.Group) == [built["sketch"].Name, built["datum"].Name, box.Name]
              + ([built["varset"].Name] if built["varset"] is not None else []), f"group after deletes {names(body.Group)}")
    finally:
        App.closeDocument(doc.Name)


def build_pad_hole_fillet(doc):
    """Pad (30x10x10 from a rectangle on XY), a through hole (circle sketch on XY,
    Pocket mid-plane through all), a 1 mm fillet on the pad's four vertical edges.
    Then a second pocket from a sketch attached to the pad's top face."""
    import Part

    V = App.Vector
    body = doc.addObject("PartDesign::Body", "Body")
    doc.recompute()
    xy = body.Origin.OriginFeatures[3]

    def sketch(name, support, geometry):
        sk = doc.addObject("Sketcher::SketchObject", name)
        sk.AttachmentSupport = support
        sk.MapMode = "FlatFace"
        for item in geometry:
            sk.addGeometry(item)
        body.addObject(sk)
        return sk

    pts = [V(0, 0, 0), V(30, 0, 0), V(30, 10, 0), V(0, 10, 0)]
    sk_pad = sketch("PadSketch", [(xy, "")], [Part.LineSegment(pts[i], pts[(i + 1) % 4]) for i in range(4)])
    pad = doc.addObject("PartDesign::Pad", "Pad")
    pad.Profile = sk_pad
    pad.Length = 10
    body.addObject(pad)
    doc.recompute()

    sk_hole = sketch("HoleSketch", [(xy, "")], [Part.Circle(V(22, 5, 0), V(0, 0, 1), 2)])
    hole = doc.addObject("PartDesign::Pocket", "Hole")
    hole.Profile = sk_hole
    hole.Type = 1  # through all
    hole.Midplane = True
    body.addObject(hole)
    doc.recompute()

    vertical = [
        f"Edge{i + 1}"
        for i, edge in enumerate(hole.Shape.Edges)
        if isinstance(edge.Curve, Part.Line) and abs(abs(edge.tangentAt(edge.FirstParameter).z) - 1) < 1e-6
    ]
    fillet = doc.addObject("PartDesign::Fillet", "Fillet")
    fillet.Base = (hole, vertical)
    fillet.Radius = 1
    body.addObject(fillet)
    doc.recompute()

    top = None
    for i, face in enumerate(fillet.Shape.Faces):
        if abs(face.CenterOfMass.z - 10) < 1e-6 and face.Surface.TypeId == "Part::GeomPlane":
            top = f"Face{i + 1}"
            break
    sk_top = sketch("TopSketch", [(fillet, top)], [Part.Circle(V(8, 5, 0), V(0, 0, 1), 1.5)])
    pocket2 = doc.addObject("PartDesign::Pocket", "Pocket2")
    pocket2.Profile = sk_top
    pocket2.Length = 5
    body.addObject(pocket2)
    doc.recompute()
    return body, {"pad": pad, "hole": hole, "fillet": fillet, "pocket2": pocket2,
                  "sk_pad": sk_pad, "sk_hole": sk_hole, "sk_top": sk_top}


def test_reorder_rules_and_move():
    doc = App.newDocument("TimelineMove")
    try:
        body, f = build_pad_hole_fillet(doc)
        pad, hole, fillet, pocket2 = f["pad"], f["hole"], f["fillet"], f["pocket2"]
        emit("--- pad / hole / fillet / pocket2 ---")
        emit(model.read_timeline(body).format_report())
        check(all(o.isValid() for o in body.Group), "test body is valid")
        check(names(model.feature_group(body, pad)) == ["PadSketch", "Pad"], "a pad folds its own sketch")
        check(names(model.feature_group(body, fillet)) == ["Fillet"], "a fillet has no sketch to fold")
        check(names(model.group_of(body, f["sk_hole"])) == ["HoleSketch", "Hole"], "group_of a sketch is its feature's group")
        check(model.hard_links(fillet, {o.Name for o in body.Group}) == set(), "fillet Base on its BaseFeature is the chain, not a dependency")
        check(model.hard_links(f["sk_top"], {o.Name for o in body.Group}) == {"Fillet"}, "a sketch on a face depends on that feature")
        check(model.is_subtractive(hole) and model.is_subtractive(fillet) and not model.is_subtractive(pad), "subtractive test")

        volume_before = volume(body)
        ok, reason = model.can_move(body, fillet, "earlier")
        check(ok, f"fillet before an independent hole is legal ({reason})")
        ok, reason = model.can_move(body, hole, "earlier")
        check(not ok and "material" in reason, f"a pocket cannot become the first feature ({reason})")
        ok, reason = model.can_move(body, pad, "later")
        check(not ok, f"the pad cannot move behind the pocket ({reason})")
        ok, reason = model.can_move(body, pocket2, "earlier")
        check(not ok and "TopSketch" in reason and "Fillet" in reason, f"a pocket whose sketch sits on the fillet's face cannot pass it ({reason})")
        ok, reason = model.can_move(body, pocket2, "later")
        check(not ok and "last" in reason, f"last feature cannot move later ({reason})")
        ok, reason = model.can_move(body, f["sk_hole"], "earlier")
        check(not ok and "sketch" in reason, f"a sketch is moved with its feature ({reason})")
        ok, reason = model.can_move(body, hole, "later")
        check(ok, f"hole later (= fillet earlier) is legal ({reason})")

        snap = model.snapshot_dressups(body)
        check("Fillet" in snap and snap["Fillet"][0] == "Hole" and len(snap["Fillet"][1]) == 4
              and all(sig is not None and sig[0] == "Edge" for _s, sig in snap["Fillet"][1]),
              f"snapshot notes the fillet's four edges on the hole: {snap.get('Fillet')}")
        moved = model.move_feature(body, fillet, "earlier")
        emit("--- after Fillet earlier ---")
        emit(model.read_timeline(body).format_report())
        check(moved == ["Fillet"], f"moved {moved}")
        check(names(model.solids_in_group(body)) == ["Pad", "Fillet", "Hole", "Pocket2"], f"solid order {names(model.solids_in_group(body))}")
        check(fillet.BaseFeature == pad and hole.BaseFeature == fillet and pocket2.BaseFeature == hole, "chain rewired by the body")
        check(fillet.Base[0] == pad, "fillet Base follows BaseFeature")
        check(len(fillet.Base[1]) == 4 and all(";" not in n for n in fillet.Base[1]), f"fillet rebound with plain edge names {fillet.Base[1]}")
        check(all(o.isValid() for o in body.Group), f"everything valid after the move: {[(o.Name, o.getStatusString()) for o in body.Group if not o.isValid()]}")
        check(body.Tip == pocket2, "tip stays at the end")
        check(abs(volume(body) - volume_before) < 1e-3, f"same volume after the move ({volume(body)} vs {volume_before})")
        model.move_feature(body, hole, "earlier")
        check(all(o.isValid() for o in body.Group), f"valid after hole earlier {[(o.Name, o.getStatusString()) for o in body.Group if not o.isValid()]}")
        check(names(model.solids_in_group(body)) == ["Pad", "Hole", "Fillet", "Pocket2"], "back to the original order via the hole")
        check(abs(volume(body) - volume_before) < 1e-3, "volume back")

        # Moving the sketch group: the hole and its sketch travel together.
        model.move_feature(body, hole, "later")
        order = names(body.Group)
        check(order.index("HoleSketch") == order.index("Hole") - 1 and order.index("Hole") > order.index("Fillet"), f"sketch travels with its pocket {order}")
        check(all(o.isValid() for o in body.Group), "valid after hole later")

        # Order is now Pad, Fillet, Hole, Pocket2: Pocket2 may pass the hole (independent)
        # but not the fillet whose face its sketch sits on.
        ok, _reason = model.can_move(body, pocket2, "earlier")
        check(ok, "pocket2 may pass the independent hole")
        model.move_feature(body, pocket2, "earlier")
        check(names(model.solids_in_group(body)) == ["Pad", "Fillet", "Pocket2", "Hole"], f"pocket2 before hole {names(model.solids_in_group(body))}")
        check(all(o.isValid() for o in body.Group), "valid after pocket2 earlier")
        ok, reason = model.can_move(body, pocket2, "earlier")
        check(not ok and "Fillet" in reason, f"pocket2 cannot pass the fillet ({reason})")
        before = names(body.Group)
        refused = False
        try:
            model.move_feature(body, pocket2, "earlier")
        except ValueError:
            refused = True
        check(refused and names(body.Group) == before, "illegal move refused, Group untouched")
        model.move_feature(body, pocket2, "later")
        check(names(model.solids_in_group(body)) == ["Pad", "Fillet", "Hole", "Pocket2"], "pocket2 back after the hole")

        # Tip on a middle feature stays on that object.
        model.set_tip(body, fillet)
        model.move_feature(body, hole, "earlier")
        check(body.Tip == fillet, "tip stays on its object when it is not at the end")
        check(all(o.isValid() for o in body.Group), "valid after the tipped move")
    finally:
        App.closeDocument(doc.Name)


def test_entries_groups_and_errors():
    doc = App.newDocument("TimelineEntries")
    try:
        body, f = build_pad_hole_fillet(doc)
        timeline = model.read_timeline(body)
        emit(timeline.format_report())
        check(timeline.has_groups, "the timeline has folded sketches")
        pad, sk_pad = timeline.entry("Pad"), timeline.entry("PadSketch")
        check(sk_pad.parent == "Pad" and pad.children == ["PadSketch"], "the pad's sketch folds under the pad")
        check(timeline.entry("HoleSketch").parent == "Hole", "the hole's sketch folds under the hole")
        check(timeline.entry("TopSketch").parent == "Pocket2", "the top sketch folds under its pocket")
        check(timeline.entry("Fillet").children == [], "a fillet folds nothing")
        check(pad.kind_name == "Pad" and sk_pad.kind_name == "Sketch" and timeline.entry("Hole").kind_name == "Pocket", "kind names")
        check(model.kind_name("PartDesign::AdditiveCylinder") == "Additive Cylinder", "kind_name splits words")
        check("Pad · Pad" in pad.tooltip and "sketch: PadSketch" in pad.tooltip, f"pad tooltip {pad.tooltip!r}")
        check("sketch of Pad" in sk_pad.tooltip, "sketch tooltip names its feature")
        check(not any(e.error for e in timeline.entries), "no errors in a valid body")

        fillet = f["fillet"]
        fillet.Radius = 100  # bigger than the part: the fillet fails
        doc.recompute()
        error, text = model.feature_error(fillet)
        check(error and text, f"feature_error reports the failed fillet: {text!r}")
        timeline = model.read_timeline(body)
        entry = timeline.entry("Fillet")
        check(entry.error and entry.error_text == text, "the entry carries the error")
        check("Error:" in entry.tooltip, "tooltip shows the error")
        check(not timeline.entry("Pad").error, "earlier features are not marked")
        fillet.Radius = 1
        doc.recompute()
        check(not model.read_timeline(body).entry("Fillet").error, "error clears with the fix")

        # A sketch used by two solids is not folded.
        doc2_body = body
        pad2 = doc.addObject("PartDesign::Pad", "Pad2")
        pad2.Profile = f["sk_hole"]
        pad2.Length = 2
        doc2_body.addObject(pad2)
        doc.recompute()
        timeline = model.read_timeline(body)
        check(timeline.entry("HoleSketch").parent is None, "a sketch shared by two features stays on its own")
        check(timeline.entry("Hole").children == [], "the hole no longer folds the shared sketch")
    finally:
        App.closeDocument(doc.Name)


def test_reorder_is_not_free():
    doc = App.newDocument("TimelineReorder")
    try:
        built = build_body(doc)
        body = built["body"]
        box = built["box"]
        cylinder = built["cylinder"]
        sphere = built["sphere"]
        original_group = names(body.Group)
        original_bases = base_map(body.Group)
        original_volume = volume(body)

        model.assign_group_order(body, list(reversed(body.Group)))
        emit("--- Group reversed, BaseFeature left alone ---")
        emit(model.read_timeline(body).format_report())
        check(names(body.Group) == list(reversed(original_group)), "assign_group_order reverses Group")
        check(base_map(body.Group) == original_bases, "Reversing Group does not rewrite BaseFeature")
        check(
            model.previous_solid_in_group(body, sphere) != cylinder,
            "After a Group-only reorder, Group-previous solid is no longer BaseFeature",
        )
        check(sphere.BaseFeature == cylinder, "Sphere.BaseFeature is still Cylinder")
        doc.recompute()
        emit(f"NOTE  volume after Group reverse = {volume(body)} state={state_of(body)}")
        check(body.isValid(), "Document still recomputes when only Group order changed")
        check(
            abs(volume(body) - original_volume) < 1e-3,
            "Group-only reorder does not change the tip solid (chain unchanged)",
        )

        # Setting BaseFeature does not pull Group into line. Feature::onChanged
        # looks up the base index into the wrong variable, so the branch is dead.
        group_before = names(body.Group)
        sphere.BaseFeature = box
        check(names(body.Group) == group_before, "Assigning BaseFeature does not reorder Group")
        check(sphere.BaseFeature == box, "BaseFeature assignment itself works")
        sphere.BaseFeature = cylinder
        model.assign_group_order(body, [doc.getObject(name) for name in original_group])
        doc.recompute()
        check(names(body.Group) == original_group, "Group restored")
        check(abs(volume(body) - original_volume) < 1e-3, "Volume restored after Group restore")

        # removeObject should be Body::removeObject: next solid's BaseFeature
        # skips the removed solid, Tip stays put if we did not remove the tip.
        model.remove_feature(body, cylinder)
        emit("--- after removeObject(Cylinder) ---")
        emit(f"NOTE  group={names(body.Group)} tip={body.Tip.Name if body.Tip else None}")
        emit(f"NOTE  sphere.BaseFeature={sphere.BaseFeature.Name if sphere.BaseFeature else None}")
        check(cylinder not in list(body.Group), "removeObject drops the feature from Group")
        check(sphere.BaseFeature == box, "removeObject relinks the next solid onto the previous solid")
        check(body.Tip == sphere, "Removing a non-tip solid leaves Tip alone")

        body.insertObject(cylinder, sphere, True)
        emit("--- after insertObject(Cylinder, after Sphere) ---")
        emit(model.read_timeline(body).format_report())
        solids = [obj.Name for obj in body.Group if model.is_solid_feature(obj)]
        check(solids == [box.Name, sphere.Name, cylinder.Name], f"Cylinder inserted after Sphere, solids={solids}")
        check(cylinder.BaseFeature == sphere, "insertObject sets the moved solid's BaseFeature")
        check(body.Tip == sphere, "insertObject does not move Tip")
        doc.recompute()
        emit(
            f"NOTE  tip still Sphere volume={volume(body)} sphere={volume(sphere)} "
            f"cylinder={volume(cylinder)} state={state_of(body)}"
        )
        check(body.isValid(), f"Recompute after remove+insert, state={state_of(body)}")
        check(
            volume(body) is not None and abs(volume(body) - volume(sphere)) < 1e-4,
            "Body shape still follows Tip, which is now before the moved solid",
        )

        # Second document-level check: explicit chain rewire, including sketches.
        # Done in this same body by putting solids back and calling the helper.
        ordered = [
            built["sketch"],
            built["datum"],
            box,
            cylinder,
            sphere,
        ]
        if built["varset"] is not None:
            ordered.append(built["varset"])
        model.rewire_solid_chain(body, ordered)
        doc.recompute()
        check(cylinder.BaseFeature == box and sphere.BaseFeature == cylinder, "rewire_solid_chain restores Box→Cylinder→Sphere")
        check(body.Tip == sphere, "rewire_solid_chain does not change Tip")
        check(abs(volume(body) - original_volume) < 1e-3, "Rewired chain matches the original tip volume")

        # insertObject does not remove an existing member first.
        before = len(body.Group)
        body.insertObject(box, sphere, True)
        after_names = names(body.Group)
        emit(f"NOTE  Group after insertObject of a member: {after_names}")
        check(len(body.Group) == before + 1, "insertObject of an existing member adds a second Group entry")
        check(after_names.count(box.Name) == 2, "The same object can appear twice in Group")
        # Do not recompute a duplicated, possibly cyclic chain. Put the model back.
        model.rewire_solid_chain(body, ordered)
        model.set_tip(body, sphere)
        doc.recompute()
        check(names(body.Group) == [obj.Name for obj in ordered], "Group restored after the duplicate insert")
        check(body.isValid(), "Body valid after restoring the duplicate insert")
    finally:
        App.closeDocument(doc.Name)


def main():
    version = ".".join(str(part) for part in App.Version()[:3])
    emit(f"=== History timeline spike on FreeCAD {version} ===")
    try:
        test_read_order_tip_and_chain()
        test_set_tip_and_suppress()
        test_playback_targets()
        test_delete_feature()
        test_reorder_rules_and_move()
        test_entries_groups_and_errors()
        test_reorder_is_not_free()
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        FAILURES.append("unhandled exception (see traceback)")
    emit(f"=== {PASSED} passed, {len(FAILURES)} failed ===")
    if FAILURES:
        for message in FAILURES:
            emit(f"  - {message}")
        os._exit(1)
    os._exit(0)


main()
