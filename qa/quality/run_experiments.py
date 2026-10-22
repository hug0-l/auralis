"""
qa.quality.run_experiments — the lever sweep.

Runs the mirror (validated bit-exact against the module) over every lever,
one lever at a time, starting from the module's own defaults, and writes the
raw numbers to ``qa/quality/results/experiments.json``.

    python qa/quality/run_experiments.py
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
SWEEP_CASES = ["tone_noise", "voice_hiss", "music_mixture"]

#: name -> list of lever overrides (one at a time, from the defaults)
SWEEPS: dict[str, list[dict]] = {
    "over_subtraction": [{"over_subtraction": v} for v in (1.0, 1.5, 2.0, 3.0, 4.0)],
    "over_subtraction_subtract_law": [
        {"law": "subtract", "over_subtraction": v} for v in (1.0, 1.5, 2.0, 3.0, 4.0)],
    "gain_floor": [
        {"law": "subtract", "over_subtraction": 1.5, "gain_floor_db": v}
        for v in (-12.0, -18.0, -24.0, -30.0, -40.0)],
    "dd_alpha": [{"omlsa": True, "dd_alpha": v} for v in (0.80, 0.90, 0.95, 0.98, 0.995)],
    "dd_alpha_power_units": [
        {"omlsa": True, "noise_power_units": "power", "dd_alpha": v}
        for v in (0.80, 0.90, 0.95, 0.98, 0.995)],
    "freq_smooth_bins": [{"freq_smooth_bins": v} for v in (1, 2, 3, 4, 6, 9, 13)],
    "noise_percentile": [{"noise_percentile": v} for v in (5, 10, 20, 30, 50, 70, 90)],
    "floor_bias_db": [{"floor_bias_db": v} for v in (0, 3, 6, 9, 12, 15, 20)],
    "broad_gap_span": [{"broad_gap": g, "broad_span": s}
                        for g, s in ((2, 8), (3, 12), (4, 16), (6, 24), (8, 32), (12, 48))],
    "tonal_ref": [{"tonal_ref": v} for v in ("broad", "learned")],
    "masking": [{"masking": v} for v in (0.0, 0.25, 0.5, 0.75)],
    "masking_guard": [{"masking_guard": True}, {"masking": 0.0}],
    "synthesis_fix": [{"synthesis_fix": True}, {"synthesis": 0.0}],
    "temporal_smooth": [{"temporal_smooth": True}],
    "learned_adaptive": [{"learned_adaptive": False}, {"floor_from_learned": False}],
}

#: candidate combined configurations
CANDIDATES: dict[str, dict] = {
    "module defaults": {},
    "A: no mask relaxation": {"masking_guard": True},
    "B: A + synthesis units": {"masking_guard": True, "synthesis_fix": True},
    "C: B + temporal smoothing": {"masking_guard": True, "synthesis_fix": True,
                                  "temporal_smooth": True},
    "D: C + learned tonal ref": {"masking_guard": True, "synthesis_fix": True,
                                 "temporal_smooth": True, "tonal_ref": "learned"},
    "E: D + wider broadband floor": {"masking_guard": True, "synthesis_fix": True,
                                     "temporal_smooth": True, "tonal_ref": "learned",
                                     "broad_gap": 6, "broad_span": 24},
    "F: E + over-subtraction 2": {"masking_guard": True, "synthesis_fix": True,
                                  "temporal_smooth": True, "tonal_ref": "learned",
                                  "broad_gap": 6, "broad_span": 24,
                                  "over_subtraction": 2.0},
    "G: F + smooth 9": {"masking_guard": True, "synthesis_fix": True,
                        "temporal_smooth": True, "tonal_ref": "learned",
                        "broad_gap": 6, "broad_span": 24,
                        "over_subtraction": 2.0, "freq_smooth_bins": 9},
}


def _run(overrides: dict, case_names: list[str]) -> dict[str, dict]:
    """Run one configuration over the given cases."""
    levers = replace(Levers(), **overrides)
    out: dict[str, dict] = {}
    for name in case_names:
        case = next(c for c in CASES if c.name == name)
        metrics, _ = measure_mirror(case, levers)
        out[name] = metrics.as_dict()
    return out


def _table(title: str, rows: dict[str, dict[str, dict]], case_names: list[str]) -> None:
    """Print a sweep table."""
    for name in case_names:
        print(f"\n  --- {name} ---")
        print(f"    {'configuration':<34} {'band red':>9} {'tone max':>9} {'snr gain':>9} "
              f"{'gain var':>9}")
        for label, per_case in rows.items():
            m = per_case[name]
            tone = f"{m['tone_shift_max_db']:+.2f}dB" if m["tone_shift_db"] else "-"
            print(f"    {label:<34} {m['band_reduction_db']:>8.2f}dB {tone:>9} "
                  f"{m['snr_improvement_db']:>8.2f}dB {m['gain_var_db2']:>8.2f}")


def main() -> None:
    """Run every sweep and write the results."""
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {"sweeps": {}, "candidates": {}}
    t0 = time.time()

    for lever, variants in SWEEPS.items():
        print(f"\n=== lever: {lever} " + "=" * (60 - len(lever)))
        rows: dict[str, dict] = {}
        for variant in variants:
            label = ", ".join(f"{k}={v}" for k, v in variant.items())
            per_case = _run(variant, SWEEP_CASES)
            rows[label] = per_case
            results["sweeps"].setdefault(lever, {})[label] = per_case
        _table(lever, rows, SWEEP_CASES)

    print("\n=== candidate configurations " + "=" * 40)
    cand_rows: dict[str, dict] = {}
    for label, variant in CANDIDATES.items():
        per_case = _run(variant, SWEEP_CASES)
        cand_rows[label] = per_case
        results["candidates"][label] = per_case
    _table("candidates", cand_rows, SWEEP_CASES)

    (OUT / "experiments.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'experiments.json'} in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
