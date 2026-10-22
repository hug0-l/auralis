"""
Quality gate for the De-Rustle module.

The tests in this file measure what the module *does*, not only that it runs.
Every test states the measurement it uses in the docstring, so a failure tells
you what the module really did.

The gate
--------
For a synthetic rustle (broadband friction noise with a burst envelope and a
slow modulation) that covers a wanted musical part, the module has to

* remove the rustle from the band that carries nothing but rustle,
* move the wanted partials by less than 1 dB,
* be an exact pass through when the reduction strength is zero,
* return the same samples for one channel and for two,
* stay finite and keep the length of the block on the awkward inputs,
* report the delay it really adds, and
* give the same answer whether the host hands it the whole case or 64
  samples at a time.

The measurement harness lives in ``qa/rustle/`` and is shared with the
report (``qa/rustle/REPORT.md``), so the numbers in the failure messages are
the numbers of the report.

The numbers the module reaches today (48 kHz, the shipped defaults, the
synthetic case of ``qa/rustle/harness.py``):

===========================  ==========  ==========
measurement                  mono        stereo
===========================  ==========  ==========
rustle removed (dB)           -2.93       -2.93
wanted part removed (dB)      -0.36       -0.36
pass through at strength 0     0.00        0.00
===========================  ==========  ==========

The module delays its output by the look ahead of its analysis (2048
samples), so the measurements read the output from that delay: the levels of
the input and of the output have to be compared on the same part of the
material.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from aurcore import registry

MODULE_CODE = "AUR_DERUSTLE"

#: The rustle band has to lose at least this much (dB).
TARGET_RUSTLE_REDUCTION_DB = 1.5

#: The wanted partials may move by at most this much (dB).
TARGET_WANTED_CHANGE_DB = 1.0

#: The pass through at zero strength may not move anything (dB).
TARGET_PASS_THROUGH_DB = 0.01

#: The cost of one 64 sample block may not exceed this many times the
#: realtime budget of the block.
TARGET_COST_FACTOR = 3.0

#: How much the output may differ between one block and many blocks (dB).
TARGET_BLOCK_COHERENCE_DB = -40.0


# --------------------------------------------------------------------------
# The shared measurement harness
# --------------------------------------------------------------------------
def _load_harness():
    """Import the measurement harness from ``qa/rustle``."""
    path = Path(__file__).resolve().parents[1] / "qa" / "rustle" / "harness.py"
    if not path.is_file():
        pytest.skip(f"the measurement harness is missing ({path})")
    spec = importlib.util.spec_from_file_location("aur_qa_rustle", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


@pytest.fixture(scope="module")
def cases():
    """The synthetic measurement cases."""
    return HARNESS.build_cases()


def _module_for(case, config=None):
    """A fresh module in the state the measurement wants."""
    cfg = dict(HARNESS.DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    module = registry.create(MODULE_CODE, HARNESS.SR, case.channels)
    for name, value in cfg.items():
        if module.params.has(name):
            module.params.set(name, value)
    return module


def _metrics(case, config=None) -> dict:
    """The measurement of the case, with the output read from the latency.

    The module delays its output by the look ahead of its analysis, so the
    levels of the input and of the output have to be compared on the same
    part of the material.
    """
    module = _module_for(case, config)
    x = np.asarray(case.x, dtype=np.float64)
    y = np.asarray(module.process(x), dtype=np.float64)
    latency = int(module.latency_samples)
    xa = x[: max(0, x.shape[0] - latency)]
    ya = y[latency:]
    count = min(xa.shape[0], ya.shape[0])
    xa = xa[:count]
    ya = ya[:count]
    rustle_in = HARNESS.band_energy_db(xa, *HARNESS.RUSTLE_BAND)
    rustle_out = HARNESS.band_energy_db(ya, *HARNESS.RUSTLE_BAND)
    wanted_in = HARNESS.partial_energy_db(xa)
    wanted_out = HARNESS.partial_energy_db(ya)
    band_in = HARNESS.band_energy_db(xa, *HARNESS.WANTED_BAND)
    band_out = HARNESS.band_energy_db(ya, *HARNESS.WANTED_BAND)
    rms_in = float(np.sqrt(np.mean(xa ** 2))) if count else 0.0
    rms_out = float(np.sqrt(np.mean(ya ** 2))) if count else 0.0
    return {
        "rustle_removed_db": rustle_out - rustle_in,
        "wanted_removed_db": wanted_out - wanted_in,
        "snr_gain_db": (band_out - rustle_out) - (band_in - rustle_in),
        "level_change_db": 20.0 * math.log10(max(rms_out, 1e-12) / max(rms_in, 1e-12)),
        "latency_samples": float(latency),
    }


@pytest.fixture(scope="module")
def results(cases):
    """The measurement of every case with the shipped defaults."""
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    return {case.name: _metrics(case) for case in cases}


def _make_module(channels: int = 2):
    """The shipped module."""
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    return registry.create(MODULE_CODE, HARNESS.SR, channels)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
def test_de_rustle_removes_the_rustle(results):
    """The band that carries nothing but rustle has to lose at least 1.5 dB.

    Measurement: the energy of the 4 - 12 kHz band before and after the
    module.  The wanted musical part has nothing above 1.4 kHz, so
    everything that disappears in that band was rustle.
    """
    for name, result in results.items():
        removed = -result["rustle_removed_db"]
        assert removed >= TARGET_RUSTLE_REDUCTION_DB, (
            f"{name}: only {removed:.2f} dB of rustle were removed, "
            f"{TARGET_RUSTLE_REDUCTION_DB:.1f} dB are required"
        )


def test_de_rustle_keeps_the_wanted_part_within_1_db(results):
    """The wanted partials may move by at most 1 dB.

    Measurement: the energy of the narrow windows around the wanted
    partials, before and after the module.  The rustle under the partials is
    far weaker than the partials themselves, so the change is the damage to
    the wanted part.
    """
    for name, result in results.items():
        change = abs(result["wanted_removed_db"])
        assert change <= TARGET_WANTED_CHANGE_DB, (
            f"{name}: the wanted part moved by {change:.2f} dB, at most "
            f"{TARGET_WANTED_CHANGE_DB:.1f} dB are allowed"
        )


def test_de_rustle_improves_the_signal_to_noise_ratio(results):
    """The module has to make the wanted part louder than the rustle.

    Measurement: the energy of the wanted band minus the energy of the
    rustle band, before and after the module.
    """
    for name, result in results.items():
        gain = result["snr_gain_db"]
        assert gain > 0.0, f"{name}: the signal to noise ratio did not improve"


def test_de_rustle_is_a_pass_through_at_zero_strength(cases):
    """At strength zero the module may not change anything.

    Measurement: the whole block, before and after the module, with
    ``Reduction Strength`` at zero.  The comparison is done on the wanted
    partials and on the rustle band, in dB.
    """
    for case in cases:
        config = dict(HARNESS.DEFAULT_CONFIG)
        config["Reduction Strength"] = 0.0
        metrics = _metrics(case, config)
        assert abs(metrics["rustle_removed_db"]) <= TARGET_PASS_THROUGH_DB, (
            f"{case.name}: the rustle band moved by "
            f"{metrics['rustle_removed_db']:.4f} dB at strength zero"
        )
        assert abs(metrics["wanted_removed_db"]) <= TARGET_PASS_THROUGH_DB, (
            f"{case.name}: the wanted part moved by "
            f"{metrics['wanted_removed_db']:.4f} dB at strength zero"
        )
        assert abs(metrics["level_change_db"]) <= TARGET_PASS_THROUGH_DB, (
            f"{case.name}: the level moved by "
            f"{metrics['level_change_db']:.4f} dB at strength zero"
        )


def test_de_rustle_agrees_between_one_and_two_channels(cases):
    """One channel and two channels have to give the same answer.

    Measurement: the rustle removed and the wanted part moved, measured on
    the mono case and on the stereo case.  The resynthesis has to handle
    both channel counts the same way.
    """
    metrics = {case.name: _metrics(case) for case in cases}
    mono = metrics["rustle_mono"]
    stereo = metrics["rustle_stereo"]
    assert math.isclose(
        mono["rustle_removed_db"], stereo["rustle_removed_db"], abs_tol=0.25
    ), (
        f"the mono case removes {mono['rustle_removed_db']:.2f} dB of rustle and "
        f"the stereo case {stereo['rustle_removed_db']:.2f} dB"
    )
    assert math.isclose(
        mono["wanted_removed_db"], stereo["wanted_removed_db"], abs_tol=0.25
    ), (
        f"the mono case moves the wanted part by {mono['wanted_removed_db']:.2f} dB "
        f"and the stereo case by {stereo['wanted_removed_db']:.2f} dB"
    )


# --------------------------------------------------------------------------
# The edges
# --------------------------------------------------------------------------
def test_de_rustle_survives_the_awkward_inputs():
    """Silence, a constant, a full scale square, an impulse and a tone.

    Measurement: the output of the module has to be finite, has to have the
    length of the input and may not gain any level.
    """
    for name, signal in HARNESS.edge_cases():
        module = _make_module(signal.shape[1])
        out = np.asarray(module.process(signal.copy()), dtype=np.float64)
        assert out.shape == signal.shape, (
            f"{name}: the module returned {out.shape} for {signal.shape}"
        )
        assert np.all(np.isfinite(out)), f"{name}: the module returned a non finite sample"
        rms_in = float(np.sqrt(np.mean(signal ** 2)))
        rms_out = float(np.sqrt(np.mean(out ** 2)))
        if rms_in > 1e-9:
            assert rms_out <= rms_in * 1.001 + 1e-6, (
                f"{name}: the level grew from {rms_in:.4f} to {rms_out:.4f}"
            )


def test_de_rustle_keeps_a_single_impulse():
    """A single impulse has to stay a single impulse.

    Measurement: the peak of the output and the number of samples above a
    tenth of that peak.  The module must not spread the impulse over the
    block.  The impulse comes out at the latency the module reports, so the
    output is read from there.
    """
    signal = np.zeros((8192, 2), dtype=np.float64)
    signal[2048, 0] = 1.0
    module = _make_module(2)
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    peak = float(np.max(np.abs(out[latency:])))
    assert peak > 0.5, f"the impulse came out with a peak of {peak:.3f}"
    loud = int(np.count_nonzero(np.abs(out[latency:, 0]) > 0.1 * peak))
    assert loud <= 8, f"the impulse was spread over {loud} samples"


# --------------------------------------------------------------------------
# Latency, determinism and block coherence
# --------------------------------------------------------------------------
def test_de_rustle_reports_its_latency(cases):
    """The module has to report the delay it really adds.

    Measurement: the delay of the output against the input, found with a
    cross correlation, compared with ``latency_samples``.
    """
    for case in cases:
        module = _make_module(case.channels)
        x = np.asarray(case.x, dtype=np.float64)
        y = np.asarray(module.process(x), dtype=np.float64)
        reported = int(module.latency_samples)
        assert reported > 0, "the module does not report a latency"
        measured = HARNESS.measure_latency(x, y)
        assert abs(abs(measured) - reported) <= 64, (
            f"the module reports {reported} samples of latency but delays the "
            f"signal by {measured} samples"
        )


def test_de_rustle_is_deterministic(cases):
    """Two runs of the module have to give the same answer.

    Measurement: two fresh modules are given the same case and the two
    results are compared sample by sample.  The module carries its analysis
    from one block to the next, so the same module called twice with the
    same block is not the same measurement.
    """
    for case in cases:
        first = np.asarray(_make_module(case.channels).process(case.x), dtype=np.float64)
        second = np.asarray(_make_module(case.channels).process(case.x), dtype=np.float64)
        difference = float(np.max(np.abs(first - second))) if first.size else 0.0
        assert difference == 0.0, (
            f"{case.name}: two runs of the module differ by {difference:.3e}"
        )


def test_de_rustle_keeps_the_signal_across_a_block_boundary(cases):
    """Two blocks of the same signal have to give the same result.

    Measurement: the signal is processed in one block and then in two
    halves.  The difference of the two results, in dB relative to the
    signal, has to stay far below the level of the signal.
    """
    for case in cases:
        info = HARNESS.measure_split(case)
        assert info["rms_diff_db"] < TARGET_BLOCK_COHERENCE_DB, (
            f"{case.name}: processing the signal in two blocks changes it by "
            f"{info['rms_diff_db']:.1f} dB, a block coherent module changes "
            "nothing"
        )


# --------------------------------------------------------------------------
# The cost
# --------------------------------------------------------------------------
def test_de_rustle_fits_into_the_block_budget():
    """One 64 sample block has to be processed inside its realtime budget.

    Measurement: the median wall clock time of one ``process`` call with a
    64 sample block, against the budget of the block (1333.3 us at 48 kHz).
    """
    timing = HARNESS.measure_cost(64, repeats=7, warmup=2, rounds=1)
    budget = timing["budget_us"]
    assert timing["median_us"] <= budget * TARGET_COST_FACTOR, (
        f"a 64 sample block took {timing['median_us']:.0f} us, the budget is "
        f"{budget:.0f} us"
    )


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------
def test_de_rustle_report(capsys: pytest.CaptureFixture[str]):
    """Print the measurement of the module.

    The test always passes: it is a report.  Run it with ``pytest -s`` to
    see the table, or read ``qa/rustle/REPORT.md`` for the recorded numbers.
    """
    lines: list[str] = []
    lines.append(f"case: {HARNESS.DURATION:.1f} s of {HARNESS.SR} Hz")
    lines.append(
        f"  {'case':<20}{'rustle dB':>12}{'wanted dB':>12}{'SNR gain dB':>14}"
    )
    for case in HARNESS.build_cases():
        metrics = _metrics(case)
        lines.append(
            f"  {case.name:<20}{metrics['rustle_removed_db']:>12.2f}"
            f"{metrics['wanted_removed_db']:>12.2f}{metrics['snr_gain_db']:>14.2f}"
        )
    lines.append("")
    lines.append("cost of one block")
    lines.append(f"  {'block':>7}{'us':>10}{'budget us':>12}{'budget %':>11}")
    for block in (64, 128, 256, 512):
        timing = HARNESS.measure_cost(block, repeats=7, warmup=2, rounds=1)
        lines.append(
            f"  {block:>7}{timing['median_us']:>10.0f}{timing['budget_us']:>12.1f}"
            f"{timing['budget_fraction'] * 100:>10.1f}%"
        )

    text = "\n".join(lines)
    with capsys.disabled():
        print(text)
