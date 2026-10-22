"""
tests/test_spatial.py — the gate of the spatial suppressor.

The suppressor removes the content that does **not** belong to the stereo
image: the sound that lives in one channel only and the sound that lives in
the anti phase relation between the channels.  What it leaves is the content
the channels agree on.

The gate holds five rules:

``test_a_mono_signal_is_left_alone``
    a mono signal has no spatial relation at all, so the suppressor must not
    touch it — it may only delay it by the latency it declares.
``test_the_content_the_channels_agree_on_survives``
    a signal that is the same in both channels is common content: it comes
    out of the suppressor unchanged.
``test_the_content_of_one_channel_is_removed``
    the material of the corpus — the voice in the middle, the hiss in the
    sides — must lose the hiss in the sides and keep the voice in the
    middle.
``test_the_anti_phase_content_is_removed``
    a content that is mirrored between the channels is spatial difference,
    not common content, and it has to go.
``test_the_suppressor_never_invents_energy``
    the suppressor is an attenuator: the output may never carry more energy
    than the input.

Two more rules belong to the family: the output may not depend on the size
of the block the host uses, and the delay the module declares has to be the
delay the signal shows.

The material is the corpus's own: the clip ``voice_hiss`` (the voice in the
middle, the hiss in the sides) and the case where the very same material
sits in one channel, which is the mono mix of the clip in both channels.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
SURVEY = ROOT / "qa" / "survey"

for _path in (str(ROOT), str(CORPUS), str(SURVEY)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import harness as survey_harness  # noqa: E402
import measure as corpus_measure  # noqa: E402

from aurcore import registry  # noqa: E402
import aurcore.modules  # noqa: E402 - importing registers the modules  # noqa: F401

#: The code of the module under test.
CODE = "AUR_SPATIALSUPPRESSOR"

#: The sample rate the material is at.
RATE = 48000

#: The voice band of the corpus clip (the middle channel) and the hiss band
#: (the side channel), in hertz.
VOICE_BAND = (100.0, 4000.0)
HISS_BAND = (5000.0, 20000.0)

#: How much of the hiss has to go, in decibels.
HISS_REMOVAL_DB = 40.0

#: How far the voice may move, in decibels.
VOICE_BUDGET_DB = 0.5

#: How far the blocked render may differ from the one piece render.
BLOCK_COHERENCE_DB = -40.0


# ---------------------------------------------------------------------------
# the material
# ---------------------------------------------------------------------------
def corpus_clip() -> np.ndarray:
    """The corpus clip: the voice in the middle, the hiss in the sides."""
    data, _rate = corpus_measure.load_clip("voice_hiss")
    return np.asarray(data, dtype=np.float64)


def same_channel_case(clip: np.ndarray) -> np.ndarray:
    """The same material with the wanted and the unwanted content in one channel.

    The signal of both channels is the mono mix of the clip, so the two cases
    carry the same parts at the same level; only the spatial arrangement
    differs.
    """
    mono = (clip[:, 0] + clip[:, 1]) * 0.5
    return np.stack([mono, mono], axis=1)


def band_db(x: np.ndarray, band, mix: str) -> float:
    """The level of a band of a signal, in dBFS (the corpus's own reader)."""
    return float(
        corpus_measure.band_energy_db(x, RATE, float(band[0]), float(band[1]), None, None, mix)
    )


def run_module(x: np.ndarray, block: int | None = None, **params) -> np.ndarray:
    """Render a block of audio through the module."""
    module = registry.create(CODE, RATE, int(x.shape[1]))
    for name, value in params.items():
        module.params.set(name, value)
    if block:
        pieces = [
            np.asarray(module.process(x[start : start + int(block)]), dtype=np.float64)
            for start in range(0, x.shape[0], int(block))
        ]
        out = np.concatenate(pieces, axis=0)
    else:
        out = np.asarray(module.process(x), dtype=np.float64)
    assert out.shape == x.shape
    return out


def align(x: np.ndarray, y: np.ndarray, latency: int) -> np.ndarray:
    """Shift the output back by the latency of the module."""
    padded = np.concatenate([y, np.zeros((latency, y.shape[1]))], axis=0)
    return padded[latency : latency + x.shape[0]]


def energy(x: np.ndarray) -> float:
    """The energy of a signal."""
    return float(np.sum(np.asarray(x, dtype=np.float64) ** 2))


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_a_mono_signal_is_left_alone():
    """A mono signal has no spatial relation, so the suppressor leaves it."""
    rng = np.random.default_rng(20_260_210)
    signal = rng.standard_normal((4096, 1)) * 0.1
    module = registry.create(CODE, RATE, 1)
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    assert out.shape == signal.shape
    delayed = align(signal, out, latency)
    # the tail of the stream cannot be delayed: the input has ended
    keep = signal.shape[0] - latency
    assert np.allclose(delayed[:keep], signal[:keep], atol=1e-9), (
        "the mono signal was changed by the suppressor"
    )


def test_the_content_the_channels_agree_on_survives():
    """A signal that is the same in both channels comes out unchanged."""
    rng = np.random.default_rng(20_260_211)
    mono = rng.standard_normal(8192) * 0.1
    signal = np.stack([mono, mono], axis=1)
    module = registry.create(CODE, RATE, 2)
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    delayed = align(signal, out, latency)
    # the tail of the stream cannot be analysed and is not part of the test
    keep = signal.shape[0] - 4096
    assert np.allclose(delayed[:keep], signal[:keep], atol=1e-6), (
        "the common content was changed by the suppressor"
    )


def test_the_content_of_one_channel_is_removed():
    """The hiss in the sides goes, the voice in the middle stays."""
    clip = corpus_clip()
    module = registry.create(CODE, RATE, 2)
    out = np.asarray(module.process(clip), dtype=np.float64)
    aligned = align(clip, out, int(module.latency_samples))
    # the region the analysis has settled in and the tail of the stream has
    # not run out yet
    low, high = 24000, 72000
    before = clip[low:high]
    after = aligned[low:high]
    hiss = band_db(after, HISS_BAND, "side") - band_db(before, HISS_BAND, "side")
    voice = band_db(after, VOICE_BAND, "mid") - band_db(before, VOICE_BAND, "mid")
    assert hiss <= -HISS_REMOVAL_DB, (
        f"the hiss in the sides only lost {hiss:.2f} dB, the gate asks for "
        f"{-HISS_REMOVAL_DB:.1f} dB"
    )
    assert abs(voice) <= VOICE_BUDGET_DB, (
        f"the voice in the middle moved {voice:.2f} dB, the budget is "
        f"{VOICE_BUDGET_DB:.1f} dB"
    )


def test_the_same_channel_case_is_left_alone():
    """Where there is nothing to separate, the suppressor does nothing."""
    clip = corpus_clip()
    same = same_channel_case(clip)
    module = registry.create(CODE, RATE, 2)
    out = np.asarray(module.process(same), dtype=np.float64)
    aligned = align(same, out, int(module.latency_samples))
    low, high = 24000, 72000
    before = same[low:high]
    after = aligned[low:high]
    for band, mix in ((VOICE_BAND, "mid"), (HISS_BAND, "mono")):
        moved = band_db(after, band, mix) - band_db(before, band, mix)
        assert abs(moved) <= 0.05, (
            f"the band {band} moved {moved:.3f} dB where the channels are the "
            "same: there is nothing to separate"
        )


def test_the_anti_phase_content_is_removed():
    """A content that is mirrored between the channels is spatial difference."""
    rng = np.random.default_rng(20_260_212)
    t = np.arange(8192) / RATE
    tone = 0.2 * np.sin(2.0 * np.pi * 440.0 * t)
    signal = np.stack([tone, -tone], axis=1)
    module = registry.create(CODE, RATE, 2)
    out = np.asarray(module.process(signal), dtype=np.float64)
    aligned = align(signal, out, int(module.latency_samples))
    keep = 8192 - 2048
    # the mirrored content lives in the side signal; the mono mix of it is
    # silence, so the side is the signal that carries it
    before = band_db(signal[:keep], (300.0, 700.0), "side")
    after = band_db(aligned[:keep], (300.0, 700.0), "side")
    assert after - before <= -HISS_REMOVAL_DB, (
        f"the mirrored content only lost {before - after:.2f} dB"
    )


def test_the_suppressor_never_invents_energy():
    """The suppressor is an attenuator: it never adds energy."""
    clip = corpus_clip()
    for signal in (clip, same_channel_case(clip)):
        out = run_module(signal)
        assert energy(out) <= energy(signal) * (1.0 + 1e-9), (
            "the output carries more energy than the input"
        )


def test_the_output_is_finite_and_keeps_its_length():
    """The module hands back the same number of samples, all of them finite."""
    clip = corpus_clip()
    for block in (1, 64, 4096):
        out = run_module(clip[:8192], block=block)
        assert out.shape == (8192, 2)
        assert bool(np.all(np.isfinite(out)))


def test_the_module_is_block_coherent():
    """A blocked render must not differ from a one piece render."""
    clip = corpus_clip()
    one = run_module(clip)
    many = run_module(clip, block=64)
    delta = energy(one - many)
    reference = energy(one)
    level = 10.0 * np.log10(max(delta, 1e-300) / max(reference, 1e-300))
    assert level <= BLOCK_COHERENCE_DB, (
        f"the blocked render differs by {level:.2f} dB, the gate asks for "
        f"{BLOCK_COHERENCE_DB:.1f} dB"
    )


def test_the_module_reports_the_delay_it_adds():
    """The module declares the latency it adds, and the signal shows it."""
    module = registry.create(CODE, RATE, 2)
    declared = int(module.latency_samples)
    rng = np.random.default_rng(20_260_213)
    burst = 0.02 * rng.standard_normal(20000)
    signal = np.repeat(burst[:, None], 2, axis=1)
    out = np.asarray(module.process(signal), dtype=np.float64)
    lag = survey_harness.cross_correlation_delay(signal[:, 0], out[:, 0])
    assert -int(lag) == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{-int(lag)}"
    )
