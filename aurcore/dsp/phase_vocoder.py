"""
aurcore.dsp.phase_vocoder — time-scale and pitch modification.

The phase vocoder is implemented with

* **identity phase locking** (Laroche & Dolson): the phase of the bins
  around a spectral peak follows the peak, which keeps the vertical phase
  coherence and removes most of the "phasiness",
* a **transient detector**: frames that contain a transient restart the phase
  accumulator so that attacks are not smeared over time.

A time-domain overlap-add implementation (WSOLA) is included as a reference
and as a fallback for material where the phase vocoder is not the right
tool.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "PhaseVocoder",
    "PVState",
    "wsola_time_stretch",
    "resample_to_length",
    "time_stretch",
    "pitch_shift",
]

_EPS = 1e-20

#: How much of the largest change in the spectrum a transient has to be
#: before it is treated as an attack.  The leakage of a steady tone moves
#: from bin to bin as the window slides and that movement is not an attack.
_TRANSIENT_PROMINENCE = 0.5


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------
def _local_window(name: str, n: int) -> np.ndarray:
    """Periodic analysis window of ``n`` points."""
    n = int(n)
    name_l = str(name).lower()
    if n <= 1:
        return np.ones(max(n, 1))
    i = np.arange(n, dtype=np.float64)
    if name_l.startswith("hann"):
        return 0.5 - 0.5 * np.cos(2.0 * np.pi * i / n)
    if name_l.startswith("hamm"):
        return 0.54 - 0.46 * np.cos(2.0 * np.pi * i / n)
    if name_l.startswith("black"):
        return 0.42 - 0.5 * np.cos(2.0 * np.pi * i / n) + 0.08 * np.cos(4.0 * np.pi * i / n)
    if name_l.startswith(("rect", "box", "none")):
        return np.ones(n)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * i / n)


def _window_array(name: str, n: int) -> np.ndarray:
    """Return the analysis window, preferring the shared ``windows`` module."""
    try:  # pragma: no cover - depends on the neighbouring module
        from .windows import window as _shared_window  # type: ignore

        arr = np.asarray(_shared_window(name, n), dtype=np.float64).ravel()
        if arr.size == n:
            return arr
    except Exception:
        pass
    return _local_window(name, n)


# ---------------------------------------------------------------------------
# local STFT
# ---------------------------------------------------------------------------
class _LocalSTFT:
    """Minimal, dependency free STFT with centred padding and COLA resynthesis."""

    def __init__(self, fft_size: int, hop: int, window: str = "hann") -> None:
        self.fft_size = int(fft_size)
        self.hop = int(hop)
        self.window = _window_array(window, self.fft_size)
        self.pad = self.fft_size // 2

    # -- analysis ------------------------------------------------------------
    def analyze(self, x) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(magnitude, phase)`` with shape ``(frames, bins)``."""
        sig = np.asarray(x, dtype=np.float64).ravel()
        if sig.size == 0:
            empty = np.zeros((0, self.fft_size // 2 + 1), dtype=np.float64)
            return empty, empty.copy()
        padded = np.concatenate([np.zeros(self.pad), sig, np.zeros(self.fft_size)])
        n_frames = 1 + (padded.size - self.fft_size) // self.hop
        idx = np.arange(self.fft_size)[None, :] + self.hop * np.arange(n_frames)[:, None]
        frames = padded[idx] * self.window[None, :]
        spec = np.fft.rfft(frames, axis=-1)
        return np.abs(spec), np.angle(spec)

    # -- synthesis ------------------------------------------------------------
    def synthesize(self, mag, phase, length: int | None = None) -> np.ndarray:
        """Resynthesise a magnitude/phase spectrogram by overlap-add."""
        mag = np.asarray(mag)
        phase = np.asarray(phase)
        if mag.ndim != 2:
            raise ValueError("spectrogram must be 2-D (frames, bins)")
        n_frames = mag.shape[0]
        if n_frames == 0:
            return np.zeros(0)
        spec = mag * np.exp(1j * phase)
        frames = np.fft.irfft(spec, n=self.fft_size, axis=-1) * self.window[None, :]
        out_len = (n_frames - 1) * self.hop + self.fft_size
        out = np.zeros(out_len)
        wsum = np.zeros(out_len)
        w2 = self.window ** 2
        for i in range(n_frames):
            s = i * self.hop
            out[s:s + self.fft_size] += frames[i]
            wsum[s:s + self.fft_size] += w2
        out = out / np.maximum(wsum, 1e-9)
        start = self.pad
        stop = start + (int(length) if length is not None else out.size - 2 * self.pad)
        stop = int(np.clip(stop, 0, out.size))
        return out[start:stop]


class _SharedSTFT:
    """Adapter around the shared ``aurcore.dsp.stft.STFT`` implementation.

    The shared engine works with ``(bins, frames)`` spectrograms; the phase
    vocoder prefers ``(frames, bins)``, so the adapter transposes.
    """

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def analyze(self, x) -> tuple[np.ndarray, np.ndarray]:
        mag, phase = self._obj.analyze_block(x, center=True)
        return np.asarray(mag).T, np.asarray(phase).T

    def synthesize(self, mag, phase, length: int | None = None) -> np.ndarray:
        return np.asarray(
            self._obj.synthesize_block(
                np.asarray(mag).T, np.asarray(phase).T, norm="exact", length=length, center=True
            )
        ).ravel()


def _make_stft(fft_size: int, hop: int, window: str = "hann"):
    """Return an STFT engine: the shared one when it behaves, else a local one."""
    try:  # pragma: no cover - depends on the neighbouring module
        from .stft import STFT as _SharedSTFTClass  # type: ignore

        obj = _SharedSTFTClass(int(fft_size), int(hop), window)
        adapter = _SharedSTFT(obj)
        # verify the round trip before trusting the shared engine
        n = 4096
        probe = np.sin(2.0 * np.pi * 440.0 * np.arange(n) / 48000.0)
        mag, phase = adapter.analyze(probe)
        back = adapter.synthesize(mag, phase, n)
        if back.size >= n and float(np.max(np.abs(back[:n] - probe))) < 1e-6:
            return adapter
    except Exception:
        pass
    return _LocalSTFT(int(fft_size), int(hop), window)


# ---------------------------------------------------------------------------
# resampling helper (used by the optional resample based pitch shifter)
# ---------------------------------------------------------------------------
def _kaiser_window(length: int, beta: float = 8.6) -> np.ndarray:
    n = int(length)
    if n <= 1:
        return np.ones(max(n, 1))
    m = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    r = 2.0 * m / (n - 1)
    arg = np.clip(1.0 - r * r, 0.0, None)
    return np.i0(beta * np.sqrt(arg)) / np.i0(beta)


def _resample_numpy(x: np.ndarray, out_len: int, taps: int = 32) -> np.ndarray:
    """Band limited resampling using only numpy.

    The output samples are read from the input with a windowed sinc
    interpolator, so the pitch follows the length ratio exactly: a signal of
    ``n`` samples that is resampled to ``m`` samples plays ``n / m`` times
    faster and every partial moves up by that factor.
    """
    sig = np.asarray(x, dtype=np.float64).ravel()
    in_len = sig.size
    out_len = int(out_len)
    if in_len == 0 or out_len <= 0:
        return np.zeros(max(out_len, 0))
    if in_len == out_len:
        return sig.copy()
    ratio = out_len / in_len
    # the interpolation runs at the lower of the two Nyquist limits, so that
    # nothing folds back when the signal is shortened
    cutoff = 0.5 * min(1.0, ratio)
    half = max(1, int(taps) // 2)
    offsets = np.arange(-half, half, dtype=np.float64)
    window = _kaiser_window(offsets.size)
    # the place every output sample is read from
    centres = np.arange(out_len, dtype=np.float64) / ratio
    base = np.floor(centres).astype(np.intp)
    indices = (base[:, None] + offsets[None, :]).astype(np.intp)
    distance = indices - centres[:, None]
    kernel = 2.0 * cutoff * np.sinc(2.0 * cutoff * distance) * window[None, :]
    inside = (indices >= 0) & (indices < in_len)
    indices = np.clip(indices, 0, in_len - 1)
    return np.sum(sig[indices] * kernel * inside, axis=1)


def resample_to_length(x, out_len: int) -> np.ndarray:
    """Resample a mono signal to a new length (pitch follows the length ratio).

    ``scipy.signal.resample_poly`` is used when available, otherwise a
    numpy band-limited implementation takes over.
    """
    sig = np.asarray(x, dtype=np.float64).ravel()
    out_len = int(out_len)
    if sig.size == 0 or out_len <= 0:
        return np.zeros(max(out_len, 0))
    if sig.size == out_len:
        return sig.copy()
    try:  # pragma: no cover - depends on the environment
        from scipy.signal import resample_poly  # type: ignore

        g = math.gcd(out_len, sig.size)
        up = out_len // g
        down = sig.size // g
        y = np.asarray(resample_poly(sig, up, down))
        if y.size >= out_len:
            return y[:out_len]
        return np.pad(y, (0, out_len - y.size))
    except Exception:
        return _resample_numpy(sig, out_len)


# ---------------------------------------------------------------------------
# phase vocoder
# ---------------------------------------------------------------------------
@dataclass
class PVState:
    """Analysis result of the phase vocoder.

    Attributes
    ----------
    mag:
        Magnitudes, shape ``(frames, bins)``.
    phase:
        Phases in radians, shape ``(frames, bins)``.
    freq:
        Bin centre frequencies in Hz.
    times:
        Time position of every frame in seconds.
    length:
        Length of the analysed signal in samples.
    """

    mag: np.ndarray
    phase: np.ndarray
    freq: np.ndarray
    times: np.ndarray
    length: int
    fft_size: int
    hop: int
    sr: int
    window: np.ndarray

    @property
    def frames(self) -> int:
        return int(self.mag.shape[0])

    @property
    def bins(self) -> int:
        return int(self.mag.shape[1])


class PhaseVocoder:
    """Phase vocoder for time-scale and pitch modification.

    Parameters
    ----------
    fft_size:
        FFT size of the analysis.
    hop:
        Analysis hop in samples.  ``fft_size / 4`` gives perfect overlap-add
        with a Hann window.
    sr:
        Sample rate in Hz.
    window:
        Analysis window name (``'hann'`` by default).
    phase_lock:
        Enable identity phase locking.
    phase_coherence:
        Blend between the free and the locked phase, ``0..1``.
    transient_sensitivity:
        ``0`` disables the transient detector, larger values react earlier.
    """

    def __init__(
        self,
        fft_size: int = 2048,
        hop: int | None = None,
        sr: int = 48000,
        window: str = "hann",
        phase_lock: bool = True,
        phase_coherence: float = 1.0,
        transient_sensitivity: float = 1.0,
    ) -> None:
        self.fft_size = int(fft_size)
        self.hop = int(hop) if hop else max(1, self.fft_size // 4)
        self.sr = int(sr)
        self.window_name = window
        self.phase_lock = bool(phase_lock)
        self.phase_coherence = float(np.clip(phase_coherence, 0.0, 1.0))
        self.transient_sensitivity = float(transient_sensitivity)
        self._stft = _make_stft(self.fft_size, self.hop, window)
        self._window = _window_array(window, self.fft_size)

    # -- analysis ------------------------------------------------------------
    def analyze(self, x) -> PVState:
        """Analyse a mono signal into magnitudes and phases."""
        sig = np.asarray(x, dtype=np.float64).ravel()
        mag, phase = self._stft.analyze(sig)
        mag = np.asarray(mag)
        phase = np.asarray(phase)
        if mag.size == 0:
            mag = np.zeros((0, self.fft_size // 2 + 1), dtype=np.float64)
            phase = np.zeros((0, self.fft_size // 2 + 1), dtype=np.float64)
        freq = np.fft.rfftfreq(self.fft_size, 1.0 / float(self.sr))
        times = np.arange(mag.shape[0], dtype=np.float64) * self.hop / float(self.sr)
        return PVState(
            mag=mag,
            phase=phase,
            freq=freq,
            times=times,
            length=int(sig.size),
            fft_size=self.fft_size,
            hop=self.hop,
            sr=self.sr,
            window=self._window,
        )

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _principal_angle(a: np.ndarray) -> np.ndarray:
        return np.mod(a + np.pi, 2.0 * np.pi) - np.pi

    def _phase_advance(self, phase: np.ndarray) -> np.ndarray:
        """Wrapped phase advance between consecutive frames.

        ``advance[i]`` is the advance from frame ``i`` to frame ``i + 1``;
        the last frame repeats the previous advance.
        """
        if phase.shape[0] < 2:
            return np.zeros_like(phase)
        d = self._principal_angle(np.diff(phase, axis=0))
        return np.concatenate([d, d[-1:]], axis=0)

    def _interval_advance(self, advance: np.ndarray) -> np.ndarray:
        """The true phase advance of every analysis interval.

        The advance the analysis measures is wrapped: it is the angle
        between two phases and it lies between -pi and pi.  The advance of
        the interval is rebuilt from the expected advance of the bin (the
        advance a partial in the middle of the bin would have) and the
        measured deviation, so that it is the advance the partial really
        had and not the advance modulo a whole period.
        """
        expected = 2.0 * np.pi * np.arange(advance.shape[1]) * (
            float(self.hop) / float(self.fft_size)
        )
        return expected + self._principal_angle(advance - expected)

    def _phase_correction(
        self,
        interval: np.ndarray,
        index: int,
        fraction: float,
        src: np.ndarray | None = None,
    ) -> np.ndarray:
        """The phase the synthesis frame advances by.

        The phase of a synthesis frame is the phase of the analysis frame
        below its position, advanced by the fraction of the position.  The
        advance is the true advance of the analysis interval: it is scaled
        by the fraction of the position, so a whole period that the
        wrapping of the measured advance lost would come back scaled.
        """
        if src is None:
            return fraction * interval[index]
        return fraction * np.interp(src, np.arange(interval.shape[1]), interval[index])

    @staticmethod
    def _flux_thresholds(flux: np.ndarray) -> tuple[float, float]:
        """The median and the 90th percentile of the flux, in one pass.

        ``numpy.median`` and ``numpy.percentile`` each copy the array and
        partition it; one partition with four pivots gives both values and
        halves the work.  The two values are the ones the two functions
        return, down to the last bit.
        """
        count = flux.size
        if count == 0:
            return 0.0, 0.0
        median_low = (count - 1) // 2
        median_high = count // 2
        position = 0.9 * (count - 1)
        p90_low = int(math.floor(position))
        p90_high = int(math.ceil(position))
        pivots = sorted({median_low, median_high, p90_low, p90_high})
        part = np.partition(flux, pivots)
        if median_low == median_high:
            median = float(part[median_low])
        else:
            median = 0.5 * (float(part[median_low]) + float(part[median_high]))
        if p90_low == p90_high:
            p90 = float(part[p90_low])
        else:
            low = float(part[p90_low])
            high = float(part[p90_high])
            gamma = position - p90_low
            if gamma >= 0.5:
                p90 = high - (high - low) * (1.0 - gamma)
            else:
                p90 = low + (high - low) * gamma
        return median, p90

    def _detect_transients(self, mag: np.ndarray) -> np.ndarray:
        """Flag frames that contain a transient (spectral flux based).

        The flux of frame ``i`` is the growth of the spectrum between the
        frames ``i - 1`` and ``i``, so the flag of frame ``i`` belongs to
        ``flux[i - 1]``.  A transient is a local maximum of the flux, which
        means that the flux has to fall again after the flag.

        A transient also has to be *prominent*.  The leakage of a steady tone
        moves from bin to bin as the window slides, and that movement reaches
        a third of the spectrum's energy in a single frame, so a flux that is
        only a small part of the largest change of the signal is a movement
        and not an attack.
        """
        n = mag.shape[0]
        if n < 3 or self.transient_sensitivity <= 0.0:
            return np.zeros(n, dtype=bool)
        flux = np.sum(np.maximum(np.diff(mag, axis=0), 0.0), axis=1)
        energy = np.sum(mag[1:], axis=1) + _EPS
        flux = flux / energy
        count = flux.size
        if count == 0:
            return np.zeros(n, dtype=bool)
        median, p90 = self._flux_thresholds(flux)
        spread = max(p90 - median, 1e-6)
        threshold = median + self.transient_sensitivity * spread
        strongest = float(np.max(flux))
        prominent = flux >= _TRANSIENT_PROMINENCE * strongest
        flags = np.zeros(n, dtype=bool)
        flags[1:] = (flux > threshold) & prominent
        if count >= 2:
            # a transient must be a local maximum of the flux
            falling = flux[0:count - 1] >= flux[1:count]
            rising = np.ones(count - 1, dtype=bool)
            if count >= 3:
                rising[1:] = flux[0:count - 2] <= flux[1:count - 1]
            flags[1:n - 1] &= falling & rising
        return flags

    @staticmethod
    def _find_peaks(mag: np.ndarray, relative_floor: float = 0.02):
        """Return ``(significant_peaks, all_peaks)`` of one magnitude frame.

        The significant peaks are the local maxima that stand out from the
        noise floor; all local maxima are needed to delimit the region of
        influence of a peak.  Both are returned as index arrays.
        """
        mag = np.asarray(mag)
        empty = np.zeros(0, dtype=np.intp)
        if mag.size < 3:
            return empty, empty
        left = mag[:-2]
        mid = mag[1:-1]
        right = mag[2:]
        all_peaks = np.nonzero((mid > left) & (mid >= right))[0] + 1
        peak_max = float(np.max(mag))
        if peak_max <= 0.0:
            return empty, all_peaks
        floor = peak_max * relative_floor
        significant = all_peaks[mag[all_peaks] > floor]
        return significant, all_peaks

    def _lock_phase(self, acc_phase: np.ndarray, in_phase: np.ndarray,
                    mag: np.ndarray) -> np.ndarray:
        """Identity phase locking around the spectral peaks.

        The phase of the bins inside the region of influence of a peak is
        anchored to the accumulated phase of the peak; the relative phase
        inside the region is taken from the analysis phase, which keeps the
        vertical phase coherence of the original signal.

        The regions of the peaks tile the spectrum without overlapping, so
        the whole operation is one vectorised pass instead of a Python loop
        over the peaks.  The result is the same as the loop produced.
        """
        significant, all_peaks = self._find_peaks(mag)
        n_bins = int(mag.size)
        if significant.size == 0 or all_peaks.size == 0:
            return acc_phase

        # the region of a peak reaches halfway to its neighbours
        previous = np.empty_like(all_peaks)
        following = np.empty_like(all_peaks)
        previous[0] = 0
        previous[1:] = all_peaks[:-1]
        following[:-1] = all_peaks[1:]
        following[-1] = n_bins - 1
        starts = (previous + all_peaks) // 2 + 1
        stops = np.minimum((all_peaks + following) // 2 + 1, n_bins)
        counts = np.maximum(stops - starts, 0)
        total = int(counts.sum())
        if total <= 0:
            return acc_phase

        # every bin belongs to the region of the peak that owns it
        owner = np.full(n_bins, -1, dtype=np.intp)
        owner[starts[0]:starts[0] + total] = np.repeat(
            np.arange(all_peaks.size, dtype=np.intp), counts
        )
        index = np.where(owner >= 0, owner, 0)
        peak_bin = all_peaks[index]
        locked = np.zeros(all_peaks.size, dtype=bool)
        locked[np.searchsorted(all_peaks, significant)] = True
        use = (owner >= 0) & locked[index]
        if not np.any(use):
            return acc_phase
        delta = self._principal_angle(in_phase - in_phase[peak_bin])
        out = acc_phase.copy()
        out[use] = acc_phase[peak_bin[use]] + delta[use]
        return out

    # -- synthesis -----------------------------------------------------------
    @staticmethod
    def _interp_complex(spec: np.ndarray, positions: np.ndarray) -> np.ndarray:
        """Linear interpolation of a complex spectrum at fractional bins."""
        n = spec.shape[-1]
        pos = np.clip(positions, 0.0, n - 1.0)
        lo = np.floor(pos).astype(np.intp)
        hi = np.minimum(lo + 1, n - 1)
        frac = pos - lo
        return (1.0 - frac) * spec[lo] + frac * spec[hi]

    def synthesize(
        self,
        state: PVState,
        stretch: float = 1.0,
        pitch: float = 1.0,
        phase_lock: bool | None = None,
        transient_reset: bool = True,
    ) -> np.ndarray:
        """Synthesise a stretched and/or pitched signal from an analysis.

        The synthesis reads the magnitude of the analysis frame that belongs
        to the output frame (the time scale) and shifts it in the frequency
        domain (the pitch factor).  A pitch factor of ``beta`` moves the
        content of bin ``k`` to bin ``k * beta``, which shifts every partial
        by the same ratio and therefore keeps the timbre intact.

        Parameters
        ----------
        state:
            Result of :meth:`analyze`.
        stretch:
            Time-scale factor, ``2.0`` makes the signal twice as long.
        pitch:
            Pitch factor, ``1.5`` shifts everything up by a perfect fifth.
        phase_lock:
            Override the instance setting.
        transient_reset:
            Restart the phase accumulator on transients.

        Returns
        -------
        numpy.ndarray
            The processed signal.
        """
        stretch = float(stretch)
        beta = float(pitch)
        if stretch <= 0.0:
            raise ValueError("stretch factor must be positive")
        if beta <= 0.0:
            raise ValueError("pitch factor must be positive")
        lock = self.phase_lock if phase_lock is None else bool(phase_lock)

        mag = np.asarray(state.mag)
        phase = np.asarray(state.phase)
        n_frames, n_bins = mag.shape
        if n_frames == 0:
            return np.zeros(0)

        advance = self._phase_advance(phase)
        transients = self._detect_transients(mag)
        # The frames around a transient carry the attack: the phase
        # relations inside them are the ones of the analysis frame the
        # transient is in, and they are not the ones of a frame that is read
        # a fraction of a hop later.
        near_transient = transients.copy()
        for shift in (1, 2):
            near_transient[shift:] |= transients[:-shift]
            near_transient[:-shift] |= transients[shift:]

        # frequency mapping of the pitch factor
        bin_index = np.arange(n_bins, dtype=np.float64)
        src = bin_index / beta
        # The analysis has no content above its last bin.  When the pitch
        # factor is below one, the top of the output maps there, so that part
        # of the output stays empty instead of copying the Nyquist bin.
        inside = src <= (n_bins - 1.0)
        src = np.clip(src, 0.0, n_bins - 1.0)

        # The advance of a partial that sits in bin k, in radians per analysis
        # hop: the advance a stationary partial in that bin would have.
        expected = 2.0 * np.pi * bin_index * (float(self.hop) / float(self.fft_size))
        # The advance of every analysis interval, rebuilt from the expected
        # advance of the bin and the measured deviation.  The advance the
        # analysis measures is wrapped, and the wrapping is what makes the
        # interpolation of the phase circular: the phase travels the
        # shortest way round.  The advance of the interval is the true one,
        # because the correction is scaled by the fraction of the position
        # and a whole period that was lost here would come back scaled.
        # It is only needed when a synthesis frame lands between two
        # analysis frames, so it is built when the first one does.
        interval: np.ndarray | None = None

        # the phase accumulator starts on the analysis phase of the shifted
        # source bin so that the vertical phase structure is preserved
        source0 = mag[0] * np.exp(1j * phase[0])
        acc = np.angle(self._interp_complex(source0, src))
        acc[~inside] = 0.0

        n_out = max(1, int(round(n_frames * stretch)))
        out_mag = np.zeros((n_out, n_bins), dtype=np.float64)
        out_phase = np.zeros((n_out, n_bins), dtype=np.float64)

        for n in range(n_out):
            pos = n / stretch
            i0 = int(math.floor(pos))
            i0 = int(np.clip(i0, 0, n_frames - 1))
            i1 = int(min(i0 + 1, n_frames - 1))
            frac = pos - i0

            mag_frame = (1.0 - frac) * mag[i0] + frac * mag[i1]
            adv_frame = (1.0 - frac) * advance[i0] + frac * advance[i1]

            # shift the content in the frequency domain
            source = mag_frame * np.exp(1j * phase[i0])
            shifted = self._interp_complex(source, src)
            mag_out = np.abs(shifted)
            psi = np.angle(shifted)
            mag_out[~inside] = 0.0
            psi[~inside] = 0.0

            # The advance of the output bin is the expected advance of that bin
            # plus the deviation of the source partial, scaled by the pitch
            # factor.  The deviation is the *wrapped* difference between the
            # measured and the expected advance: scaling a wrapped advance
            # would scale the wrapping with it and move the partial to the
            # wrong frequency.
            deviation = self._principal_angle(adv_frame - expected)
            deviation_src = np.interp(src, bin_index, deviation)
            adv_out = expected + beta * deviation_src
            adv_out[~inside] = 0.0

            # The phase of the synthesis frame is the phase of the analysis
            # frame below the position, advanced by the fraction of the
            # position.  Without that advance the phase jumps every time the
            # position crosses an integer, and the render jumps with it.
            #
            # The correction belongs to the phase relations *inside* the
            # synthesis frame, which is what the identity phase locking
            # reads.  The phase of the transient reset is left alone: it
            # re-places the attack, and the attack belongs to the analysis
            # frame it is in, not to a frame that is read a fraction of a hop
            # later.
            if frac > 0.0:
                if interval is None:
                    interval = self._interval_advance(advance)
                if beta == 1.0:
                    correction = self._phase_correction(interval, i0, frac)
                else:
                    correction = self._phase_correction(interval, i0, frac, src)
                psi_lock = psi + correction
                psi_lock[~inside] = 0.0
                if near_transient[i0] or near_transient[i1]:
                    # A frame that reads a transient is re-anchored on the
                    # analysis frame the transient is in, and the locking of
                    # that frame has to read the same phase relations: the
                    # attack is the content of that frame, and the phase
                    # relations inside it are the ones of that frame.
                    psi_lock = psi
            else:
                # the position is on an analysis frame: there is nothing to
                # interpolate
                psi_lock = psi

            phase_now = acc
            if transient_reset and transients[i0]:
                phase_now = psi.copy()
                # the accumulator restarts from the transient, otherwise the
                # next frame falls back to the old accumulator and the attack
                # is smeared over the analysis window again
                acc = psi.copy()
            if lock:
                locked = self._lock_phase(phase_now, psi_lock, mag_out)
                if self.phase_coherence >= 1.0:
                    phase_now = locked
                else:
                    c = self.phase_coherence
                    z = (1.0 - c) * np.exp(1j * phase_now) + c * np.exp(1j * locked)
                    phase_now = np.angle(z)

            out_mag[n] = mag_out
            out_phase[n] = phase_now
            acc = acc + adv_out

        out_len = (
            int(round(state.length * stretch))
             if abs(beta - 1.0) < 1e-12 else int(state.length)
        )
        out = self._stft.synthesize(out_mag, out_phase, out_len)
        out = np.asarray(out).ravel()
        if out.size < out_len:
            out = np.pad(out, (0, out_len - out.size))
        return out[:out_len]

    # -- convenience ---------------------------------------------------------
    def time_stretch(self, x, factor: float) -> np.ndarray:
        """Time-scale a mono or multi-channel signal by ``factor``."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            state = self.analyze(buf)
            return self.synthesize(state, stretch=float(factor))
        out = np.empty((int(round(buf.shape[0] * float(factor))), buf.shape[1]), dtype=np.float64)
        for ch in range(buf.shape[1]):
            state = self.analyze(buf[:, ch])
            out[:, ch] = self.synthesize(state, stretch=float(factor))
        return out

    def pitch_shift(self, x, semitones: float, method: str = "resample") -> np.ndarray:
        """Shift the pitch of a mono or multi-channel signal.

        Parameters
        ----------
        x:
            Audio, shape ``(n,)`` or ``(n, channels)``.
        semitones:
            Pitch offset in semitones (positive shifts up).
        method:
            ``'resample'`` (default) resamples the signal and stretches it
            back to its original length, which shifts every partial by the
            exact ratio.  ``'spectral'`` performs the classic phase-vocoder
            bin shift: the partials are moved to the bin grid.  The
            measurement in ``qa/vocoder/REPORT.md`` puts both paths within
            0.06 cents for every factor between a fifth and an octave; the
            bin shift breaks down for factors of three and more, because the
            interpolated magnitude then ripples and the phase locking falls
            apart.  It is kept for comparison with the time-domain
            alternative.
        """
        factor = 2.0 ** (float(semitones) / 12.0)
        buf = np.asarray(x, dtype=np.float64)
        single = buf.ndim == 1
        work = buf[:, None] if single else buf
        out = np.empty((work.shape[0], work.shape[1]), dtype=np.float64)
        for ch in range(work.shape[1]):
            column = work[:, ch]
            if method == "spectral":
                state = self.analyze(column)
                shifted = self.synthesize(state, stretch=1.0, pitch=factor)
            else:
                shortened = resample_to_length(column, int(round(column.size / factor)))
                shifted = self.time_stretch(shortened, factor)
                if shifted.size < column.size:
                    shifted = np.pad(shifted, (0, column.size - shifted.size))
                shifted = shifted[: column.size]
            out[:, ch] = shifted
        return out[:, 0] if single else out


# ---------------------------------------------------------------------------
# WSOLA
# ---------------------------------------------------------------------------
def wsola_time_stretch(
    x,
    factor: float,
    sr: int = 48000,
    frame_ms: float = 40.0,
    tolerance_ms: float = 8.0,
) -> np.ndarray:
    """Time-scale a signal with waveform similarity overlap-add.

    Parameters
    ----------
    x:
        Audio, shape ``(n,)`` or ``(n, channels)``.
    factor:
        Time-scale factor, ``2.0`` makes the signal twice as long.
    sr:
        Sample rate in Hz.
    frame_ms:
        Length of the overlap-add frame in milliseconds.
    tolerance_ms:
        Search radius around the nominal analysis position in milliseconds.

    Returns
    -------
    numpy.ndarray
        The stretched signal, same channel layout as the input.
    """
    factor = float(factor)
    if factor <= 0.0:
        raise ValueError("stretch factor must be positive")
    buf = np.asarray(x, dtype=np.float64)
    single = buf.ndim == 1
    work = buf[:, None] if single else buf
    out = np.empty((int(round(work.shape[0] * factor)), work.shape[1]), dtype=np.float64)
    for ch in range(work.shape[1]):
        out[:, ch] = _wsola_mono(work[:, ch], factor, sr, frame_ms, tolerance_ms)
    return out[:, 0] if single else out


def _wsola_mono(x: np.ndarray, factor: float, sr: int, frame_ms: float,
                tolerance_ms: float) -> np.ndarray:
    """WSOLA of one channel.

    The output frames are built from input segments whose start is searched
    around a nominal position.  The similarity is measured against the tail
    of the segment that was used for the previous output frame, so the
    selected segments continue each other and the overlap-add stays smooth.
    Because the similarity is taken against the previously *rendered* frame,
    the phase (and therefore the pitch) of the result stays correct.
    """
    sig = np.asarray(x, dtype=np.float64).ravel()
    n = sig.size
    if n == 0:
        return np.zeros(0)
    length = max(16, int(round(frame_ms * sr / 1000.0)))
    if length % 2 == 0:
        length += 1
    hop_s = length // 2  # synthesis hop
    hop_a = max(1, int(round(hop_s / factor)))  # nominal analysis advance
    tol = max(1, int(round(tolerance_ms * sr / 1000.0)))
    step = max(1, tol // 32)
    win = _local_window("hann", length)
    pad = length // 2

    # centred padding: the first frame is centred on the first sample
    padded = np.concatenate([np.zeros(pad), sig, np.zeros(length)])

    out_len = int(round(n * factor))
    n_out = max(1, out_len // hop_s + 1)
    out = np.zeros(pad + n_out * hop_s + length, dtype=np.float64)
    norm = np.zeros_like(out)

    prev_seg = None
    for m in range(n_out):
        nominal = int(round(m * hop_a))
        if m == 0 or prev_seg is None:
            pos = int(np.clip(nominal, 0, max(0, n - length)))
        else:
            ref = prev_seg[hop_s:]
            ref_len = ref.size
            ref_norm = float(np.linalg.norm(ref)) + _EPS
            best_pos = int(np.clip(nominal, 0, max(0, n - length)))
            best_score = -np.inf
            for delta in range(-tol, tol + 1, step):
                cand = nominal + delta
                if cand < 0 or cand + length > n:
                    continue
                seg = padded[cand + pad:cand + pad + ref_len] * win[hop_s:hop_s + ref_len]
                score = float(np.dot(seg, ref)) / (float(np.linalg.norm(seg)) + ref_norm)
                # a tiny bias towards the nominal position breaks the ties of
                # periodic material, where many offsets correlate equally well
                score -= 1e-3 * abs(delta) / tol
                if score > best_score:
                    best_score = score
                    best_pos = cand
            for delta in range(-step, step + 1):
                cand = best_pos + delta
                if cand < 0 or cand + length > n:
                    continue
                seg = padded[cand + pad:cand + pad + ref_len] * win[hop_s:hop_s + ref_len]
                score = float(np.dot(seg, ref)) / (float(np.linalg.norm(seg)) + ref_norm)
                score -= 1e-3 * abs(cand - nominal) / tol
                if score > best_score:
                    best_score = score
                    best_pos = cand
            pos = best_pos
        seg = padded[pos + pad:pos + pad + length]
        if seg.size < length:
            seg = np.pad(seg, (0, length - seg.size))
        # the window is applied twice (analysis + synthesis) so that the
        # overlap-add normalisation by sum(w^2) restores the original level
        seg = seg * win * win
        base = pad + m * hop_s
        out[base:base + length] += seg
        norm[base:base + length] += win * win
        prev_seg = seg

    out = out / np.maximum(norm, 1e-9)
    return out[pad:pad + out_len]


# ---------------------------------------------------------------------------
# module level convenience wrappers
# ---------------------------------------------------------------------------
def time_stretch(x, factor: float, fft_size: int = 2048, sr: int = 48000, **kwargs) -> np.ndarray:
    """Time-scale a signal with a phase vocoder (mono or multi-channel)."""
    hop = max(1, fft_size // 4)
    pv = PhaseVocoder(fft_size, hop, sr, **kwargs)
    return pv.time_stretch(x, factor)


def pitch_shift(x, semitones: float, fft_size: int = 2048, sr: int = 48000, **kwargs) -> np.ndarray:
    """Shift the pitch of a signal with a phase vocoder (mono or multi-channel)."""
    hop = max(1, fft_size // 4)
    pv = PhaseVocoder(fft_size, hop, sr, **kwargs)
    return pv.pitch_shift(x, semitones)
