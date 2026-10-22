"""
tests/test_mouth_de_click.py — the gate of the Mouth De-Click module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the click must still be **removed**: it has to lose at least 6 dB of its
  peak,
* the bed under the click must **survive**: it may not move by more than
  1.5 dB,
* the output must be **finite** and as long as the input,
* one block must fit into the **realtime budget**,
* the **knob must move the answer**: a transient that stands less above the
  material around it is repaired at a higher setting and not at a lower one,
* a **steady programme** must come out bit exact at every setting: the knob
  promises "more sensitive, more repair", so a knob step may not change the
  programme,
* the **threshold range must be the module's own**: the shared range of the
  engine lies below the whole population of the transients of the module,
  so the knob could not change anything.

Every number in this file was measured with the scripts in ``var/w79/``; the
thresholds below are the measured values with a margin, not guesses.
"""
from __future__ import annotations

import contextlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "corpus") not in sys.path:
    sys.path.insert(0, str(ROOT / "corpus"))

from aurcore.modules.de_click import (  # noqa: E402
    TransientRepair,
    _THRESHOLD_HIGH,
    _THRESHOLD_LOW,
    sensitivity_threshold,
)
from aurcore.modules.mouth_de_click import (  # noqa: E402
    _MOUTH_THRESHOLD_RANGE,
    MouthDeClick,
)

from tests.block_coherence import (  # noqa: E402
    BLOCKS,
    COHERENCE_LIMIT_DB,
    coherence_rows,
    declared_latency,
    impulse_delay,
    probe_signal,
)

import measure as corpus_measure  # noqa: E402

MODULE_CODE = "AUR_MOUTHDECLICK"

#: The click of the probe has to lose at least this much of its peak.
TARGET_CLICK_REMOVAL_DB = 6.0

#: The bed under the click may not move by more than this.
TARGET_BED_DRIFT_DB = 1.5

#: The window the click of the probe lives in, in seconds.
CLICK_WINDOW_S = (0.249, 0.251)

#: One 64 sample block may not cost more than this (us).
CEILING_US_64 = 3.0 * 1333.3

#: The whole sensitivity range of the module.
SENSITIVITIES = tuple(float(s) for s in range(0, 11))

#: The programme of the product journey: a 1 kHz tone, 50 Hz mains hum and
#: a little noise.  It is the material the defect was found on.
TONE_HZ = 1_000.0
TONE_AMP = 0.25
HUM_HZ = 50.0
HUM_AMP = 0.05
SEED = 20_260_121

#: The graded transients of the knob test: the amplitude of every transient,
#: from the loudest to the quietest.  The score the detector measures on
#: them is 27.133, 10.902, 3.536, 0.941, 0.204 and 0.065, and the threshold
#: of the knob runs from 11.0 (at Sensitivity 0) to 6.0 (at Sensitivity 10):
#: the knob decides about the two loudest of them.
GRADED_AMPLITUDES = (0.9, 0.3, 0.1, 0.03, 0.01, 0.003)

#: Where the graded transients stand, in seconds.
GRADED_POSITIONS = (0.6, 1.1, 1.6, 2.1, 2.6, 3.1)

#: A transient counts as repaired when at least this much of it is gone.
REPAIRED_FRACTION = 0.5

#: The latency of the module (the look ahead of the grid of the engine).
LATENCY = 1664


def db(value: float) -> float:
    """``value`` in decibels, with a floor so the log never blows up."""
    return 20.0 * math.log10(max(float(value), 1e-12))


def programme(seconds: float = 3.5, clicks: bool = False) -> np.ndarray:
    """The journey programme: a 1 kHz tone, 50 Hz hum, a little noise."""
    n = int(seconds * 48_000)
    t = np.arange(n, dtype=np.float64) / 48_000.0
    rng = np.random.default_rng(SEED)
    mono = (
        TONE_AMP * np.sin(2.0 * math.pi * TONE_HZ * t)
        + HUM_AMP * np.sin(2.0 * math.pi * HUM_HZ * t)
        + 0.002 * rng.standard_normal(n)
    )
    if clicks:
        for position in (0.4, 1.3, 2.2):
            start = int(position * 48_000)
            mono[start : start + 30] += 0.9
    return np.stack([mono, 0.98 * mono], axis=1)


def module_of(sensitivity: float = 3.0) -> MouthDeClick:
    """A module with the given sensitivity."""
    module = MouthDeClick(48_000, 2)
    module.params.set("Sensitivity", float(sensitivity))
    return module


def render(signal: np.ndarray, sensitivity: float = 3.0) -> np.ndarray:
    """Run the module over the signal the way the server renders it."""
    module = module_of(sensitivity)
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
    material through the mouth band).
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


def score_of(signal: np.ndarray, sensitivity: float = 3.0) -> float:
    """The largest score the engine measures on the material."""
    with score_recorder() as best:
        render(signal, sensitivity)
    return best["value"]


# ---------------------------------------------------------------------------
# 1. the gate of the shared harness
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_mouth_de_click_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_mouth_de_click_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_mouth_de_click_removes_the_click():
    """The click of the probe has to lose at least 6 dB of its peak."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = probe_signal(MODULE_CODE)
    module = registry.create(MODULE_CODE, 48000, 2)
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    first = int(round(CLICK_WINDOW_S[0] * 48000))
    last = int(round(CLICK_WINDOW_S[1] * 48000))
    before = float(np.max(np.abs(signal[first:last, 0])))
    after = float(np.max(np.abs(out[first:last, 0])))
    change = 20.0 * np.log10(after / before)
    assert change <= -TARGET_CLICK_REMOVAL_DB, (
        f"the click only lost {abs(change):.2f} dB, the gate asks for "
        f"{TARGET_CLICK_REMOVAL_DB:.1f} dB"
    )


def test_mouth_de_click_keeps_the_bed():
    """The bed under the click may not move by more than 1.5 dB."""
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
def test_mouth_de_click_keeps_the_length_and_stays_finite(block):
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


def test_mouth_de_click_fits_into_the_block_budget():
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

    The shared range (0.55 .. 3.0) lies below the whole population of the
    transients of this module: the score of a mouth click is 26.950 and the
    score of an impulse of the corpus clip is 27.450 to 27.625, so every
    transient of the module stood above every setting of the knob and the
    knob could not change the answer at all.
    """
    module = module_of(3.0)
    settings = module.settings()
    assert settings.threshold_range == _MOUTH_THRESHOLD_RANGE
    assert settings.threshold_range is not None
    assert settings.threshold_range != (_THRESHOLD_LOW, _THRESHOLD_HIGH)


def test_the_threshold_falls_with_the_sensitivity():
    """A higher sensitivity has to accept a weaker transient.

    The threshold of the knob is the factor the robust background of the
    material has to be exceeded by, and it has to run *through* the
    population of the transients: a range that lies below all of them
    cannot change the answer of the module.
    """
    values = [
        sensitivity_threshold(s, _MOUTH_THRESHOLD_RANGE) for s in SENSITIVITIES
    ]
    assert all(a > b for a, b in zip(values, values[1:])), values
    assert values[0] == pytest.approx(_MOUTH_THRESHOLD_RANGE[1])
    assert values[-1] == pytest.approx(_MOUTH_THRESHOLD_RANGE[0])


def test_the_range_runs_through_the_population_of_the_module():
    """The low end has to be above the steady material, the high end below
    the transient that has to be repaired at every setting.

    The measurement is the score the engine measures on the material, in
    the module's own path: the steady programme reaches 5.010, the click
    of 0.9 on the same programme reaches 27.133.
    """
    steady = programme(seconds=2.0, clicks=False)
    dirty = programme(seconds=2.0, clicks=True)
    steady_score = score_of(steady)
    click_score = score_of(dirty)
    low, high = _MOUTH_THRESHOLD_RANGE
    assert low > steady_score, (
        f"the low end of the range is {low}, the steady programme reaches a "
        f"score of {steady_score:.3f}"
    )
    assert high < click_score, (
        f"the high end of the range is {high}, the click reaches a score of "
        f"{click_score:.3f} and has to be repaired at every setting"
    )


# ---------------------------------------------------------------------------
# 3. the knob moves the numbers
# ---------------------------------------------------------------------------
def graded_programme() -> tuple[np.ndarray, np.ndarray]:
    """The journey programme with six graded transients, and without them."""
    clean = programme(seconds=4.0, clicks=False)[:, 0]
    dirty = clean.copy()
    for amplitude, position in zip(GRADED_AMPLITUDES, GRADED_POSITIONS):
        index = int(position * 48_000)
        dirty[index : index + 4] += amplitude
    return clean, dirty


def repaired_count(sensitivity: float) -> int:
    """How many of the graded transients the module repairs at this setting.

    A transient counts as repaired when at least half of it is gone: the
    residual of the repair is measured against the programme without the
    transient, so what is left of it is what the repair did not take.
    """
    clean, dirty = graded_programme()
    out = aligned(render(np.stack([dirty, dirty], axis=1), sensitivity))
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
    graded transients (the amplitudes 0.9, 0.3, 0.1, 0.03, 0.01 and 0.003)
    are 1 at Sensitivity 0 and 2 from Sensitivity 2 on.
    """
    counts = {s: repaired_count(s) for s in (0.0, 2.0, 5.0, 10.0)}
    assert counts[10.0] > counts[0.0], (
        f"the knob did not repair more at Sensitivity 10 than at 0: {counts}"
    )
    values = [counts[s] for s in (0.0, 2.0, 5.0, 10.0)]
    assert all(a <= b for a, b in zip(values, values[1:])), (
        f"the knob repaired less when it was turned up: {counts}"
    )


def test_a_steady_programme_is_not_touched_at_any_sensitivity():
    """Nothing at all may change: the material is not a transient."""
    signal = programme(seconds=2.0, clicks=False)
    for sensitivity in SENSITIVITIES:
        out = aligned(render(signal, sensitivity))
        delta = float(np.max(np.abs(out - signal[: out.shape[0]])))
        assert delta == 0.0, (
            f"Sensitivity {sensitivity} changed the steady programme by "
            f"{delta:.3e}"
        )


#: The six impulses of the corpus clip: the time in seconds and the level
#: in dBFS.
CORPUS_CLICKS = (
    (0.250, 0.0),
    (0.750, -4.0),
    (1.250, -8.0),
    (1.750, -12.0),
    (2.250, -16.0),
    (2.750, -20.0),
)

#: The bed of the corpus clip: a 480 Hz tone at -40 dBFS.  A click can
#: never be removed below the bed it sits on.
CORPUS_BED_DBFS = -40.0


def corpus_clicks() -> np.ndarray:
    """The corpus click clip: six impulses over a 480 Hz bed."""
    samples, rate = corpus_measure.load_clip("clicks")
    assert rate == 48_000
    return samples


def test_the_corpus_clicks_are_removed_at_every_sensitivity():
    """Every impulse of the corpus clip is taken out, at every setting.

    The bed of the clip is a 480 Hz tone at -40 dBFS, so a click can never
    be removed further than down to the bed: the removal of a click at
    L dBFS is L + 40 dB.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = corpus_clicks()
    for sensitivity in SENSITIVITIES:
        module = registry.create(MODULE_CODE, 48000, 2)
        module.params.set("Sensitivity", float(sensitivity))
        out = np.asarray(module.process(signal.copy()), dtype=np.float64)
        for index, level in CORPUS_CLICKS:
            at = int(round(index * 48000))
            before = float(np.max(np.abs(signal[at - 32 : at + 33, 0])))
            after = float(np.max(np.abs(out[at - 32 : at + 33, 0])))
            removal = db(before) - db(after)
            assert removal >= level - CORPUS_BED_DBFS - 0.5, (
                f"Sensitivity {sensitivity}: the click at {index:.2f} s was "
                f"only taken down by {removal:.2f} dB"
            )


def test_the_default_removes_the_corpus_clicks():
    """The default has to do the job it did before the range was given to
    the module: the six impulses of the corpus clip have to go.

    The measured removals of the default are 40.0, 36.0, 32.0, 28.0, 24.0
    and 20.0 dB: the bed of the clip (a 480 Hz tone at -40 dBFS) is what is
    left of them.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = corpus_clicks()
    module = registry.create(MODULE_CODE, 48000, 2)
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    for index, level in CORPUS_CLICKS:
        at = int(round(index * 48000))
        before = float(np.max(np.abs(signal[at - 32 : at + 33, 0])))
        after = float(np.max(np.abs(out[at - 32 : at + 33, 0])))
        removal = db(before) - db(after)
        assert removal >= level - CORPUS_BED_DBFS - 0.5, (
            f"the click at {index:.2f} s was only taken down by {removal:.2f} dB"
        )
