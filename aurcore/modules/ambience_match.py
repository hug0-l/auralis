"""
aurcore.modules.ambience_match — Ambience Match.

Takes the ambience (noise floor) of one recording and reproduces it under
another recording, so a dialogue can be dropped into a scene that sounds like
it belongs here.

How it works
------------
1. **Profile capture** (:meth:`AmbienceMatch.learn`).  The quietest frames of
   the reference recording are averaged into a noise spectrum; the noise
   level, the modulation depth and the dominant modulation rate are measured
   from the same frames and stored as the *ambience profile*.
2. **Source cleanup.**  The noise floor of the incoming recording is estimated
   over a rolling window of the real signal: the quiet frames of that window
   give the floor of every bin.  Bins that sit at the noise floor, and frames
   that sit below the ``Ambience Threshold`` (relative to the active level of
   the source), are gated out so the original ambience does not fight the
   matched bed.
3. **Synthesis.**  A noise bed is generated whose spectrum follows the
   reference's spectral shape.  For the ``Complex`` type the bed is modulated:
   a slow amplitude envelope plus a random spectral walk, both scaled by
   ``Movement`` and ``Randomness``.  ``Static`` produces an unmodulated bed.

MODEL-INTEGRATION POINT
-----------------------
The separation of the incoming material happens inside
:meth:`AmbienceMatch._analyse_stems`, which returns::

    {"source": (n, ch) ndarray, "ambience": (n, ch) ndarray}

``source`` is the cleaned recording and ``ambience`` is the synthesised bed at
the profile's reference level.  The final mix — and the "Output Ambience Only"
analysis render — is built **only** on top of that dictionary.  A learned
ambience model can therefore replace ``_analyse_stems`` and return the same
two keys without touching the rest of the module.

Performance
-----------
The module is a block processor, so everything it needs for one block has to
be ready in the time the block needs to be played (1333 us for 64 samples at
48 kHz).  The cost of the module used to be about 2.6 times the budget at
64 samples.  Five things were changed to bring it inside:

* The tables that never change are built once and reused: the Hann window
  of the analysis and the synthesis (:func:`_hann`), the log-spaced band
  interpolation table of the spectral walk
  (:meth:`AmbienceMatch._band_tables`), the "same" convolution matrix that
  low-passes the band walk (:func:`_same_conv_matrix`) and the overlap sum
  of the synthesis (:func:`_overlap_sum`).
* The band walk is only evaluated for the frames the bed's analysis actually
  produces.  The convolution used to widen it to the kernel length, and all
  of those extra rows were multiplied out and exponentiated before they were
  thrown away — that alone was a third of the module.
* The analysis and the synthesis work on ``(frames, channels, bins)``.  The
  channel axis sits in the middle because every reduction of the module (the
  noise floor, the frame power, the gate) runs along the frequency axis, and
  NumPy is an order of magnitude faster on a long contiguous inner loop than
  on a two element one.
* The analysis window is applied frame by frame.  The overlapping framing is
  a strided view of the padded signal, and multiplying that view by the
  window reads every sample with a hop sized stride.
* The mono downmix is not analysed for the source gate any more: the floor
  comes from the rolling window of the real signal (see
  :data:`_FLOOR_SAMPLES`), and the hop of the analysis is half the window
  instead of a quarter of it (see :data:`_HOP`).

The source cleanup and the noise floor
--------------------------------------
The floor of a bin is the root mean square of the magnitudes of the *quiet
frames* of the signal.  The root mean square is used and not a percentile
because the magnitude of a noise bin is Rayleigh distributed: its mean is
1.05 dB below its root mean square and the 20th percentile is 5.5 dB below
its mean.  A gate that compares a bin with a percentile *below* the mean can
only open on the quietest dips of a steady bed, never on the bed itself.

The floor is estimated over a rolling window of the real signal
(:data:`_FLOOR_SAMPLES`) and the analysis of the block carries the real
history of the signal (:data:`_HISTORY`), so the magnitudes the gate works
on are the magnitudes of the signal and not the magnitudes of a block that is
mostly padding.
"""
from __future__ import annotations

import io

import numpy as np

from ..params import ParamDesc, TAIL_INFINITE
from ..registry import register
from .base import Module

_EPS = 1e-12

#: Transform used for the profile analysis and the noise synthesis.
_N_FFT = 2048

#: Hop between analysis frames.  The window is 2048 samples (42.7 ms) and the
#: hop is half of it, so the analysis frames overlap by 50 % and the overlap
#: sum of the Hann window pair is constant — the round trip through the
#: analysis and the synthesis is then lossless.  A finer hop would buy a
#: finer time resolution than the module needs: the noise floor it measures
#: moves at 0.1 Hz to 8 Hz, and 46.9 frames per second resolve that with a
#: factor of six to spare, while every extra frame is a full transform of a
#: 2048 point window per block.
_HOP = 1024

#: Number of log-spaced bands used by the spectral random walk.
_N_BANDS = 24

#: Mean square of a periodic Hann window (used to undo the analysis window).
_WIN_MEAN_SQ = 0.375

#: Samples of the input the analysis keeps.  The block is analysed together
#: with the history of the signal, so the magnitudes the gate works on are the
#: magnitudes of the signal and not the magnitudes of a block that is mostly
#: padding.  A window of half the analysis window is enough: the frame that
#: carries the block sits at the peak of the analysis window, and the frames
#: before it only carry the history.
_HISTORY = 1024

#: Samples of the input the noise floor is estimated over.  The window is
#: 683 ms long at 48 kHz, which holds thirty-three analysis frames: enough
#: to find the frames the programme is not in and to average the noise of a
#: bin over them.  A window that is shorter than the gap between two
#: passages of the programme would put the programme itself into the floor.
_FLOOR_SAMPLES = 32768

#: How many new samples trigger a new floor estimate.  The floor of a
#: recording moves at 0.1 Hz to 8 Hz, so it does not have to be estimated
#: more often than every 4096 samples (85 ms at 48 kHz).  The analysis of the
#: rolling window then costs a few tens of microseconds per block.
_FLOOR_HOP = 4096

#: How many decibels above the quietest frame a frame may be and still count
#: as a quiet frame.  The quiet frames are the frames the floor is estimated
#: from: a programme is in a few bins, so the frames it is in are told apart
#: by their total level and not by their median.
_FLOOR_QUIET_DB = 3.0

#: The gap between the active level and the quiet level a recording has to
#: show before the bin gate is allowed to work.  A recording whose quietest
#: frames are as loud as its programme carries no floor of its own, and a
#: gate that cannot tell the programme from the floor would take the
#: programme with it.
_FLOOR_GUARD_DB = 6.0

#: The gate is shut at this multiple of the floor.  The floor is the root
#: mean square of the magnitudes of the quiet frames, so it sits 1.05 dB above
#: the mean of a Rayleigh magnitude; three times the floor is 10.6 dB above
#: the mean, which a steady noise bed falls below and a wanted partial 9 dB
#: above the noise does not.
_GATE_THRESHOLD = 3.0


# ---------------------------------------------------------------------------
# Small local DSP helpers (numpy only — the core never hard-depends on scipy).
# ---------------------------------------------------------------------------
#: Periodic Hann windows, keyed by their length.  A window never changes, so
#: it is built once and handed out read only.
_WINDOWS: dict[int, np.ndarray] = {}

#: Overlap sums of the analysis/synthesis window product, keyed by
#: ``(n_fft, hop, n_frames)``.  The sum is a property of the framing, not of
#: the signal, so it is built once per framing.
_OVERLAP_SUMS: dict[tuple[int, int, int], np.ndarray] = {}

#: "Same" convolution matrices, keyed by ``(kernel length, row length)``.
_CONV_MATRICES: dict[tuple[int, int], np.ndarray] = {}

#: Source bin grids of the shape resampler, keyed by their length.
_SRC_BINS: dict[int, np.ndarray] = {}

#: Source and destination bin grids of the shape resampler, keyed by
#: ``(source length, destination length)``.
_RESAMPLE_GRIDS: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

#: Index arrays that reflect a signal at its edges, keyed by
#: ``(length, left pad, right pad)``.
_REFLECT_INDEX: dict[tuple[int, int, int], np.ndarray] = {}


def _hann(n: int) -> np.ndarray:
    """Periodic Hann window of length ``n`` (cached, read only)."""
    win = _WINDOWS.get(n)
    if win is None:
        win = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=np.float64) / float(n))
        win.flags.writeable = False
        _WINDOWS[n] = win
    return win


def _frame_count(n: int, n_fft: int, hop: int) -> int:
    """Number of centred analysis frames of a signal of ``n`` samples.

    The analysis pads the signal by half a window on the left and a whole
    window on the right, so the count only depends on the signal length.
    """
    if n <= 0:
        return 0
    return 1 + (int(n) + int(n_fft) // 2) // int(hop)


def _pad_edge(x: np.ndarray, left: int, right: int) -> np.ndarray:
    """Pad ``x`` with its edge value (the ``'edge'`` mode of :func:`numpy.pad`).

    The centred analysis needs ``n_fft // 2`` samples before the signal and a
    whole window after it.  When the signal is shorter than half a window the
    analysis pads with the edge value, and building that buffer by hand is a
    good deal cheaper than the general purpose padding routine.
    """
    n = int(x.shape[0])
    out = np.empty((n + left + right,) + x.shape[1:], dtype=np.float64)
    out[left:left + n] = x
    if left:
        out[:left] = x[0]
    if right:
        out[left + n:] = x[-1]
    return out


def _pad_reflect(x: np.ndarray, left: int, right: int) -> np.ndarray:
    """Reflect ``x`` at its edges (the ``'reflect'`` mode of :func:`numpy.pad`).

    Which sample ends up where depends on the length of the signal only, so
    the index that does the reflection is built once and reused.  The general
    purpose padding routine spends more time on its own bookkeeping than on
    the copy it makes.
    """
    n = int(x.shape[0])
    key = (n, int(left), int(right))
    idx = _REFLECT_INDEX.get(key)
    if idx is None:
        idx = np.pad(np.arange(n), (left, right), mode="reflect")
        _REFLECT_INDEX[key] = idx
    return x[idx]


def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Centred short-time Fourier transform -> ``(frames, channels, bins)``.

    The channel axis sits in the middle on purpose: the spectrum is then
    contiguous along the frequency axis, which is the axis every reduction of
    the module (the noise floor, the frame power, the gate) runs over, and the
    analysis window can be applied frame by frame without touching the
    strided view the overlapping framing would produce.
    """
    n = int(x.shape[0])
    ch = int(x.shape[1]) if x.ndim > 1 else 1
    if n == 0:
        return np.zeros((0, ch, n_fft // 2 + 1), dtype=np.complex128)
    left = n_fft // 2
    if n > left:
        # A long enough signal is reflected at the edges.
        xp = _pad_reflect(x, left, n_fft)
    elif n > 1:
        xp = _pad_edge(x, left, n_fft)
    else:
        xp = np.pad(x, ((left, n_fft), (0, 0)), mode="constant")
    n_frames = 1 + (xp.shape[0] - n_fft) // hop
    window = _hann(n_fft)
    frames = np.empty((n_frames, ch, n_fft), dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        np.multiply(xp[start:start + n_fft].T, window, out=frames[i])
    return np.fft.rfft(frames, axis=2)


def _overlap_sum(n_fft: int, hop: int, n_frames: int) -> np.ndarray:
    """Per-sample overlap sum of the squared analysis window (cached)."""
    key = (int(n_fft), int(hop), int(n_frames))
    total = _OVERLAP_SUMS.get(key)
    if total is None:
        w2 = _hann(n_fft) ** 2
        total = np.zeros((n_frames - 1) * hop + n_fft, dtype=np.float64)
        for i in range(n_frames):
            start = i * hop
            total[start:start + n_fft] += w2
        _OVERLAP_SUMS[key] = total
    return total


def _istft(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    """Inverse of :func:`_stft` (weighted overlap-add, Hann analysis+synthesis).

    ``spec`` is ``(frames, channels, bins)``; the result is ``(length, channels)``.
    """
    n_frames = int(spec.shape[0])
    ch = int(spec.shape[1])
    frames = np.fft.irfft(spec, n=n_fft, axis=2) * _hann(n_fft)
    total = (n_frames - 1) * hop + n_fft
    buf = np.zeros((ch, total), dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        buf[:, start:start + n_fft] += frames[i]
    left = n_fft // 2
    # Only the returned slice is used, so only that slice is normalised.
    out = buf[:, left:left + length].T
    wsum = _overlap_sum(n_fft, hop, n_frames)[left:left + length]
    out /= np.maximum(wsum, 1e-8)[:, None]
    return np.ascontiguousarray(out)


def _band_matrix(freqs: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """Linear interpolation matrix from log-spaced band centres to FFT bins."""
    n_bands = centres.shape[0]
    matrix = np.zeros((freqs.shape[0], n_bands), dtype=np.float64)
    log_c = np.log(np.maximum(centres, 1.0))
    log_f = np.log(np.maximum(freqs, 1.0))
    for b in range(n_bands):
        if b == 0:
            matrix[log_f <= log_c[0], b] = 1.0
            continue
        if b == n_bands - 1:
            matrix[log_f >= log_c[-1], b] = 1.0
            continue
        lo, hi = log_c[b - 1], log_c[b]
        sel = (log_f > lo) & (log_f < hi)
        matrix[sel, b] = (log_f[sel] - lo) / (hi - lo)
        matrix[sel, b - 1] = 1.0 - matrix[sel, b]
    return matrix


def _same_conv_matrix(width: int, n: int) -> np.ndarray:
    """Matrix form of the "same" convolution of a length ``n`` row.

    The band walk is low-passed with a Hann kernel, which is the same
    convolution for every column.  Writing it as a matrix turns the per
    column :func:`numpy.convolve` calls into a single matrix product.

    ``A @ x`` equals ``np.convolve(x, kernel, mode="same")`` for a row ``x``
    of length ``n``, with ``kernel`` the normalised Hann window of length
    ``width``.
    """
    key = (int(width), int(n))
    mat = _CONV_MATRICES.get(key)
    if mat is None:
        kernel = np.hanning(width)
        kernel /= kernel.sum()
        total = n + width - 1
        length = max(n, width)
        start = (total - length) // 2
        mat = np.zeros((length, n), dtype=np.float64)
        for i in range(length):
            for j in range(n):
                k = start + i - j
                if 0 <= k < width:
                    mat[i, j] = kernel[k]
        _CONV_MATRICES[key] = mat
    return mat


def _percentile_rows(arr: np.ndarray, q: float) -> np.ndarray:
    """Linear interpolation percentile along the first axis of ``arr``.

    This is :func:`numpy.percentile` with its default ``'linear'`` method,
    written out so that it can use a single partition instead of the general
    purpose implementation.
    """
    n = int(arr.shape[0])
    if n == 0:
        return np.zeros(arr.shape[1:], dtype=np.float64)
    if n == 1:
        return arr[0].copy()
    v = (float(q) / 100.0) * (n - 1)
    lo = int(np.floor(v))
    hi = min(lo + 1, n - 1)
    frac = v - lo
    part = np.partition(arr, (lo, hi), axis=0)
    if hi == lo or frac == 0.0:
        return part[lo].copy()
    return part[lo] + frac * (part[hi] - part[lo])


def _resample_shape(shape: np.ndarray, n_bins: int, sample_rate: float) -> np.ndarray:
    """Resample a spectral shape onto a new bin grid (log-frequency)."""
    shape = np.asarray(shape, dtype=np.float64)
    if shape.ndim != 1:
        shape = shape.ravel()
    if shape.shape[0] == n_bins:
        return shape.copy()
    n = shape.shape[0]
    key = (n, int(n_bins))
    grid = _RESAMPLE_GRIDS.get(key)
    if grid is None:
        src_bins = _SRC_BINS.get(n)
        if src_bins is None:
            src_bins = np.arange(n, dtype=np.float64)
            _SRC_BINS[n] = src_bins
        dst_bins = np.linspace(0.0, n - 1.0, n_bins)
        grid = (src_bins, dst_bins)
        _RESAMPLE_GRIDS[key] = grid
    return np.interp(grid[1], grid[0], shape)


def _rms(x: np.ndarray) -> float:
    """Root-mean-square of a signal."""
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.sum(x * x) / x.shape[0]))


def _db(v: float) -> float:
    """Linear value to decibels."""
    return 20.0 * np.log10(max(float(v), 1e-12))


def _linear(db: float) -> float:
    """Decibels to linear value."""
    return 10.0 ** (float(db) / 20.0)


# ---------------------------------------------------------------------------
# The module.
# ---------------------------------------------------------------------------
@register
class AmbienceMatch(Module):
    """Ambience Match — reproduce a reference room tone under a recording."""

    code = "AUR_AMBIENCEMATCH"
    display_name = "Ambience Match"
    category = "Repair"
    edition = "advanced"
    element = "MatchAmbience"
    analysis = True
    supports_learn = True
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._profile: dict | None = None
        self._rng = np.random.default_rng(0xA0B1)
        # (key, freqs, centres, matrix) of the log-spaced band tables.
        self._band_cache: tuple | None = None
        self._clear_state()

    # -- the state of the rolling analysis ----------------------------------
    def _clear_state(self, channels: int | None = None) -> None:
        """Forget the history, the rolling window and the floor estimate."""
        count = int(channels) if channels else max(1, int(getattr(self, "channels", 1) or 1))
        # The history of the signal, the last _HISTORY samples.
        self._history = np.zeros((0, count), dtype=np.float64)
        # The rolling window the noise floor is estimated over.
        self._floor_buf = np.zeros((_FLOOR_SAMPLES, count), dtype=np.float64)
        self._floor_count = 0        # the samples the window holds
        self._floor_pos = 0          # the slot the oldest sample sits in
        self._floor_pending = 0      # the samples since the last estimate
        self._floor: np.ndarray | None = None   # (channels, bins)
        self._active_db = 0.0        # the level of the programme
        self._quiet_db = 0.0         # the level of the floor
        self._own_profile: dict | None = None   # the profile of the source
        self._last_gate: np.ndarray | None = None   # the bin gate of the last block

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's parameters (mirrors ``aurcore/schema/params.tsv``)."""
        return [
            ParamDesc("Ambience Type", "enum", "Complex", 0.0, 1.0, "", ("Complex", "Static"),
                      group="Algorithm",
                      help="Complex: a modulated, living room tone. Static: an unmodulated bed."),
            ParamDesc("Ambience Threshold", "float", -60.0, -100.0, 0.0, "dB", group="Threshold",
                      help="How far below the active level the source is treated as ambience."),
            ParamDesc("Movement", "float", 0.5, 0.0, 1.0, "", group="Movement",
                      help="How strongly the ambience is modulated."),
            ParamDesc("Randomness", "float", 0.5, 0.0, 1.0, "", group="Movement",
                      help="How chaotic the modulation is."),
            ParamDesc("Gain", "float", 0.0, -24.0, 4.8, "dB", group="Gain",
                      help="Level of the matched ambience relative to the reference profile.",
                      curve="taper"),
            ParamDesc("Output Ambience Only", "bool", False, 0.0, 1.0, "", group="Output",
                      help="Render the synthesised ambience without the source."),
        ]

    # -- cached tables --------------------------------------------------------
    def _band_tables(self, n_fft: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(freqs, centres, matrix)`` of the log-spaced band interpolation.

        The tables depend on the sample rate and the window size only, so
        they are built once and reused for every block.
        """
        key = (int(self.sample_rate), int(n_fft))
        cache = self._band_cache
        if cache is None or cache[0] != key:
            freqs = np.fft.rfftfreq(n_fft, 1.0 / float(self.sample_rate))
            edges = np.geomspace(30.0, max(float(self.sample_rate) / 2.0, 60.0), _N_BANDS + 1)
            centres = np.sqrt(edges[:-1] * edges[1:])
            cache = (key, freqs, centres, _band_matrix(freqs, centres))
            self._band_cache = cache
        return cache[1], cache[2], cache[3]

    # -- profile handling -----------------------------------------------------
    def _noise_magnitudes(self, x: np.ndarray) -> np.ndarray:
        """Per-bin magnitudes of the mono downmix, analysed like the profile.

        The downmix is zero-padded to a full analysis window first, exactly
        like the profile analysis, so that the spectrum the profile is built
        from describes the same signal the profile level is measured on.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        mono = arr.mean(axis=1)
        if mono.size == 0:
            return np.zeros((0, _N_FFT // 2 + 1), dtype=np.float64)
        if mono.size < _N_FFT:
            mono = np.pad(mono, (0, _N_FFT - mono.size))
        spec = _stft(mono[:, None], _N_FFT, _HOP)[:, 0, :]
        return np.abs(spec)

    # -- the rolling noise floor ---------------------------------------------
    def _push_floor(self, arr: np.ndarray) -> None:
        """Add the block to the rolling window and re-estimate the floor.

        The window holds the last :data:`_FLOOR_SAMPLES` samples of the
        input.  It is written as a ring, so a block only costs a copy of its
        own samples; the window is put back together when the floor is
        estimated, which happens once every :data:`_FLOOR_HOP` samples.
        """
        n = int(arr.shape[0])
        if n == 0:
            return
        if self._floor_buf.shape[1] != int(arr.shape[1]):
            self._clear_state(int(arr.shape[1]))
        capacity = _FLOOR_SAMPLES
        if n >= capacity:
            self._floor_buf[:] = arr[-capacity:]
            self._floor_pos = 0
            self._floor_count = capacity
        else:
            pos = self._floor_pos
            first = min(n, capacity - pos)
            self._floor_buf[pos:pos + first] = arr[:first]
            if first < n:
                self._floor_buf[:n - first] = arr[first:]
            self._floor_pos = (pos + n) % capacity
            self._floor_count = min(capacity, self._floor_count + n)
        self._floor_pending += n
        if self._floor_pending < _FLOOR_HOP:
            return
        self._floor_pending = 0
        self._estimate_floor()

    def _floor_window(self) -> np.ndarray:
        """The rolling window as one contiguous array."""
        count = int(self._floor_count)
        if count < _FLOOR_SAMPLES:
            return self._floor_buf[:count].copy()
        pos = int(self._floor_pos)
        if pos == 0:
            return self._floor_buf.copy()
        return np.concatenate([self._floor_buf[pos:], self._floor_buf[:pos]], axis=0)

    def _estimate_floor(self) -> None:
        """The per-bin noise floor of the rolling window.

        The floor of a bin is the root mean square of the magnitudes of the
        quiet frames of the window.  The quiet frames are the frames whose
        total level is at the level of the quietest frame: a programme sits
        in a few bins, so the frame it is in is told apart by the total
        level of the frame and not by the level of a single bin.
        """
        window = self._floor_window()
        if window.shape[0] < _N_FFT:
            self._floor = None
            return
        mag = np.abs(_stft(window, _N_FFT, _HOP))
        power = (mag ** 2).sum(axis=2)
        level_db = 10.0 * np.log10(np.maximum(power, 1e-20))
        overall = level_db.mean(axis=1)
        self._active_db = float(np.percentile(overall, 90.0))
        self._quiet_db = float(np.percentile(overall, 10.0))
        quiet = level_db <= (level_db.min(axis=0, keepdims=True) + _FLOOR_QUIET_DB)
        floor = np.zeros((mag.shape[1], mag.shape[2]), dtype=np.float64)
        for c in range(mag.shape[1]):
            sel = quiet[:, c]
            if not np.any(sel):
                continue
            floor[c] = np.sqrt((mag[sel, c] ** 2).mean(axis=0))
        self._floor = floor
        # The profile of the source, for the case where no reference was
        # learned: the module then matches the source to its own room tone.
        if self._profile is None:
            profile = self._profile_from_magnitudes(mag.mean(axis=1))
            profile["n_fft"] = _N_FFT
            profile["hop"] = _HOP
            if self._active_db - self._quiet_db < _FLOOR_GUARD_DB:
                # The recording shows no floor of its own: its quietest
                # frames are as loud as its programme, so there is no room
                # tone to match.  A bed at the level of the programme would
                # be added *on top of* the programme instead of replacing
                # it, and the module would hand back a recording that is
                # louder than the one it was given: a full scale sine came
                # out at 3.1 before this was fixed.  A recording without a
                # floor of its own gets no bed.
                profile["level"] = 0.0
                profile["level_db"] = -120.0
            self._own_profile = profile

    def _estimate_noise(self, x: np.ndarray) -> dict:
        """Estimate the noise floor spectrum, level and modulation of ``x``.

        Returns a dictionary with

        ``spectrum``
            the mean magnitude spectrum of the quietest frames (STFT units) —
            this is the *shape* the synthesiser follows;
        ``floor``
            a per-bin noise floor (the root mean square of the magnitudes of
            the quiet frames) used by the source gate;
        ``level``
            the noise floor level as an RMS in signal units;
        ``depth`` / ``rate``
            how strongly and how fast the noise floor moves.
        """
        return self._profile_from_magnitudes(self._noise_magnitudes(x))

    def _profile_from_magnitudes(self, mag: np.ndarray, floor: np.ndarray | None = None) -> dict:
        """Build an ambience profile from analysed magnitudes.

        This is the second half of :meth:`_estimate_noise`; it is separate so
        that the magnitudes of the mono downmix can be analysed once and used
        both for the profile and for the source gate.
        """
        n_frames = mag.shape[0]
        if n_frames == 0:
            bins = _N_FFT // 2 + 1
            return {
                "spectrum": np.ones(bins),
                "floor": np.zeros(bins),
                "level": 0.0,
                "depth": 0.0,
                "rate": 1.0,
            }

        frame_power = (mag ** 2).sum(axis=1)
        order = np.argsort(frame_power)
        quiet = order[:max(1, n_frames // 4)]
        spectrum = mag[quiet].mean(axis=0)
        if floor is None:
            # The floor of a bin is the root mean square of the magnitudes of
            # the quiet frames.  It is a real floor: it sits above the mean of
            # a Rayleigh magnitude, so a bin of a steady bed falls below it.
            floor = np.sqrt((mag[quiet] ** 2).mean(axis=0))

        # Undo the analysis window to recover the frame mean square.
        frame_ms = 2.0 * frame_power / ((_N_FFT ** 2) * _WIN_MEAN_SQ)
        quiet_ms = [float(frame_ms[i]) for i in quiet]
        level = float(np.sqrt(sum(quiet_ms) / len(quiet_ms)))

        quiet_db = [10.0 * np.log10(max(float(frame_power[i]), 1e-20)) for i in quiet]
        if len(quiet_db) > 1:
            mean_db = sum(quiet_db) / len(quiet_db)
            spread = float(np.sqrt(sum((v - mean_db) ** 2 for v in quiet_db) / len(quiet_db)))
        else:
            spread = 0.0
        depth = float(np.clip(spread / 6.0, 0.0, 1.0))
        rate = 1.0
        if len(quiet_db) > 4:
            centred = np.asarray(quiet_db, dtype=np.float64)
            centred = centred - centred.mean()
            mag_f = np.abs(np.fft.rfft(centred))
            frame_rate = float(self.sample_rate) / float(_HOP)
            freqs = np.fft.rfftfreq(centred.shape[0], 1.0 / frame_rate)
            band = (freqs >= 0.1) & (freqs <= 8.0)
            if np.any(band):
                rate = float(freqs[band][int(np.argmax(mag_f[band]))])

        return {
            "spectrum": spectrum.astype(np.float64),
            "floor": floor.astype(np.float64),
            "level": level,
            "level_db": _db(level),
            "depth": depth,
            "rate": rate,
        }

    def learn(self, x: np.ndarray) -> None:
        """Capture the reference ambience profile from ``x``.

        The profile holds the reference's noise spectrum, its noise level and
        the character of its modulation.  It is used for every later
        :meth:`process` call and is stored with the module state.
        """
        profile = self._estimate_noise(x)
        profile["n_fft"] = _N_FFT
        profile["hop"] = _HOP
        self._profile = profile

    @property
    def profile(self) -> dict | None:
        """The learned ambience profile (``None`` until :meth:`learn` runs)."""
        return None if self._profile is None else dict(self._profile)

    # -- the model-integration seam ------------------------------------------
    def _analyse_stems(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Split ``x`` into the cleaned source and the matched ambience bed.

        MODEL-INTEGRATION POINT — this method produces the two components the
        module works with and is the only place where audio is inspected.  The
        mix and the "Output Ambience Only" render are built **only** on top of
        the returned dictionary.  A learned ambience model can replace this
        method and return the same two keys ("source", "ambience").

        Parameters
        ----------
        x:
            ``(n_samples, n_channels)`` float64 audio.

        Returns
        -------
        dict
            ``{"source": ndarray, "ambience": ndarray}``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        n, ch = arr.shape
        if n == 0:
            zero = np.zeros((0, ch), dtype=np.float64)
            return {"source": zero, "ambience": zero.copy()}

        # The noise floor is a rolling estimate over the real signal: the
        # quiet frames of the last _FLOOR_SAMPLES samples give the floor of
        # every bin.  The window is only analysed once every _FLOOR_HOP
        # samples, because a floor moves at 0.1 Hz to 8 Hz.
        self._push_floor(arr)
        floor = self._floor

        # --- 1. clean the source -------------------------------------------
        # The block is analysed together with the history of the signal, so
        # the magnitudes the gate works on are the magnitudes of the signal
        # and not the magnitudes of a block that is mostly padding.
        if self._history.shape[1] != int(arr.shape[1]):
            self._history = np.zeros((0, int(arr.shape[1])), dtype=np.float64)
        buf = np.concatenate([self._history, arr], axis=0)
        spec = _stft(buf, _N_FFT, _HOP)
        mag = np.abs(spec)

        if floor is None:
            noise_gate = np.zeros(mag.shape, dtype=np.float64)
        else:
            # The gate compares a bin with the floor of that bin.  The floor
            # is the root mean square of the quiet frames, so it sits above
            # the mean of a Rayleigh magnitude and a steady bed falls below
            # it; the gate is shut three times the floor above.
            safe = np.maximum(floor[None, :, :], 1e-9)
            shut = _GATE_THRESHOLD * safe
            noise_gate = np.clip((shut - mag) / (shut - safe + _EPS), 0.0, 1.0)
            if self._active_db - self._quiet_db < _FLOOR_GUARD_DB:
                # The recording shows no floor of its own: its quietest
                # frames are as loud as its programme, so there is nothing
                # to gate and a gate that worked would take the programme
                # with it.
                noise_gate[:] = 0.0

        # The frame power is the mean over the channels of the power summed
        # over the bins; one pass over the magnitudes is enough.
        frame_power = (mag ** 2).sum(axis=2).mean(axis=1)
        frame_db = 10.0 * np.log10(np.maximum(frame_power, 1e-20))
        active_db = self._active_db
        if floor is None:
            active_db = float(_percentile_rows(frame_db[:, None], 90.0)[0])
        threshold = float(self.params.get("Ambience Threshold"))
        gate_level_db = active_db + threshold
        frame_gate = np.clip((gate_level_db - frame_db) / 6.0, 0.0, 1.0)
        gate = np.maximum(noise_gate, frame_gate[:, None, None])
        # The gate is applied in place: the spectrum is not needed afterwards.
        spec *= (1.0 - gate)
        source = _istft(spec, _N_FFT, _HOP, buf.shape[0])[-n:]
        # The bin gate is kept so that the tests can see what it did.
        self._last_gate = noise_gate

        # The history for the next block: the samples the analysis needs.
        self._history = buf[-_HISTORY:].copy() if buf.shape[0] > _HISTORY else buf.copy()

        # --- 2. synthesise the matched ambience -----------------------------
        profile = self._profile
        if profile is None:
            # No reference captured: match the source to its own room tone.
            profile = self._own_profile
            if profile is None:
                profile = self._profile_from_magnitudes(mag.mean(axis=1))
                profile["n_fft"] = _N_FFT
                profile["hop"] = _HOP
                self._own_profile = profile
        ambience = self._synthesise(n, ch, profile)
        return {"source": source, "ambience": ambience}

    def _synthesise(self, length: int, channels: int, profile: dict) -> np.ndarray:
        """Build a noise bed that follows the reference profile."""
        n = int(length)
        if n == 0:
            return np.zeros((0, max(channels, 1)), dtype=np.float64)
        rng = self._rng

        # 1. white noise shaped by the reference spectrum (exact circular filter)
        white = rng.standard_normal(n)
        bins = n // 2 + 1
        shape = _resample_shape(profile.get("spectrum", np.ones(_N_FFT // 2 + 1)), bins,
                                 float(self.sample_rate))
        peak = float(np.max(shape))
        if peak > 0.0:
            shape = shape / peak
        shaped = np.fft.irfft(np.fft.rfft(white) * shape, n=n)

        complex_type = str(self.params.get("Ambience Type")) == "Complex"
        movement = float(self.params.get("Movement")) if complex_type else 0.0
        randomness = float(self.params.get("Randomness")) if complex_type else 0.0

        n_fft = int(profile.get("n_fft", _N_FFT))
        hop = int(profile.get("hop", _HOP))
        frame_rate = float(self.sample_rate) / float(hop)
        n_frames = max(1, int(np.ceil(n / hop)) + 1)

        # 2. amplitude modulation — a slow, smoothed random envelope
        env = np.ones(n_frames)
        if complex_type and movement > 0.0:
            rate = float(np.clip(profile.get("rate", 1.0), 0.1, 8.0))
            alpha = float(np.clip(np.exp(-1.0 / max(rate * frame_rate, 1.0)), 0.0, 0.999))
            draws = rng.standard_normal(n_frames)
            acc = 0.0
            walk = np.empty(n_frames)
            for i in range(n_frames):
                acc = acc * alpha + draws[i] * (1.0 - alpha)
                walk[i] = acc
            # The standard deviation of a two or three element walk is
            # computed here rather than through numpy: the call overhead of
            # the general purpose routine dominates the arithmetic.
            values = walk.tolist()
            mean_walk = sum(values) / len(values)
            variance = sum((v - mean_walk) ** 2 for v in values) / len(values)
            std = variance ** 0.5 or 1.0
            env = 1.0 + 0.5 * movement * (walk / std)
        env_samples = np.interp(
            np.arange(n, dtype=np.float64),
            np.arange(n_frames, dtype=np.float64) * hop,
            env,
        )
        shaped *= env_samples

        # 3. spectral movement — a random walk over log-spaced bands, applied
        #    through a transparent STFT round trip.
        if complex_type and movement > 0.0:
            _freqs, _centres, matrix = self._band_tables(n_fft)
            step_scale = 0.05 + 0.45 * randomness
            band_walk = np.cumsum(rng.standard_normal((n_frames, _N_BANDS)) * step_scale, axis=0)
            band_walk -= band_walk.mean(axis=0, keepdims=True)
            # low-pass the walk so the movement is slow (a room tone breathes,
            # it does not flutter)
            width = max(3, int(round(0.5 * frame_rate)) | 1)
            smooth = _same_conv_matrix(width, n_frames) @ band_walk
            # normalise the modulation depth so Movement controls it directly
            spread = float(np.std(smooth)) or 1.0
            smooth *= (0.6 * movement) / spread
            # The convolution widens the walk to the kernel length, but only
            # the frames the bed's analysis produces are used, so the envelope
            # is evaluated for those rows alone.
            rows = min(smooth.shape[0], _frame_count(n, n_fft, hop))
            spectral = smooth[:rows] @ matrix.T
            np.exp(spectral, out=spectral)
            spec = _stft(shaped[:, None], n_fft, hop)[:, 0, :]
            # the band walk and the STFT can disagree on the frame count
            # (the STFT pads the signal at both ends) -- align them
            if spectral.shape[0] != spec.shape[0]:
                m = min(spectral.shape[0], spec.shape[0])
                spectral = spectral[:m]
                spec = spec[:m]
            spec = spec * spectral
            shaped = _istft(spec[:, None, :], n_fft, hop, n)[:, 0]

        # 4. level the bed at the reference noise level
        level = float(profile.get("level", 0.0))
        current = _rms(shaped[:, None])
        if level <= 0.0:
            # A profile without a level is the profile of a recording that
            # has no floor of its own: there is no bed to lay under the
            # recording, and the bed has to be silent rather than at the
            # level the shaped noise happens to have.
            shaped = np.zeros_like(shaped)
        elif current > 1e-12:
            shaped *= level / current
        if channels > 1:
            shaped = np.repeat(shaped[:, None], channels, axis=1)
        elif shaped.ndim == 1:
            shaped = shaped[:, None]
        return shaped

    # -- processing -----------------------------------------------------------
    @property
    def tail_samples(self) -> int:
        """The module never stops ringing.

        The module synthesises a bed of its own, so its output does not go
        quiet when the input stops.  With a learned profile the bed stays
        at the level of the reference for as long as the module is
        processed; without one the bed follows the noise floor of the
        source and fades with it.  A host has to be prepared for the bed
        that does not, so the tail is infinite.
        """
        return TAIL_INFINITE

    def process(self, x: np.ndarray) -> np.ndarray:
        """Clean the source and mix the matched ambience underneath it."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        stems = self._analyse_stems(arr)
        gain = _linear(self.params.get("Gain"))
        ambience = stems["ambience"] * gain
        if self.params.get("Output Ambience Only"):
            out = ambience
        else:
            out = stems["source"] + ambience
        return np.ascontiguousarray(out, dtype=np.float64)

    # -- housekeeping ---------------------------------------------------------
    def reset(self) -> None:
        """Forget the learned profile and the rolling analysis."""
        self._profile = None
        self._rng = np.random.default_rng(0xA0B1)
        self._clear_state()

    def serialize(self) -> dict:
        """Store the learned profile so it survives a preset round-trip."""
        if self._profile is None:
            return {}
        return {
            "profile": {
                "shape": np.asarray(self._profile["spectrum"], dtype=np.float64),
                "level": float(self._profile["level"]),
                "depth": float(self._profile.get("depth", 0.0)),
                "rate": float(self._profile.get("rate", 1.0)),
                "n_fft": int(self._profile.get("n_fft", _N_FFT)),
                "hop": int(self._profile.get("hop", _HOP)),
            }
        }

    def deserialize(self, data: dict) -> None:
        """Restore a profile written by :meth:`serialize`."""
        profile = (data or {}).get("profile")
        if not profile:
            return
        try:
            shape = np.asarray(profile["shape"], dtype=np.float64)
            self._profile = {
                "spectrum": shape,
                "floor": shape,
                "level": float(profile.get("level", 0.0)),
                "level_db": _db(float(profile.get("level", 0.0))),
                "depth": float(profile.get("depth", 0.0)),
                "rate": float(profile.get("rate", 1.0)),
                "n_fft": int(profile.get("n_fft", _N_FFT)),
                "hop": int(profile.get("hop", _HOP)),
            }
        except Exception:
            self._profile = None

    # -- state blob -----------------------------------------------------------
    def _state_blob(self) -> bytes:
        """Pack the learned profile into a binary blob (base64 in the preset)."""
        if self._profile is None:
            return b""
        buffer = io.BytesIO()
        np.savez(
            buffer,
            shape=np.asarray(self._profile["spectrum"], dtype=np.float64),
            meta=np.array(
                [
                    float(self._profile.get("level", 0.0)),
                    float(self._profile.get("depth", 0.0)),
                    float(self._profile.get("rate", 1.0)),
                    float(self._profile.get("n_fft", _N_FFT)),
                    float(self._profile.get("hop", _HOP)),
                ],
                dtype=np.float64,
            ),
        )
        return buffer.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a profile from a blob written by :meth:`_state_blob`."""
        if not data:
            return
        try:
            with np.load(io.BytesIO(data)) as loaded:
                shape = np.asarray(loaded["shape"], dtype=np.float64)
                meta = np.asarray(loaded["meta"], dtype=np.float64).ravel()
            self._profile = {
                "spectrum": shape,
                "floor": shape,
                "level": float(meta[0]),
                "level_db": _db(float(meta[0])),
                "depth": float(meta[1]),
                "rate": float(meta[2]),
                "n_fft": int(meta[3]),
                "hop": int(meta[4]),
            }
        except Exception:
            self._profile = None
