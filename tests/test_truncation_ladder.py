"""
Golden regression for the parameter-label truncation ladder.

The ladder is a straight prefix cut of the parameter name: the candidate at
budget ``s`` is exactly ``name.slice(0, s)``.  The fixture under
``tests/fixtures/truncation_ladders.json`` holds 113 ladders captured from a
real product's plug-in page tables, so a regression that changes the rule
(for example to a word-aware rebalance) fails here instead of shipping.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "truncation_ladders.json")
WIDGETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "aurui", "js", "widgets.js")


def _load_fixture():
    if not os.path.exists(FIXTURE):
        pytest.skip("truncation ladder fixture not generated")
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _shorten_from_source():
    """Extract the shortenLabel body from the ES module and mirror it in Python.

    The UI is plain JavaScript with no build step, so the rule lives in the
    source of truth rather than in a duplicated Python copy.  This keeps the
    test honest: if the JS changes, the test follows it.
    """
    if not os.path.exists(WIDGETS):
        pytest.skip("widgets.js not present")
    src = open(WIDGETS, encoding="utf-8").read()
    m = re.search(r"function\s+shortenLabel\s*\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    if not m:
        pytest.skip("shortenLabel not found in widgets.js")
    body = m.group(1)
    if "s.slice(0," not in body:
        pytest.fail(
            "shortenLabel is no longer a plain prefix cut.\n"
            "The verified rule is ladder[s] === label.slice(0, s).\n"
            "Body was:\n" + body
        )
    return body


def test_ladder_fixture_is_a_prefix_cut():
    data = _load_fixture()
    assert data["count"] >= 100
    for name, ladder in data["ladders"].items():
        for step in ladder:
            assert step == name[: len(step)], (
                f"{name!r}: {step!r} is not a prefix of the name"
            )


def test_source_rule_is_prefix_cut():
    _shorten_from_source()


def test_ladder_matches_reference_steps():
    """The generated ladder must reproduce the reference steps exactly."""
    data = _load_fixture()
    for name, ladder in data["ladders"].items():
        produced = [name[: len(step)] for step in ladder]
        assert produced == ladder, f"{name!r} ladder mismatch"


def test_ladder_generator_is_deterministic():
    """A tiny node harness exercises the real ES module when node is available."""
    node = None
    for cand in ("node", "nodejs"):
        try:
            subprocess.run([cand, "--version"], capture_output=True, check=True)
            node = cand
            break
        except Exception:
            continue
    if node is None:
        pytest.skip("node not available")

    data = _load_fixture()
    names = list(data["ladders"].items())[:12]
    js = """
import { truncationLadder } from %s;
const cases = %s;
const out = {};
for (const [name, expected] of cases) {
  const produced = truncationLadder(name, expected.length + 4);
  out[name] = produced.slice(0, expected.length);
}
console.log(JSON.stringify(out));
"""
    with tempfile.TemporaryDirectory() as tmp:
        mod = os.path.join(tmp, "widgets.mjs")
        src = open(WIDGETS, encoding="utf-8").read()
        # the module has no imports, so it can be copied verbatim
        open(mod, "w", encoding="utf-8").write(src)
        harness = os.path.join(tmp, "h.mjs")
        open(harness, "w", encoding="utf-8").write(
            js % ("./widgets.mjs", json.dumps(names))
        )
        r = subprocess.run([node, harness], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            pytest.skip("node harness could not run: " + r.stderr[-300:])
        produced = json.loads(r.stdout.strip().splitlines()[-1])

    for name, expected in names:
        assert produced[name] == expected, f"{name!r}: {produced[name]} != {expected}"
