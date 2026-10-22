"""
tests/test_benchmark_model.py — the gate of the model constants.

The numbers of the model live in ``qa/benchmark/reference_model.py``.  The
file holds two subjects and this gate holds both:

* the **model path of ours** — the three configurations of the voice
  De-Noise (real time hand written, offline hand written, offline with a
  model), what each of them removes, what it keeps, how late it is, and
  what the model costs;
* the **study of the reference's model library** — the anatomy of a model
  artefact that was read from the outside, and the arithmetic of what such a
  model costs at the audio rate and at the frame rate.

A number that cannot be read is a number that cannot be argued about, so the
gate asserts the **properties of the constants**, not a copy of them:

* every record carries a name, a series, a case, a metric, a value, a unit
  and a non-empty method;
* the unit of a record is the unit its metric declares, and every value is
  finite;
* every name is unique and reads as the name of a number;
* the cases and the series of the file are the ones the tree uses: the three
  voice cases of the benchmark and the three configurations of the path;
* the yardstick of the costs is the yardstick of the project (8.300 us per
  1024 point transform, a 64 sample block may cost 160.6 of them), and every
  cost says how it was measured and how it was converted into it;
* a cost that is **not** the cost of one 64 sample block is not compared
  with the block budget: a load happens once per instance, a frame of
  inference is not a block, and a share of a render is not a cost in time;
* the arithmetic of the reference library holds: the frame rate cost is
  inside the budget and the audio rate cost is outside it, the share of the
  budget is the figure the note quotes, the parameter counts and the weight
  bytes agree, the cost of one pass is the number of the weights, the
  library is several models in several generations, and the three size
  classes have three depths;
* the file names no product of anybody;
* the constants the notes quote are the constants the file carries.

**These are arithmetic on the numbers of the study.**  They are not a
measurement of our own model, and nothing here says that our model is cheap
or that it exists.  A measurement of a reference is a **target, not a
contract**: it was taken on somebody else's build, on somebody else's
machine, with somebody else's settings, so nothing here fails on the number
itself — a gate that failed on it would be a gate held by a machine we do
not control.  What the gate does fail on is a number that **stops adding
up**: a cost that is not in the yardstick of the project, a record without a
case, a note that quotes a constant the file has forgotten.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "qa" / "benchmark"
_CONSTANTS = _BENCH / "reference_model.py"
_NOTE = _ROOT / "docs" / "MODEL_REFERENCE.md"
_COST_NOTE = _ROOT / "docs" / "MODEL_COST.md"

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


constants = _load("aur_qa_benchmark_reference_model", _CONSTANTS)
cases_module = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
reference_module = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
budgets_module = _load(
    "aur_qa_performance_budgets", _ROOT / "qa" / "performance" / "budgets.py"
)

#: The yardstick of the project: the cost of one 1024 point transform on the
#: reference machine, the budget of one 64 sample block in microseconds, and
#: the same budget in transform equivalents.
REFERENCE_TRANSFORM_US = 8.300
BUDGET_US_PER_BLOCK = 1333.3333
BUDGET_TRANSFORMS_PER_BLOCK = 160.6426

#: The three configurations of the model path of ours.  They are the names
#: the file gives them, and the tree uses them.
MODEL_PATH_SERIES = (
    "realtime_hand_written",
    "offline_hand_written",
    "offline_with_model",
)

#: The series of the study of the reference's model library.  It is a
#: subject of its own and its numbers are not measurements of ours.
STUDY_SERIES = "reference_model"

#: The quality metrics of the path: the removal, the movement of the wanted
#: content, the variance of the gain curve and the chatter of the gain.
QUALITY_METRICS = ("removed_db", "kept_db", "gain_var_db2", "chatter_db")

#: A number of the yardstick in the method of a cost: "12.57 ms is 12570.0
#: us, that is 1514.46 transforms of 8.300 us".
_YARDSTICK_PAIR = re.compile(
    r"([0-9]+(?:\.[0-9]+)?) us, that is "
    r"([0-9]+(?:\.[0-9]+)?) transforms of 8\.300 us"
)

#: The names the note quotes as constants of the study.
_QUOTED_NAME = re.compile(r"`(REFERENCE_MODEL_[A-Z0-9_]+)`")


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_file_carries_its_shape():
    """The file has to be a module a reader can import."""
    assert constants.MODEL_REFERENCES, "there is no number at all"
    assert constants.METRICS, "there is no metric at all"
    assert constants.METRIC_BY_NAME, "the metrics are not keyed by name"
    assert constants.MODEL_REFERENCE_BY_NAME, "the numbers are not keyed by name"
    assert constants.SERIES, "there is no series at all"
    assert constants.CASES, "there is no case at all"
    assert constants.EVERY_CASE in constants.CASES
    for name in ("model_references_for", "model_reference_for"):
        assert callable(getattr(constants, name, None)), name


def test_the_exports_of_the_file_are_the_names_it_carries():
    """``__all__`` and the module must not drift apart."""
    for name in constants.__all__:
        assert hasattr(constants, name), name


def test_the_file_names_no_product():
    """No trademark of anybody may appear in the file.

    The gate of the repository is used to check the gate of the constants, so
    the two can never disagree about what a banned name is.
    """
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "qa/benchmark/reference_model.py",
        _CONSTANTS.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_notes_name_no_product():
    """The same rule holds for the notes that read the constants."""
    for path in (_NOTE, _COST_NOTE):
        report = hygiene_check.Report(root=str(_ROOT))
        hygiene_check.check_text(
            path.relative_to(_ROOT).as_posix(),
            path.read_text(encoding="utf-8"),
            report,
        )
        assert not report.findings, "\n".join(f.format() for f in report.findings)


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------
def test_every_number_carries_its_name_its_case_its_unit_and_its_method():
    """A number without those cannot be read, and cannot be replaced."""
    for record in constants.MODEL_REFERENCES:
        assert record.name, record
        assert record.series, record.name
        assert record.case, record.name
        assert record.metric, record.name
        assert record.unit, record.name
        assert record.method and len(record.method) > 10, record.name
        assert record.series in constants.SERIES, record.name


def test_the_names_of_the_numbers_are_unique_and_read_as_names():
    """A name is how a number is addressed: it has to be one, and one only."""
    seen: set[str] = set()
    for record in constants.MODEL_REFERENCES:
        assert record.name not in seen, record.name
        seen.add(record.name)
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", record.name), record.name


def test_the_unit_of_a_number_is_the_unit_of_its_metric():
    """The unit is carried twice on purpose, and the two have to agree."""
    for record in constants.MODEL_REFERENCES:
        assert record.metric in constants.METRIC_BY_NAME, record.name
        rule = constants.METRIC_BY_NAME[record.metric]
        assert record.unit == rule.unit, record.name


def test_every_value_is_finite():
    """A number that is not finite cannot be compared with anything."""
    for record in constants.MODEL_REFERENCES:
        assert record.value is not None, record.name
        assert math.isfinite(float(record.value)), record.name


def test_every_number_is_filed_under_a_series_the_file_declares():
    """The numbers of one subject must not be spread over several series."""
    for record in constants.MODEL_REFERENCES:
        assert record.series in constants.SERIES, record.name


def test_every_series_is_used_and_says_what_it_is():
    """A series nobody uses is a leftover, and an unnamed one is a mystery."""
    used = {record.series for record in constants.MODEL_REFERENCES}
    assert used == set(constants.SERIES)
    for series, description in constants.SERIES.items():
        assert description, series


# ---------------------------------------------------------------------------
# the cases
# ---------------------------------------------------------------------------
def test_the_case_of_a_number_is_one_the_file_declares():
    """A number has to belong to a case, and the cases are written down."""
    for record in constants.MODEL_REFERENCES:
        assert record.case in constants.CASES, record.name


def test_the_cases_of_the_file_are_the_cases_of_the_tree():
    """The three voice cases are the ones the benchmark measures.

    The case ``"*"`` is every case: the size of a model and the cost of an
    instance are properties of the product, not of the material.  The two
    granularities are the two ends of the cost arithmetic of the study.
    """
    voice = tuple(case.name for case in cases_module.VOICE_CASES)
    expected = {constants.EVERY_CASE, "audio_rate", "frame_rate"} | set(voice)
    assert set(constants.CASES) == expected
    for name in voice:
        assert name in constants.CASES, name


def test_every_voice_case_is_measured_in_every_configuration():
    """Every case of the path is measured in every configuration."""
    for case in cases_module.VOICE_CASES:
        for series in MODEL_PATH_SERIES:
            for metric in QUALITY_METRICS:
                found = [
                    record
                    for record in constants.MODEL_REFERENCES
                    if record.case == case.name
                    and record.series == series
                    and record.metric == metric
                ]
                assert len(found) == 1, (case.name, series, metric)


def test_the_latency_is_recorded_for_every_configuration():
    """The path publishes a latency in every configuration."""
    for series in MODEL_PATH_SERIES:
        found = [
            record
            for record in constants.MODEL_REFERENCES
            if record.metric == "latency_samples" and record.series == series
        ]
        assert found, series
        for record in found:
            assert float(record.value) >= 0.0, record.name


def test_the_cost_of_the_model_path_is_recorded():
    """A load, a frame and a share: the three costs of the path."""
    for metric in ("model_load_ms", "model_frame_ms", "model_file_share"):
        found = [
            record
            for record in constants.MODEL_REFERENCES
            if record.metric == metric
        ]
        assert found, metric


def test_the_two_subjects_of_the_file_do_not_share_a_series():
    """The study of the reference is not a measurement of ours."""
    for record in constants.MODEL_REFERENCES:
        if record.series == STUDY_SERIES:
            continue
        assert record.series in MODEL_PATH_SERIES, record.name


# ---------------------------------------------------------------------------
# the yardstick
# ---------------------------------------------------------------------------
def test_the_yardstick_is_the_one_of_the_project():
    """The benchmark and the performance gate are held to the same stick."""
    assert constants.REFERENCE_TRANSFORM_US == REFERENCE_TRANSFORM_US
    assert reference_module.REFERENCE_TRANSFORM_US == REFERENCE_TRANSFORM_US
    assert budgets_module.REFERENCE_TRANSFORM_US == REFERENCE_TRANSFORM_US
    assert constants.BUDGET_US_PER_BLOCK == pytest.approx(BUDGET_US_PER_BLOCK)
    assert constants.BUDGET_TRANSFORMS_PER_BLOCK == pytest.approx(
        BUDGET_TRANSFORMS_PER_BLOCK
    )
    assert constants.BUDGET_TRANSFORMS_PER_BLOCK == pytest.approx(
        constants.BUDGET_US_PER_BLOCK / constants.REFERENCE_TRANSFORM_US
    )


def test_the_derived_transform_costs_follow_the_records():
    """The file says the two are derived, so they cannot drift from the ms.

    A cost of the path is timed on the machine of the measurement, so the
    number in the table and the number the file converts have to be the same
    number: the conversion is derived from the record, not written down a
    second time.
    """
    for name, derived in (
        ("MODEL_LOAD_MS", constants.MODEL_LOAD_TRANSFORMS),
        ("MODEL_FRAME_MS", constants.MODEL_FRAME_TRANSFORMS),
    ):
        record = constants.MODEL_REFERENCE_BY_NAME[name]
        assert derived == pytest.approx(
            float(record.value) * 1000.0 / constants.REFERENCE_TRANSFORM_US,
            abs=0.01,
        ), name


def test_every_cost_states_the_yardstick_it_was_converted_into():
    """A cost that does not say how it was measured cannot be read."""
    for record in constants.MODEL_REFERENCES:
        if record.metric not in constants.COST_METRICS:
            continue
        method = record.method
        assert "8.300 us" in method, record.name
        assert "160.6" in method, record.name
        if record.metric == "model_file_share":
            # a share is a share and not a cost in time: it is not on the
            # yardstick, and the method says so.
            assert "not a cost in time" in method, record.name
            continue
        pairs = _YARDSTICK_PAIR.findall(method)
        assert pairs, record.name
        for us, transforms in pairs:
            assert float(transforms) * REFERENCE_TRANSFORM_US == pytest.approx(
                float(us), abs=0.5
            ), record.name
            assert float(transforms) == pytest.approx(
                float(record.value) * 1000.0 / constants.REFERENCE_TRANSFORM_US,
                abs=0.01,
            ), record.name


def test_every_cost_declares_what_it_is_a_cost_of():
    """A cost that does not say whether it is a block is a trap."""
    for metric in constants.COST_METRICS:
        assert metric in constants.PER_BLOCK_COST, metric
    assert set(constants.PER_BLOCK_COST) <= set(constants.COST_METRICS)
    for metric in constants.METRICS:
        if metric.unit == "ms":
            assert metric.name in constants.COST_METRICS, metric.name


def test_a_cost_that_is_not_a_per_block_cost_is_not_compared_with_the_budget():
    """The budget of the project is the cost of one 64 sample block.

    A load happens once per instance, a frame of inference is not a block and
    a share of a render is not a cost in time at all, so none of them may be
    compared with the block budget.  The file says which cost is which, and
    the gate reads that declaration instead of assuming one.
    """
    per_block = constants.PER_BLOCK_COST
    assert per_block, "the file does not say which of its costs is a block"
    for metric, is_per_block in per_block.items():
        assert metric in constants.METRIC_BY_NAME, metric
        records = [
            record
            for record in constants.MODEL_REFERENCES
            if record.metric == metric
        ]
        assert records, metric
        for record in records:
            if is_per_block:
                # a per-block cost is in the unit of the budget, and it has
                # to fit inside it
                assert record.unit == "us", record.name
                assert (
                    float(record.value) < constants.BUDGET_US_PER_BLOCK
                ), record.name
                continue
            # a cost that is not a per-block cost is not in the unit of the
            # budget, so it cannot be compared with it by accident
            assert record.unit != "us", record.name
            assert (
                "not a per-block cost" in record.method
                or "not a cost in time" in record.method
                or "cannot be derived" in record.method
            ), record.name


# ---------------------------------------------------------------------------
# the arithmetic of the study
# ---------------------------------------------------------------------------
def _study(name: str) -> float:
    """One number of the study of the reference's model library."""
    record = constants.MODEL_REFERENCE_BY_NAME[name]
    assert record.series == STUDY_SERIES, name
    return float(record.value)


def _cost_us_per_block(steps: float, mac_per_sample: float) -> float:
    """The cost of a 64 sample block, in microseconds."""
    return (
        steps
        * mac_per_sample
        / constants.REFERENCE_MODEL_MAC_PER_SECOND
        * 1e6
    )


def test_the_parameter_counts_and_the_weights_agree():
    """A count of words and a count of bytes have to be the same artefact."""
    for size in ("LARGE", "MEDIUM", "SMALL"):
        parameters = _study(f"REFERENCE_MODEL_PARAMETER_COUNT_{size}")
        byte_count = _study(f"REFERENCE_MODEL_WEIGHT_BYTES_{size}")
        assert byte_count / 4.0 == pytest.approx(parameters, abs=1.0), size


def test_the_cost_of_one_pass_is_the_number_of_the_weights():
    """One multiply-accumulate per weight per output element."""
    assert _study("REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE") == _study(
        "REFERENCE_MODEL_PARAMETER_COUNT_LARGE"
    )


def test_the_audio_rate_cost_is_derived_from_the_counted_parameters():
    """The cost at the audio rate is the count, multiplied and divided."""
    derived = _cost_us_per_block(
        constants.REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK,
        _study("REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE"),
    )
    recorded = _study("REFERENCE_MODEL_AUDIO_RATE_US_PER_BLOCK")
    assert derived == pytest.approx(recorded, rel=0.01)
    # the recorded figure is the same number, rounded to a whole millisecond
    assert derived / 1000.0 == pytest.approx(recorded / 1000.0, rel=0.01)


def test_the_frame_rate_cost_is_derived_from_the_counted_parameters():
    """The cost at the frame rate is the same count, over far fewer steps."""
    derived = {}
    for size in ("LARGE", "MEDIUM", "SMALL"):
        derived[size] = _cost_us_per_block(
            constants.REFERENCE_MODEL_FRAME_RATE_STEPS_PER_BLOCK,
            _study(f"REFERENCE_MODEL_PARAMETER_COUNT_{size}"),
        )
    # the recorded figure is the derived one, rounded to a whole microsecond
    assert round(derived["LARGE"]) == int(
        _study("REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MAX")
    )
    assert round(derived["SMALL"]) == int(
        _study("REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MIN")
    )
    assert derived["SMALL"] < derived["MEDIUM"] < derived["LARGE"]


def test_the_frame_rate_cost_is_inside_the_budget():
    """The whole point: a model that runs per frame fits the real time path."""
    budget = _study("REFERENCE_MODEL_BLOCK_BUDGET_US")
    assert _study("REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MAX") < budget
    assert _study("REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MIN") < budget
    assert constants.BUDGET_US_PER_BLOCK == pytest.approx(budget, abs=1.0)


def test_the_audio_rate_cost_is_outside_the_budget():
    """And the other point: a model that runs per sample cannot."""
    budget = _study("REFERENCE_MODEL_BLOCK_BUDGET_US")
    assert _study("REFERENCE_MODEL_AUDIO_RATE_US_PER_BLOCK") > budget
    derived = _cost_us_per_block(
        constants.REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK,
        _study("REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE"),
    )
    assert derived > budget


def test_the_audio_rate_cost_is_many_times_the_budget():
    """The note quotes 45 times the budget; the arithmetic has to agree."""
    budget = _study("REFERENCE_MODEL_BLOCK_BUDGET_US")
    derived = _cost_us_per_block(
        constants.REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK,
        _study("REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE"),
    )
    assert derived / budget == pytest.approx(45.0, abs=1.0)


def test_the_share_of_the_budget_is_the_figure_the_note_quotes():
    """Four per cent, rounded to a whole per cent."""
    derived = (
        _study("REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MAX")
        / _study("REFERENCE_MODEL_BLOCK_BUDGET_US")
        * 100.0
    )
    assert round(derived) == int(_study("REFERENCE_MODEL_BUDGET_SHARE_PERCENT"))


def test_the_library_is_several_models_in_several_generations():
    """Several models, several generations, and they share nothing."""
    count = _study("REFERENCE_MODEL_COUNT")
    generations = _study("REFERENCE_MODEL_GENERATION_COUNT")
    assert count > 1
    assert generations > 1
    assert generations < count
    assert _study("REFERENCE_MODEL_MAX_SHARED_FLOAT_WORDS") < (
        _study("REFERENCE_MODEL_PARAMETER_COUNT_SMALL") * 0.001
    )


def test_the_three_size_classes_have_three_depths():
    """Each size class is a stack of its own depth."""
    depths = (
        _study("REFERENCE_MODEL_DEPTH_SMALL"),
        _study("REFERENCE_MODEL_DEPTH_MEDIUM"),
        _study("REFERENCE_MODEL_DEPTH_LARGE"),
    )
    assert len(set(depths)) == 3
    assert list(depths) == sorted(depths)
    counts = (
        _study("REFERENCE_MODEL_PARAMETER_COUNT_SMALL"),
        _study("REFERENCE_MODEL_PARAMETER_COUNT_MEDIUM"),
        _study("REFERENCE_MODEL_PARAMETER_COUNT_LARGE"),
    )
    assert list(counts) == sorted(counts)


# ---------------------------------------------------------------------------
# the notes
# ---------------------------------------------------------------------------
def test_the_note_quotes_the_constants_the_file_carries():
    """The note and the constants file must not drift apart."""
    text = _NOTE.read_text(encoding="utf-8")
    carried = set(constants.MODEL_REFERENCE_BY_NAME)
    carried |= {
        name for name in dir(constants) if name.startswith("REFERENCE_MODEL_")
    }
    for name in sorted(set(_QUOTED_NAME.findall(text))):
        assert name in carried, name


def test_the_note_quotes_the_parameter_counts_the_file_carries():
    """The numbers of the note are the numbers of the file."""
    text = _NOTE.read_text(encoding="utf-8")
    for size in ("LARGE", "MEDIUM", "SMALL"):
        value = int(_study(f"REFERENCE_MODEL_PARAMETER_COUNT_{size}"))
        assert f"{value:,}" in text, size


def test_the_notes_quote_the_yardstick_the_file_carries():
    """The yardstick of the project is the same in the notes and here."""
    cost_note = _COST_NOTE.read_text(encoding="utf-8")
    assert "8.300" in cost_note
    assert "1333.3" in cost_note
    assert "160.6" in cost_note
    note = _NOTE.read_text(encoding="utf-8")
    assert "1333 us" in note
    assert "8 G MAC/s" in note
