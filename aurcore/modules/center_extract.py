"""
aurcore.modules.center_extract — centre channel extraction.

The module separates the *centred* part of a stereo image from the rest of
the image and hands the centred part back in both channels.

Two estimators are available:

``True Phase``
    A phase coherent estimator.  The inter-channel phase relationship and
    the inter-channel coherence are measured per spectral bin from the
    running cross spectrum, the right channel is phase aligned with the left
    one and the aligned mid signal is gated by the coherence.  Bins whose two
    channels do not share content (a hard panned source) are rejected, so a
    panned source disappears while a centred one survives untouched.

``Pseudo Pan``
    A panning based estimator.  The pan position is estimated from the
    magnitude balance of the two channels only (the classic amplitude panning
    law) and the mid signal is weighted by the resulting "centre-ness".
    It is cheaper and more forgiving, but it cannot tell a centred source
    from uncorrelated content that happens to be level balanced.

Both estimators work on the mid/side matrix engine of
:mod:`aurcore.modules.mixing`; the module runs a short time Fourier
transform with a constant latency of ``n_fft`` samples.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module
from .mixing import ChannelOps, SpectralStream

__all__ = ["CenterExtract"]


def _smooth_freq(mask: np.ndarray, width: int) -> np.ndarray:
    """Moving average along the last axis, with the edges replicated."""
    if width <= 1:
        return mask
    k = np.ones(int(width), dtype=np.float64) / float(width)
    pad = int(width) // 2
    pad_width = [(0, 0)] * (mask.ndim - 1) + [(pad, pad)]
    padded = np.pad(mask, pad_width, mode="edge")
    out = np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), -1, padded)
    return out


class _Averager:
    """Exponential moving average with a bias correction.

    The raw average of an exponential filter is biased towards zero for the
    first updates, which would make a ratio of two averages (the inter
    channel coherence) wrong until the filter has settled.  Dividing by
    ``1 - (1 - beta) ** n`` removes that bias, so a ratio of two corrected
    averages is correct from the very first block on.
    """

    def __init__(self, beta: float = 0.25):
        self.beta = float(beta)
        self._value: np.ndarray | None = None
        self._count = 0

    def update(self, x: np.ndarray) -> np.ndarray:
        """Fold a new observation into the average and return the result."""
        data = np.asarray(x)
        if self._value is None or self._value.shape != data.shape:
            self._value = np.zeros(data.shape,
                                   dtype=complex if np.iscomplexobj(data) else np.float64)
            self._count = 0
        self._value = (1.0 - self.beta) * self._value + self.beta * data
        self._count += 1
        return self.corrected()

    def corrected(self) -> np.ndarray:
        """The bias corrected average."""
        if self._value is None:
            return np.zeros(0)
        scale = 1.0 - (1.0 - self.beta) ** self._count
        return self._value / max(scale, 1e-12)

    def reset(self) -> None:
        """Forget everything."""
        self._value = None
        self._count = 0


@register
class CenterExtract(Module):
    """Center Extract — pull the centred part out of a stereo image.

    Parameters
    ----------
    Reduction Strength:
        How strongly the non centred content is removed.  0 leaves the
        signal untouched, 1 removes everything that is not centred.
    Artifact Smoothing:
        Smoothing applied to the per bin masks.  Higher values trade mask
        accuracy for fewer musical noise artefacts.
    Dry Mix:
        Percentage of the untouched (dry) signal blended back into the
        processed signal.
    """

    code = "AUR_CENTEREXTRACT"
    display_name = "Center Extract"
    category = "Repair"
    edition = "advanced"
    element = "ChannelOperations"
    analysis = False
    supports_learn = False
    ml = False

    N_FFT = 1024
    HOP = 256

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._ops = ChannelOps(sample_rate, channels)
        self._stream = SpectralStream(self.N_FFT, self.HOP, max(channels, 1))
        self._cross = _Averager(0.25)
        self._power_l = _Averager(0.25)
        self._power_r = _Averager(0.25)
        self._mask = _Averager(0.75)
        self._dry = np.zeros((self.N_FFT, max(channels, 1)), dtype=np.float64)
        self._wet = np.zeros((0, max(channels, 1)), dtype=np.float64)

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Algorithm",
                type="enum",
                default="True Phase",
                minimum=0,
                maximum=1,
                choices=("True Phase", "Pseudo Pan"),
                group="Algorithm",
                help="How the centred part of the image is estimated.",
            ),
            desc(
                name="Reduction",
                type="float",
                default=1.0,
                minimum=0.0,
                maximum=1.0,
                group="Reduction",
                help="How much of the non centred content is removed.",
                aliases=("Reduction Strength",),
            ),
            desc(
                name="Artifact Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help="Smoothing of the per bin masks; higher values reduce musical noise.",
            ),
            desc(
                name="Dry Mix",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=100.0,
                unit="%",
                group="Mix",
                help="Percentage of the untouched signal blended into the result.",
            ),
        ]

    # -- helpers --------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Latency introduced by the spectral processing, in samples."""
        return self.N_FFT

    def reset(self) -> None:
        """Clear the analysis state."""
        self._stream.reset()
        self._cross.reset()
        self._power_l.reset()
        self._power_r.reset()
        self._mask.reset()
        self._dry = np.zeros((self.N_FFT, max(self.channels, 1)), dtype=np.float64)
        self._wet = np.zeros((0, max(self.channels, 1)), dtype=np.float64)

    def _dry_delay(self, block: np.ndarray) -> np.ndarray:
        """Return ``block`` delayed by ``N_FFT`` samples."""
        if self._dry.shape[1] != block.shape[1]:
            self._dry = np.zeros((self.N_FFT, block.shape[1]), dtype=np.float64)
        buf = np.concatenate([self._dry, block], axis=0)
        out = buf[: block.shape[0]]
        self._dry = buf[-self.N_FFT:]
        return out

    def _update_coherence(self, left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray,
                                                                              np.ndarray]:
        """Update the running cross spectrum with one frame.

        The average is taken over **one frame** of the analysis, never over
        a whole block: an average that is taken once per block depends on
        the size of the block the host hands over, and the mask that is
        built from it is the mask the module applies to the material.  The
        frames of the analysis sit on a fixed grid, so the average is the
        same in every block size.
        """
        cross = self._cross.update(left * np.conj(right))
        power_l = self._power_l.update(np.abs(left) ** 2)
        power_r = self._power_r.update(np.abs(right) ** 2)
        magnitude = np.abs(cross)
        coherence = magnitude / np.sqrt(power_l * power_r + 1e-24)
        coherence = np.clip(coherence, 0.0, 1.0)
        phase = np.angle(cross)
        return phase, coherence

    def _mask_width(self) -> int:
        """Frequency smoothing width derived from *Artifact Smoothing*."""
        smoothing = float(self.params.get("Artifact Smoothing"))
        return int(1 + 2 * int(round(smoothing * 3.0)))

    def _centre_true_phase(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Phase coherent centre estimate (True Phase)."""
        n_frames = left.shape[0]
        phases = np.zeros((n_frames, left.shape[1]), dtype=np.float64)
        coherences = np.zeros((n_frames, left.shape[1]), dtype=np.float64)
        for index in range(n_frames):
            phase, coherence = self._update_coherence(left[index], right[index])
            phases[index] = phase
            coherences[index] = coherence
        mask = _smooth_freq(coherences, self._mask_width())
        aligned = right * np.exp(1j * phases)
        mid = 0.5 * (left + aligned)
        return mid * mask

    def _centre_pseudo_pan(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """Panning based centre estimate (Pseudo Pan)."""
        n_frames = left.shape[0]
        smoothing = float(self.params.get("Artifact Smoothing"))
        self._mask.beta = 1.0 - 0.9 * smoothing
        masks = np.zeros((n_frames, left.shape[1]), dtype=np.float64)
        for index in range(n_frames):
            mag_l = np.abs(left[index])
            mag_r = np.abs(right[index])
            pan = (mag_r - mag_l) / (mag_l + mag_r + 1e-12)
            masks[index] = self._mask.update(1.0 - np.abs(pan))
        smoothed = _smooth_freq(masks, self._mask_width())
        mid = 0.5 * (left + right)
        return mid * smoothed

    def _take_wet(self, count: int) -> np.ndarray:
        """Take ``count`` samples of the wet signal out of the line.

        The stream hands its samples out in bursts of one hop, so the
        module keeps them in a line of its own and takes them out at the
        rate the host asks for.  The line is the delay of the module: the
        samples come out of it in the order they went in, so the wet
        signal is the input delayed by the look ahead of the analysis, at
        every block size.
        """
        take = min(int(count), int(self._wet.shape[0]))
        out = self._wet[:take]
        self._wet = self._wet[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, out.shape[1]), dtype=np.float64)], axis=0
            )
        return out

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Extract the centred part of a stereo block."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        if a.shape[1] < 2:
            return a

        dry = self._dry_delay(a)
        if self._stream.channels != a.shape[1]:
            # the caller changed the channel count: restart the analysis
            self._stream = SpectralStream(self.N_FFT, self.HOP, a.shape[1])
            self._cross.reset()
            self._power_l.reset()
            self._power_r.reset()
            self._mask.reset()
            self._wet = np.zeros((0, a.shape[1]), dtype=np.float64)
        spec, first_position = self._stream.analyze(a)

        if spec is not None:
            left = spec[:, :, 0]
            right = spec[:, :, 1]
            algorithm = self.params.get("Algorithm")
            if algorithm == "Pseudo Pan":
                centre = self._centre_pseudo_pan(left, right)
            else:
                centre = self._centre_true_phase(left, right)

            strength = float(self.params.get("Reduction"))
            out = np.empty_like(spec)
            out[:, :, 0] = (1.0 - strength) * left + strength * centre
            out[:, :, 1] = (1.0 - strength) * right + strength * centre

            # The stream gives its samples out in bursts of one hop per
            # frame, so the module asks for the burst and keeps it in a
            # line of its own.  A module that took the samples out only
            # when a frame was ready would leave the stream with more and
            # more of the signal in its hands, and its own output would
            # fall further and further behind the signal that went in: at
            # 64 samples per block the output of the module was the tone
            # again and again, long after the tone had stopped.
            burst = int(spec.shape[0]) * self.HOP
            chunk = self._stream.synthesize(out, first_position, burst)
            self._wet = np.concatenate([self._wet, chunk], axis=0)

        wet = self._take_wet(a.shape[0])
        dry_mix = float(self.params.get("Dry Mix")) / 100.0
        if dry_mix > 0.0:
            wet = (1.0 - dry_mix) * wet + dry_mix * dry
        return wet
