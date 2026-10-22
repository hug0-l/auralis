"""
aurcore.modules.mouth_de_click — band limited mouth click removal.

Mouth clicks are short, bright transients produced by lips and teeth.  The
module shares the transient engine from :mod:`aurcore.modules.de_click` but
restricts both the detector and the repair to the mouth click band, so the
low frequency body of the recording is left untouched.

The engine is driven by :class:`aurcore.modules.transient_stream`, which
analyses the stream on a grid that does not depend on the size of the block
the host hands over.  A host that renders in small blocks therefore finds the
same click as one that renders the whole file in a single block; the module
declares the latency it gains (see
:attr:`MouthDeClick.latency_samples`) and the host compensates it.
"""
from __future__ import annotations

import numpy as np

from .. import registry
from ..params import ParamDesc, desc
from .base import Module
from .de_click import Algorithm, TransientSettings
from .transient_stream import StreamingTransientRepair

__all__ = ["MouthDeClick", "MOUTH_BAND", "_MOUTH_THRESHOLD_RANGE"]

# The band that carries mouth clicks.
MOUTH_BAND: tuple[float, float] = (500.0, 12000.0)

# The band that is replaced when a mouth click is repaired.  It is wider than
# the detection band so that the whole transient disappears, but it still
# leaves the low frequency rumble of the recording alone.
MOUTH_REPAIR_BAND: tuple[float, float] = (125.0, 20000.0)

#: The threshold range of the **Mouth De-Click** module, as
#: ``(low, high)``: the factor the robust background of the material has to
#: be exceeded by, at the highest and at the lowest sensitivity.
#:
#: The range is the module's own, and not the shared one of the engine
#: (0.55 .. 3.0), because the shared range lies *below* the whole population
#: of the transients of this module: the knob could not change anything.
#:
#: The population was measured in the module's own path (the detector sees
#: the material through the mouth band, 500 Hz - 12 kHz):
#:
#: * the **steady material** (the programme of the journey: a 1 kHz tone, a
#:   50 Hz hum and a little noise) reaches a score of **5.010**,
#: * a **0.5 ms burst** on that programme reaches **9.285** (the four bursts
#:   of the quality case measure 9.285, 9.352, 9.402 and 9.528),
#: * a **click of 0.3** on the same programme reaches **10.902**,
#: * a **click of 0.9** reaches **27.133**, the **mouth clicks** of the voice
#:   case reach **26.950** and the six **impulses of the corpus clip** reach
#:   27.450 to 27.625.
#:
#: The low end of the range is therefore **6.0**: above the score of the
#: steady material, so that a steady signal is never even a candidate (the
#: programme comes out bit exact at every setting of the knob).  The high end
#: is **11.0**: below the score of a mouth click, so that the click is
#: repaired at every setting of the knob, and above the score of the click
#: of 0.3, so that the knob decides about it.  The range runs through the
#: population of the module: the 0.5 ms bursts and the click of 0.3 lie
#: inside it, and the knob decides about both.
#:
#: The default (Sensitivity 3.0) sits at a factor of **9.171**, which is
#: below the score of the quietest 0.5 ms burst (9.285): the default removes
#: everything it removed before the range was given to the module.
_MOUTH_THRESHOLD_RANGE = (6.0, 11.0)


@registry.register
class MouthDeClick(Module):
    """Remove mouth clicks from dialogue while keeping the low end intact."""

    code = "AUR_MOUTHDECLICK"
    display_name = "Mouth De-Click"
    category = "Repair"
    edition = "standard"
    element = "MouthDeclick"
    analysis = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = StreamingTransientRepair(sample_rate, channels)

    # -- contract ------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's full parameter list."""
        return [
            desc(
                name="Sensitivity",
                type="float",
                default=3.0,
                minimum=0.0,
                maximum=10.0,
                group="Sensitivity",
                help="How eagerly mouth clicks are detected.",
            ),
            desc(
                name="Frequency Skew",
                type="float",
                default=0.0,
                minimum=-1.0,
                maximum=1.0,
                group="Frequency Skew",
                help="Bias detection towards high (positive) or low (negative) frequencies.",
            ),
            desc(
                name="Click Widening",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=1.0,
                group="Click Widening",
                help="Widens the spectral band that is replaced around a detected click.",
            ),
            desc(
                name="Output Clicks Only",
                type="bool",
                default=False,
                group="Output",
                help="Render only the detected click signal instead of the repaired audio.",
            ),
        ]

    def settings(self) -> TransientSettings:
        """Build the engine settings from the current parameter values."""
        high = min(MOUTH_REPAIR_BAND[1], self.sample_rate * 0.5 - 1.0)
        detect_high = min(MOUTH_BAND[1], self.sample_rate * 0.5 - 1.0)
        return TransientSettings(
            algorithm=Algorithm.SINGLE_BAND,
            sensitivity=float(self.params.get("Sensitivity")),
            frequency_skew=float(self.params.get("Frequency Skew")),
            click_widening=float(self.params.get("Click Widening")),
            strength=1.0,
            band=(MOUTH_REPAIR_BAND[0], high),
            detect_band=(MOUTH_BAND[0], detect_high),
            # The range of the knob is the range of the module: the shared
            # range of the engine lies below the whole population of the
            # transients of this module, so the knob could not change
            # anything.  See :data:`_MOUTH_THRESHOLD_RANGE`.
            threshold_range=_MOUTH_THRESHOLD_RANGE,
        )

    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove the mouth clicks from ``x`` (shape (n, ch))."""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        repaired, removed = self.engine.process(x, self.settings())
        if bool(self.params.get("Output Clicks Only")):
            return removed
        return repaired

    def reset(self) -> None:
        """Forget the running noise statistics."""
        self.engine.reset()

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the repair)."""
        return int(self.engine.latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The repair fits a partial over a window of material, and the frames
        that follow the last one still hold samples of the input.  Measured
        the way the host measures it, the output stays above -60 dBFS for
        1889 samples after the input stopped; 1664 of them are the latency
        the module declares, so the tail is the 225 that are left.  The
        number carries a margin of 95 samples (2 ms), so that a small
        change of the repair does not break the contract.
        """
        return 320
