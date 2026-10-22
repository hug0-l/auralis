"""
tests/test_mixing.py — the gate of the mixing module and of the shared stream.

What the gate asks
------------------
The module (``AUR_MIXING``)
    * the L/R matrix is the one the two faders describe;
    * 100 % / 100 % gives the input back;
    * the output is as long as the input and holds no NaN.

The shared stream (:class:`aurcore.modules.mixing.SpectralStream`)
    * it publishes the delay it puts into the signal, and the signal shows
      exactly that delay;
    * it hands its samples out **in the order they arrived**, at the rate
      the caller asks for them: a caller that asks for the number of
      samples it has to give its host gets them, whether a frame was ready
      or not, and never a gap;
    * the output does not depend on the size of the block it is fed in;
    * the state it keeps does not grow with the size of the block either.

The rule the stream exists for
------------------------------
A stream that only hands its samples out when a frame is ready forces
every module that uses it to keep a line of its own, and the size of that
line depends on the block size the host happened to use: the line grew by
192 samples every four blocks at 64 samples per block, and the delay the
signal showed grew with it.  The gate below holds the stream to the rule
that the samples come out in the order they went in, at every block size.
"""
from __future__ import annotations

import numpy as np
import pytest

from aurcore.modules.mixing import (
    ChannelOps,
    Mixing,
    SpectralStream,
    istft,
    stft,
    to_left_right,
    to_mid_side,
)

#: The window and the hop the stream is measured with.
N_FFT = 1024
HOP = 256

#: The block sizes a host may use.  64 is the smallest one and 4096 the
#: largest one the product is measured at.
BLOCKS = (64, 128, 256, 1024, 4096)

#: How much the renders of two block sizes may differ from each other.
#: The stream is deterministic, so the answer is exactly zero.
TOLERANCE = 0.0

#: The rate the measurements run at.
SAMPLE_RATE = 48000


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------
def _noise(count: int, seed: int = 11) -> np.ndarray:
    """A noise burst, the signal the delay is measured with."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(count) * 0.1


def _feed(stream: SpectralStream, signal: np.ndarray, block: int) -> np.ndarray:
    """Feed a signal to a stream in blocks and collect what comes out.

    The caller asks for the number of samples it has to give its host, on
    every call, whether a frame was ready or not.  That is the way a
    module uses the stream when it wants a continuous stream.
    """
    pieces = []
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        spec, first = stream.analyze(piece)
        pieces.append(stream.synthesize(spec, first, piece.shape[0]))
    if not pieces:
        return np.zeros((0, signal.shape[1]), dtype=np.float64)
    return np.concatenate(pieces, axis=0)


def _delay(reference: np.ndarray, answer: np.ndarray) -> int:
    """The delay the answer runs behind the reference, in samples."""
    a = np.asarray(reference, dtype=np.float64).ravel()
    b = np.asarray(answer, dtype=np.float64).ravel()
    n = min(a.size, b.size)
    if n == 0:
        return 0
    a = a[:n] - float(np.mean(a[:n]))
    b = b[:n] - float(np.mean(b[:n]))
    size = 1 << int(np.ceil(np.log2(2 * n)))
    correlation = np.fft.irfft(
        np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size
    )
    lag = int(np.argmax(np.abs(correlation)))
    if lag > size // 2:
        lag -= size
    return -lag


def _state_of(stream: SpectralStream) -> int:
    """The number of samples the stream keeps between two calls."""
    return int(
        stream._tail.shape[0]
        + stream._acc.shape[0]
        + stream._acc_w.shape[0]
        + stream._pending.shape[0]
    )


def _worst_state(block: int, signal: np.ndarray) -> int:
    """The most state the stream holds while it is fed in blocks of ``block``."""
    stream = SpectralStream(N_FFT, HOP, 1)
    worst = 0
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        spec, first = stream.analyze(piece)
        stream.synthesize(spec, first, piece.shape[0])
        worst = max(worst, _state_of(stream))
    return worst


# ---------------------------------------------------------------------------
# the stream: the delay
# ---------------------------------------------------------------------------
def test_the_stream_publishes_the_delay_it_puts_into_the_signal():
    """The stream says how late it is, so a module can declare it."""
    stream = SpectralStream(N_FFT, HOP, 1)
    assert stream.latency_samples == N_FFT, (
        f"the stream publishes {stream.latency_samples} samples of delay, it "
        f"puts {N_FFT} into the signal"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_delay_the_signal_shows_is_the_delay_the_stream_publishes(block):
    """The signal shows the delay the stream publishes, at every block size."""
    stream = SpectralStream(N_FFT, HOP, 1)
    published = stream.latency_samples
    signal = _noise(20000)
    answer = _feed(stream, signal[:, None], block)
    measured = _delay(signal, answer[:, 0])
    assert measured == published, (
        f"at {block} samples per block the signal shows a delay of {measured} "
        f"samples, the stream publishes {published}"
    )


def test_the_delay_is_the_one_a_centred_analysis_needs():
    """The delay of the stream is the delay of the shared centred engine.

    The shared engine in ``aurcore.dsp.stft`` centres its frames and
    publishes the delay that costs; the stream of the mixing module has to
    agree with it, or a module that uses both would declare two different
    numbers for the same analysis.
    """
    from aurcore.dsp.stft import StreamingSTFT

    stream = SpectralStream(N_FFT, HOP, 1)
    engine = StreamingSTFT(N_FFT, HOP, window="hann")
    assert stream.latency_samples == engine.latency_samples, (
        f"the stream publishes {stream.latency_samples} samples of delay, the "
        f"shared centred engine publishes {engine.latency_samples}"
    )


# ---------------------------------------------------------------------------
# the stream: the order of the samples
# ---------------------------------------------------------------------------
def test_the_samples_come_out_in_the_order_they_went_in():
    """A change in the signal comes out at the same place in the stream.

    The signal is a low tone for the first half and a high one for the
    second.  A stream that hands its samples out in groups, or in the
    wrong order, moves the change.
    """
    half = 4096
    t = np.arange(2 * half, dtype=np.float64) / SAMPLE_RATE
    signal = np.zeros(2 * half, dtype=np.float64)
    signal[:half] = 0.5 * np.sin(2.0 * np.pi * 400.0 * t[:half])
    signal[half:] = 0.5 * np.sin(2.0 * np.pi * 3000.0 * t[half:])

    stream = SpectralStream(N_FFT, HOP, 1)
    answer = _feed(stream, signal[:, None], 64)[:, 0]

    def band(x: np.ndarray, lo: float, hi: float) -> float:
        spectrum = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        freqs = np.fft.rfftfreq(x.size, 1.0 / SAMPLE_RATE)
        mask = (freqs >= lo) & (freqs < hi)
        return float(np.sum(spectrum[mask]))

    delay = stream.latency_samples
    early = answer[delay : delay + half - N_FFT]
    late = answer[delay + half : delay + 2 * half - N_FFT]
    assert band(early, 300.0, 500.0) > 10.0 * band(early, 2500.0, 3500.0), (
        "the first half of the output does not hold the low tone"
    )
    assert band(late, 2500.0, 3500.0) > 10.0 * band(late, 300.0, 500.0), (
        "the second half of the output does not hold the high tone"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_caller_gets_the_number_of_samples_it_asks_for(block):
    """A caller gets exactly what it asks for, on every call.

    A stream that answered with a burst, or with nothing, would force the
    module that uses it to keep a line of its own.
    """
    stream = SpectralStream(N_FFT, HOP, 2)
    signal = _noise(block * 40)[:, None] * np.ones((1, 2))
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        spec, first = stream.analyze(piece)
        out = stream.synthesize(spec, first, piece.shape[0])
        assert out.shape == piece.shape, (
            f"the stream answered {out.shape[0]} samples, the caller asked "
            f"for {piece.shape[0]}"
        )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_line_the_stream_keeps_stays_bounded(block):
    """The stream must not fall further and further behind the input.

    The line of a stream that only hands its samples out when a frame is
    ready grows by the difference between the hop and the block, every
    time a frame is ready: at 64 samples per block it grew by 192 samples
    every four blocks, and the delay the signal showed grew with it.
    """
    stream = SpectralStream(N_FFT, HOP, 1)
    signal = _noise(block * 200)[:, None]
    worst = 0
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        spec, first = stream.analyze(piece)
        stream.synthesize(spec, first, piece.shape[0])
        worst = max(worst, int(stream._pending.shape[0]))
    # the line holds the samples of one hop and the ones of the block the
    # caller is holding: it does not grow with the length of the stream
    assert worst <= HOP + block, (
        f"at {block} samples per block the line of the stream grew to {worst} "
        f"samples, it may hold {HOP + block}"
    )


# ---------------------------------------------------------------------------
# the stream: the block size
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", BLOCKS)
def test_the_output_does_not_depend_on_the_block_size(block):
    """A host that changes its block size gets the same answer.

    The stream is the shared primitive of the spectral modules, so a
    module that uses it has to give the same answer whatever the host
    chops the stream up with.
    """
    signal = _noise(8192)[:, None]
    reference = _feed(SpectralStream(N_FFT, HOP, 1), signal, BLOCKS[0])
    answer = _feed(SpectralStream(N_FFT, HOP, 1), signal, block)
    assert answer.shape == reference.shape
    difference = float(np.max(np.abs(answer - reference)))
    assert difference <= TOLERANCE, (
        f"at {block} samples per block the stream differs from the render at "
        f"{BLOCKS[0]} samples by {difference:.3e}"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_state_does_not_grow_with_the_block_length(block):
    """The state the stream keeps does not depend on the length of a block.

    A stream that held the whole block in its accumulator would need more
    memory for a longer block, and a host that hands over a long block
    would pay for it.
    """
    signal = _noise(8192)[:, None]
    reference = _worst_state(BLOCKS[0], signal)
    worst = _worst_state(block, signal)
    assert worst <= reference + HOP, (
        f"at {block} samples per block the stream kept {worst} samples of "
        f"state, at {BLOCKS[0]} samples per block it kept {reference}: the "
        f"state grew with the block"
    )
    # the analysis history, the accumulator and the line hold a window of
    # the analysis and not the length of the block
    assert worst <= 6 * N_FFT, (
        f"at {block} samples per block the stream kept {worst} samples of "
        f"state, it may keep {6 * N_FFT}"
    )


# ---------------------------------------------------------------------------
# the stream: the continuous interface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", BLOCKS)
def test_process_hands_back_the_block_it_was_given(block):
    """``process`` is the continuous interface: in, out, same length."""
    stream = SpectralStream(N_FFT, HOP, 2)
    signal = _noise(block * 20)[:, None] * np.ones((1, 2))
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        out = stream.process(piece)
        assert out.shape == piece.shape
        assert bool(np.all(np.isfinite(out)))


def test_process_can_edit_the_spectra():
    """The caller can change the spectra on the way through."""
    stream = SpectralStream(N_FFT, HOP, 1)
    signal = _noise(8192)[:, None]
    out = stream.process(signal, modify=lambda spec: spec * 0.0)
    assert out.shape == signal.shape
    assert float(np.max(np.abs(out))) == 0.0


def test_the_stream_can_be_reset():
    """A stream that is reset starts from the beginning again."""
    stream = SpectralStream(N_FFT, HOP, 1)
    signal = _noise(4096)[:, None]
    first = _feed(stream, signal, 64)
    stream.reset()
    second = _feed(stream, signal, 64)
    assert float(np.max(np.abs(first - second))) == 0.0


def test_the_stream_can_be_flushed():
    """The samples that are still in the line come out of the flush."""
    stream = SpectralStream(N_FFT, HOP, 1)
    signal = _noise(4096)[:, None]
    _feed(stream, signal, 64)
    rest = stream.flush()
    assert rest.ndim == 2
    assert rest.shape[1] == 1
    assert bool(np.all(np.isfinite(rest)))


# ---------------------------------------------------------------------------
# the module that uses the stream
# ---------------------------------------------------------------------------
def test_the_centre_extract_declares_the_delay_the_stream_publishes():
    """A module that uses the stream can declare the delay of the stream.

    The centre extract is the module the stream was built for.  It is not
    touched here: the point of the test is that the number it declares is
    the number the stream publishes, so a module that uses the stream
    never has to guess the latency.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create("AUR_CENTEREXTRACT", SAMPLE_RATE, 2)
    stream = SpectralStream(module.N_FFT, module.HOP, 2)
    assert module.latency_samples == stream.latency_samples, (
        f"the centre extract declares {module.latency_samples} samples of "
        f"latency, the stream publishes {stream.latency_samples}"
    )


# ---------------------------------------------------------------------------
# the mid / side helpers
# ---------------------------------------------------------------------------
def test_the_mid_side_round_trip_is_exact():
    """What the matrix splits, the matrix puts back together."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal((512, 2))
    left, right = to_left_right(to_mid_side(x))
    assert float(np.max(np.abs(left - x[:, 0]))) < 1.0e-15
    assert float(np.max(np.abs(right - x[:, 1]))) < 1.0e-15


def test_a_mono_block_is_treated_as_two_identical_channels():
    """A block with one channel is a block with the same channel twice."""
    x = np.arange(16, dtype=np.float64)
    mid, side = to_mid_side(x)
    assert float(np.max(np.abs(mid - x))) == 0.0
    assert float(np.max(np.abs(side))) == 0.0


def test_the_channel_ops_engine_holds_no_state():
    """The engine is a pure function of the block it is given."""
    ops = ChannelOps(SAMPLE_RATE, 2)
    rng = np.random.default_rng(5)
    x = rng.standard_normal((256, 2))
    assert float(np.max(np.abs(ops.mid(x) - ops.mid(x)))) == 0.0
    assert ops.keep_center(x, 1.0).shape == x.shape
    assert ops.keep_sides(x, 1.0).shape == x.shape


# ---------------------------------------------------------------------------
# the whole block transforms
# ---------------------------------------------------------------------------
def test_the_whole_block_transform_round_trip():
    """A tone that sits on a bin of the analysis comes back as it went in."""
    # 1500 Hz is bin 32 of a 1024 point analysis at 48 kHz
    n = 8192
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    tone = (0.5 * np.sin(2.0 * np.pi * 1500.0 * t))[:, None]
    spec = stft(tone, N_FFT, HOP)
    assert spec.shape[1] == N_FFT // 2 + 1
    back = istft(spec, N_FFT, HOP, length=n)
    assert back.shape == tone.shape
    # the edges of the block are not covered by every window, so the
    # comparison is made over the part that is
    margin = N_FFT
    error = float(np.max(np.abs(back[margin:-margin] - tone[margin:-margin])))
    assert error < 1.0e-9, f"the round trip differs by {error:.3e}"


# ---------------------------------------------------------------------------
# the module
# ---------------------------------------------------------------------------
def make_module() -> Mixing:
    """A fresh Mixing module at 48 kHz / stereo."""
    return Mixing(SAMPLE_RATE, 2)


def test_the_mixer_gives_the_input_back_at_unity():
    """100 % on both faders is the identity."""
    module = make_module()
    rng = np.random.default_rng(13)
    x = rng.standard_normal((256, 2)) * 0.1
    out = np.asarray(module.process(x), dtype=np.float64)
    assert out.shape == x.shape
    assert float(np.max(np.abs(out - x))) < 1.0e-12


def test_the_mixer_raises_both_buses_by_six_decibels():
    """200 % on both faders is +6 dB."""
    module = make_module()
    module.params.set("Left Output Mix", 200.0)
    module.params.set("Right Output Mix", 200.0)
    rng = np.random.default_rng(17)
    x = rng.standard_normal((256, 2)) * 0.1
    out = np.asarray(module.process(x), dtype=np.float64)
    ratio = float(np.max(np.abs(out)) / np.max(np.abs(x)))
    assert abs(ratio - 2.0) < 1.0e-9, f"the ratio is {ratio:.6f}, it should be 2"


def test_the_mixer_folds_a_centred_source_to_both_buses():
    """A source that is the same in both channels stays in both."""
    module = make_module()
    t = np.arange(256, dtype=np.float64) / SAMPLE_RATE
    centred = 0.5 * np.sin(2.0 * np.pi * 440.0 * t)
    x = np.column_stack([centred, centred])
    out = np.asarray(module.process(x), dtype=np.float64)
    assert float(np.max(np.abs(out[:, 0] - out[:, 1]))) < 1.0e-12


def test_the_mixer_keeps_the_length_and_stays_finite():
    """The output is as long as the input and holds no NaN."""
    module = make_module()
    rng = np.random.default_rng(19)
    x = rng.standard_normal((1024, 2)) * 0.1
    out = np.asarray(module.process(x), dtype=np.float64)
    assert out.shape == x.shape
    assert bool(np.all(np.isfinite(out)))


def test_the_mixer_leaves_an_empty_block_alone():
    """An empty block is an empty block."""
    module = make_module()
    out = np.asarray(module.process(np.zeros((0, 2))), dtype=np.float64)
    assert out.shape == (0, 2)
