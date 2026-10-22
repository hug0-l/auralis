"""
aurcore.models.manifest — the manifest of a model.

A manifest is a JSON document.  It is the *self description* of a model:
what the model is called, what it was trained for, when it was trained, what
problem it solves, what it expects, what it produces, how much history it
needs, how long its state lives, which feature layout its weights were
fitted on — and the checksum of the weights, so a reader can tell whether
the weights are the ones the manifest describes.

The parser is strict on purpose.  A model that does not match the engine's
configuration must not be loaded silently, and a manifest that carries a field
nobody reads is a manifest that will be read differently by two tools.  So:

* a missing field is a refusal;
* an unknown field is a refusal (the format version gates that);
* a value outside its enumeration is a refusal;
* a number that does not add up (the context against the hop, the parameter
  count against the weights, the output bins against the frame) is a refusal;
* a manifest that does not say which feature layout the weights were fitted
  on is a refusal, and so is one that names a layout this build does not
  read.

Every refusal names the field and says what was expected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .errors import ManifestError

__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "FEATURE_LAYOUT_VERSION",
    "FEATURE_LAYOUTS",
    "RETIRED_FEATURE_LAYOUTS",
    "PROBLEMS",
    "OUTPUT_KINDS",
    "STATE_SCOPES",
    "FEATURE_KINDS",
    "NETWORK_KINDS",
    "TRANSFERS",
    "Features",
    "InputSpec",
    "OutputSpec",
    "ContextSpec",
    "StateSpec",
    "NetworkSpec",
    "WeightSpec",
    "Manifest",
]

#: The value of the ``format`` field.  A file that does not carry it is not
#: an Auralis model.
FORMAT_NAME = "auralis-model"

#: The version of the format this build understands.  A manifest that
#: announces a newer format version is refused: the meaning of a field may
#: have changed.
FORMAT_VERSION = 1

#: The version of the **feature layout** this build reads.
#:
#: The layout is the scale the features of a model are read in: the
#: reference the level of a band is measured against, the floor below which
#: a band counts as empty, the ceiling above which it is clipped, and what a
#: band that carries nothing at all reads.  The weights of a model are
#: fitted on the layout of their training, and two models of different
#: layouts have the same weight shapes, so nothing in the weights tells the
#: two apart: the manifest says which layout the weights belong to, and a
#: layout this build does not read is refused.
FEATURE_LAYOUT_VERSION = 1

#: The feature layouts this build reads: the version, and what the layout
#: is.  A manifest that names a version that is not in here is refused.
FEATURE_LAYOUTS: dict[int, str] = {
    1: (
        "the dBFS layout: 0 dB is a full scale sinusoid in the transform, the "
        "floor is -100 dBFS, the ceiling is +12 dBFS, and a band that carries "
        "nothing at all reads as the quietest value of the layout"
    ),
}

#: The feature layouts the format knows about but does not read any more:
#: the version, and what the layout was.  A manifest of one of them is
#: refused, and the refusal names the layout: the weights of a model of a
#: retired layout were fitted on a scale that is gone, and they cannot be
#: read with the layout of this build, so the model has to be trained again.
RETIRED_FEATURE_LAYOUTS: dict[int, str] = {
    0: (
        "the raw magnitude layout: the zero point is the magnitude 1 of the raw "
        "transform (-48 dBFS for a 1024 sample frame), and a band that carries "
        "nothing at all read as the loudest value of the layout"
    ),
}

#: The problems a model may solve.  The three are the problems the product
#: is organised around: the voice, the voice with its reverb, the ambience.
PROBLEMS: tuple[str, ...] = ("voice", "voice_reverb", "ambience")

#: What a model may produce.
OUTPUT_KINDS: tuple[str, ...] = ("spectral_mask", "spectrum")

#: The lifetime of the state a model carries.
STATE_SCOPES: tuple[str, ...] = ("block", "file", "session")

#: The feature layouts the engine understands.
FEATURE_KINDS: tuple[str, ...] = ("log_magnitude_bands",)

#: The network kinds the engine understands.
NETWORK_KINDS: tuple[str, ...] = ("mlp", "sequence")

#: The transfer functions that turn the raw output of a network into the
#: values the engine applies.
TRANSFERS: tuple[str, ...] = ("sigmoid", "linear")

#: The order of the state scopes, coarsest last.  The state of a model may
#: not outlive its scope: a model whose scope is ``file`` may not keep its
#: state until the end of the session.
_SCOPE_RANK = {"block": 0, "file": 1, "session": 2}

#: A model name is a file name stem, so it may not carry a path separator.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: A model version is ``major.minor.patch``.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: A checksum is ``sha256:`` followed by 64 hex digits.
_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require(mapping: dict, key: str, kind: type, where: str) -> Any:
    """Return ``mapping[key]`` after checking that it is there and is a ``kind``."""
    if key not in mapping:
        raise ManifestError(f"{where}: the field {key!r} is missing")
    value = mapping[key]
    if kind is int and isinstance(value, bool):
        raise ManifestError(f"{where}: the field {key!r} must be a whole number, not a boolean")
    if not isinstance(value, kind):
        raise ManifestError(
            f"{where}: the field {key!r} must be {kind.__name__}, "
            f"not {type(value).__name__}"
        )
    return value


def _number(value: Any, key: str, where: str) -> float:
    """Return a finite float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{where}: the field {key!r} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ManifestError(f"{where}: the field {key!r} must be a finite number")
    return number


def _integer(value: Any, key: str, where: str) -> int:
    """Return a whole number."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{where}: the field {key!r} must be a whole number")
    return int(value)


def _no_unknown(mapping: dict, allowed: Iterable[str], where: str) -> None:
    """Refuse a field that the format does not define."""
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ManifestError(
            f"{where}: the field {unknown[0]!r} is not part of the format "
            f"(known fields: {', '.join(sorted(allowed))})"
        )


def _mapping(value: Any, key: str, where: str) -> dict:
    """Return a nested mapping."""
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: the field {key!r} must be an object")
    return value


def _feature_layout(data: dict) -> int:
    """Return the version of the feature layout the weights were fitted on.

    The field is required, and a layout this build does not read is
    refused.  The weights of a model of another layout have the same shapes
    as the weights of a model of this build, so nothing in the weights
    tells the two apart: the manifest says which layout the weights belong
    to, and a manifest that does not say is a manifest that cannot be read.

    Parameters
    ----------
    data : dict
        The manifest document.

    Returns
    -------
    int
        The version of the feature layout.

    Raises
    ------
    ManifestError
        When the field is missing, or when it names a layout this build
        does not read.
    """
    if "feature_layout_version" not in data:
        raise ManifestError(
            "the manifest: the field 'feature_layout_version' is missing: the "
            "manifest does not say which feature layout the weights were trained "
            f"on, and this build reads the layout {FEATURE_LAYOUT_VERSION} "
            f"({FEATURE_LAYOUTS[FEATURE_LAYOUT_VERSION]}); a model of a build that "
            "did not carry the field has to be trained again"
        )
    version = _integer(
        data["feature_layout_version"], "feature_layout_version", "the manifest"
    )
    if version in RETIRED_FEATURE_LAYOUTS:
        raise ManifestError(
            f"the manifest: the feature layout version {version} is "
            f"{RETIRED_FEATURE_LAYOUTS[version]}; this build reads the layout "
            f"{FEATURE_LAYOUT_VERSION}, and the weights of a model of the layout "
            f"{version} cannot be read with it: the model has to be trained again"
        )
    if version not in FEATURE_LAYOUTS:
        known = ", ".join(
            str(value)
            for value in sorted(set(FEATURE_LAYOUTS) | set(RETIRED_FEATURE_LAYOUTS))
        )
        raise ManifestError(
            f"the manifest: the feature layout version {version} is not one the "
            f"format knows (the known versions are {known}); this build reads the "
            f"layout {FEATURE_LAYOUT_VERSION} "
            f"({FEATURE_LAYOUTS[FEATURE_LAYOUT_VERSION]}); a model of another "
            "layout has to be trained again"
        )
    return version


# ---------------------------------------------------------------------------
# the parts of a manifest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Features:
    """The features a model reads out of a frame.

    Attributes
    ----------
    kind : str
        The layout.  ``"log_magnitude_bands"``: the magnitude spectrum of the
        frame, in dB, averaged over the bands the model publishes.
    band_edges_hz : tuple of float
        The ``K + 1`` band edges in Hz.  The model publishes its own bands,
        so two models with different bands can both be read correctly.
    floor_db : float
        The level below which a bin is counted as silent.
    delta_frames : int
        How many frames of history the features read.  ``0`` means the
        features of one frame only; ``2`` means the features of the frame and
        the difference to the two frames before it.
    """

    kind: str
    band_edges_hz: tuple[float, ...]
    floor_db: float
    delta_frames: int

    @property
    def bands(self) -> int:
        """The number of bands."""
        return len(self.band_edges_hz) - 1

    @property
    def dimension(self) -> int:
        """The length of the feature vector the network reads."""
        return self.bands * (1 + self.delta_frames)

    def to_dict(self) -> dict:
        """Return the manifest form of the features."""
        return {
            "kind": self.kind,
            "band_edges_hz": list(self.band_edges_hz),
            "floor_db": self.floor_db,
            "delta_frames": self.delta_frames,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Features":
        """Parse the features, refusing anything the engine cannot read."""
        mapping = _mapping(data, "features", where)
        _no_unknown(mapping, ("kind", "band_edges_hz", "floor_db", "delta_frames"), where)
        kind = _require(mapping, "kind", str, where)
        if kind not in FEATURE_KINDS:
            raise ManifestError(
                f"{where}: the feature kind {kind!r} is not one the engine reads "
                f"({', '.join(FEATURE_KINDS)})"
            )
        edges_raw = _require(mapping, "band_edges_hz", list, where)
        if len(edges_raw) < 2:
            raise ManifestError(f"{where}: the band list needs at least two edges")
        edges = tuple(_number(edge, "band_edges_hz", where) for edge in edges_raw)
        for index in range(1, len(edges)):
            if edges[index] <= edges[index - 1]:
                raise ManifestError(
                    f"{where}: the band edges must grow, but edge {index} "
                    f"({edges[index]} Hz) does not follow edge {index - 1} "
                    f"({edges[index - 1]} Hz)"
                )
        floor_db = _number(_require(mapping, "floor_db", (int, float), where), "floor_db", where)
        if floor_db > 0.0:
            raise ManifestError(f"{where}: the floor must be at or below 0 dB")
        delta = _integer(
            _require(mapping, "delta_frames", int, where), "delta_frames", where
        )
        if delta < 0:
            raise ManifestError(f"{where}: the number of delta frames cannot be negative")
        return cls(kind=kind, band_edges_hz=edges, floor_db=floor_db, delta_frames=delta)


@dataclass(frozen=True)
class InputSpec:
    """What a model expects to be fed.

    Attributes
    ----------
    sample_rate : int
        The rate the model was trained at.  A model does not run at another
        rate: the loader refuses it.
    channels : int
        The number of channels the model reads at once.  ``1`` means the
        model runs on one channel at a time, which is what a stereo engine
        needs.
    frame_size : int
        The length of the frame in samples.
    hop : int
        The distance between two frames in samples.
    features : Features
        The features the model reads out of a frame.
    """

    sample_rate: int
    channels: int
    frame_size: int
    hop: int
    features: Features

    @property
    def bins(self) -> int:
        """The number of frequency bins of a frame."""
        return self.frame_size // 2 + 1

    def to_dict(self) -> dict:
        """Return the manifest form of the input."""
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_size": self.frame_size,
            "hop": self.hop,
            "features": self.features.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "input") -> "InputSpec":
        """Parse the input, refusing anything the engine cannot feed."""
        mapping = _mapping(data, "input", where)
        _no_unknown(
            mapping, ("sample_rate", "channels", "frame_size", "hop", "features"), where
        )
        rate = _integer(_require(mapping, "sample_rate", int, where), "sample_rate", where)
        if not 8000 <= rate <= 384000:
            raise ManifestError(f"{where}: the sample rate {rate} Hz is not a rate a frame fits")
        channels = _integer(_require(mapping, "channels", int, where), "channels", where)
        if channels < 1:
            raise ManifestError(f"{where}: the channel count must be at least one")
        frame_size = _integer(_require(mapping, "frame_size", int, where), "frame_size", where)
        if frame_size < 64 or frame_size & (frame_size - 1):
            raise ManifestError(f"{where}: the frame size {frame_size} is not a power of two")
        hop = _integer(_require(mapping, "hop", int, where), "hop", where)
        if hop < 1 or hop > frame_size:
            raise ManifestError(
                f"{where}: the hop {hop} must be between 1 and the frame size {frame_size}"
            )
        features = Features.from_dict(
            _require(mapping, "features", dict, where), f"{where}.features"
        )
        nyquist = rate / 2.0
        if abs(features.band_edges_hz[0]) > 1e-9:
            raise ManifestError(f"{where}: the first band edge must be 0 Hz")
        if abs(features.band_edges_hz[-1] - nyquist) > 1e-6:
            raise ManifestError(
                f"{where}: the last band edge is {features.band_edges_hz[-1]} Hz, "
                f"but the engine runs at {nyquist} Hz (Nyquist)"
            )
        return cls(
            sample_rate=rate,
            channels=channels,
            frame_size=frame_size,
            hop=hop,
            features=features,
        )


@dataclass(frozen=True)
class OutputSpec:
    """What a model produces.

    Attributes
    ----------
    kind : str
        ``"spectral_mask"``: a gain per frequency bin, between 0 and 1, that
        is applied to the magnitude.  ``"spectrum"``: a spectrum the model
        produces, e.g. the ambience a signal should have.
    bins : int
        The number of bins the output covers.
    range : tuple of float
        The range of the produced values.
    transfer : str
        ``"sigmoid"`` turns the raw output of the network into a value
        between 0 and 1; ``"linear"`` leaves it alone.
    """

    kind: str
    bins: int
    range: tuple[float, float]
    transfer: str

    def to_dict(self) -> dict:
        """Return the manifest form of the output."""
        return {
            "kind": self.kind,
            "bins": self.bins,
            "range": list(self.range),
            "transfer": self.transfer,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "output") -> "OutputSpec":
        """Parse the output, refusing anything the engine cannot apply."""
        mapping = _mapping(data, "output", where)
        _no_unknown(mapping, ("kind", "bins", "range", "transfer"), where)
        kind = _require(mapping, "kind", str, where)
        if kind not in OUTPUT_KINDS:
            raise ManifestError(
                f"{where}: the output kind {kind!r} is not one the engine applies "
                f"({', '.join(OUTPUT_KINDS)})"
            )
        bins = _integer(_require(mapping, "bins", int, where), "bins", where)
        if bins < 2:
            raise ManifestError(f"{where}: the output must cover at least two bins")
        range_raw = _require(mapping, "range", list, where)
        if len(range_raw) != 2:
            raise ManifestError(f"{where}: the range must hold exactly two numbers")
        low = _number(range_raw[0], "range", where)
        high = _number(range_raw[1], "range", where)
        if low >= high:
            raise ManifestError(f"{where}: the range must grow, but {high} does not follow {low}")
        transfer = _require(mapping, "transfer", str, where)
        if transfer not in TRANSFERS:
            raise ManifestError(
                f"{where}: the transfer {transfer!r} is not one the engine applies "
                f"({', '.join(TRANSFERS)})"
            )
        if kind == "spectral_mask":
            if transfer != "sigmoid":
                raise ManifestError(
                    f"{where}: a spectral mask is a value between 0 and 1, so it needs "
                    f"the 'sigmoid' transfer, not {transfer!r}"
                )
            if low < 0.0 or high > 1.0:
                raise ManifestError(
                    f"{where}: a spectral mask lies between 0 and 1, but the range is "
                    f"[{low}, {high}]"
                )
        return cls(kind=kind, bins=bins, range=(low, high), transfer=transfer)


@dataclass(frozen=True)
class ContextSpec:
    """How much history a model reads before it can answer.

    The context is the latency of the model, and the model has to publish
    it: a caller that does not know the context cannot place the output of
    the model in time.

    Attributes
    ----------
    frames : int
        The number of frames of history the model reads.  A whole number.
    samples : int
        The same in samples.  It must be exactly ``frames * hop``.
    """

    frames: int
    samples: int

    def to_dict(self) -> dict:
        """Return the manifest form of the context."""
        return {"frames": self.frames, "samples": self.samples}

    @classmethod
    def from_dict(cls, data: Any, hop: int, where: str = "context") -> "ContextSpec":
        """Parse the context and refuse a context that is not whole frames."""
        mapping = _mapping(data, "context", where)
        _no_unknown(mapping, ("frames", "samples"), where)
        frames = _integer(_require(mapping, "frames", int, where), "frames", where)
        if frames < 0:
            raise ManifestError(f"{where}: the number of frames cannot be negative")
        samples = _integer(_require(mapping, "samples", int, where), "samples", where)
        if samples < 0:
            raise ManifestError(f"{where}: the number of samples cannot be negative")
        if samples != frames * hop:
            raise ManifestError(
                f"{where}: a context of {samples} samples is not a whole number of "
                f"{hop}-sample frames (the model declared {frames} frames, which is "
                f"{frames * hop} samples)"
            )
        return cls(frames=frames, samples=samples)


@dataclass(frozen=True)
class StateSpec:
    """The state a model carries and when that state is dropped.

    Attributes
    ----------
    scope : str
        The unit the state belongs to: ``block``, ``file`` or ``session``.
        The state may not outlive its scope.
    cleared : str
        When the state is dropped: ``block``, ``file`` or ``session``.  It
        must not be coarser than the scope.
    """

    scope: str
    cleared: str

    def to_dict(self) -> dict:
        """Return the manifest form of the state."""
        return {"scope": self.scope, "cleared": self.cleared}

    @classmethod
    def from_dict(cls, data: Any, where: str = "state") -> "StateSpec":
        """Parse the state and refuse a state that outlives its scope."""
        mapping = _mapping(data, "state", where)
        _no_unknown(mapping, ("scope", "cleared"), where)
        scope = _require(mapping, "scope", str, where)
        if scope not in STATE_SCOPES:
            raise ManifestError(
                f"{where}: the state scope {scope!r} is not one of "
                f"{', '.join(STATE_SCOPES)}"
            )
        cleared = _require(mapping, "cleared", str, where)
        if cleared not in STATE_SCOPES:
            raise ManifestError(
                f"{where}: the state is cleared {cleared!r}, which is not one of "
                f"{', '.join(STATE_SCOPES)}"
            )
        if _SCOPE_RANK[cleared] > _SCOPE_RANK[scope]:
            raise ManifestError(
                f"{where}: the state is cleared at {cleared!r} but its scope is "
                f"{scope!r}, so it would outlive the scope it belongs to"
            )
        return cls(scope=scope, cleared=cleared)


@dataclass(frozen=True)
class NetworkSpec:
    """The shape of the network the weights belong to.

    Attributes
    ----------
    kind : str
        ``"mlp"``: a stack of fully connected layers.  ``"sequence"``: a
        stack of **causal** one dimensional convolutions over the time
        axis, so the answer for a frame is a function of the frames
        before it — the memory the first study's network had.
    layers : tuple of tuple
        ``(in, out, activation)`` per layer for the ``"mlp"`` kind and
        ``(in, out, kernel, dilation, activation)`` per layer for the
        ``"sequence"`` kind.  The activation of the last layer is
        ``"none"``: the transfer of the output does the rest.
    parameters : int
        The number of values the weights hold.  It must add up.
    """

    kind: str
    layers: tuple[tuple[int, ...], ...]
    parameters: int

    def to_dict(self) -> dict:
        """Return the manifest form of the network."""
        if self.kind == "sequence":
            described = [
                {
                    "in": width_in,
                    "out": width_out,
                    "kernel": kernel,
                    "dilation": dilation,
                    "activation": activation,
                }
                for width_in, width_out, kernel, dilation, activation in self.layers
            ]
        else:
            described = [
                {"in": width_in, "out": width_out, "activation": activation}
                for width_in, width_out, activation in self.layers
            ]
        return {
            "kind": self.kind,
            "layers": described,
            "parameters": self.parameters,
        }

    @property
    def receptive_span(self) -> int:
        """The number of frames the network reads to answer one frame.

        The fully connected kind reads one frame: it has no memory.  The
        sequence kind reads the frames of its convolution stack, and the
        **dilation** of a layer is what stretches the reach of a kernel
        without making the kernel wider — the way a long memory costs a
        few parameters instead of many.
        """
        if self.kind != "sequence":
            return 1
        span = 1
        for layer in self.layers:
            _, _, kernel, dilation, _ = layer
            span += (kernel - 1) * dilation
        return span

    @classmethod
    def from_dict(cls, data: Any, where: str = "network") -> "NetworkSpec":
        """Parse the network description."""
        mapping = _mapping(data, "network", where)
        _no_unknown(mapping, ("kind", "layers", "parameters"), where)
        kind = _require(mapping, "kind", str, where)
        if kind not in NETWORK_KINDS:
            raise ManifestError(
                f"{where}: the network kind {kind!r} is not one the engine runs "
                f"({', '.join(NETWORK_KINDS)})"
            )
        layers_raw = _require(mapping, "layers", list, where)
        if not layers_raw:
            raise ManifestError(f"{where}: the network needs at least one layer")
        layers: list[tuple[int, ...]] = []
        for index, layer in enumerate(layers_raw):
            place = f"{where}.layers[{index}]"
            entry = _mapping(layer, "layer", place)
            if kind == "sequence":
                _no_unknown(
                    entry, ("in", "out", "kernel", "dilation", "activation"), place
                )
            else:
                _no_unknown(entry, ("in", "out", "activation"), place)
            width_in = _integer(_require(entry, "in", int, place), "in", place)
            width_out = _integer(_require(entry, "out", int, place), "out", place)
            activation = _require(entry, "activation", str, place)
            if width_in < 1 or width_out < 1:
                raise ManifestError(f"{place}: a layer needs at least one value in and out")
            if activation not in ("relu", "none"):
                raise ManifestError(
                    f"{place}: the activation {activation!r} is not one the engine runs "
                    "('relu', 'none')"
                )
            if kind == "sequence":
                kernel = _integer(_require(entry, "kernel", int, place), "kernel", place)
                dilation = _integer(
                    _require(entry, "dilation", int, place), "dilation", place
                )
                if kernel < 1:
                    raise ManifestError(f"{place}: a kernel needs at least one tap")
                if dilation < 1:
                    raise ManifestError(
                        f"{place}: a dilation has to reach at least one frame"
                    )
                layers.append((width_in, width_out, kernel, dilation, activation))
            else:
                layers.append((width_in, width_out, activation))
        parameters = _integer(_require(mapping, "parameters", int, where), "parameters", where)
        if parameters < 1:
            raise ManifestError(f"{where}: the network needs at least one parameter")
        return cls(kind=kind, layers=tuple(layers), parameters=parameters)


@dataclass(frozen=True)
class WeightSpec:
    """One array of the weights.

    Attributes
    ----------
    name : str
        The name of the array inside the model file.
    shape : tuple of int
        The shape the array must have.
    dtype : str
        The data type the array must have.  ``float32`` keeps the file
        small and the arithmetic exact.
    key : str
        The name the array is stored under in the file.
    """

    name: str
    shape: tuple[int, ...]
    dtype: str
    key: str

    @property
    def size(self) -> int:
        """The number of values in the array."""
        total = 1
        for extent in self.shape:
            total *= extent
        return total

    def to_dict(self) -> dict:
        """Return the manifest form of the weight."""
        return {
            "name": self.name,
            "shape": list(self.shape),
            "dtype": self.dtype,
            "key": self.key,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "weights") -> "WeightSpec":
        """Parse one weight description."""
        mapping = _mapping(data, "weight", where)
        _no_unknown(mapping, ("name", "shape", "dtype", "key"), where)
        name = _require(mapping, "name", str, where)
        if not name:
            raise ManifestError(f"{where}: a weight needs a name")
        shape_raw = _require(mapping, "shape", list, where)
        if not shape_raw:
            raise ManifestError(f"{where}: the weight {name!r} needs a shape")
        shape = tuple(
            _integer(extent, "shape", f"{where}.{name}") for extent in shape_raw
        )
        if any(extent < 1 for extent in shape):
            raise ManifestError(f"{where}: the shape of {name!r} must be positive")
        dtype = _require(mapping, "dtype", str, where)
        if dtype != "float32":
            raise ManifestError(
                f"{where}: the weight {name!r} has the type {dtype!r}; the format "
                "stores float32 only"
            )
        key = _require(mapping, "key", str, where)
        if not key:
            raise ManifestError(f"{where}: the weight {name!r} needs a key")
        return cls(name=name, shape=shape, dtype=dtype, key=key)


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
#: The fields a manifest carries, in the order the specification lists them.
FIELDS: tuple[str, ...] = (
    "format",
    "format_version",
    "feature_layout_version",
    "name",
    "version",
    "epoch",
    "trained_on",
    "variant",
    "problem",
    "task",
    "input",
    "output",
    "context",
    "state",
    "network",
    "weights",
    "checksum",
    "deterministic",
    "notes",
)


@dataclass(frozen=True)
class Manifest:
    """The parsed manifest of a model.

    The object is immutable and carries the parsed parts; :meth:`to_dict`
    returns the document it was parsed from, so a manifest survives a round
    trip through the file unchanged.

    The ``feature_layout_version`` is the version of the feature layout the
    weights were fitted on.  It is required in the document: a manifest that
    does not carry it is refused, because the loader could not tell the
    weights of a model of an older layout from the weights of a model of the
    layout of this build.
    """

    name: str
    version: str
    epoch: int
    trained_on: str
    variant: str
    problem: str
    task: str
    input: InputSpec
    output: OutputSpec
    context: ContextSpec
    state: StateSpec
    network: NetworkSpec
    weights: tuple[WeightSpec, ...]
    checksum: str
    deterministic: bool
    notes: str = ""
    format_version: int = FORMAT_VERSION
    feature_layout_version: int = FEATURE_LAYOUT_VERSION

    # -- convenience ------------------------------------------------------
    @property
    def sample_rate(self) -> int:
        """The rate the model runs at."""
        return self.input.sample_rate

    @property
    def hop(self) -> int:
        """The hop of the model in samples."""
        return self.input.hop

    @property
    def frame_size(self) -> int:
        """The frame size of the model in samples."""
        return self.input.frame_size

    @property
    def parameters(self) -> int:
        """The number of values the weights hold."""
        return sum(spec.size for spec in self.weights)

    def weight(self, name: str) -> WeightSpec:
        """Return the description of the weight called ``name``."""
        for spec in self.weights:
            if spec.name == name:
                return spec
        raise KeyError(name)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        """Return the manifest as a plain JSON-ready dictionary."""
        return {
            "format": FORMAT_NAME,
            "format_version": int(self.format_version),
            "feature_layout_version": int(self.feature_layout_version),
            "name": self.name,
            "version": self.version,
            "epoch": int(self.epoch),
            "trained_on": self.trained_on,
            "variant": self.variant,
            "problem": self.problem,
            "task": self.task,
            "input": self.input.to_dict(),
            "output": self.output.to_dict(),
            "context": self.context.to_dict(),
            "state": self.state.to_dict(),
            "network": self.network.to_dict(),
            "weights": [spec.to_dict() for spec in self.weights],
            "checksum": self.checksum,
            "deterministic": bool(self.deterministic),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Manifest":
        """Parse a manifest document, refusing everything that is not exact.

        Parameters
        ----------
        data : dict
            The manifest document.

        Returns
        -------
        Manifest
            The parsed manifest.

        Raises
        ------
        ManifestError
            When the document is malformed, carries an unknown field, names a
            feature layout this build does not read, or describes a model the
            engine cannot run.
        """
        if not isinstance(data, dict):
            raise ManifestError("the manifest must be an object")
        _no_unknown(data, FIELDS, "the manifest")
        fmt = _require(data, "format", str, "the manifest")
        if fmt != FORMAT_NAME:
            raise ManifestError(
                f"the manifest declares the format {fmt!r}, not {FORMAT_NAME!r}"
            )
        version = _integer(
            _require(data, "format_version", int, "the manifest"), "format_version", "the manifest"
        )
        if version < 1:
            raise ManifestError(f"the manifest declares the format version {version}")
        if version > FORMAT_VERSION:
            raise ManifestError(
                f"the manifest declares the format version {version}, which is newer "
                f"than the {FORMAT_VERSION} this build understands"
            )

        layout = _feature_layout(data)

        name = _require(data, "name", str, "the manifest")
        if not _NAME_RE.match(name):
            raise ManifestError(
                f"the model name {name!r} is not a plain name (letters, digits, "
                "'_', '-' and '.' only)"
            )
        model_version = _require(data, "version", str, "the manifest")
        if not _VERSION_RE.match(model_version):
            raise ManifestError(
                f"the model version {model_version!r} is not of the form major.minor.patch"
            )
        epoch = _integer(_require(data, "epoch", int, "the manifest"), "epoch", "the manifest")
        if epoch < 0:
            raise ManifestError("the epoch cannot be negative")
        trained_on = _require(data, "trained_on", str, "the manifest")
        try:
            date.fromisoformat(trained_on)
        except ValueError:
            raise ManifestError(
                f"the training date {trained_on!r} is not a date of the form YYYY-MM-DD"
            ) from None
        variant = _require(data, "variant", str, "the manifest")
        if not variant:
            raise ManifestError("the variant cannot be empty")
        problem = _require(data, "problem", str, "the manifest")
        if problem not in PROBLEMS:
            raise ManifestError(
                f"the problem {problem!r} is not one the product knows "
                f"({', '.join(PROBLEMS)})"
            )
        task = _require(data, "task", str, "the manifest")

        input_spec = InputSpec.from_dict(_require(data, "input", dict, "the manifest"))
        output_spec = OutputSpec.from_dict(_require(data, "output", dict, "the manifest"))
        context = ContextSpec.from_dict(
            _require(data, "context", dict, "the manifest"), input_spec.hop
        )
        state = StateSpec.from_dict(_require(data, "state", dict, "the manifest"))
        network = NetworkSpec.from_dict(_require(data, "network", dict, "the manifest"))

        weights_raw = _require(data, "weights", list, "the manifest")
        weights = tuple(
            WeightSpec.from_dict(entry, f"the manifest.weights[{index}]")
            for index, entry in enumerate(weights_raw)
        )
        if not weights:
            raise ManifestError("the manifest declares no weights")
        names = [spec.name for spec in weights]
        if len(set(names)) != len(names):
            raise ManifestError("the manifest declares the same weight twice")
        keys = [spec.key for spec in weights]
        if len(set(keys)) != len(keys):
            raise ManifestError("the manifest declares the same weight key twice")

        # The numbers have to add up.
        if network.kind == "sequence":
            # A sequence network reads the frames before it through its own
            # convolution stack, so the features must not read history as
            # well: the memory of the model is the memory of the stack.
            if input_spec.features.delta_frames != 0:
                raise ManifestError(
                    "a sequence network reads the frames before it through its own "
                    "convolution stack, so its features must not read a history of "
                    f"their own (the manifest declares {input_spec.features.delta_frames} "
                    "frames of difference)"
                )
            expected_context = network.receptive_span - 1
            if context.frames != expected_context:
                raise ManifestError(
                    f"the manifest declares a context of {context.frames} frames, but "
                    f"its convolution stack reads {expected_context} frames before a "
                    "frame"
                )
        elif context.frames != input_spec.features.delta_frames:
            raise ManifestError(
                f"the manifest declares a context of {context.frames} frames, but its "
                f"features read {input_spec.features.delta_frames} frames of history"
            )
        if output_spec.bins != input_spec.bins:
            raise ManifestError(
                f"the output covers {output_spec.bins} bins, but the frame of "
                f"{input_spec.frame_size} samples has {input_spec.bins}"
            )
        expected_in = input_spec.features.dimension
        if network.layers[0][0] != expected_in:
            raise ManifestError(
                f"the first layer reads {network.layers[0][0]} values, but the "
                f"features produce {expected_in}"
            )
        if network.layers[-1][1] != input_spec.features.bands:
            raise ManifestError(
                f"the last layer writes {network.layers[-1][1]} values, but the model "
                f"has {input_spec.features.bands} bands"
            )
        held = sum(spec.size for spec in weights)
        if network.parameters != held:
            raise ManifestError(
                f"the network declares {network.parameters} parameters, but the "
                f"weights hold {held}"
            )
        expected_names = _expected_weight_names(network)
        if [spec.name for spec in weights] != list(expected_names):
            raise ManifestError(
                "the weights are not the ones the network needs: the network of "
                f"{len(network.layers)} layer(s) needs {', '.join(expected_names)}"
            )
        expected_shapes = _expected_weight_shapes(network)
        for spec, shape in zip(weights, expected_shapes):
            if spec.shape != shape:
                raise ManifestError(
                    f"the weight {spec.name!r} has the shape {spec.shape}, but the "
                    f"network needs {shape}"
                )

        checksum = _require(data, "checksum", str, "the manifest")
        if not _CHECKSUM_RE.match(checksum):
            raise ManifestError(
                f"the checksum {checksum!r} is not of the form sha256:<64 hex digits>"
            )
        deterministic = _require(data, "deterministic", bool, "the manifest")
        notes = data.get("notes", "")
        if not isinstance(notes, str):
            raise ManifestError("the notes must be text")

        return cls(
            name=name,
            version=model_version,
            epoch=epoch,
            trained_on=trained_on,
            variant=variant,
            problem=problem,
            task=task,
            input=input_spec,
            output=output_spec,
            context=context,
            state=state,
            network=network,
            weights=weights,
            checksum=checksum,
            deterministic=deterministic,
            notes=notes,
            format_version=version,
            feature_layout_version=layout,
        )


def _expected_weight_names(network: NetworkSpec) -> tuple[str, ...]:
    """The names the weights of a network carry: ``w0, b0, w1, b1, ...``."""
    names: list[str] = []
    for index in range(len(network.layers)):
        names.append(f"w{index}")
        names.append(f"b{index}")
    return tuple(names)


def _expected_weight_shapes(network: NetworkSpec) -> tuple[tuple[int, ...], ...]:
    """The shapes the weights of a network carry: a pair per layer.

    The fully connected kind holds a matrix of ``(in, out)`` and a bias of
    ``(out,)``; the sequence kind holds a kernel of ``(in, out, kernel)``
    and the same bias.
    """
    shapes: list[tuple[int, ...]] = []
    for layer in network.layers:
        if network.kind == "sequence":
            width_in, width_out, kernel, _, _ = layer
            shapes.append((width_in, width_out, kernel))
        else:
            width_in, width_out, _ = layer
            shapes.append((width_in, width_out))
        shapes.append((width_out,))
    return tuple(shapes)
