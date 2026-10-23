# SPDX-License-Identifier: LGPL-2.1-or-later
"""BruceWorx app init. No workbench; the GUI hook lives in InitGui.py."""

import FreeCAD

FreeCAD.__unit_test__.append("TestBruceWorx")
FreeCAD.__unit_test__.append("TestBruceWorxPattern")
FreeCAD.__unit_test__.append("TestBruceWorxBrowser")
FreeCAD.__unit_test__.append("TestBruceWorxNavPill")
FreeCAD.__unit_test__.append("TestBruceWorxGroups")
FreeCAD.__unit_test__.append("TestBruceWorxWorkspaces")
FreeCAD.__unit_test__.append("TestBruceWorxSearch")
FreeCAD.__unit_test__.append("TestBruceWorxStart")
FreeCAD.__unit_test__.append("TestBruceWorxTitleBar")
FreeCAD.__unit_test__.append("TestBruceWorxVaultWizard")
