# SPDX-License-Identifier: LGPL-2.1-or-later
# [BW] History Timeline add-on (Python only). Installed with the normal build:
# copied into <build>/Mod/HistoryTimeline (what build/<preset>/bin/FreeCAD
# loads) and installed to Mod/HistoryTimeline. Turn the build step off with
# -DBUILD_BW_HISTORY_TIMELINE=OFF; turn the dock off at runtime on
# Preferences -> BruceWorx.

SET(HistoryTimeline_SRCS
    Init.py
    InitGui.py
    README.md
    history_timeline/__init__.py
    history_timeline/assembly.py
    history_timeline/bar.py
    history_timeline/dock.py
    history_timeline/model.py
    history_timeline/qtcompat.py
    history_timeline/icons/timeline_start-light.svg
    history_timeline/icons/timeline_back-light.svg
    history_timeline/icons/timeline_forward-light.svg
    history_timeline/icons/timeline_end-light.svg
    history_timeline/icons/timeline_start-dark.svg
    history_timeline/icons/timeline_back-dark.svg
    history_timeline/icons/timeline_forward-dark.svg
    history_timeline/icons/timeline_end-dark.svg
)

ADD_CUSTOM_TARGET(BW_HistoryTimeline ALL
    SOURCES ${HistoryTimeline_SRCS}
)

fc_copy_sources(BW_HistoryTimeline "${CMAKE_BINARY_DIR}/Mod/HistoryTimeline" ${HistoryTimeline_SRCS})

INSTALL(
    FILES Init.py InitGui.py README.md
    DESTINATION Mod/HistoryTimeline
)
INSTALL(
    FILES
        history_timeline/__init__.py
        history_timeline/assembly.py
        history_timeline/bar.py
        history_timeline/dock.py
        history_timeline/model.py
        history_timeline/qtcompat.py
    DESTINATION Mod/HistoryTimeline/history_timeline
)
INSTALL(
    FILES
        history_timeline/icons/timeline_start-light.svg
        history_timeline/icons/timeline_back-light.svg
        history_timeline/icons/timeline_forward-light.svg
        history_timeline/icons/timeline_end-light.svg
        history_timeline/icons/timeline_start-dark.svg
        history_timeline/icons/timeline_back-dark.svg
        history_timeline/icons/timeline_forward-dark.svg
        history_timeline/icons/timeline_end-dark.svg
    DESTINATION Mod/HistoryTimeline/history_timeline/icons
)
