"""
aurcore.modules.loudness_control — programme loudness control.

This module owns the loudness measurement used across the workbench:

* :class:`GatedLoudness` — the **gated** integrated loudness of a set of
  400 ms blocks: the absolute gate of -70 LUFS, the relative gate 10 LU
  below the mean of the blocks that are left, and the mean of the blocks
  that survive the relative gate.  This is the quantity a delivery target
  names.
* :class:`LoudnessMeter` — a BS.1770-4 style meter (K-weighted filtering,
  400 ms blocks on a 100 ms hop, absolute and relative gating for the
  integrated value).
* :class:`LoudnessGrid` — the loudness of a signal measured on a **fixed
  grid** of steps, with one step of look ahead.  The grid does not move with
  the size of the block the host hands over, so a module that rides the
  level reaches the same answer in 64 sample blocks and in one block.
* :class:`PeakLimiter` — a frame based true-peak limiter used to keep the
  output below a ceiling.
* :data:`STANDARDS` — the named loudness standards with their own integrated,
  momentary and short-term targets and true-peak ceiling.

Selecting a standard overwrites the manual target fields with the values of
that standard, so the panel always shows what is actually being applied.

The three readings the module publishes
----------------------------------------
The **momentary** loudness is the level of the last 400 ms of the material,
the **short term** loudness is the level of the last three seconds, and the
**integrated** loudness is the gated mean of the 400 ms blocks.  The first
two are ungated by definition: a window is either in the measurement or it
is not.  Only the integrated loudness is gated, because the gate is what
the standard uses to keep the quiet parts of a programme out of the mean.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = [
    "GRID_STEP_S",
    "GatedLoudness",
    "LoudnessControl",
    "LoudnessGrid",
    "LoudnessMeter",
    "PeakLimiter",
    "STANDARDS",
    "integrated_loudness",
    "k_weighting_coefficients",
]

#: Loudness standards: name -> (integrated, momentary, short-term, true peak).
#: The values are the targets published for each document; selecting one
#: replaces the manual target fields of the module.
STANDARDS: dict[str, tuple[float, float, float, float]] = {
    "EBU R128": (-23.0, -23.0, -23.0, -1.0),
    "EBU R128 s1": (-23.0, -23.0, -23.0, -1.0),
    "BS.1770-2": (-23.0, -23.0, -23.0, -1.0),
    "BS.1770-4": (-23.0, -23.0, -23.0, -1.0),
    "ATSC A/85": (-24.0, -24.0, -24.0, -2.0),
    "ARIB TR-B32": (-24.0, -24.0, -24.0, -2.0),
    "AES TD1006.1.17-10": (-16.0, -16.0, -16.0, -1.0),
    "OP-59": (-24.0, -24.0, -24.0, -2.0),
    "Portaria 354": (-23.0, -23.0, -23.0, -1.0),
    "Custom": (-23.0, -23.0, -23.0, -1.0),
}

#: Names of the standards, in the order they appear in the parameter table.
STANDARD_NAMES: tuple[str, ...] = tuple(STANDARDS.keys())

# Published BS.1770-4 K-weighting coefficients for 48 kHz.
_BS1770_SHELF_48K = (
    1.53512485958697,
    -2.69169618940638,
    1.19839281085285,
    -1.69065929318241,
    0.73248077421585,
)
_BS1770_HPF_48K = (
    1.0,
    -2.0,
    1.0,
    -1.99004745483398,
    0.99007225036621,
)

_LOUDNESS_OFFSET = -0.691  # BS.1770 calibration offset
_ABS_GATE_LUFS = -70.0
_REL_GATE_LU = 10.0
_HIST_STEP = 0.01
_HIST_MIN = -120.0
_HIST_MAX = 30.0

#: The length of the block the standard measures the programme in, and the
#: step it moves the block by.  The momentary window is one block long, the
#: short term window is thirty steps long.
_BLOCK_SECONDS = 0.4
_BLOCK_STEP_SECONDS = 0.1


# ---------------------------------------------------------------------------
# K-weighting
# ---------------------------------------------------------------------------
def _analog_prototype(coeffs, sample_rate: int):
    """Recover the analog prototype of a digital biquad (inverse bilinear).

    ``coeffs`` is ``(b0, b1, b2, a1, a2)``; the returned polynomials are the
    numerator and denominator of the analog transfer function in ``s``, ordered
    as ``(s**2, s, 1)``.
    """
    b0, b1, b2, a1, a2 = coeffs
    k = 2.0 * float(sample_rate)
    num = (
        (b0 - b1 + b2),
        (2.0 * b0 - 2.0 * b2) * k,
        (b0 + b1 + b2) * k * k,
    )
    den = (
        (1.0 - a1 + a2),
        (2.0 - 2.0 * a2) * k,
        (1.0 + a1 + a2) * k * k,
    )
    return num, den


def _digital_from_analog(num, den, sample_rate: int):
    """Re-apply the bilinear transform to an analog prototype."""
    k = 2.0 * float(sample_rate)
    n2, n1, n0 = num
    d2, d1, d0 = den
    b = (
        n2 * k * k + n1 * k + n0,
        -2.0 * n2 * k * k + 2.0 * n0,
        n2 * k * k - n1 * k + n0,
    )
    d = (
        d2 * k * k + d1 * k + d0,
        -2.0 * d2 * k * k + 2.0 * d0,
        d2 * k * k - d1 * k + d0,
    )
    a0 = d[0]
    return (b[0] / a0, b[1] / a0, b[2] / a0, d[1] / a0, d[2] / a0)


def k_weighting_coefficients(sample_rate: int):
    """Return the two K-weighting biquad stages for ``sample_rate``.

    The published 48 kHz coefficients are converted back to their analog
    prototype and re-designed for the requested rate, so the weighting curve
    stays the same shape at every sample rate.
    """
    fs = int(sample_rate)
    if fs == 48000:
        return (_BS1770_SHELF_48K, _BS1770_HPF_48K)
    out = []
    for coeffs in (_BS1770_SHELF_48K, _BS1770_HPF_48K):
        num, den = _analog_prototype(coeffs, 48000)
        out.append(_digital_from_analog(num, den, fs))
    return tuple(out)


# ---------------------------------------------------------------------------
# The gated integrated loudness
# ---------------------------------------------------------------------------
class GatedLoudness:
    """The gated integrated loudness of a set of 400 ms blocks.

    The standard measures a programme in blocks of 400 ms that step by
    100 ms, throws the blocks below the absolute gate of -70 LUFS away,
    places a relative gate 10 LU below the mean of the blocks that are
    left, and takes the mean of the blocks that are above that relative
    gate.  The class keeps the histogram of the block loudness values, so
    the gated value can be recomputed at any time without keeping the
    blocks themselves.

    The bin of the histogram is a hundredth of a LU, so the value the class
    reports is the value of the standard to within a hundredth of a LU.
    """

    def __init__(
        self,
        step_lu: float = _HIST_STEP,
        minimum: float = _HIST_MIN,
        maximum: float = _HIST_MAX,
    ):
        self._step = max(1e-6, float(step_lu))
        self._minimum = float(minimum)
        self._bins = max(1, int(round((float(maximum) - self._minimum) / self._step)))
        centres = self._minimum + (np.arange(self._bins) + 0.5) * self._step
        self._powers = np.power(10.0, (centres - _LOUDNESS_OFFSET) / 10.0)
        # the first bin whose centre is above the absolute gate
        self._absolute = self._bin_above(_ABS_GATE_LUFS)
        self._counts = np.zeros(self._bins, dtype=np.float64)
        self._total = 0

    def _bin_above(self, loudness: float) -> int:
        """The first bin whose centre is above ``loudness``."""
        index = int(math.floor((float(loudness) - self._minimum) / self._step - 0.5)) + 1
        return max(0, min(self._bins, index))

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Forget every block that was measured."""
        self._counts[:] = 0.0
        self._total = 0

    def add(self, loudness: float) -> None:
        """Add the loudness of one block."""
        if not math.isfinite(loudness):
            return
        index = int(math.floor((float(loudness) - self._minimum) / self._step))
        index = max(0, min(self._bins - 1, index))
        self._counts[index] += 1.0
        self._total += 1

    @property
    def blocks(self) -> int:
        """How many blocks were measured."""
        return int(self._total)

    # -- readouts -------------------------------------------------------------
    def integrated(self) -> float | None:
        """The gated integrated loudness of the blocks, in LUFS.

        ``None`` means that no block carries a level: the material is
        silent, and no gate can bring a silence onto a target.
        """
        if self._total <= 0:
            return None
        counts = self._counts[self._absolute:]
        powers = self._powers[self._absolute:]
        n1 = float(counts.sum())
        if n1 <= 0.0:
            return None
        p1 = float(np.dot(counts, powers))
        if p1 <= 0.0:
            return None
        # the preliminary level: the mean of the blocks above the absolute
        # gate.  The relative gate sits 10 LU below it.
        preliminary = _LOUDNESS_OFFSET + 10.0 * math.log10(p1 / n1)
        index = max(self._absolute, self._bin_above(preliminary - _REL_GATE_LU))
        counts = self._counts[index:]
        powers = self._powers[index:]
        n2 = float(counts.sum())
        if n2 <= 0.0:
            return None
        p2 = float(np.dot(counts, powers))
        if p2 <= 0.0:
            return None
        return _LOUDNESS_OFFSET + 10.0 * math.log10(p2 / n2)


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------
class LoudnessMeter:
    """Streaming BS.1770-4 style loudness meter.

    The meter K-weights every channel, evaluates 400 ms blocks on a 100 ms
    hop and keeps a histogram of the block loudness values so the gated
    integrated loudness can be recomputed cheaply at any time.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._stages = k_weighting_coefficients(self.sample_rate)
        self._window = max(1, int(round(_BLOCK_SECONDS * self.sample_rate)))
        self._hop = max(1, int(round(_BLOCK_STEP_SECONDS * self.sample_rate)))
        self._short_blocks = max(1, int(round(3.0 * self.sample_rate / self._hop)))
        self._weights = self._channel_weights(self.channels)
        self.reset()

    @staticmethod
    def _channel_weights(channels: int) -> np.ndarray:
        """Loudness weights per channel (surround channels carry more)."""
        weights = []
        for index in range(max(1, int(channels))):
            if index < 2:
                weights.append(1.0)
            elif index < 4:
                weights.append(1.41)
            else:
                weights.append(1.0)
        return np.asarray(weights, dtype=np.float64)

    def reset(self) -> None:
        """Clear the meter state."""
        self._state = [
            np.zeros((2, max(1, self.channels)), dtype=np.float64) for _ in self._stages
        ]
        self._buffer = np.zeros((0, max(1, self.channels)), dtype=np.float64)
        self._square_sum = np.zeros(max(1, self.channels), dtype=np.float64)
        self._blocks: deque[float] = deque(maxlen=self._short_blocks)
        self._gated = GatedLoudness()

    # -- filtering ------------------------------------------------------------
    def _k_weight(self, x: np.ndarray) -> np.ndarray:
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.shape[1] != self.channels:
            if buf.shape[1] == 1:
                buf = np.repeat(buf, self.channels, axis=1)
            else:
                buf = buf[:, : self.channels]
        out = buf
        for stage, state in zip(self._stages, self._state):
            b0, b1, b2, a1, a2 = stage
            out = _biquad(b0, b1, b2, a1, a2, out, state)
        return out

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """K-weight a block and update the measurements; returns the weighted audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        weighted = self._k_weight(buf)
        self._accumulate(weighted)
        return weighted

    def _accumulate(self, weighted: np.ndarray) -> None:
        """Fold weighted samples into the 400 ms block measurements.

        The incoming samples are consumed in hop sized chunks so the analysis
        buffer never grows past one block plus one hop.  A block is measured
        as soon as the buffer holds a full block **and** the hop that starts
        the next one, so the blocks are the blocks of the standard: the
        first one starts with the programme, and every block after it starts
        one hop later.
        """
        total = weighted.shape[0]
        pos = 0
        while pos < total:
            chunk = weighted[pos: pos + self._hop]
            pos += chunk.shape[0]
            if self._buffer.shape[0] == 0:
                self._buffer = chunk
                self._square_sum = np.sum(np.square(chunk), axis=0)
            else:
                self._buffer = np.concatenate((self._buffer, chunk), axis=0)
                self._square_sum += np.sum(np.square(chunk), axis=0)
            while self._buffer.shape[0] >= self._window + self._hop:
                # The block that starts here covers the first ``window``
                # samples of the buffer; the first ``hop`` samples are
                # dropped afterwards, so the next block starts one hop later.
                self._emit_block(self._block_sum())
                drop = np.sum(np.square(self._buffer[: self._hop]), axis=0)
                self._square_sum -= drop
                self._buffer = self._buffer[self._hop:]

    def _block_sum(self) -> np.ndarray:
        """The sum of the squares of the first ``window`` samples."""
        if self._buffer.shape[0] <= self._window:
            return self._square_sum
        tail = np.sum(np.square(self._buffer[self._window:]), axis=0)
        return self._square_sum - tail

    def flush(self) -> None:
        """Measure the block that is still in the buffer.

        The meter is a streaming meter, so it can only measure a block when
        the hop that follows it has arrived.  The last block of a programme
        is measured here: a block that is shorter than the window is
        measured over the window, with silence at the end, which is what the
        standard does with the last block of a programme.
        """
        if self._buffer.shape[0] == 0:
            return
        self._emit_block(self._block_sum())
        self._buffer = self._buffer[0:0]
        self._square_sum = np.zeros_like(self._square_sum)

    def _emit_block(self, window_sum: np.ndarray) -> None:
        """Store the loudness of a finished 400 ms block."""
        mean_square = np.asarray(window_sum, dtype=np.float64) / float(self._window)
        total = float(np.sum(self._weights * mean_square))
        if total <= 0.0:
            loudness = -math.inf
        else:
            loudness = _LOUDNESS_OFFSET + 10.0 * math.log10(total)
        self._blocks.append(loudness)
        self._gated.add(loudness)

    # -- readouts -------------------------------------------------------------
    def momentary(self) -> float | None:
        """Loudness of the most recent 400 ms block (``None`` before the first one)."""
        if not self._blocks:
            return None
        return self._blocks[-1]

    def short_term(self) -> float | None:
        """Loudness over the last three seconds of blocks."""
        if not self._blocks:
            return None
        values = [v for v in self._blocks if math.isfinite(v)]
        if not values:
            return None
        powers = [10.0 ** ((v - _LOUDNESS_OFFSET) / 10.0) for v in values]
        mean = sum(powers) / len(powers)
        if mean <= 0.0:
            return None
        return _LOUDNESS_OFFSET + 10.0 * math.log10(mean)

    def integrated(self) -> float | None:
        """Gated integrated loudness of everything measured so far."""
        return self._gated.integrated()


def _biquad(b0, b1, b2, a1, a2, x: np.ndarray, state: np.ndarray) -> np.ndarray:
    """Apply one biquad to a block, keeping the delay state in ``state``."""
    try:  # pragma: no cover - scipy is optional
        from scipy.signal import lfilter

        y, zf = lfilter(
            [b0, b1, b2], [1.0, a1, a2], x, axis=0, zi=state
        )
        state[:, :] = zf
        return y
    except Exception:  # pragma: no cover
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
            z0[c] = s0
            z1[c] = s1
        state[0] = z0
        state[1] = z1
        return y


def integrated_loudness(x: np.ndarray, sample_rate: int) -> float | None:
    """Measure the gated integrated loudness of a complete buffer."""
    meter = LoudnessMeter(sample_rate, 1 if np.ndim(x) == 1 else np.shape(x)[1])
    meter.process(np.asarray(x, dtype=np.float64))
    meter.flush()
    return meter.integrated()


#: The step of the analysis grid of the loudness modules, in seconds.  The
#: grid is the hop of the BS.1770-4 momentary block, so the windows the
#: modules read (400 ms momentary, 3 s short term) sit on it exactly as they
#: sit on the meter.
GRID_STEP_S = 0.1


class LoudnessGrid:
    """The loudness of a signal, measured on a fixed grid of steps.

    The grid is a fixed number of samples.  It does not move with the size of
    the block the host hands over, so a module that rides the level reaches
    the same answer whether the host renders the material in 64 sample blocks
    or in one piece.

    The signal is delayed by **one step**, so the level of the step that is
    being played is already known when the gain for that step is chosen: the
    gain of the first step is in place from the first sample, and the delay
    is the latency the module declares.  The last step of the material falls
    behind the end of the stream, exactly as it does in every module that
    looks ahead.

    The measurement is the K-weighted mean square of the step, which is the
    momentary loudness of that step; the level is reported in LUFS with the
    calibration offset of BS.1770-4, so the numbers of the grid and the
    numbers of :class:`LoudnessMeter` are the same numbers.

    The grid can also measure the **block** of 400 ms the standard measures
    the programme in: the K-weighted mean square of the last four steps.
    The block ends at every step boundary, so the blocks are the blocks of
    the standard, on the same grid.  A block that ends before the grid has
    seen four steps of material is measured over the material it has, which
    is the average of the programme so far.  The level of the last block is
    in :attr:`block_level` and the caller reads it after the grid has
    measured a step.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        step_s: float = GRID_STEP_S,
        block_s: float = 0.0,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = max(1, int(channels))
        self.step = max(1, int(round(float(step_s) * self.sample_rate)))
        self.block = max(0, int(round(float(block_s) * self.sample_rate)))
        self._block_steps = max(1, int(round(self.block / self.step))) if self.block else 0
        self._stages = k_weighting_coefficients(self.sample_rate)
        self._weights = LoudnessMeter._channel_weights(self.channels)
        self.reset()

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the filter state, the lines of samples and the counters."""
        self._state = [
            np.zeros((2, self.channels), dtype=np.float64) for _ in self._stages
        ]
        # the samples that have not been measured yet
        self._input = np.zeros((0, self.channels), dtype=np.float64)
        # the line that holds the delay: it starts with one step of silence,
        # so the signal is late by exactly one step
        self._output = np.zeros((self.step, self.channels), dtype=np.float64)
        self._steps = 0
        # the square sums of the steps the block window holds
        self._block_squares: deque[np.ndarray] = deque(maxlen=self._block_steps or 1)
        self.block_level = float("-inf")
        self.block_power = 0.0
        self.block_complete = False

    @property
    def latency_samples(self) -> int:
        """The delay the grid puts into the signal, in samples."""
        return int(self.step)

    @property
    def steps(self) -> int:
        """How many steps of the grid have been measured."""
        return int(self._steps)

    # -- measurement ---------------------------------------------------------
    def _weight(self, x: np.ndarray) -> np.ndarray:
        """Apply the K-weighting to a chunk, keeping the filter state."""
        out = x
        for stage, state in zip(self._stages, self._state):
            b0, b1, b2, a1, a2 = stage
            out = _biquad(b0, b1, b2, a1, a2, out, state)
        return out

    def _measure_block(self, squares: np.ndarray) -> None:
        """Measure the 400 ms block that ends at this step boundary."""
        if not self._block_steps:
            return
        self._block_squares.append(squares)
        total = np.zeros(self.channels, dtype=np.float64)
        for item in self._block_squares:
            total += item
        samples = float(len(self._block_squares) * self.step)
        mean_square = total / max(1.0, samples)
        weighted = float(np.sum(self._weights * mean_square))
        self.block_power = weighted
        self.block_complete = len(self._block_squares) >= self._block_steps
        if weighted <= 0.0:
            self.block_level = float("-inf")
        else:
            self.block_level = _LOUDNESS_OFFSET + 10.0 * math.log10(weighted)

    def measure(self, chunk: np.ndarray) -> tuple[float, float]:
        """Return the level (LUFS) and the peak (linear) of a chunk."""
        weighted = self._weight(chunk)
        squares = np.sum(np.square(weighted), axis=0)
        mean_square = squares / float(max(1, chunk.shape[0]))
        total = float(np.sum(self._weights * mean_square))
        if total <= 0.0:
            level = -math.inf
        else:
            level = _LOUDNESS_OFFSET + 10.0 * math.log10(total)
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        self._measure_block(squares)
        return level, peak

    # -- processing ----------------------------------------------------------
    def process(self, x: np.ndarray, controller) -> np.ndarray:
        """Measure the block, delay it by one step and apply the gain.

        Parameters
        ----------
        x:
            The block of audio, shape ``(n_samples, channels)``.
        controller:
            Called once for every step of the grid with the K-weighted level
            of that step (in LUFS) and the peak of that step (linear).  It
            returns the gain for the step, in decibels.

        Returns
        -------
        numpy.ndarray
            The delayed block, with the gain of the step every sample belongs
            to applied to it.  The block has the length of the block that
            went in.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        count = buf.shape[0]
        if count == 0:
            return buf
        if buf.shape[1] != self.channels:
            # the channel count cannot change inside a stream: the grid
            # starts again, because its filter state has the wrong shape
            self.channels = int(buf.shape[1])
            self._weights = LoudnessMeter._channel_weights(self.channels)
            self.reset()
        self._input = np.concatenate((self._input, buf), axis=0)
        while self._input.shape[0] >= self.step:
            chunk = self._input[: self.step]
            self._input = self._input[self.step :]
            level, peak = self.measure(chunk)
            gain_db = float(controller(level, peak))
            self._output = np.concatenate(
                (self._output, chunk * (10.0 ** (gain_db / 20.0))), axis=0
            )
            self._steps += 1
        take = min(count, self._output.shape[0])
        out = np.zeros((count, buf.shape[1]), dtype=np.float64)
        if take:
            out[:take] = self._output[:take]
            self._output = self._output[take:]
        return out


# ---------------------------------------------------------------------------
# Limiting
# ---------------------------------------------------------------------------
class PeakLimiter:
    """True-peak limiter with an anticipatory gain ramp.

    The signal is examined in half millisecond frames; the peak of every frame
    is estimated on a four-times oversampled grid so the limiter reacts to
    inter-sample peaks rather than to the sample values alone.  The gain that
    a frame needs is ramped backwards from the peak that causes it, so the
    reduction is already in place when the peak arrives and the transition
    stays smooth.  The recovery uses an exponential release.

    Block coherence
    ---------------
    With ``look_ahead=True`` the frames sit on a **fixed grid** of half
    milliseconds and the module holds the signal back by one frame, so the
    reduction a frame needs is already known when the frame is played.  The
    answer is then the same whatever the size of the block the host hands
    over, and the delay the module puts into the signal is the one frame it
    declares.

    With ``look_ahead=False`` the limiter works in place and has no latency,
    but its frame grid follows the block the host hands over: that mode is
    for the callers that cannot afford a delay, and it is not block
    coherent.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_ms: float = 0.5,
        ramp_db_per_ms: float = 1.0,
        release_ms: float = 80.0,
        look_ahead: bool = False,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame = max(1, int(round(self.sample_rate * frame_ms / 1000.0)))
        frame_time = self.frame / float(self.sample_rate)
        self.ramp = ramp_db_per_ms * frame_time * 1000.0
        self.release = math.exp(-frame_time / max(1e-6, release_ms / 1000.0))
        self.look_ahead = bool(look_ahead)
        self._gain_db = np.zeros(max(1, int(channels)), dtype=np.float64)
        self._pending: np.ndarray | None = None
        self._pending_req: np.ndarray | None = None
        self._line = np.zeros((0, max(1, int(channels))), dtype=np.float64)
        self._input = np.zeros((0, max(1, int(channels))), dtype=np.float64)
        self._started = False

    @property
    def latency_samples(self) -> int:
        """The delay the limiter puts into the signal, in samples.

        The limiter works in place, so it adds no latency -- unless it was
        asked to look ahead, in which case it holds the signal back by two
        frames: one so that the reduction is in place when the peak arrives
        and one so that the frame whose gain is known can wait its turn.
        """
        return 2 * int(self.frame) if self.look_ahead else 0

    def reset(self) -> None:
        """Clear the gain state."""
        self._gain_db = np.zeros_like(self._gain_db)
        self._pending = None
        self._pending_req = None
        self._line = np.zeros((0, self._gain_db.shape[0]), dtype=np.float64)
        self._input = np.zeros((0, self._gain_db.shape[0]), dtype=np.float64)
        self._started = False

    def _oversampled_peak(self, buf: np.ndarray) -> np.ndarray:
        """Return the four-times oversampled peak envelope of the signal."""
        n = buf.shape[0]
        if n == 0:
            return np.zeros((0, buf.shape[1]), dtype=np.float64)
        # The edges are repeated, so that the interpolation has something to
        # read on both sides.  The padding is built by hand: the general
        # padding of numpy is an order of magnitude slower on a block of a
        # few dozen samples, and this is the hottest line of the limiter.
        last = buf[-1:]
        pad = np.concatenate((buf[:1], buf, last, last), axis=0)
        peak = np.abs(pad[1 : 1 + n])
        for coeffs in (
            (-1.0 / 16.0, 9.0 / 16.0, 9.0 / 16.0, -1.0 / 16.0),
            (-1.0 / 8.0, 5.0 / 8.0, 5.0 / 8.0, -1.0 / 8.0),
        ):
            inter = (
                coeffs[0] * pad[0:n]
                + coeffs[1] * pad[1 : 1 + n]
                + coeffs[2] * pad[2 : 2 + n]
                + coeffs[3] * pad[3 : 3 + n]
            )
            peak = np.maximum(peak, np.abs(inter))
        return peak

    def process(self, x: np.ndarray, ceiling_dbfs: float) -> np.ndarray:
        """Limit a block so that its true peak stays below ``ceiling_dbfs``."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        if self.look_ahead:
            return self._process_ahead(buf, ceiling_dbfs)
        peak = self._oversampled_peak(buf)
        ceiling = 10.0 ** (float(ceiling_dbfs) / 20.0)
        n_frames = n // self.frame
        if n_frames == 0:
            return buf
        with np.errstate(divide="ignore", invalid="ignore"):
            frame_peak = peak[: n_frames * self.frame].reshape(
                n_frames, self.frame, buf.shape[1]
            ).max(axis=1)
            required = np.where(
                frame_peak > ceiling,
                20.0 * np.log10(ceiling / np.maximum(frame_peak, 1e-12)),
                0.0,
            )
        if not np.any(required < 0.0):
            self._gain_db = self._gain_db * 0.0
            return buf
        # Ramp the requirement backwards so the reduction is in place when the
        # peak arrives: b[k] = min(required[k], b[k+1] + ramp).
        index = np.arange(n_frames, dtype=np.float64)[:, None]
        shifted = required + index * self.ramp
        ramped = np.minimum.accumulate(shifted[::-1], axis=0)[::-1] - index * self.ramp

        out = np.empty_like(buf)
        for frame in range(n_frames):
            req = ramped[frame]
            gain = self._gain_db
            self._gain_db = np.where(
                req < gain, req, gain + (req - gain) * self.release
            )
            lo = frame * self.frame
            hi = lo + self.frame
            out[lo:hi] = buf[lo:hi] * np.power(10.0, self._gain_db / 20.0)[None, :]
        if n_frames * self.frame < n:
            tail = buf[n_frames * self.frame:]
            out[n_frames * self.frame:] = tail * np.power(10.0, self._gain_db / 20.0)[None, :]
        return out

    def _process_ahead(self, buf: np.ndarray, ceiling_dbfs: float) -> np.ndarray:
        """Limit a block on the fixed frame grid, with two frames of look ahead.

        The frames sit on a fixed grid of half milliseconds, so the frame a
        sample belongs to does not depend on the size of the block the host
        hands over.  The signal is held back by two frames: one frame, so
        that the requirement of the frame that follows is known when the gain
        of a frame is chosen -- that is what puts the reduction in place
        before the peak arrives -- and one frame, so that the frame whose
        gain is known can be held until its turn comes.

        The delay is a constant number of samples, so the signal is not
        warped: every sample is moved by the same distance.
        """
        n = buf.shape[0]
        ceiling = 10.0 ** (float(ceiling_dbfs) / 20.0)
        if buf.shape[1] != self._gain_db.shape[0]:
            # the channel count cannot change inside a stream: the limiter
            # starts again, because its state has the wrong shape
            self.channels = int(buf.shape[1])
            self._gain_db = np.zeros(self.channels, dtype=np.float64)
            self._started = False
            self._pending = None
            self._pending_req = None
            self._line = np.zeros((0, self.channels), dtype=np.float64)
            self._input = np.zeros((0, self.channels), dtype=np.float64)
        if not self._started:
            # the line starts with two frames of silence, so the signal is
            # late by exactly the two frames the look ahead costs
            self._line = np.zeros((2 * self.frame, buf.shape[1]), dtype=np.float64)
            self._started = True
        line = self._line
        pending = self._pending
        pending_req = self._pending_req
        data = np.concatenate((self._input, buf), axis=0)
        while data.shape[0] >= self.frame:
            chunk = data[: self.frame]
            data = data[self.frame:]
            peak = self._oversampled_peak(chunk).max(axis=0)
            with np.errstate(divide="ignore", invalid="ignore"):
                required = np.where(
                    peak > ceiling,
                    20.0 * np.log10(ceiling / np.maximum(peak, 1e-12)),
                    0.0,
                )
            if pending is not None:
                # the frame that waits is played now: its own requirement and
                # the one of the frame that follows it are both known
                req = np.minimum(pending_req, required + self.ramp)
                if not np.any(req < 0.0):
                    self._gain_db = self._gain_db * 0.0
                else:
                    gain = self._gain_db
                    self._gain_db = np.where(
                        req < gain, req, gain + (req - gain) * self.release
                    )
                line = np.concatenate(
                    (line, pending * np.power(10.0, self._gain_db / 20.0)[None, :]),
                    axis=0,
                )
            pending = chunk
            pending_req = required
        self._input = data
        self._pending = pending
        self._pending_req = pending_req
        take = min(n, line.shape[0])
        out = np.zeros((n, buf.shape[1]), dtype=np.float64)
        if take:
            out[:take] = line[:take]
            line = line[take:]
        self._line = line
        return out


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------
@register
class LoudnessControl(Module):
    """Bring the programme loudness onto a chosen standard.

    The measurement runs on the fixed grid of :class:`LoudnessGrid`: the
    program is measured in steps of a tenth of a second, and the grid does
    not move with the size of the block the host hands over.  The signal is
    held back by one step, so the gain of the step that is being played is
    already known when it is played, and the module reaches the same answer
    in 64 sample blocks and in one block.  The delay is the latency the
    module declares.

    The gain comes from the **gated integrated loudness** of the programme:
    the mean of the 400 ms blocks that survive the absolute gate of
    -70 LUFS and the relative gate 10 LU below the mean of the blocks that
    are left.  That is the quantity a delivery target names, so the
    programme the module delivers reads on the target the way the standard
    says it does.
    """

    code = "AUR_LOUDNESS"
    display_name = "Loudness Control"
    category = "Utility"
    edition = "standard"
    element = "Loudness"

    #: How many steps the momentary window (400 ms) and the short term window
    #: (3 s) hold on the grid.  The momentary window is the block of the
    #: standard, so the momentary loudness is read straight off the grid.
    _MOMENTARY_STEPS = 4
    _SHORT_TERM_STEPS = 30

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._grid = LoudnessGrid(
            sample_rate, channels, GRID_STEP_S, block_s=_BLOCK_SECONDS
        )
        self._limiter = PeakLimiter(sample_rate, channels, look_ahead=True)
        self._gated = GatedLoudness()
        self._gated_started = False
        self._gain_db = 0.0
        self._levels: deque[float] = deque(maxlen=self._SHORT_TERM_STEPS)
        self._target = -23.0
        self._momentary_target = -23.0
        self._short_target = -23.0
        self._last_standard: str | None = None
        self._apply_standard(str(self.params.get("Standard")))

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Target Loudness",
                type="float",
                default=-23.0,
                minimum=-36.0,
                maximum=0.0,
                unit="LUFS",
                group="Target",
                help="Integrated loudness the programme is brought to.",
            ),
            ParamDesc(
                name="Tolerance",
                type="float",
                default=0.5,
                minimum=0.5,
                maximum=2.0,
                unit="LU",
                group="Target",
                help="Deviation that is accepted before the gain is moved.",
            ),
            ParamDesc(
                name="Short Term Loudness",
                type="float",
                default=-23.0,
                minimum=-36.0,
                maximum=0.0,
                unit="LUFS",
                group="Target",
                help="Target for the three second short-term loudness.",
            ),
            ParamDesc(
                name="Momentary Loudness",
                type="float",
                default=-23.0,
                minimum=-36.0,
                maximum=0.0,
                unit="LUFS",
                group="Target",
                help="Target for the 400 ms momentary loudness.",
            ),
            ParamDesc(
                name="True Peak",
                type="float",
                default=-1.0,
                minimum=-9.0,
                maximum=0.0,
                unit="dBTP",
                group="Target",
                help="True-peak ceiling of the output.",
            ),
            ParamDesc(
                name="Standard",
                type="enum",
                default="EBU R128",
                minimum=0.0,
                maximum=10.0,
                choices=STANDARD_NAMES,
                group="Target",
                help="Loudness standard; picking one overwrites the target fields.",
            ),
        ]

    # -- standard handling ----------------------------------------------------
    def _apply_standard(self, name: str) -> None:
        """Copy the values of a standard into the manual target fields."""
        values = STANDARDS.get(name)
        if values is None:
            return
        integrated, momentary, short_term, true_peak = values
        self.params.set("Target Loudness", integrated)
        self.params.set("Momentary Loudness", momentary)
        self.params.set("Short Term Loudness", short_term)
        self.params.set("True Peak", true_peak)
        self._last_standard = name
        # The measurement history belongs to the previous target.
        self._grid.reset()
        self._limiter.reset()
        self._gated.reset()
        self._gated_started = False
        self._gain_db = 0.0
        self._levels.clear()

    def _sync_standard(self) -> None:
        """Apply a newly selected standard, if any."""
        name = str(self.params.get("Standard"))
        if name != self._last_standard:
            self._apply_standard(name)

    # -- processing -----------------------------------------------------------
    def reset(self) -> None:
        """Clear the grid, the limiter and the applied gain."""
        self._grid.reset()
        self._limiter.reset()
        self._gated.reset()
        self._gated_started = False
        self._gain_db = 0.0
        self._levels.clear()

    @property
    def latency_samples(self) -> int:
        """The delay the module puts into the signal, in samples.

        The grid holds the signal back by one step, so the gain of the step
        that is being played is already known, and the limiter holds it back
        by one frame, so the reduction is in place before the peak arrives.
        """
        return int(self._grid.latency_samples + self._limiter.latency_samples)

    # -- the readings ---------------------------------------------------------
    @property
    def momentary_lufs(self) -> float | None:
        """The momentary loudness of the last 400 ms, in LUFS.

        The momentary window is ungated: it is the level of the last 400 ms
        of the programme, whatever that level is.
        """
        value = self._grid.block_level
        return float(value) if math.isfinite(value) else None

    @property
    def short_term_lufs(self) -> float | None:
        """The short term loudness of the last three seconds, in LUFS.

        The short term window is ungated, like the momentary one.
        """
        return self._window_level(self._SHORT_TERM_STEPS)

    @property
    def integrated_lufs(self) -> float | None:
        """The gated integrated loudness of the programme so far, in LUFS.

        This is the quantity the gain is derived from: the mean of the
        400 ms blocks that survive the absolute gate and the relative gate.
        """
        return self._gated.integrated()

    @property
    def gain_db(self) -> float:
        """The gain the module applies to the programme, in decibels."""
        return float(self._gain_db)

    def _targets(self) -> tuple[float, float, float, float]:
        """Return the effective targets (integrated, momentary, short, peak)."""
        self._sync_standard()
        return (
            float(self.params.get("Target Loudness")),
            float(self.params.get("Momentary Loudness")),
            float(self.params.get("Short Term Loudness")),
            float(self.params.get("True Peak")),
        )

    def _control(self, level_db: float, peak: float) -> float:
        """The gain of one step of the grid, in decibels.

        The gain is the one that brings the **gated integrated loudness** of
        the programme onto the target.  The measurement is the gated mean of
        the 400 ms blocks the grid has measured so far: the absolute gate of
        -70 LUFS and the relative gate 10 LU below the mean of the blocks
        that are left, exactly as the standard names them.  The blocks sit
        on the grid, so the measurement is the same number in every block
        size.

        Before the first complete block of the standard is there, the
        measurement is made of the blocks the grid has, each of them over
        the material there is: that is the programme so far, and it is the
        only estimate there is until the first block of the standard
        arrives.  When that block arrives it replaces them, and from then on
        the measurement is the measurement of the standard.

        The momentary and the short term are ungated: they are the level of
        the last 400 ms and of the last three seconds.  Their targets act
        as limits on the gain: they cap how far the module may push the
        programme up.  They do not pull the programme down below the
        integrated target, because the integrated loudness is the quantity a
        delivery names, and on transient material the momentary loudness
        sits above the integrated loudness by nature.
        """
        target = self._target
        tolerance = float(self.params.get("Tolerance"))
        power = (
            10.0 ** ((level_db - _LOUDNESS_OFFSET) / 10.0)
            if math.isfinite(level_db)
            else 0.0
        )
        self._levels.append(power)
        # The momentary loudness is the level of the last 400 ms of the
        # material.  It is ungated: a window is either in the measurement or
        # it is not.
        momentary = self._grid.block_level
        # The block of 400 ms that ends at this step boundary goes into the
        # gated measurement.  The blocks before the first complete one are
        # the bootstrap of the gain: they are measured over the material
        # there is, and the first complete block replaces them.
        if self._grid.block_complete and not self._gated_started:
            self._gated.reset()
            self._gated_started = True
        self._gated.add(momentary)
        integrated = self._gated.integrated()
        if integrated is None:
            if not math.isfinite(momentary):
                # the material is silent: there is nothing to correct, and
                # the gain of the silence does not matter
                self._gain_db = 0.0
                return 0.0
            gain_db = target - momentary
        else:
            gain_db = target - integrated
        if abs(gain_db) <= tolerance:
            gain_db = 0.0
        self._gain_db = gain_db

        # The short-term and momentary targets act as limits: when the
        # programme runs above them the gain is pulled back.  The levels are
        # the ones of the material; the gain that is applied to it is added
        # to them, because that is what the output carries.
        trim = 0.0
        short = self._window_level(self._SHORT_TERM_STEPS)
        if short is not None and short + gain_db > self._short_target:
            trim = min(trim, self._short_target - short - gain_db)
        if math.isfinite(momentary) and momentary + gain_db > self._momentary_target:
            trim = min(trim, self._momentary_target - momentary - gain_db)
        if gain_db <= 0.0:
            # The module is lowering the programme to reach the integrated
            # target: the momentary and short-term limits do not pull it
            # down further, or the programme would miss the target the
            # delivery names.
            trim = 0.0
        return gain_db + trim

    def _window_level(self, steps: int) -> float | None:
        """The level of the last ``steps`` steps of the grid, in LUFS."""
        if len(self._levels) < steps:
            return None
        window = list(self._levels)[-steps:]
        mean = sum(window) / len(window)
        if mean <= 0.0:
            return None
        return _LOUDNESS_OFFSET + 10.0 * math.log10(mean)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Bring the block onto the selected loudness target."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        target, momentary_target, short_target, peak_ceiling = self._targets()
        self._target = target
        self._momentary_target = momentary_target
        self._short_target = short_target
        out = self._grid.process(buf, self._control)
        out = self._limiter.process(out, peak_ceiling)
        return out
