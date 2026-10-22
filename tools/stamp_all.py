#!/usr/bin/env python3
"""
tools/stamp_all.py — stamp and verify the identity of every built Auralis plug-in.

The adapter layer ships **one template binary per plug-in format**; the module a
binary implements is decided at build time by stamping an identity record into a
reserved slot inside the binary (see ``plugins/README.md``).

This tool walks the whole plug-in family:

* it reads the canonical module table (``aurcore/schema/params.tsv``, mirrored by
  ``aurcore/schema/generated.py``) and derives one identity per module through
  ``plugins/identity.py``;
* it finds the built plug-ins (``build/vst3*``, ``plugins/vst3native/build*``
  and anything given with ``--search``);  a plug-in is a **bundle**: a
  directory named ``<name>.vst3`` that holds the binary, or — where the
  format uses a single file — a file named ``<name>.vst3``;  the binaries
  that are not in a bundle are build intermediates and are left alone;
* it stamps every binary with the identity of the module its *name* identifies,
  using ``plugins/vst3/build_identity.py``;
* it reads every stamped binary back and verifies it: the identity marker occurs
  **exactly once**, the record **parses**, the **module code matches the name of
  the binary**, the **VST3 class id is the expected one** and is the same
  identifier on every platform, the **parameter id map is the one of the
  registry**;
* it writes a JSON manifest of the whole family — every module with its ids and
  the hash of the file — and exits non-zero when any verification fails.

Usage
-----
    python tools/stamp_all.py
    python tools/stamp_all.py --dry-run
    python tools/stamp_all.py --search build/vst3 --manifest build/stamp-manifest.json
    python tools/stamp_all.py --module AUR_DECLICK --module AUR_DEHUM

Exit codes
----------
    0   every found plug-in verified
    1   at least one plug-in failed verification (or no plug-in was found)
    2   the command line was wrong
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

#: Repository root (the parent of ``tools/``).
ROOT = Path(__file__).resolve().parents[1]

#: Suffixes of a plug-in **binary** (a plain file).
BINARY_SUFFIXES: tuple[str, ...] = (
    ".vst3",
    ".so",
    ".dylib",
    ".exe",
    ".bundle",
    ".aaxplugin",
    ".dll",
)

#: Suffixes of a plug-in **bundle** (a directory that holds the binary, or
#: the single file the format uses where there is no bundle).
BUNDLE_SUFFIXES: tuple[str, ...] = (".vst3", ".bundle", ".aaxplugin")

#: Where the built plug-ins are looked for when ``--search`` is not given.
DEFAULT_SEARCH_GLOBS: tuple[str, ...] = ("build/vst3*", "plugins/vst3native/build*")

#: Default place of the manifest.
DEFAULT_MANIFEST = Path("build") / "stamp-manifest.json"

#: Format and version of the manifest this tool writes.
MANIFEST_FORMAT = "auralis.stamp-manifest"
MANIFEST_VERSION = 1

#: The artefact directory of a module is named ``Auralis_<CODE>_artefacts``.
_ARTEFACT_RE = re.compile(r"^Auralis_(AUR_[A-Z0-9_]+)_artefacts$")

_CHUNK = 1 << 20


# ---------------------------------------------------------------------------
# loading the plug-in layer
# ---------------------------------------------------------------------------
def _load_module(name: str, path: Path) -> Any:
    """Import a Python file by path, without requiring its folder to be a package.

    The module is registered in :data:`sys.modules` under ``name`` so that a
    second request for the same name returns the *same* module object: the
    identity layer compares records with ``isinstance``, so two copies of it
    would not recognise each other's records.
    """
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_plugins() -> tuple[Any, Any]:
    """Return the ``(identity, build_identity)`` modules of the plug-in layer.

    ``plugins/identity.py`` is loaded **once** and shared with
    ``plugins/vst3/build_identity.py``, so that the records the tool derives and
    the records the stamper validates are the same class.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    build_identity = _load_module(
        "auralis_build_identity", ROOT / "plugins" / "vst3" / "build_identity.py"
    )

    identity = getattr(build_identity, "identity", None)
    if identity is None:  # pragma: no cover - the loader keeps the attribute
        identity = _load_module("auralis_identity", ROOT / "plugins" / "identity.py")
        build_identity.identity = identity

    sys.modules.setdefault("auralis_identity", identity)
    return identity, build_identity


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One plug-in binary found under a search root."""

    #: The binary itself (the file that carries the identity slot).
    path: Path
    #: The bundle that holds it, when the binary sits inside one.
    bundle: Path | None = None
    #: The names the plug-in may be known by (file name, bundle name and
    #: the directories it was built in).
    names: tuple[str, ...] = ()


def _has_marker(path: Path, marker: bytes) -> bool:
    """True when the file carries the identity marker."""
    overlap = len(marker) - 1
    tail = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    return False
                if marker in tail + chunk:
                    return True
                tail = (tail + chunk)[-overlap:] if overlap else b""
    except OSError:
        return False


def _inner_binary(bundle: Path, marker: bytes) -> Path | None:
    """Return the plug-in binary inside a bundle directory.

    A bundle holds more than the plug-in: the core library and the
    interpreter travel with it.  The binary that carries the identity
    marker is the plug-in, so that is the one that is returned.
    """
    files = [path for path in bundle.rglob("*") if path.is_file()]

    with_marker = [path for path in files if _has_marker(path, marker)]
    if len(with_marker) == 1:
        return with_marker[0]
    if with_marker:
        return max(with_marker, key=lambda path: path.stat().st_size)

    candidates = [path for path in files if path.suffix.lower() in BINARY_SUFFIXES]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _name_candidates(path: Path, bundle: Path | None) -> tuple[str, ...]:
    """The names a plug-in may be known by.

    A plug-in is known by its file name, by the name of the bundle it
    sits in and by the directory it was built in — the last one is how
    the native build names its per-module trees.
    """
    names: list[str] = []

    def _add(text: str) -> None:
        text = text.strip()
        if text and text not in names:
            names.append(text)

    for source in (path, bundle):
        if source is None:
            continue
        _add(source.name)
        _add(Path(source.name).stem)
        for suffix in BUNDLE_SUFFIXES:
            if source.name.lower().endswith(suffix):
                _add(source.name[: -len(suffix)])
                break

    for parent in path.parents:
        _add(parent.name)

    return tuple(names)


def _discover_in_root(root: Path, marker: bytes) -> list[Candidate]:
    """Return every plug-in binary under ``root``.

    A plug-in is a **bundle**: a directory named ``<name>.vst3`` that
    holds the binary, or — where the format uses a single file — a file
    named ``<name>.vst3``.  The binaries that are *not* in a bundle are
    build intermediates (the linked module before it is packaged), and
    they are left alone.
    """
    found: list[Candidate] = []
    seen: set[Path] = set()

    def _add(path: Path, bundle: Path | None) -> None:
        """Remember a candidate binary."""
        key = path.resolve()
        if key in seen:
            return
        seen.add(key)
        found.append(Candidate(path=path, bundle=bundle, names=_name_candidates(path, bundle)))

    if root.is_file():
        _add(root, None)
        return found

    for dirpath, dirnames, filenames in os.walk(root):
        directory = Path(dirpath)

        # A bundle is a directory that holds the binary.  An empty folder —
        # the one a failed plug-in build leaves behind — is not a bundle,
        # so the search goes on inside it.
        keep: list[str] = []
        for name in sorted(dirnames):
            sub = directory / name
            if name.lower().endswith(BUNDLE_SUFFIXES):
                inner = _inner_binary(sub, marker)
                if inner is not None:
                    _add(inner, sub)
                    continue
            keep.append(name)
        dirnames[:] = keep

        # The single file layout: the plug-in *is* the file.
        for name in sorted(filenames):
            path = directory / name
            if not name.lower().endswith(BUNDLE_SUFFIXES):
                continue
            if not _has_marker(path, marker):
                continue
            _add(path, None)

    return found


def resolve_search_roots(extra: Sequence[str], use_defaults: bool) -> list[Path]:
    """Return the directories that are searched for plug-in binaries."""
    roots: list[Path] = []

    if use_defaults:
        for pattern in DEFAULT_SEARCH_GLOBS:
            for path in sorted(ROOT.glob(pattern)):
                if path.is_dir():
                    roots.append(path)

    for raw in extra:
        path = Path(raw)
        if not path.exists():
            candidate = ROOT / raw
            if candidate.exists():
                path = candidate
        if path.is_dir():
            roots.append(path)
        elif path.is_file():
            roots.append(path)
        else:
            print(f"[stamp-all] search path does not exist: {raw}", file=sys.stderr)

    # Keep the order but drop duplicates.
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in roots:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


# ---------------------------------------------------------------------------
# naming: which module does a binary claim to be?
# ---------------------------------------------------------------------------
def _normalise(text: str) -> str:
    """Return a comparable form of a plug-in name."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


class NameIndex:
    """Maps the names a plug-in binary may carry to the module code."""

    def __init__(self, identities: Iterable[Any]) -> None:
        """Build the index from the derived identities."""
        self.exact: dict[str, str] = {}
        self.suffix: dict[str, str] = {}

        for identity in identities:
            code = identity.module_code
            names = {
                str(Path(identity.original_filename).stem),
                str(identity.internal_name),
                str(identity.module_code),
            }
            for name in names:
                key = _normalise(name)
                self.exact.setdefault(key, code)
                self.suffix.setdefault(key, code)

    def lookup(self, *names: str) -> str | None:
        """Return the module code the given names identify, if any.

        Every name is tried: the exact name first, and then the tail of
        the name, so that ``Auralis_AUR_DECLICK`` and ``Auralis De-Click``
        both find their module.
        """
        keys = [_normalise(name) for name in names]
        keys = [key for key in keys if key]
        if not keys:
            return None

        for key in keys:
            code = self.exact.get(key)
            if code is not None:
                return code

        matches: set[str] = set()
        for key in keys:
            for name, value in self.suffix.items():
                if key.endswith(name):
                    matches.add(value)
        if len(matches) == 1:
            return next(iter(matches))
        return None


def _artefact_code(path: Path) -> str | None:
    """Return the module code of the artefact directory ``path`` sits in."""
    for part in path.parts:
        match = _ARTEFACT_RE.match(part)
        if match:
            return match.group(1)
    return None


# ---------------------------------------------------------------------------
# stamping and verifying
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    """Return the SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_marker(path: Path, marker: bytes) -> int:
    """Count how often the identity marker occurs in a file."""
    count = 0
    overlap = len(marker) - 1
    tail = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            buffer = tail + chunk
            count += buffer.count(marker)
            tail = buffer[-overlap:] if overlap else b""
    return count


def _stamp(build_identity: Any, template: Path, destination: Path, record: Any) -> Path:
    """Stamp ``record`` into ``template`` with the build tool of the plug-in layer."""
    stamper = getattr(build_identity, "_stamp_into", None)
    if callable(stamper):
        return Path(stamper(template, destination, record))
    # Fallback for a build tool that does not expose its stamping helper.
    identity = build_identity.identity
    return Path(identity.stamp_identity(template, destination, record))


def verify_binary(
    identity: Any,
    path: Path,
    expected: Any,
    expected_params: dict[int, str],
    canonical_names: set[str],
    name_code: str | None,
    artefact_code: str | None,
) -> dict[str, Any]:
    """Verify one stamped binary and return the verification result."""
    checks: dict[str, bool] = {}
    problems: list[str] = []

    marker = identity.IDENTITY_MAGIC
    occurrences = _count_marker(path, marker)
    checks["marker_occurs_once"] = occurrences == 1
    if occurrences != 1:
        problems.append(
            f"the identity marker occurs {occurrences} time(s) in the binary, expected exactly one"
        )

    record = None
    if checks["marker_occurs_once"]:
        try:
            record = identity.verify_identity(path)
            checks["record_parses"] = True
        except Exception as exc:  # noqa: BLE001 - a broken record is a failure
            checks["record_parses"] = False
            problems.append(f"the embedded identity record does not parse: {exc}")
    else:
        checks["record_parses"] = False

    # The name of the binary and the artefact directory it sits in must agree
    # about the module; a disagreement means the build put the wrong binary in
    # the wrong place.
    checks["module_code_consistent"] = (
        name_code is None or artefact_code is None or name_code == artefact_code
    )
    if not checks["module_code_consistent"]:
        problems.append(
            f"the binary is named for module {name_code!r} but sits in the artefact "
            f"directory of module {artefact_code!r}"
        )

    if record is not None:
        expected_code = name_code if name_code is not None else artefact_code
        checks["module_code_matches_name"] = record.module_code == expected_code
        if not checks["module_code_matches_name"]:
            problems.append(
                f"the record says module {record.module_code!r} but the binary is named "
                f"for module {expected_code!r}"
            )

        product_code = record.aax_effect_ids[0][1] if record.aax_effect_ids else ""
        expected_fuid = expected.vst3_fuid
        checks["fuid_matches"] = record.vst3_fuid == expected_fuid
        if not checks["fuid_matches"]:
            problems.append(
                f"the FUID of the record is {record.vst3_fuid!r}, expected {expected_fuid!r}"
            )
        recomputed = str(identity.derive_fuid(product_code)) if product_code else ""
        checks["fuid_is_derivable"] = recomputed == expected_fuid
        if not checks["fuid_is_derivable"]:
            problems.append(
                f"the expected FUID {expected_fuid!r} is not the one derived from the product "
                f"code {product_code!r} ({recomputed!r})"
            )

        checks["param_id_map_matches_registry"] = dict(record.param_id_map) == dict(
            expected_params
        )
        if not checks["param_id_map_matches_registry"]:
            problems.append(
                "the parameter id map of the record does not match the registry: "
                f"{sorted(record.param_id_map.items())} != {sorted(expected_params.items())}"
            )
        checks["param_names_match_table"] = set(record.param_id_map.values()) == canonical_names
        if not checks["param_names_match_table"]:
            missing = sorted(canonical_names - set(record.param_id_map.values()))
            extra = sorted(set(record.param_id_map.values()) - canonical_names)
            problems.append(
                "the parameter names of the record do not match the canonical table "
                f"(missing: {missing}, unexpected: {extra})"
            )

        # The class id is a GUID string; the bytes a binary reports are
        # that string's platform encoding.  The string has to read back
        # from the bytes of *every* platform, or the same plug-in would
        # have two different class ids depending on where it runs.
        portable = True
        for com_compatible in (True, False):
            raw = identity.class_id_bytes(record.vst3_fuid, com_compatible=com_compatible)
            read_back = identity.class_id_string(raw, com_compatible=com_compatible)
            if identity.class_id_text(read_back) != identity.class_id_text(record.vst3_fuid):
                portable = False
                problems.append(
                    "the class id does not read back as the record's string on the "
                    f"{'Windows' if com_compatible else 'Unix'} layout: {read_back!r}"
                )
        checks["class_id_is_portable"] = portable

        checks["record_matches_expected"] = record.to_dict() == expected.to_dict()
        if not checks["record_matches_expected"]:
            problems.append(
                "the embedded record differs from the identity derived from the registry"
            )
    else:
        for key in (
            "module_code_matches_name",
            "fuid_matches",
            "fuid_is_derivable",
            "param_id_map_matches_registry",
            "param_names_match_table",
            "class_id_is_portable",
            "record_matches_expected",
        ):
            checks[key] = False

    return {
        "ok": all(checks.values()),
        "marker_occurrences": occurrences,
        "checks": checks,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
def _relative(path: Path) -> str:
    """Return ``path`` relative to the repository root when it is inside it."""
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def build_manifest(
    entries: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    problems: list[str],
    roots: Sequence[Path],
    dry_run: bool,
    class_id_layout: str,
) -> dict[str, Any]:
    """Assemble the manifest of the whole plug-in family."""
    # One entry per plug-in: the first binary of that module.  The same
    # module can be built in more than one tree; the other binaries are
    # listed separately, so that nothing is hidden.
    plugins: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in entries:
        code = entry.get("module_code")
        if code is None:
            plugins.append(entry)
            continue
        if code in seen:
            duplicates.append(entry)
            continue
        seen.add(code)
        plugins.append(entry)

    failed = [entry for entry in plugins if not entry["verification"]["ok"]]
    verified = len(plugins) - len(failed)
    broken_duplicates = [
        entry for entry in duplicates if not entry["verification"]["ok"]
    ]

    return {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "product": "Auralis",
        "generated_by": "tools/stamp_all.py",
        "dry_run": dry_run,
        "root": str(ROOT.resolve()),
        "search_roots": [_relative(path) for path in roots],
        "module_count": len(modules),
        "plugin_count": len(plugins),
        "verified_count": verified,
        "failed_count": len(failed),
        "binary_count": len(entries),
        "duplicate_binary_count": len(duplicates),
        "modules_built_more_than_once": sorted(
            {entry.get("module_code") for entry in duplicates if entry.get("module_code")}
        ),
        "built_module_count": len(
            {entry.get("module_code") for entry in plugins if entry.get("module_code")}
        ),
        "class_id_layout": class_id_layout,
        "ok": not failed and not broken_duplicates and not problems,
        "problems": problems,
        "modules": modules,
        "plugins": plugins,
        "duplicate_binaries": duplicates,
    }


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------
def _process_candidate(
    identity: Any,
    build_identity: Any,
    candidate: Candidate,
    index: NameIndex,
    identities: dict[str, Any],
    registry_maps: dict[str, dict[int, str]],
    canonical_names: dict[str, set[str]],
    dry_run: bool,
) -> dict[str, Any]:
    """Stamp one binary, read it back, verify it and describe the outcome."""
    path = candidate.path
    name_code = index.lookup(*candidate.names)
    artefact_code = _artefact_code(path)

    # The stamp is driven by the artefact directory — that is what the build
    # system named the target after — and the *name* of the binary is checked
    # against it afterwards, so the two are independent signals.
    code = artefact_code if artefact_code is not None else name_code

    entry: dict[str, Any] = {
        "file": _relative(path),
        "absolute_path": str(path.resolve()),
        "bundle": _relative(candidate.bundle) if candidate.bundle else None,
        "bytes": path.stat().st_size,
        "module_code": code,
        "module_code_source": (
            "artefact-directory"
            if artefact_code is not None
            else ("name" if name_code is not None else None)
        ),
        "module_code_from_name": name_code,
        "module_code_from_artefact": artefact_code,
    }

    if code is None:
        entry["sha256_before"] = _sha256(path)
        entry["sha256_after"] = entry["sha256_before"]
        entry["changed"] = False
        entry["stamped"] = False
        entry["verification"] = {
            "ok": False,
            "marker_occurrences": _count_marker(path, identity.IDENTITY_MAGIC),
            "checks": {},
            "problems": [
                "the binary does not identify a module: neither its name nor its "
                "artefact directory names one"
            ],
        }
        return entry

    expected = identities[code]
    entry["display_name"] = expected.display_name
    entry["product_code"] = expected.aax_effect_ids[0][1] if expected.aax_effect_ids else ""
    entry["vst3_fuid"] = expected.vst3_fuid
    entry["vst3_class_id_bytes"] = expected.class_id_bytes.hex()
    entry["aax_effect_ids"] = [list(triple) for triple in expected.aax_effect_ids]
    entry["param_count"] = len(expected.param_id_map)

    sha_before = _sha256(path)

    scratch: str | None = None
    try:
        if dry_run:
            # Stamp a copy, so that the binary on disk is left alone.
            scratch = tempfile.mkdtemp(prefix="auralis-stamp-")
            target = Path(scratch) / path.name
            _stamp(build_identity, path, target, expected)
        else:
            target = path
            _stamp(build_identity, path, path, expected)

        entry["sha256_before"] = sha_before
        entry["sha256_after"] = _sha256(target)
        entry["changed"] = entry["sha256_before"] != entry["sha256_after"]
        entry["stamped"] = not dry_run

        try:
            # The record has to name the module the file claims to be: a
            # binary called for one module that carries another module's
            # record is wrong, and reading it back has to say so.
            read_back = identity.verify_identity(target, expect_module_code=code)
            entry["identity"] = read_back.to_dict()
        except Exception as exc:  # noqa: BLE001 - a broken record is a failure
            entry["identity_error"] = str(exc)
        entry["expected_identity"] = expected.to_dict()

        entry["verification"] = verify_binary(
            identity,
            target,
            expected,
            registry_maps[code],
            canonical_names[code],
            name_code,
            artefact_code,
        )
    except Exception as exc:  # noqa: BLE001 - a failed stamp is a failed plug-in
        entry.setdefault("sha256_before", sha_before)
        entry.setdefault("sha256_after", sha_before)
        entry.setdefault("changed", False)
        entry["stamped"] = False
        entry["verification"] = {
            "ok": False,
            "marker_occurrences": _count_marker(path, identity.IDENTITY_MAGIC),
            "checks": {},
            "problems": [f"the binary could not be stamped: {exc}"],
        }
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    return entry


def run(args: argparse.Namespace) -> int:
    """Run the tool and return the exit code."""
    identity, build_identity = load_plugins()

    # --- the canonical module table and the derived identities -------------
    table = build_identity.module_table()
    identities = identity.identities_for()
    by_code = {record.module_code: record for record in identities}

    wanted = set(args.module) if args.module else None
    if wanted:
        unknown = sorted(code for code in wanted if code not in by_code)
        if unknown:
            print(f"[stamp-all] unknown module code(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        table = [entry for entry in table if entry["code"] in wanted]
        identities = [record for record in identities if record.module_code in wanted]

    from aurcore import schema as _schema

    specs = _schema.load()
    registry_maps = {code: identity.module_param_ids(code) for code in by_code}
    canonical_names = {
        code: {param.name for param in specs[code].params} for code in by_code if code in specs
    }

    family_problems: list[str] = []
    table_codes = {entry["code"] for entry in table}
    identity_codes = {record.module_code for record in identities}
    if table_codes != identity_codes:
        missing = sorted(table_codes - identity_codes)
        extra = sorted(identity_codes - table_codes)
        family_problems.append(
            "the canonical module table and the derived identities disagree "
            f"(without an identity: {missing}, without a table entry: {extra})"
        )

    fuids: dict[str, str] = {}
    for record in identities:
        if record.vst3_fuid in fuids:
            family_problems.append(
                f"modules {fuids[record.vst3_fuid]} and {record.module_code} share the FUID "
                f"{record.vst3_fuid}"
            )
        else:
            fuids[record.vst3_fuid] = record.module_code

    # Two modules must never share the class id *bytes* either: the bytes
    # are what a host compares, and they are what the record implies.
    class_ids: dict[bytes, str] = {}
    for record in identities:
        raw = record.class_id_bytes
        if raw in class_ids:
            family_problems.append(
                f"modules {class_ids[raw]} and {record.module_code} share the class id "
                f"bytes {raw.hex()}"
            )
        else:
            class_ids[raw] = record.module_code

    # --- find the built plug-ins -------------------------------------------
    roots = resolve_search_roots(args.search, not args.no_default_search)
    candidates: list[Candidate] = []
    seen: set[Path] = set()
    for root in roots:
        for candidate in _discover_in_root(root, identity.IDENTITY_MAGIC):
            key = candidate.path.resolve()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    # Only binaries that actually carry the identity slot are plug-ins: the slot
    # is what the stamper writes into, and a binary without it cannot be
    # stamped at all.
    with_marker: list[Candidate] = []
    without_marker: list[Path] = []
    for candidate in candidates:
        if _count_marker(candidate.path, identity.IDENTITY_MAGIC) > 0:
            with_marker.append(candidate)
        else:
            without_marker.append(candidate.path)
    plug_ins = with_marker

    if not plug_ins and not args.allow_empty:
        print("[stamp-all] no built plug-in binary was found.", file=sys.stderr)
        print(
            "[stamp-all] searched: "
            + (", ".join(_relative(path) for path in roots) if roots else "<nothing>"),
            file=sys.stderr,
        )
        return 1

    # --- stamp, read back, verify -----------------------------------------
    index = NameIndex(identities)
    entries: list[dict[str, Any]] = []
    for candidate in sorted(plug_ins, key=lambda item: str(item.path)):
        entries.append(
            _process_candidate(
                identity,
                build_identity,
                candidate,
                index,
                by_code,
                registry_maps,
                canonical_names,
                args.dry_run,
            )
        )

    built_codes = {entry["module_code"] for entry in entries if entry.get("module_code")}

    # The first binary of every module, so that the module list can name
    # the file and the hash of the plug-in that was built.
    first_of: dict[str, dict[str, Any]] = {}
    for entry in entries:
        code = entry.get("module_code")
        if code and code not in first_of:
            first_of[code] = entry

    modules = []
    for record in identities:
        entry = first_of.get(record.module_code)
        module = {
            "module_code": record.module_code,
            "display_name": record.display_name,
            "product_name": record.product_name,
            "product_code": record.aax_effect_ids[0][1] if record.aax_effect_ids else "",
            "vst3_fuid": record.vst3_fuid,
            "vst3_class_id_bytes": record.class_id_bytes.hex(),
            "aax_effect_ids": [list(triple) for triple in record.aax_effect_ids],
            "param_count": len(record.param_id_map),
            "param_ids": sorted(record.param_id_map),
            "built": record.module_code in built_codes,
        }
        if entry is not None:
            module["file"] = entry["file"]
            module["bytes"] = entry.get("bytes")
            module["sha256"] = entry.get("sha256_after")
            module["verified"] = entry["verification"]["ok"]
        modules.append(module)

    layout = "COM compatible" if identity.host_is_com_compatible() else "big endian"
    manifest = build_manifest(
        entries, modules, family_problems, roots, args.dry_run, layout
    )
    manifest["skipped_without_identity_slot"] = [_relative(path) for path in without_marker]

    # --- report ------------------------------------------------------------
    if not args.quiet:
        for entry in entries:
            result = "OK" if entry["verification"]["ok"] else "FAIL"
            print(f"{entry.get('module_code') or '?':<20} {result:<5} {entry['file']}")
            for problem in entry["verification"]["problems"]:
                print(f"    - {problem}")

    for problem in family_problems:
        print(f"[stamp-all] {problem}", file=sys.stderr)

    if not args.dry_run and args.manifest is not None:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if not args.quiet:
            print(f"[stamp-all] manifest: {_relative(manifest_path)}")

    failed = manifest["failed_count"]
    total = manifest["plugin_count"]
    binaries = manifest["binary_count"]
    if args.dry_run:
        print(f"[stamp-all] dry run: {total - failed}/{total} plug-in(s) would verify")
    else:
        print(f"[stamp-all] {total - failed}/{total} plug-in(s) verified ({binaries} binaries)")
    broken_duplicates = any(
        not entry["verification"]["ok"] for entry in manifest["duplicate_binaries"]
    )
    return 1 if failed or family_problems or broken_duplicates else 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="stamp_all",
        description="Stamp and verify the identity of every built Auralis plug-in.",
    )
    parser.add_argument(
        "--search",
        action="append",
        default=[],
        metavar="PATH",
        help="additional directory (or file) to search for plug-in binaries",
    )
    parser.add_argument(
        "--no-default-search",
        action="store_true",
        help="search only the paths given with --search",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify what would happen without touching any binary",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help=f"where to write the manifest (default: {DEFAULT_MANIFEST.as_posix()})",
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        metavar="CODE",
        help="restrict the run to this module code (may be given more than once)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="do not fail when no plug-in binary is found",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print the summary",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    if args.manifest is None:
        args.manifest = str(DEFAULT_MANIFEST)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
