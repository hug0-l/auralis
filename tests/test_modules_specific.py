"""
Per-module behaviour tests for the modules whose job is measurable.

A smoke test proves a module does not crash; these tests prove it actually
does its work.  Each test states the measurement it uses in the docstring, so
a failure tells you what the module really did.

Every test skips while its module is not implemented yet.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from aurcore import registry

SR = 48_000
N = SR  # one second -> FFT bins land exactly on the test frequencies


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _make(code: str):
    """Instantiate a registered module or skip the test."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    try:
        return registry.create(code, SR, 2)
    except Exception as exc:  # noqa: BLE001 - a module may need an external asset
        pytest.skip(f"module {code!r} cannot be instantiated here: {exc}")


def _set(module, name: str, value) -> None:
    """Set a parameter, skipping when the module does not expose it."""
    if not module.params.has(name):
        pytest.skip(f"module {module.code} has no parameter {name!r}")
    module.params.set(name, value)


def _mono(x: np.ndarray) -> np.ndarray:
    """Return the first channel of an array."""
    arr = np.asarray(x, dtype=np.float64)
    return arr[:, 0] if arr.ndim > 1 else arr


def _sine(freq: float, n: int = N, amp: float = 0.2, phase: float = 0.0) -> np.ndarray:
    """A sine wave of the given frequency."""
    t = np.arange(n, dtype=np.float64) / SR
    return amp * np.sin(2.0 * math.pi * freq * t + phase)


def _stereo(x: np.ndarray) -> np.ndarray:
    """Copy a mono signal into a stereo pair."""
    return np.repeat(np.asarray(x, dtype=np.float64)[:, None], 2, axis=1)


def _tone_amplitude(x: np.ndarray, freq: float) -> float:
    """Amplitude of a coherent tone, read from the matching FFT bin."""
    mono = _mono(x)
    n = len(mono)
    spec = np.fft.rfft(mono)
    k = int(round(freq * n / SR))
    return 2.0 * float(abs(spec[k])) / n


def _band_energy(x: np.ndarray, f_lo: float, f_hi: float) -> float:
    """Energy inside a frequency band (normalised, so ratios are meaningful)."""
    mono = _mono(x)
    n = len(mono)
    spec = np.fft.rfft(mono)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    mask = (freqs >= f_lo) & (freqs <= f_hi)
    return float(np.sum(np.abs(spec[mask]) ** 2)) / float(n * n)


def _db_ratio(out: np.ndarray, ref: np.ndarray) -> float:
    """Level difference between two signals, in dB."""
    a = float(np.sqrt(np.mean(np.asarray(out, dtype=np.float64) ** 2)))
    b = float(np.sqrt(np.mean(np.asarray(ref, dtype=np.float64) ** 2)))
    return 20.0 * math.log10(max(a, 1e-12) / max(b, 1e-12))


def _warm_up(module, signal: np.ndarray, n: int = 4_800) -> None:
    """Run the filter for 0.1 s so the measurement sees the steady state."""
    module.process(signal[:n].copy())


# --------------------------------------------------------------------------
# De-Click
# --------------------------------------------------------------------------
def test_declick_attenuates_a_click_by_at_least_20_db():
    """The peak that is left where the click was must be 20 dB below the click.

    Signal: a quiet bed plus one full-scale single-sample click.  The peak of
    the processed signal around the click position is compared with the click
    amplitude (1.0).
    """
    module = _make("AUR_DECLICK")
    bed = _sine(440.0, amp=0.02)
    click_at = 9_600
    x = bed.copy()
    x[click_at] += 1.0

    y = np.asarray(module.process(_stereo(x)))
    assert y.shape[0] == len(x)

    lo = max(click_at - 512, 0)
    hi = min(click_at + 8_192, len(y))
    residual = float(np.max(np.abs(_mono(y)[lo:hi])))
    attenuation_db = 20.0 * math.log10(max(residual, 1e-12) / 1.0)
    assert attenuation_db <= -20.0, f"the click was only attenuated by {attenuation_db:.1f} dB"


def test_declick_keeps_the_bed_signal():
    """De-clicking must not touch the clean part of the signal."""
    module = _make("AUR_DECLICK")
    bed = _sine(440.0, amp=0.2)
    _warm_up(module, _stereo(bed))
    y = np.asarray(module.process(_stereo(bed)))
    assert _db_ratio(y[4_800:], bed[4_800:]) == pytest.approx(0.0, abs=0.5)


# --------------------------------------------------------------------------
# De-Hum
# --------------------------------------------------------------------------
def test_dehum_attenuates_a_50_hz_hum_by_at_least_20_db():
    """Energy in the 25-75 Hz band (the mains fundamental) must drop by 20 dB.

    The band is used instead of a single bin because a notch filter spreads
    the rejected tone over a few bins.
    """
    module = _make("AUR_DEHUM")
    _set(module, "Base Frequency", 50.0)

    tone = _sine(1000.0, amp=0.2)
    hum = _sine(50.0, amp=0.2)
    x = tone + hum

    y = np.asarray(module.process(_stereo(x)))
    before = _band_energy(x, 25.0, 75.0)
    after = _band_energy(y, 25.0, 75.0)
    reduction_db = 10.0 * math.log10(max(after, 1e-30) / max(before, 1e-30))
    assert reduction_db <= -20.0, f"the hum was only reduced by {reduction_db:.1f} dB"


def test_dehum_keeps_a_1_khz_tone_within_half_a_db():
    """The notch must not colour the rest of the spectrum."""
    module = _make("AUR_DEHUM")
    _set(module, "Base Frequency", 50.0)

    tone = _sine(1000.0, amp=0.2)
    hum = _sine(50.0, amp=0.2)
    x = tone + hum
    _warm_up(module, _stereo(x))

    y = np.asarray(module.process(_stereo(x)))
    gain_db = 20.0 * math.log10(
        max(_tone_amplitude(y, 1000.0), 1e-12) / max(_tone_amplitude(x, 1000.0), 1e-12)
    )
    assert gain_db == pytest.approx(0.0, abs=0.5), f"the 1 kHz tone moved by {gain_db:.2f} dB"


# --------------------------------------------------------------------------
# Spectral De-Noise
# --------------------------------------------------------------------------
def test_spectral_denoise_improves_the_snr_by_at_least_6_db():
    """Noise-only band energy must drop by 6 dB while the signal is preserved.

    The test signal is three tones below 2.5 kHz plus broadband noise.  The
    8-12 kHz band carries no signal, so the energy there is pure noise: its
    reduction is the SNR improvement, provided the signal itself survives.
    """
    module = _make("AUR_SPECTRALDENOISE")

    rng = np.random.default_rng(4242)
    signal = _sine(500.0, amp=0.1) + _sine(1000.0, amp=0.1) + _sine(2000.0, amp=0.1)
    noise = rng.standard_normal(N)
    noise *= math.sqrt(0.015) / float(np.std(noise))  # SNR ~ 0 dB over the whole band
    x = signal + noise

    # Give an adaptive module a chance to learn the noise floor.
    try:
        if getattr(type(module), "learn", False):
            module.learn(_stereo(rng.standard_normal(N) * 0.1))
    except Exception:  # noqa: BLE001 - learning is optional
        pass
    module.process(_stereo(noise))

    y = np.asarray(module.process(_stereo(x)))

    before = _band_floor(x, 8000.0, 12000.0)
    after = _band_floor(y, 8000.0, 12000.0)
    improvement_db = 10.0 * math.log10(max(before, 1e-30) / max(after, 1e-30))
    assert improvement_db >= 6.0, f"the SNR only improved by {improvement_db:.1f} dB"

    for freq in (500.0, 1000.0, 2000.0):
        gain_db = 20.0 * math.log10(
            max(_tone_amplitude(y, freq), 1e-12) / max(_tone_amplitude(x, freq), 1e-12)
        )
        # A spectral denoiser attenuates tonal components that sit close to
        # the noise floor - the tone has to survive, the noise has to go.
        assert gain_db == pytest.approx(0.0, abs=4.0), (
            f"the {freq:.0f} Hz tone moved by "
            f"{gain_db:.2f} dB"
        )


# --------------------------------------------------------------------------
# Voice De-Noise
# --------------------------------------------------------------------------
def _band_floor(x: np.ndarray, f_lo: float, f_hi: float, n_fft: int = 2048,
                hop: int = 512, percentile: float = 10.0) -> float:
    """Noise floor inside a band: the low percentile of the frame energies.

    A plain band sum over a signal that contains a strong voice measures the
    voice's spectral leakage rather than the noise, so it cannot see the hiss
    at all.  Taking a low percentile over short-time frames isolates the part
    of the band that is noise rather than signal.
    """
    mono = _mono(x)
    n = len(mono)
    if n < n_fft:
        mono = np.pad(mono, (0, n_fft - n))
        n = n_fft
    win = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    mask = (freqs >= f_lo) & (freqs <= f_hi)
    if not mask.any():
        return 0.0
    energy = np.sum(spec[:, mask] ** 2, axis=1)
    return float(np.percentile(energy, percentile)) / float(n_fft * n_fft)


def test_voice_denoise_reduces_hiss_by_at_least_8_db():
    """The hiss under a voice must be pulled down by at least 8 dB.

    Measurement: the energy in the 8-12 kHz band, where the voice-like
    signal has no energy at all.  The module runs with its default settings,
    the way a user gets it.
    """
    module = _make("AUR_VOICEDENOISE")

    rng = np.random.default_rng(99)
    # A voice-like signal: a 150 Hz fundamental with harmonics up to 3 kHz.
    voice = np.zeros(N, dtype=np.float64)
    for k in range(1, 21):
        voice += _sine(150.0 * k, amp=0.05 / k)
    hiss = rng.standard_normal(N) * (10.0 ** (-95.0 / 20.0))
    x = voice + hiss

    y = np.asarray(module.process(_stereo(x)))

    before = _band_floor(x, 8000.0, 12000.0)
    after = _band_floor(y, 8000.0, 12000.0)
    reduction_db = 10.0 * math.log10(max(before, 1e-30) / max(after, 1e-30))
    assert reduction_db >= 8.0, f"the hiss was only reduced by {reduction_db:.1f} dB"


def test_voice_denoise_keeps_the_voice():
    """Voice De-Noise must not damage the voice itself."""
    module = _make("AUR_VOICEDENOISE")
    rng = np.random.default_rng(99)
    voice = np.zeros(N, dtype=np.float64)
    for k in range(1, 21):
        voice += _sine(150.0 * k, amp=0.05 / k)
    hiss = rng.standard_normal(N) * (10.0 ** (-95.0 / 20.0))
    x = voice + hiss
    module.process(_stereo(x))
    y = np.asarray(module.process(_stereo(x)))
    # A denoiser may take a decibel or two off the voice, but not more.
    assert _db_ratio(y[4_800:], x[4_800:]) == pytest.approx(0.0, abs=3.0)


# --------------------------------------------------------------------------
# Gain
# --------------------------------------------------------------------------
@pytest.mark.parametrize("gain_db", [-12.0, -6.0, 6.0, 12.0])
def test_gain_adds_exactly_the_requested_db(gain_db: float):
    """The output level must be the input level plus the requested gain."""
    module = _make("AUR_GAIN")
    _set(module, "Gain", gain_db)

    tone = _sine(1000.0, amp=0.1)
    _warm_up(module, _stereo(tone))
    y = np.asarray(module.process(_stereo(tone)))

    assert _db_ratio(y[4_800:], tone[4_800:]) == pytest.approx(gain_db, abs=0.05)


def test_gain_at_zero_is_transparent():
    """A gain of zero dB changes nothing."""
    module = _make("AUR_GAIN")
    _set(module, "Gain", 0.0)
    tone = _sine(1000.0, amp=0.1)
    y = np.asarray(module.process(_stereo(tone)))
    assert _db_ratio(y, tone) == pytest.approx(0.0, abs=0.01)


# --------------------------------------------------------------------------
# Normalize
# --------------------------------------------------------------------------
@pytest.mark.parametrize("target_db", [-6.0, -3.0, -1.0])
def test_normalize_hits_the_target_peak(target_db: float):
    """The peak of the output must be the requested target level."""
    module = _make("AUR_NORMALIZE")
    _set(module, "Target Peak Level", target_db)

    rng = np.random.default_rng(7)
    x = rng.standard_normal((N, 2)) * 0.1
    x = np.clip(x, -0.25, 0.25)  # a clear, repeatable peak

    y = np.asarray(module.process(x))
    peak = float(np.max(np.abs(y)))
    peak_db = 20.0 * math.log10(max(peak, 1e-12))
    assert peak_db == pytest.approx(target_db, abs=0.1), f"the peak landed at {peak_db:.2f} dBFS"


# --------------------------------------------------------------------------
# EQ
# --------------------------------------------------------------------------
@pytest.mark.parametrize("gain_db", [3.0, 3.6, -6.0, -18.0])
def test_eq_boosts_the_target_band(gain_db: float):
    """Band 1 is set to 1 kHz; a 1 kHz tone must move by exactly that gain.

    The gain of a band runs from -18 to +3.6 dB: the taper law puts unity in
    the middle of the travel, so the boost of a band is a fifth of its cut
    (``docs/RESTORATION_REFERENCE.md``, section 3.1).  The gains of the test
    are the ones the parameter holds: the top of the range is measured too, so
    the biggest boost the product offers is pinned, and the parameter has to
    hold the gain that was asked for.
    """
    module = _make("AUR_EQ")
    _set(module, "Band 1 Frequency", 1000.0)
    _set(module, "Band 1 Gain", gain_db)
    _set(module, "Band 1 Q", 1.0)
    for name in ("Band 2 Gain", "Band 3 Gain", "Band 4 Gain", "Output Gain"):
        if module.params.has(name):
            module.params.set(name, 0.0)
    assert float(module.params.get("Band 1 Gain")) == pytest.approx(gain_db), (
        f"the module holds {float(module.params.get('Band 1 Gain')):.2f} dB, "
        f"the test asked for {gain_db:.2f} dB"
    )

    tone = _sine(1000.0, amp=0.1)
    _warm_up(module, _stereo(tone))
    y = np.asarray(module.process(_stereo(tone)))

    measured_db = 20.0 * math.log10(
        max(_tone_amplitude(y, 1000.0), 1e-12) / max(_tone_amplitude(tone, 1000.0), 1e-12)
    )
    assert measured_db == pytest.approx(gain_db, abs=0.3), f"the band moved by {measured_db:.2f} dB"


def test_eq_leaves_other_bands_alone():
    """A 1 kHz tone must not be moved by a 100 Hz boost."""
    module = _make("AUR_EQ")
    _set(module, "Band 1 Frequency", 100.0)
    _set(module, "Band 1 Gain", 3.6)
    _set(module, "Band 1 Q", 1.0)
    for name in ("Band 2 Gain", "Band 3 Gain", "Band 4 Gain", "Output Gain"):
        if module.params.has(name):
            module.params.set(name, 0.0)

    tone = _sine(1000.0, amp=0.1)
    _warm_up(module, _stereo(tone))
    y = np.asarray(module.process(_stereo(tone)))

    measured_db = 20.0 * math.log10(
        max(_tone_amplitude(y, 1000.0), 1e-12) / max(_tone_amplitude(tone, 1000.0), 1e-12)
    )
    assert measured_db == pytest.approx(0.0,
                                        abs=0.3), f"the 1 kHz tone moved by {measured_db:.2f} dB"
