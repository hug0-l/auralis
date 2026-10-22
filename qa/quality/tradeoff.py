"""
qa.quality.tradeoff — the noise-reduction / artefact trade-off.

Runs the shipped module and the recommended configuration over a range of
``Noise Reduction`` settings, so the report can show that the current
implementation buys noise reduction with artefacts while the recommended one
does not.

    python qa/quality/tradeoff.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, RECOMMENDED, Levers, measure_mirror, measure_module  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
LEVELS = (12.0, 14.0, 16.0, 18.0, 20.0, 24.0)


def main() -> None:
    """Print the trade-off table for both implementations."""
    OUT.mkdir(parents=True, exist_ok=True)
    data: dict[str, dict] = {"current": {}, "recommended": {}}
    t0 = time.time()

    for label, runner in (("current", "module"), ("recommended", "mirror")):
        for case in CASES:
            for level in LEVELS:
                if runner == "module":
                    m, _ = measure_module(case, params={"Noise Reduction": level,
                                                        "Tonal Reduction": level})
                else:
                    levers = replace(Levers(), noise_reduction=level,
                                      tonal_reduction=level, **RECOMMENDED)
                    m, _ = measure_mirror(case, levers)
                data[label].setdefault(case.name, {})[f"{level:g}"] = m.as_dict()

    for case in CASES:
        print(f"\n=== {case.name}")
        print(f"    {'impl':<13} {'NR':>5} {'band red':>9} {'want tone':>10} {'gain var':>9} "
              f"{'snr proj':>9}")
        for label in ("current", "recommended"):
            for level in LEVELS:
                m = data[label][case.name][f"{level:g}"]
                tone = f"{m['tone_shift_max_wanted_db']:+.2f}dB" if m["tone_shift_db"] else "-"
                print(f"    {label:<13} {level:>5.0f} {m['band_reduction_db']:>8.2f}dB "
                      f"{tone:>10} {m['gain_var_db2']:>8.2f} {m['snr_improvement_db']:>8.2f}dB")

    (OUT / "tradeoff.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'tradeoff.json'} in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
