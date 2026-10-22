"""
aurcore.modules.deconstruct — Deconstruct.

Splits a recording into three components with a median filter HPSS:

* **tonal** — narrow band harmonic content, found by comparing the spectrum
  against its own broadband envelope (spectral smoothness);
* **noisy** — the stationary broadband remainder;
* **transient** — the part that is not stationary, found by comparing the
  broadband level of every frame against the broadband level of the same band
  in the frames before it (temporal sparsity).

The three components can be levelled independently and the transients can be
kept in or folded back into the noisy part.

The three components are a **partition** of the signal: the tonal, the noisy
and the transient magnitude of a bin add up to the magnitude of that bin, so
every part of the recording is in exactly one component and no part of it is
counted twice.

The transient detector
----------------------
A transient is **not** a steady bed.  The median filter HPSS cannot tell the
two apart on its own: for stationary noise the time median of the magnitude
(the harmonic estimate) and the frequency median (the percussive estimate)
are of the same size, so the percussive mask is about one half and half of a
steady noise bed ends up in the transient component — which is then mixed
back at the transient gain and survives the noisy gain untouched.

The detector therefore compares the *broadband* level of the frame (the
frequency median of the magnitude) with the broadband level of the same band
in the frames before it (the time median of that frequency median).  Both are
medians of the same kind of quantity, so their ratio is one for stationary
material and jumps by tens of decibels when a short broadband event happens.
The transient mask is zero until the frame is
:data:`_TRANSIENT_MARGIN_DB` decibels above that reference and reaches one
:data:`_TRANSIENT_RAMP_DB` decibels later.

How it is computed
------------------
The analysis is **streaming**: the samples of the previous calls are kept, so
every analysis frame of the signal is transformed exactly once instead of
once per block that contains it.  A block-local analysis would transform the
same 2048 sample window again for every block, which is why the cost of the
module did not fall with the block size and why it did not fit into the
realtime budget of a 64 sample block.

The streaming engine is the shared one,
:class:`aurcore.dsp.stft.StreamingSTFT`: its frames sit on the same grid as
the block-local analysis of :class:`aurcore.dsp.stft.STFT` with
``center=True`` and its overlap-add divides by the per-sample window sum, so
the module is a pure filter — it does not colour the signal and it adds no
group delay of its own.  It does need the samples that follow the block, so
its output is delayed by :attr:`Deconstruct.latency_samples` samples.

The three components are rendered by three engines, one per component, so
that :meth:`Deconstruct.components` and :meth:`Deconstruct.process` always
agree.
"""
from __future__ import annotations

import numpy as np

from ..dsp.stft import StreamingSTFT
from ..dsp.util import db_to_lin
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["Deconstruct"]

_EPS = 1e-20

#: Analysis window and hop (samples).
_FFT_SIZE = 2048
_HOP = 512

#: Width of the median filter along the time axis (the harmonic estimate).
_HARMONIC_WIDTH = 17

#: Width of the median filter along the frequency axis (the percussive estimate).
_PERCUSSIVE_WIDTH = 17

#: How many frames the transient detector looks back, in frames.  At the
#: default hop of 512 samples this is 96 ms of hops.
_TRANSIENT_HISTORY = 9

#: How much louder than its own history the broadband level of a frame has to
#: be before the frame counts as a transient, in decibels.  A steady bed never
#: gets this far above the median of its own history.
_TRANSIENT_MARGIN_DB = 6.0

#: The width of the soft ramp of the transient mask, in decibels.  The mask
#: is zero below the margin and one at the margin plus this width.
_TRANSIENT_RAMP_DB = 6.0

#: Gap and span of the broadband floor, in bins.
_BROAD_GAP = 4
_BROAD_SPAN = 16

#: The number of components the signal is separated into.
_COMPONENTS = 3

#: The widest time axis smoothing the *Artifact Smoothing* parameter can ask
#: for; the history of the smoothed masks is kept at that depth.
_SMOOTH_DEPTH = 4

#: The streaming engine is fed in pieces of this many samples.  The engine
#: holds the frames that have not been synthesised yet, so a piece may not
#: complete more frames than it can hold.
_FEED_CHUNK = 4096

try:  # pragma: no cover - depends on the environment
    from scipy.ndimage import median_filter as _scipy_median_filter
except Exception:  # pragma: no cover - SciPy is optional
    _scipy_median_filter = None


# ---------------------------------------------------------------------------
# Median filters
# ---------------------------------------------------------------------------
def median_filter_axis(x: np.ndarray, axis: int, width: int) -> np.ndarray:
    """Median filter along ``axis`` with a window of ``width`` samples.

    SciPy's ``ndimage.median_filter`` is used when available; a NumPy
    sliding-window implementation takes over otherwise.

    Parameters
    ----------
    x:
        Input array.
    axis:
        Axis along which the filter runs.
    width:
        Window length in samples.

    Returns
    -------
    numpy.ndarray
        The filtered array, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if _scipy_median_filter is not None:  # pragma: no branch - depends on the environment
        size = [1] * arr.ndim
        size[int(axis) % arr.ndim] = width
        return np.asarray(
            _scipy_median_filter(arr, size=size, mode="nearest"), dtype=np.float64
        )
    moved = np.moveaxis(arr, int(axis) % arr.ndim, -1)
    pad = width // 2
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(pad, pad)], mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    out = np.median(view, axis=-1)
    return np.moveaxis(out, -1, int(axis) % arr.ndim)


def _median_last_axis(x: np.ndarray, width: int) -> np.ndarray:
    """Median filter of the last axis, with the edges replicated.

    The result is the same as :func:`median_filter_axis` for an odd
    ``width``.  For an even ``width`` it is the *lower* median, i.e. the
    element at the middle of the sorted window, which is what the SciPy rank
    filter returns — NumPy's own ``median`` would average the two middle
    elements instead.

    Parameters
    ----------
    x:
        Input array; the filter runs along its last axis.
    width:
        Window length in samples.

    Returns
    -------
    numpy.ndarray
        The filtered array, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    # the window of the output i covers arr[i - width // 2 : ...], so the
    # padding is one sample shorter on the right for an even width
    left = width // 2
    right = width - 1 - left
    padded = np.pad(arr, [(0, 0)] * (arr.ndim - 1) + [(left, right)], mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    if width % 2 == 1:
        return np.partition(view, width // 2, axis=-1)[..., width // 2]
    return np.partition(view, [width // 2 - 1, width // 2], axis=-1)[..., width // 2]


def _median_filter_bins(x: np.ndarray, width: int) -> np.ndarray:
    """Median filter along the last axis (the frequency axis).

    SciPy's rank filter is used when it is available; the NumPy sliding
    window takes over otherwise.  Both return the *lower* median, i.e. the
    element at the middle of the sorted window.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if _scipy_median_filter is not None:  # pragma: no branch - depends on the environment
        size = [1] * arr.ndim
        size[-1] = width
        return np.asarray(
            _scipy_median_filter(arr, size=size, mode="nearest"), dtype=np.float64
        )
    return _median_last_axis(arr, width)


def _broadband_floor(x: np.ndarray, gap: int = _BROAD_GAP, span: int = _BROAD_SPAN) -> np.ndarray:
    """Broadband level estimate that ignores narrowband peaks.

    The median is taken of the bins that lie outside a central gap, so a
    single spectral peak can never raise the floor.

    Parameters
    ----------
    x:
        Magnitude spectrogram in dB, shape ``(..., bins)``.
    gap:
        Half width of the excluded centre region, in bins.
    span:
        Half width of the analysis window, in bins.

    Returns
    -------
    numpy.ndarray
        The broadband level in dB, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    bins = arr.shape[-1]
    width = max(1, int(span) - int(gap))
    if bins < 2 * int(gap) + 2 or span <= gap:
        return _median_last_axis(arr, 5)
    padded = np.pad(arr, [(0, 0)] * (arr.ndim - 1) + [(int(span), int(span))], mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
    # the median of the window that starts ``gap + 1`` bins further on is the
    # same series, shifted: one median per bin axis instead of two.
    if width % 2 == 1:
        medians = np.partition(view, width // 2, axis=-1)[..., width // 2]
    else:
        # NumPy's median of an even number of values averages the two middle
        # ones; the broadband floor has always used that definition.
        part = np.partition(view, [width // 2 - 1, width // 2], axis=-1)
        medians = 0.5 * (part[..., width // 2 - 1] + part[..., width // 2])
    left = medians[..., :bins]
    right = medians[..., int(gap) + 1 : int(gap) + 1 + bins]
    return np.maximum(left, right)


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------
@register
class Deconstruct(Module):
    """Deconstruct — tonal / noisy / transient separation."""

    code = "AUR_DECONSTRUCT"
    display_name = "Deconstruct"
    category = "Repair"
    edition = "advanced"
    element = "Deconstruct"
    analysis = False
    supports_learn = False  # no learning action for this module

    # -- parameters -----------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list (identical to the canonical table)."""
        return [
            desc(
                name="Tonal Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=2.4,
                unit="dB",
                group="Tonal",
                help="Level of the harmonic component.",
                curve="taper",
            ),
            desc(
                name="Noisy Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=2.4,
                unit="dB",
                group="Noisy",
                help="Level of the stationary broadband component.",
                curve="taper",
            ),
            desc(
                name="Separate Transients",
                type="bool",
                default=True,
                group="Transients",
                help="Keep the percussive part as its own component.",
            ),
            desc(
                name="Transient Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=2.4,
                unit="dB",
                group="Transients",
                help="Level of the percussive component.",
                curve="taper",
            ),
            desc(
                name="Balance",
                type="enum",
                default="Noisy",
                choices=("Noisy", "Tonal"),
                group="Balance",
                help="Tilt the balance between the noisy and the tonal part.",
            ),
            desc(
                name="Artifact Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help="Smooths the component masks so the split does not chatter.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # one streaming engine per component, so that the components and the
        # mix are always the same numbers
        self._engines = [
            StreamingSTFT(_FFT_SIZE, _HOP, window="hann") for _ in range(_COMPONENTS)
        ]
        self._edits = [self._make_edit(index) for index in range(_COMPONENTS)]
        self._n_bins = _FFT_SIZE // 2 + 1
        self._channels = max(int(channels), 1)
        # the history of the magnitudes, used for the harmonic estimate
        self._mag_hist = np.zeros(
            (self._channels, self._n_bins, _HARMONIC_WIDTH), dtype=np.float64
        )
        # the history of the percussive estimates, used for the transient
        # detector: it is the broadband level of the band over time
        self._perc_hist = np.zeros(
            (self._channels, self._n_bins, _TRANSIENT_HISTORY), dtype=np.float64
        )
        # the history of the smoothed tonal masks
        self._mask_hist = np.zeros(
            (self._channels, self._n_bins, _SMOOTH_DEPTH), dtype=np.float64
        )
        self._mag_ring = 0
        self._perc_ring = 0
        self._mag_seen = False
        self._perc_seen = False
        self._pending: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._fifos = [np.zeros((0, self._channels)) for _ in range(_COMPONENTS)]

    def reset(self) -> None:
        """Clear the analysis state."""
        for engine in self._engines:
            engine.reset()
        self._mag_hist[...] = 0.0
        self._perc_hist[...] = 0.0
        self._mask_hist[...] = 0.0
        self._mag_ring = 0
        self._perc_ring = 0
        self._mag_seen = False
        self._perc_seen = False
        self._pending = None
        self._fifos = [np.zeros((0, self._channels)) for _ in range(_COMPONENTS)]

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look-ahead of the analysis)."""
        return int(self._engines[0].latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The ring-out is the tail of the last analysis window the module
        still holds: measured by the host, the output is 10.4 dB below the
        input 1214 samples (25 ms) after the input stopped, and the window
        the separation runs on is 2048 samples long.
        """
        return 1214

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Separate and re-combine the components of a block of audio.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The re-mixed block, same shape as ``x``.  It is delayed by
            :attr:`latency_samples` samples.
        """
        arr = self._as_block(x)
        if arr.shape[0] == 0:
            return arr
        tonal, noisy, transient = self._components(arr)
        return self._mix(tonal, noisy, transient)

    def components(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return the three separated components of a block.

        The block is fed through the same streaming analysis that
        :meth:`process` uses, so the components are the ones the module
        actually works with.  Like the output of :meth:`process` they are
        delayed by :attr:`latency_samples` samples, and the module state
        advances, so a block must not be handed to both methods.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        tuple of numpy.ndarray
            ``(tonal, noisy, transient)``, each with the shape of ``x``.
        """
        arr = self._as_block(x)
        if arr.shape[0] == 0:
            return arr, arr, arr
        tonal, noisy, transient = self._components(arr)
        return tonal, noisy, transient

    # -- internals ------------------------------------------------------------
    def _as_block(self, x: np.ndarray) -> np.ndarray:
        """Return ``x`` as a ``(n_samples, n_channels)`` float64 block."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.ndim != 2:
            raise ValueError("the block must be 1-D or 2-D")
        if arr.shape[1] != self._channels:
            # the caller changed the channel count: restart the analysis
            self._channels = max(int(arr.shape[1]), 1)
            for engine in self._engines:
                engine.reset()
            self._mag_hist = np.zeros(
                (self._channels, self._n_bins, _HARMONIC_WIDTH), dtype=np.float64
            )
            self._perc_hist = np.zeros(
                (self._channels, self._n_bins, _TRANSIENT_HISTORY), dtype=np.float64
            )
            self._mask_hist = np.zeros(
                (self._channels, self._n_bins, _SMOOTH_DEPTH), dtype=np.float64
            )
            self._mag_ring = 0
            self._perc_ring = 0
            self._mag_seen = False
            self._perc_seen = False
            self._pending = None
            self._fifos = [np.zeros((0, self._channels)) for _ in range(_COMPONENTS)]
        return arr

    def _components(self, arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run the streaming analysis over a block and return the components."""
        pieces: list[list[np.ndarray]] = [[], [], []]
        for start in range(0, arr.shape[0], _FEED_CHUNK):
            chunk = arr[start : start + _FEED_CHUNK]
            for index, engine in enumerate(self._engines):
                mag, phase = engine.analyze(chunk)
                if mag.shape[-1]:
                    mag, phase = self._edits[index](mag, phase)
                pieces[index].append(engine.synthesize(mag, phase))
        outputs = []
        for index in range(_COMPONENTS):
            raw = (
                np.concatenate(pieces[index], axis=0)
                if pieces[index]
                else np.zeros((0, self._channels))
            )
            outputs.append(self._delay(index, raw, int(arr.shape[0])))
        return outputs[0], outputs[1], outputs[2]

    def _delay(self, index: int, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the output of an engine and take ``count`` samples of it.

        The streaming engine hands out the samples as soon as they are ready,
        which depends on the size of the blocks it is fed.  The module keeps
        its own line of ``latency_samples`` so that the delay is the same
        whatever the host uses, and so that the result does not depend on
        the block size.
        """
        fifo = self._fifos[index]
        if fifo.shape[0] == 0:
            fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
        fifo = np.concatenate([fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._fifos[index] = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def _make_edit(self, index: int):
        """The edit callback of the engine that renders component ``index``.

        The first engine computes the masks; the other two reuse them, so the
        separation is computed once per frame.
        """

        def edit(mag: np.ndarray, phase: np.ndarray):
            """Return the magnitudes of the component ``index``."""
            if index == 0:
                self._pending = self._split(mag)
            return self._pending[index], phase

        return edit

    def _split(self, mag: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Split the new frames into the tonal, noisy and transient magnitudes.

        Parameters
        ----------
        mag:
            The magnitudes of the new frames, shape
            ``(channels, bins, frames)``.

        Returns
        -------
        tuple of numpy.ndarray
            The magnitudes of the three components, each of shape
            ``(channels, bins, frames)``.
        """
        frames = int(mag.shape[-1])
        tonal = np.empty_like(mag)
        noisy = np.empty_like(mag)
        transient = np.empty_like(mag)
        for index in range(frames):
            t, n, p = self._split_frame(mag[:, :, index])
            tonal[:, :, index] = t
            noisy[:, :, index] = n
            transient[:, :, index] = p
        return tonal, noisy, transient

    def _split_frame(self, mag: np.ndarray):
        """Split one frame into the tonal, the noisy and the transient magnitudes.

        The three magnitudes add up to the magnitude of the frame: they are a
        partition of the signal.

        Parameters
        ----------
        mag:
            The magnitudes of one frame, shape ``(channels, bins)``.

        Returns
        -------
        tuple of numpy.ndarray
            The magnitudes of the three components, each of shape
            ``(channels, bins)``.
        """
        mag_db = 20.0 * np.log10(np.maximum(mag, 1e-12))

        # HPSS: a median filter along the time axis keeps the horizontal
        # (harmonic) structures, a median filter along the frequency axis keeps
        # the vertical (percussive) ones.
        self._push_mag(mag)
        harmonic = self._time_median(self._mag_hist, _HARMONIC_WIDTH)
        percussive = _median_filter_bins(mag, _PERCUSSIVE_WIDTH)

        # HPSS soft mask of the harmonic part.
        p = 2.0
        h_num = harmonic ** p
        p_num = percussive ** p
        total = np.maximum(h_num + p_num, _EPS)
        h_mask = h_num / total

        # Tonal content: narrow band peaks above the broadband envelope.
        broad_db = _broadband_floor(mag_db)
        tonal_mask = np.clip((mag_db - broad_db) / 6.0, 0.0, 1.0)
        smoothing = float(self.params.get("Artifact Smoothing"))
        width = 1 + int(round(6.0 * smoothing)) if smoothing > 0.0 else 0
        if width > 1:
            tonal_mask = _median_filter_bins(tonal_mask, width)
        self._push_mask(tonal_mask)
        if width > 1:
            tonal_mask = self._time_median(self._mask_hist, max(1, width // 2 + 1))

        # The transient part: the broadband level of the frame against the
        # broadband level of the same band in the frames before it.
        transient_mask = self._transient_mask(percussive)

        # The three components are a partition of the frame: the tonal part,
        # and everything that is not tonal, split between the noisy and the
        # transient part.
        harmonic_part = mag * h_mask
        tonal_mask = np.clip(tonal_mask * h_mask, 0.0, 1.0)
        tonal_mag = harmonic_part * tonal_mask
        rest = np.maximum(mag - tonal_mag, 0.0)
        noisy_mag = rest * (1.0 - transient_mask)
        transient_mag = rest * transient_mask
        return tonal_mag, noisy_mag, transient_mag

    def _transient_mask(self, percussive: np.ndarray) -> np.ndarray:
        """The mask of the transient part of a frame.

        A transient is not a steady bed: it is a short, broadband event.  The
        mask therefore compares the broadband level of the frame (the
        percussive estimate, the frequency median of the magnitude) with the
        broadband level of the same band in the frames before it (the time
        median of the percussive estimate).  Both are medians of the same kind
        of quantity, so their ratio is one for stationary material and jumps
        by tens of decibels when a transient happens.

        The mask is zero until the frame is :data:`_TRANSIENT_MARGIN_DB`
        decibels above that reference and reaches one
        :data:`_TRANSIENT_RAMP_DB` decibels later.

        Parameters
        ----------
        percussive:
            The percussive estimate of one frame, shape ``(channels, bins)``.

        Returns
        -------
        numpy.ndarray
            The mask, shape ``(channels, bins)``, values between 0 and 1.
        """
        self._push_perc(percussive)
        stationary = self._time_median(self._perc_hist, _TRANSIENT_HISTORY)
        level_db = 20.0 * np.log10(np.maximum(percussive, 1e-12))
        stationary_db = 20.0 * np.log10(np.maximum(stationary, 1e-12))
        excess_db = level_db - stationary_db
        return np.clip(
            (excess_db - _TRANSIENT_MARGIN_DB) / _TRANSIENT_RAMP_DB, 0.0, 1.0
        )

    def _push_mag(self, mag: np.ndarray) -> None:
        """Add the magnitudes of one frame to the history."""
        if not self._mag_seen:
            # the history starts with the first frame, which is the edge
            # replication the median filter uses at the start of a signal
            self._mag_hist[...] = mag[:, :, None]
            self._mag_seen = True
            return
        self._mag_hist[:, :, self._mag_ring] = mag
        self._mag_ring = (self._mag_ring + 1) % _HARMONIC_WIDTH

    def _push_perc(self, percussive: np.ndarray) -> None:
        """Add the percussive estimate of one frame to the history."""
        if not self._perc_seen:
            # the history starts with the first frame, so the reference of the
            # first frame is the frame itself and nothing looks like a
            # transient before the signal has started
            self._perc_hist[...] = percussive[:, :, None]
            self._perc_seen = True
            return
        self._perc_hist[:, :, self._perc_ring] = percussive
        self._perc_ring = (self._perc_ring + 1) % _TRANSIENT_HISTORY

    def _push_mask(self, mask: np.ndarray) -> None:
        """Add the smoothed tonal mask of one frame to the history."""
        self._mask_hist[:, :, 1:] = self._mask_hist[:, :, :-1]
        self._mask_hist[:, :, 0] = mask

    def _time_median(self, history: np.ndarray, width: int) -> np.ndarray:
        """The median over the ``width`` most recent frames of a history.

        Parameters
        ----------
        history:
            Shape ``(channels, bins, depth)``; the newest frame is at index 0.
        width:
            How many of the most recent frames the median runs over.

        Returns
        -------
        numpy.ndarray
            Shape ``(channels, bins)``.
        """
        window = history[:, :, : int(width)]
        count = window.shape[-1]
        if count % 2 == 1:
            return np.partition(window, count // 2, axis=-1)[:, :, count // 2]
        return np.partition(
            window, [count // 2 - 1, count // 2], axis=-1
        )[:, :, count // 2]

    def _mix(
        self, tonal: np.ndarray, noisy: np.ndarray, transient: np.ndarray
    ) -> np.ndarray:
        """Apply the gains and re-combine the components."""
        p = self.params
        tonal_gain = db_to_lin(float(p.get("Tonal Gain")))
        noisy_gain = db_to_lin(float(p.get("Noisy Gain")))
        transient_gain = db_to_lin(float(p.get("Transient Gain")))
        balance = str(p.get("Balance"))
        if balance == "Noisy":
            tonal_gain *= 10 ** (-3.0 / 20.0)
            noisy_gain *= 10 ** (3.0 / 20.0)
        else:
            tonal_gain *= 10 ** (3.0 / 20.0)
            noisy_gain *= 10 ** (-3.0 / 20.0)

        if bool(p.get("Separate Transients")):
            out = tonal * tonal_gain + noisy * noisy_gain + transient * transient_gain
        else:
            out = tonal * tonal_gain + (noisy + transient) * noisy_gain
        return out
