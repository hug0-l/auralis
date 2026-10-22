"""
aurcore.modules.time_pitch — time stretching and pitch shifting.

The module is built on the phase vocoder of the DSP layer,
:class:`aurcore.dsp.phase_vocoder.PhaseVocoder`.  The vocoder analyses the
signal in overlapping windows, tracks the phase of every bin and rebuilds the
signal with a different synthesis hop, which changes the duration without
touching the pitch.  Pitch shifting is done by resampling the signal with a
band limited sinc interpolator and compensating the duration with the
vocoder, so the pitch is exact.

There is **one** phase vocoder in the project: the module layer does not
carry a second copy of the algorithm, it maps its own parameters (the window
length in milliseconds, the phase coherence and the transient sensitivity)
onto the shared one.  A fix that reaches the DSP layer therefore reaches the
three modules a user reaches through the interface.

Two refinements are available:

* identity phase locking keeps the phase relation inside a spectral peak, which
  keeps the sound stable and is what the phase coherence control blends in;
* the transient detector resets the phase at attacks so that percussive
  material is not smeared.

When formants are preserved the vocal character of the source is kept: the
long term spectral envelope of the input is measured, the shift of that
envelope is calculated and a compensating curve is applied after the pitch
shift.

The resampling step
-------------------
The pitch shift is done by resampling, and the resampler is a **streaming
filter**: it keeps the history the kernel needs and holds its output back by
the look ahead the kernel needs, so that every output is computed from the
whole kernel.  A block that is resampled on its own is cut in two instead:
the first and the last samples of every block came from a kernel whose half
was missing.  The read position is counted from the start of the stream, so
one block and many blocks give the same answer.

The delay the filter gains is the latency of the module and it is declared:
:meth:`TimePitch.latency_samples`.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.phase_vocoder import PhaseVocoder as SharedPhaseVocoder
from ..params import ParamDesc
from ..registry import register
from .base import Module
from .resample import SincResampler

__all__ = ["PhaseVocoder", "PitchResampler", "TimePitch", "resampled_length", "spectral_envelope"]

#: The steepness of the kernel the pitch shift resamples with.
_STEEPNESS = 0.7

#: The pre ringing of the kernel the pitch shift resamples with.  Zero is
#: the minimum phase kernel: it has no pre ringing, at the price of a phase
#: that is not linear.
_PRE_RINGING = 0.0


def _next_pow2(n: int) -> int:
    value = 1
    while value < int(n):
        value <<= 1
    return value


def spectral_envelope(x: np.ndarray, sample_rate: int, bands: int = 24) -> np.ndarray:
    """Return the long term spectral envelope of a block, in decibels.

    The envelope is measured on a logarithmic frequency grid and smoothed, so
    it follows the shape of the spectrum and not its fine structure.
    """
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    n = buf.shape[0]
    if n < 64:
        return np.zeros(bands, dtype=np.float64)
    size = int(min(4096, _next_pow2(n)))
    size = max(256, size)
    hop = size // 4
    window = np.hanning(size)
    frames = 1 + (n - size) // hop
    stride = max(1, frames // 48)
    accumulator = np.zeros(size // 2 + 1, dtype=np.float64)
    used = 0
    for index in range(0, frames, stride):
        start = index * hop
        accumulator += np.abs(np.fft.rfft(buf[start: start + size, 0] * window))
        used += 1
    if used == 0:
        return np.zeros(bands, dtype=np.float64)
    accumulator /= used
    freqs = np.fft.rfftfreq(size, 1.0 / float(sample_rate))
    low = 40.0
    high = min(16000.0, 0.45 * sample_rate)
    ratio = (high / low) ** (1.0 / max(1, bands - 1))
    centres = low * np.power(ratio, np.arange(bands, dtype=np.float64))
    edges = np.concatenate(
        (
            np.array([centres[0] * (centres[0] / centres[1]) ** 0.5]),
            np.sqrt(centres[:-1] * centres[1:]),
            np.array([centres[-1] * (centres[-1] / centres[-2]) ** 0.5]),
        )
    )
    levels = np.zeros(bands, dtype=np.float64)
    for band in range(bands):
        mask = (freqs >= edges[band]) & (freqs < edges[band + 1])
        if not np.any(mask):
            continue
        levels[band] = 20.0 * math.log10(max(float(np.mean(accumulator[mask])), 1e-9))
    # Smooth across the bands so the envelope follows the broad shape.
    for _ in range(2):
        padded = np.concatenate((levels[:1], levels, levels[-1:]))
        levels = (padded[:-2] + 2.0 * padded[1:-1] + padded[2:]) / 4.0
    return levels


def _fit_length(x: np.ndarray, length: int) -> np.ndarray:
    """Return ``x`` with exactly ``length`` samples.

    A block processor owes its host a block of exactly ``length`` samples,
    so the difference is made up here.
    """
    have = int(x.shape[0])
    if have == length:
        return x
    if have > length:
        return x[:length]
    pad = np.zeros((length - have, x.shape[1]), dtype=np.float64)
    return np.concatenate((x, pad), axis=0)


def resampled_length(n: int, ratio: float) -> int:
    """The number of samples a block of ``n`` samples resamples to.

    The factor the phase vocoder is given is built from this number, so
    it has to be the same number the module always used: the vocoder's
    analysis frames and their positions follow from it, and a different
    number moves the grid.
    """
    return max(1, int(round(n / max(float(ratio), 1e-9))))


class PitchResampler:
    """The resampling half of a pitch shift, as a streaming filter.

    The kernel of the interpolation reaches ``half_width`` (48) input
    samples to the **right** of the read position and 47 to the left, so a
    block that is resampled on its own is cut in two: the first and the
    last samples of every block come from a kernel whose half is missing.
    A constant of 0.5 came out as 0.5647 at the start of a block and as
    0.3056 at its end.

    This filter keeps the samples that came before the block (the history)
    and holds its output back by the look ahead the kernel needs, so that
    every output is computed from the whole kernel.  The read position is
    counted from the start of the stream, so one block and many blocks give
    the same answer — the same answer, bit for bit.

    The delay is the latency of the pitch shift and it is declared:
    :meth:`latency`.

    Parameters
    ----------
    sample_rate:
        The rate the module runs at.
    """

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = int(sample_rate)
        self._resampler: SincResampler | None = None
        self._ratio: float | None = None

    # -- the kernel -----------------------------------------------------------
    def _get(self, ratio: float) -> SincResampler:
        """The resampler for a pitch factor, built once per factor.

        The kernel costs about half a millisecond to build, so a block
        processor that rebuilt it on every block would pay that price for
        every block.  The kernel only depends on the factor, so it is kept
        until the factor changes.
        """
        key = float(ratio)
        if self._resampler is None or self._ratio != key:
            self._resampler = SincResampler(
                1.0 / key,
                self.sample_rate,
                steepness=_STEEPNESS,
                pre_ringing=_PRE_RINGING,
            )
            self._ratio = key
        return self._resampler

    def reset(self) -> None:
        """Clear the state of the filter.

        The kernel is kept: it only depends on the pitch factor, and
        building it costs about half a millisecond.
        """
        if self._resampler is not None:
            self._resampler.reset()

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray, ratio: float) -> np.ndarray:
        """Resample one block by a pitch factor.

        The output is the ideal resample of the block, delayed by the look
        ahead of the kernel.  The number of samples is the number of read
        positions that fall into the block: the exact number for the whole
        signal, one sample more or less than the block rounded on its own.
        """
        return self._get(ratio).process(x)

    # -- the latency ----------------------------------------------------------
    def delay(self, ratio: float) -> int:
        """The delay of the resampler, in the samples of its output."""
        return int(self._get(ratio).delay)

    def latency(self, ratio: float, stretch: float = 1.0) -> int:
        """The latency of a pitch shift, in the samples of the output.

        The resampler holds its output back by the look ahead of its
        kernel, which is 48 input samples.  The samples of the resampled
        signal run at the rate of the pitch factor, so the delay is 48
        samples of the input signal, and the time stretch of the module
        scales it once more.
        """
        resampler = self._get(ratio)
        return int(round(resampler.delay * float(ratio) * float(stretch)))


class PhaseVocoder:
    """Phase vocoder based time stretcher.

    This is an adapter, not a second implementation: the algorithm lives in
    :class:`aurcore.dsp.phase_vocoder.PhaseVocoder`, which is the copy that
    is measured (``qa/vocoder/REPORT.md``) and guarded
    (``tests/test_phase_vocoder.py``).  The class only translates the
    module's parameters into the ones of the shared vocoder:

    * ``frame_ms`` becomes the analysis window length (rounded up to a power
      of two) and ``overlap`` the hop divisor,
    * ``coherence`` becomes the phase coherence, the blend between the free
      and the identity locked phase,
    * ``transient`` becomes the transient sensitivity; zero switches the
      transient detector off.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        frame_ms: float = 37.0,
        overlap: int = 4,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.overlap = max(1, int(overlap))
        self.frame = 0
        self.hop = 0
        self._engine: SharedPhaseVocoder | None = None
        self.set_frame(frame_ms)

    # -- configuration --------------------------------------------------------
    def set_frame(self, frame_ms: float) -> None:
        """Choose the analysis window length in milliseconds."""
        raw = max(64.0, float(frame_ms) * self.sample_rate / 1000.0)
        frame = max(64, int(_next_pow2(int(raw))))
        hop = max(1, frame // self.overlap)
        if frame == self.frame and hop == self.hop:
            # The window did not change: the vocoder stays as it is, so that
            # a block processor does not rebuild it on every block.
            return
        self.frame = frame
        self.hop = hop
        self._engine = None

    def _vocoder(self, coherence: float, transient: float) -> SharedPhaseVocoder:
        """The shared vocoder for the current window and settings."""
        engine = self._engine
        # Without any coherence the locking is blended out completely, so the
        # locking does not have to be computed at all.
        lock = bool(coherence > 0.0)
        if engine is None:
            engine = SharedPhaseVocoder(
                self.frame,
                self.hop,
                self.sample_rate,
                window="hann",
                phase_lock=lock,
                phase_coherence=float(np.clip(coherence, 0.0, 1.0)),
                transient_sensitivity=float(max(0.0, transient)),
            )
            self._engine = engine
        else:
            engine.phase_lock = lock
            engine.phase_coherence = float(np.clip(coherence, 0.0, 1.0))
            engine.transient_sensitivity = float(max(0.0, transient))
        return engine

    # -- processing -----------------------------------------------------------
    def process(
        self,
        x: np.ndarray,
        stretch: float,
        coherence: float = 0.5,
        transient: float = 1.0,
    ) -> np.ndarray:
        """Stretch a mono block of audio by ``stretch`` (2 means twice as long)."""
        source = np.asarray(x, dtype=np.float64)
        if source.ndim > 1:
            source = source[:, 0]
        n = source.shape[0]
        if n == 0:
            return source
        stretch = max(0.0625, min(16.0, float(stretch)))
        engine = self._vocoder(coherence, transient)
        out = np.asarray(engine.time_stretch(source, stretch)).ravel()
        wanted = max(1, int(round(n * stretch)))
        if out.size < wanted:
            out = np.pad(out, (0, wanted - out.size))
        return out[:wanted]


@register
class TimePitch(Module):
    """Change the duration and the pitch of the signal."""

    code = "AUR_TIMEPITCH"
    display_name = "Time & Pitch"
    category = "Utility"
    edition = "standard"
    element = "TimeStretchPitchShift"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._vocoder = PhaseVocoder(sample_rate, channels)
        self._pitch_resampler = PitchResampler(sample_rate)

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Algorithm",
                type="enum",
                default="Radius",
                minimum=0.0,
                maximum=2.0,
                choices=("Radius", "Radius RT", "Solo"),
                group="Algorithm",
                help="Variant of the phase vocoder.",
            ),
            ParamDesc(
                name="Stretch Ratio",
                type="float",
                default=1.0,
                minimum=0.125,
                maximum=8.0,
                group="Time",
                help="Duration multiplier; two means twice as long.",
            ),
            ParamDesc(
                name="Pitch Shift",
                type="float",
                default=0.0,
                minimum=-24.0,
                maximum=24.0,
                unit="st",
                group="Pitch",
                help="Pitch change in semitones.",
            ),
            ParamDesc(
                name="Adaptive Window",
                type="float",
                default=37.0,
                minimum=10.0,
                maximum=120.0,
                unit="ms",
                group="Quality",
                help="Length of the analysis window.",
            ),
            ParamDesc(
                name="Transient Sensitivity",
                type="float",
                default=1.0,
                minimum=0.0,
                maximum=2.0,
                group="Quality",
                help="How strongly attacks are detected and kept sharp.",
            ),
            ParamDesc(
                name="Phase Coherence",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Quality",
                help="Amount of identity phase locking.",
            ),
            ParamDesc(
                name="Shift Formants",
                type="bool",
                default=False,
                minimum=0.0,
                maximum=1.0,
                group="Pitch",
                help="Keeps the vocal character when the pitch is shifted.",
            ),
        ]

    def reset(self) -> None:
        """Clear the internal state."""
        self._vocoder.set_frame(37.0)
        self._pitch_resampler.reset()

    @property
    def latency_samples(self) -> int:
        """The delay the module has, in the samples of its output.

        The pitch shift resamples the signal, and the kernel of the
        interpolation reaches 48 input samples to the right of the read
        position, so the resampled signal is held back by that much.  The
        samples of the resampled signal run at the rate of the pitch
        factor, so the delay is 48 samples of the input signal, and the
        time stretch of the module scales it once more.

        A module that does not shift the pitch does not resample, and a
        module that only stretches does not delay the signal either: the
        phase vocoder centres its windows, so it does not move the signal
        in time.
        """
        ratio = 2.0 ** (float(self.params.get("Pitch Shift")) / 12.0)
        if abs(ratio - 1.0) <= 1e-9:
            return 0
        stretch = float(self.params.get("Stretch Ratio"))
        return self._pitch_resampler.latency(ratio, stretch)

    def _apply_formant_correction(
        self, x: np.ndarray, ratio: float, sample_rate: int
    ) -> np.ndarray:
        """Compensate the spectral envelope that the pitch shift moved.

        The correction is applied as a smooth zero phase curve, so the shape
        of the spectral envelope is restored without the ringing a cascade of
        narrow filters would produce.
        """
        envelope = spectral_envelope(x, sample_rate)
        if envelope.size == 0:
            return x
        bands = envelope.size
        ratio = max(ratio, 1e-6)
        centres = np.zeros(bands, dtype=np.float64)
        low = 40.0
        high = min(16000.0, 0.45 * sample_rate)
        step = (high / low) ** (1.0 / max(1, bands - 1))
        for index in range(bands):
            centres[index] = low * step**index
        # The shift moved the envelope up by the ratio, so the envelope of the
        # source is the measured one read at the higher frequency.  The
        # difference is the correction that puts the formants back.
        restored = np.interp(
            centres * ratio, centres, envelope, left=envelope[0], right=envelope[-1]
        )
        gain_db = restored - envelope
        # Bands that carry no signal are left alone, and the correction is
        # kept within a sensible range.
        audible = envelope > (float(np.max(envelope)) - 40.0)
        gain_db = np.where(audible, np.clip(gain_db, -12.0, 12.0), 0.0)

        n = x.shape[0]
        pad = min(n, 4096)
        padded = np.pad(x, ((pad, pad), (0, 0)), mode="reflect")
        size = _next_pow2(padded.shape[0])
        freqs = np.fft.rfftfreq(size, 1.0 / float(sample_rate))
        curve = np.interp(
            freqs, centres, gain_db, left=gain_db[0], right=gain_db[-1]
        )
        scale = np.power(10.0, curve / 20.0)
        spectrum = np.fft.rfft(padded, size, axis=0)
        corrected = np.fft.irfft(spectrum * scale[:, None], size, axis=0)
        return corrected[pad: pad + n]

    def process(self, x: np.ndarray) -> np.ndarray:
        """Change the duration and the pitch of a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        algorithm = str(self.params.get("Algorithm"))
        stretch = float(self.params.get("Stretch Ratio"))
        semitones = float(self.params.get("Pitch Shift"))
        window_ms = float(self.params.get("Adaptive Window"))
        transient = float(self.params.get("Transient Sensitivity"))
        coherence = float(self.params.get("Phase Coherence"))
        keep_formants = bool(self.params.get("Shift Formants"))

        if algorithm == "Radius RT":
            self._vocoder.set_frame(max(10.0, window_ms * 0.5))
            coherence = max(coherence, 0.75)
        elif algorithm == "Solo":
            self._vocoder.set_frame(window_ms)
            coherence = max(coherence, 0.9)
        else:
            self._vocoder.set_frame(window_ms)

        ratio = 2.0 ** (semitones / 12.0)
        work = buf
        if abs(ratio - 1.0) > 1e-9:
            # A ratio of one over the pitch factor plays the signal faster and
            # therefore raises the pitch by that factor.  The resampler is a
            # streaming filter: it keeps the history the kernel needs and
            # delays its output by the look ahead, so the edges of the block
            # are computed from the whole kernel.
            work = self._pitch_resampler.process(buf, ratio)
            if keep_formants:
                work = self._apply_formant_correction(work, ratio, self.sample_rate)
        target = max(1, int(round(n * stretch)))
        if work.shape[0] == 0:
            return np.zeros((target, buf.shape[1]), dtype=np.float64)
        # The vocoder's frame grid must not move: the factor it is given is
        # built from the length the resampled block has always had, so the
        # analysis frames and their positions are the ones the vocoder has
        # always seen.  The streaming resampler hands out the exact number
        # of read positions, which is one sample more or less than the block
        # rounded on its own; the vocoder's output is fitted to the length
        # the block needs.
        expected = resampled_length(n, ratio) if abs(ratio - 1.0) > 1e-9 else n
        factor = target / float(expected)
        if abs(factor - 1.0) < 1e-9:
            # The stretch cancels the pitch shift: the resampled signal is
            # handed back, and it has to have the length the block needs.
            return _fit_length(work, target)
        out = np.zeros((target, work.shape[1]), dtype=np.float64)
        for channel in range(work.shape[1]):
            stretched = self._vocoder.process(
                work[:, channel], factor, coherence=coherence, transient=transient
            )
            # The vocoder returns the length its factor asks for; the block
            # processor needs the exact target length.
            take = min(target, stretched.size)
            out[:take, channel] = stretched[:take]
        return out
