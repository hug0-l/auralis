"""
aurcore.models.train — the builder of the shipped models.

The shipped models are built by this module, and the build is
**reproducible**: the same command gives the same weights and therefore the
same checksum, every time.  The training data is synthetic and the seed is
fixed, so a rebuild does not need a corpus and does not need a network.

    python -m aurcore.models.train --all

What the three models are
-------------------------

``voice_enhance`` (problem *voice*)
    A band curve: the band of the voice is kept, the rumble below it and the
    hiss above it are taken away.  The network reads the band levels of a
    frame and answers a gain per band.  It needs no context.
``voice_reverb_reduce`` (problem *voice_reverb*)
    Reads the band levels of the frame **and** of the two frames before it,
    and answers a gain per band: a frame that is much quieter than the frame
    before it is the tail of a reverberant sound, and the tail is what the
    model takes away.  The context is two frames, and the manifest
    publishes it.
``ambience_match`` (problem *ambience*)
    Answers a **spectrum**: the envelope of the band levels of the frame,
    which is the description of the ambience the signal carries.  It reads
    one frame of context.

These are not trained separators.  They are small, deterministic, honestly
labelled artefacts that prove the mechanism — the format, the loader, the
validation, the engine, the library — and they are the artefacts the
product would replace with real weights.

The training
------------

A plain multi layer perceptron, trained with Adam on the mean square
error.  Everything is NumPy: there is no framework and no dependency.  The
gradient is computed by hand, so the run is reproducible bit for bit.

The feature layout
------------------

The training data is built in the **scale the engine reads**, so that the
weights are fitted on the features the engine hands them at run time.  A
level of a band is a level in **dBFS** (0 dB is a full scale sinusoid in
the transform), and the magnitudes of the synthetic spectra are the ones
the transform of the engine produces: a full scale sinusoid reaches
``frame_size / 4``.  The floor is the manifest's own ``floor_db``
(-100 dBFS) and the ceiling is the ceiling of the layout (+12 dBFS); a
band that carries nothing at all reads 0.0.

The layout of the 1.0.0 models clipped the levels at the top of their
range, so a model could read the shape of a spectrum but not its height,
and the training data was built in a scale the engine never produced.  The
models of this build are version 2.0.0 and are trained on the corrected
layout.

The manifest of a model says which layout its weights were fitted on (the
``feature_layout_version``), because the weights of a model of another
layout have the same shapes as the weights of a model of this one: the
loader could not tell them apart, and a model of the old layout would be
read with the new one and answer nonsense.  The weights of a model of a
retired layout cannot be converted either — the scale they were fitted on
is gone — so such a model is refused and has to be trained again.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .engine import FEATURE_CEILING_DB, SpectralModel
from .format import checksum_of, write_model
from .library import default_library_path
from .manifest import (
    FEATURE_LAYOUT_VERSION,
    ContextSpec,
    Features,
    InputSpec,
    Manifest,
    NetworkSpec,
    OutputSpec,
    StateSpec,
    WeightSpec,
)

__all__ = [
    "mel_edges",
    "train_mlp",
    "build_voice_model",
    "build_voice_reverb_model",
    "build_ambience_model",
    "build_all",
    "main",
]

#: The rate every shipped model runs at.
SAMPLE_RATE = 48000

#: The frame and the hop every shipped model uses.
FRAME_SIZE = 1024
HOP = 256

#: The number of bands every shipped model publishes.
BANDS = 64

#: The floor of the features.
FLOOR_DB = -100.0

#: The date the shipped models were trained.  It is a constant and not the
#: date of the build, so that a rebuild gives the same artefact.
TRAINED_ON = "2026-10-21"

#: The version of the shipped models.  It is **2.0.0** because the feature
#: layout changed: the features carry the level of a band in dBFS instead of
#: a value that was clipped at the top of its range, so the weights of the
#: 1.0.0 models and the engine of this build do not go together.
VERSION = "2.0.0"

#: The seed of the training data.
SEED = 20261021


# ---------------------------------------------------------------------------
# the band layout
# ---------------------------------------------------------------------------
def _hz_to_mel(hz: float) -> float:
    """Convert a frequency in Hz to the mel scale."""
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    """Convert a value of the mel scale to Hz."""
    return 700.0 * (np.power(10.0, mel / 2595.0) - 1.0)


def mel_edges(bands: int, sample_rate: int = SAMPLE_RATE) -> tuple[float, ...]:
    """Return the ``bands + 1`` mel spaced band edges in Hz.

    Parameters
    ----------
    bands : int
        The number of bands.
    sample_rate : int
        The rate the edges belong to.

    Returns
    -------
    tuple of float
        The edges, from 0 Hz to the Nyquist frequency.
    """
    top = _hz_to_mel(sample_rate / 2.0)
    mels = np.linspace(0.0, top, bands + 1)
    edges = [_mel_to_hz(value) for value in mels]
    edges[0] = 0.0
    edges[-1] = sample_rate / 2.0
    return tuple(float(value) for value in edges)


def _band_centres(edges: tuple[float, ...]) -> np.ndarray:
    """The centre frequency of every band."""
    values = np.asarray(edges, dtype=np.float64)
    return 0.5 * (values[:-1] + values[1:])


# ---------------------------------------------------------------------------
# the training
# ---------------------------------------------------------------------------
def _sigmoid(x: np.ndarray) -> np.ndarray:
    """The logistic function."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def _logit(x: np.ndarray) -> np.ndarray:
    """The inverse of the logistic function."""
    clipped = np.clip(np.asarray(x, dtype=np.float64), 1e-6, 1.0 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _forward(weights: dict[str, np.ndarray], x: np.ndarray, layers):
    """Run the network over a batch of inputs."""
    activations = [x]
    current = x
    for index, (_, _, activation) in enumerate(layers):
        current = current @ weights[f"w{index}"] + weights[f"b{index}"]
        if activation == "relu":
            current = np.maximum(current, 0.0)
        activations.append(current)
    return activations, current


def _backward(weights, activations, out, target, layers):
    """The gradient of the mean square error."""
    count = out.shape[0]
    grads: dict[str, np.ndarray] = {}
    delta = 2.0 * (out - target) / float(count)
    for index in range(len(layers) - 1, -1, -1):
        previous = activations[index]
        grads[f"w{index}"] = previous.T @ delta
        grads[f"b{index}"] = delta.sum(axis=0)
        if index > 0:
            mask = activations[index] > 0.0
            delta = (delta @ weights[f"w{index}"].T) * mask
    return grads


def train_mlp(
    features: np.ndarray,
    target: np.ndarray,
    hidden: tuple[int, ...],
    *,
    epochs: int = 2000,
    learning_rate: float = 0.01,
    seed: int = SEED,
):
    """Train a multi layer perceptron with Adam.

    Parameters
    ----------
    features : numpy.ndarray
        The inputs, shape ``(samples, in)``.
    target : numpy.ndarray
        The targets, shape ``(samples, out)``.
    hidden : tuple of int
        The width of every hidden layer.
    epochs : int
        The number of passes over the data.  The epoch count is what the
        manifest publishes as the ``epoch`` of the model.
    learning_rate : float
        The step size of Adam.
    seed : int
        The seed of the initialisation.

    Returns
    -------
    (dict, list)
        The weights and the loss of every 50th epoch.
    """
    samples = np.asarray(features, dtype=np.float64)
    goal = np.asarray(target, dtype=np.float64)
    if samples.shape[0] != goal.shape[0]:
        raise ValueError("the features and the target must have the same number of rows")
    widths = [samples.shape[1]] + list(hidden) + [goal.shape[1]]
    rng = np.random.default_rng(seed)
    weights: dict[str, np.ndarray] = {}
    for index in range(len(widths) - 1):
        span = np.sqrt(2.0 / float(widths[index]))
        weights[f"w{index}"] = rng.uniform(-span, span, size=(widths[index], widths[index + 1]))
        weights[f"b{index}"] = np.zeros(widths[index + 1], dtype=np.float64)
    layers = []
    for index in range(len(widths) - 1):
        activation = "relu" if index < len(widths) - 2 else "none"
        layers.append((widths[index], widths[index + 1], activation))

    moments = {key: np.zeros_like(value) for key, value in weights.items()}
    velocities = {key: np.zeros_like(value) for key, value in weights.items()}
    beta_one = 0.9
    beta_two = 0.999
    epsilon = 1e-8
    losses: list[float] = []
    for epoch in range(1, epochs + 1):
        activations, out = _forward(weights, samples, layers)
        grads = _backward(weights, activations, out, goal, layers)
        for key in weights:
            moments[key] = beta_one * moments[key] + (1.0 - beta_one) * grads[key]
            velocities[key] = beta_two * velocities[key] + (1.0 - beta_two) * grads[key] ** 2
            m_hat = moments[key] / (1.0 - beta_one**epoch)
            v_hat = velocities[key] / (1.0 - beta_two**epoch)
            weights[key] = weights[key] - learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)
        if epoch % 50 == 0 or epoch == epochs:
            loss = float(np.mean((_forward(weights, samples, layers)[1] - goal) ** 2))
            losses.append(loss)
    return weights, losses


# ---------------------------------------------------------------------------
# the synthetic training data
# ---------------------------------------------------------------------------
def _synthetic_spectra(model: SpectralModel, band_levels_db: np.ndarray, rng) -> np.ndarray:
    """Turn a table of band levels into magnitude spectra.

    The levels are **dBFS**: 0 dB is a full scale sinusoid.  The magnitudes
    are the ones the transform of the engine produces, so the reader of the
    engine reads the level that was asked for: the magnitude of a full scale
    sinusoid in that transform is ``frame_size / 4``.

    Parameters
    ----------
    model : SpectralModel
        The model the spectra are made for (it holds the band map).
    band_levels_db : numpy.ndarray
        The level of every band, in dBFS, for every frame.  Shape
        ``(frames, bands)``.
    rng : numpy.random.Generator
        The source of the noise of the bands.

    Returns
    -------
    numpy.ndarray
        The magnitudes, shape ``(bins, frames)``.
    """
    frames, bands = band_levels_db.shape
    jitter = rng.normal(0.0, 2.0, size=(frames, bands))
    smooth = np.zeros_like(jitter)
    for index in range(1, bands - 1):
        smooth[:, index] = (
            0.25 * jitter[:, index - 1] + 0.5 * jitter[:, index] + 0.25 * jitter[:, index + 1]
        )
    levels = band_levels_db + smooth
    linear = model.reference_magnitude * np.power(10.0, levels / 20.0)
    per_bin = linear[:, model.band_of_bin].T
    return np.ascontiguousarray(per_bin)


def _features_of(model: SpectralModel, spectra: np.ndarray) -> np.ndarray:
    """The feature vectors of a set of spectra, one frame per row."""
    frames = spectra.shape[1]
    return np.stack([model.band_levels(spectra[:, index]) for index in range(frames)], axis=0)


def _with_deltas(features: np.ndarray, lags) -> np.ndarray:
    """Add the difference to the frames before, the way the engine does."""
    parts = [features]
    for lag in lags:
        previous = np.roll(features, lag, axis=0)
        previous[:lag] = features[:lag]
        parts.append(features - previous)
    return np.concatenate(parts, axis=1)


def _layout_of(model: SpectralModel) -> tuple[float, float]:
    """The floor and the ceiling of the feature layout of a model.

    Parameters
    ----------
    model : SpectralModel
        The model whose layout is asked for.

    Returns
    -------
    (float, float)
        The floor in dBFS (the manifest's own) and the ceiling in dBFS.
    """
    floor_db = float(model.manifest.input.features.floor_db)
    return floor_db, float(FEATURE_CEILING_DB)


def _voice_training_set(model: SpectralModel, rng, samples: int = 2048):
    """The training set of the voice model: varied spectra, a band curve.

    The levels span the whole layout, from the floor to ten decibels below
    full scale, so that the model has seen an empty band as well as a loud
    one: the curve it answers does not depend on the level, and a model
    that has never seen the floor cannot be sure that the floor is quiet.
    """
    bands = model.manifest.input.features.bands
    levels = rng.uniform(-100.0, -10.0, size=(samples, bands))
    spectra = _synthetic_spectra(model, levels, rng)
    features = _features_of(model, spectra)
    centres = _band_centres(model.manifest.input.features.band_edges_hz)
    # The band shape of a voice: nothing below 60 Hz, everything from 80 Hz
    # to 8 kHz, a cosine taper down to 0.1 at 16 kHz and 0.05 above it.
    curve = np.zeros_like(centres)
    rise = (centres >= 60.0) & (centres < 80.0)
    curve[rise] = 0.5 * (1.0 + np.cos((80.0 - centres[rise]) / 20.0 * np.pi))
    flat = (centres >= 80.0) & (centres <= 8000.0)
    curve[flat] = 1.0
    fall = (centres > 8000.0) & (centres < 16000.0)
    curve[fall] = 0.1 + 0.9 * 0.5 * (1.0 + np.cos((16000.0 - centres[fall]) / 8000.0 * np.pi))
    curve[centres >= 16000.0] = 0.05
    target = np.repeat(curve[None, :], samples, axis=0)
    return features, target


def _reverb_training_set(model: SpectralModel, rng, samples: int = 2048):
    """The training set of the reverb model: bursts with a tail behind them."""
    bands = model.manifest.input.features.bands
    hop_s = HOP / float(SAMPLE_RATE)
    period = int(round(0.2 / hop_s))
    tail_length = 24
    direct = np.zeros(samples, dtype=np.float64)
    for start in range(0, samples, period):
        direct[start] = 1.0
    tail = np.zeros(samples, dtype=np.float64)
    for lag in range(1, tail_length):
        tail[lag:] += 0.55 * np.exp(-lag / 8.0) * direct[:-lag]
    total = direct + tail
    level_db = 20.0 * np.log10(np.maximum(total, 1e-6))
    levels = np.repeat(level_db[:, None], bands, axis=1)
    levels = levels + rng.normal(0.0, 1.0, size=(samples, bands))
    spectra = _synthetic_spectra(model, levels, rng)
    features = _features_of(model, spectra)
    stacked = _with_deltas(features, (1, 2))
    mask = np.clip(direct / np.maximum(total, 1e-6), 0.0, 1.0)
    target = np.repeat(mask[:, None], bands, axis=1)
    return stacked, target


def _ambience_training_set(model: SpectralModel, rng, samples: int = 2048):
    """The training set of the ambience model: the envelope of the levels."""
    bands = model.manifest.input.features.bands
    levels = rng.uniform(-100.0, -5.0, size=(samples, bands))
    spectra = _synthetic_spectra(model, levels, rng)
    features = _features_of(model, spectra)
    stacked = _with_deltas(features, (1,))
    envelope = np.zeros_like(features)
    for index in range(bands):
        low = max(0, index - 2)
        high = min(bands, index + 3)
        envelope[:, index] = features[:, low:high].max(axis=1)
    # The target is a spectrum in dBFS: the envelope of the band levels,
    # mapped back from the scale of the features to the scale of the level.
    floor_db, ceiling_db = _layout_of(model)
    target = envelope * (ceiling_db - floor_db) + floor_db
    return stacked, target


# ---------------------------------------------------------------------------
# the models
# ---------------------------------------------------------------------------
def _layout_note(features: Features) -> str:
    """The sentence that declares the feature layout in a manifest.

    The layout is part of what a model is: a reader that does not know it
    cannot know what the weights of the model mean.  The sentence travels
    with the artefact, in the notes of the manifest.
    """
    return (
        "The features are the levels of the bands in dBFS (0 dB is a full scale "
        f"sinusoid in the transform), mapped from the floor of {features.floor_db:.0f} dBFS "
        f"to the ceiling of {FEATURE_CEILING_DB:.0f} dBFS onto [0, 1]. "
        "A band that carries nothing at all reads 0.0, the quietest value of the "
        "layout, and a band above the ceiling reads 1.0."
    )


def _manifest(
    *,
    name: str,
    task: str,
    problem: str,
    features: Features,
    output: OutputSpec,
    context: ContextSpec,
    state: StateSpec,
    layers,
    weights: dict[str, np.ndarray],
    epoch: int,
    notes: str,
    frame_size: int = FRAME_SIZE,
    hop: int = HOP,
    version: str = VERSION,
    variant: str = "small",
) -> Manifest:
    """Assemble the manifest of a model."""
    # The kind of the network follows the shape of its layers: three
    # values per layer is the fully connected kind, five is the sequence
    # kind (the kernel and the dilation come with it).
    kind = "sequence" if layers and len(layers[0]) == 5 else "mlp"
    weight_specs = []
    for index, layer in enumerate(layers):
        if kind == "sequence":
            width_in, width_out, kernel, _, _ = layer
            shape = (width_in, width_out, kernel)
        else:
            width_in, width_out, _ = layer
            shape = (width_in, width_out)
        weight_specs.append(
            WeightSpec(
                name=f"w{index}",
                shape=shape,
                dtype="float32",
                key=f"w{index}",
            )
        )
        weight_specs.append(
            WeightSpec(
                name=f"b{index}",
                shape=(width_out,),
                dtype="float32",
                key=f"b{index}",
            )
        )
    parameters = sum(spec.size for spec in weight_specs)
    network = NetworkSpec(kind=kind, layers=tuple(layers), parameters=parameters)
    stored = {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}
    return Manifest(
        name=name,
        version=version,
        epoch=epoch,
        trained_on=TRAINED_ON,
        variant=variant,
        problem=problem,
        task=task,
        input=InputSpec(
            sample_rate=SAMPLE_RATE,
            channels=1,
            frame_size=frame_size,
            hop=hop,
            features=features,
        ),
        output=output,
        context=context,
        state=state,
        network=network,
        weights=tuple(weight_specs),
        checksum=checksum_of(stored),
        deterministic=True,
        notes=notes,
        feature_layout_version=FEATURE_LAYOUT_VERSION,
    )


def _skeleton(
    features, layers, output, context, state, frame_size: int = FRAME_SIZE, hop: int = HOP
) -> SpectralModel:
    """Build a model with empty weights, to read its band map."""
    weights = {}
    for index, layer in enumerate(layers):
        if len(layer) == 5:
            size_in, size_out, kernel, _, _ = layer
            weights[f"w{index}"] = np.zeros((size_in, size_out, kernel), dtype=np.float32)
        else:
            size_in, size_out, _ = layer
            weights[f"w{index}"] = np.zeros((size_in, size_out), dtype=np.float32)
        weights[f"b{index}"] = np.zeros(layer[1], dtype=np.float32)
    manifest = _manifest(
        name="skeleton",
        task="skeleton",
        problem="voice",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=0,
        notes="skeleton",
        frame_size=frame_size,
        hop=hop,
    )
    return SpectralModel(manifest, weights)


def build_voice_model(epochs: int = 2000, seed: int = SEED):
    """Build the ``voice_enhance`` model.

    Returns
    -------
    (Manifest, dict, list)
        The manifest, the weights and the loss curve.
    """
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=0
    )
    output = OutputSpec(
        kind="spectral_mask", bins=FRAME_SIZE // 2 + 1, range=(0.0, 1.0), transfer="sigmoid"
    )
    context = ContextSpec(frames=0, samples=0)
    state = StateSpec(scope="block", cleared="block")
    layers = ((BANDS, 48, "relu"), (48, 32, "relu"), (32, BANDS, "none"))
    skeleton = _skeleton(features, layers, output, context, state)
    rng = np.random.default_rng(seed)
    inputs, target = _voice_training_set(skeleton, rng)
    goal = _logit(target)
    weights, losses = train_mlp(inputs, goal, (48, 32), epochs=epochs, seed=seed)
    manifest = _manifest(
        name="voice_enhance",
        task="Keeps the band of the voice and takes the rumble and the hiss away.",
        problem="voice",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes=(
            "Calibrated band curve, trained on synthetic spectra. Not a trained "
            "separator: the weights carry the band shape of the voice and nothing else. "
            + _layout_note(features)
        ),
    )
    return manifest, weights, losses


def build_voice_reverb_model(epochs: int = 2000, seed: int = SEED + 1):
    """Build the ``voice_reverb_reduce`` model.

    Returns
    -------
    (Manifest, dict, list)
        The manifest, the weights and the loss curve.
    """
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=2
    )
    output = OutputSpec(
        kind="spectral_mask", bins=FRAME_SIZE // 2 + 1, range=(0.0, 1.0), transfer="sigmoid"
    )
    context = ContextSpec(frames=2, samples=2 * HOP)
    state = StateSpec(scope="file", cleared="file")
    layers = ((BANDS * 3, 48, "relu"), (48, 32, "relu"), (32, BANDS, "none"))
    skeleton = _skeleton(features, layers, output, context, state)
    rng = np.random.default_rng(seed)
    inputs, target = _reverb_training_set(skeleton, rng)
    goal = _logit(target)
    weights, losses = train_mlp(inputs, goal, (48, 32), epochs=epochs, seed=seed)
    manifest = _manifest(
        name="voice_reverb_reduce",
        task="Takes the reverberant tail of a voice away.",
        problem="voice_reverb",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes=(
            "Reads two frames of context: a frame that is much quieter than the frame "
            "before it is the tail of a reverberant sound. Trained on synthetic bursts. "
            + _layout_note(features)
        ),
    )
    return manifest, weights, losses


def build_ambience_model(epochs: int = 2000, seed: int = SEED + 2):
    """Build the ``ambience_match`` model.

    Returns
    -------
    (Manifest, dict, list)
        The manifest, the weights and the loss curve.
    """
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=1
    )
    output = OutputSpec(
        kind="spectrum", bins=FRAME_SIZE // 2 + 1, range=(FLOOR_DB, FEATURE_CEILING_DB),
        transfer="linear",
    )
    context = ContextSpec(frames=1, samples=HOP)
    state = StateSpec(scope="session", cleared="session")
    layers = ((BANDS * 2, 48, "relu"), (48, 32, "relu"), (32, BANDS, "none"))
    skeleton = _skeleton(features, layers, output, context, state)
    rng = np.random.default_rng(seed)
    inputs, target = _ambience_training_set(skeleton, rng)
    weights, losses = train_mlp(inputs, target, (48, 32), epochs=epochs, seed=seed)
    manifest = _manifest(
        name="ambience_match",
        task="Describes the ambience of a frame as a spectrum.",
        problem="ambience",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes=(
            "Answers the envelope of the band levels, which is the description of the "
            "ambience. Trained on synthetic spectra. The spectrum it produces is in "
            "dBFS, the scale the features are read in. "
            + _layout_note(features)
        ),
    )
    return manifest, weights, losses


# ---------------------------------------------------------------------------
# the per sample model
# ---------------------------------------------------------------------------
#: The frame of the per sample model, in samples.  128 samples are 2.7 ms at
#: 48 kHz: short enough that the decision can follow a transient, long enough
#: that a band still holds a few bins.
SAMPLE_FRAME = 128

#: The hop of the per sample model, in samples.  **One sample**: the model is
#: asked once per sample of the audio, which is what the real time path
#: cannot afford and what the offline path can.
SAMPLE_HOP = 1

#: The name of the per sample model.
SAMPLE_MODEL = "voice_sample_mask"

#: The version of the per sample model.  It is **below** the version of the
#: frame rate voice models on purpose: a model that is not chosen by name
#: must not replace the one that runs today.
SAMPLE_VERSION = "1.0.0"


def _voice_curve(centres: np.ndarray) -> np.ndarray:
    """The band shape of a voice: nothing below 60 Hz, everything from 80 Hz
    to 8 kHz, a cosine taper down to 0.1 at 16 kHz and 0.05 above it."""
    curve = np.zeros_like(centres)
    rise = (centres >= 60.0) & (centres < 80.0)
    curve[rise] = 0.5 * (1.0 + np.cos((80.0 - centres[rise]) / 20.0 * np.pi))
    flat = (centres >= 80.0) & (centres <= 8000.0)
    curve[flat] = 1.0
    fall = (centres > 8000.0) & (centres < 16000.0)
    curve[fall] = 0.1 + 0.9 * 0.5 * (1.0 + np.cos((16000.0 - centres[fall]) / 8000.0 * np.pi))
    curve[centres >= 16000.0] = 0.05
    return curve


def _sample_training_set(model: SpectralModel, rng, samples: int = 4096):
    """The training set of the per sample model: a voice over a noise floor.

    The frames are **consecutive**: the level of a band moves from frame to
    frame, so the difference the model reads is the slope of the material
    and not a number that was drawn on its own.  The wanted content is a
    voice that comes and goes in syllables, the noise is a hiss with a
    rumble under it, and the target is the share of the wanted content in
    every band of every frame.

    The target is the share that the levels carry, and not a cleaner one:
    a model that is trained on a share it cannot see would learn to guess.

    Parameters
    ----------
    model : SpectralModel
        The model the set is made for.
    rng : numpy.random.Generator
        The source of the noise.
    samples : int
        The number of frames.

    Returns
    -------
    (numpy.ndarray, numpy.ndarray)
        The features and the target mask, both ``(frames, bands)``.
    """
    bands = model.manifest.input.features.bands
    centres = _band_centres(model.manifest.input.features.band_edges_hz)
    curve = _voice_curve(centres)

    # the voice: the band shape, a level that moves with the syllables, and
    # the silence between them
    envelope = np.zeros(samples, dtype=np.float64)
    position = 0
    while position < samples:
        length = int(rng.integers(40, 160))
        envelope[position : position + length] = 1.0
        position += length + int(rng.integers(20, 90))
    voice_db = -20.0 + rng.normal(0.0, 1.5, size=samples)
    wanted = voice_db[:, None] + 20.0 * np.log10(np.maximum(curve, 1e-6))[None, :]
    wanted = np.where(envelope[:, None] > 0.0, wanted, FLOOR_DB)

    # the noise: a hiss over everything and a rumble under the voice
    hiss_db = -45.0
    rumble_db = np.where(centres < 200.0, -32.0, hiss_db)
    noise = rng.normal(0.0, 1.0, size=(samples, bands)) + rumble_db[None, :]

    observed = np.maximum(wanted, noise)
    spectra = _synthetic_spectra(model, observed, rng)
    features = _features_of(model, spectra)
    stacked = _with_deltas(features, (1,))
    share = np.clip(
        np.power(10.0, (wanted - observed) / 20.0),
        0.0,
        1.0,
    )
    return stacked, share


def build_sample_model(epochs: int = 2000, seed: int = SEED + 3):
    """Build the ``voice_sample_mask`` model: a mask decided every sample.

    The model reads a frame of 128 samples every **sample**, so it is asked
    48 000 times a second.  That is far outside the budget of the real time
    path and inside the budget of the offline path, which is why this model
    belongs to the offline path: it is the one capability of the product
    that is only affordable when there is no deadline.

    Returns
    -------
    (Manifest, dict, list)
        The manifest, the weights and the loss curve.
    """
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=1
    )
    output = OutputSpec(
        kind="spectral_mask",
        bins=SAMPLE_FRAME // 2 + 1,
        range=(0.0, 1.0),
        transfer="sigmoid",
    )
    context = ContextSpec(frames=1, samples=SAMPLE_HOP)
    state = StateSpec(scope="file", cleared="file")
    layers = ((BANDS * 2, 32, "relu"), (32, 24, "relu"), (24, BANDS, "none"))
    skeleton = _skeleton(
        features, layers, output, context, state, frame_size=SAMPLE_FRAME, hop=SAMPLE_HOP
    )
    rng = np.random.default_rng(seed)
    inputs, target = _sample_training_set(skeleton, rng)
    goal = _logit(target)
    weights, losses = train_mlp(inputs, goal, (32, 24), epochs=epochs, seed=seed)
    manifest = _manifest(
        name=SAMPLE_MODEL,
        task=(
            "Decides, once per sample, how much of every band to keep of a voice "
            "over a noise floor."
        ),
        problem="voice",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes=(
            "The per sample model: a frame of 128 samples, a hop of one sample, so "
            "the decision can follow a transient instead of being smeared over a "
            "frame. The price is the estimate: a band of a 2.7 ms window holds few "
            "bins, so the mask it decides is noisier than the mask of a frame rate "
            "model. It is an offline capability and is chosen by name. "
            + _layout_note(features)
        ),
        frame_size=SAMPLE_FRAME,
        hop=SAMPLE_HOP,
        version=SAMPLE_VERSION,
        variant="sample",
    )
    return manifest, weights, losses


# ---------------------------------------------------------------------------
# the model with a memory
# ---------------------------------------------------------------------------
#: The name of the model with a memory.
MEMORY_MODEL = "voice_sample_memory"

#: The version of the model with a memory.  It is **below** the version of
#: the frame rate voice models on purpose, like the per sample model: a
#: model that is not chosen by name must not replace the one that runs
#: today.
MEMORY_VERSION = "1.0.0"

#: The stack of the model with a memory: ``(in, out, kernel, dilation)``.
#:
#: The **dilation** is what makes the memory affordable.  A kernel of three
#: taps that reaches 256 frames back holds as much of the past as a kernel
#: of 256 taps would, and it costs a fraction of the parameters.
MEMORY_LAYERS = (
    (BANDS, 16, 3, 1),
    (16, 16, 3, 16),
    (16, BANDS, 3, 256),
)

#: The number of frames the stack of the memory model reads before a frame.
MEMORY_SPAN = 1 + sum((kernel - 1) * dilation for _, _, kernel, dilation in MEMORY_LAYERS)


def _forward_conv(weights, x, layers):
    """Run a stack of causal convolutions over a set of consecutive frames.

    The convolution is **causal**: the answer for a frame is a function of
    the frames before it and of nothing else.  The frames before the first
    one read zero, which is what the engine reads as well.

    Parameters
    ----------
    weights : dict
        The weights of the layers: ``w0``, ``b0``, ``w1``, ``b1``, ...
    x : numpy.ndarray
        The inputs, shape ``(frames, in)``.
    layers : tuple
        ``(in, out, kernel, dilation, activation)`` per layer.

    Returns
    -------
    (list, numpy.ndarray)
        The input of every layer and the output of the last one.
    """
    activations = [x]
    current = x
    frames = x.shape[0]
    for index, (_, _, kernel, dilation, activation) in enumerate(layers):
        pad = (kernel - 1) * dilation
        padded = np.concatenate([np.zeros((pad, current.shape[1])), current], axis=0)
        out = np.repeat(weights[f"b{index}"][None, :], frames, axis=0)
        for channel in range(current.shape[1]):
            for tap in range(kernel):
                back = tap * dilation
                shifted = padded[pad - back : pad - back + frames, channel]
                out += shifted[:, None] * weights[f"w{index}"][channel, :, tap][None, :]
        if activation == "relu":
            np.maximum(out, 0.0, out=out)
        activations.append(out)
        current = out
    return activations, current


def _backward_conv(weights, activations, out, target, layers):
    """The gradient of the mean square error of a stack of convolutions.

    The gradient of a convolution is the convolution of the other way
    round: the gradient of a weight is the input of the layer against the
    gradient of the output, and the gradient of the input is the weight
    against the gradient of the output, the other way round and one frame
    later.
    """
    count = out.shape[0]
    grads: dict[str, np.ndarray] = {}
    delta = 2.0 * (out - target) / float(count)
    for index in range(len(layers) - 1, -1, -1):
        _, _, kernel, dilation, activation = layers[index]
        previous = activations[index]
        frames = previous.shape[0]
        if activation == "relu":
            # The relu of the layer sits between its pre-activation and
            # its output: the gradient of the pre-activation is the
            # gradient of the output where the layer passed the signal.
            delta = delta * (activations[index + 1] > 0.0)
        grad_w = np.zeros_like(weights[f"w{index}"])
        grad_input = np.zeros_like(previous)
        for tap in range(kernel):
            back = tap * dilation
            usable = max(frames - back, 0)
            if back:
                # The weight reads the past, so its gradient is the
                # input shifted **back**; the input is read by the
                # frames ahead, so its gradient is the output shifted
                # **forward**.  The two shifts are not the same.
                source = np.zeros((frames, previous.shape[1]), dtype=np.float64)
                if usable:
                    source[back:] = previous[:usable]
                shifted = np.zeros_like(delta)
                if usable:
                    shifted[:usable] = delta[back:]
            else:
                source = previous
                shifted = delta
            grad_w[:, :, tap] = source.T @ delta
            grad_input += shifted @ weights[f"w{index}"][:, :, tap].T
        grads[f"w{index}"] = grad_w
        grads[f"b{index}"] = delta.sum(axis=0)
        if index > 0:
            delta = grad_input
    return grads


def train_sequence(
    features: np.ndarray,
    target: np.ndarray,
    layers: tuple[tuple[int, int, int, int, str], ...],
    *,
    epochs: int = 800,
    learning_rate: float = 0.01,
    seed: int = SEED,
):
    """Train a stack of causal convolutions with Adam.

    The training set has to be a set of **consecutive** frames: the answer
    of a frame depends on the frames before it, so a set of frames that
    were drawn on their own would teach the network nothing about the
    memory it is there for.

    Parameters
    ----------
    features : numpy.ndarray
        The inputs, shape ``(frames, in)``.
    target : numpy.ndarray
        The targets, shape ``(frames, out)``.
    layers : tuple
        ``(in, out, kernel, dilation, activation)`` per layer.
    epochs : int
        The number of passes over the data.  The epoch count is what the
        manifest publishes as the ``epoch`` of the model.
    learning_rate : float
        The step size of Adam.
    seed : int
        The seed of the initialisation.

    Returns
    -------
    (dict, list)
        The weights and the loss of every 50th epoch.
    """
    samples = np.asarray(features, dtype=np.float64)
    goal = np.asarray(target, dtype=np.float64)
    if samples.shape[0] != goal.shape[0]:
        raise ValueError("the features and the target must have the same number of rows")
    rng = np.random.default_rng(seed)
    weights: dict[str, np.ndarray] = {}
    for index, (width_in, width_out, kernel, _, _) in enumerate(layers):
        span = np.sqrt(2.0 / float(width_in))
        weights[f"w{index}"] = rng.uniform(
            -span, span, size=(width_in, width_out, kernel)
        )
        weights[f"b{index}"] = np.zeros(width_out, dtype=np.float64)
    moments = {key: np.zeros_like(value) for key, value in weights.items()}
    velocities = {key: np.zeros_like(value) for key, value in weights.items()}
    beta_one = 0.9
    beta_two = 0.999
    epsilon = 1e-8
    losses: list[float] = []
    for epoch in range(1, epochs + 1):
        activations, out = _forward_conv(weights, samples, layers)
        grads = _backward_conv(weights, activations, out, goal, layers)
        for key in weights:
            moments[key] = beta_one * moments[key] + (1.0 - beta_one) * grads[key]
            velocities[key] = beta_two * velocities[key] + (1.0 - beta_two) * grads[key] ** 2
            m_hat = moments[key] / (1.0 - beta_one**epoch)
            v_hat = velocities[key] / (1.0 - beta_two**epoch)
            weights[key] = weights[key] - learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)
        if epoch % 50 == 0 or epoch == epochs:
            loss = float(np.mean((_forward_conv(weights, samples, layers)[1] - goal) ** 2))
            losses.append(loss)
    return weights, losses


def _memory_training_set(model: SpectralModel, rng, samples: int = 16384):
    """The training set of the model with a memory.

    The difference with the per sample training set is the **time scale**:
    the syllables of the voice last tens of milliseconds and not a handful
    of samples.  A model that is there to remember the material has to be
    taught a material whose rhythm is the rhythm of the material — a
    memory of eleven milliseconds cannot learn anything from syllables
    that last one.

    Parameters
    ----------
    model : SpectralModel
        The model the set is made for.
    rng : numpy.random.Generator
        The source of the noise.
    samples : int
        The number of frames.

    Returns
    -------
    (numpy.ndarray, numpy.ndarray)
        The levels of every band of every frame and the target mask, both
        ``(frames, bands)``.
    """
    bands = model.manifest.input.features.bands
    centres = _band_centres(model.manifest.input.features.band_edges_hz)
    curve = _voice_curve(centres)

    # the voice: the band shape, a level that moves with the syllables, and
    # the silence between them.  At 48 kHz a syllable of 60 to 250 ms is
    # 2 880 to 12 000 samples.
    envelope = np.zeros(samples, dtype=np.float64)
    position = 0
    while position < samples:
        length = int(rng.integers(2880, 12000))
        envelope[position : position + length] = 1.0
        position += length + int(rng.integers(960, 4800))
    voice_db = -20.0 + rng.normal(0.0, 1.5, size=samples)
    wanted = voice_db[:, None] + 20.0 * np.log10(np.maximum(curve, 1e-6))[None, :]
    wanted = np.where(envelope[:, None] > 0.0, wanted, FLOOR_DB)

    # the noise: a hiss over everything and a rumble under the voice
    hiss_db = -45.0
    rumble_db = np.where(centres < 200.0, -32.0, hiss_db)
    noise = rng.normal(0.0, 1.0, size=(samples, bands)) + rumble_db[None, :]

    observed = np.maximum(wanted, noise)
    spectra = _synthetic_spectra(model, observed, rng)
    levels = _features_of(model, spectra)
    share = np.clip(
        np.power(10.0, (wanted - observed) / 20.0),
        0.0,
        1.0,
    )
    return levels, share


def build_sample_memory_model(epochs: int = 800, seed: int = SEED + 4):
    """Build the ``voice_sample_memory`` model: a mask that remembers.

    The model is the per sample model with a **stack of causal
    convolutions over the time axis**: the answer for a sample is a
    function of the samples before it.  That memory is the property the
    first study's network had and the per sample model of ours does not.

    The stack reaches 546 frames — 11 ms at 48 kHz — and it costs a few
    thousand parameters, because the dilation of the layers is what
    stretches the reach of a kernel.

    Returns
    -------
    (Manifest, dict, list)
        The manifest, the weights and the loss curve.
    """
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=0
    )
    output = OutputSpec(
        kind="spectral_mask",
        bins=SAMPLE_FRAME // 2 + 1,
        range=(0.0, 1.0),
        transfer="sigmoid",
    )
    layers = tuple(
        (
            width_in,
            width_out,
            kernel,
            dilation,
            "relu" if index < len(MEMORY_LAYERS) - 1 else "none",
        )
        for index, (width_in, width_out, kernel, dilation) in enumerate(MEMORY_LAYERS)
    )
    context = ContextSpec(frames=MEMORY_SPAN - 1, samples=(MEMORY_SPAN - 1) * SAMPLE_HOP)
    state = StateSpec(scope="file", cleared="file")
    skeleton = _skeleton(
        features, layers, output, context, state, frame_size=SAMPLE_FRAME, hop=SAMPLE_HOP
    )
    rng = np.random.default_rng(seed)
    inputs, target = _memory_training_set(skeleton, rng)
    goal = _logit(target)
    weights, losses = train_sequence(inputs, goal, layers, epochs=epochs, seed=seed)
    manifest = _manifest(
        name=MEMORY_MODEL,
        task=(
            "Decides, once per sample, how much of every band to keep of a voice "
            "over a noise floor, with the frames before the sample in view."
        ),
        problem="voice",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes=(
            "The model with a memory: a stack of causal convolutions over the time "
            "axis, so the answer for a sample is a function of the samples before "
            f"it. The stack reaches {MEMORY_SPAN - 1} frames — "
            f"{(MEMORY_SPAN - 1) * 1000.0 / SAMPLE_RATE:.1f} ms at 48 kHz — and the "
            "dilation of the layers is what makes that reach affordable. It is an "
            "offline capability and is chosen by name. " + _layout_note(features)
        ),
        frame_size=SAMPLE_FRAME,
        hop=SAMPLE_HOP,
        version=MEMORY_VERSION,
        variant="sample",
    )
    return manifest, weights, losses


def build_all(directory=None, epochs: int = 2000, verbose: bool = True):
    """Build and write the shipped models.

    Parameters
    ----------
    directory : str or pathlib.Path, optional
        Where the models are written.  The shipped library is used without
        it.
    epochs : int
        The number of training epochs per model.
    verbose : bool
        Whether the build reports what it does.

    Returns
    -------
    list of pathlib.Path
        The files that were written.
    """
    target = Path(directory) if directory is not None else default_library_path()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    builders = (
        ("voice_enhance", build_voice_model),
        ("voice_reverb_reduce", build_voice_reverb_model),
        ("ambience_match", build_ambience_model),
        (SAMPLE_MODEL, build_sample_model),
        (MEMORY_MODEL, build_sample_memory_model),
    )
    for name, builder in builders:
        manifest, weights, losses = builder(epochs=epochs)
        stored = {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}
        path = write_model(target / f"{name}.aurm", manifest, stored)
        written.append(path)
        if verbose:
            size = path.stat().st_size
            print(
                f"{name:<22} epoch {manifest.epoch:>4}  parameters "
                f"{manifest.parameters:>7}  loss {losses[0]:.6f} -> {losses[-1]:.6f}  "
                f"{size:>7} bytes  {manifest.checksum[:23]}..."
            )
    return written


def main(argv: list[str] | None = None) -> int:
    """Build the shipped models."""
    parser = argparse.ArgumentParser(
        prog="aurcore.models.train",
        description="Build the shipped models of the model library.",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=None,
        help="where the models are written (default: the shipped library)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=2000,
        help="the number of training epochs per model (default: 2000)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="build every model (the default)",
    )
    args = parser.parse_args(argv)
    build_all(directory=args.directory, epochs=args.epochs)
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entry point
    raise SystemExit(main())
