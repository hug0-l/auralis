"""qa.quality.release_probe — does the release mapping mute wanted transients?"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, Levers, measure_mirror  # noqa: E402

VARIANTS = {
    "module mapping (release on gain-up)": {},
    "swapped (attack on gain-up)": {"release_mapping": "swapped"},
    "swapped + temporal on": {"release_mapping": "swapped", "temporal_smooth": True},
    "swapped + release 20 ms": {"release_mapping": "swapped", "temporal_smooth": True,
                                 "release_s": 0.02},
    "swapped + release 300 ms": {"release_mapping": "swapped", "temporal_smooth": True,
                                  "release_s": 0.3},
}

for case in CASES:
    print(f"\n=== {case.name}")
    for label, variant in VARIANTS.items():
        m, _ = measure_mirror(case, replace(Levers(), **variant))
        tones = "  ".join(f"{k}:{v:+.2f}" for k, v in m.tone_shift_db.items())
        print(f"   {label:<34} band {m.band_reduction_db:6.2f} dB  want-tone "
              f"{m.tone_shift_max_wanted_db:5.2f} dB  gainvar {m.gain_var_db2:5.2f}  {tones}")
