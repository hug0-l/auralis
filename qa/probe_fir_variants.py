"""Find the alignment of the FIR that satisfies its own contract.

The filter has to delay the signal by the latency it reports, and the
answer must not depend on the size of the blocks the host uses.  There are
two choices that decide it: where the output is read, and how much history
is kept.  This measures every combination and prints the verdict.
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


def variant(kernel, n, x, keep_mode, tail_mode):
    """Run the filter the way the variant says."""
    taps = kernel.size
    keep = (taps - 1) // 2 if keep_mode == "group" else taps - 1
    history = (taps - 1) if tail_mode == "full" else keep

    tail = np.zeros(history)
    out = np.empty(n)

    for start in range(0, n, 64):
        block = x[start:start + 64, 0]
        buf = np.concatenate([tail, block])
        full = np.convolve(buf, kernel)
        out[start:start + block.size] = full[keep:keep + block.size]
        tail = buf[len(block):] if history == taps - 1 else buf[-history:] if history else buf[:0]

    return out


def main() -> int:
    kernel = F.linear_phase_fir(magnitudes(), FS)
    taps = kernel.size

    n = 16384
    t = np.arange(n) / FS
    x = (0.5 * np.sin(2 * np.pi * 200.0 * t))[:, None]

    # the reference: the filter as it is now, over one piece
    reference = F.FIR(kernel, FS).process(x)[:, 0]

    # the delay the filter claims
    fir = F.FIR(kernel, FS)
    claimed = fir.latency_samples

    for keep_mode in ("group", "full"):
        for tail_mode in ("full", "group"):
            one = variant(kernel, n, x, keep_mode, tail_mode)

            # the delay of the variant, by the best match with the input
            steady = slice(2 * taps, n)
            a = one[steady]
            b = x[2 * taps - taps:n - taps, 0]
            correlation = np.correlate(a, b, mode="full")
            lag = int(np.argmax(correlation)) - (len(b) - 1)

            # the block size independence
            streamed = np.empty(n)
            tail = np.zeros(taps - 1)
            keep = (taps - 1) // 2 if keep_mode == "group" else taps - 1
            history = taps - 1 if tail_mode == "full" else keep
            for start in range(0, n, 64):
                block = x[start:start + 64, 0]
                buf = np.concatenate([tail, block])
                full = np.convolve(buf, kernel)
                streamed[start:start + block.size] = full[keep:keep + block.size]
                tail = (
                    buf[len(block):]
                     if history == taps - 1
                     else buf[-history:]
                     if history else buf[:0]
                )
            difference = float(np.max(np.abs(one - streamed)))

            print(f"keep={keep_mode:<5} tail={tail_mode:<5} "
                  f"delay={lag:6d} claimed={claimed:6d} "
                  f"block_difference={difference:.3e} "
                  f"matches_the_filter={float(np.max(np.abs(one - reference))):.3e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
