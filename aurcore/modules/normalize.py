"""
aurcore.modules.normalize — peak normalisation.

The normalisation maths is shared with the other two modules of the gain
element and is imported from :mod:`aurcore.modules.gain`.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .gain import GainEngine

__all__ = ["Normalize"]


@register
class Normalize(Module):
    """Peak-normalise the signal to a target level in dBFS."""

    code = "AUR_NORMALIZE"
    display_name = "Normalize"
    category = "Utility"
    edition = "standard"
    element = "GainStage"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = GainEngine()
        # The loudest peak seen so far; keeps one gain for the whole file when
        # the engine delivers the audio in several blocks.
        self._reference_peak_db: float | None = None

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Target Peak Level",
                type="float",
                default=-1.0,
                minimum=-24.0,
                maximum=0.0,
                unit="dBFS",
                group="Gain",
                help="Peak level the signal is normalised to.",
            ),
        ]

    def reset(self) -> None:
        """Forget the measured peak."""
        self._reference_peak_db = None

    def process(self, x: np.ndarray) -> np.ndarray:
        """Normalise a block of audio to the target peak level."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.size == 0:
            return buf
        block_peak = self.engine.peak_dbfs(buf)
        if math.isfinite(block_peak):
            if self._reference_peak_db is None:
                self._reference_peak_db = block_peak
            else:
                self._reference_peak_db = max(self._reference_peak_db, block_peak)
        target = float(self.params.get("Target Peak Level"))
        out, _gain = self.engine.normalize_peak(
            buf, target, self._reference_peak_db
        )
        return out
