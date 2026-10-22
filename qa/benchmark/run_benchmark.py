"""
qa.benchmark.run_benchmark — the runner of the benchmark.

It measures our modules on the cases of the benchmark, prints the table of
our numbers against the reference numbers, and says which modules are behind
and by how much.

The runner is a **reporting gate**: a module that is behind a reference is
reported, not failed.  The reference was measured on somebody else's build
on somebody else's machine, so a hard failure would make the gate a hostage
to a machine we do not control.  The runner only fails when the instrument
itself is broken.

The cost is printed on its own, on **one yardstick**: the cost of one 64
sample block, in 1024 point transforms of 8.300 us and in microseconds, for
our side and for the reference, with the sides named.  Ours is counted, the
reference's is timed on the study machine and converted into the same
yardstick, so the two numbers can be read against each other.

    python qa/benchmark/run_benchmark.py            # the table
    python qa/benchmark/run_benchmark.py --json out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    """Load a sibling of the runner by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cases_module = _load("aur_qa_benchmark_cases", _HERE / "cases.py")
harness_module = _load("aur_qa_benchmark_harness", _HERE / "harness.py")

CASES = cases_module.CASES
collect = harness_module.collect
format_table = harness_module.format_table

#: The constants of the reference, for the cost of the reference side.
reference_module = harness_module.reference

#: The yardstick the cost is printed on.
REFERENCE_TRANSFORM_US = harness_module.REFERENCE_TRANSFORM_US
BUDGET_TRANSFORMS_PER_BLOCK = harness_module.BUDGET_TRANSFORMS_PER_BLOCK
COST_BLOCK = harness_module.COST_BLOCK


#: The sibling files that hold the reference numbers of the other subjects.
#: Each of them keeps its own record shape; the runner prints them in the
#: same table shape and never merges them into the main one: the metrics of
#: the subjects are not the metrics of the main benchmark.  A file may name
#: its records differently, so the names are tried in turn and the first one
#: the file carries is used.
_SIBLINGS = (
    ("restoration", "reference_restoration.py", ("RESTORATIONS",)),
    ("voice", "reference_voice.py", ("VOICES",)),
    ("model", "reference_model.py", ("MODEL_REFERENCES", "MEASUREMENTS")),
    ("bridge", "reference_bridge.py", ("BRIDGES",)),
    ("edge", "edge_cases.py", ("EDGES",)),
)


def _sibling_records() -> list[tuple[str, tuple]]:
    """The records of the sibling files, with the subject they belong to."""
    out: list[tuple[str, tuple]] = []
    for subject, filename, attributes in _SIBLINGS:
        path = _HERE / filename
        if not path.is_file():
            continue
        module = _load(f"aur_qa_benchmark_{subject}", path)
        records = None
        for attribute in attributes:
            records = getattr(module, attribute, None)
            if records:
                break
        if not records:
            continue
        out.append((subject, tuple(records)))
    return out


def _sibling_table(subject: str, records: tuple) -> list[str]:
    """The lines of one subject's numbers."""
    lines = [f"  the {subject} numbers:"]
    header = ("    %-34s %-20s %-20s %12s %s" % ("name", "series", "case", "value", "unit"))
    lines.append(header)
    for record in records:
        name = getattr(record, "name", "?")
        series = getattr(record, "series", "?")
        case = getattr(record, "case", "*")
        value = getattr(record, "value", None)
        unit = getattr(record, "unit", "")
        if value is None:
            continue
        lines.append("    %-34s %-20s %-20s %12.4f %s" % (name, series, case, float(value), unit))
    return lines


def _summary(rows) -> list[str]:
    """The lines of the summary: what is behind and by how much."""
    behind = [row for row in rows if row.verdict == "behind"]
    if not behind:
        return ["no module is behind a reference number"]
    lines = ["%d row(s) behind a reference number:" % len(behind)]
    for row in behind:
        lines.append(
            "  %-16s %-22s %-16s %+.2f %s"
            % (row.case, row.module, row.metric, row.gap, row.unit)
        )
    return lines


def _reference_cost(case: str) -> tuple[float, dict]:
    """The cost of the reference for a case, and the cost of every series.

    The benchmark is held to the **strongest** reference number, which for a
    cost is the cheapest one the study measured.  The series are returned as
    well, so that the runner can say which reference the number came from.
    """
    per_series: dict[str, list[float]] = {}
    for reference in reference_module.references_for(case, "cost_transform_eq"):
        if reference.value is None:
            continue
        per_series.setdefault(reference.series, []).append(float(reference.value))
    if not per_series:
        return 0.0, {}
    strongest = min(min(values) for values in per_series.values())
    return strongest, per_series


def _cost_table(rows) -> list[str]:
    """The cost of both sides of the table, on the yardstick of the project.

    Ours is **counted** and the reference's is **timed on the study machine
    and converted**, and the two are the cost of one 64 sample block: the
    same block size, the same signal, the channels of the case named.
    """
    lines = [
        "the cost, both sides on one yardstick: the cost of one %d sample "
        "block, in 1024 point transforms of %.3f us (the budget of a block is "
        "%.1f transforms = %.1f us)"
        % (
            COST_BLOCK,
            REFERENCE_TRANSFORM_US,
            BUDGET_TRANSFORMS_PER_BLOCK,
            harness_module.BUDGET_US_PER_BLOCK,
        ),
        "  ours is counted, the reference's is timed on the study machine and "
        "converted into the same yardstick",
        "  our block is the signal of the case, with the channels of the case "
        "and the block size of the table; the study did not write down the "
        "channel count or the material of its own measurement, so the "
        "reference's figure is a block of the study's own rendering",
    ]
    header = (
        "    %-16s %-22s %10s %10s %12s %12s %8s %8s"
        % (
            "case",
            "module",
            "ours trf",
            "ours us",
            "ref trf",
            "ref us",
            "% budget",
            "channels",
        )
    )
    lines.append(header)
    for row in rows:
        if row.metric != "cost_transform_eq":
            continue
        strongest, _series = _reference_cost(row.case)
        ours = float(row.ours or 0.0)
        lines.append(
            "    %-16s %-22s %10.2f %10.1f %12.2f %12.1f %7.1f%% %8s"
            % (
                row.case,
                row.module,
                ours,
                ours * REFERENCE_TRANSFORM_US,
                strongest,
                strongest * REFERENCE_TRANSFORM_US,
                100.0 * ours / max(BUDGET_TRANSFORMS_PER_BLOCK, 1e-9),
                str(int(getattr(row, "channels", 0) or 0)),
            )
        )
    lines.append("")
    lines.append(
        "  the reference's cost, per series, per %d sample block on the study "
        "machine:" % COST_BLOCK
    )
    for series in sorted(reference_module.SERIES):
        values = [
            float(reference.value)
            for reference in reference_module.references_for("*", "cost_transform_eq")
            if reference.series == series and reference.value is not None
        ]
        if not values:
            continue
        lines.append(
            "    %-20s %8.4f trf = %8.1f us  to  %8.4f trf = %8.1f us"
            % (
                series,
                min(values),
                min(values) * REFERENCE_TRANSFORM_US,
                max(values),
                max(values) * REFERENCE_TRANSFORM_US,
            )
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_benchmark", description=__doc__)
    parser.add_argument("--case", default="", help="one case, or a comma separated list")
    parser.add_argument("--json", type=Path, default=None, help="write the result as JSON")
    args = parser.parse_args(argv)

    wanted = {name.strip() for name in args.case.split(",") if name.strip()}
    cases = [case for case in CASES if not wanted or case.name in wanted]
    if not cases:
        print("no such case", file=sys.stderr)
        return 2

    rows = collect(cases)
    print("the benchmark - our modules against the recorded reference")
    print()
    print(format_table(rows))
    print()
    for line in _cost_table(rows):
        print(line)
    print()
    for line in _summary(rows):
        print(line)

    siblings = _sibling_records()
    if siblings:
        print()
        print("the other subjects' reference numbers (they are not the main benchmark):")
        for subject, records in siblings:
            print()
            for line in _sibling_table(subject, records):
                print(line)

    errors = [row for row in rows if row.verdict == "error"]
    if errors:
        print()
        for row in errors:
            print("  %s on %s could not be measured: %s" % (row.module, row.case, row.note))

    if args.json:
        payload = {"rows": [row.as_dict() for row in rows]}
        payload["siblings"] = {
            subject: [
                {
                    "name": getattr(record, "name", "?"),
                    "series": getattr(record, "series", "?"),
                    "case": getattr(record, "case", "*"),
                    "metric": getattr(record, "metric", "?"),
                    "value": getattr(record, "value", None),
                    "unit": getattr(record, "unit", ""),
                    "method": getattr(record, "method", ""),
                }
                for record in records
            ]
            for subject, records in siblings
        }
        args.json.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print()
        print("wrote %s" % args.json)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
