"""
aurcore.dsp.resample — high quality sample rate conversion.

The converter is a **windowed-sinc polyphase resampler**: the signal is
conceptually zero-stuffed by ``up``, filtered with a linear-phase low-pass
prototype, and decimated by ``down``.  The polyphase decomposition makes the
implementation efficient (no zero-stuffed array is ever materialised) and it
is *exactly* band limited: everything above the lower of the two Nyquist
frequencies is suppressed before the decimation, so no alias can fold back
into the output band.

Filter design
-------------
The prototype is a Kaiser-windowed sinc evaluated at the *upsampled* rate:

* ``up = sr_out / g`` and ``down = sr_in / g`` with ``g = gcd(sr_in, sr_out)``
  (the two are coprime).
* The cutoff is ``fc = 0.5 * min(1/up, 1/down)`` in cycles/sample of the
  upsampled rate, i.e. exactly the lower of the two Nyquist frequencies.
* The passband gain is ``up`` so that the overall conversion is unity gain
  (every polyphase branch then has the same DC sum of 1).
* The transition band is centred on the cutoff and its width follows the
  Kaiser design rule ``N = 1 + (A - 8) / (2.285 * 2*pi * delta)`` where
  ``A`` is the stopband attenuation in dB and ``delta`` the transition width
  in cycles/sample.

Quality presets
---------------
=========  =========  =========================
name       stopband    transition (fraction of fc)
=========  =========  =========================
``fast``   60 dB      0.25
``medium`` 90 dB      0.12   (default)
``high``   120 dB     0.06
``ultra``  140 dB     0.03
=========  =========  =========================

A custom stopband attenuation (in dB) can be passed as a float instead of a
preset name, and ``transition`` overrides the transition fraction.
"""
from __future__ import annotations

import math
from typing import Iterable, Iterator

import numpy as np

from .windows import kaiser

__all__ = [
    "resample",
    "resample_stream",
    "Resampler",
    "design_prototype",
    "QUALITY_PRESETS",
]

#: name -> (stopband attenuation in dB, transition width as a fraction of fc)
QUALITY_PRESETS: dict[str, tuple[float, float]] = {
    "fast": (60.0, 0.25),
    "medium": (90.0, 0.12),
    "high": (120.0, 0.06),
    "ultra": (140.0, 0.03),
}

#: Transition fraction used when a numeric attenuation is given.
_DEFAULT_TRANSITION = 0.12


def _ratio(sr_in: int, sr_out: int) -> tuple[int, int]:
    """Coprime (up, down) factors of ``sr_out / sr_in``."""
    sr_in = int(sr_in)
    sr_out = int(sr_out)
    if sr_in <= 0 or sr_out <= 0:
        raise ValueError("sample rates must be positive")
    g = math.gcd(sr_in, sr_out)
    return sr_out // g, sr_in // g


def design_prototype(
    up: int,
    down: int,
    quality: str | float = "medium",
    transition: float | None = None,
) -> tuple[np.ndarray, int, float, float]:
    """Design the low-pass prototype of the polyphase resampler.

    Parameters
    ----------
    up : int
        Upsampling factor (integer).
    down : int
        Downsampling factor (integer).
    quality : str or float
        Preset name (``"fast"``, ``"medium"``, ``"high"``, ``"ultra"``) or a
        stopband attenuation in dB.
    transition : float, optional
        Transition width as a fraction of the cutoff.  Overrides the preset.

    Returns
    -------
    (numpy.ndarray, int, float, float)
        ``(h, half_len, fc, beta)``: the prototype taps (length a multiple of
        ``up``), the group delay in output samples, the normalised cutoff and
        the Kaiser shape parameter.
    """
    up = int(up)
    down = int(down)
    if up < 1 or down < 1:
        raise ValueError("up and down must be >= 1")

    if isinstance(quality, str):
        key = quality.strip().lower()
        if key not in QUALITY_PRESETS:
            raise ValueError(
                f"unknown quality {quality!r}; known: {', '.join(sorted(QUALITY_PRESETS))}"
            )
        attenuation, trans = QUALITY_PRESETS[key]
    else:
        attenuation = float(quality)
        trans = _DEFAULT_TRANSITION

    if transition is not None:
        trans = float(transition)
    trans = min(max(trans, 1e-4), 0.9)

    beta = 0.1102 * (attenuation - 8.7) if attenuation > 50.0 else 0.0

    fc = 0.5 * min(1.0 / up, 1.0 / down)  # cycles/sample of the upsampled rate
    delta = trans * fc  # transition width in cycles/sample
    n_desired = 1.0 + (attenuation - 8.0) / (2.285 * 2.0 * math.pi * delta)
    half_len = max(1, int(math.ceil((n_desired - 1.0) / (2.0 * down))))

    n_taps = 2 * half_len * down + 1
    centre = half_len * down
    idx = np.arange(n_taps, dtype=np.float64) - centre
    # the passband gain is `up`: the polyphase branches then all have the
    # same DC sum (up / up = 1) and the conversion is unity gain
    h = up * fc * np.sinc(fc * idx) * kaiser(n_taps, beta, sym=True)

    # pad to a multiple of `up` so every polyphase branch has the same length
    taps = int(math.ceil(n_taps / up))
    if taps * up != n_taps:
        h = np.concatenate([h, np.zeros(taps * up - n_taps, dtype=np.float64)])
    return h, half_len, fc, beta


def _resample_core(
    buf: np.ndarray,
    base: int,
    h: np.ndarray,
    up: int,
    down: int,
    n_start: int,
    n_end: int,
) -> np.ndarray:
    """Compute the resampled outputs ``n`` in ``[n_start, n_end)``.

    Parameters
    ----------
    buf : numpy.ndarray
        Input samples, shape ``(n, n_channels)``.  ``buf[0]`` has the
        absolute stream index ``base``.
    base : int
        Absolute index of ``buf[0]``.
    h : numpy.ndarray
        Prototype taps, length a multiple of ``up``.
    up, down : int
        Polyphase factors.
    n_start, n_end : int
        Range of output indices to compute (in the *undelayed* output index
        space, where 0 corresponds to the first input sample).

    Returns
    -------
    numpy.ndarray
        The resampled samples, shape ``(n_end - n_start, n_channels)``.
    """
    n_end = int(n_end)
    n_start = int(n_start)
    if n_end <= n_start:
        return np.zeros((0,) + buf.shape[1:], dtype=np.float64)

    taps = len(h) // up
    lo = (n_start * down) // up - taps + 1
    hi = ((n_end - 1) * down) // up

    i_start = lo - base
    i_end = hi - base + 1
    i_lo = max(0, i_start)
    i_hi = min(len(buf), i_end)
    seg = buf[i_lo:i_hi]
    if i_start < 0:
        seg = np.concatenate([np.zeros((-i_start,) + buf.shape[1:], dtype=np.float64), seg])
    if seg.shape[0] < taps:
        seg = np.concatenate(
            [seg, np.zeros((taps - seg.shape[0],) + buf.shape[1:], dtype=np.float64)]
        )
    offset = base + i_start  # absolute index of seg[0]

    view = np.lib.stride_tricks.sliding_window_view(seg, taps, axis=0)
    out = np.zeros((n_end - n_start,) + buf.shape[1:], dtype=np.float64)
    inv_down = pow(down, -1, up) if up > 1 else 0
    for p in range(up):
        # the outputs whose (n * down) % up == p form one polyphase branch
        n0 = n_start + ((p * inv_down - n_start) % up) if up > 1 else n_start
        if n0 >= n_end:
            continue
        ns = np.arange(n0, n_end, up, dtype=np.int64)
        q = (ns * down) // up
        rows = view[q - taps + 1 - offset]  # (k, channels, taps)
        branch = h[p::up][::-1]
        out[ns - n_start] = np.einsum("kct,t->kc", rows, branch)
    return out


def resample(
    x,
    sr_in: int,
    sr_out: int,
    quality: str | float = "medium",
    transition: float | None = None,
) -> np.ndarray:
    """Resample a signal to a new sample rate.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    sr_in : int
        Input sample rate in Hz.
    sr_out : int
        Output sample rate in Hz.
    quality : str or float
        ``"fast"``, ``"medium"`` (default), ``"high"``, ``"ultra"`` or a
        stopband attenuation in dB.
    transition : float, optional
        Transition band width as a fraction of the cutoff (overrides the
        preset).

    Returns
    -------
    numpy.ndarray
        The resampled signal, shape ``(n_out,)`` for a mono input and
        ``(n_out, n_channels)`` otherwise, where
        ``n_out = n_samples * sr_out // sr_in``.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
        mono = True
    elif arr.ndim == 2:
        mono = False
    else:
        raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")

    sr_in = int(sr_in)
    sr_out = int(sr_out)
    if sr_in == sr_out:
        return arr[:, 0].copy() if mono else arr.copy()

    up, down = _ratio(sr_in, sr_out)
    h, half_len, _fc, _beta = design_prototype(up, down, quality, transition)

    n_in = arr.shape[0]
    n_out = n_in * up // down
    n_total = n_out + half_len

    outs = []
    n_in = arr.shape[0]
    n_out = n_in * up // down
    n_total = n_out + half_len

    need = ((n_total - 1) * down) // up + 1
    if need > n_in:
        arr = np.concatenate(
            [arr, np.zeros((need - n_in, arr.shape[1]), dtype=np.float64)], axis=0
        )
    y = _resample_core(arr, 0, h, up, down, 0, n_total)
    out = y[half_len : half_len + n_out]
    return out[:, 0] if mono else out


class Resampler:
    """Stateful, block-by-block sample rate converter.

    Feed it blocks with :meth:`process` and finish with :meth:`flush`; the
    concatenated output is identical to :func:`resample` on the whole signal.

    Parameters
    ----------
    sr_in : int
        Input sample rate in Hz.
    sr_out : int
        Output sample rate in Hz.
    quality : str or float
        Quality preset or stopband attenuation in dB.
    transition : float, optional
        Transition band width as a fraction of the cutoff.
    """

    def __init__(
        self,
        sr_in: int,
        sr_out: int,
        quality: str | float = "medium",
        transition: float | None = None,
    ):
        self.sr_in = int(sr_in)
        self.sr_out = int(sr_out)
        self.up, self.down = _ratio(self.sr_in, self.sr_out)
        self.h, self.half_len, self.fc, self.beta = design_prototype(
            self.up, self.down, quality, transition
        )
        self.taps = len(self.h) // self.up
        self._buf = np.zeros((0, 1), dtype=np.float64)
        self._base = 0  # absolute index of _buf[0]
        self._next_n = 0  # next undelayed output index to compute
        self._computed = 0  # number of undelayed outputs computed so far
        self._returned = 0  # number of aligned output samples handed out
        self._mono = True  # channel layout of the input seen so far

    # -- internals ------------------------------------------------------------
    def _available(self) -> int:
        """Number of input samples received so far."""
        return self._base + self._buf.shape[0]

    def _compute(self, n_end: int) -> np.ndarray:
        """Compute the undelayed outputs in ``[self._next_n, n_end)``."""
        if n_end <= self._next_n:
            return np.zeros((0, self._buf.shape[1]), dtype=np.float64)
        y = _resample_core(self._buf, self._base, self.h, self.up, self.down, self._next_n, n_end)
        self._next_n = n_end
        self._computed = n_end
        return y

    def _trim(self) -> None:
        """Drop the input samples that can no longer influence an output."""
        keep_from = (self._next_n * self.down) // self.up - self.taps + 1
        if keep_from > self._base:
            drop = keep_from - self._base
            self._buf = self._buf[drop:]
            self._base = keep_from

    def _shape_out(self, block: np.ndarray) -> np.ndarray:
        """Return ``block`` with the channel layout of the input."""
        return block[:, 0] if self._mono else block

    # -- public API -----------------------------------------------------------
    def process(self, x) -> np.ndarray:
        """Resample one block of input.

        Parameters
        ----------
        x : array_like
            Input block, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The resampled samples for this block, same channel layout as the
            input.  May be empty (the converter buffers a little internally).
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
            mono = True
        elif arr.ndim == 2:
            mono = False
        else:
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")

        if self.sr_in == self.sr_out:
            return arr[:, 0].copy() if mono else arr.copy()

        if self._buf.shape[0] == 0:
            self._buf = np.zeros((0, arr.shape[1]), dtype=np.float64)
            self._mono = mono
        elif arr.shape[1] != self._buf.shape[1]:
            raise ValueError("channel count cannot change between blocks")

        if arr.shape[0]:
            self._buf = np.concatenate([self._buf, arr], axis=0)

        available = self._available()
        n_max = (available * self.up - 1) // self.down  # largest n with q(n) <= available-1
        computed_old = self._computed
        y = self._compute(int(n_max) + 1)
        self._trim()

        # the group delay is half_len output samples: aligned index = n - half_len
        skip = max(0, self._returned - (computed_old - self.half_len))
        aligned = y[skip:]
        self._returned += aligned.shape[0]
        return self._shape_out(aligned)

    def flush(self) -> np.ndarray:
        """Finish the conversion and return the remaining samples.

        Returns
        -------
        numpy.ndarray
            The tail of the resampled signal, same channel layout as the
            input.
        """
        if self.sr_in == self.sr_out or self._buf.shape[0] == 0:
            return np.zeros((0,), dtype=np.float64)

        available = self._available()
        n_out = available * self.up // self.down
        n_total = n_out + self.half_len
        need = ((n_total - 1) * self.down) // self.up + 1
        if need > self._buf.shape[0]:
            self._buf = np.concatenate(
                [self._buf, np.zeros((need - self._buf.shape[0], self._buf.shape[1]))], axis=0
            )
        computed_old = self._computed
        y = self._compute(n_total)
        skip = max(0, self._returned - (computed_old - self.half_len))
        aligned = y[skip:]
        self._returned += aligned.shape[0]
        return self._shape_out(aligned)


def resample_stream(
    blocks: Iterable,
    sr_in: int,
    sr_out: int,
    quality: str | float = "medium",
    transition: float | None = None,
) -> Iterator[np.ndarray]:
    """Resample a stream of blocks, yielding resampled blocks.

    Parameters
    ----------
    blocks : Iterable
        Iterable of input blocks, each ``(n_samples,)`` or
        ``(n_samples, n_channels)``.
    sr_in : int
        Input sample rate in Hz.
    sr_out : int
        Output sample rate in Hz.
    quality : str or float
        Quality preset or stopband attenuation in dB.
    transition : float, optional
        Transition band width as a fraction of the cutoff.

    Yields
    ------
    numpy.ndarray
        Resampled blocks; the concatenation of all of them is the fully
        resampled signal.
    """
    conv = Resampler(sr_in, sr_out, quality, transition)
    for block in blocks:
        out = conv.process(block)
        if out.size:
            yield out
    tail = conv.flush()
    if tail.size:
        yield tail
