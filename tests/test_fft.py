"""
Numeric tests for the real FFT wrapper (aurcore/dsp/fft.py).

Every size the wrapper supports is checked, not just the one the rest of the
suite happens to use: the transform is the foundation of every spectral
measurement in the project, and a wrapper that is exact at 1024 samples and
inexact at 1023 is a wrapper nobody can trust.

What is asserted
----------------
* the forward/inverse round trip rebuilds the signal at **every** size from
  1 to 1024 and at the large sizes, to better than -260 dBFS (measured:
  -298.3 dBFS at the worst size, n = 934);
* the same round trip works through the *default* inverse length, i.e. when
  the caller does not know the length;
* the transform agrees with a textbook DFT at the sizes where the DFT is
  still affordable;
* the hand written radix-2 backend agrees with the compiled one, for every
  power of two and for every transform axis;
* the short-time transform, the only consumer of the wrapper, round trips at
  every frame size;
* a signal that is shorter than one frame, and one whose length is not a
  multiple of the frame, come back with the right length and the right
  samples;
* the numerical edges (silence, a denormal, a huge value, an infinity, a
  NaN) do what the documentation says they do.

Tolerance conventions
---------------------
* levels are compared in dBFS, with the tolerance written next to the
  assertion;
* an absolute error of 1e-13 is -260 dBFS, so the round trip assertions use
  that as their worst case;
* the DFT comparison uses a relative tolerance of 1e-15 * n, because the
  textbook DFT itself loses accuracy as n grows.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from aurcore.dsp import fft as fftmod

#: The worst absolute error the round trip may show, as a level in dBFS.
#: 1e-13 is -260 dBFS; the measured worst case is 1.2e-15 (-298.3 dBFS).
ROUND_TRIP_DBFS = -260.0

#: The worst relative error against a textbook DFT, per size.
DFT_RELATIVE_TOLERANCE = 1e-15


def _dbfs(value: float) -> float:
    """Return ``value`` as a level in dBFS (1.0 = 0 dBFS)."""
    return 20.0 * math.log10(max(abs(float(value)), 1e-300))


def _full_scale(n: int, seed: int = 20261021) -> np.ndarray:
    """A deterministic noise signal whose peak is exactly 1.0 (0 dBFS)."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(n))
    peak = float(np.max(np.abs(x)))
    if peak > 0.0:
        x = x / peak
    return x


def _naive_rfft(x: np.ndarray) -> np.ndarray:
    """The first ``n // 2 + 1`` bins of the textbook DFT."""
    n = len(x)
    k = np.arange(n // 2 + 1)
    matrix = np.exp(-2j * math.pi * np.outer(k, np.arange(n)) / n)
    return matrix @ np.asarray(x, dtype=np.float64)


#: The sizes the wrapper has to survive: every size up to 256, the odd and
#: the awkward ones around the powers of two, and the large ones.
NOTABLE_SIZES: tuple[int, ...] = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 20, 24, 31, 32,
    33, 48, 63, 64, 65, 96, 100, 101, 120, 127, 128, 129, 192, 200, 250, 251,
    256, 257, 300, 383, 384, 400, 500, 511, 512, 513, 600, 768, 1000, 1001,
    1021, 1023, 1024, 1025,
)

#: The large sizes.
LARGE_SIZES: tuple[int, ...] = (1536, 2048, 3000, 4096, 8192, 16384, 32768, 65536)

#: The sizes a textbook DFT is compared against.
DFT_SIZES: tuple[int, ...] = (2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 100, 127, 128,
                              251, 256, 500, 512, 1000, 1024)

#: The power-of-two sizes the radix-2 backend accepts.
RADIX2_SIZES: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)


# --------------------------------------------------------------------------
# the round trip, at every size
# --------------------------------------------------------------------------
def test_round_trip_is_exact_at_every_size_up_to_1024():
    """Every size from 1 to 1024 rebuilds the signal it was given.

    This is the test that the rest of the suite was missing: a wrapper that
    is exact at one size says nothing about the other thousand.
    """
    worst = 0.0
    worst_n = 0
    for n in range(1, 1025):
        x = _full_scale(n)
        back = fftmod.irfft(fftmod.rfft(x, n=n), n=n)
        assert back.shape == x.shape, f"n={n}: the rebuild has {back.shape}, expected {x.shape}"
        error = float(np.max(np.abs(back - x)))
        if error > worst:
            worst, worst_n = error, n
    assert _dbfs(worst) < ROUND_TRIP_DBFS, (
        f"the worst round trip is {_dbfs(worst):.1f} dBFS at n={worst_n}, "
        f"it must stay below {ROUND_TRIP_DBFS:.1f} dBFS"
    )


@pytest.mark.parametrize("n", NOTABLE_SIZES)
def test_round_trip_at_a_notable_size(n: int):
    """The round trip is exact at the odd, the prime and the power-of-two sizes."""
    x = _full_scale(n)
    back = fftmod.irfft(fftmod.rfft(x, n=n), n=n)
    assert back.shape == x.shape
    error = float(np.max(np.abs(back - x)))
    assert _dbfs(error) < ROUND_TRIP_DBFS, (
        f"n={n}: the round trip error is {_dbfs(error):.1f} dBFS, "
        f"it must stay below {ROUND_TRIP_DBFS:.1f} dBFS"
    )


@pytest.mark.parametrize("n", LARGE_SIZES)
def test_round_trip_at_a_large_size(n: int):
    """The round trip is exact at the large sizes too."""
    x = _full_scale(n)
    back = fftmod.irfft(fftmod.rfft(x, n=n), n=n)
    assert back.shape == x.shape
    error = float(np.max(np.abs(back - x)))
    assert _dbfs(error) < ROUND_TRIP_DBFS, (
        f"n={n}: the round trip error is {_dbfs(error):.1f} dBFS, "
        f"it must stay below {ROUND_TRIP_DBFS:.1f} dBFS"
    )


def test_round_trip_helper_is_exact_at_every_size():
    """The paired helper is exact at every size, odd ones included."""
    worst = 0.0
    worst_n = 0
    for n in range(1, 1025):
        x = _full_scale(n)
        back = fftmod.fft_round_trip(x)
        assert back.shape == x.shape, f"n={n}: the rebuild has {back.shape}"
        error = float(np.max(np.abs(back - x)))
        if error > worst:
            worst, worst_n = error, n
    assert _dbfs(worst) < ROUND_TRIP_DBFS, (
        f"the worst paired round trip is {_dbfs(worst):.1f} dBFS at n={worst_n}"
    )


def test_round_trip_through_the_default_inverse_length():
    """The round trip also works when the caller does not know the length.

    The one-sided spectrum of an **odd** length signal does not determine
    the length, so the default of :func:`irfft` has to choose the reading
    that loses no information.  The measurement says: the top bin of an odd
    length spectrum is complex, the top bin of an even length spectrum is
    the Nyquist bin and is real, so the choice is unambiguous for every
    signal that is not degenerate.
    """
    worst = 0.0
    worst_n = 0
    for n in range(1, 1025):
        x = _full_scale(n)
        back = fftmod.irfft(fftmod.rfft(x))
        assert back.shape == x.shape, (
            f"n={n}: the default inverse length returned {back.shape}, "
            f"expected {x.shape}"
        )
        error = float(np.max(np.abs(back - x)))
        if error > worst:
            worst, worst_n = error, n
    assert _dbfs(worst) < ROUND_TRIP_DBFS, (
        f"the worst default-length round trip is {_dbfs(worst):.1f} dBFS at n={worst_n}"
    )


def test_the_default_inverse_length_of_a_single_sample():
    """A one-sample signal survives the default length."""
    x = np.array([0.75])
    back = fftmod.irfft(fftmod.rfft(x))
    assert back.shape == x.shape
    assert float(back[0]) == pytest.approx(0.75, abs=1e-15)


def test_the_default_length_of_an_even_spectrum_is_the_even_length():
    """A spectrum whose top bin is real is read as an even-length signal.

    This is the documented ambiguity: a one-sided spectrum of ``k`` bins
    describes a signal of ``2*k-2`` samples (the top bin is the Nyquist bin
    and is therefore real) or of ``2*k-1`` samples.  The top bin of a real
    even-length signal is always exactly real, so the even reading is the
    one that must win.
    """
    x = _full_scale(1024)
    spec = fftmod.rfft(x)
    assert float(np.imag(spec[-1])) == 0.0, "the Nyquist bin of an even length is real"
    back = fftmod.irfft(spec)
    assert back.shape == x.shape


def test_the_default_length_of_a_degenerate_odd_spectrum_is_pinned():
    """A degenerate odd-length signal is the documented residual case.

    A DC signal of an odd length has an exactly real top bin, so the
    spectrum cannot say whether the signal had 4 or 5 samples and the
    default picks the even reading.  The behaviour is pinned here so that it
    cannot change unnoticed, and the explicit length is exact.
    """
    x = np.ones(5)
    spec = fftmod.rfft(x)
    assert float(np.imag(spec[-1])) == 0.0
    back = fftmod.irfft(spec)
    assert back.shape == (4,)
    exact = fftmod.irfft(spec, n=5)
    assert np.allclose(exact, x, atol=1e-12)
    assert np.allclose(fftmod.fft_round_trip(x), x, atol=1e-12)


# --------------------------------------------------------------------------
# against a textbook DFT
# --------------------------------------------------------------------------
@pytest.mark.parametrize("n", DFT_SIZES)
def test_the_transform_matches_a_textbook_dft(n: int):
    """The fast transform agrees with the O(n^2) DFT."""
    x = _full_scale(n)
    want = _naive_rfft(x)
    got = np.asarray(fftmod.rfft(x, n=n))
    assert got.shape == want.shape
    scale = float(np.max(np.abs(want)))
    error = float(np.max(np.abs(got - want)))
    tolerance = DFT_RELATIVE_TOLERANCE * n * scale
    assert error <= tolerance, (
        f"n={n}: the transform differs from the DFT by {error:.3e}, "
        f"which is more than {tolerance:.3e}"
    )


@pytest.mark.parametrize("n", RADIX2_SIZES)
def test_the_radix2_backend_matches_the_textbook_dft(n: int):
    """The hand written radix-2 transform agrees with the DFT too."""
    x = _full_scale(n)
    want = _naive_rfft(x)
    got = np.asarray(fftmod.rfft(x, n=n, backend="radix2"))
    assert got.shape == want.shape
    scale = float(np.max(np.abs(want)))
    error = float(np.max(np.abs(got - want)))
    tolerance = DFT_RELATIVE_TOLERANCE * n * scale
    assert error <= tolerance, (
        f"n={n}: the radix-2 transform differs from the DFT by {error:.3e}, "
        f"which is more than {tolerance:.3e}"
    )


# --------------------------------------------------------------------------
# the radix-2 backend
# --------------------------------------------------------------------------
@pytest.mark.parametrize("n", (8, 16, 32, 64, 256))
@pytest.mark.parametrize("axis", (0, 1, -1))
def test_the_radix2_backend_honours_the_transform_axis(n: int, axis: int):
    """The hand written backend transforms the axis it is asked for.

    It used to transform the last axis whatever the caller asked for, which
    returned the right numbers for the wrong axis and the wrong shape.
    """
    rng = np.random.default_rng(4242)
    block = rng.standard_normal((6, n))
    reference = np.asarray(fftmod.rfft(block, n=n, axis=axis, backend="numpy"))
    got = np.asarray(fftmod.rfft(block, n=n, axis=axis, backend="radix2"))
    assert got.shape == reference.shape, (
        f"n={n}, axis={axis}: the radix-2 backend returned {got.shape}, "
        f"expected {reference.shape}"
    )
    error = float(np.max(np.abs(got - reference)))
    scale = max(float(np.max(np.abs(reference))), 1.0)
    assert error / scale < 1e-13, (
        f"n={n}, axis={axis}: the radix-2 backend differs from the compiled one "
        f"by {error:.3e}"
    )


def test_the_radix2_backend_needs_a_power_of_two():
    """A length that is not a power of two is refused."""
    with pytest.raises(ValueError):
        fftmod.rfft(_full_scale(64), n=63, backend="radix2")


def test_fft_pow2_needs_a_power_of_two():
    """The hand written complex transform refuses an odd length."""
    with pytest.raises(ValueError):
        fftmod.fft_pow2(np.zeros(63))


@pytest.mark.parametrize("n", RADIX2_SIZES)
def test_ifft_pow2_inverts_fft_pow2(n: int):
    """The hand written inverse transform undoes the forward one."""
    x = _full_scale(n)
    back = fftmod.ifft_pow2(fftmod.fft_pow2(x))
    assert back.shape == x.shape
    assert np.allclose(back, x, atol=1e-12)


# --------------------------------------------------------------------------
# the short-time transform, the only consumer of the wrapper
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "n_fft",
    (
        2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 100, 101,
        127, 128, 129, 251, 256, 257, 511, 512, 513, 1000, 1001, 1023, 1024,
        1025, 2048, 4096,
    ),
)
@pytest.mark.parametrize("overlap", (2, 4))
def test_the_short_time_transform_round_trips_at_every_frame_size(n_fft: int, overlap: int):
    """The short-time transform rebuilds the signal at every frame size.

    The measurement goes through magnitude and phase, i.e. through the
    representation the spectral modules store and edit.
    """
    from aurcore.dsp.stft import STFT

    x = _full_scale(4096) * 0.5
    engine = STFT(n_fft, window="hann", overlap_factor=overlap)
    mag, phase_ = engine.analyze_block(x)
    back = engine.synthesize_block(mag, phase_, length=len(x))
    assert back.shape == x.shape
    error = float(np.max(np.abs(back - x)))
    assert _dbfs(error) < ROUND_TRIP_DBFS, (
        f"n_fft={n_fft}, overlap={overlap}: the short-time round trip is "
        f"{_dbfs(error):.1f} dBFS, it must stay below {ROUND_TRIP_DBFS:.1f} dBFS"
    )


@pytest.mark.parametrize(
    "length", (1, 63, 64, 100, 511, 1000, 1023, 1024, 1025, 2047, 2048, 3000, 4096)
)
def test_a_short_or_ragged_input_keeps_its_length(length: int):
    """A signal shorter than one frame, or not a multiple of the hop, comes back whole."""
    from aurcore.dsp.stft import STFT

    x = _full_scale(length) * 0.5
    engine = STFT(1024, window="hann", overlap_factor=2)
    mag, phase_ = engine.analyze_block(x, center=True)
    back = engine.synthesize_block(mag, phase_, length=length, center=True)
    assert back.shape == x.shape, (
        f"a signal of {length} samples came back as {back.shape}"
    )
    error = float(np.max(np.abs(back - x)))
    assert _dbfs(error) < ROUND_TRIP_DBFS, (
        f"a signal of {length} samples came back with an error of {_dbfs(error):.1f} dBFS"
    )


# --------------------------------------------------------------------------
# the phase at the edges of a block
# --------------------------------------------------------------------------
def test_the_nyquist_bin_of_an_even_block_is_real():
    """The top bin of an even-length block is the Nyquist bin, so it is real."""
    x = _full_scale(1024)
    spec = fftmod.rfft(x, n=1024)
    assert float(np.imag(spec[-1])) == 0.0
    assert float(np.angle(spec[-1])) in (0.0, math.pi, -math.pi)


def test_the_top_bin_of_an_odd_block_is_complex():
    """The top bin of an odd-length block is an ordinary bin, so it is complex."""
    x = _full_scale(1025)
    spec = fftmod.rfft(x, n=1025)
    assert spec.shape[-1] == 513
    assert float(np.imag(spec[-1])) != 0.0


def test_the_phase_of_a_block_survives_a_magnitude_phase_round_trip():
    """Storing the spectrum as magnitude and phase keeps the block.

    The polar form costs about 29 dB against the complex form: the rebuild
    error is 5.0e-15 (-286 dBFS) where the complex round trip is 1.7e-16
    (-315 dBFS).  That is the price of the representation the spectral
    editor works with, and it is far below anything that matters.
    """
    x = _full_scale(1024)
    spec = fftmod.rfft(x, n=1024)
    rebuilt = fftmod.to_complex(fftmod.magnitude(spec), fftmod.phase(spec))
    error = float(np.max(np.abs(rebuilt - spec)))
    assert _dbfs(error) < -280.0, (
        f"the magnitude/phase round trip is {_dbfs(error):.1f} dBFS, "
        "it must stay below -280.0 dBFS"
    )
    back = fftmod.irfft(rebuilt, n=1024)
    time_error = float(np.max(np.abs(back - x)))
    assert _dbfs(time_error) < -280.0


def test_the_transform_assumes_the_block_is_zero_padded():
    """A block that is not periodic over its own length leaks.

    The transform treats the block as if the signal continued as zeros
    outside it, so a discontinuity at a block edge appears in every bin.
    A tone that fits the block is transformed exactly; gating one sample at
    each end leaks 48.5 dB below the tone.
    """
    n = 1024
    bin_index = 64
    tone = np.cos(2.0 * math.pi * bin_index * np.arange(n) / n)

    def worst_other_bin(x: np.ndarray) -> float:
        spec = np.abs(np.asarray(fftmod.rfft(x, n=n)))
        return float(np.max(np.delete(spec, bin_index)))

    peak = float(np.max(np.abs(np.asarray(fftmod.rfft(tone, n=n)))))
    assert peak / max(worst_other_bin(tone), 1e-300) > 1e6, (
        "a periodic block must be transformed exactly"
    )

    gated = tone.copy()
    gated[0] = 0.0
    gated[-1] = 0.0
    leakage = worst_other_bin(gated) / peak
    assert _dbfs(leakage) < -40.0, (
        f"gating one sample at each end leaks {_dbfs(leakage):.1f} dB below the tone"
    )


# --------------------------------------------------------------------------
# the numerical edges
# --------------------------------------------------------------------------
def test_silence_stays_silent():
    """Silence gives an exactly zero spectrum and the magnitude floor."""
    spec = fftmod.rfft(np.zeros(64), n=64)
    assert np.all(spec == 0.0)
    assert np.all(fftmod.magnitude_db(spec) == -120.0)
    assert np.all(fftmod.phase(spec) == 0.0)
    back = fftmod.irfft(spec, n=64)
    assert np.all(back == 0.0)


def test_a_denormal_survives_the_transform():
    """A denormal is not flushed to zero and costs no measurable time."""
    x = np.full(64, 1e-320)
    spec = fftmod.rfft(x, n=64)
    assert np.all(np.isfinite(spec))
    assert float(np.abs(spec[0])) > 0.0
    assert np.all(fftmod.magnitude_db(spec) == -120.0)
    back = fftmod.irfft(spec, n=64)
    assert np.allclose(back, x, rtol=1e-12, atol=0.0)


def test_a_huge_value_stays_inside_the_float_range():
    """A value of 1e300 summed 64 times is still finite."""
    x = np.full(64, 1e300)
    with np.errstate(over="ignore"):
        spec = fftmod.rfft(x, n=64)
    assert np.all(np.isfinite(spec))
    assert np.all(np.isfinite(fftmod.magnitude_db(spec)))
    assert np.all(np.isfinite(fftmod.phase(spec)))


def test_an_overflowing_value_poisons_the_spectrum():
    """A value of 1e307 summed 64 times overflows, and that is reported."""
    x = np.full(64, 1e307)
    with np.errstate(over="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spec = fftmod.rfft(x, n=64)
    assert not np.all(np.isfinite(spec))
    # the helpers stay finite whatever the spectrum holds
    assert np.all(np.isfinite(fftmod.magnitude_db(spec)))
    assert np.all(np.isfinite(fftmod.phase(spec)))


def test_an_infinity_poisons_the_spectrum():
    """One infinity in the input makes the whole spectrum non-finite."""
    x = np.zeros(64)
    x[17] = np.inf
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spec = fftmod.rfft(x, n=64)
    assert not np.all(np.isfinite(spec))
    assert np.all(np.isfinite(fftmod.magnitude_db(spec)))
    assert np.all(np.isfinite(fftmod.phase(spec)))


def test_a_nan_poisons_the_spectrum():
    """One NaN in the input makes the whole spectrum non-finite."""
    x = np.zeros(64)
    x[17] = np.nan
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spec = fftmod.rfft(x, n=64)
    assert not np.all(np.isfinite(spec))
    assert np.all(np.isfinite(fftmod.magnitude_db(spec)))
    assert np.all(np.isfinite(fftmod.phase(spec)))


def test_the_helpers_are_always_finite():
    """The magnitude, the level and the phase helpers are total."""
    spec = np.array([np.nan + 0j, np.inf + 0j, 0j, 1.0 + 0j])
    assert np.all(np.isfinite(fftmod.magnitude(spec)))
    assert np.all(np.isfinite(fftmod.magnitude_db(spec)))
    assert np.all(np.isfinite(fftmod.phase(spec)))
    assert np.all(np.isfinite(fftmod.to_complex(fftmod.magnitude(spec), fftmod.phase(spec))))


def test_the_magnitude_floor_is_honoured():
    """A magnitude below the floor is lifted to it."""
    assert float(fftmod.magnitude_db(np.array([1e-6 + 0j]))[0]) == pytest.approx(-120.0)
    assert float(fftmod.magnitude(np.array([1e-9 + 0j]))[0]) == pytest.approx(1e-6)
    assert float(fftmod.magnitude(np.array([1e-9 + 0j]), floor_db=None)[0]) == pytest.approx(1e-9)


def test_the_magnitude_helpers_agree():
    """The dB helper is the log of the linear helper."""
    spec = fftmod.rfft(_full_scale(64), n=64)
    linear = fftmod.magnitude(spec)
    assert np.allclose(fftmod.magnitude_db(spec), 20.0 * np.log10(linear))


# --------------------------------------------------------------------------
# the padding convention and the argument checks
# --------------------------------------------------------------------------
def test_a_longer_transform_zero_pads_the_signal():
    """A transform longer than the signal zero-pads it."""
    x = np.ones(8)
    padded = fftmod.rfft(x, n=16)
    assert padded.shape == (9,)
    explicit = fftmod.rfft(np.concatenate([x, np.zeros(8)]), n=16)
    assert np.allclose(padded, explicit)
    assert float(padded[0].real) == pytest.approx(8.0)
    assert float(padded[0].imag) == pytest.approx(0.0)


def test_a_shorter_transform_truncates_the_signal():
    """A transform shorter than the signal truncates it."""
    x = np.arange(8, dtype=np.float64)
    truncated = fftmod.rfft(x, n=4)
    assert truncated.shape == (3,)
    assert np.allclose(truncated, fftmod.rfft(x[:4], n=4))


def test_a_complex_input_is_refused():
    """The real transform refuses a complex input."""
    with pytest.raises(ValueError):
        fftmod.rfft(np.zeros(8, dtype=np.complex128))


@pytest.mark.parametrize("n", (0, -1))
def test_a_length_below_one_is_refused(n: int):
    """A transform length below one is refused."""
    with pytest.raises(ValueError):
        fftmod.rfft(np.zeros(8), n=n)
    with pytest.raises(ValueError):
        fftmod.irfft(np.zeros(3, dtype=np.complex128), n=n)


def test_an_unknown_backend_is_refused():
    """An unknown backend name is refused."""
    with pytest.raises(ValueError):
        fftmod.rfft(np.zeros(8), backend="nonsense")


def test_a_scalar_input_is_refused():
    """A transform needs an input with at least one dimension."""
    with pytest.raises(ValueError):
        fftmod.rfft(np.float64(1.0))
    with pytest.raises(ValueError):
        fftmod.irfft(np.complex128(1.0 + 0j))


# --------------------------------------------------------------------------
# the sizes the helper functions report
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "n,expected", ((0, 1), (1, 1), (2, 2), (3, 4), (5, 8), (1024, 1024), (1025, 2048))
)
def test_fft_size_rounds_up_to_a_power_of_two(n: int, expected: int):
    """The size helper returns the next power of two."""
    assert fftmod.fft_size(n) == expected
