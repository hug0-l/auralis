#!/usr/bin/env python3
"""
tools/aax_application_checklist.py — the AAX identifier checklist.

WHAT THIS IS
------------
An AAX plug-in is identified by a triple of four character codes: the
*manufacturer*, the *product* and the *plug* (the channel configuration).
Those codes live in a **restricted alphabet**: exactly four characters drawn
from the uppercase ASCII letters and the digits.  No spaces, no punctuation,
no underscores.  One stray character is the difference between a plug-in the
host loads and one it refuses.

This tool reads the canonical parameter table (``aurcore/schema/params.tsv``),
derives the identifier triple of every module, checks every derived identifier
against the alphabet and the length rule, and prints the result as a markdown
table so that the AAX application document is *generated* from the table
rather than transcribed from it.

WHAT IT CHECKS
--------------
    1. every identifier component is exactly four characters long;
    2. every identifier component uses only ``[A-Z0-9]``;
    3. the manufacturer id is the one the plug-in layer registers (``AURA``);
    4. the product ids are unique across the whole registry;
    5. the (manufacturer, product, plug) triples are unique;
    6. the product ids this tool derives are the ones
       ``plugins/identity.py`` derives (drift check).

EXIT CODE
---------
    0   no violation
    1   at least one violation was found
    2   the tool could not run (the canonical table is missing or malformed)

DEPENDENCIES
------------
The standard library only.  The canonical table is parsed directly, so the
tool runs on a machine that has no DSP dependencies installed.  The product id
allocation is mirrored from ``plugins/identity.py``; when that module can be
imported the two are compared and any disagreement is reported as a
violation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]

#: The canonical parameter table, relative to the repository root.
TSV_REL = "aurcore/schema/params.tsv"

#: The AAX manufacturer id.  Four characters, shared with every other
#: plug-in format so that one module carries one identity across formats.
MANUFACTURER_ID = "AURA"

#: The AAX plug ids: the three channel configurations of every module.
PLUG_IDS = ("MONO", "M2ST", "STER")

#: The length of every component of an AAX effect identifier.
IDENTIFIER_LENGTH = 4

#: The AAX identifier alphabet: uppercase ASCII letters and digits.
IDENTIFIER_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: The identifier rule as one regular expression.
IDENTIFIER_PATTERN = re.compile(r"\A[A-Z0-9]{4}\Z")

#: The base-36 alphabet used when a natural product code has to be replaced.
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ---------------------------------------------------------------------------
# the canonical table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamRow:
    """One parameter of one module, as it appears in the canonical table."""

    name: str
    type: str
    default: str
    minimum: str
    maximum: str
    unit: str
    choices: tuple[str, ...]
    group: str

    @property
    def is_discrete(self) -> bool:
        """Return True when the parameter is a discrete control.

        A discrete control takes its value from a finite set of states: an
        enum (one state per choice) or a boolean (two states).  A float or an
        int is continuous and becomes a knob.
        """
        return self.type in ("enum", "bool")

    @property
    def control_kind(self) -> str:
        """Return the AAX control kind of the parameter.

        ``switch`` for a discrete control, ``knob`` for a continuous one.
        """
        return "switch" if self.is_discrete else "knob"


@dataclass(frozen=True)
class ModuleRow:
    """One module of the canonical table."""

    code: str
    display_name: str
    category: str
    edition: str
    element: str
    params: tuple[ParamRow, ...]

    @property
    def param_count(self) -> int:
        """The number of parameters the module exposes."""
        return len(self.params)

    @property
    def discrete_count(self) -> int:
        """The number of discrete controls (enums and booleans)."""
        return sum(1 for param in self.params if param.is_discrete)


def load_modules(path: Path) -> list[ModuleRow]:
    """Parse the canonical parameter table.

    The parser mirrors ``aurcore.schema.load``: comment lines and blank lines
    are skipped, and a row that omits the (empty) choices column is widened
    in memory.

    Raises
    ------
    OSError
        The table cannot be read.
    ValueError
        A row is malformed.
    """
    # ``dict`` keeps the insertion order, so the registry order of the table
    # is preserved without a second list.
    by_code: dict[str, ModuleRow] = {}

    with path.open("r", encoding="utf-8-sig") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 12:
                # the choices column may be omitted when it is empty
                parts.insert(11, "")
            if len(parts) != 13:
                raise ValueError(f"{path}:{lineno}: expected 13 fields, got {len(parts)}")
            (
                code, disp, cat, ed, elem, name, ptype, default,
                minimum, maximum, unit, choices, group,
            ) = parts[:13]
            code = code.strip()
            param = ParamRow(
                name=name.strip(),
                type=ptype.strip(),
                default=default.strip(),
                minimum=minimum.strip(),
                maximum=maximum.strip(),
                unit=unit.strip(),
                choices=tuple(c for c in choices.split("|") if c),
                group=group.strip(),
            )
            existing = by_code.get(code)
            if existing is None:
                by_code[code] = ModuleRow(
                    code=code,
                    display_name=disp.strip(),
                    category=cat.strip(),
                    edition=ed.strip(),
                    element=elem.strip(),
                    params=(param,),
                )
            else:
                by_code[code] = ModuleRow(
                    code=existing.code,
                    display_name=existing.display_name,
                    category=existing.category,
                    edition=existing.edition,
                    element=existing.element,
                    params=existing.params + (param,),
                )

    return list(by_code.values())


# ---------------------------------------------------------------------------
# the identifier scheme
# ---------------------------------------------------------------------------
def natural_product_code(module_code: str) -> str:
    """Return the natural four character product code of a module.

    The natural code is the first four alphanumeric characters of the module
    code's suffix, upper cased and padded with zeros.  ``AUR_DECLICK``
    therefore wants ``DECL`` and ``AUR_EQ`` wants ``EQ00``.
    """
    suffix = module_code
    if suffix.startswith("AUR_"):
        suffix = suffix[4:]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", suffix).upper()
    return (cleaned + "0000")[:IDENTIFIER_LENGTH]


def allocate_product_codes(module_codes: Sequence[str]) -> dict[str, str]:
    """Assign a unique four character product code to every module.

    This is a mirror of ``plugins.identity._product_codes``: the allocation is
    computed over the *whole* registry, so a module keeps its product code no
    matter which subset of modules is asked for.

    1. every module takes its natural code;
    2. when several modules want the same natural code, the first one in
       registry order keeps it and the others are re-assigned a code derived
       from a hash of their own module code, chosen so that it neither
       collides with another re-assigned module nor steals the natural code of
       some other module.
    """
    ordered = list(module_codes)
    natural = {code: natural_product_code(code) for code in ordered}
    reserved = set(natural.values())

    groups: dict[str, list[str]] = {}
    for code in ordered:
        groups.setdefault(natural[code], []).append(code)

    assigned: dict[str, str] = {}
    for base, members in groups.items():
        if len(members) == 1:
            assigned[members[0]] = base
            continue
        taken: set[str] = {base}
        keeper = members[0]
        assigned[keeper] = base
        for code in members[1:]:
            digest = _sha256(code)
            candidate = ""
            for byte in digest:
                candidate = base[:3] + _BASE36[byte % 36]
                if candidate not in taken and candidate not in reserved:
                    break
            else:
                for byte in digest:
                    for second in digest:
                        candidate = base[:2] + _BASE36[byte % 36] + _BASE36[second % 36]
                        if candidate not in taken and candidate not in reserved:
                            break
                    else:
                        continue
                    break
            assigned[code] = candidate
            taken.add(candidate)

    return assigned


def _sha256(text: str) -> bytes:
    """Return the SHA-256 digest of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).digest()


def canonical_product_codes(module_codes: Sequence[str]) -> dict[str, str] | None:
    """Return the product codes of ``plugins.identity``, or None.

    ``plugins/identity.py`` owns the allocation; this tool mirrors it so that
    it can run stand-alone.  When the owner can be imported the two results
    are compared and a disagreement is reported as a violation.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from plugins.identity import _product_codes  # type: ignore
    except Exception:  # noqa: BLE001 - the owner is optional by design
        return None
    try:
        return dict(_product_codes(list(module_codes)))
    except Exception:  # noqa: BLE001 - the owner is optional by design
        return None


def canonical_manufacturer_id() -> str | None:
    """Return the manufacturer id of the plug-in layer, or None."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from plugins.identity import PLUGIN_MANUFACTURER_ID  # type: ignore
    except Exception:  # noqa: BLE001 - the owner is optional by design
        return None
    return str(PLUGIN_MANUFACTURER_ID)


# ---------------------------------------------------------------------------
# violations
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Violation:
    """One broken rule.

    Attributes
    ----------
    where:
        What the violation is about, e.g. ``"AUR_DECLICK / product id"``.
    rule:
        The name of the rule that was broken.
    detail:
        A human readable explanation.
    """

    where: str
    rule: str
    detail: str

    def render(self) -> str:
        """Render the violation as one line."""
        return f"{self.where}: [{self.rule}] {self.detail}"


def check_identifier(value: str, *, where: str, rule_prefix: str) -> list[Violation]:
    """Check one identifier component against the AAX rules.

    The rules are the length rule (exactly four characters) and the alphabet
    rule (uppercase ASCII letters and digits only).
    """
    problems: list[Violation] = []
    if not value:
        problems.append(Violation(where, f"{rule_prefix}/empty", "the identifier is empty"))
        return problems
    if len(value) != IDENTIFIER_LENGTH:
        problems.append(
            Violation(
                where,
                f"{rule_prefix}/length",
                f"expected {IDENTIFIER_LENGTH} characters, got {len(value)} ({value!r})",
            )
        )
    illegal = sorted({char for char in value if char not in IDENTIFIER_ALPHABET})
    if illegal:
        problems.append(
            Violation(
                where,
                f"{rule_prefix}/alphabet",
                "contains character(s) outside [A-Z0-9]: "
                + ", ".join(repr(char) for char in illegal),
            )
        )
    if value == "0000":
        problems.append(
            Violation(
                where,
                f"{rule_prefix}/placeholder",
                "the identifier is the padding placeholder 0000",
            )
        )
    if not problems and not IDENTIFIER_PATTERN.match(value):
        # Belt and braces: the two rules above cover every case, so reaching
        # this point means one of them changed without the other.
        problems.append(
            Violation(
                where,
                f"{rule_prefix}/format",
                f"{value!r} is not a valid AAX identifier",
            )
        )
    return problems


# ---------------------------------------------------------------------------
# the checklist
# ---------------------------------------------------------------------------
@dataclass
class ModuleIdentifiers:
    """The identifiers and counts of one module."""

    code: str
    display_name: str
    category: str
    edition: str
    product_id: str
    natural_product_id: str
    manufacturer_id: str
    param_count: int
    discrete_count: int
    knob_count: int
    product_is_natural: bool

    @property
    def effect_ids(self) -> tuple[tuple[str, str, str], ...]:
        """The three (manufacturer, product, plug) triples of the module."""
        return tuple((self.manufacturer_id, self.product_id, plug) for plug in PLUG_IDS)

    def effect_id_text(self, plug: str) -> str:
        """Return one triple as ``MANU:PROD:PLUG``."""
        return f"{self.manufacturer_id}:{self.product_id}:{plug}"


@dataclass
class Checklist:
    """The whole checklist: every module, and every violation found."""

    modules: list[ModuleIdentifiers] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    manufacturer_id: str = MANUFACTURER_ID

    @property
    def ok(self) -> bool:
        """True when nothing was violated."""
        return not self.violations

    @property
    def param_count(self) -> int:
        """The total number of parameters across every module."""
        return sum(module.param_count for module in self.modules)

    @property
    def discrete_count(self) -> int:
        """The total number of discrete controls across every module."""
        return sum(module.discrete_count for module in self.modules)

    @property
    def reassigned(self) -> list[ModuleIdentifiers]:
        """The modules whose product id is not their natural code."""
        return [module for module in self.modules if not module.product_is_natural]


def build_checklist(modules: Sequence[ModuleRow]) -> Checklist:
    """Build the checklist of every module in the canonical table.

    The product ids are the ones the plug-in layer derives, when that layer
    can be imported; otherwise the local mirror is used.  Every derived
    identifier is validated against the AAX alphabet and length rules.
    """
    codes = [module.code for module in modules]
    mirrored = allocate_product_codes(codes)
    owned = canonical_product_codes(codes)
    assigned = owned if owned is not None else mirrored

    violations: list[Violation] = []

    manufacturer = MANUFACTURER_ID
    owner_manufacturer = canonical_manufacturer_id()
    if owner_manufacturer is not None and owner_manufacturer != MANUFACTURER_ID:
        violations.append(
            Violation(
                "manufacturer id",
                "manufacturer/mismatch",
                f"the plug-in layer registers {owner_manufacturer!r}, this tool expects "
                f"{MANUFACTURER_ID!r}",
            )
        )
    else:
        manufacturer = owner_manufacturer or MANUFACTURER_ID

    violations.extend(check_identifier(manufacturer, where="manufacturer id",
                                       rule_prefix="manufacturer"))

    if owned is not None and owned != mirrored:
        for code in codes:
            if owned.get(code) != mirrored.get(code):
                violations.append(
                    Violation(
                        f"{code} / product id",
                        "product/drift",
                        f"plugins/identity.py derives {owned.get(code)!r}, this tool derives "
                        f"{mirrored.get(code)!r}",
                    )
                )

    seen_products: dict[str, str] = {}
    seen_triples: dict[tuple[str, str, str], str] = {}

    checklist = Checklist(manufacturer_id=manufacturer)

    for module in modules:
        product = assigned.get(module.code, "")
        natural = natural_product_code(module.code)
        entry = ModuleIdentifiers(
            code=module.code,
            display_name=module.display_name,
            category=module.category,
            edition=module.edition,
            product_id=product,
            natural_product_id=natural,
            manufacturer_id=manufacturer,
            param_count=module.param_count,
            discrete_count=module.discrete_count,
            knob_count=module.param_count - module.discrete_count,
            product_is_natural=(product == natural),
        )
        checklist.modules.append(entry)

        violations.extend(
            check_identifier(product, where=f"{module.code} / product id", rule_prefix="product")
        )
        if product in seen_products:
            violations.append(
                Violation(
                    f"{module.code} / product id",
                    "product/duplicate",
                    f"{product!r} is already used by {seen_products[product]}",
                )
            )
        else:
            seen_products[product] = module.code

        for plug in PLUG_IDS:
            violations.extend(
                check_identifier(plug, where=f"{module.code} / plug id", rule_prefix="plug")
            )
            triple = (manufacturer, product, plug)
            if triple in seen_triples:
                violations.append(
                    Violation(
                        f"{module.code} / {plug}",
                        "effect/duplicate",
                        f"the effect id {':'.join(triple)} is already used by "
                        f"{seen_triples[triple]}",
                    )
                )
            else:
                seen_triples[triple] = module.code

    checklist.violations = violations
    return checklist


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def render_markdown(checklist: Checklist) -> str:
    """Render the checklist as a markdown table.

    The table is the artefact the AAX application document embeds, so it is
    written to be pasted as-is: one header row, one rule row and one row per
    module, in canonical registry order.
    """
    lines = [
        "| # | Module | Code | Product id | MONO | M2ST | STER | Params | Discrete |",
        "|---:|---|---|---|---|---|---:|---:|---:|",
    ]
    for index, module in enumerate(checklist.modules):
        lines.append(
            "| {index} | {name} | `{code}` | `{product}` | `{mono}` | `{m2st}` | `{ster}` | "
            "{params} | {discrete} |".format(
                index=index,
                name=module.display_name,
                code=module.code,
                product=module.product_id,
                mono=module.effect_id_text("MONO"),
                m2st=module.effect_id_text("M2ST"),
                ster=module.effect_id_text("STER"),
                params=module.param_count,
                discrete=module.discrete_count,
            )
        )
    return "\n".join(lines) + "\n"


def render_report(checklist: Checklist) -> str:
    """Render the checklist as a human readable report."""
    lines = [
        "=" * 78,
        " AURALIS AAX IDENTIFIER CHECKLIST",
        "=" * 78,
        "",
        f"manufacturer id      : {checklist.manufacturer_id}",
        f"modules             : {len(checklist.modules)}",
        f"parameters          : {checklist.param_count}",
        f"discrete controls   : {checklist.discrete_count}",
        f"plug ids per module : {', '.join(PLUG_IDS)}",
        "",
        render_markdown(checklist),
        "",
    ]
    if checklist.reassigned:
        lines.append("product ids that are not the natural code (collision resolved):")
        for module in checklist.reassigned:
            lines.append(
                f"  {module.code:<24} {module.natural_product_id} -> {module.product_id}"
            )
        lines.append("")
    if checklist.violations:
        lines.append(f"{len(checklist.violations)} violation(s):")
        for violation in checklist.violations:
            lines.append(f"  {violation.render()}")
    else:
        lines.append("no violations: every identifier obeys the AAX rules.")
    return "\n".join(lines) + "\n"


def render_json(checklist: Checklist) -> str:
    """Render the checklist as a JSON document."""
    payload = {
        "manufacturer_id": checklist.manufacturer_id,
        "plug_ids": list(PLUG_IDS),
        "identifier_alphabet": IDENTIFIER_ALPHABET,
        "identifier_length": IDENTIFIER_LENGTH,
        "module_count": len(checklist.modules),
        "parameter_count": checklist.param_count,
        "discrete_count": checklist.discrete_count,
        "violations": [
            {"where": v.where, "rule": v.rule, "detail": v.detail} for v in checklist.violations
        ],
        "modules": [
            {
                "code": module.code,
                "display_name": module.display_name,
                "category": module.category,
                "edition": module.edition,
                "manufacturer_id": module.manufacturer_id,
                "product_id": module.product_id,
                "natural_product_id": module.natural_product_id,
                "product_is_natural": module.product_is_natural,
                "effect_ids": [list(triple) for triple in module.effect_ids],
                "param_count": module.param_count,
                "discrete_count": module.discrete_count,
                "knob_count": module.knob_count,
            }
            for module in checklist.modules
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="aax_application_checklist",
        description=(
            "Derive the AAX identifiers of every module from the canonical parameter "
            "table, validate them against the AAX alphabet and length rules, and "
            "print the result as a markdown table."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root that holds the canonical table",
    )
    parser.add_argument(
        "--tsv",
        type=Path,
        default=None,
        help="the canonical parameter table (defaults to <root>/aurcore/schema/params.tsv)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "report", "json"),
        default="markdown",
        help="what to print on stdout (default: markdown, the table the document embeds)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the output to this file instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checklist and report the result."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    tsv = Path(args.tsv).resolve() if args.tsv else root / TSV_REL

    try:
        modules = load_modules(tsv)
    except OSError as exc:
        print(f"cannot read the canonical parameter table {tsv}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not modules:
        print(f"{tsv}: the table holds no modules", file=sys.stderr)
        return 2

    checklist = build_checklist(modules)

    if args.format == "markdown":
        text = render_markdown(checklist)
    elif args.format == "report":
        text = render_report(checklist)
    else:
        text = render_json(checklist)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)

    for violation in checklist.violations:
        print(violation.render(), file=sys.stderr)

    return 1 if checklist.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
