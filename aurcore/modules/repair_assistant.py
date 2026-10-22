"""
aurcore.modules.repair_assistant — Repair Assistant.

Analyses a recording, works out which problems it most likely has, proposes
settings for them and applies the repairs the user asked for.

Detection pass
--------------
The analysis measures six things:

``noise``
    the broadband noise floor against the active signal level;
``hum``
    the energy at 50 Hz / 60 Hz and their harmonics relative to the
    neighbouring spectrum;
``clicks``
    the density of sample-level outliers (impulsive clicks and pops);
``clipping``
    the ratio of samples sitting on the full-scale rails;
``sibilance``
    the 5–9 kHz energy against the 300 Hz–4 kHz energy;
``reverb``
    the decay time of the energy envelope (a T20 estimate extrapolated to T60).

Every measurement is turned into a 0–1 *score*; the scores are the proposals
the module offers, and they also scale the internal repairs.

The detection is a **file level** measurement: it looks at the recording as a
whole, not at one block.  It therefore runs once — when :meth:`learn` is
called, or on the first block the module processes — and the result is kept in
:attr:`RepairAssistant._report`.  The per block path measures only what the
repairs need (the impulsive outliers and the rails are local to the block) and
re-uses the recorded detection for everything else.  Repeating the detection
on every block was the reason the module needed more than the block budget:
the analysis window is 2048 samples wide whatever the block size is, so the
detection cost the same for 64 samples as for 512.

The two configurations
----------------------
The module works in one of two configurations, and the mode of the chain
decides which one it is (``aurcore.modules.base.Module.processing_mode``):

``realtime``
    The analysis frame is 2048 samples wide and the hop is 512 samples.  The
    module stays inside the budget of a real time block.
``offline``
    The analysis frame is 8192 samples wide and the hop is 2048 samples.  The
    frequency axis is four times finer, so the mains hum at 50/60 Hz sits in
    a bin of its own and the noise floor under a partial is measured without
    the neighbouring partial in it.  There is no deadline in this mode, so
    the frame may be as wide as the job needs.

The module does **not** switch by itself: the mode of the chain is the only
thing that decides which configuration is used, and the latency the module
declares is the latency of the configuration it is in.  The analysis of this
module is block-local -- the frame is centred inside the block and the
overlap-add divides by the window sum -- so the module never delays the
signal and it declares a latency of zero in both modes.

MODEL-INTEGRATION POINT
-----------------------
The analysis and the split into repairable components happen inside
:meth:`RepairAssistant._analyse_stems`, which returns::

    {"signal":   (n, ch) ndarray,   # the input
     "noise":    (n, ch) ndarray,   # the estimated noise floor content
     "hum":      (n, ch) ndarray,   # the estimated mains hum content
     "clicks":   (n, ch) ndarray,   # the detected impulsive clicks
     "clipped":  (n, ch) ndarray,   # the clipped (over-full-scale) content
     "sibilance": (n, ch) ndarray,  # the detected sibilant content
     "reverb":   (n, ch) ndarray}   # the estimated late field

Every repair the module applies is built **only** on top of that dictionary:
the hum repair subtracts the "hum" component, the noise repair subtracts the
"noise" component, and so on.  A learned analyser can therefore replace
``_analyse_stems`` and return the same keys without touching the rest of the
module.
"""
from __future__ import annotations

import io

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module, PROCESSING_MODE_OFFLINE

_EPS = 1e-12

#: Transform used by the analysis pass in the realtime configuration.
_N_FFT = 2048
_HOP = 512

#: Transform used by the analysis pass in the offline configuration.  The
#: frame is four times wider, so the frequency axis is four times finer:
#: the hum at 50 Hz and 60 Hz gets a bin of its own, and the noise floor
#: under a partial is measured without the neighbouring partial in it.
_OFFLINE_N_FFT = 8192
_OFFLINE_HOP = 2048

#: Frequency limits used by the detectors.
_HUM_CANDIDATES = (50.0, 60.0)
_SIBILANCE_LOW = 5000.0
_SIBILANCE_HIGH = 9000.0
_SPEECH_LOW = 300.0
_SPEECH_HIGH = 4000.0

#: The six repairs, with the parameter that drives them and the component
#: they subtract.
_REPAIRS: tuple[tuple[str, str], ...] = (
    ("De-Clip Reduction", "clipped"),
    ("Click Reduction", "clicks"),
    ("Hum Reduction", "hum"),
    ("Noise Reduction", "noise"),
    ("Reverb Reduction", "reverb"),
    ("De-Ess Reduction", "sibilance"),
)

#: The components that need the spectrum of the block.
_SPECTRAL_COMPONENTS = frozenset({"noise", "reverb", "sibilance"})

try:  # pragma: no cover - depends on the environment
    from scipy.ndimage import median_filter as _scipy_median_filter
except Exception:  # pragma: no cover - the numpy fallback below is used
    _scipy_median_filter = None


# ---------------------------------------------------------------------------
# Small local DSP helpers (numpy only — the core never hard-depends on scipy).
# ---------------------------------------------------------------------------
#: Hann windows, keyed by their length.  The analysis asks for the same window
#: several times per block and the window never changes, so it is built once.
_HANN_CACHE: dict[int, np.ndarray] = {}

#: Overlap sums of the squared analysis window, keyed by
#: ``(n_fft, hop, n_frames)``.  The pattern only depends on the framing.
_WINDOW_SUM_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _hann(n: int) -> np.ndarray:
    """Periodic Hann window of length ``n``.

    The window is cached and shared, so callers must not modify the result.
    """
    win = _HANN_CACHE.get(n)
    if win is None:
        win = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=np.float64) / float(n))
        _HANN_CACHE[n] = win
    return win


def _window_sum(n_fft: int, hop: int, n_frames: int) -> np.ndarray:
    """The overlap sum of the squared analysis window for a given framing.

    The synthesis divides by this sum, so it has to cover every sample of the
    overlap-added signal.  It only depends on the framing, so it is cached.
    """
    key = (int(n_fft), int(hop), int(n_frames))
    total = _WINDOW_SUM_CACHE.get(key)
    if total is None:
        squared = _hann(n_fft) ** 2
        total = np.zeros((n_frames - 1) * hop + n_fft, dtype=np.float64)
        for i in range(n_frames):
            start = i * hop
            total[start:start + n_fft] += squared
        _WINDOW_SUM_CACHE[key] = total
    return total


def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Centred short-time Fourier transform.

    Returns a ``(frames, channels, bins)`` complex array.  The signal is
    padded by half a window on the left and by a full window on the right so
    that every sample of the original signal is covered by a complete set of
    overlapping frames.
    """
    n = int(x.shape[0])
    ch = int(x.shape[1]) if x.ndim > 1 else 1
    if n == 0:
        return np.zeros((0, ch, n_fft // 2 + 1), dtype=np.complex128)
    left = n_fft // 2
    mode = "reflect" if n > left else ("edge" if n > 1 else "constant")
    xp = np.pad(x, ((left, n_fft), (0, 0)), mode=mode)
    n_frames = 1 + (xp.shape[0] - n_fft) // hop
    win = np.lib.stride_tricks.sliding_window_view(xp, n_fft, axis=0)  # (L-n+1, ch, n)
    frames = win[::hop][:n_frames]                                     # (F, ch, n)
    # The frames are copied into a contiguous array before the window is
    # applied: multiplying the strided view directly costs six times as much.
    frames = np.ascontiguousarray(frames)
    # The transform runs along the last axis, where the window samples are
    # contiguous: that is the layout the FFT is fastest on.
    return np.fft.rfft(frames * _hann(n_fft), axis=2)                  # (F, ch, bins)


def _istft(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    """Inverse of :func:`_stft` (weighted overlap-add, Hann analysis+synthesis).

    ``spec`` is a ``(frames, channels, bins)`` array, the inverse of what
    :func:`_stft` returns.
    """
    ch = int(spec.shape[1])
    frames = np.fft.irfft(spec, n=n_fft, axis=2) * _hann(n_fft)        # (F, ch, n)
    n_frames = frames.shape[0]
    total = (n_frames - 1) * hop + n_fft
    out = np.zeros((ch, total), dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        out[:, start:start + n_fft] += frames[i]
    wsum = _window_sum(n_fft, hop, n_frames)
    out /= np.maximum(wsum, 1e-8)[None, :]
    left = n_fft // 2
    return np.ascontiguousarray(out[:, left:left + length].T)

def _median_filter_1d(x: np.ndarray, size: int) -> np.ndarray:
    """Centred median filter of a one-dimensional signal."""
    if size <= 1:
        return np.asarray(x, dtype=np.float64).copy()
    half = size // 2
    padded = np.pad(x, (half, half), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.median(view, axis=-1)


def _median_filter_2d(arr: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Median filter of a spectrogram along ``axis`` (scipy when available).

    The values are those of ``scipy.ndimage.median_filter(arr, size=size,
    axes=axis, mode="nearest")``: the window is centred and the border is
    repeated.  The numpy path is the fallback and computes the same values
    with a partial sort.
    """
    if size <= 1:
        return np.asarray(arr, dtype=np.float64).copy()
    half = size // 2
    if _scipy_median_filter is not None:  # pragma: no branch - environment dependent
        # scipy repeats the border itself ("nearest"), so the array is not
        # padded here and the result keeps the shape of the input.
        return np.asarray(
            _scipy_median_filter(arr, size=size, axes=axis, mode="nearest"),
            dtype=np.float64,
        )
    if axis == 0:
        padded = np.pad(arr, ((half, half), (0, 0)), mode="edge")
    else:
        padded = np.pad(arr, ((0, 0), (half, half)), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, size, axis=axis)
    if size % 2:
        out = np.partition(view, half, axis=-1)[..., half]
    else:
        part = np.partition(view, (half - 1, half), axis=-1)
        out = 0.5 * (part[..., half - 1] + part[..., half])
    return np.ascontiguousarray(out, dtype=np.float64)


def _percentile_axis0(arr: np.ndarray, q: float) -> np.ndarray:
    """The ``q`` percentile of every column of ``arr`` (along axis 0).

    The same values as ``np.percentile(arr, q, axis=0)``, computed with a
    partial sort instead of the generic quantile machinery.
    """
    n = int(arr.shape[0])
    if n == 0:
        return np.zeros(arr.shape[1], dtype=np.float64)
    position = float(q) / 100.0 * (n - 1)
    low = int(np.floor(position))
    high = min(low + 1, n - 1)
    frac = position - low
    part = np.partition(arr, (low, high), axis=0)
    lo = np.take(part, low, axis=0)
    hi = np.take(part, high, axis=0)
    return lo + (hi - lo) * frac


def _onepole_lp(x: np.ndarray, cutoff_hz: float, sample_rate: float) -> np.ndarray:
    """One-pole low-pass, implemented with an FFT convolution (numpy only)."""
    n = int(x.shape[0])
    if n == 0:
        return x.copy()
    alpha = 1.0 - float(np.exp(-2.0 * np.pi * float(cutoff_hz) / float(sample_rate)))
    decay = 1.0 - alpha
    span = 6.0 * float(sample_rate) / (2.0 * np.pi * max(cutoff_hz, 1.0))
    taps = int(min(n, max(64.0, span)))
    kernel = alpha * decay ** np.arange(taps, dtype=np.float64)
    size = n + taps - 1
    nfft = 1 << int(np.ceil(np.log2(max(size, 2))))
    spectrum = np.fft.rfft(x, nfft) * np.fft.rfft(kernel, nfft)
    return np.fft.irfft(spectrum, nfft)[:n]


def _notch_component(
    x: np.ndarray, freq: float, cutoff_hz: float, sample_rate: float
) -> np.ndarray:
    """Estimate and remove a narrow component at ``freq`` (heterodyne + low-pass).

    Returns the *removed* content, so the caller decides how much of it to
    subtract.  The estimate uses single-sideband demodulation followed by two
    cascaded one-pole low-pass filters.
    """
    n = int(x.shape[0])
    if n == 0 or freq <= 0.0:
        return np.zeros_like(x)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    w = 2.0 * np.pi * float(freq)
    cos = np.cos(w * t)
    sin = np.sin(w * t)
    lp_c = _onepole_lp(x * cos, cutoff_hz, sample_rate)
    lp_s = _onepole_lp(x * sin, cutoff_hz, sample_rate)
    return 2.0 * (lp_c * cos + lp_s * sin)


def _band_edges(freqs: np.ndarray, low: float, high: float) -> np.ndarray:
    """Boolean mask of the bins inside ``[low, high]``."""
    return (freqs >= low) & (freqs < high)


def _runs(idx: np.ndarray) -> list[tuple[int, int]]:
    """Group a sorted index array into ``(start, end)`` contiguous runs."""
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [idx.size - 1]))
    return [(int(idx[s]), int(idx[e])) for s, e in zip(starts, ends)]


def _interpolate_runs(x: np.ndarray, mask: np.ndarray, cubic: bool) -> np.ndarray:
    """Replace the masked samples with an interpolation of their surroundings.

    The interpolation is a Lagrange polynomial evaluated in a local (run
    relative) coordinate, which keeps the fit well conditioned even when the
    run sits far into the signal.  ``cubic`` uses three samples on each side
    (a fifth order polynomial); otherwise the run is bridged linearly.
    """
    y = np.array(x, dtype=np.float64, copy=True)
    idx = np.flatnonzero(mask)
    n = y.shape[0]
    left = 3 if cubic else 1
    right = 3 if cubic else 1
    for a, b in _runs(idx):
        length = float(b - a + 1)
        if a - left < 0 or b + right >= n:
            # not enough context: bridge the run linearly if we can
            if a - 1 >= 0 and b + 1 < n:
                span = np.arange(a, b + 1, dtype=np.float64) - a
                total = float(b + 1 - a)
                y[a:b + 1] = y[a - 1] * (1.0 - span / total) + y[b + 1] * (span / total)
            continue
        # local coordinate: u = 0 sits on the sample before the run
        u = np.concatenate([np.arange(1 - left, 1), np.arange(b - a + 2, b - a + 2 + right)])
        u = u.astype(np.float64) / max(length, 1.0)
        v = np.concatenate([y[a - left:a], y[b + 1:b + 1 + right]])
        try:
            matrix = np.vander(u, u.shape[0])
            coeff = np.linalg.solve(matrix, v)
            uu = np.arange(1, b - a + 2, dtype=np.float64) / max(length, 1.0)
            y[a:b + 1] = np.polyval(coeff, uu)
        except np.linalg.LinAlgError:
            span = np.arange(a, b + 1, dtype=np.float64) - a
            total = float(b + 1 - a)
            y[a:b + 1] = y[a - 1] * (1.0 - span / total) + y[b + 1] * (span / total)
    return y


def _db(v: float) -> float:
    """Linear value to decibels."""
    return 20.0 * np.log10(max(float(v), 1e-12))


def _rms(x: np.ndarray) -> float:
    """Root-mean-square of a (n, ch) signal."""
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.sum(x * x, axis=1))))


# ---------------------------------------------------------------------------
# The module.
# ---------------------------------------------------------------------------
@register
class RepairAssistant(Module):
    """Repair Assistant — detect problems, propose settings and apply repairs."""

    code = "AUR_REPAIRASSISTANT"
    display_name = "Repair Assistant"
    category = "Repair"
    edition = "standard"
    element = "RepairAssistant"
    analysis = False
    supports_learn = True
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._report: dict = {}
        self._hum_hz: float = 0.0
        #: The mode the module was in when the state was written.  It travels
        #: with the state, so that a session that was rendered offline says
        #: so when it is opened again; it never decides the mode the module
        #: runs in.
        self._state_mode = self.processing_mode
        # The analysis geometry of the configuration the module is in.  It
        # follows the mode, so it is built here and rebuilt when the mode
        # changes.
        self._n_fft = 0
        self._hop = 0
        self._apply_mode()

    # -- the configuration ----------------------------------------------------
    def _apply_mode(self) -> None:
        """Build the analysis geometry of the configuration the module is in.

        The two configurations differ in the length of the analysis frame and
        in nothing else: the detectors, the thresholds and the repairs are
        the same, so the offline configuration is the same module with a
        finer frequency axis.

        The detection belongs to the configuration it was made in, so a change
        of the configuration throws the report away and the next block that
        needs it measures the recording again.
        """
        offline = self.processing_mode == PROCESSING_MODE_OFFLINE
        n_fft = _OFFLINE_N_FFT if offline else _N_FFT
        hop = _OFFLINE_HOP if offline else _HOP
        if n_fft == self._n_fft and hop == self._hop:
            return
        self._n_fft = n_fft
        self._hop = hop
        self._freqs = np.fft.rfftfreq(n_fft, 1.0 / float(self.sample_rate))
        self._sibilance_band = _band_edges(self._freqs, _SIBILANCE_LOW, _SIBILANCE_HIGH)
        self._speech_band = _band_edges(self._freqs, _SPEECH_LOW, _SPEECH_HIGH)
        self._ess_band = np.zeros(self._freqs.size, dtype=np.float64)
        self._ess_band[self._sibilance_band] = 1.0
        # the detection was made with the geometry of the other configuration
        self._report = {}
        self._hum_hz = 0.0

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's parameters (mirrors ``aurcore/schema/params.tsv``)."""
        return [
            ParamDesc("Processing Mode", "enum", "realtime", 0.0, 1.0, "",
                      ("realtime", "offline"), group="Algorithm",
                      help="Realtime keeps the module inside the budget of a block; offl"
                      "ine uses the best configuration the module has and declares "
                      "the look-ahead it costs."),
            ParamDesc("Mode", "enum", "Voice", 0.0, 1.0, "", ("Voice", "Music"),
                      group="Algorithm",
                      help="Voice tunes the detectors for speech, Music for a full mix."),
            ParamDesc("Noise Reduction", "float", 0.0, 0.0, 1.0, "", group="Clean Up",
                      help="How much of the detected noise floor to remove."),
            ParamDesc("Reverb Reduction", "float", 0.0, 0.0, 1.0, "", group="Clean Up",
                      help="How much of the detected room reverb to remove."),
            ParamDesc("Hum Reduction", "float", 0.0, 0.0, 1.0, "", group="Clean Up",
                      help="How much of the detected mains hum to remove."),
            ParamDesc("Click Reduction", "float", 0.0, 0.0, 1.0, "", group="Clean Up",
                      help="How much of the detected clicks to remove."),
            ParamDesc("De-Ess Reduction", "float", 0.0, 0.0, 1.0, "", group="Tone",
                      help="How much of the detected sibilance to tame."),
            ParamDesc("De-Clip Reduction", "float", 0.0, 0.0, 1.0, "", group="Tone",
                      help="How much of the detected clipping to repair."),
        ]

    # -- public analysis -----------------------------------------------------
    def analysis_report(self) -> dict:
        """The last detection result (empty until :meth:`learn` has run)."""
        return dict(self._report)

    def learn(self, x: np.ndarray) -> None:
        """Run the detection pass over ``x`` and store the findings.

        The report is what the user interface shows: the measured values, the
        0–1 scores and the proposed settings for the six repair amounts.
        """
        self._apply_mode()
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        stems, report = self._analyse(arr)
        self._report = report
        self._hum_hz = float(report.get("hum_frequency_hz", 0.0))
        del stems

    # -- detection helpers ----------------------------------------------------
    def _detect_hum(self, mono: np.ndarray) -> tuple[float, float]:
        """Estimate the mains hum: returns ``(frequency, score_db)``."""
        n = int(mono.shape[0])
        if n < 1024:
            return 0.0, 0.0
        spectrum = np.abs(np.fft.rfft(mono))
        freqs = np.fft.rfftfreq(n, 1.0 / float(self.sample_rate))
        best_f = 0.0
        best_score = 0.0
        for f0 in _HUM_CANDIDATES:
            score = 0.0
            for k in range(1, 9):
                f = f0 * k
                if f > freqs[-1] - 45.0:
                    break
                band = _band_edges(freqs, f - 2.0, f + 2.0)
                if not np.any(band):
                    continue
                left = _band_edges(freqs, max(f - 45.0, 1.0), f - 8.0)
                right = _band_edges(freqs, f + 8.0, f + 45.0)
                neighbours = np.concatenate([spectrum[left], spectrum[right]])
                if neighbours.size == 0:
                    continue
                local = float(np.median(neighbours))
                level = float(np.mean(spectrum[band]))
                ratio = 10.0 * np.log10((level + 1e-12) / (local + 1e-12))
                score = max(score, ratio)
            if score > best_score:
                best_score = score
                best_f = f0
        return best_f, best_score

    def _detect_clicks(self, mono: np.ndarray) -> tuple[np.ndarray, float]:
        """Find impulsive outliers.  Returns ``(mask, density_per_second)``.

        A click is measured as the deviation of a sample from the straight
        line through its two neighbours.  A steady tone barely deviates from
        that line, so tones are not mistaken for clicks, while a genuine
        impulse stands out by orders of magnitude.
        """
        n = int(mono.shape[0])
        if n < 8:
            return np.zeros(n, dtype=bool), 0.0
        neighbour = 0.5 * (np.concatenate((mono[:1], mono[:-1]))
                           + np.concatenate((mono[1:], mono[-1:])))
        deviation = np.abs(mono - neighbour)
        median = float(np.median(deviation))
        mad = float(np.median(np.abs(deviation - median)))
        sigma = 1.4826 * mad
        peak = float(np.max(np.abs(mono)))
        threshold = max(8.0 * sigma, 0.002 * peak, 1e-7)
        mask = deviation > threshold
        # A click is an isolated outlier: keep only the strongest sample of
        # every run of outliers (the neighbouring samples of a click also look
        # odd, but the click itself is the worst of them).
        for a, b in _runs(np.flatnonzero(mask)):
            segment = deviation[a:b + 1]
            mask[a:b + 1] = False
            mask[a + int(np.argmax(segment))] = True
        density = float(np.count_nonzero(mask)) * float(self.sample_rate) / float(n)
        return mask, density

    def _detect_clipping(self, mono: np.ndarray) -> tuple[np.ndarray, float]:
        """Find samples sitting on the rails.  Returns ``(mask, ratio)``."""
        n = int(mono.shape[0])
        if n == 0:
            return np.zeros(0, dtype=bool), 0.0
        peak = float(np.max(np.abs(mono)))
        if peak < 0.9:
            return np.zeros(n, dtype=bool), 0.0
        threshold = max(peak * 0.995, 0.999)
        mask = np.abs(mono) >= threshold
        ratio = float(np.count_nonzero(mask)) / float(n)
        return mask, ratio

    def _detect_reverb(self, frame_db: np.ndarray) -> tuple[np.ndarray, float, float]:
        """Estimate the late field.  Returns ``(late_ratio, t60_seconds, presence)``."""
        if frame_db.size < 4:
            return np.zeros(0), 0.0, 0.0
        peak = int(np.argmax(frame_db))
        level = frame_db[peak]
        # Walk forward until the envelope has dropped 20 dB below the loudest
        # level seen since the last onset.  A slow decay must not be mistaken
        # for a new onset, so only a *louder* frame restarts the measurement.
        drop = -1
        for i in range(peak + 1, frame_db.size):
            if frame_db[i] > level:
                level = frame_db[i]
                continue
            if frame_db[i] <= level - 20.0:
                drop = i
                break
        if drop < 0:
            t20 = 0.0
            t60 = 0.0
        else:
            t20 = (drop - peak) * float(self._hop) / float(self.sample_rate)
            t60 = 3.0 * t20

        # Late-field ratio from an exponentially decaying envelope follower.
        energy = 10.0 ** (frame_db / 10.0)
        dt = float(self._hop) / float(self.sample_rate)
        rate = dt / 0.25
        idx = np.arange(energy.shape[0], dtype=np.float64)
        logs = np.log(np.maximum(energy, 1e-30)) + idx * rate
        tail = np.exp(np.maximum.accumulate(logs) - idx * rate)
        direct = np.exp(np.maximum.accumulate(
            np.log(np.maximum(energy, 1e-30)) + idx * (dt / 0.02)) - idx * (dt / 0.02))
        late = np.clip((tail - direct) / np.maximum(tail, _EPS), 0.0, 1.0)
        presence = float(np.clip(late.mean() / 0.15, 0.0, 1.0))
        return late, t60, presence

    # -- the spectral analysis ------------------------------------------------
    def _spectral_parts(self, arr: np.ndarray) -> dict[str, np.ndarray]:
        """The spectral components of one block and the quantities they need.

        Returns the spectrum, the mean magnitude, the per frame level and the
        three share masks (noise, reverb, sibilance).  The share masks are
        what the spectral repairs subtract, and they only depend on the block
        itself — so the expensive half of the detection (the harmonic and
        percussive split, which needs two median filters) does not have to run
        again for every block.
        """
        spec = _stft(arr, self._n_fft, self._hop)
        mag = np.abs(spec)
        mean_mag = mag.mean(axis=1)

        frame_power = (mag ** 2).sum(axis=1).mean(axis=1)
        frame_ms = 2.0 * frame_power / ((self._n_fft ** 2) * 0.375)
        frame_db = 10.0 * np.log10(np.maximum(frame_ms, 1e-20))

        # Noise component: the share of every bin that the noise floor covers.
        noise_floor = _percentile_axis0(mean_mag, 20.0)
        share_noise = np.clip(
            noise_floor[None, :] / np.maximum(mean_mag, 1e-12), 0.0, 1.0)

        # Reverb component: the late field of every frame.
        late, t60, presence = self._detect_reverb(frame_db)
        if late.size != mean_mag.shape[0]:
            # the detector gives up on very short blocks; treat it as "no reverb"
            late = np.zeros(mean_mag.shape[0], dtype=np.float64)
        share_reverb = (late * presence)[:, None] * np.ones((1, mean_mag.shape[1]))

        # Sibilance component: the excess of the sibilance band over the
        # speech band, per frame.
        per_frame_sib = mean_mag[:, self._sibilance_band].sum(axis=1)
        per_frame_speech = mean_mag[:, self._speech_band].sum(axis=1)
        frame_ratio_db = 10.0 * np.log10(
            (per_frame_sib + 1e-12) / (per_frame_speech + 1e-12))
        ess_excess = np.clip((frame_ratio_db - 6.0) / 14.0, 0.0, 1.0)
        share_ess = ess_excess[:, None] * self._ess_band[None, :]

        return {
            "spec": spec,
            "mean_mag": mean_mag,
            "frame_db": frame_db,
            "frame_ratio_db": frame_ratio_db,
            "share_noise": share_noise,
            "share_reverb": share_reverb,
            "share_ess": share_ess,
            "presence": float(presence),
            "t60": float(t60),
        }

    def _components(self, arr: np.ndarray, parts: dict[str, np.ndarray] | None,
                    needed: set[str]) -> dict[str, np.ndarray]:
        """The seven repair components of one block.

        Only the components in ``needed`` are measured; the others are
        returned as silence, which is what a repair that is switched off
        does with them anyway.
        """
        n, ch = arr.shape
        mono = arr.mean(axis=1)

        hum_component = np.zeros_like(arr)
        clicks_component = np.zeros_like(arr)
        clipped_component = np.zeros_like(arr)

        if "clicks" in needed:
            click_mask, _density = self._detect_clicks(mono)
            if np.any(click_mask):
                impulse = np.zeros(n)
                impulse[click_mask] = mono[click_mask]
                clicks_component = np.repeat(impulse[:, None], ch, axis=1)

        if "clipped" in needed:
            clip_mask, _ratio = self._detect_clipping(mono)
            if np.any(clip_mask):
                excess = np.zeros(n)
                excess[clip_mask] = mono[clip_mask] - np.sign(mono[clip_mask]) * 0.999
                clipped_component = np.repeat(excess[:, None], ch, axis=1)

        if "hum" in needed:
            hum_hz, hum_score_db = self._detect_hum(mono)
            if self._hum_hz > 0.0:
                hum_hz = self._hum_hz
            if hum_hz > 0.0 and hum_score_db > 3.0:
                cutoff = max(6.0, hum_hz * 0.18)
                base = _notch_component(mono, hum_hz, cutoff, float(self.sample_rate))
                for k in range(2, 5):
                    f = hum_hz * k
                    if f > float(self.sample_rate) * 0.45:
                        break
                    base = base + _notch_component(mono, f, cutoff, float(self.sample_rate))
                hum_component = np.repeat(base[:, None], ch, axis=1)

        noise = np.zeros_like(arr)
        sibilance = np.zeros_like(arr)
        reverb = np.zeros_like(arr)
        if parts is not None:
            spec = parts["spec"]
            if "noise" in needed:
                noise = _istft(spec * parts["share_noise"][:, None, :], self._n_fft, self._hop, n)
            if "sibilance" in needed:
                sibilance = _istft(spec * parts["share_ess"][:, None, :], self._n_fft, self._hop, n)
            if "reverb" in needed:
                reverb = _istft(spec * parts["share_reverb"][:, None, :], self._n_fft, self._hop, n)

        return {
            "signal": arr,
            "noise": noise,
            "hum": hum_component,
            "clicks": clicks_component,
            "clipped": clipped_component,
            "sibilance": sibilance,
            "reverb": reverb,
        }

    # -- the model-integration seam ------------------------------------------
    def _analyse(self, x: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
        """Detect the problems in ``x`` and split it into repairable components."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        n, ch = arr.shape
        zero = np.zeros((n, ch), dtype=np.float64)
        if n == 0:
            empty = {
                "noise_floor_db": -120.0, "active_level_db": -120.0, "noise_ratio_db": 0.0,
                "hum_frequency_hz": 0.0, "hum_score_db": 0.0, "click_density_hz": 0.0,
                "clipped_ratio": 0.0, "sibilance_ratio_db": 0.0, "reverb_t60": 0.0,
                "scores": {}, "suggestions": {}, "mode": str(self.params.get("Mode")),
            }
            return ({"signal": zero, "noise": zero.copy(), "hum": zero.copy(),
                     "clicks": zero.copy(), "clipped": zero.copy(),
                     "sibilance": zero.copy(), "reverb": zero.copy()}, empty)

        parts = self._spectral_parts(arr)
        mean_mag = parts["mean_mag"]
        frame_db = parts["frame_db"]
        frame_ratio_db = parts["frame_ratio_db"]

        # Noise floor: the non-harmonic part of the spectrum.  Splitting the
        # energy into harmonic and non-harmonic parts is the only way to find
        # a noise floor under a continuous sound — a plain frame-energy
        # minimum sees the tone itself and reports a floor far too high.
        harm_time = _median_filter_2d(mean_mag, 11, axis=0)
        perc_freq = _median_filter_2d(mean_mag, 11, axis=1)
        hp = (harm_time ** 2) / (harm_time ** 2 + perc_freq ** 2 + _EPS)
        harmonicity = np.clip((hp - 0.5) / 0.5, 0.0, 1.0)
        harmonic_energy = (mean_mag ** 2 * harmonicity).sum(axis=1)
        noise_energy = (mean_mag ** 2 * (1.0 - harmonicity)).sum(axis=1)
        floor_db = float(10.0 * np.log10(max(np.median(noise_energy), 1e-20)))
        active_db = float(10.0 * np.log10(max(np.median(harmonic_energy + noise_energy), 1e-20)))
        noise_ratio_db = active_db - floor_db

        mono = arr.mean(axis=1)
        voice_mode = str(self.params.get("Mode")) == "Voice"

        # --- sibilance statistic ---------------------------------------------
        active = frame_db > (floor_db + 6.0)
        if np.any(active):
            sibilance_ratio_db = float(np.median(frame_ratio_db[active]))
        else:
            sibilance_ratio_db = float(np.median(frame_ratio_db)) if frame_ratio_db.size else 0.0

        # --- time domain measurements -----------------------------------------
        _click_mask, click_density = self._detect_clicks(mono)
        _clip_mask, clipped_ratio = self._detect_clipping(mono)
        hum_hz, hum_score_db = self._detect_hum(mono)
        if self._hum_hz > 0.0:
            hum_hz = self._hum_hz

        # --- scores and proposals ---------------------------------------------
        if voice_mode:
            noise_score = float(np.clip((20.0 - noise_ratio_db) / 12.0, 0.0, 1.0))
            hum_score = float(np.clip((hum_score_db - 6.0) / 12.0, 0.0, 1.0))
            ess_score = float(np.clip((sibilance_ratio_db - 6.0) / 14.0, 0.0, 1.0))
            reverb_score = float(np.clip((parts["t60"] - 0.15) / 0.5, 0.0, 1.0))
        else:
            noise_score = float(np.clip((16.0 - noise_ratio_db) / 14.0, 0.0, 1.0))
            hum_score = float(np.clip((hum_score_db - 8.0) / 12.0, 0.0, 1.0))
            ess_score = float(np.clip((sibilance_ratio_db - 12.0) / 12.0, 0.0, 1.0))
            reverb_score = float(np.clip((parts["t60"] - 0.10) / 0.4, 0.0, 1.0))
        click_score = float(np.clip(click_density, 0.0, 1.0))
        clip_score = float(np.clip(clipped_ratio / 0.01, 0.0, 1.0))

        scores = {
            "Noise Reduction": noise_score,
            "Reverb Reduction": reverb_score,
            "Hum Reduction": hum_score,
            "Click Reduction": click_score,
            "De-Ess Reduction": ess_score,
            "De-Clip Reduction": clip_score,
        }
        report = {
            "mode": str(self.params.get("Mode")),
            "noise_floor_db": float(floor_db),
            "active_level_db": float(active_db),
            "noise_ratio_db": float(noise_ratio_db),
            "hum_frequency_hz": float(hum_hz),
            "hum_score_db": float(hum_score_db),
            "click_density_hz": float(click_density),
            "clipped_ratio": float(clipped_ratio),
            "sibilance_ratio_db": float(sibilance_ratio_db),
            "reverb_t60": float(parts["t60"]),
            "reverb_presence": float(parts["presence"]),
            "scores": scores,
            "suggestions": {name: float(value) for name, value in scores.items()},
        }
        everything = set(_SPECTRAL_COMPONENTS) | {"clicks", "clipped", "hum"}
        stems = self._components(arr, parts, everything)
        return stems, report

    def _needed(self) -> set[str]:
        """The components the requested repairs actually work on.

        A repair that is switched off (or whose score is zero) does nothing,
        so the component it would subtract is not measured.  This is what
        keeps the per block cost proportional to the work that is asked for.
        """
        scores = self._report.get("scores", {})
        needed: set[str] = set()
        for name, component in _REPAIRS:
            if float(self.params.get(name)) <= 0.0:
                continue
            if float(scores.get(name, 1.0)) > 0.0:
                needed.add(component)
        return needed

    def _analyse_stems(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Split ``x`` into the components the repairs work on.

        MODEL-INTEGRATION POINT — this method produces every component the
        module works with and is the only place where audio is inspected.  The
        repairs are built **only** on top of the returned dictionary.  A
        learned analyser can replace this method and return the same seven
        keys ("signal", "noise", "hum", "clicks", "clipped", "sibilance",
        "reverb") without touching the rest of the module.

        Parameters
        ----------
        x:
            ``(n_samples, n_channels)`` float64 audio.

        Returns
        -------
        dict
            The seven component signals, each shaped like ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        needed = self._needed()
        if not self._report:
            # The detection is a file level measurement: it runs once, on the
            # first block, and the result is kept for the whole recording.
            # Repeating it on every block was the reason the module needed
            # more than the block budget.
            stems, report = self._analyse(arr)
            self._report = report
            return stems
        parts = self._spectral_parts(arr) if needed & _SPECTRAL_COMPONENTS else None
        return self._components(arr, parts, needed)

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the requested repairs to ``x``."""
        self._apply_mode()
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if self._report and not self._needed():
            # No repair is asked for: the module is a pass-through and does
            # not analyse the block at all.
            return np.ascontiguousarray(arr, dtype=np.float64)
        stems = self._analyse_stems(arr)
        scores = self._report.get("scores", {})

        def _depth(name: str) -> float:
            return float(self.params.get(name)) * float(scores.get(name, 1.0))

        out = stems["signal"]

        # 1. de-clip — rebuild the samples that sat on the rails
        clip_depth = _depth("De-Clip Reduction")
        clip_mask = np.abs(stems["clipped"]).max(axis=1) > 0.0
        if clip_depth > 0.0 and np.any(clip_mask):
            repaired = _interpolate_runs(out.mean(axis=1), clip_mask, cubic=True)
            weight = clip_depth * clip_mask.astype(np.float64)
            out = out * (1.0 - weight[:, None]) + repaired[:, None] * weight[:, None]

        # 2. de-click — interpolate over impulsive outliers
        click_depth = _depth("Click Reduction")
        click_mask = np.abs(stems["clicks"]).max(axis=1) > 0.0
        if click_depth > 0.0 and np.any(click_mask):
            repaired = _interpolate_runs(out.mean(axis=1), click_mask, cubic=False)
            weight = click_depth * click_mask.astype(np.float64)
            out = out * (1.0 - weight[:, None]) + repaired[:, None] * weight[:, None]

        # 3. de-hum — subtract the estimated mains hum
        hum_depth = _depth("Hum Reduction")
        if hum_depth > 0.0:
            out = out - hum_depth * stems["hum"]

        # 4. noise reduction — subtract the estimated noise floor content
        noise_depth = _depth("Noise Reduction")
        if noise_depth > 0.0:
            out = out - noise_depth * stems["noise"]

        # 5. reverb reduction — subtract the estimated late field
        reverb_depth = _depth("Reverb Reduction")
        if reverb_depth > 0.0:
            out = out - reverb_depth * stems["reverb"]

        # 6. de-ess — subtract the estimated sibilant content
        ess_depth = _depth("De-Ess Reduction")
        if ess_depth > 0.0:
            out = out - ess_depth * stems["sibilance"]

        return np.ascontiguousarray(out, dtype=np.float64)

    # -- housekeeping ---------------------------------------------------------
    def _state_blob(self) -> bytes:
        """Serialise the mode the module was in.

        The mode travels with the state of the module, so that a session
        that was rendered offline says so when it is opened again in a real
        time host.  It is a reading, not a switch: the mode the module runs
        in is the mode the engine of the chain gives it.
        """
        buf = io.BytesIO()
        np.savez(buf, mode=np.asarray(self.processing_mode))
        return buf.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore the mode the module was in."""
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as archive:
                mode = archive["mode"] if "mode" in archive.files else np.zeros(0)
        except Exception:
            return
        if mode.size:
            self._state_mode = str(np.asarray(mode).ravel()[0])

    @property
    def state_mode(self) -> str:
        """The mode the module was in when the state was written.

        The mode travels with the state of the module, so that a session
        that was rendered offline says so when it is opened again in a real
        time host.  It is a reading, not a switch: the mode the module runs
        in is the mode the engine of the chain gives it.
        """
        return self._state_mode

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds.

        The analysis of this module is block-local: the frame is centred
        inside the block and the overlap-add divides by the window sum, so
        the module never delays the signal.  The offline mode buys a finer
        frequency axis, not a delay, so the latency is zero in both modes and
        it is declared for both of them.
        """
        return self.mode_latency(realtime=0, offline=0)

    def reset(self) -> None:
        """Forget the detection results."""
        self._report = {}
        self._hum_hz = 0.0

    def serialize(self) -> dict:
        """Store the detection results so the UI can show them after a reload.

        The mode of the render travels with the state as well, so that a
        session that was rendered offline says so when it is opened again in
        a real time host.
        """
        return {
            "report": {k: v for k, v in self._report.items() if k != "suggestions"},
            "hum_hz": float(self._hum_hz),
            "mode": str(self.processing_mode),
        }

    def deserialize(self, data: dict) -> None:
        """Restore the detection results written by :meth:`serialize`."""
        report = (data or {}).get("report")
        if isinstance(report, dict):
            self._report = dict(report)
        try:
            self._hum_hz = float((data or {}).get("hum_hz", 0.0))
        except (TypeError, ValueError):
            self._hum_hz = 0.0
        # The mode of the render travels with the state, so that a session
        # that was rendered offline says so when it is opened again.  It does
        # not decide the mode the module runs in: the engine of the chain
        # owns that.
        mode = (data or {}).get("mode")
        if isinstance(mode, str) and mode:
            self._state_mode = mode
