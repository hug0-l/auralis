#!/usr/bin/env python3
"""
qa.measure_memory — what the model with a memory does, and what it costs.

The model with a memory is the per sample model with a stack of **causal**
convolutions over the time axis: it is asked once per sample and it reads
the frames before it.  The memory is what the first study's network had
and the per sample model of ours did not.

This tool measures the three things that decide whether the memory is
worth what it costs:

* the **cost**: how many times the real time the render takes;
* the **quality**: what the render does to real material — the damaged
  phone memo of ``qa/make_demo_memo.py``, measured with the corpus's own
  ruler;
* the **head**: the frames before the file are not there, so the first
  frames of the render are decided on an empty memory.  The tool says
  how loud that edge is.

The material is ours, the ruler is the project's, and the numbers this
tool prints are the numbers the documents carry.

    python qa/make_demo_memo.py
    python qa/measure_memory.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEMO = ROOT / "corpus" / "audio" / "test" / "voice_memo_damaged.wav"

BANDS = {
    "the voice 120-3000 Hz": (120.0, 3000.0),
    "the hum 45-55 Hz": (45.0, 55.0),
    "the rumble 20-80 Hz": (20.0, 80.0),
    "the hiss 5000-20000 Hz": (5000.0, 20000.0),
}

#: The models the table compares.
SAMPLE_MODEL = "voice_sample_mask"
MEMORY_MODEL = "voice_sample_memory"
FRAME_MODEL = "voice_enhance"


def _levels(signal: np.ndarray, rate: int) -> dict:
    """Return the level of every band, with the corpus's own ruler."""
    from corpus import measure

    return measure.band_levels_db(signal, rate, BANDS)


def _edge(signal: np.ndarray, frames: int) -> tuple[float, float]:
    """The level of the first ``frames`` samples and of the rest.

    The two are compared: a model whose memory is empty at the head of
    the file shows up as a difference between them.
    """
    if len(signal) <= frames:
        return (float("nan"), float("nan"))
    head = signal[:frames]
    rest = signal[frames:]
    head_rms = float(np.sqrt(np.mean(np.square(head))))
    rest_rms = float(np.sqrt(np.mean(np.square(rest))))
    return (head_rms, rest_rms)


def _peak_memory() -> int:
    """The peak working set of this process, in bytes.

    The offline path holds the frames of the whole file in memory, and a
    model that is asked once per sample has a frame for every sample: the
    peak is what decides how long a file the path can take.
    """
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Counters),
        ctypes.c_uint32,
    ]
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb
    )
    return int(counters.PeakWorkingSetSize) if ok else 0


def main(argv: list[str] | None = None) -> int:
    """Measure the cost, the quality and the head of the memory model."""
    parser = argparse.ArgumentParser(
        prog="measure_memory.py",
        description="Measure what the model with a memory costs and what it buys.",
    )
    parser.add_argument("--memo", type=Path, default=MEMO, help="the memo to measure")
    args = parser.parse_args(argv)

    if not args.memo.is_file():
        print(
            f"the memo is not there ({args.memo}); run qa/make_demo_memo.py first",
            file=sys.stderr,
        )
        return 2

    import soundfile as sf

    from aurcore import models

    signal, rate = sf.read(str(args.memo), always_2d=True)
    seconds = len(signal) / float(rate)
    print(
        f"the memo: {len(signal):,} frames, {rate} Hz, {signal.shape[1]} ch, {seconds:.2f} s"
    )

    before = _levels(signal, rate)
    print()
    print("what is wrong with it:")
    for name, level in before.items():
        print(f"   {name:<26} {level:>9.3f} dBFS")
    print(
        f"   the voice against the hum: "
        f"{before['the voice 120-3000 Hz'] - before['the hum 45-55 Hz']:+.2f} dB"
    )

    print()
    print("the models:")
    print(
        f"   {'the model':<24} {'the layout':>10} {'the cost':>10} {'x real time':>13} "
        f"{'the peak':>12}"
    )
    results: dict[str, dict] = {}
    for name in (FRAME_MODEL, SAMPLE_MODEL, MEMORY_MODEL):
        model = models.open_library().load(name)
        manifest = model.manifest
        layout = f"{manifest.input.frame_size}/{manifest.input.hop}"
        best = float("inf")
        for _ in range(2):
            start = time.perf_counter()
            out, _ = models.run_named(name, signal)
            best = min(best, time.perf_counter() - start)
        peak = _peak_memory()
        results[name] = {
            "out": out,
            "levels": _levels(out, rate),
            "cost": best,
            "peak": peak,
        }
        print(
            f"   {name:<24} {layout:>10} {best:>8.3f} s {best / seconds:>13.2f} "
            f"{peak / 1048576.0:>9.1f} MB"
        )

    print()
    print("the peak is the working set of the process, and a working set is a high")
    print("water mark: it does not go down, so the last row is the number that counts.")
    print("the offline path holds the frames of the whole file in memory, and a model")
    print("asked once per sample has a frame for every sample of the file.")
    print()
    print("what each model does to the material:")
    print(
        f"   {'the model':<24} {'the hum':>9} {'the voice':>9} {'the hiss':>9} "
        f"{'the rumble':>10}"
    )
    for name in (FRAME_MODEL, SAMPLE_MODEL, MEMORY_MODEL):
        after = results[name]["levels"]
        hum = after["the hum 45-55 Hz"] - before["the hum 45-55 Hz"]
        voice = after["the voice 120-3000 Hz"] - before["the voice 120-3000 Hz"]
        hiss = after["the hiss 5000-20000 Hz"] - before["the hiss 5000-20000 Hz"]
        rumble = after["the rumble 20-80 Hz"] - before["the rumble 20-80 Hz"]
        print(
            f"   {name:<24} {hum:>9.2f} {voice:>9.2f} {hiss:>9.2f} {rumble:>10.2f}"
        )

    print()
    print("the head of the file, where the memory is empty:")
    manifest = models.open_library().load(MEMORY_MODEL).manifest
    reach = manifest.context.frames
    for name in (SAMPLE_MODEL, MEMORY_MODEL):
        out = results[name]["out"]
        head_rms, rest_rms = _edge(out, reach)
        ratio = max(head_rms, 1e-12) / max(rest_rms, 1e-12)
        print(
            f"   {name:<24} the first {reach:>4} frames {head_rms:>9.5f}  "
            f"the rest {rest_rms:>9.5f}  "
            f"the difference {20.0 * np.log10(ratio):>+7.2f} dB"
        )

    print()
    print("the cost of the offline budget is 2.0 times the real time;")
    print(
        f"   the model with a memory takes "
        f"{results[MEMORY_MODEL]['cost'] / seconds:.2f} times the real time"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
