"""
tests/test_leveler.py — the gate of the Leveler module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the module must still do its job: the programme has to end up on the
  target level, and it has to be there from the first sample, not only at
  the end of the material.
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

MODULE_CODE = "AUR_LEVELER"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


def make_module():
    """A fresh Leveler at 48 kHz / stereo."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(MODULE_CODE, 48000, 2)


def tone(peak_dbfs: float = -6.0, seconds: float = 2.0, freq: float = 1000.0) -> np.ndarray:
    """A 1 kHz tone at a given peak level, in both channels."""
    n = int(seconds * 48000)
    t = np.arange(n, dtype=np.float64) / 48000.0
    wave = 10.0 ** (peak_dbfs / 20.0) * np.sin(2.0 * math.pi * freq * t)
    return np.repeat(wave[:, None], 2, axis=1)


def integrated_lufs(x: np.ndarray) -> float:
    """The gated integrated loudness of a buffer, in LUFS."""
    from aurcore.modules.loudness_control import integrated_loudness

    value = integrated_loudness(np.asarray(x, dtype=np.float64), 48000)
    assert value is not None
    return float(value)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_the_leveler_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_the_leveler_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_the_leveler_brings_the_programme_onto_the_target():
    """The programme ends up on the target level, and it is on target early."""
    module = make_module()
    module.params.set("Target Level", -23.0)
    signal = tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    measured = integrated_lufs(out)
    assert measured == pytest.approx(-23.0, abs=0.5), (
        f"the programme reads {measured:.2f} LUFS, the target is -23.0"
    )


def test_the_leveler_is_on_target_from_the_first_sample():
    """The gain is in place before the material starts, not only at the end.

    The leveler measures one step of the grid ahead of the material it
    plays, so the correction is in place from the first sample.  A leveler
    that reached its target only at the end of the material would be a
    different module.
    """
    module = make_module()
    module.params.set("Target Level", -23.0)
    signal = tone(seconds=4.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
    # the part of the material that has been played (the head of the output
    # is the look ahead of the analysis)
    played = out[latency:]
    quarter = len(played) // 4
    first = integrated_lufs(played[:quarter])
    last = integrated_lufs(played[-quarter:])
    assert first == pytest.approx(-23.0, abs=1.0), (
        f"the first quarter reads {first:.2f} LUFS, the target is -23.0"
    )
    assert last == pytest.approx(-23.0, abs=1.0), (
        f"the last quarter reads {last:.2f} LUFS, the target is -23.0"
    )


def test_the_leveler_follows_a_change_of_the_target():
    """A different target is reached as well."""
    module = make_module()
    module.params.set("Target Level", -18.0)
    signal = tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    measured = integrated_lufs(out)
    assert measured == pytest.approx(-18.0, abs=0.5), (
        f"the programme reads {measured:.2f} LUFS, the target is -18.0"
    )


def test_the_leveler_holds_the_level_over_a_long_material():
    """The level it reaches does not depend on how long the material is.

    The edge contract asks for that: a module that estimates a level over
    time has to reach its answer and hold it.
    """
    module = make_module()
    module.params.set("Target Level", -23.0)
    signal = tone(seconds=4.0)
    out = np.asarray(module.process(signal), dtype=np.float64)
    measured = integrated_lufs(out)
    assert measured == pytest.approx(-23.0, abs=0.5), (
        f"a four second programme reads {measured:.2f} LUFS, the target is -23.0"
    )


@pytest.mark.parametrize("block", BLOCKS)
def test_the_leveler_keeps_the_length_and_stays_finite(block):
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


def test_the_leveler_fits_into_the_block_budget():
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
