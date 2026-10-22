#!/usr/bin/env python3
"""
scripts/build_release.py - turn the repository into something a user can install.

The builder is the gate between the source tree and a shipped package.  It

  1. runs the check suite - the schema validator, the CSS coverage check and
     the plug-in ABI conformance tests - and refuses to build when a required
     check fails;
  2. checks the plug-in family: every module of the canonical registry must
     have exactly one bundle, and every bundle must carry a stamped identity.
     A module that is missing and a plug-in that is not stamped stop the
     build;
  3. assembles a release tree (the application, the core, the preset library,
     the resources, the help, the core C ABI library, the built plug-in
     bundles and the verifier the user needs);
  4. writes a manifest that describes the package completely: every file with
     its size and its SHA-256, the version, the build, the modules that are
     included, the plug-in family with the identifiers of every module, the
     size breakdown and the command that verifies the package;
  5. writes the checksum list (``SHA256SUMS``) and a zip;
  6. invokes the Inno Setup compiler when it is available;
  7. prints the size budget report.

The package is reproducible: the same tree gives the same package.  The
timestamps are the only thing that moves, and they can be pinned with
``SOURCE_DATE_EPOCH``.

Nothing here needs the network, and nothing here needs Inno Setup: the
installer is only compiled when ``iscc`` can be found.

Usage::

    python scripts/build_release.py                       # full + slim
    python scripts/build_release.py --profile full
    python scripts/build_release.py --skip-checks         # no gate
    python scripts/build_release.py --iscc "C:\\...\\ISCC.exe"
    python scripts/build_release.py --plugins-dir build/vst3
    python scripts/build_release.py --verify dist/full    # check a package

The module is importable: the packaging tests use the pieces directly.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The Inno Setup script that describes the installation.
INSTALLER_SCRIPT = ROOT / "packaging" / "installer.iss"

#: The manifest format written next to every release tree.
MANIFEST_FORMAT = "auralis.release-manifest"
MANIFEST_VERSION = 2

#: The name of the manifest inside a release tree.
MANIFEST_NAME = "manifest.json"

#: The name of the checksum list inside a release tree.  It is the file the
#: standard checksum tool reads, so a user can check a package without any
#: tool of ours.
SHA256SUMS_NAME = "SHA256SUMS"

#: The product name used in the manifest.
PRODUCT = "Auralis"

#: The size budget of the two packages, in bytes.
SIZE_BUDGETS = {
    "full": 700 * 1024 * 1024,
    "slim": 145 * 1024 * 1024,
}

#: The directories that make up the application itself.
#: ``aurmcp`` is the MCP server (an AI agent drives the product through it)
#: and ``models`` is the library of learned models — both are part of the
#: product, so both travel with it.
APP_TREES = ("aurui", "aurcore", "aurserver", "aurmcp", "models", "assets", "docs")

#: The tools that travel with the package.  The user needs the verifier to be
#: able to check the package, so the verifier is part of the package.
TOOL_FILES = (("scripts/build_release.py", "scripts/build_release.py"),)

#: The categories the size report breaks a package down into.  Every file of
#: the tree belongs to exactly one of them; the rest is reported as "other".
#: The list is filled in below, when the directories it names are known.
SIZE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = ()

#: Never copied into a release tree.
EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
    }
)
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pdb", ".ilk", ".exp", ".log", ".err")
EXCLUDE_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

#: The runtime scratch that is never shipped.
EXCLUDE_RELATIVE = (
    "var/uploads",
    "var/assets",
    "var/presets/Presets",
    "var/live.log",
    "var/live.err",
)

#: The plug-in bundle suffixes the builder knows about.
PLUGIN_SUFFIXES = (".vst3", ".aaxplugin", ".bundle")

#: The suffixes of a file that can carry the identity of a plug-in.
PLUGIN_BINARY_SUFFIXES = (".vst3", ".so", ".dylib", ".exe", ".bundle", ".aaxplugin", ".dll")

#: The directories that never hold a plug-in bundle and are not worth
#: walking: they are the noise of a build tree.
PLUGIN_SCAN_SKIP = frozenset(
    {
        "CMakeFiles",
        "JuceLibraryCode",
        "CMakeTmp",
        "Testing",
        "__pycache__",
        ".git",
    }
)

#: How deep the search for a bundle goes.  A bundle lives in the output
#: directory of a build, three or four levels below the root of the tree.
PLUGIN_SCAN_DEPTH = 6

#: Where the built plug-in bundles are looked for, in order.  Every entry is a
#: glob pattern relative to the repository root, and every directory a pattern
#: matches is searched for plug-in modules:
#:
#:   plugins/vst3native/build*  the native plug-in build
#:   build/vst3native            the native plug-in build (the canonical one)
#:   plugins/vst3/build*         the stamped output of the plug-in build
#:   build/vst3*                 the CMake build tree of the plug-in adapters
#:   build/stamped               the stamping output written next to the build
#:   dist/plugins                a bundle directory prepared by hand
#:
#: The order matters: the first directory that holds a bundle for a module
#: wins, so the canonical build is searched before the older build trees that
#: may still be lying around.
PLUGIN_SEARCH_GLOBS = (
    "plugins/vst3native/build*",
    "build/vst3native",
    "plugins/vst3/build*",
    "build/vst3*",
    "build/stamped",
    "dist/plugins",
)

#: Where the plug-in bundles are staged inside a release tree.  The path
#: mirrors the repository, so the installer script is compiled from the
#: repository or from a release tree with the same rules.
PLUGIN_STAGE_DIR = "plugins/vst3/build/stamped"

#: Where the core C ABI library is staged inside a release tree.  It sits
#: next to the application rather than inside the plug-in layer.
ABI_STAGE_DIR = "lib"

#: The directory that holds the shared runtime of the plug-in family.  The
#: family shares one copy of the core and of the interpreter instead of
#: carrying them in every bundle: the name is the one the plug-in looks
#: for, so the directory has to keep it.
RUNTIME_DIR_NAME = "Auralis Runtime"

#: The suffixes of a shared library.  A bundle holds the plug-in and the
#: runtime; the runtime is every shared library of the bundle except the
#: plug-in itself.
SHARED_LIBRARY_SUFFIXES = (".dll", ".so", ".dylib")

#: The categories the size report breaks a package down into.  Every file of
#: the tree belongs to exactly one of them; the rest is reported as "other".
SIZE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("application", ("aurui", "aurcore", "aurserver", "aurmcp")),
    ("models", ("models",)),
    ("assets", ("assets",)),
    ("documentation", ("docs",)),
    ("presets", ("var/presets",)),
    ("launchers", ("bin",)),
    ("runtime", (f"{PLUGIN_STAGE_DIR}/{RUNTIME_DIR_NAME}",)),
    ("plug-ins", ("plugins",)),
    ("core library", ("lib",)),
    ("tools", ("scripts",)),
)

#: The files that travel with the bundles: the install manifest and the
#: identity twins the stamping step writes.
PLUGIN_SIDECAR_GLOBS = ("install-manifest.json", "*.identity.json")

#: The check suite that gates the build.
CHECK_COMMANDS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("schema", ("tools/validate_schema.py",), True),
    ("css-coverage", ("qa/check_css_coverage.py",), True),
    # The base temp directory is given because pytest removes its own
    # numbered directories at the end of a session, and on this platform the
    # removal of the "current" link can fail with a permission error.  The
    # tests themselves are not affected.
    ("plugin-abi", ("-m", "pytest", "tests/test_plugin_abi.py", "-q",
                    "--basetemp=var/pytest-abi-tmp"), True),
)

#: The name of the plug-in family gate.  It is not one of the subprocess
#: checks: the family is read from the build trees, so the check runs in
#: this process.  It is part of the gate and ``--skip-checks`` skips it with
#: the rest.
PLUGIN_GATE_NAME = "plug-in-family"


# ---------------------------------------------------------------------------
#  The Inno Setup script parser
# ---------------------------------------------------------------------------
@dataclass
class IssEntry:
    """One entry of an Inno Setup section."""

    section: str
    params: dict[str, str]
    raw: str

    def get(self, key: str, default: str = "") -> str:
        """Return a parameter value, or ``default`` when it is not there."""
        return self.params.get(key.lower(), default)

    def flags(self) -> set[str]:
        """Return the flag set of the entry, lower cased."""
        text = self.get("flags")
        return {flag.strip().lower() for flag in text.split() if flag.strip()}


@dataclass
class IssScript:
    """A parsed Inno Setup script."""

    path: Path
    sections: list[str] = field(default_factory=list)
    entries: list[IssEntry] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    raw_code: str = ""

    def section(self, name: str) -> list[IssEntry]:
        """Return the entries of one section."""
        wanted = name.lower()
        return [entry for entry in self.entries if entry.section.lower() == wanted]

    def has_section(self, name: str) -> bool:
        """Is the section present?"""
        wanted = name.lower()
        return any(section.lower() == wanted for section in self.sections)

    def code(self) -> str:
        """Return the raw text of the ``[Code]`` section."""
        return self.raw_code


def split_parameters(text: str) -> list[str]:
    """Split an Inno Setup entry into its parameters.

    A semicolon separates the parameters, unless it sits inside a quoted
    string.  A doubled quote inside a quoted string is an escaped quote.
    """
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                current.append('""')
                index += 2
                continue
            quoted = not quoted
            current.append(char)
        elif char == ";" and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def parse_parameters(text: str, section: str = "") -> dict[str, str]:
    """Parse one entry into its parameters.

    The ``[Setup]`` section uses ``Key=Value``.  Every other section uses
    ``Name: Value;Name: Value`` - and only the first colon of a parameter is
    the separator, so a value may contain one.
    """
    params: dict[str, str] = {}
    if section.lower() == "setup":
        key, separator, value = text.partition("=")
        if separator:
            params[key.strip().lower()] = value.strip()
        return params

    for part in split_parameters(text):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        params[key.strip().lower()] = value.strip()
    return params


def _strip_quotes(value: str) -> str:
    """Remove one layer of surrounding double quotes."""
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _condition_holds(expression: str, defines: dict[str, str]) -> bool:
    """Evaluate the small subset of ISPP expressions this script uses."""
    text = expression.strip()
    if not text:
        return False
    if text == "1":
        return True
    if text == "0":
        return False
    if text.startswith("defined(") and text.endswith(")"):
        return text[7:-1].strip() in defines
    if text.startswith("not defined(") and text.endswith(")"):
        return text[11:-1].strip() not in defines
    return text in defines


def parse_iss(path: Path, defines: dict[str, str] | None = None) -> IssScript:
    """Parse an Inno Setup script into sections, entries and symbols.

    The parser understands the subset the Auralis script uses: ``#define``,
    ``#ifdef``, ``#ifndef``, ``#if``, ``#elif``, ``#else``, ``#endif``, section
    headers, ``;`` comments and ``{ }`` comment blocks.  It never needs Inno
    Setup to be installed.

    ``defines`` seeds the symbol table, which is how the machine-wide variant
    of the script is parsed without a second file.
    """
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    script = IssScript(path=path)
    script.defines.update(defines or {})
    script.raw_code = ""  # type: ignore[attr-defined]

    section = ""
    # Each frame is (parent_active, active, taken, condition).
    stack: list[tuple[bool, bool, bool, bool]] = []
    code_lines: list[str] = []
    in_code = False

    def active() -> bool:
        """True when the current line is inside an active branch."""
        return all(frame[1] for frame in stack)

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if in_code:
            # The [Code] section is always the last one: everything that
            # follows belongs to it.
            code_lines.append(raw_line)
            continue

        if not line or line.startswith(";"):
            continue

        if line.startswith("#"):
            directive, _, argument = line[1:].partition(" ")
            directive = directive.strip().lower()
            argument = argument.strip()
            if directive == "ifdef":
                condition = argument in script.defines
                parent = active()
                stack.append((parent, parent and condition, parent and condition, condition))
            elif directive == "ifndef":
                condition = argument not in script.defines
                parent = active()
                stack.append((parent, parent and condition, parent and condition, condition))
            elif directive == "if":
                condition = _condition_holds(argument, script.defines)
                parent = active()
                stack.append((parent, parent and condition, parent and condition, condition))
            elif directive == "elif":
                if not stack:
                    continue
                parent, _, taken, _ = stack[-1]
                condition = _condition_holds(argument, script.defines)
                stack[-1] = (
                    parent,
                    parent and not taken and condition,
                    taken or condition,
                    condition,
                )
            elif directive == "else":
                if not stack:
                    continue
                parent, _, taken, _ = stack[-1]
                stack[-1] = (parent, parent and not taken, True, not taken)
            elif directive == "endif":
                if stack:
                    stack.pop()
            elif directive == "define" and active():
                name, _, value = argument.partition(" ")
                script.defines[name.strip()] = value.strip().strip('"')
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section.lower() == "code":
                in_code = True
            if not script.has_section(section):
                script.sections.append(section)
            continue

        if not section:
            # An entry outside any section: the script is malformed.
            continue

        if not active():
            continue

        script.entries.append(
            IssEntry(section=section, params=parse_parameters(line, section), raw=line)
        )

    script.raw_code = "\n".join(code_lines)  # type: ignore[attr-defined]
    return script


def expand_iss_text(value: str, script: IssScript) -> str:
    """Expand the preprocessor symbols of a piece of script text.

    ``{#Symbol}`` is replaced by the symbol's value and one layer of
    surrounding quotes is removed.
    """
    text = value
    for name, replacement in script.defines.items():
        text = text.replace("{#" + name + "}", replacement)
    return _strip_quotes(text)


def resolve_iss_path(value: str, script: IssScript) -> Path:
    """Resolve a path written in an Inno Setup script.

    ``{#Symbol}`` is replaced by the symbol's value, ``{src}`` by the
    directory of the script, and a relative path is resolved against that
    directory.  A trailing wildcard is removed, so the result is the path the
    entry reads from.
    """
    text = value
    for name, replacement in script.defines.items():
        text = text.replace("{#" + name + "}", replacement)
    text = text.replace("{src}", str(script.path.parent))
    text = _strip_quotes(text)
    index = max(text.rfind("\\"), text.rfind("/"))
    if index >= 0 and "*" in text[index + 1 :]:
        # A wildcard on the last component: the path is the directory above it.
        text = text[:index]
    else:
        text = text.replace("*", "")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = script.path.parent / candidate
    return Path(os.path.normpath(str(candidate)))


# ---------------------------------------------------------------------------
#  The check suite
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """The outcome of one check."""

    name: str
    ok: bool
    skipped: bool = False
    required: bool = True
    detail: str = ""
    output: str = ""

    @property
    def verdict(self) -> str:
        """A one word verdict for the report."""
        if self.skipped:
            return "SKIP"
        return "PASS" if self.ok else "FAIL"


def run_check(
    name: str,
    argv: Sequence[str],
    cwd: Path,
    *,
    required: bool = True,
    timeout: float | None = 900.0,
) -> CheckResult:
    """Run one check and capture its outcome.

    The check runs in its own process, so a crash is a failure rather than a
    broken build script.
    """
    command = [sys.executable, *argv]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, False, required, f"timed out after {timeout}s")
    except OSError as exc:
        return CheckResult(name, False, False, required, f"could not run: {exc}")

    output = (completed.stdout or "") + (completed.stderr or "")
    tail = "\n".join(line for line in output.splitlines() if line.strip())[-4000:]
    skipped = completed.returncode == 0 and "skipped" in output.lower()
    detail = f"exit code {completed.returncode}"
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        skipped=skipped and completed.returncode == 0,
        required=required,
        detail=detail,
        output=tail,
    )


def run_checks(
    root: Path = ROOT,
    commands: Iterable[tuple[str, tuple[str, ...], bool]] | None = None,
) -> list[CheckResult]:
    """Run the check suite that gates the build."""
    results: list[CheckResult] = []
    for name, argv, required in commands or CHECK_COMMANDS:
        results.append(run_check(name, argv, root, required=required))
    return results


def check_gate(results: Sequence[CheckResult]) -> tuple[bool, list[CheckResult]]:
    """Return whether the build may continue, and the failures that stopped it."""
    failures = [result for result in results if result.required and not result.ok]
    return (not failures), failures


# ---------------------------------------------------------------------------
#  The release tree
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReleaseProfile:
    """One shippable profile."""

    name: str
    include_plugins: bool
    include_abi: bool

    @property
    def budget(self) -> int:
        """The size budget of the profile, in bytes."""
        return SIZE_BUDGETS[self.name]


PROFILES: dict[str, ReleaseProfile] = {
    "full": ReleaseProfile("full", include_plugins=True, include_abi=True),
    "slim": ReleaseProfile("slim", include_plugins=False, include_abi=False),
}


def _is_excluded(path: Path, root: Path) -> bool:
    """Is the path excluded from a release tree?"""
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    parts = relative.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    text = relative.as_posix()
    return any(text == item or text.startswith(item + "/") for item in EXCLUDE_RELATIVE)


#: The epoch every file of a release tree carries when the build does not
#: pin one: the first moment a zip can hold.
ZIP_EPOCH = 315532800  # 1980-01-01T00:00:00Z


def source_date_epoch() -> int | None:
    """Return the epoch the build is pinned to, when there is one."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        return None
    try:
        return int(epoch)
    except ValueError:
        return None


def normalise_timestamps(tree: Path, epoch: int | None = None) -> None:
    """Give every file of the tree the same timestamp.

    The build is reproducible when the tree is: the same sources give the
    same files with the same timestamps.  ``SOURCE_DATE_EPOCH`` pins the
    moment, and without it the first moment a zip can hold is used.
    """
    moment = epoch if epoch is not None else (source_date_epoch() or ZIP_EPOCH)
    for path in sorted(Path(tree).rglob("*")):
        try:
            os.utime(path, (moment, moment))
        except OSError:
            continue


def copy_tree(source: Path, destination: Path, root: Path) -> int:
    """Copy a directory into the release tree, skipping the excluded files.

    Returns the number of files copied.
    """
    if not source.is_dir():
        return 0
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if _is_excluded(path, root):
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def find_preset_library(root: Path) -> Path | None:
    """Return the preset library of the repository, when it exists.

    The library of the working tree is a run-time artefact of the machine
    the build ran on.  It is not what the package ships: the package
    carries the library of the build, which is written out of the
    canonical definitions (see :func:`ensure_presets`).
    """
    candidate = root / "var" / "presets" / "builtin"
    if candidate.is_dir() and any(candidate.rglob("*.json")):
        return candidate
    return None


def ensure_presets(root: Path, destination: Path) -> list[str]:
    """Make sure the release tree carries the shipped preset library.

    The library is written out of the canonical definitions in
    ``aurserver.presets`` and not copied from the working tree: the copy
    in the working tree belongs to the machine the build ran on and can be
    out of step with the build.  The plug-in publishes the presets it
    finds and matches them on the canonical parameter names, so a stale
    file would be published as a program the module cannot load, and a
    module without a preset would show an empty program list to the host.

    The index of the library is *not* shipped: the index is written by the
    preset manager from the files it finds on disk, it names the presets of
    the machine the build ran on, and it carries the moment it was written.
    Shipping it would make the package describe files it does not carry and
    would make two builds of the same tree differ.
    """
    target = destination / "var" / "presets" / "builtin"

    try:
        from aurserver.presets import seed_builtin_presets
    except Exception as exc:  # noqa: BLE001 - the build must not crash on this
        print(f"warning: the preset library could not be generated: {exc}")
        source = find_preset_library(root)
        if source is None:
            return []
        copy_tree(source, target, root)
        return sorted(
            str(path.relative_to(destination)) for path in target.rglob("*.json")
        )

    written = seed_builtin_presets(destination / "var" / "presets")

    # the index names the machine the build ran on, so it does not travel
    index = destination / "var" / "presets" / "index.json"
    if index.exists():
        index.unlink()

    return [str(Path(path).relative_to(destination)) for path in written]


def verify_presets(destination: Path) -> list[str]:
    """Report what is wrong with the preset library of the release tree.

    The rule the plug-in applies when it builds the program list of a
    module is in ``plugins/vst3native/AuralisVST3.cpp``: a preset belongs
    to the module it names, and it is dropped when it names a parameter
    the module does not have.  The gate here is that rule, so a package
    that carries a preset the plug-in would drop, or a module that has no
    preset at all, is reported before the package is signed.

    Returns the list of problems; an empty list means every module of the
    catalogue has a program list and every preset in the package is one the
    plug-in can load.
    """
    problems: list[str] = []
    try:
        from aurcore import registry
    except Exception as exc:  # noqa: BLE001 - the build must not crash on this
        return [f"the module registry could not be read: {exc}"]

    catalogue = {info.code: info for info in registry.all()}
    library = destination / "var" / "presets" / "builtin"
    published: dict[str, int] = {}

    for path in sorted(library.rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"the preset {path.name} cannot be read: {exc}")
            continue
        module = document.get("module")
        code = module.get("code") if isinstance(module, dict) else None
        if not code:
            problems.append(f"the preset {path.name} names no module")
            continue
        info = catalogue.get(code)
        if info is None:
            problems.append(
                f"the preset {path.name} names the module {code}, "
                "which is not in this build"
            )
            continue
        names = {desc.name for desc in info.params}
        params = document.get("params")
        params = params if isinstance(params, dict) else {}
        unknown = sorted(name for name in params if name not in names)
        if unknown:
            problems.append(
                f"the preset {path.name} names the parameters {unknown}, "
                f"which the module {code} does not have"
            )
            continue
        published[code] = published.get(code, 0) + 1

    for code in sorted(registry.codes()):
        if not published.get(code):
            problems.append(
                f"the module {code} has no preset in the package, so its "
                "program list would be empty"
            )
    return problems


def find_abi_library(root: Path) -> Path | None:
    """Return the built core C ABI library, when it exists."""
    build = root / "plugins" / "abi" / "build"
    for name in ("aurcore_abi.dll", "aurcore_abi.dylib", "aurcore_abi.so"):
        candidate = build / name
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class PluginBundle:
    """One built plug-in module.

    A module is either a file (the layout a Linux host uses) or a folder that
    holds one (the Windows and macOS layouts).  The folder is copied as a
    unit, because a host expects to find the layout it was shipped with.
    """

    path: Path
    is_folder: bool

    @property
    def name(self) -> str:
        """The name of the bundle, as a host sees it."""
        return self.path.name

    def size(self) -> int:
        """The size of the bundle on disk, in bytes."""
        if self.path.is_file():
            return self.path.stat().st_size
        return sum(path.stat().st_size for path in self.path.rglob("*") if path.is_file())


def plugin_search_roots(root: Path, override: Path | None = None) -> list[Path]:
    """Return the directories that are searched for the built plug-in bundles.

    ``override`` replaces the search entirely: it is what ``--plugins-dir``
    hands over.  Without it the search follows ``PLUGIN_SEARCH_GLOBS``.
    """
    root = Path(root)
    if override is not None:
        candidate = Path(override)
        return [candidate] if candidate.is_dir() else []

    roots: list[Path] = []
    seen: set[Path] = set()
    for pattern in PLUGIN_SEARCH_GLOBS:
        try:
            matches = sorted(root.glob(pattern))
        except (OSError, ValueError):
            continue
        for directory in matches:
            if not directory.is_dir():
                continue
            key = directory.resolve()
            if key in seen:
                continue
            seen.add(key)
            roots.append(directory)
    return roots


def _bundle_files(path: Path) -> list[Path]:
    """Return the files of a bundle folder."""
    try:
        return [item for item in sorted(path.rglob("*")) if item.is_file()]
    except OSError:
        return []


def _bundle_mtime(bundle: PluginBundle) -> float:
    """When the bundle was built: the newest file it holds.

    The files of a bundle are written by the build, so the newest one
    says when the build ran.  The staging removes the runtime from the
    bundle afterwards, which does not change the times of the files that
    stay.
    """
    newest = 0.0
    for path in _bundle_files(bundle.path):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _scan_for_bundles(directory: Path, bundles: list[PluginBundle], depth: int = 0) -> None:
    """Find the plug-in modules under a directory.

    A folder whose name ends in a plug-in suffix is a bundle when it holds a
    module; the empty folder a failed build leaves behind is not one, and
    the search goes on inside it.

    The walk is bounded: a build tree holds a whole compiler database, and
    a bundle is never deeper than the output directory of a build.
    """
    if depth > PLUGIN_SCAN_DEPTH:
        return
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in PLUGIN_SCAN_SKIP:
                continue
            if entry.suffix.lower() in PLUGIN_SUFFIXES and _bundle_files(entry):
                # A bundle that holds a module: it is staged as a unit, so
                # the search does not go inside it.
                bundles.append(PluginBundle(entry, True))
                continue
            _scan_for_bundles(entry, bundles, depth + 1)
        elif entry.is_file() and entry.suffix.lower() in PLUGIN_SUFFIXES:
            bundles.append(PluginBundle(entry, False))


def find_plugin_bundles(
    root: Path,
    override: Path | None = None,
    *,
    roots: Sequence[Path] | None = None,
) -> list[PluginBundle]:
    """Return the built plug-in bundles, when they exist.

    Only a *produced* module is a bundle: a ``.vst3`` file, or a folder that
    holds one.  The empty folder a failed plug-in build leaves behind is not
    a module and is not picked up.

    ``roots`` is the list of directories to search; it is computed from
    ``root`` and ``override`` when it is not given.
    """
    bundles: list[PluginBundle] = []
    seen: set[str] = set()
    directories = list(roots) if roots is not None else plugin_search_roots(root, override)
    for directory in directories:
        found: list[PluginBundle] = []
        _scan_for_bundles(directory, found)
        for bundle in found:
            if bundle.name in seen:
                # The same module in two build trees: the first one wins.
                continue
            seen.add(bundle.name)
            bundles.append(bundle)
    return bundles


@dataclass(frozen=True)
class StagedItem:
    """One artefact that was staged into a release tree."""

    kind: str
    name: str
    source: Path
    target: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of the item."""
        return {
            "kind": self.kind,
            "name": self.name,
            "source": str(self.source),
            "target": self.target,
            "size": self.size,
        }


@dataclass
class PluginLayer:
    """The plug-in layer of a release tree."""

    searched: list[Path] = field(default_factory=list)
    bundles: list[StagedItem] = field(default_factory=list)
    meta: list[StagedItem] = field(default_factory=list)
    abi: StagedItem | None = None
    family: "PluginFamily | None" = None
    runtime: StagedItem | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def built(self) -> bool:
        """Were any plug-in bundles staged?"""
        return bool(self.bundles)

    @property
    def total_size(self) -> int:
        """The size of the plug-in layer, in bytes."""
        total = sum(item.size for item in self.bundles)
        total += sum(item.size for item in self.meta)
        if self.runtime is not None:
            total += self.runtime.size
        if self.abi is not None:
            total += self.abi.size
        return total

    def absorb(self, other: "PluginLayer") -> None:
        """Take over the contents of another layer."""
        self.searched = list(other.searched)
        self.bundles = list(other.bundles)
        self.meta = list(other.meta)
        self.abi = other.abi
        self.family = other.family
        self.runtime = other.runtime
        self.problems = list(other.problems)


# ---------------------------------------------------------------------------
#  The plug-in family
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModuleInfo:
    """One module of the canonical registry."""

    code: str
    display_name: str
    category: str
    edition: str
    element: str
    param_count: int

    @property
    def product_name(self) -> str:
        """The name the plug-in carries in a host."""
        return f"{PRODUCT} {self.display_name}"


def registry_modules(root: Path = ROOT) -> dict[str, ModuleInfo]:
    """Return the modules of the canonical registry.

    The registry is the parameter table (``aurcore/schema/params.tsv``); the
    release carries one plug-in per module of it.
    """
    from aurcore import schema

    table = Path(root) / "aurcore" / "schema" / "params.tsv"
    specs = schema.load(str(table)) if table.is_file() else schema.load()
    return {
        code: ModuleInfo(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            edition=spec.edition,
            element=spec.element,
            param_count=len(spec.params),
        )
        for code, spec in specs.items()
    }


def load_identity_module(root: Path = ROOT) -> Any | None:
    """Load the identity layer, or return ``None`` when it is not there.

    The identity layer owns the format of the record that is stamped into a
    plug-in binary.  The release builder only reads it: the module is loaded
    by path, because ``plugins`` is not a package.
    """
    path = Path(root) / "plugins" / "identity.py"
    if not path.is_file():
        return None

    loaded = sys.modules.get("auralis_identity")
    if loaded is not None:
        return loaded

    spec = importlib.util.spec_from_file_location("auralis_identity", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["auralis_identity"] = module
    spec.loader.exec_module(module)
    return module


def _file_has_marker(path: Path, marker: bytes) -> bool:
    """Does the file carry the identity marker?"""
    overlap = len(marker) - 1
    tail = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                if marker in tail + chunk:
                    return True
                tail = (tail + chunk)[-overlap:] if overlap else b""
    except OSError:
        return False


def _bundle_binary(bundle: PluginBundle, identity: Any | None) -> tuple[Path | None, bool]:
    """Return the binary of a bundle and whether it carries an identity.

    A bundle holds more than the plug-in: the core library and the
    interpreter travel with it.  The binary that carries the identity marker
    is the plug-in, so that is the one that is returned.

    The binaries are looked at from the smallest to the largest and the
    search stops at the first one that carries the marker: the interpreter
    is the biggest file of a bundle and it never carries one.
    """
    files = [path for path in _bundle_files(bundle.path) if path.is_file()]
    binaries = [path for path in files if path.suffix.lower() in PLUGIN_BINARY_SUFFIXES]
    if not binaries:
        return None, False

    marker = getattr(identity, "IDENTITY_MAGIC", b"") if identity is not None else b""
    if marker:
        for path in sorted(binaries, key=lambda item: item.stat().st_size):
            if _file_has_marker(path, marker):
                return path, True

    return max(binaries, key=lambda path: path.stat().st_size), False


def _normalise_name(text: str) -> str:
    """Return a comparable form of a plug-in or module name."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _bundle_matches_module(bundle: PluginBundle, module: ModuleInfo) -> bool:
    """Does the name of the bundle name the module?

    The bundle is named after the product and the display name of the
    module.  The two are compared without their punctuation, so that the
    name the build gives a bundle and the name the registry gives the
    module are the same name.
    """
    stem = bundle.name
    for suffix in PLUGIN_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    wanted = _normalise_name(module.display_name)
    if not wanted:
        return False
    return _normalise_name(stem).endswith(wanted)


@dataclass
class PluginCandidate:
    """One built plug-in bundle and what it says about itself."""

    bundle: PluginBundle
    root_index: int = 0
    module_code: str = ""
    identity: dict[str, Any] = field(default_factory=dict)
    stamped: bool = False
    problem: str = ""
    binary: Path | None = None
    source: str = ""

    @property
    def name(self) -> str:
        """The name of the bundle."""
        return self.bundle.name

    @property
    def size(self) -> int:
        """The size of the bundle on disk, in bytes."""
        return self.bundle.size()


def find_plugin_candidates(
    root: Path,
    override: Path | None = None,
    *,
    roots: Sequence[Path] | None = None,
    identity: Any | None = None,
) -> list[PluginCandidate]:
    """Find every built plug-in bundle and read the identity it carries.

    The bundles are returned in the order they are found: the search roots
    first, in the order of :data:`PLUGIN_SEARCH_GLOBS`, and the bundles
    inside a root in a stable order.  A bundle that carries no identity is
    returned as well - it is the *gate* that decides whether such a bundle
    may be shipped, not the search.
    """
    root = Path(root)
    directories = list(roots) if roots is not None else plugin_search_roots(root, override)
    candidates: list[PluginCandidate] = []
    seen: set[str] = set()

    for index, directory in enumerate(directories):
        found: list[PluginBundle] = []
        _scan_for_bundles(directory, found)
        # The bundle that ships for a module is the one that was built
        # last: a build tree holds the builds of several rounds, and the
        # last one is the current one.  A bundle that cannot be loaded
        # comes last, whatever its age: the gate reports it, and a bundle
        # that is not stamped may not be shipped.
        found.sort(
            key=lambda bundle: (
                bundle_completeness(bundle, _runtime_dir_of(bundle.path)),
                -_bundle_mtime(bundle),
                -bundle.size(),
                bundle.name,
            )
        )
        for bundle in found:
            key = str(bundle.path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidate = PluginCandidate(bundle=bundle, root_index=index)
            candidate.source = _relative_to_root(bundle.path, root)
            binary, stamped = _bundle_binary(bundle, identity)
            if binary is None:
                candidate.problem = "the bundle holds no binary"
            elif not stamped:
                candidate.problem = "the bundle is not stamped"
            else:
                record = _read_identity(binary, identity)
                if record is None:
                    candidate.problem = "the stamped record does not parse"
                else:
                    candidate.stamped = True
                    candidate.module_code = str(record.get("module_code", ""))
                    candidate.identity = record
                    candidate.binary = binary
            candidates.append(candidate)

    return candidates


def _relative_to_root(path: Path, root: Path) -> str:
    """Return a path relative to the repository root, in posix form."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def _read_identity(binary: Path, identity: Any | None) -> dict[str, Any] | None:
    """Read the identity record out of a binary, or return ``None``."""
    if identity is None:
        return None
    try:
        record = identity.read_identity(binary)
    except Exception:  # noqa: BLE001 - a broken record is not a crash
        return None
    return dict(record.to_dict())


@dataclass
class PluginFamily:
    """The plug-in family of a release.

    The family is the set of modules the registry declares and the bundles
    that implement them.  ``selected`` holds the bundle that is shipped for
    every module, ``ignored`` holds the bundles that were found but not
    shipped, and ``problems`` holds what is wrong with the family.
    """

    modules: list[ModuleInfo] = field(default_factory=list)
    selected: list[PluginCandidate] = field(default_factory=list)
    ignored: list[tuple[str, str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Is every module of the registry covered?"""
        return not self.problems

    @property
    def module_count(self) -> int:
        """The number of modules the family covers."""
        return len(self.selected)


def bundle_completeness(bundle: PluginBundle, runtime: Path | None = None) -> str:
    """Return what the bundle is missing, or an empty string.

    A bundle has to carry the plug-in itself, and the core library it is
    linked against and the interpreter that library embeds have to be
    reachable.  They are reachable in one of two ways: inside the bundle
    (the layout before the runtime was shared) or in the shared runtime
    directory next to the bundle (the layout that ships).  A bundle that
    was copied while its build was still running is missing them, and a
    host cannot load such a bundle.
    """
    names = {path.name.lower() for path in _bundle_files(bundle.path) if path.is_file()}

    runtime_names: set[str] = set()
    if runtime is not None and Path(runtime).is_dir():
        runtime_names = {
            path.name.lower() for path in _bundle_files(Path(runtime)) if path.is_file()
        }

    def provided(prefix: str) -> bool:
        """Is a file whose name starts with the prefix there?"""
        return any(name.startswith(prefix) for name in names | runtime_names)

    missing: list[str] = []
    if not provided("aurcore_abi"):
        missing.append("the core library")
    if not provided("python3") and not provided("libpython3"):
        missing.append("the interpreter")
    if not any(name.endswith((".vst3.dll", ".vst3", ".so", ".dylib")) for name in names):
        missing.append("the plug-in binary")
    return ", ".join(missing)


def resolve_plugin_family(
    candidates: Sequence[PluginCandidate],
    modules: Mapping[str, ModuleInfo],
) -> PluginFamily:
    """Pick the bundle that ships for every module and report the rest.

    A bundle that carries an identity says which module it implements, and
    that is what the selection is based on.  A bundle that carries no
    identity can only be matched by its name; it is selected for its module
    so that the report shows it, and the gate refuses the build because a
    plug-in that is not stamped may not be shipped.
    """
    family = PluginFamily(modules=list(modules.values()))
    selected: dict[str, PluginCandidate] = {}
    unstamped: list[PluginCandidate] = []

    for candidate in candidates:
        code = candidate.module_code
        if not candidate.stamped:
            unstamped.append(candidate)
            continue
        if code not in modules:
            family.ignored.append(
                (
                    str(candidate.bundle.path),
                    "the stamped record names no known module",
                    code or "?",
                )
            )
            continue
        if code in selected:
            family.ignored.append(
                (str(candidate.bundle.path), "a second bundle for a module that is covered", code)
            )
            continue
        selected[code] = candidate

    # The bundles that carry no identity are matched by their name.
    for candidate in unstamped:
        module = next(
            (
                modules[code]
                for code in modules
                if code not in selected and _bundle_matches_module(candidate.bundle, modules[code])
            ),
            None,
        )
        if module is None:
            family.ignored.append(
                (str(candidate.bundle.path), candidate.problem or "not stamped", "")
            )
            continue
        selected[module.code] = candidate

    for code, module in modules.items():
        candidate = selected.get(code)
        if candidate is None:
            family.problems.append(
                f"the plug-in for {module.display_name} ({code}) is missing"
            )
            continue
        if not candidate.stamped:
            # The bundle is selected so that the report shows what would
            # ship, and the gate refuses the build because a plug-in that is
            # not stamped may not be shipped.
            family.problems.append(
                f"the plug-in for {module.display_name} ({code}) is not stamped: "
                f"{candidate.bundle.path}"
            )
        else:
            missing = bundle_completeness(
                candidate.bundle, _runtime_dir_of(candidate.bundle.path)
            )
            if missing:
                family.problems.append(
                    f"the plug-in for {module.display_name} ({code}) is incomplete: "
                    f"it carries no {missing}: {candidate.bundle.path}"
                )
        family.selected.append(candidate)

    family.selected.sort(key=lambda candidate: candidate.module_code)
    return family


def check_plugin_family(
    root: Path = ROOT,
    plugins_dir: Path | None = None,
    *,
    identity: Any | None = None,
) -> PluginFamily:
    """Read the plug-in family of the build trees and check it.

    The check is the gate: a module without a bundle and a bundle without a
    stamped identity are both problems the release must not be built with.
    """
    if identity is None:
        identity = load_identity_module(root)
    modules = registry_modules(root)
    candidates = find_plugin_candidates(root, plugins_dir, identity=identity)
    family = resolve_plugin_family(candidates, modules)
    family.warnings.extend(family_warnings(family))
    return family


def family_warnings(family: PluginFamily) -> list[str]:
    """Return what the family says that the user should know about.

    The bundles are built one after another, so a build tree can hold
    bundles that were linked against different versions of the core
    library.  The package then carries more than one core library, and the
    user is better off knowing than not.
    """
    warnings: list[str] = []
    libraries: dict[str, list[str]] = {}
    for candidate in family.selected:
        for path in _bundle_files(candidate.bundle.path):
            if not path.name.lower().startswith("aurcore_abi"):
                continue
            digest = sha256_of(path)
            libraries.setdefault(digest, []).append(candidate.name)
            break

    if len(libraries) > 1:
        sizes = ", ".join(
            f"{len(names)} bundle(s) with the {digest[:8]} library"
            for digest, names in sorted(libraries.items())
        )
        warnings.append(
            f"the build tree holds {len(libraries)} different core libraries ({sizes}); "
            "the family has to be rebuilt against one of them"
        )
    return warnings


def _copy_bundle(source: Path, target: Path) -> None:
    """Copy a plug-in bundle into the release tree, as a unit.

    The shared runtime carries the standard library of the interpreter, and
    that library holds byte code.  Byte code is regenerated on the first
    import, so it is not shipped: the package is smaller and the tree holds
    no artefact of the machine that built it.
    """
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, *EXCLUDE_SUFFIXES),
        )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _stage_sidecars(roots: Sequence[Path], out_dir: Path, stage: Path) -> list[StagedItem]:
    """Copy the install manifest and the identity twins next to the bundles."""
    staged: list[StagedItem] = []
    seen: set[str] = set()
    for directory in roots:
        if not directory.is_dir():
            continue
        for pattern in PLUGIN_SIDECAR_GLOBS:
            for path in sorted(directory.rglob(pattern)):
                if not path.is_file() or path.name in seen:
                    continue
                seen.add(path.name)
                target = stage / path.name
                shutil.copy2(path, target)
                staged.append(
                    StagedItem(
                        kind="meta",
                        name=path.name,
                        source=path,
                        target=target.relative_to(out_dir).as_posix(),
                        size=path.stat().st_size,
                    )
                )
    return staged


def _runtime_dir_of(bundle: Path) -> Path | None:
    """The shared runtime that belongs to a bundle, when there is one.

    The rule is the one the plug-in uses: a directory called
    ``Auralis Runtime`` next to the bundle, one level above it or two
    levels above it.
    """
    bundle = Path(bundle)
    own = bundle.parent
    for base in (own, own.parent, own.parent.parent):
        candidate = base / RUNTIME_DIR_NAME
        if candidate.is_dir():
            return candidate
    return None


def _bundle_runtime_members(bundle: Path, plug_in: Path | None) -> list[Path]:
    """The shared libraries of a bundle that belong to the shared runtime.

    That is every shared library of the bundle except the plug-in itself.
    """
    members: list[Path] = []
    for path in sorted(Path(bundle).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SHARED_LIBRARY_SUFFIXES:
            continue
        if plug_in is not None and path == plug_in:
            continue
        members.append(path)
    return members


def stage_shared_runtime(
    stage: Path,
    candidates: Sequence[PluginCandidate],
) -> tuple[Path | None, list[str], list[str]]:
    """Stage the shared runtime of the family into the release tree.

    The runtime is the core's C ABI and the interpreter the core is linked
    against.  Carried inside every bundle it is 48 copies of the same
    7.8 MB file; shared by the family it is one copy.

    The build stages the runtime after the build, so the bundles of the
    build tree hold the plug-in alone and the runtime sits in a directory
    of its own next to them.  A build tree that was not staged carries the
    runtime inside every bundle: the copies are compared, one of them is
    kept and the rest are removed, so the release carries one copy either
    way.

    The copy that ships is the one that was next to the plug-in: that is
    the one the plug-in was linked against.  A runtime the build staged
    that is not the same file is a warning, not a refusal - the family is
    still usable.

    Returns the directory of the runtime, the problems and the warnings.
    """
    problems: list[str] = []
    warnings: list[str] = []
    runtime = Path(stage) / RUNTIME_DIR_NAME

    # 1. the copies the bundles carry themselves.
    kept: dict[str, str] = {}
    for candidate in candidates:
        bundle = Path(stage) / candidate.bundle.name
        plug_in = None
        if candidate.binary is not None:
            try:
                inside = candidate.binary.relative_to(candidate.bundle.path)
            except ValueError:
                inside = None
            if inside is not None:
                plug_in = bundle / inside
        if plug_in is None:
            # The bundle carries no identity, so the plug-in inside it is
            # not known and the runtime cannot be told apart from it.  The
            # family gate is what reports such a bundle; the staging leaves
            # it alone.
            continue
        for member in _bundle_runtime_members(bundle, plug_in):
            digest = sha256_of(member)
            name = member.name
            if name in kept:
                if kept[name] != digest:
                    problems.append(
                        f"the bundles do not agree about {name} - the family "
                        "has to be rebuilt before it is released"
                    )
                member.unlink()
                continue
            kept[name] = digest
            runtime.mkdir(parents=True, exist_ok=True)
            shutil.move(str(member), str(runtime / name))

    # 2. the runtime the build staged, when it staged one.
    for candidate in candidates:
        source = _runtime_dir_of(candidate.bundle.path)
        if source is None:
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            # The runtime carries the standard library of the interpreter,
            # and that library holds byte code.  Byte code is regenerated on
            # the first import, so it is not shipped.
            if any(part in EXCLUDE_DIRS for part in relative.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            target = runtime / relative
            if target.is_file():
                if sha256_of(target) != sha256_of(path):
                    warnings.append(
                        f"the runtime the build staged and the one the bundles "
                        f"carry are not the same file ({path.name}) - the one "
                        "that was next to the bundles is the one that ships"
                    )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        break

    if not runtime.is_dir() or not any(runtime.iterdir()):
        return None, problems, warnings
    return runtime, problems, warnings


def stage_plugin_layer(
    root: Path,
    out_dir: Path,
    profile: ReleaseProfile,
    *,
    plugins_dir: Path | None = None,
    family: PluginFamily | None = None,
) -> PluginLayer:
    """Stage the plug-in layer into a release tree.

    The bundles go into the plug-in directory of the tree, the core C ABI
    library next to the application.  A profile that does not carry the
    plug-in layer stages nothing at all, and a tree built without the
    plug-ins stages nothing either - the build still succeeds.

    The bundle that ships for a module is the one the family selected: the
    bundle that carries the identity of that module.  A bundle that is
    staged without an identity is staged so that the report shows it, and
    the gate refuses the build because a plug-in that is not stamped may
    not be shipped.
    """
    root = Path(root)
    out_dir = Path(out_dir)
    layer = PluginLayer(searched=plugin_search_roots(root, plugins_dir))

    if not profile.include_plugins and not profile.include_abi:
        return layer

    if profile.include_abi:
        library = find_abi_library(root)
        if library is not None:
            target = out_dir / ABI_STAGE_DIR / library.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library, target)
            layer.abi = StagedItem(
                kind="abi",
                name=library.name,
                source=library,
                target=target.relative_to(out_dir).as_posix(),
                size=library.stat().st_size,
            )

    if profile.include_plugins:
        stage = out_dir / PLUGIN_STAGE_DIR
        stage.mkdir(parents=True, exist_ok=True)
        if family is None:
            family = check_plugin_family(root, plugins_dir)
        layer.family = family
        for candidate in family.selected:
            bundle = candidate.bundle
            target = stage / bundle.name
            _copy_bundle(bundle.path, target)
        layer.meta = _stage_sidecars(layer.searched, out_dir, stage)

        # The runtime is shared by the whole family: the bundles hold the
        # plug-in alone, and the core and the interpreter live once, in the
        # runtime directory next to them.  Without it the release carries
        # 48 copies of the interpreter (359 MiB).
        runtime, problems, warnings = stage_shared_runtime(stage, family.selected)
        layer.problems.extend(problems)
        if layer.family is not None:
            layer.family.warnings.extend(warnings)

        # The bundles are listed with the size they have in the package:
        # the runtime was taken out of them, so they are smaller now than
        # they were in the build tree.
        for candidate in family.selected:
            bundle = candidate.bundle
            target = stage / bundle.name
            layer.bundles.append(
                StagedItem(
                    kind="vst3",
                    name=bundle.name,
                    source=bundle.path,
                    target=target.relative_to(out_dir).as_posix(),
                    size=sum(
                        path.stat().st_size
                        for path in target.rglob("*")
                        if path.is_file()
                    ),
                )
            )

        if runtime is not None:
            layer.runtime = StagedItem(
                kind="runtime",
                name=runtime.name,
                source=runtime,
                target=runtime.relative_to(out_dir).as_posix(),
                size=sum(
                    path.stat().st_size for path in runtime.rglob("*") if path.is_file()
                ),
            )

    return layer


def launcher_scripts() -> dict[str, str]:
    """Return the launcher scripts written into the release tree."""
    windows_workbench = """@echo off
rem Auralis - audio restoration workbench (Windows launcher).
setlocal
set "AURALIS_ROOT=%~dp0.."
set "AURALIS_PRESET_DIR=%AURALIS_ROOT%\\var\\presets"
set "PYTHONPATH=%AURALIS_ROOT%"
if "%AURALIS_PORT%"=="" set "AURALIS_PORT=8731"
if not "%~1"=="" set "AURALIS_OPEN=%~1"
cd /d "%AURALIS_ROOT%"
set "AURALIS_PY="
where py >nul 2>nul && set "AURALIS_PY=py -3"
if "%AURALIS_PY%"=="" (where python >nul 2>nul && set "AURALIS_PY=python")
if "%AURALIS_PY%"=="" (
  echo Auralis needs Python 3.11 or newer on PATH.
  exit /b 1
)
start "Auralis server" /min %AURALIS_PY% -m aurserver --host 127.0.0.1 --port %AURALIS_PORT%
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%AURALIS_PORT%/"
exit /b 0
"""
    windows_server = """@echo off
rem Auralis - start the application server only.
setlocal
set "AURALIS_ROOT=%~dp0.."
set "AURALIS_PRESET_DIR=%AURALIS_ROOT%\\var\\presets"
set "PYTHONPATH=%AURALIS_ROOT%"
if "%AURALIS_PORT%"=="" set "AURALIS_PORT=8731"
if not "%~1"=="" set "AURALIS_OPEN=%~1"
cd /d "%AURALIS_ROOT%"
set "AURALIS_PY="
where py >nul 2>nul && set "AURALIS_PY=py -3"
if "%AURALIS_PY%"=="" (where python >nul 2>nul && set "AURALIS_PY=python")
if "%AURALIS_PY%"=="" (
  echo Auralis needs Python 3.11 or newer on PATH.
  exit /b 1
)
%AURALIS_PY% -m aurserver --host 127.0.0.1 --port %AURALIS_PORT%
"""
    posix = """#!/bin/sh
# Auralis - audio restoration workbench (POSIX launcher).
set -e
AURALIS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AURALIS_ROOT
export AURALIS_PRESET_DIR="$AURALIS_ROOT/var/presets"
export PYTHONPATH="$AURALIS_ROOT${PYTHONPATH:+:$PYTHONPATH}"
AURALIS_PORT="${AURALIS_PORT:-8731}"
export AURALIS_PORT
if [ $# -ge 1 ]; then
  export AURALIS_OPEN="$1"
fi
cd "$AURALIS_ROOT"
exec "${AURALIS_PYTHON:-python3}" -m aurserver --host 127.0.0.1 --port "$AURALIS_PORT"
"""
    return {
        "auralis.cmd": windows_workbench,
        "auralis-server.cmd": windows_server,
        "auralis.sh": posix,
    }


def assemble_release(
    root: Path,
    out_dir: Path,
    profile: ReleaseProfile,
    *,
    plugins_dir: Path | None = None,
    layer: PluginLayer | None = None,
    family: PluginFamily | None = None,
) -> Path:
    """Assemble the release tree of one profile and return its directory.

    The tree mirrors the repository layout, so the Inno Setup script can be
    compiled straight from the repository or from a release tree without a
    second set of rules.

    ``layer`` is filled with what was staged when it is given, and
    ``family`` is the plug-in family that was read from the build trees.
    """
    root = Path(root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for tree in APP_TREES:
        source = root / tree
        if source.is_dir():
            copy_tree(source, out_dir / tree, root)

    ensure_presets(root, out_dir)

    # The plug-in builds its program list from the preset files of the
    # package, so a module without a preset would show an empty list to the
    # host.  The problems are reported before the package is signed.
    for problem in verify_presets(out_dir):
        print(f"warning: {problem}")

    # The tools travel with the package: the user needs the verifier to be
    # able to check the package.
    for source_name, target_name in TOOL_FILES:
        source = root / source_name
        if not source.is_file():
            continue
        target = out_dir / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    staged = stage_plugin_layer(
        root, out_dir, profile, plugins_dir=plugins_dir, family=family
    )
    if layer is not None:
        layer.absorb(staged)

    for name, text in launcher_scripts().items():
        launcher = out_dir / "bin" / name
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(text, encoding="utf-8", newline="\r\n")
        if name.endswith(".sh"):
            launcher.chmod(0o755)

    normalise_timestamps(out_dir)
    return out_dir


# ---------------------------------------------------------------------------
#  The manifest
# ---------------------------------------------------------------------------
@dataclass
class ManifestEntry:
    """One file in the manifest."""

    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of the entry."""
        return {"path": self.path, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManifestEntry":
        """Build an entry from its JSON form."""
        return cls(
            path=str(data["path"]),
            size=int(data["size"]),
            sha256=str(data["sha256"]),
        )


@dataclass
class Manifest:
    """The manifest of a release tree.

    The manifest describes the package completely enough that a user can
    check it: every file with its size and its hash, the version, the build,
    the modules that are included, the plug-in family with the identifiers of
    every module, the size breakdown and the command that verifies it.
    """

    release_version: str
    profile: str
    created_utc: str
    entries: list[ManifestEntry] = field(default_factory=list)
    format: str = MANIFEST_FORMAT
    version: int = MANIFEST_VERSION
    product: str = PRODUCT
    build: dict[str, Any] = field(default_factory=dict)
    contents: dict[str, Any] = field(default_factory=dict)
    size: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        """The total size of the tree, in bytes."""
        return sum(entry.size for entry in self.entries)

    @property
    def file_count(self) -> int:
        """The number of files in the tree."""
        return len(self.entries)

    @property
    def build_id(self) -> str:
        """The fingerprint of the content of the tree."""
        digest = hashlib.sha256()
        for entry in self.entries:
            digest.update(f"{entry.path}\0{entry.size}\0{entry.sha256}\n".encode("utf-8"))
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of the manifest."""
        return {
            "format": self.format,
            "version": self.version,
            "product": self.product,
            "release_version": self.release_version,
            "profile": self.profile,
            "created_utc": self.created_utc,
            "build": self.build,
            "contents": self.contents,
            "size": self.size,
            "verify": self.verify,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "files": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        """Build a manifest from its JSON form."""
        return cls(
            release_version=str(data.get("release_version", "")),
            profile=str(data.get("profile", "")),
            created_utc=str(data.get("created_utc", "")),
            entries=[ManifestEntry.from_dict(item) for item in data.get("files", [])],
            format=str(data.get("format", MANIFEST_FORMAT)),
            version=int(data.get("version", MANIFEST_VERSION)),
            product=str(data.get("product", PRODUCT)),
            build=dict(data.get("build", {})),
            contents=dict(data.get("contents", {})),
            size=dict(data.get("size", {})),
            verify=dict(data.get("verify", {})),
        )


def sha256_of(path: Path) -> str:
    """Return the SHA-256 of a file, as a lower case hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp() -> str:
    """Return the build timestamp, honouring ``SOURCE_DATE_EPOCH``."""
    epoch = source_date_epoch()
    if epoch is not None:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def category_of(path: str) -> str:
    """Return the category a file of the package belongs to."""
    for name, prefixes in SIZE_CATEGORIES:
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                return name
    return "other"


def size_breakdown(entries: Iterable[ManifestEntry]) -> list[dict[str, Any]]:
    """Break the size of a package down into its categories."""
    totals: dict[str, dict[str, int]] = {}
    for entry in entries:
        bucket = totals.setdefault(category_of(entry.path), {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += entry.size

    order = [name for name, _ in SIZE_CATEGORIES]
    rows: list[dict[str, Any]] = []
    for name in order + sorted(set(totals) - set(order)):
        bucket = totals.get(name)
        if bucket is None:
            continue
        rows.append({"category": name, "files": bucket["files"], "bytes": bucket["bytes"]})
    return rows


def module_entries(modules: Mapping[str, ModuleInfo]) -> list[dict[str, Any]]:
    """Return the description of every module of the registry."""
    return [
        {
            "code": module.code,
            "display_name": module.display_name,
            "product_name": module.product_name,
            "category": module.category,
            "edition": module.edition,
            "element": module.element,
            "param_count": module.param_count,
        }
        for module in sorted(modules.values(), key=lambda item: item.code)
    ]


def plug_in_entries(
    family: PluginFamily | None,
    staged: Mapping[str, StagedItem],
    modules: Mapping[str, ModuleInfo],
) -> list[dict[str, Any]]:
    """Return the description of the plug-in family that is shipped.

    Every module gets one entry: the bundle it ships in, where that bundle
    is in the package, how big it is, the hash of the plug-in binary and the
    identifiers the plug-in carries.
    """
    if family is None:
        return []

    rows: list[dict[str, Any]] = []
    for candidate in family.selected:
        module = modules.get(candidate.module_code)
        if module is None:
            continue
        item = staged.get(candidate.name)
        identity = candidate.identity
        binary = ""
        if item is not None and candidate.binary is not None:
            try:
                inside = candidate.binary.relative_to(candidate.bundle.path).as_posix()
            except ValueError:
                inside = candidate.binary.name
            binary = f"{item.target}/{inside}"
        rows.append(
            {
                "module_code": candidate.module_code,
                "display_name": module.display_name,
                "product_name": str(identity.get("product_name", module.product_name)),
                "category": str(identity.get("category", module.category)),
                "internal_name": str(identity.get("internal_name", "")),
                "vst3_fuid": str(identity.get("vst3_fuid", "")),
                "original_filename": str(identity.get("original_filename", candidate.name)),
                "param_count": module.param_count,
                "bundle": candidate.name,
                "path": item.target if item is not None else "",
                "binary": binary,
                "sha256": sha256_of(candidate.binary) if candidate.binary else "",
                "size": item.size if item is not None else candidate.size,
                "stamped": bool(candidate.stamped),
                "source": candidate.source,
            }
        )
    return rows


def build_manifest(
    tree: Path,
    profile: str = "full",
    version: str = "",
    *,
    exclude: Iterable[str] = (),
    family: PluginFamily | None = None,
    staged: Mapping[str, StagedItem] | None = None,
    modules: Mapping[str, ModuleInfo] | None = None,
) -> Manifest:
    """Hash every file of the tree and return the manifest.

    The manifest describes the package: the files with their sizes and
    their hashes, the version, the build, the modules, the plug-in family
    and the size breakdown.
    """
    tree = Path(tree)
    skipped = {Path(item).as_posix() for item in exclude}
    entries: list[ManifestEntry] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(tree).as_posix()
        if relative in skipped:
            continue
        entries.append(
            ManifestEntry(path=relative, size=path.stat().st_size, sha256=sha256_of(path))
        )

    if modules is None:
        try:
            modules = registry_modules()
        except Exception:  # noqa: BLE001 - the registry is not worth failing over
            modules = {}

    manifest = Manifest(
        release_version=version,
        profile=profile,
        created_utc=_timestamp(),
        entries=entries,
    )

    breakdown = size_breakdown(entries)
    budget = SIZE_BUDGETS.get(profile, 0)
    measured = manifest.total_size

    manifest.build = {
        "id": manifest.build_id,
        "timestamp_utc": manifest.created_utc,
        "source_date_epoch": source_date_epoch(),
        "generator": "scripts/build_release.py",
        "manifest_version": MANIFEST_VERSION,
        "python": platform.python_version(),
        "platform": platform.system().lower(),
        "profile": profile,
    }
    runtime_prefix = f"{PLUGIN_STAGE_DIR}/{RUNTIME_DIR_NAME}/"
    runtime_entries = [entry for entry in entries if entry.path.startswith(runtime_prefix)]

    manifest.contents = {
        "module_count": len(modules),
        "parameter_count": sum(module.param_count for module in modules.values()),
        "modules": module_entries(modules),
        "plug_in_count": len(family.selected) if family is not None else 0,
        "plug_in_family": plug_in_entries(family, staged or {}, modules),
        "runtime": {
            "name": RUNTIME_DIR_NAME,
            "path": f"{PLUGIN_STAGE_DIR}/{RUNTIME_DIR_NAME}",
            "files": [
                {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
                for entry in runtime_entries
            ],
            "total_bytes": sum(entry.size for entry in runtime_entries),
            "note": (
                "the core and the interpreter are shared by the whole family: "
                "one copy, next to the bundles"
            ),
        },
        "preset_count": sum(
            1 for entry in entries if entry.path.startswith("var/presets/builtin/")
        ),
        "warnings": list(family.warnings) if family is not None else [],
        "note": (
            "the modules are the modules of the registry the package implements; "
            "the plug-in family is the bundles that ship with it"
        ),
    }
    manifest.size = {
        "budget": budget,
        "measured": measured,
        "headroom": budget - measured,
        "file_count": manifest.file_count,
        "breakdown": breakdown,
    }
    manifest.verify = {
        "manifest": MANIFEST_NAME,
        "sha256sums": SHA256SUMS_NAME,
        "command": "python scripts/build_release.py --verify .",
        "sha256sums_command": f"sha256sum -c {SHA256SUMS_NAME}",
        "note": (
            "the manifest lists every file of the package except the manifest "
            "itself and the checksum list; the checksum list covers both"
        ),
    }
    return manifest


def write_manifest(manifest: Manifest, path: Path) -> Path:
    """Write the manifest as JSON and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def write_sha256sums(tree: Path, path: Path) -> Path:
    """Write the checksum list of the tree and return its path.

    The list has the form the standard checksum tool reads: a hash, two
    spaces and the path of the file.  The list covers every file of the
    tree, the manifest included; it does not list itself.
    """
    tree = Path(tree)
    path = Path(path)
    lines: list[str] = []
    for item in sorted(tree.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(tree).as_posix()
        if relative == path.name:
            continue
        lines.append(f"{sha256_of(item)}  {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def read_sha256sums(path: Path) -> dict[str, str]:
    """Read a checksum list back from disk."""
    entries: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if not name:
            continue
        entries[name.strip()] = digest.strip()
    return entries


def verify_sha256sums(tree: Path, path: Path) -> list[str]:
    """Check the tree against the checksum list and return the problems."""
    tree = Path(tree)
    problems: list[str] = []
    listed = read_sha256sums(path)
    for name, digest in sorted(listed.items()):
        item = tree / name
        if not item.is_file():
            problems.append(f"missing file: {name}")
            continue
        if sha256_of(item) != digest:
            problems.append(f"hash mismatch: {name}")
    for item in sorted(tree.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(tree).as_posix()
        if relative in listed or relative == Path(path).name:
            continue
        problems.append(f"not in the checksum list: {relative}")
    return problems


def read_manifest(path: Path) -> Manifest:
    """Read a manifest back from disk."""
    return Manifest.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def verify_manifest(
    tree: Path,
    manifest: Manifest,
    *,
    ignore: Iterable[str] = ("manifest.json",),
) -> list[str]:
    """Check the tree against the manifest and return the problems found.

    The manifest never lists itself, so the file it is written to is ignored
    when the tree is compared with it.
    """
    tree = Path(tree)
    problems: list[str] = []
    listed: set[str] = set()
    ignored = {Path(item).as_posix() for item in ignore}

    for entry in manifest.entries:
        listed.add(entry.path)
        path = tree / entry.path
        if not path.is_file():
            problems.append(f"missing file: {entry.path}")
            continue
        size = path.stat().st_size
        if size != entry.size:
            problems.append(f"size mismatch: {entry.path} ({size} != {entry.size})")
        digest = sha256_of(path)
        if digest != entry.sha256:
            problems.append(f"hash mismatch: {entry.path}")

    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(tree).as_posix()
        if relative in listed or relative in ignored:
            continue
        problems.append(f"not in the manifest: {relative}")

    return problems


def verify_tree(tree: Path, *, quiet: bool = False) -> tuple[bool, list[str], Manifest | None]:
    """Check a release tree against its own manifest.

    The manifest is read from the tree, every file is hashed again and the
    checksum list is checked as well.  Returns ``(ok, problems, manifest)``.
    """
    tree = Path(tree)
    manifest_path = tree / MANIFEST_NAME
    if not manifest_path.is_file():
        return False, [f"the tree carries no {MANIFEST_NAME}"], None

    try:
        manifest = read_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return False, [f"the manifest does not parse: {exc}"], None

    problems = verify_manifest(tree, manifest, ignore=(MANIFEST_NAME, SHA256SUMS_NAME))

    sums_path = tree / SHA256SUMS_NAME
    if sums_path.is_file():
        problems.extend(verify_sha256sums(tree, sums_path))
    elif not quiet:
        problems.append(f"the tree carries no {SHA256SUMS_NAME}")

    digest = manifest.build_id
    recorded = str(manifest.build.get("id", ""))
    if recorded and recorded != digest:
        problems.append("the build id of the manifest does not match the files it lists")

    return (not problems), problems, manifest


# ---------------------------------------------------------------------------
#  The zip
# ---------------------------------------------------------------------------
def make_zip(tree: Path, zip_path: Path, arcname: str = "") -> Path:
    """Write a deterministic zip of the tree and return its path.

    The entries are sorted and carry a fixed timestamp, so the same tree
    always produces the same archive.
    """
    tree = Path(tree)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(tree.rglob("*")):
            relative = path.relative_to(tree).as_posix()
            name = f"{arcname}/{relative}" if arcname else relative
            if path.is_dir():
                info = zipfile.ZipInfo(f"{name}/", date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o40775 << 16
                archive.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return zip_path


# ---------------------------------------------------------------------------
#  The size budget
# ---------------------------------------------------------------------------
@dataclass
class BudgetLine:
    """One line of the size budget report."""

    profile: str
    measured: int
    budget: int

    @property
    def ok(self) -> bool:
        """Is the profile inside its budget?"""
        return self.measured <= self.budget

    @property
    def headroom(self) -> int:
        """How much of the budget is left, in bytes."""
        return self.budget - self.measured

    @property
    def verdict(self) -> str:
        """A one word verdict."""
        return "PASS" if self.ok else "OVER"


def human_size(value: int) -> str:
    """Return a size in a form a human can read."""
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} GiB"


def format_plugin_table(
    layer: PluginLayer,
    *,
    title: str = "PLUG-IN LAYER",
    profile: ReleaseProfile | None = None,
) -> str:
    """Render the table of what was staged into the release tree.

    The table says plainly what was staged, where it came from and where it
    landed - and, when nothing was staged, that the plug-ins were not built.
    """
    out = ["", title, "-" * 78]
    if profile is not None and not profile.include_plugins:
        out.append("  this package carries no plug-in layer")
        out.append("-" * 78)
        return "\n".join(out)

    if layer.searched:
        out.append("  searched : " + ", ".join(str(path) for path in layer.searched))
    else:
        out.append("  searched : nothing - no plug-in build output was found")

    rows = [*layer.bundles, *layer.meta]
    if layer.runtime is not None:
        rows.append(layer.runtime)
    if layer.abi is not None:
        rows.append(layer.abi)

    if not layer.bundles:
        out.append("  the plug-ins were not built - the release carries no plug-in bundles")
        if layer.abi is not None:
            out.append(
                f"  the core C ABI library was staged: {layer.abi.target}"
                f" ({human_size(layer.abi.size)})"
            )
        out.append("-" * 78)
        return "\n".join(out)

    kind_width = max(len("kind"), *(len(row.kind) for row in rows))
    name_width = max(len("module"), *(len(row.name) for row in rows))
    header = (
        f"  {'kind'.ljust(kind_width)}  {'module'.ljust(name_width)}  "
        f"{'size':>11}  staged as"
    )
    out.append(header)
    for row in rows:
        out.append(
            f"  {row.kind.ljust(kind_width)}  {row.name.ljust(name_width)}  "
            f"{human_size(row.size):>11}  {row.target}"
        )
    out.append(
        f"  {'':{kind_width}}  {'':{name_width}}  {human_size(layer.total_size):>11}  total"
    )
    out.append("-" * 78)
    return "\n".join(out)


def format_family_table(
    family: PluginFamily | None,
    *,
    title: str = "PLUG-IN FAMILY",
) -> str:
    """Render the table of the plug-in family of the release.

    One line per module: the code, the name the host shows, the category,
    the class id and the size of the bundle that ships.
    """
    out = ["", title, "-" * 100]
    if family is None or not family.selected:
        out.append("  no plug-in family was read")
        out.append("-" * 100)
        return "\n".join(out)

    out.append(
        f"  {len(family.selected)} modules, "
        f"{len(family.ignored)} bundle(s) found but not shipped"
    )
    header = (
        f"  {'module'.ljust(20)}  {'name'.ljust(22)}  {'category'.ljust(12)}  "
        f"{'class id'.ljust(38)}  {'size':>10}"
    )
    out.append(header)
    for candidate in family.selected:
        identity = candidate.identity
        code = candidate.module_code or "?"
        name = str(identity.get("product_name", candidate.name))
        category = str(identity.get("category", ""))
        fuid = str(identity.get("vst3_fuid", ""))
        out.append(
            f"  {code.ljust(20)}  {name.ljust(22)}  {category.ljust(12)}  "
            f"{fuid.ljust(38)}  {human_size(candidate.size):>10}"
        )
    out.append("-" * 100)
    return "\n".join(out)


def format_ignored_table(family: PluginFamily | None) -> str:
    """Render the bundles that were found but not shipped.

    The bundles are grouped by the reason they were not shipped: a build
    tree that still holds an older build of the family is normal, and the
    report says so in one line instead of one line per bundle.
    """
    if family is None or not family.ignored:
        return ""
    groups: dict[str, list[tuple[str, str]]] = {}
    for path, reason, detail in family.ignored:
        groups.setdefault(reason, []).append((path, detail))

    out = ["", "BUNDLES FOUND BUT NOT SHIPPED", "-" * 78]
    for reason, entries in sorted(groups.items()):
        out.append(f"  {len(entries):>3} x {reason}")
        for path, detail in entries[:3]:
            suffix = f" [{detail}]" if detail else ""
            out.append(f"        {path}{suffix}")
        if len(entries) > 3:
            out.append(f"        ... and {len(entries) - 3} more")
    out.append("-" * 78)
    return "\n".join(out)


def format_size_breakdown(manifest: Manifest) -> str:
    """Render what a package is made of."""
    rows = list(manifest.size.get("breakdown", []))
    out = ["", f"CONTENTS ({manifest.profile})", "-" * 68]
    if not rows:
        out.append("  the package is empty")
        out.append("-" * 68)
        return "\n".join(out)

    measured = manifest.total_size
    header = f"  {'category'.ljust(18)}  {'files':>7}  {'size':>12}  {'share':>7}"
    out.append(header)
    for row in rows:
        share = (row["bytes"] / measured * 100.0) if measured else 0.0
        out.append(
            f"  {str(row['category']).ljust(18)}  {row['files']:>7}  "
            f"{human_size(int(row['bytes'])):>12}  {share:6.1f}%"
        )
    out.append(
        f"  {'total'.ljust(18)}  {manifest.file_count:>7}  {human_size(measured):>12}  "
        f"{'100.0%' if measured else '0.0%'}"
    )
    out.append("-" * 68)
    return "\n".join(out)


def budget_report(manifests: dict[str, Manifest]) -> list[BudgetLine]:
    """Compare every release against its size budget."""
    lines: list[BudgetLine] = []
    for profile, manifest in manifests.items():
        budget = SIZE_BUDGETS.get(profile, 0)
        lines.append(BudgetLine(profile=profile, measured=manifest.total_size, budget=budget))
    return lines


def format_budget_report(lines: Sequence[BudgetLine]) -> str:
    """Render the size budget report."""
    lines = list(lines)
    width = max([len(line.profile) for line in lines] + [7])
    out = ["", "SIZE BUDGET", "-" * 68]
    header = (
        f"  {'profile'.ljust(width)}  {'measured':>12}  "
        f"{'budget':>12}  {'headroom':>12}  verdict"
    )
    out.append(header)
    for line in lines:
        out.append(
            f"  {line.profile.ljust(width)}  {human_size(line.measured):>12}  "
            f"{human_size(line.budget):>12}  {human_size(line.headroom):>12}  {line.verdict}"
        )
    out.append("-" * 68)
    return "\n".join(out)


# ---------------------------------------------------------------------------
#  The installer
# ---------------------------------------------------------------------------
def find_iscc(explicit: str | None = None) -> Path | None:
    """Locate the Inno Setup compiler, when it is installed."""
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None

    found = shutil.which("iscc") or shutil.which("iscc.exe") or shutil.which("ISCC")
    if found:
        return Path(found)

    candidates = (
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", ""))
        / "Inno Setup 5"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 5" / "ISCC.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _numeric_version(version: str) -> str:
    """Turn a release version into the four part form Inno Setup wants."""
    parts: list[str] = []
    for chunk in version.replace("-", ".").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(digits or "0")
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def release_defines(release_dir: Path) -> dict[str, str]:
    """The preprocessor symbols that point at a release tree.

    The wrapper and the source check below both use these, so the two can
    never drift apart.
    """
    release_dir = Path(release_dir).resolve()
    return {
        "SourceRoot": str(release_dir),
        "PresetsRoot": str(release_dir / "var" / "presets" / "builtin"),
        "AbiRoot": str(release_dir / ABI_STAGE_DIR),
        "PluginRoot": str(release_dir / "plugins" / "vst3" / "build" / "stamped"),
    }


def verify_installer_sources(
    release_dir: Path,
    script: Path = INSTALLER_SCRIPT,
) -> list[str]:
    """Check the release tree against the installer script.

    The installer script is the contract for the release tree: a source path
    that is not in the tree is a file the installation would be missing.
    """
    release_dir = Path(release_dir).resolve()
    parsed = parse_iss(script, defines=release_defines(release_dir))
    problems: list[str] = []
    for entry in parsed.section("Files"):
        source = entry.get("source")
        if not source:
            continue
        path = resolve_iss_path(source, parsed)
        if path.exists():
            continue
        if "skipifsourcedoesntexist" in entry.flags():
            continue
        problems.append(f"the release tree is missing {path}")
    return problems


def write_installer_wrapper(
    release_dir: Path,
    output_dir: Path,
    version: str,
    *,
    per_machine: bool = False,
    script: Path = INSTALLER_SCRIPT,
) -> Path:
    """Write the generated wrapper that compiles the installer.

    The wrapper carries the build specific symbols and includes the real
    script, so the compiled installer always refers to the release tree it was
    built from - and the real script stays the single source of truth.
    """
    release_dir = Path(release_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def quoted(path: Path) -> str:
        """Return the path as an ISPP string literal."""
        # Forward slashes: Inno Setup and the preprocessor both accept them on
        # every platform, and they need no escaping inside a quoted string.
        return str(path).replace("\\", "/")

    lines = [
        "; Generated by scripts/build_release.py - do not edit.",
    ]
    for name, value in release_defines(release_dir).items():
        lines.append(f'#define {name} "{quoted(Path(value))}"')
    lines.append(f'#define OutputDir "{quoted(output_dir)}"')
    lines.append(f'#define MyAppVersion "{version}"')
    lines.append(f'#define MyAppVersionNumeric "{_numeric_version(version)}"')
    if per_machine:
        lines.append("#define PerMachine 1")
    lines.append(f'#include "{quoted(Path(script))}"')
    lines.append("")

    wrapper = output_dir / "auralis-build.iss"
    wrapper.write_text("\n".join(lines), encoding="utf-8", newline="\r\n")
    return wrapper


def build_installer(
    release_dir: Path,
    output_dir: Path,
    version: str,
    *,
    iscc: str | None = None,
    per_machine: bool = False,
    script: Path = INSTALLER_SCRIPT,
) -> tuple[bool, str]:
    """Compile the installer.  Returns ``(built, message)``."""
    compiler = find_iscc(iscc)
    if compiler is None:
        return False, "the Inno Setup compiler (iscc) was not found"

    release_dir = Path(release_dir)
    missing = verify_installer_sources(release_dir, script)
    if missing:
        return False, "the release tree does not match the installer script:\n  " + "\n  ".join(
            missing
        )

    output_dir = Path(output_dir)
    wrapper = write_installer_wrapper(
        release_dir, output_dir, version, per_machine=per_machine, script=script
    )

    command = [str(compiler), str(wrapper)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return False, output.strip() or f"iscc failed with code {completed.returncode}"

    built = sorted(output_dir.glob("*.exe"))
    if not built:
        return False, "iscc reported success but produced no installer"
    return True, f"installer: {built[0]}"


# ---------------------------------------------------------------------------
#  The version and the command line
# ---------------------------------------------------------------------------
def repo_version(root: Path = ROOT) -> str:
    """Read the project version from ``pyproject.toml``."""
    pyproject = Path(root) / "pyproject.toml"
    try:
        import tomllib

        with open(pyproject, "rb") as handle:
            data = tomllib.load(handle)
        return str(data.get("project", {}).get("version", "0.0.0"))
    except Exception:  # noqa: BLE001 - the version is not worth failing over
        return "0.0.0"


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        prog="build_release.py",
        description="Assemble the Auralis release packages.",
    )
    parser.add_argument(
        "--profile",
        choices=("full", "slim", "both"),
        default="both",
        help="which package to build (default: both)",
    )
    parser.add_argument("--out", default="dist", help="the output directory (default: dist)")
    parser.add_argument("--version", default="", help="the release version (default: pyproject)")
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="do not run the check suite (the build is not gated)",
    )
    parser.add_argument("--no-zip", action="store_true", help="do not write the zip")
    parser.add_argument(
        "--installer",
        dest="installer",
        action="store_true",
        default=True,
        help="compile the installer when the Inno Setup compiler is available",
    )
    parser.add_argument(
        "--no-installer",
        dest="installer",
        action="store_false",
        help="do not look for the installer compiler",
    )
    parser.add_argument(
        "--per-machine",
        action="store_true",
        help="compile the machine-wide installer instead of the per-user one",
    )
    parser.add_argument("--iscc", default=None, help="the path to the Inno Setup compiler")
    parser.add_argument(
        "--plugins-dir",
        "--plugins",
        dest="plugins_dir",
        default=None,
        help="the directory with the built plug-in bundles (overrides the search)",
    )
    parser.add_argument(
        "--allow-oversize",
        action="store_true",
        help="report a package over its size budget without failing the build",
    )
    parser.add_argument(
        "--verify",
        metavar="TREE",
        default=None,
        help="check a release tree against its own manifest and exit",
    )
    return parser


def run_verify(tree: Path) -> int:
    """Check a release tree against its own manifest."""
    tree = Path(tree)
    print("=" * 78)
    print(" AURALIS RELEASE VERIFY")
    print(f" tree    : {tree}")
    print("=" * 78)

    ok, problems, manifest = verify_tree(tree)
    if manifest is None:
        print("\nRESULT: FAIL - the tree carries no manifest")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"\nproduct  : {manifest.product} {manifest.release_version}")
    print(f" profile  : {manifest.profile}")
    print(f" build    : {manifest.build.get('id', '?')}")
    print(f" files    : {manifest.file_count}")
    print(f" size     : {human_size(manifest.total_size)}")
    family = manifest.contents.get("plug_in_family", [])
    if family:
        print(f" plug-ins: {len(family)}")
    modules = manifest.contents.get("module_count", 0)
    if modules:
        print(f" modules  : {modules} ({manifest.contents.get('parameter_count', 0)} parameters)")

    if ok:
        print("\nRESULT: PASS - the tree matches its manifest")
        return 0

    print(f"\nRESULT: FAIL - {len(problems)} problem(s):")
    for problem in problems[:40]:
        print(f"  - {problem}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Build the release packages.  Returns the process exit code."""
    args = build_parser().parse_args(argv)
    root = ROOT
    version = args.version or repo_version(root)
    out_dir = (root / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    profiles = list(PROFILES) if args.profile == "both" else [args.profile]
    plugins_dir = Path(args.plugins_dir) if args.plugins_dir else None

    if args.verify:
        return run_verify(Path(args.verify))

    print("=" * 78)
    print(" AURALIS RELEASE BUILD")
    print(f" version : {version}")
    print(f" output  : {out_dir}")
    print("=" * 78)

    # 1. the gate ----------------------------------------------------------
    family: PluginFamily | None = None
    needs_family = "full" in profiles
    if args.skip_checks:
        print("\nchecks    : skipped (--skip-checks)")
        print(f"            the {PLUGIN_GATE_NAME} gate is skipped with them")
    else:
        print("\nchecks    :")
        results = run_checks(root)
        for result in results:
            print(f"  [{result.verdict:>4}] {result.name:<14} {result.detail}")
            if not result.ok:
                for line in result.output.splitlines()[-20:]:
                    print(f"         {line}")
        ok, failures = check_gate(results)
        if not ok:
            print(f"\nRESULT: FAIL - {len(failures)} required check(s) failed:")
            for failure in failures:
                print(f"  - {failure.name}: {failure.detail}")
            print("The release was not built.")
            return 1

        # 1b. the plug-in family --------------------------------------------
        if not needs_family:
            print(f"  [SKIP] {PLUGIN_GATE_NAME:<14} the package carries no plug-in layer")
        else:
            print(f"  [....] {PLUGIN_GATE_NAME:<14} reading the build trees ...")
            family = check_plugin_family(root, plugins_dir)
            if family.problems:
                print(f"  [FAIL] {PLUGIN_GATE_NAME:<14} {len(family.problems)} problem(s)")
                for problem in family.problems[:20]:
                    print(f"         - {problem}")
                if len(family.problems) > 20:
                    print(f"         ... and {len(family.problems) - 20} more")
                print("\nRESULT: FAIL - the plug-in family is not complete:")
                for problem in family.problems[:20]:
                    print(f"  - {problem}")
                print("The release was not built.")
                return 1
            print(
                f"  [PASS] {PLUGIN_GATE_NAME:<14} {family.module_count} modules,"
                f" every bundle stamped"
            )
            for warning in family.warnings:
                print(f"  [WARN] {PLUGIN_GATE_NAME:<14} {warning}")

    # 2. the trees ---------------------------------------------------------
    manifests: dict[str, Manifest] = {}
    for name in profiles:
        profile = PROFILES[name]
        print(f"\nassembling the {name} package ...")
        layer = PluginLayer()
        tree = assemble_release(
            root,
            out_dir / name,
            profile,
            plugins_dir=plugins_dir,
            layer=layer,
            family=family if profile.include_plugins else None,
        )
        print(format_plugin_table(layer, title=f"PLUG-IN LAYER ({name})", profile=profile))
        if profile.include_plugins and layer.family is not None:
            print(format_family_table(layer.family, title=f"PLUG-IN FAMILY ({name})"))
            print(format_ignored_table(layer.family))
            for warning in layer.family.warnings:
                print(f"  [WARN] {warning}")
        if layer.problems:
            print(f"  the shared runtime is not clean:")
            for problem in layer.problems[:20]:
                print(f"    - {problem}")
            print("\nRESULT: FAIL - the shared runtime of the family is not usable.")
            return 1
        staged = {item.name: item for item in layer.bundles}
        manifest = build_manifest(
            tree,
            profile=name,
            version=version,
            exclude=(MANIFEST_NAME, SHA256SUMS_NAME),
            family=layer.family,
            staged=staged,
        )
        write_manifest(manifest, tree / MANIFEST_NAME)
        write_sha256sums(tree, tree / SHA256SUMS_NAME)
        problems = verify_manifest(tree, manifest, ignore=(MANIFEST_NAME, SHA256SUMS_NAME))
        if problems:
            print(f"  the manifest does not match the tree:")
            for problem in problems[:20]:
                print(f"    {problem}")
            return 1
        manifests[name] = manifest
        print(format_size_breakdown(manifest))
        print(
            f"  {manifest.file_count} files, {human_size(manifest.total_size)}"
            f"  -> {tree}"
        )
        print(f"  manifest: {tree / MANIFEST_NAME} (build {manifest.build.get('id', '?')})")
        print(f"  checksums: {tree / SHA256SUMS_NAME}")

    # 3. the zips ----------------------------------------------------------
    if not args.no_zip:
        for name, manifest in manifests.items():
            zip_path = out_dir / f"auralis-{version}-{name}.zip"
            make_zip(out_dir / name, zip_path, arcname=name)
            digest = sha256_of(zip_path)
            zip_path.with_suffix(".zip.sha256").write_text(
                f"{digest}  {zip_path.name}\n", encoding="utf-8", newline="\n"
            )
            print(
                f"  zip: {zip_path} ({human_size(zip_path.stat().st_size)})"
                f"  sha256 {digest[:16]}..."
            )

    # 4. the installer -----------------------------------------------------
    if args.installer:
        target_profile = "full" if "full" in manifests else next(iter(manifests))
        target_tree = out_dir / target_profile
        if not PROFILES[target_profile].include_plugins or not any(
            (target_tree / PLUGIN_STAGE_DIR).glob("*.vst3")
        ):
            # The installer ships the plug-in family: a package that carries
            # no bundle has nothing to install them from, and the compiler
            # stops on a wildcard that matches no file.  Such a package is
            # published as the zip.
            print("\ninstaller : skipped (the package carries no plug-in layer)")
        else:
            installer_dir = out_dir / "installer"
            built, message = build_installer(
                target_tree,
                installer_dir,
                version,
                iscc=args.iscc,
                per_machine=args.per_machine,
            )
            print(f"\ninstaller : {message}")
            if not built and args.iscc:
                return 1

    # 5. the budget --------------------------------------------------------
    lines = budget_report(manifests)
    print(format_budget_report(lines))

    oversize = [line for line in lines if not line.ok]
    if oversize and not args.allow_oversize:
        print("\nRESULT: FAIL - a package is over its size budget.")
        return 1

    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
