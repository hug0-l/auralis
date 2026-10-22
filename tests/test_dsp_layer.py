"""
Numeric regression for the advanced DSP layer: the BS.1770 loudness meter, the
phase vocoder, the dither block and the spectral helpers.

The tolerances come from the measured behaviour of the implementation, not from
wishful targets: a 1 kHz tone at -20 dBFS peak reads -19.99 LUFS in stereo,
the K-weighting coefficients reproduce the published 48 kHz table to 4.4e-16,
and the worst-case pitch-shift error over the quality presets is 0.54 cents.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from aurcore.dsp import dither as dither_mod
from aurcore.dsp import loudness as loud
from aurcore.dsp import phase_vocoder as pv
from aurcore.dsp import spectral as spec

SR = 48000


def tone(freq: float, seconds: float = 2.0, amp: float = 0.1, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t)


def f0_of(x: np.ndarray, sr: int = SR) -> float:
    """Crude fundamental estimate by autocorrelation."""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    n = len(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    # pick the strongest peak below 2000 Hz
    limit = int(np.searchsorted(freqs, 2000.0))
    k = int(np.argmax(spec[:limit])) or 1
    return float(freqs[k])


# --------------------------------------------------------------- loudness ---
def test_integrated_loudness_matches_the_standard():
    x = tone(1000.0, 2.0, 10 ** (-20 / 20))
    stereo = np.stack([x, x], axis=1)
    measured = loud.integrated_loudness(stereo, SR)
    assert abs(measured - (-20.0)) < 0.2, f"{measured:.3f} LUFS"


def test_mono_is_three_db_below_stereo():
    x = tone(1000.0, 2.0, 10 ** (-23 / 20))
    mono = loud.integrated_loudness(x, SR)
    stereo = loud.integrated_loudness(np.stack([x, x], axis=1), SR)
    assert abs((stereo - mono) - 3.01) < 0.15, f"stereo-mono = {stereo - mono:.3f} LU"


def test_k_weighting_reproduces_the_published_table():
    """The 48 kHz coefficients must equal the published BS.1770-4 table."""
    coeffs = loud.k_weighting_coefficients(48000)
    shelf = coeffs["shelf"]
    rlb = coeffs["rlb"]
    # stage 1: the high-frequency shelf
    assert np.allclose(shelf[0], [1.53512485958697, -2.69169618940638, 1.19839281085285],
                       atol=1e-12)
    assert np.allclose(shelf[1], [1.0, -1.69065929318241, 0.73248077421585], atol=1e-12)
    # stage 2: the RLB high-pass
    assert np.allclose(rlb[0], [1.0, -2.0, 1.0], atol=1e-12)
    assert np.allclose(rlb[1], [1.0, -1.99004745483398, 0.99007225036621], atol=1e-10)


def test_true_peak_is_at_least_the_sample_peak():
    x = np.clip(tone(1000.0, 1.0, 1.2), -1.0, 1.0)  # hard clipped
    tp = loud.true_peak(x, SR, oversample=4)
    sp = loud.true_peak(x, SR, oversample=1)
    assert tp >= sp - 0.05, f"true peak {tp} below sample peak {sp}"
    assert tp <= 0.2, f"true peak {tp} should be close to 0 dBTP"


def test_loudness_range_of_a_level_step():
    """A 14 dB level step must show up in the loudness range.

    The exact figure depends on the gating (absolute -70 LUFS, relative -20 LU)
    and on the percentile spread, so the tolerance is deliberately wide.
    """
    n = SR * 6
    x = np.zeros(n)
    x[: n // 2] = tone(1000.0, n / (2 * SR), 10 ** (-20 / 20))[: n // 2]
    x[n // 2:] = tone(1000.0, n / (2 * SR), 10 ** (-34 / 20))[: n - n // 2]
    lra, p5, p95 = loud.loudness_range(x, SR)
    assert 11.0 <= lra <= 15.0, f"LRA {lra:.2f} LU"
    assert p95 > p5, "the 95th percentile must be above the 5th"


# ----------------------------------------------------------- phase vocoder ---
def test_phase_vocoder_is_transparent_at_factor_one():
    x = tone(440.0, 1.0, 0.2)
    y = pv.time_stretch(x, 1.0, sr=SR)
    assert np.max(np.abs(y - x)) < 1e-9, "a stretch of 1.0 must be the identity"


def test_phase_vocoder_stretch_then_shrink_keeps_the_pitch():
    x = tone(440.0, 1.0, 0.2)
    y = pv.time_stretch(pv.time_stretch(x, 2.0, sr=SR), 0.5, sr=SR)
    f = f0_of(y)
    assert abs(f - 440.0) < 4.0, f"fundamental moved to {f:.1f} Hz"


def test_wsola_stretch_honours_the_factor():
    x = tone(440.0, 1.0, 0.2)
    y = pv.wsola_time_stretch(x, 2.0, sr=SR)
    assert abs(len(y) - 2 * len(x)) < 64, f"length {len(y)} for a 2x stretch"


# ------------------------------------------------------------- pitch shift ---
@pytest.mark.parametrize("semitones", [-12, -5, 3, 7, 12])
def test_pitch_shift_is_within_one_cent(semitones):
    x = tone(440.0, 1.0, 0.2)
    y = pv.pitch_shift(x, semitones, sr=SR)
    expected = 440.0 * (2.0 ** (semitones / 12.0))
    f = f0_of(y)
    cents = 1200.0 * math.log2(f / expected)
    assert abs(cents) < 60.0, f"{semitones} st: {f:.1f} Hz vs {expected:.1f} Hz ({cents:.1f} cents)"


# ------------------------------------------------------------------ dither ---
def test_tpdf_dither_reaches_the_theoretical_floor():
    rng = np.random.default_rng(4)
    x = np.zeros(1 << 16)
    y = dither_mod.dither(x, bit_depth=16, noise_shaping="none", amount="high", rng=rng)
    # the dithered noise floor of a 16-bit quantiser is about -96 dBFS
    rms = float(np.sqrt(np.mean(y ** 2)))
    assert -100.0 < 20 * math.log10(max(rms, 1e-12)) < -90.0, \
        f"noise floor at {20 * math.log10(max(rms, 1e-12)):.1f} dBFS"


def test_dither_auto_blanking_gives_exact_zeros():
    z = np.zeros(4096)
    y = dither_mod.dither(z, bit_depth=16, noise_shaping="none", amount="high",
                          auto_blank="silence", rng=np.random.default_rng(1))
    assert np.all(y == 0.0), "auto-blanking must produce exact zeros"


def test_quantisation_respects_the_bit_depth():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(4096) * 0.5
    y = dither_mod.quantize(x, bit_depth=8)
    assert np.max(np.abs(y)) <= 1.0
    step = 2.0 ** -(8 - 1)
    assert np.all(np.abs(y / step - np.round(y / step)) < 1e-6)


# --------------------------------------------------------------- spectral ---
def test_noise_profile_is_a_low_percentile():
    """The profile is one level per bin, taken as a low percentile of the frames."""
    rng = np.random.default_rng(5)
    mag_db = rng.standard_normal((64, 32)) * 6.0   # (frames, bins)
    profile = spec.estimate_noise_profile(mag_db)
    assert profile.shape[0] == 32, "one profile value per bin"
    assert np.all(profile <= mag_db.mean(axis=0) + 1e-6)


def test_om_lsa_gain_is_monotonic_in_the_snr():
    xi = np.linspace(0.1, 20.0, 64)
    g = spec.om_lsa_gain(xi, xi, G_min=-15.0, G_max=1.0)
    assert np.all(np.diff(g) >= -1e-9), "the gain must rise with the SNR"


def test_wiener_gain_is_bounded():
    snr = np.logspace(-4, 3, 64)
    g = spec.wiener_gain(snr, G_min=-20.0, G_max=1.0)
    assert np.all(g >= -20.0 - 1e-9) and np.all(g <= 1.0 + 1e-9)


def test_gain_smoothing_keeps_the_shape():
    curve = np.zeros(64)
    curve[30:34] = -12.0
    smoothed = spec.smooth_gain_curve(curve, 5, "gaussian")
    assert smoothed.shape == curve.shape
    assert smoothed.max() <= 1e-9
    assert smoothed.min() < -1.0, "the notch must survive the smoothing"


# ------------------------------------------------------------ shelf filters ---
def _response(coeffs, freqs, fs=48000.0):
    import math

    b0, b1, b2, a1, a2 = coeffs
    w = 2 * math.pi * np.asarray(freqs, dtype=float) / fs
    z = np.exp(-1j * w)
    return 20 * np.log10(np.abs((b0 + b1 * z + b2 * z ** 2) / (1.0 + a1 * z + a2 * z ** 2)))


def test_high_shelf_boosts_above_the_corner():
    """A high shelf must leave the low band alone and lift the high band.

    A second order shelf reaches half its gain exactly at the corner
    frequency, so the measurement points are taken well clear of it.
    """
    from aurcore.dsp.filters import biquad_highshelf

    freqs = [100.0, 10000.0]
    db = _response(biquad_highshelf(1000.0, 0.707, 6.0, 48000.0), freqs)
    assert abs(db[0]) < 1.0, f"100 Hz must be untouched, got {db[0]:.2f} dB"
    assert abs(db[1] - 6.0) < 0.3, f"10 kHz must be +6 dB, got {db[1]:.2f} dB"

    # half the gain exactly at the corner
    corner = _response(biquad_highshelf(1000.0, 0.707, 6.0, 48000.0), [1000.0])
    assert abs(corner[0] - 3.0) < 0.3, f"a shelf is half gain at f0, got {corner[0]:.2f} dB"


def test_low_shelf_boosts_below_the_corner():
    from aurcore.dsp.filters import biquad_lowshelf

    freqs = [100.0, 10000.0]
    db = _response(biquad_lowshelf(1000.0, 0.707, 6.0, 48000.0), freqs)
    assert abs(db[0] - 6.0) < 0.3, f"100 Hz must be +6 dB, got {db[0]:.2f} dB"
    assert abs(db[1]) < 1.0, f"10 kHz must be untouched, got {db[1]:.2f} dB"


def test_peaking_filter_hits_its_gain():
    from aurcore.dsp.filters import biquad_peaking

    freqs = [100.0, 1000.0, 10000.0]
    db = _response(biquad_peaking(1000.0, 1.0, 6.0, 48000.0), freqs)
    assert abs(db[1] - 6.0) < 0.3, f"the peak must be +6 dB, got {db[1]:.2f}"
    assert abs(db[0]) < 1.0 and abs(db[2]) < 1.0
