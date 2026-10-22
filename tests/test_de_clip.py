"""
tests/test_de_clip.py — the de-clipper.

The module rebuilds the peaks that a hard clipper flattened and gives the
programme back the level the clipping chain took away.  The tests below pin
down the three things that matter for that claim:

* the detection must not fire on material that is merely loud,
* the repair must be in proportion to how hard the material was clipped,
* the result must move towards the truth of the quality instrument's ``clipped``
  case and must not damage the wanted partial of that case.

The numbers come from the quality instrument (``qa/quality/``), which is the
yard of the whole project.
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

from ground_truth import make_case  # noqa: E402
from measurement import measure, tone_level  # noqa: E402
from quality_rules import rule_for  # noqa: E402

SR = 48_000


def _sine(freq: float, seconds: float, amp: float, sr: int = SR) -> np.ndarray:
    """A mono sine."""
    n = int(seconds * sr)
    t = np.arange(n, dtype=np.float64) / float(sr)
    return amp * np.sin(2.0 * math.pi * freq * t)


def _run(signal: np.ndarray, **params) -> np.ndarray:
    """Run the module over a mono signal."""
    module = registry.create("AUR_DECLIP", SR, 1)
    for key, value in params.items():
        module.params.set(key, value)
    out = np.asarray(module.process(signal[:, None]), dtype=np.float64)
    return out[:, 0]


def _clipped(amp: float, ceiling: float) -> np.ndarray:
    """A sine driven into a hard clipper at ``ceiling``."""
    return np.clip(_sine(1000.0, 0.5, amp), -ceiling, ceiling)


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------
def test_the_module_describes_its_parameters():
    """The module publishes the parameters of the table."""
    module = registry.create("AUR_DECLIP", SR, 2)
    names = [p.name for p in module.describe()]
    assert names == ["Threshold", "Link Threshold", "Quality", "Makeup Gain",
                     "Post Limiter"]


def test_the_output_is_finite_and_as_long_as_the_input():
    """The module never damages the signal it is given."""
    module = registry.create("AUR_DECLIP", SR, 2)
    rng = np.random.default_rng(7)
    signal = rng.standard_normal((4096, 2)) * 0.3
    out = np.asarray(module.process(signal), dtype=np.float64)
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))


def test_two_runs_are_the_same():
    """The module is deterministic."""
    signal = _clipped(2.0, 1.0)
    first = _run(signal)
    second = _run(signal)
    assert np.array_equal(first, second)


def test_silence_and_a_constant_come_back_unchanged():
    """Nothing to repair, nothing changed."""
    silence = np.zeros(1024)
    constant = np.full(1024, 0.5)
    assert np.array_equal(_run(silence), silence)
    assert np.array_equal(_run(constant), constant)


# ---------------------------------------------------------------------------
# the detection must not fire on material that is merely loud
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq", [100.0, 1000.0, 8000.0])
def test_a_full_scale_sine_comes_out_unchanged(freq: float):
    """A sine that reaches the rail is loud, not clipped.

    A sine touches the rail at the top of its cycle and is not flat there, so
    the detector must not call that a clipped peak.
    """
    signal = _sine(freq, 0.5, 1.0)
    out = _run(signal)
    assert np.max(np.abs(out - signal)) < 1.0e-9


def test_a_sine_at_the_threshold_comes_out_unchanged():
    """Material that stops just below the threshold is not clipped either."""
    signal = _sine(1000.0, 0.5, 10.0 ** (-0.1 / 20.0))
    out = _run(signal)
    assert np.max(np.abs(out - signal)) < 1.0e-9


def test_a_quiet_signal_comes_out_unchanged():
    """Material that never reaches the rail is not touched."""
    signal = _sine(1000.0, 0.5, 0.2)
    out = _run(signal)
    assert np.max(np.abs(out - signal)) < 1.0e-9


# ---------------------------------------------------------------------------
# the repair is in proportion to the clipping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("headroom_db", [-1.0, -6.0, -20.0])
def test_a_signal_clipped_at_three_levels_is_repaired(headroom_db: float):
    """The three clipping levels are handled, and each one differently."""
    ceiling = 10.0 ** (headroom_db / 20.0)
    signal = _clipped(2.0, ceiling)
    out = _run(signal)
    assert np.all(np.isfinite(out))
    assert out.shape == signal.shape
    # the flat top is gone: the output does not sit still at the ceiling any
    # longer for long
    flat = np.abs(np.diff(out)) <= 1.0e-9
    assert int(np.count_nonzero(flat)) < int(np.count_nonzero(
        np.abs(np.diff(signal)) <= 1.0e-9))


def test_the_repair_grows_with_the_severity_of_the_clipping():
    """The harder the material was clipped, the more the module changes.

    The change is measured against the material's own level, so that the
    three cases can be compared with one another.
    """
    changes = []
    for headroom_db in (-1.0, -6.0, -20.0):
        ceiling = 10.0 ** (headroom_db / 20.0)
        signal = _clipped(2.0, ceiling)
        out = _run(signal)
        level = float(np.sqrt(np.mean(signal ** 2)))
        changes.append(float(np.sqrt(np.mean((out - signal) ** 2))) / level)
    assert changes[0] < changes[1] < changes[2], changes


def test_the_module_never_claims_a_peak_above_the_rail():
    """The height of a destroyed peak is an estimate, not a reading.

    The module rebuilds the waveform under the flat top and scales the
    programme so that the rebuilt peak reaches the clip level; it does not
    claim that the original peak was higher.
    """
    for headroom_db in (-1.0, -6.0, -20.0):
        ceiling = 10.0 ** (headroom_db / 20.0)
        signal = _clipped(2.0, ceiling)
        out = _run(signal)
        assert float(np.max(np.abs(out))) <= ceiling + 1.0e-6


# ---------------------------------------------------------------------------
# the quality instrument's case
# ---------------------------------------------------------------------------
def test_the_module_moves_the_clipped_case_towards_the_truth():
    """The measurement of the project's own yard: the de-clip rule."""
    rule = rule_for("AUR_DECLIP", "clipped")
    measurement = measure("AUR_DECLIP", "clipped")
    assert measurement.output_finite
    assert measurement.reference_error_db is not None
    assert measurement.reference_error_db >= rule.reference_error_db, (
        "the module came %.2f dB closer to the truth, the rule asks for %.2f dB"
        % (measurement.reference_error_db, rule.reference_error_db)
    )
    assert measurement.kept_db <= rule.keep_db, (
        "the module moved a wanted partial by %.2f dB, the budget is %.2f dB"
        % (measurement.kept_db, rule.keep_db)
    )


def test_the_module_removes_the_clipping_harmonics():
    """The distortion the clipper created has to go."""
    case = make_case("clipped")
    measurement = measure("AUR_DECLIP", "clipped")
    assert measurement.removed_db >= 6.0, measurement.removed_db
    assert case.band == (2500.0, 8000.0)


def test_the_module_does_not_damage_the_wanted_partial_of_the_case():
    """The wanted partial of the case is the truth's own content.

    It may not be moved by more than the budget of the rule, and it may not
    lose its level: a partial that is 10 dB down is gone, not repaired.
    """
    case = make_case("clipped")
    rule = rule_for("AUR_DECLIP", "clipped")
    measurement = measure("AUR_DECLIP", "clipped")
    assert measurement.kept_db <= rule.keep_db
    for freq in case.tones:
        out_level = tone_level(
            np.asarray(
                registry.create("AUR_DECLIP", SR, 1).process(case.mixed[:, None]),
                dtype=np.float64,
            )[:, 0],
            freq,
        )
        truth_level = tone_level(case.reference, freq)
        assert abs(out_level - truth_level) <= rule.keep_db
        assert out_level > truth_level - 6.0


def test_the_module_is_block_coherent():
    """The result may not depend on the block the host hands over."""
    measurement = measure("AUR_DECLIP", "clipped")
    assert measurement.block_coherence_db < -10.0, measurement.block_coherence_db


def test_every_quality_preset_rebuilds_the_peaks():
    """Low, Medium and High all produce a usable result."""
    case = make_case("clipped")
    for quality in ("Low", "Medium", "High"):
        measurement = measure("AUR_DECLIP", "clipped", params={"Quality": quality})
        assert measurement.output_finite
        assert measurement.reference_error_db >= 6.0, quality
        assert measurement.kept_db <= 2.5, quality
