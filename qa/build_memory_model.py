"""Build the model with a memory and write it to the model library.

The model is trained with the same builder the full build uses, so the
file this writes is the file ``python -m aurcore.models.train`` writes.
The training takes about a quarter of an hour for 2000 epochs: the
convolution stack is a great deal more arithmetic than the fully
connected one.

Usage
-----
    python qa/build_memory_model.py
    python qa/build_memory_model.py --epochs 200 --directory var/models
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aurcore.models.library import default_library_path
from aurcore.models.train import (
    MEMORY_MODEL,
    build_sample_memory_model,
    write_model,
)


def main(argv: list[str] | None = None) -> int:
    """Build the model and write it."""
    parser = argparse.ArgumentParser(
        prog="build_memory_model",
        description="Build the model with a memory (a causal convolution stack).",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=2000,
        help="the number of training epochs (default: 2000)",
    )
    parser.add_argument(
        "--directory",
        type=pathlib.Path,
        default=None,
        help="where the model is written (default: the shipped library)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="the seed of the training (default: the one of the builder)",
    )
    arguments = parser.parse_args(argv)

    target = (
        pathlib.Path(arguments.directory)
        if arguments.directory is not None
        else pathlib.Path(default_library_path())
    )
    target.mkdir(parents=True, exist_ok=True)

    started = time.time()
    if arguments.seed is None:
        manifest, weights, losses = build_sample_memory_model(epochs=arguments.epochs)
    else:
        manifest, weights, losses = build_sample_memory_model(
            epochs=arguments.epochs, seed=arguments.seed
        )
    elapsed = time.time() - started

    stored = {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}
    path = write_model(target / f"{MEMORY_MODEL}.aurm", manifest, stored)
    print(
        f"{MEMORY_MODEL:<22} epoch {manifest.epoch:>4}  parameters "
        f"{manifest.parameters:>7}  loss {losses[0]:.6f} -> {losses[-1]:.6f}  "
        f"{path.stat().st_size:>7} bytes  {manifest.checksum[:23]}..."
    )
    print(f"trained in {elapsed:.1f}s, written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
