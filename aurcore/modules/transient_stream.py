"""
aurcore.modules.transient_stream — the block coherent transient engine.

:mod:`aurcore.modules.de_click` holds the transient engine that
:class:`~aurcore.modules.de_click.DeClick`,
:class:`~aurcore.modules.de_crackle.DeCrackle` and
:class:`~aurcore.modules.mouth_de_click.MouthDeClick` are built on.  The
engine works on the block it is handed: it transforms the block, scores it
against a running background and repairs what it finds.  A block that is
short therefore sees a different world than a long one — the background of a
64 sample block is the loudest sample of that block, and the frames of the
transform do not reach the material around the block at all.

This module drives that engine the way a plug-in host drives a module: the
audio is analysed on a fixed grid of windows that does not depend on the size
of the blocks the host hands over, and the background of the detectors is
measured over a fixed length of material instead of over the block.

The grid
--------
The engine works on windows of ``_WINDOW`` samples that start ``_PREROLL``
samples before the part of the stream that is rendered, and every window
renders ``_STEP`` samples.  The window has to reach far enough behind the
rendered part for the partial fit of the repair to see the material in front
of the transient, and far enough in front of it for the repair to see the
material behind the transient; that is what the latency of the module is.

The background
--------------
Every detector divides its feature by the background of that feature, so the
background says how far a sample departs from the normal of the material
around it.  The background is the high quantile of the last
``_BACKGROUND_SAMPLES`` samples of the feature, which does not depend on the
size of the block the host hands over and which a transient does not drag up:
a quantile is not moved by a handful of outliers.
"""
from __future__ import annotations

import numpy as np

from .de_click import TransientRepair, TransientSettings

__all__ = ["RollingBackground", "StreamingTransientRepair"]

#: How many samples of material the background of a detector looks at.  The
#: engine asks the background twice per window (once per channel), so the
#: buffer holds twice that many values.
_BACKGROUND_SAMPLES = 48_000

#: The number of values the background buffer holds.
_BACKGROUND_VALUES = 2 * _BACKGROUND_SAMPLES

#: How many samples of the window are rendered at a time.
_STEP = 1024

#: The length of the window the engine works on.
_WINDOW = 3712

#: How many samples of the window precede the part that is rendered.
_PREROLL = 2048


class RollingBackground:
    """The robust background of one detector feature.

    The engine asks the background for the level of the feature every time it
    scores a block.  The level is the high quantile of the material seen so
    far, so it does not depend on the size of the block the host hands over.

    The quantile is 98 and not 99 because the history of a running detector
    is shorter than the whole of a recording: a high quantile of a short
    history sits a little higher than the same quantile of the whole
    material, and the detector would otherwise reach fewer transients than it
    does when the whole recording is analysed at once.
    """

    def __init__(self, span: int = _BACKGROUND_VALUES, quantile: float = 0.98):
        self._span = int(span)
        self._quantile = float(quantile)
        self._buffer = np.zeros(0, dtype=np.float64)
        self._value = 0.0
        self._fresh = 0

    def begin(self, fresh: int) -> None:
        """Announce how many of the values of the next call are new.

        The engine hands the background the values of the whole window, but
        the window overlaps the one before it, so only the last samples of
        the window are new.  Counting them once keeps the background the
        measurement of the material and not of the overlap.
        """
        self._fresh = int(fresh)

    def update(self, values) -> float:
        """Fold ``values`` into the background and return the new level."""
        v = np.abs(np.asarray(values, dtype=np.float64).ravel())
        if v.size:
            take = min(v.size, self._fresh)
            if take > 0:
                self._buffer = np.concatenate([self._buffer, v[-take:]])
                if self._buffer.size > self._span:
                    self._buffer = self._buffer[-self._span :]
                self._fresh -= take
                self._recompute()
        return self._value

    def _recompute(self) -> None:
        """Read the quantile of the history and smooth it into the level."""
        size = self._buffer.size
        if size == 0:
            return
        if size > 8:
            k = int(round(self._quantile * (size - 1)))
            level = float(np.partition(self._buffer, k)[k])
        else:
            level = float(self._buffer.max())
        if self._value <= 0.0:
            self._value = level
        else:
            self._value += 0.3 * (level - self._value)

    @property
    def value(self) -> float:
        """The current level of the background."""
        return self._value


class StreamingTransientRepair:
    """Transient detection and repair that does not depend on the block size.

    The engine of :mod:`aurcore.modules.de_click` does the work; this class
    drives it on a fixed grid of windows and keeps the background of the
    detectors on a fixed length of material.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.engine = TransientRepair(sample_rate, channels)
        self._backgrounds: dict[int, RollingBackground] = {}
        self._channels = max(int(channels), 1)
        self._buffer = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_repaired = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_removed = np.zeros((0, self._channels), dtype=np.float64)
        self._started = False
        self._started_output = False

    # -- contract ------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds.

        The repair of a transient needs the material behind it, so the
        window that renders a part of the stream reaches that far into the
        material that follows it.  The number does not depend on the size of
        the block the host hands over.
        """
        return int(_WINDOW - _PREROLL)

    def reset(self) -> None:
        """Forget the running noise statistics and the analysis state."""
        self.engine.reset()
        self._backgrounds = {}
        self._install_backgrounds()
        self._buffer = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_repaired = np.zeros((0, self._channels), dtype=np.float64)
        self._fifo_removed = np.zeros((0, self._channels), dtype=np.float64)
        self._started = False
        self._started_output = False

    def _install_backgrounds(self) -> None:
        """Hand the engine the backgrounds of the four detectors."""
        for feature in range(4):
            background = self._backgrounds.get(feature)
            if background is None:
                background = RollingBackground()
                self._backgrounds[feature] = background
            # the engine reads the background of every channel from the
            # background of channel zero
            self.engine._scales[(feature, 0)] = background

    # -- processing ----------------------------------------------------------
    def process(
        self, x: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Repair ``x`` and return ``(repaired, removed)``.

        ``removed`` is the artefact that was taken out of the signal; it is
        what the "output ... only" render modes return.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[0] == 0:
            return arr.copy(), np.zeros_like(arr)
        if arr.shape[1] != self._channels:
            # the caller changed the channel count: the analysis restarts
            self._channels = int(arr.shape[1])
            self.reset()
        if not self._started:
            # the first window reaches behind the start of the stream: the
            # material that is not there is silence
            self._buffer = np.concatenate(
                [np.zeros((_PREROLL, arr.shape[1]), dtype=np.float64), arr], axis=0
            )
            self._started = True
        else:
            self._buffer = np.concatenate([self._buffer, arr], axis=0)

        while self._buffer.shape[0] >= _WINDOW:
            window = self._buffer[:_WINDOW]
            repaired, removed = self._run(window, settings)
            self._fifo_repaired = np.concatenate(
                [self._fifo_repaired, repaired[_PREROLL : _PREROLL + _STEP]], axis=0
            )
            self._fifo_removed = np.concatenate(
                [self._fifo_removed, removed[_PREROLL : _PREROLL + _STEP]], axis=0
            )
            self._buffer = self._buffer[_STEP:]

        return self._collect(int(arr.shape[0]))

    def _run(
        self, window: np.ndarray, settings: TransientSettings
    ) -> tuple[np.ndarray, np.ndarray]:
        """Run the engine over one window of the grid."""
        self._install_backgrounds()
        for background in self._backgrounds.values():
            background.begin(_STEP)
        return self.engine.process(window, settings)

    def _collect(self, count: int) -> tuple[np.ndarray, np.ndarray]:
        """Take ``count`` samples out of the two lines.

        The line starts with the latency of the module, so the output is late
        by exactly that much whatever the size of the blocks the host hands
        over.
        """
        if not self._started_output:
            self._started_output = True
            pad = np.zeros(
                (self.latency_samples, self._channels), dtype=np.float64
            )
            self._fifo_repaired = np.concatenate([pad, self._fifo_repaired], axis=0)
            self._fifo_removed = np.concatenate([pad, self._fifo_removed], axis=0)
        take = min(count, int(self._fifo_repaired.shape[0]))
        repaired = self._fifo_repaired[:take]
        removed = self._fifo_removed[:take]
        self._fifo_repaired = self._fifo_repaired[take:]
        self._fifo_removed = self._fifo_removed[take:]
        if take < count:
            pad = np.zeros((count - take, repaired.shape[1]), dtype=np.float64)
            repaired = np.concatenate([repaired, pad], axis=0)
            removed = np.concatenate([removed, pad], axis=0)
        return repaired, removed
