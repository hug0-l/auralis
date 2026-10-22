"""
tests/test_removed.py — the difference tap: what a repair module removed.

A restoration module answers one half of its question with its output: *what
is left*.  The other half — *what did you take away* — was nowhere in the
catalogue.  This file is the gate of the answer:

* the primitive in :mod:`aurcore.dsp.removed` takes the difference between the
  dry signal and the processed one, blends it back into the output at the
  amount the user sets and measures it;
* the mix is **exact**: at 0 % the output is the processed signal, sample for
  sample, and at 100 % it is the removed signal;
* the difference is **aligned**: it is taken between two signals that stand at
  the same moment, so a module that delays its output still reports what it
  took away at the right time;
* the **meter** reads the level of what was removed;
* the **latency** and the **block coherence** of the modules are not changed
  by the tap, and the **cost** of a block stays inside the budget.

The modules that carry the tap are the ones whose job is to remove something:
the hum, the reverberant field, the rumble, the breathing, the clicks and the
noise.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from aurcore import registry
from aurcore.dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_DEFAULT,
    REMOVED_MIX_MAXIMUM,
    REMOVED_MIX_MINIMUM,
    REMOVED_MIX_NAME,
    REMOVED_MIX_UNIT,
    RemovedTap,
    mix_removed,
    removed_level_db,
    removed_mix_fields,
    removed_peak_db,
    removed_signal,
)
import aurcore.modules  # noqa: F401  - importing registers the modules

#: The modules that carry the difference tap.
WIRED: tuple[str, ...] = (
    "AUR_DEHUM",
    "AUR_DEREVERB",
    "AUR_DEWIND",
    "AUR_BREATH",
    "AUR_DECLICK",
    "AUR_VOICEDENOISE",
)

#: The realtime budget of one 64 sample block at 48 kHz, in microseconds.
BUDGET_US = 1333.3

#: The ceiling the cost gate allows: three budgets, so that a busy machine
#: does not fail the run.  The point is to catch a module that becomes an
#: order of magnitude slower.
CEILING_US_64 = 3.0 * BUDGET_US

#: The sample rate every measurement runs at.
SAMPLE_RATE = 48000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make(code: str, **params):
    """Build a module and set its parameters."""
    module = registry.create(code, SAMPLE_RATE, 2)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def spy(tap: RemovedTap) -> dict:
    """Record what the module hands to the tap, and what the tap returns.

    The record holds the dry signal the tap aligned, the processed signal the
    module produced, the amount of the mix and the output of the tap.
    """
    record: dict = {}
    original_align = tap.align
    original_apply = tap.apply

    def align(dry, latency=0):
        out = original_align(dry, latency)
        record["aligned"] = np.array(out, copy=True)
        return out

    def apply(dry, wet, percent, latency=0, removed=None):
        record["wet"] = np.array(wet, copy=True)
        record["percent"] = float(percent)
        record["latency"] = int(latency)
        out = original_apply(dry, wet, percent, latency, removed)
        record["out"] = np.array(out, copy=True)
        return out

    tap.align = align
    tap.apply = apply
    return record


def noise(frames: int = 8192, channels: int = 2, seed: int = 20260101) -> np.ndarray:
    """A deterministic test signal."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((frames, channels)) * 0.1


def render(code: str, signal: np.ndarray, block: int | None = None, **params) -> np.ndarray:
    """Render a signal, whole or in blocks of ``block`` samples."""
    module = make(code, **params)
    buf = np.asarray(signal, dtype=np.float64)
    if block is None:
        return np.asarray(module.process(buf), dtype=np.float64)
    pieces = [
        np.asarray(module.process(buf[start : start + block]), dtype=np.float64)
        for start in range(0, buf.shape[0], block)
    ]
    return np.concatenate(pieces, axis=0)


def edge_cases(frames: int = 4096) -> dict[str, np.ndarray]:
    """The awkward inputs the modules have to survive."""
    silence = np.zeros((frames, 2), dtype=np.float64)
    dc = np.full((frames, 2), 0.5, dtype=np.float64)
    square = (
        np.sign(np.sin(2 * np.pi * 220.0 * np.arange(frames) / SAMPLE_RATE))[:, None]
        * np.ones((1, 2))
    )
    impulse = np.zeros((frames, 2), dtype=np.float64)
    impulse[frames // 2, 0] = 1.0
    tone = (
        0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(frames) / SAMPLE_RATE)[:, None]
        * np.ones((1, 2))
    )
    return {"silence": silence, "dc": dc, "square": square, "impulse": impulse, "tone": tone}


# ---------------------------------------------------------------------------
# the primitive
# ---------------------------------------------------------------------------
def test_the_removed_signal_is_the_difference():
    """The removed signal is the dry signal minus the processed one."""
    dry = np.array([[1.0, 2.0], [3.0, 4.0]])
    wet = np.array([[0.5, 0.5], [1.0, 1.0]])
    assert np.array_equal(removed_signal(dry, wet), np.array([[0.5, 1.5], [2.0, 3.0]]))


def test_the_removed_signal_needs_two_signals_of_the_same_shape():
    """A difference of two signals of different shapes is not a difference."""
    with pytest.raises(ValueError):
        removed_signal(np.zeros((10, 2)), np.zeros((10, 1)))


def test_the_mix_at_zero_is_the_processed_signal():
    """At 0 % the output is the processed signal, sample for sample."""
    rng = np.random.default_rng(3)
    dry = rng.standard_normal((64, 2))
    wet = rng.standard_normal((64, 2))
    out = mix_removed(dry, wet, 0.0)
    assert out is wet
    assert np.array_equal(out, wet)


def test_the_mix_at_hundred_is_the_removed_signal():
    """At 100 % the output is the removed signal, sample for sample."""
    rng = np.random.default_rng(4)
    dry = rng.standard_normal((64, 2))
    wet = rng.standard_normal((64, 2))
    out = mix_removed(dry, wet, 100.0)
    assert np.array_equal(out, removed_signal(dry, wet))


def test_the_mix_in_between_is_the_blend():
    """In between the travel the output is the linear blend of the two."""
    rng = np.random.default_rng(5)
    dry = rng.standard_normal((64, 2))
    wet = rng.standard_normal((64, 2))
    out = mix_removed(dry, wet, 50.0)
    assert np.allclose(out, 0.5 * wet + 0.5 * removed_signal(dry, wet))
    # the two signals add up to the input, so the middle of the travel is the
    # half gain original
    assert np.allclose(out, 0.5 * dry)


def test_the_mix_reads_the_amount_as_a_percentage():
    """An amount outside the travel is read as the end it is nearest to."""
    rng = np.random.default_rng(6)
    dry = rng.standard_normal((16, 2))
    wet = rng.standard_normal((16, 2))
    assert np.array_equal(mix_removed(dry, wet, -10.0), wet)
    assert np.array_equal(mix_removed(dry, wet, 250.0), removed_signal(dry, wet))


def test_the_level_is_measured_in_dbfs():
    """The level of the difference is the amplitude convention of the project."""
    tone = np.full((1024, 2), 0.5)
    assert abs(removed_level_db(tone) - 20.0 * np.log10(0.5)) < 1e-9
    assert removed_level_db(np.zeros((16, 2))) == REMOVED_FLOOR_DB
    assert removed_level_db(np.zeros((0, 2))) == REMOVED_FLOOR_DB


def test_the_peak_is_measured_in_dbfs():
    """The peak of the difference is the largest sample of the block."""
    signal = np.zeros((64, 2))
    signal[3, 0] = 0.25
    assert abs(removed_peak_db(signal) - 20.0 * np.log10(0.25)) < 1e-9
    assert removed_peak_db(np.zeros((16, 2))) == REMOVED_FLOOR_DB


def test_a_level_that_is_not_a_number_rests_the_meter():
    """A difference that holds a NaN does not take the meter down."""
    broken = np.array([[np.nan, 0.0], [1.0, 1.0]])
    assert removed_level_db(broken) == REMOVED_FLOOR_DB
    assert removed_peak_db(broken) == REMOVED_FLOOR_DB


def test_the_parameter_fields_are_shared():
    """Every module that carries the control uses the same name and travel."""
    fields = removed_mix_fields("hum")
    assert fields["name"] == REMOVED_MIX_NAME
    assert fields["type"] == "float"
    assert fields["unit"] == REMOVED_MIX_UNIT
    assert fields["default"] == REMOVED_MIX_DEFAULT
    assert fields["minimum"] == REMOVED_MIX_MINIMUM
    assert fields["maximum"] == REMOVED_MIX_MAXIMUM
    assert "hum" in fields["help"]
    assert fields["help"].endswith(".")


# ---------------------------------------------------------------------------
# the tap
# ---------------------------------------------------------------------------
def test_the_tap_aligns_the_dry_signal():
    """The dry signal comes back late by the delay the module declares."""
    tap = RemovedTap(2)
    first = np.ones((4, 2))
    second = np.full((4, 2), 2.0)
    aligned = tap.align(first, 3)
    assert np.array_equal(aligned, np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [1.0, 1.0]]))
    aligned = tap.align(second, 3)
    assert np.array_equal(
        aligned, np.array([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [2.0, 2.0]])
    )


def test_the_tap_keeps_the_alignment_over_block_sizes():
    """The aligned signal is the same whatever the size of the blocks is."""
    signal = np.arange(64, dtype=np.float64)[:, None] * np.ones((1, 2))
    whole = RemovedTap(2)
    pieces = RemovedTap(2)
    reference = whole.align(signal, 8)
    gathered = [
        pieces.align(signal[start : start + 16], 8) for start in range(0, 64, 16)
    ]
    assert np.array_equal(reference, np.concatenate(gathered, axis=0))


def test_the_tap_starts_again_when_the_delay_changes():
    """A module that changes its delay gets a line that holds the new delay."""
    tap = RemovedTap(2)
    tap.align(np.ones((4, 2)), 2)
    aligned = tap.align(np.full((4, 2), 3.0), 5)
    assert aligned.shape == (4, 2)
    assert np.array_equal(aligned, np.array([[0.0, 0.0]] * 4))


def test_the_tap_never_raises():
    """A dry signal that does not fit the output leaves the output alone."""
    tap = RemovedTap(2)
    wet = np.ones((8, 2))
    out = tap.apply(np.ones((8, 1)), wet, 100.0, 0)
    assert np.array_equal(out, wet)
    assert tap.level_db == REMOVED_FLOOR_DB


# ---------------------------------------------------------------------------
# the modules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", WIRED)
def test_every_removal_module_carries_the_control(code):
    """The control is the same in every module that carries it."""
    module = make(code)
    assert module.params.has(REMOVED_MIX_NAME), f"{code} has no {REMOVED_MIX_NAME}"
    desc = module.params.desc(REMOVED_MIX_NAME)
    assert desc.type == "float"
    assert desc.unit == REMOVED_MIX_UNIT
    assert desc.minimum == REMOVED_MIX_MINIMUM
    assert desc.maximum == REMOVED_MIX_MAXIMUM
    assert desc.default == REMOVED_MIX_DEFAULT
    assert desc.help
    assert module.params.get(REMOVED_MIX_NAME) == REMOVED_MIX_DEFAULT


@pytest.mark.parametrize("code", WIRED)
def test_the_mix_at_zero_leaves_the_output_alone(code):
    """At 0 % the module returns exactly the signal it processed."""
    module = make(code)
    record = spy(module._tap)
    signal = noise()
    out = np.asarray(module.process(signal), dtype=np.float64)
    assert record["percent"] == 0.0
    assert np.array_equal(out, record["wet"]), f"{code} changed its output at 0 %"


@pytest.mark.parametrize("code", WIRED)
def test_the_mix_at_hundred_is_the_difference(code):
    """At 100 % the module returns exactly what it removed."""
    module = make(code)
    record = spy(module._tap)
    signal = noise()
    out = np.asarray(module.process(signal), dtype=np.float64)
    assert record["percent"] == 0.0
    module.params.set(REMOVED_MIX_NAME, 100.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    assert record["percent"] == 100.0
    assert np.array_equal(out, record["aligned"] - record["wet"]), (
        f"{code} does not return the removed signal at 100 %"
    )


@pytest.mark.parametrize("code", WIRED)
def test_the_difference_is_taken_at_the_same_moment(code):
    """The dry signal the tap aligns is the input, late by the latency."""
    module = make(code)
    record = spy(module._tap)
    signal = noise()
    module.process(signal)
    latency = int(module.latency_samples)
    expected = np.concatenate(
        (np.zeros((latency, 2), dtype=np.float64), signal), axis=0
    )[: signal.shape[0]]
    assert np.allclose(record["aligned"], expected, atol=1e-12), (
        f"{code} does not align the dry signal with its output"
    )


@pytest.mark.parametrize("code", WIRED)
def test_a_module_that_removes_nothing_removes_nothing(code):
    """The difference of a module that is set to do nothing is nothing."""
    quiet = {
        "AUR_DEHUM": {"Number of Harmonics": 0},
        "AUR_DEREVERB": {"Reduction": 0.0},
        "AUR_DEWIND": {"Reduction": 0.0},
        "AUR_BREATH": {"Gain": 0.0},
        "AUR_VOICEDENOISE": {"Reduction": 0.0},
    }
    if code not in quiet:
        pytest.skip(f"{code} has no setting that removes nothing")
    module = make(code, **quiet[code])
    record = spy(module._tap)
    signal = noise()
    module.process(signal)
    difference = record["aligned"] - record["wet"]
    scale = float(np.max(np.abs(record["wet"]))) or 1.0
    assert float(np.max(np.abs(difference))) / scale < 1e-12, (
        f"{code} reports a difference although it removes nothing"
    )


@pytest.mark.parametrize("code", WIRED)
def test_the_meter_reads_the_level_of_what_was_removed(code):
    """The meter reads the level of the difference of the last block."""
    module = make(code)
    record = spy(module._tap)
    signal = noise()
    module.process(signal)
    difference = record["aligned"] - record["wet"]
    assert abs(module.removed_level_db - removed_level_db(difference)) < 1e-9
    assert abs(module.removed_peak_db - removed_peak_db(difference)) < 1e-9
    assert module.removed_level_db <= module.removed_peak_db + 1e-9


@pytest.mark.parametrize("code", WIRED)
def test_the_latency_is_not_changed(code):
    """The tap does not move the delay the module declares.

    The number the module declares is its own business, and the gates of the
    modules hold it to the delay the signal shows; what the tap has to leave
    alone is that number, whatever it is.
    """
    module = make(code)
    at_zero = int(module.latency_samples)
    module.params.set(REMOVED_MIX_NAME, 100.0)
    assert int(module.latency_samples) == at_zero, (
        f"{code} declares a different latency when the mix is not empty"
    )
    module.reset()
    assert int(module.latency_samples) == at_zero
    assert at_zero >= 0


@pytest.mark.parametrize("code", WIRED)
def test_the_block_coherence_is_not_changed(code):
    """The difference is exactly as block coherent as the output is.

    The dry signal the tap aligns is the same in a render that is chopped
    into blocks and in one that is not, so the residual of the difference is
    the residual of the output, with the sign turned round.
    """
    signal = noise(frames=16384)
    whole = render(code, signal, block=None)
    chunked = render(code, signal, block=64)
    module = make(code, **{REMOVED_MIX_NAME: 100.0})
    removed_whole = np.asarray(module.process(signal), dtype=np.float64)
    module = make(code, **{REMOVED_MIX_NAME: 100.0})
    removed_chunked = np.asarray(
        np.concatenate(
            [
                np.asarray(module.process(signal[start : start + 64]), dtype=np.float64)
                for start in range(0, signal.shape[0], 64)
            ],
            axis=0,
        ),
        dtype=np.float64,
    )
    count = min(whole.shape[0], chunked.shape[0])
    output_residual = float(np.sqrt(np.mean((whole[:count] - chunked[:count]) ** 2)))
    count = min(removed_whole.shape[0], removed_chunked.shape[0])
    difference_residual = float(
        np.sqrt(np.mean((removed_whole[:count] - removed_chunked[:count]) ** 2))
    )
    assert abs(output_residual - difference_residual) <= (
        1e-9 * max(output_residual, difference_residual) + 1e-12
    ), (
        f"{code}: the difference is {difference_residual:.3e} apart from the output, "
        f"the output is {output_residual:.3e} apart from itself"
    )


@pytest.mark.parametrize("code", WIRED)
@pytest.mark.parametrize("case", ["silence", "dc", "square", "impulse", "tone"])
def test_the_awkward_inputs_survive(code, case):
    """The tap does not take a module down on an awkward input."""
    signal = edge_cases()[case]
    out = render(code, signal, block=None)
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out)), f"{code} returned a value that is not a number"
    out = render(code, signal, block=64)
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out)), f"{code} returned a value that is not a number"


@pytest.mark.parametrize("code", WIRED)
def test_the_cost_of_a_block_stays_inside_the_budget(code):
    """One 64 sample block still fits into the realtime budget."""
    module = make(code)
    block = np.ascontiguousarray(noise(frames=64))
    for _ in range(3):
        module.process(block)
    times = []
    for _ in range(15):
        start = time.perf_counter_ns()
        module.process(block)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    measured = float(np.median(times))
    assert measured <= CEILING_US_64, (
        f"{code}: a 64 sample block costs {measured:.0f} us, the ceiling is "
        f"{CEILING_US_64:.0f} us"
    )
