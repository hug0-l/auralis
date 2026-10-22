"""
aurcore.models.engine — the inference engine.

The engine works in the spectral domain: it reads the magnitude spectrum of
a frame, turns it into the features the model publishes, runs the network
and hands back what the model produces — a spectral mask or a spectrum.

Determinism
-----------
The engine is deterministic by construction, not by luck:

* the weights are ``float32``, the arithmetic is ``float64``;
* a dense layer is a **fixed ordered accumulation**: the contributions of
  the inputs are added in the order of the input index, one vector
  operation at a time.  No reduction library decides the order for us, so
  two runs of the same input give the same bits on every machine and every
  build — not only on the machine the model was trained on;
* the features are reduced with integer counters, so the bands are summed in
  the order of the bins;
* nothing in the engine reads the clock, the random generator or the
  environment.

The same input therefore gives the same output, bit for bit, every time.

Frames, not samples
-------------------
A model runs once per **frame**, not once per sample: a frame of 1024
samples with a hop of 256 at 48 kHz is 187.5 inferences per second of
audio.  The state the model needs (the features of the frames before) is
what the manifest calls the *context*, and the manifest publishes it.

The feature layout
------------------
The features of a model are the **levels of the bands of a frame**, and a
level is read on a scale that says three things: the reference it is
measured against, the floor below which a band counts as empty, and the
ceiling above which it is clipped.

* the **reference** is **0 dBFS**: the magnitude of a full scale sinusoid
  in the transform of the frame.  The transform of the engine is not
  normalised, so that magnitude is ``frame_size / 4`` (a Hann window has
  a coherent gain of one half and the two halves of a cosine add up), and
  the engine divides by it.  A band at -3 dBFS therefore reads -3 dB, and
  a band at -40 dBFS reads -40 dB: **the height of the spectrum is
  carried**, and a band that is louder than another one says so;
* the **floor** is the ``floor_db`` of the manifest (-100 dBFS by
  default).  A bin below the floor counts as silent, and a band that
  carries nothing at all - no bin, or no energy - reads as the floor, so
  it is the **quietest** value of the layout and not the loudest;
* the **ceiling** is :data:`FEATURE_CEILING_DB` (+12 dBFS).  It is above
  digital full scale on purpose: the loudest band a full scale signal can
  produce (the fundamental of a full scale square wave) sits 2 dB above
  full scale, and a band above the ceiling has to stay tellable from a
  band that sits exactly at the ceiling.

The level of a band is the **mean of the levels of its bins** in dB, and
the value the network reads is that level mapped from the floor to the
ceiling onto ``[0, 1]``.  The mapping is published: every model declares
the floor it was trained with, and :meth:`SpectralModel.feature_layout`
returns the whole layout.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..dsp.stft import STFT
from .errors import ConfigMismatchError, ModelError, WeightsMismatchError
from .manifest import Manifest

__all__ = [
    "ModelOutput",
    "SpectralModel",
    "ModelRunner",
    "run_offline",
    "reference_magnitude",
    "FEATURE_REFERENCE_DB",
    "FEATURE_CEILING_DB",
]


# ---------------------------------------------------------------------------
# the feature layout
# ---------------------------------------------------------------------------
#: The reference of the feature layout: 0 dB is a full scale sinusoid.
FEATURE_REFERENCE_DB = 0.0

#: The ceiling of the feature layout, in dB above the reference.  A band
#: above the ceiling is clipped to the top of the scale.
FEATURE_CEILING_DB = 12.0


def reference_magnitude(frame_size: int) -> float:
    """The magnitude of a full scale sinusoid in the transform of a frame.

    The transform of the engine is not normalised: the magnitude of a bin
    is the raw transform of the windowed frame.  This is the number that
    turns such a magnitude into a level in dBFS.

    Parameters
    ----------
    frame_size : int
        The length of the frame in samples.

    Returns
    -------
    float
        The magnitude a full scale sinusoid at a bin centre reaches.  It is
        ``frame_size / 4`` for a Hann window, which is the window the
        engine analyses with, and it is within 0.1 dB of the magnitude a
        real full scale tone reaches.
    """
    return float(frame_size) / 4.0


# ---------------------------------------------------------------------------
# the output
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelOutput:
    """What a model produced for one frame.

    Attributes
    ----------
    kind : str
        ``"spectral_mask"`` or ``"spectrum"``, as the manifest declares.
    bands : numpy.ndarray
        The value per band, shape ``(bands,)``.
    values : numpy.ndarray
        The value per frequency bin, shape ``(bins,)``.  For a mask it is the
        gain that is applied to the magnitude; for a spectrum it is the
        linear magnitude the model produced, in the scale of the
        transform of the frame (a full scale sinusoid reaches
        ``frame_size / 4``).
    """

    kind: str
    bands: np.ndarray
    values: np.ndarray

    def apply(self, magnitude: np.ndarray) -> np.ndarray:
        """Apply the output to a magnitude spectrum.

        Parameters
        ----------
        magnitude : numpy.ndarray
            The magnitudes, shape ``(bins,)``.

        Returns
        -------
        numpy.ndarray
            The modified magnitudes.  For a mask the magnitudes are scaled
            by the gain; for a spectrum the magnitudes are replaced by the
            spectrum the model produced, because a spectrum is a
            description and not a gain.
        """
        mag = np.asarray(magnitude, dtype=np.float64)
        if self.kind == "spectral_mask":
            return mag * self.values
        return self.values * np.ones_like(mag)


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

#: A layer of the fully connected kind: the matrix, the bias, the activation.
_DenseLayer = tuple[np.ndarray, np.ndarray, str]

#: A layer of the sequence kind: the kernel, the bias, the activation, the
#: width of the kernel and the distance it reaches.
_ConvLayer = tuple[np.ndarray, np.ndarray, str, int, int]


class _SequenceHistory:
    """The frames before the current one, as every layer of a sequence reads them.

    Each layer keeps a **ring buffer** of the inputs it reads: the oldest
    input is the one ``(kernel - 1) * dilation`` frames back.  A frame that
    has not been read yet is zero, which is what the batched path pads
    with — the two paths therefore see the same numbers.
    """

    def __init__(self, layers: list[_ConvLayer]) -> None:
        """Build the history of a stack of convolution layers."""
        self._buffers: list[np.ndarray] = []
        self._counts: list[int] = []
        self._pointers: list[int] = []
        for matrix, _, _, kernel, dilation in layers:
            need = (kernel - 1) * dilation
            self._buffers.append(
                np.zeros((matrix.shape[0], need + 1), dtype=np.float64)
            )
            self._counts.append(0)
            self._pointers.append(0)

    def clear(self) -> None:
        """Forget every frame: the history starts from nothing."""
        for index, buffer in enumerate(self._buffers):
            buffer[:, :] = 0.0
            self._counts[index] = 0
            self._pointers[index] = 0

    def count(self, index: int = 0) -> int:
        """How many frames the layer holds."""
        return self._counts[index]

    def push(self, index: int, vector: np.ndarray) -> None:
        """Add the input of the frame that is being read now."""
        buffer = self._buffers[index]
        width = buffer.shape[1]
        pointer = self._pointers[index]
        buffer[:, pointer] = vector
        self._pointers[index] = (pointer + 1) % width
        self._counts[index] = min(self._counts[index] + 1, width)

    def push_many(self, index: int, columns: np.ndarray) -> None:
        """Add the inputs of many frames at once, oldest first.

        The columns are the inputs of the frames, one frame per row: the
        ring of the layer holds one **frame** per column.
        """
        buffer = self._buffers[index]
        width = buffer.shape[1]
        count = columns.shape[0]
        pointer = self._pointers[index]
        if count >= width:
            buffer[:, :] = columns[-width:].T
            self._pointers[index] = 0
            self._counts[index] = width
            return
        slots = (pointer + np.arange(count)) % width
        buffer[:, slots] = columns.T
        self._pointers[index] = (pointer + count) % width
        self._counts[index] = min(self._counts[index] + count, width)

    def back(self, index: int, frames: int) -> np.ndarray:
        """The input of the frame ``frames`` before the current one."""
        buffer = self._buffers[index]
        width = buffer.shape[1]
        pointer = self._pointers[index]
        return buffer[:, (pointer - 1 - frames) % width]

    def oldest_first(self, index: int) -> np.ndarray:
        """The inputs the layer holds, oldest first."""
        buffer = self._buffers[index]
        count = self._counts[index]
        if count == 0:
            return np.zeros((buffer.shape[0], 0), dtype=np.float64)
        pointer = self._pointers[index]
        rolled = np.concatenate([buffer[:, pointer:], buffer[:, :pointer]], axis=1)
        return rolled[:, -count:]


class SpectralModel:
    """A model that is ready to run.

    The object holds the manifest and the weights, and it knows how to turn
    the magnitude spectrum of a frame into what the model produces.
    """

    def __init__(self, manifest: Manifest, weights: dict[str, np.ndarray]):
        """Build a runnable model from a manifest and its weights.

        Parameters
        ----------
        manifest : Manifest
            The manifest of the model.
        weights : dict of str to numpy.ndarray
            The weights, keyed by their manifest key.

        Raises
        ------
        WeightsMismatchError
            When a weight the manifest declares is missing, or when its
            shape or type does not match.
        """
        self.manifest = manifest
        self.weights = weights
        self._layers, self._convs = self._build_layers(manifest, weights)
        self._band_of_bin = self._build_band_map(manifest)
        self._reference_mag = reference_magnitude(manifest.input.frame_size)
        self._floor_db = float(manifest.input.features.floor_db)
        self._ceiling_db = float(FEATURE_CEILING_DB)
        self._floor_lin = self._reference_mag * 10.0 ** (self._floor_db / 20.0)

    # -- construction ----------------------------------------------------
    @staticmethod
    def _build_layers(
        manifest: Manifest, weights: dict[str, np.ndarray]
    ) -> tuple[list[_DenseLayer], list[_ConvLayer]]:
        """Bind the weight arrays to the layers of the network.

        The fully connected kind and the sequence kind are different
        shapes of the same thing, so they are kept apart: the layers of
        one kind are empty for a model of the other kind.
        """
        layers: list[_DenseLayer] = []
        convs: list[_ConvLayer] = []
        for index, layer in enumerate(manifest.network.layers):
            matrix = weights.get(f"w{index}")
            bias = weights.get(f"b{index}")
            if matrix is None or bias is None:
                raise WeightsMismatchError(
                    f"the model {manifest.name!r} needs the weights "
                    f"'w{index}' and 'b{index}', which are not there"
                )
            matrix = np.asarray(matrix, dtype=np.float64)
            bias = np.asarray(bias, dtype=np.float64)
            if manifest.network.kind == "sequence":
                width_in, width_out, kernel, dilation, activation = layer
                if matrix.shape != (width_in, width_out, kernel):
                    raise WeightsMismatchError(
                        f"the weight 'w{index}' has the shape {matrix.shape}, but the "
                        f"network declares ({width_in}, {width_out}, {kernel})"
                    )
                if bias.shape != (width_out,):
                    raise WeightsMismatchError(
                        f"the weight 'b{index}' has the shape {bias.shape}, but the "
                        f"network declares ({width_out},)"
                    )
                convs.append((matrix, bias, activation, kernel, dilation))
                continue
            width_in, width_out, activation = layer
            if matrix.shape != (width_in, width_out):
                raise WeightsMismatchError(
                    f"the weight 'w{index}' has the shape {matrix.shape}, but the "
                    f"network declares ({width_in}, {width_out})"
                )
            if bias.shape != (width_out,):
                raise WeightsMismatchError(
                    f"the weight 'b{index}' has the shape {bias.shape}, but the "
                    f"network declares ({width_out},)"
                )
            layers.append((matrix, bias, activation))
        return layers, convs

    def _build_band_map(self, manifest: Manifest) -> np.ndarray:
        """Return, for every bin, the band it belongs to."""
        rate = manifest.input.sample_rate
        bins = manifest.input.bins
        edges = manifest.input.features.band_edges_hz
        centres = (np.arange(bins, dtype=np.float64) + 0.5) * rate / manifest.input.frame_size
        index = np.searchsorted(np.asarray(edges, dtype=np.float64), centres, side="right") - 1
        return np.clip(index, 0, manifest.input.features.bands - 1).astype(np.intp)

    @property
    def band_of_bin(self) -> np.ndarray:
        """For every bin of a frame, the band it belongs to."""
        return self._band_of_bin

    @property
    def reference_magnitude(self) -> float:
        """The magnitude of a full scale sinusoid in this model's transform.

        It is the reference of the feature layout: 0 dB is this magnitude.
        """
        return self._reference_mag

    def feature_layout(self) -> dict:
        """The layout the features of this model are read in.

        Returns
        -------
        dict
            The kind of the layout, the reference it is measured against,
            the floor, the ceiling and the range the values of the network
            live in.
        """
        features = self.manifest.input.features
        return {
            "kind": features.kind,
            "reference_db": float(FEATURE_REFERENCE_DB),
            "reference_magnitude": self._reference_mag,
            "floor_db": self._floor_db,
            "ceiling_db": self._ceiling_db,
            "range": (0.0, 1.0),
            "empty": 0.0,
        }

    # -- the forward pass -------------------------------------------------
    @staticmethod
    def _dense(weights: np.ndarray, bias: np.ndarray, vector: np.ndarray) -> np.ndarray:
        """One dense layer, with a fixed order of accumulation.

        The contribution of every input is added in the order of the input
        index.  The order is part of the result, so it is fixed here and not
        left to a reduction library.
        """
        out = np.array(bias, dtype=np.float64, copy=True)
        for index in range(weights.shape[0]):
            out += weights[index] * vector[index]
        return out

    def _network(self, vector: np.ndarray) -> np.ndarray:
        """Run the network over one feature vector."""
        activation = vector
        for matrix, bias, kind in self._layers:
            activation = self._dense(matrix, bias, activation)
            if kind == "relu":
                np.maximum(activation, 0.0, out=activation)
        return activation

    def network_batch(self, features: np.ndarray) -> np.ndarray:
        """Run the network over many feature vectors at once.

        The **accumulation order is the one of the frame by frame path**:
        the contribution of every input is added in the order of the input
        index, one vector operation at a time, exactly as
        :meth:`_dense` does for one frame.  No reduction library decides the
        order for us, so the result is **bit for bit** the result of running
        :meth:`_network` over the frames one by one — the batch is a
        shortcut, not a different answer.

        This is what makes a **per sample** model affordable offline: the
        frames of a file are evaluated in one pass instead of one Python
        call per frame.  The cost of the batch is the cost of the arithmetic
        and not the cost of the interpreter.

        Parameters
        ----------
        features : numpy.ndarray
            The feature vectors, shape ``(dimension, frames)``.

        Returns
        -------
        numpy.ndarray
            What the network produced, shape ``(out, frames)``.
        """
        array = np.asarray(features, dtype=np.float64)
        if array.ndim != 2:
            raise ModelError(
                "the features of a batch must be 2-D (dimension, frames)"
            )
        # The frames are the **last** axis of the work: the accumulation of a
        # dense layer adds the contribution of every input to the whole
        # output, and a row of the features is contiguous, so the
        # accumulation reads and writes memory in order.
        activation = np.ascontiguousarray(array)
        for matrix, bias, kind in self._layers:
            out = np.empty((matrix.shape[1], activation.shape[1]), dtype=np.float64)
            out[:] = bias[:, None]
            scratch = np.empty_like(out)
            for index in range(matrix.shape[0]):
                np.multiply(
                    matrix[index][:, None], activation[index][None, :], out=scratch
                )
                np.add(out, scratch, out=out)
            activation = out
            if kind == "relu":
                np.maximum(activation, 0.0, out=activation)
        return activation

    # -- the features -----------------------------------------------------
    # -- the sequence network ----------------------------------------------
    @property
    def is_sequence(self) -> bool:
        """True when the network reads the frames before the current one.

        A sequence network is a stack of **causal** convolutions over the
        time axis, so the answer for a frame is a function of the frames
        before it.  That memory is what the fully connected kind does not
        have.
        """
        return bool(self._convs)

    def sequence_state(self) -> _SequenceHistory:
        """A fresh history of a sequence network: one ring per layer."""
        return _SequenceHistory(self._convs)

    def network_sequence(
        self, levels: np.ndarray, history: _SequenceHistory
    ) -> np.ndarray:
        """Run the sequence network over one frame.

        The order of the accumulation is the order of the input channel
        and then the order of the tap, which is the order the batched
        path uses: the two paths are the same answer and not only the
        same arithmetic.
        """
        current = np.asarray(levels, dtype=np.float64)
        history.push(0, current)
        for index, (matrix, bias, activation, kernel, dilation) in enumerate(self._convs):
            out = np.array(bias, dtype=np.float64, copy=True)
            for channel in range(matrix.shape[0]):
                for tap in range(kernel):
                    value = history.back(index, tap * dilation)
                    out += matrix[channel, :, tap] * value[channel]
            if activation == "relu":
                np.maximum(out, 0.0, out=out)
            if index + 1 < len(self._convs):
                history.push(index + 1, out)
            current = out
        return current

    def network_batch_sequence(
        self, levels: np.ndarray, history: _SequenceHistory
    ) -> np.ndarray:
        """Run the sequence network over many frames at once.

        The accumulation order is the one of :meth:`network_sequence`:
        the channel first and then the tap, so the two paths agree to the
        bit.  The frames the history does not hold are read as zero, which
        is what the frame by frame path reads as well.
        """
        array = np.asarray(levels, dtype=np.float64)
        frames = array.shape[1]
        current = array
        # The frames the history holds are the ones **before** this block:
        # they are read first, so the block cannot overwrite them.
        prefixes = [history.oldest_first(index) for index in range(len(self._convs))]
        history.push_many(0, array.T)
        for index, (matrix, bias, activation, kernel, dilation) in enumerate(self._convs):
            need = (kernel - 1) * dilation
            held = prefixes[index]
            take = min(held.shape[1], need)
            past = held[:, held.shape[1] - take :] if take else held[:, :0]
            missing = need - past.shape[1]
            if missing > 0:
                prefix = np.concatenate(
                    [np.zeros((current.shape[0], missing), dtype=np.float64), past], axis=1
                )
            else:
                prefix = past
            full = np.concatenate([prefix, current], axis=1)
            out = np.repeat(bias[:, None], frames, axis=1)
            for channel in range(matrix.shape[0]):
                for tap in range(kernel):
                    back = tap * dilation
                    shifted = full[channel, need - back : need - back + frames]
                    out += matrix[channel, :, tap][:, None] * shifted[None, :]
            if activation == "relu":
                np.maximum(out, 0.0, out=out)
            if index + 1 < len(self._convs):
                history.push_many(index + 1, out.T)
            current = out
        return current

    def band_levels(self, magnitude: np.ndarray) -> np.ndarray:
        """The level of every band, read out of the magnitude spectrum.

        The level of a band is the **mean of the levels of its bins** in
        dBFS: the magnitude of a bin is divided by the magnitude of a full
        scale sinusoid, so a band at -3 dBFS reads -3 dB and a band at
        -40 dBFS reads -40 dB.  The value the network reads is that level
        mapped from the floor of the manifest to the ceiling of the layout
        onto ``[0, 1]``.

        A band that carries nothing at all - no bin of the frame, or no
        energy in it - reads as the **floor**, which is the quietest value
        of the layout.  It never reads as the loudest one.

        Parameters
        ----------
        magnitude : numpy.ndarray
            The magnitudes of one frame, shape ``(bins,)``.

        Returns
        -------
        numpy.ndarray
            The band levels on the scale of the features: ``0`` is the
            floor of the manifest, ``1`` is the ceiling of the layout.
        """
        mag = np.asarray(magnitude, dtype=np.float64)
        if mag.shape[0] != self.manifest.input.bins:
            raise ModelError(
                f"the model {self.manifest.name!r} reads {self.manifest.input.bins} "
                f"bins, but the frame has {mag.shape[0]}"
            )
        floored = np.maximum(mag, self._floor_lin)
        decibels = 20.0 * np.log10(floored / self._reference_mag)
        bands = self.manifest.input.features.bands
        sums = np.bincount(self._band_of_bin, weights=decibels, minlength=bands)
        counts = np.bincount(self._band_of_bin, minlength=bands)
        safe = np.where(counts > 0, counts, 1)
        mean = sums / safe
        # A band without a bin carries nothing at all: it is the floor and
        # not a level.  A mean of zero decibels would be the loudest value
        # of the layout, which is exactly what an empty band is not.
        mean = np.where(counts > 0, mean, self._floor_db)
        span = self._ceiling_db - self._floor_db
        levels = (mean - self._floor_db) / span
        return np.clip(levels, 0.0, 1.0)

    def band_levels_batch(self, magnitudes: np.ndarray) -> np.ndarray:
        """The level of every band, of every frame at once.

        The reduction is the one of :meth:`band_levels`: the bins of a band
        are added **in the order of the bin index**, so the batch and the
        frame by frame path agree bit for bit.  The counter is an integer
        one, so the order of the bins is the order of the reduction and not
        the order a reduction library happens to pick.

        Parameters
        ----------
        magnitudes : numpy.ndarray
            The magnitudes of the frames, shape ``(bins, frames)``.

        Returns
        -------
        numpy.ndarray
            The band levels on the scale of the features, shape
            ``(bands, frames)``.
        """
        mag = np.asarray(magnitudes, dtype=np.float64)
        if mag.ndim != 2:
            raise ModelError(
                "the magnitudes of a batch must be 2-D (bins, frames)"
            )
        if mag.shape[0] != self.manifest.input.bins:
            raise ModelError(
                f"the model {self.manifest.name!r} reads {self.manifest.input.bins} "
                f"bins, but the frames have {mag.shape[0]}"
            )
        floored = np.maximum(mag, self._floor_lin)
        decibels = 20.0 * np.log10(floored / self._reference_mag)
        bands = self.manifest.input.features.bands
        sums = np.zeros((bands, mag.shape[1]), dtype=np.float64)
        for index in range(mag.shape[0]):
            sums[self._band_of_bin[index]] += decibels[index]
        counts = np.bincount(self._band_of_bin, minlength=bands)
        safe = np.where(counts > 0, counts, 1)
        mean = sums / safe[:, None]
        mean = np.where(counts[:, None] > 0, mean, self._floor_db)
        span = self._ceiling_db - self._floor_db
        levels = (mean - self._floor_db) / span
        return np.clip(levels, 0.0, 1.0)

    # -- the prediction ---------------------------------------------------
    def predict(self, magnitude: np.ndarray) -> ModelOutput:
        """Run the model over the magnitude spectrum of one frame.

        A model that reads a context needs that context; this entry point
        has none, so the history of the frame is empty and the differences
        are zero.  Use :class:`ModelRunner` for the streaming path.

        Parameters
        ----------
        magnitude : numpy.ndarray
            The magnitudes of one frame, shape ``(bins,)``.

        Returns
        -------
        ModelOutput
            The mask or the spectrum the model produced.
        """
        runner = ModelRunner(self)
        return runner.process_frame(magnitude)

    # -- description ------------------------------------------------------
    def describe(self) -> dict:
        """Return a JSON-safe description of the model."""
        manifest = self.manifest
        return {
            "name": manifest.name,
            "version": manifest.version,
            "epoch": manifest.epoch,
            "trained_on": manifest.trained_on,
            "variant": manifest.variant,
            "problem": manifest.problem,
            "task": manifest.task,
            "sample_rate": manifest.input.sample_rate,
            "channels": manifest.input.channels,
            "frame_size": manifest.input.frame_size,
            "hop": manifest.input.hop,
            "output": manifest.output.kind,
            "context_frames": manifest.context.frames,
            "context_samples": manifest.context.samples,
            "state_scope": manifest.state.scope,
            "state_cleared": manifest.state.cleared,
            "parameters": manifest.parameters,
            "checksum": manifest.checksum,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<SpectralModel {self.manifest.name} {self.manifest.version} "
            f"problem={self.manifest.problem} parameters={self.manifest.parameters}>"
        )


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
class ModelRunner:
    """The stateful runner of a model.

    The runner owns the history the model needs: the features of the frames
    before the current one.  How long that history lives is the model's
    own decision, published in the manifest:

    * ``cleared = "block"``: the history is dropped at the start of every
      block, so the model sees each block on its own;
    * ``cleared = "file"``: the history lives until :meth:`reset` is called
      at the end of the file;
    * ``cleared = "session"``: the history lives until :meth:`reset_session`
      is called at the end of the session.
    """

    def __init__(self, model: SpectralModel):
        """Build a runner for a model."""
        self.model = model
        self._delta = model.manifest.input.features.delta_frames
        self._history: list[np.ndarray] = []
        self._cleared = model.manifest.state.cleared
        self._sequence = model.sequence_state() if model.is_sequence else None

    # -- state ------------------------------------------------------------
    @property
    def frames_read(self) -> int:
        """How many frames of history the runner holds."""
        if self._sequence is not None:
            return self._sequence.count(0)
        return len(self._history)

    def reset(self) -> None:
        """Drop the history: the end of a file."""
        self._history.clear()
        if self._sequence is not None:
            self._sequence.clear()

    def reset_session(self) -> None:
        """Drop the history: the end of a session."""
        self._history.clear()
        if self._sequence is not None:
            self._sequence.clear()

    def _clear_if_block(self) -> None:
        """Drop the history when the model is cleared per block."""
        if self._cleared == "block":
            self._history.clear()
            if self._sequence is not None:
                self._sequence.clear()

    # -- the forward pass -------------------------------------------------
    def _input_vector(self, levels: np.ndarray) -> np.ndarray:
        """Build the input vector of the network from the band levels."""
        parts = [levels]
        for index in range(self._delta):
            if index < len(self._history):
                previous = self._history[len(self._history) - 1 - index]
            else:
                previous = levels
            parts.append(levels - previous)
        return np.concatenate(parts)

    def _output(self, raw: np.ndarray) -> ModelOutput:
        """Turn the raw output of the network into what the model produces."""
        manifest = self.model.manifest
        if manifest.output.kind == "spectral_mask":
            values = 1.0 / (1.0 + np.exp(-raw))
            values = np.clip(values, 0.0, 1.0)
        else:
            # A spectrum is produced in dBFS, and the magnitudes of the
            # transform are the scale the offline path applies it in.
            values = self.model.reference_magnitude * np.power(10.0, raw / 20.0)
        per_bin = values[self.model._band_of_bin]
        return ModelOutput(kind=manifest.output.kind, bands=values, values=per_bin)

    def process_frame(self, magnitude: np.ndarray) -> ModelOutput:
        """Run the model over one frame and keep the history.

        Parameters
        ----------
        magnitude : numpy.ndarray
            The magnitudes of one frame, shape ``(bins,)``.

        Returns
        -------
        ModelOutput
            The mask or the spectrum the model produced.
        """
        levels = self.model.band_levels(np.asarray(magnitude, dtype=np.float64))
        if self._sequence is not None:
            # A sequence network reads the frames before this one through
            # its own history, so there is no feature vector to build.
            raw = self.model.network_sequence(levels, self._sequence)
            return self._output(raw)
        vector = self._input_vector(levels)
        raw = self.model._network(vector)
        output = self._output(raw)
        if self._delta:
            self._history.append(levels)
            if len(self._history) > self._delta:
                del self._history[0]
        return output

    def process(self, magnitudes: np.ndarray) -> np.ndarray:
        """Run the model over a block of frames.

        Parameters
        ----------
        magnitudes : numpy.ndarray
            The magnitudes of the frames, shape ``(bins, frames)``.

        Returns
        -------
        numpy.ndarray
            The per-bin values the model produced, shape ``(bins, frames)``.
            For a mask that is the gain per bin and frame; for a spectrum it
            is the spectrum the model produced.
        """
        self._clear_if_block()
        array = np.asarray(magnitudes, dtype=np.float64)
        if array.ndim == 1:
            array = array[:, None]
        if array.ndim != 2:
            raise ModelError("the magnitudes must be 2-D (bins, frames)")
        if array.shape[0] != self.model.manifest.input.bins:
            raise ModelError(
                f"the model {self.model.manifest.name!r} reads "
                f"{self.model.manifest.input.bins} bins, but the frames have "
                f"{array.shape[0]}"
            )
        out = np.empty_like(array)
        for index in range(array.shape[1]):
            output = self.process_frame(array[:, index])
            out[:, index] = output.values
        return out

    # -- the batched forward pass ------------------------------------------
    def _input_matrix(self, levels: np.ndarray) -> np.ndarray:
        """The feature vectors of every frame, as a matrix.

        The runner holds the history of the frames **before** this block, so
        the first frames of the block read that history and the frames after
        them read the frames before them.  A frame that has no history reads
        the frame itself and its difference is zero — the rule the frame by
        frame path follows.

        Parameters
        ----------
        levels : numpy.ndarray
            The band levels of the frames, shape ``(bands, frames)``.

        Returns
        -------
        numpy.ndarray
            The feature vectors of every frame, shape ``(dimension, frames)``.
        """
        frames = levels.shape[1]
        parts = [levels]
        for index in range(self._delta):
            # The first difference is against the frame before, the second
            # against the frame before that: the lag of the difference is
            # one more than its index.
            lag = index + 1
            previous = levels.copy()
            if lag < frames:
                previous[:, lag:] = levels[:, : frames - lag]
            held = len(self._history)
            for back in range(1, min(held, lag) + 1):
                previous[:, lag - back] = self._history[held - back]
            parts.append(levels - previous)
        return np.concatenate(parts, axis=0)

    def _output_batch(self, raw: np.ndarray) -> np.ndarray:
        """Turn the raw output of the network into what the model produces."""
        manifest = self.model.manifest
        if manifest.output.kind == "spectral_mask":
            values = 1.0 / (1.0 + np.exp(-raw))
            values = np.clip(values, 0.0, 1.0)
        else:
            values = self.model.reference_magnitude * np.power(10.0, raw / 20.0)
        return values

    def process_batch(self, magnitudes: np.ndarray) -> np.ndarray:
        """Run the model over a block of frames, every frame at once.

        The result is **bit for bit** the result of :meth:`process`: the same
        features, the same order of the accumulation, the same transfer.  The
        difference is the cost — the frames are evaluated in one pass, so a
        model that reads a frame every *sample* costs the arithmetic and not
        the interpreter.  That is what makes the per sample path affordable
        offline.

        Parameters
        ----------
        magnitudes : numpy.ndarray
            The magnitudes of the frames, shape ``(bins, frames)``.

        Returns
        -------
        numpy.ndarray
            The per-bin values the model produced, shape ``(bins, frames)``.
        """
        self._clear_if_block()
        array = np.asarray(magnitudes, dtype=np.float64)
        if array.ndim == 1:
            array = array[:, None]
        if array.ndim != 2:
            raise ModelError("the magnitudes must be 2-D (bins, frames)")
        if array.shape[0] != self.model.manifest.input.bins:
            raise ModelError(
                f"the model {self.model.manifest.name!r} reads "
                f"{self.model.manifest.input.bins} bins, but the frames have "
                f"{array.shape[0]}"
            )
        levels = self.model.band_levels_batch(array)
        if self._sequence is not None:
            raw = self.model.network_batch_sequence(levels, self._sequence)
            values = self._output_batch(raw)
            return values[self.model._band_of_bin]
        features = self._input_matrix(levels)
        raw = self.model.network_batch(features)
        values = self._output_batch(raw)
        # The per-bin values are laid out in the order of the frame by frame
        # path: (bins, frames), row by row.  The expansion is a row of the
        # values, so the bytes of the result are the bytes of the frame by
        # frame path, and not only the values.
        per_bin = values[self.model._band_of_bin]
        if self._delta:
            frames = levels.shape[1]
            for index in range(max(0, frames - self._delta), frames):
                self._history.append(levels[:, index].copy())
            while len(self._history) > self._delta:
                del self._history[0]
        return per_bin

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ModelRunner {self.model.manifest.name} cleared={self._cleared} "
            f"history={len(self._history)}>"
        )


# ---------------------------------------------------------------------------
# the offline path
# ---------------------------------------------------------------------------
def run_offline(
    model: SpectralModel,
    signal: np.ndarray,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
) -> np.ndarray:
    """Run a model over a whole signal.

    This is the offline path: the frames of the signal are analysed, the
    model is asked for a mask or a spectrum per frame, and the frames are
    put back together.  It is the path a model belongs to — a real time
    path cannot afford the frames of a whole file, and it does not need to.

    A model that produces a **mask** scales the magnitudes with it.  A model
    that produces a **spectrum** describes a spectrum: the network answers in
    dBFS, and the offline path turns it into a matching gain, the ratio of
    the spectrum the model wants to the spectrum the signal has, limited to
    24 dB in either direction.

    Parameters
    ----------
    model : SpectralModel
        The model to run.
    signal : numpy.ndarray
        The signal, shape ``(n_samples,)`` or ``(n_samples, channels)``.
    sample_rate : int, optional
        The rate of the signal.  It must be the rate the model runs at.
    channels : int, optional
        The number of channels of the signal.

    Returns
    -------
    numpy.ndarray
        The processed signal, same shape as the input.

    Raises
    ------
    ConfigMismatchError
        When the signal does not run at the model's rate.
    """
    manifest = model.manifest
    array = np.asarray(signal, dtype=np.float64)
    mono = array.ndim == 1
    if mono:
        array = array[:, None]
    if array.ndim != 2:
        raise ModelError("the signal must be 1-D (n,) or 2-D (n, channels)")
    rate = int(sample_rate if sample_rate is not None else manifest.input.sample_rate)
    if rate != manifest.input.sample_rate:
        raise ConfigMismatchError(
            f"the model {manifest.name!r} runs at {manifest.input.sample_rate} Hz, "
            f"but the signal runs at {rate} Hz"
        )
    count = int(channels) if channels is not None else array.shape[1]
    if count != array.shape[1]:
        raise ConfigMismatchError(
            f"the signal has {array.shape[1]} channels, but {count} were announced"
        )

    engine = STFT(manifest.input.frame_size, manifest.input.hop, window="hann")
    runner = ModelRunner(model)
    floor = model.reference_magnitude * 10.0 ** (manifest.input.features.floor_db / 20.0)
    out = np.empty_like(array)
    for channel in range(array.shape[1]):
        # The transform of the engine answers (channels, bins, frames); a
        # model reads one channel at a time.
        spectrum = engine.analyze_complex_block(array[:, channel], center=True)[0]
        magnitudes = np.abs(spectrum)
        values = runner.process_batch(magnitudes)
        if manifest.output.kind == "spectral_mask":
            gain = values
        else:
            current_db = 20.0 * np.log10(np.maximum(magnitudes, floor))
            target_db = 20.0 * np.log10(np.maximum(values, floor))
            gain = np.power(10.0, np.clip(target_db - current_db, -24.0, 24.0) / 20.0)
        out[:, channel] = engine.synthesize_complex_block(
            spectrum, norm="exact", length=array.shape[0], center=True, gain=gain
        )
    return out[:, 0] if mono else out


def run_offline_mix(
    model: SpectralModel,
    signal: np.ndarray,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
) -> np.ndarray:
    """Run a model over a whole signal, deciding on the **mix**.

    The model is asked once, on the mix of the channels, and the gain it
    produces is applied to every channel.  Two things follow from it:

    * the **cost** is the cost of one channel, not of as many channels as
      the file has.  A model that reads a frame every sample is asked
      48 000 times a second, and every one of those questions is a question
      about the whole image;
    * the **decision is the same for the whole image**, so the image does
      not move: what is taken away is taken away from every channel by the
      same amount.

    This is the path a model of the offline path takes when the file has
    more than one channel.  :func:`run_offline` asks the model about every
    channel on its own, and a caller that wants the image to stay still
    asks this one.

    Parameters
    ----------
    model : SpectralModel
        The model to run.
    signal : numpy.ndarray
        The signal, shape ``(n_samples,)`` or ``(n_samples, channels)``.
    sample_rate : int, optional
        The rate of the signal.  It must be the rate the model runs at.
    channels : int, optional
        The number of channels of the signal.

    Returns
    -------
    numpy.ndarray
        The processed signal, same shape as the input.

    Raises
    ------
    ConfigMismatchError
        When the signal does not run at the model's rate.
    """
    manifest = model.manifest
    array = np.asarray(signal, dtype=np.float64)
    mono = array.ndim == 1
    if mono:
        array = array[:, None]
    if array.ndim != 2:
        raise ModelError("the signal must be 1-D (n,) or 2-D (n, channels)")
    rate = int(sample_rate if sample_rate is not None else manifest.input.sample_rate)
    if rate != manifest.input.sample_rate:
        raise ConfigMismatchError(
            f"the model {model.manifest.name!r} runs at {manifest.input.sample_rate} Hz, "
            f"but the signal runs at {rate} Hz"
        )
    count = int(channels) if channels is not None else array.shape[1]
    if count != array.shape[1]:
        raise ConfigMismatchError(
            f"the signal has {array.shape[1]} channels, but {count} were announced"
        )

    engine = STFT(manifest.input.frame_size, manifest.input.hop, window="hann")
    runner = ModelRunner(model)
    floor = model.reference_magnitude * 10.0 ** (manifest.input.features.floor_db / 20.0)

    # The decision is made on the mix.
    mix = array.mean(axis=1)
    decision = engine.analyze_complex_block(mix, center=True)[0]
    magnitudes = np.abs(decision)
    values = runner.process_batch(magnitudes)
    if manifest.output.kind == "spectral_mask":
        gain = values
    else:
        current_db = 20.0 * np.log10(np.maximum(magnitudes, floor))
        target_db = 20.0 * np.log10(np.maximum(values, floor))
        gain = np.power(10.0, np.clip(target_db - current_db, -24.0, 24.0) / 20.0)

    out = np.empty_like(array)
    for channel in range(array.shape[1]):
        spectrum = engine.analyze_complex_block(array[:, channel], center=True)[0]
        out[:, channel] = engine.synthesize_complex_block(
            spectrum, norm="exact", length=array.shape[0], center=True, gain=gain
        )
    return out[:, 0] if mono else out
