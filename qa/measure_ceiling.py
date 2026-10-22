"""Measure every module against the digital ceiling on the edge cases.

The edge cases are the ones that say something about the level of a module:
a signal that already sits at the digital ceiling and comes out above it is
clipped by a host that writes an integer file and is out of range in a host
that writes a float file.  Every ordinary "does it remove the noise" test is
blind to this, so the measurement lives here.

The measurement drives the modules the way a host drives them: the engine,
one block at a time, 48 kHz, two channels, 256 frames per block.  The block
size is the one the study used, so the numbers can be compared with the
numbers of the report that found the defect.

For every module and every edge case the script reports

``peak``
    the largest absolute value of the output,
``over``
    the number of output samples outside ``[-1, 1]``,
``worst``
    how far the largest of them is outside the ceiling.

Usage::

    python qa/measure_ceiling.py                 # the table
    python qa/measure_ceiling.py --json out.json # the same numbers, as JSON
    python qa/measure_ceiling.py --only AUR_DECONSTRUCT
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurcore import registry  # noqa: E402
from aurcore.api import Engine  # noqa: E402

#: The sample rate every module is measured at.
SAMPLE_RATE = 48_000

#: The channel count every module is measured at.
CHANNELS = 2

#: The block size the host hands the module.
BLOCK = 256

#: How many frames of signal every edge case is.
FRAMES = 48_000

#: The digital ceiling.  A sample outside [-1, 1] is outside the range a
#: 16 bit file can carry.
CEILING = 1.0

#: The number of frames of silence that follow the signal, so that a module
#: that rings is measured over its whole ring-out.
RING = 24_000


# ---------------------------------------------------------------------------
# The edge cases.
# ---------------------------------------------------------------------------
def _stereo(wave: np.ndarray) -> np.ndarray:
    """Return ``wave`` as a two channel block."""
    return np.repeat(np.asarray(wave, dtype=np.float64)[:, None], CHANNELS, axis=1)


def edge_cases(frames: int = FRAMES) -> dict[str, np.ndarray]:
    """Return the edge cases, by name.

    ``sine_full``
        A full scale sine: 1 kHz at the digital ceiling.
    ``square_full``
        A full scale square: the level the format can carry, on every
        sample, full bandwidth.
    ``impulse_train``
        A full scale impulse train: one sample of +1.0 every 48 samples.
        A module that looks at the neighbourhood of a transient has to
        survive it.
    ``clipped``
        A signal that has already been clipped: a loud sine with flat tops.
        It is the case a file arrives in.
    ``lowfreq``
        A signal at 0 dBFS with a strong low frequency: 40 Hz with its
        harmonics, at the ceiling.  The filters that a repair module puts
        in the way of a low frequency are the ones that overshoot.
    ``dc_full``
        A full scale constant.
    ``impulse``
        One single sample of 1.0.
    """
    t = np.arange(frames, dtype=np.float64) / float(SAMPLE_RATE)
    cases: dict[str, np.ndarray] = {}

    cases["sine_full"] = _stereo(np.sin(2.0 * np.pi * 1000.0 * t))

    square = np.sign(np.sin(2.0 * np.pi * 220.0 * t))
    cases["square_full"] = _stereo(square)

    train = np.zeros(frames, dtype=np.float64)
    train[::48] = 1.0
    cases["impulse_train"] = _stereo(train)

    loud = 4.0 * np.sin(2.0 * np.pi * 997.0 * t)
    cases["clipped"] = _stereo(np.clip(loud, -1.0, 1.0))

    low = np.zeros(frames, dtype=np.float64)
    for harmonic, amp in ((1, 1.0), (2, 0.25), (3, 0.12), (4, 0.06)):
        low += amp * np.sin(2.0 * np.pi * 40.0 * harmonic * t)
    # The case is a signal *at* 0 dBFS, so the peak is scaled onto 1.0.
    peak = float(np.max(np.abs(low)))
    if peak > 0.0:
        low = low / peak
    cases["lowfreq"] = _stereo(low)

    cases["dc_full"] = _stereo(np.ones(frames, dtype=np.float64))

    one = np.zeros(frames, dtype=np.float64)
    one[0] = 1.0
    cases["impulse"] = _stereo(one)

    return cases


#: The order the table is printed in.
CASE_ORDER = (
    "sine_full",
    "square_full",
    "impulse_train",
    "clipped",
    "lowfreq",
    "dc_full",
    "impulse",
)


# ---------------------------------------------------------------------------
# The measurement.
# ---------------------------------------------------------------------------
def run_module(code: str, signal: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Run one module over one signal, the way a host drives it."""
    engine = Engine(sample_rate=SAMPLE_RATE, channels=CHANNELS)
    try:
        handle = engine.add_module(code)
        if handle is None:
            raise RuntimeError(f"module {code} is not registered")
        pieces = []
        for start in range(0, signal.shape[0], block):
            chunk = signal[start:start + block]
            if chunk.shape[0] == 0:
                continue
            pieces.append(np.asarray(engine.process(chunk), dtype=np.float64))
        if not pieces:
            return np.zeros((0, CHANNELS), dtype=np.float64)
        return np.concatenate(pieces, axis=0)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


def measure(code: str, cases: dict[str, np.ndarray], ring: int = RING) -> dict:
    """Measure one module over every edge case."""
    rows: dict[str, dict] = {}
    for name, signal in cases.items():
        tail = np.zeros((ring, CHANNELS), dtype=np.float64)
        out = run_module(code, np.concatenate((signal, tail), axis=0))
        absolute = np.abs(out)
        peak = float(absolute.max()) if absolute.size else 0.0
        over = int(np.count_nonzero(absolute > CEILING))
        rows[name] = {
            "peak": peak,
            "over": over,
            "worst": max(0.0, peak - CEILING),
            "samples": int(out.shape[0]),
        }
    return rows


def measure_all(cases: dict[str, np.ndarray] | None = None) -> dict[str, dict]:
    """Measure every registered module over every edge case."""
    cases = cases if cases is not None else edge_cases()
    table: dict[str, dict] = {}
    for info in registry.all():
        try:
            table[info.code] = measure(info.code, cases)
        except Exception as exc:  # noqa: BLE001 - a broken module is a finding
            table[info.code] = {"error": f"{type(exc).__name__}: {exc}"}
    return table


# ---------------------------------------------------------------------------
# The report.
# ---------------------------------------------------------------------------
def render(table: dict[str, dict]) -> str:
    """Return the table of the measurement as text."""
    lines: list[str] = []
    lines.append(
        f"{'module':<22}" + "".join(f"{case:>16}" for case in CASE_ORDER)
    )
    lines.append(
        f"{'':<22}" + "".join(f"{'peak/over':>16}" for case in CASE_ORDER)
    )
    for code in sorted(table):
        row = table[code]
        if "error" in row:
            lines.append(f"{code:<22}  ERROR {row['error'][:60]}")
            continue
        cells = ""
        worst = 0.0
        over_total = 0
        for case in CASE_ORDER:
            entry = row.get(case)
            if entry is None:
                cells += f"{'-':>16}"
                continue
            worst = max(worst, entry["worst"])
            over_total += entry["over"]
            cells += f"{entry['peak']:>9.4f}{entry['over']:>7d}"
        lines.append(f"{code:<22}{cells}   worst {worst:.4f} over {over_total}")
    return "\n".join(lines)


def main() -> int:
    """Run the measurement and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write the numbers to this file as JSON")
    parser.add_argument("--only", help="measure this module only")
    args = parser.parse_args()

    cases = edge_cases()
    if args.only:
        table = {args.only: measure(args.only, cases)}
    else:
        table = measure_all(cases)

    print(render(table))
    if args.json:
        Path(args.json).write_text(
            json.dumps(table, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
