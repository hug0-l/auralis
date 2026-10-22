"""
aurcore.dsp.spatial — the spatial suppressor.

The idea
--------
A wanted source and an unwanted source that live in **different places** in
the stereo image can be separated by their *relation* between the channels,
and not by their spectrum.  A source that sits in the middle of the image is
the same signal in both channels; a source that sits on the side is a
different signal in each channel.  The quantity that tells the two apart is
the **inter channel coherence**: the share of the power of one channel that
the other channel explains.

The de-bleed module already computes that quantity — it calls it the *share
of the target's power the reference channel explains* — and it uses it to
take a bleed out of a target.  The same quantity, pointed the other way,
describes the content the channels do **not** share: that is the content
that lives in one channel only, or in the anti phase relation between the
channels.  This module is the suppressor that removes it.

The rule
--------
The suppressor is a **suppressor**: it multiplies the spectrum by a real gain
between zero and one and never by anything else.  It therefore cannot invent
energy, cannot re-synthesise a source it does not have, and cannot add the
roughness a re-synthesis adds.  What it removes, it removes *by a level*.

Three pieces do the work, and each of them can be measured on its own:

* **the estimator** keeps three running sums per bin — the cross spectrum of
  the channels and the power of each of them — and turns them into the share
  of the power that the channels agree on.  The sums are weighted by an
  exponential window, so the estimate follows the material without tracking
  every frame of it, and the estimate is taken *per frame* of the analysis,
  never per block of the host, so the answer does not depend on the size of
  the block.
* **the masking law** says which bins are unwanted.  A bin is unwanted when
  the channels do not agree on it; the share that has to be agreed on and
  the width of the soft edge around the decision are the two knobs.
* **the gain application** smooths the mask over time: the mask is the share
  of the bin that is removed, it closes at once (the content that does not
  belong to the image has to go) and it opens over a short time (the wanted
  content must not be damaged by a decision that moves for a moment).

Two properties are part of the contract:

* a **mono** signal has no spatial relation at all, so the suppressor leaves
  it alone (it only delays it by the latency it declares);
* the delay the signal shows is the latency the suppressor publishes, at
  every block size of the host.
"""
from __future__ import annotations

import math

import numpy as np

from .stft import StreamingSTFT

__all__ = ["SpatialSuppressor"]

#: The smallest power a bin may carry before its estimate is used.
_EPS = 1e-20

#: The share of the power of a bin that the two channels have to agree on
#: before the bin counts as common content and is kept.
DEFAULT_THRESHOLD = 0.9

#: The rate of a wrong decision the estimator is allowed to make while it is
#: still gathering frames.  The share of two channels that hold nothing in
#: common falls on its own as the estimate gathers frames, so the level the
#: decision needs is raised while the estimate is young: a bin is only kept
#: when the channels agree on it **and** the estimate is old enough to be
#: trusted.
FALSE_POSITIVE_RATE = 1e-4

#: The width of the soft edge around the decision, at the narrowest and at
#: the widest setting of the smoothing.  The edge is narrow on purpose: the
#: share of two channels that hold nothing in common stays well below the
#: decision, and a wide edge would let that share through.
MIN_EDGE = 0.01
MAX_EDGE = 0.09

#: Release of the applied mask, in seconds.  The mask is the share of the
#: bin that is removed: it closes at once (the content that does not belong
#: to the image has to go) and opens over this time (the wanted content must
#: not be damaged by a decision that moves for a moment).
RELEASE_S = 0.020

#: The level the decision needs, divided by the number of frames the
#: estimate has gathered.  It is the share of two channels that hold nothing
#: in common, raised to the rate of a wrong decision the estimator is
#: allowed to make.
_DECISION_C = 0.5 * math.log(1.0 / FALSE_POSITIVE_RATE)


class SpatialSuppressor:
    """A suppressor that removes the content the channels do not share.

    Parameters
    ----------
    sample_rate:
        The rate of the signal, in hertz.
    channels:
        The number of channels the suppressor is set up for.
    n_fft, hop:
        The analysis window and the hop between frames, in samples.
    tau:
        The time constant of the running estimate, in seconds.  It is how
        long the estimate remembers: a short one follows the material
        closely, a long one holds the relation steady.
    threshold:
        The share of a bin that the channels have to agree on before the bin
        counts as common content.
    edge:
        The width of the soft edge around the decision.
    strength:
        How much of the unwanted content is removed: 0 leaves the signal
        alone, 1 removes all of it.

    Notes
    -----
    The suppressor works on the **whole** image: the mask it computes is the
    same for every channel, so the channels stay in step with one another
    and the content that lives in one channel is removed from all of them.
    That is what makes it a suppressor of an image and not of a single
    channel.

    The share that is used is the share that the channels agree on **in
    phase**.  The magnitude coherence of the de-bleed would call a content
    that is mirrored between the channels (the anti phase relation) common,
    and the anti phase relation is spatial difference, not common content.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        n_fft: int = 512,
        hop: int = 128,
        tau: float = 0.08,
        threshold: float = DEFAULT_THRESHOLD,
        edge: float = 0.05,
        strength: float = 1.0,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self.tau = float(tau)
        self.threshold = float(threshold)
        self.edge = float(edge)
        self.strength = float(strength)
        self._stream = StreamingSTFT(self.n_fft, self.hop, window="hann", center=True)
        self._num: np.ndarray | None = None   # the running cross spectrum
        self._den: np.ndarray | None = None    # the running power of the reference
        self._den0: np.ndarray | None = None   # the running power of the first channel
        self._mask: np.ndarray | None = None   # the applied mask
        self._frames = 0.0
        self._decay = self._decay_factor()
        self._delay = np.zeros((self.latency_samples, max(1, self.channels)), dtype=np.float64)

    # -- the delay ------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """The delay the suppressor puts into the signal, in samples."""
        return int(self._stream.latency_samples)

    def _decay_factor(self) -> float:
        """The weight the running estimate loses from frame to frame."""
        return math.exp(-self.hop / max(1e-6, self.tau * float(self.sample_rate)))

    def reset(self) -> None:
        """Forget the estimate, the mask and the delay line."""
        self._stream.reset()
        self._num = None
        self._den = None
        self._den0 = None
        self._mask = None
        self._frames = 0.0
        self._delay = np.zeros((self.latency_samples, max(1, self.channels)), dtype=np.float64)

    # -- the delay line -------------------------------------------------------
    def _delay_line(self, block: np.ndarray) -> np.ndarray:
        """Return the block delayed by the latency of the suppressor."""
        if self._delay.shape[1] != block.shape[1]:
            # the host changed the number of channels: the delay line follows
            # it, the way the rest of the family adapts.
            self._delay = np.zeros((0, block.shape[1]), dtype=np.float64)
        buf = np.concatenate([self._delay, block], axis=0)
        out = buf[: block.shape[0]]
        self._delay = buf[block.shape[0] :]
        if out.shape[0] < block.shape[0]:
            out = np.concatenate(
                [out, np.zeros((block.shape[0] - out.shape[0], out.shape[1]))], axis=0
            )
        return out

    # -- the estimator --------------------------------------------------------
    def _ensure_state(self, n_bins: int) -> None:
        """Make the running estimate ready for ``n_bins`` bins."""
        if self._num is not None and self._num.shape[0] == n_bins:
            return
        self._num = np.zeros(n_bins, dtype=np.complex128)
        self._den = np.zeros(n_bins, dtype=np.float64)
        self._den0 = np.zeros(n_bins, dtype=np.float64)
        self._mask = np.zeros(n_bins, dtype=np.float64)
        self._frames = 0.0

    def _estimate(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Fold one frame into the estimate and return the share that agrees.

        The share is the part of the power of the first channel that the
        other channel explains **in phase**.  A content that is mirrored
        between the channels is not common content: it is spatial
        difference, and it has to go.

        The share of two channels that hold nothing in common is not zero but
        the chance level of the estimate, and that level falls on its own as
        the estimate gathers frames: the share of the channels that have
        nothing in common is the share of the chance.
        """
        self._num *= self._decay
        self._num += left * np.conj(right)
        self._den *= self._decay
        self._den += np.abs(right) ** 2
        self._den0 *= self._decay
        self._den0 += np.abs(left) ** 2
        self._frames = self._decay * self._frames + 1.0
        power = np.maximum(self._den * self._den0, _EPS)
        return np.maximum(self._num.real, 0.0) ** 2 / power

    def _mask_gain(self, common: np.ndarray) -> np.ndarray:
        """Turn the share that agrees into the gain of the bin."""
        # The level the decision needs while the estimate is young: the share
        # of two channels that hold nothing in common is the chance level of
        # the estimate, and the chance level falls as the estimate gathers
        # frames.  A bin is therefore only kept when the channels agree on
        # it and the estimate is old enough for the agreement to mean
        # something.
        needed = _DECISION_C / max(1.0, self._frames)
        level = min(1.0, max(self.threshold, needed))
        lower = max(0.0, level - 0.5 * self.edge)
        upper = min(1.0, level + 0.5 * self.edge)
        if upper <= lower:
            keep = (common >= upper).astype(np.float64)
        else:
            keep = np.clip((common - lower) / (upper - lower), 0.0, 1.0)
        # the gain of a suppressor is a power ratio, so the amplitude that
        # is applied is its square root.
        return np.sqrt(keep)

    def _apply(self, common: np.ndarray) -> np.ndarray:
        """Smooth the mask over time and return the gain of the bin.

        The mask is the share of the bin that is removed.  It starts at zero:
        the wanted content passes through untouched until the estimate says
        that it does not belong to the image.
        """
        fs = float(self.sample_rate)
        release = 1.0 - math.exp(-self.hop / max(1e-6, RELEASE_S * fs))
        decision = self.strength * (1.0 - self._mask_gain(common))
        # the content that does not belong to the image has to go at once: a
        # mask that closed slowly would let it through.  The wanted content
        # comes back slowly: a decision that moves for a moment must not
        # damage it.
        opened = np.where(
            decision > self._mask,
            decision,
            self._mask + release * (decision - self._mask),
        )
        self._mask = opened
        np.clip(self._mask, 0.0, 1.0, out=self._mask)
        return 1.0 - self._mask

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove the content the channels do not share from a block.

        Parameters
        ----------
        x:
            The block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The same number of samples, delayed by :attr:`latency_samples`.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        if arr.shape[1] < 2:
            # a mono signal has no spatial relation: the suppressor leaves it
            # alone and only delays it by the latency it declares.
            return self._delay_line(arr)
        if self._stream._channels and int(arr.shape[1]) != int(self._stream._channels):
            # the host changed the number of channels: the analysis starts
            # over with the new arrangement.
            self._stream = StreamingSTFT(self.n_fft, self.hop, window="hann", center=True)
            self._ensure_state(self.n_fft // 2 + 1)
            self._frames = 0.0
        out = self._stream.process(arr, self._modify)
        return np.nan_to_num(np.asarray(out, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)

    def _modify(self, mag: np.ndarray, phase: np.ndarray):
        """Edit the spectra of the new frames (the callback of the stream)."""
        if mag.ndim != 3 or mag.shape[0] < 2:
            return mag, phase
        spec = mag * np.exp(1j * phase)
        left = spec[0]
        n_bins = left.shape[0]
        self._ensure_state(n_bins)
        self._decay = self._decay_factor()
        n_frames = left.shape[1]
        gain = np.ones((n_bins, n_frames), dtype=np.float64)
        for index in range(n_frames):
            common = None
            for other in range(1, spec.shape[0]):
                share = self._estimate(left[:, index], spec[other][:, index])
                common = share if common is None else np.minimum(common, share)
            if common is None:
                continue
            gain[:, index] = self._apply(common)
        return mag * gain[None, :, :], phase
