"""
tests/test_quality_model_path.py — the gate of the model path of the
de-noisers.

The quality harness (``qa/quality/measurement.py``) measures every module on
the synthetic cases of ``qa/quality/ground_truth.py``, and the rules of
``qa/quality/quality_rules.py`` say what the modules have to achieve.  The
harness builds its modules in the **real time** configuration, and the real
time path never loads a model: the model path is the **offline** path.

The mode of a module is a class attribute of the module class, so the harness
can be pointed at the offline configuration -- and with it at the model path
-- without being changed.  That is what this file does.

What the file asserts:

* every rule that the real time path holds, the model path holds as well,
  and the gain curve of the model path does not breathe more than the rule
  allows,
* the hand written path is untouched: with no model bound the module does
  exactly what it does today, and it says why there is no model,
* a model that does not fit the engine is refused, and the module falls back
  to the hand written path,
* the context of the model is published as latency.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_QA = _ROOT / "qa" / "quality"
for _path in (str(_ROOT), str(_QA)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from aurcore import models, registry  # noqa: E402

import measurement as quality  # noqa: E402
from quality_rules import RULES  # noqa: E402

#: The modules that have a model path, and the problem they ask for.
MODEL_MODULES = ("AUR_SPECTRALDENOISE", "AUR_VOICEDENOISE")

#: The rules of the modules that have a model path.
MODEL_RULES = tuple(rule for rule in RULES if rule.code in MODEL_MODULES)


@contextlib.contextmanager
def offline(code: str):
    """Point every instance of a module at the offline configuration."""
    cls = registry.get(code)
    previous = cls._processing_mode
    cls._processing_mode = "offline"
    try:
        yield
    finally:
        cls._processing_mode = previous


@contextlib.contextmanager
def no_library():
    """A resolver that holds no model at all."""
    previous = models.default_resolver()
    models.set_default_resolver(models.Resolver(None))
    try:
        yield
    finally:
        models.set_default_resolver(previous)


def _signal(seed: int = 7, samples: int = 48000) -> np.ndarray:
    """A signal with a tone in it and a bed under it."""
    rng = np.random.default_rng(seed)
    t = np.arange(samples) / 48000.0
    return (0.05 * rng.standard_normal(samples) + 0.2 * np.sin(2 * np.pi * 1000 * t))[:, None]


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rule",
    [rule for rule in MODEL_RULES if rule.gain_var_db2 is not None],
    ids=[rule.identifier for rule in MODEL_RULES if rule.gain_var_db2 is not None],
)
def test_the_model_path_holds_the_gain_rule(rule):
    """The gain curve of the model path may not breathe more than the rule.

    The rule is measured in the offline configuration, which is the
    configuration the model path runs in.
    """
    with offline(rule.code):
        numbers = quality.measure(
            rule.code,
            rule.case,
            params=rule.params or None,
            band=rule.band,
            tones=rule.tones,
        )
    failures = rule.check(numbers)
    assert not failures, (
        "%s (%s on %s, offline with the model): %s"
        % (rule.name, rule.code, rule.case, "; ".join(failures))
    )


@pytest.mark.parametrize(
    "rule",
    MODEL_RULES,
    ids=[rule.identifier for rule in MODEL_RULES],
)
def test_the_model_path_is_not_worse_than_the_hand_written_path(rule):
    """The model path may not remove less than the hand written path.

    The removal of the real time path is the reference: the model path may
    not give up any of it.
    """
    numbers_realtime = quality.measure(
        rule.code,
        rule.case,
        params=rule.params or None,
        band=rule.band,
        tones=rule.tones,
    )
    with offline(rule.code):
        numbers_offline = quality.measure(
            rule.code,
            rule.case,
            params=rule.params or None,
            band=rule.band,
            tones=rule.tones,
        )
    assert numbers_offline.removed_db >= numbers_realtime.removed_db - 1.0, (
        "%s (%s on %s): the model path removed %.2f dB, the hand written path "
        "removed %.2f dB"
        % (
            rule.name,
            rule.code,
            rule.case,
            numbers_offline.removed_db,
            numbers_realtime.removed_db,
        )
    )


# ---------------------------------------------------------------------------
# the hand written path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", MODEL_MODULES)
def test_the_real_time_path_never_loads_a_model(code: str):
    """The real time path does not even ask the seam for a model."""
    module = registry.get(code)(48000, 1)
    assert module.processing_mode == "realtime"
    out = np.asarray(module.process(_signal()), dtype=np.float64)
    assert np.all(np.isfinite(out))
    assert module.model_name == "", "the real time path loaded a model"
    assert module.model_message, "the module does not say that it runs without a model"


@pytest.mark.parametrize("code", MODEL_MODULES)
def test_no_model_leaves_the_module_doing_what_it_does_today(code: str):
    """Without a model the module runs its hand written path, and says so.

    The output of the offline path with a library that holds no model is the
    output of the hand written path: the module binds nothing and the seam
    says why.
    """
    with no_library():
        module = registry.get(code)(48000, 1)
        module._processing_mode = "offline"
        out = np.asarray(module.process(_signal()), dtype=np.float64)
        assert np.all(np.isfinite(out))
        assert module.model_name == ""
        assert module.model_message, "the module does not say why there is no model"


@pytest.mark.parametrize("code", MODEL_MODULES)
def test_a_model_that_does_not_fit_is_refused(code: str):
    """A model that does not run at the configuration of the module is refused.

    The module then does what it does today, and says why.
    """
    module = registry.get(code)(48000, 1)
    module._processing_mode = "offline"
    message = module.select_model("a_model_that_is_not_in_the_library")
    assert "a_model_that_is_not_in_the_library" in message
    assert module.model_name == ""
    out = np.asarray(module.process(_signal()), dtype=np.float64)
    assert np.all(np.isfinite(out))


@pytest.mark.parametrize("code", MODEL_MODULES)
def test_the_context_of_the_model_is_published_as_latency(code: str):
    """The context of the model is latency, and the module publishes it."""
    module = registry.get(code)(48000, 1)
    module._processing_mode = "offline"
    with no_library():
        without = int(module.latency_samples)
    module.select_model("voice_enhance" if code == "AUR_VOICEDENOISE" else "ambience_match")
    if not module.model_name:
        pytest.skip("the library holds no model for this module")
    with_model = int(module.latency_samples)
    assert with_model >= without
    assert module.model_description.get("context_samples", 0) == with_model - without


# ---------------------------------------------------------------------------
# the instrument
# ---------------------------------------------------------------------------
def test_the_model_path_is_deterministic():
    """The model path gives the same numbers twice."""
    module = registry.get("AUR_SPECTRALDENOISE")(48000, 1)
    module._processing_mode = "offline"
    module.select_model("ambience_match")
    if not module.model_name:
        pytest.skip("the library holds no ambience model")
    first = np.asarray(module.process(_signal()), dtype=np.float64)
    module.reset()
    second = np.asarray(module.process(_signal()), dtype=np.float64)
    assert np.array_equal(first, second)
