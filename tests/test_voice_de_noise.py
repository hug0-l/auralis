"""
tests/test_voice_de_noise.py — the guard of the Voice De-Noise module.

The module owns the voice path, and the rules of the gain application are
its own (see ``qa/quality/rules_extra.py``).  This file asserts them:

* **the bound on the movement of the gain** — the gain may not cover more
  than 65 % of the distance to its target in one frame when it opens, and
  more than 10 % when it closes;
* **the creep of the noise reference** — the gain may not breathe by more
  than 1.750 dB^2 on a steady bed, where there is nothing to decide;
* **the level of the untouched material** — a band the module has decided
  to leave alone may not move by more than 0.04 dB.

It also holds the **target of the noise reference**: the reference may not
be deeper than 29 ms, because the depth of the reference is the lag of the
decision of the module.

Every test says in its docstring which measurement it uses, so that a
failure tells what the module really did.

Run it with ``python -m pytest tests/test_voice_de_noise.py -q``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aurcore import registry  # noqa: E402
import aurcore.modules  # noqa: E402,F401
from aurcore.modules import voice_de_noise as voice  # noqa: E402

#: The module code of the voice module.
MODULE_CODE = "AUR_VOICEDENOISE"

#: The sample rate every measurement is taken at.
SAMPLE_RATE = 48_000


def _load(name: str, path: Path):
    """Import a module of the quality instrument by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


#: The rules of the gain application.
RULES = _load("aur_qa_quality_rules_extra", _ROOT / "qa" / "quality" / "rules_extra.py")


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
def _rule_params() -> list:
    """One test per rule of every module that has one."""
    params = []
    for rule in RULES.RULES:
        for code in rule.modules:
            params.append(
                pytest.param(rule, code, id="%s_%s" % (code.lower(), rule.key))
            )
    return params


@pytest.mark.parametrize("rule,code", _rule_params())
def test_the_module_holds_the_rule_of_the_gain_application(rule, code):
    """Every rule of the gain application has to hold for every module that has it.

    Measurement: the instrument of ``qa/quality/rules_extra.py``.  The rule
    says what is measured, the unit and the limit; the failure message
    quotes the number that was measured.
    """
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    values = RULES.measure_rule(rule, code)
    failures = rule.check(values)
    assert not failures, (
        "%s (%s): %s" % (rule.name, code, "; ".join(failures))
    )


def test_the_module_holds_the_rule_of_the_gain_application_at_every_rate():
    """The rules of the gain application hold at every sample rate.

    Measurement: the same instrument, at 44.1 kHz and at 96 kHz.  The
    smoothing of the gain and the depth of the noise reference are both
    given in seconds, so a rate that is not 48 kHz must not change the
    numbers.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    for rate in (44_100, 96_000):
        for rule in RULES.rules_for(MODULE_CODE):
            values = RULES.measure_rule(rule, MODULE_CODE, rate)
            failures = rule.check(values)
            assert not failures, (
                "%s at %d Hz: %s" % (rule.name, rate, "; ".join(failures))
            )


# ---------------------------------------------------------------------------
# the target of the noise reference
# ---------------------------------------------------------------------------
def test_the_noise_reference_is_29_ms_deep():
    """The noise reference may not be deeper than 29 ms.

    Measurement: the number of frames the reference looks back over, read
    from the module, against the hop of the analysis.  The depth of the
    reference is the lag of the decision: a deeper reference holds the gate
    of a band shut for longer than the reference of the study does.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    module = registry.create(MODULE_CODE, SAMPLE_RATE, 1)
    frames = int(module._reference_frames)
    hop = int(voice._HOP)
    depth_ms = 1000.0 * frames * hop / float(SAMPLE_RATE)
    assert depth_ms <= 29.0 + 1e-9, (
        f"the noise reference is {depth_ms:.2f} ms deep ({frames} frames), the "
        "target is 29 ms"
    )
    assert depth_ms >= 20.0, (
        f"the noise reference is {depth_ms:.2f} ms deep, that is too shallow to "
        "estimate a noise floor from"
    )


def test_the_depth_of_the_reference_follows_the_sample_rate():
    """The depth of the reference is a time, not a number of frames.

    Measurement: the number of frames the reference looks back over at two
    sample rates.  The frames of the analysis are longer at a lower rate, so
    the number of frames has to grow with the rate.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    low = registry.create(MODULE_CODE, 44_100, 1)
    high = registry.create(MODULE_CODE, 96_000, 1)
    assert high._reference_frames > low._reference_frames
    for module in (low, high):
        depth_ms = 1000.0 * module._reference_frames * voice._HOP / float(
            module.sample_rate
        )
        assert depth_ms <= 29.0 + 1e-9


# ---------------------------------------------------------------------------
# the module
# ---------------------------------------------------------------------------
def test_the_module_does_not_take_the_voice_with_it():
    """The wanted partials may not move by more than 1.5 dB.

    Measurement: the shared quality instrument on the ``voice_hiss`` case:
    the level of the partials of the voice before and after the module.
    The budget is the one of the quality rule of the module.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    quality = _load("aur_qa_quality_measurement", _ROOT / "qa" / "quality" / "measurement.py")
    measurement = quality.measure(MODULE_CODE, "voice_hiss")
    assert measurement.kept_db <= 1.5, (
        f"the module moves a wanted partial by {measurement.kept_db:.2f} dB, the "
        "budget is 1.5 dB"
    )


def test_the_module_leaves_a_partial_it_does_not_touch_alone():
    """A strong partial has to come out at the level it went in.

    Measurement: the level of the 1 kHz partial of a bed of noise before
    and after the module.  The partial stands 44 dB above the bed, so the
    module has no reason to touch it.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    scene = RULES.partial_scene()
    module = registry.create(MODULE_CODE, SAMPLE_RATE, 1)
    out = np.asarray(module.process(scene[:, None]), dtype=np.float64)[:, 0]
    lo = RULES._PARTIAL_HZ * (1.0 - RULES._PARTIAL_WIDTH)
    hi = RULES._PARTIAL_HZ * (1.0 + RULES._PARTIAL_WIDTH)
    before = RULES.band_energy_frames(scene, lo, hi)
    after = RULES.band_energy_frames(out, lo, hi)
    count = min(before.size, after.size)
    window = slice(int(count * 0.3), int(count * 0.7))
    change = 10.0 * np.log10(
        np.maximum(np.median(after[window]), 1e-30)
        / np.maximum(np.median(before[window]), 1e-30)
    )
    assert abs(change) <= 0.04, (
        f"the partial comes out {change:.4f} dB away from where it went in, "
        "the budget is 0.04 dB"
    )


def test_the_module_is_block_coherent():
    """The output may not depend on the block size of the host.

    Measurement: the same signal in one block and in blocks of 4096
    samples.  A block coherent module returns the same samples.
    """
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    scene = RULES.noise_bed(1.0, -45.0, seed=17)
    whole = registry.create(MODULE_CODE, SAMPLE_RATE, 1)
    out_whole = np.asarray(whole.process(scene[:, None]), dtype=np.float64)
    chunked = registry.create(MODULE_CODE, SAMPLE_RATE, 1)
    pieces = []
    for start in range(0, scene.size, 4096):
        block = scene[start : start + 4096]
        pieces.append(np.asarray(chunked.process(block[:, None]), dtype=np.float64))
    out_chunked = np.concatenate(pieces, axis=0)
    count = min(out_whole.shape[0], out_chunked.shape[0])
    difference = out_whole[:count] - out_chunked[:count]
    assert np.max(np.abs(difference)) < 1e-9, (
        "the output depends on the block size of the host: the largest "
        "difference is %.3e" % float(np.max(np.abs(difference)))
    )
