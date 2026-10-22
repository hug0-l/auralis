"""
aurcore.modules.streaming_preview — preview of a lossy delivery chain.

The module is a monitoring aid, not an encoder: it brings the programme to
the playback loudness, keeps the result under a true-peak ceiling and then
applies a spectral approximation of what a given codec and quality setting
would do to the top of the band.

Block coherence
---------------
The playback loudness is measured on the **fixed grid** of
:class:`aurcore.modules.loudness_control.LoudnessGrid`: a step of a tenth of
a second that does not move with the size of the block the host hands over.
The signal is held back by one step of the grid, so the gain of the step
that is being played is already known when it is played, and the preview
reaches the same answer whether the host renders in 64 sample blocks or in
one block.  The delay is the latency the module declares (see
:attr:`StreamingPreview.latency_samples`); the host compensates it.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .loudness_control import GRID_STEP_S, LoudnessGrid

__all__ = ["CODEC_PROFILES", "StreamingPreview"]

#: Cutoff frequency per codec and quality, in hertz.  ``None`` means the
#: signal is left untouched (lossless delivery).
CODEC_PROFILES: dict[str, dict[str, float | None]] = {
    "Lossless": {"Low": None, "Medium": None, "High": None},
    "OGG": {"Low": 7500.0, "Medium": 10000.0, "High": 12500.0},
    "MP3": {"Low": 8000.0, "Medium": 11000.0, "High": 13500.0},
    "AAC": {"Low": 10000.0, "Medium": 13000.0, "High": 15000.0},
    "OPUS": {"Low": 9000.0, "Medium": 12000.0, "High": 14000.0},
}


def _lowpass_biquad(freq: float, q: float, sample_rate: int):
    """Design a second order low-pass biquad."""
    fs = float(sample_rate)
    w0 = 2.0 * math.pi * min(max(freq, 20.0), 0.45 * fs) / fs
    alpha = math.sin(w0) / (2.0 * max(q, 0.05))
    cos_w0 = math.cos(w0)
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


@register
class StreamingPreview(Module):
    """Preview how the programme would sound after lossy delivery."""

    code = "AUR_STREAMPREVIEW"
    display_name = "Streaming Preview"
    category = "Utility"
    edition = "standard"
    element = "StreamingPreview"

    #: True peak ceiling of the preview.
    CEILING_DBTP = -1.0

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._grid = LoudnessGrid(sample_rate, channels, GRID_STEP_S)
        self._states: list[np.ndarray] = []
        self._key: tuple | None = None
        self._energy = 0.0
        self._count = 0
        self._peak = 0.0
        self._target = -16.0
        self._allow_positive = True

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Playback Loudness",
                type="float",
                default=-16.0,
                minimum=-36.0,
                maximum=0.0,
                unit="LUFS",
                group="Preview",
                help="Loudness the preview is played back at.",
            ),
            ParamDesc(
                name="Apply Positive Gain",
                type="bool",
                default=True,
                minimum=0.0,
                maximum=1.0,
                group="Preview",
                help="Allows the preview to be louder than the incoming signal.",
            ),
            ParamDesc(
                name="Codec",
                type="enum",
                default="Lossless",
                minimum=0.0,
                maximum=4.0,
                choices=tuple(CODEC_PROFILES.keys()),
                group="Preview",
                help="Delivery format the preview imitates.",
            ),
            ParamDesc(
                name="Quality",
                type="enum",
                default="Medium",
                minimum=0.0,
                maximum=2.0,
                choices=("Low", "Medium", "High"),
                group="Preview",
                help="Quality of the delivery format.",
            ),
        ]

    def reset(self) -> None:
        """Clear the grid, the filter state and the measurement."""
        self._grid.reset()
        self._states = []
        self._key = None
        self._energy = 0.0
        self._count = 0
        self._peak = 0.0

    @property
    def latency_samples(self) -> int:
        """The delay the module puts into the signal, in samples.

        The grid holds the signal back by one step, so the gain of the step
        that is being played is already known when it is played.
        """
        return int(self._grid.latency_samples)

    def _cutoff(self) -> float | None:
        """Return the cutoff of the selected codec and quality."""
        codec = str(self.params.get("Codec"))
        quality = str(self.params.get("Quality"))
        return CODEC_PROFILES.get(codec, {}).get(quality)

    def _apply_band_limit(self, x: np.ndarray) -> np.ndarray:
        """Apply the spectral approximation of the selected codec."""
        cutoff = self._cutoff()
        if cutoff is None:
            return x
        key = (cutoff, x.shape[1])
        if self._key != key or len(self._states) != 2:
            self._states = [
                np.zeros((2, x.shape[1]), dtype=np.float64) for _ in range(2)
            ]
            self._key = key
        out = x
        for _ in range(2):
            b0, b1, b2, a1, a2 = _lowpass_biquad(cutoff, 0.7071, self.sample_rate)
            state = self._states.pop(0)
            try:
                from scipy.signal import lfilter

                out, zf = lfilter([b0, b1, b2], [1.0, a1, a2], out, axis=0, zi=state)
                state[:, :] = zf
            except Exception:  # pragma: no cover - numpy fallback
                out = self._fallback_biquad((b0, b1, b2, a1, a2), out, state)
            self._states.append(state)
        return out

    @staticmethod
    def _fallback_biquad(coeffs, x: np.ndarray, state: np.ndarray) -> np.ndarray:
        """Causal biquad without scipy."""
        b0, b1, b2, a1, a2 = coeffs
        n, ch = x.shape
        y = np.empty_like(x)
        z0 = np.array(state[0], copy=True)
        z1 = np.array(state[1], copy=True)
        for c in range(ch):
            s0 = float(z0[c])
            s1 = float(z1[c])
            col = x[:, c]
            out = np.empty(n, dtype=np.float64)
            for i in range(n):
                xi = float(col[i])
                yi = b0 * xi + s0
                s0 = b1 * xi - a1 * yi + s1
                s1 = b2 * xi - a2 * yi
                out[i] = yi
            y[:, c] = out
            state[0, c] = s0
            state[1, c] = s1
        return y

    def _control(self, level_db: float, peak: float) -> float:
        """The gain of one step of the grid, in decibels.

        The gain brings the programme onto the playback loudness.  The
        measurement is the running integrated loudness of the material, and
        the ceiling is checked against the loudest peak the material has
        reached, so the answer does not depend on the size of the block the
        host hands over.
        """
        power = (
            10.0 ** ((level_db + 0.691) / 10.0)
            if math.isfinite(level_db)
            else 0.0
        )
        self._energy += power
        self._count += 1
        if self._energy <= 0.0:
            # the material is silent: there is nothing to correct, and the
            # gain of the silence does not matter
            return 0.0
        integrated = -0.691 + 10.0 * math.log10(self._energy / self._count)
        gain = self._target - integrated
        if not self._allow_positive:
            gain = min(0.0, gain)
        else:
            self._peak = max(self._peak, peak)
            if self._peak > 0.0:
                peak_db = 20.0 * math.log10(self._peak)
                gain = min(gain, self.CEILING_DBTP - peak_db)
        return gain

    def process(self, x: np.ndarray) -> np.ndarray:
        """Shape a block of audio for a lossy delivery preview."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        self._target = float(self.params.get("Playback Loudness"))
        self._allow_positive = bool(self.params.get("Apply Positive Gain"))
        out = self._grid.process(buf, self._control)
        out = self._apply_band_limit(out)
        return out
