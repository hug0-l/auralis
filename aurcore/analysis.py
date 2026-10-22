"""
aurcore.analysis — the measurement service behind the Analysis tab.

This module is **not** a processing module: it is registered nowhere, it has
no parameters and it never touches a chain.  It is a *measurement* service —
given a signal (and a sample rate) it answers the questions the Analysis
tab asks:

``spectrum``
    The magnitude spectrum of the signal in decibels, together with its
    frequency axis.  The level convention is the one of a hardware analyser:
    a full scale sine reads ``0 dBFS`` on its bin.

``waveform_stats``
    True peak, sample peak, maximum / mean / minimum, RMS, total (multi
    channel) RMS, DC offset and the number of samples that are possibly
    clipped.

``loudness``
    Integrated, short term (3 s) and momentary (400 ms) loudness, the
    loudness range and the true peak level.  Every figure comes from
    :mod:`aurcore.dsp.loudness`, which implements ITU-R BS.1770-4 and
    EBU Tech 3342.

``correlation``
    The inter channel correlation coefficient (1.0 for a mono signal).

``histogram``
    The peak histogram of the waveform, binned over the full scale range,
    with the clipping region marked.

All measurements honour an optional **selection** given in seconds; without
one the whole signal is measured.

Conventions
-----------
* audio is ``float64`` in ``[-1, 1]``; ``1.0`` is ``0 dBFS``;
* a signal is 1-D ``(n,)`` or 2-D ``(n, channels)`` — the core API shape;
* levels are decibels with the amplitude convention (``20*log10``) unless a
  name says otherwise;
* times are seconds and frequencies hertz.

Only ``numpy`` is required.  SciPy is used by the loudness layer when it is
installed and every SciPy call there has a NumPy fallback.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .dsp.loudness import (
    integrated_loudness,
    loudness_range,
    momentary_loudness,
    short_term_loudness,
    true_peak_linear,
)

__all__ = [
    "Analysis",
    "analyze",
    "waveform_statistics",
    "magnitude_spectrum",
    "inter_channel_correlation",
    "peak_histogram",
    "selection_bounds",
    "DEFAULT_CLIP_THRESHOLD",
    "DEFAULT_HISTOGRAM_BINS",
    "DEFAULT_FFT_SIZE",
    "HISTOGRAM_RANGE",
]

#: A sample counts as "possibly clipped" from this absolute level upwards.
DEFAULT_CLIP_THRESHOLD: float = 0.999

#: Number of histogram bins over the full scale range.
DEFAULT_HISTOGRAM_BINS: int = 200

#: FFT size of the magnitude spectrum (a power of two).
DEFAULT_FFT_SIZE: int = 4096

#: Amplitude range covered by the peak histogram.
HISTOGRAM_RANGE: tuple[float, float] = (-1.0, 1.0)

#: Lowest level the spectrum reports (the dB floor of the analyser).
SPECTRUM_FLOOR_DB: float = -120.0

#: Lowest level any figure of the payload reports.  The floor keeps the
#: payload strictly JSON safe — ``-inf`` and ``NaN`` are not valid JSON.
LEVEL_FLOOR_DB: float = -120.0

#: True peak detector oversampling factor (BS.1770-4 Annex 2 uses 4).
_TRUE_PEAK_OVERSAMPLE: int = 4

#: Samples processed per block by the true peak detector; keeps the memory
#: use of the 4x oversampling bounded for long files.
_TRUE_PEAK_BLOCK: int = 1 << 18

#: Overlap between two true peak blocks (the filter kernel is 64 taps).
_TRUE_PEAK_OVERLAP: int = 128

_EPS: float = 1e-12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _as_2d(x) -> np.ndarray:
    """Return ``x`` as a 2-D ``(n_samples, channels)`` float64 array."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        return arr[:, None]
    if arr.ndim == 2:
        return arr
    if arr.ndim == 0:
        return arr.reshape(1, 1)
    raise ValueError("audio must be 1-D (n,) or 2-D (n, channels)")


def _next_pow2(n: int) -> int:
    """Smallest power of two greater than or equal to ``n``."""
    n = int(n)
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _db(amplitude: float, floor_db: float = LEVEL_FLOOR_DB) -> float:
    """Amplitude in dB, floored so the result is always finite."""
    value = abs(float(amplitude))
    if value <= _EPS:
        return float(floor_db)
    return max(float(floor_db), 20.0 * math.log10(value))


def _level(value: float, floor_db: float = LEVEL_FLOOR_DB) -> float:
    """Clamp a measured level into the reportable range.

    ``-inf`` (silence) and ``NaN`` become the floor, so the payload stays
    strictly JSON safe.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(floor_db)
    if not math.isfinite(number):
        return float(floor_db)
    return max(float(floor_db), number)


def selection_bounds(
    n_samples: int,
    sample_rate: int,
    start: float | None = None,
    end: float | None = None,
) -> tuple[int, int]:
    """Sample index range of a selection given in seconds.

    Parameters
    ----------
    n_samples:
        Length of the signal in samples.
    sample_rate:
        Sample rate in Hz.
    start, end:
        Selection bounds in seconds.  ``None`` means "the beginning" / "the
        end" of the signal.  Reversed bounds are swapped, out of range
        bounds are clamped.

    Returns
    -------
    tuple
        ``(first, last)`` sample indices; ``first == last`` marks an empty
        selection.
    """
    n = int(n_samples)
    sr = float(sample_rate)
    i0 = 0
    i1 = n
    if start is not None and sr > 0:
        try:
            value = float(start)
        except (TypeError, ValueError):
            value = 0.0
        if math.isfinite(value):
            i0 = int(round(value * sr))
    if end is not None and sr > 0:
        try:
            value = float(end)
        except (TypeError, ValueError):
            value = float(n)
        if math.isfinite(value):
            i1 = int(round(value * sr))
    if i1 < i0:
        i0, i1 = i1, i0
    i0 = max(0, min(i0, n))
    i1 = max(0, min(i1, n))
    if i1 < i0:
        i1 = i0
    return i0, i1


def _true_peak_blocked(buf: np.ndarray, oversample: int = _TRUE_PEAK_OVERSAMPLE) -> float:
    """Inter-sample peak of ``buf``, computed block by block.

    The detector is the BS.1770-4 Annex 2 4x oversampling peak meter of
    :mod:`aurcore.dsp.loudness`.  It is applied to overlapping blocks so
    that a long file does not need one huge oversampled buffer.
    """
    if buf.size == 0:
        return 0.0
    n = buf.shape[0]
    if n <= _TRUE_PEAK_BLOCK:
        return float(true_peak_linear(buf, oversample=oversample))
    step = max(1, _TRUE_PEAK_BLOCK - _TRUE_PEAK_OVERLAP)
    peak = 0.0
    start = 0
    while start < n:
        block = buf[start : start + _TRUE_PEAK_BLOCK]
        if block.shape[0] == 0:
            break
        peak = max(peak, float(true_peak_linear(block, oversample=oversample)))
        if start + _TRUE_PEAK_BLOCK >= n:
            break
        start += step
    return peak


# ---------------------------------------------------------------------------
# waveform statistics
# ---------------------------------------------------------------------------
def waveform_statistics(
    x,
    *,
    clip_threshold: float = DEFAULT_CLIP_THRESHOLD,
    true_peak: float | None = None,
) -> dict[str, Any]:
    """Level statistics of a signal.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    clip_threshold:
        Absolute level from which a sample counts as *possibly clipped*.
    true_peak:
        Inter-sample peak (linear) when it has already been measured;
        ``None`` measures it here.

    Returns
    -------
    dict
        ``true_peak`` / ``true_peak_db``, ``sample_peak`` /
        ``sample_peak_db``, ``max``, ``min``, ``mean``, ``rms`` /
        ``rms_db``, ``total_rms`` / ``total_rms_db``, ``dc_offset`` /
        ``dc_offset_db``, ``dc_offsets`` and ``possibly_clipped``.

        * ``max`` / ``min`` / ``mean`` are taken over the mono mix
          (the mean of the channels);
        * ``rms`` is the RMS of the mono mix, ``total_rms`` the RMS of the
          whole multi channel signal (``sqrt(mean(sum(x**2)))``);
        * ``dc_offset`` is the largest absolute DC offset among the
          channels, ``dc_offsets`` holds every channel offset.
    """
    buf = _as_2d(x)
    channels = int(buf.shape[1])
    threshold = abs(float(clip_threshold))

    if buf.size == 0:
        return {
            "true_peak": 0.0,
            "true_peak_db": LEVEL_FLOOR_DB,
            "sample_peak": 0.0,
            "sample_peak_db": LEVEL_FLOOR_DB,
            "max": 0.0,
            "min": 0.0,
            "mean": 0.0,
            "rms": 0.0,
            "rms_db": LEVEL_FLOOR_DB,
            "total_rms": 0.0,
            "total_rms_db": LEVEL_FLOOR_DB,
            "dc_offset": 0.0,
            "dc_offset_db": LEVEL_FLOOR_DB,
            "dc_offsets": [0.0] * channels,
            "possibly_clipped": 0,
            "samples": 0,
            "clip_threshold": threshold,
        }

    mono = buf.mean(axis=1) if channels > 1 else buf[:, 0]
    magnitudes = np.abs(buf)
    sample_peak = float(magnitudes.max())

    mean_value = float(mono.mean())
    rms = float(np.sqrt(np.mean(mono * mono)))
    total_rms = float(np.sqrt(np.mean(np.sum(buf * buf, axis=1))))

    channel_means = [float(v) for v in buf.mean(axis=0)]
    dc_offsets = [abs(v) for v in channel_means]
    dc_offset = max(dc_offsets) if dc_offsets else 0.0

    peak = _true_peak_blocked(buf) if true_peak is None else float(true_peak)

    return {
        "true_peak": peak,
        "true_peak_db": _db(peak),
        "sample_peak": sample_peak,
        "sample_peak_db": _db(sample_peak),
        "max": float(buf.max()),
        "min": float(buf.min()),
        "mean": mean_value,
        "rms": rms,
        "rms_db": _db(rms),
        "total_rms": total_rms,
        "total_rms_db": _db(total_rms),
        "dc_offset": dc_offset,
        "dc_offset_db": _db(dc_offset),
        "dc_offsets": channel_means,
        "possibly_clipped": int(np.count_nonzero(magnitudes >= threshold)),
        "samples": int(buf.shape[0]),
        "clip_threshold": threshold,
    }


# ---------------------------------------------------------------------------
# magnitude spectrum
# ---------------------------------------------------------------------------
def magnitude_spectrum(
    x,
    sample_rate: int = 48000,
    *,
    fft_size: int = DEFAULT_FFT_SIZE,
    max_frames: int = 256,
    floor_db: float = SPECTRUM_FLOOR_DB,
) -> dict[str, Any]:
    """Magnitude spectrum of a signal in decibels.

    The mono mix of the signal is analysed with a periodic Hann window.
    The magnitude is normalised by the coherent gain of the window, so a
    full scale sine reads ``0 dBFS`` on its bin — the level convention of a
    hardware analyser.  When the signal is longer than one frame the frames
    are averaged (the mean of the magnitudes).

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate in Hz.
    fft_size:
        FFT size in samples (rounded up to a power of two).
    max_frames:
        Upper bound on the number of analysed frames; the hop is stretched
        for long signals so the cost stays constant.
    floor_db:
        Lowest level reported.

    Returns
    -------
    dict
        ``frequencies`` (Hz), ``magnitudes_db`` (the averaged spectrum),
        ``peaks_db`` (the highest level every bin reached in any frame),
        ``min_db``, ``max_db``, ``fft_size``, ``frames`` and the peak
        (``peak_hz`` / ``peak_db``).
    """
    buf = _as_2d(x)
    sr = int(sample_rate)
    n_fft = max(64, _next_pow2(int(fft_size)))
    mono = buf.mean(axis=1) if buf.shape[1] > 1 else buf[:, 0]
    n = int(mono.shape[0])

    if n == 0:
        return {
            "frequencies": [],
            "magnitudes_db": [],
            "peaks_db": [],
            "min_db": float(floor_db),
            "max_db": float(floor_db),
            "fft_size": n_fft,
            "frames": 0,
            "sample_rate": sr,
            "peak_hz": 0.0,
            "peak_db": float(floor_db),
        }

    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n_fft, dtype=np.float64) / n_fft)
    gain = float(window.sum()) / 2.0
    if gain <= _EPS:
        gain = 1.0

    if n < n_fft:
        source = np.zeros(n_fft, dtype=np.float64)
        source[:n] = mono
        starts = [0]
    else:
        source = mono
        hop = max(1, n_fft // 2)
        count = 1 + (n - n_fft) // hop
        limit = max(1, int(max_frames))
        if count > limit and limit > 1:
            hop = max(1, (n - n_fft) // (limit - 1))
            count = 1 + (n - n_fft) // hop
        starts = range(0, n - n_fft + 1, hop)

    view = np.lib.stride_tricks.sliding_window_view(source, n_fft)
    accumulator = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    peak_accumulator = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    frames = 0
    for start in starts:
        magnitudes = np.abs(np.fft.rfft(view[start] * window))
        accumulator += magnitudes
        np.maximum(peak_accumulator, magnitudes, out=peak_accumulator)
        frames += 1
    if frames:
        accumulator /= float(frames)

    magnitudes = accumulator / gain
    floor_linear = 10.0 ** (float(floor_db) / 20.0)
    magnitudes_db = 20.0 * np.log10(np.maximum(magnitudes, floor_linear))
    peaks_db = 20.0 * np.log10(np.maximum(peak_accumulator / gain, floor_linear))
    peak_index = int(np.argmax(magnitudes_db))
    frequencies = np.arange(n_fft // 2 + 1, dtype=np.float64) * (float(sr) / float(n_fft))

    return {
        "frequencies": [float(v) for v in frequencies],
        "magnitudes_db": [float(v) for v in magnitudes_db],
        "peaks_db": [float(v) for v in peaks_db],
        "min_db": float(floor_db),
        "max_db": float(magnitudes_db[peak_index]),
        "fft_size": n_fft,
        "frames": int(frames),
        "sample_rate": sr,
        "peak_hz": float(frequencies[peak_index]),
        "peak_db": float(magnitudes_db[peak_index]),
    }


# ---------------------------------------------------------------------------
# inter channel correlation
# ---------------------------------------------------------------------------
def inter_channel_correlation(x) -> float:
    """Inter channel correlation coefficient of a signal.

    The Pearson correlation is computed for every channel pair and the mean
    of the pairs is returned.  A mono signal is perfectly correlated with
    itself and therefore reports ``1.0``.

    A pair whose channels are constant (zero variance) carries no
    correlation information; such a pair is skipped.  When no pair is left
    the result is ``0.0``.
    """
    buf = _as_2d(x)
    channels = int(buf.shape[1])
    if channels < 2:
        return 1.0
    if buf.shape[0] < 2:
        return 1.0

    values: list[float] = []
    for i in range(channels):
        for j in range(i + 1, channels):
            a = buf[:, i] - float(buf[:, i].mean())
            b = buf[:, j] - float(buf[:, j].mean())
            denominator = math.sqrt(float(np.sum(a * a)) * float(np.sum(b * b)))
            if denominator <= _EPS:
                continue
            values.append(float(np.sum(a * b)) / denominator)
    if not values:
        return 0.0
    return float(np.mean(values))


# ---------------------------------------------------------------------------
# peak histogram
# ---------------------------------------------------------------------------
def peak_histogram(
    x,
    *,
    bins: int = DEFAULT_HISTOGRAM_BINS,
    clip_threshold: float = DEFAULT_CLIP_THRESHOLD,
) -> dict[str, Any]:
    """Peak histogram of a signal.

    Every sample of every channel is counted in one of ``bins`` equal bins
    covering the full scale range ``[-1, 1]``.  Samples outside the range
    (float material can exceed full scale) are counted in the outermost
    bins and reported separately as ``out_of_range``.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    bins:
        Number of bins; an even number is used so that no bin straddles
        the zero line.
    clip_threshold:
        Level from which a sample counts as clipped.

    Returns
    -------
    dict
        ``bin_edges``, ``counts``, ``bin_width``, ``range``,
        ``clip_threshold``, ``clip_bin_start`` / ``clip_bin_end`` (the
        positive side clipping bins; the negative side is their mirror) and
        ``out_of_range``.
    """
    buf = _as_2d(x)
    count = int(bins)
    if count < 2:
        count = 2
    if count % 2:
        count += 1
    low, high = HISTOGRAM_RANGE
    width = (high - low) / count
    edges = np.linspace(low, high, count + 1, dtype=np.float64)

    if buf.size == 0:
        return {
            "bin_edges": [float(v) for v in edges],
            "counts": [0] * count,
            "bin_width": float(width),
            "range": [float(low), float(high)],
            "clip_threshold": abs(float(clip_threshold)),
            "clip_bin_start": count,
            "clip_bin_end": count,
            "out_of_range": 0,
            "samples": 0,
        }

    values = buf.ravel()
    out_of_range = int(np.count_nonzero((values < low) | (values > high)))
    clipped_values = np.clip(values, low, high)
    counts, _ = np.histogram(clipped_values, bins=edges)

    threshold = abs(float(clip_threshold))
    first = int(math.floor((threshold - low) / width)) if width > 0 else count
    first = max(0, min(first, count - 1))
    # Every bin from `first` upwards holds values at or above the
    # threshold, so the clipping zone is [first, count).  The negative side
    # is the mirror of that range.
    last = count

    return {
        "bin_edges": [float(v) for v in edges],
        "counts": [int(v) for v in counts],
        "bin_width": float(width),
        "range": [float(low), float(high)],
        "clip_threshold": threshold,
        "clip_bin_start": first,
        "clip_bin_end": last,
        "out_of_range": out_of_range,
        "samples": int(values.size),
    }


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------
class Analysis:
    """Measurement service for the Analysis tab.

    The service holds the configuration that does not change between
    measurements (sample rate, channel count, FFT size, clipping
    threshold, histogram resolution) and measures signals with
    :meth:`measure`.

    Examples
    --------
    >>> service = Analysis(sample_rate=48000, channels=2)
    >>> figures = service.measure(signal, start=1.0, end=4.0)
    >>> figures["loudness"]["integrated_lufs"]
    -20.0
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        *,
        fft_size: int = DEFAULT_FFT_SIZE,
        clip_threshold: float = DEFAULT_CLIP_THRESHOLD,
        histogram_bins: int = DEFAULT_HISTOGRAM_BINS,
        max_frames: int = 256,
    ) -> None:
        """Create the service.

        Parameters
        ----------
        sample_rate:
            Sample rate in Hz.
        channels:
            Number of channels the signals are expected to have.
        fft_size:
            FFT size of the magnitude spectrum.
        clip_threshold:
            Level from which a sample counts as possibly clipped.
        histogram_bins:
            Number of bins of the peak histogram.
        max_frames:
            Upper bound on the number of analysed spectrum frames.
        """
        if int(sample_rate) <= 0:
            raise ValueError("sample rate must be positive")
        if int(channels) <= 0:
            raise ValueError("at least one channel is required")
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.fft_size = max(64, _next_pow2(int(fft_size)))
        self.clip_threshold = abs(float(clip_threshold))
        self.histogram_bins = max(2, int(histogram_bins))
        self.max_frames = max(1, int(max_frames))

    # ------------------------------------------------------------------
    def measure(
        self,
        x,
        start: float | None = None,
        end: float | None = None,
    ) -> dict[str, Any]:
        """Measure a signal.

        Parameters
        ----------
        x:
            Audio, shape ``(n,)`` or ``(n, channels)``.
        start, end:
            Optional selection in seconds.  Without a selection the whole
            signal is measured.

        Returns
        -------
        dict
            The measurement payload:

            ``spectrum``
                ``frequencies``, ``magnitudes_db``, ``peaks_db``,
                ``min_db``, ``max_db``, ``fft_size``, ``frames``,
                ``peak_hz``, ``peak_db``.
            ``waveform_stats``
                See :func:`waveform_statistics`.
            ``loudness``
                ``integrated_lufs``, ``short_term_lufs``,
                ``momentary_lufs``, ``loudness_range_lu``,
                ``loudness_range_low_lufs``,
                ``loudness_range_high_lufs`` and ``true_peak_dbtp``.
            ``correlation``
                The inter channel correlation coefficient.
            ``histogram``
                See :func:`peak_histogram`.

            The payload also carries the signal metadata
            (``sample_rate``, ``channels``, ``frames``, ``duration``) and
            the measured range (``selection``).
        """
        buf = _as_2d(x)
        total = int(buf.shape[0])
        first, last = selection_bounds(total, self.sample_rate, start, end)
        segment = buf[first:last]

        peak = _true_peak_blocked(segment)
        stats = waveform_statistics(
            segment,
            clip_threshold=self.clip_threshold,
            true_peak=peak,
        )
        spectrum = magnitude_spectrum(
            segment,
            self.sample_rate,
            fft_size=self.fft_size,
            max_frames=self.max_frames,
        )
        histogram = peak_histogram(
            segment,
            bins=self.histogram_bins,
            clip_threshold=self.clip_threshold,
        )

        if segment.shape[0]:
            integrated = float(integrated_loudness(segment, self.sample_rate))
            momentary = float(momentary_loudness(segment, self.sample_rate, aggregate="max"))
            short_term = float(short_term_loudness(segment, self.sample_rate, aggregate="max"))
            lra, lra_low, lra_high = loudness_range(segment, self.sample_rate)
        else:
            integrated = float("-inf")
            momentary = float("-inf")
            short_term = float("-inf")
            lra, lra_low, lra_high = 0.0, float("-inf"), float("-inf")

        loudness = {
            "integrated_lufs": _level(integrated),
            "short_term_lufs": _level(short_term),
            "momentary_lufs": _level(momentary),
            "loudness_range_lu": _level(lra, 0.0),
            "loudness_range_low_lufs": _level(lra_low),
            "loudness_range_high_lufs": _level(lra_high),
            "true_peak_dbtp": _db(peak),
        }

        duration = total / float(self.sample_rate) if self.sample_rate else 0.0
        measured = (last - first) / float(self.sample_rate) if self.sample_rate else 0.0
        bounded = start is not None or end is not None

        return {
            "sample_rate": self.sample_rate,
            "channels": int(buf.shape[1]),
            "frames": total,
            "duration": duration,
            "selection": {
                "start": first / float(self.sample_rate) if self.sample_rate else 0.0,
                "end": last / float(self.sample_rate) if self.sample_rate else 0.0,
                "requested_start": None if start is None else float(start),
                "requested_end": None if end is None else float(end),
                "samples": int(last - first),
                "duration": measured,
                "bounded": bool(bounded),
            },
            "spectrum": spectrum,
            "waveform_stats": stats,
            "loudness": loudness,
            "correlation": float(inter_channel_correlation(segment)),
            "histogram": histogram,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Readable representation of the service."""
        return (
            f"<Analysis sample_rate={self.sample_rate} channels={self.channels} "
            f"fft_size={self.fft_size}>"
        )


def analyze(
    x,
    sample_rate: int = 48000,
    start: float | None = None,
    end: float | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Measure a signal in one call.

    This is the shortcut for :class:`Analysis`: the service is created,
    used and thrown away.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate in Hz.
    start, end:
        Optional selection in seconds.
    **options:
        Further options of :class:`Analysis` (``channels``, ``fft_size``,
        ``clip_threshold``, ``histogram_bins``, ``max_frames``).

    Returns
    -------
    dict
        The measurement payload; see :meth:`Analysis.measure`.
    """
    service = Analysis(sample_rate=sample_rate, **options)
    return service.measure(x, start=start, end=end)
