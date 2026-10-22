"""
The gate of the model in the offline path.

The tests in this file measure what the **model seam** of the Voice De-Noise
module does, not only that it runs.  Every test states the measurement it
uses in the docstring, so a failure tells you what the product really did.

The rules
---------
1. The real time path does not change: with a model in the library and with
   a model asked for by name, the output is the output of the module without
   the seam, **sample for sample**, and the model is not even loaded.
2. A model belongs to the offline path: it is bound there and nowhere else.
3. The latency follows the model: the context of the model that runs is
   published, and the published figure is the delay the output really has.
4. The control reads the model that is **actually running**, and selecting a
   different model changes what runs.
5. A missing model is not an error: the module works as if no model existed
   and says why.
6. The state carries the choice: a session that names a model reopens with
   that model named, and a session whose model file is gone says so.

The module is ``aurcore/modules/voice_de_noise.py``; the seam it uses is
``aurcore/models/`` (the library, the loader and the resolver).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from aurcore import models, registry

MODULE_CODE = "AUR_VOICEDENOISE"

#: The problem the module asks a model for.
PROBLEM = "voice"

#: The modes of the product.
REALTIME = "realtime"
OFFLINE = "offline"


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


def _library() -> models.ModelLibrary:
    """The shipped model library."""
    return models.open_library()


def _names() -> list[str]:
    """The names of the models the library holds."""
    try:
        return _library().names()
    except Exception:  # pragma: no cover - a broken library
        return []


def _voice_model() -> str:
    """The name of the model the library holds for the voice problem.

    The library may hold several models for the same problem; the one that
    counts is the one the library itself hands out for that problem, so the
    test and the product always ask the same question.
    """
    try:
        entry = _library().for_problem(PROBLEM)
    except Exception:  # pragma: no cover - a broken library
        entry = None
    if entry is None:
        pytest.skip("the library holds no model for the voice problem")
    return entry.name


def _make(mode: str = REALTIME):
    """A fresh instance of the module in the given mode."""
    if not registry.has(MODULE_CODE):
        pytest.skip(f"module {MODULE_CODE!r} is not registered yet")
    module = registry.create(MODULE_CODE, HARNESS.SR, 1)
    module._processing_mode = mode
    return module


def _signal(seed: int = 20_260_102) -> np.ndarray:
    """The synthetic voice under a white bed, the case of the voice rules."""
    cases = HARNESS.build_cases()
    return np.asarray(cases["voice_hiss"].x, dtype=np.float64)


def _render(module, signal: np.ndarray) -> np.ndarray:
    """Run a signal through a module."""
    return np.asarray(module.process(signal), dtype=np.float64)


# --------------------------------------------------------------------------
# 1. the real time path does not change
# --------------------------------------------------------------------------
def test_the_real_time_path_does_not_load_a_model():
    """The real time path may not load a model at all.

    Measurement: the number of calls the loader of the library receives while
    the module runs in the real time mode, with a model asked for by name.
    The number has to be zero: a load costs time and memory, and the path
    that has to keep inside the budget of a block may not pay for it.
    """
    name = _voice_model()
    loads = {"count": 0}
    original = models.ModelLibrary.load

    def counting(self, model_name, **kwargs):
        loads["count"] += 1
        return original(self, model_name, **kwargs)

    models.ModelLibrary.load = counting
    try:
        module = _make(REALTIME)
        message = module.select_model(name)
        _render(module, _signal())
    finally:
        models.ModelLibrary.load = original

    assert loads["count"] == 0, (
        f"the real time path loaded a model {loads['count']} time(s)"
    )
    assert module.model_name == "", (
        "the real time path runs a model: " + message
    )


def test_the_real_time_path_is_the_same_with_and_without_a_model():
    """Asking for a model may not change the real time path.

    Measurement: the output of the module in the real time mode with a model
    asked for by name, against the output of the module without the model.
    The two have to be equal sample for sample.
    """
    name = _voice_model()
    signal = _signal()
    plain = _render(_make(REALTIME), signal)
    asked = _make(REALTIME)
    asked.select_model(name)
    with_model = _render(asked, signal)
    assert np.array_equal(plain, with_model), (
        "the real time path moved when a model was asked for"
    )


def test_the_real_time_path_is_block_coherent_with_a_model_asked():
    """The real time path stays as block coherent as it was without a model.

    Measurement: the residual between a whole render and a render in blocks
    of 4096 samples, with a model asked for by name and without one.  The
    model may not make the path less coherent.
    """
    name = _voice_model()
    cases = HARNESS.build_cases()
    case = cases["voice_hiss"]
    signal = np.asarray(case.x, dtype=np.float64)

    def coherence(asked: bool) -> float:
        module = _make(REALTIME)
        if asked:
            module.select_model(name)
        return HARNESS.measure_block_coherence(
            case, lambda: module, None, block_size=4096
        )["block_rms_diff_db"]

    plain = coherence(False)
    with_model = coherence(True)
    assert with_model <= plain + 1e-9, (
        f"the block coherence went from {plain:.1f} dB to {with_model:.1f} dB "
        "when a model was asked for"
    )


# --------------------------------------------------------------------------
# 2. a model belongs to the offline path
# --------------------------------------------------------------------------
def test_the_offline_path_runs_the_model_that_solves_the_problem():
    """The offline path asks for the model that solves the voice problem.

    Measurement: the name the module publishes after it ran in the offline
    mode, against the name of the model the library holds for the voice
    problem.
    """
    wanted = _voice_model()
    module = _make(OFFLINE)
    _render(module, _signal())
    assert module.model_name == wanted, (
        f"the offline path runs {module.model_name!r}, the library holds "
        f"{wanted!r} for the voice problem"
    )


def test_the_control_reports_the_model_that_is_running():
    """The control reads the model that is running, not the first entry.

    Measurement: the description the module publishes and the description
    the seam of the core publishes for the same module.  They have to name
    the same model, and that model has to be the one the module runs on.
    """
    wanted = _voice_model()
    module = _make(OFFLINE)
    _render(module, _signal())
    described = module.model_description
    assert described, "the module publishes no description of its model"
    assert described["name"] == module.model_name == wanted, (
        f"the control reports {described['name']!r}, the module runs "
        f"{module.model_name!r}"
    )
    running = models.running_model(module)
    assert running is not None, "the seam holds no model for the module"
    assert running.name == module.model_name, (
        "the seam and the module disagree about the model that runs"
    )


def test_selecting_a_different_model_changes_what_runs():
    """Selecting another model has to change what runs.

    Measurement: the name the module publishes after another model was
    selected, and the bytes of the output.  The two have to be different
    models and the output has to move.
    """
    names = _names()
    if len(names) < 2:
        pytest.skip("the library holds fewer than two models")
    voice = _voice_model()
    other = next(name for name in names if name != voice)
    signal = _signal()

    first = _make(OFFLINE)
    first.select_model(voice)
    out_first = _render(first, signal)
    second = _make(OFFLINE)
    second.select_model(other)
    out_second = _render(second, signal)

    assert second.model_name != first.model_name, (
        f"both selections run {first.model_name!r}"
    )
    assert not np.array_equal(out_first, out_second), (
        "the two models produce the same output"
    )


# --------------------------------------------------------------------------
# 3. the latency follows the model
# --------------------------------------------------------------------------
def test_the_latency_publishes_the_context_of_the_model():
    """The context of the model is published as latency.

    Measurement: the latency the module publishes in the offline mode with a
    model, against the latency of the same mode without a model.  The
    difference has to be the context of the model that runs, in samples.
    """
    name = _voice_model()
    plain = _make(OFFLINE)
    plain.select_model("")
    with_model = _make(OFFLINE)
    with_model.select_model(name)
    _render(with_model, _signal())

    context = int(with_model._model_delay)
    assert with_model.latency_samples == plain.latency_samples + context, (
        f"the module publishes {with_model.latency_samples} samples of latency, "
        f"the model reads {context} samples of history"
    )


def test_the_published_latency_is_the_delay_of_the_output():
    """The published latency has to be the delay the output really has.

    Measurement: the delay of the output against the input, found with a cross
    correlation, against the figure the module publishes.
    """
    name = _voice_model()
    module = _make(OFFLINE)
    module.select_model(name)
    signal = _signal()
    out = _render(module, signal)
    # the search window has to be wide enough for the delay of the model: the
    # context of a model adds to the look ahead of the module, and the
    # default window of the instrument would not see the peak
    measured = HARNESS.measure_latency(signal, out, max_lag=16384)
    assert abs(measured - module.latency_samples) <= 2, (
        f"the module publishes {module.latency_samples} samples of latency "
        f"but delays the signal by {measured}"
    )


def test_the_real_time_latency_does_not_move():
    """The real time path publishes the latency it always published.

    Measurement: the latency of the module in the real time mode, with and
    without a model asked for.  The figure has to be the same, and it has to
    be the one the module published before the model seam existed.
    """
    name = _voice_model()
    plain = _make(REALTIME)
    asked = _make(REALTIME)
    asked.select_model(name)
    assert plain.latency_samples == asked.latency_samples, (
        "asking for a model moved the latency of the real time path"
    )
    assert plain.latency_samples == 2047, (
        f"the real time path publishes {plain.latency_samples} samples of "
        "latency, it published 2047 before the model seam existed"
    )


# --------------------------------------------------------------------------
# 4. a missing model is not an error
# --------------------------------------------------------------------------
def test_a_model_that_is_not_in_the_library_is_not_an_error():
    """A model that cannot be found is not an error.

    Measurement: the output of the module in the offline mode when a model
    that is not in the library was asked for, against the output of the
    module without a model.  The two have to be the same, and the module has
    to say why there is no model.
    """
    signal = _signal()
    plain = _make(OFFLINE)
    plain.select_model("")
    out_plain = _render(plain, signal)

    missing = _make(OFFLINE)
    message = missing.select_model("a_model_that_is_not_in_the_library")
    out_missing = _render(missing, signal)

    assert np.array_equal(out_plain, out_missing), (
        "a model that is not in the library changed the output"
    )
    assert missing.model_name == "", "a model that is not there is running"
    assert message, "the module does not say why there is no model"


def _empty_library(monkeypatch, tmp_path) -> models.ModelLibrary:
    """Point the product at a library that holds nothing."""
    empty = models.ModelLibrary(tmp_path)
    monkeypatch.setattr(models, "open_library", lambda directory=None: empty)
    monkeypatch.setattr(models, "default_resolver", lambda: models.Resolver(empty))
    monkeypatch.setattr(
        models.resolve, "default_resolver", lambda: models.Resolver(empty)
    )
    return empty


def test_an_empty_library_is_not_an_error(tmp_path, monkeypatch):
    """An empty library is not an error either.

    Measurement: the output of the module in the offline mode with an empty
    library, against the output without a model.  The two have to be the
    same, and the module has to say that the library is empty.
    """
    signal = _signal()
    plain = _make(OFFLINE)
    plain.select_model("")
    out_plain = _render(plain, signal)

    _empty_library(monkeypatch, tmp_path)
    module = _make(OFFLINE)
    out_empty = _render(module, signal)

    assert module.model_name == "", "a model ran out of an empty library"
    assert module.model_message, "the module does not say that the library is empty"
    assert np.array_equal(out_plain, out_empty), (
        "an empty library changed the output"
    )


def test_a_model_that_does_not_match_the_engine_is_refused(monkeypatch):
    """A model that does not match the engine is refused with a message.

    Measurement: the message the module publishes when it is given a model
    that runs at another sample rate.  The message has to name the reason.
    """
    library = _library()
    names = library.names()
    if not names:
        pytest.skip("the model library is empty")
    model = library.load(names[0])
    if model.manifest.input.sample_rate == HARNESS.SR:
        pytest.skip("every model of the library runs at the rate of the harness")

    module = _make(OFFLINE)
    module._processing_mode = OFFLINE
    message = module.select_model(names[0])
    assert module.model_name == "", "a model that does not match the engine runs"
    assert message, "the module does not say why the model was refused"


# --------------------------------------------------------------------------
# 5. the state carries the choice
# --------------------------------------------------------------------------
def test_a_session_that_names_a_model_reopens_with_it():
    """The name of the model travels with the state of the module.

    Measurement: the state of a module that was given a model, written into
    a module that was given none, and the name the second module publishes
    after the state was read back.
    """
    name = _voice_model()
    source = _make(OFFLINE)
    source.select_model(name)
    blob = source.state_base64()

    target = _make(OFFLINE)
    target.load_state_base64(blob)
    assert target.model_name == name, (
        f"the session reopened with {target.model_name!r}, it named {name!r}"
    )


def test_a_session_whose_model_file_is_gone_says_so(monkeypatch, tmp_path):
    """A session whose model file is gone says so instead of rendering quietly.

    Measurement: the message of a module that reads back the state of a
    session that named a model, with a library that does not hold it.
    """
    name = _voice_model()
    source = _make(OFFLINE)
    source.select_model(name)
    blob = source.state_base64()

    _empty_library(monkeypatch, tmp_path)
    target = _make(OFFLINE)
    target.load_state_base64(blob)

    assert target.model_name == "", "a model that is gone is running"
    assert target.model_message, (
        "the session does not say that the model file is gone"
    )


def test_the_choice_of_no_model_survives_the_state():
    """The choice of no model travels with the state as well.

    Measurement: the name the module publishes after it read back the state
    of a session that was rendered without a model.
    """
    source = _make(OFFLINE)
    source.select_model("")
    blob = source.state_base64()
    target = _make(OFFLINE)
    target.load_state_base64(blob)
    assert target.model_name == "", "a model ran although the session named none"
    _render(target, _signal())
    assert target.model_name == "", (
        "a model ran although the session named none"
    )


# --------------------------------------------------------------------------
# 6. the offline path with a model is a different render
# --------------------------------------------------------------------------
def test_the_offline_path_with_a_model_is_not_the_path_without():
    """The model has to change what the offline path renders.

    Measurement: the output of the module in the offline mode with a model,
    against the output of the same mode without one.  The two have to be
    different, and the module has to report the model it runs.
    """
    name = _voice_model()
    signal = _signal()
    plain = _make(OFFLINE)
    plain.select_model("")
    out_plain = _render(plain, signal)
    with_model = _make(OFFLINE)
    with_model.select_model(name)
    out_model = _render(with_model, signal)

    assert not np.array_equal(out_plain, out_model), (
        "the model did not change the render"
    )
    assert with_model.model_name == name


def test_the_offline_path_with_a_model_keeps_the_wanted_content():
    """The model may take more noise away, not more wanted content.

    Measurement: the level of the band that carries the voice, before and
    after the module, with and without the model, on the case of the voice
    rules.  The wanted content may not move further with the model than
    without it.
    """
    name = _voice_model()
    cases = HARNESS.build_cases()
    case = cases["voice_hiss"]
    signal = np.asarray(case.x, dtype=np.float64)

    plain = _make(OFFLINE)
    plain.select_model("")
    out_plain = _render(plain, signal)
    with_model = _make(OFFLINE)
    with_model.select_model(name)
    out_model = _render(with_model, signal)

    latency = int(plain.latency_samples)
    before = HARNESS.band_energy_db(signal[: signal.shape[0] - latency], *HARNESS.VOICE_BAND)
    plain_db = HARNESS.band_energy_db(
        out_plain[latency:], *HARNESS.VOICE_BAND
    ) - before
    model_db = HARNESS.band_energy_db(
        out_model[latency:], *HARNESS.VOICE_BAND
    ) - before
    assert abs(model_db) <= abs(plain_db) + 0.5, (
        f"the voice band moved by {model_db:.3f} dB with the model and by "
        f"{plain_db:.3f} dB without it"
    )
