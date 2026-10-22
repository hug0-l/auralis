"""
aurcore.modules.interpolate — repair of isolated clicks and dropouts.

The module finds isolated damaged regions with the shared transient engine and
replaces them with an interpolation.  The *Quality* control sets the order of
the interpolation:

* up to eight surrounding samples are used to fit a polynomial of that order,
  which is the right tool for short clicks in noisy material;
* in tonal material the surrounding partials are measured and re-synthesised,
  which is the right tool for longer dropouts.

Regions longer than 4000 samples are left untouched: they are not "clicks".
"""
from __future__ import annotations

import numpy as np

from .. import registry
from ..params import ParamDesc, desc
from .base import Module
from .de_click import (
    TransientRepair,
    TransientSettings,
    _eval_partials,
    _fit_partials,
)

__all__ = ["Interpolate", "MAX_REGION_SAMPLES"]

# Regions longer than this are not treated as isolated clicks.
MAX_REGION_SAMPLES = 4000

# Detection sensitivity used by the module (there is no sensitivity control).
_DETECT_SENSITIVITY = 3.0

#: The longest gap a polynomial interpolation is used for.  A polynomial of
#: order ``n`` is an interpolation over a handful of samples; extrapolated
#: over a long gap it runs away, and a dropout that is filled with a
#: polynomial that runs away is worse than the dropout it replaced.  A full
#: scale sine whose tail was treated as a dropout came out at 518 before
#: this was fixed.
_MAX_POLY_GAP = 64


def _polynomial_fill(x: np.ndarray, start: int, end: int, order: int) -> np.ndarray:
    """Replace the gap with a polynomial of the requested order."""
    n = len(x)
    radius = max(4, 2 * (order + 1))
    lo = max(0, start - radius)
    hi = min(n, end + radius)
    idx = np.arange(lo, hi)
    idx = idx[(idx < start) | (idx >= end)]
    if idx.size < order + 1:
        idx = np.arange(max(0, start - (order + 1)), min(n, end + (order + 1)))
        idx = idx[(idx < start) | (idx >= end)]
    if idx.size < 2:
        return np.full(end - start, float(x[start - 1]) if start > 0 else 0.0)
    if end - start > _MAX_POLY_GAP:
        # A long gap is a dropout, not a click.  The polynomial is fitted over
        # the samples around the gap and extrapolated over the gap itself,
        # and an extrapolation over hundreds of samples runs away.  The gap
        # is closed with a straight line between its edges instead.
        head = float(x[start - 1]) if start > 0 else 0.0
        tail = float(x[end]) if end < n else head
        return np.linspace(head, tail, end - start, dtype=np.float64)
    centre = 0.5 * (start + end)
    u = (idx - centre) / float(radius)
    vander = np.polynomial.polynomial.polyvander(u, int(order))
    coefs, *_ = np.linalg.lstsq(vander, x[idx], rcond=None)
    gap = np.arange(start, end)
    fill = np.polynomial.polynomial.polyval((gap - centre) / float(radius), coefs)
    # The fill is an interpolation of the material around the gap: it may
    # not be louder than that material.  A least squares fit of a
    # polynomial is not bounded by the data it is fitted to, and a fill
    # that is louder than the gap it fills is a click of its own.
    limit = float(np.max(np.abs(x[idx])))
    peak = float(np.max(np.abs(fill))) if fill.size else 0.0
    if limit > 0.0 and peak > limit:
        fill = fill * (limit / peak)
    return fill


@registry.register
class Interpolate(Module):
    """Interpolate over isolated clicks and dropouts."""

    code = "AUR_INTERPOLATE"
    display_name = "Interpolate"
    category = "Utility"
    edition = "standard"
    element = "Interpolate"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = TransientRepair(sample_rate, channels)

    # -- contract ------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """The module's full parameter list."""
        return [
            desc(
                name="Quality",
                type="int",
                default=3,
                minimum=1,
                maximum=8,
                group="Quality",
                help="Order of the interpolation used to replace the damaged samples.",
            ),
        ]

    def process(self, x: np.ndarray) -> np.ndarray:
        """Interpolate over the damaged regions of ``x`` (shape (n, ch))."""
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        if x.shape[0] < 64:
            return x.copy()
        order = int(self.params.get("Quality"))
        settings = TransientSettings(
            sensitivity=_DETECT_SENSITIVITY,
            band=(0.0, self.sample_rate * 0.5),
        )
        events = self.engine.detect(x, settings, refine=True)
        out = x.copy()
        for c in range(x.shape[1]):
            signal = out[:, c]
            for start, end in events:
                start = max(1, start)
                end = min(len(signal), end)
                if end - start <= 0:
                    continue
                start, end = self._extend_gap(signal, start, end)
                if end - start <= 0 or end - start >= MAX_REGION_SAMPLES:
                    continue
                fill = self._tonal_fill(signal, start, end, order)
                if fill is None:
                    fill = _polynomial_fill(signal, start, end, order)
                signal[start:end] = fill
        return out

    def _tonal_fill(
        self, x: np.ndarray, start: int, end: int, order: int
    ) -> np.ndarray | None:
        """Re-synthesise the partials that surround the gap.

        Returns ``None`` when the material is not tonal, in which case the
        polynomial interpolation is used instead.
        """
        n = len(x)
        ctx = max(512, int(0.043 * self.sample_rate))
        n_left = min(ctx, start)
        n_right = min(ctx, n - end)
        left = np.arange(start - n_left, start)
        right = np.arange(end, end + n_right)
        if left.size < 64 or right.size < 64:
            return None
        models, explained = _fit_partials(
            x, left, right, self.sample_rate, max(1, min(int(order), 8))
        )
        if not models or explained < 0.5:
            return None
        fill = _eval_partials(models, start, end, self.sample_rate)
        # The re-synthesised partials may not be louder than the material
        # they were measured from: a model that does not describe the
        # neighbourhood produces a fill that is a click of its own.
        limit = float(np.max(np.abs(x[left]))) if left.size else 0.0
        peak = float(np.max(np.abs(fill))) if fill.size else 0.0
        if limit > 0.0 and peak > limit:
            return None
        return fill

    def _extend_gap(self, x: np.ndarray, start: int, end: int) -> tuple[int, int]:
        """Grow a detected event over the dropout that contains it.

        A dropout is not an event on its own: the detector fires on the two
        edges where the material is interrupted.  The quiet part in between
        belongs to the damaged region as well.
        """
        n = len(x)
        ctx = max(256, int(0.02 * self.sample_rate))
        before = x[max(0, start - ctx) : start]
        after = x[end : min(n, end + ctx)]
        if before.size < 8 or after.size < 8:
            return start, end
        level = 0.5 * (
            float(np.sqrt(np.mean(before * before)))
            + float(np.sqrt(np.mean(after * after)))
        )
        if level < 1e-4:
            return start, end
        peak = max(
            float(np.max(np.abs(before))), float(np.max(np.abs(after)))
        )
        limit = 0.3 * peak
        limit = max(limit, 1e-6)

        forward = end
        limit_samples = MAX_REGION_SAMPLES
        while forward < n and forward - end < limit_samples:
            if abs(float(x[forward])) >= limit:
                break
            forward += 1
        backward = start
        while backward > 0 and start - backward < limit_samples:
            if abs(float(x[backward - 1])) >= limit:
                break
            backward -= 1
        return max(0, backward), min(n, forward)

    def reset(self) -> None:
        """Forget the running noise statistics."""
        self.engine.reset()
