"""
aurcore.dsp.loudness — ITU-R BS.1770-4 loudness metering.

The module implements the complete recommendation:

* the two-stage K-weighting filter (high shelf + RLB high-pass),
* the gated integrated loudness,
* momentary (400 ms) and short-term (3 s) loudness,
* the loudness range of EBU Tech 3342,
* 4x oversampled true-peak measurement (BS.1770-4 Annex 2).

The loudness range follows EBU Tech 3342 exactly: the relative gate sits 20
LU below the absolute-gated loudness level and the range is the distance
between the 10th and the 95th percentile.  The true peak is measured over
the interior of the buffer, because the reconstruction of the first and the
last eight samples depends on what lies outside the file.

Only ``numpy`` is required; ``scipy`` is used when available and every use is
guarded by a numpy fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = [
    "k_weighting_coefficients",
    "k_weight",
    "integrated_loudness",
    "momentary_loudness",
    "short_term_loudness",
    "loudness_range",
    "true_peak",
    "true_peak_linear",
    "loudness_measurements",
    "LoudnessMeasurements",
    "channel_weights",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
_LOUDNESS_OFFSET = -0.691  # dB, BS.1770-4 section 5
_ABSOLUTE_GATE_LUFS = -70.0
_RELATIVE_GATE_LU = 10.0
_BLOCK_SECONDS = 0.4
_BLOCK_STEP_SECONDS = 0.1
_SHORT_TERM_SECONDS = 3.0
_SHORT_TERM_STEP_SECONDS = 1.0
_LRA_RELATIVE_GATE_LU = 20.0
_LRA_LOW_PERCENTILE = 10.0
_LRA_HIGH_PERCENTILE = 95.0

#: Taps of the true-peak interpolation kernel per oversampling factor.  The
#: kernel is ``16 * factor + 1`` taps long, so every reconstructed sample
#: depends on eight source samples on either side of it.
_OVERSAMPLE_TAPS_PER_FACTOR = 16

# Published BS.1770-4 coefficients for 48 kHz.  The analog prototypes are
# derived from them and re-designed with the bilinear transform for any other
# sample rate, exactly as the recommendation prescribes.
_SHELF_48K = (
    (1.53512485958697, -2.69169618940638, 1.19839281085285),
    (1.0, -1.69065929318241, 0.73248077421585),
)
_RLB_48K = (
    (1.0, -2.0, 1.0),
    (1.0, -1.99004745483398, 0.99007225036621),
)


# ---------------------------------------------------------------------------
# IIR helper (scipy when available, numpy fallback otherwise)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - depends on the environment
    from scipy.signal import lfilter as _scipy_lfilter
except Exception:  # pragma: no cover
    _scipy_lfilter = None


def _iir(x: np.ndarray, b: Sequence[float], a: Sequence[float]) -> np.ndarray:
    """Apply a short IIR filter along the first axis of ``x``.

    ``scipy.signal.lfilter`` is used when importable; otherwise a direct
    form-II transposed implementation in pure python is used.
    """
    x = np.asarray(x, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    a0 = float(a[0])
    if a0 == 0.0:
        raise ValueError("filter denominator must not start with zero")
    b = b / a0
    a = a / a0
    if _scipy_lfilter is not None:
        return _scipy_lfilter(b, a, x, axis=0)
    return _iir_numpy(x, b, a)


def _iir_numpy(x: np.ndarray, b: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Pure numpy (python loop) biquad/cascade IIR along axis 0."""
    out = np.empty_like(x)
    flat_in = x.reshape(x.shape[0], -1)
    flat_out = out.reshape(x.shape[0], -1)
    b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
    a1, a2 = float(a[1]), float(a[2])
    for ch in range(flat_in.shape[1]):
        z1 = 0.0
        z2 = 0.0
        for n in range(flat_in.shape[0]):
            xn = float(flat_in[n, ch])
            yn = b0 * xn + z1
            z1 = b1 * xn - a1 * yn + z2
            z2 = b2 * xn - a2 * yn
            flat_out[n, ch] = yn
    return out


# ---------------------------------------------------------------------------
# K-weighting filter design
# ---------------------------------------------------------------------------
def _analog_from_digital(b: Sequence[float], a: Sequence[float], sr: int):
    """Recover the analog prototype of a digital filter (inverse bilinear)."""
    k = 2.0 * float(sr)
    b0, b1, b2 = (float(v) for v in b)
    a0, a1, a2 = (float(v) for v in a)
    num = (b0 - b1 + b2, 2.0 * k * (b0 - b2), k * k * (b0 + b1 + b2))
    den = (a0 - a1 + a2, 2.0 * k * (a0 - a2), k * k * (a0 + a1 + a2))
    return num, den


def _digital_from_analog(num, den, sr: int):
    """Design a digital filter from an analog prototype (bilinear transform)."""
    k = 2.0 * float(sr)
    n2, n1, n0 = (float(v) for v in num)
    d2, d1, d0 = (float(v) for v in den)
    nb = (n2 * k * k + n1 * k + n0, -2.0 * n2 * k * k + 2.0 * n0, n2 * k * k - n1 * k + n0)
    db = (d2 * k * k + d1 * k + d0, -2.0 * d2 * k * k + 2.0 * d0, d2 * k * k - d1 * k + d0)
    scale = db[0]
    return (nb[0] / scale, nb[1] / scale, nb[2] / scale), (1.0, db[1] / scale, db[2] / scale)


_SHELF_ANALOG = _analog_from_digital(_SHELF_48K[0], _SHELF_48K[1], 48000)
_RLB_ANALOG = _analog_from_digital(_RLB_48K[0], _RLB_48K[1], 48000)


def _poly_multiply(p: Sequence[float], q: Sequence[float]) -> tuple:
    """Multiply two short polynomials given as coefficient tuples."""
    out = [0.0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] += a * b
    return tuple(out)


def k_weighting_coefficients(sr: int = 48000) -> dict:
    """Return the K-weighting filter coefficients for a sample rate.

    The 48 kHz values are the published BS.1770-4 coefficients; every other
    rate is obtained by applying the bilinear transform to the equivalent
    analog prototypes.

    Returns
    -------
    dict
        ``{"shelf": (b, a), "rlb": (b, a), "cascade": (b, a)}`` where the
        cascade is the 4th-order combination of both stages.
    """
    sr = int(sr)
    if sr <= 0:
        raise ValueError("sample rate must be positive")
    shelf = _digital_from_analog(*_SHELF_ANALOG, sr)
    rlb = _digital_from_analog(*_RLB_ANALOG, sr)
    cascade_b = _poly_multiply(shelf[0], rlb[0])
    cascade_a = _poly_multiply(shelf[1], rlb[1])
    return {"shelf": shelf, "rlb": rlb, "cascade": (cascade_b, cascade_a)}


def k_weight(x: np.ndarray, sr: int = 48000) -> np.ndarray:
    """Apply the K-weighting filter to ``x`` (shape ``(n,)`` or ``(n, ch)``)."""
    coeffs = k_weighting_coefficients(sr)
    b, a = coeffs["shelf"]
    y = _iir(x, b, a)
    b, a = coeffs["rlb"]
    return _iir(y, b, a)


# ---------------------------------------------------------------------------
# channel weights
# ---------------------------------------------------------------------------
def channel_weights(channels: int) -> np.ndarray:
    """Per-channel weight factors ``G_i`` of BS.1770-4, Table 3.

    The assumed channel order follows the loudspeaker layouts the
    recommendation measures: mono / stereo / 3.0 / 4.0 / 5.0 / 5.1 / 7.0 /
    7.1, i.e. ``L, R, C, Ls, Rs`` for the 3.0 to 5.0 layouts and
    ``L, R, C, LFE, Ls, Rs, Lb, Rb`` for the layouts that carry a low
    frequency effects channel.  The LFE channel carries a weight of 0 and is
    therefore excluded from the measurement, exactly as the recommendation
    and the EBU mode require.

    The centre channel carries the weight 1.0, not 1.41: BS.1770-4 lowered
    it, and the EBU mode compliance test for a five channel programme
    (expected ``-23.0 +/- 0.1`` LUFS) only holds with the value of Table 3.
    """
    n = int(channels)
    if n <= 0:
        raise ValueError("at least one channel is required")
    if n == 1:
        return np.array([1.0])
    if n == 2:
        return np.array([1.0, 1.0])
    if n == 3:
        return np.array([1.0, 1.0, 1.0])
    if n == 4:
        return np.array([1.0, 1.0, 1.0, 1.41])
    if n == 5:
        return np.array([1.0, 1.0, 1.0, 1.41, 1.41])
    if n == 6:
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41])
    if n == 7:
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41, 1.41])
    if n == 8:
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41, 1.41, 1.41])
    return np.ones(n)


# ---------------------------------------------------------------------------
# block based loudness
# ---------------------------------------------------------------------------
def _as_2d(x) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        return a[:, None]
    if a.ndim != 2:
        raise ValueError("audio must be 1-D or 2-D (samples, channels)")
    return a


def _block_energies(y: np.ndarray, weights: np.ndarray, block: float, step: float, sr: int):
    """Return ``(z, l)``: gated block energy and block loudness."""
    n = y.shape[0]
    block_len = max(1, int(round(block * sr)))
    step_len = max(1, int(round(step * sr)))
    if n < block_len:
        pad = block_len - n
        y = np.concatenate([y, np.zeros((pad, y.shape[1]))], axis=0)
        n = block_len
    starts = list(range(0, n - block_len + 1, step_len))
    if not starts:
        starts = [0]
    z = np.empty(len(starts), dtype=np.float64)
    for i, s in enumerate(starts):
        seg = y[s:s + block_len]
        ms = np.mean(seg * seg, axis=0)
        z[i] = float(np.dot(weights, ms))
    z = np.maximum(z, 0.0)
    l = _LOUDNESS_OFFSET + 10.0 * np.log10(np.maximum(z, 1e-30))
    return z, l


def _gated_integrated(z: np.ndarray, l: np.ndarray) -> float:
    """Apply the BS.1770-4 absolute and relative gates to the block data."""
    if z.size == 0:
        return float("-inf")
    abs_keep = l > _ABSOLUTE_GATE_LUFS
    if not np.any(abs_keep):
        return float("-inf")
    rel_threshold = _LOUDNESS_OFFSET + 10.0 * math.log10(max(float(np.mean(z[abs_keep])), 1e-30))
    rel_threshold -= _RELATIVE_GATE_LU
    keep = abs_keep & (l > rel_threshold)
    if not np.any(keep):
        keep = abs_keep
    value = _LOUDNESS_OFFSET + 10.0 * math.log10(max(float(np.mean(z[keep])), 1e-30))
    return float(value)


def integrated_loudness(x, sr: int = 48000) -> float:
    """Gated integrated loudness of ``x`` in LUFS (BS.1770-4).

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sr:
        Sample rate in Hz.

    Returns
    -------
    float
        The integrated loudness in LUFS, or ``-inf`` for an empty/silent
        signal.
    """
    buf = _as_2d(x)
    if buf.shape[0] == 0:
        return float("-inf")
    weights = channel_weights(buf.shape[1])
    if not np.any(weights > 0.0):
        weights = np.ones(buf.shape[1])
    y = k_weight(buf, sr)
    z, l = _block_energies(y, weights, _BLOCK_SECONDS, _BLOCK_STEP_SECONDS, sr)
    return _gated_integrated(z, l)


def momentary_loudness(x, sr: int = 48000, aggregate: str = "max") -> float:
    """Momentary loudness (400 ms window) in LUFS.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sr:
        Sample rate in Hz.
    aggregate:
        ``'max'`` (default) returns the highest momentary value in the
        signal, ``'last'`` the value of the final window and ``'mean'`` the
        mean of all windows.
    """
    return _windowed_loudness(x, sr, _BLOCK_SECONDS, _BLOCK_STEP_SECONDS, aggregate, gated=False)


def short_term_loudness(x, sr: int = 48000, aggregate: str = "max") -> float:
    """Short-term loudness (3 s window) in LUFS.

    See :func:`momentary_loudness` for the meaning of ``aggregate``.
    """
    return _windowed_loudness(x, sr, _SHORT_TERM_SECONDS, _SHORT_TERM_STEP_SECONDS, aggregate,
                              gated=False)


def _windowed_loudness(x, sr: int, window: float, step: float, aggregate: str,
                       gated: bool) -> float:
    buf = _as_2d(x)
    if buf.shape[0] == 0:
        return float("-inf")
    weights = channel_weights(buf.shape[1])
    if not np.any(weights > 0.0):
        weights = np.ones(buf.shape[1])
    y = k_weight(buf, sr)
    z, l = _block_energies(y, weights, window, step, sr)
    if gated:
        value = _gated_integrated(z, l)
        return value
    agg = str(aggregate).lower()
    if agg.startswith("last"):
        return float(l[-1])
    if agg.startswith("mean"):
        return float(np.mean(l))
    return float(np.max(l))


def _percentile(sorted_values: np.ndarray, percent: float) -> float:
    """Return one percentile of a sorted array, EBU Tech 3342 style.

    The reference implementation of the loudness range measure selects the
    element at the rounded rank ``round((n - 1) * p / 100 + 1)`` of the
    sorted values, without interpolating between two elements.  The same
    rule is used here so that the numbers match the published measure.
    """
    n = int(sorted_values.size)
    if n == 0:
        return float("-inf")
    index = int(math.floor((n - 1) * (percent / 100.0) + 1.5)) - 1
    index = min(max(index, 0), n - 1)
    return float(sorted_values[index])


def loudness_range(x, sr: int = 48000) -> tuple[float, float, float]:
    """Loudness range (LRA) following EBU Tech 3342.

    The short-term loudness values (3 s window, 1 s step) are gated at
    -70 LUFS absolutely and 20 LU below their absolute-gated loudness
    level, then the 10th and 95th percentiles define the range.

    The relative gate is placed 20 LU below the *energy* mean of the
    absolute-gated values, which is what the reference implementation of
    the measure does; an arithmetic mean of the loudness values would let
    a low level passage survive the gate and would report a loudness range
    that is far too wide.

    Returns
    -------
    tuple
        ``(lra_lu, percentile_low_lufs, percentile_high_lufs)``.
    """
    buf = _as_2d(x)
    if buf.shape[0] == 0:
        return 0.0, float("-inf"), float("-inf")
    weights = channel_weights(buf.shape[1])
    if not np.any(weights > 0.0):
        weights = np.ones(buf.shape[1])
    y = k_weight(buf, sr)
    z, l = _block_energies(y, weights, _SHORT_TERM_SECONDS, _SHORT_TERM_STEP_SECONDS, sr)

    keep = l >= _ABSOLUTE_GATE_LUFS
    if not np.any(keep):
        return 0.0, float("-inf"), float("-inf")
    absolute = l[keep]
    # the absolute-gated loudness level, as the reference implementation
    # computes it: the mean of the un-logarithmed values
    gated = 10.0 * math.log10(max(float(np.mean(10.0 ** (absolute / 10.0))), 1e-30))
    rel = gated - _LRA_RELATIVE_GATE_LU
    keep = absolute >= rel
    if not np.any(keep):
        return 0.0, float("-inf"), float("-inf")
    values = np.sort(absolute[keep])
    low = _percentile(values, _LRA_LOW_PERCENTILE)
    high = _percentile(values, _LRA_HIGH_PERCENTILE)
    return float(high - low), low, high


# ---------------------------------------------------------------------------
# true peak
# ---------------------------------------------------------------------------
def _kaiser_sinc_lowpass(cutoff: float, length: int, beta: float = 9.0,
                         gain: float = 1.0) -> np.ndarray:
    """Windowed-sinc low-pass prototype with a Kaiser window.

    ``cutoff`` is the normalised cutoff frequency in cycles per sample
    (0 .. 0.5).  The kernel is ``gain * 2*cutoff*sinc(2*cutoff*n)``; it is
    deliberately *not* normalised to unit DC gain, because that would break
    the interpolation identity.  ``gain`` compensates the amplitude loss of
    a zero-stuffed signal (``gain = 1/(2*cutoff)`` restores unity).
    """
    n = int(length)
    if n < 1:
        return np.ones(1)
    if n % 2 == 0:
        n += 1
    m = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    h = gain * 2.0 * cutoff * np.sinc(2.0 * cutoff * m)
    # Kaiser window (numpy has no direct implementation -> use the series).
    r = 2.0 * m / (n - 1)
    arg = np.clip(1.0 - r * r, 0.0, None)
    win = np.i0(beta * np.sqrt(arg)) / np.i0(beta)
    return h * win


def _fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Linear convolution of two 1-D arrays using the FFT."""
    n = int(x.size + h.size - 1)
    nfft = 1 << max(1, (n - 1).bit_length())
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)[:n]
    return y


def _oversample(x: np.ndarray, factor: int) -> np.ndarray:
    """Upsample ``x`` by an integer factor with an anti-imaging low-pass.

    The prototype is the band-limited interpolating kernel for the given
    factor, windowed with a Kaiser window.  The original sample positions are
    reproduced exactly (up to the truncation error of the window), so the
    result is the continuous-time reconstruction of the samples.
    """
    factor = int(factor)
    if factor <= 1:
        return np.asarray(x, dtype=np.float64)
    cutoff = 0.5 / factor  # normalised to the oversampled sample rate
    taps = _OVERSAMPLE_TAPS_PER_FACTOR * factor
    if taps % 2 == 0:
        taps += 1
    h = _kaiser_sinc_lowpass(cutoff, taps, gain=1.0 / (2.0 * cutoff))
    up = np.zeros(x.size * factor, dtype=np.float64)
    up[::factor] = x
    conv = _fft_convolve(up, h)
    # keep the samples that correspond to the original grid
    offset = (h.size - 1) // 2
    start = offset
    end = start + x.size * factor
    if end > conv.size:
        conv = np.concatenate([conv, np.zeros(end - conv.size)])
    return conv[start:end]


def _interior(up: np.ndarray, factor: int) -> np.ndarray:
    """Drop the samples whose reconstruction reaches outside the buffer.

    The interpolation filter reads eight source samples on either side of
    every output sample.  At the two ends of the buffer those samples do
    not exist and the filter sees zeros instead.  The reconstruction there
    rings and can read 1.5 dB above the real waveform, which is an artefact
    of the file boundary and not a property of the signal, so the peak is
    only looked for in the interior of the buffer.
    """
    margin = (_OVERSAMPLE_TAPS_PER_FACTOR // 2) * int(factor)
    if up.size <= 2 * margin:
        return up
    return up[margin: up.size - margin]


def true_peak_linear(x, sr: int = 48000, oversample: int = 4) -> float:
    """Inter-sample peak of ``x`` (linear amplitude).

    The signal is oversampled by ``oversample`` times (4 by default, as in
    BS.1770-4 Annex 2) before the peak is measured, so that peaks that fall
    between two samples are taken into account.

    The peak is taken over the interior of the buffer; see
    :func:`_interior` for why the two ends are left out.
    """
    buf = _as_2d(x)
    if buf.size == 0:
        return 0.0
    factor = max(1, int(oversample))
    peak = 0.0
    for ch in range(buf.shape[1]):
        up = _oversample(buf[:, ch], factor)
        if up.size:
            peak = max(peak, float(np.max(np.abs(_interior(up, factor)))))
    return peak


def true_peak(x, sr: int = 48000, oversample: int = 4) -> float:
    """True-peak level of ``x`` in dBTP.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sr:
        Sample rate in Hz.
    oversample:
        Oversampling factor of the peak detector, 4 by default.

    Returns
    -------
    float
        The true-peak level in dBTP (``-inf`` for silence).
    """
    peak = true_peak_linear(x, sr, oversample)
    if peak <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(peak)


# ---------------------------------------------------------------------------
# combined measurement
# ---------------------------------------------------------------------------
@dataclass
class LoudnessMeasurements:
    """All loudness figures of one audio buffer.

    Attributes
    ----------
    integrated_lufs:
        Gated integrated loudness (BS.1770-4).
    momentary_lufs:
        Highest 400 ms loudness.
    short_term_lufs:
        Highest 3 s loudness.
    loudness_range_lu:
        Loudness range (EBU Tech 3342) in LU.
    lra_low_lufs, lra_high_lufs:
        The 10th and 95th percentiles used for the loudness range.
    true_peak_dbtp:
        Oversampled true-peak level.
    sample_peak_dbfs:
        Highest sample value.
    channels, sample_rate, samples:
        Metadata of the measured buffer.
    """

    integrated_lufs: float
    momentary_lufs: float
    short_term_lufs: float
    loudness_range_lu: float
    true_peak_dbtp: float
    sample_peak_dbfs: float
    lra_low_lufs: float = float("-inf")
    lra_high_lufs: float = float("-inf")
    channels: int = 1
    sample_rate: int = 48000
    samples: int = 0

    def to_dict(self) -> dict:
        """Return the measurements as a plain JSON-safe dictionary."""
        return {
            "integrated_lufs": float(self.integrated_lufs),
            "momentary_lufs": float(self.momentary_lufs),
            "short_term_lufs": float(self.short_term_lufs),
            "loudness_range_lu": float(self.loudness_range_lu),
            "true_peak_dbtp": float(self.true_peak_dbtp),
            "sample_peak_dbfs": float(self.sample_peak_dbfs),
            "lra_low_lufs": float(self.lra_low_lufs),
            "lra_high_lufs": float(self.lra_high_lufs),
            "channels": int(self.channels),
            "sample_rate": int(self.sample_rate),
            "samples": int(self.samples),
        }


def loudness_measurements(x, sr: int = 48000) -> LoudnessMeasurements:
    """Measure every loudness figure of ``x`` in one call.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sr:
        Sample rate in Hz.

    Returns
    -------
    LoudnessMeasurements
    """
    buf = _as_2d(x)
    integrated = integrated_loudness(buf, sr)
    momentary = momentary_loudness(buf, sr, aggregate="max")
    short_term = short_term_loudness(buf, sr, aggregate="max")
    lra, lra_low, lra_high = loudness_range(buf, sr)
    tp = true_peak(buf, sr)
    sample_peak = float(np.max(np.abs(buf))) if buf.size else 0.0
    return LoudnessMeasurements(
        integrated_lufs=float(integrated),
        momentary_lufs=float(momentary),
        short_term_lufs=float(short_term),
        loudness_range_lu=float(lra),
        true_peak_dbtp=float(tp),
        sample_peak_dbfs=(20.0 * math.log10(sample_peak) if sample_peak > 0.0 else float("-inf")),
        lra_low_lufs=float(lra_low),
        lra_high_lufs=float(lra_high),
        channels=int(buf.shape[1]),
        sample_rate=int(sr),
        samples=int(buf.shape[0]),
    )
