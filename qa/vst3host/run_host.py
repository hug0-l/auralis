#!/usr/bin/env python3
"""
qa/vst3host/run_host.py - build the Auralis VST3 host and run it.

The host itself is a C++ program (qa/vst3host/main.cpp).  This script is
the thin wrapper around it: it configures and builds the host, finds a
built VST3 module, and runs the host against it.

What it does, in order:

  1. configure and build the host into build/vst3host
  2. find a module: the one given on the command line, or the first
     *.vst3 / *.vst3.dll under build/vst3*/ and plugins/vst3/build/
  3. run the host against it and print what it says

With --all the host is run against every module of the built family, in
parallel, and the report says how long the whole family took.

If no module has been built yet, the host is still built and the host is
run without an argument, so that its "no module given" error is shown.

usage:
    python qa/vst3host/run_host.py [module] [options]

options:
    --no-build        do not configure or build the host, just run it
    --build-dir DIR   where the host is built (default: build/vst3host)
    --tone            additionally process a 1 kHz tone
    --all             run the host against every built module
    --workers N       how many modules to run at the same time (default: 8)
    --list            only list the modules that were found
    --keep-json FILE  write the host's JSON report to FILE
    -h, --help        this text

exit codes: the host's exit code (0 = everything worked, 1 = the module
could not be loaded or instantiated, 2 = a step failed).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

DEFAULT_BUILD_DIR = REPO_ROOT / "build" / "vst3host"
HOST_EXE_NAME = "vst3host.exe" if os.name == "nt" else "vst3host"

# Where the plug-ins are looked for, relative to the repository root.
MODULE_SEARCH_ROOTS = ("build", "plugins/vst3/build")

# The Auralis plug-ins link against the core's C ABI shared library, which
# in turn links against the Python runtime.  The host needs to be able to
# find both, so their directories are handed to it.
DEPENDENCY_HINTS = (
    "plugins/abi/build",
    "plugins/abi/build/_stage",
)

#: How many modules are run at the same time.
WORKERS = 8

#: How long one module may take before the host is killed.
TIMEOUT = 300


def log(message: str) -> None:
    print(f"[run_host] {message}", file=sys.stderr, flush=True)


def find_tool(name: str) -> str | None:
    """Find cmake or ninja, on PATH or in the local Python scripts folder."""
    found = shutil.which(name)
    if found:
        return found

    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Python" / "pythoncore-3.15-64" / "Scripts" / f"{name}.exe"
        if candidate.exists():
            return str(candidate)

    for base in Path(local or "").glob("Python/pythoncore-*/Scripts") if local else []:
        candidate = base / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return None


def run(command: list[str], cwd: Path | None = None) -> int:
    """Run a command, in the MSVC environment when there is one.

    The command's own output is sent to stderr, so that stdout carries
    nothing but the host's JSON report.
    """
    msvc_bat = REPO_ROOT / "tools" / "msvc.bat"
    if os.name == "nt" and msvc_bat.exists():
        command = ["cmd", "/c", str(msvc_bat), *command]
    log("$ " + " ".join(command))
    return subprocess.call(command, cwd=str(cwd) if cwd else None, stdout=sys.stderr)


def build_host(build_dir: Path) -> Path:
    """Configure and build the host.  Returns the path of the executable."""
    cmake = find_tool("cmake")
    if cmake is None:
        raise SystemExit("run_host: cmake was not found on PATH")

    build_dir.mkdir(parents=True, exist_ok=True)

    configure = [
        cmake,
        "-S",
        str(HERE),
        "-B",
        str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    generator = find_tool("ninja")
    if generator:
        configure += ["-G", "Ninja"]

    if run(configure) != 0:
        raise SystemExit("run_host: configuring the host failed")

    # The linker can lose the race with a virus scanner that holds the
    # freshly written executable for a moment.  A second try is enough.
    for attempt in range(3):
        if run([cmake, "--build", str(build_dir)]) == 0:
            break
        if attempt == 2:
            raise SystemExit("run_host: building the host failed")
        log("the build failed, trying again in a moment")
        time.sleep(5)

    executable = build_dir / HOST_EXE_NAME
    if not executable.exists():
        candidates = sorted(build_dir.glob("vst3host*"))
        candidates = [c for c in candidates if c.is_file()]
        if not candidates:
            raise SystemExit(f"run_host: the host was not built into {build_dir}")
        executable = candidates[0]
    return executable


def bundle_has_binary(bundle: Path) -> bool:
    """True when a .vst3 bundle really holds a shared library."""
    if not bundle.is_dir():
        return True
    for child in bundle.rglob("*"):
        if child.is_file() and child.suffix.lower() in (".vst3", ".dll", ".so", ".dylib"):
            return True
    return False


def find_modules() -> list[Path]:
    """Every VST3 module in the tree, best candidate first."""
    found: list[Path] = []
    for root in MODULE_SEARCH_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() != ".vst3":
                continue
            if not bundle_has_binary(path):
                # An empty bundle: the build has not produced the binary.
                continue
            found.append(path)

    # A bundle that holds a binary is more useful than a bare shared library.
    bundles = [p for p in found if p.is_dir()]
    libraries = [p for p in found if p.is_file()]
    return bundles + libraries


def family_modules() -> list[tuple[str, Path]]:
    """Every module of the built family, by module code.

    The family lives in one folder per module under build/vst3native.
    """
    base = REPO_ROOT / "build" / "vst3native"
    if not base.is_dir():
        return []

    found: list[tuple[str, Path]] = []
    for code_dir in sorted(base.iterdir()):
        if not code_dir.is_dir() or not code_dir.name.startswith("AUR_"):
            continue
        binaries = sorted((code_dir / "stamped").rglob("*.vst3.dll"))
        if binaries:
            found.append((code_dir.name, binaries[0]))
    return found


def dependency_directories() -> list[str]:
    """The directories the host should search for the module's dependencies."""
    directories: list[str] = []
    for hint in DEPENDENCY_HINTS:
        candidate = REPO_ROOT / hint
        if candidate.is_dir():
            directories.append(str(candidate))

    # The Python runtime the core's C ABI links against.
    try:
        import sysconfig

        bindir = sysconfig.get_config_var("BINDIR")
        if bindir and Path(bindir).is_dir():
            directories.append(bindir)
    except Exception:  # pragma: no cover - only used to improve the odds
        pass

    seen: set[str] = set()
    unique: list[str] = []
    for directory in directories:
        if directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def host_environment() -> dict[str, str]:
    """The environment the host runs in."""
    environment = dict(os.environ)
    dependencies = dependency_directories()
    if dependencies:
        separator = ";" if os.name == "nt" else ":"
        existing = environment.get("AURALIS_VST3HOST_DEPS", "")
        environment["AURALIS_VST3HOST_DEPS"] = separator.join(
            dependencies + ([existing] if existing else [])
        )
    return environment


def module_dependencies(module: Path) -> list[str]:
    """The directories that hold the libraries one module needs.

    A plug-in links against the core's C ABI shared library, which links
    against the Python runtime.  Where those live depends on how the
    family was built: inside the bundle, in the shared runtime next to
    it, or in the runtime's own folder.  The host is told all of them.
    """
    directories = [str(module.parent)]

    # The shared runtime of the family: the bundles hold the plug-in
    # alone, so the core and the interpreter live in a directory of their
    # own next to them.
    for base in (module.parent, module.parent.parent, module.parent.parent.parent):
        candidate = base / "Auralis Runtime"
        if candidate.is_dir():
            directories.append(str(candidate))
            break

    # The folder the module was built in: its "bin" holds the shared
    # libraries the plug-in needs.
    candidate = module.parent.parent.parent / "bin"
    if candidate.is_dir():
        directories.append(str(candidate))

    return directories


def runtime_directory(module: Path) -> Path | None:
    """The shared runtime of the family, if it is there."""
    for base in (module.parent, module.parent.parent, module.parent.parent.parent):
        candidate = base / "Auralis Runtime"
        if candidate.is_dir():
            return candidate
    return None


def run_one(executable: Path, module: Path, tone: bool) -> tuple[int, str, str]:
    """Run the host against one module.  Returns the exit code and the two
    streams.  A host that takes too long is killed, so nothing is left
    behind."""
    environment = host_environment()
    separator = ";" if os.name == "nt" else ":"
    environment["AURALIS_VST3HOST_DEPS"] = separator.join(
        module_dependencies(module) + [environment.get("AURALIS_VST3HOST_DEPS", "")]
    )

    # The plug-in looks for the shared runtime too.
    shared = runtime_directory(module)
    if shared is not None:
        environment["AURALIS_RUNTIME_DIR"] = str(shared)

    command = [str(executable), str(module)]
    if tone:
        command.append("--tone")

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        creationflags=creation_flags,
    )

    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return 2, "", f"the host did not finish within {TIMEOUT} s and was killed"

    return process.returncode, stdout, stderr


def run_family(executable: Path, tone: bool, workers: int) -> int:
    """Run the host against every module of the family."""
    modules = family_modules()
    if not modules:
        log("no module of the family is built")
        return 1

    log(f"running the host against {len(modules)} module(s) with {workers} worker(s)")

    started = time.perf_counter()

    def one(item: tuple[str, Path]) -> tuple[str, int, str, str, float]:
        code, module = item
        begin = time.perf_counter()
        exit_code, stdout, stderr = run_one(executable, module, tone)
        return code, exit_code, stdout, stderr, time.perf_counter() - begin

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, modules))

    elapsed = time.perf_counter() - started

    failures = 0
    for code, exit_code, stdout, stderr, seconds in results:
        error = ""
        start = stdout.find("{")
        if start >= 0:
            try:
                report = json.loads(stdout[start:])
                error = report.get("error") or ""
            except ValueError:
                error = "the report was not readable"
        else:
            error = "the host wrote no report"

        status = "ok" if exit_code == 0 else "FAIL"
        if exit_code != 0:
            failures += 1

        print(f"{status:4s} {code:22s} {seconds:5.2f}s  {error}")

    print(
        f"\n{len(modules)} module(s) in {elapsed:.1f} s, {failures} failure(s)"
    )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module", nargs="?", help="the module to load")
    parser.add_argument("--no-build", action="store_true", help="do not build the host")
    parser.add_argument("--build-dir", default=str(DEFAULT_BUILD_DIR),
                        help="where the host is built")
    parser.add_argument("--tone", action="store_true", help="also process a 1 kHz tone")
    parser.add_argument("--all", action="store_true",
                        help="run the host against every built module")
    parser.add_argument("--workers", type=int, default=WORKERS,
                        help="how many modules to run at once")
    parser.add_argument("--list", action="store_true", help="only list the modules that were found")
    parser.add_argument("--keep-json", help="write the host's JSON report to FILE")
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()

    if args.list:
        modules = find_modules()
        print(json.dumps({"modules": [str(m) for m in modules]}, indent=2))
        return 0

    if args.no_build:
        executable = build_dir / HOST_EXE_NAME
        if not executable.exists():
            raise SystemExit(f"run_host: no host in {build_dir}")
    else:
        executable = build_host(build_dir)

    if args.all:
        return run_family(executable, args.tone, max(1, args.workers))

    module = Path(args.module) if args.module else None
    if module is None:
        modules = find_modules()
        if not modules:
            log("no VST3 module was found in the tree; running the host without one")
            log("searched: " + ", ".join(str(REPO_ROOT / r) for r in MODULE_SEARCH_ROOTS))
            module = None
        else:
            module = modules[0]
            log(f"using module: {module}")

    command = [str(executable)]
    if module is not None:
        command.append(str(module))
    if args.tone:
        command.append("--tone")

    environment = host_environment()

    log("$ " + " ".join(command))
    result = subprocess.run(command, env=environment, capture_output=True, text=True)

    if result.stdout:
        sys.stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if result.stderr:
        sys.stderr.write(result.stderr)
        if not result.stderr.endswith("\n"):
            sys.stderr.write("\n")
    sys.stdout.flush()
    sys.stderr.flush()

    if args.keep_json and result.stdout:
        Path(args.keep_json).write_text(result.stdout, encoding="utf-8")
        log(f"wrote the JSON report to {args.keep_json}")

    if module is None:
        log("the host reported the expected 'no module given' error")
        return result.returncode

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
