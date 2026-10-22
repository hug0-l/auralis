"""
tests/test_model_budget.py — the cost gate of the shipped models.

A model is an **offline** capability: the hand written modules are exact,
deterministic and cheaper, so they stay the default in the real time path.
That is a policy of the product and not a technical limit — the
measurement says that every model we ship would fit into the real time
budget as well.  This test is what keeps the policy a choice and not a
necessity: a model that grows past the budget fails here.

**The budget.**  A 64 sample block at 48 kHz has to be processed in the
time it takes to play it, i.e. ``64 / 48000`` seconds, which is 1333.3 us.
The yardstick is the **transform equivalent**: how many 1024 point
transforms a block may cost.  1333.3 us is 160.6 transforms of the
reference machine, and the reference is a **fixed** number of the project
(8.300 us per transform) and not a number that moves with the machine
that runs the test.

**The measurement.**  The machine is shared, so a wall-clock number is not
a measurement.  Every run is timed next to a batch of 1024 point
transforms of the same machine, and the cost is expressed in transform
equivalents; the fastest of N rounds is the observation that was least
disturbed by other work.  The recorded numbers live in
``qa/performance/model_budgets.py``, are written by
``qa/performance/make_model_budgets.py`` from a sweep
(``qa/performance/sweep_models.py``) and are committed: a budget that were
regenerated on every test run would always equal the measurement and
would never catch anything.

**The table follows the library.**  A model that is added without being
measured has no budget, and a budget that names a model the library does
not hold is a budget of nothing.  Both fail.

To prove that the gate bites, run it with a fault injected::

    AURALIS_MODEL_FAULT=voice_enhance:20.0 python -m pytest tests/test_model_budget.py -q

which multiplies the measured cost of that model and has to fail the
budget test.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

# The table and the sweep live outside the test package, so they are put
# on the path and loaded under names of their own: several directories of
# the project hold modules with the same name, and a full test run would
# otherwise put the wrong one in the cache.
_PERF_DIR = Path(__file__).resolve().parents[1] / "qa" / "performance"
if str(_PERF_DIR) not in sys.path:
    sys.path.insert(0, str(_PERF_DIR))


def _load(name: str, path: Path) -> ModuleType:
    """Load a module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budgets = _load("auralis_model_budgets", _PERF_DIR / "model_budgets.py")
sweep = _load("auralis_model_sweep", _PERF_DIR / "sweep_models.py")

from aurcore import models  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: The block size the budget is quoted at.
BLOCK = 64

#: The realtime budget of one block, in microseconds (1333.3 us at 48 kHz).
BUDGET_US = budgets.BUDGET_US

#: The realtime budget of one block, in transform equivalents.
BUDGET_TRANSFORMS = budgets.BUDGET_TRANSFORMS

#: The cost of one 1024 point transform on the reference machine.
REFERENCE_TRANSFORM_US = budgets.REFERENCE_TRANSFORM_US

#: The rule the product holds its models to.
RULE = budgets.MODEL_RULE

#: The budgets a model can be held to.
BUDGET_KINDS = budgets.BUDGET_KINDS

#: The offline budget: how many times the real time the offline path may
#: take.  A model that does not fit into the realtime budget of a block is
#: held to this one instead.
OFFLINE_SPEED_CEILING = budgets.OFFLINE_SPEED_CEILING

#: How many rounds the live measurement takes.  The fastest round wins.
ROUNDS = 2

#: The length of the audio one round runs a model over.  It is the length
#: the recorded numbers were taken over, so the two can be compared.
SECONDS = 10.0

#: How many transforms are timed on each side of a run.
CALIB_REPEATS = 20

#: The environment variable that injects a fault into the measurement.
#: Its value is ``NAME:FACTOR``, e.g. ``voice_enhance:20.0``: the measured
#: cost of that model is multiplied by the factor.  It exists to prove
#: that the gate fails a model that goes over its budget.
FAULT_ENV = "AURALIS_MODEL_FAULT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _library_names() -> list[str]:
    """Return the names of the readable models of the shipped library."""
    library = models.open_library()
    return [entry.name for entry in library.list() if entry.readable]


def _fault_factor(name: str) -> float:
    """Return the fault factor of a model (1.0 when there is none)."""
    raw = os.environ.get(FAULT_ENV, "")
    if not raw:
        return 1.0
    for part in raw.split(","):
        key, _, factor = part.partition(":")
        if key.strip() != name:
            continue
        try:
            return float(factor)
        except ValueError:
            return 1.0
    return 1.0


def _over_budget_message(name: str, cost_us: float) -> str:
    """Return the message that names the model and says by how much."""
    too_much = cost_us - BUDGET_US
    percent = too_much / BUDGET_US * 100.0
    return (
        f"the model {name!r} needs {cost_us:.1f} us per {BLOCK} sample block, "
        f"which is {too_much:.1f} us too much: it is {percent:.1f} % over the "
        f"budget of {BUDGET_US:.1f} us "
        f"({BUDGET_TRANSFORMS:.1f} transforms of {REFERENCE_TRANSFORM_US:.3f} us)"
    )


def _over_speed_message(name: str, factor: float, ceiling: float) -> str:
    """Return the message that names the model and says by how much."""
    return (
        f"the model {name!r} takes {factor:.2f} times the real time, which is "
        f"{factor - ceiling:.2f} times too much: the offline budget is "
        f"{ceiling:.2f} times the real time"
    )


def _reference_us(transforms: float) -> float:
    """Return a cost in transform equivalents as reference machine us."""
    return transforms * REFERENCE_TRANSFORM_US


@pytest.fixture(scope="module")
def live_costs() -> dict[str, dict]:
    """Measure every shipped model now, in transform equivalents.

    The measurement is the one of the sweep: the run of the offline path
    is timed next to a batch of transforms of the same machine, and the
    fastest of the rounds is the observation that was least disturbed.
    """
    library = models.open_library()
    measured: dict[str, dict] = {}
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        rate = int(model.manifest.input.sample_rate)
        signal = sweep.make_signal(rate, SECONDS)
        rounds = [
            sweep.time_run(model, signal, rate, CALIB_REPEATS)
            for _ in range(max(1, ROUNDS))
        ]
        best = min(rounds, key=lambda row: row["transforms"])
        measured[entry.name] = {
            "transforms": best["transforms"],
            "us_per_block": best["us_per_block"],
            "speed_factor": best.get("speed_factor", 0.0),
            "rounds": [row["transforms"] for row in rounds],
        }
    return measured


# ---------------------------------------------------------------------------
# The yardstick
# ---------------------------------------------------------------------------
def test_the_budget_is_the_realtime_budget() -> None:
    """A 64 sample block at 48 kHz has a budget of 1333.3 us."""
    assert BUDGET_US == pytest.approx(1333.333, abs=0.01)
    assert BUDGET_TRANSFORMS == pytest.approx(
        BUDGET_US / REFERENCE_TRANSFORM_US, rel=1e-6
    )


def test_the_yardstick_is_fixed_and_plausible() -> None:
    """The yardstick is a constant of the project, not of the session."""
    assert REFERENCE_TRANSFORM_US > 0.0, "the reference yardstick is missing"
    assert 1.0 <= REFERENCE_TRANSFORM_US <= 1000.0, (
        f"the reference yardstick {REFERENCE_TRANSFORM_US} us is not a plausible "
        "cost of one 1024 point transform"
    )
    # The yardstick of the session that took the measurement is kept next
    # to the table; it may differ from the reference (the machine was
    # busy), but it has to be a real measurement.
    assert budgets.SESSION_TRANSFORM_US > 0.0


def test_the_rule_is_written_down() -> None:
    """The rule of the product is stated in the table, not only in prose."""
    assert RULE == "offline", "a model belongs to the offline path"
    for name, budget in budgets.MODEL_BUDGETS.items():
        assert budget.kind == RULE, f"{name} is not held to the {RULE} rule"
        assert budget.budget in BUDGET_KINDS, (
            f"{name} is held to the budget {budget.budget!r}, which is not one of "
            f"{BUDGET_KINDS}"
        )


def test_the_offline_budget_is_written_down() -> None:
    """The offline budget is a number, not a phrase.

    A model that does not fit into the realtime budget of a block is held
    to the *speed* of the offline path: its render may take at most this
    many times the length of the audio.  The number has to be there, or
    the rule is a superstition.
    """
    assert OFFLINE_SPEED_CEILING > 0.0, "the offline budget is missing"
    assert OFFLINE_SPEED_CEILING >= 1.0, (
        "the offline budget has to allow a render that takes as long as the "
        "file plays"
    )
    for name, budget in budgets.MODEL_BUDGETS.items():
        if budget.budget != "speed":
            continue
        assert budget.speed_ceiling > 0.0, (
            f"{name} is held to the speed budget but has no ceiling"
        )
        assert budget.speed_ceiling <= OFFLINE_SPEED_CEILING, (
            f"{name} is held to {budget.speed_ceiling:.2f} times the real time, "
            f"which is more than the offline budget of {OFFLINE_SPEED_CEILING:.2f}"
        )
        assert budget.speed_factor > 0.0, f"{name} publishes no speed factor"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_every_model_in_the_library_has_a_budget() -> None:
    """Every model the library holds has a measured budget.

    A model without an entry would not be held to anything, and a model
    that was added without being measured would slip into the product
    without anybody knowing what it costs.
    """
    names = _library_names()
    assert names, "the shipped library holds no model"
    missing = [name for name in names if name not in budgets.MODEL_BUDGETS]
    assert not missing, (
        f"{len(missing)} model(s) have no budget: {', '.join(missing)}; run "
        "qa/performance/sweep_models.py and qa/performance/make_model_budgets.py"
    )


def test_every_budget_entry_names_a_model_that_exists() -> None:
    """Every entry of the table names a model the library holds.

    A budget of a model that is not there is a budget of nothing, and it
    hides the fact that the library and the table have drifted apart.
    """
    names = set(_library_names())
    unknown = [
        name for name in budgets.MODEL_BUDGETS if name not in names
    ]
    assert not unknown, (
        f"{len(unknown)} budget(s) name a model the library does not hold: "
        f"{', '.join(unknown)}; the library holds {', '.join(sorted(names))}"
    )


@pytest.mark.parametrize("name", sorted(budgets.MODEL_BUDGETS))
def test_the_recorded_cost_of_a_model_fits_inside_the_budget(name: str) -> None:
    """The recorded cost of every model fits inside the budget it is held to.

    The recorded cost is the one of the sweep: the least disturbed
    observation of the session that took it, in transform equivalents of
    the reference machine.  A model that is held to the **offline budget**
    is measured by the speed of the offline path instead: how many times
    the real time its render took.
    """
    budget = budgets.MODEL_BUDGETS[name]
    factor = _fault_factor(name)
    if budget.budget == "speed":
        speed = budget.speed_factor * factor
        assert speed <= budget.speed_ceiling, _over_speed_message(
            name, speed, budget.speed_ceiling
        )
        return
    cost = budget.measured_us * factor
    assert cost <= budget.budget_us, _over_budget_message(name, cost)


@pytest.mark.parametrize("name", sorted(budgets.MODEL_BUDGETS))
def test_the_live_cost_of_a_model_fits_inside_the_budget(
    name: str, live_costs: dict[str, dict]
) -> None:
    """A model that is measured now still fits inside its budget.

    This is the check that catches a bigger model: the recorded number
    says what the model cost when it was measured, and this says what it
    costs now.
    """
    budget = budgets.MODEL_BUDGETS[name]
    row = live_costs.get(name)
    assert row is not None, f"{name} could not be measured"
    factor = _fault_factor(name)
    if budget.budget == "speed":
        speed = row.get("speed_factor", 0.0) * factor
        assert speed <= budget.speed_ceiling, _over_speed_message(
            name, speed, budget.speed_ceiling
        )
        return
    cost = _reference_us(row["transforms"]) * factor
    assert cost <= budget.budget_us, _over_budget_message(name, cost)


@pytest.mark.parametrize("name", sorted(budgets.MODEL_BUDGETS))
def test_the_live_cost_does_not_run_away_from_the_recorded_one(
    name: str, live_costs: dict[str, dict]
) -> None:
    """A model may not get much more expensive than it was.

    The machine is shared and the calibration of a session resolves the
    load to about a factor of two, so the live measurement may sit above
    the recorded one.  Beyond the margin of the table it is a model that
    grew, and a model that grew is a model that has to be measured
    again.
    """
    budget = budgets.MODEL_BUDGETS[name]
    row = live_costs.get(name)
    assert row is not None, f"{name} could not be measured"
    if budget.budget == "speed":
        recorded = budget.speed_factor
        assert recorded > 0.0, f"{name} has no recorded speed factor"
        ceiling = recorded * (1.0 + budgets.LIVE_MARGIN)
        live = row.get("speed_factor", 0.0)
        assert live <= ceiling, (
            f"the model {name!r} now takes {live:.2f} times the real time, "
            f"the recorded cost is {recorded:.2f} and the ceiling is "
            f"{ceiling:.2f}; the rounds were "
            f"{', '.join(f'{value:.1f}' for value in row['rounds'])}"
        )
        return
    recorded = budget.measured.get(BLOCK, 0.0)
    assert recorded > 0.0, f"{name} has no recorded cost"
    ceiling = recorded * (1.0 + budgets.LIVE_MARGIN)
    assert row["transforms"] <= ceiling, (
        f"the model {name!r} now costs {row['transforms']:.1f} transforms "
        f"({_reference_us(row['transforms']):.0f} us) per {BLOCK} sample block, "
        f"the recorded cost is {recorded:.1f} transforms "
        f"({budget.measured_us:.0f} us) and the ceiling is {ceiling:.1f} "
        f"transforms; the rounds were "
        f"{', '.join(f'{value:.1f}' for value in row['rounds'])}"
    )


def test_the_models_are_small() -> None:
    """The models that ship are small.

    A model of a few thousand parameters is a few tens of kilobytes on
    disk.  The rule of the product — a model is an offline capability —
    is a statement about the path, not about the size, but a model that
    grew by an order of magnitude would have to be measured again.
    """
    for name, budget in budgets.MODEL_BUDGETS.items():
        assert budget.parameters > 0, f"{name} publishes no parameters"
        assert budget.parameters < 100_000, (
            f"{name} holds {budget.parameters:,d} parameters, which is no "
            "longer a small model; measure it again and write the rule down "
            "again"
        )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def test_the_headroom_report(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the headroom of every model and the rule that holds it.

    The test always passes: it is a report.  Run it with ``pytest -s`` to
    see the table.
    """
    lines: list[str] = []
    lines.append(
        f"budget: {BUDGET_US:.1f} us per {BLOCK} sample block = "
        f"{BUDGET_TRANSFORMS:.1f} transforms of {REFERENCE_TRANSFORM_US:.3f} us"
    )
    lines.append(f"the rule: a model is an {RULE} capability (a policy)")
    lines.append(
        f"the budgets: a model is held to the realtime budget of a block, or to "
        f"the offline budget of {OFFLINE_SPEED_CEILING:.2f} times the real time"
    )
    lines.append("")
    lines.append(
        f"{'model':<22}{'budget':>8}{'trf':>9}{'ref us':>9}"
        f"{'% of budget':>13}{'x real time':>13}"
    )
    for name in sorted(budgets.MODEL_BUDGETS):
        budget = budgets.MODEL_BUDGETS[name]
        lines.append(
            f"{name:<22}{budget.budget:>8}{budget.measured.get(BLOCK, 0.0):>9.1f}"
            f"{budget.measured_us:>9.1f}{budget.share:>12.1f}%"
            f"{budget.speed_factor:>13.3f}"
        )
    lines.append("")
    block_kinds = [b for b in budgets.MODEL_BUDGETS.values() if b.budget == "block"]
    if block_kinds:
        lines.append(
            "the models held to the realtime budget use between "
            f"{min(b.share for b in block_kinds):.1f} and "
            f"{max(b.share for b in block_kinds):.1f} per cent of it"
        )
    speed_kinds = [b for b in budgets.MODEL_BUDGETS.values() if b.budget == "speed"]
    if speed_kinds:
        lines.append(
            "the models held to the offline budget take between "
            f"{min(b.speed_factor for b in speed_kinds):.2f} and "
            f"{max(b.speed_factor for b in speed_kinds):.2f} times the real time, "
            f"of a ceiling of {OFFLINE_SPEED_CEILING:.2f}"
        )
    text = "\n".join(lines)
    with capsys.disabled():
        print(text)
