"""
aurcore.modules.voice_reverb — Voice Reverb.

Removes the reverberant field a voice carries, and leaves the voice alone.

What makes this different from the general De-Reverb
----------------------------------------------------
The De-Reverb estimates, per wide band, how much of the signal is a
*sustained* field, and attenuates that field.  It does not know what a
voice is: a steady sound that is not a voice is exactly the thing it looks
for, so it turns a held note down together with the tail.

The Voice Reverb works on the *reverberant field of a voice*, and it
**measures the room before it removes anything**.

1. **The voice band.**  The analysis is confined to the band a voice lives
   in (80 Hz to 12 kHz by default) and the bands are spaced on the Bark
   scale, so they follow the resolution of the ear.  Everything outside the
   band is left alone.

2. **The room is measured, not assumed.**  The module looks at the gaps
   between the words.  When the voice stops, what is left is the room: the
   level in the gap is the level the room is still producing, decayed by the
   room's own rate over the length of the gap.  The distance between that
   level and the level the voice had when it stopped is the **gain of the
   room** -- the number of decibels the field sits below the source.
   A dry voice falls to the floor in the gap, so its gain is very small and
   the module leaves the recording alone.  A voice in a room falls only to
   the level the room is still producing, so the gain is a few decibels
   below the voice and the field can be taken out of the voice itself.

3. **Two shares.**  Every frame carries two estimates of how much of it is
   reverberant:
   * the *steady* share, the field the room is producing while the voice
     plays -- this is what the gain of the room gives;
   * the *gap* share, the frames in which the room is ringing without the
     source -- the room's memory is then well above the wanted sound, and
   the larger of the two is what the module takes out.

4. **The wanted sound is protected.**  The gain never boosts, the removal
   is capped by ``Reduction``, and a clean voice carries no room, so the
   module leaves it alone to within a hundredth of a decibel.

Block coherence
---------------
The analysis runs on a :class:`aurcore.dsp.stft.StreamingSTFT` engine, whose
frames sit on the same grid whatever the size of the block the host hands
over, and every measurement the module makes -- the two followers, the room's
memory and the room's gain -- is carried from frame to frame.  A host that
renders in small blocks reaches the same decision as one that renders the
whole file in a single block.

The centred analysis needs the samples that follow the frame it works on,
so the output of the module is late by
:attr:`VoiceReverb.latency_samples`; the host compensates it.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["VoiceReverb", "band_edges", "bark"]

#: Analysis window and hop (samples) of the module.
_N_FFT = 1024
_HOP = 256

#: Number of bands of the filter bank.
_BANDS = 24

#: Default edges of the voice band (Hz).
_LOW_HZ = 80.0
_HIGH_HZ = 12000.0

#: The release of the follower that tracks the wanted sound (seconds).  It
#: has to be short enough to follow the gap between two words and long
#: enough not to follow the wobble of a single syllable.
_FAST_RELEASE_S = 0.025

#: How far the room's memory has to stand above the wanted sound before the
#: frame counts as a gap (dB).
_GAP_MARGIN_DB = 0.0

#: The width of the ramp that turns the distance between the room's memory
#: and the wanted sound into a share (dB).
_GAP_WIDTH_DB = 3.0

#: The correction of the level the room holds in a gap (dB).
#:
#: The field of a room is a sum of many delayed copies of the source, so its
#: level flickers: the power of a band follows an exponential distribution
#: and the *median* of the levels of a gap therefore sits 1.6 dB above the
#: mean level of the field.  The module measures the median of the gap, so
#: it takes that difference off.
_FILL_BIAS_DB = -1.6

#: The floor of the level computation.  It keeps the logarithm finite when
#: a band carries nothing at all.
_EPS = 1e-20

#: The gain of a room that is not there at all (dB).  Until the module has
#: seen a gap it assumes a clean recording and takes nothing out.
_NO_ROOM_DB = -60.0


def bark(freq: np.ndarray | float) -> np.ndarray:
    """Convert a frequency in Hz to the Bark scale.

    Parameters
    ----------
    freq:
        Frequency in Hz (scalar or array).

    Returns
    -------
    numpy.ndarray
        The Bark value of the frequency.
    """
    f = np.asarray(freq, dtype=np.float64)
    return 13.0 * np.arctan(0.00076 * f) + 3.5 * np.arctan((f / 7500.0) ** 2)


def band_edges(count: int, low_hz: float, high_hz: float) -> np.ndarray:
    """Edge frequencies of a psychoacoustically spaced filter bank.

    The edges are equally spaced on the Bark scale, which puts more bands
    where the ear resolves more detail.

    Parameters
    ----------
    count:
        Number of bands.
    low_hz:
        Lowest edge in Hz.
    high_hz:
        Highest edge in Hz.

    Returns
    -------
    numpy.ndarray
        ``count + 1`` edge frequencies in Hz.
    """
    z = np.linspace(float(bark(low_hz)), float(bark(high_hz)), int(count) + 1)
    lo = np.full_like(z, 10.0)
    hi = np.full_like(z, 24000.0)
    for _ in range(60):  # the Bark scale is monotonic, so bisection is exact
        mid = 0.5 * (lo + hi)
        go_up = bark(mid) < z
        lo = np.where(go_up, mid, lo)
        hi = np.where(go_up, hi, mid)
    return 0.5 * (lo + hi)


class _RoomProbe:
    """Finds the gaps of the envelope and measures the room in them.

    The probe watches the broadband level of the recording.  When the level
    falls well below the recent peak a gap opens; the frames of the gap are
    collected and the gap closes when the level comes back.  A closed gap
    carries three numbers:

    ``source``
        the level the wanted sound had when it stopped,
    ``fill``
        the median of the levels inside the gap -- the level the room is
        still producing,
    ``length``
        the time the gap lasted.

    The gain of the room follows from them: the level in the gap is the
    level the room was producing when the source stopped, decayed by the
    room's own rate over the length of the gap, so

    ``gain = fill + rate * length / 2 - source``

    A dry voice falls to the floor in the gap, so its gain is very small and
    the module does nothing.  A voice in a room falls only to the level the
    room is still producing, so the gain is a few decibels below the voice.
    """

    def __init__(self, history: int = 12, bias_db: float = _FILL_BIAS_DB):
        self.history = int(history)
        self.bias = float(bias_db)
        self._peak: float | None = None
        self._last = 0.0
        self._source = 0.0
        self._levels: list[float] = []
        self._start = 0.0
        self._open = False
        self._gains: list[float] = []

    def reset(self) -> None:
        """Forget the gaps that were seen."""
        self._peak = None
        self._last = 0.0
        self._source = 0.0
        self._levels = []
        self._start = 0.0
        self._open = False
        self._gains = []

    def update(
        self, level: float, time_s: float, rate_db_s: float, drop_db: float, rise_db: float
    ) -> None:
        """Feed one frame to the probe."""
        if self._peak is None:
            self._peak = level
            self._last = level
            return
        if not self._open:
            if level > self._peak:
                self._peak = level
            elif self._peak - level >= drop_db:
                self._open = True
                self._levels = [level]
                self._start = time_s
                self._source = self._last
        else:
            self._levels.append(level)
            if self._peak - level <= rise_db:
                # the gap closed: this is what the room left behind
                fill = float(np.median(self._levels))
                length = time_s - self._start
                gain = fill + self.bias + rate_db_s * (0.5 * length) - self._source
                self._gains.append(float(gain))
                if len(self._gains) > self.history:
                    self._gains.pop(0)
                self._open = False
                self._peak = level
        self._last = level

    @property
    def gain_db(self) -> float:
        """The gain of the room, in decibels (very small without a room)."""
        if not self._gains:
            return _NO_ROOM_DB
        return float(np.median(self._gains))


@register
class VoiceReverb(Module):
    """Voice Reverb — the reverberant field of a voice, measured and removed."""

    code = "AUR_VOICEREVERB"
    display_name = "Voice Reverb"
    category = "Repair"
    edition = "standard"
    element = "Dereverb"
    analysis = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = StreamingSTFT(_N_FFT, _HOP, window="hann")
        self._channels = max(int(channels), 1)
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._build_bands()
        self._clear_state()

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            ParamDesc(
                name="Reduction",
                type="float",
                default=12.0,
                minimum=0.0,
                maximum=24.0,
                unit="dB",
                group="Reduction",
                help="How much of the reverberant field is removed.",
            ),
            ParamDesc(
                name="Tail Length",
                type="float",
                default=1.0,
                minimum=0.1,
                maximum=5.0,
                unit="s",
                group="Tail",
                help="Decay time used to separate the sustained field from the direct one.",
            ),
            ParamDesc(
                name="Voice Band Low",
                type="float",
                default=_LOW_HZ,
                minimum=40.0,
                maximum=400.0,
                unit="Hz",
                group="Bands",
                curve="log",
                help="The lowest frequency in Hz the module treats as part of the voice.",
            ),
            ParamDesc(
                name="Voice Band High",
                type="float",
                default=_HIGH_HZ,
                minimum=3000.0,
                maximum=20000.0,
                unit="Hz",
                group="Bands",
                curve="log",
                help="The highest frequency in Hz the module treats as part of the voice.",
            ),
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Sensitivity",
                help="How deeply the level has to fall before the module looks for a room.",
            ),
            ParamDesc(
                name="Output Reverb Only",
                type="bool",
                default=False,
                group="Output",
                help="Renders the removed reverberant component instead of the cleaned signal.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def _build_bands(self) -> None:
        """Build the filter bank of the current voice band."""
        low = float(self.params.get("Voice Band Low"))
        high = float(self.params.get("Voice Band High"))
        high = max(high, low + 100.0)
        self._edges = band_edges(_BANDS, low, high)
        freqs = np.fft.rfftfreq(_N_FFT, 1.0 / float(self.sample_rate))
        inside = np.nonzero((freqs >= low) & (freqs <= high))[0]
        if inside.size == 0:
            inside = np.arange(freqs.size)
        self._first_bin = int(inside[0])
        self._last_bin = int(inside[-1])
        # the band every analysis bin belongs to; the bins outside the voice
        # band belong to no band at all and are left alone
        index = np.searchsorted(self._edges, freqs, side="right") - 1
        self._bin_band = np.clip(index, 0, _BANDS - 1).astype(np.intp)
        outside = (freqs < low) | (freqs > high)
        self._bin_band[outside] = -1
        # the first bin of every band, relative to the first bin of the bank
        starts = np.searchsorted(
            self._edges, freqs[self._first_bin : self._last_bin + 1], side="right"
        ) - 1
        counts = np.bincount(starts, minlength=_BANDS)
        offsets = np.concatenate(([0], np.cumsum(counts)))
        self._band_starts = offsets[:-1]
        self._band_counts = counts
        # the matrix that sums the bins of a band: one product per frame
        matrix = np.zeros((self._last_bin - self._first_bin + 1, _BANDS), dtype=np.float64)
        for band in range(_BANDS):
            count = int(counts[band])
            if count <= 0:
                continue
            first = int(self._band_starts[band])
            matrix[first : first + count, band] = 1.0
        self._band_matrix = matrix
        self._band_key = (round(low, 3), round(high, 3))

    def _clear_state(self) -> None:
        """Drop everything the decision carries from frame to frame."""
        self._fast: np.ndarray | None = None
        self._memory: np.ndarray | None = None
        self._probe = _RoomProbe()
        self._room_gain_db = _NO_ROOM_DB
        self._frames_seen = 0

    def reset(self) -> None:
        """Forget the room that was measured and the state of the analysis."""
        self._engine.reset()
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._clear_state()

    # -- contract -------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds.

        The centred analysis needs the samples that follow the frame it
        works on, so the output of the module is late by that much.  The
        number does not depend on the size of the block the host hands over.
        """
        return int(self._engine.latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The ring-out is the tail of the last analysis window, so it is the
        length of the window beyond the look-ahead the module reports.
        """
        return _N_FFT

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Remove the reverberant field of the voice.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The processed block, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        if int(arr.shape[1]) != self._channels:
            # the engine cannot change its channel count inside a stream
            self._channels = int(arr.shape[1])
            self._engine.reset()
            self._fifo = np.zeros((0, self._channels), dtype=np.float64)
            self._clear_state()
        self._check_bands()

        mag, phase = self._engine.analyze(arr)
        if mag.shape[-1]:
            mag, phase = self._modify(mag, phase)
        raw = self._engine.synthesize(mag, phase)
        return self._delay(raw, int(arr.shape[0]))

    def _check_bands(self) -> None:
        """Rebuild the filter bank when the voice band has moved."""
        low = float(self.params.get("Voice Band Low"))
        high = float(self.params.get("Voice Band High"))
        if (round(low, 3), round(high, 3)) == self._band_key:
            return
        self._build_bands()
        self._engine.reset()
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._clear_state()

    def _delay(self, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the output by the latency of the analysis and take ``count``."""
        fifo = self._fifo
        if fifo.shape[0] == 0:
            fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
        fifo = np.concatenate([fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._fifo = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def _band_powers(self, mid: np.ndarray) -> np.ndarray:
        """The power of every band of the channel average, per frame.

        ``mid`` has the shape ``(bins, frames)``; the result has the shape
        ``(frames, bands)``.
        """
        power = mid * mid
        window = power[self._first_bin : self._last_bin + 1].T
        out = window @ self._band_matrix
        empty = self._band_counts == 0
        if np.any(empty):
            out[:, empty] = 0.0
        return out

    def _modify(self, mag: np.ndarray, phase: np.ndarray):
        """Apply the reverberant gain to the frames the engine has analysed.

        ``mag`` and ``phase`` are shaped ``(channels, bins, frames)``.
        """
        reduction = float(self.params.get("Reduction"))
        tail_s = float(self.params.get("Tail Length"))
        sensitivity = float(self.params.get("Sensitivity"))
        output_only = bool(self.params.get("Output Reverb Only"))

        mid = mag.mean(axis=0) if mag.ndim == 3 and mag.shape[0] > 1 else mag.reshape(-1,
                                                                                      mag.shape[-1])
        powers = self._band_powers(mid)
        levels = 10.0 * np.log10(np.maximum(powers, _EPS))
        n_frames = levels.shape[0]

        fps = float(self.sample_rate) / float(_HOP)
        dt = 1.0 / fps
        rate_db_s = 60.0 / max(tail_s, 1e-3)
        a_fast = math.exp(-dt / max(_FAST_RELEASE_S, 1e-6))
        drop_db = 9.0 - 6.0 * float(np.clip(sensitivity, 0.0, 1.0))

        if self._fast is None or self._fast.shape[0] != _BANDS:
            self._fast = levels[0].copy()
            self._memory = levels[0].copy()

        share = np.zeros((n_frames, _BANDS), dtype=np.float64)
        for index in range(n_frames):
            level = levels[index]
            # the follower of the wanted sound: it follows a rise at once and
            # falls back with the release of the source
            fast = self._fast
            self._fast = np.maximum(fast * a_fast + level * (1.0 - a_fast), level)
            # the room's memory: the level the room would still be producing
            memory = self._memory
            self._memory = np.maximum(level, memory - rate_db_s * dt)

            # the room is measured in the gaps of the envelope
            total = float(np.sum(powers[index]))
            self._probe.update(
                10.0 * math.log10(max(total, 1e-30)),
                self._frames_seen * dt,
                rate_db_s,
                drop_db,
                3.0,
            )
            self._frames_seen += 1
            self._room_gain_db = self._probe.gain_db
            room = 10.0 ** (self._room_gain_db / 10.0)
            steady = room / (1.0 + room)

            gap = np.clip(
                (self._memory - self._fast - _GAP_MARGIN_DB) / _GAP_WIDTH_DB, 0.0, 1.0
            )
            share[index] = np.maximum(gap, steady)

        # the share of the frame that is reverberant is taken out of it
        depth = np.minimum(
            reduction, -10.0 * np.log10(np.maximum(1.0 - share, 1e-6))
        )
        gain = 10.0 ** (-depth / 20.0)

        # the gain is the same for every channel, so the stereo image is kept
        curve = np.ones((n_frames, mag.shape[1]), dtype=np.float64)
        inside = self._bin_band >= 0
        curve[:, inside] = gain[:, self._bin_band[inside]]
        processed = mag * curve.T[None, :, :]
        if output_only:
            processed = mag - processed
        return processed, phase
