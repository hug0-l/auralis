"""
tests/test_breath_control.py — the behaviour of the Breath Control module.

The module has one job: it takes the breathing away and it leaves the voice
alone.  The two halves of that job pull in opposite directions — the gain
that removes a breath is applied to the whole spectrum, so the wanted
content is exposed to it as well — and the gate of the product holds the
module to both.

What the tests below hold the module to
---------------------------------------
* **the breath goes** (R2).  A breath that the module is certain about has
  to lose at least 6 dB inside the breath; the module ships a gain of
  -9 dB, so it has a margin.
* **the voice stays** (R1).  The band that carries the voice may move by at
  most 1.5 dB.  Before the repair the module moved it by 3.65 dB: the gain
  was flat and the wanted content sat under the breath's gain for as long
  as the envelope took to close.
* **the gain curve is steady** (R3).  A jittering gain is heard as musical
  noise.
* **the delay is declared** (R4).  The module has to report the delay it
  really adds.
* **the output does not depend on the block size** (R5).  The analysis sits
  on one grid and the state is carried from frame to frame.
* **a clean signal is left alone** (R8).  A voice without any background may
  not move by more than 0.5 dB.

The instrument is ``qa/qualityvoice`` and the rules are
``qa/qualityvoice/rules.py``; both are shared with the report, so the
numbers in the failure messages are the numbers of the report.
"""
from __future__ import annotations

import importlib.util
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402

MODULE_CODE = "AUR_BREATH"

#: The delay the module has to declare: the analysis window is 1024 points
#: and the centred analysis needs the samples that follow a frame.
EXPECTED_LATENCY = 1024


def _load(name: str, filename: str):
    """Import a sibling of the quality instrument by path."""
    path = ROOT / "qa" / "qualityvoice" / filename
    if not path.is_file():
        pytest.skip(f"the quality instrument is missing ({path})")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HARNESS = _load("aur_qa_qualityvoice", "harness.py")
RULES = _load("aur_qa_qualityvoice_rules", "rules.py")


def _probes():
    """The probes of the shared rules that belong to the module."""
    return [p for p in RULES.PROBES if p.module == MODULE_CODE]


def _cases():
    """The synthetic measurement cases of the instrument."""
    return HARNESS.build_cases()


def _make():
    """A fresh instance of the module."""
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    return registry.create(MODULE_CODE, HARNESS.SR, 1)


@pytest.fixture(scope="module")
def results():
    """The measurement of every probe of the shared rules."""
    cases = _cases()
    out = {}
    for probe in _probes():
        module = _make()
        out[probe.name] = HARNESS.evaluate(
            cases[probe.case], module, probe.config, probe.chatter_band
        )
    return out


@pytest.fixture(scope="module")
def coherence():
    """The block coherence of every probe of the shared rules."""
    cases = _cases()
    out = {}
    for probe in _probes():
        out[probe.name] = HARNESS.measure_block_coherence(
            cases[probe.case],
            lambda: _make(),
            probe.config,
            block_size=4096,
        )
    return out


# ---------------------------------------------------------------------------
# The gate of the Breath Control module
# ---------------------------------------------------------------------------
def test_the_breath_loses_at_least_six_db(results):
    """A detected breath has to lose at least 6 dB (rule R2).

    Measurement: the energy of the 1-8 kHz band inside the breath, before
    and after the module.  The band carries the breath and nothing else, so
    everything that disappears there was breath.
    """
    for probe in _probes():
        rule = probe.rule
        if not rule.target_band:
            continue
        for region in rule.target_regions or ("",):
            key = (
                f"region_{region}_{rule.target_band}_change_db"
                if region
                else f"band_{rule.target_band}_change_db"
            )
            reduction = -results[probe.name].metrics[key]
            assert reduction >= rule.target_reduction_db, (
                f"{probe.name}: the breath only lost {reduction:.2f} dB, "
                f"{rule.target_reduction_db:.0f} dB are required"
            )


def test_the_voice_band_stays_within_one_and_a_half_db(results):
    """The band that carries the voice may not move by more than 1.5 dB (R1).

    Measurement: the energy of the 120 Hz - 3 kHz band before and after the
    module.  The band carries the wanted content, so it may not move.
    """
    for probe in _probes():
        rule = probe.rule
        change = abs(results[probe.name].metrics[f"band_{rule.protected_band}_change_db"])
        assert change <= rule.protected_change_db, (
            f"{probe.name}: the {rule.protected_band} band moved by {change:.3f} dB, "
            f"at most {rule.protected_change_db:.1f} dB are allowed"
        )


def test_the_gain_curve_does_not_chatter(results):
    """The gain curve has to be steady (rule R3).

    Measurement: the roughness of the gain the module applies in the band
    the breath lives in.  A jittering gain is heard as musical noise.
    """
    for probe in _probes():
        chatter = results[probe.name].metrics["chatter_db"]
        assert chatter <= probe.rule.chatter_db, (
            f"{probe.name}: the gain curve fluctuates by {chatter:.3f} dB, at most "
            f"{probe.rule.chatter_db:.1f} dB are allowed"
        )


def test_the_module_declares_the_delay_it_adds(results):
    """The module has to report the delay it really adds (rule R4).

    Measurement: the delay of the output against the input, found with a
    cross correlation, compared with ``latency_samples``.
    """
    module = _make()
    assert module.latency_samples == EXPECTED_LATENCY, (
        f"the module declares {module.latency_samples} samples of delay, the "
        f"analysis window is {EXPECTED_LATENCY} points"
    )
    for probe in _probes():
        metrics = results[probe.name].metrics
        reported = int(metrics["latency_reported"])
        measured = int(metrics["latency_measured"])
        assert reported > 0, f"{probe.name}: the module does not report a latency"
        assert abs(measured - reported) <= probe.rule.latency_tolerance, (
            f"{probe.name}: the module reports {reported} samples of latency but "
            f"delays the signal by {measured} samples"
        )


def test_the_output_does_not_depend_on_the_block_size(coherence):
    """The output may not depend on the block size of the host (rule R5).

    Measurement: the same signal processed in one block and in blocks of
    4096 samples.  A block coherent module returns the same samples.
    """
    for probe in _probes():
        budget = probe.rule.block_coherence_db
        if budget is None:
            continue
        difference = coherence[probe.name]["block_rms_diff_db"]
        assert difference <= budget, (
            f"{probe.name}: processing the signal in blocks of 4096 samples changes "
            f"it by {difference:.1f} dB, a block coherent module changes nothing"
        )


def test_a_clean_voice_is_left_alone(results):
    """A clean signal may not move by more than 0.5 dB (rule R8).

    Measurement: the energy of the 120 Hz - 3 kHz band of a voice that has
    no background at all.
    """
    for probe in _probes():
        rule = probe.rule
        if rule.clean_budget_db is None:
            continue
        change = abs(results[probe.name].metrics[f"band_{rule.protected_band}_change_db"])
        assert change <= rule.clean_budget_db, (
            f"{probe.name}: the clean signal moved by {change:.3f} dB, at most "
            f"{rule.clean_budget_db:.1f} dB are allowed"
        )


# ---------------------------------------------------------------------------
# the shape of the module
# ---------------------------------------------------------------------------
def test_the_module_ships_the_defaults_of_the_schema():
    """The module has to ship the defaults the schema records.

    Measurement: the defaults of the parameter list of the module against
    the table of the schema (``aurcore/schema/params.tsv``).
    """
    schema = ROOT / "aurcore" / "schema" / "params.tsv"
    if not schema.is_file():
        pytest.skip("the schema is missing")
    expected: dict[str, str] = {}
    for line in schema.read_text(encoding="utf-8").splitlines():
        if not line.startswith(MODULE_CODE + "\t"):
            continue
        parts = line.split("\t")
        # code, display, category, edition, element, name, type, default
        expected[parts[5]] = parts[7]
    if not expected:
        pytest.skip("the module is not in the schema")
    module = _make()
    shipped = {d.name: module.params.get(d.name) for d in module.describe()}
    for name, value in expected.items():
        if name not in shipped:
            continue
        if isinstance(shipped[name], bool):
            assert shipped[name] == (value not in ("0", "", "False")), (
                f"{name} ships as {shipped[name]}, the schema says {value}"
            )
            continue
        if isinstance(shipped[name], str):
            assert shipped[name] == value, (
                f"{name} ships as {shipped[name]!r}, the schema says {value!r}"
            )
            continue
        assert float(shipped[name]) == pytest.approx(float(value), abs=1e-9), (
            f"{name} ships as {shipped[name]}, the schema says {value}"
        )


def test_the_target_mode_pulls_the_breath_down_to_the_target():
    """``Target Mode`` has to pull the breath down to the target level.

    Measurement: the level of the breath band inside the breath, with the
    target at -30 dBFS and with the gain mode at the same depth.
    """
    cases = _cases()
    case = cases["voice_breath"]
    x = np.asarray(case.x, dtype=np.float64)
    region = case.regions["breath"]
    band = case.bands["breath"]

    def level(module):
        out = np.asarray(module.process(x.copy()), dtype=np.float64)
        delay = int(module.latency_samples)
        out = out[delay:delay + x.shape[0] - delay]
        return HARNESS.band_energy_db(out, band[0], band[1], start=region[0], end=region[1])

    module = _make()
    module.params.set("Mode", "Target Mode")
    module.params.set("Target Level", -30.0)
    targeted = level(module)
    module = _make()
    gain = level(module)
    assert targeted < gain, (
        f"the target mode did not pull the breath down ({targeted:.2f} dB against "
        f"{gain:.2f} dB)"
    )


def test_the_output_breaths_only_mode_renders_the_removed_breath():
    """``Output Breaths Only`` has to render what the module removed.

    Measurement: the sum of the two modes has to be the input: the removed
    breath and what is left add up to the signal that went in.
    """
    cases = _cases()
    case = cases["voice_breath"]
    x = np.asarray(case.x, dtype=np.float64)

    module = _make()
    kept = np.asarray(module.process(x.copy()), dtype=np.float64)
    module = _make()
    module.params.set("Output Breaths Only", True)
    removed = np.asarray(module.process(x.copy()), dtype=np.float64)
    delay = int(module.latency_samples)
    total = kept[delay:] + removed[delay:]
    reference = x[: x.shape[0] - delay]
    error = float(np.max(np.abs(total - reference)))
    assert error < 1e-9, (
        f"the removed breath and what is left do not add up to the input "
        f"(the error is {error:.3e})"
    )


def test_the_output_is_finite_and_the_right_length():
    """The module returns a finite signal of the same length.

    Measurement: every sample of the output of the module on the breath
    case.
    """
    case = _cases()["voice_breath"]
    x = np.asarray(case.x, dtype=np.float64)
    module = _make()
    out = np.asarray(module.process(x), dtype=np.float64)
    assert out.shape == x.shape
    assert bool(np.all(np.isfinite(out))), "the module returned non finite samples"


def test_two_runs_are_the_same():
    """The module is deterministic."""
    case = _cases()["voice_breath"]
    x = np.asarray(case.x, dtype=np.float64)
    first = np.asarray(_make().process(x.copy()), dtype=np.float64)
    second = np.asarray(_make().process(x.copy()), dtype=np.float64)
    assert np.array_equal(first, second)


def test_the_cost_of_a_64_sample_block():
    """A 64 sample block has to fit into its realtime budget.

    The budget is 1333.3 us at 48 kHz.  The analysis is streaming, so the
    cost of a block is the cost of the frames it completes and not the cost
    of a whole analysis: the module gets cheaper the smaller the blocks
    are.
    """
    case = _cases()["voice_breath"]
    block = np.ascontiguousarray(np.asarray(case.x, dtype=np.float64)[:64])
    module = _make()
    module.process(block)
    times = []
    for _ in range(9):
        start = time.perf_counter_ns()
        module.process(block)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    cost = float(np.median(times))
    budget = (64.0 / HARNESS.SR) * 1.0e6
    assert cost < budget, (
        f"a 64 sample block costs {cost:.0f} us, the budget is {budget:.0f} us"
    )


def test_the_module_survives_the_edges():
    """The module has to survive silence, an impulse and a full scale sine.

    Measurement: every sample of the output has to be finite, and the
    output has to have the length of the input.
    """
    module = _make()
    sr = HARNESS.SR
    cases = {
        "silence": np.zeros((4096, 1), dtype=np.float64),
        "impulse": np.concatenate(
            [np.zeros((100, 1)), np.ones((1, 1)), np.zeros((4095, 1))], axis=0
        ).astype(np.float64),
        "full scale sine": np.sin(
            2.0 * math.pi * 1000.0 * np.arange(4096) / sr
        )[:, None].astype(np.float64),
        "a single sample": np.ones((1, 1), dtype=np.float64),
    }
    for name, signal in cases.items():
        out = np.asarray(module.process(signal.copy()), dtype=np.float64)
        assert out.shape == signal.shape, f"{name}: the length changed"
        assert bool(np.all(np.isfinite(out))), f"{name}: the output is not finite"
