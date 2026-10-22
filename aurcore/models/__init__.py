"""
aurcore.models — learned models as versioned artefacts.

A model is **one file** with two parts: the **weights** and the
**manifest**.  The manifest is the model's self description: it names the
model, and it says what the model expects, what it produces, how much
history it needs, how long its state lives and what its own checksum is.
The weights are NumPy arrays in a file NumPy reads without a dependency the
project does not have.

The library is a **directory**: one file per model, each file named after
what the model does.  The loader lists the directory — the name, the
version, the date, the problem — without reading a single weight.

Where a model belongs
---------------------
The product has two processing modes, ``realtime`` and ``offline``.  The
real time modules are hand written: exact, cheap, deterministic.  They stay
exactly as they are.  A model belongs to the **offline** path, and the seam
(:func:`request`) hands a model out in that mode only.

The format
=========

Container
---------

The file is a NumPy archive (a zip of ``.npy`` members, read by
``numpy.load`` with ``allow_pickle=False``).  It carries:

============= =========================================================
``__manifest__``  the manifest, as the UTF-8 bytes of its JSON document
``w0``, ``b0``     the matrix and the bias of layer 0
``w1``, ``b1``     the matrix and the bias of layer 1
...               one pair per layer
============= =========================================================

Every array is ``float32``.  A matrix has the shape ``(in, out)``, a bias
the shape ``(out,)``.

The checksum
------------

``checksum`` is ``sha256:`` followed by 64 hex digits, taken over the
**content** of the weights and not over the bytes of the file: for every
array, in the order of the names, the name, the data type, the shape and
the raw values are hashed.  The checksum therefore does not depend on the
order the arrays were written in, and it does not change when the archive
is rewritten.

The manifest
------------

The manifest is a JSON object.  Every field is listed here with its
meaning; a field that is missing, that carries the wrong type or that
carries a value outside its enumeration is a **refusal**, and so is a field
the format does not define.

``format`` (string)
    ``"auralis-model"``.  A file that does not carry it is not a model.
``format_version`` (integer)
    The version of the format.  A manifest that announces a version newer
    than the build understands is refused: the meaning of a field may have
    changed.
``feature_layout_version`` (integer)
    **The version of the feature layout the weights were trained on.**  The
    layout is the scale the features are read in: the reference the level
    of a band is measured against, the floor below which a band counts as
    empty, the ceiling above which it is clipped, and what a band that
    carries nothing at all reads.

    The weights of a model are fitted on the layout of their training, and
    two models of different layouts have the same weight shapes, so
    nothing in the weights tells the two apart.  Without this field the
    loader would read an old artefact with the layout of this build, and
    answer nonsense.  ``1`` is the layout of this build: the level of a
    band in dBFS, 0 dB being a full scale sinusoid in the transform, the
    floor at -100 dBFS, the ceiling at +12 dBFS, and a band that carries
    nothing at all reading as the quietest value of the layout.  A manifest
    that names a version this build does not know is refused, and so is a
    manifest that names no version at all: that is a model of a build that
    did not carry the field.

    ``0`` is the layout the 1.0.0 models were fitted on: the level of a band
    in the magnitude of the raw transform, whose zero point is -48 dBFS for
    a 1024 sample frame, and whose empty band read as the *loudest* value of
    the scale.  It is refused by name, because the weights of a model of
    that layout cannot be read with the layout of this build: the scale they
    were fitted on is gone, and the model has to be trained again.
``name`` (string)
    **The name the model publishes.**  It is also the name of the file in
    the library, so a model is loaded by name at instance creation.  It
    holds letters, digits, ``_``, ``-`` and ``.``.
``version`` (string)
    The version of the model, ``major.minor.patch``.  It is the version of
    the *model*, not of the format.
``epoch`` (integer)
    **The epoch the model was trained for** — the number of passes over
    the training data the training ended at.
``trained_on`` (string)
    **The date the model was trained**, ``YYYY-MM-DD``.
``variant`` (string)
    **The variant** inside the family: which build of the same model this
    is (``"base"``, ``"small"``, ``"large"``, ...).
``problem`` (string)
    **What problem the model solves.**  One of ``"voice"``,
    ``"voice_reverb"`` or ``"ambience"`` — the three problems the product
    is organised around.
``task`` (string)
    What the model does, in one sentence.
``input`` (object)
    **What the model expects to be fed.**  See below.
``output`` (object)
    **What the model produces.**  See below.
``context`` (object)
    **How much history the model needs.**  See below.
``state`` (object)
    **The scope of the state the model carries.**  See below.
``network`` (object)
    The shape of the network the weights belong to.
``weights`` (array of objects)
    One entry per array of the weights: ``name``, ``shape``, ``dtype`` and
    ``key``.  The names are the ones the network needs, in order:
    ``w0, b0, w1, b1, ...``.
``checksum`` (string)
    **The checksum of the weights**, as described above.
``deterministic`` (boolean)
    Whether the model promises that the same input gives the same output.
``notes`` (string)
    Free text: what the model was trained on, what it is good for, what
    it is not.

``input``
---------

``sample_rate`` (integer)
    **The rate the model was trained at.**  A model does not run at another
    rate: the loader refuses it rather than resampling behind the caller's
    back.
``channels`` (integer)
    The number of channels the model reads at once.  ``1`` means the model
    runs on one channel at a time, which is what a stereo engine needs.
``frame_size`` (integer)
    **The length of the frame in samples** the model reads.  A power of two.
``hop`` (integer)
    **The distance between two frames in samples.**
``features`` (object)
    **The features the model publishes** — how it reads a frame.

    ``kind`` (string): ``"log_magnitude_bands"`` — the magnitude spectrum
    of the frame, in dBFS, averaged over the bands.  **0 dB is a full scale
    sinusoid** in the transform of the frame, so a band at -3 dBFS reads
    -3 dB and a band at -40 dBFS reads -40 dB: the level of a band is
    carried, and not only the shape of the spectrum.  The level of a band
    is mapped from the floor onto the ceiling of the layout, and the value
    the network reads is between 0 and 1.
    ``band_edges_hz`` (array of numbers): the ``K + 1`` band edges in Hz.
    The model publishes its own bands, so two models with different bands
    are both read correctly.
    ``floor_db`` (number): the level below which a bin counts as silent.
    A band that carries nothing at all — no bin of the frame, or no
    energy in it — reads the floor, which is the **quietest** value of the
    layout.
    ``delta_frames`` (integer): how many frames of history the features
    read.  ``0`` means the features of one frame only; ``2`` means the
    features of the frame and the difference to the two frames before it.

``output``
----------

``kind`` (string)
    **What the model produces.**  ``"spectral_mask"``: a gain per
    frequency bin that is applied to the magnitude.  ``"spectrum"``: a
    spectrum the model produces, e.g. the ambience a signal should have.
``bins`` (integer)
    The number of bins the output covers.  It must be the number of bins
    of the frame.
``range`` (array of two numbers)
    The range of the produced values.
``transfer`` (string)
    ``"sigmoid"`` turns the raw output of the network into a value between
    0 and 1; ``"linear"`` leaves it alone.  A mask needs ``"sigmoid"``.

``context``
-----------

``frames`` (integer)
    **How many frames of history the model reads** before it can answer.
``samples`` (integer)
    The same in samples.  It must be exactly ``frames * hop``: a context
    that is not a whole number of frames is a refusal, because a context
    that is not whole frames cannot be placed in time.

The context is the latency of the model.  A model that needs context must
publish it.

``state``
---------

``scope`` (string)
    **The unit the state belongs to**: ``"block"``, ``"file"`` or
    ``"session"``.
``cleared`` (string)
    **When the state is dropped**: ``"block"``, ``"file"`` or
    ``"session"``.  It may not be coarser than the scope: a state whose
    scope is ``file`` may not live until the end of the session.

``network``
----------

``kind`` (string)
    ``"mlp"``: a stack of fully connected layers.
``layers`` (array of objects)
    ``in``, ``out`` and ``activation`` (``"relu"`` or ``"none"``) per layer.
    The first layer reads as many values as the features produce, the last
    layer writes one value per band.
``parameters`` (integer)
    The number of values the weights hold.  It must add up.

The refusals
============

The loader refuses a model that does not match, and says what is wrong.

``ManifestError``
    The manifest is malformed, incomplete, carries an unknown field, or
    describes something the engine cannot run: a context that is not a
    whole number of frames, a state that outlives its scope, a feature list
    that does not reach the Nyquist frequency, a network that does not
    match its own weights, a feature layout this build does not read.
``WeightsMismatchError``
    The manifest does not match the weights: a declared array is not in the
    file, or it is there with another shape or another type.
``ChecksumError``
    The weights are not the weights the manifest describes.
``ConfigMismatchError``
    The model does not run at the configuration the engine asked for: the
    sample rate, or the number of channels the model reads at once.
``ModelNotFoundError``
    No file of the library carries that name.

Determinism
===========

The engine is deterministic by construction: the weights are ``float32``,
the arithmetic is ``float64``, a dense layer adds the contributions of its
inputs **in the order of the input index** (no reduction library decides
the order), the bands are reduced with integer counters, and nothing reads
the clock, the random generator or the environment.  The same input gives
the same output, bit for bit, every time.

Usage
=====

    from aurcore import models

    library = models.open_library()          # the shipped library
    for entry in library.list():             # without reading the weights
        print(entry.name, entry.version, entry.trained_on, entry.problem)

    binding = models.request(module, "voice")   # a model, or nothing
    if binding is None:
        ...  # the module does what it does today

The modules of the product do not use this yet: the seam is here, the
modules keep their hand written path, and the models are used where they
are affordable.
"""
from __future__ import annotations

import numpy as np

from .engine import (
    ModelOutput,
    ModelRunner,
    SpectralModel,
    run_offline,
    run_offline_mix,
)
from .errors import (
    ChecksumError,
    ConfigMismatchError,
    ManifestError,
    ModelError,
    ModelNotFoundError,
    WeightsMismatchError,
)
from .format import (
    MANIFEST_KEY,
    MODEL_SUFFIX,
    checksum_of,
    read_manifest,
    read_weights,
    write_model,
)
from .library import (
    ModelInfo,
    ModelLibrary,
    default_library_path,
    library_search_path,
    open_library,
)


def run_named(
    name: str,
    signal: np.ndarray,
    *,
    sample_rate: int | None = None,
) -> tuple[np.ndarray, SpectralModel]:
    """Run a named model of the shipped library over a whole signal.

    This is the **learned restoration** of a file render: the chain is the
    hand written restoration, and a model refines what it produced.  The
    model is asked about the **mix** of the channels and the gain it
    produces is applied to every channel, so the decision is the same for
    the whole image and the image does not move.

    The name is the name the model publishes.  A name the library does not
    hold is a **refusal** (:class:`ModelError`), not a silent fall back to
    the hand written path: a render that was asked for a model and did not
    get one is not the render that was asked for.

    Parameters
    ----------
    name : str
        The name of the model.
    signal : numpy.ndarray
        The signal, shape ``(n_samples,)`` or ``(n_samples, channels)``.
    sample_rate : int, optional
        The rate of the signal.  It must be the rate the model runs at.

    Returns
    -------
    (numpy.ndarray, SpectralModel)
        The processed signal, same shape as the input, and the model that
        ran.

    Raises
    ------
    ModelError
        When the library does not hold a model of that name.
    ConfigMismatchError
        When the signal does not run at the rate of the model.
    """
    library = open_library()
    model = library.load(str(name).strip())
    out = run_offline_mix(model, signal, sample_rate=sample_rate)
    return out, model
from .manifest import (
    FEATURE_LAYOUT_VERSION,
    FEATURE_LAYOUTS,
    FORMAT_NAME,
    FORMAT_VERSION,
    OUTPUT_KINDS,
    PROBLEMS,
    RETIRED_FEATURE_LAYOUTS,
    STATE_SCOPES,
    ContextSpec,
    Features,
    InputSpec,
    Manifest,
    NetworkSpec,
    OutputSpec,
    StateSpec,
    WeightSpec,
)
from .resolve import (
    OFFLINE,
    REALTIME,
    Binding,
    Resolver,
    default_resolver,
    describe_running,
    mode_of,
    request,
    running_model,
    set_default_resolver,
)

__all__ = [
    # the format
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "FEATURE_LAYOUT_VERSION",
    "FEATURE_LAYOUTS",
    "RETIRED_FEATURE_LAYOUTS",
    "PROBLEMS",
    "OUTPUT_KINDS",
    "STATE_SCOPES",
    "MANIFEST_KEY",
    "MODEL_SUFFIX",
    # the manifest
    "Manifest",
    "Features",
    "InputSpec",
    "OutputSpec",
    "ContextSpec",
    "StateSpec",
    "NetworkSpec",
    "WeightSpec",
    # the container
    "write_model",
    "read_manifest",
    "read_weights",
    "checksum_of",
    # the library
    "ModelLibrary",
    "ModelInfo",
    "open_library",
    "default_library_path",
    "library_search_path",
    # the engine
    "SpectralModel",
    "ModelRunner",
    "ModelOutput",
    "run_offline",
    "run_offline_mix",
    "run_named",
    # the seam
    "Resolver",
    "Binding",
    "request",
    "running_model",
    "describe_running",
    "mode_of",
    "default_resolver",
    "set_default_resolver",
    "REALTIME",
    "OFFLINE",
    # the refusals
    "ModelError",
    "ManifestError",
    "WeightsMismatchError",
    "ChecksumError",
    "ConfigMismatchError",
    "ModelNotFoundError",
]
