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
    TestBruceWorxGroups.py
    TestBruceWorxWorkspaces.py
    TestBruceWorxSearch.py
    TestBruceWorxStart.py
    TestBruceWorxTitleBar.py
    TestBruceWorxVaultWizard.py
    preferences-bruceworx.ui
    preferences-bruceworx.svg
    BruceWorxModelTree.qss
    bruceworx_pack.py
    bruceworx_defaults.py
    bruceworx_menu.py
    ground_grid.py
    bruceworx_pattern.py
    bruceworx_pattern_gui.py
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
    bruceworx_vaultwizard.py
    bruceworx_browser.py
    TestBruceWorxBrowser.py
    bruceworx_navpill.py
    BruceWorx_RectangularPattern.svg
    BruceWorx_CircularPattern.svg
)

ADD_CUSTOM_TARGET(BruceWorx ALL
    SOURCES ${BruceWorx_SRCS}
)

fc_copy_sources(BruceWorx "${CMAKE_BINARY_DIR}/Mod/BruceWorx" ${BruceWorx_SRCS})

INSTALL(
    FILES ${BruceWorx_SRCS}
    DESTINATION Mod/BruceWorx
)
