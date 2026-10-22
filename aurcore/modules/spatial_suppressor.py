"""
aurcore.modules.spatial_suppressor — the spatial suppressor as a module.

The module removes the content that does **not** belong to the stereo image
from the image: the sound that lives in one channel only, and the sound that
lives in the anti phase relation between the channels.  What it leaves is the
content the channels agree on.

The work is done by the primitive of :mod:`aurcore.dsp.spatial`, which
computes the inter channel coherence — the share of the power of one channel
that the other channel explains — and turns it into a mask.  The mask is a
real gain between zero and one, so the module **attenuates** and never
invents: unlike a separator that re-synthesises a stem, it cannot add the
roughness a re-synthesis adds.

The module is transparent where there is nothing to separate: a mono signal
has no spatial relation at all, and a signal whose two channels are the same
signal is left alone.
"""
from __future__ import annotations

import numpy as np

from ..dsp.spatial import MAX_EDGE, MIN_EDGE, SpatialSuppressor
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["SpatialSuppressorModule"]


@register
class SpatialSuppressorModule(Module):
    """Spatial Suppressor — remove the content the channels do not share.

    Parameters
    ----------
    Reduction:
        How much of the content that does not belong to the image is
        removed.  0 leaves the signal untouched, 1 removes all of it.
    Artifact Smoothing:
        The width of the soft edge around the decision.  A narrow edge makes
        the decision sharp, a wide one trades the removal for a smoother
        gain curve.
    """

    code = "AUR_SPATIALSUPPRESSOR"
    display_name = "Spatial Suppressor"
    category = "Repair"
    edition = "advanced"
    element = "ChannelOperations"
    analysis = False
    supports_learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._suppressor = SpatialSuppressor(sample_rate, channels)

    # -- the parameters -------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Reduction",
                type="float",
                default=1.0,
                minimum=0.0,
                maximum=1.0,
                group="Reduction",
                help=(
                    "How much of the content the channels do not share is removed. "
                    "At 0 nothing is touched, at 1 all of it is removed."
                ),
                aliases=("Reduction Strength",),
            ),
            desc(
                name="Artifact Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help=(
                    "The width of the soft edge around the decision. A high value "
                    "widens the edge and trades the removal for a smoother gain "
                    "curve."
                ),
            ),
        ]

    # -- the delay ------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """The delay the module adds, in samples."""
        return int(self._suppressor.latency_samples)

    def reset(self) -> None:
        """Forget the estimate and the mask."""
        self._suppressor.reset()

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove the content the channels do not share from a block."""
        smoothing = float(self.params.get("Artifact Smoothing"))
        self._suppressor.strength = float(self.params.get("Reduction"))
        self._suppressor.edge = MIN_EDGE + (MAX_EDGE - MIN_EDGE) * smoothing
        return self._suppressor.process(x)
