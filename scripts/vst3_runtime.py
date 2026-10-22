#!/usr/bin/env python3
"""
scripts/vst3_runtime.py — the command line of the shared VST3 runtime.

The VST3 family is 48 plug-ins that all need the same two shared
libraries: the core's C ABI (144 kB) and the interpreter it is linked
against (7.8 MB).  Carried inside every bundle, the family is 374 MB of
which 95 % is one file, copied 48 times.

This script owns the arrangement that removes the copy:

    <family root>/
        Auralis Runtime/          the shared runtime, one copy
        <name>.vst3/              the plug-in, alone

Usage
-----

    python scripts/vst3_runtime.py size     --family build/vst3native
    python scripts/vst3_runtime.py plan     --family build/vst3native
    python scripts/vst3_runtime.py stage    --family build/vst3native
    python scripts/vst3_runtime.py restore  --family build/vst3native
    python scripts/vst3_runtime.py verify   --family build/vst3native
    python scripts/vst3_runtime.py manifest --family build/vst3native
    python scripts/vst3_runtime.py deps     --family build/vst3native

``stage`` moves the runtime out of the bundles into the shared directory.
``restore`` puts it back (the build writes it into every bundle again, so
this is what undoes a staging run).  ``verify`` checks that every bundle
can still be loaded with the runtime it is given: the plug-in's imports
have to be satisfied by the bundle or by the runtime.

``deps`` prints the value ``AURALIS_VST3HOST_DEPS`` needs so that the
independent host finds the runtime.

The loader's own behaviour is measured by ``scripts/vst3_loader_probe.cpp``;
the numbers are in plugins/vst3native/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugins" / "vst3native"

if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import make_manifests  # noqa: E402  (the module lives in the plug-in directory)
import stage_runtime  # noqa: E402  (the module lives in the plug-in directory)


def _roots(args: argparse.Namespace) -> list[Path]:
    return [Path(root) for root in args.family]


def _runtime(args: argparse.Namespace, roots: list[Path]) -> Path:
    if args.runtime:
        return Path(args.runtime)
    return stage_runtime.default_runtime_dir(roots)


def command_size(args: argparse.Namespace) -> int:
    """Print the size of the family, and of the runtime it would share."""
    roots = _roots(args)
    family = stage_runtime.family_size(roots)
    runtime = _runtime(args, roots)

    # The runtime members are counted once: the family carries the same
    # file 48 times, and the shared arrangement keeps one copy.
    members: dict[str, int] = {}
    plug_in_total = 0

    for root in roots:
        for bundle in stage_runtime.find_bundles(root):
            for member in stage_runtime.runtime_members(bundle):
                members[member.name] = member.stat().st_size
            plug_in = stage_runtime.bundle_binary(bundle)
            if plug_in is not None:
                plug_in_total += plug_in.stat().st_size

    # The staging moves the runtime out of the bundles into the shared
    # directory, so the copies the family shares are counted there when the
    # bundles do not carry them any more.
    if runtime.is_dir():
        for path in sorted(runtime.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in stage_runtime.LIBRARY_SUFFIXES:
                continue
            members.setdefault(path.name, path.stat().st_size)

    shared = sum(members.values())
    after = plug_in_total + shared

    # What the family would weigh when every bundle carried the runtime
    # itself: the same file, once per bundle.
    alone = plug_in_total + shared * family["bundles"]
    saving = alone - after

    print("family")
    print("  bundles : %d" % family["bundles"])
    print("  bytes   : %d  (%.2f MiB)" % (family["bytes"], family["mib"]))
    print("  shared runtime (one copy of each): %d bytes  (%.2f MiB)"
          % (shared, shared / (1024 * 1024)))
    print("  plug-in binaries                  : %d bytes  (%.2f MiB)"
          % (plug_in_total, plug_in_total / (1024 * 1024)))
    print("  family with one shared runtime    : %d bytes  (%.2f MiB)"
          % (after, after / (1024 * 1024)))
    print("  family with the runtime in every bundle: %d bytes  (%.2f MiB)"
          % (alone, alone / (1024 * 1024)))
    print("  saving                            : %d bytes  (%.2f MiB)"
          % (saving, saving / (1024 * 1024)))
    print("  runtime directory                 : %s" % runtime)

    return 0


def command_plan(args: argparse.Namespace) -> int:
    """Print what a staging run would do, without doing it."""
    roots = _roots(args)
    runtime = _runtime(args, roots)
    report = stage_runtime.stage(roots, runtime, dry_run=True)

    print("runtime directory : %s" % report.runtime_dir)
    print("files to move     : %d" % len(report.moved))
    for item in report.moved[:8]:
        print("  %-46s %10d" % (item["file"], item["bytes"]))
    if len(report.moved) > 8:
        print("  ... and %d more" % (len(report.moved) - 8))

    for problem in report.problems:
        print("PROBLEM: %s" % problem)

    return 1 if report.problems else 0


def command_stage(args: argparse.Namespace) -> int:
    """Move the runtime out of the bundles into the shared directory."""
    roots = _roots(args)
    runtime = _runtime(args, roots)
    report = stage_runtime.stage(roots, runtime, dry_run=args.dry_run)

    print("runtime directory : %s" % report.runtime_dir)
    print("files moved       : %d  (%d bytes)"
          % (len(report.moved), report.moved_bytes))

    for item in report.replaced:
        print("replaced          : %s  (the runtime directory held the copy "
              "of another build; the copy of the bundles is the one that "
              "ships)" % item["file"])

    home = report.home or {}
    if home:
        print("home of the interpreter: %d file(s), %d bytes  (%s)"
              % (home.get("files", 0), home.get("bytes", 0), home.get("source", "")))

    for problem in report.problems:
        print("PROBLEM: %s" % problem)

    for problem in home.get("problems", []):
        print("PROBLEM: %s" % problem)

    if not args.dry_run and not report.problems:
        manifest = stage_runtime.write_runtime_manifest(runtime)
        print("runtime manifest  : %s" % manifest)

    return 1 if report.problems else 0


def command_restore(args: argparse.Namespace) -> int:
    """Put the runtime back into every bundle."""
    roots = _roots(args)
    runtime = _runtime(args, roots)
    report = stage_runtime.restore(roots, runtime)

    print("runtime directory : %s" % report.runtime_dir)
    print("files restored    : %d" % len(report.moved))

    for problem in report.problems:
        print("PROBLEM: %s" % problem)

    return 1 if report.problems else 0


def command_verify(args: argparse.Namespace) -> int:
    """Check that every bundle can be loaded with the runtime it is given."""
    roots = _roots(args)
    runtime = _runtime(args, roots) if args.runtime else None

    problems = stage_runtime.verify(roots, runtime)

    for bundle in [b for root in roots for b in stage_runtime.find_bundles(root)]:
        found = stage_runtime.discover_runtime(bundle, runtime)
        if found is not None and not stage_runtime.home_is_usable(found):
            problems.append("%s: the runtime %s carries no standard library of "
                            "the interpreter, so the plug-in cannot start it"
                            % (bundle.name, found))

    if problems:
        for problem in problems:
            print("PROBLEM: %s" % problem)
        return 1

    bundles = []
    for root in roots:
        bundles.extend(stage_runtime.find_bundles(root))

    groups: dict[str, int] = {}
    where: dict[str, Path] = {}

    for bundle in bundles:
        found = stage_runtime.discover_runtime(bundle, runtime)

        if found is None:
            continue

        if found == bundle:
            key = "inside the bundle"
        else:
            key = str(found)
            where[key] = found

        groups[key] = groups.get(key, 0) + 1

    for key, count in sorted(groups.items(), key=lambda item: item[0]):
        print("OK: %d bundle(s) load with the runtime %s"
              % (count, ("in " + key) if key != "inside the bundle" else key))

    return 0


def command_manifest(args: argparse.Namespace) -> int:
    """Write the manifests of the family."""
    roots = _roots(args)
    runtime = _runtime(args, roots)

    written = []

    if runtime.is_dir():
        written.append(make_manifests.write_runtime_manifest(runtime))

    written.append(make_manifests.write_family_manifest(
        roots, runtime, Path(args.out) if args.out else None))

    for path in written:
        print(path)

    return 0


def command_deps(args: argparse.Namespace) -> int:
    """Print the value AURALIS_VST3HOST_DEPS needs."""
    roots = _roots(args)
    runtime = _runtime(args, roots)

    print(runtime)

    separator = ";" if os.name == "nt" else ":"
    existing = os.environ.get("AURALIS_VST3HOST_DEPS", "")
    if existing:
        print("%s%s" % (runtime, separator + existing))

    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--family", action="append", default=[],
                         help="a directory that holds VST3 bundles (repeatable)")
    common.add_argument("--runtime", default=None,
                         help="the shared runtime directory (default: next to the bundles)")

    parser = argparse.ArgumentParser(
        description="The shared runtime of the VST3 family",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    commands = parser.add_subparsers(dest="command")

    commands.add_parser("size", parents=[common],
                        help="the size of the family, before and after")
    commands.add_parser("plan", parents=[common],
                        help="what the staging would do")
    commands.add_parser("verify", parents=[common],
                        help="check that every bundle still loads")
    commands.add_parser("deps", parents=[common],
                        help="print the value AURALIS_VST3HOST_DEPS needs")

    stage = commands.add_parser("stage", parents=[common],
                                help="move the runtime out of the bundles")
    stage.add_argument("--dry-run", action="store_true", help="only report what would move")

    commands.add_parser("restore", parents=[common],
                        help="put the runtime back into every bundle")

    manifest = commands.add_parser("manifest", parents=[common],
                                   help="write the install manifests")
    manifest.add_argument("--out", default=None, help="where the family manifest is written")

    args = parser.parse_args(argv)

    if not args.family:
        parser.error("--family is required")

    handlers = {
        "size": command_size,
        "plan": command_plan,
        "stage": command_stage,
        "restore": command_restore,
        "verify": command_verify,
        "manifest": command_manifest,
        "deps": command_deps,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.error("a command is required")

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
