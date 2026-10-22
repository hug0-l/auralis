"""What delay does the FIR really have, and does it depend on the block size?

The tests say the filter must delay the signal by the latency it reports
and must give the same answer in any block size.  This measures both.
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


def best_lag(a: np.ndarray, b: np.ndarray, span: int = 4096) -> int:
    """The shift that aligns b with a."""
    correlation = np.correlate(a, b, mode="full")
    return int(np.argmax(correlation)) - (len(b) - 1)


def main() -> int:
    kernel = F.linear_phase_fir(magnitudes(), FS)
    print(f"the kernel: {kernel.size} taps, symmetric residual "
          f"{float(np.max(np.abs(kernel - kernel[::-1]))):.3e}")
    print(f"the reported latency: {F.FIR(kernel, FS).latency_samples}")

    # the peak of the kernel, which is where the response is centred
    peak = int(np.argmax(np.abs(kernel)))
    print(f"the peak of the kernel: at {peak} "
          f"(the centre would be {(kernel.size - 1) // 2})")

    n = 16384
    t = np.arange(n) / FS
    x = (0.5 * np.sin(2 * np.pi * 200.0 * t))[:, None]

    for block in (16384, 4096, 64):
        fir = F.FIR(kernel, FS)
        if block == n:
            out = fir.process(x)
        else:
            pieces = [fir.process(x[i:i + block]) for i in range(0, n, block)]
            out = np.concatenate(pieces, axis=0)

        steady = slice(8000, 12000)
        lag = best_lag(out[steady, 0], x[steady, 0])
        print(f"block {block:6d}: the output lags the input by {lag} samples, "
              f"amplitude {float(np.max(np.abs(out[steady, 0]))) / 0.5:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
