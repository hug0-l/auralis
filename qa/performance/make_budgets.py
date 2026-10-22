#!/usr/bin/env python3
"""
qa.performance.make_budgets — write the realtime budget table from a sweep.

The budget table is the *gate*: it says how much a module may cost for one
block of audio.  It is written by hand, reviewed and committed, because a
budget that is regenerated automatically is not a budget — it would always
equal the measurement and would never catch anything.

This script is the tool that writes the table.  It reads a sweep
(``qa/performance/sweep.py --json ...``) and produces
``qa/performance/budgets.py``:

* a module that fits into the realtime budget of a 64 sample block gets
  the **realtime budget** — the requirement itself,
* a module that does not fit gets the **measured cost plus a margin** —
  a debt ceiling that holds the module where it is today and fails when it
  gets worse.  The margin is what makes the test green today and still
  catch a regression.

The numbers are stored in **transform equivalents**: how many 1024 point
transforms a block may cost.  That is a machine independent unit, so the
verdict of the guard does not depend on how busy the machine is.

Example
-------
    python qa/performance/sweep.py --runs 5 --json var/perf/sweep.json
    python qa/performance/make_budgets.py --json var/perf/sweep.json

THE REFERENCE MACHINE
---------------------
The budget is written in **transform equivalents**, so the number a module
is held at does not depend on how busy the machine that measured it was.
The *reference* is the yardstick the requirement is expressed in: the
realtime budget of a 64 sample block (1333.3 us) is
``1333.3 / 8.3`` = 160.6 transform equivalents.

That reference is deliberately **not** the yardstick of the session that
runs the sweep.  The yardstick of a session moves with the load of the
machine (6.25 us on a quiet machine, 8.3 us on a busy one), and a
requirement that moves with the load of the measuring session is not a
requirement.  The tool therefore keeps the reference of the committed
table unless it is told otherwise, and it prints both numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from harness_loader import load_harness  # noqa: E402  (path setup has to come first)

harness = load_harness(_HERE)

#: Where the generated table goes.
TARGET = _HERE / "budgets.py"

#: The cost of one 1024 point transform on the reference machine, in
#: microseconds.  It is the yardstick the *requirement* is written in: the
#: realtime budget of a 64 sample block is 1333.3 us, which is 160.6
#: transform equivalents of this yardstick.
#:
#: It is a constant of the project and not of a session: the yardstick a
#: sweep measures moves with the load of the machine, and the requirement
#: must not move with it.  ``main()`` reads the reference of the committed
#: table and falls back to this value.
REFERENCE_TRANSFORM_US = 8.300

#: The margin a module that does not fit into the realtime budget gets.
#: The measured cost of a module moves by up to 1.35x between two
#: sessions on this shared machine, so the margin has to cover that.
#: 50 % still fails a module that became half again as slow.
DEBT_MARGIN = 0.5

#: A module is only held at the realtime budget when it is at least this
#: far below it.  The measurement of a shared machine resolves the load
#: to about 20 %, so a module inside that band cannot be held at the
#: budget without failing on noise: it is held at its measured cost
#: instead, and the report names it.
COMFORT_THRESHOLD = 0.8

#: The margin of the per module allocation baselines.
ALLOC_MARGIN = 1.5

#: The modules whose owner is repairing the realtime cost right now.  They
#: are the ones the report has to name.  A module that has been repaired
#: comes off this list: it is held at the realtime budget again, so that a
#: regression fails the gate.
#:
#: The Spectral De-Noise was on this list until its streaming rewrite landed
#: on 2026-10-21: it cost 568 transform equivalents for a 64 sample block
#: then (354 % of the budget) and it costs 3 now, so it is held at the
#: realtime budget again and a regression fails the gate.
UNDER_REPAIR: tuple[str, ...] = ()


def _note_lines(note: str) -> list[str]:
    """Return the source lines of a note that fits into 100 columns.

    A long note is written as a concatenation of string literals, so the
    generated table stays inside the line length limit of the project.
    """
    single = f"        note={note!r},"
    if len(single) <= 100:
        return [single]
    chunks: list[str] = []
    current = ""
    for word in note.split(" "):
        candidate = current + word + " "
        if len(f"            {candidate!r}") > 96 and current:
            chunks.append(current)
            current = word + " "
        else:
            current = candidate
    chunks.append(current)
    lines = ["        note=("]
    for chunk in chunks:
        lines.append(f"            {chunk!r}")
    lines.append("        ),")
    return lines


def _budget_transforms(block: int, reference_transform_us: float) -> float:
    """Return the realtime budget of one block, in transform equivalents."""
    return harness.block_budget_us(block) / reference_transform_us


def _measured_of(entry: dict[str, Any]) -> float | None:
    """Return the measured cost of one block from a sweep entry.

    The median of the rounds is used, not the fastest round: the median
    is the typical cost of the module, and the fastest round is the one
    that was lucky.  A budget that is derived from the median holds the
    module where it really is.
    """
    value = entry.get("transforms_median")
    if value is None:
        value = entry.get("transforms")
    if value is None:
        return None
    return float(value)


def _budget_for(
    measured: dict[int, float],
    budget_transforms: dict[int, float],
) -> tuple[str, dict[int, float], int, str]:
    """Return the kind, the ceiling, the worst block and the note.

    A module that is comfortably inside the realtime budget is held at
    the realtime budget — that is the requirement.  A module that is not
    (or that is inside the noise band of the measurement) is held at its
    measured cost plus a margin: a ceiling that fails when the module
    gets worse, until its owner brings it down.
    """
    worst = max(measured, key=lambda b: measured[b] / budget_transforms[b])
    comfortable = all(
        measured[b] <= COMFORT_THRESHOLD * budget_transforms[b] for b in measured
    )
    if comfortable:
        kind = "realtime"
        ceiling = {b: budget_transforms[b] for b in measured}
        note = f"fits the realtime budget at {worst} samples"
    else:
        kind = "debt"
        ceiling = {b: round(measured[b] * (1.0 + DEBT_MARGIN), 4) for b in measured}
        share = measured[worst] / budget_transforms[worst] * 100.0
        note = (
            f"{share:.0f} % of the {worst} sample budget; "
            f"debt ceiling = measured + {DEBT_MARGIN * 100:.0f} %"
        )
    return kind, ceiling, worst, note


def build(sweep: dict[str, Any], reference_transform_us: float) -> str:
    """Return the source of the budget table for one sweep."""
    blocks = [int(b) for b in sweep.get("blocks", (64, 128, 256, 512))]
    modules = sweep.get("modules", [])
    chains = sweep.get("chains", {})
    allocations = sweep.get("allocations", {})
    budget_transforms = {
        block: _budget_transforms(block, reference_transform_us) for block in blocks
    }

    lines: list[str] = []
    lines.append('"""')
    lines.append("qa.performance.budgets — the realtime budgets of the Auralis core.")
    lines.append("")
    lines.append("This file is the gate of ``tests/test_performance.py``.  It says how")
    lines.append("much a module and a product chain may cost for one block of audio.")
    lines.append("")
    lines.append("It is written by ``qa/performance/make_budgets.py`` from a sweep")
    lines.append("(``qa/performance/sweep.py``) and it is committed.  A budget that")
    lines.append("were regenerated on every test run would always equal the")
    lines.append("measurement and would never catch anything.")
    lines.append("")
    lines.append("The unit is the **transform equivalent**: how many 1024 point")
    lines.append("transforms a block may cost.  It is machine independent, so the")
    lines.append("verdict does not depend on how busy the machine that runs the")
    lines.append("test is.  The realtime budget of a block at 48 kHz is")
    lines.append("``block / 48000`` seconds, which is")
    lines.append(
        f"{harness.block_budget_us(64) / reference_transform_us:.1f} transform equivalents"
    )
    lines.append("for a 64 sample block on the reference machine.")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from dataclasses import dataclass, field")
    lines.append("")
    lines.append("#: The cost of one 1024 point transform on the reference machine,")
    lines.append("#: in microseconds.  It is the yardstick every measurement is")
    lines.append("#: divided by, so a timing becomes a machine independent number.")
    lines.append(f"REFERENCE_TRANSFORM_US = {reference_transform_us:.3f}")
    lines.append("")
    lines.append("#: The block sizes the guard measures.")
    lines.append(f"BLOCK_SIZES: tuple[int, ...] = {tuple(blocks)!r}")
    lines.append("")
    lines.append("#: The realtime budget of one block, in microseconds.")
    lines.append("BUDGET_US: dict[int, float] = {")
    for block in blocks:
        lines.append(f"    {block}: {harness.block_budget_us(block):.4f},")
    lines.append("}")
    lines.append("")
    lines.append("#: The realtime budget of one block, in transform equivalents.")
    lines.append("BUDGET_TRANSFORMS: dict[int, float] = {")
    for block in blocks:
        lines.append(f"    {block}: {_budget_transforms(block, reference_transform_us):.4f},")
    lines.append("}")
    lines.append("")
    lines.append("#: The modules whose realtime cost is being repaired right now.")
    lines.append("UNDER_REPAIR: tuple[str, ...] = (")
    for code in UNDER_REPAIR:
        lines.append(f"    {code!r},")
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append("@dataclass(frozen=True)")
    lines.append("class Budget:")
    lines.append('    """The realtime budget of one module or one chain.')
    lines.append("")
    lines.append("    Attributes")
    lines.append("    ----------")
    lines.append("    code:")
    lines.append("        The module code, or the name of the chain.")
    lines.append("    kind:")
    lines.append("        ``realtime`` — the budget is the realtime budget of the")
    lines.append("        block, i.e. the requirement itself.  ``debt`` — the module")
    lines.append("        does not fit into the realtime budget today, so the budget")
    lines.append("        is the measured cost plus a margin: a ceiling that holds it")
    lines.append("        where it is and fails when it gets worse.")
    lines.append("    margin:")
    lines.append("        The margin of a debt budget, in percent.")
    lines.append("    note:")
    lines.append("        Why the budget is what it is.")
    lines.append("    measured:")
    lines.append("        The measured cost per block size, in transform equivalents.")
    lines.append("    transforms:")
    lines.append("        The ceiling per block size, in transform equivalents.")
    lines.append('    """')
    lines.append("")
    lines.append("    code: str")
    lines.append('    kind: str = "realtime"')
    lines.append("    margin: float = 0.0")
    lines.append("    note: str = \"\"")
    lines.append("    measured: dict[int, float] = field(default_factory=dict)")
    lines.append("    transforms: dict[int, float] = field(default_factory=dict)")
    lines.append("")
    lines.append("    @property")
    lines.append("    def measured_us(self) -> float:")
    lines.append('        """The measured cost of a 64 sample block, in reference us."""')
    lines.append("        return self.measured.get(64, 0.0) * REFERENCE_TRANSFORM_US")
    lines.append("")
    lines.append("    @property")
    lines.append("    def budget_us(self) -> float:")
    lines.append('        """The ceiling for a 64 sample block, in reference us."""')
    lines.append("        return self.transforms.get(64, 0.0) * REFERENCE_TRANSFORM_US")
    lines.append("")
    lines.append("    @property")
    lines.append("    def over_budget(self) -> bool:")
    lines.append('        """True when the module does not fit into the realtime budget."""')
    lines.append("        return self.kind == \"debt\"")
    lines.append("")
    lines.append("")
    lines.append("#: The product chains the guard measures, in signal order.")
    lines.append("CHAIN_MODULES: dict[str, tuple[str, ...]] = {")
    for name, chain in chains.items():
        codes = ", ".join(repr(code) for code in chain.get("codes", []))
        lines.append(f"    {name!r}: ({codes}),")
    lines.append("}")
    lines.append("")
    lines.append("#: The budget of every module.")
    lines.append("MODULE_BUDGETS: dict[str, Budget] = {")
    for module in modules:
        code = module["code"]
        timings = module.get("timings", {})
        measured: dict[int, float] = {}
        for block in blocks:
            entry = timings.get(str(block)) or timings.get(block)
            if not entry:
                continue
            value = _measured_of(entry)
            if value is None:
                continue
            measured[block] = value
        if not measured:
            continue
        worst = max(measured, key=lambda b: measured[b] / budget_transforms[b])
        kind, ceiling, worst, note = _budget_for(measured, budget_transforms)
        if code in UNDER_REPAIR:
            note += "; owner is repairing it"
        lines.append(f"    {code!r}: Budget(")
        lines.append(f'        code={code!r},')
        lines.append(f'        kind={kind!r},')
        lines.append(f'        margin={0.0 if kind == "realtime" else DEBT_MARGIN!r},')
        lines.extend(_note_lines(note))
        lines.append("        measured={")
        for block in measured:
            lines.append(f"            {block}: {measured[block]:.4f},")
        lines.append("        },")
        lines.append("        transforms={")
        for block in ceiling:
            lines.append(f"            {block}: {ceiling[block]:.4f},")
        lines.append("        },")
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    lines.append("#: The budget of the product chains.")
    lines.append("CHAIN_BUDGETS: dict[str, Budget] = {")
    for name, chain in chains.items():
        timings = chain.get("timings", {})
        measured = {}
        for block in blocks:
            entry = timings.get(str(block)) or timings.get(block)
            if not entry:
                continue
            value = _measured_of(entry)
            if value is None:
                continue
            measured[block] = value
        if not measured:
            continue
        worst = max(measured, key=lambda b: measured[b] / budget_transforms[b])
        kind, ceiling, worst, note = _budget_for(measured, budget_transforms)
        lines.append(f"    {name!r}: Budget(")
        lines.append(f'        code={name!r},')
        lines.append(f'        kind={kind!r},')
        lines.append(f'        margin={0.0 if kind == "realtime" else DEBT_MARGIN!r},')
        lines.extend(_note_lines(note))
        lines.append("        measured={")
        for block in measured:
            lines.append(f"            {block}: {measured[block]:.4f},")
        lines.append("        },")
        lines.append("        transforms={")
        for block in ceiling:
            lines.append(f"            {block}: {ceiling[block]:.4f},")
        lines.append("        },")
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    lines.append("#: The modules that allocate inside ``process()`` today.  The")
    lines.append("#: guard fails when a module that is not in this list starts to")
    lines.append("#: allocate: the number of allocating modules must not grow.")
    lines.append("ALLOCATING_MODULES: frozenset[str] = frozenset({")
    allocating = [
        code
        for code, entry in allocations.items()
        if float(entry.get("arrays_per_block", 0.0)) > 0.0
        or int(entry.get("peak_bytes", 0)) > 0
        or float(entry.get("bytes_per_block", 0.0)) > 0.0
    ]
    for code in sorted(allocating):
        lines.append(f"    {code!r},")
    lines.append("})")
    lines.append("")
    lines.append("#: The allocation baseline of every module: the number of NumPy")
    lines.append("#: arrays it creates per block, the bytes of new array data it")
    lines.append("#: creates per block and the peak Python churn of one block.")
    lines.append("ALLOC_BASELINE: dict[str, tuple[float, float, int]] = {")
    for code in sorted(allocations):
        entry = allocations[code]
        arrays = float(entry.get("arrays_per_block", 0.0))
        data = float(entry.get("bytes_per_block", 0.0))
        peak = int(entry.get("peak_bytes", 0))
        lines.append(f"    {code!r}: ({arrays:.2f}, {data:.1f}, {peak}),")
    lines.append("}")
    lines.append("")
    lines.append("#: The factor the allocation baseline may grow by before the")
    lines.append("#: guard complains.")
    lines.append(f"ALLOC_MARGIN = {ALLOC_MARGIN!r}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _committed_reference() -> float:
    """Return the reference yardstick of the committed table.

    The requirement of the gate is written in the transform equivalents of
    the *reference machine*.  It is kept from one table to the next, so
    that the requirement does not drift with the load of the machine that
    happened to run the sweep: the yardstick of a session moves (6.25 us
    on a quiet machine, 8.3 us on a busy one), the requirement may not.
    """
    if not TARGET.is_file():
        return REFERENCE_TRANSFORM_US
    for line in TARGET.read_text(encoding="utf-8").splitlines():
        if line.startswith("REFERENCE_TRANSFORM_US"):
            _, _, value = line.partition("=")
            try:
                return float(value.strip())
            except ValueError:
                break
    return REFERENCE_TRANSFORM_US


def main(argv: list[str] | None = None) -> int:
    """Write the budget table."""
    parser = argparse.ArgumentParser(
        prog="make_budgets.py",
        description="Write qa/performance/budgets.py from a sweep.",
    )
    parser.add_argument("--json", required=True, help="the sweep JSON to read")
    parser.add_argument(
        "--reference-transform-us",
        type=float,
        default=None,
        help=(
            "the transform cost of the reference machine (default: the reference "
            "of the committed table)"
        ),
    )
    parser.add_argument("--out", default=str(TARGET), help="where to write the table")
    args = parser.parse_args(argv)

    sweep = json.loads(Path(args.json).read_text(encoding="utf-8"))
    yardstick = float(
        sweep.get("yardstick_transform_us")
        or sweep.get("calibration", {}).get("fft_us", 0.0)
    )
    reference = args.reference_transform_us
    if reference is None:
        reference = _committed_reference()
    if reference <= 0:
        print("the sweep has no calibration, pass --reference-transform-us", file=sys.stderr)
        return 2

    print(f"the sweep measured this machine at {yardstick:.3f} us per transform")
    print(
        f"the reference of the budget is {reference:.3f} us -> the 64 sample "
        f"budget is {harness.block_budget_us(64) / reference:.1f} transforms"
    )
    if yardstick > 0 and abs(yardstick - reference) / reference > 0.10:
        print(
            "  note: this machine is not the reference machine "
            f"({yardstick:.3f} us against {reference:.3f} us per transform); "
            "the requirement stays on the reference machine"
        )

    source = build(sweep, reference)
    target = Path(args.out)
    target.write_text(source, encoding="utf-8")
    print(f"wrote {target} (reference transform {reference:.3f} us)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
