"""Run every gate of the project and report the result in one table.

The gates:
    pytest             the whole suite
    edges              the edges of every module (finite, length, tail,
                       silence, the length of the material)
    schema             the canonical table against the registry and the schema
    css-coverage       every class the interface uses is styled
    verify-app         the server and every user visible flow (30 checks)
    hygiene            no third party name, no absolute path, no stray binary
    lint               the style gate
    vst3-host          the independent host loads the built plug-in family
    journey            the whole path through the product
    benchmark          our modules against the reference numbers (reporting)

Exit code 0 when every gate passed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path(os.environ.get("AURALIS_PYTHON", sys.executable))

GATES = [
    ("imports", [PYTHON, "qa/import_smoke.py"]),
    ("finite", [PYTHON, "qa/check_module_finite.py"]),
    # The gate against the edges: every module is driven over the edges of
    # the material and has to hold the three rules (finite, length, tail).
    ("edges", [PYTHON, "qa/check_module_edges.py"]),
    ("biquad", [PYTHON, "qa/check_biquad_shapes.py"]),
    ("js-duplicates", [PYTHON, "qa/check_js_duplicates.py"]),
    ("pytest", [PYTHON, "-m", "pytest", "tests", "-q", "--no-header",
                "-p", "no:cacheprovider", "--basetemp=var/pytest-tmp"]),
    ("schema", [PYTHON, "tools/validate_schema.py"]),
    ("codegen-drift", [PYTHON, "tools/codegen_registry.py", "--check"]),
    ("css-coverage", [PYTHON, "qa/check_css_coverage.py"]),
    ("verify-app", [PYTHON, "qa/verify_app.py"]),
    ("hygiene", [PYTHON, "tools/hygiene_check.py"]),
    ("lint", [PYTHON, "tools/lint_check.py"]),
    ("vst3-host", [PYTHON, "-m", "pytest", "tests/test_vst3_host.py", "-q",
                   "--no-header", "-p", "no:cacheprovider"]),
    ("journey", [PYTHON, "-m", "pytest", "tests/test_journey.py", "-q",
                 "--no-header", "-p", "no:cacheprovider"]),
    # The benchmark is a reporting gate: it prints the table of our modules
    # against the reference numbers the study recorded and says which modules
    # are behind.  A reference is a target, not a contract, so a module that
    # is behind one does not fail the gate.
    ("benchmark", [PYTHON, "qa/benchmark/run_benchmark.py"]),
]


def run(name: str, command: list) -> tuple[bool, str, float]:
    """Run one gate.  Returns (passed, the last lines of its output, seconds)."""
    started = time.time()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)

    result = subprocess.run([str(c) for c in command], cwd=ROOT, env=environment,
                            capture_output=True, text=True)

    output = (result.stdout or "") + (result.stderr or "")
    lines = [line for line in output.splitlines() if line.strip()]
    tail = "\n".join(lines[-6:]) if lines else "(no output)"

    return result.returncode == 0, tail, time.time() - started


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run every gate")
    ap.add_argument("--only", default="", help="a comma separated list of gates")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--quiet", action="store_true", help="only the verdict")
    args = ap.parse_args(argv)

    gates = GATES
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        gates = [g for g in GATES if g[0] in wanted]

    results = []
    failures = 0

    for name, command in gates:
        passed, tail, seconds = run(name, command)
        results.append({"gate": name, "passed": passed, "seconds": round(seconds, 1),
                         "tail": tail})
        if not passed:
            failures += 1
        if not args.quiet:
            print(f"{'PASS' if passed else 'FAIL'}  {name:<14} {seconds:7.1f}s")
            if not passed:
                for line in tail.splitlines():
                    print("        " + line)

    if args.json:
        import json

        print(json.dumps({"passed": failures == 0, "gates": results}, indent=1))
    else:
        print()
        print(f"{len(gates) - failures}/{len(gates)} gates passed")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
