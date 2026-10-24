# Drawings & Vault GUI checks

Self-quitting scripts that drive the real FreeCAD window the way a user does (workspace
menu, toolbar commands, task panels and their OK buttons, message boxes, the Start page,
the Vault card) and log `CHECK PASS|FAIL <name> | <detail>` per check.

| Script | Covers |
|---|---|
| `test_drawing.py` | Drawing workspace and its TechDraw toolbars (pack-hidden ones reachable from the menu), New Drawing (no selection, ANSI B from an ASME vault, ISO A3 first angle from an ISO vault), title block filled from the part and the vault, hand-typed field kept, Insert View, Projection Group, Section View, a dimension, undo/redo, all sheets load, the page readable light/dark, the right panel's Properties tab (light check), save |
| `test_vault_setup.py` | Start page vault switcher (no vault, with vault, Close, Open, Open on a plain folder), Create Vault (folder follows name, Choose…, errors, part-number builder segments, programs/types, first parent, properties, Download CSV template / Load CSV / a bad CSV, Create), Import Files (offered after Create, Find Files, numbers, Import, Close), My work table, Vault Settings (delete refused, rename, retire, add, Save), Break Lock and Move Vault from Vault Settings |
| `test_vault_work.py` | Open from Vault (read-only, tab lock), first-edit offer, the save prompt (Don't Save / Check Out), Browser badges and lock icons, status-bar readout, the Vault card (command, filters, search, status lines, right-click menu), Check In (comment required), Undo Check-Out, newer version and Get Latest, Get Revision, a lock held by someone else, an assembly check-out |
| `test_changes.py` | Submit for Review, check-out refused in review, Reject (reason), Approve with checklist and note, drawing title block, released check-out offering Raise ECR, Raise ECR with an attachment, Changes chip, Decide ECR, Open ECO (offered), Release ECO (blocked, then Submit in the panel), Retire |

Every script also checks text contrast (`readability`) and clipped labels/buttons (`clipped`)
of the panels it opens, and saves screenshots.

## Run

```sh
package/bruceworx/tests/gui/docs/run.sh                 # all four, light and dark
package/bruceworx/tests/gui/docs/run.sh drawing changes # some
BW_THEMES=dark BW_OUT=/tmp/x package/bruceworx/tests/gui/docs/run.sh vault_work
```

`run.sh` copies `~/Library/Preferences/FreeCAD/v1-1/user.cfg` per run, applies the theme with
`cfgset.py` (FollowSystemAppearance off, VaultPath cleared), starts FreeCAD through the agents'
`fcslot.sh` when it exists (at most 3 FreeCADs at once; `BW_FCSLOT` overrides) under
`timeout BW_TIMEOUT` (300 s), and prints PASS/FAIL lines and a total. Exit status 0 = all passed.

Output (`BW_OUT`, default `$TMPDIR/bruceworx-gui-docs`): `logs/<script>-<theme>.log` (checks,
modal texts, notes, Report-view warnings), `.out` (FreeCAD's stdout), `.fault` (Python stacks
if FreeCAD crashes), `shots/<theme>-<script>-*.png`, `run/` (temporary vaults and sample files).

## Safety

Vaults are made only under `BW_OUT/run/`. `bwdocs.py` refuses a `BW_OUT` in iCloud Drive,
~/Documents or ~/Desktop, clears a VaultPath that points elsewhere, and wraps
`vault_setup.create_vault`, `FolderVault.init` and `vault_move.plan_move` so nothing is created
or moved outside `BW_OUT` (a refusal is a FAIL named `SAFETY`). File dialogs are stood in for
(`fake_file_dialog`); message boxes are answered by plan (`answer`) or cancelled.
