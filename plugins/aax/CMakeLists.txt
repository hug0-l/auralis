# ============================================================================
#  plugins/aax/CMakeLists.txt
#
#  Builds the Auralis AAX adapter against the AAX SDK that is in the tree.
#
#  THE SDK
#  -------
#  The import library of the SDK is not in the tree, so the SDK's own
#  sources are compiled into the plug-in - which is what the framework's
#  own client does as well.  The sources are compiled once into a static
#  library and the plug-in links it, so the code ends up inside the binary
#  exactly as it would if every source were compiled into it directly.
#
#  THE TEMPLATE
#  ------------
#  AuralisAAX.cpp implements any Auralis module.  It contains no module
#  name, no parameter name and no preset: the module is chosen by the
#  identity record that the build stamps into the binary, and the page
#  tables come from the generated description, which comes from the
#  canonical parameter table.
#
#  THE PROBE
#  ---------
#  There is no AAX host on a build machine, so the build also produces a
#  probe: a console program that builds the same data model the plug-in
#  builds and reads it back through the format's own interfaces.
#
#  Example
#  -------
#    cmake -S plugins/aax -B build/aax -G Ninja -DCMAKE_BUILD_TYPE=Release
#    cmake --build build/aax --parallel 2
# ============================================================================

cmake_minimum_required(VERSION 3.22)

project(AuralisAAX LANGUAGES C CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if (MSVC)
    add_compile_options(/W3 /wd4996 /wd4127 /wd4512 /wd4244 /wd4267)
endif()

# ---------------------------------------------------------------------------
#  options
# ---------------------------------------------------------------------------

set(AURALIS_AAX_SDK_PATH "" CACHE PATH
    "Path to the AAX SDK (the directory that holds Interfaces/ and Libs/)")

set(AURALIS_BUILD_PROBE "Build the verification probe" ON)

# ---------------------------------------------------------------------------
#  the layout of this repository
# ---------------------------------------------------------------------------

get_filename_component(AURALIS_PLUGIN_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)
get_filename_component(AURALIS_PLUGINS_DIR "${AURALIS_PLUGIN_DIR}/.." ABSOLUTE)
get_filename_component(AURALIS_REPO_ROOT "${AURALIS_PLUGINS_DIR}/.." ABSOLUTE)

if (NOT AURALIS_AAX_SDK_PATH)
    set(AURALIS_AAX_SDK_PATH
        "${AURALIS_REPO_ROOT}/vendor/JUCE8/modules/juce_audio_plugin_client/AAX/SDK")
endif()

if (NOT EXISTS "${AURALIS_AAX_SDK_PATH}/Interfaces/AAX.h")
    message(FATAL_ERROR
        "The AAX SDK was not found: ${AURALIS_AAX_SDK_PATH}\n"
        "Point AURALIS_AAX_SDK_PATH at a copy of the SDK (the directory that "
        "holds Interfaces/ and Libs/).")
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# ---------------------------------------------------------------------------
#  the generated description
# ---------------------------------------------------------------------------

set(AURALIS_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(AURALIS_GENERATED_HEADER "${AURALIS_GENERATED_DIR}/AuralisAAXGenerated.h")

add_custom_command(
    OUTPUT "${AURALIS_GENERATED_HEADER}"
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_REPO_ROOT}/scripts/build_aax.py"
            generate --out "${AURALIS_GENERATED_HEADER}"
    WORKING_DIRECTORY "${AURALIS_REPO_ROOT}"
    DEPENDS "${AURALIS_REPO_ROOT}/scripts/build_aax.py"
            "${AURALIS_REPO_ROOT}/docs/aax_page_tables.json"
    COMMENT "Generating AuralisAAXGenerated.h from the generated page tables"
    VERBATIM)

add_custom_target(auralis_aax_generated DEPENDS "${AURALIS_GENERATED_HEADER}")

# ---------------------------------------------------------------------------
#  the SDK
# ---------------------------------------------------------------------------

file(GLOB AURALIS_AAX_SDK_SOURCES CONFIGURE_DEPENDS
     "${AURALIS_AAX_SDK_PATH}/Libs/AAXLibrary/source/*.cpp")

list(APPEND AURALIS_AAX_SDK_SOURCES
     "${AURALIS_AAX_SDK_PATH}/Interfaces/ACF/CACFClassFactory.cpp")

if (NOT WIN32)
    # The Windows autorelease pool has no counterpart on the other platforms.
    list(REMOVE_ITEM AURALIS_AAX_SDK_SOURCES
         "${AURALIS_AAX_SDK_PATH}/Libs/AAXLibrary/source/AAX_CAutoreleasePool.Win.cpp")
endif()

add_library(aax_sdk STATIC ${AURALIS_AAX_SDK_SOURCES})

target_include_directories(aax_sdk PUBLIC
    "${AURALIS_AAX_SDK_PATH}/Interfaces"
    "${AURALIS_AAX_SDK_PATH}/Interfaces/ACF")

if (MSVC)
    target_compile_definitions(aax_sdk PUBLIC _CRT_SECURE_NO_WARNINGS)
    target_compile_options(aax_sdk PRIVATE /wd4996 /wd4127 /wd4512 /wd4244 /wd4267)
else()
    target_compile_options(aax_sdk PRIVATE -Wno-unused-parameter)
endif()

# ---------------------------------------------------------------------------
#  the adapter
# ---------------------------------------------------------------------------

add_library(auralis_aax_adapter STATIC AuralisAAX.cpp AuralisAAX.h)

target_include_directories(auralis_aax_adapter PUBLIC
    "${AURALIS_PLUGIN_DIR}"
    "${AURALIS_GENERATED_DIR}"
    "${AURALIS_PLUGINS_DIR}/vst3")

add_dependencies(auralis_aax_adapter auralis_aax_generated)

target_link_libraries(auralis_aax_adapter PUBLIC aax_sdk)

# ---------------------------------------------------------------------------
#  the plug-in
# ---------------------------------------------------------------------------
#
#  The exported entry points of the format live in the SDK's
#  AAX_Exports.cpp; the file is compiled into the plug-in and nowhere else.

add_library(AuralisAAX SHARED "${AURALIS_AAX_SDK_PATH}/Interfaces/AAX_Exports.cpp")

target_link_libraries(AuralisAAX PRIVATE auralis_aax_adapter)

set_target_properties(AuralisAAX PROPERTIES
    OUTPUT_NAME "AuralisAAX"
    PREFIX ""
    CXX_VISIBILITY_PRESET default)

# ---------------------------------------------------------------------------
#  the probe
# ---------------------------------------------------------------------------

if (AURALIS_BUILD_PROBE)
    add_executable(aaxprobe AuralisAAXProbe.cpp)

    target_link_libraries(aaxprobe PRIVATE auralis_aax_adapter)

    set_target_properties(aaxprobe PROPERTIES OUTPUT_NAME "aaxprobe")
endif()
