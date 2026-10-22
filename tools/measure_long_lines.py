#!/usr/bin/env python3
"""
tools/measure_long_lines.py - the measurement behind the warning budget.

The gate ``tools/lint_check.py`` counts the warning level findings and
fails when there are more of them than the budget allows.  The budget is
the number of long lines the tree carries today, so the number has to be
measured, not guessed.  This script produces that measurement:

  * how many warnings the gate counts, by rule;
  * in which file every counted warning sits;
  * for every long line, whether it can be broken up (a line with a
    token that is longer than the limit can never be) and, when it
    cannot, which token it is that says so;
  * the length of the longest token of every line that cannot be broken.

Usage::

    python tools/measure_long_lines.py                # the table
    python tools/measure_long_lines.py --markdown      # a markdown table
    python tools/measure_long_lines.py --json          # machine readable
    python tools/measure_long_lines.py --files         # the files only
    python tools/measure_long_lines.py --budget 200    # the check against a budget

The exit code is 0 when the counted warnings are at or below the budget
and 1 when they are above it, so the script can be used as a gate of its
own.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lint_check  # noqa: E402  (the path is set above)


def longest_token(line: str) -> tuple[str, int]:
    """Return the longest whitespace separated token of the line."""
    best = ""
    for token in line.split():
        if len(token) > len(best):
            best = token
    return best, len(best)


def collect(root: Path, limit: int) -> list[dict[str, object]]:
    """Return one record per counted warning, with the split analysis."""
    records: list[dict[str, object]] = []
    for result in lint_check.run_checks(root, lint_check.CHECKS, limit, limit * 2):
        for finding in result.findings:
            if finding.severity != "warning":
                continue
            if finding.rule in lint_check.BUDGET_EXEMPT_RULES:
                continue
            record: dict[str, object] = {
                "rule": finding.rule,
                "path": finding.path,
                "line": finding.line,
                "message": finding.message,
            }
            if finding.rule.startswith("py-line-length"):
                path = root / finding.path
                text = lint_check._read_text(path)
                if text is not None:
                    lines = text.splitlines()
                    if 0 < finding.line <= len(lines):
                        line = lines[finding.line - 1].rstrip("\r")
                        token, token_length = longest_token(line)
                        record["length"] = len(line)
                        record["splittable"] = lint_check._can_be_split(line, limit)
                        record["longest_token_length"] = token_length
                        record["longest_token"] = token
            records.append(record)
    return records


def main(argv: list[str] | None = None) -> int:
    """Run the measurement and report the table."""
    parser = argparse.ArgumentParser(
        prog="measure_long_lines",
        description="Measure the long lines the lint budget is made of.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="the repository root")
    parser.add_argument(
        "--limit", type=int, default=lint_check.DEFAULT_MAX_LINE_LENGTH, help="the limit"
    )
    parser.add_argument("--markdown", action="store_true", help="print a markdown table")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print JSON")
    parser.add_argument("--files", action="store_true", help="print the files only")
    parser.add_argument(
        "--budget",
        type=int,
        default=lint_check.DEFAULT_WARNING_BUDGET,
        help="the budget to compare the measurement against",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    records = collect(root, args.limit)

    by_rule: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for record in records:
        by_rule[str(record["rule"])] = by_rule.get(str(record["rule"]), 0) + 1
        by_file[str(record["path"])] = by_file.get(str(record["path"]), 0) + 1

    splittable = [r for r in records if r.get("splittable") is True]
    unsplittable = [r for r in records if r.get("splittable") is False]
    unknown = [r for r in records if "splittable" not in r]

    if args.as_json:
        payload = {
            "counted": len(records),
            "by_rule": by_rule,
            "by_file": dict(sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))),
            "splittable": len(splittable),
            "unsplittable": len(unsplittable),
            "records": records,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.files:
        for path, count in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{count:4d}  {path}")
        print(f"{len(records):4d}  TOTAL in {len(by_file)} file(s)")
        return 0

    if args.markdown:
        print("| rule | count |")
        print("|---|---|")
        for rule, count in sorted(by_rule.items()):
            print(f"| `{rule}` | {count} |")
        print(f"| **total** | **{len(records)}** |")
        print()
        print("| file | count |")
        print("|---|---|")
        for path, count in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"| `{path}` | {count} |")
        return 0

    print("=" * 78)
    print(" LONG LINE MEASUREMENT")
    print(f" root: {root}")
    print(f" limit: {args.limit} characters")
    print("=" * 78)
    print()
    print(" by rule:")
    for rule, count in sorted(by_rule.items()):
        print(f"   {rule:<28} {count}")
    print(f"   {'TOTAL':<28} {len(records)}")
    print()
    print(f" splittable (a line that can be wrapped): {len(splittable)}")
    print(f" unsplittable (a token longer than the limit): {len(unsplittable)}")
    print(f" not a python line: {len(unknown)}")
    print()
    print(" by file:")
    for path, count in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"   {count:4d}  {path}")
    print()
    if unsplittable:
        print(" the lines that cannot be split:")
        for record in unsplittable:
            print(
                f"   {record['path']}:{record['line']}  "
                f"length {record.get('length')}  "
                f"longest token {record.get('longest_token_length')}  "
                f"{str(record.get('longest_token'))[:60]!r}"
            )
        print()
    if args.budget:
        state = "within" if len(records) <= args.budget else "OVER"
        print(f" counted {len(records)}, budget {args.budget}: {state}")
    return 0 if len(records) <= args.budget else 1


if __name__ == "__main__":
    raise SystemExit(main())
