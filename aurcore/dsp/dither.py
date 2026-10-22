"""
aurcore.dsp.dither — TPDF dither, noise shaping and bit-depth requantisation.

The module turns a floating point signal into a true fixed point signal:

1. triangular-PDF (TPDF) dither is added so that the quantisation error is
   decorrelated from the signal,
2. the quantisation error is fed back through a noise-shaping filter so that
   the residual noise is pushed towards the top of the band,
3. the signal is requantised to the requested bit depth.

Conventions
-----------
* A full scale sine has a peak of ``1.0``; the quantisation step of *b* bits
  is ``2**-(b-1)`` (so 16 bit gives a step of 3.05e-5, i.e. 96 dB SNR).
* The noise-shaping coefficients are error-feedback coefficients.  The
  resulting noise transfer function is ``1 - sum(c_i * z**-i)``; a single
  coefficient of ``1.0`` therefore gives a first-order high-pass shaping
  (the noise is pushed towards high frequencies).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

__all__ = [
    "dither",
    "quantize",
    "bit_depth_step",
    "noise_shaping_presets",
    "dither_amount_scale",
]

_EPS = 1e-20

# Empirical noise-shaping presets.  The values are the error-feedback
# coefficients; the resulting transfer function is 1 - sum(c_i z^-i).
_NOISE_SHAPING_PRESETS: dict[str, tuple[float, ...]] = {
    "none": (),
    "shape1": (1.0,),
    "shape2": (2.0, -1.0),
    "shape3": (1.5, -0.7, 0.1),
    "shape4": (1.6, -0.9, 0.3, -0.05),
    "shape5": (1.7, -1.1, 0.5, -0.2, 0.05),
    "ultra": (1.8, -1.3, 0.7, -0.35, 0.15, -0.05),
}

# TPDF dither amplitude in quantisation steps.
_AMOUNT_SCALE: dict[str, float] = {
    "none": 0.0,
    "off": 0.0,
    "low": 0.5,
    "medium": 1.0,
    "high": 2.0,
}


def dither_amount_scale(amount) -> float:
    """Return the TPDF amplitude (in quantisation steps) of an amount setting."""
    if amount is None:
        return 0.0
    if isinstance(amount, str):
        return _AMOUNT_SCALE.get(amount.strip().lower(), 1.0)
    return float(amount)


def noise_shaping_presets() -> dict[str, tuple[float, ...]]:
    """Return the named noise-shaping presets and their coefficients."""
    return dict(_NOISE_SHAPING_PRESETS)


def _resolve_noise_shaping(noise_shaping) -> tuple[float, ...]:
    """Normalise the ``noise_shaping`` argument into a tuple of coefficients."""
    if noise_shaping is None:
        return ()
    if isinstance(noise_shaping, str):
        key = noise_shaping.strip().lower()
        if key in _NOISE_SHAPING_PRESETS:
            return _NOISE_SHAPING_PRESETS[key]
        raise ValueError(f"unknown noise shaping preset {noise_shaping!r}")
    if isinstance(noise_shaping, (int, float)):
        return (float(noise_shaping),)
    coeffs = tuple(float(c) for c in noise_shaping)
    return coeffs


def bit_depth_step(bit_depth: int) -> float:
    """Quantisation step of a signed ``bit_depth`` bit signal."""
    bits = int(bit_depth)
    if bits < 2:
        raise ValueError("bit depth must be at least 2")
    return 2.0 ** (-(bits - 1))


def _quantize_scalar(v: float, step: float, limit: float) -> float:
    return math.floor(v / step + 0.5) * step


def _shape_and_quantize(
    sig: np.ndarray,
    step: float,
    noise: np.ndarray,
    coeffs: Sequence[float],
    limit: float = 1.0,
) -> np.ndarray:
    """Error-feedback quantiser with optional noise shaping."""
    if not coeffs:
        v = sig + noise
        q = np.floor(v / step + 0.5) * step
        return np.clip(q, -limit, limit - step)
    values = sig.tolist()
    noise_list = noise.tolist()
    out = [0.0] * len(values)
    hist = [0.0] * len(coeffs)
    taps = len(coeffs)
    floor = math.floor
    for n in range(len(values)):
        v = values[n] + noise_list[n]
        for i in range(taps):
            v += coeffs[i] * hist[i]
        q = floor(v / step + 0.5) * step
        e = v - q
        for i in range(taps - 1, 0, -1):
            hist[i] = hist[i - 1]
        hist[0] = e
        out[n] = q
    q = np.asarray(out, dtype=np.float64)
    return np.clip(q, -limit, limit - step)


def _silence_mask(x: np.ndarray, threshold_db: float, sr: int = 48000,
                  block_ms: float = 20.0) -> np.ndarray:
    """Boolean mask of the blocks that are below the silence threshold."""
    if x.size == 0:
        return np.zeros(0, dtype=bool)
    threshold = 10.0 ** (float(threshold_db) / 20.0)
    block = max(1, int(round(block_ms * 1e-3 * float(sr))))
    if block <= 1:
        return np.abs(x) < threshold
    n_blocks = int(math.ceil(x.size / block))
    padded = np.concatenate([x, np.zeros(n_blocks * block - x.size)])
    peaks = np.max(np.abs(padded.reshape(n_blocks, block)), axis=1)
    quiet = peaks < threshold
    mask = np.repeat(quiet, block)[: x.size]
    return mask


def _blank_mode(auto_blank) -> str:
    """Normalise the auto-blanking setting into ``'off'``/``'silence'``/``'quantized'``."""
    if auto_blank is None or auto_blank is False:
        return "off"
    if auto_blank is True:
        return "silence"
    text = str(auto_blank).strip().lower()
    if text in ("off", "none", "false", "0"):
        return "off"
    if text in ("silence", "on silence", "true", "1"):
        return "silence"
    if text in ("quantized", "if quantized", "quantised"):
        return "quantized"
    raise ValueError(f"unknown auto blanking mode {auto_blank!r}")


def dither(
    x,
    bit_depth: int = 16,
    noise_shaping="none",
    amount="medium",
    *,
    auto_blank=False,
    blank_threshold_db: float = -90.0,
    sr: int = 48000,
    seed=None,
    rng=None,
) -> np.ndarray:
    """Dither and requantise a signal to a true bit depth.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.  The shape is preserved.
    bit_depth:
        Target bit depth (8, 12, 16, 20, 24, ...).
    noise_shaping:
        ``'none'``, one of the presets returned by
        :func:`noise_shaping_presets`, or a list of error-feedback
        coefficients (a single element is a first-order filter).
    amount:
        ``'none'``, ``'low'``, ``'medium'`` or ``'high'``; controls the
        amplitude of the TPDF dither in quantisation steps.  A number is
        accepted as well.
    auto_blank:
        ``False``/``'off'`` keeps the noise everywhere, ``True``/``'silence'``
        forces exact zeros where the signal falls below
        ``blank_threshold_db`` and ``'quantized'`` additionally removes the
        dither from the samples that quantise to zero.
    blank_threshold_db:
        Silence threshold used by the auto blanking.
    sr:
        Sample rate, used for the block size of the silence detector.
    seed:
        Seed for the random number generator (reproducible dither).
    rng:
        Optional pre-built ``numpy`` generator; wins over ``seed``.

    Returns
    -------
    numpy.ndarray
        The quantised signal, same shape as the input.
    """
    buf = np.asarray(x, dtype=np.float64)
    single = buf.ndim == 1
    if single:
        buf = buf[:, None]
    if buf.ndim != 2:
        raise ValueError("audio must be 1-D or 2-D (samples, channels)")

    step = bit_depth_step(bit_depth)
    limit = 1.0
    coeffs = _resolve_noise_shaping(noise_shaping)
    scale = dither_amount_scale(amount)
    mode = _blank_mode(auto_blank)

    generator = rng if rng is not None else np.random.default_rng(seed)

    out = np.empty_like(buf)
    for ch in range(buf.shape[1]):
        sig = buf[:, ch].copy()
        mask = None
        if mode == "silence":
            mask = _silence_mask(sig, blank_threshold_db, sr)
        elif mode == "quantized":
            # samples that quantise to zero on their own are left at zero,
            # so that the dither does not lift the digital silence
            mask = np.abs(sig) < 0.5 * step
        if mask is not None:
            sig[mask] = 0.0
        if scale > 0.0:
            u1 = generator.random(sig.size)
            u2 = generator.random(sig.size)
            noise = scale * step * (u1 - u2)
        else:
            noise = np.zeros(sig.size, dtype=np.float64)
        if mask is not None:
            noise = np.where(mask, 0.0, noise)
        out[:, ch] = _shape_and_quantize(sig, step, noise, coeffs)
        if mask is not None:
            out[mask, ch] = 0.0
    return out[:, 0] if single else out


def quantize(x, bit_depth: int = 16) -> np.ndarray:
    """Requantise a signal to ``bit_depth`` bits without any dither."""
    buf = np.asarray(x, dtype=np.float64)
    single = buf.ndim == 1
    if single:
        buf = buf[:, None]
    step = bit_depth_step(bit_depth)
    q = np.floor(buf / step + 0.5) * step
    q = np.clip(q, -1.0, 1.0 - step)
    return q[:, 0] if single else q
