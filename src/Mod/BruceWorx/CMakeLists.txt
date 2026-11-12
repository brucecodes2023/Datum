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
    bruceworx_rope.py
    bruceworx_drag.py
    bruceworx_flexsub.py
    bruceworx_routing_gui.py
    bruceworx_routing_ports.py
    bruceworx_fittings.py
    bruceworx_stock.py
    bruceworx_harness.py
    TestBruceWorxRoutingCore.py
    TestBruceWorxRopeCore.py
    TestBruceWorxFlexSub.py
    TestBruceWorxRouting.py
    TestBruceWorxHarness.py
    TestBruceWorxConnectorLibrary.py
    BruceWorx_RoutePath.svg
    BruceWorx_RouteFrame.svg
    BruceWorx_RouteFitting.svg
    BruceWorx_RouteTerminals.svg
    BruceWorx_RouteAddPoint.svg
    BruceWorx_RouteClip.svg
    BruceWorx_RouteWire.svg
    BruceWorx_RoutePipe.svg
    BruceWorx_RouteHose.svg
    BruceWorx_RouteBundle.svg
    BruceWorx_RouteHarness.svg
    BruceWorx_RouteMakeVaultItem.svg
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
    bruceworx_projectpage.py
    TestBruceWorxProjectPage.py
    bruceworx_vaultwizard.py
    bruceworx_preview.py
    bruceworx_previewmesh.py
    bruceworx_vlsim.py
    bruceworx_vaultbrowser.py
    bruceworx_package.py
    TestBruceWorxPackage.py
    TestBruceWorxVlsim.py
    bruceworx_sim_vault.py
    bruceworx_sim_vault_ui.py
    TestBruceWorxSimVault.py
    TestBruceWorxVaultBrowser.py
    TestBruceWorxPreviewMesh.py
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
    bruceworx_fastopen.py
    TestBruceWorxPickTrace.py
    bruceworx_selectfilter.py
    TestBruceWorxSelectFilter.py
    TestBruceWorxSelectFilterGui.py
    bruceworx_autoactivate.py
    TestBruceWorxAutoActivate.py
    TestBruceWorxTreeActiveOnly.py
    TestBruceWorxTreeRootIcon.py
    TestBruceWorxFastOpen.py
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
    bruceworx_massreport.py
    TestBruceWorxBalance.py
    bruceworx_massreport_gui.py
    BruceWorx_DroneBalance.svg
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
    TestBruceWorxDrawingGui.py
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
    bruceworx_sim_eigen.py
    bruceworx_sim_thermal.py
    bruceworx_sim_post.py
    TestBruceWorxSimRotor.py
    TestBruceWorxDeckWrite.py
    TestBruceWorxSimCore.py
    bruceworx_cfd.py
    bruceworx_cfd_gui.py
    bruceworx_cfd_wash.py
    bruceworx_cfd_paraview.py
    TestBruceWorxCfd.py
    TestBruceWorxCfdWash.py
    TestBruceWorxCfdParaView.py
    bruceworx_sim_gui.py
    bruceworx_sim_events.py
    bruceworx_sim_meshworker.py
    bruceworx_sim_workspace.py
    bruceworx_sim_panes.py
    TestBruceWorxSimWorkspace.py
    TestBruceWorxSimulation.py
    bruceworx_sim_model.py
    bruceworx_sim_defs.py
    bruceworx_sim_steps.py
    TestBruceWorxSimModel.py
    TestBruceWorxAsmModel.py
    TestBruceWorxAsmPanes.py
    bruceworx_asm_model.py
    bruceworx_asm_panes.py
    bruceworx_validation.py
    bruceworx_validation_cases.py
    bruceworx_validation_gui.py
    BruceWorx_Validation.svg
    TestBruceWorxValidation.py
    TestBruceWorxValidationGui.py
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
    bruceworx_api_save.py
    bruceworx_agent.py
    TestBruceWorxAgent.py
    bruceworx_codecad.py
    bruceworx_codecad_templates.py
    bruceworx_codecad_gui.py
    bruceworx_codecad_dims.py
    TestBruceWorxCodeCadGui.py
    BruceWorx_CodePart.svg
    BruceWorx_CodePartError.svg
    BruceWorx_CodePartLocked.svg
    BruceWorx_CodePartRebuild.svg
    TestBruceWorxCodeCad.py
    bruceworx_api_docs.py
    bruceworx_api_asm.py
    TestBruceWorxApi.py
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

# [BW] The Vector Labs material library (SIM-01): engineering cards with yield strengths,
# registered as the read-only library "Vector Labs" by Init.py (bruceworx_materials.py).
SET(BruceWorx_VectorLabs_Materials
    "Resources/Materials/VectorLabs/Composites/Carbon fibre plate QI.FCMat"
    "Resources/Materials/VectorLabs/Composites/G10-FR4 laminate.FCMat"
    "Resources/Materials/VectorLabs/Metals/Aluminium 5052-H32.FCMat"
    "Resources/Materials/VectorLabs/Metals/Aluminium 6061-T6.FCMat"
    "Resources/Materials/VectorLabs/Metals/Aluminium 7075-T6.FCMat"
    "Resources/Materials/VectorLabs/Metals/Brass C360 half hard.FCMat"
    "Resources/Materials/VectorLabs/Metals/Stainless 304 annealed.FCMat"
    "Resources/Materials/VectorLabs/Metals/Stainless 316 annealed.FCMat"
    "Resources/Materials/VectorLabs/Metals/Stainless 17-4PH H1150.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel 1018 cold drawn.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel 1045 cold drawn.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel 4140 annealed.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel 4140 pre-hardened.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel A36 structural.FCMat"
    "Resources/Materials/VectorLabs/Metals/Steel mild CR sheet.FCMat"
    "Resources/Materials/VectorLabs/Metals/Titanium Ti-6Al-4V annealed.FCMat"
    "Resources/Materials/VectorLabs/Plastics/Acrylic PMMA cast.FCMat"
    "Resources/Materials/VectorLabs/Plastics/PA6 extruded dry.FCMat"
    "Resources/Materials/VectorLabs/Plastics/PC sheet.FCMat"
    "Resources/Materials/VectorLabs/Plastics/POM-C acetal copolymer.FCMat"
    "Resources/Materials/VectorLabs/Plastics/POM-H Delrin 150.FCMat"
    "Resources/Materials/VectorLabs/Plastics/UHMW-PE.FCMat"
    "Resources/Materials/VectorLabs/Printed/ABS printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/ASA-CF printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/ASA printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PA12-CF printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PA6-CF printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PC printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PETG HF printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PETG-CF printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PETG printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/PLA printed.FCMat"
    "Resources/Materials/VectorLabs/Printed/TPU 95A printed.FCMat"
)
ADD_CUSTOM_TARGET(BruceWorxVectorLabsMaterials ALL
    SOURCES ${BruceWorx_VectorLabs_Materials}
)
fc_target_copy_resource(BruceWorxVectorLabsMaterials
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_VectorLabs_Materials})
INSTALL(
    DIRECTORY Resources/Materials/VectorLabs
    DESTINATION Mod/BruceWorx/Resources/Materials
    FILES_MATCHING PATTERN "*.FCMat"
)

# [BW] SIM-07 Model Builder icons: one SVG per sprite symbol of the mockups (node icons and state marks).
SET(BruceWorx_SimIcons
    Resources/icons/sim/i-defs.svg
    Resources/icons/sim/i-disk.svg
    Resources/icons/sim/i-fixed.svg
    Resources/icons/sim/i-flow.svg
    Resources/icons/sim/i-force.svg
    Resources/icons/sim/i-geo.svg
    Resources/icons/sim/i-grav.svg
    Resources/icons/sim/i-mat.svg
    Resources/icons/sim/i-mesh.svg
    Resources/icons/sim/i-param.svg
    Resources/icons/sim/i-phys.svg
    Resources/icons/sim/i-plot.svg
    Resources/icons/sim/i-press.svg
    Resources/icons/sim/i-probe.svg
    Resources/icons/sim/i-report.svg
    Resources/icons/sim/i-results.svg
    Resources/icons/sim/i-sel.svg
    Resources/icons/sim/i-solver.svg
    Resources/icons/sim/i-step.svg
    Resources/icons/sim/i-study.svg
    Resources/icons/sim/s-bad.svg
    Resources/icons/sim/s-ok.svg
    Resources/icons/sim/s-run.svg
    Resources/icons/sim/s-stale.svg
    Resources/icons/sim/s-todo.svg
    Resources/icons/sim/s-warn.svg
)
ADD_CUSTOM_TARGET(BruceWorxSimIcons ALL
    SOURCES ${BruceWorx_SimIcons}
)
fc_target_copy_resource(BruceWorxSimIcons
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_SimIcons})
INSTALL(
    FILES ${BruceWorx_SimIcons}
    DESTINATION Mod/BruceWorx/Resources/icons/sim
)

# [BW] SIM-05 validation cases: published reference values with citations (numbers only), for bruceworx_validation.py.
SET(BruceWorx_Validation
    Resources/Validation/cases.json
)
ADD_CUSTOM_TARGET(BruceWorxValidation ALL
    SOURCES ${BruceWorx_Validation}
)
fc_target_copy_resource(BruceWorxValidation
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/Mod/BruceWorx
    ${BruceWorx_Validation})
INSTALL(
    FILES ${BruceWorx_Validation}
    DESTINATION Mod/BruceWorx/Resources/Validation
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
