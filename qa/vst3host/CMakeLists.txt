# ============================================================================
#  qa/vst3host/CMakeLists.txt
#
#  The independent VST3 host.
#
#  It is deliberately small: one translation unit plus the two SDK source
#  files that have to exist for the interface identifiers and the in-memory
#  stream to work.  Nothing of the plug-in build is reused, so the host
#  proves the built binary on its own terms.
#
#  The VST3 SDK is used from its headers only.  The layout expected here is
#  the new one, the directory that holds
#
#      pluginterfaces/base/...
#      pluginterfaces/vst/...
#      public.sdk/source/common/...
#
#  which is what vendor/vst3sdk is.
#
#  Configure and build
#  -------------------
#      cmd /c "tools\msvc.bat cmake -S qa\vst3host -B build\vst3host -G Ninja ^
#            -DCMAKE_BUILD_TYPE=Release"
#      cmd /c "tools\msvc.bat cmake --build build\vst3host"
#
#  or just run qa/vst3host/run_host.py, which does both and then runs the
#  host against a built module.
# ============================================================================

cmake_minimum_required(VERSION 3.22)

project(AuralisVST3Host LANGUAGES C CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---------------------------------------------------------------------------
#  the layout of this repository
# ---------------------------------------------------------------------------

get_filename_component(AURALIS_HOST_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)
get_filename_component(AURALIS_QA_DIR "${AURALIS_HOST_DIR}/.." ABSOLUTE)
get_filename_component(AURALIS_REPO_ROOT "${AURALIS_QA_DIR}/.." ABSOLUTE)

# ---------------------------------------------------------------------------
#  the VST3 SDK
# ---------------------------------------------------------------------------

set(AURALIS_VST3_SDK_PATH "" CACHE PATH
    "Path to the VST3 SDK: the directory that holds pluginterfaces/ and public.sdk/")

if (NOT AURALIS_VST3_SDK_PATH)
    foreach (candidate
             "${AURALIS_REPO_ROOT}/vendor/vst3sdk"
             "${AURALIS_REPO_ROOT}/vendor/VST3_SDK")
        if (EXISTS "${candidate}/pluginterfaces")
            set(AURALIS_VST3_SDK_PATH "${candidate}")
            break()
        endif()
    endforeach()
endif()

if (NOT AURALIS_VST3_SDK_PATH OR NOT EXISTS "${AURALIS_VST3_SDK_PATH}/pluginterfaces")
    message(FATAL_ERROR
        "The VST3 SDK was not found.  Point AURALIS_VST3_SDK_PATH at the directory\n"
        "that holds pluginterfaces/ and public.sdk/, for example\n"
        "    -DAURALIS_VST3_SDK_PATH=${AURALIS_REPO_ROOT}/vendor/vst3sdk")
endif()

message(STATUS "[auralis] VST3 SDK      : ${AURALIS_VST3_SDK_PATH}")

# ---------------------------------------------------------------------------
#  the host
# ---------------------------------------------------------------------------

add_executable(vst3host
    "${AURALIS_HOST_DIR}/main.cpp"
    # The measuring instruments the host uses on the audio and the state.
    "${AURALIS_HOST_DIR}/measurements.cpp"
    # The interface identifier symbols and the FUID implementation.
    "${AURALIS_VST3_SDK_PATH}/pluginterfaces/base/funknown.cpp"
    # The IBStream implementation used for the state round trip.
    "${AURALIS_VST3_SDK_PATH}/public.sdk/source/common/memorystream.cpp"
)

target_include_directories(vst3host PRIVATE "${AURALIS_VST3_SDK_PATH}")

target_compile_definitions(vst3host PRIVATE
    _CRT_SECURE_NO_WARNINGS
    NOMINMAX
)

if (MSVC)
    # The SDK headers are third party code; do not fail the build over their
    # warnings.
    target_compile_options(vst3host PRIVATE /W3 /wd4996 /wd4244 /wd4250 /permissive-)
    # CoCreateGuid, used by FUID::generate, lives here.
    target_link_libraries(vst3host PRIVATE ole32)
else()
    target_compile_options(vst3host PRIVATE -Wall)
    target_link_libraries(vst3host PRIVATE ${CMAKE_DL_LIBS})
endif()
