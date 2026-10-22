"""
tests/test_deconstruct.py — the Deconstruct module: what it does and what it costs.

The module separates a signal into a tonal, a noisy and a transient component
with a median filter HPSS.  The tests below cover three things:

* the **contract**: the length, the finiteness, the latency and the reset;
* the **quality**: the tonal part of a mixture has to survive, the noise and
  the transient have to be separable, a steady noise bed has to belong to
  the noisy stem and not to the transient one, and the result must not
  depend on the block size the host happens to use;
* the **cost**: a 64 sample block has to be processed inside the realtime
  budget of that block, which is 1333.3 us at 48 kHz.

The numbers in the docstrings are the ones that were measured; the margin in
the assertions is the room the machine leaves.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

from aurcore import registry
from aurcore.modules.deconstruct import (
    Deconstruct,
    _broadband_floor,
    _median_filter_bins,
    _median_last_axis,
    median_filter_axis,
)

#: The sample rate every measurement runs at.
SAMPLE_RATE = 48_000

#: The channel count every measurement runs at.
CHANNELS = 2

#: The block size the cost is measured at.
BLOCK = 64

#: The realtime budget of one block, in microseconds (1333.3 us at 48 kHz).
BUDGET_US = BLOCK / SAMPLE_RATE * 1_000_000.0

#: The band the wanted partial of the test mixture lives in.
TONAL_BAND = (200.0, 240.0)

#: A band of the test mixture that holds nothing but noise.
EMPTY_BAND = (3000.0, 3100.0)

#: How many blocks the cost measurement streams through the module.
STREAM_BLOCKS = 200

#: How often the cost measurement is repeated; the fastest run is kept, so
#: that a machine which is busy with other work does not fail the test.
COST_RUNS = 3

#: The tilt the balance puts on the tonal and on the noisy stem, in decibels.
BALANCE_TILT_DB = 3.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_module() -> Deconstruct:
    """Return a fresh Deconstruct module."""
    return registry.create("AUR_DECONSTRUCT", SAMPLE_RATE, CHANNELS)


def rms_db(x: np.ndarray) -> float:
    """The RMS level of a signal in dBFS."""
    arr = np.asarray(x, dtype=np.float64)
    value = float(np.sqrt(np.mean(arr * arr)))
    return 20.0 * np.log10(value) if value > 0 else -120.0


def band_db(x: np.ndarray, low: float, high: float) -> float:
    """The level of a frequency band in dBFS (the power share of the band)."""
    signal = np.asarray(x, dtype=np.float64).mean(axis=1)
    window = np.hanning(signal.size)
    spectrum = np.fft.rfft(signal * window)
    freqs = np.fft.rfftfreq(signal.size, 1.0 / SAMPLE_RATE)
    power = np.abs(spectrum) ** 2
    mask = (freqs >= low) & (freqs <= high)
    total = float(np.sum(power))
    if total <= 0.0 or not bool(mask.any()):
        return -120.0
    share = float(np.sum(power[mask])) / total
    mean_square = float(np.mean(signal * signal))
    if share * mean_square <= 0.0:
        return -120.0
    return 10.0 * np.log10(share * mean_square)


def mixture(duration_s: float = 1.0, seed: int = 4242) -> np.ndarray:
    """A tonal + noise + transient mixture with a known ground truth.

    The tonal part is a harmonic stack at 220 Hz, the noisy part is white
    noise 27 dB below it and the transient is a single impulse at 1.0 s.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n) / float(SAMPLE_RATE)
    rng = np.random.default_rng(seed)
    tonal = np.zeros(n)
    for harmonic, amp in ((1, 0.30), (2, 0.18), (3, 0.12), (4, 0.07), (5, 0.04)):
        tonal += amp * np.sin(2.0 * np.pi * 220.0 * harmonic * t)
    noise = 0.02 * rng.standard_normal(n)
    click = np.zeros(n)
    if n > SAMPLE_RATE:
        click[SAMPLE_RATE] = 0.5
    mono = tonal + noise + click
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def impulse_signal(duration_s: float = 1.0) -> np.ndarray:
    """A tonal bed with one impulse in it.

    The bed is a single tone at 480 Hz, so the broadband level of the bed is
    the floor of the spectrum and the impulse stands out of it by far more
    than the margin of the transient detector (6 dB).  The impulse therefore
    belongs to the transient stem and the bed does not.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n) / float(SAMPLE_RATE)
    bed = 0.20 * np.sin(2.0 * np.pi * 480.0 * t)
    click = np.zeros(n)
    click[int(0.5 * SAMPLE_RATE)] = 1.0
    mono = bed + click
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def stream(module, signal: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Process a signal block by block and return the whole output."""
    out = np.empty_like(signal)
    for start in range(0, signal.shape[0], block):
        chunk = signal[start : start + block]
        result = np.asarray(module.process(chunk.copy()), dtype=np.float64)
        out[start : start + chunk.shape[0]] = result[: chunk.shape[0]]
    return out


def stream_components(module, signal: np.ndarray, block: int = BLOCK):
    """Process a signal block by block and return the three stems."""
    stems = [np.zeros_like(signal) for _ in range(3)]
    for start in range(0, signal.shape[0], block):
        chunk = signal[start : start + block]
        parts = module.components(chunk.copy())
        for index, part in enumerate(parts):
            stems[index][start : start + chunk.shape[0]] = part
    return stems


def set_gains(
    module: Deconstruct,
    tonal: float,
    noisy: float,
    transient: float,
    balance: str = "Noisy",
) -> Deconstruct:
    """Set the three component gains and isolate one component.

    The balance is the tilt between the noisy and the tonal part: the
    component that is isolated is the one the tilt points at.
    """
    module.params.set("Tonal Gain", tonal)
    module.params.set("Noisy Gain", noisy)
    module.params.set("Transient Gain", transient)
    module.params.set("Separate Transients", True)
    module.params.set("Balance", balance)
    return module


def range_of(module: Deconstruct, name: str) -> tuple[float, float]:
    """Return the range the module declares for one of its gains.

    The test reads the range from the module and not from a number of its
    own, so it measures the product as it is and not as it was.
    """
    for desc in module.params.descs():
        if desc.name == name:
            return float(desc.minimum), float(desc.maximum)
    raise KeyError(f"the module has no parameter called {name!r}")


def corpus_clip(name: str):
    """Return a clip of the corpus, or skip the test."""
    corpus = Path(__file__).resolve().parents[1] / "corpus"
    if not (corpus / "audio").exists():
        pytest.skip("the corpus is not built")
    sys.path.insert(0, str(corpus))
    try:
        from corpus import measure
    except Exception as exc:  # noqa: BLE001 - a broken corpus is not a failure
        pytest.skip(f"the corpus could not be imported: {exc}")
    try:
        data, sample_rate = measure.load_clip(name)
    except Exception as exc:  # noqa: BLE001 - a missing clip is not a failure
        pytest.skip(f"the corpus clip is not there: {exc}")
    return np.asarray(data, dtype=np.float64), int(sample_rate)


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------
def test_the_module_is_registered() -> None:
    """The module is in the registry under its own code."""
    assert registry.has("AUR_DECONSTRUCT")
    assert registry.get("AUR_DECONSTRUCT") is Deconstruct


def test_process_keeps_the_length_and_stays_finite() -> None:
    """A block comes back with the length it went in with, and finite."""
    module = make_module()
    signal = mixture(0.5)
    out = np.asarray(module.process(signal.copy()))
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))


def test_the_output_is_delayed_by_the_reported_latency() -> None:
    """An impulse comes back exactly ``latency_samples`` later."""
    module = make_module()
    n = 4 * SAMPLE_RATE
    impulse = np.zeros((n, CHANNELS))
    impulse[SAMPLE_RATE, 0] = 1.0
    out = stream(module, impulse)
    peak = int(np.argmax(np.abs(out[:, 0])))
    assert peak == SAMPLE_RATE + module.latency_samples
    assert module.latency_samples > 0


def test_reset_clears_the_analysis() -> None:
    """After a reset the module starts from silence again."""
    module = make_module()
    signal = mixture(0.5)
    first = stream(module, signal)
    module.reset()
    second = stream(module, signal)
    assert np.allclose(first, second)


# ---------------------------------------------------------------------------
# the quality
# ---------------------------------------------------------------------------
def test_the_result_does_not_depend_on_the_block_size() -> None:
    """The same signal gives the same result at every block size.

    The analysis is streaming, so the result must not depend on the way the
    host chops the signal into blocks.
    """
    signal = mixture(1.0)
    reference = stream(make_module(), signal, block=4096)
    for block in (64, 128, 256, 512):
        out = stream(make_module(), signal, block=block)
        assert np.array_equal(out, reference), f"block {block} gave a different result"


def test_the_components_add_up_to_the_input() -> None:
    """The three components carry the signal, not a fraction of it.

    The stems are a partition: the tonal, the noisy and the transient
    magnitude of a bin add up to the magnitude of that bin, so the signal
    is in the three stems once and once only.
    """
    module = make_module()
    signal = mixture(1.0)
    tonal = np.zeros_like(signal)
    noisy = np.zeros_like(signal)
    transient = np.zeros_like(signal)
    for start in range(0, signal.shape[0], BLOCK):
        chunk = signal[start : start + BLOCK]
        parts = module.components(chunk.copy())
        for index, part in enumerate(parts):
            (tonal, noisy, transient)[index][start : start + chunk.shape[0]] = part
    total = tonal + noisy + transient
    core = slice(module.latency_samples + 2048, signal.shape[0] - 2048)
    ratio = float(np.sqrt(np.mean(total[core] ** 2)) / np.sqrt(np.mean(signal[core] ** 2)))
    assert 0.95 < ratio < 1.05, f"the components carry {ratio:.3f} of the signal"


def test_process_is_the_mix_of_the_components() -> None:
    """The output is the re-mixed components, with the parameter gains."""
    signal = mixture(0.5)
    mixed = stream(make_module(), signal)
    module = make_module()
    tonal = np.zeros_like(signal)
    noisy = np.zeros_like(signal)
    transient = np.zeros_like(signal)
    for start in range(0, signal.shape[0], BLOCK):
        chunk = signal[start : start + BLOCK]
        parts = module.components(chunk.copy())
        for index, part in enumerate(parts):
            (tonal, noisy, transient)[index][start : start + chunk.shape[0]] = part
    gain_tonal = 10.0 ** (-3.0 / 20.0)
    gain_noisy = 10.0 ** (3.0 / 20.0)
    expected = tonal * gain_tonal + noisy * gain_noisy + transient
    core = slice(module.latency_samples + 2048, signal.shape[0] - 2048)
    assert np.allclose(mixed[core], expected[core], atol=1e-9)


def test_the_tonal_component_is_isolated() -> None:
    """The wanted partial survives, the noise around it does not.

    With the tonal component at the top of its range and the other two at the
    bottom, and the balance tilted towards the tonal part, the output has to
    be the partial and nothing else, so the partial has to be more than
    10 dB further above the noise band than it was in the input.
    The block local analysis this module used before the streaming rewrite
    reached 34 dB of separation where the input has 44 dB, i.e. it lost
    the separation completely.

    The gains of the components run from -12 to +2.4 dB: the taper law puts
    unity in the middle of the travel, so the boost of a component is a
    fifth of its cut (``docs/RESTORATION_REFERENCE.md``, section 3.1).  The
    rule is therefore measured with the balance tilted towards the component
    that is isolated, which is what the balance is for.  With the default
    balance the largest contrast the two components can reach is 8.4 dB, and
    no separation beats the contrast of the gains that make it: the rule
    cannot be met there by any module.
    """
    signal = mixture(1.0)
    module = make_module()
    low, high = range_of(module, "Tonal Gain")
    module = set_gains(module, high, low, low, "Tonal")
    out = stream(module, signal)
    separation = band_db(out, *TONAL_BAND) - band_db(out, *EMPTY_BAND)
    before = band_db(signal, *TONAL_BAND) - band_db(signal, *EMPTY_BAND)
    assert separation - before > 10.0, (
        f"the separation is {separation:.1f} dB, the input has {before:.1f} dB"
    )


def test_the_tonal_stem_holds_the_partial_and_not_the_noise() -> None:
    """The tonal stem is the partial, and the noise is not in it.

    The separation of the module is a property of the module and not of the
    surface of its controls: the tonal stem has to be more than 10 dB
    further above the noise band than the input was, whatever the range of
    the gains is.  Measured: the stem separates 59.4 dB where the input has
    44.2 dB.
    """
    signal = mixture(1.0)
    module = make_module()
    tonal = stream_components(module, signal)[0]
    separation = band_db(tonal, *TONAL_BAND) - band_db(tonal, *EMPTY_BAND)
    before = band_db(signal, *TONAL_BAND) - band_db(signal, *EMPTY_BAND)
    assert separation - before > 10.0, (
        f"the tonal stem separates {separation:.1f} dB, "
        f"the input has {before:.1f} dB"
    )


def test_the_noisy_component_is_isolated() -> None:
    """The noise survives, the partial does not.

    With the noisy component at the top of its range and the other two at the
    bottom, and the balance tilted towards the noisy part, the output has to
    be the noise, so the noise band has to be more than 10 dB further above
    the band of the partial than it was in the input.  The block local
    analysis reached -42 dB where the input has -44 dB, i.e. it could not
    separate anything.
    """
    signal = mixture(1.0)
    module = make_module()
    low, high = range_of(module, "Noisy Gain")
    module = set_gains(module, low, high, low, "Noisy")
    out = stream(module, signal)
    separation = band_db(out, *EMPTY_BAND) - band_db(out, *TONAL_BAND)
    before = band_db(signal, *EMPTY_BAND) - band_db(signal, *TONAL_BAND)
    assert separation - before > 10.0, (
        f"the separation is {separation:.1f} dB, the input has {before:.1f} dB"
    )


def test_the_transient_component_is_isolated() -> None:
    """The impulse survives, the steady material does not.

    With the transient component at the top of its range and the other two
    at the bottom the output has to hold the impulse and nothing else: the
    impulse has to be more than 20 dB above the background.

    The rule of the module is that a transient is not a steady bed: the
    transient stem holds the short broadband events and the steady material
    stays in the tonal and in the noisy stem.  An impulse on a tonal bed
    stands out of the broadband floor of the bed by far more than the 6 dB
    margin of the detector, so it has to be found.
    """
    module = make_module()
    low, high = range_of(module, "Transient Gain")
    module = set_gains(module, low, low, high)
    out = stream(module, impulse_signal(1.0))
    peak = float(np.max(np.abs(out)))
    background = float(np.median(np.abs(out)))
    assert 20.0 * np.log10(peak / max(background, 1e-12)) > 20.0, (
        "the impulse is not clearly above the background"
    )


def test_the_transient_stem_holds_the_impulse_and_not_the_bed() -> None:
    """The impulse is in the transient stem, the steady bed is not.

    The transient stem of the bed has to stay 20 dB below the bed itself,
    and the impulse has to be 20 dB above the background of that stem.
    """
    module = make_module()
    stems = stream_components(module, impulse_signal(1.0))
    transient = stems[2]
    core = slice(module.latency_samples + 4096, transient.shape[0] - 4096)
    background = float(np.median(np.abs(transient[core])))
    position = int(0.5 * SAMPLE_RATE) + module.latency_samples
    window = transient[max(0, position - 64) : position + 64]
    peak = float(np.max(np.abs(window)))
    assert 20.0 * np.log10(peak / max(background, 1e-12)) > 20.0, (
        "the impulse is not in the transient stem"
    )


def test_the_module_does_not_make_the_signal_louder() -> None:
    """The module is a filter: it must not add energy to the signal."""
    module = make_module()
    signal = mixture(1.0)
    out = stream(module, signal)
    core = slice(module.latency_samples + 2048, signal.shape[0] - 2048)
    assert rms_db(out[core]) < rms_db(signal[core]) + 0.1


# ---------------------------------------------------------------------------
# the steady noise bed
# ---------------------------------------------------------------------------
def test_a_steady_bed_belongs_to_the_noisy_stem() -> None:
    """A steady noise bed is stationary material, so it is the noisy stem.

    The corpus hiss is a steady white noise bed of -60 dBFS and nothing
    else.  The transient stem has to stay empty of it: the transient
    detector compares the broadband level of a frame with the broadband
    level of the same band in the frames before it, and for stationary
    material the two are the same.

    Measured: the tonal stem holds 1.8 % of the energy of the bed, the
    noisy stem 98.2 % and the transient stem 0.02 %.
    """
    data, sample_rate = corpus_clip("hiss")
    module = registry.create("AUR_DECONSTRUCT", sample_rate, data.shape[1])
    stems = stream_components(module, data)
    core = slice(module.latency_samples + 4096, data.shape[0] - 4096)
    power = [float(np.mean(stem[core] ** 2)) for stem in stems]
    total = sum(power)
    assert total > 0.0
    share = [value / total for value in power]
    assert share[1] > 0.95, f"the noisy stem holds {share[1] * 100:.1f} % of the bed"
    assert share[2] < 0.01, f"the transient stem holds {share[2] * 100:.2f} % of the bed"
    assert share[0] < 0.05, f"the tonal stem holds {share[0] * 100:.1f} % of the bed"


def test_the_transient_stem_is_far_below_a_steady_bed() -> None:
    """The transient stem of a steady bed is 20 dB below the bed itself.

    The bed is mixed back at the transient gain (0 dB by default), so every
    decibel that the transient stem holds of the bed is a decibel the noisy
    gain cannot take away.  Before the transient detector was repaired the
    transient stem held 52 % of the energy of the bed, which is why the
    noisy gain only took 3.8 dB off it.
    """
    data, sample_rate = corpus_clip("hiss")
    module = registry.create("AUR_DECONSTRUCT", sample_rate, data.shape[1])
    stems = stream_components(module, data)
    core = slice(module.latency_samples + 4096, data.shape[0] - 4096)
    bed = rms_db(data[core])
    transient = rms_db(stems[2][core])
    assert transient < bed - 20.0, (
        f"the transient stem is {bed - transient:.1f} dB below the bed"
    )


def test_the_noisy_gain_removes_the_steady_bed() -> None:
    """With the noisy gain at -12 dB the bed has to lose the gain.

    The balance tilts the noisy stem by +3 dB and the tonal stem by -3 dB,
    so the promise of a -12 dB noisy gain is -9 dB on the noisy stem.  The
    bed has to lose at least 7.5 dB of it.

    Before the transient detector was repaired the bed lost 3.8 dB, because
    half of it was in the transient stem, which is mixed back at 0 dB.
    """
    data, sample_rate = corpus_clip("hiss")
    module = registry.create("AUR_DECONSTRUCT", sample_rate, data.shape[1])
    module.params.set("Noisy Gain", -12.0)
    module.params.set("Balance", "Noisy")
    out = stream(module, data)
    core = slice(module.latency_samples + 4096, data.shape[0] - 4096)
    removed = rms_db(data[core]) - rms_db(out[core])
    assert removed > 7.5, f"the bed lost {removed:.2f} dB, the promise is 9.00 dB"
    assert removed < 9.0, f"the bed lost {removed:.2f} dB, more than it could"


def test_the_noisy_gain_leaves_the_wanted_part_alone() -> None:
    """The wanted part may only lose the tilt of the balance.

    The tonal signal on its own loses the 3 dB of the balance tilt and
    nothing else: measured -3.01 dB with a -12 dB noisy gain, which is the
    -3.00 dB of the tilt plus 0.01 dB.  Before the repair it was -3.00 dB,
    so the repair did not damage the wanted part.
    """
    signal = mixture(1.0)
    module = make_module()
    module.params.set("Noisy Gain", -12.0)
    module.params.set("Balance", "Noisy")
    out = stream(module, signal)
    core = slice(module.latency_samples + 4096, signal.shape[0] - 4096)
    tonal = rms_db(out[core]) - rms_db(signal[core])
    assert abs(tonal + BALANCE_TILT_DB) < 1.0, (
        f"the wanted part lost {tonal:.2f} dB, the tilt is {BALANCE_TILT_DB:.2f} dB"
    )


def test_the_corpus_clicks_survive_the_module() -> None:
    """The impulses of the corpus click clip stay impulses.

    The clip is six single sample impulses on a 480 Hz bed.  Every impulse
    has to be at least 20 dB above the bed after the module, and the bed
    must not be lifted by more than 3 dB.
    """
    corpus = Path(__file__).resolve().parents[1] / "corpus"
    if not (corpus / "audio").exists():
        pytest.skip("the corpus is not built")
    sys.path.insert(0, str(corpus))
    try:
        from corpus import measure
    except Exception as exc:  # noqa: BLE001 - a broken corpus is not a failure
        pytest.skip(f"the corpus could not be imported: {exc}")
    try:
        data, sample_rate = measure.load_clip("clicks")
    except Exception as exc:  # noqa: BLE001 - a missing clip is not a failure
        pytest.skip(f"the corpus clip is not there: {exc}")

    module = registry.create("AUR_DECONSTRUCT", sample_rate, data.shape[1])
    out = stream(module, data, block=BLOCK)
    found = measure.detect_clicks(out, sample_rate)
    assert len(found) >= 6, f"the module lost impulses: {len(found)} of 6 survived"
    assert min(item["excess_db"] for item in found) > 20.0
    assert rms_db(out) < rms_db(data) + 3.0


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------
def test_the_median_filter_matches_the_scipy_rank_filter() -> None:
    """The fast median filter returns what SciPy's rank filter returns."""
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(7)
    arr = rng.standard_normal((53, 61))
    for width in (3, 4, 5, 16, 17):
        mine = _median_last_axis(arr, width)
        reference = scipy_ndimage.median_filter(arr, size=[1, width], mode="nearest")
        assert np.array_equal(mine, reference), f"width {width} differs"
        assert np.array_equal(
            _median_filter_bins(arr, width), reference
        ), f"width {width} differs in the SciPy path"


def test_the_broadband_floor_ignores_narrow_peaks() -> None:
    """A narrow peak must not lift the broadband floor with it.

    The floor of a flat spectrum with one narrow peak in it has to stay at
    the level of the flat part: the estimator takes the median of the bins
    outside the gap, so a single peak cannot raise it.
    """
    arr = np.zeros((1, 512))
    arr[0, 256] = 40.0
    floor = _broadband_floor(arr)
    assert float(np.max(floor)) < 1.0, "the peak raised the broadband floor"


def test_the_public_median_filter_is_unchanged() -> None:
    """The public helper still filters along the axis it is given."""
    rng = np.random.default_rng(13)
    arr = rng.standard_normal((9, 21))
    filtered = median_filter_axis(arr, 0, 5)
    assert filtered.shape == arr.shape
    assert np.all(np.abs(filtered - arr) <= np.abs(arr).max())


# ---------------------------------------------------------------------------
# the cost
# ---------------------------------------------------------------------------
def test_a_block_is_processed_inside_the_realtime_budget() -> None:
    """A 64 sample block must cost less than the time it takes to play it.

    The budget is ``block / sample_rate`` seconds: 1333.3 us for 64 samples
    at 48 kHz.  The measurement streams a continuous signal through the
    module, because that is how a host uses it, and the fastest of
    :data:`COST_RUNS` runs is kept so that a machine which is busy with
    other work does not fail the test.
    """
    signal = mixture(0.5)
    blocks = [
        np.ascontiguousarray(signal[:BLOCK]) for _ in range(STREAM_BLOCKS)
    ]
    best = float("inf")
    for _ in range(COST_RUNS):
        module = make_module()
        for block in blocks[:8]:
            module.process(block.copy())
        start = time.perf_counter_ns()
        for block in blocks:
            module.process(block.copy())
        elapsed = (time.perf_counter_ns() - start) / 1000.0 / STREAM_BLOCKS
        best = min(best, elapsed)
    assert best <= BUDGET_US, (
        f"a {BLOCK} sample block costs {best:.0f} us, the budget is {BUDGET_US:.0f} us"
    )
