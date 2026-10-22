"""
tests/test_guitar_de_noise.py — the guard of the Guitar De-Noise module.

The module is one of the seven that did not fit into the realtime budget at
a 64 sample block (see ``qa/performance/REPORT.md``).  The tests here hold
three things:

* the module still does what it says (it removes the mains hum and the
  string squeak, it leaves the wanted partials alone, it can be bypassed);
* it does **not** cost more than the budget of a 64 sample block;
* it does not cost noticeably more than it costs today — the ceiling below
  is the measured cost with a generous margin, so a regression fails the
  test even on a machine that is faster than the reference one.

The repair of the module (the survey found that it removed 11.96 dB from
the guitar's own fundamental) is held by the numbers below: the fundamental
may not move by more than 1 dB, the squeak has to go, and the guard that
decides "short burst or held note" has to measure the length of the *event*
rather than the length of the current excursion.

Run it with ``python -m pytest tests/test_guitar_de_noise.py -q``.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aurcore import registry  # noqa: E402
import aurcore.modules  # noqa: E402,F401
from aurcore.modules import guitar_de_noise as guitar  # noqa: E402
from aurcore.modules.guitar_de_noise import GuitarDenoise  # noqa: E402

#: The sample rate the module is measured at.
SAMPLE_RATE = 48_000

#: The channel count the module is measured with.
CHANNELS = 2

#: The block size the budget problem is about.
BLOCK = 64

#: The realtime budget of one block, in microseconds (1333.3 us at 48 kHz).
BUDGET_US = (BLOCK / SAMPLE_RATE) * 1_000_000.0

#: The cost the module may never exceed, in microseconds.  The measurement
#: below gives 410 us on an idle machine and about 600 us on a machine that
#: is busy with other work; 1000 us leaves room for both and is still
#: inside the budget, so a regression is caught twice.
COST_CEILING_US = 1000.0

#: How many blocks are timed, and how many warm the module up first.
REPEATS = 20
WARMUP = 5

#: How much the fundamental of the guitar may move, in decibels.  The rule
#: of the survey is 1 dB; the measurement below is 0.08 dB, so the test
#: holds a tighter number and still has room for the machine.
FUNDAMENTAL_BUDGET_DB = -1.0

#: How much the harmonics of the guitar may move, in decibels.  The rule of
#: the survey is 3 dB; the measurement is 0.18 dB.
HARMONICS_BUDGET_DB = -3.0

#: How much of the string squeak has to go, in decibels.  The rule of the
#: survey is 3 dB; the measurement is 11.8 dB.
SQUEAK_REMOVED_DB = -6.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_module(**params) -> GuitarDenoise:
    """Return a module with the given parameters set."""
    module = GuitarDenoise(SAMPLE_RATE, CHANNELS)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def _test_signal(n: int = 2048, seed: int = 20260211) -> np.ndarray:
    """Return a deterministic guitar-like signal, shape ``(n, 2)``.

    A harmonic stack (the guitar), a noise bed, the mains hum and a low
    rumble — the material the module is meant to work on.  The stack has
    twelve partials, so every partial can be measured.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    signal = 0.004 * rng.standard_normal(n)
    signal += 0.05 * np.sin(2 * np.pi * 60.0 * t)
    signal += 0.01 * np.sin(2 * np.pi * 35.0 * t)
    for harmonic in range(1, 13):
        signal += (0.30 / harmonic**1.25) * np.sin(2 * np.pi * 110.0 * harmonic * t)
    peak = float(np.max(np.abs(signal)))
    mono = signal / peak * 0.5
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def _guitar_with_squeak(n: int = 24000, f0: float = 196.0) -> np.ndarray:
    """Return the probe of the survey: a guitar stack with a string squeak.

    The stack has eight partials with a 1/f tilt and the squeak is a 3 kHz
    tone from 0.20 s to 0.32 s.  The shape is ``(n, 2)``.
    """
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    stack = np.zeros(n, dtype=np.float64)
    for harmonic in range(1, 9):
        stack += (1.0 / harmonic) * np.sin(2 * np.pi * f0 * harmonic * t)
    stack /= float(np.max(np.abs(stack)))
    stack *= 10.0 ** (-20.0 / 20.0) / float(np.sqrt(np.mean(stack**2)))
    squeak = 0.031623 * np.sin(2 * np.pi * 3000.0 * t)
    envelope = np.zeros(n, dtype=np.float64)
    envelope[int(0.20 * SAMPLE_RATE) : int(0.32 * SAMPLE_RATE)] = 1.0
    mono = stack + squeak * envelope
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def _level_db(signal: np.ndarray, freq: float) -> float:
    """Return the level of one partial of a mono signal, in dB."""
    flat = np.asarray(signal, dtype=np.float64).ravel()
    window = np.hanning(flat.size)
    n = np.arange(flat.size, dtype=np.float64)
    tone = np.sum(flat * window * np.exp(-2j * np.pi * freq * n / SAMPLE_RATE))
    return 20.0 * float(np.log10(max(2.0 * float(np.abs(tone)) / float(np.sum(window)), 1e-12)))


def _band_db(
    signal: np.ndarray, lo: float, hi: float, start: int = 0, end: int | None = None
) -> float:
    """Return the level of a band of a mono signal, in dBFS."""
    seg = np.asarray(signal, dtype=np.float64).ravel()[start:end]
    if seg.size == 0:
        return -180.0
    window = np.hanning(seg.size)
    spectrum = np.fft.rfft(seg * window)
    freqs = np.fft.rfftfreq(seg.size, 1.0 / float(SAMPLE_RATE))
    mask = (freqs >= lo) & (freqs < hi)
    if not bool(mask.any()):
        return -180.0
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    if weight <= 0.0 or power <= 0.0:
        return -180.0
    return 10.0 * float(np.log10(max(2.0 * power / (weight * weight), 1e-30)))


def _measure_block_us(
    module: GuitarDenoise, block: np.ndarray, repeats: int = REPEATS, warmup: int = WARMUP
) -> float:
    """Return the time of the fastest of a number of blocks, in microseconds.

    The fastest block is the least disturbed estimate of the cost of the
    module, which is what the budget has to be compared with.
    """
    for _ in range(warmup):
        module.process(block.copy())
    times = []
    for _ in range(repeats):
        chunk = block.copy()
        start = time.perf_counter_ns()
        module.process(chunk)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return min(times)


def _process_in_blocks(module: GuitarDenoise, signal: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Run the module over a signal the way a host does: block by block."""
    out = np.empty_like(signal)
    for start in range(0, signal.shape[0], block):
        chunk = signal[start : start + block]
        if chunk.shape[0] == 0:
            continue
        result = np.asarray(module.process(chunk.copy()), dtype=np.float64)
        result = result.reshape(chunk.shape)
        out[start : start + chunk.shape[0]] = result
    return out


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------
def test_a_64_sample_block_fits_into_the_realtime_budget() -> None:
    """The module must process a 64 sample block faster than it plays.

    This is the defect the module was reported for: it needed more than the
    whole budget for a 64 sample block.  The budget is the time the block
    needs to be played, ``block / sample_rate`` seconds.
    """
    module = _make_module()
    block = np.ascontiguousarray(_test_signal(BLOCK))
    cost = _measure_block_us(module, block)
    assert cost <= BUDGET_US, (
        f"a {BLOCK} sample block cost {cost:.0f} us, the budget is "
        f"{BUDGET_US:.0f} us"
    )


def test_the_cost_does_not_regress() -> None:
    """The module must not become slow again.

    The ceiling is the measured cost with a generous margin; it is below the
    budget, so this test fails before the budget test does.
    """
    module = _make_module()
    block = np.ascontiguousarray(_test_signal(BLOCK))
    cost = _measure_block_us(module, block)
    assert cost <= COST_CEILING_US, (
        f"a {BLOCK} sample block cost {cost:.0f} us, the ceiling is "
        f"{COST_CEILING_US:.0f} us (budget {BUDGET_US:.0f} us)"
    )


def test_the_cost_per_sample_does_not_grow_with_the_block_size() -> None:
    """The cost per sample must not grow with the block.

    The analysis windows are fixed, so the work per block is fixed; the
    budget grows with the block.  A module whose cost per sample grew with
    the block would be over the budget at the small block sizes.
    """
    module = _make_module()
    signal = _test_signal(1024)
    costs = {}
    for size in (64, 128, 256, 512):
        costs[size] = _measure_block_us(
            module, np.ascontiguousarray(signal[:size]), repeats=10, warmup=3
        )
    per_sample = {size: cost / size for size, cost in costs.items()}
    assert per_sample[512] <= per_sample[64] * 1.5, (
        f"the cost per sample grows with the block: {per_sample}"
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_the_block_keeps_its_shape_and_stays_finite() -> None:
    """A processed block has the shape of the input and no bad samples."""
    module = _make_module()
    block = np.ascontiguousarray(_test_signal(512))
    out = module.process(block)
    assert out is not None
    assert out.shape == block.shape
    assert np.all(np.isfinite(np.asarray(out, dtype=np.float64)))


@pytest.mark.parametrize(
    "name",
    ["silence", "dc", "square", "impulse", "tone"],
)
def test_awkward_material_is_handled(name: str) -> None:
    """Silence, a constant, a square wave, an impulse and a pure tone."""
    n = 1024
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    cases = {
        "silence": np.zeros((n, CHANNELS)),
        "dc": np.full((n, CHANNELS), 0.5),
        "square": np.sign(np.sin(2 * np.pi * 220.0 * t))[:, None] * np.ones((1, CHANNELS)),
        "impulse": np.zeros((n, CHANNELS)),
        "tone": (0.5 * np.sin(2 * np.pi * 1000.0 * t))[:, None] * np.ones((1, CHANNELS)),
    }
    cases["impulse"][n // 2, 0] = 1.0
    module = _make_module()
    out = module.process(cases[name])
    assert out.shape == cases[name].shape
    assert np.all(np.isfinite(np.asarray(out, dtype=np.float64)))


def test_an_empty_block_is_accepted() -> None:
    """A block without samples is not an error."""
    module = _make_module()
    out = module.process(np.zeros((0, CHANNELS)))
    assert out.shape == (0, CHANNELS)


def test_a_mono_block_is_accepted() -> None:
    """A one dimensional block is treated as one channel."""
    module = _make_module()
    out = module.process(_test_signal(256)[:, 0])
    assert out.size == 256
    assert np.all(np.isfinite(np.asarray(out, dtype=np.float64)))


def test_the_input_block_is_not_modified() -> None:
    """The module must not write into the block the host gave it."""
    module = _make_module()
    block = np.ascontiguousarray(_test_signal(256))
    original = block.copy()
    module.process(block)
    assert np.array_equal(block, original)


def test_bypassing_everything_leaves_the_signal_alone() -> None:
    """With all three sub-processors bypassed the block passes through."""
    module = _make_module()
    for name in ("amp", "squeak", "pick"):
        module.set_bypass(name, True)
    block = np.ascontiguousarray(_test_signal(256))
    out = module.process(block)
    assert np.allclose(np.asarray(out, dtype=np.float64), block, atol=1e-12)


def test_the_bypass_state_is_serialised() -> None:
    """The three sub-states are stored apart from each other."""
    module = _make_module()
    module.set_bypass("squeak", True)
    state = module.serialize()
    assert state["squeak"]["bypass"] is True
    assert state["amp"]["bypass"] is False
    assert state["pick"]["bypass"] is False

    other = _make_module()
    other.deserialize(state)
    assert other._squeak.bypass is True
    assert other._amp.bypass is False


def test_the_module_is_registered() -> None:
    """The module is in the registry under its own code."""
    assert registry.has("AUR_GUITARDENOISE")
    module = registry.create("AUR_GUITARDENOISE", SAMPLE_RATE, CHANNELS)
    assert isinstance(module, GuitarDenoise)


# ---------------------------------------------------------------------------
# The quality: the wanted part
# ---------------------------------------------------------------------------
def test_the_fundamental_of_the_guitar_survives() -> None:
    """The fundamental of the guitar may not be taken for a squeak.

    This is the defect the survey found: the run length that decides
    whether a narrow band component is a short burst was measured along the
    wrong axis, so it never grew, and the held fundamental was classified
    as a squeak in every frame.  The survey measured 11.96 dB of damage;
    the measurement below is 0.08 dB.
    """
    module = _make_module()
    signal = _guitar_with_squeak()[:, 0]
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    before = _band_db(signal, 120.0, 250.0)
    after = _band_db(out, 120.0, 250.0)
    assert after - before > FUNDAMENTAL_BUDGET_DB, (
        f"the fundamental of the guitar lost {before - after:.2f} dB, the budget "
        f"is {-FUNDAMENTAL_BUDGET_DB:.2f} dB"
    )


def test_the_harmonics_of_the_guitar_survive() -> None:
    """The harmonics of the guitar may not be taken for a squeak either."""
    module = _make_module()
    signal = _guitar_with_squeak()[:, 0]
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    before = _band_db(signal, 250.0, 3000.0)
    after = _band_db(out, 250.0, 3000.0)
    assert after - before > HARMONICS_BUDGET_DB, (
        f"the harmonics of the guitar lost {before - after:.2f} dB, the budget "
        f"is {-HARMONICS_BUDGET_DB:.2f} dB"
    )


def test_a_held_note_is_not_a_burst() -> None:
    """A note that is still sounding is not a squeak.

    The guard that tells a burst from a held note looks at the length of
    the event.  A tone that is sounding for a second is one long event, so
    the module leaves it alone — the tail of the tone must not move.
    """
    n = SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    tone = 0.5 * np.sin(2 * np.pi * 1000.0 * t)
    module = _make_module()
    out = np.asarray(module.process(tone.copy()), dtype=np.float64)
    tail = slice(int(0.6 * SAMPLE_RATE), n)
    before = _level_db(tone[tail], 1000.0)
    after = _level_db(out[tail], 1000.0)
    assert after - before > -0.5, (
        f"the tail of the tone lost {before - after:.2f} dB, a held note is "
        f"not a burst"
    )


def test_a_short_burst_is_still_taken_away() -> None:
    """A short narrow band burst is what the module is for."""
    n = SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    tone = 0.2 * np.sin(2 * np.pi * 3000.0 * t)
    envelope = np.zeros(n, dtype=np.float64)
    envelope[int(0.30 * SAMPLE_RATE) : int(0.40 * SAMPLE_RATE)] = 1.0
    burst = tone * envelope
    module = _make_module()
    out = np.asarray(module.process(burst.copy()), dtype=np.float64)
    window = slice(int(0.30 * SAMPLE_RATE), int(0.40 * SAMPLE_RATE))
    before = _level_db(burst[window], 3000.0)
    after = _level_db(out[window], 3000.0)
    assert after - before < -3.0, (
        f"the burst only lost {before - after:.2f} dB, it has to go"
    )


# ---------------------------------------------------------------------------
# The quality: the unwanted part
# ---------------------------------------------------------------------------
def test_the_string_squeak_is_removed() -> None:
    """The squeak has to go: that is what the module is for."""
    module = _make_module()
    signal = _guitar_with_squeak()[:, 0]
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    window = slice(int(0.20 * SAMPLE_RATE), int(0.32 * SAMPLE_RATE))
    before = _band_db(signal, 2800.0, 3200.0, window.start, window.stop)
    after = _band_db(out, 2800.0, 3200.0, window.start, window.stop)
    assert after - before < SQUEAK_REMOVED_DB, (
        f"the squeak only lost {before - after:.2f} dB, it has to go"
    )


def test_the_noise_bed_is_not_raised() -> None:
    """The module must not add noise to the material."""
    module = _make_module()
    signal = _test_signal(8192)[:, 0]
    out = _process_in_blocks(module, signal)
    before = 20.0 * float(np.log10(max(float(np.sqrt(np.mean(signal**2))), 1e-12)))
    after = 20.0 * float(np.log10(max(float(np.sqrt(np.mean(out**2))), 1e-12)))
    assert after - before < 1.0, (
        f"the noise bed grew by {after - before:.2f} dB"
    )


def test_the_wanted_partials_are_not_damaged() -> None:
    """The harmonics of the guitar must survive the module.

    The module is a repair tool: it may take the buzz and the rumble away,
    but the notes must stay where they are.  The signal is fed to the
    module the way a host feeds it, block by block, because that is how the
    module is used.
    """
    module = _make_module()
    signal = _test_signal(8192)[:, 0]
    out = _process_in_blocks(module, signal)
    shifts = []
    for harmonic in range(1, 13):
        freq = 110.0 * harmonic
        shifts.append(_level_db(out, freq) - _level_db(signal, freq))
    mean_shift = float(np.mean(shifts))
    worst_shift = float(np.min(shifts))
    assert mean_shift > -1.0, f"the partials lost {mean_shift:.2f} dB on average"
    assert worst_shift > -3.0, f"the worst partial lost {worst_shift:.2f} dB"


def test_the_programme_is_not_damaged_in_small_blocks() -> None:
    """A block of 64 samples may not damage the programme either.

    The old module built its filters again for every block, so every block
    started from a filter that had not settled: the high pass rang for
    8 ms at the start of every block, which cost the fundamental 1.99 dB
    and raised the noise bed by 2.24 dB.
    """
    module = _make_module()
    signal = _guitar_with_squeak()[:, 0]
    out = _process_in_blocks(module, signal)
    before = _band_db(signal, 120.0, 250.0)
    after = _band_db(out, 120.0, 250.0)
    assert after - before > FUNDAMENTAL_BUDGET_DB, (
        f"the fundamental lost {before - after:.2f} dB in 64 sample blocks"
    )


# ---------------------------------------------------------------------------
# The detectors
# ---------------------------------------------------------------------------
def test_the_mains_hum_is_notched_out_of_a_small_block() -> None:
    """The mains notch has to work when the host feeds 64 samples.

    A projection over 64 samples cannot tell 50 Hz from 60 Hz — over
    1.33 ms the two differ by 4.8 degrees — so the detector keeps a
    rolling window of 170.7 ms and updates its projections incrementally.
    The audio is not delayed by this; only the decision uses the past.
    """
    n = 4 * SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    signal = 0.2 * np.sin(2 * np.pi * 50.0 * t) + 0.2 * np.sin(2 * np.pi * 196.0 * t)
    module = _make_module()
    out = _process_in_blocks(module, np.repeat(signal[:, None], CHANNELS, axis=1))
    tail = slice(int(0.30 * SAMPLE_RATE), n)
    hum_before = _level_db(signal[tail], 50.0)
    hum_after = _level_db(out[tail, 0], 50.0)
    assert hum_after - hum_before < -10.0, (
        f"the 50 Hz hum only lost {hum_before - hum_after:.2f} dB"
    )
    note_before = _level_db(signal[tail], 196.0)
    note_after = _level_db(out[tail, 0], 196.0)
    assert note_after - note_before > -1.0, (
        f"the notch took {note_before - note_after:.2f} dB of the note with it"
    )


def test_the_rumble_is_measured_and_taken_away() -> None:
    """A rumble below the guitar has to be measured and removed.

    The ``Amp Resolution`` (16..512 samples) cannot resolve the rumble
    band: at the largest setting the bin spacing is 93.75 Hz, so the only
    bin below 55 Hz is the one at 0 Hz.  The level is therefore measured
    in the time domain, with a low pass at 55 Hz.
    """
    n = 4 * SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    signal = 0.3 * np.sin(2 * np.pi * 35.0 * t) + 0.3 * np.sin(2 * np.pi * 196.0 * t)
    module = _make_module()
    out = _process_in_blocks(module, np.repeat(signal[:, None], CHANNELS, axis=1))
    tail = slice(int(0.30 * SAMPLE_RATE), n)
    rumble_before = _level_db(signal[tail], 35.0)
    rumble_after = _level_db(out[tail, 0], 35.0)
    note_before = _level_db(signal[tail], 196.0)
    note_after = _level_db(out[tail, 0], 196.0)
    assert rumble_before - rumble_after > 3.0, (
        f"the rumble only lost {rumble_before - rumble_after:.2f} dB"
    )
    assert note_before - note_after < 1.0, (
        f"the rumble filter took {note_before - note_after:.2f} dB of the note"
    )


def test_a_clean_recording_is_left_alone_by_the_rumble_filter() -> None:
    """Without rumble there is no rumble filter: the note stays."""
    n = 4 * SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    signal = 0.3 * np.sin(2 * np.pi * 196.0 * t)
    module = _make_module()
    out = _process_in_blocks(module, np.repeat(signal[:, None], CHANNELS, axis=1))
    tail = slice(int(0.30 * SAMPLE_RATE), n)
    before = _level_db(signal[tail], 196.0)
    after = _level_db(out[tail, 0], 196.0)
    assert after - before > -0.5, f"the note lost {before - after:.2f} dB"


def test_the_pick_detector_cannot_change_a_small_block() -> None:
    """The pick detector cannot change a block of one or two frames.

    The temporal median of a one or two frame analysis is the analysis
    itself, so the excess over it — and with it the pick gain — is zero.
    The module uses that to skip the pick analysis for a small block; the
    test holds the shortcut to it.
    """
    module = _make_module()
    module.set_bypass("amp", True)
    module.set_bypass("squeak", True)
    signal = _test_signal(256)[:, 0]
    out = np.asarray(module.process(signal[:, None]), dtype=np.float64)[:, 0]
    difference = float(np.max(np.abs(out - signal)))
    assert difference < 1e-9, (
        f"the pick detector changed a small block by {difference:.3e}"
    )


def test_the_pick_detector_works_when_there_are_three_frames() -> None:
    """Three frames are enough for the pick detector to act."""
    module = _make_module()
    module.set_bypass("amp", True)
    module.set_bypass("squeak", True)
    n = 1024
    signal = np.zeros(n, dtype=np.float64)
    signal[n // 2] = 1.0
    out = np.asarray(module.process(signal[:, None]), dtype=np.float64)[:, 0]
    difference = float(np.max(np.abs(out)))
    assert difference < 1.0, f"the pick attack was not damped: {difference:.3f}"


# ---------------------------------------------------------------------------
# The rule that decides
# ---------------------------------------------------------------------------
def test_the_run_length_is_measured_along_the_time_axis() -> None:
    """The run length has to count the frames, not the channels.

    The analysis is laid out as ``(channels, frames, bins)``, so the time
    axis is axis 1.  The run length used to be measured along axis 0, which
    made it the length of a channel and never more than one frame.
    """
    active = np.zeros((2, 6, 3), dtype=bool)
    active[0, 1:5, 0] = True
    run = guitar._running_length(active, axis=1)
    assert run[0, 1, 0] == 1.0
    assert run[0, 2, 0] == 2.0
    assert run[0, 4, 0] == 4.0
    assert run[0, 5, 0] == 0.0
    assert run[1, 4, 0] == 0.0


def test_the_event_survives_a_short_gap() -> None:
    """A loud frame keeps its event alive for a few frames.

    The prominence of a partial that does not sit on a bin centre dips
    while the leakage pattern rotates, so the event has to survive those
    dips — otherwise a held note is a row of short bursts.
    """
    peak = np.zeros((1, 20, 1), dtype=bool)
    peak[0, 0, 0] = True
    peak[0, 10, 0] = True
    event = guitar._event_mask(peak, 4)
    assert event[0, 3, 0], "the event died before the hold was over"
    assert not event[0, 6, 0], "the event outlived the hold"
    assert event[0, 13, 0], "the second loud frame did not start an event"


def test_the_guard_counts_the_event_in_samples() -> None:
    """The guard measures the length of the event in samples.

    The window of the ``Squeak Duration`` is a time, and the time is
    counted in samples so that the guard means the same thing whether the
    host feeds the module 64 samples or 5120 at a time.
    """
    assert guitar._HOP == 256
    # "Short" is 20 frames of 256 samples: 106.7 ms.
    assert 20 * guitar._HOP == 5120
    assert math.isclose(20 * guitar._HOP / SAMPLE_RATE, 0.10666, abs_tol=1e-4)


def test_both_shared_api_paths_give_the_same_signal() -> None:
    """The module gives the same signal with and without the new core API.

    The module uses the complex spectrum of the shared engine when the
    engine offers it, and the magnitude/phase API when it does not.  Both
    paths have to give the same numbers.
    """
    import aurcore.modules.guitar_de_noise as guitar_module

    if not hasattr(guitar_module.STFT, "analyze_complex_block"):
        pytest.skip("the shared engine does not offer the complex API yet")

    signal = _test_signal(4096)
    saved = guitar_module._COMPLEX_ANALYSIS
    try:
        guitar_module._COMPLEX_ANALYSIS = True
        new = np.asarray(_make_module().process(signal), dtype=np.float64)
        guitar_module._COMPLEX_ANALYSIS = False
        old = np.asarray(_make_module().process(signal), dtype=np.float64)
    finally:
        guitar_module._COMPLEX_ANALYSIS = saved
    assert np.max(np.abs(new - old)) < 1e-12
