#!/usr/bin/env python3
"""
scripts/aax_tables.py — the AAX application package generator.

WHAT THIS IS
------------
The AAX format is built on a machine that has a copy of the SDK, and the
SDK may not be redistributed, so this repository carries the *adapter*
(`plugins/aax/`) and the **application package** this tool produces: the
data that describes the plug-in to the host.  The adapter is built from
this package, so the two plug-in formats cannot drift apart.

    the effect identifiers      manufacturer / product / plug
    the parameter identifiers   the same numbers the VST3 adapter uses
    the page tables             one page layout per module
    the labels                 the short text drawn on a control
    the ranges and defaults     the numbers the core works in
    the category                what the host files the plug-in under

None of that needs the SDK, and all of it comes from one place: the
canonical parameter table (``aurcore/schema/params.tsv``).  This tool is
the only thing that writes the package.

THE RULES
---------
The package is generated, never written by hand.  The rules it encodes
are the ones stated in ``docs/AAX_APPLICATION.md``:

    * a module's identifiers are the ones the plug-in layer derives
      (``plugins/identity.py``): manufacturer ``AURA``, one product code
      per module, three plug codes per module (``MONO``, ``M2ST``,
      ``STER``);
    * a parameter's identifier is the one the generated registry
      publishes — the position of the parameter inside its own module —
      which is the number the VST3 adapter carries in the identity
      record, so a session written by one format means the same thing in
      another;
    * a parameter's group decides the layout order; a page holds one
      switch slot and three knob slots;
    * a label is the full name when it fits the budget, otherwise the
      name without its trailing word, otherwise an abbreviation, otherwise
      a truncated name marked with an ellipsis.

DETERMINISM
-----------
The output is a pure function of the canonical table: no timestamps, no
paths, no dictionary-order surprises.  Running the tool twice produces
byte-identical files, which is what ``--check`` verifies.

USAGE
-----
    python scripts/aax_tables.py               # write the package
    python scripts/aax_tables.py --check       # fail when the tree drifted
    python scripts/aax_tables.py --out-dir D   # write the package elsewhere
    python scripts/aax_tables.py --sync-doc    # refresh the worked example
                                               # in the application document

Exit codes: ``0`` success, ``1`` the tree drifted (with ``--check``),
``2`` the tool could not run.

DEPENDENCIES
------------
The standard library only.  The canonical table is parsed directly, so
the tool runs on a machine that has no DSP dependencies installed; when
``plugins.identity`` can be imported the derived identifiers are checked
against it and a disagreement is an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: The canonical parameter table, relative to the repository root.
TSV_REL = "aurcore/schema/params.tsv"

#: The human readable half of the package.
MARKDOWN_REL = "docs/AAX_PAGE_TABLES.md"

#: The machine readable half of the package.
JSON_REL = "docs/aax_page_tables.json"

#: The application document, whose worked example the generator owns.
DOC_REL = "docs/AAX_APPLICATION.md"

#: The module whose page table the application document quotes.
WORKED_EXAMPLE_MODULE = "AUR_SPECTRALDENOISE"

#: The AAX manufacturer id: four characters, shared by every plug-in format
#: so that one module carries one identity across formats.
MANUFACTURER_ID = "AURA"

#: The three channel configurations of every module.  A mono, a
#: mono-to-stereo and a stereo build are three different plug-ins to the
#: host, so they need three different ids.
PLUG_IDS: tuple[str, ...] = ("MONO", "M2ST", "STER")

#: The base the SDK-facing step adds to a registry parameter id.  The low
#: range is reserved for the plug-in's own controls; the exact size of the
#: reserved range is confirmed against the SDK's parameter-id header before
#: the first shipping build.
PARAM_ID_BASE = 256

#: The parameter id of the bypass switch.  It is the same in every plug-in
#: format of the family, and it is the id the reference plug-ins use, so a
#: host's automation map lines up with ours.
BYPASS_PARAM_ID = "1652125811"

#: The name the host shows for the bypass switch.
BYPASS_LABEL = "Bypass"

#: The sentence that explains the bypass switch.
BYPASS_HELP = "Bypass the module: the audio passes through it unchanged."

#: The label budget, in characters.  A label has to fit on a control.
LABEL_BUDGET = 12

#: The slots a page holds, in the order they are written.
SLOT_ORDER: tuple[str, ...] = ("switch", "knob1", "knob2", "knob3")

#: The knob slots of a page.
KNOB_SLOTS: tuple[str, ...] = ("knob1", "knob2", "knob3")

#: The parameter types that become a switch rather than a knob.
DISCRETE_TYPES: tuple[str, ...] = ("enum", "bool")

# ---------------------------------------------------------------------------
# the page table document
# ---------------------------------------------------------------------------
#: The name of the page table resource inside the plug-in bundle.  The
#: plug-in declares it to the host with ``AAX_eResourceType_PageTable`` and
#: the host looks for the file in ``Contents/Resources`` of the bundle, so
#: the name is the same in the declaration and in the bundle.
PAGE_TABLE_RESOURCE = "AuralisPages.xml"

#: The directory the generated page table documents live in, relative to
#: the repository root.  The document is a resource of the plug-in, so it
#: is generated next to the adapter and copied into the bundle by the
#: build - the way the format's own examples keep their page table next to
#: their sources and copy it into the bundle.
RESOURCES_REL = "plugins/aax/resources"

#: The name of the layout the document holds.  The host looks a plug-in up
#: by the triad of its manufacturer, product and plug id, and the layout
#: tells it which set of page tables to use.
PAGE_TABLE_LAYOUT = "StandardLayout"

#: The version of the page table schema the document is written in.
PAGE_TABLE_VERS = "1.0.0.0"

#: The page table types the document holds.  ``PgTL`` is the generic one:
#: the host lays the controls of the plug-in out with it.
PAGE_TABLE_TYPE = "PgTL"

#: The number of controls a page holds, for every page table type the
#: document carries.  One control per page is the narrow arrangement every
#: reference plug-in publishes; the second one is the arrangement this
#: project's own layout uses - a switch and three knobs on a page.
PAGE_TABLE_SIZES: tuple[int, ...] = (1, len(SLOT_ORDER))

#: The sizes the short names of the controls are published for.  The host
#: takes the name that fits the space it has, so the ladder of a label is
#: published for the sizes a surface asks for.
NAME_SIZES: tuple[int, ...] = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12)

#: The base-36 alphabet used when a natural product code has to be replaced.
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: The ellipsis that marks a truncated label.
ELLIPSIS = "…"

#: The abbreviations of the parameter names that are still too long after
#: the trailing word is dropped.  This table is the **only** hand
#: maintained text in the label area; it is keyed by parameter *name*, so
#: a name that is shared by several modules reads the same everywhere.
#:
#: A name that is not in this table falls back to the truncation step,
#: which is why the table stays small: it only holds the names for which
#: a truncated word would read badly.
ABBREVIATIONS: dict[str, str] = {
    "Apply Positive Gain": "Pos Gain",
    "Band Strength High": "Band High",
    "Band Strength High-Mid": "Band Hi-Mid",
    "Band Strength Low": "Band Low",
    "Band Strength Low-Mid": "Band Lo-Mid",
    "Center Global Pitch": "Center Pitch",
    "Output Ambience Only": "Amb Only",
    "Output Breaths Only": "Breaths Only",
    "Output Clicks Only": "Clicks Only",
    "Output Crackle Only": "Crackle Only",
    "Output Reverb Only": "Reverb Only",
    "Responsiveness": "Response",
}


class PackageError(Exception):
    """Raised when the package cannot be built from the canonical table."""


# ---------------------------------------------------------------------------
# the canonical table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Param:
    """One parameter of one module, as the canonical table states it."""

    name: str
    type: str
    default_text: str
    minimum_text: str
    maximum_text: str
    unit: str
    choices: tuple[str, ...]
    group: str
    identifier: int = -1
    help: str = ""

    @property
    def is_discrete(self) -> bool:
        """True when the parameter is a discrete control (a switch)."""
        return self.type in DISCRETE_TYPES

    @property
    def control_kind(self) -> str:
        """The kind of control the parameter becomes."""
        return "switch" if self.is_discrete else "knob"

    @property
    def minimum(self) -> float:
        """The lower bound of the parameter, as a number."""
        return float(self.minimum_text)

    @property
    def maximum(self) -> float:
        """The upper bound of the parameter, as a number."""
        return float(self.maximum_text)


@dataclass(frozen=True)
class Module:
    """One module of the canonical table."""

    code: str
    display_name: str
    category: str
    edition: str
    element: str
    params: tuple[Param, ...]


def load_canonical(path: Path) -> list[Module]:
    """Parse the canonical parameter table.

    The parser mirrors ``aurcore.schema.load`` — comment lines and blank
    lines are skipped, and a row that omits the (empty) choices column is
    widened in memory — but it keeps the *text* of the numeric columns, so
    the generated table can print the numbers the way the canonical table
    writes them.
    """
    by_code: dict[str, Module] = {}

    with path.open("r", encoding="utf-8-sig") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 12:
                # the choices column may be omitted when it is empty
                parts.insert(11, "")
            if len(parts) == 17:
                # The canonical table carries the curve of the travel, the
                # exponent of the taper, the help sentence and the names the
                # parameter had before it was renamed, beside the columns it
                # always had.
                (code, disp, cat, ed, elem, name, ptype, default, minimum,
                 maximum, curve, exponent, unit, choices, group, help,
                 aliases) = parts[:17]
            elif len(parts) == 13:
                # The table before the curve, the exponent, the help and the
                # aliases were added.
                (code, disp, cat, ed, elem, name, ptype, default, minimum,
                 maximum, unit, choices, group) = parts[:13]
                curve, exponent, help, aliases = "linear", "", "", ""
            else:
                raise PackageError(f"{path}:{lineno}: expected 13 or 17 fields, got {len(parts)}")
            code = code.strip()
            ptype = ptype.strip()
            if ptype not in ("float", "int", "bool", "enum"):
                raise PackageError(f"{path}:{lineno}: unknown parameter type {ptype!r}")
            param = Param(
                name=name.strip(),
                type=ptype,
                default_text=default.strip(),
                minimum_text=minimum.strip(),
                maximum_text=maximum.strip(),
                unit=unit.strip(),
                choices=tuple(c for c in choices.split("|") if c),
                group=group.strip(),
                help=help.strip(),
            )
            existing = by_code.get(code)
            if existing is None:
                by_code[code] = Module(
                    code=code,
                    display_name=disp.strip(),
                    category=cat.strip(),
                    edition=ed.strip() or "standard",
                    element=elem.strip(),
                    params=(param,),
                )
            else:
                by_code[code] = Module(
                    code=existing.code,
                    display_name=existing.display_name,
                    category=existing.category,
                    edition=existing.edition,
                    element=existing.element,
                    params=existing.params + (param,),
                )

    return list(by_code.values())


def assign_parameter_ids(modules: Sequence[Module]) -> list[Module]:
    """Give every parameter its identifier.

    The identifiers are the ones the generated registry publishes: the
    position of the parameter **inside its own module**, counted from zero.
    That is the numbering the plug-in layer puts into the identity record
    and the generated header, so the numbers in this package are the
    numbers the VST3 adapter uses.

    The id space is per module on purpose: a plug-in implements exactly
    one module, so the ids are unique within the plug-in, and adding or
    removing a module never renumbers another one.
    """
    numbered: list[Module] = []
    for module in modules:
        params = []
        for index, param in enumerate(module.params):
            params.append(
                Param(
                    name=param.name,
                    type=param.type,
                    default_text=param.default_text,
                    minimum_text=param.minimum_text,
                    maximum_text=param.maximum_text,
                    unit=param.unit,
                    choices=param.choices,
                    group=param.group,
                    identifier=index,
                    help=param.help,
                )
            )
        numbered.append(
            Module(
                code=module.code,
                display_name=module.display_name,
                category=module.category,
                edition=module.edition,
                element=module.element,
                params=tuple(params),
            )
        )
    return numbered


# ---------------------------------------------------------------------------
# the identifiers
# ---------------------------------------------------------------------------
def natural_product_code(module_code: str) -> str:
    """Return the natural four character product code of a module.

    The natural code is the first four alphanumeric characters of the
    module code's suffix, upper cased and padded with zeros.  ``AUR_DECLICK``
    therefore wants ``DECL`` and ``AUR_EQ`` wants ``EQ00``.
    """
    suffix = module_code
    if suffix.startswith("AUR_"):
        suffix = suffix[4:]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", suffix).upper()
    return (cleaned + "0000")[:4]


def mirror_product_codes(module_codes: Sequence[str]) -> dict[str, str]:
    """Assign a unique four character product code to every module.

    This is a mirror of ``plugins.identity._product_codes`` so that the
    tool runs without the DSP dependencies; :func:`cross_check` compares
    the two and refuses to build a package when they disagree.
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
        # The first module in registry order keeps the natural code.
        assigned[members[0]] = base
        for code in members[1:]:
            digest = hashlib.sha256(code.encode("utf-8")).digest()
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


def _identity_module():
    """Return ``plugins.identity`` when it can be imported, else ``None``."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from plugins import identity  # noqa: PLC0415 - optional by design
    except Exception:  # noqa: BLE001 - the owner is optional by design
        return None
    return identity


def cross_check(modules: Sequence[Module], products: dict[str, str]) -> list[str]:
    """Compare the derived identifiers with the plug-in layer's own.

    The plug-in layer (``plugins/identity.py``) owns the identifier
    allocation and the registry index.  The package must use the very same
    numbers, so the two are compared and every disagreement is reported.
    """
    identity = _identity_module()
    if identity is None:
        return []

    problems: list[str] = []

    owner_manufacturer = getattr(identity, "AAX_MANUFACTURER_ID", None)
    if owner_manufacturer is not None and str(owner_manufacturer) != MANUFACTURER_ID:
        problems.append(
            f"the plug-in layer registers the manufacturer {owner_manufacturer!r}, "
            f"this tool uses {MANUFACTURER_ID!r}"
        )

    codes = [module.code for module in modules]
    try:
        owned = dict(identity._product_codes(codes))  # noqa: SLF001 - the owner
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        problems.append(f"the plug-in layer could not allocate the product codes: {exc}")
    else:
        for code in codes:
            if owned.get(code) != products.get(code):
                problems.append(
                    f"{code}: the plug-in layer derives the product id "
                    f"{owned.get(code)!r}, this tool derives {products.get(code)!r}"
                )

    try:
        index = identity.param_index()
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        problems.append(f"the plug-in layer could not build the registry index: {exc}")
    else:
        for module in modules:
            mine = {param.identifier: param.name for param in module.params}
            theirs = index.names_for(module.code)
            if mine != theirs:
                problems.append(
                    f"{module.code}: the parameter ids of the plug-in layer "
                    f"({theirs}) differ from the ones this tool derived ({mine}): "
                    "the generated registry and the canonical table are out of "
                    "step (run tools/codegen_registry.py)"
                )

    return problems


# ---------------------------------------------------------------------------
# the labels
# ---------------------------------------------------------------------------
def base_label(name: str) -> str:
    """Return the label that is drawn on a control.

    The ladder is fixed and deterministic:

    1. the full name, when it fits the budget;
    2. the name without its trailing word, when that fits;
    3. the abbreviation of the name, when the table has one;
    4. the current candidate, truncated to the budget and marked with an
       ellipsis.

    The first step that produces a label within the budget wins.
    """
    if len(name) <= LABEL_BUDGET:
        return name

    candidate = name
    words = name.split()
    if len(words) > 1:
        trimmed = " ".join(words[:-1])
        if len(trimmed) <= LABEL_BUDGET:
            return trimmed
        candidate = trimmed

    abbreviation = ABBREVIATIONS.get(name)
    if abbreviation is not None and len(abbreviation) <= LABEL_BUDGET:
        return abbreviation

    return candidate[: LABEL_BUDGET - 1] + ELLIPSIS


def truncation_ladder(label: str) -> tuple[tuple[int, str], ...]:
    """Return the published ladder of a label.

    The ladder is a straight prefix cut of the label, published in
    ascending size order: the host takes the entry that fits the space it
    has.  The rule is verified against a golden fixture by
    ``tests/test_truncation_ladder.py``.
    """
    return tuple((size, label[:size]) for size in range(1, len(label)))


# ---------------------------------------------------------------------------
# the layout
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Control:
    """One bound control of a page."""

    slot: str
    parameter: int
    type: str
    name: str
    label: str
    group: str
    minimum: float | None
    maximum: float | None
    minimum_text: str
    maximum_text: str
    default: Any
    default_text: str
    unit: str
    choices: tuple[str, ...]
    ladder: tuple[tuple[int, str], ...]
    help: str = ""


@dataclass(frozen=True)
class Page:
    """One page of controls."""

    index: int
    controls: tuple[Control, ...]


@dataclass(frozen=True)
class ModulePackage:
    """The package of one module: its identifiers and its pages."""

    code: str
    display_name: str
    category: str
    product_id: str
    effect_ids: tuple[tuple[str, str, str], ...]
    pages: tuple[Page, ...]
    param_count: int
    discrete_count: int
    params: tuple[Param, ...] = ()

    @property
    def knob_count(self) -> int:
        """The number of continuous controls of the module."""
        return self.param_count - self.discrete_count

    @property
    def page_count(self) -> int:
        """The number of pages of the module."""
        return len(self.pages)


def _default_value(param: Param) -> Any:
    """Return the default of a parameter as a JSON-friendly value."""
    if param.type == "bool":
        return param.default_text.strip().lower() in ("1", "true", "yes")
    if param.type == "enum":
        return param.default_text
    if param.type == "int":
        return int(float(param.default_text))
    return float(param.default_text)


def _default_text(param: Param) -> str:
    """Return the default of a parameter as the text the package prints."""
    if param.type == "bool":
        return "1" if _default_value(param) else "0"
    if param.type == "enum":
        return param.default_text
    if param.type == "int":
        return str(int(float(param.default_text)))
    return repr(float(param.default_text))


def layout_order(params: Sequence[Param]) -> list[Param]:
    """Return the layout order of a module's parameters.

    The parameters are grouped by their ``group`` column, the groups are
    ordered by first appearance in the canonical table, and the table
    order is kept inside a group.
    """
    order: list[str] = []
    groups: dict[str, list[Param]] = {}
    for param in params:
        if param.group not in groups:
            groups[param.group] = []
            order.append(param.group)
        groups[param.group].append(param)
    return [param for group in order for param in groups[group]]


def layout_pages(params: Sequence[Param]) -> tuple[Page, ...]:
    """Lay a module's parameters out on pages.

    A page holds one switch slot and three knob slots.  A discrete
    control takes the switch slot, a continuous control takes the next
    free knob slot, and a control that does not fit starts a new page.
    The controls of a page are written in slot order: the switch first,
    then the knobs.
    """
    pages: list[Page] = []
    current: list[tuple[Param, str]] = []
    switch_used = False
    knobs_used = 0

    def flush() -> None:
        nonlocal current, switch_used, knobs_used
        if current:
            pages.append(
                Page(
                    index=len(pages) + 1,
                    controls=tuple(
                        _make_control(param, slot) for param, slot in sorted(current, key=_slot_key)
                    ),
                )
            )
        current = []
        switch_used = False
        knobs_used = 0

    for param in layout_order(params):
        if param.is_discrete:
            if switch_used:
                flush()
            current.append((param, "switch"))
            switch_used = True
            continue
        if knobs_used >= len(KNOB_SLOTS):
            flush()
        current.append((param, KNOB_SLOTS[knobs_used]))
        knobs_used += 1

    flush()
    return tuple(pages)


def _slot_key(item: tuple[Param, str]) -> int:
    """Return the position of a slot in the written order of a page."""
    return SLOT_ORDER.index(item[1])


def _make_control(param: Param, slot: str) -> Control:
    """Build the control of one parameter."""
    label = base_label(param.name)
    discrete = param.is_discrete
    return Control(
        slot=slot,
        parameter=param.identifier,
        type=param.type,
        name=param.name,
        label=label,
        group=param.group,
        minimum=None if discrete else param.minimum,
        maximum=None if discrete else param.maximum,
        minimum_text="" if discrete else param.minimum_text,
        maximum_text="" if discrete else param.maximum_text,
        default=_default_value(param),
        default_text=_default_text(param),
        unit="" if discrete else param.unit,
        choices=param.choices if param.type == "enum" else (),
        ladder=truncation_ladder(label),
        help=param.help,
    )


# ---------------------------------------------------------------------------
# the package
# ---------------------------------------------------------------------------
@dataclass
class Package:
    """The whole application package."""

    modules: list[ModulePackage] = field(default_factory=list)
    source_sha256: str = ""
    param_count: int = 0
    discrete_count: int = 0
    page_count: int = 0
    effect_id_count: int = 0


def build_package(root: Path = ROOT) -> Package:
    """Build the application package from the canonical parameter table."""
    tsv = root / TSV_REL
    if not tsv.is_file():
        raise PackageError(f"the canonical parameter table is missing: {tsv}")

    modules = assign_parameter_ids(load_canonical(tsv))
    codes = [module.code for module in modules]
    products = mirror_product_codes(codes)

    problems = cross_check(modules, products)
    if problems:
        raise PackageError(
            "the derived identifiers disagree with the plug-in layer:\n  "
            + "\n  ".join(problems)
        )

    for name, abbreviation in sorted(ABBREVIATIONS.items()):
        if len(abbreviation) > LABEL_BUDGET:
            raise PackageError(
                f"the abbreviation of {name!r} is {len(abbreviation)} characters long; "
                f"the budget is {LABEL_BUDGET}"
            )

    package = Package(source_sha256=hashlib.sha256(tsv.read_bytes()).hexdigest())

    for module in modules:
        product = products[module.code]
        pages = layout_pages(module.params)
        discrete = sum(1 for param in module.params if param.is_discrete)
        package.modules.append(
            ModulePackage(
                code=module.code,
                display_name=module.display_name,
                category=module.category,
                product_id=product,
                effect_ids=tuple((MANUFACTURER_ID, product, plug) for plug in PLUG_IDS),
                pages=pages,
                param_count=len(module.params),
                discrete_count=discrete,
                params=tuple(module.params),
            )
        )

    package.param_count = sum(m.param_count for m in package.modules)
    package.discrete_count = sum(m.discrete_count for m in package.modules)
    package.page_count = sum(m.page_count for m in package.modules)
    package.effect_id_count = sum(len(m.effect_ids) for m in package.modules)
    return package


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _escape_cell(text: str) -> str:
    """Escape the characters that would break a markdown table cell."""
    return text.replace("|", "\\|")


def _range_text(control: Control) -> str:
    """Return the range of a control as the package prints it."""
    if control.type == "enum":
        return f"{len(control.choices)} choices"
    if control.type == "bool":
        return "off / on"
    return f"{control.minimum_text} .. {control.maximum_text}"


def _default_cell(control: Control) -> str:
    """Return the default of a control as the package prints it."""
    if control.type == "bool":
        return "on" if control.default else "off"
    if control.type == "enum":
        return str(control.default)
    if control.type == "int":
        return str(control.default)
    return repr(float(control.default))


def render_markdown(package: Package) -> str:
    """Render the human readable half of the package."""
    lines: list[str] = []
    lines.append("# AAX page tables — the generated application package")
    lines.append("")
    lines.append("<!--")
    lines.append("     GENERATED FILE, DO NOT EDIT.")
    lines.append("     Generator:       scripts/aax_tables.py")
    lines.append("     Source of truth: aurcore/schema/params.tsv")
    lines.append(f"     Source sha256:   {package.source_sha256}")
    lines.append("     Regenerate with: python scripts/aax_tables.py")
    lines.append("     Verify with:     python scripts/aax_tables.py --check")
    lines.append("-->")
    lines.append("")
    lines.append(
        "This is the **application package** of the AAX plug-in family: the data that "
        "describes the plug-in to the host — the effect identifiers, the parameter "
        "identifiers, the page layout, the labels, the ranges, the defaults and the "
        "category."
    )
    lines.append("")
    lines.append(
        "It is **generated** from the canonical parameter table "
        "(`aurcore/schema/params.tsv`) and it is **complete**: every module of the "
        "registry has an effect id, every parameter of every module has an identifier "
        "and a place on exactly one page."
    )
    lines.append("")
    lines.append(
        "It is **not** the format's page table. The format's page table is a "
        "resource document in the plug-in bundle, in the format's own schema; "
        "this package is the part that does not need the SDK, and the generator "
        "writes that document as well, into `plugins/aax/resources/`, which the "
        "build stages into the bundle. See `plugins/aax/README.md` §6."
    )
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append("| Item | Count |")
    lines.append("|---|---:|")
    lines.append(f"| Modules | {len(package.modules)} |")
    lines.append(f"| Pages | {package.page_count} |")
    lines.append(f"| Parameters | {package.param_count} |")
    lines.append(f"| Switches (discrete controls) | {package.discrete_count} |")
    lines.append(f"| Knobs (continuous controls) | {package.param_count - package.discrete_count} "
                 f"|")
    lines.append(f"| Effect identifiers | {package.effect_id_count} |")
    lines.append("")
    lines.append("## The rules the package encodes")
    lines.append("")
    lines.append("| Rule | Value |")
    lines.append("|---|---|")
    lines.append(f"| Manufacturer id | `{MANUFACTURER_ID}` |")
    lines.append(f"| Plug ids (channel configurations) | {', '.join(f'`{p}`' for p in PLUG_IDS)} |")
    lines.append(f"| Parameter id base | `{PARAM_ID_BASE}` (added by the SDK-facing step) |")
    lines.append(f"| Label budget | {LABEL_BUDGET} characters |")
    lines.append(f"| Page geometry | one `{SLOT_ORDER[0]}` slot and {len(KNOB_SLOTS)} knob slots |")
    lines.append("")
    lines.append(
        "A parameter's identifier is the one the generated registry publishes — the "
        "position of the parameter inside its own module.  It is the number the VST3 "
        "adapter carries in the identity record, so a session written by one format "
        "means the same thing in another.  A plug-in implements exactly one module, "
        "so the identifiers are unique within the plug-in, and adding or removing a "
        "module never renumbers another one."
    )
    lines.append("")
    lines.append("## Modules")
    lines.append("")

    for number, module in enumerate(package.modules):
        lines.append(f"### {number}. {module.display_name} — `{module.code}`")
        lines.append("")
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| Category | {module.category} |")
        lines.append(f"| Product id | `{module.product_id}` |")
        ids = " · ".join(f"`{':'.join(triple)}`" for triple in module.effect_ids)
        lines.append(f"| Effect ids | {ids} |")
        lines.append(
            f"| Parameters | {module.param_count} "
            f"({module.discrete_count} switches, {module.knob_count} knobs) |"
        )
        lines.append(f"| Pages | {module.page_count} |")
        lines.append("")
        for page in module.pages:
            lines.append(f"**Page {page.index}**")
            lines.append("")
            lines.append("| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for control in page.controls:
                choices = ", ".join(control.choices)
                lines.append(
                    "| {slot} | {pid} | {ptype} | {name} | {label} | {range} | "
                    "{default} | {unit} | {choices} |".format(
                        slot=control.slot,
                        pid=control.parameter,
                        ptype=control.type,
                        name=_escape_cell(control.name),
                        label=_escape_cell(control.label),
                        range=_escape_cell(_range_text(control)),
                        default=_escape_cell(_default_cell(control)),
                        unit=_escape_cell(control.unit),
                        choices=_escape_cell(choices),
                    )
                )
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_xml(package: Package, module_code: str, plug: str = "MONO") -> str:
    """Render the page table of one module in the documented XML schema.

    The schema is ours (see ``docs/AAX_APPLICATION.md`` §3.2); the
    SDK-facing step translates it into the format's own page table.  The
    rendered document is the worked example of the application document,
    and the test compares the two, so the document cannot drift away from
    the generator.
    """
    module = next((m for m in package.modules if m.code == module_code), None)
    if module is None:
        raise PackageError(f"unknown module code {module_code!r}")

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<!-- GENERATED FILE, DO NOT EDIT.")
    lines.append("     Generator:       scripts/aax_tables.py")
    lines.append("     Source of truth: aurcore/schema/params.tsv")
    lines.append("-->")
    lines.append(
        '<PageTable module="{module}" name="{name}" category="{category}" '
        'manufacturer="{manufacturer}" product="{product}" plug="{plug}" '
        'paramIdBase="{base}">'.format(
            module=module.code,
            name=module.display_name,
            category=module.category,
            manufacturer=MANUFACTURER_ID,
            product=module.product_id,
            plug=plug,
            base=PARAM_ID_BASE,
        )
    )
    for page in module.pages:
        lines.append(f'  <Page index="{page.index}">')
        for control in page.controls:
            head = (
                '    <Control slot="{slot}" parameter="{pid}" type="{ptype}" '
                'name="{name}" label="{label}"'.format(
                    slot=control.slot,
                    pid=control.parameter,
                    ptype=control.type,
                    name=control.name,
                    label=control.label,
                )
            )
            if control.type == "enum":
                lines.append(head + ">")
                for choice in control.choices:
                    lines.append(f"      <Choice>{choice}</Choice>")
                lines.append("    </Control>")
            elif control.type == "bool":
                lines.append(head + f' default="{control.default_text}"/>')
            else:
                lines.append(
                    head
                    + ' min="{min}" max="{max}" default="{default}" unit="{unit}"/>'.format(
                        min=control.minimum_text,
                        max=control.maximum_text,
                        default=control.default_text,
                        unit=control.unit,
                    )
                )
        lines.append("  </Page>")
    lines.append("</PageTable>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# the page table document of the format
# ---------------------------------------------------------------------------
def _xml_escape(text: str) -> str:
    """Return the text with the characters XML reserves escaped."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _name_variations(label: str) -> list[tuple[int, str]]:
    """Return the short names of a label, and the size each one is for.

    The ladder of the label is a straight prefix cut, published for the
    sizes a surface asks for, and the whole label is published as well: a
    host that has room for it shows the name in full.
    """
    variations = [(size, text) for size, text in truncation_ladder(label) if size in NAME_SIZES]

    if not any(size == len(label) for size, _ in variations):
        variations.append((len(label), label))

    return sorted(variations)


def _chunks(controls: Sequence[Any], size: int) -> list[list[Any]]:
    """Split the controls into pages of at most ``size`` controls."""
    return [list(controls[i:i + size]) for i in range(0, len(controls), size)]


def render_page_table(package: Package, module_code: str) -> str:
    """Render the page table document of one module in the format's schema.

    The format expects the page table as a **resource document inside the
    plug-in bundle**: the plug-in declares the file to the host with
    ``AAX_eResourceType_PageTable`` (the name of the file) and the host
    reads it out of ``Contents/Resources`` of the bundle.

    The document is one ``PageTables`` element.  It holds one ``Plugin``
    element per channel configuration of the module - the triad of the
    manufacturer, the product and the plug id the host looks the plug-in up
    by - one ``PTLayout`` per layout, and one ``PageTable`` element per
    page table type.  A control is bound by its parameter id, and the short
    names of the controls are in ``ControlNamesVariations``.
    """
    module = next((m for m in package.modules if m.code == module_code), None)
    if module is None:
        raise PackageError(f"unknown module code {module_code!r}")

    # The controls of the plug-in: the bypass switch and the controls of
    # the module.
    #
    # The bypass switch is the plug-in's own control - it carries the id
    # every format of the family uses - and the format's own examples list
    # it in the page table.  The host warns about an automatable parameter
    # that is not in the table, so it is listed.
    controls: list[tuple[str, str]] = [(BYPASS_PARAM_ID, BYPASS_LABEL)]

    for page in module.pages:
        for control in page.controls:
            controls.append((str(PARAM_ID_BASE + control.parameter), control.label))

    lines: list[str] = []
    lines.append("<?xml version='1.0' encoding='UTF-8' standalone='yes'?>")
    lines.append("<!-- GENERATED FILE, DO NOT EDIT.")
    lines.append("     Generator:       scripts/aax_tables.py")
    lines.append("     Source of truth: aurcore/schema/params.tsv")
    lines.append("")
    lines.append("     This document is a resource of the plug-in: the build copies")
    lines.append("     it into Contents/Resources of the bundle, and the plug-in")
    lines.append("     declares it to the host with AAX_eResourceType_PageTable.")
    lines.append("-->")
    lines.append(f"<PageTables vers='{PAGE_TABLE_VERS}'>")
    lines.append("\t<PageTableLayouts>")

    for triple, plug in zip(module.effect_ids, PLUG_IDS):
        lines.append(
            "\t\t<Plugin manID='{man}' prodID='{prod}' plugID='{plug}'>".format(
                man=triple[0], prod=triple[1], plug=plug
            )
        )
        lines.append(f"\t\t\t<Desc>{_xml_escape(module.display_name)}</Desc>")
        lines.append(f"\t\t\t<Layout>{PAGE_TABLE_LAYOUT}</Layout>")
        lines.append("\t\t</Plugin>")

    lines.append(f"\t\t<PTLayout name='{PAGE_TABLE_LAYOUT}'>")

    for size in PAGE_TABLE_SIZES:
        lines.append(f"\t\t\t<PageTable type='{PAGE_TABLE_TYPE}' pgsz='{size}'>")

        # The bypass switch is alone on the first page and the controls of
        # the module follow it, the way the format's own examples write it.
        groups = [[controls[0]]] + _chunks(controls[1:], size)

        for number, chunk in enumerate(groups, start=1):
            lines.append(f"\t\t\t\t<Page num='{number}'>")
            for identifier, _ in chunk:
                lines.append(f"\t\t\t\t\t<ID>{identifier}</ID>")
            lines.append("\t\t\t\t</Page>")
        lines.append("\t\t\t</PageTable>")

    lines.append("\t\t</PTLayout>")
    lines.append("\t</PageTableLayouts>")
    lines.append("\t<ControlNamesVariations>")

    for identifier, label in controls:
        lines.append(f"\t\t<Ctrl ID='{identifier}'>")
        for size, text in _name_variations(label):
            lines.append(
                f"\t\t\t<name typ='{PAGE_TABLE_TYPE}' sz='{size}'>{_xml_escape(text)}</name>"
            )
        lines.append("\t\t</Ctrl>")

    lines.append("\t</ControlNamesVariations>")
    lines.append("</PageTables>")
    return "\n".join(lines) + "\n"


def render_input_table(package: Package, module_code: str) -> str:
    """Render the canonical rows of one module as the document quotes them.

    The application document shows the input of its worked example next to
    the output; this is that input, taken from the canonical table.
    """
    module = next((m for m in package.modules if m.code == module_code), None)
    if module is None:
        raise PackageError(f"unknown module code {module_code!r}")

    rows = ["| Parameter id | Name | Type | Range | Default | Group |",
            "|---:|---|---|---|---|---|"]
    for param in module.params:
        if param.type == "enum":
            span = f"{len(param.choices)} choices"
        elif param.type == "bool":
            span = "off / on"
        else:
            span = f"{param.minimum_text} .. {param.maximum_text}"
            if param.unit:
                span = f"{span} {param.unit}"
        rows.append(
            f"| {param.identifier} | {param.name} | {param.type} | {span} | "
            f"`{_default_text(param)}` | {param.group} |"
        )
    return "\n".join(rows)


def sync_document(text: str, package: Package) -> str:
    """Refresh the worked example inside the application document.

    Two blocks are replaced: the quotation of the canonical rows and the
    page table the generator produces.  Nothing else in the document is
    touched.
    """
    table = render_input_table(package, WORKED_EXAMPLE_MODULE)
    xml = render_xml(package, WORKED_EXAMPLE_MODULE).rstrip("\n")

    text, count = re.subn(
        r"(The input \(the canonical table, abbreviated\):\n\n)"
        r"\|.*?\|\n\|.*?\|\n.*?"
        r"(\n\nThe output \(the generator's expected page table\):\n\n```xml\n).*?(\n```\n)",
        lambda m: m.group(1) + table + m.group(2) + xml + m.group(3),
        text,
        flags=re.S,
    )
    if count != 1:
        raise PackageError(
            f"{DOC_REL}: the worked example could not be located ({count} matches)"
        )
    return text


def render_json(package: Package) -> str:
    """Render the machine readable half of the package."""
    payload: dict[str, Any] = {
        "_generated": (
            "GENERATED FILE, DO NOT EDIT - produced by scripts/aax_tables.py"
        ),
        "generator": "scripts/aax_tables.py",
        "source_of_truth": TSV_REL,
        "source_sha256": package.source_sha256,
        "schema_version": 1,
        "manufacturer_id": MANUFACTURER_ID,
        "plug_ids": list(PLUG_IDS),
        "param_id_base": PARAM_ID_BASE,
        "label_budget": LABEL_BUDGET,
        "slots": list(SLOT_ORDER),
        "page_table": {
            "resource": PAGE_TABLE_RESOURCE,
            "directory": RESOURCES_REL,
            "layout": PAGE_TABLE_LAYOUT,
            "type": PAGE_TABLE_TYPE,
            "sizes": list(PAGE_TABLE_SIZES),
        },
        "totals": {
            "modules": len(package.modules),
            "pages": package.page_count,
            "parameters": package.param_count,
            "switches": package.discrete_count,
            "knobs": package.param_count - package.discrete_count,
            "effect_ids": package.effect_id_count,
        },
        "modules": [
            {
                "code": module.code,
                "display_name": module.display_name,
                "category": module.category,
                "product_id": module.product_id,
                "effect_ids": [
                    {"plug": plug, "id": ":".join(triple)}
                    for triple, plug in zip(module.effect_ids, PLUG_IDS)
                ],
                "param_count": module.param_count,
                "switch_count": module.discrete_count,
                "knob_count": module.knob_count,
                "page_count": module.page_count,
                "bypass": {
                    "id": BYPASS_PARAM_ID,
                    "label": BYPASS_LABEL,
                    "help": BYPASS_HELP,
                },
                "pages": [
                    {
                        "index": page.index,
                        "controls": [
                            {
                                "slot": control.slot,
                                "parameter": control.parameter,
                                "type": control.type,
                                "name": control.name,
                                "label": control.label,
                                "minimum": control.minimum,
                                "maximum": control.maximum,
                                "default": control.default,
                                "unit": control.unit,
                                "choices": list(control.choices),
                                "help": control.help,
                                "ladder": [[size, text] for size, text in control.ladder],
                            }
                            for control in page.controls
                        ],
                    }
                    for page in module.pages
                ],
            }
            for module in package.modules
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_all(package: Package) -> dict[str, str]:
    """Return every artefact of the package, keyed by its relative path."""
    rendered: dict[str, str] = {
        MARKDOWN_REL: render_markdown(package),
        JSON_REL: render_json(package),
    }

    # The page table document of every module: the resource the plug-in
    # declares to the host, in the format's own schema.
    for module in package.modules:
        rendered[f"{RESOURCES_REL}/{module.code}.xml"] = render_page_table(package, module.code)

    return rendered


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------
def _write(path: Path, text: str) -> str:
    """Write an artefact and return its SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check(root: Path, rendered: dict[str, str]) -> list[str]:
    """Compare the committed artefacts with a fresh run of the generator."""
    problems: list[str] = []
    for rel, text in rendered.items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        committed = path.read_text(encoding="utf-8")
        if committed != text:
            problems.append(f"{rel}: drifted from a fresh run of the generator")

    # A page table document of a module that is gone would still be copied
    # into a bundle, so a stale one is a finding.
    resources = root / RESOURCES_REL
    if resources.is_dir():
        expected = {Path(rel).name for rel in rendered if rel.startswith(f"{RESOURCES_REL}/")}
        for path in sorted(resources.glob("*.xml")):
            if path.name not in expected:
                problems.append(f"{RESOURCES_REL}/{path.name}: stale, its module is gone")

    return problems


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="aax_tables",
        description=(
            "Generate the AAX application package — the effect identifiers, the "
            "parameter identifiers, the page tables, the labels, the ranges, the "
            "defaults and the category — from the canonical parameter table."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root that holds the canonical table",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="write the package into this directory instead of the repository",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write anything; fail when the committed artefacts drifted",
    )
    parser.add_argument(
        "--sync-doc",
        action="store_true",
        help=(
            "refresh the worked example of the application document so that it "
            "cannot drift away from the generator"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the generator and report the result."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    try:
        package = build_package(root)
    except PackageError as exc:
        print(f"aax_tables: {exc}", file=sys.stderr)
        return 2

    rendered = render_all(package)

    if args.sync_doc:
        doc_path = root / DOC_REL
        try:
            current = doc_path.read_text(encoding="utf-8")
            updated = sync_document(current, package)
        except OSError as exc:
            print(f"aax_tables: cannot read {DOC_REL}: {exc}", file=sys.stderr)
            return 2
        except PackageError as exc:
            print(f"aax_tables: {exc}", file=sys.stderr)
            return 2
        if updated != current:
            doc_path.write_text(updated, encoding="utf-8", newline="")
            print(f"{DOC_REL}  worked example refreshed")
        else:
            print(f"{DOC_REL}  worked example already up to date")

    if args.check:
        problems = check(root, rendered)
        for problem in problems:
            print(f"aax_tables: {problem}", file=sys.stderr)
        if problems:
            print("aax_tables: the package drifted; run: python scripts/aax_tables.py")
            return 1
        for rel, text in rendered.items():
            if rel.startswith(f"{RESOURCES_REL}/"):
                continue
            print(f"{rel}  {hashlib.sha256(text.encode('utf-8')).hexdigest()}")
        print(
            f"{RESOURCES_REL}  {len(package.modules)} page table document(s), "
            f"one per module"
        )
        print(
            f"aax_tables: the package is up to date "
            f"({len(package.modules)} modules, {package.page_count} pages, "
            f"{package.param_count} parameters)"
        )
        return 0

    base = Path(args.out_dir).resolve() if args.out_dir else root
    for rel, text in rendered.items():
        digest = _write(base / rel, text)
        if rel.startswith(f"{RESOURCES_REL}/"):
            continue
        print(f"{rel}  {digest}")
    print(
        f"{RESOURCES_REL}  {len(package.modules)} page table document(s), "
        f"one per module"
    )
    print(
        f"aax_tables: {len(package.modules)} modules, {package.page_count} pages, "
        f"{package.param_count} parameters, {package.effect_id_count} effect ids"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
