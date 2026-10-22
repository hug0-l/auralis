#!/usr/bin/env python3
"""
scripts/build_vst3.py — build one VST3 plug-in per module.

The adapter is a template: which module a binary implements is decided at
build time, so every module is the same build with another module code.
The builds run one after another on purpose: a parallel fleet of compilers
would cost more memory than the machine should give up, and the per-build
time is dominated by the configuration anyway.

WHAT THE BUILD LEAVES BEHIND
----------------------------
The family is 48 plug-ins that share one runtime: the core's C ABI and the
interpreter the core is linked against.  The build of a module copies that
runtime into the module's tree, and the copy of the interpreter is 7.5 MB
per module - 360 MB of the same file, 48 times.

The copy is scratch, and the build says why:

* the stamping writes the identity record into a copy of the binary and
  never loads the plug-in, so it does not need a runtime next to it;
* the build's own checks load the plug-in to read its parameters and its
  state, but they never start the engine, so the core is never called and
  the interpreter is never needed;
* the plug-in finds the runtime itself, in the shared directory next to
  the family, and that is where the runtime is put before the first build.

So the runtime is shared from the start, the copies the build makes are
removed with the rest of the scratch, and the tree keeps the bundles and
the shared runtime and nothing else.

Run:  python scripts/build_vst3.py [--only AUR_DECLICK,AUR_DEHUM] [--jobs 2]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "plugins" / "vst3native"
BUILD_ROOT = ROOT / "build" / "vst3native"
IDENTITY_TOOL = ROOT / "plugins" / "vst3" / "build_identity.py"

MSVC = ROOT / "tools" / "msvc.bat"
CMAKE = Path(os.environ.get("LOCALAPPDATA",
                            "")) / "Python" / "pythoncore-3.15-64" / "Scripts" / "cmake.exe"
NINJA = Path(os.environ.get("LOCALAPPDATA",
                            "")) / "Python" / "pythoncore-3.15-64" / "Scripts" / "ninja.exe"

#: The directory that holds the shared runtime of the family.  The name is
#: the one the plug-in looks for, so it is part of the contract between
#: the build and the plug-in.
RUNTIME_DIR_NAME = "Auralis Runtime"

# The core library the plug-in carries.  The build tree of the core is
# produced by plugins/abi/build_abi.py.  The canonical output is the one
# the release ships and the one the ABI gate builds, so the family and the
# package always carry the same core.
CORE_DLL = ROOT / "plugins" / "abi" / "build" / "aurcore_abi.dll"
CORE_HOME = ROOT / "plugins" / "abi" / "build"
SDK = ROOT / "vendor" / "vst3sdk"

# The staging of the shared runtime lives in the plug-in directory: it owns
# the layout of the family, and the build uses it to put the runtime in
# place and to take the copies out of the bundles again.
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import stage_runtime  # noqa: E402  (the module lives in the plug-in directory)


def module_codes() -> list[str]:
    """The module codes of the registry, in canonical order."""
    result = subprocess.run([sys.executable, str(IDENTITY_TOOL), "list", "--format", "cmake"],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"could not read the module registry:\n{result.stderr}")

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


def run(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run a command inside the Visual C++ environment."""
    wrapped = ["cmd", "/c", str(MSVC)] + [str(c) for c in command]
    result = subprocess.run(wrapped, capture_output=True, text=True, cwd=cwd)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def build_one(module: str, jobs: int) -> tuple[bool, str]:
    """Configure and build one module.  Returns (ok, message)."""
    build_dir = BUILD_ROOT / module
    build_dir.mkdir(parents=True, exist_ok=True)

    configure = [
        CMAKE, "-S", SOURCE, "-B", build_dir, "-G", "Ninja",
        f"-DAURALIS_MODULE={module}",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_MAKE_PROGRAM={NINJA}",
        f"-DAURALIS_CORE_DLL={CORE_DLL}",
        f"-DAURALIS_VST3_SDK_PATH={SDK}",
    ]
    code, output = run(configure, ROOT)
    if code != 0:
        return False, f"configure failed: {output[-400:]}"

    code, output = run([CMAKE, "--build", build_dir, "--parallel", str(jobs)], ROOT)
    if code != 0:
        return False, f"build failed: {output[-400:]}"

    return True, "built"


# ---------------------------------------------------------------------------
#  the shared runtime
# ---------------------------------------------------------------------------
#
#  The family shares one runtime: the core's C ABI, the interpreter the core
#  is linked against and the home of that interpreter (the standard library
#  and the packages the core imports).  The runtime is put in place before
#  the first module is built, so the plug-ins have a runtime to find while
#  they are built and the copies the build makes into the module trees are
#  scratch.

def shared_runtime_dir() -> Path:
    """The directory that holds the shared runtime of the family."""
    return BUILD_ROOT / RUNTIME_DIR_NAME


def _digest(path: Path) -> str:
    """The SHA-256 of a file, or an empty string when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def copy_if_different(source: Path, destination: Path) -> bool:
    """Copy a file unless the destination already holds the same one.

    The two files are compared by their digest and not by their size: a
    rebuilt core is very often exactly as big as the one it replaces --
    the core was 207360 bytes before and after the change of 21 October --
    so the runtime kept the old copy and the family kept running against
    a core that is not the one the plug-ins were built against.

    That is how the defect of the plug-in family survived a rebuild of
    the core: the core was rebuilt with the fix in
    ``plugins/abi/aurcore_abi.cpp`` (the search for the core package never
    left the directory the core was loaded from), the runtime was told
    about the new core, and the runtime answered "same size, nothing to
    do".  The plug-in then kept passing the audio through unchanged in
    every host whose working directory is not the project.
    """
    try:
        if destination.is_file() and _digest(source) == _digest(destination):
            return False
    except OSError:
        pass

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def prepare_shared_runtime() -> tuple[bool, str]:
    """Put the shared runtime of the family in place.

    The runtime is built from the canonical build of the core: the core's
    shared library, the interpreter it is linked against and the home of
    that interpreter.  The home is staged by the module that owns the
    layout of the runtime, so the family and the package carry the same
    standard library.

    Returns (ok, message).
    """
    if not CORE_DLL.is_file():
        return False, "the core was not built: %s" % CORE_DLL

    interpreters = sorted(CORE_HOME.glob("python3*.dll"))
    if not interpreters:
        return False, "the interpreter of the core was not found in %s" % CORE_HOME

    runtime = shared_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)

    copy_if_different(CORE_DLL, runtime / CORE_DLL.name)

    wanted = set()
    for interpreter in interpreters:
        wanted.add(interpreter.name)
        copy_if_different(interpreter, runtime / interpreter.name)

    # An interpreter of another version would not match the standard
    # library that travels with it, so it does not stay in the runtime.
    for path in sorted(runtime.glob("python3*.dll")):
        if path.name not in wanted:
            path.unlink()

    report = stage_runtime.stage_interpreter_home(runtime)
    problems = list(report.get("problems", []))

    if problems:
        return False, "; ".join(problems)

    return True, "the shared runtime is in %s" % runtime


def stage_shared_runtime(attempts: int = 6, wait: float = 10.0) -> int:
    """Stage the shared runtime of the family.

    The staging moves the runtime out of the bundles, and a file that
    another process still holds cannot be removed.  That happens when a
    host has one of the bundles loaded, so the staging is tried again a
    few times before the build gives up.

    Returns the exit code of the staging.
    """
    command = [
        sys.executable,
        str(ROOT / "scripts" / "vst3_runtime.py"),
        "stage",
        "--family",
        str(BUILD_ROOT),
    ]

    code = 1
    for attempt in range(1, attempts + 1):
        # The runtime directory is not written fresh: the staging compares
        # the copy it finds there with the copy the bundles carry, and
        # replaces the one that is stale.  Writing the directory fresh
        # would copy the home of the interpreter (70 MB) again for nothing.
        code = subprocess.run(command, cwd=str(ROOT)).returncode
        if code == 0:
            return 0
        if attempt < attempts:
            print(
                f"  the runtime is held by another process; "
                f"the staging is tried again ({attempt}/{attempts}) ...",
                flush=True,
            )
            time.sleep(wait)
    return code


# ---------------------------------------------------------------------------
#  the scratch
# ---------------------------------------------------------------------------
#
#  The tree of a module holds the bundle and the scratch of the build: the
#  object files, the configure log, the compiler probes and the copy of the
#  core and of the interpreter that the build puts next to the plug-in.
#
#  Only the bundle is needed, so the rest is removed when the module is
#  built.  Two rules keep the cleanup safe:
#
#  * the binary of the plug-in is never touched, and
#  * a bundle whose binary does not carry an identity record is removed as
#    well, because it is a bundle the build did not finish.
#
#  A cleanup that is interrupted therefore leaves a tree that still holds
#  every plug-in that was built, and never a plug-in that is half written.

_IDENTITY_MARKER: bytes | None = None


def identity_marker() -> bytes:
    """The marker the identity record of a plug-in starts with."""
    global _IDENTITY_MARKER

    if _IDENTITY_MARKER is None:
        path = ROOT / "plugins" / "identity.py"
        spec = importlib.util.spec_from_file_location("auralis_plugin_identity", path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ImportError("cannot load %s" % path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _IDENTITY_MARKER = module.IDENTITY_MAGIC

    return _IDENTITY_MARKER


def bundle_plug_in(bundle: Path) -> Path | None:
    """The binary of the plug-in inside a bundle.

    The build writes it as "<name>.vst3.dll".  When the name is not the one
    the build uses, the file that nothing else in the bundle imports is the
    plug-in.
    """
    candidates = sorted(path for path in bundle.iterdir()
                        if path.is_file() and path.name.lower().endswith(".vst3.dll"))

    if candidates:
        return candidates[0]

    return stage_runtime.bundle_binary(bundle)


def clean_bundle(bundle: Path) -> tuple[bool, int]:
    """Take the runtime out of a bundle.  Returns (complete, bytes removed).

    A bundle is complete when its binary carries the identity record: the
    stamp writes that record into a copy of the binary, so a binary without
    it is one the build was interrupted in the middle of.
    """
    plug_in = bundle_plug_in(bundle)
    if plug_in is None:
        return False, 0

    try:
        data = plug_in.read_bytes()
    except OSError:
        return False, 0

    if identity_marker() not in data:
        return False, 0

    removed = 0

    # The runtime the build copied into the bundle is scratch: the family
    # shares one runtime, and it lives next to the bundles.
    for member in sorted(bundle.iterdir()):
        if not member.is_file():
            continue
        if member.suffix.lower() not in stage_runtime.LIBRARY_SUFFIXES:
            continue
        if member == plug_in:
            continue
        try:
            removed += member.stat().st_size
            member.unlink()
        except OSError:
            pass

    return True, removed


def remove_path(path: Path) -> tuple[int, int]:
    """Remove a file or a directory.  Returns (files removed, bytes freed)."""
    files = 0
    size = 0

    if path.is_file():
        try:
            size = path.stat().st_size
            path.unlink()
            files = 1
        except OSError:
            pass
        return files, size

    if path.is_dir():
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            files += 1
            try:
                size += item.stat().st_size
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)

    return files, size


def prune_module(module: str, scratch: bool = True) -> dict:
    """Remove the scratch of one module's build tree.

    The bundle stays, the copy of the runtime that the build put into it
    goes, and a bundle the build did not finish is removed.  With
    ``scratch`` false the build tree is kept (a module that failed is
    worth looking at), and only the bundles are cleaned.

    The report says how much was removed.
    """
    report = {"files": 0, "bytes": 0, "bundles": 0, "unfinished": 0}

    build_dir = BUILD_ROOT / module
    if not build_dir.is_dir():
        return report

    stamped = build_dir / "stamped"

    if scratch:
        for child in sorted(build_dir.iterdir()):
            if child == stamped:
                continue
            files, size = remove_path(child)
            report["files"] += files
            report["bytes"] += size

    if not stamped.is_dir():
        return report

    for child in sorted(stamped.iterdir()):
        if not (child.is_dir() and child.suffix.lower() == ".vst3"):
            files, size = remove_path(child)
            report["files"] += files
            report["bytes"] += size
            continue

        complete, removed = clean_bundle(child)

        if not complete:
            files, size = remove_path(child)
            report["files"] += files
            report["bytes"] += size
            report["unfinished"] += 1
            continue

        report["bundles"] += 1
        report["bytes"] += removed

    return report


def clean_module(module: str, scratch: bool = True) -> dict:
    """Prune a module and report what was removed.

    A cleanup that fails is reported and does not stop the build: the
    plug-in is built either way, and a tree that is too big is a smaller
    problem than a build that does not finish.
    """
    try:
        return prune_module(module, scratch=scratch)
    except OSError as error:
        print("    the scratch of %s could not be removed: %s" % (module, error),
              flush=True)
        return {"files": 0, "bytes": 0, "bundles": 0, "unfinished": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build one VST3 plug-in per module")
    ap.add_argument("--only", default="", help="a comma separated list of module codes")
    ap.add_argument("--jobs", type=int, default=2, help="compiler jobs per build")
    ap.add_argument("--keep-going", action="store_true",
                    help="carry on after a module fails")
    ap.add_argument("--keep-scratch", action="store_true",
                    help="keep the scratch of the build (the object files, the "
                         "configure log and the copy of the interpreter)")
    args = ap.parse_args(argv)

    codes = module_codes()
    if args.only:
        wanted = [c.strip() for c in args.only.split(",") if c.strip()]
        codes = [c for c in codes if c in wanted]

    if not codes:
        print("no modules to build")
        return 1

    print(f"building {len(codes)} plug-in(s) with {args.jobs} job(s) each")

    # The runtime is shared by the whole family: the bundles hold the
    # plug-in alone, and the core and the interpreter live once, in the
    # runtime directory next to them.  It is put in place before the first
    # build, so the copies the build makes into the module trees are
    # scratch that the cleanup removes.
    ready, message = prepare_shared_runtime()
    if not ready:
        print("the shared runtime could not be prepared: %s" % message, file=sys.stderr)
        return 1
    print(message)

    started = time.time()
    built: list[str] = []
    failed: list[tuple[str, str]] = []
    removed_files = 0
    removed_bytes = 0
    interrupted = False
    current = ""

    try:
        for index, module in enumerate(codes, start=1):
            current = module
            module_started = time.time()
            ok, message = build_one(module, args.jobs)
            elapsed = time.time() - module_started

            if ok:
                built.append(module)
                print(f"[{index}/{len(codes)}] {module:<22} built in {elapsed:6.1f}s", flush=True)

                if not args.keep_scratch:
                    report = clean_module(module)
                    removed_files += report["files"]
                    removed_bytes += report["bytes"]
            else:
                failed.append((module, message))
                print(f"[{index}/{len(codes)}] {module:<22} "
                      f"FAILED after {elapsed:6.1f}s", flush=True)
                print("    " + message.replace("\n", "\n    ")[:600], flush=True)

                if not args.keep_scratch:
                    # A bundle the build did not finish is removed, so the
                    # tree never holds a plug-in that is half written.  The
                    # build tree itself is kept: a module that failed is
                    # worth looking at.
                    report = clean_module(module, scratch=False)
                    removed_files += report["files"]
                    removed_bytes += report["bytes"]

                if not args.keep_going:
                    break
    except KeyboardInterrupt:
        interrupted = True
        print("\nthe build was interrupted", flush=True)

        # The module the build was in the middle of is cleaned as well: its
        # scratch is of no use, and a bundle it did not finish must not
        # stay in the tree.
        if current and not args.keep_scratch:
            report = clean_module(current)
            removed_files += report["files"]
            removed_bytes += report["bytes"]
    except BaseException:
        # The family is left in the shape it ships in, even when the build
        # died: the staging runs before the error travels on.
        if built:
            stage_shared_runtime()
        raise

    total = time.time() - started
    print(f"\n{len(built)}/{len(codes)} built in {total / 60:.1f} min")

    if not args.keep_scratch:
        print("scratch removed: %d file(s), %.1f MiB"
              % (removed_files, removed_bytes / (1024 * 1024)))

    # The runtime is shared by the whole family: the bundles hold the
    # plug-in alone, and the core and the interpreter live once, in the
    # runtime directory next to them.  The staging runs after the build,
    # because the build copies the runtime into every bundle (the build's
    # own checks load the module the way a host loads it).
    #
    # The staging compares the copies first: a family whose bundles do not
    # carry the same core is reported, not silently deduplicated.
    staged = 0
    if built:
        staged = stage_shared_runtime()

        if staged != 0:
            print("the runtime was not staged cleanly; see the report above",
                  file=sys.stderr)

    if failed:
        print("failed:")
        for module, message in failed:
            print(f"  {module}: {message[:200]}")
        return 1

    if interrupted or staged != 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
