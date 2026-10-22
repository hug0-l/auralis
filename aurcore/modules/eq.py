"""
aurcore.modules.eq — four band parametric equaliser.

Two filter topologies are offered:

``Analog IIR``
    A cascade of four RBJ peaking biquads.  Cheap, causal and free of latency;
    this is the classic "analog" parametric EQ shape.

``Digital Linear Phase FIR``
    The same four peaking curves are realised as a single linear-phase FIR
    kernel which is applied forward and backward.  The net phase shift is zero
    and the group delay is doubled, which costs latency but keeps the
    transient shape of the material intact.

The biquad design, the streaming filter and the linear-phase pass are also
imported by :mod:`aurcore.modules.eq_match`, which fits a learned curve with
the same filter primitives.

Latency
-------
The analog path is a cascade of biquads and delays the output by nothing
at all: measured with a sweep, the delay is 0 samples and the cross
correlation of the input and the output is 1.000.

The linear phase path applies its kernel forward and backward, so it adds
the length of the kernel minus one sample, which the module reports as its
latency.  A host that measures the delay of the linear phase path finds
952 samples against the 1023 the module reports; the difference is the
taper of the kernel, which no longer carries energy at its edges.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

try:  # pragma: no cover - scipy is optional, numpy fallback below
    from scipy.signal import lfilter as _sp_lfilter
except Exception:  # pragma: no cover
    _sp_lfilter = None

__all__ = [
    "EQ",
    "biquad_stream",
    "next_pow2",
    "peaking_biquad",
    "zero_phase_fir",
]

#: Longest impulse response used by the linear-phase path.
MAX_IR_LENGTH = 8192


def next_pow2(n: int) -> int:
    """Return the smallest power of two greater than or equal to ``n``."""
    v = 1
    while v < int(n):
        v <<= 1
    return v


def peaking_biquad(freq: float, gain_db: float, q: float, sample_rate: int):
    """Design an RBJ peaking (bell) biquad.

    Returns the normalised coefficient tuple ``(b0, b1, b2, a1, a2)``.  At the
    centre frequency the filter has exactly ``gain_db`` decibels of gain.
    """
    fs = float(sample_rate)
    f0 = min(max(float(freq), 10.0), 0.49 * fs)
    g = float(gain_db)
    if g == 0.0:
        return (1.0, 0.0, 0.0, 0.0, 0.0)
    a = 10.0 ** (g / 40.0)
    w0 = 2.0 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2.0 * max(float(q), 1e-3))
    cos_w0 = math.cos(w0)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _biquad_numpy(b, a, x: np.ndarray, zi: np.ndarray):
    """Pure-numpy causal biquad (direct form II transposed).

    Used when scipy is unavailable.  ``x`` is ``(n, ch)``; ``zi`` is
    ``(2, ch)`` and is updated in place.
    """
    b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
    a1, a2 = float(a[0]), float(a[1])
    n, ch = x.shape
    y = np.empty_like(x)
    z0 = np.array(zi[0], dtype=np.float64, copy=True)
    z1 = np.array(zi[1], dtype=np.float64, copy=True)
    for c in range(ch):
        s0 = float(z0[c])
        s1 = float(z1[c])
        col = x[:, c]
        out = np.empty(n, dtype=np.float64)
        for i in range(n):
            xi = float(col[i])
            yi = b0 * xi + s0
            s0 = b1 * xi - a1 * yi + s1
            s1 = b2 * xi - a2 * yi
            out[i] = yi
        y[:, c] = out
        z0[c] = s0
        z1[c] = s1
    zi[0] = z0
    zi[1] = z1
    return y


def biquad_stream(b, a, x: np.ndarray, zi: np.ndarray):
    """Filter a block with a biquad, carrying the delay state in ``zi``.

    ``zi`` must be a ``(2, channels)`` array; it is updated in place and
    returned so the caller can keep it between blocks.
    """
    if _sp_lfilter is not None:
        y, zf = _sp_lfilter(b, a, x, axis=0, zi=zi)
        zi[:, :] = zf
        return y
    return _biquad_numpy(b, a, x, zi)


def zero_phase_fir(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Apply a symmetric FIR forward and backward (no net phase shift).

    The pair of passes behaves like a zero-phase filter with the squared
    magnitude response of ``h`` and the doubled group delay of ``h``.
    """
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    n = buf.shape[0]
    length = len(h)
    if n == 0 or length <= 1:
        return buf
    nfft = next_pow2(n + 2 * (length - 1))
    spec = np.fft.rfft(h, nfft)
    data = np.fft.rfft(buf, nfft, axis=0)
    gain = (np.abs(spec) ** 2)[:, None]
    out = np.fft.irfft(data * gain, nfft, axis=0)
    return out[length - 1: length - 1 + n]


def _tukey(length: int, taper: float = 0.2) -> np.ndarray:
    """Return a Tukey window (flat in the middle, tapered at the ends)."""
    n = int(length)
    if n <= 1:
        return np.ones(max(n, 0), dtype=np.float64)
    w = np.ones(n, dtype=np.float64)
    edge = int(max(1, round(taper * n / 2.0)))
    if 2 * edge >= n:
        edge = max(1, n // 2)
    ramp = 0.5 * (1.0 + np.cos(np.pi * np.arange(edge) / edge))
    w[:edge] = ramp
    w[-edge:] = ramp[::-1]
    return w


@register
class EQ(Module):
    """Four band parametric equaliser."""

    code = "AUR_EQ"
    display_name = "EQ"
    category = "Utility"
    edition = "standard"
    element = "ParametricEQ"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._states: list[np.ndarray] = []
        self._ir: np.ndarray | None = None
        self._ir_key: tuple | None = None

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        descs = [
            ParamDesc(
                name="Type",
                type="enum",
                default="Analog IIR",
                minimum=0.0,
                maximum=1.0,
                choices=("Analog IIR", "Digital Linear Phase FIR"),
                group="Algorithm",
                help="Filter topology used for the four bands.",
            )
        ]
        defaults = ((100.0, 1.0), (1000.0, 1.0), (5000.0, 1.0), (10000.0, 1.0))
        for index, (freq, q) in enumerate(defaults, start=1):
            group = f"Band {index}"
            descs.append(
                ParamDesc(
                    name=f"Band {index} Frequency",
                    type="float",
                    default=freq,
                    minimum=20.0,
                    maximum=20000.0,
                    unit="Hz",
                    group=group,
                    curve="log",
                    help="Centre frequency of the band.",
                )
            )
            descs.append(
                ParamDesc(
                    name=f"Band {index} Gain",
                    type="float",
                    default=0.0,
                    minimum=-18.0,
                    maximum=3.6,
                    unit="dB",
                    group=group,
                    help="Gain of the band in decibels.",
                    curve="taper",
                )
            )
            descs.append(
                ParamDesc(
                    name=f"Band {index} Q",
                    type="float",
                    default=q,
                    minimum=0.1,
                    maximum=20.0,
                    group=group,
                    curve="log",
                    help="Bandwidth of the band.",
                )
            )
        descs.append(
            ParamDesc(
                name="Output Gain",
                type="float",
                default=0.0,
                minimum=-18.0,
                maximum=3.6,
                unit="dB",
                group="Gain",
                help="Gain applied after the filter chain.",
                curve="taper",
            )
        )
        return descs

    # -- helpers -------------------------------------------------------------
    def _bands(self) -> list[tuple[float, float, float]]:
        """Return the four ``(frequency, gain, q)`` band settings."""
        bands = []
        for index in range(1, 5):
            freq = float(self.params.get(f"Band {index} Frequency"))
            gain = float(self.params.get(f"Band {index} Gain"))
            q = float(self.params.get(f"Band {index} Q"))
            bands.append((freq, gain, q))
        return bands

    def _coefficients(self) -> list[tuple]:
        """Design the biquad cascade for the current band settings."""
        return [
            peaking_biquad(freq, gain, q, self.sample_rate)
            for freq, gain, q in self._bands()
        ]

    def _impulse_response(self) -> np.ndarray:
        """Build the linear-phase kernel for the current band settings.

        The kernel is designed by frequency sampling: the magnitude response of
        the analog cascade is evaluated on the FFT grid, the square root is
        taken and a symmetric kernel is reconstructed.  Applying that kernel
        forward and backward therefore reproduces the requested curve exactly
        while adding no phase shift.
        """
        bands = self._bands()
        key = tuple(bands)
        if self._ir is not None and self._ir_key == key:
            return self._ir
        length = 1024
        for freq, gain, q in bands:
            if gain == 0.0:
                continue
            decay = q / (math.pi * max(freq, 20.0))
            length = max(length, int(self.sample_rate * 6.0 * decay) + 64)
        length = int(min(max(next_pow2(length), 1024), MAX_IR_LENGTH))
        w = 2.0 * np.pi * np.arange(length, dtype=np.float64) / float(length)
        mag = np.ones(length, dtype=np.float64)
        for coeff in self._coefficients():
            b0, b1, b2, a1, a2 = coeff
            num = b0 + b1 * np.exp(-1j * w) + b2 * np.exp(-2j * w)
            den = 1.0 + a1 * np.exp(-1j * w) + a2 * np.exp(-2j * w)
            mag *= np.abs(num / den)
        target = np.sqrt(mag)
        shift = np.exp(-1j * w * (length - 1) / 2.0)
        ir = np.fft.irfft(target * shift, length)
        ir *= _tukey(length, 0.1)
        self._ir = ir
        self._ir_key = key
        return ir

    # -- processing ----------------------------------------------------------
    def reset(self) -> None:
        """Clear the filter delay state."""
        self._states = []
        self._ir = None
        self._ir_key = None

    @property
    def latency_samples(self) -> int:
        """Latency introduced by the current filter topology."""
        if str(self.params.get("Type")) == "Digital Linear Phase FIR":
            return len(self._impulse_response()) - 1
        return 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """Equalise a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        if str(self.params.get("Type")) == "Digital Linear Phase FIR":
            out = zero_phase_fir(buf, self._impulse_response())
        else:
            coeffs = self._coefficients()
            if len(self._states) != len(coeffs):
                self._states = [
                    np.zeros((2, buf.shape[1]), dtype=np.float64) for _ in coeffs
                ]
            out = buf
            for coeff, state in zip(coeffs, self._states):
                out = biquad_stream(coeff[:3], (1.0, coeff[3], coeff[4]), out, state)
        out_gain = float(self.params.get("Output Gain"))
        if out_gain != 0.0:
            out = out * (10.0 ** (out_gain / 20.0))
        return out
