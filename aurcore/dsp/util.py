"""
aurcore.dsp.util — small numeric helpers shared by the whole DSP layer.

Conventions used everywhere in ``aurcore.dsp``
---------------------------------------------
* **Audio** samples are ``float64`` in the range ``[-1, 1]``; full scale is
  ``1.0`` which is ``0 dBFS``.
* **Levels** are decibels (``dB``, amplitude convention ``20*log10``) unless a
  name says otherwise.
* **Frequencies** are hertz (``Hz``), **times** are seconds (``s``), angles are
  radians and **sample rates** are samples per second (``sr``).
* Every function documents the *units* and the *shape* of its arguments and of
  its return value.
"""
from __future__ import annotations

import math

import numpy as np

__all__ = [
    "db_to_lin",
    "lin_to_db",
    "next_pow2",
    "is_pow2",
    "freq_to_bin",
    "bin_to_freq",
    "bin_frequencies",
    "frame_times",
    "running_rms",
    "running_peak",
    "envelope_follower",
    "soft_limiter",
    "dc_block",
    "safe_divide",
    "fade_in",
    "fade_out",
    "fade",
    "crossfade",
    "overlap_add",
    "ola",
    "apply_gain_db",
    "normalize_peak",
    "peak_dbfs",
]

#: Smallest level (dB) that the helpers will report; keeps logs finite and
#: matches the usual "digital silence" floor of the audio world.
MIN_DB: float = -300.0

_EPS: float = 1e-12


# ---------------------------------------------------------------------------
# level conversion
# ---------------------------------------------------------------------------
def db_to_lin(db, floor_db: float = MIN_DB):
    """Convert a level in dB to a linear amplitude.

    Parameters
    ----------
    db : float or array_like
        Level(s) in dB, amplitude convention (``20*log10``).
    floor_db : float
        Lower clamp, in dB.  Values below it are lifted to it, so the result
        never reaches zero (and ``-inf`` becomes a finite tiny value).

    Returns
    -------
    float or numpy.ndarray
        Linear amplitude(s) ``10 ** (db / 20)``.  A scalar input gives a
        Python ``float``; anything else gives an array of the same shape.
    """
    arr = np.asarray(db, dtype=np.float64)
    out = np.power(10.0, np.maximum(arr, float(floor_db)) / 20.0)
    if arr.ndim == 0:
        return float(out)
    return out


def lin_to_db(lin, floor_db: float = MIN_DB):
    """Convert a linear amplitude to dB.

    Parameters
    ----------
    lin : float or array_like
        Linear amplitude(s).  The magnitude is used, so the sign is ignored.
    floor_db : float
        Lower clamp in dB; the magnitude is floored at ``10 ** (floor_db/20)``
        so the result is always finite.

    Returns
    -------
    float or numpy.ndarray
        Level(s) in dB, amplitude convention (``20*log10``).
    """
    arr = np.asarray(lin, dtype=np.float64)
    mag = np.maximum(np.abs(arr), 10.0 ** (float(floor_db) / 20.0))
    out = 20.0 * np.log10(mag)
    if arr.ndim == 0:
        return float(out)
    return out


def apply_gain_db(x, db):
    """Scale a signal by a gain given in dB.

    Parameters
    ----------
    x : array_like
        Signal, any shape.
    db : float
        Gain in dB.

    Returns
    -------
    numpy.ndarray
        ``x`` multiplied by ``10 ** (db / 20)`` (same shape as ``x``).
    """
    return np.asarray(x, dtype=np.float64) * db_to_lin(db)


def normalize_peak(x, target: float = 1.0):
    """Scale a signal so its absolute peak equals ``target``.

    Parameters
    ----------
    x : array_like
        Signal, any shape.
    target : float
        Desired absolute peak (linear amplitude, ``1.0`` = 0 dBFS).

    Returns
    -------
    numpy.ndarray
        Scaled signal, same shape as ``x``.  A silent input is returned
        unchanged.
    """
    arr = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak <= _EPS:
        return arr.copy()
    return arr * (float(target) / peak)


def peak_dbfs(x) -> float:
    """Absolute peak of a signal expressed in dBFS.

    Parameters
    ----------
    x : array_like
        Signal, any shape.

    Returns
    -------
    float
        Peak level in dBFS (0.0 = full scale).
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return MIN_DB
    return lin_to_db(float(np.max(np.abs(arr))))


# ---------------------------------------------------------------------------
# sizes and bin conversions
# ---------------------------------------------------------------------------
def next_pow2(n: int) -> int:
    """Smallest power of two greater than or equal to ``n``.

    Parameters
    ----------
    n : int
        Requested size (values <= 1 give 1).

    Returns
    -------
    int
        Power of two (1, 2, 4, 8, ...).
    """
    n = int(n)
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def is_pow2(n: int) -> bool:
    """True when ``n`` is a positive power of two."""
    n = int(n)
    return n > 0 and (n & (n - 1)) == 0


def freq_to_bin(freq: float, n_fft: int, sr: float) -> float:
    """Convert a frequency to a (fractional) FFT bin index.

    Parameters
    ----------
    freq : float
        Frequency in Hz.
    n_fft : int
        FFT size in samples.
    sr : float
        Sample rate in Hz.

    Returns
    -------
    float
        Bin index, ``freq * n_fft / sr``.  May be fractional.
    """
    return float(freq) * float(n_fft) / float(sr)


def bin_to_freq(bin: float, n_fft: int, sr: float) -> float:
    """Convert an FFT bin index to a frequency.

    Parameters
    ----------
    bin : float
        Bin index (may be fractional).
    n_fft : int
        FFT size in samples.
    sr : float
        Sample rate in Hz.

    Returns
    -------
    float
        Frequency in Hz, ``bin * sr / n_fft``.
    """
    return float(bin) * float(sr) / float(n_fft)


def bin_frequencies(n_fft: int, sr: float, onesided: bool = True) -> np.ndarray:
    """Centre frequency of every FFT bin.

    Parameters
    ----------
    n_fft : int
        FFT size in samples.
    sr : float
        Sample rate in Hz.
    onesided : bool
        True (default) returns the ``n_fft // 2 + 1`` bins of a one-sided
        spectrum; False returns all ``n_fft`` bins.

    Returns
    -------
    numpy.ndarray
        Frequencies in Hz, shape ``(n_fft // 2 + 1,)`` or ``(n_fft,)``.
    """
    n_fft = int(n_fft)
    if onesided:
        count = n_fft // 2 + 1
    else:
        count = n_fft
    return np.arange(count, dtype=np.float64) * (float(sr) / float(n_fft))


def frame_times(n_frames: int, hop: int, sr: float, offset: float = 0.0) -> np.ndarray:
    """Time stamp of every analysis frame.

    Parameters
    ----------
    n_frames : int
        Number of frames.
    hop : int
        Hop size in samples.
    sr : float
        Sample rate in Hz.
    offset : float
        Time of the first sample of frame 0, in seconds (default 0.0).

    Returns
    -------
    numpy.ndarray
        Frame centre times in seconds, shape ``(n_frames,)``.
    """
    return offset + np.arange(int(n_frames), dtype=np.float64) * (float(hop) / float(sr))


# ---------------------------------------------------------------------------
# level / loudness followers
# ---------------------------------------------------------------------------
def _frame_sums(x: np.ndarray, window: int, hop: int) -> tuple[int, np.ndarray]:
    """Sum of squares over consecutive windows.  Returns (n_frames, sums)."""
    n = x.shape[0]
    w = int(window)
    if w <= 0:
        raise ValueError("window must be >= 1")
    if n < w:
        return 0, np.zeros((0,) + x.shape[1:], dtype=np.float64)
    n_frames = 1 + (n - w) // int(hop)
    c = np.concatenate(
        [np.zeros((1,) + x.shape[1:], dtype=np.float64), np.cumsum(x * x, axis=0)], axis=0
    )
    starts = np.arange(n_frames, dtype=np.int64) * int(hop)
    return n_frames, c[starts + w] - c[starts]


def running_rms(x, window: int, hop: int | None = None) -> np.ndarray:
    """Root-mean-square level over consecutive windows.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    window : int
        Window length in samples.
    hop : int, optional
        Hop between windows in samples.  Defaults to ``window`` (no overlap).

    Returns
    -------
    numpy.ndarray
        RMS per window, shape ``(n_frames,)`` for a mono input and
        ``(n_frames, n_channels)`` otherwise.  RMS is in linear amplitude.
    """
    arr = np.asarray(x, dtype=np.float64)
    mono = arr.ndim == 1
    if mono:
        arr = arr[:, None]
    elif arr.ndim != 2:
        raise ValueError("x must be 1-D or 2-D")
    w = int(window)
    hop = w if hop is None else int(hop)
    if hop <= 0:
        raise ValueError("hop must be >= 1")
    n_frames, sums = _frame_sums(arr, w, hop)
    rms = np.sqrt(np.maximum(sums, 0.0) / float(w))
    return rms[:, 0] if mono else rms


def running_peak(x, window: int, hop: int | None = None) -> np.ndarray:
    """Absolute peak over consecutive windows.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    window : int
        Window length in samples.
    hop : int, optional
        Hop between windows in samples.  Defaults to ``window``.

    Returns
    -------
    numpy.ndarray
        Peak absolute amplitude per window, shape ``(n_frames,)`` (mono) or
        ``(n_frames, n_channels)``.
    """
    arr = np.asarray(x, dtype=np.float64)
    mono = arr.ndim == 1
    if mono:
        arr = arr[:, None]
    elif arr.ndim != 2:
        raise ValueError("x must be 1-D or 2-D")
    w = int(window)
    hop = w if hop is None else int(hop)
    if hop <= 0:
        raise ValueError("hop must be >= 1")
    n = arr.shape[0]
    if n < w:
        out = np.zeros((0,) + arr.shape[1:], dtype=np.float64)
        return out[:, 0] if mono else out
    n_frames = 1 + (n - w) // hop
    view = np.lib.stride_tricks.sliding_window_view(arr, w, axis=0)  # (n_frames+slack, ch, w)
    view = view[:n_frames * hop : hop] if hop != w else view[:n_frames]
    peak = np.max(np.abs(view), axis=-1)
    return peak[:, 0] if mono else peak


def envelope_follower(
    x,
    sr: float,
    attack_ms: float = 10.0,
    release_ms: float = 100.0,
) -> np.ndarray:
    """Asymmetric attack/release envelope follower on the signal magnitude.

    The follower rises with the ``attack_ms`` time constant when the input
    magnitude is above the current envelope and falls with the ``release_ms``
    time constant otherwise.  One-pole coefficients are
    ``exp(-1 / (tau * sr))`` with ``tau`` in seconds.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    sr : float
        Sample rate in Hz.
    attack_ms : float
        Attack time in milliseconds (rise time constant).
    release_ms : float
        Release time in milliseconds (fall time constant).

    Returns
    -------
    numpy.ndarray
        Envelope in linear amplitude, same shape as ``x``.

    Notes
    -----
    The recursion is inherently sequential; it runs one Python step per
    sample (vectorised over channels).  For long files, feed it decimated
    control signals.
    """
    arr = np.asarray(x, dtype=np.float64)
    mono = arr.ndim == 1
    if mono:
        arr = arr[:, None]
    elif arr.ndim != 2:
        raise ValueError("x must be 1-D or 2-D")
    n = arr.shape[0]
    ch = arr.shape[1]
    out = np.zeros((n, ch), dtype=np.float64)
    if n == 0:
        return out[:, 0] if mono else out

    sr = float(sr)
    a_att = math.exp(-1.0 / (max(float(attack_ms), 1e-6) * 1e-3 * sr))
    a_rel = math.exp(-1.0 / (max(float(release_ms), 1e-6) * 1e-3 * sr))
    g_att = 1.0 - a_att
    g_rel = 1.0 - a_rel

    out = np.empty((n, ch), dtype=np.float64)
    for c in range(ch):
        env = 0.0
        res: list[float] = []
        append = res.append
        for m in np.abs(arr[:, c]).tolist():
            if m > env:
                env = a_att * env + g_att * m
            else:
                env = a_rel * env + g_rel * m
            append(env)
        out[:, c] = res
    return out[:, 0] if mono else out


def soft_limiter(
    x,
    threshold_db: float = -1.0,
    ratio: float = 20.0,
    knee_db: float = 0.0,
) -> np.ndarray:
    """Static soft-knee limiter (a per-sample gain curve, no look-ahead).

    Below ``threshold_db`` the signal is untouched.  Above it the level is
    compressed by ``ratio`` (20 means 20:1).  ``knee_db`` softens the corner
    with a quadratic transition of that width in dB.

    Parameters
    ----------
    x : array_like
        Signal, any shape.
    threshold_db : float
        Threshold in dBFS.
    ratio : float
        Compression ratio (>= 1).  ``ratio == 1`` leaves the signal unchanged.
    knee_db : float
        Soft-knee width in dB (0 = hard knee).

    Returns
    -------
    numpy.ndarray
        Limited signal, same shape as ``x``, still in linear amplitude.
    """
    arr = np.asarray(x, dtype=np.float64)
    ratio = max(float(ratio), 1.0)
    if ratio == 1.0 and knee_db <= 0.0:
        return arr.copy()

    mag = np.abs(arr)
    level = lin_to_db(mag, floor_db=MIN_DB)
    thr = float(threshold_db)
    knee = max(float(knee_db), 0.0)

    if knee > 0.0:
        lo = thr - knee * 0.5
        hi = thr + knee * 0.5
        below = level <= lo
        above = level >= hi
        middle = ~(below | above)
        out_db = np.array(level, dtype=np.float64, copy=True)
        out_db[above] = thr + (level[above] - thr) / ratio
        span = level[middle] - (thr - knee * 0.5)
        out_db[middle] = level[middle] + (1.0 / ratio - 1.0) * span * span / (2.0 * knee)
    else:
        out_db = np.where(level <= thr, level, thr + (level - thr) / ratio)

    gain = db_to_lin(out_db) / np.maximum(mag, 10.0 ** (MIN_DB / 20.0))
    out = np.sign(arr) * mag * gain
    return out


def dc_block(x, sr: float, cutoff_hz: float = 20.0) -> np.ndarray:
    """Remove the DC (very low frequency) component of a signal.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    sr : float
        Sample rate in Hz.
    cutoff_hz : float
        Approximate corner frequency of the high-pass, in Hz.

    Returns
    -------
    numpy.ndarray
        Filtered signal, same shape as ``x``.
    """
    from .filters import DCBlocker  # local import: avoids an import cycle

    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        return DCBlocker(cutoff_hz, sr).process(arr)
    out = np.empty_like(arr)
    for c in range(arr.shape[1]):
        out[:, c] = DCBlocker(cutoff_hz, sr).process(arr[:, c])
    return out


# ---------------------------------------------------------------------------
# arithmetic helpers
# ---------------------------------------------------------------------------
def safe_divide(a, b, eps: float = _EPS):
    """Divide without blowing up on zero denominators.

    Parameters
    ----------
    a, b : array_like or float
        Numerator and denominator, broadcastable.
    eps : float
        Denominators with a magnitude below this are replaced by ``eps``
        (keeping their sign).

    Returns
    -------
    numpy.ndarray or float
        ``a / b`` with a safe denominator.  A scalar input gives a scalar.
    """
    num = np.asarray(a, dtype=np.float64)
    den = np.asarray(b, dtype=np.float64)
    safe = np.where(np.abs(den) < float(eps), np.where(den < 0.0, -float(eps), float(eps)), den)
    out = num / safe
    if num.ndim == 0 and den.ndim == 0:
        return float(out)
    return out


# ---------------------------------------------------------------------------
# fades, crossfades and overlap-add
# ---------------------------------------------------------------------------
def _fade_curve(n: int) -> np.ndarray:
    """Raised-cosine fade of ``n`` samples from 0 to 1."""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(n - 1)
    return 0.5 - 0.5 * np.cos(np.pi * t)


def fade_in(x, n: int) -> np.ndarray:
    """Apply a raised-cosine fade-in to the start of a signal.

    Parameters
    ----------
    x : array_like
        Signal, any shape; the fade is applied along axis 0.
    n : int
        Fade length in samples.  Values larger than the signal length fade the
        whole signal.

    Returns
    -------
    numpy.ndarray
        Faded copy of ``x`` (the input is not modified).
    """
    arr = np.asarray(x, dtype=np.float64)
    n = int(n)
    if n <= 0:
        return arr.copy()
    k = min(n, arr.shape[0])
    curve = _fade_curve(k)
    out = arr.copy()
    out[:k] = out[:k] * curve.reshape((-1,) + (1,) * (arr.ndim - 1))
    return out


def fade_out(x, n: int) -> np.ndarray:
    """Apply a raised-cosine fade-out to the end of a signal.

    Parameters
    ----------
    x : array_like
        Signal, any shape; the fade is applied along axis 0.
    n : int
        Fade length in samples.

    Returns
    -------
    numpy.ndarray
        Faded copy of ``x`` (the input is not modified).
    """
    arr = np.asarray(x, dtype=np.float64)
    n = int(n)
    if n <= 0:
        return arr.copy()
    k = min(n, arr.shape[0])
    curve = _fade_curve(k)[::-1]
    out = arr.copy()
    out[-k:] = out[-k:] * curve.reshape((-1,) + (1,) * (arr.ndim - 1))
    return out


def fade(x, fade_in_samples: int = 0, fade_out_samples: int = 0) -> np.ndarray:
    """Fade both ends of a signal.

    Parameters
    ----------
    x : array_like
        Signal, any shape.
    fade_in_samples : int
        Fade-in length in samples.
    fade_out_samples : int
        Fade-out length in samples.

    Returns
    -------
    numpy.ndarray
        Faded copy of ``x``.
    """
    out = fade_in(x, fade_in_samples)
    return fade_out(out, fade_out_samples)


def crossfade(a, b, n: int) -> np.ndarray:
    """Equal-power crossfade between the tail of ``a`` and the head of ``b``.

    Parameters
    ----------
    a, b : array_like
        Signals, 1-D (or 2-D, faded along axis 0).  ``a`` and ``b`` must have
        the same length along axis 0.
    n : int
        Crossfade length in samples.  Clamped to the signal length.

    Returns
    -------
    numpy.ndarray
        The concatenated result, length ``len(a) + len(b) - n`` along axis 0.
    """
    xa = np.asarray(a, dtype=np.float64)
    xb = np.asarray(b, dtype=np.float64)
    if xa.shape[0] != xb.shape[0]:
        raise ValueError("a and b must have the same length")
    n = int(max(0, min(n, xa.shape[0])))
    if n == 0:
        return np.concatenate([xa, xb], axis=0)
    t = np.arange(n, dtype=np.float64) / float(n)
    shape = (-1,) + (1,) * (xa.ndim - 1)
    w_out = np.cos(0.5 * np.pi * t).reshape(shape)
    w_in = np.sin(0.5 * np.pi * t).reshape(shape)
    mid = xa[-n:] * w_out + xb[:n] * w_in
    return np.concatenate([xa[:-n], mid, xb[n:]], axis=0)


def overlap_add(
    frames,
    hop: int,
    window=None,
    length: int | None = None,
) -> np.ndarray:
    """Overlap-add a stack of frames into a single signal.

    Parameters
    ----------
    frames : array_like
        Frames, shape ``(n_frames, frame_length)``.
    hop : int
        Hop size in samples (the distance between consecutive frame starts).
    window : array_like, optional
        Window of length ``frame_length`` applied to every frame before the
        overlap.  ``None`` means no window.
    length : int, optional
        Exact output length in samples; the result is trimmed or zero-padded.

    Returns
    -------
    numpy.ndarray
        The overlap-added signal, shape ``(length,)`` or
        ``(out_length,)`` when ``length`` is ``None``.
    """
    fr = np.asarray(frames, dtype=np.float64)
    if fr.ndim != 2:
        raise ValueError("frames must be 2-D (n_frames, frame_length)")
    n_frames, frame_len = fr.shape
    hop = int(hop)
    if hop <= 0:
        raise ValueError("hop must be >= 1")
    if n_frames == 0:
        out = np.zeros(0, dtype=np.float64)
    else:
        total = (n_frames - 1) * hop + frame_len
        out = np.zeros(total, dtype=np.float64)
        for i in range(n_frames):
            seg = fr[i]
            if window is not None:
                seg = seg * np.asarray(window, dtype=np.float64)
            out[i * hop : i * hop + frame_len] += seg
    if length is not None:
        length = int(length)
        if out.shape[0] < length:
            out = np.concatenate([out, np.zeros(length - out.shape[0], dtype=np.float64)])
        else:
            out = out[:length]
    return out


#: Short alias for :func:`overlap_add`.
ola = overlap_add
