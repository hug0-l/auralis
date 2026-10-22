# ============================================================================
#  plugins/vst3native/CMakeLists.txt
#
#  Builds the JUCE-free VST3 adapter.
#
#  WHY A SEPARATE DIRECTORY
#  ------------------------
#  plugins/vst3 holds the JUCE based adapter.  It needs a JUCE checkout and
#  a JUCE compatible VST3 SDK layout at the same time, and the two do not
#  line up: JUCE 6 wants the 3.6 era SDK, JUCE 7 and 8 want a 3.7 era
#  layout that no SDK tag provides.  The SDK in this repository is 3.8.1,
#  in the new split layout (pluginterfaces/ next to public.sdk/).
#
#  This build therefore compiles the adapter straight against the SDK's
#  pluginterfaces, with no JUCE and no SDK helper library.
#
#  ONE TARGET, MANY PLUG-INS
#  -------------------------
#  The adapter is a template: which module a binary implements is decided by
#  the identity record that the build stamps into it (see
#  plugins/identity.py and plugins/vst3/build_identity.py).  So there is ONE
#  target, and the module is chosen with a cache variable:
#
#      cmake -S plugins/vst3native -B build/vst3native \
#            -DAURALIS_MODULE=AUR_DECLICK
#      cmake --build build/vst3native --config Release
#
#  Every other module is the same build with another -DAURALIS_MODULE.  See
#  README.md for the loop that builds all of them, and for the note on the
#  variants this repository ships.
#
#  WHAT THIS BUILD NEEDS
#  ---------------------
#  * the VST3 SDK (vendor/vst3sdk by default: pluginterfaces/ + public.sdk/),
#  * a shared library that implements the C ABI in plugins/vst3/aurcore_c_api.h
#    (plugins/abi/build/aurcore_abi.dll by default), and
#  * Python, for the generated parameter table, the identity stamping and
#    the checks below.
#
#  ON WINDOWS
#  ----------
#  The MSVC environment has to be set up before cmake runs.  This repository
#  ships a wrapper for that:
#
#      cmd /c "tools\msvc.bat cmake -S plugins/vst3native -B build/vst3native \
#                -G Ninja -DAURALIS_MODULE=AUR_DECLICK"
#      cmd /c "tools\msvc.bat cmake --build build/vst3native"
#
#  cmake and ninja live in %LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts.
# ============================================================================

cmake_minimum_required(VERSION 3.22)

project(AuralisVST3Native LANGUAGES C CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 11)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---------------------------------------------------------------------------
#  options
# ---------------------------------------------------------------------------

set(AURALIS_MODULE "" CACHE STRING
    "The module code this build implements, e.g. AUR_DECLICK")
set(AURALIS_VST3_SDK_PATH "" CACHE PATH
    "Path to the VST3 SDK 3.8.1 (the directory that holds pluginterfaces/ and public.sdk/)")
set(AURALIS_CORE_LIBRARY "" CACHE FILEPATH
    "Library implementing plugins/vst3/aurcore_c_api.h")
set(AURALIS_CORE_DLL "" CACHE FILEPATH
    "The core shared library, copied next to the plug-in so it can be loaded")
set(AURALIS_STAMP_IDENTITY "ON" CACHE STRING
    "Stamp the identity record into the built plug-in (ON/OFF)")
set(AURALIS_PYTHON "" CACHE FILEPATH "The Python interpreter to use")

# ---------------------------------------------------------------------------
#  the layout of this repository
# ---------------------------------------------------------------------------

get_filename_component(AURALIS_PLUGIN_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)
get_filename_component(AURALIS_PLUGINS_DIR "${AURALIS_PLUGIN_DIR}/.." ABSOLUTE)
get_filename_component(AURALIS_REPO_ROOT "${AURALIS_PLUGINS_DIR}/.." ABSOLUTE)

set(AURALIS_IDENTITY_TOOL "${AURALIS_PLUGIN_DIR}/../vst3/build_identity.py")
set(AURALIS_CORE_HEADER_DIR "${AURALIS_PLUGIN_DIR}/../vst3")

# ---------------------------------------------------------------------------
#  the SDK
# ---------------------------------------------------------------------------

if (NOT AURALIS_VST3_SDK_PATH)
    set(AURALIS_VST3_SDK_PATH "${AURALIS_REPO_ROOT}/vendor/vst3sdk"
        CACHE PATH "Path to the VST3 SDK" FORCE)
endif()

if (NOT EXISTS "${AURALIS_VST3_SDK_PATH}/pluginterfaces/vst/ivstaudioprocessor.h")
    message(FATAL_ERROR
        "AURALIS_VST3_SDK_PATH does not look like the VST3 SDK 3.8.1: ${AURALIS_VST3_SDK_PATH}\n"
        "It must be the directory that holds pluginterfaces/ and public.sdk/.")
endif()

# The include roots:
#   * the SDK root, for "pluginterfaces/..."
#   * the SDK's public.sdk, for "public.sdk/source/..."
set(AURALIS_SDK_INCLUDE_DIRS
    "${AURALIS_VST3_SDK_PATH}"
    "${AURALIS_VST3_SDK_PATH}/public.sdk"
    "${AURALIS_VST3_SDK_PATH}/public.sdk/source/vst")

# ---------------------------------------------------------------------------
#  the core
# ---------------------------------------------------------------------------
#
#  The core is a separate shared library.  On Windows the adapter needs an
#  IMPORT library for it, and that import library is generated here from the
#  shared C ABI header.  Two consequences:
#
#    * the build does not depend on whatever the core's own build tree
#      happens to contain, and
#    * the export list can never drift away from the header, because the
#      header IS the list.
#
#  Everywhere else the shared library itself is linked against.

if (NOT AURALIS_CORE_DLL)
    if (WIN32)
        set(_dll_candidates "${AURALIS_REPO_ROOT}/plugins/abi/build/aurcore_abi.dll")
    else()
        set(_dll_candidates "${AURALIS_REPO_ROOT}/plugins/abi/build/libaurcore_abi.so")
    endif()

    foreach (candidate IN LISTS _dll_candidates)
        if (EXISTS "${candidate}")
            set(AURALIS_CORE_DLL "${candidate}" CACHE FILEPATH "" FORCE)
            break()
        endif()
    endforeach()
endif()

if (NOT AURALIS_CORE_DLL OR NOT EXISTS "${AURALIS_CORE_DLL}")
    message(FATAL_ERROR
        "AURALIS_CORE_DLL is not set, or does not exist.  It must point at the\n"
        "shared library that implements the C ABI declared in\n"
        "plugins/vst3/aurcore_c_api.h:\n"
        "    aur_engine_create, aur_engine_add_module, aur_engine_set_param,\n"
        "    aur_engine_process and aur_engine_destroy.\n"
        "The adapter layer is written against that interface; the implementation\n"
        "of the interface lives with the DSP core.\n"
        "Build it with:  python plugins/abi/build_abi.py")
endif()

get_filename_component(AURALIS_CORE_DLL_NAME "${AURALIS_CORE_DLL}" NAME_WE)

if (NOT AURALIS_CORE_LIBRARY)
    if (WIN32)
        set(_core_candidates
            "${AURALIS_REPO_ROOT}/plugins/abi/build/aurcore_abi.lib")
    else()
        set(_core_candidates
            "${AURALIS_REPO_ROOT}/plugins/abi/build/libaurcore_abi.a"
            "${AURALIS_REPO_ROOT}/plugins/abi/build/aurcore_abi.a")
    endif()

    foreach (candidate IN LISTS _core_candidates)
        if (EXISTS "${candidate}")
            set(AURALIS_CORE_LIBRARY "${candidate}" CACHE FILEPATH "" FORCE)
            break()
        endif()
    endforeach()
endif()

if (MSVC)
    # The import library is generated from the ABI header.
    set(AURALIS_CORE_DEF "${CMAKE_CURRENT_BINARY_DIR}/auralis_core.def")
    set(AURALIS_CORE_IMPORT_LIB "${CMAKE_CURRENT_BINARY_DIR}/auralis_core.lib")

    file(READ "${AURALIS_CORE_HEADER_DIR}/aurcore_c_api.h" AURALIS_CORE_HEADER_TEXT)
    # Every entry point of the ABI, not only the engine ones: the shutdown
    # call is part of the contract as well.
    string(REGEX MATCHALL "aur_[a-z_]+[ ]*\\(" AURALIS_CORE_EXPORTS "${AURALIS_CORE_HEADER_TEXT}")
    list(REMOVE_DUPLICATES AURALIS_CORE_EXPORTS)

    if (NOT AURALIS_CORE_EXPORTS)
        message(FATAL_ERROR
            "No aur_* entry points were found in\n"
            "    ${AURALIS_CORE_HEADER_DIR}/aurcore_c_api.h\n"
            "so no import library can be generated for the core.")
    endif()

    set(AURALIS_CORE_DEF_TEXT "LIBRARY ${AURALIS_CORE_DLL_NAME}\nEXPORTS\n")

    foreach (symbol IN LISTS AURALIS_CORE_EXPORTS)
        string(REGEX REPLACE "[ ]*\\($" "" symbol "${symbol}")
        set(AURALIS_CORE_DEF_TEXT "${AURALIS_CORE_DEF_TEXT}${symbol}\n")
    endforeach()

    file(WRITE "${AURALIS_CORE_DEF}" "${AURALIS_CORE_DEF_TEXT}")

    find_program(AURALIS_LIB_EXECUTABLE
        NAMES lib lib.exe
        HINTS ${CMAKE_CXX_IMPLICIT_LINK_DIRECTORIES} ${CMAKE_C_IMPLICIT_LINK_DIRECTORIES})

    if (NOT AURALIS_LIB_EXECUTABLE)
        message(FATAL_ERROR
            "The MSVC library tool (lib.exe) was not found, so the import library\n"
            "for the core cannot be generated.  Set AURALIS_CORE_LIBRARY to a\n"
            "ready made import library instead.")
    endif()

    if (CMAKE_SIZEOF_VOID_P EQUAL 8)
        set(AURALIS_LIB_MACHINE "x64")
    else()
        set(AURALIS_LIB_MACHINE "x86")
    endif()

    add_custom_command(
        OUTPUT "${AURALIS_CORE_IMPORT_LIB}"
        COMMAND "${AURALIS_LIB_EXECUTABLE}" /nologo
                "/def:${AURALIS_CORE_DEF}" "/out:${AURALIS_CORE_IMPORT_LIB}"
                "/machine:${AURALIS_LIB_MACHINE}"
        DEPENDS "${AURALIS_CORE_DEF}"
        COMMENT "Generating the import library for the core"
        VERBATIM)

    add_custom_target(auralis_core_import_lib DEPENDS "${AURALIS_CORE_IMPORT_LIB}")

    set(AURALIS_CORE_LINK_LIBRARY "${AURALIS_CORE_IMPORT_LIB}")
else()
    if (NOT AURALIS_CORE_LIBRARY OR NOT EXISTS "${AURALIS_CORE_LIBRARY}")
        message(FATAL_ERROR
            "AURALIS_CORE_LIBRARY is not set, or does not exist.  It must point at\n"
            "the library that implements the C ABI declared in\n"
            "plugins/vst3/aurcore_c_api.h.")
    endif()

    set(AURALIS_CORE_LINK_LIBRARY "${AURALIS_CORE_LIBRARY}")
endif()

# ---------------------------------------------------------------------------
#  python and the registry
# ---------------------------------------------------------------------------

if (AURALIS_PYTHON)
    set(Python3_EXECUTABLE "${AURALIS_PYTHON}")
else()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
endif()

if (NOT AURALIS_MODULE)
    message(FATAL_ERROR
        "AURALIS_MODULE is not set.  It names the module this build implements,\n"
        "for example:\n"
        "    cmake -S plugins/vst3native -B build/vst3native -DAURALIS_MODULE=AUR_DECLICK")
endif()

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

list(FIND AURALIS_MODULE_CODES "${AURALIS_MODULE}" AURALIS_MODULE_INDEX)

if (AURALIS_MODULE_INDEX LESS 0)
    message(FATAL_ERROR
        "Unknown module code '${AURALIS_MODULE}'.  The registry knows:\n"
        "    ${AURALIS_MODULE_CODES}")
endif()

list(GET AURALIS_MODULE_NAMES ${AURALIS_MODULE_INDEX} AURALIS_DISPLAY_NAME)

# The four character product code is what the class id is derived from.  It
# is looked up in the registry table, so the class id of the built plug-in
# is the class id the identity record carries.
#
# NOTE: CMake's regular expressions have no {n} repetition, so the four
# characters of the product code are spelled out one by one.
string(REGEX MATCH
    "set\\(AURALIS_PRODUCT_CODE_${AURALIS_MODULE} \"([A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9])\"\\)"
    AURALIS_PRODUCT_CODE_MATCH "${AURALIS_MODULE_TABLE_TEXT}")

set(AURALIS_PRODUCT_CODE "${CMAKE_MATCH_1}")

if (NOT AURALIS_PRODUCT_CODE)
    message(WARNING
        "No product code found for '${AURALIS_MODULE}'.  The class id of an "
        "unstamped template will not match the stamped one.")
    set(AURALIS_PRODUCT_CODE "UNKN")
endif()

string(REGEX MATCH
    "set\\(AURALIS_FILE_NAME_${AURALIS_MODULE} \"([^\"]+)\"\\)"
    AURALIS_FILE_NAME_MATCH "${AURALIS_MODULE_TABLE_TEXT}")

set(AURALIS_FILE_NAME "${CMAKE_MATCH_1}")

if (NOT AURALIS_FILE_NAME)
    set(AURALIS_FILE_NAME "Auralis ${AURALIS_DISPLAY_NAME}.vst3")
endif()

# The build passes its command lines through the shell, and the shell
# treats a handful of characters specially.  A name that holds one of them
# ("Time & Pitch") would break the command line, so the artefact carries a
# safe spelling.  The name the host shows comes from the class inside the
# plug-in, and that keeps the original.
string(REGEX REPLACE "[ ]*&[ ]*" " and " AURALIS_SAFE_NAME "Auralis ${AURALIS_DISPLAY_NAME}")
string(REGEX REPLACE "[|<>^\"]" "" AURALIS_SAFE_NAME "${AURALIS_SAFE_NAME}")
string(REGEX REPLACE "[ ]*&[ ]*" " and " AURALIS_SAFE_FILE_NAME "${AURALIS_FILE_NAME}")
string(REGEX REPLACE "[|<>^\"]" "" AURALIS_SAFE_FILE_NAME "${AURALIS_SAFE_FILE_NAME}")

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
            "${AURALIS_PLUGINS_DIR}/identity.py"
            ${AURALIS_REGISTRY_SOURCES}
    COMMENT "Generating AuralisGenerated.h from the canonical parameter table"
    VERBATIM)

add_custom_target(auralis_generated_header DEPENDS "${AURALIS_GENERATED_HEADER}")

# ---------------------------------------------------------------------------
#  the adapter
# ---------------------------------------------------------------------------

add_library(AuralisVST3Native SHARED
    "${AURALIS_PLUGIN_DIR}/AuralisVST3.cpp"
    "${AURALIS_PLUGIN_DIR}/AuralisVST3.h"
    "${AURALIS_CORE_HEADER_DIR}/aurcore_c_api.h"
    # The SDK's own interface implementations.  The pluginterfaces module is
    # self contained; the public.sdk helper classes are not used, because
    # they need the SDK's base module, which is not part of the 3.8.1
    # split layout this repository holds.
    "${AURALIS_VST3_SDK_PATH}/pluginterfaces/base/funknown.cpp"
    "${AURALIS_VST3_SDK_PATH}/pluginterfaces/base/coreiids.cpp"
    "${AURALIS_VST3_SDK_PATH}/pluginterfaces/base/conststringtable.cpp"
    "${AURALIS_VST3_SDK_PATH}/pluginterfaces/base/ustring.cpp"
    "${AURALIS_VST3_SDK_PATH}/public.sdk/source/vst/vstinitiids.cpp")

set_target_properties(AuralisVST3Native PROPERTIES
    OUTPUT_NAME "${AURALIS_SAFE_NAME}"
    PREFIX ""
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin")

target_include_directories(AuralisVST3Native PRIVATE
    "${AURALIS_PLUGIN_DIR}"
    "${AURALIS_GENERATED_DIR}"
    "${AURALIS_CORE_HEADER_DIR}"
    ${AURALIS_SDK_INCLUDE_DIRS})

target_compile_definitions(AuralisVST3Native PRIVATE
    AURALIS_MODULE_CODE="${AURALIS_MODULE}"
    AURALIS_PRODUCT_CODE="${AURALIS_PRODUCT_CODE}"
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    _CRT_SECURE_NO_WARNINGS)

if (MSVC)
    target_compile_options(AuralisVST3Native PRIVATE /W3 /EHsc /utf-8)
endif()

# The core is not inside the bundle any more: the family shares one copy of
# it, in the runtime directory next to the bundles.  A static import on the
# core cannot be satisfied then, because the loader resolves it when the
# plug-in is loaded and never looks outside the bundle.  The import becomes
# a delay-load descriptor instead, and the plug-in loads the core itself
# (see AuralisVST3.cpp, "the shared runtime").
#
# delayimp.lib is the delay-load helper; without it the first call into the
# core cannot be resolved.
target_link_libraries(AuralisVST3Native PRIVATE "${AURALIS_CORE_LINK_LIBRARY}" delayimp.lib)

if (MSVC)
    target_link_options(AuralisVST3Native PRIVATE /DELAYLOAD:aurcore_abi.dll)
endif()

add_dependencies(AuralisVST3Native auralis_generated_header)

if (TARGET auralis_core_import_lib)
    add_dependencies(AuralisVST3Native auralis_core_import_lib)
endif()

# ---------------------------------------------------------------------------
#  the checks
# ---------------------------------------------------------------------------
#
#  The built module has to export GetPluginFactory, and it has to publish
#  the parameters and the state chunk of the shared convention.  The two
#  scripts below load the module the way a host loads it, so they also
#  prove that the core library the module needs is resolvable.
#
#  The scripts are written into the build tree, so the repository stays
#  free of extra files.

set(AURALIS_CHECK_EXPORT_SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/check_vst3_export.py")
set(AURALIS_CHECK_STATE_SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/check_vst3_state.py")

file(WRITE "${AURALIS_CHECK_EXPORT_SCRIPT}" [==[
"""Check that a built Auralis VST3 module is loadable and reports its class id.

Usage:  python check_vst3_export.py <module binary>

The module is loaded the way a host loads it, so this also proves that the
core library the module needs is resolvable.
"""

import ctypes
import json
import os
import sys

MARKER = b"AURIDENTITYv1"
REGION = 4096

FUNC = ctypes.WINFUNCTYPE if os.name == "nt" else ctypes.CFUNCTYPE


class FactoryVtbl(ctypes.Structure):
    _fields_ = [
        ("queryInterface", ctypes.c_void_p),
        ("addRef", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("getFactoryInfo", ctypes.c_void_p),
        ("countClasses", ctypes.c_void_p),
        ("getClassInfo", ctypes.c_void_p),
        ("createInstance", ctypes.c_void_p),
        ("getClassInfo2", ctypes.c_void_p),
    ]


class PClassInfo(ctypes.Structure):
    _fields_ = [
        ("cid", ctypes.c_char * 16),
        ("cardinality", ctypes.c_int32),
        ("category", ctypes.c_char * 32),
        ("name", ctypes.c_char * 64),
    ]


def read_identity(path):
    """Read the identity record that is stamped into a binary."""
    with open(path, "rb") as handle:
        data = handle.read()

    at = data.find(MARKER)

    if at < 0:
        return None

    region = data[at + len(MARKER): at + len(MARKER) + REGION]
    end = region.find(b"\x00")

    if end <= 0:
        return None

    return json.loads(region[:end].decode("utf-8"))


def class_id_bytes(text):
    """The bytes a binary reports for a class id on this platform.

    The class id is a GUID string; the sixteen bytes are that string's
    platform encoding.  The SDK keeps the COM compatible layout on
    Windows (the first word little endian, the second one with its bytes
    swapped in pairs, the last two big endian) and the plain big endian
    layout everywhere else, so the layout has to follow the platform.
    """
    digits = text.replace("-", "")
    words = [int(digits[index * 8:(index + 1) * 8], 16) for index in range(4)]

    def word(value, index):
        raw = value.to_bytes(4, "big")

        if os.name != "nt":
            return raw

        if index == 0:
            return raw[::-1]

        if index == 1:
            return bytes([raw[1], raw[0], raw[3], raw[2]])

        return raw

    return b"".join(word(value, index) for index, value in enumerate(words))


def main(argv):
    if len(argv) < 2:
        print("usage: check_vst3_export.py <module binary>", file=sys.stderr)
        return 2

    path = os.path.abspath(argv[1])
    print("module : %s" % path)

    if os.name == "nt":
        module = ctypes.WinDLL(path)
    else:
        module = ctypes.CDLL(path)

    failures = []

    for symbol in ("GetPluginFactory", "ModuleEntry"):
        try:
            getattr(module, symbol)
            print("export : %-16s present" % symbol)
        except AttributeError:
            failures.append("%s is not exported" % symbol)
            print("export : %-16s MISSING" % symbol)

    factory = module.GetPluginFactory
    factory.restype = ctypes.c_void_p
    factory.argtypes = []
    factory_pointer = factory()

    if not factory_pointer:
        failures.append("GetPluginFactory returned null")
        print("factory: null")
        return 1

    print("factory: %s" % hex(factory_pointer))

    vtable = ctypes.cast(factory_pointer, ctypes.POINTER(ctypes.POINTER(FactoryVtbl))).contents

    count_classes = FUNC(ctypes.c_int32, ctypes.c_void_p)(vtable.contents.countClasses)
    count = count_classes(factory_pointer)
    print("classes: %d" % count)

    if count != 1:
        failures.append("expected exactly one class, got %d" % count)

    info = PClassInfo()
    get_class_info = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32,
                          ctypes.POINTER(PClassInfo))(vtable.contents.getClassInfo)
    result = get_class_info(factory_pointer, 0, ctypes.byref(info))

    if result != 0:
        failures.append("getClassInfo failed with 0x%08x" % (result & 0xFFFFFFFF))
    else:
        cid = bytes(info.cid)
        name = info.name.decode("utf-8", "replace")
        category = info.category.decode("utf-8", "replace")
        print("class  : %s" % name)
        print("category: %s" % category)
        print("cid    : %s" % cid.hex())

        identity = read_identity(path)

        if identity is None:
            failures.append("the binary carries no identity record")
        else:
            expected = class_id_bytes(identity["vst3_fuid"])
            print("record : %s / %s" % (identity["module_code"], identity["vst3_fuid"]))

            if expected != cid:
                failures.append("class id %s does not match the record %s"
                                % (cid.hex(), expected.hex()))

    if failures:
        print("")

        for failure in failures:
            print("FAILED: %s" % failure)

        return 1

    print("")
    print("OK: the module loads and exports GetPluginFactory")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
]==])

file(WRITE "${AURALIS_CHECK_STATE_SCRIPT}" [==[
"""Check the parameters and the state chunk of a built Auralis VST3 module.

The state chunk is the convention the JUCE adapter uses as well, so this
script checks the container, the chain document and the parameter
document, and it round trips a parameter through the state.

Usage:  python check_vst3_state.py <module binary>
"""

import ctypes
import json
import os
import sys

MARKER = b"AURIDENTITYv1"
REGION = 4096

FUNC = ctypes.WINFUNCTYPE if os.name == "nt" else ctypes.CFUNCTYPE

NO_INTERFACE = 0x80004002 if os.name == "nt" else -1
RESULT_OK = 0


def inline_uid(l1, l2, l3, l4):
    """The byte layout the SDK's INLINE_UID macro produces on this platform.

    The SDK keeps the layout COM compatible on Windows (the first word
    little endian, the second one with its bytes swapped in pairs, the
    last two big endian) and plain big endian everywhere else, so the
    layout has to follow the platform.
    """
    com = os.name == "nt"

    def word(value, index):
        raw = value.to_bytes(4, "big")

        if not com:
            return raw

        if index == 0:
            return raw[::-1]

        if index == 1:
            return bytes([raw[1], raw[0], raw[3], raw[2]])

        return raw

    return word(l1, 0) + word(l2, 1) + word(l3, 2) + word(l4, 3)


IID_EDIT_CONTROLLER = inline_uid(0xDCD7BBE3, 0x7742448D, 0xA874AACC, 0x979C759E)


class FactoryVtbl(ctypes.Structure):
    _fields_ = [
        ("queryInterface", ctypes.c_void_p),
        ("addRef", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("getFactoryInfo", ctypes.c_void_p),
        ("countClasses", ctypes.c_void_p),
        ("getClassInfo", ctypes.c_void_p),
        ("createInstance", ctypes.c_void_p),
        ("getClassInfo2", ctypes.c_void_p),
    ]


class PClassInfo(ctypes.Structure):
    _fields_ = [
        ("cid", ctypes.c_char * 16),
        ("cardinality", ctypes.c_int32),
        ("category", ctypes.c_char * 32),
        ("name", ctypes.c_char * 64),
    ]


class ParameterInfo(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("title", ctypes.c_uint16 * 128),
        ("shortTitle", ctypes.c_uint16 * 128),
        ("units", ctypes.c_uint16 * 128),
        ("stepCount", ctypes.c_int32),
        ("defaultNormalizedValue", ctypes.c_double),
        ("unitId", ctypes.c_int32),
        ("flags", ctypes.c_int32),
    ]


class StreamVtbl(ctypes.Structure):
    _fields_ = [
        ("queryInterface", ctypes.c_void_p),
        ("addRef", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("read", ctypes.c_void_p),
        ("write", ctypes.c_void_p),
        ("seek", ctypes.c_void_p),
        ("tell", ctypes.c_void_p),
    ]


class StreamState(object):
    """The data behind a memory stream."""

    def __init__(self):
        self.data = bytearray()
        self.position = 0


STREAMS = {}


@FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
def _query_interface(this, iid, obj):
    if obj:
        obj[0] = None

    return NO_INTERFACE


@FUNC(ctypes.c_uint32, ctypes.c_void_p)
def _add_ref(this):
    return 1


@FUNC(ctypes.c_uint32, ctypes.c_void_p)
def _release(this):
    return 1


@FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32,
      ctypes.POINTER(ctypes.c_int32))
def _read(this, buffer, num_bytes, num_read):
    state = STREAMS.get(this)

    if state is None:
        return -1

    available = max(0, min(num_bytes, len(state.data) - state.position))

    if available > 0:
        payload = bytes(state.data[state.position:state.position + available])
        ctypes.memmove(buffer, payload, available)

    state.position += available

    if num_read:
        num_read[0] = available

    return RESULT_OK if available == num_bytes else -1


@FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32,
      ctypes.POINTER(ctypes.c_int32))
def _write(this, buffer, num_bytes, num_written):
    state = STREAMS.get(this)

    if state is None:
        return -1

    if num_bytes > 0:
        address = ctypes.cast(buffer, ctypes.c_void_p).value
        payload = ctypes.string_at(address, num_bytes)

        if len(state.data) < state.position + num_bytes:
            state.data.extend(b"\x00" * (state.position + num_bytes - len(state.data)))

        state.data[state.position:state.position + num_bytes] = payload

    state.position += num_bytes

    if num_written:
        num_written[0] = num_bytes

    return RESULT_OK


@FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32,
      ctypes.POINTER(ctypes.c_int64))
def _seek(this, position, mode, result):
    state = STREAMS.get(this)

    if state is None:
        return -1

    if mode == 0:
        target = position
    elif mode == 1:
        target = state.position + position
    else:
        target = len(state.data) + position

    state.position = max(0, min(target, len(state.data)))

    if result:
        result[0] = state.position

    return RESULT_OK


@FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))
def _tell(this, result):
    state = STREAMS.get(this)

    if state is None:
        return -1

    if result:
        result[0] = state.position

    return RESULT_OK


def make_stream():
    """Return the address of a memory stream the plug-in can write to."""
    entries = (ctypes.c_void_p * 7)(
        ctypes.cast(_query_interface, ctypes.c_void_p),
        ctypes.cast(_add_ref, ctypes.c_void_p),
        ctypes.cast(_release, ctypes.c_void_p),
        ctypes.cast(_read, ctypes.c_void_p),
        ctypes.cast(_write, ctypes.c_void_p),
        ctypes.cast(_seek, ctypes.c_void_p),
        ctypes.cast(_tell, ctypes.c_void_p),
    )

    vtable = ctypes.cast(entries, ctypes.POINTER(StreamVtbl))

    # The stream is a COM style object: the first field points at the
    # vtable.  The object itself only has to have an address, and it has
    # to stay alive for as long as the plug-in may use it.
    class Holder(ctypes.Structure):
        _fields_ = [("vtbl", ctypes.POINTER(StreamVtbl))]

    holder = Holder()
    holder.vtbl = vtable

    address = ctypes.addressof(holder)
    state = StreamState()
    state.holder = holder
    STREAMS[address] = state

    return address


def read_identity(path):
    with open(path, "rb") as handle:
        data = handle.read()

    at = data.find(MARKER)

    if at < 0:
        return None

    region = data[at + len(MARKER): at + len(MARKER) + REGION]
    end = region.find(b"\x00")

    if end <= 0:
        return None

    return json.loads(region[:end].decode("utf-8"))


def text16(field):
    out = []

    for value in field:
        if value == 0:
            break

        out.append(chr(value))

    return "".join(out)


def split_chunk(blob):
    """Split a state chunk into its NUL terminated fields."""
    fields = []
    start = 0

    while start < len(blob):
        end = blob.find(b"\x00", start)

        if end < 0:
            fields.append(blob[start:])
            break

        fields.append(blob[start:end])
        start = end + 1

    return fields


def main(argv):
    if len(argv) < 2:
        print("usage: check_vst3_state.py <module binary>", file=sys.stderr)
        return 2

    path = os.path.abspath(argv[1])

    if os.name == "nt":
        module = ctypes.WinDLL(path)
    else:
        module = ctypes.CDLL(path)

    failures = []

    factory = module.GetPluginFactory
    factory.restype = ctypes.c_void_p
    factory.argtypes = []
    factory_pointer = factory()

    vtable = ctypes.cast(factory_pointer, ctypes.POINTER(ctypes.POINTER(FactoryVtbl))).contents

    info = PClassInfo()
    get_class_info = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32,
                          ctypes.POINTER(PClassInfo))(vtable.contents.getClassInfo)
    get_class_info(factory_pointer, 0, ctypes.byref(info))

    identity = read_identity(path)

    if identity is None:
        print("FAILED: the binary carries no identity record")
        return 1

    # -- create an edit controller ------------------------------------------
    create = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                  ctypes.POINTER(ctypes.c_void_p))(vtable.contents.createInstance)

    controller = ctypes.c_void_p()
    result = create(factory_pointer,
                     ctypes.cast(ctypes.create_string_buffer(bytes(info.cid)), ctypes.c_void_p),
                     ctypes.cast(ctypes.create_string_buffer(IID_EDIT_CONTROLLER), ctypes.c_void_p),
                     ctypes.byref(controller))

    if result != RESULT_OK or not controller:
        print("FAILED: the factory did not create an edit controller (0x%08x)"
              % (result & 0xFFFFFFFF))
        return 1

    controller_vtable = ctypes.cast(controller,
                                    ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def entry(index):
        return ctypes.cast(controller_vtable[index], ctypes.c_void_p).value

    get_parameter_count = FUNC(ctypes.c_int32, ctypes.c_void_p)(entry(8))
    get_parameter_info = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32,
                              ctypes.POINTER(ParameterInfo))(entry(9))
    get_param_normalized = FUNC(ctypes.c_double, ctypes.c_void_p, ctypes.c_uint32)(entry(14))
    set_param_normalized = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                                ctypes.c_double)(entry(15))
    get_state = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)(entry(7))
    set_state = FUNC(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)(entry(6))

    count = get_parameter_count(controller)
    print("parameters: %d" % count)

    expected = {int(k): v for k, v in identity["param_id_map"].items()}

    # The plug-in publishes two parameters of its own: the bypass switch and
    # the program change parameter.  They are not in the identity record,
    # because they are not parameters of the core.  The program change
    # parameter is only published when the module has more than one preset.
    own = {1652125811: "Bypass", 1652125810: "Program"}

    published = {}
    steps = {}

    for index in range(count):
        details = ParameterInfo()
        get_parameter_info(controller, index, ctypes.byref(details))

        title = text16(details.title)
        published[details.id] = title
        steps[details.id] = details.stepCount

        print("  %3d  %-24s units %-6s steps %-3d default %.4f"
              % (details.id, title, text16(details.units), details.stepCount,
                 details.defaultNormalizedValue))

        if details.id in own:
            if title != own[details.id]:
                failures.append("parameter %d is called '%s', the plug-in calls it '%s'"
                                % (details.id, title, own[details.id]))
        elif details.id not in expected:
            failures.append("parameter %d is not in the identity record" % details.id)
        elif title != expected[details.id]:
            failures.append("parameter %d is called '%s', the record says '%s'"
                            % (details.id, title, expected[details.id]))

    extra = [pid for pid in published if pid not in expected]

    if count != len(expected) + len(extra):
        failures.append("the controller publishes %d parameter(s), the record names %d and the "
                        "plug-in owns %d more"
                        % (count, len(expected), len(extra)))

    for pid in extra:
        if pid not in own:
            failures.append("parameter %d is neither in the identity record nor one of the "
                            "plug-in's own" % pid)

    # -- the state chunk ----------------------------------------------------
    # A continuous parameter is used for the round trip, because a stepped
    # one snaps its value.
    continuous = [pid for pid in sorted(published) if steps[pid] == 0] or sorted(published)
    param_id = continuous[0]

    set_param_normalized(controller, param_id, 0.25)
    first = get_param_normalized(controller, param_id)

    if abs(first - 0.25) > 1e-6:
        failures.append("the parameter did not keep the value it was given")

    stream = make_stream()
    result = get_state(controller, ctypes.c_void_p(stream))

    if result != RESULT_OK:
        print("FAILED: getState failed with 0x%08x" % (result & 0xFFFFFFFF))
        return 1

    blob = bytes(STREAMS[stream].data)
    fields = split_chunk(blob)

    print("state: %d byte(s), %d field(s)" % (len(blob), len(fields)))

    if not fields or fields[0] != b"auralis.state.v1":
        failures.append("the state chunk does not start with the shared marker")
    else:
        print("  marker : auralis.state.v1")

    if len(fields) < 3:
        failures.append("the state chunk has %d field(s), three are expected" % len(fields))
    else:
        chain = fields[1].decode("utf-8", "replace")
        document = fields[2].decode("utf-8", "replace")

        if chain:
            print("  chain  : %s" % (chain[:70] + ("..." if len(chain) > 70 else "")))

            try:
                parsed = json.loads(chain)
                print("  chain  : a JSON document with %d key(s)" % len(parsed))
            except Exception as exc:
                failures.append("the chain field is not JSON: %s" % exc)
        else:
            # The core engine only exists while the plug-in is processing,
            # and the checker never starts it.
            print("  chain  : (empty, the core engine is not running)")

        print("  params : %s" % document.replace("\r\n", " ").replace("\n", " "))

        if not document.startswith("<?xml"):
            failures.append("the parameter field is not an XML document")

        for expected_id in list(expected) + list(published):
            if ('id="%d"' % expected_id) not in document:
                failures.append("the parameter document has no entry for %d" % expected_id)

    # -- a round trip through the state -------------------------------------
    if count > 0:
        set_param_normalized(controller, param_id, 0.75)

        if abs(get_param_normalized(controller, param_id) - 0.75) > 1e-6:
            failures.append("the parameter did not take the second value")

        # The host hands the state back in a fresh stream.
        restore = make_stream()
        STREAMS[restore].data.extend(blob)
        STREAMS[restore].position = 0

        result = set_state(controller, ctypes.c_void_p(restore))

        if result != RESULT_OK:
            failures.append("setState failed with 0x%08x" % (result & 0xFFFFFFFF))
        else:
            restored = get_param_normalized(controller, param_id)
            print("  round trip: %.4f -> %.4f -> %.4f" % (first, 0.75, restored))

            # The state carries the natural value of the parameter.  A
            # continuous parameter keeps it exactly, so the value after the
            # round trip has to be the one that was set.  A stepped one snaps
            # to its steps, so what has to hold there is that the round trip
            # is stable: restoring the same state again gives the same
            # value.
            if steps.get(param_id, 0) == 0:
                if abs(restored - first) > 1e-4:
                    failures.append("the state did not restore the parameter (%.4f)" % restored)
            else:
                again_stream = make_stream()
                STREAMS[again_stream].data.extend(blob)
                STREAMS[again_stream].position = 0
                set_state(controller, ctypes.c_void_p(again_stream))
                again = get_param_normalized(controller, param_id)

                if abs(again - restored) > 1e-6:
                    failures.append("the state is not stable (%.4f then %.4f)"
                                    % (restored, again))
                else:
                    print("  the parameter is stepped, so the round trip is checked for stability")

    if failures:
        print("")

        for failure in failures:
            print("FAILED: %s" % failure)

        return 1

    print("")
    print("OK: the parameters and the state chunk are the shared ones")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
]==])

# ---------------------------------------------------------------------------
#  stamping
# ---------------------------------------------------------------------------
#
#  The identity record is written into the binary after it has been linked.
#  The record goes into a slot that the adapter reserves for it, so the
#  binary is patched in place; nothing else about the binary changes.

set(AURALIS_STAMPED_DIR "${CMAKE_BINARY_DIR}/stamped")
set(AURALIS_STAMPED_BUNDLE "${AURALIS_STAMPED_DIR}/${AURALIS_SAFE_FILE_NAME}")
set(AURALIS_STAMPED_BINARY "${AURALIS_STAMPED_BUNDLE}/${AURALIS_SAFE_FILE_NAME}.dll")

if (AURALIS_STAMP_IDENTITY)
    add_custom_command(TARGET AuralisVST3Native POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${AURALIS_STAMPED_BUNDLE}"
        COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" stamp
                --template "$<TARGET_FILE:AuralisVST3Native>"
                --output "${AURALIS_STAMPED_BINARY}"
                --module ${AURALIS_MODULE}
        COMMENT "Stamping the identity of ${AURALIS_MODULE} into the plug-in"
        VERBATIM)
endif()

# The core shared library travels with the plug-in, so the module can be
# loaded without a PATH that happens to contain it.  The interpreter the
# core is linked against travels with it as well: the core embeds CPython,
# and CPython looks for its standard library next to the library that pulls
# it in.
if (AURALIS_CORE_DLL AND EXISTS "${AURALIS_CORE_DLL}")
    get_filename_component(AURALIS_CORE_DIR "${AURALIS_CORE_DLL}" DIRECTORY)

    file(GLOB AURALIS_INTERPRETER "${AURALIS_CORE_DIR}/python3*.dll"
                                   "${AURALIS_CORE_DIR}/libpython3*.so"
                                   "${AURALIS_CORE_DIR}/libpython3*.dylib")

    add_custom_command(TARGET AuralisVST3Native POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${AURALIS_CORE_DLL}" "$<TARGET_FILE_DIR:AuralisVST3Native>"
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${AURALIS_STAMPED_BUNDLE}"
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${AURALIS_CORE_DLL}" "${AURALIS_STAMPED_BUNDLE}"
        COMMENT "Copying the core library next to the plug-in"
        VERBATIM)

    foreach (interpreter IN LISTS AURALIS_INTERPRETER)
        add_custom_command(TARGET AuralisVST3Native POST_BUILD
            COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                    "${interpreter}" "$<TARGET_FILE_DIR:AuralisVST3Native>"
            COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                    "${interpreter}" "${AURALIS_STAMPED_BUNDLE}"
            COMMENT "Copying the interpreter next to the plug-in"
            VERBATIM)
    endforeach()
endif()

# ---------------------------------------------------------------------------
#  running the checks
# ---------------------------------------------------------------------------

if (AURALIS_STAMP_IDENTITY)
    set(AURALIS_CHECK_TARGET "${AURALIS_STAMPED_BINARY}")
else()
    set(AURALIS_CHECK_TARGET "$<TARGET_FILE:AuralisVST3Native>")
endif()

add_custom_command(TARGET AuralisVST3Native POST_BUILD
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_CHECK_EXPORT_SCRIPT}" "${AURALIS_CHECK_TARGET}"
    COMMENT "Checking that the module exports GetPluginFactory"
    VERBATIM)

add_custom_command(TARGET AuralisVST3Native POST_BUILD
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_CHECK_STATE_SCRIPT}" "${AURALIS_CHECK_TARGET}"
    COMMENT "Checking the parameters and the state chunk"
    VERBATIM)

# ---------------------------------------------------------------------------
#  the install manifest
# ---------------------------------------------------------------------------
#
#  The manifest tool recognises a plug-in by its file name, and the only
#  suffix it knows is ".vst3".  A Windows VST3 bundle is a FOLDER named
#  "<name>.vst3" holding "<name>.vst3.dll", so the bundle is copied to a
#  flat file for the tool.  It lives in a sub directory of its own, so
#  that a host which scans the build output does not find the plug-in
#  twice.

set(AURALIS_FLAT_DIR "${AURALIS_STAMPED_DIR}/flat")

add_custom_target(auralis_vst3native_manifest
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${AURALIS_FLAT_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${AURALIS_STAMPED_BINARY}" "${AURALIS_FLAT_DIR}/${AURALIS_SAFE_FILE_NAME}"
    COMMAND "${Python3_EXECUTABLE}" "${AURALIS_IDENTITY_TOOL}" manifest
            --artefact "${AURALIS_FLAT_DIR}/${AURALIS_SAFE_FILE_NAME}"
            --out "${AURALIS_STAMPED_DIR}/install-manifest.json"
    DEPENDS AuralisVST3Native
    COMMENT "Writing the install manifest"
    VERBATIM)

