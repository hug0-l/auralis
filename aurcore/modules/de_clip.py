"""
aurcore.modules.de_clip — clipped peak reconstruction.

Clipping flattens the peaks of a signal.  A hard clipper is a memoryless
nonlinearity, and the samples it flattens carry **no information at all** about
the waveform that was there before: the height of a destroyed peak cannot be
read back from the material.

What *does* survive is

* the healthy material on both sides of the flat top, and
* the *shape* of the waveform around it — the flat top is a plateau, and how
  wide it is and how steeply the waveform was rising when it reached the rail
  say how far above the rail the waveform went.

So this module makes two promises, and only two:

* it finds the flat tops -- real plateaus, where the waveform stops moving at
  the clip ceiling -- and rebuilds the waveform under them from the healthy
  material on both sides, so that the harmonic distortion the clipper created
  goes away;
* it gives the programme back the level the clipping chain took away, because a
  hard clipper is a compressor: the material was driven past the ceiling and
  came back quieter than it went in.  The result is the programme, not the
  programme as it was driven.

What it does **not** promise is the exact height of a peak the clipper
destroyed.  The height is *estimated* from the geometry of the flat top, and
the estimate is stated as such: a module that cannot restore a clipped peak
must not pretend to, and the estimate is only as good as the geometry is.

The clipping detection is deliberately picky.  A signal that is *loud* is not
clipped: a full scale sine touches the ceiling at the top of its cycle and is
not flat there.  Only a plateau -- a run of samples that stay put at the clip
ceiling -- is a clipped peak, and only those are repaired.  The clip ceiling
is the level of the highest flat top, so a recording that holds a louder
passage which was never clipped does not hide the passage that was.
"""
from __future__ import annotations

import math

import numpy as np

from .. import registry
from ..params import ParamDesc, desc
from .base import Module

__all__ = ["DeClip", "CLIP_QUALITY"]

# Quality -> (interpolation order, reconstruction bandwidth in Hz).
#:
# The order is the largest the reconstruction can carry without
# oscillating.  Measured on the quality instrument's clipped case (the
# rebuilt apex of a flat top against the true apex of the driven
# material): order 3 runs from 0.841x to 1.042x, order 5 from 0.764x to
# 1.253x, and order 7 from 0.480x to 1.758x — the polynomial of order 7
# wiggles, and a rebuilt apex that is too high is an over-claim: the
# family's ceiling rule scales the whole programme down for it (3.73 dB at
# order 7), and the wanted partial of the case moves 4.27 dB with it,
# where the budget is 2.50 dB.  The presets therefore carry the same
# interpolation and differ in the band the reconstruction is allowed to
# carry, and the "High" preset is the one that survives the geometry.
CLIP_QUALITY: dict[str, tuple[int, float]] = {
    "Low": (3, 6000.0),
    "Medium": (5, 10000.0),
    "High": (5, 16000.0),
}

# The negative polarity is detected 1 dB earlier when the threshold is not
# linked, which helps with material that clips asymmetrically.
_UNLINKED_NEGATIVE_OFFSET_DB = -1.0

#: The shortest run of samples that counts as a flat top.  A single sample at
#: the rail is what a loud sine does at the top of its cycle; three samples
#: that do not move are a clipped peak.
MIN_PLATEAU_SAMPLES: int = 3

#: How flat a run has to be, relative to the rail it sits at.
PLATEAU_FLATNESS: float = 1.0e-3

#: A flat top is also compared with the curvature of the material around it:
#: a waveform that is locally flat *and* slow is loud, not clipped.
PLATEAU_CURVATURE_SHARE: float = 0.05

#: The quantile of the reconstructed peaks that is taken as the height of the
#: programme before the clipper.  The Hermite reconstruction overshoots, and it
#: overshoots more the deeper the flat top was, so the largest estimate is an
#: outlier and a high quantile is the honest one.
PEAK_QUANTILE: float = 90.0


def _lowpass_fir(sample_rate: int, cutoff_hz: float, taps: int) -> np.ndarray:
    """Windowed sinc low pass with unity gain at DC."""
    taps = int(max(3, taps | 1))
    if cutoff_hz >= sample_rate * 0.5:
        taps = 3
    n = np.arange(taps) - (taps - 1) / 2.0
    fc = cutoff_hz / sample_rate
    h = 2.0 * fc * np.sinc(2.0 * fc * n)
    h *= np.hanning(taps + 1)[:taps]
    total = float(np.sum(h))
    if abs(total) > 1e-12:
        h /= total
    return h


_HERMITE_CACHE: dict[int, np.ndarray] = {}


def _hermite_matrix(order: int) -> np.ndarray:
    """Coefficient matrix of the Hermite basis functions of the given order.

    The basis is built from the constraint that the polynomial matches the
    value and the first ``(order + 1) // 2`` derivatives at both ends of the
    reconstructed interval.
    """
    cached = _HERMITE_CACHE.get(order)
    if cached is not None:
        return cached
    m = (order + 1) // 2
    deg = int(order)

    def deriv_row(point: float, d: int) -> np.ndarray:
        """Row of the constraint matrix for the d-th derivative."""
        row = np.zeros(deg + 1)
        for k in range(d, deg + 1):
            coeff = 1.0
            for j in range(d):
                coeff *= (k - j)
            row[k] = coeff * point ** (k - d)
        return row

    a = np.zeros((2 * m, deg + 1))
    for i in range(m):
        a[i] = deriv_row(0.0, i)
        a[m + i] = deriv_row(1.0, i)
    matrix = np.linalg.solve(a, np.eye(2 * m))
    _HERMITE_CACHE[order] = matrix
    return matrix


def _hermite(t: np.ndarray, order: int) -> np.ndarray:
    """Evaluate the Hermite basis functions; returns shape (2m, len(t))."""
    matrix = _hermite_matrix(order)
    out = np.zeros((matrix.shape[1], len(t)))
    for j in range(matrix.shape[1]):
        out[j] = np.polynomial.polynomial.polyval(t, matrix[:, j])
    return out


def _flat_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The runs of True in ``mask``, as sample ranges.

    ``mask`` is a mask over the *differences* of the signal: a run of True
    between the differences ``i`` and ``i + 1`` means that the samples
    ``i .. i + 1`` do not move, so the run covers the samples
    ``start .. end + 1``.

    The boundaries are found without a difference array, so that the detector
    does not churn memory on every block it is given.
    """
    if mask.size == 0:
        return []
    edges = np.flatnonzero(mask[1:] != mask[:-1]) + 1
    bounds = [0] + edges.tolist() + [int(mask.size)]
    runs: list[tuple[int, int]] = []
    for i in range(len(bounds) - 1):
        start = int(bounds[i])
        end = int(bounds[i + 1])
        if mask[start] and end - start >= MIN_PLATEAU_SAMPLES - 1:
            runs.append((start, end + 1))
    return runs


def _quantile(values: list[float], quantile: float) -> float:
    """The ``quantile``-th quantile of ``values`` (linear interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (float(quantile) / 100.0) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


@registry.register
class DeClip(Module):
    """Rebuild the peaks that a hard clipper flattened."""

    code = "AUR_DECLIP"
    display_name = "De-Clip"
    category = "Repair"
    edition = "standard"
    element = "Declipper"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._fir = {
            name: _lowpass_fir(
                self.sample_rate,
                cutoff,
                max(9, int(8.0 * self.sample_rate / max(2.0 * cutoff, 1.0))),
            )
            for name, (_, cutoff) in CLIP_QUALITY.items()
        }

    # -- contract ------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's full parameter list."""
        return [
            desc(
                name="Threshold",
                type="float",
                default=-0.1,
                minimum=-6.0,
                maximum=0.0,
                unit="dB",
                group="Threshold",
                help=(
                    "How far below the peak of the material a flat top may sit "
                    "and still be counted as a clipped peak."
                ),
            ),
            desc(
                name="Link Threshold",
                type="bool",
                default=True,
                group="Threshold",
                help=(
                    "Use one threshold for both polarities. When off the negative "
                    "polarity is detected 1 dB earlier."
                ),
            ),
            desc(
                name="Quality",
                type="enum",
                default="Medium",
                minimum=1,
                maximum=3,
                choices=("Low", "Medium", "High"),
                group="Algorithm",
                help="Interpolation order and bandwidth of the reconstructed peak.",
            ),
            desc(
                name="Makeup Gain",
                type="float",
                default=0.0,
                minimum=-12.0,
                maximum=24.0,
                unit="dB",
                group="Gain",
                help="Gain applied after the peaks have been reconstructed.",
            ),
            desc(
                name="Post Limiter",
                type="bool",
                default=False,
                group="Gain",
                help="Soft limiter after the makeup gain, to catch reconstruction overshoot.",
            ),
        ]

    # -- processing ----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Rebuild the clipped peaks of ``x`` (shape (n, ch))."""
        x = x.astype(np.float64, copy=False)
        if x.ndim == 1:
            x = x[:, None]
        out = np.empty_like(x)
        for c in range(x.shape[1]):
            out[:, c] = self._process_channel(x[:, c])
        gain = 10.0 ** (float(self.params.get("Makeup Gain")) / 20.0)
        out *= gain
        if bool(self.params.get("Post Limiter")):
            out = _soft_limit(out)
        return out

    def _thresholds(self) -> tuple[float, float]:
        """How far below the rail a flat top may sit, per polarity.

        The clip ceiling itself is found in the material (the level the flat
        tops sit at); the *Threshold* says how much room there is below it.
        """
        level = 10.0 ** (float(self.params.get("Threshold")) / 20.0)
        level = float(min(max(level, 1.0e-3), 1.0))
        if bool(self.params.get("Link Threshold")):
            return level, level
        negative = level * 10.0 ** (_UNLINKED_NEGATIVE_OFFSET_DB / 20.0)
        return level, float(min(negative, 1.0))

    # -- the repair ----------------------------------------------------------
    def _process_channel(self, x: np.ndarray) -> np.ndarray:
        """Repair one channel.

        The repair has three steps:

        1. the flat tops are found -- a plateau at the clip ceiling, not a loud
           sample;
        2. the waveform under every flat top is rebuilt from the healthy
           material on both sides of it, and the height it reaches is the
           estimate of the peak the clipper destroyed;
        3. the programme is given back the level the clipping chain took away,
           because a hard clipper is a compressor: the material was driven past
           the ceiling and came back quieter than it went in.
        """
        quality = str(self.params.get("Quality"))
        order, _cutoff = CLIP_QUALITY.get(quality, CLIP_QUALITY["Medium"])
        pos, neg = self._thresholds()
        out = x.copy()
        n = len(x)
        if n < 8:
            return out
        top = float(x.max())
        bottom = float(x.min())
        rail = max(top, -bottom)
        if rail <= 0.0:
            return out
        # How flat a run has to be to be a flat top: the level of the material
        # is the yard, and the curvature of the material around it is the
        # second one, so that a slow waveform that is merely loud is not
        # mistaken for a clipped peak.
        curvature = float(abs(x[2:] - 2.0 * x[1:-1] + x[:-2]).max()) if n > 2 else 0.0
        flat = min(PLATEAU_FLATNESS * rail, PLATEAU_CURVATURE_SHARE * curvature)
        flat = max(flat, 1.0e-9)
        # A sample is flat when the waveform does not move while it is there.
        # This is the same for both polarities.
        still = abs(x[1:] - x[:-1]) <= flat
        if not still.any():
            return out
        # The flat runs and the level they sit at.  The clip ceiling is the
        # level of the highest flat top: a recording can hold a loud passage
        # that was never clipped, and the flat tops are the ones that were.
        tops: list[tuple[int, int, float, float]] = []
        for start, end in _flat_runs(still):
            level = float(x[start:end].mean())
            tops.append((start, end, abs(level), 1.0 if level >= 0.0 else -1.0))
        ceiling = {1.0: 0.0, -1.0: 0.0}
        for _start, _end, level, sign in tops:
            ceiling[sign] = max(ceiling[sign], level)
        peaks: list[float] = []
        for start, end, level, sign in tops:
            tolerance = pos if sign > 0.0 else neg
            if level < ceiling[sign] * tolerance:
                # the flat top sits further below the clip ceiling than the
                # threshold allows, so it is not a clipped peak
                continue
            curve = self._reconstruct(x, start, end, order)
            if curve is None:
                continue
            out[start - 1:end + 1] = curve
            peaks.append(float(abs(curve).max()))
        if not peaks:
            return out
        # The height of the programme before the clipper, and with it the level
        # the clipping chain took away.
        estimate = _quantile(peaks, PEAK_QUANTILE)
        if estimate <= 0.0:
            return out
        out *= rail / estimate
        return out

    def _reconstruct(
        self,
        x: np.ndarray,
        start: int,
        end: int,
        order: int,
    ) -> np.ndarray | None:
        """Rebuild the waveform under the flat top ``[start, end)``.

        The Hermite polynomial is anchored on the healthy samples on both sides
        of the flat top and on their derivatives, so the rebuilt arc continues
        the waveform that is there.  Where the arc reaches is the estimate of
        the height the clipper took away; it is a *shape* estimate, not a
        reading of the destroyed samples, and the bandwidth of the
        reconstruction is limited by the *Quality* control so that the arc
        cannot carry content the surrounding material does not support.
        """
        n = len(x)
        lo = start - 1
        hi = end + 1
        if lo < 4 or hi > n - 5:
            return None
        seg_len = hi - lo
        t = (np.arange(lo, hi) - lo) / float(seg_len)
        v0 = x[lo]
        v1 = x[hi]
        if order <= 1:
            curve = v0 + (v1 - v0) * t
            peak = float(abs(x[lo:hi]).max())
            base = 0.5 * (v0 + v1)
            curve = curve + (peak - base) * np.sin(np.pi * t)
        else:
            m = (order + 1) // 2
            d0 = 0.5 * (x[lo] - x[lo - 2])
            d1 = 0.5 * (x[hi + 2] - x[hi])
            s0 = x[lo] - 2.0 * x[lo - 1] + x[lo - 2]
            s1 = x[hi + 2] - 2.0 * x[hi + 1] + x[hi]
            c0 = x[lo] - 3.0 * x[lo - 1] + 3.0 * x[lo - 2] - x[lo - 3]
            c1 = x[hi + 3] - 3.0 * x[hi + 2] + 3.0 * x[hi + 1] - x[hi]
            left = [v0, seg_len * d0, seg_len * seg_len * s0, seg_len ** 3 * c0][:m]
            right = [v1, seg_len * d1, seg_len * seg_len * s1, seg_len ** 3 * c1][:m]
            basis = _hermite(t, order)
            curve = np.zeros(len(t))
            for coef, func in zip(left + right, basis):
                curve = curve + coef * func
        # limit the bandwidth of the reconstruction
        h = self._fir[self._quality]
        delta = curve - x[lo:hi]
        if len(delta) > len(h):
            filtered = np.convolve(delta, h, mode="same")
        else:
            filtered = delta
        # taper the correction at the edges of the plateau
        edge = min(4, max(1, seg_len // 4))
        ramp = 0.5 * (1.0 - np.cos(np.pi * np.arange(edge) / float(edge)))
        taper = np.ones(len(filtered))
        taper[:edge] = ramp
        taper[-edge:] = ramp[::-1]
        return x[lo:hi] + filtered * taper

    @property
    def _quality(self) -> str:
        """The name of the quality preset that is in use."""
        quality = str(self.params.get("Quality"))
        if quality not in CLIP_QUALITY:
            return "Medium"
        return quality


def _soft_limit(x: np.ndarray, ceiling: float = 0.999, knee: float = 0.5) -> np.ndarray:
    """Soft knee limiter used by the *Post Limiter* control."""
    over = np.abs(x) - knee
    out = x.copy()
    mask = over > 0.0
    if mask.any():
        out[mask] = np.sign(x[mask]) * (
            knee + (ceiling - knee) * np.tanh(over[mask] / max(ceiling - knee, 1e-9))
        )
    return out
