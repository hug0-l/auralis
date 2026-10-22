"""
aurcore.dsp.spectral — the spectral-processing layer.

Every routine in this module works on magnitude/phase representations of a
short-time Fourier transform.  Conventions used throughout:

* **Magnitudes** are *linear* unless the name or the argument says ``_db``.
* **Gain curves** returned by this module are in **dB** so that they can be
  fed straight into :func:`apply_gain` / :func:`apply_gain_ola`.
* A "frame" is one STFT column; a "bin" is one STFT row of the
  ``(frames, bins)`` arrays.

The functions are deliberately free of module state so that they can be used
both from the realtime processing modules and from offline analysis tools.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

__all__ = [
    "smooth_gain_curve",
    "moving_average",
    "resample_gain_2d",
    "apply_gain",
    "apply_gain_ola",
    "estimate_noise_profile",
    "spectral_subtract",
    "om_lsa_gain",
    "om_lsa_gain_curve",
    "decision_directed_snr",
    "noise_power_from_db",
    "wiener_gain",
    "spectral_mask",
    "whiten",
    "masking_model",
    "interpolate_band",
]

_EPS = 1e-20


# ---------------------------------------------------------------------------
# dB helpers
# ---------------------------------------------------------------------------
def _fallback_db_to_lin(x):
    """Convert dB to a linear amplitude ratio (array aware)."""
    return np.power(10.0, np.asarray(x, dtype=np.float64) / 20.0)


def _fallback_lin_to_db(x, floor_db: float = -300.0):
    """Convert a linear amplitude ratio to dB (array aware)."""
    a = np.maximum(np.abs(np.asarray(x, dtype=np.float64)), 1e-15)
    out = 20.0 * np.log10(a)
    return np.maximum(out, floor_db)


def _resolve_db_helpers():
    """Prefer the shared ``aurcore.dsp.util`` helpers, fall back locally.

    The shared helpers are optional: this module must keep working even if
    ``util.py`` is not present yet or exposes a different signature.
    """
    try:  # pragma: no cover - depends on the neighbouring module
        from .util import db_to_lin, lin_to_db  # type: ignore

        if abs(float(db_to_lin(0.0)) - 1.0) < 1e-9 and abs(float(lin_to_db(1.0))) < 1e-9:
            return db_to_lin, lin_to_db
    except Exception:
        pass
    return _fallback_db_to_lin, _fallback_lin_to_db


_db_to_lin, _lin_to_db = _resolve_db_helpers()


def _as_db_array(x) -> np.ndarray:
    """Return ``x`` as a float64 dB array (no conversion, just casting)."""
    return np.asarray(x, dtype=np.float64)


# ---------------------------------------------------------------------------
# Gain-curve smoothing
# ---------------------------------------------------------------------------
def _smooth_kernel(width_bins: float, mode: str) -> np.ndarray:
    """Build a normalised, symmetric smoothing kernel of ``width_bins`` bins."""
    width = float(width_bins)
    if width <= 0.0:
        return np.ones(1, dtype=np.float64)
    mode_l = str(mode).lower()
    if mode_l.startswith("gauss"):
        # ``width_bins`` is the full width at half maximum.
        sigma = width / 2.3548200450309493
        radius = max(1, int(math.ceil(3.0 * sigma)))
        i = np.arange(-radius, radius + 1, dtype=np.float64)
        k = np.exp(-0.5 * (i / sigma) ** 2)
    elif mode_l.startswith("tri"):
        # ``width_bins`` is the full base width of the triangle.
        radius = max(1, int(math.ceil(width / 2.0)))
        i = np.arange(-radius, radius + 1, dtype=np.float64)
        k = np.maximum(1.0 - np.abs(i) / (radius + 1.0), 0.0)
    else:
        raise ValueError(f"unknown smoothing mode {mode!r} (use 'gaussian' or 'triangular')")
    total = float(np.sum(k))
    if total <= 0.0:
        return np.ones(1, dtype=np.float64)
    return k / total


def _convolve_same(a: np.ndarray, k: np.ndarray, axis: int = -1) -> np.ndarray:
    """Convolve ``a`` with the centred kernel ``k`` keeping the same shape.

    Edges are handled by replicating the first/last sample so that a smoothed
    gain curve does not dip at the band limits.
    """
    if k.size <= 1:
        return a.copy()
    a = np.moveaxis(a, axis, -1)
    n = a.shape[-1]
    pad = k.size // 2
    if pad > 0:
        a = np.concatenate(
            [np.repeat(a[..., :1], pad, axis=-1), a, np.repeat(a[..., -1:], k.size - 1 - pad,
                                                               axis=-1)],
            axis=-1,
        )
    nfft = int(2 ** math.ceil(math.log2(a.shape[-1] + k.size - 1)))
    fa = np.fft.rfft(a, n=nfft, axis=-1)
    fk = np.fft.rfft(k, n=nfft)
    out = np.fft.irfft(fa * fk, n=nfft, axis=-1)
    out = out[..., pad:pad + n]
    return np.moveaxis(out, -1, axis)


def smooth_gain_curve(gain_db, width_bins: float, mode: str = "gaussian") -> np.ndarray:
    """Smooth a frequency-domain gain curve.

    Parameters
    ----------
    gain_db:
        Gain curve in dB.  Either ``(bins,)`` or ``(frames, bins)``; the
        smoothing always runs along the **last** (frequency) axis.
    width_bins:
        Width of the smoothing kernel measured in bins.  For ``'gaussian'``
        this is the full width at half maximum, for ``'triangular'`` the full
        base width.  ``0`` returns the curve unchanged.
    mode:
        ``'gaussian'`` (default) or ``'triangular'``.

    Returns
    -------
    numpy.ndarray
        The smoothed gain curve, same shape and unit (dB) as the input.
    """
    g = _as_db_array(gain_db)
    if g.size == 0:
        return g.copy()
    if float(width_bins) <= 0.0:
        return g.copy()
    kernel = _smooth_kernel(float(width_bins), mode)
    if kernel.size <= 1:
        return g.copy()
    if g.ndim == 1:
        return _convolve_same(g, kernel, axis=0)
    return _convolve_same(g, kernel, axis=-1)


def _axis_slice(arr: np.ndarray, axis: int, start: int, stop: int) -> np.ndarray:
    """Return the view of ``arr`` between ``start`` and ``stop`` on ``axis``."""
    idx = [slice(None)] * arr.ndim
    idx[axis] = slice(start, stop)
    return arr[tuple(idx)]


def moving_average(x, width: int, axis: int = -1) -> np.ndarray:
    """Centred moving average along ``axis`` with edge replication.

    The result is the same as a convolution with a box kernel of ``width``
    samples over edge-replicated borders, but it is computed with a single
    cumulative sum instead of one convolution per line.  For a ``(frames,
    bins)`` array that is the difference between a few dozen NumPy calls
    and one call per bin, which is what made the spectral modules
    expensive.

    Even widths are rounded up to the next odd number so that the window
    stays centred.

    Parameters
    ----------
    x:
        Input array.
    width:
        Window length in samples.  ``1`` returns the input unchanged.
    axis:
        Axis the filter runs along.

    Returns
    -------
    numpy.ndarray
        The filtered array, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if width % 2 == 0:
        width += 1
    ax = int(axis) % arr.ndim
    n = arr.shape[ax]
    pad = width // 2
    if pad > 0:
        pw = [(0, 0)] * arr.ndim
        pw[ax] = (pad, pad)
        padded = np.pad(arr, pw, mode="edge")
    else:
        padded = arr
    length = padded.shape[ax]
    # cumulative sum: the sum of every window is one difference of two
    # entries of the cumulative sum.
    cs = np.cumsum(padded, axis=ax)
    out = np.empty_like(arr, dtype=np.float64)
    # the first window has no lower neighbour
    np.divide(
        _axis_slice(cs, ax, width - 1, width),
        float(width),
        out=_axis_slice(out, ax, 0, 1),
    )
    if n > 1:
        rest = _axis_slice(cs, ax, width, length) - _axis_slice(cs, ax, 0, n - 1)
        np.divide(rest, float(width), out=_axis_slice(out, ax, 1, n))
    return out


def _bracket(xp: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, ...]:
    """Bracket ``x`` in the increasing grid ``xp``.

    Returns the index of the lower neighbour, the index of the upper
    neighbour, the lower grid value, the distance between the two grid
    points and the two boolean masks that mark the values below and above
    the grid.  The masks are needed because a linear interpolation that is
    clamped (``numpy.interp``) is *not* the same as an extrapolation.
    """
    n = int(xp.size)
    if n == 1:
        zeros = np.zeros(x.size, dtype=np.intp)
        return zeros, zeros, xp[0] * np.ones_like(x), np.ones_like(x), (
            x < xp[0]
        ), (x > xp[0])
    idx = np.searchsorted(xp, x, side="right") - 1
    idx = np.clip(idx, 0, n - 2).astype(np.intp)
    lo = xp[idx]
    dx = xp[idx + 1] - lo
    return idx, idx + 1, lo, dx, (x < xp[0]), (x > xp[-1])


def resample_gain_2d(
    gain_db,
    src_times,
    src_freqs,
    dst_times,
    dst_freqs,
) -> np.ndarray:
    """Interpolate a gain curve onto another time/frequency grid.

    The curve is interpolated in time first and in frequency second, which
    is a separable bilinear interpolation.  Both steps are done with one
    vectorised pass over the grid, so the whole resample costs a handful
    of NumPy calls instead of one ``numpy.interp`` call per bin and per
    frame.

    Outside the source grid the curve is **clamped** to the nearest edge
    value, exactly like ``numpy.interp`` does.

    Parameters
    ----------
    gain_db:
        The source curve, ``(src_times, src_freqs)``.
    src_times:
        Increasing time coordinates of the source curve, in seconds.
    src_freqs:
        Increasing frequency coordinates of the source curve, in Hz.
    dst_times:
        The time coordinates of the result.
    dst_freqs:
        The frequency coordinates of the result.

    Returns
    -------
    numpy.ndarray
        The resampled curve, shape ``(len(dst_times), len(dst_freqs))``.
    """
    g = _as_db_array(gain_db)
    if g.ndim != 2:
        raise ValueError("gain_db must be a 2-D (frames, bins) array")
    st = np.asarray(src_times, dtype=np.float64).ravel()
    sf = np.asarray(src_freqs, dtype=np.float64).ravel()
    dt = np.asarray(dst_times, dtype=np.float64).ravel()
    df = np.asarray(dst_freqs, dtype=np.float64).ravel()
    if st.size == 0 or sf.size == 0:
        raise ValueError("the source grid must not be empty")
    if dt.size == 0 or df.size == 0:
        return np.zeros((dt.size, df.size), dtype=np.float64)

    # -- time: one interpolation for every bin at once -----------------------
    t_lo, t_hi, t_x0, t_dx, t_below, t_above = _bracket(st, dt)
    if st.size == 1:
        in_time = np.repeat(g[0][None, :], dt.size, axis=0)
    else:
        lo = g[t_lo]
        hi = g[t_hi]
        slope = (hi - lo) / np.where(t_dx == 0.0, 1.0, t_dx)[:, None]
        slope[t_dx == 0.0] = 0.0      # a repeated grid point has no slope
        in_time = slope * (dt[:, None] - t_x0[:, None]) + lo
        in_time[t_below] = g[0]
        in_time[t_above] = g[-1]

    # -- frequency: one interpolation for every frame at once ----------------
    f_lo, f_hi, f_x0, f_dx, f_below, f_above = _bracket(sf, df)
    if sf.size == 1:
        return np.repeat(in_time[:, :1], df.size, axis=1)
    lo = in_time[:, f_lo]
    hi = in_time[:, f_hi]
    slope = (hi - lo) / np.where(f_dx == 0.0, 1.0, f_dx)[None, :]
    slope[:, f_dx == 0.0] = 0.0
    out = slope * (df[None, :] - f_x0[None, :]) + lo
    out[:, f_below] = in_time[:, :1]
    out[:, f_above] = in_time[:, -1:]
    return out


# ---------------------------------------------------------------------------
# Applying a gain curve
# ---------------------------------------------------------------------------
def apply_gain(mag, phase, gain_db) -> np.ndarray:
    """Scale a magnitude/phase spectrum by a gain curve.

    Parameters
    ----------
    mag:
        Linear magnitudes, ``(bins,)`` or ``(frames, bins)``.
    phase:
        Phases in radians with the same shape as ``mag``.
    gain_db:
        Gain curve in dB.  A ``(bins,)`` curve is broadcast over all frames.

    Returns
    -------
    numpy.ndarray
        Complex spectrum ``mag * gain * exp(1j*phase)``.
    """
    m = np.asarray(mag, dtype=np.float64)
    p = np.asarray(phase, dtype=np.float64)
    if m.shape != p.shape:
        p = np.broadcast_to(p, m.shape)
    g = _db_to_lin(_as_db_array(gain_db))
    try:
        g = np.broadcast_to(g, m.shape)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(
            f"gain curve shape {np.shape(gain_db)} is not broadcastable onto magnitude shape "
            f"{m.shape}"
        ) from exc
    return m * g * np.exp(1j * p)


def _spectrogram_of(stft: Any):
    """Return ``(complex_spectrogram, synthesizer, bin_axis)``.

    Accepted inputs:

    * a plain complex spectrogram, shape ``(frames, bins)`` — the layout used
      throughout this module (the shared ``aurcore.dsp.stft`` engine uses
      ``(bins, frames)``);
    * an object that holds a magnitude/phase spectrogram plus a synthesis
      method, e.g. the shared ``aurcore.dsp.stft.Spectrogram``;
    * an object that exposes a complex spectrogram attribute.

    ``synthesizer`` is ``None`` when the object cannot resynthesise, in which
    case the gain-modified spectrogram is returned.  ``bin_axis`` tells which
    axis of the spectrogram holds the frequency bins.
    """
    if isinstance(stft, np.ndarray):
        return stft, None, -1

    # the shared Spectrogram object: magnitude/phase plus an engine
    mag_attr = getattr(stft, "mag", None)
    phase_attr = getattr(stft, "phase", None)
    if mag_attr is not None and phase_attr is not None:
        mag = np.asarray(mag_attr)
        phase = np.asarray(phase_attr)
        spec = mag * np.exp(1j * phase)
        engine = getattr(stft, "engine", None)
        center = bool(getattr(stft, "center", True))

        def _synthesizer(modified):
            if engine is not None and hasattr(engine, "synthesize_block"):
                # the shared engine works with (bins, frames)
                return engine.synthesize_block(
                    np.abs(modified), np.angle(modified), norm="exact", center=center
                )
            return np.asarray(stft.synthesize())

        # the shared engine stores (bins, frames)
        return spec, _synthesizer, 0

    spec = None
    for name in ("spec", "spectrum", "stft", "X", "frames", "data"):
        value = getattr(stft, name, None)
        if value is None:
            continue
        arr = np.asarray(value)
        if arr.ndim == 2 and np.iscomplexobj(arr):
            spec = arr
            break
    if spec is None:
        raise TypeError(
            "cannot find a complex spectrogram on the given object; pass a "
            "(frames, bins) spectrogram or a spectrogram object"
        )

    synth = None
    for name in ("synthesize", "istft", "inverse", "reconstruct", "from_spectrogram"):
        fn = getattr(stft, name, None)
        if callable(fn):
            synth = fn
            break
    return spec, synth, -1


def apply_gain_ola(stft, gain_db) -> np.ndarray:
    """Apply a gain curve to an STFT and resynthesise by overlap-add.

    Parameters
    ----------
    stft:
        A complex spectrogram ``(frames, bins)`` or an object that holds one
        (for example ``aurcore.dsp.stft.Spectrogram``, which uses the
        ``(bins, frames)`` layout).
    gain_db:
        Gain curve in dB, ``(bins,)`` or one value per frame and bin.

    Returns
    -------
    numpy.ndarray
        The resynthesised time-domain signal when the object provides a
        synthesis method, otherwise the gain-modified complex spectrogram.
    """
    spec, synth, bin_axis = _spectrogram_of(stft)
    mag = np.abs(spec)
    phase = np.angle(spec)
    g = _db_to_lin(_as_db_array(gain_db))
    if g.ndim == 1 and mag.ndim == 2 and g.size == mag.shape[bin_axis]:
        g = g[:, None] if bin_axis == 0 else g[None, :]
    out_spec = mag * g * np.exp(1j * phase)
    if synth is None:
        return out_spec
    try:
        return np.asarray(synth(out_spec))
    except TypeError:
        return np.asarray(synth(mag * g, phase))


# ---------------------------------------------------------------------------
# Noise profiling and spectral subtraction
# ---------------------------------------------------------------------------
def estimate_noise_profile(mag_db, frames=None, percentile: float = 10.0) -> np.ndarray:
    """Estimate a per-bin noise floor from a magnitude spectrogram.

    The profile is the given percentile of the magnitude over the selected
    frames, which is a robust estimator of the noise floor when the selected
    frames contain mostly noise.

    Parameters
    ----------
    mag_db:
        Magnitude spectrogram in dB, ``(frames, bins)`` (a 1-D array is
        treated as a single frame).
    frames:
        Which frames to use.  ``None`` uses every frame, an integer uses the
        first *n* frames, an integer sequence selects those frame indices.
    percentile:
        Percentile used for the floor, in ``[0, 100]``.

    Returns
    -------
    numpy.ndarray
        The per-bin noise floor in dB, shape ``(bins,)``.
    """
    m = _as_db_array(mag_db)
    if m.ndim == 1:
        m = m[None, :]
    if m.ndim != 2:
        raise ValueError("mag_db must be a 1-D or 2-D array")
    if frames is None:
        sel = m
    elif isinstance(frames, (int, np.integer)):
        sel = m[: int(frames)]
    else:
        idx = np.asarray(frames, dtype=np.intp)
        sel = m[idx]
    if sel.shape[0] == 0:
        return np.zeros(m.shape[-1], dtype=np.float64)
    return np.percentile(sel, float(percentile), axis=0)


def spectral_subtract(
    mag,
    noise_profile,
    over_subtraction: float = 1.0,
    floor_db: float = -100.0,
    mag_is_db: bool = False,
    profile_is_db: bool = True,
) -> np.ndarray:
    """Spectral subtraction: turn a noise estimate into a gain curve.

    Parameters
    ----------
    mag:
        Observed magnitude.  Linear by default (``mag_is_db=False``).
    noise_profile:
        Per-bin noise floor.  dB by default (the output of
        :func:`estimate_noise_profile`); set ``profile_is_db=False`` for a
        linear magnitude profile.
    over_subtraction:
        Multiplier applied to the noise estimate (1.0 = plain subtraction).
    floor_db:
        Lower bound of the returned gain, in dB.

    Returns
    -------
    numpy.ndarray
        Gain curve in dB, same shape as ``mag``.
    """
    m = _as_db_array(mag)
    p = _as_db_array(noise_profile)
    if mag_is_db:
        m = _db_to_lin(m)
    if profile_is_db:
        p = _db_to_lin(p)
    p = np.maximum(p, 0.0)

    clean = m - float(over_subtraction) * p
    gain = np.where(m > _EPS, clean / np.maximum(m, _EPS), 0.0)
    gain = np.clip(gain, 0.0, 1.0)
    floor = float(_db_to_lin(floor_db))
    gain = np.maximum(gain, floor)
    return _lin_to_db(gain)


# ---------------------------------------------------------------------------
# Gain estimators
# ---------------------------------------------------------------------------
def decision_directed_snr(
    prior_mag,
    current_mag,
    alpha: float = 0.98,
    noise_power=None,
) -> np.ndarray:
    """Decision-directed a-priori SNR estimate (Ephraim-Malah).

    ``xi = alpha * (|S_prev|^2 / lambda_d) + (1 - alpha) * max(gamma - 1, 0)``

    Parameters
    ----------
    prior_mag:
        Previous clean-magnitude estimate (linear), same shape as
        ``current_mag``.
    current_mag:
        Current observed magnitude (linear).
    alpha:
        Decision-directed smoothing factor, ``0 <= alpha < 1``.
    noise_power:
        Linear noise **power** per bin.  When omitted it is estimated as the
        10th percentile of the current power over time (2-D input) or taken
        as the instantaneous power (1-D input).  A noise floor that is
        available in dB has to be converted with
        :func:`noise_power_from_db`; handing the estimator the *magnitude*
        of the floor instead makes every SNR come out too low.

    Returns
    -------
    numpy.ndarray
        A-priori SNR estimate (linear), same shape as ``current_mag``.
    """
    prev = _as_db_array(prior_mag)
    cur = _as_db_array(current_mag)
    if noise_power is None:
        if cur.ndim == 2:
            npow = np.percentile(cur ** 2, 10.0, axis=0)
        else:
            npow = cur ** 2
    else:
        npow = _as_db_array(noise_power)
    npow = np.maximum(npow, _EPS)

    gamma = (cur ** 2) / npow
    xi_dd = alpha * (prev ** 2) / npow
    xi_inst = np.maximum(gamma - 1.0, 0.0)
    a = float(alpha)
    return a * xi_dd + (1.0 - a) * xi_inst


def noise_power_from_db(floor_db) -> np.ndarray:
    """Convert a per-bin noise floor in dB into a linear **power**.

    A floor in dB describes a magnitude.  The gain estimators of this module
    compare powers (``|Y|^2 / lambda_d``), so the magnitude has to be
    squared.  Feeding them the magnitude instead drives the a-posteriori
    and the a-priori SNR orders of magnitude too low, the speech presence
    probability collapses to zero and the branch saturates the reduction
    instead of refining it.

    Parameters
    ----------
    floor_db:
        Per-bin noise floor in dB.

    Returns
    -------
    numpy.ndarray
        The linear noise power per bin, same shape as ``floor_db``.
    """
    return np.maximum(_db_to_lin(_as_db_array(floor_db)), _EPS) ** 2


def om_lsa_gain_curve(
    prior_mag,
    current_mag,
    noise_floor_db,
    alpha: float = 0.98,
    G_min: float = 0.0,
    G_max: float = 1.0,
) -> np.ndarray:
    """The complete OM-LSA branch: a-priori SNR and gain, in one call.

    The branch is the combination of :func:`decision_directed_snr` and
    :func:`om_lsa_gain`.  It is offered as one function because the two
    estimators have to agree on the *units* of the noise estimate: they
    compare powers, and a noise floor in dB is a magnitude.  Taking the
    floor in dB here makes the conversion — and therefore the unit error
    that used to saturate the branch — impossible to write.

    Parameters
    ----------
    prior_mag:
        Previous clean-magnitude estimate (linear), same shape as
        ``current_mag``.
    current_mag:
        Current observed magnitude (linear).
    noise_floor_db:
        Per-bin noise floor in **dB**.  It is converted to a power with
        :func:`noise_power_from_db`.
    alpha:
        Decision-directed smoothing factor, ``0 <= alpha < 1``.
    G_min, G_max:
        Gain limits (linear).

    Returns
    -------
    numpy.ndarray
        Linear gain, same shape as ``current_mag``.
    """
    cur = _as_db_array(current_mag)
    prev = _as_db_array(prior_mag)
    npow = np.maximum(noise_power_from_db(noise_floor_db), _EPS)
    gamma = (cur ** 2) / npow
    xi = decision_directed_snr(prev, cur, alpha, npow)
    return om_lsa_gain(gamma, xi, G_min, G_max)


def om_lsa_gain(
    gamma,
    xi,
    G_min: float = -15.0,
    G_max: float = 1.0,
    nu=None,
) -> np.ndarray:
    """Optimally-modified log-spectral-amplitude gain estimator.

    The estimator is the MMSE-LSA core of Ephraim-Malah weighted by the
    speech presence probability ``nu``:

    ``G = nu * G_lsa(gamma, nu) + (1 - nu) * G_min``

    with the MMSE-LSA core

    ``G_lsa = (gamma/(1+gamma)) * nu + (1/(1+gamma)) * exp(0.5*nu*ln(1+gamma*nu))``

    The speech presence probability is derived from the a-priori SNR as
    ``nu = xi / (1 + xi)``, so a decision-directed a-priori SNR (see
    :func:`decision_directed_snr`) drives the whole estimator.  The result
    is finally clamped to ``[G_min, G_max]``.

    ``gamma`` and ``xi`` are *ratios of powers*: the caller has to divide by
    the noise **power**, never by its magnitude.  :func:`om_lsa_gain_curve`
    takes the floor in dB and does the conversion, so the unit cannot be
    got wrong.

    Parameters
    ----------
    gamma:
        A-posteriori SNR (linear, ``|Y|^2 / lambda_d``).
    xi:
        A-priori SNR (linear).
    G_min, G_max:
        Gain limits (linear).  ``G_min`` is typically negative so that the
        estimator can attenuate noise.
    nu:
        Optional explicit speech presence probability in ``[0, 1]``.

    Returns
    -------
    numpy.ndarray
        Linear gain, same shape as ``gamma``.
    """
    g = np.maximum(_as_db_array(gamma), 0.0)
    x = np.maximum(_as_db_array(xi), 0.0)
    if nu is None:
        nu_arr = x / (1.0 + x)
    else:
        nu_arr = np.clip(_as_db_array(nu), 0.0, 1.0)
    one_plus = 1.0 + g
    with np.errstate(divide="ignore", invalid="ignore"):
        core = (g / one_plus) * nu_arr + (1.0 / one_plus) * np.exp(
            0.5 * nu_arr * np.log1p(g * nu_arr)
        )
        gain = nu_arr * core + (1.0 - nu_arr) * float(G_min)
    gain = np.nan_to_num(gain, nan=0.0, posinf=1.0, neginf=0.0)
    return np.clip(gain, float(G_min), float(G_max))


def wiener_gain(snr, G_min: float = -20.0, G_max: float = 1.0) -> np.ndarray:
    """Wiener gain ``G = snr / (1 + snr)`` clamped to ``[G_min, G_max]``."""
    s = np.maximum(_as_db_array(snr), 0.0)
    gain = s / (1.0 + s)
    return np.clip(gain, float(G_min), float(G_max))


def spectral_mask(
    clean_mag,
    noisy_mag,
    floor_db: float = -100.0,
    mode: str = "soft",
) -> np.ndarray:
    """Build a spectral mask from a clean and a noisy magnitude estimate.

    Parameters
    ----------
    clean_mag:
        Estimated clean magnitude (linear).
    noisy_mag:
        Observed magnitude (linear).
    floor_db:
        Lower bound of the mask, in dB.
    mode:
        ``'soft'``  — magnitude-ratio soft mask (default),
        ``'wiener'`` — power (Wiener) soft mask,
        ``'binary'`` — hard decision, 1 where the clean estimate wins.

    Returns
    -------
    numpy.ndarray
        The mask, same shape as ``clean_mag``.
    """
    c = _as_db_array(clean_mag)
    n = _as_db_array(noisy_mag)
    n = np.maximum(n, _EPS)
    ratio = np.clip(c / n, 0.0, None)
    mode_l = str(mode).lower()
    if mode_l.startswith("bin"):
        # a hard decision keeps the mask binary
        return (ratio > 1.0).astype(np.float64)
    if mode_l.startswith("wie"):
        mask = ratio ** 2 / (1.0 + ratio ** 2)
    else:
        mask = ratio
    mask = np.clip(mask, 0.0, 1.0)
    floor = float(_db_to_lin(floor_db))
    return np.maximum(mask, floor)


def whiten(
    mag,
    target_curve,
    mag_is_db: bool | None = None,
    target_is_db: bool | None = None,
    preserve_level: bool = True,
    max_gain_db: float = 60.0,
) -> np.ndarray:
    """Spectral whitening: the gain curve that moves ``mag`` toward a target.

    Parameters
    ----------
    mag:
        Magnitude curve to be whitened.  Linear or dB (auto-detected when the
        matching flag is left at ``None``).
    target_curve:
        Desired spectral shape, same units as ``mag``.
    mag_is_db, target_is_db:
        Force the interpretation of the inputs.
    preserve_level:
        Remove the mean gain offset so the broadband level is preserved.
    max_gain_db:
        Symmetrical limit of the returned gain curve, in dB.

    Returns
    -------
    numpy.ndarray
        Gain curve in dB, same shape as ``mag``.
    """
    m = _as_db_array(mag)
    t = _as_db_array(target_curve)
    if mag_is_db is None:
        mag_is_db = bool(np.any(m < 0.0))
    if target_is_db is None:
        target_is_db = bool(np.any(t < 0.0))
    m_lin = _db_to_lin(m) if mag_is_db else np.maximum(m, 0.0)
    t_lin = _db_to_lin(t) if target_is_db else np.maximum(t, 0.0)

    gain_db = _lin_to_db(t_lin) - _lin_to_db(m_lin)
    if preserve_level:
        gain_db = gain_db - float(np.mean(gain_db))
    return np.clip(gain_db, -abs(float(max_gain_db)), abs(float(max_gain_db)))


# ---------------------------------------------------------------------------
# Psychoacoustic masking model
# ---------------------------------------------------------------------------
def _absolute_threshold_of_hearing(freqs: np.ndarray) -> np.ndarray:
    """Terhardt's absolute threshold of hearing, in dB SPL."""
    f = np.maximum(np.asarray(freqs, dtype=np.float64), 20.0) / 1000.0
    return 3.64 * f ** (-0.8) - 0.6e-3 * f ** 4


def masking_model(
    mag_db,
    offset_db: float = 0.0,
    sr: int = 48000,
    n_fft: int | None = None,
    spread_up_db: float = 24.0,
    spread_down_db: float = 12.0,
) -> np.ndarray:
    """Simple psychoacoustic masking threshold.

    The threshold combines the absolute threshold of hearing with a simple
    spectral spread of masking: every spectral component acts as a masker for
    its neighbours, attenuated by ``spread_up_db`` per octave above and
    ``spread_down_db`` per octave below the masker.

    Parameters
    ----------
    mag_db:
        Magnitude spectrogram in dB, ``(frames, bins)`` or ``(bins,)``.
    offset_db:
        Global offset added to the threshold (positive = more masking).
    sr:
        Sample rate used to derive the frequency axis.
    n_fft:
        FFT size.  Derived from the number of bins when omitted.
    spread_up_db, spread_down_db:
        Spreading slopes in dB per octave.

    Returns
    -------
    numpy.ndarray
        Masking threshold in dB, same shape as ``mag_db``.
    """
    m = _as_db_array(mag_db)
    single = m.ndim == 1
    if single:
        m = m[None, :]
    bins = m.shape[-1]
    if n_fft is None:
        n_fft = max(2 * (bins - 1), 2)
    freqs = np.fft.rfftfreq(int(n_fft), 1.0 / float(sr))
    if freqs.size < bins:
        freqs = np.pad(freqs, (0, bins - freqs.size), mode="edge")
    freqs = freqs[:bins]

    ath = _absolute_threshold_of_hearing(freqs)

    # Log-frequency grid for the spreading computation.
    lo = math.log2(max(freqs[0], 20.0) / 1000.0)
    hi = math.log2(max(freqs[-1], 40.0) / 1000.0)
    if hi <= lo:
        hi = lo + 1.0
    grid = np.linspace(lo, hi, num=min(max(bins * 2, 256), 4096))
    grid_f = np.power(2.0, grid) * 1000.0
    grid_f = np.clip(grid_f, 20.0, float(sr) / 2.0)

    level = np.mean(m, axis=0) if m.shape[0] > 1 else m[0]
    level_grid = np.interp(grid_f, freqs, level)
    # The absolute threshold of hearing is an absolute level, so it is shifted
    # into the level scale of the spectrum before the two are combined.
    peak_level = float(np.max(level)) if level.size else 0.0
    ath_grid = np.interp(grid_f, freqs, ath) + peak_level

    # Spreading: for every octave offset, attenuate and accumulate the maximum.
    spread = np.full_like(level_grid, -np.inf)
    for offset in (-3.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0):
        decay = spread_up_db * max(offset, 0.0) + spread_down_db * max(-offset, 0.0)
        shifted = np.interp(grid + offset, grid, level_grid, left=level_grid[0],
                            right=level_grid[-1])
        spread = np.maximum(spread, shifted - decay)
    spread = np.where(np.isfinite(spread), spread, level_grid)

    spread_bins = np.interp(freqs, grid_f, spread)
    threshold = float(offset_db) + np.maximum(ath, spread_bins)
    if single:
        return threshold
    return np.broadcast_to(threshold, m.shape).copy()


# ---------------------------------------------------------------------------
# Band interpolation (click / clip repair)
# ---------------------------------------------------------------------------
def interpolate_band(
    mag,
    phase,
    f0: float,
    f1: float,
    t0: int,
    t1: int,
    sr: int = 48000,
    n_fft: int | None = None,
    strength: float = 1.0,
):
    """Fill a damaged spectral region by interpolation from its borders.

    The region spanning frames ``t0..t1`` and bins between ``f0`` and ``f1``
    (Hz) is replaced by a smooth patch: the magnitude is interpolated
    linearly across the band and cross-faded in time between the frame just
    before the hole and the frame just after it.  The phase is interpolated
    along the shortest arc so that the vertical phase structure is preserved.

    Parameters
    ----------
    mag, phase:
        Magnitude and phase spectrograms, ``(frames, bins)``.  A 1-D input is
        treated as a single frame.
    f0, f1:
        Band limits in Hz.
    t0, t1:
        First and last frame of the damaged region (inclusive, frame units).
    sr:
        Sample rate used to convert Hz into bins.
    n_fft:
        FFT size.  Derived from the number of bins when omitted.
    strength:
        ``0`` leaves the region untouched, ``1`` applies the full patch.

    Returns
    -------
    tuple[numpy.ndarray, numpy.ndarray]
        The patched magnitude and phase spectrograms.
    """
    m = _as_db_array(mag)
    p = _as_db_array(phase)
    if m.ndim == 1:
        m = m[None, :]
        p = p[None, :]
    if m.shape != p.shape:
        p = np.broadcast_to(p, m.shape).copy()

    frames, bins = m.shape
    if n_fft is None:
        n_fft = max(2 * (bins - 1), 2)

    b0 = int(round(float(f0) * n_fft / float(sr)))
    b1 = int(round(float(f1) * n_fft / float(sr)))
    b0 = int(np.clip(b0, 0, bins - 1))
    b1 = int(np.clip(b1, 0, bins - 1))
    if b1 < b0:
        b0, b1 = b1, b0

    t0i = int(np.clip(int(t0), 0, frames - 1))
    t1i = int(np.clip(int(t1), 0, frames - 1))
    if t1i <= t0i or strength <= 0.0:
        return m, p
    if b1 == b0 and t1i == t0i:
        return m, p

    out_m = m.copy()
    out_p = p.copy()

    pre = t0i - 1 if t0i > 0 else t0i
    post = t1i + 1 if t1i < frames - 1 else t1i

    def _ramp(frame_mag: np.ndarray) -> np.ndarray:
        left = float(frame_mag[b0 - 1]) if b0 > 0 else float(frame_mag[b0])
        right = float(frame_mag[b1 + 1]) if b1 < bins - 1 else float(frame_mag[b1])
        if b1 == b0:
            return np.array([0.5 * (left + right)])
        w = np.linspace(0.0, 1.0, b1 - b0 + 1)
        return left + (right - left) * w

    ramp_pre = _ramp(m[pre])
    ramp_post = _ramp(m[post])
    slice_b = slice(b0, b1 + 1)

    for fr in range(t0i, t1i + 1):
        u = 0.0 if t1i == t0i else (fr - t0i) / float(t1i - t0i)
        target = (1.0 - u) * ramp_pre + u * ramp_post
        out_m[fr, slice_b] = (1.0 - strength) * m[fr, slice_b] + strength * target

        # Shortest-arc phase interpolation between the two border frames.
        d = np.mod(p[post, slice_b] - p[pre, slice_b] + np.pi, 2.0 * np.pi) - np.pi
        out_p[fr, slice_b] = p[pre, slice_b] + u * d

    return out_m, out_p
