"""What delay does the FIR really have, and where do the two paths differ?

Cross correlation answers the delay without a phase argument, and the
difference between the one piece and the streamed path says where the
alignment goes wrong.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aurcore.dsp import filters as F  # noqa: E402

FS = 48000


def magnitudes():
    n = 2049
    freqs = np.arange(n // 2 + 1) * (FS / n)
    return np.ones(n // 2 + 1) - 0.5 * np.exp(-((freqs - 1000.0) / 50.0) ** 2)


def main() -> int:
    kernel = F.linear_phase_fir(magnitudes(), FS)
    taps = kernel.size
    fir = F.FIR(kernel, FS)

    n = 16384
    t = np.arange(n) / FS
    x = (0.5 * np.sin(2 * np.pi * 200.0 * t))[:, None]

    out = fir.process(x)

    # the delay by cross correlation, over the steady part
    a = out[2 * taps:, 0]
    b = x[2 * taps - taps: n - taps, 0]
    correlation = np.correlate(a, b, mode="full")
    lag = int(np.argmax(correlation)) - (len(b) - 1)
    print(f"taps={taps} reported={fir.latency_samples} measured_lag={lag}")

    # where do the one piece and the streamed path differ?
    one = F.FIR(kernel, FS).process(x)
    streamed = F.FIR(kernel, FS)
    pieces = [streamed.process(x[i:i + 64]) for i in range(0, n, 64)]
    joined = np.concatenate(pieces, axis=0)
    difference = np.abs(one - joined)[:, 0]
    first = int(np.argmax(difference > 1e-12))
    print(f"the first sample that differs: {first} (difference {difference[first]:.3e})")
    print("the maximum difference:", f"{difference.max():.3e}")
    print("the tail of the difference:", " ".join(f"{v:.2e}" for v in difference[-6:]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
