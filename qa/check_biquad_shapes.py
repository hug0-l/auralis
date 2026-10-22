"""The biquad must survive a change of shape.

The filter's memory used to follow the number of CALLS instead of the shape
of the block: a mono block after a stereo one left a (2, 2) memory in a one
dimensional filter, and a mono two dimensional block raised an IndexError.
The numbers of the filter must not change, so the test compares the fixed
filter against the same filter applied one channel at a time.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aurcore.dsp.filters import Biquad, biquad_lowpass  # noqa: E402


def make_filter():
    """One low pass section, built the way the modules build it."""
    return Biquad(*biquad_lowpass(1000.0, 0.7071, 48000.0))


def main() -> int:
    rng = np.random.default_rng(11)
    failures = []

    # A one dimensional call must work on a fresh filter.
    mono = rng.normal(0, 0.1, 256)
    try:
        out = make_filter().process(mono)
        if out.shape != mono.shape or not np.all(np.isfinite(out)):
            failures.append(f"the mono call returned {out.shape}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"the mono call raised {type(exc).__name__}: {exc}")

    # A one dimensional call after a two dimensional one must work.
    try:
        filter_ = make_filter()
        filter_.process(rng.normal(0, 0.1, (256, 2)))
        out = filter_.process(mono)
        if out.shape != mono.shape or not np.all(np.isfinite(out)):
            failures.append(f"the mono call after a stereo one returned {out.shape}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"the mono call after a stereo one raised {type(exc).__name__}: {exc}")

    # A one channel two dimensional block must work.
    try:
        out = make_filter().process(mono[:, None])
        if out.shape != (256, 1) or not np.all(np.isfinite(out)):
            failures.append(f"the (n, 1) call returned {out.shape}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"the (n, 1) call raised {type(exc).__name__}: {exc}")

    # The numbers must not change: the two dimensional path and the one
    # dimensional path must agree channel for channel.
    stereo = rng.normal(0, 0.1, (256, 2))
    both = make_filter().process(stereo)
    for channel in range(2):
        alone = make_filter().process(stereo[:, channel])
        if not np.allclose(both[:, channel], alone, atol=1e-12):
            failures.append(f"channel {channel} differs from the single channel path")

    for message in failures:
        print(f"  {message}")

    print("no problems" if not failures else f"{len(failures)} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
