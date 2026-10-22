#!/usr/bin/env python3
"""
plugins/vst3/make_manifests.py — write the VST3 manifest of every bundle.

A VST3 bundle carries a manifest (``Contents/Resources/moduleinfo.json``)
that tells a host what the module contains, so the host can list it without
loading it.  The manifest is produced by loading the module, which means it
can only be written once the bundle is complete: the core library, the
interpreter and the core package have to be inside it first.

That is why this runs as a separate pass over the finished bundles instead
of as a step of the link: the link happens before the runtime is staged.

Run:  python plugins/vst3/make_manifests.py --helper <tool> --bundle <dir> ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def manifest_path(bundle: Path) -> Path:
    """Where the manifest of a bundle belongs."""
    return bundle / "Contents" / "Resources" / "moduleinfo.json"


def write_manifest(helper: Path, bundle: Path, version: str) -> tuple[bool, str]:
    """Write the manifest of one bundle.  Returns (ok, message)."""
    target = manifest_path(bundle)
    target.parent.mkdir(parents=True, exist_ok=True)

    command = [str(helper), "-create", "-version", version,
               "-path", str(bundle), "-output", str(target)]
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"exit {result.returncode}"

    if not target.is_file() or target.stat().st_size == 0:
        return False, "the manifest was not written"

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"the manifest is not valid JSON: {exc}"

    classes = payload.get("Classes") or []
    if not classes:
        return False, "the manifest lists no class"

    names = ", ".join(str(c.get("Name", "?")) for c in classes)
    return True, f"{len(classes)} class(es): {names}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the VST3 manifests")
    ap.add_argument("--helper", required=True, help="the manifest helper tool")
    ap.add_argument("--bundle", action="append", default=[],
                    help="a bundle directory (may be given more than once)")
    ap.add_argument("--version", default="0.1.0", help="the version to record")
    args = ap.parse_args(argv)

    helper = Path(args.helper)
    if not helper.is_file():
        print(f"the manifest helper is missing: {helper}", file=sys.stderr)
        return 2

    bundles = [Path(b) for b in args.bundle]
    if not bundles:
        print("no bundles were given", file=sys.stderr)
        return 2

    failures = 0
    for bundle in bundles:
        if not bundle.is_dir():
            print(f"[SKIP] {bundle} is not a bundle directory")
            failures += 1
            continue

        ok, message = write_manifest(helper, bundle, args.version)
        if ok:
            print(f"[ OK ] {bundle.name}: {message}")
        else:
            print(f"[FAIL] {bundle.name}: {message}", file=sys.stderr)
            failures += 1

    print(f"{len(bundles) - failures}/{len(bundles)} manifests written")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
