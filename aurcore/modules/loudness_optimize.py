"""
aurcore.modules.loudness_optimize — a slow compressor that lifts quiet parts.

The module follows the programme level slowly.  Material below the threshold
is lifted towards it, material above it is compressed, so the programme gets
louder without the loud passages growing as much as the quiet ones.  The
maximum lift is bounded by the boost control, which keeps the overall
loudness increase modest.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["LoudnessOptimize"]


@register
class LoudnessOptimize(Module):
    """Lift quiet passages towards a threshold with a slow compressor."""

    code = "AUR_LOUDNESSOPT"
    display_name = "Loudness Optimize"
    category = "Utility"
    edition = "standard"
    element = "LoudnessOptimize"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._frame = max(1, int(round(0.001 * sample_rate)))
        self._envelope = np.zeros(max(1, channels), dtype=np.float64)
        self._gain_db = np.zeros(max(1, channels), dtype=np.float64)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Boost",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=12.0,
                unit="dB",
                group="Boost",
                help="Maximum lift applied to quiet passages.",
            ),
            ParamDesc(
                name="Threshold",
                type="float",
                default=-12.0,
                minimum=-30.0,
                maximum=0.0,
                unit="dBFS",
                group="Compressor",
                help="Level the quiet passages are lifted towards.",
            ),
            ParamDesc(
                name="Ratio",
                type="float",
                default=4.0,
                minimum=1.0,
                maximum=20.0,
                group="Compressor",
                help="Compression ratio above the threshold.",
            ),
            ParamDesc(
                name="Speed",
                type="float",
                default=120.0,
                minimum=10.0,
                maximum=1000.0,
                unit="ms",
                group="Compressor",
                help="Time the level follower takes to settle.",
            ),
        ]

    def reset(self) -> None:
        """Clear the envelope and gain state."""
        self._envelope = np.zeros_like(self._envelope)
        self._gain_db = np.zeros_like(self._gain_db)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Optimise the loudness of a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        boost = float(self.params.get("Boost"))
        threshold = float(self.params.get("Threshold"))
        ratio = max(1.0, float(self.params.get("Ratio")))
        speed = float(self.params.get("Speed"))

        frame = self._frame
        n_frames = n // frame
        out = np.empty_like(buf)
        if n_frames == 0:
            return buf
        release = math.exp(-(frame / float(self.sample_rate)) / max(1e-6, speed / 1000.0))
        attack = math.exp(-(frame / float(self.sample_rate)) / 0.002)
        smoothing = math.exp(-(frame / float(self.sample_rate)) / 0.020)

        peaks = np.abs(buf[: n_frames * frame]).reshape(
            n_frames, frame, buf.shape[1]
        ).max(axis=1)
        for index in range(n_frames):
            peak = peaks[index]
            level = 20.0 * np.log10(np.maximum(peak, 1e-9))
            # A fast attack, a slow release: the follower only falls quickly.
            self._envelope = np.where(
                level > self._envelope,
                self._envelope + (level - self._envelope) * attack,
                self._envelope + (level - self._envelope) * release,
            )
            over = self._envelope - threshold
            # Below the threshold the signal is lifted towards it, above it the
            # compressor takes over.
            wanted = np.where(
                over > 0.0,
                -over * (1.0 - 1.0 / ratio),
                np.minimum(boost, -over),
            )
            self._gain_db = self._gain_db + (wanted - self._gain_db) * smoothing
            lo = index * frame
            hi = lo + frame
            out[lo:hi] = buf[lo:hi] * np.power(10.0, self._gain_db / 20.0)[None, :]
        if n_frames * frame < n:
            out[n_frames * frame:] = buf[n_frames * frame:] * np.power(
                10.0, self._gain_db / 20.0
            )[None, :]
        return out
