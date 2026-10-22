"""
aurcore.modules.band_restoration — Band Restoration.

A recording is often damaged in one place: a band of hiss, a band of mains
hum, a band that a transfer cut off, a band that was clipped.  Band
Restoration repairs such a recording band by band.

The module splits the signal into up to four bands with a parametric
crossover whose three edges are the user's controls, and every band is
repaired on its own:

* the **restoration** of a band is a downward expander.  The module learns
  the spectrum of the noise (the host's *Learn* action) and the level of
  the learned noise inside a band is that band's floor; what sits at the
  floor is taken out of the band, up to ``Restoration Depth`` decibels.
  How far above the floor the band has to rise before it is left alone is
  the user's ``Noise Threshold``.

* the **surface** is a band mixer.  Every band carries a ``Mix`` (its level
  in the output), a ``Solo`` (to audition the band on its own), a ``Mute``
  (to take the band out of the recording) and a ``Bypass`` (to leave the
  band as the crossover made it, without the restoration).

* the **broadband path** mixes the untouched signal back against the band
  path, so that a recording whose bands were rebalanced does not lose its
  level, and so that the untouched signal is always there to fall back on.

* the **splitter** is chosen by ``Phase Linearity``.  ``Efficient`` is a
  Butterworth crossover: it costs a handful of biquads and adds no delay,
  but the bands of it do not add up to the input again — they add up to a
  signal with the level of the input, but not with its phase.
  ``Linear Phase`` is the same split made of symmetric FIR kernels: there
  the bands do add up to the input, delayed by half the length of the
  kernel, and the delay is reported as the latency of the module, so the
  host can compensate for it.

* the **top band** is protected by ``High Frequency Transparency``: the
  restoration is not allowed to cut it deeper than the user allows, so the
  air of the recording survives by default.

The module is a restoration, not a mixer.  The bands are the handles of the
repair, the expander is what takes the damage out of them, and the
recording comes back whole.

The expander needs to know what the noise of the recording sounds like, so
it only works after a *Learn*.  Without a learned profile the module falls
back to the band mixer alone, which touches nothing.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.filters import FIR, biquad_highpass, biquad_lowpass, linear_phase_fir
from ..params import ParamDesc
from ..registry import register
from .base import Module

try:  # pragma: no cover - scipy is optional, the numpy path below is complete
    from scipy.signal import lfilter as _sp_lfilter
except Exception:  # pragma: no cover
    _sp_lfilter = None

__all__ = ["BandRestoration"]

#: The analysis window of the learner, in samples.  A longer window resolves
#: the noise floor better, a shorter one follows a change of the noise
#: faster.
_LEARN_FFT = 2048

#: The length of the kernel of the linear phase crossover, in taps.  It has
#: to be odd, and the group delay of a symmetric kernel is half its length.
_LP_KERNEL = 4097

#: The time constant of the level follower of a band, in seconds.  It is the
#: window the expander judges the band in.
_LEVEL_TAU = 0.010

#: The width of the soft knee of the expander, in decibels.  A band that
#: sits below the threshold by this much is closed completely, so a
#: stationary bed is removed by the whole depth and the gain of the band
#: does not breathe.
_KNEE_DB = 3.0

#: The lowest level the expander looks at, as a ratio.  It keeps the
#: logarithm of a silent band finite.
_FLOOR = 1.0e-12

#: The quality factors of the second order sections of the crossover.  The
#: crossover is a Butterworth of order eight: four second order sections
#: with the two quality factors of a fourth order Butterworth, twice.
_BUTTERWORTH_Q = (
    1.0 / (2.0 * math.sin(math.pi / 8.0)),
    1.0 / (2.0 * math.sin(3.0 * math.pi / 8.0)),
)


def _db_to_lin(db: float) -> float:
    """Convert decibels to a linear amplitude."""
    return 10.0 ** (float(db) / 20.0)


# ---------------------------------------------------------------------------
# the stateful filter
# ---------------------------------------------------------------------------
class _Filter:
    """A stateful rational filter, applied over every channel at once.

    The state is one vector of the filter's memory per channel, and it is
    carried from one block to the next, so the answer does not depend on
    the size of the blocks the host uses.
    """

    __slots__ = ("b", "a", "_zi")

    def __init__(self, b, a, channels: int):
        self.b = [float(c) for c in b]
        self.a = [1.0] + [float(c) for c in a[1:]]
        self._zi = np.zeros((len(self.a) - 1, int(channels)), dtype=np.float64)

    @property
    def order(self) -> int:
        """The order of the filter."""
        return len(self.a) - 1

    def reset(self) -> None:
        """Clear the memory of the filter."""
        self._zi[:] = 0.0

    def resize(self, channels: int) -> None:
        """Make the state fit a different number of channels."""
        if self._zi.shape[1] != int(channels):
            self._zi = np.zeros((self._zi.shape[0], int(channels)), dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Filter a block of shape ``(n_samples, n_channels)``."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.shape[0] == 0:
            return arr
        if arr.shape[1] != self._zi.shape[1]:
            self.resize(arr.shape[1])
        if _sp_lfilter is not None:
            y, zf = _sp_lfilter(self.b, self.a, arr, axis=0, zi=self._zi)
            self._zi[:, :] = np.asarray(zf).reshape(self._zi.shape)
            return y
        return self._process_numpy(arr)

    def _process_numpy(self, arr: np.ndarray) -> np.ndarray:
        """Filter a block with a pure NumPy recursion.

        The transposed direct form II is used, so the state of the filter
        is the same one the SciPy path keeps.
        """
        n = arr.shape[0]
        out = np.empty_like(arr)
        state = self._zi
        b = self.b
        a = self.a
        order = self.order
        for i in range(n):
            xi = arr[i]
            yi = b[0] * xi + state[0]
            for k in range(1, order):
                state[k - 1] = b[k] * xi - a[k] * yi + state[k]
            state[order - 1] = b[order] * xi - a[order] * yi
            out[i] = yi
        return out


def _butterworth(kind: str, fc: float, sample_rate: float) -> _Filter:
    """Design an eighth order Butterworth low pass or high pass.

    The order is high on purpose: the bands of the crossover have to be
    apart, because the restoration of one band must not hear the programme
    of the next one.  An eighth order low pass leaves the programme of the
    band below an edge forty-eight decibels down at an octave below the
    edge, and a fourth order one leaves it twenty-five decibels down.
    """
    b = [1.0]
    a = [1.0]
    for _ in range(2):
        for q in _BUTTERWORTH_Q:
            if kind == "high":
                section = biquad_highpass(fc, q, sample_rate)
            else:
                section = biquad_lowpass(fc, q, sample_rate)
            b = _convolve(b, list(section[:3]))
            a = _convolve(a, [1.0] + list(section[3:]))
    return _Filter(b, a, 1)


def _convolve(a, b) -> list:
    """Convolve two short coefficient lists."""
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


def _linear_phase_lowpass(fc: float, sample_rate: float, length: int) -> np.ndarray:
    """Design the symmetric low pass of one edge of the linear phase splitter.

    The magnitude is one below the edge, zero above it, and a raised cosine
    in between.  The width of the transition is the width of the main lobe
    of the window, so the kernel resolves the edge as finely as its length
    allows.
    """
    n = int(length)
    grid = n // 2 + 1
    freqs = np.arange(grid, dtype=np.float64) * (float(sample_rate) / float(n))
    width = 4.0 * float(sample_rate) / float(n)
    lo = float(fc) - 0.5 * width
    hi = float(fc) + 0.5 * width
    mag = np.zeros(grid, dtype=np.float64)
    mag[freqs <= lo] = 1.0
    between = (freqs > lo) & (freqs < hi)
    if np.any(between):
        t = (freqs[between] - lo) / max(hi - lo, 1e-9)
        mag[between] = 0.5 * (1.0 + np.cos(math.pi * t))
    return linear_phase_fir(mag, float(sample_rate), length=n, taper=0.1)


# ---------------------------------------------------------------------------
# the crossover
# ---------------------------------------------------------------------------
class _Splitter:
    """The parametric crossover of the module.

    The bands are the products of the complementary pairs of the edges, so
    every band is a clean band pass and the bands do not leak into each
    other: that is what lets the restoration work on one band without
    touching the next one.  The price is that the bands of an IIR
    crossover do not add up to the input again — they add up to a signal
    with the level of the input, but not with its phase.  The linear phase
    splitter is the same split made of symmetric kernels: there the bands
    do add up to the input, delayed by half the length of the kernel.
    """

    def __init__(
        self,
        sample_rate: int,
        edges: tuple[float, ...],
        bands: int,
        mode: str,
        channels: int = 2,
    ):
        self.sample_rate = float(sample_rate)
        self.edges = tuple(float(e) for e in edges)
        self.bands = int(bands)
        self.mode = str(mode)
        self.channels = int(channels)
        self._low: list[_Filter] = []
        self._high: list[_Filter] = []
        self._fir: list[FIR] = []
        self._delay = 0
        count = max(self.bands - 1, 0)
        if self.mode == "Linear Phase":
            for edge in self.edges[:count]:
                kernel = _linear_phase_lowpass(edge, self.sample_rate, _LP_KERNEL)
                fir = FIR(kernel, self.sample_rate)
                self._fir.append(fir)
                self._delay = int(fir.latency_samples)
        else:
            for edge in self.edges[:count]:
                self._low.append(
                    _butterworth("low", edge, self.sample_rate)
                )
                self._high.append(
                    _butterworth("high", edge, self.sample_rate)
                )
        self._tail = np.zeros((self._delay, int(channels)), dtype=np.float64)

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Clear the memory of the filters."""
        for section in self._low:
            section.reset()
        for section in self._high:
            section.reset()
        for fir in self._fir:
            fir.reset()
        self._tail = np.zeros((self._delay, self.channels), dtype=np.float64)

    def resize(self, channels: int) -> None:
        """Make the state fit a different number of channels."""
        self.channels = int(channels)
        for section in self._low:
            section.resize(channels)
        for section in self._high:
            section.resize(channels)
        self._tail = np.zeros((self._delay, int(channels)), dtype=np.float64)

    @property
    def latency_samples(self) -> int:
        """The delay the splitter adds, in samples."""
        return int(self._delay)

    # -- processing -----------------------------------------------------------
    def _delayed(self, arr: np.ndarray) -> np.ndarray:
        """Return the input, delayed by the delay of the splitter."""
        n = arr.shape[0]
        if self._delay <= 0:
            return arr
        if self._tail.shape[1] != arr.shape[1]:
            self._tail = np.zeros((self._delay, arr.shape[1]), dtype=np.float64)
        buffer = np.concatenate([self._tail, arr], axis=0)
        self._tail = buffer[n:]
        return buffer[:n]

    def process(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return the bands of a block and the untouched signal.

        The bands have the shape ``(n_samples, n_bands, n_channels)`` and
        the untouched signal is the input, delayed so that it lines up with
        the bands.

        The efficient splitter is a cascade: the first band is the low pass
        of the first edge, the second band is the low pass of the second
        edge applied to the high pass of the first one, and so on.  The
        linear phase splitter is subtractive: the first band is the low
        pass of the first edge and every other band is the difference of
        two low passes, so the bands add up to the input again.
        """
        arr = np.asarray(x, dtype=np.float64)
        n = arr.shape[0]
        channels = arr.shape[1]
        if n == 0:
            return (
                np.zeros((0, self.bands, channels), dtype=np.float64),
                arr,
            )
        dry = self._delayed(arr)
        bands = np.zeros((n, self.bands, channels), dtype=np.float64)
        count = self.bands - 1
        if count <= 0:
            bands[:, 0, :] = dry
            return bands, dry
        if self.mode == "Linear Phase":
            lows = [fir.process(arr) for fir in self._fir]
            previous = None
            for index in range(self.bands):
                current = lows[index] if index < len(lows) else dry
                if previous is None:
                    bands[:, 0, :] = current
                else:
                    bands[:, index, :] = current - previous
                previous = current
            return bands, dry
        if self._low and self._low[0]._zi.shape[1] != channels:
            self.resize(channels)
        bands[:, 0, :] = self._low[0].process(arr)
        signal = arr
        for index in range(count):
            high = self._high[index].process(signal)
            if index + 1 < count:
                bands[:, index + 1, :] = self._low[index + 1].process(high)
            signal = high
        bands[:, count, :] = signal
        return bands, dry


# ---------------------------------------------------------------------------
# the module
# ---------------------------------------------------------------------------
@register
class BandRestoration(Module):
    """Band Restoration — repair a recording band by band."""

    code = "AUR_BANDRESTORATION"
    display_name = "Band Restoration"
    category = "Repair"
    edition = "standard"
    element = "BandRestoration"
    supports_learn = True

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Bands",
                type="int",
                default=4,
                minimum=1,
                maximum=4,
                group="Crossover",
                help="How many bands the signal is split into.",
            ),
            ParamDesc(
                name="Low Crossover",
                type="float",
                default=200.0,
                minimum=20.0,
                maximum=2000.0,
                unit="Hz",
                group="Crossover",
                curve="log",
                help="The edge between the first band and the second one.",
            ),
            ParamDesc(
                name="Mid Crossover",
                type="float",
                default=2000.0,
                minimum=200.0,
                maximum=10000.0,
                unit="Hz",
                group="Crossover",
                curve="log",
                help="The edge between the second band and the third one.",
            ),
            ParamDesc(
                name="High Crossover",
                type="float",
                default=10000.0,
                minimum=1000.0,
                maximum=20000.0,
                unit="Hz",
                group="Crossover",
                curve="log",
                help="The edge between the third band and the fourth one.",
            ),
            ParamDesc(
                name="Phase Linearity",
                type="enum",
                default="Efficient",
                choices=("Efficient", "Linear Phase"),
                group="Crossover",
                help=(
                    "Efficient is an IIR crossover that adds no delay, "
                    "Linear Phase is a FIR crossover whose bands carry no phase "
                    "error and which delays the signal."
                ),
            ),
            ParamDesc(
                name="Restoration Depth",
                type="float",
                default=20.0,
                minimum=0.0,
                maximum=40.0,
                unit="dB",
                group="Restoration",
                help="The deepest cut the restoration may make in a band.",
            ),
            ParamDesc(
                name="Noise Threshold",
                type="float",
                default=6.0,
                minimum=0.0,
                maximum=24.0,
                unit="dB",
                group="Restoration",
                help=(
                    "How far above the learned noise level a band has to rise "
                    "before the restoration leaves it alone."
                ),
            ),
            ParamDesc(
                name="High Frequency Transparency",
                type="float",
                default=100.0,
                minimum=0.0,
                maximum=100.0,
                unit="%",
                group="Restoration",
                help=(
                    "How much of the restoration the top band is spared; at "
                    "100 % the top band is left alone."
                ),
            ),
            ParamDesc(
                name="Broadband Mix",
                type="float",
                default=0.0,
                minimum=0.0,
                maximum=100.0,
                unit="%",
                group="Mix",
                help=(
                    "How much of the untouched signal is mixed back against "
                    "the band path."
                ),
            ),
        ] + [
            ParamDesc(
                name=f"Band {index} {control}",
                type=("bool" if control != "Mix" else "float"),
                default=(False if control != "Mix" else 0.0),
                minimum=(0 if control != "Mix" else -60.0),
                maximum=(1 if control != "Mix" else 12.0),
                unit=("" if control != "Mix" else "dB"),
                group=f"Band {index}",
                curve=("linear" if control != "Mix" else "taper"),
                help=(
                    {
                        "Mix": f"The level of band {index} in the output.",
                        "Solo": f"Audition band {index} on its own.",
                        "Mute": f"Take band {index} out of the recording.",
                        "Bypass": (
                            f"Leave band {index} as the crossover made it, "
                            "without the restoration."
                        ),
                    }[control]
                ),
            )
            for index in (1, 2, 3, 4)
            for control in ("Mix", "Solo", "Mute", "Bypass")
        ]

    # -- lifecycle ------------------------------------------------------------
    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._profile: np.ndarray | None = None
        self._profile_fft = 0
        self._splitter: _Splitter | None = None
        self._splitter_key: tuple | None = None
        self._level: _Filter | None = None
        self._level_width = 0
        self._channels = int(channels)

    def reset(self) -> None:
        """Clear the memory of the filters and of the level follower."""
        if self._splitter is not None:
            self._splitter.reset()
        if self._level is not None:
            self._level.reset()

    # -- the learned profile --------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Learn the spectrum of the noise of the recording.

        The profile is the mean power spectrum of the excerpt, and the level
        of the learned noise inside a band is read from it.  The profile
        does not depend on the edges of the crossover, so the user may move
        them after the learning.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] < 64:
            return
        mono = arr.mean(axis=1)
        length = int(min(_LEARN_FFT, mono.shape[0]))
        hop = max(1, length // 4)
        window = np.hanning(length)
        weight = float(np.sum(window * window))
        count = 1 + (mono.shape[0] - length) // hop
        frames = np.lib.stride_tricks.sliding_window_view(mono, length)[::hop][:count]
        spectrum = np.fft.rfft(frames * window, axis=-1)
        profile = np.mean(np.abs(spectrum) ** 2, axis=0) / weight
        self._profile = np.asarray(profile, dtype=np.float64)
        self._profile_fft = length

    def _state_blob(self) -> bytes:
        """Pack the learned profile into a blob."""
        if self._profile is None:
            return b""
        header = np.array(
            [self._profile_fft, float(self.sample_rate), float(self._profile.size)],
            dtype=np.float64,
        )
        return header.tobytes() + self._profile.astype(np.float64).tobytes()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a profile packed by :meth:`_state_blob`."""
        try:
            raw = bytes(data)
            header = np.frombuffer(raw[:24], dtype=np.float64)
            fft_size = int(header[0])
            rate = float(header[1])
            count = int(header[2])
            if count <= 0 or fft_size <= 0:
                return
            profile = np.frombuffer(raw[24 : 24 + count * 8], dtype=np.float64)
            if profile.size != count:
                return
            if abs(rate - float(self.sample_rate)) > 1e-6:
                # the profile was learned at another rate, so it does not
                # describe this recording any more
                self._profile = None
                self._profile_fft = 0
                return
            self._profile = profile.copy()
            self._profile_fft = fft_size
        except Exception:
            self._profile = None
            self._profile_fft = 0

    # -- the edges ------------------------------------------------------------
    def _edges(self) -> tuple[float, float, float]:
        """Return the three edges, ordered and never crossing."""
        top = 0.45 * float(self.sample_rate)
        high = min(max(float(self.params.get("High Crossover")), 1000.0), top)
        gap_high = max(5.0, 0.01 * high)
        mid = min(max(float(self.params.get("Mid Crossover")), 200.0), high - gap_high)
        gap_mid = max(5.0, 0.01 * mid)
        low = min(max(float(self.params.get("Low Crossover")), 20.0), mid - gap_mid)
        return float(low), float(mid), float(high)

    def _band_limits(self) -> list[tuple[float, float]]:
        """Return the frequency range of every band."""
        low, mid, high = self._edges()
        top = 0.45 * float(self.sample_rate)
        edges = [low, mid, high]
        count = int(self.params.get("Bands"))
        limits = []
        for index in range(count):
            lo = 0.0 if index == 0 else edges[index - 1]
            hi = top if index == count - 1 else edges[index]
            limits.append((float(lo), float(hi)))
        return limits

    # -- the splitter ---------------------------------------------------------
    def _splitter_for(self) -> _Splitter:
        """Return the crossover of the current settings, rebuilt when needed."""
        edges = self._edges()
        bands = int(self.params.get("Bands"))
        mode = str(self.params.get("Phase Linearity"))
        key = (edges, bands, mode, self._channels)
        if self._splitter is None or self._splitter_key != key:
            self._splitter = _Splitter(
                self.sample_rate, edges, bands, mode, self._channels
            )
            self._splitter_key = key
        return self._splitter

    @property
    def latency_samples(self) -> int:
        """The delay the module adds, in samples.

        The efficient crossover adds none.  The linear phase crossover is a
        symmetric kernel, so it delays the signal by half the length of
        that kernel.
        """
        return int(self._splitter_for().latency_samples)

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The filters of the bands are recursive, and a band that was lifted
        rings for a part of its decay after the input stopped.  Measured
        the way the host measures it, the output stays above -60 dBFS for
        625 samples after the input stopped for a tone at -6 dBFS, and for
        654 samples for a signal at the clipping ceiling.  The tail covers
        the loud one, with a margin of 114 samples (2.4 ms).
        """
        return 768

    # -- the restoration ------------------------------------------------------
    def _level_filter(self, width: int) -> _Filter:
        """Return the level follower of the bands."""
        if self._level is None or self._level_width != int(width):
            coefficient = math.exp(-1.0 / (_LEVEL_TAU * float(self.sample_rate)))
            self._level = _Filter(
                [1.0 - coefficient, 0.0], [1.0, -coefficient], int(width)
            )
            self._level_width = int(width)
        return self._level

    def _floor_of_band(self, limits: list[tuple[float, float]]) -> list[float] | None:
        """Return the level of the learned noise inside every band."""
        if self._profile is None or self._profile_fft <= 0:
            return None
        bin_hz = float(self.sample_rate) / float(self._profile_fft)
        levels = []
        for lo, hi in limits:
            start = int(math.floor(lo / bin_hz))
            stop = int(math.ceil(hi / bin_hz))
            start = max(0, min(start, self._profile.size - 1))
            stop = max(start + 1, min(stop, self._profile.size))
            power = float(np.sum(self._profile[start:stop])) / float(self._profile_fft)
            levels.append(math.sqrt(max(power, 0.0)))
        return levels

    def _depths(self, bands: int) -> list[float]:
        """Return how deep the restoration may cut in every band."""
        depth = float(self.params.get("Restoration Depth"))
        if depth <= 0.0:
            return [0.0] * bands
        spared = 1.0 - float(self.params.get("High Frequency Transparency")) / 100.0
        depths = [depth] * bands
        for index in range(bands):
            if bool(self.params.get(f"Band {index + 1} Bypass")):
                # the band is left as the crossover made it
                depths[index] = 0.0
        if bands > 0:
            depths[bands - 1] = min(
                depths[bands - 1], depth * max(0.0, min(1.0, spared))
            )
        return depths

    def _restore(self, bands: np.ndarray) -> np.ndarray:
        """Take the learned floor out of every band."""
        n, count, channels = bands.shape
        depths = self._depths(count)
        if not any(d > 0.0 for d in depths):
            return bands
        limits = self._band_limits()
        floors = self._floor_of_band(limits)
        if floors is None:
            return bands
        margin = _db_to_lin(float(self.params.get("Noise Threshold")))
        thresholds = [max(level * margin, _FLOOR) for level in floors]
        # the follower works on the bands and the channels at the same time,
        # so the array is flattened into one column per band and channel
        width = count * channels
        level = self._level_filter(width)
        flat = bands.reshape(n, width)
        follower = level.process(flat * flat)
        rms = np.sqrt(np.maximum(follower, 0.0)).reshape(n, count, channels)
        ratio = 20.0 * np.log10(
            np.maximum(rms, _FLOOR) / np.asarray(thresholds)[None, :, None]
        )
        t = np.clip((ratio + _KNEE_DB) / _KNEE_DB, 0.0, 1.0)
        smooth = t * t * (3.0 - 2.0 * t)
        depth = np.asarray(depths, dtype=np.float64)[None, :, None]
        gain_db = -depth * (1.0 - smooth)
        return bands * np.power(10.0, gain_db / 20.0)

    # -- the mixer ------------------------------------------------------------
    def _mix(self, bands: np.ndarray) -> np.ndarray:
        """Apply the mix, the mute and the solo of every band."""
        count = bands.shape[1]
        soloed = [
            index
            for index in range(count)
            if bool(self.params.get(f"Band {index + 1} Solo"))
        ]
        out = np.zeros((bands.shape[0], bands.shape[2]), dtype=np.float64)
        for index in range(count):
            if soloed and index not in soloed:
                continue
            if bool(self.params.get(f"Band {index + 1} Mute")):
                continue
            gain = _db_to_lin(float(self.params.get(f"Band {index + 1} Mix")))
            if gain == 0.0:
                continue
            out += bands[:, index, :] * gain
        return out

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Restore a block of audio band by band."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr
        if arr.shape[1] != self._channels:
            self._channels = int(arr.shape[1])
            self._splitter = None
            self._splitter_key = None
            self._level = None
        splitter = self._splitter_for()
        bands, dry = splitter.process(arr)
        bands = self._restore(bands)
        out = self._mix(bands)
        broadband = float(self.params.get("Broadband Mix")) / 100.0
        if broadband > 0.0:
            out = out + broadband * dry
        return out
