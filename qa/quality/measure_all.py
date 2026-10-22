"""
qa.quality.measure_all — run the instrument over the whole registry.

The runner measures two things:

1. **every module of the registry** — so that the instrument is known to work
   for all of them and so that every module owner can see where their module
   stands;
2. **every rule** — so that the gate and the report quote the same numbers.

It writes ``qa/quality/results/measure_all.json`` and prints the table.

    python qa/quality/measure_all.py            # the registry and the rules
    python qa/quality/measure_all.py --all-cases  # every module on every case
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from aurcore import registry  # noqa: E402

from ground_truth import CASES, make_case  # noqa: E402
from measurement import measure  # noqa: E402
from quality_rules import RULES, RULES_BY_CODE  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"

#: The case a module without a rule is measured on.
DEFAULT_CASE = "tone_noise"


def _cases_for(code: str) -> list[str]:
    """The cases a module is measured on: the ones of its rules, or the default."""
    rules = RULES_BY_CODE.get(code)
    if rules:
        return sorted({rule.case for rule in rules})
    return [DEFAULT_CASE]


def main(argv: list[str] | None = None) -> int:
    """Measure every module and every rule."""
    parser = argparse.ArgumentParser(prog="measure_all")
    parser.add_argument("--all-cases", action="store_true",
                        help="measure every module on every case")
    parser.add_argument("--out", type=Path, default=OUT / "measure_all.json",
                        help="where the JSON result is written")
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    codes = sorted(info.code for info in registry.all())
    results: dict = {"modules": {}, "rules": []}
    problems: list[str] = []

    for code in codes:
        cases = [c.name for c in CASES] if args.all_cases else _cases_for(code)
        per_case = {}
        for case_name in cases:
            try:
                measurement = measure(code, case_name)
            except Exception as exc:  # noqa: BLE001 - the runner must not stop
                problems.append(f"{code} on {case_name}: {type(exc).__name__}: {exc}")
                continue
            per_case[case_name] = measurement.as_dict()
        results["modules"][code] = per_case

    print(f"{'module':<26} {'case':<16} {'removed':>9} {'kept':>8} {'gain var':>10} "
          f"{'lat':>6} {'block':>8}")
    print("-" * 92)
    for code in codes:
        for case_name, data in results["modules"].get(code, {}).items():
            print(f"{code:<26} {case_name:<16} {data['removed_db']:>8.2f}dB "
                  f"{data['kept_db']:>7.3f}dB {data['gain_var_db2']:>10.4f} "
                  f"{data['latency_samples']:>6d} {data['block_coherence_db']:>7.2f}dB")

    print()
    print(f"{'rule':<42} {'verdict':<10} detail")
    print("-" * 110)
    for rule in RULES:
        try:
            measurement = measure(
                rule.code,
                rule.case,
                params=rule.params or None,
                band=rule.band,
                tones=rule.tones,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{rule.identifier:<42} {'ERROR':<10} {type(exc).__name__}: {exc}")
            problems.append(f"rule {rule.identifier}: {type(exc).__name__}: {exc}")
            continue
        failures = rule.check(measurement)
        verdict = "holds" if not failures else "NOT MET"
        print(f"{rule.identifier:<42} {verdict:<10} {'; '.join(failures)}")
        results["rules"].append({
            "rule": rule.identifier,
            "code": rule.code,
            "case": rule.case,
            "holds": not failures,
            "failures": failures,
            "measurement": measurement.as_dict(),
        })

    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print()
    print(f"{len(codes)} modules measured, {len(results['rules'])} rules checked, "
          f"{len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    print(f"wrote {args.out} in {time.time() - started:.1f} s")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
