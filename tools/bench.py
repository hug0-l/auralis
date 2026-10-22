#!/usr/bin/env python3
"""
Auralis benchmark CLI.

Renders a deterministic test signal through a module (or through every
registered module) and reports how fast the core runs:

* the **realtime factor** - how many times faster than realtime the module
  processed the signal (``realtime_factor = audio_seconds / wall_seconds``;
  ``1.0`` means exactly realtime, ``80.0`` means eighty times faster).  The
  DSP-convention counterpart is ``rtf = wall_seconds / audio_seconds``
  (``rtf < 1`` is faster than realtime);
* the **CPU load** - the process CPU time spent inside the module relative to
  the wall clock, in percent;
* the **peak resident set size** of the process, in MiB;
* the **latency** the module reports in samples, plus the measured per-block
  processing time (median, p95 and maximum over all blocks).

Examples
--------
    python tools/bench.py --module AUR_DEHUM --seconds 5
    python tools/bench.py --all
    python tools/bench.py --module AUR_DECLICK --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aurcore import registry  # noqa: E402  (path setup has to come first)

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 2
DEFAULT_BLOCK = 1024
SIGNAL_SEED = 20_260_101


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _import_modules() -> str | None:
    """Import the module tree; returns the first import error, if any."""
    try:
        import aurcore.modules  # noqa: F401  (importing registers the modules)
    except Exception as exc:  # noqa: BLE001 - a broken file must not kill the run
        return f"{type(exc).__name__}: {exc}"
    return None


def _peak_rss_bytes() -> int | None:
    """Peak resident set size of this process in bytes (None when unknown)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
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

            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.GetCurrentProcess()
            handle = ctypes.c_void_p(handle)

            candidates = []
            try:
                candidates.append(ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo)
            except OSError:
                pass
            try:
                candidates.append(kernel32.K32GetProcessMemoryInfo)
            except AttributeError:
                pass

            for func in candidates:
                func.restype = ctypes.c_int
                func.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(_Counters),
                    ctypes.c_ulong,
                ]
                if func(handle, ctypes.byref(counters), counters.cb):
                    return int(counters.PeakWorkingSetSize)
        except Exception:  # noqa: BLE001 - the metric is optional
            return None
        return None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS reports bytes.
        return int(usage) * (1024 if sys.platform.startswith("linux") else 1)
    except Exception:  # noqa: BLE001 - the metric is optional
        return None


def _test_signal(seconds: float, sample_rate: int, channels: int) -> np.ndarray:
    """A deterministic stereo test signal: noise bed plus three tones.

    The signal sits at -12 dBFS so that a module has real work to do without
    running into the clip detector.
    """
    n = max(1, int(round(seconds * sample_rate)))
    rng = np.random.default_rng(SIGNAL_SEED)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)

    signal = 0.06 * rng.standard_normal(n)
    for freq, amp in ((110.0, 0.20), (440.0, 0.12), (3000.0, 0.06)):
        signal += amp * np.sin(2.0 * np.pi * freq * t)

    peak = float(np.max(np.abs(signal)))
    if peak > 0.0:
        signal = signal / peak * 0.25  # -12 dBFS
    return np.repeat(signal[:, None], channels, axis=1)


def _percentile(values: list[float], q: float) -> float:
    """The given percentile of a list of numbers."""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


# --------------------------------------------------------------------------
# Benchmark
# --------------------------------------------------------------------------
def bench_module(
    code: str,
    seconds: float = 5.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    block: int = DEFAULT_BLOCK,
) -> dict[str, Any]:
    """Benchmark one module and return a machine-readable record."""
    record: dict[str, Any] = {
        "code": code,
        "ok": False,
        "error": None,
        "display_name": code,
        "category": "",
        "audio_seconds": 0.0,
        "wall_seconds": 0.0,
        "cpu_seconds": 0.0,
        "realtime_factor": 0.0,
        "rtf": 0.0,
        "cpu_percent": 0.0,
        "peak_rss_bytes": None,
        "latency_samples": 0,
        "blocks": 0,
        "block_ms_median": 0.0,
        "block_ms_p95": 0.0,
        "block_ms_max": 0.0,
    }

    try:
        module = registry.create(code, sample_rate, channels)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    cls = type(module)
    record["display_name"] = getattr(cls, "display_name", code)
    record["category"] = getattr(cls, "category", "")
    record["latency_samples"] = int(getattr(module, "latency_samples", 0))

    signal = _test_signal(seconds, sample_rate, channels)
    block_times: list[float] = []
    try:
        wall0 = time.perf_counter()
        cpu0 = time.process_time()
        for start in range(0, len(signal), block):
            chunk = signal[start : start + block]
            t0 = time.perf_counter()
            out = module.process(chunk)
            block_times.append((time.perf_counter() - t0) * 1000.0)
            if out is None:
                raise RuntimeError("the module returned nothing")
        wall = time.perf_counter() - wall0
        cpu = time.process_time() - cpu0
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    audio_seconds = len(signal) / float(sample_rate)
    record.update(
        {
            "ok": True,
            "audio_seconds": audio_seconds,
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            # realtime factor: how many times faster than realtime we ran
            "realtime_factor": (audio_seconds / wall) if wall > 0 else 0.0,
            # DSP convention: processing time per audio second (< 1 is faster)
            "rtf": (wall / audio_seconds) if audio_seconds > 0 else 0.0,
            "cpu_percent": (cpu / wall * 100.0) if wall > 0 else 0.0,
            "peak_rss_bytes": _peak_rss_bytes(),
            "blocks": len(block_times),
            "block_ms_median": _percentile(block_times, 50.0),
            "block_ms_p95": _percentile(block_times, 95.0),
            "block_ms_max": max(block_times) if block_times else 0.0,
        }
    )
    return record


def bench_all(
    seconds: float = 5.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    block: int = DEFAULT_BLOCK,
) -> list[dict[str, Any]]:
    """Benchmark every registered module."""
    results = []
    for info in registry.all():
        results.append(
            bench_module(info.code, seconds, sample_rate, channels, block)
        )
    return results


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def _format_table(results: list[dict[str, Any]]) -> str:
    """Render the benchmark results as a text table."""
    header = (
        f"{'module':<22} {'name':<22} {'rt factor':>10} {'rtf':>8} "
        f"{'cpu %':>8} {'lat(smp)':>9} {'p95 ms':>8} {'max ms':>8} {'rss MiB':>8}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        if not r["ok"]:
            lines.append(f"{r['code']:<22} FAILED: {r['error']}")
            continue
        rss = (r["peak_rss_bytes"] / (1024.0 * 1024.0)) if r["peak_rss_bytes"] else float("nan")
        lines.append(
            f"{r['code']:<22} {r['display_name'][:22]:<22} {r['realtime_factor']:>10.2f} "
            f"{r['rtf']:>8.3f} {r['cpu_percent']:>8.1f} {r['latency_samples']:>9d} "
            f"{r['block_ms_p95']:>8.3f} {r['block_ms_max']:>8.3f} {rss:>8.1f}"
        )
    return "\n".join(lines)


def _print_single(result: dict[str, Any], block: int, sample_rate: int, channels: int) -> None:
    """Print the benchmark result of one module."""
    print(f"module          : {result['code']} ({result['display_name']})")
    print(f"category        : {result['category']}")
    print(f"signal          : {result['audio_seconds']:.2f} s, {sample_rate} Hz, {channels} ch, "
          f"block {block}")
    if not result["ok"]:
        print(f"error           : {result['error']}")
        return
    rss = (
        (result["peak_rss_bytes"] / (1024.0 * 1024.0))
        if result["peak_rss_bytes"]
        else float("nan")
    )
    print(f"wall time       : {result['wall_seconds']:.3f} s")
    print(f"cpu time        : {result['cpu_seconds']:.3f} s")
    print(f"realtime factor : {result['realtime_factor']:.2f} x realtime (audio seconds per wall "
          f"second)")
    print(f"rtf             : {result['rtf']:.4f} (wall seconds per audio second, < 1 is faster)")
    print(f"cpu load        : {result['cpu_percent']:.1f} %")
    print(f"peak rss        : {rss:.1f} MiB")
    print(f"latency         : {result['latency_samples']} samples")
    print(f"blocks          : {result['blocks']}")
    print(
        f"block time      : median {result['block_ms_median']:.3f} ms, "
        f"p95 {result['block_ms_p95']:.3f} ms, max {result['block_ms_max']:.3f} ms"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description="Benchmark the Auralis DSP core.",
    )
    parser.add_argument("--module", "-m", help="module code to benchmark (e.g. AUR_DEHUM)")
    parser.add_argument("--all", "-a", action="store_true",
                        help="benchmark every registered module")
    parser.add_argument("--seconds", "-s", type=float, default=5.0,
                        help="length of the test signal")
    parser.add_argument("--block", "-b", type=int, default=DEFAULT_BLOCK,
                        help="processing block size")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
                        help="sample rate in Hz")
    parser.add_argument("--channels", type=int, default=DEFAULT_CHANNELS, help="channel count")
    parser.add_argument("--json", dest="json_path", help="write the results to this JSON file")
    parser.add_argument("--list", action="store_true", help="list the registered modules and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the benchmark CLI."""
    args = build_parser().parse_args(argv)

    import_error = _import_modules()

    if args.list:
        for info in registry.all():
            print(f"{info.code:<22} {info.category:<12} {info.display_name}")
        return 0

    if not args.module and not args.all:
        print("nothing to do: pass --module CODE, --all or --list", file=sys.stderr)
        return 2

    if args.module and not registry.has(args.module):
        print(f"unknown module {args.module!r}", file=sys.stderr)
        return 2

    if import_error:
        print(f"warning: the module tree reported an import error: {import_error}", file=sys.stderr)

    if args.all:
        results = bench_all(args.seconds, args.sample_rate, args.channels, args.block)
        print(_format_table(results))
    else:
        results = [
            bench_module(args.module, args.seconds, args.sample_rate, args.channels, args.block)
        ]
        _print_single(results[0], args.block, args.sample_rate, args.channels)

    if args.json_path:
        payload = {
            "tool": "auralis.bench",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sample_rate": args.sample_rate,
            "channels": args.channels,
            "block": args.block,
            "results": results,
        }
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {target}")

    failed = [r for r in results if not r["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
