"""
aurcore.modules.monitor — loudness and peak metering.

``Monitor`` is a system module that leaves the audio untouched and measures
it.  The loudness figures follow ITU-R BS.1770-4 (the same definition the
EBU R128 meters use):

* K weighted analysis (pre-filter plus RLB high pass),
* 400 ms blocks with 75 % overlap,
* an absolute gate at -70 LUFS and a relative gate 10 LU below the ungated
  mean for the integrated value,
* momentary (400 ms), short term (3 s) and integrated loudness,
* a 4x oversampled true peak meter (dBTP).

``Monitor Loudness`` switches the loudness analysis off when it is not
needed; the true peak meter keeps running because it is far cheaper.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["Monitor"]

try:  # pragma: no cover - scipy is optional
    from scipy.signal import sosfilt as _sosfilt
    from scipy.signal import sosfilt_zi as _sosfilt_zi

    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

#: Channel weights of ITU-R BS.1770-4 (1.0 for the front channels).
_CHANNEL_WEIGHTS = (1.0, 1.0, 1.41, 1.41, 1.41, 1.41)

#: Absolute gate of the integrated loudness, in LUFS.
_ABSOLUTE_GATE = -70.0

#: Relative gate below the ungated mean, in LU.
_RELATIVE_GATE = 10.0


def _shelf_sos(sample_rate: int) -> np.ndarray:
    """High shelf pre-filter of the K weighting, designed for ``sample_rate``.

    The design follows the parameters of ITU-R BS.1770-4: a shelf with
    ``f0 = 1681.974 Hz``, a gain of ``3.99984 dB`` and ``Q = 0.7071752``.
    """
    f0 = 1681.974450955533
    gain_db = 3.999843853973347
    quality = 0.7071752369334195
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / float(sample_rate)
    cos_w0 = np.cos(w0)
    alpha = np.sin(w0) / (2.0 * quality)
    two_sqrt_a_alpha = 2.0 * np.sqrt(a) * alpha
    b0 = a * ((a + 1.0) + (a - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_w0)
    b2 = a * ((a + 1.0) + (a - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a + 1.0) - (a - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cos_w0)
    a2 = (a + 1.0) - (a - 1.0) * cos_w0 - two_sqrt_a_alpha
    return np.array(
        [[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float64
    )


def _rlb_sos(sample_rate: int) -> np.ndarray:
    """RLB high pass of the K weighting, designed for ``sample_rate``.

    ITU-R BS.1770-4 specifies a second order high pass at 38.13547 Hz; the
    48 kHz coefficients of the recommendation are used verbatim and the
    other rates are built from the same prototype.
    """
    if sample_rate == 48000:
        return np.array(
            [[1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]], dtype=np.float64
        )
    k = np.tan(np.pi * 38.13547046637679 / float(sample_rate))
    b = (1.0 - k) / (1.0 + k)
    a = -(1.0 - k) / (1.0 + k)
    return np.array(
        [
            [1.0, b, 0.0, 1.0, a, 0.0],
            [1.0, b, 0.0, 1.0, a, 0.0],
        ],
        dtype=np.float64,
    )


def _k_weighting_sos(sample_rate: int) -> np.ndarray:
    """Return the second order sections of the K weighting filter."""
    pre = np.array(
        [
            1.53512485958697,
            -2.69169618940638,
            1.19839281085285,
            1.0,
            -1.69065929318241,
            0.73248077421585,
        ],
        dtype=np.float64,
    ) if sample_rate == 48000 else _shelf_sos(sample_rate)[0]
    return np.vstack([pre.reshape(1, 6), _rlb_sos(sample_rate)])


class _BiquadCascade:
    """Second order IIR filter with per section state.

    Uses :func:`scipy.signal.sosfilt` when SciPy is available and a numpy
    fallback otherwise; both keep the filter state between blocks.
    """

    def __init__(self, sos: np.ndarray, channels: int):
        self.sos = np.asarray(sos, dtype=np.float64)
        self.channels = int(channels)
        if _HAVE_SCIPY:
            self._zi = _sosfilt_zi(self.sos)[:, :, None] * np.ones((1, 1, self.channels))
            self._zi = self._zi.reshape(self.sos.shape[0], 2, self.channels)
        else:
            self._zi = np.zeros((self.sos.shape[0], 2, self.channels), dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Filter a block of audio, keeping the filter state."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        if _HAVE_SCIPY:
            out, self._zi = _sosfilt(self.sos, a, axis=0, zi=self._zi)
            return out
        out = a
        for index, section in enumerate(self.sos):
            b0, b1, b2, a0, a1, a2 = section
            state = self._zi[index]
            result = np.empty_like(out)
            for n in range(out.shape[0]):
                for channel in range(out.shape[1]):
                    value = out[n, channel]
                    y = b0 * value + state[0, channel]
                    state[0, channel] = b1 * value - a1 * y + state[1, channel]
                    state[1, channel] = b2 * value - a2 * y
                    result[n, channel] = y
            out = result
        return out

    def reset(self) -> None:
        """Clear the filter state."""
        if _HAVE_SCIPY:
            self._zi = _sosfilt_zi(self.sos)[:, :, None] * np.ones((1, 1, self.channels))
            self._zi = self._zi.reshape(self.sos.shape[0], 2, self.channels)
        else:
            self._zi = np.zeros((self.sos.shape[0], 2, self.channels), dtype=np.float64)


class _LoudnessMeter:
    """ITU-R BS.1770-4 loudness meter.

    The meter accumulates 100 ms chunks of K weighted audio, derives the
    400 ms momentary blocks from them and keeps the block values for the
    gated integrated measurement.
    """

    CHUNK_SECONDS = 0.1
    BLOCKS_PER_MOMENTARY = 4     # 400 ms
    CHUNKS_PER_SHORT_TERM = 30   # 3 s

    def __init__(self, sample_rate: int, channels: int):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._filter = _BiquadCascade(_k_weighting_sos(sample_rate), channels)
        self._chunk = max(1, int(round(self.CHUNK_SECONDS * sample_rate)))
        self._acc = np.zeros(channels, dtype=np.float64)
        self._pos = 0
        self._chunks: list[np.ndarray] = []
        self._blocks: list[np.ndarray] = []
        self._weights = np.array(
            [_CHANNEL_WEIGHTS[i] if i < len(_CHANNEL_WEIGHTS) else 1.41 for i in range(channels)],
            dtype=np.float64,
        )

    # -- helpers --------------------------------------------------------------
    def _loudness_of(self, z: np.ndarray) -> float:
        """Return the loudness of a per channel mean square vector."""
        total = float(np.sum(self._weights * z))
        if total <= 0.0:
            return -np.inf
        return -0.691 + 10.0 * np.log10(total)

    def _mean_of(self, vectors: list[np.ndarray]) -> np.ndarray:
        """Return the mean of a list of per channel mean square vectors."""
        if not vectors:
            return np.zeros(self.channels, dtype=np.float64)
        return np.mean(np.asarray(vectors, dtype=np.float64), axis=0)

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> None:
        """Feed a block of audio into the meter."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return
        weighted = self._filter.process(a)
        position = 0
        while position < weighted.shape[0]:
            take = min(self._chunk - self._pos, weighted.shape[0] - position)
            segment = weighted[position:position + take]
            self._acc += np.sum(segment ** 2, axis=0)
            self._pos += take
            position += take
            if self._pos >= self._chunk:
                self._chunks.append(self._acc / float(self._chunk))
                self._acc[:] = 0.0
                self._pos = 0
                if len(self._chunks) > self.CHUNKS_PER_SHORT_TERM:
                    self._chunks.pop(0)
                if len(self._chunks) >= self.BLOCKS_PER_MOMENTARY:
                    block = self._mean_of(self._chunks[-self.BLOCKS_PER_MOMENTARY:])
                    self._blocks.append(block)

    def reset(self) -> None:
        """Clear the meter state."""
        self._filter.reset()
        self._acc = np.zeros(self.channels, dtype=np.float64)
        self._pos = 0
        self._chunks = []
        self._blocks = []

    # -- readings -------------------------------------------------------------
    def momentary(self) -> float:
        """Momentary loudness (400 ms) in LUFS."""
        if len(self._chunks) < self.BLOCKS_PER_MOMENTARY:
            return -np.inf
        return self._loudness_of(self._mean_of(self._chunks[-self.BLOCKS_PER_MOMENTARY:]))

    def short_term(self) -> float:
        """Short term loudness (3 s) in LUFS."""
        if not self._chunks:
            return -np.inf
        return self._loudness_of(self._mean_of(self._chunks))

    def integrated(self) -> float:
        """Gated integrated loudness in LUFS."""
        if not self._blocks:
            return -np.inf
        blocks = np.asarray(self._blocks, dtype=np.float64)
        values = np.array([self._loudness_of(block) for block in blocks])
        keep = values > _ABSOLUTE_GATE
        if not np.any(keep):
            return -np.inf
        gated = self._loudness_of(blocks[keep].mean(axis=0))
        threshold = gated - _RELATIVE_GATE
        keep = keep & (values > threshold)
        if not np.any(keep):
            return -np.inf
        return self._loudness_of(blocks[keep].mean(axis=0))


class _TruePeakMeter:
    """4x oversampled true peak meter (dBTP).

    The signal is upsampled with a windowed sinc interpolator and the
    absolute peak of the oversampled signal is reported, which catches the
    inter sample peaks a sample peak meter would miss.
    """

    def __init__(self, sample_rate: int, factor: int = 4, taps: int = 16):
        self.factor = int(factor)
        self.taps = int(taps)
        length = self.factor * self.taps
        centre = (length - 1) / 2.0
        index = np.arange(length, dtype=np.float64)
        window = np.blackman(length)
        # The interpolation filter of an upsampler by `factor` is a sinc
        # with a cut off at the original Nyquist.  Its sum has to be
        # `factor`, because the zero stuffing divides the spectrum by that
        # factor.
        h = np.sinc((index - centre) / self.factor) * window
        total = h.sum()
        if abs(total) > 1e-12:
            h = h * (self.factor / total)
        self.h = h
        self.hold = self.taps + 2
        self._pending = np.zeros((0, 1), dtype=np.float64)
        self.peak = 0.0

    def process(self, x: np.ndarray) -> None:
        """Feed a block of audio into the meter."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return
        if self._pending.shape[1] != a.shape[1]:
            # the caller changed the channel count: drop the stale carry-over
            self._pending = np.zeros((0, a.shape[1]), dtype=np.float64)
        buffer = np.concatenate([self._pending, a], axis=0)
        upsampled = np.zeros((buffer.shape[0] * self.factor, buffer.shape[1]), dtype=np.float64)
        upsampled[:: self.factor] = buffer
        oversampled = np.empty_like(upsampled)
        for channel in range(buffer.shape[1]):
            oversampled[:, channel] = np.convolve(upsampled[:, channel],
                                                  self.h)[: upsampled.shape[0]]
        commit = max(0, buffer.shape[0] - self.hold) * self.factor
        if commit > 0:
            self.peak = max(self.peak, float(np.max(np.abs(oversampled[:commit]))))
        self._pending = buffer[-self.hold:].copy()

    def reset(self) -> None:
        """Clear the meter state."""
        self._pending = np.zeros((0, 1), dtype=np.float64)
        self.peak = 0.0

    def dbtp(self) -> float:
        """The measured true peak in dBTP."""
        if self.peak <= 0.0:
            return -np.inf
        return 20.0 * np.log10(self.peak)


@register
class Monitor(Module):
    """Monitor — loudness and true peak metering.

    The module passes the audio through unchanged and measures it.  The
    readings are available through the :attr:`loudness_lufs` and
    :attr:`true_peak_dbtp` properties and through :meth:`meter_snapshot`.
    """

    code = "AUR_MONITOR"
    display_name = "Monitor"
    category = "System"
    edition = "standard"
    element = "Monitor"
    analysis = True
    supports_learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._loudness = _LoudnessMeter(sample_rate, max(channels, 1))
        self._true_peak = _TruePeakMeter(sample_rate)

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Monitor Loudness",
                type="bool",
                default=True,
                minimum=0,
                maximum=1,
                group="Monitor",
                help="Run the BS.1770-4 loudness analysis.",
            ),
        ]

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Clear the meters."""
        self._loudness.reset()
        self._true_peak.reset()

    # -- readings -------------------------------------------------------------
    @property
    def loudness_lufs(self) -> float:
        """The gated integrated loudness of the material seen so far, in LUFS."""
        return self._loudness.integrated()

    @property
    def momentary_lufs(self) -> float:
        """The momentary (400 ms) loudness, in LUFS."""
        return self._loudness.momentary()

    @property
    def short_term_lufs(self) -> float:
        """The short term (3 s) loudness, in LUFS."""
        return self._loudness.short_term()

    @property
    def true_peak_dbtp(self) -> float:
        """The true peak level of the material seen so far, in dBTP."""
        return self._true_peak.dbtp()

    def meter_snapshot(self) -> dict:
        """Return all meter readings in one dictionary."""
        return {
            "integrated_lufs": self.loudness_lufs,
            "short_term_lufs": self.short_term_lufs,
            "momentary_lufs": self.momentary_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "loudness_metered": bool(self.params.get("Monitor Loudness")),
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
        }

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Pass the audio through unchanged while metering it."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        if self.params.get("Monitor Loudness"):
            self._loudness.process(a)
        self._true_peak.process(a)
        return a
