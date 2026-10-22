"""
aurcore.modules.spectral_editor — Spectral Editor.

Non destructive, *selection based* spectral editing.

The module works on a **region**: a rectangle in the time/frequency plane.
Only the samples inside that rectangle are touched; everything outside it is
passed through bit for bit, which is what makes the edit non destructive.

Five modes are available:

* **Attenuate** — reduce the energy inside the region, the surroundings stay
  as they are;
* **Replace**   — substitute the region's content with material interpolated
  from the material that surrounds it (a spectral band replacement);
* **Gain**      — apply a plain gain to the region;
* **Pattern**   — look for a similar region elsewhere in the recording (the
  similarity search of :mod:`aurcore.modules.find_similar`) and copy it into
  the selected region;
* **Partials**  — suppress the harmonic partials inside the region.

The region is part of the module state: it survives a ``serialize`` /
``deserialize`` round trip and it is carried by the chain document, so a
session reopens with the edit exactly where the user put it.

Units
-----
Times are seconds, frequencies are hertz, levels are decibels.  The core
never sees normalised values (CONTRACT §3).
"""
from __future__ import annotations

import json
import math

import numpy as np

from ..dsp.spectral import interpolate_band
from ..dsp.stft import STFT
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module
from .find_similar import FindSimilar

__all__ = ["SpectralEditor"]


#: Analysis window of the main resolution, in samples.
_FFT_SIZE = 2048
#: Hop of the main resolution, in samples.
_HOP = 512
#: Analysis window of the second resolution (multi resolution mode).
_FFT_SIZE_ALT = 1024
#: Hop of the second resolution, in samples.
_HOP_ALT = 256

#: How much the ``Attenuate`` mode removes at ``Strength`` = 1, in dB.
_MAX_ATTENUATION_DB = 60.0
#: How much the ``Gain`` mode adds at ``Strength`` = 1, in dB.
_MAX_GAIN_DB = 12.0

#: Longest cross fade at a region border, in seconds.
_MAX_FADE_S = 0.02

#: Lowest similarity a ``Pattern`` candidate has to reach to be copied.
_PATTERN_MIN_SCORE = 0.30

_EPS = 1e-20


@register
class SpectralEditor(Module):
    """Spectral Editor — non destructive editing of a spectral region.

    The region is chosen by the host (the editor hands the selection over);
    the module then edits it in one of five ways.  ``Region Length`` sets how
    long the edit lasts, ``Before Weighting`` and ``After Weighting`` shape the
    cross fade at the borders of the region and ``Direction`` decides whether
    the cross fade runs along the time axis, along the frequency axis or along
    both.
    """

    code = "AUR_SPECTRALEDITOR"
    display_name = "Spectral Editor"
    category = "Repair"
    edition = "standard"
    element = "SpectralEditor"
    analysis = False
    supports_learn = False
    ml = False

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
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
                group="Edit",
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
                group="Edit",
                help="Length of the edited region in seconds.",
            ),
            desc(
                name="Direction",
                type="enum",
                default="2D",
                choices=("Horizontal", "Vertical", "2D"),
                group="Edit",
                help="Axis the cross fade runs along: in time, in frequency, or both.",
            ),
            desc(
                name="Multi Resolution",
                type="bool",
                default=True,
                group="Edit",
                help="Analyse the region with more than one window size.",
            ),
            desc(
                name="Before Weighting",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Edit",
                help="Cross fade weight at the start of the region: 0 is a hard edge, 1 a 20 ms "
                "fade.",
            ),
            desc(
                name="After Weighting",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Edit",
                help="Cross fade weight at the end of the region: 0 is a hard edge, 1 a 20 ms "
                "fade.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = STFT(_FFT_SIZE, _HOP, window="hann")
        self._engine_alt = STFT(_FFT_SIZE_ALT, _HOP_ALT, window="hann")
        self._region: tuple[float, float, float, float] | None = None
        self._finder: FindSimilar | None = None

    def reset(self) -> None:
        """Forget the region and the cached similarity search."""
        self._region = None
        self._finder = None

    # -- region ---------------------------------------------------------------
    def set_region(
        self,
        start_s: float | None = None,
        end_s: float | None = None,
        low_hz: float | None = None,
        high_hz: float | None = None,
    ) -> None:
        """Select the region that is edited.

        Parameters
        ----------
        start_s:
            Start of the region in seconds.  ``None`` keeps the current value.
        end_s:
            End of the region in seconds.
        low_hz:
            Lower edge of the region in Hz.
        high_hz:
            Upper edge of the region in Hz.

        Notes
        -----
        The ``Region Length`` parameter follows the selection: it is set to the
        length of the new region so that the parameter and the drawn rectangle
        always agree.
        """
        current = self._region or (0.0, 0.0, 0.0, float(self.sample_rate) / 2.0)
        start = current[0] if start_s is None else float(start_s)
        end = current[1] if end_s is None else float(end_s)
        low = current[2] if low_hz is None else float(low_hz)
        high = current[3] if high_hz is None else float(high_hz)
        self._set_region_raw(start, end, low, high)
        length = min(max(self._region[1] - self._region[0], 0.02), 10.0)
        self.params.set("Region Length", length)

    def _set_region_raw(
        self,
        start_s: float | None,
        end_s: float | None,
        low_hz: float | None,
        high_hz: float | None,
    ) -> None:
        """Store the region without touching the parameters."""
        current = self._region or (0.0, 0.0, 0.0, float(self.sample_rate) / 2.0)
        start = current[0] if start_s is None else float(start_s)
        end = current[1] if end_s is None else float(end_s)
        low = current[2] if low_hz is None else float(low_hz)
        high = current[3] if high_hz is None else float(high_hz)
        if not (math.isfinite(start) and math.isfinite(end)):
            return
        if not (math.isfinite(low) and math.isfinite(high)):
            return
        if end < start:
            start, end = end, start
        nyquist = float(self.sample_rate) / 2.0
        low = min(max(low, 0.0), nyquist)
        high = min(max(high, 0.0), nyquist)
        if high < low:
            low, high = high, low
        self._region = (start, end, low, high)

    @property
    def region(self) -> dict | None:
        """The selected region as ``{start, end, low, high}`` (seconds / Hz)."""
        if self._region is None:
            return None
        start, end, low, high = self._region
        return {"start": start, "end": end, "low": low, "high": high}

    def get_region(self) -> dict | None:
        """Return a copy of the selected region, or ``None``."""
        return self.region

    # -- state ----------------------------------------------------------------
    def serialize(self) -> dict:
        """Return the module state (the selected region) as a JSON-safe dict."""
        region = self.region
        if region is None:
            return {}
        return {"region": region}

    def deserialize(self, data: dict) -> None:
        """Restore the state produced by :meth:`serialize`."""
        region = (data or {}).get("region")
        if not isinstance(region, dict):
            return
        self._set_region_raw(
            region.get("start"),
            region.get("end"),
            region.get("low"),
            region.get("high"),
        )

    def _state_blob(self) -> bytes:
        """Serialise the region for the chain document."""
        region = self.region
        if region is None:
            return b""
        return json.dumps(region, sort_keys=True).encode("utf-8")

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a region produced by :meth:`_state_blob`.

        Both shapes are accepted: the bare region
        (``{"start": …, "end": …, "low": …, "high": …}``) and the region
        nested under a ``region`` key, so a host can hand the state of
        :meth:`serialize` over unchanged.
        """
        try:
            payload = json.loads(bytes(data).decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if isinstance(payload.get("region"), dict):
            payload = payload["region"]
        self._set_region_raw(
            payload.get("start"),
            payload.get("end"),
            payload.get("low"),
            payload.get("high"),
        )

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the edit to the selected region.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The edited block, same shape as ``x``.  Samples outside the region
            are bit identical to the input.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0 or self._region is None:
            return arr.copy()

        params = self.params
        mode = str(params.get("Mode"))
        strength = float(params.get("Reduction"))
        direction = str(params.get("Direction"))
        before = float(params.get("Before Weighting"))
        after = float(params.get("After Weighting"))
        if strength <= 0.0:
            return arr.copy()

        start_s, end_s, low_hz, high_hz = self._region
        length = float(params.get("Region Length"))
        centre = 0.5 * (start_s + end_s)
        t0 = max(start_s, centre - 0.5 * length)
        t1 = min(end_s, centre + 0.5 * length)
        if t1 <= t0:
            t0, t1 = start_s, end_s
        if t1 <= t0:
            return arr.copy()

        s0 = int(round(t0 * self.sample_rate))
        s1 = int(round(t1 * self.sample_rate))
        if s1 <= s0:
            return arr.copy()
        if s0 >= arr.shape[0] or s1 <= 0:
            return arr.copy()  # the region lies outside of this block

        pattern_source = None
        fundamental = 0.0
        if mode == "Pattern":
            pattern_source = self._find_pattern(arr, s0, s1)
        elif mode == "Partials":
            fundamental = self._estimate_fundamental(arr, s0, s1)

        fade_in = int(round(before * _MAX_FADE_S * self.sample_rate))
        fade_out = int(round(after * _MAX_FADE_S * self.sample_rate))

        engines = [self._engine]
        if bool(params.get("Multi Resolution")):
            engines.append(self._engine_alt)

        edited = arr.copy()
        for engine in engines:
            one = self._apply(
                engine,
                arr,
                mode=mode,
                strength=strength,
                direction=direction,
                fade_in=fade_in,
                fade_out=fade_out,
                s0=s0,
                s1=s1,
                low_hz=low_hz,
                high_hz=high_hz,
                pattern_source=pattern_source,
                fundamental=fundamental,
            )
            edited = edited + (one - arr) / float(len(engines))

        mask = self._time_mask(arr.shape[0], s0, s1, fade_in, fade_out)
        out = arr + (edited - arr) * mask[:, None]
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    # -- spectral core --------------------------------------------------------
    def _apply(
        self,
        engine: STFT,
        arr: np.ndarray,
        *,
        mode: str,
        strength: float,
        direction: str,
        fade_in: int,
        fade_out: int,
        s0: int,
        s1: int,
        low_hz: float,
        high_hz: float,
        pattern_source: tuple[int, int] | None,
        fundamental: float,
    ) -> np.ndarray:
        """Run one window size over the block and return the edited block."""
        n_samples = int(arr.shape[0])
        mag, phase = engine.analyze_block(arr, center=True)
        if mag.ndim == 2:
            mag = mag[None, :, :]
            phase = phase[None, :, :]
        channels, bins, frames = mag.shape
        spec = np.transpose(mag * np.exp(1j * phase), (0, 2, 1))  # (ch, frames, bins)

        freqs = np.fft.rfftfreq(engine.n_fft, 1.0 / float(self.sample_rate))
        f0 = int(np.searchsorted(freqs, low_hz, side="left"))
        f1 = int(np.searchsorted(freqs, high_hz, side="right"))
        f0 = int(np.clip(f0, 0, bins - 1))
        f1 = int(np.clip(f1, f0 + 1, bins))

        hop = int(engine.hop)
        # The frames that straddle the border of the region hold half of the
        # material that belongs inside it, so the edited range is padded by
        # half a window.  The sample accurate mask keeps the audible result
        # inside the region.
        pad = int(math.ceil(engine.n_fft / (2.0 * hop)))
        t0 = int(np.clip(int(math.floor(s0 / hop)) - pad, 0, frames - 1))
        t1 = int(np.clip(int(math.ceil(s1 / hop)) + pad, t0 + 1, frames))
        if t1 <= t0 or f1 <= f0:
            return arr.copy()

        time_weight = self._frame_weight(
            t0, t1, frames, fade_in, fade_out, hop, direction
        )
        freq_weight = self._band_weight(f0, f1, bins, direction)
        blend = time_weight[:, None] * freq_weight[None, :]

        if mode == "Attenuate":
            gain_db = -_MAX_ATTENUATION_DB * strength * blend
            new_spec = spec * np.power(10.0, gain_db / 20.0)[None, :, :]
        elif mode == "Gain":
            gain_db = _MAX_GAIN_DB * strength * blend
            new_spec = spec * np.power(10.0, gain_db / 20.0)[None, :, :]
        elif mode == "Replace":
            new_spec = self._replace(
                spec, engine, freqs, f0, f1, t0, t1, blend, strength
            )
        elif mode == "Pattern":
            new_spec = self._pattern(
                spec,
                f0,
                f1,
                t0,
                t1,
                blend,
                strength,
                pattern_source,
                hop,
                s0,
                frames,
                int(engine.n_fft),
            )
        elif mode == "Partials":
            new_spec = self._partials(
                spec, f0, f1, t0, t1, blend, strength, fundamental, engine.n_fft
            )
        else:  # pragma: no cover - the enum only holds the five known modes
            new_spec = spec

        rendered = engine.synthesize_block(
            np.abs(np.transpose(new_spec, (0, 2, 1))),
            np.angle(np.transpose(new_spec, (0, 2, 1))),
            norm="exact",
            length=n_samples,
            center=True,
        )
        return np.asarray(rendered, dtype=np.float64)

    # -- weights --------------------------------------------------------------
    def _frame_weight(
        self,
        t0: int,
        t1: int,
        frames: int,
        fade_in: int,
        fade_out: int,
        hop: int,
        direction: str,
    ) -> np.ndarray:
        """Cross fade shape of the region borders along the time axis.

        The returned array covers every frame of the spectrogram; it is zero
        outside the edited range and shaped by the border weights inside it.
        """
        weight = np.zeros(max(1, int(frames)), dtype=np.float64)
        span = max(1, t1 - t0)
        if direction == "Vertical":
            weight[t0:t1] = 1.0
            return weight
        edge_in = int(np.clip(int(math.ceil(fade_in / float(hop))), 0, span // 2))
        edge_out = int(np.clip(int(math.ceil(fade_out / float(hop))), 0, span - edge_in))
        weight[t0:t1] = 1.0
        if edge_in > 0:
            weight[t0 : t0 + edge_in] = _ramp(edge_in)
        if edge_out > 0:
            weight[t1 - edge_out : t1] = _ramp(edge_out)[::-1]
        return weight

    def _band_weight(self, f0: int, f1: int, bins: int, direction: str) -> np.ndarray:
        """Cross fade shape of the region borders along the frequency axis."""
        weight = np.zeros(bins, dtype=np.float64)
        if f1 <= f0:
            return weight
        weight[f0:f1] = 1.0
        if direction == "Horizontal":
            return weight
        edge = max(1, (f1 - f0) // 8)
        edge = int(min(edge, 2, max(1, (f1 - f0) // 4)))
        if edge > 0:
            weight[f0 : f0 + edge] = _ramp(edge)
            weight[f1 - edge : f1] = _ramp(edge)[::-1]
        return weight

    def _time_mask(
        self, n_samples: int, s0: int, s1: int, fade_in: int, fade_out: int
    ) -> np.ndarray:
        """Sample accurate cross fade that keeps the region local."""
        mask = np.zeros(n_samples, dtype=np.float64)
        s0 = int(np.clip(s0, 0, n_samples))
        s1 = int(np.clip(s1, 0, n_samples))
        if s1 <= s0:
            return mask
        mask[s0:s1] = 1.0
        span = s1 - s0
        fade_in = int(np.clip(fade_in, 0, span // 2))
        fade_out = int(np.clip(fade_out, 0, span - fade_in))
        if fade_in > 0:
            mask[s0 : s0 + fade_in] = _ramp(fade_in)
        if fade_out > 0:
            mask[s1 - fade_out : s1] = _ramp(fade_out)[::-1]
        return mask

    # -- modes ----------------------------------------------------------------
    def _replace(
        self,
        spec: np.ndarray,
        engine: STFT,
        freqs: np.ndarray,
        f0: int,
        f1: int,
        t0: int,
        t1: int,
        blend: np.ndarray,
        strength: float,
    ) -> np.ndarray:
        """Replace the region with material interpolated from its borders.

        The magnitude of the band is replaced by the magnitude interpolated
        from the frames that surround the region; the phase of the region is
        kept, which keeps the edit free of the smearing a phase interpolation
        would introduce.
        """
        out = spec.copy()
        low_hz = float(freqs[f0])
        high_hz = float(freqs[f1 - 1])
        for channel in range(spec.shape[0]):
            magnitude = np.abs(spec[channel])
            phase = np.angle(spec[channel])
            patch_mag, _patch_phase = interpolate_band(
                magnitude,
                phase,
                low_hz,
                high_hz,
                t0,
                max(t0, t1 - 1),
                sr=int(self.sample_rate),
                n_fft=int(engine.n_fft),
                strength=1.0,
            )
            weight = (strength * blend)[:, f0:f1]
            blended = (
                (1.0 - weight) * magnitude[:, f0:f1]
                + weight * patch_mag[:, f0:f1]
            )
            out[channel, :, f0:f1] = blended * np.exp(1j * phase[:, f0:f1])
        return out

    def _pattern(
        self,
        spec: np.ndarray,
        f0: int,
        f1: int,
        t0: int,
        t1: int,
        blend: np.ndarray,
        strength: float,
        source: tuple[int, int] | None,
        hop: int,
        s0: int,
        frames: int,
        n_fft: int,
    ) -> np.ndarray:
        """Copy a similar region of the recording into the selected region."""
        out = spec.copy()
        if source is None:
            return out
        bins = spec.shape[2]
        delta = float(source[0]) - float(s0)
        offset = int(round(delta / float(hop)))
        # The residual of the frame offset is a shift of a fraction of a
        # sample; it is undone with the phase rotation of a time shift so that
        # the copy lands exactly on the region.
        residual = delta - offset * float(hop)
        rotation = np.exp(
            2j * np.pi * np.arange(bins, dtype=np.float64) * residual / float(n_fft)
        )
        for frame in range(t0, t1):
            other = frame + offset
            if other < 0 or other >= frames:
                continue
            weight = (strength * blend[frame][f0:f1])[None, :]
            copied = spec[:, other, f0:f1] * rotation[f0:f1][None, :]
            out[:, frame, f0:f1] = (
                (1.0 - weight) * spec[:, frame, f0:f1] + weight * copied
            )
        return out

    def _partials(
        self,
        spec: np.ndarray,
        f0: int,
        f1: int,
        t0: int,
        t1: int,
        blend: np.ndarray,
        strength: float,
        fundamental: float,
        n_fft: int,
    ) -> np.ndarray:
        """Suppress the harmonic partials inside the region."""
        out = spec.copy()
        bins = spec.shape[2]
        bin_hz = float(self.sample_rate) / float(n_fft)
        partials = self._partial_frequencies(spec, f0, f1, fundamental, bin_hz, bins)
        if not partials:
            return out
        half_width = min(max(0.35 * fundamental, 60.0), 250.0)
        freqs = np.arange(bins, dtype=np.float64) * bin_hz
        depth = np.zeros(bins, dtype=np.float64)
        for centre in partials:
            distance = np.abs(freqs - centre) / half_width
            shape = np.where(distance < 1.0, 0.5 * (1.0 + np.cos(np.pi * distance)), 0.0)
            depth = np.maximum(depth, shape)
        depth[:f0] = 0.0
        depth[f1:] = 0.0
        gain = 1.0 - strength * blend * depth[None, :]
        out[:, t0:t1, :] = out[:, t0:t1, :] * gain[t0:t1][None, :, :]
        return out

    def _partial_frequencies(
        self,
        spec: np.ndarray,
        f0: int,
        f1: int,
        fundamental: float,
        bin_hz: float,
        bins: int,
    ) -> list[float]:
        """Return the frequencies of the harmonic partials inside the band."""
        if f1 <= f0:
            return []
        partials: list[float] = []
        if fundamental > 0.0:
            index = 1
            while index * fundamental < bins * bin_hz:
                centre = index * fundamental
                if f0 * bin_hz <= centre < f1 * bin_hz:
                    partials.append(centre)
                index += 1
            if partials:
                return partials
        # No clear fundamental: fall back to the spectral peaks of the region.
        magnitude = np.abs(spec)
        if magnitude.size == 0:
            return []
        average = magnitude[:, :, f0:f1].mean(axis=(0, 1))
        if average.size == 0:
            return []
        median = float(np.median(average))
        threshold = max(median * 3.0, 1e-12)
        peaks: list[float] = []
        for offset in range(1, average.size - 1):
            value = average[offset]
            if value > threshold and value >= average[offset - 1] and value > average[offset + 1]:
                peaks.append((f0 + offset) * bin_hz)
        return peaks

    # -- helpers --------------------------------------------------------------
    def _find_pattern(
        self, arr: np.ndarray, s0: int, s1: int
    ) -> tuple[int, int] | None:
        """Look for a similar region elsewhere in the recording."""
        start = int(np.clip(s0, 0, arr.shape[0]))
        end = int(np.clip(s1, 0, arr.shape[0]))
        if end - start < 64:
            return None
        if self._finder is None:
            self._finder = FindSimilar(int(self.sample_rate), int(self.channels))
        finder = self._finder
        finder.reset()
        finder.learn(arr[start:end])
        if not finder.has_reference:
            return None
        for match_start, match_end, score in finder.rank(arr):
            if score < _PATTERN_MIN_SCORE:
                break
            if match_start < end and start < match_end:
                continue  # the match overlaps the region itself
            return int(match_start), int(match_end)
        return None

    def _estimate_fundamental(self, arr: np.ndarray, s0: int, s1: int) -> float:
        """Estimate the fundamental of the material inside the region, in Hz."""
        start = int(np.clip(s0, 0, arr.shape[0]))
        end = int(np.clip(s1, 0, arr.shape[0]))
        if end - start < 256:
            return 0.0
        signal = arr[start:end]
        if signal.ndim == 2:
            signal = signal.mean(axis=1)
        signal = np.asarray(signal, dtype=np.float64)
        n = signal.size
        if n < 256:
            return 0.0
        signal = signal - float(signal.mean())
        if float(np.dot(signal, signal)) <= _EPS:
            return 0.0
        sr = float(self.sample_rate)
        min_lag = max(2, int(sr / 2000.0))
        max_lag = min(n - 1, int(sr / 40.0))
        if max_lag <= min_lag:
            return 0.0
        size = 1 << int(math.ceil(math.log2(2 * n)))
        spectrum = np.fft.rfft(signal, size)
        correlation = np.fft.irfft(spectrum * np.conj(spectrum), size)[: max_lag + 1]
        if correlation[0] <= 0.0:
            return 0.0
        correlation = correlation / correlation[0]
        window = correlation[min_lag : max_lag + 1]
        lag = min_lag + int(np.argmax(window))
        if correlation[lag] < 0.3:
            return 0.0
        if 0 < lag < max_lag:
            left = correlation[lag - 1]
            middle = correlation[lag]
            right = correlation[lag + 1]
            denominator = left - 2.0 * middle + right
            if abs(denominator) > 1e-12:
                lag = lag + 0.5 * (left - right) / denominator
        if lag <= 0.0:
            return 0.0
        return sr / lag


def _ramp(length: int) -> np.ndarray:
    """A raised cosine from 0 to 1 over ``length`` samples."""
    count = int(max(1, length))
    t = np.arange(count, dtype=np.float64) / float(max(1, count - 1))
    return 0.5 - 0.5 * np.cos(np.pi * t)
