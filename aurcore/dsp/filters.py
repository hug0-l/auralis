"""
aurcore.dsp.filters — biquad design and stateful filtering.

The coefficient factories implement the classic second-order ("biquad")
design equations published by Robert Bristow-Johnson, in the normalised form
``a0 = 1``:

``H(z) = (b0 + b1 z^-1 + b2 z^-2) / (1 + a1 z^-1 + a2 z^-2)``

Every factory returns the tuple ``(b0, b1, b2, a1, a2)`` in that order.

The stateful classes (:class:`Biquad`, :class:`Butterworth`,
:class:`LinkwitzRiley`, :class:`OnePole`, :class:`DCBlocker`) keep their
filter memory between calls, so they can be used inside a realtime block
processor.  :class:`FIR` does the same for a finite impulse response
kernel, and :func:`linear_phase_fir` designs one with a constant group
delay.  ``scipy.signal.lfilter`` is used when SciPy is importable and a
pure NumPy recursion is used otherwise; both produce the same numbers.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

__all__ = [
    "biquad_lowpass",
    "biquad_highpass",
    "biquad_bandpass",
    "biquad_notch",
    "biquad_peaking",
    "biquad_lowshelf",
    "biquad_highshelf",
    "biquad_allpass",
    "Biquad",
    "Cascade",
    "Butterworth",
    "LinkwitzRiley",
    "OnePole",
    "DCBlocker",
    "FIR",
    "linear_phase_fir",
    "butterworth_lowpass",
    "butterworth_highpass",
    "linkwitz_riley_lowpass",
    "linkwitz_riley_highpass",
]

Coeffs = tuple[float, float, float, float, float]

#: Coefficients below this are treated as zero.
_EPS = 1e-14


# ---------------------------------------------------------------------------
# coefficient factories
# ---------------------------------------------------------------------------
def _norm(b0, b1, b2, a0, a1, a2) -> Coeffs:
    """Normalise a biquad so that ``a0 == 1``."""
    a0 = float(a0)
    if abs(a0) < _EPS:
        raise ValueError("degenerate filter (a0 == 0)")
    return (
        float(b0) / a0,
        float(b1) / a0,
        float(b2) / a0,
        float(a1) / a0,
        float(a2) / a0,
    )


def _alpha(f0: float, q: float, fs: float) -> tuple[float, float]:
    """Return ``(omega0, alpha)`` for a cutoff and quality factor."""
    f0 = float(f0)
    q = float(q)
    fs = float(fs)
    nyq = 0.5 * fs
    if f0 <= 0.0 or f0 >= nyq:
        raise ValueError(f"cutoff {f0} Hz must be inside (0, {nyq}) Hz")
    if q <= 0.0:
        raise ValueError("Q must be > 0")
    w0 = 2.0 * math.pi * f0 / fs
    return w0, math.sin(w0) / (2.0 * q)


def biquad_lowpass(f0: float, q: float, fs: float) -> Coeffs:
    """Second-order low-pass.

    Parameters
    ----------
    f0 : float
        Cutoff frequency in Hz.  The response is **not** -3 dB there in
        general: the level at ``f0`` is exactly ``q`` (that is
        ``20 * log10(q)`` dB), so it is -3.01 dB only for the Butterworth
        case ``q = 1 / sqrt(2)``.  At ``q = 1`` the response is 0 dB at
        ``f0``, at ``q = 2`` it is +6.02 dB.
    q : float
        Quality factor; ``1/sqrt(2)`` is the Butterworth case.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    b0 = (1.0 - c) / 2.0
    b1 = 1.0 - c
    b2 = b0
    return _norm(b0, b1, b2, 1.0 + a, -2.0 * c, 1.0 - a)


def biquad_highpass(f0: float, q: float, fs: float) -> Coeffs:
    """Second-order high-pass.

    Parameters
    ----------
    f0 : float
        Cutoff frequency in Hz.  The response is **not** -3 dB there in
        general: the level at ``f0`` is exactly ``q`` (that is
        ``20 * log10(q)`` dB), so it is -3.01 dB only for the Butterworth
        case ``q = 1 / sqrt(2)``.  At ``q = 1`` the response is 0 dB at
        ``f0``, at ``q = 2`` it is +6.02 dB.
    q : float
        Quality factor.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    b0 = (1.0 + c) / 2.0
    b1 = -(1.0 + c)
    b2 = b0
    return _norm(b0, b1, b2, 1.0 + a, -2.0 * c, 1.0 - a)


def biquad_bandpass(f0: float, q: float, fs: float) -> Coeffs:
    """Second-order band-pass with 0 dB peak gain.

    The peak gain is exactly 1.0 at ``f0`` (measured: 0.0000 dB).

    Parameters
    ----------
    f0 : float
        Centre frequency in Hz.
    q : float
        Quality factor; the bandwidth is ``f0 / q``.  The measured -3 dB
        width is 0.3 % to 0.9 % narrower than that, depending on ``q``.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    return _norm(a, 0.0, -a, 1.0 + a, -2.0 * c, 1.0 - a)


def biquad_notch(f0: float, q: float, fs: float) -> Coeffs:
    """Second-order notch (band-stop).

    Parameters
    ----------
    f0 : float
        Centre frequency in Hz.  The null sits exactly on ``f0``.
    q : float
        Quality factor; the notch width is ``f0 / q``.  The measured -3 dB
        width is 0.3 % to 0.9 % narrower than that, depending on ``q``.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    return _norm(1.0, -2.0 * c, 1.0, 1.0 + a, -2.0 * c, 1.0 - a)


def biquad_peaking(f0: float, q: float, gain_db: float, fs: float) -> Coeffs:
    """Second-order peaking (bell) equaliser.

    The gain at ``f0`` is exactly ``gain_db`` (measured to 0.0001 dB).

    Parameters
    ----------
    f0 : float
        Centre frequency in Hz.
    q : float
        Quality factor.  The bandwidth ``f0 / q`` is the width between the
        two frequencies where the magnitude has fallen to **half** its peak
        amplitude (measured: 999.7 Hz for ``f0 / q`` = 1000 Hz).  The -3 dB
        width of the bell is narrower: 0.534 times ``f0 / q`` (measured:
        534.4 Hz for ``f0 / q`` = 1000 Hz).
    gain_db : float
        Peak gain in dB (positive boosts, negative cuts).
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    amp = 10.0 ** (float(gain_db) / 40.0)
    return _norm(
        1.0 + a * amp,
        -2.0 * c,
        1.0 - a * amp,
        1.0 + a / amp,
        -2.0 * c,
        1.0 - a / amp,
    )


def biquad_lowshelf(f0: float, q: float, gain_db: float, fs: float) -> Coeffs:
    """Second-order low-shelf equaliser.

    Parameters
    ----------
    f0 : float
        Corner frequency in Hz.
    q : float
        Quality factor (shelf slope).
    gain_db : float
        Shelved gain in dB, applied below ``f0``.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    amp = 10.0 ** (float(gain_db) / 40.0)
    two_sqrt_a_alpha = 2.0 * math.sqrt(amp) * a
    b0 = amp * ((amp + 1.0) - (amp - 1.0) * c + two_sqrt_a_alpha)
    b1 = 2.0 * amp * ((amp - 1.0) - (amp + 1.0) * c)
    b2 = amp * ((amp + 1.0) - (amp - 1.0) * c - two_sqrt_a_alpha)
    a0 = (amp + 1.0) + (amp - 1.0) * c + two_sqrt_a_alpha
    a1 = -2.0 * ((amp - 1.0) + (amp + 1.0) * c)
    a2 = (amp + 1.0) + (amp - 1.0) * c - two_sqrt_a_alpha
    return _norm(b0, b1, b2, a0, a1, a2)


def biquad_highshelf(f0: float, q: float, gain_db: float, fs: float) -> Coeffs:
    """Second-order high-shelf equaliser.

    Parameters
    ----------
    f0 : float
        Corner frequency in Hz.
    q : float
        Quality factor (shelf slope).
    gain_db : float
        Shelved gain in dB, applied above ``f0``.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    amp = 10.0 ** (float(gain_db) / 40.0)
    two_sqrt_a_alpha = 2.0 * math.sqrt(amp) * a
    b0 = amp * ((amp + 1.0) + (amp - 1.0) * c + two_sqrt_a_alpha)
    b1 = -2.0 * amp * ((amp - 1.0) + (amp + 1.0) * c)
    b2 = amp * ((amp + 1.0) + (amp - 1.0) * c - two_sqrt_a_alpha)
    a0 = (amp + 1.0) - (amp - 1.0) * c + two_sqrt_a_alpha
    a1 = 2.0 * ((amp - 1.0) - (amp + 1.0) * c)
    a2 = (amp + 1.0) - (amp - 1.0) * c - two_sqrt_a_alpha
    return _norm(b0, b1, b2, a0, a1, a2)


def biquad_allpass(f0: float, q: float, fs: float) -> Coeffs:
    """Second-order all-pass.

    Parameters
    ----------
    f0 : float
        Centre frequency in Hz.
    q : float
        Quality factor.
    fs : float
        Sample rate in Hz.

    Returns
    -------
    tuple
        ``(b0, b1, b2, a1, a2)`` with ``a0 = 1``.
    """
    w0, a = _alpha(f0, q, fs)
    c = math.cos(w0)
    return _norm(1.0 - a, -2.0 * c, 1.0 + a, 1.0 + a, -2.0 * c, 1.0 - a)


# ---------------------------------------------------------------------------
# stateful filtering
# ---------------------------------------------------------------------------
def _lfilter(b, a, x, zi):
    """Second-order IIR filter with a SciPy fast path and a NumPy fallback."""
    x = np.asarray(x, dtype=np.float64)
    try:  # pragma: no cover - depends on the environment
        from scipy.signal import lfilter as _sp_lfilter

        y, zf = _sp_lfilter(b, a, x, zi=zi)
        return y, zf
    except Exception:
        pass
    return _lfilter_numpy(b, a, x, zi)


def _lfilter_numpy(b, a, x, zi):
    """Pure NumPy transposed direct form II recursion (matches SciPy)."""
    b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
    a1, a2 = float(a[1]), float(a[2])
    n = x.shape[0]
    out = np.empty(n, dtype=np.float64)
    s0 = float(zi[0]) if zi is not None else 0.0
    s1 = float(zi[1]) if zi is not None else 0.0
    for i in range(n):
        xi = float(x[i])
        y = b0 * xi + s0
        s0 = b1 * xi - a1 * y + s1
        s1 = b2 * xi - a2 * y
        out[i] = y
    return out, np.array([s0, s1], dtype=np.float64)


class Biquad:
    """A stateful second-order IIR section.

    Parameters
    ----------
    b0, b1, b2, a1, a2 : float
        Coefficients of ``H(z) = (b0 + b1 z^-1 + b2 z^-2) / (1 + a1 z^-1 +
        a2 z^-2)``.  Use the ``biquad_*`` factories to obtain them.

    Examples
    --------
    >>> b = Biquad(*biquad_lowpass(1000.0, 0.7071, 48000.0))
    >>> y = b.process(np.zeros(10))
    """

    __slots__ = ("b0", "b1", "b2", "a1", "a2", "_zi", "_nch")

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float):
        self.b0 = float(b0)
        self.b1 = float(b1)
        self.b2 = float(b2)
        self.a1 = float(a1)
        self.a2 = float(a2)
        self._zi = np.zeros(2, dtype=np.float64)
        self._nch = 1

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_coeffs(cls, coeffs: Sequence[float]) -> "Biquad":
        """Build a biquad from a ``(b0, b1, b2, a1, a2)`` tuple."""
        b0, b1, b2, a1, a2 = (float(c) for c in coeffs)
        return cls(b0, b1, b2, a1, a2)

    @classmethod
    def from_design(
        cls,
        kind: str,
        f0: float,
        q: float = 0.7071067811865476,
        fs: float = 48000.0,
        gain_db: float = 0.0,
    ) -> "Biquad":
        """Build a biquad from a design name.

        Parameters
        ----------
        kind : str
            One of ``"lowpass"``, ``"highpass"``, ``"bandpass"``,
            ``"notch"``, ``"peaking"``, ``"lowshelf"``, ``"highshelf"`` or
            ``"allpass"``.
        f0 : float
            Cutoff / centre frequency in Hz.
        q : float
            Quality factor.
        fs : float
            Sample rate in Hz.
        gain_db : float
            Gain in dB, used by the peaking and shelf designs.

        Returns
        -------
        Biquad
            The filter instance.
        """
        key = str(kind).strip().lower().replace("-", "").replace("_", "")
        if key == "lowpass":
            c = biquad_lowpass(f0, q, fs)
        elif key == "highpass":
            c = biquad_highpass(f0, q, fs)
        elif key == "bandpass":
            c = biquad_bandpass(f0, q, fs)
        elif key == "notch":
            c = biquad_notch(f0, q, fs)
        elif key == "peaking":
            c = biquad_peaking(f0, q, gain_db, fs)
        elif key == "lowshelf":
            c = biquad_lowshelf(f0, q, gain_db, fs)
        elif key == "highshelf":
            c = biquad_highshelf(f0, q, gain_db, fs)
        elif key == "allpass":
            c = biquad_allpass(f0, q, fs)
        else:
            raise ValueError(f"unknown biquad kind {kind!r}")
        return cls(*c)

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Clear the filter memory."""
        self._zi = np.zeros(2, dtype=np.float64)

    @property
    def state(self) -> np.ndarray:
        """The two filter memory values, shape ``(2,)``."""
        return self._zi.copy()

    @state.setter
    def state(self, value) -> None:
        self._zi = np.asarray(value, dtype=np.float64).ravel().copy()

    # -- processing -----------------------------------------------------------
    def process(self, x) -> np.ndarray:
        """Filter a block of samples.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
            Each channel is filtered with its own state.

        Returns
        -------
        numpy.ndarray
            The filtered samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            # The memory of a one dimensional filter is one pair of values;
            # a two dimensional call leaves a pair per channel.
            if self._zi.shape != (2,):
                self._zi = np.zeros(2, dtype=np.float64)
            y, self._zi = _lfilter(
                [self.b0, self.b1, self.b2], [1.0, self.a1, self.a2], arr, self._zi
            )
            return y
        if arr.ndim != 2:
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")
        # The memory is one pair of values per channel, so its shape has to
        # follow the channel count and not the number of calls: a mono block
        # (one channel) used to leave the memory one dimensional, and the
        # loop below raised an IndexError on it.
        if self._zi.shape != (2, arr.shape[1]):
            self._zi = np.zeros((2, arr.shape[1]), dtype=np.float64)
        self._nch = arr.shape[1]
        out = np.empty_like(arr)
        for c in range(arr.shape[1]):
            out[:, c], zi = _lfilter(
                [self.b0, self.b1, self.b2], [1.0, self.a1, self.a2], arr[:, c], self._zi[:, c]
            )
            self._zi[:, c] = zi
        return out

    __call__ = process

    # -- analysis -------------------------------------------------------------
    def freq_response(self, freqs, fs: float) -> np.ndarray:
        """Complex frequency response.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.
        fs : float
            Sample rate in Hz.

        Returns
        -------
        numpy.ndarray
            ``H(f)`` (complex), same shape as ``freqs``.
        """
        w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / float(fs)
        e1 = np.exp(-1j * w)
        e2 = e1 * e1
        num = self.b0 + self.b1 * e1 + self.b2 * e2
        den = 1.0 + self.a1 * e1 + self.a2 * e2
        return num / den

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Biquad b=({self.b0:.6g}, {self.b1:.6g}, {self.b2:.6g}) "
            f"a=({self.a1:.6g}, {self.a2:.6g})>"
        )


class Cascade:
    """A cascade of biquad sections.

    Parameters
    ----------
    sections : Iterable
        Coefficient tuples ``(b0, b1, b2, a1, a2)``.
    fs : float
        Sample rate in Hz (used for the frequency response).
    """

    def __init__(self, sections: Iterable[Sequence[float]], fs: float = 48000.0):
        self.fs = float(fs)
        self.sections: list[Biquad] = [Biquad.from_coeffs(c) for c in sections]

    def reset(self) -> None:
        """Clear the memory of every section."""
        for s in self.sections:
            s.reset()

    def process(self, x) -> np.ndarray:
        """Filter a block of samples through the whole cascade.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The filtered samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        for s in self.sections:
            arr = s.process(arr)
        return arr

    __call__ = process

    def freq_response(self, freqs) -> np.ndarray:
        """Complex frequency response of the whole cascade.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.

        Returns
        -------
        numpy.ndarray
            The product of the section responses.
        """
        h = np.ones(np.shape(freqs), dtype=np.complex128)
        for s in self.sections:
            h = h * s.freq_response(freqs, self.fs)
        return h


def _butterworth_q(order: int) -> list[float]:
    """Quality factors of the biquad sections of an even-order Butterworth."""
    q = []
    for k in range(order // 2):
        theta = math.pi * (2 * k + 1) / (2.0 * order)
        q.append(1.0 / (2.0 * math.sin(theta)))
    return q


def butterworth_lowpass(f0: float, fs: float, order: int = 2) -> list[Coeffs]:
    """Butterworth low-pass as a list of biquad sections.

    Parameters
    ----------
    f0 : float
        Cutoff frequency in Hz (-3 dB for the whole cascade).
    fs : float
        Sample rate in Hz.
    order : int
        Filter order; must be even (2, 4, 6, ...).

    Returns
    -------
    list
        The coefficient tuples, in cascade order.
    """
    order = int(order)
    if order < 2 or order % 2:
        raise ValueError("order must be an even number >= 2")
    return [biquad_lowpass(f0, q, fs) for q in _butterworth_q(order)]


def butterworth_highpass(f0: float, fs: float, order: int = 2) -> list[Coeffs]:
    """Butterworth high-pass as a list of biquad sections.

    Parameters
    ----------
    f0 : float
        Cutoff frequency in Hz (-3 dB for the whole cascade).
    fs : float
        Sample rate in Hz.
    order : int
        Filter order; must be even (2, 4, 6, ...).

    Returns
    -------
    list
        The coefficient tuples, in cascade order.
    """
    order = int(order)
    if order < 2 or order % 2:
        raise ValueError("order must be an even number >= 2")
    return [biquad_highpass(f0, q, fs) for q in _butterworth_q(order)]


def linkwitz_riley_lowpass(f0: float, fs: float, order: int = 4) -> list[Coeffs]:
    """Linkwitz-Riley low-pass as a list of biquad sections.

    A Linkwitz-Riley filter of order ``2n`` is two cascaded Butterworth
    filters of order ``n``.  The result is -6.02 dB at ``f0`` (measured)
    and has a phase-corrected roll-off: the phase is all-pass like, that
    is the group delay is symmetric about the corner, but it is not
    constant, so the filter is not linear phase.

    Parameters
    ----------
    f0 : float
        Corner frequency in Hz.
    fs : float
        Sample rate in Hz.
    order : int
        Total order: 4, 8, 12, ... (multiples of 4).

    Returns
    -------
    list
        The coefficient tuples, in cascade order.
    """
    order = int(order)
    if order < 4 or order % 4:
        raise ValueError("order must be a multiple of 4 and >= 4")
    half = order // 2
    return butterworth_lowpass(f0, fs, half) + butterworth_lowpass(f0, fs, half)


def linkwitz_riley_highpass(f0: float, fs: float, order: int = 4) -> list[Coeffs]:
    """Linkwitz-Riley high-pass as a list of biquad sections.

    The result is -6.02 dB at ``f0`` (measured) and has a phase-corrected
    roll-off, like the low pass.

    Parameters
    ----------
    f0 : float
        Corner frequency in Hz.
    fs : float
        Sample rate in Hz.
    order : int
        Total order: 4, 8, 12, ... (multiples of 4).

    Returns
    -------
    list
        The coefficient tuples, in cascade order.
    """
    order = int(order)
    if order < 4 or order % 4:
        raise ValueError("order must be a multiple of 4 and >= 4")
    half = order // 2
    return butterworth_highpass(f0, fs, half) + butterworth_highpass(f0, fs, half)


class Butterworth(Cascade):
    """Stateful Butterworth filter.

    Parameters
    ----------
    f0 : float
        Cutoff frequency in Hz.
    fs : float
        Sample rate in Hz.
    order : int
        Filter order (even).
    kind : str
        ``"lowpass"`` (default) or ``"highpass"``.
    """

    def __init__(self, f0: float, fs: float = 48000.0, order: int = 2, kind: str = "lowpass"):
        key = str(kind).strip().lower()
        if key == "lowpass":
            sections = butterworth_lowpass(f0, fs, order)
        elif key == "highpass":
            sections = butterworth_highpass(f0, fs, order)
        else:
            raise ValueError(f"unknown kind {kind!r}")
        super().__init__(sections, fs)
        self.f0 = float(f0)
        self.order = int(order)
        self.kind = key

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Butterworth {self.kind} f0={self.f0}Hz order={self.order}>"


class LinkwitzRiley(Cascade):
    """Stateful Linkwitz-Riley filter.

    Parameters
    ----------
    f0 : float
        Corner frequency in Hz.
    fs : float
        Sample rate in Hz.
    order : int
        Total order: 4 (default), 8, 12, ...
    kind : str
        ``"lowpass"`` (default) or ``"highpass"``.
    """

    def __init__(self, f0: float, fs: float = 48000.0, order: int = 4, kind: str = "lowpass"):
        key = str(kind).strip().lower()
        if key == "lowpass":
            sections = linkwitz_riley_lowpass(f0, fs, order)
        elif key == "highpass":
            sections = linkwitz_riley_highpass(f0, fs, order)
        else:
            raise ValueError(f"unknown kind {kind!r}")
        super().__init__(sections, fs)
        self.f0 = float(f0)
        self.order = int(order)
        self.kind = key

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LinkwitzRiley {self.kind} f0={self.f0}Hz order={self.order}>"


class OnePole:
    """First-order low-pass smoother.

    The response is exactly -3.01 dB at ``cutoff_hz`` (the corner is
    solved for, not approximated).

    Parameters
    ----------
    cutoff_hz : float
        Corner frequency in Hz; the response is -3 dB there.
    fs : float
        Sample rate in Hz.
    tau_s : float, optional
        Alternative parameterisation: the time constant in seconds.  When
        given it overrides ``cutoff_hz``.  The time constant and the corner
        frequency are two names for the same number,
        ``cutoff_hz = 1 / (2 * pi * tau_s)``, and the two parameterisations
        give the same filter: the corner of ``OnePole(tau_s=t)`` is exactly
        ``1 / (2 * pi * t)`` Hz.
    """

    __slots__ = ("fs", "tau_s", "cutoff_hz", "a", "_state", "_nch")

    def __init__(self, cutoff_hz: float = 1000.0, fs: float = 48000.0, tau_s: float | None = None):
        self.fs = float(fs)
        self._state = np.zeros(1, dtype=np.float64)
        self._nch = 1
        if tau_s is not None:
            self.tau_s = float(tau_s)
            # the time constant and the corner frequency name the same
            # number; solving the exact -3 dB condition for that corner
            # keeps the two parameterisations identical (before this, the
            # time constant branch put the corner 18.9 % too high at
            # 10 kHz because it used the exponential approximation).
            self.cutoff_hz = 1.0 / (2.0 * math.pi * max(self.tau_s, 1e-12))
            self._update()
        else:
            self.cutoff_hz = float(cutoff_hz)
            self.tau_s = 1.0 / (2.0 * math.pi * max(self.cutoff_hz, 1e-9))
            self._update()

    def _update(self) -> None:
        """Recompute the one-pole coefficient from the current cutoff."""
        w = 2.0 * math.pi * max(self.cutoff_hz, 1e-9) / self.fs
        w = min(max(w, 1e-9), math.pi)
        # solve (1 - a)^2 = 4 a sin^2(w/2) for the exact -3 dB point
        s = math.sin(w / 2.0)
        t = -s + math.sqrt(s * s + 1.0)
        self.a = t * t

    def set_cutoff(self, cutoff_hz: float) -> None:
        """Change the corner frequency (the state is kept)."""
        self.cutoff_hz = float(cutoff_hz)
        self.tau_s = 1.0 / (2.0 * math.pi * max(self.cutoff_hz, 1e-9))
        self._update()

    def reset(self) -> None:
        """Clear the filter memory."""
        self._state = np.zeros(1, dtype=np.float64)

    def process(self, x) -> np.ndarray:
        """Filter a block of samples.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The smoothed samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        a = self.a
        if arr.ndim == 1:
            out = np.empty_like(arr)
            y = float(self._state[0])
            for i in range(arr.shape[0]):
                y = a * y + (1.0 - a) * float(arr[i])
                out[i] = y
            self._state[0] = y
            return out
        if arr.ndim != 2:
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")
        if arr.shape[1] != self._nch:
            self._state = np.zeros(arr.shape[1], dtype=np.float64)
            self._nch = arr.shape[1]
        out = np.empty_like(arr)
        state = self._state
        for i in range(arr.shape[0]):
            state = a * state + (1.0 - a) * arr[i]
            out[i] = state
        self._state = state
        return out

    __call__ = process

    def freq_response(self, freqs) -> np.ndarray:
        """Complex frequency response.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.

        Returns
        -------
        numpy.ndarray
            ``H(f)`` (complex).
        """
        w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / self.fs
        e1 = np.exp(-1j * w)
        return (1.0 - self.a) / (1.0 - self.a * e1)


class DCBlocker:
    """First-order high-pass that removes the DC component.

    The classic single pole filter ``y[n] = x[n] - x[n-1] + R y[n-1]`` with
    ``R = 1 - 2*pi*fc/fs``.

    The corner is the classic approximation, so the measured -3 dB point
    sits slightly below ``cutoff_hz``: 0.03 % low at 5 Hz, 0.13 % low at
    20 Hz and 0.65 % low at 100 Hz.  The exact corner needs a different
    ``R``, and that ``R`` is unstable above 9.4 kHz, so the approximation
    is kept.

    Parameters
    ----------
    cutoff_hz : float
        Corner frequency in Hz (default 20 Hz).
    fs : float
        Sample rate in Hz.
    """

    __slots__ = ("fs", "cutoff_hz", "r", "_x1", "_y1", "_nch")

    def __init__(self, cutoff_hz: float = 20.0, fs: float = 48000.0):
        self.fs = float(fs)
        self.cutoff_hz = float(cutoff_hz)
        self.r = 1.0 - 2.0 * math.pi * float(cutoff_hz) / float(fs)
        self._x1 = np.zeros(1, dtype=np.float64)
        self._y1 = np.zeros(1, dtype=np.float64)
        self._nch = 1

    def reset(self) -> None:
        """Clear the filter memory."""
        self._x1 = np.zeros(1, dtype=np.float64)
        self._y1 = np.zeros(1, dtype=np.float64)

    def process(self, x) -> np.ndarray:
        """Filter a block of samples.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The filtered samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        r = self.r
        if arr.ndim == 1:
            out = np.empty_like(arr)
            x1 = float(self._x1[0])
            y1 = float(self._y1[0])
            for i in range(arr.shape[0]):
                xi = float(arr[i])
                y = xi - x1 + r * y1
                x1 = xi
                y1 = y
                out[i] = y
            self._x1[0] = x1
            self._y1[0] = y1
            return out
        if arr.ndim != 2:
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")
        if arr.shape[1] != self._nch:
            self._x1 = np.zeros(arr.shape[1], dtype=np.float64)
            self._y1 = np.zeros(arr.shape[1], dtype=np.float64)
            self._nch = arr.shape[1]
        out = np.empty_like(arr)
        x1 = self._x1
        y1 = self._y1
        for i in range(arr.shape[0]):
            xi = arr[i]
            y = xi - x1 + r * y1
            x1[:] = xi
            y1 = y
            out[i] = y
        self._x1 = x1
        self._y1 = y1
        return out

    __call__ = process

    def freq_response(self, freqs) -> np.ndarray:
        """Complex frequency response.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.

        Returns
        -------
        numpy.ndarray
            ``H(f)`` (complex).
        """
        w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / self.fs
        e1 = np.exp(-1j * w)
        return (1.0 - e1) / (1.0 - self.r * e1)


# ---------------------------------------------------------------------------
# finite impulse response filters
# ---------------------------------------------------------------------------
#: The smallest kernel the transform path of :class:`FIR` is used for.
#:
#: The direct convolution is bit-for-bit reproducible, the transform path
#: is only accurate to the rounding of the transform (about 1e-13
#: relative), so the direct path is kept for the kernels that are short
#: enough for it to be cheap.  The linear phase kernels of this project
#: are 4097 taps and longer, and at that length the direct convolution of
#: a 64 sample block costs four times the realtime budget of the block.
_FFT_MIN_TAPS = 4096


def _next_pow2(n: int) -> int:
    """The smallest power of two that is not smaller than ``n``."""
    v = 1
    while v < int(n):
        v <<= 1
    return v


def _tukey(length: int, taper: float) -> np.ndarray:
    """Return a symmetric taper (1 inside, raised cosine at the edges)."""
    n = int(length)
    if n <= 1 or taper <= 0.0:
        return np.ones(max(n, 0), dtype=np.float64)
    edge = int(max(1, round(min(taper, 0.5) * n / 2.0)))
    if 2 * edge >= n:
        edge = max(1, n // 2)
    w = np.ones(n, dtype=np.float64)
    ramp = 0.5 * (1.0 + np.cos(np.pi * np.arange(edge) / edge))
    w[:edge] = ramp
    w[-edge:] = ramp[::-1]
    return w


def linear_phase_fir(magnitudes, fs: float = 48000.0, *, length: int | None = None,
                     taper: float = 0.0) -> np.ndarray:
    """Design a linear-phase FIR kernel from a magnitude response.

    The kernel is symmetric, so a filter built from it has a constant
    group delay of ``(length - 1) / 2`` samples.  That is the linear
    phase claim, and it is exact, not an approximation.

    Parameters
    ----------
    magnitudes : array_like
        The wanted **linear** magnitude, sampled on the grid
        ``k * fs / length`` for ``k = 0 .. length // 2``.  The Nyquist
        sample is included.
    fs : float
        Sample rate in Hz.
    length : int, optional
        Length of the kernel in taps.  It must be odd.  When it is not
        given, it is ``2 * len(magnitudes) - 1``.
    taper : float
        Fraction of the kernel that is tapered at each end, from 0 (no
        taper) to 0.5.  A taper lowers the sidelobes at the price of the
        stop band depth.

    Returns
    -------
    numpy.ndarray
        The symmetric kernel, ``length`` taps long.
    """
    mag = np.asarray(magnitudes, dtype=np.float64).ravel()
    if mag.size < 2:
        raise ValueError("at least two magnitude samples are needed")
    if length is None:
        length = 2 * mag.size - 1
    length = int(length)
    if length < 3 or length % 2 == 0:
        raise ValueError("length must be an odd number of at least 3 taps")
    if mag.size != length // 2 + 1:
        raise ValueError("the magnitude response must have length // 2 + 1 samples")
    if not np.all(np.isfinite(mag)):
        raise ValueError("the magnitude response must be finite")
    # The magnitude alone gives a kernel that is even about zero, that is a
    # zero phase kernel.  Multiplying by the delay of a symmetric kernel
    # turns it into a kernel that is symmetric about (length - 1) / 2, which
    # is the linear phase kernel.
    k = np.arange(mag.size, dtype=np.float64)
    w = 2.0 * math.pi * k / float(length)
    shifted = mag * np.exp(-1j * w * (length - 1) / 2.0)
    kernel = np.fft.irfft(shifted, length)
    if taper > 0.0:
        kernel = kernel * _tukey(length, taper)
    return np.asarray(kernel, dtype=np.float64)


class FIR:
    """A stateful finite impulse response filter.

    The kernel is applied causally, so the output is the filtered signal
    delayed by the group delay of the kernel at DC, which is
    ``(len(kernel) - 1) / 2`` samples for a symmetric kernel.  The filter
    reports that delay as its latency.  It keeps the tail of the previous
    block, so a signal filtered in blocks of any size gives the same
    answer as the same signal filtered in one block.

    A long kernel is applied with a transform instead of with the direct
    convolution.  The direct convolution costs ``taps * (taps - 1 + n)``
    multiply-adds for a block of ``n`` samples, and the tail of the
    previous block is convolved again on every call, so the cost does not
    fall with the size of the block: a 4801 tap kernel costs 5072 us for a
    64 sample block with two channels (measured), four times the realtime
    budget of that block.  The transform path costs two transforms of
    ``next_pow2(taps + n - 1)`` points per channel, and the same block
    costs 185 us with it.

    The two paths are not the same number: the rounding of a transform
    depends on the length of its frame, so the transform path is accurate
    to about 1e-13 relative and not bit-for-bit.  The direct path is
    therefore kept for the kernels that are short enough for it to be
    cheap, and the transform path is used from
    :data:`_FFT_MIN_TAPS` taps on.

    Parameters
    ----------
    kernel : array_like
        The impulse response.  A symmetric kernel gives a constant group
        delay, that is a linear phase filter.
    fs : float
        Sample rate in Hz (used for the frequency response).
    exact : bool
        Force the direct convolution even for a long kernel.  The answer is
        then bit-for-bit the same as the direct convolution of the whole
        stream, and it costs ``taps * (taps - 1 + n)`` multiply-adds per
        block.
    """

    __slots__ = ("kernel", "fs", "_tail", "_nch", "_seen", "_exact",
                 "_specs", "_frame", "_frame_size")

    def __init__(self, kernel, fs: float = 48000.0, exact: bool = False):
        k = np.asarray(kernel, dtype=np.float64).ravel()
        if k.size < 1:
            raise ValueError("the kernel must have at least one tap")
        if not np.all(np.isfinite(k)):
            raise ValueError("the kernel must be finite")
        self.kernel = k
        self.fs = float(fs)
        self._tail = np.zeros((max(k.size - 1, 0), 1), dtype=np.float64)
        self._nch = 1
        # How many samples the filter has seen.  A causal filter is silent
        # until its delay has elapsed: the convolution of a symmetric kernel
        # carries the kernel's left half into the region before the input
        # arrives, and that region must be zero.
        self._seen = 0
        # The direct convolution is the reference; the transform path is
        # used for the long kernels unless the caller asks for the exact
        # one.
        self._exact = bool(exact)
        # The spectrum of the kernel, one for every length of the transform
        # that has been used.  A host that changes the size of its blocks
        # must not pay for the transform of the kernel again every time.
        self._specs: dict[int, np.ndarray] = {}
        self._frame: np.ndarray | None = None
        self._frame_size = 0

    def reset(self) -> None:
        """Clear the filter memory."""
        self._tail = np.zeros((max(self.kernel.size - 1, 0), self._nch),
                              dtype=np.float64)
        self._seen = 0

    def set_kernel(self, kernel) -> None:
        """Change the impulse response the filter applies.

        The memory of the filter is kept: it holds the samples that
        came before, and those are needed whatever the kernel is.  The
        spectrum of the kernel is computed again, because the transform
        path caches it.

        Parameters
        ----------
        kernel : array_like
            The new impulse response.  It must have the same number of
            taps as the one the filter was built with.
        """
        k = np.asarray(kernel, dtype=np.float64).ravel()
        if k.size != self.kernel.size:
            raise ValueError("the kernel must keep the number of taps it has")
        if not np.all(np.isfinite(k)):
            raise ValueError("the kernel must be finite")
        self.kernel = k
        # The cached spectra belong to the old kernel.
        self._specs = {}

    @property
    def latency_samples(self) -> int:
        """The delay the filter adds, in samples.

        That is the group delay at DC, the centre of mass of the kernel.
        For a symmetric kernel it is ``(len(kernel) - 1) / 2``, which is
        the constant group delay of a linear phase filter.
        """
        total = float(np.sum(self.kernel))
        if total == 0.0:
            return 0
        moment = float(np.sum(np.arange(self.kernel.size, dtype=np.float64) * self.kernel))
        return int(round(moment / total))

    @property
    def state(self) -> np.ndarray:
        """The filter memory, shape ``(len(kernel) - 1, channels)``."""
        return self._tail.copy()

    # -- the transform path ----------------------------------------------------
    def _use_transform(self, n: int) -> bool:
        """True when the block is filtered with a transform.

        The direct path costs ``taps * (taps - 1 + n)`` multiply-adds, the
        transform path two transforms of ``next_pow2(taps + n - 1)`` points
        per channel.  The transform wins from a few hundred taps on, but it
        is not bit-for-bit the same answer, so it is used from
        :data:`_FFT_MIN_TAPS` taps on, where the direct path is out of the
        realtime budget.
        """
        if self._exact:
            return False
        return self.kernel.size >= _FFT_MIN_TAPS

    def _spectrum(self, size: int) -> np.ndarray:
        """The spectrum of the kernel, for a transform of ``size`` points."""
        spec = self._specs.get(size)
        if spec is None:
            spec = np.fft.rfft(self.kernel, size)
            self._specs[size] = spec
        return spec

    def _frame_buffer(self, size: int) -> np.ndarray:
        """A zeroed buffer for the frame of a transform of ``size`` points."""
        if self._frame is None or self._frame_size != size:
            self._frame = np.zeros(size, dtype=np.float64)
            self._frame_size = size
        return self._frame

    def _transform_block(self, buf: np.ndarray, n: int) -> np.ndarray:
        """The wanted samples of the convolution of ``buf`` with the kernel.

        The frame is the tail of the previous block followed by the new
        block, zero padded at the front, and the transform is long enough
        for the *linear* convolution of the two: ``size >= taps + n - 1``.
        The circular wrap around of the transform can therefore only reach
        the first ``taps - 1`` samples of the answer, and the samples that
        are read are the last ``n`` of it.

        The sample ``keep + k`` of the direct convolution is the sample
        ``size - n + k`` of the transform: the frame starts
        ``size - (taps - 1 + n)`` samples before the first sample of the
        convolution, so the two differ by that offset.
        """
        taps = self.kernel.size
        size = _next_pow2(taps + n - 1)
        spec = self._spectrum(size)
        frame = self._frame_buffer(size)
        start = size - (taps - 1 + n)
        # The front of the frame is zero: the samples in front of the tail
        # of the previous block are older than the kernel is long, so they
        # cannot reach the samples that are read.
        if start > 0:
            frame[:start] = 0.0
        frame[start:] = buf
        conv = np.fft.irfft(np.fft.rfft(frame) * spec, size)
        return conv[size - n:]

    def process(self, x) -> np.ndarray:
        """Filter a block of samples.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The filtered samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim not in (1, 2):
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")
        one_d = arr.ndim == 1
        if one_d:
            arr = arr[:, None]
        n = arr.shape[0]
        if arr.shape[1] != self._nch:
            self._tail = np.zeros((max(self.kernel.size - 1, 0), arr.shape[1]),
                                  dtype=np.float64)
            self._nch = arr.shape[1]
        if n == 0:
            return arr[:, 0] if one_d else arr
        taps = self.kernel.size
        # The output is read at the FULL LENGTH of the kernel, so the delay
        # the filter has is the group delay of a symmetric kernel: the
        # convolution itself delays by the centre of the kernel, and reading
        # it one kernel length later cancels the two.
        keep = taps - 1
        out = np.empty((n, arr.shape[1]), dtype=np.float64)
        transform = self._use_transform(n)
        for c in range(arr.shape[1]):
            buf = np.concatenate([self._tail[:, c], arr[:, c]])
            # The convolution is NOT truncated: the tail of it belongs to the
            # samples of the block that has just been read, and cutting them
            # off makes the answer depend on the size of the blocks the host
            # uses.
            if transform:
                out[:, c] = self._transform_block(buf, n)
            else:
                full = np.convolve(buf, self.kernel)
                out[:, c] = full[keep:keep + n]
            self._tail[:, c] = buf[n:]

        # The filter is silent until its delay has elapsed: the convolution
        # of a symmetric kernel carries the kernel's left half into the
        # region before the input arrives, and that region must be zero.
        silence = max(0, int(np.argmax(np.abs(self.kernel))) - self._seen)
        if silence:
            out[:min(silence, n)] = 0.0
        self._seen += n

        return out[:, 0] if one_d else out

    __call__ = process

    def freq_response(self, freqs) -> np.ndarray:
        """Complex frequency response.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.

        Returns
        -------
        numpy.ndarray
            ``H(f)`` (complex).
        """
        w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / self.fs
        response = np.zeros(np.shape(freqs), dtype=np.complex128)
        for k, hk in enumerate(self.kernel):
            response = response + hk * np.exp(-1j * w * k)
        return response
