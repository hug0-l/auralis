"""Check one module for a finite output over a set of signals.

The quality survey found modules that return a non-finite value for a
perfectly ordinary input.  This is the smallest reproduction of that: one
module, a few signals, and a report of what came out.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402


def signals(sample_rate: int, frames: int):
    t = np.arange(frames) / sample_rate
    return {
        "silence": np.zeros((frames, 2)),
        "tone": (0.5 * np.sin(2 * np.pi * 1000 * t))[:, None] * np.ones((1, 2)),
        "hum": (0.2 * np.sin(2 * np.pi * 50 * t))[:, None] * np.ones((1, 2)),
        "noise": np.random.default_rng(7).normal(0, 0.05, (frames, 2)),
    }


def main(argv: list[str]) -> int:
    codes = argv[1:] or [info.code for info in registry.all()]
    sample_rate = 48000
    frames = 4096

    bad = 0
    for code in codes:
        for name, signal in signals(sample_rate, frames).items():
            module = registry.create(code, sample_rate, 2)
            try:
                out = module.process(signal)
            except Exception as exc:  # noqa: BLE001
                print(f"{code:<22} {name:<9} RAISED {type(exc).__name__}: {exc}")
                bad += 1
                continue

            finite = int(np.count_nonzero(~np.isfinite(out)))
            if finite:
                print(f"{code:<22} {name:<9} {finite}/{out.size} non-finite samples")
                bad += 1

    print("no problems" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
