# SPDX-License-Identifier: LGPL-2.1-or-later
"""Semantic GD&T (DRW-03, ASME Y14.5): datums and feature control frames.

A datum is the letter in a datum feature symbol. A feature control frame
stores a geometric characteristic, a tolerance, and the datum letters it
references, in primary-to-tertiary order. ``GdtModel.validate`` flags every
reference whose letter is absent from the model's datums.

The model is plain Python. ``make_gdt`` writes it as JSON on a FeaturePython
property (``Gdt``), so a save and reopen keeps it. TechDraw's SVG library
still draws the symbols; this module stores what they mean.
"""

import json

# ASME Y14.5 geometric characteristics. Concentricity and symmetry were
# withdrawn in Y14.5-2018; they stay so a frame from an older drawing still loads.
CHARACTERISTICS = (
    "straightness",
    "flatness",
    "circularity",
    "cylindricity",
    "profile_of_a_line",
    "profile_of_a_surface",
    "angularity",
    "perpendicularity",
    "parallelism",
    "position",
    "concentricity",
    "symmetry",
    "circular_runout",
    "total_runout",
)
# Datum feature letters. I, O and Q are not used (they read as 1, 0 and O).
DATUM_LETTERS = frozenset("ABCDEFGHJKLMNPRSTUVWXYZ")
SCHEMA = 1
PROP = "Gdt"
TYPE = "BruceWorx::Gdt"


def datum_label(text):
    """One datum letter: a capital other than I, O or Q."""
    label = str(text).strip().upper()
    if label not in DATUM_LETTERS:
        raise ValueError(f"datum label must be a single letter other than I, O or Q, not {text!r}")
    return label


def positive_tolerance(value):
    """A tolerance value: a finite number greater than zero, in the document unit."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"tolerance must be a positive number, not {value!r}")
    number = float(value)
    if number <= 0.0 or number != number or number == float("inf"):
        raise ValueError(f"tolerance must be a positive number, not {value!r}")
    return number


class Datum:
    """A datum feature symbol. ``label`` is the letter in the box."""

    def __init__(self, label):
        self.label = datum_label(label)

    def to_dict(self):
        return {"label": self.label}

    def __eq__(self, other):
        return isinstance(other, Datum) and self.label == other.label

    def __repr__(self):
        return f"Datum({self.label!r})"


class FeatureControlFrame:
    """One feature control frame: characteristic, tolerance, datum references.

    A reference is kept even when that datum is not in the set. ``validate``
    is what flags it.
    """

    def __init__(self, characteristic, tolerance, datums=()):
        if characteristic not in CHARACTERISTICS:
            names = ", ".join(CHARACTERISTICS)
            raise ValueError(f"characteristic must be one of {names}, not {characteristic!r}")
        refs = tuple(datum_label(ref) for ref in datums)
        if len(refs) > 3:
            raise ValueError("a feature control frame references at most three datums")
        self.characteristic = characteristic
        self.tolerance = positive_tolerance(tolerance)
        self.datums = refs

    def to_dict(self):
        return {"characteristic": self.characteristic, "datums": list(self.datums), "tolerance": self.tolerance}

    def __eq__(self, other):
        return (isinstance(other, FeatureControlFrame) and self.characteristic == other.characteristic
                and self.tolerance == other.tolerance and self.datums == other.datums)

    def __repr__(self):
        return f"FeatureControlFrame({self.characteristic!r}, {self.tolerance!r}, {list(self.datums)!r})"


class Finding:
    """A feature control frame references a datum the set does not have."""

    def __init__(self, frame, datum, characteristic):
        self.frame = frame
        self.datum = datum
        self.characteristic = characteristic

    def __str__(self):
        return (f"frame {self.frame} ({self.characteristic}) references datum {self.datum}, "
                "which is not in the datum set")

    def __eq__(self, other):
        return (isinstance(other, Finding) and self.frame == other.frame and self.datum == other.datum
                and self.characteristic == other.characteristic)

    def __repr__(self):
        return f"Finding({self.frame}, {self.datum!r}, {self.characteristic!r})"


class GdtModel:
    """The datums and feature control frames of one part."""

    def __init__(self, datums=(), frames=()):
        datums = tuple(datums)
        frames = tuple(frames)
        for datum in datums:
            if not isinstance(datum, Datum):
                raise TypeError(f"datums must be Datum, not {datum!r}")
        for frame in frames:
            if not isinstance(frame, FeatureControlFrame):
                raise TypeError(f"frames must be FeatureControlFrame, not {frame!r}")
        labels = [datum.label for datum in datums]
        if len(labels) != len(set(labels)):
            raise ValueError("datum labels must be unique")
        self.datums = datums
        self.frames = frames

    def validate(self):
        """Flags for datum references this set does not contain. Empty means clean."""
        known = {datum.label for datum in self.datums}
        flagged = []
        for index, frame in enumerate(self.frames):
            for ref in frame.datums:
                if ref not in known:
                    flagged.append(Finding(index, ref, frame.characteristic))
        return flagged

    def to_dict(self):
        return {
            "datums": [datum.to_dict() for datum in self.datums],
            "frames": [frame.to_dict() for frame in self.frames],
            "version": SCHEMA,
        }

    def to_json(self):
        """One stable line, so a save and a reopen compare equal."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("GD&T JSON must be an object")
        version = data.get("version", SCHEMA)
        if version != SCHEMA:
            raise ValueError(f"GD&T JSON version {version} is not supported")
        datums = []
        for item in data.get("datums", ()):
            if not isinstance(item, dict) or "label" not in item:
                raise ValueError("each datum needs a label")
            datums.append(Datum(item["label"]))
        frames = []
        for item in data.get("frames", ()):
            if not isinstance(item, dict):
                raise ValueError("each feature control frame must be an object")
            for key in ("characteristic", "tolerance"):
                if key not in item:
                    raise ValueError(f"a feature control frame needs {key}")
            frames.append(FeatureControlFrame(
                item["characteristic"], item["tolerance"], item.get("datums", ())))
        return cls(datums, frames)

    @classmethod
    def from_json(cls, text):
        if text is None or str(text).strip() == "":
            return cls()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GD&T JSON could not be read: {exc}") from exc
        return cls.from_dict(data)

    def __eq__(self, other):
        return isinstance(other, GdtModel) and self.datums == other.datums and self.frames == other.frames

    def __repr__(self):
        return f"GdtModel({list(self.datums)!r}, {list(self.frames)!r})"


# --- the document object ----------------------------------------------------------------------


def ensure_property(obj):
    """The JSON property. Added again when an older file is opened without it."""
    if PROP not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", PROP, "GDT", "Datums and feature control frames, as JSON")


class GdtFeature:
    """Proxy of the FeaturePython that keeps a ``GdtModel`` in its Gdt property.

    The JSON is the document's copy. The proxy only brings the property back
    on restore; ``read`` rebuilds the model from that string.
    """

    def __init__(self, obj):
        self.Type = TYPE
        obj.Proxy = self
        ensure_property(obj)

    def onDocumentRestored(self, obj):
        ensure_property(obj)

    def execute(self, obj):
        return

    def dumps(self):
        return {"Type": getattr(self, "Type", TYPE)}

    def loads(self, state):
        self.Type = (state or {}).get("Type", TYPE)


def is_gdt(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", "") == TYPE


def read(obj):
    """The model stored on ``obj``."""
    return GdtModel.from_json(getattr(obj, PROP, "") or "")


def write(obj, model):
    """Replace ``obj``'s stored model."""
    if not isinstance(model, GdtModel):
        raise TypeError(f"model must be a GdtModel, not {model!r}")
    ensure_property(obj)
    obj.Gdt = model.to_json()


def make_gdt(doc, model=None, name="Gdt"):
    """A FeaturePython object holding ``model`` (an empty set when omitted)."""
    obj = doc.addObject("App::FeaturePython", name)
    GdtFeature(obj)
    write(obj, model if model is not None else GdtModel())
    return obj
