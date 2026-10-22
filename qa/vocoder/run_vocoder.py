"""
qa.vocoder.run_vocoder — run every measurement of the phase vocoder.

    python qa/vocoder/run_vocoder.py
    python qa/vocoder/run_vocoder.py --json var/vocoder.json
    python qa/vocoder/run_vocoder.py --only pitch

The command prints the measurement tables and, with ``--json``, writes them
as a machine readable report so that two runs can be compared.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    SR,
    EdgeResult,
    PitchResult,
    SpectrumResult,
    StretchResult,
    TransientResult,
    check_edges,
    measure_cost,
    measure_pitch,
    measure_spectrum,
    measure_stretch,
    measure_transient,
)

BLOCKS = (64, 128, 256, 512)


# ---------------------------------------------------------------------------
# the measurements
# ---------------------------------------------------------------------------
def pitch_table() -> list[PitchResult]:
    """The pitch accuracy of the two pitch shift paths."""
    rows: list[PitchResult] = []
    for method in ("resample", "spectral"):
        for source in (220.0, 440.0, 1000.0, 4000.0):
            for factor in (1.25, 1.5, 2.0, 0.5):
                if source * factor > SR / 2.0 * 0.98:
                    continue
                rows.append(measure_pitch(source, factor, method=method))
    return rows


def fallback_table() -> list[PitchResult]:
    """The pitch accuracy of the resampler without SciPy."""
    rows: list[PitchResult] = []
    for source in (220.0, 440.0, 1000.0):
        for factor in (1.25, 1.5, 2.0, 0.5):
            if source * factor > SR / 2.0 * 0.98:
                continue
            row = measure_pitch(source, factor, method="resample", fallback=True)
            row.label = f"numpy resampler {source:.0f} Hz x{factor:g}"
            rows.append(row)
    return rows


def pitch_sweep() -> dict[str, list[float]]:
    """The cents error of the spectral path over a grid of frequencies."""
    from harness import dominant_frequency, tone

    from aurcore.dsp.phase_vocoder import PhaseVocoder

    vocoder = PhaseVocoder(2048, 512, SR)
    frequencies = [110.0, 220.0, 440.0, 880.0, 1000.0, 2000.0, 4000.0, 8000.0, 12000.0]
    out: dict[str, list[float]] = {}
    for factor in (0.5, 1.25, 1.5, 2.0):
        row = []
        for frequency in frequencies:
            if frequency * factor > SR / 2.0 * 0.98:
                row.append(float("nan"))
                continue
            state = vocoder.analyze(tone(frequency, 0.5, SR))
            shifted = vocoder.synthesize(state, stretch=1.0, pitch=factor)
            measured = dominant_frequency(shifted, SR)
            row.append(
                float(1200.0 * np.log2(measured / (frequency * factor)))
                if measured > 0.0
                else float("nan")
            )
        out[f"x{factor:g}"] = row
    return out


def stretch_table() -> list[StretchResult]:
    """The spacing accuracy of the click train."""
    rows: list[StretchResult] = []
    for factor in (0.5, 0.75, 1.5, 2.0, 3.0):
        rows.append(measure_stretch(100.0, 10, factor))
    return rows


def transient_table() -> list[TransientResult]:
    """The width and the level of the stretched attack."""
    rows: list[TransientResult] = []
    for factor in (0.5, 1.5, 2.0):
        rows.append(measure_transient(factor=factor))
    return rows


def spectrum_table() -> list[SpectrumResult]:
    """The level of the top band before and after the shift."""
    rows: list[SpectrumResult] = []
    for method in ("resample", "spectral"):
        for factor in (1.5, 0.5):
            rows.append(measure_spectrum(factor=factor, method=method))
    return rows


def cost_table() -> list:
    """The cost of one block at the four block sizes."""
    return [measure_cost(block) for block in BLOCKS]


def cost_long_table() -> list:
    """The cost of one second of audio, where the transient detector runs."""
    rows = []
    for block in BLOCKS:
        rows.append(measure_cost(block, signal_seconds=1.0))
    return rows


def edge_table() -> list[EdgeResult]:
    """The five awkward inputs."""
    return check_edges()


def level_table() -> list[tuple[str, float, float]]:
    """The level change of the time stretch on broadband material."""
    from harness import tone

    from aurcore.dsp.phase_vocoder import PhaseVocoder

    vocoder = PhaseVocoder(2048, 512, SR)
    rng = np.random.default_rng(20240607)
    rows = []
    for name, signal in (
        ("white noise", rng.standard_normal(SR) * 0.1),
        ("440 Hz tone", tone(440.0, 1.0, SR)),
    ):
        for factor in (1.0, 1.5, 2.0):
            stretched = vocoder.time_stretch(signal, factor)
            before = 20.0 * np.log10(max(float(np.sqrt(np.mean(np.square(signal)))), 1e-20))
            after = 20.0 * np.log10(
                max(float(np.sqrt(np.mean(np.square(stretched)))), 1e-20)
            )
            rows.append((f"{name} x{factor:g}", before, after))
    return rows


# ---------------------------------------------------------------------------
# the tables
# ---------------------------------------------------------------------------
def render_pitch(rows: list[PitchResult]) -> str:
    lines = [
        "| path | source | factor | expected | measured | error | output length |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label.split()[0]} | {row.source_hz:.0f} Hz | x{row.factor:g} | "
            f"{row.expected_hz:.2f} Hz | {row.measured_hz:.2f} Hz | "
            f"{row.cents:+.2f} cents | {row.length_out} |"
        )
    return "\n".join(lines)


def render_pitch_sweep(sweep: dict[str, list[float]], frequencies: list[float]) -> str:
    header = "| factor | " + " | ".join(f"{f:.0f} Hz" for f in frequencies) + " |"
    lines = [header, "|---" * (len(frequencies) + 1) + "|"]
    for factor, row in sweep.items():
        cells = " | ".join(
            "n/a" if not np.isfinite(value) else f"{value:+.2f}" for value in row
        )
        lines.append(f"| {factor} | {cells} |")
    return "\n".join(lines)


def render_stretch(rows: list[StretchResult]) -> str:
    lines = [
        "| factor | expected spacing | median spacing | median error | mean spacing | "
        "mean error | position error | clicks found | output length |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| x{row.factor:g} | {row.expected_period_ms:.3f} ms | "
            f"{row.measured_period_ms:.3f} ms | {row.spacing_error_percent:+.3f} % | "
            f"{row.mean_period_ms:.3f} ms | {row.mean_error_percent:+.3f} % | "
            f"{row.position_error_ms:.3f} ms | "
            f"{row.clicks_out}/{row.clicks_in} | {row.length_out} |"
        )
    return "\n".join(lines)


def render_transient(rows: list[TransientResult]) -> str:
    lines = [
        "| factor | attack width in | attack width out | width ratio | energy spread | "
        "pre-echo | post-echo | peak loss | energy within 1 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| x{row.factor:g} | {row.width_ms_in:.4f} ms | {row.width_ms_out:.4f} ms | "
            f"{row.width_ratio:.2f} | {row.spread_ms:.2f} ms | {row.pre_echo_db:.1f} dB | "
            f"{row.post_echo_db:.1f} dB | {row.peak_loss_db:+.2f} dB | "
            f"{row.energy_1ms_percent:.2f} % |"
        )
    return "\n".join(lines)


def render_spectrum(rows: list[SpectrumResult]) -> str:
    lines = [
        "| path | factor | top band in | top band out | top band loss | worst band | worst loss |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        path = "resample" if "resample" in row.label else "spectral"
        lines.append(
            f"| {path} | x{row.factor:g} | {row.top_band_db_in:.2f} dB | "
            f"{row.top_band_db_out:.2f} dB | {row.top_band_loss_db:.2f} dB | "
            f"{row.worst_band_low_hz:.0f} Hz | {row.worst_band_loss_db:.2f} dB |"
        )
    return "\n".join(lines)


def render_cost(rows: list, show_budget: bool = True) -> str:
    lines = [
        "| block | budget | total | budget share | analyse | transient detection | "
        "median share | synthesis loop | phase locking | overlap-add | frames |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        budget = f"{row.budget_us:.1f} us | {row.budget_percent:.1f} %" if show_budget else "- | -"
        lines.append(
            f"| {row.block} | {budget} | {row.total_us:.1f} us | "
            f"{row.analyze_us:.1f} us | "
            f"{row.transients_us:.1f} us | {row.median_percent:.1f} % | "
            f"{row.loop_us:.1f} us | {row.lock_us:.1f} us | {row.overlap_us:.1f} us | "
            f"{row.frames} |"
        )
    return "\n".join(lines)


def render_levels(rows: list[tuple[str, float, float]]) -> str:
    lines = ["| material | level in | level out | change |", "|---|---|---|---|"]
    for name, before, after in rows:
        lines.append(f"| {name} | {before:.2f} dB | {after:.2f} dB | {after - before:+.2f} dB |")
    return "\n".join(lines)


def render_edges(rows: list[EdgeResult]) -> str:
    lines = ["| input | finite | length kept | peak | note |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row.name} | {'yes' if row.finite else 'NO'} | "
            f"{'yes' if row.length_ok else 'NO'} | {row.peak:.4f} | {row.message or '-'} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_vocoder", description="Measure the phase vocoder."
    )
    parser.add_argument("--json", type=Path, default=None, help="write the report as JSON")
    parser.add_argument(
        "--only",
        default="all",
        help="run one group only: pitch, stretch, transient, spectrum, cost, edge",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    only = str(args.only).lower()
    everything = only == "all"

    payload: dict[str, object] = {}

    if everything or only == "pitch":
        rows = pitch_table()
        sweep = pitch_sweep()
        frequencies = [110.0, 220.0, 440.0, 880.0, 1000.0, 2000.0, 4000.0, 8000.0, 12000.0]
        print("## pitch accuracy\n")
        print(render_pitch(rows))
        print()
        print("### cents error of the spectral path\n")
        print(render_pitch_sweep(sweep, frequencies))
        print()
        payload["pitch"] = [
            {
                "label": row.label,
                "source_hz": row.source_hz,
                "factor": row.factor,
                "expected_hz": row.expected_hz,
                "measured_hz": row.measured_hz,
                "cents": row.cents,
                "length_out": row.length_out,
            }
            for row in rows
        ]
        payload["pitch_sweep"] = sweep

        fallback = fallback_table()
        print("### the resampler without SciPy (the numpy fallback)\n")
        print(render_pitch(fallback))
        print()
        payload["pitch_fallback"] = [
            {
                "label": row.label,
                "source_hz": row.source_hz,
                "factor": row.factor,
                "expected_hz": row.expected_hz,
                "measured_hz": row.measured_hz,
                "cents": row.cents,
                "rms_db": row.rms_db,
            }
            for row in fallback
        ]

    if everything or only == "stretch":
        rows = stretch_table()
        print("## time stretch accuracy\n")
        print(render_stretch(rows))
        print()
        payload["stretch"] = [
            {
                "factor": row.factor,
                "expected_period_ms": row.expected_period_ms,
                "measured_period_ms": row.measured_period_ms,
                "spacing_error_percent": row.spacing_error_percent,
                "mean_period_ms": row.mean_period_ms,
                "mean_error_percent": row.mean_error_percent,
                "position_error_ms": row.position_error_ms,
                "clicks_out": row.clicks_out,
                "clicks_in": row.clicks_in,
                "length_out": row.length_out,
            }
            for row in rows
        ]

    if everything or only == "transient":
        rows = transient_table()
        print("## transient handling\n")
        print(render_transient(rows))
        print()
        payload["transient"] = [
            {
                "factor": row.factor,
                "width_ms_in": row.width_ms_in,
                "width_ms_out": row.width_ms_out,
                "width_ratio": row.width_ratio,
                "spread_ms": row.spread_ms,
                "pre_echo_db": row.pre_echo_db,
                "post_echo_db": row.post_echo_db,
                "peak_loss_db": row.peak_loss_db,
                "energy_1ms_percent": row.energy_1ms_percent,
            }
            for row in rows
        ]

    if everything or only == "spectrum":
        rows = spectrum_table()
        print("## spectral loss\n")
        print(render_spectrum(rows))
        print()
        payload["spectrum"] = [
            {
                "label": row.label,
                "top_band_db_in": row.top_band_db_in,
                "top_band_db_out": row.top_band_db_out,
                "top_band_loss_db": row.top_band_loss_db,
                "worst_band_low_hz": row.worst_band_low_hz,
                "worst_band_loss_db": row.worst_band_loss_db,
            }
            for row in rows
        ]

    if everything or only == "level":
        rows = level_table()
        print("## level change of the time stretch\n")
        print(render_levels(rows))
        print()
        payload["level"] = [
            {"material": name, "db_in": before, "db_out": after} for name, before, after in rows
        ]

    if everything or only == "cost":
        rows = cost_table()
        long_rows = cost_long_table()
        print("## cost per block\n")
        print(render_cost(rows))
        print()
        print("### the same vocoder over one second of audio (the transient detector runs)\n")
        print(render_cost(long_rows, show_budget=False))
        print()
        payload["cost"] = [
            {
                "block": row.block,
                "budget_us": row.budget_us,
                "total_us": row.total_us,
                "budget_percent": row.budget_percent,
                "analyze_us": row.analyze_us,
                "transients_us": row.transients_us,
                "median_us": row.median_us,
                "median_percent": row.median_percent,
                "loop_us": row.loop_us,
                "lock_us": row.lock_us,
                "overlap_us": row.overlap_us,
                "frames": row.frames,
            }
            for row in rows
        ]
        payload["cost_one_second"] = [
            {
                "block": row.block,
                "total_us": row.total_us,
                "analyze_us": row.analyze_us,
                "transients_us": row.transients_us,
                "median_us": row.median_us,
                "median_percent": row.median_percent,
                "loop_us": row.loop_us,
                "lock_us": row.lock_us,
                "overlap_us": row.overlap_us,
                "frames": row.frames,
            }
            for row in long_rows
        ]

    if everything or only == "edge":
        rows = edge_table()
        print("## the five awkward inputs\n")
        print(render_edges(rows))
        print()
        payload["edge"] = [
            {
                "name": row.name,
                "ok": row.ok,
                "finite": row.finite,
                "length_ok": row.length_ok,
                "peak": row.peak,
                "message": row.message,
            }
            for row in rows
        ]

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
