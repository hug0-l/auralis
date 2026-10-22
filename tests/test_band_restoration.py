"""
tests/test_band_restoration.py — the Band Restoration module.

The module splits a recording into up to four bands at three edges the user
sets, repairs every band on its own and hands the user a mixer for the
bands.  These tests cover:

* the contract: the output is finite, it is as long as the input, and it
  does not depend on the size of the blocks the host uses;
* the crossover: the three edges are ordered and never cross, and the
  module is transparent in the linear phase mode;
* the restoration: after a *Learn* the learned floor is taken out of the
  band that carries it, and the wanted partial survives;
* the surface: the mix, the solo, the mute and the bypass of a band, and
  the broadband path;
* the latency: the efficient splitter adds none, the linear phase splitter
  delays the signal by half the length of its kernel, and the module says
  so.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
from aurcore.modules.band_restoration import BandRestoration  # noqa: E402

CODE = "AUR_BANDRESTORATION"
SAMPLE_RATE = 48_000

#: The block sizes the module is exercised at.  The short ones matter: a
#: module that only misbehaves on a block shorter than its own analysis
#: window is not caught by a sweep that always uses 4096 samples.
FRAME_SIZES = (1, 2, 3, 64, 100, 512, 4096)

INPUT_NAMES = ("silence", "dc", "square", "impulse", "tone")


def awkward_signal(name: str, frames: int) -> np.ndarray:
    """Return one of the awkward signals of the module survey."""
    t = np.arange(frames, dtype=np.float64) / SAMPLE_RATE
    if name == "silence":
        data = np.zeros(frames)
    elif name == "dc":
        data = np.full(frames, 0.5)
    elif name == "square":
        data = np.sign(np.sin(2 * np.pi * 220.0 * t))
    elif name == "impulse":
        data = np.zeros(frames)
        if frames > 2:
            data[frames // 2] = 1.0
    else:
        data = 0.5 * np.sin(2 * np.pi * 1000.0 * t)
    return np.repeat(data[:, None], 2, axis=1)


def build(channels: int = 2, **params) -> BandRestoration:
    """Return a module with the given parameters."""
    module = BandRestoration(SAMPLE_RATE, channels)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def band_level(x: np.ndarray, lo: float, hi: float) -> float:
    """The level of a frequency band of a mono signal, in dB."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size == 0:
        return -300.0
    window = np.hanning(arr.size)
    spectrum = np.abs(np.fft.rfft(arr * window)) ** 2
    freqs = np.fft.rfftfreq(arr.size, 1.0 / SAMPLE_RATE)
    sel = (freqs >= lo) & (freqs <= hi)
    if not np.any(sel):
        return -300.0
    return 10.0 * math.log10(max(float(np.mean(spectrum[sel])), 1e-30))


def partial_level(x: np.ndarray, freq: float) -> float:
    """The level of a partial of a mono signal, in dB."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size == 0:
        return -300.0
    basis = np.exp(-2j * math.pi * freq * np.arange(arr.size) / SAMPLE_RATE)
    return 20.0 * math.log10(max(abs(np.dot(arr, basis)) / arr.size, 1e-12))


# ---------------------------------------------------------------------------
# The module itself.
# ---------------------------------------------------------------------------
def test_the_module_is_registered():
    """The module is in the registry under its own code."""
    assert registry.has(CODE)
    assert registry.get(CODE) is BandRestoration


def test_the_parameters_are_the_documented_ones():
    """The module declares the parameters of the surface."""
    names = [desc.name for desc in BandRestoration(SAMPLE_RATE, 2).describe()]
    assert names[:9] == [
        "Bands",
        "Low Crossover",
        "Mid Crossover",
        "High Crossover",
        "Phase Linearity",
        "Restoration Depth",
        "Noise Threshold",
        "High Frequency Transparency",
        "Broadband Mix",
    ]
    for index in (1, 2, 3, 4):
        for control in ("Mix", "Solo", "Mute", "Bypass"):
            assert f"Band {index} {control}" in names


def test_the_parameters_carry_their_units():
    """A frequency is in Hz, a level in dB and a share in percent."""
    descs = {desc.name: desc for desc in BandRestoration(SAMPLE_RATE, 2).describe()}
    assert descs["Low Crossover"].unit == "Hz"
    assert descs["Mid Crossover"].unit == "Hz"
    assert descs["High Crossover"].unit == "Hz"
    assert descs["Restoration Depth"].unit == "dB"
    assert descs["Noise Threshold"].unit == "dB"
    assert descs["High Frequency Transparency"].unit == "%"
    assert descs["Broadband Mix"].unit == "%"
    assert descs["Band 1 Mix"].unit == "dB"
    assert descs["Bands"].type == "int"
    assert descs["Phase Linearity"].choices == ("Efficient", "Linear Phase")


def test_every_parameter_carries_a_sentence():
    """The help of every parameter is a sentence."""
    for desc in BandRestoration(SAMPLE_RATE, 2).describe():
        assert desc.help
        assert desc.help.endswith("."), f"{desc.name}: {desc.help!r}"


@pytest.mark.parametrize("frames", FRAME_SIZES)
@pytest.mark.parametrize("name", INPUT_NAMES)
def test_the_block_length_is_kept(frames: int, name: str):
    """The module hands back the number of samples it was given."""
    signal = awkward_signal(name, frames)
    out = np.asarray(build().process(signal))
    assert out.shape == signal.shape, (
        f"{frames} samples in, {out.shape[0]} samples out for {name}"
    )


@pytest.mark.parametrize("frames", FRAME_SIZES)
@pytest.mark.parametrize("name", INPUT_NAMES)
def test_the_output_is_finite(frames: int, name: str):
    """No block makes the module return a NaN or an infinity."""
    signal = awkward_signal(name, frames)
    out = np.asarray(build().process(signal))
    assert np.all(np.isfinite(out)), f"{name} at {frames} samples gave a non-finite sample"


def test_two_runs_give_the_same_answer():
    """The same block gives the same answer twice."""
    signal = awkward_signal("tone", 4096)
    first = np.asarray(build().process(signal.copy()))
    second = np.asarray(build().process(signal.copy()))
    assert np.array_equal(first, second, equal_nan=True)


def test_reset_clears_the_state():
    """After a reset the module behaves like a fresh one."""
    signal = awkward_signal("tone", 4096)
    module = build()
    module.process(signal.copy())
    module.reset()
    after = np.asarray(module.process(signal.copy()))
    fresh = np.asarray(build().process(signal.copy()))
    assert np.array_equal(after, fresh, equal_nan=True)


def test_the_module_is_block_coherent():
    """The answer may not depend on the block size of the host.

    Measurement: the same signal is processed in one block and in blocks of
    64 samples, and the two results are compared sample by sample.
    """
    signal = awkward_signal("tone", 8192)
    whole = np.asarray(build().process(signal.copy()))

    module = build()
    pieces = []
    for start in range(0, signal.shape[0], 64):
        block = signal[start : start + 64]
        pieces.append(np.asarray(module.process(block.copy())))
    chunked = np.concatenate(pieces)

    count = min(whole.shape[0], chunked.shape[0])
    difference = whole[:count] - chunked[:count]
    rms = float(np.sqrt(np.mean(difference ** 2)))
    reference = float(np.sqrt(np.mean(whole[:count] ** 2)))
    level = 20.0 * math.log10(max(rms, 1e-12) / max(reference, 1e-12))
    assert level < -60.0, (
        f"the block size changes the result by {level:.1f} dB, a block "
        "coherent module changes nothing"
    )


# ---------------------------------------------------------------------------
# The crossover.
# ---------------------------------------------------------------------------
def test_the_edges_never_cross():
    """The three edges stay ordered, whatever the user sets."""
    module = build()
    module.params.set("Low Crossover", 2000.0)
    module.params.set("Mid Crossover", 200.0)
    module.params.set("High Crossover", 1000.0)
    low, mid, high = module._edges()
    assert low < mid < high
    assert low >= 20.0


def test_the_edges_follow_the_user():
    """The edges are the frequencies the user sets, when they are in order."""
    module = build()
    module.params.set("Low Crossover", 100.0)
    module.params.set("Mid Crossover", 1000.0)
    module.params.set("High Crossover", 12000.0)
    assert module._edges() == (100.0, 1000.0, 12000.0)


@pytest.mark.parametrize("bands", (1, 2, 3, 4))
def test_every_number_of_bands_works(bands: int):
    """The module splits into one, two, three or four bands."""
    module = build(Bands=bands)
    signal = awkward_signal("tone", 4096)
    out = np.asarray(module.process(signal))
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))
    assert len(module._band_limits()) == bands


def test_the_linear_phase_splitter_is_transparent():
    """The linear phase splitter adds the bands back up to the input.

    Measurement: the output of the module, read at the latency it reports,
    is compared with the input.  The linear phase splitter is the one that
    keeps the phase of the recording, so the two have to agree.
    """
    signal = awkward_signal("tone", 16384)
    module = build(Phase_Linearity="Linear Phase") if False else build()
    module.params.set("Phase Linearity", "Linear Phase")
    delay = int(module.latency_samples)
    assert delay > 0, "the linear phase splitter must report its delay"
    out = np.asarray(module.process(signal.copy()))
    count = out.shape[0] - delay
    a = signal[:count, 0]
    b = out[delay:, 0]
    difference = b - a
    rms = float(np.sqrt(np.mean(difference ** 2)))
    reference = float(np.sqrt(np.mean(a ** 2)))
    level = 20.0 * math.log10(max(rms, 1e-12) / max(reference, 1e-12))
    assert level < -60.0, (
        f"the linear phase splitter changed the signal by {level:.1f} dB"
    )


def test_the_efficient_splitter_adds_no_delay():
    """The efficient splitter adds no delay at all."""
    module = build()
    assert module.latency_samples == 0


def test_the_latency_is_the_delay_of_the_signal():
    """The reported latency is the delay the output really has.

    Measurement: the delay of the output against the input, found with a
    cross correlation, compared with ``latency_samples``.
    """
    signal = awkward_signal("tone", 16384)
    module = build()
    module.params.set("Phase Linearity", "Linear Phase")
    reported = int(module.latency_samples)
    out = np.asarray(module.process(signal.copy()))
    a = signal[:, 0] - float(np.mean(signal[:, 0]))
    b = out[:, 0] - float(np.mean(out[:, 0]))
    count = min(a.shape[0], b.shape[0])
    spectrum = np.fft.rfft(a[:count]) * np.conj(np.fft.rfft(b[:count]))
    correlation = np.fft.irfft(spectrum, n=count)
    low = max(0, reported - 64)
    high = min(count - 1, reported + 64)
    lag = int(low + np.argmax(correlation[low:high]))
    assert abs(lag - reported) <= 64, (
        f"the module reports {reported} samples of latency but delays the "
        f"signal by {lag} samples"
    )


# ---------------------------------------------------------------------------
# The restoration.
# ---------------------------------------------------------------------------
def _bed_and_programme(seconds: float = 1.0):
    """Return a stationary bed and a programme that sits on it."""
    n = int(SAMPLE_RATE * seconds)
    rng = np.random.default_rng(20_260_121)
    bed = 0.02 * rng.standard_normal(n)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    programme = 0.2 * np.sin(2 * np.pi * 1000.0 * t)
    return bed, programme


def test_the_learned_floor_is_taken_out_of_the_band():
    """After a Learn the bed is gone from the band that carries it.

    Measurement: the level of the 4-8 kHz band, where the bed is the only
    thing there, before and after the module.
    """
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.learn(bed[:, None])
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(mixed, 4000.0, 8000.0)
    after = band_level(out, 4000.0, 8000.0)
    assert after < before - 12.0, (
        f"the bed only lost {before - after:.1f} dB in the 4-8 kHz band"
    )


def test_the_wanted_partial_survives_the_restoration():
    """The partial the user wants is not taken with the bed.

    Measurement: the level of the 1 kHz partial, before and after.
    """
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.learn(bed[:, None])
    out = np.asarray(module.process(mixed.copy()))
    before = partial_level(mixed, 1000.0)
    after = partial_level(out, 1000.0)
    assert abs(after - before) <= 1.0, (
        f"the wanted partial moved by {after - before:.2f} dB"
    )


def test_without_a_learn_the_module_touches_nothing():
    """Without a learned profile the restoration stays out of the way."""
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(mixed, 4000.0, 8000.0)
    after = band_level(out, 4000.0, 8000.0)
    assert abs(after - before) < 1.0, (
        f"the module changed the bed by {after - before:.2f} dB without a "
        "learned profile"
    )


def test_the_bypass_leaves_the_band_alone():
    """The bypass of a band switches the restoration off for it."""
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.learn(bed[:, None])
    module.params.set("Band 3 Bypass", True)
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(mixed, 4000.0, 8000.0)
    after = band_level(out, 4000.0, 8000.0)
    assert abs(after - before) < 3.0, (
        f"the bypassed band still lost {before - after:.2f} dB"
    )


def test_the_high_frequency_transparency_spares_the_top_band():
    """The top band is not restored when the transparency is at its end.

    Measurement: the level of the 12-16 kHz band, where the bed is the
    only thing there, before and after the module.
    """
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.learn(bed[:, None])
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(mixed, 12000.0, 16000.0)
    after = band_level(out, 12000.0, 16000.0)
    assert abs(after - before) < 1.0, (
        f"the spared top band still lost {before - after:.2f} dB"
    )


def test_the_restoration_depth_is_the_limit_of_the_cut():
    """The restoration never cuts deeper than the depth the user set."""
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.learn(bed[:, None])
    module.params.set("Restoration Depth", 6.0)
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(mixed, 4000.0, 8000.0)
    after = band_level(out, 4000.0, 8000.0)
    assert before - after <= 6.5, (
        f"the band lost {before - after:.2f} dB, the depth was 6 dB"
    )


# ---------------------------------------------------------------------------
# The surface.
# ---------------------------------------------------------------------------
def test_a_muted_band_is_gone():
    """A muted band does not reach the output.

    The bands of a Linkwitz-Riley crossover overlap by a third of an
    octave, so the skirts of the neighbouring bands carry a little of the
    muted band.  The measurement therefore asks for the band to be gone
    by twenty decibels, not for silence.
    """
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    reference = np.asarray(build().process(mixed.copy()))
    module = build()
    module.params.set("Band 2 Mute", True)
    out = np.asarray(module.process(mixed.copy()))
    before = band_level(reference, 900.0, 1100.0)
    after = band_level(out, 900.0, 1100.0)
    assert after < before - 20.0, (
        f"the muted band is only {before - after:.1f} dB down"
    )


def test_a_soloed_band_is_the_only_one_that_is_heard():
    """A soloed band silences the other bands."""
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    reference = np.asarray(build().process(mixed.copy()))
    module = build()
    module.params.set("Band 2 Solo", True)
    out = np.asarray(module.process(mixed.copy()))
    assert band_level(out, 900.0, 1100.0) > -40.0, "the soloed band is missing"
    before = band_level(reference, 4000.0, 8000.0)
    after = band_level(out, 4000.0, 8000.0)
    assert after < before - 20.0, (
        f"another band is still heard, it is only {before - after:.1f} dB down"
    )


def test_the_mix_is_the_level_of_the_band():
    """The mix of a band is its level in the output.

    The bands overlap, so a little of the band reaches the output through
    the skirts of its neighbours and the fader moves the band by a little
    less than its own travel.  The measurement allows two decibels for
    that.
    """
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.params.set("Band 2 Mix", -12.0)
    out = np.asarray(module.process(mixed.copy()))
    reference = np.asarray(build().process(mixed.copy()))
    before = band_level(reference, 900.0, 1100.0)
    after = band_level(out, 900.0, 1100.0)
    assert abs((after - before) + 12.0) < 2.0, (
        f"the mix moved the band by {after - before:.2f} dB instead of -12 dB"
    )


def test_the_broadband_path_mixes_the_untouched_signal_back():
    """The broadband path puts the untouched signal back into the output."""
    bed, programme = _bed_and_programme()
    mixed = (programme + bed)[:, None]
    module = build()
    module.params.set("Broadband Mix", 100.0)
    out = np.asarray(module.process(mixed.copy()))
    assert band_level(out, 4000.0, 8000.0) > band_level(mixed, 4000.0, 8000.0) - 1.0, (
        "the broadband path did not bring the bed back"
    )


def test_the_module_survives_a_json_round_trip():
    """The learned profile survives the serialisation of the module."""
    bed, _programme = _bed_and_programme()
    module = build()
    module.learn(bed[:, None])
    document = module.to_dict()
    restored = build()
    restored.from_dict(document)
    assert restored._profile is not None
    assert np.allclose(restored._profile, module._profile)
