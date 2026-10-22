"""
qa.qualityvoice.harness — the measurement instrument for the voice shaped
modules.

The harness measures **any** module that works on a voice shaped signal.  It
does not look inside a module: it drives the public ``process`` entry point
and compares the output with the input.

Measured quantities
-------------------
``band_<name>_change_db``
    How much the energy of a named band moved (dB).  A negative number means
    the band got quieter.
``region_<name>_<band>_change_db``
    The same measurement, restricted to a named stretch of the signal (the
    breath, the click, the sibilance ...).
``chatter_db``
    The temporal variation of the gain the module applies, with the wanted
    content taken out of the measurement (see :func:`measure_chatter`).
``latency_reported`` / ``latency_measured``
    The delay the module claims and the delay it really adds.
``block_rms_diff_db``
    How much the output changes when the host chops the stream into blocks.

The signal set is synthetic and fully deterministic: a synthetic voice with
a jittering fundamental, a formant envelope, a spectral tilt and pauses (see
:mod:`qa.qualityvoice.voice_signal`), plus a background of white noise, pink
noise, mains hum, a breath, a mouth click or a sibilance.

The three reference modules in this file (:class:`SteadyGain`,
:class:`FrameGain` and :class:`ContentFollower`) calibrate the chatter
measurement: the first applies a constant gain and must report no chatter
at all, the second applies a known per frame gain and must report it, the
third follows the level of the content and must still report no chatter.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

# Make the repository importable when the harness is run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Load a sibling module of the harness by path."""
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


voice_signal = _load("aur_qa_qualityvoice_voice_signal", "voice_signal.py")

__all__ = [
    "SR",
    "VOICE_BAND",
    "HISS_BAND",
    "LEVEL_SEQUENCE_DB",
    "Case",
    "Result",
    "build_cases",
    "evaluate",
    "level_step",
    "measure_block_coherence",
    "measure_chatter",
    "measure_latency",
    "measure_level_spread",
    "band_energy_db",
    "band_energy_series_db",
    "white_noise",
    "pink_noise",
    "mains_hum",
    "FrameGain",
    "SteadyGain",
    "ContentFollower",
    "VOICE_MODULES",
    "harness_version",
]

#: Sample rate used by every measurement.
SR = 48_000

#: Band that carries the harmonics of the synthetic voice (Hz).
VOICE_BAND = (120.0, 3_000.0)

#: Band that carries the background only (Hz).  The synthetic voice has no
#: energy above 5 kHz, so this band is independent of the voice.
HISS_BAND = (5_000.0, 14_000.0)

#: Analysis window used by the band-energy measurement (samples).
_MEAS_FFT = 2048

#: Hop of the band-energy measurement (samples).
_MEAS_HOP = 512

#: Fraction of the frames that is skipped before the steady state is
#: measured: the gain of a module settles in the first frames.
SETTLE = 0.2

#: The levels of the step case (dB).  The same material is played at every
#: one of them under a fixed setting, so that the only thing that changes
#: between two stretches is the level of the input.  The sequence covers
#: 18 dB.
LEVEL_SEQUENCE_DB: tuple[float, ...] = (-12.0, 0.0, -6.0, -18.0, -12.0)

#: How long the warm-up before the first level of the step case is
#: (seconds).  The gain of a module settles in the first frames, and the
#: warm-up is not part of the measurement.
WARMUP_S = 2.0

#: How long every stretch of the step case is (seconds).
SEGMENT_S = 1.0

#: Version of the instrument.  Bumped whenever a number in the report moves.
harness_version = "2"


# ---------------------------------------------------------------------------
# background generators
# ---------------------------------------------------------------------------
def white_noise(n: int, rms_db: float, rng: np.random.Generator) -> np.ndarray:
    """White noise with a given RMS level."""
    noise = rng.standard_normal(n)
    noise -= noise.mean()
    target = 10.0 ** (rms_db / 20.0)
    current = float(np.sqrt(np.mean(noise * noise)))
    return noise * (target / max(current, 1e-12))


def pink_noise(n: int, rms_db: float, rng: np.random.Generator) -> np.ndarray:
    """Pink (1/f) noise with a given RMS level."""
    spectrum = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    shape = np.ones_like(freqs)
    shape[1:] = 1.0 / np.sqrt(freqs[1:])
    shape[0] = shape[1]
    spectrum *= shape
    noise = np.fft.irfft(spectrum, n=n)
    target = 10.0 ** (rms_db / 20.0)
    current = float(np.sqrt(np.mean(noise * noise)))
    return noise * (target / max(current, 1e-12))


def mains_hum(n: int, f0: float = 50.0, amp: float = 0.02) -> np.ndarray:
    """Mains hum: the fundamental plus four harmonics."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    out = np.zeros(n, dtype=np.float64)
    for k, weight in enumerate((1.0, 0.5, 0.25, 0.125, 0.0625), start=1):
        out += (amp * weight) * np.sin(2.0 * math.pi * f0 * k * t)
    return out


def _burst(
    n: int,
    lo_hz: float,
    hi_hz: float,
    rms_db: float,
    rng: np.random.Generator,
    decay_s: float = 0.0,
) -> np.ndarray:
    """A band limited noise burst, optionally with an exponential decay."""
    spectrum = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    freqs = np.fft.rfftfreq(n, 1.0 / float(SR))
    edge = max(50.0, 0.25 * (hi_hz - lo_hz))
    weight = np.zeros_like(freqs)
    inside = (freqs >= lo_hz) & (freqs <= hi_hz)
    weight[inside] = 1.0
    low = (freqs >= lo_hz - edge) & (freqs < lo_hz)
    high = (freqs > hi_hz) & (freqs <= hi_hz + edge)
    if np.any(low):
        weight[low] = 0.5 * (1.0 + np.cos(np.pi * (freqs[low] - (lo_hz - edge)) / edge))
    if np.any(high):
        weight[high] = 0.5 * (1.0 - np.cos(np.pi * (freqs[high] - hi_hz) / edge))
    spectrum *= weight
    noise = np.fft.irfft(spectrum, n=n)
    if decay_s > 0.0:
        t = np.arange(n, dtype=np.float64) / float(SR)
        noise *= np.exp(-t / decay_s)
    target = 10.0 ** (rms_db / 20.0)
    current = float(np.sqrt(np.mean(noise * noise)))
    return noise * (target / max(current, 1e-12))


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
@dataclass
class Case:
    """One synthetic measurement case.

    Attributes
    ----------
    name:
        Identifier of the case.
    x:
        The signal to process, shape ``(n_samples, 1)``.
    voice:
        The wanted content of ``x`` (the synthetic voice).
    background:
        Everything that is not wanted (noise, hum, breath, click ...).
    bands:
        The named frequency bands of this case, in Hz.
    regions:
        The named sample ranges of this case.
    notch:
        Frequencies that are left out of every band measurement.
    """

    name: str
    x: np.ndarray
    voice: np.ndarray
    background: np.ndarray
    bands: dict[str, tuple[float, float]] = field(default_factory=dict)
    regions: dict[str, tuple[int, int]] = field(default_factory=dict)
    notch: tuple[float, ...] = ()

    @property
    def n_samples(self) -> int:
        """Length of the case in samples."""
        return int(self.x.shape[0])


def _voice_segments() -> list[tuple[str, int, int]]:
    """The syllable and pause layout of the synthetic voice."""
    return voice_signal.voice_segments(voice_signal.VOICE_SPEC)


def build_cases(seed: int = 20_260_102) -> dict[str, Case]:
    """Build the measurement cases.

    Six cases, all built from the same synthetic voice, so that the numbers
    of the different cases can be compared with each other.

    ``voice_hiss``
        the voice under white noise
    ``voice_clean``
        the voice alone, without any background: a module that removes noise
        has to leave it alone
    ``voice_pink_hiss``
        the voice under pink noise
    ``voice_hum``
        the voice with a mains hum
    ``voice_breath``
        the voice with a breath in one of its pauses
    ``voice_click``
        the voice with two mouth clicks
    ``voice_sibilance``
        the voice with a sibilant burst
    """
    rng = np.random.default_rng(seed)
    voice = voice_signal.synth_voice()
    n = voice.size
    voice2 = voice[:, None]
    segments = _voice_segments()
    pauses = [(s, e) for kind, s, e in segments if kind == "pause"]
    voiced = [(s, e) for kind, s, e in segments if kind == "voiced"]

    cases: dict[str, Case] = {}

    # The clean voice: no background at all.  A module that removes noise
    # has to leave this signal alone.
    cases["voice_clean"] = Case(
        name="voice_clean",
        x=voice2.copy(),
        voice=voice2,
        background=np.zeros((n, 1), dtype=np.float64),
        bands={"voice": VOICE_BAND, "hiss": HISS_BAND, "low": (150.0, 600.0)},
    )

    white = white_noise(n, -45.0, rng)
    cases["voice_hiss"] = Case(
        name="voice_hiss",
        x=(voice + white)[:, None],
        voice=voice2,
        background=white[:, None],
        bands={"voice": VOICE_BAND, "hiss": HISS_BAND, "low": (150.0, 600.0)},
    )

    # The bed: a noise bed with nothing wanted in it.  A module that removes
    # noise has to take the bed away, and the case is the one the bed rule
    # of the module is written for: there is nothing in it to protect.
    cases["voice_bed"] = Case(
        name="voice_bed",
        x=white[:, None].copy(),
        voice=np.zeros((n, 1), dtype=np.float64),
        background=white[:, None],
        bands={"hiss": HISS_BAND, "bed": (100.0, 16_000.0)},
    )

    pink = pink_noise(n, -45.0, rng)
    cases["voice_pink_hiss"] = Case(
        name="voice_pink_hiss",
        x=(voice + pink)[:, None],
        voice=voice2,
        background=pink[:, None],
        bands={"voice": VOICE_BAND, "hiss": HISS_BAND, "low": (150.0, 600.0)},
    )

    hum = mains_hum(n)
    quiet = white_noise(n, -55.0, rng)
    cases["voice_hum"] = Case(
        name="voice_hum",
        x=(voice + hum + quiet)[:, None],
        voice=voice2,
        background=(hum + quiet)[:, None],
        bands={
            "voice": VOICE_BAND,
            "hiss": HISS_BAND,
            "hum": (40.0, 90.0),
        },
        notch=(50.0, 100.0, 150.0, 200.0, 250.0),
    )

    # the breath sits in the middle of the third pause
    breath_start = pauses[2][0] + (pauses[2][1] - pauses[2][0]) // 4
    breath_len = int(0.2 * SR)
    breath = _burst(breath_len, 400.0, 6_000.0, -22.0, rng)
    signal = voice.copy()
    signal[breath_start : breath_start + breath_len] += breath
    hiss = white_noise(n, -48.0, rng)
    breath_bg = np.zeros(n, dtype=np.float64)
    breath_bg[breath_start : breath_start + breath_len] += breath
    cases["voice_breath"] = Case(
        name="voice_breath",
        x=(signal + hiss)[:, None],
        voice=voice2,
        background=(hiss + breath_bg)[:, None],
        bands={
            "voice": VOICE_BAND,
            "breath": (1_000.0, 8_000.0),
            "hiss": HISS_BAND,
        },
        regions={"breath": (breath_start, breath_start + breath_len)},
    )

    # two mouth clicks: one over a vowel, one inside a pause
    click_len = max(8, int(0.0015 * SR))
    click_a = voiced[2][0] + (voiced[2][1] - voiced[2][0]) // 2
    click_b = pauses[3][0] + (pauses[3][1] - pauses[3][0]) // 2
    signal = voice.copy()
    background = np.zeros(n, dtype=np.float64)
    for index, position in enumerate((click_a, click_b)):
        click = _burst(click_len, 500.0, 12_000.0, -12.0, rng, decay_s=0.0004)
        signal[position : position + click_len] += click
        background[position : position + click_len] += click
    hiss = white_noise(n, -48.0, rng)
    cases["voice_click"] = Case(
        name="voice_click",
        x=(signal + hiss)[:, None],
        voice=voice2,
        background=(background + hiss)[:, None],
        bands={"voice": VOICE_BAND, "click": (1_000.0, 14_000.0), "hiss": HISS_BAND},
        regions={
            "click_a": (click_a, click_a + click_len),
            "click_b": (click_b, click_b + click_len),
        },
    )

    # a sibilant burst inside a pause
    sib_start = pauses[1][0] + (pauses[1][1] - pauses[1][0]) // 4
    sib_len = int(0.18 * SR)
    sib = _burst(sib_len, 6_000.0, 12_000.0, -20.0, rng)
    signal = voice.copy()
    signal[sib_start : sib_start + sib_len] += sib
    hiss = white_noise(n, -48.0, rng)
    sib_bg = np.zeros(n, dtype=np.float64)
    sib_bg[sib_start : sib_start + sib_len] += sib
    cases["voice_sibilance"] = Case(
        name="voice_sibilance",
        x=(signal + hiss)[:, None],
        voice=voice2,
        background=(hiss + sib_bg)[:, None],
        bands={
            "voice": VOICE_BAND,
            "low": (300.0, 3_500.0),
            "sib": (6_000.0, 12_000.0),
            "hiss": HISS_BAND,
        },
        regions={"sib": (sib_start, sib_start + sib_len)},
    )
    return cases


# ---------------------------------------------------------------------------
# the analysis grid
# ---------------------------------------------------------------------------
def _mono(x: np.ndarray) -> np.ndarray:
    """First channel of ``x`` as a 1-D array."""
    arr = np.asarray(x, dtype=np.float64)
    return arr[:, 0] if arr.ndim > 1 else arr.ravel()


def stft_frames(
    x: np.ndarray, n_fft: int = _MEAS_FFT, hop: int = _MEAS_HOP
) -> tuple[np.ndarray, np.ndarray]:
    """The magnitude spectrum of every frame of ``x``.

    Returns the power spectrum (frames, bins) and the start sample of every
    frame.  The frames start at sample 0 and overlap by 75 %.
    """
    seg = _mono(x)
    if seg.size < n_fft:
        return np.zeros((0, n_fft // 2 + 1), dtype=np.float64), np.zeros(0, dtype=np.intp)
    window = np.hanning(n_fft)
    n_frames = 1 + (seg.size - n_fft) // hop
    view = np.lib.stride_tricks.sliding_window_view(seg, n_fft)[::hop][:n_frames]
    spectrum = np.fft.rfft(view * window, axis=-1)
    return np.abs(spectrum) ** 2, (np.arange(n_frames) * hop).astype(np.intp)


def _frame_mask(
    n_frames: int, hop: int, n_fft: int, exclude: Sequence[tuple[int, int]]
) -> np.ndarray:
    """Mask of the frames that do not touch an excluded range."""
    keep = np.ones(n_frames, dtype=bool)
    if not exclude:
        return keep
    starts = np.arange(n_frames) * hop
    for low, high in exclude:
        keep &= ~((starts < high) & (starts + n_fft > low))
    return keep


def band_energy_db(
    x: np.ndarray,
    f_lo: float,
    f_hi: float,
    start: int = 0,
    end: int | None = None,
    n_fft: int = _MEAS_FFT,
    hop: int = _MEAS_HOP,
    notch: Sequence[float] = (),
    exclude: Sequence[tuple[int, int]] = (),
) -> float:
    """Energy inside a frequency band, in dB.

    The measurement uses a Hann windowed short time Fourier transform and
    sums the power of every bin inside the band over every frame.  The
    absolute value is an arbitrary power unit; only the ratio between two
    measurements of the same length is meaningful.
    """
    seg = _mono(x)
    if end is not None:
        seg = seg[start:end]
    elif start:
        seg = seg[start:]
    if seg.size < 32:
        return -300.0
    # A short stretch (a click lasts a millisecond) cannot be measured with
    # the full window, so the window shrinks with the stretch.
    window_size = int(n_fft)
    while window_size > 32 and seg.size < window_size:
        window_size //= 2
    if seg.size < window_size:
        return -300.0
    power, frame_start = stft_frames(seg, window_size, hop)
    if power.shape[0] == 0:
        return -300.0
    keep = _frame_mask(power.shape[0], hop, window_size, exclude)
    if not np.any(keep):
        return -300.0
    freqs = np.fft.rfftfreq(window_size, 1.0 / SR)
    mask = (freqs >= f_lo) & (freqs <= f_hi)
    for centre in notch:
        mask &= np.abs(freqs - centre) > 18.0
    if not np.any(mask):
        return -300.0
    total = float(np.sum(power[keep][:, mask]))
    return 10.0 * math.log10(max(total, 1e-30))


def band_energy_series_db(
    x: np.ndarray,
    f_lo: float,
    f_hi: float,
    n_fft: int = _MEAS_FFT,
    hop: int = _MEAS_HOP,
) -> np.ndarray:
    """Per frame band energy in dB (the gain curve of a band)."""
    power, _ = stft_frames(x, n_fft, hop)
    if power.shape[0] == 0:
        return np.zeros(0, dtype=np.float64)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    mask = (freqs >= f_lo) & (freqs <= f_hi)
    if not np.any(mask):
        return np.zeros(0, dtype=np.float64)
    per_frame = np.sum(power[:, mask], axis=1)
    return 10.0 * np.log10(np.maximum(per_frame, 1e-30))


def measure_latency(x: np.ndarray, y: np.ndarray, max_lag: int = 4096) -> int:
    """Delay between ``x`` and ``y`` in samples (cross correlation peak).

    The sign follows the convention of the modules: a positive number means
    that the output is **later** than the input, a negative number that the
    output runs ahead of it.
    """
    a = _mono(x) - float(np.mean(_mono(x)))
    b = _mono(y) - float(np.mean(_mono(y)))
    n = min(a.size, b.size)
    if n < 8:
        return 0
    a = a[:n]
    b = b[:n]
    spectrum = np.fft.rfft(a) * np.conj(np.fft.rfft(b))
    correlation = np.fft.irfft(spectrum, n=n)
    correlation = np.concatenate([correlation[-max_lag:], correlation[: max_lag + 1]])
    lags = np.arange(-max_lag, max_lag + 1)
    return -int(lags[int(np.argmax(correlation))])


# ---------------------------------------------------------------------------
# the level step
# ---------------------------------------------------------------------------
def level_step(
    x: np.ndarray,
    levels: Sequence[float] = LEVEL_SEQUENCE_DB,
    warmup_s: float = WARMUP_S,
    segment_s: float = SEGMENT_S,
) -> np.ndarray:
    """The same material at several levels, under one fixed setting.

    Every stretch carries the same stretch of the material, so that the only
    thing that changes between two stretches is the level of the input.
    The warm-up in front of the first stretch is at the level of the
    material.

    Parameters
    ----------
    x:
        The material.
    levels:
        The levels of the stretches, in dB, relative to the material.
    warmup_s:
        How long the warm-up is, in seconds.
    segment_s:
        How long every stretch is, in seconds.

    Returns
    -------
    numpy.ndarray
        The stepped signal.
    """
    arr = _mono(x)
    window = int(segment_s * SR)
    if arr.size == 0 or window < 1:
        return np.zeros(0, dtype=np.float64)
    material = arr[:window]
    pieces = [np.resize(material, int(warmup_s * SR))]
    for level in levels:
        piece = np.resize(material, int(segment_s * SR))
        pieces.append(piece * (10.0 ** (float(level) / 20.0)))
    return np.concatenate(pieces)


def measure_level_spread(
    case: Case,
    module,
    config: dict[str, object] | None = None,
    band: tuple[float, float] = HISS_BAND,
    levels: Sequence[float] = LEVEL_SEQUENCE_DB,
) -> dict[str, float]:
    """How far the applied gain moves when the level of the input moves (dB).

    **What is measured.**  The same material is played at five levels under
    a fixed setting.  The gain the module applies in ``band`` is measured
    frame by frame, the mean of every stretch is taken, and the spread of
    those means is the number.  A module whose decision does not carry the
    level of the input applies the same gain at every level, so the spread
    is 0 dB.

    The frames that see two levels at once (the frames that cross a step)
    are left out: they would report a gain the module never applied.

    Parameters
    ----------
    case:
        The case to process.
    module:
        The module (an instance).
    config:
        Parameter overrides.
    band:
        The band the gain is measured in, in Hz.
    levels:
        The levels of the stretches, in dB.

    Returns
    -------
    dict
        The measured numbers.
    """
    reset = getattr(module, "reset", None)
    if callable(reset):
        reset()
    set_params(module, config)
    x = np.asarray(case.x, dtype=np.float64)
    stepped = level_step(x, levels)
    out = np.asarray(module.process(stepped.copy()), dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    lag = int(getattr(module, "latency_samples", 0))
    # the input and the output that belong together: the output at t + lag
    # is the input at t, seen through the module.
    count = min(stepped.shape[0], out.shape[0]) - lag
    if count < _MEAS_FFT * 2:
        return {"level_spread_db": 0.0, "level_means_db": [], "level_frames": 0.0}
    xa = stepped[:count]
    ya = out[lag : lag + count]
    xs = band_energy_series_db(_mono(xa), band[0], band[1])
    ys = band_energy_series_db(_mono(ya), band[0], band[1])
    frames = int(min(xs.size, ys.size))
    curve = ys[:frames] - xs[:frames]
    means: list[float] = []
    position = int(WARMUP_S * SR)
    for level in levels:
        last = position + int(SEGMENT_S * SR)
        begin = max(0, int(math.ceil(position / _MEAS_HOP)))
        end = min(frames, int((last - _MEAS_FFT) // _MEAS_HOP) + 1)
        if end > begin:
            means.append(float(np.mean(curve[begin:end])))
        position = last
    if len(means) < 2:
        return {"level_spread_db": 0.0, "level_means_db": [], "level_frames": 0.0}
    return {
        "level_spread_db": float(max(means) - min(means)),
        "level_means_db": [round(value, 3) for value in means],
        "level_frames": float(frames),
    }


# ---------------------------------------------------------------------------
# the chatter measurement
# ---------------------------------------------------------------------------
def measure_chatter(
    x: np.ndarray,
    y: np.ndarray,
    band: tuple[float, float],
    content_band: tuple[float, float] = VOICE_BAND,
    n_fft: int = _MEAS_FFT,
    hop: int = _MEAS_HOP,
    settle: float = SETTLE,
) -> dict[str, float]:
    """Measure the chatter of the gain the module applies.

    **What is measured.**  The gain the module applies to a band is the
    difference between the level of the band in the output and its level in
    the input, frame by frame.  The wanted content cannot hide in that
    number: it is in the input *and* in the output, so it cancels out.

    **How the wanted content is kept out.**  The chatter is the *roughness*
    of the gain curve, not its spread:

    * the wanted content changes **slowly** — the envelope of a syllable
      moves over tens of milliseconds, so the frame to frame difference of a
      gain that follows it stays small;
    * the chatter moves **fast** — the gain of a denoiser that hunts from
      frame to frame produces a large frame to frame difference.

    The spread of the gain curve (its standard deviation) is reported next
    to the roughness, and the spread of the wanted content is reported next
    to that, so the two can be compared: a module whose gain follows the
    content has a large spread and a small roughness.

    The roughness is measured with the median absolute deviation, so a
    single step (a gate that opens once, a syllable that starts) does not
    count as chatter.

    The three reference modules of this file calibrate the result:

    ==========================  =========================
    :class:`SteadyGain`         a constant gain reports 0 dB
    :class:`FrameGain`           a random gain of 2 dB reports 2 dB
    :class:`ContentFollower`     a gain that follows the content of a
                                  signal whose level moves by tens of dB
                                  reports 0 dB
    ==========================  =========================

    Parameters
    ----------
    x:
        The input of the module.
    y:
        The output of the module, already aligned with the input.
    band:
        The frequency band the chatter is measured in, in Hz.
    content_band:
        The band that carries the wanted content; its spread is reported.
    n_fft, hop:
        The analysis grid.
    settle:
        The fraction of the frames that is skipped at the start.

    Returns
    -------
    dict
        The measured numbers.
    """
    xs = band_energy_series_db(x, band[0], band[1], n_fft, hop)
    ys = band_energy_series_db(y, band[0], band[1], n_fft, hop)
    count = min(xs.size, ys.size)
    empty = {
        "chatter_db": 0.0,
        "chatter_gain_std_db": 0.0,
        "chatter_gain_step_db": 0.0,
        "chatter_content_std_db": 0.0,
        "chatter_content_span_db": 0.0,
        "chatter_frames": 0.0,
    }
    if count < 8:
        return empty
    xs = xs[:count]
    ys = ys[:count]
    gain = ys - xs

    skip = int(settle * count)
    if skip >= count - 4:
        skip = max(0, count - 8)
    steady = gain[skip:]
    steps = np.diff(steady)
    if steps.size < 4:
        return empty
    median = float(np.median(steps))
    deviation = float(np.median(np.abs(steps - median)))
    roughness = 1.4826 * deviation / math.sqrt(2.0)

    content = band_energy_series_db(x, content_band[0], content_band[1], n_fft, hop)
    if content.size:
        content = content[:count]
        content_std = float(np.std(content))
        content_span = float(np.max(content) - np.min(content))
    else:
        content_std = 0.0
        content_span = 0.0
    return {
        "chatter_db": float(roughness),
        "chatter_gain_std_db": float(np.std(steady)),
        "chatter_gain_step_db": float(np.max(np.abs(steps))),
        "chatter_content_std_db": content_std,
        "chatter_content_span_db": content_span,
        "chatter_frames": float(steady.size),
    }


# ---------------------------------------------------------------------------
# reference modules (the calibration of the instrument)
# ---------------------------------------------------------------------------
class _NullParams:
    """The parameter interface of a module that has no parameters."""

    def has(self, name: str) -> bool:
        """Whether the module knows the parameter."""
        return False

    def get(self, name: str, default=None):
        """The value of a parameter."""
        return default

    def set(self, name: str, value) -> None:
        """Set a parameter (ignored)."""
        return None


class SteadyGain:
    """A reference module that applies a constant gain.

    It is the zero point of the chatter measurement: a module that changes
    the signal by a fixed amount must not report any chatter.
    """

    code = "QA_STEADY"

    def __init__(self, sample_rate: int = SR, channels: int = 1, gain_db: float = -3.0):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.gain_db = float(gain_db)
        self.params = _NullParams()

    def process(self, x: np.ndarray) -> np.ndarray:
        """Scale the signal by a constant gain."""
        arr = np.asarray(x, dtype=np.float64)
        return arr * 10.0 ** (self.gain_db / 20.0)

    def reset(self) -> None:
        """Nothing to reset."""

    @property
    def latency_samples(self) -> int:
        """The reference module adds no delay."""
        return 0


class FrameGain:
    """A reference module that applies a known gain to every frame.

    The gain is applied on the analysis grid of the harness, so the
    measurement recovers it exactly.  It is the scale of the chatter
    measurement: a gain that alternates by +-2 dB has to be reported as
    2 dB of chatter.
    """

    code = "QA_FRAME"

    def __init__(
        self,
        sample_rate: int = SR,
        channels: int = 1,
        gains_db: Sequence[float] | None = None,
        n_fft: int = _MEAS_FFT,
        hop: int = _MEAS_HOP,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.gains_db = list(gains_db) if gains_db is not None else [0.0]
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self.params = _NullParams()

    def process(self, x: np.ndarray) -> np.ndarray:
        """Apply the per frame gain on the analysis grid of the harness."""
        arr = np.asarray(x, dtype=np.float64)
        seg = _mono(arr)
        length = seg.size
        padded = np.concatenate([seg, np.zeros(self.n_fft, dtype=np.float64)])
        window = np.hanning(self.n_fft)
        n_frames = 1 + (padded.size - self.n_fft) // self.hop
        view = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft)[::self.hop][:n_frames]
        spectrum = np.fft.rfft(view * window, axis=-1)
        gains = np.asarray(self.gains_db, dtype=np.float64)
        if gains.size < spectrum.shape[0]:
            gains = np.resize(gains, spectrum.shape[0])
        factor = np.power(10.0, gains[: spectrum.shape[0]] / 20.0)[:, None]
        frames = np.fft.irfft(spectrum * factor, n=self.n_fft, axis=-1) * window
        total = (frames.shape[0] - 1) * self.hop + self.n_fft
        out = np.zeros(total, dtype=np.float64)
        weight = np.zeros(total, dtype=np.float64)
        for index in range(frames.shape[0]):
            begin = index * self.hop
            out[begin : begin + self.n_fft] += frames[index]
            weight[begin : begin + self.n_fft] += window * window
        out /= np.maximum(weight, 1e-12)
        return out[:length][:, None] if arr.ndim > 1 else out[:length]

    def reset(self) -> None:
        """Nothing to reset."""

    @property
    def latency_samples(self) -> int:
        """The reference module adds no delay."""
        return 0


class ContentFollower:
    """A reference module whose gain follows the level of the content.

    It behaves like a compressor: the louder the signal, the more gain it
    takes away.  The gain curve moves over several dB while the level of the
    signal moves over tens of dB — and none of that is chatter, because the
    movement is slow.  This module is the proof that the chatter
    measurement does not mistake the wanted content's variation for
    chatter.
    """

    code = "QA_FOLLOW"

    def __init__(
        self,
        sample_rate: int = SR,
        channels: int = 1,
        depth_db: float = -12.0,
        n_fft: int = _MEAS_FFT,
        hop: int = _MEAS_HOP,
        smooth: int = 7,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.depth_db = float(depth_db)
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self.smooth = int(smooth)
        self.params = _NullParams()

    def process(self, x: np.ndarray) -> np.ndarray:
        """Attenuate the loud frames and leave the quiet ones alone."""
        arr = np.asarray(x, dtype=np.float64)
        seg = _mono(arr)
        length = seg.size
        padded = np.concatenate([seg, np.zeros(self.n_fft, dtype=np.float64)])
        window = np.hanning(self.n_fft)
        n_frames = 1 + (padded.size - self.n_fft) // self.hop
        view = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft)[::self.hop][:n_frames]
        frames = view * window
        level = 10.0 * np.log10(np.maximum(np.sum(frames * frames, axis=1) / self.n_fft, 1e-30))
        # a slow detector, the way a compressor works
        kernel = np.ones(self.smooth) / float(self.smooth)
        detector = np.convolve(level, kernel, mode="same")
        reference = float(np.median(detector))
        gain_db = np.clip(self.depth_db * (detector - reference) / 6.0, -24.0, 0.0)
        spectrum = np.fft.rfft(frames, axis=1)
        factor = np.power(10.0, gain_db / 20.0)[:, None]
        modified = np.fft.irfft(spectrum * factor, n=self.n_fft, axis=1) * window
        total = (modified.shape[0] - 1) * self.hop + self.n_fft
        out = np.zeros(total, dtype=np.float64)
        weight = np.zeros(total, dtype=np.float64)
        for index in range(modified.shape[0]):
            begin = index * self.hop
            out[begin : begin + self.n_fft] += modified[index]
            weight[begin : begin + self.n_fft] += window * window
        out /= np.maximum(weight, 1e-12)
        return out[:length][:, None] if arr.ndim > 1 else out[:length]

    def reset(self) -> None:
        """Nothing to reset."""

    @property
    def latency_samples(self) -> int:
        """The reference module adds no delay."""
        return 0


# ---------------------------------------------------------------------------
# the evaluation
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


def set_params(module, config: dict[str, object] | None) -> None:
    """Set every parameter of ``config`` on ``module``."""
    if not config:
        return
    params = getattr(module, "params", None)
    if params is None:
        return
    for name, value in config.items():
        if params.has(name):
            params.set(name, value)


def _run(module, x: np.ndarray, block_size: int | None = None) -> np.ndarray:
    """Process ``x``, optionally in blocks of ``block_size`` samples."""
    arr = np.asarray(x, dtype=np.float64)
    if block_size:
        pieces = []
        for start in range(0, arr.shape[0], block_size):
            block = arr[start : start + block_size]
            pieces.append(np.asarray(module.process(block), dtype=np.float64))
        out = np.concatenate(pieces, axis=0)
    else:
        out = np.asarray(module.process(arr), dtype=np.float64)
    if out.shape[0] < arr.shape[0]:
        out = np.concatenate(
            [out, np.zeros((arr.shape[0] - out.shape[0], out.shape[1]), dtype=np.float64)], axis=0
        )
    return out[: arr.shape[0]]


def evaluate(
    case: Case,
    module,
    config: dict[str, object] | None = None,
    chatter_band: tuple[float, float] = HISS_BAND,
    block_size: int | None = None,
) -> Result:
    """Run one case through a module and measure the result.

    Parameters
    ----------
    case:
        The case to process.
    module:
        The module (an instance).  It is used as it is; the caller creates a
        fresh one for every measurement.
    config:
        Parameter overrides.
    chatter_band:
        The band the chatter is measured in.
    block_size:
        When given, the signal is processed in blocks of this size.

    Returns
    -------
    Result
        The measured metrics of the case.
    """
    set_params(module, config)
    x = np.asarray(case.x, dtype=np.float64)
    y = _run(module, x, block_size)

    reported = int(getattr(module, "latency_samples", 0))
    latency = measure_latency(x, y)
    span = x.shape[0] - abs(latency)
    if span <= _MEAS_FFT * 2:
        span = x.shape[0] - _MEAS_FFT
    if latency >= 0:
        xa = x[:span]
        ya = y[latency : latency + span]
    else:
        xa = x[-latency : -latency + span]
        ya = y[:span]

    metrics: dict[str, float] = {
        "latency_reported": float(reported),
        "latency_measured": float(latency),
    }

    difference = ya - xa
    reference = float(np.sqrt(np.mean(xa * xa))) or 1e-12
    rms_difference = float(np.sqrt(np.mean(difference * difference)))
    rms_output = float(np.sqrt(np.mean(ya * ya)))
    metrics["output_max_diff"] = float(np.max(np.abs(difference))) if difference.size else 0.0
    metrics["output_rms_diff_db"] = 20.0 * math.log10(max(rms_difference, 1e-12) / reference)
    metrics["output_level_db"] = 20.0 * math.log10(max(rms_output, 1e-12) / reference)

    for name, band in case.bands.items():
        before = band_energy_db(xa, band[0], band[1], notch=case.notch)
        after = band_energy_db(ya, band[0], band[1], notch=case.notch)
        metrics[f"band_{name}_in_db"] = before
        metrics[f"band_{name}_out_db"] = after
        metrics[f"band_{name}_change_db"] = after - before

    for name, (low, high) in case.regions.items():
        for band_name, band in case.bands.items():
            before = band_energy_db(
                xa, band[0], band[1], start=low, end=high, notch=case.notch
            )
            after = band_energy_db(
                ya, band[0], band[1], start=low, end=high, notch=case.notch
            )
            metrics[f"region_{name}_{band_name}_in_db"] = before
            metrics[f"region_{name}_{band_name}_out_db"] = after
            metrics[f"region_{name}_{band_name}_change_db"] = after - before

    metrics.update(measure_chatter(xa, ya, chatter_band))
    return Result(case=case.name, metrics=metrics)


def measure_block_coherence(
    case: Case,
    factory,
    config: dict[str, object] | None = None,
    block_size: int = 4_096,
) -> dict[str, float]:
    """Compare one block processing with small block processing.

    A block coherent module returns the same samples no matter how the host
    chops the stream.
    """
    x = np.asarray(case.x, dtype=np.float64)

    whole = factory()
    set_params(whole, config)
    y_whole = _run(whole, x)

    chunked = factory()
    set_params(chunked, config)
    y_chunked = _run(chunked, x, block_size=block_size)

    count = min(y_whole.shape[0], y_chunked.shape[0])
    diff = y_whole[:count] - y_chunked[:count]
    reference = float(np.sqrt(np.mean(y_whole[:count] ** 2))) or 1e-12
    rms_diff = float(np.sqrt(np.mean(diff ** 2)))
    return {
        "block_size": float(block_size),
        "block_max_diff": float(np.max(np.abs(diff))) if diff.size else 0.0,
        "block_rms_diff": rms_diff,
        "block_rms_diff_db": 20.0 * math.log10(max(rms_diff, 1e-12) / reference),
    }


# ---------------------------------------------------------------------------
# the modules the instrument measures
# ---------------------------------------------------------------------------
#: The voice shaped modules: every one of them works on a signal that carries
#: a voice, and every one of them has to leave that voice alone.
VOICE_MODULES: tuple[str, ...] = (
    "AUR_VOICEDENOISE",
    "AUR_BREATH",
    "AUR_MOUTHDECLICK",
    "AUR_DEESS",
    "AUR_DIALOGUEISOLATE",
)


def measured_modules() -> list[str]:
    """The modules the harness can measure (the ones that are registered)."""
    from aurcore import registry  # local import: the harness may run stand alone

    return [code for code in VOICE_MODULES if registry.has(code)]
