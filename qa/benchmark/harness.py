"""
qa.benchmark.harness — the instrument of the benchmark.

It measures **our** modules on the cases of :mod:`qa.benchmark.cases` and puts
the numbers next to the reference numbers of :mod:`qa.benchmark.reference`.

The measurement is deterministic.  Every case is read from the corpus or
built from a fixed seed, the analysis geometry is a constant, and the cost is
*counted* rather than timed, so two runs of the benchmark give the same
numbers.  A number that moves with the load of the machine does not belong
in a benchmark.

Two rulers are used, and each case says which one it wants:

* the **corpus ruler** — the bands of the corpus manifest (what has to go)
  and the partials of the manifest (what has to stay), measured with the
  quality instrument of the project (``qa/quality/measurement.py``);
* the **voice ruler** — the bands of the voice report (the voice and the bed
  around it) measured with the voice instrument of the project
  (``qa/qualityvoice/harness.py``).

The reference numbers were taken with the same rulers, so the two sides of
the table are measured with the same code.

The cost has one ruler, and it is the ruler of the project: one 1024 point
transform is worth **8.300 us**, so a 64 sample block of real time at 48 kHz
may cost **160.6** transforms.  Ours is counted in that unit, block by block,
the way a host drives a module, and the reference's is timed on the study
machine and converted into the same unit.  Both sides of the comparison are
therefore the cost of **one 64 sample block**, of the same signal, with the
same number of channels.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "corpus"), str(_ROOT / "qa" / "quality")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from aurcore import registry  # noqa: E402

import measure as corpus_measure  # noqa: E402  (corpus/measure.py)
import measurement as quality  # noqa: E402  (qa/quality/measurement.py)
from ground_truth import case_voice_hiss  # noqa: E402  (qa/quality/ground_truth.py)

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    """Load a sibling of the harness by path, under a name of its own.

    The siblings are loaded by path and not imported by their plain names,
    because the tree has several modules called ``harness`` and ``cases``
    and a plain name would let one of them stand in for another.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The cases of the benchmark.
cases = _load("aur_qa_benchmark_cases", _HERE / "cases.py")

#: The reference numbers.
reference = _load("aur_qa_benchmark_reference", _HERE / "reference.py")

#: The voice instrument of the project (``qa/qualityvoice/harness.py``).
voice_harness = _load(
    "aur_qa_qualityvoice_harness", _ROOT / "qa" / "qualityvoice" / "harness.py"
)

Case = cases.Case

__all__ = [
    "Row",
    "Cost",
    "render",
    "render_in_blocks",
    "measure_case",
    "collect",
    "format_table",
    "COST_BLOCK",
    "REFERENCE_TRANSFORM_US",
    "BUDGET_US_PER_BLOCK",
    "BUDGET_TRANSFORMS_PER_BLOCK",
]

#: The synthetic voice of the voice report.
voice_signal = voice_harness.voice_signal

#: The analysis geometry of the quality instrument.
N_FFT = quality.N_FFT
HOP = quality.HOP
SR = quality.SR

#: The number of frames the smoothed gain curve averages over.
SMOOTH_FRAMES = quality.SMOOTH_FRAMES

#: The fraction of the frames that is skipped before the steady state is read.
SETTLE = 0.25

#: The block the cost of a module is expressed in (samples).
COST_BLOCK = reference.COST_BLOCK

#: The yardstick of the cost, taken from the constants file of the benchmark
#: so that the two sides of the comparison cannot drift apart: one 1024 point
#: transform costs 8.300 us on the reference machine, so a 64 sample block of
#: real time at 48 kHz may cost 160.6 of them.
REFERENCE_TRANSFORM_US = reference.REFERENCE_TRANSFORM_US
BUDGET_US_PER_BLOCK = reference.BUDGET_US_PER_BLOCK
BUDGET_TRANSFORMS_PER_BLOCK = reference.BUDGET_TRANSFORMS_PER_BLOCK

_EPS = 1e-30


# ---------------------------------------------------------------------------
# the cost of one block
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Cost:
    """The cost of one 64 sample block of a module, and the geometry of it.

    Attributes
    ----------
    transforms:
        The transform equivalents of **one 64 sample block**, counted.
    total:
        The transform equivalents of the whole run the figure comes from.
    blocks:
        How many 64 sample blocks that run was made of.
    channels:
        How many channels the signal of the case carries.
    amortised:
        The cost of the whole clip render spread over the blocks of that
        clip.  It is kept because it says what the cost of a module looks
        like when the module is given the whole clip at once: a module whose
        analysis window is fixed does the same work in every block, and a
        whole clip render hides that.
    """

    transforms: float = 0.0
    total: float = 0.0
    blocks: int = 0
    channels: int = 0
    amortised: float = 0.0

    @property
    def us(self) -> float:
        """The cost of one block, in microseconds on the reference machine."""
        return float(self.transforms) * REFERENCE_TRANSFORM_US

    @property
    def share_of_budget(self) -> float:
        """The share of the realtime budget of a block the cost is."""
        return float(self.transforms) / max(BUDGET_TRANSFORMS_PER_BLOCK, 1e-9)


# ---------------------------------------------------------------------------
# the row of the table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Row:
    """One line of the table: a number of ours and the reference it is held to."""

    case: str
    module: str
    metric: str
    ours: float | None
    reference: float | None
    unit: str
    gap: float | None
    verdict: str
    note: str = ""
    #: How many channels the signal of the case carries.  It is part of the
    #: cost comparison: a block of two channels costs a module about twice
    #: what a block of one channel costs it.
    channels: int = 0

    def as_dict(self) -> dict:
        """The row as a dictionary, for the JSON result."""
        return {
            "case": self.case,
            "module": self.module,
            "metric": self.metric,
            "ours": self.ours,
            "reference": self.reference,
            "unit": self.unit,
            "gap": self.gap,
            "verdict": self.verdict,
            "note": self.note,
            "channels": self.channels,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _shift(a: np.ndarray, b: np.ndarray, latency: int) -> tuple[np.ndarray, np.ndarray]:
    """Align two signals by ``latency`` samples (positive = ``b`` is late)."""
    a = np.asarray(a)
    b = np.asarray(b)
    if latency > 0:
        b = b[latency:]
        a = a[: b.shape[0]]
    elif latency < 0:
        a = a[-latency:]
        b = b[: a.shape[0]]
    count = min(a.shape[0], b.shape[0])
    return a[:count], b[:count]


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    """Centred moving average; the edges are held at the edge value."""
    arr = np.asarray(values, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if arr.size < width:
        return np.full(arr.shape, float(np.mean(arr)))
    kernel = np.ones(width, dtype=np.float64) / float(width)
    smoothed = np.convolve(arr, kernel, mode="valid")
    head = np.full(width // 2, smoothed[0])
    tail = np.full(width - 1 - width // 2, smoothed[-1])
    return np.concatenate([head, smoothed, tail])


def _frame_count(n_samples: int, n_fft: int = N_FFT, hop: int = HOP) -> int:
    """How many analysis frames a signal of ``n_samples`` samples carries."""
    if n_samples < n_fft:
        return 0
    return 1 + (n_samples - n_fft) // hop


def _region_mask(count: int, start_s: float | None, end_s: float | None,
                 sr: int = SR, hop: int = HOP) -> np.ndarray:
    """The frames of a stretch of the signal, as a boolean mask."""
    mask = np.zeros(max(count, 0), dtype=bool)
    if count == 0:
        return mask
    hop_s = hop / float(sr)
    first = 0 if start_s is None else int(round(float(start_s) / hop_s))
    last = count if end_s is None else int(round(float(end_s) / hop_s))
    mask[max(first, 0): max(last, 0)] = True
    return mask


def _band_power(x: np.ndarray, low: float, high: float, mix: str, sr: int) -> float:
    """The power inside a band, with the quality instrument."""
    signal = corpus_measure.mix_signal(x, mix)
    return quality.band_power(signal, low, high, sr)


def _band_frames(x: np.ndarray, bands: Sequence[tuple[float, float]], mix: str,
                 sr: int) -> np.ndarray:
    """The power inside a set of bands, one value per frame."""
    signal = corpus_measure.mix_signal(x, mix)
    total = None
    for low, high in bands:
        series = quality.band_power_frames(signal, low, high, sr)
        total = series if total is None else total + series
    if total is None:
        return np.zeros(0, dtype=np.float64)
    return total


def _partial_level(x: np.ndarray, hz: float, mix: str, sr: int,
                   start_s: float | None, end_s: float | None) -> float:
    """The level of a partial over a stretch of the signal, in dB."""
    signal = corpus_measure.mix_signal(x, mix)
    count = _frame_count(int(signal.size))
    mask = _region_mask(count, start_s, end_s, sr)
    if not np.any(mask):
        return -300.0
    return quality.tone_level(signal, hz, sr, frames=mask)


# ---------------------------------------------------------------------------
# the cost
# ---------------------------------------------------------------------------
def _batches(shape: Sequence[int], axis: int) -> int:
    """How many one dimensional transforms a call over ``shape`` performs."""
    axis = int(axis)
    if axis < 0:
        axis += len(shape)
    count = 1
    for index, size in enumerate(shape):
        if index != axis:
            count *= int(size)
    return max(count, 1)


class TransformCounter:
    """Counts the transforms a render performs.

    The count is a count, not a time: the number of transforms does not move
    with the load of the machine, so two runs of the benchmark give the same
    numbers.  A transform of ``n`` points is worth ``n log2(n)`` units, and
    one 1024 point transform is worth one unit.
    """

    def __init__(self) -> None:
        self.work = 0.0
        self.calls = 0
        self._saved: tuple = ()

    def __enter__(self) -> "TransformCounter":
        self._saved = (np.fft.rfft, np.fft.irfft)
        counter = self

        def rfft(x, n=None, axis=-1):
            arr = np.asarray(x)
            length = int(n) if n else int(arr.shape[axis])
            counter._count(length, _batches(arr.shape, axis))
            return counter._saved[0](x, n=n, axis=axis)

        def irfft(x, n=None, axis=-1):
            arr = np.asarray(x)
            length = int(n) if n else 2 * (int(arr.shape[axis]) - 1)
            counter._count(length, _batches(arr.shape, axis))
            return counter._saved[1](x, n=n, axis=axis)

        np.fft.rfft = rfft  # type: ignore[assignment]
        np.fft.irfft = irfft  # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        np.fft.rfft = self._saved[0]  # type: ignore[assignment]
        np.fft.irfft = self._saved[1]  # type: ignore[assignment]

    def _count(self, length: int, batches: int = 1) -> None:
        length = max(int(length), 1)
        self.calls += max(int(batches), 1)
        self.work += (
            max(int(batches), 1) * length * math.log2(length) / (1024.0 * 10.0)
        )

    @property
    def equivalents(self) -> float:
        """The work of the render, in 1024 point transforms."""
        return self.work


# ---------------------------------------------------------------------------
# the render
# ---------------------------------------------------------------------------
def render(case: Case, x: np.ndarray, rate: int) -> tuple[np.ndarray, TransformCounter]:
    """Run the module that owns the case over a signal.

    The module is measured with its own settings, because that is what a user
    gets.  The transforms the render performs are counted on the way.
    """
    data = np.ascontiguousarray(np.asarray(x, dtype=np.float64))
    if data.ndim == 1:
        data = data[:, None]
    module = registry.create(case.module, int(rate), int(data.shape[1]))
    for key, value in case.settings_map.items():
        if module.params.has(key):
            module.params.set(key, value)
    with TransformCounter() as counter:
        out = np.asarray(module.process(data), dtype=np.float64)
    if out.ndim == 1:
        out = out[:, None]
    return out, counter


def render_in_blocks(case: Case, x: np.ndarray, rate: int,
                     block: int = COST_BLOCK) -> tuple[float, int, int]:
    """The transform equivalents of one 64 sample block, counted block wise.

    The reference's cost is the cost of **one 64 sample block**, so ours has
    to be the same figure and not the cost of a whole clip spread over the
    blocks of that clip: a module whose analysis window is fixed does the
    same work in every block, and a whole clip render hides that.  The module
    is therefore driven the way a host drives it, block by block, over the
    signal of the case and with the channels of that case.

    Returns the transform equivalents of the whole run, the number of blocks
    and the number of channels.
    """
    data = np.ascontiguousarray(np.asarray(x, dtype=np.float64))
    if data.ndim == 1:
        data = data[:, None]
    channels = int(data.shape[1])
    module = registry.create(case.module, int(rate), channels)
    for key, value in case.settings_map.items():
        if module.params.has(key):
            module.params.set(key, value)
    blocks = 0
    with TransformCounter() as counter:
        for start in range(0, int(data.shape[0]), int(block)):
            chunk = data[start:start + int(block)]
            out = np.asarray(module.process(chunk), dtype=np.float64)
            if out.ndim == 1:
                out = out[:, None]
            blocks += 1
    return counter.equivalents, blocks, channels


def _cost(case: Case, data: np.ndarray, rate: int,
          counter: TransformCounter) -> Cost:
    """The cost of one 64 sample block of the module of a case.

    The figure is the block wise one, because that is the figure the
    reference's cost is: one 64 sample block of the signal of the case, with
    the channels of the case.
    """
    samples = int(np.asarray(data).shape[0])
    total, blocks, channels = render_in_blocks(case, data, rate)
    amortised = float(counter.equivalents) * float(COST_BLOCK) / float(max(samples, 1))
    per_block = total / float(max(blocks, 1))
    return Cost(
        transforms=per_block,
        total=float(total),
        blocks=int(blocks),
        channels=int(channels),
        amortised=amortised,
    )


# ---------------------------------------------------------------------------
# the corpus cases
# ---------------------------------------------------------------------------
def _manifest_bands(clip: str) -> list[dict]:
    entry = corpus_measure.manifest_entry(clip)
    return list(entry.get("annotations", {}).get("bands", []))


def _manifest_frequencies(clip: str) -> list[dict]:
    entry = corpus_measure.manifest_entry(clip)
    return list(entry.get("annotations", {}).get("frequencies", []))


def measure_corpus_case(case: Case) -> dict:
    """Measure our module on a clip of the corpus."""
    data, rate = corpus_measure.load_clip(case.name)
    out, counter = render(case, data, rate)
    cost = _cost(case, data, rate, counter)
    return measure_corpus_output(case, data, out, rate, cost)


def measure_corpus_output(case: Case, data: np.ndarray, out: np.ndarray,
                          rate: int, cost: Cost | None = None) -> dict:
    """Measure an output against a clip of the corpus.

    The output may come from our module or from a reference: the ruler is the
    same either way, which is what makes the two sides of the table
    comparable.
    """
    bands = _manifest_bands(case.name)
    wanted = _manifest_frequencies(case.name)
    by_name = {band["name"]: band for band in bands}
    chosen = [by_name[name] for name in case.artefact if name in by_name]
    if not chosen:
        raise KeyError(f"the manifest of {case.name} has no band {case.artefact}")
    mix = str(chosen[0].get("mix", "mono"))
    limits = [(float(band["band_hz"][0]), float(band["band_hz"][1])) for band in chosen]

    latency = quality.estimate_latency(
        corpus_measure.mix_signal(data, "mono"),
        corpus_measure.mix_signal(out, "mono"),
        int(rate),
    )
    before, after = _shift(data, out, latency)

    # -- what is removed ----------------------------------------------------
    power_in = sum(_band_power(before, low, high, mix, int(rate)) for low, high in limits)
    power_out = sum(_band_power(after, low, high, mix, int(rate)) for low, high in limits)
    removed_db = 10.0 * math.log10(max(power_in, _EPS) / max(power_out, _EPS))

    # -- the gain curve -----------------------------------------------------
    frames_in = _band_frames(before, limits, mix, int(rate))
    frames_out = _band_frames(after, limits, mix, int(rate))
    count = min(frames_in.size, frames_out.size)
    gain_var = 0.0
    gain_var_smooth = 0.0
    if count >= 3:
        ratio = np.maximum(frames_out[:count], _EPS) / np.maximum(frames_in[:count], _EPS)
        curve = 10.0 * np.log10(ratio)[int(count * SETTLE):]
        if curve.size >= 3:
            gain_var = float(np.var(curve))
        smooth_in = _moving_average(frames_in[:count], SMOOTH_FRAMES)
        smooth_out = _moving_average(frames_out[:count], SMOOTH_FRAMES)
        smooth = 10.0 * np.log10(
            np.maximum(smooth_out, _EPS) / np.maximum(smooth_in, _EPS)
        )[int(count * SETTLE):]
        if smooth.size >= 3:
            gain_var_smooth = float(np.var(smooth))

    # -- what is kept -------------------------------------------------------
    kept_db = 0.0
    shifts: dict[str, float] = {}
    wanted_by_name = {item["name"]: item for item in wanted}
    for name in case.wanted:
        item = wanted_by_name.get(name)
        if item is None:
            continue
        region = item.get("region_s") or [None, None]
        start_s = float(region[0]) if region[0] is not None else None
        end_s = float(region[1]) if region[1] is not None else None
        level_in = _partial_level(before, float(item["hz"]), str(item.get("mix", mix)),
                                  int(rate), start_s, end_s)
        level_out = _partial_level(after, float(item["hz"]), str(item.get("mix", mix)),
                                   int(rate), start_s, end_s)
        shifts[name] = float(level_out - level_in)
        kept_db = max(kept_db, abs(shifts[name]))

    seconds = float(data.shape[0]) / float(rate)
    measured = cost if cost is not None else Cost()
    return {
        "removed_db": float(removed_db),
        "kept_db": float(kept_db) if case.wanted else None,
        "gain_var_db2": gain_var,
        "gain_var_smooth_db2": gain_var_smooth,
        "latency_samples": int(latency),
        "cost_transform_eq": float(measured.transforms),
        "cost_transform_eq_total": float(measured.total),
        "cost_transform_eq_per_s": float(measured.total) / max(seconds, 1e-9),
        "cost_transform_eq_amortised": float(measured.amortised),
        "cost_us_per_block": float(measured.us),
        "cost_block": COST_BLOCK,
        "cost_channels": int(measured.channels),
        "cost_blocks": int(measured.blocks),
        "shifts_db": shifts,
        "output_finite": bool(np.all(np.isfinite(out))),
    }


# ---------------------------------------------------------------------------
# the voice cases
# ---------------------------------------------------------------------------
def _voice_case_signal(case: Case) -> tuple[np.ndarray, tuple[float, float],
                                           tuple[float, float], bool]:
    """The signal of a voice case, with the two bands and the stereo flag.

    The corpus voice clip is read in its **mid** channel, the way the voice
    report reads it: the manifest puts the voice in the mid and the bed in
    the difference between the channels, and the mid channel carries both the
    voice and the bed, so one signal carries the whole case.
    """
    if case.name == "corpus_voice":
        clip, _rate = corpus_measure.load_clip("voice_hiss")
        mid = np.asarray(clip, dtype=np.float64).mean(axis=1)
        return mid[:, None], (100.0, 4000.0), (5000.0, 20000.0), False
    if case.name == "synthetic_voice":
        voice = voice_signal.synth_voice()
        rng = np.random.default_rng(20_260_102)
        count = int(voice.size)
        white = rng.standard_normal(count)
        white -= white.mean()
        white *= (10.0 ** (-45.0 / 20.0)) / max(float(np.sqrt(np.mean(white * white))), 1e-12)
        signal = (np.asarray(voice, dtype=np.float64) + white)[:, None]
        return signal, (120.0, 3000.0), (5000.0, 14000.0), False
    if case.name == "speech_shaped":
        ground = case_voice_hiss()
        signal = np.asarray(ground.mixed, dtype=np.float64)[:, None]
        return signal, (120.0, 3000.0), (5000.0, 14000.0), False
    raise KeyError(f"unknown voice case {case.name!r}")


def _align(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Align the output with the input and report the delay."""
    latency = int(voice_harness.measure_latency(x, y))
    span = x.shape[0] - abs(latency)
    if span <= 4096:
        span = x.shape[0] - 4096
    if latency >= 0:
        return x[:span], y[latency: latency + span], latency
    return x[-latency: -latency + span], y[:span], latency


def measure_voice_case(case: Case) -> dict:
    """Measure our module on one of the voice cases."""
    signal, _voice_band, _hiss_band, _stereo = _voice_case_signal(case)
    out, counter = render(case, signal, SR)
    cost = _cost(case, signal, SR, counter)
    return measure_voice_output(case, signal, out, SR, cost)


def measure_voice_output(case: Case, signal: np.ndarray, out: np.ndarray,
                         rate: int, cost: Cost | None = None) -> dict:
    """Measure an output against one of the voice cases.

    The ruler is the voice instrument of the project, so our module and a
    reference are measured with the same code.
    """
    _signal, voice_band, hiss_band, stereo = _voice_case_signal(case)
    if out.shape[0] < signal.shape[0]:
        pad = np.zeros((signal.shape[0] - out.shape[0], out.shape[1]), dtype=np.float64)
        out = np.concatenate([out, pad], axis=0)
    out = out[: signal.shape[0]]

    if stereo:
        x_voice = signal.mean(axis=1)
        x_hiss = 0.5 * (signal[:, 0] - signal[:, 1])
        y_voice = out.mean(axis=1)
        y_hiss = 0.5 * (out[:, 0] - out[:, 1])
    else:
        x_voice = signal[:, 0]
        x_hiss = signal[:, 0]
        y_voice = out[:, 0]
        y_hiss = out[:, 0]

    va, vb, latency = _align(x_voice, y_voice)
    ha, hb, _ = _align(x_hiss, y_hiss)

    voice_before = voice_harness.band_energy_db(va, *voice_band)
    voice_after = voice_harness.band_energy_db(vb, *voice_band)
    hiss_before = voice_harness.band_energy_db(ha, *hiss_band)
    hiss_after = voice_harness.band_energy_db(hb, *hiss_band)
    chatter = voice_harness.measure_chatter(ha, hb, hiss_band)

    seconds = float(signal.shape[0]) / float(rate)
    measured = cost if cost is not None else Cost()
    return {
        "removed_db": float(hiss_before - hiss_after),
        "kept_db": float(abs(voice_after - voice_before)),
        "chatter_db": float(chatter["chatter_db"]),
        "gain_var_db2": float(chatter["chatter_gain_std_db"]) ** 2,
        "latency_samples": int(latency),
        "cost_transform_eq": float(measured.transforms),
        "cost_transform_eq_total": float(measured.total),
        "cost_transform_eq_per_s": float(measured.total) / max(seconds, 1e-9),
        "cost_transform_eq_amortised": float(measured.amortised),
        "cost_us_per_block": float(measured.us),
        "cost_block": COST_BLOCK,
        "cost_channels": int(measured.channels),
        "cost_blocks": int(measured.blocks),
        "shifts_db": {"voice": float(voice_after - voice_before)},
        "output_finite": bool(np.all(np.isfinite(out))),
    }


# ---------------------------------------------------------------------------
# the measurement of one case
# ---------------------------------------------------------------------------
def measure_case(case: Case) -> dict:
    """Measure our module on one case, with the ruler the case asks for."""
    if case.kind == "corpus":
        return measure_corpus_case(case)
    if case.kind == "voice":
        return measure_voice_case(case)
    raise KeyError(f"unknown kind of case {case.kind!r}")


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------
def collect(cases: Sequence[Case] | None = None) -> list[Row]:
    """Measure every case and build the rows of the table."""
    rows: list[Row] = []
    for case in cases or _all_cases():
        try:
            numbers = measure_case(case)
        except Exception as exc:  # noqa: BLE001 - a broken module is reported
            rows.append(Row(
                case=case.name,
                module=case.module,
                metric="measurement",
                ours=None,
                reference=None,
                unit="-",
                gap=None,
                verdict="error",
                note="%s: %s" % (type(exc).__name__, exc),
            ))
            continue
        for name in _metrics_of(case):
            metric = reference.metric_for(name)
            ours = numbers.get(name)
            target = reference.reference_for(case.name, name)
            gap = None
            verdict = "no reference"
            if ours is not None and target is not None:
                if metric.higher_is_better:
                    gap = float(ours) - float(target)
                else:
                    gap = float(target) - float(ours)
                verdict = "ahead" if gap >= 0.0 else "behind"
            rows.append(Row(
                case=case.name,
                module=case.module,
                metric=name,
                ours=ours,
                reference=target,
                unit=metric.unit,
                gap=gap,
                verdict=verdict,
                note=metric.note,
                channels=int(numbers.get("cost_channels", 0) or 0),
            ))
    return rows


def _all_cases() -> tuple:
    return cases.CASES


def _metrics_of(case: Case) -> tuple[str, ...]:
    """The metrics a case is measured on."""
    if case.kind == "voice":
        return ("removed_db", "kept_db", "chatter_db", "latency_samples",
                "cost_transform_eq")
    return ("removed_db", "kept_db", "gain_var_db2", "latency_samples",
            "cost_transform_eq")


def format_table(rows: Sequence[Row]) -> str:
    """The table, as text."""
    header = ("case", "module", "metric", "ours", "reference", "gap", "verdict")
    widths = [len(name) for name in header]
    body: list[tuple[str, ...]] = []
    for row in rows:
        ours = "-" if row.ours is None else _number(row.ours, row.unit)
        reference = "-" if row.reference is None else _number(row.reference, row.unit)
        gap = "-" if row.gap is None else "%+.2f" % row.gap
        body.append((
            row.case,
            row.module,
            row.metric,
            ours,
            reference,
            gap,
            row.verdict,
        ))
        for index, cell in enumerate(body[-1]):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(name.ljust(widths[index]) for index, name in enumerate(header))]
    lines.append("  ".join("-" * width for width in widths))
    for cells in body:
        lines.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)))
    return "\n".join(lines)


def _number(value: float, unit: str) -> str:
    """A number with its unit, in the precision the metric deserves."""
    if unit == "samples":
        return "%d" % int(round(value))
    if unit == "transforms":
        return "%.2f" % value
    return "%.2f" % value
