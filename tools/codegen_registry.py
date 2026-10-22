#!/usr/bin/env python3
"""
tools/codegen_registry.py — generate every machine-readable form of the module registry.

The canonical parameter table (``aurcore/schema/params.tsv``) is the single
source of truth.  This tool turns it into the artefacts the rest of the
project reads:

    aurcore/schema/modules.json          the registry (JSON, flat code -> module)
    aurcore/schema/modules.schema.json   JSON Schema (draft 2020-12) for the above
    aurcore/schema/generated.py          Python dataclasses + the parameter index
    aurcore/schema/aur_modules.h         C header with stable parameter ids
    docs/MODULES.md                      the human-readable module table

Nothing here is hand-written: the files above are generated and must never be
edited by hand.

The parameter id
----------------
Every parameter has one integer id, and it is the **same number in every
artefact**: in the registry JSON, in the JSON Schema, in the generated Python
module, in the C header and in the module document.  The id of a parameter is
its position *inside its own module*, counted from zero:

    AUR_DECLICK  Sensitivity  ->  id 1
    AUR_DECRACKLE Quality     ->  id 1

The id space is per module, and that is what makes it stable.  A module's
ids do not depend on any other module, so adding a module, removing a module
or growing any other module leaves every existing id where it was.  Under the
old scheme — one registry-wide counter, so that the id of a parameter was the
sum of the parameter counts of all the modules before it — adding a single
parameter to the first module moved 248 of the 253 ids.

Inside a module the id is the position, so a parameter that is inserted
*before* another one still renumbers the ones after it.  A new parameter is
therefore appended at the end of its module's block in the table.  The drift
gate below reports every id that moved, so that a renumbering can never reach
a surface unnoticed.

Usage
-----
    python tools/codegen_registry.py             write the artefacts
    python tools/codegen_registry.py --check     CI drift gate (non-zero on drift)
    python tools/codegen_registry.py --properties  run the id property checks
    python tools/codegen_registry.py --summary   print the module/parameter table

The tool is idempotent: running it twice produces byte-identical files.  All
output uses LF line endings and UTF-8 without a BOM, on every platform.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore.params import ParamDesc  # noqa: E402
from aurcore.schema import ModuleSpec  # noqa: E402

#: Repository-relative location of the canonical parameter table.
TSV_REL = "aurcore/schema/params.tsv"

#: The parameter fields carried by every generated artefact, in output order.
PARAM_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "default",
    "minimum",
    "maximum",
    "unit",
    "choices",
    "group",
    "automatable",
    "stepped",
    "curve",
    "curve_exponent",
    "help",
    "aliases",
)

#: The module fields carried by every generated artefact, in output order.
MODULE_FIELDS: tuple[str, ...] = (
    "code",
    "display_name",
    "category",
    "edition",
    "element",
)

#: Categories in their canonical display order.
CATEGORY_ORDER: tuple[str, ...] = ("Repair", "Utility", "Measurement", "System")

#: The soft line length limit of the style gate (``tools/lint_check.py``).
#:
#: The generated Python has to fit inside it.  A long line in a generated
#: file is the generator's fault, not a debt the tree has to carry, and the
#: style gate only exempts the findings of a generated file from its
#: warning budget - it still reports them.  The generator wraps its output
#: instead, so the artefact it writes is as readable as a hand written one.
MAX_LINE_LENGTH = 100

#: The marker every JSON artefact carries.  JSON has no comment syntax, so the
#: marker is a key; the text is what the hygiene gate looks for.
GENERATED_MARKER = (
    "do not edit - this file is produced by tools/codegen_registry.py "
    "from aurcore/schema/params.tsv"
)


# ---------------------------------------------------------------------------
# canonical input
# ---------------------------------------------------------------------------
def _widen_short_rows(text: str) -> tuple[str, int]:
    """Widen short data rows to the canonical columns.

    A data row of the canonical table carries seventeen tab separated columns.
    When one of the two optional columns (``unit`` / ``choices``) is empty and
    the author dropped the tab, the row comes in with sixteen columns and the
    canonical loader refuses it.  This helper restores the column count *in
    memory only* — the values are never touched and the committed table is
    never rewritten.
    """
    out: list[str] = []
    fixed = 0
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if stripped and not stripped.lstrip().startswith("#"):
            parts = line.split("\t")
            if len(parts) == 16:
                # Both `unit` and `choices` are empty on these rows, so the
                # dropped column is one of the two empties: restoring it as an
                # empty `choices` column keeps `unit` and `group` in place.
                parts = parts[:13] + [""] + parts[13:]
                fixed += 1
            elif len(parts) != 17:
                raise ValueError(
                    f"{TSV_REL}: expected 17 columns, got {len(parts)}: {line!r}"
                )
            line = "\t".join(parts)
        out.append(line)
    return "\n".join(out), fixed


def load_canonical(path: Path | None = None) -> tuple[dict[str, ModuleSpec], int]:
    """Load the canonical parameter table through :func:`aurcore.schema.load`.

    Returns the module specifications and the number of rows that had to be
    widened to be parsable (0 for a well formed table).
    """
    from aurcore.schema import load

    tsv = Path(path) if path is not None else ROOT / TSV_REL
    if not tsv.is_file():
        raise FileNotFoundError(f"canonical parameter table not found: {tsv}")
    try:
        return load(str(tsv)), 0
    except ValueError:
        normalised, fixed = _widen_short_rows(tsv.read_text(encoding="utf-8"))
        if not fixed:
            raise
        handle, tmp_name = tempfile.mkstemp(suffix=".tsv", prefix="aur_params_")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(normalised)
            return load(tmp_name), fixed
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:  # pragma: no cover - best effort cleanup
                pass


# ---------------------------------------------------------------------------
# registry model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Registry:
    """Everything the generators need, derived once from the canonical table."""

    specs: dict[str, ModuleSpec]
    payload: dict[str, dict[str, Any]]
    codes: tuple[str, ...]
    param_id: dict[str, dict[str, int]]
    code_by_id: dict[str, dict[int, str]]
    param_count: dict[str, int]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_params(self) -> int:
        return sum(len(spec.params) for spec in self.specs.values())


def _param_payload(desc: ParamDesc, pid: int) -> dict[str, Any]:
    """Project a :class:`ParamDesc` onto the canonical generated field set."""
    raw = desc.to_dict()
    payload: dict[str, Any] = {"id": pid}
    payload.update({key: raw[key] for key in PARAM_FIELDS})
    return payload


def build_registry(specs: dict[str, ModuleSpec]) -> Registry:
    """Build the registry model from the canonical specifications.

    The parameter id is the position of the parameter inside its own module.
    The id space is per module, so the ids of a module never depend on any
    other module: adding, removing or growing a module leaves the ids of the
    other modules untouched.
    """
    payload: dict[str, dict[str, Any]] = {}
    param_id: dict[str, dict[str, int]] = {}
    code_by_id: dict[str, dict[int, str]] = {}
    param_count: dict[str, int] = {}

    for code, spec in specs.items():
        ids = {desc.name: index for index, desc in enumerate(spec.params)}
        param_id[code] = ids
        code_by_id[code] = {index: name for name, index in ids.items()}
        param_count[code] = len(spec.params)
        payload[code] = {
            "code": spec.code,
            "display_name": spec.display_name,
            "category": spec.category,
            "edition": spec.edition,
            "element": spec.element,
            "params": [
                _param_payload(desc, ids[desc.name]) for desc in spec.params
            ],
        }

    return Registry(
        specs=specs,
        payload=payload,
        codes=tuple(specs.keys()),
        param_id=param_id,
        code_by_id=code_by_id,
        param_count=param_count,
    )


def id_table(reg: Registry) -> dict[tuple[str, str], int]:
    """Return the flat ``(module code, parameter name) -> id`` table."""
    return {
        (code, name): pid
        for code, inner in reg.param_id.items()
        for name, pid in inner.items()
    }


# ---------------------------------------------------------------------------
# generators
# ---------------------------------------------------------------------------
def _py_value(value: Any) -> str:
    """Render a Python literal for a parameter value."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return repr(value)


def _dumps(payload: Any) -> str:
    """Serialise a JSON document deterministically (LF, UTF-8, trailing newline)."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# line wrapping
# ---------------------------------------------------------------------------
def _split_string(text: str, budget: int) -> list[str]:
    """Split a string into chunks whose Python literal fits into ``budget``.

    ``budget`` is the number of characters the literal may take on one
    line, the two quotes included.  The chunks concatenate back into the
    original string, character for character: the split happens after a
    space when there is one inside the window, so the space stays with the
    chunk in front of it and nothing is lost.
    """
    chunks: list[str] = []
    rest = text
    while rest:
        take = min(len(rest), budget)
        while take > 0 and len(repr(rest[:take])) > budget:
            take -= 1
        if take <= 0:
            # One character does not fit on a line of its own.  Emit it and
            # let the guard below report the line that is over the limit.
            chunks.append(rest[0])
            rest = rest[1:]
            continue
        space = rest.rfind(" ", 0, take)
        if space >= take // 2:
            take = space + 1
        chunks.append(rest[:take])
        rest = rest[take:]
    return chunks


def _literal_lines(
    text: str,
    indent: int,
    head: str = "",
    tail: str = "",
) -> list[str]:
    """Render ``head<literal>tail`` over as many lines as the limit needs.

    ``head`` is the text in front of the literal on the first line (for
    example ``help=``) and ``tail`` the text behind it on the last line
    (for example ``,``).  A long string is emitted as several adjacent
    string literals: Python joins them when it compiles the module, so the
    value is character for character the string that was handed in.
    """
    pad = " " * indent
    single = f"{pad}{head}{text!r}{tail}"
    if len(single) <= MAX_LINE_LENGTH:
        return [single]
    budget = MAX_LINE_LENGTH - indent - len(head) - len(tail)
    chunks = _split_string(text, budget)
    if len(chunks) < 2:
        return [single]
    lines: list[str] = []
    last = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        prefix = f"{pad}{head}" if index == 0 else f"{pad}{' ' * len(head)}"
        suffix = tail if index == last else ""
        lines.append(f"{prefix}{chunk!r}{suffix}")
    return lines


def _value_lines(value: Any, indent: int, keyword: str) -> list[str]:
    """Render ``keyword=<value>`` over as many lines as the limit needs."""
    if isinstance(value, str):
        return _literal_lines(value, indent, f"{keyword}=", ",")
    return [f"{' ' * indent}{keyword}={_py_value(value)},"]


def _tuple_lines(items: list[str], indent: int, keyword: str) -> list[str]:
    """Render ``keyword=(...)`` over as many lines as the limit needs.

    One item per line, so a list of choices or of aliases stays readable
    when it no longer fits on a single line.  The tuple is unchanged.
    """
    pad = " " * indent
    body = ", ".join(repr(item) for item in items)
    single = f"{pad}{keyword}=({body}{',' if items else ''}),"
    if len(single) <= MAX_LINE_LENGTH:
        return [single]
    lines = [f"{pad}{keyword}=("]
    for item in items:
        lines.extend(_literal_lines(item, indent + 4, "", ","))
    lines.append(f"{pad}),")
    return lines


def _param_id_lines(code: str, entries: list[tuple[str, int]], indent: int) -> list[str]:
    """Render ``'CODE': {'Name': 0, ...}`` — the module local id table."""
    pad = " " * indent
    body = ", ".join(f"{name!r}: {pid}" for name, pid in entries)
    single = f"{pad}{code!r}: {{{body}}},"
    if len(single) <= MAX_LINE_LENGTH:
        return [single]
    lines = [f"{pad}{code!r}: {{"]
    for name, pid in entries:
        lines.extend(_literal_lines(name, indent + 4, "", f": {pid},"))
    lines.append(f"{pad}}},")
    return lines


def _code_by_index_lines(code: str, entries: list[tuple[int, str]], indent: int) -> list[str]:
    """Render ``'CODE': {0: 'Name', ...}`` — the reverse of the id table."""
    pad = " " * indent
    body = ", ".join(f"{pid}: {name!r}" for pid, name in entries)
    single = f"{pad}{code!r}: {{{body}}},"
    if len(single) <= MAX_LINE_LENGTH:
        return [single]
    lines = [f"{pad}{code!r}: {{"]
    for pid, name in entries:
        lines.extend(_literal_lines(name, indent + 4, f"{pid}: ", ","))
    lines.append(f"{pad}}},")
    return lines


def _overlong_lines(text: str) -> list[tuple[int, int]]:
    """Return the ``(number, length)`` of every line above the limit."""
    out: list[tuple[int, int]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if len(line) > MAX_LINE_LENGTH:
            out.append((number, len(line)))
    return out


def render_modules_json(reg: Registry) -> str:
    """``aurcore/schema/modules.json`` — the machine-readable registry."""
    payload: dict[str, Any] = {
        "_generated": f"{GENERATED_MARKER} ({len(reg.codes)} modules, {reg.total_params} "
        f"parameters)"
    }
    payload.update(reg.payload)
    return _dumps(payload)


def render_modules_schema(reg: Registry) -> str:
    """``aurcore/schema/modules.schema.json`` — JSON Schema for the registry."""
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://auralis.local/schema/modules.schema.json",
        "title": "Auralis module registry",
        "description": (
            "Every Auralis module and its parameters.  This file is generated "
            "by tools/codegen_registry.py from aurcore/schema/params.tsv - "
            "do not edit it by hand."
        ),
        "type": "object",
        "minProperties": 1,
        "propertyNames": {"pattern": "^(_generated|AUR_[A-Z0-9_]+)$"},
        "patternProperties": {"^AUR_[A-Z0-9_]+$": {"$ref": "#/$defs/module"}},
        "properties": {
            "_generated": {
                "type": "string",
                "description": (
                    "The marker that says the file is generated.  JSON has no "
                    "comment syntax, so the marker is a key; it carries the same "
                    "'do not edit' warning as the header of the other artefacts."
                ),
            }
        },
        "additionalProperties": False,
        "$defs": {
            "module": {
                "type": "object",
                "description": "One module of the registry.",
                "additionalProperties": False,
                "required": list(MODULE_FIELDS) + ["params"],
                "properties": {
                    "code": {"type": "string", "pattern": "^AUR_[A-Z0-9_]+$"},
                    "display_name": {"type": "string", "minLength": 1},
                    "category": {"enum": list(CATEGORY_ORDER)},
                    "edition": {"enum": ["standard", "advanced"]},
                    "element": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/param"},
                    },
                },
            },
            "param": {
                "type": "object",
                "description": "One parameter of one module.",
                "additionalProperties": False,
                "required": ["id"] + list(PARAM_FIELDS),
                "properties": {
                    "id": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "The parameter id: its position inside its own "
                            "module, counted from zero.  The same number is in "
                            "aurcore/schema/generated.py, aurcore/schema/"
                            "aur_modules.h and docs/MODULES.md."
                        ),
                    },
                    "name": {"type": "string", "minLength": 1, "pattern": r"^\S(.*\S)?$"},
                    "type": {"enum": ["float", "int", "bool", "enum"]},
                    "default": {"type": ["number", "string", "boolean"]},
                    "minimum": {"type": "number"},
                    "maximum": {"type": "number"},
                    "unit": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "group": {"type": "string"},
                    "automatable": {"type": "boolean"},
                    "stepped": {"type": "boolean"},
                    "curve": {"enum": ["linear", "log", "taper"]},
                    "curve_exponent": {
                        "type": "number",
                        "exclusiveMinimum": 0.0,
                        "description": (
                            "The exponent p of the taper law, v = lo + (hi - lo) "
                            "* n ** p.  It only means something when the curve is "
                            "a taper."
                        ),
                    },
                    "help": {
                        "type": "string",
                        "description": (
                            "The sentence that says what the parameter does."
                        ),
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The names the parameter had before it was renamed.  "
                            "A document that carries one of them still loads."
                        ),
                    },
                },
                "allOf": [
                    {
                        "if": {"required": ["type"], "properties": {"type": {"const": "enum"}}},
                        "then": {"properties": {"choices": {"minItems": 1}}},
                    },
                    {
                        "if": {"required": ["type"], "properties": {"type": {"const": "bool"}}},
                        "then": {
                            "properties": {
                                "minimum": {"const": 0},
                                "maximum": {"const": 1},
                            }
                        },
                    },
                ],
            },
        },
    }
    return _dumps(schema)


def render_generated_python(reg: Registry) -> str:
    """``aurcore/schema/generated.py`` — dataclasses plus the parameter index."""
    out: list[str] = []
    add = out.append

    add('"""')
    add("aurcore.schema.generated — GENERATED FILE, DO NOT EDIT.")
    add("")
    add("Regenerate with:  python tools/codegen_registry.py")
    add("Source of truth:  aurcore/schema/params.tsv")
    add("")
    add("Machine-readable mirror of the canonical parameter table.  The module")
    add("specs carry the same data as the registry JSON, and ``PARAM_ID`` gives")
    add("every parameter the id a host facing adapter uses as an integer")
    add("parameter id.")
    add("")
    add("The id of a parameter is its position inside its own module, counted")
    add("from zero.  The id space is per module, so the ids of a module never")
    add("depend on any other module: adding or removing a module, or growing")
    add("another module, leaves every existing id where it was.  The same")
    add("numbers appear in ``aurcore/schema/modules.json``,")
    add("``aurcore/schema/aur_modules.h`` and ``docs/MODULES.md``.")
    add('"""')
    add("from __future__ import annotations")
    add("")
    add("from dataclasses import dataclass")
    add("from typing import Union")
    add("")
    add("__all__ = [")
    add('    "ParamSpec",')
    add('    "ModuleSpec",')
    add('    "MODULE_SPECS",')
    add('    "MODULE_CODES",')
    add('    "MODULE_INDEX",')
    add('    "INDEX_BY_CODE",')
    add('    "PARAM_ID",')
    add('    "PARAM_INDEX",')
    add('    "CODE_BY_INDEX",')
    add('    "PARAM_COUNT",')
    add('    "param_index",')
    add('    "param_name",')
    add('    "param_id",')
    add("]")
    add("")
    add("Value = Union[float, int, bool, str]")
    add("")
    add("")
    add("@dataclass(frozen=True)")
    add("class ParamSpec:")
    add('    """One parameter of one module (mirrors aurcore.params.ParamDesc)."""')
    add("")
    add("    name: str")
    add('    type: str')
    add("    default: Value")
    add("    minimum: float")
    add("    maximum: float")
    add("    unit: str")
    add("    choices: tuple[str, ...]")
    add("    group: str")
    add("    automatable: bool")
    add("    stepped: bool")
    add("    curve: str")
    add("    curve_exponent: float")
    add("    help: str")
    add("    aliases: tuple[str, ...]")
    add("")
    add("")
    add("@dataclass(frozen=True)")
    add("class ModuleSpec:")
    add('    """One module of the registry."""')
    add("")
    add("    code: str")
    add("    display_name: str")
    add("    category: str")
    add("    edition: str")
    add("    element: str")
    add("    params: tuple[ParamSpec, ...]")
    add("")
    add("")
    add("#: Every module code, in canonical order.")
    add("MODULE_CODES: tuple[str, ...] = (")
    for code in reg.codes:
        add(f"    {code!r},")
    add(")")
    add("")
    add("#: The module specs, keyed by module code.")
    add("MODULE_SPECS: dict[str, ModuleSpec] = {")
    for code in reg.codes:
        spec = reg.specs[code]
        add(f"    {code!r}: ModuleSpec(")
        out.extend(_value_lines(spec.code, 8, "code"))
        out.extend(_value_lines(spec.display_name, 8, "display_name"))
        out.extend(_value_lines(spec.category, 8, "category"))
        out.extend(_value_lines(spec.edition, 8, "edition"))
        out.extend(_value_lines(spec.element, 8, "element"))
        if not spec.params:
            add("        params=(),")
        else:
            add("        params=(")
            for desc in spec.params:
                raw = desc.to_dict()
                add("            ParamSpec(")
                out.extend(_value_lines(raw["name"], 16, "name"))
                out.extend(_value_lines(raw["type"], 16, "type"))
                out.extend(_value_lines(raw["default"], 16, "default"))
                out.extend(_value_lines(raw["minimum"], 16, "minimum"))
                out.extend(_value_lines(raw["maximum"], 16, "maximum"))
                out.extend(_value_lines(raw["unit"], 16, "unit"))
                out.extend(_tuple_lines(list(raw["choices"]), 16, "choices"))
                out.extend(_value_lines(raw["group"], 16, "group"))
                out.extend(_value_lines(raw["automatable"], 16, "automatable"))
                out.extend(_value_lines(raw["stepped"], 16, "stepped"))
                out.extend(_value_lines(raw["curve"], 16, "curve"))
                out.extend(_value_lines(raw["curve_exponent"], 16, "curve_exponent"))
                out.extend(_value_lines(raw["help"], 16, "help"))
                out.extend(_tuple_lines(list(raw["aliases"]), 16, "aliases"))
                add("            ),")
            add("        ),")
        add("    ),")
    add("}")
    add("")
    add("#: Module code -> ordinal in the canonical order.")
    add("MODULE_INDEX: dict[str, int] = {")
    for ordinal, code in enumerate(reg.codes):
        add(f"    {code!r}: {ordinal},")
    add("}")
    add("")
    add("#: Module ordinal -> module code.")
    add("INDEX_BY_CODE: dict[int, str] = {")
    for ordinal, code in enumerate(reg.codes):
        add(f"    {ordinal}: {code!r},")
    add("}")
    add("")
    add("#: Module code -> parameter name -> the parameter id.")
    add("#:")
    add("#: The id is the position of the parameter inside its own module, counted")
    add("#: from zero.  The id space is per module: the ids of a module never")
    add("#: depend on any other module, so a module can be added or removed")
    add("#: without renumbering anything else.")
    add("PARAM_ID: dict[str, dict[str, int]] = {")
    for code in reg.codes:
        entries = list(reg.param_id[code].items())
        out.extend(_param_id_lines(code, entries, 4))
    add("}")
    add("")
    add("#: The same table under the name the adapter layer used before the ids")
    add("#: became module local.  It is an alias, not a second copy.")
    add("PARAM_INDEX: dict[str, dict[str, int]] = PARAM_ID")
    add("")
    add("#: Module code -> parameter id -> parameter name.")
    add("CODE_BY_INDEX: dict[str, dict[int, str]] = {")
    for code in reg.codes:
        entries = list(reg.code_by_id[code].items())
        out.extend(_code_by_index_lines(code, entries, 4))
    add("}")
    add("")
    add("#: Module code -> the number of parameters of that module.")
    add("PARAM_COUNT: dict[str, int] = {")
    for code in reg.codes:
        add(f"    {code!r}: {reg.param_count[code]},")
    add("}")
    add("")
    add("")
    add("def param_index(code: str, name: str) -> int:")
    add('    """Return the id of a parameter (its index inside its module)."""')
    add("    try:")
    add("        return PARAM_ID[code][name]")
    add("    except KeyError:")
    add('        raise KeyError(f"unknown parameter {name!r} for module {code!r}") from None')
    add("")
    add("")
    add("def param_name(code: str, index: int) -> str:")
    add('    """Return the parameter name at an id inside a module."""')
    add("    try:")
    add("        return CODE_BY_INDEX[code][index]")
    add("    except KeyError:")
    add('        raise KeyError(f"unknown parameter index {index} for module {code!r}") from None')
    add("")
    add("")
    add("def param_id(code: str, name: str) -> int:")
    add('    """Return the id of a parameter (its index inside its module)."""')
    add("    try:")
    add("        return PARAM_ID[code][name]")
    add("    except KeyError:")
    add('        raise KeyError(f"unknown parameter {name!r} for module {code!r}") from None')
    add("")
    text = "\n".join(out)

    # The style gate measures every line of every Python file.  A line that
    # is over the limit in a generated file is the generator's fault, so the
    # generator refuses to write it instead of leaving the debt behind.
    overlong = _overlong_lines(text)
    if overlong:
        detail = ", ".join(f"line {number} ({length})" for number, length in overlong)
        raise ValueError(
            "aurcore/schema/generated.py would carry lines above the "
            f"{MAX_LINE_LENGTH} character limit: {detail}"
        )
    return text


def render_c_header(reg: Registry) -> str:
    """``aurcore/schema/aur_modules.h`` — stable parameter ids for adapters."""
    out: list[str] = []
    add = out.append

    add("/*")
    add(" * aur_modules.h — GENERATED FILE, DO NOT EDIT.")
    add(" *")
    add(" * Regenerate with:  python tools/codegen_registry.py")
    add(" * Source of truth:  aurcore/schema/params.tsv")
    add(" *")
    add(" * A plug-in adapter uses the macros below to expose stable integer")
    add(" * parameter ids:")
    add(" *")
    add(" *   AUR_PARAM_<CODE>_<N>  the parameter with id N of module <CODE>,")
    add(" *                        N being its position in the canonical table.")
    add(" *   AUR_PARAM_<CODE>_COUNT  the number of parameters of module <CODE>.")
    add(" *")
    add(" * The id space is per module.  The id of a parameter is its position")
    add(" * inside its own module, so the ids of one module never depend on any")
    add(" * other module: adding or removing a module, or growing another")
    add(" * module, leaves every existing id where it was.  The same numbers")
    add(" * are in aurcore/schema/generated.py (PARAM_ID), in")
    add(" * aurcore/schema/modules.json and in docs/MODULES.md.")
    add(" */")
    add("#ifndef AUR_MODULES_H")
    add("#define AUR_MODULES_H")
    add("")
    add("#ifdef __cplusplus")
    add('extern "C" {')
    add("#endif")
    add("")
    add(f"#define AUR_MODULE_COUNT {len(reg.codes)}")
    add("")
    add("/* The module codes, in canonical order (index matches MODULE_INDEX in")
    add(" * aurcore/schema/generated.py). */")
    add("extern const char* const AUR_MODULE_CODES[];")
    add("")
    for code in reg.codes:
        spec = reg.specs[code]
        add(f"/* {code} — {spec.display_name} */")
        for desc in spec.params:
            index = reg.param_id[code][desc.name]
            add(f"#define AUR_PARAM_{code}_{index} {index}")
        add(f"#define AUR_PARAM_{code}_COUNT {len(spec.params)}")
        add("")
    add("#ifdef __cplusplus")
    add("}")
    add("#endif")
    add("")
    add("#endif /* AUR_MODULES_H */")
    add("")
    return "\n".join(out)


def _md_escape(text: str) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _md_range(desc: ParamDesc) -> str:
    if desc.type == "bool":
        return "0 – 1"
    return f"{desc.minimum:g} – {desc.maximum:g}"


def _md_default(desc: ParamDesc) -> str:
    value = desc.default
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def render_modules_md(reg: Registry) -> str:
    """``docs/MODULES.md`` — the human readable module table."""
    out: list[str] = []
    add = out.append

    add("# Auralis Modules")
    add("")
    add("> **Generated file — do not edit by hand.**")
    add("> Regenerate with `python tools/codegen_registry.py`.")
    add("> Source of truth: `aurcore/schema/params.tsv`.")
    add("")
    add(
        f"{len(reg.codes)} modules, {reg.total_params} parameters. "
        "Every module exposes its parameters through `ParamStore`; the names "
        "below are the canonical parameter keys used in presets and chains."
    )
    add("")
    add(
        "The **Id** column is the parameter id: the position of the parameter "
        "inside its own module, counted from zero.  The id space is per "
        "module, so the ids of a module never depend on any other module — "
        "adding or removing a module, or growing another module, leaves every "
        "existing id where it was.  The same numbers appear in "
        "`aurcore/schema/modules.json`, `aurcore/schema/generated.py` and "
        "`aurcore/schema/aur_modules.h`."
    )
    add("")

    add("## Index")
    add("")
    add("| # | Code | Module | Category | Edition | Element | Params |")
    add("|---:|---|---|---|---|---|---:|")
    for ordinal, code in enumerate(reg.codes):
        spec = reg.specs[code]
        add(
            f"| {ordinal} | `{code}` | {_md_escape(spec.display_name)} "
            f"| {spec.category} | {spec.edition} | `{spec.element}` "
            f"| {len(spec.params)} |"
        )
    add("")

    for category in CATEGORY_ORDER:
        codes = [c for c in reg.codes if reg.specs[c].category == category]
        if not codes:
            continue
        add(f"## {category}")
        add("")
        for code in codes:
            spec = reg.specs[code]
            add(f"### {_md_escape(spec.display_name)} (`{code}`)")
            add("")
            add(
                f"Element `{spec.element}` · edition `{spec.edition}` · "
                f"{len(spec.params)} parameters."
            )
            add("")
            add(
                "| Id | Parameter | Type | Default | Range | Unit | Choices | Group "
                "| Automatable | Stepped | Curve | Exponent | Help | Aliases |"
            )
            add("|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|")
            for desc in spec.params:
                index = reg.param_id[code][desc.name]
                choices = ", ".join(_md_escape(c) for c in desc.choices) or "—"
                unit = _md_escape(desc.unit) or "—"
                aliases = ", ".join(_md_escape(a) for a in desc.aliases) or "—"
                add(
                    f"| {index} | {_md_escape(desc.name)} | {desc.type} "
                    f"| `{_md_default(desc)}` | {_md_range(desc)} | {unit} "
                    f"| {choices} | {_md_escape(desc.group) or '—'} "
                    f"| {'yes' if desc.automatable else 'no'} "
                    f"| {'yes' if desc.stepped else 'no'} | {desc.curve} "
                    f"| {desc.curve_exponent:g} | {_md_escape(desc.help) or '—'} "
                    f"| {aliases} |"
                )
            add("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# output plumbing
# ---------------------------------------------------------------------------
#: Repository-relative output files, in the order they are written.
OUTPUTS: tuple[tuple[str, Callable[[Registry], str]], ...] = (
    ("aurcore/schema/modules.json", render_modules_json),
    ("aurcore/schema/modules.schema.json", render_modules_schema),
    ("aurcore/schema/generated.py", render_generated_python),
    ("aurcore/schema/aur_modules.h", render_c_header),
    ("docs/MODULES.md", render_modules_md),
)


def render_all(reg: Registry) -> dict[str, str]:
    """Render every artefact into memory (path -> text)."""
    return {rel: renderer(reg) for rel, renderer in OUTPUTS}


def write_outputs(root: Path, rendered: dict[str, str]) -> list[str]:
    """Write the rendered artefacts to disk with LF endings and UTF-8."""
    written: list[str] = []
    for rel, text in rendered.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        written.append(rel)
    return written


def diff_summary(expected: str, actual: str, limit: int = 6) -> list[str]:
    """Return a short description of the first differences between two texts."""
    exp = expected.splitlines()
    act = actual.splitlines()
    out: list[str] = []
    for index in range(max(len(exp), len(act))):
        e = exp[index] if index < len(exp) else "<missing>"
        a = act[index] if index < len(act) else "<missing>"
        if e != a:
            out.append(f"line {index + 1}: committed {a!r} != generated {e!r}")
            if len(out) >= limit:
                out.append("...")
                break
    return out


def diff_line_count(expected: str, actual: str) -> int:
    """Return how many lines differ between two texts."""
    exp = expected.splitlines()
    act = actual.splitlines()
    count = 0
    for index in range(max(len(exp), len(act))):
        e = exp[index] if index < len(exp) else None
        a = act[index] if index < len(act) else None
        if e != a:
            count += 1
    return count


# ---------------------------------------------------------------------------
# the id property checks
# ---------------------------------------------------------------------------
def check_id_invariants(reg: Registry) -> list[str]:
    """Check the invariants of the id model on a registry.

    Three properties, all of them structural (they need no history):

    1. the ids of a module are exactly ``0 .. n-1``;
    2. the ids of two different parameters of the same module differ;
    3. the ids of a module do not depend on any other module: dropping a
       module from the registry leaves the ids of the other modules alone.
    """
    problems: list[str] = []

    for code in reg.codes:
        ids = reg.param_id[code]
        expected = list(range(len(ids)))
        if sorted(ids.values()) != expected:
            problems.append(
                f"{code}: the parameter ids are {sorted(ids.values())}, "
                f"expected {expected}"
            )
        if len(set(ids.values())) != len(ids):
            problems.append(f"{code}: two parameters share an id")

    # 3. the ids of a module must not depend on any other module.
    for dropped in reg.codes:
        smaller = {
            code: spec for code, spec in reg.specs.items() if code != dropped
        }
        other = build_registry(smaller)
        for code in other.codes:
            if other.param_id[code] != reg.param_id[code]:
                problems.append(
                    f"{code}: the ids changed when the module {dropped} was "
                    "removed from the registry"
                )
                break
    return problems


def check_id_stability(reg: Registry) -> list[str]:
    """Check that growing the registry does not move an existing id.

    Two growths are tried for every module:

    * a parameter is appended to the module — the ids of the parameters that
      were already there must not move;
    * a module is added to the registry — the ids of the parameters that were
      already there must not move.
    """
    problems: list[str] = []
    base = id_table(reg)

    for code in reg.codes:
        spec = reg.specs[code]

        # (a) a parameter appended to the end of the module
        grown = dict(reg.specs)
        grown[code] = ModuleSpec(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            edition=spec.edition,
            element=spec.element,
            params=list(spec.params)
            + [
                ParamDesc(
                    name="Codegen Probe",
                    type="float",
                    default=0.0,
                    minimum=0.0,
                    maximum=1.0,
                    unit="",
                    choices=(),
                    group="Codegen",
                )
            ],
        )
        after = id_table(build_registry(grown))
        for key, pid in base.items():
            if key[0] == code:
                continue
            if after.get(key) != pid:
                problems.append(
                    f"{key[0]}.{key[1]}: id {pid} -> {after.get(key)} after a "
                    f"parameter was added to {code}"
                )
                break
        for name, pid in after.items():
            if name[0] == code and name[1] != "Codegen Probe" and base.get(name) != pid:
                problems.append(
                    f"{name[0]}.{name[1]}: id {base.get(name)} -> {pid} after a "
                    "parameter was added to the module"
                )
                break

    # (b) a module added to the registry
    probe_code = "AUR_CODEGENPROBE"
    probe = ModuleSpec(
        code=probe_code,
        display_name="Codegen Probe",
        category="System",
        edition="standard",
        element="CodegenProbe",
        params=[
            ParamDesc(
                name="Probe",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=1.0,
                unit="",
                choices=(),
                group="Codegen",
            )
        ],
    )
    grown = dict(reg.specs)
    grown[probe_code] = probe
    after = id_table(build_registry(grown))
    for key, pid in base.items():
        if after.get(key) != pid:
            problems.append(
                f"{key[0]}.{key[1]}: id {pid} -> {after.get(key)} after a module "
                "was added to the registry"
            )
            break
    return problems


def id_moves(committed: dict[tuple[str, str], int], fresh: dict[tuple[str, str], int]) -> list[str]:
    """Return the ids that moved between two id tables."""
    moved: list[str] = []
    for key, old in sorted(committed.items()):
        new = fresh.get(key)
        if new is None:
            moved.append(f"{key[0]}.{key[1]}: id {old} -> removed")
        elif new != old:
            moved.append(f"{key[0]}.{key[1]}: id {old} -> {new}")
    return moved


def committed_ids(root: Path) -> dict[tuple[str, str], int]:
    """Read the parameter ids out of the committed registry JSON.

    The JSON is the artefact that carries the ids for everybody who reads the
    registry without importing Python, so it is also the record the drift gate
    compares the fresh ids against.
    """
    path = root / "aurcore/schema/modules.json"
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    out: dict[tuple[str, str], int] = {}
    for code, module in document.items():
        if not isinstance(module, dict) or not code.startswith("AUR_"):
            continue
        params = module.get("params")
        if not isinstance(params, list):
            continue
        for param in params:
            if isinstance(param, dict) and "name" in param and "id" in param:
                try:
                    out[(code, str(param["name"]))] = int(param["id"])
                except (TypeError, ValueError):
                    continue
    return out


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def print_summary(reg: Registry) -> None:
    """Print the module / parameter table."""
    print(f"{len(reg.codes)} modules, {reg.total_params} parameters")
    print()
    width = max((len(c) for c in reg.codes), default=4)
    for code in reg.codes:
        spec = reg.specs[code]
        print(
            f"{code:<{width}}  {len(spec.params):>3} params  "
            f"{spec.category:<11} {spec.edition:<8} {spec.display_name}"
        )
        for desc in spec.params:
            extra = f"  [{', '.join(desc.choices)}]" if desc.choices else ""
            print(
                f"{'':<{width}}    {reg.param_id[code][desc.name]:>3} {desc.name:<26} "
                f"{desc.type:<5} default={_md_default(desc)!r} range={_md_range(desc)} "
                f"unit={desc.unit!r}{extra}"
            )
        print()


def raw_defaults(path: Path | None = None) -> dict[tuple[str, str], str]:
    """Return the raw ``(code, param) -> default text`` of the canonical table.

    Only used for reporting: the canonical values always come from
    :func:`aurcore.schema.load`.
    """
    tsv = Path(path) if path is not None else ROOT / TSV_REL
    out: dict[tuple[str, str], str] = {}
    if not tsv.is_file():
        return out
    for raw in tsv.read_text(encoding="utf-8").split("\n"):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        out[(parts[0].strip(), parts[5].strip())] = parts[7].strip()
    return out


def print_enum_advisory(reg: Registry, raw: dict[tuple[str, str], str] | None = None) -> None:
    """Warn about enum parameters whose canonical default was collapsed.

    The canonical table stores the default of an enum parameter as the *index*
    of a choice, while ``ParamDesc`` only accepts a value that is one of the
    choices, so the index is resolved onto the first choice by
    ``ParamDesc.__post_init__``.  Where the table asked for another choice the
    difference is reported here.  The generated artefacts stay faithful to
    ``aurcore.schema.load()``.
    """
    raw = raw or {}
    collapsed: list[str] = []
    for code in reg.codes:
        for desc in reg.specs[code].params:
            if desc.type != "enum" or not desc.choices:
                continue
            text = raw.get((code, desc.name), "")
            if not text.lstrip("-").isdigit():
                continue  # a literal choice, nothing was collapsed
            index = int(text)
            if index == 0 or index >= len(desc.choices):
                continue
            collapsed.append(
                f"{code}.{desc.name}: table default index {index} "
                f"({desc.choices[index]!r}) resolved to {desc.default!r}"
            )
    if collapsed:
        print(
            f"NOTE: {len(collapsed)} enum parameter(s) had a non-zero default index in "
            "the canonical table; ParamDesc stores a choice, so the index is resolved "
            "onto the first choice:"
        )
        for item in collapsed:
            print(f"  - {item}")
        print()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codegen_registry",
        description="Generate the Auralis module registry artefacts from params.tsv.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and exit non-zero if the committed files differ",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root that holds the outputs (default: the tool's repository)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="also print the module and parameter table (the artefacts are still written)",
    )
    parser.add_argument(
        "--properties",
        action="store_true",
        help="run the parameter id property checks and report the result",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print problems",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: write the artefacts, check them for drift, or check the ids."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    specs, widened = load_canonical(root / TSV_REL)
    reg = build_registry(specs)
    rendered = render_all(reg)

    if widened:
        print(
            f"NOTE: {widened} row(s) of {TSV_REL} had 12 columns; they were widened "
            "in memory so the canonical loader could read them. Fix the table.",
            file=sys.stderr,
        )

    if args.properties:
        problems = check_id_invariants(reg) + check_id_stability(reg)
        if problems:
            print("ID PROPERTIES: FAILED")
            for problem in problems:
                print(f"  {problem}")
            return 1
        if not args.quiet:
            print(
                f"ID PROPERTIES: OK — {len(reg.codes)} modules, {reg.total_params} "
                "parameters; the ids are per module and do not move when the "
                "registry grows."
            )
        return 0

    if args.check:
        problems: list[str] = []
        for rel, text in rendered.items():
            path = root / rel
            if not path.is_file():
                problems.append(f"missing: {rel}")
                continue
            committed = path.read_text(encoding="utf-8")
            if committed != text:
                problems.append(f"drifted: {rel}")
                problems.append(
                    f"    {diff_line_count(text, committed)} line(s) differ"
                )
                for line in diff_summary(text, committed):
                    problems.append(f"    {line}")
        moves = id_moves(committed_ids(root), id_table(reg))
        if moves:
            problems.append(f"parameter ids moved ({len(moves)}):")
            for move in moves[:20]:
                problems.append(f"    {move}")
            if len(moves) > 20:
                problems.append(f"    ... and {len(moves) - 20} more")
        invariant_problems = check_id_invariants(reg)
        if invariant_problems:
            problems.append("the id model itself is broken:")
            for problem in invariant_problems:
                problems.append(f"    {problem}")
        if problems:
            print("CODEGEN DRIFT: the committed files are out of date.")
            for problem in problems:
                print(f"  {problem}")
            print("Run: python tools/codegen_registry.py")
            return 1
        if not args.quiet:
            print(f"CODEGEN DRIFT: none — {len(rendered)} file(s) up to date.")
        return 0

    written = write_outputs(root, rendered)
    if not args.quiet:
        for rel in written:
            print(f"wrote {rel}")
    if args.summary:
        print_summary(reg)
        print_enum_advisory(reg, raw_defaults(root / TSV_REL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
