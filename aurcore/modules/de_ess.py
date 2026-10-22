"""
aurcore.modules.de_ess — De-Ess (``DeEsser``).

Tames sibilance: the harsh, high-frequency energy of /s/, /z/, /ʃ/ and /t͡s/.

The module splits the signal at ``Cutoff Frequency`` and measures the energy
of the band above it.  A frame is treated as sibilant when that band stands
out — either against an absolute level (``Absolute Mode``) or against the
energy of the whole signal (the default, relative detector).  The excess over
``Threshold`` becomes a gain reduction that is smoothed with the attack and
release of ``Speed``.

Two modes are offered:

``Classic``
    The whole sibilant band is attenuated by the same amount.  This is the
    behaviour of a traditional de-esser.

``Spectral``
    The reduction is shaped per bin.  ``Spectral Flattening`` pulls the
    sibilant band towards its own average, which takes the edge off the
    harsh peak, and ``Spectral Tilt`` tilts the band so the reduction can
    be weighted towards either end of it.

``Output Ess Only`` renders the sibilance that was removed.

The analysis is **streaming**
-----------------------------
The frames of the signal sit on one grid, and the gain reduction envelope is
carried from frame to frame: the attack of one frame continues into the
next.  A block-local analysis would start the envelope again in every block,
so a /s/ that sits on a block boundary would be reduced differently from the
same /s/ in the middle of a block.

The centred analysis needs the samples that follow a frame, so the module
delays its output by :attr:`DeEsser.latency_samples` samples and says so;
the host takes the delay off.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["DeEsser"]

#: Deepest gain reduction the de-esser applies, in decibels.
_MAX_REDUCTION_DB = 24.0

#: How much of the detector excess turns into gain reduction.
_REDUCTION_SLOPE = 0.8

#: Tilt authority, in decibels, at the top of the sibilant band.
_TILT_RANGE_DB = 6.0

#: Width of the soft edge below the cutoff, in octaves.
_SKIRT_OCTAVES = 1.6

#: Analysis window of the detector.
_NFFT = 1024

#: Hop of the analysis.
_HOP = 256

#: How many samples the streaming engine is fed at a time.  The engine holds
#: the frames it has not synthesised yet, so a piece may not complete more
#: frames than it can hold.
_FEED_CHUNK = 8 * _HOP


@register
class DeEsser(Module):
    """De-Ess — sibilance control."""

    code = "AUR_DEESS"
    display_name = "De-Ess"
    category = "Repair"
    edition = "standard"
    element = "DeEsser"
    analysis = True
    supports_learn = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # Two outputs: the de-essed signal and the sibilance that was taken
        # out of it.  ``Output Ess Only`` chooses between them.
        self._engine = StreamingSTFT(_NFFT, _HOP, window="hann", outputs=2)
        self._prev_reduction = 0.0
        self._channels = max(int(channels), 1)
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_ess = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Mode",
                type="enum",
                default="Classic",
                minimum=0.0,
                maximum=1.0,
                choices=("Classic", "Spectral"),
                group="Algorithm",
                help="Broadband band reduction, or per-bin spectral shaping.",
            ),
            ParamDesc(
                name="Threshold",
                type="float",
                default=-30.0,
                minimum=-60.0,
                maximum=0.0,
                unit="dB",
                group="Threshold",
                help="Level above which the band counts as sibilant.",
            ),
            ParamDesc(
                name="Absolute Mode",
                type="bool",
                default=False,
                group="Threshold",
                help="Measure the band against an absolute level instead of the whole signal.",
            ),
            ParamDesc(
                name="Cutoff Frequency",
                type="float",
                default=6000.0,
                minimum=1000.0,
                maximum=16000.0,
                unit="Hz",
                group="Threshold",
                curve="log",
                help="Lowest frequency of the sibilant band.",
            ),
            ParamDesc(
                name="Spectral Flattening",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Tone",
                help="Pull the sibilant band towards its own average.",
            ),
            ParamDesc(
                name="Spectral Tilt",
                type="float",
                default=0.0,
                minimum=-1.0,
                maximum=1.0,
                group="Tone",
                help="Weight the reduction towards the low or the high end of the band.",
            ),
            ParamDesc(
                name="Speed",
                type="enum",
                default="Fast",
                minimum=0.0,
                maximum=1.0,
                choices=("Fast", "Slow"),
                group="Algorithm",
                help="Attack and release of the gain reduction.",
            ),
            ParamDesc(
                name="Output Ess Only",
                type="bool",
                default=False,
                group="Output",
                help="Render the sibilance that was removed.",
            ),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the gain-reduction envelope and the analysis."""
        self._engine.reset()
        self._prev_reduction = 0.0
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_ess = np.zeros((0, self._channels), dtype=np.float64)
        self._primed = False

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis)."""
        return int(self._engine.latency_samples)

    # -- helpers -------------------------------------------------------------
    def _band_levels(self, mag: np.ndarray, cutoff_bin: int):
        """Return the level of the sibilant band and of the whole frame.

        ``mag`` is the magnitude spectrogram of the mono mix, shaped
        ``(bins, frames)``.  The levels are returned in dBFS: a full scale
        sine sits at -3 dBFS.
        """
        n_fft = 2 * (mag.shape[0] - 1)
        scale = 4.0 / float(n_fft)  # amplitude of a bin holding a full scale sine
        amp = mag * scale
        power = amp * amp * 0.5
        total = np.sum(power, axis=0)
        band = np.sum(power[cutoff_bin:, :], axis=0)
        full_db = 10.0 * np.log10(np.maximum(total, 1e-18))
        band_db = 10.0 * np.log10(np.maximum(band, 1e-18))
        return full_db, band_db

    def _detector(self, mag: np.ndarray, cutoff_bin: int) -> np.ndarray:
        """Return the requested gain reduction, in decibels, per frame."""
        p = self.params
        threshold = float(p.get("Threshold"))
        full_db, band_db = self._band_levels(mag, cutoff_bin)
        if p.get("Absolute Mode"):
            det_db = band_db
        else:
            det_db = band_db - full_db
        excess = det_db - threshold
        reduction = np.clip(excess, 0.0, None) * _REDUCTION_SLOPE
        return np.minimum(reduction, _MAX_REDUCTION_DB)

    def _smooth(self, reduction: np.ndarray) -> np.ndarray:
        """Smooth the gain reduction with the attack and release of ``Speed``.

        The envelope is the state the module carries: the attack of one frame
        continues into the next, so the reduction does not jump at the frame
        boundary.
        """
        fs = float(self.sample_rate)
        if str(self.params.get("Speed")) == "Slow":
            attack_ms, release_ms = 20.0, 250.0
        else:
            attack_ms, release_ms = 1.0, 40.0
        a_att = math.exp(-_HOP / (max(attack_ms, 1e-6) * 1e-3 * fs))
        a_rel = math.exp(-_HOP / (max(release_ms, 1e-6) * 1e-3 * fs))
        out = np.zeros_like(reduction)
        prev = self._prev_reduction
        for i in range(len(reduction)):
            target = float(reduction[i])
            coef = a_att if target > prev else a_rel
            prev = coef * prev + (1.0 - coef) * target
            out[i] = prev
        self._prev_reduction = prev
        return out

    def _shapes(self, n_bins: int, cutoff_bin: int):
        """Return the soft edge and the tilt ramp of the sibilant band."""
        p = self.params
        nyquist = float(self.sample_rate) * 0.5
        cutoff_hz = float(p.get("Cutoff Frequency"))
        start_hz = cutoff_hz / _SKIRT_OCTAVES
        freqs = np.fft.rfftfreq(_NFFT, 1.0 / float(self.sample_rate))
        band = np.arange(n_bins) >= cutoff_bin

        # soft edge of the sibilant band: 0 below the skirt, 1 at the cutoff
        skirt = np.zeros(n_bins, dtype=np.float64)
        span = max(cutoff_hz - start_hz, 1.0)
        rising = (freqs >= start_hz) & (freqs < cutoff_hz)
        skirt[rising] = (freqs[rising] - start_hz) / span
        skirt[band] = 1.0

        # tilt ramp across the sibilant band
        ramp = np.zeros(n_bins, dtype=np.float64)
        tilt_span = max(nyquist - cutoff_hz, 1.0)
        ramp[band] = np.clip((freqs[band] - cutoff_hz) / tilt_span, 0.0, 1.0)
        return skirt, ramp, band

    def _gain_curve(
        self, mag: np.ndarray, reduction: np.ndarray, cutoff_bin: int
    ) -> np.ndarray:
        """Return the per-bin gain curve, in decibels.

        The reduction is applied above ``Cutoff Frequency``.  The edge of the
        sibilant band is softened over a fraction of an octave so the band
        does not end on a brick wall, which is what keeps the harsh part of a
        /s/ from being left behind.
        """
        p = self.params
        n_bins = mag.shape[0]
        skirt, ramp, band = self._shapes(n_bins, cutoff_bin)
        tilt = float(p.get("Spectral Tilt"))
        flatten = float(p.get("Spectral Flattening"))
        gain = np.zeros((n_bins, reduction.shape[0]), dtype=np.float64)
        if str(p.get("Mode")) == "Classic":
            gain[:] = -reduction[np.newaxis, :] * skirt[:, np.newaxis]
        else:
            gain[:] = -reduction[np.newaxis, :] * skirt[:, np.newaxis]
            gain += tilt * _TILT_RANGE_DB * ramp[:, np.newaxis]
            if flatten > 0.0 and bool(band.any()):
                band_mag = mag[band, :]
                mean = np.mean(band_mag, axis=0)
                excess = np.maximum(
                    0.0, 20.0 * np.log10(np.maximum(band_mag, 1e-12) / np.maximum(mean, 1e-12))
                )
                gain[band, :] -= flatten * 0.5 * excess
            np.minimum(gain, 0.0, out=gain)
        return gain

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
        list of numpy.ndarray
            The magnitudes of the de-essed signal and of the sibilance that
            was taken out of it.
        """
        fs = float(self.sample_rate)
        cutoff = float(self.params.get("Cutoff Frequency"))
        n_bins = mag.shape[1]
        cutoff_bin = int(min(max(1, round(cutoff / (fs / _NFFT))), n_bins - 1))

        # the detector works on the mono mix, exactly like the block-local
        # analysis it replaces
        if mag.shape[0] > 1:
            mono = np.abs((mag * np.exp(1j * phase)).mean(axis=0))
        else:
            mono = mag[0]
        reduction = self._smooth(self._detector(mono, cutoff_bin))
        gain_db = self._gain_curve(mono, reduction, cutoff_bin)
        gain = np.power(10.0, gain_db / 20.0)
        cleaned = mag * gain[np.newaxis, :, :]
        # what was taken out of the signal: the same phase, a smaller
        # magnitude
        removed = mag * (1.0 - gain)[np.newaxis, :, :]
        return [cleaned, removed]

    def _delay(self, raw: np.ndarray, raw_ess: np.ndarray, count: int):
        """Delay the output of the engine by the latency of the analysis."""
        if not self._primed:
            self._fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
            self._fifo_ess = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
            self._primed = True
        outs = []
        rest = []
        for fifo, piece in ((self._fifo, raw), (self._fifo_ess, raw_ess)):
            line = np.concatenate([fifo, piece], axis=0)
            take = min(count, int(line.shape[0]))
            out = line[:take]
            if take < count:
                out = np.concatenate(
                    [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
                )
            outs.append(out)
            rest.append(line[take:])
        self._fifo, self._fifo_ess = rest
        return outs

    def process(self, x: np.ndarray) -> np.ndarray:
        """De-ess a block of audio."""
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
            self._fifo_ess = np.zeros((0, self._channels), dtype=np.float64)
            self._primed = False
        pieces: list[np.ndarray] = []
        pieces_ess: list[np.ndarray] = []
        for start in range(0, n, _FEED_CHUNK):
            chunk = buf[start : start + _FEED_CHUNK]
            mag, phase = self._engine.analyze(chunk)
            if mag.shape[-1]:
                edited = self._edit(mag, phase)
            else:
                edited = [mag, mag]
            outs = self._engine.synthesize_many(edited, phase)
            pieces.append(outs[0])
            pieces_ess.append(outs[1])
        raw = (
            np.concatenate(pieces, axis=0)
            if pieces
            else np.zeros((0, self._channels), dtype=np.float64)
        )
        raw_ess = (
            np.concatenate(pieces_ess, axis=0)
            if pieces_ess
            else np.zeros((0, self._channels), dtype=np.float64)
        )
        cleaned, ess = self._delay(raw, raw_ess, n)
        out = ess if self.params.get("Output Ess Only") else cleaned
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
