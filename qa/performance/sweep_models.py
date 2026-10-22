#!/usr/bin/env python3
"""
qa.performance.sweep_models — the measurement sweep the model budgets come from.

The sweep answers one question: **what does a shipped model cost for one
block of audio?**

A model runs on the **offline** path: it is given a whole signal and it
produces a whole signal.  The cost of that path is therefore measured over
a whole signal and then amortised over the blocks of that signal, so that
the number can be compared with the realtime budget of a block — the budget
the hand written modules are held to.

The unit is the **transform equivalent**, the same unit the module budgets
are written in: how many 1024 point transforms a block may cost.  Every
measurement is timed next to a batch of transforms of the same machine, so
the number does not depend on how busy the machine was while it was taken.
The reported statistic is the fastest of N rounds: the round that was least
disturbed by other work.  The rounds of the whole library are interleaved,
so every model is sampled over the whole sweep and a burst of load does
not fall on one model only.

The yardstick of the *requirement* is the fixed reference of the project
(8.300 us per transform), not the yardstick of this session.  The session
yardstick moves with the load of the machine; the requirement may not.

Examples
--------
    # the sweep, five rounds per model
    python qa/performance/sweep_models.py --runs 5

    # write the results as JSON
    python qa/performance/sweep_models.py --runs 5 --json var/perf/models.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harness_loader import load_harness  # noqa: E402  (path setup has to come first)

harness = load_harness(_HERE)

from aurcore import models  # noqa: E402

#: The block size the realtime budget is quoted at.
BLOCK = 64

#: The length of the audio one round runs a model over.  Ten seconds is
#: long enough for the setup of the path to be amortised over thousands of
#: blocks and short enough to be repeated.
SECONDS = 10.0

#: The seed of the test signal.  The measurement has to be repeatable.
SEED = 20261021

#: How many rounds every model is measured for.  The fastest round wins.
ROUNDS = 5

#: How many transforms are timed on each side of a run.  The fastest of
#: them is the observation that was least disturbed by the other work on
#: the machine.
CALIB_REPEATS = 30

#: The cost of one 1024 point transform on the **reference machine**, in
#: microseconds.  It is the yardstick the requirement is written in — the
#: same constant the module budget table is written in.  The yardstick of
#: the *session* moves with the load of the machine and is measured here
#: as well; the requirement is quoted in the fixed one.
REFERENCE_TRANSFORM_US = 8.300


def _calibration(repeats: int) -> list[float]:
    """Return the cost of ``repeats`` 1024 point transforms, in microseconds.

    The times are returned one by one and not reduced here: the caller
    takes the fastest of them, which is the observation that was least
    disturbed by the other work on the machine.
    """
    data = np.zeros(1024, dtype=np.float64)
    np.fft.rfft(data)  # warm the plan up
    times: list[float] = []
    for _ in range(max(1, int(repeats))):
        start = time.perf_counter_ns()
        np.fft.rfft(data)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return times


def make_signal(rate: int, seconds: float) -> np.ndarray:
    """Return the deterministic mono signal one round runs a model over."""
    count = int(round(rate * seconds))
    signal = harness.make_test_signal(count, rate, channels=1, seed=SEED)
    return np.ascontiguousarray(signal[:, 0])


def time_run(model, signal: np.ndarray, rate: int, calib_repeats: int) -> dict[str, Any]:
    """Run the offline path once, timed next to the machine calibration.

    The calibration is taken on both sides of the run, so that a change of
    the load between the two measurements cannot make the model look cheap.
    The cost of the run is divided by the number of blocks it carries, so
    the result is the cost of one block of audio.
    """
    before = _calibration(calib_repeats)
    start = time.perf_counter_ns()
    out = models.run_offline(model, signal, sample_rate=rate)
    elapsed_us = (time.perf_counter_ns() - start) / 1000.0
    after = _calibration(calib_repeats)

    fft_us = (min(before) + min(after)) / 2.0
    blocks = signal.shape[0] / float(BLOCK)
    us_per_block = elapsed_us / blocks
    transforms = us_per_block / fft_us if fft_us > 0.0 else 0.0
    audio_seconds = signal.shape[0] / float(rate)
    speed_factor = elapsed_us / 1_000_000.0 / audio_seconds if audio_seconds else 0.0
    return {
        "elapsed_us": elapsed_us,
        "blocks": blocks,
        "us_per_block": us_per_block,
        "transform_us": fft_us,
        "transforms": transforms,
        "speed_factor": speed_factor,
        "finite": bool(np.all(np.isfinite(out))),
    }


def _measure(models_to_measure, seconds: float, calib_repeats: int) -> dict[str, dict]:
    """Measure every model, round by round.

    The rounds are interleaved: every round measures every model once, so
    the samples of a model are spread over the whole sweep.  The load of a
    shared machine comes and goes in bursts, and a model that is measured
    inside one burst only never sees a quiet moment.
    """
    rounds: dict[str, list[dict[str, Any]]] = {name: [] for name in models_to_measure}
    signals: dict[str, np.ndarray] = {}
    for name, (model, rate) in models_to_measure.items():
        signals[name] = make_signal(rate, seconds)
    for _ in range(max(1, int(ROUNDS))):
        for name, (model, rate) in models_to_measure.items():
            rounds[name].append(
                time_run(model, signals[name], rate, calib_repeats)
            )
    measured: dict[str, dict[str, Any]] = {}
    for name, rows in rounds.items():
        best = min(rows, key=lambda row: row["transforms"])
        measured[name] = {
            "rounds": rows,
            "transforms": best["transforms"],
            "us_per_block": best["us_per_block"],
            "transform_us": best["transform_us"],
            "elapsed_us": best["elapsed_us"],
            "finite": all(row["finite"] for row in rows),
        }
    return measured


def main(argv: list[str] | None = None) -> int:
    """Run the sweep and print the table."""
    global ROUNDS

    parser = argparse.ArgumentParser(
        prog="sweep_models.py",
        description="Measure the cost of every shipped model.",
    )
    parser.add_argument("--runs", type=int, default=ROUNDS, help="rounds per model")
    parser.add_argument("--seconds", type=float, default=SECONDS, help="audio per round")
    parser.add_argument(
        "--calib-repeats",
        type=int,
        default=CALIB_REPEATS,
        help=f"transforms timed next to every round (default {CALIB_REPEATS})",
    )
    parser.add_argument("--json", dest="json_path", help="write the results as JSON")
    args = parser.parse_args(argv)

    ROUNDS = max(1, int(args.runs))
    seconds = max(0.5, float(args.seconds))

    library = models.open_library()
    entries = [entry for entry in library.list() if entry.readable]
    if not entries:
        print("the library holds no readable model", file=sys.stderr)
        return 2

    budget_us = harness.block_budget_us(BLOCK)
    budget_transforms = budget_us / REFERENCE_TRANSFORM_US

    session_samples = harness.transform_us_samples(runs=12, repeats=10)
    print(
        f"session yardstick: {min(session_samples):.2f} us per transform "
        f"(fastest of {len(session_samples)} samples, "
        f"slowest {max(session_samples):.2f} us)"
    )
    print(
        f"the requirement is written in the fixed reference: "
        f"{REFERENCE_TRANSFORM_US:.3f} us per transform -> {budget_transforms:.1f} "
        f"transforms ({budget_us:.1f} us) for a {BLOCK} sample block"
    )

    to_measure: dict[str, tuple[Any, int]] = {}
    for entry in entries:
        try:
            model = library.load(entry.name)
        except Exception as exc:  # noqa: BLE001 - a broken model is reported
            print(f"  {entry.name}: not measurable: {type(exc).__name__}: {exc}")
            continue
        to_measure[entry.name] = (model, int(model.manifest.input.sample_rate))

    started = time.perf_counter()
    measured = _measure(to_measure, seconds, max(1, int(args.calib_repeats)))
    for entry in entries:
        row = measured.get(entry.name)
        if row is None:
            continue
        share = row["transforms"] / budget_transforms * 100.0
        spread = [round(r["transforms"], 2) for r in row["rounds"]]
        print(
            f"{entry.name:<22} {row['elapsed_us'] / 1_000_000.0 / seconds:6.3f} x real time "
            f"{row['us_per_block']:8.1f} us per {BLOCK} sample block "
            f"= {row['transforms']:6.2f} transforms "
            f"= {row['transforms'] * REFERENCE_TRANSFORM_US:7.1f} us on the "
            f"reference machine ({share:5.1f} % of the budget)"
        )
        print(f"{'':<22} the rounds: {spread} transforms")

    after_samples = harness.transform_us_samples(runs=12, repeats=10)
    session_samples.extend(after_samples)
    print(
        f"sweep: {len(entries)} models x {ROUNDS} rounds x {seconds:g} s of audio "
        f"in {time.perf_counter() - started:.1f} s"
    )
    print(
        f"session yardstick after the sweep: {min(session_samples):.2f} us per transform"
    )
    print(f"machine load: {harness.machine_load_percent(0.3):.1f} %")

    if args.json_path:
        payload = {
            "tool": "auralis.performance.sweep_models",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": harness.environment_info(),
            "runs": ROUNDS,
            "seconds": seconds,
            "block": BLOCK,
            "reference_transform_us": REFERENCE_TRANSFORM_US,
            "session_transform_us": [round(float(v), 3) for v in session_samples],
            "session_yardstick_us": round(min(session_samples), 3),
            "budget_us": budget_us,
            "budget_transforms": budget_transforms,
            "models": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "problem": entry.problem,
                    "variant": entry.variant,
                    "task": entry.task,
                    "trained_on": entry.trained_on,
                    "parameters": int(
                        to_measure[entry.name][0].manifest.network.parameters
                    ),
                    "transforms": measured[entry.name]["transforms"],
                    "us_per_block": measured[entry.name]["us_per_block"],
                    "transform_us": measured[entry.name]["transform_us"],
                    "elapsed_us": measured[entry.name]["elapsed_us"],
                    "finite": measured[entry.name]["finite"],
                    "rounds": measured[entry.name]["rounds"],
                }
                for entry in entries
                if entry.name in measured
            ],
        }
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
