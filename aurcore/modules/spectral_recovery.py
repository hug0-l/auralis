"""
aurcore.modules.spectral_recovery — Spectral Recovery.

Restores recordings that lost their top end (a band limited transfer, a
telephone line, a truncated download):

* ``Learn`` detects the true bandwidth of the recording;
* ``Spectral Patching`` regenerates the missing band from the detected
  cutoff, using the spectral shape of the material below it;
* ``Low Gain`` / ``Low Cutoff`` shape the bottom of the spectrum and
  ``High Cutoff`` / ``High Gain`` shape the top.

The module is non destructive: the patched band is added to the signal, the
original content is never removed.
"""
from __future__ import annotations

import io
import math

import numpy as np

from ..dsp.stft import STFT
from ..dsp.util import db_to_lin
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["SpectralRecovery"]

#: Analysis window and hop (samples).
_FFT_SIZE = 2048
_HOP = 512

#: How far above the noise floor the spectrum has to reach for a band to
#: count as "present" when the bandwidth is detected.
_CUTOFF_MARGIN_DB = 6.0

#: The bandwidth is measured as the classic "-40 dB bandwidth".
_BANDWIDTH_DB = 40.0


@register
class SpectralRecovery(Module):
    """Spectral Recovery — rebuild a band limited recording."""

    code = "AUR_SPECTRALRECOVERY"
    display_name = "Spectral Recovery"
    category = "Repair"
    edition = "advanced"
    element = "SpectralRecovery"
    analysis = False
    supports_learn = True

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            desc(
                name="Spectral Patching",
                type="bool",
                default=True,
                group="Recovery",
                help="Regenerate the band above the detected cutoff.",
            ),
            desc(
                name="Low Gain",
                type="float",
                default=-20.0,
                minimum=-60.0,
                maximum=12.0,
                unit="dB",
                group="Recovery",
                help="Level below the low cutoff.",
                curve="taper",
            ),
            desc(
                name="Low Cutoff",
                type="float",
                default=200.0,
                minimum=20.0,
                maximum=2000.0,
                unit="Hz",
                group="Recovery",
                help="Frequency where the low shelf starts.",
                curve="log",
            ),
            desc(
                name="High Cutoff",
                type="float",
                default=4000.0,
                minimum=2000.0,
                maximum=16000.0,
                unit="Hz",
                group="Recovery",
                help="Frequency where the high shelf starts.",
                curve="log",
            ),
            desc(
                name="High Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=2.4,
                unit="dB",
                group="Recovery",
                help="Level above the high cutoff.",
                curve="taper",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = STFT(_FFT_SIZE, _HOP, window="hann")
        self._cutoff_hz: float | None = None
        self._spectrum_db: np.ndarray | None = None
        self._rng = np.random.default_rng(0x5EC0)

    def reset(self) -> None:
        """Forget the detected bandwidth."""
        self._cutoff_hz = None
        self._spectrum_db = None

    # -- learning -------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Detect the true bandwidth of the recording.

        The long term spectrum is measured and the highest frequency that
        still carries real signal (instead of the noise floor) is stored.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels).
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return
        mag, _ = self._engine.analyze_block(arr, center=True)
        mag = np.asarray(mag, dtype=np.float64)
        if mag.ndim == 3:
            mag = np.mean(mag, axis=0)
        freqs = np.fft.rfftfreq(_FFT_SIZE, 1.0 / float(self.sample_rate))
        # the magnitude matrix may be (frames, bins) or (bins, frames); the
        # per-bin level is the mean over the time axis in either case
        n_bins = len(freqs)
        if mag.ndim >= 2:
            axis = 0 if mag.shape[-1] == n_bins else 1
            mag = np.mean(mag, axis=axis)
        db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        self._spectrum_db = db
        self._cutoff_hz = self._detect_cutoff(freqs, db)

    def _detect_cutoff(self, freqs: np.ndarray, db: np.ndarray) -> float:
        """Return the highest frequency that still carries signal.

        The spectrum is smoothed first so the gaps between the harmonics do
        not break the band.  The bandwidth is then taken as the classic
        "-40 dB bandwidth": the highest frequency whose level is still within
        40 dB of the peak of the spectrum.  The noise floor acts as a lower
        guard so a silent recording does not report a bandwidth of zero.
        """
        db = np.asarray(db, dtype=np.float64).ravel()
        n = int(min(db.shape[0], freqs.shape[0]))
        if n == 0:
            return float(freqs[-1])
        db = db[:n]
        window = max(3, n // 40)
        kernel = np.ones(window, dtype=np.float64) / window
        smooth = np.convolve(db, kernel, mode="same")
        peak = float(np.percentile(smooth, 99.0))
        floor = float(np.percentile(smooth, 20.0))
        threshold = max(peak - _BANDWIDTH_DB, floor + _CUTOFF_MARGIN_DB)
        above = np.flatnonzero(smooth > threshold)
        if above.size == 0:
            return float(freqs[-1])
        return float(freqs[int(above[-1])])

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Restore the bandwidth of a block of audio.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels).

        Returns
        -------
        numpy.ndarray
            The restored block, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        p = self.params
        mag, phase = self._engine.analyze_block(arr, center=True)
        if mag.ndim == 2:
            mag = mag[None, :]
            phase = phase[None, :]
        freqs = np.fft.rfftfreq(_FFT_SIZE, 1.0 / float(self.sample_rate))

        cutoff = self._cutoff_hz
        if cutoff is None:
            # Auto detect from the material itself.
            db = 20.0 * np.log10(np.maximum(np.mean(mag, axis=0), 1e-12))
            cutoff = self._detect_cutoff(freqs, db)
            self._cutoff_hz = cutoff

        curve_db = self._shape_curve(freqs, cutoff)
        gain = db_to_lin(curve_db)

        # The phase of the original signal is preserved; only the magnitudes
        # are shaped and the missing band is added on top of them.
        out_mag = mag * gain[None, :, None]
        if bool(p.get("Spectral Patching")):
            patch_mag, patch_phase = self._patch(out_mag, mag, freqs, cutoff)
            out_mag = out_mag + patch_mag
            phase = phase + np.where(patch_mag > 0.0, patch_phase, 0.0)

        rendered = self._engine.synthesize_block(
            out_mag, phase, norm="exact", length=int(arr.shape[0]), center=True
        )
        return np.asarray(rendered, dtype=np.float64)

    def _shape_curve(self, freqs: np.ndarray, cutoff: float) -> np.ndarray:
        """Frequency response of the low and high shelves, in dB."""
        p = self.params
        low_gain = float(p.get("Low Gain"))
        low_cut = float(p.get("Low Cutoff"))
        high_cut = float(p.get("High Cutoff"))
        high_gain = float(p.get("High Gain"))
        curve = np.zeros_like(freqs)
        # Low shelf: a gentle transition over one octave below the cutoff.
        low = np.clip((low_cut - freqs) / np.maximum(low_cut, 1.0), 0.0, 1.0)
        curve += low_gain * low * low * (3.0 - 2.0 * low)
        # High shelf.
        high = np.clip((freqs - high_cut) / np.maximum(high_cut * 0.5, 1.0), 0.0, 1.0)
        curve += high_gain * high * high * (3.0 - 2.0 * high)
        return curve

    def _patch(
        self,
        spec: np.ndarray,
        mag: np.ndarray,
        freqs: np.ndarray,
        cutoff: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Synthesize the band above the detected cutoff.

        The patch follows the spectral shape of the material below the cutoff
        (extrapolated) so the restored band blends in naturally.

        Returns
        -------
        tuple of numpy.ndarray
            The magnitude and the phase of the synthesised band.
        """
        p = self.params
        high_cut = float(p.get("High Cutoff"))
        empty_mag = np.zeros_like(spec)
        empty_phase = np.zeros_like(spec)
        if cutoff >= high_cut:
            return empty_mag, empty_phase
        start = int(np.searchsorted(freqs, cutoff))
        stop = int(np.searchsorted(freqs, high_cut))
        if stop <= start:
            return empty_mag, empty_phase

        channels, bins, frames = spec.shape
        # The average spectrum below the cutoff decides the shape of the patch.
        average = 20.0 * np.log10(
            np.maximum(np.mean(np.mean(mag, axis=2), axis=0), 1e-12)
        )
        low = average[:start]
        if low.size < 4:
            return empty_mag, empty_phase
        # Extrapolate the slope of the spectrum into the missing band.
        edge = int(max(1, low.size // 2))
        slope = float(np.polyfit(freqs[:edge], low[:edge], 1)[0])
        target_db = np.clip(
            low[-1] + slope * (freqs[start:stop] - freqs[start - 1]), -140.0, -10.0
        )

        level = np.power(10.0, target_db / 20.0)
        high_gain = float(p.get("High Gain"))
        if high_gain:
            level = level * db_to_lin(high_gain)
        scale = float(_FFT_SIZE) * 0.5
        patch_mag = np.zeros((channels, stop - start, frames), dtype=np.float64)
        for ch in range(channels):
            noise = self._rng.standard_normal((stop - start, frames))
            patch_mag[ch] = level[:, None] * np.abs(noise)
        patch_mag *= scale / np.maximum(scale, 1.0)
        patch_phase = self._rng.uniform(0.0, 2.0 * math.pi, patch_mag.shape)
        out_mag = np.zeros_like(spec)
        out_phase = np.zeros_like(spec)
        out_mag[:, start:stop, :] = patch_mag
        out_phase[:, start:stop, :] = patch_phase
        return out_mag, out_phase
