"""
aurcore.modules.leveler — slow gain riding towards a loudness target.

The leveler measures the programme loudness and rides a gain so the material
sits on the requested target.  Dialogue leans on the three second short-term
loudness, music on the 400 ms momentary loudness, so the correction reacts at
a pace that suits the material.  A limiter catches the peaks the riding gain
creates and an optional de-esser control keeps sibilance in check.

Block coherence
---------------
The level is measured on the **fixed grid** of
:class:`aurcore.modules.loudness_control.LoudnessGrid`: a step of a tenth of
a second that does not move with the size of the block the host hands over.
The signal is held back by one step of the grid, so the gain of the step that
is being played is already known when it is played, and the leveler reaches
the same answer whether the host renders in 64 sample blocks or in one
block.  The delay is the latency the module declares (see
:attr:`Leveler.latency_samples`); the host compensates it.

The loudness meter and the limiter are shared with the other loudness
modules and are imported from :mod:`aurcore.modules.loudness_control`.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .loudness_control import GRID_STEP_S, LoudnessGrid, PeakLimiter

__all__ = ["Leveler", "SibilanceDetector"]

try:  # pragma: no cover - scipy is optional, a numpy fallback is provided
    from scipy.signal import lfilter as _sp_lfilter
except Exception:  # pragma: no cover
    _sp_lfilter = None


def _highpass(freq: float, q: float, sample_rate: int):
    """Design a second order high-pass biquad."""
    fs = float(sample_rate)
    w0 = 2.0 * math.pi * min(max(freq, 20.0), 0.45 * fs) / fs
    alpha = math.sin(w0) / (2.0 * max(q, 0.05))
    cos_w0 = math.cos(w0)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


class SibilanceDetector:
    """Detects sibilance and returns the gain that should be applied.

    A high-pass around 6.5 kHz isolates the sibilance band; the envelope of
    that band is followed with a fast attack and a slow release and compared
    against a threshold.

    The frames of the detector sit on a fixed grid of a millisecond, so the
    envelope is the same whether the host hands the detector one block or
    many.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        freq: float = 6500.0,
        q: float = 0.7,
        threshold_dbfs: float = -32.0,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.threshold_dbfs = threshold_dbfs
        self._coeffs = _highpass(freq, q, sample_rate)
        self._state = np.zeros((2, max(1, channels)), dtype=np.float64)
        self._envelope = np.zeros(max(1, channels), dtype=np.float64)
        self._frame = max(1, int(round(0.001 * sample_rate)))
        self._attack = math.exp(-(self._frame / float(sample_rate)) / 0.002)
        self._release = math.exp(-(self._frame / float(sample_rate)) / 0.060)
        # the samples of the frame that is not complete yet
        self._pending = np.zeros((0, max(1, channels)), dtype=np.float64)

    def reset(self) -> None:
        """Clear the filter and envelope state."""
        self._state = np.zeros_like(self._state)
        self._envelope = np.zeros_like(self._envelope)
        self._pending = np.zeros((0, self._envelope.shape[0]), dtype=np.float64)

    def _filter(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2, a1, a2 = self._coeffs
        if _sp_lfilter is not None:
            y, zf = _sp_lfilter([b0, b1, b2], [1.0, a1, a2], x, axis=0, zi=self._state)
            self._state[:, :] = zf
            return y
        n, ch = x.shape
        y = np.empty_like(x)
        z0 = np.array(self._state[0], copy=True)
        z1 = np.array(self._state[1], copy=True)
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
            self._state[0, c] = s0
            self._state[1, c] = s1
        return y

    def gains(self, x: np.ndarray, max_reduction_db: float) -> np.ndarray:
        """Return a per-sample gain array that ducks sibilance."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0 or max_reduction_db <= 0.0:
            return np.ones((n, buf.shape[1]), dtype=np.float64)
        band = np.abs(self._filter(buf))
        # the frames sit on a fixed grid: the samples of the frame that is not
        # complete yet wait for the next block
        data = np.concatenate((self._pending, band), axis=0)
        n_frames = data.shape[0] // self._frame
        usable = n_frames * self._frame
        out = np.ones((n, buf.shape[1]), dtype=np.float64)
        threshold = 10.0 ** (self.threshold_dbfs / 20.0)
        if n_frames:
            frame_peak = data[:usable].reshape(n_frames, self._frame, -1).max(axis=1)
            for index in range(n_frames):
                peak = frame_peak[index]
                level = 20.0 * np.log10(np.maximum(peak, 1e-12))
                over = np.clip((level - self.threshold_dbfs) / 10.0, 0.0, 1.0)
                target_db = -max_reduction_db * over
                attack = target_db < self._envelope
                self._envelope = np.where(
                    attack, target_db, self._envelope + (target_db - self._envelope) * self._release
                )
                lo = index * self._frame
                hi = lo + self._frame
                out[lo:hi] = np.power(10.0, self._envelope / 20.0)[None, :]
        self._pending = data[usable:]
        if usable < n:
            out[usable:] = np.power(10.0, self._envelope / 20.0)[None, :]
        return out


@register
class Leveler(Module):
    """Ride the programme loudness onto a target level.

    The measurement runs on the fixed grid of the shared
    :class:`aurcore.modules.loudness_control.LoudnessGrid`, and the signal is
    held back by one step of that grid, so the gain of the step that is being
    played is already known when it is played.  A host that renders the
    material in 64 sample blocks and a host that renders it in one block
    therefore reach the same gain at the same moment of the material.
    """

    code = "AUR_LEVELER"
    display_name = "Leveler"
    category = "Utility"
    edition = "advanced"
    element = "Leveler"

    #: How many steps of the grid the momentary window (400 ms) and the short
    #: term window (3 s) hold.
    _MOMENTARY_STEPS = 4
    _SHORT_TERM_STEPS = 30

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._grid = LoudnessGrid(sample_rate, channels, GRID_STEP_S)
        self._limiter = PeakLimiter(sample_rate, channels, look_ahead=True)
        self._sibilance = SibilanceDetector(sample_rate, channels)
        self._gain_db = 0.0
        self._seeded = False
        self._powers: list[float] = []
        self._target = -23.0
        self._step = 0.0
        self._dialogue = True

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Optimize For",
                type="enum",
                default="Dialogue",
                minimum=0.0,
                maximum=1.0,
                choices=("Dialogue", "Music"),
                group="Algorithm",
                help="Dialogue rides the short-term loudness, music the momentary one.",
            ),
            ParamDesc(
                name="Target Level",
                type="float",
                default=-23.0,
                minimum=-40.0,
                maximum=0.0,
                unit="LUFS",
                group="Target",
                help="Loudness the leveler steers towards.",
            ),
            ParamDesc(
                name="Responsiveness",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Response",
                help="How quickly the leveler follows the target.",
            ),
            ParamDesc(
                name="Preserve Dynamics",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Dynamics",
                help="Keeps the original level differences; less gain riding.",
            ),
            ParamDesc(
                name="Ess Reduction",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=12.0,
                unit="dB",
                group="Ess",
                help="Amount of sibilance reduction.",
            ),
            ParamDesc(
                name="Limiter",
                type="float",
                default=-1.0,
                minimum=-12.0,
                maximum=0.0,
                unit="dBTP",
                group="Limiter",
                help="True-peak ceiling of the output.",
            ),
        ]

    def reset(self) -> None:
        """Clear the grid, the limiter and the applied gain."""
        self._grid.reset()
        self._limiter.reset()
        self._sibilance.reset()
        self._gain_db = 0.0
        self._seeded = False
        self._powers = []

    @property
    def latency_samples(self) -> int:
        """The delay the module puts into the signal, in samples.

        The grid holds the signal back by one step, so the gain of the step
        that is being played is already known, and the limiter holds it back
        by two frames, so the reduction is in place before the peak arrives.
        """
        return int(self._grid.latency_samples + self._limiter.latency_samples)

    # -- the control loop -----------------------------------------------------
    def _read_parameters(self) -> None:
        """Read the parameters the control loop needs."""
        self._target = float(self.params.get("Target Level"))
        responsiveness = float(self.params.get("Responsiveness"))
        preserve = float(self.params.get("Preserve Dynamics"))
        self._dialogue = str(self.params.get("Optimize For")) == "Dialogue"
        # The gain may not move further than three decibels per step, and
        # the step is the size the correction takes towards the target.
        self._step = (0.05 + 0.45 * responsiveness) * (1.0 - 0.7 * preserve)

    def _window_level(self, steps: int) -> float | None:
        """The level of the last ``steps`` steps of the grid, in LUFS."""
        if len(self._powers) < steps:
            return None
        window = self._powers[-steps:]
        mean = sum(window) / len(window)
        if mean <= 0.0:
            return None
        return -0.691 + 10.0 * math.log10(mean)

    def _control(self, level_db: float, peak: float) -> float:
        """The gain of one step of the grid, in decibels.

        The first step of the grid seeds the gain from the level of the
        material, so the leveler is on target from the first sample.  Every
        step after that moves the gain towards the target by a fraction of
        the error, and the measurement it uses is the momentary loudness of
        the last 400 ms (music) or the short term loudness of the last three
        seconds (dialogue) of the material, with the gain that is applied
        added to it, because that is what the output carries.
        """
        power = 10.0 ** ((level_db + 0.691) / 10.0) if math.isfinite(level_db) else 0.0
        self._powers.append(power)
        if len(self._powers) > self._SHORT_TERM_STEPS:
            del self._powers[: len(self._powers) - self._SHORT_TERM_STEPS]
        if not self._seeded:
            if not math.isfinite(level_db):
                # the material is silent: there is no level to ride, and the
                # seed waits for the first step that carries one
                return 0.0
            # the gain that puts the material on the target
            self._gain_db = max(-24.0, min(24.0, self._target - level_db))
            self._seeded = True
            return self._gain_db
        steps = self._SHORT_TERM_STEPS if self._dialogue else self._MOMENTARY_STEPS
        measured = self._window_level(steps)
        if measured is None:
            return self._gain_db
        # the level of the output is the level of the material with the gain
        # that is applied to it
        error = self._target - (measured + self._gain_db)
        wanted = self._gain_db + self._step * error
        # The gain may not move further than three decibels per step.
        wanted = max(self._gain_db - 3.0, min(self._gain_db + 3.0, wanted))
        self._gain_db = max(-24.0, min(24.0, wanted))
        return self._gain_db

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Level a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        self._read_parameters()
        ess = float(self.params.get("Ess Reduction"))
        ceiling = float(self.params.get("Limiter"))

        # The gain of every step of the grid is chosen on the grid, and the
        # signal is delayed by one step, so the answer does not depend on the
        # size of the block the host hands over.
        out = self._grid.process(buf, self._control)
        if ess > 0.0:
            out = out * self._sibilance.gains(out, ess)
        out = self._limiter.process(out, ceiling)
        return out
