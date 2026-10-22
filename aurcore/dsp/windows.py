"""
aurcore.dsp.windows — exact window functions and COLA helpers.

Every window is returned as a ``float64`` array of length ``n``.

Periodicity
-----------
All windows take a ``sym`` flag:

* ``sym=False`` (default) returns the **periodic** (DFT-even) window, i.e.
  the denominator of the cosine terms is ``n``.  This is the correct choice
  for STFT / overlap-add work because it makes the window exactly periodic
  with the analysis period, so no seam appears every ``n`` samples.
* ``sym=True`` returns the **symmetric** window (denominator ``n - 1``), which
  is the usual "one period of a continuous window" and is what most spectral
  editors display.

COLA
----
The constant-overlap-add condition is about the *sum* of the window over the
overlapping positions, ``sum_k w[p - k*hop]``.  :func:`colA` returns that
constant; the matching multiplicative normalisation factor is
:func:`cola_norm`.
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "hann",
    "hamming",
    "blackman",
    "blackman_harris",
    "kaiser",
    "gaussian",
    "nuttall",
    "rectangular",
    "sqrt_hann",
    "window",
    "window_names",
    "colA",
    "cola_sum",
    "cola_norm",
    "cola_ripple",
    "cola_product",
]

#: Names accepted by :func:`window`.
window_names: tuple[str, ...] = (
    "hann",
    "hamming",
    "blackman",
    "blackman-harris",
    "kaiser",
    "gaussian",
    "nuttall",
    "sqrt-hann",
    "rectangular",
)


def _index(n: int) -> np.ndarray:
    """Sample indices ``0 .. n-1`` as float64."""
    return np.arange(int(n), dtype=np.float64)


def _denominator(n: int, sym: bool) -> float:
    """Denominator of the cosine terms: ``n-1`` (symmetric) or ``n`` (periodic)."""
    n = int(n)
    if sym:
        return float(n - 1) if n > 1 else 1.0
    return float(n)


def hann(n: int, sym: bool = False) -> np.ndarray:
    """Hann (raised cosine) window.

    ``w[k] = 0.5 - 0.5 * cos(2*pi*k / D)`` with ``D = n-1`` (symmetric) or
    ``D = n`` (periodic, default).

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * _index(n) / d)


def hamming(n: int, sym: bool = False) -> np.ndarray:
    """Hamming window.

    ``w[k] = 0.54 - 0.46 * cos(2*pi*k / D)``.

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    return 0.54 - 0.46 * np.cos(2.0 * np.pi * _index(n) / d)


def blackman(n: int, sym: bool = False) -> np.ndarray:
    """Blackman window.

    ``w[k] = 0.42 - 0.5*cos(2*pi*k/D) + 0.08*cos(4*pi*k/D)``.

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    k = _index(n)
    return 0.42 - 0.5 * np.cos(2.0 * np.pi * k / d) + 0.08 * np.cos(4.0 * np.pi * k / d)


def blackman_harris(n: int, sym: bool = False) -> np.ndarray:
    """Four-term Blackman-Harris window.

    ``w[k] = a0 - a1*cos(2*pi*k/D) + a2*cos(4*pi*k/D) - a3*cos(6*pi*k/D)``
    with ``a = (0.35875, 0.48829, 0.14128, 0.01168)`` (about -92 dB sidelobes).

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    k = _index(n)
    return (
        0.35875
        - 0.48829 * np.cos(2.0 * np.pi * k / d)
        + 0.14128 * np.cos(4.0 * np.pi * k / d)
        - 0.01168 * np.cos(6.0 * np.pi * k / d)
    )


def kaiser(n: int, beta: float = 8.6, sym: bool = False) -> np.ndarray:
    """Kaiser window (Beta family) with a tunable sidelobe level.

    ``w[k] = I0(beta * sqrt(1 - x[k]^2)) / I0(beta)`` with
    ``x[k] = 2*k/D - 1``.

    Parameters
    ----------
    n : int
        Window length in samples.
    beta : float
        Shape parameter.  ``0`` gives a rectangular window, larger values give
        wider main lobes and lower sidelobes.  ``beta = 8.6`` is about -90 dB.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    x = 2.0 * _index(n) / d - 1.0
    beta = float(beta)
    if beta <= 0.0:
        return np.ones(n, dtype=np.float64)
    return np.i0(beta * np.sqrt(np.maximum(0.0, 1.0 - x * x))) / np.i0(beta)


def gaussian(n: int, alpha: float = 0.5, sym: bool = False) -> np.ndarray:
    """Gaussian window.

    ``w[k] = exp(-0.5 * ((k - (n-1)/2) / sigma)^2)`` with
    ``sigma = alpha * (n-1) / 2``.

    Parameters
    ----------
    n : int
        Window length in samples.
    alpha : float
        Width factor.  ``alpha = 0.5`` (default) puts the 1/e point at half
        the window half-width; smaller values give a narrower window.
    sym : bool
        Kept for signature compatibility; the Gaussian window is symmetric
        either way.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    k = _index(n)
    sigma = float(alpha) * (n - 1) / 2.0
    if sigma <= 0.0:
        return np.ones(n, dtype=np.float64)
    return np.exp(-0.5 * ((k - (n - 1) / 2.0) / sigma) ** 2)


def nuttall(n: int, sym: bool = False) -> np.ndarray:
    """Nuttall window (about -98 dB sidelobes).

    ``w[k] = a0 - a1*cos(2*pi*k/D) + a2*cos(4*pi*k/D) - a3*cos(6*pi*k/D)``
    with ``a = (0.3635819, 0.4891775, 0.1365995, 0.0106411)``.

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    d = _denominator(n, sym)
    k = _index(n)
    a0, a1, a2, a3 = 0.3635819, 0.4891775, 0.1365995, 0.0106411
    return (
        a0
        - a1 * np.cos(2.0 * np.pi * k / d)
        + a2 * np.cos(4.0 * np.pi * k / d)
        - a3 * np.cos(6.0 * np.pi * k / d)
    )


def sqrt_hann(n: int, sym: bool = False) -> np.ndarray:
    """Square root of the Hann window.

    ``w[k] = sqrt(0.5 - 0.5 * cos(2*pi*k / D))``.

    This window is the classic choice for weighted overlap-add: because the
    *square* of the window is a plain Hann window, the overlap sum of
    ``w**2`` is constant (1.0 at 50 % overlap), so a single constant
    normalisation factor reconstructs the signal exactly.

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        False (default) for the periodic window, True for the symmetric one.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    return np.sqrt(hann(n, sym=sym))


def rectangular(n: int, sym: bool = False) -> np.ndarray:
    """Rectangular (boxcar) window: all ones.

    Parameters
    ----------
    n : int
        Window length in samples.
    sym : bool
        Ignored; the rectangular window is symmetric by definition.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.
    """
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    return np.ones(n, dtype=np.float64)


_ALIASES: dict[str, str] = {
    "hann": "hann",
    "hanning": "hann",
    "hann-periodic": "hann",
    "hamming": "hamming",
    "blackman": "blackman",
    "blackman-harris": "blackman-harris",
    "blackmanharris": "blackman-harris",
    "bh4": "blackman-harris",
    "kaiser": "kaiser",
    "gaussian": "gaussian",
    "gauss": "gaussian",
    "nuttall": "nuttall",
    "rect": "rectangular",
    "rectangular": "rectangular",
    "boxcar": "rectangular",
    "none": "rectangular",
    "ones": "rectangular",
    "sqrt-hann": "sqrt-hann",
    "sqrt-hann-window": "sqrt-hann",
    "sqrthann": "sqrt-hann",
    "hann-sqrt": "sqrt-hann",
}


def window(name: str | np.ndarray, n: int, **kw) -> np.ndarray:
    """Build a window by name.

    Parameters
    ----------
    name : str or numpy.ndarray
        Window name (case and separator insensitive: ``"Blackman_Harris"`` and
        ``"blackman-harris"`` are the same) or an explicit window array, which
        is returned as-is (padded or truncated to ``n`` if needed).
    n : int
        Window length in samples.
    **kw
        Extra keyword arguments forwarded to the window function, e.g.
        ``beta=12.0`` for Kaiser or ``alpha=0.4`` for Gaussian.  ``sym=True``
        selects the symmetric variant.

    Returns
    -------
    numpy.ndarray
        Window of shape ``(n,)``.

    Raises
    ------
    ValueError
        If the window name is unknown.
    """
    n = int(n)
    if isinstance(name, np.ndarray) or isinstance(name, (list, tuple)):
        arr = np.asarray(name, dtype=np.float64).ravel()
        if arr.shape[0] == n:
            return arr.copy()
        out = np.zeros(n, dtype=np.float64)
        k = min(n, arr.shape[0])
        out[:k] = arr[:k]
        return out

    key = str(name).strip().lower().replace("_", "-").replace(" ", "-")
    key = _ALIASES.get(key, key)
    if key not in window_names:
        raise ValueError(f"unknown window {name!r}; known: {', '.join(window_names)}")

    sym = bool(kw.pop("sym", False))
    if key == "hann":
        return hann(n, sym=sym)
    if key == "hamming":
        return hamming(n, sym=sym)
    if key == "blackman":
        return blackman(n, sym=sym)
    if key == "blackman-harris":
        return blackman_harris(n, sym=sym)
    if key == "kaiser":
        return kaiser(n, beta=float(kw.pop("beta", 8.6)), sym=sym)
    if key == "gaussian":
        return gaussian(n, alpha=float(kw.pop("alpha", 0.5)), sym=sym)
    if key == "nuttall":
        return nuttall(n, sym=sym)
    if key == "sqrt-hann":
        return sqrt_hann(n, sym=sym)
    return rectangular(n, sym=sym)


# ---------------------------------------------------------------------------
# COLA helpers
# ---------------------------------------------------------------------------
def _overlap_to_factor(overlap) -> int:
    """Normalise the overlap argument to an integer overlap factor.

    ``2`` means 50 % overlap (hop = n/2), ``4`` means 75 % overlap.  A value
    in ``(0, 1)`` is read as an overlap *ratio* (0.5 -> factor 2, 0.75 ->
    factor 4).
    """
    ov = float(overlap)
    if 0.0 < ov < 1.0:
        ov = 1.0 / (1.0 - ov)
    factor = int(round(ov))
    if factor < 1:
        raise ValueError("overlap factor must be >= 1")
    return factor


def cola_sum(n: int, overlap=2, window_name: str = "hann", **kw) -> float:
    """Constant overlap-add sum of a window.

    The COLA constant is ``C = sum_k w[p - k*hop]``; for a COLA window it does
    not depend on ``p``.  The value returned is the mean over all residue
    classes, so non-COLA windows give a sensible average.

    Parameters
    ----------
    n : int
        Window length in samples.
    overlap : int or float
        Overlap factor (``2`` = 50 %, ``4`` = 75 %) or an overlap ratio in
        ``(0, 1)``.
    window_name : str
        Window to evaluate (default ``"hann"``).
    **kw
        Extra window arguments (e.g. ``beta``).

    Returns
    -------
    float
        The COLA constant ``C`` (dimensionless).

    Examples
    --------
    >>> round(cola_sum(1024, 2, "hann"), 6)
    1.0
    >>> round(cola_sum(1024, 2, "hamming"), 6)
    1.08
    """
    n = int(n)
    factor = _overlap_to_factor(overlap)
    hop = max(1, n // factor)
    w = window(window_name, n, **kw)
    if w.size == 0:
        return 0.0
    total = 0.0
    for r in range(hop):
        total += float(np.sum(w[r::hop]))
    return total / float(hop)


def colA(n: int, overlap=2, window_name: str = "hann", **kw) -> float:
    """COLA constant of a window (the "COLA normalisation factor").

    ``C = sum_k w[p - k*hop]``.  Divide the overlap-added signal by ``C`` to
    restore unity gain, or multiply by :func:`cola_norm`.

    Known values (periodic windows):

    * ``hann``, 50 % overlap -> ``1.0``; 75 % overlap -> ``2.0``
    * ``hamming``, 50 % overlap -> ``1.08``; 75 % overlap -> ``2.16``

    Parameters
    ----------
    n : int
        Window length in samples.
    overlap : int or float
        Overlap factor (``2`` = 50 %, ``4`` = 75 %) or an overlap ratio in
        ``(0, 1)``.
    window_name : str
        Window to evaluate (default ``"hann"``).
    **kw
        Extra window arguments (e.g. ``beta``).

    Returns
    -------
    float
        The COLA constant ``C`` (dimensionless).
    """
    return cola_sum(n, overlap, window_name, **kw)


def cola_norm(n: int, overlap=2, window_name: str = "hann", **kw) -> float:
    """Multiplicative COLA normalisation factor, ``1 / colA(...)``.

    Parameters
    ----------
    n : int
        Window length in samples.
    overlap : int or float
        Overlap factor (``2`` = 50 %, ``4`` = 75 %) or an overlap ratio in
        ``(0, 1)``.
    window_name : str
        Window to evaluate (default ``"hann"``).
    **kw
        Extra window arguments (e.g. ``beta``).

    Returns
    -------
    float
        The factor to multiply an overlap-added signal by.
    """
    c = colA(n, overlap, window_name, **kw)
    return 1.0 / c if c != 0.0 else 0.0


def cola_ripple(n: int, overlap=2, window_name: str = "hann", **kw) -> float:
    """Peak-to-peak variation of the COLA sum across output samples.

    A perfectly COLA window returns ``0.0``.

    Parameters
    ----------
    n : int
        Window length in samples.
    overlap : int or float
        Overlap factor (``2`` = 50 %, ``4`` = 75 %) or an overlap ratio in
        ``(0, 1)``.
    window_name : str
        Window to evaluate (default ``"hann"``).
    **kw
        Extra window arguments (e.g. ``beta``).

    Returns
    -------
    float
        ``max(C) - min(C)`` over the residue classes (dimensionless).
    """
    n = int(n)
    factor = _overlap_to_factor(overlap)
    hop = max(1, n // factor)
    w = window(window_name, n, **kw)
    if w.size == 0:
        return 0.0
    vals = [float(np.sum(w[r::hop])) for r in range(hop)]
    return max(vals) - min(vals)


def cola_product(
    n: int,
    overlap=2,
    window_name: str = "hann",
    synthesis_window: str | np.ndarray | None = None,
    **kw,
) -> float:
    """COLA constant of an analysis / synthesis window **pair**.

    The overlap-added signal is scaled by
    ``sum_k w_a[p - k*hop] * w_s[p - k*hop]``.  This function returns the mean
    of that sum over the residue classes; for a well chosen pair it is
    constant and the value is the correct constant normalisation factor.

    Examples
    --------
    * Hann analysis + rectangular synthesis at 50 % overlap -> ``1.0``
    * sqrt-Hann on both sides at 50 % overlap -> ``1.0``
    * Hann on both sides at 50 % overlap -> ``0.75`` (not constant)

    Parameters
    ----------
    n : int
        Window length in samples.
    overlap : int or float
        Overlap factor (``2`` = 50 %, ``4`` = 75 %) or an overlap ratio in
        ``(0, 1)``.
    window_name : str
        Analysis window (default ``"hann"``).
    synthesis_window : str or numpy.ndarray, optional
        Synthesis window.  Default: the analysis window.
    **kw
        Extra window arguments for the analysis window (e.g. ``beta``).

    Returns
    -------
    float
        The window-pair COLA constant (dimensionless).
    """
    n = int(n)
    factor = _overlap_to_factor(overlap)
    hop = max(1, n // factor)
    wa = window(window_name, n, **kw)
    ws = wa if synthesis_window is None else window(synthesis_window, n, **kw)
    if wa.size == 0:
        return 0.0
    product = wa * ws
    total = 0.0
    for r in range(hop):
        total += float(np.sum(product[r::hop]))
    return total / float(hop)
