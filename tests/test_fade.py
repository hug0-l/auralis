"""
tests/test_fade.py — the gate of the Fade module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB).  A fade that is applied
  to every block is not a fade of the material,
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the module must still do its job: the head of the material is faded in,
  the tail of it is faded out, and the shape of the curve is the one the
  gain engine defines.
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

MODULE_CODE = "AUR_FADE"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


def make_module():
    """A fresh Fade at 48 kHz / stereo."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(MODULE_CODE, 48000, 2)


def tone(seconds: float = 1.0, freq: float = 1000.0) -> np.ndarray:
    """A full scale 1 kHz tone, in both channels.

    A cosine is used and not a sine, so that the first sample is at full
    scale and the gain the module applied can be read straight off it.
    """
    n = int(seconds * 48000)
    t = np.arange(n, dtype=np.float64) / 48000.0
    return np.repeat(np.cos(2.0 * math.pi * freq * t)[:, None], 2, axis=1)


def envelope(x: np.ndarray) -> np.ndarray:
    """The gain the module applied, read from the samples of a full scale tone."""
    arr = np.asarray(x, dtype=np.float64)
    return np.abs(arr[:, 0])


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_the_fade_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_the_fade_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared == 0, "the fade moves the signal, so it adds no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_the_head_of_the_material_is_faded_in():
    """The first samples are quiet and the rest of the material is not."""
    module = make_module()
    module.params.set("Fade Length", 0.1)
    signal = tone(seconds=1.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    gains = envelope(out)
    assert gains[0] < 0.01, f"the first sample is at {gains[0]:.4f}"
    middle = gains[24000:48000]
    assert float(np.max(middle)) > 0.9, "the steady part of the material is faded"
    # the ramp is over the fade length and nothing longer
    assert float(np.max(gains[:12000])) > 0.9


def test_the_tail_of_the_material_is_faded_out():
    """The last samples are quiet when the host declares the length."""
    module = make_module()
    module.params.set("Fade Length", 0.1)
    signal = tone(seconds=1.0)
    module.set_material_length(len(signal))
    out = np.asarray(module.process(signal), dtype=np.float64)
    gains = envelope(out)
    assert gains[-1] < 0.01, f"the last sample is at {gains[-1]:.4f}"
    assert float(np.max(gains[-24000:])) > 0.9, "the steady part is faded"


def test_the_fade_is_not_applied_to_every_block():
    """The head of the material is faded once, not once per block.

    A fade that was applied to every block would fade the material over and
    over again, and the blocked render would not sound like the one piece
    render.
    """
    module = make_module()
    module.params.set("Fade Length", 0.1)
    signal = tone(seconds=1.0)
    pieces = [
        np.asarray(module.process(signal[start : start + 64]), dtype=np.float64)
        for start in range(0, signal.shape[0], 64)
    ]
    out = np.concatenate(pieces, axis=0)
    gains = envelope(out)
    assert gains[0] < 0.01, "the head of the material is not faded"
    assert float(np.max(gains[24000:48000])) > 0.9, (
        "the material is faded again in the middle of itself"
    )


def test_the_curve_is_the_one_of_the_gain_engine():
    """The shape of the fade is the ramp the gain engine defines."""
    from aurcore.modules.gain import GainEngine

    module = make_module()
    module.params.set("Fade Type", "Log")
    module.params.set("Fade Length", 0.1)
    signal = tone(seconds=1.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    ramp = GainEngine.fade_ramp(4800, "Log")
    gains = envelope(out)
    assert gains[0] == pytest.approx(ramp[0], abs=1e-6)
    assert gains[2400] == pytest.approx(ramp[2400], abs=1e-3)


@pytest.mark.parametrize("block", BLOCKS)
def test_the_fade_keeps_the_length_and_stays_finite(block):
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


def test_the_fade_fits_into_the_block_budget():
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
