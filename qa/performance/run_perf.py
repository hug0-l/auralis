#!/usr/bin/env python3
"""
qa.performance.run_perf — the command line front end of the performance harness.

Examples
--------
    # every registered module, every block size, printed as a table
    python qa/performance/run_perf.py --all

    # one module, with a profile of its hot spots
    python qa/performance/run_perf.py --module AUR_SPECTRALDENOISE --profile

    # a chain of modules
    python qa/performance/run_perf.py --chain AUR_DEHUM,AUR_DECLICK,AUR_DEESS

    # machine readable results
    python qa/performance/run_perf.py --all --json var/perf.json

The peak resident memory of a single module is measured in a dedicated child
process, so the number belongs to the module and not to the harness.  Use
``--rss`` to enable that pass (it starts one interpreter per module).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness  # noqa: E402  (path setup has to come first)


# ---------------------------------------------------------------------------
# Peak RSS worker
# ---------------------------------------------------------------------------
def _rss_worker(codes: Sequence[str], blocks: Sequence[int], repeats: int) -> dict[str, Any]:
    """Measure the peak RSS of a chain of modules in this (child) process.

    With an empty ``codes`` the worker only imports the core, which gives
    the baseline the module measurements are compared against.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    if not codes:
        return {"peak_rss_bytes": harness.peak_rss_bytes()}

    if len(codes) == 1:
        module = registry.create(codes[0], harness.SAMPLE_RATE, harness.CHANNELS)
        process = module.process
    else:
        from aurcore.api import create_engine

        engine = create_engine(harness.SAMPLE_RATE, harness.CHANNELS)
        for code in codes:
            engine.add_module(code)
        process = engine.process

    signal = harness.make_test_signal(
        max(blocks) * 4, harness.SAMPLE_RATE, harness.CHANNELS
    )
    for block in blocks:
        data = signal[:block]
        for _ in range(3):
            process(data)
        for _ in range(repeats):
            process(data)
    return {"peak_rss_bytes": harness.peak_rss_bytes()}


def measure_peak_rss(
    codes: Sequence[str],
    blocks: Sequence[int] = (64,),
    repeats: int = 3,
    timeout: float = 300.0,
) -> int | None:
    """Return the peak RSS of a child process that only ran ``codes``.

    Parameters
    ----------
    codes:
        The module codes to run in the child process.  An empty list gives
        the baseline: a process that only imports the core.
    blocks:
        The block sizes the child feeds.
    repeats:
        How many blocks the child processes per block size.
    timeout:
        Timeout of the child process, in seconds.

    Returns
    -------
    int or None
        The peak resident set size in bytes, or ``None`` when the child failed.
    """
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--codes",
        ",".join(codes),
        "--blocks",
        ",".join(str(int(b)) for b in blocks),
        "--repeats",
        str(int(repeats)),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    value = payload.get("peak_rss_bytes")
    return int(value) if value else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="run_perf.py",
        description="Measure the realtime performance of the Auralis DSP core.",
    )
    parser.add_argument("--module", "-m", help="measure a single module")
    parser.add_argument("--all", "-a", action="store_true", help="measure every module")
    parser.add_argument(
        "--chain",
        help="measure a chain of modules (comma separated codes)",
    )
    parser.add_argument(
        "--blocks",
        default="64,128,256,512",
        help="comma separated block sizes (default 64,128,256,512)",
    )
    parser.add_argument("--repeats", type=int, default=harness.DEFAULT_REPEATS)
    parser.add_argument("--warmup", type=int, default=harness.DEFAULT_WARMUP)
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="measure every module this many times and keep the fastest run",
    )
    parser.add_argument(
        "--rss",
        action="store_true",
        help="measure the peak RSS of every module in its own process (slow)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="print a cProfile report of the measured module",
    )
    parser.add_argument("--json", dest="json_path", help="write the results to a JSON file")
    parser.add_argument("--markdown", dest="markdown_path", help="write a Markdown table")
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: the peak RSS child process
    )
    parser.add_argument("--codes", default="", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the performance CLI."""
    args = build_parser().parse_args(argv)
    blocks = [int(b) for b in str(args.blocks).split(",") if b.strip()]

    if args.worker:
        codes = [c for c in str(args.codes).split(",") if c.strip()]
        payload = _rss_worker(codes, blocks, args.repeats)
        print(json.dumps(payload))
        return 0

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    if args.module and not registry.has(args.module):
        print(f"unknown module {args.module!r}", file=sys.stderr)
        return 2

    chains: list[list[str]] = []
    if args.all:
        chains = [[info.code] for info in registry.all()]
    elif args.module:
        chains = [[args.module]]
    elif args.chain:
        chains = [[c.strip() for c in args.chain.split(",") if c.strip()]]
    else:
        print("nothing to do: pass --module CODE, --all, --chain or --list", file=sys.stderr)
        return 2

    results: list[Any] = []
    baseline_rss: int | None = None
    if args.rss:
        baseline_rss = measure_peak_rss([], blocks=blocks, repeats=1)
    started = time.perf_counter()
    for codes in chains:
        runs: list[Any] = []
        for _ in range(max(1, args.runs)):
            if len(codes) == 1:
                measurement = harness.measure_module(
                    codes[0],
                    block_sizes=blocks,
                    repeats=args.repeats,
                    warmup=args.warmup,
                )
            else:
                measurement = harness.measure_chain(
                    codes,
                    block_sizes=blocks,
                    repeats=args.repeats,
                    warmup=args.warmup,
                )
            runs.append(measurement)
        if len(codes) == 1:
            measurement = harness.merge_measurements(runs)
        else:
            measurement = harness.merge_chain_measurements(runs)
        if args.rss:
            measurement.peak_rss_bytes = measure_peak_rss(
                codes, blocks=blocks, repeats=max(2, args.repeats // 3)
            )
        results.append(measurement)
        if args.profile and len(codes) == 1:
            print(harness.profile_module(codes[0], block=min(blocks)))
    elapsed = time.perf_counter() - started

    if results and all(isinstance(r, harness.ModuleMeasurement) for r in results):
        print(harness.render_table(results, blocks, baseline_rss_bytes=baseline_rss))
        print()
        print(harness.summarise(results, block=min(blocks)))
    else:
        print(harness.render_chain_table(results))

    calibration = harness.reference_fft_us()
    budget = harness.block_budget_us(min(blocks))
    print(
        f"\ncalibration: one 1024 point rfft costs {calibration:.1f} us on this machine "
        f"({budget:.0f} us of budget = {budget / calibration:.0f} transforms)"
    )

    print(f"\nmeasured in {elapsed:.1f} s")
    if baseline_rss:
        print(f"baseline peak RSS (core import only): {baseline_rss / (1024.0 * 1024.0):.1f} MiB")

    if args.json_path:
        payload = {
            "tool": "auralis.performance",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": harness.environment_info(),
            "blocks": blocks,
            "repeats": args.repeats,
            "runs": args.runs,
            "calibration_fft1024_us": calibration,
            "baseline_peak_rss_bytes": baseline_rss,
            "results": [r.to_dict() for r in results],
        }
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {target}")

    if args.markdown_path:
        target = Path(args.markdown_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            harness.render_table(results, blocks, baseline_rss_bytes=baseline_rss) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
