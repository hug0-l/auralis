"""
tests/test_eq_match.py — EQ Match: the output must match the reference.

The module measures the spectrum of the incoming audio in sixteen log spaced
bands, compares it with the spectrum of a learned reference and shapes the
audio so that the two agree.  The tests below hold it to that.

The defect the quality survey found is the first test: a host that drives the
module in 64 sample blocks used to get an output that was 298 dB louder than
its input, because the band measurement returned the floor of the analyser
for every band of a block that was shorter than the analysis window, and the
difference between the floor and the learned reference was then applied as
the gain of sixteen peaking filters.

The rules the tests hold the module to:

* the output is the input, within 1 dB, when the reference IS the input, at
  every block size and at every level of the reference;
* the output is 6 dB quieter when the reference is 6 dB quieter;
* the correction is bounded: the module never asks a filter for more gain
  than the measurement shows, and never more than 24 dB;
* a short block is measured, not floored;
* the output does not depend on the block size, and the module does not
  delay the signal.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from aurcore import registry
from aurcore.modules.eq_match import (
    ANALYSIS_HOP,
    ANALYSIS_SAMPLES,
    BAND_COUNT,
    MAX_CORRECTION_DB,
    EQMatch,
    band_centres,
    band_levels,
)

#: The sample rate the tests run at.
SAMPLE_RATE = 48_000

#: The channel count the tests run at.
CHANNELS = 2

#: The block sizes a host may drive the module with.
BLOCKS = (64, 128, 256, 512, 4096)

#: How much louder than the input the output may be, in decibels.  The rule
#: of the module survey.
RUNAWAY_LIMIT_DB = 24.0

#: How far the output may be from the input when the reference IS the input,
#: in decibels.
LEVEL_LIMIT_DB = 1.0

#: The block size the cost is measured at, and the budget it has to fit in.
COST_BLOCK = 64
BUDGET_US = (COST_BLOCK / SAMPLE_RATE) * 1_000_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_programme(samples: int = SAMPLE_RATE) -> np.ndarray:
    """A programme with a 1 kHz tone and a 3 kHz partial 18 dB down."""
    t = np.arange(samples, dtype=np.float64) / SAMPLE_RATE
    mono = 0.501187 * np.sin(2.0 * np.pi * 1000.0 * t)
    mono += 0.125893 * np.sin(2.0 * np.pi * 3000.0 * t)
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def make_module(amount: float = 1.0) -> EQMatch:
    """Return a module with the amount control set."""
    module = registry.create("AUR_EQMATCH", SAMPLE_RATE, CHANNELS)
    module.params.set("Amount", amount)
    return module


def stream(module: EQMatch, signal: np.ndarray, block: int) -> np.ndarray:
    """Feed a signal to a module the way a host does, in blocks."""
    out = np.empty_like(signal)
    for start in range(0, signal.shape[0], block):
        stop = min(start + block, signal.shape[0])
        out[start:stop] = module.process(signal[start:stop])
    return out


def rms_db(x: np.ndarray) -> float:
    """Return the level of a signal, in decibels."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return -200.0
    value = float(np.sqrt(np.mean(arr * arr)))
    return 20.0 * math.log10(value) if value > 0.0 else -200.0


def level_change_db(before: np.ndarray, after: np.ndarray) -> float:
    """Return how much the level of a signal moved, in decibels."""
    return rms_db(after) - rms_db(before)


# ---------------------------------------------------------------------------
# The rule of the survey
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", BLOCKS)
@pytest.mark.parametrize("level", (0.0, -6.0, -20.0, -40.0, -60.0))
def test_the_output_is_the_input_when_the_reference_is_the_input(block, level):
    """The reference IS the input, so the output is the input.

    This is the rule the quality survey measures: the output level may not
    differ from the input level by more than 1 dB.  The module used to
    answer +322 dB in a 64 sample block.
    """
    programme = make_programme()
    reference = programme * (10.0 ** (level / 20.0))
    module = make_module()
    module.learn(reference)
    out = stream(module, reference, block)
    change = level_change_db(reference, out)
    assert abs(change) <= LEVEL_LIMIT_DB, (
        f"a {block} sample block changed the level by {change:+.2f} dB"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_output_is_six_decibels_quieter_when_the_reference_is(block):
    """A reference that is 6 dB quieter makes the output 6 dB quieter.

    That is the purpose of the module.  The measurement is taken over the
    part of the signal the module has settled in: it needs a full analysis
    window before it can measure the spectrum, and the samples before that
    are passed through unchanged.
    """
    programme = make_programme()
    reference = programme * (10.0 ** (-6.0 / 20.0))
    module = make_module()
    module.learn(reference)
    out = stream(module, programme, block)
    settled = slice(ANALYSIS_SAMPLES, None)
    change = level_change_db(programme[settled], out[settled])
    assert abs(change + 6.0) <= LEVEL_LIMIT_DB, (
        f"a {block} sample block made the output {change:+.2f} dB, asked -6.00 dB"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_correction_is_bounded(block):
    """A reference that is far away may not run the module away.

    The difference between the reference and the input is 40 dB, which is
    more than a filter can be asked for.  The module clamps the correction,
    so the output is 24 dB quieter and not 40 dB and not louder.
    """
    programme = make_programme()
    reference = programme * (10.0 ** (-40.0 / 20.0))
    module = make_module()
    module.learn(reference)
    out = stream(module, programme, block)
    settled = slice(ANALYSIS_SAMPLES, None)
    change = level_change_db(programme[settled], out[settled])
    assert abs(change + MAX_CORRECTION_DB) <= LEVEL_LIMIT_DB, (
        f"the correction was {change:+.2f} dB, the bound is {-MAX_CORRECTION_DB:.2f} dB"
    )
    assert change <= 0.0, "the module made the signal louder"
    assert change >= -RUNAWAY_LIMIT_DB, "the module ran away"


@pytest.mark.parametrize("block", BLOCKS)
def test_the_output_is_never_a_runaway(block):
    """The output may not be more than 24 dB louder than the input."""
    programme = make_programme()
    module = make_module()
    module.learn(programme * (10.0 ** (12.0 / 20.0)))
    out = stream(module, programme, block)
    peak_before = float(np.max(np.abs(programme)))
    peak_after = float(np.max(np.abs(out)))
    assert peak_after <= peak_before * (10.0 ** (RUNAWAY_LIMIT_DB / 20.0)), (
        f"the peak grew by {20.0 * math.log10(peak_after / peak_before):+.1f} dB"
    )


def test_the_rule_holds_for_noise():
    """The rule holds for noise, not only for a steady programme."""
    rng = np.random.default_rng(7)
    mono = rng.standard_normal(SAMPLE_RATE) * 0.1
    programme = np.repeat(mono[:, None], CHANNELS, axis=1)
    module = make_module()
    module.learn(programme)
    out = stream(module, programme, 64)
    change = level_change_db(programme, out)
    assert abs(change) <= LEVEL_LIMIT_DB, f"the level changed by {change:+.2f} dB"


def test_without_a_reference_the_module_does_nothing():
    """Before a reference is learned the module passes the audio through."""
    module = make_module()
    programme = make_programme()
    out = stream(module, programme, 64)
    assert np.array_equal(out, programme)


def test_an_amount_of_zero_does_nothing():
    """The amount control scales the whole correction."""
    programme = make_programme()
    module = make_module(amount=0.0)
    module.learn(programme * (10.0 ** (-6.0 / 20.0)))
    out = stream(module, programme, 64)
    assert np.array_equal(out, programme)


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------
def test_a_short_block_is_measured_and_not_floored():
    """A block of 64 samples carries a level, not the floor of the analyser.

    The defect was here: the band measurement returned -120 dB for every
    band of a block that was shorter than the analysis window, and the
    difference between that and the learned reference was applied as a gain.
    """
    block = make_programme(samples=64)
    levels = band_levels(block, SAMPLE_RATE)
    assert levels.shape == (BAND_COUNT,)
    assert float(np.max(levels)) > -60.0, "the 3 kHz partial was not measured"
    measured = int(np.count_nonzero(levels > -119.0))
    assert measured >= 1, "every band of a short block reads the floor"


def test_the_band_level_does_not_depend_on_the_length_of_the_window():
    """The level of a band is the same over a short and over a long window.

    That is what makes the learned reference and the running measurement
    comparable.  The mean of the magnitudes, which the module used to take,
    does not have that property.
    """
    programme = make_programme()
    short = band_levels(programme[:1024], SAMPLE_RATE, size=1024)
    long = band_levels(programme, SAMPLE_RATE, size=ANALYSIS_SAMPLES)
    centres = band_centres()
    # The band that holds the 3 kHz partial is the one both windows resolve.
    band = int(np.argmin(np.abs(centres - 3000.0)))
    assert abs(short[band] - long[band]) <= 0.5, (
        f"the 3 kHz band reads {short[band]:.2f} dB over 1024 samples and "
        f"{long[band]:.2f} dB over {ANALYSIS_SAMPLES} samples"
    )


def test_the_learned_reference_describes_the_reference():
    """The learned profile is the spectrum of the reference."""
    reference = make_programme()
    module = make_module()
    module.learn(reference)
    levels = band_levels(reference, SAMPLE_RATE)
    assert np.allclose(module._reference, levels, atol=1e-9)
    centres = band_centres()
    loud = int(np.argmax(levels))
    assert abs(centres[loud] - 976.8) < 1.0, "the loudest band is not the 1 kHz one"


def test_the_learned_reference_survives_a_state_round_trip():
    """The module keeps its reference, and its level, across a state."""
    reference = make_programme()
    module = make_module()
    module.learn(reference)
    other = make_module()
    other.deserialize(module.serialize())
    assert np.allclose(other._reference, module._reference)
    assert other._reference_level == pytest.approx(module._reference_level)
    programme = make_programme()
    assert np.array_equal(stream(other, programme, 64), stream(module, programme, 64))


# ---------------------------------------------------------------------------
# The block size
# ---------------------------------------------------------------------------
def test_the_output_does_not_depend_on_the_block_size():
    """A host that feeds 64 sample blocks gets what a host that feeds the
    whole file gets."""
    programme = make_programme()
    reference = programme * (10.0 ** (-6.0 / 20.0))
    whole = make_module()
    whole.learn(reference)
    expected = whole.process(programme.copy())
    for block in BLOCKS:
        module = make_module()
        module.learn(reference)
        out = stream(module, programme, block)
        residual = float(np.max(np.abs(expected - out)))
        assert residual < 1e-9, f"a {block} sample block differs by {residual:.3e}"


def test_the_module_does_not_delay_the_signal():
    """The module adds no latency: it does not move the signal in time."""
    module = make_module()
    assert module.latency_samples == 0


def test_the_correction_engages_after_the_analysis_window():
    """The module waits for a full analysis window before it corrects.

    The window is 2048 samples at 48 kHz, so 42.7 ms.  Until then the audio
    is passed through unchanged, and afterwards the correction follows the
    reference.
    """
    programme = make_programme()
    module = make_module()
    module.learn(programme * (10.0 ** (-6.0 / 20.0)))
    out = stream(module, programme, 64)
    early = slice(0, ANALYSIS_SAMPLES)
    assert np.allclose(out[early], programme[early], atol=1e-12), (
        "the module corrected the audio before it had measured it"
    )
    late = slice(ANALYSIS_SAMPLES + 4 * ANALYSIS_HOP, None)
    assert abs(level_change_db(programme[late], out[late]) + 6.0) <= 0.5


# ---------------------------------------------------------------------------
# The cost
# ---------------------------------------------------------------------------
def test_the_module_fits_into_the_realtime_budget():
    """One 64 sample block has to be played in 1333 us at 48 kHz."""
    module = make_module()
    programme = make_programme()
    module.learn(programme * (10.0 ** (6.0 / 20.0)))
    block = np.ascontiguousarray(programme[:COST_BLOCK])
    module.process(block)
    best = math.inf
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(20):
            module.process(block)
        elapsed = (time.perf_counter() - start) / 20.0
        best = min(best, elapsed)
    cost_us = best * 1e6
    assert cost_us <= BUDGET_US, f"a block costs {cost_us:.1f} us, the budget is {BUDGET_US:.1f} us"
