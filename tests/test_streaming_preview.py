"""
tests/test_streaming_preview.py — the gate of the Streaming Preview module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the module must still do its job: the programme has to come out at the
  playback loudness, under the ceiling, and with the band of the codec
  taken off.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from tests.block_coherence import (  # noqa: E402
    BLOCKS,
    COHERENCE_LIMIT_DB,
    coherence_rows,
    declared_latency,
    impulse_delay,
)

MODULE_CODE = "AUR_STREAMPREVIEW"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


def make_module():
    """A fresh Streaming Preview at 48 kHz / stereo."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(MODULE_CODE, 48000, 2)


def tone(
    peak_dbfs: float = -6.0, seconds: float = 4.0, freq: float = 1000.0
) -> np.ndarray:
    """A sine at a given peak level, in both channels."""
    n = int(seconds * 48000)
    t = np.arange(n, dtype=np.float64) / 48000.0
    wave = 10.0 ** (peak_dbfs / 20.0) * np.sin(2.0 * math.pi * freq * t)
    return np.repeat(wave[:, None], 2, axis=1)


def band_level_db(x: np.ndarray, lo_hz: float, hi_hz: float) -> float:
    """The level of a band of a buffer, in dBFS."""
    arr = np.asarray(x, dtype=np.float64)
    window = np.hanning(arr.shape[0])
    spectrum = np.fft.rfft(arr[:, 0] * window)
    freqs = np.fft.rfftfreq(arr.shape[0], 1.0 / 48000.0)
    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    mean_square = 2.0 * power / (weight * weight)
    return 10.0 * math.log10(max(mean_square, 1e-30))


def two_tone(peak_dbfs: float = -6.0, seconds: float = 1.0) -> np.ndarray:
    """A 1 kHz tone and an 18 kHz tone at the same level, in both channels."""
    n = int(seconds * 48000)
    t = np.arange(n, dtype=np.float64) / 48000.0
    amp = 10.0 ** (peak_dbfs / 20.0)
    wave = amp * (np.sin(2.0 * math.pi * 1000.0 * t) + np.sin(2.0 * math.pi * 18000.0 * t))
    return np.repeat(wave[:, None], 2, axis=1)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_the_streaming_preview_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_the_streaming_preview_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_the_preview_brings_the_programme_onto_the_playback_loudness():
    """The programme comes out at the playback loudness."""
    module = make_module()
    module.params.set("Playback Loudness", -16.0)
    signal = tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    played = out[latency:]
    before = band_level_db(signal[latency:], 950.0, 1050.0)
    after = band_level_db(played, 950.0, 1050.0)
    assert after - before == pytest.approx(-10.0, abs=1.0), (
        f"the programme moved by {after - before:.2f} dB, the preview brings a "
        f"-6 dBFS tone onto -16 LUFS"
    )


def test_the_preview_keeps_the_ceiling():
    """The output does not come out above the true peak ceiling."""
    module = make_module()
    module.params.set("Playback Loudness", 0.0)
    signal = tone(peak_dbfs=-1.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    peak = float(np.max(np.abs(out)))
    assert peak <= 10.0 ** (-1.0 / 20.0) * 1.001, (
        f"the preview comes out at {20 * math.log10(peak):.2f} dBFS, the "
        f"ceiling is -1.0 dBTP"
    )


def test_the_codec_takes_the_top_of_the_band_off():
    """A lossy codec takes the top of the band off."""
    module = make_module()
    module.params.set("Codec", "MP3")
    module.params.set("Quality", "High")
    signal = two_tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    top = band_level_db(out[latency:], 17000.0, 19000.0) - band_level_db(
        signal[latency:], 17000.0, 19000.0
    )
    low = band_level_db(out[latency:], 950.0, 1050.0) - band_level_db(
        signal[latency:], 950.0, 1050.0
    )
    assert top < low - 3.0, (
        f"the 18 kHz tone lost only {low - top:.2f} dB against the 1 kHz tone, "
        f"the codec cuts at 13.5 kHz"
    )


def test_a_lossless_codec_leaves_the_band_alone():
    """The lossless profile does not touch the band.

    The preview still brings the programme onto the playback loudness, and
    that gain is flat, so the top of the band moves by exactly as much as
    the bottom of it.
    """
    module = make_module()
    module.params.set("Codec", "Lossless")
    signal = two_tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    top = band_level_db(out[latency:], 17000.0, 19000.0) - band_level_db(
        signal[latency:], 17000.0, 19000.0
    )
    low = band_level_db(out[latency:], 950.0, 1050.0) - band_level_db(
        signal[latency:], 950.0, 1050.0
    )
    assert top == pytest.approx(low, abs=0.2)


@pytest.mark.parametrize("block", BLOCKS)
def test_the_streaming_preview_keeps_the_length_and_stays_finite(block):
    """The output is as long as the input and holds no NaN."""
    module = make_module()
    signal = tone(seconds=0.5)
    pieces = [
        np.asarray(module.process(signal[start : start + block]), dtype=np.float64)
        for start in range(0, signal.shape[0], block)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_the_streaming_preview_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget."""
    import time

    module = make_module()
    block = np.zeros((64, 2), dtype=np.float64)
    for _ in range(3):
        module.process(block)
    times = []
    for _ in range(15):
        start = time.perf_counter_ns()
        module.process(block)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    measured = float(np.median(times))
    assert measured <= CEILING_US_64, (
        f"a 64 sample block costs {measured:.0f} us, the ceiling is "
        f"{CEILING_US_64:.0f} us"
    )
