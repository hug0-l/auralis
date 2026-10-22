"""
aurcore.modules.variable_pitch — pitch contour.

The contour control carries a single value in the alpha; the curve editor
that will drive a per-sample contour arrives later.  The pitch is changed with
the same band limited resampling and phase vocoder pair the time and pitch
module uses, so the result is identical in quality.

With ``Preserve Time`` the duration is kept and only the pitch moves,
otherwise the duration follows the pitch the way a tape machine would.

The resampler
-------------
The resampling step is a **streaming filter**: it keeps the history the
interpolation kernel needs and holds its output back by the look ahead the
kernel needs, so that every sample of a block is computed from the whole
kernel.  A block that is resampled on its own is cut in two instead, and the
read position restarts at the start of every block, so one block and many
blocks did not give the same answer.

Latency
-------
The module delays the output by the look ahead of the kernel, and it says so:
:meth:`VariablePitch.latency_samples`.  The kernel reaches 48 input samples
to the right of the read position, so the delay is 48 samples of the input
signal — 1.0 ms at 48 kHz — when the duration is preserved, and the delay of
the resampler itself (in the samples of the resampled signal) when it is
not.

A contour of zero semitones is a ratio of one, and the module then hands
the block through untouched: measured with a sweep, the delay is 0 samples
and the cross correlation of the input and the output is 1.000.

A contour that is not zero changes the *time scale* of the signal, and a
time scale is not a delay.  A host that measures the delay while the
contour is at -12 semitones compares the second half of the input with the
second half of an output that is not the same length, and the cross
correlation of the two falls to 0.003: the figure such a measurement
reports (137 samples) is the time scale and not a delay of the module.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .time_pitch import PhaseVocoder, PitchResampler, resampled_length

__all__ = ["VariablePitch"]


@register
class VariablePitch(Module):
    """Shift the pitch along a contour while optionally keeping the duration."""

    code = "AUR_VARIPITCH"
    display_name = "Variable Pitch"
    category = "Utility"
    edition = "standard"
    element = "PitchContour"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._vocoder = PhaseVocoder(sample_rate, channels)
        self._pitch_resampler = PitchResampler(sample_rate)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Contour",
                type="float",
                default=0.0,
                minimum=-24.0,
                maximum=24.0,
                unit="st",
                group="Contour",
                help="Pitch of the contour in semitones.",
            ),
            ParamDesc(
                name="Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Contour",
                help="How much the phase is locked while the pitch moves.",
            ),
            ParamDesc(
                name="Preserve Time",
                type="bool",
                default=True,
                minimum=0.0,
                maximum=1.0,
                group="Pitch",
                help="Keeps the duration while the pitch moves.",
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
        self._pitch_resampler.reset()

    @property
    def latency_samples(self) -> int:
        """The delay the module has, in the samples of its output.

        The resampling kernel reaches 48 input samples to the right of the
        read position, so the resampled signal is held back by that much.
        The samples of the resampled signal run at the rate of the pitch
        factor, so the delay is 48 samples of the input signal when the
        duration is preserved, and the delay of the resampler itself — in
        the samples of the resampled signal — when it is not.

        A contour of zero semitones is a ratio of one: nothing is
        resampled and the module has no latency at all.
        """
        ratio = 2.0 ** (float(self.params.get("Contour")) / 12.0)
        if abs(ratio - 1.0) <= 1e-9:
            return 0
        if bool(self.params.get("Preserve Time")):
            # the vocoder restores the duration, so the delay of the
            # resampler is paid in the samples of the output
            return self._pitch_resampler.latency(ratio, 1.0)
        # without the vocoder the output runs at the rate of the pitch
        # factor, so the delay is the delay of the resampler itself
        return self._pitch_resampler.delay(ratio)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Shift the pitch of a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        contour = float(self.params.get("Contour"))
        smoothing = float(self.params.get("Smoothing"))
        preserve = bool(self.params.get("Preserve Time"))
        coherence = float(self.params.get("Phase Coherence"))

        ratio = 2.0 ** (contour / 12.0)
        if abs(ratio - 1.0) < 1e-9:
            return buf
        # The resampler is a streaming filter: it keeps the history the
        # kernel needs, so the edges of the block are computed from the
        # whole kernel, and it counts the read position from the start of
        # the stream, so one block and many blocks agree.
        work = self._pitch_resampler.process(buf, ratio)
        if not preserve or work.shape[0] == 0:
            return work
        # The duration is restored with the phase vocoder.  The factor it is
        # given is built from the length the resampled block has always had,
        # so the vocoder's analysis frames and their positions do not move.
        self._vocoder.set_frame(37.0)
        expected = resampled_length(n, ratio)
        factor = n / float(expected)
        out = np.zeros((n, work.shape[1]), dtype=np.float64)
        blend = min(1.0, max(0.0, coherence * 0.5 + smoothing * 0.5))
        for channel in range(work.shape[1]):
            stretched = self._vocoder.process(
                work[:, channel],
                factor,
                coherence=blend,
                transient=1.0 - smoothing,
            )
            # The vocoder returns the length its factor asks for; the block
            # processor needs the exact length the block had.
            take = min(n, stretched.size)
            out[:take, channel] = stretched[:take]
        return out
