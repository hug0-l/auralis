"""
aurcore.models.bench — the measurement of the model engine.

The question this tool answers: **what does a model cost, and what size of
model fits the real time budget?**

The budget
----------

A real time plug-in may spend 1333 us on a block of 64 samples.  A model
that runs once per frame of 1024 samples with a hop of 256 produces one
answer per 256 samples, i.e. one answer per four blocks, so the budget of
one frame is

    1333 us * 4 = 5332 us

Everything below is measured with :func:`time.perf_counter`, after a warm
up.  The **fastest** of the repetitions is reported: a run that was
disturbed by another process says something about the machine and not about
the engine.  The median is printed next to it, so the noise is visible.

    python -m aurcore.models.bench

Two arithmetic paths
--------------------

The engine adds the contribution of every input of a layer in the order of
the input index.  The order is part of the result, so it is fixed by the
engine and not by a reduction library: two runs give the same bits on
every machine.  A library matmul is faster, and it is what a real time
product would use, but the order of the summation is then the order of the
library.  Both paths are measured here, so the price of the determinism is
a number and not an adjective.

The sweep builds networks of a stated size **in memory** (nothing is
written), so a model of four million parameters can be measured without
putting four million parameters into the tree.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from .engine import ModelRunner, SpectralModel
from .library import open_library
from .manifest import (
    ContextSpec,
    Features,
    InputSpec,
    Manifest,
    NetworkSpec,
    OutputSpec,
    StateSpec,
    WeightSpec,
)
from .train import BANDS, FLOOR_DB, FRAME_SIZE, HOP, SAMPLE_RATE, mel_edges

__all__ = [
    "BUDGET_US_PER_BLOCK",
    "BLOCK_SIZE",
    "measure_model",
    "measure_sweep",
    "main",
]

#: The time a real time plug-in may spend on a block, in microseconds.
BUDGET_US_PER_BLOCK = 1333.0

#: The size of a block of the real time path, in samples.
BLOCK_SIZE = 64

#: The number of frames the measurement runs.
FRAMES = 400

#: The number of repetitions the measurement takes.
REPEATS = 9


def _budget_us_per_frame(hop: int = HOP) -> float:
    """The real time budget of one frame of the model path."""
    return BUDGET_US_PER_BLOCK * (hop / float(BLOCK_SIZE))


def _synthetic_magnitudes(frames: int = FRAMES, seed: int = 20261021) -> np.ndarray:
    """A table of magnitude spectra to run the engine over."""
    rng = np.random.default_rng(seed)
    bins = FRAME_SIZE // 2 + 1
    levels = rng.uniform(-70.0, -15.0, size=(bins, frames))
    return np.power(10.0, levels / 20.0)


def _input_vectors(model: SpectralModel, magnitudes: np.ndarray) -> list[np.ndarray]:
    """The vectors the network reads, one per frame."""
    runner = ModelRunner(model)
    vectors: list[np.ndarray] = []
    for index in range(magnitudes.shape[1]):
        levels = model.band_levels(magnitudes[:, index])
        vectors.append(runner._input_vector(levels))
        runner.process_frame(magnitudes[:, index])
    return vectors


def _chain_us(model: SpectralModel, vectors, repeats: int, ordered: bool) -> float:
    """The cost of one frame of the network, on one of the two paths."""
    layers = model._layers
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        for vector in vectors:
            activation = vector
            for matrix, bias, kind in layers:
                if ordered:
                    activation = model._dense(matrix, bias, activation)
                else:
                    activation = activation @ matrix + bias
                if kind == "relu":
                    np.maximum(activation, 0.0, out=activation)
        samples.append((time.perf_counter() - start) / len(vectors) * 1e6)
    return min(samples)


def measure_model(model: SpectralModel, frames: int = FRAMES, repeats: int = REPEATS) -> dict:
    """Measure the cost of one inference of a model.

    Parameters
    ----------
    model : SpectralModel
        The model to measure.
    frames : int
        The number of frames per repetition.
    repeats : int
        The number of repetitions.

    Returns
    -------
    dict
        ``per_frame_us`` (the fastest run), ``per_frame_median_us``,
        ``per_frame_max_us``, ``per_second_ms`` (the cost of one second of
        audio), ``per_block_us`` (the cost of one 64-sample block) and
        ``budget_fraction`` (the share of the budget the model takes).
    """
    magnitudes = _synthetic_magnitudes(frames)
    runner = ModelRunner(model)
    # warm up: the first call pays for the caches of NumPy
    runner.process(magnitudes)
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        runner.process(magnitudes)
        elapsed = time.perf_counter() - start
        samples.append(elapsed / frames * 1e6)
    samples.sort()
    per_frame = samples[0]
    median = samples[len(samples) // 2]
    per_second_ms = per_frame * 1e-6 * (SAMPLE_RATE / float(HOP)) * 1000.0
    per_block = per_frame / (HOP / float(BLOCK_SIZE))
    return {
        "per_frame_us": per_frame,
        "per_frame_median_us": median,
        "per_frame_max_us": samples[-1],
        "per_second_ms": per_second_ms,
        "per_block_us": per_block,
        "budget_fraction": per_frame / _budget_us_per_frame(model.manifest.hop),
        "frames": frames,
    }


def compare_accumulation(
    model: SpectralModel, frames: int = FRAMES, repeats: int = REPEATS
) -> dict:
    """Measure the ordered accumulation against the library matmul.

    The engine adds the contributions of a layer in the order of the input
    index, so two runs give the same bits on every machine.  A library
    matmul is faster, but the order of the summation is then the order of
    the library.  This measures what the determinism costs.

    Returns
    -------
    dict
        ``ordered_us`` and ``library_us``: the cost of one frame of the
        network, the two ways.
    """
    magnitudes = _synthetic_magnitudes(frames)
    vectors = _input_vectors(model, magnitudes)
    return {
        "ordered_us": _chain_us(model, vectors, repeats, ordered=True),
        "library_us": _chain_us(model, vectors, repeats, ordered=False),
    }


def _model_of_size(hidden: tuple[int, ...]) -> SpectralModel:
    """Build a model with the given hidden widths, in memory."""
    edges = mel_edges(BANDS)
    features = Features(
        kind="log_magnitude_bands", band_edges_hz=edges, floor_db=FLOOR_DB, delta_frames=0
    )
    output = OutputSpec(
        kind="spectral_mask", bins=FRAME_SIZE // 2 + 1, range=(0.0, 1.0), transfer="sigmoid"
    )
    context = ContextSpec(frames=0, samples=0)
    state = StateSpec(scope="block", cleared="block")
    layers = [(BANDS, hidden[0], "relu")]
    for index in range(1, len(hidden)):
        layers.append((hidden[index - 1], hidden[index], "relu"))
    layers.append((hidden[-1], BANDS, "none"))
    rng = np.random.default_rng(11)
    weights: dict[str, np.ndarray] = {}
    specs = []
    parameters = 0
    for index, (width_in, width_out, _) in enumerate(layers):
        weights[f"w{index}"] = rng.uniform(-0.1, 0.1, size=(width_in, width_out)).astype(np.float32)
        weights[f"b{index}"] = np.zeros(width_out, dtype=np.float32)
        specs.append(
            WeightSpec(
                name=f"w{index}", shape=(width_in, width_out), dtype="float32", key=f"w{index}"
            )
        )
        specs.append(
            WeightSpec(name=f"b{index}", shape=(width_out,), dtype="float32", key=f"b{index}")
        )
        parameters += width_in * width_out + width_out
    manifest = Manifest(
        name="bench",
        version="0.0.0",
        epoch=0,
        trained_on="2026-10-21",
        variant="bench",
        problem="voice",
        task="bench",
        input=InputSpec(
            sample_rate=SAMPLE_RATE,
            channels=1,
            frame_size=FRAME_SIZE,
            hop=HOP,
            features=features,
        ),
        output=output,
        context=context,
        state=state,
        network=NetworkSpec(kind="mlp", layers=tuple(layers), parameters=parameters),
        weights=tuple(specs),
        checksum="sha256:" + "0" * 64,
        deterministic=True,
        notes="bench",
    )
    return SpectralModel(manifest, weights)


#: The hidden widths of the sweep: from a tiny model to one of the size of
#: the models of the reference products (4.5 million parameters, 17 MB).
SWEEP: tuple[tuple[int, ...], ...] = ((32,), (64, 64), (256, 256), (1024, 1024), (2048, 2048))


def measure_sweep(frames: int = FRAMES, repeats: int = REPEATS) -> list[dict]:
    """Measure the cost of a series of model sizes.

    Returns
    -------
    list of dict
        One entry per size: the hidden widths, the number of parameters,
        the cost per frame on the ordered path and on the library path, and
        the share of the budget.
    """
    results: list[dict] = []
    for hidden in SWEEP:
        model = _model_of_size(hidden)
        measured = measure_model(model, frames=frames, repeats=repeats)
        magnitudes = _synthetic_magnitudes(frames)
        vectors = _input_vectors(model, magnitudes)
        library = _chain_us(model, vectors, repeats, ordered=False)
        measured["hidden"] = hidden
        measured["parameters"] = model.manifest.parameters
        measured["megabytes"] = model.manifest.parameters * 4 / (1024.0 * 1024.0)
        measured["library_us"] = library
        measured["library_fraction"] = library / _budget_us_per_frame(model.manifest.hop)
        results.append(measured)
    return results


def _affordable(results: list[dict], budget_us: float, key: str = "per_frame_us") -> dict:
    """The largest measured size that fits the budget, and the one after it."""
    fits = [item for item in results if item[key] <= budget_us]
    misses = [item for item in results if item[key] > budget_us]
    best = fits[-1] if fits else None
    first_miss = misses[0] if misses else None
    answer = {"fits": best, "misses": first_miss}
    if best is not None and first_miss is not None:
        # interpolate in log-log space between the two measured points
        low = np.log10(best["parameters"])
        high = np.log10(first_miss["parameters"])
        low_cost = np.log10(best[key])
        high_cost = np.log10(first_miss[key])
        target = np.log10(budget_us)
        share = (target - low_cost) / (high_cost - low_cost)
        answer["estimated_parameters"] = float(10.0 ** (low + share * (high - low)))
    return answer


def main(argv: list[str] | None = None) -> int:
    """Measure the shipped models and the sweep of the sizes."""
    parser = argparse.ArgumentParser(
        prog="aurcore.models.bench",
        description="Measure the cost of the model engine.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=FRAMES,
        help="the number of frames per repetition (default: 400)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=REPEATS,
        help="the number of repetitions (default: 9)",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="the model library to measure (default: the shipped one)",
    )
    args = parser.parse_args(argv)

    budget = _budget_us_per_frame()
    print(f"budget: {BUDGET_US_PER_BLOCK:.0f} us per {BLOCK_SIZE}-sample block")
    print(
        f"        frame {FRAME_SIZE}, hop {HOP} at {SAMPLE_RATE} Hz "
        f"-> {budget:.0f} us per frame"
    )
    print()

    library = open_library(args.library)
    print("the shipped models (the ordered path the engine uses)")
    print(
        f"  {'model':<22} {'params':>8} {'us/frame':>9} {'ms/s audio':>11} "
        f"{'us/block':>9} {'budget':>8} {'median':>8}"
    )
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        measured = measure_model(model, frames=args.frames, repeats=args.repeats)
        print(
            f"  {entry.name:<22} {model.manifest.parameters:>8} "
            f"{measured['per_frame_us']:>9.1f} {measured['per_second_ms']:>11.2f} "
            f"{measured['per_block_us']:>9.1f} "
            f"{measured['budget_fraction'] * 100.0:>7.2f}% "
            f"{measured['per_frame_median_us']:>8.1f}"
        )
    print()

    print("the size sweep (built in memory, nothing is written)")
    print(
        f"  {'hidden':>16} {'params':>9} {'MB':>7} {'us/frame':>9} "
        f"{'ms/s audio':>11} {'budget':>8} {'library':>9} {'library budget':>15}"
    )
    results = measure_sweep(frames=args.frames, repeats=args.repeats)
    for item in results:
        print(
            f"  {str(item['hidden']):>16} {item['parameters']:>9} "
            f"{item['megabytes']:>7.2f} {item['per_frame_us']:>9.1f} "
            f"{item['per_second_ms']:>11.2f} {item['budget_fraction'] * 100.0:>7.2f}% "
            f"{item['library_us']:>9.1f} {item['library_fraction'] * 100.0:>14.2f}%"
        )
    print()

    for key, title in (
        ("per_frame_us", "the ordered path (the engine as it is)"),
        ("library_us", "the library matmul path (the order is then the library's)"),
    ):
        answer = _affordable(results, budget, key=key)
        print(title)
        if answer.get("fits") is not None:
            item = answer["fits"]
            print(
                f"  the largest measured model inside the budget: {item['parameters']} "
                f"parameters ({item['megabytes']:.2f} MB), {item[key]:.1f} us per frame"
            )
        if answer.get("estimated_parameters") is not None:
            print(
                f"  the budget holds about {answer['estimated_parameters']:.0f} parameters "
                f"({answer['estimated_parameters'] * 4 / (1024.0 * 1024.0):.2f} MB of float32)"
            )
        if answer.get("misses") is not None:
            item = answer["misses"]
            print(
                f"  the next measured size ({item['parameters']} parameters, "
                f"{item['megabytes']:.2f} MB) needs {item[key]:.1f} us per frame, "
                f"{item[key] / budget:.2f} times the budget"
            )
    print()

    print("what the determinism costs (the network alone, one frame)")
    for entry in library.list():
        if not entry.readable:
            continue
        model = library.load(entry.name)
        measured = compare_accumulation(model, frames=args.frames, repeats=args.repeats)
        print(
            f"  {entry.name:<22} ordered {measured['ordered_us']:>9.1f} us   "
            f"library {measured['library_us']:>9.1f} us   "
            f"factor {measured['ordered_us'] / max(measured['library_us'], 1e-9):>6.1f}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entry point
    raise SystemExit(main())
