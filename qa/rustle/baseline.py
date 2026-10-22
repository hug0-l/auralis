"""qa.rustle.baseline — the De-Rustle module as it was before the fix.

This is a copy of ``aurcore/modules/de_rustle.py`` in its original state,
kept here so that the before/after table of ``qa/rustle/REPORT.md`` can be
reproduced with one command::

    python qa/rustle/run_rustle.py --baseline

The algorithm is the shipped one, unchanged; only the relative imports are
rewritten so that the file can live outside the package.  The class is *not*
registered, so it cannot shadow the real module in the registry.

What the original implementation did wrong (all three are measured in
``qa/rustle/REPORT.md``):

* the resynthesis transposed the spectrogram the wrong way for one channel,
  so a mono block came out as noise (RMS 11.6 instead of 0.14);
* the noise floor estimate was taken over the whole block, so a wanted
  partial ended up inside the floor and was subtracted from itself
  (5.6 dB of wanted content lost);
* the floor was broadcast over the frame axis, which made the stereo path
  fall back to the local overlap-add on every long block.
"""
from __future__ import annotations

import math

import numpy as np

from aurcore.params import ParamDesc
from aurcore.modules.base import Module

try:  # pragma: no cover - the shared DSP package is optional
    from aurcore.dsp.stft import istft as _sp_istft, stft as _sp_stft
except Exception:  # pragma: no cover
    _sp_stft = _sp_istft = None


__all__ = ["DeRustle"]

_EPS = 1e-12

_MAX_OVERSUB = 1.6
_MIN_FLOOR = 0.06
_NFFT = 2048
_HOP = 512
_FLOOR_BLOCK = 0.5


def _shared_stft_usable() -> bool:
    if _sp_stft is None or _sp_istft is None:
        return False
    try:
        probe = np.zeros(1024, dtype=np.float64)
        probe[16] = 1.0
        spec = _sp_stft(probe, n_fft=256, hop=64, window="hann", center=True)
        back = _sp_istft(spec, hop=64, window="hann", length=1024)
        return back.shape[0] == 1024 and bool(np.all(np.isfinite(back)))
    except Exception:
        return False


_USE_SHARED_STFT = _shared_stft_usable()


def _spectrogram(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if _USE_SHARED_STFT:
        try:
            spec = _sp_stft(x, n_fft=n_fft, hop=hop, window="hann", center=True)
            if spec.ndim == 2:
                return np.transpose(spec, (1, 0))[:, :, None]
            return np.transpose(spec, (2, 1, 0))
        except Exception:
            pass
    win = np.hanning(n_fft)
    n, ch = x.shape
    pad = n_fft // 2
    xp = np.concatenate(
        [np.zeros((pad, ch), dtype=np.float64), x, np.zeros((n_fft, ch), dtype=np.float64)],
        axis=0,
    )
    n_frames = 1 + max(0, len(xp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = xp[idx] * win[:, None]
    return np.fft.rfft(frames, axis=1)


def _resynthesize(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    if _USE_SHARED_STFT:
        try:
            shaped = spec[:, :, 0] if spec.shape[2] == 1 else np.transpose(spec, (2, 1, 0))
            out = _sp_istft(shaped, hop=hop, window="hann", length=length)
            if out.ndim == 1:
                out = out[:, None]
            return np.asarray(out, dtype=np.float64)[:length]
        except Exception:
            pass
    win = np.hanning(n_fft)
    frames = np.fft.irfft(spec, n=n_fft, axis=1) * win[:, None]
    n_frames = frames.shape[0]
    total = (n_frames - 1) * hop + n_fft
    out = np.zeros((total, spec.shape[2]), dtype=np.float64)
    wsum = np.zeros(total, dtype=np.float64)
    w2 = win * win
    for i in range(n_frames):
        out[i * hop:i * hop + n_fft] += frames[i]
        wsum[i * hop:i * hop + n_fft] += w2
    out /= np.maximum(wsum, 1e-8)[:, None]
    start = n_fft // 2
    out = out[start:start + length]
    if out.shape[0] < length:
        out = np.concatenate(
            [out, np.zeros((length - out.shape[0], out.shape[1]), dtype=np.float64)], axis=0
        )
    return out



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
        self._floor: np.ndarray | None = None
        self._coherence: np.ndarray | None = None

    def describe(self) -> list[ParamDesc]:
        return [
            ParamDesc(name="Reduction Strength", type="float", default=0.5, minimum=0.0,
                      maximum=1.0, group="Reduction",
                      help="How much of the estimated floor is subtracted."),
            ParamDesc(name="Ambience Preservation", type="float", default=0.5, minimum=0.0,
                      maximum=1.0, group="Reduction",
                      help="How much of the ambience is left in the signal."),
            ParamDesc(name="Separation Algorithm", type="enum", default="Channel Independent",
                      minimum=0.0, maximum=2.0,
                      choices=("Channel Independent", "Joint", "Advanced Joint"),
                      group="Algorithm",
                      help="Whether the channels are cleaned on their own or together."),
        ]

    def reset(self) -> None:
        self._floor = None
        self._coherence = None

    def _floor_estimate(self, mag: np.ndarray) -> np.ndarray:
        n_frames = mag.shape[0]
        if n_frames == 0:
            return np.zeros(mag.shape[1], dtype=np.float64)
        block = max(4, int(_FLOOR_BLOCK * self.sample_rate / _HOP))
        floors = []
        for start in range(0, n_frames, block):
            chunk = mag[start:start + block]
            floors.append(np.percentile(chunk, 10.0, axis=0))
        floors = np.asarray(floors, dtype=np.float64)
        if len(floors) == 1:
            return floors[0]
        out = np.empty_like(mag)
        edges = np.linspace(0, len(floors) - 1, n_frames)
        for i in range(n_frames):
            pos = edges[i]
            low = int(math.floor(pos))
            high = min(low + 1, len(floors) - 1)
            frac = pos - low
            out[i] = (1.0 - frac) * floors[low] + frac * floors[high]
        return out

    def _coherence_estimate(self, spec: np.ndarray) -> np.ndarray:
        n_frames = spec.shape[0]
        if n_frames < 2 or spec.shape[2] < 2:
            return np.zeros(spec.shape[1], dtype=np.float64)
        alpha = math.exp(-_HOP / (0.25 * self.sample_rate))
        sxx = np.zeros(spec.shape[1])
        syy = np.zeros(spec.shape[1])
        sxy = np.zeros(spec.shape[1], dtype=np.complex128)
        out = np.empty((n_frames, spec.shape[1]), dtype=np.float64)
        for i in range(n_frames):
            x = spec[i, :, 0]
            y = spec[i, :, 1]
            sxx = alpha * sxx + (1.0 - alpha) * np.abs(x) ** 2
            syy = alpha * syy + (1.0 - alpha) * np.abs(y) ** 2
            sxy = alpha * sxy + (1.0 - alpha) * (x * np.conj(y))
            denom = sxx * syy
            out[i] = np.where(denom > 1e-18, np.abs(sxy) ** 2 / np.maximum(denom, 1e-18), 0.0)
        return np.clip(out, 0.0, 1.0)

    def _tonality(self, mag: np.ndarray) -> np.ndarray:
        background = np.zeros_like(mag)
        count = 0
        for d in range(6, 17):
            background[:, d:] += mag[:, :-d]
            background[:, :-d] += mag[:, d:]
            count += 2
        background /= float(max(count, 1))
        ratio = mag / np.maximum(background, 1e-12)
        return np.clip((ratio - 1.0) / 3.0, 0.0, 1.0)

    def _gain_curve(self, mag: np.ndarray, floor: np.ndarray) -> np.ndarray:
        p = self.params
        strength = float(p.get("Reduction Strength"))
        preservation = float(p.get("Ambience Preservation"))
        algorithm = str(p.get("Separation Algorithm"))
        over = strength * _MAX_OVERSUB
        keep = _MIN_FLOOR + (1.0 - _MIN_FLOOR) * preservation
        if algorithm == "Advanced Joint" and self._coherence is not None:
            protect = self._coherence * self._tonality(mag)
            over = over * (1.0 - 0.85 * protect)
        clean = mag - over * floor[None, :]
        clean = np.maximum(clean, keep * floor[None, :])
        gain = np.where(mag > _EPS, clean / np.maximum(mag, _EPS), 1.0)
        gain = np.clip(gain, 0.0, 1.0)
        return 20.0 * np.log10(np.maximum(gain, 1e-6))

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        spec = _spectrogram(buf, _NFFT, _HOP)
        n_frames, n_bins, n_ch = spec.shape
        mag = np.abs(spec)

        algorithm = str(self.params.get("Separation Algorithm"))
        if algorithm == "Channel Independent":
            floor = np.zeros_like(mag)
            for c in range(n_ch):
                floor[:, :, c] = self._floor_estimate(mag[:, :, c])
        else:
            shared = self._floor_estimate(mag.mean(axis=2))
            floor = np.repeat(shared[:, :, None], n_ch, axis=2)

        if algorithm == "Advanced Joint" and n_ch >= 2:
            self._coherence = self._coherence_estimate(spec)
        else:
            self._coherence = None

        gain_db = np.zeros((n_frames, n_bins, n_ch), dtype=np.float64)
        for c in range(n_ch):
            gain_db[:, :, c] = self._gain_curve(mag[:, :, c], floor[:, :, c])

        kernel = np.array([0.25, 0.5, 0.25], dtype=np.float64)
        padded = np.pad(gain_db, ((0, 0), (1, 1), (0, 0)), mode="edge")
        gain_db = np.apply_along_axis(
            lambda v: np.convolve(v, kernel, mode="valid"), 1, padded
        )
        alpha = math.exp(-_HOP / (0.08 * self.sample_rate))
        for i in range(1, n_frames):
            gain_db[i] = alpha * gain_db[i - 1] + (1.0 - alpha) * gain_db[i]

        gain = np.power(10.0, gain_db / 20.0)
        out = _resynthesize(spec * gain, _NFFT, _HOP, buf.shape[0])
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
