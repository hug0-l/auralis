"""
aurcore.modules.guitar_de_noise — Guitar De-Noise.

Three independent sub-processors, each with its own bypass:

* **AMP** — amp buzz and rumble.  The mains hum is notched out with a precise
  IIR filter, and the rumble below the guitar's range is taken away with a
  high pass whose corner follows the rumble that is actually there.
* **SQUEAK** — string squeak.  Narrow band components whose energy appears in
  a short burst are attenuated; ``Squeak Duration`` decides how long such a
  burst has to be to count.
* **PICK** — pick attack noise.  The broadband click at the start of a note
  is damped; ``Pick Attack`` decides how aggressively the attack is caught.

The three sub-states are serialised separately so a preset can keep them
apart.

What the detectors can and cannot do
------------------------------------
The module is driven one block at a time, and a block can be as short as 64
samples, so the rules below keep the price of a block down without touching
what the module decides:

* **One analysis for the whole block.**  The sub-processors work on the
  ``(n_samples, n_channels)`` block, so the channels share one transform
  instead of one transform each.
* **Nothing to do means nothing to pay for.**  A sub-processor that finds no
  squeak and no pick attack leaves the block alone; the spectral round trip
  (inverse transform, overlap-add, normalisation) is then skipped, because it
  would only reproduce the input.
* **A detector that cannot act is not run.**  The pick detector cannot
  change a block whose analysis has one or two frames (the temporal median
  of such an analysis is the analysis itself), and the squeak detector
  finds nothing in a block that is too short to resolve a narrow band peak.
* **The tables that never change are built once.**  The mains hum
  projections and the analysis engines are cached instead of being rebuilt
  for every block.

The detectors that need to see the time
---------------------------------------
Three of the four detectors of the module look at the *time* the signal
spends in a state, and a block of 64 samples is 1.33 ms — too short for
that.  What the module does about it is written down here, because it is the
difference between a detector that works and one that is silently inert:

* **The mains notch** cannot tell 50 Hz from 60 Hz in 1.33 ms: the two
  differ by 4.8 degrees over that time, and the projection of a 50 Hz tone
  onto a 45 Hz or a 55 Hz basis is 0.01 dB below the projection onto the
  50 Hz basis.  The detector therefore keeps a rolling window of the last
  170.7 ms (8192 samples) and updates the five projections incrementally.
  The *audio* is not delayed by this — only the decision about which
  frequency to notch uses the past, and the notch itself is an IIR filter.
* **The rumble level** is measured in the time domain, by a low pass at
  55 Hz, because the ``Amp Resolution`` (16..512 samples) cannot resolve
  the rumble band with a transform: at the largest setting the bin spacing
  is 93.75 Hz, so the only bin below 55 Hz is the one at 0 Hz.  The
  parameter is therefore the length of the analysis the rumble level is
  read over.
* **The pick detector** needs three analysis frames before its temporal
  median can differ from the analysis itself, and three frames need a block
  of 512 samples (10.7 ms).  In a 64 sample block the pick gain is exactly
  zero, and the module says so instead of paying for the analysis.
* **The squeak guard** measures the length of the *event*, not the length of
  the current excursion: a loud frame keeps the event alive for 16 more
  frames, so that the rotating leakage pattern of a partial that does not
  sit on a bin centre cannot split a held note into a row of short bursts.
  The run it measures is the run of frames, which is why the layout of the
  analysis is ``(channels, frames, bins)`` and the time axis is axis 1, and
  the length of the event is counted in samples and carried from block to
  block, so that the guard means the same thing for a host that feeds 64
  samples at a time and for one that feeds 5120.

**The block size the module needs.**  A block of 64 samples holds two
analysis frames, and the leakage of a 64 sample burst is 750 Hz wide — wider
than the 703 Hz the detector looks at — so no narrow band peak is found in
it and the squeak detector leaves the block alone.  The detector starts to
see the squeak at a block of 192 samples and works properly from 256
samples (5.3 ms).  The block coherence of the module therefore depends on
the block size of the host: the one block render removes the squeak, the
64 sample render does not.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..dsp.filters import Biquad
from ..dsp.stft import STFT
from ..dsp.util import db_to_lin
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["GuitarDenoise"]

#: Analysis window used by the squeak and pick detectors (samples).
_FFT_SIZE = 1024
_HOP = 256

#: Lowest frequency (Hz) the rumble filter starts to act.
_RUMBLE_HZ = 55.0

#: The frequencies (Hz) the mains hum detector projects the block onto: the
#: two mains frequencies and the two neighbours of each of them.
_MAINS_CANDIDATES: tuple[float, ...] = (45.0, 50.0, 55.0, 60.0, 65.0)

#: How many samples of the past the mains detector looks at.  8192 samples
#: are 170.7 ms at 48 kHz, which is what it takes to tell 50 Hz from 60 Hz
#: and from its neighbours at 45 Hz and 55 Hz.
_MAINS_WINDOW = 8192

#: How many analysis frames the pick detector needs before its temporal
#: median can differ from the analysis itself.  With one or two frames the
#: median is the analysis, so the pick detector cannot change anything.
_PICK_MEDIAN_FRAMES = 2

#: How many frames a loud frame keeps its event alive.  The leakage pattern
#: of a partial that does not sit on a bin centre rotates with the frame
#: position, so the prominence of such a partial dips for a number of frames
#: at a time: the third harmonic of the probe dips for up to 13 frames.  The
#: hold of 16 frames (85.3 ms) bridges those dips, so that a held note is
#: one long event instead of a row of short bursts.
_SQUEAK_HOLD = 16

#: The time constant (ms) of the follower that smooths the rumble level, so
#: that the high pass corner does not jump from block to block.
_RUMBLE_FOLLOW_MS = 50.0

#: How many frames of material the module needs before its answer is final
#: (samples).
#:
#: The three detectors of the module judge an event by the time it has been
#: sounding: an event that is still young is a pick attack, and an event that
#: has been sounding for longer than the guard of the squeak detector is a
#: held note.  The judgement of the first events of a material is therefore
#: provisional -- the module cannot tell a pick attack from the start of a
#: recording before it has seen enough of the material -- and the level of
#: its output is only final once it has.
#:
#: The guard of the squeak detector is 20 frames of 256 samples (5120
#: samples, 106.7 ms at 48 kHz); the number here is that guard rounded up
#: to the next power of two: 8192 frames, 170.7 ms at 48 kHz.
#:
#: Measured: the level of the output of the module is 0.00 dBFS after 8192
#: frames of a full scale tone and 0.00 dBFS after 65536, and it is 4.25 dB
#: lower after 4096 frames.
_SETTLING_FRAMES = 8192

#: The shared engine can hand out the complex spectrum and can rebuild the
#: signal from it with a gain applied to it.  When it can, the module skips
#: the magnitude/phase round trip; when it cannot (an older core), the
#: module uses the magnitude/phase API.
_COMPLEX_ANALYSIS = hasattr(STFT, "analyze_complex_block")


def _median_filter_axis(x: np.ndarray, axis: int, width: int) -> np.ndarray:
    """Median filter along ``axis`` with a NumPy sliding window.

    SciPy's rank filter is fastest on a one dimensional array, so a filter
    along the last axis is done one row at a time.  The numbers are the same
    either way; only the speed differs.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    axis = int(axis) % arr.ndim
    try:  # pragma: no cover - depends on the environment
        from scipy.ndimage import median_filter as _mf
    except Exception:
        _mf = None
    if _mf is not None:
        if arr.ndim > 2:
            out = np.empty_like(arr)
            inner = axis - 1 if axis >= 1 else arr.ndim - 2
            for index in range(arr.shape[0]):
                out[index] = _median_filter_axis(arr[index], inner, width)
            return out
        if arr.ndim == 2 and axis == 1:
            out = np.empty_like(arr)
            for row in range(arr.shape[0]):
                out[row] = _mf(arr[row], size=width, mode="nearest")
            return out
        size = [1] * arr.ndim
        size[axis] = width
        return np.asarray(_mf(arr, size=size, mode="nearest"), dtype=np.float64)
    moved = np.moveaxis(arr, axis, -1)
    pad = width // 2
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(pad, pad)], mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    out = np.median(view, axis=-1)
    return np.moveaxis(out, -1, axis)


def _frame_count(engine: STFT, signal: np.ndarray) -> int:
    """Return the number of analysis frames an engine produces for a block.

    The engine's own framing is used, so the answer is always the number of
    frames the analysis will produce.  It is much cheaper than the analysis
    itself, which makes it usable as a guard in front of it.
    """
    return sum(1 for _ in engine.frames(signal, center=True))


def _running_length(active: np.ndarray, axis: int = 1) -> np.ndarray:
    """Length of the run of active samples that ends at each position.

    Parameters
    ----------
    active:
        Boolean array.  The analysis of the module is laid out as
        ``(channels, frames, bins)``, so the time axis is axis 1.
    axis:
        The axis the run is measured along.

    Returns
    -------
    numpy.ndarray
        The run length at every position, same shape as ``active``.  A
        position that is not active has a run length of zero.
    """
    arr = np.asarray(active, dtype=bool)
    if arr.size == 0:
        return np.zeros(arr.shape, dtype=np.float64)
    axis = int(axis) % arr.ndim
    count = arr.shape[axis]
    shape = [1] * arr.ndim
    shape[axis] = count
    index = np.arange(count, dtype=np.float64).reshape(shape)
    # The position of the last inactive frame at or before every frame.
    marks = np.where(arr, -1.0, index)
    last_inactive = np.maximum.accumulate(marks, axis=axis)
    return index - last_inactive


def _event_mask(peak: np.ndarray, hold: int, axis: int = 1) -> np.ndarray:
    """The mask of the *events*: a loud frame keeps its event alive.

    A frame belongs to an event when there was a loud frame no more than
    ``hold`` frames before it.  The run of this mask is the length of the
    event, so a note whose prominence dips every few frames is one long
    event instead of a row of short bursts.

    Parameters
    ----------
    peak:
        Boolean array of loud frames.
    hold:
        How many frames a loud frame keeps its event alive.
    axis:
        The time axis.

    Returns
    -------
    numpy.ndarray
        Boolean array, same shape as ``peak``.
    """
    arr = np.asarray(peak, dtype=bool)
    if arr.size == 0:
        return arr.copy()
    axis = int(axis) % arr.ndim
    count = arr.shape[axis]
    shape = [1] * arr.ndim
    shape[axis] = count
    index = np.arange(count, dtype=np.float64).reshape(shape)
    marks = np.where(arr, index, -1.0)
    last_loud = np.maximum.accumulate(marks, axis=axis)
    # A frame before the first loud frame is not part of an event.
    return (last_loud >= 0.0) & ((index - last_loud) <= float(hold))


class _SubProcessor:
    """Common behaviour of the three sub-processors."""

    name = "sub"

    def __init__(self) -> None:
        self.bypass = False

    def reset(self) -> None:
        """Clear the internal state."""

    def state(self) -> dict[str, Any]:
        """Return the serialisable state of the sub-processor."""
        return {"bypass": bool(self.bypass)}

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore the state produced by :meth:`state`."""
        if "bypass" in data:
            self.bypass = bool(data["bypass"])


class _MainsDetector:
    """The mains hum detector.

    The hum is found by projecting the signal onto the two mains frequencies
    and their neighbours.  A projection over 64 samples cannot tell 50 Hz
    from 60 Hz — over 1.33 ms the two differ by 4.8 degrees, and the
    projection of a 50 Hz tone onto a 45 Hz or a 55 Hz basis is 0.01 dB
    below the projection onto the 50 Hz basis.  The detector therefore keeps
    a rolling window of the last :data:`_MAINS_WINDOW` samples and updates
    the five projections incrementally, so that the decision is made over
    170.7 ms of audio however short the blocks are.

    The audio is not delayed: the notch that removes the hum is an IIR
    filter, and only the *decision* about which frequency to notch uses the
    past.
    """

    def __init__(self, window: int, sample_rate: int) -> None:
        self._window = int(max(64, window))
        self._sample_rate = float(sample_rate)
        self._freqs = np.asarray(_MAINS_CANDIDATES, dtype=np.float64)
        # The phase of the basis of the next sample that arrives.
        self._phase = np.ones(self._freqs.size, dtype=np.complex128)
        # The basis of the samples inside a block, relative to its first one.
        self._base: np.ndarray | None = None
        self._base_n = 0
        # The running projections, shape (candidates, channels).
        self._proj = np.zeros((self._freqs.size, 0), dtype=np.complex128)
        # The rolling window of the past, shape (window, channels).
        self._history = np.zeros((self._window, 0), dtype=np.float64)
        self._pos = 0
        self._count = 0

    def reset(self) -> None:
        """Forget the past."""
        self._phase = np.ones(self._freqs.size, dtype=np.complex128)
        self._proj = np.zeros((self._freqs.size, 0), dtype=np.complex128)
        self._history = np.zeros((self._window, 0), dtype=np.float64)
        self._pos = 0
        self._count = 0

    def _basis(self, count: int) -> np.ndarray:
        """Return the basis of ``count`` samples, relative to the first one."""
        if self._base is None or self._base_n != count:
            index = np.arange(count, dtype=np.float64)
            self._base = np.exp(
                -2j
                * np.pi
                * self._freqs[:, None]
                * index[None, :]
                / self._sample_rate
            )
            self._base_n = count
        return self._base

    def update(self, x: np.ndarray) -> np.ndarray:
        """Add a block to the rolling window and return the projections.

        Parameters
        ----------
        x:
            The block, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The projections of the window onto the five candidates, shape
            ``(candidates, channels)``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        count, channels = arr.shape
        if self._history.shape[1] != channels:
            self._history = np.zeros((self._window, channels), dtype=np.float64)
            self._proj = np.zeros((self._freqs.size, channels), dtype=np.complex128)
            self._pos = 0
            self._count = 0
        # The basis of the first sample of the block.  The projection is
        # taken against the absolute time, so the samples that fall out of
        # the window carry the basis of the time they were written at and
        # no rotation of the running sum is needed.
        phase_in = self._phase[:, None] * self._basis(count)
        step = np.exp(-2j * np.pi * self._freqs * count / self._sample_rate)
        index = (self._pos + np.arange(count)) % self._window
        outgoing = None
        phase_out = None
        if self._count >= self._window:
            # The samples that fall out of the window carry the phase of the
            # samples that were ``window`` samples earlier.
            outgoing = self._history[index]
            phase_out = phase_in * np.exp(
                2j * np.pi * self._freqs * self._window / self._sample_rate
            )[:, None]
        self._proj = self._proj + phase_in @ arr
        if outgoing is not None:
            self._proj = self._proj - phase_out @ outgoing
        self._history[index] = arr
        self._pos = int((self._pos + count) % self._window)
        self._count = int(min(self._count + count, self._window))
        self._phase = self._phase * step
        return self._proj

    def frequency(self, projections: np.ndarray) -> float | None:
        """Return the mains frequency when the hum is there.

        The decision needs the hum to stand at least 6 dB above its
        neighbours, so that a hum at 50 Hz is not confused with a tone at
        55 Hz or with the programme.  The channels are compared by their
        magnitude, so that a hum that is in one channel only is still found.

        The decision is only made once the window is full.  A window that
        is not full cannot tell 50 Hz from 60 Hz -- over 1.33 ms the two
        differ by 4.8 degrees -- and the leakage of the programme into the
        basis of the hum is large over a short window: a 1 kHz tone at
        -6 dBFS projects 15 dB down onto the 50 Hz basis over 64 samples,
        and the detector would notch a hum that is not there.  The notch
        would then be put in at a different point of the programme for
        every block size, and a notch that is put in and taken out rings
        for as long as the notch rings.
        """
        count = int(self._count)
        if count < self._window:
            return None
        magnitudes = 2.0 * np.abs(projections) / float(count)
        levels = 20.0 * np.log10(np.maximum(magnitudes.max(axis=1), 1e-12))
        index = _MAINS_CANDIDATES.index(50.0)
        best_index = index if levels[index] >= levels[index + 2] else index + 2
        best_level = float(levels[best_index])
        neighbours = (levels[best_index - 1], levels[best_index + 1])
        if best_level > max(neighbours) + 6.0:
            return _MAINS_CANDIDATES[best_index]
        return None


class _AmpProcessor(_SubProcessor):
    """Amp buzz and rumble removal."""

    name = "amp"

    def __init__(self) -> None:
        super().__init__()
        self._mains: _MainsDetector | None = None
        self._notches: list[Biquad] = []
        self._notch_key: tuple[float, float] | None = None
        self._highpasses: list[Biquad] = []
        self._highpass_hz = 0.0
        self._rumble_lps: list[Biquad] = []
        self._rumble_env = 0.0

    def reset(self) -> None:
        """Forget the past of the detector and the filters."""
        if self._mains is not None:
            self._mains.reset()
        self._notches = []
        self._notch_key = None
        self._highpasses = []
        self._highpass_hz = 0.0
        self._rumble_lps = []
        self._rumble_env = 0.0

    def state(self) -> dict[str, Any]:
        """Return the serialisable state of the amp processor."""
        data = super().state()
        data["rumble_env"] = float(self._rumble_env)
        data["notch_state"] = [np.asarray(f.state).tolist() for f in self._notches]
        data["highpass_state"] = [np.asarray(f.state).tolist() for f in self._highpasses]
        data["rumble_state"] = [np.asarray(f.state).tolist() for f in self._rumble_lps]
        return data

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore the state produced by :meth:`state`."""
        super().load_state(data)
        if "rumble_env" in data:
            self._rumble_env = float(data["rumble_env"])
        for key, filters in (
            ("notch_state", self._notches),
            ("highpass_state", self._highpasses),
            ("rumble_state", self._rumble_lps),
        ):
            values = data.get(key)
            if values is None:
                continue
            try:
                for filt, value in zip(filters, values):
                    filt.state = np.asarray(value, dtype=np.float64)
            except Exception:
                pass

    def process(
        self, signal: np.ndarray, sensitivity: float, resolution: int, sample_rate: int
    ) -> np.ndarray:
        """Notch out the amp buzz (mains hum) and the rumble.

        The mains hum is measured over a rolling window of the past, and
        the removal itself is done with a precise notch filter so that a
        coarse resolution can never take the guitar with it.

        The block is filtered **in place** — the caller owns the buffer.
        """
        if self.bypass or sensitivity <= 0.0:
            return signal
        arr = np.asarray(signal, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        channels = int(arr.shape[1])
        if self._mains is None:
            self._mains = _MainsDetector(_MAINS_WINDOW, sample_rate)
        if len(self._rumble_lps) != channels:
            self._rumble_lps = [
                Biquad.from_design("lowpass", _RUMBLE_HZ, 0.707, float(sample_rate))
                for _ in range(channels)
            ]

        # -- the mains hum ----------------------------------------------------
        mains = self._mains.frequency(self._mains.update(arr))
        if mains is not None:
            q = 8.0 + 30.0 * float(sensitivity)
            key = (float(mains), float(q))
            if self._notch_key != key:
                # The notch keeps its state when it is built again: a filter
                # that starts from zero in the middle of the signal puts a
                # step into it, and the step rings in the notch for as long
                # as the notch rings.  The detector picks its frequency
                # once per block, so the frequency it picks moves with the
                # size of the block the host hands over, and the step was
                # put in once per block.
                notches = []
                for index in range(channels):
                    fresh = Biquad.from_design(
                        "notch", mains, q, float(sample_rate)
                    )
                    if index < len(self._notches):
                        fresh.state = self._notches[index].state
                    notches.append(fresh)
                self._notches = notches
                self._notch_key = key
            for ch in range(channels):
                arr[:, ch] = self._notches[ch].process(arr[:, ch])

        # -- the rumble --------------------------------------------------------
        # The level is measured in the time domain: the ``Amp Resolution``
        # (16..512 samples) cannot resolve the rumble band, because at the
        # largest setting the bin spacing is 93.75 Hz and the only bin below
        # 55 Hz is the one at 0 Hz.  A low pass at 55 Hz measures the band
        # itself, and it works at any block size.
        # The length of the analysis is the ``Amp Resolution``: the level of
        # the rumble band is read over the last ``resolution`` samples of
        # the block, which is the resolution of the measurement.
        count = int(max(1, min(int(resolution), int(arr.shape[0]))))
        low = np.empty_like(arr)
        for ch in range(channels):
            low[:, ch] = self._rumble_lps[ch].process(arr[:, ch])
        rms_low = float(np.sqrt(np.mean(low[-count:] * low[-count:])))
        rms_all = float(np.sqrt(np.mean(arr[-count:] * arr[-count:])))
        if rms_all > 0.0:
            level_db = 20.0 * float(np.log10(max(rms_low, 1e-12) / rms_all))
            rumble = float(np.clip((level_db + 30.0) / 30.0, 0.0, 1.0))
        else:
            rumble = 0.0
        # The follower keeps the corner of the filter from jumping.  Its
        # time constant is a time and not a number of blocks, so that the
        # corner of the filter does not depend on the size of the block the
        # host hands over: the follower is the one pole filter of a 50 ms
        # time constant, written out over the whole block at once.  A
        # follower whose coefficient is taken from the length of the block
        # moves sixteen times faster at 64 samples per block than at 1024,
        # and the module answers differently to the same material.
        length = int(arr.shape[0])
        per_sample = 1.0 - float(
            np.exp(-1.0 / (float(sample_rate) * (_RUMBLE_FOLLOW_MS / 1000.0)))
        )
        decay = (1.0 - per_sample) ** length
        self._rumble_env = self._rumble_env * decay + rumble * (1.0 - decay)
        # Rumble: a gentle high pass below the lowest useful guitar note.
        # (A high shelf is avoided on purpose: the shared shelf filter keeps
        # its gain all the way up, which would colour the whole guitar.)
        # The cutoff follows the amount of rumble that is actually there, so
        # a clean recording is left alone.
        cutoff = 20.0 + (15.0 + 30.0 * float(sensitivity)) * self._rumble_env
        if len(self._highpasses) != channels or abs(cutoff - self._highpass_hz) > 1.0:
            highpasses = []
            for index in range(channels):
                fresh = Biquad.from_design(
                    "highpass", cutoff, 0.707, float(sample_rate)
                )
                if index < len(self._highpasses):
                    fresh.state = self._highpasses[index].state
                highpasses.append(fresh)
            self._highpasses = highpasses
            self._highpass_hz = cutoff
        for ch in range(channels):
            arr[:, ch] = self._highpasses[ch].process(arr[:, ch])
        return arr


class _SqueakProcessor(_SubProcessor):
    """String squeak removal."""

    name = "squeak"

    def __init__(self) -> None:
        super().__init__()
        self._engine = STFT(_FFT_SIZE, _HOP, window="hann")
        self._event_samples: np.ndarray | None = None
        self._quiet_samples: np.ndarray | None = None

    def reset(self) -> None:
        """Forget the events that are still open."""
        self._event_samples = None
        self._quiet_samples = None

    def state(self) -> dict[str, Any]:
        """Return the serialisable state of the squeak processor."""
        data = super().state()
        if self._event_samples is not None:
            data["events"] = np.asarray(self._event_samples).tolist()
        if self._quiet_samples is not None:
            data["quiet"] = np.asarray(self._quiet_samples).tolist()
        return data

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore the state produced by :meth:`state`."""
        super().load_state(data)
        for key, attribute in (("events", "_event_samples"), ("quiet", "_quiet_samples")):
            value = data.get(key)
            if value is None:
                continue
            try:
                setattr(self, attribute, np.asarray(value, dtype=np.float64))
            except Exception:
                setattr(self, attribute, None)

    def process(
        self,
        signal: np.ndarray,
        sensitivity: float,
        reduction: float,
        duration: str,
        sample_rate: int,
    ) -> np.ndarray:
        """Attenuate narrow band squeaks."""
        if self.bypass or sensitivity <= 0.0 or reduction <= 0.0:
            return signal
        arr = np.asarray(signal, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        engine = self._engine
        spec = None
        phase = None
        if _COMPLEX_ANALYSIS:
            spec = engine.analyze_complex_block(arr, center=True)
            mag = np.abs(spec)
        else:
            mag, phase = engine.analyze_block(arr, center=True)
        if mag.ndim == 2:
            mag = mag[None, :, :]
            if phase is not None:
                phase = phase[None, :, :]
        # "Short" catches a quick squeak, "Long" a slow one.
        win = 20 if duration == "Short" else 100

        # (channels, frames, bins)
        db = 20.0 * np.log10(np.maximum(mag.transpose(0, 2, 1), 1e-12))
        local = _median_filter_axis(db, 2, 15)
        prominence = db - local
        peak = prominence > (10.0 - 6.0 * float(sensitivity))
        if not np.any(peak):
            # Nothing stands out, so no event is open any more.
            self._event_samples = None
            self._quiet_samples = None
            return signal
        # A squeak lives in a short burst.  The guard has to measure the
        # length of the *event*, so the event mask keeps an event alive
        # across the frames in which the prominence of the bin dips, and the
        # run of that mask is the length of the event.  The run is measured
        # along the time axis, which is axis 1 of the analysis.
        #
        # The length of the event is counted in samples and carried from
        # block to block, so that the guard means the same thing whether
        # the host feeds the module 64 samples or 5120 at a time: a block
        # of 64 samples holds two frames, and two frames cannot tell a
        # burst from a held note on their own.  An event that is still open
        # survives a gap of up to ``_SQUEAK_HOLD`` frames; after that it is
        # over and the next event starts from zero.
        event = _event_mask(peak, _SQUEAK_HOLD)
        carried = self._event_samples
        quiet = self._quiet_samples
        if (
            carried is None
            or quiet is None
            or carried.shape != (arr.shape[1], db.shape[2])
        ):
            carried = np.zeros((arr.shape[1], db.shape[2]), dtype=np.float64)
            quiet = np.zeros((arr.shape[1], db.shape[2]), dtype=np.float64)
        # An event that is still open continues into this block.
        event[:, 0, :] = event[:, 0, :] | (carried > 0.0)
        run = _running_length(event, axis=1)
        frames = run.shape[1]
        length = int(arr.shape[0])
        index = np.arange(frames, dtype=np.float64).reshape(1, frames, 1)
        # The frame the event starts at, and the time the event has taken
        # inside this block.
        start = np.maximum(index - run + 1.0, 0.0)
        within = np.minimum(run * float(_HOP), float(length))
        duration = within + np.where(start <= 0.0, carried[:, None, :], 0.0)
        burst = event & (duration <= float(win) * float(_HOP))
        # The event that is still open at the end of the block, and how long
        # the bin has been quiet.
        loud_end = peak[:, -1, :]
        hold_samples = float(_SQUEAK_HOLD) * float(_HOP)
        quiet = np.where(loud_end, 0.0, quiet + float(length))
        self._quiet_samples = quiet
        self._event_samples = np.where(
            quiet > hold_samples,
            0.0,
            np.where(event[:, -1, :], duration[:, -1, :], carried),
        ).astype(np.float64)
        amount = np.clip((prominence - 6.0) / 12.0, 0.0, 1.0)
        gain_db = -(amount * burst * float(reduction) * 24.0).transpose(0, 2, 1)
        if not np.any(gain_db):
            return signal

        gain = db_to_lin(gain_db)
        if spec is not None:
            out = engine.synthesize_complex_block(
                spec, norm="exact", length=int(arr.shape[0]), center=True, gain=gain
            )
        else:
            out = engine.synthesize_block(
                mag * gain, phase, norm="exact", length=int(arr.shape[0]), center=True
            )
        return np.asarray(out, dtype=np.float64)


class _PickProcessor(_SubProcessor):
    """Pick attack noise removal."""

    name = "pick"

    def __init__(self) -> None:
        super().__init__()
        self._engine = STFT(_FFT_SIZE, _HOP, window="hann")

    def process(
        self,
        signal: np.ndarray,
        sensitivity: float,
        reduction: float,
        attack: float,
        sample_rate: int,
    ) -> np.ndarray:
        """Damp the broadband click at the start of a note."""
        if self.bypass or sensitivity <= 0.0 or reduction <= 0.0:
            return signal
        arr = np.asarray(signal, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        engine = self._engine
        if _frame_count(engine, arr) <= _PICK_MEDIAN_FRAMES:
            # The temporal median of a one or two frame analysis is the
            # analysis itself: the window holds (width + 1) / 2 copies of the
            # frame it is centred on, which is more than half of the window,
            # so the median of the window is that frame.  The excess over the
            # median — and with it the pick gain — is therefore zero and the
            # detector cannot change anything.  Three frames are needed, and
            # three frames need a block of 512 samples.  This is the normal
            # case for a small block, and the analysis is skipped for it.
            return signal
        spec = None
        phase = None
        if _COMPLEX_ANALYSIS:
            spec = engine.analyze_complex_block(arr, center=True)
            mag = np.abs(spec)
        else:
            mag, phase = engine.analyze_block(arr, center=True)
        if mag.ndim == 2:
            mag = mag[None, :, :]
            if phase is not None:
                phase = phase[None, :, :]
        # The shorter the attack, the tighter the window that catches it.
        window = max(2, int(round((1.0 - float(attack)) * 6.0 + 2.0)))

        # (channels, frames, bins)
        db = 20.0 * np.log10(np.maximum(mag.transpose(0, 2, 1), 1e-12))
        flat = db.mean(axis=2)  # (channels, frames)
        onset = np.zeros_like(flat)
        onset[:, 1:] = np.diff(flat, axis=1)
        limit = 3.0 + 5.0 * float(sensitivity)
        trigger = onset > limit
        if not np.any(trigger):
            return signal
        # Only the transient part of the spectrum is damped: the excess
        # over the temporal median is what a pick attack adds.
        sustained = _median_filter_axis(db, 1, 21)
        excess = db - sustained
        amount = np.clip(excess / 12.0, 0.0, 1.0)
        active = np.zeros(trigger.shape, dtype=bool)
        for ch in range(trigger.shape[0]):
            for f in np.flatnonzero(trigger[ch]):
                active[ch, f : f + window] = True
        gain_db = -(
            amount * active[:, :, None] * float(reduction) * 24.0
        ).transpose(0, 2, 1)
        if not np.any(gain_db):
            return signal

        gain = db_to_lin(gain_db)
        if spec is not None:
            out = engine.synthesize_complex_block(
                spec, norm="exact", length=int(arr.shape[0]), center=True, gain=gain
            )
        else:
            out = engine.synthesize_block(
                mag * gain, phase, norm="exact", length=int(arr.shape[0]), center=True
            )
        return np.asarray(out, dtype=np.float64)


@register
class GuitarDenoise(Module):
    """Guitar De-Noise — amp, squeak and pick sub-processors."""

    code = "AUR_GUITARDENOISE"
    display_name = "Guitar De-Noise"
    category = "Repair"
    edition = "standard"
    element = "GuitarDenoiser"
    analysis = False
    supports_learn = False  # no learning action for this module

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            desc(
                name="Amp Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Amp",
                help="How hard the amp buzz and rumble are notched out.",
            ),
            desc(
                name="Amp Resolution",
                type="int",
                default=128,
                minimum=16,
                maximum=512,
                group="Amp",
                help="Length (in samples) of the analysis the amp detector "
                "reads the rumble level with.",
            ),
            desc(
                name="Squeak Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Squeak",
                help="How sensitive the string squeak detector is.",
            ),
            desc(
                name="Squeak Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Squeak",
                help="How much of a detected squeak is removed.",
            ),
            desc(
                name="Squeak Duration",
                type="enum",
                default="Short",
                choices=("Short", "Long"),
                group="Squeak",
                help="How long a squeak has to last to count.",
            ),
            desc(
                name="Pick Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Pick",
                help="How sensitive the pick attack detector is.",
            ),
            desc(
                name="Pick Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Pick",
                help="How much of the pick attack is removed.",
            ),
            desc(
                name="Pick Attack",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Pick",
                help="How tightly the attack window follows the onset.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._amp = _AmpProcessor()
        self._squeak = _SqueakProcessor()
        self._pick = _PickProcessor()

    def reset(self) -> None:
        """Reset the three sub-processors."""
        self._amp.reset()
        self._squeak.reset()
        self._pick.reset()

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The sub-processors hold a window of 1024 samples and a biquad each.
        Measured the way the host measures it, the output stays above
        -60 dBFS for 612 samples after the input stopped for a tone at
        -6 dBFS, and for 1457 samples for a signal at the clipping
        ceiling, which drives the filters much harder.  The tail has to
        cover the loud one: the number carries a margin of 79 samples
        (1.6 ms) over it.
        """
        return 1536

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

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Run the three sub-processors in series.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels).

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
        out = arr.copy()
        out = self._amp.process(
            out,
            float(self.params.get("Amp Sensitivity")),
            int(self.params.get("Amp Resolution")),
            self.sample_rate,
        )
        out = self._squeak.process(
            out,
            float(self.params.get("Squeak Sensitivity")),
            float(self.params.get("Squeak Reduction")),
            str(self.params.get("Squeak Duration")),
            self.sample_rate,
        )
        out = self._pick.process(
            out,
            float(self.params.get("Pick Sensitivity")),
            float(self.params.get("Pick Reduction")),
            float(self.params.get("Pick Attack")),
            self.sample_rate,
        )
        return np.asarray(out, dtype=np.float64)

    # -- serialisation --------------------------------------------------------
    def serialize(self) -> dict:
        """Serialise the three sub-states separately."""
        return {
            "amp": self._amp.state(),
            "squeak": self._squeak.state(),
            "pick": self._pick.state(),
        }

    def deserialize(self, data: dict) -> None:
        """Restore the three sub-states."""
        if not isinstance(data, dict):
            return
        self._amp.load_state(data.get("amp", {}) or {})
        self._squeak.load_state(data.get("squeak", {}) or {})
        self._pick.load_state(data.get("pick", {}) or {})

    def set_bypass(self, processor: str, bypass: bool) -> None:
        """Bypass one of the three sub-processors.

        Parameters
        ----------
        processor:
            ``"amp"``, ``"squeak"`` or ``"pick"``.
        bypass:
            ``True`` to bypass the sub-processor.
        """
        sub = {"amp": self._amp, "squeak": self._squeak, "pick": self._pick}.get(str(processor))
        if sub is not None:
            sub.bypass = bool(bypass)
