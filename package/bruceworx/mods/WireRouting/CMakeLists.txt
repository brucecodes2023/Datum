# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] Wire routing add-on (Python only, roadmap track WIR). Installed with
# the normal build: copied into <build>/Mod/WireRouting (what
# build/<preset>/bin/FreeCAD loads) and installed to Mod/WireRouting. Turn
# the build step off with -DBUILD_BW_WIRE_ROUTING=OFF; turn the feature off
# at runtime on Preferences -> BruceWorx.

SET(WireRouting_SRCS
    Init.py
    InitGui.py
    README.md
    wire_routing/__init__.py
    wire_routing/cutlist.py
    wire_routing/geometry.py
    wire_routing/gui.py
    wire_routing/objects.py
)

ADD_CUSTOM_TARGET(BW_WireRouting ALL
    SOURCES ${WireRouting_SRCS}
)

fc_copy_sources(BW_WireRouting "${CMAKE_BINARY_DIR}/Mod/WireRouting" ${WireRouting_SRCS})

INSTALL(
    FILES Init.py InitGui.py README.md
    DESTINATION Mod/WireRouting
)
INSTALL(
    FILES
        wire_routing/__init__.py
        wire_routing/cutlist.py
        wire_routing/geometry.py
        wire_routing/gui.py
        wire_routing/objects.py
    DESTINATION Mod/WireRouting/wire_routing
)
