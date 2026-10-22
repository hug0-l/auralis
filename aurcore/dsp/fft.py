"""
aurcore.dsp.fft — real FFT helpers.

The public API is a thin, well documented wrapper around ``numpy.fft`` with an
extra radix-2 complex transform for power-of-two sizes.

Conventions
-----------
* The forward transform is **unnormalised** (the same convention as
  ``numpy.fft``): ``rfft`` of a unit sine of amplitude ``A`` has magnitude
  ``A * n_fft / 2`` on the positive bin.
* The inverse transform **is normalised** by ``n_fft``, so
  ``irfft(rfft(x), n_fft) == x`` to floating point precision.  Pass the
  length explicitly: the one-sided spectrum of an **odd** length signal does
  not determine the length, so the default of :func:`irfft` has to guess
  (see :func:`irfft`).
* Only the non-negative frequency half is produced by :func:`rfft`:
  ``n_fft // 2 + 1`` bins, DC first, Nyquist last.
* :func:`magnitude`, :func:`magnitude_db` and :func:`phase` are **total**:
  they always return a finite value, whatever the spectrum holds.  A
  non-finite magnitude is reported as the floor and a non-finite phase as
  zero, so a single bad sample cannot poison a spectrogram.

Block edges and padding
------------------------
A transform of a block of samples **assumes that the signal continues as
zeros outside the block**.  The block is cut out of a longer signal and the
cut is a step discontinuity, so the spectrum of a block whose first and last
samples do not match its neighbours carries that step in every bin.

Measured on a 1024-sample block with a tone at bin 64 (the numbers come from
``qa/fft``):

    block content                       worst bin other than the tone
    ---------------------------------  ------------------------------------
    periodic (the tone fits the block)  -289.6 dB below the tone
    1 sample gated at each end           -48.5 dB below the tone
    64 samples gated at each end         -17.1 dB below the tone
    a ramp (a step of 1.0 at the end)      0.0 dB below the tone

A block that is *periodic* over its own length is transformed exactly; a
block with a discontinuity at its edge is not.  Two conventions make this
manageable: a window that is zero at its edges (Hann and friends) removes
the step, and the frames of the short-time transform are centred, so the
step falls on the zero padding rather than on the signal.

Two backends are available:

* ``"numpy"`` (default) uses ``numpy.fft``; it handles any length.
* ``"radix2"`` uses the iterative radix-2 Cooley-Tukey transform implemented
  in :func:`fft_pow2`; it only accepts power-of-two lengths.  It exists for
  reference, for cross-checking and for environments where a hand written
  transform is wanted; it is not faster than the compiled library (measured:
  108 us against 4.1 us for a 64-point transform, i.e. 26 times slower).
"""
from __future__ import annotations

import math

import numpy as np

from .util import is_pow2, next_pow2

__all__ = [
    "fft_size",
    "rfft",
    "irfft",
    "fft_round_trip",
    "magnitude_db",
    "phase",
    "magnitude",
    "to_complex",
    "fft_pow2",
    "ifft_pow2",
]


def fft_size(n: int) -> int:
    """Smallest FFT size that can hold ``n`` samples.

    Parameters
    ----------
    n : int
        Required number of samples.

    Returns
    -------
    int
        A power of two greater than or equal to ``n``.
    """
    return next_pow2(n)


# ---------------------------------------------------------------------------
# radix-2 complex FFT (iterative Cooley-Tukey, in-place butterflies)
# ---------------------------------------------------------------------------
def _bit_reverse(values: np.ndarray) -> None:
    """Reorder ``values`` (complex) into bit-reversed index order (in place)."""
    n = values.shape[0]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            values[i], values[j] = values[j], values[i]


def fft_pow2(x) -> np.ndarray:
    """Iterative radix-2 complex FFT.

    Parameters
    ----------
    x : array_like
        Complex or real input, shape ``(n,)``.  ``n`` must be a power of two.

    Returns
    -------
    numpy.ndarray
        The unnormalised forward transform, shape ``(n,)``, complex128.

    Raises
    ------
    ValueError
        If the length is not a power of two.
    """
    values = np.asarray(x, dtype=np.complex128).ravel()
    n = values.shape[0]
    if n == 0:
        return values.copy()
    if not is_pow2(n):
        raise ValueError(f"radix-2 FFT needs a power-of-two length, got {n}")
    if n == 1:
        return values.copy()

    out = values.astype(np.complex128, copy=True)
    _bit_reverse(out)

    length = 2
    while length <= n:
        theta = -2.0 * np.pi / length
        wl = np.cos(theta) + 1j * np.sin(theta)
        w = np.complex128(1.0)
        half = length >> 1
        for i in range(0, n, length):
            w = np.complex128(1.0)
            for j in range(i, i + half):
                u = out[j]
                v = out[j + half] * w
                out[j] = u + v
                out[j + half] = u - v
                w = w * wl
        length <<= 1
    return out


def ifft_pow2(x) -> np.ndarray:
    """Inverse of :func:`fft_pow2`, normalised by the transform length.

    Parameters
    ----------
    x : array_like
        Complex spectrum, shape ``(n,)`` with ``n`` a power of two.

    Returns
    -------
    numpy.ndarray
        The time-domain signal, shape ``(n,)``, complex128.
    """
    values = np.asarray(x, dtype=np.complex128).ravel()
    n = values.shape[0]
    if n == 0:
        return values.copy()
    conj = np.conjugate(values)
    return np.conjugate(fft_pow2(conj)) / float(n)


# ---------------------------------------------------------------------------
# real transforms
# ---------------------------------------------------------------------------
def rfft(x, n: int | None = None, backend: str = "numpy", axis: int = -1) -> np.ndarray:
    """Real FFT.

    The transform is taken over ``axis``.  A block of samples is transformed
    **as if the signal continued as zeros outside the block** — see the
    module documentation, "Block edges and padding".

    Parameters
    ----------
    x : array_like
        Real input, shape ``(n,)`` or ``(m, n)`` (the last axis is
        transformed by default).
    n : int, optional
        Transform length.  ``None`` (default) uses the length of ``axis``; a
        value shorter than the input truncates it, a longer one zero-pads it.
    backend : str
        ``"numpy"`` (default) or ``"radix2"`` (power-of-two lengths only).
    axis : int
        Axis to transform (default -1, the last one).

    Returns
    -------
    numpy.ndarray
        Complex spectrum, shape ``(..., n // 2 + 1)`` along ``axis``.

    Raises
    ------
    ValueError
        If the input is complex, if ``n`` is smaller than 1, or if
        ``backend="radix2"`` and the length is not a power of two.
    """
    arr = np.asarray(x)
    if np.iscomplexobj(arr):
        raise ValueError("rfft expects a real input")
    if arr.ndim == 0:
        raise ValueError("rfft needs an input with at least one dimension")
    arr = arr.astype(np.float64, copy=False)

    if n is None:
        n = arr.shape[axis]
    n = int(n)
    if n <= 0:
        raise ValueError("n must be >= 1")

    if backend == "radix2":
        if not is_pow2(n):
            raise ValueError("the radix2 backend needs a power-of-two length")
        # The hand written transform works on the last axis, so the axis
        # that is asked for is moved there and back afterwards.
        moved = np.moveaxis(arr, axis, -1)
        work = np.zeros(moved.shape[:-1] + (n,), dtype=np.float64)
        take = min(n, moved.shape[-1])
        work[..., :take] = moved[..., :take]
        flat = work.reshape(-1, n)
        spec = np.empty((flat.shape[0], n), dtype=np.complex128)
        for i in range(flat.shape[0]):
            spec[i] = fft_pow2(flat[i])
        spec = spec.reshape(work.shape[:-1] + (n,))
        spec = spec[..., : n // 2 + 1]
        return np.moveaxis(spec, -1, axis)
    if backend != "numpy":
        raise ValueError(f"unknown backend {backend!r}")

    return np.fft.rfft(arr, n=n, axis=axis)


def _infer_length(spec: np.ndarray, axis: int) -> int:
    """Infer the length of the real signal a one-sided spectrum describes.

    A one-sided spectrum of ``k`` bins describes a real signal of either
    ``2 * k - 2`` samples (an **even** length, where the top bin is the
    Nyquist bin and is therefore real) or ``2 * k - 1`` samples (an **odd**
    length, where the top bin is an ordinary bin and is therefore usually
    complex).  The two cannot be told apart from a top bin that happens to
    be real, so the rule is:

    * the top bin is complex -> the length is odd, ``2 * k - 1``; the even
      reading would throw the imaginary part of the top bin away.
    * the top bin is real -> the length is even, ``2 * k - 2``, unless that
      is not a valid length, in which case it is odd, ``2 * k - 1``.
    """
    n_bins = int(spec.shape[axis])
    if n_bins <= 0:
        raise ValueError("the spectrum has no bins")
    even = 2 * (n_bins - 1)
    odd = 2 * n_bins - 1
    top = np.take(spec, n_bins - 1, axis=axis)
    if np.all(np.isreal(top)):
        return even if even >= 1 else odd
    return odd


def irfft(X, n: int | None = None, axis: int = -1) -> np.ndarray:
    """Inverse real FFT.

    Parameters
    ----------
    X : array_like
        Complex half-spectrum, shape ``(..., n // 2 + 1)``.  The last axis is
        transformed by default.
    n : int, optional
        Length of the output signal.  ``None`` (default) infers it from the
        spectrum: a one-sided spectrum of ``k`` bins describes a signal of
        ``2 * k - 2`` samples (an even length, the top bin is the Nyquist
        bin) or of ``2 * k - 1`` samples (an odd length, the top bin is an
        ordinary bin).  The inference keeps the reading that loses no
        information: the odd length when the top bin is complex, the even
        length when it is real.  Pass ``n`` explicitly when the output
        length has to be predictable, or when the signal is of an odd length
        *and* its top bin is exactly real.
    axis : int
        Axis to transform (default -1, the last one).

    Returns
    -------
    numpy.ndarray
        Real time-domain signal, shape ``(..., n)``.

    Raises
    ------
    ValueError
        If ``n`` is smaller than 1.
    """
    spec = np.asarray(X, dtype=np.complex128)
    if spec.ndim == 0:
        raise ValueError("irfft needs a spectrum with at least one dimension")
    if n is None:
        n = _infer_length(spec, axis)
    n = int(n)
    if n <= 0:
        raise ValueError("n must be >= 1")
    return np.fft.irfft(spec, n=n, axis=axis)


def fft_round_trip(
    x,
    n: int | None = None,
    backend: str = "numpy",
    axis: int = -1,
) -> np.ndarray:
    """Forward and inverse transform in one call.

    The pair is exact for **every** length, including the odd ones, because
    the length is carried from the forward transform to the inverse one
    instead of being guessed from the spectrum.

    Parameters
    ----------
    x : array_like
        Real input, shape ``(n,)`` or ``(m, n)``.
    n : int, optional
        Transform length.  ``None`` (default) uses the length of ``axis``.
    backend : str
        ``"numpy"`` (default) or ``"radix2"`` (power-of-two lengths only).
    axis : int
        Axis to transform (default -1, the last one).

    Returns
    -------
    numpy.ndarray
        The rebuilt signal, same shape as the input.
    """
    arr = np.asarray(x)
    length = arr.shape[axis] if n is None else int(n)
    spec = rfft(arr, n=length, backend=backend, axis=axis)
    return irfft(spec, n=length, axis=axis)


def _all_finite(arr: np.ndarray) -> bool:
    """True when every element of a **real** array is finite.

    The test is one reduction plus one scalar comparison, which is cheaper
    than ``isfinite(arr).all()`` on the small arrays these helpers work on.
    ``max`` propagates a NaN, so a single NaN is enough to make the
    reduction non-finite.
    """
    if arr.size == 0:
        return True
    return math.isfinite(float(arr.max()))


def magnitude(X, floor_db: float = -120.0) -> np.ndarray:
    """Magnitude of a complex spectrum.

    The magnitude is floored, so that it is never zero and never
    non-finite: a magnitude below the floor is lifted to it, and a
    non-finite magnitude (an infinity or a NaN in the spectrum) is reported
    as the floor as well.

    Parameters
    ----------
    X : array_like
        Complex spectrum.
    floor_db : float or None
        Magnitudes below this level (dB) are lifted to it before the
        logarithm, so the result is always finite.  ``None`` returns the
        raw magnitude, which is then not necessarily finite.

    Returns
    -------
    numpy.ndarray
        Magnitude in linear units, same shape as ``X``.
    """
    spec = np.asarray(X)
    mag = np.abs(spec)
    if floor_db is None:
        return mag
    floor = 10.0 ** (float(floor_db) / 20.0)
    mag = np.maximum(mag, floor)
    if not _all_finite(mag):
        # a NaN or an infinity in the spectrum is reported as the floor
        mag = np.where(np.isfinite(mag), mag, floor)
    return mag


def magnitude_db(X, floor_db: float = -120.0) -> np.ndarray:
    """Magnitude of a complex spectrum in dB.

    Magnitudes at or below the floor are reported as ``floor_db``, and a
    non-finite magnitude is reported as the floor as well, so the result is
    always finite.

    Parameters
    ----------
    X : array_like
        Complex spectrum.
    floor_db : float or None
        Floor in dB (default -120).  ``None`` applies no floor; the result
        is then ``-inf`` for a zero magnitude and ``nan`` for a NaN.

    Returns
    -------
    numpy.ndarray
        Magnitude in dB, same shape as ``X``.
    """
    mag = np.abs(np.asarray(X))
    if floor_db is None:
        return 20.0 * np.log10(mag)
    floor = 10.0 ** (float(floor_db) / 20.0)
    mag = np.maximum(mag, floor)
    if not _all_finite(mag):
        # a NaN or an infinity in the spectrum is reported as the floor
        mag = np.where(np.isfinite(mag), mag, floor)
    return 20.0 * np.log10(mag)


def phase(X) -> np.ndarray:
    """Phase of a complex spectrum.

    The phase of a zero magnitude is zero, and so is the phase of a
    non-finite spectrum, so the result is always finite.

    Parameters
    ----------
    X : array_like
        Complex spectrum.

    Returns
    -------
    numpy.ndarray
        Phase in radians, in ``[-pi, pi]``, same shape as ``X``.
    """
    spec = np.asarray(X, dtype=np.complex128)
    out = np.angle(spec)
    if not _all_finite(out):
        # the phase of a non-finite spectrum is zero, like the phase of silence
        out = np.where(np.isfinite(out), out, 0.0)
    return out


def to_complex(mag, phase_) -> np.ndarray:
    """Rebuild a complex spectrum from a magnitude and a phase.

    A non-finite magnitude or phase is treated as zero, so the result is
    always finite.

    Parameters
    ----------
    mag : array_like
        Magnitude in linear units.
    phase_ : array_like
        Phase in radians.

    Returns
    -------
    numpy.ndarray
        Complex spectrum ``mag * exp(1j * phase_)``.
    """
    m = np.asarray(mag, dtype=np.float64)
    p = np.asarray(phase_, dtype=np.float64)
    if not _all_finite(m):
        m = np.where(np.isfinite(m), m, 0.0)
    if not _all_finite(p):
        p = np.where(np.isfinite(p), p, 0.0)
    return m * np.exp(1j * p)
