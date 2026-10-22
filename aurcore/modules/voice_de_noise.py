"""
aurcore.modules.voice_de_noise — Voice De-Noise.

A bank of 64 psychoacoustically spaced bands with a per band gain.

* the band edges are equally spaced on the Bark scale, so the bands follow the
  resolution of the human ear instead of a linear frequency axis;
* the noise of every band is measured on the analysis spectrum, where the
  harmonic peaks can be told apart from the noise floor, so a continuous voice
  never hides the hiss underneath it;
* the gain of a band comes from a Wiener term (only the part of the band that
  exceeds its own noise reference may survive) and from the per band group
  thresholds, capped by ``Reduction``;
* the decision is taken on the balance **inside** a band -- the noise of the
  band against the band itself -- and not on the level of the band, so the
  gain does not follow the level of the input: the same material at another
  level gets the same gain;
* the reference of a band is the balance over a short window and the lowest
  balance the band has ever shown, so that a band that carries wanted
  content is left alone;
* the band gains are interpolated onto the analysis grid and smoothed, so the
  overlap-add of the modified frames stays consistent and the wanted content
  is not smeared into the neighbouring bands.

The module is a **streaming** processor.  The analysis frames sit on a fixed
grid that does not depend on the block size of the host, the noise reference
is tracked from the frames that have already gone by, and the state of the
overlap-add is carried from one block to the next.  Two blocks of the same
signal are therefore treated the same way, no matter how the host chops the
stream: the module is block coherent.

The cost of a call is the cost of the frames it analyses, so the module gets
cheaper the smaller the blocks are — the opposite of the analysis-per-block
design it replaces.

``Removed Mix`` puts the removed noise back into the output at the amount the
user sets: 0 % leaves the denoised signal as it is, 100 % plays the removed
noise alone.  The module measures what it removed and keeps the reading, so
an interface can show a meter.

THE BED RULE
------------
The module removes noise, so it has to decide what the noise is.  Its
estimator can only take away what it believes stands above a floor, and on
a **bed that carries no wanted content** -- a hiss, a room tone, a noise
floor with nothing wanted in it -- that is not enough: the bed itself is the
unwanted content, and the module stops at the noise reduction cap instead
of taking the bed away.

The rule is the one the study of the reference hands over: **on a frame
that carries no wanted content, the estimate of the unwanted content is the
bed itself and the gain goes to the floor of the bed** -- not to some
fraction of it.  A source separator can throw a whole bed away, and a noise
remover can do the same on a bed that carries nothing wanted.

The module decides, per frame, whether the frame carries wanted content.
The measure is the one the estimator already keeps: the **balance of a band
inside itself**, how far the content of the band stands above the noise
floor of that same band.  A band that carries wanted content has a balance
well below zero, a band that carries nothing but the bed sits at zero, and
a band only counts when it carries a share of the power of the frame.

The decision is **held**, and the hold is what keeps the gain curve still.
A pause inside a word is not a bed: the module calls a bed a bed only after
the material has shown nothing wanted for a moment, and it calls it off
again as soon as the wanted content is back.  A bed that is called off too
late would take the attack of a syllable with it; a bed that is called off
too early only costs a little of the removal.

THE TWO CONFIGURATIONS
----------------------
The module works in one of two configurations, and the mode of the chain
decides which one it is (``aurcore.modules.base.Module.processing_mode``):

``realtime``
    The analysis window is 2048 samples: 42.7 ms at 48 kHz.  The module
    keeps inside the budget of a real time block and declares 2047 samples
    of look-ahead.
``offline``
    The analysis window is 4096 samples: 85.3 ms at 48 kHz.  There is no
    deadline in this mode, so the window may be as long as the job needs, and
    the longer window is what resolves a close voiced stack: the running
    minimum that estimates the spectral floor looks five bins to each side
    of a bin, and at 2048 samples those five bins are a whole period of a
    110 Hz fundamental, so the estimator lands on the skirt of the
    neighbouring harmonic instead of on the noise between two harmonics.  The
    module declares the 4095 samples of look-ahead that the longer window
    costs.

The mode is the only thing that decides: the module never switches by
itself, and the latency it declares is the latency of the configuration it
is in.  Everything else -- the bank of 64 bands, the thresholds, the hop of
the frame grid, the smoothing of the gain -- is the same in both
configurations, so the offline configuration is the same module with a
finer frequency axis.

THE TAIL
--------
A tail says how long a module keeps ringing after the input stopped, and
the rule of the family is one line: **the tail a module declares must be
the tail it has; a module may declare more than it needs, never less.**
The host cuts the sound off at the end of the declared tail, so a
declaration below the tail the module has loses the end of the wanted
content, and a declaration above it costs the host a wait it does not
need.

The tail of this module is **the end of the wanted content, spread over
the analysis window**.  The module makes no sound of its own: with the
reduction at zero it is a plain delay, and it goes quiet exactly one
latency after its input stops.  With the reduction at its default the band
that carries the wanted content stays open -- it has to, the wanted content
may not be taken with the noise -- and the overlap-add, which rebuilds a
frame from a magnitude and a phase, spreads the end of that content over
the rest of the window.  The residue is the wanted content itself: it is
the tone of the input, smeared.

The state the module keeps is not what makes it ring.  The memory of the
lowest balance holds the band that carried the wanted content open while
the residue decays, and it is worth 298 samples of the ring-out; a module
that gives the memory up when the input stops rings for 2981 samples
instead of 3279 -- which is still 387 samples more than the quiet time it
promised -- and the clean voice moves by 0.64 dB instead of 0.15 dB.  The
rest of the ring-out is the window, and the window may not be shortened
without taking the wanted content with it.

The declaration is therefore the measured ring-out of the worst case, with
a margin: 1536 samples in the realtime configuration and 3072 in the
offline one.  The measurement is in :attr:`VoiceDenoise.tail_samples`.

THE MODEL, AND WHERE IT BELONGS
-------------------------------
The module asks the core for **the model that solves the voice problem**
(:func:`aurcore.models.request`) and uses it when there is one.  The answer is
either a model or nothing, and nothing is a normal answer: the module then
does exactly what it does today, sample for sample.

A model is used in the **offline** path only.  The real time path never even
asks: the load of a model costs time and memory, and the hand written path is
the one that has to keep inside the budget of a block.  With no model the
output of the module is bit for bit the output it has always been — the model
is an addition, never a change.

The mask the model produces is a gain per frequency bin.  It is applied on
top of the gain the module's own decision makes, and it may only **deepen**
a removal the module already makes: a bin the module leaves alone keeps
its level, however sure the model is.  The model may never add gain, and it
may never take more away on its own than ``Reduction`` allows.

The context of the model — the history it reads before it can answer — **is**
latency, and the module publishes it: :attr:`VoiceDenoise.latency_samples` is
the window of the module plus the context of the model that runs.
"""
from __future__ import annotations

import io
import math

import numpy as np

from .. import models
from ..dsp.fft import irfft, to_complex
from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..dsp.stft import STFT
from ..dsp.util import db_to_lin
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module, PROCESSING_MODE_OFFLINE

__all__ = ["VoiceDenoise", "band_edges", "bark"]

_EPS = 1e-20

#: Number of bands in the filter bank.
BAND_COUNT = 64

#: Number of threshold groups the bands are split into.
GROUP_COUNT = 6

#: Lowest and highest band edge in Hz.
_LOW_HZ = 30.0
_HIGH_HZ = 16000.0

#: Analysis window and hop (samples) of the noise measurement.
_FFT_SIZE = 2048
_HOP = 256

#: Analysis window of the offline configuration (samples).
#:
#: The window of the realtime configuration is 2048 samples: 42.7 ms at
#: 48 kHz, 4.7 periods of a 110 Hz fundamental.  The offline configuration
#: has no deadline, so its window may be longer, and a longer window is what
#: the module needs to resolve a close voiced stack.  The running minimum
#: that estimates the spectral floor looks five bins to each side of a bin
#: (:data:`_VALLEY_SPAN`), and at 2048 samples those five bins are a whole
#: period of a 110 Hz fundamental: the "valley" the estimator lands in is
#: then the skirt of the neighbouring harmonic and not the noise between two
#: harmonics.  At 4096 samples the five bins are half a period, the valley is
#: the valley, and the noise reference of a band is the noise and not the
#: harmonic that stands next to it.
_OFFLINE_FFT_SIZE = 4096

#: Over-subtraction factor of the Wiener term (how far above its own noise
#: reference a band must reach before it keeps its energy).
_OVER_SUBTRACTION = 4.0

#: How often the gain curve is smoothed over the frequency axis.
_CURVE_SMOOTHING = 3

#: How close to zero the decision of a band has to be for the band to count
#: as *left alone* (dB).  A band that is attenuated by less than this is not
#: touched by the module, and the gain curve may not reach into it.
_LEFT_ALONE_DB = 0.01

#: Time constant of the gain when it opens again (seconds).  The wanted
#: content has to get through as soon as it is there, so the attack of a
#: syllable may not be taken with it.
#:
#: The one-pole covers ``1 - exp(-hop / sr / tau)`` of the distance to its
#: target in one frame, and that fraction is the bound of the project (see
#: ``qa/quality/rules_extra.py``): 65 % when the gain opens.  The constant is
#: 5.1 ms and not 5.0 ms because of that bound: 5.0 ms would cover 65.6 %.
_ATTACK_S = 0.0051

#: Time constant of the gain when it closes (seconds).  The noise may not be
#: let in and out again from frame to frame.
#:
#: The same bound applies to the closing: the gain may not cover more than
#: 10 % of the distance to its target in one frame, and 51 ms gives 9.9 %.
_RELEASE_S = 0.051

#: The same, for the gentle filter type.
_RELEASE_GENTLE_S = 0.12

#: The largest fraction of the distance to its target that the gain may
#: cover in one frame when it opens.
_GAIN_STEP_OPEN = 0.65

#: The same when the gain closes.
_GAIN_STEP_CLOSE = 0.10

#: How far a bin has to stand above the local spectral floor to count as a
#: harmonic.  With a close voiced stack the harmonics sit a few bins apart,
#: so a small margin mistakes the noise itself for a harmonic.
_TONAL_MARGIN_DB = 18.0

#: How far below the level of a band the noise floor is assumed to lie when
#: every bin of that band carries a harmonic.  The minimum-statistics tracker
#: follows the floor down, so this bound also bounds how far the tracker can
#: fall: a floor that is estimated 40 dB too low would close the gate of the
#: band for good.
_TONAL_FLOOR_RATIO = 0.01

#: Shape of the running minimum that estimates the spectral floor: the
#: number of bins that are skipped next to the bin itself and the number of
#: bins that are inspected on each side.
_VALLEY_GAP = 1
_VALLEY_SPAN = 4

#: Depth of the noise reference (seconds).  The reference is a statistic over
#: the frames that have already gone by, and the depth of that window is the
#: lag of the reference: how long it takes before the reference has seen
#: enough of the new material to change its mind.
#:
#: 29 ms is the number the project works to (see
#: ``qa/quality/rules_extra.py``).  A reference that is deeper holds the
#: gate of a band shut for longer than the reference of the study does, and
#: the study measured our decision 12 ms behind that reference.
#:
#: The depth is a time and not a number of frames: the frames of the
#: analysis are 5.33 ms long at 48 kHz and longer at a lower rate, and the
#: lag of the reference has to be the same at every rate.  It is also the
#: time the reference needs to correct a change of the level of the input:
#: the window turns over inside 200 ms.
_REFERENCE_DEPTH_S = 0.029

#: How fast the memory of the lowest balance the band has shown may creep
#: up (dB per frame).  It follows a lower balance at once and creeps back
#: up slowly, so that a band is not held for good.
_TRACKER_CREEP_DB = 0.02

#: How far the memory of the lowest balance may protect a band (dB).  The
#: memory tells a band that carries wanted content from a band that is
#: noise; the bound keeps a band that has only ever been noise from being
#: protected by a single dip of the estimate.
_STICKY_MARGIN_DB = 6.0

#: The percentile of the window that the reference takes.  A low percentile
#: follows the wanted content down at once (a syllable has to get through)
#: and ignores a dip of the noise estimate (a bin that is mistaken for a
#: harmonic takes its share with it).
#:
#: The memory of the lowest balance is what protects the wanted content,
#: so the percentile of the window does not have to be extreme: a quarter
#: of the window is allowed to be below the reference.  That keeps the gain
#: curve still (0.018 dB^2 on the case the gain rule is written for, the
#: budget is 1.0 dB^2) and the gain at its value within one analysis frame
#: after a change of the level of the input.
_TRACKER_PERCENTILE = 25.0

#: The depth the estimator may reach on a frame that carries no wanted
#: content (dB).
#:
#: The noise reduction cap (``Reduction``) is the limit of what the module
#: may take away while the wanted content is there.  On a frame that carries
#: no wanted content there is nothing to protect: the whole of the frame is
#: the unwanted content.  The rule the study hands over is that the
#: estimator goes **to the floor of the bed** there instead of stopping at
#: the cap: a source separator can throw a whole bed away, and a noise
#: remover can do the same on a bed that carries nothing wanted.
#:
#: The floor is 40 dB.  The Wiener term of the module reaches 60 dB, and
#: 40 dB is already below the level a bed of the corpus carries (the bed of
#: the corpus sits at -63 dBFS), so the bed is gone and the depth buys
#: nothing more.
_BED_FLOOR_DB = 40.0

#: How far the content of a band has to stand above the noise floor of that
#: same band before the band counts as carrying wanted content (dB).
#:
#: The measure is the balance the estimator already keeps: the reference of
#: a band is how far the content of the band stands above its own floor, so
#: a band that carries wanted content has a balance well below zero and a
#: band that carries nothing but the bed sits at zero.  Six dB is the margin
#: of the module's own gate.
_WANTED_MARGIN_DB = 6.0

#: The share of the power of a frame a band has to carry before its balance
#: counts (fraction).
#:
#: A narrow band holds one or two bins, and a single bin that is mistaken
#: for a harmonic puts the whole band 20 dB above its own floor.  Such a
#: band carries almost none of the power of the frame, so it may not decide
#: for the whole frame.
_WANTED_SHARE = 0.02

#: How long the material has to carry no wanted content before the module
#: treats a frame as a bed (seconds).
#:
#: The decision is held, and the hold is what keeps the gain curve still: a
#: pause inside a word is not a bed, so the gain of the noise band does not
#: step at every syllable.  The constant is measured, not guessed: the
#: longest stretch of the voice case that carries no wanted content is
#: 80 ms (15 analysis frames), and the hold has to be longer than that.  At
#: 70 ms the hold is too short: the gain curve of the voice case then
#: varies by 2.03 dB^2 instead of 0.02 dB^2 and the voice moves by 0.44 dB
#: instead of 0.14 dB.  At 150 ms the hold is nearly twice the longest
#: pause, and the bed of the corpus is still taken away to the floor.
_BED_HOLD_S = 0.15

#: How long the wanted content has to be back before the module calls a bed
#: off again (seconds).
#:
#: The two holds are not the same length: the module is slow to call a bed
#: and quick to call it off, because a bed that is called off too late takes
#: the attack of a syllable with it, while a bed that is called off too
#: early only costs a little of the removal.  The constant is the depth of
#: the noise reference, so a single frame that carries wanted content does
#: not call a bed off.
_BED_LEAVE_S = 0.029

#: Delay between the input and the output of the module (samples).
#:
#: The overlap-add reconstruction of a centred frame needs the frame that
#: reaches one window length into the future, so a streaming module cannot
#: emit an output sample before the input has advanced by ``_FFT_SIZE - 1``.
#: The delay is therefore a property of the analysis window, not a choice.
#: The offline configuration analyses a longer window, so it delays more:
#: the delay it declares is the delay of the configuration it is in.
_LATENCY = _FFT_SIZE - 1
_OFFLINE_LATENCY = _OFFLINE_FFT_SIZE - 1

#: How long the module keeps ringing after the input stopped (samples).
#:
#: The tail of the module is **the end of the wanted content, spread over
#: the analysis window**.  The module makes no sound of its own: with the
#: reduction at zero it is a plain delay, and it goes quiet exactly one
#: latency after its input stops.  With the reduction at its default the
#: band that carries the wanted content stays open -- it has to, the
#: wanted content may not be taken with the noise -- and the overlap-add,
#: which rebuilds a frame from a magnitude and a phase, spreads the end of
#: that content over the rest of the window.  The residue is the wanted
#: content itself: it is the tone of the input, smeared.
#:
#: Measured the way the host measures it (a tone, then silence, then the
#: number of samples the output keeps coming above -60 dBFS), the realtime
#: configuration rings for 3279 samples past the end of the input for a
#: tone at -6 dBFS and for 3404 samples for a square wave at the clipping
#: ceiling: 1232 and 1357 samples beyond the latency of 2047.  The offline
#: configuration, whose window is twice as long, rings for 6555 and 6803
#: samples: 2460 and 2708 samples beyond its latency of 4095.
#:
#: The declaration is the worst case that was measured, with a margin of
#: 179 samples (3.7 ms) and of 364 samples (7.6 ms).  The rule of the
#: family is that a module may declare more than it needs and never less:
#: the host cuts the sound off at the end of the declared tail.
_TAIL = 1536
_OFFLINE_TAIL = 3072

#: How many frames of material the module needs before its answer is final
#: (samples).
#:
#: The noise reference of the module is built from the frames that have
#: already gone by, so the first frames of a material are judged with a
#: reference that has not seen the material yet: the gate of a band is still
#: closing when the stream starts, and the level of the output is only
#: final once the reference has settled.
#:
#: The number is 8192 frames, 170.7 ms at 48 kHz.  Measured: the level of
#: the output of the module is -0.0035 dBFS after 6144 frames of a full
#: scale tone and -0.0034 dBFS after 65536, and it is 2.55 dB lower after
#: 4096 frames.  The offline configuration settles at the same length.
_SETTLING_FRAMES = 8192

#: The problem a model has to solve for this module to use one.
#:
#: The library holds one family of models per problem, and the module asks
#: for the family that separates the voice from the bed around it.  The ask
#: is made through the seam of the core (:func:`aurcore.models.request`),
#: which hands a model out in the **offline** mode only.
_MODEL_PROBLEM = "voice"

#: How much of the removal of the module has to be there before the model
#: may deepen it (dB).
#:
#: The model may only take more noise away where the module already takes
#: some away: a bin the module leaves alone keeps its level, however sure
#: the model is.  One decibel of removal opens the door; below that the
#: wanted content of the bin is at stake and the model may not touch it.
_MODEL_OPEN_DB = 1.0


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


def band_edges(
    count: int = BAND_COUNT, low_hz: float = _LOW_HZ, high_hz: float = _HIGH_HZ
) -> np.ndarray:
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
    for _ in range(80):  # the Bark scale is monotonic, so bisection is exact
        mid = 0.5 * (lo + hi)
        go_up = bark(mid) < z
        lo = np.where(go_up, mid, lo)
        hi = np.where(go_up, hi, mid)
    return 0.5 * (lo + hi)


def _valley_floor(
    x: np.ndarray, gap: int = _VALLEY_GAP, span: int = _VALLEY_SPAN
) -> np.ndarray:
    """Spectral floor from the running minimum next to a bin.

    A *median* over a wide window cannot separate the harmonics of a close
    voiced stack: with a 110 Hz fundamental and a 2048 point window the
    harmonics sit 4.7 bins apart, so every window that is wide enough to be
    robust also contains two or three peaks and the median lands on the peak
    skirts instead of the noise between them.  The running minimum ignores
    the peaks and therefore lands in the valley between two harmonics, which
    is the level the noise estimate needs.

    Parameters
    ----------
    x:
        Magnitude spectrogram in dB, shape ``(frames, bins)``.
    gap:
        Number of bins that are skipped next to the bin itself.
    span:
        Number of bins that are inspected on each side.

    Returns
    -------
    numpy.ndarray
        The floor in dB, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[None, :]
    frames, bins = arr.shape
    pad = int(span) + int(gap)
    padded = np.pad(arr, ((0, 0), (pad, pad)), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, int(span), axis=-1)
    left = view[:, :bins].min(axis=-1)
    right = view[:, int(gap) + 1 : int(gap) + 1 + bins].min(axis=-1)
    return np.minimum(left, right)


def _median_filter(x: np.ndarray, axis: int, width: int) -> np.ndarray:
    """Median filter along ``axis`` (SciPy when available).

    The module does not use this any more — the spectral floor comes from the
    running minimum of :func:`_valley_floor` — but it is the fallback of the
    other estimators of the core and it costs nothing to keep.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    try:  # pragma: no cover - depends on the environment
        from scipy.ndimage import median_filter as _mf

        size = [1] * arr.ndim
        size[int(axis) % arr.ndim] = width
        return np.asarray(_mf(arr, size=size, mode="nearest"), dtype=np.float64)
    except Exception:
        pass
    moved = np.moveaxis(arr, int(axis) % arr.ndim, -1)
    pad = width // 2
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(pad, pad)], mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    out = np.median(view, axis=-1)
    return np.moveaxis(out, -1, int(axis) % arr.ndim)


@register
class VoiceDenoise(Module):
    """Voice De-Noise — 64 band psychoacoustic noise reduction."""

    code = "AUR_VOICEDENOISE"
    display_name = "Voice De-Noise"
    category = "Repair"
    edition = "standard"
    element = "DenoiserZL"
    analysis = False
    supports_learn = True

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            desc(
                name="Processing Mode",
                type="enum",
                default="realtime",
                choices=("realtime", "offline"),
                group="Algorithm",
                help="Realtime keeps the module inside the budget of a block; offline uses the "
                "best configuration the module has and declares the look-ahead it costs.",
            ),
            desc(
                name="Optimize For",
                type="enum",
                default="Dialogue",
                choices=("Dialogue", "Music"),
                group="Algorithm",
                help="Dialogue favours speech intelligibility, Music keeps the spectrum natural.",
            ),
            desc(
                name="Filter Type",
                type="enum",
                default="Surgical",
                choices=("Surgical", "Gentle"),
                group="Algorithm",
                help="Surgical removes more, Gentle keeps more of the original texture.",
            ),
            desc(
                name="Adaptive Mode",
                type="bool",
                default=True,
                group="Adaptive",
                help="Keep tracking the noise reference while processing.",
            ),
            desc(
                name="Threshold",
                type="float",
                default=-15.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Threshold",
                help="Global threshold offset applied to every band group.",
            ),
            desc(
                name="Threshold 1",
                type="float",
                default=-40.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the highest band group.",
            ),
            desc(
                name="Threshold 2",
                type="float",
                default=-50.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the second band group.",
            ),
            desc(
                name="Threshold 3",
                type="float",
                default=-60.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the third band group.",
            ),
            desc(
                name="Threshold 4",
                type="float",
                default=-70.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the fourth band group.",
            ),
            desc(
                name="Threshold 5",
                type="float",
                default=-80.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the fifth band group.",
            ),
            desc(
                name="Threshold 6",
                type="float",
                default=-90.0,
                minimum=-90.0,
                maximum=0.0,
                unit="dB",
                group="Bands",
                help="Threshold of the lowest band group.",
            ),
            desc(
                name="Reduction",
                type="float",
                default=18.0,
                minimum=0.0,
                maximum=30.0,
                unit="dB",
                group="Reduction",
                help="Maximum gain reduction per band.",
            ),
            desc(
                name="Learn",
                type="bool",
                default=False,
                group="Learning",
                help="Capture the noise profile of the incoming audio.",
            ),
            ParamDesc(**removed_mix_fields("noise")),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._edges = band_edges()
        self._centers = np.sqrt(self._edges[:-1] * self._edges[1:])
        self._widths = self._edges[1:] - self._edges[:-1]
        self._groups = self._group_map()

        # How many frames the noise reference looks back over.  The depth is
        # a time (see ``_REFERENCE_DEPTH_S``), so the number of frames
        # follows the sample rate.  The number is rounded down: the depth
        # may not be deeper than the target at any rate.
        self._reference_frames = max(
            2,
            int(_REFERENCE_DEPTH_S * float(self.sample_rate) / float(_HOP)),
        )

        # How many frames the material has to carry no wanted content before
        # the module treats a frame as a bed.  The hold is a time (see
        # ``_BED_HOLD_S``), so the number of frames follows the sample rate.
        self._bed_hold_frames = max(
            1,
            int(_BED_HOLD_S * float(self.sample_rate) / float(_HOP)),
        )
        # How many frames the wanted content has to be back before the module
        # calls a bed off again.
        self._bed_leave_frames = max(
            1,
            int(_BED_LEAVE_S * float(self.sample_rate) / float(_HOP)),
        )

        # the difference tap: what the module took away
        self._tap = RemovedTap(max(int(channels), 1))
        #: The level of the removed noise of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed noise of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB

        # The analysis geometry of the configuration the module is in.  The
        # mode of a module is written after it is built -- the engine of the
        # chain owns it -- so the geometry is resolved again whenever the
        # mode changes.
        self._fft_size = 0
        #: The mode the module was in when the state was written.  It travels
        #: with the state, so that a session that was rendered offline says
        #: so when it is opened again; it never decides the mode the module
        #: runs in.
        self._state_mode = self.processing_mode
        self._apply_mode()
        self._init_model()

    # -- the configuration ----------------------------------------------------
    def _apply_mode(self) -> None:
        """Build the analysis geometry of the configuration the module is in.

        The two configurations differ in the length of the analysis window and
        in nothing else: the band bank, the thresholds, the hop of the frame
        grid and the smoothing of the gain are the same, so the offline
        configuration is the same module with a finer frequency axis.

        The frames of the two configurations do not sit on the same grid --
        the window of the offline configuration is longer -- so the streaming
        state cannot continue when the configuration changes: the buffers
        start again.  The captured noise profile is per band and survives,
        because the bank is the same in both configurations.
        """
        offline = self.processing_mode == PROCESSING_MODE_OFFLINE
        fft = _OFFLINE_FFT_SIZE if offline else _FFT_SIZE
        if fft == self._fft_size:
            return
        self._fft_size = fft
        self._engine = STFT(fft, _HOP, window="hann")
        self._bins = self._bin_map()
        self._bin_positions = self._bin_positions_map()

        # The band sums are taken in one pass: the analysis bins are sorted by
        # band, so the bands are the segments between the band boundaries.
        counts = np.bincount(self._bins, minlength=BAND_COUNT)
        self._band_counts = counts
        self._band_starts = np.clip(
            np.concatenate(([0], np.cumsum(counts)[:-1])), 0, self._bins.size - 1
        )
        self._empty_bands = counts == 0

        # The gain curve is interpolated on a regular grid, so the
        # interpolation of every frame is one vectorised expression.
        index = np.clip(
            np.floor(self._bin_positions).astype(np.intp), 0, BAND_COUNT - 2
        )
        self._curve_index = index
        self._curve_fraction = self._bin_positions - index

        # The window product is the denominator of the overlap-add.
        self._window_product = self._engine.win * self._engine.syn_win

        # The frames of the other configuration do not sit on this grid, so
        # the streaming state starts again.  The captured profile is per band
        # and survives the change.
        reference = getattr(self, "_reference", None)
        tracker = getattr(self, "_min_track", None)
        # The buffers come back with the channel count the module is
        # running with: a host that changed the channel count must not find
        # them at the count the module was built with.
        pending = getattr(self, "_pending", None)
        count = int(pending.shape[1]) if pending is not None else None
        self._clear_state(count)
        self._reference = reference
        self._min_track = tracker

        # The map from the bins of the module to the bins of the model
        # belongs to the geometry it was made for, so it is rebuilt with it.
        model = getattr(self, "_model", None)
        if model is not None:
            self._model_bins = self._model_bin_map(model.manifest)

    def _init_model(self) -> None:
        """Set the model state of the module up.

        The model is bound in the **offline** mode only, and it is bound
        lazily: the mode of a module is written after it is built, so the
        module asks for the model the first time it runs.
        """
        #: The model that runs, or nothing.
        self._model = None
        #: The runner of the model: it holds the history the model reads.
        self._model_runner = None
        #: The name of the model that runs ("" when the module runs its hand
        #: written path).
        self._model_name = ""
        #: The name the session asked for.  It survives a model that cannot
        #: be found: the session says which model it wanted.
        self._model_requested = ""
        #: Whether anybody chose a model at all.  A module that was never
        #: asked runs the model that solves its problem; a module that was
        #: asked for no model runs its hand written path.
        self._model_chosen = False
        #: Why there is no model, or what the running model is.
        self._model_message = ""
        #: The delay the context of the model costs, in samples.
        self._model_delay = 0
        #: The bin of the model that speaks for every bin of the module.
        self._model_bins: np.ndarray | None = None
        #: The window the model reads its frames with.
        self._model_window: np.ndarray | None = None

    def _clear_state(self, channels: int | None = None) -> None:
        """Drop the tracking state and the streaming buffers."""
        #: The captured profile of the material (a level per band).
        self._reference: np.ndarray | None = None
        #: The frozen profile of the material (a ratio per band).
        self._frozen: np.ndarray | None = None
        #: The ratio history the reference is built from.
        self._history = np.zeros((0, BAND_COUNT), dtype=np.float64)
        self._frames_seen = 0
        self._prev_gain: np.ndarray | None = None
        #: Whether the module has called the material a bed.
        self._bed_mode = False
        #: The number of frames in a row that carried no wanted content.
        self._bed_run = 0
        #: The number of frames in a row that carried wanted content.
        self._want_run = 0
        count = int(self.channels if channels is None else channels)
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB
        # input that has not been analysed yet
        self._pending = np.zeros((0, count), dtype=np.float64)
        self._pending_at = 0
        self._received = 0
        # the frame grid
        self._frame_at = 0
        # the overlap-add accumulator and its denominator
        self._ola = np.zeros((0, count), dtype=np.float64)
        self._denom = np.zeros(0, dtype=np.float64)
        self._ola_at = 0
        # the finalised output that has not been emitted yet
        self._fifo = np.zeros((0, count), dtype=np.float64)
        self._fifo_at = 0
        self._sent = 0

    def reset(self) -> None:
        """Drop the captured profile and the tracking state."""
        self._clear_state()
        if self._model_runner is not None:
            # The history the model reads is dropped with the rest of the
            # state: the module starts a new stream with a fresh model.
            self._model_runner.reset()

    def _bin_map(self) -> np.ndarray:
        """Map every analysis bin onto a band index."""
        freqs = np.fft.rfftfreq(self._fft_size, 1.0 / float(self.sample_rate))
        idx = np.searchsorted(self._edges, freqs, side="right") - 1
        return np.clip(idx, 0, BAND_COUNT - 1).astype(np.intp)

    def _group_map(self) -> np.ndarray:
        """Map every band onto a threshold group.

        ``Threshold 1`` drives the highest band group and ``Threshold 6`` the
        lowest one, so the numbers run from the treble down to the bass.
        """
        edges = np.linspace(0, BAND_COUNT, GROUP_COUNT + 1)
        groups = np.zeros(BAND_COUNT, dtype=np.intp)
        for band in range(BAND_COUNT):
            index = int(
                np.clip(np.searchsorted(edges, band + 0.5, side="right") - 1, 0, GROUP_COUNT - 1)
            )
            groups[band] = GROUP_COUNT - 1 - index
        return groups

    def _bin_positions_map(self) -> np.ndarray:
        """Fractional band index of every analysis bin.

        The band gains are interpolated on this scale, which keeps the gain
        curve smooth: a step per band would make the overlap-add of the
        modified frames inconsistent and smear the wanted content.
        """
        freqs = np.fft.rfftfreq(self._fft_size, 1.0 / float(self.sample_rate))
        positions = np.zeros(freqs.size, dtype=np.float64)
        covered = np.zeros(freqs.size, dtype=bool)

        for b in range(BAND_COUNT):
            lo = self._edges[b]
            hi = self._edges[b + 1]
            inside = (freqs >= lo) & (freqs <= hi)
            if np.any(inside):
                span = max(hi - lo, 1e-6)
                positions[inside] = b + (freqs[inside] - lo) / span
                covered[inside] = True

        # The bins outside the bank belong to the nearest edge of it.  They
        # have to be written: an unwritten entry carries whatever the
        # allocator handed out, and a single NaN there poisons the whole
        # output of the module.
        outside = ~covered
        positions[outside] = BAND_COUNT - 1.0
        positions[outside & (freqs < self._edges[0])] = 0.0

        return np.clip(positions, 0.0, BAND_COUNT - 1.0)

    # -- learning -------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Capture the per band noise profile of ``x``."""
        self._apply_mode()
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return
        mag, _ = self._engine.analyze_block(arr, center=True)
        if mag.ndim == 3:
            mag = np.mean(mag, axis=0)
        mag = np.asarray(mag, dtype=np.float64)
        if mag.size == 0:
            return
        noise, total = self._band_levels(mag.T)
        # The captured profile is a *level*: the floor the material of a band
        # may not fall under.  The history the reference is built from is a
        # *ratio*: it does not carry the level of the input.
        self._reference = np.median(noise, axis=0)
        self._frozen = None
        self._history = (noise - total)[-self._reference_frames :].copy()

    # -- the model ------------------------------------------------------------
    # The model is an addition to the offline path.  Nothing here changes
    # what the module does when there is no model: the hand written path is
    # the path, and it is bit for bit the path the module has always taken.

    @property
    def model_name(self) -> str:
        """The name of the model that runs ("" when none runs).

        This is the control of the module, and it reads the model that is
        **actually running**: it is never "the first model of the library".
        A model that cannot be found is not running, so the name is empty
        and :attr:`model_message` says why.
        """
        return self._model_name

    @property
    def model_message(self) -> str:
        """What the model seam has to say.

        The message is empty when a model runs and says what it is.  When no
        model runs it says why: the library is empty, the model that was
        asked for is not in it, the manifest does not match the engine, or
        the module is on the real time path, where a model is never loaded.
        """
        if self._model_name:
            return f"the model {self._model_name!r} runs"
        return self._model_message

    @property
    def model_description(self) -> dict:
        """The description of the running model, or nothing.

        The description is the one the core publishes for the model that is
        bound to this module, so a control that shows it shows the model
        that runs and not the first entry of the library.
        """
        if not self._model_name:
            return {}
        described = models.describe_running(self)
        if described is None:
            return {
                "name": self._model_name,
                "problem": _MODEL_PROBLEM,
                "context_samples": self._model_delay,
            }
        return described

    def select_model(self, name: str) -> str:
        """Choose the model this module runs.

        The name is the name the model publishes.  An empty name takes the
        model away and the module runs its hand written path again.

        A module that was never asked runs the model that solves its
        problem, if the library holds one.  This call is the *choice*: an
        empty name is the choice of no model, and it is kept, so a session
        that was rendered without a model reopens without one.

        The answer is a message: it says which model runs now and — when no
        model runs — why not.  A message is also kept in
        :attr:`model_message`.

        Parameters
        ----------
        name:
            The name of the model, or "" for none.

        Returns
        -------
        str
            The message that says what happened.
        """
        wanted = str(name or "").strip()
        self._model_requested = wanted
        self._model_chosen = True
        self._unbind()
        if not wanted:
            self._model_message = (
                "no model is selected; the module runs its hand written path"
            )
            return self._model_message
        if self.processing_mode != PROCESSING_MODE_OFFLINE:
            # The real time path does not load a model: the load costs time
            # and memory, and the hand written path is the one that has to
            # keep inside the budget of a block.  The choice is kept, so the
            # offline render of the same session runs the model that was
            # asked for.
            self._model_message = (
                f"the model {wanted!r} is selected; the real time path does not "
                "load a model, so the module runs its hand written path"
            )
            return self._model_message
        return self._bind(wanted)

    def _unbind(self) -> None:
        """Let go of the model that runs."""
        self._model = None
        self._model_runner = None
        self._model_name = ""
        self._model_delay = 0
        self._model_bins = None
        self._model_window = None

    def _bind(self, wanted: str) -> str:
        """Take the model called ``wanted`` into use.

        The model is asked for through the seam of the core, so the binding
        the seam keeps is the binding the module runs on: the control that
        reads the seam reads the model that runs.
        """
        library = models.open_library()
        try:
            entry = library.info(wanted)
        except models.ModelError as error:
            self._model_message = str(error)
            return self._model_message
        if not entry.readable:
            self._model_message = entry.error or (
                f"the model {wanted!r} cannot be read"
            )
            return self._model_message

        binding = models.request(self, entry.problem)
        if binding is None:
            reason = models.default_resolver().last_error(self)
            self._model_message = reason or (
                f"the library holds no model for the problem {entry.problem!r}"
            )
            return self._model_message
        if binding.name != wanted:
            # The library holds a newer model of the same problem, and the
            # newer one is the one that runs.  The control says so.
            self._model_message = (
                f"the library runs {binding.name!r} for the problem "
                f"{entry.problem!r}; the model {wanted!r} is not the one that runs"
            )
        else:
            self._model_message = ""
        self._take(binding)
        return self._model_message

    def _take(self, binding) -> None:
        """Take a bound model into use, if the module can run it."""
        manifest = binding.model.manifest
        reason = self._model_refusal(manifest)
        if reason:
            self._model_message = reason
            self._unbind()
            return
        self._model = binding.model
        self._model_runner = models.ModelRunner(binding.model)
        self._model_name = manifest.name
        self._model_delay = int(manifest.context.samples)
        self._model_bins = self._model_bin_map(manifest)
        self._model_window = self._engine_window(int(manifest.input.frame_size))

    def _model_refusal(self, manifest) -> str:
        """Why the module cannot run this model ("" when it can).

        A model that does not run at the configuration of the module is not
        loaded: the module does what it does today and says why.
        """
        if manifest.output.kind != "spectral_mask":
            return (
                f"the model {manifest.name!r} produces a {manifest.output.kind!r}; "
                "the module applies a spectral mask"
            )
        if manifest.input.channels != 1:
            return (
                f"the model {manifest.name!r} reads {manifest.input.channels} "
                "channels at a time; the module analyses the mix of the channels"
            )
        if manifest.input.hop != _HOP:
            return (
                f"the model {manifest.name!r} reads a frame every "
                f"{manifest.input.hop} samples; the module analyses every {_HOP}"
            )
        if manifest.input.frame_size > self._fft_size:
            return (
                f"the model {manifest.name!r} reads a frame of "
                f"{manifest.input.frame_size} samples; the module analyses "
                f"{self._fft_size}"
            )
        return ""

    def _engine_window(self, size: int) -> np.ndarray:
        """The analysis window of the model's own frame size."""
        return np.asarray(
            STFT(int(size), _HOP, window="hann").win, dtype=np.float64
        )

    def _model_bin_map(self, manifest) -> np.ndarray:
        """The bin of the model that speaks for every bin of the module.

        The model publishes its own bands, so the two analyses do not have to
        share a grid: the band of a bin of the module is found in the edges
        the model publishes, and the first bin of that band is the bin that
        carries the value.
        """
        edges = np.asarray(
            manifest.input.features.band_edges_hz, dtype=np.float64
        )
        rate = float(manifest.input.sample_rate)
        bins = int(manifest.input.bins)
        centres = (
            np.arange(bins, dtype=np.float64) + 0.5
        ) * rate / float(manifest.input.frame_size)
        band_of_bin = np.clip(
            np.searchsorted(edges, centres, side="right") - 1, 0, edges.size - 2
        )
        first_of_band = np.zeros(edges.size - 1, dtype=np.intp)
        for band in range(edges.size - 1):
            found = np.nonzero(band_of_bin == band)[0]
            if found.size:
                first_of_band[band] = found[0]
        freqs = np.fft.rfftfreq(self._fft_size, 1.0 / float(self.sample_rate))
        band = np.clip(
            np.searchsorted(edges, freqs, side="right") - 1, 0, edges.size - 2
        )
        return first_of_band[band]

    def _ensure_model(self) -> None:
        """Bind the model, once, when the module runs in the offline mode."""
        if self._model_runner is not None:
            return
        if self.processing_mode != PROCESSING_MODE_OFFLINE:
            # The real time path never loads a model.  The ask is not even
            # made: the load costs time and memory, and the path that has to
            # keep inside the budget of a block is the hand written one.
            self._model_message = (
                "the real time path does not load a model, so the module runs "
                "its hand written path"
            )
            return
        if not self._model_chosen:
            # Nobody chose: the module asks for the model that solves its
            # problem, and it runs its hand written path when there is none.
            self._bind_default()
        elif self._model_requested:
            self._bind(self._model_requested)

    def _bind_default(self) -> str:
        """Take the model that solves the voice problem into use."""
        binding = models.request(self, _MODEL_PROBLEM)
        if binding is None:
            reason = models.default_resolver().last_error(self)
            self._model_message = reason or (
                f"the library holds no model for the problem {_MODEL_PROBLEM!r}"
            )
            return self._model_message
        self._model_message = ""
        self._take(binding)
        return self._model_message

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Denoise a block of audio.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels).

        Returns
        -------
        numpy.ndarray
            The denoised block, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        if arr.shape[1] != self._pending.shape[1]:
            # The channel count changed: the streaming state does not apply
            # any more, so the module starts over.
            self._clear_state(int(arr.shape[1]))
        self._apply_mode()
        self._ensure_model()
        self._pending = np.concatenate((self._pending, arr), axis=0)
        self._received += arr.shape[0]
        self._run_frames()
        wet = self._emit(arr.shape[0])
        out = self._tap.apply(
            arr, wet, float(self.params.get(REMOVED_MIX_NAME)), self.latency_samples
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return out

    def _run_frames(self) -> None:
        """Analyse and re-synthesise every frame the input has made available.

        The frames sit on a grid that only depends on the number of samples
        that have gone by, never on the size of the block the host used, so
        the result does not depend on how the stream is chopped up.
        """
        half = self._fft_size // 2
        if self._received < half:
            return
        last = (self._received - half) // _HOP
        count = last - self._frame_at + 1
        if count <= 0:
            return

        start = self._frame_at * _HOP - half
        end = (self._frame_at + count - 1) * _HOP + half
        lo = max(start, 0)
        data = self._pending[lo - self._pending_at : end - self._pending_at]
        if start < 0:
            data = np.concatenate((np.zeros((-start, data.shape[1])), data), axis=0)
        view = np.lib.stride_tricks.sliding_window_view(data, self._fft_size, axis=0)
        frames = view[::_HOP][:count]
        self._frame_at += count

        # --- the analysis ---------------------------------------------------
        # The frames are (count, channels, samples): the window is applied
        # along the last axis and the transform runs along it as well.
        spec = np.fft.rfft(frames * self._engine.win, axis=2)
        mag = np.abs(spec)
        noise, total = self._band_levels(mag.mean(axis=1))
        gains = self._band_gains(total, noise)
        curve = self._gain_curve(gains)

        # --- the model ------------------------------------------------------
        # The mask of the model is a gain per bin.  It is applied on top of
        # the gain the module decided on, and the total is capped by
        # ``Reduction``: the model may take more noise away, it may never
        # take the wanted content with it, and it may never take more than
        # the cap allows.
        if self._model_runner is not None:
            curve = self._apply_model(curve, data, start, lo, count)

        # --- the synthesis --------------------------------------------------
        out_mag = mag * db_to_lin(curve)[:, None, :]
        out_frames = irfft(to_complex(out_mag, np.angle(spec)), n=self._fft_size, axis=2)
        out_frames *= self._engine.syn_win
        self._overlap_add(out_frames, start)

        # The input that no frame needs any more is released.
        keep = max(self._frame_at * _HOP - half, 0)
        drop = keep - self._pending_at
        if drop > 0:
            self._pending = self._pending[drop:].copy()
            self._pending_at = keep

    def _model_magnitudes(self, data, start, lo, count) -> np.ndarray:
        """The magnitudes the model reads, on the model's own window.

        The model's frames sit on the **same centres** as the frames of the
        module — the hop is the same — so the two analyses speak about the
        same moment of the stream.  The window is the model's own: a model
        that reads a 1024 sample frame sees 1024 samples and not the 2048
        the module analyses.

        Parameters
        ----------
        data:
            The samples the frames of the module were taken from.
        start:
            The absolute position of the first sample of the first frame.
        lo:
            The absolute position of the first sample of ``data``.
        count:
            The number of frames.

        Returns
        -------
        numpy.ndarray
            The magnitudes of the frames, shape ``(bins, frames)``.
        """
        manifest = self._model.manifest
        size = int(manifest.input.frame_size)
        window = self._model_window
        # The first window of the model, relative to the start of ``data``.
        first = (start - lo) + (self._fft_size // 2) - (size // 2)
        front = max(0, -first)
        buffer = data
        if front:
            buffer = np.concatenate(
                (np.zeros((front, data.shape[1]), dtype=np.float64), data), axis=0
            )
        view = np.lib.stride_tricks.sliding_window_view(buffer, size, axis=0)
        frames = view[first + front : first + front + count * _HOP : _HOP][:count]
        spectrum = np.fft.rfft(frames * window, axis=2)
        return np.abs(spectrum).mean(axis=1).T

    def _apply_model(self, curve, data, start, lo, count) -> np.ndarray:
        """Apply the mask of the model to the gain curve of the module.

        Parameters
        ----------
        curve:
            The gain the module decided on, in dB, shape ``(frames, bins)``.
        data, start, lo, count:
            The samples and the frame grid, as in :meth:`_model_magnitudes`.

        Returns
        -------
        numpy.ndarray
            The gain curve with the model applied, same shape as ``curve``.
        """
        magnitudes = self._model_magnitudes(data, start, lo, count)
        values = self._model_runner.process(magnitudes)
        # The mask per bin of the module, read out of the bins of the model.
        mask = np.asarray(values[self._model_bins, :], dtype=np.float64).T
        mask = np.clip(mask, 0.0, 1.0)

        cap = float(self.params.get("Reduction"))
        model_db = 20.0 * np.log10(np.maximum(mask, 1e-6))
        # The model may never add gain -- the mask is a gain between zero and
        # one -- and it may not take more away on its own than the cap
        # allows.  What it takes away is *added* to what the module decided
        # on: the two are two opinions about the same bin, and the bin is
        # only as quiet as both of them say it is.
        model_db = np.maximum(model_db, -cap)
        # The model may only deepen a removal the module already makes.  A
        # bin the module leaves alone keeps its level, however sure the
        # model is: the wanted content is never taken with the noise.
        opened = np.clip(-curve / _MODEL_OPEN_DB, 0.0, 1.0)
        return curve + model_db * opened

    def _band_levels(self, mean_mag: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Noise level and total level per band and frame.

        The noise level only counts the bins that do not carry a harmonic
        peak, so a continuous voice cannot hide the hiss underneath it.

        Parameters
        ----------
        mean_mag:
            Magnitude spectrogram of shape ``(frames, bins)``, averaged over
            the channels.

        Returns
        -------
        tuple of numpy.ndarray
            ``(noise, total)``, each of shape ``(frames, bands)`` in dB.
        """
        mag = np.asarray(mean_mag, dtype=np.float64)
        if mag.size == 0:
            empty = np.zeros((0, BAND_COUNT), dtype=np.float64)
            return empty, empty
        db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        power = np.power(10.0, db / 10.0)
        broad = _valley_floor(db)
        tonal = (db - broad) > _TONAL_MARGIN_DB
        band_power = np.add.reduceat(power, self._band_starts, axis=1)
        floor_power = np.add.reduceat(power * (~tonal), self._band_starts, axis=1)
        # A band that carries no bin at all is empty, and a band that is
        # completely harmonic still has a noise floor: it is estimated well
        # below the harmonic content.
        band_power[:, self._empty_bands] = 0.0
        floor_power[:, self._empty_bands] = 0.0
        floor_power = np.maximum(floor_power, band_power * _TONAL_FLOOR_RATIO)
        noise = 10.0 * np.log10(np.maximum(floor_power, _EPS))
        total = 10.0 * np.log10(np.maximum(band_power, _EPS))
        return noise, total

    def _carries_wanted(self, total: np.ndarray, noise: np.ndarray) -> np.ndarray:
        """Does every frame carry wanted content?

        The measure is the **balance of a band inside itself**: how far the
        content of a band stands above the noise floor of that same band.
        ``noise`` is the floor of the band and ``total`` is the level of the
        band, both read in the same frame, so the balance does not carry the
        level of the input.  A band that carries wanted content has a
        balance well below zero; a band that carries nothing but the bed
        sits at zero.

        The measure is the one the estimator already keeps -- the gate and
        the Wiener term of the module are built on the same balance -- and it
        costs one pass over the bands of the frame.

        A band only counts when it carries a share of the power of the frame.
        A narrow band holds one or two bins, and a single bin that is
        mistaken for a harmonic puts the whole band 20 dB above its own
        floor; such a band carries almost none of the power of the frame, so
        it may not decide for the whole frame.

        Parameters
        ----------
        total:
            The level of every band of every frame (dB).
        noise:
            The noise floor of every band of every frame (dB).

        Returns
        -------
        numpy.ndarray
            One flag per frame: ``True`` when the frame carries wanted
            content.
        """
        power = np.power(10.0, total / 10.0)
        frame_power = np.maximum(power.sum(axis=1, keepdims=True), _EPS)
        share = power / frame_power
        balance = noise - total
        counts = (balance < -_WANTED_MARGIN_DB) & (share > _WANTED_SHARE)
        return counts.any(axis=1)

    def _bed_frames(self, carries: np.ndarray) -> np.ndarray:
        """The frames the module treats as a bed.

        The module decides that the material carries nothing wanted after the
        material has shown it for a moment, and it decides the other way
        after the wanted content has come back for a moment.  The two holds
        are not the same length: the module is slow to call a bed and quick
        to call it off, because a bed that is called off too late takes the
        attack of a syllable with it, while a bed that is called off too
        early only costs a little of the removal.

        The decision is **latched**: once the module has called the material
        a bed, a single frame that carries wanted content does not call it
        off again.  That is what keeps the gain curve still -- the gain
        walks from one behaviour to the other through the smoothing of the
        gain, and it never jumps back.

        Parameters
        ----------
        carries:
            One flag per frame: ``True`` when the frame carries wanted
            content.

        Returns
        -------
        numpy.ndarray
            One flag per frame: ``True`` when the frame is a bed.
        """
        frames = int(carries.shape[0])
        out = np.zeros(frames, dtype=bool)
        if frames == 0:
            return out
        bed = bool(self._bed_mode)
        bed_run = int(self._bed_run)
        want_run = int(self._want_run)
        hold = max(1, int(self._bed_hold_frames))
        leave = max(1, int(self._bed_leave_frames))
        for f in range(frames):
            if bool(carries[f]):
                bed_run = 0
                want_run = min(want_run + 1, leave)
                if bed and want_run >= leave:
                    bed = False
            else:
                want_run = 0
                bed_run = min(bed_run + 1, hold)
                if (not bed) and bed_run >= hold:
                    bed = True
            out[f] = bed
        self._bed_mode = bed
        self._bed_run = bed_run
        self._want_run = want_run
        return out

    def _band_gains(self, total: np.ndarray, noise: np.ndarray) -> np.ndarray:
        """Per band gain in dB for every frame.

        The gain of a band is the smaller of

        * the Wiener term: only the part of the band that exceeds its own
          noise reference may survive, and
        * the gate: the per band group threshold decides how far above the
          reference a band may reach before it is left alone.

        ``Reduction`` caps the result.  The result is then smoothed along the
        time axis (see :meth:`_smooth_gain`).
        """
        return self._smooth_gain(self._band_targets(total, noise))

    def _band_targets(self, total: np.ndarray, noise: np.ndarray) -> np.ndarray:
        """The gain a band would have if it were applied without any smoothing.

        This is the *decision* of the module, one value per band and frame.
        The smoothing of :meth:`_smooth_gain` is what turns it into the gain
        that is really applied, and the two are kept apart so that the bound
        on the movement of the gain can be measured (see
        ``qa/quality/rules_extra.py``).
        """
        p = self.params
        reduction = float(p.get("Reduction"))
        dialogue = str(p.get("Optimize For")) == "Dialogue"
        surgical = str(p.get("Filter Type")) == "Surgical"

        # The reference of the module is the balance inside the band: how
        # far the content of a band stands above the noise of that same
        # band.  It is a difference of two levels of the same band, so it
        # does not carry the level of the input: the gain of a band is the
        # same whatever the level of the material is.
        reference = self._noise_reference(noise, total)

        # Thresholds: offsets above the band's own noise reference.  The six
        # nodes shape the threshold across the spectrum, the global Threshold
        # shifts every group at once.
        global_offset = (float(p.get("Threshold")) + 45.0) * 0.05
        offsets = np.empty(BAND_COUNT, dtype=np.float64)
        for group in range(GROUP_COUNT):
            value = float(p.get(f"Threshold {group + 1}"))
            offsets[self._groups == group] = (value + 90.0) * 0.05 + global_offset
        if dialogue:
            # Speech intelligibility lives between 1 kHz and 8 kHz.
            weight = np.exp(
                -0.5 * ((np.log2(np.maximum(self._centers, 1.0)) - 12.0) / 1.6) ** 2
            )
            offsets = offsets - 1.0 * weight
        else:
            offsets = offsets + 1.0

        # ``effective`` is how far the band stands above its own noise
        # floor: 0 dB means the band is nothing but noise, a large negative
        # number means the wanted content is far above the floor.
        effective = reference + offsets[None, :]

        # --- the bed rule ----------------------------------------------------
        # The rule the study hands over: on a frame that carries no wanted
        # content the estimate of the unwanted content is the bed itself and
        # the gain goes to the floor of the bed, instead of stopping at the
        # noise reduction cap.  A frame that carries wanted content keeps
        # the rule it has always had.
        #
        # The rule does not fight the user: a cap of 0 dB means that nothing
        # is to be removed, and the module removes nothing.
        bed = np.zeros(effective.shape[0], dtype=bool)
        if reduction > 0.0:
            bed = self._bed_frames(self._carries_wanted(total, noise))
        if bool(bed.any()):
            # The balance of a bed is zero: the whole of the band is the
            # unwanted content, so the Wiener term of the band is zero and
            # the gain of the band is the floor.
            effective = np.where(bed[:, None], 0.0, effective)

        # The gate is relative to the tracked noise floor: a band that sits
        # at its own reference carries no wanted content and may be pulled
        # down, a band that stands well above it is left alone.  The Wiener
        # term below is what protects a continuous voice, because there the
        # wanted content is far above the floor.
        gate = np.clip((effective + 6.0) / 6.0, 0.0, 1.0)

        # Wiener term with over-subtraction: the band is only allowed to keep
        # the energy that exceeds its noise reference.  The term is a ratio
        # of two powers of the same band, so it does not carry the level of
        # the input either.
        gain = np.maximum(
            1.0 - _OVER_SUBTRACTION * np.power(10.0, effective / 10.0), 0.0
        )
        wiener_depth = -10.0 * np.log10(np.maximum(gain, 1e-6))

        # The cap: the noise reduction cap on a frame that carries wanted
        # content, the floor of the bed on a frame that carries none.
        cap = reduction
        if bool(bed.any()):
            cap = np.where(bed[:, None], _BED_FLOOR_DB, reduction)

        depth = np.minimum(wiener_depth, cap) * gate
        if not surgical:
            depth = depth * 0.65
        return -depth

    def _smooth_gain(self, gains: np.ndarray) -> np.ndarray:
        """Smooth the band gains along the time axis.

        Temporal smoothing so the gains do not chatter.  The smoothing runs
        along the time axis: a gain that jumps from frame to frame would
        modulate the wanted content.  The two directions are smoothed with
        different time constants: the gain opens quickly when the wanted
        content comes back (otherwise the attack of a syllable is taken
        with it) and closes slowly, so the noise does not pump.

        The one-pole covers a fixed fraction of the distance to its target in
        one frame: ``1 - exp(-hop / sr / tau)``.  That fraction is the bound
        on the movement of the gain, and it is a rule of the project (see
        ``qa/quality/rules_extra.py``): the gain may not cover more than
        65 % of the distance to its target in one frame when it opens, and
        more than 10 % when it closes.

        The frame of the analysis is a fixed number of samples, so the
        fraction of it that the gain covers depends on the sample rate.  The
        time constant therefore never falls below the one that the bound
        asks for: the gain is never faster than the bound, at any rate.
        """
        surgical = str(self.params.get("Filter Type")) == "Surgical"
        step = _HOP / float(self.sample_rate)
        attack = max(_ATTACK_S, -step / math.log(1.0 - _GAIN_STEP_OPEN))
        release = max(
            _RELEASE_S if surgical else _RELEASE_GENTLE_S,
            -step / math.log(1.0 - _GAIN_STEP_CLOSE),
        )
        up = 1.0 - math.exp(-step / max(1e-6, attack))
        down = 1.0 - math.exp(-step / max(1e-6, release))
        if self._prev_gain is None or self._prev_gain.shape[-1] != gains.shape[-1]:
            self._prev_gain = gains[0].copy()
        previous = self._prev_gain
        smoothed = np.empty_like(gains)
        for f in range(gains.shape[0]):
            step = np.where(gains[f] > previous, up, down)
            previous = previous + step * (gains[f] - previous)
            smoothed[f] = previous
        self._prev_gain = previous.copy()
        return smoothed

    def _noise_reference(self, noise: np.ndarray, total: np.ndarray) -> np.ndarray:
        """The noise reference of every band and frame, in the ratio domain (dB).

        The reference is the balance **inside** the band: the level of the
        noise of the band against the level of the band itself.  It is a
        difference of two levels of the same band, taken in the same frame,
        so it does not carry the level of the input.  The same material at
        another level gives the same reference and the same gain: the amount
        of noise that is taken away is a property of the material and not of
        how loud the material is.

        The reference has two parts.  The first is the **window**: the
        balance of the band over the frames that have already gone by, so
        that the wanted content that is there now gets through.  The second
        is the **memory**: the lowest balance the band has ever shown.  A
        band that has been more than noise at some point carries wanted
        content, and the module leaves it alone.  The memory creeps up at a
        fixed rate, so that it does not hold a band for good, and it is
        bounded, so that a band that has only ever been noise is not
        protected by a single dip of the estimate.

        The reference of a frame is built from the frames that have already
        gone by, never from the frames that are still to come: that is what
        makes the module independent of the block size of the host.  The
        window is short (29 ms), so a change of the level of the input is
        corrected well inside 200 ms.
        """
        frames = noise.shape[0]
        if frames == 0:
            return np.zeros((0, BAND_COUNT), dtype=np.float64)

        ratio = noise - total
        history = np.concatenate((self._history, ratio), axis=0)
        missing = self._reference_frames - self._history.shape[0]
        if missing > 0:
            history = np.concatenate((np.repeat(history[:1], missing, axis=0), history), axis=0)
        windows = np.lib.stride_tricks.sliding_window_view(
            history, self._reference_frames, axis=0
        )
        # The window that ends at a frame: the reference of a frame is
        # taken over the frames that came before it.  The window of the
        # sliding view has the window axis last, so it is moved back.
        view = np.transpose(windows[1 : frames + 1], (0, 2, 1))
        # A low percentile of the window and not the strict minimum: the
        # balance of a band dips now and then (a bin that is mistaken for a
        # harmonic takes its share with it), and a reference that follows
        # such a dip opens the gate of the band for good.
        tracked = np.percentile(view, _TRACKER_PERCENTILE, axis=1)

        # The memory: the lowest balance the band has ever shown.  It
        # follows a lower balance at once and creeps back up slowly, so that
        # a band is not held for good.  The creep is given per frame, so
        # that the memory behaves the same no matter how the host chops the
        # stream.
        index = np.arange(frames, dtype=np.float64)[:, None]
        running = np.minimum.accumulate(ratio - _TRACKER_CREEP_DB * index, axis=0)
        sticky = running + _TRACKER_CREEP_DB * index
        carried = self._min_track
        if carried is None or carried.shape[-1] != BAND_COUNT:
            carried = ratio[0].copy()
        sticky = np.minimum(
            sticky, carried[None, :] + _TRACKER_CREEP_DB * (index + 1.0)
        )
        self._min_track = sticky[-1].copy()
        tracked = np.minimum(tracked, sticky + _STICKY_MARGIN_DB)

        learn = bool(self.params.get("Learn"))
        adaptive = bool(self.params.get("Adaptive Mode"))
        captured = self._reference
        if captured is not None and captured.shape[-1] != BAND_COUNT:
            captured = None
        if captured is not None:
            # A captured profile is a *level*: the floor the material of a
            # band may not fall under.  It is the one path of the module
            # that carries the level of the input, and it is a path the host
            # asks for explicitly (its Learn action).
            floor = captured[None, :] - total
        else:
            floor = None

        if learn:
            # Capture mode: the profile of the material follows what is
            # coming in.
            reference = tracked
        elif adaptive:
            # The captured profile is the floor of the reference: the
            # tracked reference may raise it (a louder noise floor), but the
            # wanted content may not make it go down.
            reference = tracked if floor is None else np.maximum(floor, tracked)
        else:
            if floor is not None:
                # The captured profile is the frozen one.
                reference = floor
            else:
                if self._frozen is None or self._frozen.shape[-1] != BAND_COUNT:
                    # The frozen profile is taken when the tracker has
                    # settled, not from the first frame of the stream.
                    settled = max(0, self._reference_frames - 1 - self._frames_seen)
                    self._frozen = tracked[min(settled, frames - 1)].copy()
                reference = np.repeat(self._frozen[None, :], frames, axis=0)
        self._frames_seen += frames
        self._history = np.concatenate((self._history, ratio), axis=0)[
            -self._reference_frames :
        ]
        return reference

    def _gain_curve(self, gains: np.ndarray) -> np.ndarray:
        """Interpolate the band gains onto the analysis bins.

        The curve is smoothed over a few bins: a sharp step makes the
        overlap-add of the modified frames inconsistent, which smears the
        wanted content into the neighbouring bands.

        A band that the module has decided to **leave alone** keeps its
        level.  The interpolation and the smoothing both reach into the
        neighbouring band, so without this the wanted content of a band
        that is left alone is attenuated by the decision of its neighbour:
        a partial that sits near a band edge loses up to 14 dB that way.
        The rule of the untouched material (see
        ``qa/quality/rules_extra.py``) says that it may not lose more than
        0.04 dB.
        """
        index = self._curve_index
        fraction = self._curve_fraction
        curve = (
            gains[:, index] * (1.0 - fraction)[None, :]
            + gains[:, index + 1] * fraction[None, :]
        )
        for _ in range(_CURVE_SMOOTHING):
            padded = np.concatenate([curve[:, :1], curve, curve[:, -1:]], axis=1)
            curve = (padded[:, :-2] + 2.0 * padded[:, 1:-1] + padded[:, 2:]) * 0.25
        # The decision of the band a bin belongs to.  A band that is left
        # alone is not allowed to be attenuated by its neighbour.
        own = gains[:, index]
        return np.where(own >= -_LEFT_ALONE_DB, np.maximum(curve, own), curve)

    def _overlap_add(self, frames: np.ndarray, start: int) -> None:
        """Add the modified frames into the overlap-add accumulator.

        Parameters
        ----------
        frames:
            The modified frames, shape ``(count, _FFT_SIZE, channels)``.
        start:
            Absolute position of the first sample of the first frame.
        """
        count, channels, _ = frames.shape
        chunks = self._fft_size // _HOP
        span = (count - 1) * _HOP + self._fft_size

        local = np.zeros((span, channels), dtype=np.float64)
        block = frames.reshape(count, channels, chunks, _HOP)
        window = self._window_product.reshape(chunks, _HOP)
        denominator = np.zeros(span, dtype=np.float64)
        for m in range(chunks):
            # The chunk m of frame j lands at the position (j + m) * _HOP, so
            # the chunks of one frame index fill a run of the accumulator.
            target = local[m * _HOP : (m + count) * _HOP].reshape(count, _HOP, channels)
            target += np.swapaxes(block[:, :, m, :], 1, 2)
            weight = denominator[m * _HOP : (m + count) * _HOP].reshape(count, _HOP)
            weight += window[m]

        offset = start - self._ola_at
        if offset < 0:
            pad = -offset
            self._ola = np.concatenate((np.zeros((pad, channels)), self._ola), axis=0)
            self._denom = np.concatenate((np.zeros(pad), self._denom))
            self._ola_at -= pad
            offset = 0
        end = offset + span
        if end > self._ola.shape[0]:
            grow = end - self._ola.shape[0]
            self._ola = np.concatenate((self._ola, np.zeros((grow, channels))), axis=0)
            self._denom = np.concatenate((self._denom, np.zeros(grow)))
        keep = min(span, self._ola.shape[0] - offset)
        local[:keep] += self._ola[offset : offset + keep]
        denominator[:keep] += self._denom[offset : offset + keep]
        self._ola[offset : offset + span] = local
        self._denom[offset : offset + span] = denominator

        # The samples whose last frame has been analysed are final.
        final = self._frame_at * _HOP - self._fft_size // 2 - 1
        first = max(self._ola_at, 0)
        last = min(final + 1, self._ola_at + self._ola.shape[0])
        if last > first:
            a = first - self._ola_at
            b = last - self._ola_at
            segment = self._ola[a:b]
            weight = self._denom[a:b]
            ready = np.divide(
                segment,
                weight[:, None],
                out=np.zeros_like(segment),
                where=weight[:, None] > 1e-12,
            )
            self._fifo = np.concatenate((self._fifo, ready), axis=0)
        drop = final + 1 - self._ola_at
        if drop > 0:
            self._ola = self._ola[drop:].copy()
            self._denom = self._denom[drop:].copy()
            self._ola_at = final + 1

    def _emit(self, count: int) -> np.ndarray:
        """Hand out the finalised output, delayed by the latency of the module."""
        out = np.zeros((count, self._fifo.shape[1]), dtype=np.float64)
        # The latency the module declares is the delay of its output: the
        # window of the analysis plus the context of the model that runs.
        start = self._sent - self.latency_samples
        available = self._fifo_at + self._fifo.shape[0]
        if start < self._fifo_at:
            # The output has not caught up with the delay line yet.
            out[: min(count, self._fifo_at - start)] = 0.0
            position = min(count, self._fifo_at - start)
        else:
            position = 0
        take = min(count - position, available - max(start, self._fifo_at))
        if take > 0:
            first = max(start, self._fifo_at) - self._fifo_at
            out[position : position + take] = self._fifo[first : first + take]
            self._fifo = self._fifo[first + take :].copy()
            self._fifo_at += first + take
        self._sent += count
        return out

    # -- serialisation --------------------------------------------------------
    def _state_blob(self) -> bytes:
        """Serialise the captured noise profile and the model that was asked for.

        The name of the model travels with the state, so a session that names
        a model reopens with that model named — and a session whose model
        file is gone says so instead of rendering something else.

        The mode of the render travels with the state as well, so that a
        session that was rendered offline says so when it is opened again in
        a real time host.
        """
        buf = io.BytesIO()
        payload: dict[str, np.ndarray] = {
            "reference": (
                self._reference if self._reference is not None else np.zeros(0)
            ),
            "tracker": (self._frozen if self._frozen is not None else np.zeros(0)),
            "mode": np.asarray(self.processing_mode),
        }
        if self._model_chosen:
            # The choice travels with the state: the name of the model that
            # was asked for, or an empty name for the choice of no model.
            payload["model"] = np.asarray(self._model_requested)
        np.savez(buf, **payload)
        return buf.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore the captured noise profile and the model that was asked for."""
        mode = np.zeros(0)
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as archive:
                reference = archive["reference"]
                tracker = archive["tracker"]
                model = archive["model"] if "model" in archive.files else np.zeros(0)
                if "mode" in archive.files:
                    mode = archive["mode"]
        except Exception:
            return
        # The input that has not been analysed yet, the overlap-add
        # accumulator and the output that waits to be handed out are not part
        # of the saved state: the module starts a new stream with the
        # restored profile.
        self._clear_state()
        self._reference = reference if reference.size else None
        self._frozen = tracker if tracker.size else None
        # The mode of the render travels with the state, so that a session
        # that was rendered offline says so when it is opened again.  It does
        # not decide the mode the module runs in: the engine of the chain
        # owns that.
        if mode.size:
            self._state_mode = str(np.asarray(mode).ravel()[0])
        wanted = ""
        if model.size:
            wanted = str(np.asarray(model).ravel()[0])
        if model.size:
            # The choice travels with the state: the model that was asked
            # for is taken into use again, if it can be.  A model whose file
            # is gone is not an error: the module runs its hand written path
            # and says why.
            self.select_model(wanted)
        else:
            # Nobody chose a model: the module asks for the model that
            # solves its problem, as it did before the state was written.
            self._unbind()
            self._model_requested = ""
            self._model_chosen = False
            self._model_message = ""

    # -- introspection --------------------------------------------------------
    @property
    def state_mode(self) -> str:
        """The mode the module was in when the state was written.

        The mode travels with the state of the module, so that a session
        that was rendered offline says so when it is opened again in a real
        time host.  It is a reading, not a switch: the mode the module runs
        in is the mode the engine of the chain gives it.
        """
        return self._state_mode

    @property
    def latency_samples(self) -> int:
        """Look-ahead of the module (the analysis window plus the model's context).

        The window of the analysis is the look-ahead of the module: the
        overlap-add needs the frame that reaches one window length into the
        future.  A model that reads a context needs that history before it
        can answer, and **that context is latency**: the module publishes it,
        so the host is told how late the audio really is.

        The figure follows the configuration the module is in: the realtime
        configuration analyses a 2048 sample window and declares 2047 samples
        of look-ahead, the offline configuration analyses a 4096 sample window
        and declares 4095.  Without a model the figure of the realtime
        configuration is the one the module has always published: 2047
        samples, bit for bit the same as before.
        """
        return self.mode_latency(
            realtime=_LATENCY, offline=_OFFLINE_LATENCY
        ) + int(self._model_delay)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The ring-out is **the end of the wanted content, spread over the
        analysis window**.  The module makes no sound of its own: with the
        reduction at zero it is a plain delay and it goes quiet exactly one
        latency after its input stops.  With the reduction at its default
        the band that carries the wanted content stays open -- it has to,
        the wanted content may not be taken with the noise -- and the
        overlap-add, which rebuilds a frame from a magnitude and a phase,
        spreads the end of that content over the rest of the window.  The
        residue is the wanted content itself: it is the tone of the input,
        smeared.

        Measured the way the host measures it -- a tone, then silence, then
        the number of samples the output keeps coming above -60 dBFS --
        the realtime configuration rings for 3279 samples past the end of
        the input for a tone at -6 dBFS and for 3404 samples for a square
        wave at the clipping ceiling: 1232 and 1357 samples beyond the
        latency of 2047.  The offline configuration, whose window is twice
        as long, rings for 6555 and 6803 samples: 2460 and 2708 samples
        beyond its latency of 4095.

        The declaration is the worst case that was measured, with a margin
        of 179 samples (3.7 ms) and of 364 samples (7.6 ms).  The rule of
        the family is one line: **the tail a module declares must be the
        tail it has; a module may declare more than it needs, never
        less.**

        The figure follows the configuration the module is in, the way the
        latency does: the two configurations analyse windows of different
        length, and the window is what the tail comes from.
        """
        if self.processing_mode == PROCESSING_MODE_OFFLINE:
            return _OFFLINE_TAIL
        return _TAIL

    @property
    def settling_frames(self) -> int:
        """How many frames of material the module needs before it is ready.

        The module says so, and the gate of the edges
        (``qa/check_module_edges.py``) reads the declaration and measures
        the level of the output only from the settling time on: a module
        that is still settling is not held to the level of a material it has
        not heard yet.

        The number and the measurement behind it are in
        :data:`_SETTLING_FRAMES`.
        """
        return _SETTLING_FRAMES
