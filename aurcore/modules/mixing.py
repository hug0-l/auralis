"""
aurcore.modules.mixing — the shared L/R matrix engine.

This module owns the *mid/side* (M/S) matrix engine that the whole
``ChannelOperations`` element family is built on:

* :func:`to_mid_side` / :func:`to_left_right` — the plain signal transforms.
* :class:`ChannelOps` — the engine itself: mid, side, a general 2x2 matrix
  and the ``keep_center`` / ``keep_sides`` partial extractors.
* :class:`Mixing` — the ``AUR_MIXING`` module that drives an L/R output mix
  matrix with the two output faders.

Because Azimuth, Center Extract and Phase share the same element they share
the same engine, so the matrix maths lives here exactly once.

The file also carries the small streaming spectral helpers
(:class:`SpectralStream`, :func:`stft`, :func:`istft`) that the spectral
modules of this family need.  They live here so that the shared engine stays
a single, dependency-free import (numpy only).

The stream hands its samples out in the order they arrived, at the rate the
caller asks for them, and publishes the delay it puts into the signal
(:attr:`SpectralStream.latency_samples`), so a module that uses it gets a
continuous stream and can declare the delay to its host.

Signal conventions
------------------
``mid = (L + R) / 2`` and ``side = (L - R) / 2`` so that

    L = mid + side,   R = mid - side

and the round trip ``to_left_right(to_mid_side(x))`` reproduces ``x`` bit for
bit.  A signal that is identical in both channels is pure *mid* (it is
centred), a signal that is opposite in the two channels is pure *side*.
"""
from __future__ import annotations

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = [
    "to_mid_side",
    "to_left_right",
    "ChannelOps",
    "SpectralStream",
    "stft",
    "istft",
    "Mixing",
]


# ---------------------------------------------------------------------------
# Mid / side helpers
# ---------------------------------------------------------------------------
def to_mid_side(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a stereo block into its mid and side components.

    Parameters
    ----------
    x:
        Audio block, shape ``(n, channels)`` with at least two channels.  A
        one dimensional array is treated as mono and duplicated.

    Returns
    -------
    (mid, side):
        Two ``(n,)`` float64 arrays.  ``mid`` is the shared (centred) part of
        the image, ``side`` the difference between the two channels.
    """
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = np.column_stack([a, a])
    if a.shape[1] < 2:
        raise ValueError("to_mid_side() needs at least two channels")
    left = a[:, 0]
    right = a[:, 1]
    return 0.5 * (left + right), 0.5 * (left - right)


def to_left_right(ms) -> tuple[np.ndarray, np.ndarray]:
    """Rebuild the left and right channels from a mid/side pair.

    The inverse of :func:`to_mid_side`.
    """
    mid, side = ms
    mid = np.asarray(mid, dtype=np.float64)
    side = np.asarray(side, dtype=np.float64)
    return mid + side, mid - side


# ---------------------------------------------------------------------------
# The shared engine
# ---------------------------------------------------------------------------
class ChannelOps:
    """Mid/side matrix engine shared by every ``ChannelOperations`` module.

    The engine holds no audio state: every operation is a pure function of the
    block it is given, which keeps the modules that use it realtime safe.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)

    # -- components ----------------------------------------------------------
    def mid(self, x: np.ndarray) -> np.ndarray:
        """Return the mid (centred) component of a stereo block."""
        return to_mid_side(x)[0]

    def side(self, x: np.ndarray) -> np.ndarray:
        """Return the side (difference) component of a stereo block."""
        return to_mid_side(x)[1]

    def fold_mono(self, x: np.ndarray) -> np.ndarray:
        """Fold a stereo block down to the mono bus (its mid component)."""
        return to_mid_side(x)[0]

    # -- matrices ------------------------------------------------------------
    def apply_matrix(self, x: np.ndarray, matrix) -> np.ndarray:
        """Apply a general 2x2 matrix to a stereo block.

        Parameters
        ----------
        x:
            Audio block, shape ``(n, 2)``.
        matrix:
            ``[[a, b], [c, d]]``; the left output is ``a*L + b*R`` and the
            right output is ``c*L + d*R``.

        Returns
        -------
        numpy.ndarray
            A new ``(n, 2)`` block.
        """
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = np.column_stack([a, a])
        m = np.asarray(matrix, dtype=np.float64).reshape(2, 2)
        left = m[0, 0] * a[:, 0] + m[0, 1] * a[:, 1]
        right = m[1, 0] * a[:, 0] + m[1, 1] * a[:, 1]
        return np.column_stack([left, right])

    def keep_center(self, x: np.ndarray, amount: float = 1.0,
                    side_amount: float | None = None) -> np.ndarray:
        """Scale the centre and the side paths of a stereo block.

        Parameters
        ----------
        x:
            Audio block, shape ``(n, 2)``.
        amount:
            Gain applied to the mid (centre) path, ``0`` .. ``1``.
        side_amount:
            Gain applied to the side path.  When omitted it is
            ``1 - amount``, which turns the call into a centre/side crossfade
            (``amount = 1`` leaves pure centre, ``amount = 0`` pure side).

        Returns
        -------
        numpy.ndarray
            A new ``(n, 2)`` block.
        """
        mid, side = to_mid_side(x)
        if side_amount is None:
            side_amount = 1.0 - amount
        left, right = to_left_right((mid * amount, side * side_amount))
        return np.column_stack([left, right])

    def keep_sides(self, x: np.ndarray, amount: float = 1.0,
                   center_amount: float | None = None) -> np.ndarray:
        """Scale the side and the centre paths of a stereo block.

        The mirror image of :meth:`keep_center`: ``amount`` is the gain of the
        side path and the centre path is scaled by ``1 - amount`` unless
        ``center_amount`` says otherwise.
        """
        mid, side = to_mid_side(x)
        if center_amount is None:
            center_amount = 1.0 - amount
        left, right = to_left_right((mid * center_amount, side * amount))
        return np.column_stack([left, right])

    def mono_fold_matrix(self) -> np.ndarray:
        """Return the 2x2 matrix that folds a stereo block to mono."""
        return np.array([[0.5, 0.5], [0.5, 0.5]], dtype=np.float64)


# ---------------------------------------------------------------------------
# Shared spectral helpers
# ---------------------------------------------------------------------------
def _periodic_hann(n: int) -> np.ndarray:
    """Return a periodic Hann window of length ``n``."""
    n = int(n)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=np.float64) / n)


def stft(x: np.ndarray, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """Short time Fourier transform of a whole block of audio.

    Parameters
    ----------
    x:
        Audio block, shape ``(n, channels)``.
    n_fft:
        Transform size in samples.
    hop:
        Hop size in samples.

    Returns
    -------
    numpy.ndarray
        Complex array of shape ``(n_frames, n_fft // 2 + 1, channels)``.  The
        block is zero padded at the end so that no samples are lost.
    """
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    n = a.shape[0]
    if n < n_fft:
        a = np.pad(a, ((0, n_fft - n), (0, 0)))
        n = n_fft
    win = _periodic_hann(n_fft)
    n_frames = 1 + (n - n_fft) // hop
    idx = hop * np.arange(n_frames)[:, None] + np.arange(n_fft)[None, :]
    frames = a[idx] * win[:, None]
    return np.fft.rfft(frames, axis=1)


def istft(spec: np.ndarray, n_fft: int = 1024, hop: int = 256,
          length: int | None = None) -> np.ndarray:
    """Inverse short time Fourier transform (overlap-add with a Hann window).

    Parameters
    ----------
    spec:
        Complex spectrogram of shape ``(n_frames, n_fft // 2 + 1, channels)``.
    n_fft, hop:
        Transform and hop size used for the analysis.
    length:
        Optional number of samples to return; the result is truncated or zero
        padded to that length.

    Returns
    -------
    numpy.ndarray
        The reconstructed block, shape ``(length or out_len, channels)``.
    """
    s = np.asarray(spec)
    n_frames = s.shape[0]
    win = _periodic_hann(n_fft)
    frames = np.fft.irfft(s, n=n_fft, axis=1) * win[:, None]
    out_len = (n_frames - 1) * hop + n_fft
    channels = frames.shape[2]
    out = np.zeros((out_len, channels), dtype=np.float64)
    wsum = np.zeros(out_len, dtype=np.float64)
    w2 = win ** 2
    for k in range(n_frames):
        out[k * hop:k * hop + n_fft] += frames[k]
        wsum[k * hop:k * hop + n_fft] += w2
    good = wsum > 1e-8
    out[good] /= wsum[good, None]
    if length is not None:
        if out.shape[0] >= length:
            out = out[:length]
        else:
            out = np.pad(out, ((0, length - out.shape[0]), (0, 0)))
    return out


class SpectralStream:
    """Streaming STFT analyser with overlap-add synthesis.

    The stream keeps the analysis history between calls, so a module can
    process an arbitrarily long signal block by block with a constant
    memory footprint.  The synthesised output lags the input by
    :attr:`latency_samples` samples, and the stream publishes that number
    so that a module that uses it can declare it to its host.

    The order of the samples
    ------------------------
    The stream hands its samples out **in the order they arrived**, at the
    rate the caller asks for them.  A caller asks for the number of
    samples it has to give its host and gets them, whether a frame was
    ready or not: the samples that are ready but not needed yet wait in a
    line of the stream's own.  A stream that only handed its samples out
    when a frame was ready forced every module that used it to keep a line
    of its own, and the size of that line depended on the block size the
    host happened to use.

    The memory
    ----------
    The analysis history and the overlap-add accumulator hold a window of
    the analysis and not the length of the block.  The frames of a block
    are added to the accumulator in groups that fit into it, so a block of
    any length is accepted and the state the stream keeps between two
    calls does not depend on it.  The line holds the samples the caller
    has not taken yet: the difference between the rate the analysis
    produces them and the rate the caller asks for them.

    Parameters
    ----------
    n_fft:
        Transform size in samples.
    hop:
        Hop size in samples (must divide ``n_fft`` for a constant window sum).
    channels:
        Number of audio channels carried by the stream.
    """

    def __init__(self, n_fft: int = 1024, hop: int = 256, channels: int = 2):
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self.channels = int(channels)
        self.win = _periodic_hann(self.n_fft)
        self._w2 = self.win ** 2
        # The delay the stream puts into the signal.  A frame is only final
        # when the samples that follow it have arrived, so the output of
        # the stream is the input delayed by one window.  For every hop
        # that divides the half of the window this is the delay a centred
        # analysis of the same window needs.
        self._latency = int(self.n_fft)
        # The analysis history: the samples the frames that have not been
        # transformed yet still need.
        self._keep = self.n_fft + self.hop
        self._tail = np.zeros((self.n_fft, self.channels), dtype=np.float64)
        self._tail_pos = 0
        # The overlap-add accumulator and the line of the samples that are
        # ready but not needed yet.
        self._group = max(1, self.n_fft // self.hop)
        self._acc_size = self.n_fft + self._group * self.hop
        self._acc = np.zeros((self._acc_size, self.channels), dtype=np.float64)
        self._acc_w = np.zeros(self._acc_size, dtype=np.float64)
        self._acc_pos = 0
        self._acc_fill = 0
        self._pending = np.zeros((0, self.channels), dtype=np.float64)
        self._complete_end = 0

    # -- the delay ------------------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """The delay the stream puts into the signal, in samples.

        The number is published so that a module that uses the stream can
        declare it to its host: the analysis needs the samples that follow
        a frame, so the output of the stream is the input delayed by one
        window.
        """
        return int(self._latency)

    # -- analysis ------------------------------------------------------------
    def analyze(self, x: np.ndarray):
        """Consume a block of audio and return its spectra.

        Parameters
        ----------
        x:
            Audio block, shape ``(n, channels)``.

        Returns
        -------
        (spec, first_position) or (None, None)
            ``spec`` has shape ``(n_frames, n_fft // 2 + 1, channels)`` and
            ``first_position`` is the global sample index of its first frame.
            ``(None, None)`` is returned when the block is too short to
            produce a complete frame.
        """
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return None, None
        buf = np.concatenate([self._tail, a], axis=0)
        n_frames = (buf.shape[0] - self.n_fft) // self.hop + 1
        if n_frames < 1:
            # The history is copied, so the buffer the host handed over is
            # not kept alive by the stream: the state does not grow with
            # the length of the block.
            self._tail = buf.copy()
            return None, None
        idx = self.hop * np.arange(n_frames)[:, None] + np.arange(self.n_fft)[None, :]
        frames = buf[idx] * self.win[:, None]
        spec = np.fft.rfft(frames, axis=1)
        consumed = n_frames * self.hop
        first_position = self._tail_pos
        self._tail = buf[consumed:].copy()
        self._tail_pos += consumed
        if self._tail.shape[0] > self._keep:
            drop = self._tail.shape[0] - self._keep
            self._tail = self._tail[drop:].copy()
            self._tail_pos += drop
        return spec, first_position

    # -- synthesis -----------------------------------------------------------
    def _ola_add(self, frame: np.ndarray, f0: int) -> None:
        """Overlap-add one time domain frame at the global position ``f0``."""
        off = int(f0) - self._acc_pos
        if off < 0:
            # the frame reaches back before the accumulator starts: shift it
            shift = -off
            self._acc = np.concatenate(
                [np.zeros((shift, self.channels)), self._acc], axis=0
            )
            self._acc_w = np.concatenate([np.zeros(shift), self._acc_w])
            self._acc_pos -= shift
            off = 0
        need = off + self.n_fft
        if need > self._acc.shape[0]:
            grow = need - self._acc.shape[0]
            self._acc = np.concatenate(
                [self._acc, np.zeros((grow, self.channels))], axis=0
            )
            self._acc_w = np.concatenate([self._acc_w, np.zeros(grow)])
        if need > self._acc_fill:
            # the slots that were used before hold the tail of the frames
            # that are already in: they are cleared before they are used
            # again, so the residue of an old frame cannot leak into the
            # samples of a new one.
            self._acc[self._acc_fill:need] = 0.0
            self._acc_w[self._acc_fill:need] = 0.0
            self._acc_fill = need
        self._acc[off:need] += frame
        self._acc_w[off:need] += self._w2

    def _extract(self, start: int, end: int) -> np.ndarray:
        """Return the normalised samples for the global positions ``[start, end)``."""
        i0 = int(start) - self._acc_pos
        i1 = int(end) - self._acc_pos
        length = max(0, i1 - i0)
        seg = np.zeros((length, self.channels), dtype=np.float64)
        w = np.zeros(length, dtype=np.float64)
        # never let an exception escape the module: the part of the range
        # that the accumulator does not hold is left at zero
        lo = max(0, i0)
        hi = min(self._acc_fill, i1)
        if hi > lo:
            seg[lo - i0:hi - i0] = self._acc[lo:hi]
            w[lo - i0:hi - i0] = self._acc_w[lo:hi]
        good = w > 0.05
        seg[~good] = 0.0
        seg[good] /= w[good, None]
        drop = min(self._acc_fill, max(0, i1))
        if drop:
            self._acc_pos += drop
            self._acc_fill -= drop
            self._acc[: self._acc_fill] = self._acc[drop : drop + self._acc_fill].copy()
            self._acc_w[: self._acc_fill] = self._acc_w[drop : drop + self._acc_fill].copy()
        return seg

    def synthesize(self, spec: np.ndarray, first_position: int, out_len: int) -> np.ndarray:
        """Overlap-add the (modified) spectra and return ``out_len`` samples.

        The samples are handed out **in the order they arrived**: the ones
        that are ready but not needed yet wait in a line of the stream's
        own, and the caller takes them at the rate it needs them.  ``spec``
        may be ``None``, in which case the stream only hands out the
        samples it already has.  The samples the analysis has not produced
        yet come back as zeros: they are the look ahead of the analysis.

        The returned block lags the analysed block by
        :attr:`latency_samples` samples.
        """
        out_len = int(out_len)
        if spec is not None:
            frames = np.fft.irfft(spec, n=self.n_fft, axis=1) * self.win[:, None]
            n_frames = int(frames.shape[0])
            start = 0
            while start < n_frames:
                # The frames are added to the accumulator in groups that fit
                # into it, so the accumulator does not grow with the length
                # of the block the caller hands over.
                take = min(self._group, n_frames - start)
                for k in range(start, start + take):
                    self._ola_add(frames[k], int(first_position) + k * self.hop)
                chunk_end = (
                    int(first_position) + (start + take - 1) * self.hop + self.hop
                )
                if chunk_end > self._complete_end:
                    chunk = self._extract(self._complete_end, chunk_end)
                    if chunk.shape[0]:
                        self._pending = np.concatenate([self._pending, chunk], axis=0)
                    self._complete_end = chunk_end
                start += take
        return self._take(out_len)

    def _take(self, count: int) -> np.ndarray:
        """Take ``count`` samples out of the line, in the order they arrived."""
        count = max(0, int(count))
        out = np.zeros((count, self.channels), dtype=np.float64)
        take = min(count, int(self._pending.shape[0]))
        if take:
            out[:take] = self._pending[:take]
            self._pending = self._pending[take:]
        return out

    def process(self, x: np.ndarray, modify=None) -> np.ndarray:
        """Analyse a block, edit it and hand back the same number of samples.

        The samples come back **in the order they went in**, delayed by
        :attr:`latency_samples`.  A module that uses this method gets a
        continuous stream and does not have to keep a line of its own.

        Parameters
        ----------
        x:
            Audio block, shape ``(n, channels)``.
        modify:
            A callable that is given the spectra of the new frames and
            returns the edited ones.  ``None`` leaves the spectra alone.

        Returns
        -------
        numpy.ndarray
            A block of ``n`` samples, shape ``(n, channels)``.
        """
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        spec, first = self.analyze(a)
        if spec is not None and modify is not None:
            spec = modify(spec)
        return self.synthesize(spec, first, a.shape[0])

    def flush(self) -> np.ndarray:
        """Return the samples that are still buffered and clear the stream."""
        out = self._pending
        self._pending = np.zeros((0, self.channels), dtype=np.float64)
        return out

    def reset(self) -> None:
        """Drop all internal state."""
        self._tail = np.zeros((self.n_fft, self.channels), dtype=np.float64)
        self._tail_pos = 0
        self._acc = np.zeros((self._acc_size, self.channels), dtype=np.float64)
        self._acc_w = np.zeros(self._acc_size, dtype=np.float64)
        self._acc_pos = 0
        self._acc_fill = 0
        self._pending = np.zeros((0, self.channels), dtype=np.float64)
        self._complete_end = 0


# ---------------------------------------------------------------------------
# The Mixing module
# ---------------------------------------------------------------------------
@register
class Mixing(Module):
    """Mixing — L/R output mixer built on the shared mid/side matrix.

    ``Left Output Mix`` and ``Right Output Mix`` are linear output faders
    (100 % = unity, 200 % = +6 dB).  They drive an L/R matrix that is
    expressed in the mid/side domain:

    ==============  ====================================================
    ``gM``          the shared mono bus, ``0.5 * (gl + gr)``
    ``gSL``/``gSR`` the side feed of the left/right bus, ``gl``/``gr``
    ==============  ====================================================

    with ``gl``/``gr`` the two fader positions in percent.  The resulting
    matrix is ``[[0.75*gl + 0.25*gr, 0.25*gr - 0.25*gl],
    [0.25*gl - 0.25*gr, 0.25*gl + 0.75*gr]]``.

    Consequences worth knowing:

    * 100 % / 100 % reproduces the input exactly and 200 % / 200 % is
      +6 dB on both buses.
    * The mono (mid) bus is *shared* by the two outputs.  That shared bus is
      the module's mono folding path: a centred source stays present in both
      buses and a hard panned source folds into the opposite bus.  Pulling
      one fader to zero therefore mutes that bus' side feed but not the
      shared mono bus - set both faders to 0 % for a completely silent
      output.
    """

    code = "AUR_MIXING"
    display_name = "Mixing"
    category = "Utility"
    edition = "standard"
    element = "ChannelOperations"
    analysis = False
    supports_learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._ops = ChannelOps(sample_rate, channels)

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Left Output Mix",
                type="float",
                default=100.0,
                minimum=0.0,
                maximum=200.0,
                unit="%",
                group="Mix",
                help="Level of the left output bus (100 % = unity, 200 % = +6 dB).",
            ),
            desc(
                name="Right Output Mix",
                type="float",
                default=100.0,
                minimum=0.0,
                maximum=200.0,
                unit="%",
                group="Mix",
                help="Level of the right output bus (100 % = unity, 200 % = +6 dB).",
            ),
        ]

    # -- matrix ---------------------------------------------------------------
    def matrix(self) -> np.ndarray:
        """Return the 2x2 L/R matrix currently driven by the two faders."""
        gl = float(self.params.get("Left Output Mix")) / 100.0
        gr = float(self.params.get("Right Output Mix")) / 100.0
        return np.array(
            [
                [0.75 * gl + 0.25 * gr, 0.25 * gr - 0.25 * gl],
                [0.25 * gl - 0.25 * gr, 0.25 * gl + 0.75 * gr],
            ],
            dtype=np.float64,
        )

    def fold_to_mono(self, x: np.ndarray) -> np.ndarray:
        """Return the mono bus (the mid component) of a stereo block."""
        return self._ops.fold_mono(x)

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the L/R output mix matrix to a block of audio."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0 or a.shape[1] < 2:
            return a
        return self._ops.apply_matrix(a, self.matrix())
