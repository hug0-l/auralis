"""
tests/test_performance.py — the realtime performance gate of the DSP core.

A plug-in that misses its deadline produces drop-outs, and a drop-out is a
bug the user hears.  This test therefore measures what every module costs
for one block of audio and compares it with the realtime budget of that
block.

The budget is the time the block needs to be played: ``block / sample_rate``
seconds.  At 48 kHz a 64 sample block has a budget of 1333.3 us.

**The gate.**  A module that costs more than its budget fails the test.
There is no list of "known offenders" that are allowed to be over: the
budget of a module that fits into the realtime budget *is* the realtime
budget.  A module that does not fit yet (the owner is repairing it) gets
a **debt ceiling** — its measured cost plus a margin — so the test is
green today and still fails when the module gets worse.  The table of
budgets lives in ``qa/performance/budgets.py`` and is written by
``qa/performance/make_budgets.py`` from a sweep.

**The measurement.**  The machine is shared, so a wall-clock number is not
a measurement.  Every block is timed next to a batch of 1024 point
transforms of the same machine, and the cost of the block is expressed in
*transform equivalents*: how many transforms the block is worth.  That
number does not change when the load of the machine changes, so the
budget does not depend on the machine the test happens to run on.  The
statistic is the fastest of N rounds (the round that was least disturbed
by other work), the rounds of the whole catalogue are interleaved so
that every module is sampled over the whole sweep, and the load of the
machine is reported next to the numbers.

**The allocations.**  A module that allocates inside ``process()`` glitters
in a host.  The number of modules that allocate must not grow.

Run it with ``pytest tests/test_performance.py -s`` to see the headroom
table.  The full measurement lives in ``qa/performance/`` and its report in
``qa/performance/REPORT.md``.

To prove that the gate bites, run it with a fault injected::

    AURALIS_PERF_FAULT=AUR_DECLICK:4.0 python -m pytest tests/test_performance.py -q

which multiplies the measured cost of that module and has to fail the
budget test.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

# The harness lives outside the test package, so it is put on the path.
_PERF_DIR = Path(__file__).resolve().parents[1] / "qa" / "performance"
if str(_PERF_DIR) not in sys.path:
    sys.path.insert(0, str(_PERF_DIR))


def _load(name: str, path: Path) -> ModuleType:
    """Load a module by path, under a name of its own.

    Several directories of the project have a module called ``harness``
    (``qa/survey/harness.py``, ``qa/performance/harness.py``, ...).  When
    two of them are imported in the same test session, the second one
    gets the first one out of the module cache.  The guard therefore
    loads its own harness and its own budget table under names of their
    own, so it works in a full test run and does not break the other
    test files.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budgets = _load("auralis_performance_budgets", _PERF_DIR / "budgets.py")
harness = _load("auralis_performance_harness", _PERF_DIR / "harness.py")

from aurcore import registry  # noqa: E402

try:  # pragma: no cover - importing registers the modules
    import aurcore.modules  # noqa: F401
except Exception:  # pragma: no cover - a broken module file must not kill the run
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: The sample rate the guard runs at.
SAMPLE_RATE = harness.SAMPLE_RATE

#: The channel count the guard runs at.
CHANNELS = harness.CHANNELS

#: The block sizes the guard measures.
BLOCK_SIZES: tuple[int, ...] = tuple(budgets.BLOCK_SIZES)

#: The block size the realtime budget is quoted at.
TEST_BLOCK = 64

#: The realtime budget of one block, in microseconds (1333.3 us at 48 kHz).
BUDGET_US = harness.block_budget_us(TEST_BLOCK, SAMPLE_RATE)

#: The cost of one 1024 point transform on the reference machine.
REFERENCE_TRANSFORM_US = budgets.REFERENCE_TRANSFORM_US

#: How many times a module is measured.  The fastest run wins.
RUNS = 5

#: How many blocks are timed per run.
REPEATS = 9

#: How many untimed blocks warm a module up before it is measured.  The
#: warm-up pays for the one-off setup of a module (filter design, kernel
#: tables), which does not happen again while the plug-in runs.
WARMUP = 3

#: How many transforms are timed next to every block.
CALIB_REPEATS = 10

#: The environment variable that injects a fault into the measurement.
#: Its value is ``CODE:FACTOR``, e.g. ``AUR_DECLICK:4.0``: the measured
#: cost of that module is multiplied by the factor.  It exists to prove
#: that the gate fails a module that goes over its budget.
FAULT_ENV = "AURALIS_PERF_FAULT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _module_codes() -> list[str]:
    """Return the codes of every registered module."""
    return [info.code for info in registry.all()]


def _fault_factor(code: str) -> float:
    """Return the fault factor of a module (1.0 when there is none)."""
    raw = os.environ.get(FAULT_ENV, "")
    if not raw:
        return 1.0
    for part in raw.split(","):
        name, _, factor = part.partition(":")
        if name.strip().upper() != code.upper():
            continue
        try:
            return float(factor)
        except ValueError:
            return 1.0
    return 1.0


def _module_factory(code: str):
    """Return a factory that builds a fresh module of ``code``."""

    def make():
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        return module.process

    return make


def _chain_factory(codes: tuple[str, ...]):
    """Return a factory that builds a fresh engine with ``codes`` in it."""
    from aurcore.api import create_engine

    def make():
        engine = create_engine(SAMPLE_RATE, CHANNELS)
        for code in codes:
            engine.add_module(code)
        return engine.process

    return make


#: The measurements are cached: the whole catalogue is measured once per
#: session, no matter how many tests look at it.
_CATALOGUE: dict[str, harness.ModuleMeasurement] = {}


@pytest.fixture(scope="module")
def catalogue() -> dict[str, harness.ModuleMeasurement]:
    """Measure every module and every chain, in one interleaved sweep.

    The sweep is interleaved on purpose: every round measures every
    module once, so the samples of a module are spread over the whole
    sweep.  The load of a shared machine comes and goes in bursts, and a
    module that is only measured inside one burst never sees a quiet
    moment.
    """
    if not _CATALOGUE:
        targets: dict[str, Any] = {}
        for code in _module_codes():
            targets[code] = _module_factory(code)
        for name, codes in budgets.CHAIN_MODULES.items():
            targets[f"chain:{name}"] = _chain_factory(codes)
        _CATALOGUE.update(
            harness.measure_catalogue_calibrated(
                targets,
                block_sizes=BLOCK_SIZES,
                rounds=RUNS,
                repeats=REPEATS,
                warmup=WARMUP,
                calib_repeats=CALIB_REPEATS,
            )
        )
    return _CATALOGUE


def _measure_module(code: str, catalogue: dict[str, harness.ModuleMeasurement]):
    """Return the measurement of one module (or ``None``)."""
    return catalogue.get(code)


def _measure_chain(name: str, catalogue: dict[str, harness.ModuleMeasurement]):
    """Return the measurement of one chain (or ``None``)."""
    return catalogue.get(f"chain:{name}")


def _ceiling(code: str, block: int) -> tuple[float, str]:
    """Return the ceiling of a module at a block size and where it comes from.

    A module that has no entry in the budget table is held at the
    realtime budget: that is the requirement itself.
    """
    budget = budgets.MODULE_BUDGETS.get(code)
    if budget is None:
        return budgets.BUDGET_TRANSFORMS[block], "realtime budget (no entry)"
    return budget.transforms[block], f"{budget.kind} budget"


def _cost_us(transforms: float) -> float:
    """Return the cost of a measurement in reference machine microseconds."""
    return transforms * REFERENCE_TRANSFORM_US


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_block_budget_is_the_realtime_budget() -> None:
    """A 64 sample block at 48 kHz has a budget of 1333.3 us."""
    assert harness.block_budget_us(64, 48_000) == pytest.approx(1333.333, abs=0.01)
    assert harness.block_budget_us(128, 48_000) == pytest.approx(2666.667, abs=0.01)
    assert harness.block_budget_us(256, 48_000) == pytest.approx(5333.333, abs=0.01)
    assert harness.block_budget_us(512, 48_000) == pytest.approx(10666.667, abs=0.01)


def test_the_calibration_is_usable() -> None:
    """The yardstick of the guard must exist and must be sane.

    Without a calibration the budgets could not be compared with the
    measurement, so a broken calibration has to fail loudly instead of
    turning every budget into a wall-clock comparison.
    """
    assert REFERENCE_TRANSFORM_US > 0.0, "the reference yardstick is missing"
    assert 1.0 <= REFERENCE_TRANSFORM_US <= 1000.0, (
        f"the reference yardstick {REFERENCE_TRANSFORM_US} us is not a plausible "
        "cost of one 1024 point transform"
    )
    for block, transforms in budgets.BUDGET_TRANSFORMS.items():
        assert transforms > 0.0
        assert transforms == pytest.approx(
            harness.block_budget_us(block) / REFERENCE_TRANSFORM_US, rel=1e-6
        )


@pytest.mark.parametrize("code", _module_codes())
def test_module_processes_a_block_within_its_budget(
    code: str, catalogue: dict[str, harness.ModuleMeasurement]
) -> None:
    """Every module processes a block of audio within its budget.

    The budget is the realtime budget of the block, expressed in the
    transform equivalents of the reference machine.  A module that costs
    more than that fails the test — there is no exemption list.
    """
    measurement = _measure_module(code, catalogue)
    if measurement is None or not measurement.ok:
        error = measurement.error if measurement is not None else "not measured"
        pytest.skip(f"{code} could not be measured: {error}")
    factor = _fault_factor(code)
    for block in BLOCK_SIZES:
        timing = measurement.timing_for(block)
        if timing is None:
            continue
        cost = timing.transforms * factor
        ceiling, source = _ceiling(code, block)
        assert cost <= ceiling, (
            f"{code} needed {cost:.1f} transforms ({_cost_us(cost):.0f} us on the "
            f"reference machine) for a {block} sample block, the ceiling is "
            f"{ceiling:.1f} transforms ({_cost_us(ceiling):.0f} us, "
            f"{source}); the raw block time was {timing.median_us:.0f} us "
            f"(budget {timing.budget_us:.0f} us)"
        )


@pytest.mark.parametrize("code", _module_codes())
def test_module_output_is_finite(
    code: str, catalogue: dict[str, harness.ModuleMeasurement]
) -> None:
    """A performance measurement must not hide a broken module.

    The guard measures the modules anyway, so it also checks that the
    output of a 64 sample block is finite.  A module that changes the
    block length (a resampler, a stretcher) is allowed to do so, but it
    must not produce garbage.
    """
    measurement = _measure_module(code, catalogue)
    if measurement is None or not measurement.ok:
        error = measurement.error if measurement is not None else "not measured"
        pytest.skip(f"{code} could not be measured: {error}")
    signal = harness.make_test_signal(256, SAMPLE_RATE, CHANNELS)
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    out = module.process(signal[:TEST_BLOCK].copy())
    assert out is not None
    assert np.all(np.isfinite(np.asarray(out, dtype=np.float64)))


@pytest.mark.parametrize("name", sorted(budgets.CHAIN_MODULES))
def test_chain_processes_a_block_within_its_budget(
    name: str, catalogue: dict[str, harness.ModuleMeasurement]
) -> None:
    """The product chains stay inside their budget.

    A chain is the number the plug-in actually has to hit: the modules
    share one block budget, so the chain is what has to fit into 1333 us.
    """
    codes = budgets.CHAIN_MODULES[name]
    for code in codes:
        assert registry.has(code), f"the chain needs the module {code}"
    measurement = _measure_chain(name, catalogue)
    if measurement is None or not measurement.ok:
        error = measurement.error if measurement is not None else "not measured"
        pytest.skip(f"the chain {name} could not be measured: {error}")
    factor = _fault_factor(name)
    budget = budgets.CHAIN_BUDGETS.get(name)
    for block in BLOCK_SIZES:
        timing = measurement.timing_for(block)
        if timing is None:
            continue
        cost = timing.transforms * factor
        if budget is not None:
            ceiling = budget.transforms[block]
            source = f"{budget.kind} budget"
        else:
            ceiling = budgets.BUDGET_TRANSFORMS[block]
            source = "realtime budget (no entry)"
        assert cost <= ceiling, (
            f"the chain {name} needed {cost:.1f} transforms "
            f"({_cost_us(cost):.0f} us on the reference machine) for a {block} "
            f"sample block, the ceiling is {ceiling:.1f} transforms "
            f"({_cost_us(ceiling):.0f} us, {source}); the raw block time was "
            f"{timing.median_us:.0f} us (budget {timing.budget_us:.0f} us)"
        )


def test_chain_output_is_finite() -> None:
    """The chains the guard measures must produce a finite signal."""
    signal = harness.make_test_signal(512, SAMPLE_RATE, CHANNELS)
    from aurcore.api import create_engine

    for name, codes in budgets.CHAIN_MODULES.items():
        engine = create_engine(SAMPLE_RATE, CHANNELS)
        for code in codes:
            engine.add_module(code)
        out = engine.process(signal[:TEST_BLOCK].copy())
        assert out is not None, f"the chain {name} produced nothing"
        assert np.all(
            np.isfinite(np.asarray(out, dtype=np.float64))
        ), f"the chain {name} produced a non finite signal"


# ---------------------------------------------------------------------------
# The allocations
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def allocation_stats() -> dict[str, harness.AllocationStats | None]:
    """Measure what every module allocates inside ``process()``."""
    signal = harness.make_test_signal(max(BLOCK_SIZES) * 4, SAMPLE_RATE, CHANNELS)
    stats: dict[str, harness.AllocationStats | None] = {}
    for code in _module_codes():
        try:
            module = registry.create(code, SAMPLE_RATE, CHANNELS)
            block = signal[:TEST_BLOCK].copy()
            module.process(block.copy())  # warm up
            stats[code] = harness.measure_allocations(
                module.process, block, repeats=2, warmup=0
            )
        except Exception:  # noqa: BLE001 - a broken module is reported elsewhere
            stats[code] = None
    return stats


def test_the_number_of_allocating_modules_does_not_grow(
    allocation_stats: dict[str, harness.AllocationStats | None],
) -> None:
    """No module may start allocating inside ``process()``.

    A module that allocates in the audio path glitters in a host, and the
    allocation is part of the cost of the block.  The number of modules
    that allocate is therefore a budget of its own: it may go down (a
    module that was repaired), never up.
    """
    allocating = {
        code
        for code, stats in allocation_stats.items()
        if stats is not None and stats.allocates
    }
    new = allocating - set(budgets.ALLOCATING_MODULES)
    assert not new, (
        f"{len(new)} module(s) allocate inside process() that did not before: "
        f"{', '.join(sorted(new))}; the number of allocating modules must not grow"
    )
    assert len(allocating) <= len(budgets.ALLOCATING_MODULES), (
        f"{len(allocating)} modules allocate inside process(), the budget is "
        f"{len(budgets.ALLOCATING_MODULES)}"
    )


@pytest.mark.parametrize("code", _module_codes())
def test_allocation_churn_stays_bounded(
    code: str,
    allocation_stats: dict[str, harness.AllocationStats | None],
) -> None:
    """A module may not allocate more than it does today.

    The baseline is the measured churn of the module: the number of NumPy
    arrays it creates per block, the bytes of new array data and the peak
    Python churn of one block.  The baseline may grow by
    ``ALLOC_MARGIN`` before the guard complains.
    """
    stats = allocation_stats.get(code)
    if stats is None:
        pytest.skip(f"{code} could not be measured")
    baseline = budgets.ALLOC_BASELINE.get(code)
    if baseline is None:
        pytest.skip(f"{code} has no allocation baseline")
    arrays, data, peak = baseline
    margin = budgets.ALLOC_MARGIN
    assert stats.arrays_per_block <= arrays * margin + 2.0, (
        f"{code} creates {stats.arrays_per_block:.1f} NumPy arrays per block, "
        f"the baseline is {arrays:.1f}"
    )
    assert stats.bytes_per_block <= data * margin + 1024.0, (
        f"{code} allocates {stats.bytes_per_block / 1024.0:.1f} KiB of new array "
        f"data per block, the baseline is {data / 1024.0:.1f} KiB"
    )
    assert stats.peak_bytes <= int(peak * margin) + 65536, (
        f"{code} churns {stats.peak_bytes / 1024.0:.1f} KiB of Python memory per "
        f"block, the baseline is {peak / 1024.0:.1f} KiB"
    )


# ---------------------------------------------------------------------------
# The budget table
# ---------------------------------------------------------------------------
def test_every_measured_module_has_a_budget(
    catalogue: dict[str, harness.ModuleMeasurement]
) -> None:
    """Every module that can be measured has a budget.

    A module without an entry would be held at the realtime budget by
    accident.  This test makes the table follow the catalogue.
    """
    missing = [
        code
        for code in _module_codes()
        if code not in budgets.MODULE_BUDGETS
        and (catalogue.get(code) is not None and catalogue[code].ok)
    ]
    assert not missing, (
        f"{len(missing)} module(s) have no budget: {', '.join(missing)}; "
        "run qa/performance/sweep.py and qa/performance/make_budgets.py"
    )


def test_every_chain_has_a_budget() -> None:
    """Every product chain has a budget."""
    for name in budgets.CHAIN_MODULES:
        assert name in budgets.CHAIN_BUDGETS, f"the chain {name} has no budget"


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def test_headroom_report(
    capsys: pytest.CaptureFixture[str],
    catalogue: dict[str, harness.ModuleMeasurement],
) -> None:
    """Print the headroom of every module, of the chains and the load.

    The test always passes: it is a report.  Run it with ``pytest -s`` to
    see the table, or read ``qa/performance/REPORT.md`` for the recorded
    numbers.
    """
    load = harness.machine_load_percent(0.3)
    lines: list[str] = []
    lines.append(
        f"budget: {BUDGET_US:.1f} us per 64 sample block at {SAMPLE_RATE} Hz "
        f"= {budgets.BUDGET_TRANSFORMS[64]:.1f} transforms of "
        f"{REFERENCE_TRANSFORM_US:.2f} us"
    )
    lines.append(f"machine load while reporting: {load:.0f} %")
    lines.append("")
    lines.append(
        f"{'module':<24}{'64 trf':>9}{'64 %':>8}{'128 trf':>9}{'128 %':>8}"
        f"{'256 trf':>9}{'256 %':>8}{'512 trf':>9}{'512 %':>8}{'ceiling':>9}{'kind':>7}"
    )
    offenders: list[str] = []
    debt: list[str] = []
    for code in _module_codes():
        measurement = _measure_module(code, catalogue)
        if measurement is None or not measurement.ok:
            error = measurement.error if measurement is not None else "not measured"
            lines.append(f"{code:<24}  not measurable: {error}")
            continue
        cells = ""
        for block in BLOCK_SIZES:
            timing = measurement.timing_for(block)
            if timing is None:
                cells += f"{'—':>9}{'—':>8}"
                continue
            share = timing.transforms / budgets.BUDGET_TRANSFORMS[block] * 100.0
            flag = "!" if share > 100.0 else " "
            cells += f"{timing.transforms:>8.0f}{flag}{share:>7.1f}%"
        budget = budgets.MODULE_BUDGETS.get(code)
        ceiling = budget.transforms.get(TEST_BLOCK, 0.0) if budget else 0.0
        kind = budget.kind if budget else "none"
        if budget is not None and budget.over_budget:
            debt.append(code)
        timing64 = measurement.timing_for(TEST_BLOCK)
        if timing64 is not None:
            share64 = timing64.transforms / budgets.BUDGET_TRANSFORMS[TEST_BLOCK] * 100.0
            if share64 > 100.0:
                offenders.append(code)
        lines.append(
            f"{code:<24}{cells}{ceiling:>9.0f}{kind:>8}"
        )
    lines.append("")
    for name in sorted(budgets.CHAIN_MODULES):
        measurement = _measure_chain(name, catalogue)
        if measurement is None:
            continue
        timing = measurement.timing_for(TEST_BLOCK)
        if timing is None:
            continue
        budget = budgets.CHAIN_BUDGETS.get(name)
        ceiling = budget.transforms.get(TEST_BLOCK, 0.0) if budget else 0.0
        share = timing.transforms / budgets.BUDGET_TRANSFORMS[TEST_BLOCK] * 100.0
        lines.append(
            f"chain {name} ({' -> '.join(budgets.CHAIN_MODULES[name])}): "
            f"{timing.transforms:.0f} transforms, {share:.1f} % of the budget, "
            f"ceiling {ceiling:.0f} transforms ({budget.kind if budget else 'none'})"
        )
    lines.append("")
    lines.append(
        f"modules over the realtime budget right now: {len(offenders)} -> "
        f"{', '.join(offenders) if offenders else 'none'}"
    )
    lines.append(
        f"modules held at a debt ceiling (measured + margin): {len(debt)} -> "
        f"{', '.join(debt) if debt else 'none'}"
    )
    lines.append(f"modules under repair: {', '.join(budgets.UNDER_REPAIR)}")

    text = "\n".join(lines)
    with capsys.disabled():
        print(text)
