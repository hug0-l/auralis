"""
aurcore.modules.music_rebalance — Music Rebalance.

Splits a music mix into four stems — **vocal**, **bass**, **drums** and
**other** — and re-balances them with four gain controls plus four mute
switches.

How the split is made (an approximation, not a real separator)
----------------------------------------------------------------
This module implements a *pragmatic content-based decomposition*.  It is an
approximation of a stem separator, not one: it will not pull a vocal out of a
dense mix the way a trained model can, but it is deterministic, needs no
model files and behaves musically on typical material.

* **Bass** — everything below a 250 Hz crossover (with a narrow, cosine-shaped
  transition band up to 297 Hz).  The bass stem is a dedicated linear-phase FIR
  low-pass instead of a per-frame brick wall: a hard-edged STFT mask smears
  energy around the crossover and the stems stop summing coherently, which
  makes a bass gain change audible in the transition band.  With the FIR the
  bass stem is an exact filtered copy of the input, so a bass gain change
  cannot touch anything above the crossover.
* **Drums** — transient-detected broadband content.  A harmonic/percussive
  decomposition provides the per-bin "how transient is this" measure and a
  half-wave-rectified spectral flux provides the per-frame "is there a hit
  here" gate, so only the onset frames of a hit end up in the drum stem.
* **Vocal** — centre-panned, mid-band, harmonic content.  Centre-ness is
  measured per bin with the magnitude-squared coherence between the left and
  right channels (1.0 = perfectly centred, 0.0 = uncorrelated / wide).
* **Other** — everything that is left over.  The four masks always sum to one,
  so the stems always sum back to the input.

MODEL-INTEGRATION POINT
-----------------------
Every bit of separation happens inside :meth:`MusicRebalance._analyse_stems`,
which returns the separated sources as a dictionary of signals::

    {"vocal": (n, ch) ndarray, "bass": ..., "drums": ..., "other": ...}

The rest of the module — the mix, the gains, the mutes — is built **only** on
top of that dictionary.  A learned four-stem model can therefore replace
``_analyse_stems`` with a model that returns the same four keys and nothing
else in the module has to change.  ``ml = True`` tells the user interface that
a trained model could improve this module.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

_EPS = 1e-12

try:  # pragma: no cover - scipy is optional, the numpy path below is complete
    from scipy.ndimage import median_filter as _sp_median
except Exception:  # pragma: no cover
    _sp_median = None

#: Analysis constants per ``Quality`` setting.
_QUALITY = {
    "Good": {"n_fft": 1024, "hop": 256},
    "Better": {"n_fft": 2048, "hop": 512},
    "Best": {"n_fft": 4096, "hop": 1024},
}

#: Bass crossover.  Everything below ``BASS_LOW`` belongs to the bass stem,
#: everything above ``BASS_HIGH`` belongs to the other three.
BASS_LOW = 250.0
BASS_HIGH = 297.0  # 250 Hz * 2^(1/4) — a quarter-octave transition

#: Vocal band (cosine tapers at both ends).
VOCAL_LOW = 300.0
VOCAL_LOW_EDGE = 180.0
VOCAL_HIGH = 6000.0
VOCAL_HIGH_EDGE = 9000.0


# ---------------------------------------------------------------------------
# Small local DSP helpers (numpy only — the core never hard-depends on scipy).
# ---------------------------------------------------------------------------
#: The Hann window of every length the module uses.  The window is a
#: constant of the analysis, so it is built once and then read.
_HANN_CACHE: dict[int, np.ndarray] = {}

#: The frequency axis and the band weights of the analysis, by
#: ``(n_fft, sample_rate)``.  They depend on the quality setting and on
#: nothing else.
_ANALYSIS_CACHE: dict[tuple[int, int], dict[str, np.ndarray]] = {}

#: The kernel of the bass filter, by ``(low, high, sample_rate)``.
_FIR_CACHE: dict[tuple[float, float, float], tuple[np.ndarray, int]] = {}

#: The spectrum of that kernel, by ``(low, high, sample_rate, n_fft)``.
_FIR_SPECTRUM_CACHE: dict[tuple[float, float, float, int], np.ndarray] = {}

#: The sum of the squared windows of the overlap-add, by
#: ``(frames, n_fft, hop)``.
_ISTFT_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _hann(n: int) -> np.ndarray:
    """Periodic Hann window of length ``n`` (built once, then read)."""
    count = int(n)
    window = _HANN_CACHE.get(count)
    if window is None:
        window = 0.5 - 0.5 * np.cos(
            2.0 * np.pi * np.arange(count, dtype=np.float64) / float(count)
        )
        window.flags.writeable = False
        _HANN_CACHE[count] = window
    return window


def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Centred short-time Fourier transform.

    Returns a ``(frames, bins, channels)`` complex array.  The signal is
    padded by half a window on the left and by a full window on the right so
    that every sample of the original signal is covered by a complete set of
    overlapping frames.
    """
    n = int(x.shape[0])
    ch = int(x.shape[1]) if x.ndim > 1 else 1
    if n == 0:
        return np.zeros((0, n_fft // 2 + 1, ch), dtype=np.complex128)
    left = n_fft // 2
    mode = "reflect" if n > left else ("edge" if n > 1 else "constant")
    xp = np.pad(x, ((left, n_fft), (0, 0)), mode=mode)
    n_frames = 1 + (xp.shape[0] - n_fft) // hop
    win = np.lib.stride_tricks.sliding_window_view(xp, n_fft, axis=0)  # (L-n+1, ch, n)
    frames = win[::hop][:n_frames]                                     # (F, ch, n)
    frames = np.swapaxes(frames, 1, 2) * _hann(n_fft)[:, None]         # (F, n, ch)
    return np.fft.rfft(frames, axis=1)


def _istft(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    """Inverse of :func:`_stft` (weighted overlap-add, Hann analysis+synthesis)."""
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * _hann(n_fft)[:, None]  # (F, n, ch)
    n_frames = frames.shape[0]
    total = (n_frames - 1) * hop + n_fft
    out = np.zeros((total, spec.shape[2]), dtype=np.float64)
    wsum = _overlap_weight(n_frames, n_fft, hop)
    for i in range(n_frames):
        start = i * hop
        out[start:start + n_fft] += frames[i]
    out /= np.maximum(wsum, 1e-8)[:, None]
    left = n_fft // 2
    return out[left:left + length]


def _overlap_weight(n_frames: int, n_fft: int, hop: int) -> np.ndarray:
    """Return the sum of the squared windows of the overlap-add.

    It only depends on the geometry of the overlap-add, so it is built once
    per geometry and then read.
    """
    key = (int(n_frames), int(n_fft), int(hop))
    weight = _ISTFT_CACHE.get(key)
    if weight is None:
        total = (int(n_frames) - 1) * int(hop) + int(n_fft)
        weight = np.zeros(total, dtype=np.float64)
        w2 = _hann(n_fft) ** 2
        for i in range(int(n_frames)):
            start = i * int(hop)
            weight[start:start + int(n_fft)] += w2
        weight.flags.writeable = False
        _ISTFT_CACHE[key] = weight
    return weight


def _percentile_90(values: np.ndarray) -> float:
    """Return the 90th percentile of ``values``.

    It is the linear interpolation of NumPy's ``percentile``, computed
    without the overhead of the generic implementation: the array holds one
    value per analysis frame, so it is tiny and the call overhead of the
    generic routine was a measurable part of the cost of a block.
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    ordered = np.sort(arr)
    position = 0.9 * (ordered.size - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, ordered.size - 1)
    fraction = position - lower
    return float(ordered[lower] + fraction * (ordered[upper] - ordered[lower]))


def _median_filter(arr: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Median filter along ``axis`` with a centred odd-length kernel."""
    if size <= 1:
        return np.asarray(arr, dtype=np.float64).copy()
    if _sp_median is not None:
        # scipy names the argument ``axes``.  ``mode="nearest"`` repeats the
        # edge value, which is what the numpy path below pads with, so the
        # two paths return the same numbers.
        return np.asarray(
            _sp_median(arr, size=size, axes=axis, mode="nearest"), dtype=np.float64
        )
    half = size // 2
    if axis == 0:
        padded = np.pad(arr, ((half, half), (0, 0)), mode="edge")
        view = np.lib.stride_tricks.sliding_window_view(padded, size, axis=0)
    else:
        padded = np.pad(arr, ((0, 0), (half, half)), mode="edge")
        view = np.lib.stride_tricks.sliding_window_view(padded, size, axis=1)
    out = np.empty(arr.shape, dtype=np.float64)
    rows = max(1, int(2_000_000 // max(view.shape[-1] * max(arr.shape[1], 1), 1)))
    for i in range(0, arr.shape[0], rows):
        out[i:i + rows] = np.median(view[i:i + rows], axis=-1)
    return out


def _lowpass_weight(freqs: np.ndarray, low: float, high: float) -> np.ndarray:
    """1 below ``low``, cosine transition to 0 at ``high``."""
    w = np.zeros_like(freqs)
    w[freqs <= low] = 1.0
    band = (freqs > low) & (freqs < high)
    w[band] = 0.5 * (1.0 + np.cos(np.pi * (freqs[band] - low) / (high - low)))
    return w


def _fir_kernel(low: float, high: float, sample_rate: float) -> tuple[np.ndarray, int]:
    """Return the kernel of the bass filter and its half length.

    The kernel depends on the crossover and on the sample rate and on
    nothing else, so it is designed once and then read.  Designing it for
    every block was a measurable part of the cost of a block: the kernel is
    4087 taps long, and the window and the sinc function of it are built
    from scratch every time.
    """
    key = (float(low), float(high), float(sample_rate))
    cached = _FIR_CACHE.get(key)
    if cached is not None:
        return cached
    transition = max(float(high - low), 1.0)
    taps = int(np.ceil(4.0 * float(sample_rate) / transition))
    taps = max(33, min(taps, 8193))
    if taps % 2 == 0:
        taps += 1
    half = taps // 2

    k = np.arange(taps, dtype=np.float64) - half
    fc = float(low)
    kernel = (2.0 * fc / float(sample_rate)) * np.sinc(2.0 * fc / float(sample_rate) * k)
    window = np.blackman(taps)
    kernel *= window
    total = float(np.sum(kernel))
    if abs(total) > 1e-12:
        kernel /= total
    kernel.flags.writeable = False
    _FIR_CACHE[key] = (kernel, half)
    return kernel, half


def _fir_spectrum(low: float, high: float, sample_rate: float, nfft: int) -> np.ndarray:
    """Return the spectrum of the kernel of the bass filter."""
    key = (float(low), float(high), float(sample_rate), int(nfft))
    cached = _FIR_SPECTRUM_CACHE.get(key)
    if cached is None:
        kernel, _half = _fir_kernel(low, high, sample_rate)
        cached = np.fft.rfft(kernel, int(nfft))
        cached.flags.writeable = False
        _FIR_SPECTRUM_CACHE[key] = cached
    return cached


def _fir_lowpass(x: np.ndarray, low: float, high: float, sample_rate: float) -> np.ndarray:
    """Linear-phase low-pass with a cosine-like transition between ``low`` and ``high``.

    A windowed-sinc FIR is used instead of a per-frame brick wall: an STFT
    mask with a hard edge smears energy around the crossover and the stems
    stop summing coherently, which makes a bass gain change audible in the
    transition band.  The FIR keeps the bass stem an exact (delayed) filtered
    copy of the input, so only the intended band moves.

    The convolution is done with an FFT and is chunked so that the memory
    stays bounded for long recordings.
    """
    n = int(x.shape[0])
    if n == 0:
        return x.copy()
    kernel, half = _fir_kernel(low, high, sample_rate)
    taps = kernel.shape[0]

    chunk = 1 << 20
    if n + taps <= chunk:
        size = n + taps - 1
        nfft = 1 << int(np.ceil(np.log2(max(size, 2))))
        spectrum = np.fft.rfft(x, nfft, axis=0) * _fir_spectrum(
            low, high, sample_rate, nfft
        )[:, None]
        filtered = np.fft.irfft(spectrum, nfft, axis=0)
        return filtered[half:half + n]

    out = np.zeros_like(x)
    step = chunk - taps + 1
    for start in range(0, n, step):
        stop = min(n, start + step)
        block = x[start:stop]
        size = block.shape[0] + taps - 1
        nfft = 1 << int(np.ceil(np.log2(max(size, 2))))
        spectrum = np.fft.rfft(block, nfft, axis=0) * _fir_spectrum(
            low, high, sample_rate, nfft
        )[:, None]
        filtered = np.fft.irfft(spectrum, nfft, axis=0)
        take = min(stop - start, filtered.shape[0] - half)
        if take > 0:
            out[start:start + take] = filtered[half:half + take]
    return out


def _band_weight(freqs: np.ndarray, lo: float, hi: float, lo_edge: float,
                 hi_edge: float) -> np.ndarray:
    """Smooth band-pass weight: 1 inside ``[lo, hi]``, cosine tapers outside."""
    w = np.zeros_like(freqs)
    w[(freqs >= lo) & (freqs <= hi)] = 1.0
    if lo_edge < lo:
        up = (freqs > lo_edge) & (freqs < lo)
        w[up] = 0.5 * (1.0 - np.cos(np.pi * (freqs[up] - lo_edge) / (lo - lo_edge)))
    if hi_edge > hi:
        down = (freqs > hi) & (freqs < hi_edge)
        w[down] = 0.5 * (1.0 + np.cos(np.pi * (freqs[down] - hi) / (hi_edge - hi)))
    return w


def _db_gain(db: float) -> float:
    """Decibel value to linear gain."""
    return 10.0 ** (float(db) / 20.0)


def _analysis_grid(n_fft: int, sample_rate: int) -> dict[str, np.ndarray]:
    """Return the frequency axis and the band weights of the analysis.

    They depend on the size of the transform and on the sample rate and on
    nothing else, so they are built once per quality setting and then read.
    """
    key = (int(n_fft), int(sample_rate))
    grid = _ANALYSIS_CACHE.get(key)
    if grid is None:
        freqs = np.fft.rfftfreq(int(n_fft), 1.0 / float(sample_rate))
        grid = {
            "freqs": freqs,
            "bass": _lowpass_weight(freqs, BASS_LOW, BASS_HIGH)[None, :],
            "vocal": _band_weight(
                freqs, VOCAL_LOW, VOCAL_HIGH, VOCAL_LOW_EDGE, VOCAL_HIGH_EDGE
            ),
        }
        for value in grid.values():
            value.flags.writeable = False
        _ANALYSIS_CACHE[key] = grid
    return grid


# ---------------------------------------------------------------------------
# The module.
# ---------------------------------------------------------------------------
@register
class MusicRebalance(Module):
    """Music Rebalance — vocal / bass / drums / other stem balance."""

    code = "AUR_MUSICREBALANCE"
    display_name = "Music Rebalance"
    category = "Repair"
    edition = "standard"
    element = "MusicSourceSeparate"
    analysis = False
    supports_learn = False
    ml = True

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's parameters (mirrors ``aurcore/schema/params.tsv``)."""
        return [
            ParamDesc("Vocal Gain", "float", 0.0, -24.0, 4.8, "dB", group="Stems",
                      help="Level of the vocal stem.", curve="taper"),
            ParamDesc("Bass Gain", "float", 0.0, -24.0, 4.8, "dB", group="Stems",
                      help="Level of the bass stem (everything below 250 Hz).", curve="taper"),
            ParamDesc("Drums Gain", "float", 0.0, -24.0, 4.8, "dB", group="Stems",
                      help="Level of the drum stem (transient broadband content).", curve="taper"),
            ParamDesc("Other Gain", "float", 0.0, -24.0, 4.8, "dB", group="Stems",
                      help="Level of the remaining instruments.", curve="taper"),
            ParamDesc("Vocal Mute", "bool", False, 0.0, 1.0, "", group="Stems",
                      help="Silence the vocal stem."),
            ParamDesc("Bass Mute", "bool", False, 0.0, 1.0, "", group="Stems",
                      help="Silence the bass stem."),
            ParamDesc("Drums Mute", "bool", False, 0.0, 1.0, "", group="Stems",
                      help="Silence the drum stem."),
            ParamDesc("Other Mute", "bool", False, 0.0, 1.0, "", group="Stems",
                      help="Silence the remaining instruments."),
            ParamDesc("Quality", "enum", "Good", 0.0, 2.0, "", ("Good", "Better", "Best"),
                      group="Quality",
                      help="Mask resolution. Best uses a 4096-point transform."),
        ]

    # -- helpers --------------------------------------------------------------
    def _settings(self) -> dict:
        """Analysis settings for the current ``Quality`` setting."""
        return _QUALITY.get(str(self.params.get("Quality")), _QUALITY["Good"])

    # -- the model-integration seam ------------------------------------------
    def _analyse_stems(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Separate ``x`` into vocal, bass, drums and other.

        MODEL-INTEGRATION POINT — this method is the only place where source
        separation happens.  It returns a dictionary of separated signals
        (each shaped like ``x``) and *nothing else in the module* looks at the
        audio: the mix is assembled purely from this dictionary.  Replacing
        this method with a trained four-stem model that returns the same four
        keys ("vocal", "bass", "drums", "other") upgrades the module without
        touching the rest of the code.

        This is an approximation of a stem separator, not a real one.

        Parameters
        ----------
        x:
            ``(n_samples, n_channels)`` float64 audio.

        Returns
        -------
        dict
            ``{"vocal": ndarray, "bass": ndarray, "drums": ndarray, "other": ndarray}``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        n, ch = arr.shape
        if n == 0:
            zero = np.zeros((0, ch), dtype=np.float64)
            return {"vocal": zero, "bass": zero.copy(), "drums": zero.copy(), "other": zero.copy()}

        cfg = self._settings()
        n_fft = int(cfg["n_fft"])
        hop = int(cfg["hop"])

        spec = _stft(arr, n_fft, hop)                 # (F, B, C)
        mag = np.abs(spec)
        mid = mag.mean(axis=2)                        # (F, B)
        grid = _analysis_grid(n_fft, self.sample_rate)

        # --- harmonic / percussive split ------------------------------------
        harm_time = _median_filter(mid, 11, axis=0)
        perc_freq = _median_filter(mid, 11, axis=1)
        hp = (harm_time ** 2) / (harm_time ** 2 + perc_freq ** 2 + _EPS)
        harmonicity = np.clip((hp - 0.5) / 0.5, 0.0, 1.0)
        percussivity = np.clip(((1.0 - hp) - 0.5) / 0.5, 0.0, 1.0)

        # --- transient gate (half-wave rectified spectral flux) --------------
        if mag.shape[0] >= 2:
            flux = np.maximum(mid[1:] - mid[:-1], 0.0).sum(axis=1)
            flux = np.concatenate([flux[:1], flux])
        else:
            flux = np.zeros(mag.shape[0])
        ref = _percentile_90(flux)
        gate = np.clip(flux / max(ref, _EPS), 0.0, 1.0) if ref > 0 else np.zeros_like(flux)

        # --- centre-ness (magnitude squared coherence between the channels) --
        if ch >= 2:
            left = spec[:, :, 0]
            right = spec[:, :, 1]
            num = np.abs(left * np.conj(right)) ** 2
            den = (np.abs(left) ** 2) * (np.abs(right) ** 2) + _EPS
            centre = np.clip(num / den, 0.0, 1.0)
        else:
            centre = np.ones_like(mid)

        # --- assemble the four soft masks -------------------------------------
        # The bass stem is a dedicated linear-phase low-pass (see
        # :func:`_fir_lowpass`); the remaining three stems share the spectral
        # masks below.
        bass = _fir_lowpass(arr, BASS_LOW, BASS_HIGH, float(self.sample_rate))

        m_bass = grid["bass"]
        remainder = 1.0 - m_bass

        m_drums = percussivity * gate[:, None] * remainder

        vocal_band = grid["vocal"]
        m_vocal = harmonicity * centre * vocal_band[None, :] * remainder * (1.0 - m_drums)

        m_other = np.clip(1.0 - m_bass - m_drums - m_vocal, 0.0, 1.0)

        drums = _istft(spec * m_drums[:, :, None], n_fft, hop, n)
        vocal = _istft(spec * m_vocal[:, :, None], n_fft, hop, n)
        other = arr - bass - drums - vocal
        return {"vocal": vocal, "bass": bass, "drums": drums, "other": other}

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Mix the separated stems with the four gain controls and mutes."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        stems = self._analyse_stems(arr)

        def _level(gain_db: float, mute: bool) -> float:
            return 0.0 if mute else _db_gain(gain_db)

        out = (
            stems["vocal"] * _level(self.params.get("Vocal Gain"), self.params.get("Vocal Mute"))
            + stems["bass"] * _level(self.params.get("Bass Gain"), self.params.get("Bass Mute"))
            + stems["drums"] * _level(self.params.get("Drums Gain"), self.params.get("Drums Mute"))
            + stems["other"] * _level(self.params.get("Other Gain"), self.params.get("Other Mute"))
        )
        return np.ascontiguousarray(out, dtype=np.float64)

    # -- housekeeping ---------------------------------------------------------
    def reset(self) -> None:
        """Music Rebalance keeps no cross-block state."""
        return None

    def serialize(self) -> dict:
        """No learned state — the module is fully described by its parameters."""
        return {}

    def deserialize(self, data: dict) -> None:
        """Nothing to restore."""
        return None
