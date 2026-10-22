"""
tests.test_trained_model - the gate of the learned spectral mask model.

The gate checks the things a model artefact has to be true of:

* it **loads** through the loader of the library (``aurcore.models``);
* its **manifest carries its provenance**: the name, the version, the epoch,
  the date it was trained, the variant, the problem it solves, what it
  expects, what it produces, how much history it reads, how long its state
  lives and the checksum of its own weights;
* the **checksum** of the weights is the one the manifest publishes;
* the model is **deterministic**: the same input gives the same output, bit
  for bit;
* the **training** is deterministic: the same data gives the same weights,
  and therefore the same checksum, every time;
* it was **trained on the layout the engine reads**: the features carry the
  level of a band in dBFS, and a band that carries nothing at all reads as
  empty;
* it **declares the version of the feature layout** its weights were fitted
  on, so that a model of an older build is refused and not read with the
  wrong scale;
* it does its **job** on material it has never seen: the hiss goes away and
  the voice stays.

Run it with::

    python -m pytest tests/test_trained_model.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aurcore import models  # noqa: E402
from aurcore.models.format import checksum_of, read_weights  # noqa: E402

#: The name the model publishes.
MODEL_NAME = "voice_denoise_mask"

#: The directory the training code lives in.
TRAINING_DIR = REPO_ROOT / "var" / "model_training"


def _load(name: str, path: Path):
    """Load a module of the repository by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def model():
    """The model, loaded through the loader of the library."""
    library = models.open_library()
    return library.load(MODEL_NAME, sample_rate=48000, channels=1)


@pytest.fixture(scope="module")
def harness():
    """The quality harness, read by path."""
    return _load(
        "aur_test_qualityvoice_harness", REPO_ROOT / "qa" / "qualityvoice" / "harness.py"
    )


# ---------------------------------------------------------------------------
# the library
# ---------------------------------------------------------------------------
def test_the_library_lists_the_model():
    """The library lists the model, with its name, its version and its date."""
    library = models.open_library()
    entries = [entry for entry in library.list() if entry.name == MODEL_NAME]
    assert entries, f"the library holds no model called {MODEL_NAME!r}"
    entry = entries[0]
    assert entry.readable, entry.error
    assert entry.version
    assert entry.trained_on
    assert entry.problem == "voice"


def test_the_model_loads_through_the_loader(model):
    """The loader accepts the model and hands back a runnable model."""
    assert model.manifest.name == MODEL_NAME
    assert model.manifest.input.sample_rate == 48000
    assert model.manifest.input.channels == 1
    assert model.manifest.output.kind == "spectral_mask"
    assert model.manifest.output.transfer == "sigmoid"
    assert model.manifest.parameters > 0


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
def test_the_manifest_carries_its_provenance(model):
    """Every field of the format is there, with a value that means something."""
    manifest = model.manifest
    assert manifest.name == MODEL_NAME
    assert manifest.version.count(".") == 2
    assert manifest.epoch > 0
    assert manifest.trained_on == "2026-10-21"
    assert manifest.variant
    assert manifest.problem == "voice"
    assert manifest.task
    assert manifest.deterministic is True
    assert manifest.notes
    assert manifest.input.frame_size >= 64
    assert manifest.input.hop >= 1
    assert manifest.input.hop <= manifest.input.frame_size
    assert manifest.input.features.kind == "log_magnitude_bands"
    assert manifest.input.features.band_edges_hz[0] == 0.0
    assert manifest.input.features.band_edges_hz[-1] == pytest.approx(24000.0)
    assert manifest.output.bins == manifest.input.bins
    assert manifest.output.range == (0.0, 1.0)
    assert manifest.state.scope in ("block", "file", "session")
    assert manifest.state.cleared in ("block", "file", "session")


def test_the_context_is_a_whole_number_of_frames(model):
    """The context the model reads is published, and it adds up."""
    manifest = model.manifest
    assert manifest.context.frames == manifest.input.features.delta_frames
    assert manifest.context.samples == manifest.context.frames * manifest.input.hop
    assert manifest.context.samples >= 0


def test_the_checksum_is_the_checksum_of_the_weights(model):
    """The published checksum is the checksum of the weights in the file."""
    path = models.open_library().directory / f"{MODEL_NAME}.aurm"
    weights = read_weights(path)
    assert checksum_of(weights) == model.manifest.checksum
    assert model.manifest.checksum.startswith("sha256:")
    assert len(model.manifest.checksum) == len("sha256:") + 64


def test_the_artefact_is_small_enough_to_ship():
    """The artefact is a file, and it is small enough to be shipped."""
    path = models.open_library().directory / f"{MODEL_NAME}.aurm"
    assert path.is_file()
    size = path.stat().st_size
    assert 0 < size < 1_000_000, f"the artefact is {size} bytes"


# ---------------------------------------------------------------------------
# the determinism
# ---------------------------------------------------------------------------
def test_the_model_is_deterministic(model, harness):
    """The same input gives the same output, bit for bit."""
    cases = harness.build_cases()
    x = np.asarray(cases["voice_hiss"].x, dtype=np.float64)
    first = np.asarray(models.run_offline(model, x), dtype=np.float64)
    second = np.asarray(models.run_offline(model, x), dtype=np.float64)
    assert np.array_equal(first, second)


def test_the_training_is_deterministic():
    """The same data gives the same weights, and therefore the same checksum.

    The check runs the real training twice, with a small number of epochs, and
    compares the two sets of weights bit for bit, and the two checksums.
    """
    if not (TRAINING_DIR / "train_voice_mask.py").is_file():
        pytest.skip("the training code is not in the tree")
    trainer = _load("aur_test_train_voice_mask", TRAINING_DIR / "train_voice_mask.py")
    material = _load("aur_test_model_material", TRAINING_DIR / "material.py")
    layers = ((trainer.BANDS, 8, "relu"), (8, 8, "relu"), (8, trainer.BANDS, "none"))
    skeleton = trainer._skeleton(layers, 0)
    mixtures = material.training_mixtures()[:4]
    features, targets, pairs, _summary = trainer.build_dataset(
        mixtures, skeleton, 0, frame_stride=8
    )
    first, _losses = trainer.train(features, targets, hidden=(8, 8), epochs=3, pairs=pairs)
    second, _losses = trainer.train(features, targets, hidden=(8, 8), epochs=3, pairs=pairs)
    assert set(first) == set(second)
    for key in first:
        assert np.array_equal(first[key], second[key]), f"{key} differs between two runs"

    # the checksum the manifest publishes is the checksum of the weights, so
    # two runs publish the same checksum
    one = trainer.build_manifest(layers, first, 3, 0, "the first run")
    other = trainer.build_manifest(layers, second, 3, 0, "the second run")
    assert one.checksum == other.checksum
    assert one.checksum == checksum_of(
        {key: np.asarray(value, dtype=np.float32) for key, value in first.items()}
    )


def test_the_model_was_trained_on_the_layout_the_engine_reads():
    """The artefact says which layout it was trained on, and it is the current one.

    The features of the 1.0.0 model were clipped at the top of their range:
    a model could read the shape of a spectrum but not its height.  The
    model of this build was trained again, on the layout the engine reads.
    """
    if not (TRAINING_DIR / "train_voice_mask.py").is_file():
        pytest.skip("the training code is not in the tree")
    trainer = _load("aur_test_train_voice_mask", TRAINING_DIR / "train_voice_mask.py")
    manifest = models.open_library().load(MODEL_NAME).manifest
    assert manifest.version != "1.0.0", "the model of the clipped layout is still shipped"
    assert "dBFS" in manifest.notes
    assert trainer.FEATURE_CEILING_DB > 0.0
    assert manifest.input.features.floor_db < 0.0


def test_the_artefact_declares_the_feature_layout(model):
    """The artefact says which feature layout its weights were fitted on.

    The weights of a model of the old layout have the same shapes as the
    weights of a model of the current one, so the manifest is the only
    place a reader can learn which layout they belong to.  A manifest that
    does not say is refused by the loader.
    """
    assert model.manifest.feature_layout_version == models.FEATURE_LAYOUT_VERSION
    path = models.open_library().directory / f"{MODEL_NAME}.aurm"
    document = models.read_manifest(path)
    assert document["feature_layout_version"] == models.FEATURE_LAYOUT_VERSION


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------
def test_the_model_takes_the_hiss_away_and_leaves_the_voice(model, harness):
    """The model does its job on the case of the harness, which it has not seen.

    The numbers are the rules of ``qa/qualityvoice/rules.py``: the hiss band
    has to lose at least 10 dB (R2) and the voice band may move by at most
    1.5 dB (R1).
    """
    cases = harness.build_cases()
    case = cases["voice_hiss"]
    x = np.asarray(case.x, dtype=np.float64)
    y = np.asarray(models.run_offline(model, x), dtype=np.float64)
    assert y.shape == x.shape
    assert np.all(np.isfinite(y))
    before = harness.band_energy_db(x, *harness.HISS_BAND)
    after = harness.band_energy_db(y, *harness.HISS_BAND)
    assert after - before <= -10.0, f"the hiss band only lost {before - after:.2f} dB"
    before = harness.band_energy_db(x, *harness.VOICE_BAND)
    after = harness.band_energy_db(y, *harness.VOICE_BAND)
    assert abs(after - before) <= 1.5, f"the voice band moved by {after - before:.2f} dB"


def test_the_model_leaves_a_clean_voice_alone(model, harness):
    """A clean voice may not move: the rule R8 of the harness."""
    cases = harness.build_cases()
    x = np.asarray(cases["voice_clean"].x, dtype=np.float64)
    y = np.asarray(models.run_offline(model, x), dtype=np.float64)
    before = harness.band_energy_db(x, *harness.VOICE_BAND)
    after = harness.band_energy_db(y, *harness.VOICE_BAND)
    assert abs(after - before) <= 0.5, f"the voice band moved by {after - before:.2f} dB"


def test_the_model_is_quiet_enough(model, harness):
    """The output may not be more than 12 dB quieter than the input (R7)."""
    cases = harness.build_cases()
    x = np.asarray(cases["voice_hiss"].x, dtype=np.float64)
    y = np.asarray(models.run_offline(model, x), dtype=np.float64)
    reference = float(np.sqrt(np.mean(x * x)))
    level = float(np.sqrt(np.mean(y * y)))
    assert 20.0 * np.log10(max(level, 1e-12) / reference) >= -12.0
