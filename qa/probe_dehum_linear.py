"""Where does the de-hum's linear phase path answer?

The module promises that the linear phase path does not answer before the
input has arrived.  This prints where the answer actually is, so the cause
is a number and not a guess.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402


def main() -> int:
    module = registry.create("AUR_DEHUM", 48000, 1)
    module.params.set("Linear Phase Filters", True)

    n = 24000
    impulse = np.zeros((n, 1))
    impulse[0, 0] = 1.0

    out = np.asarray(module.process(impulse))[:, 0]

    loud = np.flatnonzero(np.abs(out) > 1e-9)
    print(f"the first sample above 1e-9 : {loud[0] if loud.size else -1}")
    print(f"the last  sample above 1e-9 : {loud[-1] if loud.size else -1}")
    print(f"the peak                   : {int(np.argmax(np.abs(out)))}"
          f" ({float(np.max(np.abs(out))):.6f})")

    early = loud[loud < 2399] if loud.size else np.array([], dtype=int)
    print(f"samples above 1e-9 before 2399: {early.size}")
    if early.size:
        worst = early[int(np.argmax(np.abs(out[early])))]
        print(f"  the worst is at {worst}: {float(out[worst]):.8f}")

    print("the first 12 samples:",
          " ".join(f"{v:.3e}" for v in out[:12]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
