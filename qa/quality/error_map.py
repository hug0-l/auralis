"""qa.quality.error_map — where does the leftover error energy live?"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, GainProbe, SR, band_power  # noqa: E402

BANDS = [(0.0, 500.0), (500.0, 900.0), (900.0, 1500.0), (1500.0, 4000.0),
         (4000.0, 8000.0), (8000.0, 12000.0), (12000.0, 16000.0),
         (16000.0, 24000.0)]

VARIANTS = {
    "as shipped": {},
    "no synthesis": {"Noise Floor Synthesis": 0.0},
    "no whitening": {"Whitening": 0.0},
    "no masking": {"Masking": 0.0},
    "no noise-floor feats": {"Masking": 0.0, "Enhancement": 0.0,
                             "Whitening": 0.0, "Noise Floor Synthesis": 0.0},
}


def run(case, params) -> None:
    module = GainProbe(SR, 1)
    for k, v in params.items():
        module.params.set(k, v)
    module.learn(case.noise[:, None])
    module.process(case.noise[:, None])
    out = np.asarray(module.process(case.mixed[:, None]))[:, 0]
    err = out - case.clean
    print(f"  -- {params}")
    print(f"     {'band':>16} {'noise in':>10} {'err out':>10} {'delta':>8}")
    for lo, hi in BANDS:
        pn = band_power(case.noise, lo, hi)
        pe = band_power(err, lo, hi)
        d = 10 * np.log10(max(pe, 1e-30) / max(pn, 1e-30)) if pn > 0 else float("nan")
        print(f"     {lo:7.0f}-{hi:<8.0f} {pn:10.3e} {pe:10.3e} {d:+8.2f}")


for case in CASES:
    if case.name == "bed_only":
        continue
    print("=" * 70)
    print(f"case {case.name}")
    for label, params in VARIANTS.items():
        run(case, params)
