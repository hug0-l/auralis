# ============================================================================
#  plugins/vst3/CMakeLists.txt
#
#  Builds one VST3 plug-in per Auralis module out of ONE adapter template.
#
#  THE TEMPLATE
#  ------------
#  AuralisPlugin.cpp / AuralisPlugin.h implement any Auralis module.  They
#  contain no module name, no parameter name and no preset: the module is
#  chosen by the identity record that the build stamps into the binary.
#
#  WHAT VARIES PER MODULE
#  ----------------------
#  Two things, both taken from the generated identity record, never written
#  by hand:
#
#    1. The VST3 class id and class name.  The plug-in wrapper bakes both
#       into the binary at compile time (from JucePlugin_PluginCode and
#       JucePlugin_Name), and the VST3 format requires every plug-in to
#       have its own.  The four-character plugin code is the module's
#       product code, the same one the AAX format uses, and the class id
#       is derived from it exactly the way the wrapper derives it - so the
#       value in the identity record is the class id the binary reports.
#
#    2. The identity record itself, stamped into the binary after it has
#       been linked.  It tells the adapter which module to load, how to
#       map host parameter ids to core parameter names, and which
#       identifiers the plug-in stands for.
#
#  Adding a module to the registry therefore needs no change here: the loop
#  below picks it up from the registry.
#
#  WHAT THIS BUILD NEEDS
#  ---------------------
#  * a JUCE checkout (7.0.9 or newer), and
#  * a library that implements the C ABI in plugins/vst3/aurcore_c_api.h.
#    The DSP core is a separate deliverable; this build only knows its
#    interface.
#
#  Example
#  -------
#    cmake -S plugins/vst3 -B build/vst3 \
#          -DAURALIS_JUCE_PATH=<path to a JUCE checkout> \
#          -DAURALIS_CORE_LIBRARY=<path to the core library>
#    cmake --build build/vst3 --config Release
# ============================================================================

cmake_minimum_required(VERSION 3.22)

project(AuralisVST3 LANGUAGES C CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---------------------------------------------------------------------------
#  options
# ---------------------------------------------------------------------------

set(AURALIS_JUCE_PATH "" CACHE PATH "Path to a JUCE checkout (7.0.9 or newer)")
set(AURALIS_VST3_SDK_PATH "" CACHE PATH
    "Path to the VST3 SDK (the directory that holds pluginterfaces/, base/ and public.sdk/)")
set(AURALIS_CORE_LIBRARY "" CACHE FILEPATH "Library implementing aurcore_c_api.h")
set(AURALIS_CORE_INCLUDE_DIR "" CACHE PATH "Extra include directory for the core's headers")

option(AURALIS_BUILD_VST3 "Build the VST3 format" ON)
option(AURALIS_BUILD_AU "Build the Audio Unit format" OFF)
option(AURALIS_BUILD_STANDALONE "Build the Standalone format" OFF)
option(AURALIS_BUILD_AAX "Build the AAX format (needs the Avid SDK)" OFF)
option(AURALIS_STAMP_IDENTITIES "Stamp the identity record into every plug-in" ON)

set(AURALIS_MACOS_CODESIGN_IDENTITY "" CACHE STRING
    "Codesign identity used to re-sign the plug-ins after they are stamped")

set(AURALIS_AAX_SDK_PATH "" CACHE PATH "Path to the Avid AAX SDK")

# ---------------------------------------------------------------------------
#  the layout of this repository
# ---------------------------------------------------------------------------

get_filename_component(AURALIS_PLUGIN_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)
get_filename_component(AURALIS_PLUGINS_DIR "${AURALIS_PLUGIN_DIR}/.." ABSOLUTE)
get_filename_component(AURALIS_REPO_ROOT "${AURALIS_PLUGINS_DIR}/.." ABSOLUTE)

set(AURALIS_IDENTITY_TOOL "${AURALIS_PLUGIN_DIR}/build_identity.py")
set(AURALIS_IDENTITY_MODULE "${AURALIS_PLUGINS_DIR}/identity.py")
set(AURALIS_STAGE_TOOL "${AURALIS_PLUGIN_DIR}/stage_runtime.py")
set(AURALIS_MANIFEST_TOOL "${AURALIS_PLUGIN_DIR}/make_manifests.py")

# The runtime that travels with the plug-in: the core ABI library and the
# interpreter it is linked against.
set(AURALIS_RUNTIME_DIR "${AURALIS_REPO_ROOT}/plugins/abi/build"
    CACHE PATH "Directory holding the core ABI library and the interpreter")

# The core package, staged into the bundle so the plug-in does not need the
# source tree.
set(AURALIS_CORE_PACKAGE_DIR "${AURALIS_REPO_ROOT}/aurcore"
    CACHE PATH "The core package directory (aurcore)")

# ---------------------------------------------------------------------------
#  tools
# ---------------------------------------------------------------------------

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# ---------------------------------------------------------------------------
#  JUCE
# ---------------------------------------------------------------------------

if (TARGET juce::juce_audio_processors)
    # JUCE was already added by an enclosing project.
elseif (AURALIS_JUCE_PATH)
    if (NOT EXISTS "${AURALIS_JUCE_PATH}/CMakeLists.txt")
        message(FATAL_ERROR
            "AURALIS_JUCE_PATH does not look like a JUCE checkout: ${AURALIS_JUCE_PATH}")
    endif()
    add_subdirectory("${AURALIS_JUCE_PATH}" "${CMAKE_BINARY_DIR}/JUCE")
else()
    message(FATAL_ERROR
        "JUCE was not found.  Point AURALIS_JUCE_PATH at a JUCE checkout, for example\n"
        "    -DAURALIS_JUCE_PATH=<path to a JUCE checkout>")
endif()

# The manifest helper: JUCE builds it only when it writes the manifest
# itself, and the manifest is written by a pass over the finished bundles,
# so the tool is requested here.
if (COMMAND _juce_add_vst3_manifest_helper_target)
    _juce_add_vst3_manifest_helper_target()
endif()

# The Visual C++ tool chain keeps the "old style" library creation rules
# around for module libraries, and that path links through
# "cmake -E vs_link_dll", which fails on a current tool set.  The modern
# rule does the same work with the linker directly, so the old one is
# switched off here.  Both variables have to be cleared: the module one
# otherwise keeps the value it was given for another platform.
set(CMAKE_CXX_SHARED_LIBRARY_CREATE_CXX_FLAGS "")
set(CMAKE_CXX_SHARED_MODULE_CREATE_CXX_FLAGS "")
set(CMAKE_SHARED_MODULE_CREATE_CXX_FLAGS "")
set(CMAKE_SHARED_LIBRARY_CREATE_CXX_FLAGS "")
message(STATUS "AURALIS create flags: lib='${CMAKE_SHARED_LIBRARY_CREATE_CXX_FLAGS}' "
               "module='${CMAKE_SHARED_MODULE_CREATE_CXX_FLAGS}' "
               "cxx-lib='${CMAKE_CXX_SHARED_LIBRARY_CREATE_CXX_FLAGS}' "
               "cxx-module='${CMAKE_CXX_SHARED_MODULE_CREATE_CXX_FLAGS}'")

# The VST3 SDK is not part of JUCE: point the build at a copy.  The path
# must be the directory that holds pluginterfaces/, base/ and public.sdk/,
# because the wrapper includes them as <pluginterfaces/...>, <base/...> and
# <public.sdk/...>.
if (AURALIS_VST3_SDK_PATH AND NOT TARGET juce_vst3_sdk)
    if (NOT EXISTS "${AURALIS_VST3_SDK_PATH}/pluginterfaces")
        message(FATAL_ERROR
            "AURALIS_VST3_SDK_PATH does not look like a VST3 SDK: ${AURALIS_VST3_SDK_PATH}")
    endif()
    juce_set_vst3_sdk_path("${AURALIS_VST3_SDK_PATH}")
endif()

# The AAX format needs the Avid SDK, which is licensed separately and is
# therefore not part of this repository.
if (AURALIS_BUILD_AAX AND AURALIS_AAX_SDK_PATH)
    juce_set_aax_sdk_path("${AURALIS_AAX_SDK_PATH}")
endif()

# ---------------------------------------------------------------------------
#  the core
# ---------------------------------------------------------------------------

if (NOT AURALIS_CORE_LIBRARY)
    message(FATAL_ERROR
        "AURALIS_CORE_LIBRARY is not set.  It must point at a library that\n"
        "implements the C ABI declared in plugins/vst3/aurcore_c_api.h:\n"
        "    aur_engine_create, aur_engine_add_module, aur_engine_set_param,\n"
        "    aur_engine_process and aur_engine_destroy.\n"
        "The adapter layer is written against that interface; the implementation\n"
        "of the interface lives with the DSP core.")
endif()

if (NOT EXISTS "${AURALIS_CORE_LIBRARY}")
    message(FATAL_ERROR "AURALIS_CORE_LIBRARY does not exist: ${AURALIS_CORE_LIBRARY}")
endif()

# ---------------------------------------------------------------------------
#  the generated parameter table
# ---------------------------------------------------------------------------

set(AURALIS_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(AURALIS_GENERATED_HEADER "${AURALIS_GENERATED_DIR}/AuralisGenerated.h")

file(GLOB AURALIS_REGISTRY_SOURCES CONFIGURE_DEPENDS
     "${AURALIS_REPO_ROOT}/aurcore/*.py"
     "${AURALIS_REPO_ROOT}/aurcore/schema/*.py"
     "${AURALIS_REPO_ROOT}/aurcore/schema/*.tsv")

add_custom_command(
    OUTPUT "${AURALIS_GENERATED_HEADER}"
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" header
            --out "${AURALIS_GENERATED_HEADER}"
    WORKING_DIRECTORY "${AURALIS_REPO_ROOT}"
    DEPENDS "${AURALIS_IDENTITY_TOOL}"
            "${AURALIS_IDENTITY_MODULE}"
            ${AURALIS_REGISTRY_SOURCES}
    COMMENT "Generating AuralisGenerated.h from the canonical parameter table"
    VERBATIM)

add_custom_target(auralis_generated_header DEPENDS "${AURALIS_GENERATED_HEADER}")

# ---------------------------------------------------------------------------
#  the module list
# ---------------------------------------------------------------------------

# The module list comes from the registry, so a new module needs no change
# to this file.  It is written to a file and included, because a module
# display name may contain spaces.
set(AURALIS_MODULE_TABLE "${CMAKE_CURRENT_BINARY_DIR}/auralis_modules.cmake")

execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" list --format cmake
    WORKING_DIRECTORY "${AURALIS_REPO_ROOT}"
    OUTPUT_VARIABLE AURALIS_MODULE_TABLE_TEXT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE AURALIS_MODULE_TABLE_RESULT)

if (NOT AURALIS_MODULE_TABLE_RESULT EQUAL 0)
    message(FATAL_ERROR "Could not read the module registry: ${AURALIS_MODULE_TABLE_RESULT}")
endif()

file(WRITE "${AURALIS_MODULE_TABLE}" "${AURALIS_MODULE_TABLE_TEXT}\n")
include("${AURALIS_MODULE_TABLE}")

list(LENGTH AURALIS_MODULE_CODES AURALIS_MODULE_COUNT)

if (AURALIS_MODULE_COUNT EQUAL 0)
    message(FATAL_ERROR "The registry is empty, so there is nothing to build.")
endif()

# ---------------------------------------------------------------------------
#  the formats
# ---------------------------------------------------------------------------

set(AURALIS_FORMATS "")

if (AURALIS_BUILD_VST3)
    list(APPEND AURALIS_FORMATS VST3)
endif()

if (AURALIS_BUILD_AU)
    list(APPEND AURALIS_FORMATS AU)
endif()

if (AURALIS_BUILD_STANDALONE)
    list(APPEND AURALIS_FORMATS Standalone)
endif()

if (AURALIS_BUILD_AAX)
    list(APPEND AURALIS_FORMATS AAX)
endif()

if (AURALIS_FORMATS STREQUAL "")
    message(FATAL_ERROR "No plug-in format was selected.  Enable at least one of "
                        "AURALIS_BUILD_VST3, AURALIS_BUILD_AU, AURALIS_BUILD_STANDALONE "
                        "or AURALIS_BUILD_AAX.")
endif()

# ---------------------------------------------------------------------------
#  one plug-in target per module
# ---------------------------------------------------------------------------

set(AURALIS_PLUGIN_TARGETS "")
set(AURALIS_FORMAT_TARGETS "")

math(EXPR AURALIS_LAST_MODULE "${AURALIS_MODULE_COUNT} - 1")

foreach (index RANGE ${AURALIS_LAST_MODULE})
    list(GET AURALIS_MODULE_CODES ${index} module_code)
    list(GET AURALIS_MODULE_NAMES ${index} module_name)
    set(module_product "${AURALIS_PRODUCT_CODE_${module_code}}")

    set(target "Auralis_${module_code}")

    juce_add_plugin(${target}
        COMPANY_NAME "Auralis"
        BUNDLE_ID "audio.auralis.${module_code}"
        PLUGIN_MANUFACTURER_CODE "AURA"
        PLUGIN_CODE "${module_product}"
        PRODUCT_NAME "Auralis ${module_name}"
        FORMATS ${AURALIS_FORMATS}
        IS_SYNTH FALSE
        NEEDS_MIDI_INPUT FALSE
        NEEDS_MIDI_OUTPUT FALSE
        IS_MIDI_EFFECT FALSE
        EDITOR_WANTS_KEYBOARD_FOCUS FALSE
        COPY_PLUGIN_AFTER_BUILD FALSE
        # The manifest is written by a pass over the finished bundles, after
        # the runtime has been staged into them: the manifest is produced by
        # loading the module, and the module can only be loaded once the
        # bundle is complete.
        VST3_AUTO_MANIFEST FALSE
        VST3_CATEGORIES Fx Restoration)

    target_sources(${target} PRIVATE
        "${AURALIS_PLUGIN_DIR}/AuralisPlugin.cpp"
        "${AURALIS_PLUGIN_DIR}/AuralisPlugin.h"
        "${AURALIS_PLUGIN_DIR}/aurcore_c_api.h")

    target_include_directories(${target} PRIVATE
        "${AURALIS_PLUGIN_DIR}"
        "${AURALIS_GENERATED_DIR}")

    if (AURALIS_CORE_INCLUDE_DIR)
        target_include_directories(${target} PRIVATE "${AURALIS_CORE_INCLUDE_DIR}")
    endif()

    target_compile_definitions(${target} PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_REPORT_APP_USAGE=0
        JUCE_DISPLAY_SPLASH_SCREEN=0
        JUCE_STRICT_REFCOUNTEDPOINTER=1
        # The VST3-only build does not need the VST2 compatibility layer,
        # and the VST3 SDK we ship does not carry the VST2 headers.
        JUCE_VST3_CAN_REPLACE_VST2=0)

    # juce_add_plugin() does NOT pull the JUCE modules in by itself: the
    # plug-in target has to be linked against them explicitly, or the module
    # include paths never reach the compiler.
    target_link_libraries(${target} PRIVATE
        juce::juce_audio_utils
        juce::juce_audio_processors
        juce::juce_audio_plugin_client
        juce::juce_core
        juce::juce_events
        juce::juce_graphics
        juce::juce_gui_basics)

    target_link_libraries(${target} PRIVATE "${AURALIS_CORE_LIBRARY}")

    add_dependencies(${target} auralis_generated_header)

    list(APPEND AURALIS_PLUGIN_TARGETS ${target})

    foreach (format IN LISTS AURALIS_FORMATS)
        if (TARGET ${target}_${format})
            list(APPEND AURALIS_FORMAT_TARGETS ${target}_${format})
        endif()
    endforeach()
endforeach()

# ---------------------------------------------------------------------------
#  stamping
# ---------------------------------------------------------------------------
#
#  The identity record is written into the binary after it has been linked.
#  The record goes into a slot that the adapter reserves for it, so the
#  binary is patched in place; nothing else about the binary changes.

if (AURALIS_STAMP_IDENTITIES)
    foreach (index RANGE ${AURALIS_LAST_MODULE})
        list(GET AURALIS_MODULE_CODES ${index} module_code)
        set(target "Auralis_${module_code}")

        foreach (format IN LISTS AURALIS_FORMATS)
            set(format_target "${target}_${format}")

            if (NOT TARGET ${format_target})
                continue()
            endif()

            add_custom_command(TARGET ${format_target} POST_BUILD
                COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" stamp
                        --template "$<TARGET_FILE:${format_target}>"
                        --module ${module_code}
                COMMENT "Stamping the identity of ${module_code} into ${format_target}"
                VERBATIM)

            # A bundle has to carry its own runtime: the host loads it from
            # wherever the user put it, with no search path of ours.  The
            # adapter looks for the core package next to the ABI library, so
            # the package goes into Contents/Python.
            add_custom_command(TARGET ${format_target} POST_BUILD
                COMMAND "${Python3_EXECUTABLE}" "${AURALIS_STAGE_TOOL}"
                        --bundle "$<TARGET_FILE_DIR:${format_target}>/../.."
                        --runtime-dir "${AURALIS_RUNTIME_DIR}"
                        --package-dir "${AURALIS_CORE_PACKAGE_DIR}"
                COMMENT "Staging the runtime into ${format_target}"
                VERBATIM)

            if (APPLE AND AURALIS_MACOS_CODESIGN_IDENTITY)
                # Stamping changes the bytes of the binary, so the code
                # signature has to be made again afterwards.
                add_custom_command(TARGET ${format_target} POST_BUILD
                    COMMAND codesign --force --sign "${AURALIS_MACOS_CODESIGN_IDENTITY}"
                            --options runtime --timestamp "$<TARGET_FILE:${format_target}>"
                    COMMENT "Re-signing ${format_target} after stamping"
                    VERBATIM)
            endif()
        endforeach()
    endforeach()
endif()

# ---------------------------------------------------------------------------
#  the install manifest
# ---------------------------------------------------------------------------

if (AURALIS_STAMP_IDENTITIES)
    # The manifest lists every plug-in that was built, with the identity
    # that was read back out of the binary and the hash of the file.
    set(AURALIS_MANIFEST_ARTEFACTS "")

    foreach (format_target IN LISTS AURALIS_FORMAT_TARGETS)
        list(APPEND AURALIS_MANIFEST_ARTEFACTS
             "$<TARGET_PROPERTY:${format_target},JUCE_PLUGIN_ARTEFACT_FILE>")
    endforeach()

    add_custom_target(auralis_install_manifest ALL
        COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" manifest
                --out "${CMAKE_BINARY_DIR}/install-manifest.json"
                ${AURALIS_MANIFEST_ARTEFACTS}
        DEPENDS ${AURALIS_FORMAT_TARGETS}
        COMMENT "Writing the install manifest"
        VERBATIM)

    # The VST3 manifest of every bundle is written last: the helper loads the
    # module, and the module can only be loaded once the bundle carries its
    # runtime.
    if (TARGET juce_vst3_helper)
        set(AURALIS_MANIFEST_BUNDLES "")
        foreach (format_target IN LISTS AURALIS_FORMAT_TARGETS)
            list(APPEND AURALIS_MANIFEST_BUNDLES
                 "$<TARGET_FILE_DIR:${format_target}>/../..")
        endforeach()

        add_dependencies(auralis_install_manifest juce_vst3_helper)

        add_custom_command(TARGET auralis_install_manifest POST_BUILD
            COMMAND "${Python3_EXECUTABLE}" "${AURALIS_MANIFEST_TOOL}"
                    --helper "$<TARGET_FILE:juce_vst3_helper>"
                    --version "${PROJECT_VERSION}"
                    ${AURALIS_MANIFEST_BUNDLES}
            COMMENT "Writing the VST3 manifests"
            VERBATIM)
    endif()
endif()

