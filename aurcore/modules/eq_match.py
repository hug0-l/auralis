"""
aurcore.modules.eq_match — match the spectral envelope of a reference.

``learn`` stores the average spectrum of the reference in a set of logarithm
spaced bands.  During processing the average spectrum of the incoming audio is
measured in the same bands, the difference between the two is split into a
broadband part and a spectral shape, and the shape is applied as a gentle
equaliser, scaled by the amount control.

The band filters are the same peaking biquads the equaliser module uses, so
the two modules behave consistently.

The measurement
---------------
The level of a band is the mean square of the signal inside the band,
reconstructed from a Hann windowed transform.  That number does not depend on
the length of the analysis window, so the learned reference and the running
measurement can be compared.  The leakage a tone leaves in the neighbouring
bands does depend on the length of the window, so the window is the same
length for the reference and for the running measurement.

The module keeps a rolling window of :data:`ANALYSIS_SAMPLES` samples and
measures it every :data:`ANALYSIS_HOP` samples.  A host that feeds 64 sample
blocks therefore gets the same measurement as a host that hands the whole
file over in one piece, and a block that is shorter than the analysis window
is never measured with a window it does not fill.

The module delays the signal by nothing.  It waits until it holds a full
analysis window before it corrects anything (42.7 ms at 48 kHz), and the
correction it applies afterwards is derived from the samples it has just
seen, so the curve is never more than :data:`ANALYSIS_HOP` samples older
than the audio it shapes.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .eq import biquad_stream, peaking_biquad

__all__ = ["EQMatch", "band_centres", "band_edges", "band_levels"]

#: Number of bands the envelope is measured in.
BAND_COUNT = 16

#: Lowest and highest frequency of the band grid.
BAND_LOW = 40.0
BAND_HIGH = 16000.0

#: The analysis window, in samples.  2048 samples put a bin into every band
#: of the grid at 48 kHz (the bin spacing is 23.4 Hz, the lowest band ends at
#: 48.8 Hz), so every band can be measured.
ANALYSIS_SAMPLES = 2048

#: How often the spectrum is measured again, in samples.
ANALYSIS_HOP = 256

#: The largest correction the module applies, in decibels.  A bell that is
#: asked for more than this is not a correction, it is a runaway.
MAX_CORRECTION_DB = 24.0

#: The level of the analyser, in decibels.
FLOOR_DB = -120.0

#: How far above the floor a band has to be to count as measured.
MARGIN_DB = 6.0

#: How far below the loudest band a band may be and still count as measured.
#: A band that holds nothing but the leakage of a neighbour is not a
#: measurement, and matching it would only add noise.
SIGNAL_DB = 30.0

#: The Q of the band filters.  The same as the equaliser module uses.
BAND_Q = 1.1


def band_centres(count: int = BAND_COUNT) -> np.ndarray:
    """Return the centre frequencies of the band grid."""
    ratio = (BAND_HIGH / BAND_LOW) ** (1.0 / max(1, count - 1))
    return BAND_LOW * np.power(ratio, np.arange(count, dtype=np.float64))


def band_edges(count: int = BAND_COUNT) -> np.ndarray:
    """Return the lower edge of every band and the upper edge of the last."""
    centres = band_centres(count)
    return np.concatenate(
        (
            np.array([centres[0] * (centres[0] / centres[1]) ** 0.5]),
            np.sqrt(centres[:-1] * centres[1:]),
            np.array([centres[-1] * (centres[-1] / centres[-2]) ** 0.5]),
        )
    )


def level_db(x: np.ndarray) -> float:
    """Return the mean square level of a signal, in decibels."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return FLOOR_DB
    value = float(np.mean(arr * arr))
    if value <= 0.0:
        return FLOOR_DB
    return max(10.0 * math.log10(value), FLOOR_DB)


def band_levels(
    x: np.ndarray,
    sample_rate: int,
    count: int = BAND_COUNT,
    size: int = ANALYSIS_SAMPLES,
) -> np.ndarray:
    """Return the level of every band of a block, in decibels.

    The level of a band is the mean square of the signal inside the band,
    reconstructed from a Hann windowed transform.  A band that carries no
    signal reads :data:`FLOOR_DB`.

    A signal that is longer than the window is measured over several
    windows and the results are averaged.  The window itself is never longer
    than the signal, so a short block is measured with the samples it has
    instead of with a window it does not fill.

    Parameters
    ----------
    x:
        The signal, shape ``(n_samples, n_channels)`` or ``(n_samples,)``.
    sample_rate:
        The sample rate of the signal.
    count:
        How many bands the spectrum is measured in.
    size:
        The length of the analysis window, in samples.

    Returns
    -------
    numpy.ndarray
        The level of every band, in decibels.
    """
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    n = buf.shape[0]
    levels = np.full(count, FLOOR_DB, dtype=np.float64)
    if n < 16:
        return levels
    size = int(min(max(int(size), 16), n))
    mono = buf.mean(axis=1)
    window = np.hanning(size)
    weight = float(np.sum(window))
    if weight <= 0.0:
        return levels
    freqs = np.fft.rfftfreq(size, 1.0 / float(sample_rate))
    edges = band_edges(count)
    index = np.full(freqs.shape[0], -1, dtype=np.int64)
    for band in range(count):
        index[(freqs >= edges[band]) & (freqs < edges[band + 1])] = band
    usable = index >= 0
    if not bool(np.any(usable)):
        return levels
    hop = max(1, size // 4)
    frames = 1 + (n - size) // hop
    stride = max(1, frames // 32)
    accumulator = np.zeros(count, dtype=np.float64)
    used = 0
    for frame_index in range(0, frames, stride):
        start = frame_index * hop
        frame = mono[start: start + size] * window
        power = np.abs(np.fft.rfft(frame)) ** 2
        accumulator += np.bincount(
            index[usable], weights=power[usable], minlength=count
        )
        used += 1
    if used == 0:
        return levels
    mean_square = 2.0 * accumulator / (float(used) * weight * weight)
    with np.errstate(divide="ignore", invalid="ignore"):
        values = 10.0 * np.log10(np.maximum(mean_square, 1e-30))
    values = np.where(np.isfinite(values), values, FLOOR_DB)
    return np.maximum(values, FLOOR_DB)


def _smooth_valid(
    curve: np.ndarray, valid: np.ndarray, passes: int = 2
) -> np.ndarray:
    """Smooth a band curve over the bands that carry a measurement.

    The bands that carry no measurement are left out of the average, so a
    band that holds nothing but noise does not drag the correction of its
    neighbour towards zero.  The bands themselves are left at zero.
    """
    out = np.asarray(curve, dtype=np.float64).copy()
    mask = np.asarray(valid, dtype=bool)
    for _ in range(max(0, passes)):
        padded = np.concatenate((out[:1], out, out[-1:]))
        weights = np.concatenate((mask[:1], mask, mask[-1:]))
        num = (
            padded[:-2] * weights[:-2]
            + 2.0 * padded[1:-1] * weights[1:-1]
            + padded[2:] * weights[2:]
        )
        den = weights[:-2] + 2.0 * weights[1:-1] + weights[2:]
        out = np.where(den > 0.0, num / np.where(den > 0.0, den, 1.0), 0.0)
        # A band that carries no measurement gets no correction at all.
        out = np.where(mask, out, 0.0)
    return out


@register
class EQMatch(Module):
    """Match the spectral envelope of a learned reference."""

    code = "AUR_EQMATCH"
    display_name = "EQ Match"
    category = "Utility"
    edition = "advanced"
    element = "MatchEQ"
    supports_learn = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._reference: np.ndarray | None = None
        self._reference_level: float | None = None
        self._current: np.ndarray | None = None
        self._current_level = 0.0
        self._states: list[np.ndarray] = []
        self._history = np.zeros(ANALYSIS_SAMPLES, dtype=np.float64)
        self._written = 0
        self._position = 0
        self._next_refresh = ANALYSIS_SAMPLES
        self._broadband = 0.0
        self._gains = np.zeros(BAND_COUNT, dtype=np.float64)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Amount",
                type="float",
                default=0.25,
                minimum=0.0,
                maximum=1.0,
                group="Match",
                help="How much of the difference is applied.",
            ),
        ]

    def reset(self) -> None:
        """Clear the filter state and the running measurement."""
        self._current = None
        self._current_level = 0.0
        self._states = []
        self._history = np.zeros(ANALYSIS_SAMPLES, dtype=np.float64)
        self._written = 0
        self._position = 0
        self._next_refresh = ANALYSIS_SAMPLES
        self._broadband = 0.0
        self._gains = np.zeros(BAND_COUNT, dtype=np.float64)

    def learn(self, x: np.ndarray) -> None:
        """Capture the spectral envelope of the reference."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return
        self._reference = band_levels(buf, self.sample_rate)
        self._reference_level = level_db(buf.mean(axis=1))

    def serialize(self) -> dict:
        """Return the learned reference."""
        if self._reference is None:
            return {}
        return {
            "reference": [float(v) for v in self._reference],
            "reference_level": float(self._reference_level or 0.0),
        }

    def deserialize(self, data: dict) -> None:
        """Restore a learned reference.

        A state that carries no level comes from a build that measured the
        bands differently; its levels cannot be compared with the ones of
        this build, so the module matches the spectrum band by band and
        leaves the overall level alone.
        """
        curve = (data or {}).get("reference")
        if curve:
            self._reference = np.asarray(curve, dtype=np.float64)
            level = (data or {}).get("reference_level")
            self._reference_level = None if level is None else float(level)

    # -- the measurement ------------------------------------------------------
    def _append(self, block: np.ndarray) -> None:
        """Add a block to the rolling analysis window."""
        mono = block.mean(axis=1) if block.ndim > 1 else np.asarray(block)
        n = mono.shape[0]
        if n == 0:
            return
        position = self._written % ANALYSIS_SAMPLES
        first = min(n, ANALYSIS_SAMPLES - position)
        self._history[position: position + first] = mono[:first]
        if first < n:
            self._history[: n - first] = mono[first:]
        self._written += n

    def _window(self) -> np.ndarray:
        """Return the analysis window, oldest sample first."""
        position = self._written % ANALYSIS_SAMPLES
        if position == 0:
            return self._history
        return np.concatenate((self._history[position:], self._history[:position]))

    def _measure(self) -> None:
        """Measure the spectrum of the analysis window and refit the curve."""
        window = self._window()
        if window.shape[0] < 64:
            return
        measured = band_levels(window, self.sample_rate)
        level = level_db(window)
        if self._current is None:
            self._current = measured
            self._current_level = level
        else:
            # A slow average keeps the correction stable between measurements.
            self._current = 0.7 * self._current + 0.3 * measured
            self._current_level = 0.7 * self._current_level + 0.3 * level
        self._update_curve()

    def _update_curve(self) -> None:
        """Turn the measured difference into a broadband gain and a shape."""
        reference = self._reference
        current = self._current
        if reference is None or current is None:
            self._broadband = 0.0
            self._gains = np.zeros(BAND_COUNT, dtype=np.float64)
            return
        limit = FLOOR_DB + MARGIN_DB
        valid = (
            (reference > limit)
            & (current > limit)
            & (reference > float(np.max(reference)) - SIGNAL_DB)
            & (current > float(np.max(current)) - SIGNAL_DB)
        )
        if not bool(np.any(valid)):
            self._broadband = 0.0
            self._gains = np.zeros(BAND_COUNT, dtype=np.float64)
            return
        difference = np.where(valid, reference - current, 0.0)
        difference = np.clip(difference, -MAX_CORRECTION_DB, MAX_CORRECTION_DB)
        # The broadband part is the level the reference asks for; the shape is
        # what is left once that is taken out.  A reference that is the same
        # signal, only quieter, is a pure broadband correction and the shape
        # stays empty.
        if self._reference_level is None:
            broadband = 0.0
        else:
            broadband = float(
                np.clip(
                    self._reference_level - self._current_level,
                    -MAX_CORRECTION_DB,
                    MAX_CORRECTION_DB,
                )
            )
        shape = np.where(valid, difference - broadband, 0.0)
        self._broadband = broadband
        self._gains = _smooth_valid(shape, valid)

    # -- the processing -------------------------------------------------------
    def _filter(self, block: np.ndarray, amount: float) -> np.ndarray:
        """Apply the current correction to a block."""
        out = block
        if len(self._states) != BAND_COUNT or self._states[0].shape[1] != out.shape[1]:
            self._states = [
                np.zeros((2, out.shape[1]), dtype=np.float64) for _ in range(BAND_COUNT)
            ]
        centres = band_centres()
        for index in range(BAND_COUNT):
            gain = float(self._gains[index]) * amount
            if abs(gain) < 0.05:
                continue
            coeff = peaking_biquad(float(centres[index]), gain, BAND_Q, self.sample_rate)
            out = biquad_stream(
                coeff[:3], (1.0, coeff[3], coeff[4]), out, self._states[index]
            )
        broadband = self._broadband * amount
        if abs(broadband) > 1e-9:
            out = out * (10.0 ** (broadband / 20.0))
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the learned correction to a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0 or self._reference is None:
            return buf
        amount = float(self.params.get("Amount"))
        n = buf.shape[0]
        out = np.empty_like(buf)
        offset = 0
        while offset < n:
            take = int(min(n - offset, max(1, self._next_refresh - self._position)))
            segment = buf[offset: offset + take]
            self._append(segment)
            self._position += take
            out[offset: offset + take] = self._filter(segment, amount)
            offset += take
            if self._position >= self._next_refresh:
                # The window now holds the samples up to this point, so the
                # measurement describes the audio that follows it.
                self._measure()
                self._next_refresh = self._position + ANALYSIS_HOP
        return out
