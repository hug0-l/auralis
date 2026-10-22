"""
aurcore.modules.dither — TPDF dither and noise shaping.

The module reduces the signal to the chosen bit depth with a triangular
probability density dither and, optionally, an error feedback filter that
moves the resulting noise towards the top of the band.

The noise shaping filters are built from the family

    A(z) = (1 + r z**-1) ** N - 1

which gives a noise transfer function of ``1 / (1 + A(z))``.  The roots of
``1 + A(z)`` sit at ``-r`` with ``r < 1``, so the feedback loop is stable for
every shape, and the magnitude of the transfer function rises towards the
Nyquist frequency, which is where the shaped noise is wanted.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["DITHER_AMOUNTS", "Dither", "NOISE_SHAPES", "shaping_coefficients"]

#: Bit depths the quantiser can produce.
BIT_DEPTHS: tuple[str, ...] = ("24", "20", "16", "12", "8")

#: Available noise shaping filters.
NOISE_SHAPES: tuple[str, ...] = (
    "None",
    "Shape 1",
    "Shape 2",
    "Shape 3",
    "Shape 4",
    "Shape 5",
    "Ultra",
)

#: Dither levels as a fraction of the ideal triangular dither.
DITHER_AMOUNTS: dict[str, float] = {
    "None": 0.0,
    "Low": 0.5,
    "Medium": 1.0,
    "High": 2.0,
}

#: Blanking behaviours.
BLANKING: tuple[str, ...] = ("If Quantized", "On Silence", "Off")

#: (order, radius) of the noise shaping family.
_SHAPE_PARAMETERS: dict[str, tuple[int, float]] = {
    "None": (0, 0.0),
    "Shape 1": (1, 0.5),
    "Shape 2": (1, 0.7),
    "Shape 3": (2, 0.5),
    "Shape 4": (2, 0.65),
    "Shape 5": (3, 0.5),
    "Ultra": (3, 0.7),
}


def shaping_coefficients(name: str) -> list[float]:
    """Return the error feedback coefficients of a noise shaping filter.

    The coefficients are the ones of ``A(z) = (1 + r z**-1) ** N - 1``; an
    empty list means no shaping.
    """
    order, radius = _SHAPE_PARAMETERS.get(name, (0, 0.0))
    if order <= 0:
        return []
    coeffs = []
    for index in range(1, order + 1):
        coeffs.append(math.comb(order, index) * (radius ** index))
    return coeffs


@register
class Dither(Module):
    """Reduce the signal to a lower bit depth with dither and noise shaping."""

    code = "AUR_DITHER"
    display_name = "Dither"
    category = "Utility"
    edition = "standard"
    element = "Dither"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._rng = np.random.default_rng(0xA17E)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Bit Depth",
                type="enum",
                default="16",
                minimum=0.0,
                maximum=4.0,
                choices=BIT_DEPTHS,
                group="Output",
                help="Output word length.",
            ),
            ParamDesc(
                name="Noise Shaping",
                type="enum",
                default="None",
                minimum=0.0,
                maximum=6.0,
                choices=NOISE_SHAPES,
                group="Output",
                help="Error feedback filter applied to the quantisation error.",
            ),
            ParamDesc(
                name="Dither Amount",
                type="enum",
                default="Medium",
                minimum=0.0,
                maximum=3.0,
                choices=("None", "Low", "Medium", "High"),
                group="Output",
                help="Amount of dither added to the signal.",
            ),
            ParamDesc(
                name="Auto Blanking",
                type="enum",
                default="If Quantized",
                minimum=0.0,
                maximum=2.0,
                choices=BLANKING,
                group="Output",
                help="When the dither is suppressed.",
            ),
        ]

    def reset(self) -> None:
        """Restart the noise generator."""
        self._rng = np.random.default_rng(0xA17E)

    def _step_size(self) -> float:
        """Return the quantiser step for the selected bit depth."""
        bits = int(self.params.get("Bit Depth"))
        return 2.0 / float(1 << bits)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Dither and quantise a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        step = self._step_size()
        amount = DITHER_AMOUNTS.get(str(self.params.get("Dither Amount")), 1.0)
        coeffs = shaping_coefficients(str(self.params.get("Noise Shaping")))
        blanking = str(self.params.get("Auto Blanking"))

        if blanking == "On Silence":
            peak = float(np.max(np.abs(buf))) if buf.size else 0.0
            if peak <= 0.0 or 20.0 * math.log10(peak) < -90.0:
                return np.zeros_like(buf)

        out = np.empty_like(buf)
        channels = buf.shape[1]
        for channel in range(channels):
            source = buf[:, channel]
            if amount > 0.0:
                noise = self._rng.random(n) + self._rng.random(n) - 1.0
                source = source + noise * (0.5 * step * amount)
            quantised = self._quantise(source, step, coeffs)
            if blanking == "If Quantized":
                quiet = np.abs(buf[:, channel]) <= 0.5 * step
                quantised = np.where(quiet, 0.0, quantised)
            out[:, channel] = quantised
        return out

    @staticmethod
    def _quantise(
        source: np.ndarray, step: float, coeffs: list[float]
    ) -> np.ndarray:
        """Quantise a channel, optionally with an error feedback filter."""
        if not coeffs:
            return np.round(source / step) * step
        order = len(coeffs)
        values = source.tolist()
        history = [0.0] * order
        result = [0.0] * len(values)
        for index, value in enumerate(values):
            feedback = 0.0
            for tap in range(order):
                feedback += coeffs[tap] * history[tap]
            quantised = (value + feedback) / step
            quantised = math.floor(quantised + 0.5)
            quantised *= step
            error = value - quantised
            for tap in range(order - 1, 0, -1):
                history[tap] = history[tap - 1]
            history[0] = error
            result[index] = quantised
        return np.asarray(result, dtype=np.float64)
