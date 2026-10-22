"""
aurcore.modules.gain — the linear gain stage and the shared gain engine.

``Gain``, ``Fade`` and ``Normalize`` all drive the same runtime element, so the
maths they have in common lives here: one linear-gain helper, the family of
fade curves and the peak-normalisation helper.  The three module classes import
the engine from this file so the behaviour of a fade-in, a fade-out and a
normalisation pass is defined exactly once.

All helpers work on ``(n_samples, n_channels)`` float64 blocks.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

#: The fade shapes offered by the fade control.
FADE_TYPES: tuple[str, ...] = ("Log", "Linear", "Cosine", "Equal Power")

__all__ = ["FADE_TYPES", "Gain", "GainEngine"]


class GainEngine:
    """Shared gain maths for the gain element (Gain / Fade / Normalize).

    The methods are static so callers can use the engine without owning an
    instance; a module keeps one for convenience.
    """

    @staticmethod
    def linear_gain(gain_db: float) -> float:
        """Convert a gain in decibels to a linear amplitude factor."""
        if gain_db == 0.0:
            return 1.0
        return 10.0 ** (gain_db / 20.0)

    @staticmethod
    def apply_gain_db(x: np.ndarray, gain_db: float) -> np.ndarray:
        """Return ``x`` scaled by ``gain_db`` decibels (a new array)."""
        if gain_db == 0.0:
            return x
        return x * GainEngine.linear_gain(gain_db)

    @staticmethod
    def fade_ramp(length: int, kind: str = "Log") -> np.ndarray:
        """Return a monotone 0..1 fade ramp of ``length`` samples.

        ``Log`` is a constant-decibel ramp from -60 dB to 0 dB, ``Linear`` is a
        straight line, ``Cosine`` is a raised cosine and ``Equal Power`` keeps
        the power of the faded portion constant.
        """
        n = int(length)
        if n <= 0:
            return np.zeros(0, dtype=np.float64)
        if n == 1:
            return np.zeros(1, dtype=np.float64)
        t = np.arange(n, dtype=np.float64) / float(n - 1)
        if kind == "Linear":
            return t
        if kind == "Cosine":
            return 0.5 * (1.0 - np.cos(np.pi * t))
        if kind == "Equal Power":
            return np.sin(0.5 * np.pi * t)
        # "Log" (and any unknown shape) falls back to the decibel-linear ramp.
        return np.power(10.0, 3.0 * (t - 1.0))

    @classmethod
    def apply_fade(
        cls,
        x: np.ndarray,
        kind: str,
        length_s: float,
        sample_rate: int,
        selection: tuple[int, int] | None = None,
    ) -> np.ndarray:
        """Fade the head and the tail of ``x`` over ``length_s`` seconds.

        ``selection`` is an optional ``(start, end)`` sample range; the fade is
        then applied to the head and the tail of that range instead of the head
        and the tail of the whole block.  Overlapping head/tail fades multiply.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        total = buf.shape[0]
        if total == 0:
            return buf
        if selection is None:
            start, end = 0, total
        else:
            start = max(0, int(selection[0]))
            end = min(total, int(selection[1]))
        span = end - start
        if span <= 0:
            return buf
        n_fade = int(round(float(length_s) * float(sample_rate)))
        if n_fade <= 0:
            return buf
        n_fade = min(n_fade, span)
        ramp = cls.fade_ramp(n_fade, kind)
        curve = np.ones(span, dtype=np.float64)
        curve[:n_fade] *= ramp
        curve[-n_fade:] *= ramp[::-1]
        out = buf.copy()
        out[start:end] *= curve[:, None]
        return out

    @staticmethod
    def peak_dbfs(x: np.ndarray) -> float:
        """Return the peak level of ``x`` in dBFS (``-inf`` for silence)."""
        if x.size == 0:
            return -math.inf
        peak = float(np.max(np.abs(x)))
        if peak <= 0.0:
            return -math.inf
        return 20.0 * math.log10(peak)

    @classmethod
    def normalize_peak(
        cls, x: np.ndarray, target_dbfs: float, reference_peak: float | None = None
    ) -> tuple[np.ndarray, float]:
        """Peak-normalise ``x`` to ``target_dbfs`` dBFS.

        ``reference_peak`` lets a caller reuse a previously measured peak so a
        file keeps one consistent gain across several blocks.  Returns the
        scaled buffer and the applied gain in decibels.
        """
        buf = np.asarray(x, dtype=np.float64)
        peak = cls.peak_dbfs(buf) if reference_peak is None else float(reference_peak)
        if not math.isfinite(peak):
            return buf, 0.0
        gain_db = float(target_dbfs) - peak
        return cls.apply_gain_db(buf, gain_db), gain_db


@register
class Gain(Module):
    """Linear gain stage in decibels."""

    code = "AUR_GAIN"
    display_name = "Gain"
    category = "Utility"
    edition = "standard"
    element = "GainStage"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = GainEngine()

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Gain",
                type="float",
                default=0.0,
                minimum=-60.0,
                maximum=12.0,
                unit="dB",
                group="Gain",
                help="Output gain applied to the signal.",
                curve="taper",
            ),
        ]

    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the gain to a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.size == 0:
            return buf
        return self.engine.apply_gain_db(buf, float(self.params.get("Gain")))
