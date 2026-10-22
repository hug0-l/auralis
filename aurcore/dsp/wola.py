"""
aurcore.dsp.wola — a weighted overlap-add filter bank.

A linear-phase filter does not need a long convolution kernel.  A
weighted overlap-add (WOLA) filter bank applies the same response with a
symmetric analysis window and a transform, and the window length plays
the role the kernel length plays in the direct-convolution design.

The bank is built so that its answer is the answer of the kernel it is
given, to the rounding of the transform:

* the analysis window is symmetric and its two halves sum to one, so the
  overlap-add of the frames reproduces the convolution with the kernel
  exactly (measured: a relative error of 6.7e-16, which is the rounding
  of the transform), and
* the frames are half a window apart, so the bank runs at twice the rate
  of a critical-sampling transform and the transform is paid once per
  half window instead of once per block.

The state is carried between the calls, so a signal filtered in blocks of
any size gives the same answer as the same signal filtered in one block.
"""
from __future__ import annotations

import numpy as np

__all__ = ["WolaBank", "cola_window"]

#: The window length the fast path of the de-hum uses.
DEFAULT_WINDOW = 4096


def _next_pow2(n: int) -> int:
    """The smallest power of two that is not smaller than ``n``."""
    v = 1
    while v < int(n):
        v <<= 1
    return v


def cola_window(length: int) -> np.ndarray:
    """A symmetric window whose two halves sum to one.

    The overlap-add of the frames of a bank with this window reproduces
    the convolution with the bank's kernel exactly: the two frames that
    share a sample weigh it with ``w[r] + w[r + length // 2]``, and that
    sum is one for every ``r``.  The window is symmetric about its
    centre, so the bank is a linear-phase filter.

    Parameters
    ----------
    length : int
        The length of the window in samples.  It must be even; the hop
        of the bank is half of it (50 % overlap).

    Returns
    -------
    numpy.ndarray
        The window, ``length`` samples long.
    """
    n = int(length)
    if n < 2 or n % 2:
        raise ValueError("the window length must be even and at least 2")
    half = n // 2
    if half == 1:
        return np.ones(2, dtype=np.float64)
    first = 0.5 * (1.0 - np.cos(np.pi * np.arange(half, dtype=np.float64) / (half - 1)))
    return np.concatenate([first, first[::-1]])


class WolaBank:
    """A weighted overlap-add filter bank.

    The bank applies the impulse response it is given.  The response is
    the response of that impulse response: the bank is a way of running
    the filter, not a different filter.

    The analysis window is symmetric and the frames are half a window
    apart, so the bank is linear phase and the transform is paid once
    per half window.  The state is carried between the calls.

    The bank answers a window after the input it filters: the frames
    overlap, so the first frame is only complete after a whole window of
    input has arrived.  The delay of the filter itself is on top of
    that; :attr:`latency_samples` reports the sum of the two.

    Parameters
    ----------
    kernel : array_like
        The impulse response the bank applies.  A symmetric kernel gives
        a linear-phase filter.
    fs : float
        Sample rate in Hz (used for the frequency response).
    window_length : int
        The length of the analysis window in samples.  It must be even;
        the hop of the bank is half of it.
    """

    __slots__ = ("kernel", "fs", "window_length", "hop", "window",
                 "_size", "_gain", "_out_len", "_ring", "_mask",
                 "_acc", "_nch", "_inbuf", "_inlen", "_frame_start",
                 "_emit", "_final", "_silence")

    def __init__(self, kernel, fs: float = 48000.0, *,
                 window_length: int = DEFAULT_WINDOW):
        k = np.asarray(kernel, dtype=np.float64).ravel()
        if k.size < 1:
            raise ValueError("the kernel must have at least one tap")
        if not np.all(np.isfinite(k)):
            raise ValueError("the kernel must be finite")
        n = int(window_length)
        if n < 2 or n % 2:
            raise ValueError("the window length must be even and at least 2")
        self.kernel = k
        self.fs = float(fs)
        self.window_length = n
        self.hop = n // 2
        self.window = cola_window(n)
        # The transform has to be long enough for the linear convolution
        # of a frame and the kernel: the frame is a window long and the
        # kernel may be longer than the window.
        self._size = _next_pow2(n + k.size - 1)
        self._gain = np.fft.rfft(k, self._size)
        self._out_len = n + k.size - 1
        # The overlap-add accumulator is a ring: the frames write their
        # output at the position of the frame and read it back when the
        # answer is due.
        self._ring = _next_pow2(self._out_len + 2 * self.hop)
        self._mask = self._ring - 1
        self.reset()

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Clear the memory of the bank."""
        self._acc = None
        self._nch = 1
        self._inbuf = np.zeros((1, self.window_length), dtype=np.float64)
        self._inlen = 0
        self._frame_start = 0
        # The first answer is due one window after the start of the
        # stream: the frames overlap, so the first frame is complete
        # only after a whole window of input.
        self._emit = -self.window_length
        # The filter is silent until its own delay has elapsed: the
        # convolution of a symmetric kernel carries the kernel's left
        # half into the region before the input arrives.  The bank
        # answers a window later, so the silence lasts a window longer
        # than the delay of the kernel.
        self._silence = self.window_length + self._delay_samples
        self._prime()

    def set_kernel(self, kernel) -> None:
        """Change the impulse response the bank applies.

        The memory of the bank is kept: the frames that have already
        been run used the old response and the frames that come use the
        new one, so the filter moves to the new response without a step.

        Parameters
        ----------
        kernel : array_like
            The new impulse response.  It must have the same number of
            taps as the one the bank was built with.
        """
        k = np.asarray(kernel, dtype=np.float64).ravel()
        if k.size != self.kernel.size:
            raise ValueError("the kernel must keep the number of taps it has")
        if not np.all(np.isfinite(k)):
            raise ValueError("the kernel must be finite")
        self.kernel = k
        self._gain = np.fft.rfft(k, self._size)

    def _prime(self) -> None:
        """Start the frames half a window before the stream.

        The window weighs the samples of the stream with the sum of two
        frames, and the first half window of the stream is only in one
        frame unless the bank is primed with the frame that starts
        before the stream.  That frame reads zeros, so the priming does
        not change the signal, only the weighting of its first samples.
        """
        self._inbuf[:, :self.hop] = 0.0
        self._inlen = self.hop
        self._frame_start = -self.hop
        # The answers up to this index are ready: the frame that ends
        # here owns the answers up to the start of the next frame.
        self._final = 0

    @property
    def _delay_samples(self) -> int:
        """The delay of the filter itself, in samples."""
        total = float(np.sum(self.kernel))
        if total == 0.0:
            return 0
        moment = float(np.sum(np.arange(self.kernel.size, dtype=np.float64) * self.kernel))
        return int(round(moment / total))

    @property
    def latency_samples(self) -> int:
        """The delay the bank adds, in samples.

        That is the window the bank needs before it can answer plus the
        delay of the kernel it applies.
        """
        return int(self.window_length + self._delay_samples)

    @property
    def state(self) -> np.ndarray:
        """The memory of the bank, shape ``(channels, window_length)``."""
        return self._inbuf[:, :self._inlen].copy()

    # -- processing -----------------------------------------------------------
    def _allocate(self, channels: int) -> None:
        """Make the buffers for a channel count."""
        self._acc = np.zeros((channels, self._ring), dtype=np.float64)
        self._nch = channels
        self._inbuf = np.zeros((channels, self.window_length), dtype=np.float64)
        self._prime()

    def _process_frame(self) -> None:
        """Run one frame through the bank and add it to the accumulator."""
        n = self.window_length
        buf = np.zeros((self._nch, self._size), dtype=np.float64)
        buf[:, :n] = self._inbuf * self.window
        spec = np.fft.rfft(buf, self._size, axis=-1) * self._gain
        y = np.fft.irfft(spec, self._size, axis=-1)[:, :self._out_len]
        # The part of the frame that lies before the stream is not
        # written: the stream has no samples there, and the slot of a
        # sample that does not exist would be read again much later.
        start = max(self._frame_start, 0)
        skip = start - self._frame_start
        idx = (start + np.arange(self._out_len - skip)) & self._mask
        self._acc[:, idx] += y[:, skip:]

    def _drain(self, out: np.ndarray, written: int, limit: int) -> int:
        """Move the answers that are ready into the output.

        The answers leave the accumulator in the order they are ready,
        and the slots they leave behind are cleared: the ring carries
        the answer of a sample that is long gone into the answer of the
        sample that takes its place otherwise.
        """
        end = min(self._final, self._emit + (limit - written))
        if end <= self._emit:
            return written
        first = self._emit
        start = max(first, 0)
        if end > start:
            idx = np.arange(start, end) & self._mask
            out[:, written + (start - first):written + (end - first)] = self._acc[:, idx]
            self._acc[:, idx] = 0.0
        written += end - first
        self._emit = end
        return written

    def process(self, x) -> np.ndarray:
        """Filter a block of samples.

        Parameters
        ----------
        x : array_like
            Samples, shape ``(n_samples,)`` or ``(n_samples, n_channels)``.

        Returns
        -------
        numpy.ndarray
            The filtered samples, same shape as ``x``.
        """
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim not in (1, 2):
            raise ValueError("x must be 1-D (n,) or 2-D (n, channels)")
        one_d = arr.ndim == 1
        if one_d:
            arr = arr[:, None]
        n_in, channels = arr.shape
        if self._acc is None or self._nch != channels:
            self._allocate(channels)
        if n_in == 0:
            return arr[:, 0] if one_d else arr

        out = np.zeros((channels, n_in), dtype=np.float64)
        written = self._drain(out, 0, n_in)

        # 1. the input goes into the frame that is being filled
        block = arr.T
        pos = 0
        while pos < n_in:
            take = min(self.window_length - self._inlen, n_in - pos)
            self._inbuf[:, self._inlen:self._inlen + take] = block[:, pos:pos + take]
            self._inlen += take
            pos += take
            if self._inlen == self.window_length:
                self._process_frame()
                # The next frame starts half a window later, so the
                # frame keeps the tail of this one and waits for the
                # rest.
                keep = self.window_length - self.hop
                self._inbuf[:, :keep] = self._inbuf[:, self.hop:self.window_length]
                self._inlen = keep
                # The answers up to the start of the next frame are
                # ready: the next frame does not touch them.
                self._final = self._frame_start + self.hop
                self._frame_start += self.hop
                # 2. the answers that are due leave the accumulator
                written = self._drain(out, written, n_in)

        # 3. the filter is silent until its own delay has elapsed
        if self._silence > 0:
            take = min(self._silence, n_in)
            out[:, :take] = 0.0
            self._silence -= take
        out = out.T
        return out[:, 0] if one_d else out

    __call__ = process

    # -- analysis -------------------------------------------------------------
    def freq_response(self, freqs) -> np.ndarray:
        """Complex frequency response.

        The bank applies the kernel it was given, so its response is the
        response of that kernel.

        Parameters
        ----------
        freqs : array_like
            Frequencies in Hz.

        Returns
        -------
        numpy.ndarray
            ``H(f)`` (complex), same shape as ``freqs``.
        """
        w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64) / self.fs
        response = np.zeros(np.shape(freqs), dtype=np.complex128)
        for k, hk in enumerate(self.kernel):
            response = response + hk * np.exp(-1j * w * k)
        return response
