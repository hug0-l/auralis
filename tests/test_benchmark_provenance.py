"""tests/test_benchmark_provenance.py — the provenance gate of the benchmark.

Every number the tree carries says how it was measured, and the way it was
measured says **which instrument** measured it.  That is the tree's
convention, and this gate holds it in place:

* every recorded number carries a non-empty method string;
* every method string names an instrument, so a reader can tell a number
  measured with the study's **renderer** (which moves no parameter of its
  own) from a number measured with the study's **exercise host** (which moves
  a parameter of its own and never puts it back);
* a number measured with the study's exercise host carries the **caveat**,
  so it is never silently read as a measurement of the default.

The caveat is the one this tree's audit found: the study's exercise host
proves it may write a parameter by writing one of them to a different value,
and it leaves it there.  For the first measured product that parameter is
``Bypass``, for the second it is the ``Ambience Gain``.  Every measurement
taken with that host in that state describes a bypassed, or a re-gained,
plug-in — never the default.

The numbers of the study that were measured with the renderer, or with the
study's own driver, are **not** affected: the renderer moves no parameter of
its own, and the driver sets only the parameters it was asked for.  The
numbers of our own modules are our own numbers.

The reason this gate exists is that a method string that says "the study's
host" cannot be read on its own: the study has more than one host, and only
one of them has the defect.  The gate therefore asks the method string to
**name the instrument**, so that the difference can be read off the number
itself.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "qa" / "benchmark"
NOTES = ROOT / "docs" / "BENCHMARK_NOTES.md"

#: The constants files of the benchmark, and the record each of them holds.
FILES = {
    "reference.py": ("Reference", "REFERENCES"),
    "reference_restoration.py": ("Restoration", "RESTORATIONS"),
    "reference_voice.py": ("Voice", "VOICES"),
    "reference_model.py": ("ModelReference", "MODEL_REFERENCES"),
    "reference_bridge.py": ("Bridge", "BRIDGES"),
    "edge_cases.py": ("Edge", "EDGES"),
}

#: The instruments a method string may name.  The list is closed on purpose:
#: a number that cannot say which instrument measured it cannot be read.
#:
#: The study's instruments
#: -----------------------
#:
#: ``the study's renderer``
#:     The renderer of the study.  It writes a parameter only when it is
#:     asked to, and it moves no parameter of its own.
#: ``the study's driver``
#:     The driver of the study.  It sets only the parameters it was asked for.
#: ``the study's render host``
#:     The render host of the study.  It puts every parameter back to its
#:     default before it renders, so a render starts from the state the
#:     plug-in ships with.
#: ``the study's surface host``
#:     Reads the controller of a plug-in and changes nothing.
#: ``the study's edge host``
#:     Sets only the parameters it was asked for.
#: ``the study's real time host``
#:     Sets only the preset it was given.
#: ``the study's exercise host``
#:     The one with the defect: it moves a parameter of its own and never
#:     puts it back.
#: ``the study's own cost model``
#:     The arithmetic the study used to price a model.
#:
#: Ours
#: ----
#:
#: ``our own module`` / ``our own modules`` / ``the module of ours``
#:     The product's own engine.
#: ``our own reader`` / ``our own clock``
#:     Ours, reading a file or timing a run.
#: ``the ruler of the project`` / ``the quality instrument of the project``
#: / ``the voice instrument of the project``
#: / ``the edge contract instrument of the project``
#:     The instruments of the project.
#: ``the gate of the tree``
#:     The gate of the tree.
#: ``the product's own engine``
#:     The engine of the product.
INSTRUMENTS = (
    "the study's renderer",
    "the study's driver",
    "the study's render host",
    "the study's surface host",
    "the study's edge host",
    "the study's real time host",
    "the study's exercise host",
    "the study's own cost model",
    "our own module",
    "our own modules",
    "the module of ours",
    "our own reader",
    "our own clock",
    "our own performance harness",
    "the ruler of the project",
    "the ruler of the corpus",
    "the quality instrument of the project",
    "the voice instrument of the project",
    "the edge contract instrument of the project",
    "the gate of the tree",
    "the product's own engine",
)

#: The phrase that marks a number as measured with the study's exercise host.
#:
#: The study's exercise host is the one that proves it may write a parameter
#: by writing one of them to a different value, and never puts it back.  A
#: number measured with it therefore describes a plug-in with a moved
#: parameter, and the number says so.
EXERCISE_HOST = "the study's exercise host"

#: The phrases that say a parameter was moved and not put back.
MOVED = ("moved", "bypassed", "not restored", "never put it back")

#: The instruments that are ours.  A number of ours must name one of them.
OURS = (
    "our own module",
    "our own modules",
    "the module of ours",
    "our own reader",
    "our own clock",
    "a measurement of ours",
    "the product's own engine",
    "the edge contract instrument of the project",
    "the gate of the tree",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"aur_qa_benchmark_prov_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _records(name: str) -> list:
    record_name, collection = FILES[name]
    module = _load(name, BENCHMARK / name)
    rows = getattr(module, collection, None)
    assert rows, f"{name} has no {collection}"
    return list(rows)


def _label(record) -> str:
    for attribute in ("name", "metric"):
        value = getattr(record, attribute, None)
        if value:
            return str(value)
    return "?"


def _is_exercise_host(method: str) -> bool:
    """Does the method string say the study's exercise host measured it?

    The phrase that marks it is the one the audit added: without it, a method
    string that says "the study's host" cannot be told apart from one that
    says the study's renderer.
    """
    return EXERCISE_HOST in method


def _names_an_instrument(method: str) -> bool:
    """Does the method string name the instrument that measured the number?"""
    return any(phrase in method for phrase in INSTRUMENTS)


# ---------------------------------------------------------------------------
# the method string
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(FILES), ids=lambda n: n)
def test_every_number_carries_a_method(name: str):
    """A number without its method cannot be read and cannot be replaced."""
    for record in _records(name):
        method = getattr(record, "method", None)
        assert method, f"{_label(record)} has no method"
        assert len(method) > 10, f"{_label(record)} has a thin method"


@pytest.mark.parametrize("name", sorted(FILES), ids=lambda n: n)
def test_every_method_names_its_instrument(name: str):
    """A method string that names no instrument leaves the number unreadable.

    This is the gap the audit found: the study has more than one host, and
    only one of them moves a parameter and never puts it back, so "the
    study's host" is not enough to say whether a number is of the default.
    """
    for record in _records(name):
        method = record.method
        if _is_exercise_host(method):
            # a number of the exercise host says so, and says which one
            assert EXERCISE_HOST in method
            continue
        assert _names_an_instrument(method), (
            f"{name}: {_label(record)} names no instrument: {method[:120]}"
        )


# ---------------------------------------------------------------------------
# the exercise host and its caveat
# ---------------------------------------------------------------------------
def test_the_caveat_is_written_down():
    """The caveat about the exercise host is in the notes of the benchmark."""
    text = NOTES.read_text(encoding="utf-8")
    assert "exercise host" in text, (
        "docs/BENCHMARK_NOTES.md does not mention the study's exercise host"
    )
    assert "never put it back" in text or "never restores it" in text, (
        "the caveat does not say that the exercise host never puts the "
        "parameter back"
    )


def test_the_caveat_names_the_parameters_it_moves():
    """The caveat says which parameter the exercise host moves, per product."""
    text = NOTES.read_text(encoding="utf-8")
    assert "Bypass" in text, "the caveat does not name the bypass"
    assert "Ambience Gain" in text, "the caveat does not name the ambience gain"


@pytest.mark.parametrize("name", sorted(FILES), ids=lambda n: n)
def test_the_suspect_pile_is_the_one_the_caveat_names(name: str):
    """The numbers held as they are are the ones the caveat is about."""
    for record in _records(name):
        method = record.method
        if not _is_exercise_host(method):
            continue
        # a number of the exercise host must not claim to be of the default
        # without saying that it is not
        assert any(phrase in method for phrase in MOVED), (
            f"{name}: {_label(record)} is a number of the "
            "exercise host and does not say that a parameter was moved"
        )


def test_the_numbers_of_our_own_modules_are_ours():
    """A number of ours is our own number, and says so."""
    for name in FILES:
        for record in _records(name):
            if getattr(record, "series", "") != "ours":
                continue
            method = record.method
            assert any(phrase in method for phrase in OURS), (
                f"{name}: {_label(record)} is a number of ours and does not say so"
            )
