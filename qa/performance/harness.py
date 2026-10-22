"""
qa.performance.harness — the real-time performance harness of the Auralis core.

The harness answers the one question a plug-in has to answer before it ships:
**can the DSP core process a block of audio faster than real time?**

Everything in here is measurement only — it never touches ``aurcore`` or
``aurcore.dsp``.  The modules are driven exactly the way a plug-in host drives
them: one block in, one block out.

The four numbers that matter per module and per block size are:

``time_us``
    Median wall-clock time of one ``process()`` call, in microseconds.
``budget_fraction``
    ``time_us / block_budget_us`` — the share of the realtime budget the
    module eats.  ``1.0`` means the module already misses the deadline.
``peak_rss_bytes``
    Peak resident set size of the process (measured in a dedicated child
    process, so the number belongs to the module and not to the harness).
``arrays_per_block``
    How many NumPy arrays the module creates inside ``process()``.  A
    realtime-safe module creates none once it is warmed up.

Run it with::

    python qa/performance/run_perf.py --all
    python qa/performance/run_perf.py --module AUR_DECLICK --profile
"""

from __future__ import annotations

import cProfile
import functools
import io
import os
import platform
import pstats
import sys
import threading
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The sample rate every measurement runs at.
SAMPLE_RATE = 48_000

#: The channel count every measurement runs at.
CHANNELS = 2

#: The block sizes a plug-in host realistically uses.
BLOCK_SIZES: tuple[int, ...] = (64, 128, 256, 512)

#: Seed of the deterministic test signal.
SIGNAL_SEED = 20_260_101

#: Number of timed blocks per measurement.
DEFAULT_REPEATS = 15

#: Number of untimed blocks used to warm the module up before measuring.
DEFAULT_WARMUP = 3

__all__ = [
    "BLOCK_SIZES",
    "CHANNELS",
    "DEFAULT_REPEATS",
    "DEFAULT_WARMUP",
    "SAMPLE_RATE",
    "SIGNAL_SEED",
    "AllocationStats",
    "BlockTiming",
    "Calibration",
    "ChainMeasurement",
    "LoadMonitor",
    "ModuleMeasurement",
    "NumpyAllocationCounter",
    "block_budget_us",
    "calibrate",
    "fastest_transform_us",
    "machine_load_percent",
    "make_test_signal",
    "measure_all_modules",
    "measure_allocations",
    "measure_chain",
    "measure_chain_calibrated",
    "measure_chain_runs",
    "measure_module",
    "measure_module_calibrated",
    "measure_module_runs",
    "measure_transform_us",
    "merge_chain_measurements",
    "merge_measurements",
    "peak_rss_bytes",
    "profile_module",
    "reference_transform_us",
    "render_chain_table",
    "render_table",
    "reference_fft_us",
    "summarise",
    "transform_us_samples",
]


# ---------------------------------------------------------------------------
# Budget arithmetic
# ---------------------------------------------------------------------------
def block_budget_us(block: int, sample_rate: int = SAMPLE_RATE) -> float:
    """Return the realtime budget of one block, in microseconds.

    A block of ``block`` samples has to be processed in the time it takes to
    play it, i.e. ``block / sample_rate`` seconds.  At 48 kHz a 64 sample
    block therefore has a budget of 1333.3 us.

    Parameters
    ----------
    block:
        Block size in samples.
    sample_rate:
        Sample rate in Hz.

    Returns
    -------
    float
        The budget in microseconds.
    """
    return (float(block) / float(sample_rate)) * 1_000_000.0


# ---------------------------------------------------------------------------
# Machine load
# ---------------------------------------------------------------------------
def _cpu_times() -> tuple[int, int, int] | None:
    """Return the ``(idle, kernel, user)`` CPU times of the machine.

    The unit is the 100 ns tick of the Windows system clock.  ``None`` is
    returned when the platform does not expose the counters.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _FileTime(ctypes.Structure):
            """The layout of ``FILETIME``."""

            _fields_ = [
                ("dwLowDateTime", ctypes.c_ulong),
                ("dwHighDateTime", ctypes.c_ulong),
            ]

        def _value(ft: "_FileTime") -> int:
            return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        ok = ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            return None
        return _value(idle), _value(kernel), _value(user)
    except Exception:  # noqa: BLE001 - the metric is optional
        return None


def machine_load_percent(interval: float = 0.25) -> float:
    """Return the CPU utilisation of the machine, in percent.

    The measurement is a delta over ``interval`` seconds, so it is the
    share of the CPU that was *busy* while the window was open.  A machine
    that is shared with other work reports a high number here, and every
    timing taken on it is inflated by that much.

    Parameters
    ----------
    interval:
        The length of the sampling window, in seconds.

    Returns
    -------
    float
        The load in percent, or ``nan`` when the platform does not expose
        the counters.
    """
    if sys.platform == "win32":
        first = _cpu_times()
        if first is None:
            return float("nan")
        time.sleep(max(0.01, float(interval)))
        second = _cpu_times()
        if second is None:
            return float("nan")
        idle = second[0] - first[0]
        total = (second[1] - first[1]) + (second[2] - first[2])
        if total <= 0:
            return float("nan")
        return 100.0 * (total - idle) / float(total)
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):  # pragma: no cover - platform specific
        return float("nan")
    cpus = os.cpu_count() or 1
    return 100.0 * min(100.0, (load / float(cpus)) * 100.0)


class LoadMonitor:
    """Sample the machine load in the background while work is running.

    The monitor answers the question a timing cannot answer on its own:
    *how busy was the machine while the number was taken?*  A timing taken
    on a machine that ran at 100 % is not comparable with a timing taken on
    an idle one, which is why every measurement in this directory is
    reported together with the load.

    Example
    -------
    >>> with LoadMonitor() as monitor:
    ...     pass
    >>> monitor.mean_percent >= 0.0
    True
    """

    def __init__(self, interval: float = 0.25) -> None:
        """Create the monitor; nothing is sampled until it is entered."""
        self.interval = max(0.01, float(interval))
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            value = machine_load_percent(self.interval)
            if value == value:  # not nan
                self.samples.append(value)
            self._stop.wait(self.interval)

    def __enter__(self) -> "LoadMonitor":
        """Start sampling."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Stop sampling."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def mean_percent(self) -> float:
        """The mean load, in percent (``nan`` when nothing was sampled)."""
        if not self.samples:
            return float("nan")
        return float(sum(self.samples) / len(self.samples))

    @property
    def max_percent(self) -> float:
        """The peak load, in percent (``nan`` when nothing was sampled)."""
        if not self.samples:
            return float("nan")
        return float(max(self.samples))


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
@dataclass
class Calibration:
    """The machine yardstick that makes a timing portable.

    A wall clock number is only meaningful next to the machine it was
    taken on.  The calibration measures how long this machine needs for
    one 1024 point FFT, which is the unit the DSP core spends most of its
    time in.  Dividing a timing by the calibration turns it into a
    machine independent number: "how many transforms is this worth".

    Attributes
    ----------
    fft_us:
        The cost of one 1024 point transform on this machine, in
        microseconds (the fastest of ``runs`` runs).
    fft_us_slowest:
        The cost of the slowest run, in microseconds.  The spread between
        the two is the noise of the measurement.
    runs:
        How many runs the calibration took.
    repeats:
        How many transforms were timed per run.
    size:
        The transform size.
    load_percent:
        The machine load while the calibration ran.
    """

    fft_us: float = 0.0
    fft_us_slowest: float = 0.0
    runs: int = 0
    repeats: int = 0
    size: int = 1024
    load_percent: float = float("nan")

    @property
    def spread(self) -> float:
        """The relative spread between the fastest and the slowest run."""
        if self.fft_us <= 0:
            return 0.0
        return (self.fft_us_slowest - self.fft_us) / self.fft_us

    def to_reference(self, measured_us: float, reference_fft_us: float) -> float:
        """Return what ``measured_us`` would cost on the reference machine.

        Parameters
        ----------
        measured_us:
            The timing taken on *this* machine.
        reference_fft_us:
            The transform cost of the machine the budgets were written on.

        Returns
        -------
        float
            The timing expressed in reference machine microseconds.
        """
        if self.fft_us <= 0 or reference_fft_us <= 0:
            return float(measured_us)
        return float(measured_us) * (float(reference_fft_us) / self.fft_us)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "fft_us": round(self.fft_us, 3),
            "fft_us_slowest": round(self.fft_us_slowest, 3),
            "spread": round(self.spread, 4),
            "runs": self.runs,
            "repeats": self.repeats,
            "size": self.size,
            "load_percent": round(self.load_percent, 1),
        }


def calibrate(
    runs: int = 5,
    repeats: int = 200,
    size: int = 1024,
    load_interval: float = 0.2,
) -> Calibration:
    """Calibrate the machine: how long does one transform cost here?

    The fastest of ``runs`` runs is used: the fastest run is the one that
    was least disturbed by the other work on a shared machine, so it is the
    best estimate of what the machine can do.

    Parameters
    ----------
    runs:
        How many times the transform is timed.
    repeats:
        How many transforms are timed per run.
    size:
        The transform size.
    load_interval:
        The length of the load sampling window, in seconds.

    Returns
    -------
    Calibration
        The calibration of this machine.
    """
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)  # warm the FFT plan up
    per_run: list[float] = []
    with LoadMonitor(interval=load_interval) as monitor:
        for _ in range(max(1, int(runs))):
            times: list[float] = []
            for _ in range(max(1, int(repeats))):
                start = time.perf_counter_ns()
                np.fft.rfft(data)
                times.append((time.perf_counter_ns() - start) / 1000.0)
            per_run.append(_percentile(times, 50.0))
    load = monitor.mean_percent
    return Calibration(
        fft_us=min(per_run) if per_run else 0.0,
        fft_us_slowest=max(per_run) if per_run else 0.0,
        runs=len(per_run),
        repeats=max(1, int(repeats)),
        size=int(size),
        load_percent=load,
    )


# ---------------------------------------------------------------------------
# Test signal
# ---------------------------------------------------------------------------
def make_test_signal(
    n: int,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    seed: int = SIGNAL_SEED,
) -> np.ndarray:
    """Build the deterministic test signal, shape ``(n, channels)``.

    The signal is speech-like: a noise bed, a harmonic stack with a formant
    envelope and a couple of impulsive clicks.  The clicks matter — without
    them the transient repair modules would find nothing to do and would look
    far cheaper than they are in production.

    Parameters
    ----------
    n:
        Number of samples.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    seed:
        RNG seed; the default makes the signal reproducible.

    Returns
    -------
    numpy.ndarray
        The signal, ``float64``, shape ``(n, channels)``.
    """
    n = max(1, int(n))
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)

    signal = 0.02 * rng.standard_normal(n)
    # A harmonic stack with a formant-like envelope.
    for harmonic, amp in ((1, 0.30), (2, 0.18), (3, 0.11), (5, 0.06), (8, 0.03)):
        signal += amp * np.sin(2.0 * np.pi * 118.0 * harmonic * t)
    # A little air on top so the high band is not empty.
    signal += 0.02 * np.sin(2.0 * np.pi * 2600.0 * t)
    # Two clicks.
    for pos in (n // 3, (2 * n) // 3):
        if 0 <= pos < n:
            signal[pos] += 0.85
            signal[pos + 1 if pos + 1 < n else pos] -= 0.4

    peak = float(np.max(np.abs(signal)))
    if peak > 0.0:
        signal = signal / peak * 0.25  # -12 dBFS
    return np.repeat(signal[:, None], channels, axis=1)


# ---------------------------------------------------------------------------
# Process memory
# ---------------------------------------------------------------------------
def peak_rss_bytes() -> int | None:
    """Return the peak resident set size of this process, in bytes.

    Returns ``None`` when the platform does not expose the number.  On
    Windows the value comes from ``GetProcessMemoryInfo`` (peak working set),
    on Linux and macOS from ``getrusage(RUSAGE_SELF).ru_maxrss``.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                """The layout of ``PROCESS_MEMORY_COUNTERS``."""

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
            handle = ctypes.c_void_p(kernel32.GetCurrentProcess())

            candidates = []
            try:
                candidates.append(
                    ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
                )
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


# ---------------------------------------------------------------------------
# Allocation counting
# ---------------------------------------------------------------------------
#: NumPy entry points that hand out a new array.  They are wrapped while the
#: counter is active, so every call that produces an array is counted.
_COUNTED_NUMPY_NAMES: tuple[str, ...] = (
    "array",
    "asarray",
    "asanyarray",
    "ascontiguousarray",
    "asfortranarray",
    "copy",
    "concatenate",
    "stack",
    "vstack",
    "hstack",
    "dstack",
    "column_stack",
    "row_stack",
    "zeros",
    "ones",
    "empty",
    "full",
    "arange",
    "linspace",
    "logspace",
    "eye",
    "identity",
    "pad",
    "repeat",
    "tile",
    "diff",
    "cumsum",
    "interp",
    "convolve",
    "correlate",
    "median",
    "mean",
    "sum",
    "std",
    "var",
    "percentile",
    "quantile",
    "sort",
    "argsort",
    "where",
    "nonzero",
    "unique",
    "flip",
    "roll",
    "resize",
    "outer",
    "inner",
    "dot",
    "matmul",
    "einsum",
    "tensordot",
    "abs",
    "absolute",
    "sqrt",
    "exp",
    "expm1",
    "log",
    "log2",
    "log10",
    "sin",
    "cos",
    "tan",
    "arctan",
    "arctan2",
    "sinh",
    "cosh",
    "tanh",
    "sign",
    "floor",
    "ceil",
    "round",
    "rint",
    "maximum",
    "minimum",
    "power",
    "fmod",
    "mod",
    "hypot",
    "clip",
    "cumprod",
    "trapz",
)


class _CountedCallable:
    """A callable proxy that counts the arrays its target hands out.

    The proxy forwards every attribute lookup to the wrapped object, so a
    ufunc keeps its ``reduce`` / ``outer`` / ``at`` methods and NumPy's own
    internals keep working while the counter is active.
    """

    def __init__(self, func: Callable[..., Any], counter: "NumpyAllocationCounter") -> None:
        """Wrap ``func`` and count its array results through ``counter``."""
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_counter", counter)
        try:
            functools.update_wrapper(self, func)
        except (AttributeError, TypeError):  # pragma: no cover - exotic callables
            pass

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the wrapped object and count a new array when there is one."""
        result = self._func(*args, **kwargs)
        counter = self._counter
        if isinstance(result, np.ndarray) and "out" not in kwargs:
            counter.count += 1
            name = getattr(self._func, "__name__", repr(self._func))
            counter.top.append((int(result.nbytes), str(name)))
            # A view shares its buffer with an input: it allocates an array
            # object but no memory, so only owned data counts as bytes.
            owns_data = result.base is None and not any(a is result for a in args)
            if owns_data:
                counter.bytes += int(result.nbytes)
        return result

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the wrapped object."""
        return getattr(object.__getattribute__(self, "_func"), name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Readable representation."""
        return f"<counted {getattr(self._func, '__name__', self._func)!r}>"


class NumpyAllocationCounter:
    """Count the NumPy arrays a code region creates.

    NumPy has no public allocation counter, so the counter wraps the NumPy
    entry points that hand out arrays (constructors and ufuncs) and counts
    every call that returns one.  Views count as well: they allocate an
    array object even when they share their buffer.

    The counter is a *measurement* tool — it changes the cost of the code it
    measures, so it must never be active while a timing pass runs.

    Example
    -------
    >>> with NumpyAllocationCounter() as counter:
    ...     _ = np.zeros((16, 2))
    ...     _ = np.zeros((16, 2)) + 1.0
    >>> counter.count > 0
    True
    """

    def __init__(self) -> None:
        """Create the counter (nothing is patched yet)."""
        self.count = 0
        self.bytes = 0
        self.top: list[tuple[int, str]] = []
        self._saved: list[tuple[Any, str, Any]] = []

    # -- numpy patching -------------------------------------------------------
    def _wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        return _CountedCallable(func, self)

    def _patch(self, module: Any, names: Iterable[str]) -> None:
        for name in names:
            # ``vars`` instead of ``getattr``: NumPy resolves unknown names
            # lazily and that import can fail while the module is patched.
            original = vars(module).get(name)
            if original is None or not callable(original):
                continue
            if getattr(original, "__auralis_counted__", False):
                continue
            wrapper = self._wrap(original)
            self._saved.append((module, name, original))
            setattr(module, name, wrapper)

    def _patch_ufuncs(self) -> None:
        for name, original in list(vars(np).items()):
            if name.startswith("_"):
                continue
            if isinstance(original, np.ufunc):
                self._patch(np, (name,))

    def _unpatch(self) -> None:
        for module, name, original in reversed(self._saved):
            setattr(module, name, original)
        self._saved.clear()

    # -- context manager ------------------------------------------------------
    def __enter__(self) -> "NumpyAllocationCounter":
        """Start counting."""
        self._patch(np, _COUNTED_NUMPY_NAMES)
        self._patch_ufuncs()
        try:
            import numpy.fft as _fft

            self._patch(_fft, ("fft", "ifft", "rfft", "irfft", "fftfreq", "rfftfreq", "hfft"))
        except Exception:  # noqa: BLE001 - the FFT module is always there
            pass
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Stop counting and restore NumPy."""
        self._unpatch()

    # -- reporting ------------------------------------------------------------
    def report(self, blocks: int) -> "AllocationStats":
        """Return the statistics for ``blocks`` measured blocks."""
        blocks = max(1, int(blocks))
        return AllocationStats(
            arrays_per_block=self.count / blocks,
            bytes_per_block=self.bytes / blocks,
            total_arrays=self.count,
            total_bytes=self.bytes,
        )


@dataclass
class AllocationStats:
    """What a module allocates while it processes audio.

    Attributes
    ----------
    arrays_per_block:
        NumPy array objects created per processed block (views included).
    bytes_per_block:
        Bytes of *new* array data per processed block (views excluded).
    total_arrays:
        Total number of arrays created over the measured run.
    total_bytes:
        Total number of new bytes created over the measured run.
    peak_bytes:
        Peak traced memory during a single block: the transient churn of one
        ``process()`` call.  This is the authoritative "does it allocate"
        number, because it also sees the arrays that operator expressions
        (and other calls the counter cannot see) create.
    retained_bytes:
        Memory that stayed alive after the run (a leak indicator).
    """

    arrays_per_block: float = 0.0
    bytes_per_block: float = 0.0
    total_arrays: int = 0
    total_bytes: int = 0
    peak_bytes: int = 0
    retained_bytes: int = 0

    @property
    def allocates(self) -> bool:
        """True when the module allocates memory inside ``process()``."""
        return self.peak_bytes > 0 or self.bytes_per_block > 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "arrays_per_block": round(self.arrays_per_block, 2),
            "bytes_per_block": round(self.bytes_per_block, 1),
            "total_arrays": self.total_arrays,
            "total_bytes": self.total_bytes,
            "peak_bytes": self.peak_bytes,
            "retained_bytes": self.retained_bytes,
            "allocates": self.allocates,
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class BlockTiming:
    """The timing of one module at one block size.

    Attributes
    ----------
    block:
        Block size in samples.
    budget_us:
        The realtime budget of one block, in microseconds.
    median_us:
        Median wall-clock time of one block, in microseconds.
    min_us:
        Fastest block, in microseconds.
    p95_us:
        95th percentile of the block times, in microseconds.
    max_us:
        Slowest block, in microseconds.
    output_samples:
        Number of samples the module returned for the last block.
    transforms:
        The cost of one block in 1024 point transforms — the calibrated,
        machine independent number the budgets are compared with.  It is
        the smallest ratio of a block time to the transform cost measured
        right next to it, over every run of the measurement.
    transform_us:
        The wall-clock cost of one 1024 point transform next to the block
        that produced :attr:`transforms` (the fastest observation).
    samples:
        The ratio of every round of the measurement.  The spread of these
        numbers is the noise of a shared machine, and it is what the
        margin of a budget has to cover.
    """

    block: int
    budget_us: float
    median_us: float
    min_us: float
    p95_us: float
    max_us: float
    output_samples: int = 0
    transforms: float = 0.0
    transform_us: float = 0.0
    samples: list[float] = field(default_factory=list)

    @property
    def sample_median(self) -> float:
        """The median of the rounds — the typical calibrated cost."""
        if not self.samples:
            return self.transforms
        return _percentile(self.samples, 50.0)

    @property
    def sample_spread(self) -> float:
        """How much the rounds of the measurement disagree (1.0 = stable)."""
        if len(self.samples) < 2:
            return 1.0
        low = min(self.samples)
        high = max(self.samples)
        return (high / low) if low > 0 else 1.0

    @property
    def budget_fraction(self) -> float:
        """Share of the realtime budget the median block time consumes."""
        return (self.median_us / self.budget_us) if self.budget_us > 0 else 0.0

    @property
    def headroom_us(self) -> float:
        """Microseconds left in the budget at the median block time."""
        return self.budget_us - self.median_us

    @property
    def over_budget(self) -> bool:
        """True when the median block time does not fit into the budget."""
        return self.median_us > self.budget_us

    def reference_us(self, reference_transform_us: float) -> float:
        """Return the cost of one block in reference machine microseconds.

        Parameters
        ----------
        reference_transform_us:
            The cost of one transform on the machine the budgets were
            written on.

        Returns
        -------
        float
            The block cost expressed in the microseconds of the reference
            machine.  This is the number the budgets are compared with, so
            the verdict does not depend on how busy the machine is.
        """
        return float(self.transforms) * float(reference_transform_us)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "block": self.block,
            "budget_us": round(self.budget_us, 2),
            "median_us": round(self.median_us, 2),
            "min_us": round(self.min_us, 2),
            "p95_us": round(self.p95_us, 2),
            "max_us": round(self.max_us, 2),
            "budget_fraction": round(self.budget_fraction, 4),
            "headroom_us": round(self.headroom_us, 2),
            "transform_us": round(self.transform_us, 3),
            "transforms": round(self.transforms, 3),
            "transforms_median": round(self.sample_median, 3),
            "transforms_spread": round(self.sample_spread, 3),
            "transforms_samples": [round(float(v), 3) for v in self.samples],
            "output_samples": self.output_samples,
        }


@dataclass
class ModuleMeasurement:
    """Everything measured about one module.

    Attributes
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    display_name:
        The human readable name of the module.
    category:
        The module category.
    timings:
        One :class:`BlockTiming` per measured block size.
    allocations:
        The allocation statistics of the module.
    peak_rss_bytes:
        Peak resident set size of a process that only ran this module.
    error:
        The error message when the module could not be measured.
    """

    code: str
    display_name: str = ""
    category: str = ""
    timings: list[BlockTiming] = field(default_factory=list)
    allocations: AllocationStats = field(default_factory=AllocationStats)
    peak_rss_bytes: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the module could be measured."""
        return self.error is None

    def timing_for(self, block: int) -> BlockTiming | None:
        """Return the timing of one block size, or ``None``."""
        for timing in self.timings:
            if timing.block == block:
                return timing
        return None

    @property
    def worst(self) -> BlockTiming | None:
        """Return the timing with the largest budget share."""
        if not self.timings:
            return None
        return max(self.timings, key=lambda t: t.budget_fraction)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "code": self.code,
            "display_name": self.display_name,
            "category": self.category,
            "ok": self.ok,
            "error": self.error,
            "timings": [t.to_dict() for t in self.timings],
            "allocations": self.allocations.to_dict(),
            "peak_rss_bytes": self.peak_rss_bytes,
        }


@dataclass
class ChainMeasurement:
    """The measurement of a chain of modules.

    Attributes
    ----------
    codes:
        The module codes of the chain, in signal order.
    timings:
        One :class:`BlockTiming` per measured block size.
    allocations:
        The allocation statistics of the chain.
    peak_rss_bytes:
        Peak resident set size of a process that only ran this chain.
    error:
        The error message when the chain could not be measured.
    """

    codes: list[str] = field(default_factory=list)
    timings: list[BlockTiming] = field(default_factory=list)
    allocations: AllocationStats = field(default_factory=AllocationStats)
    peak_rss_bytes: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the chain could be measured."""
        return self.error is None

    def timing_for(self, block: int) -> BlockTiming | None:
        """Return the timing of one block size, or ``None``."""
        for timing in self.timings:
            if timing.block == block:
                return timing
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "codes": list(self.codes),
            "ok": self.ok,
            "error": self.error,
            "timings": [t.to_dict() for t in self.timings],
            "allocations": self.allocations.to_dict(),
            "peak_rss_bytes": self.peak_rss_bytes,
        }


# ---------------------------------------------------------------------------
# The measurement core
# ---------------------------------------------------------------------------
def _time_blocks(
    process: Callable[[np.ndarray], np.ndarray],
    template: np.ndarray,
    repeats: int,
    warmup: int,
) -> tuple[list[float], int]:
    """Time ``repeats`` blocks and return the times in microseconds.

    The warm-up blocks are not measured: they pay for the one-off setup of a
    module (filter design, kernel tables, FFT plans), which does not happen
    again while the plug-in is running.

    Every call gets its own copy of the input, so a module that works in
    place cannot make the next call cheaper (or different).  The copies are
    made outside the timed region.
    """
    times: list[float] = []
    output_samples = 0
    total = max(0, warmup) + max(1, repeats)
    blocks = [template.copy() for _ in range(total)]
    for index, block in enumerate(blocks):
        start = time.perf_counter_ns()
        out = process(block)
        elapsed = time.perf_counter_ns() - start
        if index >= warmup:
            times.append(elapsed / 1000.0)
        if out is not None and hasattr(out, "shape"):
            output_samples = int(out.shape[0])
    return times, output_samples


def _percentile(values: Sequence[float], q: float) -> float:
    """Return the ``q``-th percentile of ``values``."""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def _allocation_pass(
    process: Callable[[np.ndarray], np.ndarray],
    template: np.ndarray,
    repeats: int,
    warmup: int,
) -> AllocationStats:
    """Measure what the code path allocates per block.

    Two passes are made, because the two instruments interfere with each
    other: the NumPy counter wraps the NumPy entry points (and therefore
    allocates its own bookkeeping, about 270 KiB per block), and
    ``tracemalloc`` slows the code down.  The first pass measures the
    *Python* churn of a single block with ``tracemalloc`` alone; the
    second counts the NumPy arrays the code creates with the counter
    alone.  Measuring both at once would charge the counter's own
    bookkeeping to the module.

    Parameters
    ----------
    process:
        The function to measure.
    template:
        The block to feed it.
    repeats:
        Number of measured blocks.
    warmup:
        Number of untimed blocks before the measurement starts.

    Returns
    -------
    AllocationStats
        The allocation statistics.
    """
    repeats = max(1, int(repeats))
    warm = max(0, int(warmup))
    blocks = [template.copy() for _ in range(warm + repeats)]
    for block in blocks[:warm]:
        process(block)

    # -- pass one: the Python level churn of a single block
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start(1)
    before_current = 0
    peak_memory = 0
    retained = 0
    try:
        before_current, _ = tracemalloc.get_traced_memory()
        for block in blocks[warm:]:
            tracemalloc.reset_peak()
            process(block)
            peak, _ = tracemalloc.get_traced_memory()
            peak_memory = max(peak_memory, peak)
        after_current, _ = tracemalloc.get_traced_memory()
        retained = int(after_current - before_current)
    finally:
        if not already_tracing:
            tracemalloc.stop()

    # -- pass two: the NumPy arrays the code creates
    with NumpyAllocationCounter() as counter:
        for block in blocks[warm:]:
            process(block)

    stats = counter.report(repeats)
    stats.peak_bytes = int(max(0, peak_memory - before_current))
    stats.retained_bytes = retained
    return stats


def measure_module(
    code: str,
    block_sizes: Iterable[int] = BLOCK_SIZES,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    signal: np.ndarray | None = None,
    measure_allocations: bool = True,
) -> ModuleMeasurement:
    """Measure one module across the given block sizes.

    Parameters
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    block_sizes:
        The block sizes to measure.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    repeats:
        Number of timed blocks per block size.
    warmup:
        Number of untimed blocks before the timed ones.
    signal:
        The signal to feed; a deterministic one is built when omitted.
    measure_allocations:
        Run the (slow) allocation pass as well.

    Returns
    -------
    ModuleMeasurement
        The measurement; ``error`` is set when the module failed.
    """
    from aurcore import registry  # local import: keeps the harness importable

    measurement = ModuleMeasurement(code=code)
    cls = registry.get(code)
    measurement.display_name = str(getattr(cls, "display_name", code))
    measurement.category = str(getattr(cls, "category", ""))

    try:
        module = cls(sample_rate, channels)  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        measurement.error = f"{type(exc).__name__}: {exc}"
        return measurement

    if signal is None:
        longest = max(int(b) for b in block_sizes) * 4
        signal = make_test_signal(longest, sample_rate, channels)

    for block_size in block_sizes:
        block = np.ascontiguousarray(signal[:block_size])
        try:
            times, output_samples = _time_blocks(
                module.process, block, repeats=repeats, warmup=warmup
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            measurement.error = f"{type(exc).__name__}: {exc}"
            return measurement
        measurement.timings.append(
            BlockTiming(
                block=int(block_size),
                budget_us=block_budget_us(int(block_size), sample_rate),
                median_us=_percentile(times, 50.0),
                min_us=min(times) if times else 0.0,
                p95_us=_percentile(times, 95.0),
                max_us=max(times) if times else 0.0,
                output_samples=output_samples,
            )
        )

    if measure_allocations:
        block = np.ascontiguousarray(signal[:max(int(b) for b in block_sizes)])
        try:
            measurement.allocations = _allocation_pass(
                module.process, block, repeats=repeats, warmup=warmup
            )
        except Exception:  # noqa: BLE001 - the allocation pass is optional
            measurement.allocations = AllocationStats()

    return measurement


def measure_module_runs(
    code: str,
    runs: int = 5,
    block_sizes: Iterable[int] = BLOCK_SIZES,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    signal: np.ndarray | None = None,
    measure_allocations: bool = False,
) -> ModuleMeasurement:
    """Measure one module several times and keep the fastest run.

    A wall clock measurement on a shared machine is not a measurement: the
    number is whatever the machine happened to be doing.  Measuring the
    module ``runs`` times and keeping the fastest run per block size (see
    :func:`merge_measurements`) removes the descheduling artefacts: the
    fastest run is the one that was least disturbed.

    Parameters
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    runs:
        How many times the module is measured.
    block_sizes:
        The block sizes to measure.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    repeats:
        Number of timed blocks per block size and run.
    warmup:
        Number of untimed blocks before the timed ones.
    signal:
        The signal to feed; a deterministic one is built when omitted.
    measure_allocations:
        Run the (slow) allocation pass as well.

    Returns
    -------
    ModuleMeasurement
        The merged measurement: the fastest run of every block size.
    """
    collected = [
        measure_module(
            code,
            block_sizes=block_sizes,
            sample_rate=sample_rate,
            channels=channels,
            repeats=repeats,
            warmup=warmup,
            signal=signal,
            measure_allocations=measure_allocations,
        )
        for _ in range(max(1, int(runs)))
    ]
    return merge_measurements(collected)


def measure_chain(
    codes: Sequence[str],
    block_sizes: Iterable[int] = BLOCK_SIZES,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    signal: np.ndarray | None = None,
    measure_allocations: bool = True,
) -> ChainMeasurement:
    """Measure a chain of modules across the given block sizes.

    The chain is driven through :class:`aurcore.api.Engine`, i.e. exactly the
    way the server and the plug-in host drive it.

    Parameters
    ----------
    codes:
        The module codes of the chain, in signal order.
    block_sizes:
        The block sizes to measure.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    repeats:
        Number of timed blocks per block size.
    warmup:
        Number of untimed blocks before the timed ones.
    signal:
        The signal to feed; a deterministic one is built when omitted.
    measure_allocations:
        Run the (slow) allocation pass as well.

    Returns
    -------
    ChainMeasurement
        The measurement; ``error`` is set when the chain failed.
    """
    from aurcore.api import create_engine

    measurement = ChainMeasurement(codes=list(codes))
    engine = create_engine(sample_rate, channels)
    try:
        for code in codes:
            engine.add_module(code)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        measurement.error = f"{type(exc).__name__}: {exc}"
        return measurement

    if signal is None:
        longest = max(int(b) for b in block_sizes) * 4
        signal = make_test_signal(longest, sample_rate, channels)

    for block_size in block_sizes:
        block = np.ascontiguousarray(signal[:block_size])
        try:
            times, output_samples = _time_blocks(
                engine.process, block, repeats=repeats, warmup=warmup
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            measurement.error = f"{type(exc).__name__}: {exc}"
            return measurement
        measurement.timings.append(
            BlockTiming(
                block=int(block_size),
                budget_us=block_budget_us(int(block_size), sample_rate),
                median_us=_percentile(times, 50.0),
                min_us=min(times) if times else 0.0,
                p95_us=_percentile(times, 95.0),
                max_us=max(times) if times else 0.0,
                output_samples=output_samples,
            )
        )

    if measure_allocations:
        block = np.ascontiguousarray(signal[:max(int(b) for b in block_sizes)])
        try:
            measurement.allocations = _allocation_pass(
                engine.process, block, repeats=repeats, warmup=warmup
            )
        except Exception:  # noqa: BLE001 - the allocation pass is optional
            measurement.allocations = AllocationStats()

    return measurement


def measure_chain_runs(
    codes: Sequence[str],
    runs: int = 5,
    block_sizes: Iterable[int] = BLOCK_SIZES,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    signal: np.ndarray | None = None,
    measure_allocations: bool = False,
) -> ChainMeasurement:
    """Measure a chain several times and keep the fastest run.

    Parameters
    ----------
    codes:
        The module codes of the chain, in signal order.
    runs:
        How many times the chain is measured.
    block_sizes:
        The block sizes to measure.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    repeats:
        Number of timed blocks per block size and run.
    warmup:
        Number of untimed blocks before the timed ones.
    signal:
        The signal to feed; a deterministic one is built when omitted.
    measure_allocations:
        Run the (slow) allocation pass as well.

    Returns
    -------
    ChainMeasurement
        The merged measurement: the fastest run of every block size.
    """
    collected = [
        measure_chain(
            codes,
            block_sizes=block_sizes,
            sample_rate=sample_rate,
            channels=channels,
            repeats=repeats,
            warmup=warmup,
            signal=signal,
            measure_allocations=measure_allocations,
        )
        for _ in range(max(1, int(runs)))
    ]
    return merge_chain_measurements(collected)


def measure_transform_us(
    runs: int = 3,
    repeats: int = 120,
    size: int = 1024,
) -> float:
    """Return the cost of one 1024 point transform on this machine.

    The fastest of ``runs`` runs is used: the fastest run is the one that
    was least disturbed by the other work on a shared machine, so it is
    the best estimate of what the machine can do.  This is the yardstick
    every timing is measured against.

    Parameters
    ----------
    runs:
        How many runs are timed.
    repeats:
        How many transforms are timed per run.
    size:
        The transform size.

    Returns
    -------
    float
        The cost of one transform, in microseconds.
    """
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)  # warm the plan up
    best = 0.0
    for _ in range(max(1, int(runs))):
        times: list[float] = []
        for _ in range(max(1, int(repeats))):
            start = time.perf_counter_ns()
            np.fft.rfft(data)
            times.append((time.perf_counter_ns() - start) / 1000.0)
        median = _percentile(times, 50.0)
        best = median if best <= 0.0 else min(best, median)
    return best


def fastest_transform_us(repeats: int = 20, size: int = 1024) -> float:
    """Return the cost of the fastest of ``repeats`` transforms.

    This is the yardstick of the guard.  It is deliberately the *fastest*
    observation of a batch of transforms and not the median: the guard
    compares the cost of a block with the cost of a transform measured
    right next to it, and the fastest transform of a batch is the one
    that was least disturbed by the other work on the machine.

    Parameters
    ----------
    repeats:
        How many transforms are timed in one batch.
    size:
        The transform size.

    Returns
    -------
    float
        The cost of one transform, in microseconds.
    """
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)  # warm the plan up
    times: list[float] = []
    for _ in range(max(1, int(repeats))):
        start = time.perf_counter_ns()
        np.fft.rfft(data)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return min(times) if times else 0.0


def transform_us_samples(
    runs: int = 20,
    repeats: int = 10,
    size: int = 1024,
) -> list[float]:
    """Return the samples of the machine yardstick.

    One sample is the average of the fastest transform of two batches that
    sit next to each other — the same statistic the per block calibration
    of the guard uses.  The samples describe how fast the machine is: the
    spread between the fastest and the slowest sample is the load of the
    machine during the measurement.

    Parameters
    ----------
    runs:
        How many samples are taken.
    repeats:
        How many transforms are timed per batch.
    size:
        The transform size.

    Returns
    -------
    list[float]
        The samples, in microseconds.
    """
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)  # warm the plan up
    samples: list[float] = []
    for _ in range(max(1, int(runs))):
        before: list[float] = []
        for _ in range(max(1, int(repeats))):
            start = time.perf_counter_ns()
            np.fft.rfft(data)
            before.append((time.perf_counter_ns() - start) / 1000.0)
        after: list[float] = []
        for _ in range(max(1, int(repeats))):
            start = time.perf_counter_ns()
            np.fft.rfft(data)
            after.append((time.perf_counter_ns() - start) / 1000.0)
        samples.append((min(before) + min(after)) / 2.0)
    return samples


def reference_transform_us(
    runs: int = 20,
    repeats: int = 10,
    size: int = 1024,
    percentile: float = 10.0,
) -> float:
    """Return the yardstick of the reference machine.

    The yardstick is a low percentile of the samples of
    :func:`transform_us_samples`: it describes the machine when it is not
    busy, which is the machine a plug-in runs on.  The realtime budget of
    a block is expressed in these units, so the budget of a module does
    not depend on the machine the test happens to run on.

    Parameters
    ----------
    runs:
        How many samples are taken.
    repeats:
        How many transforms are timed per batch.
    size:
        The transform size.
    percentile:
        The percentile of the samples that is used.

    Returns
    -------
    float
        The yardstick, in microseconds.
    """
    samples = transform_us_samples(runs=runs, repeats=repeats, size=size)
    if not samples:
        return 0.0
    return _percentile(samples, percentile)


def _paired_block_times(
    process: Callable[[np.ndarray], np.ndarray],
    template: np.ndarray,
    repeats: int,
    warmup: int,
    calib_repeats: int,
    calib_size: int = 1024,
) -> tuple[list[float], list[float], list[float], int]:
    """Time blocks and the machine calibration side by side.

    The machine is shared, so a wall-clock number on its own says nothing
    about the module: it says how busy the machine was.  Every block is
    therefore timed together with a batch of transforms that is measured
    right next to it, and the pair gives the cost of the block in
    transforms.  Both sides of the pair scale with the load of the
    machine, so their ratio does not.

    Parameters
    ----------
    process:
        The function to measure.
    template:
        The block to feed it.
    repeats:
        Number of measured blocks.
    warmup:
        Number of untimed blocks before the measurement starts.
    calib_repeats:
        How many transforms are timed next to every block.
    calib_size:
        The transform size.

    Returns
    -------
    tuple
        The block times, the ratios (block time / transform time), the
        transform times and the number of output samples of the last
        block.
    """
    warm = max(0, int(warmup))
    blocks = [template.copy() for _ in range(warm + max(1, int(repeats)))]
    for block in blocks[:warm]:
        process(block)

    data = np.zeros(calib_size, dtype=np.float64)
    np.fft.rfft(data)  # warm the plan up

    times: list[float] = []
    ratios: list[float] = []
    calibs: list[float] = []
    output_samples = 0
    for block in blocks[warm:]:
        # The calibration is taken on both sides of the block, so that a
        # change of the load between the two measurements cannot make the
        # block look cheap.
        before: list[float] = []
        for _ in range(max(1, int(calib_repeats))):
            start = time.perf_counter_ns()
            np.fft.rfft(data)
            before.append((time.perf_counter_ns() - start) / 1000.0)

        start = time.perf_counter_ns()
        out = process(block)
        block_us = (time.perf_counter_ns() - start) / 1000.0

        after: list[float] = []
        for _ in range(max(1, int(calib_repeats))):
            start = time.perf_counter_ns()
            np.fft.rfft(data)
            after.append((time.perf_counter_ns() - start) / 1000.0)

        fft_us = (min(before) + min(after)) / 2.0

        times.append(block_us)
        calibs.append(fft_us)
        ratios.append(block_us / fft_us if fft_us > 0 else 0.0)
        if out is not None and hasattr(out, "shape"):
            output_samples = int(out.shape[0])
    return times, ratios, calibs, output_samples


def measure_module_calibrated(
    code: str,
    block_sizes: Iterable[int] = BLOCK_SIZES,
    runs: int = 5,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    calib_repeats: int = 20,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    signal: np.ndarray | None = None,
) -> ModuleMeasurement:
    """Measure a module together with the machine it was measured on.

    Every block is timed next to a batch of transforms, so the result is
    a cost in *transform equivalents*: how many 1024 point transforms the
    module spends on one block of audio.  That number does not depend on
    how busy the machine is, which is what makes the budget portable.

    The reported statistic is the **fastest of N runs**: the smallest
    ratio of the whole measurement.  On a shared machine that is the
    observation that was least disturbed by other work.

    Parameters
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    block_sizes:
        The block sizes to measure.
    runs:
        How many times the module is measured; the fastest run wins.
    repeats:
        Number of timed blocks per block size and run.
    warmup:
        Number of untimed blocks before the timed ones.
    calib_repeats:
        How many transforms are timed next to every block.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    signal:
        The signal to feed; a deterministic one is built when omitted.

    Returns
    -------
    ModuleMeasurement
        The merged measurement; every timing carries the calibrated cost
        in transform equivalents.
    """
    from aurcore import registry  # local import: keeps the harness importable

    measurement = ModuleMeasurement(code=code)
    cls = registry.get(code)
    measurement.display_name = str(getattr(cls, "display_name", code))
    measurement.category = str(getattr(cls, "category", ""))

    blocks = [int(b) for b in block_sizes]
    if signal is None:
        signal = make_test_signal(max(blocks) * 4, sample_rate, channels)

    best: dict[int, tuple[float, float, float, float, float, float, int]] = {}
    seen: dict[int, list[float]] = {}
    for _ in range(max(1, int(runs))):
        try:
            module = cls(sample_rate, channels)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            measurement.error = f"{type(exc).__name__}: {exc}"
            return measurement
        for block_size in blocks:
            block = np.ascontiguousarray(signal[:block_size])
            try:
                times, ratios, calibs, output_samples = _paired_block_times(
                    module.process,
                    block,
                    repeats=repeats,
                    warmup=warmup,
                    calib_repeats=calib_repeats,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                measurement.error = f"{type(exc).__name__}: {exc}"
                return measurement
            if not ratios:
                continue
            index = min(range(len(ratios)), key=lambda i: ratios[i])
            row = (
                min(ratios),
                _percentile(times, 50.0),
                min(times),
                _percentile(times, 95.0),
                max(times),
                calibs[index],
                output_samples,
            )
            seen.setdefault(block_size, []).append(row[0])
            current = best.get(block_size)
            if current is None or row[0] < current[0]:
                best[block_size] = row

    for block_size in blocks:
        row = best.get(block_size)
        if row is None:
            continue
        measurement.timings.append(
            BlockTiming(
                block=block_size,
                budget_us=block_budget_us(block_size, sample_rate),
                median_us=row[1],
                min_us=row[2],
                p95_us=row[3],
                max_us=row[4],
                output_samples=row[6],
                transforms=row[0],
                transform_us=row[5],
                samples=list(seen.get(block_size, [])),
            )
        )
    return measurement


def measure_chain_calibrated(
    codes: Sequence[str],
    block_sizes: Iterable[int] = BLOCK_SIZES,
    runs: int = 5,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    calib_repeats: int = 20,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    signal: np.ndarray | None = None,
) -> ChainMeasurement:
    """Measure a chain together with the machine it was measured on.

    See :func:`measure_module_calibrated` for the method.

    Parameters
    ----------
    codes:
        The module codes of the chain, in signal order.
    block_sizes:
        The block sizes to measure.
    runs:
        How many times the chain is measured; the fastest run wins.
    repeats:
        Number of timed blocks per block size and run.
    warmup:
        Number of untimed blocks before the timed ones.
    calib_repeats:
        How many transforms are timed next to every block.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    signal:
        The signal to feed; a deterministic one is built when omitted.

    Returns
    -------
    ChainMeasurement
        The merged measurement; every timing carries the calibrated cost
        in transform equivalents.
    """
    from aurcore.api import create_engine

    measurement = ChainMeasurement(codes=list(codes))
    engine = create_engine(sample_rate, channels)
    try:
        for code in codes:
            engine.add_module(code)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        measurement.error = f"{type(exc).__name__}: {exc}"
        return measurement

    blocks = [int(b) for b in block_sizes]
    if signal is None:
        signal = make_test_signal(max(blocks) * 4, sample_rate, channels)

    best: dict[int, tuple[float, float, float, float, float, float, int]] = {}
    for _ in range(max(1, int(runs))):
        for block_size in blocks:
            block = np.ascontiguousarray(signal[:block_size])
            try:
                times, ratios, calibs, output_samples = _paired_block_times(
                    engine.process,
                    block,
                    repeats=repeats,
                    warmup=warmup,
                    calib_repeats=calib_repeats,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                measurement.error = f"{type(exc).__name__}: {exc}"
                return measurement
            if not ratios:
                continue
            index = min(range(len(ratios)), key=lambda i: ratios[i])
            row = (
                min(ratios),
                _percentile(times, 50.0),
                min(times),
                _percentile(times, 95.0),
                max(times),
                calibs[index],
                output_samples,
            )
            current = best.get(block_size)
            if current is None or row[0] < current[0]:
                best[block_size] = row

    for block_size in blocks:
        row = best.get(block_size)
        if row is None:
            continue
        measurement.timings.append(
            BlockTiming(
                block=block_size,
                budget_us=block_budget_us(block_size, sample_rate),
                median_us=row[1],
                min_us=row[2],
                p95_us=row[3],
                max_us=row[4],
                output_samples=row[6],
                transforms=row[0],
                transform_us=row[5],
            )
        )
    return measurement


def measure_catalogue_calibrated(
    targets: Mapping[str, Callable[[], Callable[[np.ndarray], np.ndarray]]],
    block_sizes: Iterable[int] = BLOCK_SIZES,
    rounds: int = 5,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    calib_repeats: int = 10,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    signal: np.ndarray | None = None,
) -> dict[str, ModuleMeasurement]:
    """Measure many targets in an interleaved sweep.

    Every round measures every target once, so the samples of a target
    are spread over the whole sweep instead of being taken back to back.
    On a shared machine that matters: the load comes and goes in bursts,
    and a target that is only measured inside one burst never sees a quiet
    moment.  The result of a target is the smallest ratio of all its
    samples, i.e. the sample that was least disturbed.

    Parameters
    ----------
    targets:
        A mapping of a key to a factory that returns a fresh ``process``
        callable (a bound ``process()`` of a new module, or of a new
        engine).
    block_sizes:
        The block sizes to measure.
    rounds:
        How many times every target is measured.
    repeats:
        Number of timed blocks per block size and round.
    warmup:
        Number of untimed blocks before the timed ones.
    calib_repeats:
        How many transforms are timed next to every block.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    signal:
        The signal to feed; a deterministic one is built when omitted.

    Returns
    -------
    dict[str, ModuleMeasurement]
        One measurement per target.
    """
    blocks = [int(b) for b in block_sizes]
    if signal is None:
        signal = make_test_signal(max(blocks) * 4, sample_rate, channels)

    measurements: dict[str, ModuleMeasurement] = {}
    best: dict[str, dict[int, tuple[float, ...]]] = {}
    rounds_seen: dict[str, dict[int, list[float]]] = {}
    for key in targets:
        measurements[key] = ModuleMeasurement(code=key)
        best[key] = {}
        rounds_seen[key] = {}

    for _round in range(max(1, int(rounds))):
        for key, make in targets.items():
            try:
                process = make()
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                measurements[key].error = f"{type(exc).__name__}: {exc}"
                continue
            for block_size in blocks:
                block = np.ascontiguousarray(signal[:block_size])
                try:
                    times, ratios, calibs, output_samples = _paired_block_times(
                        process,
                        block,
                        repeats=repeats,
                        warmup=warmup,
                        calib_repeats=calib_repeats,
                    )
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    measurements[key].error = f"{type(exc).__name__}: {exc}"
                    continue
                if not ratios:
                    continue
                index = min(range(len(ratios)), key=lambda i: ratios[i])
                row = (
                    min(ratios),
                    _percentile(times, 50.0),
                    min(times),
                    _percentile(times, 95.0),
                    max(times),
                    calibs[index],
                    output_samples,
                )
                rounds_seen[key].setdefault(block_size, []).append(row[0])
                current = best[key].get(block_size)
                if current is None or row[0] < current[0]:
                    best[key][block_size] = row

    for key, rows in best.items():
        for block_size, row in rows.items():
            measurements[key].timings.append(
                BlockTiming(
                    block=block_size,
                    budget_us=block_budget_us(block_size, sample_rate),
                    median_us=row[1],
                    min_us=row[2],
                    p95_us=row[3],
                    max_us=row[4],
                    output_samples=row[6],
                    transforms=row[0],
                    transform_us=row[5],
                    samples=list(rounds_seen[key].get(block_size, [])),
                )
            )
    return measurements


def measure_all_modules(
    block_sizes: Iterable[int] = BLOCK_SIZES,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    repeats: int = DEFAULT_REPEATS,
    warmup: int = DEFAULT_WARMUP,
    measure_allocations: bool = True,
) -> list[ModuleMeasurement]:
    """Measure every registered module.

    Parameters
    ----------
    block_sizes:
        The block sizes to measure.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    repeats:
        Number of timed blocks per block size.
    warmup:
        Number of untimed blocks before the timed ones.
    measure_allocations:
        Run the (slow) allocation pass as well.

    Returns
    -------
    list of ModuleMeasurement
        One record per registered module, in registry order.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = make_test_signal(max(int(b) for b in block_sizes) * 4, sample_rate, channels)
    results: list[ModuleMeasurement] = []
    for info in registry.all():
        results.append(
            measure_module(
                info.code,
                block_sizes=block_sizes,
                sample_rate=sample_rate,
                channels=channels,
                repeats=repeats,
                warmup=warmup,
                signal=signal,
                measure_allocations=measure_allocations,
            )
        )
    return results


def measure_allocations(
    process: Callable[[np.ndarray], np.ndarray],
    block: np.ndarray,
    repeats: int = 3,
    warmup: int = 1,
) -> AllocationStats:
    """Measure what a processing function allocates per block.

    Parameters
    ----------
    process:
        The function to measure, e.g. ``module.process``.
    block:
        The block to feed it.
    repeats:
        Number of measured blocks.
    warmup:
        Number of untimed blocks before the measurement starts.

    Returns
    -------
    AllocationStats
        The allocation statistics.
    """
    return _allocation_pass(process, block, repeats=repeats, warmup=warmup)


def merge_measurements(runs: Sequence[ModuleMeasurement]) -> ModuleMeasurement:
    """Merge repeated measurements of the same module.

    A shared machine makes every timing noisy, so the harness can measure a
    module several times and keep the *fastest* run for every block size.
    The fastest run is the one that was least disturbed by other work, which
    makes it the best estimate of the module's own cost.

    Parameters
    ----------
    runs:
        The repeated measurements of one module.

    Returns
    -------
    ModuleMeasurement
        The merged measurement.
    """
    if not runs:
        return ModuleMeasurement(code="")
    merged = ModuleMeasurement(
        code=runs[0].code,
        display_name=runs[0].display_name,
        category=runs[0].category,
        error=runs[0].error,
    )
    for timing in runs[0].timings:
        best = timing
        for run in runs[1:]:
            other = run.timing_for(timing.block)
            if other is not None and other.median_us < best.median_us:
                best = other
        merged.timings.append(best)
    merged.allocations = min(
        (run.allocations for run in runs),
        key=lambda a: a.peak_bytes,
    )
    merged.peak_rss_bytes = next(
        (run.peak_rss_bytes for run in runs if run.peak_rss_bytes), None
    )
    return merged


def merge_chain_measurements(runs: Sequence[ChainMeasurement]) -> ChainMeasurement:
    """Merge repeated measurements of the same chain.

    Parameters
    ----------
    runs:
        The repeated measurements of one chain.

    Returns
    -------
    ChainMeasurement
        The merged measurement.
    """
    if not runs:
        return ChainMeasurement()
    merged = ChainMeasurement(codes=list(runs[0].codes), error=runs[0].error)
    for timing in runs[0].timings:
        best = timing
        for run in runs[1:]:
            other = run.timing_for(timing.block)
            if other is not None and other.median_us < best.median_us:
                best = other
        merged.timings.append(best)
    merged.allocations = min((run.allocations for run in runs), key=lambda a: a.peak_bytes)
    merged.peak_rss_bytes = next(
        (run.peak_rss_bytes for run in runs if run.peak_rss_bytes), None
    )
    return merged


# ---------------------------------------------------------------------------
# Profiling (hot spot finder)
# ---------------------------------------------------------------------------
def profile_module(
    code: str,
    block: int = 64,
    repeats: int = 5,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    top: int = 15,
) -> str:
    """Return a ``cProfile`` report for one module.

    The report is the raw material for the "which primitive is slow" part of
    the performance review.

    Parameters
    ----------
    code:
        The module code to profile.
    block:
        The block size to profile with.
    repeats:
        Number of profiled blocks.
    sample_rate:
        Sample rate in Hz.
    channels:
        Channel count.
    top:
        Number of lines in the report.

    Returns
    -------
    str
        The formatted profile report.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, sample_rate, channels)
    signal = make_test_signal(max(block * 4, 1024), sample_rate, channels)
    block_data = np.ascontiguousarray(signal[:block])

    module.process(block_data)  # warm up
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(max(1, repeats)):
        module.process(block_data)
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(top)
    return stream.getvalue()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_table(
    measurements: Sequence[ModuleMeasurement],
    block_sizes: Iterable[int] = BLOCK_SIZES,
    baseline_rss_bytes: int | None = None,
) -> str:
    """Render the measurements as a Markdown table.

    One row per module, one column pair (time, budget share) per block size.

    Parameters
    ----------
    measurements:
        The measurements to render.
    block_sizes:
        The block sizes to show.
    baseline_rss_bytes:
        The peak RSS of a process that only imports the core.  When it is
        given, the memory column shows what the module costs on top of it.

    Returns
    -------
    str
        The Markdown table.
    """
    blocks = [int(b) for b in block_sizes]
    header = "| module | name | " + " | ".join(
        f"{b} smp us | {b} smp %" for b in blocks
    ) + " | arrays/blk | RSS MiB | RSS Δ MiB |"
    rule = "|---|---|" + "---|" * (2 * len(blocks)) + "---|---|---|"
    lines = [header, rule]
    for measurement in measurements:
        cells: list[str] = []
        for block in blocks:
            timing = measurement.timing_for(block)
            if timing is None:
                cells.extend(["—", "—"])
                continue
            flag = " ⚠" if timing.over_budget else ""
            cells.append(f"{timing.median_us:.0f}{flag}")
            cells.append(f"{timing.budget_fraction * 100:.1f}%")
        rss = (
            measurement.peak_rss_bytes / (1024.0 * 1024.0)
            if measurement.peak_rss_bytes
            else float("nan")
        )
        rss_text = "—" if rss != rss else f"{rss:.1f}"
        if baseline_rss_bytes and measurement.peak_rss_bytes:
            delta = (measurement.peak_rss_bytes - baseline_rss_bytes) / (1024.0 * 1024.0)
            delta_text = f"{delta:+.1f}"
        else:
            delta_text = "—"
        lines.append(
            f"| `{measurement.code}` | {measurement.display_name} | "
            + " | ".join(cells)
            + f" | {measurement.allocations.arrays_per_block:.0f}"
            + f" | {rss_text} | {delta_text} |"
        )
    return "\n".join(lines)


def render_chain_table(chains: Sequence[ChainMeasurement]) -> str:
    """Render chain measurements as a Markdown table.

    Parameters
    ----------
    chains:
        The chain measurements to render.

    Returns
    -------
    str
        The Markdown table.
    """
    blocks = sorted({t.block for chain in chains for t in chain.timings})
    header = "| chain | modules | " + " | ".join(
        f"{b} smp us | {b} smp %" for b in blocks
    ) + " | arrays/blk |"
    rule = "|---|---|" + "---|" * (2 * len(blocks)) + "---|"
    lines = [header, rule]
    for chain in chains:
        cells: list[str] = []
        for block in blocks:
            timing = chain.timing_for(block)
            if timing is None:
                cells.extend(["—", "—"])
                continue
            flag = " ⚠" if timing.over_budget else ""
            cells.append(f"{timing.median_us:.0f}{flag}")
            cells.append(f"{timing.budget_fraction * 100:.1f}%")
        lines.append(
            f"| {' → '.join(chain.codes)} | {len(chain.codes)} | "
            + " | ".join(cells)
            + f" | {chain.allocations.arrays_per_block:.0f} |"
        )
    return "\n".join(lines)


def reference_fft_us(size: int = 1024, repeats: int = 200) -> float:
    """Measure the cost of one forward FFT, in microseconds.

    This is the calibration of the whole report: it turns a wall-clock time
    into a machine independent "how many transforms is this worth" number.
    A measurement taken on a busy machine and one taken on an idle machine
    then describe the same amount of work.

    Parameters
    ----------
    size:
        The transform size.
    repeats:
        How many transforms are timed.

    Returns
    -------
    float
        The median time of one transform, in microseconds.
    """
    data = np.zeros(size, dtype=np.float64)
    np.fft.rfft(data)
    times: list[float] = []
    for _ in range(max(1, repeats)):
        start = time.perf_counter_ns()
        np.fft.rfft(data)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return _percentile(times, 50.0)


def summarise(
    measurements: Sequence[ModuleMeasurement],
    block: int = 64,
    top: int = 12,
) -> str:
    """Summarise a measurement sweep: the worst modules and the offenders.

    Parameters
    ----------
    measurements:
        The measurements to summarise.
    block:
        The block size the summary is about.
    top:
        How many modules the "worst" list holds.

    Returns
    -------
    str
        A plain text summary.
    """
    lines: list[str] = []
    budget = block_budget_us(block)
    lines.append(f"block budget at {block} samples / 48 kHz: {budget:.1f} us")
    lines.append("")

    rows = []
    for measurement in measurements:
        timing = measurement.timing_for(block)
        if timing is None:
            continue
        rows.append((timing.budget_fraction, measurement, timing))
    rows.sort(key=lambda row: row[0], reverse=True)

    lines.append(f"worst {top} modules at {block} samples:")
    lines.append(
        f"  {'module':<24}{'us':>9}{'budget %':>10}{'arrays/blk':>12}{'churn KiB':>11}"
    )
    for _fraction, measurement, timing in rows[:top]:
        lines.append(
            f"  {measurement.code:<24}{timing.median_us:>9.0f}"
            f"{timing.budget_fraction * 100:>9.1f}%"
            f"{measurement.allocations.arrays_per_block:>12.0f}"
            f"{measurement.allocations.peak_bytes / 1024.0:>11.0f}"
        )

    lines.append("")
    offenders = [row for row in rows if row[2].over_budget]
    lines.append(f"over budget at {block} samples: {len(offenders)} of {len(rows)}")
    for _fraction, measurement, timing in offenders:
        lines.append(
            f"  {measurement.code:<24}{timing.median_us:>9.0f} us"
            f"{timing.budget_fraction * 100:>9.1f}% of the budget"
        )

    lines.append("")
    lines.append("largest allocation churn per block:")
    churn = sorted(
        measurements,
        key=lambda m: m.allocations.peak_bytes,
        reverse=True,
    )[:top]
    for measurement in churn:
        lines.append(
            f"  {measurement.code:<24}{measurement.allocations.peak_bytes / 1024.0:>11.0f} KiB"
            f"{measurement.allocations.arrays_per_block:>12.0f} arrays"
        )

    if any(m.peak_rss_bytes for m in measurements):
        lines.append("")
        lines.append("largest peak RSS:")
        memory = sorted(
            (m for m in measurements if m.peak_rss_bytes),
            key=lambda m: m.peak_rss_bytes or 0,
            reverse=True,
        )[:top]
        for measurement in memory:
            lines.append(
                f"  {measurement.code:<24}"
                f"{(measurement.peak_rss_bytes or 0) / (1024.0 * 1024.0):>9.1f} MiB"
            )
    return "\n".join(lines)


def environment_info() -> dict[str, Any]:
    """Return a description of the machine the measurements ran on."""
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
    }
