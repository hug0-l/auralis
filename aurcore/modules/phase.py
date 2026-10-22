"""
aurcore.modules.phase — channel phase and polarity.

``Rotation`` turns the phase of the right channel relative to the left.  The
rotation is a true broadband phase rotation: the analytic signal of the
channel is multiplied by a complex constant, which is the same as adding the
requested angle to the phase of every partial.  At 180 degrees the channel is
inverted, exactly.

``Adaptive Rotation`` measures the delay and the polarity that align the two
channels best.  The channels are cross-correlated and the peak of the
correlation gives the delay; a negative correlation means the channels have
the opposite polarity and one of them is inverted.  The measured delay is
applied with a windowed sinc fractional delay and smoothed over time.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["Phase"]


def _next_pow2(n: int) -> int:
    value = 1
    while value < int(n):
        value <<= 1
    return value


def _analytic(x: np.ndarray) -> np.ndarray:
    """Return the analytic signal of a real block (one sided spectrum)."""
    n = x.shape[0]
    if n == 0:
        return x.astype(np.complex128)
    spectrum = np.fft.fft(x, axis=0)
    weights = np.zeros(n, dtype=np.float64)
    if n % 2 == 0:
        weights[0] = 1.0
        weights[n // 2] = 1.0
        weights[1: n // 2] = 2.0
    else:
        weights[0] = 1.0
        weights[1: (n + 1) // 2] = 2.0
    return np.fft.ifft(spectrum * weights[:, None], axis=0)


def _fractional_delay(x: np.ndarray, delay: float, half: int = 12) -> np.ndarray:
    """Delay a mono block by a fractional number of samples.

    The kernel is a windowed sinc centred on the wanted position, so the
    delay is accurate to a fraction of a sample.
    """
    n = x.shape[0]
    if n == 0:
        return x
    out = np.zeros_like(x)
    index = np.arange(n, dtype=np.float64)
    whole = math.floor(delay)
    fraction = delay - whole
    for offset in range(-half + 1, half + 1):
        source = (index - whole - offset).astype(np.int64)
        valid = (source >= 0) & (source < n)
        if not np.any(valid):
            continue
        distance = offset - fraction
        weight = np.sinc(distance) * (0.5 * (1.0 + np.cos(np.pi * distance / half)))
        out[valid] += weight * x[source[valid]]
    return out


@register
class Phase(Module):
    """Rotate the phase between the channels and align them automatically."""

    code = "AUR_PHASE"
    display_name = "Phase"
    category = "Utility"
    edition = "standard"
    element = "ChannelOperations"

    #: Largest delay the adaptive alignment searches for, in milliseconds.
    MAX_LAG_MS = 10.0

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._delay = 0.0
        self._invert = False
        self._initialised = False

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Rotation",
                type="float",
                default=0.0,
                minimum=-180.0,
                maximum=180.0,
                unit="deg",
                group="Phase",
                help="Phase rotation of the right channel relative to the left.",
            ),
            ParamDesc(
                name="Adaptive Rotation",
                type="bool",
                default=False,
                minimum=0.0,
                maximum=1.0,
                group="Phase",
                help="Aligns the channels automatically by cross correlation.",
            ),
        ]

    def reset(self) -> None:
        """Forget the measured delay and polarity."""
        self._delay = 0.0
        self._invert = False
        self._initialised = False

    # -- adaptive alignment ---------------------------------------------------
    def _measure(self, left: np.ndarray, right: np.ndarray) -> tuple[float, bool]:
        """Return the delay and the polarity that align the two channels.

        The correlation is evaluated as ``sum(a[n - k] * b[n])`` so the peak
        sits at the delay that has to be added to the right channel.
        """
        n = min(left.shape[0], right.shape[0])
        if n < 64:
            return 0.0, False
        a = left[:n] - float(np.mean(left[:n]))
        b = right[:n] - float(np.mean(right[:n]))
        nfft = _next_pow2(2 * n)
        correlation = np.fft.irfft(
            np.conj(np.fft.rfft(a, nfft)) * np.fft.rfft(b, nfft), nfft
        )
        max_lag = int(self.MAX_LAG_MS * 0.001 * self.sample_rate)
        max_lag = max(1, min(max_lag, n - 1))
        positive = correlation[: max_lag + 1]
        negative = correlation[-max_lag:]
        best_pos = int(np.argmax(positive))
        best_neg = int(np.argmax(negative))
        if abs(positive[best_pos]) >= abs(negative[best_neg]):
            lag = best_pos
            value = positive[best_pos]
        else:
            lag = -(max_lag - best_neg)
            value = negative[best_neg]
        if lag == 0 and abs(value) < 1e-9:
            return 0.0, False
        return float(lag), bool(value < 0.0)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Rotate the phase of a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        out = buf.copy()
        if bool(self.params.get("Adaptive Rotation")) and buf.shape[1] >= 2:
            lag, invert = self._measure(buf[:, 0], buf[:, 1])
            # A positive lag means the right channel arrives late, so it has
            # to be advanced by that amount.
            correction = -lag
            if not self._initialised:
                self._delay = correction
                self._invert = invert
                self._initialised = True
            else:
                # A gentle slew keeps the correction free of clicks.
                self._delay += 0.25 * (correction - self._delay)
                if invert != self._invert:
                    self._invert = invert
            if abs(self._delay) > 1e-6:
                out[:, 1] = _fractional_delay(out[:, 1], self._delay)
            if self._invert:
                out[:, 1] = -out[:, 1]

        rotation = float(self.params.get("Rotation"))
        if rotation != 0.0:
            # The rotation is applied to the right channel, so it changes the
            # phase between the channels and not the phase of the programme.
            target = 1 if buf.shape[1] >= 2 else 0
            angle = math.radians(rotation)
            pad = min(1024, n)
            column = out[:, target]
            padded = np.pad(column, (pad, pad), mode="reflect")
            analytic = _analytic(padded[:, None])
            turned = np.real(np.exp(1j * angle) * analytic)[:, 0]
            out[:, target] = turned[pad: pad + n]
        return out
