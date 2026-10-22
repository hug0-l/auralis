"""
aurcore.modules.azimuth — stereo placement control.

Azimuth moves the phantom image of a stereo signal by applying a relative
time shift and a relative level trim between the two channels:

* ``Delay`` shifts one channel against the other with a fractional sample
  interpolator, which is what actually moves the image.
* ``Level`` trims the right channel against the left one.
* ``Adaptive Matching`` measures the level and the time offset that already
  exist between the two channels (cross correlation of the running signal)
  and corrects them.

The module is built on the shared mid/side engine of
:mod:`aurcore.modules.mixing`; the delay line lives here because it is
specific to this module.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module
from .mixing import ChannelOps

__all__ = ["FractionalDelay", "Azimuth"]


class FractionalDelay:
    """Fractional sample delay line with a windowed sinc interpolator.

    The line only *delays*; a negative relative shift is realised by delaying
    the opposite channel instead (see :class:`Azimuth`).

    Parameters
    ----------
    channels:
        Number of channels the line carries.
    taps:
        Number of interpolation taps (odd, 9 or more is sensible).
    max_delay:
        Largest delay in samples the line has to support.
    """

    def __init__(self, channels: int = 1, taps: int = 16, max_delay: int = 0):
        self.channels = int(channels)
        self.taps = int(taps) | 1
        self.max_delay = int(max_delay)
        self._hist_len = self.max_delay + self.taps
        self._hist = np.zeros((self._hist_len, self.channels), dtype=np.float64)
        self._delay = 0.0
        self._start = 0
        self._centre: float | None = None
        self._coeffs = self._design(0.0, self.taps)

    # -- design ---------------------------------------------------------------
    @staticmethod
    def _design(centre: float, taps: int = 16, beta: float = 8.0) -> np.ndarray:
        """Return the interpolation filter centred on ``centre``.

        ``centre`` is the position of the filter peak inside the tap range;
        a fractional centre interpolates between two samples.
        """
        half = max(centre, taps - 1 - centre)
        u = (np.arange(taps, dtype=np.float64) - centre) / max(half, 1e-9)
        window = np.i0(beta * np.sqrt(np.maximum(0.0, 1.0 - u ** 2))) / np.i0(beta)
        return np.sinc(np.arange(taps, dtype=np.float64) - centre) * window

    def set_delay(self, samples: float) -> None:
        """Set the delay of the line in samples (fractional part included)."""
        delay = max(0.0, float(samples))
        delay = min(delay, float(self.max_delay))
        self._delay = delay
        whole = int(np.floor(delay))
        # the first tap sits ``whole - taps // 2 + 1`` samples in the past;
        # for short delays the tap range starts at the current sample.
        self._start = max(0, whole - self.taps // 2 + 1)
        centre = delay - self._start
        if self._centre is None or abs(centre - self._centre) > 1e-9:
            self._centre = centre
            self._coeffs = self._design(centre, self.taps)

    @property
    def delay(self) -> float:
        """The current delay of the line in samples."""
        return self._delay

    def reset(self) -> None:
        """Clear the line history."""
        self._hist = np.zeros((self._hist_len, self.channels), dtype=np.float64)

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Delay a block of audio."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        n = a.shape[0]
        if n == 0:
            return a
        if self._hist.shape[1] != a.shape[1]:
            # the caller changed the channel count: resize the history
            self._hist = np.zeros((self._hist_len, a.shape[1]), dtype=np.float64)
        buf = np.concatenate([self._hist, a], axis=0)
        offset = self._hist_len - self._start
        out = np.zeros((n, a.shape[1]), dtype=np.float64)
        for k in range(self.taps):
            idx = offset - k
            out += self._coeffs[k] * buf[idx:idx + n]
        self._hist = buf[-self._hist_len:]
        return out


@register
class Azimuth(Module):
    """Azimuth — level and time placement of a stereo image.

    ``Level`` trims the right channel against the left one (0 dB keeps the
    balance untouched, +6 dB makes the image move to the right).
    ``Delay`` shifts the channels against each other: a positive value delays
    the right channel (the image moves to the left), a negative value delays
    the left channel (the image moves to the right).  The shift is a *relative*
    inter channel shift - the channel that is not delayed defines the time
    reference, so the module adds no stream latency.
    ``Adaptive Matching`` measures the level and time offset that already
    exist between the two channels and corrects them before the manual
    controls are applied.
    """

    code = "AUR_AZIMUTH"
    display_name = "Azimuth"
    category = "Repair"
    edition = "advanced"
    element = "ChannelOperations"
    analysis = False
    supports_learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._ops = ChannelOps(sample_rate, channels)
        self._max_delay = int(np.ceil(0.012 * sample_rate)) + 1
        self._delay_left = FractionalDelay(1, taps=16, max_delay=self._max_delay)
        self._delay_right = FractionalDelay(1, taps=16, max_delay=self._max_delay)
        # adaptive matching state
        self._analysis_seconds = 1.0
        self._analysis_len = int(self._analysis_seconds * sample_rate)
        self._history = np.zeros((0, 2), dtype=np.float64)
        self._since_update = 0
        self._update_interval = max(1, int(0.1 * sample_rate))
        self._match_lag = 0.0     # samples, positive = the right channel is late
        self._match_gain = 1.0    # gain that is applied to the right channel
        self._match_confidence = 0.0

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=12.0,
                unit="dB",
                group="Gain",
                help="Gain trim of the right channel against the left one.",

                aliases=("Level",)),
            desc(
                name="Delay",
                type="float",
                default=0.0,
                minimum=-10.0,
                maximum=10.0,
                unit="ms",
                group="Delay",
                help="Relative shift between the channels; positive delays the right channel.",
            ),
            desc(
                name="Adaptive Matching",
                type="bool",
                default=False,
                minimum=0,
                maximum=1,
                group="Matching",
                help="Measure and correct the level and time offset between the channels.",
            ),
        ]

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Clear the delay lines and the adaptive matching state."""
        self._delay_left.reset()
        self._delay_right.reset()
        self._history = np.zeros((0, 2), dtype=np.float64)
        self._since_update = 0
        self._match_lag = 0.0
        self._match_gain = 1.0
        self._match_confidence = 0.0

    @property
    def matched_lag_samples(self) -> float:
        """The time offset (in samples) measured by the adaptive matcher."""
        return self._match_lag

    @property
    def matched_gain(self) -> float:
        """The level correction measured by the adaptive matcher."""
        return self._match_gain

    # -- adaptive matching ---------------------------------------------------
    def _update_match(self, block: np.ndarray) -> None:
        """Re-measure the inter channel offset and level from the history.

        The offset is found by cross correlating the two channels; the level
        correction is the least squares gain that maps the right channel onto
        the left one.  Both are smoothed over time so that the correction
        does not jump.
        """
        self._history = np.concatenate([self._history, block], axis=0)
        if self._history.shape[0] > self._analysis_len:
            self._history = self._history[-self._analysis_len:]
        self._since_update += block.shape[0]
        if self._since_update < self._update_interval:
            return
        self._since_update = 0
        if self._history.shape[0] < self._update_interval * 4:
            return

        decimate = 4
        usable = (self._history.shape[0] // decimate) * decimate
        if usable < decimate * 64:
            return
        window = self._history[-usable:]
        left = window[:, 0].reshape(-1, decimate).mean(axis=1)
        right = window[:, 1].reshape(-1, decimate).mean(axis=1)
        left = left - left.mean()
        right = right - right.mean()
        energy_l = float(np.dot(left, left))
        energy_r = float(np.dot(right, right))
        if energy_l < 1e-12 or energy_r < 1e-12:
            return

        size = 1 << int(np.ceil(np.log2(2 * left.shape[0])))
        corr = np.fft.irfft(np.conj(np.fft.rfft(left, size)) * np.fft.rfft(right, size), size)
        max_lag = max(1, int(self._max_delay // decimate))
        lags = np.concatenate([np.arange(0, max_lag + 1), -np.arange(1, max_lag + 1)])
        values = np.concatenate([corr[:max_lag + 1], corr[-max_lag:]])
        best = int(np.argmax(values))
        peak = float(values[best])
        confidence = peak / np.sqrt(energy_l * energy_r)
        if confidence < 0.3:
            return
        lag = float(lags[best])
        # refine the peak with a parabolic fit for a sub decimation accuracy
        index = int(lag) % size
        before = float(corr[(index - 1) % size])
        centre = float(corr[index])
        after = float(corr[(index + 1) % size])
        curvature = before - 2.0 * centre + after
        if abs(curvature) > 1e-15:
            lag += float(np.clip(0.5 * (before - after) / curvature, -1.0, 1.0))
        # a positive lag means that the right channel arrives later
        lag_samples = lag * decimate
        gain = float(np.clip(peak / energy_r, 0.1, 10.0))
        if self._match_confidence <= 0.0:
            self._match_lag = lag_samples
            self._match_gain = gain
        else:
            self._match_lag = 0.7 * self._match_lag + 0.3 * lag_samples
            self._match_gain = 0.7 * self._match_gain + 0.3 * gain
        self._match_confidence = confidence

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the level trim and the relative delay to a stereo block."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0 or a.shape[1] < 2:
            return a

        if self.params.get("Adaptive Matching"):
            self._update_match(a)

        manual_ms = float(self.params.get("Delay"))
        manual = manual_ms * 0.001 * self.sample_rate
        # ``self._match_lag`` is positive when the right channel arrives
        # later than the left one; the correction therefore advances the
        # right channel, which is the same as delaying the left one.
        total = manual - self._match_lag
        total = float(np.clip(total, -self._max_delay, self._max_delay))

        level = 10.0 ** (float(self.params.get("Gain")) / 20.0)
        right_gain = level * self._match_gain

        if total >= 0.0:
            self._delay_left.set_delay(0.0)
            self._delay_right.set_delay(total)
        else:
            self._delay_left.set_delay(-total)
            self._delay_right.set_delay(0.0)

        left = self._delay_left.process(a[:, 0:1])
        right = self._delay_right.process(a[:, 1:2] * right_gain)
        return np.concatenate([left, right], axis=1)
