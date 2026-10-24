"""Point a user.cfg copy at the fixtures: prepcfg.py <cfg> <fixtures folder> [<vault> <actor>].

Recent files become Plate, Bracket and a file that no longer exists; no vault is open,
or <vault> is, with <actor> pinned as the user of it (BruceWorx/VaultActors/<vault id>).
"""

import glob
import json

import os
import sys
import xml.etree.ElementTree as ET


def group(root, path):
    node = root.find("FCParamGroup[@Name='Root']")
    for part in path.split("/"):
        nxt = node.find(f"FCParamGroup[@Name='{part}']")
        if nxt is None:
            nxt = ET.SubElement(node, "FCParamGroup", Name=part)
        node = nxt
    return node


def put(g, tag, name, value):
    el = g.find(f"{tag}[@Name='{name}']")
    if el is None:
        el = ET.SubElement(g, tag, Name=name)
    if tag == "FCText":
        el.text = value
    else:
        el.set("Value", value)


cfg, fix = sys.argv[1], sys.argv[2]
tree = ET.parse(cfg)
root = tree.getroot()
rec = group(root, "BaseApp/Preferences/RecentFiles")
for el in list(rec):
    if el.get("Name", "").startswith("MRU"):
        rec.remove(el)
files = [os.path.join(fix, "Plate.FCStd"), os.path.join(fix, "Bracket.FCStd"), os.path.join(fix, "Gone.FCStd")]
for i, f in enumerate(files):
    put(rec, "FCText", f"MRU{i}", f)
bw = group(root, "BaseApp/Preferences/BruceWorx")
vault = sys.argv[3] if len(sys.argv) > 4 else ""
put(bw, "FCText", "VaultPath", vault)
if vault:
    refs = glob.glob(os.path.join(vault, "*.bwvault"))
    if refs:
        vault_id = json.load(open(refs[0], encoding="utf-8")).get("vault_id", "")
        if vault_id:
            put(group(root, "BaseApp/Preferences/BruceWorx/VaultActors"), "FCText", vault_id, sys.argv[4])
tree.write(cfg, encoding="utf-8", xml_declaration=True)
