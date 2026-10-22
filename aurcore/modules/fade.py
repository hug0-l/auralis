"""
aurcore.modules.fade — head and tail fades.

The fade shapes and the ramp maths are shared with the other two modules of
the gain element; they are imported from :mod:`aurcore.modules.gain` so a fade
looks identical wherever it is used.

Block coherence
---------------
The fades sit on the **material**, not on the block: the head fade covers the
first *Fade Length* seconds of the material and the tail fade the last ones,
whatever the size of the block the host hands over.  The module counts the
samples it has played, so the head fade is at the same place in every render.

The length of the material is what the host declares with
:meth:`Fade.set_material_length`; a render knows the length of the file it
renders, a live host does not.  Without that declaration the module fades
the head of the material and leaves the tail alone, because a module that
streams cannot know where the material ends.  A selection narrows the range
the fades are applied to, in the coordinates of the material.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc
from ..registry import register
from .base import Module
from .gain import FADE_TYPES, GainEngine

__all__ = ["Fade"]


@register
class Fade(Module):
    """Fade the head and the tail of the material over a fixed duration."""

    code = "AUR_FADE"
    display_name = "Fade"
    category = "Utility"
    edition = "standard"
    element = "GainStage"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.engine = GainEngine()
        # Optional (start, end) sample range in the coordinates of the
        # material; when set the fade is applied to the head and the tail of
        # that range instead of the head and the tail of the material.
        self.selection: tuple[int, int] | None = None
        # The number of samples the host has played, and the length of the
        # material when the host has declared it.
        self._position = 0
        self._length: int | None = None
        # the ramp of the current fade, kept between the blocks: it only
        # changes when the fade length or the shape changes
        self._ramp: np.ndarray | None = None
        self._ramp_key: tuple | None = None

    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Fade Type",
                type="enum",
                default="Log",
                minimum=0.0,
                maximum=3.0,
                choices=FADE_TYPES,
                group="Algorithm",
                help="Shape of the fade curve.",
            ),
            ParamDesc(
                name="Fade Length",
                type="float",
                default=1.0,
                minimum=0.01,
                maximum=30.0,
                unit="s",
                group="Fade",
                help="Duration of the fade-in and the fade-out.",
            ),
        ]

    def set_selection(self, start: int | None, end: int | None) -> None:
        """Restrict the fade to a sample range (``None`` clears it).

        The range is in the coordinates of the material: the head fade covers
        the first *Fade Length* seconds of the range and the tail fade the
        last ones.
        """
        if start is None or end is None:
            self.selection = None
        else:
            self.selection = (int(start), int(end))

    def set_material_length(self, length: int | None) -> None:
        """Tell the module how long the material is, in samples.

        A host that renders a file knows the length of the file, and a host
        that plays a live stream does not.  The tail fade is applied to the
        last *Fade Length* seconds of the material, so it needs that length.
        """
        self._length = None if length is None else max(0, int(length))

    def reset(self) -> None:
        """Forget the position in the material and its length."""
        self._position = 0
        self._length = None

    def _fade_ramp(self, n_fade: int, kind: str) -> np.ndarray:
        """The ramp of the fade, taken from the gain engine.

        The ramp only changes when the length of the fade or its shape
        changes, so it is kept between the blocks: a ramp that is built
        again for every block is the most expensive part of a fade.
        """
        key = (int(n_fade), str(kind))
        if self._ramp_key != key or self._ramp is None:
            self._ramp = self.engine.fade_ramp(int(n_fade), str(kind))
            self._ramp_key = key
        return self._ramp

    @property
    def latency_samples(self) -> int:
        """The fade does not move the signal, so it adds no latency."""
        return 0

    @property
    def settling_frames(self) -> int:
        """The fade shapes the material over its own length.

        The head fade covers the first *Fade Length* seconds of the material
        and the tail fade the last ones, so the level of the output depends
        on where in the material the module is: the answer of the module is
        only final once the fade is over.  The number is the length of the
        fade in frames, and it follows the parameter.

        The fade says so, and the gate of the edges
        (``qa/check_module_edges.py``) measures the level of the output only
        from the end of the fade on: a fade that is still ramping is not a
        module whose level depends on the length of the material, it is a
        module that is doing its job.
        """
        length = float(self.params.get("Fade Length"))
        return int(round(max(0.0, length) * float(self.sample_rate)))

    def _range(self) -> tuple[int, int | None]:
        """The range of the material the fades are applied to."""
        if self.selection is not None:
            return int(self.selection[0]), int(self.selection[1])
        return 0, self._length

    def process(self, x: np.ndarray) -> np.ndarray:
        """Fade the head and the tail of the material."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.size == 0:
            return buf
        if buf.ndim == 1:
            buf = buf[:, None]
        count = buf.shape[0]
        kind = str(self.params.get("Fade Type"))
        length = float(self.params.get("Fade Length"))
        n_fade = int(round(length * self.sample_rate))
        if n_fade <= 0:
            self._position += count
            return buf
        start, end = self._range()
        if end is not None:
            # the fade never reaches beyond the material
            n_fade = min(n_fade, max(0, int(end) - int(start)))
        if n_fade <= 0:
            self._position += count
            return buf
        ramp = self._fade_ramp(n_fade, kind)
        positions = np.arange(self._position, self._position + count, dtype=np.int64)
        gains = np.ones(count, dtype=np.float64)

        # the head of the material
        head = positions - int(start)
        inside = (head >= 0) & (head < n_fade)
        if bool(inside.any()):
            gains[inside] *= ramp[head[inside]]

        # the tail of the material
        if end is not None:
            tail = positions - (int(end) - n_fade)
            inside = (tail >= 0) & (tail < n_fade)
            if bool(inside.any()):
                gains[inside] *= ramp[n_fade - 1 - tail[inside]]

        self._position += count
        return buf * gains[:, None]
