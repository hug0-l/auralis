"""
qa.reverb.harness — the measurement harness of the De-Reverb module.

The harness answers the questions a reverberation remover has to answer
before it ships:

* **does it remove the tail?**  The reverberant field of the golden corpus
  clip ``reverb`` is measured before and after the module.
* **does it damage the wanted sound?**  The 440 Hz starter of that clip is
  the direct sound the user wants to keep; its level is measured before and
  after.
* **does it shorten the decay?**  The reverberation time of the tail is
  measured before and after.
* **does it fit into the realtime budget?**  The time of one block is
  measured at 64, 128, 256 and 512 samples against the budget of the block
  (1333 us at 64 samples and 48 kHz).
* **is it safe at the edges?**  Silence, a constant signal, a full scale
  square and a single impulse must come back finite and with the same
  length.
* **is it block coherent?**  The result of a one block render is compared
  with the result of a render that is chopped into blocks.

Everything is measured with the helpers of :mod:`corpus.measure`, so the
numbers are the same kind of numbers the rest of the project reports.

Usage
-----
::

    from qa.reverb import harness

    report = harness.measure_module()
    print(report["wanted_removed_db"])

The harness never touches ``aurcore`` outside the module it measures, and
it imports nothing that the DSP core does not already import.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from corpus import measure as corpus_measure  # noqa: E402

__all__ = [
    "BLOCK_SIZES",
    "BUDGET_US",
    "CHANNELS",
    "CLIP_ID",
    "EDGE_CASES",
    "SAMPLE_RATE",
    "block_budget_us",
    "edge_cases",
    "measure_block_coherence",
    "measure_edges",
    "measure_latency",
    "measure_module",
    "measure_reverb",
    "measure_timing",
    "render",
    "starter_window",
    "wanted_level_db",
]

#: The sample rate every measurement runs at.
SAMPLE_RATE: int = corpus_measure.SAMPLE_RATE

#: The channel count every measurement runs at.
CHANNELS: int = 2

#: The corpus clip the measurements are taken on.
CLIP_ID: str = "reverb"

#: The block sizes a plug-in host realistically uses.
BLOCK_SIZES: tuple[int, ...] = (64, 128, 256, 512)

#: The realtime budget of a 64 sample block at 48 kHz, in microseconds.
BUDGET_US: float = 1333.3

#: The names of the awkward inputs the edges are checked with.
EDGE_CASES: tuple[str, ...] = ("silence", "dc", "square", "impulse", "tone")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def block_budget_us(block: int, sample_rate: int = SAMPLE_RATE) -> float:
    """The realtime budget of one block, in microseconds.

    A block of ``block`` samples has to be processed in the time it takes to
    play it, so the budget is ``block / sample_rate`` seconds.
    """
    return (float(block) / float(sample_rate)) * 1_000_000.0


def _module(code: str = "AUR_DEREVERB", channels: int = CHANNELS, **params: Any):
    """Build the module and apply the parameters."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, SAMPLE_RATE, channels)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def render(signal: np.ndarray, block: int | None = None, **params: Any) -> np.ndarray:
    """Run the module over a signal.

    Parameters
    ----------
    signal:
        Audio, shape ``(n_samples,)`` or ``(n_samples, channels)``.
    block:
        ``None`` renders the whole signal in one block; an integer chops the
        signal into blocks of that size, the way a plug-in host does.
    params:
        Parameter values for the module.

    Returns
    -------
    numpy.ndarray
        The processed signal, with the same shape as the input.
    """
    buf = np.asarray(signal, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    module = _module(channels=buf.shape[1], **params)
    if block is None:
        return np.asarray(module.process(buf), dtype=np.float64)
    pieces = [
        np.asarray(module.process(buf[start : start + block]), dtype=np.float64)
        for start in range(0, buf.shape[0], block)
    ]
    return np.concatenate(pieces, axis=0)


# ---------------------------------------------------------------------------
# the wanted part and the tail
# ---------------------------------------------------------------------------
def starter_window(clip_id: str = CLIP_ID) -> tuple[float, float, float, int, int]:
    """The window of the wanted 440 Hz starter of the clip.

    The ground truth comes from the manifest of the corpus: the starter
    starts at ``starter_start_s`` and lasts ``starter_length_s``.

    Returns
    -------
    tuple
        ``(start_s, end_s, tone_hz, first_sample, last_sample)``.
    """
    levels = corpus_measure.manifest_entry(clip_id)["annotations"]["levels"]
    start = float(levels["starter_start_s"])
    length = float(levels["starter_length_s"])
    first = int(round(start * SAMPLE_RATE))
    last = first + int(round(length * SAMPLE_RATE))
    return start, start + length, float(levels["tone_hz"]), first, last


def wanted_level_db(x: np.ndarray, clip_id: str = CLIP_ID) -> float:
    """The level of the wanted 440 Hz starter, in dBFS.

    The level is read from the band the starter lives in, over the window of
    the starter itself, so the measurement follows the direct sound and not
    the tail that follows it.
    """
    start, end, _tone, _first, _last = starter_window(clip_id)
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    return float(
        corpus_measure.band_energy_db(buf, SAMPLE_RATE, 410.0, 470.0, start, end)
    )


def wanted_amplitude(x: np.ndarray, clip_id: str = CLIP_ID) -> float:
    """The amplitude of the wanted 440 Hz starter, fitted in the time domain.

    A least squares fit of a 440 Hz sinusoid over the window of the starter
    reads the direct sound, and nothing else: the reverberant tail that
    starts in the same window is uncorrelated with the sinusoid.
    """
    _start, _end, tone, first, last = starter_window(clip_id)
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    segment = buf[first:last, 0]
    times = np.arange(first, last) / float(SAMPLE_RATE)
    basis = np.stack(
        [np.sin(2.0 * math.pi * tone * times), np.cos(2.0 * math.pi * tone * times)],
        axis=1,
    )
    coefficients, *_ = np.linalg.lstsq(basis, segment, rcond=None)
    return float(math.hypot(float(coefficients[0]), float(coefficients[1])))


def tail_level_db(x: np.ndarray, clip_id: str = CLIP_ID) -> float:
    """The level of the reverberant tail, in dBFS.

    The tail is measured over the region the manifest reserves for the
    reverberation time (``rt60``), which holds the decaying field and no
    starter.
    """
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    region = corpus_measure.manifest_entry(clip_id)["annotations"]["regions"]["rt60"]
    return float(
        corpus_measure.band_energy_db(
            buf, SAMPLE_RATE, 20.0, 20000.0, float(region[0]), float(region[1])
        )
    )


def rt60_s(x: np.ndarray, clip_id: str = CLIP_ID) -> float | None:
    """The reverberation time of the tail region, in seconds."""
    buf = np.asarray(x, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    region = corpus_measure.manifest_entry(clip_id)["annotations"]["regions"]["rt60"]
    return corpus_measure.rt60(buf, SAMPLE_RATE, float(region[0]), float(region[1]))


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def measure_reverb(
    clip_id: str = CLIP_ID,
    block: int | None = None,
    **params: Any,
) -> dict[str, Any]:
    """Measure what the module does to the reverberant clip.

    Parameters
    ----------
    clip_id:
        The corpus clip to measure.
    block:
        ``None`` renders the clip in one block; an integer chops it into
        blocks of that size.
    params:
        Parameter values for the module.

    Returns
    -------
    dict
        The levels before and after, the three differences that matter
        (``tail_removed_db``, ``wanted_removed_db`` and ``rt60_change_s``)
        and the reverberation times.
    """
    clip, sample_rate = corpus_measure.load_clip(clip_id)
    if int(sample_rate) != SAMPLE_RATE:
        raise ValueError(f"{clip_id}: expected {SAMPLE_RATE} Hz, found {sample_rate}")

    out = render(clip, block=block, **params)
    before_wanted = wanted_level_db(clip, clip_id)
    after_wanted = wanted_level_db(out, clip_id)
    before_amp = wanted_amplitude(clip, clip_id)
    after_amp = wanted_amplitude(out, clip_id)
    before_tail = tail_level_db(clip, clip_id)
    after_tail = tail_level_db(out, clip_id)
    before_rt = rt60_s(clip, clip_id)
    after_rt = rt60_s(out, clip_id)

    _start, _end, _tone, first, last = starter_window(clip_id)
    window_in = np.asarray(clip, dtype=np.float64)[first:last, 0]
    window_out = np.asarray(out, dtype=np.float64)[first:last, 0]
    difference = window_out - window_in
    window_rms = float(np.sqrt(np.mean(window_in ** 2))) or 1e-12
    window_diff = float(np.sqrt(np.mean(difference ** 2)))

    return {
        "clip_id": clip_id,
        "block": block,
        "params": dict(params),
        "wanted_in_db": before_wanted,
        "wanted_out_db": after_wanted,
        "wanted_removed_db": after_wanted - before_wanted,
        "wanted_amplitude_in": before_amp,
        "wanted_amplitude_out": after_amp,
        "wanted_gain_db": 20.0 * math.log10(max(after_amp, 1e-15) / max(before_amp, 1e-15)),
        "wanted_window_diff_db": 20.0 * math.log10(max(window_diff, 1e-15) / window_rms),
        "tail_in_db": before_tail,
        "tail_out_db": after_tail,
        "tail_removed_db": after_tail - before_tail,
        "rt60_in_s": before_rt,
        "rt60_out_s": after_rt,
        "rt60_change_s": (after_rt - before_rt) if (after_rt and before_rt) else None,
        "peak_out_dbfs": float(corpus_measure.peak_db(out)),
        "length_in": int(clip.shape[0]),
        "length_out": int(out.shape[0]),
    }


# ---------------------------------------------------------------------------
# latency and block coherence
# ---------------------------------------------------------------------------
def measure_latency(x: np.ndarray, y: np.ndarray, max_lag: int = 4096) -> int:
    """The delay of the output against the input, in samples.

    The delay is the lag with the largest correlation between the input and
    the output, so a module that does not delay its output reports zero.
    """
    a = np.asarray(x, dtype=np.float64).ravel()
    b = np.asarray(y, dtype=np.float64).ravel()
    count = min(a.size, b.size)
    if count < 2:
        return 0
    a = a[:count] - float(np.mean(a[:count]))
    b = b[:count] - float(np.mean(b[:count]))
    best = -np.inf
    best_lag = 0
    for lag in range(0, max(1, min(max_lag, count - 1))):
        value = float(np.dot(a[: count - lag], b[lag:]))
        if value > best:
            best = value
            best_lag = lag
    return int(best_lag)


def measure_block_coherence(
    signal: np.ndarray,
    block_sizes: Iterable[int] = BLOCK_SIZES,
    **params: Any,
) -> list[dict[str, Any]]:
    """Compare a one block render with a render that is chopped into blocks.

    A block coherent module returns the same samples no matter how the host
    chops the stream.  The difference is reported both as an absolute RMS
    level and relative to the level of the one block render.

    Returns
    -------
    list of dict
        One entry per block size.
    """
    buf = np.asarray(signal, dtype=np.float64)
    if buf.ndim == 1:
        buf = buf[:, None]
    whole = render(buf, block=None, **params)
    results: list[dict[str, Any]] = []
    for block in block_sizes:
        chunked = render(buf, block=int(block), **params)
        count = min(whole.shape[0], chunked.shape[0])
        difference = whole[:count] - chunked[:count]
        reference = float(np.sqrt(np.mean(whole[:count] ** 2))) or 1e-12
        rms = float(np.sqrt(np.mean(difference ** 2)))
        results.append(
            {
                "block": int(block),
                "max_diff": float(np.max(np.abs(difference))) if difference.size else 0.0,
                "rms_diff": rms,
                "rms_diff_db": 20.0 * math.log10(max(rms, 1e-12) / reference),
            }
        )
    return results


# ---------------------------------------------------------------------------
# the edges
# ---------------------------------------------------------------------------
def edge_cases(frames: int = 4096, sample_rate: int = SAMPLE_RATE) -> dict[str, np.ndarray]:
    """The awkward inputs the module has to survive."""
    silence = np.zeros((frames, 2), dtype=np.float64)
    dc = np.full((frames, 2), 0.5, dtype=np.float64)
    square = (
        np.sign(np.sin(2.0 * np.pi * 220.0 * np.arange(frames) / sample_rate))[:, None]
        * np.ones((1, 2))
    )
    impulse = np.zeros((frames, 2), dtype=np.float64)
    impulse[frames // 2, 0] = 1.0
    tone = (
        0.5 * np.sin(2.0 * np.pi * 1000.0 * np.arange(frames) / sample_rate)
    )[:, None] * np.ones((1, 2))
    return {
        "silence": silence,
        "dc": dc,
        "square": square,
        "impulse": impulse,
        "tone": tone,
    }


def measure_edges(
    frames: int = 4096,
    blocks: Sequence[int | None] = (None, 64),
    **params: Any,
) -> list[dict[str, Any]]:
    """Run the module over the awkward inputs and report what came back.

    Returns
    -------
    list of dict
        One entry per case and block size, with the length, the number of
        non finite samples and the peak of the output.
    """
    results: list[dict[str, Any]] = []
    for name, signal in edge_cases(frames).items():
        for block in blocks:
            out = render(signal, block=block, **params)
            bad = int(np.count_nonzero(~np.isfinite(out)))
            results.append(
                {
                    "case": name,
                    "block": block,
                    "length_in": int(signal.shape[0]),
                    "length_out": int(out.shape[0]),
                    "non_finite": bad,
                    "peak": float(np.max(np.abs(out))) if out.size else 0.0,
                    "rms": float(np.sqrt(np.mean(out ** 2))) if out.size else 0.0,
                    "over_full_scale": bool(out.size and float(np.max(np.abs(out))) > 1.0),
                }
            )
    return results


# ---------------------------------------------------------------------------
# the realtime budget
# ---------------------------------------------------------------------------
def measure_timing(
    block_sizes: Iterable[int] = BLOCK_SIZES,
    repeats: int = 15,
    warmup: int = 3,
    runs: int = 5,
    **params: Any,
) -> list[dict[str, Any]]:
    """Measure the time of one block against the realtime budget.

    The *fastest* of ``runs`` measurements is kept for every block size: a
    shared machine makes every timing noisy, and the fastest run is the one
    that was least disturbed by other work.  The fastest single block of the
    whole sweep is reported as well, so a reader can see how much of the
    spread is the machine and how much is the module.

    Returns
    -------
    list of dict
        One entry per block size, with the median time, the budget and the
        share of the budget the module eats.
    """
    clip, _sample_rate = corpus_measure.load_clip(CLIP_ID)
    signal = np.ascontiguousarray(clip)
    results: list[dict[str, Any]] = []
    for block in block_sizes:
        best: dict[str, Any] | None = None
        every: list[float] = []
        for _run in range(max(1, runs)):
            module = _module(channels=CHANNELS, **params)
            chunk = np.ascontiguousarray(signal[: int(block)])
            times: list[float] = []
            for index in range(max(0, warmup) + max(1, repeats)):
                start = time.perf_counter_ns()
                module.process(chunk)
                elapsed = (time.perf_counter_ns() - start) / 1000.0
                if index >= warmup:
                    times.append(elapsed)
            every.extend(times)
            median = float(np.median(times)) if times else 0.0
            entry = {
                "block": int(block),
                "budget_us": block_budget_us(int(block)),
                "median_us": median,
                "min_us": float(np.min(times)) if times else 0.0,
                "max_us": float(np.max(times)) if times else 0.0,
            }
            if best is None or entry["median_us"] < best["median_us"]:
                best = entry
        assert best is not None
        best["min_us"] = float(np.min(every)) if every else best["min_us"]
        best["max_us"] = float(np.max(every)) if every else best["max_us"]
        best["budget_fraction"] = best["median_us"] / best["budget_us"]
        best["over_budget"] = bool(best["median_us"] > best["budget_us"])
        results.append(best)
    return results


# ---------------------------------------------------------------------------
# the whole report
# ---------------------------------------------------------------------------
def measure_module(clip_id: str = CLIP_ID, **params: Any) -> dict[str, Any]:
    """Measure the module on the corpus clip and return the whole report.

    The report holds the quality numbers (tail, wanted part, reverberation
    time), the numbers of the block sizes the plug-in host uses, the edges
    and the block coherence.
    """
    clip, _sample_rate = corpus_measure.load_clip(clip_id)
    module = _module(channels=CHANNELS, **params)
    whole = np.asarray(module.process(clip), dtype=np.float64)

    latency_measured = measure_latency(clip[:, 0], whole[:, 0])
    by_block = [
        measure_reverb(clip_id=clip_id, block=int(block), **params)
        for block in BLOCK_SIZES
    ]
    return {
        "module": "AUR_DEREVERB",
        "clip_id": clip_id,
        "params": dict(params),
        "whole_block": measure_reverb(clip_id=clip_id, block=None, **params),
        "by_block": by_block,
        "timing": measure_timing(**params),
        "edges": measure_edges(**params),
        "block_coherence": measure_block_coherence(clip, **params),
        "latency_reported": int(module.latency_samples),
        "latency_measured": int(latency_measured),
    }
