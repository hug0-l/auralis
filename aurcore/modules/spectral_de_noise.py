"""
aurcore.modules.spectral_de_noise — Spectral De-Noise (the flagship module).

A multi-resolution spectral denoiser:

* **Analysis** — a configurable window (``FFT Size``), optional multi-resolution
  analysis and a quality preset (``A``..``D``) that trades speed against
  accuracy.
* **Learning** — ``Learn`` captures a per-bin noise floor plus the tonal peaks
  of the noise; ``Adaptive`` keeps tracking the profile while processing.
* **Reduction law** — per-bin attenuation depth from ``Noise Threshold`` /
  ``Tonal Threshold`` with ``Noise Reduction`` / ``Tonal Reduction`` capping
  the maximum attenuation, refined by an OM-LSA (or plain spectral
  subtraction) estimator.
* **Smoothing** — ``Artifact Control`` (time) and ``Smoothing`` (frequency)
  shape the gain curve so that no musical noise is produced.
* **Noise floor** — ``Noise Floor Synthesis`` regenerates the removed floor,
  ``Masking`` protects psychoacoustically masked bins, ``Enhancement`` re-adds
  low level but wanted content and ``Whitening`` flattens the long term
  spectrum.

The two configurations
----------------------
The module works in one of two configurations, and the mode of the chain
decides which one it is (``aurcore.modules.base.Module.processing_mode``):

``realtime``
    The analysis window is the one the parameters ask for: 20 ms at the
    default quality, 720 samples at 48 kHz.  The module keeps inside the
    budget of a real time block.
``offline``
    The same window, 1.5 times as long: 1080 samples at 48 kHz.  There is no
    deadline in this mode, so the window may be as long as the job needs, and
    the longer window is what tells two wanted partials that stand 120 Hz
    apart from each other -- at 720 samples they share a bin, at 1080 they
    do not.

The mode is the only thing that decides: the module never switches by
itself, and the latency it declares is the latency of the configuration it
is in -- 1440 samples (30 ms) in the realtime mode, 2160 samples (45 ms) in
the offline mode.  The number of the offline mode is the one of the longer
window, and it is published, so a host knows how late the audio is.

The analysis is **streaming**
---------------------------
The module keeps the samples of the previous calls, so every analysis frame
of the signal is transformed exactly once instead of once per block that
contains it.  A block-local analysis transforms the same 20 ms window again
for every block, which is why the cost of the module did not fall with the
block size and why it did not fit into the realtime budget of a 64 sample
block.

The streaming engine is the shared one,
:class:`aurcore.dsp.stft.StreamingSTFT`: its frames sit on the same grid as
the block-local analysis of :class:`aurcore.dsp.stft.STFT` with
``center=True`` and its overlap-add divides by the per-sample window sum, so
the module stays a pure filter — it does not colour the signal.

The engine needs the samples that follow a frame, so the output of the module
is delayed by :attr:`SpectralDenoise.latency_samples` samples.  The
multi-resolution analysis needs more: the curve of a longer window is only
known once the samples on both sides of that window have arrived, so the base
resolution is fed a signal that is a few samples old.  That look-ahead is
part of the latency the module declares, and it is what keeps the gain curves
of the two resolutions aligned in time.

The state of the module is per channel and per frame: every tracker (the
minimum statistics, the temporal smoothing, the long term average) is
advanced frame by frame, so the result of the module does not depend on the
block size the host happens to use.

THE MODEL, AND WHERE IT BELONGS
-------------------------------
The module asks the core for **the model that solves its problem**
(:func:`aurcore.models.request`) and uses it when there is one.  The answer
is either a model or nothing, and nothing is a normal answer: the module
then does exactly what it does today, sample for sample.

A model is used in the **offline** path only.  The real time path never even
asks: the load of a model costs time and memory, and the hand written path
is the one that has to keep inside the budget of a block.  With no model the
output of the module is bit for bit the output it has always been -- the
model is an addition, never a change.

Why the module asks for a model at all: the floor the module uses is
re-estimated from every frame of the signal, so the floor moves and the gain
moves with it.  On a steady bed the gain curve of the de-noiser breathes,
and the curve is what is heard as musical noise.  A model decides from a
quantity that does not carry the level of the input and does not re-estimate
from scratch every frame, so a floor that comes from a model does not
breathe.

The module asks for the **ambience** of the material: the bed the wanted
content sits in.  The shipped model of that problem describes the ambience
of a frame as a spectrum, and a spectrum is a noise floor.  The floor the
module applies is then the higher of the two floors -- the model's and the
one the module estimates -- so that the model may say that there is more
noise than the module thinks, never less.  The frame-to-frame movement of
the estimate is cut off by the floor of the model, the gain curve of a
steady bed stops breathing, and the removal is never smaller than the one of
the hand written path.

The model of the **voice** problem produces a mask instead of a spectrum; the
mask is read as the signal-to-noise ratio it stands for, and the floor of
the module is the level of the bin minus that ratio.

The context of the model -- the history it reads before it can answer -- **is**
latency, and the module publishes it: :attr:`SpectralDenoise.latency_samples`
is the latency of the configuration plus the context of the model that runs.
"""
from __future__ import annotations

import io
import math
from typing import Any

import numpy as np

from .. import models
from ..dsp import spectral as _spectral
from ..dsp.stft import STFT, StreamingSTFT
from ..dsp.util import db_to_lin, lin_to_db
from ..params import ParamDesc, desc
from ..registry import register
from .base import Module, PROCESSING_MODE_OFFLINE

__all__ = ["SpectralDenoise", "median_filter_axis", "soft_weight"]

#: Quality preset -> analysis behaviour.  ``A`` is the fastest and least
#: accurate, ``D`` the slowest and most accurate.
_QUALITY_TABLE: dict[str, dict[str, Any]] = {
    "A": {"fft_scale": 0.5, "overlap": 2, "omlsa": False, "freq_smooth": 0.20},
    "B": {"fft_scale": 0.75, "overlap": 4, "omlsa": False, "freq_smooth": 0.40},
    "C": {"fft_scale": 1.0, "overlap": 4, "omlsa": True, "freq_smooth": 0.60},
    "D": {"fft_scale": 1.5, "overlap": 4, "omlsa": True, "freq_smooth": 0.80},
}

#: How much longer the analysis window of the offline configuration is than
#: the one of the realtime configuration.
#:
#: The window of the realtime configuration is 20 ms at the default quality,
#: 720 samples at 48 kHz: the wanted partials of a close voiced stack, 120 Hz
#: apart, sit 1.8 bins apart and share a bin.  The offline configuration has
#: no deadline, so its window may be longer, and 1.5 times as long puts those
#: partials 2.7 bins apart.
#:
#: The factor is measured and not guessed.  The damage the module does to the
#: wanted content, the noise it takes away and the roughness of its gain
#: curve were measured for every window length from 720 to 2880 samples (the
#: sweep is ``var/w33/exp_spectral.py``).  At 1080 samples the wanted
#: partials of the voice case are damaged by 2.73 dB instead of the 9.16 dB
#: of the realtime window, the removal on the hum case grows from 12.30 dB to
#: 13.05 dB and the roughness of the gain curve stays at 0.26 dB^2.  A
#: window of 1440 samples damages the plucked note of the music case by
#: 7.58 dB, and a window of 2160 samples makes the gain curve breathe
#: (5.5 dB^2), so the longer windows are not better and they are not the
#: configuration of the offline mode.
_OFFLINE_WINDOW_SCALE = 1.5

#: Percentile used when a noise profile is captured from a learning block.
_LEARN_PERCENTILE = 50.0

#: Width of the excluded zone and of the analysis window of the broadband
#: floor, in Hz.  A fixed number of bins would mean a different width for
#: every analysis resolution, and a zone that is too narrow lets the wanted
#: partials next to a bin pollute its noise estimate.
_BROAD_GAP_HZ = 500.0
_BROAD_SPAN_HZ = 1500.0

#: How many frames of an extra resolution the module keeps, so that the
#: curve of the base resolution can be interpolated in time between them.
_EXTRA_HISTORY = 4

#: How many frames of the base resolution the module keeps the floor and the
#: reference of, so that the curves of the extra resolutions can be computed
#: against the frame that lies at their point of the signal.
_BASE_HISTORY = 8

#: The problem the module asks the model seam for.  The de-noiser's problem
#: is the **ambience** of the material: the bed the wanted content sits in.
#: The shipped model of that problem describes the ambience of a frame as a
#: spectrum, which is a noise floor.  The voice problem is the other
#: candidate: its model produces a mask, and the module reads that mask as a
#: signal-to-noise ratio (see :meth:`SpectralDenoise._model_floor`).
_MODEL_PROBLEM = "ambience"

#: The rule the model's mask and the module's own floor are combined by.
#:
#: ``"replace"``
#:     The model's mask *is* the floor.  The per-bin floor the module
#:     re-estimates every frame is not used at all: the module asks the model
#:     what the material is and applies the answer.
#: ``"pessimistic"``
#:     The model may only say that there is **more** noise than the module
#:     thinks, never less: the floor of the module is the higher of the two.
#:     The floor of the module can then not fall below the description the
#:     model makes of the material, so the frame-to-frame movement of the
#:     estimate is cut off and the gain curve stops breathing -- and the
#:     removal is never lower than the one of the hand written path.
#: ``"hold"``
#:     The higher of the two wins, and the floor the module estimates is
#:     *held still* over the time constant of the material instead of being
#:     re-estimated from every frame.
_MODEL_FLOOR_RULES = ("replace", "pessimistic", "optimistic", "hold")

#: The rule the module uses.
_MODEL_FLOOR_RULE = "pessimistic"

#: The time constant of the held floor, in seconds.  It is long enough for a
#: steady bed to look the same in every frame and short enough for a change
#: of the material to be noticed.
_MODEL_HOLD_TAU_S = 0.25

#: The lowest and the highest signal-to-noise ratio the model's mask may
#: stand for, in dB.  A mask of zero is "no wanted content at all" and a
#: mask of one is "nothing but wanted content"; the two ends are clipped so
#: that the floor of the module stays a number.
_MODEL_SNR_LIMITS_DB = (-60.0, 60.0)


# ---------------------------------------------------------------------------
# small numeric helpers (kept local so the module works without SciPy)
# ---------------------------------------------------------------------------
def median_filter_axis(x: np.ndarray, axis: int, width: int) -> np.ndarray:
    """Median filter along ``axis`` with a window of ``width`` samples.

    SciPy's ``ndimage.median_filter`` is used when available; otherwise a
    NumPy sliding-window implementation takes over.

    Parameters
    ----------
    x:
        Input array.
    axis:
        Axis along which the filter runs.
    width:
        Window length in samples (odd values are rounded up).

    Returns
    -------
    numpy.ndarray
        The filtered array, same shape as ``x``.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if width % 2 == 0:
        width += 1
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


def soft_weight(snr_db: np.ndarray, threshold_db: float, knee_db: float) -> np.ndarray:
    """Raised-cosine weight that turns an SNR into a "how noisy is this bin".

    The weight is ``1`` at or below ``threshold_db`` and falls to ``0``
    ``knee_db`` above it.  A ``knee_db`` of zero produces a hard gate.

    Parameters
    ----------
    snr_db:
        Per-bin signal-to-noise ratio in dB.
    threshold_db:
        The ratio at which the bin is fully considered noise.
    knee_db:
        Width of the soft transition in dB.

    Returns
    -------
    numpy.ndarray
        Weights in ``[0, 1]``, same shape as ``snr_db``.
    """
    snr = np.asarray(snr_db, dtype=np.float64)
    if knee_db <= 0.0:
        return (snr <= float(threshold_db)).astype(np.float64)
    x = np.clip((snr - float(threshold_db)) / float(knee_db), 0.0, 1.0)
    return 0.5 * (1.0 + np.cos(np.pi * x))


def _median_along_last(view: np.ndarray) -> np.ndarray:
    """The median of every line of ``view``, exactly as NumPy computes it.

    ``numpy.median`` sorts every line; ``numpy.partition`` finds the same two
    order statistics without the sort, and the two agree bit for bit (the
    median of an even number of values is the mean of the two middle ones).
    The broadband floor is the hottest routine of the module, so the
    difference is worth a factor of three in the cost of a frame.
    """
    n = int(view.shape[-1])
    if n % 2 == 1:
        part = np.partition(view, n // 2, axis=-1)
        return part[..., n // 2]
    part = np.partition(view, [n // 2 - 1, n // 2], axis=-1)
    return 0.5 * (part[..., n // 2 - 1] + part[..., n // 2])


def broadband_floor(x: np.ndarray, gap: int = 4, span: int = 16) -> np.ndarray:
    """Robust broadband level estimate that ignores narrowband tonal peaks.

    For every bin the median is taken of the bins that lie *outside* a central
    gap of ``gap`` bins but inside a window of ``span`` bins.  The louder of
    the two side medians is used, so a single peak can never raise the floor.

    Parameters
    ----------
    x:
        Magnitude spectrogram in dB, shape ``(frames, bins)``.
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
    if arr.ndim == 1:
        arr = arr[None, :]
    frames, bins = arr.shape
    width = max(1, int(span) - int(gap))
    if bins < 2 * int(gap) + 2 or span <= gap:
        return median_filter_axis(arr, 1, 5)
    padded = np.pad(arr, ((0, 0), (span, span)), mode="edge")
    out = np.empty_like(arr)
    for i in range(frames):
        windows = np.lib.stride_tricks.sliding_window_view(padded[i], width)
        left = _median_along_last(windows[:bins])
        right = _median_along_last(windows[gap + 1 : gap + 1 + bins])
        out[i] = np.maximum(left, right)
    return out


def _resample_frequency(
    values: np.ndarray, src_freqs: np.ndarray, dst_freqs: np.ndarray
) -> np.ndarray:
    """Interpolate every line of ``values`` from one frequency grid onto another.

    The interpolation is linear and clamps outside the source grid, exactly
    like ``numpy.interp`` does.  It is the frequency half of
    :func:`aurcore.dsp.spectral.resample_gain_2d`, kept local because the
    time half is done by the module itself, frame by frame.
    """
    src = np.asarray(src_freqs, dtype=np.float64).ravel()
    dst = np.asarray(dst_freqs, dtype=np.float64).ravel()
    arr = np.asarray(values, dtype=np.float64)
    n = int(src.size)
    if n == 0 or arr.size == 0:
        return np.zeros((arr.shape[0], dst.size), dtype=np.float64)
    if n == 1:
        return np.repeat(arr[:, :1], dst.size, axis=1)
    idx = np.searchsorted(src, dst, side="right") - 1
    below = dst < src[0]
    above = dst > src[-1]
    idx = np.clip(idx, 0, n - 2)
    lo = src[idx]
    hi = src[idx + 1]
    dx = hi - lo
    safe = np.where(dx > 0.0, dx, 1.0)
    w = np.where(dx > 0.0, (dst - lo) / safe, 0.0)
    w[below] = 0.0
    w[above] = 1.0
    idx[below] = 0
    idx[above] = n - 2
    return arr[:, idx] * (1.0 - w)[None, :] + arr[:, idx + 1] * w[None, :]


class _DenoiseState:
    """Per-channel tracking state of the denoiser.

    The first group of entries is the tracking state of the gain law, the
    second group the machinery of the streaming analysis.
    """

    __slots__ = (
        "sm_db",
        "min_db",
        "floor_db",
        "ref_db",
        "prev_gain_db",
        "prev_mag",
        "dev_db",
        "avg_db",
        "tonal_acc",
        "learned_floor_db",
        "learned_tonal",
        "base",
        "extras",
        "delay_buf",
        "fifo",
        "extra_hist",
        "extra_pending",
        "extra_prev",
        "extra_count",
        "base_count",
        "base_hist",
        "rng",
        "seed",
        "key",
        "model_buf",
        "model_at",
        "model_hold",
        "model_runner",
    )

    def __init__(self) -> None:
        self.sm_db: np.ndarray | None = None
        self.min_db: np.ndarray | None = None
        self.floor_db: np.ndarray | None = None
        self.ref_db: np.ndarray | None = None
        self.prev_gain_db: np.ndarray | None = None
        self.prev_mag: np.ndarray | None = None
        self.dev_db: np.ndarray | None = None
        self.avg_db: np.ndarray | None = None
        self.tonal_acc: np.ndarray | None = None
        self.learned_floor_db: np.ndarray | None = None
        self.learned_tonal: np.ndarray | None = None
        # the streaming analysis
        self.base: StreamingSTFT | None = None
        self.extras: dict[int, StreamingSTFT] = {}
        self.delay_buf: np.ndarray | None = None
        self.fifo: np.ndarray | None = None
        # the extra resolutions: the curves that are known, the frames that
        # wait for the base resolution to catch up with them, and the
        # magnitude of the frame before the queue
        self.extra_hist: dict[int, list[tuple[float, np.ndarray, np.ndarray]]] = {}
        self.extra_pending: dict[int, list[tuple[float, np.ndarray]]] = {}
        self.extra_prev: dict[int, np.ndarray] = {}
        self.extra_count: dict[int, int] = {}
        self.base_count: int = 0
        # the floor and the reference of the recent frames of the base
        # resolution, with the time of the frame they belong to
        self.base_hist: list[tuple[float, np.ndarray, np.ndarray]] = []
        # the samples the model reads its own frames from, and the position
        # of the first of them in the stream of the base resolution
        self.model_buf: np.ndarray | None = None
        self.model_at: int = 0
        # the floor the module holds still while the model runs
        self.model_hold: np.ndarray | None = None
        # The runner of the model: it holds the history the model reads, and
        # every channel has its own, because the history of one channel is
        # not the history of the next.
        self.model_runner = None
        # Every channel has its own generator: the noise that the module
        # synthesises has to be the same whatever the block size of the host
        # is, and a generator that is shared between the channels would hand
        # the two channels the numbers in the order the blocks arrive.
        self.rng: np.random.Generator | None = None
        self.seed: int = 0
        self.key: tuple | None = None

    def reset(self) -> None:
        """Drop every learned profile, every tracker and the analysis state."""
        seed = self.seed
        for name in self.__slots__:
            setattr(self, name, None)
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.extras = {}
        self.extra_hist = {}
        self.extra_pending = {}
        self.extra_prev = {}
        self.extra_count = {}
        self.base_count = 0
        self.base_hist = []
        self.model_at = 0


@register
class SpectralDenoise(Module):
    """Spectral De-Noise — multi-resolution spectral noise reduction."""

    code = "AUR_SPECTRALDENOISE"
    display_name = "Spectral De-Noise"
    category = "Repair"
    edition = "standard"
    element = "Denoiser"
    analysis = True
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
                name="Algorithm",
                type="enum",
                default="Advanced",
                choices=("Simple", "Advanced", "Extreme"),
                group="Algorithm",
                help="Simple: single resolution subtraction. Advanced: OM-LSA refinement. Extreme: "
                "multi resolution plus stronger artifact control.",
            ),
            desc(
                name="Quality",
                type="enum",
                default="B",
                choices=("A", "B", "C", "D"),
                group="Quality",
                help="A is the fastest and least accurate, D the slowest and most accurate.",
            ),
            desc(
                name="FFT Size",
                type="float",
                default=20.0,
                minimum=5.0,
                maximum=60.0,
                unit="ms",
                group="Analysis",
                help="Length of the analysis window.",
            ),
            desc(
                name="Multi Resolution",
                type="bool",
                default=True,
                group="Analysis",
                help="Analyse several window sizes and combine the gain curves.",
            ),
            desc(
                name="Learning Time",
                type="float",
                default=2.0,
                minimum=0.5,
                maximum=30.0,
                unit="s",
                group="Learning",
                help="How long the noise profile is captured.",
            ),
            desc(
                name="Adaptive",
                type="bool",
                default=True,
                group="Learning",
                help="Keep tracking the noise profile while processing.",
            ),
            desc(
                name="Noise Threshold",
                type="float",
                default=0.0,
                minimum=-40.0,
                maximum=20.0,
                unit="dB",
                group="Threshold",
                help="Estimated signal to noise ratio below which a bin counts as noise.",
            ),
            desc(
                name="Tonal Threshold",
                type="float",
                default=0.0,
                minimum=-40.0,
                maximum=20.0,
                unit="dB",
                group="Threshold",
                help="Same, measured against the broadband floor for tonal components.",
            ),
            desc(
                name="Noise Reduction",
                type="float",
                default=15.0,
                minimum=0.0,
                maximum=40.0,
                unit="dB",
                group="Reduction",
                help="Maximum broadband attenuation.",
            ),
            desc(
                name="Tonal Reduction",
                type="float",
                default=12.0,
                minimum=0.0,
                maximum=40.0,
                unit="dB",
                group="Reduction",
                help="Maximum attenuation applied to tonal components.",
            ),
            desc(
                name="Artifact Control",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help="Temporal smoothing of the gain curve (musical noise control).",
            ),
            desc(
                name="Smoothing",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Smoothing",
                help="Frequency smoothing width of the gain curve.",
            ),
            desc(
                name="Knee",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Dynamics",
                help="Soft knee width of the reduction law.",
            ),
            desc(
                name="Release",
                type="float",
                default=100.0,
                minimum=10.0,
                maximum=1000.0,
                unit="ms",
                group="Dynamics",
                help="Gain release time once wanted content returns.",
            ),
            desc(
                name="Noise Floor Synthesis",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Noise Floor",
                help="Regenerate the removed noise floor instead of leaving a hole.",
            ),
            desc(
                name="Masking",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Noise Floor",
                help="Psychoacoustic masking model limits how far a bin may be attenuated.",
            ),
            desc(
                name="Enhancement",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Noise Floor",
                help="Re-add low level but wanted content.",
            ),
            desc(
                name="Whitening",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Noise Floor",
                help="Flatten the long term spectrum towards a target curve.",
            ),
            desc(
                name="Output Noise Only",
                type="bool",
                default=False,
                group="Output",
                help="Render only the noise that was removed.",
            ),
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._states = [self._new_state(index) for index in range(max(1, int(channels)))]
        self._block_engines: dict[tuple[int, int], STFT] = {}
        self._rng = np.random.default_rng(0xA0A15)
        #: The mode the module was in when the state was written.  It travels
        #: with the state, so that a session that was rendered offline says
        #: so when it is opened again; it never decides the mode the module
        #: runs in.
        self._state_mode = self.processing_mode
        self._init_model()

    @staticmethod
    def _new_state(index: int) -> _DenoiseState:
        """A fresh tracking state with its own generator."""
        state = _DenoiseState()
        state.seed = 0xA0A15 + int(index)
        state.rng = np.random.default_rng(state.seed)
        return state

    def reset(self) -> None:
        """Drop every learned profile and tracking state."""
        for state in self._states:
            state.reset()
        if self._model is not None:
            # The runner of the model is part of the state of the module, so
            # it is dropped with the rest of it and built again: a module
            # that has been reset starts a new stream with a fresh model.
            self._model_runner = models.ModelRunner(self._model)
            for state in self._states:
                state.model_runner = models.ModelRunner(self._model)

    def _state(self, channel: int) -> _DenoiseState:
        while len(self._states) <= channel:
            state = self._new_state(len(self._states))
            if self._model is not None:
                # A channel that appears while the module runs gets its own
                # runner of the model: the history the model reads is the
                # history of one channel.
                state.model_runner = models.ModelRunner(self._model)
            self._states.append(state)
        return self._states[channel]

    # -- the model ------------------------------------------------------------
    # The model is an addition to the **offline** path.  Nothing in this
    # section changes what the module does when there is no model: the hand
    # written path is the path, and it is bit for bit the path the module has
    # always taken.
    #
    # What the model is for: the floor the module uses is re-estimated from
    # every frame of the signal, so the floor moves and the gain moves with
    # it -- the gain curve of a steady bed breathes.  A model decides from a
    # quantity that does not carry the level of the input and does not
    # re-estimate from scratch every frame, so a floor that comes from a
    # model does not breathe.

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
        #: Whether anybody chose a model at all.  Nobody choosing means the
        #: module asks the seam for the model that solves its problem; a
        #: session that chose the empty name means the hand written path.
        self._model_chosen = False
        #: Why there is no model, or what the running model is.
        self._model_message = ""
        #: The delay the context of the model costs, in samples.
        self._model_delay = 0
        #: The bin of the model that speaks for every bin of the module.
        self._model_bins: np.ndarray | None = None
        #: The window the model reads its frames with.
        self._model_window: np.ndarray | None = None

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
        """The description of the running model, or nothing."""
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

        The answer is a message: it says which model runs now and -- when no
        model runs -- why not.  A message is also kept in
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
        for state in self._states:
            state.model_buf = None
            state.model_at = 0
            state.model_hold = None
            state.model_runner = None

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

    def _bind_default(self) -> str:
        """Take the model that solves the noise problem into use."""
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
        # Every channel gets its own runner: the history the model reads is
        # the history of one channel, not of the next.
        for state in self._states:
            state.model_runner = models.ModelRunner(binding.model)

    def _model_refusal(self, manifest) -> str:
        """Why the module cannot run this model ("" when it can).

        A model that does not run at the configuration of the module is not
        loaded: the module does what it does today and says why.
        """
        if manifest.output.kind not in ("spectral_mask", "spectrum"):
            return (
                f"the model {manifest.name!r} produces a {manifest.output.kind!r}; "
                "the module applies a spectral mask or reads a spectrum"
            )
        if manifest.input.channels != 1:
            return (
                f"the model {manifest.name!r} reads {manifest.input.channels} "
                "channels at a time; the module analyses the mix of the channels"
            )
        if int(manifest.input.sample_rate) != int(self.sample_rate):
            return (
                f"the model {manifest.name!r} runs at {manifest.input.sample_rate} Hz; "
                f"the module runs at {self.sample_rate} Hz"
            )
        if int(manifest.input.frame_size) > int(self._config()["fft"]):
            return (
                f"the model {manifest.name!r} reads a frame of "
                f"{manifest.input.frame_size} samples; the module analyses "
                f"{self._config()['fft']}"
            )
        return ""

    def _engine_window(self, size: int) -> np.ndarray:
        """The analysis window of the model's own frame size."""
        return np.asarray(
            STFT(int(size), int(size) // 4, window="hann").win, dtype=np.float64
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
        freqs = np.fft.rfftfreq(int(self._config()["fft"]), 1.0 / float(self.sample_rate))
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
        if self._model_requested:
            self._bind(self._model_requested)
        elif not self._model_chosen:
            # Nobody chose: the module asks for the model that solves its
            # problem, and it runs its hand written path when there is none.
            self._bind_default()

    # -- the model at work ----------------------------------------------------
    def _model_magnitudes(
        self, state: _DenoiseState, first: int, count: int, cfg: dict[str, Any]
    ) -> np.ndarray | None:
        """The magnitudes the model reads, on the model's own window.

        The frames of the model sit on the **same centres** as the frames of
        the base resolution: the model reads the same instant of the stream
        with its own window, so the two analyses speak about the same moment.

        Parameters
        ----------
        state:
            The state of the channel.
        first:
            The index of the first frame of the base resolution.
        count:
            The number of frames.
        cfg:
            The configuration of the module.

        Returns
        -------
        numpy.ndarray or None
            The magnitudes of the frames, shape ``(bins, frames)``.
        """
        if self._model is None:
            return None
        manifest = self._model.manifest
        size = int(manifest.input.frame_size)
        half = size // 2
        hop = int(cfg["hop"])

        buf = state.model_buf
        if buf is None:
            buf = np.zeros(0, dtype=np.float64)
        # the samples the first of the frames needs
        lo = int(first) * hop - half
        hi = (int(first) + int(count) - 1) * hop + half
        start = lo - int(state.model_at)
        stop = hi - int(state.model_at)
        if start < 0:
            # the stream has not enough history yet: the model reads zeros
            segment = np.concatenate(
                (np.zeros(-start, dtype=np.float64), buf[: max(stop, 0)])
            )
        else:
            segment = buf[start:stop]
        if segment.size < size:
            segment = np.concatenate(
                (segment, np.zeros(size - segment.size, dtype=np.float64))
            )
        views = np.lib.stride_tricks.sliding_window_view(segment, size)
        first_view = int(first) * hop - half - lo
        # The frames of the model sit one hop apart, like the frames of the
        # base resolution: the window of the model is the window of the base
        # frame, centred on the same instant.
        frames = views[first_view : first_view + (int(count) - 1) * hop + 1 : hop]
        if frames.shape[0] < int(count):
            return None
        spectrum = np.fft.rfft(frames * self._model_window, axis=1)
        return np.abs(spectrum).T

    def _model_output(
        self, state: _DenoiseState, magnitudes: np.ndarray, cfg: dict[str, Any]
    ) -> np.ndarray | None:
        """What the model produced, on the bins of the base resolution.

        A model that produces a **mask** hands back a gain per bin; a model
        that produces a **spectrum** hands back the spectrum of the noise
        itself, which is the floor of the de-noiser.  The two are told apart
        by the manifest of the model.
        """
        if self._model_runner is None or self._model_bins is None:
            return None
        runner = state.model_runner
        if runner is None:
            return None
        # The frames are handed to the model one by one: the runner of the
        # model reads the frame before the one it answers for, and the frames
        # it reads are the frames of the analysis of the module -- not the
        # frames of the call.  A model that reads a history therefore gives
        # the same answer whatever the size of the blocks the host uses.
        values = np.empty_like(magnitudes)
        for index in range(int(magnitudes.shape[1])):
            output = runner.process_frame(magnitudes[:, index])
            values[:, index] = output.values
        if self._model.manifest.output.kind == "spectral_mask":
            mask = np.asarray(values[self._model_bins, :], dtype=np.float64).T
            return np.clip(mask, 0.0, 1.0)
        # A spectrum: the model describes the noise of the material.  Its
        # output is the level of the noise on the scale of the features of
        # the manifest, so it is read back into decibels.
        spectrum = np.asarray(values[self._model_bins, :], dtype=np.float64).T
        floor_db = self._model.manifest.input.features.floor_db
        span = -floor_db if floor_db < 0 else 1.0
        return floor_db + span * np.clip(spectrum, 0.0, 1.0)

    def _model_floor(
        self,
        sm_db: np.ndarray,
        mask: np.ndarray | None,
        state: _DenoiseState,
        cfg: dict[str, Any],
        estimated: np.ndarray,
    ) -> np.ndarray:
        """The floor the module applies when a model runs.

        A model that produces a **spectrum** hands back the floor itself: the
        floor the module applies is the spectrum the model produced, and the
        floor the module estimates every frame is not used at all.

        A model that produces a **mask** hands back a gain per bin: one means
        "wanted content", zero means "noise".  The signal-to-noise ratio the
        mask stands for is the ratio of the two, and the floor of the module
        is the level of the bin minus that ratio.

        The floor of the module is combined with the floor the module
        estimates itself by the rule of :data:`_MODEL_FLOOR_RULE`:

        ``"replace"``
            the floor of the model is the floor; the floor the module
            estimates every frame is not used at all,
        ``"pessimistic"``
            the higher of the two floors wins, so the model may only make the
            module remove more noise,
        ``"optimistic"``
            the lower of the two floors wins, so the model may only make the
            module remove less noise: the removal of the hand written path is
            never given up,
        ``"hold"``
            the higher of the two wins, and the floor the module estimates is
            held still over the time constant of the material instead of
            being re-estimated from every frame.
        """
        if mask is None:
            return estimated
        if self._model is not None and self._model.manifest.output.kind != "spectral_mask":
            # The model produced a spectrum: that spectrum is the floor.
            if _MODEL_FLOOR_RULE == "pessimistic":
                return np.maximum(estimated, mask)
            if _MODEL_FLOOR_RULE == "optimistic":
                return np.minimum(estimated, mask)
            if _MODEL_FLOOR_RULE == "hold":
                return np.maximum(self._hold_floor(estimated, state, cfg), mask)
            return mask
        # the signal-to-noise ratio the mask stands for
        wanted = np.clip(mask, 1e-6, 1.0 - 1e-6)
        snr_db = 10.0 * np.log10(wanted / (1.0 - wanted))
        snr_db = np.clip(snr_db, _MODEL_SNR_LIMITS_DB[0], _MODEL_SNR_LIMITS_DB[1])
        model_floor = sm_db - snr_db

        rule = _MODEL_FLOOR_RULE
        if rule == "replace":
            return model_floor
        if rule == "pessimistic":
            return np.maximum(estimated, model_floor)
        if rule == "optimistic":
            return np.minimum(estimated, model_floor)
        if rule == "hold":
            held = self._hold_floor(estimated, state, cfg)
            return np.maximum(held, model_floor)
        return model_floor

    def _hold_floor(
        self, estimated: np.ndarray, state: _DenoiseState, cfg: dict[str, Any]
    ) -> np.ndarray:
        """The floor of the module, held still over the time constant.

        The floor the module estimates is re-derived from every frame, so it
        moves and the gain moves with it.  Here it is held: the new value
        follows the old one with the time constant of the material, so a
        steady bed produces the same floor in every frame and the gain curve
        of the bed does not breathe.
        """
        alpha = 1.0 - math.exp(
            -float(cfg["hop"]) / max(1e-6, self.sample_rate * _MODEL_HOLD_TAU_S)
        )
        held = state.model_hold
        if held is None or held.shape != estimated.shape[-1]:
            held = estimated[-1].copy()
            state.model_hold = held
        out = np.empty_like(estimated)
        current = held.astype(np.float64, copy=True)
        for i in range(estimated.shape[0]):
            current = current + alpha * (estimated[i] - current)
            out[i] = current
        state.model_hold = current
        return out

    # -- learning -------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Capture a noise profile (per bin floor plus tonal peaks).

        The block is limited to ``Learning Time`` seconds.  The profile holds
        the per-bin noise floor and the tonal components found in the noise, so
        that hum or whine can be attenuated separately from broadband noise.

        Learning is an offline action, so it analyses the whole block at
        once: it does not run in the audio path and its cost does not count
        against the realtime budget.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return
        seconds = float(self.params.get("Learning Time"))
        limit = max(1, int(seconds * self.sample_rate))
        arr = arr[:limit]

        cfg = self._config()
        for ch in range(arr.shape[1]):
            state = self._state(ch)
            mag, _ = self._block_engine(cfg["fft"], cfg["hop"]).analyze_block(
                arr[:, ch], center=True
            )
            mag_db = np.asarray(mag[0] if mag.ndim == 3 else mag, dtype=np.float64).T
            if mag_db.size == 0:
                continue
            profile = np.asarray(
                _spectral.estimate_noise_profile(
                    mag_db, mag_db.shape[0], percentile=_LEARN_PERCENTILE
                ),
                dtype=np.float64,
            )
            if profile.shape[-1] != mag_db.shape[-1]:
                profile = np.resize(profile, mag_db.shape[-1])
            state.learned_floor_db = profile
            broad = median_filter_axis(profile, 0, 9)
            tonal = (profile - broad) > 8.0
            state.learned_tonal = tonal
            if state.floor_db is None or state.floor_db.shape[-1] != profile.shape[-1]:
                state.floor_db = profile.copy()
                state.min_db = profile.copy()
                state.sm_db = None
                state.prev_gain_db = None

    # -- configuration --------------------------------------------------------
    def _config(self) -> dict[str, Any]:
        """Resolve the parameter set into concrete DSP settings."""
        p = self.params
        algorithm = str(p.get("Algorithm"))
        quality = str(p.get("Quality"))
        q = _QUALITY_TABLE.get(quality, _QUALITY_TABLE["C"])

        fft_ms = float(p.get("FFT Size"))
        window = float(q["fft_scale"])
        if self.processing_mode == PROCESSING_MODE_OFFLINE:
            # The offline configuration analyses a longer window.  There is no
            # deadline in that mode, and the longer window is what separates
            # the wanted content from the noise.
            window *= _OFFLINE_WINDOW_SCALE
        fft = int(round(self.sample_rate * fft_ms / 1000.0 * window))
        fft = max(128, min(8192, fft))
        fft += fft % 2
        overlap = int(q["overlap"])
        hop = max(16, fft // overlap)

        omlsa = bool(q["omlsa"])
        multi = bool(p.get("Multi Resolution"))
        if algorithm == "Simple":
            omlsa = False
            multi = False
        elif algorithm == "Extreme":
            omlsa = True
            multi = True

        knee = float(p.get("Knee"))
        artifact = float(p.get("Artifact Control"))
        smoothing = float(p.get("Smoothing"))
        if algorithm == "Extreme":
            artifact = min(1.0, artifact + 0.25)

        extra_ffts: list[int] = []
        if multi:
            for factor in (2, 0.5):
                cand = int(round(fft * factor))
                cand = max(128, min(8192, cand))
                cand += cand % 2
                if cand != fft:
                    extra_ffts.append(cand)

        # The hop of every extra resolution, and the look-ahead the module
        # needs because the curve of a longer window is only known once the
        # samples on both sides of that window have arrived.
        extra_hops: dict[int, int] = {}
        lookahead = 0
        half = fft // 2
        for extra_fft in extra_ffts:
            hop_extra = max(16, int(round(extra_fft * hop / float(fft))))
            extra_hops[extra_fft] = hop_extra
            half_extra = extra_fft // 2
            # The extra frame that brackets the base frame has to be analysed
            # before the base frame can be closed, and the frame after it
            # has to be analysed before its curve can be computed: the
            # magnitude of an extra frame is smoothed along the time axis.
            need = (hop_extra + half_extra - half + 1) // 2
            lookahead = max(lookahead, need + hop_extra)
        lookahead = max(0, int(lookahead))

        # The latency of the streaming engine: the samples it needs before the
        # first output sample is ready.
        base_latency = (half // hop) * hop + fft - half

        # The engine holds the frames that have not been synthesised yet, so
        # the samples it is fed at once may not complete more frames than it
        # can hold.  A few frames at a time is always safe and keeps the
        # number of NumPy calls low.
        chunk = 4 * hop

        # The curves of the extra resolutions have to reach far enough back
        # for the interpolation on the frames of the base resolution.
        extra_history = _EXTRA_HISTORY
        if extra_hops:
            fastest = min(extra_hops.values())
            extra_history = max(
                extra_history,
                int(math.ceil((chunk + lookahead) / float(max(1, fastest)))) + 2,
            )

        return {
            "fft": fft,
            "hop": hop,
            "extra_ffts": extra_ffts,
            "extra_hops": extra_hops,
            "extra_history": extra_history,
            "chunk": chunk,
            "lookahead": lookahead,
            "base_latency": base_latency,
            "latency": base_latency + lookahead,
            "omlsa": omlsa,
            "noise_threshold": float(p.get("Noise Threshold")),
            "tonal_threshold": float(p.get("Tonal Threshold")),
            "noise_reduction": float(p.get("Noise Reduction")),
            "tonal_reduction": float(p.get("Tonal Reduction")),
            "knee_db": 2.0 + 14.0 * knee,
            "freq_smooth_bins": 1 + int(round(6.0 * smoothing)),
            "artifact": artifact,
            "release_s": max(0.001, float(p.get("Release")) / 1000.0),
            "attack_s": 0.004,
            "synthesis": float(p.get("Noise Floor Synthesis")),
            "masking": float(p.get("Masking")),
            "enhancement": float(p.get("Enhancement")),
            "whitening": float(p.get("Whitening")),
            "noise_only": bool(p.get("Output Noise Only")),
            "smooth_tau": 0.025,
            "min_rise_db": 0.15,
            "floor_bias_db": 12.0,
        }

    def _block_engine(self, fft: int, hop: int) -> STFT:
        """The whole-signal engine, used by the learning action."""
        key = (int(fft), int(hop))
        engine = self._block_engines.get(key)
        if engine is None:
            engine = STFT(fft, hop, window="hann")
            self._block_engines[key] = engine
        return engine

    # -- the streaming analysis ----------------------------------------------
    def _prepare_stream(self, state: _DenoiseState, cfg: dict[str, Any]) -> None:
        """Make sure the channel has the engines of the current configuration.

        A configuration change replaces the analysis grid, so the streaming
        state cannot continue: the engines and the delay lines start again.
        The learned profile survives, it does not depend on the grid.
        """
        key = (cfg["fft"], cfg["hop"], tuple(cfg["extra_ffts"]))
        if state.key == key and state.base is not None:
            return
        state.base = StreamingSTFT(cfg["fft"], cfg["hop"], window="hann")
        state.extras = {
            extra_fft: StreamingSTFT(
                extra_fft, cfg["extra_hops"][extra_fft], window="hann"
            )
            for extra_fft in cfg["extra_ffts"]
        }
        state.delay_buf = np.zeros(0, dtype=np.float64)
        state.fifo = None
        state.extra_hist = {extra_fft: [] for extra_fft in cfg["extra_ffts"]}
        state.extra_pending = {extra_fft: [] for extra_fft in cfg["extra_ffts"]}
        state.extra_prev = {}
        state.extra_count = {}
        state.base_count = 0
        state.key = key
        # The model reads the samples of the base resolution, so the grid it
        # reads on has changed as well: its samples and its history start
        # again.
        state.model_buf = None
        state.model_at = 0
        state.model_hold = None
        if self._model_runner is not None:
            self._model_runner.reset()
            for state in self._states:
                if state.model_runner is not None:
                    state.model_runner.reset()

    def _push_delay(
        self, state: _DenoiseState, signal: np.ndarray, cfg: dict[str, Any]
    ) -> np.ndarray:
        """Hold the signal back and return the part that is old enough.

        The base resolution is fed a signal that is ``lookahead`` samples
        old: the curves of the longer windows are then known before the frame
        of the base window that they belong to is closed, so the two
        resolutions stay aligned in time.
        """
        delay = int(cfg["lookahead"])
        buf = state.delay_buf
        buf = np.concatenate([buf, signal]) if buf is not None and buf.size else signal
        take = int(buf.size) - delay
        if take <= 0:
            state.delay_buf = buf
            return np.zeros(0, dtype=np.float64)
        out = buf[:take]
        state.delay_buf = buf[take:]
        return out

    def _take_output(
        self, state: _DenoiseState, count: int, cfg: dict[str, Any]
    ) -> np.ndarray:
        """The output line: the rendered samples delayed by the latency.

        The streaming engine hands out the samples as soon as they are ready,
        which depends on the size of the blocks it is fed.  The module keeps
        its own line of ``base_latency`` samples so that the delay is the
        same whatever the host uses, and so that the result does not depend
        on the block size.
        """
        fifo = state.fifo
        if fifo is None:
            # The context of the model is a delay of its own: the mask of a
            # frame is only known once the frames before it have been read.
            # The line has to carry it, or the module would declare a
            # latency it does not add.
            fifo = np.zeros(
                int(cfg["latency"]) + int(self._model_delay), dtype=np.float64
            )
        take = int(min(count, fifo.size))
        out = fifo[:take]
        state.fifo = fifo[take:]
        if out.size < count:
            out = np.concatenate(
                [out, np.zeros(count - out.size, dtype=np.float64)], axis=0
            )
        return out

    def _push_render(
        self,
        state: _DenoiseState,
        rendered: np.ndarray,
        fed: np.ndarray,
        cfg: dict[str, Any],
    ) -> None:
        """Put the rendered samples into the output line."""
        if cfg["noise_only"] and rendered.size:
            take = int(min(rendered.size, fed.size))
            rendered = fed[:take] - rendered[:take]
        if not rendered.size:
            return
        fifo = state.fifo
        if fifo is None:
            fifo = np.zeros(
                int(cfg["latency"]) + int(self._model_delay), dtype=np.float64
            )
        state.fifo = np.concatenate([fifo, rendered])

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Denoise a block of audio.

        Parameters
        ----------
        x:
            Block of audio, shape ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The denoised block, same shape as ``x``.  It is delayed by
            :attr:`latency_samples` samples.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        self._ensure_model()
        cfg = self._config()
        out = np.empty((arr.shape[0], arr.shape[1]), dtype=np.float64)
        for ch in range(arr.shape[1]):
            out[:, ch] = self._process_channel(arr[:, ch], self._state(ch), cfg)
        return out

    def _process_channel(
        self, signal: np.ndarray, state: _DenoiseState, cfg: dict[str, Any]
    ) -> np.ndarray:
        """Denoise one channel of a block, frame by frame.

        The block is walked in chunks of one hop: the engine holds the frames
        that have not been synthesised yet, so it may not be given more
        samples at once than it can hold the frames of.
        """
        self._prepare_stream(state, cfg)
        count = int(signal.size)
        chunk = int(cfg["chunk"])

        # -- the extra resolutions see the signal as it is
        for extra_fft in cfg["extra_ffts"]:
            engine = state.extras[extra_fft]
            pos = 0
            while pos < count:
                piece = signal[pos : pos + chunk]
                pos += chunk
                mag, _ = engine.analyze(piece)
                if mag.shape[-1]:
                    self._queue_extra(state, extra_fft, mag, cfg)

        # -- the base resolution sees the signal as it was ``lookahead`` ago
        fed = self._push_delay(state, signal, cfg)
        if self._model_runner is not None and fed.size:
            # The model reads the samples of the base resolution, so they are
            # kept until the frames that need them have been read.
            buf = state.model_buf
            if buf is None or not buf.size:
                state.model_buf = np.asarray(fed, dtype=np.float64)
            else:
                state.model_buf = np.concatenate((buf, fed))
        pos = 0
        while pos < fed.size:
            piece = fed[pos : pos + chunk]
            pos += chunk
            base_mag, base_phase = state.base.analyze(piece)
            if not base_mag.shape[-1]:
                continue
            first = state.base_count
            state.base_count += int(base_mag.shape[-1])
            mask = self._model_output_for(state, first, int(base_mag.shape[-1]), cfg)
            rendered = self._render(base_mag, base_phase, state, cfg, mask)
            self._push_render(state, rendered, piece, cfg)

        self._trim_model_buffer(state, cfg)
        return self._take_output(state, count, cfg)

    def _model_output_for(
        self, state: _DenoiseState, first: int, count: int, cfg: dict[str, Any]
    ) -> np.ndarray | None:
        """The mask of the model for the frames of the base resolution."""
        if self._model_runner is None:
            return None
        magnitudes = self._model_magnitudes(state, first, count, cfg)
        if magnitudes is None:
            return None
        return self._model_output(state, magnitudes, cfg)

    def _trim_model_buffer(self, state: _DenoiseState, cfg: dict[str, Any]) -> None:
        """Drop the samples no frame of the model needs any more."""
        if self._model_runner is None or state.model_buf is None:
            return
        size = int(self._model.manifest.input.frame_size)
        keep_from = int(state.base_count) * int(cfg["hop"]) - size // 2 - 1
        drop = keep_from - int(state.model_at)
        if drop > 0:
            state.model_buf = state.model_buf[drop:].copy()
            state.model_at += int(drop)

    def _render(
        self,
        mag: np.ndarray,
        phase: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
        mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """Run the full denoising chain over the new frames of the base window."""
        mag = np.asarray(mag.T, dtype=np.float64)  # (frames, bins)
        phase = np.asarray(phase.T, dtype=np.float64)

        sm_db = self._smooth_magnitude(mag, state, cfg)
        broad_db = broadband_floor(
            sm_db, gap=self._broad_gap(cfg), span=self._broad_span(cfg)
        )
        ref_db = self._noise_reference(state, broad_db)
        tonal_mask = np.clip((sm_db - ref_db) / 6.0, 0.0, 1.0)
        # The tonal decision is made against the learned noise profile when
        # one is available: the instantaneous broadband estimate is polluted
        # by the wanted partials that sit next to it, and that pollution
        # makes the de-noiser treat wanted content as noise.
        learned_tonal = self._learned_tonal_mask(sm_db.shape[-1])
        # Tonal peaks that belong to the learned noise profile are noise, not
        # wanted content: they must not be protected by the tonal law.
        blend = tonal_mask * (1.0 - learned_tonal)
        floor_db = self._noise_floor(
            sm_db, state, cfg, ref_db, tonal_mask, learned_tonal, mask
        )

        # The base resolution has caught up with the frames of the extra
        # resolutions that it needs, so their curves can be computed.  The
        # drain runs frame by frame: the curve of an extra frame is computed
        # against the floor and the reference of the base frame that reaches
        # its point of the signal, so that the result does not depend on the
        # size of the blocks the host uses.
        base_times = self._base_times(state, mag.shape[0], cfg)
        for i in range(mag.shape[0]):
            state.base_hist.append(
                (float(base_times[i]), floor_db[i].copy(), ref_db[i].copy())
            )
        while len(state.base_hist) > _BASE_HISTORY:
            state.base_hist.pop(0)
        if mag.shape[0]:
            self._drain_extra(state, cfg, float(base_times[-1]))

        gain_db = self._gain_curve(mag, sm_db, floor_db, ref_db, blend, state, cfg)

        if cfg["extra_ffts"]:
            gain_db = self._multi_resolution_gain(
                gain_db, blend, base_times, state, cfg
            )
        gain_db = self._apply_masking(gain_db, mag, cfg, snr_db=sm_db - floor_db)
        gain = db_to_lin(gain_db)

        spec = mag * np.exp(1j * phase)
        out_spec = spec * gain
        out_spec = self._apply_enhancement(out_spec, sm_db, floor_db, state, cfg)
        out_spec = self._apply_synthesis(out_spec, floor_db, gain, state)
        out_spec = self._apply_whitening(out_spec, sm_db, tonal_mask, state, cfg)

        out_mag = np.abs(out_spec)
        out_phase = np.angle(out_spec)
        return state.base.synthesize(out_mag.T, out_phase.T)

    # -- gain estimation ------------------------------------------------------
    def _smooth_magnitude(
        self, mag: np.ndarray, state: _DenoiseState, cfg: dict[str, Any]
    ) -> np.ndarray:
        """Time smoothed magnitude in dB plus the minimum-statistics tracker.

        The tracker follows the lowest level a bin has recently seen, which is
        what a noise floor looks like for a stationary noise bed.
        """
        mag_db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        beta = 1.0 - math.exp(-cfg["hop"] / max(1e-6, self.sample_rate * cfg["smooth_tau"]))
        if state.sm_db is None or state.sm_db.shape[-1] != mag_db.shape[-1]:
            state.sm_db = mag_db[0].copy()
            state.dev_db = np.zeros(mag_db.shape[-1], dtype=np.float64)
            state.min_db = mag_db[0].copy()
        smoothed = np.empty_like(mag_db)
        prev = state.sm_db
        tracker = state.min_db
        rise = cfg["min_rise_db"]
        for i in range(mag_db.shape[0]):
            cur = mag_db[i]
            prev = prev + beta * (cur - prev)
            smoothed[i] = prev
            tracker = np.minimum(tracker + rise, prev)
        state.sm_db = prev
        state.min_db = tracker
        # The deviation is tracked frame by frame: a block local average would
        # make the tracker depend on the size of the blocks the host uses.
        deviation = np.abs(mag_db - smoothed)
        dev = state.dev_db
        for i in range(deviation.shape[0]):
            dev = dev + beta * (deviation[i] - dev)
        state.dev_db = dev
        return smoothed

    def _broad_gap(self, cfg: dict[str, Any]) -> int:
        """Half width of the zone the broadband floor excludes, in bins."""
        bin_hz = float(self.sample_rate) / float(cfg["fft"])
        return max(2, int(round(_BROAD_GAP_HZ / bin_hz)))

    def _broad_span(self, cfg: dict[str, Any]) -> int:
        """Half width of the broadband floor's analysis window, in bins."""
        bin_hz = float(self.sample_rate) / float(cfg["fft"])
        return max(self._broad_gap(cfg) + 4, int(round(_BROAD_SPAN_HZ / bin_hz)))

    def _noise_reference(
        self, state: _DenoiseState, broad_db: np.ndarray
    ) -> np.ndarray:
        """The per-bin noise level the tonal decision is made against.

        The learned profile is used when it is available: it is captured
        from a noise-only block, so it is not polluted by the wanted
        content that sits next to it.  Without a profile the instantaneous
        broadband estimate is the best available reference.
        """
        learned = state.learned_floor_db
        if learned is None:
            return broad_db
        bins = broad_db.shape[-1]
        if learned.shape[-1] != bins:
            learned = np.interp(
                np.linspace(0.0, 1.0, bins),
                np.linspace(0.0, 1.0, learned.shape[-1]),
                learned,
            )
            state.learned_floor_db = learned
        # the profile is the reference of every frame
        return np.broadcast_to(learned[None, :], broad_db.shape)

    def _learned_tonal_mask(self, bins: int) -> np.ndarray:
        """Return the learned tonal peaks as a 0/1 array over the bins."""
        mask = np.zeros(bins, dtype=np.float64)
        for state in self._states:
            tonal = state.learned_tonal
            if tonal is None:
                continue
            if tonal.shape[-1] != bins:
                tonal = np.interp(
                    np.linspace(0.0, 1.0, bins),
                    np.linspace(0.0, 1.0, tonal.shape[-1]),
                    tonal.astype(np.float64),
                ) > 0.5
            mask = np.maximum(mask, tonal.astype(np.float64))
        return mask

    def _noise_floor(
        self,
        sm_db: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
        ref_db: np.ndarray,
        tonal_mask: np.ndarray,
        learned_tonal: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """Per-bin broadband noise floor in dB.

        The floor combines a two sided broadband estimate (robust against
        narrowband tonal content) with a minimum-statistics tracker (follows a
        changing noise floor).  Bins that carry wanted tonal content use the
        broadband estimate only: a stationary tone would otherwise pin the
        tracker to the tone level.

        When a model runs, the floor the module applies is the one the model
        produces (see :meth:`_model_floor`): the floor that is re-estimated
        from every frame is what makes the gain curve breathe.
        """
        floor = np.maximum(ref_db, state.min_db[None, :] + cfg["floor_bias_db"])
        wanted_tonal = (tonal_mask > 0.5) & (learned_tonal < 0.5)
        # The mask is applied frame by frame: a frame without wanted tonal
        # content must not change the floor of the frames around it.
        floor = np.where(wanted_tonal, ref_db, floor)

        learned = state.learned_floor_db
        if learned is not None:
            if learned.shape[-1] != floor.shape[-1]:
                learned = np.interp(
                    np.linspace(0.0, 1.0, floor.shape[-1]),
                    np.linspace(0.0, 1.0, learned.shape[-1]),
                    learned,
                )
                state.learned_floor_db = learned
            if bool(self.params.get("Adaptive")):
                floor = np.maximum(floor, learned[None, :])
            else:
                floor = learned[None, :] + (floor - learned[None, :]) * 0.25

        if mask is not None:
            floor = self._model_floor(sm_db, mask, state, cfg, floor)

        # The extra resolutions are computed against the floor and the
        # reference of the last frame of the base resolution.
        state.floor_db = floor[-1].copy()
        state.ref_db = np.asarray(ref_db[-1], dtype=np.float64).copy()
        return floor

    def _gain_curve(
        self,
        mag: np.ndarray,
        sm_db: np.ndarray,
        floor_db: np.ndarray,
        ref_db: np.ndarray,
        blend: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        """Build the broadband + tonal gain curve in dB."""
        snr = sm_db - floor_db
        weight = soft_weight(snr, cfg["noise_threshold"], cfg["knee_db"])
        depth = weight * cfg["noise_reduction"]

        # The estimator compares powers.  A floor in dB is a magnitude, so
        # the conversion to a power belongs to the DSP layer: feeding the
        # magnitude instead drives gamma and xi orders of magnitude too
        # low, the speech presence probability collapses to zero and the
        # branch saturates the reduction instead of refining it.
        noise_power = _spectral.noise_power_from_db(floor_db)
        if cfg["omlsa"]:
            gamma = (mag * mag) / noise_power
            prior = state.prev_mag
            om_depth = np.empty_like(depth)
            for i in range(mag.shape[0]):
                prev = mag[i] if prior is None else prior
                xi = _spectral.decision_directed_snr(
                    prev, mag[i], 0.98, noise_power[i]
                )
                gain = _spectral.om_lsa_gain(gamma[i], xi, 0.0, 1.0)
                om_depth[i] = np.maximum(0.0, -lin_to_db(np.maximum(gain, 1e-6)))
                # the a-priori estimate of the next frame
                prior = mag[i] * db_to_lin(-depth[i])
            depth = np.maximum(depth, om_depth)
        depth = np.minimum(depth, cfg["noise_reduction"])
        gain_db = -depth

        # Tonal components are judged against the noise reference instead.
        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, cfg["tonal_threshold"], cfg["knee_db"])
        tonal_depth = np.minimum(tonal_weight * cfg["tonal_reduction"], cfg["tonal_reduction"])
        tonal_gain_db = -tonal_depth
        gain_db = gain_db * (1.0 - blend) + tonal_gain_db * blend

        # Frequency smoothing, but never across a bin that carries content.
        protect_snr = np.where(blend > 0.5, tonal_snr, snr)
        protect = protect_snr > 6.0
        width = int(cfg["freq_smooth_bins"])
        if width > 1:
            smoothed = _spectral.smooth_gain_curve(gain_db, width, "gaussian")
            gain_db = np.where(protect, gain_db, smoothed)

        # Temporal smoothing: fast attack, release controlled by the Release knob.
        gain_db = self._temporal_smooth(gain_db, state, cfg)
        if cfg["omlsa"]:
            state.prev_mag = (mag * db_to_lin(gain_db))[-1].copy()
        return gain_db

    def _temporal_smooth(
        self, gain_db: np.ndarray, state: _DenoiseState, cfg: dict[str, Any]
    ) -> np.ndarray:
        """Smooth the gain curve across frames (musical-noise control).

        ``gain_db`` is laid out as ``(frames, bins)``, so the smoothing runs
        along axis 0 and the carried state is the last smoothed frame.
        """
        release = cfg["release_s"] * (0.5 + cfg["artifact"])
        alpha_rel = 1.0 - math.exp(-cfg["hop"] / max(1e-6, self.sample_rate * release))
        alpha_att = 1.0 - math.exp(-cfg["hop"] / max(1e-6, self.sample_rate * cfg["attack_s"]))
        n_frames = gain_db.shape[0]
        n_bins = gain_db.shape[1]
        if state.prev_gain_db is None or state.prev_gain_db.shape != (n_bins,):
            # The smoothing starts with the first block: frame 0 keeps its
            # raw value, the rest is smoothed against the running state.
            # Returning the raw curve (as before) meant that the
            # musical-noise control did nothing at all for a render that
            # arrives in one piece.
            current = gain_db[0].astype(np.float64, copy=True)
            out = gain_db.copy()
            for i in range(1, n_frames):
                target = gain_db[i]
                alpha = np.where(target > current, alpha_rel, alpha_att)
                current = current + alpha * (target - current)
                out[i] = current
            state.prev_gain_db = out[-1].copy()
            return out
        prev = state.prev_gain_db
        out = np.empty_like(gain_db)
        current = prev.astype(np.float64, copy=True)
        for i in range(n_frames):
            target = gain_db[i]
            alpha = np.where(target > current, alpha_rel, alpha_att)
            current = current + alpha * (target - current)
            out[i] = current
        state.prev_gain_db = out[-1].copy()
        return out

    # -- the extra resolutions ------------------------------------------------
    def _queue_extra(
        self,
        state: _DenoiseState,
        extra_fft: int,
        mag: np.ndarray,
        cfg: dict[str, Any],
    ) -> None:
        """Queue the new frames of an extra resolution.

        The frames wait in the queue until the base resolution has caught up
        with them: the curve of an extra frame is computed against the floor
        and the reference of the base resolution, and those are only known
        once the base resolution has reached that point of the signal.
        """
        queue = state.extra_pending.setdefault(extra_fft, [])
        hop_extra = int(cfg["extra_hops"][extra_fft])
        first = int(state.extra_count.get(extra_fft, 0))
        for i in range(int(mag.shape[-1])):
            queue.append(((first + i) * hop_extra, mag[:, i].copy()))
        state.extra_count[extra_fft] = first + int(mag.shape[-1])

    def _drain_extra(
        self,
        state: _DenoiseState,
        cfg: dict[str, Any],
        limit: float,
    ) -> None:
        """Compute the curves of the queued frames of the extra resolutions.

        A frame is ready when the base resolution has reached its point of
        the signal: the curve is computed against the floor and the
        reference of the base frame that lies at that point, and the frame
        that follows it is needed for the time smoothing of its magnitude.
        The newest frame of the queue waits for its successor, so that the
        smoothing always sees the frame that follows it.
        """
        if not state.base_hist:
            return
        freqs = np.fft.rfftfreq(cfg["fft"], 1.0 / float(self.sample_rate))
        for extra_fft in cfg["extra_ffts"]:
            queue = state.extra_pending.get(extra_fft)
            if not queue:
                continue
            take = 0
            for item in queue:
                if item[0] > limit:
                    break
                take += 1
            # the newest frame waits for the frame that follows it
            take = min(take, len(queue) - 1)
            if take <= 0:
                continue
            items = queue[:take]
            del queue[:take]
            mag = np.stack([item[1] for item in items], axis=0)
            times = [item[0] for item in items]
            extra_freqs = np.fft.rfftfreq(extra_fft, 1.0 / float(self.sample_rate))
            sm_db = self._extra_magnitude(state, extra_fft, mag, queue)
            # The frames are grouped by the frame of the base resolution they
            # take their floor and their reference from: a group of frames
            # that share the reference is computed in one go.
            groups: list[tuple[int, list[int]]] = []
            for i, t in enumerate(times):
                index = self._reference_index(state, t)
                if groups and groups[-1][0] == index:
                    groups[-1][1].append(i)
                else:
                    groups.append((index, [i]))
            history = state.extra_hist.setdefault(extra_fft, [])
            for index, members in groups:
                floor, ref = self._reference_at(state, index)
                if floor is None or ref is None:
                    continue
                extra_floor = np.interp(extra_freqs, freqs, floor)[None, :]
                extra_ref = np.interp(extra_freqs, freqs, ref)[None, :]
                tonal = np.clip((sm_db[members] - extra_ref) / 6.0, 0.0, 1.0)
                learned = self._learned_tonal_mask(extra_freqs.size)
                extra_blend = tonal * (1.0 - learned[None, :])
                extra_gain = self._gain_curve_static(
                    mag[members], sm_db[members], extra_floor, extra_ref, extra_blend, cfg
                )
                for k, i in enumerate(members):
                    history.append(
                        (times[i], extra_gain[k].copy(), extra_blend[k].copy())
                    )
            while len(history) > int(cfg["extra_history"]):
                history.pop(0)

    @staticmethod
    def _reference_index(state: _DenoiseState, time: float) -> int:
        """The index of the base frame that lies at ``time``."""
        for index in range(len(state.base_hist) - 1, -1, -1):
            if state.base_hist[index][0] <= time:
                return index
        return 0

    @staticmethod
    def _reference_at(
        state: _DenoiseState, index: int
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """The floor and the reference of the base frame at ``index``."""
        if not state.base_hist:
            return None, None
        index = int(np.clip(index, 0, len(state.base_hist) - 1))
        return state.base_hist[index][1], state.base_hist[index][2]

    def _extra_magnitude(
        self,
        state: _DenoiseState,
        extra_fft: int,
        mag: np.ndarray,
        queue: list[tuple[float, np.ndarray]],
    ) -> np.ndarray:
        """The magnitude of the extra frames, smoothed along the time axis.

        The smoothing is centred, so the frame before and the frame after a
        frame take part in it.  The frame before is the one that was queued
        last, the frame after the one that is queued next.
        """
        db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        count = int(db.shape[0])
        previous = state.extra_prev.get(extra_fft)
        left = np.empty_like(db)
        if previous is None:
            left[0] = db[0]
        else:
            left[0] = previous
        if count > 1:
            left[1:] = db[:-1]
        right = np.empty_like(db)
        if queue:
            right[-1] = 20.0 * np.log10(np.maximum(queue[0][1], 1e-12))
        else:
            right[-1] = db[-1]
        if count > 1:
            right[:-1] = db[1:]
        state.extra_prev[extra_fft] = db[-1].copy()
        return (left + db + right) / 3.0

    def _multi_resolution_gain(
        self,
        gain_db: np.ndarray,
        blend: np.ndarray,
        base_times: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        """Combine the gain curves of several window sizes (most conservative wins).

        The extra resolutions keep the same overlap ratio, so their frame
        centres still line up with the base grid; the curves are interpolated
        in time and frequency before they are combined.
        """
        freqs = np.fft.rfftfreq(cfg["fft"], 1.0 / float(self.sample_rate))
        for extra_fft in cfg["extra_ffts"]:
            history = state.extra_hist.get(extra_fft)
            if not history:
                continue
            extra_freqs = np.fft.rfftfreq(extra_fft, 1.0 / float(self.sample_rate))
            extra_gain, extra_blend = self._interpolate(
                history, base_times, extra_freqs, freqs
            )
            # Tonal content is decided by the base resolution: a finer window
            # resolves the main lobe differently and must not notch it away.
            protect = (blend > 0.5) | (extra_blend > 0.5)
            gain_db = np.where(protect, gain_db, np.minimum(gain_db, extra_gain))
        return gain_db

    def _base_times(
        self, state: _DenoiseState, n_frames: int, cfg: dict[str, Any]
    ) -> np.ndarray:
        """The time of the new frames of the base resolution, in samples.

        The base resolution is fed a signal that is ``lookahead`` samples
        old, so its frames sit that much earlier in time than the frames of
        the extra resolutions, which see the signal as it is.
        """
        first = state.base_count - int(n_frames)
        index = first + np.arange(int(n_frames), dtype=np.float64)
        return index * float(cfg["hop"]) - float(cfg["lookahead"])

    @staticmethod
    def _interpolate(
        history: list[tuple[float, np.ndarray, np.ndarray]],
        base_times: np.ndarray,
        src_freqs: np.ndarray,
        dst_freqs: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Interpolate the curves of an extra resolution onto the base grid.

        Only the frames of the extra resolution that lie at or before the
        time of the base frame take part: the curve of an extra frame is
        not known before the base resolution has reached that point of the
        signal, so a curve from the future would make the result depend on
        the size of the blocks the host uses.  The frequency axis is
        interpolated between the bins of the two resolutions.
        """
        times = np.array([item[0] for item in history], dtype=np.float64)
        curves = [item[1] for item in history]
        blends = [item[2] for item in history]
        count = int(times.size)
        # the last frame of the extra resolution at or before the base frame
        idx = np.searchsorted(times, base_times, side="right") - 1
        idx = np.clip(idx, 0, count - 1).astype(np.intp)
        return (
            _resample_frequency(
                np.stack([curves[i] for i in idx]), src_freqs, dst_freqs
            ),
            _resample_frequency(
                np.stack([blends[i] for i in idx]), src_freqs, dst_freqs
            ),
        )

    def _gain_curve_static(
        self,
        mag: np.ndarray,
        sm_db: np.ndarray,
        floor_db: np.ndarray,
        ref_db: np.ndarray,
        blend: np.ndarray,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        """Gain curve without the temporal state (used by the extra resolutions)."""
        snr = sm_db - floor_db
        weight = soft_weight(snr, cfg["noise_threshold"], cfg["knee_db"])
        depth = np.minimum(weight * cfg["noise_reduction"], cfg["noise_reduction"])
        gain_db = -depth
        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, cfg["tonal_threshold"], cfg["knee_db"])
        tonal_gain_db = -np.minimum(
            tonal_weight * cfg["tonal_reduction"], cfg["tonal_reduction"]
        )
        gain_db = gain_db * (1.0 - blend) + tonal_gain_db * blend
        width = int(cfg["freq_smooth_bins"])
        if width > 1:
            protect = np.where(blend > 0.5, tonal_snr, snr) > 6.0
            smoothed = _spectral.smooth_gain_curve(gain_db, width, "gaussian")
            gain_db = np.where(protect, gain_db, smoothed)
        return gain_db

    # -- noise floor features -------------------------------------------------
    def _apply_masking(
        self,
        gain_db: np.ndarray,
        mag: np.ndarray,
        cfg: dict[str, Any],
        snr_db: np.ndarray | None = None,
    ) -> np.ndarray:
        """Limit the attenuation with a psychoacoustic masking model.

        The model protects wanted content from being masked by its
        neighbours.  It must therefore only relax the attenuation of a bin
        that carries wanted content: applied to a bin that is pure noise it
        silently undoes the noise reduction, and because the mask follows
        the instantaneous spectrum it also makes the gain curve breathe -
        which is exactly what musical noise is.

        The model is applied frame by frame: it averages the spectrum over
        the frames it is given, so a batch of frames would give a different
        mask than the same frames one by one.
        """
        amount = float(cfg["masking"])
        if amount <= 0.0:
            return gain_db
        mag_db = 20.0 * np.log10(np.maximum(mag, 1e-12))
        offset = -(14.0 + 26.0 * (1.0 - amount))
        out = np.empty_like(gain_db)
        for i in range(gain_db.shape[0]):
            try:
                mask_db = _spectral.masking_model(
                    mag_db[i : i + 1], offset, int(self.sample_rate), int(cfg["fft"])
                )
            except Exception:
                return gain_db
            limit = np.minimum(mask_db[0] - mag_db[i], 0.0)
            # The mask may only *relax* the attenuation, never amplify a bin.
            if snr_db is not None:
                noise_only = snr_db[i] <= float(cfg["noise_threshold"])
                limit = np.where(noise_only, -np.inf, limit)
            out[i] = gain_db[i] + amount * np.maximum(0.0, limit - gain_db[i])
        return out

    def _apply_enhancement(
        self,
        out_spec: np.ndarray,
        sm_db: np.ndarray,
        floor_db: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        """Re-add low level but temporally consistent content."""
        amount = float(cfg["enhancement"])
        if amount <= 0.0:
            return out_spec
        snr = sm_db - floor_db
        window = np.clip(snr / 12.0, 0.0, 1.0) * np.clip((12.0 - snr) / 12.0, 0.0, 1.0)
        if state.dev_db is None:
            return out_spec
        consistency = np.exp(-state.dev_db[None, :] / 6.0)
        boost = 1.0 + amount * 0.5 * window * consistency
        return out_spec * boost

    def _apply_synthesis(
        self,
        out_spec: np.ndarray,
        floor_db: np.ndarray,
        gain: np.ndarray,
        state: _DenoiseState,
    ) -> np.ndarray:
        """Regenerate the noise floor that was removed (fills spectral holes).

        The synthesised noise follows the shape of the noise profile and the
        level of the *surrounding* bands, so only genuine holes are filled: a
        band that was reduced uniformly keeps its reduced level.
        """
        amount = float(self.params.get("Noise Floor Synthesis"))
        if amount <= 0.0:
            return out_spec
        noise_level = np.maximum(db_to_lin(floor_db), 1e-18)
        neighbour_gain = median_filter_axis(gain, 1, 9)
        # The target is a power - the level of the removed noise after the
        # gain has been applied.  Mixing a level with a power made the
        # synthesised floor far too loud.
        target = (noise_level * neighbour_gain) ** 2
        out_power = np.abs(out_spec) ** 2
        fill = np.maximum(0.0, 0.35 * target - out_power)
        synth = np.sqrt(fill) * amount
        if not np.any(synth > 0.0):
            return out_spec
        rng = state.rng if state.rng is not None else self._rng
        # The phase is drawn frame by frame: a batch of frames would take
        # the numbers of the generator in another order, and the noise the
        # module synthesises has to be the same whatever the block size of
        # the host is.  A frame without a hole does not draw at all, so that
        # the frames around it keep their numbers.
        out = out_spec.copy()
        for i in range(synth.shape[0]):
            if not np.any(synth[i] > 0.0):
                continue
            random_phase = rng.uniform(0.0, 2.0 * math.pi, size=synth[i].shape)
            out[i] = out_spec[i] + synth[i] * np.exp(1j * random_phase)
        return out

    def _apply_whitening(
        self,
        out_spec: np.ndarray,
        sm_db: np.ndarray,
        tonal_mask: np.ndarray,
        state: _DenoiseState,
        cfg: dict[str, Any],
    ) -> np.ndarray:
        """Flatten the long term spectrum towards a target curve.

        Tonal bins are excluded from the average (they would otherwise tilt
        the whole correction curve) and they are never re-levelled, so wanted
        content keeps its level.
        """
        amount = float(cfg["whitening"])
        if amount <= 0.0:
            return out_spec
        bins = sm_db.shape[-1]
        if state.avg_db is None or state.avg_db.shape[-1] != bins:
            state.avg_db = sm_db[0].copy()
            state.tonal_acc = np.zeros(bins, dtype=np.float64)
        beta = 0.05
        out = out_spec.copy()
        # The whitening runs frame by frame: the average is tracked frame by
        # frame, and the tonal bins of a frame are the ones of that frame, so
        # that the result does not depend on the size of the blocks the host
        # uses.
        for i in range(sm_db.shape[0]):
            state.avg_db = state.avg_db + beta * (sm_db[i] - state.avg_db)
            state.tonal_acc = state.tonal_acc + beta * (tonal_mask[i] - state.tonal_acc)
            avg = state.avg_db.copy()
            tonal_bins = (state.tonal_acc > 0.5) | (tonal_mask[i] > 0.5)
            if np.any(tonal_bins):
                broad = broadband_floor(
                    sm_db[i : i + 1], gap=max(2, self._broad_gap(cfg) // 2),
                    span=max(8, self._broad_span(cfg) // 2)
                )[0]
                avg[tonal_bins] = broad[tonal_bins]
            target = np.full(bins, float(np.mean(avg)), dtype=np.float64)
            try:
                gain_db = _spectral.whiten(
                    avg, target, mag_is_db=True, target_is_db=True, preserve_level=True
                )
            except Exception:
                continue
            gain_db = np.where(tonal_bins, 0.0, gain_db)
            out[i] = out_spec[i] * db_to_lin(gain_db * amount)
        return out

    # -- serialisation --------------------------------------------------------
    def _state_blob(self) -> bytes:
        """Serialise the learned profiles and the mode the module was in."""
        payload = []
        for state in self._states:
            floor = state.learned_floor_db
            tonal = state.learned_tonal
            payload.append(
                {
                    "floor": floor if floor is not None else np.zeros(0),
                    "tonal": tonal.astype(np.uint8) if tonal is not None else np.zeros(0),
                }
            )
        buf = io.BytesIO()
        np.savez(
            buf,
            count=len(payload),
            mode=np.asarray(self.processing_mode),
            **{
                f"floor_{i}": p["floor"]
                for i, p in enumerate(payload)
            },
            **{
                f"tonal_{i}": p["tonal"]
                for i, p in enumerate(payload)
            },
        )
        return buf.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore the learned profiles and the mode the module was in."""
        mode = np.zeros(0)
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as archive:
                count = int(archive["count"])
                for i in range(count):
                    state = self._state(i)
                    floor = archive[f"floor_{i}"]
                    tonal = archive[f"tonal_{i}"]
                    state.learned_floor_db = floor if floor.size else None
                    state.learned_tonal = tonal.astype(bool) if tonal.size else None
                if "mode" in archive.files:
                    mode = archive["mode"]
        except Exception:
            return
        # The mode of the render travels with the state, so that a session
        # that was rendered offline says so when it is opened again.  It does
        # not decide the mode the module runs in: the engine of the chain
        # owns that.
        if mode.size:
            self._state_mode = str(np.asarray(mode).ravel()[0])

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
        """Samples of delay the module adds.

        The centred analysis needs the samples that follow a frame, and the
        multi-resolution analysis needs the samples that follow the *longer*
        window, so the delay is the latency of the streaming engine plus the
        look-ahead of the extra resolutions.

        The figure follows the configuration the module is in: the realtime
        configuration analyses a 720 sample window and declares 1440 samples
        of delay, the offline configuration analyses a 1080 sample window and
        declares 2160.

        A model that reads a context needs that history before it can
        answer, and **that context is latency**: the module publishes it, so
        the host is told how late the audio really is.  Without a model the
        figure is the one the module has always published.
        """
        return int(self._config()["latency"]) + int(self._model_delay)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The centred analysis works on frames that reach into the future, so
        the frames that follow the last one still hold samples of the input
        and the gain they carry fades out.  Measured the way the host
        measures it, the output stays above -60 dBFS for 1812 samples after
        the input stopped; 1440 of them are the latency the module
        declares, so the tail is the 372 that are left.  The number carries
        a margin of 140 samples (2.9 ms), so that a small change of the
        analysis does not break the contract.
        """
        return 512
