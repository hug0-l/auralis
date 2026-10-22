"""
aurcore.modules.spectral_repair — Spectral Repair.

Non destructive spectral editing inside a selected time/frequency region.

The region is chosen by the host (the editor hands over the selection); the
module then edits it in one of five ways:

* **Attenuate** — reduce the energy inside the region;
* **Replace** — swap the region for the material of a surrounding region;
* **Gain** — raise the energy inside the region;
* **Pattern** — copy a similar pattern into the region;
* **Partials** — suppress the harmonic partials inside the region.

``Before Weighting`` and ``After Weighting`` shape the cross fade at the
borders of the region, ``Region Length`` sets how long the edit lasts and
``Direction`` decides whether the edit is smeared in time, in frequency or in
both directions.
"""
from __future__ import annotations

import numpy as np

from ..dsp.stft import STFT
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["SpectralRepair"]

#: Analysis window and hop (samples).
_FFT_SIZE = 2048
_HOP = 512


@register
class SpectralRepair(Module):
    """Spectral Repair — non destructive editing of a spectral region."""

    code = "AUR_SPECTRALREPAIR"
    display_name = "Spectral Repair"
    category = "Repair"
    edition = "standard"
    element = "SpectralRepair"
    analysis = False
    supports_learn = False  # no learning action for this module

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            desc(
                name="Mode",
                type="enum",
                default="Attenuate",
                choices=("Attenuate", "Replace", "Gain", "Pattern", "Partials"),
                group="Algorithm",
                help="What happens inside the selected region.",
            ),
            desc(
                name="Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Attenuate",
                help="How strongly the edit is applied.",
                aliases=("Strength",),
            ),
            desc(
                name="Region Length",
                type="float",
                default=0.25,
                minimum=0.02,
                maximum=10.0,
                unit="s",
                group="Attenuate",
                help="Length of the edited region in seconds.",
            ),
            desc(
                name="Direction",
                type="enum",
                default="2D",
                choices=("Horizontal", "Vertical", "2D"),
                group="Attenuate",
                help="Horizontal smears in time, Vertical in frequency, 2D in both.",
            ),
            desc(
                name="Multi Resolution",
                type="bool",
                default=True,
                group="Attenuate",
                help="Analyse the region with more than one window size.",
            ),
            desc(
                name="Before Weighting",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Attenuate",
                help="Cross fade weight at the start of the region.",
            ),
            desc(
                name="After Weighting",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Attenuate",
                help="Cross fade weight at the end of the region.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = STFT(_FFT_SIZE, _HOP, window="hann")
        self._engine_alt = STFT(_FFT_SIZE // 2, _HOP // 2, window="hann")
        # The region: (start_sample, end_sample, low_hz, high_hz)
        self._region: tuple[int, int, float, float] | None = None

    # -- region ---------------------------------------------------------------
    def set_region(
        self,
        start: float | None = None,
        end: float | None = None,
        low_hz: float | None = None,
        high_hz: float | None = None,
    ) -> None:
        """Select the region that is edited.

        Parameters
        ----------
        start:
            Start of the region in seconds (or ``None`` to keep the current one).
        end:
            End of the region in seconds.
        low_hz:
            Lower edge of the region in Hz.
        high_hz:
            Upper edge of the region in Hz.
        """
        current = self._region or (0, 0, 0.0, float(self.sample_rate) / 2.0)
        s = current[0] if start is None else int(round(float(start) * self.sample_rate))
        e = current[1] if end is None else int(round(float(end) * self.sample_rate))
        lo = current[2] if low_hz is None else float(low_hz)
        hi = current[3] if high_hz is None else float(high_hz)
        self._region = (min(s, e), max(s, e), max(0.0, lo), max(lo, float(hi)))

    @property
    def region(self) -> tuple[int, int, float, float] | None:
        """The selected region as ``(start_sample, end_sample, low_hz, high_hz)``."""
        return self._region

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the edit to the selected region.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels).

        Returns
        -------
        numpy.ndarray
            The edited block, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        if self._region is None:
            return arr

        p = self.params
        mode = str(p.get("Mode"))
        strength = float(p.get("Reduction"))
        direction = str(p.get("Direction"))
        before = float(p.get("Before Weighting"))
        after = float(p.get("After Weighting"))

        engines = [self._engine]
        if bool(p.get("Multi Resolution")):
            engines.append(self._engine_alt)

        out = arr.copy()
        for engine in engines:
            edited = self._apply(engine, arr, mode, strength, direction, before, after)
            out = out + (edited - arr) * (1.0 / len(engines))
        return out

    def _apply(
        self,
        engine: STFT,
        arr: np.ndarray,
        mode: str,
        strength: float,
        direction: str,
        before: float,
        after: float,
    ) -> np.ndarray:
        """Apply one window size to the block."""
        mag, phase = engine.analyze_block(arr, center=True)
        if mag.ndim == 2:
            mag = mag[None, :]
            phase = phase[None, :]
        channels, bins, frames = mag.shape
        # Work in the (frames, bins) layout.
        mag_t = np.transpose(mag, (0, 2, 1))
        phase_t = np.transpose(phase, (0, 2, 1))
        freqs = np.fft.rfftfreq(engine.n_fft, 1.0 / float(self.sample_rate))
        start, end, low_hz, high_hz = self._region  # type: ignore[misc]
        hop = engine.hop

        f0 = int(np.searchsorted(freqs, low_hz))
        f1 = int(np.searchsorted(freqs, high_hz))
        f1 = max(f1, f0 + 1)
        f1 = min(f1, bins)
        t0 = max(0, int((start - engine.n_fft // 2) // hop))
        t1 = min(frames, int((end - engine.n_fft // 2) // hop) + 1)
        if t1 <= t0:
            return arr.copy()

        time_weight = self._region_weight(t0, t1, frames, before, after)
        freq_weight = self._band_weight(f0, f1, bins)
        if direction == "Horizontal":
            weight = time_weight[:, None] * np.ones((1, bins))
        elif direction == "Vertical":
            weight = np.ones((frames, 1)) * freq_weight[None, :]
        else:
            weight = time_weight[:, None] * freq_weight[None, :]
        weight = np.broadcast_to(weight, (frames, bins)).copy()

        new_mag = mag_t.copy()
        if mode == "Attenuate":
            factor = 1.0 - strength
            region = np.zeros((frames, bins), dtype=np.float64)
            region[t0:t1, f0:f1] = factor
            new_mag = mag_t * (1.0 - region * weight[None, :, :])
        elif mode == "Gain":
            factor = 10 ** (12.0 * strength / 20.0)
            region = np.zeros((frames, bins), dtype=np.float64)
            region[t0:t1, f0:f1] = factor
            new_mag = mag_t * (1.0 + (region[None, :, :] - 1.0) * weight[None, :, :])
        elif mode == "Replace":
            new_mag = self._replace(mag_t, t0, t1, f0, f1, time_weight, freq_weight, strength)
        elif mode == "Pattern":
            new_mag = self._pattern(mag_t, t0, t1, f0, f1, time_weight, freq_weight, strength)
        elif mode == "Partials":
            new_mag = self._partials(mag_t, t0, t1, f0, f1, time_weight, freq_weight, strength)

        rendered = engine.synthesize_block(
            np.transpose(new_mag, (0, 2, 1)),
            np.transpose(phase_t, (0, 2, 1)),
            norm="exact",
            length=int(arr.shape[0]),
            center=True,
        )
        return np.asarray(rendered, dtype=np.float64)

    def _region_weight(
        self, t0: int, t1: int, frames: int, before: float, after: float
    ) -> np.ndarray:
        """Cross fade shape of the region borders."""
        weight = np.zeros(frames, dtype=np.float64)
        span = max(1, t1 - t0)
        edge_in = max(1, int(round(before * span * 0.5)))
        edge_out = max(1, int(round(after * span * 0.5)))
        for f in range(frames):
            if f < t0 or f >= t1:
                continue
            value = 1.0
            if f < t0 + edge_in:
                value = min(value, (f - t0) / edge_in)
            if f >= t1 - edge_out:
                value = min(value, max(0.0, (t1 - f) / edge_out))
            weight[f] = value
        return weight

    def _band_weight(self, f0: int, f1: int, bins: int) -> np.ndarray:
        """Cross fade shape of the region borders in the frequency axis."""
        weight = np.zeros(bins, dtype=np.float64)
        if f1 <= f0:
            return weight
        edge = max(1, (f1 - f0) // 8)
        for b in range(f0, min(f1, bins)):
            value = 1.0
            if b < f0 + edge:
                value = min(value, (b - f0) / edge)
            if b >= f1 - edge:
                value = min(value, max(0.0, (f1 - b) / edge))
            weight[b] = value
        return weight

    def _replace(
        self,
        mag: np.ndarray,
        t0: int,
        t1: int,
        f0: int,
        f1: int,
        time_weight: np.ndarray,
        freq_weight: np.ndarray,
        strength: float,
    ) -> np.ndarray:
        """Swap the region for the material that surrounds it."""
        out = mag.copy()
        frames = mag.shape[1]
        span = max(1, t1 - t0)
        src = t1 if t1 + span <= frames else max(0, t0 - span)
        for ch in range(mag.shape[0]):
            for i in range(span):
                f = t0 + i
                if f >= frames:
                    break
                blend = strength * float(time_weight[f]) * freq_weight[f0:f1]
                source = min(frames - 1, src + i)
                out[ch, f, f0:f1] = (
                    (1.0 - blend) * mag[ch, f, f0:f1] + blend * mag[ch, source, f0:f1]
                )
        return out

    def _pattern(
        self,
        mag: np.ndarray,
        t0: int,
        t1: int,
        f0: int,
        f1: int,
        time_weight: np.ndarray,
        freq_weight: np.ndarray,
        strength: float,
    ) -> np.ndarray:
        """Copy a similar pattern into the region.

        The pattern is taken from the frames that follow the region: the
        region is replaced by a cross faded continuation of its own material.
        """
        out = mag.copy()
        frames = mag.shape[1]
        span = max(1, t1 - t0)
        fade = max(1, span // 4)
        for ch in range(mag.shape[0]):
            for i in range(span):
                f = t0 + i
                if f >= frames:
                    break
                w = min(1.0, i / fade) if i < fade else 1.0
                blend = strength * float(time_weight[f]) * freq_weight[f0:f1] * w
                source = min(frames - 1, f + span)
                out[ch, f, f0:f1] = (
                    (1.0 - blend) * mag[ch, f, f0:f1] + blend * mag[ch, source, f0:f1]
                )
        return out

    def _partials(
        self,
        mag: np.ndarray,
        t0: int,
        t1: int,
        f0: int,
        f1: int,
        time_weight: np.ndarray,
        freq_weight: np.ndarray,
        strength: float,
    ) -> np.ndarray:
        """Suppress the harmonic partials inside the region."""
        out = mag.copy()
        frames = mag.shape[1]
        for ch in range(mag.shape[0]):
            block = mag[ch, t0:t1, f0:f1]
            if block.size == 0:
                continue
            average = np.mean(block, axis=0)
            threshold = max(float(np.median(average)) * 1.8, 1e-9)
            peaks = average > threshold
            if not np.any(peaks):
                continue
            for f in range(t0, min(t1, frames)):
                blend = strength * float(time_weight[f]) * freq_weight[f0:f1]
                row = mag[ch, f, f0:f1].copy()
                row[peaks] = row[peaks] * (1.0 - blend[peaks])
                out[ch, f, f0:f1] = row
        return out
