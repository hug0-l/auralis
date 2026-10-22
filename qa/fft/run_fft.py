"""
qa.fft.run_fft — run the FFT measurements and print the tables.

Usage::

    python qa/fft/run_fft.py            # print every table
    python qa/fft/run_fft.py --json     # write the raw results as JSON
    python qa/fft/run_fft.py --section round-trip

The raw results land in ``qa/fft/results.json``; the written report is
``qa/fft/REPORT.md``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE.parents[1]) not in sys.path:
    sys.path.insert(0, str(HERE.parents[1]))

import harness as H  # noqa: E402

__all__ = ["main", "run_all", "render_tables"]

RESULT_PATH = HERE / "results.json"
TABLES_PATH = HERE / "tables.md"


def run_all() -> dict:
    """Run every measurement and return the raw results."""
    return {
        "round_trip": [r.to_dict() for r in H.measure_round_trip()],
        "round_trip_default_length": [
            r.to_dict() for r in H.measure_default_length_round_trip()
        ],
        "large_round_trip": [r.to_dict() for r in H.measure_round_trip(H.LARGE_SIZES)],
        "dft": H.measure_dft_error(),
        "radix2_axis": H.measure_radix2_axis(),
        "stft_round_trip": H.measure_stft_round_trip(),
        "short_and_ragged": H.measure_short_and_ragged_input(),
        "timing": [t.to_dict() for t in H.measure_timing()],
        "edge_phase": H.measure_edge_phase(),
        "padding": H.measure_padding_assumption(),
        "edge_cases": [e.to_dict() for e in H.measure_edge_cases()],
        "structured_round_trip": H.measure_structured_round_trip(),
        "denormal_cost": H.measure_denormal_cost(),
        "round_trip_helper": [
            r.to_dict() for r in H.measure_round_trip(H.FFT_SIZES, backend="numpy")
        ],
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _table(header: list[str], rows: list[list[str]]) -> str:
    """Render a Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _fmt(value: float, digits: int = 2) -> str:
    """Format a float, keeping the infinities readable."""
    if value == math.inf:
        return "inf"
    if value == -math.inf:
        return "-inf"
    if value != value:  # NaN
        return "nan"
    return f"{value:.{digits}f}"


def render_tables(data: dict) -> str:
    """Render every measurement as a Markdown table."""
    out: list[str] = []

    # --- 1. round trip, every size -----------------------------------------
    out.append("## 1. Transform round trip, every size (1 .. 1024)\n")
    rows = []
    for record in data["round_trip"]:
        rows.append(
            [
                str(record["n"]),
                f"{record['error']:.3e}",
                f"{record['error_dbfs']:.1f}",
                str(record["out_length"]),
                str(record["length_lost"]) if record["length_lost"] else "0",
            ]
        )
    out.append(
        _table(["n", "max abs error", "error dBFS", "samples back", "samples lost"], rows)
    )
    worst = max(data["round_trip"], key=lambda r: r["error"])
    out.append(
        f"\nWorst explicit-length round trip: n={worst['n']} "
        f"error {worst['error']:.3e} ({worst['error_dbfs']:.1f} dBFS).\n"
    )

    # --- 2. default length --------------------------------------------------
    out.append("\n## 2. Round trip through the *default* inverse length\n")
    rows = []
    for record in data["round_trip_default_length"]:
        if record["n"] % 64 and record["n"] not in (1, 2, 3, 5, 7, 9, 11, 1023, 1025):
            continue
        if record["raised"]:
            detail = f"raised {record['raised']}"
        elif record["length_lost"]:
            detail = f"TRUNCATED, {record['length_lost']} sample(s) lost"
        else:
            detail = f"{record['error']:.3e}"
        level = record.get("level_error_db")
        rows.append(
            [
                str(record["n"]),
                str(record["in_length"]),
                str(record["out_length"]) if not record["raised"] else "-",
                str(record["length_lost"]) if not record["raised"] else "-",
                detail,
                "-" if level is None else f"{level:+.4f} dB",
            ]
        )
    out.append(_table(["n", "in", "out", "lost", "result", "level error"], rows))
    lost = [r for r in data["round_trip_default_length"] if r["length_lost"]]
    out.append(
        f"\nSizes whose default inverse length is short: "
        f"{len(lost)} of {len(data['round_trip_default_length'])} "
        f"(all odd sizes: {sorted(r['n'] for r in lost)[:12]} ...).\n"
    )

    # --- 2b. structured signals --------------------------------------------
    out.append("\n## 2b. The default length on structured signals\n")
    rows = []
    for row in data["structured_round_trip"]:
        if row["raised"]:
            detail = f"raised {row['raised']}"
        elif row["out_length"] != row["n"]:
            detail = f"TRUNCATED, {row['n'] - row['out_length']} sample(s) lost"
        else:
            detail = f"{row['error']:.3e}"
        rows.append(
            [
                str(row["n"]),
                row["case"],
                "yes" if row["top_is_real"] else "no",
                str(row["out_length"]) if row["out_length"] >= 0 else "-",
                detail,
            ]
        )
    out.append(
        _table(["n", "signal", "top bin real", "out", "result"], rows)
    )

    # --- 3. large sizes -----------------------------------------------------
    out.append("\n## 3. Round trip, the large sizes\n")
    rows = [
        [
            str(r["n"]),
            f"{r['error']:.3e}",
            f"{r['error_dbfs']:.1f}",
            str(r["out_length"]),
        ]
        for r in data["large_round_trip"]
    ]
    out.append(_table(["n", "max abs error", "error dBFS", "samples back"], rows))

    # --- 4. direct DFT ------------------------------------------------------
    out.append("\n## 4. Against a textbook DFT\n")
    rows = []
    for row in data["dft"]:
        radix = "-" if row["radix2_error"] is None else f"{row['radix2_error']:.3e}"
        rows.append(
            [
                str(row["n"]),
                f"{row['error']:.3e}",
                f"{row['relative']:.3e}",
                radix,
                f"{row['max_bin']:.1f}",
            ]
        )
    out.append(
        _table(
            ["n", "abs error", "relative error", "radix-2 abs error", "peak bin"],
            rows,
        )
    )

    # --- 5. radix2 axis -----------------------------------------------------
    out.append("\n## 5. The radix-2 backend and the transform axis\n")
    rows = []
    for row in data["radix2_axis"]:
        if row["raised"]:
            detail = row["raised"]
        elif not row["shape_ok"]:
            detail = f"shape {row['radix2_shape']} != {row['reference_shape']}"
        else:
            detail = f"{row['error']:.3e}"
        rows.append(
            [
                str(row["n"]),
                str(row["axis"]),
                "ok" if row.get("shape_ok") and not row["raised"] else "DEFECT",
                str(row.get("reference_shape")),
                str(row.get("radix2_shape")),
                detail,
            ]
        )
    out.append(
        _table(["n", "axis", "result", "numpy shape", "radix-2 shape", "detail"], rows)
    )

    # --- 6. STFT round trip -------------------------------------------------
    out.append("\n## 6. Short-time transform round trip, every frame size\n")
    by_size: dict[int, list[dict]] = {}
    for row in data["stft_round_trip"]:
        by_size.setdefault(row["n_fft"], []).append(row)
    rows = []
    for n_fft in sorted(by_size):
        cells = [str(n_fft)]
        for overlap in (2, 4):
            match = [r for r in by_size[n_fft] if r["overlap"] == overlap]
            if not match:
                cells.append("-")
                continue
            row = match[0]
            if row["raised"]:
                cells.append(f"raised {row['raised'][:40]}")
            else:
                cells.append(f"{row['error']:.2e} ({row['error_dbfs']:.1f} dBFS)")
        rows.append(cells)
    out.append(_table(["n_fft", "overlap 2 (50 %)", "overlap 4 (75 %)"], rows))
    worst_stft = max(
        (r for r in data["stft_round_trip"] if not r["raised"]),
        key=lambda r: r["error"],
    )
    out.append(
        f"\nWorst short-time round trip: n_fft={worst_stft['n_fft']} "
        f"overlap {worst_stft['overlap']} error {worst_stft['error']:.3e} "
        f"({worst_stft['error_dbfs']:.1f} dBFS).\n"
    )

    # --- 7. short and ragged input -----------------------------------------
    out.append("\n## 7. Input shorter than one frame, and a ragged input\n")
    rows = []
    for row in data["short_and_ragged"]:
        if row["raised"]:
            rows.append(
                [str(row["n_fft"]), str(row["overlap"]), str(row["length"]),
                 str(row["center"]), "raised", row["raised"][:60]]
            )
            continue
        rows.append(
            [
                str(row["n_fft"]),
                str(row["overlap"]),
                str(row["length"]),
                str(row["center"]),
                str(row["out_length"]),
                str(row["frames"]),
                f"{row['error']:.2e}" if row["error"] != math.inf else "n/a",
            ]
        )
    out.append(
        _table(
            ["n_fft", "overlap", "in", "center", "out", "frames", "error"],
            rows,
        )
    )

    # --- 8. timing ----------------------------------------------------------
    out.append("\n## 8. The cost of one transform\n")
    rows = []
    for row in data["timing"]:
        budget = row["budget_us"]
        share = (row["forward_us"] / budget * 100.0) if budget else math.nan
        rows.append(
            [
                str(row["n"]),
                f"{row['forward_us']:.2f}",
                f"{row['inverse_us']:.2f}",
                "-" if row["radix2_us"] is None else f"{row['radix2_us']:.1f}",
                "-" if budget is None else f"{budget:.1f}",
                "-" if budget is None else f"{share:.2f} %",
            ]
        )
    out.append(
        _table(
            ["n", "forward us", "inverse us", "radix-2 us", "block budget us", "share"],
            rows,
        )
    )

    # --- 9. edge phase ------------------------------------------------------
    out.append("\n## 9. The phase at the edges of a block\n")
    rows = []
    for row in data["edge_phase"]:
        rows.append(
            [
                str(row["length"]),
                "even" if row["even"] else "odd",
                str(row["bins"]),
                f"{row['dc_imag']:.3e}",
                f"{row['dc_phase']:.6f}",
                f"{row['top_value'][0]:.6f}",
                f"{row['top_value'][1]:.6f}",
                f"{row['top_phase']:.6f}",
                f"{row['mag_phase_rebuild_error']:.3e}",
            ]
        )
    out.append(
        _table(
            [
                "length",
                "parity",
                "bins",
                "DC imag",
                "DC phase",
                "top real",
                "top imag",
                "top phase",
                "mag/phase rebuild error",
            ],
            rows,
        )
    )

    # --- 10. padding assumption --------------------------------------------
    out.append("\n## 10. Does the transform assume the block is zero padded?\n")
    rows = []
    for row in data["padding"]:
        rows.append(
            [
                row["case"],
                f"{row['peak']:.1f}",
                f"{row['leakage_floor']:.3e}",
                f"{row['leakage_db_below_peak']:.1f} dB",
            ]
        )
    out.append(_table(["block", "peak bin", "worst other bin", "leakage"], rows))

    # --- 11. numerical edges ------------------------------------------------
    out.append("\n## 11. The numerical edges\n")
    rows = []
    for row in data["edge_cases"]:
        rows.append(
            [
                row["name"],
                "yes" if row["spec_finite"] else "NO",
                "yes" if row["back_finite"] else "NO",
                "yes" if row["spec_is_zero"] else "no",
                f"{row['mag_db']:.1f} dB",
                f"{row['phase_first']:.3f}",
                row["note"],
            ]
        )
    out.append(
        _table(
            ["case", "spectrum finite", "rebuild finite", "spectrum zero", "mag of DC",
             "phase of DC", "what happens"],
            rows,
        )
    )

    # --- 12. denormal cost --------------------------------------------------
    out.append("\n## 12. The cost of a transform of denormal samples\n")
    rows = [
        [
            str(row["n"]),
            f"{row['normal_us']:.2f}",
            f"{row['denormal_us']:.2f}",
            f"{row['ratio']:.2f} x",
        ]
        for row in data["denormal_cost"]
    ]
    out.append(_table(["n", "normal us", "denormal us", "ratio"], rows))

    return "\n".join(out)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="run_fft", description="Measure the real FFT of the Auralis core."
    )
    parser.add_argument(
        "--json", action="store_true", help="write the raw results to results.json"
    )
    parser.add_argument(
        "--table", action="store_true", help="print the Markdown tables and nothing else"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    data = run_all()
    tables = render_tables(data)
    if args.json or not args.table:
        RESULT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        TABLES_PATH.write_text(tables, encoding="utf-8")
        print(f"wrote {RESULT_PATH}")
        print(f"wrote {TABLES_PATH}")
    print(tables)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
