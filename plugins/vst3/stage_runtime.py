#!/usr/bin/env python3
"""
plugins/vst3/stage_runtime.py — make a plug-in bundle self-contained.

A VST3 bundle has to carry everything it needs: the host loads the module
from wherever the user put it, with no search path of ours to rely on.  The
Auralis module needs three things besides itself:

    <bundle>/Contents/x86_64-win/aurcore_abi.dll     the core C ABI
    <bundle>/Contents/x86_64-win/python3XX.dll        the interpreter the
                                                       ABI library is linked
                                                       against
    <bundle>/Contents/Python/aurcore/                 the core package

The interpreter looks for its standard library where it was told to, and
the core package is found through the directory the ABI library itself was
loaded from, so the layout above is the one the adapter expects.

Run:  python plugins/vst3/stage_runtime.py --bundle <bundle dir> [...]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd~")


def copy_tree(source: Path, destination: Path) -> int:
    """Copy a package, leaving the caches out."""
    if not source.is_dir():
        return 0

    count = 0
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        relative = Path(root).relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)

        for name in files:
            if name.endswith(SKIP_SUFFIXES):
                continue
            shutil.copy2(Path(root) / name, target_dir / name)
            count += 1
    return count


def find_interpreter(runtime_dir: Path) -> Path | None:
    """Find the interpreter library inside the runtime directory."""
    if not runtime_dir.is_dir():
        return None

    for name in sorted(os.listdir(runtime_dir)):
        if name.lower().startswith(("python3", "libpython3")) and \
                name.lower().endswith((".dll", ".so", ".dylib")):
            return runtime_dir / name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage the runtime into a plug-in bundle")
    ap.add_argument("--bundle", required=True, help="the .vst3 bundle directory")
    ap.add_argument("--runtime-dir", required=True,
                    help="directory holding the core ABI library and the interpreter")
    ap.add_argument("--package-dir", required=True,
                    help="the core package directory (aurcore)")
    ap.add_argument("--arch", default="x86_64-win",
                    help="the bundle's architecture directory")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    runtime = Path(args.runtime_dir)
    package = Path(args.package_dir)

    if not bundle.is_dir():
        print(f"not a bundle directory: {bundle}", file=sys.stderr)
        return 1

    binary_dir = bundle / "Contents" / args.arch
    python_dir = bundle / "Contents" / "Python"
    binary_dir.mkdir(parents=True, exist_ok=True)
    python_dir.mkdir(parents=True, exist_ok=True)

    staged = 0

    # 1. the core ABI library
    #
    # The staging directory holds the library that was built last.  It is
    # preferred over the installed one, because the installed copy may be an
    # older build: a library that is currently loaded cannot be replaced on
    # Windows, so the build leaves the fresh copy aside.
    runtime_candidates = [runtime / "_stage", runtime]

    for name in ("aurcore_abi.dll", "aurcore_abi.so", "aurcore_abi.dylib"):
        source = None
        for folder in runtime_candidates:
            candidate = folder / name
            if candidate.is_file():
                source = candidate
                break
        if source is not None:
            shutil.copy2(source, binary_dir / name)
            print(f"staged {name} -> {binary_dir} (from {source.parent})")
            staged += 1
            break
    else:
        print(f"note: no core ABI library in {runtime}", file=sys.stderr)

    # 2. the interpreter
    interpreter = find_interpreter(runtime)
    if interpreter is not None:
        shutil.copy2(interpreter, binary_dir / interpreter.name)
        print(f"staged {interpreter.name} -> {binary_dir}")
        staged += 1
    else:
        print(f"note: no interpreter library in {runtime}", file=sys.stderr)

    # 3. the core package
    files = copy_tree(package, python_dir / package.name)
    print(f"staged {files} package files -> {python_dir / package.name}")
    staged += files

    print(f"bundle is self-contained: {staged} files staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
