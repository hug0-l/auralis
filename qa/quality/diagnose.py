"""qa.quality.diagnose — isolate which module feature costs what.

Runs the real module over every case with individual features switched off
(Masking, Enhancement, Whitening, Noise Floor Synthesis) and prints the
resulting metrics, so the report can attribute the losses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, measure_module  # noqa: E402

VARIANTS = {
    "as shipped": {},
    "no masking": {"Masking": 0.0},
    "no enhancement": {"Enhancement": 0.0},
    "no whitening": {"Whitening": 0.0},
    "no synthesis": {"Noise Floor Synthesis": 0.0},
    "no noise-floor feats": {"Masking": 0.0, "Enhancement": 0.0,
                             "Whitening": 0.0, "Noise Floor Synthesis": 0.0},
    "no multi-res": {"Multi Resolution": False},
    "quality C": {"Quality": "C"},
    "quality D": {"Quality": "D"},
}


def main() -> None:
    """Print the attribution table."""
    for case in CASES:
        print("=" * 92)
        print(f"{case.name}: {case.description}")
        print(f"  band {case.band[0]:.0f}-{case.band[1]:.0f} Hz, tones {case.tones}")
        print(f"{'variant':<24} {'band red':>9} {'tone max':>9} {'snr gain':>9} {'gain var':>9} "
              f"{'gain mean':>10}")
        for label, params in VARIANTS.items():
            m, _ = measure_module(case, params=params)
            tone = f"{m.tone_shift_max_db:+.2f}dB" if case.tones else "-"
            print(f"{label:<24} {m.band_reduction_db:>8.2f}dB {tone:>9} "
                  f"{m.snr_improvement_db:>8.2f}dB {m.gain_var_db2:>8.2f} {m.gain_mean_db:>9.2f}dB")


if __name__ == "__main__":
    main()
