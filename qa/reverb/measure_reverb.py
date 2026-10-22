#!/usr/bin/env python3
"""
qa.reverb.measure_reverb — the command line of the De-Reverb harness.

Run it from the repository root::

    python qa\\reverb\\measure_reverb.py
    python qa\\reverb\\measure_reverb.py --json var\\reverb.json
    python qa\\reverb\\measure_reverb.py --reduction 1.0

It prints the measurement table of the De-Reverb module: what it removes
from the reverberant tail, what it takes from the wanted 440 Hz starter, how
the reverberation time moves, how long one block takes against the realtime
budget, what the module does at the edges and how far the result moves when
the host chops the stream into blocks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qa.reverb import harness  # noqa: E402


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """Render a list of dictionaries as a Markdown table."""
    header = "| " + " | ".join(label for _key, label in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, rule]
    for row in rows:
        cells = []
        for key, _label in columns:
            value = row.get(key)
            if isinstance(value, float):
                cells.append(f"{value:.4f}")
            elif value is None:
                cells.append("-")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="measure_reverb",
        description="Measure the De-Reverb module on the corpus reverb clip.",
    )
    parser.add_argument(
        "--reduction",
        type=float,
        default=None,
        help="the value of the Reduction parameter (default: the shipped default)",
    )
    parser.add_argument(
        "--tail-length",
        type=float,
        default=None,
        help="the value of the Tail Length parameter (default: the shipped default)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="write the report as JSON to this path",
    )
    args = parser.parse_args(argv)

    params: dict[str, object] = {}
    if args.reduction is not None:
        params["Reduction"] = args.reduction
    if args.tail_length is not None:
        params["Tail Length"] = args.tail_length

    report = harness.measure_module(**params)

    whole = report["whole_block"]
    print("Auralis De-Reverb - measurement on the corpus clip "
          f"'{report['clip_id']}'")
    print()
    print("one block render (the whole clip in one call)")
    print(f"  tail removed        {whole['tail_removed_db']:+8.3f} dB")
    print(f"  wanted part removed {whole['wanted_removed_db']:+8.4f} dB")
    print(f"  RT60                {whole['rt60_in_s']:.5f} s -> "
          f"{whole['rt60_out_s']:.5f} s "
          f"({whole['rt60_change_s']:+.5f} s)")
    print()

    print("by block size")
    print(_table(
        [
            {
                "block": row["block"],
                "wanted_removed_db": row["wanted_removed_db"],
                "tail_removed_db": row["tail_removed_db"],
                "rt60_out_s": row["rt60_out_s"],
            }
            for row in report["by_block"]
        ],
        [
            ("block", "block"),
            ("wanted_removed_db", "wanted removed dB"),
            ("tail_removed_db", "tail removed dB"),
            ("rt60_out_s", "RT60 s"),
        ],
    ))
    print()

    print("realtime budget")
    print(_table(
        [
            {
                "block": row["block"],
                "median_us": row["median_us"],
                "min_us": row["min_us"],
                "budget_us": row["budget_us"],
                "budget_fraction": row["budget_fraction"],
            }
            for row in report["timing"]
        ],
        [
            ("block", "block"),
            ("median_us", "median us"),
            ("min_us", "fastest us"),
            ("budget_us", "budget us"),
            ("budget_fraction", "share of budget"),
        ],
    ))
    print()

    print("edges")
    print(_table(
        report["edges"],
        [
            ("case", "case"),
            ("block", "block"),
            ("length_out", "length"),
            ("non_finite", "non finite"),
            ("peak", "peak"),
        ],
    ))
    print()

    print("block coherence (difference against the one block render)")
    print(_table(
        report["block_coherence"],
        [
            ("block", "block"),
            ("rms_diff_db", "RMS difference dB"),
            ("max_diff", "max difference"),
        ],
    ))
    print()
    print(f"latency: reported {report['latency_reported']} samples, "
          f"measured {report['latency_measured']} samples")

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
