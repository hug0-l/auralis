#!/usr/bin/env python3
"""
qa.performance.sweep — the measurement sweep that the budgets are derived from.

The sweep answers the four questions the realtime guard has to be built on:

1.  how long does every module need for one block,
2.  how long does a realistic chain need for one block,
3.  how much does each module allocate inside ``process()``,
4.  how busy was the machine while the numbers were taken.

The machine is shared, so every timing is the **fastest of N runs** (the
fastest run is the one that was least disturbed by other work) and the
calibration (the cost of one 1024 point transform on this machine) is
measured in the same session, so a timing can be converted into a
machine independent number.

Examples
--------
    # the full sweep, five runs per module, four block sizes
    python qa/performance/sweep.py --runs 5

    # write the results as JSON
    python qa/performance/sweep.py --runs 5 --json var/perf/sweep.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness  # noqa: E402  (path setup has to come first)

#: The dialogue cleanup chain: mains hum, clicks, sibilance, plosives and a
#: level change.  This is the chain the product is sold on.
DIALOGUE_CHAIN: tuple[str, ...] = (
    "AUR_DEHUM",
    "AUR_DECLICK",
    "AUR_DEESS",
    "AUR_DEPLOSIVE",
    "AUR_GAIN",
)

#: The spectral chain: the dialogue chain with the flagship Spectral
#: De-Noise module in it.  This is what a spectral restoration session
#: looks like.
SPECTRAL_CHAIN: tuple[str, ...] = (
    "AUR_DEHUM",
    "AUR_SPECTRALDENOISE",
    "AUR_DECLICK",
    "AUR_DEESS",
    "AUR_GAIN",
)

CHAINS: dict[str, tuple[str, ...]] = {
    "dialogue": DIALOGUE_CHAIN,
    "spectral": SPECTRAL_CHAIN,
}


def _module_row(measurement: harness.ModuleMeasurement, blocks: Sequence[int]) -> dict[str, Any]:
    """Return the JSON-ready record of one module."""
    return {
        "code": measurement.code,
        "display_name": measurement.display_name,
        "category": measurement.category,
        "ok": measurement.ok,
        "error": measurement.error,
        "timings": {
            str(block): {
                "median_us": round(t.median_us, 2),
                "min_us": round(t.min_us, 2),
                "p95_us": round(t.p95_us, 2),
                "max_us": round(t.max_us, 2),
                "budget_us": round(t.budget_us, 2),
                "budget_fraction": round(t.budget_fraction, 4),
                "over_budget": t.over_budget,
                "transform_us": round(t.transform_us, 3),
                "transforms": round(t.transforms, 3) if t.transform_us > 0 else None,
            }
            for block in blocks
            if (t := measurement.timing_for(block)) is not None
        },
        "allocations": measurement.allocations.to_dict(),
    }


def render_markdown(
    modules: list[harness.ModuleMeasurement],
    chains: dict[str, harness.ChainMeasurement],
    allocations: dict[str, dict[str, Any]],
    blocks: list[int],
    yardstick: float,
) -> str:
    """Render the sweep as a Markdown table (the report of the guard)."""
    lines: list[str] = []
    header = "| module | " + " | ".join(f"{b} trf | {b} "
                                        f"%" for b in blocks) + " | arrays/blk | churn KiB |"
    lines.append(header)
    lines.append("|---|" + "---|" * (2 * len(blocks)) + "---|---|")
    for measurement in modules:
        cells: list[str] = []
        for block in blocks:
            timing = measurement.timing_for(block)
            if timing is None:
                cells.extend(["—", "—"])
                continue
            share = timing.transforms * yardstick / timing.budget_us * 100.0
            flag = " ⚠" if share > 100.0 else ""
            cells.append(f"{timing.transforms:.0f}{flag}")
            cells.append(f"{share:.1f}%")
        alloc = allocations.get(measurement.code, {})
        lines.append(
            f"| `{measurement.code}` | "
            + " | ".join(cells)
            + f" | {alloc.get('arrays_per_block', 0):.0f}"
            + f" | {alloc.get('peak_bytes', 0) / 1024.0:.0f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the sweep and print the table."""
    parser = argparse.ArgumentParser(
        prog="sweep.py",
        description="Measure every module and the product chains.",
    )
    parser.add_argument("--runs", type=int, default=5, help="runs per module (fastest wins)")
    parser.add_argument("--repeats", type=int, default=harness.DEFAULT_REPEATS)
    parser.add_argument("--warmup", type=int, default=harness.DEFAULT_WARMUP)
    parser.add_argument(
        "--blocks",
        default="64,128,256,512",
        help="comma separated block sizes (default 64,128,256,512)",
    )
    parser.add_argument("--calibration-runs", type=int, default=5)
    parser.add_argument("--calibration-repeats", type=int, default=200)
    parser.add_argument(
        "--calib-repeats",
        type=int,
        default=10,
        help="transforms timed next to every block (default 10)",
    )
    parser.add_argument("--json", dest="json_path", help="write the results as JSON")
    parser.add_argument(
        "--markdown",
        dest="markdown_path",
        help="write the measurement table as Markdown",
    )
    args = parser.parse_args(argv)

    blocks = [int(b) for b in str(args.blocks).split(",") if b.strip()]
    runs = max(1, int(args.runs))

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    calibration = harness.calibrate(
        runs=max(1, int(args.calibration_runs)),
        repeats=max(1, int(args.calibration_repeats)),
    )
    # The yardstick of the guard: how fast this machine transforms when it
    # is not busy.  The reference value is the *fastest* transform the
    # machine manages, because the reference machine of the budget is
    # this machine at rest — the machine a plug-in runs on.  It is
    # sampled before and after the sweep, so the quietest moment of the
    # session is used.
    yardstick_samples = harness.transform_us_samples(
        runs=max(4, int(args.calibration_runs) * 4), repeats=10
    )
    yardstick = min(yardstick_samples)
    print(
        f"calibration: one 1024 point rfft costs {calibration.fft_us:.2f} us "
        f"(fastest of {calibration.runs} runs, slowest {calibration.fft_us_slowest:.2f} us, "
        f"machine load {calibration.load_percent:.1f} %)"
    )
    print(
        f"yardstick: {yardstick:.2f} us per transform "
        f"(fastest of {len(yardstick_samples)} samples, "
        f"median {float(np.percentile(np.asarray(yardstick_samples), 50.0)):.2f} us, "
        f"slowest {max(yardstick_samples):.2f} us) "
        f"-> the 64 sample budget is {harness.block_budget_us(64) / yardstick:.1f} transforms"
    )

    signal = harness.make_test_signal(max(blocks) * 4)
    targets: dict[str, Any] = {}
    for info in registry.all():
        code = info.code

        def make(code: str = code):
            module = registry.create(code, harness.SAMPLE_RATE, harness.CHANNELS)
            return module.process

        targets[code] = make
    for name, codes in CHAINS.items():

        def make_chain(codes: tuple[str, ...] = codes):
            from aurcore.api import create_engine

            engine = create_engine(harness.SAMPLE_RATE, harness.CHANNELS)
            for code in codes:
                engine.add_module(code)
            return engine.process

        targets[f"chain:{name}"] = make_chain

    modules: list[harness.ModuleMeasurement] = []
    chains: dict[str, harness.ChainMeasurement] = {}
    started = time.perf_counter()
    with harness.LoadMonitor() as monitor:
        measured = harness.measure_catalogue_calibrated(
            targets,
            block_sizes=blocks,
            rounds=runs,
            repeats=args.repeats,
            warmup=args.warmup,
            calib_repeats=args.calib_repeats,
            signal=signal,
        )
    for info in registry.all():
        measurement = measured.get(info.code)
        if measurement is None:
            continue
        cls = registry.get(info.code)
        measurement.display_name = str(getattr(cls, "display_name", info.code))
        measurement.category = str(getattr(cls, "category", ""))
        modules.append(measurement)
    for name in CHAINS:
        measurement = measured.get(f"chain:{name}")
        if measurement is None:
            continue
        chains[name] = harness.ChainMeasurement(
            codes=list(CHAINS[name]),
            timings=measurement.timings,
            error=measurement.error,
        )
    print(
        f"sweep: {len(modules)} modules and {len(chains)} chains x {runs} rounds "
        f"x {len(blocks)} block sizes in {time.perf_counter() - started:.1f} s, "
        f"machine load {monitor.mean_percent:.1f} % (peak {monitor.max_percent:.1f} %)"
    )

    # The yardstick is sampled again after the sweep: the quietest moment
    # of the session is the best estimate of the machine at rest.
    after_samples = harness.transform_us_samples(
        runs=max(4, int(args.calibration_runs) * 4), repeats=10
    )
    yardstick_samples.extend(after_samples)
    yardstick = min(yardstick_samples)
    print(
        f"yardstick after the sweep: {yardstick:.2f} us per transform "
        f"({len(after_samples)} more samples, median "
        f"{float(np.percentile(np.asarray(after_samples), 50.0)):.2f} us)"
    )

    # The allocation pass is slow, so it runs last and only once.
    allocations: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for measurement in modules:
        module = registry.create(measurement.code, harness.SAMPLE_RATE, harness.CHANNELS)
        block = signal[: blocks[0]].copy()
        try:
            module.process(block.copy())  # warm up
            stats = harness.measure_allocations(module.process, block, repeats=2, warmup=0)
        except Exception as exc:  # noqa: BLE001 - a broken module is reported
            print(f"  {measurement.code}: allocation pass failed: {type(exc).__name__}: {exc}")
            continue
        allocations[measurement.code] = stats.to_dict()
    print(f"allocation pass: {len(allocations)} modules in {time.perf_counter() - started:.1f} s")

    # -- the table ----------------------------------------------------------
    budget64 = harness.block_budget_us(min(blocks))
    lines: list[str] = []
    lines.append(
        f"{'module':<24}{'64 trf':>9}{'64 %':>8}{'128 trf':>9}{'128 %':>8}"
        f"{'256 trf':>9}{'256 %':>8}{'512 trf':>9}{'512 %':>8}{'64 us':>9}{'arrays':>8}"
    )
    for measurement in modules:
        cells = ""
        for block in blocks:
            timing = measurement.timing_for(block)
            if timing is None:
                cells += f"{'—':>9}{'—':>8}"
                continue
            share = timing.transforms * yardstick / timing.budget_us * 100.0
            flag = "!" if share > 100.0 else " "
            cells += f"{timing.transforms:>8.0f}{flag}{share:>7.1f}%"
        alloc = allocations.get(measurement.code, {})
        timing64 = measurement.timing_for(min(blocks))
        median64 = timing64.median_us if timing64 is not None else float("nan")
        lines.append(
            f"{measurement.code:<24}{cells}{median64:>9.0f}"
            f"{alloc.get('arrays_per_block', 0):>8.0f}"
        )
    lines.append("")
    for name, chain in chains.items():
        parts = []
        for block in blocks:
            timing = chain.timing_for(block)
            if timing is None:
                continue
            share = timing.transforms * yardstick / timing.budget_us * 100.0
            flag = "!" if share > 100.0 else " "
            parts.append(
                f"{block}: {timing.transforms:.0f} trf ({share:.1f} %){flag} "
                f"[{timing.median_us:.0f} us]"
            )
        lines.append(f"chain {name} ({' -> '.join(chain.codes)}): " + ", ".join(parts))
    lines.append("")
    lines.append(
        f"budget at {min(blocks)} samples: {budget64:.1f} us "
        f"= {budget64 / yardstick:.1f} transforms of {yardstick:.2f} us"
    )
    print("\n".join(lines))

    if args.markdown_path:
        target = Path(args.markdown_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_markdown(modules, chains, allocations, blocks, yardstick) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}")

    if args.json_path:
        payload = {
            "tool": "auralis.performance.sweep",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": harness.environment_info(),
            "runs": runs,
            "repeats": args.repeats,
            "warmup": args.warmup,
            "blocks": blocks,
            "calibration": calibration.to_dict(),
            "yardstick_transform_us": round(yardstick, 3),
            "yardstick_samples": [round(float(v), 3) for v in yardstick_samples],
            "load_during_sweep_percent": round(monitor.mean_percent, 1),
            "modules": [_module_row(m, blocks) for m in modules],
            "allocations": allocations,
            "chains": {
                name: {
                    "codes": list(chain.codes),
                    "timings": {
                        str(block): {
                            "median_us": round(t.median_us, 2),
                            "min_us": round(t.min_us, 2),
                            "budget_us": round(t.budget_us, 2),
                            "budget_fraction": round(t.budget_fraction, 4),
                            "over_budget": t.over_budget,
                            "transform_us": round(t.transform_us, 3),
                            "transforms": round(t.transforms, 3) if t.transform_us > 0 else None,
                        }
                        for block in blocks
                        if (t := chain.timing_for(block)) is not None
                    },
                }
                for name, chain in chains.items()
            },
        }
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
