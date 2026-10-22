"""
tests/test_de_reverb.py — the quality gate of the De-Reverb module.

The module is measured on the golden corpus clip ``reverb``: a 440 Hz
starter (the direct sound the user wants to keep) in a reverberant room
whose tail has an RT-60 of 1.2 s by construction.  The ground truth of the
clip is in the manifest of the corpus, so every number below is a
measurement and not an opinion.

What the gate asks
------------------
* the reverberant **tail** has to lose at least 5 dB,
* the wanted **440 Hz starter** must not move by more than 0.01 dB when the
  clip is rendered in one block, and must not move by more than 1 dB when the
  host chops the stream into the blocks a plug-in host uses,
* the **reverberation time** of the tail must not move,
* the module must be **block coherent**: the result of a render that is
  chopped into blocks must not differ much from the result of a render that
  is not,
* the module must report the **latency** it adds, and the delay measured on
  the signal must be the same number,
* the awkward inputs — silence, a constant signal, a full scale square, a
  single impulse and a tone — must come back **finite and with the same
  length**,
* one block must fit into the **realtime budget**.

The measurement harness lives in ``qa/reverb/`` and is shared with the
report (``qa/reverb/REPORT.md``), so the numbers in the failure messages
are the numbers of the report.

The numbers were measured on the reference machine with::

    python qa\\reverb\\measure_reverb.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

MODULE_CODE = "AUR_DEREVERB"

#: The tail has to lose at least this much level (dB).
TARGET_TAIL_REMOVAL_DB = 5.0

#: The wanted starter must not move by more than this in a one block render (dB).
TARGET_WANTED_DRIFT_DB = 0.01

#: The wanted starter must not move by more than this in a blocked render (dB).
TARGET_WANTED_DRIFT_BLOCKED_DB = 1.0

#: The tail has to lose at least this much in a blocked render (dB).
TARGET_TAIL_REMOVAL_BLOCKED_DB = 1.0

#: The reverberation time of the tail must not move by more than this (s).
TARGET_RT60_DRIFT_S = 0.05

#: The blocked render must not differ from the one block render by more than
#: this, relative to the level of the render (dB).  The quality harness asks
#: for -40 dB.
TARGET_BLOCK_COHERENCE_DB = -40.0

#: One 64 sample block may not cost more than this (us).  The budget is
#: 1333 us; the reference machine measures 646 us, the measured ceiling is
#: three times the budget so that a busy machine does not make the test
#: fail.
CEILING_US_64 = 3.0 * 1333.3

#: The number of samples the module is expected to delay its output with.
#: The analysis is centred, so the module has to look ahead by the length of
#: its analysis window.
EXPECTED_LATENCY_SAMPLES = 2048


# --------------------------------------------------------------------------
# The shared measurement harness
# --------------------------------------------------------------------------
def _load_harness():
    """Import the measurement harness from ``qa/reverb``."""
    path = Path(__file__).resolve().parents[1] / "qa" / "reverb" / "harness.py"
    if not path.is_file():
        pytest.skip(f"the measurement harness is missing ({path})")
    spec = importlib.util.spec_from_file_location("aur_qa_reverb", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


def _latency() -> int:
    """The latency the module reports."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, HARNESS.SAMPLE_RATE, HARNESS.CHANNELS)
    return int(module.latency_samples)


def _align(out: np.ndarray, latency: int) -> np.ndarray:
    """Shift a delayed output back so that it lines up with the input."""
    arr = np.asarray(out, dtype=np.float64)
    if latency <= 0:
        return arr
    aligned = arr[latency:]
    if aligned.shape[0] < arr.shape[0]:
        pad = np.zeros((arr.shape[0] - aligned.shape[0], arr.shape[1]), dtype=np.float64)
        aligned = np.concatenate([aligned, pad], axis=0)
    return aligned


def _measure(block: int | None = None) -> dict:
    """Measure what the module does to the reverberant clip.

    The module delays its output, so the render is shifted back before it is
    measured: the wanted starter and the tail are read at the positions of
    the clip they belong to.
    """
    clip, sample_rate = HARNESS.corpus_measure.load_clip(HARNESS.CLIP_ID)
    out = HARNESS.render(clip, block=block)
    out = _align(out, _latency())

    before_wanted = HARNESS.wanted_level_db(clip, HARNESS.CLIP_ID)
    after_wanted = HARNESS.wanted_level_db(out, HARNESS.CLIP_ID)
    before_amp = HARNESS.wanted_amplitude(clip, HARNESS.CLIP_ID)
    after_amp = HARNESS.wanted_amplitude(out, HARNESS.CLIP_ID)
    before_tail = HARNESS.tail_level_db(clip, HARNESS.CLIP_ID)
    after_tail = HARNESS.tail_level_db(out, HARNESS.CLIP_ID)
    before_rt = HARNESS.rt60_s(clip, HARNESS.CLIP_ID)
    after_rt = HARNESS.rt60_s(out, HARNESS.CLIP_ID)
    return {
        "wanted_removed_db": after_wanted - before_wanted,
        "wanted_gain_db": 20.0
        * np.log10(max(after_amp, 1e-15) / max(before_amp, 1e-15)),
        "tail_removed_db": after_tail - before_tail,
        "rt60_in_s": before_rt,
        "rt60_out_s": after_rt,
        "rt60_change_s": (after_rt - before_rt) if (after_rt and before_rt) else None,
    }


@pytest.fixture(scope="module")
def whole_block():
    """The measurement of the clip rendered in one block."""
    return _measure()


@pytest.fixture(scope="module")
def blocked():
    """The measurement of the clip rendered in the blocks a host uses."""
    return {
        int(block): _measure(block=int(block))
        for block in HARNESS.BLOCK_SIZES
    }


@pytest.fixture(scope="module")
def edges():
    """The measurement of the awkward inputs."""
    # the module delays its output, so the buffer has to be long enough for
    # the impulse to come back
    return HARNESS.measure_edges(frames=4 * EXPECTED_LATENCY_SAMPLES)


@pytest.fixture(scope="module")
def coherence():
    """The block coherence of the module."""
    return HARNESS.measure_block_coherence(
        HARNESS.corpus_measure.load_clip(HARNESS.CLIP_ID)[0]
    )


# --------------------------------------------------------------------------
# The tail
# --------------------------------------------------------------------------
def test_de_reverb_removes_the_reverberant_tail(whole_block):
    """The reverberant tail has to lose at least 5 dB.

    The tail is measured over the region the manifest reserves for the
    reverberation time, which holds the decaying field and no starter.
    """
    assert whole_block["tail_removed_db"] <= -TARGET_TAIL_REMOVAL_DB, (
        f"the tail only lost {whole_block['tail_removed_db']:.2f} dB, "
        f"the gate asks for {TARGET_TAIL_REMOVAL_DB:.1f} dB"
    )


def test_de_reverb_removes_the_tail_in_every_block_size(blocked):
    """The tail has to lose at least 1 dB in the blocks a host uses.

    A module that only works on a one block render does not work in a
    plug-in, where the host hands it 64 samples at a time.
    """
    for block, row in sorted(blocked.items()):
        assert row["tail_removed_db"] <= -TARGET_TAIL_REMOVAL_BLOCKED_DB, (
            f"at {block} samples the tail only lost "
            f"{row['tail_removed_db']:.2f} dB"
        )


# --------------------------------------------------------------------------
# The wanted sound
# --------------------------------------------------------------------------
def test_de_reverb_keeps_the_wanted_starter(whole_block):
    """The wanted 440 Hz starter must not move by more than 0.01 dB.

    The level is read from the band the starter lives in, over the window of
    the starter itself.  A restoration module that takes the wanted sound
    with it is worse than no module at all.
    """
    drift = whole_block["wanted_removed_db"]
    assert abs(drift) <= TARGET_WANTED_DRIFT_DB, (
        f"the wanted starter moved {drift:+.4f} dB, the gate allows "
        f"{TARGET_WANTED_DRIFT_DB:.2f} dB"
    )


def test_de_reverb_keeps_the_amplitude_of_the_wanted_starter(whole_block):
    """The direct sound itself must keep its amplitude.

    The measurement is a least squares fit of the 440 Hz sinusoid over the
    window of the starter: it reads the direct sound and nothing else, so it
    is independent of the band measurement above.
    """
    gain = whole_block["wanted_gain_db"]
    assert abs(gain) <= TARGET_WANTED_DRIFT_DB, (
        f"the amplitude of the wanted starter moved {gain:+.4f} dB"
    )


def test_de_reverb_keeps_the_wanted_starter_in_every_block_size(blocked):
    """The wanted starter must not move by more than 1 dB in a blocked render.

    This is the regression guard for the defect the module had: the two
    envelope trackers restarted at every block boundary, so a host that
    renders in small blocks could not tell the direct sound from the tail
    and turned the whole signal down (measured: -6.02 dB at 64 samples).
    """
    for block, row in sorted(blocked.items()):
        drift = row["wanted_removed_db"]
        assert drift >= -TARGET_WANTED_DRIFT_BLOCKED_DB, (
            f"at {block} samples the wanted starter lost {abs(drift):.2f} dB, "
            f"the gate allows {TARGET_WANTED_DRIFT_BLOCKED_DB:.1f} dB"
        )


# --------------------------------------------------------------------------
# The reverberation time
# --------------------------------------------------------------------------
def test_de_reverb_does_not_change_the_reverberation_time(whole_block):
    """The reverberation time of the tail must not move.

    The module attenuates the sustained field; it does not shorten the
    decay.  The number is pinned down here so that a change of the
    behaviour is visible.
    """
    change = whole_block["rt60_change_s"]
    assert change is not None, "the reverberation time could not be measured"
    assert abs(change) <= TARGET_RT60_DRIFT_S, (
        f"the reverberation time moved {change:+.5f} s "
        f"({whole_block['rt60_in_s']:.5f} s -> {whole_block['rt60_out_s']:.5f} s)"
    )


# --------------------------------------------------------------------------
# Latency and block coherence
# --------------------------------------------------------------------------
def test_de_reverb_reports_its_latency():
    """The module reports the latency it adds, and the signal shows it.

    The analysis is centred, so the module has to look ahead by the length
    of its analysis window; the host compensates the delay.
    """
    from aurcore import registry

    module = registry.create(MODULE_CODE, HARNESS.SAMPLE_RATE, HARNESS.CHANNELS)
    assert module.latency_samples == EXPECTED_LATENCY_SAMPLES

    clip, _sample_rate = HARNESS.corpus_measure.load_clip(HARNESS.CLIP_ID)
    out = np.asarray(module.process(clip), dtype=np.float64)
    measured = HARNESS.measure_latency(clip[:, 0], out[:, 0])
    assert measured == EXPECTED_LATENCY_SAMPLES, (
        f"the output is delayed by {measured} samples, the module reports "
        f"{EXPECTED_LATENCY_SAMPLES}"
    )


def test_de_reverb_is_block_coherent(coherence):
    """A blocked render must not differ much from a one block render.

    The difference is measured as the RMS of the difference, relative to the
    level of the one block render.
    """
    for row in coherence:
        assert row["rms_diff_db"] <= TARGET_BLOCK_COHERENCE_DB, (
            f"at {row['block']} samples the blocked render differs by "
            f"{row['rms_diff_db']:.2f} dB, the gate asks for "
            f"{TARGET_BLOCK_COHERENCE_DB:.1f} dB"
        )


def test_de_reverb_is_deterministic():
    """Two fresh modules must return the same samples."""
    from aurcore import registry

    clip, _sample_rate = HARNESS.corpus_measure.load_clip(HARNESS.CLIP_ID)
    first = np.asarray(
        registry.create(MODULE_CODE, HARNESS.SAMPLE_RATE, HARNESS.CHANNELS).process(clip),
        dtype=np.float64,
    )
    second = np.asarray(
        registry.create(MODULE_CODE, HARNESS.SAMPLE_RATE, HARNESS.CHANNELS).process(clip),
        dtype=np.float64,
    )
    assert np.array_equal(first, second)


# --------------------------------------------------------------------------
# The edges
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case", ["silence", "dc", "square", "impulse", "tone"]
)
def test_de_reverb_survives_the_awkward_inputs(edges, case):
    """No NaN, no infinity and no change of length.

    The module is driven with the awkward inputs in one block and in the
    64 sample blocks a plug-in host uses.
    """
    rows = [row for row in edges if row["case"] == case]
    assert rows, f"the case {case!r} was not measured"
    for row in rows:
        assert row["non_finite"] == 0, (
            f"{case} (block {row['block']}) returned {row['non_finite']} "
            f"non finite samples"
        )
        assert row["length_out"] == row["length_in"], (
            f"{case} (block {row['block']}) returned {row['length_out']} "
            f"samples instead of {row['length_in']}"
        )


def test_de_reverb_keeps_the_impulse_at_full_scale(edges):
    """A single impulse must not be turned down.

    The impulse is the shortest possible wanted sound; a module that cannot
    protect it cannot protect anything.
    """
    rows = [row for row in edges if row["case"] == "impulse"]
    for row in rows:
        assert row["peak"] > 0.9, (
            f"the impulse (block {row['block']}) came back at "
            f"{row['peak']:.4f} instead of 1.0"
        )


# --------------------------------------------------------------------------
# The realtime budget
# --------------------------------------------------------------------------
def test_de_reverb_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget.

    The budget is 1333 us at 48 kHz.  The reference machine measures
    646 us, so the guard allows three budgets: the point of the test is to
    catch a module that becomes an order of magnitude slower, not to
    measure the machine.
    """
    rows = HARNESS.measure_timing(block_sizes=(64,), runs=3)
    measured = rows[0]["median_us"]
    assert measured <= CEILING_US_64, (
        f"a 64 sample block costs {measured:.0f} us, the ceiling is "
        f"{CEILING_US_64:.0f} us"
    )
