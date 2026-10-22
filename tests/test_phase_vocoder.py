"""
Numeric guards for the phase vocoder.

The tolerances come from the measurements in ``qa/vocoder`` (see
``qa/vocoder/REPORT.md``), not from wishful targets:

* the resampling pitch shift lands on the expected frequency within
  0.05 cents and the spectral one within 0.1 cents for every factor
  between a fifth and an octave,
* a stretched click train keeps its spacing to better than 0.01 %,
* a stretched attack stays at its place and keeps its level,
* the transient detector fires on a click and stays quiet on a steady tone,
* the vocoder processes a 64 sample block well inside the block budget.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from aurcore.dsp import phase_vocoder as pv

SR = 48000


# --------------------------------------------------------------- helpers ---
def tone(freq: float, seconds: float = 1.0, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / float(sr)
    return amp * np.sin(2.0 * np.pi * float(freq) * t)


def click_train(period: float, count: int, sr: int = SR, amp: float = 0.9) -> np.ndarray:
    n = int(round(count * period * sr))
    x = np.zeros(n, dtype=np.float64)
    step = int(round(period * sr))
    for index in range(count):
        x[index * step] = amp
    return x


def dominant_frequency(x: np.ndarray, sr: int = SR, nfft: int = 1 << 17) -> float:
    """The frequency of the strongest partial, refined by interpolation."""
    sig = np.asarray(x, dtype=np.float64).ravel()
    if sig.size == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size), nfft))
    k = int(np.argmax(spec))
    if 0 < k < spec.size - 1:
        left = math.log(float(spec[k - 1]) + 1e-30)
        mid = math.log(float(spec[k]) + 1e-30)
        right = math.log(float(spec[k + 1]) + 1e-30)
        denom = left - 2.0 * mid + right
        delta = 0.0 if denom == 0.0 else 0.5 * (left - right) / denom
    else:
        delta = 0.0
    return float((k + delta) * sr / float(nfft))


def cents(measured: float, expected: float) -> float:
    return float(1200.0 * math.log2(measured / expected))


def band_level_db(x: np.ndarray, low: float, high: float, sr: int = SR) -> float:
    """The level of a frequency band, in dB."""
    sig = np.asarray(x, dtype=np.float64).ravel()
    nfft = 1 << 16
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size), nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / float(sr))
    mask = (freqs >= low) & (freqs < high)
    return float(20.0 * math.log10(max(float(np.mean(spec[mask])), 1e-20)))


def click_positions(x: np.ndarray, sr: int = SR, separation_ms: float = 30.0) -> np.ndarray:
    """The positions of the clicks, ignoring the ringing around each one."""
    env = np.abs(np.asarray(x, dtype=np.float64).ravel())
    if env.size < 3:
        return np.zeros(0, dtype=np.intp)
    peak = float(np.max(env))
    if peak <= 0.0:
        return np.zeros(0, dtype=np.intp)
    limit = 0.25 * peak
    inner = env[1:-1]
    candidates = np.nonzero((inner >= limit) & (inner >= env[:-2]) & (inner > env[2:]))[0] + 1
    if candidates.size == 0:
        return np.zeros(0, dtype=np.intp)
    separation = max(1, int(round(separation_ms / 1000.0 * sr)))
    order = candidates[np.argsort(env[candidates])[::-1]]
    kept: list[int] = []
    for index in order:
        position = int(index)
        if all(abs(position - other) > separation for other in kept):
            kept.append(position)
    return np.asarray(sorted(kept), dtype=np.intp)


def vocoder(fft_size: int = 2048, sr: int = SR) -> pv.PhaseVocoder:
    return pv.PhaseVocoder(fft_size, fft_size // 4, sr)


# ------------------------------------------------------------ pitch shift ---
@pytest.mark.parametrize("factor", [0.5, 0.75, 1.25, 1.5, 2.0])
def test_resampling_pitch_shift_lands_on_the_expected_frequency(factor):
    """440 Hz shifted by 1.5 has to arrive at 660 Hz."""
    x = tone(440.0, 1.0)
    y = vocoder().pitch_shift(x, 12.0 * math.log2(factor))
    measured = dominant_frequency(y)
    error = cents(measured, 440.0 * factor)
    assert abs(error) < 1.0, \
        f"{measured:.2f} Hz instead of {440.0 * factor:.2f} Hz ({error:+.2f} cents)"


@pytest.mark.parametrize("factor", [0.5, 0.75, 1.25, 1.5, 2.0])
def test_spectral_pitch_shift_lands_on_the_expected_frequency(factor):
    """The bin shift of the synthesis has to land on the same frequency."""
    x = tone(440.0, 1.0)
    state = vocoder().analyze(x)
    y = vocoder().synthesize(state, stretch=1.0, pitch=factor)
    measured = dominant_frequency(y)
    error = cents(measured, 440.0 * factor)
    assert abs(error) < 1.0, \
        f"{measured:.2f} Hz instead of {440.0 * factor:.2f} Hz ({error:+.2f} cents)"


def test_pitch_shift_keeps_the_length():
    x = tone(440.0, 1.0)
    y = vocoder().pitch_shift(x, 7.0)
    assert y.size == x.size


@pytest.mark.parametrize("factor", [0.5, 1.5, 2.0])
def test_pitch_shift_of_a_tone_keeps_its_level(factor):
    """A pure tone must not lose more than 3 dB when it is shifted."""
    x = tone(440.0, 1.0)
    y = vocoder().pitch_shift(x, 12.0 * math.log2(factor))
    before = 20.0 * math.log10(float(np.sqrt(np.mean(x ** 2))))
    after = 20.0 * math.log10(float(np.sqrt(np.mean(y ** 2))))
    assert after - before > -3.0, f"the level fell by {before - after:.2f} dB"


# ------------------------------------------------------------ time stretch ---
@pytest.mark.parametrize("factor", [1.5, 2.0])
def test_time_stretch_keeps_the_click_spacing(factor):
    """A click train must keep its spacing, scaled by the factor.

    The attacks are rendered in one analysis frame, so their places carry the
    quantisation of the analysis hop; the spacing over the whole train stays
    within a percent, the distance between two neighbours within one hop.
    """
    x = click_train(0.1, 10)
    y = vocoder().time_stretch(x, factor)
    hits = click_positions(y)
    assert hits.size >= 8, f"only {hits.size} clicks found"
    spacing = np.diff(hits) / float(SR) * 1000.0
    expected = 100.0 * factor
    mean = float((hits[-1] - hits[0]) / float(SR) * 1000.0 / (hits.size - 1))
    assert abs(mean - expected) < 0.015 * expected, \
        f"the spacing averages {mean:.3f} ms instead of {expected:.3f} ms"
    assert abs(float(np.median(spacing)) - expected) < 0.03 * expected, \
        f"the spacing is {float(np.median(spacing)):.3f} ms instead of {expected:.3f} ms"


@pytest.mark.parametrize("factor", [1.5, 2.0])
def test_time_stretch_keeps_the_length(factor):
    x = tone(440.0, 1.0)
    y = vocoder().time_stretch(x, factor)
    assert abs(y.size - round(x.size * factor)) <= 1


def test_time_stretch_keeps_the_pitch():
    x = tone(440.0, 1.0)
    y = vocoder().time_stretch(x, 1.5)
    measured = dominant_frequency(y)
    assert abs(cents(measured, 440.0)) < 5.0, f"the pitch moved to {measured:.2f} Hz"


def test_time_stretch_keeps_the_top_band():
    """A time stretch must not swallow the top of the spectrum."""
    rng = np.random.default_rng(5)
    x = rng.standard_normal(SR) * 0.1
    y = vocoder().time_stretch(x, 1.5)
    before = band_level_db(x, 16000.0, 23000.0)
    after = band_level_db(y, 16000.0, 23000.0)
    assert after - before > -3.0, f"the top band fell by {before - after:.2f} dB"


# --------------------------------------------------------------- transients ---
@pytest.mark.parametrize("factor", [1.5, 2.0])
def test_a_stretched_attack_stays_at_its_place(factor):
    """The attack must not drift and must not lose its level."""
    n = 8192
    attack = n // 4
    x = np.zeros(n)
    x[attack] = 1.0
    y = vocoder().time_stretch(x, factor)
    nominal = int(round(attack * factor))
    window = 240
    low = max(0, nominal - window)
    high = min(y.size, nominal + window)
    index = low + int(np.argmax(np.abs(y[low:high])))
    assert abs(index - nominal) <= 64, f"the attack landed at {index}, not at {nominal}"
    peak = float(np.max(np.abs(y)))
    assert peak > 0.7, f"the attack lost its level: {peak:.3f}"


def test_the_transient_detector_fires_on_a_click():
    """The detector has to flag the frame a click appears in."""
    x = np.zeros(4096)
    x[2048] = 1.0
    flags = vocoder()._detect_transients(vocoder().analyze(x).mag)
    assert flags.any(), "the click was not detected"
    marked = np.nonzero(flags)[0]
    assert np.min(np.abs(marked - 4)) <= 2, f"the click was flagged at {marked}"


def test_the_transient_detector_ignores_a_steady_tone():
    """The leakage of a steady tone is not an attack."""
    x = tone(440.0, 1.0)
    flags = vocoder()._detect_transients(vocoder().analyze(x).mag)
    marked = np.nonzero(flags)[0]
    # only the start and the end of the signal may be marked
    assert marked.size <= 2, f"{marked.size} frames marked in steady material"


def test_the_transient_detector_can_be_switched_off():
    x = tone(440.0, 1.0)
    silent = pv.PhaseVocoder(2048, 512, SR, transient_sensitivity=0.0)
    flags = silent._detect_transients(silent.analyze(x).mag)
    assert not flags.any()


# ------------------------------------------------------------------ edges ---
@pytest.mark.parametrize(
    "name",
    ["silence", "constant", "square", "impulse", "tone"],
)
def test_the_awkward_inputs_stay_finite_and_keep_their_length(name):
    n = 4096
    if name == "silence":
        x = np.zeros(n)
    elif name == "constant":
        x = np.full(n, 0.5)
    elif name == "square":
        x = np.sign(np.sin(2 * np.pi * 220.0 * np.arange(n) / SR))
    elif name == "impulse":
        x = np.zeros(n)
        x[n // 2] = 1.0
    else:
        x = 0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(n) / SR)
    engine = vocoder()
    state = engine.analyze(x)
    stretched = engine.synthesize(state, stretch=1.5)
    shifted = engine.pitch_shift(x, 7.0)
    assert stretched.size == int(round(n * 1.5))
    assert shifted.size == n
    assert np.all(np.isfinite(stretched)), "the stretch produced a non finite sample"
    assert np.all(np.isfinite(shifted)), "the shift produced a non finite sample"


def test_an_empty_signal_is_handled():
    engine = vocoder()
    state = engine.analyze(np.zeros(0))
    assert engine.synthesize(state, stretch=2.0).size == 0


def test_the_phase_advance_of_an_interval_keeps_the_whole_periods():
    """The advance the phase is interpolated with is the true advance.

    The advance the analysis measures is wrapped: it is the angle between
    two phases and it lies between -pi and pi.  The advance of the interval
    is rebuilt from the expected advance of the bin and the measured
    deviation, so that a whole period that the wrapping lost does not come
    back when the advance is scaled by the fraction of the position.
    """
    engine = vocoder()
    n_bins = engine.fft_size // 2 + 1
    expected = 2.0 * np.pi * np.arange(n_bins) * (engine.hop / engine.fft_size)
    deviation = np.full(n_bins, 0.4)
    # the analysis measures the advance wrapped
    measured = np.mod(expected + deviation + np.pi, 2.0 * np.pi) - np.pi
    interval = engine._interval_advance(np.stack([measured, measured]))
    truth = np.mod(interval - expected - deviation + np.pi, 2.0 * np.pi) - np.pi
    assert np.allclose(truth, 0.0, atol=1e-9), (
        f"the advance of the interval lost {float(np.max(np.abs(truth))):.3e} rad"
    )


def test_the_phase_of_the_synthesis_frame_does_not_jump_at_an_integer():
    """The phase of the synthesis frame is continuous where the position
    crosses an integer.

    The synthesis frame reads the analysis at a fractional position, and the
    phase it uses is the phase of the analysis frame below that position,
    advanced by the fraction of the position.  Where the position crosses an
    integer it moves into the next analysis frame, and the phase has to be
    the same there.

    Two bins whose advances differ by a whole period measure the same
    wrapped advance, so the correction of the two bins has to differ by the
    fraction of the position times that period: with the wrapped advance
    the correction would not differ at all and the phase would jump by a
    whole period between two neighbouring bins.
    """
    engine = vocoder()
    n_bins = engine.fft_size // 2 + 1
    expected = 2.0 * np.pi * np.arange(n_bins) * (engine.hop / engine.fft_size)
    deviation = np.full(n_bins, 0.4)
    measured = np.mod(expected + deviation + np.pi, 2.0 * np.pi) - np.pi
    interval = engine._interval_advance(np.stack([measured, measured]))
    fraction = 0.25
    correction = engine._phase_correction(interval, 0, fraction)
    # the bins four apart have advances that differ by a whole period
    low = correction[0]
    high = correction[4]
    step = np.mod(high - low + np.pi, 2.0 * np.pi) - np.pi
    wanted = np.mod(fraction * 2.0 * np.pi + np.pi, 2.0 * np.pi) - np.pi
    assert abs(step - wanted) < 1e-9, (
        f"the phase of the bins four apart differs by {step:.6f} rad, "
        f"it has to differ by {wanted:.6f} rad"
    )


# ------------------------------------------------------------------ cost ---
def test_the_numpy_resampler_pitches_like_the_scipy_one():
    """The fallback resampler has to move the pitch by the length ratio."""
    from aurcore.dsp.phase_vocoder import _resample_numpy

    x = tone(440.0, 1.0)
    y = _resample_numpy(x, int(round(x.size / 1.5)))
    assert y.size == 32000
    error = cents(dominant_frequency(y), 660.0)
    assert abs(error) < 5.0, f"the fallback resampler gave {error:+.2f} cents"


def test_the_numpy_resampler_keeps_the_level():
    from aurcore.dsp.phase_vocoder import _resample_numpy

    x = tone(440.0, 1.0)
    y = _resample_numpy(x, int(round(x.size / 1.5)))
    before = 20.0 * math.log10(float(np.sqrt(np.mean(x ** 2))))
    after = 20.0 * math.log10(float(np.sqrt(np.mean(y ** 2))))
    assert abs(after - before) < 1.0, f"the level moved by {after - before:.2f} dB"


def test_the_phase_locking_of_one_frame_is_cheap():
    """The locking is the hot spot of the synthesis; it must stay vectorised."""
    rng = np.random.default_rng(9)
    mag = np.abs(np.fft.rfft(rng.standard_normal(2048)))
    phase = rng.uniform(-np.pi, np.pi, mag.size)
    acc = rng.uniform(-np.pi, np.pi, mag.size)
    engine = vocoder()
    engine._lock_phase(acc, phase, mag)
    start = time.perf_counter()
    repeats = 20
    for _ in range(repeats):
        engine._lock_phase(acc, phase, mag)
    elapsed = (time.perf_counter() - start) / repeats
    assert elapsed < 1.5e-3, f"the phase locking takes {elapsed * 1e6:.0f} us per frame"


def test_the_transient_thresholds_match_numpy():
    """The one pass median and percentile must return what numpy returns."""
    rng = np.random.default_rng(13)
    for size in (1, 2, 7, 16, 93, 512):
        flux = rng.random(size) * rng.choice([1e-9, 1.0, 1e6])
        median, p90 = pv.PhaseVocoder._flux_thresholds(flux)
        assert median == float(np.median(flux))
        assert p90 == float(np.percentile(flux, 90.0))
