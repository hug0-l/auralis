#!/usr/bin/env python3
"""
tools/build_abi.py - build the core C ABI and check that it loads.

The builder itself lives in ``plugins/abi/build_abi.py``.  This wrapper is
the entry point the pipeline uses, because the pipeline has to answer one
more question than the builder can: is the shared library there, and can a
plug-in host load it the way the conformance tests load it?

A build that needs a C compiler that is not on the machine is not an error:
the caller is expected to skip the ABI checks and say so.  That is why
"not built" has its own exit code.

Usage::

    python tools/build_abi.py            # build, then check
    python tools/build_abi.py --build    # build only
    python tools/build_abi.py --check    # check only

Exit codes::

    0   the library is built and loads
    1   the build failed, or the library does not load
    2   the library is not built, so there was nothing to check
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The builder of the core ABI.
BUILDER = ROOT / "plugins" / "abi" / "build_abi.py"

#: Where the builder writes the shared library.
BUILD_DIR = ROOT / "plugins" / "abi" / "build"

#: The name of the shared library, per platform.
LIBRARY_NAMES = {
    "win32": "aurcore_abi.dll",
    "darwin": "aurcore_abi.dylib",
}


def library_path(root: Path = ROOT) -> Path:
    """Return the path of the shared library of this platform."""
    name = LIBRARY_NAMES.get(sys.platform, "aurcore_abi.so")
    return root / "plugins" / "abi" / "build" / name


def build(root: Path = ROOT) -> int:
    """Build the shared library.  Returns the exit code of the builder."""
    if not BUILDER.is_file():
        print(f"the builder is missing: {BUILDER}", file=sys.stderr)
        return 1
    print(f"building the core ABI with {BUILDER}")
    completed = subprocess.run(
        [sys.executable, str(BUILDER)],
        cwd=str(root),
        check=False,
    )
    return completed.returncode


def check(root: Path = ROOT) -> int:
    """Check that the shared library is there and that it loads.

    Returns 0 when the library loads, 1 when it does not load and 2 when
    it was never built.
    """
    path = library_path(root)
    if not path.is_file():
        print(f"the core ABI library has not been built: {path}")
        return 2

    # The library links against the CPython runtime, so its directory has to
    # be discoverable before the library is loaded.
    import sysconfig

    for extra in (
        sysconfig.get_config_var("LIBDIR") or "",
        os.path.dirname(sys.executable),
    ):
        if extra and os.path.isdir(extra):
            try:
                os.add_dll_directory(extra)
            except (AttributeError, OSError):
                pass
    os.environ.setdefault("AURALIS_ROOT", str(root))

    try:
        library = ctypes.CDLL(str(path))
    except OSError as exc:
        print(f"the core ABI library does not load: {exc}", file=sys.stderr)
        return 1

    try:
        library.aur_engine_version.restype = ctypes.c_char_p
        version = library.aur_engine_version()
    except AttributeError as exc:
        print(f"the core ABI library does not export the entry points: {exc}", file=sys.stderr)
        return 1
    print(f"the core ABI library loads: {path} (version {version.decode(errors='replace')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        prog="build_abi",
        description="Build the core C ABI and check that it loads.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the repository root (default: the root of this checkout)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--build",
        action="store_true",
        help="build the library and stop",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="only check the library that is already there",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the library and check that it loads."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    if not args.check:
        code = build(root)
        if code != 0:
            print("the core ABI library could not be built", file=sys.stderr)
            return 1
        if args.build:
            return 0

    return check(root)


if __name__ == "__main__":
    raise SystemExit(main())
