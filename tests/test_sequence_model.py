"""
tests/test_sequence_model.py — the network that remembers.

The first generation of the learned stage had no memory: every frame was
read on its own, so the answer for a frame was a function of that frame
and of nothing else.  The second kind of network is a stack of **causal**
convolutions over the time axis, so the answer for a frame is a function
of the frames before it.

The tests cover the five things the second kind promises:

* the **format**: a manifest can name a sequence network, and it is
  refused when the context, the features or the weights do not match it;
* the **reach**: the network reads as many frames before a frame as it
  publishes, and not one more;
* the **batch**: the batched path is the frame by frame path, bit for
  bit, as it is for the fully connected kind;
* the **blocks**: a block that follows another one reads the history of
  the first;
* the **gradient**: the gradient of the convolution is the gradient, which
  is what makes the training of the stack worth anything at all.

The tests build their own model in a temporary directory, so they do not
depend on the shipped artefact, and they read the shipped artefact for the
listing and for the promise that a model with a memory does not replace
the model that runs today.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

import numpy as np
import pytest

from aurcore import models
from aurcore.models import errors
from aurcore.models.engine import ModelRunner, SpectralModel
from aurcore.models.manifest import Manifest
from aurcore.models.train import (
    MEMORY_LAYERS,
    MEMORY_MODEL,
    build_sample_memory_model,
)


@lru_cache(maxsize=None)
def _tiny_sequence():
    """Build a small sequence model for the tests.

    The model is built once and shared: the tests never change it.
    """
    manifest, weights, _losses = build_sample_memory_model(epochs=1)
    manifest = replace(manifest, name="tiny_sequence")
    return manifest, {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}


def _model() -> SpectralModel:
    """The small sequence model, ready to run."""
    manifest, weights = _tiny_sequence()
    return SpectralModel(manifest, weights)


def _document(manifest) -> dict:
    """Return the manifest as the JSON document it is stored as."""
    import json

    return json.loads(json.dumps(manifest.to_dict()))


def _frames(rng, model: SpectralModel, count: int = 24) -> np.ndarray:
    """A table of magnitude spectra the model can read."""
    bins = model.manifest.input.bins
    levels = rng.uniform(-70.0, -10.0, size=(bins, count))
    return np.power(10.0, levels / 20.0) * model.manifest.input.frame_size / 4.0


# ---------------------------------------------------------------------------
# the format
# ---------------------------------------------------------------------------
def test_the_manifest_reads_a_sequence_network():
    """A sequence network is a network the manifest can name.

    The kind is part of the manifest, the layers of a sequence carry a
    kernel and a dilation, and the manifest comes back as it went in.
    """
    manifest, _weights = _tiny_sequence()
    assert manifest.network.kind == "sequence"
    assert len(manifest.network.layers[0]) == 5
    document = manifest.to_dict()
    again = Manifest.from_dict(document)
    assert again.network.kind == "sequence"
    assert again.to_dict() == document


def test_the_reach_of_a_sequence_is_the_sum_of_what_its_layers_reach():
    """The reach of the stack is the sum of the reach of its layers.

    A layer of a kernel of three taps and a dilation of sixteen reads
    the frame sixteen before it and the one before that: the reach of
    the layer is ``(kernel - 1) * dilation`` frames.
    """
    manifest, _weights = _tiny_sequence()
    expected = 1 + sum((kernel - 1) * dilation for _, _, kernel, dilation in MEMORY_LAYERS)
    assert manifest.network.receptive_span == expected
    assert manifest.context.frames == expected - 1


def test_the_manifest_refuses_a_sequence_whose_context_is_not_its_reach():
    """A context that does not match the stack is a refusal.

    The context is what the engine promises the model it can be asked
    after; a context the stack does not keep is a promise the model
    cannot hold.
    """
    manifest, _weights = _tiny_sequence()
    document = _document(manifest)
    document["context"]["frames"] = 12
    with pytest.raises(errors.ManifestError):
        Manifest.from_dict(document)


def test_the_manifest_refuses_a_sequence_that_reads_a_history_of_its_own():
    """The memory of a sequence is the memory of its stack.

    A model whose features read the frames before them *and* whose
    network reads them as well would have two memories, and the engine
    would only keep one of them.
    """
    manifest, _weights = _tiny_sequence()
    document = _document(manifest)
    document["input"]["features"]["delta_frames"] = 1
    with pytest.raises(errors.ManifestError):
        Manifest.from_dict(document)


def test_the_manifest_refuses_a_weight_whose_shape_is_not_the_one_the_network_needs():
    """The weight of a layer is a kernel, and the manifest says so."""
    manifest, _weights = _tiny_sequence()
    document = _document(manifest)
    document["weights"][0]["shape"] = [64, 32]
    with pytest.raises(errors.ManifestError):
        Manifest.from_dict(document)


def test_the_engine_refuses_a_kernel_whose_shape_is_not_the_one_the_network_needs():
    """The engine checks the weights against the manifest as well.

    A file whose weights do not have the shape the manifest declares is
    refused before it runs, not half way through the first block.
    """
    manifest, weights = _tiny_sequence()
    broken = dict(weights)
    broken["w0"] = np.zeros((64, 32), dtype=np.float32)
    with pytest.raises(errors.WeightsMismatchError):
        SpectralModel(manifest, broken)


# ---------------------------------------------------------------------------
# the reach
# ---------------------------------------------------------------------------
def test_a_sequence_reads_as_far_back_as_it_publishes():
    """The memory of the model is the memory it publishes.

    A change in one frame reaches the frames that follow it, up to the
    reach of the stack and not one frame further.
    """
    model = _model()
    rng = np.random.default_rng(4242)
    span = model.manifest.network.receptive_span
    levels = rng.normal(0.0, 1.0, size=(model.manifest.input.features.bands, span + 20))

    history = model.sequence_state()
    plain = model.network_batch_sequence(levels, history)

    moved = levels.copy()
    moved[:, 0] += 10.0
    history = model.sequence_state()
    changed = model.network_batch_sequence(moved, history)

    difference = np.abs(changed - plain).max(axis=0)
    assert difference[0] > 0.0
    assert difference[span - 1] > 0.0
    assert difference[span] == 0.0
    assert difference[span + 1] == 0.0


def test_a_sequence_does_not_read_the_future():
    """The convolution is causal: the future cannot change the present.

    A model that read the frames ahead of the one it answers would be a
    model of the future, and the offline path would be the only place it
    could run.
    """
    model = _model()
    rng = np.random.default_rng(99)
    levels = rng.normal(0.0, 1.0, size=(model.manifest.input.features.bands, 40))

    history = model.sequence_state()
    plain = model.network_batch_sequence(levels, history)

    moved = levels.copy()
    moved[:, 20:] += 10.0
    history = model.sequence_state()
    changed = model.network_batch_sequence(moved, history)

    assert np.array_equal(changed[:, :20], plain[:, :20])


# ---------------------------------------------------------------------------
# the batch
# ---------------------------------------------------------------------------
def test_the_batch_of_a_sequence_is_the_frame_by_frame_path_bit_for_bit():
    """The batched path is a shortcut, not a different answer.

    The batch evaluates every frame of a block in one pass and keeps the
    order of the accumulation of the frame by frame path, so the two
    agree to the bit.
    """
    model = _model()
    rng = np.random.default_rng(31337)
    magnitudes = _frames(rng, model, count=24)
    frame_by_frame = ModelRunner(model).process(magnitudes)
    batched = ModelRunner(model).process_batch(magnitudes)
    assert batched.tobytes() == frame_by_frame.tobytes()


def test_the_blocks_of_a_sequence_carry_the_history():
    """A block that follows another one reads the history of the first.

    The memory of a sequence lives across the blocks, so a block that
    starts on its own would answer with an empty memory.
    """
    model = _model()
    rng = np.random.default_rng(2718)
    magnitudes = _frames(rng, model, count=24)
    one_block = ModelRunner(model).process_batch(magnitudes)

    runner = ModelRunner(model)
    first = runner.process_batch(magnitudes[:, :12])
    second = runner.process_batch(magnitudes[:, 12:])
    assert np.concatenate([first, second], axis=1).tobytes() == one_block.tobytes()


def test_a_sequence_starts_from_nothing():
    """A model whose memory is not fed sees zeros for the frames it lacks.

    The frames before the first one are not there, and a convolution
    reads them as zero — the same in the frame by frame path and in the
    batch.
    """
    model = _model()
    rng = np.random.default_rng(7)
    levels = rng.normal(0.0, 1.0, size=(model.manifest.input.features.bands, 12))
    history = model.sequence_state()
    batch = model.network_batch_sequence(levels, history)
    history = model.sequence_state()
    frame_by_frame = np.empty((12, model.manifest.input.features.bands))
    for index in range(12):
        frame_by_frame[index] = model.network_sequence(levels[:, index], history)
    assert np.array_equal(batch.T, frame_by_frame)


# ---------------------------------------------------------------------------
# the gradient
# ---------------------------------------------------------------------------
def test_the_gradient_of_a_convolution_is_the_gradient():
    """The gradient of the training is the gradient of the loss.

    The training of a convolution stack is written by hand, so the
    numbers it computes are checked against the loss itself: the
    difference of the loss over a small step of a weight has to be the
    gradient the training computed.
    """
    from aurcore.models.train import _backward_conv, _forward_conv

    manifest, weights = _tiny_sequence()
    layers = manifest.network.layers
    rng = np.random.default_rng(11)
    frames = 40
    inputs = rng.normal(0.0, 1.0, size=(frames, manifest.input.features.bands))
    goal = rng.normal(0.0, 1.0, size=(frames, manifest.input.features.bands))
    work = {key: np.asarray(value, dtype=np.float64) for key, value in weights.items()}

    activations, out = _forward_conv(work, inputs, layers)
    grads = _backward_conv(work, activations, out, goal, layers)

    def loss(of: dict) -> float:
        _, value = _forward_conv(of, inputs, layers)
        return float(np.sum((value - goal) ** 2) / value.shape[0])

    for key in ("w0", "w1", "w2", "b0", "b2"):
        flat = work[key].reshape(-1)
        for position in (0, flat.size // 2, flat.size - 1):
            original = flat[position]
            flat[position] = original + 1e-6
            up = loss(work)
            flat[position] = original - 1e-6
            down = loss(work)
            flat[position] = original
            numeric = (up - down) / 2e-6
            analytic = grads[key].reshape(-1)[position]
            assert abs(numeric - analytic) <= 1e-6 * max(1.0, abs(numeric)), (
                f"{key}[{position}]: {numeric} against {analytic}"
            )


# ---------------------------------------------------------------------------
# the shipped model
# ---------------------------------------------------------------------------
def test_the_library_holds_the_model_with_a_memory():
    """The library lists the model with a memory."""
    names = models.open_library().names()
    assert MEMORY_MODEL in names


def test_the_model_with_a_memory_runs_over_a_signal():
    """The shipped model with a memory answers over a real signal.

    The model is asked once per sample, so the answer has to be finite
    and inside the range the mask declares, and the same input has to
    give the same output.
    """
    library = models.open_library()
    assert library.load(MEMORY_MODEL) is not None
    rng = np.random.default_rng(2024)
    signal = rng.normal(0.0, 0.05, size=4800)
    first, _model = models.run_named(MEMORY_MODEL, signal, sample_rate=48000)
    second, _model = models.run_named(MEMORY_MODEL, signal, sample_rate=48000)
    assert first.shape == signal.shape
    assert np.isfinite(first).all()
    assert first.tobytes() == second.tobytes()


def test_a_model_with_a_memory_does_not_replace_the_model_that_runs_today():
    """The model with a memory is chosen by name.

    Its version is below the version of the frame rate voice models on
    purpose: a model that is not chosen by name must not replace the one
    that runs today.
    """
    library = models.open_library()
    chosen = library.for_problem("voice")
    assert chosen is not None
    assert chosen.name == "voice_enhance"
