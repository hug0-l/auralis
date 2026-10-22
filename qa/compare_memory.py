#!/usr/bin/env python3
"""
qa.compare_memory — what the memory of the model is worth.

The model with a memory and the per sample model differ in **two** things:
the network (a stack of causal convolutions over the time axis against a
fully connected stack over one frame) and the **time scale of the material
they were taught**.  The per sample model was taught a voice whose
syllables last a few samples; the model with a memory was taught one whose
syllables last tens of milliseconds, because a memory of eleven
milliseconds cannot learn anything from syllables that last one.

This tool takes the two apart.  It trains a third model — the per sample
architecture, on the training set of the model with a memory — and renders
the memo with all three.  The differences then say what each of the two
changes is worth:

* the difference between the **per sample model** and the **same
  architecture on the new material** is what the *material* is worth;
* the difference between that and the **model with a memory** is what the
  *memory* is worth.

The third model is **not shipped**: it is a measurement, and it lives in
this run only.  The material is ours and the ruler is the project's.

    python qa/make_demo_memo.py
    python qa/compare_memory.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEMO = ROOT / "corpus" / "audio" / "test" / "voice_memo_damaged.wav"

BANDS = {
    "the voice 120-3000 Hz": (120.0, 3000.0),
    "the hum 45-55 Hz": (45.0, 55.0),
    "the rumble 20-80 Hz": (20.0, 80.0),
    "the hiss 5000-20000 Hz": (5000.0, 20000.0),
}

#: The models the table compares.
FRAME_MODEL = "voice_enhance"
SAMPLE_MODEL = "voice_sample_mask"
MEMORY_MODEL = "voice_sample_memory"


def _levels(signal: np.ndarray, rate: int) -> dict:
    """Return the level of every band, with the corpus's own ruler."""
    from corpus import measure

    return measure.band_levels_db(signal, rate, BANDS)


def _build_the_third_model(epochs: int):
    """Train the per sample architecture on the material of the memory model.

    The training set is the one the model with a memory was taught, made
    with the same seed, so the two models have seen **the same material**
    and differ in nothing but the network.
    """
    from aurcore.models import train as T
    from aurcore.models.engine import SpectralModel

    edges = T.mel_edges(T.BANDS)
    output = T.OutputSpec(
        kind="spectral_mask",
        bins=T.SAMPLE_FRAME // 2 + 1,
        range=(0.0, 1.0),
        transfer="sigmoid",
    )
    state = T.StateSpec(scope="file", cleared="file")

    # The material: the set the model with a memory was taught, made with
    # the same seed and the same skeleton, so the two models have seen the
    # **same material** and differ in nothing but the network.
    memory_features = T.Features(
        kind="log_magnitude_bands",
        band_edges_hz=edges,
        floor_db=T.FLOOR_DB,
        delta_frames=0,
    )
    memory_layers = (
        (T.BANDS, 16, 3, 1, "relu"),
        (16, 16, 3, 16, "relu"),
        (16, T.BANDS, 3, 256, "none"),
    )
    memory_context = T.ContextSpec(
        frames=T.MEMORY_SPAN - 1, samples=(T.MEMORY_SPAN - 1) * T.SAMPLE_HOP
    )
    memory_skeleton = T._skeleton(
        memory_features,
        memory_layers,
        output,
        memory_context,
        state,
        frame_size=T.SAMPLE_FRAME,
        hop=T.SAMPLE_HOP,
    )
    rng = np.random.default_rng(T.SEED + 4)
    levels, share = T._memory_training_set(memory_skeleton, rng)

    # The third model: the architecture of the per sample model, taught the
    # material above.
    features = T.Features(
        kind="log_magnitude_bands",
        band_edges_hz=edges,
        floor_db=T.FLOOR_DB,
        delta_frames=1,
    )
    layers = ((T.BANDS * 2, 32, "relu"), (32, 24, "relu"), (24, T.BANDS, "none"))
    context = T.ContextSpec(frames=1, samples=T.SAMPLE_HOP)
    stacked = T._with_deltas(levels, (1,))
    goal = T._logit(share)
    weights, losses = T.train_mlp(stacked, goal, (32, 24), epochs=epochs, seed=T.SEED + 3)
    manifest = T._manifest(
        name="voice_sample_same_material",
        task="The per sample architecture on the material of the model with a memory.",
        problem="voice",
        features=features,
        output=output,
        context=context,
        state=state,
        layers=layers,
        weights=weights,
        epoch=epochs,
        notes="A measurement, not a shipped model.",
        frame_size=T.SAMPLE_FRAME,
        hop=T.SAMPLE_HOP,
        version="0.0.1",
        variant="sample",
    )
    model = SpectralModel(manifest, weights)
    return model, losses


def main(argv: list[str] | None = None) -> int:
    """Render the memo with the three models and compare them."""
    parser = argparse.ArgumentParser(
        prog="compare_memory.py",
        description="Say what the memory of the model is worth.",
    )
    parser.add_argument("--memo", type=Path, default=MEMO, help="the memo to measure")
    parser.add_argument(
        "--epochs",
        type=int,
        default=2000,
        help="the epochs of the third model (default: 2000)",
    )
    args = parser.parse_args(argv)

    if not args.memo.is_file():
        print(
            f"the memo is not there ({args.memo}); run qa/make_demo_memo.py first",
            file=sys.stderr,
        )
        return 2

    import soundfile as sf

    from aurcore import models

    signal, rate = sf.read(str(args.memo), always_2d=True)
    seconds = len(signal) / float(rate)
    before = _levels(signal, rate)
    print(f"the memo: {len(signal):,} frames, {rate} Hz, {seconds:.2f} s")
    print()
    print("what is wrong with it:")
    for name, level in before.items():
        print(f"   {name:<26} {level:>9.3f} dBFS")

    print()
    print("training the third model (the per sample architecture, the material of")
    print("the model with a memory):")
    started = time.perf_counter()
    third, losses = _build_the_third_model(args.epochs)
    print(
        f"   {third.manifest.parameters} parameters, epoch {args.epochs}, "
        f"loss {losses[0]:.4f} -> {losses[-1]:.4f}, {time.perf_counter() - started:.1f}s"
    )

    print()
    print("the models:")
    print(f"   {'the model':<34} {'the cost':>10} {'x real time':>13}")
    renders: dict[str, np.ndarray] = {}
    for name in (FRAME_MODEL, SAMPLE_MODEL, "voice_sample_same_material", MEMORY_MODEL):
        if name == "voice_sample_same_material":
            model = third
        else:
            model = models.open_library().load(name)
        best = float("inf")
        for _ in range(2):
            start = time.perf_counter()
            out = _render(model, signal, rate)
            best = min(best, time.perf_counter() - start)
        renders[name] = out
        print(f"   {name:<34} {best:>8.3f} s {best / seconds:>13.2f}")

    print()
    print("what each model does to the material:")
    print(
        f"   {'the model':<34} {'the hum':>9} {'the voice':>9} {'the hiss':>9} "
        f"{'the rumble':>10}"
    )
    for name, out in renders.items():
        after = _levels(out, rate)
        hum = after["the hum 45-55 Hz"] - before["the hum 45-55 Hz"]
        voice = after["the voice 120-3000 Hz"] - before["the voice 120-3000 Hz"]
        hiss = after["the hiss 5000-20000 Hz"] - before["the hiss 5000-20000 Hz"]
        rumble = after["the rumble 20-80 Hz"] - before["the rumble 20-80 Hz"]
        print(
            f"   {name:<34} {hum:>9.2f} {voice:>9.2f} {hiss:>9.2f} {rumble:>10.2f}"
        )
    return 0


def _render(model, signal: np.ndarray, rate: int) -> np.ndarray:
    """Run a model over the mix of the channels and apply the gain to all."""
    from aurcore.models import run_offline_mix

    return run_offline_mix(model, signal, sample_rate=rate)


if __name__ == "__main__":
    raise SystemExit(main())
