"""
aurcore.modules.de_plosive — De-Plosive (``DePlosive``).

Tames the low frequency "P" burst that a plosive consonant (P, B, T, K, G)
produces when the lips or the tongue release a pressure build-up.

The signal is analysed in short frames and the energy below ``Frequency
Limit`` is measured.  A slow reference level tracks what that band normally
carries; a burst that stands out from the reference by more than the
sensitivity margin is treated as a plosive and a downward expander pulls the
band down.  The release of the expander is short, so the consonant body that
follows the burst keeps its level.

Working in the spectral domain keeps the band split and the gain perfectly
aligned in time: a time domain band split would delay the low band by the
group delay of the filter, which smears a transient instead of controlling it.

The analysis is **streaming**
-----------------------------
The frames of the signal sit on one grid, and the two pieces of state the
module measures — the reference level of the band and the gain reduction
envelope — are carried from frame to frame.  A block-local analysis would
measure a fresh reference in every block, so a burst that sits on a block
boundary would be judged against the wrong level and the answer would depend
on where the host chopped the signal.

The centred analysis needs the samples that follow a frame, so the module
delays its output by :attr:`DePlosive.latency_samples` samples and says so;
the host takes the delay off.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["DePlosive"]

#: Deepest gain reduction the expander applies, in decibels.
_MAX_REDUCTION_DB = 24.0

#: Release of the gain reduction, in seconds.
_RELEASE_TAU = 0.020

#: Attack of the gain reduction, in seconds.
_ATTACK_TAU = 0.001

#: Analysis window.
_NFFT = 512

#: Hop between frames.
_HOP = 128

#: How many samples the streaming engine is fed at a time.  The engine holds
#: the frames it has not synthesised yet, so a piece may not complete more
#: frames than it can hold.
_FEED_CHUNK = 8 * _HOP

#: How much the reference level follows the band, per frame.
_REFERENCE_RATE = 0.02


@register
class DePlosive(Module):
    """De-Plosive — low frequency transient control."""

    code = "AUR_DEPLOSIVE"
    display_name = "De-Plosive"
    category = "Repair"
    edition = "standard"
    element = "DePlosive"
    supports_learn = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = StreamingSTFT(_NFFT, _HOP, window="hann")
        self._reference = 0.0
        self._reduction = 0.0
        self._channels = max(int(channels), 1)
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=3.0,
                minimum=0.0,
                maximum=10.0,
                group="Sensitivity",
                help="How far above the reference level a burst has to sit to count.",
            ),
            ParamDesc(
                name="Reduction",
                type="float",
                default=2.5,
                minimum=0.0,
                maximum=10.0,
                group="Reduction",
                help="How hard the detected burst is pulled down.",

                aliases=("Strength",)),
            ParamDesc(
                name="Frequency Limit",
                type="float",
                default=300.0,
                minimum=20.0,
                maximum=500.0,
                unit="Hz",
                group="Band",
                curve="log",
                help="Upper edge of the band the plosive is looked for in.",
            ),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the analysis, the reference tracker and the envelope."""
        self._engine.reset()
        self._reference = 0.0
        self._reduction = 0.0
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis)."""
        return int(self._engine.latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The centred analysis works on frames that reach into the future, so
        the frames that follow the last one still hold samples of the input
        and the reduction they carry fades out.  Measured the way the host
        measures it, the output stays above -60 dBFS for 700 samples after
        the input stopped; 512 of them are the latency the module
        declares, so the tail is the 188 that are left.  The number carries
        a margin of 132 samples (2.75 ms), so that a small change of the
        analysis does not break the contract.
        """
        return 320

    # -- helpers -------------------------------------------------------------
    def _band_level(self, mag: np.ndarray, band_bin: int) -> np.ndarray:
        """Level of the band below ``band_bin``, in dBFS, per frame.

        Parameters
        ----------
        mag:
            The magnitude spectrogram of the mono mix, shape
            ``(bins, frames)``.
        band_bin:
            The first bin above the band.

        Returns
        -------
        numpy.ndarray
            The level of the band, one value per frame.
        """
        n_fft = 2 * (mag.shape[0] - 1)
        amp = mag * (4.0 / float(n_fft))
        power = amp * amp * 0.5
        band = np.sum(power[:band_bin, :], axis=0)
        return 10.0 * np.log10(np.maximum(band, 1e-18))

    def _detect(self, level_db: np.ndarray) -> np.ndarray:
        """Return the requested gain reduction, in decibels, per frame.

        The reference level is the state the module measures about its own
        surroundings, so it is carried from frame to frame: the burst of one
        frame is judged against the level the band has been carrying, not
        against the level of the block the frame happens to sit in.
        """
        p = self.params
        sensitivity = float(p.get("Sensitivity"))
        strength = float(p.get("Reduction"))
        if sensitivity <= 0.0 or strength <= 0.0:
            return np.zeros_like(level_db)
        margin_db = 6.0 - 1.0 * sensitivity
        scale = min(1.0, sensitivity / 3.0) * min(1.0, strength / 2.5)
        ref = self._reference
        out = np.empty_like(level_db)
        for i in range(level_db.shape[0]):
            value = float(level_db[i])
            if ref == 0.0:
                ref = value
            excess = value - ref - margin_db
            out[i] = min(_MAX_REDUCTION_DB, max(0.0, excess) * scale)
            # the reference follows the band slowly, so a burst barely moves it
            ref += _REFERENCE_RATE * (value - ref)
        self._reference = ref
        return out

    def _smooth(self, reduction: np.ndarray) -> np.ndarray:
        """Smooth the gain reduction: fast attack, short release.

        The envelope is the second piece of state the module carries: the
        attack of one frame continues into the next.
        """
        fs = float(self.sample_rate)
        a_att = math.exp(-_HOP / (max(_ATTACK_TAU, 1e-6) * fs))
        a_rel = math.exp(-_HOP / (max(_RELEASE_TAU, 1e-6) * fs))
        out = np.zeros_like(reduction)
        prev = self._reduction
        for i in range(len(reduction)):
            target = float(reduction[i])
            coef = a_att if target > prev else a_rel
            prev = coef * prev + (1.0 - coef) * target
            out[i] = prev
        self._reduction = prev
        return out

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
        fs = float(self.sample_rate)
        cutoff = float(self.params.get("Frequency Limit"))
        n_bins = mag.shape[1]
        bin_hz = fs / _NFFT
        band_bin = int(min(max(1, math.ceil(cutoff / bin_hz)), n_bins - 1))

        # the detector works on the mono mix, exactly like the block-local
        # analysis it replaces
        mono = np.abs(
            (mag * np.exp(1j * phase)).mean(axis=0)
        ) if mag.shape[0] > 1 else mag[0]
        level_db = self._band_level(mono, band_bin)
        reduction = self._smooth(self._detect(level_db))

        # the reduction is applied to the band, with a soft edge on the
        # boundary bin so the split does not ring
        gain_db = np.zeros((mono.shape[0], mono.shape[1]), dtype=np.float64)
        gain_db[: band_bin - 1, :] = -reduction[np.newaxis, :]
        if band_bin - 1 < n_bins:
            gain_db[band_bin - 1, :] = -reduction * 0.5
        gain = np.power(10.0, gain_db / 20.0)
        return mag * gain[np.newaxis, :, :], phase

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
        """Remove plosive bursts from a block of audio."""
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
