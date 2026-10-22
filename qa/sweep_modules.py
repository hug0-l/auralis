"""Sweep every module over awkward inputs and report anything unusable.

The interesting inputs are the ones that break a module: silence, a
constant signal, a full scale square, a single impulse and a pure tone.
A module that returns NaN, infinity, a different length or a non finite
sample has a defect, and the defect is worth more than the module.
"""
import sys
from pathlib import Path
import traceback

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurcore.api import Engine  # noqa: E402
from aurcore import registry  # noqa: E402


def cases(sample_rate: int, frames: int):
    silence = np.zeros((frames, 2), dtype=np.float64)
    dc = np.full((frames, 2), 0.5, dtype=np.float64)
    square = (
        np.sign(np.sin(2 * np.pi * 220.0 * np.arange(frames) / sample_rate))[:, None]
        * np.ones((1, 2))
    )
    impulse = np.zeros((frames, 2), dtype=np.float64)
    impulse[frames // 2, 0] = 1.0
    tone = (
        (0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(frames) / sample_rate))[:, None]
        * np.ones((1, 2))
    )
    return [("silence", silence), ("dc", dc), ("square", square),
            ("impulse", impulse), ("tone", tone)]


def main() -> int:
    sample_rate = 48000
    frames = 4096
    modules = [info.code for info in registry.all()]
    problems = []
    checked = 0

    for code in sorted(modules):
        for name, signal in cases(sample_rate, frames):
            engine = Engine(sample_rate=sample_rate, channels=2)
            try:
                handle = engine.add_module(code)
                if handle is None:
                    problems.append((code, name, "add_module failed"))
                    continue
                out = engine.process(signal)
                checked += 1

                if out.shape != signal.shape:
                    problems.append((code, name, f"shape {out.shape} != {signal.shape}"))
                    continue
                if not np.all(np.isfinite(out)):
                    bad = int(np.count_nonzero(~np.isfinite(out)))
                    problems.append((code, name, f"{bad} non-finite samples"))
            except Exception:
                problems.append((code, name, traceback.format_exc(limit=2).replace("\n", " | ")))
            finally:
                try:
                    engine.close()
                except Exception:
                    pass

    print(f"{len(modules)} modules, {checked} runs")
    if problems:
        print(f"{len(problems)} problem(s):")
        for code, name, message in problems:
            print(f"  {code:<22} {name:<10} {message[:200]}")
        return 1

    print("no problems")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

