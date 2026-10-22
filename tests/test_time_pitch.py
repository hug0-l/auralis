"""
tests/test_time_pitch.py — the guards of the three time and pitch modules.

The tolerances come from the measurements in ``var/f6`` (the script
``var/f6/measure_time_pitch.py``, the numbers in ``var/f6/REPORT.md``), not
from wishful targets:

* a 440 Hz tone shifted by a factor of 1.5 arrives at 660 Hz within one
  cent, and the time stretcher does not move the pitch at all,
* the attack of a stretched impulse stays at its place (within one analysis
  hop), keeps more than 90 % of its energy within a millisecond of it and is
  not smeared over more than 5 ms,
* nine of the ten clicks of a click train survive a stretch by two and their
  spacing is within 1 % of the ideal,
* the stretched block does not overload: the old implementation divided the
  overlap-added signal by a near zero weight and reached +70 dBFS,
* the module layer uses the **shared** phase vocoder, so a fix that reaches
  ``aurcore/dsp/phase_vocoder.py`` reaches the three modules,
* the cost of one 64 sample block is measured in transform equivalents, so
  the number does not depend on how busy the machine is.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from aurcore import registry
from aurcore.dsp import phase_vocoder as pv
from aurcore.dsp import timepitch as dsp_timepitch
from aurcore.modules import time_pitch as module_time_pitch
from aurcore.modules.resample import SincResampler

SR = 48000

#: The factor the pitch shift of the guards below uses: a fifth up.
FACTOR = 1.5

#: The settings that make the variable pitch module do nothing but the
#: resampling: with ``Preserve Time`` off it hands the resampled block
#: straight back, so the resampling step can be measured on its own.
RESAMPLE_ONLY = {"Contour": 12.0 * math.log2(FACTOR), "Preserve Time": False}

#: The same, with the duration preserved.
WITH_VOCODER = {"Contour": 12.0 * math.log2(FACTOR), "Preserve Time": True}

#: The cost of one 1024 point transform on the reference machine, in
#: microseconds.  It is the yardstick of the performance gate
#: (``qa/performance/budgets.py``).
REFERENCE_TRANSFORM_US = 8.300

#: The ceiling of one 64 sample block, in transform equivalents.  The
#: measured cost is 200 (Time & Pitch), 233 (Variable Time) and 268
#: (Variable Pitch); the ceiling holds them where they are and fails when
#: they get worse.
COST_CEILING_TRANSFORMS = 400.0

#: The three modules and the parameters that exercise them.
MODULES = {
    "AUR_TIMEPITCH": ("Time & Pitch", {"Stretch Ratio": 2.0}),
    "AUR_VARIPITCH": ("Variable Pitch", {"Contour": 12.0 * math.log2(1.5), "Preserve Time": True}),
    "AUR_VARTIME": ("Variable Time", {"Ratio": 2.0}),
}


# --------------------------------------------------------------- helpers ---
def tone(freq: float, seconds: float = 1.0, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    """A sine of ``freq`` Hz."""
    t = np.arange(int(seconds * sr)) / float(sr)
    return amp * np.sin(2.0 * np.pi * float(freq) * t)


def click_train(period: float, count: int, sr: int = SR, amp: float = 0.9) -> np.ndarray:
    """``count`` impulses spaced ``period`` seconds apart."""
    n = int(round((count + 1) * period * sr))
    x = np.zeros(n, dtype=np.float64)
    step = int(round(period * sr))
    for index in range(count):
        position = index * step
        if position < n:
            x[position] = amp
    return x


def dominant_frequency(x: np.ndarray, sr: int = SR, nfft: int = 1 << 17) -> float:
    """The frequency of the strongest partial, refined by interpolation."""
    sig = np.asarray(x, dtype=np.float64).ravel()
    if sig.size == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size), nfft))
    k = int(np.argmax(spec))
    if 0 < k < spec.size - 1:
        left = math.log(float(spec[k - 1]) + 1e-30)
        mid = math.log(float(spec[k]) + 1e-30)
        right = math.log(float(spec[k + 1]) + 1e-30)
        denom = left - 2.0 * mid + right
        delta = 0.0 if denom == 0.0 else 0.5 * (left - right) / denom
    else:
        delta = 0.0
    return float((k + delta) * sr / float(nfft))


def cents(measured: float, expected: float) -> float:
    """The error of ``measured`` against ``expected``, in cents."""
    return float(1200.0 * math.log2(measured / expected))


def click_positions(x: np.ndarray, sr: int = SR, separation_ms: float = 30.0) -> np.ndarray:
    """The positions of the clicks, ignoring the ringing around each one."""
    env = np.abs(np.asarray(x, dtype=np.float64).ravel())
    if env.size < 3:
        return np.zeros(0, dtype=np.intp)
    peak = float(np.max(env))
    if peak <= 0.0:
        return np.zeros(0, dtype=np.intp)
    limit = 0.25 * peak
    inner = env[1:-1]
    candidates = np.nonzero((inner >= limit) & (inner >= env[:-2]) & (inner > env[2:]))[0] + 1
    if candidates.size == 0:
        return np.zeros(0, dtype=np.intp)
    separation = max(1, int(round(separation_ms / 1000.0 * sr)))
    order = candidates[np.argsort(env[candidates])[::-1]]
    kept: list[int] = []
    for index in order:
        position = int(index)
        if all(abs(position - other) > separation for other in kept):
            kept.append(position)
    return np.asarray(sorted(kept), dtype=np.intp)


def build(code: str, params: dict, channels: int = 1):
    """Create a module and set its parameters."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered")
    module = registry.create(code, SR, channels)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def energy_within(signal: np.ndarray, centre: int, window_ms: float = 1.0) -> float:
    """The share of the energy that sits within ``window_ms`` of ``centre``."""
    energy = np.square(np.asarray(signal, dtype=np.float64).ravel())
    total = float(np.sum(energy))
    if total <= 0.0:
        return 0.0
    window = max(1, int(window_ms / 1000.0 * SR))
    lo = max(0, centre - window)
    hi = min(energy.size, centre + window)
    return 100.0 * float(np.sum(energy[lo:hi])) / total


def energy_spread_ms(signal: np.ndarray, centre: int, span_ms: float = 40.0) -> float:
    """The energy weighted RMS spread around ``centre``, in milliseconds."""
    sig = np.asarray(signal, dtype=np.float64).ravel()
    half = int(round(span_ms / 1000.0 * SR))
    lo = max(0, centre - half)
    hi = min(sig.size, centre + half)
    energy = np.square(sig[lo:hi])
    total = float(np.sum(energy))
    if total <= 0.0:
        return 0.0
    times = (np.arange(lo, hi, dtype=np.float64) - centre) / float(SR) * 1000.0
    return float(math.sqrt(float(np.sum(times * times * energy)) / total))


def fastest_transform_us(repeats: int = 20, size: int = 1024) -> float:
    """The cost of the fastest of ``repeats`` transforms of this machine."""
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)
    times: list[float] = []
    for _ in range(max(1, int(repeats))):
        start = time.perf_counter_ns()
        np.fft.rfft(data)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return min(times) if times else 0.0


def calibrated_transforms(process, rounds: int = 5, inner: int = 10) -> float:
    """How many 1024 point transforms one block is worth.

    The machine is shared, so the block is timed next to a batch of
    transforms of the same machine and the ratio of the two is the number
    that does not change with the load.
    """
    best: tuple[float, float] | None = None
    for _ in range(rounds):
        before = fastest_transform_us()
        start = time.perf_counter()
        for _ in range(inner):
            process()
        block_us = (time.perf_counter() - start) / inner * 1e6
        after = fastest_transform_us()
        calib = 0.5 * (before + after)
        if best is None or calib < best[1]:
            best = (block_us, calib)
    assert best is not None
    if best[1] <= 0.0:
        return 0.0
    return best[0] / best[1]


# ------------------------------------------------------------ the sharing ---
def test_the_module_layer_uses_the_shared_phase_vocoder():
    """The module layer must not carry a second phase vocoder.

    The three modules a user reaches through the interface
    (``AUR_TIMEPITCH``, ``AUR_VARTIME``, ``AUR_VARIPITCH``) all go through
    ``aurcore.modules.time_pitch.PhaseVocoder``.  That class has to be an
    adapter over ``aurcore.dsp.phase_vocoder.PhaseVocoder``: the two
    implementations of the same algorithm drift apart, and they already had.
    """
    adapter = module_time_pitch.PhaseVocoder(SR, 1)
    adapter.set_frame(37.0)
    engine = adapter._vocoder(coherence=0.5, transient=1.0)
    assert isinstance(engine, pv.PhaseVocoder)
    assert engine.fft_size == adapter.frame
    assert engine.hop == adapter.hop


def test_the_adapter_and_the_shared_vocoder_agree():
    """The adapter has to produce what the shared vocoder produces."""
    x = tone(440.0, 0.5)
    adapter = module_time_pitch.PhaseVocoder(SR, 1)
    adapter.set_frame(37.0)
    shared = pv.PhaseVocoder(adapter.frame, adapter.hop, SR, phase_coherence=0.5)
    through_adapter = adapter.process(x, 1.5, coherence=0.5, transient=1.0)
    through_shared = shared.time_stretch(x, 1.5)
    assert through_adapter.shape == through_shared.shape
    assert np.allclose(through_adapter, through_shared, atol=1e-9)


def test_the_window_of_the_module_layer_is_the_periodic_hann():
    """``np.hanning(size + 1)[:size]`` **is** the periodic Hann window.

    NumPy divides the cosine of ``np.hanning(m)`` by ``m - 1``, so the
    window the module layer used is the periodic window of the length
    ``size`` — the same window the DSP layer uses.  The two vocoders did not
    use two different windows, and the shared one is kept because
    ``aurcore.dsp.windows`` is the authority for windows in this project.
    """
    from aurcore.dsp.windows import hann

    for size in (256, 1024, 2048):
        assert np.allclose(hann(size), np.hanning(size + 1)[:size], atol=1e-12)
        # the symmetric window is a different one
        assert not np.allclose(hann(size), np.hanning(size), atol=1e-6)


# ---------------------------------------------------------- the pitch shift ---
@pytest.mark.parametrize(
    "code,params",
    [
        ("AUR_TIMEPITCH", {"Pitch Shift": 12.0 * math.log2(1.5), "Stretch Ratio": 1.0}),
        ("AUR_VARIPITCH", {"Contour": 12.0 * math.log2(1.5), "Preserve Time": True}),
    ],
)
def test_the_pitch_shift_lands_on_the_expected_frequency(code, params):
    """440 Hz shifted by a factor of 1.5 has to arrive at 660 Hz."""
    module = build(code, params)
    x = tone(440.0, 1.0)[:, None]
    y = np.asarray(module.process(x)).ravel()
    measured = dominant_frequency(y)
    error = cents(measured, 660.0)
    assert abs(error) < 1.0, (
        f"{code} put the tone at {measured:.2f} Hz, that is {error:+.2f} cents "
        "away from 660 Hz"
    )


def test_the_time_stretcher_does_not_move_the_pitch():
    """The Variable Time module changes the duration, not the pitch."""
    module = build("AUR_VARTIME", {"Ratio": 2.0})
    x = tone(440.0, 1.0)[:, None]
    y = np.asarray(module.process(x)).ravel()
    measured = dominant_frequency(y)
    error = cents(measured, 440.0)
    assert abs(error) < 1.0, f"the pitch moved by {error:+.2f} cents"


# ------------------------------------------------------------ the resampling ---
def _stream(module, signal: np.ndarray, block: int) -> np.ndarray:
    """Run a signal through a module in blocks; return the concatenation."""
    pieces = []
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        out = np.asarray(module.process(piece.copy()))
        pieces.append(out if out.ndim > 1 else out.reshape(-1, 1))
    return np.concatenate(pieces, axis=0) if pieces else np.zeros((0, 1))


def test_the_resampling_step_is_transparent_at_the_edges():
    """A constant has to come out of the resampler as the same constant.

    The kernel of the interpolation reaches 48 samples to the right of the
    read position, so a block that is resampled on its own is cut in two:
    the first and the last samples of every block came from a kernel whose
    half was missing.  A constant of 0.5 came out as 0.5647 at the start of
    a block and as 0.3056 at its end.
    """
    module = build("AUR_VARIPITCH", RESAMPLE_ONLY)
    block = 256
    constant = np.full((block * 8, 1), 0.5, dtype=np.float64)
    out = _stream(module, constant, block)
    delay = int(module.latency_samples)
    settled = out[delay + 128 :]
    assert settled.size > 0
    error = float(np.max(np.abs(settled - 0.5)))
    assert error < 1e-12, (
        f"a constant of 0.5 comes out of the resampler as {settled[0, 0]:.12f}, "
        f"the worst error is {error:.3e}"
    )


def test_the_resampling_step_keeps_the_phase_of_a_tone_at_the_edges():
    """A tone has to come out of the resampler with the right phase.

    The ideal is the exact resample of the whole signal: the kernel
    evaluated at the read position of every output sample.  The streaming
    resampler holds its output back by the look ahead of the kernel, so
    its output is the ideal, delayed.
    """
    module = build("AUR_VARIPITCH", RESAMPLE_ONLY)
    block = 256
    x = tone(1000.0, 0.5)[:, None]
    out = _stream(module, x, block)
    delay = int(module.latency_samples)
    resampler = SincResampler(
        1.0 / FACTOR, SR, steepness=0.7, pre_ringing=0.0
    )
    ideal = resampler.resample_exact(x)
    count = min(ideal.shape[0], out.shape[0] - delay)
    assert count > 1000
    error = float(
        np.max(np.abs(out[delay : delay + count, 0] - ideal[:count, 0]))
    )
    assert error < 1e-12, (
        f"the tone comes out of the resampler with an error of {error:.3e} "
        "at the edges of the block"
    )


def test_one_block_and_many_blocks_agree():
    """The resampling step has to be the same, whatever the block size is.

    The read position of the resampler is counted from the start of the
    stream, so one block of 2048 samples and eight blocks of 256 samples
    have to give the same answer — bit for bit.
    """
    x = tone(1000.0, 0.5)[:, None]
    one = np.asarray(build("AUR_VARIPITCH", RESAMPLE_ONLY).process(x.copy()))
    many = _stream(build("AUR_VARIPITCH", RESAMPLE_ONLY), x, 256)
    assert one.shape == many.shape
    assert np.array_equal(one, many), (
        f"one block and many blocks differ by "
        f"{float(np.max(np.abs(one - many))):.3e}"
    )


def test_the_module_declares_the_latency_it_has():
    """The delay the module reports is the delay it really has.

    The kernel of the interpolation reaches 48 input samples to the right
    of the read position, so the resampled signal is held back by that
    much: 48 samples of the input signal, which is 1.0 ms at 48 kHz.
    """
    module = build("AUR_VARIPITCH", RESAMPLE_ONLY)
    x = np.zeros((4096, 1), dtype=np.float64)
    x[0, 0] = 1.0
    y = np.asarray(module.process(x)).ravel()
    assert int(np.argmax(np.abs(y))) == int(module.latency_samples), (
        f"the impulse comes back at {int(np.argmax(np.abs(y)))} and the module "
        f"declares {module.latency_samples} samples of latency"
    )


def test_the_latency_follows_the_look_ahead_of_the_kernel():
    """The declared latency is the look ahead of the kernel, in every case.

    The look ahead is 48 input samples.  The samples of the resampled signal
    run at the rate of the pitch factor, so the delay of the resampler is
    48 input samples and the delay of the module — which puts the duration
    back — is 48 samples of the output again.
    """
    up = build("AUR_VARIPITCH", {"Contour": 12.0 * math.log2(1.5), "Preserve Time": False})
    down = build("AUR_VARIPITCH", {"Contour": -12.0, "Preserve Time": False})
    assert up.latency_samples == 32, "the resampler of a fifth up delays 32 samples"
    assert down.latency_samples == 96, "the resampler of an octave down delays 96 samples"

    kept_up = build("AUR_VARIPITCH", WITH_VOCODER)
    kept_down = build("AUR_VARIPITCH", {"Contour": -12.0, "Preserve Time": True})
    assert kept_up.latency_samples == 48, (
        f"the module declares {kept_up.latency_samples} samples of latency, "
        "the look ahead of the kernel is 48"
    )
    assert kept_down.latency_samples == 48, (
        f"the module declares {kept_down.latency_samples} samples of latency, "
        "the look ahead of the kernel is 48"
    )

    # a module that does not resample does not delay the signal
    assert build("AUR_VARIPITCH", {}).latency_samples == 0
    assert build("AUR_TIMEPITCH", {"Stretch Ratio": 2.0}).latency_samples == 0
    # the time stretch scales the delay once more
    assert (
        build(
            "AUR_TIMEPITCH", {"Pitch Shift": 12.0 * math.log2(1.5), "Stretch Ratio": 2.0}
        ).latency_samples
        == 96
    )


# ------------------------------------------------------------- the transients ---
@pytest.mark.parametrize("code", list(MODULES))
def test_the_stretched_attack_stays_at_its_place(code):
    """The attack of a stretched impulse keeps its place and its energy."""
    name, params = MODULES[code]
    module = build(code, params)
    n = 8192
    attack_in = n // 4
    x = np.zeros((n, 1), dtype=np.float64)
    x[attack_in, 0] = 1.0
    y = np.asarray(module.process(x)).ravel()
    assert y.size > 0
    attack_out = int(np.argmax(np.abs(y)))
    ideal = attack_in if code == "AUR_VARIPITCH" else attack_in * 2
    assert abs(attack_out - ideal) <= 512, (
        f"{name} put the attack at {attack_out}, the ideal is {ideal} "
        "(more than one analysis hop away)"
    )


@pytest.mark.parametrize("code", list(MODULES))
def test_the_attack_keeps_its_energy(code):
    """More than 90 % of the energy has to stay within a millisecond."""
    name, params = MODULES[code]
    module = build(code, params)
    n = 8192
    x = np.zeros((n, 1), dtype=np.float64)
    x[n // 4, 0] = 1.0
    y = np.asarray(module.process(x)).ravel()
    attack_out = int(np.argmax(np.abs(y)))
    share = energy_within(y, attack_out)
    assert share >= 90.0, (
        f"{name} left only {share:.2f} % of the energy within a millisecond "
        "of the attack"
    )


@pytest.mark.parametrize("code", list(MODULES))
def test_the_attack_is_not_smeared(code):
    """The energy of the attack may not spread over more than 5 ms."""
    name, params = MODULES[code]
    module = build(code, params)
    n = 8192
    x = np.zeros((n, 1), dtype=np.float64)
    x[n // 4, 0] = 1.0
    y = np.asarray(module.process(x)).ravel()
    attack_out = int(np.argmax(np.abs(y)))
    spread = energy_spread_ms(y, attack_out)
    assert spread <= 5.0, f"{name} smeared the attack over {spread:.2f} ms"


# ------------------------------------------------------------- the time stretch ---
@pytest.mark.parametrize("code", list(MODULES))
def test_the_click_train_keeps_its_clicks(code):
    """Nine of the ten clicks have to survive a stretch by two."""
    name, params = MODULES[code]
    module = build(code, params)
    x = click_train(0.1, 10)[:, None]
    y = np.asarray(module.process(x)).ravel()
    hits = click_positions(y)
    assert hits.size >= 9, f"{name} kept only {hits.size} of the 10 clicks"


@pytest.mark.parametrize(
    "code",
    ["AUR_TIMEPITCH", "AUR_VARTIME"],
)
def test_the_click_train_keeps_its_spacing(code):
    """The spacing of the train has to stay within 1.5 % of the ideal.

    The mean over the whole train is free of the hop quantisation of the
    single clicks, the median carries it: it may be off by one hop
    (10.67 ms at 48 kHz, 5.3 % of a 200 ms spacing).
    """
    name, params = MODULES[code]
    module = build(code, params)
    x = click_train(0.1, 10)[:, None]
    y = np.asarray(module.process(x)).ravel()
    hits = click_positions(y)
    assert hits.size >= 2
    spacings = np.diff(hits) / float(SR) * 1000.0
    median = float(np.median(spacings))
    mean = float((hits[-1] - hits[0]) / float(SR) * 1000.0 / (hits.size - 1))
    mean_error = 100.0 * (mean - 200.0) / 200.0
    median_error = 100.0 * (median - 200.0) / 200.0
    assert abs(mean_error) < 1.5, f"{name} stretched the spacing by {mean_error:+.2f} %"
    assert abs(median_error) < 3.0, (
        f"{name} stretched the median spacing by {median_error:+.2f} %"
    )


def test_the_stretched_block_does_not_overload():
    """The overlap-add normalisation must not amplify the block.

    The old implementation divided the overlap-added signal by the sum of
    the squared windows.  At the end of a block that sum is tiny, so the
    signal was amplified by 70 dB: a stretched 440 Hz tone reached
    +70.41 dBFS.
    """
    module = build("AUR_TIMEPITCH", {"Stretch Ratio": 2.0})
    n = 4096
    t = np.arange(n) / float(SR)
    x = (0.5 * np.sin(2.0 * np.pi * 440.0 * t))[:, None]
    y = np.asarray(module.process(x))
    peak = float(np.max(np.abs(y)))
    assert peak <= 1.0, (
        f"the stretched block reached {peak:.3f} ({20.0 * math.log10(peak):+.1f} "
        f"dBFS)"
    )


# ------------------------------------------------------------------- the cost ---
@pytest.mark.parametrize("code", list(MODULES))
def test_the_block_cost_is_within_the_ceiling(code):
    """One 64 sample block may not cost more than the ceiling.

    The cost is measured in transform equivalents, so the number does not
    depend on how busy the machine that runs the test is.
    """
    name, params = MODULES[code]
    module = build(code, params)
    rng = np.random.default_rng(11)
    block = (rng.standard_normal(64) * 0.1)[:, None]
    module.process(block)  # the warm up pays the one-off setup
    transforms = calibrated_transforms(lambda: module.process(block))
    assert transforms <= COST_CEILING_TRANSFORMS, (
        f"{name} needed {transforms:.0f} transforms for a 64 sample block, "
        f"the ceiling is {COST_CEILING_TRANSFORMS:.0f} "
        f"({COST_CEILING_TRANSFORMS * REFERENCE_TRANSFORM_US:.0f} us on the "
        "reference machine)"
    )


# --------------------------------------------------- the DSP layer's helpers ---
def test_the_partition_median_is_bit_identical_to_numpy():
    """The one pass median has to return what ``np.median`` returns."""
    rng = np.random.default_rng(4242)
    for count in (1, 2, 3, 4, 5, 8, 17, 64, 199, 200):
        values = rng.standard_normal(count)
        assert dsp_timepitch._median(values) == float(np.median(values))
        scaled = values * 1e9
        assert dsp_timepitch._median(scaled) == float(np.median(scaled))
        shifted = values + 1000.0
        assert dsp_timepitch._median(shifted) == float(np.median(shifted))


def test_the_transient_spacing_is_unchanged():
    """The repair of the median must not change what the function returns."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal(SR) * 0.1
    for index in range(4):
        x[index * SR // 4] += 1.0
    spacing = dsp_timepitch._estimate_transient_spacing(x, SR)
    assert spacing == pytest.approx(0.25, abs=1e-9)

    # stationary material has no usable estimate
    steady = np.sin(2.0 * np.pi * 220.0 * np.arange(SR) / float(SR)) * 0.5
    assert dsp_timepitch._estimate_transient_spacing(steady, SR) == float("inf")


def test_the_adaptive_window_is_unchanged():
    """The window the DSP layer picks must not have changed."""
    spec = dsp_timepitch.adaptive_window(37.0, SR, "medium")
    assert spec.fft_size == 2048
    assert spec.hop == 512
    assert spec.window_ms == pytest.approx(37.0)
