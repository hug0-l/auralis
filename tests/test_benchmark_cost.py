"""
tests/test_benchmark_cost.py — the gate of the cost yardstick.

The cost row of the benchmark compares our modules with the reference
product, and the two sides have to be in the same units or the comparison is
an echo.  The yardstick of the project is fixed: one 1024 point transform
costs **8.300 us** on the reference machine, so a 64 sample block of real
time at 48 kHz (1333.3 us) may cost **160.6** transforms.

This gate asserts that:

* every cost number of the benchmark is written on that yardstick, and the
  microseconds its method claims are the microseconds its value stands for
  (within the rounding of the method);
* every cost method says which yardstick the number is in, and, where a
  conversion was applied, the number it came from and the machine it came
  from;
* no cost number of another yardstick is left in the table — the numbers the
  study wrote down in transforms of the study machine were removed, because
  the cost of a transform on that machine is not recorded in this tree and
  they cannot be converted;
* the runner prints the cost of both sides in the yardstick, and says which
  side is which;
* our own side is measured the way the reference's is: one 64 sample block,
  of the signal of the case, with the channels of the case.
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
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load(name: str, path: Path):
    """Load a module of the benchmark by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference_module = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
restoration_module = _load(
    "aur_qa_benchmark_reference_restoration", _BENCH / "reference_restoration.py"
)
model_module = _load("aur_qa_benchmark_reference_model", _BENCH / "reference_model.py")
harness_module = _load("aur_qa_benchmark_harness", _BENCH / "harness.py")
runner_module = _load("aur_qa_benchmark_runner", _BENCH / "run_benchmark.py")

#: The yardstick of the project.
REFERENCE_TRANSFORM_US = 8.300

#: The metrics that carry a cost.
COST_METRICS = ("cost_transform_eq",)

#: A number of the yardstick in the method of a cost: "306.7 us per 64 sample
#: block = 36.9466 transforms of 8.300 us".
_YARDSTICK_PAIR = re.compile(
    r"([0-9]+(?:\.[0-9]+)?) us per 64 sample block = "
    r"([0-9]+(?:\.[0-9]+)?) transforms"
)

#: A number of the yardstick that is not a per block cost: "10440.0 us, that
#: is 1257.83 transforms of 8.300 us".
_YARDSTICK_PAIR_PLAIN = re.compile(
    r"([0-9]+(?:\.[0-9]+)?) us, that is "
    r"([0-9]+(?:\.[0-9]+)?) transforms of 8\.300 us"
)

#: The numbers of the yardstick that the study wrote down in the transforms of
#: the study machine.  They cannot be converted, so they must not be here.
_REMOVED = (0.31, 0.42)


def _cost_records():
    """Every cost number of the benchmark, with the file it comes from."""
    out = []
    for reference in reference_module.REFERENCES:
        if reference.metric in COST_METRICS:
            out.append(("reference.py", reference))
    for restoration in restoration_module.RESTORATIONS:
        if restoration.metric in COST_METRICS:
            out.append(("reference_restoration.py", restoration))
    return out


def _raw_cost_records():
    """Every cost number that is kept in the unit it was measured in."""
    return [
        ("reference_restoration.py", restoration)
        for restoration in restoration_module.RESTORATIONS
        if restoration.metric == "cost_ms"
    ]


# ---------------------------------------------------------------------------
# the yardstick
# ---------------------------------------------------------------------------
def test_the_yardstick_is_the_one_of_the_project():
    """The benchmark and the performance gate are held to the same stick."""
    budgets = _load("aur_qa_performance_budgets", _ROOT / "qa" / "performance" / "budgets.py")
    assert reference_module.REFERENCE_TRANSFORM_US == REFERENCE_TRANSFORM_US
    assert reference_module.REFERENCE_TRANSFORM_US == budgets.REFERENCE_TRANSFORM_US
    assert harness_module.REFERENCE_TRANSFORM_US == REFERENCE_TRANSFORM_US
    assert reference_module.COST_BLOCK == 64
    assert harness_module.COST_BLOCK == 64
    assert reference_module.BUDGET_US_PER_BLOCK == pytest.approx(1333.3333)
    assert reference_module.BUDGET_TRANSFORMS_PER_BLOCK == pytest.approx(160.6426)
    assert reference_module.BUDGET_TRANSFORMS_PER_BLOCK == pytest.approx(
        reference_module.BUDGET_US_PER_BLOCK / REFERENCE_TRANSFORM_US
    )


def test_every_cost_number_is_on_the_yardstick():
    """A cost number that is not in transforms cannot be read against ours."""
    records = _cost_records()
    assert records, "there is no cost number in the benchmark at all"
    for origin, record in records:
        assert record.unit == "transforms", f"{origin}: {record.metric}"
        if record.value is None:
            continue
        assert float(record.value) > 0.0, f"{origin}: {record.metric}"
        assert math.isfinite(float(record.value)), f"{origin}: {record.metric}"


def test_the_method_of_every_cost_number_states_the_conversion():
    """The method says what it was measured as, and how it was converted."""
    for origin, record in _cost_records():
        method = record.method
        assert "8.300 us" in method, f"{origin}: {record.metric}"
        assert "160.6" in method, f"{origin}: {record.metric}"
        if record.value is None:
            # a number that was removed says why it could not be converted.
            assert "could not be converted" in method, f"{origin}: {record.metric}"
            continue
        pairs = _YARDSTICK_PAIR.findall(method)
        assert pairs, f"{origin}: {record.metric} states no conversion"
        for us, transforms in pairs:
            stated_us = float(us)
            stated_transforms = float(transforms)
            # the value of the number and the microseconds its method claims
            # are the same figure, within the rounding of the method.
            assert stated_transforms * REFERENCE_TRANSFORM_US == pytest.approx(
                stated_us, abs=0.5
            ), f"{origin}: {record.metric}: {us} us is not {transforms} transforms"
        # the value of the number is one of the figures the method states.
        stated = [float(transforms) for _us, transforms in pairs]
        assert any(
            float(record.value) == pytest.approx(value, abs=0.01)
            for value in stated
        ), f"{origin}: {record.metric}: the value is not one of the ones stated"


def test_a_raw_cost_number_states_the_conversion_into_the_yardstick():
    """A number kept in the unit it was measured in says how it converts.

    The study measured the reference in milliseconds per 512 frame block on
    the study machine.  That number is kept, because it is the measurement,
    but the method has to say what it is in the yardstick of the project.
    """
    records = _raw_cost_records()
    assert records, "there is no raw cost number in the benchmark at all"
    for origin, record in records:
        method = record.method
        assert "8.300 us" in method, f"{origin}: {record.name}"
        assert "160.6" in method, f"{origin}: {record.name}"
        pairs = _YARDSTICK_PAIR.findall(method)
        assert pairs, f"{origin}: {record.name} states no conversion"
        for us, transforms in pairs:
            stated_us = float(us)
            stated_transforms = float(transforms)
            assert stated_transforms * REFERENCE_TRANSFORM_US == pytest.approx(
                stated_us, abs=0.5
            ), f"{origin}: {record.name}: {us} us is not {transforms} transforms"
            # the conversion is the one the raw number asks for: a 512 frame
            # block is eight 64 sample blocks.
            derived = float(record.value) * 1000.0 * 64.0 / 512.0
            assert derived == pytest.approx(stated_us, abs=0.5), (
                f"{origin}: {record.name}: {record.value} ms per 512 frames is "
                f"{derived:.1f} us per 64 sample block, not {stated_us}"
            )
            assert derived / REFERENCE_TRANSFORM_US == pytest.approx(
                stated_transforms, abs=0.01
            ), f"{origin}: {record.name}: the stated transforms are not the ones"


def test_the_model_cost_numbers_say_which_yardstick_they_are_in():
    """The model path has a load, a frame and a share, and says so."""
    by_name = {
        reference.name: reference for reference in model_module.MODEL_REFERENCES
    }
    assert "MODEL_LOAD_MS" in by_name
    assert "MODEL_FRAME_MS" in by_name
    assert "MODEL_FILE_SHARE" in by_name

    for name in ("MODEL_LOAD_MS", "MODEL_FRAME_MS"):
        record = by_name[name]
        assert "8.300 us" in record.method, name
        assert "160.6" in record.method, name
        pairs = _YARDSTICK_PAIR_PLAIN.findall(record.method)
        assert pairs, f"{name} states no conversion"
        for us, transforms in pairs:
            assert float(transforms) * REFERENCE_TRANSFORM_US == pytest.approx(
                float(us), abs=0.5
            ), f"{name}: {us} us is not {transforms} transforms"
            assert float(transforms) == pytest.approx(
                float(record.value) * 1000.0 / REFERENCE_TRANSFORM_US, abs=0.01
            ), f"{name}: the stated transforms are not the ones"

    # the share is a share and not a cost in time: it is not on the yardstick
    # and it says so.
    share = by_name["MODEL_FILE_SHARE"]
    assert "not a cost in time" in share.method
    assert "8.300 us" in share.method


def test_a_cost_number_of_another_yardstick_is_not_in_the_table():
    """The numbers of the study machine cannot be converted, so they are gone."""
    for origin, record in _cost_records():
        if record.value is None:
            continue
        for gone in _REMOVED:
            assert float(record.value) != pytest.approx(gone, abs=1e-9), (
                f"{origin}: {record.metric} still holds {gone}, a number of the "
                "transforms of the study machine"
            )


def test_the_removed_numbers_are_recorded_as_unknown():
    """A number that was removed is marked, not silently dropped."""
    unknown = [
        record
        for record in reference_module.REFERENCES
        if record.metric == "cost_transform_eq" and record.value is None
    ]
    assert unknown, "the numbers that were removed are not marked as unknown"
    for record in unknown:
        assert "could not be converted" in record.method, record


def test_the_reference_of_a_cost_is_the_cheapest_one():
    """The benchmark is held to the strongest reference number."""
    for case in ("hiss", "corpus_voice"):
        target = reference_module.reference_for(case, "cost_transform_eq")
        assert target is not None
        values = [
            float(reference.value)
            for reference in reference_module.references_for(case, "cost_transform_eq")
            if reference.value is not None
        ]
        assert target == min(values)


# ---------------------------------------------------------------------------
# our own side
# ---------------------------------------------------------------------------
def test_our_side_is_measured_in_blocks_of_64():
    """The reference's cost is the cost of one 64 sample block, so ours is."""
    cases_module = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
    case = cases_module.CASE_BY_NAME["hum50"]
    numbers = harness_module.measure_case(case)
    assert numbers["cost_block"] == 64
    assert numbers["cost_transform_eq"] > 0.0
    assert numbers["cost_us_per_block"] == pytest.approx(
        numbers["cost_transform_eq"] * REFERENCE_TRANSFORM_US
    )
    assert numbers["cost_blocks"] > 0
    assert numbers["cost_channels"] >= 1


def test_our_side_is_the_cost_of_one_block_and_not_an_amortised_one():
    """A whole clip render hides the cost a module pays in every block."""
    cases_module = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
    case = cases_module.CASE_BY_NAME["hum50"]
    numbers = harness_module.measure_case(case)
    assert "cost_transform_eq_amortised" in numbers
    # the block wise figure is the one that is compared with the reference,
    # and it is the one the budget of a block is measured against.
    assert numbers["cost_transform_eq"] * REFERENCE_TRANSFORM_US < (
        harness_module.BUDGET_US_PER_BLOCK
    )


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
def test_the_runner_prints_the_cost_of_both_sides_in_the_yardstick(capsys):
    """The cost row has to say which side is which, and in what unit."""
    code = runner_module.main(["--case", "hum50"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "one yardstick" in output
    assert "ours is counted" in output
    assert "the reference's is timed on the study machine" in output
    assert "ours trf" in output
    assert "ref us" in output


def test_the_runner_prints_the_reference_cost_of_the_yardstick(capsys):
    """The number the runner prints is the number the constants carry."""
    code = runner_module.main(["--case", "hum50"])
    output = capsys.readouterr().out
    assert code == 0, output
    target = reference_module.reference_for("hum50", "cost_transform_eq")
    assert target is not None
    assert ("%.2f" % target) in output, output
    assert ("%.1f" % (target * REFERENCE_TRANSFORM_US)) in output, output
