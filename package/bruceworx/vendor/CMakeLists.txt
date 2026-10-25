# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] Vendored add-ons (MOD-06, MOD-05), each copied into <build>/Mod/<folder>
# (what build/<preset>/bin/FreeCAD loads) and installed to Mod/<folder>, with its
# package.xml (it names the workbench). Versions, commits and our patches are in
# VENDOR.txt and patches/.
#
# SheetMetal: -DBUILD_BW_SHEETMETAL=OFF. Runtime switch: Preferences -> BruceWorx ->
# Sheet metal add-on (adds SheetMetal to DisabledAddons).
# Fasteners (folder "fasteners", the Addon Manager's name): -DBUILD_BW_FASTENERS=OFF.
# Runtime switch: Preferences -> BruceWorx -> Fasteners add-on (adds fasteners to
# DisabledAddons).

if(BUILD_BW_SHEETMETAL)
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
endif()

if(BUILD_BW_FASTENERS)
    file(GLOB_RECURSE BW_Fasteners_SRCS
        RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
        CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/fasteners/*"
    )
    list(FILTER BW_Fasteners_SRCS EXCLUDE REGEX "/__pycache__/|\\.pyc$|/\\.DS_Store$")

    ADD_CUSTOM_TARGET(BW_Fasteners ALL
        SOURCES ${BW_Fasteners_SRCS}
    )
    fc_copy_sources(BW_Fasteners "${CMAKE_BINARY_DIR}/Mod" ${BW_Fasteners_SRCS})

    INSTALL(
        DIRECTORY fasteners/
        DESTINATION Mod/fasteners
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.pyc" EXCLUDE
        PATTERN ".DS_Store" EXCLUDE
        PATTERN "tests" EXCLUDE
        PATTERN "CONTRIBUTING.md" EXCLUDE
    )
    INSTALL(
        FILES fasteners/LICENSE
        DESTINATION share/doc/BruceWorx/third-party
        RENAME Fasteners-LICENSE.txt
    )
endif()
