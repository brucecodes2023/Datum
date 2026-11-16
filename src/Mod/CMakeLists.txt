if(BUILD_ADDONMGR)
  if( NOT EXISTS "${CMAKE_SOURCE_DIR}/src/Mod/AddonManager/CMakeLists.txt" )
    message(FATAL_ERROR "The Addon Manager has been moved into a git submodule. Please run
    git submodule update --init" )
  endif()
  add_subdirectory(AddonManager)
endif(BUILD_ADDONMGR)

if(BUILD_BIM)
  add_subdirectory(BIM)
endif(BUILD_BIM)

if(BUILD_ASSEMBLY)
  add_subdirectory(Assembly)
endif(BUILD_ASSEMBLY)

if(BUILD_CLOUD)
    add_subdirectory(Cloud)
endif(BUILD_CLOUD)

if(BUILD_DRAFT)
  add_subdirectory(Draft)
endif(BUILD_DRAFT)

if(BUILD_FEM)
    add_subdirectory(Fem)
endif(BUILD_FEM)

if(BUILD_HELP)
  add_subdirectory(Help)
endif(BUILD_HELP)

if(BUILD_IDF)
  add_subdirectory(Idf)
endif(BUILD_IDF)

if(BUILD_IMPORT)
  add_subdirectory(Import)
endif(BUILD_IMPORT)

if(BUILD_INSPECTION)
  add_subdirectory(Inspection)
endif(BUILD_INSPECTION)

if(BUILD_JTREADER)
  add_subdirectory(JtReader)
endif(BUILD_JTREADER)

if(BUILD_MATERIAL)
  add_subdirectory(Material)
endif(BUILD_MATERIAL)

if(BUILD_MESH)
  add_subdirectory(Mesh)
endif(BUILD_MESH)

if(BUILD_MESH_PART)
  add_subdirectory(MeshPart)
endif(BUILD_MESH_PART)

if(BUILD_OPENSCAD)
  add_subdirectory(OpenSCAD)
endif(BUILD_OPENSCAD)

if(BUILD_PART)
  add_subdirectory(Part)
endif(BUILD_PART)

if(BUILD_PART_DESIGN)
  add_subdirectory(PartDesign)
endif(BUILD_PART_DESIGN)

if(BUILD_CAM)
    add_subdirectory(CAM)
endif(BUILD_CAM)

if(BUILD_PLOT)
  add_subdirectory(Plot)
endif(BUILD_PLOT)

if(BUILD_POINTS)
  add_subdirectory(Points)
endif(BUILD_POINTS)

if(BUILD_REVERSEENGINEERING)
  add_subdirectory(ReverseEngineering)
endif(BUILD_REVERSEENGINEERING)

if(BUILD_ROBOT)
    if(EIGEN3_FOUND)
      add_subdirectory(Robot)
    else(EIGEN3_FOUND)
      MESSAGE("Due to the missing Eigen3 library the Robot module won't be built")
    endif(EIGEN3_FOUND)
endif(BUILD_ROBOT)

if(BUILD_SANDBOX)
    add_subdirectory(Sandbox)
endif(BUILD_SANDBOX)

if(BUILD_SHOW)
    add_subdirectory(Show)
endif(BUILD_SHOW)

if(BUILD_SKETCHER)
    if(EIGEN3_FOUND)
      add_subdirectory(Sketcher)
    else(EIGEN3_FOUND)
      MESSAGE("Due to the missing Eigen3 library the Sketcher module won't be built")
    endif(EIGEN3_FOUND)
endif(BUILD_SKETCHER)

if(BUILD_SPREADSHEET)
  add_subdirectory(Spreadsheet)
endif(BUILD_SPREADSHEET)

if(BUILD_START)
    add_subdirectory(Start)
endif(BUILD_START)

if(BUILD_SURFACE)
  add_subdirectory(Surface)
endif(BUILD_SURFACE)

if(BUILD_MEASURE)
    add_subdirectory(Measure)
endif(BUILD_MEASURE)

if(BUILD_TECHDRAW)
    add_subdirectory(TechDraw)
endif(BUILD_TECHDRAW)

#add_subdirectory(TemplatePyMod)

if(BUILD_TEST)
  add_subdirectory(Test)
endif(BUILD_TEST)

if(BUILD_TUX)
    add_subdirectory(Tux)
endif(BUILD_TUX)

if(BUILD_WEB)
    add_subdirectory(Web)
endif(BUILD_WEB)

# [BW] Python only: preference page, overlay model-tree config,
# preference-pack helper and ground grid. No C++.
add_subdirectory(BruceWorx)

# [BW] BruceWorx rule: everything ships on by default, with an off switch on
# Preferences -> BruceWorx. These two Python add-ons live outside src/Mod and
# are copied into <build>/Mod (used by build/<preset>/bin/FreeCAD) and
# installed to Mod/ by the normal build.
# -DBUILD_HEU_* is the switch (default ON). -DBUILD_BW_* still works when HEU was not passed.
foreach(_heu HISTORY_TIMELINE HOOKS SHEETMETAL FASTENERS)
    if(DEFINED BUILD_BW_${_heu} AND NOT DEFINED BUILD_HEU_${_heu})
        set(BUILD_HEU_${_heu} "${BUILD_BW_${_heu}}" CACHE BOOL "legacy BUILD_BW_${_heu}" FORCE)
    endif()
endforeach()
option(BUILD_HEU_HISTORY_TIMELINE "Heuretes FreeCAD: install the History Timeline add-on (Mod/HistoryTimeline)" ON)
option(BUILD_HEU_HOOKS "Heuretes FreeCAD: install the hooks / part-number add-on (Mod/HeuretesHooks)" ON)
# MOD-06 (resumed 25 Oct 2026; networkx is in pixi.toml for its new unfolder).
option(BUILD_HEU_SHEETMETAL "Heuretes FreeCAD: install the vendored SheetMetal add-on (Mod/SheetMetal, MOD-06)" ON)
# MOD-05: the Fasteners add-on, folder "fasteners" (the Addon Manager's name, so a user copy wins).
option(BUILD_HEU_FASTENERS "Heuretes FreeCAD: install the vendored Fasteners add-on (Mod/fasteners, MOD-05)" ON)
if(BUILD_HEU_HISTORY_TIMELINE)
    add_subdirectory(
        ${CMAKE_SOURCE_DIR}/package/bruceworx/mods/HistoryTimeline
        ${CMAKE_BINARY_DIR}/bruceworx/HistoryTimeline
    )
endif()
if(BUILD_HEU_HOOKS)
    add_subdirectory(
        ${CMAKE_SOURCE_DIR}/addons/Heuretes
        ${CMAKE_BINARY_DIR}/bruceworx/BruceWorxHooks
    )
endif()
if(BUILD_HEU_SHEETMETAL OR BUILD_HEU_FASTENERS)
    add_subdirectory(
        ${CMAKE_SOURCE_DIR}/package/bruceworx/vendor
        ${CMAKE_BINARY_DIR}/bruceworx/vendor
    )
endif()

# [BW] Vector Labs splash screen (24 Oct 2026): App::Application reads <home>/bin/branding.xml at
# start; the images go to <home>/icons, which BitmapFactory searches (the _2x one on Retina).
if(DEFINED BUILD_BW_BRANDING AND NOT DEFINED BUILD_HEU_BRANDING)
    set(BUILD_HEU_BRANDING "${BUILD_BW_BRANDING}" CACHE BOOL "legacy BUILD_BW_BRANDING" FORCE)
endif()
option(BUILD_HEU_BRANDING "Heuretes FreeCAD splash (bin/branding.xml, icons/heuretes_splash*.png)" ON)
if(BUILD_HEU_BRANDING)
    set(HEU_BRANDING_DIR ${CMAKE_SOURCE_DIR}/package/bruceworx/branding)
    configure_file(${HEU_BRANDING_DIR}/branding.xml ${CMAKE_BINARY_DIR}/bin/branding.xml COPYONLY)
    foreach(HEU_SPLASH heuretes_splash.png heuretes_splash_2x.png)
        configure_file(${HEU_BRANDING_DIR}/${HEU_SPLASH} ${CMAKE_BINARY_DIR}/icons/${HEU_SPLASH} COPYONLY)
    endforeach()
    install(FILES ${HEU_BRANDING_DIR}/branding.xml DESTINATION bin)
    install(
        FILES ${HEU_BRANDING_DIR}/heuretes_splash.png ${HEU_BRANDING_DIR}/heuretes_splash_2x.png
        DESTINATION icons
    )
endif()
