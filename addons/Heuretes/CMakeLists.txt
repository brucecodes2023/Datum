# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] BruceWorx hooks add-on (Python only), installed with the normal build
# as Mod/HeuretesHooks: copied into <build>/Mod/HeuretesHooks (what
# build/<preset>/bin/FreeCAD loads) and installed to Mod/HeuretesHooks.
#
# The folder name differs from src/Mod/BruceWorx on purpose: FreeCAD keys
# modules by lower-cased folder name, so a second "BruceWorx" would replace
# the preference module.
#
# package.xml is not installed: with a package.xml that lists no workbench,
# FreeCAD skips Init.py / InitGui.py. hooks.py goes to cli/ so the add-on's
# top folder (on sys.path) has no module named "hooks".
#
# Build switch: -DBUILD_BW_HOOKS=OFF. Runtime switch: Preferences -> BruceWorx.

SET(BruceWorxHooks_SRCS
    Init.py
    InitGui.py
    LICENSE
    README.md
    heuretes_freecad/__init__.py
    heuretes_freecad/env.py
    heuretes_freecad/__main__.py
    heuretes_freecad/cli.py
    heuretes_freecad/config_path.py
    heuretes_freecad/freecad_io.py
    heuretes_freecad/gui.py
    heuretes_freecad/macro.py
    heuretes_freecad/numbering.py
    heuretes_freecad/partprops.py
    heuretes_freecad/pdm.py
    heuretes_freecad/pdm_folder.py
    heuretes_freecad/release_check.py
    heuretes_freecad/sim_reports.py
    heuretes_freecad/sources.py
    heuretes_freecad/step_export.py
    heuretes_freecad/tree.py
    heuretes_freecad/vault_setup.py
    heuretes_freecad/drawing_standards.py
    heuretes_freecad/pdm_workspace.py
    heuretes_freecad/vault_move.py
    heuretes_freecad/renumber.py
    heuretes_freecad/kicad_bridge.py
    heuretes_freecad/ecad_vault.py
    heuretes_freecad/sheetmetal_rules.py
    heuretes_freecad/flat_dxf.py
    heuretes_freecad/flat_export.py
    heuretes_freecad/bom.py
    heuretes_freecad/vault_dashboard.py
    heuretes_freecad/project_pages.py
    heuretes_freecad/vault_trees.py
    heuretes_freecad/vault_storage.py
    heuretes_freecad/vault_simulations.py
    heuretes_freecad/product_tree.py
    heuretes_freecad/project_setup.py
    heuretes_freecad/hardware.py
    heuretes_freecad/routing.py
    heuretes_freecad/balance.py
    heuretes_freecad/rope.py
    heuretes_freecad/fittings.py
    heuretes_freecad/stock.py
    heuretes_freecad/connector_library.py
    bruceworx/__init__.py
    bruceworx/__main__.py
    config/document_types.json
    config/numbering.example.json
    config/part_properties.json
    config/hole_presets.json
    macros/PartNumber.FCMacro
)

ADD_CUSTOM_TARGET(BW_BruceWorxHooks ALL
    SOURCES ${BruceWorxHooks_SRCS} hooks.py
)

set(BW_HOOKS_BUILD_DIR "${CMAKE_BINARY_DIR}/Mod/HeuretesHooks")
fc_copy_sources(BW_BruceWorxHooks "${BW_HOOKS_BUILD_DIR}" ${BruceWorxHooks_SRCS})

# hooks.py -> cli/hooks.py
add_custom_command(
    OUTPUT "${BW_HOOKS_BUILD_DIR}/cli/hooks.py"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${BW_HOOKS_BUILD_DIR}/cli"
    COMMAND "${CMAKE_COMMAND}" -E copy "${CMAKE_CURRENT_SOURCE_DIR}/hooks.py" "${BW_HOOKS_BUILD_DIR}/cli/hooks.py"
    MAIN_DEPENDENCY "${CMAKE_CURRENT_SOURCE_DIR}/hooks.py"
    COMMENT "Copying hooks.py to ${BW_HOOKS_BUILD_DIR}/cli/hooks.py"
)
add_custom_target(BW_BruceWorxHooks_cli ALL DEPENDS "${BW_HOOKS_BUILD_DIR}/cli/hooks.py")

INSTALL(FILES Init.py InitGui.py LICENSE README.md DESTINATION Mod/HeuretesHooks)
INSTALL(
    FILES
        heuretes_freecad/__init__.py
        heuretes_freecad/env.py
        heuretes_freecad/__main__.py
        heuretes_freecad/cli.py
        heuretes_freecad/config_path.py
        heuretes_freecad/freecad_io.py
        heuretes_freecad/gui.py
        heuretes_freecad/macro.py
        heuretes_freecad/numbering.py
        heuretes_freecad/partprops.py
        heuretes_freecad/pdm.py
        heuretes_freecad/pdm_folder.py
        heuretes_freecad/release_check.py
        heuretes_freecad/sim_reports.py
        heuretes_freecad/sources.py
        heuretes_freecad/step_export.py
        heuretes_freecad/tree.py
        heuretes_freecad/vault_setup.py
        heuretes_freecad/drawing_standards.py
    heuretes_freecad/pdm_workspace.py
    heuretes_freecad/vault_move.py
    heuretes_freecad/renumber.py
        heuretes_freecad/kicad_bridge.py
        heuretes_freecad/ecad_vault.py
        heuretes_freecad/sheetmetal_rules.py
        heuretes_freecad/flat_dxf.py
        heuretes_freecad/flat_export.py
        heuretes_freecad/bom.py
        heuretes_freecad/vault_dashboard.py
        heuretes_freecad/project_pages.py
        heuretes_freecad/vault_trees.py
        heuretes_freecad/vault_storage.py
        heuretes_freecad/vault_simulations.py
        heuretes_freecad/product_tree.py
        heuretes_freecad/project_setup.py
        heuretes_freecad/hardware.py
        heuretes_freecad/routing.py
        heuretes_freecad/balance.py
        heuretes_freecad/rope.py
        heuretes_freecad/fittings.py
        heuretes_freecad/stock.py
        heuretes_freecad/connector_library.py
    DESTINATION Mod/HeuretesHooks/heuretes_freecad
)
INSTALL(
    FILES config/document_types.json config/numbering.example.json config/part_properties.json
          config/hole_presets.json
    DESTINATION Mod/HeuretesHooks/config
)
INSTALL(FILES macros/PartNumber.FCMacro DESTINATION Mod/HeuretesHooks/macros)
INSTALL(FILES hooks.py DESTINATION Mod/HeuretesHooks/cli)

# Old import name. heuretes_freecad is the package; this shim keeps `import bruceworx` working.
INSTALL(FILES bruceworx/__init__.py bruceworx/__main__.py DESTINATION Mod/HeuretesHooks/bruceworx)
