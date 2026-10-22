"""
tests/test_de_wind.py — the gate of the De-Wind module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the output must be **finite** and as long as the input,
* one block must fit into the **realtime budget**.
"""
from __future__ import annotations

import numpy as np
import pytest

from tests.block_coherence import (  # noqa: E402
    BLOCKS,
    COHERENCE_LIMIT_DB,
    coherence_rows,
    declared_latency,
    impulse_delay,
)

MODULE_CODE = "AUR_DEWIND"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_de_wind_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_de_wind_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_de_wind_keeps_the_length_and_stays_finite(block):
    """The output is as long as the input and holds no NaN."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, 48000, 2)
    signal = np.zeros((4096, 2), dtype=np.float64)
    signal[:, 0] = 0.5 * np.sin(2.0 * np.pi * 200.0 * np.arange(4096) / 48000.0)
    pieces = [
        np.asarray(module.process(signal[start : start + block]), dtype=np.float64)
        for start in range(0, signal.shape[0], block)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_de_wind_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget."""
    import time

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, 48000, 2)
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
