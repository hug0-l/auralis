"""
tests/test_benchmark_restoration.py — the gate of the restoration constants.

The numbers of the restoration study live in
``qa/benchmark/reference_restoration.py``.  A number that cannot be read is a
number that cannot be argued about, so this gate asserts the **properties of
the constants**, not the behaviour of any product:

* every constant of the study is there, under the name the study gave it, with
  the value the study recorded;
* every constant names the **case** it belongs to, the **unit** it is in and
  the **way it was measured**;
* the unit of a constant is the unit of its metric;
* every value is finite;
* the file names no product of anybody.

A measurement of a reference is a **target, not a contract**.  It was taken on
somebody else's build, on somebody else's machine, with somebody else's
settings, so nothing here fails on the number itself: a gate that failed on it
would be a gate held by a machine we do not control.

Nothing in this file asserts what Auralis does.  The numbers that are
measurements of our own modules carry the series ``ours``, so that they are
never mistaken for a measurement of a reference, and the gate checks that they
are filed that way.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "qa" / "benchmark"
_CONSTANTS = _BENCH / "reference_restoration.py"
_DOCUMENT = _ROOT / "docs" / "RESTORATION_REFERENCE.md"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hygiene_check  # noqa: E402

import importlib.util  # noqa: E402


def _load(name: str, path: Path):
    """Load a module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


constants = _load("aur_qa_benchmark_reference_restoration", _CONSTANTS)


# ---------------------------------------------------------------------------
# the numbers the study recorded
#
# The name of every constant and the value it carries.  A constant that is
# added, renamed or given another value has to change this table with it: the
# table is what says what the study measured.
# ---------------------------------------------------------------------------
EXPECTED: dict[str, float] = {
    # what it removes, keeps and leaves alone
    "REFERENCE_VOICE_HISS_REMOVED_DB": 96.3,
    "REFERENCE_VOICE_HISS_KEPT_DB": 0.0,
    "REFERENCE_UNTOUCHED_LEVEL_DB": 0.04,
    # its best mode and its factory default
    "REFERENCE_MODE_BEST_REMOVED_DB": 24.64,
    "REFERENCE_MODE_BEST_KEPT_DB": 0.22,
    "REFERENCE_MODE_DEFAULT_REMOVED_DB": 27.32,
    "REFERENCE_MODE_DEFAULT_KEPT_DB": 3.03,
    "REFERENCE_MODE_REVERB_REMOVED_DB": 53.90,
    "REFERENCE_MODE_REVERB_KEPT_DB": 1.19,
    # what a block of 512 frames costs it
    "REFERENCE_MODE_COST_MS_PER_512_FRAMES": 23.0,
    "REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN": 5.5,
    # how fast it decides, and how much its gain breathes
    "REFERENCE_DECISION_MS": 28.7,
    "REFERENCE_DECISION_MS_OURS": 1.3,
    "REFERENCE_CHATTER_DB2": 2.518,
    "REFERENCE_CHATTER_DB2_OURS": 0.021,
    # the cost of splitting a signal into bands and mixing it back
    "REFERENCE_SPLIT_ROUNDTRIP_DB": 0.73,
    "REFERENCE_SPLIT_DELAY_SAMPLES": 3,
    # the latency it declares, and the tail it does not
    "REFERENCE_LATENCY_SAMPLES_48K": 1470,
    "REFERENCE_LATENCY_BLOCK_BASE_SAMPLES": 1214,
    "REFERENCE_LATENCY_MODEL_SAMPLES_48K": 3993,
    "REFERENCE_DECISION_LAG_MS": 12.5,
    "REFERENCE_TAIL_SAMPLES": 0,
    "REFERENCE_TAIL_EXCESS_SAMPLES": 325,
    # the state it carries
    "REFERENCE_STATE_COMPONENT_BYTES": 1042,
    "REFERENCE_STATE_CONTROLLER_BYTES": 0,
    # its surface
    "REFERENCE_SURFACE_PARAMETERS": 22,
    "REFERENCE_SURFACE_MODES": 7,
    "REFERENCE_SURFACE_MODE_SLOTS": 16,
    "REFERENCE_SURFACE_PROGRAM_LIST": 1,
    # what it ships as data
    "REFERENCE_MODELS_SHIPPED": 7,
    "REFERENCE_MODEL_SIZE_BYTES_MIN": 16175616,
    "REFERENCE_MODEL_SIZE_BYTES_MAX": 45376512,
    # the laws of its controls
    "REFERENCE_FREQUENCY_CURVE_P": 3.334,
    "REFERENCE_CUTOFF_RANGE_LO_HZ": 100.0,
    "REFERENCE_CUTOFF_RANGE_HI_HZ": 10000.0,
    "REFERENCE_GAIN_CURVE_P": 0.264,
    "REFERENCE_GAIN_RANGE_LO_DB": -60.0,
    "REFERENCE_GAIN_RANGE_HI_DB": 12.0,
    "REFERENCE_GAIN_DB_PER_PERCENT_AT_UNITY": 0.316,
    # its synthesis
    "REFERENCE_PHASE_CORRELATION": 0.0124,
    "REFERENCE_PHASE_CORRELATION_OURS": 0.9991,
    # the corpus head-to-head, the setting the reference comes with
    "REFERENCE_HISS_REMOVED_DB_DEFAULT": 14.11,
    "REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT": 14.09,
    "REFERENCE_HUM50_REMOVED_DB_DEFAULT": 14.08,
    "REFERENCE_RUMBLE_REMOVED_DB_DEFAULT": 14.13,
    "REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT": 14.12,
    "REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT": 13.71,
    "REFERENCE_HISS_KEPT_DB_DEFAULT": 0.0,
    "REFERENCE_PINK_HISS_KEPT_DB_DEFAULT": 0.0,
    "REFERENCE_HUM50_KEPT_DB_DEFAULT": 13.31,
    "REFERENCE_RUMBLE_KEPT_DB_DEFAULT": 16.18,
    "REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT": 5.79,
    "REFERENCE_PLOSIVE_KEPT_DB_DEFAULT": 14.69,
    # the corpus head-to-head, the best setting the reference offers.  The
    # three removal figures and the two "kept" figures that carry FLOOR in
    # their name are floors of the measurement: the band of the output is
    # exactly zero there, so the decibels are not a figure of removal.
    "REFERENCE_HISS_REMOVED_DB_BEST_FLOOR": 269.29,
    "REFERENCE_PINK_HISS_REMOVED_DB_BEST": 46.3,
    "REFERENCE_HUM50_REMOVED_DB_BEST": 80.15,
    "REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR": 302.38,
    "REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR": 296.47,
    "REFERENCE_PLOSIVE_REMOVED_DB_BEST": 41.29,
    "REFERENCE_HISS_KEPT_DB_BEST": 0.0,
    "REFERENCE_PINK_HISS_KEPT_DB_BEST": 0.0,
    "REFERENCE_HUM50_KEPT_DB_BEST_FLOOR": 232.37,
    "REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR": 232.45,
    "REFERENCE_VOICE_HISS_KEPT_DB_BEST": 1.28,
    "REFERENCE_PLOSIVE_KEPT_DB_BEST": 0.17,
    # what the output of the reference is: its level and the share of it that
    # is digital silence
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT": -14.29,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT": -14.29,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT": -11.91,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT": -11.8,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT": -3.07,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT": 1.36,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST": -38.36,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST": -43.74,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST": -62.0,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST": -28.12,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST": -0.41,
    "REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST": -1.09,
    "REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST": 0.812,
    "REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST": 0.862,
    "REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST": 0.856,
    "REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST": 0.0,
    "REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST": 0.0,
    "REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST": 0.0,
    "REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT": 0.0,
    # how much the gain curve of the reference breathes
    "REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT": 0.042,
    "REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT": 0.458,
    "REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT": 0.003,
    "REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT": 7.476,
    "REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT": 0.041,
    "REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT": 60.187,
    "REFERENCE_GAIN_VAR_DB2_HISS_BEST": 0.019,
    "REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST": 0.227,
    "REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST": 0.025,
    "REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST": 8.152,
    # how repeatable the reference is
    "REFERENCE_REPRODUCIBLE": False,
    "REFERENCE_REPRODUCIBILITY_SPREAD_DB": 0.8,
    "REFERENCE_REPRODUCIBLE_OURS": True,
    # the first band edge, and the latency that belongs to the model
    "REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ": 1000.0,
    "REFERENCE_LATENCY_SAMPLES_MIN": 3880,
    "REFERENCE_LATENCY_SAMPLES_MAX": 4641,
    # our own side of the same table
    "REFERENCE_HISS_REMOVED_DB_OURS": 13.54,
    "REFERENCE_PINK_HISS_REMOVED_DB_OURS": 17.3,
    "REFERENCE_HUM50_REMOVED_DB_OURS": 38.03,
    "REFERENCE_RUMBLE_REMOVED_DB_OURS": 11.44,
    "REFERENCE_VOICE_HISS_REMOVED_DB_OURS": 17.7,
    "REFERENCE_HISS_KEPT_DB_OURS": 0.0,
    "REFERENCE_PINK_HISS_KEPT_DB_OURS": 0.0,
    "REFERENCE_HUM50_KEPT_DB_OURS": 0.04,
    "REFERENCE_RUMBLE_KEPT_DB_OURS": 0.03,
    "REFERENCE_VOICE_HISS_KEPT_DB_OURS": 0.02,
    # the bypass and the defaults
    "REFERENCE_BYPASS_BIT_EXACT": True,
    "REFERENCE_BYPASS_MAX_DIFF": 0.0,
    "REFERENCE_BYPASS_DELAY_SAMPLES_256": 1470,
    "REFERENCE_BYPASS_DELAY_SAMPLES_512": 1726,
    "REFERENCE_BYPASS_DELAY_SAMPLES_1024": 2238,
    "REFERENCE_BYPASS_DELAY_MS_AT_48K": 35.96,
    "REFERENCE_DEFAULT_MAX_DIFF": 0.000321984,
    "REFERENCE_DEFAULT_MAX_DIFF_DB": -63.84,
    "REFERENCE_DEFAULT_MAX_DIFF_24BIT_STEPS": 2701,
    "REFERENCE_DEFAULT_GAIN_DB": -0.0055,
    "REFERENCE_DEFAULT_GAIN_SPREAD_DB": 0.0073,
    "REFERENCE_DEFAULT_GAIN_SD_DB": 0.0012,
    "REFERENCE_DEFAULT_PRE_SIGNAL_NOISE_DBFS": -103.10,
    "OWN_BYPASS_BIT_EXACT": True,
    "OWN_BYPASS_DELAY_SAMPLES": 0,
    "OWN_DEFAULT_BIT_EXACT_ON_CLEAN": True,
}

#: The names that are measurements of our own modules.  They carry their own
#: series, so that a reader can tell which side of the comparison they are on.
OURS = (
    "REFERENCE_DECISION_MS_OURS",
    "REFERENCE_CHATTER_DB2_OURS",
    "REFERENCE_PHASE_CORRELATION_OURS",
    "REFERENCE_REPRODUCIBLE_OURS",
    "REFERENCE_HISS_REMOVED_DB_OURS",
    "REFERENCE_PINK_HISS_REMOVED_DB_OURS",
    "REFERENCE_HUM50_REMOVED_DB_OURS",
    "REFERENCE_RUMBLE_REMOVED_DB_OURS",
    "REFERENCE_VOICE_HISS_REMOVED_DB_OURS",
    "REFERENCE_HISS_KEPT_DB_OURS",
    "REFERENCE_PINK_HISS_KEPT_DB_OURS",
    "REFERENCE_HUM50_KEPT_DB_OURS",
    "REFERENCE_RUMBLE_KEPT_DB_OURS",
    "REFERENCE_VOICE_HISS_KEPT_DB_OURS",
    "OWN_BYPASS_BIT_EXACT",
    "OWN_BYPASS_DELAY_SAMPLES",
    "OWN_DEFAULT_BIT_EXACT_ON_CLEAN",
)


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_constants_are_importable():
    """The file has to be a module a reader can import."""
    assert constants.RESTORATIONS, "there is no constant at all"
    assert constants.METRICS, "there is no metric at all"


def test_the_file_names_no_product():
    """No trademark of anybody may appear in the file.

    The gate of the repository is used to check the gate of the constants, so
    the two can never disagree about what a banned name is.
    """
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "qa/benchmark/reference_restoration.py",
        _CONSTANTS.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_document_names_no_product():
    """The same rule holds for the note that reads the constants."""
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "docs/RESTORATION_REFERENCE.md",
        _DOCUMENT.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


# ---------------------------------------------------------------------------
# the constants
# ---------------------------------------------------------------------------
def test_every_constant_of_the_study_is_there():
    """A constant that is missing is a number the study measured and we lost."""
    assert constants.RESTORATION_BY_NAME.keys() == EXPECTED.keys()
    for name, value in EXPECTED.items():
        assert getattr(constants, name) == value, name


def test_every_number_carries_its_case_its_unit_and_its_method():
    """A number without those three cannot be read, and cannot be replaced."""
    for restoration in constants.RESTORATIONS:
        assert restoration.name, restoration
        assert restoration.case, restoration.name
        assert restoration.unit, restoration.name
        assert restoration.method, restoration.name
        assert restoration.series in constants.SERIES, restoration.name


def test_the_unit_of_a_number_is_the_unit_of_its_metric():
    """The unit is carried twice on purpose, and the two have to agree."""
    for restoration in constants.RESTORATIONS:
        assert restoration.metric in constants.METRIC_BY_NAME, restoration.name
        rule = constants.METRIC_BY_NAME[restoration.metric]
        assert restoration.unit == rule.unit, restoration.name


def test_every_value_is_finite():
    """A number that is not finite cannot be compared with anything."""
    for restoration in constants.RESTORATIONS:
        assert math.isfinite(float(restoration.value)), restoration.name
    for name, value in EXPECTED.items():
        assert math.isfinite(float(value)), name


def test_the_table_of_the_constants_and_the_table_here_agree():
    """The value in the table is the value in the record of the number."""
    for restoration in constants.RESTORATIONS:
        assert EXPECTED[restoration.name] == float(restoration.value), (
            restoration.name
        )


def test_a_number_of_ours_is_filed_under_its_own_series():
    """A number of ours must never be mistaken for a number of a reference."""
    for name in OURS:
        assert constants.RESTORATION_BY_NAME[name].series == "ours", name
    for restoration in constants.RESTORATIONS:
        if restoration.series != "ours":
            assert restoration.name not in OURS, restoration.name
    assert "ours" in constants.SERIES


def test_the_two_reference_series_are_the_series_of_the_benchmark():
    """The constants file and the benchmark have to speak of the same series."""
    benchmark = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
    for series in ("band_restoration", "stem_separation"):
        assert series in benchmark.SERIES, series
        assert series in constants.SERIES, series


def test_the_case_of_a_number_is_one_the_file_declares():
    """A number has to belong to a case, and the cases are written down."""
    for restoration in constants.RESTORATIONS:
        assert restoration.case in constants.CASES, restoration.name


def test_every_case_of_the_benchmark_can_be_named():
    """The vocabulary of the file has to cover the cases of the benchmark."""
    cases = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
    for case in cases.CASES:
        assert case.name in constants.CASES, case.name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_document_quotes_the_constant(name: str):
    """The note and the constants file must not drift apart."""
    text = _DOCUMENT.read_text(encoding="utf-8")
    assert name in text, name
