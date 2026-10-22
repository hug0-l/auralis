"""
qa.measure_edge_contract -- the edge contract, measured on our own modules.

The edge contract is the set of rules a module has to hold at the limits of
the signal: silence, a full scale tone, an impulse, a hard clipped signal, a
full scale offset, a denormal, a square at the ceiling and inputs of one,
two, seven, sixty four and five hundred and eleven frames.  The rules and
the numbers of the references that were measured against them are in
``docs/EDGE_CONTRACT.md``, and the constants that carry our side of the
table are in ``qa/benchmark/edge_cases.py``.

This script is the instrument of our side of that table.  It drives every
module of the registry over the edge signals of the study and reports

``one_frame_out``
    the number of frames the module hands back for an input of one frame;
``non_finite``
    the number of output samples that are not a number, over every edge
    signal;
``silence_peak``
    the peak of the output when the input is silence;
``ring_deep``
    the number of frames the module keeps sounding after the input stopped,
    counted above the -180 dBFS floor the study used;
``ring_80``, ``ring_tree``
    the same ring-out counted above the -80 dBFS floor and above the -60 dBFS
    floor the gate of the tree uses (``qa/check_module_edges.py``);
``square_peak``, ``square_over``
    the peak of the output when the input is a square at the ceiling, and the
    number of samples outside [-1, 1].

The module is driven the way the study drove it: the whole signal in one call
to ``process()``, at 48 kHz, in stereo.

Usage::

    python qa/measure_edge_contract.py                 # the table
    python qa/measure_edge_contract.py --json out.json  # the same, as JSON
    python qa/measure_edge_contract.py --only AUR_DECLICK
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurcore import registry  # noqa: E402

#: The sample rate every module is measured at.
SAMPLE_RATE = 48_000

#: The channel count every module is measured at.
CHANNELS = 2

#: The length of the long edge signals, in samples.
FRAMES = 4096

#: The length of the square at the ceiling, in samples: one second at 48 kHz,
#: the length the study measured the reference over.
SQUARE_FRAMES = 48_000

#: The floor of the ring-out of the study: -180 dBFS.
RING_FLOOR_DEEP = 1.0e-9

#: The floor of the ring-out at the usual thresholds: -80 dBFS.
RING_FLOOR_80 = 1.0e-4

#: The floor of the ring-out of the gate of the tree: -60 dBFS.
RING_FLOOR_TREE = 1.0e-3

#: The digital ceiling: a sample outside [-1, 1] is outside the range a
#: 16 bit file can carry.
CEILING = 1.0


# ---------------------------------------------------------------------------
# the edge signals
# ---------------------------------------------------------------------------
def _stereo(signal: np.ndarray) -> np.ndarray:
    """Return the signal as an identical stereo pair."""
    return np.repeat(np.asarray(signal, dtype=np.float64)[:, None], CHANNELS, axis=1)


def edge_signals(frames: int = FRAMES) -> dict[str, np.ndarray]:
    """Return the edge signals of the contract, by name.

    ``silence``
        nothing at all.
    ``full_scale_tone``
        a sine at the ceiling, 1 kHz.
    ``impulse_first``, ``impulse_middle``
        one sample of full scale, at the first frame and in the middle.
    ``clip_square``
        a square at the ceiling: it alternates between +1.0 and -1.0.
    ``clip_hard_tone``
        a sine driven into the rails, so the samples sit at the ceiling.
    ``dc_offset``
        a full scale constant.
    ``denormal``
        a full scale constant of 1e-40, below the smallest number a double
        precision number holds without flushing.
    ``one_frame`` and the other short signals
        inputs of one, two, seven, sixty four and five hundred and eleven
        frames.

    Returns
    -------
    dict[str, numpy.ndarray]
        The signals, shape ``(n_samples, 2)``.
    """
    n = frames
    t = np.arange(n) / float(SAMPLE_RATE)

    impulse_first = np.zeros(n)
    impulse_first[0] = 1.0
    impulse_middle = np.zeros(n)
    impulse_middle[n // 2] = 1.0

    signals: dict[str, np.ndarray] = {
        "silence": np.zeros((n, CHANNELS)),
        "full_scale_tone": _stereo(np.sin(2 * np.pi * 1000 * t)),
        "impulse_first": _stereo(impulse_first),
        "impulse_middle": _stereo(impulse_middle),
        "clip_square": _stereo(np.sign(np.sin(2 * np.pi * 100 * t))),
        "clip_hard_tone": _stereo(np.clip(4 * np.sin(2 * np.pi * 1000 * t), -1, 1)),
        "dc_offset": _stereo(np.ones(n)),
        "denormal": _stereo(np.full(n, 1.0e-40)),
    }
    for name, length in (
        ("one_frame", 1),
        ("two_frames", 2),
        ("seven_frames", 7),
        ("sixty_four_frames", 64),
        ("five_hundred_eleven_frames", 511),
    ):
        signals[name] = np.ones((length, CHANNELS))
    return signals


def ceiling_square(frames: int = SQUARE_FRAMES) -> np.ndarray:
    """Return a square at the ceiling, alternating between +1.0 and -1.0."""
    t = np.arange(frames) / float(SAMPLE_RATE)
    return _stereo(np.sign(np.sin(2 * np.pi * 100 * t)))


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def expected_length(module, frames: int) -> int:
    """Return the number of samples the module owes for this input.

    A module that keeps the rate of the signal hands back the number of
    samples it was given.  The module that changes the rate on purpose says
    so, and the number of samples follows the rate it declares.

    Parameters
    ----------
    module:
        The module.
    frames:
        The number of samples that were handed in.

    Returns
    -------
    int
        The number of samples the module owes.
    """
    rate = getattr(module, "output_sample_rate", None)
    if rate:
        try:
            return int(round(float(frames) * float(rate) / float(SAMPLE_RATE)))
        except (TypeError, ValueError):
            return int(frames)
    return int(frames)


def peak_of(block: np.ndarray) -> float:
    """Return the largest absolute value of a block, ignoring what is not finite.

    Parameters
    ----------
    block:
        The block to measure.

    Returns
    -------
    float
        The largest absolute value, or 0.0 for an empty block.
    """
    if block.size == 0:
        return 0.0
    good = block[np.isfinite(block)]
    return float(np.max(np.abs(good))) if good.size else 0.0


def ring_out_frames(tail: np.ndarray, floor: float) -> int:
    """Return the number of frames of the tail that stay above the floor.

    Parameters
    ----------
    tail:
        The part of the output that follows the input.
    floor:
        The level below which a sample counts as silence.

    Returns
    -------
    int
        The ring-out in frames; zero when the module goes quiet at once.
    """
    loud = np.nonzero(np.abs(tail) > floor)[0]
    return int(loud[-1]) + 1 if loud.size else 0


def _process(code: str, signal: np.ndarray) -> np.ndarray:
    """Return what the module hands back for the signal.

    A module that raises is a finding and not a crash of the measurement:
    the error is raised again, and the caller catches it.

    Parameters
    ----------
    code:
        The module code.
    signal:
        The signal to hand to the module.

    Returns
    -------
    numpy.ndarray
        The output of the module.
    """
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    out = np.asarray(module.process(signal), dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    return out


def measure(code: str) -> dict:
    """Measure one module over the edge signals.

    A module that raises is measured as far as it can be: the numbers that
    were taken before the raise are kept and the error is written into the
    row, so that a broken module is a row of the table and not a crash of
    the table.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    dict
        The numbers of the module.
    """
    row: dict = {"code": code}
    signals = edge_signals()

    # -- the length of an input of one frame ----------------------------
    try:
        out = _process(code, signals["one_frame"])
        row["one_frame_out"] = int(out.shape[0])
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        row["one_frame_wanted"] = expected_length(module, 1)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        row["one_frame_out"] = -1
        row["one_frame_wanted"] = -1
        row["error"] = f"{type(exc).__name__}: {exc}"

    # -- the finiteness and the length over every edge signal ------------
    non_finite = 0
    length_faults = 0
    raised = 0
    for signal in signals.values():
        try:
            out = _process(code, signal)
        except Exception:  # noqa: BLE001 - the finding is the report
            raised += 1
            continue
        non_finite += int(np.count_nonzero(~np.isfinite(out)))
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        if int(out.shape[0]) != expected_length(module, int(signal.shape[0])):
            length_faults += 1
    row["non_finite"] = non_finite
    row["length_faults"] = length_faults
    row["raised"] = raised

    # -- the silence -----------------------------------------------------
    try:
        row["silence_peak"] = peak_of(_process(code, signals["silence"]))
    except Exception:  # noqa: BLE001 - the finding is the report
        row["silence_peak"] = float("nan")

    # -- the ring out ----------------------------------------------------
    tone = signals["full_scale_tone"]
    stream = np.zeros((tone.shape[0] + 8192, CHANNELS))
    stream[: tone.shape[0]] = tone
    try:
        out = _process(code, stream)
        tail = out[tone.shape[0]:]
        row["ring_deep"] = ring_out_frames(tail, RING_FLOOR_DEEP)
        row["ring_80"] = ring_out_frames(tail, RING_FLOOR_80)
        row["ring_tree"] = ring_out_frames(tail, RING_FLOOR_TREE)
    except Exception:  # noqa: BLE001 - the finding is the report
        row["ring_deep"] = -1
        row["ring_80"] = -1
        row["ring_tree"] = -1
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    row["latency"] = int(getattr(module, "latency_samples", 0))
    row["tail_declared"] = int(getattr(module, "tail_samples", 0))

    # -- the square at the ceiling ----------------------------------------
    try:
        out = _process(code, ceiling_square())
        row["square_peak"] = peak_of(out)
        row["square_over"] = int(np.count_nonzero(np.abs(out) > CEILING))
    except Exception:  # noqa: BLE001 - the finding is the report
        row["square_peak"] = float("nan")
        row["square_over"] = -1
    return row


def measure_all(codes: list[str] | None = None) -> list[dict]:
    """Measure every module of the registry, or the ones that are named.

    Parameters
    ----------
    codes:
        The module codes; every module of the registry when it is empty.

    Returns
    -------
    list[dict]
        One row per module.
    """
    if codes is None:
        codes = [info.code for info in registry.all()]
    return [measure(code) for code in codes]


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def render(rows: list[dict]) -> str:
    """Return the table of the measurement as text.

    Parameters
    ----------
    rows:
        The rows of the measurement.

    Returns
    -------
    str
        The table.
    """
    lines = [
        "%-22s %6s %5s %10s %8s %8s %8s %10s %7s"
        % (
            "module",
            "out1",
            "nf",
            "silence",
            "ring180",
            "ring80",
            "ring60",
            "sq_peak",
            "over",
        )
    ]
    for row in rows:
        if row.get("error"):
            lines.append("%-22s  ERROR %s" % (row["code"], row["error"]))
            continue
        lines.append(
            "%-22s %6d %5d %10.3e %8d %8d %8d %10.6f %7d"
            % (
                row["code"],
                row["one_frame_out"],
                row["non_finite"],
                row["silence_peak"],
                row["ring_deep"],
                row["ring_80"],
                row["ring_tree"],
                row["square_peak"],
                row["square_over"],
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the measurement and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="write the numbers as JSON")
    parser.add_argument("--only", default="", help="measure this module only")
    args = parser.parse_args(argv)

    if args.only:
        rows = measure_all([args.only])
    else:
        rows = measure_all()

    print(render(rows))

    if args.json:
        args.json.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print()
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
