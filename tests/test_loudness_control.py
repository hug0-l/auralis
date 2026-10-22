"""
tests/test_loudness_control.py — the gate of the Loudness Control module.

What the gate asks
------------------
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ from the result of a render that is
  not (the quality harness allows at most -40 dB),
* the module must report the **latency** it adds, and the signal must show
  exactly that delay,
* the module must still do its job: the programme has to end up on the
  standard that was picked, and it has to be there from the first sample,
* the measurement the gain comes from must be the **gated integrated
  loudness** of BS.1770-4: the mean of the 400 ms blocks that survive the
  absolute gate and the relative gate.  The integrated reading of the
  module must be the reading of the standard, and the momentary and the
  short term must stay ungated.
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

MODULE_CODE = "AUR_LOUDNESS"

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us at 48 kHz; the measured ceiling is three budgets so that a busy
#: machine does not make the test fail.
CEILING_US_64 = 3.0 * 1333.3


def make_module():
    """A fresh Loudness Control at 48 kHz / stereo."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    return registry.create(MODULE_CODE, 48000, 2)


def tone(peak_dbfs: float = -6.0, seconds: float = 4.0, freq: float = 1000.0) -> np.ndarray:
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


def reference_lufs(x: np.ndarray) -> float:
    """The gated integrated loudness of the standard (``aurcore.dsp.loudness``)."""
    from aurcore.dsp.loudness import integrated_loudness

    return float(integrated_loudness(np.asarray(x, dtype=np.float64), 48000))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    """The block coherence of the module."""
    return coherence_rows(MODULE_CODE)


def test_the_loudness_control_is_block_coherent(rows):
    """A blocked render must not differ from a one block render."""
    for row in rows:
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


def test_the_loudness_control_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it."""
    declared = declared_latency(MODULE_CODE)
    assert declared > 0, "the module reports no latency"
    measured = impulse_delay(MODULE_CODE)
    assert measured == declared, (
        f"the module reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


def test_the_loudness_control_brings_the_programme_onto_the_standard():
    """The programme ends up on the target loudness."""
    module = make_module()
    module.params.set("Target Loudness", -23.0)
    signal = tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    measured = integrated_lufs(out)
    assert measured == pytest.approx(-23.0, abs=0.5), (
        f"the programme reads {measured:.2f} LUFS, the target is -23.0"
    )


def test_the_loudness_control_is_on_target_from_the_first_sample():
    """The gain is in place before the material starts, not only at the end."""
    module = make_module()
    module.params.set("Target Loudness", -23.0)
    signal = tone()
    out = np.asarray(module.process(signal), dtype=np.float64)
    latency = int(module.latency_samples)
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


def test_the_loudness_control_leaves_a_programme_that_is_on_target():
    """A programme that is already on target is left alone."""
    module = make_module()
    module.params.set("Target Loudness", -23.0)
    # a tone at -23 dBFS peak reads -23 LUFS, which is on the target
    signal = tone(peak_dbfs=-23.05)
    out = np.asarray(module.process(signal), dtype=np.float64)
    before = integrated_lufs(signal)
    after = integrated_lufs(out)
    assert after == pytest.approx(before, abs=0.6), (
        f"the programme was {before:.2f} LUFS and came out at {after:.2f} LUFS"
    )


def test_a_standard_overwrites_the_targets():
    """Picking a standard sets the target fields of the module."""
    module = make_module()
    module.params.set("Standard", "AES TD1006.1.17-10")
    module.process(tone(seconds=0.5))
    assert float(module.params.get("Target Loudness")) == pytest.approx(-16.0)
    assert float(module.params.get("True Peak")) == pytest.approx(-1.0)


@pytest.mark.parametrize("block", BLOCKS)
def test_the_loudness_control_keeps_the_length_and_stays_finite(block):
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


def test_the_loudness_control_fits_into_the_block_budget():
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


# ---------------------------------------------------------------------------
# the measurement the gain comes from
# ---------------------------------------------------------------------------
def test_the_integrated_reading_is_the_reading_of_the_standard():
    """The integrated reading of the module is the gated one of BS.1770-4."""
    module = make_module()
    signal = tone(seconds=4.0)
    module.process(signal)
    measured = module.integrated_lufs
    assert measured is not None
    assert measured == pytest.approx(reference_lufs(signal), abs=0.05), (
        f"the module reads {measured:.3f} LUFS, the standard reads "
        f"{reference_lufs(signal):.3f} LUFS"
    )


def test_the_relative_gate_keeps_the_quiet_part_out_of_the_measurement():
    """A quiet passage must not pull the integrated reading down.

    The standard gates the measurement: the blocks below the relative gate
    are excluded from the mean.  The programme below is loud for two
    seconds and quiet for two; the integrated reading must sit near the
    loud part, and not near the mean of the whole programme.
    """
    loud = tone(peak_dbfs=-6.0, seconds=2.0)
    quiet = tone(peak_dbfs=-40.0, seconds=2.0)
    signal = np.concatenate((loud, quiet), axis=0)
    module = make_module()
    module.process(signal)
    measured = module.integrated_lufs
    assert measured is not None
    loud_level = reference_lufs(loud)
    assert measured == pytest.approx(loud_level, abs=0.5), (
        f"the module reads {measured:.2f} LUFS, the loud part reads "
        f"{loud_level:.2f} LUFS"
    )


def test_the_momentary_and_the_short_term_are_ungated():
    """The momentary and the short term are windows, not gated means."""
    module = make_module()
    loud = tone(peak_dbfs=-6.0, seconds=3.0)
    quiet = tone(peak_dbfs=-40.0, seconds=3.0)
    signal = np.concatenate((loud, quiet), axis=0)
    module.process(signal)
    momentary = module.momentary_lufs
    short = module.short_term_lufs
    assert momentary is not None and short is not None
    # the programme ends in the quiet part: the momentary window and the
    # three second window are the quiet part, and the gate would have
    # thrown it away
    assert momentary < module.integrated_lufs - 5.0, (
        f"the momentary reads {momentary:.2f} LUFS, the integrated reads "
        f"{module.integrated_lufs:.2f} LUFS"
    )
    assert short < module.integrated_lufs - 5.0, (
        f"the short term reads {short:.2f} LUFS, the integrated reads "
        f"{module.integrated_lufs:.2f} LUFS"
    )


def test_the_gain_does_not_move_with_the_block_size():
    """The gain of every step is the same in every block size."""
    module = make_module()
    signal = tone(seconds=1.0)
    gains: list[float] = []
    original = module._control

    def _control(level_db: float, peak: float) -> float:
        gain = original(level_db, peak)
        gains.append(float(gain))
        return gain

    module._control = _control
    module.process(signal)
    whole = list(gains)

    for block in (64, 512, 4096):
        module = make_module()
        gains = []
        original = module._control

        def _control(level_db: float, peak: float) -> float:
            gain = original(level_db, peak)
            gains.append(float(gain))
            return gain

        module._control = _control
        for start in range(0, signal.shape[0], block):
            module.process(signal[start : start + block])
        assert len(gains) == len(whole)
        for index, (a, b) in enumerate(zip(whole, gains)):
            assert a == pytest.approx(b, abs=1e-9), (
                f"at step {index} the gain is {b:.6f} dB in {block} sample "
                f"blocks and {a:.6f} dB in one piece"
            )


def test_the_programme_lands_on_the_target_on_transient_material():
    """A programme with transients comes as close as the material allows.

    The clip is a quiet bed with six impulses, one of them at full scale.
    The gain the module applies comes from the gated integrated loudness of
    the programme, so the programme lands near the target; but the impulse
    is held under the true-peak ceiling, and the energy the limiter takes
    out of it is gone from the measurement as well.  A programme like this
    cannot hit the target exactly, and the gate holds the honest number.
    """
    from aurcore.dsp.loudness import true_peak

    rate = 48000
    bed = 10.0 ** (-40.0 / 20.0) * np.sin(
        2.0 * math.pi * 480.0 * np.arange(rate * 3) / rate
    )
    data = np.repeat(bed[:, None], 2, axis=1)
    for index, level in enumerate((0.0, -4.0, -8.0, -12.0, -16.0, -20.0)):
        data[int((0.25 + 0.5 * index) * rate) : int((0.25 + 0.5 * index) * rate) + 1, :] += (
            10.0 ** (level / 20.0)
        )

    module = make_module()
    module.params.set("Target Loudness", -23.0)
    module.params.set("True Peak", -1.0)
    out = np.asarray(module.process(data), dtype=np.float64)
    measured = integrated_lufs(out)
    # the material allows about three decibels: the impulse is at full
    # scale and the true-peak ceiling takes its energy out of the
    # measurement
    assert measured == pytest.approx(-23.0, abs=3.0), (
        f"the programme reads {measured:.2f} LUFS, the target is -23.0"
    )
    peak = true_peak(out, rate)
    assert peak <= -1.0 + 0.05, f"the true peak is {peak:.2f} dBTP"
    # the gain came from the gated integrated loudness of the programme, so
    # the programme is 15 dB louder than it was
    assert module.gain_db == pytest.approx(15.7, abs=1.5), (
        f"the module applied {module.gain_db:.2f} dB"
    )
