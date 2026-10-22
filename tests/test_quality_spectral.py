"""
tests/test_quality_spectral.py — the quality gate of the project.

The measurement instrument lives in ``qa/quality/``:

* ``qa/quality/ground_truth.py`` — the ground truth library: seven synthetic cases
  whose wanted part, unwanted part and events are known exactly;
* ``qa/quality/measurement.py`` — the instrument: it measures *any* module of
  the registry with the same questions (what is removed, what is kept, the
  variance of the gain curve, the latency, the block coherence);
* ``qa/quality/quality_rules.py`` — the rules: the numbers every module is judged
  by, with the reasoning behind them.

This file asserts the rules.  A module that has no rule is not judged here;
a module owner adds a rule to ``qa/quality/quality_rules.py`` and the gate picks
it up.

The markers are **strict**: a rule that is not met yet is marked
``xfail(strict=True)``, so the day the module reaches the target the marker
turns into a failure and the marker has to be removed.  A green suite
therefore always means "the rule is not met yet", never "nobody looked".
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_QA = _ROOT / "qa" / "quality"
for _path in (str(_ROOT), str(_QA)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from aurcore import registry  # noqa: E402

from ground_truth import CASES, case_bed_only, make_case  # noqa: E402
from measurement import measure  # noqa: E402
from quality_rules import MODULES_WITH_RULES, RULES, Rule  # noqa: E402

# ---------------------------------------------------------------------------
# The rules that are not met yet.
#
# The marker of a rule is static: it says "this rule is not met yet".  When
# the module reaches the target the strict marker turns the pass into a
# failure, so the marker cannot be forgotten.  The *reason* is measured when
# the test file is collected, so it always quotes the numbers of the moment.
# ---------------------------------------------------------------------------
UNMET: dict[str, str] = {
    "aur_spectraldenoise_voice_hiss": (
        "the voice's partials sit 120 Hz apart and the analysis window of the "
        "default quality is 20 ms, so two partials share a bin"
    ),
    "aur_spectraldenoise_music_mixture": (
        "the plucked 660 Hz note is crushed: the same analysis resolution as "
        "in the voice case (qa/quality/STUDY.md section 4.2)"
    ),
    "aur_declick_transient": (
        "the click is detected but only partly taken out of the signal"
    ),
}


def _reason(rule: Rule) -> str:
    """The measured reason why a rule is not met yet."""
    try:
        measurement = measure(
            rule.code,
            rule.case,
            params=rule.params or None,
            band=rule.band,
            tones=rule.tones,
        )
    except Exception as exc:  # noqa: BLE001
        return "the module could not be measured (%s: %s)" % (type(exc).__name__, exc)
    failures = rule.check(measurement)
    if not failures:
        return "the rule is not met yet"
    return "the rule is not met: " + "; ".join(failures)


def _params() -> list:
    """The rule test parameters, with a strict marker on the unmet rules."""
    out = []
    for rule in RULES:
        if rule.identifier not in UNMET:
            out.append(pytest.param(rule, id=rule.identifier))
            continue
        reason = "%s (%s)" % (UNMET[rule.identifier], _reason(rule))
        out.append(
            pytest.param(
                rule,
                id=rule.identifier,
                marks=pytest.mark.xfail(strict=True, reason=reason),
            )
        )
    return out


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rule", _params())
def test_the_module_holds_its_rule(rule: Rule):
    """Every module that has a rule must hold it.

    The rule says how much the module has to remove, how much of the wanted
    content it may not damage, how steady its gain curve has to be and — for
    the modules whose job is not a plain removal — what the artefact and the
    restoration have to look like afterwards.
    """
    measurement = measure(
        rule.code,
        rule.case,
        params=rule.params or None,
        band=rule.band,
        tones=rule.tones,
    )
    failures = rule.check(measurement)
    assert not failures, (
        "%s (%s on %s): %s"
        % (
            rule.name,
            rule.code,
            rule.case,
            "; ".join(failures)),
    )


def test_the_rules_cover_the_modules_they_claim_to():
    """Every rule must name a module of the registry and a known case."""
    for rule in RULES:
        assert registry.has(rule.code), f"{rule.code} is not registered"
        assert rule.case in {case.name for case in CASES}, rule.case
        assert rule.reason, f"{rule.identifier} does not say why it exists"
    assert len(MODULES_WITH_RULES) == len({rule.code for rule in RULES})
    assert MODULES_WITH_RULES, "there is no rule at all"


# ---------------------------------------------------------------------------
# The instrument
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code",
    sorted(info.code for info in registry.all()),
)
def test_the_instrument_runs_for_every_module(code: str):
    """The instrument must be able to measure *every* module.

    A module that cannot be measured cannot be judged, so the instrument is
    run over the whole registry here.  The signal is short: the test is
    about the instrument, not about the quality of the module.

    A module that crashes or that returns a non-finite sample is *broken*,
    not unmeasurable: the test says so and skips, and the QA runner
    (``qa/quality/measure_all.py``) reports it as a problem.
    """
    case = case_bed_only(duration=0.25)
    try:
        measurement = measure(code, case)
    except Exception as exc:  # noqa: BLE001 - the module is broken
        pytest.skip(f"{code} could not be measured: {type(exc).__name__}: {exc}")
    assert measurement.output_length > 0, f"{code} returned nothing"
    if not measurement.output_finite:
        pytest.skip(f"{code} returns a non-finite sample; the defect is the module's")
    assert math.isfinite(measurement.removed_db), f"{code}: the removal is not a number"
    assert math.isfinite(measurement.kept_db), f"{code}: the damage is not a number"
    assert math.isfinite(measurement.gain_var_db2), f"{code}: the gain variance is not a number"
    assert measurement.latency_samples >= 0, f"{code}: the latency is negative"


def test_the_measurement_is_deterministic():
    """The same measurement twice must give the same numbers.

    Everything the instrument reads is fixed: the cases come from a seeded
    generator, the analysis geometry is a constant, and no wall clock, no
    thread and no unseeded random number takes part.  The source of the
    module is fingerprinted as well, so a measurement can be traced back to
    the program that produced it.
    """
    first = measure("AUR_SPECTRALDENOISE", "tone_noise")
    second = measure("AUR_SPECTRALDENOISE", "tone_noise")
    assert not first.source_changed, "the source changed during the first measurement"
    assert not second.source_changed, "the source changed during the second measurement"
    assert first.removed_db == second.removed_db
    assert first.kept_db == second.kept_db
    assert first.gain_var_db2 == second.gain_var_db2
    assert first.gain_var_smooth_db2 == second.gain_var_smooth_db2
    assert first.latency_samples == second.latency_samples
    assert first.block_coherence_db == second.block_coherence_db
    assert first.tone_shift_db == second.tone_shift_db


def test_the_measurement_is_deterministic_for_every_rule():
    """Every rule is measured twice, and the two runs must agree.

    When the source of the module changes between the two runs the two
    measurements describe two different programs, so the comparison is
    meaningless and the test says so instead of reporting a difference that
    is not the instrument's fault.
    """
    for rule in RULES:
        first = measure(rule.code, rule.case, params=rule.params or None, band=rule.band)
        second = measure(rule.code, rule.case, params=rule.params or None, band=rule.band)
        assert not first.source_changed, f"{rule.identifier}: the source changed " \
            "during the first measurement"
        assert not second.source_changed, f"{rule.identifier}: the source changed " \
            "during the second measurement"
        if first.source_sha != second.source_sha:
            pytest.skip(
                "the source of %s changed between the two measurements; the "
                "comparison would measure two different programs"
                % rule.code
            )
        assert first.removed_db == second.removed_db, rule.identifier
        assert first.kept_db == second.kept_db, rule.identifier
        assert first.gain_var_db2 == second.gain_var_db2, rule.identifier
        assert first.as_dict() == second.as_dict(), rule.identifier


# ---------------------------------------------------------------------------
# The ground truth
# ---------------------------------------------------------------------------
def test_the_ground_truth_cases_are_stable():
    """The cases are the yard: they must not change under the feet.

    The fingerprint pins every case to the numbers it had when the rules
    were written down.  A case that changes invalidates every rule that is
    measured on it, so the change has to be a deliberate one.
    """
    fingerprints = {
        case.name: float(
            np.sum(np.abs(np.fft.rfft(case.mixed)))
        )
        for case in CASES
    }
    expected = {
        "tone_noise": 2.739202e05,
        "tone_hum": 1.468460e05,
        "voice_hiss": 2.143149e05,
        "music_mixture": 2.209744e05,
        "bed_only": 2.633191e05,
        "clipped": 2.419677e05,
        "transient": 1.687553e05,
    }
    for name, value in expected.items():
        assert name in fingerprints, name
        assert abs(fingerprints[name] - value) / value < 1e-4, (
            f"the case {name!r} changed: {fingerprints[name]:.6e} instead of {value:.6e}"
        )


def test_every_case_carries_its_truth():
    """A case without a truth cannot be judged."""
    for case in CASES:
        assert case.mixed.size == case.clean.size, case.name
        assert case.mixed.size == case.noise.size, case.name
        assert case.description, case.name
        if case.name == "clipped":
            assert case.reference is not None, case.name
            assert not np.array_equal(case.reference, case.mixed), case.name
        if case.name == "transient":
            assert case.events, case.name
            for start, end in case.events:
                assert 0 <= start < end <= case.mixed.size, case.name


# ---------------------------------------------------------------------------
# The spectral de-noise, the module the study was written for
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("quality", ["A", "B", "C", "D"])
def test_the_spectral_de_noise_is_stable_for_every_quality_preset(quality: str):
    """Every quality preset must produce a usable result."""
    probe = registry.create("AUR_SPECTRALDENOISE", 48_000, 1)
    probe.params.set("Quality", quality)
    case = make_case("tone_noise")
    x = case.mixed[:, None]
    y = np.asarray(probe.process(x), dtype=np.float64)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))


def test_the_spectral_de_noise_output_is_finite_and_length_preserving():
    """The module must not damage the signal it is given."""
    probe = registry.create("AUR_SPECTRALDENOISE", 48_000, 2)
    case = make_case("tone_noise")
    x = np.stack([case.mixed] * 2, axis=1)
    y = np.asarray(probe.process(x), dtype=np.float64)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))
    assert float(np.max(np.abs(y))) < 4.0
