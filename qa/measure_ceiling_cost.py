"""Measure what the ceiling rule of the family costs.

The rule is enforced in one place — the module's own ``process`` is wrapped
by the base class — so the cost of the rule is paid by every module of the
family.  This script measures the cost of a 64 sample block with and without
the wrapper, so that the number can be reported against the realtime budget
of the block (1333.3 us at 48 kHz).

The measurement is the one of the performance harness: every block is timed
next to a batch of 1024 point transforms of the same machine, and the cost of
the block is expressed in transform equivalents, so the number does not
depend on how busy the machine is.

Run it with::

    python qa/measure_ceiling_cost.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa" / "performance"))

import harness  # noqa: E402
import budgets  # noqa: E402

from aurcore import registry  # noqa: E402

#: The block the realtime budget is quoted at.
BLOCK = 64

#: The number of timed blocks per measurement.
REPEATS = 15

#: The number of untimed blocks that warm a module up.
WARMUP = 3


def main() -> int:
    """Measure the cost of every module with and without the ceiling rule."""
    codes = [info.code for info in registry.all()]

    def plain(code: str):
        """Return a callable that runs the module without the ceiling rule."""
        module = registry.create(code, harness.SAMPLE_RATE, harness.CHANNELS)
        unwrapped = getattr(module.process, "__wrapped__", None)
        if unwrapped is None:
            return module.process
        return lambda block: unwrapped(module, block)

    def wrapped(code: str):
        """Return a callable that runs the module with the ceiling rule."""
        module = registry.create(code, harness.SAMPLE_RATE, harness.CHANNELS)
        return module.process

    targets = {}
    for code in codes:
        targets[f"{code}:after"] = (lambda code=code: wrapped(code))
        targets[f"{code}:before"] = (lambda code=code: plain(code))

    measurement = harness.measure_catalogue_calibrated(
        targets,
        block_sizes=(BLOCK,),
        rounds=5,
        repeats=REPEATS,
        warmup=WARMUP,
        calib_repeats=10,
    )

    budget_us = harness.block_budget_us(BLOCK, harness.SAMPLE_RATE)
    print(
        f"block: {BLOCK} samples at {harness.SAMPLE_RATE} Hz, budget "
        f"{budget_us:.1f} us, yardstick {budgets.REFERENCE_TRANSFORM_US:.2f} us "
        f"per 1024 point transform"
    )
    print(
        f"{'module':<24}{'before us':>11}{'after us':>10}{'delta us':>10}"
        f"{'after %':>9}{'after trf':>11}"
    )
    worst = 0.0
    total_before = 0.0
    total_after = 0.0
    for code in codes:
        after = measurement.get(f"{code}:after")
        before = measurement.get(f"{code}:before")
        if after is None or before is None or not after.ok or not before.ok:
            print(f"{code:<24}  not measurable")
            continue
        a = after.timing_for(BLOCK)
        b = before.timing_for(BLOCK)
        if a is None or b is None:
            print(f"{code:<24}  not measurable")
            continue
        after_us = a.transforms * budgets.REFERENCE_TRANSFORM_US
        before_us = b.transforms * budgets.REFERENCE_TRANSFORM_US
        delta = after_us - before_us
        share = after_us / budget_us * 100.0
        worst = max(worst, share)
        total_before += before_us
        total_after += after_us
        print(
            f"{code:<24}{before_us:>11.1f}{after_us:>10.1f}{delta:>10.1f}"
            f"{share:>8.1f}%{a.transforms:>11.1f}"
        )
    print(
        f"\nall {len(codes)} modules together: {total_before:.0f} us before, "
        f"{total_after:.0f} us after, "
        f"{(total_after - total_before) / len(codes):.1f} us per module; "
        f"the busiest module uses {worst:.1f} % of the budget"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
