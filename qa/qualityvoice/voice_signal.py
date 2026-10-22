"""
qa.qualityvoice.voice_signal — the synthetic voice and the measurement of it.

A harmonic stack with a formant envelope is *not* a voice: it has no
fundamental jitter, no formant movement, no spectral tilt and no pauses.
This module builds a signal that has those properties and, more importantly,
**measures** them, so the harness can state how close the synthetic voice is
to the properties a real voice has.

Properties of the synthetic voice
---------------------------------
``f0``
    The fundamental is never perfectly constant.  The contour carries a slow
    vibrato and a fast, bounded jitter (cycle-to-cycle period perturbation).
``harmonics``
    The partials sit at exact multiples of the (instantaneous) fundamental and
    their amplitudes follow a formant envelope: a sum of resonances.
``tilt``
    The source spectrum falls with frequency.  A glottal source falls by
    6 to 16 dB per octave; the design uses 12 dB per octave.
``pauses``
    The voice is not continuous.  Syllables are separated by silence, so the
    signal has a pause structure and its level varies by tens of dB.

The measurement
---------------
:func:`measure_voice` reports the properties of a signal that is given to it:

============================  ==========================================
``f0_mean_hz``               the mean fundamental of the voiced frames
``f0_std_pct``               the spread of the fundamental, in percent
``period_jitter_pct``        the mean relative change of the period between
                              neighbouring analysis frames (the jitter)
``harmonic_spacing_hz``      the distance between the spectral peaks, which
                              has to be the fundamental
``formants_hz``              the fitted formant centres (F1, F2, F3)
``source_tilt_db_per_octave`` the fitted slope of the source spectrum
``envelope_peaks_hz``        the peaks of the smoothed harmonic envelope
``pause_count``              the number of silent stretches
``hf_leak_db``               how far the voice's energy above 5 kHz sits
                              below its total energy
============================  ==========================================

The reference ranges a voice has to fall into are in :data:`VOICE_REFERENCE`.
They are general facts about human speech, not measurements of a particular
recording.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "SR",
    "Formant",
    "VoiceSpec",
    "VOICE_SPEC",
    "VOICE_REFERENCE",
    "synth_voice",
    "measure_voice",
    "harmonic_response",
]

#: Sample rate of every measurement.
SR = 48_000

#: Frequency above which the synthetic voice carries no energy at all.  The
#: fricative bursts are band limited to this frequency so that the 5-14 kHz
#: measurement band carries background only; without that the hiss
#: measurement and the voice measurement would not be independent.
FRICATIVE_HIGH_HZ = 3_500.0

#: The frequency above which the leakage into the noise band is reported.
LEAK_HZ = 5_000.0


# ---------------------------------------------------------------------------
# the design
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Formant:
    """One resonance of the vocal tract."""

    #: Centre frequency in Hz.
    freq: float
    #: Bandwidth in Hz.
    bandwidth: float
    #: Relative strength of the resonance.
    gain: float


@dataclass(frozen=True)
class VoiceSpec:
    """The design of the synthetic voice.

    Every field is a design value; :func:`measure_voice` measures what the
    rendered signal actually has, and the two are compared in the report.
    """

    #: Mean fundamental in Hz.
    f0: float = 110.0
    #: Cycle-to-cycle period perturbation, in percent.
    jitter_pct: float = 0.35
    #: Depth of the slow vibrato, in percent.
    vibrato_pct: float = 1.0
    #: Rate of the vibrato in Hz.
    vibrato_hz: float = 4.8
    #: Slope of the radiated spectrum in dB per octave.  A glottal source
    #: falls by 12 dB per octave and the radiation at the lips adds 6 dB per
    #: octave, so the spectrum that reaches the microphone falls by about
    #: 6 dB per octave.
    tilt_db_per_octave: float = -6.0
    #: Slope of the glottal source alone, in dB per octave.  It is reported
    #: next to the radiated tilt because the source tilt is the number the
    #: literature quotes.
    source_tilt_db_per_octave: float = -12.0
    #: The resonances of the vocal tract.
    formants: tuple[Formant, ...] = (
        Formant(520.0, 70.0, 1.00),
        Formant(1480.0, 95.0, 0.72),
        Formant(2480.0, 130.0, 0.42),
        Formant(3450.0, 220.0, 0.22),
    )
    #: Number of syllables.
    syllables: int = 5
    #: Length of a syllable in seconds.
    syllable_s: float = 0.17
    #: Length of the pause between two syllables in seconds.
    pause_s: float = 0.07
    #: Silence before the first syllable, in seconds.
    lead_in_s: float = 0.05
    #: How many syllables carry a fricative burst.
    fricative_count: int = 2
    #: Length of a fricative burst in seconds.
    fricative_s: float = 0.035
    #: Level of a fricative burst below the syllable it belongs to, in dB.
    fricative_db: float = -24.0
    #: Lowest frequency of a fricative burst in Hz.
    fricative_low_hz: float = 300.0
    #: Highest frequency of a fricative burst in Hz.
    fricative_high_hz: float = FRICATIVE_HIGH_HZ
    #: Seed of the random parts (the jitter and the fricatives).
    seed: int = 20_260_102
    #: Level of the voiced parts in dBFS RMS.
    level_dbfs: float = -20.0


#: The design the harness uses.
VOICE_SPEC = VoiceSpec()

#: The properties a real voice has.  The ranges are general facts about human
#: speech; the synthetic voice is compared against them by
#: :func:`voice_property_table`.
VOICE_REFERENCE: dict[str, tuple[float, float, str, str]] = {
    "f0_mean_hz": (
        85.0,
        255.0,
        "Hz",
        "an adult speaker spans roughly 85-255 Hz; one synthetic voice can "
        "only sit inside that band",
    ),
    "period_jitter_pct": (
        0.0,
        1.0,
        "%",
        "the cycle-to-cycle period perturbation of a sustained vowel stays "
        "below 1 %",
    ),
    "harmonic_spacing_hz": (
        85.0,
        255.0,
        "Hz",
        "the spacing of the partials is the fundamental",
    ),
    "formant_f1_hz": (250.0, 900.0, "Hz", "the first formant of a vowel"),
    "formant_f2_hz": (800.0, 2800.0, "Hz", "the second formant of a vowel"),
    "formant_f3_hz": (2200.0, 3200.0, "Hz", "the third formant of a vowel"),
    "formant_f4_hz": (2800.0, 4200.0, "Hz", "the fourth formant of a vowel"),
    "tilt_db_per_octave": (
        -12.0,
        -4.0,
        "dB/octave",
        "the long term average spectrum of speech falls by 4 to 12 dB per "
        "octave above the first formant",
    ),
    "pause_fraction": (
        0.05,
        0.60,
        "-",
        "speech is not continuous: a fifth to three fifths of it is a pause",
    ),
    "hf_leak_db": (
        -200.0,
        -40.0,
        "dB",
        "the energy of a voice above 5 kHz sits at least 40 dB below its "
        "total energy",
    ),
}


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------
def harmonic_response(freqs: np.ndarray, formants: tuple[Formant, ...]) -> np.ndarray:
    """Return the formant envelope at ``freqs`` (linear amplitude).

    Parameters
    ----------
    freqs:
        The frequencies to evaluate, in Hz.
    formants:
        The resonances of the vocal tract.

    Returns
    -------
    numpy.ndarray
        The amplitude of the envelope at every frequency.
    """
    f = np.asarray(freqs, dtype=np.float64)
    out = np.zeros_like(f)
    for fm in formants:
        out += fm.gain / (1.0 + ((f - fm.freq) / fm.bandwidth) ** 2)
    return out


def _band_noise(
    n: int, lo_hz: float, hi_hz: float, rng: np.random.Generator, sample_rate: int = SR
) -> np.ndarray:
    """Noise that lives between ``lo_hz`` and ``hi_hz``."""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    size = int(n)
    spectrum = rng.standard_normal(size // 2 + 1) + 1j * rng.standard_normal(size // 2 + 1)
    freqs = np.fft.rfftfreq(size, 1.0 / float(sample_rate))
    edge = max(50.0, 0.25 * (hi_hz - lo_hz))
    weight = np.zeros_like(freqs)
    inside = (freqs >= lo_hz) & (freqs <= hi_hz)
    weight[inside] = 1.0
    low = (freqs >= lo_hz - edge) & (freqs < lo_hz)
    high = (freqs > hi_hz) & (freqs <= hi_hz + edge)
    if np.any(low):
        weight[low] = 0.5 * (1.0 + np.cos(np.pi * (freqs[low] - (lo_hz - edge)) / edge))
    if np.any(high):
        weight[high] = 0.5 * (1.0 - np.cos(np.pi * (freqs[high] - hi_hz) / edge))
    spectrum *= weight
    return np.fft.irfft(spectrum, n=size)


def _fade(n: int, ramp: int) -> np.ndarray:
    """A window that fades in and out over ``ramp`` samples."""
    out = np.ones(int(n), dtype=np.float64)
    r = int(min(ramp, max(1, n // 2)))
    out[:r] = 0.5 * (1.0 - np.cos(np.pi * np.arange(r) / float(max(r - 1, 1))))
    out[-r:] = 0.5 * (1.0 + np.cos(np.pi * np.arange(r) / float(max(r - 1, 1))))
    return out


def _segment_plan(spec: VoiceSpec, sample_rate: int) -> list[tuple[str, int, int]]:
    """The syllable and pause layout of the voice."""
    plan: list[tuple[str, int, int]] = []
    cursor = int(spec.lead_in_s * sample_rate)
    syllable = int(spec.syllable_s * sample_rate)
    pause = int(spec.pause_s * sample_rate)
    for _ in range(spec.syllables):
        plan.append(("voiced", cursor, cursor + syllable))
        cursor += syllable
        plan.append(("pause", cursor, cursor + pause))
        cursor += pause
    return plan


def voice_segments(
    spec: VoiceSpec = VOICE_SPEC, sample_rate: int = SR
) -> list[tuple[str, int, int]]:
    """The syllable and pause layout of the voice, in samples.

    Every entry is ``("voiced" | "pause", start, end)``.  The harness uses it
    to put a breath into a pause and a mouth click next to a syllable.
    """
    return _segment_plan(spec, sample_rate)


def synth_voice(spec: VoiceSpec = VOICE_SPEC, sample_rate: int = SR) -> np.ndarray:
    """Render the synthetic voice.

    Parameters
    ----------
    spec:
        The design of the voice.
    sample_rate:
        The sample rate of the result.

    Returns
    -------
    numpy.ndarray
        The voice, shape ``(n_samples,)``, normalised to ``spec.level_dbfs``
        RMS over its voiced parts.
    """
    rng = np.random.default_rng(spec.seed)
    plan = _segment_plan(spec, sample_rate)
    n = plan[-1][2] if plan else int(0.1 * sample_rate)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)

    # --- the fundamental contour -----------------------------------------
    vibrato = (spec.vibrato_pct / 100.0) * np.sin(2.0 * math.pi * spec.vibrato_hz * t + 0.7)
    step = max(2, int(0.030 * sample_rate))
    coarse = rng.standard_normal(int(n / step) + 3) * (spec.jitter_pct / 100.0)
    jitter = np.interp(np.arange(n), np.arange(coarse.size) * step, coarse)
    f0 = spec.f0 * (1.0 + vibrato + jitter)
    phase = 2.0 * math.pi * np.cumsum(f0) / float(sample_rate)

    # --- the amplitude envelope ------------------------------------------
    env = np.zeros(n, dtype=np.float64)
    attack = max(1, int(0.008 * sample_rate))
    release = max(1, int(0.025 * sample_rate))
    for kind, start, end in plan:
        if kind != "voiced":
            continue
        env[start:end] = _fade(end - start, max(attack, release))

    # --- the harmonic stack ------------------------------------------------
    # The design quotes the radiated tilt in dB per octave of the power
    # spectrum.  The amplitude therefore falls with the exponent
    # ``tilt / (20 log10 2)``.
    exponent = spec.tilt_db_per_octave / (20.0 * math.log10(2.0))
    phases = rng.uniform(0.0, 2.0 * math.pi, size=64)
    out = np.zeros(n, dtype=np.float64)
    k_max = min(60, int(0.45 * sample_rate / max(spec.f0 * 0.96, 1.0)))
    for k in range(1, k_max + 1):
        fk = k * f0
        amp = env * np.power(fk / spec.f0, exponent) * harmonic_response(fk, spec.formants)
        if float(np.max(amp)) < 1e-7:
            continue
        out += amp * np.sin(k * phase + phases[k - 1])

    # --- the fricative bursts ---------------------------------------------
    voiced_parts = [(s, e) for kind, s, e in plan if kind == "voiced"]
    for index in range(min(spec.fricative_count, len(voiced_parts))):
        start, end = voiced_parts[index]
        f_start = start + int(0.006 * sample_rate)
        f_end = min(end, f_start + int(spec.fricative_s * sample_rate))
        if f_end <= f_start:
            continue
        local = out[start:end]
        rms = float(np.sqrt(np.mean(local * local)))
        if rms <= 0.0:
            continue
        burst = _band_noise(
            f_end - f_start, spec.fricative_low_hz, spec.fricative_high_hz, rng, sample_rate
        )
        burst_rms = float(np.sqrt(np.mean(burst * burst))) or 1.0
        burst *= (rms * 10.0 ** (spec.fricative_db / 20.0)) / burst_rms
        burst *= _fade(burst.size, max(1, int(0.003 * sample_rate)))
        out[f_start:f_end] += burst

    # --- level -------------------------------------------------------------
    voiced = np.zeros(n, dtype=bool)
    for kind, start, end in plan:
        if kind == "voiced":
            voiced[start:end] = True
    if np.any(voiced):
        rms = float(np.sqrt(np.mean(out[voiced] ** 2)))
        if rms > 0.0:
            out *= (10.0 ** (spec.level_dbfs / 20.0)) / rms
    return out


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def _mono(x: np.ndarray) -> np.ndarray:
    """First channel of ``x`` as a 1-D array."""
    arr = np.asarray(x, dtype=np.float64)
    return arr[:, 0] if arr.ndim > 1 else arr.ravel()


def _f0_track(
    x: np.ndarray,
    sample_rate: int,
    frame: int = 2048,
    hop: int = 512,
    f_min: float = 60.0,
    f_max: float = 400.0,
    min_corr: float = 0.35,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate the fundamental of every frame.

    Returns the frequency per frame and a mask of the voiced frames.
    """
    if x.size < frame:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=bool)
    window = np.hanning(frame)
    n_frames = 1 + (x.size - frame) // hop
    view = np.lib.stride_tricks.sliding_window_view(x, frame)[::hop][:n_frames] * window
    n_fft = 1 << int(math.ceil(math.log2(2.0 * frame)))
    spectrum = np.fft.rfft(view, n=n_fft, axis=1)
    correlation = np.fft.irfft(np.abs(spectrum) ** 2, n=n_fft, axis=1)[:, :frame]
    zero = np.maximum(correlation[:, :1], 1e-30)
    normalised = correlation / zero
    lag_min = max(2, int(sample_rate / f_max))
    lag_max = min(frame - 2, int(sample_rate / f_min))
    window_corr = normalised[:, lag_min : lag_max + 1]
    if window_corr.shape[1] < 3:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=bool)
    index = np.argmax(window_corr, axis=1) + lag_min
    rows = np.arange(n_frames)
    best = normalised[rows, index]
    left = normalised[rows, index - 1]
    right = normalised[rows, index + 1]
    denom = left - 2.0 * best + right
    delta = np.where(
        np.abs(denom) > 1e-12, 0.5 * (left - right) / np.where(denom == 0.0, 1.0, denom), 0.0
    )
    period = np.maximum(index + delta, 1e-6)
    f0 = float(sample_rate) / period
    voiced = best >= min_corr
    # An autocorrelation can lock onto the wrong period (a double or a half)
    # and a noise burst produces a period at all.  A frame whose estimate
    # leaves the neighbourhood of the local median is not a voiced frame.
    for _ in range(2):
        if not np.any(voiced):
            break
        local = _masked_running_median(f0, voiced, 9)
        usable = np.isfinite(local)
        refined = voiced.copy()
        refined[usable] &= np.abs(f0[usable] - local[usable]) <= 0.15 * np.maximum(
            local[usable], 1e-6
        )
        if np.array_equal(refined, voiced):
            break
        voiced = refined
    return f0, voiced


def _running_median(values: np.ndarray, width: int) -> np.ndarray:
    """Median of ``values`` over a centred window of ``width`` samples."""
    arr = np.asarray(values, dtype=np.float64)
    w = int(max(3, width))
    if arr.size == 0:
        return arr.copy()
    if arr.size < w:
        return np.full_like(arr, float(np.median(arr)))
    pad = w // 2
    padded = np.pad(arr, (pad, pad), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, w)
    return np.median(view, axis=-1)


def _masked_running_median(values: np.ndarray, mask: np.ndarray, width: int) -> np.ndarray:
    """Median of the values that are marked, over a centred window."""
    arr = np.asarray(values, dtype=np.float64)
    keep = np.asarray(mask, dtype=bool)
    out = np.full_like(arr, np.nan)
    half = int(max(1, width // 2))
    for index in range(arr.size):
        lo = max(0, index - half)
        hi = min(arr.size, index + half + 1)
        selected = arr[lo:hi][keep[lo:hi]]
        if selected.size:
            out[index] = float(np.median(selected))
    return out


def _local_peaks(values: np.ndarray) -> np.ndarray:
    """Indices of the local maxima of ``values``."""
    if values.size < 3:
        return np.zeros(0, dtype=np.intp)
    inner = values[1:-1]
    mask = (inner > values[:-2]) & (inner >= values[2:])
    return np.nonzero(mask)[0] + 1


def _spectrum_peaks(
    spectrum_db: np.ndarray,
    freqs: np.ndarray,
    lo: float,
    hi: float,
    prominence_db: float = 6.0,
    width: int = 31,
) -> np.ndarray:
    """Frequencies of the spectral peaks that stand out in a band.

    ``width`` is the width of the running median that acts as the local
    spectral floor.  It has to be wider than the distance between two
    partials, otherwise the floor sits on the partials themselves and no
    peak stands out of it.
    """
    band = (freqs >= lo) & (freqs <= hi)
    if not np.any(band):
        return np.zeros(0, dtype=np.float64)
    values = spectrum_db[band]
    band_freqs = freqs[band]
    floor = _running_median(values, width)
    peaks = _local_peaks(values)
    if peaks.size == 0:
        return np.zeros(0, dtype=np.float64)
    keep = (values[peaks] - floor[peaks]) > prominence_db
    return band_freqs[peaks[keep]]


def _comb_spacing(
    spectrum_db: np.ndarray, freqs: np.ndarray, lo: float, hi: float
) -> tuple[float, float]:
    """The spacing of the partials, from the autocorrelation of the spectrum.

    A harmonic stack is a comb: the autocorrelation of its spectrum has a
    peak at the spacing of the teeth.  Unlike the peak picking this does not
    depend on every partial being strong enough to be found.
    """
    band = (freqs >= lo) & (freqs <= hi)
    if np.count_nonzero(band) < 16:
        return 0.0, 0.0
    values = 10.0 ** (spectrum_db[band] / 20.0)
    values = values - float(np.mean(values))
    corr = np.correlate(values, values, mode="full")[values.size - 1 :]
    corr = corr / max(float(corr[0]), 1e-30)
    step = float(freqs[1] - freqs[0])
    min_lag = max(1, int(round(60.0 / step)))
    max_lag = min(corr.size - 2, int(round(400.0 / step)))
    if max_lag <= min_lag:
        return 0.0, 0.0
    window = corr[min_lag : max_lag + 1]
    index = int(np.argmax(window)) + min_lag
    peak = float(corr[index])
    if index <= 0 or index >= corr.size - 1:
        return 0.0, peak
    left = float(corr[index - 1])
    centre = float(corr[index])
    right = float(corr[index + 1])
    denom = left - 2.0 * centre + right
    delta = 0.5 * (left - right) / denom if abs(denom) > 1e-15 else 0.0
    lag = index + delta
    if lag <= 0.0:
        return 0.0, peak
    return float(lag * step), peak


def _harmonic_amplitudes(
    spectrum_db: np.ndarray, freqs: np.ndarray, f0: float, count: int
) -> tuple[np.ndarray, np.ndarray]:
    """Amplitude of every harmonic of the voice.

    The amplitude of a partial is the square root of the **total** power of
    its tooth, not the height of its peak: the partial moves with the
    vibrato, so its tooth is smeared over a few bins, and the total power
    does not depend on how wide the tooth is.  The window around the partial
    is narrower than the distance to the next one, so the two do not overlap.

    Returns the harmonic frequencies and their amplitudes (linear).
    """
    power = np.power(10.0, spectrum_db / 10.0)
    out_f: list[float] = []
    out_a: list[float] = []
    half = 0.45 * f0
    for k in range(1, count + 1):
        centre = k * f0
        if centre > freqs[-1]:
            break
        band = (freqs >= centre - half) & (freqs <= centre + half)
        if not np.any(band):
            continue
        total = float(np.sum(power[band]))
        out_f.append(centre)
        out_a.append(math.sqrt(max(total, 0.0)))
    return np.asarray(out_f, dtype=np.float64), np.asarray(out_a, dtype=np.float64)


def _fit_resonances(
    freqs: np.ndarray,
    amps: np.ndarray,
    f0: float,
    bandwidths: tuple[float, ...],
    ranges: tuple[tuple[float, float], ...],
) -> dict[str, object]:
    """Fit the source tilt and the formant centres to the harmonic amplitudes.

    The model is ``A(f) = (f/f0)^t * sum_j g_j / (1 + ((f - F_j)/BW_j)^2)``.
    The exponents ``t`` and the centres ``F_j`` are found by a coordinate
    descent, the gains ``g_j`` by a linear least squares at every step.
    """
    n_res = len(bandwidths)
    if freqs.size < n_res + 4:
        return {}
    exponents = np.arange(-3.0, 0.05, 0.1)
    centres: list[float] = []

    def score(candidate: list[float], exponent: float) -> tuple[float, np.ndarray]:
        base = np.power(freqs / max(f0, 1e-6), exponent)
        target = amps / np.maximum(base, 1e-30)
        matrix = np.stack(
            [1.0 / (1.0 + ((freqs - c) / bandwidths[i]) ** 2) for i, c in enumerate(candidate)],
            axis=1,
        )
        coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
        coef = np.maximum(coef, 0.0)
        model = base * (matrix @ coef)
        error = float(
            np.sqrt(np.mean((model - amps) ** 2)) / max(float(np.sqrt(np.mean(amps**2))), 1e-30)
        )
        return error, coef

    best_error = float("inf")
    best_coef = np.zeros(n_res)
    best_exponent = float(exponents[0])
    for _ in range(2):
        for slot in range(n_res):
            lo, hi = ranges[slot]
            if slot < len(centres):
                span = (hi - lo) / 20.0
                grid = np.linspace(centres[slot] - 2.0 * span, centres[slot] + 2.0 * span, 9)
                grid = grid[(grid >= lo) & (grid <= hi)]
            else:
                grid = np.linspace(lo, hi, 21)
            for candidate in grid:
                trial = list(centres)
                while len(trial) <= slot:
                    trial.append(float(np.mean(ranges[len(trial)])))
                trial[slot] = float(candidate)
                for exponent in exponents:
                    error, coef = score(trial, float(exponent))
                    if error < best_error:
                        best_error = error
                        best_coef = coef
                        best_exponent = float(exponent)
                        centres = list(trial)
    levels = 20.0 * np.log10(np.maximum(best_coef, 1e-12))
    if best_coef.size > 1:
        levels = levels - float(np.max(levels))
    return {
        "formants_hz": [float(c) for c in centres],
        "formant_levels_db": [float(v) for v in levels],
        "tilt_db_per_octave": float(best_exponent * 20.0 * math.log10(2.0)),
        "fit_error_db": float(20.0 * math.log10(max(best_error, 1e-9))),
    }


def _envelope_peaks(
    freqs: np.ndarray, amps: np.ndarray, f0: float, lo: float = 150.0, hi: float = 4_000.0
) -> list[float]:
    """Peaks of the smoothed harmonic envelope (a cross check of the fit)."""
    if freqs.size < 8:
        return []
    grid = np.geomspace(lo, hi, 600)
    values = np.interp(grid, freqs, 20.0 * np.log10(np.maximum(amps, 1e-30)))
    width = max(2, int(round(2.4 * f0 * grid.size / (hi - lo) * math.log(hi / lo))))
    width = min(width, values.size - 1)
    kernel = np.ones(width) / float(width)
    smoothed = np.convolve(values, kernel, mode="same")
    peaks = _local_peaks(smoothed)
    return [float(grid[p]) for p in peaks]


def measure_voice(x: np.ndarray, sample_rate: int = SR) -> dict[str, object]:
    """Measure the properties of a voice-like signal.

    Parameters
    ----------
    x:
        The signal, shape ``(n_samples,)`` or ``(n_samples, channels)``.
    sample_rate:
        The sample rate of the signal.

    Returns
    -------
    dict
        The measured properties; the keys are documented at the top of this
        module.
    """
    signal = _mono(x)
    out: dict[str, object] = {}
    if signal.size < 4096:
        out["error"] = "the signal is too short to measure"
        return out

    # --- level and pause structure -----------------------------------------
    frame = 512
    hop = 256
    n_frames = 1 + (signal.size - frame) // hop
    view = np.lib.stride_tricks.sliding_window_view(signal, frame)[::hop][:n_frames]
    rms = np.sqrt(np.mean(view * view, axis=1))
    peak = float(np.max(rms)) if rms.size else 0.0
    out["level_dbfs"] = float(20.0 * math.log10(max(peak, 1e-12)))
    threshold = peak * 10.0 ** (-40.0 / 20.0)
    silent = rms < threshold
    runs = 0
    total = 0
    index = 0
    while index < silent.size:
        if silent[index]:
            start = index
            while index < silent.size and silent[index]:
                index += 1
            runs += 1
            total += (index - start) * hop
        else:
            index += 1
    out["pause_count"] = int(runs)
    out["pause_total_s"] = float(total / float(sample_rate))
    out["pause_fraction"] = float(total / float(signal.size))

    # --- the fundamental ----------------------------------------------------
    f0_track, voiced = _f0_track(signal, sample_rate)
    if f0_track.size:
        keep = voiced.copy()
        keep[:2] = False
        keep[-2:] = False
        f0_voiced = f0_track[keep] if np.any(keep) else f0_track[voiced]
    else:
        f0_voiced = np.zeros(0, dtype=np.float64)
    if f0_voiced.size == 0:
        out["error"] = "no voiced frame was found"
        return out
    f0_mean = float(np.mean(f0_voiced))
    out["f0_mean_hz"] = f0_mean
    out["f0_std_pct"] = float(np.std(f0_voiced) / max(f0_mean, 1e-9) * 100.0)
    out["f0_min_hz"] = float(np.min(f0_voiced))
    out["f0_max_hz"] = float(np.max(f0_voiced))
    out["voiced_frames"] = int(f0_voiced.size)
    out["voiced_fraction"] = float(f0_voiced.size / max(f0_track.size, 1))

    # the jitter: the mean relative change of the period between two frames
    periods = float(sample_rate) / np.maximum(f0_voiced, 1e-6)
    if periods.size > 1:
        steps = np.abs(np.diff(periods)) / max(float(np.mean(periods)), 1e-9)
        out["period_jitter_pct"] = float(np.mean(steps) * 100.0)
    else:
        out["period_jitter_pct"] = 0.0

    # --- the long term average spectrum -------------------------------------
    # Only the *periodically voiced* frames take part in the average: a
    # fricative burst is broadband noise, and it would bury the weak upper
    # partials in the average even though it lives at another moment in
    # time.  The frames are selected with the autocorrelation of the frame
    # itself, so the selection does not need to know anything about the
    # design.
    ltas_frame = 4096
    ltas_hop = 512
    ltas_nfft = 16384
    window = np.hanning(ltas_frame)
    frames = np.lib.stride_tricks.sliding_window_view(signal, ltas_frame)[::ltas_hop]
    _, ltas_voiced = _f0_track(signal, sample_rate, frame=ltas_frame, hop=ltas_hop)
    if ltas_voiced.size == frames.shape[0] and np.any(ltas_voiced):
        selected = frames[ltas_voiced]
    else:
        frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
        loud = frame_rms > peak * 10.0 ** (-35.0 / 20.0)
        selected = frames[loud] if np.any(loud) else frames
    selected = selected * window
    power = np.abs(np.fft.rfft(selected, n=ltas_nfft, axis=1)) ** 2
    mean_power = np.mean(power, axis=0)
    ltas_db = 10.0 * np.log10(np.maximum(mean_power, 1e-30))
    freqs = np.fft.rfftfreq(ltas_nfft, 1.0 / float(sample_rate))
    out["ltas_frames"] = int(selected.shape[0])

    # --- the leakage into the noise band ------------------------------------
    total_power = float(np.sum(mean_power))
    high = freqs >= LEAK_HZ
    high_power = float(np.sum(mean_power[high]))
    out["hf_leak_db"] = float(10.0 * math.log10(max(high_power, 1e-30) / max(total_power, 1e-30)))

    # --- the harmonic spacing ------------------------------------------------
    bin_hz = float(freqs[1] - freqs[0])
    floor_width = max(15, int(round(2.5 * f0_mean / max(bin_hz, 1e-9))))
    peaks = _spectrum_peaks(ltas_db, freqs, 80.0, 4_000.0, prominence_db=6.0, width=floor_width)
    spacing, comb_peak = _comb_spacing(ltas_db, freqs, 80.0, 2_000.0)
    out["harmonic_spacing_hz"] = float(spacing)
    out["harmonic_spacing_comb_peak"] = float(comb_peak)
    out["harmonic_spacing_error_pct"] = float(
        abs(spacing - f0_mean) / max(f0_mean, 1e-9) * 100.0
    )
    out["harmonic_count"] = int(peaks.size)
    if peaks.size > 1:
        out["harmonic_spacing_peaks_hz"] = float(np.median(np.diff(peaks)))
    else:
        out["harmonic_spacing_peaks_hz"] = 0.0

    # --- the formants and the tilt -------------------------------------------
    count = int(min(48, int(freqs[-1] / max(f0_mean, 1e-6))))
    hz, amps = _harmonic_amplitudes(ltas_db, freqs, f0_mean, count)
    if hz.size >= 8:
        fit = _fit_resonances(
            hz,
            amps,
            f0_mean,
            bandwidths=(90.0, 120.0, 180.0, 260.0),
            ranges=(
                (250.0, 900.0),
                (900.0, 2_600.0),
                (2_100.0, 3_800.0),
                (3_000.0, 4_500.0),
            ),
        )
        out.update(fit)
        out["envelope_peaks_hz"] = _envelope_peaks(hz, amps, f0_mean)
        low = hz <= 400.0
        if np.count_nonzero(low) >= 3:
            slope = np.polyfit(np.log2(hz[low]), 20.0 * np.log10(amps[low]), 1)[0]
            out["envelope_slope_below_400hz_db_per_octave"] = float(slope)
    return out


def voice_property_table(measured: dict[str, object]) -> list[tuple[str, str, str, str]]:
    """Compare the measured properties with the reference ranges.

    Returns a list of ``(property, measured, reference, verdict)`` rows.
    """
    values: dict[str, object] = {
        key: measured[key] for key in VOICE_REFERENCE if key in measured
    }
    formants = measured.get("formants_hz")
    if isinstance(formants, (list, tuple)) and formants:
        for index in range(4):
            key = f"formant_f{index + 1}_hz"
            if key in VOICE_REFERENCE and index < len(formants):
                values[key] = float(formants[index])
    rows: list[tuple[str, str, str, str]] = []
    for key, (lo, hi, unit, note) in VOICE_REFERENCE.items():
        if key not in values:
            continue
        value = values[key]
        if isinstance(value, (list, tuple)):
            text = ", ".join(f"{float(v):.0f}" for v in value)
            numbers = [float(v) for v in value]
        elif isinstance(value, (int, np.integer)):
            text = str(int(value))
            numbers = [float(value)]
        else:
            text = f"{float(value):.2f}"
            numbers = [float(value)]
        inside = all(lo <= n <= hi for n in numbers)
        verdict = "inside" if inside else "OUTSIDE"
        rows.append((key, f"{text} {unit}", f"{lo:g} .. {hi:g} {unit} ({note})", verdict))
    return rows
