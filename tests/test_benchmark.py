"""
tests/test_benchmark.py — the gate of the benchmark.

The benchmark is the instrument that keeps the gap closed: it measures our
modules on the cases of the corpus and puts the numbers next to the reference
numbers the study recorded.

Two things are asserted here:

* the **references** are well formed — every number names its case, its
  metric, its unit and the way it was measured, and every number belongs to a
  series the benchmark knows;
* the **benchmark** is deterministic and it *reports* a module that is behind
  a reference instead of failing on it.  A reference is a target, not a
  contract: it was measured on somebody else's build on somebody else's
  machine, so a hard failure would make the gate a hostage to a machine we
  do not control.
"""
from __future__ import annotations

import importlib.util
import math
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


cases_module = _load("aur_qa_benchmark_cases", _BENCH / "cases.py")
reference_module = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
harness_module = _load("aur_qa_benchmark_harness", _BENCH / "harness.py")
runner_module = _load("aur_qa_benchmark_runner", _BENCH / "run_benchmark.py")

CASES = cases_module.CASES
REFERENCES = reference_module.REFERENCES
METRICS = reference_module.METRICS
SERIES = reference_module.SERIES


# ---------------------------------------------------------------------------
# the references
# ---------------------------------------------------------------------------
def test_every_reference_names_its_case_its_unit_and_its_method():
    """A number without its case, its unit and its method cannot be read."""
    assert REFERENCES, "there is no reference number at all"
    names = {case.name for case in CASES}
    for reference in REFERENCES:
        assert reference.case in names or reference.case == "*", (
            f"{reference.series}/{reference.metric} names a case that the "
            f"benchmark does not measure: {reference.case}"
        )
        assert reference.metric in reference_module.METRIC_BY_NAME, reference.metric
        assert reference.unit, reference
        assert reference.method, reference
        assert reference.series in SERIES, reference.series
        if reference.value is not None:
            assert math.isfinite(float(reference.value)), reference
            assert reference.unit == reference_module.METRIC_BY_NAME[
                reference.metric
            ].unit, reference


def test_every_case_has_a_reference():
    """A case without a reference is a case the benchmark cannot judge."""
    for case in CASES:
        found = [
            reference for reference in REFERENCES if reference.case == case.name
        ]
        assert found, f"the case {case.name} has no reference number"


def test_every_case_names_a_module_of_the_registry():
    """The case has to name a module that exists, or it measures nothing."""
    from aurcore import registry

    for case in CASES:
        assert registry.has(case.module), f"{case.name} names {case.module}"
        assert case.reason, f"{case.name} does not say why it is here"
        assert case.kind in {"corpus", "voice"}, case.kind


def test_the_reference_of_a_case_is_the_strongest_one():
    """With two series, the benchmark is held to the better number."""
    for case in CASES:
        for metric in reference_module.METRIC_BY_NAME:
            values = [
                float(reference.value)
                for reference in reference_module.references_for(case.name, metric)
                if reference.value is not None
            ]
            if not values:
                continue
            rule = reference_module.metric_for(metric)
            expected = max(values) if rule.higher_is_better else min(values)
            assert reference_module.reference_for(case.name, metric) == expected


# ---------------------------------------------------------------------------
# the instrument
# ---------------------------------------------------------------------------
def test_the_measurement_is_deterministic():
    """Two runs of the same case must give the same numbers.

    The case is read from the corpus, the analysis geometry is a constant and
    the cost is counted rather than timed, so nothing in the measurement
    moves with the load of the machine.
    """
    case = cases_module.CASE_BY_NAME["hum50"]
    first = harness_module.measure_case(case)
    second = harness_module.measure_case(case)
    assert first["removed_db"] == second["removed_db"]
    assert first["kept_db"] == second["kept_db"]
    assert first["gain_var_db2"] == second["gain_var_db2"]
    assert first["latency_samples"] == second["latency_samples"]
    assert first["cost_transform_eq"] == second["cost_transform_eq"]


def test_the_measurement_gives_finite_numbers():
    """A number that is not finite cannot be compared with anything."""
    for name in ("hum50", "rumble"):
        numbers = harness_module.measure_case(cases_module.CASE_BY_NAME[name])
        for key, value in numbers.items():
            if key == "shifts_db":
                for shift in value.values():
                    assert math.isfinite(shift), f"{name}: {key}"
                continue
            if isinstance(value, (int, float)):
                assert math.isfinite(float(value)), f"{name}: {key}"
        assert numbers["output_finite"], name


def test_the_cost_is_counted_and_not_timed():
    """The cost of a module is a count of transforms, so it cannot drift."""
    case = cases_module.CASE_BY_NAME["hum50"]
    numbers = harness_module.measure_case(case)
    assert numbers["cost_transform_eq"] > 0.0
    assert numbers["cost_transform_eq_total"] >= numbers["cost_transform_eq"]


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_a_module_behind_a_reference_is_reported_and_not_failed(monkeypatch, capsys):
    """The reference is a target, not a contract.

    The test moves one reference number out of reach, runs the gate, and
    checks two things: the row says the module is behind, and the gate still
    passes.  A hard failure here would make the whole gate depend on a
    number measured on a machine we do not control.
    """
    reference = reference_module.REFERENCES[0]
    moved = reference_module.Reference(
        series=reference.series,
        case=reference.case,
        metric=reference.metric,
        value=(reference.value + 1000.0) if reference.value is not None else 1000.0,
        unit=reference.unit,
        method=reference.method,
    )
    monkeypatch.setattr(reference_module, "REFERENCES", (moved,))
    monkeypatch.setattr(harness_module.reference, "REFERENCES", (moved,))

    code = runner_module.main([])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "behind" in output


def test_the_table_shows_our_number_the_reference_and_the_difference(capsys):
    """The table is the point of the gate: three numbers and a verdict."""
    rows = harness_module.collect([cases_module.CASE_BY_NAME["hum50"]])
    table = harness_module.format_table(rows)
    assert "ours" in table
    assert "reference" in table
    assert "gap" in table
    assert "hum50" in table
    assert "AUR_DEHUM" in table
    for row in rows:
        assert row.verdict in {"ahead", "behind", "no reference", "error"}


def test_the_gate_runs_and_prints_the_table(capsys):
    """The runner of the gate must work and must not fail."""
    code = runner_module.main(["--case", "hum50"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "the benchmark" in output
    assert "hum50" in output


@pytest.mark.parametrize("name", sorted(case.name for case in CASES))
def test_every_case_can_be_measured(name: str):
    """A case that cannot be measured is a case the benchmark cannot judge."""
    case = cases_module.CASE_BY_NAME[name]
    numbers = harness_module.measure_case(case)
    assert numbers["output_finite"], name
    assert math.isfinite(numbers["removed_db"]), name
    assert numbers["latency_samples"] >= 0, name
