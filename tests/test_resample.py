"""
tests.test_resample — the resampler: transparency, accuracy and cost.

The module used to evaluate its 96 tap Kaiser windowed sinc kernel for every
tap of every output sample, which cost about 12 ms for a 64 sample block —
nine times the realtime budget of that block.  The kernel is now evaluated
once per phase when the settings change and looked up afterwards.

It used to resample every block on its own, which cut the kernel in two: the
kernel reaches 48 samples to the right of the read position and 47 to the
left, so the first 47 and the last 48 samples of every block were computed
from a truncated kernel.  A constant of 0.5 came out as 0.5647 at the start
of a block and as 0.3056 at its end.

The resampler is now a streaming filter: it keeps the history of the input
and delays its output by the look ahead the kernel needs, so that every
output is computed from the whole kernel.  The delay is the latency of the
module and it is declared.

The tests below hold four things:

* **the transparency** — a constant goes in and the same constant comes out,
  at every block size and at the first and the last sample of a block,
* **the latency** — the module declares the look ahead it needs, and the
  delay it really has is the delay it declares,
* **the coherence** — one block and many blocks give the same answer, and
* **the accuracy and the cost** — the phase table must reproduce the exact
  resampler at least as well as the implementation it replaced, and a 64
  sample block must fit into the realtime budget of that block.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PERF_DIR = _REPO_ROOT / "qa" / "performance"

# The project has TWO modules called "harness" (the performance one and the
# survey one).  A plain import takes whichever one the module cache already
# holds, so the harness is loaded by path under a name of its own.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "aurperf_harness", _PERF_DIR / "harness.py"
)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

from aurcore.api import Engine  # noqa: E402
from aurcore.modules.resample import Resample, SincResampler  # noqa: E402

#: The sample rate the module runs at in the tests.
SAMPLE_RATE = 48_000

#: The realtime budget of one 64 sample block, in microseconds.
BUDGET_US = harness.block_budget_us(64, SAMPLE_RATE)

#: How many 1024 point transforms the budget is worth on the reference
#: machine of qa/performance/REPORT.md.  One such transform costs 13.3 to
#: 15.6 us there, so the budget of a 64 sample block is worth 85 to 100 of
#: them.  The smaller number is used, so the test is the strict one.
BUDGET_IN_TRANSFORMS = 85.0

#: The error the phase table may not exceed, in dB below the peak of the
#: reference.  The table holds the exact kernel values, so the error is the
#: rounding of the sums, which is around -300 dB.
ERROR_LIMIT_DB = -200.0

#: The slack the comparison against the old implementation gets, in dB.
#: Both implementations sit on the rounding noise floor, so their errors are
#: of the same order and a strict comparison would be a coin toss.
COMPARISON_SLACK_DB = 6.0

#: How far a constant may move on its way through the resampler.  The
#: weights of every phase sum to one, so the error is the rounding of the
#: sums: 1e-12 is 240 dB below the signal.
TRANSPARENCY_LIMIT = 1.0e-12

#: The ratios the module is used with, and the phase pattern they produce.
#: The rate pairs are the ones the module offers; the last one is the
#: irrational ratio the pitch shifters use, which has no phase pattern.
RATIOS: dict[str, float] = {
    "48000 to 48000": 1.0,
    "48000 to 44100": 44100.0 / 48000.0,
    "48000 to 22050": 22050.0 / 48000.0,
    "48000 to 32000": 32000.0 / 48000.0,
    "48000 to 96000": 96000.0 / 48000.0,
    "48000 to 192000": 192000.0 / 48000.0,
    "44100 to 48000": 48000.0 / 44100.0,
    "irrational": 2.0 ** (-3.0 / 12.0),
}

#: The rate pairs the module offers, with the delay each of them needs.
#: The delay is the look ahead of the kernel (48 input samples) counted in
#: the samples of the output, so it grows with the ratio.
RATE_PAIRS: dict[str, int] = {
    "48000": 48,
    "44100": 45,
    "22050": 23,
    "32000": 32,
    "88200": 89,
    "96000": 96,
    "192000": 192,
}

#: The length of the signal the accuracy is measured on.  It is longer than
#: the largest phase count (160), so every phase of every ratio is used.
SIGNAL_LENGTH = 1024


# ---------------------------------------------------------------------------
# The references
# ---------------------------------------------------------------------------
def _signal(length: int = SIGNAL_LENGTH, channels: int = 1) -> np.ndarray:
    """Return a deterministic test signal."""
    rng = np.random.default_rng(20_260_101)
    signal = rng.standard_normal((length, channels)) * 0.25
    # a tone and an impulse on top, so the kernel sees a sharp edge too
    t = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    signal[:, 0] += 0.4 * np.sin(2.0 * np.pi * 1000.0 * t)
    signal[length // 3, 0] += 0.8
    return signal


def _original_resample(buf: np.ndarray, resampler: SincResampler) -> np.ndarray:
    """Resample the way the module did before the phase table existed.

    The kernel is evaluated for every tap of every output sample and the
    read position is the float division ``n / ratio``.  This is the
    implementation the phase table has to match.
    """
    arr = np.asarray(buf, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    n_in = arr.shape[0]
    n_out = int(round(n_in * resampler.ratio))
    if n_out <= 0:
        return np.zeros((0, arr.shape[1]), dtype=np.float64)
    positions = np.arange(n_out, dtype=np.float64) / resampler.ratio
    base = np.floor(positions).astype(np.int64)
    frac = positions - base
    a = resampler.half_width
    out = np.zeros((n_out, arr.shape[1]), dtype=np.float64)
    for offset in range(-a + 1, a + 1):
        index = base + offset
        valid = (index >= 0) & (index < n_in)
        if not np.any(valid):
            continue
        weight = resampler._weight(frac - offset)
        out[valid] += weight[valid, None] * arr[index[valid]]
    return out


def _exact_resample(buf: np.ndarray, resampler: SincResampler) -> np.ndarray:
    """Resample with the exact read positions and correctly rounded sums.

    The read position of output sample ``n`` is ``n / ratio``; when the
    ratio is rational it is computed in integer arithmetic here, and the
    taps of one output sample are summed with ``math.fsum``, which rounds the
    sum correctly.  The result is the exact answer of the module's own
    filter, so the error of an implementation against it is that
    implementation's error.
    """
    arr = np.asarray(buf, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    n_in = arr.shape[0]
    n_out = int(round(n_in * resampler.ratio))
    if n_out <= 0:
        return np.zeros((0, arr.shape[1]), dtype=np.float64)

    from fractions import Fraction

    parts = Fraction(resampler.ratio).limit_denominator(4096)
    up, down = parts.numerator, parts.denominator
    exact = abs(up / down - resampler.ratio) <= 8.0 * math.ulp(resampler.ratio)

    offsets = list(range(-resampler.half_width + 1, resampler.half_width + 1))
    out = np.zeros((n_out, arr.shape[1]), dtype=np.float64)
    for index in range(n_out):
        if exact:
            base = (index * down) // up
            frac = (index * down % up) / float(up)
        else:
            position = index / resampler.ratio
            base = int(math.floor(position))
            frac = position - base
        weights = resampler._weight(np.asarray([frac - off for off in offsets]))
        for channel in range(arr.shape[1]):
            column = arr[:, channel]
            terms = []
            for off, weight in zip(offsets, weights):
                position = base + off
                if 0 <= position < n_in:
                    terms.append(float(weight) * float(column[position]))
            out[index, channel] = math.fsum(terms)
    return out


def _error_db(measured: np.ndarray, reference: np.ndarray) -> float:
    """Return the peak error of ``measured`` in dB below the reference peak."""
    diff = np.asarray(measured, dtype=np.float64) - np.asarray(reference, dtype=np.float64)
    if diff.size == 0:
        return -math.inf
    peak = float(np.max(np.abs(reference)))
    worst = float(np.max(np.abs(diff)))
    if worst <= 0.0:
        return -math.inf
    if peak <= 0.0:
        return 0.0
    return 20.0 * math.log10(worst / peak)


def _stream(module: Resample, signal: np.ndarray, block: int) -> np.ndarray:
    """Feed a signal through a module, block by block, and collect the output."""
    out: list[np.ndarray] = []
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        out.append(np.asarray(module.process(piece)))
    return np.concatenate(out) if out else np.zeros((0, signal.shape[1]))


# ---------------------------------------------------------------------------
# The cost
# ---------------------------------------------------------------------------
def _time_us(call, block: np.ndarray, repeats: int = 9, warmup: int = 3) -> float:
    """Return the fastest of ``repeats`` timed calls, in microseconds.

    The fastest block is used, because a shared machine makes the slow ones
    slow for reasons that have nothing to do with the module.
    """
    blocks = [block.copy() for _ in range(warmup + repeats)]
    times: list[float] = []
    for index, item in enumerate(blocks):
        start = time.perf_counter_ns()
        call(item)
        elapsed = time.perf_counter_ns() - start
        if index >= warmup:
            times.append(elapsed / 1000.0)
    return min(times)


def _transform_us() -> float:
    """Return the cost of one 1024 point transform on this machine, in us."""
    return harness.reference_fft_us(1024, repeats=100)


# ---------------------------------------------------------------------------
# The transparency
# ---------------------------------------------------------------------------
def _ramp(module: Resample) -> int:
    """The number of samples the filter needs to settle.

    The delay is the look ahead of the kernel; the rest is the left half of
    the kernel, which has no samples to read at the start of a stream.
    """
    resampler = module._get_resampler()
    return module.latency_samples + int(math.ceil(resampler.half_width * resampler.ratio))


@pytest.mark.parametrize("block", [64, 128, 256, 512])
def test_a_constant_comes_out_unchanged(block: int) -> None:
    """A constant goes in and the same constant comes out.

    The kernel of every phase sums to one, so the resampler is transparent:
    the first and the last sample of every block are the constant, not a
    number that the cut kernel produced.
    """
    module = Resample(SAMPLE_RATE, 2)
    constant = np.full((block * 6, 2), 0.5, dtype=np.float64)
    stream = _stream(module, constant, block)

    # the first outputs of a stream are the ramp of the filter: the delay and
    # the left half of the kernel have no samples to read yet
    ramp = _ramp(module)
    assert stream.shape[0] == constant.shape[0], (
        "a block of 64 samples has to come back as 64 samples at the ratio "
        f"of one, not {stream.shape[0]}"
    )

    checked = 0
    for index in range(constant.shape[0] // block):
        start = index * block
        if start < ramp:
            continue
        first = float(stream[start, 0])
        last = float(stream[start + block - 1, 0])
        assert abs(first - 0.5) <= TRANSPARENCY_LIMIT, (
            f"the first sample of block {index} is {first!r}, not 0.5"
        )
        assert abs(last - 0.5) <= TRANSPARENCY_LIMIT, (
            f"the last sample of block {index} is {last!r}, not 0.5"
        )
        checked += 1
    assert checked >= 2, "the test did not look at a single settled block"


@pytest.mark.parametrize("pre_ringing", [0.0, 1.0])
def test_a_constant_comes_out_unchanged_with_both_kernels(pre_ringing: float) -> None:
    """Both kernels are transparent: the symmetric one and the minimum phase one."""
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Pre Ringing", pre_ringing)
    block = 128
    constant = np.full((block * 6, 2), 0.5, dtype=np.float64)
    stream = _stream(module, constant, block)
    settled = stream[_ramp(module) :]
    assert np.all(np.abs(settled - 0.5) <= TRANSPARENCY_LIMIT), (
        f"the settled output of the constant is {settled.min():.17g} to "
        f"{settled.max():.17g}, not 0.5"
    )


@pytest.mark.parametrize("target", sorted(RATE_PAIRS))
def test_a_constant_comes_out_unchanged_at_every_rate(target: str) -> None:
    """The conversion is transparent at every rate the module offers.

    The number of output samples of a block is the number of read positions
    that fall into it, so the count varies by one sample from block to
    block; the *value* of every one of them is the constant.
    """
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Sample Rate", target)
    block = 256
    constant = np.full((block * 8, 2), 0.5, dtype=np.float64)
    stream = _stream(module, constant, block)
    settled = stream[_ramp(module) :]
    assert settled.size > 0
    worst = float(np.max(np.abs(settled - 0.5)))
    assert worst <= TRANSPARENCY_LIMIT, (
        f"the conversion to {target} Hz moved the constant by {worst:.3g}"
    )


def test_a_tone_comes_out_as_the_ideal_resample() -> None:
    """A tone comes back the way the kernel says it should.

    The ideal resample of the tone is computed with the exact kernel and
    exact sums; the module has to reproduce it to the rounding.
    """
    for pre_ringing in (0.0, 1.0):
        module = Resample(SAMPLE_RATE, 1)
        module.params.set("Pre Ringing", pre_ringing)
        count = 4096
        t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
        tone = (0.5 * np.sin(2.0 * np.pi * 1000.0 * t))[:, None]

        resampler = module._get_resampler()
        ideal = _exact_resample(tone, resampler)
        out = np.asarray(module.process(tone))
        delay = module.latency_samples
        length = min(ideal.shape[0], out.shape[0] - delay)
        assert length > 0
        error = _error_db(out[delay : delay + length], ideal[:length])
        assert error <= ERROR_LIMIT_DB, (
            f"the tone with the pre ringing {pre_ringing} is {error:.1f} dB off "
            f"the ideal resample, the limit is {ERROR_LIMIT_DB:.1f} dB"
        )


def test_the_symmetric_kernel_delays_the_tone_by_the_declared_latency() -> None:
    """The linear phase kernel is a pure delay, and the delay is declared.

    The minimum phase kernel is not: its phase is not linear, so a tone
    comes back a sample or two away from the declared delay.  That is the
    trade the "Pre Ringing" control offers.
    """
    module = Resample(SAMPLE_RATE, 1)
    module.params.set("Pre Ringing", 1.0)
    count = 8192
    t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    tone = (0.5 * np.sin(2.0 * np.pi * 1000.0 * t))[:, None]
    out = _stream(module, tone, 256)

    delay = module.latency_samples
    index = np.arange(delay + 200, count - 64, dtype=np.int64)
    ideal = 0.5 * np.sin(2.0 * np.pi * 1000.0 * (index - delay) / SAMPLE_RATE)
    measured = out[index, 0]
    worst = float(np.max(np.abs(measured - ideal)))
    assert worst <= 1.0e-9, (
        f"the tone came back {worst:.3g} away from the delayed tone, the "
        "linear phase kernel is a pure delay"
    )


# ---------------------------------------------------------------------------
# The latency
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target,delay", sorted(RATE_PAIRS.items()))
def test_the_module_declares_the_look_ahead_it_needs(target: str, delay: int) -> None:
    """The module declares the delay the kernel needs.

    The kernel reaches 48 input samples to the right of the read position,
    so the output has to be held back by that much.  The delay is counted in
    the samples of the output, which is the unit a host sees.
    """
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Sample Rate", target)
    assert module.latency_samples == delay, (
        f"the conversion to {target} Hz declares a latency of "
        f"{module.latency_samples} samples, the look ahead of the kernel "
        f"needs {delay}"
    )


def test_the_declared_latency_is_the_delay_the_module_has() -> None:
    """The delay the module reports is the delay it really adds.

    An impulse goes in and the peak of the answer comes out exactly
    ``latency_samples`` later.
    """
    module = Resample(SAMPLE_RATE, 2)
    block = 256
    impulse = np.zeros((block * 4, 2), dtype=np.float64)
    impulse[0, 0] = 1.0
    stream = _stream(module, impulse, block)
    peak = int(np.argmax(np.abs(stream[:, 0])))
    assert peak == module.latency_samples, (
        f"the impulse came back after {peak} samples, the module declares "
        f"{module.latency_samples}"
    )


def test_the_engine_reports_the_latency_of_the_chain() -> None:
    """The engine adds the latency of the module to the chain."""
    from aurcore.api import create_engine

    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_RESAMPLE")
    assert engine.latency_samples == 48


# ---------------------------------------------------------------------------
# The coherence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", [16, 64, 128, 512])
def test_one_block_and_many_blocks_give_the_same_answer(block: int) -> None:
    """The resampler counts from the start of the stream, not of the block.

    One block of 1024 samples and sixteen blocks of 64 samples have to give
    the same output, sample for sample.
    """
    signal = _signal(1024, 2)
    whole = Resample(SAMPLE_RATE, 2)
    one = np.asarray(whole.process(signal))

    pieces = Resample(SAMPLE_RATE, 2)
    many = _stream(pieces, signal, block)

    assert one.shape == many.shape, (
        f"one block gave {one.shape[0]} samples, {1024 // block} blocks of "
        f"{block} gave {many.shape[0]}"
    )
    worst = float(np.max(np.abs(one - many)))
    assert worst <= 1.0e-12, (
        f"one block and {1024 // block} blocks of {block} samples differ by "
        f"{worst:.3g}"
    )


def test_the_stream_survives_a_change_of_the_block_size() -> None:
    """The output does not depend on how the stream is cut up.

    The module is fed blocks of different sizes, and the result has to be
    the same as when the whole signal is fed in one block.
    """
    signal = _signal(1024, 2)
    whole = Resample(SAMPLE_RATE, 2)
    reference = np.asarray(whole.process(signal))

    mixed = Resample(SAMPLE_RATE, 2)
    out: list[np.ndarray] = []
    for size in (100, 7, 333, 64, 520):
        out.append(np.asarray(mixed.process(signal[:size])))
        signal = signal[size:]
    stream = np.concatenate(out)

    assert stream.shape[0] == reference.shape[0]
    worst = float(np.max(np.abs(stream - reference)))
    assert worst <= 1.0e-12, f"the stream of mixed blocks differs by {worst:.3g}"


@pytest.mark.parametrize("target", ["48000", "44100", "22050", "96000"])
def test_the_module_is_block_coherent_at_every_rate(target: str) -> None:
    """The module is block coherent.

    The rule of the family is at the output of the product and not inside the
    module, so the measurement is over the module alone: the resampler
    counts its read positions from the start of the stream, and the same
    output sample is computed from the same kernel and the same input
    whether the host hands it the whole stream in one block or in many.
    """
    signal = _signal(1024, 2)
    whole = Resample(SAMPLE_RATE, 2)
    whole.params.set("Sample Rate", target)
    one = np.asarray(whole.process(signal))

    for block in (16, 64, 128, 512):
        pieces = Resample(SAMPLE_RATE, 2)
        pieces.params.set("Sample Rate", target)
        many = _stream(pieces, signal, block)
        assert one.shape == many.shape, (
            f"one block gave {one.shape[0]} samples to {target} Hz, "
            f"{1024 // block} blocks of {block} gave {many.shape[0]}"
        )
        worst = float(np.max(np.abs(one - many)))
        assert worst <= 1.0e-12, (
            f"to {target} Hz, one block and {1024 // block} blocks of {block} "
            f"samples differ by {worst:.3g}"
        )


def test_the_overshoot_is_clipped_at_the_output_in_every_block_size() -> None:
    """A band limited square wave overshoots, and the chain clips it.

    The Gibbs ripple of a band limited square wave is 1.18 by
    construction, so the output of the resampler goes over the digital
    ceiling.  The rule of the family is at the output of the product, so the
    module's own answer is left alone and the chain clips the overshoot.
    The clip is a per sample operation, so the room it makes is the same
    however the host chops the stream into.
    """
    t = np.arange(1024, dtype=np.float64) / SAMPLE_RATE
    square = np.repeat(np.sign(np.sin(2.0 * np.pi * 220.0 * t))[:, None], 2, axis=1)

    whole = Resample(SAMPLE_RATE, 2)
    one = np.asarray(whole.process(square))
    assert float(np.max(np.abs(one))) > 1.0, (
        "the module's own answer did not overshoot: the rule is inside the "
        "module again"
    )

    engine = Engine(sample_rate=SAMPLE_RATE, channels=2)
    engine.add_module("AUR_RESAMPLE")
    clipped = np.asarray(engine.process(square))
    assert float(np.max(np.abs(clipped))) <= 1.0 + 1e-9, (
        "the chain did not clip the overshoot"
    )

    for block in (16, 64, 128, 512):
        engine = Engine(sample_rate=SAMPLE_RATE, channels=2)
        engine.add_module("AUR_RESAMPLE")
        pieces = [
            np.asarray(engine.process(square[start : start + block]))
            for start in range(0, square.shape[0], block)
        ]
        many = np.concatenate(pieces, axis=0)
        assert many.shape == clipped.shape
        worst = float(np.max(np.abs(clipped - many)))
        assert worst <= 1.0e-12, (
            f"one block and {1024 // block} blocks of {block} samples differ "
            f"by {worst:.3g}"
        )


def test_the_resampler_itself_is_block_coherent() -> None:
    """The resampler on its own does not depend on the block size either.

    This is the measurement under the rule of the family: the resampler
    counts its read positions from the start of the stream, so the same
    output sample is computed from the same kernel and the same input
    samples, whatever the size of the block it arrives in.
    """
    signal = _signal(1024, 2)
    whole = SincResampler(1.0, SAMPLE_RATE)
    one = np.asarray(whole.process(signal))

    for block in (16, 64, 128, 512):
        pieces = SincResampler(1.0, SAMPLE_RATE)
        many = np.concatenate(
            [
                np.asarray(pieces.process(signal[start : start + block]))
                for start in range(0, signal.shape[0], block)
            ],
            axis=0,
        )
        worst = float(np.max(np.abs(one - many)))
        assert worst <= 1.0e-12, (
            f"one block and {1024 // block} blocks of {block} samples differ "
            f"by {worst:.3g}"
        )


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------
def test_the_module_converts_the_block_length() -> None:
    """The module returns about as many samples as the target rate asks for.

    The count of a block is the number of read positions that fall into it,
    so it is one sample more or less than the plain multiplication; over a
    whole signal the two agree.
    """
    for target in ("22050", "44100", "96000"):
        module = Resample(SAMPLE_RATE, 2)
        module.params.set("Sample Rate", target)
        block = np.zeros((256, 2), dtype=np.float64)
        out = module.process(block)
        expected = 256 * int(target) / SAMPLE_RATE
        assert abs(out.shape[0] - expected) <= 2.0, (
            f"the conversion to {target} Hz gave {out.shape[0]} samples for "
            f"256 input samples, the rate asks for {expected:.1f}"
        )


def test_the_kernel_is_built_once_and_not_per_block(monkeypatch) -> None:
    """The kernel is evaluated when the settings change, not per block.

    This is the structural guard against the defect the performance report
    found: the old implementation evaluated the Kaiser window for every tap
    of every block.
    """
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Sample Rate", "44100")
    signal = np.zeros((64, 2), dtype=np.float64)
    module.process(signal)  # the kernel is built here

    calls: list[int] = []
    original = np.i0

    def counting(values):
        calls.append(int(np.size(values)))
        return original(values)

    monkeypatch.setattr(np, "i0", counting)
    try:
        module.process(signal)
    finally:
        monkeypatch.undo()
    assert calls == [], f"the kernel was evaluated again: {calls}"


@pytest.mark.parametrize("name", sorted(RATIOS))
def test_the_phase_table_reproduces_the_exact_resampler(name: str) -> None:
    """The phase table matches the exact resampler to the rounding error."""
    ratio = RATIOS[name]
    signal = _signal()
    resampler = SincResampler(ratio, SAMPLE_RATE)
    reference = _exact_resample(signal, resampler)
    measured = resampler.resample(signal)
    error = _error_db(measured, reference)
    assert error <= ERROR_LIMIT_DB, (
        f"ratio {name} ({ratio}) is {error:.1f} dB off the exact resampler, "
        f"the limit is {ERROR_LIMIT_DB:.1f} dB"
    )


@pytest.mark.parametrize("name", sorted(RATIOS))
def test_the_streaming_path_reproduces_the_exact_resampler(name: str) -> None:
    """The streaming path is as exact as the stateless one.

    The output of the streaming path is the ideal resample of the signal,
    delayed by the look ahead of the kernel, so the two have to agree once
    the delay is taken off.
    """
    ratio = RATIOS[name]
    signal = _signal()
    resampler = SincResampler(ratio, SAMPLE_RATE)
    reference = _exact_resample(signal, resampler)

    out = np.asarray(resampler.process(signal))
    delay = resampler.delay
    count = min(reference.shape[0], out.shape[0] - delay)
    assert count > 0, "the streaming path produced nothing to compare"
    error = _error_db(out[delay : delay + count], reference[:count])
    assert error <= ERROR_LIMIT_DB, (
        f"ratio {name} ({ratio}) is {error:.1f} dB off the exact resampler, "
        f"the limit is {ERROR_LIMIT_DB:.1f} dB"
    )


@pytest.mark.parametrize("name", sorted(RATIOS))
def test_the_phase_table_is_not_worse_than_the_old_implementation(name: str) -> None:
    """The quality did not drop: the table is at least as good as the old code.

    The old implementation is measured against the same exact resampler, so
    the two numbers are comparable.
    """
    ratio = RATIOS[name]
    signal = _signal()
    resampler = SincResampler(ratio, SAMPLE_RATE)
    reference = _exact_resample(signal, resampler)
    old_error = _error_db(_original_resample(signal, resampler), reference)
    new_error = _error_db(resampler.resample(signal), reference)
    assert new_error <= old_error + COMPARISON_SLACK_DB, (
        f"ratio {name} ({ratio}) got worse: {old_error:.1f} dB before, "
        f"{new_error:.1f} dB now"
    )


@pytest.mark.parametrize("name", sorted(RATIOS))
def test_every_phase_of_the_ratio_is_exercised(name: str) -> None:
    """The accuracy test really covers every phase of the ratio."""
    ratio = RATIOS[name]
    resampler = SincResampler(ratio, SAMPLE_RATE)
    if resampler.phases == 0:
        pytest.skip("this ratio has no phase pattern")
    n_out = int(round(SIGNAL_LENGTH * ratio))
    m = np.arange(n_out, dtype=np.int64)
    _base, phase = resampler._positions(m)
    assert int(np.unique(phase).size) == resampler.phases, (
        f"ratio {name} uses {np.unique(phase).size} of {resampler.phases} phases"
    )


@pytest.mark.parametrize("target", ["48000", "44100"])
def test_a_block_fits_into_the_realtime_budget(target: str) -> None:
    """A 64 sample block costs a fraction of the budget of that block.

    The cost is measured in 1024 point transforms, the yardstick of the
    performance report: the budget of a 64 sample block is worth about 90
    such transforms on the reference machine, and the ratio between the
    module and the transform does not change with the speed of the machine.
    """
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Sample Rate", target)
    block = harness.make_test_signal(64, SAMPLE_RATE, 2)
    module.process(block)  # warm up: the kernel is built here
    block_us = _time_us(module.process, block)
    transform_us = _transform_us()
    transforms = block_us / transform_us
    budget_transforms = BUDGET_US / transform_us
    assert transforms <= min(BUDGET_IN_TRANSFORMS, budget_transforms), (
        f"a 64 sample block to {target} Hz costs {block_us:.0f} us "
        f"({transforms:.1f} transforms), the budget is {BUDGET_US:.0f} us "
        f"({budget_transforms:.1f} transforms)"
    )


def test_the_cost_does_not_grow_with_the_block_size() -> None:
    """The cost per sample must not grow when the block grows.

    The resampler has to convert every sample, so its cost is proportional
    to the block size.  What must not happen is a fixed cost per block that
    the block size does not amortise: the old implementation paid the same
    price for 64 and for 512 samples.
    """
    module = Resample(SAMPLE_RATE, 2)
    module.params.set("Sample Rate", "44100")
    transform_us = _transform_us()
    per_sample: list[float] = []
    for size in (64, 128, 256, 512):
        block = harness.make_test_signal(size, SAMPLE_RATE, 2)
        module.process(block)
        block_us = _time_us(module.process, block)
        per_sample.append(block_us / transform_us / size)
    # the per sample cost must not grow: the fixed part of a block gets
    # smaller, not bigger, when the block gets longer
    assert per_sample[-1] <= per_sample[0] * 1.5, (
        f"the cost per sample grew: {per_sample[0]:.4f} transforms at 64 "
        f"samples, {per_sample[-1]:.4f} at 512"
    )


@pytest.mark.parametrize("name", sorted(RATIOS))
def test_the_output_is_finite_and_the_right_length(name: str) -> None:
    """Whatever the ratio is, the output is finite and has the right length."""
    ratio = RATIOS[name]
    resampler = SincResampler(ratio, SAMPLE_RATE)
    block = _signal(256, 2)
    out = resampler.resample(block)
    assert out.shape == (int(round(256 * ratio)), 2)
    assert np.all(np.isfinite(out))
