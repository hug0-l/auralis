"""
tests/test_de_hum.py — the gate of the De-Hum module.

What the gate asks
------------------
* the **hum** must be taken away: the 50 Hz hum of the corpus clip has to
  lose at least 20 dB, and its 100 Hz harmonic with it,
* the **programme** must survive: the 1 kHz tone of the corpus clip may not
  move by more than 0.5 dB,
* a band that carries **no hum** must not be touched: a wanted partial that
  sits on a harmonic of the hum frequency is programme, not hum, and the
  module may not notch it away,
* the **dynamic** mode must still take the hum away and leave the programme
  alone,
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

#: The hum has to lose at least this much of its level.
TARGET_HUM_REMOVAL_DB = 20.0

#: The wanted part of the signal may not move by more than this.
KEEP_BUDGET_DB = 0.5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make(mode: str = "Static"):
    """Instantiate the module, or skip the test."""
    if not registry.has("AUR_DEHUM"):
        pytest.skip("the module is not registered yet")
    module = registry.create("AUR_DEHUM", SR, 1)
    if mode != "Static":
        module.params.set("Mode", mode)
    return module


def _tone(freq: float, n: int, amp: float = 0.2) -> np.ndarray:
    """A sine of ``freq`` Hz lasting ``n`` samples."""
    t = np.arange(n, dtype=np.float64) / float(SR)
    return amp * np.sin(2.0 * math.pi * freq * t)


def _band_db(x: np.ndarray, lo: float, hi: float) -> float:
    """The level of a band, in dBFS (the corpus' own measure)."""
    return float(corpus_measure.band_energy_db(x, SR, lo, hi))


def _corpus_hum():
    """The hum clip of the corpus: a 1 kHz tone over 50 Hz mains hum."""
    samples, _sr = corpus_measure.load_clip("hum50")
    return np.asarray(samples, dtype=np.float64).mean(axis=1)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_the_corpus_hum_is_taken_away():
    """The 50 Hz hum and its 100 Hz harmonic have to lose at least 20 dB.

    The measurement is the level of the band, before and after, on the hum
    clip of the corpus.
    """
    module = _make()
    x = _corpus_hum()
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)

    removal = _band_db(x, 42.0, 108.0) - _band_db(out, 42.0, 108.0)
    assert removal >= TARGET_HUM_REMOVAL_DB, (
        f"the hum was only taken away by {removal:.1f} dB, the gate asks for "
        f"{TARGET_HUM_REMOVAL_DB:.1f} dB"
    )


def test_the_programme_survives_the_hum_removal():
    """The 1 kHz programme of the corpus clip may not move by more than 0.5 dB."""
    module = _make()
    x = _corpus_hum()
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)

    change = _band_db(x, 950.0, 1050.0) - _band_db(out, 950.0, 1050.0)
    assert change <= KEEP_BUDGET_DB, (
        f"the programme moved by {change:.2f} dB, the budget is "
        f"{KEEP_BUDGET_DB:.1f} dB"
    )


def test_a_wanted_partial_on_a_harmonic_is_not_taken_away():
    """A partial that sits on a harmonic of the hum frequency is programme.

    The signal is a 200 Hz tone — the fourth harmonic of the 50 Hz mains
    frequency — and no hum at all.  There is no hum to remove, so the
    module may not notch the tone away.
    """
    module = _make()
    x = _tone(200.0, SR)
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)

    change = _band_db(x, 195.0, 205.0) - _band_db(out, 195.0, 205.0)
    assert change <= KEEP_BUDGET_DB, (
        f"a wanted partial on a harmonic lost {change:.2f} dB, the budget is "
        f"{KEEP_BUDGET_DB:.1f} dB"
    )


def test_a_signal_without_a_hum_is_left_alone():
    """A 1 kHz tone carries no hum: the module may not change it.

    The measurement is the level of the tone, and the level of the band the
    hum would live in: the module may not add anything there either.
    """
    module = _make()
    x = _tone(1000.0, SR)
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)

    change = _band_db(x, 950.0, 1050.0) - _band_db(out, 950.0, 1050.0)
    assert change <= KEEP_BUDGET_DB, (
        f"a signal without a hum moved by {change:.2f} dB, the budget is "
        f"{KEEP_BUDGET_DB:.1f} dB"
    )
    added = _band_db(out, 40.0, 90.0) - _band_db(x, 40.0, 90.0)
    assert added <= KEEP_BUDGET_DB, (
        f"the module put {added:.2f} dB into the hum band, the budget is "
        f"{KEEP_BUDGET_DB:.1f} dB"
    )


def test_the_dynamic_mode_takes_the_hum_away():
    """The dynamic mode has to remove the hum and keep the programme."""
    module = _make(mode="Dynamic")
    x = _corpus_hum()
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)

    removal = _band_db(x, 42.0, 108.0) - _band_db(out, 42.0, 108.0)
    assert removal >= 10.0, (
        f"the dynamic mode only took the hum away by {removal:.1f} dB, the gate "
        f"asks for 10.0 dB"
    )
    change = _band_db(x, 950.0, 1050.0) - _band_db(out, 950.0, 1050.0)
    assert change <= KEEP_BUDGET_DB, (
        f"the dynamic mode moved the programme by {change:.2f} dB, the budget "
        f"is {KEEP_BUDGET_DB:.1f} dB"
    )


def test_the_output_is_finite_and_as_long_as_the_input():
    """The module may not produce a NaN and may not lose a sample."""
    module = _make()
    x = _corpus_hum()[:4096]
    out = np.asarray(module.process(x[:, None].copy()), dtype=np.float64)
    assert out.shape[0] == x.shape[0]
    assert bool(np.all(np.isfinite(out)))


# ---------------------------------------------------------------------------
# the linear phase path
# ---------------------------------------------------------------------------
def _linear_module(channels: int = 1):
    """A de-hum module with the linear phase path on."""
    module = registry.create("AUR_DEHUM", SR, channels)
    module.params.set("Linear Phase Filters", True)
    module.params.set("Base Frequency", 50.0)
    module.params.set("Adaptive", False)
    return module


def _amplitude(x: np.ndarray, freq: float) -> float:
    """The amplitude of a tone, read without any leakage.

    The segment that is measured is an integer number of periods of the
    tone, so the bin of the tone is exact.
    """
    mono = x.mean(axis=1) if x.ndim == 2 else x
    period = int(round(SR / freq))
    n = (len(mono) // period) * period
    spec = np.fft.rfft(mono[-n:])
    k = int(round(freq * n / SR))
    return 2.0 * float(abs(spec[k])) / n


def test_the_linear_phase_path_takes_the_hum_away():
    """The notch of the linear phase path is deeper than 59 dB at 50 Hz.

    The kernel is designed on a grid that puts the hum exactly on a
    sample of the grid, so the null of the notch sits on the hum itself.
    Measured: 61.8 dB.
    """
    module = _linear_module()
    n = SR
    t = np.arange(n, dtype=np.float64) / float(SR)
    x = (0.2 * np.sin(2.0 * math.pi * 1000.0 * t)
         + 0.2 * np.sin(2.0 * math.pi * 50.0 * t))[:, None]
    out = np.asarray(module.process(x.copy()))
    # the steady state starts after the delay and after the kernel has
    # rung out
    start = module.latency_samples + 4801
    before = _amplitude(x[start:], 50.0)
    after = _amplitude(out[start:], 50.0)
    removed = 20.0 * math.log10(max(after, 1e-12) / max(before, 1e-12))
    assert removed <= -59.0, (
        f"the hum was only taken away by {removed:.1f} dB, the gate asks for "
        f"59 dB"
    )


def test_the_linear_phase_path_keeps_the_programme():
    """The 1 kHz programme of the linear phase path may not move by more than 1 dB.

    Measured: 0.007 dB.
    """
    module = _linear_module()
    n = SR
    t = np.arange(n, dtype=np.float64) / float(SR)
    x = (0.2 * np.sin(2.0 * math.pi * 1000.0 * t)
         + 0.2 * np.sin(2.0 * math.pi * 50.0 * t))[:, None]
    out = np.asarray(module.process(x.copy()))
    start = module.latency_samples + 4801
    before = _amplitude(x[start:], 1000.0)
    after = _amplitude(out[start:], 1000.0)
    moved = 20.0 * math.log10(max(after, 1e-12) / max(before, 1e-12))
    assert abs(moved) <= 1.0, f"the programme moved by {moved:.3f} dB"


def test_the_linear_phase_path_is_silent_before_its_delay():
    """The linear phase path answers nothing before the delay it declares."""
    module = _linear_module()
    impulse = np.zeros((12_000, 1))
    impulse[0, 0] = 1.0
    out = np.asarray(module.process(impulse))[:, 0]
    assert module.latency_samples == 6496
    assert float(np.max(np.abs(out[:module.latency_samples]))) == 0.0


def test_the_linear_phase_path_is_the_same_in_blocks_of_any_size():
    """The linear phase path gives the same answer in blocks of any size.

    The decision of the bank runs on a fixed grid of samples, so the
    answer does not depend on the size of the blocks the host uses.
    """
    n = 16_384
    rng = np.random.default_rng(99)
    x = rng.standard_normal((n, 1)) * 0.1
    one = np.asarray(_linear_module().process(x.copy()))
    for block in (64, 128, 256, 512):
        module = _linear_module()
        pieces = [np.asarray(module.process(x[i:i + block].copy()))
                  for i in range(0, n, block)]
        many = np.concatenate(pieces, axis=0)
        diff = float(np.max(np.abs(one - many)))
        peak = float(np.max(np.abs(one)))
        assert diff / peak < 1e-4, (
            f"the blocks of {block} samples differ by "
            f"{20.0 * math.log10(max(diff / peak, 1e-300)):.1f} dB"
        )


def test_the_linear_phase_path_agrees_with_the_long_kernel_path():
    """The two paths apply the same kernel, so they answer the same thing.

    The fast path is a weighted overlap-add bank and the long path is
    the direct convolution of the kernel; the fast path answers a window
    later, because the frames of the bank overlap.  Measured: 3.3e-16
    absolute.
    """
    from aurcore.dsp.filters import FIR
    from aurcore.dsp.wola import WolaBank

    module = _linear_module()
    n = 20_000
    rng = np.random.default_rng(4242)
    x = rng.standard_normal((n, 1)) * 0.1
    fast = np.asarray(module.process(x.copy()))[:, 0]
    module._remember(x)
    module._openness(50.0, n)
    kernel = module._fir_kernel(50.0)
    long = np.asarray(FIR(kernel, SR).process(x.copy()))[:, 0]
    shift = 4096
    diff = float(np.max(np.abs(long[:n - shift] - fast[shift:])))
    assert diff < 1e-13, f"the two paths differ by {diff:.3e}"
