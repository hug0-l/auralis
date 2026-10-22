"""
aurcore.models.format — the file a model lives in.

A model is **one file** with two parts: the weights and the manifest.  The
file is a NumPy archive (``.npz``): a zip of ``.npy`` members, read by
``numpy.load`` with ``allow_pickle=False``.  Nothing outside NumPy is needed
to read it, so the project gains no dependency.

Members
-------
``__manifest__``
    The manifest, as the UTF-8 bytes of its JSON document.
``w0``, ``b0``, ``w1``, ``b1``, ...
    The weights.  ``w{i}`` is the matrix of layer ``i`` (shape
    ``(in, out)``), ``b{i}`` is its bias (shape ``(out,)``).  Every array is
    ``float32``.

Why one file
------------
One artefact per model means one artefact to ship, to version, to date and
to checksum.  The manifest lives inside the artefact, so a model that has
been copied somewhere still describes itself: its name, its version, the
date it was trained, the problem it solves, the frame it expects, the
context it needs, **the feature layout its weights were fitted on** and
the checksum of its own weights travel with it.

The checksum
------------
The checksum is taken over the **content** of the weights, not over the
bytes of the file: for every array, in the order of the names, the name,
the data type, the shape and the raw values are hashed.  So the checksum
does not depend on the order the arrays were written in, and it does not
change when the archive is rewritten.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Mapping

import numpy as np

from .errors import ChecksumError, ManifestError, WeightsMismatchError
from .manifest import FORMAT_NAME, Manifest

__all__ = [
    "MANIFEST_KEY",
    "checksum_of",
    "read_manifest",
    "read_weights",
    "write_model",
]

#: The member the manifest lives in.
MANIFEST_KEY = "__manifest__"

#: The extension of a model file.
MODEL_SUFFIX = ".aurm"


def checksum_of(weights: Mapping[str, np.ndarray]) -> str:
    """Return the checksum of a set of weights.

    The checksum covers the name, the data type, the shape and the values of
    every array, taken in the order of the names.  It therefore does not
    depend on the order the arrays were written in.

    Parameters
    ----------
    weights : mapping of str to numpy.ndarray
        The arrays to hash.

    Returns
    -------
    str
        ``"sha256:"`` followed by the 64 hex digits of the digest.
    """
    digest = hashlib.sha256()
    for name in sorted(weights):
        array = np.ascontiguousarray(weights[name])
        digest.update(name.encode("utf-8"))
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(repr(tuple(array.shape)).encode("ascii"))
        digest.update(array.tobytes(order="C"))
    return "sha256:" + digest.hexdigest()


def _manifest_bytes(manifest: Manifest) -> bytes:
    """Return the canonical JSON bytes of a manifest."""
    document = manifest.to_dict()
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def write_model(path, manifest: Manifest, weights: Mapping[str, np.ndarray]) -> Path:
    """Write a model file.

    Parameters
    ----------
    path : str or pathlib.Path
        Where to write the file.  The parent directory is created if it is
        not there.
    manifest : Manifest
        The manifest of the model.
    weights : mapping of str to numpy.ndarray
        The weights.  Every array the manifest declares has to be here, and
        nothing else may be.

    Returns
    -------
    pathlib.Path
        The path that was written.

    Raises
    ------
    WeightsMismatchError
        When the weights do not match the manifest.
    """
    target = Path(path)
    declared = {spec.key: spec for spec in manifest.weights}
    missing = sorted(set(declared) - set(weights))
    if missing:
        raise WeightsMismatchError(
            f"the model {manifest.name!r} declares the weight(s) "
            f"{', '.join(repr(name) for name in missing)}, which are not in the weights"
        )
    extra = sorted(set(weights) - set(declared))
    if extra:
        raise WeightsMismatchError(
            f"the weights hold {', '.join(repr(name) for name in extra)}, which the "
            f"manifest of the model {manifest.name!r} does not declare"
        )
    for spec in manifest.weights:
        array = np.asarray(weights[spec.key])
        if tuple(array.shape) != tuple(spec.shape):
            raise WeightsMismatchError(
                f"the weight {spec.name!r} has the shape {tuple(array.shape)}, but "
                f"the manifest declares {tuple(spec.shape)}"
            )
        if array.dtype != np.dtype(spec.dtype):
            raise WeightsMismatchError(
                f"the weight {spec.name!r} has the type {array.dtype}, but the "
                f"manifest declares {spec.dtype}"
            )
    payload: dict[str, np.ndarray] = {
        MANIFEST_KEY: np.frombuffer(_manifest_bytes(manifest), dtype=np.uint8)
    }
    for spec in manifest.weights:
        payload[spec.key] = np.ascontiguousarray(weights[spec.key], dtype=np.dtype(spec.dtype))
    buffer = io.BytesIO()
    np.savez(buffer, **payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(buffer.getvalue())
    return target


def _open_archive(path: Path):
    """Open a model file as a NumPy archive."""
    try:
        archive = np.load(path, allow_pickle=False)
    except FileNotFoundError:
        raise
    except Exception as error:  # noqa: BLE001 - any archive problem is a refusal
        raise ManifestError(f"{path.name} is not a readable model file: {error}") from None
    if not hasattr(archive, "files"):
        raise ManifestError(
            f"{path.name} is not a model file: it holds a single array, not an archive"
        )
    return archive


def read_manifest(path) -> dict:
    """Read the manifest of a model file **without reading the weights**.

    Only the manifest member is read; the archive is a zip, so the weight
    members are not touched.  This is what makes the listing of a library
    cheap.

    Parameters
    ----------
    path : str or pathlib.Path
        The model file.

    Returns
    -------
    dict
        The manifest document.

    Raises
    ------
    ManifestError
        When the file is not a model file or carries no manifest.
    """
    target = Path(path)
    with _open_archive(target) as archive:
        if MANIFEST_KEY not in archive.files:
            raise ManifestError(
                f"{target.name} carries no manifest: the member {MANIFEST_KEY!r} is missing"
            )
        raw = np.asarray(archive[MANIFEST_KEY]).tobytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"{target.name} carries a manifest that is not JSON: {error}") from None
    if not isinstance(document, dict):
        raise ManifestError(f"{target.name} carries a manifest that is not an object")
    if document.get("format") != FORMAT_NAME:
        raise ManifestError(
            f"{target.name} is not an Auralis model (the manifest declares the format "
            f"{document.get('format')!r})"
        )
    return document


def read_weights(path) -> dict[str, np.ndarray]:
    """Read the weights of a model file.

    Parameters
    ----------
    path : str or pathlib.Path
        The model file.

    Returns
    -------
    dict of str to numpy.ndarray
        The arrays, keyed by their manifest key.
    """
    target = Path(path)
    with _open_archive(target) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files if key != MANIFEST_KEY}


def verify_checksum(manifest: Manifest, weights: Mapping[str, np.ndarray]) -> None:
    """Refuse weights that do not carry the checksum the manifest publishes.

    Raises
    ------
    ChecksumError
        When the checksum does not match.
    """
    found = checksum_of(weights)
    if found != manifest.checksum:
        raise ChecksumError(
            f"the model {manifest.name!r} publishes the checksum {manifest.checksum} "
            f"but its weights carry {found}"
        )
