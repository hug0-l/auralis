"""
corpus.measure - measurement helpers for the Auralis golden corpus.

The module-test suites assert on numbers, not on waveforms.  This helper
loads a clip of the corpus and returns exactly the figures a test needs:

* the level of the clip (peak, true peak, RMS) in dBFS,
* the energy of a frequency band in dBFS,
* the positions of the impulsive transients, found by a simple detector,
* the runs of samples that sit at full scale,
* the reverberation time (Schroeder / T30),
* the frequency of the strongest partial of a band,
* the phase difference between the two channels,
* the correlation of the two channels,
* the pitch track of a signal.

Everything is measured the same way for every clip, so a test can compare a
*before* and an *after* measurement of the same interval and get a
meaningful difference in decibels.

Usage
-----
::

    from corpus import measure

    figures = measure.measure_clip("clicks")
    figures["clicks"][0]["time_s"]        # 0.25 - the first impulse
    figures["bands"]["tone1k"]            # -7.4 dBFS - the 1 kHz band

The module works with ``numpy`` only and imports nothing from ``aurcore``,
so it can be used from any test suite without pulling the DSP core in.

Conventions
-----------
* audio is ``float64`` in ``[-1, 1]``; ``1.0`` is ``0 dBFS``;
* a signal is 1-D ``(n,)`` or 2-D ``(n, channels)`` - the core API shape;
* levels are decibels with the amplitude convention (``20*log10``);
* times are seconds and frequencies hertz.
"""
from __future__ import annotations

import hashlib
import json
import math
import wave
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

__all__ = [
    "SAMPLE_RATE",
    "LEVEL_FLOOR_DB",
    "CORPUS_DIR",
    "MANIFEST_PATH",
    "CORPUS_BANDS",
    "OCTAVE_BANDS",
    "read_wav",
    "load_manifest",
    "manifest_entry",
    "clip_path",
    "load_clip",
    "mix_signal",
    "mid_side",
    "sample_peak",
    "true_peak",
    "rms",
    "peak_db",
    "true_peak_db",
    "rms_db",
    "band_energy_db",
    "band_levels_db",
    "detect_clicks",
    "clip_runs",
    "clip_stats",
    "rt60",
    "dominant_frequency",
    "spectral_centroid_hz",
    "phase_difference_deg",
    "interchannel_correlation",
    "pitch_track",
    "ground_truth_claims",
    "ground_truth_count",
    "measure",
    "measure_clip",
    "verify_corpus",
    "main",
]

#: Sample rate of the corpus.
SAMPLE_RATE: int = 48_000
#: Lowest level any figure reports (the floor of the analyser in aurcore).
LEVEL_FLOOR_DB: float = -120.0
#: Root of the corpus directory.
CORPUS_DIR: Path = Path(__file__).resolve().parent
#: The manifest of the corpus.
MANIFEST_PATH: Path = CORPUS_DIR / "manifest.json"

_EPS: float = 1e-12

#: The bands the corpus is measured in.  The names are the ones the module
#: test suites use.
CORPUS_BANDS: dict[str, tuple[float, float]] = {
    "broadband": (20.0, 20000.0),
    "hum50": (45.0, 55.0),
    "hum100": (95.0, 105.0),
    "tone220": (200.0, 240.0),
    "tone440": (410.0, 470.0),
    "tone1k": (950.0, 1050.0),
    "low": (20.0, 250.0),
    "sibilance": (5000.0, 7000.0),
    "high": (5000.0, 20000.0),
}

#: A generic octave band table, useful for spectral modules.
OCTAVE_BANDS: dict[str, tuple[float, float]] = {
    "sub": (20.0, 60.0),
    "bass": (60.0, 250.0),
    "low_mid": (250.0, 500.0),
    "mid": (500.0, 2000.0),
    "high_mid": (2000.0, 4000.0),
    "presence": (4000.0, 6000.0),
    "sibilance": (6000.0, 10000.0),
    "air": (10000.0, 20000.0),
}


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


def _db(amplitude: float, floor_db: float = LEVEL_FLOOR_DB) -> float:
    """Amplitude in dB, floored so the result is always finite."""
    value = abs(float(amplitude))
    if value <= _EPS:
        return float(floor_db)
    return max(float(floor_db), 20.0 * math.log10(value))


def _db_to_linear(db: float) -> float:
    """Amplitude (linear) of a level in dBFS."""
    return float(10.0 ** (float(db) / 20.0))


def _next_pow2(n: int) -> int:
    """Smallest power of two greater than or equal to ``n``."""
    n = int(n)
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _region(x, sample_rate: int, start: float | None, end: float | None) -> np.ndarray:
    """The slice of ``x`` between ``start`` and ``end`` (seconds)."""
    buf = _as_2d(x)
    total = int(buf.shape[0])
    sr = float(sample_rate)
    first = 0
    last = total
    if start is not None and sr > 0:
        first = int(round(float(start) * sr))
    if end is not None and sr > 0:
        last = int(round(float(end) * sr))
    first = max(0, min(first, total))
    last = max(first, min(last, total))
    return buf[first:last]


# ---------------------------------------------------------------------------
# WAV reading
# ---------------------------------------------------------------------------
def _decode_pcm(raw: bytes, width: int, channels: int) -> np.ndarray:
    """Decode interleaved PCM bytes into a ``(n, channels)`` float64 array."""
    if width == 1:  # 8-bit PCM is unsigned
        codes = np.frombuffer(raw, dtype=np.uint8).astype(np.int64) - 128
        return (codes.reshape(-1, channels) / 128.0).astype(np.float64)
    if width == 2:
        codes = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        return (codes.reshape(-1, channels) / 32767.0).astype(np.float64)
    if width == 3:
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        codes = (
            data[:, 0].astype(np.int64)
            | (data[:, 1].astype(np.int64) << 8)
            | (data[:, 2].astype(np.int64) << 16)
        )
        codes = np.where(codes >= 1 << 23, codes - (1 << 24), codes)
        return (codes.reshape(-1, channels) / 8388607.0).astype(np.float64)
    if width == 4:
        codes = np.frombuffer(raw, dtype="<i4").astype(np.float64)
        return (codes.reshape(-1, channels) / 2147483647.0).astype(np.float64)
    raise ValueError(f"unsupported sample width: {width} bytes")


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV file.

    Parameters
    ----------
    path:
        The file to read.

    Returns
    -------
    tuple
        ``(samples, sample_rate)`` where ``samples`` is a float64 array of
        shape ``(n_samples, channels)`` with the samples in ``[-1, 1]``.
        8-bit, 16-bit, 24-bit and 32-bit PCM files are supported; full
        scale is ``2**(bits - 1) - 1``, so a full scale sample reads
        exactly ``1.0``.
    """
    with wave.open(str(path), "rb") as handle:
        channels = int(handle.getnchannels())
        width = int(handle.getsampwidth())
        sample_rate = int(handle.getframerate())
        raw = handle.readframes(handle.getnframes())
    if channels <= 0:
        raise ValueError(f"{path}: no channels")
    return _decode_pcm(raw, width, channels), sample_rate


# ---------------------------------------------------------------------------
# manifest access
# ---------------------------------------------------------------------------
def load_manifest(path: str | Path | None = None) -> list[dict[str, Any]]:
    """The manifest of the corpus as a list of entries.

    An empty list is returned when the manifest does not exist, so a test
    that runs in a checkout without the corpus does not crash.
    """
    target = Path(path) if path is not None else MANIFEST_PATH
    if not target.exists():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    return list(data)


def manifest_entry(clip_id: str, path: str | Path | None = None) -> dict[str, Any]:
    """The manifest entry of one clip.

    Raises ``KeyError`` when the clip is not in the manifest.
    """
    for entry in load_manifest(path):
        if entry.get("id") == clip_id:
            return entry
    raise KeyError(f"no corpus clip named {clip_id!r}")


def clip_path(
    clip_id: str,
    path: str | Path | None = None,
    audio_dir: str | Path | None = None,
) -> Path:
    """The absolute path of a clip of the corpus.

    Parameters
    ----------
    clip_id:
        The id of the clip.
    path:
        An alternative manifest; ``None`` means the manifest of the corpus.
    audio_dir:
        An alternative root for the audio files; ``None`` means the corpus
        directory itself.  The root is the directory that holds the
        ``audio/`` tree, not the ``audio/`` directory.
    """
    entry = manifest_entry(clip_id, path)
    root = Path(audio_dir) if audio_dir is not None else CORPUS_DIR
    return root / str(entry["file"])


def load_clip(
    clip_id: str,
    path: str | Path | None = None,
    audio_dir: str | Path | None = None,
) -> tuple[np.ndarray, int]:
    """Load a clip of the corpus.

    Returns
    -------
    tuple
        ``(samples, sample_rate)`` - see :func:`read_wav`.
    """
    return read_wav(clip_path(clip_id, path, audio_dir))


# ---------------------------------------------------------------------------
# channel selection
# ---------------------------------------------------------------------------
def mid_side(x) -> tuple[np.ndarray, np.ndarray]:
    """The mid and the side signal of a stereo buffer.

    ``mid = (L + R) / 2`` and ``side = (L - R) / 2``; a mono buffer is
    returned unchanged as the mid signal, with a silent side.
    """
    buf = _as_2d(x)
    if buf.shape[1] < 2:
        return buf.copy(), np.zeros_like(buf)
    left = buf[:, 0]
    right = buf[:, 1]
    return (left + right) * 0.5, (left - right) * 0.5


def mix_signal(x, mix: str | int = "mono") -> np.ndarray:
    """The signal a measurement is taken on.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    mix:
        ``"mono"`` (the mean of the channels), ``"mid"``, ``"side"``,
        ``"left"`` / ``"right"``, ``"ch0"`` / ``"ch1"`` or a channel index.

    Returns
    -------
    numpy.ndarray
        A 1-D float64 array.
    """
    buf = _as_2d(x)
    channels = int(buf.shape[1])
    if isinstance(mix, (int, np.integer)) and not isinstance(mix, bool):
        index = int(mix)
        if index < 0 or index >= channels:
            raise IndexError(f"channel {index} does not exist")
        return buf[:, index].copy()
    name = str(mix).lower()
    if name == "mono":
        return buf.mean(axis=1)
    if name == "mid":
        return mid_side(buf)[0]
    if name == "side":
        return mid_side(buf)[1]
    if name in ("left", "ch0", "0"):
        return buf[:, 0].copy()
    if name in ("right", "ch1", "1"):
        return buf[:, min(1, channels - 1)].copy()
    raise ValueError(f"unknown mix: {mix!r}")


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------
def sample_peak(x) -> float:
    """The largest absolute sample of a signal (linear)."""
    buf = _as_2d(x)
    if buf.size == 0:
        return 0.0
    return float(np.max(np.abs(buf)))


def _true_peak_filter(oversample: int) -> np.ndarray:
    """The band-limited interpolating kernel of the true peak detector.

    The kernel is a windowed sinc whose cutoff is half the original sample
    rate, expressed in cycles per sample of the oversampled signal.  It is
    *not* normalised to unit gain: the central tap is one, so that a sample
    of the original signal is reproduced exactly and a full scale impulse
    reads exactly ``1.0``.
    """
    taps = 24 * int(oversample) + 1
    cutoff = 0.5 / float(oversample)
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    return np.sinc(2.0 * cutoff * n) * np.hanning(taps)


def true_peak(x, oversample: int = 4) -> float:
    """The inter-sample peak of a signal (linear).

    The signal is oversampled by a factor ``oversample`` with a windowed
    sinc filter and the largest sample of the result is returned.  A full
    scale sine reads exactly ``1.0``; a signal that reaches full scale
    between two samples reads more.
    """
    buf = _as_2d(x)
    if buf.size == 0:
        return 0.0
    factor = max(1, int(oversample))
    if factor == 1:
        return sample_peak(buf)
    kernel = _true_peak_filter(factor)
    peak = 0.0
    for channel in range(buf.shape[1]):
        upsampled = np.zeros(buf.shape[0] * factor, dtype=np.float64)
        upsampled[::factor] = buf[:, channel]
        filtered = np.convolve(upsampled, kernel, mode="same")
        peak = max(peak, float(np.max(np.abs(filtered))))
    return peak


def rms(x) -> float:
    """The RMS of a signal (linear), over the mono mix."""
    buf = _as_2d(x)
    if buf.size == 0:
        return 0.0
    mono = buf.mean(axis=1)
    return float(np.sqrt(np.mean(mono * mono)))


def peak_db(x) -> float:
    """The sample peak of a signal in dBFS."""
    return _db(sample_peak(x))


def true_peak_db(x, oversample: int = 4) -> float:
    """The true (inter-sample) peak of a signal in dBFS."""
    return _db(true_peak(x, oversample))


def rms_db(x) -> float:
    """The RMS of a signal in dBFS."""
    return _db(rms(x))


# ---------------------------------------------------------------------------
# band energy
# ---------------------------------------------------------------------------
def band_energy_db(
    x,
    sample_rate: int = SAMPLE_RATE,
    low_hz: float = 20.0,
    high_hz: float = 20000.0,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
) -> float:
    """The level of a frequency band in dBFS.

    The level of the band is the share of the signal's power that lies in
    the band: the signal is windowed with a periodic Hann window, its
    spectrum is computed and the power of the bins inside the band is
    compared with the power of the whole spectrum.  A full scale sine
    inside the band reads ``-3.01 dBFS`` - the RMS of the sine, not its
    peak - and a full scale noise band reads the level of its power.

    The window limits the resolution: the main lobe of the window is four
    bins of the transform wide, so a band that is narrower than
    ``4 * sample_rate / length`` of the selection is only measured
    partially.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    low_hz, high_hz:
        Edges of the band in Hz.
    start, end:
        Optional selection in seconds.
    mix:
        Which signal to measure - see :func:`mix_signal`.

    Returns
    -------
    float
        The band level in dBFS; the floor (``-120``) for silence.
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[0] == 0:
        return LEVEL_FLOOR_DB
    signal = mix_signal(region, mix)
    count = int(signal.size)
    mean_square = float(np.mean(signal * signal))
    if mean_square <= _EPS:
        return LEVEL_FLOOR_DB
    window = 0.5 - 0.5 * np.cos(2.0 * math.pi * np.arange(count, dtype=np.float64) / count)
    n_fft = _next_pow2(count * 4)
    spectrum = np.fft.rfft(signal * window, n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sample_rate))
    mask = (freqs >= float(low_hz)) & (freqs <= float(high_hz))
    if not bool(mask.any()):
        return LEVEL_FLOOR_DB
    power = np.abs(spectrum) ** 2
    total = float(np.sum(power))
    if total <= _EPS:
        return LEVEL_FLOOR_DB
    share = float(np.sum(power[mask])) / total
    band_square = mean_square * share
    if band_square <= _EPS:
        return LEVEL_FLOOR_DB
    return max(LEVEL_FLOOR_DB, 10.0 * math.log10(band_square))


def band_levels_db(
    x,
    sample_rate: int = SAMPLE_RATE,
    bands: Mapping[str, Sequence[float]] | None = None,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
) -> dict[str, float]:
    """The level of every band of a table, in dBFS.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    bands:
        Mapping of a name to a ``(low_hz, high_hz)`` pair.  ``None`` means
        :data:`CORPUS_BANDS`.
    start, end:
        Optional selection in seconds.
    mix:
        Which signal to measure - see :func:`mix_signal`.

    Returns
    -------
    dict
        ``{name: level_db}``.
    """
    table = CORPUS_BANDS if bands is None else bands
    out: dict[str, float] = {}
    for name, edges in table.items():
        low, high = float(edges[0]), float(edges[1])
        out[str(name)] = band_energy_db(x, sample_rate, low, high, start, end, mix)
    return out


# ---------------------------------------------------------------------------
# transient detection
# ---------------------------------------------------------------------------
def _running_median(values: np.ndarray, window: int) -> np.ndarray:
    """The running median of a series, one value per sample of the input.

    The median is taken over blocks of ``block`` samples and then over a
    sliding window of blocks, so that a single transient - which is far too
    short to move a median - cannot raise the background.
    """
    count = int(values.size)
    size = 64
    blocks = int(np.ceil(count / size))
    padded = np.zeros(blocks * size, dtype=np.float64)
    padded[:count] = values
    medians = np.median(padded.reshape(blocks, size), axis=1)
    span = max(1, int(window))
    half = span // 2
    padded = np.pad(medians, (half, span - 1 - half), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, span)
    running = np.median(view, axis=1)
    return np.repeat(running, size)[:count]


def detect_clicks(
    x,
    sample_rate: int = SAMPLE_RATE,
    threshold_db: float = 20.0,
    floor_dbfs: float = -40.0,
    min_gap_s: float = 0.005,
    block_ms: float = 200.0,
    start: float | None = None,
    end: float | None = None,
) -> list[dict[str, Any]]:
    """Find the impulsive transients of a signal.

    The detector compares the level of every sample with the level of its
    surroundings:

    1. the power of a sample is the largest power of the channels;
    2. the background is the *median* power of a window of ``block_ms``
       around the sample - a transient is far too short to move a median,
       so the background follows the material, not the transient;
    3. every sample that is ``threshold_db`` above the background *and*
       louder than ``floor_dbfs`` is a candidate;
    4. neighbouring candidates form one transient, whose position is the
       loudest sample of the group.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    threshold_db:
        How far above the background a sample has to be, in dB.
    floor_dbfs:
        Absolute level a sample has to reach to be a candidate, in dBFS.
    min_gap_s:
        Two transients closer than this are one transient.
    block_ms:
        Length of the window the background is measured over.
    start, end:
        Optional selection in seconds.

    Returns
    -------
    list
        One dictionary per transient: ``index`` (the sample), ``time_s``,
        ``peak`` (linear), ``peak_dbfs``, ``excess_db`` (above the
        background) and ``width_s`` (the length of the group).
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[0] < 3:
        return []
    offset = 0
    if start is not None:
        offset = int(round(float(start) * float(sample_rate)))
    raw = np.max(np.abs(region), axis=1)
    power = raw * raw

    span = max(1, int(round(float(block_ms) * 0.001 * float(sample_rate) / 64.0)))
    background = _running_median(power, span)
    background = np.maximum(background, _EPS)

    excess = 10.0 * np.log10(np.maximum(power, _EPS) / background)
    floor = _db_to_linear(floor_dbfs)
    candidates = (excess >= float(threshold_db)) & (raw >= floor)

    indices = np.flatnonzero(candidates)
    if indices.size == 0:
        return []
    gap = max(1, int(round(float(min_gap_s) * float(sample_rate))))
    groups: list[list[int]] = [[int(indices[0])]]
    for value in indices[1:]:
        value = int(value)
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])

    results: list[dict[str, Any]] = []
    for group in groups:
        first = max(0, group[0] - 1)
        last = min(raw.size, group[-1] + 2)
        peak_at = first + int(np.argmax(raw[first:last]))
        peak = float(raw[peak_at])
        results.append(
            {
                "index": peak_at + offset,
                "time_s": (peak_at + offset) / float(sample_rate),
                "peak": peak,
                "peak_dbfs": _db(peak),
                "excess_db": float(excess[peak_at]),
                "width_s": (group[-1] - group[0] + 1) / float(sample_rate),
            }
        )
    return results


# ---------------------------------------------------------------------------
# clipping
# ---------------------------------------------------------------------------
def clip_runs(x, threshold: float = 0.999, mix: str | int = "ch0") -> list[tuple[int, int]]:
    """The sample runs that sit at (or above) full scale.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    threshold:
        The absolute level from which a sample counts as clipped.
    mix:
        Which signal to look at - see :func:`mix_signal`.  The default is
        the first channel, because a stereo clip carries the same runs in
        both of them.

    Returns
    -------
    list
        ``(first, last)`` sample indices, half open: the samples
        ``first .. last - 1`` are clipped.
    """
    signal = mix_signal(x, mix)
    if signal.size == 0:
        return []
    flagged = np.abs(signal) >= abs(float(threshold))
    edges = np.diff(flagged.astype(np.int8))
    starts = [int(v) + 1 for v in np.flatnonzero(edges == 1)]
    stops = [int(v) + 1 for v in np.flatnonzero(edges == -1)]
    if bool(flagged[0]):
        starts.insert(0, 0)
    if bool(flagged[-1]):
        stops.append(int(flagged.size))
    return list(zip(starts, stops))


def clip_stats(x, threshold: float = 0.999, mix: str | int = "ch0") -> dict[str, Any]:
    """How much of a signal is clipped.

    Returns
    -------
    dict
        ``runs`` (the list of ``(first, last)`` pairs), ``run_count``,
        ``clipped_samples``, ``clipped_fraction`` (of the signal) and
        ``clipped_seconds``.
    """
    signal = mix_signal(x, mix)
    runs = clip_runs(x, threshold, mix)
    total = int(signal.size)
    clipped = int(sum(stop - start for start, stop in runs))
    sample_rate = float(SAMPLE_RATE)
    return {
        "runs": runs,
        "run_count": len(runs),
        "clipped_samples": clipped,
        "clipped_fraction": clipped / float(total) if total else 0.0,
        "clipped_seconds": clipped / sample_rate if sample_rate else 0.0,
    }


# ---------------------------------------------------------------------------
# reverberation time
# ---------------------------------------------------------------------------
def _analytic_envelope(signal: np.ndarray) -> np.ndarray:
    """The envelope of a real signal (the magnitude of its analytic signal)."""
    return np.abs(_analytic_signal(signal))


def _robust_slope(times: np.ndarray, values: np.ndarray) -> float | None:
    """The slope of a straight line through noisy points, in dB/s.

    The line is fitted by least squares; points that are far from the line
    (the envelope of a decaying tone fluctuates) are dropped and the line
    is fitted again.
    """
    if times.size < 8:
        return None
    keep_x = times
    keep_y = values
    slope = 0.0
    for _ in range(8):
        slope, intercept = np.polyfit(keep_x, keep_y, 1)
        residual = keep_y - (slope * keep_x + intercept)
        spread = float(np.median(np.abs(residual - np.median(residual)))) * 1.4826
        inside = np.abs(residual) <= max(2.0 * spread, 0.5)
        if bool(inside.all()) or int(np.count_nonzero(inside)) < 8:
            break
        keep_x = keep_x[inside]
        keep_y = keep_y[inside]
    if not np.isfinite(slope):
        return None
    return float(slope)


def rt60(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
    drop_db: float = 20.0,
    window_ms: float = 40.0,
    mix: str | int = "mono",
    method: str = "schroeder",
) -> float | None:
    """The reverberation time of a decay, in seconds.

    Two methods are available:

    ``"schroeder"`` (the default)
        The classical Schroeder method: the backward integral of the
        energy (the Schroeder curve) is fitted between 5 dB and 35 dB and
        extrapolated to a 60 dB drop (the T30 method).

    ``"energy"``
        The envelope of the signal is taken (the magnitude of its analytic
        signal), the energy of the envelope is smoothed over ``window_ms``
        and the decay curve is fitted between 5 dB and ``drop_db`` below
        its maximum.  The fit rejects the points that lie far from the
        line.  This method also works for the decaying tone of a
        reverberant room.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    start, end:
        The decay to measure, in seconds.  ``None`` means the whole signal.
    drop_db:
        How far below the maximum of the decay curve the line is fitted
        (the ``"energy"`` method only).
    window_ms:
        Length of the smoothing window of the energy curve, in
        milliseconds (the ``"energy"`` method only).
    mix:
        Which signal to measure - see :func:`mix_signal`.
    method:
        ``"schroeder"`` or ``"energy"``.

    Returns
    -------
    float or None
        The reverberation time in seconds, or ``None`` when the selection
        does not hold a decay that can be fitted.
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[0] < 16:
        return None
    signal = mix_signal(region, mix)
    if method == "schroeder":
        return _rt60_schroeder(signal, sample_rate)
    if method != "energy":
        raise ValueError(f"unknown rt60 method: {method!r}")

    envelope = _analytic_envelope(signal)
    window = max(1, int(round(float(window_ms) * 0.001 * float(sample_rate))))
    power = envelope * envelope
    kernel = np.ones(window, dtype=np.float64) / float(window)
    smoothed = np.convolve(power, kernel, mode="same")
    curve = 10.0 * np.log10(np.maximum(smoothed, 1e-30))
    reference = float(np.max(curve))
    inside = np.flatnonzero((curve <= reference - 5.0) & (curve >= reference - float(drop_db)))
    if inside.size < 8:
        return None
    times = inside / float(sample_rate)
    slope = _robust_slope(times, curve[inside])
    if slope is None or slope >= 0.0:
        return None
    return float(-60.0 / slope)


def _rt60_schroeder(signal: np.ndarray, sample_rate: int) -> float | None:
    """The reverberation time from the Schroeder curve, in seconds."""
    energy = np.asarray(signal, dtype=np.float64) ** 2
    total = float(np.sum(energy))
    if total <= _EPS:
        return None
    curve = np.cumsum(energy[::-1])[::-1]
    curve_db = 10.0 * np.log10(np.maximum(curve / curve[0], 1e-30))
    inside = np.flatnonzero((curve_db <= -5.0) & (curve_db >= -35.0))
    if inside.size < 8:
        return None
    times = inside / float(sample_rate)
    slope, _intercept = np.polyfit(times, curve_db[inside], 1)
    if not np.isfinite(slope) or slope >= 0.0:
        return None
    return float(-60.0 / slope)


# ---------------------------------------------------------------------------
# spectral peak, phase and correlation
# ---------------------------------------------------------------------------
def _analytic_signal(signal: np.ndarray) -> np.ndarray:
    """The analytic signal of a real signal (the complex one-sided spectrum).

    The analytic signal of ``A * sin(w t + p)`` is ``A * exp(i (w t + p))``:
    its magnitude is the envelope of the signal and its argument is its
    instantaneous phase.
    """
    x = np.asarray(signal, dtype=np.float64).ravel()
    n = int(x.size)
    if n < 4:
        return x.astype(np.complex128)
    spectrum = np.fft.fft(x)
    weights = np.zeros(n, dtype=np.float64)
    weights[0] = 1.0
    if n % 2 == 0:
        weights[n // 2] = 1.0
        weights[1 : n // 2] = 2.0
    else:
        weights[1 : (n + 1) // 2] = 2.0
    return np.fft.ifft(spectrum * weights)


def dominant_frequency(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
    low_hz: float = 20.0,
    high_hz: float = 20000.0,
) -> float | None:
    """The frequency of the strongest partial of a signal, in hertz.

    The spectrum of the selection is computed with a periodic Hann window and
    zero padded to four times its length; the strongest bin inside the search
    band is taken and its position is refined by a parabolic interpolation of
    the log of the magnitude, which resolves a pure tone far below the bin
    spacing.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    start, end:
        Optional selection in seconds.
    mix:
        Which signal to measure - see :func:`mix_signal`.
    low_hz, high_hz:
        The search band.  Only the partials inside it are considered, so a
        harmonic can be measured without the fundamental disturbing it.

    Returns
    -------
    float or None
        The frequency of the strongest partial, or ``None`` when the
        selection holds no partial at all (silence, or an empty band).
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[0] < 16:
        return None
    signal = mix_signal(region, mix)
    if float(np.max(np.abs(signal))) <= _EPS:
        return None
    count = int(signal.size)
    window = 0.5 - 0.5 * np.cos(2.0 * math.pi * np.arange(count, dtype=np.float64) / count)
    n_fft = _next_pow2(count * 4)
    spectrum = np.fft.rfft(signal * window, n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sample_rate))
    power = np.abs(spectrum) ** 2
    mask = (freqs >= float(low_hz)) & (freqs <= float(high_hz))
    if not bool(mask.any()):
        return None
    index = int(np.argmax(np.where(mask, power, 0.0)))
    if float(power[index]) <= _EPS:
        return None
    if 0 < index < power.size - 1:
        y0 = math.log(float(power[index - 1]) + _EPS)
        y1 = math.log(float(power[index]) + _EPS)
        y2 = math.log(float(power[index + 1]) + _EPS)
        denominator = y0 - 2.0 * y1 + y2
        if abs(denominator) > _EPS:
            delta = 0.5 * (y0 - y2) / denominator
            index = index + max(-0.5, min(0.5, delta))
    spacing = float(freqs[1] - freqs[0])
    return float(freqs[0] + index * spacing)


def spectral_centroid_hz(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
    low_hz: float = 20.0,
    high_hz: float = 20000.0,
) -> float:
    """The centre of mass of the power spectrum of a signal, in hertz.

    White noise has its centroid at the middle of the band and pink noise
    (a power density proportional to ``1/f``) well below it, so the centroid
    tells the two apart with a single number.

    Returns
    -------
    float
        The centroid in hertz; ``0.0`` for silence.
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[0] < 16:
        return 0.0
    signal = mix_signal(region, mix)
    if float(np.max(np.abs(signal))) <= _EPS:
        return 0.0
    count = int(signal.size)
    window = 0.5 - 0.5 * np.cos(2.0 * math.pi * np.arange(count, dtype=np.float64) / count)
    n_fft = _next_pow2(count * 4)
    spectrum = np.fft.rfft(signal * window, n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sample_rate))
    power = np.abs(spectrum) ** 2
    mask = (freqs >= float(low_hz)) & (freqs <= float(high_hz))
    total = float(np.sum(power[mask]))
    if total <= _EPS:
        return 0.0
    return float(np.sum(freqs[mask] * power[mask]) / total)


def phase_difference_deg(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
) -> float | None:
    """The phase of the second channel relative to the first, in degrees.

    The analytic signal of every channel is computed and the phase
    difference is taken from the weighted cross product of the two.  The
    weight is the product of the two envelopes, so the parts of the signal
    that carry no energy do not influence the result.

    The result is in ``(-180, 180]``: ``+90`` means that the right channel
    leads the left channel by a quarter of a period.

    Returns
    -------
    float or None
        The phase difference in degrees, or ``None`` when the buffer carries
        fewer than two channels or no energy at all.
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[1] < 2:
        return None
    left = region[:, 0]
    right = region[:, 1]
    # The analytic transform of a truncated tone has edge effects; the first
    # and the last five per cent of the selection are dropped.
    trim = int(0.05 * int(region.shape[0]))
    if int(region.shape[0]) - 2 * trim < 16:
        trim = 0
    if trim:
        left = left[trim:-trim]
        right = right[trim:-trim]
    analytic_left = _analytic_signal(left)
    analytic_right = _analytic_signal(right)
    weight = np.abs(analytic_left) * np.abs(analytic_right)
    if float(np.sum(weight)) <= _EPS:
        return None
    cross = np.sum(weight * np.conj(analytic_left) * analytic_right)
    angle = math.atan2(float(np.imag(cross)), float(np.real(cross)))
    return float(math.degrees(angle))


def interchannel_correlation(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
) -> float:
    """The Pearson correlation of the two channels of a buffer.

    ``+1`` means the channels are identical, ``-1`` means they are opposite
    and ``0`` means they are uncorrelated.
    """
    region = _region(x, sample_rate, start, end)
    if region.shape[1] < 2 or region.shape[0] < 2:
        return 0.0
    left = region[:, 0]
    right = region[:, 1]
    left = left - float(np.mean(left))
    right = right - float(np.mean(right))
    denominator = float(np.sqrt(np.sum(left * left) * np.sum(right * right)))
    if denominator <= _EPS:
        return 0.0
    return float(np.sum(left * right) / denominator)


def pitch_track(
    x,
    sample_rate: int = SAMPLE_RATE,
    window_s: float = 0.25,
    hop_s: float = 0.25,
    low_hz: float = 20.0,
    high_hz: float = 20000.0,
    mix: str | int = "mono",
) -> list[dict[str, Any]]:
    """The dominant frequency of every window of a signal.

    The windows are ``window_s`` long and start every ``hop_s``; the last
    window that fits inside the signal is used.

    Returns
    -------
    list
        One dictionary per window: ``start_s``, ``end_s`` and ``hz`` (the
        dominant partial, ``None`` when the window holds no energy).
    """
    region = _region(x, sample_rate, None, None)
    total = int(region.shape[0])
    width = max(16, int(round(float(window_s) * float(sample_rate))))
    hop = max(1, int(round(float(hop_s) * float(sample_rate))))
    out: list[dict[str, Any]] = []
    first = 0
    while first + width <= total:
        chunk = region[first : first + width]
        frequency = dominant_frequency(chunk, sample_rate, mix=mix, low_hz=low_hz, high_hz=high_hz)
        out.append(
            {
                "start_s": first / float(sample_rate),
                "end_s": (first + width) / float(sample_rate),
                "hz": frequency,
            }
        )
        first += hop
    return out


# ---------------------------------------------------------------------------
# the ground truth of a manifest entry
# ---------------------------------------------------------------------------
def ground_truth_claims(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every assertable ground truth claim of a manifest entry.

    A claim is one fact a test can assert: a level, an event, a position, a
    named region, a band level, a frequency, the inter-channel phase and the
    duration of the clip.

    Parameters
    ----------
    entry:
        One entry of the manifest.

    Returns
    -------
    list
        One dictionary per claim: ``kind``, ``name`` and ``value``.
    """
    annotations = dict(entry.get("annotations", {}))
    claims: list[dict[str, Any]] = []
    claims.append(
        {
            "kind": "duration",
            "name": "duration_s",
            "value": float(entry.get("duration_s", 0.0)),
        }
    )
    for key, value in dict(annotations.get("levels", {})).items():
        claims.append({"kind": "level", "name": key, "value": value})
    for index, event in enumerate(annotations.get("events", [])):
        claims.append(
            {
                "kind": "event",
                "name": f"{event.get('type', 'event')}[{index}]",
                "value": [event.get("start_s"), event.get("end_s")],
            }
        )
    for index, position in enumerate(annotations.get("positions", [])):
        claims.append({"kind": "position", "name": f"position[{index}]", "value": list(position)})
    for name, region in dict(annotations.get("regions", {})).items():
        claims.append({"kind": "region", "name": name, "value": list(region)})
    for band in annotations.get("bands", []):
        claims.append(
            {
                "kind": "band",
                "name": str(band.get("name", "band")),
                "value": float(band.get("level_dbfs", 0.0)),
            }
        )
    for frequency in annotations.get("frequencies", []):
        claims.append(
            {
                "kind": "frequency",
                "name": str(frequency.get("name", "frequency")),
                "value": frequency.get("hz"),
            }
        )
    phase = annotations.get("phase")
    if phase:
        claims.append(
            {
                "kind": "phase",
                "name": str(phase.get("name", "phase")),
                "value": float(phase.get("degrees", 0.0)),
            }
        )
    return claims


def ground_truth_count(entry: Mapping[str, Any]) -> int:
    """The number of assertable ground truth claims of a manifest entry."""
    return len(ground_truth_claims(entry))


# ---------------------------------------------------------------------------
# the one shot measurement
# ---------------------------------------------------------------------------
def measure(
    x,
    sample_rate: int = SAMPLE_RATE,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
    bands: Mapping[str, Sequence[float]] | None = None,
    clip_threshold: float = 0.999,
) -> dict[str, Any]:
    """Measure a signal the way the module tests do.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    sample_rate:
        Sample rate of the signal in Hz.
    start, end:
        Optional selection in seconds.
    mix:
        Which signal to measure - see :func:`mix_signal`.
    bands:
        The band table - see :func:`band_levels_db`.
    clip_threshold:
        The level from which a sample counts as clipped.

    Returns
    -------
    dict
        ``sample_rate``, ``channels``, ``frames``, ``duration_s``,
        ``peak_db`` (the largest sample of the whole buffer),
        ``true_peak_db``, ``rms_db`` (of the mix),
        ``rms_ch_db`` (the RMS of every channel),
        ``bands`` (a mapping of a band name to its level in dBFS),
        ``clicks`` (the detected transients), ``clip`` (the clipping
        statistics) and ``rt60_s``.
    """
    buf = _as_2d(x)
    region = _region(buf, sample_rate, start, end)
    signal = mix_signal(region, mix)
    stats = clip_stats(region, clip_threshold, mix)
    channel_rms = [
        _db(float(np.sqrt(np.mean(region[:, channel] ** 2)))) if region.shape[0] else LEVEL_FLOOR_DB
        for channel in range(region.shape[1])
    ]
    return {
        "sample_rate": int(sample_rate),
        "channels": int(buf.shape[1]),
        "frames": int(buf.shape[0]),
        "duration_s": int(buf.shape[0]) / float(sample_rate),
        "selection": {
            "start_s": float(start) if start is not None else 0.0,
            "end_s": float(end) if end is not None else int(buf.shape[0]) / float(sample_rate),
            "samples": int(region.shape[0]),
        },
        "peak_db": peak_db(region),
        "true_peak_db": true_peak_db(region),
        "rms_db": rms_db(signal),
        "rms_ch_db": channel_rms,
        "bands": band_levels_db(region, sample_rate, bands, mix=mix),
        "clicks": detect_clicks(region, sample_rate),
        "clip": stats,
        "rt60_s": rt60(region, sample_rate, mix=mix),
    }


def measure_clip(
    clip_id: str,
    manifest: str | Path | None = None,
    start: float | None = None,
    end: float | None = None,
    mix: str | int = "mono",
) -> dict[str, Any]:
    """Measure a clip of the corpus.

    The measurement of the whole clip is returned, with the manifest entry
    of the clip under the key ``"entry"``, so a test can compare a measured
    figure with the ground truth that the manifest carries.
    """
    entry = manifest_entry(clip_id, manifest)
    data, sample_rate = load_clip(clip_id, manifest)
    figures = measure(data, sample_rate, start, end, mix)
    figures["id"] = clip_id
    figures["entry"] = entry
    return figures


# ---------------------------------------------------------------------------
# verification of the corpus against its own ground truth
# ---------------------------------------------------------------------------
#: The level claims the measurement helper knows how to measure.  The key is
#: the name of the claim in ``annotations.levels`` and the value names the
#: measurement that produces it.  Every claim that is not in this table is
#: provenance (the recipe of the clip) and is verified by rebuilding the
#: corpus, not by measuring it.
LEVEL_MEASURERS: dict[str, str] = {
    "peak_dbfs": "peak",
    "rms_dbfs": "rms",
    "total_rms_dbfs": "total_rms",
    "noise_floor_dbfs": "noise_floor",
    "mid_peak_dbfs": "mid_peak",
    "side_peak_dbfs": "side_peak",
    "mid_rms_dbfs": "mid_rms",
    "side_rms_dbfs": "side_rms",
    "left_rms_dbfs": "left_rms",
    "right_rms_dbfs": "right_rms",
    "hiss_rms_dbfs": "side_rms",
}


def _measured_level(data: np.ndarray, sample_rate: int, name: str) -> float | None:
    """The measured value of a level claim, in dBFS.

    ``None`` is returned when the claim is not one this helper measures.
    """
    kind = LEVEL_MEASURERS.get(name)
    if kind is None:
        return None
    if kind == "peak":
        return peak_db(data)
    if kind == "rms":
        return rms_db(data)
    if kind == "total_rms":
        return _db(float(np.sqrt(np.mean(np.sum(data ** 2, axis=1)))))
    if kind == "noise_floor":
        if data.shape[1] < 1:
            return LEVEL_FLOOR_DB
        return max(
            _db(float(np.sqrt(np.mean(data[:, channel] ** 2))))
            for channel in range(data.shape[1])
        )
    if kind == "mid_peak":
        return peak_db(mid_side(data)[0])
    if kind == "side_peak":
        return peak_db(mid_side(data)[1])
    if kind == "mid_rms":
        return rms_db(mid_side(data)[0])
    if kind == "side_rms":
        return rms_db(mid_side(data)[1])
    if kind == "left_rms":
        return _db(float(np.sqrt(np.mean(data[:, 0] ** 2))))
    if kind == "right_rms":
        index = min(1, data.shape[1] - 1)
        return _db(float(np.sqrt(np.mean(data[:, index] ** 2))))
    return None


def _band_level(data: np.ndarray, sample_rate: int, band: Mapping[str, Any]) -> float:
    """The level of a band of the manifest, in dBFS."""
    edges = band["band_hz"]
    region = band.get("region_s")
    return band_energy_db(
        data,
        sample_rate,
        float(edges[0]),
        float(edges[1]),
        region[0] if region else None,
        region[1] if region else None,
        band.get("mix", "mono"),
    )


def _band_level_outside(
    data: np.ndarray,
    sample_rate: int,
    band: Mapping[str, Any],
    annotations: Mapping[str, Any],
) -> float | None:
    """The level of a band *outside* its window and outside the transients.

    The windows of the impulsive events of the clip are cut out as well:
    a drum hit that lies outside the window of the band would otherwise
    make the band look just as loud outside the window as inside it.
    """
    region = band.get("region_s")
    if not region:
        return None
    windows: list[tuple[float, float]] = [(float(region[0]), float(region[1]))]
    for event in annotations.get("events", []):
        if event.get("type") != "click":
            continue
        windows.append((float(event.get("start_s", 0.0)), float(event.get("end_s", 0.0))))
    keep = np.ones(int(data.shape[0]), dtype=bool)
    for start_s, end_s in windows:
        first = int(round(start_s * sample_rate))
        last = int(round(end_s * sample_rate))
        keep[max(0, first) : max(0, last)] = False
    outside = data[keep]
    if outside.shape[0] < 16:
        return None
    return band_energy_db(
        outside,
        sample_rate,
        float(band["band_hz"][0]),
        float(band["band_hz"][1]),
        None,
        None,
        band.get("mix", "mono"),
    )


def verify_corpus(
    tolerance_db: float = 0.3,
    manifest: str | Path | None = None,
    audio_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Check the clips on disk against the ground truth of the manifest.

    Every clip is measured with the functions of this module and every claim
    the manifest makes is compared with the measurement:

    * the file exists and its checksum is the one the manifest records,
    * the sample rate, the channel count and the duration are the declared
      ones,
    * every level the manifest lists in ``annotations.levels`` that names a
      measurement (see :data:`LEVEL_MEASURERS`) is the measured one,
    * every derived claim (a difference of two measured figures) is the
      measured difference,
    * every band of ``annotations.bands`` has the declared level, and a band
      with a region is louder inside the region than outside it,
    * every frequency of ``annotations.frequencies`` is the dominant partial
      of the clip inside its search band,
    * the inter-channel phase and the correlation of the clip are the
      declared ones,
    * the reverberation time of a reverberant clip is the declared one,
    * every click event is a transient that the detector finds where the
      manifest says it is, and no other transient is there,
    * every run of clipped samples is where the manifest says it is.

    Parameters
    ----------
    tolerance_db:
        How far a level may be from its ground truth, in decibels.
    manifest:
        An alternative manifest; ``None`` means the manifest of the corpus.
    audio_dir:
        An alternative root for the audio files; ``None`` means the corpus
        directory itself.  It is what lets a test point the verification at
        a corrupted copy of the corpus.

    Returns
    -------
    dict
        ``{"ok": bool, "checks": [...], "clips": int, "failed": int}`` - one
        entry per check, with ``name``, ``expected``, ``measured``,
        ``tolerance`` and ``ok``.
    """
    checks: list[dict[str, Any]] = []
    root = Path(audio_dir) if audio_dir is not None else CORPUS_DIR

    def _check(name: str, expected: float, measured: float, tolerance: float) -> None:
        """Record one comparison."""
        checks.append(
            {
                "name": name,
                "expected": float(expected),
                "measured": float(measured),
                "tolerance": float(tolerance),
                "ok": abs(float(expected) - float(measured)) <= float(tolerance),
            }
        )

    def _check_bool(name: str, ok: bool, expected: Any = None, measured: Any = None) -> None:
        """Record one yes/no claim."""
        checks.append(
            {
                "name": name,
                "expected": expected,
                "measured": measured,
                "tolerance": 0.0,
                "ok": bool(ok),
            }
        )

    entries = load_manifest(manifest)
    for entry in entries:
        clip_id = str(entry["id"])
        path = root / str(entry["file"])
        if not path.exists():
            _check_bool(f"{clip_id}: file", False, "present", "missing")
            continue

        annotations = dict(entry.get("annotations", {}))
        levels = dict(annotations.get("levels", {}))
        sample_rate = int(entry["sample_rate"])
        data, file_rate = load_clip(clip_id, manifest, root)

        # --- the file itself -------------------------------------------
        _check(f"{clip_id}: sample rate", float(sample_rate), float(file_rate), 0.0)
        _check(f"{clip_id}: channels", float(entry["channels"]), float(data.shape[1]), 0.0)
        duration = int(data.shape[0]) / float(file_rate)
        _check(f"{clip_id}: duration", float(entry["duration_s"]), duration, 1.0 / float(file_rate))
        declared_hash = str(entry.get("sha256", ""))
        if declared_hash:
            _check_bool(
                f"{clip_id}: sha256",
                _sha256_of(path) == declared_hash,
                declared_hash[:12],
                _sha256_of(path)[:12],
            )

        figures = measure(data, file_rate)

        # --- the levels ------------------------------------------------
        for name in sorted(levels):
            measured = _measured_level(data, file_rate, name)
            if measured is None:
                continue
            _check(f"{clip_id}: {name}", levels[name], measured, tolerance_db)

        # --- the derived claims (a difference of measured figures) -----
        band_table = {str(band.get("name", "")): band for band in annotations.get("bands", [])}
        for derived in annotations.get("derived", []):
            total = 0.0
            complete = True
            for term in derived.get("terms", []):
                sign = float(term.get("sign", 1.0))
                if "band" in term:
                    band = band_table.get(str(term["band"]))
                    if band is None:
                        complete = False
                        break
                    total += sign * _band_level(data, file_rate, band)
                else:
                    value = _measured_level(data, file_rate, str(term.get("key", "")))
                    if value is None:
                        complete = False
                        break
                    total += sign * value
            if not complete:
                _check_bool(f"{clip_id}: derived {derived.get('name')}", False, "measurable",
                            "not measurable")
                continue
            _check(
                f"{clip_id}: derived {derived.get('name')}",
                float(derived["value"]),
                total,
                float(derived.get("tolerance", tolerance_db)),
            )

        # --- the band ground truth -------------------------------------
        for band in annotations.get("bands", []):
            measured = _band_level(data, file_rate, band)
            _check(f"{clip_id}: band {band['name']}", band["level_dbfs"], measured, tolerance_db)

            # A band with a window has to be louder inside the window
            # than outside it: that is what makes the window meaningful.
            region = band.get("region_s")
            excess = band.get("min_excess_db")
            if not region or excess is None:
                continue
            inside_level = _band_level(data, file_rate, band)
            outside_level = _band_level_outside(
                data,
                file_rate,
                band,
                annotations,
            )
            if outside_level is None:
                continue
            contrast = inside_level - outside_level
            _check_bool(
                f"{clip_id}: band {band['name']} contrast",
                contrast >= float(excess) - 1e-9,
                f">= {float(excess):.1f} dB",
                f"{contrast:.2f} dB",
            )

        # --- the frequency ground truth --------------------------------
        for frequency in annotations.get("frequencies", []):
            region = frequency.get("region_s")
            search = frequency.get("search_hz")
            low = float(search[0]) if search else 20.0
            high = float(search[1]) if search else 20000.0
            mix = frequency.get("mix", "mono")
            if frequency.get("method") == "centroid":
                measured = spectral_centroid_hz(
                    data,
                    file_rate,
                    region[0] if region else None,
                    region[1] if region else None,
                    mix,
                    low,
                    high,
                )
            else:
                measured = dominant_frequency(
                    data,
                    file_rate,
                    region[0] if region else None,
                    region[1] if region else None,
                    mix,
                    low,
                    high,
                )
            expected = frequency.get("hz")
            if expected is None:
                _check_bool(
                    f"{clip_id}: frequency {frequency['name']}",
                    measured is None,
                    "no partial",
                    "no partial" if measured is None else f"{measured:.2f} Hz",
                )
                continue
            if measured is None:
                _check_bool(f"{clip_id}: frequency {frequency['name']}", False, expected, None)
                continue
            _check(
                f"{clip_id}: frequency {frequency['name']}",
                float(expected),
                measured,
                float(frequency.get("tolerance_hz", 5.0)),
            )

        # --- the inter-channel phase ----------------------------------
        phase = annotations.get("phase")
        if phase:
            region = phase.get("region_s")
            measured = phase_difference_deg(
                data,
                file_rate,
                region[0] if region else None,
                region[1] if region else None,
            )
            if measured is None:
                _check_bool(f"{clip_id}: phase", False, phase.get("degrees"), None)
            else:
                _check(
                    f"{clip_id}: phase",
                    float(phase["degrees"]),
                    measured,
                    float(phase.get("tolerance_deg", 5.0)),
                )
            if phase.get("correlation") is not None:
                correlation = interchannel_correlation(
                    data,
                    file_rate,
                    region[0] if region else None,
                    region[1] if region else None,
                )
                _check(
                    f"{clip_id}: correlation",
                    float(phase["correlation"]),
                    correlation,
                    float(phase.get("tolerance_correlation", 0.02)),
                )

        # --- the reverberation time ------------------------------------
        if "rt60_s" in levels:
            region = annotations.get("regions", {}).get("rt60")
            measured = rt60(
                data,
                file_rate,
                region[0] if region else None,
                region[1] if region else None,
            )
            if measured is None:
                _check_bool(f"{clip_id}: rt60", False, levels["rt60_s"], None)
            else:
                _check(f"{clip_id}: rt60", levels["rt60_s"], measured, 0.3)

        # --- the positions of the events -------------------------------
        found = figures["clicks"]
        click_events = [
            event for event in annotations.get("events", []) if event.get("type") == "click"
        ]
        for event in annotations.get("events", []):
            start = float(event.get("start_s", 0.0))
            end = float(event.get("end_s", 0.0))
            inside = 0.0 <= start < end <= float(entry["duration_s"]) + 1e-9
            _check_bool(
                f"{clip_id}: event {event.get('type')} at {start:.3f} s inside the clip",
                inside,
                f"[{start:.3f}, {end:.3f}]",
                f"[{start:.3f}, {end:.3f}]",
            )

        for event in click_events:
            expected = float(event["start_s"])
            near = [item for item in found if abs(item["time_s"] - expected) < 0.001]
            _check_bool(
                f"{clip_id}: click at {expected:.3f} s",
                bool(near),
                expected,
                near[0]["time_s"] if near else None,
            )
        if click_events:
            # No other transient may hide in the clip.
            _check_bool(
                f"{clip_id}: transient count",
                len(found) == len(click_events),
                len(click_events),
                len(found),
            )

        # The plosive burst has to be a transient inside its window.
        for event in annotations.get("events", []):
            if event.get("type") != "plosive":
                continue
            start = float(event["start_s"])
            end = float(event["end_s"])
            inside = [item for item in found if start <= item["time_s"] <= end]
            _check_bool(
                f"{clip_id}: plosive between {start:.3f} s and {end:.3f} s",
                bool(inside),
                "a transient",
                inside[0]["time_s"] if inside else None,
            )

        # --- the runs of clipped samples --------------------------------
        positions = annotations.get("positions", [])
        if "clipped_sample_count" in levels:
            measured_count = int(figures["clip"]["clipped_samples"])
            _check(
                f"{clip_id}: clipped samples",
                levels["clipped_sample_count"],
                measured_count,
                1.0,
            )
            _check(
                f"{clip_id}: clip runs",
                levels.get("clip_run_count", 0.0),
                figures["clip"]["run_count"],
                0.0,
            )
            _check(
                f"{clip_id}: clipped fraction",
                levels.get("clipped_fraction", 0.0),
                figures["clip"]["clipped_fraction"],
                1e-4,
            )
            for position in positions:
                first = int(round(float(position[0]) * file_rate))
                inside = any(run[0] <= first < run[1] for run in figures["clip"]["runs"])
                _check_bool(
                    f"{clip_id}: clip run at {position[0]:.4f} s",
                    inside,
                    float(position[0]),
                    float(position[0]) if inside else None,
                )

    failed = [item for item in checks if not item.get("ok")]
    return {
        "ok": not failed,
        "clips": len(entries),
        "checks": checks,
        "failed": len(failed),
    }


def _sha256_of(path: Path) -> str:
    """The SHA-256 checksum of a file, in hexadecimal."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Measure the Auralis golden corpus.")
    parser.add_argument("--clip", type=str, default=None, help="the clip to measure")
    parser.add_argument("--list", action="store_true", help="list the clips of the manifest")
    parser.add_argument("--verify", action="store_true",
                        help="verify the corpus against the manifest")
    parser.add_argument("--start", type=float, default=None,
                        help="start of the selection, in seconds")
    parser.add_argument("--end", type=float, default=None, help="end of the selection, in seconds")
    parser.add_argument("--mix", type=str, default="mono", help="mono, mid, side, left or right")
    args = parser.parse_args(argv)

    if args.list:
        for entry in load_manifest():
            print(f"{entry['id']:<14} {entry['duration_s']:>4.1f} s  {entry['file']}")
        return 0

    if args.verify:
        report = verify_corpus()
        for item in report["checks"]:
            mark = "ok  " if item.get("ok") else "FAIL"
            print(f"{mark} {item['name']}")
        print(
            f"{len(report['checks'])} check(s) over {report['clips']} clip(s), "
            f"{report['failed']} failed"
        )
        print("corpus ok" if report["ok"] else "corpus FAILED")
        return 0 if report["ok"] else 1

    if args.clip:
        figures = measure_clip(args.clip, start=args.start, end=args.end, mix=args.mix)
        figures.pop("entry", None)
        print(json.dumps(figures, indent=2, default=str))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
