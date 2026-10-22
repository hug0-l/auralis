"""
tests/test_benchmark_adaptive.py — the gate of the adaptive behaviour numbers.

The numbers of the adaptive behaviour live in
``qa/benchmark/reference_masking.py``: the five that describe the reference and
the one that describes ours.  A number that cannot be read is a number that
cannot be argued about, so this gate asserts the **properties of the
constants**, not the behaviour of any product:

* every constant of the study is there, under the name the study gave it,
  with the value the study recorded;
* every constant carries its **case**, its **unit** and the **way it was
  measured** — in that file the way it was measured is the comment the
  constant stands under, so the gate reads that comment back;
* every value is finite and has the sign its meaning needs;
* the file names no product of anybody, and neither does the note that reads
  the numbers.

A measurement of a reference is a **target, not a contract**.  It was taken on
somebody else's build, on somebody else's machine, with somebody else's
settings, so nothing here fails on the number itself: a gate that failed on it
would be a gate held by a machine we do not control.  The number of ours, on
the other hand, is a measurement of our own module and it is the number the
benchmark has to close the gap on.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "qa" / "benchmark"
_CONSTANTS = _BENCH / "reference_masking.py"
_NOTE = _ROOT / "docs" / "ADAPTIVE_BEHAVIOUR.md"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hygiene_check  # noqa: E402


def _load(name: str, path: Path):
    """Load a module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


constants = _load("aur_qa_benchmark_reference_masking", _CONSTANTS)


# ---------------------------------------------------------------------------
# the numbers the study recorded
#
# The name of every constant and the value it carries.  A constant that is
# added, renamed or given another value has to change this table with it: the
# table is what says what the study measured.
# ---------------------------------------------------------------------------
EXPECTED: dict[str, float] = {
    # the reference
    "REFERENCE_BED_LEAK_INTO_WANTED_DB": -0.12,
    "REFERENCE_SEPARATION_WANTED_OVER_BED_DB": 25.4,
    "REFERENCE_CHARACTER_SETTLING_MS": 100.0,
    "REFERENCE_LEVEL_SETTLING_MS": 25.0,
    "REFERENCE_CONVERGENCE_MS": 250.0,
    # ours
    "OUR_BED_LEAK_LEVEL_SENSITIVITY_DB": 0.79,
}

#: The unit of every constant of the study.
UNITS: dict[str, str] = {
    "REFERENCE_BED_LEAK_INTO_WANTED_DB": "dB",
    "REFERENCE_SEPARATION_WANTED_OVER_BED_DB": "dB",
    "REFERENCE_CHARACTER_SETTLING_MS": "ms",
    "REFERENCE_LEVEL_SETTLING_MS": "ms",
    "REFERENCE_CONVERGENCE_MS": "ms",
    "OUR_BED_LEAK_LEVEL_SENSITIVITY_DB": "dB",
}

#: The name the study prepared for the number of ours.  It is the same
#: number, filed with our own numbers, and it keeps the name of the study so
#: that the prepared text can be applied without renaming.
ALIAS = "REFERENCE_BED_LEAK_LEVEL_SENSITIVITY_DB"
ALIAS_OF = "OUR_BED_LEAK_LEVEL_SENSITIVITY_DB"


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def _source() -> str:
    """The text of the constants file."""
    return _CONSTANTS.read_text(encoding="utf-8")


def _line_of(source: str, name: str) -> int:
    """Return the line a constant is assigned on."""
    tree = ast.parse(source, filename=str(_CONSTANTS))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.lineno
    raise AssertionError(f"{name} is not a constant of {_CONSTANTS}")


def _comment_above(source: str, lineno: int) -> str:
    """Return the comment block that stands directly above a line."""
    lines = source.splitlines()
    collected: list[str] = []
    index = lineno - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        collected.append(lines[index].strip())
        index -= 1
    return " ".join(reversed(collected))


def test_the_constants_are_importable():
    """The file has to be a module a reader can import."""
    assert constants.MASKING_LAWS, "there is no law at all"
    for name in list(EXPECTED) + [ALIAS]:
        assert hasattr(constants, name), name


def test_the_file_names_no_product():
    """No trademark of anybody may appear in the file.

    The gate of the repository is used to check the gate of the constants, so
    the two can never disagree about what a banned name is.
    """
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "qa/benchmark/reference_masking.py",
        _source(),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_note_names_no_product():
    """The same rule holds for the note that reads the constants."""
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "docs/ADAPTIVE_BEHAVIOUR.md",
        _NOTE.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


# ---------------------------------------------------------------------------
# the constants
# ---------------------------------------------------------------------------
def test_every_constant_of_the_study_is_there():
    """A constant that is missing is a number the study measured and we lost."""
    for name, value in EXPECTED.items():
        assert getattr(constants, name) == value, name


def test_every_value_is_finite():
    """A number that is not finite cannot be compared with anything."""
    for name, value in EXPECTED.items():
        assert math.isfinite(float(value)), name


def test_every_number_carries_its_case_its_unit_and_its_method():
    """A number without those three cannot be read, and cannot be replaced.

    The constants of this file are plain constants, so the case, the unit and
    the way it was measured are the comment the constant stands under.  A
    constant without that comment is a number nobody can read.
    """
    source = _source()
    for name in EXPECTED:
        comment = _comment_above(source, _line_of(source, name))
        assert comment, f"{name} has no comment: no case, no unit, no method"
        assert len(comment) > 80, f"{name} has a thin comment: {comment}"
        assert UNITS[name] in comment, f"{name} does not name its unit"
        assert "measured" in comment.lower(), (
            f"{name} does not say how it was measured: {comment}"
        )


def test_every_number_names_the_case_it_belongs_to():
    """A number without its case cannot be compared with another one."""
    source = _source()
    for name in EXPECTED:
        comment = _comment_above(source, _line_of(source, name))
        assert "case" in comment.lower(), f"{name} does not name its case"


def test_the_leak_is_below_the_wanted_part_and_the_gap_is_above_it():
    """The signs carry the meaning: a leak under zero, a gap over it."""
    assert constants.REFERENCE_BED_LEAK_INTO_WANTED_DB < 0.0
    assert constants.OUR_BED_LEAK_LEVEL_SENSITIVITY_DB > 0.0


def test_the_separation_is_read_as_a_ratio_of_two_stems():
    """The separation is the wanted part over the bed, so it is over zero."""
    assert constants.REFERENCE_SEPARATION_WANTED_OVER_BED_DB > 0.0


def test_the_settling_times_are_ordered_the_way_the_study_found_them():
    """A level step settles before a change of character settles."""
    assert constants.REFERENCE_LEVEL_SETTLING_MS < constants.REFERENCE_CHARACTER_SETTLING_MS
    assert constants.REFERENCE_CHARACTER_SETTLING_MS < constants.REFERENCE_CONVERGENCE_MS


def test_the_number_of_ours_is_filed_with_our_own_numbers():
    """A number of ours must not look like a number of the reference.

    It keeps the name the study prepared as an alias, and the alias has to
    carry the same value: one number, two names, not two numbers.
    """
    assert constants.OUR_BED_LEAK_LEVEL_SENSITIVITY_DB == pytest.approx(0.79)
    assert getattr(constants, ALIAS) == getattr(constants, ALIAS_OF)
    assert ALIAS in constants.__all__
    assert ALIAS_OF in constants.__all__


# ---------------------------------------------------------------------------
# the note
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_note_quotes_the_constant(name: str):
    """The note and the constants file must not drift apart."""
    text = _NOTE.read_text(encoding="utf-8")
    assert name in text, name


def test_the_note_carries_our_own_defect():
    """The defect of ours is written down, with the number of it."""
    text = _NOTE.read_text(encoding="utf-8")
    assert "0.79" in text
    assert "not level invariant" in text


def test_the_note_says_which_of_ours_learn():
    """The note names the modules that learn and where their state lives."""
    text = _NOTE.read_text(encoding="utf-8")
    assert "AUR_SPECTRALDENOISE" in text
    assert "AUR_VOICEDENOISE" in text
    assert "per file" in text
    assert "per session" in text
