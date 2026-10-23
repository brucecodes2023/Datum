# SPDX-License-Identifier: LGPL-2.1-or-later
"""Write settings_index.json: the labels of FreeCAD's preference pages, for Cmd-K search.

FreeCAD compiles most preference pages from .ui files, so the installed app has
no text to search. This script reads the .ui files in the source tree once and
keeps, for every page, the texts and tooltips of its labels, check boxes, radio
buttons and group boxes. Run it from the repository root after an upstream merge:

    python3 src/Mod/BruceWorx/make_settings_index.py
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(HERE, "settings_index.json")

# Option pages whose file names do not say so.
EXTRA = ("AddonManagerOptions.ui", "DlgImportStep.ui", "DlgExportStep.ui")
TEXT_CLASSES = ("QLabel", "QCheckBox", "QRadioButton", "Gui::PrefCheckBox", "Gui::PrefRadioButton")
TITLE_CLASSES = ("QGroupBox", "Gui::PrefGroupBox")


def is_page(path, text):
    """A preference page: it stores preferences and its file, folder or top widget says so."""
    name = os.path.basename(path)
    if "prefEntry" not in text:
        return False
    top = re.search(r'<widget class="[^"]*" name="([^"]*)"', text)
    return (
        bool(re.search(r"(?i)settings|pref", name))
        or name in EXTRA
        or os.path.basename(os.path.dirname(path)) == "preferences"
        or bool(top and re.search(r"Settings|Pref", top.group(1)))
    )


def area(path):
    """The module a page belongs to, for display ("Sketcher", "Part", ...)."""
    rel = os.path.relpath(path, os.path.join(ROOT, "src"))
    parts = rel.split(os.sep)
    return "General" if parts[0] == "Gui" else parts[1] if len(parts) > 1 else parts[0]


def _string(widget, prop):
    node = widget.find(f"property[@name='{prop}']/string")
    return (node.text or "").strip() if node is not None else ""


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text)  # rich-text labels
    text = re.sub(r"\s+", " ", text).replace("&", "").strip()
    return text.rstrip(":").strip()


def page_entries(path):
    root = ET.parse(path).getroot()
    top = root.find("widget")
    if top is None:
        return []
    title = clean(_string(top, "windowTitle")) or os.path.splitext(os.path.basename(path))[0]
    where = area(path)
    seen = set()
    out = []
    for widget in top.iter("widget"):
        cls = widget.get("class", "")
        if cls in TEXT_CLASSES:
            text = clean(_string(widget, "text"))
        elif cls in TITLE_CLASSES:
            text = clean(_string(widget, "title"))
        else:
            continue
        if len(text) < 3 or not re.search(r"[A-Za-z]", text) or text in seen:
            continue
        seen.add(text)
        tip = clean(_string(widget, "toolTip"))
        out.append({"text": text[:120], "tip": tip[:200], "page": title, "area": where})
    return out


def build():
    entries = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        if "3rdParty" in folder or os.sep + "Ext" + os.sep in folder + os.sep:
            continue
        for name in sorted(files):
            if not name.endswith(".ui"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8", errors="replace") as handle:
                if not is_page(path, handle.read()):
                    continue
            entries += page_entries(path)
    entries.sort(key=lambda e: (e["area"], e["page"], e["text"]))
    return entries


if __name__ == "__main__":
    data = build()
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=0, ensure_ascii=False)
        handle.write("\n")
    print(f"wrote {OUT}: {len(data)} entries")
