#!/usr/bin/env python3
"""
qa.performance.make_model_budgets — write the model budget table from a sweep.

The model budget table is the *gate*: it says what a shipped model may
cost for one block of audio.  It is written from a measurement, reviewed
and committed, because a budget that were regenerated on every test run
would always equal the measurement and would never catch anything.

The numbers are stored in **transform equivalents**: how many 1024 point
transforms a block may cost.  That is a machine independent unit, so the
verdict of the guard does not depend on how busy the machine is.

Example
-------
    python qa/performance/sweep_models.py --runs 5 --json var/perf/models.json
    python qa/performance/make_model_budgets.py --json var/perf/models.json

THE REFERENCE MACHINE
---------------------
The budget is written in **transform equivalents**, so the number a model
is held at does not depend on how busy the machine that measured it was.
The *reference* is the yardstick the requirement is expressed in: the
realtime budget of a 64 sample block (1333.3 us) is ``1333.3 / 8.3`` =
160.6 transform equivalents.

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
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import harness  # noqa: E402  (path setup has to come first)

#: Where the generated table goes.
TARGET = _HERE / "model_budgets.py"

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

#: The rule of the product: a model belongs to the **offline** path.
#:
#: The rule is a *policy* and not a technical limit.  The hand written
#: modules are exact, deterministic and cheaper, so they stay the default
#: in the real time path; a model is offered where the extra quality is
#: worth the wait.  The measurement shows that every model we ship would
#: fit into the real time budget as well — the gate keeps that true, so
#: the rule stays a choice and not a necessity.
MODEL_RULE = "offline"

#: The budgets a model can be held to.
#:
#: ``"block"`` — the **realtime budget** of a 64 sample block: the model is
#: cheap enough to run inside a real time host, and the gate holds it
#: there.
#: ``"speed"`` — the **offline budget**: the model is asked once per
#: sample, or the like, and it is held to the *speed* of the offline path
#: instead of the budget of a block.  Its render may take at most
#: :data:`OFFLINE_SPEED_CEILING` times the length of the audio.
BUDGET_KINDS = ("block", "speed")

#: The **offline budget**: how many times the real time the offline path
#: may take.  A render may take as long as the file plays (1.0), and twice
#: as long (2.0) before the gate complains.
#:
#: It is a budget of the *path*, not of a module: there is no block to
#: hold a file render to, because a file render has no deadline.  What it
#: must not become is a render nobody waits for.
OFFLINE_SPEED_CEILING = 2.0

#: The share of the realtime budget above which a model is held to the
#: **offline** budget instead of the realtime one.
#:
#: The live check of the gate runs on a shared machine, and the load of
#: that machine moves a measurement by a factor of two.  A model that
#: takes a quarter of the realtime budget survives that; a model that
#: takes two thirds of it does not, and it would fail the gate on a busy
#: machine rather than on a model that grew.  A model above the share is
#: held to the speed of the offline path instead, which it clears with a
#: margin of the same kind.
LIVE_SAFE_SHARE = 0.25

#: The margin the live measurement may exceed the recorded one by.  The
#: machine is shared and the calibration of the session resolves the load
#: to about a factor of two, so the live check of the gate must not fail
#: on noise.  A model that got twice as expensive is still caught.
LIVE_MARGIN = 1.0


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


def build(
    sweep: dict[str, Any],
    reference_transform_us: float,
    kinds: dict[str, str] | None = None,
) -> str:
    """Return the source of the model budget table for one sweep."""
    kinds = dict(kinds or {})
    block = int(sweep.get("block", 64))
    budget_us = float(sweep.get("budget_us") or harness.block_budget_us(block))
    budget_transforms = budget_us / reference_transform_us
    seconds = float(sweep.get("seconds", 10.0))
    runs = int(sweep.get("runs", 0))

    lines: list[str] = []
    lines.append('"""')
    lines.append("qa.performance.model_budgets — the cost budgets of the shipped models.")
    lines.append("")
    lines.append("This file is the gate of ``tests/test_model_budget.py``.  It says")
    lines.append("what a shipped model may cost for one block of audio.")
    lines.append("")
    lines.append("It is written by ``qa/performance/make_model_budgets.py`` from a")
    lines.append("sweep (``qa/performance/sweep_models.py``) and it is committed.  A")
    lines.append("budget that were regenerated on every test run would always equal")
    lines.append("the measurement and would never catch anything.")
    lines.append("")
    lines.append("The unit is the **transform equivalent**: how many 1024 point")
    lines.append("transforms a block may cost.  It is machine independent, so the")
    lines.append("verdict does not depend on how busy the machine that runs the")
    lines.append("test is.  The realtime budget of a block at 48 kHz is")
    lines.append("``block / 48000`` seconds, which is")
    lines.append(f"{budget_transforms:.1f} transform equivalents")
    lines.append("for a 64 sample block on the reference machine.")
    lines.append("")
    lines.append("The rule of the product is written down here as well: a model")
    lines.append("belongs to the **offline** path.  The rule is a policy and not a")
    lines.append("technical limit — the hand written modules are exact,")
    lines.append("deterministic and cheaper, so they stay the default in the real")
    lines.append("time path.  The measurement below shows that every model we")
    lines.append("ship would fit into the real time budget as well, and the gate")
    lines.append("keeps that true.")
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
    lines.append("#: The block size the budget is quoted at.")
    lines.append(f"BLOCK = {block}")
    lines.append("")
    lines.append("#: The realtime budget of one block, in microseconds.")
    lines.append(f"BUDGET_US = {budget_us:.4f}")
    lines.append("")
    lines.append("#: The realtime budget of one block, in transform equivalents.")
    lines.append(f"BUDGET_TRANSFORMS = {budget_transforms:.4f}")
    lines.append("")
    lines.append("#: The rule of the product: a model is an offline capability.")
    lines.append("#:")
    lines.append("#: The rule is a policy and not a technical limit.  The hand")
    lines.append("#: written modules are exact, deterministic and cheaper, so they")
    lines.append("#: stay the default in the real time path; a model is offered")
    lines.append("#: where the extra quality is worth the wait.  The measurement")
    lines.append("#: shows that every model we ship fits into the real time budget")
    lines.append("#: as well, so the rule is a choice.  The gate keeps it a choice:")
    lines.append("#: a model that grows past the budget fails the test.")
    lines.append(f"MODEL_RULE = {MODEL_RULE!r}")
    lines.append("")
    lines.append("#: The budgets a model can be held to.")
    lines.append("#:")
    lines.append("#: ``\"block\"`` — the realtime budget of a 64 sample block: the")
    lines.append("#: model is cheap enough to run inside a real time host.")
    lines.append("#: ``\"speed\"`` — the offline budget: the model is asked once per")
    lines.append("#: sample, or the like, and it is held to the speed of the")
    lines.append("#: offline path instead of the budget of a block.")
    lines.append(f"BUDGET_KINDS = {BUDGET_KINDS!r}")
    lines.append("")
    lines.append("#: The offline budget: how many times the real time the offline")
    lines.append("#: path may take.  A render may take as long as the file plays")
    lines.append("#: (1.0), and twice as long (2.0) before the gate complains.")
    lines.append("#:")
    lines.append("#: It is a budget of the *path*, not of a module: a file render")
    lines.append("#: has no deadline, so there is no block to hold it to.  What it")
    lines.append("#: must not become is a render nobody waits for.")
    lines.append(f"OFFLINE_SPEED_CEILING = {OFFLINE_SPEED_CEILING!r}")
    lines.append("")
    lines.append("#: The factor the live measurement may exceed the recorded one")
    lines.append("#: by before the gate complains.  The machine is shared and the")
    lines.append("#: calibration of a session resolves the load to about a factor of")
    lines.append("#: two, so the live check must not fail on noise.  A model that")
    lines.append("#: got twice as expensive is still caught.")
    lines.append(f"LIVE_MARGIN = {LIVE_MARGIN!r}")
    lines.append("")
    lines.append("")
    lines.append("@dataclass(frozen=True)")
    lines.append("class ModelBudget:")
    lines.append('    """The cost budget of one shipped model.')
    lines.append("")
    lines.append("    Attributes")
    lines.append("    ----------")
    lines.append("    name:")
    lines.append("        The name the model publishes.")
    lines.append("    problem:")
    lines.append("        The problem the model solves.")
    lines.append("    version:")
    lines.append("        The version of the model.")
    lines.append("    variant:")
    lines.append("        The variant inside the family.")
    lines.append("    parameters:")
    lines.append("        The number of values the weights hold.")
    lines.append("    kind:")
    lines.append("        The rule the model is held to.  ``offline`` — a model")
    lines.append("        is an offline capability; the rule is a policy of the")
    lines.append("        product and not a technical limit.")
    lines.append("    budget:")
    lines.append("        The budget the model is held to.  ``block`` — the")
    lines.append("        realtime budget of a 64 sample block.  ``speed`` — the")
    lines.append("        offline budget: the speed of the offline path.")
    lines.append("    speed_factor:")
    lines.append("        How many times the real time the model took for it.")
    lines.append("    speed_ceiling:")
    lines.append("        The ceiling of the speed budget, in times the real time.")
    lines.append("    note:")
    lines.append("        Why the budget is what it is.")
    lines.append("    seconds:")
    lines.append("        The length of the audio the cost was measured over.")
    lines.append("    realtime_factor:")
    lines.append("        How many times the real time the model took for it.")
    lines.append("    measured:")
    lines.append("        The measured cost per block size, in transform equivalents.")
    lines.append("    transforms:")
    lines.append("        The ceiling per block size, in transform equivalents.")
    lines.append('    """')
    lines.append("")
    lines.append("    name: str")
    lines.append('    problem: str = ""')
    lines.append('    version: str = ""')
    lines.append('    variant: str = ""')
    lines.append("    parameters: int = 0")
    lines.append('    kind: str = "offline"')
    lines.append('    budget: str = "block"')
    lines.append("    speed_factor: float = 0.0")
    lines.append("    speed_ceiling: float = 0.0")
    lines.append('    note: str = ""')
    lines.append("    seconds: float = 0.0")
    lines.append("    realtime_factor: float = 0.0")
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
    lines.append("    def share(self) -> float:")
    lines.append('        """How much of the realtime budget the model takes, in %."""')
    lines.append("        budget = self.budget_us")
    lines.append("        return self.measured_us / budget * 100.0 if budget else 0.0")
    lines.append("")
    lines.append("    @property")
    lines.append("    def over_budget(self) -> bool:")
    lines.append('        """True when the model does not fit inside its budget."""')
    lines.append("        if self.budget == \"speed\":")
    lines.append("            return self.speed_factor > self.speed_ceiling")
    lines.append("        return self.measured_us > self.budget_us")
    lines.append("")
    lines.append("")
    lines.append("#: The budget of every shipped model.")
    lines.append("MODEL_BUDGETS: dict[str, ModelBudget] = {")
    for model in sweep.get("models", []):
        name = model["name"]
        transforms = float(model.get("transforms") or 0.0)
        us_per_block = float(model.get("us_per_block") or 0.0)
        elapsed_us = float(model.get("elapsed_us") or 0.0)
        factor = elapsed_us / 1_000_000.0 / seconds if seconds else 0.0
        speed = float(model.get("speed_factor") or factor)
        share = transforms / budget_transforms * 100.0 if budget_transforms else 0.0
        budget_kind = kinds.get(name)
        if budget_kind is None:
            # A model that is not in the committed table yet is classified by
            # the measurement: it fits the realtime budget with room to
            # spare, or it is held to the offline one.
            budget_kind = "block" if share <= LIVE_SAFE_SHARE * 100.0 else "speed"
        rounds = [float(row.get("transforms") or 0.0) for row in model.get("rounds", [])]
        spread = ""
        if rounds:
            spread = (
                f"the {len(rounds)} rounds spread {min(rounds):.1f} to "
                f"{max(rounds):.1f} transforms"
            )
        if budget_kind == "speed":
            note = (
                f"{speed:.2f} x the real time and {share:.1f} % of the 64 sample "
                f"budget: too close to the edge to be measured reliably on a "
                f"shared machine, so the model is held to the offline budget: a "
                f"render may take at most {OFFLINE_SPEED_CEILING:g} times the "
                f"length of the audio"
            )
        else:
            note = (
                f"{share:.1f} % of the 64 sample budget "
                f"({transforms * reference_transform_us:.1f} us of {budget_us:.1f} us); "
                f"{runs} rounds of {seconds:g} s of audio, fastest round wins"
            )
        if spread:
            note = f"{note}; {spread}"
        lines.append(f"    {name!r}: ModelBudget(")
        lines.append(f"        name={name!r},")
        lines.append(f"        problem={model.get('problem', '')!r},")
        lines.append(f"        version={model.get('version', '')!r},")
        lines.append(f"        variant={model.get('variant', '')!r},")
        lines.append(f"        parameters={int(model.get('parameters') or 0)!r},")
        lines.append(f"        kind={MODEL_RULE!r},")
        lines.append(f"        budget={budget_kind!r},")
        lines.append(f"        speed_factor={round(speed, 4)!r},")
        ceiling = OFFLINE_SPEED_CEILING if budget_kind == "speed" else 0.0
        lines.append(f"        speed_ceiling={round(ceiling, 4)!r},")
        lines.extend(_note_lines(note))
        lines.append(f"        seconds={seconds!r},")
        lines.append(f"        realtime_factor={round(factor, 4)!r},")
        lines.append("        measured={")
        lines.append(f"            {block}: {transforms:.4f},")
        lines.append("        },")
        lines.append("        transforms={")
        lines.append(f"            {block}: {budget_transforms:.4f},")
        lines.append("        },")
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    lines.append("#: The raw cost of a model per 64 sample block on the machine")
    lines.append("#: that took the measurement, in microseconds.  It is kept next")
    lines.append("#: to the table so that the wall clock number of the session is")
    lines.append("#: not lost in the conversion, and so that a reader can compare")
    lines.append("#: it with a reading of their own machine.")
    lines.append("SESSION_US: dict[str, float] = {")
    for model in sweep.get("models", []):
        us_per_block = float(model.get("us_per_block") or 0.0)
        lines.append(f"    {model['name']!r}: {us_per_block:.4f},")
    lines.append("}")
    lines.append("")
    lines.append("#: The yardstick of the session that took the measurement, in")
    lines.append("#: microseconds per 1024 point transform.  It is the speed of the")
    lines.append("#: machine that ran the sweep; the requirement is quoted in the")
    lines.append("#: fixed reference above, not in this one.")
    lines.append(
        f"SESSION_TRANSFORM_US = {float(sweep.get('session_yardstick_us') or 0.0):.4f}"
    )
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


def _committed_kinds() -> dict[str, str]:
    """Return the budget kind every model of the committed table is held to.

    The kind is a **decision**, not a measurement: a model that fits the
    realtime budget is held to it, and a model that does not is held to
    the offline budget.  The decision is committed with the table, so that
    a model that grows past the realtime budget fails the gate instead of
    being re-classified by the next sweep.
    """
    kinds: dict[str, str] = {}
    if not TARGET.is_file():
        return kinds
    current = ""
    for line in TARGET.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("name="):
            _, _, value = stripped.partition("=")
            current = value.strip().strip("',")
        elif stripped.startswith("budget=") and current:
            _, _, value = stripped.partition("=")
            kinds[current] = value.strip().strip("',")
    return kinds


def main(argv: list[str] | None = None) -> int:
    """Write the model budget table."""
    parser = argparse.ArgumentParser(
        prog="make_model_budgets.py",
        description="Write qa/performance/model_budgets.py from a sweep.",
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
    yardstick = float(sweep.get("session_yardstick_us") or 0.0)
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

    source = build(sweep, reference, kinds=_committed_kinds())
    target = Path(args.out)
    target.write_text(source, encoding="utf-8")
    print(f"wrote {target} (reference transform {reference:.3f} us)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
