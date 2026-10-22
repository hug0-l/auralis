#!/usr/bin/env python3
"""
qa.qualityvoice.run_voice — the runner of the voice quality instrument.

It measures every voice shaped module on every case of the instrument and
writes the numbers the report quotes:

* the metrics of every probe of ``qa/qualityvoice/rules.py``,
* the block coherence of every probe,
* the calibration of the chatter measurement (the three reference modules),
* the measured properties of the synthetic voice.

    python qa/qualityvoice/run_voice.py
    python qa/qualityvoice/run_voice.py --json qa/qualityvoice/results.json

The gate is ``tests/test_quality_voice.py``; it reads the same harness and
the same rules, so the numbers of this runner and the numbers of the gate
are the same numbers.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load(name: str, filename: str):
    """Load a sibling of the runner by path."""
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load("aur_qa_qualityvoice_harness", "harness.py")
rules = _load("aur_qa_qualityvoice_rules", "rules.py")
voice_signal = harness.voice_signal

__all__ = ["main", "measure_all", "render_tables"]


def _make(code: str):
    """Return a fresh instance of a module of the catalogue."""
    from aurcore import registry

    return registry.create(code, harness.SR, 1)


def measure_all() -> dict[str, Any]:
    """Measure every probe, the calibration and the synthetic voice."""
    cases = harness.build_cases()
    probes: dict[str, Any] = {}
    for probe in rules.PROBES:
        try:
            module = _make(probe.module)
        except Exception as error:  # noqa: BLE001 - a broken module is reported
            probes[probe.name] = {
                "module": probe.module,
                "case": probe.case,
                "error": f"{type(error).__name__}: {error}",
            }
            continue
        result = harness.evaluate(
            cases[probe.case], module, probe.config, probe.chatter_band
        )
        coherence = harness.measure_block_coherence(
            cases[probe.case],
            lambda code=probe.module: _make(code),
            probe.config,
            block_size=4096,
        )
        probes[probe.name] = {
            "module": probe.module,
            "case": probe.case,
            "config": dict(probe.config),
            "chatter_band": list(probe.chatter_band),
            "metrics": result.as_row(),
            "block": coherence,
        }

    # -- the calibration of the chatter measurement -------------------------
    case = cases["voice_hiss"]
    x = np.asarray(case.x, dtype=np.float64)
    # a gain that is redrawn every frame with a standard deviation of 2.0 dB
    rng = np.random.default_rng(20_260_103)
    frame_gains = (2.0 * rng.standard_normal(64)).tolist()
    calibration = []
    for name, factory in (
        ("the identity", lambda: _Identity()),
        ("SteadyGain", lambda: harness.SteadyGain()),
        ("FrameGain", lambda: harness.FrameGain(gains_db=frame_gains)),
        ("ContentFollower", lambda: harness.ContentFollower()),
    ):
        module = factory()
        y = np.asarray(module.process(x.copy()), dtype=np.float64)
        if y.ndim == 1:
            y = y[:, None]
        measured = harness.measure_chatter(x, y, harness.HISS_BAND)
        calibration.append(
            {
                "name": name,
                "chatter_db": measured["chatter_db"],
                "gain_std_db": measured["chatter_gain_std_db"],
                "content_span_db": measured["chatter_content_span_db"],
            }
        )

    # -- the properties of the synthetic voice ------------------------------
    voice = voice_signal.synth_voice()
    measured = voice_signal.measure_voice(voice)
    properties = voice_signal.voice_property_table(measured)

    # -- the voice de-noise in parts ---------------------------------------
    parts = _voice_denoise_parts(cases["voice_hiss"])

    return {
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "harness_version": harness.harness_version,
        },
        "probes": probes,
        "calibration": calibration,
        "voice": {"measured": measured, "table": properties},
        "voice_denoise_parts": parts,
        "rules": {
            "R1_wanted_content_db": rules.VOICE_BUDGET_DB,
            "R2_target_db": {p.name: p.rule.target_reduction_db for p in rules.PROBES},
            "R3_chatter_db": rules.CHATTER_BUDGET_DB,
            "R4_latency_samples": rules.LATENCY_TOLERANCE_SAMPLES,
            "R5_block_coherence_db": rules.BLOCK_COHERENCE_DB,
            "R6_transparency": rules.TRANSPARENCY_TOLERANCE,
            "R7_level_db": rules.LEVEL_BUDGET_DB,
            "R8_clean_db": rules.CLEAN_BUDGET_DB,
        },
    }


#: The bands the voice de-noise is read in, in parts.
_BANDS: tuple[tuple[float, float], ...] = (
    (120.0, 400.0),
    (400.0, 700.0),
    (700.0, 1200.0),
    (1200.0, 1800.0),
    (1800.0, 2400.0),
    (2400.0, 3000.0),
    (3000.0, 4000.0),
    (4000.0, 5000.0),
    (5000.0, 8000.0),
    (8000.0, 14000.0),
)


def _voice_denoise_parts(case) -> list[dict[str, float]]:
    """Return the change of every band of the voice de-noise, on one case."""
    module = _make("AUR_VOICEDENOISE")
    x = np.asarray(case.x, dtype=np.float64)
    y = np.asarray(module.process(x.copy()), dtype=np.float64)
    if y.ndim == 1:
        y = y[:, None]
    latency = harness.measure_latency(x, y)
    span = x.shape[0] - abs(latency)
    if latency >= 0:
        xa, ya = x[:span], y[latency : latency + span]
    else:
        xa, ya = x[-latency : -latency + span], y[:span]
    rows: list[dict[str, float]] = []
    for low, high in _BANDS:
        before = harness.band_energy_db(xa, low, high, notch=case.notch)
        after = harness.band_energy_db(ya, low, high, notch=case.notch)
        rows.append(
            {
                "lo_hz": low,
                "hi_hz": high,
                "before_db": before,
                "after_db": after,
                "change_db": after - before,
            }
        )
    return rows


class _Identity:

    code = "QA_IDENTITY"

    def __init__(self, sample_rate: int = harness.SR, channels: int = 1):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.params = harness._NullParams()

    def process(self, x: np.ndarray) -> np.ndarray:
        """Return the signal unchanged."""
        return np.asarray(x, dtype=np.float64)

    def reset(self) -> None:
        """Nothing to reset."""

    @property
    def latency_samples(self) -> int:
        """The reference module adds no delay."""
        return 0


def render_tables(payload: dict[str, Any]) -> str:
    """Render the measurements as the tables of the report."""
    lines: list[str] = []
    lines.append("| probe | module | case | R2 the target | R1 the content | R3 the chatter "
                 "| R4 the latency | R5 the block | R6 | R7 the level | R8 the clean signal |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for probe in rules.PROBES:
        entry = payload["probes"].get(probe.name, {})
        if "metrics" not in entry:
            lines.append(f"| `{probe.name}` | `{probe.module}` | {probe.case} | "
                         f"**not measured**: {entry.get('error', '?')} | | | | | | | |")
            continue
        metrics = entry["metrics"]
        rule = probe.rule

        target = "—"
        if rule.target_band:
            regions = rule.target_regions or ("",)
            values = []
            for region in regions:
                key = (
                    f"region_{region}_{rule.target_band}_change_db"
                    if region
                    else f"band_{rule.target_band}_change_db"
                )
                values.append(metrics.get(key, 0.0))
            target = f"**{min(values):.2f} dB**"
        content = f"{abs(metrics.get(f'band_{rule.protected_band}_change_db', 0.0)):.2f} dB"
        chatter = f"{metrics.get('chatter_db', 0.0):.2f} dB"
        latency = (f"{int(metrics.get('latency_reported', 0))} vs "
                   f"{int(metrics.get('latency_measured', 0))}")
        block = f"**{entry['block']['block_rms_diff_db']:.1f} dB**"
        transparency = "—"
        if rule.exact_output:
            transparency = f"{metrics.get('output_max_diff', 0.0):.1e}"
        level = f"{metrics.get('output_level_db', 0.0):+.2f} dB"
        clean = "—"
        if rule.clean_budget_db is not None:
            clean = f"**{abs(metrics.get(f'band_{rule.protected_band}_change_db', 0.0)):.2f} dB**"
        lines.append(
            f"| `{probe.name}` | `{probe.module}` | {probe.case} | {target} | {content} "
            f"| {chatter} | {latency} | {block} | {transparency} | {level} | {clean} |"
        )
    lines.append("")
    lines.append("| reference module | the chatter | the spread of its gain | the spread of the "
                 "content |")
    lines.append("|---|---|---|---|")
    for entry in payload["calibration"]:
        lines.append(
            f"| {entry['name']} | **{entry['chatter_db']:.3f} dB** | "
            f"{entry['gain_std_db']:.2f} dB | {entry['content_span_db']:.1f} dB |"
        )
    lines.append("")
    lines.append("| band | the change |")
    lines.append("|---|---|")
    for row in payload.get("voice_denoise_parts", []):
        lines.append(
            f"| {row['lo_hz']:.0f}-{row['hi_hz']:.0f} Hz | {row['change_db']:+.2f} dB |"
        )
    lines.append("")
    lines.append("| property | the measured value | a voice has | verdict |")
    lines.append("|---|---|---|---|")
    for name, value, reference, verdict in payload["voice"]["table"]:
        lines.append(f"| {name} | {value} | {reference} | {verdict} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the instrument and print (or write) the tables."""
    parser = argparse.ArgumentParser(description="Measure the voice shaped modules.")
    parser.add_argument("--json", type=Path, default=None, help="write the results as JSON")
    parser.add_argument(
        "--default-json",
        action="store_true",
        help="write the results to qa/qualityvoice/results.json",
    )
    args = parser.parse_args(argv)

    payload = measure_all()
    print(render_tables(payload))
    target = args.json
    if target is None and args.default_json:
        target = _HERE / "results.json"
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
