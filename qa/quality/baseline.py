"""
qa.quality.baseline — the baseline numbers of the current implementation.

Prints (and stores) the measured quality of the shipped Spectral De-Noise
module for every synthetic case.  This is the reference the report quotes.

    python qa/quality/baseline.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (CASES, TONE_SNR_FLOOR_DB, band_power, measure_module,  # noqa: E402
                     partial_snr_db)

OUT = Path(__file__).resolve().parent / "results"


def main() -> None:
    """Measure the shipped module and write the baseline table."""
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    data: dict[str, dict] = {}
    for case in CASES:
        metrics, out = measure_module(case)
        per_tone = {}
        for freq in case.tones:
            key = f"{freq:.0f}Hz"
            per_tone[key] = {
                "snr_db": round(partial_snr_db(case.clean, case.noise, freq), 3),
                "level_in_db": round(metrics.tone_before_db[key], 3),
                "level_out_db": round(metrics.tone_after_db[key], 3),
                "shift_db": round(metrics.tone_shift_db[key], 3),
                "counts_as_wanted": bool(partial_snr_db(case.clean, case.noise, freq)
                                         >= TONE_SNR_FLOOR_DB),
            }
        err = out - case.clean
        data[case.name] = {
            "description": case.description,
            "band_hz": list(case.band),
            "band_power_in": band_power(case.mixed, *case.band),
            "band_power_out": band_power(out, *case.band),
            "metrics": metrics.as_dict(),
            "tones": per_tone,
            "error_vs_clean_db": {
                "band": round(10.0 * __import__("math").log10(max(
                    band_power(err, *case.band), 1e-30) / max(
                    band_power(case.noise, *case.band), 1e-30)), 3),
            },
        }

    for name, entry in data.items():
        m = entry["metrics"]
        print(f"\n=== {name}: {entry['description']}")
        print(f"    noise band {entry['band_hz'][0]:.0f}-{entry['band_hz'][1]:.0f} Hz")
        print(f"    band energy   {m['band_before_db']:+.2f} dB -> {m['band_after_db']:+.2f} dB"
              f"   reduction {m['band_reduction_db']:.2f} dB")
        print(f"    SNR (projection) {m['snr_in_db']:+.2f} dB -> {m['snr_out_db']:+.2f} dB"
              f"   improvement {m['snr_improvement_db']:+.2f} dB")
        print(f"    gain curve in band: mean {m['gain_mean_db']:+.2f} dB,"
              f" temporal variance {m['gain_var_db2']:.3f} dB^2"
              f" (std {m['gain_std_db']:.3f} dB), band mean range {m['gain_range_db']:.2f} dB")
        for key, t in entry["tones"].items():
            flag = "wanted" if t["counts_as_wanted"] else "near floor"
            print(f"    tone {key:>8}  SNR {t['snr_db']:+7.2f} dB ({flag:<9})"
                  f"  {t['level_in_db']:+7.2f} dB -> {t['level_out_db']:+7.2f} dB"
                  f"  shift {t['shift_db']:+.3f} dB")
        print(f"    worst tone shift (wanted partials only): "
              f"{m['tone_shift_max_wanted_db']:.3f} dB")

    (OUT / "baseline.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'baseline.json'} in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
