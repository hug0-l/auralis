"""qa.quality.validate — prove the mirror reproduces the module exactly.

The mirror reproduces the gain law of the Spectral De-Noise **as it was before
the change of ``qa/quality/STUDY.md`` section 4**.  While the module carries
that change the two no longer agree, and this script says so: the lever
results of the study describe the implementation the change was written for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, Levers, measure_module, measure_mirror  # noqa: E402


def main() -> None:
    """Compare module and mirror output for the default lever set."""
    ok = True
    for case in CASES:
        mm, mod_out = measure_module(case)
        mv, mir_out = measure_mirror(case, Levers())
        diff = (
            float(np.max(np.abs(mod_out - mir_out)))
             if mod_out.shape == mir_out.shape else float("nan")
        )
        rel = diff / max(float(np.max(np.abs(mod_out))), 1e-12)
        same = (abs(mm.band_reduction_db - mv.band_reduction_db) < 1e-6
                and abs(mm.snr_improvement_db - mv.snr_improvement_db) < 1e-6
                and abs(mm.tone_shift_max_db - mv.tone_shift_max_db) < 1e-6)
        ok = ok and same and rel < 1e-9
        print(f"{case.name:<16} max|Δout| = {diff:.3e}  rel = {rel:.3e}  "
              f"metrics match: {same}")
        print(f"{'':<16} module: band {mm.band_reduction_db:+.4f} dB  snr "
              f"{mm.snr_improvement_db:+.4f} dB  "
              f"tone {mm.tone_shift_max_db:+.4f} dB  gainvar {mm.gain_var_db2:.4f}")
        print(f"{'':<16} mirror: band {mv.band_reduction_db:+.4f} dB  snr "
              f"{mv.snr_improvement_db:+.4f} dB  "
              f"tone {mv.tone_shift_max_db:+.4f} dB  gainvar {mv.gain_var_db2:.4f}")
    if ok:
        print("MIRROR MATCHES MODULE")
        return
    print("MIRROR DOES NOT MATCH MODULE")
    print(
        "The mirror reproduces the gain law of the implementation *before* the "
        "change of qa/quality/STUDY.md section 4; the module carries that "
        "change now.  The lever results of the study describe the old "
        "implementation, which is what the change was written for."
    )


if __name__ == "__main__":
    main()
