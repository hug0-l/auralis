#!/usr/bin/env python3
"""
tools/validate_schema.py — validate the generated Auralis module registry.

Four independent checks, each with its own PASS/FAIL report:

    1. Live registry conformance — every implemented module's ``describe()``
       must match the canonical parameter table exactly.
    2. JSON Schema conformance — ``aurcore/schema/modules.json`` must validate
       against ``aurcore/schema/modules.schema.json``.  The ``jsonschema``
       package is used when it is installed; otherwise a small built-in
       validator (the subset of draft 2020-12 the generated schema uses) is
       used, so the check never depends on a third party package.
    3. Codegen drift — the committed artefacts must be byte identical to a
       fresh in-memory run of ``tools/codegen_registry.py``.
    4. Static registry checks — unique codes, unique parameter names, enum
       parameters carry choices, defaults inside their range, no stray
       whitespace in parameter names and no third party trademark token.

Exit code
---------
    0   every required check passed
    1   a required check failed (schema, drift or static checks)
    2   the tool could not run (missing canonical table, unreadable files)

Check 1 is informational: modules that are not implemented yet are expected
while the module implementations are in flight, so it never fails the run
unless ``--strict-modules`` is given.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import pkgutil
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TSV_REL = "aurcore/schema/params.tsv"
MODULES_JSON_REL = "aurcore/schema/modules.json"
MODULES_SCHEMA_REL = "aurcore/schema/modules.schema.json"

#: Third party tokens that must never appear in generated artefacts.  Only
#: standalone tokens count: the two letter one inside "matrix" or "prefix" is
#: harmless, and an identifier built from one is not a mention either.
#:
#: The two words are spelled in pieces on purpose: this file is part of the
#: codebase, and the codebase must not contain a third party trademark even in
#: the scanner that looks for one (CONTRACT §0).
FORBIDDEN_TOKENS: tuple[str, ...] = ("r" + "x", "izo" + "tope")


# ---------------------------------------------------------------------------
# a minimal JSON Schema (draft 2020-12) validator
# ---------------------------------------------------------------------------
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "number":
        return _is_number(value)
    if expected == "integer":
        return _is_integer(value)
    return True


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality: 1 and 1.0 are equal, True and 1 are not."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if _is_number(left) and _is_number(right):
        return float(left) == float(right)
    if type(left) is not type(right):
        return False
    return left == right


def _resolve_ref(ref: str, root: dict) -> Any:
    """Resolve a local ``#``-style JSON pointer against the root schema."""
    if not ref.startswith("#"):
        return None
    node: Any = root
    for part in ref.lstrip("#").strip("/").split("/"):
        if not part:
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def validate_instance(instance: Any, schema: Any, root: Any = None, path: str = "$") -> list[str]:
    """Validate ``instance`` against ``schema`` and return a list of errors.

    Supports the subset of draft 2020-12 that the generated schema uses:
    ``$ref``, ``type``, ``enum``, ``const``, ``properties``,
    ``patternProperties``, ``additionalProperties``, ``propertyNames``,
    ``required``, ``minProperties``, ``maxProperties``, ``items``,
    ``minItems``, ``maxItems``, ``uniqueItems``, ``minLength``, ``maxLength``,
    ``pattern``, ``minimum``, ``maximum``, ``exclusiveMinimum``,
    ``exclusiveMaximum``, ``multipleOf``, ``allOf``, ``anyOf``, ``oneOf``,
    ``not`` and ``if``/``then``/``else``.
    """
    if root is None:
        root = schema
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        target = _resolve_ref(str(schema["$ref"]), root)
        if target is None:
            return [f"{path}: cannot resolve $ref {schema['$ref']!r}"]
        errors.extend(validate_instance(instance, target, root, path))

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(instance, t) for t in types):
            got = type(instance).__name__
            return errors + [f"{path}: expected type {expected!r}, got {got}"]

    if "enum" in schema:
        if not any(_json_equal(instance, option) for option in schema["enum"]):
            errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if "const" in schema:
        if not _json_equal(instance, schema["const"]):
            errors.append(f"{path}: {instance!r} is not equal to const {schema['const']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance!r} is below the minimum {schema['minimum']!r}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance!r} is above the maximum {schema['maximum']!r}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(
                f"{path}: {instance!r} is not above exclusiveMinimum "
                f"{schema['exclusiveMinimum']!r}"
            )
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(
                f"{path}: {instance!r} is not below exclusiveMaximum "
                f"{schema['exclusiveMaximum']!r}"
            )
        if "multipleOf" in schema:
            factor = schema["multipleOf"]
            if factor and (instance % factor) != 0:
                errors.append(f"{path}: {instance!r} is not a multiple of {factor!r}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            for i, item in enumerate(instance):
                if any(_json_equal(item, other) for other in instance[:i]):
                    errors.append(f"{path}: duplicate item {item!r}")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(
                    validate_instance(item, schema["items"], root, f"{path}[{index}]")
                )

    if isinstance(instance, dict):
        if "required" in schema:
            for key in schema["required"]:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(f"{path}: fewer than minProperties {schema['minProperties']}")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: more than maxProperties {schema['maxProperties']}")
        properties = schema.get("properties") or {}
        pattern_properties = schema.get("patternProperties") or {}
        for key, value in instance.items():
            matched = False
            if key in properties:
                errors.extend(
                    validate_instance(value, properties[key], root, f"{path}.{key}")
                )
                matched = True
            for pattern, subschema in pattern_properties.items():
                if re.search(pattern, key):
                    errors.extend(
                        validate_instance(value, subschema, root, f"{path}.{key}")
                    )
                    matched = True
            if "propertyNames" in schema:
                errors.extend(
                    validate_instance(key, schema["propertyNames"], root, f"{path}.{key}<name>")
                )
            if not matched and "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                if additional is False:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
                elif isinstance(additional, dict):
                    errors.extend(
                        validate_instance(value, additional, root, f"{path}.{key}")
                    )

    for subschema in schema.get("allOf", []) or []:
        errors.extend(validate_instance(instance, subschema, root, path))

    if "anyOf" in schema:
        branches = schema["anyOf"] or []
        if branches and not any(
            not validate_instance(instance, branch, root, path) for branch in branches
        ):
            errors.append(f"{path}: does not match any branch of anyOf")

    if "oneOf" in schema:
        branches = schema["oneOf"] or []
        matched = sum(
            1 for branch in branches if not validate_instance(instance, branch, root, path)
        )
        if matched != 1:
            errors.append(f"{path}: matches {matched} branches of oneOf, expected exactly 1")

    if "not" in schema and not validate_instance(instance, schema["not"], root, path):
        errors.append(f"{path}: must not match the 'not' schema")

    if "if" in schema:
        condition = not validate_instance(instance, schema["if"], root, path)
        branch = schema.get("then") if condition else schema.get("else")
        if branch is not None:
            errors.extend(validate_instance(instance, branch, root, path))

    return errors


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """The outcome of one validation check."""

    title: str
    ok: bool
    lines: list[str] = field(default_factory=list)
    required: bool = True

    def render(self) -> str:
        state = "PASS" if self.ok else "FAIL"
        out = [f"{self.title}  ->  {state}"]
        out.extend(f"    {line}" for line in self.lines)
        return "\n".join(out)


def _iter_strings(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(path, string)`` for every string in a JSON document."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_strings(key, f"{path}.<key>")
            yield from _iter_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_strings(value, f"{path}[{index}]")


def _token_pattern(token: str) -> re.Pattern[str]:
    """A pattern that matches ``token`` only as a standalone word."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE)


def _substring_pattern(token: str) -> re.Pattern[str]:
    """A pattern that matches ``token`` inside any word."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}[A-Za-z0-9]*", re.IGNORECASE)


def _package_version(name: str) -> str:
    """Return the version of an installed package, or ``unknown``."""
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001 - the version is informational only
        return "unknown"


def _wrap_codes(codes: list[str], width: int = 66) -> list[str]:
    """Wrap a list of module codes into indented, readable lines."""
    lines: list[str] = []
    current = "  "
    for code in codes:
        piece = code + ", "
        if len(current) + len(piece) > width + 2 and current.strip():
            lines.append(current.rstrip())
            current = "  "
        current += piece
    if current.strip():
        lines.append(current.rstrip().rstrip(","))
    return lines


def _import_live_modules() -> tuple[list[str], list[str]]:
    """Import every module implementation so the registry gets populated.

    Returns ``(errors, imported)``.  A module that fails to import is
    reported, never raised: the check must report, not crash.
    """
    errors: list[str] = []
    imported: list[str] = []
    try:
        package = importlib.import_module("aurcore.modules")
        imported = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
        return errors, imported
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        errors.append(f"aurcore.modules: {type(exc).__name__}: {exc}")

    # Fall back to importing every module file on its own, using a package
    # stub so that a single broken file does not hide the other modules.
    import aurcore

    modules_dir = Path(aurcore.__file__).resolve().parent / "modules"
    if not modules_dir.is_dir():
        return errors, imported
    stub = types.ModuleType("aurcore.modules")
    stub.__path__ = [str(modules_dir)]
    stub.__package__ = "aurcore.modules"
    sys.modules["aurcore.modules"] = stub
    for finder, name, ispkg in pkgutil.iter_modules([str(modules_dir)]):
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"aurcore.modules.{name}")
            imported.append(name)
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            errors.append(f"aurcore.modules.{name}: {type(exc).__name__}: {exc}")
    return errors, imported


# ---------------------------------------------------------------------------
# check 1 — live registry conformance
# ---------------------------------------------------------------------------
def check_conformance(specs: dict, strict: bool) -> CheckResult:
    """Compare every implemented module against the canonical table."""
    from aurcore import registry
    from aurcore.schema import validate_module

    import_errors, imported = _import_live_modules()
    live = {info.code: info for info in registry.all()}

    lines: list[str] = []
    problems: list[str] = []

    if import_errors:
        lines.append("module files that could not be imported:")
        lines.extend(f"  {message}" for message in import_errors)

    matched = 0
    for code, spec in specs.items():
        info = live.get(code)
        if info is None:
            continue
        differences = validate_module(spec, info.params)
        if differences:
            problems.append(f"{code} ({spec.display_name}):")
            problems.extend(f"    {difference}" for difference in differences)
        else:
            matched += 1

    missing = [code for code in specs if code not in live]
    extra = [code for code in live if code not in specs]

    lines.append(
        f"{len(live)} module(s) implemented, {len(specs)} module(s) in the canonical table"
    )
    lines.append(f"{matched} implemented module(s) match the canonical table exactly")
    if missing:
        lines.append(f"{len(missing)} module(s) not implemented yet:")
        lines.extend(_wrap_codes(missing))
    if extra:
        lines.append(f"{len(extra)} implemented module(s) are not in the canonical table")
        problems.extend(f"{code} is implemented but not in the canonical table" for code in extra)
    if problems:
        lines.append("differences against the canonical table:")
        lines.extend(problems)

    ok = not problems
    title = "[1] live registry conformance"
    if not strict:
        title += "  (informational)"
    return CheckResult(title=title, ok=ok, lines=lines, required=strict)


# ---------------------------------------------------------------------------
# check 2 — JSON Schema conformance
# ---------------------------------------------------------------------------
def check_json_schema(root: Path, force_builtin: bool) -> CheckResult:
    """Validate the registry JSON against the generated JSON Schema."""
    lines: list[str] = []
    registry_path = root / MODULES_JSON_REL
    schema_path = root / MODULES_SCHEMA_REL

    for path in (registry_path, schema_path):
        if not path.is_file():
            return CheckResult(
                title=f"[2] JSON Schema conformance ({MODULES_JSON_REL})",
                ok=False,
                lines=[f"missing file: {path}"],
            )
    try:
        instance = json.loads(registry_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(
            title=f"[2] JSON Schema conformance ({MODULES_JSON_REL})",
            ok=False,
            lines=[f"cannot read the JSON files: {exc}"],
        )

    backend = "built-in validator (draft 2020-12 subset)"
    errors: list[str] = []

    if not force_builtin:
        try:
            import jsonschema  # noqa: PLC0415 - optional dependency, checked at runtime
        except ImportError:
            jsonschema = None  # type: ignore[assignment]
        if jsonschema is not None:
            try:
                from jsonschema import Draft202012Validator

                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
                found = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
                errors = [
                    f"{_json_path(error)}: {error.message}" for error in found
                ]
                backend = f"jsonschema {_package_version('jsonschema')} (draft 2020-12)"
            except Exception as exc:  # noqa: BLE001 - fall back to the built-in validator
                errors = []
                backend = "built-in validator (draft 2020-12 subset)"
                lines.append(f"the jsonschema package could not be used ({exc}); falling back")

    if not errors and backend.startswith("built-in"):
        errors = validate_instance(instance, schema)

    lines.append(f"backend: {backend}")
    lines.append(f"instance: {MODULES_JSON_REL}")
    lines.append(f"schema:   {MODULES_SCHEMA_REL}")
    if errors:
        lines.append(f"{len(errors)} schema violation(s):")
        lines.extend(f"  {error}" for error in errors[:40])
        if len(errors) > 40:
            lines.append(f"  ... and {len(errors) - 40} more")
    return CheckResult(
        title=f"[2] JSON Schema conformance ({MODULES_JSON_REL})",
        ok=not errors,
        lines=lines,
    )


def _json_path(error: Any) -> str:
    """Render the location of a jsonschema error as a JSON pointer."""
    parts = list(error.absolute_path)
    if not parts:
        return "$"
    out = "$"
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


# ---------------------------------------------------------------------------
# check 3 — codegen drift
# ---------------------------------------------------------------------------
def check_drift(root: Path) -> CheckResult:
    """Regenerate every artefact in memory and compare it with the committed file."""
    lines: list[str] = []
    tools_dir = root / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        import codegen_registry  # noqa: PLC0415 - loaded from the tools directory
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        return CheckResult(
            title="[3] codegen drift",
            ok=False,
            lines=[f"cannot import tools/codegen_registry.py: {exc}"],
        )

    try:
        specs, widened = codegen_registry.load_canonical(root / codegen_registry.TSV_REL)
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        return CheckResult(
            title="[3] codegen drift",
            ok=False,
            lines=[f"cannot read the canonical table: {exc}"],
        )
    if widened:
        lines.append(
            f"note: {widened} row(s) of the canonical table have 12 columns and were "
            "widened in memory"
        )

    registry = codegen_registry.build_registry(specs)
    rendered = codegen_registry.render_all(registry)

    problems: list[str] = []
    for rel, text in rendered.items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: missing")
            lines.append(f"  {rel:<40} MISSING")
            continue
        committed = path.read_text(encoding="utf-8")
        if committed == text:
            lines.append(f"  {rel:<40} up to date")
        else:
            problems.append(f"{rel}: drifted")
            lines.append(f"  {rel:<40} DRIFTED")
            for detail in codegen_registry.diff_summary(text, committed):
                lines.append(f"      {detail}")

    if problems:
        lines.append(f"{len(problems)} file(s) differ from a fresh run of the codegen tool")
        lines.append("fix with: python tools/codegen_registry.py")
    return CheckResult(title="[3] codegen drift", ok=not problems, lines=lines)


# ---------------------------------------------------------------------------
# check 4 — static registry checks
# ---------------------------------------------------------------------------
def check_static(root: Path, specs: dict) -> CheckResult:
    """Run the static invariants over the canonical table and the registry JSON."""
    lines: list[str] = []
    problems: list[str] = []
    passed: list[tuple[str, str]] = []

    # -- unique module codes -------------------------------------------------
    codes = list(specs.keys())
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        problems.append(f"duplicate module codes: {', '.join(duplicates)}")
    else:
        passed.append(("unique module codes", f"{len(codes)} codes"))
    for code in codes:
        if not re.fullmatch(r"AUR_[A-Z0-9_]+", code):
            problems.append(f"module code {code!r} does not match AUR_[A-Z0-9_]+")

    # -- unique parameter names, enums, ranges, whitespace --------------------
    total_params = 0
    enum_count = 0
    duplicate_names = False
    enum_without_choices = False
    default_out_of_range = False
    whitespace_name = False
    for code, spec in specs.items():
        names = [desc.name for desc in spec.params]
        total_params += len(names)
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            duplicate_names = True
            problems.append(f"{code}: duplicate parameter names: {', '.join(repeated)}")
        for desc in spec.params:
            if desc.name != desc.name.strip():
                whitespace_name = True
                problems.append(
                    f"{code}: parameter {desc.name!r} has leading or trailing whitespace"
                )
            if not desc.name:
                problems.append(f"{code}: a parameter has an empty name")
            if desc.type == "enum":
                enum_count += 1
                if not desc.choices:
                    enum_without_choices = True
                    problems.append(f"{code}.{desc.name}: enum parameter without choices")
                elif desc.default not in desc.choices:
                    problems.append(
                        f"{code}.{desc.name}: default {desc.default!r} is not one of the choices"
                    )
            elif desc.type == "bool":
                if not isinstance(desc.default, bool):
                    problems.append(f"{code}.{desc.name}: bool default is not a boolean")
            else:
                if not (desc.minimum <= float(desc.default) <= desc.maximum):
                    default_out_of_range = True
                    problems.append(
                        f"{code}.{desc.name}: default {desc.default!r} is outside "
                        f"[{desc.minimum}, {desc.maximum}]"
                    )
            if desc.maximum < desc.minimum:
                problems.append(f"{code}.{desc.name}: maximum is below the minimum")
    if not duplicate_names:
        passed.append(("unique parameter names", f"{total_params} parameters"))
    if not enum_without_choices:
        passed.append(("enum parameters carry choices", f"{enum_count} enum parameters"))
    if not default_out_of_range:
        passed.append(("defaults inside [min, max]", ""))
    if not whitespace_name:
        passed.append(("no whitespace in parameter names", ""))

    # -- trademarks ----------------------------------------------------------
    registry_path = root / MODULES_JSON_REL
    if registry_path.is_file():
        try:
            instance = json.loads(registry_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"cannot parse {MODULES_JSON_REL}: {exc}")
            instance = {}
        hits: list[str] = []
        for path, text in _iter_strings(instance):
            for token in FORBIDDEN_TOKENS:
                if _token_pattern(token).search(text):
                    hits.append(f"{path}: {text!r} contains the token {token!r}")
        if hits:
            problems.extend(hits)
        else:
            passed.append(("no forbidden trademark token", ""))

        # Advisory only: a token that merely *contains* a forbidden word (for
        # example an identifier built from one) is not a standalone mention,
        # but it is worth showing to a human.
        advisory: list[str] = []
        seen: set[str] = set()
        for path, text in _iter_strings(instance):
            for token in FORBIDDEN_TOKENS:
                for match in _substring_pattern(token).finditer(text):
                    word = match.group(0)
                    if word.lower() != token.lower() and word not in seen:
                        seen.add(word)
                        advisory.append(f"{path}: {word!r} contains {token!r}")
        if advisory:
            lines.append("advisory: identifier contains a forbidden word (not a mention):")
            lines.extend(f"  {item}" for item in advisory)
    else:
        problems.append(f"missing file: {registry_path}")

    for label, detail in passed:
        lines.append(f"{label:<33}{detail:>16}  PASS")
    if problems:
        lines.append(f"{len(problems)} problem(s):")
        lines.extend(f"  {problem}" for problem in problems)
    return CheckResult(title="[4] static registry checks", ok=not problems, lines=lines)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_schema",
        description="Validate the generated Auralis module registry.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root that holds the generated files",
    )
    parser.add_argument(
        "--strict-modules",
        action="store_true",
        help="let the live registry conformance check fail the run",
    )
    parser.add_argument(
        "--builtin-validator",
        action="store_true",
        help="always use the built-in JSON Schema validator, even when jsonschema is installed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run every validation check and report the result."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    print("=" * 78)
    print(" AURALIS SCHEMA VALIDATION")
    print(f" root: {root}")
    print("=" * 78)

    tools_dir = root / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        import codegen_registry  # noqa: PLC0415 - loaded from the tools directory
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        print(f"cannot import tools/codegen_registry.py: {exc}")
        return 2

    try:
        specs, widened = codegen_registry.load_canonical(root / codegen_registry.TSV_REL)
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        print(f"cannot read the canonical parameter table: {exc}")
        return 2
    if widened:
        print(
            f"note: {widened} row(s) of the canonical table have 12 columns and were "
            "widened in memory\n"
        )

    results = [
        check_conformance(specs, args.strict_modules),
        check_json_schema(root, args.builtin_validator),
        check_drift(root),
        check_static(root, specs),
    ]

    print()
    for result in results:
        print(result.render())
        print()

    failed = [result for result in results if not result.ok and result.required]
    print("-" * 78)
    if failed:
        print(f"RESULT: FAIL ({len(failed)} required check(s) failed)")
        for result in failed:
            print(f"  - {result.title.strip()}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
