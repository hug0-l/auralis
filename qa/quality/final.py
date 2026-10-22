"""
qa.quality.final — evaluate the recommended configurations on every case.

Writes ``qa/quality/results/final.json`` and prints the table that the
report quotes.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, Levers, measure_mirror  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"

BASE = {"masking_guard": True, "synthesis_fix": True, "temporal_smooth": True}

CANDIDATES: dict[str, dict] = {
    "0 module defaults": {},
    "1 R1 mask guard": {"masking_guard": True},
    "2 R1+R2 synthesis units": {"masking_guard": True, "synthesis_fix": True},
    "3 R1..R3 + temporal": {**BASE},
    "4 R1..R3 + floor 500/1500 Hz": {**BASE, "broad_gap_hz": 500.0, "broad_span_hz": 1500.0},
    "5 R1..R3 + floor 400/1200 Hz": {**BASE, "broad_gap_hz": 400.0, "broad_span_hz": 1200.0},
    "6 R1..R3 + floor 600/1800 Hz": {**BASE, "broad_gap_hz": 600.0, "broad_span_hz": 1800.0},
    "7 R1..R4 + reduction 15 dB": {**BASE, "broad_gap_hz": 500.0, "broad_span_hz": 1500.0,
                                   "noise_reduction": 15.0, "tonal_reduction": 15.0},
    "8 R1..R4 + reduction 18 dB": {**BASE, "broad_gap_hz": 500.0, "broad_span_hz": 1500.0,
                                   "noise_reduction": 18.0, "tonal_reduction": 18.0},
    "9 R1..R4 + reduction 18 + smooth 2": {**BASE, "broad_gap_hz": 500.0, "broad_span_hz": 1500.0,
                                           "noise_reduction": 18.0, "tonal_reduction": 18.0,
                                           "freq_smooth_bins": 2},
    "10 R1..R4 + reduction 18 + smooth 6": {**BASE, "broad_gap_hz": 500.0, "broad_span_hz": 1500.0,
                                            "noise_reduction": 18.0, "tonal_reduction": 18.0,
                                            "freq_smooth_bins": 6},
}


def main() -> None:
    """Evaluate every candidate on every case."""
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    t0 = time.time()
    for label, variant in CANDIDATES.items():
        levers = replace(Levers(), **variant)
        per_case = {}
        for case in CASES:
            metrics, _ = measure_mirror(case, levers)
            per_case[case.name] = metrics.as_dict()
        results[label] = per_case

    for case in CASES:
        print(f"\n=== {case.name} ({case.description})")
        print(f"    {'configuration':<38} {'band red':>9} {'tone all':>9} {'tone want':>10} "
              f"{'snr gain':>9} {'gain var':>9} {'gain mean':>10}")
        for label in CANDIDATES:
            m = results[label][case.name]
            tone = f"{m['tone_shift_max_db']:+.2f}dB" if m["tone_shift_db"] else "-"
            print(f"    {label:<38} {m['band_reduction_db']:>8.2f}dB {tone:>9} "
                  f"{m['tone_shift_max_wanted_db']:>9.2f}dB "
                  f"{m['snr_improvement_db']:>8.2f}dB {m['gain_var_db2']:>8.2f} "
                  f"{m['gain_mean_db']:>9.2f}dB")

    (OUT / "final.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'final.json'} in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
