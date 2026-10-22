"""
tests/test_center_extract.py — the gate of the Center Extract module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the module must still do its job: a centred source has to survive and a
  hard panned one has to go.
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

MODULE_CODE = "AUR_CENTEREXTRACT"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


def make_module():
    """A fresh Center Extract at 48 kHz / stereo."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(MODULE_CODE, 48000, 2)


def stereo_pair(seconds: float = 1.0) -> np.ndarray:
    """A centred 440 Hz tone and a 220 Hz tone in the left channel only."""
    n = int(seconds * 48000)
    t = np.arange(n, dtype=np.float64) / 48000.0
    centred = 0.501187 * np.sin(2.0 * math.pi * 440.0 * t)
    panned = 0.501187 * np.sin(2.0 * math.pi * 220.0 * t)
    out = np.zeros((n, 2), dtype=np.float64)
    out[:, 0] = centred + panned
    out[:, 1] = centred
    return out


def band_level_db(x: np.ndarray, lo_hz: float, hi_hz: float, channel: int = 0) -> float:
    """The level of a band of one channel of a buffer, in dBFS."""
    arr = np.asarray(x, dtype=np.float64)
    column = arr[:, channel]
    window = np.hanning(column.size)
    spectrum = np.fft.rfft(column * window)
    freqs = np.fft.rfftfreq(column.size, 1.0 / 48000.0)
    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    mean_square = 2.0 * power / (weight * weight)
    return 10.0 * math.log10(max(mean_square, 1e-30))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_the_center_extract_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_the_center_extract_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_a_centred_source_survives_and_a_panned_one_goes():
    """The centred tone stays, the panned tone is taken away."""
    module = make_module()
    signal = stereo_pair()
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    centred_before = band_level_db(signal[latency:], 410.0, 470.0, channel=1)
    centred_after = band_level_db(out[latency:], 410.0, 470.0, channel=1)
    assert centred_after - centred_before >= -1.5, (
        f"the centred tone lost {centred_before - centred_after:.2f} dB, it may "
        f"lose 1.5 dB"
    )
    panned_before = band_level_db(signal[latency:], 200.0, 240.0, channel=0)
    panned_after = band_level_db(out[latency:], 200.0, 240.0, channel=0)
    assert panned_before - panned_after >= 3.0, (
        f"the panned tone lost only {panned_before - panned_after:.2f} dB, it "
        f"has to lose at least 3 dB"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_center_extract_keeps_the_length_and_stays_finite(block):
    """The output is as long as the input and holds no NaN."""
    module = make_module()
    signal = stereo_pair(seconds=0.5)
    pieces = [
        np.asarray(module.process(signal[start : start + block]), dtype=np.float64)
        for start in range(0, signal.shape[0], block)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_the_center_extract_fits_into_the_block_budget():
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
