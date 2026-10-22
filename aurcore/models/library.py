"""
aurcore.models.library — the directory of models.

The library is a **directory**: one file per model, each file named after
what the model does.  The directory is the shipping unit — it is copied, it
is versioned, it is listed.

Listing is cheap
----------------
The loader can list the library — the name, the version, the date, the
problem — **without reading the weights**.  The manifest lives inside the
model file, and the file is an archive, so the manifest is read on its own.
A library whose weights are broken still lists; the entry carries the
reason.

Loading is strict
-----------------
Loading a model validates it, and a model that does not match is refused
with a message that says what is wrong:

* the manifest is malformed, incomplete or carries an unknown field;
* the manifest does not match the weights (a declared array is missing, or
  it is there with another shape);
* the weights do not carry the checksum the manifest publishes;
* the model does not run at the configuration the engine asked for.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .engine import SpectralModel
from .errors import (
    ChecksumError,
    ConfigMismatchError,
    ManifestError,
    ModelError,
    ModelNotFoundError,
    WeightsMismatchError,
)
from .format import MODEL_SUFFIX, checksum_of, read_manifest, read_weights
from .manifest import Manifest

__all__ = [
    "ModelInfo",
    "ModelLibrary",
    "default_library_path",
    "open_library",
    "library_search_path",
]

#: The environment variable that points at a model directory.
ENV_LIBRARY = "AURALIS_MODEL_PATH"


@dataclass(frozen=True)
class ModelInfo:
    """What the library knows about one model, without its weights.

    Attributes
    ----------
    name : str
        The name the model publishes.
    version : str
        The version of the model.
    trained_on : str
        The date the model was trained.
    problem : str
        The problem the model solves.
    variant : str
        The variant inside the family.
    epoch : int
        The epoch the training stopped at.
    task : str
        What the model does, in one sentence.
    path : str
        The file the model lives in, relative to the library when possible.
    readable : bool
        False when the manifest could not be read; ``error`` then says why.
    error : str
        The reason the model could not be read, if it could not.
    """

    name: str
    version: str = ""
    trained_on: str = ""
    problem: str = ""
    variant: str = ""
    epoch: int = 0
    task: str = ""
    path: str = ""
    readable: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-safe description of the entry."""
        return {
            "name": self.name,
            "version": self.version,
            "trained_on": self.trained_on,
            "problem": self.problem,
            "variant": self.variant,
            "epoch": self.epoch,
            "task": self.task,
            "path": self.path,
            "readable": self.readable,
            "error": self.error,
        }


def library_search_path() -> list[Path]:
    """Return the directories that are searched for a model library.

    The search order is

    1. the directories in the :data:`ENV_LIBRARY` environment variable,
       separated by the separator of the platform;
    2. the ``models`` directory next to the package, which is where the
       shipped models live in the source tree;
    3. the ``share/auralis/models`` directory of the installation, which is
       where an installed copy keeps them.

    Returns
    -------
    list of pathlib.Path
        The directories, in the order they are searched.  The list may
        carry directories that do not exist; the caller skips those.
    """
    found: list[Path] = []
    raw = os.environ.get(ENV_LIBRARY, "")
    for piece in raw.split(os.pathsep):
        piece = piece.strip()
        if piece:
            found.append(Path(piece))
    package_root = Path(__file__).resolve().parents[2]
    found.append(package_root / "models")
    found.append(Path(sys.prefix) / "share" / "auralis" / "models")
    return found


def default_library_path() -> Path:
    """Return the directory of the shipped models.

    Returns
    -------
    pathlib.Path
        The first directory of :func:`library_search_path` that exists.
        When none exists, the ``models`` directory next to the package is
        returned, so the caller gets a path it can report.
    """
    for candidate in library_search_path():
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[2] / "models"


class ModelLibrary:
    """A directory of models.

    Parameters
    ----------
    directory : str or pathlib.Path
        The directory the models live in.
    """

    def __init__(self, directory):
        """Point the library at a directory."""
        self.directory = Path(directory)

    # -- the files --------------------------------------------------------
    def files(self) -> list[Path]:
        """Return the model files of the directory, sorted by name."""
        if not self.directory.is_dir():
            return []
        found = [
            path
            for path in self.directory.iterdir()
            if path.is_file() and path.suffix == MODEL_SUFFIX
        ]
        return sorted(found, key=lambda path: path.name)

    def _relative(self, path: Path) -> str:
        """Return the path of a file, relative to the library when possible."""
        try:
            return path.relative_to(self.directory).as_posix()
        except ValueError:
            return path.name

    # -- the listing ------------------------------------------------------
    def list(self) -> list[ModelInfo]:
        """List the models of the library **without reading their weights**.

        Returns
        -------
        list of ModelInfo
            One entry per model file, sorted by name.  An entry that could
            not be read carries the reason in ``error`` and says so in
            ``readable``.
        """
        infos: list[ModelInfo] = []
        seen: dict[str, str] = {}
        for path in self.files():
            relative = self._relative(path)
            try:
                document = read_manifest(path)
                manifest = Manifest.from_dict(document)
            except ModelError as error:
                infos.append(
                    ModelInfo(
                        name=path.stem,
                        path=relative,
                        readable=False,
                        error=str(error),
                    )
                )
                continue
            except Exception as error:  # noqa: BLE001 - a broken file is an entry
                infos.append(
                    ModelInfo(
                        name=path.stem,
                        path=relative,
                        readable=False,
                        error=f"{type(error).__name__}: {error}",
                    )
                )
                continue
            if manifest.name in seen:
                infos.append(
                    ModelInfo(
                        name=manifest.name,
                        version=manifest.version,
                        trained_on=manifest.trained_on,
                        problem=manifest.problem,
                        variant=manifest.variant,
                        epoch=manifest.epoch,
                        task=manifest.task,
                        path=relative,
                        readable=False,
                        error=(
                            f"the model name {manifest.name!r} is already carried by "
                            f"{seen[manifest.name]!r}"
                        ),
                    )
                )
                continue
            seen[manifest.name] = relative
            infos.append(
                ModelInfo(
                    name=manifest.name,
                    version=manifest.version,
                    trained_on=manifest.trained_on,
                    problem=manifest.problem,
                    variant=manifest.variant,
                    epoch=manifest.epoch,
                    task=manifest.task,
                    path=relative,
                    readable=True,
                )
            )
        return infos

    def info(self, name: str) -> ModelInfo:
        """Return what the library knows about the model called ``name``.

        Raises
        ------
        ModelNotFoundError
            When no file carries that name.
        """
        for entry in self.list():
            if entry.name == name:
                return entry
        raise ModelNotFoundError(f"the library holds no model called {name!r}")

    def for_problem(self, problem: str) -> ModelInfo | None:
        """Return the model that solves ``problem``, or nothing.

        A library may hold several models for the same problem; the one
        with the highest version wins, so an update replaces the old model
        without the caller having to know about it.
        """
        best: ModelInfo | None = None
        for entry in self.list():
            if not entry.readable or entry.problem != problem:
                continue
            if best is None or _version_key(entry.version) > _version_key(best.version):
                best = entry
        return best

    def names(self) -> list[str]:
        """Return the names of the readable models of the library."""
        return [entry.name for entry in self.list() if entry.readable]

    # -- the load ---------------------------------------------------------
    def load(
        self,
        name: str,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> SpectralModel:
        """Load a model by name, with the validation the format demands.

        Parameters
        ----------
        name : str
            The name the model publishes.
        sample_rate : int, optional
            The rate the engine runs at.  A model that was trained at
            another rate is refused.
        channels : int, optional
            The number of channels the engine runs at.  A model that reads
            more than one channel at a time has to match it.

        Returns
        -------
        SpectralModel
            The model, ready to run.

        Raises
        ------
        ModelNotFoundError
            When no file carries that name.
        ManifestError
            When the manifest is malformed.
        WeightsMismatchError
            When the manifest does not match the weights.
        ChecksumError
            When the weights do not carry the published checksum.
        ConfigMismatchError
            When the model does not run at the configuration asked for.
        """
        entry = self.info(name)
        path = self.directory / entry.path
        document = read_manifest(path)
        manifest = Manifest.from_dict(document)
        weights = read_weights(path)
        _check_weights(manifest, weights)
        found = checksum_of(weights)
        if found != manifest.checksum:
            raise ChecksumError(
                f"the model {manifest.name!r} publishes the checksum {manifest.checksum} "
                f"but its weights carry {found}"
            )
        _check_config(manifest, sample_rate, channels)
        return SpectralModel(manifest, weights)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModelLibrary {self.directory.name}>"


def _version_key(version: str) -> tuple[int, int, int]:
    """Return a comparable key for a ``major.minor.patch`` version."""
    parts = version.split(".")
    numbers = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _check_weights(manifest: Manifest, weights: dict[str, np.ndarray]) -> None:
    """Refuse weights that do not match the manifest."""
    declared = {spec.key: spec for spec in manifest.weights}
    missing = sorted(set(declared) - set(weights))
    if missing:
        raise WeightsMismatchError(
            f"the model {manifest.name!r} declares the weight(s) "
            f"{', '.join(repr(name) for name in missing)}, which are not in the file"
        )
    extra = sorted(set(weights) - set(declared))
    if extra:
        raise WeightsMismatchError(
            f"the file of the model {manifest.name!r} holds the array(s) "
            f"{', '.join(repr(name) for name in extra)}, which the manifest does not declare"
        )
    for spec in manifest.weights:
        array = np.asarray(weights[spec.key])
        if tuple(array.shape) != tuple(spec.shape):
            raise WeightsMismatchError(
                f"the weight {spec.name!r} of the model {manifest.name!r} has the "
                f"shape {tuple(array.shape)}, but the manifest declares "
                f"{tuple(spec.shape)}"
            )
        if array.dtype != np.dtype(spec.dtype):
            raise WeightsMismatchError(
                f"the weight {spec.name!r} of the model {manifest.name!r} has the "
                f"type {array.dtype}, but the manifest declares {spec.dtype}"
            )


def _check_config(
    manifest: Manifest, sample_rate: int | None, channels: int | None
) -> None:
    """Refuse a model that does not run at the configuration asked for."""
    if sample_rate is not None:
        rate = int(sample_rate)
        if rate != manifest.input.sample_rate:
            raise ConfigMismatchError(
                f"the model {manifest.name!r} was trained at "
                f"{manifest.input.sample_rate} Hz, but the engine runs at {rate} Hz"
            )
    if channels is not None:
        count = int(channels)
        if manifest.input.channels != 1 and count != manifest.input.channels:
            raise ConfigMismatchError(
                f"the model {manifest.name!r} reads {manifest.input.channels} "
                f"channels at a time, but the engine runs {count}"
            )


def open_library(directory=None) -> ModelLibrary:
    """Open a model library.

    Parameters
    ----------
    directory : str or pathlib.Path, optional
        The directory of the models.  Without it the shipped library is
        used.

    Returns
    -------
    ModelLibrary
        The library.
    """
    if directory is None:
        return ModelLibrary(default_library_path())
    return ModelLibrary(directory)
