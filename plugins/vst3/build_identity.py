#!/usr/bin/env python3
"""
build_identity.py — build-time half of the Auralis plug-in adapter layer.

The adapter is a *template*: one binary, compiled once, that implements any
Auralis module.  Which module a given binary implements is decided at build
time by stamping an identity record into a reserved slot inside the binary
(see ``plugins/identity.py``).

This tool is a thin CLI over ``plugins/identity.py``.  It can:

``header``     emit the generated C++ header that carries the parameter table
               (ids come from the generated registry index, never by hand);
``stamp``      stamp one module's identity into one binary (in place when
               no output path is given);
``stamp-all``  stamp every module in the registry and write an install
               manifest;
``manifest``   write an install manifest for a directory of stamped binaries;
``verify``     read an identity back out of a stamped binary;
``list``       list the modules (for CMake to iterate over).

Usage
-----
    python plugins/vst3/build_identity.py header --out build/AuralisGenerated.h
    python plugins/vst3/build_identity.py stamp \
        --template build/Auralis.vst3 --output build/stamped/AuralisDeClick.vst3 \
        --module AUR_DECLICK
    python plugins/vst3/build_identity.py stamp-all \
        --template build/Auralis.vst3 --outdir build/stamped \
        --manifest build/stamped/install-manifest.json
    python plugins/vst3/build_identity.py manifest \
        --dir build/stamped --out build/stamped/install-manifest.json
    python plugins/vst3/build_identity.py verify --binary build/stamped/Auralis.vst3
    python plugins/vst3/build_identity.py list --format cmake
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent


def _load_identity_module():
    """Import ``plugins/identity.py`` without requiring ``plugins`` to be a package."""
    path = _ROOT / "plugins" / "identity.py"
    spec = importlib.util.spec_from_file_location("auralis_plugin_identity", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = _load_identity_module()


# --------------------------------------------------------------------------
# generated C++ header
# --------------------------------------------------------------------------

_CPP_TYPE = {
    "float": "ParamType::Float",
    "int": "ParamType::Int",
    "bool": "ParamType::Bool",
    "enum": "ParamType::Enum",
}


def _cpp_string(text: str) -> str:
    """Render a Python string as a C++ string literal."""
    out = ['"']
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) > 0x7E:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def module_table() -> list[dict[str, Any]]:
    """Return the canonical module/parameter table used by the C++ header.

    The generated registry (``aurcore/schema/generated.py``) is preferred
    because it is the artefact the rest of the project treats as canonical.
    The parameter table itself is the fallback.
    """
    try:
        from aurcore.schema import generated  # type: ignore

        modules: list[dict[str, Any]] = []
        for code in generated.MODULE_CODES:
            spec = generated.MODULE_SPECS[code]
            params = []
            for desc in spec.params:
                params.append(
                    {
                        "id": generated.PARAM_ID[code][desc.name],
                        "name": desc.name,
                        "module_code": code,
                        "type": desc.type,
                        "minimum": float(desc.minimum),
                        "maximum": float(desc.maximum),
                        "default": desc.default,
                        "unit": desc.unit,
                        "group": desc.group,
                        "choices": tuple(desc.choices),
                        "automatable": bool(desc.automatable),
                        "stepped": bool(desc.stepped),
                        "curve": desc.curve,
                        "curve_exponent": float(desc.curve_exponent),
                        "help": getattr(desc, "help", "") or "",
                    }
                )
            modules.append(
                {
                    "code": code,
                    "display_name": spec.display_name,
                    "category": spec.category,
                    "params": params,
                }
            )
        return modules
    except Exception:
        from aurcore import schema as _schema

        modules = []
        for spec in _schema.load().values():
            params = []
            for desc in spec.params:
                params.append(
                    {
                        "id": 0,
                        "name": desc.name,
                        "module_code": spec.code,
                        "type": desc.type,
                        "minimum": float(desc.minimum),
                        "maximum": float(desc.maximum),
                        "default": desc.default,
                        "unit": desc.unit,
                        "group": desc.group,
                        "choices": tuple(desc.choices),
                        "automatable": bool(desc.automatable),
                        "stepped": bool(desc.stepped),
                        "curve": desc.curve,
                        "curve_exponent": float(desc.curve_exponent),
                        "help": getattr(desc, "help", "") or "",
                    }
                )
            modules.append(
                {
                    "code": spec.code,
                    "display_name": spec.display_name,
                    "category": spec.category,
                    "params": params,
                }
            )
        return modules


def render_generated_header() -> str:
    """Render the C++ header that carries the parameter table.

    The ids in the header are the ids from the generated registry index - the
    position of a parameter inside its own module - so the identity record,
    the C++ adapter and the AAX page table all agree on the same numbers.  The
    header deliberately does NOT contain the identity marker bytes: the marker
    must appear exactly once in the linked binary, otherwise the build-time
    stamper cannot tell which occurrence is the slot.
    """
    modules = module_table()
    index = identity.param_index()

    # Registry ids come from the generated index; the fallback path (no
    # generated registry) numbers the parameters itself.
    for module in modules:
        for param in module["params"]:
            pid = index.name_for(module["code"], param["name"])
            if pid is not None:
                param["id"] = pid

    flat: list[dict[str, Any]] = []
    for module in modules:
        module["params"].sort(key=lambda p: p["id"])
        flat.extend(module["params"])

    # Choice pool: every enum choice in the registry, listed once.
    pool: list[str] = []
    pool_index: dict[str, int] = {}
    for param in flat:
        if param["type"] != "enum":
            continue
        for choice in param["choices"]:
            if choice not in pool_index:
                pool_index[choice] = len(pool)
                pool.append(choice)

    lines: list[str] = []
    add = lines.append

    add("// ===========================================================================")
    add("//  AuralisGenerated.h - GENERATED FILE, DO NOT EDIT.")
    add("//")
    add("//  Regenerate with:")
    add("//      python plugins/vst3/build_identity.py header --out <this file>")
    add("//")
    add("//  Source of truth: aurcore/schema/params.tsv, indexed by the generated")
    add("//  registry (aurcore/schema/generated.py).  The parameter ids below are the")
    add("//  ids from that index - the same numbers the identity")
    add("//  record and the AAX page table use.  Never renumber them.")
    add("//")
    add("//  NOTE: the identity marker bytes are deliberately NOT in this header.")
    add("//  The marker must occur exactly once in the linked binary so that the")
    add("//  build-time stamper can find the slot unambiguously.")
    add("// ===========================================================================")
    add("")
    add("#pragma once")
    add("")
    add("#include <cstddef>")
    add("")
    add("namespace auralis")
    add("{")
    add("")
    add("// --- identity slot geometry ---------------------------------------------")
    add("// These must match plugins/identity.py exactly; build_identity.py writes")
    add("// the JSON identity record into the region that follows the marker.")
    add(f"inline constexpr int kAuralisIdentityMarkerLength = {len(identity.IDENTITY_MAGIC)};")
    add(f"inline constexpr int kAuralisIdentityRegionSize  = {identity.IDENTITY_REGION_SIZE};")
    add("inline constexpr int kAuralisIdentitySlotSize     = kAuralisIdentityMarkerLength")
    add("                                                       + kAuralisIdentityRegionSize;")
    add("")
    add("// --- parameter metadata ------------------------------------------------")
    add("")
    add("enum class ParamType")
    add("{")
    add("    Float,")
    add("    Int,")
    add("    Bool,")
    add("    Enum")
    add("};")
    add("")
    add("struct ParamMeta")
    add("{")
    add("    int         id;            // host parameter id (inside its module)")
    add("    const char* name;          // canonical core parameter name")
    add("    const char* moduleCode;    // module that owns the parameter")
    add("    ParamType   type;")
    add("    float       minimum;")
    add("    float       maximum;")
    add("    float       defaultValue;")
    add("    const char* unit;")
    add("    const char* group;")
    add("    const char* const* choices; // pool slice, nullptr when not an enum")
    add("    int         choiceCount;")
    add("    bool        automatable;")
    add("    bool        stepped;")
    add("    const char* curve;")
    add("    float       curveExponent;  // the exponent p of the taper law")
    add("    const char* help;         // the sentence that explains the parameter")
    add("};")
    add("")
    add("struct ModuleMeta")
    add("{")
    add("    const char* code;")
    add("    const char* displayName;")
    add("    const char* category;")
    add("    int         paramBase;   // first host id of this module (always 0)")
    add("    int         paramCount;")
    add("};")
    add("")
    add(f"inline constexpr int kAuralisModuleCount = {len(modules)};")
    add(f"inline constexpr int kAuralisParamCount  = {len(flat)};")
    add("")
    add("// Every enum choice in the registry, pooled.")
    add("inline constexpr const char* const kAuralisChoicePool[] =")
    add("{")
    for choice in pool:
        add(f"    {_cpp_string(choice)},")
    add("};")
    add(f"inline constexpr int kAuralisChoicePoolSize = {len(pool)};")
    add("")
    add("// Every parameter in the registry, in the order of the generated index.")
    add("inline constexpr ParamMeta kAuralisParams[kAuralisParamCount] =")
    add("{")
    for param in flat:
        choices = param["choices"]
        if param["type"] == "enum" and choices:
            offset = pool_index[choices[0]]
            choice_count = len(choices)
            choices_expr = f"kAuralisChoicePool + {offset}"
        else:
            choice_count = 0
            choices_expr = "nullptr"

        default = param["default"]
        if param["type"] == "enum":
            default_value = float(choices.index(default)) if default in choices else 0.0
        elif param["type"] == "bool":
            default_value = 1.0 if default else 0.0
        else:
            default_value = float(default)

        add(
            "    { "
            f"{param['id']}, "
            f"{_cpp_string(param['name'])}, "
            f"{_cpp_string(param['module_code'])}, "
            f"{_CPP_TYPE[param['type']]}, "
            f"{param['minimum']!r}f, "
            f"{param['maximum']!r}f, "
            f"{default_value!r}f, "
            f"{_cpp_string(param['unit'])}, "
            f"{_cpp_string(param['group'])}, "
            f"{choices_expr}, {choice_count}, "
            f"{'true' if param['automatable'] else 'false'}, "
            f"{'true' if param['stepped'] else 'false'}, "
            f"{_cpp_string(param['curve'])}, "
            f"{float(param.get('curve_exponent', 1.0))!r}f, "
            f"{_cpp_string(param.get('help', ''))}"
            " },"
        )
    add("};")
    add("")
    add("// Every module in the registry, in canonical order.")
    add("inline constexpr ModuleMeta kAuralisModules[kAuralisModuleCount] =")
    add("{")
    for module in modules:
        base = module["params"][0]["id"] if module["params"] else 0
        add(
            "    { "
            f"{_cpp_string(module['code'])}, "
            f"{_cpp_string(module['display_name'])}, "
            f"{_cpp_string(module['category'])}, "
            f"{base}, {len(module['params'])}"
            " },"
        )
    add("};")
    add("")
    add("} // namespace auralis")
    add("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# stamping
# --------------------------------------------------------------------------

_BINARY_SUFFIXES = (".vst3", ".so", ".dylib", ".exe", ".bundle", ".aaxplugin", ".dll")


def _has_marker(path: Path) -> bool:
    """True when the file carries the identity marker.

    A plug-in carries the marker; the core library and the interpreter
    that travel with it do not.
    """
    marker = identity.IDENTITY_MAGIC
    overlap = len(marker) - 1
    tail = b""

    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    return False
                if marker in tail + chunk:
                    return True
                tail = (tail + chunk)[-overlap:] if overlap else b""
    except OSError:
        return False


def _find_inner_binary(root: Path) -> Path | None:
    """Locate the executable inside a plug-in bundle directory.

    A bundle holds more than the plug-in: the core library and the
    interpreter travel with it.  The binary that carries the identity
    marker is the plug-in, so that is the one that is found.
    """
    # The macOS bundle layout puts the executable in Contents/MacOS and
    # gives it no file name extension, so that layout is checked first.
    macos_dir = root / "Contents" / "MacOS"
    if macos_dir.is_dir():
        candidates = [p for p in macos_dir.iterdir() if p.is_file()]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
            return candidates[0]

    files = [p for p in root.rglob("*") if p.is_file()]

    with_marker = [p for p in files if _has_marker(p)]
    if len(with_marker) == 1:
        return with_marker[0]
    if with_marker:
        return max(with_marker, key=lambda p: p.stat().st_size)

    candidates = [p for p in files if p.suffix.lower() in _BINARY_SUFFIXES]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def _find_binary(path: Path) -> Path | None:
    """Return the binary of ``path``, or None when it is not a plug-in."""
    if path.is_dir():
        return _find_inner_binary(path)
    if path.is_file() and path.suffix.lower() in _BINARY_SUFFIXES:
        return path
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp_into(template: Path, destination: Path | None, record: Any) -> Path:
    """Stamp ``record`` into a copy of ``template``.

    ``template`` may be a single binary or a bundle directory; in the bundle
    case the whole tree is copied and the binary inside it is stamped.
    When ``destination`` is None the template is stamped in place.
    """
    if destination is None:
        destination = template

    if template.is_dir():
        if destination.resolve() != template.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(template, destination)
        inner = _find_inner_binary(destination)
        if inner is None:
            raise SystemExit(f"no binary found inside bundle {destination}")
        identity.stamp_identity(inner, inner, record)
        return inner

    identity.stamp_identity(template, destination, record)
    return destination


def _manifest_entry(record: Any, binary: Path) -> dict[str, Any]:
    """Build one entry of the install manifest."""
    return {
        "module_code": record.module_code,
        "display_name": record.display_name,
        "product_name": record.product_name,
        "file_description": record.file_description,
        "original_filename": record.original_filename,
        "internal_name": record.internal_name,
        "category": record.category,
        "vst3_fuid": record.vst3_fuid,
        "vst3_class_id_bytes": record.class_id_bytes.hex(),
        "aax_effect_ids": [list(t) for t in record.aax_effect_ids],
        "param_ids": sorted(record.param_id_map),
        "path": str(binary),
        "bytes": binary.stat().st_size,
        "sha256": _sha256(binary),
    }


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def command_header(args: argparse.Namespace) -> int:
    """Write the generated C++ header."""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_generated_header(), encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


def command_stamp(args: argparse.Namespace) -> int:
    """Stamp a single module into a single binary."""
    record = identity.identity_for(args.module)
    output = Path(args.output) if args.output else None
    binary = _stamp_into(Path(args.template), output, record)
    print(f"{args.module}: {record.display_name} -> {binary}")
    print(f"  fuid    {record.vst3_fuid}")
    print(f"  aax     {', '.join('/'.join(t) for t in record.aax_effect_ids)}")
    return 0


def command_stamp_all(args: argparse.Namespace) -> int:
    """Stamp every module of the registry and write the install manifest."""
    template = Path(args.template)
    if not template.exists():
        raise SystemExit(f"template not found: {template}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    records = identity.identities_for()
    wanted = set(args.modules) if args.modules else None
    suffix = args.suffix or template.suffix

    entries: list[dict[str, Any]] = []
    for record in records:
        if wanted is not None and record.module_code not in wanted:
            continue
        name = record.original_filename
        if suffix and not name.endswith(suffix):
            name = f"{Path(record.original_filename).stem}{suffix}"
        destination = outdir / name
        binary = _stamp_into(template, destination, record)

        entries.append (_manifest_entry(record, binary))

        # The human readable twin of the embedded record.
        identity.write_identity(record, outdir / f"{record.module_code}.identity.json")

    manifest = {
        "format": "auralis.install-manifest",
        "version": 1,
        "product": "Auralis",
        "plugin_count": len(entries),
        "plugins": entries,
    }
    manifest_path = Path(args.manifest) if args.manifest else outdir / "install-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"stamped {len(entries)} plugin(s) into {outdir}")
    print(f"manifest: {manifest_path}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    """Read an identity back out of a stamped binary."""
    record = identity.verify_identity(Path(args.binary))
    print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    return 0


def command_manifest(args: argparse.Namespace) -> int:
    """Write an install manifest for a set of stamped binaries.

    Every binary is read back, which verifies that it really carries the
    identity it claims and that the record matches the schema.
    """
    directories = [Path(d) for d in (args.dir or [])]
    artefacts = [Path(a) for a in (args.artefact or [])]

    if not directories and not artefacts:
        raise SystemExit("at least one --dir or --artefact is required")

    candidates: list[Path] = list(artefacts)

    for directory in directories:
        if not directory.is_dir():
            raise SystemExit(f"not a directory: {directory}")

        for path in sorted(directory.iterdir()):
            if path.name.endswith(".identity.json"):
                continue

            candidates.append(path)

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in candidates:
        binary = _find_binary(path)
        if binary is None:
            continue

        # Only the binary that carries the identity slot is a plug-in; the
        # core library and the interpreter travel with it.
        if not _has_marker(binary):
            continue

        try:
            record = identity.verify_identity(binary)
        except Exception as exc:
            raise SystemExit(f"{binary}: {exc}") from exc

        if record.module_code in seen:
            continue

        seen.add(record.module_code)
        entries.append(_manifest_entry(record, binary))

    manifest = {
        "format": "auralis.install-manifest",
        "version": 1,
        "product": "Auralis",
        "plugin_count": len(entries),
        "plugins": entries,
    }

    manifest_path = Path(args.out)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"manifest: {manifest_path} ({len(entries)} plugin(s))")
    return 0


def command_list(args: argparse.Namespace) -> int:
    """List the modules of the registry."""
    records = identity.identities_for()
    if args.format == "json":
        print(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False))
    elif args.format == "cmake":
        print("# Generated by plugins/vst3/build_identity.py list --format cmake")
        print()
        print("# The module codes, in canonical registry order.")
        print("set(AURALIS_MODULE_CODES")
        for record in records:
            print(f"    {record.module_code}")
        print(")")
        print()
        print("# The display names, in the same order as AURALIS_MODULE_CODES.")
        print("set(AURALIS_MODULE_NAMES")
        for record in records:
            print(f'    "{record.display_name}"')
        print(")")
        print()
        print("# module_code:product_code (four characters, unique per module)")
        for record in records:
            product = record.aax_effect_ids[0][1]
            print(f'set(AURALIS_PRODUCT_CODE_{record.module_code} "{product}")')
        print()
        print("# module_code:file name the plug-in is installed under")
        for record in records:
            print(f'set(AURALIS_FILE_NAME_{record.module_code} "{record.original_filename}")')
    else:
        for record in records:
            print(f"{record.module_code}\t{record.display_name}\t{record.category}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_identity.py",
        description="Stamp Auralis plug-in identities into adapter binaries.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_header = sub.add_parser("header", help="emit the generated C++ parameter header")
    p_header.add_argument("--out", required=True, help="output .h path")
    p_header.set_defaults(func=command_header)

    p_stamp = sub.add_parser("stamp", help="stamp one module into one binary")
    p_stamp.add_argument("--template", required=True, help="template binary or bundle")
    p_stamp.add_argument(
        "--output",
        default=None,
        help="output binary or bundle (omit to stamp the template in place)",
    )
    p_stamp.add_argument("--module", required=True, help="module code, e.g. AUR_DECLICK")
    p_stamp.set_defaults(func=command_stamp)

    p_all = sub.add_parser("stamp-all", help="stamp every module and write the manifest")
    p_all.add_argument("--template", required=True, help="template binary or bundle")
    p_all.add_argument("--outdir", required=True, help="output directory")
    p_all.add_argument("--manifest", default=None, help="install manifest path")
    p_all.add_argument("--suffix", default=None, help="file suffix for the stamped copies")
    p_all.add_argument(
        "--modules",
        nargs="*",
        default=None,
        help="restrict to these module codes (default: every module)",
    )
    p_all.set_defaults(func=command_stamp_all)

    p_verify = sub.add_parser("verify", help="read an identity back out of a binary")
    p_verify.add_argument("--binary", required=True, help="stamped binary")
    p_verify.set_defaults(func=command_verify)

    p_list = sub.add_parser("list", help="list the modules of the registry")
    p_list.add_argument("--format", choices=("text", "json", "cmake"), default="text")
    p_list.set_defaults(func=command_list)

    p_manifest = sub.add_parser(
        "manifest", help="write an install manifest for a directory of stamped binaries"
    )
    p_manifest.add_argument(
        "--dir",
        action="append",
        default=None,
        help="directory to scan (may be given more than once)",
    )
    p_manifest.add_argument(
        "--artefact",
        action="append",
        default=None,
        help="a stamped binary or bundle (may be given more than once)",
    )
    p_manifest.add_argument("--out", required=True, help="manifest path")
    p_manifest.set_defaults(func=command_manifest)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
