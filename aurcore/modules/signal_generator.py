"""
aurcore.modules.signal_generator — test signal generator.

The tone is built with additive synthesis: every harmonic that fits below the
Nyquist frequency is summed, so the waveform never folds back on itself and
no aliasing is produced.  The noise generators cover the usual white, pink and
brown flavours; their state is carried between blocks so the noise stays
continuous.

The generated signal is added to the incoming audio, which lets the module be
dropped into a chain to inject a reference without destroying the programme.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc, TAIL_INFINITE
from ..registry import register
from .base import Module

__all__ = ["NOISE_TYPES", "SignalGenerator", "TONE_SHAPES"]

#: Available tone shapes.
TONE_SHAPES: tuple[str, ...] = ("Sine", "Triangle", "Saw", "Square", "Pulses")

#: Available noise flavours.
NOISE_TYPES: tuple[str, ...] = (
    "White Gaussian",
    "White Triangular",
    "White Uniform",
    "White Binary",
    "Pink",
    "Brown",
)

#: Duty cycle of the pulse shape.
_PULSE_DUTY = 0.25

#: Highest harmonic that is summed; keeps the generator affordable.
_MAX_HARMONICS = 256


def _harmonic_amplitudes(shape: str, order: int) -> list[float]:
    """Return the amplitude of every harmonic of a tone shape.

    Index zero holds the direct current term, so harmonic ``k`` lives at
    index ``k``.
    """
    amps: list[float] = [0.0] * (order + 1)
    if shape == "Triangle":
        for k in range(1, order + 1):
            if k % 2 == 0:
                continue
            sign = 1.0 if ((k - 1) // 2) % 2 == 0 else -1.0
            amps[k] = sign * 8.0 / (math.pi * math.pi * k * k)
    elif shape == "Saw":
        for k in range(1, order + 1):
            amps[k] = 2.0 / (math.pi * k)
    elif shape == "Square":
        for k in range(1, order + 1):
            if k % 2 == 1:
                amps[k] = 4.0 / (math.pi * k)
    elif shape == "Pulses":
        for k in range(1, order + 1):
            amps[k] = 2.0 * math.sin(math.pi * k * _PULSE_DUTY) / (math.pi * k)
    else:
        amps[1] = 1.0
    return amps


@register
class SignalGenerator(Module):
    """Generate a reference tone and a noise bed."""

    code = "AUR_SIGNALGEN"
    display_name = "Signal Generator"
    category = "Utility"
    edition = "standard"
    element = "SignalGenerator"

    #: The module makes a sound of its own: it adds the generated signal to
    #: the incoming audio, and the level of that signal is the user's own
    #: control (the ``Amplitude`` and ``Level`` parameters).  The ceiling of
    #: the family therefore does not apply to the sum.
    generates = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._phase = 0.0
        self._offset = 0
        self._pink = np.zeros(7, dtype=np.float64)
        self._brown = 0.0
        self._generator = np.random.default_rng(0x5164)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Tone Shape",
                type="enum",
                default="Sine",
                minimum=0.0,
                maximum=4.0,
                choices=TONE_SHAPES,
                group="Generator",
                help="Shape of the generated tone.",
            ),
            ParamDesc(
                name="Frequency",
                type="float",
                default=440.0,
                minimum=20.0,
                maximum=20000.0,
                unit="Hz",
                group="Generator",
                curve="log",
                help="Frequency of the generated tone.",
            ),
            ParamDesc(
                name="Amplitude",
                type="float",
                default=-18.0,
                minimum=-96.0,
                maximum=0.0,
                unit="dBFS",
                group="Generator",
                help="Peak level of the generated tone.",
            ),
            ParamDesc(
                name="Noise Type",
                type="enum",
                default="White Gaussian",
                minimum=0.0,
                maximum=6.0,
                choices=NOISE_TYPES,
                group="Generator",
                help="Flavour of the generated noise.",
            ),
            ParamDesc(
                name="Level",
                type="float",
                default=-60.0,
                minimum=-96.0,
                maximum=0.0,
                unit="dBFS",
                group="Generator",
                help="Level of the generated noise.",
            ),
        ]

    def reset(self) -> None:
        """Restart the phase and the noise generators."""
        self._phase = 0.0
        self._offset = 0
        self._pink = np.zeros(7, dtype=np.float64)
        self._brown = 0.0
        self._generator = np.random.default_rng(0x5164)

    # -- tone -----------------------------------------------------------------
    @property
    def tail_samples(self) -> int:
        """The module never stops ringing.

        The generator makes a tone of its own, so the output does not go
        quiet when the input stops: the host has to keep the module
        running for as long as the generator is switched on.
        """
        return TAIL_INFINITE

    def _tone(self, shape: str, freq: float, count: int) -> np.ndarray:
        """Build the tone with additive synthesis."""
        sr = float(self.sample_rate)
        nyquist_order = int(sr / (2.0 * max(freq, 1.0)))
        order = max(1, min(nyquist_order, _MAX_HARMONICS))
        amps = _harmonic_amplitudes(shape, order)
        start = self._phase + 2.0 * math.pi * freq * self._offset / sr
        times = np.arange(count, dtype=np.float64)
        out = np.zeros(count, dtype=np.float64)
        omega = 2.0 * math.pi * freq / sr
        for k in range(1, order + 1):
            amp = amps[k]
            if amp == 0.0:
                continue
            out += amp * np.sin(k * (omega * times + start))
        if shape == "Pulses":
            # The pulse train carries a direct current offset; remove it so
            # the generator does not add a constant to the programme.
            out -= _PULSE_DUTY
            peak = max(1.0 - _PULSE_DUTY, _PULSE_DUTY)
            out /= peak
        return out

    # -- noise ----------------------------------------------------------------
    def _noise(self, kind: str, count: int) -> np.ndarray:
        """Build the noise, keeping the state between calls."""
        rng = self._generator
        if kind == "White Gaussian":
            return rng.standard_normal(count)
        if kind == "White Triangular":
            return rng.random(count) + rng.random(count) - 1.0
        if kind == "White Uniform":
            return rng.random(count) * 2.0 - 1.0
        if kind == "White Binary":
            return np.where(rng.random(count) < 0.5, -1.0, 1.0)
        if kind == "Brown":
            white = rng.standard_normal(count)
            out = np.empty(count, dtype=np.float64)
            state = self._brown
            for index in range(count):
                state = (state + 0.02 * white[index]) / 1.02
                out[index] = state
            self._brown = state
            return out * 3.5
        # Pink: the classic multi pole filter, roughly three decibels per octave.
        white = rng.standard_normal(count)
        out = np.empty(count, dtype=np.float64)
        b = self._pink
        for index in range(count):
            w = white[index]
            b[0] = 0.99886 * b[0] + w * 0.0555179
            b[1] = 0.99332 * b[1] + w * 0.0750759
            b[2] = 0.96900 * b[2] + w * 0.1538520
            b[3] = 0.86650 * b[3] + w * 0.3104856
            b[4] = 0.55000 * b[4] + w * 0.5329522
            b[5] = -0.7616 * b[5] - w * 0.0168980
            out[index] = (
                b[0] + b[1] + b[2] + b[3] + b[4] + b[5] + b[6] + w * 0.5362
            )
            b[6] = w * 0.115926
        self._pink = b
        return out * 0.11

    def process(self, x: np.ndarray) -> np.ndarray:
        """Add the generated signal to a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        count = buf.shape[0]
        if count == 0:
            return buf
        shape = str(self.params.get("Tone Shape"))
        freq = float(self.params.get("Frequency"))
        amplitude = float(self.params.get("Amplitude"))
        noise_type = str(self.params.get("Noise Type"))
        level = float(self.params.get("Level"))

        tone = self._tone(shape, freq, count) * (10.0 ** (amplitude / 20.0))
        noise = self._noise(noise_type, count) * (10.0 ** (level / 20.0))
        generated = tone + noise

        self._offset += count
        self._phase = (
            self._phase + 2.0 * math.pi * freq * count / self.sample_rate
        ) % (2.0 * math.pi)
        return buf + generated[:, None]
