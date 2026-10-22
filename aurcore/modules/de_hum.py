"""
aurcore.modules.de_hum — De-Hum (``HumRemoval``).

Removes mains hum and its harmonic series from a recording.

Two modes are offered:

``Static``
    A bank of narrow notch filters parked on the hum fundamental and on its
    harmonics.  ``Filter Q`` sets the *absolute* bandwidth of the fundamental
    notch; every harmonic notch shares that absolute bandwidth, so the bank
    never wanders far above the hum and leaves programme material alone.

``Dynamic``
    A bank of narrow adaptive filters.  The fundamental is located by a
    spectral-peak search, tracked frame by frame and followed together with
    its harmonics, so a slowly drifting hum is tracked rather than parked.

``Enable Learning`` analyses a captured excerpt, finds the hum fundamental in
the 20-500 Hz band (spectral peak search with parabolic interpolation, refined
by direct evaluation of the windowed transform) and seeds the filter bank with
it.  With ``Adaptive`` enabled the fundamental keeps being re-estimated while
the audio runs, so the notches follow a drifting hum.

``Output Hum Only`` renders the estimated hum component on its own.  The
estimate is made in the spectral domain and is not the output of the filter
chain, so the module adds no latency in that mode.

``Removed Mix`` puts the removed hum back into the output at the amount the
user sets: 0 % leaves the cleaned signal as it is, 100 % plays the removed
hum alone.  The module measures what it removed and keeps the reading, so an
interface can show a meter.

``Linear Phase Filters`` replaces the biquad bank with a linear-phase FIR: a
symmetric kernel of 4801 taps, designed from the same notches, applied
through the shared streaming filter of ``aurcore.dsp.filters`` so that the
answer does not depend on the size of the blocks the host uses.  The filter
delays the signal by half its length (2400 samples) and the module reports
that delay as its latency.  The high-pass, the low-pass and the DC blocker
are part of the kernel in that mode, so that every stage of the chain
carries the same delay and nothing answers before it.

The linear-phase path has a fast way to run: a weighted overlap-add filter
bank (``aurcore.dsp.wola``).  The bank applies a symmetric window of 4096
points, the frames half a window apart, and the state is carried between
the blocks.  It applies the same kernel as the long path, so the two answer
the same thing to the rounding of the transform (measured: 3.3e-16), but
the transform is paid once per half window instead of once per block: a 64
sample block with two channels costs 59 us against the 483 us of the direct
convolution (the realtime budget of that block is 1333 us).  The price is
the delay of the bank: the frames overlap, so the bank answers a window
after the input, and the module declares that delay (4096 + 2400 = 6496
samples) while it runs the fast path.  The long path stays the reference
the bank is measured against, and it is used when the kernel is shorter
than the window: then it is cheap and exact, and the bank would only add
its own delay.

The notch biquads and the spectrogram helpers are implemented in this file:
the shared ``aurcore.dsp`` package is used when it is importable *and*
self-consistent, and a local implementation is used otherwise.
"""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from ..dsp.filters import FIR, linear_phase_fir
from ..dsp.wola import WolaBank
from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..params import ParamDesc
from ..registry import register
from .base import Module

try:  # pragma: no cover - scipy is optional, the numpy path below is complete
    from scipy.signal import lfilter as _sp_lfilter
except Exception:  # pragma: no cover
    _sp_lfilter = None

try:  # pragma: no cover - the shared DSP package is optional
    from ..dsp.stft import istft as _sp_istft, stft as _sp_stft
except Exception:  # pragma: no cover
    _sp_stft = _sp_istft = None

__all__ = ["DeHum"]

_EPS = 1e-12

#: Depth of a single harmonic notch, in decibels.
NOTCH_DEPTH_DB = 60.0

#: Highest fraction of the sample rate a notch may be placed at.
_MAX_NOTCH_RATIO = 0.45

#: Deepest attenuation a dynamic band may apply, in decibels.
_MAX_DYNAMIC_REDUCTION_DB = 24.0

#: Analysis window used by the fundamental estimator, in samples.
_ESTIMATE_NFFT = 4096

#: Analysis window used by the learner, in samples.
_LEARN_NFFT = 16384

#: Block size of the adaptive static path, in samples.
_SEGMENT = 2048

#: How far the hum at a harmonic has to stand above the floor beside it
#: before the notch of that harmonic is applied.  The factor is a power, so
#: 6.0 means about sixteen decibels.
_HUM_GATE = 6.0

#: The factor at which a notch that is open closes again.  It is below the
#: gate, so that a level that sits on the threshold does not make the notch
#: open and close.
_HUM_GATE_CLOSE = 3.0

#: The attack and the release of the opening of a notch, in seconds.  The
#: opening follows the measurement quickly, the closing slowly: a notch that
#: closes at once would let the programme back in and then take it away
#: again.
_OPEN_ATTACK_S = 0.050
_OPEN_RELEASE_S = 0.500

#: The width of the window the floor of the hum is measured in, in bins of
#: the analysis, and the half width of the gap around the harmonic that is
#: left out of it.
_HUM_FLOOR_BINS = 24
_HUM_GAP_BINS = 3

#: The length of the analysis window of the weighted overlap-add bank, in
#: samples.  The frames of the bank are half a window apart (50 % overlap).
_WOLA_WINDOW = 4096


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _next_pow2(n: int) -> int:
    """Return the smallest power of two greater than or equal to ``n``."""
    v = 1
    while v < int(n):
        v <<= 1
    return v


def _db_to_lin(db) -> np.ndarray:
    """Convert decibels to a linear amplitude."""
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 20.0)


# ---------------------------------------------------------------------------
# biquad primitives (RBJ audio EQ cookbook)
# ---------------------------------------------------------------------------
def _median(values: np.ndarray) -> float:
    """The median of a small array, without sorting it.

    ``numpy.partition`` finds the two middle values without the sort, and
    the two agree bit for bit (the median of an even number of values is the
    mean of the two middle ones).  The measurement of the hum asks for the
    median of a few dozen values several times per segment, and the sort is
    the expensive part of it.
    """
    arr = np.asarray(values, dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        part = np.partition(arr, n // 2)
        return float(part[n // 2])
    part = np.partition(arr, [n // 2 - 1, n // 2])
    return 0.5 * (float(part[n // 2 - 1]) + float(part[n // 2]))


def _notch_biquad(fc: float, q: float, fs: float):
    """Design an RBJ notch (band-stop) biquad.

    The -3 dB bandwidth of the result is ``fc / q``.  A notch is used rather
    than a deep peaking bell because a bell of -60 dB has a very wide skirt,
    while the notch returns to unity within a fraction of its bandwidth.
    """
    w0 = 2.0 * math.pi * float(fc) / float(fs)
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(float(q), 1e-3))
    a0 = 1.0 + alpha
    return (
        1.0 / a0,
        (-2.0 * cos_w0) / a0,
        1.0 / a0,
        (-2.0 * cos_w0) / a0,
        (1.0 - alpha) / a0,
    )


def _notch_depth_mix(depth_db: float) -> float:
    """Return the mix factor that turns a full notch into ``depth_db`` deep.

    The notch path is mixed with the dry signal as ``(1 - g) * dry + g * notch``
    so the stop band attenuation is exactly ``depth_db`` decibels.
    """
    g = 1.0 - 10.0 ** (float(depth_db) / 20.0)
    return float(min(max(g, 0.0), 1.0))


def _lowpass_biquad(fc: float, q: float, fs: float):
    """Design an RBJ low-pass biquad."""
    w0 = 2.0 * math.pi * float(fc) / float(fs)
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(float(q), 1e-3))
    a0 = 1.0 + alpha
    return (
        ((1.0 - cos_w0) / 2.0) / a0,
        (1.0 - cos_w0) / a0,
        ((1.0 - cos_w0) / 2.0) / a0,
        (-2.0 * cos_w0) / a0,
        (1.0 - alpha) / a0,
    )


def _highpass_biquad(fc: float, q: float, fs: float):
    """Design an RBJ high-pass biquad."""
    w0 = 2.0 * math.pi * float(fc) / float(fs)
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(float(q), 1e-3))
    a0 = 1.0 + alpha
    return (
        ((1.0 + cos_w0) / 2.0) / a0,
        (-(1.0 + cos_w0)) / a0,
        ((1.0 + cos_w0) / 2.0) / a0,
        (-2.0 * cos_w0) / a0,
        (1.0 - alpha) / a0,
    )


def _biquad_stream(b, a, x: np.ndarray, zi: np.ndarray) -> np.ndarray:
    """Filter a block with a biquad, carrying the delay state in ``zi``.

    ``zi`` has shape ``(2, channels)`` and is updated in place.
    """
    if _sp_lfilter is not None:
        y, zf = _sp_lfilter(b, a, x, axis=0, zi=zi)
        zi[:, :] = zf
        return y
    # pure numpy fallback: direct form II transposed
    b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
    a1, a2 = float(a[0]), float(a[1])
    n, ch = x.shape
    y = np.empty_like(x)
    for c in range(ch):
        s0 = float(zi[0, c])
        s1 = float(zi[1, c])
        col = x[:, c]
        out = np.empty(n, dtype=np.float64)
        for i in range(n):
            xi = float(col[i])
            yi = b0 * xi + s0
            s0 = b1 * xi - a1 * yi + s1
            s1 = b2 * xi - a2 * yi
            out[i] = yi
        y[:, c] = out
        zi[0, c] = s0
        zi[1, c] = s1
    return y


def _filter_response(freqs: np.ndarray, coeffs, fs: float) -> np.ndarray:
    """Magnitude response of a biquad at the given frequencies."""
    b0, b1, b2, a1, a2 = coeffs
    w = 2.0 * math.pi * np.asarray(freqs, dtype=np.float64) / float(fs)
    num = b0 + b1 * np.exp(-1j * w) + b2 * np.exp(-2j * w)
    den = 1.0 + a1 * np.exp(-1j * w) + a2 * np.exp(-2j * w)
    return np.abs(num) / np.maximum(np.abs(den), _EPS)


def _kernel_delay(kernel: np.ndarray) -> int:
    """The group delay of a kernel at DC, in samples.

    For a symmetric kernel that is half its length, which is the
    constant group delay of a linear-phase filter.
    """
    total = float(np.sum(kernel))
    if total == 0.0:
        return 0
    moment = float(np.sum(np.arange(kernel.size, dtype=np.float64) * kernel))
    return int(round(moment / total))


def _fft_convolve_same(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Convolve each channel of ``x`` with ``h`` and keep the original length."""
    n = x.shape[0]
    length = len(h)
    if n == 0:
        return x.copy()
    if length <= 1:
        return x * h[0]
    size = _next_pow2(n + length - 1)
    spec = np.fft.rfft(h, size)
    out = np.empty_like(x)
    start = (length - 1) // 2
    for c in range(x.shape[1]):
        y = np.fft.irfft(np.fft.rfft(x[:, c], size) * spec, size)
        out[:, c] = y[start:start + n]
    return out


# ---------------------------------------------------------------------------
# spectrogram helpers
# ---------------------------------------------------------------------------
def _shared_stft_usable() -> bool:
    """Return True when the shared DSP package can do a round trip."""
    if _sp_stft is None or _sp_istft is None:
        return False
    try:
        probe = np.zeros(1024, dtype=np.float64)
        probe[16] = 1.0
        spec = _sp_stft(probe, n_fft=256, hop=64, window="hann", center=True)
        back = _sp_istft(spec, hop=64, window="hann", length=1024)
        return back.shape[0] == 1024 and bool(np.all(np.isfinite(back)))
    except Exception:
        return False


_USE_SHARED_STFT = _shared_stft_usable()


def _spectrogram(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Return a complex spectrogram shaped ``(frames, bins, channels)``."""
    if _USE_SHARED_STFT:  # pragma: no cover - depends on the shared package
        try:
            spec = _sp_stft(x, n_fft=n_fft, hop=hop, window="hann", center=True)
            if spec.ndim == 2:
                return np.transpose(spec, (1, 0))[:, :, None]
            return np.transpose(spec, (2, 1, 0))
        except Exception:
            pass
    win = np.hanning(n_fft)
    n, ch = x.shape
    pad = n_fft // 2
    xp = np.concatenate(
        [np.zeros((pad, ch), dtype=np.float64), x, np.zeros((n_fft, ch), dtype=np.float64)],
        axis=0,
    )
    n_frames = 1 + max(0, len(xp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = xp[idx] * win[:, None]
    return np.fft.rfft(frames, axis=1)


def _resynthesize(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    """Inverse of :func:`_spectrogram` (overlap-add, exact normalisation)."""
    if _USE_SHARED_STFT:  # pragma: no cover - depends on the shared package
        try:
            # The shared inverse transform reads (bins, frames); the
            # spectrogram of this module is (frames, bins, channels).
            if spec.shape[2] == 1:
                shaped = np.transpose(spec[:, :, 0])
            else:
                shaped = np.transpose(spec, (2, 1, 0))
            out = _sp_istft(shaped, hop=hop, window="hann", length=length)
            if out.ndim == 1:
                out = out[:, None]
            return np.asarray(out, dtype=np.float64)[:length]
        except Exception:
            pass
    win = np.hanning(n_fft)
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * win[:, None]
    n_frames = frames.shape[0]
    total = (n_frames - 1) * hop + n_fft
    out = np.zeros((total, spec.shape[2]), dtype=np.float64)
    wsum = np.zeros(total, dtype=np.float64)
    w2 = win * win
    for i in range(n_frames):
        out[i * hop:i * hop + n_fft] += frames[i]
        wsum[i * hop:i * hop + n_fft] += w2
    out /= np.maximum(wsum, 1e-8)[:, None]
    start = n_fft // 2
    out = out[start:start + length]
    if out.shape[0] < length:
        out = np.concatenate(
            [out, np.zeros((length - out.shape[0], out.shape[1]), dtype=np.float64)], axis=0
        )
    return out


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------
@register
class DeHum(Module):
    """De-Hum — mains hum and harmonic removal."""

    code = "AUR_DEHUM"
    display_name = "De-Hum"
    category = "Repair"
    edition = "standard"
    element = "HumRemoval"
    analysis = True
    supports_learn = True
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._states: list[np.ndarray] = []
        self._hp_state: np.ndarray | None = None
        self._lp_state: np.ndarray | None = None
        self._dc_state: np.ndarray | None = None
        self._history = np.zeros((0, channels), dtype=np.float64)
        self._tracked_freq = 0.0
        self._learned_freq = 0.0
        self._learned_phase = 0.0
        self._learned_mag = 0.0
        self._has_learned = False
        self._dyn_freq = 0.0
        self._dyn_gain = np.zeros(0, dtype=np.float64)
        self._fir: np.ndarray | None = None
        self._fir_key: tuple | None = None
        self._fir_filter: FIR | None = None
        self._fir_source: np.ndarray | None = None
        self._wola_source: np.ndarray | None = None
        self._wola: WolaBank | None = None
        # how open the notch of every harmonic is at the moment
        self._open_state: dict[int, float] = {}
        # the samples that have gone into the decision that is pending
        self._segment_fill = 0
        # the difference tap: what the module took away
        self._tap = RemovedTap(max(int(channels), 1))
        #: The level of the removed hum of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed hum of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Mode",
                type="enum",
                default="Static",
                minimum=0.0,
                maximum=1.0,
                choices=("Static", "Dynamic"),
                group="Algorithm",
                help="Static notch bank, or an adaptive bank that follows a drifting hum.",
            ),
            ParamDesc(
                name="Base Frequency",
                type="float",
                default=50.0,
                minimum=20.0,
                maximum=500.0,
                unit="Hz",
                group="Static",
                curve="log",
                help="Fundamental frequency of the hum.",
            ),
            ParamDesc(
                name="Filter Q",
                type="float",
                default=1.0,
                minimum=0.1,
                maximum=30.0,
                group="Static",
                curve="log",
                help="Width of the notch, relative to the fundamental.",
            ),
            ParamDesc(
                name="Number of Harmonics",
                type="int",
                default=8,
                minimum=0,
                maximum=16,
                group="Static",
                help="How many harmonics above the fundamental are notched.",
            ),
            ParamDesc(
                name="Link Harmonics",
                type="enum",
                default="All",
                minimum=0.0,
                maximum=2.0,
                choices=("All", "Odd/Even", "None"),
                group="Static",
                help=(
                    "Which harmonics take part: All, odd harmonics only, or the "
                    "fundamental alone."
                ),
            ),
            ParamDesc(
                name="Harmonic Slope",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=12.0,
                unit="dB",
                group="Static",
                help="Gain tilt applied to each successive harmonic.",
            ),
            ParamDesc(
                name="Enable Learning",
                type="bool",
                default=False,
                group="Learning",
                help="Analyse an excerpt and use the hum frequency found in it.",
            ),
            ParamDesc(
                name="Adaptive",
                type="bool",
                default=True,
                group="Learning",
                help="Keep re-estimating the hum frequency while the audio runs.",
            ),
            ParamDesc(
                name="Enable Highpass",
                type="bool",
                default=False,
                group="Filters",
                help="Remove everything below the high-pass frequency.",
            ),
            ParamDesc(
                name="Highpass Frequency",
                type="float",
                default=20.0,
                minimum=20.0,
                maximum=500.0,
                unit="Hz",
                group="Filters",
                curve="log",
                help="Corner frequency of the optional high-pass.",
            ),
            ParamDesc(
                name="Enable Lowpass",
                type="bool",
                default=False,
                group="Filters",
                help="Remove everything above the low-pass frequency.",
            ),
            ParamDesc(
                name="Lowpass Frequency",
                type="float",
                default=20000.0,
                minimum=500.0,
                maximum=22050.0,
                unit="Hz",
                group="Filters",
                curve="log",
                help="Corner frequency of the optional low-pass.",
            ),
            ParamDesc(
                name="Linear Phase Filters",
                type="bool",
                default=False,
                group="Filters",
                help="Use a linear-phase FIR instead of the IIR biquad bank.",
            ),
            ParamDesc(
                name="Enable DC Offset",
                type="bool",
                default=False,
                group="Filters",
                help="Remove a constant DC offset from the signal.",
            ),
            ParamDesc(
                name="Dynamic Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Dynamic",
                help="How aggressively the adaptive bank suppresses the hum.",
            ),
            ParamDesc(
                name="Dynamic Bands",
                type="int",
                default=3,
                minimum=1,
                maximum=12,
                group="Dynamic",
                help="Number of adaptive filters (fundamental plus harmonics).",
            ),
            ParamDesc(
                name="Dynamic Low Cutoff",
                type="float",
                default=20.0,
                minimum=20.0,
                maximum=500.0,
                unit="Hz",
                group="Dynamic",
                curve="log",
                help="Lowest frequency the adaptive bank looks at.",
            ),
            ParamDesc(
                name="Dynamic High Cutoff",
                type="float",
                default=20000.0,
                minimum=500.0,
                maximum=22050.0,
                unit="Hz",
                group="Dynamic",
                curve="log",
                help="Highest frequency the adaptive bank looks at.",
            ),
            ParamDesc(
                name="Output Hum Only",
                type="bool",
                default=False,
                group="Output",
                help="Render the estimated hum component on its own.",
            ),
            ParamDesc(**removed_mix_fields("hum")),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the filter state, the trackers and the learned profile."""
        self._states = []
        self._hp_state = None
        self._lp_state = None
        self._dc_state = None
        self._history = np.zeros((0, self.channels), dtype=np.float64)
        self._tracked_freq = 0.0
        self._dyn_freq = 0.0
        self._dyn_gain = np.zeros(0, dtype=np.float64)
        self._fir = None
        self._fir_key = None
        self._fir_filter = None
        self._fir_source = None
        self._wola_source = None
        self._wola = None
        self._open_state = {}
        self._segment_fill = 0
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB

    def _state_blob(self) -> bytes:
        """Pack the learned profile into a JSON blob."""
        payload = {
            "learned_freq": self._learned_freq,
            "learned_phase": self._learned_phase,
            "learned_mag": self._learned_mag,
            "has_learned": self._has_learned,
            "tracked_freq": self._tracked_freq,
        }
        return json.dumps(payload).encode("utf-8")

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a profile packed by :meth:`_state_blob`."""
        try:
            payload = json.loads(bytes(data).decode("utf-8"))
        except Exception:
            return
        self._learned_freq = float(payload.get("learned_freq", 0.0))
        self._learned_phase = float(payload.get("learned_phase", 0.0))
        self._learned_mag = float(payload.get("learned_mag", 0.0))
        self._has_learned = bool(payload.get("has_learned", False))
        self._tracked_freq = float(payload.get("tracked_freq", 0.0))

    # -- frequency estimation ------------------------------------------------
    def _mono(self, x: np.ndarray) -> np.ndarray:
        """Return the mono mix of a block."""
        if x.shape[1] == 1:
            return x[:, 0]
        return x.mean(axis=1)

    def _find_peak(self, mono: np.ndarray, f_lo: float, f_hi: float, n_fft: int):
        """Locate the strongest spectral peak between ``f_lo`` and ``f_hi``.

        The coarse peak comes from a windowed FFT; the position is then
        refined by evaluating the windowed transform directly on a fine grid
        around the peak, which removes the bias a plain parabolic fit leaves
        on a windowed main lobe.  Returns ``(frequency, magnitude, phase)``.
        """
        fs = float(self.sample_rate)
        n = len(mono)
        if n < 8:
            return None
        if n < n_fft:
            seg = np.zeros(n_fft, dtype=np.float64)
            seg[n_fft - n:] = mono
        else:
            seg = mono[-n_fft:].astype(np.float64, copy=True)
        win = np.hanning(n_fft)
        windowed = seg * win
        spec = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / fs)
        lo = int(np.searchsorted(freqs, max(f_lo, 0.0)))
        hi = int(np.searchsorted(freqs, min(f_hi, fs * 0.5)))
        if hi <= lo:
            return None
        k = lo + int(np.argmax(spec[lo:hi]))
        bin_hz = fs / n_fft
        offsets = np.linspace(-0.6, 0.6, 13)
        cands = (k + offsets) * bin_hz
        idx = np.arange(n_fft, dtype=np.float64)
        values = np.empty(len(cands), dtype=np.float64)
        phases = np.empty(len(cands), dtype=np.float64)
        for i, f in enumerate(cands):
            z = np.dot(windowed, np.exp(-2j * np.pi * f * idx / fs))
            values[i] = abs(z)
            phases[i] = np.angle(z)
        j = int(np.argmax(values))
        freq = float(cands[j])
        if 0 < j < len(cands) - 1:
            # parabolic refinement on the log magnitude of the three best
            # candidates: the windowed main lobe is smooth, so the vertex of
            # the parabola lands on the true peak.
            lm = np.log(np.maximum(values[j - 1:j + 2], 1e-300))
            denom = lm[0] - 2.0 * lm[1] + lm[2]
            if abs(denom) > 1e-12:
                delta = 0.5 * (lm[0] - lm[2]) / denom
                delta = max(-1.0, min(1.0, float(delta)))
                freq = float(cands[j] + delta * (cands[1] - cands[0]))
        z = np.dot(windowed, np.exp(-2j * np.pi * freq * idx / fs))
        return float(freq), float(abs(z)), float(np.angle(z))

    def _fundamental(self) -> float:
        """Return the hum frequency the static path should work on."""
        if self.params.get("Enable Learning") and self._has_learned:
            return self._learned_freq
        return float(self.params.get("Base Frequency"))

    def _update_frequency(self, block: np.ndarray) -> float:
        """Track the hum frequency over time and return the current estimate."""
        current = self._fundamental()
        if self._tracked_freq <= 0.0:
            self._tracked_freq = current
        else:
            current = self._tracked_freq
        self._remember(block)
        if not self.params.get("Adaptive"):
            return current
        mono = self._mono(block)
        if len(mono) < 64:
            return current
        # the analysis runs on the recent history so the estimate is stable
        analysis = self._mono(self._history)
        tol = max(current * 0.08, 2.0)
        found = self._find_peak(analysis, current - tol, current + tol, _ESTIMATE_NFFT)
        if found is None:
            return current
        freq = found[0]
        self._tracked_freq = current + 0.35 * (freq - current)
        if not np.isfinite(self._tracked_freq):
            self._tracked_freq = current
        return self._tracked_freq

    # -- filter design -------------------------------------------------------
    def _harmonic_indices(self) -> list[int]:
        """Return the harmonic numbers that take part in the notch bank."""
        count = int(self.params.get("Number of Harmonics"))
        link = str(self.params.get("Link Harmonics"))
        if link == "None":
            return [1]
        if link == "Odd/Even":
            return [k for k in range(1, count + 2) if k % 2 == 1]
        return list(range(1, count + 2))

    def _notch_specs(self, f0: float) -> list[tuple[int, float, float, float]]:
        """Return ``(harmonic, frequency, depth_db, q)`` for every notch."""
        fs = float(self.sample_rate)
        q = max(float(self.params.get("Filter Q")), 1e-3)
        slope = float(self.params.get("Harmonic Slope"))
        bandwidth = max(f0 / q, 0.5)
        specs: list[tuple[int, float, float, float]] = []
        for k in self._harmonic_indices():
            fk = f0 * k
            if fk >= _MAX_NOTCH_RATIO * fs:
                break
            depth = -NOTCH_DEPTH_DB + slope * (k - 1)
            depth = max(-90.0, min(0.0, depth))
            if depth > -0.5:
                continue
            specs.append((k, fk, depth, fk / bandwidth))
        return specs

    # -- the estimator of the hum --------------------------------------------
    def _remember(self, block: np.ndarray) -> None:
        """Keep the recent history, which the measurement of the hum reads."""
        if self._history.shape[1] != block.shape[1]:
            self._history = np.zeros((0, block.shape[1]), dtype=np.float64)
        self._history = np.concatenate([self._history, block], axis=0)[-_ESTIMATE_NFFT:]

    def _measure_hum(self, f0: float) -> dict[int, tuple[float, float]]:
        """The level of the hum at every harmonic, and the floor beside it.

        The measurement is made on the recent history: the level at the
        harmonic and the level of the neighbourhood the harmonic stands out
        of.  A harmonic that does not stand out of its neighbourhood carries
        no hum, and the module does not notch it — the programme that lives
        in that band is left alone.

        The floor is the median of the bins around the harmonic, the
        harmonic's own bins left out.  The neighbourhood is narrow on
        purpose: the skirt of a loud tone falls away with the distance, and
        a wide floor would swallow the hum that sits a few bins away from a
        loud programme tone.
        """
        if self._history.shape[0] < 64:
            return {}
        mono = self._mono(self._history)
        if mono.size < 64:
            return {}
        seg = mono[-_ESTIMATE_NFFT:]
        if seg.size < 64:
            return {}
        windowed = seg * np.hanning(seg.size)
        spec = np.abs(np.fft.rfft(windowed))
        bin_hz = float(self.sample_rate) / float(seg.size)
        levels: dict[int, tuple[float, float]] = {}
        for k, fk, _depth, _q in self._notch_specs(f0):
            centre = int(round(fk / bin_hz))
            if centre < 2 or centre >= spec.size - 2:
                continue
            level = float(spec[centre])
            floor_idx = list(range(max(0, centre - _HUM_FLOOR_BINS),
                                   max(0, centre - _HUM_GAP_BINS)))
            floor_idx += list(range(min(spec.size, centre + _HUM_GAP_BINS + 1),
                                    min(spec.size, centre + _HUM_FLOOR_BINS + 1)))
            if not floor_idx:
                continue
            floor = _median(spec[floor_idx])
            levels[k] = (level, floor)
        return levels

    def _openness(self, f0: float, samples: int) -> dict[int, float]:
        """How open the notch of every harmonic is, from zero to one.

        The masking law: a harmonic is notched when the hum at it stands
        clearly above the floor beside it, and it is left alone when it does
        not.  A harmonic is also only notched when the fundamental carries a
        hum: a harmonic without a fundamental is a wanted partial that
        happens to sit on a multiple of the mains frequency, and the module
        leaves it alone.  The decision has a hysteresis, so a level that
        sits on the threshold does not make the notch open and close.

        The gain application: the opening of a notch follows the measurement
        with an attack and a release, so that the depth of the bank moves
        smoothly and the filter does not step.  The two time constants are
        measured in seconds, and the decision is made on a fixed grid of
        ``_SEGMENT`` samples, so that the answer of the module does not
        depend on the size of the blocks the host uses.
        """
        fs = float(self.sample_rate)
        self._segment_fill += max(1, int(samples))
        if self._segment_fill < _SEGMENT and self._open_state:
            return self._open_state
        step = self._segment_fill
        self._segment_fill = 0
        attack = 1.0 - math.exp(-step / max(1e-6, _OPEN_ATTACK_S * fs))
        release = 1.0 - math.exp(-step / max(1e-6, _OPEN_RELEASE_S * fs))
        levels = self._measure_hum(f0)
        out: dict[int, float] = {}
        fundamental = 0.0
        for k, (level, floor) in levels.items():
            if floor <= 0.0:
                target = 0.0
            elif level > floor * _HUM_GATE:
                target = 1.0
            elif level < floor * _HUM_GATE_CLOSE:
                target = 0.0
            else:
                # between the two thresholds the decision is held
                target = self._open_state.get(k, 0.0)
            if k > 1 and fundamental <= 0.0:
                # a harmonic without a fundamental is not a harmonic of the
                # hum, it is programme that sits on a multiple of the mains
                # frequency
                target = 0.0
            previous = self._open_state.get(k, target)
            alpha = attack if target > previous else release
            value = previous + alpha * (target - previous)
            value = min(1.0, max(0.0, value))
            self._open_state[k] = value
            out[k] = value
            if k == 1:
                fundamental = value
        return out

    def _bank(self, f0: float) -> list[tuple[int, float, float, float]]:
        """The notch bank with the depth the measurement gives.

        The shape of a notch (its frequency and its width) is the user's
        setting; its depth is the measurement's: a harmonic that carries no
        hum is not notched at all.
        """
        out: list[tuple[int, float, float, float]] = []
        for k, fk, depth, q in self._notch_specs(f0):
            factor = self._open_state.get(k, 0.0)
            if factor <= 0.0:
                continue
            out.append((k, fk, depth * factor, q))
        return out

    def _design_iir(self, f0: float) -> list[tuple]:
        """Design the biquad cascade of the notch bank."""
        fs = float(self.sample_rate)
        out = []
        for _k, freq, depth, q in self._bank(f0):
            out.append((_notch_biquad(freq, q, fs), _notch_depth_mix(depth)))
        return out

    def _design_fir(self, f0: float) -> np.ndarray:
        """Design the linear-phase kernel of the whole filter chain.

        The kernel is built by frequency sampling: the wanted magnitude
        response is evaluated on the grid of the kernel, and the notch of
        every harmonic is **put on the nearest grid point**, so the hum and
        its harmonics land exactly on a sample of the grid and the notches
        stay deep.  The kernel itself is symmetric, hence linear phase.
        """
        fs = float(self.sample_rate)
        specs = self._bank(f0)
        period = max(1, int(round(fs / max(f0, 1.0))))
        length = int(period * max(1, int(math.ceil(4096.0 / period))))
        length = int(min(max(length, 512), 16384)) | 1
        grid_hz = fs / length
        freqs = np.arange(length // 2 + 1, dtype=np.float64) * grid_hz
        mag = np.ones(length // 2 + 1, dtype=np.float64)
        for _k, f, depth, q in specs:
            g = _notch_depth_mix(depth)
            value = _filter_response(np.array([f]), _notch_biquad(f, q, fs), fs)[0]
            k = int(min(max(int(round(f / grid_hz)), 0), length // 2))
            mag[k] = mag[k] * ((1.0 - g) + g * value)
        if self.params.get("Enable Highpass"):
            mag = mag * _filter_response(freqs, _highpass_biquad(
                float(self.params.get("Highpass Frequency")), 0.7071, fs), fs)
        if self.params.get("Enable Lowpass"):
            mag = mag * _filter_response(freqs, _lowpass_biquad(
                float(self.params.get("Lowpass Frequency")), 0.7071, fs), fs)
        if self.params.get("Enable DC Offset"):
            # The DC blocker belongs INSIDE the kernel.  A stage that runs
            # before the filter answers before the filter's delay, so the
            # chain would not be linear phase and the module would report a
            # signal before the impulse that caused it had arrived.
            mag = mag * _filter_response(freqs, _highpass_biquad(5.0, 0.7071, fs), fs)
        return linear_phase_fir(mag, fs, length=length)

    def _fir_kernel(self, f0: float) -> np.ndarray:
        """Return the cached linear-phase kernel for the current settings."""
        # The measured depths are part of the key: a harmonic that starts or
        # stops carrying a hum changes the kernel.
        depths = tuple(
            int(round(self._open_state.get(k, 0.0) * 8.0)) for k, _f, _d, _q in self._bank(f0)
        )
        key = (
            round(f0, 3),
            int(self.params.get("Number of Harmonics")),
            str(self.params.get("Link Harmonics")),
            float(self.params.get("Harmonic Slope")),
            float(self.params.get("Filter Q")),
            bool(self.params.get("Enable Highpass")),
            float(self.params.get("Highpass Frequency")),
            bool(self.params.get("Enable Lowpass")),
            float(self.params.get("Lowpass Frequency")),
            depths,
        )
        if self._fir is None or self._fir_key != key:
            self._fir = self._design_fir(f0)
            self._fir_key = key
        return self._fir

    # -- processing ----------------------------------------------------------
    def _dc_block(self, x: np.ndarray) -> np.ndarray:
        """Remove a constant DC offset with a very low high-pass."""
        coeff = _highpass_biquad(5.0, 0.7071, float(self.sample_rate))
        if self._dc_state is None or self._dc_state.shape[1] != x.shape[1]:
            self._dc_state = np.zeros((2, x.shape[1]), dtype=np.float64)
        return _biquad_stream(coeff[:3], (1.0, coeff[3], coeff[4]), x, self._dc_state)

    def _highpass(self, x: np.ndarray) -> np.ndarray:
        """Apply the optional high-pass."""
        fc = float(self.params.get("Highpass Frequency"))
        coeff = _highpass_biquad(fc, 0.7071, float(self.sample_rate))
        if self._hp_state is None or self._hp_state.shape[1] != x.shape[1]:
            self._hp_state = np.zeros((2, x.shape[1]), dtype=np.float64)
        return _biquad_stream(coeff[:3], (1.0, coeff[3], coeff[4]), x, self._hp_state)

    def _lowpass(self, x: np.ndarray) -> np.ndarray:
        """Apply the optional low-pass."""
        fc = float(self.params.get("Lowpass Frequency"))
        coeff = _lowpass_biquad(fc, 0.7071, float(self.sample_rate))
        if self._lp_state is None or self._lp_state.shape[1] != x.shape[1]:
            self._lp_state = np.zeros((2, x.shape[1]), dtype=np.float64)
        return _biquad_stream(coeff[:3], (1.0, coeff[3], coeff[4]), x, self._lp_state)

    def _apply_iir(self, src: np.ndarray) -> np.ndarray:
        """Run the notch bank over the signal, tracking the hum as it goes."""
        n = src.shape[0]
        out = np.empty_like(src)
        for start in range(0, n, _SEGMENT):
            block = src[start:start + _SEGMENT]
            f0 = self._update_frequency(block)
            self._openness(f0, block.shape[0])
            coeffs = self._design_iir(f0)
            if len(self._states) != len(coeffs):
                self._states = [
                    np.zeros((2, src.shape[1]), dtype=np.float64) for _ in coeffs
                ]
            filtered = block
            for (coeff, mix), state in zip(coeffs, self._states):
                notched = _biquad_stream(
                    coeff[:3], (1.0, coeff[3], coeff[4]), filtered, state
                )
                filtered = (1.0 - mix) * filtered + mix * notched
            out[start:start + block.shape[0]] = filtered
        return out

    def _apply_fir(self, src: np.ndarray) -> np.ndarray:
        """Run the linear-phase filter chain over the signal.

        Two paths apply the same kernel:

        the fast path
            A weighted overlap-add filter bank: a symmetric window of
            4096 points, the frames half a window apart, the state
            carried between the blocks.  The bank answers the same thing
            as the kernel it is given, but the transform is paid once
            per half window instead of once per block, so a 64 sample
            block with two channels costs a few microseconds instead of
            the hundreds the direct convolution costs.

        the long path
            The shared FIR filter, which convolves the kernel with the
            whole history on every call.  It is the reference the bank is
            measured against, and it is used when the kernel is shorter
            than the window: then it is cheap and exact, and the bank
            would only add its own delay.

        The signal is run through in segments of ``_SEGMENT`` samples,
        the same grid the decision of the bank runs on, so that the
        answer does not depend on the size of the blocks the host uses.
        """
        n = src.shape[0]
        out = np.empty_like(src)
        for start in range(0, n, _SEGMENT):
            block = src[start:start + _SEGMENT]
            f0 = self._fundamental()
            self._remember(block)
            self._openness(f0, block.shape[0])
            kernel = self._fir_kernel(f0)
            if len(kernel) >= _WOLA_WINDOW:
                if self._wola is None or self._wola.kernel.size != len(kernel):
                    # The kernel is new or it changed its length: the bank
                    # is built again, with the window it needs.
                    self._wola = WolaBank(kernel, float(self.sample_rate),
                                          window_length=_WOLA_WINDOW)
                    self._wola_source = kernel
                elif self._wola_source is not kernel:
                    # The kernel changed its values: the bank takes the new
                    # response without losing its memory.  The spectrum of
                    # the kernel is computed again, which is the price of
                    # the change.
                    self._wola.set_kernel(kernel)
                    self._wola_source = kernel
                filtered = self._wola.process(block)
            else:
                if self._fir_filter is None or self._fir_filter.kernel.size != len(kernel):
                    self._fir_filter = FIR(kernel, float(self.sample_rate))
                    self._fir_source = kernel
                elif self._fir_source is not kernel:
                    self._fir_filter.set_kernel(kernel)
                    self._fir_source = kernel
                filtered = self._fir_filter.process(block)
            out[start:start + block.shape[0]] = filtered
        return out

    def _process_static(self, buf: np.ndarray) -> np.ndarray:
        """Static mode: a fixed bank of notch filters."""
        src = buf
        linear = bool(self.params.get("Linear Phase Filters"))
        hum_only = bool(self.params.get("Output Hum Only"))
        if self.params.get("Enable DC Offset") and (not linear or hum_only):
            # In the linear phase path the DC blocker is part of the
            # kernel, so that every stage of the chain carries the same
            # delay.  The estimate of the hum is not the output of the
            # kernel, so it needs the blocker of its own.
            src = self._dc_block(src)
        if linear and not hum_only:
            out = self._apply_fir(src)
        elif linear:
            # The estimate of the hum is made in the spectral domain and
            # does not use the kernel, so the kernel is not run: that saves
            # the whole cost of the linear phase path.  The measurement of
            # the hum still runs, so that the estimate knows which harmonics
            # carry a hum.
            self._remember(src)
            self._openness(self._fundamental(), src.shape[0])
            out = src
        else:
            # The IIR bank tracks the hum frequency while it runs, so it is
            # run even when only the estimate is wanted.
            out = self._apply_iir(src)
        if hum_only:
            return self._hum_render(src, self._tracked_freq or self._fundamental())
        if not linear:
            # The FIR carries the high-pass and the low-pass already: running
            # the IIR stages as well would apply them twice and would take
            # the linear phase away.
            if self.params.get("Enable Highpass"):
                out = self._highpass(out)
            if self.params.get("Enable Lowpass"):
                out = self._lowpass(out)
        return out

    def _hum_render(self, src: np.ndarray, f0: float) -> np.ndarray:
        """Estimate the hum component on its own.

        The estimate is the narrow-band content that sits on the hum
        fundamental and its harmonics.  It is taken in the spectral domain so
        the mask can be as narrow as the analysis resolution allows, which
        keeps programme material that lives close to a harmonic out of the
        estimate.
        """
        fs = float(self.sample_rate)
        n_fft = 4096
        hop = 1024
        specs = self._bank(f0)
        if not specs:
            return np.zeros_like(src)
        spec = _spectrogram(src, n_fft, hop)
        n_bins = spec.shape[1]
        mask = np.zeros(n_bins, dtype=np.float64)
        bin_hz = fs / n_fft
        for _k, freq, _depth, _q in specs:
            centre = int(round(freq / bin_hz))
            for d in range(-3, 4):
                k = centre + d
                if 0 <= k < n_bins:
                    weight = 0.5 * (1.0 + math.cos(math.pi * d / 3.0))
                    mask[k] = max(mask[k], weight)
        out = _resynthesize(spec * mask[None, :, None], n_fft, hop, src.shape[0])
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    def _process_dynamic(self, buf: np.ndarray) -> np.ndarray:
        """Dynamic mode: an adaptive bank that follows a drifting hum."""
        fs = float(self.sample_rate)
        n_fft = 4096
        hop = 1024
        spec = _spectrogram(buf, n_fft, hop)
        n_frames, n_bins, n_ch = spec.shape
        freqs = np.fft.rfftfreq(n_fft, 1.0 / fs)
        lo = float(self.params.get("Dynamic Low Cutoff"))
        hi = float(self.params.get("Dynamic High Cutoff"))
        bands = int(self.params.get("Dynamic Bands"))
        sens = float(self.params.get("Dynamic Sensitivity"))

        mono = spec.mean(axis=2)
        mag = np.abs(mono)
        gain_db = np.zeros((n_frames, n_bins), dtype=np.float64)

        bin_hz = fs / n_fft
        search_lo = int(max(1, math.ceil(lo / bin_hz)))
        search_hi = int(min(n_bins - 2, math.floor(min(hi, 500.0) / bin_hz)))
        track = self._dyn_freq

        for f in range(n_frames):
            row = mag[f]
            if search_hi <= search_lo:
                continue
            segment = row[search_lo:search_hi + 1]
            k = search_lo + int(np.argmax(segment))
            # refine the peak with the phase slope of the neighbouring bins
            if 0 < k < n_bins - 1:
                num = np.angle(mono[f, k + 1] * np.conj(mono[f, k - 1]))
                delta = (-num * n_fft / (4.0 * math.pi)) - k
                delta = (delta + 0.5) % 1.0 - 0.5
            else:
                delta = 0.0
            f0 = (k + delta) * bin_hz
            if track <= 0.0:
                track = f0
            else:
                track += 0.35 * (f0 - track)

            for b in range(1, bands + 1):
                target = b * track
                kk = int(round(target / bin_hz))
                if kk < 2 or kk > n_bins - 3:
                    continue
                lo_i = max(1, kk - 1)
                hi_i = min(n_bins - 2, kk + 1)
                kp = lo_i + int(np.argmax(row[lo_i:hi_i + 1]))
                if abs(kp - kk) > 1.5:
                    continue
                floor_idx = list(range(max(0, kp - 12), max(0, kp - 4))) + list(
                    range(min(n_bins, kp + 5), min(n_bins, kp + 13))
                )
                if not floor_idx:
                    continue
                floor = float(np.median(row[floor_idx]))
                if floor <= 0.0:
                    continue
                for b_idx in range(max(0, kp - 2), min(n_bins, kp + 3)):
                    ratio = row[b_idx] / floor
                    if ratio <= 1.0:
                        continue
                    excess = 20.0 * math.log10(ratio)
                    red = min(_MAX_DYNAMIC_REDUCTION_DB, sens * excess)
                    gain_db[f, b_idx] = min(gain_db[f, b_idx], -red)

        # smooth the gain curve across frequency and time
        if n_frames > 0 and n_bins > 2:
            kernel = np.array([0.25, 0.5, 0.25], dtype=np.float64)
            padded = np.pad(gain_db, ((0, 0), (1, 1)), mode="edge")
            gain_db = np.apply_along_axis(
                lambda v: np.convolve(v, kernel, mode="valid"), 1, padded
            )
        alpha = math.exp(-hop / (0.030 * fs))
        for f in range(1, n_frames):
            gain_db[f] = alpha * gain_db[f - 1] + (1.0 - alpha) * gain_db[f]

        self._dyn_freq = track
        self._dyn_gain = gain_db[-1].copy() if n_frames else np.zeros(0)

        gain = _db_to_lin(gain_db)[:, :, None]
        processed = spec * gain
        if self.params.get("Output Hum Only"):
            out = _resynthesize(spec - processed, n_fft, hop, buf.shape[0])
        else:
            out = _resynthesize(processed, n_fft, hop, buf.shape[0])
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove hum from a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        if str(self.params.get("Mode")) == "Dynamic":
            wet = self._process_dynamic(buf)
        else:
            wet = self._process_static(buf)
        if bool(self.params.get("Output Hum Only")):
            # the module renders the estimate of the hum on its own: the mix
            # does not apply, because the output already is the removed hum
            return wet
        out = self._tap.apply(
            buf, wet, float(self.params.get(REMOVED_MIX_NAME)), self.latency_samples
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return out

    # -- learning ------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Capture the hum fundamental (and its phase) from ``x``."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return
        mono = self._mono(buf)
        found = self._find_peak(mono, 20.0, 500.0, _LEARN_NFFT)
        if found is None:
            return
        freq, mag, phase = found
        if not np.isfinite(freq) or freq <= 0.0:
            return
        self._learned_freq = float(freq)
        self._learned_mag = float(mag)
        self._learned_phase = float(phase)
        self._has_learned = True
        self._tracked_freq = float(freq)
        self._dyn_freq = float(freq)

    @property
    def latency_samples(self) -> int:
        """Latency of the current filter topology."""
        if self.params.get("Output Hum Only"):
            # The estimate of the hum is made in the spectral domain and is
            # not the output of the filter chain, so the chain adds no
            # delay to it.
            return 0
        if self.params.get("Linear Phase Filters") and self.params.get("Mode") != "Dynamic":
            # The delay of the path that is actually used: the bank
            # answers a window later than the long path, because the
            # frames overlap.
            if self._wola is not None:
                return int(self._wola.latency_samples)
            if self._fir_filter is not None:
                return int(self._fir_filter.latency_samples)
            # The filter has not run yet: the delay of a symmetric kernel
            # is half its length, and the bank adds its window on top.
            kernel = self._fir_kernel(self._fundamental())
            if len(kernel) >= _WOLA_WINDOW:
                return int(_WOLA_WINDOW + _kernel_delay(kernel))
            return int(round((len(kernel) - 1) / 2.0))
        return 0

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The module does not ring for material that carries no hum: the
        notches only open when the hum is there, and then the output stops
        with the input (measured: 0 samples for a tone at -6 dBFS and for a
        signal at the clipping ceiling).

        The hum is the material the module is built for, and the notch
        rings in it: measured the way the host measures it, the output
        stays above -60 dBFS for 2152 samples after the input stopped when
        the input is the hum of the mains at 50 Hz and 100 Hz.  The tail
        covers that case, with a margin of 88 samples (1.8 ms).
        """
        return 2240
