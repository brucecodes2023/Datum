"""Roadmap epics: the single source for the CSV and the LaTeX summary table.

    python3 package/bruceworx/docs/fusion-ui-plan/scripts/epics.py

Writes ../roadmap-epics.csv (for import into BruceWorx Ops) and
../chapters/gen-epics.tex. Epics only; tasks are broken down when an epic
starts. "origin" lists the Ops idea IDs (sample data) each epic absorbs.
"""

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TRACKS = {
    "FND": "Foundation & upstream",
    "MOD": "Part modeling",
    "ASM": "Assembly",
    "DRW": "Drawings (ASME)",
    "PDM": "Part & drawing management",
    "SIM": "Simulation",
    "MFG": "Manufacturing outputs",
    "ROB": "Robotics & drone kits",
    "ECD": "ECAD bridge (KiCad)",
    "AUT": "AI & automation",
    "UIX": "UI & styling",
    "KRN": "Kernel study",
}

MILESTONES = {
    "M0": "Foundation green",
    "M1": "Model it: LE-02001 modelled in FreeCAD",
    "M2": "Document & release it: LE-02001 rev A from FreeCAD",
    "M3": "Make it: print, CNC and sheet-metal outputs",
    "M4": "Validate it: simulation",
    "M5": "Advanced modeling & assembly",
    "M6": "Switch system of record (FC-T01)",
    "UI": "UI & styling pass (after M2)",
    "BG": "Background study",
}


def E(id, title, milestone, status, depends, origin, summary, accept):
    return dict(id=f"BW-{id}", track=TRACKS[id[:3]], title=title, milestone=milestone,
                status=status, depends=" ".join(f"BW-{d}" for d in depends), origin=origin,
                summary=summary, acceptance=accept)


EPICS = [
    # Foundation ----------------------------------------------------------
    E("FND-01", "Build & CI on Linux and macOS arm64", "M0", "In progress", [],
      "FCB-P0-CI-01..06, P0-01, P0-06..08, P0-10",
      "Pixi envs and lockfiles, ccache, self-hosted Mac runner, docs. Linux STEP check and hosted macOS lock are merged (#4).",
      "Fresh clone builds on both platforms using documented commands only; CI green on both."),
    E("FND-02", "Packaging, branding & releases", "M0", "In progress", ["FND-01"],
      "FCB-P0-02, P0-03, P0-09, P0-11, P0-17, P0-18",
      "Branding 1.1.4-bw.1 and the unsigned .dmg (#3) exist. Keystone.app (25 Oct, your choice: fully renamed, own settings folders, built from the working tree): package/bruceworx/make-keystone.sh builds a self-contained bundle into /Applications; rebuilt through the 25 Oct UAT (Coin 4.0.10 bundled; the check run uses a throwaway profile). Still to do: release tagging with dmg + source, signing and notarising.",
      "Tagged release installs on a clean Mac and opens a Fusion STEP file within envelope."),
    E("FND-03", "Track upstream FreeCAD main", "M0", "To do", ["FND-01"],
      "FCB-P1-16..21, P0-12 (replaced: rebase-at-26.3 policy), P2-01/02 (add-on vs fork)",
      "Continuously rebase [BW] commits onto upstream main; nightly CI merge-test; conflict budget; rewrite UPSTREAM-SYNC.md.",
      "Nightly job rebases [BW] onto main and runs tests; a conflict opens an issue within 24 h."),
    E("FND-04", "Licences & notices", "M0", "To do", [],
      "FCB-P0-13",
      "NOTICE of changes; third-party notices for bundled SheetMetal, OCP, CadQuery, build123d and any OpenSCAD binary.",
      "Every bundled component listed with its licence; source tarball attached to each release."),
    E("FND-05", "Acceptance corpus & test harness", "M0", "To do", [],
      "FCB-P1-09, P3-08",
      "LE part corpus (STEP + native), headless fixtures, screenshot script; used by every epic's acceptance.",
      "Corpus in repo or LFS; one command runs all headless acceptance checks."),
    E("FND-06", "Fixes found in review", "M0", "Done", [],
      "(new)",
      "Spacings2 bug fixed + 3 tests; pack trim fixed (int(None) fallback, 9991ddf); tree auto-hide off by default. Done 25 Oct, checked on a fresh profile in the GUI: a 4x3 pattern gives 12 solids; 7 workbenches enabled, the More menu lists only Assembly, Draft, Part, Sketcher and Spreadsheet, no stock selector; the Browser shows as soon as a document is open (it stays out of the way of the empty Start page); Report view clean.",
      "4x3 pattern gives 12 instances; only intended workbenches shown; tree visible on first run."),
    E("FND-07", "On by default, each with an off switch", "M0", "Done", [],
      "FCB-P1-01, P1-10",
      "Feature flags and preference page (#9). Rule applies to every later epic.",
      "Merged (#9)."),
    E("FND-08", "Gaps found in real use", "M1", "In progress", [],
      "(new, found driving the Leonardo actuator in the Vector vault through the bridge, 24 Oct)",
      "Fixed 25 Oct (commits fba9166, 1f46252, d42d312): New Part in Vault (part or assembly, numbered, WIP 01, checked out and open; no number burnt on failure); new vaults number without REV; Create Assembly with a spreadsheet tab in front (the assembly was set to edit in the sheet's view, so assembly commands stayed greyed). A counterbored Hole from a top or bottom face sketch cuts into the plate (GUI test); the reported wrong preview did not reproduce. Open: a face sketch's centre (FreeCAD puts a face sketch's origin at the projected body origin, as Fusion does; your call whether it should be the face centre). Found in the 25 Oct UAT and fixed: Fit All fitted the 2 m ground grid, FreeCAD 1.1's first-start page overwrote the theme.",
      "New part in vault makes a numbered WIP 01 part without an import; a new vault's format has no REV; Create Assembly works (or says why) with a spreadsheet in front; a face sketch's centre and a counterbored hole land where they should; each covered by a GUI test."),
    # Modeling ------------------------------------------------------------
    E("MOD-01", "Rectangular & circular patterns", "M1", "Done", ["FND-06"],
      "(new, UI plan)",
      "Bodies/components built (link arrays, symmetric, suppress, linked/independent, dialog); features use PartDesign until the upstream rebase brings suppression. Done 25 Oct: the acceptance is a GUI test (TestBruceWorxPattern.GuiAcceptanceTests, through the commands and their dialog): 4x3 feature holes, 3x2 bodies with a suppressed instance, symmetric n=5, 8 bolts the Assembly counts; undo/redo; save/reload. It found and fixed a bug: a redo brought suppressed instances back (link visibility lost), now re-hidden after undo/redo. Compute Adjust stays deferred (question Q6).",
      "Fig. examples reproduced in GUI; undo/redo; save/reload; Assembly sees component instances."),
    E("MOD-02", "Parameters & top-down design", "M1", "To do", [],
      "(new)",
      "Change Parameters dialog over VarSet/Spreadsheet; skeleton sketches; derive geometry across files. Decided 24 Oct: one spreadsheet Parameters at the root of the top-assembly file holds every dimension and spec, with a Used in column naming the part or drawing each value drives; every part's dimensions are expressions on it, so changing the sheet updates the model.",
      "A skeleton change propagates to three linked part files in one recompute."),
    E("MOD-03", "Configurations", "M1", "To do", ["MOD-02"],
      "(new)",
      "Table-driven variants of one part or assembly; suppression per configuration; P/N per configuration.",
      "Short/Standard/Long bracket from one file; BOM lists the right configuration P/Ns."),
    E("MOD-04", "Direct edit on imported geometry", "M1", "To do", [],
      "FCB-P2-15 (STEP import checks)",
      "Press-pull, move, delete and replace face on dumb solids; hole/fillet recognition. Builds on Part_Defeaturing.",
      "Fusion STEP of LE-01001 edited (hole moved, face offset) and still valid."),
    E("MOD-05", "Threads, holes & hardware library", "M1", "To do", [],
      "FCB-P2-09/10 (COTS), (new)",
      "Cosmetic thread object; Hole presets incl. heat-set inserts; fastener library (evaluate Fasteners add-on).",
      "M3 tapped hole and M3 heat-set hole callouts appear on the drawing from model data."),
    E("MOD-06", "Sheet metal", "M3", "In progress", [],
      "(new, UI plan)",
      "Bundle the SheetMetal add-on; per-material/thickness rules and K-factor table. Plan approved 24 Oct; paused 24 Oct, half-built (vendored add-on, BUILD_BW_SHEETMETAL=OFF).",
      "Bracket flat length matches the bend-allowance formula to 0.01 mm."),
    E("MOD-07", "Frames & weldments", "M3", "To do", [],
      "(new)",
      "Structural members along sketch edges, profile library (2020 extrusion, tube, dimensional lumber), end treatments, cut list. Reference: LumberCAD (beta) component catalogue. Plan (FrameForge plus a BruceWorx layer) approved 24 Oct; not started.",
      "Frame example produces a cut list with lengths and mitre angles."),
    E("MOD-08", "Surfacing parity", "M5", "To do", [],
      "(new)",
      "Trim/untrim by curve, patch, extend, stitch, replace face, thicken; continuity (zebra, curvature) analysis.",
      "Surface benchmark set rebuilt to G1/G2 and thickened into a valid solid."),
    E("MOD-09", "Freeform (subdivision / T-spline-like)", "M5", "To do", ["MOD-08"],
      "(new)",
      "Form body: subdivision cage editing, conversion to B-spline B-rep.",
      "Drone canopy modelled as a cage and exported as a valid STEP solid."),
    E("MOD-10", "Mesh to solid", "M5", "To do", [],
      "(new)",
      "Mesh repair, segmentation, primitive fitting, rebuild as features; faceted fallback.",
      "Scanned bracket STL becomes a solid with planar/cylindrical faces within 0.1 mm."),
    E("MOD-11", "Inspect tools", "M1", "To do", [],
      "FCB-P2R-07 (mass), (new)",
      "Measure, mass/CG/inertia, section analysis, curvature/zebra, draft and overhang, wall thickness.",
      "Mass/CG matches a hand calculation fixture; overhang map on a printed part."),
    E("MOD-12", "Sketcher parity audit", "M1", "To do", [],
      "(new)",
      "Audit Sketcher against Fusion/SolidWorks sketch tools; fix the top gaps.",
      "Gap list approved; top five gaps closed."),
    # Assembly ------------------------------------------------------------
    E("MOD-13", "Sketch profile repair", "M1", "To do", ["MOD-12"],
      "(new, from the Nxt add-on review, 23 Oct)",
      "Before Pad, Pocket or Revolve fails on an open wire: find the gaps and missing coincidences and offer to close them.",
      "A sketch with a 0.05 mm gap pads after one click; nothing is changed without asking."),
    E("MOD-14", "On-canvas editing", "M5", "In progress", ["UIX-12"],
      "(new, from the Nxt add-on review, 23 Oct)",
      "Double-click a face or feature for value fields and drag arrows in the 3D view (Fusion press-pull style), on FreeCAD 1.1's dragger work. Plan approved 24 Oct; paused 24 Oct, half-built.",
      "Pad length, fillet radius and hole depth edited in the view without the task panel."),
    E("MOD-15", "Design worksheet", "M2", "To do", ["UIX-12", "PDM-04", "PDM-09"],
      "(new, the user's request of 24 Oct)",
      "Live worksheet tab: Markdown text, LaTeX math and unit-aware calc lines that read the model's Parameters "
      "and write chosen results back; its own vault item (04 Design spec) linked to the assembly; out of date "
      "when a value it read changes; PDF without TeX. Plan written 24 Oct, 14 questions waiting.",
      "Leonardo gearbox worksheet: reads Zs/Zp/Zr/m_n, writes a, follows a Zr change live, checked in as "
      "LE-04-01001-000 and shown out of date after the gearbox changes; PDF export without TeX."),
    E("MOD-16", "Persistent section views", "M1", "Done", ["UIX-12"],
      "(new, the user's request of 25 Oct; split from MOD-11)",
      "Sections saved in the file like views (eye on/off, double-click to edit), up to 3 planes, caps in each "
      "part's colour with a hatch per part and clashes in red, drawn by the GPU (SoFCSectionCap from upstream PR "
      "#33186, extended); handles and a right-panel tab with expressions. Planned, approved and built 25 Oct. UAT 25 Oct: the crash on dragging fixed (Coin 4.0.10, callbacks through the view); the Section button is a toggle (on, off, the last one back) and cuts on a face picked inside a body or an assembly.",
      "Gearbox section caps all 24 components, survives save/reopen, opens in stock FreeCAD; 3 planes; the old sun "
      "hub shows red against the carrier; grid never cut; 30 fps while dragging."),
    E("ASM-01", "File model: one part per file", "M1", "To do", [],
      "(new)",
      "Assemblies link external part files; relative paths; 'make external' from a multi-body file.",
      "LE-02001 opens from a moved folder with no broken links."),
    E("ASM-02", "Assembly gap audit", "M1", "Done", [],
      "FCB-P2A-01/02",
      "Approved 2026-10-19 (1.1.4 + 97 upstream commits): 6 gaps close on rebase; BOM needs part properties only. GUI checks (limits while dragging, joints on pattern instances, fixed-joint placement) are in the test checklist.",
      "Gap list approved by mechanical reviewer."),
    E("ASM-03", "Joint UX & limits", "M1", "In progress", ["ASM-02"],
      "FCB-P2A-03/04",
      "Pick-two-features quick joint with type inference; limit sliders honoured while dragging. Built 2026-10-19: limits measured from datums (LimitAngleZero/LimitLengthZero), drag backstop at limits, Keep parts where they are (as-built), Python drag API, 7 tests. 25 Oct: joint markers half the size, in outline (your UAT request).",
      "2-DOF arm assembled with quick joints; limits stop the drag."),
    E("ASM-04", "Exploded views & BOM", "M1", "In progress", ["ASM-01"],
      "FCB-P2A-05/06",
      "Part properties now come from a layered schema (config/part_properties.json, bruceworx/partprops.py): new properties need no code; Mass and Material are computed; the BOM keeps text such as rev 01 as typed. Still to do: exploded presets, indented and flattened BOM, CSV.",
      "LE-02001 BOM matches hand count; flattened BOM sums quantities."),
    E("ASM-05", "Sub-assemblies", "M1", "To do", ["ASM-01"],
      "FCB-P2A-07",
      "Rigid vs flexible sub-assemblies; fix-up macro.",
      "Flexible sub-assembly moves in the parent; rigid one does not."),
    E("ASM-06", "Interference & clearance", "M1", "To do", [],
      "FCB-P2A-08",
      "Pairwise clash/contact list with bbox prefilter; clearance check; highlight.",
      "Too-long bolt example reports 4.0 mm / 78.5 mm^3 clash."),
    E("ASM-07", "In-context editing", "M5", "To do", ["ASM-01", "MOD-02"],
      "(new)",
      "Edit a part inside the assembly referencing other parts; update and break/lock references.",
      "Bracket holes follow a moved motor mount across files."),
    E("ASM-08", "Large-assembly mode", "M5", "To do", ["ASM-01"],
      "(new)",
      "Lightweight/partial loading, selective open, display caches.",
      "Full robot (>500 parts) opens in under N s (target set in spike)."),
    E("ASM-09", "Smart fastener mating", "M5", "To do", ["MOD-05", "ASM-03"],
      "FCB-P2R-04 (mounting LCS)",
      "Recognise holes, insert matching fastener of correct length, auto-joint.",
      "Bolting a motor mount places and joints all screws in one command."),
    E("ASM-10", "Dynamic motion study", "M5", "To do", ["ASM-03"],
      "FCB-P2A-10",
      "Gravity, forces, motor torque and contact over time; plots and video.",
      "Arm under gravity matches analytic pendulum period within 2 %."),
    # Drawings ------------------------------------------------------------
    E("DRW-01", "ASME templates & title block", "M2", "Done", [],
      "FCB-P2-06/07/08",
      "ANSI A-D sheets, third-angle symbol, tolerance block; fields filled from PDM metadata. Built 24 Oct (ten templates, the fill observer, New Drawing). Done 25 Oct: on a copy of the Vector vault a new part with PLA and a finish, released as A, gets a drawing whose 20 title-block fields all fill by themselves (company, units, projection, standard, tolerances, title, P/N, revision A, material, finish, mass 15 g, drawn/checked/approved with dates, scale, sheet); a GUI test repeats it on a fresh vault. LE-01001 itself is drawn in the PDM-07 pilot.",
      "LE-01001 sample drawing fills every field automatically."),
    E("DRW-02", "Associative BOM table & balloons", "M2", "To do", ["ASM-04"],
      "FCB-P2A-06",
      "Native parts list (ASME Y14.34) driven by the assembly BOM; balloons linked to item numbers.",
      "Adding a part updates the table and keeps balloon numbers."),
    E("DRW-03", "GD&T & datums (ASME Y14.5)", "M2", "To do", [],
      "(new)",
      "Semantic feature control frames, datum symbols, basic dimensions; validation.",
      "FCF referencing a missing datum is flagged; frames survive model edits."),
    E("DRW-04", "Hole & thread callouts from the model", "M2", "To do", ["MOD-05"],
      "(new)",
      "Callout text generated from Hole/thread data (counterbore, depth, thread class).",
      "Callouts update when the hole changes."),
    E("DRW-05", "Revision table & release stamp", "M2", "To do", ["PDM-02"],
      "(new)",
      "Revision block from change history; PRELIMINARY stamp for numeric revs; P/N-REV printed.",
      "Releasing rev A clears the stamp and adds the revision row."),
    E("DRW-06", "Auto-generate drawing", "M2", "To do", ["DRW-01", "DRW-04"],
      "(new)",
      "One click: view selection, hole callouts and key dimensions, then manual edit.",
      "Corpus parts get a usable first-pass drawing; reviewer rates >= 70 % of dims kept."),
    E("DRW-07", "Sheet-metal flat & cut-list drawings", "M3", "To do", ["MOD-06", "MOD-07"],
      "(new)",
      "Flat-pattern view with bend table; weldment cut-list table.",
      "Bracket drawing shows flat with bend lines and table."),
    E("DRW-08", "Publish PDF/DXF", "M2", "To do", ["DRW-01"],
      "(new)",
      "Vector PDF and DXF export, batch publish for a BOM.",
      "One command publishes every drawing of LE-02001."),
    # PDM -----------------------------------------------------------------
    E("PDM-01", "PDM evaluation & decision", "M2", "Done", [],
      "(new)",
      "Decided 2026-10-19: folder-backed vault in any folder (iCloud Drive first), opened through a .bwvault ref file; one writer at a time. Prototype bruceworx/pdm_folder.py passes the PDM-02 contract plus folder and sync-duplicate tests.",
      "Decision memo approved at a gate."),
    E("PDM-02", "Data model & lifecycle spec", "M2", "Done", [],
      "(new)",
      "Spec approved 24 Oct (A1-A11) and built: pdm-openapi.yaml 0.3.0-draft, transition table T1-T12/F1-F4/B1; the approved changes are in pdm.py (A3 required texts, A9 refusals, A10 file-link where-used; A5 ECO Cancelled dropped). Done 25 Oct: every row of the table has its endpoint and a test (test_pdm_contract.py, 62 tests, on the model and the folder vault).",
      "Spec approved; state machine covers every transition in the doc."),
    E("PDM-03", "Numbering service", "M2", "In progress", ["PDM-02"],
      "FCB-P2-03/04/05",
      "P/N without revision; numbering-file and label migration (counters kept, REV dropped, one-time backup); Ops reader POST /part-numbers tested against a local stub only; source chosen by the numbering file (default local). New vaults write the P/N form since 25 Oct (FND-08). Waiting on: Ops serving the endpoint.",
      "P/N allocated from Ops; existing LE numbers migrate unchanged except REV."),
    E("PDM-04", "FreeCAD vault client", "M2", "In progress", ["PDM-01", "PDM-02"],
      "(new)",
      "Vault client over the folder vault: Vault panel, Browser badges, status bar; check out/in (comment required, linked parts fetched, links rewritten to <P/N>.FCStd, outside files imported), undo, get latest/revision, save prompt, Move vault; iCloud files downloaded before use with progress. Built 24 Oct; New Part in Vault added 25 Oct (FND-08). The LE-02001 iCloud round trip is yours to run.",
      "Check-out/check-in round trip of LE-02001 from an iCloud Drive vault inside FreeCAD; status icons correct."),
    E("PDM-05", "Lifecycle & change control", "M2", "In progress", ["PDM-04"],
      "(new)",
      "Submit, reject, approve with the recorded release checklist, ECR -> ECO -> release, Retire with reason and where-used; released copies read-only; CLI approve/reject/retire --note. Round trip rev A -> ECR -> ECO -> rev B passed on a sample part and drawing 24 Oct; LE-01001 run is yours.",
      "Rev A -> ECR -> ECO -> rev B round trip on LE-01001."),
    E("PDM-06", "BOM sync & where-used", "M2", "In progress", ["ASM-04", "PDM-04"],
      "(new)",
      "Push indented BOM per revision to Ops; where-used queries (25 Oct: where used shown in the Files tree, from recorded BOMs or the assembly files' links). Plan (BOM per assembly revision, pinned at release; released BOMs to an outbox for Ops) approved 24 Oct; paused 24 Oct, half-built.",
      "Where-used of a bolt lists every assembly revision that uses it."),
    E("PDM-07", "Pilot: LE-02001 & CAD register", "M2", "To do", ["PDM-05", "DRW-05"],
      "FCB-P2-15/16/17/18, P2-21",
      "Pedestal assembly tree, CAD register links, design-freeze checklist; real release. Plan approved 24 Oct: starts fresh, modelled in FreeCAD; waits on design data and DRW-05/08, MFG-04. 24 Oct: the Leonardo actuator (NEMA 17 planetary gearbox, LE-02-01001-001 and six parts) is being modelled in the real vault Vector through the bridge, the vault's first real use.",
      "LE-02001 rev A released from FreeCAD with drawings and package."),
    E("PDM-09", "Units, product tree & New Project", "M2", "In progress", ["PDM-04", "PDM-06", "DRW-01", "UIX-12"],
      "(new, 24 Oct)",
      "Vault default mm/inch with per-document override (decimals per unit, inch drawing profile); Structure view in the Vault tab with P/N-REV, state and drawing (current / out of date / missing); New Project wizard: typed structure -> numbered parts, assemblies, drawings checked in as WIP 01. Approved 24 Oct (warn on out-of-date drawings; blank drawings with Insert views; duplicate bubble); building paused 24 Oct, no code yet.",
      "A six-line typed project becomes numbered inch files checked in as WIP 01; changing one part marks exactly its drawing (and those above it) out of date."),
    E("PDM-10", "Vault dashboard", "M2", "Done", ["PDM-04", "PDM-05", "PDM-09"],
      "(new, 24 Oct)",
      "Start page with a vault open becomes the vault's dashboard: header with sync state, tiles by type x lifecycle state, projects with release progress, capped My work queues (incl. drawings out of date), open ECR/ECOs, folded activity feed (14 d), vault health (iCloud, locks, journal); vault items only, never local files; every tile opens the filtered Vault panel. Engine: linear replay (one cycle check), incremental tail refresh; a Recent files tab beside it for local files. Built 24 Oct (Start page Dashboard + Recent files tabs, 5,000-item vault opens in 59 ms). UAT 25 Oct (your choices): the vault is browsed on the Start page only, the floating card retired; a Files tab lists the vault as one tree (top assemblies, parts, each item's drawings) with where used on selection and iso pictures in the tooltip (made at check-in; Refresh Pictures caches older files on this Mac). Done 25 Oct, acceptance checked in the GUI on a 60-item fixture vault: each of the 4 tiles and 8 state entries opens the Files tab with exactly its count; a check-in written by another Mac's journal shows in the tiles and the feed after 0.8 s; the 500-item read stays under 0.1 s (test_vault_dashboard).",
      "500-item vault page fills in < 0.1 s on the worker; a check-in shows in feed and counts within 5 s; every tile opens a list with exactly its count."),
    E("PDM-11", "Vault material library", "M2", "To do", ["PDM-04", "PDM-05"],
      "(new, 24 Oct)",
      "Shop materials (what is bought: 6061-T6 plate, 1045/C45 bar, PETG, POM-C) as FreeCAD cards in <vault>/materials, registered as a material library while the vault is open; engineering values revised and approved like vault items (MAT-0001, rev A, B; datasheet attached), stock forms and price per kg beside them, not revisioned. Parts reference a material by UUID and carry a snapshot pinned at release, so a released part keeps its values and a part opened without the library keeps its material (spike 24 Oct: FreeCAD stores only the UUID, falls back to Default and loses it on save). Picker with density and price; cards without density (Appearance 'Steel') refused everywhere; supplier CSV import; bridge and CLI tools. Plan written 24 Oct, 13 questions waiting.",
      "A supplier CSV becomes four approved materials; revising a material moves WIP parts' mass and leaves a released part's; a part opened and saved with no vault open keeps its material and mass; Claude cannot assign an appearance-only card."),
    E("PDM-08", "Migration from Fusion", "M6", "To do", ["PDM-07"],
      "FC-T01, FCB-P2-22",
      "Bulk import Fusion exports with P/N and revision mapping; switch criteria checklist. Plan written 24 Oct (STEP per component + Fusion manifest, migration CSV, geometry check, 9 FC-T01 criteria) approved 24 Oct; the pilot starts fresh, so migration is M6 work for the other LE items, programs and history.",
      "All LE parts imported, numbered and released; FC-T01 criteria met."),
    # Simulation ----------------------------------------------------------
    E("SIM-01", "Study setup (Fusion-style)", "M4", "To do", [],
      "(new)",
      "Simulation workspace: static, modal, buckling, thermal, thermal-stress studies on CalculiX/Elmer.",
      "Each study type runs from the wizard on a corpus part."),
    E("SIM-02", "Contacts, bolts & preload", "M4", "To do", ["SIM-01"],
      "(new)",
      "Bonded/separation contacts, bolt connectors with preload.",
      "Bolted bracket benchmark within 5 % of reference."),
    E("SIM-03", "Materials incl. FDM", "M4", "To do", ["SIM-01"],
      "(new)",
      "Isotropic + knockdown by default; orthotropic by print orientation; PETG/ABS/PLA/PA-CF library.",
      "Same part, two orientations: Z-weak case shows lower safety factor."),
    E("SIM-04", "Results & reports", "M4", "To do", ["SIM-01"],
      "(new)",
      "Safety factor, mode shapes, probes, mesh convergence, PDF report.",
      "Report regenerates from the study; convergence plot included."),
    E("SIM-05", "Verification benchmarks", "M4", "To do", ["SIM-01"],
      "(new)",
      "Cantilever, plate with hole, Euler column, beam modes vs closed form.",
      "All benchmarks within stated tolerance in CI."),
    E("SIM-06", "CFD / airflow (later)", "M5", "To do", ["SIM-01"],
      "(new)",
      "Prop wash and enclosure cooling via CfdOF/OpenFOAM.",
      "Deferred; scope set when started."),
    # Manufacturing -------------------------------------------------------
    E("MFG-01", "3D-print prep", "M3", "To do", ["MOD-11"],
      "(new)",
      "Print orientation property, overhang map, 3MF export, slicer handoff, insert hole presets.",
      "Part opens in the slicer with orientation preserved."),
    E("MFG-02", "CNC CAM", "M3", "To do", [],
      "(new)",
      "CAM setups and templates, tool library, post-processor for the shop's machine.",
      "Corpus part posts G-code that simulates clean."),
    E("MFG-03", "Sheet-metal flat DXF", "M3", "To do", ["MOD-06"],
      "(new)",
      "Flat DXF with cut and bend layers, bend notes, K-factor per material.",
      "Laser shop accepts DXF unchanged."),
    E("MFG-04", "Release package", "M2", "To do", ["PDM-05", "DRW-08"],
      "(new)",
      "On release: STEP AP242, PDF, DXF/3MF, checksums; attached to the revision.",
      "Package rebuilds byte-identically from the released files."),
    # Robotics & drones ---------------------------------------------------
    E("ROB-01", "COTS library & insert", "M1", "To do", ["MOD-05"],
      "FCB-P2-09/10, P2R-01..04",
      "Per-part metadata (mass, CG, mount pattern), motors/servos/ESCs/props/bearings, insert with mounting LCS.",
      "NEMA17 and 2207 motor inserted and jointed by mounting LCS."),
    E("ROB-02", "Frame & arm templates", "M3", "To do", ["MOD-03"],
      "FCB-P2R-05/06",
      "Parametric quad X-frame and servo arm as configurations.",
      "Template regenerates for three wheelbases."),
    E("ROB-03", "Mass, CG & inertia report", "M1", "To do", ["MOD-11", "ROB-01"],
      "FCB-P2R-07",
      "Per-part mass (density or COTS override), total, CG, inertia; CSV and 3D marker.",
      "X-frame example: 388.1 g, CG (2.45, 0, -2.73) mm."),
    E("ROB-04", "Drone balance & thrust-to-weight", "M3", "To do", ["ROB-03"],
      "FCB-P2R-08",
      "CG vs motor centroid, thrust-to-weight from COTS data.",
      "Check flags an offset battery."),
    E("ROB-05", "Kinematic chains & joint drive", "M5", "To do", ["ASM-03"],
      "FCB-P2A-09/10",
      "Link chain from revolute joints, joint table, FK readout, servo sweeps, video.",
      "2-DOF arm FK matches hand calculation."),
    # ECAD ----------------------------------------------------------------
    E("ECD-01", "ECAD intake & adopt-vs-build", "M1", "Done", [],
      "FCB-P2E-01..04",
      "Requirements intake; StepUp evaluation (AGPL); decision memo. KiCad 10.0.5 and kicad-cli tested on the Mac (23 Oct): STEP export with copper, mask and silk imports in under a second. Decided 23 Oct (your answers, all five as recommended): our own bridge on kicad-cli, StepUp evaluated only for the outline round trip (ECD-03); one PCBA BOM line; component materials guessed by footprint library; KiCad's render as the thumbnail; only you edit boards.",
      "Decision recorded."),
    E("ECD-02", "Board STEP into assembly", "M3", "To do", ["ECD-01"],
      "FCB-P2E-05",
      "kicad-cli STEP export, inserted with LCS at board origin.",
      "Board lands at the right origin."),
    E("ECD-03", "Board outline round-trip", "M3", "To do", ["ECD-01"],
      "FCB-P2E-06, P2E-09",
      "Edge.Cuts to Sketcher and back.",
      "Outline delta < 0.01 mm."),
    E("ECD-04", "Placement sync & enclosure fit", "M3", "To do", ["ECD-02", "ASM-06"],
      "FCB-P2E-07/08",
      "Connector/hole positions both ways; board vs housing interference.",
      "Fit check flags a moved connector."),
    E("ECD-05", "Boards in the vault", "M3", "To do", ["ECD-01", "PDM-04"],
      "(new, 23 Oct)",
      "PCB item type: the KiCad project checked in as one item with a P/N; board STEP and thumbnail made at check-in with kicad-cli; Insert board, newer-version badge and Update board in assemblies.",
      "A board revised in KiCad and checked in updates in its enclosure assembly with one click; joints survive."),
    E("ECD-06", "Board materials & mass", "M3", "In progress", ["ECD-02"],
      "(new, 23 Oct)",
      "Map KiCad bodies to materials on import (_PCB FR-4, _pad/tracks copper, mask and silk appearance only, components by library class); add FR-4 and solder-mask materials.",
      "Board mass within 5 % of a weighed sample; BOM lists FR-4 and copper."),
    # Automation ----------------------------------------------------------
    E("AUT-01", "Headless hooks & CLI", "M0", "Done", [],
      "FCB-P2-11..14",
      "Export STEP, tree JSON, rename/numbering via FreeCADCmd (#5).",
      "Merged (#5)."),
    E("AUT-02", "bruceworx.api", "M1", "To do", ["AUT-01"],
      "FCB-P2AI-03",
      "Python API over the hooks: create/edit part, parameters, tree, export.",
      "API covered by headless tests."),
    E("AUT-03", "MCP server", "M1", "In progress", ["AUT-02"],
      "FCB-P2AI-01/02, 04..08",
      "Own harness built 23-24 Oct: bridge in FreeCAD (localhost, token, session consent, audit log, one undo step per call), stdlib MCP server with a headless FreeCADCmd fallback, .mcp.json. Modelling, vault and KiCad tools. Fixed 24 Oct: a second FreeCAD never takes another live window's bridge (held_by_other); agent test runs get their own BW_BRIDGE_DIR. CI still to do.",
      "Agent creates, queries and exports a part headless in CI."),
    E("AUT-04", "Assistant panel", "M5", "To do", ["AUT-03"],
      "FCB-P2AI-09..15",
      "Dock chat with tool-call preview, apply/undo per action.",
      "Try-out on real CAD work signed off."),
    E("AUT-05", "Command line", "M5", "To do", [],
      "(new, UI plan)",
      "AutoCAD-style aliases, inline Python, CadQuery/build123d (pinned), OpenSCAD; CodeFeature with trust prompt.",
      "CPATTERN @Bolt Q 8 reproduces the component pattern; build123d box lands in the timeline."),
    # UI ------------------------------------------------------------------
    E("UIX-01", "Theme, pref pack & viewport styling", "UI", "Done", [],
      "FCB-P1-01..05, P1V-01..07, P1V-10..18",
      "Preference pack, QSS, viewport colours, ground grid (#8); follow-ups: AA check, Victor try-out. 25 Oct: FreeCAD 1.1's first-start theme picker no longer replaces the pack (a profile it overwrote is healed); the grid is left out of Fit All.",
      "Merged (#8); try-out pending."),
    E("UIX-02", "macOS title bar", "UI", "Done", [],
      "FCB-P1F-01..05, P1F-26",
      "Unified title bar and native buttons (#6).",
      "Merged (#6)."),
    E("UIX-03", "Browser (overlay model tree)", "UI", "In progress", [],
      "FCB-P1F-06..14, P1F-27",
      "Overlay tree via prefs/QSS merged (#6); free-floating panel needs OverlayManager patch. 25 Oct (UAT #7): the transparent Browser's text halo always contrasts with the text, in any theme.",
      "Tree floats top-left at any inset; off switch restores docks."),
    E("UIX-04", "History timeline", "UI", "In progress", [],
      "FCB-P1F-15..25, P1F-28",
      "Spike and mock merged (#7); full timeline with marker drag, edit, suppress, reorder.",
      "Timeline drives Tip and edits on LE parts."),
    E("UIX-05", "Workspaces & tabs (Fusion naming)", "UI", "In progress", [],
      "(new, UI plan)",
      "Design/Simulation/Manufacture/Drawing; Solid/Surface/Mesh/Sheet Metal/Utilities; contextual Sketch. 25 Oct (UAT #10): a tab's groups keep their buttons the first time it shows (toolbars ignore the title strip's safe area).",
      "No workbench switch when changing Design tabs; off switch restores tab bar."),
    E("UIX-06", "Toolbar & panels", "UI", "In progress", ["UIX-05"],
      "(new, UI plan)",
      "Captioned groups, floating command dialog, document tabs on top, one-row timeline. 25 Oct (UAT #2): a narrow window shares the tools row out between the groups; no cut captions.",
      "Matches the redesigned mock at two resolutions."),
    E("UIX-07", "Task panel polish", "UI", "To do", [],
      "FCB-P1-06/07/08",
      "Sketcher, PartDesign and TechDraw dialogs.",
      "Before/after gallery approved."),
    E("UIX-08", "Matcap shading", "UI", "To do", [],
      "FCB-P1V-08/09/13/14/15",
      "Matcap draw style behind a preference. Asked for in the 25 Oct UAT (#6: better rendering with textures), with each part's appearance taken from its material.",
      "Pref off renders stock; light/dark screenshots."),
    E("UIX-09", "Fresh UI design & re-render figures", "UI", "In progress", ["UIX-05"],
      "(new)",
      "New visual design for the whole app; regenerate all roadmap figures.",
      "Design approved; figures regenerated."),
    E("UIX-10", "Undo toasts", "UI", "To do", [],
      "(new, from the Nxt add-on review, 23 Oct)",
      "A short notice after each command with a one-click Undo, bottom centre above the navigation pill.",
      "Pad, fillet, delete and pattern each show a toast; Undo restores the model."),
    E("UIX-11", "Pick to feature & error chips", "UI", "To do", ["UIX-03"],
      "(new, from the Nxt add-on review, 23 Oct)",
      "Click a face: the Browser and timeline show the feature that made it. A failing feature gets a red chip that opens it; dependency lines (Sketch to Pad) in the Browser.",
      "Every face of LE-02001 traces to its feature; a broken sketch shows a chip that opens it."),
    E("UIX-12", "Property inspector", "UI", "Done", ["UIX-07"],
      "(new, from the Nxt add-on review, 23 Oct)",
      "A grouped inspector for a feature, face, body, part or several objects; FreeCAD's editor one click away. Card in the Browser built 24 Oct (bruceworx_inspector.py); redesigned at your request as a tabbed right panel (Properties, Vault, Parameters, Physical): Properties tab built 24 Oct; Physical and Parameters tabs finished and on 25 Oct (local/linked sheet, dependents, ml shown as cm3). Also 24 Oct evening: Create Vault opens as a full-window tab; document tabs at the bottom or top (Preferences > BruceWorx > Document tabs, default bottom) with the Start page pinned as the first tab. Done 25 Oct: the acceptance is a GUI test (TestBruceWorxInspector.EditInPlaceTests: Pad Distance, Pocket Depth and Fillet Radius typed on the Properties tab, one named undo step each), passed 3 runs of 3 with no crash; Physical, Parameters and Document tabs suites pass in the GUI.",
      "Pad, Pocket and Fillet edited from the inspector without the Data tab."),
    E("UIX-13", "Trackpad navigation", "UI", "Done", [],
      "(new, from the 25 Oct UAT, #9)",
      "Two-finger swipe on a Mac trackpad: plain swipe pans (the model follows the fingers), Shift + swipe zooms at the pointer (as a wheel turn: zoom step, Invert zoom, Zoom at cursor), Cmd + swipe orbits about the point a mouse orbit would pick. The trackpad is told apart from a mouse wheel by its scroll phase; a mouse wheel and pinch are unchanged, in every navigation style. NavigationStyle::processTrackpadScroll; switch BruceWorx/TrackpadGestures, on (off: a swipe zooms, as stock FreeCAD). Built 25 Oct (4eed4ca); TestBruceWorxTrackpad (synthetic swipes on the 3D view) passes in the GUI.",
      "Pan, zoom and orbit by swipe on the real trackpad feel right; a mouse wheel still zooms. Try-out pending."),
    # Kernel --------------------------------------------------------------
    E("KRN-01", "OCCT coupling survey", "BG", "To do", [],
      "FCB-P3-01..06",
      "Count OCCT use per module, TNP/element-map dependencies, Python exposure; option A note.",
      "Coupling report published."),
    E("KRN-02", "STEP exchange harness", "BG", "To do", ["FND-05"],
      "FCB-P3-07..11",
      "Separate harness repo, LE corpus, round-trip metrics, CI.",
      "Harness runs on Linux CI."),
    E("KRN-03", "Kernel decision", "BG", "To do", ["KRN-01", "KRN-02"],
      "FCB-P3-12/13/14",
      "A/B/C memo with effort estimates; gate.",
      "Direction picked."),
]


def tex_escape(s):
    return (s.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%")
            .replace("_", r"\_").replace("#", r"\#").replace("$", r"\$").replace("~", r"\textasciitilde{}")
            .replace("^", r"\textasciicircum{}").replace("<", r"\textless{}").replace(">", r"\textgreater{}"))


def main():
    ids = {e["id"] for e in EPICS}
    for e in EPICS:
        for d in e["depends"].split():
            assert d in ids, f"{e['id']} depends on unknown {d}"
    csv_path = os.path.join(ROOT, "roadmap-epics.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "track", "title", "milestone", "milestone_name", "status", "depends_on",
                    "origin_ideas", "summary", "acceptance"])
        for e in EPICS:
            w.writerow([e["id"], e["track"], e["title"], e["milestone"], MILESTONES[e["milestone"]],
                        e["status"], e["depends"], e["origin"], e["summary"], e["acceptance"]])
    rows = []
    track = None
    for e in EPICS:
        if e["track"] != track:
            track = e["track"]
            rows.append(r"\midrule\multicolumn{5}{@{}l}{\textbf{" + tex_escape(track) + r"}}\\")
        status = {"Done": r"\stDone", "In progress": r"\stProg", "To do": r"\stTodo"}[e["status"]]
        rows.append(" & ".join([r"\code{" + e["id"][3:] + "}", tex_escape(e["title"]), e["milestone"],
                                r"\scriptsize " + tex_escape(e["depends"].replace("BW-", "")) if e["depends"] else "---",
                                status]) + r"\\")
    tex_path = os.path.join(ROOT, "chapters", "gen-epics.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w") as fh:
        fh.write("% Generated by scripts/epics.py; do not edit.\n")
        fh.write(r"\begin{longtable}{@{}p{1.4cm}p{5.8cm}p{0.9cm}p{4.3cm}p{1.95cm}@{}}" + "\n")
        fh.write(r"\toprule ID & Epic & Mile. & Depends on & Status\\ \midrule\endhead" + "\n")
        fh.write(r"\bottomrule\endfoot" + "\n")
        fh.write("\n".join(rows) + "\n")
        fh.write(r"\end{longtable}" + "\n")
    # Swimlane: tracks x milestones, each cell lists its epics coloured by status.
    order = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "UI", "BG"]
    colour = {"Done": "bwgreen", "In progress": "bworange", "To do": "bwdark"}
    lane = [r"% Generated by scripts/epics.py; do not edit.",
            r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{2.35cm}*{9}{>{\centering\arraybackslash}p{1.12cm}}@{}}",
            r"\toprule",
            " & ".join([r"\textbf{Track}"] + [r"\textbf{" + m + "}" for m in order]) + r"\\",
            r"\midrule"]
    for key, name in TRACKS.items():
        cells = []
        for m in order:
            ids = [e for e in EPICS if e["id"][3:6] == key and e["milestone"] == m]
            cells.append(" ".join(r"\textcolor{" + colour[e["status"]] + "}{" + e["id"][7:] + "}"
                                          for e in ids))
        lane.append(r"\scriptsize " + tex_escape(name) + " & "
                    + " & ".join(r"\scriptsize " + c if c else "" for c in cells)
                    + r"\\[2pt]")
    lane += [r"\bottomrule", r"\end{tabular}"]
    with open(os.path.join(ROOT, "chapters", "gen-swimlanes.tex"), "w") as fh:
        fh.write("\n".join(lane) + "\n")
    counts = {}
    for e in EPICS:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(f"{len(EPICS)} epics -> {csv_path}, {tex_path}; {counts}")


if __name__ == "__main__":
    main()
