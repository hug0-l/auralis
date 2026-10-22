#!/usr/bin/env python3
"""
plugins/abi/interpreter_home.py - the home of the interpreter.

WHAT THIS IS ABOUT
------------------
The interpreter needs a standard library to start at all.  Without one it
stops with

    Fatal Python error: Failed to import encodings module

and it takes the host down with it: the host dies in the middle of the
exercise, without a report and without a word about the reason.  The
package therefore carries the home of the interpreter it ships, and the
core finds it next to itself - the runtime directory IS the home, the
standard library sits in it and the core looks for it there.

    Auralis Runtime/
        aurcore_abi.dll          the core
        python315.dll            the interpreter
        Lib/                     the standard library
            site-packages/numpy/ the packages the core imports
        DLLs/                    the extension modules

The home is taken from an installation of the interpreter.  Two places
need one:

* the build of the core (``plugins/abi/build_abi.py``), so that the
  library that comes out of the build can be loaded by a host that is not
  Python - the conformance tests do that;
* the staging of the family (``plugins/vst3native/stage_runtime.py``), so
  that the shared runtime of the plug-ins carries it.

The module is standard library only, and it never writes outside the
directory it is given.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: The packages the core imports.  The core is a Python program, so the
#: packages it needs travel with the standard library.
CORE_PACKAGES: tuple[str, ...] = ("numpy", "numpy.libs")

#: The directories of an installation that the home does not carry.  They
#: belong to the tools around the interpreter, not to the interpreter: the
#: editor, the turtle, the installer, the toolkit and the test suite.
HOME_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "site-packages",
        "idlelib",
        "turtledemo",
        "ensurepip",
        "tkinter",
        "venv",
        "test",
        "tests",
        "doc",
        "f2py",
        "distutils",
    }
)

#: The libraries of the extension modules that the home does not carry.
#: They belong to the parts of the standard library the core does not use:
#: the toolkit, the TLS library and the database.  Together they are 12 MB
#: of the 47 MB of the home.
HOME_SKIP_LIBRARIES: frozenset[str] = frozenset(
    {
        "tcl90.dll",
        "tcl9tk90.dll",
        "libcrypto-3.dll",
        "libssl-3.dll",
        "sqlite3.dll",
    }
)

#: The suffixes of the extension modules of the platform.
EXTENSION_SUFFIXES: tuple[str, ...] = (".pyd", ".so", ".dylib")


def interpreter_version(name: str) -> tuple[int, int] | None:
    """The version of an interpreter library, read out of its name.

    "python315.dll" is 3.15, "libpython3.15.so" is 3.15 and "python3.dll"
    is the stable ABI, which serves every 3.x and has no version of its
    own.
    """
    stem = name.lower()

    for prefix in ("libpython", "python"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    else:
        return None

    if stem.startswith("3."):
        digits = ""
        for char in stem[2:]:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            return None
        return (3, int(digits))

    if not stem.startswith("3"):
        return None

    # "python315.dll": the major version and the minor version, without a
    # dot between them.  "python3.dll" carries no minor version at all: it
    # is the stable ABI.
    digits = ""
    for char in stem[1:]:
        if not char.isdigit():
            break
        digits += char

    if not digits:
        return None

    return (3, int(digits))


def copy_if_different(source: Path, destination: Path) -> bool:
    """Copy a file unless the destination already is the same file.

    The time of the copy is kept, so a file that was copied before is
    recognised the next time and the file is not copied again.
    """
    try:
        current = destination.stat()
    except OSError:
        current = None

    if current is not None:
        try:
            original = source.stat()
        except OSError:
            original = None

        if (original is not None
                and current.st_size == original.st_size
                and int(current.st_mtime) == int(original.st_mtime)):
            return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def copy_tree(source: Path, destination: Path,
              skip_dirs: frozenset[str] = frozenset(),
              skip_files: frozenset[str] = frozenset()) -> tuple[int, int]:
    """Copy a directory, skipping what does not travel.

    Returns the number of files that were copied and their size.
    """
    files = 0
    total = 0

    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue

        relative = path.relative_to(source)
        if any(part in skip_dirs for part in relative.parts[:-1]):
            continue
        if path.name in skip_dirs or path.name in skip_files:
            continue

        if copy_if_different(path, destination / relative):
            files += 1
            total += path.stat().st_size

    return files, total


def copy_extension_modules(source: Path, destination: Path) -> tuple[int, int]:
    """Copy the extension modules of an installation.

    The modules themselves and the small libraries they need travel; the
    toolkit, the TLS library and the database do not: they belong to the
    parts of the standard library the core does not use.
    """
    files = 0
    total = 0

    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in EXTENSION_SUFFIXES:
            continue
        if path.name.lower() in HOME_SKIP_LIBRARIES:
            continue

        if copy_if_different(path, destination / path.name):
            files += 1
            total += path.stat().st_size

    return files, total


def layout(base_prefix: Path) -> tuple[Path, Path, Path, Path]:
    """Where an installation keeps its standard library and its modules.

    Windows keeps the standard library in "Lib" and the extension modules
    in "DLLs"; the other platforms keep both in "lib/python3.X".  The home
    is the directory the standard library is in, so the standard library
    of the installation, the path it takes inside a home, the extension
    modules of the installation and the path they take are returned.
    """
    base_prefix = Path(base_prefix)
    major, minor = sys.version_info.major, sys.version_info.minor

    if os.name == "nt":
        return (base_prefix / "Lib", Path("Lib"),
                base_prefix / "DLLs", Path("DLLs"))

    standard = base_prefix / "lib" / ("python%d.%d" % (major, minor))
    relative = Path("lib") / ("python%d.%d" % (major, minor))
    return (standard, relative, standard / "lib-dynload", relative / "lib-dynload")


def stage_home(target_dir: Path, source: Path | None = None,
               packages: tuple[str, ...] = CORE_PACKAGES) -> dict:
    """Put the home of the interpreter into a directory.

    The home is the standard library of the interpreter, the extension
    modules and the packages the core imports.  It is taken from the
    installation that is given, or from the installation of the
    interpreter that runs the caller.

    Returns a report: the source, the number of files, their size and the
    problems.
    """
    target_dir = Path(target_dir)
    report: dict = {
        "source": "",
        "files": 0,
        "bytes": 0,
        "problems": [],
    }

    home = Path(source) if source is not None else Path(sys.base_prefix)
    report["source"] = str(home)

    if not home.is_dir():
        report["problems"].append(
            "the installation of the interpreter was not found: %s" % home)
        return report

    standard, standard_home, modules, modules_home = layout(home)

    if not standard.is_dir():
        report["problems"].append(
            "the standard library was not found in the installation: %s" % standard)
        return report

    files = 0
    total = 0

    # The standard library.  It is the same in every home, and the packages
    # travel with it.
    copied, size = copy_tree(standard, target_dir / standard_home,
                             skip_dirs=HOME_SKIP_DIRS)
    files += copied
    total += size

    package_root = standard / "site-packages"
    for name in packages:
        source_package = package_root / name
        if not source_package.is_dir():
            report["problems"].append(
                "the package %s was not found in the installation: the core "
                "imports it and the plug-in cannot work without it" % name)
            continue
        copied, size = copy_tree(source_package,
                                 target_dir / standard_home / "site-packages" / name)
        files += copied
        total += size

    # The extension modules.  On Windows they live in a directory of their
    # own and only the modules and the small libraries they need travel.
    if modules.is_dir() and modules != standard:
        if os.name == "nt":
            copied, size = copy_extension_modules(modules, target_dir / modules_home)
        else:
            copied, size = copy_tree(modules, target_dir / modules_home)
        files += copied
        total += size

    report["files"] = files
    report["bytes"] = total
    return report
