"""
qa.quality.verify_patch — measure the patched module.

Runs inside the throw-away verification tree that ``qa/quality/make_patch.py``
builds, so ``import aurcore`` resolves to the *patched* copy of the core.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CASES, measure_module  # noqa: E402

LEVELS = (12.0, 15.0, 18.0)


def main() -> None:
    """Print the measured quality of the patched module."""
    out: dict[str, dict] = {}
    for level in LEVELS:
        print(f"\n### patched module, Noise Reduction = {level:g} dB")
        print(f"    {'case':<16} {'band red':>9} {'want tone':>10} {'gain var':>9} {'snr proj':>9}")
        for case in CASES:
            m, _ = measure_module(case, params={"Noise Reduction": level,
                                                "Tonal Reduction": level})
            out.setdefault(case.name, {})[f"{level:g}"] = m.as_dict()
            tone = f"{m.tone_shift_max_wanted_db:+.2f}dB" if case.tones else "-"
            print(f"    {case.name:<16} {m.band_reduction_db:>8.2f}dB {tone:>10} "
                  f"{m.gain_var_db2:>8.2f} {m.snr_improvement_db:>8.2f}dB")
    (Path(__file__).resolve().parent / "patched.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
