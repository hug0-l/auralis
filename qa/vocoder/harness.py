"""
qa.vocoder.harness — the measurement library for the phase vocoder.

Every number in ``qa/vocoder/REPORT.md`` comes from this file, so it can be
re-run at any time:

    python qa/vocoder/run_vocoder.py

The harness measures five things:

* **pitch accuracy** — where a tone ends up after a pitch shift, in cents,
* **time accuracy** — where the clicks of a click train end up,
* **transient smear** — how wide an attack becomes,
* **spectral loss** — how much of the top band survives,
* **cost** — how long a block takes and what the price of the pieces is.

Nothing in here changes the behaviour of the vocoder; it only observes it.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore.dsp.phase_vocoder import PhaseVocoder  # noqa: E402

__all__ = [
    "SR",
    "PitchResult",
    "StretchResult",
    "TransientResult",
    "SpectrumResult",
    "CostResult",
    "tone",
    "click_train",
    "impulse",
    "dominant_frequency",
    "cents_error",
    "measure_pitch",
    "measure_stretch",
    "measure_transient",
    "measure_spectrum",
    "measure_cost",
    "edge_cases",
    "check_edges",
]

SR = 48000


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
def tone(freq: float, seconds: float = 1.0, sr: int = SR, amp: float = 0.5) -> np.ndarray:
    """A sine of ``freq`` Hz."""
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / float(sr)
    return amp * np.sin(2.0 * np.pi * float(freq) * t)


def click_train(period: float, count: int, sr: int = SR, amp: float = 0.9) -> np.ndarray:
    """``count`` impulses spaced ``period`` seconds apart."""
    n = int(round((count * period + period) * sr))
    x = np.zeros(n, dtype=np.float64)
    step = int(round(period * sr))
    for index in range(count):
        pos = index * step
        if pos < n:
            x[pos] = amp
    return x


def impulse(n: int = 4096, position: int | None = None, sr: int = SR,
            amp: float = 1.0) -> np.ndarray:
    """A single impulse inside a block of silence."""
    x = np.zeros(int(n), dtype=np.float64)
    x[int(position) if position is not None else int(n // 2)] = amp
    return x


def edge_signals(n: int = 4096, sr: int = SR) -> list[tuple[str, np.ndarray]]:
    """The five awkward inputs the module sweep uses."""
    silence = np.zeros(n, dtype=np.float64)
    constant = np.full(n, 0.5, dtype=np.float64)
    square = np.sign(np.sin(2.0 * np.pi * 220.0 * np.arange(n) / sr))
    click = np.zeros(n, dtype=np.float64)
    click[n // 2] = 1.0
    pure = 0.5 * np.sin(2.0 * np.pi * 1000.0 * np.arange(n) / sr)
    return [
        ("silence", silence),
        ("constant", constant),
        ("square", square),
        ("impulse", click),
        ("tone", pure),
    ]


# ---------------------------------------------------------------------------
# analysis helpers
# ---------------------------------------------------------------------------
def dominant_frequency(x: np.ndarray, sr: int = SR, nfft: int = 1 << 17) -> float:
    """The frequency of the strongest partial, refined by parabolic interpolation.

    The spectrum is zero padded so that the bin grid is far finer than the
    frequency resolution of the window, and the peak is then interpolated on
    the logarithmic magnitude, which is accurate to a small fraction of a bin.
    """
    sig = np.asarray(x, dtype=np.float64).ravel()
    if sig.size == 0:
        return 0.0
    window = np.hanning(sig.size)
    spec = np.abs(np.fft.rfft(sig * window, nfft))
    if spec.size == 0:
        return 0.0
    k = int(np.argmax(spec))
    if 0 < k < spec.size - 1:
        left = math.log(float(spec[k - 1]) + 1e-30)
        mid = math.log(float(spec[k]) + 1e-30)
        right = math.log(float(spec[k + 1]) + 1e-30)
        denom = left - 2.0 * mid + right
        delta = 0.0 if denom == 0.0 else 0.5 * (left - right) / denom
    else:
        delta = 0.0
    return float((k + delta) * sr / float(nfft))


def cents_error(measured: float, expected: float) -> float:
    """The error of ``measured`` against ``expected``, in cents."""
    if expected <= 0.0 or measured <= 0.0:
        return float("inf")
    return float(1200.0 * math.log2(measured / expected))


def band_levels(x: np.ndarray, sr: int = SR, nfft: int = 1 << 16) -> np.ndarray:
    """The level of every third of an octave band, in dB.

    The bands run from 20 Hz to the Nyquist frequency; the last band is
    closed on the right.
    """
    sig = np.asarray(x, dtype=np.float64).ravel()
    if sig.size == 0:
        return np.zeros(0)
    window = np.hanning(sig.size)
    spec = np.abs(np.fft.rfft(sig * window, nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / float(sr))
    low = 20.0
    high = sr / 2.0
    edges = []
    value = low
    while value < high:
        edges.append(value)
        value *= 2.0 ** (1.0 / 3.0)
    edges.append(high)
    out = np.zeros(len(edges) - 1, dtype=np.float64)
    for index in range(len(edges) - 1):
        mask = (freqs >= edges[index]) & (freqs < edges[index + 1])
        if not np.any(mask):
            out[index] = -300.0
        else:
            out[index] = 20.0 * math.log10(max(float(np.mean(spec[mask])), 1e-20))
    return out


def band_edges(sr: int = SR) -> np.ndarray:
    """The band edges that :func:`band_levels` uses."""
    edges = []
    value = 20.0
    while value < sr / 2.0:
        edges.append(value)
        value *= 2.0 ** (1.0 / 3.0)
    edges.append(sr / 2.0)
    return np.asarray(edges, dtype=np.float64)


def detect_onsets(
    x: np.ndarray,
    sr: int = SR,
    threshold: float = 0.25,
    separation_ms: float = 30.0,
) -> np.ndarray:
    """The positions (in samples) of the clicks in a click train.

    A stretched click rings, so the envelope of one click has several local
    maxima.  The picker therefore keeps the strongest maximum and ignores
    every other maximum that is closer than ``separation_ms`` to it, which is
    the usual refractory rule of a peak picker.
    """
    sig = np.abs(np.asarray(x, dtype=np.float64).ravel())
    if sig.size < 3:
        return np.zeros(0, dtype=np.intp)
    peak = float(np.max(sig))
    if peak <= 0.0:
        return np.zeros(0, dtype=np.intp)
    limit = threshold * peak
    inner = sig[1:-1]
    candidates = np.nonzero((inner >= limit) & (inner >= sig[:-2]) & (inner > sig[2:]))[0] + 1
    if candidates.size == 0:
        return np.zeros(0, dtype=np.intp)
    separation = max(1, int(round(separation_ms / 1000.0 * sr)))
    order = candidates[np.argsort(sig[candidates])[::-1]]
    kept: list[int] = []
    for index in order:
        position = int(index)
        if all(abs(position - other) > separation for other in kept):
            kept.append(position)
    return np.asarray(sorted(kept), dtype=np.intp)


# ---------------------------------------------------------------------------
# measurements
# ---------------------------------------------------------------------------
@dataclass
class PitchResult:
    """Where a tone ended up after a pitch shift."""

    label: str
    source_hz: float
    factor: float
    expected_hz: float
    measured_hz: float
    cents: float
    length_in: int
    length_out: int
    rms_db: float

    @property
    def ok(self) -> bool:
        return np.isfinite(self.cents) and abs(self.cents) <= 5.0


@dataclass
class StretchResult:
    """Where the clicks of a click train ended up after a time stretch."""

    label: str
    factor: float
    expected_period_ms: float
    measured_period_ms: float
    mean_period_ms: float
    spacing_error_ms: float
    spacing_error_percent: float
    mean_error_percent: float
    position_error_ms: float
    clicks_in: int
    clicks_out: int
    length_in: int
    length_out: int

    @property
    def ok(self) -> bool:
        return (
            abs(self.mean_error_percent) < 1.5
            and self.position_error_ms < 17.0
            and self.clicks_out >= self.clicks_in - 2
            and self.length_out == int(round(self.length_in * self.factor))
        )


@dataclass
class TransientResult:
    """How wide an attack became."""

    label: str
    factor: float
    width_ms_in: float
    width_ms_out: float
    width_ratio: float
    smear_ratio: float
    spread_ms: float
    pre_echo_db: float
    post_echo_db: float
    peak_in: float
    peak_out: float
    peak_loss_db: float
    energy_1ms_percent: float


@dataclass
class SpectrumResult:
    """The level of every band before and after the operation."""

    label: str
    factor: float
    edges: np.ndarray
    db_in: np.ndarray
    db_out: np.ndarray
    top_band_db_in: float
    top_band_db_out: float
    top_band_loss_db: float
    worst_band_loss_db: float
    worst_band_low_hz: float

    @property
    def losses(self) -> np.ndarray:
        return self.db_in - self.db_out


@dataclass
class CostResult:
    """What one block costs and what the pieces of it cost."""

    block: int
    fft_size: int
    hop: int
    budget_us: float
    total_us: float
    analyze_us: float
    transients_us: float
    median_us: float
    loop_us: float
    lock_us: float
    overlap_us: float
    frames: int

    @property
    def budget_percent(self) -> float:
        return 100.0 * self.total_us / self.budget_us if self.budget_us > 0 else 0.0

    @property
    def median_percent(self) -> float:
        return 100.0 * self.median_us / self.total_us if self.total_us > 0 else 0.0


@dataclass
class EdgeResult:
    """The result of one awkward input."""

    name: str
    ok: bool
    message: str
    length_ok: bool
    finite: bool
    peak: float


# ---------------------------------------------------------------------------
# the measurements
# ---------------------------------------------------------------------------
def _without_scipy():
    """Hide SciPy so that the numpy fallbacks of the core are used.

    Returns a context manager that restores the modules afterwards.
    """
    import contextlib

    @contextlib.contextmanager
    def manager():
        saved = {
            name: module
            for name, module in list(sys.modules.items())
            if name == "scipy" or name.startswith("scipy.")
        }
        for name in saved:
            sys.modules[name] = None
        try:
            yield
        finally:
            for name in list(sys.modules):
                if name == "scipy" or name.startswith("scipy."):
                    del sys.modules[name]
            sys.modules.update(saved)

    return manager()


def measure_pitch(
    source_hz: float = 440.0,
    factor: float = 1.5,
    seconds: float = 1.0,
    sr: int = SR,
    fft_size: int = 2048,
    method: str = "resample",
    label: str | None = None,
    fallback: bool = False,
) -> PitchResult:
    """Shift a tone by ``factor`` and report where it landed.

    ``method`` is the one of :meth:`PhaseVocoder.pitch_shift`: ``'resample'``
    is the default of the class, ``'spectral'`` is the bin shift of
    :meth:`PhaseVocoder.synthesize`.  With ``fallback`` the SciPy modules are
    hidden, so that the numpy only resampler is measured.
    """
    x = tone(source_hz, seconds, sr)
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    if fallback:
        with _without_scipy():
            if method == "spectral":
                state = vocoder.analyze(x)
                y = vocoder.synthesize(state, stretch=1.0, pitch=factor)
            else:
                semitones = 12.0 * math.log2(factor)
                y = vocoder.pitch_shift(x, semitones, method=method)
    elif method == "spectral":
        state = vocoder.analyze(x)
        y = vocoder.synthesize(state, stretch=1.0, pitch=factor)
    else:
        semitones = 12.0 * math.log2(factor)
        y = vocoder.pitch_shift(x, semitones, method=method)
    expected = source_hz * factor
    measured = dominant_frequency(y, sr)
    rms = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    return PitchResult(
        label=label or f"{method} {source_hz:.0f} Hz x{factor:g}",
        source_hz=source_hz,
        factor=factor,
        expected_hz=expected,
        measured_hz=measured,
        cents=cents_error(measured, expected),
        length_in=int(x.size),
        length_out=int(y.size),
        rms_db=20.0 * math.log10(max(rms, 1e-20)),
    )


def measure_stretch(
    period_ms: float = 100.0,
    count: int = 10,
    factor: float = 2.0,
    sr: int = SR,
    fft_size: int = 2048,
    label: str | None = None,
) -> StretchResult:
    """Stretch a click train and report the spacing and the position of the clicks.

    The spacing is the distance between two neighbouring clicks, the position
    error is how far the clicks are from the place the stretch puts them.
    """
    x = click_train(period_ms / 1000.0, count, sr)
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    y = vocoder.time_stretch(x, factor)
    hits = detect_onsets(y, sr)
    if hits.size >= 2:
        spacings = np.diff(hits) / float(sr) * 1000.0
        measured = float(np.median(spacings))
        # the mean over the whole train is free of the hop quantisation
        mean_period = float((hits[-1] - hits[0]) / float(sr) * 1000.0 / (hits.size - 1))
    else:
        measured = 0.0
        mean_period = 0.0
    # where the clicks should be: the first one stays where it was, the rest
    # follow the stretch
    ideal = np.arange(count, dtype=np.float64) * period_ms * factor
    if hits.size:
        offset = float(hits[0]) / float(sr) * 1000.0
        found = hits / float(sr) * 1000.0 - offset
        # every click is compared with the ideal grid point closest to it
        nearest = ideal[np.abs(found[:, None] - ideal[None, :]).argmin(axis=1)]
        position_error = float(np.max(np.abs(found - nearest)))
    else:
        position_error = float("inf")
    expected = period_ms * factor
    return StretchResult(
        label=label or f"click train {period_ms:.0f} ms x{factor:g}",
        factor=factor,
        expected_period_ms=expected,
        measured_period_ms=measured,
        mean_period_ms=mean_period,
        spacing_error_ms=measured - expected,
        spacing_error_percent=(100.0 * (measured - expected) / expected) if expected else 0.0,
        mean_error_percent=(100.0 * (mean_period - expected) / expected) if expected else 0.0,
        position_error_ms=position_error,
        clicks_in=count,
        clicks_out=int(hits.size),
        length_in=int(x.size),
        length_out=int(y.size),
    )


def _width_ms(x: np.ndarray, sr: int, level: float = 0.1) -> float:
    """The width of the strongest transient, at ``level`` of its peak."""
    sig = np.abs(np.asarray(x, dtype=np.float64).ravel())
    if sig.size == 0:
        return 0.0
    peak = float(np.max(sig))
    if peak <= 0.0:
        return 0.0
    limit = level * peak
    index = int(np.argmax(sig))
    start = index
    while start > 0 and sig[start] > limit:
        start -= 1
    stop = index
    while stop < sig.size - 1 and sig[stop] > limit:
        stop += 1
    return float((stop - start) / float(sr) * 1000.0)


def _spread_ms(x: np.ndarray, sr: int, centre: int, span_ms: float = 40.0) -> float:
    """The energy weighted RMS time spread around ``centre``, in milliseconds."""
    sig = np.asarray(x, dtype=np.float64).ravel()
    half = int(round(span_ms / 1000.0 * sr))
    lo = max(0, centre - half)
    hi = min(sig.size, centre + half)
    if hi <= lo:
        return 0.0
    energy = np.square(sig[lo:hi])
    total = float(np.sum(energy))
    if total <= 0.0:
        return 0.0
    times = (np.arange(lo, hi, dtype=np.float64) - centre) / float(sr) * 1000.0
    return float(math.sqrt(float(np.sum(times * times * energy)) / total))


def measure_transient(
    sr: int = SR,
    fft_size: int = 2048,
    factor: float = 2.0,
    label: str | None = None,
) -> TransientResult:
    """Stretch a single impulse and report how wide the attack became.

    The impulse sits at a known place in the input, so the place where the
    attack has to appear in the output is known as well.  The metrics are

    * ``width_ms`` — the width of the attack at 10 % of its peak,
    * ``spread_ms`` — the energy weighted RMS spread around the attack,
    * ``pre_echo_db`` — the level before the attack relative to the attack,
    * ``post_echo_db`` — the level after the attack relative to the attack.
    """
    n = 8192
    attack_in = n // 4
    x = impulse(n, attack_in, sr)
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    y = vocoder.time_stretch(x, factor)
    nominal = int(round(attack_in * factor))
    window = max(1, int(0.005 * sr))
    lo = max(0, nominal - window)
    hi = min(y.size, nominal + window)
    if hi > lo:
        attack_out = lo + int(np.argmax(np.abs(y[lo:hi])))
    else:
        attack_out = min(nominal, max(0, y.size - 1))

    width_in = _width_ms(x, sr)
    width_out = _width_ms(y, sr)
    peak_out = float(np.max(np.abs(y))) if y.size else 0.0
    peak_in = float(np.max(np.abs(x)))

    guard = max(1, int(0.0005 * sr))
    pre_lo = max(0, attack_out - int(0.010 * sr))
    pre_hi = max(0, attack_out - guard)
    post_lo = min(y.size, attack_out + guard)
    post_hi = min(y.size, attack_out + int(0.010 * sr))
    core_lo = max(0, attack_out - guard)
    core_hi = min(y.size, attack_out + guard)
    core = float(np.max(np.abs(y[core_lo:core_hi]))) if core_hi > core_lo else 0.0
    pre = float(np.max(np.abs(y[pre_lo:pre_hi]))) if pre_hi > pre_lo else 0.0
    post = float(np.max(np.abs(y[post_lo:post_hi]))) if post_hi > post_lo else 0.0
    if core > 0.0:
        pre_db = 20.0 * math.log10(max(pre, 1e-12) / core)
        post_db = 20.0 * math.log10(max(post, 1e-12) / core)
    else:
        pre_db = -300.0
        post_db = -300.0

    energy = np.square(y)
    total = float(np.sum(energy))
    if total > 0.0:
        window1 = max(1, int(0.001 * sr))
        lo1 = max(0, attack_out - window1)
        hi1 = min(y.size, attack_out + window1)
        share = 100.0 * float(np.sum(energy[lo1:hi1])) / total
    else:
        share = 0.0

    expected_width = width_in * factor
    return TransientResult(
        label=label or f"impulse stretched x{factor:g}",
        factor=factor,
        width_ms_in=width_in,
        width_ms_out=width_out,
        width_ratio=(width_out / expected_width) if expected_width > 0 else float("inf"),
        smear_ratio=(width_out / width_in) if width_in > 0 else float("inf"),
        spread_ms=_spread_ms(y, sr, attack_out),
        pre_echo_db=pre_db,
        post_echo_db=post_db,
        peak_in=peak_in,
        peak_out=peak_out,
        peak_loss_db=20.0 * math.log10(max(peak_out, 1e-12) / max(peak_in, 1e-12)),
        energy_1ms_percent=share,
    )


def measure_spectrum(
    sr: int = SR,
    fft_size: int = 2048,
    factor: float = 1.0,
    method: str = "resample",
    label: str | None = None,
    top_lo_hz: float = 16000.0,
) -> SpectrumResult:
    """Compare the spectrum of a wide band signal before and after a shift."""
    rng = np.random.default_rng(20240607)
    n = SR // 2
    x = rng.standard_normal(n) * 0.1
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    if method == "spectral":
        state = vocoder.analyze(x)
        y = vocoder.synthesize(state, stretch=1.0, pitch=factor)
    else:
        semitones = 12.0 * math.log2(factor)
        y = vocoder.pitch_shift(x, semitones, method=method)
    edges = band_edges(sr)
    db_in = band_levels(x, sr)
    db_out = band_levels(y, sr)
    top = edges[:-1] >= top_lo_hz
    if np.any(top):
        top_in = float(np.max(db_in[top]))
        top_out = float(np.max(db_out[top]))
    else:
        top_in = float(np.max(db_in))
        top_out = float(np.max(db_out))
    losses = db_in - db_out
    worst = int(np.argmax(losses))
    return SpectrumResult(
        label=label or f"noise, {method} x{factor:g}",
        factor=factor,
        edges=edges,
        db_in=db_in,
        db_out=db_out,
        top_band_db_in=top_in,
        top_band_db_out=top_out,
        top_band_loss_db=top_in - top_out,
        worst_band_loss_db=float(losses[worst]),
        worst_band_low_hz=float(edges[worst]),
    )


def _best_us(fn: Callable[[], object], repeats: int = 5, inner: int = 20) -> float:
    """The fastest of ``repeats`` runs of ``inner`` calls, in microseconds."""
    import time

    best = math.inf
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        elapsed = (time.perf_counter() - start) / inner
        best = min(best, elapsed)
    return best * 1e6


def measure_cost(
    block: int = 64,
    sr: int = SR,
    fft_size: int = 2048,
    factor: float = 1.5,
    repeats: int = 5,
    inner: int = 20,
    signal_seconds: float | None = None,
) -> CostResult:
    """What one block of ``block`` samples costs, and what the pieces cost.

    ``signal_seconds`` replaces the block with a longer signal, which is what
    a block processor accumulates over one second of audio.  The transient
    detector needs at least three frames, so on a short block it never runs
    and the median is not part of the cost at all.
    """
    import time

    rng = np.random.default_rng(7)
    length = int(round(signal_seconds * sr)) if signal_seconds else block
    x = (rng.standard_normal(length) * 0.1).astype(np.float64)
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    state = vocoder.analyze(x)

    analyze_us = _best_us(lambda: vocoder.analyze(x), repeats, inner)
    total_us = _best_us(lambda: vocoder.synthesize(state, stretch=factor), repeats, inner)
    transients_us = _best_us(lambda: vocoder._detect_transients(state.mag), repeats, inner)

    mag = state.mag
    if mag.shape[0] >= 3:
        flux = np.sum(np.maximum(np.diff(mag, axis=0), 0.0), axis=1)
        energy = np.sum(mag[1:], axis=1) + 1e-20
        flux = flux / energy
        median_us = _best_us(lambda: float(np.median(flux)), repeats, inner)
        percentile_us = _best_us(lambda: float(np.percentile(flux, 90.0)), repeats, inner)
        median_us += percentile_us
    else:
        median_us = 0.0

    phase = state.phase
    advance = vocoder._phase_advance(phase)
    n_frames = max(1, int(state.mag.shape[0]))
    lock_us = 0.0
    for index in range(min(n_frames, 32)):
        row = index % n_frames
        lock_us += _best_us(
            lambda row=row: vocoder._lock_phase(advance[row], phase[row], state.mag[row]),
            repeats=2,
            inner=5,
        )
    lock_us = lock_us / max(1, min(n_frames, 32))

    out_mag = np.abs(state.mag)
    out_phase = state.phase
    overlap_us = _best_us(
        lambda: vocoder._stft.synthesize(out_mag, out_phase, length), repeats, inner
    )

    loop_us = max(0.0, total_us - transients_us - overlap_us)
    budget_us = block / float(sr) * 1e6
    return CostResult(
        block=block,
        fft_size=fft_size,
        hop=fft_size // 4,
        budget_us=budget_us,
        total_us=total_us,
        analyze_us=analyze_us,
        transients_us=transients_us,
        median_us=median_us,
        loop_us=loop_us,
        lock_us=lock_us,
        overlap_us=overlap_us,
        frames=int(state.mag.shape[0]),
    )


# ---------------------------------------------------------------------------
# the awkward inputs
# ---------------------------------------------------------------------------
def check_edges(
    sr: int = SR,
    fft_size: int = 2048,
    factor: float = 1.5,
    n: int = 4096,
) -> list[EdgeResult]:
    """Run the five awkward inputs through the vocoder and check the output."""
    results: list[EdgeResult] = []
    vocoder = PhaseVocoder(fft_size, fft_size // 4, sr)
    for name, signal in edge_signals(n, sr):
        try:
            state = vocoder.analyze(signal)
            stretched = vocoder.synthesize(state, stretch=factor)
            shifted = vocoder.pitch_shift(signal, 12.0 * math.log2(factor))
        except Exception as error:  # pragma: no cover - reported, not raised
            results.append(EdgeResult(name, False, f"raised {error!r}", False, False, 0.0))
            continue
        expected_length = int(round(signal.size * factor))
        length_ok = stretched.size == expected_length and shifted.size == signal.size
        finite = bool(
            np.all(np.isfinite(stretched)) and np.all(np.isfinite(shifted))
        )
        peak = float(np.max(np.abs(stretched))) if stretched.size else 0.0
        message = ""
        if not length_ok:
            message = (
                f"length {stretched.size} != {expected_length} (stretch), "
                f"{shifted.size} != {signal.size} (pitch)"
            )
        elif not finite:
            message = "non finite samples"
        results.append(
            EdgeResult(name, length_ok and finite, message, length_ok, finite, peak)
        )
    return results


def edge_cases(n: int = 4096, sr: int = SR) -> list[tuple[str, np.ndarray]]:
    """The awkward inputs, as a list of ``(name, signal)``."""
    return edge_signals(n, sr)
