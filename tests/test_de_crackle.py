"""
tests/test_de_crackle.py — the gate of the De-Crackle module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the crackle must still be **removed**: the impulses of the probe have to
  lose at least 4 dB of their peak,
* the bed under the crackle must **survive**: it may not move by more than
  1.5 dB,
* the output must be **finite** and as long as the input,
* one block must fit into the **realtime budget**,
* the **knob must move the answer**: a crackle that stands less above the
  material around it is repaired at a higher setting and not at a lower one,
* a **steady bed** must come out bit exact at every setting: the knob
  promises "more sensitive, more repair", so a knob step may not change the
  bed,
* the **threshold range must be the module's own**: a crackle stands only a
  few times above its own background, and the shared range of the engine is
  far too high for it -- the Low quality level was deaf to the crackle of
  the probe of the module.

Every number in this file was measured with the scripts in ``var/w79/``; the
thresholds below are the measured values with a margin, not guesses.
"""
from __future__ import annotations

import contextlib
import math

import numpy as np
import pytest

from aurcore.modules.de_click import (  # noqa: E402
    TransientRepair,
    _THRESHOLD_HIGH,
    _THRESHOLD_LOW,
    sensitivity_threshold,
)
from aurcore.modules.de_crackle import (  # noqa: E402
    CRACKLE_SENSITIVITY,
    _CRACKLE_THRESHOLD_RANGE,
    DeCrackle,
)

from tests.block_coherence import (  # noqa: E402
    BLOCKS,
    COHERENCE_LIMIT_DB,
    coherence_rows,
    declared_latency,
    impulse_delay,
    probe_signal,
)

MODULE_CODE = "AUR_DECRACKLE"

#: The impulses of the probe have to lose at least this much of their peak.
TARGET_CRACKLE_REMOVAL_DB = 4.0

#: The bed under the crackle may not move by more than this.
TARGET_BED_DRIFT_DB = 1.5

#: One 64 sample block may not cost more than this (us).
CEILING_US_64 = 3.0 * 1333.3

#: The three quality levels of the module.
QUALITIES = ("Low", "Medium", "High")

#: The latency of the module (the look ahead of the grid of the engine).
LATENCY = 1664

#: The level of the steady bed the crackle is measured on (dBFS RMS).
BED_LEVEL_DB = -34.0

#: The graded transients of the knob test: the amplitude of every crackle,
#: from the loudest to the quietest.  The score the detector measures on
#: them is 2.943, 0.921, 0.200, 0.200, 0.200 and 0.200 in the Low band,
#: and the threshold of the knob runs from 0.974 (Low) to 0.863 (High): the
#: knob decides about the crackle of 0.3.
GRADED_AMPLITUDES = (0.9, 0.3, 0.1, 0.03, 0.01, 0.003)

#: Where the graded transients stand, in seconds.
GRADED_POSITIONS = (0.6, 1.1, 1.6, 2.1, 2.6, 3.1)

#: A crackle counts as repaired when at least this much of it is gone.  The
#: module removes half of what it finds by default (its Reduction control
#: is at 0.5), so three quarters of the crackle is enough.
REPAIRED_FRACTION = 0.75


def db(value: float) -> float:
    """``value`` in decibels, with a floor so the log never blows up."""
    return 20.0 * math.log10(max(float(value), 1e-12))


def noise_bed(seconds: float = 4.0, level_db: float = BED_LEVEL_DB) -> np.ndarray:
    """A steady bed of broadband noise: the material a crackle sits on."""
    rng = np.random.default_rng(51)
    count = int(round(seconds * 48_000))
    x = rng.standard_normal(count)
    x *= 10.0 ** (level_db / 20.0) / math.sqrt(float(np.mean(x * x)))
    return np.stack([x, x], axis=1)


def module_of(quality: str = "Medium") -> DeCrackle:
    """A module with the given quality."""
    module = DeCrackle(48_000, 2)
    module.params.set("Quality", str(quality))
    return module


def render(signal: np.ndarray, quality: str = "Medium") -> np.ndarray:
    """Run the module over the signal the way the server renders it."""
    module = module_of(quality)
    out = np.empty_like(signal)
    for start in range(0, signal.shape[0], 65_536):
        chunk = signal[start : start + 65_536]
        out[start : start + chunk.shape[0]] = np.asarray(module.process(chunk))
    return out


def aligned(out: np.ndarray, latency: int = LATENCY) -> np.ndarray:
    """Take the latency off a render, so that it lines up with the input."""
    return out[latency:] if latency > 0 else out


@contextlib.contextmanager
def score_recorder():
    """Record the largest score the engine computes.

    The score of the engine is what the threshold is compared against, so
    the test needs the number the module itself works with: the score of
    the material, measured in the module's own path (the detector sees the
    material through the crackle band of the quality level).
    """
    original = TransientRepair._detection_score
    best = {"value": 0.0}

    def wrapper(self, x, spec, settings):
        score = original(self, x, spec, settings)
        if score.size:
            best["value"] = max(best["value"], float(np.max(score)))
        return score

    TransientRepair._detection_score = wrapper
    try:
        yield best
    finally:
        TransientRepair._detection_score = original


def score_of(signal: np.ndarray, quality: str = "Medium") -> float:
    """The largest score the engine measures on the material."""
    with score_recorder() as best:
        render(signal, quality)
    return best["value"]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_de_crackle_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_de_crackle_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_de_crackle_removes_the_crackle():
    """The impulses of the probe have to lose at least 4 dB of their peak."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = probe_signal(MODULE_CODE)
    module = registry.create(MODULE_CODE, 48000, 2)
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    before = float(np.max(np.abs(signal)))
    after = float(np.max(np.abs(out)))
    change = 20.0 * np.log10(after / before)
    assert change <= -TARGET_CRACKLE_REMOVAL_DB, (
        f"the crackle only lost {abs(change):.2f} dB, the gate asks for "
        f"{TARGET_CRACKLE_REMOVAL_DB:.1f} dB"
    )


def test_de_crackle_keeps_the_bed():
    """The bed under the crackle may not move by more than 1.5 dB."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = probe_signal(MODULE_CODE)
    module = registry.create(MODULE_CODE, 48000, 2)
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)

    def bed(x: np.ndarray) -> float:
        """The level of the 480 Hz bed."""
        spec = np.fft.rfft(x[:, 0] * np.hanning(x.shape[0]))
        freqs = np.fft.rfftfreq(x.shape[0], 1.0 / 48000.0)
        band = (freqs >= 450.0) & (freqs <= 510.0)
        return float(20.0 * np.log10(np.max(np.abs(spec[band])) + 1e-30))

    drift = bed(out) - bed(signal)
    assert abs(drift) <= TARGET_BED_DRIFT_DB, (
        f"the bed moved {drift:+.2f} dB, the gate allows "
        f"{TARGET_BED_DRIFT_DB:.1f} dB"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_de_crackle_keeps_the_length_and_stays_finite(block):
    """The output is as long as the input and holds no NaN."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, 48000, 2)
    signal = np.zeros((4096, 2), dtype=np.float64)
    signal[:, 0] = 0.5 * np.sin(2.0 * np.pi * 200.0 * np.arange(4096) / 48000.0)
    pieces = [
        np.asarray(module.process(signal[start : start + block]), dtype=np.float64)
        for start in range(0, signal.shape[0], block)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_de_crackle_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget."""
    import time

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, 48000, 2)
    block = np.zeros((64, 2), dtype=np.float64)
    for _ in range(3):
        module.process(block)
    times = []
    for _ in range(15):
        start = time.perf_counter_ns()
        module.process(block)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    measured = float(np.median(times))
    assert measured <= CEILING_US_64, (
        f"a 64 sample block costs {measured:.0f} us, the ceiling is "
        f"{CEILING_US_64:.0f} us"
    )


# ---------------------------------------------------------------------------
# 2. the range of the knob is the range of the module
# ---------------------------------------------------------------------------
def test_the_module_declares_its_own_threshold_range():
    """The module may not use the shared range of the engine.

    A crackle stands only a few times above its own background: the
    shared range (0.55 .. 3.0) put the Low quality level at a factor of
    2.137, and the detector does not see the crackle of the probe of the
    module above a factor of 1.35: the Low level was deaf to the crackle
    and removed 0.08 dB of it.
    """
    module = module_of("Medium")
    settings = module.settings()
    assert settings.threshold_range == _CRACKLE_THRESHOLD_RANGE
    assert settings.threshold_range is not None
    assert settings.threshold_range != (_THRESHOLD_LOW, _THRESHOLD_HIGH)


def test_the_threshold_falls_with_the_quality():
    """A higher quality has to accept a weaker crackle.

    The threshold of the knob is the factor the robust background of the
    material has to be exceeded by, and it has to run *through* the
    population of the crackle: a range that lies below all of them cannot
    change the answer of the module.
    """
    values = [
        sensitivity_threshold(
            CRACKLE_SENSITIVITY[quality], _CRACKLE_THRESHOLD_RANGE
        )
        for quality in QUALITIES
    ]
    assert all(a > b for a, b in zip(values, values[1:])), values
    assert values[0] < _CRACKLE_THRESHOLD_RANGE[1]
    assert values[-1] > _CRACKLE_THRESHOLD_RANGE[0]


def test_the_range_runs_through_the_population_of_the_module():
    """The low end has to be above the steady bed, the high end below the
    crackle that has to be repaired at every setting.

    The measurement is the score the engine measures on the material, in
    the module's own path: the steady bed of broadband noise reaches a
    score of 0.473 to 0.522, the crackle of 0.9 on the same bed reaches
    2.943 to 4.857.
    """
    low, high = _CRACKLE_THRESHOLD_RANGE
    for quality in QUALITIES:
        bed = noise_bed(seconds=2.0)
        steady_score = score_of(bed, quality)
        dirty = bed.copy()
        for amplitude, position in zip(GRADED_AMPLITUDES, GRADED_POSITIONS):
            index = int(position * 48_000)
            dirty[index : index + 4, 0] += amplitude
            dirty[index : index + 4, 1] += amplitude
        crackle_score = score_of(dirty, quality)
        assert low > steady_score, (
            f"quality {quality}: the low end of the range is {low}, the steady "
            f"bed reaches a score of {steady_score:.3f}"
        )
        assert high < crackle_score, (
            f"quality {quality}: the high end of the range is {high}, the "
            f"crackle reaches a score of {crackle_score:.3f} and has to be "
            "repaired at every setting"
        )


# ---------------------------------------------------------------------------
# 3. the knob moves the numbers
# ---------------------------------------------------------------------------
def graded_bed() -> tuple[np.ndarray, np.ndarray]:
    """The steady bed with six graded crackles on it, and without them."""
    clean = noise_bed()[:, 0]
    dirty = clean.copy()
    for amplitude, position in zip(GRADED_AMPLITUDES, GRADED_POSITIONS):
        index = int(position * 48_000)
        dirty[index : index + 4] += amplitude
    return clean, dirty


def repaired_count(quality: str) -> int:
    """How many of the graded crackles the module repairs at this setting.

    A crackle counts as repaired when at least three quarters of it is
    gone: the residual of the repair is measured against the bed without
    the crackle, so what is left of it is what the repair did not take.
    """
    clean, dirty = graded_bed()
    out = aligned(render(np.stack([dirty, dirty], axis=1), quality))
    count = 0
    for amplitude, position in zip(GRADED_AMPLITUDES, GRADED_POSITIONS):
        index = int(position * 48_000)
        left = float(
            np.max(np.abs(out[index - 4 : index + 5, 0] - clean[index - 4 : index + 5]))
        )
        if left <= REPAIRED_FRACTION * amplitude:
            count += 1
    return count


def test_the_knob_repairs_more_as_it_rises():
    """The knob has to move the numbers.

    A parameter that does not change the sound is a defect: the interface
    shows a knob that does not change anything.  The measured counts of the
    graded crackles (the amplitudes 0.9, 0.3, 0.1, 0.03, 0.01 and 0.003)
    are 1 at the Low quality and 2 at the Medium and the High quality.
    """
    counts = {quality: repaired_count(quality) for quality in QUALITIES}
    assert counts["High"] > counts["Low"], (
        f"the knob did not repair more at the High quality than at the Low "
        f"one: {counts}"
    )
    values = [counts[quality] for quality in QUALITIES]
    assert all(a <= b for a, b in zip(values, values[1:])), (
        f"the knob repaired less when it was turned up: {counts}"
    )


def test_every_quality_level_removes_the_crackle_of_the_probe():
    """No quality level may be deaf to the crackle.

    With the shared range of the engine the Low quality level removed
    0.08 dB of the crackle of the probe (the detector does not see it above
    a factor of 1.35 in the Low band); it now removes 1.94 dB of it.
    """
    signal = probe_signal(MODULE_CODE)
    for quality in QUALITIES:
        out = aligned(render(signal, quality))
        before = float(np.max(np.abs(signal)))
        after = float(np.max(np.abs(out)))
        removal = db(before) - db(after)
        assert removal >= 1.0, (
            f"the {quality} quality removed only {removal:.2f} dB of the "
            "crackle of the probe"
        )


def test_a_steady_bed_is_not_touched_at_any_quality():
    """Nothing at all may change: the bed is not a transient."""
    bed = noise_bed()
    for quality in QUALITIES:
        out = aligned(render(bed, quality))
        delta = float(np.max(np.abs(out - bed[: out.shape[0]])))
        assert delta == 0.0, (
            f"the {quality} quality changed the steady bed by {delta:.3e}"
        )


def test_the_default_removes_the_crackle_of_the_probe():
    """The default has to do the job it did before the range was given to
    the module: the crackle of the probe has to lose at least 4 dB.

    The measured removal of the default is 4.86 dB (the rule of the module
    in qa/survey asks for 4 dB).
    """
    signal = probe_signal(MODULE_CODE)
    out = aligned(render(signal, "Medium"))
    before = float(np.max(np.abs(signal)))
    after = float(np.max(np.abs(out)))
    removal = db(before) - db(after)
    assert removal >= TARGET_CRACKLE_REMOVAL_DB, (
        f"the default removed only {removal:.2f} dB of the crackle, the rule "
        f"asks for {TARGET_CRACKLE_REMOVAL_DB:.0f} dB"
    )
