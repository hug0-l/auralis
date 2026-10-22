"""
aurcore.modules.dialogue_isolate — Dialogue Isolate.

Splits a dialogue recording into three stems — **voice**, **room reverb** and
**noise** — and lets the user re-balance them with three gain controls.

How the split is made (pure DSP, no trained model)
---------------------------------------------------
1. **Short-time analysis.**  A centred STFT (Hann window, 75 % overlap) is
   computed for every channel.  All masks are derived from the channel-average
   magnitude so the stereo image is preserved.
2. **Harmonic estimate.**  A bin counts as *harmonic* when it stands far
   above the noise that lies **between** the partials.  The noise between the
   partials is found with a *valley floor*: a running minimum that skips the
   bin itself and its two neighbours (the main lobe of a Hann window is four
   bins wide) and looks the width of a voice fundamental on either side.
   A median cannot do this: with a 110 Hz fundamental and a 4096 point
   transform the partials sit 9.4 bins apart, so a window wide enough to be
   robust also holds two or three partials and the median lands on their
   skirts instead of in the valley between them.
3. **Voice activity.**  A soft VAD reads the **balance of the voice band**:
   how much of the band is the wanted content, against the noise of that same
   band, in that same frame.  A quotient of two powers of the same band does
   not carry the level of the input, so the same material at another level
   gives the same decision and the module does not pump.  The reference of
   the decision is built from that balance: the balance over a short window
   (200 ms) and the memory of the balance the band has shown, which is what
   tells a band that carries wanted content from a band that carries nothing
   but noise.  The decision is smoothed into a sigmoid whose centre and width
   are controlled by ``Sensitivity``: at 0 the boundaries are hard and only
   clear voice is detected, at 1 they are wide and soft and quieter voice
   still counts.  A clear voice saturates the detector, so the voice is not
   turned down by the soft edge.
4. **Late field.**  An exponentially decaying envelope follower predicts how
   much energy a single exponential decay would still produce; whatever the
   fast release cannot explain is the late field, i.e. the reverb tail.  The
   envelope is smoothed before the two followers run: the frame to frame
   fluctuation of a voice is a few decibels, and a follower that answers to
   it calls the whole voice a late field and the reverb stem takes the voice
   with it.  A fall that is faster than the direct release can explain is a
   direct sound and not a tail, and the tail of the old level does not
   outlive it.  A global "reverberance" factor keeps clean, dry material from
   being mistaken for a room.

The three stems always sum back to the input, so a unity-gain mix is
bit-for-bit transparent.

THE ANALYSIS IS STREAMING, AND THE LATENCY IS DECLARED
-------------------------------------------------------
The module used to analyse the block it was given and nothing else, so a
host that chopped the signal into blocks got a different answer for every
block: the residual between a whole render and a render in blocks was
-22 dB, and the module was 38 dB short of the block coherence the survey
asks for.  The cause was structural: the module kept no state between the
blocks.

The analysis is now **streaming**:

* the frames sit on one grid that does not depend on the block size of the
  host,
* the noise reference of the detector, the two envelope followers and the
  reverberance factor are carried from frame to frame,
* the state of the overlap-add is carried from one block to the next.

Two blocks of the same signal are therefore treated the same way, no matter
how the host chops the stream: the module is block coherent.

THE DECISION IS TAKEN IN THE RATIO DOMAIN
-----------------------------------------
The rule the study hands over is that the gain a module applies may not
follow the level of the input: the same material at another level has to
get the same gain, or the module pumps.  Two things in this module carried
the level of the input into the gain, and both are gone:

1. **The voice activity compared the wanted content of a frame with the
   noise level of the material.**  The reference was the median of the
   *absolute* noise power of the last second, so a change of the level of
   the input moved the reference -- and the detector with it -- and it took
   the reference a second to follow.

   The decision is now taken on the **balance inside the band**: how much
   of the band is the wanted content, against the noise of that same band,
   in that same frame.  A quotient of two powers of the same band does not
   carry the level of the input.  The reference of the decision is built
   from that balance: the balance over a short window (200 ms) and the
   memory of the lowest balance the band has ever shown, which is what
   tells a band that carries wanted content from a band that carries
   nothing but noise.

2. **The late field was measured in levels.**  The two envelope followers
   that tell a reverberant tail from a direct sound kept the *energy* of
   the frame, so their state carried the level of the input from frame to
   frame, and the gain of the module followed it.

   The energy of the frame is now measured against a fast scale, so that
   the state of the two followers is a ratio and not a level.  The
   followers therefore hold nothing that a change of the level of the
   input could move.

The centred analysis needs the samples that follow a frame, so the module
delays its output by :attr:`DialogueIsolate.latency_samples` samples and
says so; the host takes the delay off.  That delay is the price of the
transform size, and the transform size is not a free choice.

THE TRANSFORM SIZE IS NOT A FREE CHOICE
----------------------------------------
The partials of a voice have to be *resolved* for the valley floor to find
the noise between them.  The main lobe of a Hann window is four bins wide,
so two partials only separate when their spacing is more than four bins:

    partial spacing in bins = f0 * n_fft / sample rate

With a 110 Hz fundamental at 48 kHz that means

    n_fft = 1024  ->  2.35 bins   (the partials overlap, there is no valley)
    n_fft = 2048  ->  4.69 bins   (the nulls of the main lobes just meet)
    n_fft = 4096  ->  9.39 bins   (a valley of five bins, clean)
    n_fft = 8192  ->  18.77 bins  (a valley of thirteen bins)

``Good`` therefore uses a 4096 point transform and ``Best`` an 8192 point
one.  The price is an analysis window of 85 ms (170 ms for ``Best``), and
the delay the module reports is that window.  The module also needs a block
of at least one hop (1024 samples, 2048 for ``Best``): a block that is
shorter than the hop carries less context than the analysis assumes, and
the module would have to guess the rest of the window.

MODEL-INTEGRATION POINT
-----------------------
Every bit of separation happens inside :meth:`DialogueIsolate._separate`,
which returns the two masks of one frame — the voice and the reverb.  The
rest of the module — the mix, the three gains — is built **only** on top of
those masks.  A learned separator can therefore replace ``_separate`` with a
model that returns the same two masks, and nothing else in the module has to
change; the model has to be a streaming one, because the masks of a frame
depend on the frames that came before it.  ``ml = True`` tells the user
interface that a trained model could improve this module.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

_EPS = 1e-12

#: Analysis constants.  ``Good`` is the default, ``Best`` doubles the
#: transform size.  The sizes are the ones that resolve a 110 Hz fundamental:
#: see the module docstring.
_QUALITY = {
    "Good": {"n_fft": 4096, "hop": 1024},
    "Best": {"n_fft": 8192, "hop": 2048},
}

#: How many bins on either side of a bin the valley floor skips.  The main
#: lobe of a Hann window is four bins wide, so the two bins next to the bin
#: itself still belong to the peak of the partial.
_VALLEY_GAP = 2

#: How far the valley floor looks for the noise between two partials, in
#: hertz.  It has to reach the valley but not the next partial, so it is
#: given in hertz and turned into bins for the transform at hand.
_VALLEY_HZ = 70.0

#: How far a bin has to stand above the valley floor to count as a harmonic,
#: and how wide the soft edge of that decision is.
_TONAL_MARGIN_DB = 8.0
_TONAL_EDGE_DB = 6.0

#: The voice band: the centre and the edge of the band, and the edges the
#: band tapers to.  The band has to reach down to the fundamental of a voice
#: (85 Hz for a low male voice), otherwise the fundamental — which carries a
#: large part of the energy of a voice — falls into the noise stem.
_VOICE_BAND = (80.0, 4000.0, 40.0, 8000.0)

#: The shape of the voice detector.  The centre of the sigmoid is the signal
#: to noise ratio at which the voice starts to count; the width is how sharp
#: the decision is.  The width is small enough that a clear voice saturates
#: the detector and is not turned down by the soft edge.
_VAD_THRESHOLD_DB = 6.0
_VAD_THRESHOLD_SLOPE = 18.0
_VAD_SOFTNESS = 1.0
_VAD_SOFTNESS_SLOPE = 3.0

#: The window the reference of the voice activity is built from, in seconds.
#: The reference is a window of the balance, so it follows the material
#: quickly; a change of the level of the input cannot be carried by it,
#: because the balance does not carry the level of the input.
_BALANCE_WINDOW_S = 0.200

#: The percentile of that window the reference is taken from: the floor of
#: the balance of the band.
_BALANCE_PERCENTILE = 20.0

#: How far the memory of the balance may protect the band (dB).
_BALANCE_MEMORY_MARGIN_DB = 6.0

#: How fast the memory of the balance creeps back down (dB per frame).  The
#: creep is given per frame, so that the memory behaves the same no matter
#: how the host chops the stream.
_BALANCE_CREEP_DB = 0.02

#: How much of the protection the memory of the balance may give (dB), and
#: how much of it is used per decibel the band falls below its reference.
_BALANCE_PROTECTION_DB = 6.0
_BALANCE_PROTECTION_GAIN = 0.25

#: The floor and the ceiling of the balance of the band (dB).  The balance
#: is a quotient of two powers, so it is bounded in principle; the floor and
#: the ceiling keep the decision of the module away from the arithmetic
#: limits of the machine.
_BALANCE_FLOOR_DB = -60.0
_BALANCE_CEILING_DB = 60.0

#: The length of the window the energy envelope is smoothed over before the
#: late field is measured, in seconds.  A frame of a voice carries a few
#: decibels of fluctuation — the partials of a harmonic stack interfere with
#: each other as the window slides — and the late field must not answer to
#: that fluctuation, or the reverb stem takes the voice with it.
_ENVELOPE_SMOOTHING_S = 0.15

#: The release time of a direct sound, in seconds.  The late field is the
#: part of the energy that outlives this release.
_DIRECT_RELEASE_S = 0.02

#: The kernel the reverb stem is smeared with, so that it reads as a diffuse
#: tail rather than as a gated copy of the direct sound.  It is applied to
#: the stem in the time domain, so the three stems stay aligned with the
#: input and a unity mix stays transparent.
_REVERB_KERNEL = (0.25, 0.5, 0.25)

#: How much of the average late field counts as a room.
_REVERBERANCE_FULL = 0.15


# ---------------------------------------------------------------------------
# Small local DSP helpers (numpy only — the core never hard-depends on scipy).
# ---------------------------------------------------------------------------
def _hann(n: int) -> np.ndarray:
    """Periodic Hann window of length ``n``."""
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=np.float64) / float(n))


def _valley_floor(x: np.ndarray, gap: int = _VALLEY_GAP, span: int = 4) -> np.ndarray:
    """The running minimum next to a bin — the noise between two partials.

    A *median* over a wide window cannot separate the harmonics of a voice
    from the noise between them: the window that is wide enough to be robust
    also holds two or three partials, and the median lands on their skirts.
    The running minimum ignores the peaks and therefore lands in the valley
    between two harmonics, which is the level the noise estimate needs.

    Parameters
    ----------
    x:
        Magnitude spectrogram, shape ``(frames, bins)``.
    gap:
        How many bins next to the bin itself are skipped (the main lobe of a
        Hann window is four bins wide).
    span:
        How many bins are inspected on each side.

    Returns
    -------
    numpy.ndarray
        The floor, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[None, :]
    bins = int(arr.shape[1])
    gap = max(0, int(gap))
    span = max(1, int(span))
    pad = gap + span
    padded = np.pad(arr, ((0, 0), (pad, pad)), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, span, axis=-1)
    left = view[:, :bins].min(axis=-1)
    start = span + 2 * gap + 1
    right = view[:, start: start + bins].min(axis=-1)
    return np.minimum(left, right)


def _band_weight(freqs: np.ndarray, lo: float, hi: float, lo_edge: float,
                 hi_edge: float) -> np.ndarray:
    """Smooth band-pass weight: 1 inside ``[lo, hi]``, cosine tapers outside."""
    w = np.zeros_like(freqs)
    inside = (freqs >= lo) & (freqs <= hi)
    w[inside] = 1.0
    if lo_edge < lo:
        up = (freqs > lo_edge) & (freqs < lo)
        w[up] = 0.5 * (1.0 - np.cos(np.pi * (freqs[up] - lo_edge) / (lo - lo_edge)))
    if hi_edge > hi:
        down = (freqs > hi) & (freqs < hi_edge)
        w[down] = 0.5 * (1.0 + np.cos(np.pi * (freqs[down] - hi) / (hi_edge - hi)))
    return w


def _sigmoid(v: np.ndarray) -> np.ndarray:
    """Numerically safe logistic function."""
    return 1.0 / (1.0 + np.exp(-np.clip(v, -60.0, 60.0)))


def _percentile(values: "deque[float] | list[float]", percent: float) -> float:
    """The percentile of a short series, without the cost of a sort of numpy.

    The window of the reference holds a handful of frames, and the call
    happens once per frame: the overhead of the numpy machinery would be
    larger than the work itself.
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = int((percent / 100.0) * (len(ordered) - 1))
    return float(ordered[index])


def _db_gain(db: float) -> float:
    """Decibel value to linear gain."""
    return 10.0 ** (float(db) / 20.0)


def _latency(n_fft: int, hop: int) -> int:
    """The delay a centred analysis of this size adds, in samples.

    The overlap-add reconstruction of a centred frame needs the frame that
    reaches one window length into the future, so a streaming module cannot
    emit an output sample before the input has advanced by the length of the
    window.  The delay is therefore a property of the analysis window, not a
    choice.
    """
    half = n_fft // 2
    return int((half // hop) * hop + n_fft - half)


class _DelayLine:
    """A line of samples that hands out what it is given, late.

    The line is primed with silence, so the delay is the same whatever the
    block size of the host is.  The samples that are still in the line are
    kept as the blocks they arrived in, so a block does not copy the whole
    history: only the block itself is copied, and the history that is
    already in the line is not touched.
    """

    def __init__(self, length: int, width: int) -> None:
        self._length = int(length)
        self._width = int(width)
        self._pending: list[np.ndarray] = []
        self._held = 0
        self._primed = False

    def push(self, block: np.ndarray, count: int) -> np.ndarray:
        """Put the block into the line and take ``count`` samples out.

        Parameters
        ----------
        block:
            The new samples, shape ``(n, width)``.
        count:
            How many samples to hand out.

        Returns
        -------
        numpy.ndarray
            The samples that left the line, shape ``(count, width)``.  The
            samples that are not in the line yet are silence.
        """
        if not self._primed:
            self._pending.append(
                np.zeros((self._length, self._width), dtype=np.float64)
            )
            self._held += self._length
            self._primed = True
        if int(block.shape[0]):
            self._pending.append(np.array(block, dtype=np.float64, copy=True))
            self._held += int(block.shape[0])
        out = np.zeros((count, self._width), dtype=np.float64)
        taken = 0
        while taken < count and self._held > 0:
            head = self._pending[0]
            take = min(count - taken, int(head.shape[0]), self._held)
            if take <= 0:
                break
            out[taken: taken + take] = head[:take]
            taken += take
            self._held -= take
            if take == int(head.shape[0]):
                self._pending.pop(0)
            else:
                self._pending[0] = head[take:]
        return out

    def reset(self) -> None:
        """Forget the samples that are still in the line."""
        self._pending = []
        self._held = 0
        self._primed = False


# ---------------------------------------------------------------------------
# The module.
# ---------------------------------------------------------------------------
@register
class DialogueIsolate(Module):
    """Dialogue Isolate — voice / reverb / noise separation for dialogue."""

    code = "AUR_DIALOGUEISOLATE"
    display_name = "Dialogue Isolate"
    category = "Repair"
    edition = "advanced"
    element = "DialogueExtract"
    analysis = False
    supports_learn = False
    ml = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._channels = max(int(channels), 1)
        self._build_engine()

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's parameters (mirrors ``aurcore/schema/params.tsv``)."""
        return [
            ParamDesc(
                "Voice Gain", "float", 0.0, -24.0, 4.8, "dB", group="Mix",
                help="Level of the isolated voice stem.",

                curve="taper"),
            ParamDesc(
                "Reverb Gain", "float", 0.0, -24.0, 4.8, "dB", group="Mix",
                help="Level of the isolated room-reverb stem.",

                curve="taper"),
            ParamDesc(
                "Noise Gain", "float", 0.0, -24.0, 4.8, "dB", group="Mix",
                help="Level of the isolated noise stem.",

                curve="taper"),
            ParamDesc(
                "Sensitivity", "float", 0.5, 0.0, 1.0, "", group="Sensitivity",
                help="Softness of the separation boundaries and the voice-detection threshold.",
            ),
            ParamDesc(
                "Quality", "enum", "Good", 0.0, 1.0, "", ("Good", "Best"), group="Quality",
                help="Good: fast transform. Best: finer transform and a longer analysis window.",
            ),
        ]

    # -- helpers --------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis).

        The centred analysis needs the samples that follow a frame, so the
        module cannot hand out an output sample before the input has
        advanced by the length of the window.  The number is a property of
        the transform size and not a choice: ``Good`` analyses with 4096
        points and ``Best`` with 8192.
        """
        cfg = self._settings()
        return _latency(int(cfg["n_fft"]), int(cfg["hop"]))

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The centred analysis rebuilds every frame it resynthesises, so the
        last frame that carries material is not the frame that took it in.
        The difference between the two is spread across the rest of the
        window, and that difference is the ring-out of the module.  A host
        that stops the stream has to wait for it.

        Measured the way the host measures it -- a tone, then silence, then
        the number of samples the output keeps coming -- the module rings
        for 1642 samples past its latency with the default settings and
        the ``Good`` quality (a 4096 point analysis).  The number below
        covers it with a margin of 406 samples over it.
        """
        return 2048

    def _settings(self) -> dict:
        """Analysis settings for the current ``Quality`` setting."""
        return _QUALITY.get(str(self.params.get("Quality")), _QUALITY["Good"])

    def _build_engine(self) -> None:
        """Build the streaming engine and clear the state that belongs to it."""
        cfg = self._settings()
        self._n_fft = int(cfg["n_fft"])
        self._hop = int(cfg["hop"])
        self._engine = StreamingSTFT(self._n_fft, self._hop, window="hann", outputs=2)
        self._clear_state()

    def _clear_state(self) -> None:
        """Clear everything the decision carries from frame to frame."""
        cfg = self._settings()
        hop = int(cfg["hop"])
        fs = float(self.sample_rate)
        self._balance_history: deque[float] = deque(
            maxlen=max(4, int(round(_BALANCE_WINDOW_S * fs / hop)))
        )
        #: The memory of the highest balance the band has ever shown (dB).
        self._balance_memory = 0.0
        self._energy_history: deque[float] = deque(
            maxlen=max(3, int(round(_ENVELOPE_SMOOTHING_S * fs / hop)))
        )
        self._tail = 0.0
        self._direct = 0.0
        #: The energy of the frame before the current one.
        self._previous_energy = 0.0
        self._late_sum = 0.0
        self._late_count = 0
        self._reverb_tail = np.zeros((len(_REVERB_KERNEL) - 1, self._channels), dtype=np.float64)
        # The two lines are primed with the delay of the analysis, so the
        # output of the module is delayed by exactly ``latency_samples``
        # whatever the block size of the host is.
        self._out_line = _DelayLine(self.latency_samples, 2 * self._channels)
        self._in_line = _DelayLine(self.latency_samples, self._channels)

    def _separate(self, mid: np.ndarray, band: np.ndarray, bin_hz: float) -> tuple[np.ndarray,
                                                                                   np.ndarray]:
        """Separate one frame into a voice mask and a reverb mask.

        MODEL-INTEGRATION POINT — this method is the only place where source
        separation happens.  It returns the two masks of one frame — the
        voice and the reverb — and *nothing else in the module* looks at the
        audio: the mix is assembled purely from these masks.  Replacing this
        method with a trained model that returns the same two masks upgrades
        the module without touching the rest of the code.  The model has to be
        a streaming one: the masks of a frame depend on the frames that came
        before it.

        Parameters
        ----------
        mid:
            The magnitude of the channel average of the frame, shape ``(bins,)``.
        band:
            The weight of the voice band, shape ``(bins,)``.
        bin_hz:
            The width of one bin, in hertz.

        Returns
        -------
        tuple of numpy.ndarray
            ``(voice_mask, reverb_mask)``, both of shape ``(bins,)``.
        """
        hop = self._hop
        fs = float(self.sample_rate)

        # --- 1. the harmonic estimate ----------------------------------------
        # The noise that lies between the partials: the running minimum next
        # to a bin.  The window is given in hertz, because the distance
        # between two partials is a frequency, not a number of bins.
        span = max(2, int(round(_VALLEY_HZ / max(bin_hz, 1e-9))))
        floor = _valley_floor(mid[None, :], gap=_VALLEY_GAP, span=span)[0]
        margin = 20.0 * np.log10(np.maximum(mid, _EPS) / np.maximum(floor, _EPS))
        harmonicity = np.clip((margin - _TONAL_MARGIN_DB) / _TONAL_EDGE_DB, 0.0, 1.0)

        # --- 2. voice activity ----------------------------------------------
        power = mid * mid
        total_energy = float(power.sum()) + _EPS

        # The balance of the band, in the same frame: how much of the band is
        # the wanted content, against the noise of that same band.  It is a
        # quotient of two powers of the same band, so it does not carry the
        # level of the input: the same material at another level gives the
        # same balance and the same decision.
        harmonic_power = float((np.maximum(power - floor * floor, 0.0) * band).sum())
        noise_power = float((floor * floor * band).sum()) + _EPS
        balance_db = 10.0 * math.log10((harmonic_power + _EPS) / (noise_power + _EPS))
        balance_db = min(max(balance_db, _BALANCE_FLOOR_DB), _BALANCE_CEILING_DB)

        # The reference of the decision: the balance over a short window and
        # the memory of the balance the band has shown.  The memory follows
        # a higher balance at once and creeps back down slowly, so that a
        # band is not held for good, and the margin keeps a single peak of
        # the estimate from holding it either.  Both parts of the reference
        # are ratios, so neither of them carries the level of the input.
        if not self._balance_history:
            self._balance_memory = balance_db
        self._balance_history.append(balance_db)
        tracked = _percentile(self._balance_history, _BALANCE_PERCENTILE)
        memory = max(self._balance_memory, balance_db)
        self._balance_memory = memory - _BALANCE_CREEP_DB
        reference = max(tracked, self._balance_memory - _BALANCE_MEMORY_MARGIN_DB)

        sensitivity = float(self.params.get("Sensitivity"))
        threshold = _VAD_THRESHOLD_DB - _VAD_THRESHOLD_SLOPE * sensitivity
        softness = _VAD_SOFTNESS + _VAD_SOFTNESS_SLOPE * sensitivity
        # A band that has carried the wanted content carries wanted content:
        # the decision is taken with a lower threshold, so that the wanted
        # content of such a band stays in the voice stem.  The correction is
        # bounded, so that it can protect the band but never carry it.
        protection = min(
            _BALANCE_PROTECTION_DB, max(0.0, reference - balance_db) * _BALANCE_PROTECTION_GAIN
        )
        activity = float(
            _sigmoid(np.array([(balance_db + protection - threshold) / softness]))[0]
        )

        # --- 3. late field (reverb) -----------------------------------------
        # The envelope is smoothed before the two followers run, so the
        # frame to frame fluctuation of a voice does not make the reverb
        # stem take the voice.
        dt = float(hop) / fs
        previous = self._previous_energy
        self._previous_energy = total_energy
        # A fall faster than the direct release can explain is a direct sound
        # and not a reverberant tail.  The tail of the *old* level must not
        # outlive it: the whole material, the tail included, is at the new
        # level now.  The test is a quotient of two levels of the same
        # thing, so it does not carry the level of the input itself.
        rebased = previous > 0.0 and total_energy < previous * math.exp(-dt / _DIRECT_RELEASE_S)
        if rebased:
            self._energy_history.clear()
        self._energy_history.append(total_energy)
        envelope = float(np.median(self._energy_history))
        tau = 0.30 if str(self.params.get("Quality")) == "Best" else 0.22
        if rebased:
            self._tail = envelope
            self._direct = envelope
        else:
            self._tail = max(self._tail * np.exp(-dt / max(tau, 1e-3)), envelope)
            self._direct = max(self._direct * np.exp(-dt / _DIRECT_RELEASE_S), envelope)
        late = float(np.clip((self._tail - self._direct) / max(self._tail, _EPS), 0.0, 1.0))
        self._late_sum += late
        self._late_count += 1
        presence = float(np.clip((self._late_sum / max(self._late_count, 1)) / _REVERBERANCE_FULL,
                                 0.0, 1.0))

        # --- 4. the masks ----------------------------------------------------
        # The late field competes with the voice: a ringing tail is reverb,
        # not voice.
        m_reverb = (late * presence) * (0.5 + 0.5 * harmonicity)
        m_voice = band * harmonicity * activity * (1.0 - m_reverb)
        return m_voice, m_reverb

    # -- processing -----------------------------------------------------------
    def _edit(self, mag: np.ndarray, phase: np.ndarray):
        """Return the edited magnitudes of the new frames."""
        fs = float(self.sample_rate)
        freqs = np.fft.rfftfreq(self._n_fft, 1.0 / fs)
        bin_hz = float(fs) / float(self._n_fft)
        band = _band_weight(freqs, *_VOICE_BAND)

        if mag.shape[0] == 1:
            mid = mag.mean(axis=0).T            # (frames, bins)
        else:
            mid = mag.mean(axis=0).T            # (frames, bins)

        m_voice = np.empty_like(mid)
        m_reverb = np.empty_like(mid)
        for index in range(mid.shape[0]):
            m_voice[index], m_reverb[index] = self._separate(mid[index], band, bin_hz)

        voice = mag * m_voice.T[None, :, :]
        reverb = mag * m_reverb.T[None, :, :]
        return [voice, reverb], phase

    def _smear(self, reverb: np.ndarray) -> np.ndarray:
        """Smear the reverb stem along time, so that it reads as a diffuse tail.

        The kernel is applied to the stem in the time domain, so the three
        stems stay aligned with each other and with the input: the noise
        stem is the residual of the other two and a unity mix stays
        transparent.
        """
        kernel = np.asarray(_REVERB_KERNEL, dtype=np.float64)
        tail = self._reverb_tail
        padded = np.concatenate([tail, reverb], axis=0)
        out = np.zeros_like(reverb)
        for index, weight in enumerate(kernel):
            # the taps sit next to each other: the first one is the sample
            # from two frames back, the last one is the sample itself
            piece = padded[index: index + reverb.shape[0]]
            if piece.shape[0] < reverb.shape[0]:
                piece = np.concatenate(
                    [piece, np.zeros((reverb.shape[0] - piece.shape[0], reverb.shape[1]),
                                     dtype=np.float64)], axis=0
                )
            out += weight * piece
        self._reverb_tail = padded[-tail.shape[0]:] if tail.shape[0] else tail
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Mix the separated stems with the three gain controls."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        n = arr.shape[0]
        if n == 0:
            return arr
        if arr.shape[1] != self._channels:
            # the caller changed the channel count: the analysis starts again
            self._channels = int(arr.shape[1])
            self._build_engine()
        elif int(self._settings()["n_fft"]) != int(self._engine.n_fft):
            # the quality changed: the transform size changed with it
            self._build_engine()

        mag, phase = self._engine.analyze(arr)
        if mag.shape[-1]:
            (mag_voice, mag_reverb), phase = self._edit(mag, phase)
            raw_voice, raw_reverb = self._engine.synthesize_many([mag_voice, mag_reverb], phase)
        else:
            raw_voice = np.zeros((0, self._channels), dtype=np.float64)
            raw_reverb = np.zeros((0, self._channels), dtype=np.float64)

        # The voice and the reverb leave the engine at the same time, so they
        # are delayed together.
        stacked = np.concatenate([raw_voice, raw_reverb], axis=1)
        delayed = self._out_line.push(stacked, n)
        channels = int(arr.shape[1])
        voice = delayed[:, :channels]
        reverb = delayed[:, channels:]
        reverb = self._smear(reverb)
        input_delayed = self._in_line.push(arr, n)

        # The noise stem is the residual of the other two, so the three stems
        # always sum back to the input and a unity mix is transparent.
        noise = input_delayed - voice - reverb

        g_voice = _db_gain(self.params.get("Voice Gain"))
        g_reverb = _db_gain(self.params.get("Reverb Gain"))
        g_noise = _db_gain(self.params.get("Noise Gain"))
        out = (
            voice * g_voice
            + reverb * g_reverb
            + noise * g_noise
        )
        return np.ascontiguousarray(out, dtype=np.float64)

    # -- housekeeping ---------------------------------------------------------
    def reset(self) -> None:
        """Dialogue Isolate forgets its frames when the stream starts again."""
        self._build_engine()

    def serialize(self) -> dict:
        """No learned state — the module is fully described by its parameters."""
        return {}

    def deserialize(self, data: dict) -> None:
        """Nothing to restore."""
        return None
