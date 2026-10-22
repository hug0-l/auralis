"""
aurcore.modules.de_bleed — De-Bleed (``DeBleed``).

Removes sound that leaks from one source into another: a talkback microphone
picking up the presenter, a room microphone hearing the instrument, a
headset leaking into the boom track.

The first channel is the target that has to be cleaned, the second channel is
the reference that carries the bleeding source.  The bleed is estimated in
the spectral domain: the transfer from the reference to the target is
estimated per bin and multiplied with the reference spectrum, and that
estimate is subtracted from the target.

Three pieces do the work, and each of them can be measured on its own:

* **the estimator** says how much of the reference is in the target, bin by
  bin, and how sure that answer is.  :func:`learn` captures the transfer
  from an excerpt in which the bleed dominates; that transfer is then used
  as it was learned, which is stable.  Without a learned profile the
  estimate is built while the audio runs: every frame adds to three sums,
  and the sums are weighted by an exponential window whose time constant is
  ``_ESTIMATE_TAU``.  The certainty of the answer is the share of the
  target's power that the reference explains — the square of the coherence
  between the two channels.  That share falls to the chance level on its
  own when the two channels have nothing in common, so a reference that
  carries no bleed never opens the mask.
* **the masking law** says where the estimate may be applied.  A bin
  belongs to the bleed when the reference explains more of the target than
  the programme does.  ``Strength`` sets the share the reference has to
  explain, ``Artifact Smoothing`` the width of the soft edge, so that a bin
  that sits on the border keeps more of the original target.
* **the gain application** smooths the mask over time, with a fast attack
  and a slow release, and limits how far the mask may move in one frame.
  A balance that changes therefore does not make the decision chatter, and
  the estimate is never taken out of the target in one step.
"""
from __future__ import annotations

import io
import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

try:  # pragma: no cover - the shared DSP package is optional
    from ..dsp.stft import istft as _sp_istft, stft as _sp_stft
except Exception:  # pragma: no cover
    _sp_stft = _sp_istft = None


__all__ = ["DeBleed"]

_EPS = 1e-12

#: Analysis window.
_NFFT = 2048

#: Hop between frames.
_HOP = 512

#: Time constant of the running estimate, in seconds.
_ESTIMATE_TAU = 0.25

#: The share of the target the reference has to explain before the mask may
#: open at all, at the strongest setting of ``Strength``.
_MIN_SHARE = 0.25

#: The width of the soft edge of the mask, at the lowest and at the highest
#: setting of ``Artifact Smoothing``.
_MIN_EDGE = 0.05
_MAX_EDGE = 0.40

#: Attack and release of the applied mask, in seconds.  The mask opens fast
#: (the bleed has to go away) and closes slowly (the programme must not be
#: damaged by a balance that moves for a moment).
_MASK_ATTACK_S = 0.010
_MASK_RELEASE_S = 0.150

#: The largest change the applied mask may make in one frame.
_MAX_MASK_STEP = 0.5

#: The smallest reference power a bin may carry before the estimate of that
#: bin is used at all.
_DEN_FLOOR = 1e-20


# ---------------------------------------------------------------------------
# spectrogram helpers
# ---------------------------------------------------------------------------

def _shared_stft_usable() -> bool:
    """Return True when the shared DSP package can do a round trip."""
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
    """Return a complex spectrogram shaped ``(frames, bins, channels)``."""
    if _USE_SHARED_STFT:  # pragma: no cover - depends on the shared package
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
    """Inverse of :func:`_spectrogram` (overlap-add, exact normalisation)."""
    if _USE_SHARED_STFT:  # pragma: no cover - depends on the shared package
        try:
            # The shared inverse transform reads (bins, frames); the
            # spectrogram of this module is (frames, bins, channels).
            if spec.shape[2] == 1:
                shaped = np.transpose(spec[:, :, 0])
            else:
                shaped = np.transpose(spec, (2, 1, 0))
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


def _soft_edge(share: np.ndarray, lower: float, upper: float) -> np.ndarray:
    """A ramp that rises from zero to one between two shares.

    ``share`` is the share of the target the reference explains.  The result
    is zero below ``lower``, one above ``upper``, and a straight line in
    between: the decision is at its softest in the middle of the edge.
    """
    values = np.asarray(share, dtype=np.float64)
    if upper <= lower:
        return (values >= upper).astype(np.float64)
    return np.clip((values - float(lower)) / float(upper - lower), 0.0, 1.0)


# ---------------------------------------------------------------------------
# module
# ---------------------------------------------------------------------------
@register
class DeBleed(Module):
    """De-Bleed — cross channel bleed suppression."""

    code = "AUR_DEBLEED"
    display_name = "De-Bleed"
    category = "Repair"
    edition = "standard"
    element = "DeBleed"
    supports_learn = True
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._transfer: np.ndarray | None = None
        self._bleed_profile: np.ndarray | None = None
        # the seed of the estimate: the sums of the learning excerpt
        self._seed: tuple[np.ndarray, np.ndarray, np.ndarray, float] | None = None
        # the running estimate and the mask it drives
        self._num: np.ndarray | None = None
        self._den: np.ndarray | None = None
        self._den_target: np.ndarray | None = None
        self._live: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._frames = 0.0

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
                help=(
                    "How much of the target the estimated bleed is taken out of. "
                    "At 0 nothing is touched, at 1 the bleed is taken out of every "
                    "bin the reference explains."
                ),
                aliases=("Strength",),
            ),
            ParamDesc(
                name="Artifact Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help=(
                    "How much of the original target is protected from artefacts. "
                    "A high value widens the soft edge of the decision and slows "
                    "the mask down."
                ),
            ),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the learned bleed profile and the running estimate."""
        self._transfer = None
        self._bleed_profile = None
        self._seed = None
        self._num = None
        self._den = None
        self._den_target = None
        self._live = None
        self._mask = None
        self._frames = 0.0

    def _state_blob(self) -> bytes:
        """Pack the learned bleed profile."""
        buffer = io.BytesIO()
        seed = self._seed if self._seed is not None else None
        payload = {
            "transfer": self._transfer if self._transfer is not None else np.zeros(0, complex),
            "profile": self._bleed_profile if self._bleed_profile is not None else np.zeros(0),
            "num": seed[0] if seed is not None else np.zeros(0, complex),
            "den": seed[1] if seed is not None else np.zeros(0),
            "den_target": seed[2] if seed is not None else np.zeros(0),
            "frames": np.array([seed[3] if seed is not None else 0.0]),
        }
        np.savez(buffer, **payload)
        return buffer.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a bleed profile packed by :meth:`_state_blob`."""
        try:
            with np.load(io.BytesIO(bytes(data))) as archive:
                transfer = archive["transfer"]
                profile = archive["profile"]
                num = archive["num"] if "num" in archive else None
                den = archive["den"] if "den" in archive else None
                den_target = archive["den_target"] if "den_target" in archive else None
                frames = archive["frames"] if "frames" in archive else None
        except Exception:
            return
        if transfer.size:
            self._transfer = np.asarray(transfer, dtype=np.complex128)
        if profile.size:
            self._bleed_profile = np.asarray(profile, dtype=np.float64)
        if num is not None and num.size and den is not None and den_target is not None:
            count = float(frames[0]) if frames is not None and frames.size else 0.0
            self._seed = (
                np.asarray(num, dtype=np.complex128),
                np.asarray(den, dtype=np.float64),
                np.asarray(den_target, dtype=np.float64),
                count,
            )

    # -- learning ------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Capture the bleed profile from ``x``.

        The first channel is the target, the second the reference that
        carries the bleeding source.  The per-bin transfer from the reference
        to the target is stored, and with it the sums of the excerpt: the
        learned profile is the seed of the estimate, so the module knows the
        transfer from the first frame and follows the material afterwards.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return
        spec = _spectrogram(buf, _NFFT, _HOP)
        n_frames, n_bins, n_ch = spec.shape
        if n_ch < 2:
            # a mono excerpt can still describe the bleed spectrum
            mag = np.abs(spec[:, :, 0])
            self._bleed_profile = np.percentile(mag, 10.0, axis=0)
            return
        ref = spec[:, :, 1]
        tgt = spec[:, :, 0]
        num = np.sum(tgt * np.conj(ref), axis=0)
        den = np.sum(np.abs(ref) ** 2, axis=0)
        den_target = np.sum(np.abs(tgt) ** 2, axis=0)
        transfer = np.where(den > _DEN_FLOOR, num / np.maximum(den, _DEN_FLOOR), 0.0)
        self._transfer = np.asarray(transfer, dtype=np.complex128)
        self._seed = (
            np.asarray(num, dtype=np.complex128),
            np.asarray(den, dtype=np.float64),
            np.asarray(den_target, dtype=np.float64),
            float(n_frames),
        )
        self._bleed_profile = np.percentile(np.abs(ref), 10.0, axis=0)

    # -- the estimator --------------------------------------------------------
    def _ensure_state(self, n_bins: int) -> None:
        """Make the running estimate ready for ``n_bins`` bins."""
        if self._num is not None and self._num.shape[0] == n_bins:
            return
        self._num = np.zeros(n_bins, dtype=np.complex128)
        self._den = np.zeros(n_bins, dtype=np.float64)
        self._den_target = np.zeros(n_bins, dtype=np.float64)
        self._live = np.zeros(n_bins, dtype=np.complex128)
        self._mask = np.zeros(n_bins, dtype=np.float64)
        self._frames = 0.0
        # the learned profile is the seed of the estimate
        seed = self._seed
        if seed is not None and seed[0].shape[0] == n_bins:
            self._num = seed[0].copy()
            self._den = seed[1].copy()
            self._den_target = seed[2].copy()
            self._frames = float(seed[3])
        if self._transfer is not None and self._transfer.shape[0] == n_bins:
            self._live = self._transfer.copy()

    def _mask_curve(
        self,
        ref: np.ndarray,
        tgt: np.ndarray,
        n_bins: int,
        threshold: float,
        edge: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run the estimator and the mask over the frames of the block.

        Returns the mask (one row per frame) and the estimate of the bleed
        (one row per frame).  The state of the estimate and of the mask is
        kept on the module, so the answer of a block does not depend on the
        block that came before it.
        """
        fs = float(self.sample_rate)
        decay = math.exp(-_HOP / max(1e-6, _ESTIMATE_TAU * fs))
        alpha_attack = 1.0 - math.exp(-_HOP / max(1e-6, _MASK_ATTACK_S * fs))
        alpha_release = 1.0 - math.exp(-_HOP / max(1e-6, _MASK_RELEASE_S * fs))
        # the mask may not move further than this in one frame
        alpha_attack = min(alpha_attack, _MAX_MASK_STEP)
        alpha_release = min(alpha_release, _MAX_MASK_STEP)
        self._ensure_state(n_bins)
        n_frames = ref.shape[0]
        mask = np.empty((n_frames, n_bins), dtype=np.float64)
        bleed = np.empty((n_frames, n_bins), dtype=np.complex128)
        # the share of the target the reference has to explain, and the width
        # of the soft edge around it
        lower = threshold - 0.5 * edge
        upper = threshold + 0.5 * edge
        for i in range(n_frames):
            r = ref[i]
            t = tgt[i]
            # the estimator: three weighted sums over the frames
            self._num *= decay
            self._num += t * np.conj(r)
            self._den *= decay
            self._den += np.abs(r) ** 2
            self._den_target *= decay
            self._den_target += np.abs(t) ** 2
            self._frames = decay * self._frames + 1.0
            # the transfer of a bin in which the reference is quiet keeps the
            # value it had: a quiet reference never invents a transfer.
            np.divide(self._num, self._den, out=self._live, where=self._den > _DEN_FLOOR)
            bleed[i] = self._live * r
            # the masking law: the share of the target the reference
            # explains, above the chance level of the estimate
            share = np.abs(self._num) ** 2 / np.maximum(self._den * self._den_target, _EPS)
            chance = 1.0 / max(1.0, self._frames)
            raw = np.clip((share - chance - lower) / (upper - lower), 0.0, 1.0)
            # the gain application: a fast attack, a slow release
            alpha = np.where(raw > self._mask, alpha_attack, alpha_release)
            self._mask += alpha * (raw - self._mask)
            np.clip(self._mask, 0.0, 1.0, out=self._mask)
            mask[i] = self._mask
        return mask, bleed

    # -- processing ----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove bleed from a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        strength = float(self.params.get("Reduction"))
        smoothing = float(self.params.get("Artifact Smoothing"))
        spec = _spectrogram(buf, _NFFT, _HOP)
        n_frames, n_bins, n_ch = spec.shape
        out_spec = spec.copy()

        if n_ch >= 2:
            ref = spec[:, :, 1]
            tgt = spec[:, :, 0]
            # the share of the target the reference has to explain, and the
            # width of the soft edge around it
            threshold = 1.0 - strength * (1.0 - _MIN_SHARE)
            edge = _MIN_EDGE + (_MAX_EDGE - _MIN_EDGE) * smoothing
            mask, bleed = self._mask_curve(ref, tgt, n_bins, threshold, edge)
            out_spec[:, :, 0] = tgt - mask * bleed
        elif self._bleed_profile is not None and self._bleed_profile.size == n_bins:
            # mono: the learned spectrum is the bleed, and it is taken out
            # of the bins it explains
            mag = np.abs(spec[:, :, 0])
            floor = self._bleed_profile[None, :]
            threshold = 1.0 - strength * (1.0 - _MIN_SHARE)
            edge = _MIN_EDGE + (_MAX_EDGE - _MIN_EDGE) * smoothing
            share = np.clip(floor ** 2 / np.maximum(mag ** 2, _EPS), 0.0, 1.0)
            raw = np.clip((share - (threshold - 0.5 * edge)) / edge, 0.0, 1.0)
            self._ensure_state(n_bins)
            fs = float(self.sample_rate)
            alpha_attack = min(1.0 - math.exp(-_HOP / max(1e-6, _MASK_ATTACK_S * fs)),
                               _MAX_MASK_STEP)
            alpha_release = min(1.0 - math.exp(-_HOP / max(1e-6, _MASK_RELEASE_S * fs)),
                                _MAX_MASK_STEP)
            mask = np.empty((n_frames, n_bins), dtype=np.float64)
            for i in range(n_frames):
                target = raw[i]
                alpha = np.where(target > self._mask, alpha_attack, alpha_release)
                self._mask += alpha * (target - self._mask)
                np.clip(self._mask, 0.0, 1.0, out=self._mask)
                mask[i] = self._mask
            clean = mag - mask * floor
            gain = np.where(mag > _EPS, clean / np.maximum(mag, _EPS), 1.0)
            out_spec[:, :, 0] = spec[:, :, 0] * np.clip(gain, 0.0, 1.0)

        out = _resynthesize(out_spec, _NFFT, _HOP, buf.shape[0])
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
