"""
aurcore.dsp.stft — short-time Fourier transform engine.

The engine is *streaming capable*: :meth:`STFT.analyze` and
:meth:`STFT.synthesize` work on one frame at a time, so a block processor can
feed them frame by frame without ever holding the whole signal.

Shapes
------
* A frame is a 1-D ``float64`` array of at most ``n_fft`` samples.
* A magnitude or phase vector has ``n_fft // 2 + 1`` entries (DC .. Nyquist).
* A spectrogram is ``(n_bins, n_frames)`` with ``n_bins = n_fft // 2 + 1``;
  for a multi-channel signal it is ``(n_channels, n_bins, n_frames)``.

Normalisation
-------------
Analysis and synthesis both apply a window, so the overlap-added signal is
scaled by the *overlap sum* of the two windows.  That sum is only constant
for special window pairs, therefore:

* ``norm="exact"`` (default) divides by the **per-sample** overlap sum.  It
  is exact for every window and every overlap, and it is the mode that makes
  the round trip lossless.
* ``norm="cola"`` divides by the constant :func:`aurcore.dsp.windows.colA`
  value.  It is only correct for a genuinely COLA pair, e.g. a Hann analysis
  window with a rectangular synthesis window.
"""
from __future__ import annotations

import math
from typing import Iterator

import numpy as np

from .fft import irfft, magnitude_db, phase, rfft, to_complex
from .util import bin_frequencies
from .windows import window as make_window

__all__ = [
    "STFT",
    "StreamingSTFT",
    "Spectrogram",
    "stft",
    "istft",
    "spectrogram_db",
    "magnitude_spectrogram",
]


def _resolve_hop(n_fft: int, hop: int | None, overlap_factor) -> int:
    """Return the hop size, deriving it from the overlap factor if needed."""
    if hop is not None:
        hop = int(hop)
        if hop <= 0:
            raise ValueError("hop must be >= 1")
        return hop
    factor = int(overlap_factor) if overlap_factor else 2
    if factor < 1:
        raise ValueError("overlap_factor must be >= 1")
    return max(1, n_fft // factor)


def _plan(n_samples: int, n_fft: int, hop: int, center: bool) -> tuple[int, int, int]:
    """Framing plan.  Returns ``(n_frames, pad_left, padded_length)``."""
    n_samples = int(n_samples)
    if n_samples <= 0:
        return 0, 0, 0
    if center:
        pad_left = n_fft // 2
        n_frames = 1 + int(math.ceil(n_samples / hop)) if n_samples > 0 else 1
        needed = (n_frames - 1) * hop + n_fft
        padded = max(n_samples + 2 * pad_left, needed)
        return n_frames, pad_left, padded
    if n_samples < n_fft:
        return 1, 0, n_fft
    n_frames = 1 + (n_samples - n_fft) // hop
    return int(n_frames), 0, n_samples


def _pad(arr: np.ndarray, pad_left: int, padded_len: int) -> np.ndarray:
    """Place ``arr`` (n, ch) inside a zero array of length ``padded_len``."""
    n = arr.shape[0]
    out = np.zeros((padded_len,) + arr.shape[1:], dtype=np.float64)
    if n:
        out[pad_left : pad_left + n] = arr
    return out


def _window_sum(n_fft: int, hop: int, n_frames: int, product: np.ndarray) -> np.ndarray:
    """Per-sample overlap sum of a window product, length ``(n_frames-1)*hop+n_fft``."""
    total = (n_frames - 1) * hop + n_fft
    acc = np.zeros(total, dtype=np.float64)
    for f in range(n_frames):
        start = f * hop
        acc[start : start + n_fft] += product
    return acc


def _cola_constant(n_fft: int, hop: int, wa: np.ndarray, ws: np.ndarray) -> float:
    """Mean over residue classes of the overlap sum of the window product."""
    product = np.asarray(wa, dtype=np.float64) * np.asarray(ws, dtype=np.float64)
    hop = max(1, int(hop))
    vals = [float(np.sum(product[r::hop])) for r in range(hop)]
    return float(np.mean(vals)) if vals else 0.0


def _as_2d(x) -> tuple[np.ndarray, bool]:
    """Return ``(2-D view, was_mono)``."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        return arr[:, None], True
    if arr.ndim == 2:
        return arr, False
    raise ValueError("signal must be 1-D (n,) or 2-D (n, channels)")


class STFT:
    """Short-time Fourier transform engine for one window / hop configuration.

    Parameters
    ----------
    fft_size : int
        Transform size in samples (the window length).
    hop : int, optional
        Hop between frames in samples.  When omitted it is derived from
        ``overlap_factor`` as ``fft_size // overlap_factor``.
    window : str or numpy.ndarray
        Analysis/synthesis window name (see
        :func:`aurcore.dsp.windows.window`) or an explicit window array.
    overlap_factor : int
        Overlap factor used to derive the hop: ``2`` = 50 % overlap,
        ``4`` = 75 % overlap.  Ignored when ``hop`` is given.
    **window_kw
        Extra window arguments, e.g. ``beta=12.0`` for Kaiser.

    Attributes
    ----------
    n_fft : int
        Transform size in samples.
    hop : int
        Hop size in samples.
    win : numpy.ndarray
        The analysis window actually used, shape ``(n_fft,)``.
    syn_win : numpy.ndarray
        The synthesis window, shape ``(n_fft,)``.  Equal to ``win`` unless a
        different synthesis window is requested.
    n_bins : int
        Number of frequency bins, ``n_fft // 2 + 1``.
    """

    def __init__(
        self,
        fft_size: int = 1024,
        hop: int | None = None,
        window: str | np.ndarray = "hann",
        overlap_factor: int = 2,
        **window_kw,
    ):
        self.n_fft = int(fft_size)
        if self.n_fft < 2:
            raise ValueError("fft_size must be >= 2")
        self.hop = _resolve_hop(self.n_fft, hop, overlap_factor)
        self.overlap_factor = int(overlap_factor) if overlap_factor else 2
        self.window_name = window if isinstance(window, str) else "custom"
        self.window_kw = dict(window_kw)
        self.win = np.asarray(make_window(window, self.n_fft, **window_kw), dtype=np.float64)
        self.syn_win = self.win
        self.n_bins = self.n_fft // 2 + 1
        # The overlap sum of the window product is a property of the framing
        # and not of the signal, so it is built once per framing and reused.
        self._denom: np.ndarray | None = None
        self._denom_frames = -1

    # -- single frame --------------------------------------------------------
    def analyze(self, frame) -> tuple[np.ndarray, np.ndarray]:
        """Analyse one frame.

        Parameters
        ----------
        frame : array_like
            Frame samples, length <= ``n_fft``.  Shorter frames are
            zero-padded, longer frames are truncated.

        Returns
        -------
        (numpy.ndarray, numpy.ndarray)
            ``(mag, phase)``, each of length ``n_fft // 2 + 1``.  ``mag`` is a
            linear magnitude, ``phase`` is in radians.
        """
        data = np.asarray(frame, dtype=np.float64).ravel()
        if data.shape[0] > self.n_fft:
            data = data[: self.n_fft]
        buf = np.zeros(self.n_fft, dtype=np.float64)
        buf[: data.shape[0]] = data
        spec = rfft(buf * self.win, n=self.n_fft)
        return np.abs(spec), phase(spec)

    def synthesize(self, mag, phase_) -> np.ndarray:
        """Rebuild one frame from a magnitude and a phase.

        The returned frame is already multiplied by the synthesis window, so
        it can be overlap-added directly.  The analysis window is undone
        exactly: the inverse transform of the analysed spectrum is the
        windowed frame.

        Parameters
        ----------
        mag : array_like
            Magnitude, length ``n_fft // 2 + 1``.
        phase_ : array_like
            Phase in radians, length ``n_fft // 2 + 1``.

        Returns
        -------
        numpy.ndarray
            The windowed frame, length ``n_fft``.
        """
        spec = to_complex(mag, phase_)
        frame = irfft(spec, n=self.n_fft)
        return frame * self.syn_win

    # -- whole blocks --------------------------------------------------------
    def analyze_block(self, x, center: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """Analyse a whole signal at once.

        Parameters
        ----------
        x : array_like
            Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
        center : bool
            Centre the frames (pad ``n_fft // 2`` samples on both sides).

        Returns
        -------
        (numpy.ndarray, numpy.ndarray)
            ``(mag, phase)``.  Mono: shape ``(n_bins, n_frames)``.
            Multi-channel: shape ``(n_channels, n_bins, n_frames)``.
        """
        arr, mono = _as_2d(x)
        spec = self._spectrogram(arr, center=center)
        if mono:
            spec = spec[0]
        return np.abs(spec), phase(spec)

    def analyze_magnitude_block(self, x, center: bool = True) -> np.ndarray:
        """Analyse a whole signal and return the magnitudes only.

        Same analysis as :meth:`analyze_block`, but the phase is not
        computed: a caller that only looks at the magnitudes does not pay
        for the phase, which is the second most expensive step of the
        analysis.

        Parameters
        ----------
        x : array_like
            Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
        center : bool
            Centre the frames (pad ``n_fft // 2`` samples on both sides).

        Returns
        -------
        numpy.ndarray
            The magnitudes.  Mono: shape ``(n_bins, n_frames)``.
            Multi-channel: shape ``(n_channels, n_bins, n_frames)``.
        """
        arr, mono = _as_2d(x)
        spec = self._spectrogram(arr, center=center)
        if mono:
            spec = spec[0]
        return np.abs(spec)

    def analyze_complex_block(self, x, center: bool = True) -> np.ndarray:
        """Analyse a whole signal and return the complex spectrogram.

        The complex spectrum is what a caller needs when it wants to change
        the magnitude of a bin (a gain, a mask): multiplying the complex
        spectrum by a real factor is much cheaper than rebuilding it from a
        magnitude and a phase, because rebuilding it needs an exponential
        per bin.

        Parameters
        ----------
        x : array_like
            Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
        center : bool
            Centre the frames (pad ``n_fft // 2`` samples on both sides).

        Returns
        -------
        numpy.ndarray
            The complex spectrum.  Mono: shape ``(n_bins, n_frames)``.
            Multi-channel: shape ``(n_channels, n_bins, n_frames)``.
        """
        arr, _ = _as_2d(x)
        return self._spectrogram(arr, center=center)

    def synthesize_block(
        self,
        mag,
        phase_,
        norm: str = "exact",
        length: int | None = None,
        center: bool = True,
    ) -> np.ndarray:
        """Rebuild a whole signal from a magnitude/phase spectrogram.

        Parameters
        ----------
        mag, phase_ : array_like
            Spectrogram components, shape ``(n_bins, n_frames)`` (mono) or
            ``(n_channels, n_bins, n_frames)``.
        norm : str
            ``"exact"`` (default) divides by the per-sample overlap sum;
            ``"cola"`` divides by the constant COLA factor.
        length : int, optional
            Exact output length in samples.
        center : bool
            True when the analysis was centred; the leading ``n_fft // 2``
            samples are removed from the result.

        Returns
        -------
        numpy.ndarray
            The reconstructed signal, same channel layout as the input.
        """
        spec = to_complex(mag, phase_)
        if spec.ndim == 2:
            out = self._istft(spec, norm=norm)
        else:
            out = np.stack([self._istft(spec[c], norm=norm) for c in range(spec.shape[0])], axis=1)
        if center:
            out = out[self.n_fft // 2 :]
        if length is not None:
            length = int(length)
            if out.shape[0] < length:
                extra = np.zeros((length - out.shape[0],) + out.shape[1:], dtype=np.float64)
                out = np.concatenate([out, extra], axis=0)
            else:
                out = out[:length]
        return out

    def synthesize_complex_block(
        self,
        spec,
        norm: str = "exact",
        length: int | None = None,
        center: bool = True,
        gain=None,
    ) -> np.ndarray:
        """Rebuild a whole signal from a complex spectrogram.

        Parameters
        ----------
        spec : array_like
            Complex spectrogram, shape ``(n_bins, n_frames)`` (mono) or
            ``(n_channels, n_bins, n_frames)``.
        norm : str
            ``"exact"`` (default) or ``"cola"``.
        length : int, optional
            Exact output length in samples.
        center : bool
            True when the analysis was centred; the leading ``n_fft // 2``
            samples are removed from the result.
        gain : array_like, optional
            A real factor per bin and per frame.  It is applied to the
            complex spectrum before the inverse transform, which is the
            cheap way to change the magnitude of a bin: no phase has to be
            taken, and no exponential has to be evaluated.

        Returns
        -------
        numpy.ndarray
            The reconstructed signal, same channel layout as the input.
        """
        arr = np.asarray(spec, dtype=np.complex128)
        if gain is not None:
            arr = arr * np.asarray(gain, dtype=np.float64)
        if arr.ndim == 2:
            out = self._istft(arr, norm=norm)
        else:
            out = np.stack(
                [self._istft(arr[c], norm=norm) for c in range(arr.shape[0])], axis=1
            )
        if center:
            out = out[self.n_fft // 2 :]
        if length is not None:
            length = int(length)
            if out.shape[0] < length:
                extra = np.zeros((length - out.shape[0],) + out.shape[1:], dtype=np.float64)
                out = np.concatenate([out, extra], axis=0)
            else:
                out = out[:length]
        return out

    # -- streaming helpers ---------------------------------------------------
    def streaming(self, **kw) -> "StreamingSTFT":
        """Return a streaming engine with this engine's configuration.

        The returned engine keeps its state between blocks, so it can be fed
        a stream of blocks instead of a whole signal.  It produces the same
        frames as this engine, so a module that analyses a block at a time
        can switch to it without changing what it computes.

        Parameters
        ----------
        **kw
            Overrides for the :class:`StreamingSTFT` constructor, e.g.
            ``center=False`` or ``norm="cola"``.

        Returns
        -------
        StreamingSTFT
            A new streaming engine.
        """
        kw.setdefault("center", True)
        kw.setdefault("norm", "exact")
        return StreamingSTFT(
            self.n_fft,
            self.hop,
            self.win,
            overlap_factor=self.overlap_factor,
            synthesis_window=self.syn_win,
            **kw,
        )

    def frames(self, x, center: bool = True) -> Iterator[np.ndarray]:
        """Iterate over the frames of a signal.

        Parameters
        ----------
        x : array_like
            Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
        center : bool
            Centre the frames (pad ``n_fft // 2`` samples on both sides).

        Yields
        ------
        numpy.ndarray
            One frame of length ``n_fft`` at a time.
        """
        arr, _ = _as_2d(x)
        n_frames, pad_left, padded_len = _plan(arr.shape[0], self.n_fft, self.hop, center)
        padded = _pad(arr, pad_left, padded_len)
        view = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft, axis=0)
        for f in range(n_frames):
            yield view[f * self.hop]

    def stream_analyze(self, x, center: bool = True):
        """Analyse a signal frame by frame.

        Parameters
        ----------
        x : array_like
            Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
        center : bool
            Centre the frames.

        Yields
        ------
        (numpy.ndarray, numpy.ndarray)
            ``(mag, phase)`` for each frame, each of length ``n_bins``.
        """
        for frame in self.frames(x, center=center):
            yield self.analyze(frame)

    # -- internals ------------------------------------------------------------
    def _denominator(self, n_frames: int) -> np.ndarray:
        """Per-sample overlap sum of the analysis/synthesis window product.

        The sum only depends on the window, the hop and the number of
        frames, so it is built once per framing instead of once per call.
        """
        if self._denom is None or self._denom_frames != n_frames:
            self._denom = _window_sum(self.n_fft, self.hop, n_frames, self.win * self.syn_win)
            self._denom_frames = n_frames
        return self._denom

    def _spectrogram(self, arr: np.ndarray, center: bool = True) -> np.ndarray:
        """Complex spectrogram of a 2-D (n, ch) array -> (ch, n_bins, n_frames)."""
        n_frames, pad_left, padded_len = _plan(arr.shape[0], self.n_fft, self.hop, center)
        if n_frames == 0:
            return np.zeros((arr.shape[1], self.n_bins, 0), dtype=np.complex128)
        padded = _pad(arr, pad_left, padded_len)
        # The window is applied frame by frame.  The overlapping framing is a
        # strided view of the padded signal, and multiplying that view by the
        # window reads the samples with a hop sized stride; multiplying one
        # contiguous frame after the other is several times faster and gives
        # bit identical results.
        frames = np.empty((n_frames, arr.shape[1], self.n_fft), dtype=np.float64)
        for f in range(n_frames):
            start = f * self.hop
            np.multiply(padded[start:start + self.n_fft].T, self.win, out=frames[f])
        spec = rfft(frames, n=self.n_fft, axis=-1)  # (n_frames, ch, n_bins)
        return np.transpose(spec, (1, 2, 0))  # (ch, n_bins, n_frames)

    def _istft(self, spec: np.ndarray, norm: str = "exact") -> np.ndarray:
        """Inverse STFT of a complex spectrogram (n_bins, n_frames)."""
        n_frames = spec.shape[1]
        if n_frames == 0:
            return np.zeros(0, dtype=np.float64)
        frames = irfft(spec.T, n=self.n_fft, axis=-1)  # (n_frames, n_fft)
        frames = frames * self.syn_win
        out = np.zeros((n_frames - 1) * self.hop + self.n_fft, dtype=np.float64)
        for f in range(n_frames):
            out[f * self.hop : f * self.hop + self.n_fft] += frames[f]
        if norm == "cola":
            denom = _cola_constant(self.n_fft, self.hop, self.win, self.syn_win)
            if denom:
                out = out / denom
        elif norm == "exact":
            den = self._denominator(n_frames)
            out = np.divide(out, den, out=np.zeros_like(out), where=den > 1e-12)
        else:
            raise ValueError(f"unknown norm {norm!r}")
        return out


class StreamingSTFT:
    """Stateful streaming short-time Fourier transform engine.

    The engine keeps the analysis history, the overlap-add accumulator and
    the frame counters between calls, so the work a call does depends only on
    the number of *new* samples and not on the size of the analysis window.
    A block of ``B`` samples costs ``B / hop`` transforms, where the
    whole-signal engine costs two or three transforms for the same block
    whatever the block size is.

    The stream is the stream the whole-signal engine sees: the frames sit on
    the same grid, the same window is applied and the normalisation is the
    same per-sample overlap sum.  A round trip through the streaming engine
    therefore reproduces the round trip of :meth:`STFT.analyze_block` and
    :meth:`STFT.synthesize_block` to the precision of the floating point
    arithmetic, and a per-frame spectral edit gives the same result in both
    paths.

    Block size
    ----------
    A block of any size is accepted.  The work of a block is split into
    groups of frames that fit into the overlap-add accumulator, and the
    output samples are handed out group by group, so neither the memory the
    engine needs nor the failure it may report depends on the size of the
    block the caller uses.

    Latency
    -------
    The centred analysis needs the samples on *both* sides of a frame, so the
    engine cannot emit an output sample before the last frame that covers it
    has been analysed.  The first output sample is available after
    :attr:`latency_samples` input samples; the engine emits the output
    samples as they become ready, so a caller that wants a fixed block size
    in and out should use :meth:`process`.

    Examples
    --------
    >>> engine = StreamingSTFT(1024, 256, window="hann")
    >>> for block in blocks:
    ...     mag, phase = engine.analyze(block)
    ...     out = engine.synthesize(mag, phase)
    >>> tail = engine.flush_synthesis()      # doctest: +SKIP
    """

    def __init__(
        self,
        fft_size: int = 1024,
        hop: int | None = None,
        window: str | np.ndarray = "hann",
        overlap_factor: int = 2,
        center: bool = True,
        norm: str = "exact",
        synthesis_window: str | np.ndarray | None = None,
        outputs: int = 1,
        **window_kw,
    ):
        self.n_fft = int(fft_size)
        if self.n_fft < 2:
            raise ValueError("fft_size must be >= 2")
        self.hop = _resolve_hop(self.n_fft, hop, overlap_factor)
        self.overlap_factor = int(overlap_factor) if overlap_factor else 2
        self.window_name = window if isinstance(window, str) else "custom"
        self.window_kw = dict(window_kw)
        self.win = np.asarray(make_window(window, self.n_fft, **window_kw), dtype=np.float64)
        if synthesis_window is None:
            self.syn_win = self.win
        else:
            self.syn_win = np.asarray(
                make_window(synthesis_window, self.n_fft, **self.window_kw), dtype=np.float64
            )
        self.n_bins = self.n_fft // 2 + 1
        self.outputs = int(outputs)
        if self.outputs < 1:
            raise ValueError("outputs must be >= 1")
        self.center = bool(center)
        self.half = (self.n_fft // 2) if self.center else 0
        self.norm = str(norm)
        if self.norm not in ("exact", "cola"):
            raise ValueError(f"unknown norm {self.norm!r}")
        self._product = self.win * self.syn_win
        self._cola = _cola_constant(self.n_fft, self.hop, self.win, self.syn_win)
        # The window is applied along the sample axis, which is the first
        # axis of the (n_fft, channels) frames of the streaming buffers.
        self._win_col = self.win[:, None]
        self._syn_col = self.syn_win[:, None]
        # The analysis history must still hold the oldest frame that has not
        # been transformed yet: a frame window reaches ``hop`` samples back
        # from the frame that follows it.
        self._keep = self.n_fft + 2 * self.hop + 1
        self._chunk = max(1, min(self.hop, self._keep - self.n_fft - self.hop + 1))
        # The overlap-add accumulator is addressed by the global position
        # modulo its size, so its size has to be a power of two.
        size = 1
        while size < self.n_fft + 4 * self.hop + 16:
            size <<= 1
        self._acc_size = size
        self._mask = size - 1
        # How many frames the accumulator holds at the same time.  The frames
        # of a block are synthesised in groups of this size, so a block of any
        # length is accepted and the memory of the engine does not depend on
        # the length of the block.
        self._group = max(1, (self._acc_size - self.n_fft) // self.hop)
        self.reset()

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Forget the history, the accumulator and the frame counters."""
        self._seen = 0        # input samples received, padding included
        self._samples = 0      # input samples received, padding excluded
        self._frame = 0        # next frame to analyse
        self._syn_frame = 0    # next frame to synthesise
        self._out_pos = 0      # next output sample to emit
        self._channels = 0
        self._mono = True
        self._ring = None      # (keep, channels) analysis history
        self._ring_head = 0    # ring slot of the oldest sample
        self._base = 0         # global position of the oldest sample
        self._acc = None       # (outputs, acc_size, channels) overlap-add numerator
        self._den = None       # (acc_size,) overlap-add denominator
        self._fifos = [None for _ in range(self.outputs)]  # output FIFOs

    @property
    def latency_samples(self) -> int:
        """Input samples needed before the first output sample is ready."""
        first = self.half // self.hop
        return int(first * self.hop + self.n_fft - self.half)

    @property
    def frames_pending(self) -> int:
        """Number of analysed frames that have not been synthesised yet."""
        return int(self._frame - self._syn_frame)

    @property
    def output_position(self) -> int:
        """Global index of the next output sample."""
        return int(self._out_pos)

    # -- buffers -------------------------------------------------------------
    def _allocate(self, channels: int) -> None:
        """Create the buffers for a channel count."""
        if self._ring is not None and self._channels == int(channels):
            return
        self._channels = int(channels)
        self._ring = np.zeros((self._keep, self._channels), dtype=np.float64)
        self._ring_head = 0
        self._base = -self._keep
        self._acc = np.zeros(
            (self.outputs, self._acc_size, self._channels), dtype=np.float64
        )
        self._den = np.zeros((self._acc_size,), dtype=np.float64)

    def _push(self, chunk: np.ndarray) -> None:
        """Append samples to the analysis history."""
        keep = self._keep
        n = int(chunk.shape[0])
        if n >= keep:
            self._ring[:] = chunk[n - keep :]
            self._ring_head = 0
        else:
            # The new samples overwrite the oldest ones, so they are written
            # over the slots that hold them and the head moves forward.
            start = self._ring_head
            first = min(n, keep - start)
            self._ring[start : start + first] = chunk[:first]
            if first < n:
                self._ring[: n - first] = chunk[first:]
            self._ring_head = (start + n) % keep
        self._seen += n
        self._base = self._seen - keep

    def _window(self, g: int) -> np.ndarray:
        """The ``n_fft`` samples that start at the global position ``g``."""
        keep = self._keep
        off = (self._ring_head + (g - self._base)) % keep
        if off + self.n_fft <= keep:
            return self._ring[off : off + self.n_fft]
        first = keep - off
        return np.concatenate((self._ring[off:], self._ring[: self.n_fft - first]))

    def _add(self, buf: np.ndarray, g: int, values: np.ndarray) -> None:
        """Add ``values`` at the global position ``g``."""
        size = buf.shape[0]
        off = g & self._mask
        n = int(values.shape[0])
        first = min(n, size - off)
        buf[off : off + first] += values[:first]
        if first < n:
            buf[: n - first] += values[first:]

    def _clear(self, buf: np.ndarray, g: int, n: int) -> None:
        """Zero the ``n`` entries that start at the global position ``g``."""
        if n <= 0:
            return
        size = buf.shape[0]
        off = g & self._mask
        first = min(n, size - off)
        buf[off : off + first] = 0.0
        if first < n:
            buf[: n - first] = 0.0

    def _gather(self, buf: np.ndarray, g: int, n: int) -> np.ndarray:
        """The ``n`` entries that start at the global position ``g``."""
        size = buf.shape[0]
        off = g & self._mask
        first = min(n, size - off)
        if first == n:
            return buf[off : off + n].copy()
        return np.concatenate((buf[off:], buf[: n - first]))

    # -- analysis ------------------------------------------------------------
    def _ready(self):
        """Transform the frames whose window is completely available."""
        f_hi = (self._seen + self.half - self.n_fft) // self.hop
        if f_hi < self._frame:
            return None
        count = int(f_hi - self._frame + 1)
        g0 = self._frame * self.hop - self.half
        if count == 1:
            frame = self._window(g0) * self._win_col
            spec = rfft(frame, n=self.n_fft, axis=0)
            spec = spec[None, ...]
        else:
            stack = np.stack(
                [self._window(g0 + i * self.hop) for i in range(count)], axis=0
            )
            spec = rfft(stack * self._win_col, n=self.n_fft, axis=1)
        self._frame = int(f_hi) + 1
        return spec

    def analyze(self, block) -> tuple[np.ndarray, np.ndarray]:
        """Analyse the frames that the new samples complete.

        Parameters
        ----------
        block : array_like
            New samples, shape ``(n,)`` or ``(n, channels)``.

        Returns
        -------
        (numpy.ndarray, numpy.ndarray)
            ``(mag, phase)`` for the new frames.  Mono: shape
            ``(n_bins, n_frames)``.  Multi-channel: shape
            ``(channels, n_bins, n_frames)``.  The number of frames is
            ``0`` when the block does not complete a frame.
        """
        arr, mono = _as_2d(block)
        n = int(arr.shape[0])
        if n == 0:
            return self._empty(mono)
        if self._channels and int(arr.shape[1]) != self._channels:
            raise ValueError("the channel count cannot change inside a stream")
        self._allocate(arr.shape[1])
        self._mono = bool(mono)
        specs = []
        pos = 0
        while pos < n:
            take = min(self._chunk, n - pos)
            self._push(arr[pos : pos + take])
            pos += take
            spec = self._ready()
            if spec is not None:
                specs.append(spec)
        self._samples += n
        if not specs:
            return self._empty(mono)
        spec = specs[0] if len(specs) == 1 else np.concatenate(specs, axis=0)
        return self._pack(spec, mono)

    def _pack(self, spec: np.ndarray, mono: bool) -> tuple[np.ndarray, np.ndarray]:
        """Turn a (n_frames, n_bins, channels) spectrum into the public shape."""
        mag = np.abs(spec)
        phase_ = np.angle(spec)
        if mono:
            return mag[:, :, 0].T.copy(), phase_[:, :, 0].T.copy()
        return np.transpose(mag, (2, 1, 0)), np.transpose(phase_, (2, 1, 0))

    def _empty(self, mono: bool) -> tuple[np.ndarray, np.ndarray]:
        """An empty spectrogram of the right shape."""
        if mono:
            return np.zeros((self.n_bins, 0)), np.zeros((self.n_bins, 0))
        return (
            np.zeros((self._channels, self.n_bins, 0)),
            np.zeros((self._channels, self.n_bins, 0)),
        )

    # -- synthesis -----------------------------------------------------------
    def synthesize_many(self, mags, phase_) -> list[np.ndarray]:
        """Rebuild the output samples the analysed frames complete.

        The frames must be the ones the last :meth:`analyze` call returned,
        in the same order; the engine refuses to run out of step.

        Parameters
        ----------
        mags :
            The magnitudes of the new frames, one array per output signal,
            shaped ``(n_bins, n_frames)`` (mono) or
            ``(channels, n_bins, n_frames)``.
        phase_ : array_like
            The phase of the new frames, the same shape as the magnitudes.

        Returns
        -------
        list of numpy.ndarray
            The output samples that are ready, one array per output signal.
            Mono: shape ``(n,)``; multi-channel: shape ``(n, channels)``.
        """
        if not isinstance(mags, (list, tuple)):
            mags = [mags]
        if len(mags) != self.outputs:
            raise ValueError(
                f"the stream has {self.outputs} outputs, {len(mags)} were given"
            )
        p = np.asarray(phase_, dtype=np.float64)
        specs = []
        count = 0
        for mag in mags:
            m = np.asarray(mag, dtype=np.float64)
            if m.shape != p.shape:
                raise ValueError("mag and phase must have the same shape")
            if m.ndim == 2:
                spec = to_complex(m.T[:, :, None], p.T[:, :, None])
            elif m.ndim == 3:
                spec = to_complex(np.transpose(m, (2, 1, 0)), np.transpose(p, (2, 1, 0)))
            else:
                raise ValueError(
                    "mag must be 2-D (n_bins, n_frames) or 3-D (ch, n_bins, n_frames)"
                )
            specs.append(spec)
            count = int(spec.shape[0])
        pending = self.frames_pending
        if count != pending:
            raise ValueError(
                f"the stream is out of step: {pending} frames were analysed but {count} were given"
            )
        if count == 0:
            return [np.zeros((0, self._channels)) for _ in range(self.outputs)]
        # The overlap-add accumulator holds the frames that have not been
        # handed out yet, so it cannot hold the frames of an arbitrarily long
        # block at the same time.  The frames are therefore added to it in
        # groups that fit into it, and the samples are handed out group by
        # group: a block of any size is accepted, and the memory the
        # accumulator needs does not depend on the size of the block.
        #
        # The transform is taken on the whole set of frames at once: the
        # result of a transform depends on the shape of the array it is given,
        # so a transform per group would not be the same transform.
        rendered = [
            irfft(spec, n=self.n_fft, axis=1) * self._syn_col for spec in specs
        ]
        pieces: list[list[np.ndarray]] = [[] for _ in range(self.outputs)]
        start = 0
        while start < count:
            take = min(self._group, count - start)
            for i in range(take):
                f = self._syn_frame + i
                g = f * self.hop - self.half
                # The accumulator is addressed by the global position modulo
                # its size, so the slots a position recycles have to be zeroed
                # before the position is written for the first time.  A
                # position is written for the first time by the first frame
                # that covers it: for every frame but the first that is the
                # tail of the frame, the part no earlier frame reaches.
                if f == 0:
                    fresh = g
                else:
                    fresh = g + self.n_fft - self.hop
                for index in range(self.outputs):
                    self._clear(self._acc[index], fresh, g + self.n_fft - fresh)
                self._clear(self._den, fresh, g + self.n_fft - fresh)
                for index in range(self.outputs):
                    self._add(self._acc[index], g, rendered[index][start + i])
                self._add(self._den, g, self._product)
            self._syn_frame += take
            start += take
            # The samples the frames of the group complete are handed out
            # before the next group is written, so the accumulator never
            # holds more frames than it can.
            out = self._emit(self._syn_frame - 1)
            for index in range(self.outputs):
                pieces[index].append(out[index])
        result = []
        for index in range(self.outputs):
            item = (
                pieces[index][0]
                if len(pieces[index]) == 1
                else np.concatenate(pieces[index], axis=0)
            )
            result.append(item[:, 0] if self._mono else item)
        return result

    def synthesize(self, mag, phase_) -> np.ndarray:
        """Rebuild the output samples the analysed frames complete.

        The frames must be the ones the last :meth:`analyze` call returned,
        in the same order; the engine refuses to run out of step.

        Parameters
        ----------
        mag, phase_ : array_like
            The spectra of the new frames, shaped ``(n_bins, n_frames)``
            (mono) or ``(channels, n_bins, n_frames)``.

        Returns
        -------
        numpy.ndarray
            The output samples that are ready.  Mono: shape ``(n,)``;
            multi-channel: shape ``(n, channels)``.  The number of samples
            is the number the frames complete, which in the steady state is
            the size of the analysed block.
        """
        return self.synthesize_many([mag], phase_)[0]

    def _emit(self, last_frame: int) -> list[np.ndarray]:
        """Emit the output samples the frames up to ``last_frame`` complete."""
        p_max = (last_frame + 1) * self.hop - self.half - 1
        # The engine never emits more samples than it received: the frames
        # the end-of-stream padding completes reach past the signal.
        p_max = min(p_max, self._samples - 1)
        if p_max < self._out_pos:
            return [np.zeros((0, self._channels)) for _ in range(self.outputs)]
        n = int(p_max - self._out_pos + 1)
        den = self._gather(self._den, self._out_pos, n)
        outs = []
        for index in range(self.outputs):
            num = self._gather(self._acc[index], self._out_pos, n)
            if self.norm == "cola":
                out = num / self._cola if self._cola else np.zeros_like(num)
            else:
                out = np.zeros_like(num)
                np.divide(num, den[:, None], out=out, where=(den > 1e-12)[:, None])
            outs.append(out)
        self._out_pos += n
        return outs
    def flush_analysis(self) -> tuple[np.ndarray, np.ndarray]:
        """Analyse the frames the end-of-stream padding completes.

        The centred analysis pads the end of the signal the way the
        whole-signal engine does, so both engines produce the same frames.
        The returned spectra have to be handed to :meth:`synthesize` before
        :meth:`flush_synthesis` is called.

        Returns
        -------
        (numpy.ndarray, numpy.ndarray)
            ``(mag, phase)`` for the remaining frames.
        """
        if not self._channels:
            return self._empty(self._mono)
        specs = []
        pad = self._flush_pad()
        if pad:
            zeros = np.zeros((pad, self._channels), dtype=np.float64)
            pos = 0
            while pos < pad:
                take = min(self._chunk, pad - pos)
                self._push(zeros[pos : pos + take])
                pos += take
                spec = self._ready()
                if spec is not None:
                    specs.append(spec)
        if not specs:
            return self._empty(self._mono)
        spec = specs[0] if len(specs) == 1 else np.concatenate(specs, axis=0)
        return self._pack(spec, self._mono)

    def _flush_pad(self) -> int:
        """Number of zero samples the end of the stream is padded with."""
        n = int(self._samples)
        if self.center:
            # The whole-signal engine produces ``ceil(n / hop) + 1`` frames,
            # so the last frame has to see the end of the signal.
            last = -(-n // self.hop) * self.hop
            return max(self.half, last + self.half - n)
        return max(0, self.n_fft - n)

    def _covered_end(self) -> int:
        """Global output position where the synthesised frames stop."""
        if self._syn_frame == 0:
            return 0
        last = self._syn_frame - 1
        return int(last * self.hop - self.half + self.n_fft)

    def flush_synthesis(self) -> list[np.ndarray]:
        """Emit the output samples that are left at the end of the stream.

        The samples the frames do not reach are zeros, exactly like the
        whole-signal engine, which drops the tail it cannot reconstruct.

        Returns
        -------
        list of numpy.ndarray
            The remaining output samples, one array per output signal, same
            layout as the output of :meth:`synthesize`.
        """
        total = int(self._samples - self._out_pos)
        if total <= 0:
            return [np.zeros((0, self._channels)) for _ in range(self.outputs)]
        n = int(min(self._samples, self._covered_end()) - self._out_pos)
        if n <= 0:
            out = np.zeros((total, self._channels), dtype=np.float64)
            self._out_pos += total
            return [
                out[:, 0] if self._mono else out for _ in range(self.outputs)
            ]
        den = self._gather(self._den, self._out_pos, n)
        outs = []
        for index in range(self.outputs):
            num = self._gather(self._acc[index], self._out_pos, n)
            if self.norm == "cola":
                out = num / self._cola if self._cola else np.zeros_like(num)
            else:
                out = np.zeros_like(num)
                np.divide(num, den[:, None], out=out, where=(den > 1e-12)[:, None])
            outs.append(out)
        self._out_pos += n
        if n < total:
            outs = [
                np.concatenate(
                    (item, np.zeros((total - n, self._channels), dtype=np.float64)),
                    axis=0,
                )
                for item in outs
            ]
            self._out_pos += total - n
        return [item[:, 0] if self._mono else item for item in outs]

    def process(self, block, modify=None):
        """Analyse a block, edit it and synthesise it back.

        Parameters
        ----------
        block : array_like
            New samples, shape ``(n,)`` or ``(n, channels)``.
        modify : callable, optional
            Called as ``modify(mag, phase)`` with the new frames and returns
            the edited ``(mag, phase)``.  A list of magnitude arrays is
            accepted as well, one per output signal, and the phase is then
            the phase of the frames.  ``None`` leaves the spectra alone.

        Returns
        -------
        numpy.ndarray or list of numpy.ndarray
            Exactly as many samples as the block had, one array per output
            signal.  The first :attr:`latency_samples` samples are zeros,
            because the centred analysis needs the samples that follow them.
        """
        arr, mono = _as_2d(block)
        n = int(arr.shape[0])
        mag, phase_ = self.analyze(block)
        if modify is not None and mag.shape[-1]:
            mag, phase_ = modify(mag, phase_)
        if isinstance(mag, (list, tuple)):
            outs = self.synthesize_many(mag, phase_)
        else:
            outs = self.synthesize_many([mag] * self.outputs, phase_)
        results = []
        for index, item in enumerate(outs):
            if self._fifos[index] is None:
                # The line starts with the latency of the engine, so the
                # output is delayed by exactly ``latency_samples`` whatever
                # the size of the blocks the engine is fed.
                self._fifos[index] = np.zeros(
                    (self.latency_samples, self._channels), dtype=np.float64
                )
            # The line holds every channel, so a mono result is put back
            # into its two dimensions before the two are joined.
            data = item if item.ndim == 2 else item[:, None]
            fifo = np.concatenate((self._fifos[index], data), axis=0)
            take = min(n, int(fifo.shape[0]))
            out = fifo[:take]
            self._fifos[index] = fifo[take:]
            if take < n:
                out = np.concatenate(
                    (out, np.zeros((n - take, self._channels), dtype=np.float64)), axis=0
                )
            results.append(out[:, 0] if mono else out)
        return results if len(results) > 1 else results[0]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<StreamingSTFT n_fft={self.n_fft} hop={self.hop} "
            f"bins={self.n_bins} window={self.window_name!r} latency={self.latency_samples}>"
        )


def stft(
    x,
    n_fft: int = 1024,
    hop: int | None = None,
    window: str | np.ndarray = "hann",
    overlap_factor: int = 2,
    center: bool = True,
    **window_kw,
) -> np.ndarray:
    """Short-time Fourier transform of a signal.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    n_fft : int
        Transform size in samples.
    hop : int, optional
        Hop between frames in samples (default ``n_fft // overlap_factor``).
    window : str or numpy.ndarray
        Window name or array.
    overlap_factor : int
        Overlap factor used to derive the hop (2 = 50 %, 4 = 75 %).
    center : bool
        Centre the frames (pad ``n_fft // 2`` samples on both sides).
    **window_kw
        Extra window arguments (e.g. ``beta``).

    Returns
    -------
    numpy.ndarray
        Complex spectrogram.  Mono: shape ``(n_fft // 2 + 1, n_frames)``.
        Multi-channel: shape ``(n_channels, n_fft // 2 + 1, n_frames)``.
    """
    engine = STFT(n_fft, hop, window, overlap_factor, **window_kw)
    arr, mono = _as_2d(x)
    spec = engine._spectrogram(arr, center=center)
    if mono:
        spec = spec[0]
    return spec


def istft(
    spec,
    hop: int | None = None,
    window: str | np.ndarray = "hann",
    overlap_factor: int = 2,
    *,
    n_fft: int | None = None,
    synthesis_window=None,
    norm: str = "exact",
    center: bool = True,
    length: int | None = None,
    **window_kw,
) -> np.ndarray:
    """Inverse short-time Fourier transform (overlap-add).

    Parameters
    ----------
    spec : array_like
        Complex spectrogram, shape ``(n_bins, n_frames)`` or
        ``(n_channels, n_bins, n_frames)``.
    hop : int, optional
        Hop between frames in samples (default ``n_fft // overlap_factor``).
    window : str or numpy.ndarray
        The **analysis** window.  It defines the normalisation denominator.
    overlap_factor : int
        Overlap factor used to derive the hop.
    n_fft : int, optional
        Transform size.  Default ``2 * (n_bins - 1)``.
    synthesis_window : str or numpy.ndarray, optional
        Window applied to the synthesised frames.  Default: the analysis
        window (which is what makes the round trip exact).  Use
        ``"rectangular"`` for the classic Hann-analysis / rectangular-synthesis
        scheme, which pairs with ``norm="cola"``.
    norm : str
        ``"exact"`` (default) or ``"cola"``.
    center : bool
        True when the analysis was centred; the leading ``n_fft // 2`` samples
        are removed from the result.
    length : int, optional
        Exact output length in samples.

    Returns
    -------
    numpy.ndarray
        The reconstructed signal, 1-D for a mono spectrogram and 2-D
        ``(n_samples, n_channels)`` otherwise.

    Notes
    -----
    With ``center=False`` the first and the last samples are covered by only
    part of a window.  For a window that is exactly zero at its edge (Hann and
    friends) the very first sample carries no information at all and cannot be
    reconstructed; use ``center=True`` (the default) for a lossless round trip.
    """
    arr = np.asarray(spec, dtype=np.complex128)
    if arr.ndim not in (2, 3):
        raise ValueError("spec must be 2-D (n_bins, n_frames) or 3-D (ch, n_bins, n_frames)")
    if n_fft is None:
        n_fft = 2 * (arr.shape[-2] - 1)
    n_fft = int(n_fft)
    engine = STFT(n_fft, hop, window, overlap_factor, **window_kw)
    if synthesis_window is not None:
        engine.syn_win = np.asarray(
            make_window(synthesis_window, n_fft, **engine.window_kw), dtype=np.float64
        )
    if arr.ndim == 2:
        out = engine._istft(arr, norm=norm)
    else:
        out = np.stack([engine._istft(arr[c], norm=norm) for c in range(arr.shape[0])], axis=1)
    if center:
        out = out[n_fft // 2 :]
    if length is not None:
        length = int(length)
        if out.shape[0] < length:
            extra = np.zeros((length - out.shape[0],) + out.shape[1:], dtype=np.float64)
            out = np.concatenate([out, extra], axis=0)
        else:
            out = out[:length]
    return out


def magnitude_spectrogram(
    x,
    n_fft: int = 1024,
    hop: int | None = None,
    window: str | np.ndarray = "hann",
    overlap_factor: int = 2,
    center: bool = True,
    **window_kw,
) -> np.ndarray:
    """Linear magnitude spectrogram.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    n_fft, hop, window, overlap_factor, center
        As for :func:`stft`.
    **window_kw
        Extra window arguments.

    Returns
    -------
    numpy.ndarray
        Magnitudes, shape ``(n_bins, n_frames)`` (mono) or
        ``(n_channels, n_bins, n_frames)``.
    """
    return np.abs(stft(x, n_fft, hop, window, overlap_factor, center, **window_kw))


def spectrogram_db(
    x,
    n_fft: int = 1024,
    hop: int | None = None,
    window: str | np.ndarray = "hann",
    overlap_factor: int = 2,
    center: bool = True,
    floor_db: float = -120.0,
    sample_rate: float = 48000.0,
    **window_kw,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Magnitude spectrogram in dB, with its frequency and time axes.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    n_fft : int
        Transform size in samples.
    hop : int, optional
        Hop between frames in samples.
    window : str or numpy.ndarray
        Window name or array.
    overlap_factor : int
        Overlap factor used to derive the hop.
    center : bool
        Centre the frames.
    floor_db : float
        Floor in dB for the log (default -120).
    sample_rate : float
        Sample rate in Hz, used for the frequency and time axes.
    **window_kw
        Extra window arguments.

    Returns
    -------
    (numpy.ndarray, numpy.ndarray, numpy.ndarray)
        ``(db, freqs, times)``.  ``db`` has shape ``(n_bins, n_frames)``
        (mono) or ``(n_channels, n_bins, n_frames)``; ``freqs`` holds the bin
        centre frequencies in Hz (length ``n_bins``) and ``times`` the frame
        centre times in seconds (length ``n_frames``).
    """
    engine = STFT(n_fft, hop, window, overlap_factor, **window_kw)
    arr, mono = _as_2d(x)
    spec = engine._spectrogram(arr, center=center)
    if mono:
        spec = spec[0]
    db = magnitude_db(spec, floor_db=floor_db)
    freqs = bin_frequencies(engine.n_fft, sample_rate)
    n_frames = spec.shape[-1]
    if center:
        times = np.arange(n_frames, dtype=np.float64) * (engine.hop / float(sample_rate))
    else:
        times = (
            np.arange(n_frames, dtype=np.float64) * engine.hop + engine.n_fft / 2.0
        ) / float(sample_rate)
    return db, freqs, times


class Spectrogram:
    """Full-signal magnitude spectrogram with a matching synthesis.

    Parameters
    ----------
    x : array_like
        Signal, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.
    n_fft : int
        Transform size in samples.
    hop : int, optional
        Hop between frames in samples (default ``n_fft // overlap_factor``).
    window : str or numpy.ndarray
        Window name or array.
    overlap_factor : int
        Overlap factor used to derive the hop (2 = 50 %, 4 = 75 %).
    center : bool
        Centre the frames (pad ``n_fft // 2`` samples on both sides).
    floor_db : float
        Floor in dB for :attr:`db`.
    sample_rate : float
        Sample rate in Hz, used for :attr:`freqs` and :attr:`times`.
    **window_kw
        Extra window arguments.

    Attributes
    ----------
    mag : numpy.ndarray
        Linear magnitude, shape ``(n_bins, n_frames)`` (mono) or
        ``(n_channels, n_bins, n_frames)``.
    phase : numpy.ndarray
        Phase in radians, same shape as :attr:`mag`.
    db : numpy.ndarray
        Magnitude in dB, same shape as :attr:`mag`.
    freqs : numpy.ndarray
        Bin centre frequencies in Hz, shape ``(n_bins,)``.
    times : numpy.ndarray
        Frame centre times in seconds, shape ``(n_frames,)``.
    """

    def __init__(
        self,
        x,
        n_fft: int = 1024,
        hop: int | None = None,
        window: str | np.ndarray = "hann",
        overlap_factor: int = 2,
        center: bool = True,
        floor_db: float = -120.0,
        sample_rate: float = 48000.0,
        **window_kw,
    ):
        self.engine = STFT(n_fft, hop, window, overlap_factor, **window_kw)
        self.center = bool(center)
        self.floor_db = float(floor_db)
        self.sample_rate = float(sample_rate)
        arr, self._mono = _as_2d(x)
        self.n_samples = int(arr.shape[0])

        spec = self.engine._spectrogram(arr, center=self.center)
        if self._mono:
            spec = spec[0]
        self.mag = np.abs(spec)
        self.phase = phase(spec)
        self.db = magnitude_db(spec, floor_db=self.floor_db)
        self.n_bins = int(spec.shape[-2])
        self.n_frames = int(spec.shape[-1])
        self.freqs = bin_frequencies(self.engine.n_fft, self.sample_rate)
        if self.center:
            self.times = np.arange(self.n_frames, dtype=np.float64) * (
                self.engine.hop / self.sample_rate
            )
        else:
            self.times = (
                np.arange(self.n_frames, dtype=np.float64) * self.engine.hop
                + self.engine.n_fft / 2.0
            ) / self.sample_rate

    # -- accessors ------------------------------------------------------------
    def to_db(self, floor_db: float | None = None) -> np.ndarray:
        """Magnitude in dB.

        Parameters
        ----------
        floor_db : float, optional
            Floor in dB; defaults to the floor given at construction.

        Returns
        -------
        numpy.ndarray
            The dB spectrogram, same shape as :attr:`mag`.
        """
        floor = self.floor_db if floor_db is None else float(floor_db)
        return magnitude_db(self.mag, floor_db=floor)

    def synthesize(self, norm: str = "exact", length: int | None = None) -> np.ndarray:
        """Rebuild the time-domain signal from the stored spectrogram.

        Parameters
        ----------
        norm : str
            ``"exact"`` (default) or ``"cola"``.
        length : int, optional
            Exact output length in samples (defaults to the input length).

        Returns
        -------
        numpy.ndarray
            The reconstructed signal, shape ``(n_samples,)`` (mono) or
            ``(n_samples, n_channels)``.
        """
        if self._mono:
            out = self.engine._istft(to_complex(self.mag, self.phase), norm=norm)
        else:
            out = np.stack(
                [
                    self.engine._istft(to_complex(self.mag[c], self.phase[c]), norm=norm)
                    for c in range(self.mag.shape[0])
                ],
                axis=1,
            )
        if self.center:
            out = out[self.engine.n_fft // 2 :]
        target = self.n_samples if length is None else int(length)
        if out.shape[0] < target:
            extra = np.zeros((target - out.shape[0],) + out.shape[1:], dtype=np.float64)
            out = np.concatenate([out, extra], axis=0)
        else:
            out = out[:target]
        return out if self._mono else out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Spectrogram n_fft={self.engine.n_fft} hop={self.engine.hop} "
            f"bins={self.n_bins} frames={self.n_frames} window={self.engine.window_name!r}>"
        )
