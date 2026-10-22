"""Tests for the short-time Fourier transform engine.

The file covers the whole-signal engine (:class:`aurcore.dsp.stft.STFT`) and
the streaming engine (:class:`aurcore.dsp.stft.StreamingSTFT`).

The streaming engine has to be *exact*: the frames it produces, the spectrum
it rebuilds and the samples it emits have to be the ones the whole-signal
engine produces for the same signal, because the modules that use them
compute their result from those frames.
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pytest

from aurcore.dsp.stft import STFT, StreamingSTFT, istft, stft

# ``aurcore.dsp`` re-exports the ``stft`` function, so the module object has
# to be taken from the module registry to be able to instrument it.
_stft_module = sys.modules["aurcore.dsp.stft"]

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _signal(n: int, channels: int = 1, seed: int = 7) -> np.ndarray:
    """A deterministic test signal."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, channels)) * 0.5


def _whole_signal(engine: STFT, x: np.ndarray, center: bool = True):
    """The whole-signal analysis and synthesis of ``x``."""
    mag, phase = engine.analyze_block(x, center=center)
    out = engine.synthesize_block(
        mag, phase, norm="exact", length=int(x.shape[0]), center=center
    )
    return mag, phase, out


def _stream(engine: StreamingSTFT, x: np.ndarray, block: int, gain=None):
    """Run a whole signal through the streaming engine, block by block."""
    pieces = []
    for start in range(0, len(x), block):
        chunk = x[start : start + block]
        mag, phase = engine.analyze(chunk)
        if gain is not None and mag.shape[-1]:
            mag = mag * gain[:, None] if mag.ndim == 2 else mag * gain[None, :, None]
        pieces.append(engine.synthesize(mag, phase))
    mag, phase = engine.flush_analysis()
    if gain is not None and mag.shape[-1]:
        mag = mag * gain[:, None] if mag.ndim == 2 else mag * gain[None, :, None]
    pieces.append(engine.synthesize(mag, phase))
    # the engine hands out one array per output signal
    tail = engine.flush_synthesis()
    if isinstance(tail, list):
        tail = tail[0]
    pieces.append(tail)
    return np.concatenate([p for p in pieces if p.size], axis=0)


def _db(error: float, reference: np.ndarray) -> float:
    """Error in dB relative to the peak of the reference."""
    peak = float(np.max(np.abs(reference))) or 1.0
    return 20.0 * math.log10(max(float(error) / peak, 1e-30))


class _TransformCounter:
    """Count the transforms the engine runs.

    A transform is one ``n_fft`` point transform of one channel, so the
    number of transforms of a batched call is the size of the array without
    the transformed axis.
    """

    def __init__(self) -> None:
        self.transforms = 0

    def __enter__(self) -> "_TransformCounter":
        self._rfft = _stft_module.rfft
        self._irfft = _stft_module.irfft
        counter = self

        def rfft(x, n=None, backend="numpy", axis=-1):
            out = counter._rfft(x, n=n, backend=backend, axis=axis)
            counter.transforms += int(np.asarray(x).size // np.asarray(x).shape[axis])
            return out

        def irfft(x, n=None, axis=-1):
            out = counter._irfft(x, n=n, axis=axis)
            counter.transforms += int(np.asarray(x).size // np.asarray(x).shape[axis])
            return out

        _stft_module.rfft = rfft
        _stft_module.irfft = irfft
        return self

    def __exit__(self, *exc) -> bool:
        _stft_module.rfft = self._rfft
        _stft_module.irfft = self._irfft
        return False


# ---------------------------------------------------------------------------
# the whole-signal engine (the interface the modules use today)
# ---------------------------------------------------------------------------


def test_whole_signal_round_trip_is_exact():
    """The whole-signal round trip reconstructs the signal."""
    engine = STFT(1024, 256, window="hann")
    x = _signal(4096)
    _, _, out = _whole_signal(engine, x)
    assert out.shape == x.shape
    assert _db(float(np.max(np.abs(out - x))), x) < -200.0


def test_whole_signal_frames_do_not_grow_with_the_block_size():
    """A whole-window analysis of a block always costs the same frames."""
    engine = STFT(1024, 256, window="hann")
    counts = []
    for block in (64, 128, 256, 512):
        mag, _ = engine.analyze_block(_signal(block, 2), center=True)
        counts.append(int(mag.shape[-1]))
    assert counts == [2, 2, 2, 3], counts


def test_module_level_functions_still_work():
    """The module level helpers keep their signature and their result."""
    x = _signal(2048)[:, 0]
    spec = stft(x, n_fft=1024, hop=256, window="hann", center=True)
    assert spec.shape == (513, 9)
    back = istft(spec, hop=256, window="hann", center=True, length=len(x))
    assert back.shape == x.shape
    assert _db(float(np.max(np.abs(back - x))), x) < -200.0


# ---------------------------------------------------------------------------
# the streaming engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_fft,hop", [(1024, 256), (1024, 512), (512, 128), (2048, 512)])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("center", [True, False])
def test_streaming_round_trip_matches_the_whole_signal(n_fft, hop, channels, center):
    """The streaming round trip is the whole-signal round trip."""
    engine = STFT(n_fft, hop, window="hann")
    for n in (8192, 5000, 1000, 333):
        x = _signal(n, channels, seed=n)
        _, _, reference = _whole_signal(engine, x, center=center)
        stream = StreamingSTFT(n_fft, hop, window="hann", center=center)
        got = _stream(stream, x, 64)
        assert got.shape == reference.shape, (n_fft, hop, channels, center, n)
        error = float(np.max(np.abs(got - reference)))
        assert _db(error, reference) < -200.0, (n_fft, hop, channels, center, n, error)


@pytest.mark.parametrize("n_fft,hop", [(1024, 256), (512, 128)])
def test_streaming_spectral_edit_matches_the_whole_signal(n_fft, hop):
    """A per-frame edit gives the same result in both paths."""
    engine = STFT(n_fft, hop, window="hann")
    x = _signal(4096, 2, seed=3)
    gain = np.linspace(0.2, 1.0, engine.n_bins)
    mag, phase, _ = _whole_signal(engine, x)
    edited = mag * gain[None, :, None]
    reference = engine.synthesize_block(
        edited, phase, norm="exact", length=len(x), center=True
    )
    stream = StreamingSTFT(n_fft, hop, window="hann")
    got = _stream(stream, x, 64, gain=gain)
    error = float(np.max(np.abs(got - reference)))
    assert _db(error, reference) < -200.0, error


def test_streaming_produces_the_same_frames_as_the_whole_signal():
    """The frames of the two engines are the same frames."""
    engine = STFT(1024, 256, window="hann")
    x = _signal(4096, 1, seed=5)
    mag_ref, _ = engine.analyze_block(x, center=True)
    stream = StreamingSTFT(1024, 256, window="hann")
    frames = []
    for start in range(0, len(x), 64):
        mag, _ = stream.analyze(x[start : start + 64])
        if mag.shape[-1]:
            frames.append(mag)
    mag, _ = stream.flush_analysis()
    if mag.shape[-1]:
        frames.append(mag)
    mag_stream = np.concatenate(frames, axis=-1)
    assert mag_stream.shape == mag_ref.shape
    assert float(np.max(np.abs(mag_stream - mag_ref))) == 0.0


def test_streaming_transform_count_follows_the_block_size():
    """The transforms per block follow the block size, they are not fixed."""
    counts = {}
    for block in (64, 128, 256, 512):
        stream = StreamingSTFT(1024, 256, window="hann")
        x = _signal(block, 2, seed=2)
        # warm the engine up, then count a fixed number of blocks
        for _ in range(32):
            mag, phase = stream.analyze(x)
            stream.synthesize(mag, phase)
        with _TransformCounter() as counter:
            for _ in range(16):
                mag, phase = stream.analyze(x)
                stream.synthesize(mag, phase)
        counts[block] = counter.transforms
    # 16 blocks of 64 samples hold 1024 samples, which is four frames of
    # 1024 points and two channels: four forward and four backward
    # transforms per channel.
    assert counts == {64: 16, 128: 32, 256: 64, 512: 128}, counts


def test_streaming_costs_fewer_transforms_than_the_whole_signal():
    """A stream of blocks costs a fraction of the whole-window analyses."""
    whole = STFT(1024, 256, window="hann")
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(64, 2, seed=4)
    for _ in range(32):
        mag, phase = stream.analyze(x)
        stream.synthesize(mag, phase)
    with _TransformCounter() as counter:
        for _ in range(16):
            mag, phase = whole.analyze_block(x, center=True)
            whole.synthesize_block(mag, phase, norm="exact", length=64, center=True)
    whole_count = counter.transforms
    with _TransformCounter() as counter:
        for _ in range(16):
            mag, phase = stream.analyze(x)
            stream.synthesize(mag, phase)
    stream_count = counter.transforms
    # 16 whole-window analyses of a 64 sample block: two frames of two
    # channels, forward and backward.
    assert whole_count == 16 * 2 * 2 * 2, whole_count
    # the stream completes one frame every four blocks.
    assert stream_count == 16, stream_count


def test_streaming_reports_its_latency():
    """The engine reports the delay it introduces."""
    assert StreamingSTFT(1024, 256, window="hann").latency_samples == 1024
    assert StreamingSTFT(1024, 512, window="hann").latency_samples == 1024
    assert StreamingSTFT(512, 128, window="hann").latency_samples == 512


def test_streaming_process_returns_a_block_of_the_same_size():
    """The convenience entry point keeps the block size."""
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(64, 2, seed=9)
    out = stream.process(x)
    assert out.shape == x.shape
    # the engine is warming up, so the first blocks are still silent
    assert float(np.max(np.abs(out))) == 0.0
    for _ in range(40):
        out = stream.process(x)
    assert float(np.max(np.abs(out))) > 0.0


def test_streaming_reset_starts_a_new_stream():
    """``reset`` forgets the history of the stream."""
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(2048, 1, seed=11)
    first = _stream(stream, x, 64)
    stream.reset()
    second = _stream(stream, x, 64)
    assert float(np.max(np.abs(first - second))) == 0.0


def test_streaming_factory_on_the_whole_signal_engine():
    """``STFT.streaming`` hands out an engine with the same configuration."""
    engine = STFT(1024, 256, window="hann")
    stream = engine.streaming()
    assert stream.n_fft == engine.n_fft
    assert stream.hop == engine.hop
    assert stream.n_bins == engine.n_bins
    assert float(np.max(np.abs(stream.win - engine.win))) == 0.0


def test_streaming_refuses_to_run_out_of_step():
    """The engine does not silently produce a wrong result."""
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(2048, 1, seed=13)
    stream.analyze(x)
    with pytest.raises(ValueError):
        stream.synthesize(np.zeros((513, 3)), np.zeros((513, 3)))


def test_streaming_handles_mono_and_stereo():
    """The layout of the result follows the layout of the input."""
    mono = StreamingSTFT(1024, 256, window="hann")
    mag, phase = mono.analyze(_signal(4096, 1, seed=15)[:, 0])
    # 4096 samples complete the frames 0..14 of a 1024 point window with a
    # hop of 256: the frame 15 needs the samples up to 4352.
    assert mag.shape == (513, 15)
    assert phase.shape == (513, 15)

    stereo = StreamingSTFT(1024, 256, window="hann")
    mag, phase = stereo.analyze(_signal(4096, 2, seed=15))
    assert mag.shape == (2, 513, 15)
    assert phase.shape == (2, 513, 15)


def test_streaming_output_is_finite():
    """The engine never returns a NaN or an infinity."""
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(4096, 2, seed=17)
    out = _stream(stream, x, 64)
    assert np.all(np.isfinite(out))


# ---------------------------------------------------------------------------
# the block size does not decide whether the engine works
# ---------------------------------------------------------------------------


def test_streaming_accepts_a_block_that_holds_more_frames_than_the_accumulator():
    """A block is accepted however many frames it completes.

    The engine used to refuse a block that completed more frames than its
    overlap-add accumulator could hold, so a module that handed it a large
    block failed, and the failure depended on the size of the block.
    """
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(12000, 1, seed=19)
    mag, phase = stream.analyze(x)
    # 12000 samples complete 45 frames of a 1024 point window with a hop of
    # 256, and the accumulator of the engine holds twelve of them.
    assert mag.shape[-1] == 45
    out = stream.synthesize(mag, phase)
    assert out.shape[0] > 0
    assert np.all(np.isfinite(out))


@pytest.mark.parametrize("block", [64, 512, 4096, 12000])
@pytest.mark.parametrize("channels", [1, 2])
def test_streaming_block_of_any_size_matches_the_whole_signal(block, channels):
    """The answer does not depend on the size of the block.

    The streaming path is compared with the whole-signal path, and the two
    have to be the same samples, not only the same to a tolerance.
    """
    engine = STFT(1024, 256, window="hann")
    x = _signal(12000, channels, seed=23)
    _, _, reference = _whole_signal(engine, x)
    stream = StreamingSTFT(1024, 256, window="hann")
    got = _stream(stream, x, block)
    assert got.shape == reference.shape, (block, channels)
    assert np.array_equal(got, reference), (block, channels)


def test_streaming_block_of_any_size_carries_a_spectral_edit():
    """A per-frame edit of a large block gives the same result."""
    engine = STFT(1024, 256, window="hann")
    x = _signal(12000, 2, seed=29)
    gain = np.linspace(0.2, 1.0, engine.n_bins)
    mag, phase, _ = _whole_signal(engine, x)
    edited = mag * gain[None, :, None]
    reference = engine.synthesize_block(
        edited, phase, norm="exact", length=len(x), center=True
    )
    stream = StreamingSTFT(1024, 256, window="hann")
    got = _stream(stream, x, 12000, gain=gain)
    assert got.shape == reference.shape
    assert np.array_equal(got, reference)


def test_streaming_cost_follows_the_number_of_frames():
    """The work of a block follows the frames it completes.

    The cost of a block must not grow faster than the number of frames it
    completes, or a host that uses a large block would pay for it.
    """
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(12000, 2, seed=31)
    with _TransformCounter() as counter:
        mag, phase = stream.analyze(x)
        stream.synthesize(mag, phase)
    # 45 frames of two channels, forward and backward.
    assert counter.transforms == 45 * 2 * 2, counter.transforms


def test_streaming_keeps_its_latency_for_a_block_of_any_size():
    """The delay of the engine does not depend on the size of the block."""
    stream = StreamingSTFT(1024, 256, window="hann")
    impulse = np.zeros(8192, dtype=np.float64)
    impulse[0] = 1.0
    out = stream.process(impulse)
    assert out.shape == impulse.shape
    assert int(np.argmax(np.abs(out))) == stream.latency_samples


def test_streaming_process_handles_a_mono_block():
    """The convenience entry point works for a mono signal."""
    stream = StreamingSTFT(1024, 256, window="hann")
    x = _signal(512, 1, seed=41)[:, 0]
    out = stream.process(x)
    assert out.shape == x.shape
    assert np.all(np.isfinite(out))


def test_streaming_with_two_outputs_accepts_a_large_block():
    """A block of any size works for an engine with more than one output."""
    stream = StreamingSTFT(1024, 256, window="hann", outputs=2)
    x = _signal(12000, 2, seed=37)
    mag, phase = stream.analyze(x)
    assert mag.shape[-1] == 45
    outs = stream.synthesize_many([mag, mag], phase)
    assert len(outs) == 2
    for item in outs:
        assert item.shape[0] > 0
        assert np.all(np.isfinite(item))
