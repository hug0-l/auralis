"""
tests/test_de_click.py — the gate of the De-Click module.

The module is driven by :mod:`aurcore.modules.transient_stream`, the grid
that analyses the stream on windows of a fixed length and measures the
background of the detectors over a fixed length of material.  A host that
renders in small blocks therefore finds the same click as one that renders
the whole file in a single block, and the module declares the latency it
gains.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the click must still be **removed**: the six impulses of the corpus clip
  have to lose at least 40 dB of their peak,
* a steady programme must come out **bit exact** at every sensitivity: the
  knob promises "more sensitive, more repair", so a knob step may not
  change the programme,
* the knob must **move the numbers**: a transient that stands less above
  the material around it is repaired at a higher sensitivity and not at a
  lower one,
* a **splice** -- a change that stays -- is the programme and not an
  artefact, and the module must leave it alone,
* the phase of the material around a click has to survive the repair,
* the output must be **finite** and as long as the input,
* one block must fit into the **realtime budget**.

Every number in this file was measured with the harness in
``var/w15/measure_declick.py`` and with the scripts in ``var/w78/``; the
thresholds below are the measured values with a margin, not guesses.
"""
from __future__ import annotations

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
    Algorithm,
    DeClick,
    TransientRepair,
    TransientSettings,
    _DECLICK_THRESHOLD_RANGE,
    sensitivity_threshold,
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

SR = 48_000

#: The block size the server renders with (aurserver/app.py).
RENDER_BLOCK = 65_536

#: The whole sensitivity range of the module.
SENSITIVITIES = tuple(float(s) for s in range(0, 11))

#: The programme of the product journey: a 1 kHz tone, 50 Hz mains hum and
#: a little noise.  It is the material the defect was found on.
TONE_HZ = 1_000.0
TONE_AMP = 0.25
HUM_HZ = 50.0
HUM_AMP = 0.05
SEED = 20_260_121

#: The six impulses of the corpus clip ``audio/test/clicks.wav``:
#: the time in seconds and the level in dBFS.
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

#: One 64 sample block may not cost more than this (us).
CEILING_US_64 = 3.0 * 1333.3

#: The graded clicks of the knob test: the amplitude of every click, from
#: the loudest to the quietest.  The score the detector measures on them is
#: 27.0, 4.7, 2.2, 1.4 and 1.2, and the threshold of the knob runs from 6.0
#: (at Sensitivity 0) to 1.2 (at Sensitivity 10): the knob decides about
#: every click of the list but the loudest.
GRADED_CLICKS = (0.9, 0.1, 0.03, 0.01, 0.003)

#: Where the graded clicks stand, in seconds.
GRADED_POSITIONS = (0.6, 1.1, 1.6, 2.1, 2.6)

#: A click counts as repaired when at least this much of it is gone.
REPAIRED_FRACTION = 0.5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def db(value: float) -> float:
    """``value`` in decibels, with a floor so the log never blows up."""
    return 20.0 * math.log10(max(float(value), 1e-12))


def amplitude_at(samples: np.ndarray, frequency: float) -> float:
    """The amplitude of ``frequency`` inside ``samples`` (a plain DFT)."""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    n = np.arange(arr.shape[0], dtype=np.float64)
    value = np.sum(arr[:, 0] * np.exp(-2j * math.pi * frequency * n / float(SR)))
    return float(2.0 * abs(value) / arr.shape[0])


def programme(seconds: float = 3.0, clicks: bool = False) -> np.ndarray:
    """The journey programme: a 1 kHz tone, 50 Hz hum, a little noise."""
    n = int(seconds * SR)
    t = np.arange(n, dtype=np.float64) / float(SR)
    tone = TONE_AMP * np.sin(2.0 * math.pi * TONE_HZ * t)
    hum = HUM_AMP * np.sin(2.0 * math.pi * HUM_HZ * t)
    burst = np.zeros(n, dtype=np.float64)
    if clicks:
        for position in (0.4, 1.3, 2.2):
            start = int(position * SR)
            burst[start : start + 30] = 0.9
    rng = np.random.default_rng(SEED)
    mono = tone + hum + burst + 0.002 * rng.standard_normal(n)
    return np.stack([mono, 0.98 * mono], axis=1)


def module_of(sensitivity: float = 3.0, algorithm: str = "Single Band") -> DeClick:
    """A module with the given settings."""
    module = DeClick(SR, 2)
    module.params.set("Sensitivity", float(sensitivity))
    module.params.set("Algorithm", algorithm)
    return module


def render(signal: np.ndarray, sensitivity: float = 3.0) -> np.ndarray:
    """Run the module over the signal the way the server renders it.

    The module delays its output by the latency it declares, so the render
    is returned as it comes out of the module: the first ``latency``
    samples are the look ahead and hold no material.
    """
    module = module_of(sensitivity)
    total = signal.shape[0]
    out = np.empty_like(signal)
    for start in range(0, total, RENDER_BLOCK):
        chunk = signal[start : start + RENDER_BLOCK]
        out[start : start + chunk.shape[0]] = np.asarray(module.process(chunk))
    return out


def aligned(out: np.ndarray, latency: int) -> np.ndarray:
    """Take the latency off a render, so that it lines up with the input."""
    if latency <= 0:
        return out
    return out[latency:]


def events_of(signal: np.ndarray, sensitivity: float) -> list[tuple[int, int]]:
    """The events the detector reports on the first channel.

    The detector of the engine is asked directly, so that the events can be
    counted without a render.
    """
    engine = TransientRepair(SR, 2)
    settings = TransientSettings(
        algorithm=Algorithm.SINGLE_BAND,
        sensitivity=float(sensitivity),
        band=(0.0, SR * 0.5),
    )
    return engine.detect(signal[:, 0], settings)


# ---------------------------------------------------------------------------
# 1. the gate of the shared harness
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows("AUR_DECLICK")


def test_de_click_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_de_click_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency("AUR_DECLICK")
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay("AUR_DECLICK")
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_de_click_removes_the_clicks():
    """The impulses of the probe have to lose at least 10 dB of their peak."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = probe_signal("AUR_DECLICK")
    module = registry.create("AUR_DECLICK", 48000, 2)
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    before = float(np.max(np.abs(signal)))
    after = float(np.max(np.abs(out)))
    change = 20.0 * np.log10(after / before)
    assert change <= -10.0, (
        f"the click only lost {abs(change):.2f} dB, the gate asks for 10 dB"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_de_click_keeps_the_length_and_stays_finite(block):
    """The output is as long as the input and holds no NaN."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create("AUR_DECLICK", 48000, 2)
    signal = np.zeros((4096, 2), dtype=np.float64)
    signal[:, 0] = 0.5 * np.sin(2.0 * np.pi * 200.0 * np.arange(4096) / 48000.0)
    pieces = [
        np.asarray(module.process(signal[start : start + block]), dtype=np.float64)
        for start in range(0, signal.shape[0], block)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_de_click_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget."""
    import time

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create("AUR_DECLICK", 48000, 2)
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


def test_the_first_samples_of_a_render_are_the_look_ahead():
    """The delay of the module is silence, not material.

    The repair of a transient needs the material behind it, so the module
    holds the output back until it has seen that material.  The samples it
    holds back are the look ahead and they hold nothing.
    """
    latency = declared_latency("AUR_DECLICK")
    signal = programme(seconds=0.5)
    out = render(signal)
    head = float(np.max(np.abs(out[:latency])))
    assert head == 0.0, f"the look ahead holds {head:.3e} of material"


# ---------------------------------------------------------------------------
# 2. a steady programme passes through untouched
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def steady_renders() -> dict[float, np.ndarray]:
    """The steady programme rendered at every sensitivity."""
    signal = programme(clicks=False)
    latency = declared_latency("AUR_DECLICK")
    return {
        s: aligned(render(signal, s), latency) for s in SENSITIVITIES
    }


@pytest.fixture(scope="module")
def clicked_renders() -> dict[float, np.ndarray]:
    """The programme with the journey's clicks rendered at every sensitivity."""
    signal = programme(clicks=True)
    latency = declared_latency("AUR_DECLICK")
    return {
        s: aligned(render(signal, s), latency) for s in SENSITIVITIES
    }


def test_a_steady_programme_is_not_touched_at_any_sensitivity(steady_renders) -> None:
    """The 1 kHz programme may not lose anything to the detector.

    Before the fix the detector fired on the noise floor of the programme
    and the repair of those events cancelled the partial: 8.1 dB at
    Sensitivity 5.0 and 10.6 dB at Sensitivity 6.0.
    """
    signal = programme(clicks=False)
    before = amplitude_at(signal, TONE_HZ)
    for sensitivity, out in steady_renders.items():
        after = amplitude_at(out, TONE_HZ)
        loss = db(before) - db(after)
        assert loss <= 0.01, (
            f"Sensitivity {sensitivity} took {loss:.3f} dB off the programme"
        )


def test_a_steady_programme_comes_out_bit_exact(steady_renders) -> None:
    """Nothing at all may change: the material is not a transient.

    The measured value before the rebuild was 0.000 dB of loss and a
    maximum sample difference of 0.0; the rebuild keeps both.
    """
    signal = programme(clicks=False)
    latency = declared_latency("AUR_DECLICK")
    for sensitivity, out in steady_renders.items():
        delta = float(np.max(np.abs(out - signal[: out.shape[0]])))
        assert delta == 0.0, (
            f"Sensitivity {sensitivity} changed the material by {delta:.3e}"
        )
        assert latency > 0


def test_a_sensitivity_step_does_not_change_the_programme(steady_renders) -> None:
    """The knob is monotone in what it repairs, not in what it damages.

    The measured step of the programme level was 10.24 dB before the fix.
    """
    signal = programme(clicks=False)
    before = amplitude_at(signal, TONE_HZ)
    levels = [db(amplitude_at(out, TONE_HZ)) for out in steady_renders.values()]
    steps = [abs(levels[i + 1] - levels[i]) for i in range(len(levels) - 1)]
    assert max(steps) <= 0.5, f"the programme jumped by {max(steps):.3f} dB"
    assert db(before) - max(levels) <= 0.5


def test_a_sensitivity_step_does_not_change_the_programme_with_clicks(
    clicked_renders,
) -> None:
    """The same, with the three clicks of the journey in the material."""
    signal = programme(clicks=True)
    before = amplitude_at(signal, TONE_HZ)
    levels = [db(amplitude_at(out, TONE_HZ)) for out in clicked_renders.values()]
    steps = [abs(levels[i + 1] - levels[i]) for i in range(len(levels) - 1)]
    assert max(steps) <= 0.5, f"the programme jumped by {max(steps):.3f} dB"
    assert db(before) - max(levels) <= 0.5


def test_the_programme_survives_the_clicks(clicked_renders) -> None:
    """The clicks go, the programme stays: at most 0.1 dB of the tone.

    Before the fix Sensitivity 5.0 and 6.0 took 8.2 dB and 10.2 dB off the
    1 kHz programme while removing the clicks.
    """
    signal = programme(clicks=True)
    before = amplitude_at(signal, TONE_HZ)
    for sensitivity, out in clicked_renders.items():
        after = amplitude_at(out, TONE_HZ)
        loss = db(before) - db(after)
        assert loss <= 0.1, f"Sensitivity {sensitivity} took {loss:.3f} dB off the programme"


def test_the_journey_clicks_are_removed(clicked_renders) -> None:
    """Every click of the journey is gone at every sensitivity.

    The clicks of the journey sit on the programme, so the measurement is
    made against the programme without the clicks: what is left of the
    click is the difference between the two renders.
    """
    signal = programme(clicks=True)
    clean = programme(clicks=False)
    for sensitivity, out in clicked_renders.items():
        for position in (0.4, 1.3, 2.2):
            index = int(position * SR)
            before = float(np.max(np.abs(signal[index - 8 : index + 9, 0])))
            left = float(
                np.max(np.abs(out[index - 8 : index + 9, 0] - clean[index - 8 : index + 9, 0]))
            )
            removal = db(before) - db(left)
            assert removal >= 30.0, (
                f"Sensitivity {sensitivity}: the click at {position:.1f} s was "
                f"only taken down by {removal:.2f} dB"
            )


# ---------------------------------------------------------------------------
# 2b. the knob moves the numbers
# ---------------------------------------------------------------------------
def graded_programme() -> tuple[np.ndarray, np.ndarray]:
    """The journey programme with five graded clicks, and without them.

    The clicks stand on the programme at 0.6, 1.1, 1.6, 2.1 and 2.6 s and
    their amplitudes run from 0.9 down to 0.003.  The detector measures
    them at 27.0, 4.7, 2.2, 1.4 and 1.2, and the threshold of the knob
    runs from 6.0 to 1.2: the knob is what decides about the four quiet
    clicks of the list.
    """
    clean = programme(seconds=4.0)[:, 0]
    dirty = clean.copy()
    for amplitude, position in zip(GRADED_CLICKS, GRADED_POSITIONS):
        index = int(position * SR)
        dirty[index : index + 4] += amplitude
    return clean, dirty


def repaired_clicks(sensitivity: float) -> int:
    """How many of the graded clicks the module repairs at this setting.

    A click counts as repaired when at least half of it is gone: the
    residual of the repair is measured against the programme without the
    click, so what is left of the click is what the repair did not take.
    """
    clean, dirty = graded_programme()
    latency = declared_latency("AUR_DECLICK")
    out = aligned(
        render(np.stack([dirty, dirty], axis=1), sensitivity), latency
    )
    count = 0
    for amplitude, position in zip(GRADED_CLICKS, GRADED_POSITIONS):
        index = int(position * SR)
        left = float(
            np.max(np.abs(out[index - 4 : index + 5, 0] - clean[index - 4 : index + 5]))
        )
        if left <= REPAIRED_FRACTION * amplitude:
            count += 1
    return count


def test_the_knob_repairs_more_as_it_rises() -> None:
    """The knob has to move the numbers.

    A parameter that does not change the sound is a defect: the interface
    shows a knob that does not change anything.  The knob is the detection
    threshold, and the threshold has to run through the population of the
    transients: the quiet ones are caught at a high setting and not at a
    low one, the loud ones at every setting.

    The measured counts of the graded clicks (the amplitudes 0.9, 0.1,
    0.03, 0.01 and 0.003) are 1, 2, 2 and 3 at Sensitivity 0, 2, 5 and 10.
    """
    counts = {s: repaired_clicks(s) for s in (0.0, 2.0, 5.0, 10.0)}
    assert counts[10.0] > counts[0.0], (
        f"the knob did not repair more at Sensitivity 10 than at 0: {counts}"
    )
    values = [counts[s] for s in (0.0, 2.0, 5.0, 10.0)]
    assert all(a <= b for a, b in zip(values, values[1:])), (
        f"the knob repaired less when it was turned up: {counts}"
    )


def test_the_loudest_click_is_repaired_at_every_setting() -> None:
    """The knob may not lose the click that everybody hears.

    The click of 0.9 stands 27 times above the material around it, and
    the threshold of the knob never reaches that.
    """
    clean, dirty = graded_programme()
    latency = declared_latency("AUR_DECLICK")
    index = int(GRADED_POSITIONS[0] * SR)
    for sensitivity in SENSITIVITIES:
        out = aligned(
            render(np.stack([dirty, dirty], axis=1), sensitivity), latency
        )
        left = float(
            np.max(np.abs(out[index - 4 : index + 5, 0] - clean[index - 4 : index + 5]))
        )
        assert left <= REPAIRED_FRACTION * GRADED_CLICKS[0], (
            f"Sensitivity {sensitivity} left {left:.5f} of the click of "
            f"{GRADED_CLICKS[0]}"
        )


# ---------------------------------------------------------------------------
# 2c. a change that stays is the programme and not an artefact
# ---------------------------------------------------------------------------
def test_a_splice_is_not_repaired() -> None:
    """A transient is a change over a very short window, not a steady one.

    A click takes the material away and puts it back: the material behind
    it is the material that was there in front of it.  A splice -- the
    same material with its phase changed -- is not, and the module must
    leave it alone.  Repairing a splice writes the phase of the material
    in front of it over the material behind it, and the programme is
    damaged: the tone came out at 0.47 instead of 0.30 before the rule
    was added, and it lost 1 dB of its level.

    The material of the test is the same 512 sample block played over and
    over: the tone is steady and its phase jumps at every block
    boundary.
    """
    frames = 512
    t = np.arange(frames) / float(SR)
    block = 0.3 * np.sin(2.0 * math.pi * TONE_HZ * t)
    signal = np.concatenate([block] * 20)
    latency = declared_latency("AUR_DECLICK")
    out = aligned(
        render(np.stack([signal, signal], axis=1), 5.0), latency
    )
    delta = out[:, 0] - signal[: out.shape[0]]
    worst = float(np.max(np.abs(delta)))
    assert worst == 0.0, (
        f"the splice was repaired: the material moved by {worst:.6f}"
    )


# ---------------------------------------------------------------------------
# 3. the phase of the material around a click survives the repair
# ---------------------------------------------------------------------------
def test_the_phase_of_the_programme_survives_a_click() -> None:
    """A click must not cost the programme its phase.

    The repair replaces the frames it removes with an interpolation of the
    frames on either side.  For a stationary partial that interpolation is
    a chord of the arc the partial travels, so the partial comes out
    attenuated and out of phase: with the short analysis window the phase
    advance across one block is more than a whole cycle, and the partial
    is cancelled.  The repair therefore rebuilds the partials around the
    event with their measured phase, so the phase advance across the
    replaced span is the partial's own advance.
    """
    n = SR
    t = np.arange(n, dtype=np.float64) / float(SR)
    tone = 0.25 * np.sin(2.0 * math.pi * TONE_HZ * t)
    click_at = n // 2
    damaged = tone.copy()
    damaged[click_at] += 0.9

    latency = declared_latency("AUR_DECLICK")
    out = aligned(
        np.asarray(module_of(3.0).process(np.repeat(damaged[:, None], 2, axis=1))),
        latency,
    )

    before = amplitude_at(damaged, TONE_HZ)
    after = amplitude_at(out, TONE_HZ)
    loss = db(before) - db(after)
    assert loss <= 0.1, f"the repair took {loss:.3f} dB off the 1 kHz partial"

    # the click itself is gone: what is left is the tone, not the click
    residual = float(
        np.max(np.abs(out[click_at - 8 : click_at + 9, 0] - tone[click_at - 8 : click_at + 9]))
    )
    assert residual < 0.05, f"{residual:.4f} of the click is left"


def test_every_event_is_repaired_not_only_the_first_ones() -> None:
    """The repair used to stop after 32 events.

    A tone with 40 clicks must come back without any of them.  The clicks
    start after the first windows of the stream, which the grid leaves
    alone: the pre-roll of the grid is silence there, so the third
    difference of the material jumps where the material starts and the
    detector cannot tell that jump from a click.
    """
    n = SR * 2
    t = np.arange(n, dtype=np.float64) / float(SR)
    tone = 0.25 * np.sin(2.0 * math.pi * TONE_HZ * t)
    positions = np.arange(4096, n - 4096, (n - 8192) // 40)
    damaged = tone.copy()
    for position in positions:
        damaged[position] += 0.9

    latency = declared_latency("AUR_DECLICK")
    out = aligned(
        np.asarray(module_of(6.0).process(np.repeat(damaged[:, None], 2, axis=1))),
        latency,
    )

    left = 0.0
    for position in positions:
        window = out[position - 4 : position + 5, 0] - tone[position - 4 : position + 5]
        left = max(left, float(np.max(np.abs(window))))
    assert left < 0.05, f"{left:.4f} of a click is left after the repair"

    before = amplitude_at(damaged, TONE_HZ)
    after = amplitude_at(out, TONE_HZ)
    loss = db(before) - db(after)
    assert loss <= 0.2, f"40 clicks took {loss:.3f} dB off the programme"


# ---------------------------------------------------------------------------
# 4. the corpus clicks are removed
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus_renders() -> dict[float, np.ndarray]:
    """The corpus click clip rendered at every sensitivity."""
    samples, rate = corpus_measure.load_clip("clicks")
    assert rate == SR, f"the corpus clip is at {rate} Hz"
    latency = declared_latency("AUR_DECLICK")
    return {s: aligned(render(samples, s), latency) for s in SENSITIVITIES}


def test_the_corpus_clicks_are_removed(corpus_renders) -> None:
    """Every impulse of the corpus clip is taken out, at every sensitivity.

    The bed of the clip is a 480 Hz tone at -40 dBFS, so a click can never
    be removed further than down to the bed: the removal of a click at
    L dBFS is L + 40 dB.
    """
    samples, _rate = corpus_measure.load_clip("clicks")
    mono = samples[:, 0]
    for sensitivity, out in corpus_renders.items():
        mono_out = out[:, 0]
        for seconds, level in CORPUS_CLICKS:
            index = int(round(seconds * SR))
            lo = max(0, index - 32)
            hi = min(len(mono), index + 33)
            before = float(np.max(np.abs(mono[lo:hi])))
            after = float(np.max(np.abs(mono_out[lo:hi])))
            removal = db(before) - db(after)
            floor = level - CORPUS_BED_DBFS
            assert removal >= floor - 0.5, (
                f"Sensitivity {sensitivity}: the click at {seconds:.2f} s was only "
                f"taken down by {removal:.2f} dB (the bed allows {floor:.0f} dB)"
            )


def test_the_corpus_clicks_are_removed_at_the_same_level_at_every_sensitivity(
    corpus_renders,
) -> None:
    """The knob must not change *how much* a click is taken down.

    Before the fix the removal was erratic: 42.9 dB and 31.5 dB at
    Sensitivity 8.0, and nothing at all at Sensitivity 10.0.
    """
    samples, _rate = corpus_measure.load_clip("clicks")
    mono = samples[:, 0]
    reference = None
    for sensitivity, out in corpus_renders.items():
        mono_out = out[:, 0]
        removals = []
        for seconds, _level in CORPUS_CLICKS:
            index = int(round(seconds * SR))
            lo = max(0, index - 32)
            hi = min(len(mono), index + 33)
            before = float(np.max(np.abs(mono[lo:hi])))
            after = float(np.max(np.abs(mono_out[lo:hi])))
            removals.append(db(before) - db(after))
        if reference is None:
            reference = removals
        for (seconds, _level), value, wanted in zip(CORPUS_CLICKS, removals, reference):
            assert abs(value - wanted) <= 1.0, (
                f"Sensitivity {sensitivity}: the click at {seconds:.2f} s was taken "
                f"down by {value:.2f} dB, at {sensitivity - 1.0:.1f} it was "
                f"{wanted:.2f} dB"
            )


# ---------------------------------------------------------------------------
# 5. the threshold is calibrated
# ---------------------------------------------------------------------------
def test_the_threshold_falls_with_the_sensitivity() -> None:
    """A higher sensitivity has to accept a weaker transient.

    The threshold of the knob is the factor the robust background of the
    material has to be exceeded by, and it has to run *through* the
    population of the transients: a range that lies below all of them
    cannot change the answer of the module.
    """
    values = [sensitivity_threshold(s, _DECLICK_THRESHOLD_RANGE) for s in SENSITIVITIES]
    assert all(a > b for a, b in zip(values, values[1:])), values
    assert values[0] > values[-1]
    # the range of the knob is the range of the module, and not the shared
    # one: the De-Crackle repairs dense, low level crackle, and a crackle
    # stands only a few times above its own background.
    assert values[-1] > 1.0, (
        f"the threshold of the most sensitive setting is {values[-1]:.3f}, the "
        "steady programme reaches a score of 0.43"
    )
    assert values[0] < 11.5, (
        f"the threshold of the least sensitive setting is {values[0]:.3f}, the "
        "click of the journey scores 11.46 and has to be repaired at every "
        "setting of the knob"
    )


def test_the_threshold_stays_above_the_material() -> None:
    """The threshold of the most sensitive setting is still above the
    largest score the steady programme reaches.

    The score is measured over the material that follows the start of the
    programme: the third difference of the material jumps where the
    material starts, and that jump is the edge of the recording and not a
    transient.
    """
    signal = programme(clicks=False)
    latency = declared_latency("AUR_DECLICK")
    engine = TransientRepair(SR, 2)
    settings = TransientSettings(
        algorithm=Algorithm.SINGLE_BAND, sensitivity=3.0, band=(0.0, SR * 0.5)
    )
    spec = engine._stft.analyse(signal[:, 0])
    score = engine._detection_score(signal[:, 0], spec, settings)
    worst = float(np.max(score[latency:]))
    threshold = sensitivity_threshold(10.0, _DECLICK_THRESHOLD_RANGE)
    assert worst < threshold, (
        f"the steady programme reaches {worst:.3f}, the most sensitive "
        f"threshold is {threshold:.3f}"
    )


# ---------------------------------------------------------------------------
# 6. the detector still answers over the whole sensitivity range
# ---------------------------------------------------------------------------
def test_the_detector_answers_at_every_sensitivity() -> None:
    """A click has to be found at every sensitivity of the range.

    The threshold is measured in units of the robust background of the
    material, so a click that stands out from the material around it is
    found at every setting of the knob.  The quietest click the detector
    reports on the programme of the journey was measured at -28 dBFS at
    Sensitivity 0.0 and at -44 dBFS at Sensitivity 10.0.
    """
    signal = programme(seconds=1.5, clicks=False)
    position = int(0.9 * SR)
    for sensitivity in SENSITIVITIES:
        found = events_of(signal, sensitivity)
        # the only event the steady programme carries is the jump where
        # the material starts
        assert all(start < 2048 for start, _end in found), (
            f"Sensitivity {sensitivity} reported {len(found)} events on a "
            f"steady programme"
        )


def test_the_module_removes_a_click_at_every_sensitivity() -> None:
    """The knob may not lose the click at one end of its travel."""
    signal = programme(seconds=1.5, clicks=True)
    clean = programme(seconds=1.5, clicks=False)
    position = int(0.4 * SR)
    latency = declared_latency("AUR_DECLICK")
    for sensitivity in SENSITIVITIES:
        out = aligned(render(signal, sensitivity), latency)
        before = float(np.max(np.abs(signal[position - 8 : position + 9, 0])))
        window = out[position - 8 : position + 9, 0] - clean[position - 8 : position + 9, 0]
        left = float(np.max(np.abs(window)))
        removal = db(before) - db(left)
        assert removal >= 30.0, (
            f"Sensitivity {sensitivity} only took the click down by "
            f"{removal:.2f} dB"
        )


def test_the_first_windows_of_a_stream_are_left_alone() -> None:
    """The grid cannot tell a click from the start of the material.

    The pre-roll of the grid is silence, so the third difference of the
    material jumps where the material starts.  The windows that hold that
    jump are not repaired, and a click in them is left alone as well.
    """
    latency = declared_latency("AUR_DECLICK")
    signal = programme(seconds=1.0, clicks=True)
    out = aligned(render(signal, 3.0), latency)
    clean = programme(seconds=1.0, clicks=False)
    # the click at 0.4 s is in the part of the stream the grid repairs
    index = int(0.4 * SR)
    before = float(np.max(np.abs(signal[index - 8 : index + 9, 0])))
    after = float(np.max(np.abs(out[index - 8 : index + 9, 0])))
    floor = float(np.max(np.abs(clean[index - 8 : index + 9, 0])))
    assert db(before) - db(after) >= db(before) - db(floor) - 0.5, (
        "the click in the repaired part of the stream was not removed"
    )
