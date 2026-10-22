"""
aurcore.modules.de_click — transient detection and repair.

This module hosts the shared transient-repair engine used by several Auralis
modules:

* :class:`DeClick`      -- ``AUR_DECLICK`` (broadband / multi-band click repair)
* :class:`DeCrackle`    -- ``AUR_DECRACKLE`` (dense low level transients)
* :class:`MouthDeClick` -- ``AUR_MOUTHDECLICK`` (band limited mouth clicks)

The engine itself is :class:`TransientRepair`.  It detects impulsive
artefacts with four complementary detectors (high order difference, spectral
flux, amplitude-envelope jump and derivative sign-change density),
normalises them against a running robust background of the material and
removes them by replacing the corrupted spectral region with content
interpolated from the surrounding healthy frames.  The partials around an
event are rebuilt with their measured phase, so the phase advance across
the replaced span is the partial's own advance and a stationary partial
survives the repair.

What a transient is
-------------------
The detectors find candidates, and a candidate is only an artefact when it
is a **transient**: a change over a very short window, not a steady one.
The material behind a click is the material that was there in front of it;
the material behind a splice, a step or a cut is not, and a change that
stays is the programme.  The engine therefore asks every candidate two
questions before it repairs it:

* does the **level** of the material on the two sides of the event differ
  by more than 3 dB?  A click does not change the level of the material, a
  step changes it for good.
* where the material is **tonal**, do the partials in front of the event
  continue behind it?  The partial model of the material in front of the
  event is evaluated on the material behind it, and the share it explains
  is the measure.  A click leaves the partials alone; a splice moves
  them, and the repair of a splice writes the phase of the material in
  front of it over the material behind it, which damages the programme.

A candidate that fails the rule is left alone.  The rule is what keeps a
steady tone -- and the phase splices of a looping buffer -- bit exact.

The DSP primitives the engine needs are implemented in this file so the module
stays self contained and does not depend on the shared ``aurcore.dsp``
package.

The engine is driven on a fixed grid
------------------------------------
:class:`TransientRepair` works on the material it is handed: it measures
the background of its detectors over the block and repairs what it finds
in it.  A block of 64 samples therefore sees a different world than a
block of 4096, and the answer of the module depends on where the block
boundary of the host happened to fall.

:mod:`aurcore.modules.transient_stream` drives the engine on a grid of
windows that does not depend on the block size of the host, and measures
the background of the detectors over a fixed length of material.  The
De-Click module is built on that grid (see :class:`DeClick`), so a host
that renders in small blocks finds the same click as one that renders the
whole file in a single block, and the module declares the latency it
gains.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

from .. import registry
from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..params import ParamDesc, desc
from .base import Module

__all__ = [
    "Algorithm",
    "LowLatencyStream",
    "TransientSettings",
    "TransientRepair",
    "Stft",
    "DeClick",
]


# ---------------------------------------------------------------------------
# DSP primitives
# ---------------------------------------------------------------------------


def hann_window(n: int) -> np.ndarray:
    """Return a periodic Hann window of length ``n``."""
    n = int(max(1, n))
    return np.hanning(n + 1)[:n]


def moving_average(x: np.ndarray, size: int) -> np.ndarray:
    """Centred moving average along the first axis (edge padded)."""
    x = np.asarray(x, dtype=np.float64)
    if size <= 1:
        return x.copy()
    k = int(size) if int(size) % 2 == 1 else int(size) + 1
    pad = k // 2
    xp = np.pad(x, [(pad, pad)] + [(0, 0)] * (x.ndim - 1), mode="edge")
    cs = np.cumsum(xp, axis=0, dtype=np.float64)
    cs = np.concatenate([np.zeros((1,) + cs.shape[1:], dtype=np.float64), cs], axis=0)
    return (cs[k:] - cs[:-k]) / float(k)


def trailing_mean(x: np.ndarray, size: int) -> np.ndarray:
    """Causal (trailing) moving average along the first axis."""
    x = np.asarray(x, dtype=np.float64)
    if size <= 1:
        return x.copy()
    cs = np.cumsum(x, axis=0, dtype=np.float64)
    zero = np.zeros((1,) + cs.shape[1:], dtype=np.float64)
    cs = np.concatenate([zero, cs], axis=0)
    idx = np.arange(x.shape[0])
    start = np.maximum(0, idx + 1 - int(size))
    num = cs[idx + 1] - cs[start]
    den = (idx + 1 - start).astype(np.float64)
    if x.ndim > 1:
        den = den[:, None]
    return num / den


class Stft:
    """Weighted overlap-add short time Fourier transform.

    The analysis and synthesis window is the same periodic Hann window, and
    the overlap-add is normalised by the summed window power, so the transform
    pair is an exact identity (to machine precision) wherever the spectrum is
    left untouched.
    """

    def __init__(self, sample_rate: int, n_fft: int = 1024, hop: int | None = None):
        self.sample_rate = int(sample_rate)
        self.n_fft = int(n_fft)
        self.hop = int(hop) if hop else max(1, self.n_fft // 4)
        self.window = hann_window(self.n_fft)
        self.pad = self.n_fft
        self._w2 = self.window ** 2

    # -- geometry ------------------------------------------------------------
    def frame_count(self, length: int) -> int:
        """Number of analysis frames produced for a signal of ``length``."""
        return 1 + (int(length) + 2 * self.pad) // self.hop

    def frame_span(self, index: int) -> tuple[int, int]:
        """Input-sample span covered by frame ``index``."""
        start = index * self.hop - self.pad
        return start, start + self.n_fft

    def frames_for_range(self, start: int, end: int, length: int) -> tuple[int, int]:
        """Index range of the frames whose window overlaps ``[start, end)``."""
        first = int(np.floor((start + self.pad - self.n_fft) / self.hop)) + 1
        last = int(np.ceil((end + self.pad) / self.hop)) - 1
        first = max(0, first)
        last = min(self.frame_count(length) - 1, last)
        if last < first:
            return first, first
        return first, last + 1

    def interior_frames(self, length: int) -> tuple[int, int]:
        """First and last frame whose window lies completely inside the signal.

        The frames outside that range see the zero padding, not the material,
        so they carry no evidence about the signal itself.
        """
        first = int(np.ceil(self.pad / self.hop))
        last = int(np.floor((int(length) + self.pad - self.n_fft) / self.hop))
        last = min(last, self.frame_count(int(length)) - 1)
        return first, last

    def bin_hz(self) -> float:
        """Frequency resolution of the transform in Hz."""
        return self.sample_rate / float(self.n_fft)

    def bins_for_band(self, low_hz: float, high_hz: float) -> tuple[int, int]:
        """Bin range covering ``[low_hz, high_hz]``."""
        step = self.bin_hz()
        lo = int(np.ceil(max(0.0, low_hz) / step))
        hi = int(np.floor(min(self.sample_rate * 0.5, high_hz) / step)) + 1
        lo = max(0, min(lo, self.n_fft // 2))
        hi = max(lo, min(hi, self.n_fft // 2))
        return lo, hi

    # -- transform -----------------------------------------------------------
    def analyse(self, x: np.ndarray) -> np.ndarray:
        """Return the complex STFT of a 1-D signal, shape ``(frames, bins)``."""
        x = np.asarray(x, dtype=np.float64).ravel()
        xp = np.concatenate(
            (np.zeros(self.pad), x, np.zeros(self.pad + self.n_fft))
        )
        frames = np.lib.stride_tricks.sliding_window_view(xp, self.n_fft)[:: self.hop]
        return np.fft.rfft(frames * self.window, axis=1)

    def synthesise(self, spec: np.ndarray, length: int) -> np.ndarray:
        """Inverse of :meth:`analyse`; returns ``length`` samples."""
        frames = np.fft.irfft(spec, n=self.n_fft, axis=1) * self.window
        nframes = frames.shape[0]
        total = (nframes - 1) * self.hop + self.n_fft
        out = np.zeros(total)
        wsum = np.zeros(total)
        for m in range(nframes):
            s = m * self.hop
            out[s : s + self.n_fft] += frames[m]
            wsum[s : s + self.n_fft] += self._w2
        out /= np.maximum(wsum, 1e-12)
        start = self.pad
        return out[start : start + int(length)]


class _RunningScale:
    """Robust running background estimate of a detector feature.

    The background tracks the *normal* level of a detector: a high quantile
    of the recent samples, not their mean.  A mean is dragged upwards by the
    very transients the detector is looking for -- the louder the click, the
    higher the mean, the smaller the score of that click.  A quantile is not
    moved by a handful of outliers, so a click is always measured against
    the material around it.

    The estimate is smoothed across blocks, so a short block does not make
    the detector jump, and it is *local* in the sense that it follows the
    level of the material instead of a global average.
    """

    __slots__ = ("_ema", "_alpha", "_value", "_quantile")

    def __init__(self, alpha: float = 0.3, quantile: float = 0.99):
        self._ema = 0.0
        self._alpha = float(alpha)
        self._value = 0.0
        self._quantile = float(quantile)

    def update(self, values: np.ndarray) -> float:
        """Fold ``values`` into the estimator and return the new scale."""
        v = np.abs(np.asarray(values, dtype=np.float64).ravel())
        if v.size:
            if v.size > 8:
                # a single quantile: a partition is a good deal cheaper than
                # the general purpose quantile machinery
                k = int(round(self._quantile * (v.size - 1)))
                level = float(np.partition(v, k)[k])
            else:
                # too few samples for a quantile: the maximum is the only
                # robust choice, and the smoothing carries it forward
                level = float(v.max())
            if self._ema <= 0.0:
                self._ema = level
            else:
                self._ema += self._alpha * (level - self._ema)
            self._value = self._ema
        return self._value

    @property
    def value(self) -> float:
        """The current scale estimate."""
        return self._value


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------


class Algorithm(str, enum.Enum):
    """Repair strategies offered by the De-Click module."""

    SINGLE_BAND = "Single Band"
    MB_PERIODIC = "MB Periodic"
    MB_RANDOM = "MB Random"
    LOW_LATENCY = "Low Latency"

    @classmethod
    def coerce(cls, value) -> "Algorithm":
        """Return a valid algorithm for ``value`` (unknown values fall back)."""
        if isinstance(value, cls):
            return value
        text = str(value)
        for member in cls:
            if member.value.lower() == text.lower():
                return member
        return cls.SINGLE_BAND


@dataclass
class TransientSettings:
    """Tunables shared by every module built on :class:`TransientRepair`."""

    algorithm: Algorithm = Algorithm.SINGLE_BAND
    sensitivity: float = 3.0
    frequency_skew: float = 0.0
    click_widening: float = 0.0
    strength: float = 1.0
    amplitude_skew: float = 0.0
    band: tuple[float, float] = (0.0, 24000.0)
    detect_band: tuple[float, float] | None = None
    max_region: int = 4000
    lookahead: int = 32
    max_regions: int = 256
    tonal_repair: bool = True
    #: How many windows of a stream are left alone.  At the start of a
    #: stream the material behind a transient does not exist and the
    #: pre-roll of the grid is silence, so the third difference of the
    #: material jumps at the place where the material starts.  A detector
    #: cannot tell that jump from a click, and the repair of it would ramp
    #: the first part of the material in.  The windows that hold the start
    #: of the material are therefore left alone, which is what the engine
    #: did before it was driven on a grid: there was no healthy frame in
    #: front of the event.
    skip_first_windows: int = 0
    #: The range of the detection threshold, as ``(low, high)``: the factor
    #: the robust background of the material has to be exceeded by, at the
    #: highest and at the lowest sensitivity.  ``None`` means the shared
    #: range of :func:`sensitivity_threshold`.
    #:
    #: The range is a property of the job of the module.  A module that
    #: repairs impulsive clicks can afford a high factor: a click stands
    #: far above the material around it.  A module that repairs dense,
    #: low level crackle cannot: a crackle stands only a few times above
    #: its own background, and a high factor would let it through.
    threshold_range: tuple[float, float] | None = None


@dataclass
class _EventModel:
    """What the partial model of the material says about one event.

    Attributes
    ----------
    start, end:
        The sample range of the event.
    a, b:
        The sample range the repair replaces: the first frame that reaches
        into the event and the last one.
    models:
        The partials of the material in front of the event, as
        ``(frequency, amplitude, phase)`` triples.  Empty when the
        material is not tonal.
    explained:
        The share of the material in front of the event the model explains.
    continues:
        Whether the material behind the event is the material that was
        there in front of it.  This is the rule of the detector: a
        transient is a short lived excursion, a splice is the programme.
    """

    start: int
    end: int
    a: int
    b: int
    models: list[tuple[float, float, float]]
    explained: float
    continues: bool = True


# Feature weights (high order difference, spectral flux, envelope jump,
# sign change density).  The first two carry the detection, the other two
# add the "this looks like a transient and not like loud material" evidence.
_WEIGHTS = (0.55, 0.35, 0.05, 0.05)

# Cap applied to every normalised detector so that one very large ratio
# cannot dominate the combined score.
_RATIO_CAP = 50.0

# The threshold at the highest and at the lowest sensitivity, in multiples
# of the robust background of the material.  See
# :func:`sensitivity_threshold`.
_THRESHOLD_LOW = 0.55
_THRESHOLD_HIGH = 3.0

#: The threshold range of the **De-Click** module.  The De-Click repairs
#: impulsive clicks, and a click stands far above the material around it:
#: the measured score of a click is 27 (the six impulses of the corpus clip
#: all reach the same score, the feature they are measured on is capped),
#: while the score of steady material never reaches 0.43.  The range of the
#: knob therefore runs from a factor of 1.2 (at Sensitivity 10, where a
#: transient that stands twice above the material is still repaired) to a
#: factor of 6.0 (at Sensitivity 0, where only the impulsive clicks are).
#:
#: The range is the module's own, and not the shared one, because the
#: modules of the family have different jobs: the De-Crackle repairs dense,
#: low level crackle, and a crackle stands only a little above its own
#: background, so it needs a lower factor.  The measurement behind it: the
#: crackle of the De-Crackle probe is found up to a factor of 1.35, 2.40 and
#: 4.50 per band, so the shared range left the De-Crackle's **Low** level
#: deaf to it (its threshold of 2.137 stood above the 1.35 of the Low
#: band) — the De-Crackle now carries its own range, and so does the
#: Mouth De-Click, whose band weighted analysis scores steady tonal
#: material far higher than this module's does.
_DECLICK_THRESHOLD_RANGE = (1.2, 6.0)

# How many events the phase compensated repair is allowed to rebuild.  The
# detector reports the strongest transients, so the cap is only reached on
# material that is damaged everywhere.
_MAX_TONAL_EVENTS = 128

#: How far the level of the material on the two sides of an event may differ
#: before the event is taken to be a change of the programme rather than a
#: transient.  A click takes the material away and puts it back; a step
#: changes the level for good.  The two levels are measured over a short
#: window, so a fade does not count as a step.
_STEP_LEVEL_DB = 3.0

#: How many samples of the level the rule compares on each side of an event.
_STEP_WINDOW = 256

#: How much of the material behind an event the partial model of the
#: material in front of it has to explain.  A model that explains the
#: material in front of the event but not the material behind it means the
#: material behind the event is a *different* material: the event is a
#: splice, and a splice is the programme and not an artefact.
_CONTINUATION_MIN = 0.6

#: How much of the material in front of an event the model has to explain
#: before the rule may decide on it.  A model that explains only a part of
#: the material is not a description of it, and the rule must not drop an
#: event on the word of a fit that did not hold.
_CONTINUATION_FIT_MIN = 0.8

#: How many samples of the material behind an event the continuation is
#: measured over.  The window is short: a long one would reach over the
#: next event and measure that one instead.
_CONTINUATION_WINDOW = 256

#: The number of samples the third difference of a window carries at its
#: edges.  The differences are "prepended" with the first sample of the
#: signal, so the second difference jumps at the edge of the window and the
#: third difference carries that jump.  The jump says something about the
#: edge and not about the material, so those samples carry no evidence.
_DIFF_EDGE = 4

#: How much louder than the material a partial model may be before the
#: model is thrown away.  A model that is measured from the material cannot
#: be louder than the material; a model that is louder is a fit that went
#: wrong.
_MAX_MODEL_LEVEL = 2.0

# The number of samples the frequency search of the partial model uses.
_SCAN_CONTEXT = 1024


def sensitivity_threshold(
    sensitivity: float, threshold_range: tuple[float, float] | None = None
) -> float:
    """Map the 0..10 sensitivity control onto a detection threshold.

    Every detector is normalised against the *robust background* of the
    material, so the score of a feature says how far the feature stands
    above that background.  The threshold is in the same unit: a candidate
    has to stand out from the material around it by that factor.

    ``threshold_range`` is the ``(low, high)`` pair of the travel: the
    factor at the highest and at the lowest sensitivity.  The shared range
    of the module is used when it is not given.  The range is a property
    of the job of the module -- see :data:`_DECLICK_THRESHOLD_RANGE` for
    the measurement behind the range of the De-Click.

    The mapping is calibrated against the measured behaviour.  The score of
    the detector was measured on three populations (the script that did it
    is ``var/w78/measure_09_scores.py``):

    * **steady material** -- a 1 kHz programme with a little noise -- never
      reaches a score of **0.43**,
    * a **0.5 ms broadband burst** on a steady programme reaches **10.0**,
    * an **impulsive click** (the six impulses of the corpus clip, the
      click of the product journey) reaches **27.0**, and it is saturated
      there: the feature it is measured on is capped.

    A higher sensitivity lowers the factor, so a quieter transient is
    accepted.  The travel of the knob has to run *through* the population
    of the transients: a range that lies below all of them (the old range
    of 0.55 to 3.0 did) cannot change the answer of the module, and a
    parameter that does not change the sound is a defect.
    """
    s = float(np.clip(sensitivity, 0.0, 10.0))
    low, high = threshold_range if threshold_range is not None else (
        _THRESHOLD_LOW,
        _THRESHOLD_HIGH,
    )
    t = s / 10.0
    return float(low**t * high ** (1.0 - t))


# ---------------------------------------------------------------------------
# The shared engine
# ---------------------------------------------------------------------------


class TransientRepair:
    """Shared transient detection and repair engine.

    Detection combines four complementary detectors:

    1. a high order difference ("click") score,
    2. the spectral flux of the short time Fourier transform,
    3. the jump of a short amplitude envelope,
    4. the density of derivative sign changes.

    Every detector is divided by a running *robust background* of the
    material, so a detector says how far a feature stands above the normal
    of the material around it.  The background is a high quantile and not a
    mean, because a mean is dragged upwards by the very transients the
    detector is looking for.

    Repair replaces the corrupted spectral region with content interpolated
    from the healthy frames that surround it and then rebuilds the partials
    around the event with their measured phase, so that the phase advance
    across the replaced span is the partial's own advance and a stationary
    partial is not cancelled by the repair.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._stft = Stft(self.sample_rate, n_fft=1024, hop=256)
        self._stft_short = Stft(self.sample_rate, n_fft=256, hop=64)
        self._scales: dict[tuple[int, int], _RunningScale] = {}
        self._pending = np.zeros((0, self.channels))
        self.analysis_buffer = np.zeros((0, self.channels))
        self._windows = 0

    # -- helpers -------------------------------------------------------------
    def reset(self) -> None:
        """Forget the running noise statistics and the latency line."""
        self._scales.clear()
        self._pending = np.zeros((0, self.channels))
        self._windows = 0

    def _scale(self, feature: int, channel: int) -> _RunningScale:
        key = (feature, channel)
        scale = self._scales.get(key)
        if scale is None:
            scale = _RunningScale()
            self._scales[key] = scale
        return scale

    # -- public API ----------------------------------------------------------
    def detect(
        self, x: np.ndarray, settings: TransientSettings, refine: bool = False
    ) -> list[tuple[int, int]]:
        """Return the detected transient regions as ``(start, end)`` ranges.

        The regions are the same ones the repair path works on, so a module
        can either use them directly or let :meth:`process` repair them.
        With ``refine`` the (deliberately wide) detector regions are narrowed
        down to the core of the transient, which is what a sample accurate
        repair needs.
        """
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        events: list[tuple[int, int]] = []
        for c in range(x.shape[1]):
            spec = self._stft.analyse(x[:, c])
            analysis, detection_spec = self._analysis_signal(x[:, c], spec, settings)
            score = self._detection_score(analysis, detection_spec, settings)
            found = self._detect_events(score, settings, analysis)
            if refine:
                found = _refine_events(x[:, c], found)
            events.extend(found)
        events.sort()
        return events[: max(1, settings.max_regions)]

    def process(
        self, x: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Repair ``x`` and return ``(repaired, removed)``.

        ``removed`` is the artefact that was taken out of the signal; it is
        what the "output ... only" render modes return.
        """
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        if x.shape[0] == 0:
            return x.copy(), np.zeros_like(x)
        if x.shape[0] < 64:
            # Too short for a meaningful analysis: pass the material through.
            return x.copy(), np.zeros_like(x)
        self._windows += 1
        if settings.skip_first_windows and self._windows <= settings.skip_first_windows:
            # The first windows of a stream carry the pre-roll of the grid,
            # which is silence.  The third difference of the material jumps
            # where the material starts, and the repair of that jump would
            # ramp the first part of the material in.  The window is left
            # alone.
            return x.copy(), np.zeros_like(x)
        if settings.algorithm is Algorithm.LOW_LATENCY:
            repaired, removed = self._process_low_latency(x, settings)
        else:
            repaired = np.empty_like(x)
            removed = np.zeros_like(x)
            for c in range(x.shape[1]):
                y, rem = self._process_channel(x[:, c], settings)
                repaired[:, c] = y
                removed[:, c] = rem
        strength = float(np.clip(settings.strength, 0.0, 1.0))
        if strength < 1.0:
            repaired = x + strength * (repaired - x)
            removed = removed * strength
        self.analysis_buffer = removed
        return repaired, removed

    # -- spectral path -------------------------------------------------------
    def _process_channel(
        self, x: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        stft = self._stft
        spec = stft.analyse(x)
        analysis, detection_spec = self._analysis_signal(x, spec, settings)
        score = self._detection_score(analysis, detection_spec, settings)
        events = self._detect_events(score, settings, analysis)
        if not events:
            return x, np.zeros_like(x)

        if settings.algorithm is Algorithm.MB_PERIODIC:
            x = self._apply_periodic_notch(x, events, settings)
            spec = stft.analyse(x)
            analysis, detection_spec = self._analysis_signal(x, spec, settings)
            score = self._detection_score(analysis, detection_spec, settings)
            events = self._detect_events(score, settings, analysis)
            if not events:
                return x, np.zeros_like(x)

        # A dense train of events leaves no healthy frame in a long analysis
        # window, so switch to a shorter one for the repair.
        gap = self._median_gap(events)
        if gap is not None and gap < stft.n_fft + stft.hop:
            stft = self._stft_short
            spec = stft.analyse(x)
        elif settings.algorithm is Algorithm.SINGLE_BAND:
            # A short analysis window replaces less clean material around the
            # transient, which matters on noisy recordings.
            stft = self._stft_short
            spec = stft.analyse(x)
        else:
            # Narrow the detector regions down to the transient itself: the
            # replacement should not touch more material than necessary.
            events = _refine_events(x, events)

        # The rule of the detector: a transient is a change over a very
        # short window, not a steady one.  The events that turn out to be
        # a change of the programme -- a step, a splice, a cut -- are not
        # artefacts, and the module leaves them alone.
        models = self._event_models(x, events, settings, stft)
        events = [
            (model.start, model.end) for model in models if model.continues
        ]
        models = [model for model in models if model.continues]
        if not events:
            return x, np.zeros_like(x)

        repaired = self._repair_spectral(x, spec, events, settings, stft)
        if settings.tonal_repair:
            repaired = self._tonal_refine(x, repaired, models)
        return repaired, x - repaired

    # -- the rule of the detector --------------------------------------------
    def _event_models(
        self,
        x: np.ndarray,
        events: list[tuple[int, int]],
        settings: TransientSettings,
        stft: Stft,
    ) -> list[_EventModel]:
        """Measure the partial model of the material around every event.

        The model is the one the phase compensation needs, and it is also
        the evidence the rule of the detector works on: a partial model
        that explains the material in front of an event has to explain the
        material behind it as well, or the material behind the event is a
        different material and the event is a splice.

        The model is measured once and used twice: by the rule and by the
        phase compensation.
        """
        out: list[_EventModel] = []
        ctx = max(512, int(0.043 * self.sample_rate))
        lo_hz = max(0.0, float(settings.band[0]))
        hi_hz = min(self.sample_rate * 0.5, float(settings.band[1]))
        level = float(np.max(np.abs(x))) if x.size else 0.0
        for index, (start, end) in enumerate(events):
            if index >= _MAX_TONAL_EVENTS:
                # The repair of the phase is capped; the events beyond the
                # cap are repaired without it, and the rule is not asked
                # of them either.
                out.append(_EventModel(int(start), int(end), 0, 0, [], 0.0, True))
                continue
            first, last = stft.frames_for_range(
                max(0, int(start) - 1), min(len(x), int(end) + 1), len(x)
            )
            if last <= first:
                out.append(_EventModel(int(start), int(end), 0, 0, [], 0.0, True))
                continue
            a = max(1, first * stft.hop - stft.pad)
            b = min(len(x) - 1, (last - 1) * stft.hop - stft.pad + stft.n_fft)
            if b - a < 2:
                out.append(_EventModel(int(start), int(end), 0, 0, [], 0.0, True))
                continue
            left = np.arange(max(0, a - ctx), a)
            right = np.arange(b, min(len(x), b + ctx))
            models, explained = _fit_partials(x, left, right, self.sample_rate, 4)
            models = [m for m in models if lo_hz <= m[0] <= hi_hz]
            # A model that is louder than the material it was measured from
            # is a fit that went wrong: the context was too short for the
            # frequency search, and the fit put its energy into a partial
            # that is not in the material.  Such a model is not used: it
            # would write a signal into the repair that is louder than the
            # material it replaces, and the rule would decide on a model
            # that is wrong.
            loudness = float(sum(m[1] for m in models))
            if not models or explained < 0.5 or level <= 0.0:
                models = []
                explained = 0.0
            elif loudness > _MAX_MODEL_LEVEL * level:
                models = []
                explained = 0.0
            continues = self._continues(x, int(start), int(end), models, explained)
            out.append(
                _EventModel(
                    int(start), int(end), int(a), int(b), models, explained, continues
                )
            )
        return out

    def _continues(
        self,
        x: np.ndarray,
        start: int,
        end: int,
        models: list[tuple[float, float, float]],
        explained: float,
    ) -> bool:
        """Is the material behind the event the material that was in front?

        This is the rule of the detector: **a transient is a change over a
        very short window, not a steady one.**  A click takes the material
        away and puts it back, so the material behind it is the material
        that was there in front of it.  A change that stays -- a step in
        the level, a splice, a cut -- is the programme and not an artefact,
        and the module leaves it alone.

        The rule has two parts:

        * the **level** on the two sides of the event may not differ by
          more than :data:`_STEP_LEVEL_DB` over a short window.  A click
          does not change the level of the material; a step changes it for
          good, and a splice in a noisy recording cannot be told apart
          from a click by its phase.
        * where the material is **tonal**, the partials in front of the
          event have to continue behind it.  The partial model of the
          material in front of the event is evaluated on the material
          behind it, and the share it explains is the measure.

        Material that is not tonal has no phase to check, and the rule
        stops at the level.
        """
        n = len(x)
        span = _STEP_WINDOW
        lo = max(0, int(start) - span)
        hi = min(n, int(end) + span)
        if hi - lo < 2 * span:
            # there is not enough material around the event to judge it
            return True
        # the level: a short window on each side, so that a fade does not
        # count as a step.  The level is the *median* of the samples and
        # not their root mean square: a click that sits in the window
        # would move the root mean square and not the median, and the
        # window is allowed to hold the neighbour of the event.
        near_left = np.arange(max(0, int(start) - span), int(start))
        near_right = np.arange(int(end), min(n, int(end) + span))
        if near_left.size and near_right.size:
            left_level = float(np.median(np.abs(x[near_left])))
            right_level = float(np.median(np.abs(x[near_right])))
            if left_level > 0.0 and right_level > 0.0:
                step = abs(20.0 * np.log10(right_level / left_level))
                if step > _STEP_LEVEL_DB:
                    return False
        # the partials: the model of the material in front of the event is
        # evaluated on the material behind it
        if not models or explained < _CONTINUATION_FIT_MIN:
            # The model of the wide context is not a description of the
            # material: the context reaches over other events, or the
            # material is not tonal at all.  The narrow context right
            # around the event is measured then: it is the material the
            # rule is about.
            models, explained = _fit_partials(
                x, near_left, near_right, self.sample_rate, 4
            )
            if not models or explained < _CONTINUATION_FIT_MIN:
                # the material is not tonal: there is no phase to check
                return True
        count = int(min(_CONTINUATION_WINDOW, n - int(end)))
        if count < 64:
            return True
        begin = int(end)
        target = x[begin : begin + count]
        fill = _eval_partials(models, begin, begin + count, self.sample_rate)
        energy = float(np.sum(target * target))
        if energy <= 0.0:
            return True
        residual = float(np.sum((target - fill) ** 2))
        return (1.0 - residual / energy) >= _CONTINUATION_MIN

    def _tonal_refine(
        self,
        x: np.ndarray,
        repaired: np.ndarray,
        models: list[_EventModel],
    ) -> np.ndarray:
        """Restore the stationary partials across the replaced span.

        This is the **phase compensation** of the repair.

        A spectral replacement fills the frames it removes with an
        interpolation of the frames on either side.  For a stationary
        partial that interpolation is a *chord* of the arc the partial
        travels, not the arc itself: the partial comes out attenuated and out
        of phase.  With the short analysis window the phase advance across
        one block is more than a whole cycle for a 1 kHz tone, so the chord
        is a diameter and the partial is cancelled almost completely --
        that is where the 8 to 10 dB of programme loss came from.

        The partials around the event are therefore measured in the time
        domain and re-synthesised over the whole replaced span.  Their
        absolute phase is the phase of the material in front of the event,
        so the phase advance across the span is the partial's own advance
        and the partial leaves the repair exactly as it entered it.

        The model is the one :meth:`_event_models` measured: the rule of the
        detector and the compensation share it.
        """
        if not models:
            return repaired
        out = repaired.copy()
        for model in models:
            if not model.models or model.explained < 0.5:
                continue
            a, b = model.a, model.b
            if b - a < 2:
                continue
            fill = _eval_partials(model.models, a, b, self.sample_rate)
            if fill.size != b - a:
                continue
            # The model is fitted to the material right before the event, so
            # it joins the original seamlessly; only a very short cross fade
            # is used to take the edge off.
            fade = min(8, max(1, (b - a) // 8))
            ramp = 0.5 * (1.0 - np.cos(np.pi * np.arange(fade) / float(fade)))
            blended = fill.copy()
            blended[:fade] = out[a : a + fade] * (1.0 - ramp) + fill[:fade] * ramp
            blended[-fade:] = (
                out[b - fade : b] * ramp[::-1] + fill[-fade:] * (1.0 - ramp[::-1])
            )
            out[a:b] = blended
        return out

    def _band_gain(self, settings: TransientSettings) -> np.ndarray:
        """Frequency weighting applied to the signal the detectors see."""
        stft = self._stft
        freqs = np.fft.rfftfreq(stft.n_fft, 1.0 / stft.sample_rate)
        band = settings.detect_band or settings.band
        lo = max(0.0, float(band[0]))
        hi = min(self.sample_rate * 0.5, float(band[1]))
        gain = np.ones_like(freqs)
        if lo > 0.0:
            width = max(lo * 0.35, 50.0)
            t = np.clip((freqs - (lo - width)) / width, 0.0, 1.0)
            gain *= t * t * (3.0 - 2.0 * t)
        if hi < self.sample_rate * 0.5:
            width = max((self.sample_rate * 0.5 - hi) * 0.35, 50.0)
            t = np.clip(((hi + width) - freqs) / width, 0.0, 1.0)
            gain *= t * t * (3.0 - 2.0 * t)
        skew = float(np.clip(settings.frequency_skew, -1.0, 1.0))
        if abs(skew) > 1e-3:
            tilt = np.power(np.maximum(freqs, 20.0) / 1000.0, skew)
            tilt /= max(float(np.max(tilt)), 1e-12)
            gain = gain * tilt
        return gain

    def _analysis_signal(
        self, x: np.ndarray, spec: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return the (weighted) signal and spectrum the detectors run on."""
        gain = self._band_gain(settings)
        if float(np.max(gain)) <= 0.0:
            return x, spec
        if np.allclose(gain, 1.0):
            return x, spec
        weighted = spec * gain
        return self._stft.synthesise(weighted, len(x)), weighted

    def _detection_score(
        self, x: np.ndarray, spec: np.ndarray, settings: TransientSettings
    ) -> np.ndarray:
        """Combine the four detectors into one normalised score per sample."""
        n = len(x)
        stft = self._stft

        # 1. high order difference (third difference)
        d1 = np.diff(x, prepend=x[0])
        d2 = np.diff(d1, prepend=d1[0])
        d3 = np.diff(d2, prepend=d2[0])
        f_click = np.abs(d3)
        # The third difference of a window carries an artefact in its first
        # samples: the "prepend" of the differences makes the second
        # difference jump at the edge of the window, and the jump of a
        # smooth signal is large compared with the third difference of the
        # material.  Those samples say something about the edge of the
        # window and not about the material, so they carry no evidence.
        if f_click.size > 2 * _DIFF_EDGE:
            f_click[:_DIFF_EDGE] = 0.0
            f_click[-_DIFF_EDGE:] = 0.0

        # 2. spectral flux
        mag = np.abs(spec)
        diff = np.diff(mag, axis=0, prepend=mag[:1])
        flux_frames = np.sum(np.maximum(diff, 0.0), axis=1)
        # The frames that reach into the zero padding see the padding, not
        # the material: their flux is an artefact of the block boundary and
        # not evidence of a transient.  They carry no score, and they are
        # left out of the background as well.
        first_frame, last_frame = stft.interior_frames(n)
        interior = flux_frames[first_frame : last_frame + 1]
        flux_frames = flux_frames.copy()
        flux_frames[: first_frame + 1] = 0.0
        flux_frames[last_frame + 1 :] = 0.0
        flux = np.repeat(flux_frames, stft.hop)[:n]
        if len(flux) < n:
            flux = np.concatenate([flux, np.zeros(n - len(flux))])
        shift = stft.n_fft // 2 - stft.hop // 2
        if shift > 0:
            # the frame centres sit half a window behind the frame starts
            flux = np.concatenate([np.zeros(shift), flux])[:n]
        elif shift < 0:
            flux = np.concatenate([flux[-shift:], np.zeros(-shift)])[:n]

        # 3. amplitude envelope jump
        env_size = max(4, int(round(0.002 * self.sample_rate)))
        env = np.sqrt(np.maximum(moving_average(x * x, env_size), 0.0))
        f_env = np.abs(np.diff(env, prepend=env[0]))
        # The envelope of the first and the last samples of the block is the
        # ramp of the block itself, not a jump in the material.
        if n > 2 * env_size:
            f_env[:env_size] = 0.0
            f_env[-env_size:] = 0.0

        # 4. derivative sign change density
        sign = np.sign(d1)
        changes = (sign[1:] != sign[:-1]).astype(np.float64)
        changes = np.concatenate([changes, [0.0]])
        dens_size = max(4, int(round(0.001 * self.sample_rate)))
        f_sign = moving_average(changes, dens_size) * dens_size

        # normalise every detector against the running background of the
        # material: the score of a feature says how many times the
        # background it is
        peak = float(np.max(np.abs(x))) if n else 0.0
        floor = max(peak * 1e-7, 1e-12)

        z_click = self._normalised(0, f_click, None, floor)
        z_flux = self._normalised(1, flux, interior, floor)
        z_env = self._normalised(2, f_env, None, floor)
        z_sign = self._normalised(3, f_sign, None, floor)

        # A feature only counts when it stands *above* the background: the
        # score of a sample says how far the material departs from its own
        # normal, not how much of it there is.  Ordinary material therefore
        # scores zero and only an outlier is counted.
        score = (
            _WEIGHTS[0] * np.maximum(z_click - 1.0, 0.0)
            + _WEIGHTS[1] * np.maximum(z_flux - 1.0, 0.0)
            + _WEIGHTS[2] * np.maximum(z_env - 1.0, 0.0)
            + _WEIGHTS[3] * np.maximum(z_sign - 1.0, 0.0)
        )
        return score

    def _normalised(
        self,
        feature: int,
        values: np.ndarray,
        background: np.ndarray | None,
        floor: float,
    ) -> np.ndarray:
        """Divide ``values`` by the running background of that feature.

        ``background`` is the part of the feature the background is measured
        from; it defaults to the whole feature.
        """
        scale = self._scale(feature, 0)
        level = scale.update(background if background is not None else values)
        return np.minimum(values / max(level, floor), _RATIO_CAP)

    def _detect_events(
        self,
        score: np.ndarray,
        settings: TransientSettings,
        x: np.ndarray | None = None,
    ) -> list[tuple[int, int]]:
        """Return the detected events as ``(start, end)`` sample ranges."""
        thr = sensitivity_threshold(settings.sensitivity, settings.threshold_range)
        if settings.amplitude_skew:
            reference = x if x is not None else score
            thr = self._skew_threshold(reference, score, settings.amplitude_skew, thr)
        mask = score > thr
        events: list[tuple[int, int]] = []
        gap = max(2, int(round(0.0005 * self.sample_rate)))
        limit = max(64, int(settings.max_region))
        for start, end in _mask_runs(mask):
            if end - start > limit:
                # Not an isolated transient: ignore the run instead of
                # replacing a huge part of the recording.
                continue
            if events and start - events[-1][1] <= gap:
                events[-1] = (events[-1][0], end)
            else:
                events.append((start, end))
        if len(events) > max(1, settings.max_regions):
            # Keep the strongest transients when there are more than we can
            # repair in one pass.
            ranked = sorted(
                events,
                key=lambda ev: float(np.max(score[ev[0] : ev[1]])) if ev[1] > ev[0] else 0.0,
                reverse=True,
            )
            events = sorted(ranked[: max(1, settings.max_regions)])
        return events

    def _skew_threshold(
        self,
        x: np.ndarray,
        score: np.ndarray,
        skew: float,
        thr: float,
    ) -> np.ndarray:
        """Bias the threshold with the local level (De-Crackle amplitude skew).

        A positive skew makes the detector less sensitive to loud events and
        more sensitive to quiet ones, a negative skew does the opposite.
        """
        window = max(8, int(round(0.005 * self.sample_rate)))
        level = np.sqrt(np.maximum(moving_average(x * x, window), 0.0))
        reference = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        rel = level / max(reference, 1e-12)
        bias = np.clip(rel - 1.0, -1.0, 1.0)
        return thr * (1.0 + float(skew) * bias)

    # -- spectral repair -----------------------------------------------------
    def _repair_spectral(
        self,
        x: np.ndarray,
        spec: np.ndarray,
        events: list[tuple[int, int]],
        settings: TransientSettings,
        stft: Stft | None = None,
    ) -> np.ndarray:
        stft = stft or self._stft
        nframes, nbins = spec.shape
        length = len(x)
        lo_bin, hi_bin = stft.bins_for_band(*settings.band)
        if hi_bin <= lo_bin:
            return x

        corrupt = np.zeros((nframes, nbins), dtype=bool)
        # Frames whose analysis window reaches outside the signal are not
        # repaired: their content is dominated by the padding.
        first_valid = int(np.ceil(stft.pad / stft.hop))
        last_valid = int(np.floor((length - stft.n_fft + stft.pad) / stft.hop)) - 1
        last_valid = min(last_valid, nframes - 1)
        for start, end in events:
            start = max(0, start - 1)
            end = min(length, end + 1)
            f0, f1 = stft.frames_for_range(start, end, length)
            f0 = max(f0, first_valid)
            f1 = min(f1, last_valid + 1)
            if f1 <= f0:
                continue
            if settings.algorithm is Algorithm.MB_RANDOM:
                mask = self._band_mask(spec, f0, f1, lo_bin, hi_bin, settings, stft)
            else:
                mask = np.zeros(nbins, dtype=bool)
                mask[lo_bin:hi_bin] = True
            if settings.click_widening > 1e-3:
                pad = int(round(settings.click_widening * 24.0))
                if pad > 0:
                    mask = _dilate(mask, pad)
            corrupt[f0:f1, :] |= mask[None, :]

        if not corrupt.any():
            return x

        out = spec.copy()
        replaced = False
        frame_bad = corrupt.any(axis=1)
        for block_start, block_end in _mask_runs(frame_bad):
            m0 = block_start - 1
            m1 = block_end
            if m0 < first_valid or m1 > last_valid:
                # No healthy frame on one side of the block (the event sits at
                # the very edge of the material): leave it alone.
                continue
            for m in range(block_start, block_end):
                w = (m - m0) / float(m1 - m0)
                sel = corrupt[m]
                if not sel.any():
                    continue
                out[m, sel] = (1.0 - w) * out[m0, sel] + w * out[m1, sel]
                replaced = True

        if not replaced:
            # Nothing was replaced: the material is returned as it came in,
            # bit for bit.  A round trip through the transform would change
            # the last bit of every sample.
            return x

        return stft.synthesise(out, length)

    def _band_mask(
        self,
        spec: np.ndarray,
        f0: int,
        f1: int,
        lo_bin: int,
        hi_bin: int,
        settings: TransientSettings,
        stft: Stft | None = None,
    ) -> np.ndarray:
        """Bins whose band carries the transient (multi-band repair)."""
        stft = stft or self._stft
        nbins = spec.shape[1]
        edges = np.geomspace(
            max(settings.band[0], 20.0),
            min(settings.band[1], stft.sample_rate * 0.5 - 1.0),
            7,
        )
        step = stft.bin_hz()
        mag = np.abs(spec)
        flux = np.maximum(np.diff(mag, axis=0, prepend=mag[:1]), 0.0)
        mask = np.zeros(nbins, dtype=bool)
        bands: list[tuple[int, int]] = []
        weights: list[float] = []
        for b in range(len(edges) - 1):
            b0 = int(np.ceil(edges[b] / step))
            b1 = int(np.floor(edges[b + 1] / step)) + 1
            b0 = max(b0, lo_bin)
            b1 = min(b1, hi_bin)
            if b1 <= b0:
                bands.append((0, 0))
                weights.append(0.0)
                continue
            bands.append((b0, b1))
            weights.append(float(flux[f0:f1, b0:b1].sum()))
        peak = max(weights) if weights else 0.0
        for (b0, b1), weight in zip(bands, weights):
            if b1 > b0 and peak > 0.0 and weight > 0.005 * peak:
                mask[b0:b1] = True
        if not mask.any():
            mask[lo_bin:hi_bin] = True
        return mask

    # -- periodic (buzz) repair ---------------------------------------------
    def _apply_periodic_notch(
        self,
        x: np.ndarray,
        events: list[tuple[int, int]],
        settings: TransientSettings,
    ) -> np.ndarray:
        """Notch a periodic narrow band component and interpolate the bins.

        The analysis uses the short transform of the engine.  The long one
        needs more material than the window of the grid holds, and a
        transform that reaches into the padding of the window sees the
        padding instead of the material.
        """
        stft = self._stft
        spec = stft.analyse(x)
        mag = np.abs(spec)
        avg = mag.mean(axis=0)
        step = stft.bin_hz()
        freqs = np.fft.rfftfreq(stft.n_fft, 1.0 / stft.sample_rate)

        candidates: list[float] = []
        # (a) periodic click train: look at the spacing of the detected events
        period = self._event_period(events)
        if period:
            candidates.append(self.sample_rate / float(period))
        # (b) persistent narrow band component (mains style buzz)
        low_bin, high_bin = stft.bins_for_band(20.0, 500.0)
        for k in range(low_bin, high_bin):
            prom = self._prominence(avg, k)
            if prom > 10.0:
                candidates.append(float(freqs[k]))
        if not candidates:
            return x

        notch_bins: set[int] = set()
        width = 2 + int(round(settings.click_widening * 3.0))
        for f0 in candidates:
            if f0 < 15.0 or f0 > 0.45 * self.sample_rate:
                continue
            k = int(round(f0 / step))
            for kk in range(max(0, k - width), min(len(freqs), k + width + 1)):
                notch_bins.add(kk)
            # prominent harmonics of the same fundamental
            for mult in (2, 3, 4):
                kh = int(round(f0 * mult / step))
                if kh >= len(freqs):
                    break
                if self._prominence(avg, kh) > 14.0:
                    for kk in range(max(0, kh - width), min(len(freqs), kh + width + 1)):
                        notch_bins.add(kk)
        if not notch_bins:
            return x

        ordered = sorted(notch_bins)
        out = spec.copy()
        for k in ordered:
            left = k - 1
            right = k + 1
            while left in notch_bins and left > 0:
                left -= 1
            while right in notch_bins and right < spec.shape[1] - 1:
                right += 1
            if left < 0 or right >= spec.shape[1]:
                continue
            w = (k - left) / float(right - left)
            out[:, k] = (1.0 - w) * spec[:, left] + w * spec[:, right]
        return stft.synthesise(out, len(x))

    @staticmethod
    def _median_gap(events: list[tuple[int, int]]) -> int | None:
        """Median distance between the detected events, in samples."""
        if len(events) < 2:
            return None
        starts = [e[0] for e in events]
        gaps = np.diff(np.asarray(starts, dtype=np.float64))
        gaps = gaps[gaps > 0]
        if gaps.size == 0:
            return None
        return int(round(float(np.median(gaps))))

    @staticmethod
    def _prominence(spectrum: np.ndarray, index: int) -> float:
        """How many dB the bin stands above the local spectral floor."""
        lo = max(0, index - 8)
        hi = min(len(spectrum), index + 9)
        floor = float(np.median(spectrum[lo:hi]))
        if floor <= 0.0:
            floor = 1e-20
        return float(20.0 * np.log10((spectrum[index] + 1e-20) / floor))

    @staticmethod
    def _event_period(events: list[tuple[int, int]]) -> int | None:
        """Estimate the period of a periodic click train, in samples."""
        if len(events) < 4:
            return None
        onsets = np.array([e[0] for e in events], dtype=np.float64)
        gaps = np.diff(onsets)
        gaps = gaps[gaps > 0]
        if gaps.size < 3:
            return None
        period = float(np.median(gaps))
        if period < 4:
            return None
        close = np.abs(gaps - period) <= max(2.0, 0.15 * period)
        if float(close.mean()) < 0.6:
            return None
        return int(round(period))

    # -- low latency path ----------------------------------------------------
    def _process_low_latency(
        self, x: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Causal repair with a minimal look ahead."""
        look = max(4, int(settings.lookahead))
        nch = x.shape[1]
        if self._pending.shape[0] < look:
            pad = look - self._pending.shape[0]
            self._pending = np.concatenate(
                [np.zeros((pad, nch), dtype=np.float64), self._pending], axis=0
            )
        elif self._pending.shape[0] > look:
            self._pending = self._pending[-look:].copy()
        stream = np.concatenate([self._pending, x], axis=0)
        n = len(stream)
        original = stream.copy()
        thr = sensitivity_threshold(settings.sensitivity, settings.threshold_range)

        for c in range(nch):
            sig = stream[:, c]
            d1 = np.diff(sig, prepend=sig[0])
            d2 = np.diff(d1, prepend=d1[0])
            d3 = np.diff(d2, prepend=d2[0])
            env = np.sqrt(
                np.maximum(trailing_mean(sig ** 2, max(4, look // 2)), 0.0)
            )
            f_click = np.abs(d3)
            f_env = np.abs(np.diff(env, prepend=env[0]))
            # The third difference and the envelope of the first samples of
            # the window carry the edge of the window and not the material:
            # the differences are prepended and the trailing average has no
            # history there.  Those samples carry no evidence.
            if f_click.size > 2 * _DIFF_EDGE:
                f_click[:_DIFF_EDGE] = 0.0
                f_click[-_DIFF_EDGE:] = 0.0
            env_span = max(4, look // 2)
            f_env[: min(env_span, f_env.size)] = 0.0
            sign = np.sign(d1)
            changes = (sign[1:] != sign[:-1]).astype(np.float64)
            changes = np.concatenate([changes, [0.0]])
            f_sign = trailing_mean(changes, max(4, look * 2)) * max(4, look * 2)

            scale = self._scale(9, c).update(f_click)
            env_scale = self._scale(10, c).update(f_env)
            sign_scale = self._scale(11, c).update(f_sign)
            floor = max(float(np.max(np.abs(sig))) * 1e-7, 1e-12)
            z = (
                0.70 * np.maximum(
                    np.minimum(f_click / max(scale, floor), _RATIO_CAP) - 1.0, 0.0
                )
                + 0.15 * np.maximum(
                    np.minimum(f_env / max(env_scale, floor), _RATIO_CAP) - 1.0, 0.0
                )
                + 0.15 * np.maximum(
                    np.minimum(f_sign / max(sign_scale, floor), _RATIO_CAP) - 1.0, 0.0
                )
            )
            merged: list[tuple[int, int]] = []
            for start, end in _mask_runs(z > thr):
                if merged and start - merged[-1][1] <= look:
                    merged[-1] = (merged[-1][0], end)
                else:
                    merged.append((start, end))
            for start, end in merged:
                # refine the event with the (precise) high order difference
                peak = float(np.max(f_click[start:end])) if end > start else 0.0
                if peak > 0.0:
                    core = np.where(f_click[start:end] > 0.3 * peak)[0]
                    if core.size:
                        start = start + int(core[0])
                        end = start + int(core[-1]) + 1
                lo = max(1, start - 1)
                hi = min(n - 1, end + 1)
                if hi <= lo:
                    continue
                # cubic Hermite between the healthy samples on both sides
                a = sig[lo - 1]
                b = sig[hi]
                m0 = 0.5 * (sig[lo - 1] - sig[max(0, lo - 3)])
                m1 = 0.5 * (sig[min(n - 1, hi + 2)] - sig[hi])
                dt = float(hi - (lo - 1))
                t = (np.arange(lo, hi) - (lo - 1)) / dt
                t2 = t * t
                t3 = t2 * t
                h00 = 2.0 * t3 - 3.0 * t2 + 1.0
                h10 = t3 - 2.0 * t2 + t
                h01 = -2.0 * t3 + 3.0 * t2
                h11 = t3 - t2
                stream[lo:hi, c] = h00 * a + h10 * (dt * m0) + h01 * b + h11 * (dt * m1)

        emitted = max(0, n - look)
        out = stream[:emitted]
        rem = (original - stream)[:emitted]
        self._pending = stream[emitted:].copy()
        if out.shape[0] < x.shape[0]:
            pad = x.shape[0] - out.shape[0]
            out = np.concatenate([out, np.zeros((pad, nch))], axis=0)
            rem = np.concatenate([rem, np.zeros((pad, nch))], axis=0)
        return out[: x.shape[0]], rem[: x.shape[0]]


# ---------------------------------------------------------------------------
# The low latency grid
# ---------------------------------------------------------------------------


class LowLatencyStream:
    """The low latency repair on a fixed grid of small windows.

    The De-Click module offers a repair that only looks a few samples
    ahead.  Such a repair cannot use the spectral path: the frames of the
    transform have to reach the material behind the transient, and that is
    thousands of samples.  This class therefore drives the time domain
    repair of the engine on a grid of small windows.

    The grid does not depend on the size of the block the host hands over.
    The window is always the same length, so the background of the
    detectors is measured over a fixed length of material and the answer
    does not depend on where the block boundary of the host fell.

    The window reaches ``_PREROLL`` samples behind the part that is
    rendered and the rest of it ahead of it, so the repair of a transient
    sees the material on both sides of it.  The delay the module adds is
    the part of the window that lies ahead of the rendered material.
    """

    #: The length of the window the detector sees.
    _WINDOW = 512

    #: How many samples of the window precede the part that is rendered.
    _PREROLL = 256

    #: How many samples of the window are rendered at a time.
    _STEP = 128

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.engine = TransientRepair(sample_rate, channels)
        self._buffer = np.zeros((0, self.channels), dtype=np.float64)
        self._fifo_repaired = np.zeros((0, self.channels), dtype=np.float64)
        self._fifo_removed = np.zeros((0, self.channels), dtype=np.float64)
        self._started = False
        self._started_output = False

    # -- contract ------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds.

        The number does not depend on the size of the block the host hands
        over: the window of the grid is always the same length.
        """
        return int(self._WINDOW - self._PREROLL)

    def reset(self) -> None:
        """Forget the running statistics and the analysis state."""
        self.engine.reset()
        self._buffer = np.zeros((0, self.channels), dtype=np.float64)
        self._fifo_repaired = np.zeros((0, self.channels), dtype=np.float64)
        self._fifo_removed = np.zeros((0, self.channels), dtype=np.float64)
        self._started = False
        self._started_output = False

    # -- processing ----------------------------------------------------------
    def process(
        self, x: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Repair ``x`` and return ``(repaired, removed)``."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr.copy(), np.zeros_like(arr)
        if not self._started:
            # the first window reaches behind the start of the stream: the
            # material that is not there is silence
            self._buffer = np.concatenate(
                [np.zeros((self._PREROLL, arr.shape[1]), dtype=np.float64), arr], axis=0
            )
            self._started = True
        else:
            self._buffer = np.concatenate([self._buffer, arr], axis=0)

        look = max(4, int(settings.lookahead))
        while self._buffer.shape[0] >= self._WINDOW:
            window = self._buffer[: self._WINDOW]
            repaired, removed = self.engine._process_low_latency(window, settings)
            # the repair of the engine is delayed by its own look ahead
            # inside the window, so the rendered part starts that much
            # further into the window
            start = self._PREROLL + look
            self._fifo_repaired = np.concatenate(
                [self._fifo_repaired, repaired[start : start + self._STEP]], axis=0
            )
            self._fifo_removed = np.concatenate(
                [self._fifo_removed, removed[start : start + self._STEP]], axis=0
            )
            self._buffer = self._buffer[self._STEP :]

        return self._collect(int(arr.shape[0]))

    def _collect(self, count: int) -> tuple[np.ndarray, np.ndarray]:
        """Take ``count`` samples out of the two lines."""
        if not self._started_output:
            self._started_output = True
            pad = np.zeros((self.latency_samples, self.channels), dtype=np.float64)
            self._fifo_repaired = np.concatenate([pad, self._fifo_repaired], axis=0)
            self._fifo_removed = np.concatenate([pad, self._fifo_removed], axis=0)
        take = min(count, int(self._fifo_repaired.shape[0]))
        repaired = self._fifo_repaired[:take]
        removed = self._fifo_removed[:take]
        self._fifo_repaired = self._fifo_repaired[take:]
        self._fifo_removed = self._fifo_removed[take:]
        if take < count:
            pad = np.zeros((count - take, repaired.shape[1]), dtype=np.float64)
            repaired = np.concatenate([repaired, pad], axis=0)
            removed = np.concatenate([removed, pad], axis=0)
        return repaired, removed


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` index ranges where ``mask`` is true."""
    mask = np.asarray(mask, dtype=bool)
    if mask.size == 0 or not mask.any():
        return []
    diff = np.diff(mask.astype(np.int8))
    starts = (np.where(diff == 1)[0] + 1).tolist()
    ends = (np.where(diff == -1)[0] + 1).tolist()
    if mask[0]:
        starts.insert(0, 0)
    if mask[-1]:
        ends.append(int(mask.size))
    return [(int(s), int(e)) for s, e in zip(starts, ends)]


def _fit_sinusoid(
    x: np.ndarray, idx: np.ndarray, freq: float, sample_rate: int
) -> tuple[np.ndarray, float]:
    """Least squares fit of ``A cos(w) + B sin(w)``; returns (coefficients, error)."""
    w = 2.0 * np.pi * freq * idx / float(sample_rate)
    matrix = np.stack([np.cos(w), np.sin(w)], axis=1)
    target = x[idx]
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    resid = target - matrix @ coef
    return coef, float(np.sum(resid * resid))


def _scan_residual(
    x: np.ndarray, idx: np.ndarray, freqs: np.ndarray, sample_rate: int
) -> np.ndarray:
    """Residual energy of a sinusoid fit for every candidate frequency."""
    w = 2.0 * np.pi * np.outer(freqs, idx) / float(sample_rate)
    cos_w = np.cos(w)
    sin_w = np.sin(w)
    target = x[idx]
    a = np.einsum("ij,ij->i", cos_w, cos_w)
    b = np.einsum("ij,ij->i", cos_w, sin_w)
    c = np.einsum("ij,ij->i", sin_w, sin_w)
    p = cos_w @ target
    q = sin_w @ target
    det = np.maximum(a * c - b * b, 1e-30)
    total = float(np.sum(target * target))
    explained = (c * p * p - 2.0 * b * p * q + a * q * q) / det
    return np.maximum(total - explained, 0.0)


def _fit_partials(
    x: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    sample_rate: int,
    max_partials: int = 4,
) -> tuple[list[tuple[float, float, float]], float]:
    """Measure the partials that surround a gap.

    Returns the list of ``(frequency, amplitude, phase)`` triples and the
    fraction of the neighbourhood energy the model explains.  An empty list
    means the material is not tonal.
    """
    if left.size < 64 or right.size < 64:
        return [], 0.0
    # both contexts must have the same length so that their spectra can be
    # combined bin by bin
    ctx_len = int(min(left.size, right.size))
    left = left[-ctx_len:]
    right = right[:ctx_len]
    window_l = np.hanning(left.size)
    window_r = np.hanning(right.size)
    spec_l = np.abs(np.fft.rfft(x[left] * window_l))
    spec_r = np.abs(np.fft.rfft(x[right] * window_r))
    freqs_l = np.fft.rfftfreq(left.size, 1.0 / sample_rate)
    freqs_r = np.fft.rfftfreq(right.size, 1.0 / sample_rate)
    spectrum = np.minimum(spec_l, spec_r)
    freqs = (freqs_l * left.size + freqs_r * right.size) / (left.size + right.size)
    bin_hz = sample_rate / float(left.size)

    work = spectrum.copy()
    # The bin at 0 Hz is the offset of the material and not a partial: a
    # partial has a phase that advances with the material, an offset does
    # not.  A fit that takes the offset for a partial puts its energy
    # into a term that is not in the material, and the model is then
    # louder than the material it was measured from.
    work[0] = 0.0
    peaks: list[tuple[float, float]] = []
    for _ in range(max(1, int(max_partials))):
        k = int(np.argmax(work))
        if work[k] <= 0.0:
            break
        delta = 0.0
        if 0 < k < work.size - 1:
            a = np.log(work[k - 1] + 1e-20)
            b = np.log(work[k] + 1e-20)
            c = np.log(work[k + 1] + 1e-20)
            den = a - 2.0 * b + c
            if abs(den) > 1e-20:
                delta = 0.5 * (a - c) / den
        peaks.append((float(freqs[k] + delta * bin_hz), float(work[k])))
        work[max(0, k - 3) : k + 4] = 0.0
    if not peaks:
        return [], 0.0
    strongest = max(p[1] for p in peaks)
    selected = [p for p in peaks if p[1] > strongest * 0.05]
    if not selected:
        return [], 0.0

    models: list[tuple[float, float, float]] = []
    # The frequency search runs on a shorter context than the final fit:
    # the search only has to find the peak, the least squares fit on the
    # whole context is what makes the model exact.
    scan_idx = left[-min(left.size, _SCAN_CONTEXT):]
    for freq, _mag in selected:
        best = freq
        span = 2.0 * bin_hz
        for _ in range(3):
            candidates = np.linspace(best - span, best + span, 25)
            residual = _scan_residual(x, scan_idx, candidates, sample_rate)
            k = int(np.argmin(residual))
            best = float(candidates[k])
            span = (candidates[-1] - candidates[0]) / 24.0
        coef, _ = _fit_sinusoid(x, left, best, sample_rate)
        amplitude = float(np.hypot(coef[0], coef[1]))
        phase = float(-np.arctan2(coef[1], coef[0]))
        models.append((best, amplitude, phase))

    idx = left
    design = np.zeros((idx.size, len(models)))
    for j, (freq, amplitude, phase) in enumerate(models):
        w = 2.0 * np.pi * freq * idx / sample_rate
        design[:, j] = amplitude * np.cos(w + phase)
    target = x[idx]
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coef
    energy = float(np.sum(target * target))
    explained = 1.0 - float(np.sum(residual * residual)) / max(energy, 1e-30)
    if explained < 0.5:
        return [], explained
    return models, explained


def _eval_partials(
    models: list[tuple[float, float, float]], start: int, end: int, sample_rate: int
) -> np.ndarray:
    """Re-synthesise the partials over the sample range ``[start, end)``."""
    gap = np.arange(start, end)
    fill = np.zeros(gap.size)
    for freq, amplitude, phase in models:
        w = 2.0 * np.pi * freq * gap / sample_rate
        fill += amplitude * np.cos(w + phase)
    return fill


def _refine_events(
    x: np.ndarray, events: list[tuple[int, int]], ratio: float = 0.25
) -> list[tuple[int, int]]:
    """Narrow detector regions down to the core of the transient."""
    if not events:
        return []
    d1 = np.diff(x, prepend=x[0])
    d2 = np.diff(d1, prepend=d1[0])
    d3 = np.diff(d2, prepend=d2[0])
    strength = np.abs(d3)
    out: list[tuple[int, int]] = []
    for start, end in events:
        start = max(0, int(start))
        end = min(len(x), int(end))
        if end <= start:
            continue
        peak = float(np.max(strength[start:end]))
        if peak <= 0.0:
            out.append((start, end))
            continue
        core = np.where(strength[start:end] > ratio * peak)[0]
        if core.size == 0:
            out.append((start, end))
            continue
        out.append((start + int(core[0]), start + int(core[-1]) + 1))
    return out


def _dilate(mask: np.ndarray, pad: int) -> np.ndarray:
    """Grow a boolean mask by ``pad`` entries on both sides."""
    if pad <= 0:
        return mask
    out = mask.copy()
    idx = np.where(mask)[0]
    if idx.size == 0:
        return out
    lo = max(0, int(idx.min()) - pad)
    hi = min(len(mask), int(idx.max()) + pad + 1)
    out[lo:hi] = True
    return out


# ---------------------------------------------------------------------------
# The De-Click module
# ---------------------------------------------------------------------------


@registry.register
class DeClick(Module):
    """Remove impulsive clicks, buzz and other transient artefacts."""

    code = "AUR_DECLICK"
    display_name = "De-Click"
    category = "Repair"
    edition = "standard"
    element = "DeClicker"
    analysis = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # The import is local because the engine of the grid imports this
        # module: an import at the top of the file would be a cycle.
        from .transient_stream import StreamingTransientRepair
        from .transient_stream import _PREROLL as _GRID_PREROLL
        from .transient_stream import _STEP as _GRID_STEP

        self.engine = StreamingTransientRepair(sample_rate, channels)
        self.low_latency = LowLatencyStream(sample_rate, channels)
        #: The windows of the grid that hold the start of the material.
        self._start_windows = max(
            1,
            -(-max(1, int(_GRID_PREROLL) - _DIFF_EDGE) // int(_GRID_STEP)),
        )
        # the difference tap: what the module took away
        self._tap = RemovedTap(max(int(channels), 1))
        #: The level of the removed clicks of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed clicks of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB

    # -- contract ------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's full parameter list."""
        return [
            desc(
                name="Algorithm",
                type="enum",
                default="Single Band",
                minimum=1,
                maximum=4,
                choices=("Single Band", "MB Periodic", "MB Random", "Low Latency"),
                group="Algorithm",
                help="Repair strategy: broadband, periodic buzz, multi-band or low latency.",
            ),
            desc(
                name="Sensitivity",
                type="float",
                default=3.0,
                minimum=0.0,
                maximum=10.0,
                group="Sensitivity",
                help="How eagerly transients are detected. Higher values catch quieter events.",
            ),
            desc(
                name="Frequency Skew",
                type="float",
                default=0.0,
                minimum=-1.0,
                maximum=1.0,
                group="Frequency Skew",
                help="Bias detection towards high (positive) or low (negative) frequencies.",
            ),
            desc(
                name="Click Widening",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=1.0,
                group="Click Widening",
                help="Widens the spectral band that is replaced around a detected event.",
            ),
            desc(
                name="Output Clicks Only",
                type="bool",
                default=False,
                group="Output",
                help="Render only the detected click signal instead of the repaired audio.",
            ),
            ParamDesc(**removed_mix_fields("clicks")),
        ]

    def settings(self) -> TransientSettings:
        """Build the engine settings from the current parameter values."""
        return TransientSettings(
            algorithm=Algorithm.coerce(self.params.get("Algorithm")),
            sensitivity=float(self.params.get("Sensitivity")),
            frequency_skew=float(self.params.get("Frequency Skew")),
            click_widening=float(self.params.get("Click Widening")),
            strength=1.0,
            band=(0.0, self.sample_rate * 0.5),
            # The pre-roll of the grid is silence, so the first windows of
            # a stream cannot tell a click from the start of the material.
            skip_first_windows=self._start_windows,
            # The De-Click repairs impulsive clicks, and a click stands far
            # above the material around it: the knob needs the range of
            # the module and not the shared one.
            threshold_range=_DECLICK_THRESHOLD_RANGE,
        )

    def _stream(self):
        """The engine that serves the current algorithm.

        The spectral algorithms run on the grid of the shared engine; the
        low latency algorithm runs on its own small grid, because a repair
        that looks a few samples ahead cannot use the spectral path.
        """
        if Algorithm.coerce(self.params.get("Algorithm")) is Algorithm.LOW_LATENCY:
            return self.low_latency
        return self.engine

    def process(self, x: np.ndarray) -> np.ndarray:
        """Repair the transients in ``x`` (shape (n, ch))."""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        repaired, removed = self._stream().process(x, self.settings())
        if bool(self.params.get("Output Clicks Only")):
            # the module renders the removed clicks on their own: the mix
            # does not apply, because the output already is the removed part
            return removed
        out = self._tap.apply(
            x, repaired, float(self.params.get(REMOVED_MIX_NAME)), self.latency_samples
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return out

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the repair).

        The repair of a transient needs the material behind it, so the
        window of the grid reaches that far into the material that follows
        it.  The number does not depend on the size of the block the host
        hands over.
        """
        return int(self._stream().latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The repair fits a partial over a window of material, and the frames
        that follow the last one still hold samples of the input.  Measured
        the way the host measures it, the output stays above -60 dBFS for
        1889 samples after the input stopped for a tone at -6 dBFS, and for
        1895 samples for a signal at the clipping ceiling.  1664 of them
        are the latency the module declares, so the tail is the 231 that
        are left.  The number carries a margin of 89 samples (1.9 ms), so
        that a small change of the repair does not break the contract.
        """
        return 320

    def reset(self) -> None:
        """Forget the running noise statistics."""
        self.engine.reset()
        self.low_latency.reset()
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB
