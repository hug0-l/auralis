#!/usr/bin/env python3
"""
qa.measure_per_sample — what the per sample model does, and what it costs.

The per sample model reads a window of 128 samples and is asked **once per
sample**: 48 000 times a second.  The competitive study ruled that out for
the real time path, and this tool measures the two things that decide
whether it is worth having in the offline path:

* the **cost**: how many times the real time the render takes;
* the **quality**: what the render does to real material — the damaged
  phone memo of ``qa/make_demo_memo.py``, measured with the corpus's own
  ruler.

The material is ours, the ruler is the project's, and the numbers this tool
prints are the numbers the documents carry.

    python qa/make_demo_memo.py
    python qa/measure_per_sample.py
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

#: The two models the table compares.
FRAME_MODEL = "voice_enhance"
SAMPLE_MODEL = "voice_sample_mask"


def _levels(signal: np.ndarray, rate: int) -> dict:
    """Return the level of every band, with the corpus's own ruler."""
    from corpus import measure

    return measure.band_levels_db(signal, rate, BANDS)


def main(argv: list[str] | None = None) -> int:
    """Measure the cost and the quality of the per sample model."""
    parser = argparse.ArgumentParser(
        prog="measure_per_sample.py",
        description="Measure what the per sample model costs and what it buys.",
    )
    parser.add_argument("--memo", type=Path, default=MEMO, help="the memo to measure")
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
    print(f"the memo: {len(signal):,} frames, {rate} Hz, {signal.shape[1]} ch, "
          f"{seconds:.2f} s")

    before = _levels(signal, rate)
    print()
    print("what is wrong with it:")
    for name, level in before.items():
        print(f"   {name:<26} {level:>9.3f} dBFS")
    print(f"   the voice against the hum: "
          f"{before['the voice 120-3000 Hz'] - before['the hum 45-55 Hz']:+.2f} dB")

    print()
    print("the two models:")
    print(f"   {'the model':<24} {'the layout':>10} {'the cost':>10} {'x real time':>13}")
    results: dict[str, dict] = {}
    for name in (FRAME_MODEL, SAMPLE_MODEL):
        model = models.open_library().load(name)
        manifest = model.manifest
        layout = f"{manifest.input.frame_size}/{manifest.input.hop}"
        best = float("inf")
        for _ in range(2):
            start = time.perf_counter()
            out, _ = models.run_named(name, signal)
            best = min(best, time.perf_counter() - start)
        results[name] = {"out": out, "levels": _levels(out, rate), "cost": best}
        print(f"   {name:<24} {layout:>10} {best:>8.3f} s {best / seconds:>13.2f}")

    print()
    print("what each model does to the material:")
    print(f"   {'the model':<24} {'the hum':>9} {'the voice':>9} {'the hiss':>9} "
          f"{'the rumble':>10}")
    for name in (FRAME_MODEL, SAMPLE_MODEL):
        after = results[name]["levels"]
        hum = after["the hum 45-55 Hz"] - before["the hum 45-55 Hz"]
        voice = after["the voice 120-3000 Hz"] - before["the voice 120-3000 Hz"]
        hiss = after["the hiss 5000-20000 Hz"] - before["the hiss 5000-20000 Hz"]
        rumble = after["the rumble 20-80 Hz"] - before["the rumble 20-80 Hz"]
        print(f"   {name:<24} {hum:>9.2f} {voice:>9.2f} {hiss:>9.2f} {rumble:>10.2f}")

    print()
    print("the cost of the offline budget is 2.0 times the real time;")
    print(f"   the per sample model takes "
          f"{results[SAMPLE_MODEL]['cost'] / seconds:.2f} times the real time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
