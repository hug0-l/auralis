"""
tests/test_models.py — the model mechanism.

The tests cover the six things the mechanism promises:

* the **format round trip**: a model that is written comes back with the
  same manifest and the same weights;
* the **three refusals**: a manifest that does not match the weights, a
  wrong checksum, a context that is not a whole number of frames, and a
  model that does not run at the rate of the engine;
* the **determinism**: the same input gives the same output, bit for bit;
* the **listing**: the library lists the name, the version, the date and
  the problem of its models without reading a single weight;
* the **seam**: a module asks for the model that solves a problem and gets
  either a model or nothing;
* the **fallback**: without a model the module does exactly what it did
  before.

and one thing the layout has to promise:

* the **feature layout**: a quiet band and a loud one read differently, a
  band that carries nothing at all reads as empty, and the values stay
  inside the range the layout declares.

and one thing the format has to promise about the layout:

* the **feature layout version**: a manifest that names a layout this
  build does not read is refused, and so is one that names no layout at
  all: a model of an older build would be read with the wrong scale.

The tests build their own models in a temporary directory, so they do not
depend on the shipped artefacts, and they read the shipped artefacts for
the listing and the determinism.
"""
from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from aurcore import models
from aurcore.dsp.stft import STFT
from aurcore.models import errors
from aurcore.models.engine import ModelRunner, SpectralModel
from aurcore.models.train import build_all, build_voice_model, mel_edges

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_SIZE = 1024
HOP = 256


@lru_cache(maxsize=None)
def _tiny_model(name: str = "tiny_model"):
    """Build a small model for the tests.

    The model is built once and shared: the tests never change it.
    """
    manifest, weights, _losses = build_voice_model(epochs=2)

    manifest = replace(manifest, name=name)
    return manifest, {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}


def _write(directory: Path, manifest, weights, filename: str | None = None) -> Path:
    """Write a model into a directory."""
    target = directory / (filename or f"{manifest.name}.aurm")
    models.write_model(target, manifest, weights)
    return target


def _read_raw(path: Path) -> dict:
    """Return the members of a model file."""
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def _write_raw(path: Path, members: dict) -> Path:
    """Write a model file from raw members."""
    import io

    buffer = io.BytesIO()
    np.savez(buffer, **members)
    path.write_bytes(buffer.getvalue())
    return path


def _write_raw_model(path: Path, manifest, members: dict) -> Path:
    """Write a model file from a manifest and raw members.

    The writer of the product refuses a file whose weights do not match the
    manifest, so a test that wants such a file has to write it itself.
    """
    document = json.dumps(manifest.to_dict(), sort_keys=True, separators=(",", ":"))
    payload = {"__manifest__": np.frombuffer(document.encode("utf-8"), dtype=np.uint8)}
    payload.update(members)
    return _write_raw(path, payload)


def _write_raw_document(path: Path, document: dict, members: dict) -> Path:
    """Write a model file from a manifest document and raw members.

    A test that wants a manifest the parser has to refuse writes the
    document itself: the writer of the product only writes manifests that
    parse.
    """
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"))
    payload = {"__manifest__": np.frombuffer(raw.encode("utf-8"), dtype=np.uint8)}
    payload.update(members)
    return _write_raw(path, payload)


def _manifest_document(manifest) -> dict:
    """Return the manifest as the JSON document it is stored as."""
    return json.loads(json.dumps(manifest.to_dict()))


def _frames(rng, count: int = 6, bins: int | None = None) -> np.ndarray:
    """A table of magnitude spectra.

    The number of bins is the one of the model the frames are made for: the
    library holds models of different frame sizes, and a frame of the wrong
    size is a refusal, not a number.
    """
    if bins is None:
        bins = FRAME_SIZE // 2 + 1
    levels = rng.uniform(-70.0, -10.0, size=(bins, count))
    return np.power(10.0, levels / 20.0)


def _frames_for(rng, model, count: int = 6) -> np.ndarray:
    """A table of magnitude spectra of the size the model reads."""
    return _frames(rng, count=count, bins=model.manifest.input.bins)


# ---------------------------------------------------------------------------
# the format round trip
# ---------------------------------------------------------------------------
def test_the_format_round_trip(tmp_path):
    """A model that is written comes back unchanged."""
    manifest, weights = _tiny_model()
    path = _write(tmp_path, manifest, weights)

    library = models.ModelLibrary(tmp_path)
    loaded = library.load(manifest.name)

    assert loaded.manifest.name == manifest.name
    assert loaded.manifest.version == manifest.version
    assert loaded.manifest.epoch == manifest.epoch
    assert loaded.manifest.trained_on == manifest.trained_on
    assert loaded.manifest.variant == manifest.variant
    assert loaded.manifest.problem == manifest.problem
    assert loaded.manifest.task == manifest.task
    assert loaded.manifest.checksum == manifest.checksum
    assert loaded.manifest.deterministic is manifest.deterministic
    assert loaded.manifest.notes == manifest.notes
    assert loaded.manifest.input == manifest.input
    assert loaded.manifest.output == manifest.output
    assert loaded.manifest.context == manifest.context
    assert loaded.manifest.state == manifest.state
    assert loaded.manifest.network == manifest.network
    assert loaded.manifest.weights == manifest.weights

    for spec in manifest.weights:
        stored = np.asarray(weights[spec.key])
        read = np.asarray(loaded.weights[spec.key])
        assert read.dtype == np.float32
        assert read.shape == stored.shape
        assert read.tobytes() == stored.tobytes()

    # the manifest that was written is the manifest that comes back
    document = models.read_manifest(path)
    assert document["format"] == models.FORMAT_NAME
    assert document["format_version"] == models.FORMAT_VERSION
    assert document["feature_layout_version"] == models.FEATURE_LAYOUT_VERSION
    assert document["name"] == manifest.name
    assert document["context"] == {"frames": 0, "samples": 0}
    assert document["state"] == {"scope": "block", "cleared": "block"}
    assert document["input"]["sample_rate"] == SAMPLE_RATE
    assert document["input"]["frame_size"] == FRAME_SIZE
    assert document["input"]["hop"] == HOP
    assert document["output"]["kind"] == "spectral_mask"
    assert document["checksum"].startswith("sha256:")


def test_the_checksum_covers_the_weights(tmp_path):
    """The checksum changes when a weight changes."""
    manifest, weights = _tiny_model()
    first = models.checksum_of(weights)
    assert first == manifest.checksum
    changed = dict(weights)
    changed["w0"] = np.asarray(changed["w0"]) + np.float32(1e-6)
    assert models.checksum_of(changed) != manifest.checksum


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------
def test_it_refuses_a_manifest_that_does_not_match_the_weights(tmp_path):
    """A weight with another shape than the manifest declares is refused."""
    manifest, weights = _tiny_model()
    broken = {key: np.asarray(value) for key, value in weights.items()}
    broken["w0"] = broken["w0"][:, :-1].copy()
    path = tmp_path / f"{manifest.name}.aurm"
    _write_raw_model(path, manifest, broken)

    library = models.ModelLibrary(tmp_path)
    with pytest.raises(errors.WeightsMismatchError) as failure:
        library.load(manifest.name)
    message = str(failure.value)
    assert "w0" in message
    assert "shape" in message


def test_it_refuses_a_weight_that_is_not_in_the_file(tmp_path):
    """A weight the manifest declares but the file does not hold is refused."""
    manifest, weights = _tiny_model()
    path = _write(tmp_path, manifest, weights)
    members = _read_raw(path)
    del members["w1"]
    _write_raw(path, members)

    library = models.ModelLibrary(tmp_path)
    with pytest.raises(errors.WeightsMismatchError) as failure:
        library.load(manifest.name)
    assert "w1" in str(failure.value)


def test_it_refuses_a_wrong_checksum(tmp_path):
    """Weights that are not the weights the manifest describes are refused."""
    manifest, weights = _tiny_model()
    path = _write(tmp_path, manifest, weights)
    members = _read_raw(path)
    tampered = np.array(members["w0"], copy=True)
    tampered[0, 0] = np.float32(tampered[0, 0] + 1.0)
    members["w0"] = tampered
    _write_raw(path, members)

    library = models.ModelLibrary(tmp_path)
    with pytest.raises(errors.ChecksumError) as failure:
        library.load(manifest.name)
    message = str(failure.value)
    assert "checksum" in message
    assert manifest.checksum in message


def test_it_refuses_a_context_that_is_not_a_whole_number_of_frames(tmp_path):
    """A context of 300 samples with a hop of 256 is not a whole context."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["context"] = {"frames": 1, "samples": 300}
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    message = str(failure.value)
    assert "whole number" in message
    assert "256" in message


def test_it_refuses_a_context_that_does_not_match_the_features(tmp_path):
    """A context that the features do not read is refused."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["context"] = {"frames": 1, "samples": HOP}
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    assert "history" in str(failure.value)


def test_it_refuses_a_model_that_does_not_run_at_the_rate_of_the_engine(tmp_path):
    """A model trained at 48 kHz does not run in an engine at 44.1 kHz."""
    manifest, weights = _tiny_model()
    _write(tmp_path, manifest, weights)
    library = models.ModelLibrary(tmp_path)

    with pytest.raises(errors.ConfigMismatchError) as failure:
        library.load(manifest.name, sample_rate=44100)
    message = str(failure.value)
    assert "48000" in message
    assert "44100" in message

    # the model runs at the rate it was trained at
    assert library.load(manifest.name, sample_rate=SAMPLE_RATE) is not None


def test_it_refuses_a_state_that_outlives_its_scope(tmp_path):
    """A state that is cleared at the session but belongs to a file is refused."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["state"] = {"scope": "file", "cleared": "session"}
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    assert "outlive" in str(failure.value)


def test_it_refuses_a_manifest_with_an_unknown_field(tmp_path):
    """A field the format does not define is refused."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["extra"] = 1
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    assert "extra" in str(failure.value)


def test_it_refuses_a_manifest_with_a_missing_field(tmp_path):
    """A field the format needs is refused when it is missing."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    del document["checksum"]
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    assert "checksum" in str(failure.value)


def test_it_refuses_a_model_of_an_unknown_problem(tmp_path):
    """A model of a problem the product does not know is refused."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["problem"] = "weather"
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    assert "weather" in str(failure.value)


def test_it_refuses_a_model_that_is_not_in_the_library(tmp_path):
    """A name that no file carries is refused."""
    library = models.ModelLibrary(tmp_path)
    with pytest.raises(errors.ModelNotFoundError):
        library.load("nothing_here")


# ---------------------------------------------------------------------------
# the feature layout version
# ---------------------------------------------------------------------------
def test_it_refuses_a_manifest_of_an_unknown_feature_layout():
    """A layout this build does not know is refused, and the message says so.

    The weights of a model of another layout have the same shapes as the
    weights of a model of this build, so the manifest is the only thing
    that can tell the two apart.
    """
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["feature_layout_version"] = 99
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    message = str(failure.value)
    assert "feature layout" in message
    assert "99" in message
    assert "trained again" in message


def test_it_refuses_a_manifest_of_the_retired_feature_layout():
    """A model of the old layout is refused, and the refusal names the layout."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    document["feature_layout_version"] = 0
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    message = str(failure.value)
    assert "raw magnitude" in message
    assert "trained again" in message


def test_it_refuses_a_manifest_without_a_feature_layout():
    """A model of a build that did not carry the field is refused.

    The manifest does not say which layout the weights were fitted on, so
    the loader could read them with the wrong scale.
    """
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    del document["feature_layout_version"]
    with pytest.raises(errors.ManifestError) as failure:
        models.Manifest.from_dict(document)
    message = str(failure.value)
    assert "feature_layout_version" in message
    assert "trained again" in message


def test_the_library_refuses_an_old_artefact(tmp_path):
    """An artefact of a build that did not carry the field is not loadable.

    The listing reports it as an entry that could not be read, with the
    reason, and the loader refuses it.
    """
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    del document["feature_layout_version"]
    members = {spec.key: np.asarray(weights[spec.key]) for spec in manifest.weights}
    _write_raw_document(tmp_path / f"{manifest.name}.aurm", document, members)

    library = models.ModelLibrary(tmp_path)
    entries = library.list()
    assert len(entries) == 1
    assert entries[0].readable is False
    assert "feature_layout_version" in entries[0].error
    with pytest.raises(errors.ManifestError):
        library.load(manifest.name)


# ---------------------------------------------------------------------------
# the listing
# ---------------------------------------------------------------------------
def test_the_library_lists_the_models_without_reading_the_weights(tmp_path):
    """The listing works on a file whose weights are gone."""
    manifest, weights = _tiny_model()
    path = _write(tmp_path, manifest, weights)
    members = _read_raw(path)
    del members["w0"]
    del members["b0"]
    _write_raw(path, members)

    library = models.ModelLibrary(tmp_path)
    entries = library.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == manifest.name
    assert entry.version == manifest.version
    assert entry.trained_on == manifest.trained_on
    assert entry.problem == manifest.problem
    assert entry.variant == manifest.variant
    assert entry.epoch == manifest.epoch
    assert entry.readable is True

    # the weights are gone, so the model cannot be loaded
    with pytest.raises(errors.WeightsMismatchError):
        library.load(manifest.name)


def test_the_listing_reports_a_broken_file_instead_of_raising(tmp_path):
    """A file that is not a model is an entry with a reason, not a crash."""
    manifest, weights = _tiny_model()
    _write(tmp_path, manifest, weights)
    (tmp_path / "broken.aurm").write_bytes(b"this is not a model file")

    library = models.ModelLibrary(tmp_path)
    entries = library.list()
    assert len(entries) == 2
    broken = [entry for entry in entries if entry.name == "broken"][0]
    assert broken.readable is False
    assert broken.error


def test_the_shipped_library_holds_a_model_for_every_problem():
    """The shipped library carries the three problems of the product."""
    library = models.open_library()
    entries = library.list()
    assert entries, "the shipped library is empty"
    problems = {entry.problem for entry in entries if entry.readable}
    assert problems == set(models.PROBLEMS)
    for entry in entries:
        assert entry.readable, f"{entry.name}: {entry.error}"
        assert entry.version
        assert entry.trained_on
        assert entry.path.endswith(".aurm")


# ---------------------------------------------------------------------------
# the determinism
# ---------------------------------------------------------------------------
def test_the_engine_is_deterministic():
    """The same input gives the same output, bit for bit."""
    rng = np.random.default_rng(4242)
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        magnitudes = _frames_for(rng, model, count=8)
        first = ModelRunner(model).process(magnitudes)
        second = ModelRunner(model).process(magnitudes)
        assert first.tobytes() == second.tobytes(), entry.name
        assert np.array_equal(first, second), entry.name


def test_the_engine_is_deterministic_over_a_whole_file():
    """A whole file gives the same result twice."""
    rng = np.random.default_rng(99)
    signal = rng.standard_normal((48000, 1)) * 0.1
    library = models.open_library()
    model = library.load("voice_enhance")
    first = models.run_offline(model, signal)
    second = models.run_offline(model, signal)
    assert first.tobytes() == second.tobytes()


def test_the_mask_is_between_zero_and_one():
    """A mask never leaves the range the manifest publishes."""
    rng = np.random.default_rng(5)
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        magnitudes = _frames_for(rng, model, count=4)
        output = ModelRunner(model).process(magnitudes)
        if model.manifest.output.kind == "spectral_mask":
            assert output.min() >= 0.0
            assert output.max() <= 1.0


# ---------------------------------------------------------------------------
# the batch
# ---------------------------------------------------------------------------
def test_the_batch_is_the_frame_by_frame_path_bit_for_bit():
    """The batched path is a shortcut, not a different answer.

    The batch evaluates every frame of a block in one pass.  It keeps the
    accumulation order of the frame by frame path, so the two agree to the
    bit — which is what lets the offline path use the batch without
    changing what a model produces.
    """
    rng = np.random.default_rng(31337)
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        magnitudes = _frames_for(rng, model, count=16)
        frame_by_frame = ModelRunner(model).process(magnitudes)
        batched = ModelRunner(model).process_batch(magnitudes)
        assert batched.tobytes() == frame_by_frame.tobytes(), entry.name


def test_the_batch_carries_the_history_of_the_block_before():
    """A block that follows another one reads the history of the first.

    A model whose state lives longer than a block reads the frames before
    the block it is given.  The batch has to carry that history the way the
    frame by frame path carries it, or the two would disagree on the first
    frames of the second block.
    """
    rng = np.random.default_rng(2718)
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        if model.manifest.state.scope == "block":
            continue
        magnitudes = _frames_for(rng, model, count=12)
        one_block = ModelRunner(model).process(magnitudes)
        runner = ModelRunner(model)
        first = runner.process(magnitudes[:, :6])
        second = runner.process(magnitudes[:, 6:])
        assert np.concatenate([first, second], axis=1).tobytes() == one_block.tobytes(), (
            entry.name
        )
        runner = ModelRunner(model)
        first = runner.process_batch(magnitudes[:, :6])
        second = runner.process_batch(magnitudes[:, 6:])
        assert np.concatenate([first, second], axis=1).tobytes() == one_block.tobytes(), (
            entry.name
        )


def test_the_offline_path_gives_the_same_bits_as_the_frame_by_frame_path():
    """The offline path runs the batch; the answer did not change.

    The offline path of a model used to evaluate the frames one by one.
    It evaluates them in one pass now, and the file it produces is the
    same file, bit for bit.
    """
    rng = np.random.default_rng(4711)
    signal = rng.standard_normal((48000, 1)) * 0.1
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        manifest = model.manifest
        engine = STFT(manifest.input.frame_size, manifest.input.hop, window="hann")
        floor = model.reference_magnitude * 10.0 ** (
            manifest.input.features.floor_db / 20.0
        )
        spectrum = engine.analyze_complex_block(signal[:, 0], center=True)[0]
        magnitudes = np.abs(spectrum)
        values = ModelRunner(model).process(magnitudes)
        if manifest.output.kind == "spectral_mask":
            gain = values
        else:
            current_db = 20.0 * np.log10(np.maximum(magnitudes, floor))
            target_db = 20.0 * np.log10(np.maximum(values, floor))
            gain = np.power(10.0, np.clip(target_db - current_db, -24.0, 24.0) / 20.0)
        expected = engine.synthesize_complex_block(
            spectrum, norm="exact", length=signal.shape[0], center=True, gain=gain
        )
        actual = models.run_offline(model, signal)
        assert actual[:, 0].tobytes() == expected.tobytes(), entry.name


# ---------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------
class _Seam:
    """A stand-in for a module that asks for a model.

    It is deliberately small: the point is not the audio it makes, it is
    that the module asks, and that the answer decides what the module
    does.
    """

    def __init__(self, mode: str = "offline", sample_rate: int = SAMPLE_RATE):
        self.processing_mode = mode
        self.sample_rate = sample_rate
        self.channels = CHANNELS
        self.used_model = False
        self.resolver = models.Resolver(None)

    def process(self, x: np.ndarray) -> np.ndarray:
        """The hand written path, or the path that runs the model."""
        binding = models.request(self, "voice", resolver=self.resolver)
        if binding is None:
            self.used_model = False
            return x * 0.5
        self.used_model = True
        runner = ModelRunner(binding.model)
        engine = STFT(FRAME_SIZE, HOP, window="hann")
        out = np.empty_like(x)
        for channel in range(x.shape[1]):
            spectrum = engine.analyze_complex_block(x[:, channel], center=True)[0]
            magnitudes = np.abs(spectrum)
            values = runner.process(magnitudes)
            out[:, channel] = engine.synthesize_complex_block(
                spectrum, norm="exact", length=x.shape[0], center=True, gain=values
            )
        return out


def test_a_module_that_asks_for_a_model_gets_one_in_the_offline_mode():
    """In the offline mode the module gets the model that solves the problem."""
    library = models.open_library()
    resolver = models.Resolver(library)
    module = _Seam(mode="offline")
    module.resolver = resolver

    rng = np.random.default_rng(3)
    signal = rng.standard_normal((4096, CHANNELS)) * 0.1
    out = module.process(signal)

    assert module.used_model is True
    binding = resolver.bound(module)
    assert binding is not None
    assert binding.name == "voice_enhance"
    assert binding.problem == "voice"
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))


def test_a_module_gets_nothing_in_the_real_time_mode():
    """In the real time mode the hand written path is what runs."""
    library = models.open_library()
    resolver = models.Resolver(library)
    module = _Seam(mode="realtime")
    module.resolver = resolver

    rng = np.random.default_rng(3)
    signal = rng.standard_normal((4096, CHANNELS)) * 0.1
    out = module.process(signal)

    assert module.used_model is False
    assert resolver.bound(module) is None
    assert np.array_equal(out, signal * 0.5)


def test_a_module_without_a_mode_is_a_real_time_module():
    """A module that does not carry the mode keeps its hand written path."""
    module = _Seam(mode="realtime")
    del module.processing_mode
    module.resolver = models.Resolver(models.open_library())
    assert models.mode_of(module) == models.REALTIME
    rng = np.random.default_rng(3)
    signal = rng.standard_normal((1024, CHANNELS)) * 0.1
    out = module.process(signal)
    assert module.used_model is False
    assert np.array_equal(out, signal * 0.5)


def test_without_a_model_the_module_does_exactly_what_it_did_before():
    """No library, no model, no change of the output."""
    module = _Seam(mode="offline")
    module.resolver = models.Resolver(None)
    rng = np.random.default_rng(11)
    signal = rng.standard_normal((4096, CHANNELS)) * 0.1

    first = module.process(signal)
    assert module.used_model is False
    second = module.process(signal)
    assert first.tobytes() == second.tobytes()
    assert np.array_equal(first, signal * 0.5)


def test_a_broken_model_leaves_the_module_on_its_own_path(tmp_path):
    """A model that does not load does not break the module."""
    manifest, weights = _tiny_model()
    document = _manifest_document(manifest)
    # a model that was trained at 44.1 kHz: the band edges follow the rate
    document["input"]["sample_rate"] = 44100
    document["input"]["features"]["band_edges_hz"] = [
        edge * 44100.0 / 48000.0 for edge in document["input"]["features"]["band_edges_hz"]
    ]
    broken = models.Manifest.from_dict(document)
    _write(tmp_path, broken, weights)

    resolver = models.Resolver(models.ModelLibrary(tmp_path))
    module = _Seam(mode="offline")
    module.resolver = resolver
    rng = np.random.default_rng(11)
    signal = rng.standard_normal((2048, CHANNELS)) * 0.1
    out = module.process(signal)

    assert module.used_model is False
    assert np.array_equal(out, signal * 0.5)
    assert resolver.last_error(module)


def test_the_control_reads_the_model_that_is_running():
    """A control reads the model that runs, not the first of the library."""
    library = models.open_library()
    resolver = models.Resolver(library)
    module = _Seam(mode="offline")
    module.resolver = resolver

    rng = np.random.default_rng(3)
    signal = rng.standard_normal((1024, CHANNELS)) * 0.1
    module.process(signal)

    described = models.describe_running(module, resolver)
    assert described is not None
    assert described["name"] == "voice_enhance"
    assert described["version"]
    assert described["trained_on"]
    assert described["problem"] == "voice"

    # the library holds the shipped models, and the control reads the one
    # that runs, not the first entry of the library
    entries = library.list()
    assert len(entries) == 6
    assert entries[0].name == "ambience_match"
    assert described["name"] == "voice_enhance"


def test_a_bound_model_is_the_model_that_keeps_running():
    """A second request for the same module gives the model that is bound."""
    library = models.open_library()
    resolver = models.Resolver(library)
    module = _Seam(mode="offline")
    module.resolver = resolver

    first = resolver.request(module, "voice")
    second = resolver.request(module, "voice")
    assert first is not None
    assert second is first


# ---------------------------------------------------------------------------
# the feature layout
# ---------------------------------------------------------------------------
def _frame_with_bands(model: SpectralModel, levels_db: dict[int, float]) -> np.ndarray:
    """A magnitude spectrum with the given bands at the given levels (dBFS).

    The magnitudes are the ones the transform of the engine produces: a
    full scale sinusoid reaches ``frame_size / 4``.
    """
    bins = model.manifest.input.bins
    reference = model.reference_magnitude
    magnitude = np.full(bins, reference * 10.0 ** (-100.0 / 20.0), dtype=np.float64)
    for band, level in levels_db.items():
        magnitude[model.band_of_bin == band] = reference * 10.0 ** (level / 20.0)
    return magnitude


def _model_with_a_hole(model: SpectralModel, band: int = 30) -> SpectralModel:
    """The same model with a band layout that leaves one band without a bin.

    The band is one hertz wide and sits between two bin centres, so it
    holds no bin at all.
    """
    manifest = model.manifest
    edges = list(manifest.input.features.band_edges_hz)
    edges[band] = 1000.0
    edges[band + 1] = 1001.0
    features = replace(manifest.input.features, band_edges_hz=tuple(float(e) for e in edges))
    patched = replace(manifest, input=replace(manifest.input, features=features))
    return SpectralModel(patched, model.weights)


def test_the_feature_layout_carries_the_level_of_a_band():
    """A quiet band and a loud one read differently, and the values are in range.

    The layout reads the level of a band in dBFS: 0 dB is a full scale
    sinusoid, the floor is the floor of the manifest and the ceiling is
    above digital full scale.  A band at -3 dBFS and a band at -40 dBFS
    therefore do not read the same, and the model can tell them apart.
    """
    manifest, weights = _tiny_model()
    model = SpectralModel(manifest, weights)
    layout = model.feature_layout()
    floor_db = manifest.input.features.floor_db
    span = layout["ceiling_db"] - floor_db

    assert layout["floor_db"] == floor_db
    assert layout["range"] == (0.0, 1.0)
    assert layout["empty"] == 0.0

    quiet, loud = 12, 40
    magnitude = _frame_with_bands(model, {quiet: -40.0, loud: -3.0})
    levels = model.band_levels(magnitude)

    assert np.all(np.isfinite(levels))
    assert levels.min() >= layout["range"][0]
    assert levels.max() <= layout["range"][1]

    # the two bands are 37 dB apart and the layout carries that distance
    assert levels[loud] > levels[quiet]
    assert levels[loud] - levels[quiet] == pytest.approx(37.0 / span, abs=1e-9)
    # and each of them reads the level it was built with
    assert levels[loud] == pytest.approx((-3.0 - floor_db) / span, abs=1e-9)
    assert levels[quiet] == pytest.approx((-40.0 - floor_db) / span, abs=1e-9)


def test_a_band_above_the_ceiling_is_not_the_same_as_a_band_at_it():
    """The ceiling is above digital full scale, so a loud band is still told apart.

    A band at full scale reads below the top of the scale, and a band above
    the ceiling is the only thing that is clipped: the ceiling sits above
    digital full scale, so a signal that does not clip its own samples
    never reaches it.
    """
    manifest, weights = _tiny_model()
    model = SpectralModel(manifest, weights)
    layout = model.feature_layout()
    floor_db = layout["floor_db"]
    span = layout["ceiling_db"] - floor_db

    assert layout["ceiling_db"] > 0.0, "the ceiling has to be above digital full scale"

    full_scale = model.band_levels(_frame_with_bands(model, {40: 0.0}))
    at_ceiling = model.band_levels(_frame_with_bands(model, {40: layout["ceiling_db"]}))
    above = model.band_levels(_frame_with_bands(model, {40: layout["ceiling_db"] + 6.0}))

    assert full_scale[40] == pytest.approx((0.0 - floor_db) / span, abs=1e-9)
    assert full_scale[40] < 1.0
    assert at_ceiling[40] == pytest.approx(1.0, abs=1e-9)
    # above the ceiling the layout clips, and nothing else does
    assert above[40] == pytest.approx(1.0, abs=1e-9)


def test_an_empty_band_reads_as_empty():
    """A band that carries nothing at all reads as the quietest value.

    A band without a bin, and a band whose bins carry no energy, are both
    the floor of the layout: the quietest value, and never the loudest.
    """
    manifest, weights = _tiny_model()
    model = SpectralModel(manifest, weights)
    layout = model.feature_layout()

    holed = _model_with_a_hole(model)
    # the energy is in another band: the band without a bin cannot hold any
    magnitude = _frame_with_bands(holed, {31: -20.0})
    levels = holed.band_levels(magnitude)
    assert levels[30] == pytest.approx(layout["empty"], abs=0.0)
    assert levels[31] > 0.0
    assert levels[30] < levels[31]

    # a frame without any energy at all
    silent = model.band_levels(np.zeros(model.manifest.input.bins))
    assert np.all(silent == pytest.approx(layout["empty"], abs=0.0))


def test_the_shipped_models_are_built_the_same_way_every_time(tmp_path):
    """Two builds of the shipped models give the same checksums.

    The build is reproducible: the same command gives the same weights and
    therefore the same checksum, every time.
    """
    first = build_all(directory=tmp_path / "first", epochs=3, verbose=False)
    second = build_all(directory=tmp_path / "second", epochs=3, verbose=False)
    assert len(first) == len(second) == 5
    for left, right in zip(first, second):
        one = models.read_manifest(left)
        other = models.read_manifest(right)
        assert one["name"] == other["name"]
        assert one["checksum"] == other["checksum"], left.name
        assert one["version"] == other["version"]
        assert one["feature_layout_version"] == other["feature_layout_version"]


def test_the_shipped_models_declare_the_layout_they_were_trained_on():
    """Every shipped model says what its features are and where they stop."""
    library = models.open_library()
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        layout = model.feature_layout()
        assert layout["floor_db"] == model.manifest.input.features.floor_db
        assert layout["ceiling_db"] > layout["floor_db"]
        assert layout["range"] == (0.0, 1.0)
        # the artefact says it too, in its own notes
        assert "dBFS" in model.manifest.notes


def test_the_shipped_models_declare_the_feature_layout_version():
    """Every shipped model says which layout its weights were fitted on.

    The layout is not in the weight shapes, so the manifest is the only
    place a reader can learn it: a model of the library that does not carry
    the field would be read with the wrong scale.
    """
    library = models.open_library()
    for entry in library.list():
        assert entry.readable, f"{entry.name}: {entry.error}"
        model = library.load(entry.name)
        assert model.manifest.feature_layout_version == models.FEATURE_LAYOUT_VERSION
        document = models.read_manifest(library.directory / f"{entry.name}.aurm")
        assert document["feature_layout_version"] == models.FEATURE_LAYOUT_VERSION


# ---------------------------------------------------------------------------
# the modules of the product
# ---------------------------------------------------------------------------
def test_the_modules_of_the_product_are_not_changed_by_the_library():
    """A module that does not ask for a model behaves exactly as before."""
    from aurcore import registry

    if not registry.has("AUR_VOICEDENOISE"):
        pytest.skip("the Voice De-Noise module is not registered")
    rng = np.random.default_rng(77)
    signal = rng.standard_normal((4096, CHANNELS)) * 0.1

    plain = registry.create("AUR_VOICEDENOISE", SAMPLE_RATE, CHANNELS)
    first = plain.process(signal)

    resolver = models.Resolver(models.open_library())
    models.set_default_resolver(resolver)
    try:
        with_library = registry.create("AUR_VOICEDENOISE", SAMPLE_RATE, CHANNELS)
        second = with_library.process(signal)
    finally:
        models.set_default_resolver(None)

    assert first.shape == second.shape
    assert first.tobytes() == second.tobytes()


def test_the_offline_path_runs_a_model_over_a_whole_signal():
    """The offline path keeps the length of the signal and stays finite."""
    library = models.open_library()
    model = library.load("voice_enhance")
    rng = np.random.default_rng(21)
    signal = rng.standard_normal((48000, CHANNELS)) * 0.1
    out = models.run_offline(model, signal)
    assert out.shape == signal.shape
    assert np.all(np.isfinite(out))


def test_the_offline_path_refuses_a_signal_at_another_rate():
    """A signal at another rate is refused, not resampled behind the back."""
    library = models.open_library()
    model = library.load("voice_enhance")
    signal = np.zeros((1024, 1))
    with pytest.raises(errors.ConfigMismatchError):
        models.run_offline(model, signal, sample_rate=44100)


def test_the_bands_of_a_model_are_published():
    """The model publishes the bands it reads."""
    library = models.open_library()
    model = library.load("voice_enhance")
    edges = model.manifest.input.features.band_edges_hz
    assert len(edges) == 65
    assert edges[0] == 0.0
    assert edges[-1] == pytest.approx(24000.0)
    assert all(edges[index] < edges[index + 1] for index in range(len(edges) - 1))
    assert edges == mel_edges(64)


def test_the_context_of_a_model_is_published_in_frames_and_samples():
    """A model that needs context publishes it, and it is whole frames."""
    library = models.open_library()
    model = library.load("voice_reverb_reduce")
    assert model.manifest.context.frames == 2
    assert model.manifest.context.samples == 2 * HOP
    assert model.manifest.context.samples % HOP == 0
    assert model.manifest.state.scope == "file"
    assert model.manifest.state.cleared == "file"


def test_the_state_of_a_runner_follows_the_manifest():
    """A model that is cleared per block starts every block on its own."""
    library = models.open_library()
    model = library.load("voice_reverb_reduce")
    runner = ModelRunner(model)
    rng = np.random.default_rng(6)
    magnitudes = _frames(rng, count=4)
    runner.process(magnitudes)
    assert runner.frames_read == 2
    runner.reset()
    assert runner.frames_read == 0
