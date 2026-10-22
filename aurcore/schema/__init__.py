"""
aurcore.schema — loader for the canonical parameter table.

`params.tsv` is the single source of truth for every module's parameter set.
Module implementations must match it exactly; :func:`validate_module` checks that
and the test-suite asserts it for all modules.

The table
---------
The table is a tab separated file with seventeen columns::

    code  display_name  category  edition  element  param  type  default
    min  max  curve  exponent  unit  choices  group  help  aliases

The last four columns are what the surface of a module needs and the first
thirteen did not carry:

``curve``
    The law the travel of the parameter follows: ``linear``, ``log`` or
    ``taper``.  A row without the column is linear.
``exponent``
    The exponent of the taper law, ``v = lo + (hi - lo) * n ** p``.  It is
    carried beside the range because the shape of the taper belongs to the
    parameter and not to the law.
``help``
    The sentence that says what the parameter does.
``aliases``
    The names the parameter had before it was renamed, separated by ``|``.
    A document that carries an old name still loads.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..params import CURVE_TAPER, CURVES, TAPER_EXPONENT, ParamDesc

TSV_PATH = os.path.join(os.path.dirname(__file__), "params.tsv")

#: The number of columns of the canonical table.
COLUMN_COUNT = 17

#: The number of columns a row of the table had before the curve, the
#: exponent, the help and the aliases were carried.  A row of that width is
#: still read: it describes a linear parameter without a help sentence.
LEGACY_COLUMN_COUNT = 13


@dataclass
class ModuleSpec:
    code: str
    display_name: str
    category: str
    edition: str = "standard"
    element: str = ""
    params: list[ParamDesc] = field(default_factory=list)

    @property
    def param_names(self) -> list[str]:
        return [p.name for p in self.params]


def _parse_type(text: str):
    text = text.strip()
    if text == "float":
        return "float"
    if text == "int":
        return "int"
    if text == "bool":
        return "bool"
    if text == "enum":
        return "enum"
    raise ValueError(f"unknown param type {text!r}")


def _parse_value(text: str, ptype: str):
    text = text.strip()
    if ptype == "bool":
        return text.lower() in ("1", "true", "yes")
    if ptype == "enum":
        return text
    if ptype == "int":
        return int(float(text))
    return float(text)


def _parse_curve(text: str) -> str:
    curve = text.strip() or "linear"
    if curve not in CURVES:
        raise ValueError(f"unknown curve {curve!r}")
    return curve


def _parse_exponent(text: str, curve: str) -> float:
    """The exponent of the taper law.

    The exponent only means something when the curve is a taper: a row that
    does not name one gets the exponent of the law, and every other row gets
    the neutral one, because a straight line and a logarithm do not have a
    shape to carry.
    """
    if text.strip():
        return float(text)
    return TAPER_EXPONENT if curve == CURVE_TAPER else 1.0


def _parse_aliases(text: str) -> tuple[str, ...]:
    return tuple(a.strip() for a in text.split("|") if a.strip())


def load(path: str | None = None) -> dict[str, ModuleSpec]:
    """Parse the parameter table into module specifications."""
    path = path or TSV_PATH
    specs: dict[str, ModuleSpec] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 12:
                # a row of the old table dropped its empty choices column
                parts = parts[:11] + [""] + parts[11:]
            if len(parts) == LEGACY_COLUMN_COUNT:
                # a row of the old table: the curve, the exponent, the help
                # and the aliases are the columns that were added to it
                parts = parts[:10] + ["linear", ""] + parts[10:] + ["", ""]
            if len(parts) != COLUMN_COUNT:
                raise ValueError(
                    f"{path}:{lineno}: expected {COLUMN_COUNT} fields, got {len(parts)}"
                )
            (
                code,
                disp,
                cat,
                ed,
                elem,
                pname,
                ptype,
                pdef,
                pmin,
                pmax,
                curve,
                exponent,
                unit,
                choices,
                group,
                help_text,
                aliases,
            ) = parts[:COLUMN_COUNT]
            ptype = _parse_type(ptype)
            desc = ParamDesc(
                name=pname.strip(),
                type=ptype,
                default=_parse_value(pdef, ptype),
                minimum=float(pmin),
                maximum=float(pmax),
                unit=unit.strip(),
                choices=tuple(c for c in choices.split("|") if c) if ptype == "enum" else (),
                group=group.strip(),
                curve=_parse_curve(curve),
                curve_exponent=_parse_exponent(exponent, curve),
                help=help_text.strip(),
                aliases=_parse_aliases(aliases),
            )
            spec = specs.get(code)
            if spec is None:
                spec = ModuleSpec(
                    code=code.strip(),
                    display_name=disp.strip(),
                    category=cat.strip(),
                    edition=ed.strip() or "standard",
                    element=elem.strip(),
                )
                specs[spec.code] = spec
            if any(p.name == desc.name for p in spec.params):
                raise ValueError(f"{path}:{lineno}: duplicate param {desc.name!r} in {code}")
            spec.params.append(desc)
    return specs


def validate_module(spec: ModuleSpec, descs: list[ParamDesc]) -> list[str]:
    """Compare an implemented module's parameters against the canonical table.

    Returns a list of human-readable differences (empty means the module matches).
    """
    problems: list[str] = []
    want = {p.name: p for p in spec.params}
    have = {p.name: p for p in descs}
    for name in want:
        if name not in have:
            problems.append(f"missing parameter {name!r}")
    for name in have:
        if name not in want:
            problems.append(f"unexpected parameter {name!r}")
    for name, w in want.items():
        h = have.get(name)
        if h is None:
            continue
        if h.type != w.type:
            problems.append(f"{name!r}: type {h.type!r} != {w.type!r}")
        if w.type == "enum" and tuple(h.choices) != tuple(w.choices):
            problems.append(f"{name!r}: choices {h.choices} != {w.choices}")
        if abs(float(h.minimum) - w.minimum) > 1e-9:
            problems.append(f"{name!r}: min {h.minimum} != {w.minimum}")
        if abs(float(h.maximum) - w.maximum) > 1e-9:
            problems.append(f"{name!r}: max {h.maximum} != {w.maximum}")
        if isinstance(w.default, float) and isinstance(h.default, (int, float)):
            if abs(float(h.default) - w.default) > 1e-9:
                problems.append(f"{name!r}: default {h.default} != {w.default}")
        elif h.default != w.default:
            problems.append(f"{name!r}: default {h.default!r} != {w.default!r}")
        if h.unit != w.unit:
            problems.append(f"{name!r}: unit {h.unit!r} != {w.unit!r}")
        if h.group != w.group:
            problems.append(f"{name!r}: group {h.group!r} != {w.group!r}")
        if h.curve != w.curve:
            problems.append(f"{name!r}: curve {h.curve!r} != {w.curve!r}")
        if w.curve == CURVE_TAPER and abs(
            float(h.curve_exponent) - float(w.curve_exponent)
        ) > 1e-9:
            problems.append(
                f"{name!r}: curve exponent {h.curve_exponent} != {w.curve_exponent}"
            )
        if h.help != w.help:
            problems.append(f"{name!r}: help {h.help!r} != {w.help!r}")
        if tuple(h.aliases) != tuple(w.aliases):
            problems.append(f"{name!r}: aliases {h.aliases} != {w.aliases}")
    return problems


def dump_json(path: str) -> None:
    """Write the canonical table out as JSON (used by the codegen tool)."""
    import json

    specs = load()
    data = {
        s.code: {
            "code": s.code,
            "display_name": s.display_name,
            "category": s.category,
            "edition": s.edition,
            "element": s.element,
            "params": [p.to_dict() for p in s.params],
        }
        for s in specs.values()
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
