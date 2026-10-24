# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] Vendored add-ons (MOD-06). SheetMetal is copied into <build>/Mod/SheetMetal
# (what build/<preset>/bin/FreeCAD loads) and installed to Mod/SheetMetal, with
# its package.xml (it names the workbench). The pinned commit and our patches are
# in VENDOR.txt and patches/.
#
# Build switch: -DBUILD_BW_SHEETMETAL=OFF. Runtime switch: Preferences ->
# BruceWorx -> Sheet metal add-on (adds SheetMetal to DisabledAddons).

file(GLOB_RECURSE BW_SheetMetal_SRCS
    RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/SheetMetal/*"
)
list(FILTER BW_SheetMetal_SRCS EXCLUDE REGEX "/__pycache__/|\\.pyc$|/\\.DS_Store$")

ADD_CUSTOM_TARGET(BW_SheetMetal ALL
    SOURCES ${BW_SheetMetal_SRCS}
)

# The build tree also gets the add-on's own tests (FreeCADCmd -t TestSheetMetal).
fc_copy_sources(BW_SheetMetal "${CMAKE_BINARY_DIR}/Mod" ${BW_SheetMetal_SRCS})

INSTALL(
    DIRECTORY SheetMetal/
    DESTINATION Mod/SheetMetal
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
    PATTERN ".DS_Store" EXCLUDE
    PATTERN ".gitignore" EXCLUDE
    PATTERN ".gitattributes" EXCLUDE
    PATTERN "SMTests" EXCLUDE
    PATTERN "TestSheetMetal.py" EXCLUDE
    # translation tools (the .qm files stay)
    REGEX "/Resources/translations/[^/]*\\.(py|ts|md)$" EXCLUDE
)

INSTALL(
    FILES SheetMetal/LICENSE
    DESTINATION share/doc/BruceWorx/third-party
    RENAME SheetMetal-LICENSE.txt
)
INSTALL(
    FILES third-party/networkx-LICENSE.txt
    DESTINATION share/doc/BruceWorx/third-party
)
