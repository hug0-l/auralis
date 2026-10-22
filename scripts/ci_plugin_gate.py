#!/usr/bin/env python3
"""
scripts/ci_plugin_gate.py - build the plug-in family and run the host against it.

The gate is the one that proves the plug-ins: it builds the core shared
library, builds the independent host, builds the plug-in modules, stages
the shared runtime of the family and then runs the host against every
module it built.  The host loads the module the way a host loads it,
drives a parameter, processes audio, restores the state and tears the
module down; the report has to say that all of that worked.

The runtime is shared: the bundles hold the plug-in alone, and the core
and the interpreter live once, in the runtime directory next to them.
The gate stages it the way the build does, so the host runs against the
arrangement that ships.

The same command is used by the pipeline (.github/workflows/ci.yml) and by
the local scripts (scripts/ci_local.sh, scripts/ci_local.ps1), so a failure
of the pipeline can be reproduced with one command.

The gate builds every module of the registry by default, because the host
gate of the test suite covers the whole family and skips itself when the
family is only partly built.  A gate that skips is not a gate.

Usage::

    python scripts/ci_plugin_gate.py                      # the whole family
    python scripts/ci_plugin_gate.py --modules AUR_DECLICK # only one module
    python scripts/ci_plugin_gate.py --strict             # a missing tool fails

Exit codes::

    0   every module was built and the host accepted every one of them
    1   a build failed, or the host rejected a module
    3   a tool this gate needs is not on the machine (nothing was checked)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Where the core shared library is built.  It is a directory of its own, so
#: the gate does not disturb the library another build has staged.
CORE_DIR = ROOT / "plugins" / "abi" / "build_ci"

#: Where the host is built.  The host gate of the test suite looks for the
#: host in this directory.
HOST_DIR = ROOT / "build" / "vst3host"

#: Where the plug-in modules are built.  The host gate of the test suite
#: looks for the modules in this directory.
NATIVE_DIR = ROOT / "build" / "vst3native"

#: The wrapper that puts the Microsoft build tools into the environment.
MSVC = ROOT / "tools" / "msvc.bat"

#: The builder of the core shared library.
ABI_BUILDER = ROOT / "plugins" / "abi" / "build_abi.py"

#: The header the VST3 SDK has to provide, so that the SDK is the one the
#: plug-in build expects and not a directory of the same name.
SDK_MARKER = "pluginterfaces/vst/ivstaudioprocessor.h"

#: The SDK of the repository.
SDK_DIR = ROOT / "vendor" / "vst3sdk"

#: The modules the host gate of the test suite names.  They are built by
#: default, so that the gate of the test suite cannot skip.
DEFAULT_MODULES = "all"

#: The tool that reads the module codes out of the registry.  It is the
#: same tool the build of the family uses.
IDENTITY_TOOL = ROOT / "plugins" / "vst3" / "build_identity.py"

#: The name of the core shared library, per platform.
CORE_NAMES = {"win32": "aurcore_abi.dll", "darwin": "aurcore_abi.dylib"}

#: The name of the directory that holds the shared runtime of the family.
#: The bundles hold the plug-in alone; the core and the interpreter live
#: once, in a directory of this name next to the bundles.
RUNTIME_DIR_NAME = "Auralis Runtime"

#: The tool that stages the shared runtime.  It is the tool the build of
#: the family uses, so the gate and the build agree about the layout.
RUNTIME_TOOL = ROOT / "scripts" / "vst3_runtime.py"

#: The messages that mean "the machine has no compiler", not "the build is
#: broken".  They are the words the builders use.
NO_COMPILER = (
    "the compiler environment could not be set up",
    "no c compiler found",
)


def log(message: str) -> None:
    """Print a line of the running commentary."""
    print(f"[plugin-gate] {message}", flush=True)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
def find_tool(name: str) -> str | None:
    """Find a build tool on PATH, or next to the running interpreter."""
    found = shutil.which(name)
    if found:
        return found

    candidates = []
    try:
        import sysconfig

        scripts = sysconfig.get_path("scripts")
        if scripts:
            candidates.append(Path(scripts) / f"{name}.exe")
            candidates.append(Path(scripts) / name)
    except Exception:  # pragma: no cover - only used to improve the odds
        pass

    local = os.environ.get("LOCALAPPDATA")
    if local:
        for base in Path(local).glob("Python/pythoncore-*/Scripts"):
            candidates.append(base / f"{name}.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def msvc_wrap(command: list[str]) -> list[str]:
    """Put a command into the Microsoft build environment, when there is one."""
    if os.name != "nt":
        return command
    return ["cmd", "/c", str(MSVC), *command]


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command and capture its output."""
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def combined(result: subprocess.CompletedProcess) -> str:
    """The output of a command, both streams together."""
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# the builds
# ---------------------------------------------------------------------------
def build_core() -> tuple[int, str]:
    """Build the core shared library.  Returns (state, message).

    The state is 0 when the library was built, 1 when the build failed and 3
    when the machine has no compiler at all.
    """
    command = [sys.executable, str(ABI_BUILDER)]
    if os.name == "nt":
        # The Microsoft build tools are the compiler of this platform.
        command.append("--msvc")
    command += ["--out", str(CORE_DIR)]

    result = run(msvc_wrap(command))
    output = combined(result)
    if result.returncode == 0:
        return 0, f"the core library was built into {CORE_DIR}"

    lowered = output.lower()
    if any(marker in lowered for marker in NO_COMPILER):
        return 3, "no C compiler was found (the Microsoft build tools are missing)"
    return 1, f"the core library did not build (exit {result.returncode}):\n{output[-2000:]}"


def configure_and_build(
    source: Path,
    build: Path,
    defines: list[str],
    cmake: str,
    ninja: str | None,
    label: str,
) -> tuple[int, str]:
    """Configure and build one CMake project.  Returns (state, message)."""
    build.mkdir(parents=True, exist_ok=True)

    configure = [cmake, "-S", str(source), "-B", str(build)]
    if ninja:
        configure += ["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}"]
    configure += [f"-D{define}" for define in defines]

    result = run(msvc_wrap(configure))
    if result.returncode != 0:
        return 1, f"the configuration of the {label} failed:\n{combined(result)[-2000:]}"

    result = run(msvc_wrap([cmake, "--build", str(build), "--parallel", "2"]))
    if result.returncode != 0:
        return 1, f"the build of the {label} failed:\n{combined(result)[-2000:]}"
    return 0, f"the {label} was built"


def build_host(cmake: str, ninja: str | None) -> tuple[int, str]:
    """Build the independent host."""
    return configure_and_build(
        ROOT / "qa" / "vst3host",
        HOST_DIR,
        ["CMAKE_BUILD_TYPE=Release", f"AURALIS_VST3_SDK_PATH={SDK_DIR}"],
        cmake,
        ninja,
        "host",
    )


def build_module(code: str, cmake: str, ninja: str | None) -> tuple[int, str]:
    """Build one plug-in module."""
    name = CORE_NAMES.get(sys.platform, "aurcore_abi.so")
    core = CORE_DIR / name
    if not core.is_file():
        return 1, f"the core library is missing: {core}"
    return configure_and_build(
        ROOT / "plugins" / "vst3native",
        NATIVE_DIR / code,
        [
            "CMAKE_BUILD_TYPE=Release",
            f"AURALIS_MODULE={code}",
            f"AURALIS_CORE_DLL={core}",
            f"AURALIS_VST3_SDK_PATH={SDK_DIR}",
        ],
        cmake,
        ninja,
        f"module {code}",
    )


# ---------------------------------------------------------------------------
# the host
# ---------------------------------------------------------------------------
def host_executable() -> Path:
    """The path of the host binary of this platform."""
    name = "vst3host.exe" if os.name == "nt" else "vst3host"
    return HOST_DIR / name


def find_module_binary(code: str) -> Path | None:
    """The built binary of one module, if it is there."""
    folder = NATIVE_DIR / code / "stamped"
    if not folder.is_dir():
        return None
    if os.name == "nt":
        found = sorted(folder.rglob("*.vst3.dll"))
    else:
        found = sorted(folder.rglob("*.vst3")) + sorted(folder.rglob("*.so"))
    return found[0] if found else None


def runtime_dir(binary: Path) -> Path | None:
    """The shared runtime that belongs to a bundle, when there is one.

    The rule is the one the plug-in uses: a directory called
    ``Auralis Runtime`` next to the bundle, one level above it or two
    levels above it.
    """
    own = Path(binary).parent
    for base in (own, own.parent, own.parent.parent):
        candidate = base / RUNTIME_DIR_NAME
        if candidate.is_dir():
            return candidate
    return None


def stage_runtime(attempts: int = 6, wait: float = 10.0) -> tuple[int, str]:
    """Stage the shared runtime of the family.

    The build stages the runtime after the build: the bundles hold the
    plug-in alone and the core and the interpreter live once, in the
    runtime directory next to them.  The gate does the same, so the host
    runs against the arrangement that ships.

    The staging moves the runtime out of the bundles, and a file that
    another process still holds cannot be removed, so the staging is
    tried again before the gate gives up.
    """
    command = [sys.executable, str(RUNTIME_TOOL), "stage", "--family", str(NATIVE_DIR)]

    for attempt in range(1, attempts + 1):
        # The runtime directory is not written fresh: the staging compares
        # the copy it finds there with the copy the bundles carry, and
        # replaces the one that is stale.  Writing the directory fresh
        # would copy the home of the interpreter (70 MB) again for nothing.
        result = run(command)
        output = combined(result)
        if result.returncode == 0:
            return 0, "the shared runtime was staged: the bundles hold the plug-in alone"
        if attempt < attempts:
            log(f"the runtime is held by another process, the staging is tried again "
                f"({attempt}/{attempts})")
            time.sleep(wait)

    return 1, f"the shared runtime was not staged cleanly:\n{output[-2000:]}"


def run_host(binary: Path) -> tuple[int, dict, str]:
    """Run the host against a module.  Returns (exit code, report, stderr)."""
    environment = dict(os.environ)

    # The host is told where the shared runtime is.  The bundles hold the
    # plug-in alone, so the core and the interpreter live in the runtime
    # directory next to them; without that the host cannot load the
    # module at all.
    directories = [str(binary.parent)]
    runtime = runtime_dir(binary)
    if runtime is not None:
        directories.append(str(runtime))
    environment["AURALIS_VST3HOST_DEPS"] = os.pathsep.join(directories)

    # The plug-in loads the core out of the shared runtime itself, and the
    # environment tells it where the runtime is.  Without it the plug-in
    # does not find the runtime in the build layout, where it sits three
    # levels above the bundle.
    if runtime is not None:
        environment["AURALIS_RUNTIME_DIR"] = str(runtime)

    result = subprocess.run(
        [str(host_executable()), str(binary), "--tone"],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(ROOT),
        timeout=600,
    )

    start = result.stdout.find("{")
    if start < 0:
        return result.returncode, {}, result.stderr or ""
    try:
        report = json.loads(result.stdout[start:])
    except json.JSONDecodeError:
        return result.returncode, {}, result.stderr or ""
    return result.returncode, report, result.stderr or ""


def check_report(report: dict) -> list[str]:
    """The things the report has to say.  Returns the list of complaints."""
    problems: list[str] = []
    if not report:
        return ["the host wrote no report"]
    if report.get("faults"):
        problems.append(f"the module faulted: {report['faults']}")
    if report.get("ok") is not True:
        problems.append("the host did not accept the module")
    if report.get("exit_code") != 0:
        problems.append(f"the host reported the exit code {report.get('exit_code')}")
    if not report.get("class_count"):
        problems.append("the module published no class")
    if not report.get("parameters"):
        problems.append("the module published no parameter")
    processing = report.get("processing") or {}
    if not processing.get("frames"):
        problems.append("the module processed no audio")
    state = report.get("state") or {}
    if state.get("component_restored") is not True:
        problems.append("the module did not restore its state")
    return problems


# ---------------------------------------------------------------------------
# the host gate of the test suite
# ---------------------------------------------------------------------------
def run_host_gate() -> tuple[int, str]:
    """Run the host gate of the test suite and refuse a skipped test.

    The gate skips itself when the host is not built or when the family is
    only partly built, so the output is checked for a skip: a gate that
    skipped has not checked anything, and it must not pass silently.
    """
    result = run([sys.executable, "-m", "pytest", "tests/test_vst3_host.py", "-q", "-rs"])
    output = combined(result)
    if result.returncode != 0:
        return 1, f"the host gate of the test suite failed:\n{output[-3000:]}"
    if "skipped" in output.lower():
        return 1, f"the host gate of the test suite skipped a test:\n{output[-3000:]}"
    return 0, "the host gate of the test suite passed without a skip"


# ---------------------------------------------------------------------------
# the modules of the registry
# ---------------------------------------------------------------------------
def registry_codes() -> list[str]:
    """Every module code of the registry, in canonical order.

    The codes are read with the same tool the build of the family uses, so
    that the gate and the family never disagree about the list.
    """
    result = run([sys.executable, str(IDENTITY_TOOL), "list", "--format", "cmake"])
    if result.returncode != 0:
        return []

    codes: list[str] = []
    inside = False
    for line in result.stdout.splitlines():
        text = line.strip()
        if text.startswith("set(AURALIS_MODULE_CODES"):
            inside = True
            continue
        if inside:
            if text == ")":
                break
            codes.append(text)
    return codes


# ---------------------------------------------------------------------------
# the checks before the build
# ---------------------------------------------------------------------------
def check_sdk() -> tuple[int, str]:
    """Is the SDK of the repository there?"""
    if (SDK_DIR / SDK_MARKER).is_file():
        return 0, f"the SDK is at {SDK_DIR}"

    # The SDK may be a checkout of its own.  Try to fill it in before the
    # gate gives up, so that a missing checkout is not a missing SDK.
    if shutil.which("git"):
        result = run(["git", "submodule", "update", "--init", "--recursive"])
        if (SDK_DIR / SDK_MARKER).is_file():
            return 0, f"the SDK was fetched into {SDK_DIR}"
        if result.returncode != 0:
            log(f"the submodule update failed:\n{combined(result)[-500:]}")

    return 1, (
        f"the VST3 SDK is missing: {SDK_DIR} does not hold {SDK_MARKER}.\n"
        "The plug-in build needs it; check the checkout of the repository."
    )


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--modules",
        default=DEFAULT_MODULES,
        help=(
            "the module codes to build, comma separated, or 'all' for every "
            "module of the registry (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="a missing tool is a failure, not a skip",
    )
    parser.add_argument(
        "--no-host-gate",
        action="store_true",
        help="do not run the host gate of the test suite",
    )
    args = parser.parse_args(argv)

    if args.modules.strip() == "all":
        modules = registry_codes()
        if not modules:
            log("FAIL: the registry could not be read, so no module is known")
            return 1
    else:
        modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    if not modules:
        print("no module was named", file=sys.stderr)
        return 1
    log(f"modules: {len(modules)}")

    cmake = find_tool("cmake")
    ninja = find_tool("ninja")
    if cmake is None:
        message = "cmake was not found on PATH"
        if args.strict:
            log(f"FAIL: {message}")
            return 1
        log(f"SKIP: {message}")
        return 3
    log(f"cmake: {cmake}")
    log(f"ninja: {ninja or 'not found (the default generator is used)'}")

    state, message = check_sdk()
    if state != 0:
        log(f"FAIL: {message}")
        return 1

    started = time.time()

    state, message = build_core()
    if state != 0:
        if state == 3 and not args.strict:
            log(f"SKIP: {message}")
            return 3
        log(f"FAIL: {message}")
        return 1
    log(f"the core library: {time.time() - started:.1f}s")

    mark = time.time()
    state, message = build_host(cmake, ninja)
    if state != 0:
        log(f"FAIL: {message}")
        return 1
    log(f"the host: {time.time() - mark:.1f}s")

    for code in modules:
        mark = time.time()
        state, message = build_module(code, cmake, ninja)
        if state != 0:
            log(f"FAIL: {message}")
            return 1
        log(f"the module {code}: {time.time() - mark:.1f}s")

    # The runtime is shared by the whole family: the bundles hold the
    # plug-in alone, and the core and the interpreter live once, in the
    # runtime directory next to them.  The gate stages it the way the
    # build does, so the host runs against what ships.
    state, message = stage_runtime()
    if state != 0:
        log(f"FAIL: {message}")
        return 1
    log(message)

    failures = 0
    for code in modules:
        binary = find_module_binary(code)
        if binary is None:
            log(f"FAIL: the module {code} was not built")
            failures += 1
            continue
        mark = time.time()
        code_out, report, stderr = run_host(binary)
        problems = check_report(report)
        if code_out != 0:
            problems.insert(0, f"the host exited with {code_out}")
        if problems:
            log(f"FAIL: the host rejected the module {code}: {'; '.join(problems)}")
            if stderr:
                log(f"the host said:\n{stderr[-2000:]}")
            failures += 1
            continue
        log(
            f"the host accepted {code} in {time.time() - mark:.1f}s "
            f"({report.get('class_count')} class(es), "
            f"{len(report.get('parameters') or [])} parameter(s), "
            f"{(report.get('processing') or {}).get('frames')} frames)"
        )

    if not args.no_host_gate:
        state, message = run_host_gate()
        if state != 0:
            log(f"FAIL: {message}")
            failures += 1
        else:
            log(message)

    total = time.time() - started
    log(f"total: {total:.1f}s")
    if failures:
        log(f"RESULT: FAIL ({failures} problem(s))")
        return 1
    log("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
