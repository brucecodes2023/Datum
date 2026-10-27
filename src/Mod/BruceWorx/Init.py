# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx app init. No workbench; the GUI hook lives in InitGui.py."""

import FreeCAD

FreeCAD.__unit_test__.append("TestBruceWorx")
FreeCAD.__unit_test__.append("TestBruceWorxPattern")
FreeCAD.__unit_test__.append("TestBruceWorxBrowser")
FreeCAD.__unit_test__.append("TestBruceWorxPickTrace")
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
FreeCAD.__unit_test__.append("TestBruceWorxGaps")
FreeCAD.__unit_test__.append("TestBruceWorxPreview")
FreeCAD.__unit_test__.append("TestBruceWorxChanges")
FreeCAD.__unit_test__.append("TestBruceWorxBom")
FreeCAD.__unit_test__.append("TestBruceWorxProject")
FreeCAD.__unit_test__.append("TestBruceWorxConfigs")
FreeCAD.__unit_test__.append("TestBruceWorxBridge")
FreeCAD.__unit_test__.append("TestBruceWorxDrawing")
FreeCAD.__unit_test__.append("TestBruceWorxUnits")
FreeCAD.__unit_test__.append("TestBruceWorxBoard")
FreeCAD.__unit_test__.append("TestBruceWorxInterference")
FreeCAD.__unit_test__.append("TestBruceWorxStudio")
FreeCAD.__unit_test__.append("TestBruceWorxAnalysis")
FreeCAD.__unit_test__.append("TestBruceWorxHardware")
FreeCAD.__unit_test__.append("TestBruceWorxSimCore")

# BruceWorx's own material library (FR-4, PCB copper, ...), also used headless.
try:
    import bruceworx_board

    bruceworx_board.register_materials()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"BruceWorx materials were not registered: {exc}\n")

# Simulation (SIM-01): a fresh profile finds the bundled CalculiX (Keystone: Contents/bin/ccx).
try:
    import bruceworx_sim

    bruceworx_sim.ensure_ccx_pref()
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"BruceWorx could not look for CalculiX: {exc}\n")
