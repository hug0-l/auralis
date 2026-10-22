"""
aurcore.modules.resample — band limited sample rate conversion.

The resampler is a windowed sinc interpolator.  Its cutoff follows the lower
of the two Nyquist frequencies, the window is a Kaiser window whose width
follows the steepness control, and the kernel can be blended between a
symmetric (linear phase) shape and a minimum phase shape.  The minimum phase
kernel is obtained with the homomorphic method: the log magnitude of the
symmetric kernel is kept and the phase is replaced by its minimum phase
version, which moves the response behind the impulse and therefore removes
the pre-ringing.

How it is evaluated
-------------------
The read position of output sample ``n`` is ``n / ratio``.  When the ratio
is rational (``up``/``down``) the fractional part of that position takes only
``up`` different values, so the interpolation kernel is evaluated **once per
phase** when the settings change and looked up afterwards.  The table holds
the exact kernel values — nothing is interpolated and nothing is rounded
away — so the lookup is not an approximation of the kernel but the same
kernel.

The read position itself is computed in exact integer arithmetic
(``n * down`` divided by ``up``), which is also why the table is exact: the
float division ``n / ratio`` loses a bit at every output whose position is
an integer, and the minimum phase kernel is not shift invariant, so that
bit turned into a visible error every ``up`` samples.

Transparency
------------
The kernel of every phase is normalised so that its weights sum to one.
That is what makes the resampler transparent: a constant goes in and the
same constant comes out, at every block size and at the first and the last
sample of a block.  Without the normalisation the sum of the weights of a
phase is 0.998 to 1.001 — the Kaiser window is not a partition of unity —
and a constant of 0.5 came out as 0.4995.

The kernel and the block
------------------------
The kernel of the interpolation reaches ``half_width`` (48) samples to the
**right** of the read position and ``half_width - 1`` (47) samples to the
**left** of it.  A block that is resampled on its own therefore cuts the
kernel in two: the first outputs of the block are computed from a kernel
whose left half is missing, and the last outputs from a kernel whose right
half is missing.  A constant of 0.5 came out as 0.5647 at the start of a
block and as 0.3056 at its end.

The resampler is therefore a **streaming** filter:

* it keeps the samples that came before the block (the history), so that
  the left half of the kernel is always there, and
* it delays its output by the look ahead the kernel needs, so that the
  right half of the kernel is always there too.

The delay is the latency of the module, and it is declared:
:attr:`Resample.latency_samples`.  It is 48 output samples at the ratio of
one — 1.0 ms at 48 kHz — and it grows with the ratio, because the kernel
reaches the same number of *input* samples to the right whatever the ratio
is.  The output of the module is the ideal resample of the input, delayed
by that many samples: the first samples of a stream are the ramp of the
filter, which is the price of the delay and the reason it is declared.

Block coherence
---------------
The read position is counted from the start of the *stream*, not from the
start of the block, so one block of 256 samples and four blocks of 64
samples give the same answer.  The number of output samples of a block is
the number of read positions that fall into it, which is the exact number
for a ratio of one and one sample more or less for the other ratios.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["Resample", "SincResampler", "TARGET_RATES"]

#: Sample rates the module can convert to.
TARGET_RATES: tuple[str, ...] = (
    "22050",
    "32000",
    "44100",
    "48000",
    "88200",
    "96000",
    "176400",
    "192000",
)

#: Oversampling of the kernel grid used by the minimum phase transform.
_FINE = 8

#: Largest number of polyphase branches the precomputed table may hold.
#: The module's own rate pairs need at most 320 of them (32 kHz to
#: 44.1 kHz is 320/441); the limit keeps the table and the time it takes
#: to build it small for exotic ratios.
_MAX_PHASES = 1024

#: How many output samples are filtered at a time.  The gathered windows of
#: a whole block do not fit into the cache any more, and a gather that has
#: to go to the memory three times the time of one that does not.
_CHUNK = 256


def _next_pow2(n: int) -> int:
    value = 1
    while value < int(n):
        value <<= 1
    return value


def _bessel_i0(x: float) -> float:
    """Modified Bessel function of the first kind, order zero."""
    total = 1.0
    term = 1.0
    for index in range(1, 64):
        term *= (x / (2.0 * index)) ** 2
        total += term
        if term < 1e-16 * total:
            break
    return total


def _kaiser(window: np.ndarray, beta: float) -> np.ndarray:
    """Return a Kaiser window of the given shape."""
    if beta <= 0.0:
        return np.ones_like(window)
    scale = 1.0 / _bessel_i0(beta)
    return np.i0(beta * np.sqrt(np.maximum(0.0, 1.0 - window * window))) * scale


def _sinc(x: np.ndarray) -> np.ndarray:
    """Normalised sinc function ``sin(pi x) / (pi x)``."""
    out = np.ones_like(x)
    mask = np.abs(x) > 1e-12
    out[mask] = np.sin(np.pi * x[mask]) / (np.pi * x[mask])
    return out


def _rational_parts(ratio: float, max_denominator: int = _MAX_PHASES) -> tuple[int, int] | None:
    """Return the coprime ``(up, down)`` of a rational ratio, or ``None``.

    A ratio that is not rational with a small denominator — a pitch shift of
    a quarter tone, say — has no repeating phase pattern, so it cannot use
    the polyphase table.

    Parameters
    ----------
    ratio:
        The conversion ratio (output rate divided by input rate).
    max_denominator:
        The largest denominator the fraction may have.

    Returns
    -------
    tuple of int or None
        ``(up, down)`` with ``up / down == ratio`` and ``gcd(up, down) == 1``,
        or ``None`` when the ratio is not such a fraction.
    """
    if not math.isfinite(ratio) or ratio <= 0.0:
        return None
    parts = Fraction(ratio).limit_denominator(int(max_denominator))
    value = parts.numerator / parts.denominator
    # The fraction has to be the ratio itself, not a neighbour of it: a
    # fraction that is merely close would shift the read position.
    if abs(value - ratio) > 8.0 * math.ulp(ratio):
        return None
    return parts.numerator, parts.denominator


class SincResampler:
    """Band limited windowed sinc resampler.

    ``ratio`` is the output rate divided by the input rate.  ``pre_ringing``
    blends between the minimum phase kernel (0) and the symmetric linear
    phase kernel (1).

    The kernel is evaluated once per phase when the resampler is built (see
    the module docstring); :meth:`process` only looks the weights up.

    The resampler is a streaming filter: it keeps the history of the input
    and delays its output by :attr:`delay`, so that every output is computed
    from the whole kernel.  Feed it blocks with :meth:`process` and finish
    with :meth:`flush`.
    """

    def __init__(
        self,
        ratio: float,
        sample_rate: int = 48000,
        steepness: float = 0.5,
        pre_ringing: float = 0.0,
        half_width: int = 48,
    ):
        self.ratio = float(ratio)
        self.sample_rate = int(sample_rate)
        self.steepness = float(steepness)
        self.pre_ringing = float(pre_ringing)
        self.half_width = int(half_width)
        self.cutoff = 0.5 * min(1.0, 1.0 / max(self.ratio, 1e-9))
        self.beta = 4.0 + 8.0 * self.steepness
        #: The tap offsets, from ``-half_width + 1`` to ``half_width``.
        self._offsets = np.arange(-self.half_width + 1, self.half_width + 1, dtype=np.int64)
        #: The same tap offsets as floats, for the kernel evaluation.
        self._offsets_f = self._offsets.astype(np.float64)
        self._table: np.ndarray | None = None
        self._up = 0
        self._down = 0
        self._build()
        #: The delay of the resampler, in output samples.
        self._delay = self._compute_delay()
        self.reset()

    # -- kernel ---------------------------------------------------------------
    def _symmetric_kernel(self, u: np.ndarray) -> np.ndarray:
        """Evaluate the symmetric (linear phase) interpolation kernel."""
        a = float(self.half_width)
        window = _kaiser(np.clip(np.abs(u) / a, 0.0, 1.0), self.beta)
        return 2.0 * self.cutoff * _sinc(2.0 * self.cutoff * u) * window

    def _build(self) -> None:
        """Build the minimum phase kernel, the blend factors and the table."""
        count = 2 * self.half_width * _FINE + 1
        u = (np.arange(count, dtype=np.float64) - self.half_width * _FINE) / _FINE
        symmetric = self._symmetric_kernel(u)
        self._min_phase = None
        self._min_peak = 0
        if self.pre_ringing < 0.999:
            self._min_phase, self._min_peak = self._minimum_phase(symmetric)
        self._build_table()

    def _minimum_phase(self, symmetric: np.ndarray):
        """Return the minimum phase impulse response of a symmetric kernel.

        The magnitude of the kernel is kept and the phase is replaced by its
        minimum phase version, which the Hilbert transform of the logarithm of
        the magnitude gives.  The result is causal, so the response before the
        impulse is gone and the pre-ringing disappears.
        """
        size = _next_pow2(len(symmetric) * 2)
        spectrum = np.fft.fft(symmetric, size)
        peak = float(np.max(np.abs(spectrum)))
        floor = max(peak * 1e-8, 1e-15)
        log_magnitude = np.log(np.maximum(np.abs(spectrum), floor))
        # The analytic signal of the log magnitude carries the minimum phase.
        weights = np.zeros(size, dtype=np.float64)
        weights[0] = 1.0
        if size % 2 == 0:
            weights[size // 2] = 1.0
            weights[1 : size // 2] = 2.0
        else:
            weights[1 : (size + 1) // 2] = 2.0
        analytic = np.fft.ifft(np.fft.fft(log_magnitude) * weights)
        minimum = np.fft.ifft(
            np.exp(log_magnitude - 1j * np.imag(analytic))
        ).real
        index = int(np.argmax(np.abs(minimum)))
        return minimum, index

    def _weight(self, u: np.ndarray) -> np.ndarray:
        """Evaluate the blended interpolation kernel.

        The kernel of every phase is normalised so that its weights sum to
        one.  That is what makes the resampler transparent: a constant goes
        in and the same constant comes out.  The Kaiser window is not a
        partition of unity, so the sum of the weights of a phase is 0.998 to
        1.001 before the normalisation.

        The normalisation is per phase (the last axis), so the kernel of a
        single phase — a one dimensional array — is normalised as a whole.
        """
        linear = self._symmetric_kernel(u)
        if self.pre_ringing >= 0.999 or self._min_phase is None:
            value = linear
        else:
            minimum = self._min_phase
            # The minimum phase kernel is causal: the sample delay is measured
            # from the position of its own impulse response.  The array lives on
            # the fine grid, so the delay is counted in fine steps.
            position = u * _FINE + self._min_peak
            index = np.clip(position, 0.0, len(minimum) - 1.0001)
            low = np.floor(index).astype(np.int64)
            frac = index - low
            value = minimum[low] * (1.0 - frac) + minimum[low + 1] * frac
            blend = self.pre_ringing
            value = value * (1.0 - blend) + linear * blend
        total = np.sum(value, axis=-1, keepdims=True)
        # a phase whose weights cancel out would divide by zero
        total = np.where(np.abs(total) < 1e-300, 1.0, total)
        return value / total

    # -- the polyphase table --------------------------------------------------
    def _build_table(self) -> None:
        """Evaluate the kernel once for every phase the ratio can produce.

        The read position of output sample ``n`` is ``n * down / up``, so its
        fractional part is ``(n * down) mod up`` divided by ``up``: there are
        ``up`` phases and every one of them is used.  The table holds the
        kernel of every phase and every tap, evaluated with the same
        function the per-sample path uses, so the two agree to the last bit.
        """
        self._table = None
        self._up = 0
        self._down = 0
        parts = _rational_parts(self.ratio)
        if parts is None:
            return
        up, down = parts
        if up > _MAX_PHASES:
            return
        phases = np.arange(up, dtype=np.float64) / float(up)
        self._table = self._weight(phases[:, None] - self._offsets_f[None, :])
        self._up = up
        self._down = down

    @property
    def phases(self) -> int:
        """Number of phases the kernel table holds (0 when there is none)."""
        return 0 if self._table is None else int(self._table.shape[0])

    # -- the delay ------------------------------------------------------------
    def _compute_delay(self) -> int:
        """Return the delay of the resampler, in output samples.

        The kernel reaches ``half_width`` input samples to the right of the
        read position, so the output has to be held back by that many input
        samples.  The delay is counted in the samples of the output, which
        is the unit a host sees, so it grows with the ratio.
        """
        if self._table is not None:
            scaled = self.half_width * self._up
            return -((-scaled) // self._down)
        return int(math.ceil(self.half_width * self.ratio))

    @property
    def delay(self) -> int:
        """The delay of the resampler, in output samples."""
        return int(self._delay)

    @property
    def look_ahead(self) -> int:
        """The number of input samples the kernel reaches to the right."""
        return int(self.half_width)

    # -- the streaming state --------------------------------------------------
    def reset(self) -> None:
        """Clear the streaming state."""
        self._buf: np.ndarray | None = None
        self._origin = 0  # the absolute index of _buf[0]
        self._in_count = 0  # the number of input samples received
        self._out_count = 0  # the number of output samples handed out
        self._channels = 0

    def _positions(self, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return the read index and the phase of every ideal output.

        The read position of the ideal output ``m`` is ``m * down / up``, so
        the integer part and the phase are exact integer arithmetic: no
        rounding and no drift over a long signal.
        """
        scaled = m * self._down
        base = scaled // self._up
        phase = scaled - base * self._up
        return base, phase

    def _float_positions(self, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return the read index and the fraction of every ideal output."""
        positions = m.astype(np.float64) / self.ratio
        base = np.floor(positions).astype(np.int64)
        return base, positions - base

    def _limit(self, t: int) -> int:
        """The first output that needs an input sample at or after ``t``.

        The output ``n`` is the ideal output ``n - delay``, and the ideal
        output ``m`` reads the input up to ``floor(m * down / up) +
        half_width``.  The output is therefore ready as soon as that input
        sample has arrived, and it waits for the block that carries it.
        """
        if self._table is not None:
            scaled = (int(t) - self.half_width) * self._up
            return self._delay + (-((-scaled) // self._down))
        return int(
            math.ceil(self._delay + (int(t) - self.half_width) * self.ratio)
        )

    def _keep_history(self, merged: np.ndarray, origin: int) -> None:
        """Keep the samples the next block still needs, drop the rest."""
        keep = 2 * self.half_width
        drop = merged.shape[0] - keep
        if drop > 0:
            self._buf = merged[drop:]
            self._origin = origin + drop
        else:
            self._buf = merged
            self._origin = origin

    # -- resampling -----------------------------------------------------------
    def _apply(
        self,
        buf: np.ndarray,
        origin: int,
        base: np.ndarray,
        weights: np.ndarray,
        first_output: int,
    ) -> np.ndarray:
        """Multiply the block with the weights of every output sample.

        The taps of one output sample are a *window* of the buffer, so the
        windows are taken with a sliding window view: the view is free and
        one gather brings in the whole window of every output sample.

        The outputs are taken in chunks, because a whole block of gathered
        windows does not fit into the cache any more: the gather then costs
        three times what it costs in chunks.  The chunks are aligned to the
        index of the output in the stream, so that the same output is
        computed the same way whatever the size of the block is.
        """
        taps = self._offsets.size
        windows = np.lib.stride_tricks.sliding_window_view(buf, taps, axis=0)
        n_out = int(base.shape[0])
        out = np.empty((n_out, buf.shape[1]), dtype=np.float64)
        # the taps reach `half_width - 1` samples to the left of the read
        # position, so the window of an output starts there
        left = self.half_width - 1
        start = 0
        while start < n_out:
            step = _CHUNK - ((first_output + start) % _CHUNK)
            stop = min(start + max(1, step), n_out)
            gathered = windows[base[start:stop] - origin - left]
            # (chunk, channels, taps) @ (chunk, taps, channels) -> (chunk, channels)
            stacked = np.ascontiguousarray(gathered.transpose(0, 2, 1))
            out[start:stop] = np.matmul(weights[start:stop, None, :], stacked)[:, 0]
            start = stop
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Resample one block of audio.

        The block is appended to the history of the resampler and the
        outputs whose kernel is complete are returned.  The output is
        delayed by :attr:`delay` samples, so the first outputs of a stream
        are the ramp of the filter.

        Parameters
        ----------
        x:
            The block, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The resampled samples of the block, the same number of samples
            as the block has for a ratio of one and one sample more or less
            for the other ratios.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n_in = int(buf.shape[0])
        channels = int(buf.shape[1])
        if n_in == 0:
            return np.zeros((0, channels), dtype=np.float64)
        if self._channels and channels != self._channels:
            # the channel count changed: the history does not fit any more
            self.reset()
        self._channels = channels

        start = self._in_count
        if self._buf is None or self._buf.shape[0] == 0:
            # the samples before the start of the stream are silence: the
            # kernel of the first outputs reaches that far back
            silence = np.zeros((self.half_width - 1, channels), dtype=np.float64)
            merged = np.concatenate((silence, buf), axis=0)
            origin = start - (self.half_width - 1)
        else:
            merged = np.concatenate((self._buf, buf), axis=0)
            origin = self._origin
        self._in_count = start + n_in

        first = self._out_count
        stop = self._limit(self._in_count)
        count = stop - first
        if count <= 0:
            self._keep_history(merged, origin)
            return np.zeros((0, channels), dtype=np.float64)

        out = np.zeros((count, channels), dtype=np.float64)
        # the outputs before the delay are the ramp of the filter: the read
        # position is before the start of the stream
        begin = max(first, self._delay)
        if begin < stop:
            m = np.arange(begin, stop, dtype=np.int64) - self._delay
            if self._table is not None:
                base, phase = self._positions(m)
                weights = self._table[phase]
            else:
                base, frac = self._float_positions(m)
                weights = self._weight(frac[:, None] - self._offsets_f[None, :])
            out[begin - first :] = self._apply(merged, origin, base, weights, begin)
        self._out_count = stop
        self._keep_history(merged, origin)
        return out

    def flush(self) -> np.ndarray:
        """Return the tail of the conversion.

        The tail is the part of the filter that still rings after the input
        stopped: the outputs whose read position is inside the signal and
        the ramp that follows it.  The module does not need it — the host
        keeps feeding the plug-in — but a file writer does.
        """
        if self._channels == 0:
            return np.zeros((0, 0), dtype=np.float64)
        if self._table is not None:
            scaled = self._in_count * self._up
            stop = self._delay + (-((-scaled) // self._down))
        else:
            stop = int(math.ceil(self._delay + self._in_count * self.ratio))
        count = max(0, stop - self._out_count)
        if count == 0:
            return np.zeros((0, self._channels), dtype=np.float64)

        buf = self._buf if self._buf is not None else np.zeros((0, self._channels))
        # the samples after the end of the signal are silence
        padded = np.zeros((buf.shape[0] + self.half_width, self._channels), dtype=np.float64)
        padded[: buf.shape[0]] = buf
        origin = self._origin

        out = np.zeros((count, self._channels), dtype=np.float64)
        begin = max(self._out_count, self._delay)
        if begin < stop:
            m = np.arange(begin, stop, dtype=np.int64) - self._delay
            if self._table is not None:
                base, phase = self._positions(m)
                weights = self._table[phase]
            else:
                base, frac = self._float_positions(m)
                weights = self._weight(frac[:, None] - self._offsets_f[None, :])
            out[begin - self._out_count :] = self._apply(padded, origin, base, weights, begin)
        self._out_count = stop
        return out

    def resample(self, x: np.ndarray) -> np.ndarray:
        """Resample a whole block of audio in one go.

        This is the stateless path: the block is the whole signal, so the
        kernel is cut at both ends of it.  The streaming path
        (:meth:`process`) is the one a plug-in uses; this one is kept for
        the callers that resample a buffer that is complete on its own.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n_in = buf.shape[0]
        if n_in == 0:
            return buf
        n_out = int(round(n_in * self.ratio))
        if n_out <= 0:
            return np.zeros((0, buf.shape[1]), dtype=np.float64)
        m = np.arange(n_out, dtype=np.int64)
        if self._table is not None:
            base, phase = self._positions(m)
            weights = self._table[phase]
        else:
            base, frac = self._float_positions(m)
            weights = self._weight(frac[:, None] - self._offsets_f[None, :])
        return self._apply_padded(buf, base, weights)
    def _apply_padded(
        self, buf: np.ndarray, base: np.ndarray, weights: np.ndarray
    ) -> np.ndarray:
        """Apply the weights to a buffer that is the whole signal.

        The buffer is padded with zeros on both sides, so a tap that reads
        outside the signal contributes nothing — the same thing the
        per-sample path does when it skips those taps.
        """
        n_in = buf.shape[0]
        channels = buf.shape[1]
        taps = self._offsets.size
        # the taps reach `half_width - 1` samples to the left and
        # `half_width` samples to the right of the read position
        pad = self.half_width - 1
        # the padded buffer has to cover the whole output range, not only the
        # input range, because an upsampling ratio reads past the end
        size = max(pad + n_in, int(base[-1]) + taps)
        padded = np.zeros((size, channels), dtype=np.float64)
        padded[pad : pad + n_in] = buf
        return self._apply(padded, -pad, base, weights, 0)

    def resample_exact(self, x: np.ndarray) -> np.ndarray:
        """Resample a block with the kernel evaluated for every output sample.

        This is the yardstick the phase table is measured against: the same
        kernel, evaluated at the read position of every output sample instead
        of being looked up.  It is also the path a ratio without a repeating
        phase pattern takes.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n_in = buf.shape[0]
        if n_in == 0:
            return buf
        n_out = int(round(n_in * self.ratio))
        if n_out <= 0:
            return np.zeros((0, buf.shape[1]), dtype=np.float64)
        m = np.arange(n_out, dtype=np.int64)
        base, frac = self._float_positions(m)
        weights = self._weight(frac[:, None] - self._offsets_f[None, :])
        return self._apply_padded(buf, base, weights)


@register
class Resample(Module):
    """Convert the sample rate of the signal."""

    code = "AUR_RESAMPLE"
    display_name = "Resample"
    category = "Utility"
    edition = "standard"
    element = "Resampler"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._resampler: SincResampler | None = None
        self._key: tuple | None = None

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Sample Rate",
                type="enum",
                default="48000",
                minimum=0.0,
                maximum=8.0,
                choices=TARGET_RATES,
                group="Output",
                help="Sample rate the signal is converted to.",
            ),
            ParamDesc(
                name="Filter Steepness",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Filter",
                help="How sharp the anti-aliasing filter is.",
            ),
            ParamDesc(
                name="Pre Ringing",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=1.0,
                group="Filter",
                help="Zero is a minimum phase kernel, one a linear phase kernel.",
            ),
            ParamDesc(
                name="Post Limiter",
                type="bool",
                default=False,
                minimum=0.0,
                maximum=1.0,
                group="Output",
                help="Catches peaks that appear after the conversion.",
            ),
        ]

    def reset(self) -> None:
        """Drop the cached filter."""
        self._resampler = None
        self._key = None

    def _get_resampler(self) -> SincResampler:
        """Return the resampler for the current settings."""
        target = int(self.params.get("Sample Rate"))
        ratio = target / float(self.sample_rate)
        key = (
            target,
            float(self.params.get("Filter Steepness")),
            float(self.params.get("Pre Ringing")),
        )
        if self._resampler is None or self._key != key:
            self._resampler = SincResampler(
                ratio,
                self.sample_rate,
                steepness=float(self.params.get("Filter Steepness")),
                pre_ringing=float(self.params.get("Pre Ringing")),
            )
            self._key = key
        return self._resampler

    @property
    def latency_samples(self) -> int:
        """The delay of the conversion, in the samples of the output.

        The kernel of the interpolation reaches 48 input samples to the
        right of the read position, so the output has to be held back by
        that much.  The delay is counted in the samples of the output,
        which is the unit a host sees: 48 samples at the ratio of one
        (1.0 ms at 48 kHz), and more when the rate is raised.
        """
        return self._get_resampler().delay

    @property
    def output_sample_rate(self) -> int:
        """The rate the converted signal runs at.

        The module changes the rate of the signal, and the engine has to
        know it: the samples it hands over belong to this rate, and the
        latency and the tail of the chain are counted in the samples of
        it.
        """
        return int(self.params.get("Sample Rate"))

    def process(self, x: np.ndarray) -> np.ndarray:
        """Resample a block of audio to the selected rate."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        out = self._get_resampler().process(buf)
        if bool(self.params.get("Post Limiter")) and out.size:
            from .loudness_control import PeakLimiter

            if not hasattr(self, "_limiter"):
                self._limiter = PeakLimiter(self.sample_rate, buf.shape[1])
            out = self._limiter.process(out, -0.1)
        return out
