"""
aurcore.modules.de_crackle — dense low level transient removal.

De-Crackle reuses the shared transient engine from :mod:`aurcore.modules.de_click`
with a different configuration: the detector concentrates on the high frequency
band where crackle lives, the threshold is biased by the *Amplitude Skew* and
the *Strength* control scales how much of the detected artefact is removed.

The engine is driven by :class:`aurcore.modules.transient_stream`, which
analyses the stream on a grid that does not depend on the size of the block
the host hands over.  A host that renders in small blocks therefore finds the
same crackle as one that renders the whole file in a single block; the module
declares the latency it gains (see
:attr:`DeCrackle.latency_samples`) and the host compensates it.
"""
from __future__ import annotations

import numpy as np

from .. import registry
from ..params import ParamDesc, desc
from .base import Module
from .de_click import Algorithm, TransientSettings
from .transient_stream import StreamingTransientRepair

__all__ = [
    "DeCrackle",
    "CRACKLE_BANDS",
    "CRACKLE_SENSITIVITY",
    "_CRACKLE_THRESHOLD_RANGE",
]

# Detection / repair band per quality setting (Low, Medium, High).
CRACKLE_BANDS: dict[str, tuple[float, float]] = {
    "Low": (3000.0, 12000.0),
    "Medium": (1500.0, 16000.0),
    "High": (800.0, 20000.0),
}

# Base detection sensitivity per quality setting.
CRACKLE_SENSITIVITY: dict[str, float] = {
    "Low": 2.0,
    "Medium": 3.0,
    "High": 4.0,
}

#: The threshold range of the **De-Crackle** module, as ``(low, high)``: the
#: factor the robust background of the material has to be exceeded by, at the
#: highest and at the lowest sensitivity.
#:
#: The range is the module's own, and not the shared one of the engine
#: (0.55 .. 3.0), because the shared range is far too high for a module
#: whose transients stand only a few times above their own background: the
#: quality levels mapped to 2.137, 1.803 and 1.522, and the *Low* level was
#: deaf to the crackle of its own probe (it removed 0.08 dB of it, while
#: the Medium level removed 4.89 dB).
#:
#: The population was measured in the module's own path (the detector sees
#: the material through the crackle band of the quality level):
#:
#: * a **steady bed** of broadband noise at -34 dBFS reaches a score of
#:   **0.473** (Low), **0.522** (Medium) and **0.510** (High),
#: * a **crackle of 0.3** on that bed reaches **0.921**, **0.924** and
#:   **1.098**,
#: * a **crackle of 0.9** reaches **2.943**, **3.910** and **4.857**,
#: * the **crackle of the probe** of the module is found up to a factor of
#:   1.35 (Low), 2.40 (Medium) and 4.50 (High): above those factors the
#:   detector does not see it any more and nothing is removed.
#:
#: The low end of the range is therefore **0.6**: above the score of the
#: steady bed, so that a steady bed is never even a candidate.  The high end
#: is **1.1**: below the factor at which the crackle of the probe is found
#: in every band, so that every quality level removes the crackle, and the
#: range runs through the population of the module (the crackle of 0.3
#: lies inside it, so the knob decides about it).
#:
#: The three quality levels sit at a factor of 0.974 (Low), 0.884 (Medium,
#: the default) and 0.802 (High): the default keeps the 4.89 dB of removal
#: of the probe with a wide margin, and the Low level is no longer deaf to
#: the crackle.
_CRACKLE_THRESHOLD_RANGE = (0.6, 1.1)


@registry.register
class DeCrackle(Module):
    """Remove dense, low level crackle from recordings."""

    code = "AUR_DECRACKLE"
    display_name = "De-Crackle"
    category = "Repair"
    edition = "standard"
    element = "DeClicker"
    analysis = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = StreamingTransientRepair(sample_rate, channels)

    # -- contract ------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's full parameter list."""
        return [
            desc(
                name="Quality",
                type="enum",
                default="Medium",
                minimum=1,
                maximum=3,
                choices=("Low", "Medium", "High"),
                group="Algorithm",
                help="Detection aggressiveness and the bandwidth that is repaired.",
            ),
            desc(
                name="Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Reduction",
                help="How much of the detected crackle is removed.",

                aliases=("Strength",)),
            desc(
                name="Amplitude Skew",
                type="float",
                default=0.0,
                minimum=-1.0,
                maximum=1.0,
                group="Amplitude Skew",
                help="Positive values favour quiet crackle, negative values favour loud crackle.",
            ),
            desc(
                name="Output Crackle Only",
                type="bool",
                default=False,
                group="Output",
                help="Render only the detected crackle instead of the repaired audio.",
            ),
        ]

    def settings(self) -> TransientSettings:
        """Build the engine settings from the current parameter values."""
        quality = str(self.params.get("Quality"))
        band = CRACKLE_BANDS.get(quality, CRACKLE_BANDS["Medium"])
        high = min(band[1], self.sample_rate * 0.5 - 1.0)
        return TransientSettings(
            algorithm=Algorithm.SINGLE_BAND,
            sensitivity=CRACKLE_SENSITIVITY.get(quality, 3.0),
            frequency_skew=0.0,
            click_widening=0.0,
            strength=float(self.params.get("Reduction")),
            amplitude_skew=float(self.params.get("Amplitude Skew")),
            # The detector works on the crackle band, the repair replaces a
            # wider band so that the whole transient disappears.
            band=(max(20.0, band[0] * 0.25), high),
            detect_band=band,
            # The range of the knob is the range of the module: a crackle
            # stands only a few times above its own background, and the
            # shared range of the engine is far too high for it.  See
            # :data:`_CRACKLE_THRESHOLD_RANGE`.
            threshold_range=_CRACKLE_THRESHOLD_RANGE,
        )

    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove the crackle from ``x`` (shape (n, ch))."""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        repaired, removed = self.engine.process(x, self.settings())
        if bool(self.params.get("Output Crackle Only")):
            return removed
        return repaired

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
        1876 samples after the input stopped; 1664 of them are the latency
        the module declares, so the tail is the 212 that are left.  The
        number carries a margin of 108 samples (2.25 ms), so that a small
        change of the repair does not break the contract.
        """
        return 320

    def reset(self) -> None:
        """Forget the running noise statistics."""
        self.engine.reset()
