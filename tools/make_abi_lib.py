#!/usr/bin/env python3
"""
tools/make_abi_lib.py -- produce the MSVC import library for the core ABI.

The plug-in adapter links against the core through a small C ABI that lives in
a shared library, so it needs the import library that ``lib.exe`` produces
from the DLL's exports.

    python tools/make_abi_lib.py [dll] [outdir]

Uses the dumpbin/lib tools from the build tools; falls back to a hand written
module definition file so the script also works where the tools are absent.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "plugins", "abi", "build")
MSVC_BAT = os.path.join(HERE, "msvc.bat")


def exports_from_dumpbin(dll: str) -> list[str]:
    out = subprocess.run(
        ["cmd", "/c", MSVC_BAT, "dumpbin", "/exports", dll],
        capture_output=True, text=True,
    )
    names: list[str] = []
    for line in out.stdout.splitlines():
        m = re.match(r"^\s*[0-9A-F]+\s+[0-9A-F]+\s+[0-9A-F]+\s+(\S+)", line)
        if m and m.group(1).startswith("aur_"):
            names.append(m.group(1))
    return names


def main() -> int:
    dll = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BUILD, "aurcore_abi.dll")
    outdir = sys.argv[2] if len(sys.argv) > 2 else BUILD
    if not os.path.exists(dll):
        print(f"the shared library is missing: {dll}", file=sys.stderr)
        return 2
    os.makedirs(outdir, exist_ok=True)

    names = exports_from_dumpbin(dll)
    if not names:
        print("no exports found", file=sys.stderr)
        return 1

    def_file = os.path.join(outdir, "aurcore_abi.def")
    with open(def_file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("EXPORTS\n")
        for n in names:
            fh.write(f"{n}\n")

    lib = os.path.join(outdir, "aurcore_abi.lib")
    out = subprocess.run(
        ["cmd", "/c", MSVC_BAT, "lib", f"/def:{def_file}", f"/out:{lib}", "/machine:x64"],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not os.path.exists(lib):
        print(out.stdout)
        print(out.stderr, file=sys.stderr)
        return out.returncode or 1

    print(f"{len(names)} exports -> {lib} ({os.path.getsize(lib)} bytes)")
    for n in names:
        print("   ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
