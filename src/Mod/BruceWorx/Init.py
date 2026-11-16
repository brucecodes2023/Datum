# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx app init. No workbench; the GUI hook lives in InitGui.py."""

import FreeCAD

FreeCAD.__unit_test__.append("TestBruceWorx")
FreeCAD.__unit_test__.append("TestBruceWorxPattern")
FreeCAD.__unit_test__.append("TestBruceWorxBrowser")
FreeCAD.__unit_test__.append("TestBruceWorxPickTrace")
FreeCAD.__unit_test__.append("TestBruceWorxSelectFilter")
FreeCAD.__unit_test__.append("TestBruceWorxSelectFilterGui")
FreeCAD.__unit_test__.append("TestBruceWorxAutoActivate")
FreeCAD.__unit_test__.append("TestBruceWorxTreeActiveOnly")
FreeCAD.__unit_test__.append("TestBruceWorxFastOpen")
FreeCAD.__unit_test__.append("TestBruceWorxToasts")
FreeCAD.__unit_test__.append("TestBruceWorxOnCanvas")
FreeCAD.__unit_test__.append("TestBruceWorxSketchRepair")
FreeCAD.__unit_test__.append("TestBruceWorxNavPill")
FreeCAD.__unit_test__.append("TestBruceWorxTrackpad")
FreeCAD.__unit_test__.append("TestBruceWorxGroups")
FreeCAD.__unit_test__.append("TestBruceWorxWorkspaces")
FreeCAD.__unit_test__.append("TestBruceWorxSearch")
FreeCAD.__unit_test__.append("TestBruceWorxStart")
FreeCAD.__unit_test__.append("TestBruceWorxTitleBar")
FreeCAD.__unit_test__.append("TestBruceWorxVaultWizard")
FreeCAD.__unit_test__.append("TestBruceWorxVaultClient")
FreeCAD.__unit_test__.append("TestBruceWorxPreviewMesh")
FreeCAD.__unit_test__.append("TestBruceWorxVaultBrowser")
FreeCAD.__unit_test__.append("TestBruceWorxPackage")
FreeCAD.__unit_test__.append("TestBruceWorxGaps")
FreeCAD.__unit_test__.append("TestBruceWorxPreview")
FreeCAD.__unit_test__.append("TestBruceWorxChanges")
FreeCAD.__unit_test__.append("TestBruceWorxBom")
FreeCAD.__unit_test__.append("TestBruceWorxProject")
FreeCAD.__unit_test__.append("TestBruceWorxConfigs")
FreeCAD.__unit_test__.append("TestBruceWorxBridge")
FreeCAD.__unit_test__.append("TestBruceWorxCodeCad")
FreeCAD.__unit_test__.append("TestBruceWorxAgent")
FreeCAD.__unit_test__.append("TestBruceWorxPrintPrep")
FreeCAD.__unit_test__.append("TestBruceWorxDirectEdit")
FreeCAD.__unit_test__.append("TestBruceWorxCots")
FreeCAD.__unit_test__.append("TestBruceWorxRevTable")
FreeCAD.__unit_test__.append("TestBruceWorxTopDown")
FreeCAD.__unit_test__.append("TestBruceWorxInContext")
FreeCAD.__unit_test__.append("TestBruceWorxMating")
FreeCAD.__unit_test__.append("TestBruceWorxSurfacing")
FreeCAD.__unit_test__.append("TestBruceWorxWorksheet")
FreeCAD.__unit_test__.append("TestBruceWorxPartFiles")
FreeCAD.__unit_test__.append("TestBruceWorxDrawingEpics")
FreeCAD.__unit_test__.append("TestBruceWorxRibs")
FreeCAD.__unit_test__.append("TestBruceWorxApi")
FreeCAD.__unit_test__.append("TestBruceWorxDrawing")
FreeCAD.__unit_test__.append("TestBruceWorxUnits")
FreeCAD.__unit_test__.append("TestBruceWorxBoard")
FreeCAD.__unit_test__.append("TestBruceWorxInterference")
FreeCAD.__unit_test__.append("TestBruceWorxStudio")
FreeCAD.__unit_test__.append("TestBruceWorxAnalysis")
FreeCAD.__unit_test__.append("TestBruceWorxHardware")
FreeCAD.__unit_test__.append("TestBruceWorxRoutingCore")
FreeCAD.__unit_test__.append("TestBruceWorxSimCore")
FreeCAD.__unit_test__.append("TestBruceWorxSimRotor")
FreeCAD.__unit_test__.append("TestBruceWorxDeckWrite")
FreeCAD.__unit_test__.append("TestBruceWorxSimModel")
FreeCAD.__unit_test__.append("TestBruceWorxCfd")
FreeCAD.__unit_test__.append("TestBruceWorxSimWorkspace")
FreeCAD.__unit_test__.append("TestBruceWorxCfdParaView")
FreeCAD.__unit_test__.append("TestBruceWorxMaterials")
FreeCAD.__unit_test__.append("TestBruceWorxVlsim")
FreeCAD.__unit_test__.append("TestBruceWorxSimVault")

# BruceWorx's own material library (FR-4, PCB copper, ...), also used headless.
try:
    import bruceworx_board

    bruceworx_board.register_materials()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"BruceWorx materials were not registered: {exc}\n")

# The Heuretes FreeCAD material library (SIM-01: cards with yield strengths), also used headless.
try:
    import bruceworx_materials

    bruceworx_materials.register_library()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"The Heuretes FreeCAD material library was not registered: {exc}\n")

# Simulation files (PDM-14): File > Open, Open Recent and Finder open *.vlsim as normal documents.
try:
    import bruceworx_vlsim

    bruceworx_vlsim.register()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"The .vlsim file type was not registered: {exc}\n")

# Simulation (SIM-01): a fresh profile finds the bundled CalculiX (Heuretes FreeCAD: Contents/bin/ccx).
try:
    import bruceworx_sim

    bruceworx_sim.ensure_ccx_pref()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"BruceWorx could not look for CalculiX: {exc}\n")

# Rib stations (MOD-02): a changed rib count adds or removes stations right after a recompute, also
# headless (the MCP's set_parameter).
try:
    import bruceworx_ribs

    bruceworx_ribs.install_observer()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"BruceWorx rib stations were not registered: {exc}\n")
