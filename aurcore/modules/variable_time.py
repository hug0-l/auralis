"""
aurcore.modules.variable_time — variable speed.

The module stretches or compresses the signal with a phase vocoder.  The
smoothing control decides how strongly the phase of neighbouring spectral
peaks is locked together: a low value leaves the classic freedom of the phase
vocoder, a high value keeps the sound stable.  The phase coherence control
blends directly between free phase and identity phase locking.

The module does not resample, so it does not need the look ahead of an
interpolation kernel, and the phase vocoder centres its analysis windows, so
it does not move the signal in time either: the module has no latency.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .time_pitch import PhaseVocoder

__all__ = ["VariableTime"]


@register
class VariableTime(Module):
    """Change the duration of the signal without changing the pitch."""

    code = "AUR_VARTIME"
    display_name = "Variable Time"
    category = "Utility"
    edition = "standard"
    element = "VariableTime"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._vocoder = PhaseVocoder(sample_rate, channels)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Ratio",
                type="float",
                default=1.0,
                minimum=0.125,
                maximum=8.0,
                group="Time",
                help="Duration multiplier; two means twice as long.",
            ),
            ParamDesc(
                name="Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Time",
                help="How strongly the phase of neighbouring peaks is locked.",
            ),
            ParamDesc(
                name="Phase Coherence",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Quality",
                help="Amount of identity phase locking.",
            ),
        ]

    def reset(self) -> None:
        """Clear the internal state."""
        self._vocoder.set_frame(37.0)

    @property
    def latency_samples(self) -> int:
        """The delay the module has, in the samples of its output.

        The module only stretches: the phase vocoder centres its analysis
        windows, so the output is aligned with the input and there is
        nothing to declare.
        """
        return 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """Stretch a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        ratio = float(self.params.get("Ratio"))
        smoothing = float(self.params.get("Smoothing"))
        coherence = float(self.params.get("Phase Coherence"))
        if abs(ratio - 1.0) < 1e-9:
            return buf
        self._vocoder.set_frame(37.0)
        target = max(1, int(round(n * ratio)))
        blend = min(1.0, max(0.0, coherence * 0.5 + smoothing * 0.5))
        out = np.empty((target, buf.shape[1]), dtype=np.float64)
        for channel in range(buf.shape[1]):
            out[:, channel] = self._vocoder.process(
                buf[:, channel],
                ratio,
                coherence=blend,
                transient=1.0 - smoothing,
            )
        return out
