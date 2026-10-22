"""
tests/test_param_table.py — the canonical parameter table is CHECKED, not trusted.

``aurcore/schema/params.tsv`` is the single source of truth of Auralis: every
module, the user interface, the preset writer, the plug-in adapters and the
generated registry are written against it.  The table is 253 parameters wide
and nobody looks at it row by row, so this file does it mechanically.

What is checked
---------------
1.  **The raw file.**  The table is parsed here, not through the loader, so a
    row with a missing column, a value in the wrong column or a broken range
    cannot hide behind a forgiving parser.
2.  **The module.**  Every parameter is compared with the module that
    implements it: the same name, the same type, the same unit, the same
    range, the same default, the same law of the travel, the same sentence of
    help and — for an enum — the same choice strings in the same order.  The
    module is asked, not read: the instance is created and its ``describe()``
    and its parameter store are inspected.
3.  **The step count.**  The plug-in publishes a step count with every
    parameter.  It must be 0 for a continuous parameter and the number of
    steps for a stepped one, so the number of values the host can reach
    matches the range in the table.
4.  **The units.**  A unit that the module does not use is a bug the user
    sees on the knob, so the unit is cross-checked with the help text the
    module publishes for the parameter.
5.  **The ids.**  The ids the plug-in publishes are the positions of the
    parameters inside their module; a parameter that is renamed keeps its id,
    and a parameter that is removed must leave a hole instead of renumbering
    the rest.
6.  **The travel.**  A frequency control moves in octaves and a gain control
    in decibels, so the resolution of the travel is measured: how many
    octaves one percent of the travel moves a frequency, and how many
    decibels it moves a gain.
7.  **The names.**  One word names one idea, and a parameter that was renamed
    keeps its old name, so a document that names it still loads.

The file is deliberately free of third party names (CONTRACT §0).
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from aurcore import registry  # noqa: E402
from aurcore.params import ParamDesc  # noqa: E402
from aurcore.schema import ModuleSpec, load  # noqa: E402

TSV_PATH = ROOT / "aurcore" / "schema" / "params.tsv"
MODULES_DIR = ROOT / "aurcore" / "modules"

#: The units the table may use.  A frequency is always in Hz, a level in dB
#: (dBFS when it is measured against full scale, dBTP for a true peak), a
#: time in ms or s, a pitch in semitones, a loudness in LUFS or LU.
KNOWN_UNITS = frozenset(
    {"", "dB", "dBFS", "dBTP", "Hz", "ms", "s", "%", "st", "LU", "LUFS", "deg", "Q"}
)

#: The parameter types the table may use.
KNOWN_TYPES = frozenset({"float", "int", "bool", "enum"})

#: Parameters whose value in the table is ahead of the module that implements
#: them: another agent asked for the change in ``var/patches/`` and the table
#: carries it, but the module still declares the old value.  Each entry names
#: the value the table is expected to carry, so a table that drifts away from
#: the request is caught as well.  The list is empty when every module
#: matches the table.
PENDING_MODULE_SYNC: dict[tuple[str, str], object] = {}

#: The parameters whose help text mentions a unit that is not the unit of the
#: parameter.  The help of these parameters talks about something else (the
#: band a strength belongs to, the length of a fade), so the cross check
#: below cannot be applied to them.
HELP_UNIT_EXCEPTIONS = frozenset(
    {
        ("AUR_DEREVERB", "Band Reduction Low"),
        ("AUR_DEREVERB", "Band Reduction Low-Mid"),
        ("AUR_DEREVERB", "Band Reduction High-Mid"),
        ("AUR_DEREVERB", "Band Reduction High"),
        ("AUR_MUSICREBALANCE", "Bass Gain"),
    }
)

#: The controls that keep a straight line on purpose.
#:
#: A **threshold** is judged against the signal, not against unity, so its
#: law is a straight line in decibels.  A **makeup gain** puts back what the
#: module took away: its range runs from -12 to +24 dB, so two thirds of it
#: is above unity, and the fader shape would need a range of -4.8 to +24 dB,
#: which is no longer a makeup gain.
LINEAR_ON_PURPOSE = frozenset(
    {
        ("AUR_DECLIP", "Makeup Gain"),
        ("AUR_DECLIP", "Threshold"),
        ("AUR_DEESS", "Threshold"),
        ("AUR_VOICEDENOISE", "Threshold"),
        ("AUR_SPECTRALDENOISE", "Noise Threshold"),
        ("AUR_SPECTRALDENOISE", "Tonal Threshold"),
        ("AUR_AMBIENCEMATCH", "Ambience Threshold"),
        ("AUR_LOUDNESSOPT", "Threshold"),
        ("AUR_DEHUM", "Harmonic Slope"),
        ("AUR_AZIMUTH", "Gain"),
    }
)

#: The gain controls the finding re-ranges: they carry the taper law, and
#: the range they get puts unity in the middle of their travel.
TAPERED_GAINS = frozenset(
    {
        ("AUR_GAIN", "Gain"),
        ("AUR_SPECTRALRECOVERY", "Low Gain"),
        ("AUR_SPECTRALRECOVERY", "High Gain"),
        ("AUR_MUSICREBALANCE", "Vocal Gain"),
        ("AUR_MUSICREBALANCE", "Bass Gain"),
        ("AUR_MUSICREBALANCE", "Drums Gain"),
        ("AUR_MUSICREBALANCE", "Other Gain"),
        ("AUR_DIALOGUEISOLATE", "Voice Gain"),
        ("AUR_DIALOGUEISOLATE", "Reverb Gain"),
        ("AUR_DIALOGUEISOLATE", "Noise Gain"),
        ("AUR_AMBIENCEMATCH", "Gain"),
        ("AUR_EQ", "Band 1 Gain"),
        ("AUR_EQ", "Band 2 Gain"),
        ("AUR_EQ", "Band 3 Gain"),
        ("AUR_EQ", "Band 4 Gain"),
        ("AUR_EQ", "Output Gain"),
        ("AUR_DECONSTRUCT", "Tonal Gain"),
        ("AUR_DECONSTRUCT", "Noisy Gain"),
        ("AUR_DECONSTRUCT", "Transient Gain"),
    }
)


# ---------------------------------------------------------------------------
# the raw table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Row:
    """One raw row of the canonical table."""

    lineno: int
    ncols: int
    code: str
    display_name: str
    category: str
    edition: str
    element: str
    param: str
    type: str
    default: str
    minimum: str
    maximum: str
    curve: str
    exponent: str
    unit: str
    choices: str
    group: str
    help: str
    aliases: str

    @property
    def choice_list(self) -> list[str]:
        """The choice strings of an enum row."""
        return [c for c in self.choices.split("|") if c]

    @property
    def alias_list(self) -> list[str]:
        """The names the parameter had before it was renamed."""
        return [a for a in self.aliases.split("|") if a]


def read_rows() -> list[Row]:
    """Parse the canonical table, strictly: 17 columns or nothing."""
    rows: list[Row] = []
    text = TSV_PATH.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 17:
            # A short row is kept, with its column count, so that the test
            # can report it.  The loader would repair it silently.
            rows.append(Row(lineno, len(parts), *parts, *[""] * (17 - len(parts))))
            continue
        rows.append(Row(lineno, len(parts), *parts))
    return rows


ROWS = read_rows()
DATA_ROWS = [row for row in ROWS if row.code.startswith("AUR_")]
SPECS = load(str(TSV_PATH))
CODES = sorted(SPECS)


def _module_instance(code: str):
    """Create the module that implements ``code`` (or skip the test)."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered")
    try:
        return registry.create(code, 48000, 2)
    except Exception as exc:  # noqa: BLE001 - a module may need an asset
        pytest.skip(f"module {code!r} cannot be created here: {exc}")


def _live_descs(code: str) -> dict[str, ParamDesc]:
    """Return the parameter descriptions of the module that implements ``code``."""
    instance = _module_instance(code)
    return {desc.name: desc for desc in instance.describe()}


def _source_of(code: str) -> str:
    """Return the source of the module and of the modules it imports from."""
    instance = _module_instance(code)
    path = ROOT / (instance.__module__.replace(".", "/") + ".py")
    seen: set[Path] = set()
    pending = [path]
    text: list[str] = []
    while pending:
        current = pending.pop()
        if current in seen or not current.is_file():
            continue
        seen.add(current)
        body = current.read_text(encoding="utf-8")
        text.append(body)
        for match in re.finditer(r"^from\s+\.(\w+)\s+import", body, re.MULTILINE):
            pending.append(current.parent / f"{match.group(1)}.py")
    return "\n".join(text)


# ---------------------------------------------------------------------------
# 1. the raw file
# ---------------------------------------------------------------------------
def test_every_data_row_has_seventeen_columns():
    """Every data row of the table carries the 17 canonical columns."""
    short = [
        f"line {row.lineno} has {row.ncols} columns"
        for row in DATA_ROWS
        if row.ncols != 17
    ]
    assert not short, "\n".join(short)


def test_the_table_has_no_empty_cell():
    """No cell of a data row is empty except the two optional ones."""
    problems = []
    for row in DATA_ROWS:
        for name in ("code", "display_name", "category", "edition", "element", "param", "type"):
            if not getattr(row, name):
                problems.append(f"line {row.lineno}: {name} is empty")
        if not row.group:
            problems.append(f"line {row.lineno}: the group is empty")
        if not row.type:
            problems.append(f"line {row.lineno}: the type is empty")
    assert not problems, "\n".join(problems)


def test_every_parameter_carries_a_help_sentence():
    """Every parameter says in a sentence what it does.

    The panel has nowhere else to learn it: the help of a parameter is the
    only place where the module says in words what its value means.
    """
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} has no help"
        for row in DATA_ROWS
        if not row.help.strip()
    ]
    assert not problems, "\n".join(problems)


def test_the_help_is_a_sentence():
    """The help of a parameter is a sentence, not a word."""
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} says {row.help!r}"
        for row in DATA_ROWS
        if row.help.strip() and not row.help.strip().endswith(".")
    ]
    assert not problems, "\n".join(problems)


def test_the_curve_is_one_of_the_three_laws():
    """Every parameter states the law its travel follows."""
    known = {"linear", "log", "taper"}
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} has the curve {row.curve!r}"
        for row in DATA_ROWS
        if row.curve not in known
    ]
    assert not problems, "\n".join(problems)


def test_a_taper_carries_its_exponent():
    """A tapered parameter carries the exponent of its law.

    The exponent only means something when the curve is a taper, so the
    other rows leave the column empty.
    """
    problems = []
    for row in DATA_ROWS:
        if row.curve == "taper":
            if not row.exponent.strip():
                problems.append(f"line {row.lineno}: {row.code}.{row.param} tapers without an "
                                f"exponent")
            elif float(row.exponent) <= 0.0:
                problems.append(f"line {row.lineno}: {row.code}.{row.param} tapers with "
                                f"{row.exponent}")
        elif row.exponent.strip():
            problems.append(
                f"line {row.lineno}: {row.code}.{row.param} carries the exponent "
                f"{row.exponent} but its curve is {row.curve!r}"
            )
    assert not problems, "\n".join(problems)


def test_a_log_curve_needs_a_range_above_zero():
    """A logarithm of zero is not a number, so the range has to start above it."""
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} is logarithmic but starts at {row.minimum}"
        for row in DATA_ROWS
        if row.curve == "log" and float(row.minimum) <= 0.0
    ]
    assert not problems, "\n".join(problems)


def test_an_alias_is_not_the_name_of_the_parameter():
    """An alias is the name the parameter had before; it is not the name."""
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} is its own alias"
        for row in DATA_ROWS
        for alias in row.alias_list
        if alias == row.param
    ]
    assert not problems, "\n".join(problems)


def test_an_alias_is_not_used_by_two_parameters():
    """An alias names one parameter only, so a document cannot mean two things."""
    seen: dict[tuple[str, str], int] = {}
    problems = []
    for row in DATA_ROWS:
        for alias in row.alias_list:
            key = (row.code, alias)
            if key in seen:
                problems.append(
                    f"line {row.lineno}: {row.code}.{row.param} uses the alias "
                    f"{alias!r}, which line {seen[key]} uses as well"
                )
            seen[key] = row.lineno
    assert not problems, "\n".join(problems)


def test_the_types_are_known():
    """Every row uses one of the four parameter types."""
    unknown = [
        f"line {row.lineno}: {row.type!r}"
        for row in DATA_ROWS
        if row.type not in KNOWN_TYPES
    ]
    assert not unknown, "\n".join(unknown)


def test_the_units_are_known():
    """Every row uses a unit the product knows."""
    unknown = [
        f"line {row.lineno}: {row.unit!r}"
        for row in DATA_ROWS
        if row.unit not in KNOWN_UNITS
    ]
    assert not unknown, "\n".join(unknown)


def test_only_enums_carry_choices():
    """A parameter that is not an enum has no choice strings."""
    problems = [
        f"line {row.lineno}: {row.param} carries {row.choices!r}"
        for row in DATA_ROWS
        if row.type != "enum" and row.choices
    ]
    assert not problems, "\n".join(problems)


def test_every_enum_carries_choices():
    """An enum always has at least one choice."""
    problems = [
        f"line {row.lineno}: {row.param} has no choices"
        for row in DATA_ROWS
        if row.type == "enum" and not row.choice_list
    ]
    assert not problems, "\n".join(problems)


def test_the_enum_range_is_the_choice_index_range():
    """The range of an enum is the 0-based index range of its choices.

    The plug-in turns an enum into a list of positions, so the table has to
    say 0 .. len(choices) - 1.  A 1-based range (1 .. n) or an upper bound
    that is one too high would publish one step too many.
    """
    problems = []
    for row in DATA_ROWS:
        if row.type != "enum":
            continue
        count = len(row.choice_list)
        if (float(row.minimum), float(row.maximum)) != (0.0, float(count - 1)):
            problems.append(
                f"line {row.lineno}: {row.code}.{row.param} says "
                f"[{row.minimum}, {row.maximum}], the {count} choices need "
                f"[0, {count - 1}]"
            )
    assert not problems, "\n".join(problems)


def test_the_enum_default_is_one_of_the_choices():
    """The default of an enum is a choice string, not an index."""
    problems = []
    for row in DATA_ROWS:
        if row.type != "enum":
            continue
        if row.default not in row.choice_list:
            problems.append(
                f"line {row.lineno}: {row.code}.{row.param} defaults to "
                f"{row.default!r}, which is not one of {row.choice_list}"
            )
    assert not problems, "\n".join(problems)


def test_the_default_lies_inside_the_range():
    """Every default is inside its own range."""
    problems = []
    for row in DATA_ROWS:
        if row.type in ("enum", "bool"):
            continue
        low, high, value = float(row.minimum), float(row.maximum), float(row.default)
        if not (low <= value <= high):
            problems.append(
                f"line {row.lineno}: {row.code}.{row.param} defaults to {value} "
                f"outside [{low}, {high}]"
            )
    assert not problems, "\n".join(problems)


def test_the_range_is_ordered():
    """No parameter has a maximum below its minimum."""
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} has "
        f"[{row.minimum}, {row.maximum}]"
        for row in DATA_ROWS
        if row.type != "enum" and float(row.maximum) < float(row.minimum)
    ]
    assert not problems, "\n".join(problems)


def test_a_bool_uses_the_range_zero_to_one():
    """A bool parameter has the range 0 .. 1."""
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} has "
        f"[{row.minimum}, {row.maximum}]"
        for row in DATA_ROWS
        if row.type == "bool"
        and (float(row.minimum), float(row.maximum)) != (0.0, 1.0)
    ]
    assert not problems, "\n".join(problems)


def test_an_int_has_integral_bounds():
    """An int parameter has whole bounds, so its step count is exact."""
    problems = []
    for row in DATA_ROWS:
        if row.type != "int":
            continue
        for name in ("minimum", "maximum", "default"):
            value = float(getattr(row, name))
            if value != int(value):
                problems.append(f"line {row.lineno}: {name} is {value}")
    assert not problems, "\n".join(problems)


def test_a_parameter_name_is_unique_inside_its_module():
    """A module does not list the same parameter twice."""
    seen: dict[tuple[str, str], int] = {}
    problems = []
    for row in DATA_ROWS:
        key = (row.code, row.param)
        if key in seen:
            problems.append(
                f"line {row.lineno}: {row.code}.{row.param} is already on "
                f"line {seen[key]}"
            )
        seen[key] = row.lineno
    assert not problems, "\n".join(problems)


def test_the_table_covers_every_registered_module():
    """The table and the registry know the same modules."""
    registered = sorted(registry.codes())
    assert registered == CODES, (
        "modules only in the table: "
        f"{sorted(set(CODES) - set(registered))}, only in the registry: "
        f"{sorted(set(registered) - set(CODES))}"
    )


def test_the_table_has_a_row_for_every_parameter():
    """The number of rows is the number of parameters of all modules."""
    expected = sum(len(spec.params) for spec in SPECS.values())
    assert len(DATA_ROWS) == expected, (
        f"the table has {len(DATA_ROWS)} rows, the modules have {expected} "
        "parameters"
    )


# ---------------------------------------------------------------------------
# 2. the module is the authority
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", CODES)
def test_the_module_declares_the_parameters_of_the_table(code: str):
    """The module declares exactly the parameters of the table, in order.

    Type, unit, range, default, group and — for an enum — the choice strings
    in their order have to be the ones the table publishes.
    """
    live = _live_descs(code)
    wanted = SPECS[code].params

    assert [d.name for d in wanted] == list(live), (
        f"{code}: the module declares {list(live)}, the table lists "
        f"{[d.name for d in wanted]}"
    )

    problems = []
    for desc in wanted:
        have = live[desc.name]
        if have.type != desc.type:
            problems.append(f"{desc.name}: type {have.type!r} != {desc.type!r}")
        if have.unit != desc.unit:
            problems.append(f"{desc.name}: unit {have.unit!r} != {desc.unit!r}")
        if have.group != desc.group:
            problems.append(f"{desc.name}: group {have.group!r} != {desc.group!r}")
        if tuple(have.choices) != tuple(desc.choices):
            problems.append(
                f"{desc.name}: choices {have.choices} != {desc.choices}"
            )
        if have.curve != desc.curve:
            problems.append(f"{desc.name}: curve {have.curve!r} != {desc.curve!r}")
        if desc.curve == "taper" and not math.isclose(
            float(have.curve_exponent), float(desc.curve_exponent), abs_tol=1e-9
        ):
            problems.append(
                f"{desc.name}: curve exponent {have.curve_exponent} != {desc.curve_exponent}"
            )
        if have.help != desc.help:
            problems.append(f"{desc.name}: help {have.help!r} != {desc.help!r}")
        if tuple(have.aliases) != tuple(desc.aliases):
            problems.append(f"{desc.name}: aliases {have.aliases} != {desc.aliases}")
        if (code, desc.name) in PENDING_MODULE_SYNC:
            # The table is ahead of the module here; the default is checked
            # by test_the_pending_module_sync_is_understood.
            continue
        if have.type == "enum":
            if have.default != desc.default:
                problems.append(
                    f"{desc.name}: default {have.default!r} != {desc.default!r}"
                )
        else:
            if not math.isclose(float(have.minimum), float(desc.minimum), abs_tol=1e-9):
                problems.append(
                    f"{desc.name}: min {have.minimum} != {desc.minimum}"
                )
            if not math.isclose(float(have.maximum), float(desc.maximum), abs_tol=1e-9):
                problems.append(
                    f"{desc.name}: max {have.maximum} != {desc.maximum}"
                )
            if not math.isclose(float(have.default), float(desc.default), abs_tol=1e-9):
                problems.append(
                    f"{desc.name}: default {have.default!r} != {desc.default!r}"
                )
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("code", CODES)
def test_the_module_starts_at_the_default_of_the_table(code: str):
    """The value the module starts with is the default the table publishes."""
    instance = _module_instance(code)
    store = instance.params
    problems = []
    for desc in SPECS[code].params:
        if (code, desc.name) in PENDING_MODULE_SYNC:
            continue
        value = store.get(desc.name)
        if desc.type == "enum":
            if value != desc.default:
                problems.append(f"{desc.name}: the module starts at {value!r}")
        elif not math.isclose(float(value), float(desc.default), abs_tol=1e-9):
            problems.append(
                f"{desc.name}: the module starts at {value!r}, the table says "
                f"{desc.default!r}"
            )
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("code", CODES)
def test_the_range_of_the_table_contains_its_default(code: str):
    """min <= default <= max for every parameter of the module."""
    problems = [
        f"{desc.name}: default {desc.default!r} outside "
        f"[{desc.minimum}, {desc.maximum}]"
        for desc in SPECS[code].params
        if desc.type not in ("enum", "bool")
        and not (float(desc.minimum) <= float(desc.default) <= float(desc.maximum))
    ]
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("code", CODES)
def test_the_step_count_of_every_parameter(code: str):
    """The step count the plug-in publishes matches the range of the table.

    The plug-in computes it from the published metadata (see
    ``plugins/vst3native/AuralisVST3.cpp``, ``getParameterInfo``): a
    continuous parameter has 0 steps, a bool has 1, an enum has one step per
    choice but the first one, and an int has one step per unit of its range.
    The number of values the host can reach is the step count plus one.
    """
    records = _published_records(code)
    problems = []
    for record in records:
        steps = _step_count(record)
        if record.type == "float":
            if steps != 0:
                problems.append(
                    f"{record.name}: a continuous parameter has {steps} steps, "
                    "it must have none"
                )
            continue
        values = _value_count(record)
        if steps != values - 1:
            problems.append(
                f"{record.name}: the plug-in publishes {steps} steps, the range "
                f"[{record.minimum}, {record.maximum}] holds {values} values"
            )
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("code", CODES)
def test_every_choice_is_a_string_the_module_knows(code: str):
    """Every choice of an enum is a string the module itself mentions.

    A choice the module has never heard of would silently fall back to the
    default, so the table may only offer strings that exist in the module
    (or in a module it imports from).
    """
    source = _source_of(code)
    problems = []
    for desc in SPECS[code].params:
        if desc.type != "enum":
            continue
        for choice in desc.choices:
            if choice not in source:
                problems.append(
                    f"{desc.name}: {choice!r} does not appear in the module"
                )
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("code", CODES)
def test_the_unit_agrees_with_the_help_of_the_module(code: str):
    """A unit the help contradicts is a unit the module does not use.

    The help of a parameter is the only place where the module says in words
    what its value means, so a frequency that is written as kHz there, or a
    value that is called a percentage there, has to be published with that
    unit.
    """
    live = _live_descs(code)
    problems = []
    for desc in SPECS[code].params:
        if (code, desc.name) in HELP_UNIT_EXCEPTIONS:
            continue
        help_text = live[desc.name].help
        if not help_text:
            continue
        lowered = help_text.lower()
        if "khz" in lowered:
            if desc.unit != "kHz":
                problems.append(
                    f"{desc.name}: the help talks about kHz, the unit is "
                    f"{desc.unit!r}"
                )
        elif re.search(r"\bhz\b", lowered):
            if desc.unit != "Hz":
                problems.append(
                    f"{desc.name}: the help talks about Hz, the unit is "
                    f"{desc.unit!r}"
                )
        if "%" in help_text or "percent" in lowered:
            if desc.unit != "%":
                problems.append(
                    f"{desc.name}: the help calls the value a percentage, the "
                    f"unit is {desc.unit!r}"
                )
    assert not problems, f"{code}:\n  " + "\n  ".join(problems)


def test_the_pending_module_sync_is_understood():
    """Every entry of the pending list names a parameter that exists."""
    problems = []
    for (code, name), value in PENDING_MODULE_SYNC.items():
        if code not in SPECS:
            problems.append(f"{code} is not in the table")
            continue
        names = [d.name for d in SPECS[code].params]
        if name not in names:
            problems.append(f"{code}.{name} is not in the table")
            continue
        desc = SPECS[code].params[names.index(name)]
        if desc.default != value:
            problems.append(
                f"{code}.{name} holds {desc.default!r}, the pending list says "
                f"{value!r}"
            )
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 3. the published metadata and the ids
# ---------------------------------------------------------------------------
def _published_records(code: str):
    """Return the parameter records the plug-in publishes for ``code``."""
    try:
        from plugins import identity
    except Exception as exc:  # noqa: BLE001 - the plug-in layer is optional here
        pytest.skip(f"the plug-in identity layer is not importable: {exc}")
    return identity.module_param_records(code)


def _step_count(record) -> int:
    """The step count the plug-in publishes for a parameter record."""
    if record.type == "enum":
        return max(0, len(record.choices) - 1)
    if record.type == "bool":
        return 1
    if record.type == "int":
        return int(math.ceil(record.maximum) - math.floor(record.minimum))
    return 0


def _value_count(record) -> int:
    """The number of values a parameter can take."""
    if record.type == "enum":
        return len(record.choices)
    if record.type == "bool":
        return 2
    return int(math.ceil(record.maximum) - math.floor(record.minimum)) + 1


@pytest.mark.parametrize("code", CODES)
def test_the_published_ids_are_the_positions_in_the_table(code: str):
    """The id of a parameter is its position inside its module.

    The plug-in publishes the id of a parameter in every artefact, so the id
    has to be the position of the parameter in the table.  A table that was
    reordered without regenerating the artefacts would move an id, and a
    host that saved a preset would then write its value to the wrong
    parameter.
    """
    records = _published_records(code)
    wanted = [desc.name for desc in SPECS[code].params]
    assert [record.name for record in records] == wanted, (
        f"{code}: the plug-in publishes {[r.name for r in records]}, the table "
        f"lists {wanted}"
    )
    for index, record in enumerate(records):
        assert record.id == index, (
            f"{code}.{record.name}: the published id is {record.id}, the "
            f"position in the table is {index}"
        )


def _id_table(specs: dict[str, ModuleSpec]) -> dict[tuple[str, str], int]:
    """Return the ``(code, name) -> id`` table of a set of specifications."""
    import codegen_registry

    registry_model = codegen_registry.build_registry(specs)
    return codegen_registry.id_table(registry_model)


def test_renaming_a_parameter_keeps_its_id():
    """A parameter that is renamed keeps the id the plug-in publishes.

    The id is the position of the parameter inside its module, so a rename
    cannot move it.  This is what makes a rename safe for the hosts and for
    the presets that are already stored.
    """
    for code in CODES:
        spec = SPECS[code]
        if not spec.params:
            continue
        original = spec.params[0]
        renamed = ParamDesc(
            name="Renamed Parameter",
            type=original.type,
            default=original.default,
            minimum=original.minimum,
            maximum=original.maximum,
            unit=original.unit,
            choices=original.choices,
            group=original.group,
        )
        changed = ModuleSpec(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            edition=spec.edition,
            element=spec.element,
            params=[renamed] + list(spec.params[1:]),
        )
        after = _id_table({code: changed})
        assert after[(code, "Renamed Parameter")] == 0, (
            f"{code}: the renamed parameter does not keep the id 0"
        )
        for desc in spec.params[1:]:
            assert after[(code, desc.name)] == _id_table({code: spec})[(code, desc.name)], (
                f"{code}.{desc.name} moved when the parameter before it was "
                "renamed"
            )


def test_growing_a_module_keeps_the_ids_of_the_other_parameters():
    """A parameter that is appended keeps the ids of the ones before it."""
    for code in CODES:
        spec = SPECS[code]
        before = _id_table({code: spec})
        grown = ModuleSpec(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            edition=spec.edition,
            element=spec.element,
            params=list(spec.params)
            + [
                ParamDesc(
                    name="Appended Parameter",
                    type="float",
                    default=0.0,
                    minimum=0.0,
                    maximum=1.0,
                )
            ],
        )
        after = _id_table({code: grown})
        for desc in spec.params:
            assert after[(code, desc.name)] == before[(code, desc.name)], (
                f"{code}.{desc.name} moved when a parameter was appended"
            )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "the id of a parameter is its position, so a removed parameter "
        "renumbers the ones after it; see var/patches/f1_open_questions.md"
    ),
)
def test_a_removed_parameter_leaves_a_hole_in_the_id_space():
    """A parameter that is removed must not move the ids of the others.

    The hosts store the state of a plug-in by parameter id.  A removal that
    renumbers the rest of the module would make every stored state of that
    module address the wrong parameter, so a removed parameter has to leave
    a hole in the id space.
    """
    for code in CODES:
        spec = SPECS[code]
        if len(spec.params) < 2:
            continue
        before = _id_table({code: spec})
        shortened = ModuleSpec(
            code=spec.code,
            display_name=spec.display_name,
            category=spec.category,
            edition=spec.edition,
            element=spec.element,
            params=list(spec.params[:1]) + list(spec.params[2:]),
        )
        after = _id_table({code: shortened})
        for desc in spec.params[2:]:
            assert after[(code, desc.name)] == before[(code, desc.name)], (
                f"{code}.{desc.name}: the id moved from "
                f"{before[(code, desc.name)]} to {after[(code, desc.name)]} when "
                "the parameter before it was removed"
            )


def test_removing_a_module_keeps_the_ids_of_the_other_modules():
    """A module that is removed does not move the ids of the other modules.

    The id space is per module, so the id of a parameter only depends on the
    parameters of its own module.
    """
    baseline = _id_table(SPECS)
    for code in CODES:
        smaller = {other: spec for other, spec in SPECS.items() if other != code}
        after = _id_table(smaller)
        for other in smaller:
            for desc in smaller[other].params:
                assert after[(other, desc.name)] == baseline[(other, desc.name)], (
                    f"{other}.{desc.name} moved when {code} was removed"
                )


# ---------------------------------------------------------------------------
# 4. the generated artefacts
# ---------------------------------------------------------------------------
def test_the_generated_registry_carries_the_table():
    """The generated registry is the table, parameter for parameter."""
    import json

    path = ROOT / "aurcore" / "schema" / "modules.json"
    if not path.is_file():
        pytest.skip("the registry has not been generated yet")
    document = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for code, spec in SPECS.items():
        module = document.get(code)
        if module is None:
            problems.append(f"{code} is missing from the registry")
            continue
        params = module.get("params") or []
        if [p.get("name") for p in params] != [d.name for d in spec.params]:
            problems.append(f"{code}: the registry lists other parameters")
            continue
        for index, (param, desc) in enumerate(zip(params, spec.params)):
            if param.get("id") != index:
                problems.append(f"{code}.{desc.name}: the id is {param.get('id')}")
            if param.get("unit") != desc.unit:
                problems.append(
                    f"{code}.{desc.name}: the registry says {param.get('unit')!r}"
                )
            if desc.type == "enum":
                if list(param.get("choices") or []) != list(desc.choices):
                    problems.append(f"{code}.{desc.name}: other choices")
            elif not math.isclose(
                float(param.get("default")), float(desc.default), abs_tol=1e-9
            ):
                problems.append(
                    f"{code}.{desc.name}: the registry defaults to "
                    f"{param.get('default')!r}"
                )
            if param.get("curve") != desc.curve:
                problems.append(
                    f"{code}.{desc.name}: the registry says the curve "
                    f"{param.get('curve')!r}"
                )
            if desc.curve == "taper" and not math.isclose(
                float(param.get("curve_exponent")),
                float(desc.curve_exponent),
                abs_tol=1e-9,
            ):
                problems.append(
                    f"{code}.{desc.name}: the registry says the exponent "
                    f"{param.get('curve_exponent')!r}"
                )
            if list(param.get("aliases") or []) != list(desc.aliases):
                problems.append(f"{code}.{desc.name}: the registry says other aliases")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 5. the travel of a control
# ---------------------------------------------------------------------------
#: The resolution a frequency control must have: the logarithmic law over
#: 20 ... 20000 Hz moves 0.0997 octaves per one percent of the travel.
TARGET_OCTAVES_PER_PERCENT = 0.0997

#: The resolution a gain control must have at unity.
TARGET_DB_PER_PERCENT = 0.316


def _octaves_per_percent(desc: ParamDesc, position: float) -> float:
    """How many octaves one percent of the travel moves a frequency."""
    low = desc.natural_value(position)
    high = desc.natural_value(min(position + 0.01, 1.0))
    if low <= 0.0 or high <= 0.0:
        return 0.0
    return math.log2(high / low)


def _decibels_per_percent(desc: ParamDesc, position: float) -> float:
    """How many decibels one percent of the travel moves a value."""
    return desc.natural_value(min(position + 0.01, 1.0)) - desc.natural_value(position)


def _position_of_unity(desc: ParamDesc) -> float:
    """Where unity sits on the travel of a tapered parameter."""
    low = float(desc.minimum)
    high = float(desc.maximum)
    if high <= low:
        return 0.0
    return ((0.0 - low) / (high - low)) ** (1.0 / float(desc.curve_exponent))


def test_every_frequency_control_above_zero_is_logarithmic():
    """A frequency control moves in the logarithm of its value.

    The law is the one the model already carried and the adapter already
    implemented; the table is what never set it.
    """
    problems = [
        f"{row.code}.{row.param} is {row.curve!r}, not logarithmic"
        for row in DATA_ROWS
        if row.unit == "Hz"
        and float(row.minimum) > 0.0
        and row.curve != "log"
    ]
    assert not problems, "\n".join(problems)


def test_a_frequency_control_is_as_fine_as_the_reference():
    """One percent of the travel moves a frequency by at most 0.0997 octaves.

    The control used to move 1.58 octaves at the bottom of its range, so a
    100 Hz region could not be set at all.
    """
    problems = []
    for code, spec in SPECS.items():
        for desc in spec.params:
            if desc.type != "float" or desc.unit != "Hz":
                continue
            if float(desc.minimum) <= 0.0:
                continue  # the logarithm of zero is not a number
            worst = max(
                _octaves_per_percent(desc, step / 100.0) for step in range(0, 100)
            )
            if worst > TARGET_OCTAVES_PER_PERCENT + 1e-9:
                problems.append(
                    f"{code}.{desc.name}: one percent of the travel moves it by "
                    f"{worst:.4f} octaves"
                )
    assert not problems, "\n".join(problems)


def test_every_gain_control_is_tapered():
    """A gain control spends its travel below unity.

    The taper is the shape of a fader: a wide cut, a narrow boost, and unity
    in the middle of the travel.  The controls that keep a straight line on
    purpose are the ones named in :data:`LINEAR_ON_PURPOSE`.
    """
    problems = [
        f"{row.code}.{row.param} is {row.curve!r}, not tapered"
        for row in DATA_ROWS
        if row.unit == "dB"
        and float(row.minimum) < 0.0 < float(row.maximum)
        and row.curve != "taper"
        and (row.code, row.param) not in LINEAR_ON_PURPOSE
    ]
    assert not problems, "\n".join(problems)


def test_unity_sits_in_the_middle_of_the_travel_of_a_gain_control():
    """A tapered gain control puts unity in the middle of its travel."""
    problems = []
    for code, spec in SPECS.items():
        for desc in spec.params:
            if desc.curve != "taper" or (code, desc.name) not in TAPERED_GAINS:
                continue
            position = _position_of_unity(desc)
            if abs(position - 0.5) > 0.01:
                problems.append(
                    f"{code}.{desc.name}: unity sits at {position:.4f} of the travel"
                )
    assert not problems, "\n".join(problems)


def test_a_gain_control_is_as_fine_as_the_reference_at_unity():
    """One percent of the travel moves a gain by at most 0.316 dB at unity."""
    problems = []
    for code, spec in SPECS.items():
        for desc in spec.params:
            if desc.curve != "taper" or (code, desc.name) not in TAPERED_GAINS:
                continue
            position = _position_of_unity(desc)
            moved = _decibels_per_percent(desc, position)
            if moved > TARGET_DB_PER_PERCENT + 1e-9:
                problems.append(
                    f"{code}.{desc.name}: one percent of the travel moves it by "
                    f"{moved:.4f} dB at unity"
                )
    assert not problems, "\n".join(problems)


def test_the_controls_that_keep_a_straight_line_keep_it_on_purpose():
    """A threshold and a makeup gain are judged against the signal.

    They are the two controls the taper law does not fit: a threshold is read
    against the material that comes in, and a makeup gain is read against the
    material that comes out.
    """
    problems = []
    for (code, name) in LINEAR_ON_PURPOSE:
        spec = SPECS.get(code)
        if spec is None:
            problems.append(f"{code} is not in the table")
            continue
        desc = next((d for d in spec.params if d.name == name), None)
        if desc is None:
            problems.append(f"{code}.{name} is not in the table")
            continue
        if desc.curve != "linear":
            problems.append(f"{code}.{name} is {desc.curve!r}, not a straight line")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 6. the names
# ---------------------------------------------------------------------------
#: The parameters that carry an old name, and the old name itself.
RENAMED: dict[tuple[str, str], str] = {
    ("AUR_DECRACKLE", "Reduction"): "Strength",
    ("AUR_DEPLOSIVE", "Reduction"): "Strength",
    ("AUR_DEREVERB", "Band Reduction Low"): "Band Strength Low",
    ("AUR_DEREVERB", "Band Reduction Low-Mid"): "Band Strength Low-Mid",
    ("AUR_DEREVERB", "Band Reduction High-Mid"): "Band Strength High-Mid",
    ("AUR_DEREVERB", "Band Reduction High"): "Band Strength High",
    ("AUR_DEBLEED", "Reduction"): "Strength",
    ("AUR_DERUSTLE", "Reduction"): "Reduction Strength",
    ("AUR_SPECTRALREPAIR", "Reduction"): "Strength",
    ("AUR_CENTEREXTRACT", "Reduction"): "Reduction Strength",
    ("AUR_SPECTRALEDITOR", "Reduction"): "Strength",
    ("AUR_AZIMUTH", "Gain"): "Level",
    ("AUR_CLIPCLASSIFIER", "Similarity"): "Match Strength",
}


def test_the_surface_does_not_use_two_words_for_one_idea():
    """The amount that is taken out is a Reduction, everywhere.

    The surface used to call it a Reduction in twelve names and a Strength
    in seven; the word "Strength" is gone.
    """
    problems = [
        f"line {row.lineno}: {row.code}.{row.param} still says Strength"
        for row in DATA_ROWS
        if "Strength" in row.param
    ]
    assert not problems, "\n".join(problems)


def test_every_renamed_parameter_carries_its_old_name():
    """A renamed parameter keeps the name it had, so a document still loads."""
    problems = []
    for (code, name), old in RENAMED.items():
        spec = SPECS.get(code)
        if spec is None:
            problems.append(f"{code} is not in the table")
            continue
        desc = next((d for d in spec.params if d.name == name), None)
        if desc is None:
            problems.append(f"{code}.{name} is not in the table")
            continue
        if old not in desc.aliases:
            problems.append(f"{code}.{name} does not carry the alias {old!r}")
    assert not problems, "\n".join(problems)


def test_an_old_name_is_still_accepted_by_the_module():
    """The store of the module answers to the old name of a parameter."""
    problems = []
    for (code, name), old in RENAMED.items():
        instance = _module_instance(code)
        store = instance.params
        if not store.has(old):
            problems.append(f"{code}: the store does not know {old!r}")
            continue
        if not store.has(name):
            problems.append(f"{code}: the store does not know {name!r}")
            continue
        store.set(old, 0.25)
        if abs(float(store.get(name)) - 0.25) > 1e-9:
            problems.append(f"{code}: setting {old!r} did not move {name!r}")
    assert not problems, "\n".join(problems)
