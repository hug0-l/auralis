"""
aurcore.models.errors — the refusals of the model loader.

Every problem the loader finds is a *refusal*: the model is not loaded and
the reason is written down in words a human can act on.  The hierarchy is
flat on purpose — a caller catches :class:`ModelError` and gets the message;
a test catches the concrete class and gets the kind of problem.

The classes are the four refusals the format specification names:

``ManifestError``
    The manifest is malformed, incomplete, or describes a model the engine
    cannot run: a missing field, an unknown field, a context that is not a
    whole number of frames, a state scope that outlives the state.
``WeightsMismatchError``
    The manifest does not match the weights: a declared array is not in the
    file, or it is there with another shape.
``ChecksumError``
    The weights are not the weights the manifest describes.
``ConfigMismatchError``
    The model does not run at the engine's configuration (sample rate or
    channel count).

``ModelNotFoundError`` is raised by the library when a name is asked for and
no file carries it.
"""
from __future__ import annotations

__all__ = [
    "ModelError",
    "ManifestError",
    "WeightsMismatchError",
    "ChecksumError",
    "ConfigMismatchError",
    "ModelNotFoundError",
]


class ModelError(Exception):
    """Base class of every refusal of the model loader."""


class ManifestError(ModelError):
    """The manifest is malformed or describes an unusable model."""


class WeightsMismatchError(ModelError):
    """The manifest and the weights do not describe the same model."""


class ChecksumError(ModelError):
    """The weights do not carry the checksum the manifest publishes."""


class ConfigMismatchError(ModelError):
    """The model does not run at the configuration the engine asked for."""


class ModelNotFoundError(ModelError):
    """No model of that name is in the library."""
