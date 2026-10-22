"""qa.quality.tonal_ref_probe — which reference protects the wanted partials?"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, Levers, measure_mirror  # noqa: E402

VARIANTS = {
    "module (broadband ref)": {},
    "learned tonal ref only": {"tonal_ref": "learned"},
    "learned tonal mask": {"tonal_mask_ref": "learned"},
    "learned mask + ref": {"tonal_mask_ref": "learned", "tonal_ref": "learned"},
    "learned mask + ref + wide floor": {"tonal_mask_ref": "learned", "tonal_ref": "learned",
                                         "broad_gap_hz": 500.0, "broad_span_hz": 1500.0},
    "learned mask + ref + wide floor + mask guard": {
        "tonal_mask_ref": "learned", "tonal_ref": "learned",
        "broad_gap_hz": 500.0, "broad_span_hz": 1500.0, "masking_guard": True},
    "learned mask + ref + wide floor + guard + synth + NR18": {
        "tonal_mask_ref": "learned", "tonal_ref": "learned",
        "broad_gap_hz": 500.0, "broad_span_hz": 1500.0, "masking_guard": True,
        "synthesis_fix": True, "temporal_smooth": True,
        "noise_reduction": 18.0, "tonal_reduction": 18.0},
}

for case in CASES:
    print(f"\n=== {case.name}")
    for label, variant in VARIANTS.items():
        m, _ = measure_mirror(case, replace(Levers(), **variant))
        tones = "  ".join(f"{k}:{v:+.2f}" for k, v in m.tone_shift_db.items())
        print(f"   {label:<48} band {m.band_reduction_db:6.2f}  want "
              f"{m.tone_shift_max_wanted_db:5.2f}  "
              f"var {m.gain_var_db2:5.2f}  {tones}")
