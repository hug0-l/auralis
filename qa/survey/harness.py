"""
qa.survey.harness — the measurement library of the module survey.

Everything in here is measurement only: it drives the modules the way a
plug-in host drives them (one block in, one block out) and it never touches
``aurcore`` or ``aurcore.dsp``.

The five measurements
---------------------

``region_level_db``
    The level of a *region* of the signal: a frequency band, a time window,
    or both.  A region is measured with a Hann windowed transform, so a full
    scale sine inside the band reads -3.01 dBFS, exactly like the corpus
    measurement helper does.

``wanted_removed_db`` / ``unwanted_removed_db``
    The change of a region's level between the input and the output.  A
    negative number means the region got quieter.

``cost_us``
    What one block of 64 samples costs the module, in the microseconds of
    the *reference machine*.  Every block is timed next to a batch of
    1024 point transforms of the machine that measures it, so the ratio of
    the two does not move with the load of the machine, and the result is
    expressed in the yardstick of the performance table
    (``qa/performance/budgets.py``).  The fastest of five passes is kept,
    because a shared machine makes every timing noisy and the fastest
    pass is the one that was least disturbed.

``latency_samples``
    The delay the module puts into the signal, measured by the peak of the
    cross correlation between the input and the output.  The probe is a
    broadband noise burst, so the correlation has one peak and not one peak
    per period of a tone, and the search covers the whole range the
    catalogue can declare: the bound of the search is the largest latency
    any module of the catalogue declares, plus a margin.

``block_delta_db``
    How much the output changes when the same audio is fed in 64 sample
    blocks instead of in one piece.  A block coherent module produces the
    same audio either way.

``allocator_independent``
    Whether the output depends on memory the allocator handed out.  The
    check poisons the heap between two runs and compares the results bit by
    bit: a module that reads uninitialised memory produces different
    numbers.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The sample rate every measurement runs at.
SAMPLE_RATE = 48_000

#: The channel count every measurement runs at.
CHANNELS = 2

#: The block size the realtime budget is measured against.
BLOCK = 64

#: The realtime budget of one 64 sample block at 48 kHz, in microseconds.
BUDGET_US = (float(BLOCK) / float(SAMPLE_RATE)) * 1_000_000.0

#: The yardstick of the cost measurement: the cost of one 1024 point
#: transform on the **reference machine**, in microseconds.  Every block is
#: timed next to a batch of transforms of the machine that measures it, so
#: the ratio of the two does not move with the load of the machine, and the
#: cost of the block is expressed in the microseconds of the reference
#: machine.  It is the same yardstick the performance table uses
#: (``qa/performance/budgets.py``) and it is read from that table, so the
#: two never drift apart.
REFERENCE_TRANSFORM_US = 8.300

#: How many transforms are timed next to every block.
TRANSFORM_REPEATS = 10

#: The floor of the level analyser, in dBFS.
FLOOR_DB = -180.0

#: How many blocks are timed per measurement.
TIMED_BLOCKS = 15

#: How many untimed blocks warm the module up before the timing starts.
WARMUP_BLOCKS = 3

#: How often the whole timing pass is repeated; the fastest run is kept.
TIMING_RUNS = 5

#: The seed of the deterministic noise sources.
SEED = 20_260_101

#: The margin above the largest declared latency of the catalogue that the
#: delay search covers.  The bound of the search is not a constant: it is
#: the largest latency any module of the catalogue declares plus this
#: margin, so that a module that declares less than it delays — or that
#: declares more than it delays — is still measured inside the search.  The
#: margin is one window of 2048 samples (42.7 ms at 48 kHz), the width the
#: host of the plug-in family used to search, so the survey sees every
#: delay the host could see and every delay the catalogue can declare.
LATENCY_SEARCH_MARGIN = 2048

#: The seed of the broadband noise the delay is measured with.  It is not
#: the seed of the quality probes: the delay probe has to be broadband, so
#: that the cross correlation has one peak and not one peak per period of
#: a tone.
LATENCY_PROBE_SEED = SEED + 7

#: How long the broadband delay probe is, in seconds, when the catalogue
#: declares no latency at all.  The probe is made longer when the catalogue
#: declares a long one, so that the search range always fits inside it.
LATENCY_PROBE_SECONDS = 0.25

__all__ = [
    "BLOCK",
    "BUDGET_US",
    "CHANNELS",
    "FLOOR_DB",
    "LATENCY_PROBE_SECONDS",
    "LATENCY_SEARCH_MARGIN",
    "Region",
    "SAMPLE_RATE",
    "SEED",
    "TIMED_BLOCKS",
    "WARMUP_BLOCKS",
    "block_budget_us",
    "cross_correlation_delay",
    "declared_latencies",
    "latency_probe_signal",
    "latency_search_bound",
    "level_change_db",
    "machine_load",
    "measure_allocator_independence",
    "measure_block_coherence",
    "measure_cost_us",
    "measure_latency_samples",
    "measure_module_delay",
    "poison_heap",
    "reference_transform_us",
    "region_level_db",
    "reset_latency_search_bound",
    "stream_process",
]


# ---------------------------------------------------------------------------
# The load of the machine
# ---------------------------------------------------------------------------
def _cpu_times() -> tuple[int, int, int] | None:
    """Return the idle, kernel and user CPU times of the machine.

    Returns ``None`` when the platform does not expose the counters.
    """
    if sys.platform != "win32":
        try:
            times = os.times()
        except (OSError, AttributeError):  # pragma: no cover - platform specific
            return None
        return (int(times.idle * 1e9), int(times.system * 1e9), int(times.user * 1e9))
    try:  # pragma: no cover - Windows only
        import ctypes

        class _FileTime(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32),
            ]

        def _value(ft: "_FileTime") -> int:
            return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        ok = ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            return None
        return (_value(idle), _value(kernel), _value(user))
    except Exception:  # noqa: BLE001 - the metric is optional
        return None


def machine_load(
    start: tuple[int, int, int] | None = None,
    end: tuple[int, int, int] | None = None,
) -> float:
    """Return the share of the CPU that was busy between two snapshots.

    The number answers the question a timing cannot answer on its own: how
    busy was the machine while the number was taken?  A measurement taken on
    a machine that ran at 100 % is not comparable with one taken on an idle
    machine, which is why the cost column of the report carries this number
    next to it.

    Parameters
    ----------
    start:
        The snapshot taken before the work.
    end:
        The snapshot taken after the work.

    Returns
    -------
    float
        The load in percent, or ``nan`` when the platform does not expose
        the counters.
    """
    if start is None or end is None:
        return float("nan")
    idle = end[0] - start[0]
    total = (end[1] - start[1]) + (end[2] - start[2])
    if total <= 0:
        return float("nan")
    return 100.0 * (total - idle) / float(total)


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Region:
    """A rectangle in the time/frequency plane of a signal.

    Attributes
    ----------
    lo_hz, hi_hz:
        The frequency band of the region.
    start_s, end_s:
        The time window of the region.  ``end_s`` of ``None`` means "to the
        end of the signal".
    metric:
        ``"rms"`` measures the band limited mean square, ``"peak"`` measures
        the largest sample in the window.  A single impulse wants ``"peak"``:
        its mean square says nothing about how loud the click is.
    channel:
        ``None`` measures the mono mix, an integer measures that channel
        only.
    label:
        A human readable name for the report.
    """

    lo_hz: float
    hi_hz: float
    start_s: float = 0.0
    end_s: float | None = None
    metric: str = "rms"
    channel: int | None = None
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "lo_hz": self.lo_hz,
            "hi_hz": self.hi_hz,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "metric": self.metric,
            "channel": self.channel,
            "label": self.label,
        }


# ---------------------------------------------------------------------------
# Level measurement
# ---------------------------------------------------------------------------
def _channel_slice(x: np.ndarray, channel: int | None) -> np.ndarray:
    if channel is None:
        if x.ndim == 1:
            return x
        return x.mean(axis=1)
    return x[:, int(channel)]


def region_level_db(x: np.ndarray, sample_rate: int, region: Region) -> float:
    """Return the level of ``region`` inside ``x``, in dBFS.

    The measurement is a Hann windowed transform of the time window, and
    the mean square of the band is reconstructed from the transform, so a
    full scale sine inside the band reads -3.01 dBFS.

    Parameters
    ----------
    x:
        The signal, shape ``(n_samples, n_channels)`` or ``(n_samples,)``.
    sample_rate:
        The sample rate of the signal.
    region:
        The region to measure.

    Returns
    -------
    float
        The level in dBFS; the floor of the analyser when the region is
        empty or silent.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.size == 0:
        return FLOOR_DB
    total = arr.shape[0]
    i0 = max(0, int(round(float(region.start_s) * sample_rate)))
    i1 = total if region.end_s is None else min(total,
                                                int(round(float(region.end_s) * sample_rate)))
    if i1 <= i0:
        return FLOOR_DB
    seg = _channel_slice(arr[i0:i1], region.channel)
    if seg.size == 0:
        return FLOOR_DB
    if region.metric == "peak":
        peak = float(np.max(np.abs(seg)))
        return 20.0 * math.log10(peak) if peak > 0.0 else FLOOR_DB

    window = np.hanning(seg.size)
    spectrum = np.fft.rfft(seg * window)
    freqs = np.fft.rfftfreq(seg.size, 1.0 / float(sample_rate))
    mask = (freqs >= float(region.lo_hz)) & (freqs < float(region.hi_hz))
    if not bool(mask.any()):
        return FLOOR_DB
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    if weight <= 0.0 or power <= 0.0:
        return FLOOR_DB
    mean_square = 2.0 * power / (weight * weight)
    return 10.0 * math.log10(max(mean_square, 1e-30))


def level_change_db(before: float, after: float) -> float:
    """Return the change of a level, in decibels.

    A negative number means the level went down.  A level that fell to the
    floor of the analyser is reported as the distance to the floor, not as
    "no change": a module that silences a band has removed it completely.
    """
    if before <= FLOOR_DB and after <= FLOOR_DB:
        return 0.0
    if after <= FLOOR_DB:
        return FLOOR_DB - before
    if before <= FLOOR_DB:
        return after - FLOOR_DB
    return after - before


# ---------------------------------------------------------------------------
# Driving a module
# ---------------------------------------------------------------------------
def stream_process(module, x: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Feed ``x`` to the module in blocks of ``block`` samples.

    This is the way a plug-in host drives a module, and the only way that
    exercises the state the module carries between blocks.

    Parameters
    ----------
    module:
        The module instance.
    x:
        The signal, shape ``(n_samples, n_channels)``.
    block:
        The block size.

    Returns
    -------
    numpy.ndarray
        The concatenated output.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    pieces: list[np.ndarray] = []
    for start in range(0, arr.shape[0], max(1, int(block))):
        out = module.process(arr[start : start + block])
        if out is None:
            out = arr[start : start + block]
        pieces.append(np.asarray(out, dtype=np.float64))
    if not pieces:
        return np.zeros((0, arr.shape[1]), dtype=np.float64)
    return np.concatenate(pieces, axis=0)


def _fresh(code: str, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
    """Create a new instance of a module."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(code, sample_rate, channels)


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------
def block_budget_us(block: int = BLOCK, sample_rate: int = SAMPLE_RATE) -> float:
    """Return the realtime budget of one block, in microseconds."""
    return (float(block) / float(sample_rate)) * 1_000_000.0


def _time_pass(process: Callable[[np.ndarray], np.ndarray], block: np.ndarray) -> float:
    """Return the median time of one call, in microseconds."""
    times: list[float] = []
    for index in range(WARMUP_BLOCKS + TIMED_BLOCKS):
        start = time.perf_counter_ns()
        process(block)
        elapsed = (time.perf_counter_ns() - start) / 1000.0
        if index >= WARMUP_BLOCKS:
            times.append(elapsed)
    if not times:
        return 0.0
    return float(np.median(np.asarray(times, dtype=np.float64)))


def reference_transform_us() -> float:
    """Return the yardstick of the committed performance table.

    The cost of a block is measured next to a batch of 1024 point
    transforms of the machine that measures it, and the result is expressed
    in the microseconds of the **reference** machine: the requirement of
    the gate is then the same number the performance table uses, and it
    does not move with the load of the machine that measured it.

    Returns
    -------
    float
        The cost of one 1024 point transform on the reference machine, in
        microseconds.
    """
    path = REPO_ROOT / "qa" / "performance" / "budgets.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - the table is always there
        return REFERENCE_TRANSFORM_US
    for line in text.splitlines():
        if line.startswith("REFERENCE_TRANSFORM_US"):
            _, _, value = line.partition("=")
            try:
                return float(value.strip())
            except ValueError:
                break
    return REFERENCE_TRANSFORM_US


def _transform_us(repeats: int = TRANSFORM_REPEATS, size: int = 1024) -> float:
    """Return the cost of one 1024 point transform on this machine, in us.

    The **fastest** transform of the batch is the one that was least
    disturbed by the other work on the machine, so it is the one that
    describes what the machine can do.

    Parameters
    ----------
    repeats:
        How many transforms are timed.
    size:
        The size of the transform.

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
    return float(min(times)) if times else 0.0


def _paired_pass(
    process: Callable[[np.ndarray], np.ndarray],
    block: np.ndarray,
    repeats: int,
    warmup: int,
    transform_repeats: int = TRANSFORM_REPEATS,
) -> list[float]:
    """Return the ratio of the block time to the transform time.

    Every block is timed between two batches of transforms, so a change of
    the load of the machine between the two measurements cannot make the
    block look cheap.  The ratio of the two does not move with the load,
    and it is the number the cost of a block is expressed in.

    Parameters
    ----------
    process:
        The function to measure.
    block:
        The block to feed it.
    repeats:
        Number of measured blocks.
    warmup:
        Number of untimed blocks before the measurement starts.
    transform_repeats:
        How many transforms are timed next to every block.

    Returns
    -------
    list
        One ratio per measured block.
    """
    blocks = [block.copy() for _ in range(max(0, int(warmup)) + max(1, int(repeats)))]
    for item in blocks[: max(0, int(warmup))]:
        process(item)
    ratios: list[float] = []
    for item in blocks[max(0, int(warmup)) :]:
        before = _transform_us(transform_repeats)
        start = time.perf_counter_ns()
        process(item)
        elapsed = (time.perf_counter_ns() - start) / 1000.0
        after = _transform_us(transform_repeats)
        yardstick = 0.5 * (before + after)
        if yardstick > 0.0:
            ratios.append(elapsed / yardstick)
    return ratios


def measure_cost_us(
    code: str,
    block: int = BLOCK,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    signal: np.ndarray | None = None,
    runs: int = TIMING_RUNS,
    prepare: Callable[[Any], None] | None = None,
) -> float:
    """Return what one block of ``block`` samples costs the module, in us.

    The number is the cost **on the reference machine**: every block is
    timed next to a batch of 1024 point transforms of the same machine, so
    the ratio of the two does not move with the load of the machine that
    measures it, and the result is expressed in the microseconds of the
    reference machine — the yardstick of the performance table
    (``qa/performance/budgets.py``).  A gate that compares a wall clock
    number with a budget is a gate that fails when the machine is busy
    and passes when it is quiet, and neither says anything about the code.

    The fastest of ``runs`` passes is kept: a shared machine makes every
    timing noisy, and the fastest pass is the one that was least disturbed
    by other work.

    Parameters
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    block:
        The block size.
    sample_rate:
        The sample rate.
    channels:
        The channel count.
    signal:
        The block to feed the module; a deterministic one is built when
        omitted.
    runs:
        How often the timing pass is repeated.
    prepare:
        A callable that puts a fresh module into the state the measurement
        expects (parameters, learned profile, selected region).

    Returns
    -------
    float
        The cost of one block on the reference machine, in microseconds.
    """
    if signal is None:
        signal = test_signal(block, sample_rate, channels)
    block_data = np.ascontiguousarray(signal[:block])
    reference = reference_transform_us()
    best = math.inf
    for _ in range(max(1, int(runs))):
        module = _fresh(code, sample_rate, channels)
        if prepare is not None:
            prepare(module)
        try:
            ratios = _paired_pass(
                module.process,
                block_data,
                repeats=TIMED_BLOCKS,
                warmup=WARMUP_BLOCKS,
            )
        except Exception:  # noqa: BLE001 - a broken module is reported, not raised
            return math.inf
        if not ratios:
            continue
        value = float(np.median(np.asarray(ratios, dtype=np.float64)))
        best = min(best, value)
    if best == math.inf:
        return math.inf
    return float(best * reference)


def test_signal(n: int, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> np.ndarray:
    """Return a deterministic speech-like signal, shape ``(n, channels)``."""
    n = max(1, int(n))
    rng = np.random.default_rng(SEED)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    signal = 0.02 * rng.standard_normal(n)
    for harmonic, amp in ((1, 0.30), (2, 0.18), (3, 0.11), (5, 0.06), (8, 0.03)):
        signal += amp * np.sin(2.0 * np.pi * 118.0 * harmonic * t)
    signal += 0.02 * np.sin(2.0 * np.pi * 2600.0 * t)
    for pos in (n // 3, (2 * n) // 3):
        if 0 <= pos < n:
            signal[pos] += 0.85
            signal[pos + 1 if pos + 1 < n else pos] -= 0.4
    peak = float(np.max(np.abs(signal)))
    if peak > 0.0:
        signal = signal / peak * 0.25
    return np.repeat(signal[:, None], channels, axis=1)


# ---------------------------------------------------------------------------
# The search range of the delay
# ---------------------------------------------------------------------------
def declared_latencies(codes: list[str] | None = None) -> dict[str, int]:
    """Return the latency every module of the catalogue declares.

    The catalogue is the only place that knows how long a delay the product
    can produce is, so the search range of the delay measurement is taken
    from here and not from a constant in the measurement code.

    Parameters
    ----------
    codes:
        The modules to ask; every module of the registry when omitted.

    Returns
    -------
    dict
        The latency of every module, in samples.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    out: dict[str, int] = {}
    for info in registry.all():
        if codes is not None and info.code not in codes:
            continue
        module = registry.create(info.code, SAMPLE_RATE, CHANNELS)
        out[info.code] = int(getattr(module, "latency_samples", 0) or 0)
    return out


_BOUND_CACHE: dict[str, int] = {}


def latency_search_bound(
    codes: list[str] | None = None,
    margin: int = LATENCY_SEARCH_MARGIN,
) -> int:
    """Return the largest lag the delay search looks at, in samples.

    The bound is the largest latency the catalogue declares plus a margin,
    so that the search covers every delay the product can declare and a
    margin around it.  It is not a constant: a module that declares a
    longer delay than any module of the catalogue did before moves the
    bound with it.

    The number is cached, because asking the catalogue means creating one
    instance of every module, and that is not free.

    Parameters
    ----------
    codes:
        The modules to ask; every module of the registry when omitted.
    margin:
        The margin above the largest declared latency.

    Returns
    -------
    int
        The bound of the search, in samples.
    """
    key = "all" if codes is None else ",".join(sorted(codes))
    if key not in _BOUND_CACHE:
        largest = max(declared_latencies(codes).values(), default=0)
        _BOUND_CACHE[key] = int(largest) + int(margin)
    return _BOUND_CACHE[key]


def reset_latency_search_bound() -> None:
    """Forget the cached bound, so that it is taken from the catalogue again."""
    _BOUND_CACHE.clear()


def latency_probe_signal(
    n: int | None = None,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    seed: int = LATENCY_PROBE_SEED,
) -> np.ndarray:
    """Return the broadband noise burst the delay is measured with.

    The probe is noise and not a tone on purpose: the cross correlation of
    a tone has a peak at every multiple of its period, so the largest peak
    is not necessarily the delay.  The correlation of noise has one peak.

    Parameters
    ----------
    n:
        How many samples the burst has; long enough to hold the whole
        search range when omitted.
    sample_rate:
        The sample rate of the probe.
    channels:
        The channel count of the probe.
    seed:
        The seed of the noise.

    Returns
    -------
    numpy.ndarray
        The probe, shape ``(n, channels)``.
    """
    if n is None:
        bound = latency_search_bound()
        seconds = max(
            LATENCY_PROBE_SECONDS, (5.0 * float(bound)) / float(sample_rate)
        )
        n = int(seconds * sample_rate)
    n = max(1, int(n))
    rng = np.random.default_rng(seed)
    burst = rng.standard_normal(n)
    burst[: int(0.01 * sample_rate)] = 0.0
    return np.repeat(burst[:, None], max(1, int(channels)), axis=1)


def cross_correlation_delay(
    reference: np.ndarray,
    delayed: np.ndarray,
    max_lag: int | None = None,
) -> int:
    """Return the lag of the largest correlation peak, in samples.

    The lag is the argument of the peak of the cross correlation of the two
    signals.  A signal that is late by D samples peaks at the lag -D, so a
    negative number means that ``delayed`` runs **behind** ``reference``.

    The whole search range of the catalogue is searched when no range is
    given, so that every delay the product can declare is inside it.

    Parameters
    ----------
    reference:
        The reference signal, shape ``(n,)``.
    delayed:
        The delayed signal, shape ``(n,)``.
    max_lag:
        The largest lag that is looked at; the bound of the catalogue when
        omitted.

    Returns
    -------
    int
        The lag in samples; 0 when no peak stands out.
    """
    limit = latency_search_bound() if max_lag is None else int(max_lag)
    return _correlation_peak(reference, delayed, limit, -limit, limit)


def _correlation_peak(
    reference: np.ndarray,
    delayed: np.ndarray,
    max_lag: int,
    low: int,
    high: int,
) -> int:
    """Return the lag of the largest correlation peak inside ``[low, high]``.

    The band is cut to the lags that were actually computed: a band that
    reaches past the end of the searched range is a band with a hole in it,
    and a hole is where a wrong number comes from.
    """
    a = np.asarray(reference, dtype=np.float64).ravel()
    b = np.asarray(delayed, dtype=np.float64).ravel()
    n = min(a.size, b.size)
    if n == 0:
        return 0
    a = a[:n] - float(np.mean(a[:n]))
    b = b[:n] - float(np.mean(b[:n]))
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spectrum_a = np.fft.rfft(a, size)
    spectrum_b = np.fft.rfft(b, size)
    correlation = np.fft.irfft(spectrum_a * np.conj(spectrum_b), size)
    # The lags that are exact are the ones the two signals have in common:
    # a lag that is longer than the signal is a lag the circular
    # correlation wraps around and lies about.
    limit = int(min(int(max_lag), n - 1, size // 2 - 1))
    if limit < 1:
        return 0
    correlation = np.concatenate((correlation[-limit:], correlation[: limit + 1]))
    lags = np.arange(-limit, limit + 1)
    window = (lags >= int(low)) & (lags <= int(high))
    if not bool(window.any()):
        return 0
    masked = np.where(window, correlation, -np.inf)
    best = int(np.argmax(masked))
    if not np.isfinite(masked[best]) or float(masked[best]) <= 0.0:
        return 0
    return int(lags[best])


def refine_delay(
    reference: np.ndarray,
    delayed: np.ndarray,
    centre: int,
    window: int = 64,
    max_lag: int | None = None,
) -> int:
    """Return the lag of the output, searched around a known value.

    The cross correlation of a periodic signal has a peak at every multiple
    of the period, so the largest peak is not necessarily the delay.  When
    the module says how much it delays the signal, the search can be
    restricted to a narrow band around that number.

    Parameters
    ----------
    reference:
        The input signal.
    delayed:
        The output of the module.
    centre:
        The lag to search around.
    window:
        The half width of the search band.
    max_lag:
        The largest lag that is computed at all; the bound of the catalogue
        when omitted.

    Returns
    -------
    int
        The lag in samples.
    """
    limit = latency_search_bound() if max_lag is None else int(max_lag)
    return _correlation_peak(
        reference, delayed, limit, int(centre) - int(window), int(centre) + int(window)
    )


def measure_latency_samples(
    code: str,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    duration_s: float | None = None,
    block: int = BLOCK,
    prepare: Callable[[Any], None] | None = None,
) -> int:
    """Return the lag of the module against its input, in samples.

    A broadband noise burst is fed to the module and the lag is read from
    the cross correlation with the input.  The burst is long enough to hold
    the whole search range of the catalogue, so that every delay the
    product can declare is measured inside it.

    The number that comes back is the **lag** of the correlation peak: a
    signal that is late by D samples peaks at the lag -D.  Use
    :func:`measure_module_delay` for the delay itself.

    Parameters
    ----------
    code:
        The module code.
    sample_rate:
        The sample rate.
    channels:
        The channel count.
    duration_s:
        How much audio is fed to the module; long enough for the search
        range when omitted.
    block:
        The block size the module is driven with.
    prepare:
        A callable that puts a fresh module into the state the measurement
        expects (parameters, learned profile, selected region).

    Returns
    -------
    int
        The lag in samples; 0 when no peak stands out.
    """
    signal = latency_probe_signal(
        None if duration_s is None else int(duration_s * sample_rate),
        sample_rate=sample_rate,
        channels=channels,
    )
    module = _fresh(code, sample_rate, channels)
    if prepare is not None:
        prepare(module)
    out = stream_process(module, signal, block)
    reference = signal[:, 0]
    got = out[:, 0] if out.ndim > 1 else out
    return cross_correlation_delay(reference, got)


def measure_module_delay(
    code: str,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    duration_s: float | None = None,
    block: int = BLOCK,
    prepare: Callable[[Any], None] | None = None,
) -> int:
    """Return the delay the module puts into the signal, in samples.

    The measurement is the one of :func:`measure_latency_samples`, with the
    sign of the lag turned around: a module that is late by D samples
    returns D.

    Parameters
    ----------
    code:
        The module code.
    sample_rate:
        The sample rate.
    channels:
        The channel count.
    duration_s:
        How much audio is fed to the module; long enough for the search
        range when omitted.
    block:
        The block size the module is driven with.
    prepare:
        A callable that puts a fresh module into the state the measurement
        expects (parameters, learned profile, selected region).

    Returns
    -------
    int
        The delay in samples; 0 when no peak stands out.
    """
    return -int(
        measure_latency_samples(
            code,
            sample_rate=sample_rate,
            channels=channels,
            duration_s=duration_s,
            block=block,
            prepare=prepare,
        )
    )


# ---------------------------------------------------------------------------
# Block coherence
# ---------------------------------------------------------------------------
def _rms_db(x: np.ndarray) -> float:
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return FLOOR_DB
    value = float(np.sqrt(np.mean(arr * arr)))
    return 20.0 * math.log10(value) if value > 0.0 else FLOOR_DB


def measure_block_coherence(
    code: str,
    signal: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    block: int = BLOCK,
    prepare: Callable[[Any], None] | None = None,
) -> tuple[float, int, float]:
    """Return how much the output depends on the block size.

    The same audio is processed twice: once in one piece, once in blocks of
    ``block`` samples.  The two results are aligned by the delay between
    them and the residual is measured.

    Parameters
    ----------
    code:
        The module code.
    signal:
        The signal to process.
    sample_rate:
        The sample rate.
    channels:
        The channel count.
    block:
        The block size of the streaming pass.
    prepare:
        A callable that puts a fresh module into the state the measurement
        expects.

    Returns
    -------
    tuple
        ``(delta_db, lag, runaway_db)`` — the residual in decibels relative
        to the one-piece output, the alignment lag in samples, and how much
        louder the streamed output is than the input.
    """
    module_one = _fresh(code, sample_rate, channels)
    if prepare is not None:
        prepare(module_one)
    whole = np.asarray(module_one.process(signal), dtype=np.float64)
    module_two = _fresh(code, sample_rate, channels)
    if prepare is not None:
        prepare(module_two)
    streamed = stream_process(module_two, signal, block)
    runaway = _peak_delta_db(signal, streamed)
    n = min(whole.shape[0], streamed.shape[0])
    if n == 0:
        return FLOOR_DB, 0, runaway
    a = whole[:n]
    b = streamed[:n]
    lag = cross_correlation_delay(a[:, 0] if a.ndim > 1 else a, b[:, 0] if b.ndim > 1 else b)
    if lag > 0:
        a = a[lag:]
        b = b[: b.shape[0] - lag]
    elif lag < 0:
        b = b[-lag:]
        a = a[: a.shape[0] + lag]
    if a.size == 0:
        return FLOOR_DB, lag, runaway
    residual = a - b
    reference = _rms_db(a)
    if reference <= FLOOR_DB:
        # The one piece render is silent: the residual cannot be expressed
        # relative to it, so the input level is used instead.
        reference = _rms_db(np.asarray(signal, dtype=np.float64)[: a.shape[0], 0])
    residual_db = _rms_db(residual)
    if reference <= FLOOR_DB:
        return residual_db, lag, runaway
    return residual_db - reference, lag, runaway


def _peak_delta_db(signal: np.ndarray, out: np.ndarray) -> float:
    """Return how much louder the output is than the input, in decibels."""
    a = np.asarray(signal, dtype=np.float64)
    b = np.asarray(out, dtype=np.float64)
    if a.size == 0 or b.size == 0:
        return 0.0
    before = float(np.max(np.abs(a)))
    after = float(np.max(np.abs(b)))
    if before <= 0.0:
        return 0.0 if after <= 0.0 else 120.0
    if after <= 0.0:
        return -120.0
    return 20.0 * math.log10(after / before)


# ---------------------------------------------------------------------------
# Allocator independence
# ---------------------------------------------------------------------------
def poison_heap(megabytes: int = 64) -> None:
    """Fill the free heap with a sentinel and give it back to the allocator.

    A module that reads memory it never wrote picks the sentinel up, so two
    runs of the same code with the same input differ.  A module that writes
    every sample it returns does not.
    """
    block = 1 << 18  # 2 MiB of float64
    count = max(1, int(megabytes) * (1 << 20) // (block * 8))
    for _ in range(count):
        junk = np.full(block, 1.0e300, dtype=np.float64)
        junk[0] = float("nan")
        del junk


def measure_allocator_independence(
    code: str,
    signal: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    block: int = BLOCK,
    prepare: Callable[[Any], None] | None = None,
) -> bool:
    """Return True when the output does not depend on the allocator.

    The module is run twice with two fresh instances: once with a clean
    heap, once with a poisoned heap.  The two outputs have to be identical,
    bit for bit.

    Parameters
    ----------
    code:
        The module code.
    signal:
        The signal to process.
    sample_rate:
        The sample rate.
    channels:
        The channel count.
    block:
        The block size.
    prepare:
        A callable that puts a fresh module into the state the measurement
        expects.

    Returns
    -------
    bool
        True when both runs produced the same numbers.
    """
    module_a = _fresh(code, sample_rate, channels)
    if prepare is not None:
        prepare(module_a)
    out_a = np.asarray(module_a.process(np.array(signal, copy=True)), dtype=np.float64)
    poison_heap()
    module_b = _fresh(code, sample_rate, channels)
    if prepare is not None:
        prepare(module_b)
    out_b = np.asarray(module_b.process(np.array(signal, copy=True)), dtype=np.float64)
    if out_a.shape != out_b.shape:
        return False
    return bool(np.array_equal(out_a, out_b))


# ---------------------------------------------------------------------------
# The result record
# ---------------------------------------------------------------------------
@dataclass
class ModuleSurvey:
    """Everything the survey measured about one module."""

    code: str
    display_name: str = ""
    category: str = ""
    policy: str = ""
    probe: str = ""
    wanted_removed_db: float | None = None
    unwanted_removed_db: float | None = None
    wanted_before_db: float | None = None
    wanted_after_db: float | None = None
    unwanted_before_db: float | None = None
    unwanted_after_db: float | None = None
    cost_us: float = 0.0
    budget_us: float = BUDGET_US
    latency_declared: int = 0
    latency_measured: int = 0
    latency_tolerance: int = 0
    block_delta_db: float = 0.0
    block_lag: int = 0
    block_coherent: bool = True
    runaway_db: float = 0.0
    finite: bool = True
    same_length: bool = True
    allocator_independent: bool = True
    deterministic: bool = True
    error: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def budget_fraction(self) -> float:
        """The share of the realtime budget the module costs."""
        if self.budget_us <= 0.0:
            return 0.0
        return self.cost_us / self.budget_us

    @property
    def latency_error(self) -> int:
        """How far the measured delay is from the delay the module declares.

        A positive number means the module delays the signal longer than it
        says it does.
        """
        return int(self.latency_measured) - int(self.latency_declared)

    @property
    def over_budget(self) -> bool:
        """True when the module misses the 64 sample deadline."""
        return self.cost_us > self.budget_us

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "code": self.code,
            "display_name": self.display_name,
            "category": self.category,
            "policy": self.policy,
            "probe": self.probe,
            "wanted_removed_db": _round(self.wanted_removed_db),
            "unwanted_removed_db": _round(self.unwanted_removed_db),
            "wanted_before_db": _round(self.wanted_before_db, 2),
            "wanted_after_db": _round(self.wanted_after_db, 2),
            "unwanted_before_db": _round(self.unwanted_before_db, 2),
            "unwanted_after_db": _round(self.unwanted_after_db, 2),
            "cost_us": round(self.cost_us, 1),
            "budget_us": round(self.budget_us, 1),
            "budget_fraction": round(self.budget_fraction, 4),
            "over_budget": self.over_budget,
            "latency_declared": self.latency_declared,
            "latency_measured": self.latency_measured,
            "latency_error": self.latency_error,
            "latency_tolerance": self.latency_tolerance,
            "block_delta_db": _round(self.block_delta_db, 2),
            "block_lag": self.block_lag,
            "block_coherent": self.block_coherent,
            "runaway_db": _round(self.runaway_db, 2),
            "finite": self.finite,
            "same_length": self.same_length,
            "allocator_independent": self.allocator_independent,
            "deterministic": self.deterministic,
            "error": self.error,
            "checks": list(self.checks),
            "notes": list(self.notes),
        }


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
