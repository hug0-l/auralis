"""
qa.rustle.run_rustle — run the De-Rustle measurement and print the report.

Usage
-----
    python qa/rustle/run_rustle.py
    python qa/rustle/run_rustle.py --json

The numbers this prints are the numbers of ``qa/rustle/REPORT.md`` and the
numbers the tests in ``tests/test_de_rustle.py`` assert.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import harness  # noqa: E402
import baseline  # noqa: E402


ALGORITHMS = ("Channel Independent", "Joint", "Advanced Joint")
BLOCK_SIZES = (64, 128, 256, 512)


def measure_baseline() -> dict:
    """Run the same measurement against the module as it was before the fix."""
    out: dict = {}
    for case in harness.build_cases():
        for algorithm in ALGORITHMS:
            config = dict(harness.DEFAULT_CONFIG)
            config["Separation Algorithm"] = algorithm
            module = baseline.DeRustle(harness.SR, case.channels)
            result = harness.evaluate(case, config, module=module)
            out[f"{case.name}/{algorithm}"] = result.metrics

    # the neutral setting has to be a pass through
    for case in harness.build_cases():
        config = dict(harness.DEFAULT_CONFIG)
        config["Reduction Strength"] = 0.0
        module = baseline.DeRustle(harness.SR, case.channels)
        result = harness.evaluate(case, config, module=module)
        out[f"neutral/{case.name}"] = result.metrics

    # the cost of one block
    for block in BLOCK_SIZES:
        out[f"cost/{block}"] = harness.measure_cost(
            block, module_cls=baseline.DeRustle
        )
    return out


def measure_all() -> dict:
    """Run every measurement and return the results as plain data."""
    out: dict = {}

    cases = harness.build_cases()
    out["cases"] = {
        case.name: {
            "samples": case.n_samples,
            "channels": case.channels,
            "wanted_rms_db": float(20.0 * np.log10(np.sqrt(np.mean(case.wanted ** 2)))),
            "rustle_rms_db": float(20.0 * np.log10(np.sqrt(np.mean(case.rustle ** 2)))),
        }
        for case in cases
    }

    # -- what the module does to the rustle case, per algorithm --------------
    quality: dict = {}
    for case in cases:
        for algorithm in ALGORITHMS:
            config = dict(harness.DEFAULT_CONFIG)
            config["Separation Algorithm"] = algorithm
            result = harness.evaluate(case, config)
            quality[f"{case.name}/{algorithm}"] = result.metrics
    out["quality"] = quality

    # -- the neutral setting: strength 0 has to be a pass through ------------
    neutral: dict = {}
    for case in cases:
        config = dict(harness.DEFAULT_CONFIG)
        config["Reduction Strength"] = 0.0
        result = harness.evaluate(case, config)
        neutral[case.name] = result.metrics
    out["neutral"] = neutral

    # -- latency --------------------------------------------------------------
    latency: dict = {}
    for case in cases:
        module = harness.RustleProbe(harness.SR, case.channels)
        x = case.x
        y = np.asarray(module.process(x), dtype=np.float64)
        latency[case.name] = {
            "reported": int(module.latency_samples),
            "measured": harness.measure_latency(x, y),
            "lookahead_samples": harness.LOOKAHEAD,
        }
    out["latency"] = latency

    # -- block coherence ------------------------------------------------------
    coherence: dict = {}
    for case in cases:
        for block in (512, 1024, 4096):
            info = harness.measure_block_coherence(case, block_size=block)
            coherence[f"{case.name}/{block}"] = info
    out["coherence"] = coherence

    # -- two blocks of the same signal, and determinism ----------------------
    out["split"] = {case.name: harness.measure_split(case) for case in cases}
    out["determinism"] = {
        case.name: harness.measure_determinism(case) for case in cases
    }
    out["lookahead"] = {case.name: harness.measure_lookahead(case) for case in cases}

    # -- cost -----------------------------------------------------------------
    cost: dict = {}
    for block in BLOCK_SIZES:
        cost[str(block)] = harness.measure_cost(block)
    out["cost"] = cost

    # -- edges ----------------------------------------------------------------
    edges: dict = {}
    for name, signal in harness.edge_cases():
        module = harness.RustleProbe(harness.SR, signal.shape[1])
        out_signal = np.asarray(module.process(signal.copy()), dtype=np.float64)
        rms_in = float(np.sqrt(np.mean(signal ** 2)))
        rms_out = float(np.sqrt(np.mean(out_signal ** 2))) if out_signal.size else 0.0
        edges[name] = {
            "shape": list(out_signal.shape),
            "length_ok": bool(out_signal.shape == signal.shape),
            "finite": bool(np.all(np.isfinite(out_signal))),
            "rms_in": rms_in,
            "rms_out": rms_out,
            "peak_out": float(np.max(np.abs(out_signal))) if out_signal.size else 0.0,
        }
    out["edges"] = edges

    return out


def render(data: dict) -> str:
    """Render the measurements as a text report."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("De-Rustle measurement")
    lines.append("=" * 78)

    lines.append("")
    lines.append("case")
    lines.append(f"  {'name':<20}{'smp':>10}{'ch':>4}{'wanted dBFS':>15}{'rustle dBFS':>14}")
    for name, info in data["cases"].items():
        lines.append(
            f"  {name:<20}{info['samples']:>10}{info['channels']:>4}"
            f"{info['wanted_rms_db']:>15.2f}{info['rustle_rms_db']:>14.2f}"
        )

    lines.append("")
    lines.append("what it removes and what it keeps  (negative = removed)")
    lines.append(
        f"  {'case / algorithm':<40}{'rustle dB':>12}{'wanted dB':>12}"
        f"{'SNR gain dB':>14}{'level dB':>10}"
    )
    for name, m in data["quality"].items():
        lines.append(
            f"  {name:<40}{m['rustle_removed_db']:>12.2f}{m['wanted_removed_db']:>12.2f}"
            f"{m['snr_gain_db']:>14.2f}{m['level_change_db']:>10.2f}"
        )

    lines.append("")
    lines.append("neutral setting (Reduction Strength 0, must be a pass through)")
    lines.append(f"  {'case':<20}{'rustle dB':>12}{'wanted dB':>12}{'level dB':>10}")
    for name, m in data["neutral"].items():
        lines.append(
            f"  {name:<20}{m['rustle_removed_db']:>12.2f}"
            f"{m['wanted_removed_db']:>12.2f}{m['level_change_db']:>10.2f}"
        )

    lines.append("")
    lines.append("latency")
    lines.append(
        f"  {'case':<20}{'reported':>10}{'measured':>10}{'lookahead':>11}{'lookahead ms':>14}"
    )
    for name, info in data["latency"].items():
        look = data["lookahead"].get(name, {}).get("lookahead", 0.0)
        lines.append(
            f"  {name:<20}{info['reported']:>10}{info['measured']:>10}"
            f"{look:>11.0f}{look * 1000.0 / harness.SR:>14.2f}"
        )

    lines.append("")
    lines.append("block coherence  (whole signal vs the same signal in blocks)")
    lines.append(f"  {'case / block':<28}{'max diff':>12}{'rms diff dB':>14}")
    for name, info in data["coherence"].items():
        lines.append(
            f"  {name:<28}{info['max_diff']:>12.3e}{info['rms_diff_db']:>14.2f}"
        )

    lines.append("")
    lines.append("two blocks of the same signal, and determinism")
    lines.append(
        f"  {'case':<20}{'split max diff':>16}{'split rms dB':>15}{'repeat max diff':>18}"
    )
    for name in data["split"]:
        split = data["split"][name]
        det = data["determinism"][name]
        lines.append(
            f"  {name:<20}{split['max_diff']:>16.3e}{split['rms_diff_db']:>15.2f}"
            f"{det['max_diff']:>18.3e}"
        )

    lines.append("")
    lines.append("cost per block")
    lines.append(
        f"  {'block':>7}{'median us':>12}{'budget us':>12}{'budget %':>11}{'vs 1333 us':>13}"
    )
    for block in sorted(data["cost"], key=int):
        c = data["cost"][block]
        lines.append(
            f"  {int(block):>7}{c['median_us']:>12.1f}{c['budget_us']:>12.1f}"
            f"{c['budget_fraction'] * 100:>10.1f}%{c['median_us'] / 1333.333:>12.2f}x"
        )

    lines.append("")
    lines.append("edges")
    lines.append(
        f"  {'case':<12}{'shape ok':>10}{'finite':>9}{'rms in':>10}{'rms out':>10}{'peak out':>11}"
    )
    for name, info in data["edges"].items():
        lines.append(
            f"  {name:<12}{str(info['length_ok']):>10}{str(info['finite']):>9}"
            f"{info['rms_in']:>10.4f}{info['rms_out']:>10.4f}{info['peak_out']:>11.4f}"
        )

    lines.append("")
    return "\n".join(lines)


def measure_cost_ab(rounds: int = 6, repeats: int = 25) -> dict:
    """Time both implementations against each other, round by round.

    The two implementations are measured alternately, so a machine that gets
    busy during the run hits both of them the same way.  The fastest round of
    each is reported.
    """
    out: dict = {"before": {}, "after": {}}
    for block in BLOCK_SIZES:
        best = {"before": float("inf"), "after": float("inf")}
        for _ in range(max(1, rounds)):
            for label, cls in (
                ("before", baseline.DeRustle),
                ("after", harness.RustleProbe),
            ):
                timing = harness.measure_cost(
                    block, repeats=repeats, warmup=3, rounds=1, module_cls=cls
                )
                best[label] = min(best[label], timing["median_us"])
        for label in ("before", "after"):
            budget = (float(block) / float(harness.SR)) * 1_000_000.0
            out[label][str(block)] = {
                "block": float(block),
                "median_us": best[label],
                "budget_us": budget,
                "budget_fraction": best[label] / budget,
            }
    return out


def render_comparison(after: dict, before: dict, cost_ab: dict | None = None) -> str:
    """Render the before/after table of the fix."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("De-Rustle: before the fix and after the fix")
    lines.append("=" * 78)
    lines.append("")
    lines.append("what it removes and what it keeps  (negative = removed)")
    lines.append(
        f"  {'case / algorithm':<40}{'rustle before':>14}{'rustle after':>13}"
        f"{'wanted before':>15}{'wanted after':>14}"
    )
    for name in after["quality"]:
        a = after["quality"][name]
        b = before.get(name, {})
        lines.append(
            f"  {name:<40}{b.get('rustle_removed_db', float('nan')):>14.2f}"
            f"{a['rustle_removed_db']:>13.2f}"
            f"{b.get('wanted_removed_db', float('nan')):>15.2f}"
            f"{a['wanted_removed_db']:>14.2f}"
        )

    lines.append("")
    lines.append("neutral setting (Reduction Strength 0, must be a pass through)")
    lines.append(
        f"  {'case':<20}{'rustle before':>14}{'rustle after':>13}"
        f"{'wanted before':>15}{'wanted after':>14}{'level before':>14}{'level after':>13}"
    )
    for name in after["neutral"]:
        a = after["neutral"][name]
        b = before.get(f"neutral/{name}", {})
        lines.append(
            f"  {name:<20}{b.get('rustle_removed_db', float('nan')):>14.2f}"
            f"{a['rustle_removed_db']:>13.2f}"
            f"{b.get('wanted_removed_db', float('nan')):>15.2f}"
            f"{a['wanted_removed_db']:>14.2f}"
            f"{b.get('level_change_db', float('nan')):>14.2f}"
            f"{a['level_change_db']:>13.2f}"
        )

    lines.append("")
    lines.append("cost per block")
    lines.append(
        f"  {'block':>7}{'before us':>12}{'after us':>11}{'budget us':>12}"
        f"{'before %':>11}{'after %':>10}"
    )
    if cost_ab is not None:
        for block in BLOCK_SIZES:
            b = cost_ab["before"][str(block)]
            a = cost_ab["after"][str(block)]
            lines.append(
                f"  {block:>7}{b['median_us']:>12.1f}{a['median_us']:>11.1f}"
                f"{a['budget_us']:>12.1f}"
                f"{b['budget_fraction'] * 100:>10.1f}%{a['budget_fraction'] * 100:>9.1f}%"
            )
    else:
        for block in sorted(after["cost"], key=int):
            a = after["cost"][block]
            b = before.get(f"cost/{block}", {})
            lines.append(
                f"  {int(block):>7}{b.get('median_us', float('nan')):>12.1f}"
                f"{a['median_us']:>11.1f}{a['budget_us']:>12.1f}"
                f"{b.get('budget_fraction', float('nan')) * 100:>10.1f}%"
                f"{a['budget_fraction'] * 100:>9.1f}%"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Run the measurement and print the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the raw data as JSON")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="also measure the module as it was before the fix and print both",
    )
    args = parser.parse_args()

    data = measure_all()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print(render(data))
    if args.baseline:
        before = measure_baseline()
        print(render_comparison(data, before, measure_cost_ab()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
