"""
qa.rustle.harness — measurement harness for the De-Rustle module.

The harness measures what ``aurcore.modules.de_rustle`` *does* to a signal,
without changing the module: it drives the public ``process`` entry point and
reads the internal gain curve through a thin subclass that only *observes*.

The synthetic case
------------------
The case is a close microphone recording:

* a **wanted musical part** — a sustained note (fundamental plus five
  harmonics) that sits in the low band, and
* a **rustle** — broadband friction noise with a burst envelope and a slow
  amplitude modulation, decorrelated between the two channels the way a rub on
  a microphone mount is.

The two parts are separated by frequency, so the two questions can be asked
independently:

``rustle_removed_db``
    Energy of the band that carries **only** rustle (4 - 12 kHz), after minus
    before.  Negative means the rustle went away.
``wanted_removed_db``
    Energy of the narrow windows around the wanted partials, after minus
    before.  Negative means the module damaged the part the user wants.

Measured quantities
-------------------
``rustle_removed_db`` / ``wanted_removed_db``
    The two numbers above, in dB.
``snr_gain_db``
    ``SNR_after - SNR_before`` where the SNR is the wanted band energy minus
    the rustle band energy.
``latency_samples``
    Delay of the module measured on the signal (cross correlation).
``block_coherence_db``
    Difference between processing the signal in one block and processing it
    in blocks of N samples, in dB relative to the signal.  Zero means the
    module is block coherent.
``cost_us``
    Wall clock time of one ``process()`` call, in microseconds.

Everything is deterministic: the same seed gives the same case, the same
numbers.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

# Make the repository importable when the harness is run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from aurcore.modules import de_rustle as _module  # noqa: E402
from aurcore.modules.de_rustle import DeRustle  # noqa: E402

#: Analysis window of the module, in samples.
NFFT = int(_module._NFFT)

#: Hop between the frames of the module, in samples.
HOP = int(_module._HOP)

#: The look ahead the module needs, in samples (half the analysis window).
LOOKAHEAD = NFFT // 2

__all__ = [
    "SR",
    "NFFT",
    "HOP",
    "LOOKAHEAD",
    "WANTED_PARTIALS",
    "WANTED_BAND",
    "RUSTLE_BAND",
    "Case",
    "RustleProbe",
    "band_energy_db",
    "build_case",
    "build_cases",
    "edge_cases",
    "evaluate",
    "measure_block_coherence",
    "measure_cost",
    "measure_determinism",
    "measure_latency",
    "measure_lookahead",
    "measure_split",
    "partial_energy_db",
    "DEFAULT_CONFIG",
]


#: Sample rate used by every measurement.
SR = 48_000

#: Duration of the synthetic case, in seconds.
DURATION = 4.0

#: Fundamental of the wanted musical part, in Hz (A3).
F0 = 220.0

#: Number of harmonics of the wanted part.
N_HARMONICS = 6

#: The wanted partials, in Hz.
WANTED_PARTIALS: tuple[float, ...] = tuple(F0 * k for k in range(1, N_HARMONICS + 1))

#: Half width of the measurement window around a wanted partial, in Hz.
PARTIAL_WINDOW_HZ = 45.0

#: Band that carries the wanted part (and some rustle).
WANTED_BAND = (150.0, 1_500.0)

#: Band that carries rustle only: the wanted part has nothing above 1.4 kHz.
RUSTLE_BAND = (4_000.0, 12_000.0)

#: Level of the wanted part, linear RMS.
WANTED_RMS = 0.12

#: Level of the rustle relative to the wanted part, in dB.
RUSTLE_BELOW_DB = -20.0

#: Parameters the harness starts from (the shipped defaults).
DEFAULT_CONFIG: dict[str, object] = {
    "Reduction Strength": 0.5,
    "Ambience Preservation": 0.5,
    "Separation Algorithm": "Channel Independent",
}

#: Analysis window used by the band-energy measurement (samples).
_MEAS_FFT = 2048

#: Hop of the band-energy measurement (samples).
_MEAS_HOP = 512


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------
def _musical_part(n: int) -> np.ndarray:
    """A sustained musical note: the fundamental plus five harmonics."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    out = np.zeros(n, dtype=np.float64)
    for k in range(1, N_HARMONICS + 1):
        amp = 0.30 / k
        out += amp * np.sin(2.0 * math.pi * F0 * k * t + 0.31 * k)
    rms = float(np.sqrt(np.mean(out * out)))
    return out * (WANTED_RMS / max(rms, 1e-12))


def _rustle(n: int, rng: np.random.Generator) -> np.ndarray:
    """Broadband friction noise with a burst envelope and a slow modulation."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    noise = rng.standard_normal(n)

    # Band limit the noise to 120 Hz .. 14 kHz, the range a clothing rub and
    # a harness rumble actually live in.
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spectrum[(freqs < 120.0) | (freqs > 14_000.0)] = 0.0
    noise = np.fft.irfft(spectrum, n=n)

    # Burst envelope: a rise, a hold and a fall.
    env = np.ones(n, dtype=np.float64)
    ramp = int(0.4 * SR)
    fall = int(0.4 * SR)
    env[:ramp] = np.linspace(0.0, 1.0, ramp) ** 2
    env[n - fall :] = np.linspace(1.0, 0.0, fall) ** 2

    # Slow amplitude modulation: the rub is never constant.
    env *= 0.65 + 0.35 * np.sin(2.0 * math.pi * 3.0 * t + 0.4)

    out = noise * env
    rms = float(np.sqrt(np.mean(out * out)))
    target = WANTED_RMS * (10.0 ** (RUSTLE_BELOW_DB / 20.0))
    return out * (target / max(rms, 1e-12))


# ---------------------------------------------------------------------------
# The case
# ---------------------------------------------------------------------------
@dataclass
class Case:
    """One synthetic measurement case.

    Attributes
    ----------
    name:
        Identifier of the case.
    x:
        The signal to process, shape ``(n_samples, channels)``.
    wanted:
        The musical part, shape ``(n_samples, channels)``.
    rustle:
        The rustle, shape ``(n_samples, channels)``.
    """

    name: str
    x: np.ndarray
    wanted: np.ndarray
    rustle: np.ndarray

    @property
    def n_samples(self) -> int:
        """Length of the case in samples."""
        return int(self.x.shape[0])

    @property
    def channels(self) -> int:
        """Number of channels of the case."""
        return int(self.x.shape[1])


def build_case(
    name: str = "rustle_under_note",
    seed: int = 20_260_321,
    channels: int = 2,
    duration: float = DURATION,
) -> Case:
    """Build the synthetic rustle case.

    The musical part is centred (the same in both channels) and the rustle is
    decorrelated between the channels, which is what a rub on a microphone
    mount looks like.
    """
    n = int(round(duration * SR))
    rng = np.random.default_rng(seed)

    wanted_mono = _musical_part(n)
    rustle_mono = _rustle(n, rng)

    if channels <= 1:
        wanted = wanted_mono[:, None]
        rustle = rustle_mono[:, None]
    else:
        # A second, independent rub for the second channel.
        rustle_r = _rustle(n, np.random.default_rng(seed + 1))
        wanted = np.repeat(wanted_mono[:, None], channels, axis=1)
        rustle = np.stack([rustle_mono, rustle_r], axis=1)[:, :channels]

    x = wanted + rustle
    return Case(name=name, x=x, wanted=wanted, rustle=rustle)


def build_cases(seed: int = 20_260_321, duration: float = DURATION) -> list[Case]:
    """Build the measurement cases: mono and stereo."""
    return [
        build_case("rustle_mono", seed=seed, channels=1, duration=duration),
        build_case("rustle_stereo", seed=seed, channels=2, duration=duration),
    ]


# ---------------------------------------------------------------------------
# Module probe (observation only)
# ---------------------------------------------------------------------------
class RustleProbe(DeRustle):
    """De-Rustle with the internal gain curve captured.

    The subclass does not change the algorithm: it stores the gain curve and
    the estimated noise floor so that the harness can look at what the module
    decided instead of guessing it from the output signal.
    """

    def __init__(self, sample_rate: int = SR, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.gain_curves: list[np.ndarray] = []
        self.floors: list[np.ndarray] = []

    def reset(self) -> None:
        """Drop the module state and the captured curves."""
        super().reset()
        self.gain_curves.clear()
        self.floors.clear()

    def _gain_curve(
        self,
        mag: np.ndarray,
        floor: np.ndarray,
        tonality: np.ndarray | None = None,
    ) -> np.ndarray:
        """Compute the gain curve and remember it."""
        curve = super()._gain_curve(mag, floor, tonality)
        self.gain_curves.append(np.array(curve, dtype=np.float64, copy=True))
        self.floors.append(np.array(floor, dtype=np.float64, copy=True))
        return curve

    @property
    def curve(self) -> np.ndarray:
        """The captured gain curve, shape ``(frames, bins)``, in dB."""
        if not self.gain_curves:
            return np.zeros((0, 0), dtype=np.float64)
        return np.concatenate(self.gain_curves, axis=0)


# ---------------------------------------------------------------------------
# Energy measurement
# ---------------------------------------------------------------------------
def _mono(x: np.ndarray) -> np.ndarray:
    """First channel of ``x`` as a 1-D array."""
    arr = np.asarray(x, dtype=np.float64)
    return arr[:, 0] if arr.ndim > 1 else arr


def _spectrum(x: np.ndarray, n_fft: int = _MEAS_FFT, hop: int = _MEAS_HOP):
    """Return the Hann windowed STFT of ``x`` and its frequency axis."""
    seg = _mono(x)
    if seg.size < n_fft:
        seg = np.concatenate([seg, np.zeros(n_fft - seg.size, dtype=np.float64)])
    window = np.hanning(n_fft)
    n_frames = 1 + (seg.size - n_fft) // hop
    view = np.lib.stride_tricks.sliding_window_view(seg, n_fft)[::hop][:n_frames]
    spectrum = np.fft.rfft(view * window, axis=-1)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    return spectrum, freqs


def band_energy_db(x: np.ndarray, f_lo: float, f_hi: float) -> float:
    """Energy inside a frequency band, in dB.

    The absolute value is an arbitrary power unit; only the ratio between two
    measurements of the same length is meaningful.
    """
    spectrum, freqs = _spectrum(x)
    mask = (freqs >= f_lo) & (freqs <= f_hi)
    power = float(np.sum(np.abs(spectrum[:, mask]) ** 2))
    return 10.0 * math.log10(max(power, 1e-30))


def partial_energy_db(x: np.ndarray, partials: Sequence[float] = WANTED_PARTIALS) -> float:
    """Energy in narrow windows around the wanted partials, in dB."""
    spectrum, freqs = _spectrum(x)
    total = 0.0
    for centre in partials:
        mask = np.abs(freqs - centre) <= PARTIAL_WINDOW_HZ
        if np.any(mask):
            total += float(np.sum(np.abs(spectrum[:, mask]) ** 2))
    return 10.0 * math.log10(max(total, 1e-30))


# ---------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------
@dataclass
class Result:
    """The measurement result of one case under one configuration."""

    case: str
    metrics: dict[str, float] = field(default_factory=dict)

    def __getitem__(self, key: str) -> float:
        """Read one metric."""
        return self.metrics[key]

    def get(self, key: str, default: float = 0.0) -> float:
        """Read one metric with a default."""
        return self.metrics.get(key, default)

    def as_row(self) -> dict[str, float]:
        """The metrics as a flat dictionary."""
        return dict(self.metrics)


def _apply_config(module: DeRustle, config: dict[str, object]) -> None:
    """Set every parameter of ``config`` on ``module``."""
    for name, value in config.items():
        if module.params.has(name):
            module.params.set(name, value)


def evaluate(
    case: Case,
    config: dict[str, object] | None = None,
    module: DeRustle | None = None,
    block_size: int | None = None,
) -> Result:
    """Run one case through the module and measure the result.

    Parameters
    ----------
    case:
        The case to process.
    config:
        Parameter overrides; the shipped defaults are used when omitted.
    module:
        An existing module instance (a fresh one is created when omitted).
    block_size:
        When given, the signal is processed in blocks of this size instead of
        in one go.

    Returns
    -------
    Result
        The measured metrics of the case.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    probe = module if module is not None else RustleProbe(SR, case.channels)
    _apply_config(probe, cfg)

    x = np.asarray(case.x, dtype=np.float64)
    if block_size:
        pieces = []
        for start in range(0, x.shape[0], block_size):
            block = x[start : start + block_size]
            pieces.append(np.asarray(probe.process(block), dtype=np.float64))
        y = np.concatenate(pieces, axis=0)
    else:
        y = np.asarray(probe.process(x), dtype=np.float64)

    n = min(x.shape[0], y.shape[0])
    xa = x[:n]
    ya = y[:n]

    rustle_in = band_energy_db(xa, *RUSTLE_BAND)
    rustle_out = band_energy_db(ya, *RUSTLE_BAND)
    wanted_in = partial_energy_db(xa)
    wanted_out = partial_energy_db(ya)
    band_in = band_energy_db(xa, *WANTED_BAND)
    band_out = band_energy_db(ya, *WANTED_BAND)

    snr_in = band_in - rustle_in
    snr_out = band_out - rustle_out

    rms_in = float(np.sqrt(np.mean(xa ** 2)))
    rms_out = float(np.sqrt(np.mean(ya ** 2)))

    metrics = {
        "rustle_in_db": rustle_in,
        "rustle_out_db": rustle_out,
        "rustle_removed_db": rustle_out - rustle_in,
        "wanted_in_db": wanted_in,
        "wanted_out_db": wanted_out,
        "wanted_removed_db": wanted_out - wanted_in,
        "wanted_band_in_db": band_in,
        "wanted_band_out_db": band_out,
        "wanted_band_change_db": band_out - band_in,
        "snr_in_db": snr_in,
        "snr_out_db": snr_out,
        "snr_gain_db": snr_out - snr_in,
        "rms_in": rms_in,
        "rms_out": rms_out,
        "level_change_db": 20.0 * math.log10(max(rms_out, 1e-12) / max(rms_in, 1e-12)),
        "latency_samples": float(probe.latency_samples),
        "finite": 1.0 if np.all(np.isfinite(y)) else 0.0,
        "length_ok": 1.0 if y.shape == x.shape else 0.0,
    }
    return Result(case=case.name, metrics=metrics)


# ---------------------------------------------------------------------------
# Latency, block coherence and cost
# ---------------------------------------------------------------------------
def measure_latency(x: np.ndarray, y: np.ndarray, max_lag: int = 4096) -> int:
    """Delay between ``x`` and ``y`` in samples (cross correlation peak)."""
    a = _mono(x) - float(np.mean(_mono(x)))
    b = _mono(y) - float(np.mean(_mono(y)))
    n = min(a.size, b.size)
    a = a[:n]
    b = b[:n]
    spectrum = np.fft.rfft(a) * np.conj(np.fft.rfft(b))
    correlation = np.fft.irfft(spectrum, n=n)
    correlation = np.concatenate([correlation[-max_lag:], correlation[: max_lag + 1]])
    lags = np.arange(-max_lag, max_lag + 1)
    return int(lags[int(np.argmax(correlation))])


def measure_block_coherence(
    case: Case,
    config: dict[str, object] | None = None,
    block_size: int = 512,
) -> dict[str, float]:
    """Compare one block processing with small block processing.

    A block coherent module returns the same samples no matter how the host
    chops the stream.  The returned dictionary holds the largest sample
    difference and the RMS difference in dB relative to the signal.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    x = np.asarray(case.x, dtype=np.float64)

    whole = RustleProbe(SR, case.channels)
    _apply_config(whole, cfg)
    y_whole = np.asarray(whole.process(x), dtype=np.float64)

    chunked = RustleProbe(SR, case.channels)
    _apply_config(chunked, cfg)
    pieces = []
    for start in range(0, x.shape[0], block_size):
        pieces.append(
            np.asarray(chunked.process(x[start : start + block_size]), dtype=np.float64)
        )
    y_chunked = np.concatenate(pieces, axis=0)

    m = min(y_whole.shape[0], y_chunked.shape[0])
    diff = y_whole[:m] - y_chunked[:m]
    rms_ref = float(np.sqrt(np.mean(y_whole[:m] ** 2))) or 1e-12
    rms_diff = float(np.sqrt(np.mean(diff ** 2)))
    return {
        "block_size": float(block_size),
        "max_diff": float(np.max(np.abs(diff))) if diff.size else 0.0,
        "rms_diff": rms_diff,
        "rms_diff_db": 20.0 * math.log10(max(rms_diff, 1e-12) / rms_ref),
    }


def measure_split(case: Case, config: dict[str, object] | None = None) -> dict[str, float]:
    """Compare the whole signal with the same signal cut into two blocks.

    This is the "two blocks of the same signal" case: the signal is handed to
    the module once and then handed to it in two halves.  A module that
    carries its analysis over the block boundary returns the same samples.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    x = np.asarray(case.x, dtype=np.float64)
    half = x.shape[0] // 2

    whole = RustleProbe(SR, case.channels)
    _apply_config(whole, cfg)
    y_whole = np.asarray(whole.process(x), dtype=np.float64)

    split = RustleProbe(SR, case.channels)
    _apply_config(split, cfg)
    pieces = [
        np.asarray(split.process(x[:half]), dtype=np.float64),
        np.asarray(split.process(x[half:]), dtype=np.float64),
    ]
    y_split = np.concatenate(pieces, axis=0)

    m = min(y_whole.shape[0], y_split.shape[0])
    diff = y_whole[:m] - y_split[:m]
    rms_ref = float(np.sqrt(np.mean(y_whole[:m] ** 2))) or 1e-12
    rms_diff = float(np.sqrt(np.mean(diff ** 2)))
    return {
        "max_diff": float(np.max(np.abs(diff))) if diff.size else 0.0,
        "rms_diff": rms_diff,
        "rms_diff_db": 20.0 * math.log10(max(rms_diff, 1e-12) / rms_ref),
    }


def measure_determinism(case: Case, config: dict[str, object] | None = None) -> dict[str, float]:
    """Process the same block twice and compare the two results.

    A module may not carry state from one call into the next: the same
    block has to come out the same way every time.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    x = np.asarray(case.x, dtype=np.float64)
    module = RustleProbe(SR, case.channels)
    _apply_config(module, cfg)
    first = np.asarray(module.process(x.copy()), dtype=np.float64)
    second = np.asarray(module.process(x.copy()), dtype=np.float64)
    diff = np.abs(first - second)
    return {
        "max_diff": float(np.max(diff)) if diff.size else 0.0,
        "identical": bool(diff.size == 0 or np.max(diff) == 0.0),
    }


def measure_lookahead(
    case: Case,
    config: dict[str, object] | None = None,
) -> dict[str, float]:
    """How far into the future the module has to look.

    The module analyses a centred window of ``NFFT`` samples every ``HOP``
    samples, so the output of the last sample of a block is computed from
    input samples that lie *beyond* the block.  The number is measured from
    the module's own analysis: the frame count of the block and the window
    the last frame covers.
    """
    x = np.asarray(case.x, dtype=np.float64)
    n = int(x.shape[0])
    spec = _module._spectrogram(x, NFFT, HOP)
    n_frames = int(spec.shape[0])
    last_centre = (n_frames - 1) * HOP
    lookahead = last_centre + NFFT // 2 - (n - 1)
    return {
        "frames": float(n_frames),
        "last_centre": float(last_centre),
        "lookahead": float(lookahead),
        "lookahead_ms": 1000.0 * lookahead / SR,
    }


def measure_cost(
    block_size: int,
    channels: int = 2,
    repeats: int = 15,
    warmup: int = 3,
    rounds: int = 3,
    config: dict[str, object] | None = None,
    module_cls: type | None = None,
) -> dict[str, float]:
    """Time one ``process()`` call at a block size.

    The module is driven the way a host drives it: one block in, one block
    out.  The reported number is the fastest of ``rounds`` rounds of
    ``repeats`` timed calls: on a shared machine the fastest round is the
    one that was least disturbed by other work, so it is the best estimate
    of the module's own cost.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    cls = module_cls if module_cls is not None else RustleProbe
    module = cls(SR, channels)
    _apply_config(module, cfg)

    rng = np.random.default_rng(20_260_322)
    block = rng.standard_normal((block_size, channels))
    block = block / max(float(np.max(np.abs(block))), 1e-12) * 0.5

    rounds_median: list[float] = []
    all_times: list[float] = []
    for _ in range(max(1, rounds)):
        for _ in range(max(0, warmup)):
            module.process(block.copy())
        times: list[float] = []
        for _ in range(max(1, repeats)):
            start = time.perf_counter_ns()
            module.process(block.copy())
            times.append((time.perf_counter_ns() - start) / 1000.0)
        rounds_median.append(float(np.percentile(times, 50.0)))
        all_times.extend(times)

    median = min(rounds_median)
    budget = (float(block_size) / float(SR)) * 1_000_000.0
    return {
        "block": float(block_size),
        "median_us": median,
        "min_us": float(min(all_times)),
        "max_us": float(max(all_times)),
        "budget_us": budget,
        "budget_fraction": median / budget if budget > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def edge_cases(n: int = 4096) -> list[tuple[str, np.ndarray]]:
    """The awkward inputs: silence, a constant, a full scale square, an impulse."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    silence = np.zeros((n, 2), dtype=np.float64)
    dc = np.full((n, 2), 0.5, dtype=np.float64)
    square = np.sign(np.sin(2.0 * math.pi * 220.0 * t))[:, None] * np.ones((1, 2))
    impulse = np.zeros((n, 2), dtype=np.float64)
    impulse[n // 2, 0] = 1.0
    tone = (0.5 * np.sin(2.0 * math.pi * 1000.0 * t))[:, None] * np.ones((1, 2))
    return [
        ("silence", silence),
        ("dc", dc),
        ("square", square),
        ("impulse", impulse),
        ("tone", tone),
    ]
