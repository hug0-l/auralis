"""qa.quality.resolution_probe — is the residual damage a resolution limit?"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, Levers, measure_mirror  # noqa: E402

REC = {"masking_guard": True, "synthesis_fix": True, "temporal_smooth": True,
       "tonal_mask_ref": "learned", "tonal_ref": "learned"}

QUALITY = {"B (fft 720)": (720, 180), "C (fft 960)": (960, 240), "D (fft 1440)": (1440, 360)}

for case in CASES:
    print(f"\n=== {case.name}")
    for qname, (fft, hop) in QUALITY.items():
        for tag, extra in (("defaults", {}), ("recommended", REC)):
            lv = replace(Levers(), fft=fft, hop=hop, **extra)
            m, _ = measure_mirror(case, lv)
            tones = "  ".join(f"{k}:{v:+.2f}" for k, v in m.tone_shift_db.items())
            print(f"   {qname:<16} {tag:<12} band {m.band_reduction_db:6.2f}  "
                  f"want {m.tone_shift_max_wanted_db:5.2f}  var {m.gain_var_db2:5.2f}  {tones}")
