"""
aurcore.modules.de_wind — De-Wind (``DeWind``).

Removes the low frequency rumble that wind produces on an outdoor recording.

Everything below ``Crossover Frequency`` is treated as the wind band.  Its
level is compared with a slow reference and its spectral flatness is measured:
wind is loud, slow moving and broadband, while a sung or spoken low note is
loud but tonal.  Only the diffuse part is attenuated, and the attenuation is
smoothed in time so no zipper artefacts appear.

``Fundamental Recovery`` puts back the wanted fundamental.  The strongest
narrow peak of the band is tracked and the attenuation that was applied to it
is partly undone, so the voice keeps its fundamental even while the rumble
around it is removed.

``Removed Mix`` puts the removed rumble back into the output at the amount
the user sets: 0 % leaves the cleaned signal as it is, 100 % plays the
removed rumble alone.  The module measures what it removed and keeps the
reading, so an interface can show a meter.

Block coherence
---------------
The module measures its own surroundings: the reference level of the rumble,
the tracked fundamental and the smoothed gain all live from one block to the
next.  The analysis itself runs on a
:class:`aurcore.dsp.stft.StreamingSTFT` engine, whose frames sit on the same
grid whatever the size of the block the host hands over, so a host that
renders in small blocks reaches the same decision as one that renders the
whole file in a single block.  The engine needs the samples that follow the
frame it works on, so the module declares the latency it gains (see
:attr:`DeWind.latency_samples`); the host compensates it.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["DeWind"]

#: Deepest attenuation the module applies to the wind band, in decibels.
_MAX_REDUCTION_DB = 24.0

#: Analysis window.
_NFFT = 1024

#: Hop between frames.
_HOP = 256

#: Lowest frequency the fundamental is searched in.
_FUNDAMENTAL_LO = 60.0

#: Highest frequency the fundamental is searched in.
_FUNDAMENTAL_HI = 400.0

#: How many samples are handed to the analysis engine at a time.  The engine
#: holds a limited number of frames in flight, so a long block is fed in
#: pieces.  The frame grid does not depend on the size of the piece.
_FEED_CHUNK = 1024


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------
@register
class DeWind(Module):
    """De-Wind — low frequency rumble removal."""

    code = "AUR_DEWIND"
    display_name = "De-Wind"
    category = "Repair"
    edition = "advanced"
    element = "DeWind"
    supports_learn = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # the streaming analysis: one frame grid for every block size
        self._engine = StreamingSTFT(_NFFT, _HOP, window="hann")
        self._channels = max(int(channels), 1)
        self._reference = 0.0
        self._reduction = 0.0
        self._fundamental = 0.0
        # the line that keeps the delay the same for every block size
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        # the difference tap: what the module took away
        self._tap = RemovedTap(self._channels)
        #: The level of the removed rumble of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed rumble of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB

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
                help="How much of the detected rumble is taken away.",
            ),
            ParamDesc(
                name="Crossover Frequency",
                type="float",
                default=500.0,
                minimum=50.0,
                maximum=2000.0,
                unit="Hz",
                group="Band",
                curve="log",
                help="Frequency that separates the rumble band from the signal.",
            ),
            ParamDesc(
                name="Fundamental Recovery",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Recovery",
                help="How much of the wanted fundamental is put back.",
            ),
            ParamDesc(
                name="Artifact Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help="How slowly the gain is allowed to move.",
            ),
            ParamDesc(**removed_mix_fields("rumble")),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the reference tracker and the gain state."""
        self._engine.reset()
        self._reference = 0.0
        self._reduction = 0.0
        self._fundamental = 0.0
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis).

        The centred analysis needs the samples that follow the frame it
        works on, so the output of the module is late by that much.  The
        number does not depend on the size of the block the host hands over.
        """
        return int(self._engine.latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The centred analysis works on frames that reach into the future, so
        the frames that follow the last one still hold samples of the input
        and the gain they carry fades out.  Measured the way the host
        measures it, the output stays above -60 dBFS for 1225 samples after
        the input stopped; 1024 of them are the latency the module
        declares, so the tail is the 201 that are left.  The number carries
        a margin of 183 samples (3.8 ms), so that a small change of the
        analysis does not break the contract.
        """
        return 384

    # -- helpers -------------------------------------------------------------
    def _smoothing_tau(self) -> float:
        """Time constant of the gain smoothing, in seconds."""
        s = float(self.params.get("Artifact Smoothing"))
        return 0.05 + 0.95 * s

    def _wind_score(
        self, mag: np.ndarray, band_bin: int, peaks: np.ndarray
    ) -> np.ndarray:
        """Return the wind amount, 0..1, for every frame.

        The reference level follows the quietest the band gets, so a rumble
        that sits above the floor stays visible even when it never stops.
        The wanted fundamental is excluded from the measurement: it would
        otherwise hide the rumble underneath it.

        The reference is the state the module measures, and it is carried
        from frame to frame: a tracker that restarted at every block
        boundary would read every block as if it were the first one, and the
        rumble would come and go with the block size.
        """
        fs = float(self.sample_rate)
        n_fft = 2 * (mag.shape[1] - 1)
        amp = mag * (4.0 / float(n_fft))
        power = amp * amp * 0.5
        band_amp = np.maximum(amp[:, :band_bin], 1e-12)
        # mask out the fundamental and its immediate neighbours
        mask = np.ones_like(band_amp, dtype=bool)
        for i, peak in enumerate(peaks):
            if 0 <= peak < band_bin:
                mask[i, max(0, peak - 1):peak + 2] = False
        masked = np.where(mask, band_amp, 0.0)
        level = 10.0 * np.log10(
            np.maximum(np.sum(np.where(mask, power[:, :band_bin], 0.0), axis=1), 1e-18)
        )
        peak_val = np.max(masked, axis=1)
        median = np.median(np.where(mask, band_amp, np.nan), axis=1)
        tonality = peak_val / np.maximum(np.nan_to_num(median, nan=0.0), 1e-12)

        rise = 1.0 - math.exp(-_HOP / (4.0 * fs))
        ref = self._reference
        scores = np.empty_like(level)
        for i, value in enumerate(level):
            if ref == 0.0:
                ref = float(value)
            excess = float(value) - ref
            amount = np.clip((excess - 3.0) / 10.0, 0.0, 1.0)
            # a tonal band is treated as wind with less conviction; the
            # fundamental recovery puts the wanted note back
            amount *= 0.6 + 0.4 * float(np.clip((10.0 - tonality[i]) / 6.0, 0.0, 1.0))
            scores[i] = amount
            if value < ref:
                ref = float(value)
            else:
                ref += rise * (float(value) - ref)
        self._reference = ref
        return scores

    # -- processing ----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove low frequency rumble from a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        if buf.shape[1] != self._channels:
            # the engine cannot change its channel count inside a stream
            self._channels = int(buf.shape[1])
            self._engine.reset()
            self._fifo = np.zeros((0, self._channels), dtype=np.float64)
            self._tap = RemovedTap(self._channels)
        pieces: list[np.ndarray] = []
        for start in range(0, buf.shape[0], _FEED_CHUNK):
            chunk = buf[start : start + _FEED_CHUNK]
            mag, phase = self._engine.analyze(chunk)
            if mag.shape[-1]:
                mag, phase = self._modify(mag, phase)
            pieces.append(self._engine.synthesize(mag, phase))
        raw = (
            np.concatenate(pieces, axis=0)
            if pieces
            else np.zeros((0, self._channels), dtype=np.float64)
        )
        wet = np.nan_to_num(
            self._delay(raw, int(buf.shape[0])), nan=0.0, posinf=0.0, neginf=0.0
        )
        out = self._tap.apply(
            buf, wet, float(self.params.get(REMOVED_MIX_NAME)), self.latency_samples
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    def _delay(self, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the output by the latency of the analysis and take ``count``.

        The engine hands out the samples as soon as the frames that cover
        them are ready, which depends on the size of the blocks it is fed.
        The module keeps its own line of ``latency_samples`` samples, so the
        delay is the same whatever the host uses and the result does not
        depend on the block size.
        """
        fifo = self._fifo
        if fifo.shape[0] == 0:
            fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
        fifo = np.concatenate([fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._fifo = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def _modify(self, mag: np.ndarray, phase: np.ndarray):
        """Apply the wind gain to the frames the engine has just analysed.

        ``mag`` and ``phase`` are shaped ``(channels, bins, frames)``.  The
        gain is measured on the spectrum of the channels averaged together,
        the way the module has always measured it, and it is applied to
        every channel.
        """
        fs = float(self.sample_rate)
        reduction_amount = float(self.params.get("Reduction"))
        recovery = float(self.params.get("Fundamental Recovery"))
        crossover = float(self.params.get("Crossover Frequency"))

        spec = mag * np.exp(1j * phase)
        frame_mag = np.abs(spec.mean(axis=0)).T  # (frames, bins)
        n_frames, n_bins = frame_mag.shape
        if n_frames == 0:
            return mag, phase
        bin_hz = fs / _NFFT
        band_bin = int(min(max(2, round(crossover / bin_hz)), n_bins - 1))

        # the fundamental that has to survive the rumble
        lo = int(max(1, _FUNDAMENTAL_LO / bin_hz))
        hi = int(min(n_bins - 2, _FUNDAMENTAL_HI / bin_hz))
        peaks = np.zeros(n_frames, dtype=np.intp)
        fundamental = self._fundamental
        centres = np.zeros(n_frames, dtype=np.intp)
        if hi > lo:
            for i in range(n_frames):
                row = frame_mag[i]
                peak = lo + int(np.argmax(row[lo:hi + 1]))
                if fundamental <= 0.0:
                    fundamental = peak * bin_hz
                else:
                    fundamental += 0.3 * (peak * bin_hz - fundamental)
                centre = int(round(fundamental / bin_hz))
                centres[i] = centre
                peaks[i] = peak
        self._fundamental = fundamental

        score = self._wind_score(frame_mag, band_bin, peaks)
        target_db = -reduction_amount * _MAX_REDUCTION_DB * score
        tau = self._smoothing_tau()
        alpha = math.exp(-_HOP / (tau * fs))
        reduction = np.empty_like(target_db)
        prev = self._reduction
        for i in range(n_frames):
            prev = alpha * prev + (1.0 - alpha) * float(target_db[i])
            reduction[i] = prev
        self._reduction = prev

        recover_db = np.zeros((n_frames, n_bins), dtype=np.float64)
        if hi > lo and recovery > 0.0:
            for i in range(n_frames):
                centre = int(centres[i])
                if 0 < centre < n_bins - 1:
                    for d in (-1, 0, 1):
                        k = centre + d
                        if 0 <= k < n_bins:
                            weight = 0.5 * (1.0 + math.cos(math.pi * d))
                            recover_db[i, k] = -reduction[i] * recovery * weight

        gain_db = np.zeros((n_frames, n_bins), dtype=np.float64)
        for i in range(n_frames):
            row = gain_db[i]
            row[:band_bin] = reduction[i]
            if band_bin < n_bins:
                row[band_bin] = reduction[i] * 0.5
            row += recover_db[i]
            np.minimum(row, 0.0, out=row)

        gain = np.power(10.0, gain_db.T[None, :, :] / 20.0)  # (1, bins, frames)
        return mag * gain, phase
