"""
aurcore.modules.wow_and_flutter — correction of slow pitch modulation.

The module tracks the pitch of the programme with a narrow band around the
centre frequency, takes the deviation from that centre as the modulation
signal, keeps the part of it that lives in the wow and the flutter band and
removes it by warping the time axis with a band limited sinc interpolator.

The tracker is a **quadrature discriminator**: the programme is mixed down
to the centre frequency, the two quadratures are low-passed, and the speed of
the phase of the complex baseband is the instantaneous pitch.  The deviation
from the centre is smoothed over ten milliseconds.

The selection of the wow and the flutter band is a **quadrature
demodulator** at the rate the parameter names: the deviation is mixed down
to that rate, the baseband is low-passed, and the band is rebuilt from the
two quadratures.  A demodulator has no group delay at the rate it is tuned
to, so the correction stays in phase with the modulation it removes, and the
module needs to look ahead only as far as the pitch tracker itself.

Both stages are **streaming**: the filters, the smoother, the delay lines
and the ring of the interpolator carry their state from one block to the
next.  That state is what makes the answer the same whether the host hands
the module the whole file or 64 samples at a time.

The module delays its output by :attr:`WowAndFlutter.latency_samples`
samples, because the correction of a sample can only be computed once the
samples that follow it have been seen.
"""
from __future__ import annotations

import math

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["WowAndFlutter"]

#: Half width of the band the pitch tracker measures, in hertz.  The
#: block-local version used a band-pass filter of quality 6, whose width is
#: the centre frequency divided by six.
_TRACK_BAND_HZ = 36.7

#: Quality of the low-pass filters of the tracker and of the bands.
_FILTER_Q = 0.707

#: Length of the moving average that takes the noise out of the pitch
#: estimate, in seconds.
_SMOOTH_SECONDS = 0.010

#: Half width of the wow band, in hertz.
_WOW_HALF_WIDTH = 1.5

#: Half width of the flutter band, in hertz.
_FLUTTER_HALF_WIDTH = 8.0

#: The delay every band is aligned to, in samples.  It is the delay the
#: pitch tracker and the smoother add before the band is selected; the bands
#: that are faster than that are delayed to match it.
_LATENCY = 1024

#: The largest deviation the tracker reports, as a fraction of the centre
#: frequency.
_MAX_DEVIATION = 0.05

#: Half width of the sinc kernel of the interpolator, in samples.
_INTERP_HALF = 16

#: How many samples of the input the interpolator keeps, so that it can read
#: the material that lies before the block.
_HISTORY = 8192

#: How many samples are written into the ring and resampled at a time.  The
#: ring has to hold the material that lies before the block, so a piece may
#: not be longer than the ring.
_PIECE = 256

#: How many samples at the start of a stream carry the transient of the
#: filters.  The estimate of the pitch is not worth anything there.
_WARMUP = 2400

#: The time constant of the slow mean of the correction, in seconds.  The
#: mean is subtracted from the correction, so that the duration of the signal
#: does not change.
_DC_TAU = 1.0


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------
def _lowpass(freq: float, q: float, sample_rate: int):
    """Design a second order low-pass biquad."""
    fs = float(sample_rate)
    w0 = 2.0 * math.pi * min(max(freq, 1e-4), 0.45 * fs) / fs
    alpha = math.sin(w0) / (2.0 * max(q, 0.05))
    cos_w0 = math.cos(w0)
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _response(coeffs, freq: float, sample_rate: int) -> complex:
    """The complex response of a biquad at a frequency."""
    b0, b1, b2, a1, a2 = coeffs
    w = 2.0 * math.pi * float(freq) / float(sample_rate)
    z = complex(math.cos(w), -math.sin(w))
    num = b0 + b1 * z + b2 * z * z
    den = 1.0 + a1 * z + a2 * z * z
    return complex(num / den)


def _phase_delay(coeffs, freq: float, sample_rate: int) -> float:
    """The delay a narrow band component of ``freq`` sees, in samples.

    A low-pass filter delays the low end of its band by its group delay and
    the top of its band by less; the delay of a narrow band component is the
    phase the filter gives it, divided by its frequency.
    """
    w = 2.0 * math.pi * float(freq) / float(sample_rate)
    if w <= 0.0:
        return 0.0
    response = _response(coeffs, freq, sample_rate)
    return -math.atan2(response.imag, response.real) / w


def _apply_biquad(coeffs, x: np.ndarray, state: np.ndarray) -> np.ndarray:
    """Apply a biquad to a block, keeping the delay state.

    The block may be mono (1-D) or multi-channel (2-D, one column per
    channel); the shape of the input is preserved on the way out.
    """
    b0, b1, b2, a1, a2 = coeffs
    x = np.asarray(x, dtype=np.float64)
    one_d = x.ndim == 1
    if one_d:
        x = x[:, None]
    try:  # pragma: no cover - scipy is optional
        from scipy.signal import lfilter

        y, zf = lfilter([b0, b1, b2], [1.0, a1, a2], x, axis=0, zi=state)
        state[:, :] = np.asarray(zf).reshape(state.shape)
        return y[:, 0] if one_d else y
    except Exception:  # pragma: no cover
        n, ch = x.shape
        # The two buffers are filled one column and one sample at a time, so
        # they start at zero: an uninitialised buffer would carry whatever the
        # allocator handed out into the part that a later write misses.
        y = np.zeros_like(x)
        z0 = np.array(state[0], copy=True)
        z1 = np.array(state[1], copy=True)
        for c in range(ch):
            s0 = float(z0[c])
            s1 = float(z1[c])
            col = x[:, c]
            out = np.zeros(n, dtype=np.float64)
            for i in range(n):
                xi = float(col[i])
                yi = b0 * xi + s0
                s0 = b1 * xi - a1 * yi + s1
                s1 = b2 * xi - a2 * yi
                out[i] = yi
            y[:, c] = out
            state[0, c] = s0
            state[1, c] = s1
        return y[:, 0] if one_d else y


@register
class WowAndFlutter(Module):
    """Estimate and correct slow pitch modulation."""

    code = "AUR_WOWFLUTTER"
    display_name = "Wow & Flutter"
    category = "Repair"
    edition = "advanced"
    element = "WowAndFlutter"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # The tracker follows the mono mix, so it keeps one column of state
        # whatever the channel count of the block is.
        self._tracker_state = np.zeros((2, 1), dtype=np.float64)
        self._tracker_q_state = np.zeros((2, 1), dtype=np.float64)
        self._tracker_coeffs = _lowpass(_TRACK_BAND_HZ, _FILTER_Q, self.sample_rate)
        self._phase = 0.0
        self._previous = 0.0 + 0.0j
        self._smooth_length = max(2, int(_SMOOTH_SECONDS * self.sample_rate))
        self._smooth_history = np.zeros(self._smooth_length, dtype=np.float64)
        # how many samples are still to come out of the transient of the
        # filters
        self._warmup = _WARMUP
        # the state of the two bands: the low-pass of the baseband, the
        # delay line that aligns the band with the others, the phase of the
        # demodulator and the rotation that undoes the phase of the image
        self._bands = {
            "Wow": self._make_band(_WOW_HALF_WIDTH),
            "Flutter": self._make_band(_FLUTTER_HALF_WIDTH),
        }
        # the ring of the input, so that the interpolator can read the
        # material that lies before the block
        self._channels = max(int(channels), 1)
        self._ring = np.zeros((_HISTORY, self._channels), dtype=np.float64)
        self._ring_mask = _HISTORY - 1
        self._written = 0
        self._offset = 0.0
        self._dc_state = np.zeros((2, 1), dtype=np.float64)
        # the slow mean of the correction: a leaky integrator with a time
        # constant of a second
        beta = 1.0 - math.exp(-1.0 / (_DC_TAU * float(self.sample_rate)))
        self._dc_coeffs = (beta, 0.0, 0.0, -(1.0 - beta), 0.0)

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Wow",
                type="float",
                default=1.0,
                minimum=0.0,
                maximum=5.0,
                unit="Hz",
                group="Wow",
                help="Rate of the slow pitch drift that is corrected.",
            ),
            ParamDesc(
                name="Flutter",
                type="float",
                default=20.0,
                minimum=8.0,
                maximum=40.0,
                unit="Hz",
                group="Flutter",
                help="Rate of the fast pitch flutter that is corrected.",

                curve="log"),
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Sensitivity",
                help="How much of the measured modulation is removed.",
            ),
            ParamDesc(
                name="Center Global Pitch",
                type="float",
                default=440.0,
                minimum=380.0,
                maximum=500.0,
                unit="Hz",
                group="Pitch",
                help="Frequency the pitch tracker follows.",

                curve="log"),
        ]

    def reset(self) -> None:
        """Clear the filter states and the history of the interpolator."""
        self._tracker_state = np.zeros((2, 1), dtype=np.float64)
        self._tracker_q_state = np.zeros((2, 1), dtype=np.float64)
        self._phase = 0.0
        self._previous = 0.0 + 0.0j
        self._smooth_history = np.zeros(self._smooth_length, dtype=np.float64)
        self._warmup = _WARMUP
        for band in self._bands.values():
            band["state"] = np.zeros((2, 1), dtype=np.float64)
            band["state_q"] = np.zeros((2, 1), dtype=np.float64)
            band["delay"] = np.zeros(0, dtype=np.float64)
            band["phase"] = 0.0
            band["frequency"] = -1.0
            band["delay_length"] = 0
        self._ring = np.zeros((_HISTORY, self._channels), dtype=np.float64)
        self._written = 0
        self._offset = 0.0
        self._dc_state = np.zeros((2, 1), dtype=np.float64)

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the tracker)."""
        return int(_LATENCY)

    # -- the bands -----------------------------------------------------------
    def _make_band(self, half_width: float) -> dict:
        """Return the state of one band."""
        return {
            "half_width": float(half_width),
            "coeffs": _lowpass(half_width, _FILTER_Q, self.sample_rate),
            "state": np.zeros((2, 1), dtype=np.float64),
            "state_q": np.zeros((2, 1), dtype=np.float64),
            "delay": np.zeros(0, dtype=np.float64),
            "phase": 0.0,
            "rotation": 1.0 + 0.0j,
            "frequency": -1.0,
        }

    def _prepare_band(self, band: dict, frequency: float) -> None:
        """Set a band up for the frequency it has to remove.

        The band is delayed so that it carries the same delay as the slowest
        of the bands: the correction of a sample is applied at one moment,
        and the bands have to agree on that moment.
        """
        if abs(band["frequency"] - frequency) < 1e-9:
            return
        band["frequency"] = float(frequency)
        band["coeffs"] = _lowpass(band["half_width"], _FILTER_Q, self.sample_rate)
        # the delay the tracker and the smoother add before the band is
        # selected
        natural = _phase_delay(self._tracker_coeffs, max(frequency, 0.1),
                               self.sample_rate) + self._smooth_length
        wanted = int(round(max(0.0, _LATENCY - natural)))
        band["delay_length"] = wanted
        band["delay"] = np.zeros(wanted, dtype=np.float64)
        # the image of the band is the negative frequency that the
        # demodulation folds onto the baseband; the rotation undoes the
        # phase it gives the band
        image = _response(band["coeffs"], 2.0 * max(frequency, 0.1),
                          self.sample_rate)
        total = 1.0 + image.conjugate()
        band["rotation"] = (
            total.conjugate() / abs(total) if abs(total) > 1e-12 else 1.0 + 0.0j
        )

    # -- tracking -------------------------------------------------------------
    def _deviation(self, mono: np.ndarray) -> np.ndarray:
        """Return the relative pitch deviation of the programme."""
        n = mono.shape[0]
        centre = float(self.params.get("Center Global Pitch"))
        fs = float(self.sample_rate)
        increment = 2.0 * math.pi * centre / fs
        phase = self._phase + increment * np.arange(n, dtype=np.float64)
        self._phase = float(self._phase + increment * n) % (2.0 * math.pi)
        cos = np.cos(phase)
        sin = np.sin(phase)
        real = _apply_biquad(self._tracker_coeffs, (mono * cos)[:, None],
                             self._tracker_state)[:, 0]
        imag = _apply_biquad(self._tracker_coeffs, (-mono * sin)[:, None],
                             self._tracker_q_state)[:, 0]
        baseband = real + 1j * imag
        # the speed of the phase of the baseband is the offset of the pitch
        previous = np.empty(n, dtype=np.complex128)
        previous[0] = self._previous
        if n > 1:
            previous[1:] = baseband[:-1]
        self._previous = complex(baseband[-1])
        offset = np.angle(baseband * np.conj(previous)) * fs / (2.0 * math.pi)

        # a short average takes the noise out of the estimate.  It has to
        # come back with the length of the block, or the correction would be
        # longer than the audio it is applied to.
        length = self._smooth_length
        combined = np.concatenate([self._smooth_history, offset])
        cumulative = np.cumsum(combined)
        window = cumulative[length - 1:length - 1 + n] - np.concatenate(
            ([0.0], cumulative[:n - 1])
        )
        self._smooth_history = combined[-length:]
        deviation = (window / float(length)) / max(centre, 1.0)
        deviation = np.clip(deviation, -_MAX_DEVIATION, _MAX_DEVIATION)
        # the first samples carry the transient of the filters: the estimate
        # of the pitch is not worth anything there, and a wrong estimate would
        # warp the start of the material.
        warm = self._warmup
        if warm > 0:
            if warm >= n:
                deviation[:] = 0.0
                warm -= n
            else:
                deviation[:warm] = 0.0
                warm = 0
            self._warmup = warm
        return deviation

    # -- the bands ------------------------------------------------------------
    def _select_bands(self, deviation: np.ndarray) -> np.ndarray:
        """Return the part of the deviation that lives in the bands.

        The bands are quadrature demodulators: the deviation is mixed down to
        the rate of the band, the baseband is low-passed, and the band is
        rebuilt from the two quadratures.  The result carries the delay the
        band was given, so that every band agrees on the moment the
        correction belongs to.
        """
        n = deviation.shape[0]
        fs = float(self.sample_rate)
        out = np.zeros(n, dtype=np.float64)
        for name, frequency in (
            ("Wow", float(self.params.get("Wow"))),
            ("Flutter", float(self.params.get("Flutter"))),
        ):
            band = self._bands[name]
            if name == "Wow" and frequency <= 0.0:
                continue
            if name == "Flutter" and frequency < 8.0:
                continue
            self._prepare_band(band, frequency)
            increment = 2.0 * math.pi * frequency / fs
            phase = band["phase"] + increment * np.arange(n, dtype=np.float64)
            band["phase"] = float(band["phase"] + increment * n) % (2.0 * math.pi)
            cos = np.cos(phase)
            sin = np.sin(phase)
            real = _apply_biquad(band["coeffs"], (deviation * cos)[:, None],
                                 band["state"])[:, 0]
            imag = _apply_biquad(band["coeffs"], (-deviation * sin)[:, None],
                                 band["state_q"])[:, 0]
            baseband = (real + 1j * imag) * band["rotation"]
            # the demodulation halves the amplitude of a tone, so the band is
            # built with twice the amplitude of its baseband
            rebuilt = (baseband * np.exp(1j * phase)).real * 2.0
            # the band is delayed so that it carries the same delay as the
            # other bands
            length = int(band.get("delay_length", 0))
            if length > 0:
                line = np.concatenate([band["delay"], rebuilt])
                rebuilt = line[length:]
                band["delay"] = line[-length:]
            out += rebuilt
        # the mean of the correction is taken off, so that the duration of the
        # signal does not change.  The mean is tracked slowly, so that it does
        # not turn into a phase shift of the band itself.
        mean = _apply_biquad(self._dc_coeffs, out[:, None], self._dc_state)[:, 0]
        return out - mean

    # -- correction -----------------------------------------------------------
    @staticmethod
    def _interpolate(x: np.ndarray, positions: np.ndarray, half: int = 16) -> np.ndarray:
        """Resample a block at the given fractional positions.

        The kernel is a windowed sinc: the sample at ``base + offset`` is
        weighted with the sinc of its distance to the wanted position, and the
        window is centred on that position.
        """
        n = x.shape[0]
        base = np.floor(positions).astype(np.int64)
        frac = positions - base
        offsets = np.arange(-half + 1, half + 1)
        index = base[None, :] + offsets[:, None]
        distance = frac[None, :] - offsets[:, None]
        window = np.cos(np.pi * distance / half)
        window += 1.0
        window *= 0.5
        weight = np.sinc(distance)
        weight *= window
        valid = (index >= 0) & (index < n)
        weight *= valid
        gathered = x[np.clip(index, 0, n - 1)]
        return np.einsum("tn,tnc->nc", weight, gathered)

    def _resample(self, positions: np.ndarray) -> np.ndarray:
        """Resample at the given global positions, reading from the ring.

        The input is kept in a ring, so the interpolator can read the samples
        that lie before the block — the time warp reads into the past.
        """
        n = positions.shape[0]
        out = np.zeros((n, self._channels), dtype=np.float64)
        half = _INTERP_HALF
        offsets = np.arange(-half + 1, half + 1)
        for start in range(0, n, _PIECE):
            stop = min(start + _PIECE, n)
            local = positions[start:stop]
            base = np.floor(local).astype(np.int64)
            frac = local - base
            index = base[None, :] + offsets[:, None]
            distance = frac[None, :] - offsets[:, None]
            window = np.cos(np.pi * distance / half)
            window += 1.0
            window *= 0.5
            weight = np.sinc(distance)
            weight *= window
            oldest = self._written - _HISTORY
            valid = (index >= oldest) & (index < self._written)
            weight *= valid
            slot = index & self._ring_mask
            gathered = self._ring[slot]
            out[start:stop] = np.einsum("tn,tnc->nc", weight, gathered)
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Correct the slow pitch modulation of a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        n = buf.shape[0]
        if n == 0:
            return buf
        if buf.shape[1] != self._channels:
            self._channels = int(buf.shape[1])
            self._ring = np.zeros((_HISTORY, self._channels), dtype=np.float64)
            self._written = 0
        sensitivity = float(self.params.get("Sensitivity"))
        if sensitivity <= 0.0:
            return buf

        mono = np.mean(buf, axis=1)
        deviation = self._deviation(mono)
        correction = self._select_bands(deviation)

        # the read position is the integral of the speed factor: where the
        # pitch was high the material is read earlier, which slows it down.
        factor = 1.0 - correction * sensitivity
        steps = factor - 1.0
        offsets = self._offset + np.cumsum(steps) - steps
        self._offset = float(offsets[-1] + steps[-1])
        # the material the correction belongs to was measured
        # ``latency_samples`` ago, so the output is that much behind.  The
        # positions are the global ones: the ring holds the whole history.
        base_global = self._written
        positions = (
            np.arange(n, dtype=np.float64) + offsets - _LATENCY + base_global
        )

        # the input goes into the ring, so that the interpolator can read the
        # material that lies before the block
        out = np.zeros((n, self._channels), dtype=np.float64)
        for start in range(0, n, _PIECE):
            stop = min(start + _PIECE, n)
            piece = buf[start:stop]
            slot = (np.arange(stop - start, dtype=np.int64) + self._written) & self._ring_mask
            self._ring[slot] = piece
            self._written += stop - start
            out[start:stop] = self._resample(positions[start:stop])
        return out
