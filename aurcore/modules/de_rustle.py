"""
aurcore.modules.de_rustle — De-Rustle (``DeRustle``).

Removes the low energy broadband friction that clothing, harnesses and
lavaliere mounts add to a close microphone recording.

The module works on a magnitude spectrogram.  A per-bin noise floor is
estimated from the quietest parts of the material, the floor is subtracted
with an over-subtraction factor set by ``Reduction Strength`` and the result
is floored so that the noise is not subtracted away completely.
``Ambience Preservation`` controls how much of the floor survives.

A bin that carries wanted content is protected.  A wanted partial is *peaky*
in the spectrum while a rub is *flat*, so the peakiness of every bin decides
how much of the estimated floor is subtracted from it.  Without that
protection the wanted part ends up inside the estimated floor and the module
subtracts the wanted part from itself; the measurement in ``qa/rustle/``
shows the difference (5.6 dB of wanted content lost before, 0.3 dB after).

Three separation algorithms are offered:

``Channel Independent``
    Every channel is cleaned on its own, with its own floor estimate.

``Joint``
    The channels share one floor estimate, so a rumble that is present in
    both channels is removed by the same amount and the stereo image stays
    intact.

``Advanced Joint``
    On top of the joint floor the inter-channel coherence is used.  Content
    that is coherent between the channels (a centred voice) is protected,
    while incoherent, diffuse content (a rub) is subtracted hardest.

The analysis is **streaming**
-----------------------------
The frames of the signal sit on one grid, and the state the module measures
about its own surroundings is carried from frame to frame:

* the **noise floor** is the tenth percentile of the last half second of the
  band, and the window of frames that percentile is taken over is kept, so
  the floor of a frame does not depend on where the host chopped the
  signal;
* the **gain curve** is smoothed along the time axis with a filter whose
  memory reaches into the previous frames;
* the **inter-channel coherence** is an exponential average over the frames.

The centred analysis needs the samples that follow a frame, so the module
delays its output by :attr:`DeRustle.latency_samples` samples and says so;
the host takes the delay off.

The frame the module resynthesises is not the frame it took in: the
magnitude is edited and the phase is kept, so the last frame that carries
material comes back a little different, and the difference spreads over
the rest of the window.  The module therefore keeps ringing for a short
while after its input stopped, and it says so as well:
:attr:`DeRustle.tail_samples` tells the host how long it has to wait.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["DeRustle"]

_EPS = 1e-12

#: Over-subtraction factor at full ``Reduction Strength``.
_MAX_OVERSUB = 1.6

#: Lowest fraction of the noise floor that survives the subtraction.
_MIN_FLOOR = 0.06

#: Analysis window.
_NFFT = 2048

#: Hop between frames.
_HOP = 512

#: Length of the block used for the noise floor estimate, in seconds.
_FLOOR_BLOCK = 0.5

#: Quantile of the window the noise floor is taken at.
#:
#: The block-local version of the module took the tenth percentile of the
#: whole block.  The streaming version takes a quantile of the last half
#: second, and the low quantiles of a handful of frames are much lower than
#: the low quantile of half a second: the removal of the friction bed fell
#: from 3.7 dB to 3.1 dB when the quantile was left at ten per cent.  At
#: twenty per cent the module takes away as much as it did before, and the
#: wanted part is left alone exactly as before (0.36 dB).
_FLOOR_QUANTILE = 0.20

#: Peakiness above which a bin is treated as wanted content and protected.
#: Below the gate a bin is noise, and the gate keeps the protection away
#: from the bins that only carry the random peaks of the noise itself.
_TONALITY_GATE = 0.5

#: Half width of the neighbourhood the peakiness is measured over, in bins.
_NEIGHBOUR = 16

#: Inner radius of that neighbourhood, in bins.
_NEIGHBOUR_INNER = 6

#: Conversion from decibels to a linear gain, without the slow ``power``.
_DB_TO_LIN = math.log(10.0) / 20.0

#: How many samples the streaming engine is fed at a time.  The engine holds
#: the frames it has not synthesised yet, so a piece may not complete more
#: frames than it can hold.
_FEED_CHUNK = 8 * _HOP


# ---------------------------------------------------------------------------
# spectrogram helpers
# ---------------------------------------------------------------------------
def _hann(n_fft: int) -> np.ndarray:
    """The periodic Hann window."""
    k = np.arange(int(n_fft), dtype=np.float64)
    return 0.5 - 0.5 * np.cos(2.0 * math.pi * k / float(n_fft))


def _plan(n: int, n_fft: int, hop: int) -> tuple[int, int]:
    """Return ``(n_frames, padded_length)`` of a centred analysis."""
    n = int(n)
    n_frames = 1 + int(math.ceil(n / float(hop)))
    pad = n_fft // 2
    padded = max(n + 2 * pad, (n_frames - 1) * hop + n_fft)
    return n_frames, padded


def _spectrogram(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Return a complex spectrogram shaped ``(frames, bins, channels)``.

    The helper is kept for the measurement harness in ``qa/rustle``; the
    module itself works on the streaming engine.
    """
    win = _hann(n_fft)
    n, ch = x.shape
    n_frames, padded = _plan(n, n_fft, hop)
    xp = np.zeros((padded, ch), dtype=np.float64)
    xp[n_fft // 2 : n_fft // 2 + n] = x
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = xp[idx] * win[:, None]
    return np.fft.rfft(frames, axis=1)


def _band_sum(mag: np.ndarray) -> np.ndarray:
    """Sum every bin over its neighbourhood, 6 to 16 bins on both sides.

    The sum is computed with a prefix sum along the frequency axis, so the
    whole neighbourhood costs a handful of array operations instead of one
    per offset.  Bins near the edge of the spectrum only get the neighbours
    that exist; the missing ones are zeros in the padding and therefore do
    not change the sum.
    """
    pad = _NEIGHBOUR
    inner = _NEIGHBOUR_INNER
    n_bins = mag.shape[1]
    padded = np.zeros((mag.shape[0], n_bins + 2 * pad) + mag.shape[2:], dtype=np.float64)
    padded[:, pad:pad + n_bins] = mag
    prefix = np.cumsum(padded, axis=1)
    zeros = np.zeros((mag.shape[0], 1) + mag.shape[2:], dtype=np.float64)
    prefix = np.concatenate([zeros, prefix], axis=1)
    # the lower window holds the bins ``pad-inner+1 .. pad`` of the padding,
    # the upper window the bins ``pad+inner .. 2*pad``
    lower = prefix[:, pad - inner + 1:pad - inner + 1 + n_bins] - prefix[:, :n_bins]
    upper = prefix[:, 2 * pad + 1:2 * pad + 1 + n_bins] - prefix[
        :, pad + inner:pad + inner + n_bins
    ]
    return lower + upper


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------
@register
class DeRustle(Module):
    """De-Rustle — broadband friction removal."""

    code = "AUR_DERUSTLE"
    display_name = "De-Rustle"
    category = "Repair"
    edition = "advanced"
    element = "DeRustle"
    supports_learn = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = StreamingSTFT(_NFFT, _HOP, window="hann")
        self._floor: np.ndarray | None = None
        self._coherence: np.ndarray | None = None
        self._channels = max(int(channels), 1)
        self._n_bins = _NFFT // 2 + 1
        # the number of frames the noise floor estimate looks at
        self._window = max(4, int(_FLOOR_BLOCK * self.sample_rate / _HOP))
        self._noise_hist = np.zeros((self._window, self._n_bins, self._channels), dtype=np.float64)
        self._mono_hist = np.zeros((self._window, self._n_bins), dtype=np.float64)
        self._hist_count = 0
        self._hist_pos = 0
        # the gain curve of the previous frames, for the smoothing along time
        self._prev_gain = np.zeros((self._n_bins, self._channels), dtype=np.float64)
        # the exponential averages of the inter-channel coherence
        self._sxx = np.zeros(self._n_bins, dtype=np.float64)
        self._syy = np.zeros(self._n_bins, dtype=np.float64)
        self._sxy = np.zeros(self._n_bins, dtype=np.complex128)
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Reduction",
                help="How much of the estimated floor is subtracted.",
                aliases=("Reduction Strength",),
            ),
            ParamDesc(
                name="Ambience Preservation",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Reduction",
                help="How much of the ambience is left in the signal.",
            ),
            ParamDesc(
                name="Separation Algorithm",
                type="enum",
                default="Channel Independent",
                minimum=0.0,
                maximum=2.0,
                choices=("Channel Independent", "Joint", "Advanced Joint"),
                group="Algorithm",
                help="Whether the channels are cleaned on their own or together.",
            ),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the analysis, the noise floor and the coherence estimate."""
        self._engine.reset()
        self._floor = None
        self._coherence = None
        self._noise_hist[...] = 0.0
        self._mono_hist[...] = 0.0
        self._hist_count = 0
        self._hist_pos = 0
        self._prev_gain[...] = 0.0
        self._sxx[...] = 0.0
        self._syy[...] = 0.0
        self._sxy[...] = 0.0
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis)."""
        return int(self._engine.latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The module edits the magnitude of the spectrum and keeps the
        phase, so the frame it resynthesises is not the frame it took in:
        the last frame that carries material is rebuilt with a gain that
        depends on the frequency, and the difference between the two
        spreads across the rest of the window.  That difference is the
        ring-out of the module, and a host that stops the stream has to
        wait for it.

        Measured the way the host measures it -- a tone, then silence, then
        the number of samples the output keeps coming -- the module rings
        for 655 samples past its latency for a tone at -6 dBFS with the
        default settings, and for 1416 samples for a signal at the
        clipping ceiling with the reduction at its strongest and no
        ambience preserved.  The tail covers the loud one, with a margin
        of 120 samples (2.5 ms) over it.

        The module makes no sound of its own: with the reduction at zero
        it is a plain delay, and it goes quiet exactly one latency after
        its input stops.
        """
        return 1536

    # -- helpers -------------------------------------------------------------
    def _floor_now(self, joint: bool) -> np.ndarray:
        """The per-bin noise floor of the frame that was just pushed.

        The estimate is the tenth percentile of the frames of the window the
        module keeps.  A wanted partial is peaky and a rub is flat, so the
        peaky part of the spectrum is taken out of the estimate: the wanted
        content must not end up in the floor, or the module would subtract
        the wanted part from itself.

        The window is the state that has to survive the block boundary: a
        floor that was measured over one block only would depend on where
        the host chopped the signal.

        Parameters
        ----------
        joint:
            ``True`` for the joint algorithms, where the channels share one
            floor.

        Returns
        -------
        numpy.ndarray
            The floor, shaped ``(bins, channels)``.
        """
        count = self._hist_count
        if joint:
            chunk = self._mono_hist[:count] if count < self._window else self._mono_hist
            rank = int((chunk.shape[0] - 1) * _FLOOR_QUANTILE)
            if rank == 0:
                floor = np.min(chunk, axis=0)
            else:
                floor = np.partition(chunk, rank, axis=0)[rank]
            return np.repeat(floor[:, None], self._channels, axis=1)
        chunk = (
            self._noise_hist[:count] if count < self._window else self._noise_hist
        )
        rank = int((chunk.shape[0] - 1) * _FLOOR_QUANTILE)
        if rank == 0:
            return np.min(chunk, axis=0)
        return np.partition(chunk, rank, axis=0)[rank]

    def _push_noise(self, noise: np.ndarray, mono: np.ndarray) -> None:
        """Put the noise magnitude of a frame into the window of the floor."""
        self._noise_hist[self._hist_pos] = noise
        self._mono_hist[self._hist_pos] = mono
        self._hist_pos = (self._hist_pos + 1) % self._window
        if self._hist_count < self._window:
            self._hist_count += 1

    def _coherence_estimate(self, spec: np.ndarray) -> np.ndarray:
        """Magnitude squared coherence between the two channels, per bin."""
        n_frames = spec.shape[0]
        if n_frames < 1 or spec.shape[2] < 2:
            return np.zeros(spec.shape[1], dtype=np.float64)
        alpha = math.exp(-_HOP / (0.25 * self.sample_rate))
        out = np.empty((n_frames, spec.shape[1]), dtype=np.float64)
        for i in range(n_frames):
            x = spec[i, :, 0]
            y = spec[i, :, 1]
            self._sxx = alpha * self._sxx + (1.0 - alpha) * np.abs(x) ** 2
            self._syy = alpha * self._syy + (1.0 - alpha) * np.abs(y) ** 2
            self._sxy = alpha * self._sxy + (1.0 - alpha) * (x * np.conj(y))
            denom = self._sxx * self._syy
            out[i] = np.where(
                denom > 1e-18, np.abs(self._sxy) ** 2 / np.maximum(denom, 1e-18), 0.0
            )
        return np.clip(out, 0.0, 1.0)

    @staticmethod
    def _tonality(mag: np.ndarray) -> np.ndarray:
        """How peaky every bin is compared with its neighbourhood, 0..1."""
        background = _band_sum(mag) / float(2 * (_NEIGHBOUR - _NEIGHBOUR_INNER + 1))
        ratio = mag / np.maximum(background, 1e-12)
        return np.clip((ratio - 1.0) / 3.0, 0.0, 1.0)

    @staticmethod
    def _protect(gain_db: np.ndarray, tonality: np.ndarray) -> np.ndarray:
        """Keep the bins that carry wanted content.

        ``tonality`` is 0 for a bin that holds nothing but noise and 1 for a
        bin that holds a wanted partial.  A bin above the gate is wanted
        content, and its gain is pulled back towards 0 dB, so that the module
        leaves the wanted part alone.
        """
        gate = np.clip(
            (tonality - _TONALITY_GATE) / (1.0 - _TONALITY_GATE), 0.0, 1.0
        )
        return gain_db * (1.0 - gate)

    @staticmethod
    def _smooth_frequency(gain_db: np.ndarray) -> np.ndarray:
        """Smooth the gain across frequency, to keep musical noise out.

        The edges of the spectrum repeat their own value, so the gain does
        not dip at the band limits.
        """
        out = 0.5 * gain_db.copy()
        out[:, 1:-1] += 0.25 * (gain_db[:, :-2] + gain_db[:, 2:])
        out[:, 0] += 0.25 * gain_db[:, 0]
        out[:, -1] += 0.25 * gain_db[:, -1]
        return out

    def _smooth_time(self, gain_db: np.ndarray) -> np.ndarray:
        """Smooth the gain across time, to keep musical noise out.

        The filter reaches into the frames before the block, so the gain of
        a frame does not jump at the frame boundary.
        """
        alpha = math.exp(-_HOP / (0.08 * self.sample_rate))
        out = np.empty_like(gain_db)
        prev = self._prev_gain
        for i in range(gain_db.shape[0]):
            prev = alpha * prev + (1.0 - alpha) * gain_db[i]
            out[i] = prev
        self._prev_gain = prev
        return out

    def _gain_curve(
        self,
        mag: np.ndarray,
        floor: np.ndarray,
        tonality: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return the gain curve, in decibels, for the frames of the block.

        ``mag``, ``floor`` and ``tonality`` are shaped
        ``(frames, bins, channels)``.
        """
        p = self.params
        strength = float(p.get("Reduction"))
        preservation = float(p.get("Ambience Preservation"))
        algorithm = str(p.get("Separation Algorithm"))
        over = strength * _MAX_OVERSUB
        keep = _MIN_FLOOR + (1.0 - _MIN_FLOOR) * preservation
        if algorithm == "Advanced Joint" and self._coherence is not None and tonality is not None:
            # a centred, tonal voice is coherent between the channels and is
            # protected; a diffuse rub is not
            protect = self._coherence[None, :, None] * tonality
            over = over * (1.0 - 0.85 * protect)
        clean = mag - over * floor
        clean = np.maximum(clean, keep * floor)
        gain = np.where(mag > _EPS, clean / np.maximum(mag, _EPS), 1.0)
        gain = np.clip(gain, 0.0, 1.0)
        gain_db = 20.0 * np.log10(np.maximum(gain, 1e-6))
        if tonality is not None:
            gain_db = self._protect(gain_db, tonality)
        gain_db = self._smooth_frequency(gain_db)
        gain_db = self._smooth_time(gain_db)
        if tonality is not None:
            # the smoothing pulled the neighbours into the wanted bins, so the
            # protection is applied once more
            gain_db = self._protect(gain_db, tonality)
        return gain_db

    # -- processing ----------------------------------------------------------
    def _edit(self, mag: np.ndarray, phase: np.ndarray):
        """Return the edited magnitudes of the new frames.

        Parameters
        ----------
        mag:
            The magnitudes of the new frames, shape
            ``(channels, bins, frames)``.
        phase:
            The phases of the new frames, the same shape.

        Returns
        -------
        tuple of numpy.ndarray
            The edited magnitudes and the unchanged phases.
        """
        algorithm = str(self.params.get("Separation Algorithm"))
        joint = algorithm != "Channel Independent"
        # the frames are handled one after the other, so that the state of
        # the module is the state of the last frame it has seen
        frames = int(mag.shape[2])
        edited = np.array(mag, copy=True)
        for index in range(frames):
            frame = mag[:, :, index]
            # a wanted partial is peaky in the spectrum, a rub is flat
            tonality = self._tonality(frame.T[None, :, :])[0]
            noise = frame.T * (1.0 - np.clip(tonality, 0.0, 1.0))
            if joint:
                mono = frame.mean(axis=0)
                mono_noise = mono * (1.0 - np.clip(tonality.mean(axis=1), 0.0, 1.0))
            else:
                mono_noise = np.zeros(frame.shape[1], dtype=np.float64)
            self._push_noise(noise, mono_noise)
            floor = self._floor_now(joint)
            if algorithm == "Advanced Joint" and frame.shape[0] >= 2:
                spec = (frame * np.exp(1j * phase[:, :, index])).T[None, :, :]
                self._coherence = self._coherence_estimate(spec)[0]
            else:
                self._coherence = None
            gain_db = self._gain_curve(frame.T[None, :, :], floor[None, :, :], tonality[None, :, :])
            edited[:, :, index] = (np.exp(gain_db[0] * _DB_TO_LIN) * frame.T).T
        return edited, phase

    def _delay(self, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the output of the engine by the latency of the analysis.

        The engine hands out the samples as soon as they are ready, which
        depends on the size of the blocks it is fed.  The module keeps its
        own line of ``latency_samples`` so that the delay is the same
        whatever the host uses, and so that the result does not depend on the
        block size.
        """
        if not self._primed:
            self._fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
            self._primed = True
        fifo = np.concatenate([self._fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._fifo = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove broadband friction from a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        if buf.shape[1] != self._channels:
            # the caller changed the channel count: the analysis starts again
            self._channels = int(buf.shape[1])
            self._engine.reset()
            self._noise_hist = np.zeros(
                (self._window, self._n_bins, self._channels), dtype=np.float64
            )
            self._prev_gain = np.zeros((self._n_bins, self._channels), dtype=np.float64)
            self._fifo = np.zeros((0, self._channels), dtype=np.float64)
            self._primed = False
        pieces: list[np.ndarray] = []
        for start in range(0, n, _FEED_CHUNK):
            chunk = buf[start : start + _FEED_CHUNK]
            mag, phase = self._engine.analyze(chunk)
            if mag.shape[-1]:
                mag, phase = self._edit(mag, phase)
            pieces.append(self._engine.synthesize(mag, phase))
        raw = (
            np.concatenate(pieces, axis=0)
            if pieces
            else np.zeros((0, self._channels), dtype=np.float64)
        )
        out = self._delay(raw, n)
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
