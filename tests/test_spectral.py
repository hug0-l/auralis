"""
tests/test_spectral.py — the spectral primitives of the DSP layer.

The module under test is ``aurcore/dsp/spectral.py``.  Four of its
primitives carry a contract that the spectral modules depend on:

``resample_gain_2d``
    Interpolates a gain curve from one time/frequency grid onto another.
    The result has to be the same as the obvious loop over ``numpy.interp``
    calls, because that loop is what the module used to do; the primitive
    only changes *how* the result is computed.

``moving_average``
    A centred box filter with replicated edges.  Same contract: the result
    has to be the convolution it replaces.

``noise_power_from_db``
    Converts a noise floor in dB into a linear **power**.  The gain
    estimators compare powers, and a floor in dB is a magnitude: handing
    the magnitude to the estimator collapses the speech presence
    probability and saturates the reduction.

``om_lsa_gain_curve``
    The OM-LSA branch in one call, with the floor taken in dB so that the
    unit error cannot be written in the first place.

The tolerances are measured, not wished for: the two replacements agree
with the loops they replace to 0 dB (bit identical) and 1.4e-11 dB
respectively.
"""
from __future__ import annotations

import numpy as np
import pytest

from aurcore.dsp import spectral as spec

SR = 48000

#: The two replacements agree with the loops they replace to this many dB.
#: The resample is bit identical, the moving average differs by the
#: rounding of a different summation order (1.4e-11 dB on a 77 dB signal).
TOLERANCE_DB = 1e-9


# ---------------------------------------------------------------------------
# reference implementations (the loops the primitives replaced)
# ---------------------------------------------------------------------------
def reference_resample(gain_db, src_times, src_freqs, dst_times, dst_freqs):
    """Resample a gain curve with one ``numpy.interp`` call per bin and frame."""
    in_time = np.empty((dst_times.size, gain_db.shape[1]), dtype=np.float64)
    for b in range(gain_db.shape[1]):
        in_time[:, b] = np.interp(dst_times, src_times, gain_db[:, b])
    out = np.empty((dst_times.size, dst_freqs.size), dtype=np.float64)
    for i in range(dst_times.size):
        out[i] = np.interp(dst_freqs, src_freqs, in_time[i])
    return out


def reference_moving_average(x, width, axis=-1):
    """Centred moving average with edge replication, one convolution per line."""
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if width % 2 == 0:
        width += 1
    pad = width // 2
    moved = np.moveaxis(arr, axis, -1)
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(pad, pad)], mode="edge")
    kernel = np.ones(width, dtype=np.float64) / float(width)
    out = np.apply_along_axis(
        lambda v: np.convolve(v, kernel, mode="valid"), -1, padded
    )
    return np.moveaxis(out, -1, axis)


def _grid(n, lo, hi):
    """An increasing grid of ``n`` points between ``lo`` and ``hi``."""
    return np.linspace(lo, hi, n)


# ---------------------------------------------------------------------------
# resample_gain_2d
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "src_t, src_f, dst_t, dst_f",
    [
        (3, 721, 4, 361),   # the shape the module uses for a 512 sample block
        (7, 181, 4, 361),   # the shape the module uses for an extra resolution
        (2, 721, 2, 361),   # the shape the module uses for a 64 sample block
        (5, 11, 7, 13),     # a small irregular grid
        (1, 9, 3, 5),       # a single source frame
        (4, 1, 3, 6),       # a single source bin
    ],
)
def test_resample_matches_the_interpolation_loop(src_t, src_f, dst_t, dst_f):
    """The fast resample is the same curve as the loop over ``numpy.interp``."""
    rng = np.random.default_rng(20_260_121)
    gain = rng.standard_normal((src_t, src_f)) * 6.0
    src_times = np.sort(rng.random(src_t)) * 0.02
    src_freqs = np.sort(rng.random(src_f)) * 24_000.0
    dst_times = rng.random(dst_t) * 0.03
    dst_freqs = rng.random(dst_f) * 26_000.0
    expected = reference_resample(gain, src_times, src_freqs, dst_times, dst_freqs)
    got = spec.resample_gain_2d(gain, src_times, src_freqs, dst_times, dst_freqs)
    assert got.shape == (dst_t, dst_f)
    assert np.max(np.abs(got - expected)) <= TOLERANCE_DB, (
        f"max|delta| = {np.max(np.abs(got - expected)):.3e} dB"
    )


def test_resample_clamps_outside_the_source_grid():
    """Outside the source grid the curve is clamped, never extrapolated."""
    gain = np.array([[0.0], [10.0], [20.0]])
    src_times = np.array([0.0, 1.0, 2.0])
    src_freqs = np.array([0.0])
    dst_times = np.array([-5.0, 0.5, 5.0])
    got = spec.resample_gain_2d(gain, src_times, src_freqs, dst_times, src_freqs)
    assert got[0, 0] == pytest.approx(0.0)      # before the first frame
    assert got[1, 0] == pytest.approx(5.0)      # half way
    assert got[2, 0] == pytest.approx(20.0)     # after the last frame


def test_resample_of_an_empty_target_is_empty():
    """A target without frames or bins gives an empty result, not an error."""
    gain = np.zeros((4, 8))
    src_t, src_f = _grid(4, 0.0, 1.0), _grid(8, 0.0, 1000.0)
    out = spec.resample_gain_2d(gain, src_t, src_f, np.zeros(0), src_f)
    assert out.shape == (0, 8)
    out = spec.resample_gain_2d(gain, src_t, src_f, src_t, np.zeros(0))
    assert out.shape == (4, 0)


def test_resample_rejects_a_one_dimensional_curve():
    """A curve has to be two dimensional."""
    with pytest.raises(ValueError):
        spec.resample_gain_2d(np.zeros(8), np.arange(1.0), np.arange(8.0),
                               np.arange(1.0), np.arange(8.0))


# ---------------------------------------------------------------------------
# moving_average
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "shape, width, axis",
    [
        ((3, 721), 3, 0),      # the shape the module uses for a 512 sample block
        ((7, 181), 3, 0),      # the shape the module uses for an extra resolution
        ((16, 16), 4, -1),
        ((16, 16), 4, 0),
        ((5,), 3, -1),
        ((1, 9), 3, 0),
        ((10, 10), 2, 1),
    ],
)
def test_moving_average_matches_the_convolution(shape, width, axis):
    """The cumulative sum gives the same average as the box convolution."""
    rng = np.random.default_rng(20_260_122)
    x = rng.standard_normal(shape) * 40.0
    expected = reference_moving_average(x, width, axis)
    got = spec.moving_average(x, width, axis)
    assert got.shape == x.shape
    assert np.max(np.abs(got - expected)) <= TOLERANCE_DB, (
        f"max|delta| = {np.max(np.abs(got - expected)):.3e}"
    )


def test_moving_average_replicates_the_edges():
    """The first and the last sample are replicated, not zero padded."""
    x = np.array([[1.0], [2.0], [3.0], [4.0]])
    got = spec.moving_average(x, 3, axis=0)
    assert got[0, 0] == pytest.approx((1.0 + 1.0 + 2.0) / 3.0)
    assert got[1, 0] == pytest.approx((1.0 + 2.0 + 3.0) / 3.0)
    assert got[3, 0] == pytest.approx((3.0 + 4.0 + 4.0) / 3.0)


def test_moving_average_of_a_constant_is_the_constant():
    """A flat curve must not move."""
    x = np.full((11, 5), -42.0)
    got = spec.moving_average(x, 3, axis=0)
    assert np.allclose(got, -42.0)


def test_moving_average_width_one_is_the_identity():
    """A window of one sample returns the input."""
    x = np.arange(12, dtype=np.float64).reshape(3, 4)
    assert np.array_equal(spec.moving_average(x, 1, axis=0), x)


def test_moving_average_rounds_an_even_width_up():
    """An even width becomes the next odd one, so the window stays centred."""
    x = np.arange(9, dtype=np.float64).reshape(3, 3)
    assert np.allclose(
        spec.moving_average(x, 2, axis=0),
        spec.moving_average(x, 3, axis=0),
    )


# ---------------------------------------------------------------------------
# the noise power
# ---------------------------------------------------------------------------
def test_noise_power_is_the_square_of_the_magnitude():
    """A dB floor describes a magnitude; the power is its square."""
    floor_db = np.array([-60.0, -40.0, -13.7, -80.0])
    power = spec.noise_power_from_db(floor_db)
    magnitude = np.power(10.0, floor_db / 20.0)
    assert np.allclose(power, magnitude ** 2, rtol=0, atol=0)
    # the power of the floor, read with the power convention (10*log10), has
    # the same dB value as the magnitude read with the amplitude convention
    assert np.allclose(10.0 * np.log10(power), floor_db, atol=1e-9)


def test_noise_power_keeps_the_shape():
    """The conversion is element wise."""
    floor_db = np.full((3, 5), -50.0)
    assert spec.noise_power_from_db(floor_db).shape == (3, 5)
    assert spec.noise_power_from_db(-50.0) == pytest.approx(1e-5)


# ---------------------------------------------------------------------------
# the OM-LSA branch
# ---------------------------------------------------------------------------
def test_om_lsa_branch_is_the_longhand_computation():
    """The combined branch is the decision directed SNR plus the gain."""
    rng = np.random.default_rng(20_260_123)
    mag = np.abs(rng.standard_normal((4, 6))) * 1e-3
    prior = mag * 0.8
    floor_db = np.full((4, 6), -55.0)
    expected_power = np.maximum(spec.noise_power_from_db(floor_db), spec._EPS)
    gamma = (mag ** 2) / expected_power
    xi = spec.decision_directed_snr(prior, mag, 0.98, expected_power)
    expected = spec.om_lsa_gain(gamma, xi, 0.0, 1.0)
    got = spec.om_lsa_gain_curve(prior, mag, floor_db, 0.98, 0.0, 1.0)
    assert np.max(np.abs(got - expected)) <= 1e-12


def test_om_lsa_branch_does_not_saturate_a_noise_bin():
    """A bin at the noise level must not be attenuated by the whole budget.

    With the magnitude of the floor in the estimator, the a-priori SNR
    collapses, the speech presence probability goes to zero and the branch
    asks for the full reduction everywhere.  With a power it refines the
    reduction instead.
    """
    floor_db = -60.0
    magnitude = np.power(10.0, floor_db / 20.0)
    at_the_floor = np.array([[magnitude]])

    # the defect: the magnitude of the floor where a power is expected
    gamma_bad = (at_the_floor ** 2) / magnitude
    xi_bad = spec.decision_directed_snr(at_the_floor, at_the_floor, 0.98, magnitude)
    nu_bad = xi_bad / (1.0 + xi_bad)
    assert nu_bad[0, 0] < 0.01, "the speech presence probability must collapse"

    # the fix: the power of the floor
    gain = spec.om_lsa_gain_curve(at_the_floor, at_the_floor,
                                   np.full((1, 1), floor_db), 0.98, 0.0, 1.0)
    attenuation_db = -20.0 * np.log10(max(float(gain[0, 0]), 1e-6))
    assert 0.0 < attenuation_db < 20.0, (
        f"a bin at the noise level must be refined, not saturated "
        f"({attenuation_db:.1f} dB)"
    )


def test_om_lsa_branch_leaves_a_wanted_bin_alone():
    """A bin far above the floor must keep its level."""
    floor_db = -60.0
    wanted = np.array([[np.power(10.0, -20.0 / 20.0)]])   # 40 dB above the floor
    gain = spec.om_lsa_gain_curve(wanted, wanted,
                                   np.full((1, 1), floor_db), 0.98, 0.0, 1.0)
    assert gain[0, 0] > 0.9, f"gain = {gain[0, 0]:.3f}"


def test_om_lsa_branch_is_bounded():
    """The gain stays inside the limits it was given."""
    rng = np.random.default_rng(20_260_124)
    mag = np.abs(rng.standard_normal((3, 8))) * 1e-2
    gain = spec.om_lsa_gain_curve(mag, mag, np.full((3, 8), -45.0), 0.98, -3.0, 1.0)
    assert np.all(gain >= -3.0 - 1e-12) and np.all(gain <= 1.0 + 1e-12)
