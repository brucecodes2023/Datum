# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] Python-only: preference page, overlay model tree, preference-pack
# helper (applied on first start), shared BruceWorx menu, ground grid and
# Rectangular/Circular Pattern commands and the two-row toolbar layout.
# No workbench, no C++.

SET(BruceWorx_SRCS
    Init.py
    InitGui.py
    BruceWorx.py
    TestBruceWorx.py
    TestBruceWorxPattern.py
    TestBruceWorxNavPill.py
    TestBruceWorxTrackpad.py
    TestBruceWorxGroups.py
    TestBruceWorxWorkspaces.py
    TestBruceWorxSearch.py
    TestBruceWorxStart.py
    TestBruceWorxTitleBar.py
    TestBruceWorxVaultWizard.py
    TestBruceWorxPreview.py
    TestBruceWorxVaultClient.py
    TestBruceWorxGaps.py
    TestBruceWorxBridge.py
    preferences-bruceworx.ui
    preferences-bruceworx.svg
    BruceWorxModelTree.qss
    bruceworx_pack.py
    bruceworx_defaults.py
    bruceworx_menu.py
    ground_grid.py
    bruceworx_pattern.py
    bruceworx_pattern_gui.py
    bruceworx_section.py
    bruceworx_slots.py
    bruceworx_routing.py
    bruceworx_routing_gui.py
    bruceworx_routing_ports.py
    TestBruceWorxRoutingCore.py
    TestBruceWorxRouting.py
    TestBruceWorxConnectorLibrary.py
    BruceWorx_RoutePath.svg
    BruceWorx_RouteAddPoint.svg
    BruceWorx_RouteClip.svg
    BruceWorx_RouteWire.svg
    BruceWorx_RoutePipe.svg
    BruceWorx_RouteHose.svg
    BruceWorx_RouteBundle.svg
    BruceWorx_RouteFromTo.svg
    BruceWorx_RouteCutList.svg
    BruceWorx_RouteBendRules.svg
    bruceworx_studio.py
    bruceworx_toolbars.py
    bruceworx_groups.py
    bruceworx_workspaces.py
    bruceworx_wsload.py
    workspaces.json
    bruceworx_search.py
    settings_index.json
    bruceworx_titlebar.py
    bruceworx_grid.py
    bruceworx_appearance.py
    bruceworx_taskcard.py
    bruceworx_statusbar.py
    bruceworx_start.py
    bruceworx_dashboard.py
    bruceworx_vaultwizard.py
    bruceworx_preview.py
    bruceworx_vaultclient.py
    bruceworx_changes.py
    TestBruceWorxChanges.py
    bruceworx_bom.py
    bruceworx_structure.py
    bruceworx_project.py
    TestBruceWorxProject.py
    TestBruceWorxBom.py
    bruceworx_configs.py
    TestBruceWorxConfigs.py
    bruceworx_configs_gui.py
    bruceworx_bridge.py
    bruceworx_bridge_tools.py
    bruceworx_browser.py
    TestBruceWorxBrowser.py
    bruceworx_picktrace.py
    TestBruceWorxPickTrace.py
    TestBruceWorxTimeline.py
    bruceworx_inspector.py
    bruceworx_rightpanel.py
    bruceworx_testwindow.py
    bruceworx_doctabs.py
    TestBruceWorxDocTabs.py
    TestBruceWorxTestWindow.py
    TestBruceWorxRightPanel.py
    TestBruceWorxInspector.py
    bruceworx_physical.py
    TestBruceWorxPhysical.py
    bruceworx_parameters.py
    TestBruceWorxParameters.py
    bruceworx_toasts.py
    TestBruceWorxToasts.py
    TestBruceWorxSection.py
    bruceworx_interference.py
    TestBruceWorxInterference.py
    bruceworx_interference_gui.py
    BruceWorx_Interference.svg
    TestBruceWorxStudio.py
    bruceworx_oncanvas.py
    TestBruceWorxOnCanvas.py
    bruceworx_sketchrepair.py
    TestBruceWorxSketchRepair.py
    bruceworx_navpill.py
    bruceworx_drawing.py
    TestBruceWorxDrawing.py
    bruceworx_units.py
    TestBruceWorxUnits.py
    BruceWorx_RectangularPattern.svg
    BruceWorx_CircularPattern.svg
    BruceWorx_Section.svg
    bruceworx_analysis.py
    TestBruceWorxAnalysis.py
    bruceworx_analysis_gui.py
    TestBruceWorxInspect.py
    BruceWorx_Overhang.svg
    BruceWorx_Draft.svg
    BruceWorx_Thickness.svg
    BruceWorx_Curvature.svg
    BruceWorx_Zebra.svg
    BruceWorx_MassProperties.svg
    bruceworx_board.py
    bruceworx_board_gui.py
    BruceWorx_InsertBoard.svg
    TestBruceWorxBoard.py
    bruceworx_sheetmetal.py
    BruceWorx_ExportFlat.svg
    BruceWorx_SheetMetalRule.svg
    TestBruceWorxSheetMetal.py
    bruceworx_hardware.py
    TestBruceWorxHardware.py
    BruceWorx_HolePreset.svg
    BruceWorx_CosmeticThread.svg
    BruceWorx_HoleCallouts.svg
    BruceWorx_InsertFastener.svg
    BruceWorx_MarkCots.svg
    bruceworx_sim.py
    TestBruceWorxSimCore.py
    bruceworx_sim_gui.py
    TestBruceWorxSimulation.py
    BruceWorx_NewStudy.svg
    BruceWorx_SafetyFactor.svg
    BruceWorx_Probe.svg
    BruceWorx_StudyReport.svg
    BruceWorx_PreCheck.svg
    bruceworx_motion.py
    TestBruceWorxMotion.py
    BruceWorx_Joint.svg
    BruceWorx_DriveJoint.svg
    BruceWorx_MotionStudy.svg
    BruceWorx_MotionLink.svg
    bruceworx_materials.py
    TestBruceWorxMaterials.py
)

# [BW] BruceWorx's own material library (KiCad boards, ECD-06), registered with
# the Material module as a workbench library; upstream material folders untouched.
SET(BruceWorx_Materials
    "Materials/Electronics/Ceramic Chip.FCMat"
    "Materials/Electronics/Contact Metal.FCMat"
    "Materials/Electronics/Copper Finish ENIG.FCMat"
    "Materials/Electronics/Copper Finish HASL.FCMat"
    "Materials/Electronics/Electronics Plastic.FCMat"
    "Materials/Electronics/Epoxy Mold Compound.FCMat"
    "Materials/Electronics/FR-4.FCMat"
    "Materials/Electronics/PBT-GF30.FCMat"
    "Materials/Electronics/PCB Copper.FCMat"
    "Materials/Electronics/Silkscreen Ink.FCMat"
    "Materials/Electronics/Solder Mask.FCMat"
)

ADD_CUSTOM_TARGET(BruceWorx ALL
    SOURCES ${BruceWorx_SRCS}
)

fc_copy_sources(BruceWorx "${CMAKE_BINARY_DIR}/Mod/BruceWorx" ${BruceWorx_SRCS})

ADD_CUSTOM_TARGET(BruceWorxMaterials ALL
    SOURCES ${BruceWorx_Materials}
)
fc_target_copy_resource(BruceWorxMaterials
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_Materials})
INSTALL(
    DIRECTORY Materials
    DESTINATION Mod/BruceWorx
    FILES_MATCHING PATTERN "*.FCMat"
)

# [BW] The Keystone material library (SIM-01): engineering cards with yield strengths,
# registered as the read-only library "Keystone" by Init.py (bruceworx_materials.py).
SET(BruceWorx_Keystone_Materials
    "Resources/Materials/Keystone/Composites/Carbon fibre plate QI.FCMat"
    "Resources/Materials/Keystone/Composites/G10-FR4 laminate.FCMat"
    "Resources/Materials/Keystone/Metals/Aluminium 5052-H32.FCMat"
    "Resources/Materials/Keystone/Metals/Aluminium 6061-T6.FCMat"
    "Resources/Materials/Keystone/Metals/Aluminium 7075-T6.FCMat"
    "Resources/Materials/Keystone/Metals/Brass C360 half hard.FCMat"
    "Resources/Materials/Keystone/Metals/Stainless 304 annealed.FCMat"
    "Resources/Materials/Keystone/Metals/Stainless 316 annealed.FCMat"
    "Resources/Materials/Keystone/Metals/Steel 1018 cold drawn.FCMat"
    "Resources/Materials/Keystone/Metals/Steel 1045 cold drawn.FCMat"
    "Resources/Materials/Keystone/Metals/Steel 4140 annealed.FCMat"
    "Resources/Materials/Keystone/Metals/Steel 4140 pre-hardened.FCMat"
    "Resources/Materials/Keystone/Metals/Steel mild CR sheet.FCMat"
    "Resources/Materials/Keystone/Plastics/Acrylic PMMA cast.FCMat"
    "Resources/Materials/Keystone/Plastics/PA6 extruded dry.FCMat"
    "Resources/Materials/Keystone/Plastics/PC sheet.FCMat"
    "Resources/Materials/Keystone/Plastics/POM-C acetal copolymer.FCMat"
    "Resources/Materials/Keystone/Plastics/POM-H Delrin 150.FCMat"
    "Resources/Materials/Keystone/Plastics/UHMW-PE.FCMat"
    "Resources/Materials/Keystone/Printed/ABS printed.FCMat"
    "Resources/Materials/Keystone/Printed/ASA printed.FCMat"
    "Resources/Materials/Keystone/Printed/PA12-CF printed.FCMat"
    "Resources/Materials/Keystone/Printed/PA6-CF printed.FCMat"
    "Resources/Materials/Keystone/Printed/PC printed.FCMat"
    "Resources/Materials/Keystone/Printed/PETG HF printed.FCMat"
    "Resources/Materials/Keystone/Printed/PETG printed.FCMat"
    "Resources/Materials/Keystone/Printed/PLA printed.FCMat"
    "Resources/Materials/Keystone/Printed/TPU 95A printed.FCMat"
)
ADD_CUSTOM_TARGET(BruceWorxKeystoneMaterials ALL
    SOURCES ${BruceWorx_Keystone_Materials}
)
fc_target_copy_resource(BruceWorxKeystoneMaterials
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_Keystone_Materials})
INSTALL(
    DIRECTORY Resources/Materials/Keystone
    DESTINATION Mod/BruceWorx/Resources/Materials
    FILES_MATCHING PATTERN "*.FCMat"
)

INSTALL(
    FILES ${BruceWorx_SRCS}
    DESTINATION Mod/BruceWorx
)

# [BW] Drawing templates with the title block that fills itself (DRW-01),
# generated by make_templates.py (not installed); upstream TechDraw templates untouched.
SET(BruceWorx_Templates
    Templates/ANSI_A_Landscape.svg
    Templates/ANSI_A_Portrait.svg
    Templates/ANSI_B_Landscape.svg
    Templates/ANSI_C_Landscape.svg
    Templates/ANSI_D_Landscape.svg
    Templates/ISO_A4_Landscape.svg
    Templates/ISO_A4_Portrait.svg
    Templates/ISO_A3_Landscape.svg
    Templates/ISO_A2_Landscape.svg
    Templates/ISO_A1_Landscape.svg
)
ADD_CUSTOM_TARGET(BruceWorxTemplates ALL
    SOURCES ${BruceWorx_Templates} make_templates.py
)
fc_target_copy_resource(BruceWorxTemplates
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_Templates})
INSTALL(
    FILES ${BruceWorx_Templates}
    DESTINATION Mod/BruceWorx/Templates
)
