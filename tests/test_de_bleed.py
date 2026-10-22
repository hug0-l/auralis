"""
tests/test_de_bleed.py — the gate of the De-Bleed module.

What the gate asks
------------------
* the **bleed** must be taken out of the target: a bleeding source that the
  reference carries has to lose at least 20 dB, and it has to be taken out
  *of the target and not of the programme*,
* the **programme** must survive: the wanted part of the target may not move
  by more than 1 dB,
* the module must work **without a learned profile**: the estimate is built
  while the audio runs, so a user who does not capture an excerpt still gets
  the bleed out,
* a reference that carries **nothing of the target** must leave the target
  alone: the module may not touch a target it cannot explain,
* a bleed that is **below the programme** must be left where it is: the
  module may not take the programme with it,
* the output must be **finite** and as long as the input.

Every number the gate uses is a measurement of the module, not a constant of
the implementation: the tests fail when the module stops doing its work.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "corpus"
if str(CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(CORPUS_DIR))

import measure as corpus_measure  # noqa: E402  (the path has to be set up first)

from aurcore import registry  # noqa: E402

SR = 48_000

#: The bleeding source has to lose at least this much of its level.
TARGET_BLEED_REMOVAL_DB = 20.0

#: The wanted part of the target may not move by more than this.
KEEP_BUDGET_DB = 1.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make():
    """Instantiate the module, or skip the test."""
    if not registry.has("AUR_DEBLEED"):
        pytest.skip("the module is not registered yet")
    return registry.create("AUR_DEBLEED", SR, 2)


def _tone(freq: float, n: int, amp: float = 0.2) -> np.ndarray:
    """A sine of ``freq`` Hz lasting ``n`` samples."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    return amp * np.sin(2.0 * math.pi * freq * t)


def _band_db(x: np.ndarray, lo: float, hi: float) -> float:
    """The level of a band, in dBFS (the corpus' own measure)."""
    return float(corpus_measure.band_energy_db(x, SR, lo, hi))


def _projection_db(out: np.ndarray, component: np.ndarray) -> float:
    """The level of ``component`` that is left in ``out``, in dB.

    The level is read as the least squares gain of the component in the
    output, so it says how much of the component is still there whatever the
    module did to the rest of the signal.
    """
    a = np.asarray(out, dtype=np.float64)
    c = np.asarray(component, dtype=np.float64)
    if a.ndim > 1:
        a = a[:, 0]
    if c.ndim > 1:
        c = c[:, 0]
    n = min(a.size, c.size)
    a = a[:n]
    c = c[:n]
    denom = float(np.dot(c, c))
    if denom <= 0.0:
        return 0.0
    gain = float(np.dot(a, c)) / denom
    return 20.0 * math.log10(max(abs(gain), 1e-12))


def _bleed_pair(n: int = SR, gain: float = 1.0):
    """A target that carries a bleed, and the reference that carries it.

    The bleed is a 500 Hz tone, the programme a 1 kHz tone.  The two live in
    different bands, so the bleed and the programme can be told apart.
    """
    bleed = _tone(500.0, n, amp=0.2)
    programme = _tone(1000.0, n, amp=0.2)
    target = programme + gain * bleed
    stereo = np.zeros((n, 2), dtype=np.float64)
    stereo[:, 0] = target
    stereo[:, 1] = bleed
    return stereo, target, bleed, programme


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_the_bleed_is_taken_out_of_the_target():
    """The bleeding source must lose at least 20 dB in the target.

    The measurement is the level of the bleed inside the target, read as
    the least squares gain of the bleed in the output.
    """
    module = _make()
    stereo, target, bleed, _programme = _bleed_pair()
    module.learn(stereo)
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    before = _projection_db(target[:, None], bleed)
    after = _projection_db(out[:, :1], bleed)
    removal = before - after
    assert removal >= TARGET_BLEED_REMOVAL_DB, (
        f"the bleed was only taken away by {removal:.1f} dB, the gate asks for "
        f"{TARGET_BLEED_REMOVAL_DB:.1f} dB"
    )


def test_the_programme_survives_the_bleed_removal():
    """The wanted part of the target may not move by more than 1 dB."""
    module = _make()
    stereo, target, _bleed, programme = _bleed_pair()
    module.learn(stereo)
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    before = _projection_db(target[:, None], programme)
    after = _projection_db(out[:, :1], programme)
    loss = before - after
    assert loss <= KEEP_BUDGET_DB, (
        f"the programme lost {loss:.2f} dB, the budget is {KEEP_BUDGET_DB:.1f} dB"
    )


def test_the_module_works_without_a_learned_profile():
    """Without a captured excerpt the module still has to take the bleed out.

    The estimate is built while the audio runs, so the module must not need
    a learning step to do its work.
    """
    module = _make()
    stereo, target, bleed, programme = _bleed_pair()
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    removal = _projection_db(target[:, None], bleed) - _projection_db(out[:, :1], bleed)
    assert removal >= TARGET_BLEED_REMOVAL_DB, (
        f"without a learned profile the bleed was only taken away by "
        f"{removal:.1f} dB, the gate asks for {TARGET_BLEED_REMOVAL_DB:.1f} dB"
    )
    loss = _projection_db(target[:, None], programme) - _projection_db(out[:, :1], programme)
    assert loss <= KEEP_BUDGET_DB, (
        f"without a learned profile the programme lost {loss:.2f} dB, the budget "
        f"is {KEEP_BUDGET_DB:.1f} dB"
    )


def test_a_reference_that_explains_nothing_leaves_the_target_alone():
    """A reference that carries nothing of the target must not change it.

    The reference is noise, the target is a 1 kHz tone.  There is nothing to
    take away, so the module may not touch the target.
    """
    module = _make()
    rng = np.random.default_rng(20261)
    n = SR
    programme = _tone(1000.0, n)
    noise = rng.standard_normal(n) * 0.05
    stereo = np.zeros((n, 2), dtype=np.float64)
    stereo[:, 0] = programme
    stereo[:, 1] = noise
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    change = _band_db(stereo[:, :1], 950.0, 1050.0) - _band_db(out[:, :1], 950.0, 1050.0)
    assert change <= KEEP_BUDGET_DB, (
        f"a reference that explains nothing moved the programme by {change:.2f} dB, "
        f"the budget is {KEEP_BUDGET_DB:.1f} dB"
    )


def test_a_bleed_below_the_programme_is_left_where_it_is():
    """The module may not take the programme with a quiet bleed.

    The bleed arrives 26 dB below the programme, so the reference explains
    only a small part of the target.  The programme has to stay.
    """
    module = _make()
    stereo, target, _bleed, programme = _bleed_pair(gain=0.05)
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    loss = _projection_db(target[:, None], programme) - _projection_db(out[:, :1], programme)
    assert loss <= KEEP_BUDGET_DB, (
        f"a quiet bleed took {loss:.2f} dB of the programme with it, the budget "
        f"is {KEEP_BUDGET_DB:.1f} dB"
    )


def test_the_output_is_finite_and_as_long_as_the_input():
    """The module may not produce a NaN and may not lose a sample."""
    module = _make()
    stereo, _target, _bleed, _programme = _bleed_pair(n=4096)
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)
    assert out.shape[0] == stereo.shape[0]
    assert bool(np.all(np.isfinite(out)))


def test_the_module_removes_the_bleed_of_the_corpus():
    """The gate on the corpus: the hum clip bleeds into the click clip.

    The bleeding source is the hum clip of the corpus, the programme is the
    click clip, and the target carries both.  The bleed has to go, the
    programme has to stay.
    """
    module = _make()
    bleed_samples, _sr = corpus_measure.load_clip("hum50")
    programme_samples, _sr = corpus_measure.load_clip("clicks")
    bleed = np.asarray(bleed_samples, dtype=np.float64).mean(axis=1)
    programme = np.asarray(programme_samples, dtype=np.float64).mean(axis=1) * 8.0
    n = min(bleed.size, programme.size)
    bleed = bleed[:n]
    programme = programme[:n]
    target = programme + bleed
    stereo = np.zeros((n, 2), dtype=np.float64)
    stereo[:, 0] = target
    stereo[:, 1] = bleed

    learn = np.zeros((n, 2), dtype=np.float64)
    learn[:, 0] = bleed
    learn[:, 1] = bleed
    module.learn(learn)
    out = np.asarray(module.process(stereo.copy()), dtype=np.float64)

    removal = _projection_db(target[:, None], bleed) - _projection_db(out[:, :1], bleed)
    assert removal >= TARGET_BLEED_REMOVAL_DB, (
        f"the corpus bleed was only taken away by {removal:.1f} dB, the gate asks "
        f"for {TARGET_BLEED_REMOVAL_DB:.1f} dB"
    )
    loss = _projection_db(target[:, None], programme) - _projection_db(out[:, :1], programme)
    assert loss <= KEEP_BUDGET_DB, (
        f"the corpus programme lost {loss:.2f} dB, the budget is "
        f"{KEEP_BUDGET_DB:.1f} dB"
    )
