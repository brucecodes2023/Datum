# SPDX-License-Identifier: LGPL-2.1-or-later
"""Studio shading for the 3D view (UIX-08; 25 Oct UAT #6, "better rendering").

The Fusion-like studio look: soft key and fill lights, a studio environment
(bright horizon band, softboxes, darker floor) reflected by every face, and
each part coloured and finished from its material: aluminium is brushed
silver, steel darker and sharper, PETG glossy charcoal, ABS ivory...

How it works: a small GLSL program is put as the first child of the
``FlatRoot`` separator of every Part view provider (the faces only: edges,
points, sketches, text and the section caps keep FreeCAD's own drawing). One
program per *look* (colour, metal, roughness), shared by all the parts with
that look. Nothing is written to the document: the look is worked out from
the part's material and appearance each time either changes.

* The material decides the finish (``look_for``). A colour set by hand (the
  ShapeAppearance differs from the material's appearance and from FreeCAD's
  default) is kept, with the material's finish.
* Selection and preselection colours, per-face colours and link colour
  overrides come through unchanged: the material colour is replaced only where
  FreeCAD sends exactly that colour.
* "No shading" (base colour) and textures work as in stock FreeCAD.

Off switch (FND-07): Preferences > BruceWorx > Studio shading, the same as
BruceWorx > Studio Shading. Off removes every program node, so the scene is
exactly stock FreeCAD again. Like the ground grid, it is drawn only while the
BruceWorx preference pack is applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFS_PATH = "User parameter:BaseApp/Preferences/BruceWorx"
PREF = "StudioShading"
NODE_NAME = "BWStudio"
COMMAND = "BruceWorx_StudioShading"

# FreeCAD's appearance of the "Default" material and of a new part.
DEFAULT_DIFFUSE = (0.8, 0.8, 0.9019608)


@dataclass(frozen=True)
class Look:
    """A finish. ``tint`` None keeps the colour FreeCAD sends (the card's or the user's)."""

    name: str
    tint: tuple | None
    metal: float
    rough: float


# Material name patterns (lower case, matched against the card name and its "Father"), first match wins.
# Colours are what the finished part looks like in a studio, not FreeCAD's fixed-function values
# (FreeCAD's Steel appearance is a black diffuse with a white specular, aluminium a dark grey).
LOOKS = (
    (r"anodi[sz]ed|powder|painted", Look("coated", None, 0.35, 0.45)),
    (r"chrom", Look("chrome", (0.80, 0.81, 0.83), 1.0, 0.06)),
    (r"stainless|inox|x\d+cr|1\.4\d{3}|\b30[1-4]\b|\b316", Look("stainless steel", (0.76, 0.76, 0.75), 1.0, 0.26)),
    (r"alumin|\bal[- ]?(mg|si|cu|zn)|\b[1-7]\d{3}-t\d|\ben ?aw", Look("aluminium", (0.86, 0.87, 0.88), 1.0, 0.42)),
    (r"titan|ti-?6al", Look("titanium", (0.64, 0.61, 0.57), 1.0, 0.34)),
    (r"brass|cuzn", Look("brass", (0.90, 0.76, 0.43), 1.0, 0.24)),
    (r"bronze|cusn", Look("bronze", (0.80, 0.58, 0.38), 1.0, 0.30)),
    (r"copper|\bcu\b", Look("copper", (0.95, 0.64, 0.54), 1.0, 0.24)),
    (r"gold", Look("gold", (1.00, 0.78, 0.34), 1.0, 0.16)),
    (r"silver", Look("silver", (0.96, 0.95, 0.91), 1.0, 0.14)),
    (r"zinc|galvani", Look("zinc", (0.80, 0.82, 0.83), 1.0, 0.45)),
    (r"nickel|pewter", Look("nickel", (0.70, 0.68, 0.64), 1.0, 0.30)),
    (r"steel|iron|\bc\d{2}\b|1c\d{2}|\b10\d{2}\b|\bs\d{3}|gj[lsm]|metal", Look("steel", (0.60, 0.61, 0.62), 1.0, 0.30)),
    (r"petg\b|pet-g\b", Look("PETG", (0.20, 0.22, 0.25), 0.0, 0.22)),
    (r"\babs\b|abs-", Look("ABS", (0.91, 0.89, 0.84), 0.0, 0.42)),
    (r"\bpla\b|pla-", Look("PLA", (0.94, 0.94, 0.94), 0.0, 0.48)),
    (r"\bpa\d*\b|nylon|polyamid", Look("nylon", (0.91, 0.89, 0.81), 0.0, 0.58)),
    (r"\bpom\b|pom-|acetal|delrin", Look("POM", (0.95, 0.95, 0.93), 0.0, 0.34)),
    (r"peek", Look("PEEK", (0.78, 0.68, 0.52), 0.0, 0.40)),
    (r"ptfe|teflon", Look("PTFE", (0.97, 0.97, 0.96), 0.0, 0.70)),
    (r"\bpp\b|pp-|\bpe\b|pe-|hdpe|uhmw", Look("polyolefin", (0.93, 0.93, 0.90), 0.0, 0.55)),
    (r"\bpc\b|pc-|polycarb|acryl|pmma|glass", Look("clear", (0.80, 0.86, 0.90), 0.0, 0.08)),
    (r"\bpvc\b|pvc-", Look("PVC", (0.55, 0.57, 0.60), 0.0, 0.45)),
    (r"rubber|tpu|tpe|silicone|epdm|nbr", Look("rubber", (0.13, 0.13, 0.14), 0.0, 0.85)),
    (r"wood|mdf|balsa|particle", Look("wood", (0.72, 0.55, 0.36), 0.0, 0.80)),
    (r"thermoplast|plastic", Look("plastic", (0.86, 0.86, 0.84), 0.0, 0.42)),
)

# No material, or one the table does not know: FreeCAD's own colour, satin.
PLAIN = Look("plain", None, 0.0, 0.42)


def _norm(text) -> str:
    return str(text or "").strip().lower()


def _luminance(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def look_for(name, father="", card=None) -> Look:
    """The finish of a material from its card name and family ("Father"); ``card`` is the card's own
    appearance colour, if it has one.

    A card's own colour is kept (a shop card such as "PETG Orange" says what it looks like), except
    FreeCAD's dark metal appearances, which are tuned for fixed-function lighting (black steel with a
    white highlight, aluminium a dark grey): those metals take the table's colour."""
    text = " ".join(part for part in (_norm(name), _norm(father)) if part)
    if not text or text == "default":
        return PLAIN
    for pattern, look in LOOKS:
        if re.search(pattern, text):
            if card is not None and look.tint is not None and (look.metal < 0.5 or _luminance(card) >= 0.35):
                return Look(look.name, None, look.metal, look.rough)
            return look
    return PLAIN


def close(a, b, tol=0.012) -> bool:
    return a is not None and b is not None and all(abs(float(x) - float(y)) <= tol for x, y in zip(a[:3], b[:3]))


def resolve(look: Look, shown, expected) -> tuple:
    """(tint or None, metal, rough) for a part whose FreeCAD colour is ``shown``.

    ``expected`` are the colours FreeCAD gives that material (its card's appearance, or the default
    shape colours). A colour set by hand keeps that colour; on a metal it reads as a coating (half
    metal)."""
    if look.tint is None:
        return None, look.metal, look.rough
    if shown is not None and expected and not any(close(shown, colour) for colour in expected):
        return None, min(look.metal, 0.5), look.rough
    return look.tint, look.metal, look.rough


# ----- the shader -------------------------------------------------------------------------------

VERTEX_SHADER = """#version 120
varying vec3 bwNormal;
varying vec3 bwEye;
void main() {
    vec4 eye = gl_ModelViewMatrix * gl_Vertex;
    bwEye = eye.xyz;
    bwNormal = gl_NormalMatrix * gl_Normal;
    gl_FrontColor = gl_Color;
    gl_BackColor = gl_Color;
    gl_TexCoord[0] = gl_TextureMatrix[0] * gl_MultiTexCoord0;
    gl_ClipVertex = eye;  // section views and clip planes
    gl_Position = ftransform();  // same depth as FreeCAD's fixed-function edges
}
"""

# Eye space: +y up the screen, +z towards the viewer. The studio turns with the camera, so every view
# is lit the same way (the horizon stays level on screen), like a matcap but with real reflections.
FRAGMENT_SHADER = """#version 120
varying vec3 bwNormal;
varying vec3 bwEye;
uniform int coin_light_model;
uniform int coin_texunit0_model;
uniform sampler2D bwTexture;
uniform vec3 bwTint;
uniform vec3 bwBase;
uniform float bwUseTint;
uniform float bwMetal;
uniform float bwRough;

float softbox(vec3 r, vec3 d, float size, float blur) {
    return smoothstep(cos(size + blur), cos(max(size - blur, 0.0)), dot(r, d));
}

float strip(vec3 r, float azimuth, float width, float blur) {
    float across = 1.0 - smoothstep(max(width - blur, 0.0), width + blur, abs(atan(r.x, r.z) - azimuth));
    float along = smoothstep(-1.0, -0.85 + blur, r.y) * (1.0 - smoothstep(0.7, 0.95 + blur, r.y));
    return across * along;
}

vec3 studio(vec3 r, float blur) {
    // a white cyclorama: light floor, bright band below the horizon (seen in the walls of a part
    // viewed from above), a softer sky
    vec3 floorColor = vec3(0.50, 0.51, 0.53);
    vec3 band = vec3(0.97, 0.97, 0.98);
    vec3 sky = vec3(0.74, 0.76, 0.80);
    vec3 c = mix(floorColor, band, smoothstep(-0.85 - blur, -0.50 + blur, r.y));
    c = mix(c, sky, smoothstep(-0.35 - blur, 0.45 + blur, r.y));
    c += 0.75 * softbox(r, normalize(vec3(-0.45, 0.80, 0.40)), 0.32, blur);
    c += 0.50 * softbox(r, normalize(vec3(0.85, 0.30, 0.45)), 0.20, blur);
    c += 0.40 * softbox(r, normalize(vec3(0.15, 0.35, -0.92)), 0.40, blur);
    // tall strip lights left and right: the bright bands down a turned part
    c += 0.60 * strip(r, -1.05, 0.10, blur);
    c += 0.45 * strip(r, 1.35, 0.08, blur);
    return c;
}

void main() {
    vec4 base = gl_Color;
    if (coin_texunit0_model != 0) {
        base *= texture2D(bwTexture, gl_TexCoord[0].st);
    }
    if (coin_light_model == 0) {
        gl_FragColor = base;
        return;
    }
    vec3 albedo = base.rgb;
    if (bwUseTint > 0.5 && distance(albedo, bwBase) < 0.02) {
        albedo = bwTint;
    }
    vec3 n = normalize(bwNormal);
    if (!gl_FrontFacing) {
        n = -n;
    }
    vec3 v = gl_ProjectionMatrix[3][3] > 0.5 ? vec3(0.0, 0.0, 1.0) : normalize(-bwEye);
    float nv = clamp(dot(n, v), 0.0, 1.0);
    float rough = clamp(bwRough, 0.03, 1.0);
    vec3 f0 = mix(vec3(0.04), albedo, bwMetal);
    vec3 fresnel = f0 + (max(vec3(1.0 - rough), f0) - f0) * pow(1.0 - nv, 5.0);
    vec3 env = studio(reflect(-v, n), 0.9 * rough * rough + 0.02);

    // key light near the eye, a little up and left (walls seen from above stay bright), cool fill right
    vec3 key = normalize(vec3(-0.40, 0.45, 0.80));
    vec3 fill = normalize(vec3(0.75, 0.05, 0.66));
    vec3 hemi = mix(vec3(0.82, 0.83, 0.85), vec3(1.0), 0.5 + 0.5 * n.y);
    float lit = 0.40 + 0.55 * max(dot(n, key), 0.0) + 0.22 * max(dot(n, fill), 0.0);
    vec3 diffuse = albedo * (1.0 - 0.75 * bwMetal) * hemi * lit;  // brushed metal scatters a little

    float shininess = exp2(10.0 * (1.0 - rough) + 1.0);
    float highlight = pow(max(dot(n, normalize(key + v)), 0.0), shininess) * (1.0 - rough) * 0.8;
    vec3 color = diffuse + fresnel * (env + highlight);
    color = mix(color, 0.8 + 0.2 * (1.0 - exp(-(color - 0.8) / 0.2)), step(0.8, color));  // soft shoulder
    gl_FragColor = vec4(color + gl_FrontMaterial.emission.rgb, base.a);
}
"""


# ----- pure helpers for the view provider (tested headless) -------------------------------------


def material_of(obj):
    """(name, family, appearance diffuse or None) of an object's ShapeMaterial. The family is the card's
    "Father" and its library folder (Standard/Thermoplast: PET has no Father).

    Only attribute getters are used: a method call on the material a ShapeMaterial property returns
    (hasAppearanceModel, getAppearanceValue...) writes the material back to the object (FreeCAD 1.1),
    which would touch the document and set off the observers again."""
    material = getattr(obj, "ShapeMaterial", None)
    if material is None:
        return "", "", None
    name = getattr(material, "Name", "") or ""
    family = []
    try:
        family.append(material.PhysicalProperties.get("Father", "") or "")
    except Exception:
        pass
    family.append(getattr(material, "Directory", "") or "")
    father = " ".join(part for part in family if part)
    diffuse = None
    try:
        diffuse = _parse_color((material.AppearanceProperties or {}).get("DiffuseColor"))
    except Exception:
        diffuse = None
    return name, father, diffuse


def _parse_color(value):
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        return tuple(float(x) for x in value[:3])
    numbers = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
    if len(numbers) < 3:
        return None
    return tuple(float(x) for x in numbers[:3])


def shown_color(vobj):
    """The colour FreeCAD draws the faces in (the first ShapeAppearance), or None."""
    try:
        appearance = vobj.ShapeAppearance
        if appearance:
            return tuple(float(x) for x in appearance[0].DiffuseColor[:3])
    except Exception:
        pass
    return None


def finish_of(obj):
    """(look, tint, base, metal, rough) for a part, from its material and how it is shown."""
    name, father, card = material_of(obj)
    look = look_for(name, father, card)
    vobj = getattr(obj, "ViewObject", None)
    shown = shown_color(vobj) if vobj is not None else None
    # A new part keeps the default colour when a material is assigned if the default is not FreeCAD's
    # own (the BruceWorx pack sets one): that is not a colour set by hand either.
    expected = ([card] if card is not None else []) + default_colors()
    tint, metal, rough = resolve(look, shown, expected)
    return look, tint, shown, metal, rough


def default_colors() -> list:
    """FreeCAD's default shape colour and the one in the preferences (the BruceWorx pack sets its own)."""
    colors = [DEFAULT_DIFFUSE]
    try:
        import FreeCAD

        packed = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/View").GetUnsigned("DefaultShapeColor", 0)
        if packed:
            colors.append(tuple(((packed >> shift) & 0xFF) / 255.0 for shift in (24, 16, 8)))
    except Exception:
        pass
    return colors


def wanted(obj) -> bool:
    """Objects with faces drawn by ViewProviderPartExt: Part and PartDesign features, not sketches."""
    try:
        if not obj.isDerivedFrom("Part::Feature"):
            return False
        if obj.isDerivedFrom("Part::Part2DObject"):
            return False
    except Exception:
        return False
    return getattr(obj, "ViewObject", None) is not None


# ----- preferences --------------------------------------------------------------------------------


def _prefs():
    import FreeCAD

    return FreeCAD.ParamGet(PREFS_PATH)


def enabled() -> bool:
    group = _prefs()
    return group.GetBool("Applied", False) and group.GetBool(PREF, True)


# ----- Coin -------------------------------------------------------------------------------------

_state = {
    "programs": {},  # key -> SoShaderProgram
    "pending": set(),  # (document name, object name)
    "timer": None,
    "observers": None,
    "param": None,
    "installed": False,
    "on": False,
    "action": None,
}


def _program(tint, base, metal, rough):
    from pivy import coin

    key = (
        tuple(round(x, 3) for x in tint) if tint else None,
        tuple(round(x, 3) for x in base) if (tint and base) else None,
        round(metal, 3),
        round(rough, 3),
    )
    program = _state["programs"].get(key)
    if program is not None:
        return program
    program = coin.SoShaderProgram()
    program.setName(NODE_NAME)
    vertex = coin.SoVertexShader()
    vertex.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    vertex.sourceProgram = VERTEX_SHADER
    fragment = coin.SoFragmentShader()
    fragment.sourceType = coin.SoShaderObject.GLSL_PROGRAM
    fragment.sourceProgram = FRAGMENT_SHADER

    def vec3(name, value):
        parameter = coin.SoShaderParameter3f()
        parameter.name = name
        parameter.value.setValue(*value)
        return parameter

    def scalar(name, value):
        parameter = coin.SoShaderParameter1f()
        parameter.name = name
        parameter.value = value
        return parameter

    def integer(name, value):
        parameter = coin.SoShaderParameter1i()
        parameter.name = name
        parameter.value = value
        return parameter

    parameters = [
        vec3("bwTint", tint or (1.0, 1.0, 1.0)),
        vec3("bwBase", base or (-1.0, -1.0, -1.0)),
        scalar("bwUseTint", 1.0 if (tint and base) else 0.0),
        scalar("bwMetal", metal),
        scalar("bwRough", rough),
        integer("bwTexture", 0),
        # Coin keeps these two current while the program is on; these are the values until it does.
        integer("coin_light_model", 1),
        integer("coin_texunit0_model", 0),
    ]
    for index, parameter in enumerate(parameters):
        fragment.parameter.set1Value(index, parameter)
    program.shaderObject.set1Value(0, vertex)
    program.shaderObject.set1Value(1, fragment)
    program.ref()
    _state["programs"][key] = program
    return program


def flat_root(vobj):
    """The FlatRoot separator (the faces) of a Part view provider, or None."""
    from pivy import coin

    try:
        root = vobj.RootNode
    except Exception:
        return None
    if root is None:
        return None
    search = coin.SoSearchAction()
    search.setName("FlatRoot")
    search.setInterest(coin.SoSearchAction.FIRST)
    search.setSearchingAll(True)
    search.apply(root)
    path = search.getPath()
    if path is None:
        return None
    node = path.getTail()
    return node if node.isOfType(coin.SoSeparator.getClassTypeId()) else None


def _installed_node(sep):
    if sep is not None and sep.getNumChildren() and sep.getChild(0).getName().getString() == NODE_NAME:
        return sep.getChild(0)
    return None


def apply_to(obj) -> bool:
    """Put (or update) the studio program on one part. True when it is on."""
    if not wanted(obj):
        return False
    sep = flat_root(obj.ViewObject)
    if sep is None:
        return False
    _look, tint, shown, metal, rough = finish_of(obj)
    program = _program(tint, shown, metal, rough)
    current = _installed_node(sep)
    if current is program:
        return True
    if current is not None:
        sep.replaceChild(0, program)
    else:
        sep.insertChild(program, 0)
    return True


def remove_from(obj) -> None:
    vobj = getattr(obj, "ViewObject", None)
    if vobj is None:
        return
    sep = flat_root(vobj)
    if _installed_node(sep) is not None:
        sep.removeChild(0)


def _documents():
    import FreeCAD

    return list(FreeCAD.listDocuments().values())


def apply_document(doc) -> int:
    count = 0
    for obj in list(doc.Objects):
        try:
            count += bool(apply_to(obj))
        except Exception:
            pass
    return count


def apply_all() -> int:
    _state["on"] = True
    return sum(apply_document(doc) for doc in _documents())


def remove_all() -> None:
    """Back to stock FreeCAD: every program node out of every FlatRoot."""
    _state["on"] = False
    _state["pending"].clear()
    for doc in _documents():
        for obj in list(doc.Objects):
            try:
                remove_from(obj)
            except Exception:
                pass
    for program in _state["programs"].values():
        program.unref()
    _state["programs"].clear()


def installed_count() -> int:
    """Parts that carry the program now (tests)."""
    count = 0
    for doc in _documents():
        for obj in doc.Objects:
            vobj = getattr(obj, "ViewObject", None)
            if vobj is not None and wanted(obj) and _installed_node(flat_root(vobj)) is not None:
                count += 1
    return count


def sync(*_args) -> None:
    """Studio on or off as the preferences say."""
    if enabled():
        apply_all()
    elif _state["on"] or _state["programs"]:
        remove_all()
    _refresh_action()


# ----- keeping up with the documents ------------------------------------------------------------


def _schedule(doc_name, obj_name=None):
    if not _state["on"]:
        return
    _state["pending"].add((doc_name, obj_name))
    timer = _state["timer"]
    if timer is not None and not timer.isActive():
        timer.start()


def _flush():
    import FreeCAD

    pending, _state["pending"] = _state["pending"], set()
    if not _state["on"]:
        return
    if (None, None) in pending:
        apply_all()
        return
    for doc_name, obj_name in pending:
        doc = FreeCAD.listDocuments().get(doc_name)
        if doc is None:
            continue
        if obj_name is None:
            apply_document(doc)
            continue
        obj = doc.getObject(obj_name)
        if obj is not None:
            try:
                apply_to(obj)
            except Exception:
                pass


class _AppObserver:
    def slotFinishRestoreDocument(self, doc):
        _schedule(doc.Name)

    def slotCreatedObject(self, obj):
        _schedule(obj.Document.Name, obj.Name)

    def slotChangedObject(self, obj, prop):
        if prop == "ShapeMaterial":
            _schedule(obj.Document.Name, obj.Name)

    def slotUndoDocument(self, doc):
        _schedule(doc.Name)

    def slotRedoDocument(self, doc):
        _schedule(doc.Name)


class _GuiObserver:
    # The view provider is not touched here: it may still be under construction (no object yet), and
    # vobj.Object then crashes FreeCAD. A colour change refreshes every document once, debounced.
    def slotChangedObject(self, _vobj, prop):
        if prop in ("ShapeAppearance", "DiffuseColor", "ShapeColor"):
            _schedule(None)


class _ParamObserver:
    def onChange(self, _group, reason):
        if reason in (PREF, "Applied"):
            from PySide import QtCore

            QtCore.QTimer.singleShot(0, sync)  # not inside the parameter notification


# ----- the menu toggle ----------------------------------------------------------------------------


def toggle() -> bool:
    group = _prefs()
    value = not group.GetBool(PREF, True)
    group.SetBool(PREF, value)
    sync()
    return value


def _refresh_action():
    action = _state["action"]
    if action is not None:
        try:
            action.setChecked(enabled())
            action.setEnabled(_prefs().GetBool("Applied", False))
        except Exception:
            pass


def _install_menu():
    if _state["action"] is not None:
        return
    try:
        import bruceworx_menu
        from PySide import QtGui

        menu = bruceworx_menu.get_menu()
        if menu is None:
            return
        action = QtGui.QAction(menu)
        action.setText("Studio Shading")
        action.setObjectName("BruceWorxStudioShadingAction")
        action.setCheckable(True)
        action.setToolTip(
            "Soft studio lights and reflections, parts coloured from their material. "
            "Off: FreeCAD's own shading (Preferences > Vector Labs)."
        )
        action.triggered.connect(lambda _checked=False: toggle())
        bruceworx_menu.add_action(action)
        _state["action"] = action
        _refresh_action()
    except Exception:
        pass


def install() -> None:
    """Observers, the menu toggle, and the studio on every open document (InitGui)."""
    if _state["installed"]:
        return
    import FreeCAD
    import FreeCADGui
    from PySide import QtCore

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.setInterval(0)
    timer.timeout.connect(_flush)
    _state["timer"] = timer
    observers = (_AppObserver(), _GuiObserver())
    FreeCAD.addDocumentObserver(observers[0])
    FreeCADGui.addDocumentObserver(observers[1])
    _state["observers"] = observers
    param = _ParamObserver()
    group = _prefs()  # the handle is kept: the observer lives as long as it does
    group.Attach(param)
    _state["param"] = (group, param)
    _install_menu()
    _state["installed"] = True
    sync()
