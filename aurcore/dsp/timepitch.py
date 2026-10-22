"""
aurcore.dsp.timepitch — time and pitch control on top of the phase vocoder.

The module offers the user facing entry points of the "Time & Pitch" tool:

* :func:`time_stretch` — change the duration without changing the pitch,
* :func:`pitch_shift` — change the pitch without changing the duration,
* :func:`adaptive_window` — pick the analysis window for a given quality or
  window length.

Both operations accept mono and multi-channel audio in the ``(n, channels)``
layout used by the engine.
"""
from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from .phase_vocoder import PhaseVocoder, _make_stft

__all__ = [
    "WindowSpec",
    "adaptive_window",
    "time_stretch",
    "pitch_shift",
    "QUALITY_PRESETS",
]


class WindowSpec(NamedTuple):
    """Analysis window description.

    Attributes
    ----------
    fft_size:
        FFT size in samples.
    hop:
        Analysis hop in samples.
    window_ms:
        Length of the analysis window in milliseconds.
    """

    fft_size: int
    hop: int
    window_ms: float


#: Per quality settings: analysis length, hop divisor, phase locking,
#: phase coherence and transient sensitivity.
#:
#: The phase locking stays enabled for every quality: it anchors the phase of
#: every spectral peak, which keeps the pitch of a shifted signal exact even
#: with the short windows of the low quality setting.
QUALITY_PRESETS: dict[str, dict] = {
    "low": {
        "window_ms": 20.0,
        "hop_div": 2,
        "phase_lock": True,
        "phase_coherence": 1.0,
        "transient_sensitivity": 1.0,
    },
    "medium": {
        "window_ms": 37.0,
        "hop_div": 4,
        "phase_lock": True,
        "phase_coherence": 1.0,
        "transient_sensitivity": 1.0,
    },
    "high": {
        "window_ms": 74.0,
        "hop_div": 4,
        "phase_lock": True,
        "phase_coherence": 1.0,
        "transient_sensitivity": 1.2,
    },
}

_MIN_WINDOW_MS = 8.0
_MAX_WINDOW_MS = 200.0


def _quality_settings(quality: str) -> dict:
    key = str(quality).strip().lower()
    if key not in QUALITY_PRESETS:
        raise ValueError(f"unknown quality {quality!r} (use one of {sorted(QUALITY_PRESETS)})")
    return QUALITY_PRESETS[key]


def _median(values: np.ndarray) -> float:
    """The median of ``values``, with one partition.

    ``numpy.median`` copies the array, partitions it and, for an even number
    of values, averages the two middle ones.  The helper does the same work
    without the wrapper: the values it returns are the ones ``np.median``
    returns, down to the last bit, and it costs one partition instead of a
    copy, a partition and a mean.
    """
    array = np.asarray(values)
    count = int(array.size)
    if count == 0:
        return float("nan")
    if count % 2:
        middle = count // 2
        return float(np.partition(array, middle)[middle])
    low = (count - 1) // 2
    high = count // 2
    part = np.partition(array, [low, high])
    return 0.5 * (float(part[low]) + float(part[high]))


def _estimate_transient_spacing(x: np.ndarray, sr: int) -> float:
    """Median distance between transients in seconds (``inf`` when unknown).

    Stationary material has no usable estimate, in which case the caller
    keeps the requested window length.
    """
    sig = np.asarray(x, dtype=np.float64)
    if sig.ndim > 1:
        sig = sig.max(axis=1)
    n = sig.size
    if n < 1024:
        return float("inf")
    block = max(1, int(0.005 * sr))
    frames = n // block
    if frames < 8:
        return float("inf")
    # The envelope of every block.  The largest absolute value of a block is
    # the larger of its largest and the opposite of its smallest value, so
    # the absolute value of the whole signal never has to be materialised.
    blocks = sig[: frames * block].reshape(frames, block)
    env = np.maximum(blocks.max(axis=1), -blocks.min(axis=1))
    d = np.diff(env)
    if d.size == 0:
        return float("inf")
    span = float(np.max(env) - np.min(env))
    if span < 0.02 * float(np.max(env) + 1e-12):
        # no dynamic range: the material is stationary
        return float("inf")
    median = _median(d)
    spread = float(np.std(d))
    threshold = max(median + 4.0 * spread, 0.02 * span)
    onsets = np.nonzero(d > threshold)[0]
    if onsets.size < 3:
        return float("inf")
    spacing = np.diff(onsets) * block / sr
    spacing = spacing[spacing > 0.02]
    if spacing.size < 2:
        return float("inf")
    return _median(spacing)


def adaptive_window(
    window_ms: float | None = None,
    sr: int = 48000,
    quality: str = "medium",
    x=None,
) -> WindowSpec:
    """Choose the analysis window for a time/pitch operation.

    Parameters
    ----------
    window_ms:
        Requested window length in milliseconds.  ``None`` uses the default
        of the quality preset.
    sr:
        Sample rate in Hz.
    quality:
        ``'low'``, ``'medium'`` or ``'high'``.
    x:
        Optional audio.  When given, the window is shortened so that it does
        not cover more than one transient, which keeps attacks crisp.

    Returns
    -------
    WindowSpec
        The FFT size, the hop and the effective window length.
    """
    settings = _quality_settings(quality)
    ms = float(settings["window_ms"] if window_ms is None else window_ms)
    ms = float(np.clip(ms, _MIN_WINDOW_MS, _MAX_WINDOW_MS))

    if x is not None:
        spacing = _estimate_transient_spacing(x, sr)
        if np.isfinite(spacing):
            ms = min(ms, max(_MIN_WINDOW_MS, spacing * 1000.0 * 1.5))

    fft_size = int(2 ** math.ceil(math.log2(max(sr * ms / 1000.0, 32.0))))
    fft_size = int(np.clip(fft_size, 64, 1 << 16))
    hop = max(1, fft_size // int(settings["hop_div"]))
    return WindowSpec(fft_size, hop, ms)


def _build_vocoder(
    spec: WindowSpec,
    sr: int,
    settings: dict,
    transient_sensitivity: float | None = None,
    phase_coherence: float | None = None,
):
    sens = (
        settings["transient_sensitivity"]
         if transient_sensitivity is None else float(transient_sensitivity)
    )
    coherence = settings["phase_coherence"] if phase_coherence is None else float(phase_coherence)
    return PhaseVocoder(
        spec.fft_size,
        spec.hop,
        sr,
        window="hann",
        phase_lock=bool(settings["phase_lock"]),
        phase_coherence=float(np.clip(coherence, 0.0, 1.0)),
        transient_sensitivity=sens,
    )


def time_stretch(
    x,
    factor: float,
    quality: str = "medium",
    sr: int = 48000,
    window_ms: float | None = None,
    adaptive: bool = True,
    transient_sensitivity: float | None = None,
    phase_coherence: float | None = None,
) -> np.ndarray:
    """Change the duration of a signal without changing its pitch.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    factor:
        Time-scale factor, ``2.0`` makes the signal twice as long.
    quality:
        ``'low'``, ``'medium'`` or ``'high'``.
    sr:
        Sample rate in Hz.
    window_ms:
        Explicit analysis window length in milliseconds.
    adaptive:
        Shorten the window automatically when the material is transient
        dense.
    transient_sensitivity:
        Override the transient sensitivity of the quality preset.
    phase_coherence:
        Override the phase coherence of the quality preset (``0..1``).

    Returns
    -------
    numpy.ndarray
        The stretched signal, same channel layout as the input.
    """
    settings = _quality_settings(quality)
    buf = np.asarray(x, dtype=np.float64)
    spec = adaptive_window(window_ms, sr, quality, x=buf if adaptive else None)
    pv = _build_vocoder(spec, sr, settings, transient_sensitivity, phase_coherence)
    return pv.time_stretch(buf, float(factor))


def _smooth_envelope(mag_db: np.ndarray, sr: int, n_fft: int, octaves: float = 0.5) -> np.ndarray:
    """Constant-Q (log frequency) smoothed spectral envelope, in dB.

    Smoothing in the log-frequency domain gives every region of the spectrum
    the same relative bandwidth, which is what a formant envelope needs.  A
    fixed width in bins would follow narrow features (a single partial) and
    would then try to "correct" them away.
    """
    if mag_db.size == 0:
        return mag_db
    n_bins = mag_db.shape[-1]
    freqs = np.maximum(np.fft.rfftfreq(n_fft, 1.0 / float(sr)), 20.0)
    lo = math.log2(freqs[0])
    hi = math.log2(freqs[-1])
    if hi <= lo:
        hi = lo + 1.0
    grid = np.linspace(lo, hi, num=int(np.clip(n_bins, 128, 1024)))
    grid_f = 2.0 ** grid
    sigma = max(octaves, 1e-3) / 2.3548200450309493  # octaves -> sigma
    radius = max(2, int(math.ceil(3.0 * sigma * grid.size)))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k = k / k.sum()
    out = np.empty_like(mag_db)
    for i in range(mag_db.shape[0]):
        curve = np.interp(grid_f, freqs, mag_db[i])
        full = np.convolve(curve, k, mode="full")
        start = (k.size - 1) // 2
        smooth = full[start:start + curve.size]
        out[i] = np.interp(freqs, grid_f, smooth)
    return out


def _preserve_formants(
    original: np.ndarray,
    shifted: np.ndarray,
    sr: int,
    spec: WindowSpec,
    max_correction_db: float = 6.0,
) -> np.ndarray:
    """Move the spectral envelope of ``shifted`` back onto ``original``.

    The correction is the difference between the two constant-Q envelopes.
    It is limited to ``max_correction_db`` so that strongly tonal material
    (where the "envelope" is the partial itself) keeps its new pitch instead
    of being pulled back to the original one.
    """
    stft = _make_stft(spec.fft_size, spec.hop, "hann")
    orig_mag, orig_phase = stft.analyze(original)
    shift_mag, shift_phase = stft.analyze(shifted)
    n = min(orig_mag.shape[0], shift_mag.shape[0])
    if n == 0:
        return shifted
    eps = 1e-10
    orig_env = _smooth_envelope(20.0 * np.log10(orig_mag[:n] + eps), sr, spec.fft_size)
    shift_env = _smooth_envelope(20.0 * np.log10(shift_mag[:n] + eps), sr, spec.fft_size)
    correction = np.clip(orig_env - shift_env, -max_correction_db, max_correction_db)
    # smooth the correction over time so that it does not modulate the signal
    if correction.shape[0] >= 3:
        padded = np.pad(correction, ((1, 1), (0, 0)), mode="edge")
        correction = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
    gain = 10.0 ** (correction / 20.0)
    out = stft.synthesize(shift_mag[:n] * gain, shift_phase[:n], shifted.size)
    out = np.asarray(out).ravel()
    if out.size < shifted.size:
        out = np.pad(out, (0, shifted.size - out.size))
    return out[: shifted.size]


def pitch_shift(
    x,
    semitones: float,
    quality: str = "medium",
    sr: int = 48000,
    window_ms: float | None = None,
    adaptive: bool = True,
    shift_formants: bool = False,
    transient_sensitivity: float | None = None,
    phase_coherence: float | None = None,
) -> np.ndarray:
    """Change the pitch of a signal without changing its duration.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    semitones:
        Pitch offset in semitones, positive shifts up.
    quality:
        ``'low'``, ``'medium'`` or ``'high'``.
    sr:
        Sample rate in Hz.
    window_ms:
        Explicit analysis window length in milliseconds.
    adaptive:
        Shorten the window automatically for transient dense material.
    shift_formants:
        ``True`` lets the formants follow the pitch (the plain spectral
        pitch shifter).  ``False`` (the default) keeps the formants where
        they were by re-applying the original spectral envelope.
    transient_sensitivity:
        Override the transient sensitivity of the quality preset.
    phase_coherence:
        Override the phase coherence of the quality preset (``0..1``).

    Returns
    -------
    numpy.ndarray
        The pitch shifted signal, same channel layout as the input.
    """
    settings = _quality_settings(quality)
    buf = np.asarray(x, dtype=np.float64)
    single = buf.ndim == 1
    work = buf[:, None] if single else buf
    spec = adaptive_window(window_ms, sr, quality, x=work if adaptive else None)
    pv = _build_vocoder(spec, sr, settings, transient_sensitivity, phase_coherence)

    out = np.empty_like(work)
    for ch in range(work.shape[1]):
        shifted = pv.pitch_shift(work[:, ch], float(semitones))
        if not shift_formants and abs(float(semitones)) > 1e-9:
            shifted = _preserve_formants(work[:, ch], shifted, sr, spec)
        out[:, ch] = shifted
    return out[:, 0] if single else out
