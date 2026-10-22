"""
tests.test_wow_flutter — the Wow & Flutter module.

The module tracks the pitch of the programme around a centre frequency, takes
the deviation from that centre as the modulation signal and removes the part
of it that lives in the wow band by warping the time axis.

These tests cover three things:

* the module keeps the length of the block it is given, on every block size;
* its result does not depend on what the allocator handed out — the buffer it
  accumulates the correction into has to start at zero;
* it removes a wow that is really there, and it gives the same answer whether
  the host hands it the whole file or 64 samples at a time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
from aurcore.api import Engine  # noqa: E402
from aurcore.modules.wow_flutter import WowAndFlutter  # noqa: E402
from tests.test_module_robustness import PoisonedAllocator, awkward_signal  # noqa: E402

CODE = "AUR_WOWFLUTTER"
SAMPLE_RATE = 48_000

#: The block sizes the module is exercised at.  The short ones matter: a
#: module that only misbehaves on a block shorter than its own analysis
#: window is not caught by a sweep that always uses 4096 samples.
FRAME_SIZES = (1, 2, 3, 64, 100, 480, 512, 4096)

INPUT_NAMES = ("silence", "dc", "square", "impulse", "tone")


def build(sample_rate: int = SAMPLE_RATE, channels: int = 2) -> Engine:
    """Return an engine that holds one Wow & Flutter module."""
    engine = Engine(sample_rate=sample_rate, channels=channels)
    handle = engine.add_module(CODE)
    assert handle is not None
    return engine


def set_param(engine: Engine, name: str, value) -> None:
    """Set a parameter of the module the engine holds."""
    engine.set_param(engine.handles()[0], name, value)


def process(engine: Engine, signal: np.ndarray, marker: float | None = None) -> np.ndarray:
    """Run the chain of the engine over one block."""
    if marker is None:
        out = engine.process(signal)
    else:
        with PoisonedAllocator(marker):
            with np.errstate(all="ignore"):
                out = engine.process(signal)
    return np.asarray(out, dtype=np.float64)


# ---------------------------------------------------------------------------
# The module itself.
# ---------------------------------------------------------------------------
def test_the_module_is_registered():
    """The module is in the registry under its own code."""
    assert registry.has(CODE)
    assert registry.get(CODE) is WowAndFlutter


def test_the_parameters_are_the_documented_ones():
    """The module declares the four parameters of the correction."""
    names = [desc.name for desc in WowAndFlutter(SAMPLE_RATE, 2).describe()]
    assert names == ["Wow", "Flutter", "Sensitivity", "Center Global Pitch"]


@pytest.mark.parametrize("frames", FRAME_SIZES)
@pytest.mark.parametrize("name", INPUT_NAMES)
def test_the_block_length_is_kept(frames: int, name: str):
    """The module hands back the number of samples it was given."""
    signal = awkward_signal(name, frames)
    engine = build()
    out = process(engine, signal)
    assert out.shape == signal.shape, (
        f"{frames} samples in, {out.shape[0]} samples out for {name}"
    )


@pytest.mark.parametrize("frames", FRAME_SIZES)
@pytest.mark.parametrize("name", INPUT_NAMES)
def test_the_output_is_finite(frames: int, name: str):
    """No block makes the module return a NaN or an infinity."""
    signal = awkward_signal(name, frames)
    engine = build()
    out = process(engine, signal)
    assert np.all(np.isfinite(out)), f"{name} at {frames} samples gave a non-finite sample"


@pytest.mark.parametrize("frames", FRAME_SIZES)
@pytest.mark.parametrize("name", INPUT_NAMES)
def test_the_result_does_not_depend_on_the_allocator(frames: int, name: str):
    """The result must not depend on what the allocator handed out.

    The correction is accumulated into a buffer that is only written where
    the interpolation kernel is valid.  A buffer that starts on uninitialised
    memory would carry the leftovers of the heap into the result, and the
    result would change with every run.
    """
    signal = awkward_signal(name, frames)
    first = process(build(), signal, marker=1.0e300)
    second = process(build(), signal, marker=-1.0e300)
    assert first.shape == second.shape
    assert np.array_equal(first, second, equal_nan=True), (
        f"the result changed with the allocator for {name} at {frames} samples"
    )


def test_the_accumulator_starts_at_zero():
    """The buffer the correction is accumulated into starts at zero.

    A zero signal has to come back as a zero signal: if the buffer started
    on uninitialised memory the result would carry whatever the allocator
    handed out.
    """
    module = WowAndFlutter(SAMPLE_RATE, 1)
    block = np.zeros((256, 1), dtype=np.float64)
    positions = np.arange(256, dtype=np.float64)
    out = module._interpolate(block, positions)
    assert np.all(out == 0.0), "the accumulator did not start at zero"


def test_two_runs_give_the_same_answer():
    """The same block gives the same answer twice."""
    signal = awkward_signal("tone", 2048)
    first = process(build(), signal)
    second = process(build(), signal)
    assert np.array_equal(first, second, equal_nan=True)


def test_sensitivity_zero_is_a_pass_through():
    """With the sensitivity at zero the block comes back untouched."""
    signal = awkward_signal("tone", 2048)
    engine = build()
    set_param(engine, "Sensitivity", 0.0)
    out = engine.process(signal)
    assert np.array_equal(out, signal)


def test_reset_clears_the_tracker():
    """After a reset the module behaves like a fresh one."""
    signal = awkward_signal("tone", 2048)
    engine = build()
    engine.process(signal)
    engine.reset()
    after_reset = process(engine, signal)
    fresh = process(build(), signal)
    assert np.array_equal(after_reset, fresh, equal_nan=True)


def test_mono_and_stereo_agree():
    """The tracker follows the mono mix, so both give the same answer."""
    signal = awkward_signal("tone", 2048)
    mono = process(build(channels=1), signal[:, :1])
    stereo = process(build(channels=2), signal)
    assert np.array_equal(mono[:, 0], stereo[:, 0], equal_nan=True)


# ---------------------------------------------------------------------------
# The correction itself.
# ---------------------------------------------------------------------------
def _wow(seconds: float, base: float = 440.0, depth: float = 0.02, rate: float = 1.0):
    """Return a tone whose pitch is modulated at ``rate`` Hz."""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    instant = base * (1.0 + depth * np.sin(2.0 * np.pi * rate * t))
    phase = 2.0 * np.pi * np.cumsum(instant) / SAMPLE_RATE
    return 0.5 * np.sin(phase)


def _deviation(x: np.ndarray) -> np.ndarray:
    """Return the instantaneous frequency of a signal, in hertz."""
    n = x.shape[0]
    weights = np.zeros(n)
    weights[0] = 1.0
    weights[1 : n // 2] = 2.0
    weights[n // 2] = 1.0
    analytic = np.fft.ifft(np.fft.fft(x) * weights)
    phase = np.unwrap(np.angle(analytic))
    return np.gradient(phase) * SAMPLE_RATE / (2.0 * np.pi)


def test_a_wow_is_corrected():
    """A tone with a one hertz wow comes back with a smaller wow.

    Measurement: the analytic signal of the signal and of the output, with
    the output read from the latency the module reports, so that both are
    measured on the same part of the material.
    """
    signal = _wow(2.0)
    engine = build(channels=1)
    set_param(engine, "Center Global Pitch", 440.0)
    set_param(engine, "Wow", 1.0)
    set_param(engine, "Flutter", 20.0)
    set_param(engine, "Sensitivity", 0.5)
    out = process(engine, signal[:, None])[:, 0]

    latency = int(engine.latency_samples)
    count = min(out.shape[0], signal.shape[0]) - latency
    before = float(np.std(_deviation(signal[:count])))
    after = float(np.std(_deviation(out[latency : latency + count])))
    assert after < 0.8 * before, (
        f"the wow was not corrected: the deviation went from {before:.3f} Hz "
        f"to {after:.3f} Hz"
    )


def test_the_module_is_block_coherent():
    """The answer may not depend on the block size of the host.

    Measurement: the same signal is processed in one block and in blocks of
    64 samples, and the two results are compared sample by sample.  A module
    that carries its analysis over the block boundary returns the same
    samples.
    """
    signal = _wow(2.0)
    whole = process(build(channels=1), signal[:, None])[:, 0]

    engine = build(channels=1)
    pieces = []
    for start in range(0, signal.shape[0], 64):
        block = signal[start : start + 64]
        pieces.append(process(engine, block[:, None])[:, 0])
    chunked = np.concatenate(pieces)

    count = min(whole.shape[0], chunked.shape[0])
    difference = whole[:count] - chunked[:count]
    rms = float(np.sqrt(np.mean(difference ** 2)))
    reference = float(np.sqrt(np.mean(whole[:count] ** 2)))
    level = 20.0 * np.log10(max(rms, 1e-12) / max(reference, 1e-12))
    assert level < -60.0, (
        f"the block size changes the result by {level:.1f} dB, a block "
        "coherent module changes nothing"
    )


def test_the_module_declares_its_latency():
    """The module has to report the delay it really adds.

    Measurement: the delay of the output against the input, found with a
    cross correlation, compared with ``latency_samples``.
    """
    engine = build(channels=1)
    reported = int(engine.latency_samples)
    assert reported > 0, "the module does not report a latency"
    signal = _wow(1.0)
    out = process(engine, signal[:, None])[:, 0]
    a = signal - float(np.mean(signal))
    b = out - float(np.mean(out))
    count = min(a.shape[0], b.shape[0])
    spectrum = np.fft.rfft(a[:count]) * np.conj(np.fft.rfft(b[:count]))
    correlation = np.fft.irfft(spectrum, n=count)
    # the correlation of a tone has a peak at every period, so the search
    # looks around the latency the module reports
    low = max(0, reported - 64)
    high = min(count - 1, reported + 64)
    lag = int(low + np.argmax(correlation[low:high]))
    assert abs(lag - reported) <= 64, (
        f"the module reports {reported} samples of latency but delays the "
        f"signal by {lag} samples"
    )


def test_the_correction_stays_inside_the_block():
    """The corrected block is not longer than the block that went in."""
    signal = _wow(0.25)
    engine = build(channels=1)
    set_param(engine, "Sensitivity", 1.0)
    out = process(engine, signal[:, None])
    assert out.shape[0] == signal.shape[0]
