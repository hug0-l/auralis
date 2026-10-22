#!/usr/bin/env python3
"""
plugins/vst3native/stage_runtime.py — the shared runtime of the VST3 family.

WHAT THIS IS ABOUT
------------------
The VST3 family is 48 plug-ins.  Every one of them is the same adapter with
another module stamped into it, and every one of them carries the same two
shared libraries:

    <name>.vst3.dll        the plug-in itself          ~163 kB
    aurcore_abi.dll        the core's C ABI             ~144 kB
    python315.dll          the interpreter the core     7.8 MB

The interpreter is the same file 48 times, so the family is 374 MB of which
95 % is one file, copied.  This module owns the arrangement that removes the
copy: **the runtime lives once, in a directory of its own, next to the
family**, and the bundles hold the plug-in alone.

    <family root>/
        Auralis Runtime/          the shared runtime, one copy
            aurcore_abi.dll
            python315.dll
        Auralis De-Click.vst3/    the plug-in, alone
            Auralis De-Click.vst3.dll

THE LOAD PATH
-------------
The Windows loader does **not** look in the directory of a loaded module for
that module's dependencies.  It looks in the directory the *host* gave it:
the bundle's own directory when the host loads the bundle with
LOAD_WITH_ALTERED_SEARCH_PATH (or with AddDllDirectory), the process
directory otherwise, and then the system directories and PATH.

Measured with scripts/vst3_loader_probe.cpp (see README.md):

    plain LoadLibrary of the bundle          fails, error 126
    LOAD_WITH_ALTERED_SEARCH_PATH            the bundle and its runtime
                                             are found, the search
                                             propagates down the chain
    runtime moved to a sibling directory     fails, error 126 - the
                                             altered path is the bundle's
                                             directory, not the runtime's

So a bundle that no longer carries the runtime can only be loaded if the
host is told where the runtime is, or if the plug-in loads it itself.  The
plug-in does the latter: the adapter's import of the core becomes a
delay-load descriptor, and the plug-in loads the core out of the runtime
directory with LOAD_WITH_ALTERED_SEARCH_PATH, which also makes the loader
find the interpreter next to it.  The patch for that is
``var/patches/a2_adapter.patch``.

WHAT THIS MODULE DOES
---------------------
It knows the layout, it moves the runtime out of the bundles, it puts it
back, and it checks the result.  The command line is
``scripts/vst3_runtime.py``.

The module is standard library only, and it never writes outside the
directories it is given.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
#  the layout
# ---------------------------------------------------------------------------

#: The directory that holds the shared runtime.  It is a sibling of the
#: bundles, so the whole family is one directory tree.
RUNTIME_DIR_NAME = "Auralis Runtime"

#: The manifest of the runtime directory.
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"

#: The manifest of the family (every bundle, and the runtime they share).
FAMILY_MANIFEST_NAME = "install-manifest.json"

#: The core's shared library.  It is the one the plug-in imports.
CORE_LIBRARY = "aurcore_abi.dll"

#: The interpreter the core is linked against.  The name carries the major
#: and the minor version, so the pattern matches every build of it.
INTERPRETER_PATTERN = re.compile(r"^python3\d*\.dll$", re.IGNORECASE)

#: The shared library suffixes a bundle may hold.
LIBRARY_SUFFIXES = (".dll", ".so", ".dylib")

#: The imports that the operating system provides.  Everything else a
#: bundle imports has to come with the bundle or with the runtime.
SYSTEM_PREFIXES = ("api-ms-win-", "ext-ms-win-")

SYSTEM_LIBRARIES = frozenset(
    {
        "advapi32.dll",
        "bcrypt.dll",
        "combase.dll",
        "gdi32.dll",
        "kernel32.dll",
        "kernelbase.dll",
        "msvcp140.dll",
        "msvcrt.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleaut32.dll",
        "shell32.dll",
        "user32.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "version.dll",
        "ws2_32.dll",
    }
)


# ---------------------------------------------------------------------------
#  a small PE reader
# ---------------------------------------------------------------------------
#
#  The reader answers one question: which shared libraries does this binary
#  import?  It is what makes the runtime's contents checkable instead of
#  guessed, and it keeps the staging honest: a file that the bundle does
#  not need does not go into the runtime, and a file the bundle needs but
#  the runtime does not hold is reported.

@dataclass(frozen=True)
class PeInfo:
    """What the reader found in a PE image."""

    imports: tuple[str, ...] = ()
    delay_imports: tuple[str, ...] = ()
    machine: int = 0
    image_base: int = 0


def _sections(data: bytes, pe_offset: int) -> list[tuple[int, int, int, int]]:
    """Return (virtual address, virtual size, raw offset, raw size) per section."""
    count = struct.unpack_from("<H", data, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    table = pe_offset + 24 + optional_size

    sections: list[tuple[int, int, int, int]] = []
    for index in range(count):
        base = table + 40 * index
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", data, base + 8
        )
        sections.append((virtual_address, virtual_size, raw_offset, raw_size))

    return sections


def _to_offset(rva: int, sections: Sequence[tuple[int, int, int, int]]) -> int | None:
    """Translate a relative virtual address into a file offset."""
    for virtual_address, virtual_size, raw_offset, raw_size in sections:
        if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
            return raw_offset + (rva - virtual_address)
    return None


def _import_names(data: bytes, directory: tuple[int, int],
                  sections: Sequence[tuple[int, int, int, int]]) -> tuple[str, ...]:
    """Read one import directory.

    The descriptor of a regular import is 20 bytes long and the name of
    the library is at offset 12 of it.
    """
    rva, size = directory
    if rva == 0 or size == 0:
        return ()

    offset = _to_offset(rva, sections)
    if offset is None:
        return ()

    names: list[str] = []
    cursor = offset

    while cursor + 20 <= len(data):
        entry = data[cursor:cursor + 20]
        name_rva = struct.unpack_from("<I", entry, 12)[0]
        if name_rva == 0:
            break

        name_offset = _to_offset(name_rva, sections)
        if name_offset is None:
            break

        end = data.find(b"\x00", name_offset)
        if end < 0:
            break

        names.append(data[name_offset:end].decode("ascii", "replace"))
        cursor += 20

    return tuple(names)


def _delay_import_names(data: bytes, directory: tuple[int, int],
                        sections: Sequence[tuple[int, int, int, int]],
                        image_base: int = 0) -> tuple[str, ...]:
    """Read one delay-import directory.

    THE DESCRIPTOR OF A DELAYED IMPORT IS NOT THE DESCRIPTOR OF A REGULAR
    IMPORT.  It is 32 bytes long, not 20, and the name of the library is
    at offset 4 of it, not at offset 12: offset 12 holds the address of
    the table of the imported names.  Reading a delayed import with the
    layout of a regular one therefore does not find a name but the first
    bytes of that table, and every delayed import is reported as a library
    called "?a??".

    The first field of the descriptor says whether the addresses in it are
    relative or absolute: the modern form (dlattrRva) is relative, the
    old one holds the address of the name.
    """
    rva, size = directory
    if rva == 0 or size == 0:
        return ()

    offset = _to_offset(rva, sections)
    if offset is None:
        return ()

    names: list[str] = []
    cursor = offset

    while cursor + 32 <= len(data):
        attributes = struct.unpack_from("<I", data, cursor)[0]
        name_field = struct.unpack_from("<I", data, cursor + 4)[0]
        if name_field == 0:
            break

        if not attributes & 1:
            # The old form: the addresses are absolute.
            if name_field < image_base:
                break
            name_field -= image_base

        name_offset = _to_offset(name_field, sections)
        if name_offset is None:
            break

        end = data.find(b"\x00", name_offset)
        if end < 0:
            break

        names.append(data[name_offset:end].decode("ascii", "replace"))
        cursor += 32

    return tuple(names)


def pe_info(path: Path) -> PeInfo:
    """Read the import tables of a PE image.

    A file that is not a PE image (the other platforms) gives an empty
    result rather than an error: the staging then falls back to the file
    names.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return PeInfo()

    if len(data) < 0x40 or data[:2] != b"MZ":
        return PeInfo()

    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        return PeInfo()

    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    optional = pe_offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]

    if magic == 0x20B:
        directory_base = optional + 112
        image_base = struct.unpack_from("<Q", data, optional + 24)[0]
    elif magic == 0x10B:
        directory_base = optional + 96
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
    else:
        return PeInfo(machine=machine)

    sections = _sections(data, pe_offset)

    if directory_base + 16 * 2 > len(data):
        return PeInfo(machine=machine, image_base=image_base)

    imports = _import_names(data, struct.unpack_from("<II", data, directory_base + 8), sections)
    delay = _delay_import_names(
        data,
        struct.unpack_from("<II", data, directory_base + 8 * 13),
        sections,
        image_base,
    )

    return PeInfo(imports=imports, delay_imports=delay, machine=machine,
                  image_base=image_base)


def is_system_library(name: str) -> bool:
    """True when the operating system provides this library."""
    lowered = name.lower()
    return lowered.startswith(SYSTEM_PREFIXES) or lowered in SYSTEM_LIBRARIES


# ---------------------------------------------------------------------------
#  the interpreter's home
# ---------------------------------------------------------------------------
#
#  The interpreter needs a standard library to start at all.  Without one it
#  stops with "Failed to import encodings module", takes the host down with
#  it and says nothing about the reason.  The package therefore carries the
#  home of the interpreter it ships, and the core finds it next to itself:
#  the runtime directory IS the home, the standard library sits in it and
#  the core looks for it there.
#
#      Auralis Runtime/
#          aurcore_abi.dll          the core
#          python315.dll            the interpreter
#          Lib/                     the standard library
#              site-packages/numpy/ the packages the core imports
#          DLLs/                    the extension modules
#
#  The tool that copies the home is the one that owns the layout of the
#  runtime, and the build of the core uses it as well, so the library that
#  comes out of the build carries a home too.

#: The tool that copies the home of the interpreter into a directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "abi"))
import interpreter_home  # noqa: E402  (the module lives in the core's directory)


def runtime_interpreter(runtime_dir: Path) -> Path | None:
    """The interpreter library the runtime directory carries."""
    runtime_dir = Path(runtime_dir)
    if not runtime_dir.is_dir():
        return None

    for path in sorted(runtime_dir.iterdir()):
        if path.is_file() and INTERPRETER_PATTERN.match(path.name):
            return path

    return None


def home_is_usable(runtime_dir: Path) -> bool:
    """True when the runtime directory carries the home of its interpreter.

    The interpreter needs a standard library to start at all, and the home
    of the one the runtime carries is the standard library that sits next
    to it.  A runtime without it cannot start the interpreter, and the
    plug-in that uses it passes the audio through.
    """
    runtime_dir = Path(runtime_dir)
    _, relative, _, _ = interpreter_home.layout(runtime_dir)
    return (runtime_dir / relative / "os.py").is_file()


def stage_interpreter_home(runtime_dir: Path, source: Path | None = None) -> dict:
    """Put the home of the interpreter into the runtime directory.

    The home is the standard library of the interpreter the runtime
    directory carries, the extension modules and the packages the core
    imports.  It is taken from the installation of the interpreter that
    runs the staging, and it is refused when that installation is not the
    one the runtime carries: the standard library of one version is not the
    standard library of another.

    Returns a report: the source, the number of files, their size and the
    problems.
    """
    runtime_dir = Path(runtime_dir)
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

    interpreter = runtime_interpreter(runtime_dir)
    if interpreter is not None:
        wanted = interpreter_home.interpreter_version(interpreter.name)
        running = (sys.version_info.major, sys.version_info.minor)
        if wanted is not None and wanted != running:
            report["problems"].append(
                "the runtime carries %s but the staging runs under Python %d.%d: "
                "the standard library would not match the interpreter"
                % (interpreter.name, running[0], running[1]))
            return report

    result = interpreter_home.stage_home(runtime_dir, home)
    report.update(result)
    return report


# ---------------------------------------------------------------------------
#  the bundles
# ---------------------------------------------------------------------------

def is_bundle(path: Path) -> bool:
    """True when the path is a VST3 bundle that holds a shared library."""
    if not path.is_dir() or path.suffix.lower() != ".vst3":
        return False
    return bundle_binary(path) is not None


def bundle_binary(bundle: Path) -> Path | None:
    """Return the plug-in inside a bundle.

    The plug-in is the shared library that nothing else in the bundle
    imports: the root of the bundle's import graph.  That is the plug-in
    even when the file is named in an unexpected way.
    """
    bundle = Path(bundle)
    libraries = [path for path in sorted(bundle.rglob("*"))
                 if path.is_file() and path.suffix.lower() in LIBRARY_SUFFIXES]

    if not libraries:
        return None

    imported: set[str] = set()
    for path in libraries:
        for name in pe_info(path).imports:
            imported.add(name.lower())

    roots = [path for path in libraries if path.name.lower() not in imported]

    if roots:
        return roots[0]

    return libraries[0]


def bundle_size(bundle: Path) -> int:
    """The size of a bundle on disk, in bytes."""
    bundle = Path(bundle)
    return sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())


def runtime_members(bundle: Path) -> list[Path]:
    """Return the files of a bundle that belong to the shared runtime.

    That is every shared library in the bundle except the plug-in itself.
    """
    bundle = Path(bundle)
    plug_in = bundle_binary(bundle)

    members = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_SUFFIXES:
            continue
        if plug_in is not None and path == plug_in:
            continue
        members.append(path)

    return members


def find_bundles(root: Path) -> list[Path]:
    """Every VST3 bundle under a directory."""
    root = Path(root)
    if not root.is_dir():
        return []

    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if is_bundle(path):
            found.append(path)

    return found


def family_size(roots: Iterable[Path]) -> dict:
    """The size of a family: the bundles, and the runtime they share."""
    bundles: list[Path] = []
    for root in roots:
        bundles.extend(find_bundles(root))

    total = 0
    per_bundle = []
    for bundle in bundles:
        size = bundle_size(bundle)
        total += size
        per_bundle.append({"name": bundle.name, "bytes": size})

    return {
        "bundles": len(bundles),
        "bytes": total,
        "mib": round(total / (1024 * 1024), 2),
        "per_bundle": per_bundle,
    }


# ---------------------------------------------------------------------------
#  where the runtime is
# ---------------------------------------------------------------------------

def family_root(roots: Sequence[Path]) -> Path:
    """The directory that holds the bundles.

    When the bundles sit in one directory (the release layout) that is the
    directory itself; when they sit in per-module build directories (the
    build layout) it is the directory that holds them.
    """
    roots = [Path(root) for root in roots]
    if not roots:
        raise ValueError("no family root was given")

    parents: list[Path] = []
    for root in roots:
        for bundle in find_bundles(root):
            parents.append(bundle.parent)

    if not parents:
        return roots[0]

    common = parents[0]
    for parent in parents[1:]:
        try:
            common = Path(os.path.commonpath([str(common), str(parent)]))
        except ValueError:
            # Two roots on different drives: the first one wins.
            break

    return common


def default_runtime_dir(roots: Sequence[Path]) -> Path:
    """Where the runtime goes when no directory is given.

    The runtime is a sibling of the bundles: a directory of its own, next
    to them.
    """
    return family_root(roots) / RUNTIME_DIR_NAME


def discover_runtime(bundle: Path, runtime_dir: Path | None = None) -> Path | None:
    """Find the runtime that belongs to a bundle.

    The rule is the one the plug-in uses:

    1. the directory that is given, if there is one;
    2. a directory called ``Auralis Runtime`` next to the bundle;
    3. one in the bundle's parent directory (the build layout, where the
       bundle sits in ``<module>/stamped/``);
    4. the bundle's own directory (the old layout, where the runtime is
       still carried inside the bundle).
    """
    bundle = Path(bundle)

    if runtime_dir is not None:
        candidate = Path(runtime_dir)
        return candidate if candidate.is_dir() else None

    own = bundle.parent
    for base in (own, own.parent, own.parent.parent):
        candidate = base / RUNTIME_DIR_NAME
        if candidate.is_dir():
            return candidate

    # The old layout: the bundle still carries the runtime itself.
    if (bundle / CORE_LIBRARY).is_file():
        return bundle

    return None


# ---------------------------------------------------------------------------
#  the staging
# ---------------------------------------------------------------------------

@dataclass
class StagingReport:
    """What a staging run did."""

    runtime_dir: Path
    moved: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    already_shared: list[dict] = field(default_factory=list)
    replaced: list[dict] = field(default_factory=list)
    home: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def moved_bytes(self) -> int:
        return sum(item["bytes"] for item in self.moved)

    def to_dict(self) -> dict:
        return {
            "runtime_dir": str(self.runtime_dir),
            "dry_run": self.dry_run,
            "moved": self.moved,
            "removed": self.removed,
            "already_shared": self.already_shared,
            "replaced": self.replaced,
            "home": self.home,
            "problems": self.problems,
        }


def file_hash(path: Path) -> str:
    """The SHA-256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(roots: Sequence[Path], runtime_dir: Path | None = None,
          dry_run: bool = False) -> StagingReport:
    """Move the runtime out of the bundles into one shared directory.

    Every bundle gives up its copy of the core and of the interpreter; the
    runtime directory keeps one copy of each.  The copies are compared
    before they are moved, so a family whose bundles do not agree is
    reported instead of silently deduplicated.

    The copy the runtime directory already holds is compared as well: a
    runtime directory that holds a copy of another build is stale, and the
    copy the bundles carry is the one the plug-ins were built against.  The
    stale copy is therefore replaced, and the replacement is reported.
    """
    bundles: list[Path] = []
    for root in roots:
        bundles.extend(find_bundles(root))

    target = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir(roots)
    report = StagingReport(runtime_dir=target, dry_run=dry_run)

    if not bundles:
        report.problems.append("no VST3 bundle was found")
        return report

    # The runtime keeps one copy of every file.  The first bundle that
    # carries a file decides what the copy looks like; a bundle whose copy
    # differs is reported, because the family has to agree.
    kept: dict[str, str] = {}
    seen: dict[str, set[str]] = {}

    for bundle in bundles:
        for member in runtime_members(bundle):
            name = member.name
            digest = file_hash(member)
            size = member.stat().st_size

            seen.setdefault(name, set()).add(digest)

            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)
                destination = target / name
                if name not in kept:
                    if destination.exists():
                        existing = file_hash(destination)
                        if existing != digest:
                            # The runtime directory holds a copy of another
                            # build.  The copy of the bundle is the one the
                            # plug-ins were built against, so it wins.
                            shutil.copy2(member, destination)
                            report.replaced.append({
                                "file": name,
                                "bytes": size,
                                "sha256": digest,
                                "was": existing,
                            })
                    else:
                        shutil.copy2(member, destination)
                    kept[name] = file_hash(destination)
                member.unlink()

            report.moved.append({
                "bundle": bundle.name,
                "file": name,
                "bytes": size,
                "sha256": digest,
            })

        if not runtime_members(bundle):
            report.already_shared.append({"bundle": bundle.name})

    for name, digests in sorted(seen.items()):
        if len(digests) > 1:
            report.problems.append(
                "the bundles do not agree about %s (%d different copies) - "
                "rebuild the family before it is staged" % (name, len(digests))
            )

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        report.home = stage_interpreter_home(target)

    return report


def restore(roots: Sequence[Path], runtime_dir: Path) -> StagingReport:
    """Put the runtime back into every bundle.

    The build writes the runtime into every bundle again, so this is what
    undoes a staging run when the family has to be built or tested as it
    was.
    """
    bundles: list[Path] = []
    for root in roots:
        bundles.extend(find_bundles(root))

    source = Path(runtime_dir)
    report = StagingReport(runtime_dir=source)

    if not source.is_dir():
        report.problems.append("the runtime directory does not exist: %s" % source)
        return report

    for bundle in bundles:
        for member in sorted(source.iterdir()):
            if not member.is_file() or member.suffix.lower() not in LIBRARY_SUFFIXES:
                continue
            destination = bundle / member.name
            if destination.exists():
                continue
            shutil.copy2(member, destination)
            report.moved.append({
                "bundle": bundle.name,
                "file": member.name,
                "bytes": member.stat().st_size,
                "sha256": file_hash(member),
            })

    # The undo is complete when every bundle carries the runtime again: the
    # shared directory has nothing left to share.
    shared = [path for path in sorted(source.iterdir())
              if path.is_file() and path.suffix.lower() in LIBRARY_SUFFIXES]

    if shared and bundles:
        complete = all((bundle / member.name).is_file()
                       for bundle in bundles for member in shared)

        if complete:
            for member in shared:
                member.unlink()

            manifest = source / RUNTIME_MANIFEST_NAME
            if manifest.is_file():
                manifest.unlink()

            try:
                source.rmdir()
                report.removed.append({"runtime_dir": source.name})
            except OSError:
                # The directory holds something else; it stays.
                pass

    return report


def verify(roots: Sequence[Path], runtime_dir: Path | None = None) -> list[str]:
    """Check that every bundle can be loaded with the runtime it is given.

    A bundle is complete when the plug-in's imports are satisfied: the
    system libraries, the files the bundle carries itself, and the files
    the shared runtime carries.  What is left over is a finding.
    """
    problems: list[str] = []

    bundles: list[Path] = []
    for root in roots:
        bundles.extend(find_bundles(root))

    if not bundles:
        return ["no VST3 bundle was found"]

    for bundle in bundles:
        plug_in = bundle_binary(bundle)
        if plug_in is None:
            problems.append("%s: it holds no shared library" % bundle.name)
            continue

        runtime = discover_runtime(bundle, runtime_dir)
        if runtime is None:
            problems.append("%s: no runtime was found" % bundle.name)
            continue

        available = {path.name.lower() for path in bundle.rglob("*") if path.is_file()}
        available |= {path.name.lower() for path in runtime.rglob("*") if path.is_file()}

        for name in pe_info(plug_in).imports:
            if is_system_library(name):
                continue
            if name.lower() in available:
                continue
            problems.append("%s: %s is neither in the bundle nor in the runtime"
                            % (bundle.name, name))

        for name in pe_info(plug_in).delay_imports:
            if is_system_library(name):
                continue
            if name.lower() in available:
                continue
            problems.append("%s: the delayed import %s is neither in the bundle "
                            "nor in the runtime" % (bundle.name, name))

    return problems


def write_runtime_manifest(runtime_dir: Path, out_path: Path | None = None) -> Path:
    """Write the manifest of the runtime directory."""
    runtime_dir = Path(runtime_dir)
    files = []

    for path in sorted(runtime_dir.rglob("*")):
        if not path.is_file() or path.name == RUNTIME_MANIFEST_NAME:
            continue
        files.append({
            "name": path.name,
            "path": path.relative_to(runtime_dir).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": file_hash(path),
        })

    document = {
        "do not edit": "generated by plugins/vst3native/make_manifests.py",
        "kind": "auralis.vst3.runtime",
        "version": 1,
        "runtime_dir": runtime_dir.name,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }

    target = Path(out_path) if out_path is not None else runtime_dir / RUNTIME_MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    return target
