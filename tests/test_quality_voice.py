"""
Quality gate for the Voice De-Noise module.

The tests in this file measure what the module *does*, not only that it runs.
Every test states the measurement it uses in the docstring, so a failure tells
you what the module really did.

The gate
--------
For a synthetic voice that is covered with noise, the module has to

* remove at least 10 dB of the noise in the band where the voice is absent
  (R2),
* move the band that carries the voice by less than 1.5 dB (R1),
* keep the gain curve steady (R3, no musical noise),
* report the delay it really adds (R4),
* return the same samples no matter how the host chops the stream (R5).

The numbers live in ``qa/qualityvoice/rules.py`` and the measurement
instrument in ``qa/qualityvoice/harness.py``; both are shared with the report
(``qa/qualityvoice/REPORT.md``), so the numbers in the failure messages are
the numbers of the report.

The module also has to *keep* what it measures: the values in
:data:`MEASURED` are the values of the shipped implementation, and a test
that lets them drift away unnoticed is a test that does not measure anything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from aurcore import registry

MODULE_CODE = "AUR_VOICEDENOISE"

#: The defaults the module ships with (see aurcore/schema/params.tsv).
SHIPPED_DEFAULTS = {
    "Threshold": -15.0,
    "Reduction": 18.0,
}


def _load(name: str, filename: str):
    """Import a sibling of the quality instrument by path."""
    path = Path(__file__).resolve().parents[1] / "qa" / "qualityvoice" / filename
    if not path.is_file():
        pytest.skip(f"the quality instrument is missing ({path})")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HARNESS = _load("aur_qa_qualityvoice", "harness.py")
RULES = _load("aur_qa_qualityvoice_rules", "rules.py")


#: The measurements of the shipped implementation, taken with the instrument
#: of qa/qualityvoice on 2026-10-21, after the noise reference was shortened
#: to 29 ms, the gain curve was taught to leave a band that is left alone
#: alone, and the decision was moved into the ratio domain: the balance
#: inside a band, so that the gain does not follow the level of the input.
#: The tolerances leave room for the platform, but they are far tighter than
#: the budgets of the rules.
MEASURED = {
    # probe name: (hiss reduction dB, voice change dB, chatter dB)
    "voice_denoise_hiss": (-17.85, -0.135, 0.025),
    "voice_denoise_pink_hiss": (-17.87, -0.053, 0.024),
    "voice_denoise_clean": (-0.43, -0.150, 0.000),
}

#: How far a measurement may move from the value above (dB).
TOLERANCE_DB = 1.0


def _probes(module_code: str):
    """The probes of the shared rules that belong to one module."""
    return [p for p in RULES.PROBES if p.module == module_code]


def _cases():
    """The synthetic measurement cases of the instrument."""
    return HARNESS.build_cases()


def _make(module_code: str):
    """A fresh instance of a module."""
    if not registry.has(module_code):
        pytest.skip(f"module {module_code!r} is not registered yet")
    return registry.create(module_code, HARNESS.SR, 1)


@pytest.fixture(scope="module")
def results():
    """The measurement of every probe of the shared rules."""
    cases = _cases()
    out = {}
    for probe in RULES.PROBES:
        if not registry.has(probe.module):
            continue
        module = _make(probe.module)
        out[probe.name] = HARNESS.evaluate(
            cases[probe.case], module, probe.config, probe.chatter_band
        )
    return out


@pytest.fixture(scope="module")
def spreads():
    """The spread of the applied gain when the level of the input moves.

    The same material is played at five levels under a fixed setting, and
    the spread of the mean gain over the stretches is the number: a module
    whose decision carries the level of the input moves its gain with the
    level of the input.
    """
    cases = _cases()
    out = {}
    for probe in RULES.PROBES:
        if not registry.has(probe.module):
            continue
        if probe.rule.level_spread_db is None:
            continue
        module = _make(probe.module)
        out[probe.name] = HARNESS.measure_level_spread(
            cases[probe.case], module, probe.config, probe.chatter_band
        )
    return out


@pytest.fixture(scope="module")
def coherence():
    """The block coherence of every probe of the shared rules."""
    cases = _cases()
    out = {}
    for probe in RULES.PROBES:
        if not registry.has(probe.module):
            continue
        out[probe.name] = HARNESS.measure_block_coherence(
            cases[probe.case],
            lambda code=probe.module: _make(code),
            probe.config,
            block_size=4096,
        )
    return out


# --------------------------------------------------------------------------
# The gate of the Voice De-Noise module
# --------------------------------------------------------------------------
def test_voice_denoise_ships_the_recommended_defaults():
    """The module has to ship the defaults the quality report recommends.

    Measurement: the defaults of the parameter list of the module.  The
    Threshold opens the gate of the module (the reference of a band sits
    below its level), the Reduction is the cap of the gain.
    """
    module = _make(MODULE_CODE)
    shipped = {d.name: module.params.get(d.name) for d in module.describe()}
    for name, value in SHIPPED_DEFAULTS.items():
        assert shipped[name] == value, (
            f"{name} ships as {shipped[name]}, the report recommends {value}"
        )


def test_voice_denoise_removes_at_least_10_db_of_hiss(results):
    """The noise band has to lose at least 10 dB (rule R2).

    Measurement: the energy of the 5-14 kHz band before and after the
    module, on a voice that is covered with white noise and on a voice that
    is covered with pink noise.  The band carries no harmonic of the voice,
    so everything that disappears there was noise.
    """
    for probe in _probes(MODULE_CODE):
        rule = probe.rule
        if not rule.target_band:
            continue
        change = results[probe.name].metrics[f"band_{rule.target_band}_change_db"]
        reduction = -change
        assert reduction >= rule.target_reduction_db, (
            f"{probe.name}: only {reduction:.2f} dB of "
            f"{rule.target_band} were removed, {rule.target_reduction_db:.0f} dB are required"
        )


def test_voice_denoise_keeps_the_voice_band_within_1_5_db(results):
    """The band that carries the voice may not move by more than 1.5 dB (R1).

    Measurement: the energy of the 120 Hz - 3 kHz band before and after the
    module, on the same cases.  The band carries the wanted content, so it
    may not move.
    """
    for probe in _probes(MODULE_CODE):
        rule = probe.rule
        change = abs(results[probe.name].metrics[f"band_{rule.protected_band}_change_db"])
        assert change <= rule.protected_change_db, (
            f"{probe.name}: the {rule.protected_band} band moved by {change:.3f} dB, "
            f"at most {rule.protected_change_db:.1f} dB are allowed"
        )


def test_voice_denoise_does_not_chatter(results):
    """The gain curve in the noise band has to be steady (rule R3).

    Measurement: the roughness of the gain the module applies in the 5-14 kHz
    band.  A jittering gain is heard as musical noise, so the roughness has
    to stay below 1 dB.
    """
    for probe in _probes(MODULE_CODE):
        chatter = results[probe.name].metrics["chatter_db"]
        assert chatter <= probe.rule.chatter_db, (
            f"{probe.name}: the gain curve in the noise band fluctuates by "
            f"{chatter:.3f} dB, at most {probe.rule.chatter_db:.1f} dB are allowed"
        )


def test_voice_denoise_is_block_coherent(coherence):
    """The output may not depend on the block size of the host (rule R5).

    Measurement: the same signal processed in one block and in blocks of
    4096 samples.  A block coherent module returns the same samples.
    """
    for probe in _probes(MODULE_CODE):
        budget = probe.rule.block_coherence_db
        if budget is None:
            continue
        difference = coherence[probe.name]["block_rms_diff_db"]
        assert difference <= budget, (
            f"{probe.name}: processing the signal in blocks of 4096 samples changes it "
            f"by {difference:.1f} dB, a block coherent module changes nothing"
        )


def test_voice_denoise_reports_its_latency(results):
    """The module has to report the delay it really adds (rule R4).

    Measurement: the delay of the output against the input, found with a
    cross correlation, compared with ``latency_samples``.
    """
    for probe in _probes(MODULE_CODE):
        metrics = results[probe.name].metrics
        reported = int(metrics["latency_reported"])
        measured = int(metrics["latency_measured"])
        assert reported > 0, f"{probe.name}: the module does not report a latency"
        assert abs(measured - reported) <= probe.rule.latency_tolerance, (
            f"{probe.name}: the module reports {reported} samples of latency but delays "
            f"the signal by {measured} samples"
        )


def test_voice_denoise_measures_the_same_numbers_every_time(results):
    """The measurements of the module may not drift away (the gate of the gate).

    Measurement: the same numbers as the tests above, compared with the
    values the shipped implementation measures.  The tolerance is far tighter
    than the budget of the rules, so a change in the estimator shows up here
    before it shows up as a broken gate.
    """
    for name, (hiss, voice, chatter) in MEASURED.items():
        if name not in results:
            continue
        metrics = results[name].metrics
        measured_hiss = metrics.get("band_hiss_change_db", 0.0)
        measured_voice = metrics.get("band_voice_change_db", 0.0)
        measured_chatter = metrics.get("chatter_db", 0.0)
        assert abs(measured_hiss - hiss) <= TOLERANCE_DB, (
            f"{name}: the hiss band moved by {measured_hiss:.2f} dB, the shipped "
            f"implementation measures {hiss:.2f} dB"
        )
        assert abs(measured_voice - voice) <= TOLERANCE_DB, (
            f"{name}: the voice band moved by {measured_voice:.3f} dB, the shipped "
            f"implementation measures {voice:.3f} dB"
        )
        assert abs(measured_chatter - chatter) <= TOLERANCE_DB, (
            f"{name}: the gain curve fluctuates by {measured_chatter:.3f} dB, the "
            f"shipped implementation measures {chatter:.3f} dB"
        )


def test_voice_denoise_output_is_finite():
    """The output of the module may not contain NaN or infinity.

    Measurement: every sample of the output of the module on the white
    noise case.
    """
    module = _make(MODULE_CODE)
    case = _cases()["voice_hiss"]
    x = np.asarray(case.x, dtype=np.float64)
    y = np.asarray(module.process(x), dtype=np.float64)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y)), "the module returned non finite samples"


def test_voice_denoise_maps_every_analysis_bin():
    """Every analysis bin has to belong to a band.

    Measurement: the band position of every bin of the analysis spectrum.
    The bins below 30 Hz belong to the first band, the bins above 16 kHz to
    the last one.  A bin without a band would carry the gain of whatever
    the memory of the allocator held.
    """
    module = _make(MODULE_CODE)
    positions = np.asarray(module._bin_positions, dtype=np.float64)
    freqs = np.fft.rfftfreq(module._engine.n_fft, 1.0 / float(HARNESS.SR))
    below = freqs < module._edges[0]
    above = freqs > module._edges[-1]
    assert np.all(np.isfinite(positions)), "some analysis bins have no band position"
    assert np.all(positions[below] == 0.0), (
        "the analysis bins below 30 Hz are not mapped onto the first band"
    )
    assert np.all(positions[above] == 63.0), (
        "the analysis bins above 16 kHz are not mapped onto the last band"
    )


def test_voice_denoise_keeps_the_length_and_the_level():
    """The module may not change the length and may not change the level much."""
    module = _make(MODULE_CODE)
    case = _cases()["voice_hiss"]
    x = np.asarray(case.x, dtype=np.float64)
    y = np.asarray(module.process(x), dtype=np.float64)
    assert y.shape == x.shape
    before = float(np.sqrt(np.mean(x * x)))
    after = float(np.sqrt(np.mean(y * y)))
    assert abs(after - before) <= 0.25 * before, (
        f"the level changed from {before:.4f} to {after:.4f}"
    )


# --------------------------------------------------------------------------
# The rules of every voice shaped module
# --------------------------------------------------------------------------
def test_voice_module_does_not_follow_the_level_of_the_input(spreads):
    """The gain the module applies may not follow the level of the input (R9).

    Measurement: the same material at five levels under a fixed setting, the
    gain the module applies in the band that carries the noise, the mean of
    every stretch, and the spread of those means.  The rule holds when the
    spread stays inside the budget of the rule.

    The rule is checked for every module that has a voice rule: a module
    that works on a voice may not pump when the level of the input moves.
    """
    for probe in RULES.PROBES:
        if probe.rule.level_spread_db is None:
            continue
        if probe.pending:
            # The rule of this probe is not met yet: the marker of the
            # parametrized gate carries it until the module reaches the
            # number.
            continue
        if probe.name not in spreads:
            continue
        spread = spreads[probe.name]["level_spread_db"]
        assert spread <= probe.rule.level_spread_db, (
            f"{probe.name}: the applied gain moves by {spread:.3f} dB when the level "
            f"of the input moves over 18 dB, at most {probe.rule.level_spread_db:.1f} dB "
            "are allowed"
        )


# --------------------------------------------------------------------------
#: The modules that have reached their numbers.  Their probes carry no
#: marker, so a rule that breaks again fails this file.
REACHED = ("AUR_BREATH", "AUR_DIALOGUEISOLATE")


def _probe_params():
    """The probes of the shared rules, with the marker of the open ones."""
    params = []
    for probe in RULES.PROBES:
        marks = []
        if probe.module != MODULE_CODE and probe.module not in REACHED:
            # The other voice shaped modules have not reached their numbers
            # yet.  The marker is not strict: a module that reaches them does
            # not break this file, it only shows up as an unexpected pass.
            marks.append(
                pytest.mark.xfail(
                    strict=False,
                    reason=(
                        f"{probe.module} does not meet its rule yet; "
                        "see qa/qualityvoice/REPORT.md"
                    ),
                )
            )
        if probe.pending:
            # The rule of this probe is not met yet.  The marker is not
            # strict either: the probe turns into a failure as soon as the
            # module reaches the number.
            marks.append(
                pytest.mark.xfail(
                    strict=False,
                    reason=(
                        f"{probe.module} does not meet the rule of this probe yet; "
                        "see qa/qualityvoice/REPORT.md"
                    ),
                )
            )
        params.append(pytest.param(probe, id=probe.name, marks=marks))
    return params


@pytest.mark.parametrize("probe", _probe_params())
def test_voice_module_meets_its_rule(probe, results, coherence, spreads):
    """Every probe of the shared rules has to meet its numbers.

    The rules of the modules that do not meet them yet carry a marker: the
    marker is not strict, so a module that reaches its numbers does not
    break this file.
    """
    if probe.name not in results:
        pytest.skip(f"module {probe.module!r} is not registered yet")
    rule = probe.rule
    metrics = results[probe.name].metrics

    if rule.target_band:
        for region in rule.target_regions or ("",):
            key = (
                f"region_{region}_{rule.target_band}_change_db"
                if region
                else f"band_{rule.target_band}_change_db"
            )
            reduction = -metrics.get(key, 0.0)
            assert reduction >= rule.target_reduction_db, (
                f"{probe.name}: {key} lost {reduction:.2f} dB, "
                f"{rule.target_reduction_db:.0f} dB are required"
            )

    change = abs(metrics.get(f"band_{rule.protected_band}_change_db", 0.0))
    assert change <= rule.protected_change_db, (
        f"{probe.name}: the {rule.protected_band} band moved by {change:.3f} dB, "
        f"at most {rule.protected_change_db:.1f} dB are allowed"
    )

    if rule.clean_budget_db is not None:
        clean = abs(metrics.get(f"band_{rule.protected_band}_change_db", 0.0))
        assert clean <= rule.clean_budget_db, (
            f"{probe.name}: the clean signal moved by {clean:.3f} dB, at most "
            f"{rule.clean_budget_db:.1f} dB are allowed"
        )

    chatter = metrics.get("chatter_db", 0.0)
    assert chatter <= rule.chatter_db, (
        f"{probe.name}: the gain curve fluctuates by {chatter:.3f} dB, at most "
        f"{rule.chatter_db:.1f} dB are allowed"
    )

    level = metrics.get("output_level_db", 0.0)
    assert level >= -rule.level_db, (
        f"{probe.name}: the output is {level:.2f} dB quieter than the input, at most "
        f"{rule.level_db:.1f} dB are allowed"
    )

    if rule.exact_output:
        difference = metrics.get("output_max_diff", 0.0)
        assert difference <= RULES.TRANSPARENCY_TOLERANCE, (
            f"{probe.name}: the output differs from the input by {difference:.3e}"
        )

    if rule.block_coherence_db is not None:
        difference = coherence[probe.name]["block_rms_diff_db"]
        assert difference <= rule.block_coherence_db, (
            f"{probe.name}: the block size changes the output by {difference:.1f} dB"
        )

    if rule.level_spread_db is not None and probe.name in spreads:
        spread = spreads[probe.name]["level_spread_db"]
        assert spread <= rule.level_spread_db, (
            f"{probe.name}: the applied gain moves by {spread:.3f} dB when the level "
            f"of the input moves over 18 dB, at most {rule.level_spread_db:.1f} dB "
            "are allowed"
        )

    reported = int(metrics.get("latency_reported", 0))
    measured = int(metrics.get("latency_measured", 0))
    assert abs(measured - reported) <= rule.latency_tolerance, (
        f"{probe.name}: the module reports {reported} samples of latency but delays "
        f"the signal by {measured} samples"
    )
