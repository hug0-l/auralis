#!/usr/bin/env python3
"""
tools/extract_truncation_ladders.py -- pull the parameter-label truncation ladders
out of the reference AAX page tables and turn them into a golden fixture.

The rule the ladders encode is:  ladder[s] === label.slice(0, s)
for s = 1 .. len(label) - 1.  The ladder is a straight prefix cut of the
parameter name, and the fixture locks that behaviour into the test suite.
"""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "truncation_ladders.json")


def harvest(pagetables_dir: str) -> dict:
    out: dict[str, list[str]] = {}
    for dirpath, _dirs, files in os.walk(pagetables_dir):
        for fn in files:
            if fn != "AAXPageTables.XML":
                continue
            path = os.path.join(dirpath, fn)
            try:
                root = ET.parse(path).getroot()
            except Exception:
                continue
            for ctrl in root.iter("Ctrl"):
                name = ctrl.get("ID")
                if not name:
                    continue
                steps = []
                for nm in ctrl.findall("name"):
                    sz = nm.get("sz")
                    # keep trailing spaces: "Click" and "Click " are different
                    # ladder steps, and the trailing space is part of the cut
                    val = (nm.text or "").replace("\n", "").replace("\r", "").replace("\t", "")
                    if sz is None or not val:
                        continue
                    steps.append((int(sz), val))
                if not steps:
                    continue
                steps.sort()
                ladder = [v for _s, v in steps]
                # keep only ladders that are a pure prefix cut
                ok = all(ladder[i] == name[: len(ladder[i])] for i in range(len(ladder)))
                if ok:
                    out[name] = ladder
    return out


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src or not os.path.isdir(src):
        print("usage: extract_truncation_ladders.py <dir-containing-AAXPageTables.XML>")
        print("       writes tests/fixtures/truncation_ladders.json")
        return 2
    ladders = harvest(src)
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    payload = {
        "_generated": (
            "do not edit - this file is produced by "
            "tools/extract_truncation_ladders.py"
        ),
        "rule": "ladder[s] === label.slice(0, s)",
        "count": len(ladders),
        "ladders": ladders,
    }
    with open(FIXTURE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    print(f"wrote {FIXTURE}  ({len(ladders)} ladders)")
    for name, lad in list(ladders.items())[:6]:
        print(f"   {name!r}: {lad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
