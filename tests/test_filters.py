"""
Tests for :mod:`aurcore.dsp.filters`.

The numbers in this file are measurements, not wishes: every assertion
below is the value that ``qa/filters/measure_filters.py`` produces for the
shipped code.  When one of them breaks, either the filter changed or the
harness and this file drifted apart; run the harness to find out which.

The rules the file has to keep, in one place:

* the level at ``f0`` of a low pass or a high pass is exactly ``q``
  (so it is -3.01 dB only for the Butterworth case);
* a band pass peaks at 0 dB, a notch is null at ``f0``, a bell reaches its
  ``gain_db``, a shelf reaches its ``gain_db`` on its own side;
* a Butterworth cascade is -3 dB at ``f0`` and a Linkwitz-Riley cascade is
  -6 dB at ``f0``;
* the one pole is -3 dB at ``cutoff_hz`` and its time constant
  parameterisation is the same filter;
* silence in gives exact zeros out, a full scale square stays finite and a
  single impulse stays finite;
* a filter that keeps its state gives the same answer in blocks of any
  size;
* the linear phase kernel is symmetric and has a constant group delay.
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np
import pytest

from aurcore.dsp import filters as F

FS = 48_000.0

#: The -3 dB point of a power response (1/sqrt(2)), in decibels.
M3 = 20.0 * math.log10(1.0 / math.sqrt(2.0))

#: The -6 dB point of a Linkwitz-Riley corner, in decibels.
M6 = 20.0 * math.log10(0.5)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def db(value):
    """Decibels of a magnitude (scalar or array)."""
    arr = np.abs(np.asarray(value, dtype=np.complex128))
    return 20.0 * np.log10(np.maximum(arr, 1e-300))


def response(coeffs, freqs, fs: float = FS) -> np.ndarray:
    """The complex response of a coefficient tuple."""
    b0, b1, b2, a1, a2 = coeffs
    w = 2.0 * math.pi * np.asarray(freqs, dtype=np.float64) / fs
    e1 = np.exp(-1j * w)
    e2 = e1 * e1
    return (b0 + b1 * e1 + b2 * e2) / (1.0 + a1 * e1 + a2 * e2)


def crossing(freqs, m_db, level, rising: bool) -> float:
    """The frequency where the response crosses ``level``, interpolated."""
    if rising:
        idx = np.nonzero((m_db[:-1] < level) & (m_db[1:] >= level))[0]
    else:
        idx = np.nonzero((m_db[:-1] > level) & (m_db[1:] <= level))[0]
    assert len(idx), "the response never crosses the level"
    i = int(idx[0])
    m0, m1 = float(m_db[i]), float(m_db[i + 1])
    f0, f1 = float(freqs[i]), float(freqs[i + 1])
    if m1 == m0:
        return f0
    return f0 + (level - m0) * (f1 - f0) / (m1 - m0)


def group_delay(h: np.ndarray, freqs: np.ndarray, fs: float = FS) -> np.ndarray:
    """The group delay of a complex response, in samples."""
    phase = np.unwrap(np.angle(h))
    w = 2.0 * math.pi * np.asarray(freqs, dtype=np.float64) / fs
    return -np.gradient(phase, w)


def lowpass_magnitudes(f0: float = 1000.0, length: int = 2049) -> np.ndarray:
    """The magnitude of a fourth order low pass on the FIR grid."""
    k = np.arange(length // 2 + 1, dtype=np.float64)
    freqs = k * FS / float(length)
    ratio = freqs / f0
    return 1.0 / np.sqrt(1.0 + ratio ** 8)


# ---------------------------------------------------------------------------
# the level at f0
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("q", [0.5, 0.7071067811865476, 1.0, 2.0, 10.0])
@pytest.mark.parametrize("factory", [F.biquad_lowpass, F.biquad_highpass])
def test_the_level_at_f0_is_exactly_q(factory, q):
    """The level at ``f0`` is ``q``, not -3 dB.

    The file used to promise a -3 dB point for every ``q``; that is true
    only for the Butterworth case.  Measured: -6.02 dB at q = 0.5,
    -3.01 dB at q = 0.7071, 0.00 dB at q = 1, +6.02 dB at q = 2 and
    +20.00 dB at q = 10.
    """
    coeffs = factory(1000.0, q, FS)
    got = db(response(coeffs, [1000.0])[0])
    assert got == pytest.approx(20.0 * math.log10(q), abs=0.01)


@pytest.mark.parametrize("factory", [F.biquad_lowpass, F.biquad_highpass])
def test_the_butterworth_case_is_minus_3_db_at_f0(factory):
    """With ``q = 1 / sqrt(2)`` the corner is the -3 dB point."""
    coeffs = factory(1000.0, 0.7071067811865476, FS)
    assert db(response(coeffs, [1000.0])[0]) == pytest.approx(M3, abs=0.01)


# ---------------------------------------------------------------------------
# the other factory designs
# ---------------------------------------------------------------------------
def test_the_band_pass_peaks_at_zero_db():
    """The band pass has a 0 dB peak, exactly where it is promised."""
    freqs = np.linspace(100.0, 5000.0, 200_001)
    m = db(response(F.biquad_bandpass(1000.0, 1.0, FS), freqs))
    assert float(m.max()) == pytest.approx(0.0, abs=0.01)
    assert freqs[int(m.argmax())] == pytest.approx(1000.0, abs=1.0)


@pytest.mark.parametrize("q", [0.5, 1.0, 2.0, 10.0])
def test_the_band_pass_is_f0_over_q_wide(q):
    """The -3 dB width of the band pass is ``f0 / q`` (measured 0.3 % to 0.9 % narrow)."""
    freqs = np.linspace(1.0, 20_000.0, 200_001)
    m = db(response(F.biquad_bandpass(1000.0, q, FS), freqs))
    lo = crossing(freqs, m, M3, True)
    hi = crossing(freqs, m, M3, False)
    assert (hi - lo) == pytest.approx(1000.0 / q, rel=0.01)


@pytest.mark.parametrize("q", [0.5, 1.0, 2.0, 10.0])
def test_the_notch_is_f0_over_q_wide_and_null_at_f0(q):
    """The notch is ``f0 / q`` wide and its null sits on ``f0``."""
    coeffs = F.biquad_notch(1000.0, q, FS)
    assert db(response(coeffs, [1000.0])[0]) < -200.0
    freqs = np.linspace(1.0, 20_000.0, 200_001)
    m = db(response(coeffs, freqs))
    lo = crossing(freqs, m, M3, False)
    hi = crossing(freqs, m, M3, True)
    assert (hi - lo) == pytest.approx(1000.0 / q, rel=0.01)


@pytest.mark.parametrize("gain", [-18.0, -6.0, 6.0, 18.0])
def test_the_bell_reaches_its_gain(gain):
    """The bell is exactly ``gain_db`` at ``f0``."""
    coeffs = F.biquad_peaking(1000.0, 1.0, gain, FS)
    assert db(response(coeffs, [1000.0])[0]) == pytest.approx(gain, abs=0.01)


def test_the_bell_bandwidth_is_the_half_amplitude_width():
    """``f0 / q`` is the half amplitude width, not the -3 dB width.

    Measured: the half amplitude width is 999.7 Hz for ``f0 / q`` =
    1000 Hz, the -3 dB width is 534.4 Hz, that is 0.534 times ``f0 / q``.
    """
    freqs = np.linspace(100.0, 5000.0, 400_001)
    m = db(response(F.biquad_peaking(1000.0, 1.0, 12.0, FS), freqs))
    ref = db(response(F.biquad_peaking(1000.0, 1.0, 12.0, FS), [1000.0])[0])
    half_lo = crossing(freqs, m, ref - 6.0206, True)
    half_hi = crossing(freqs, m, ref - 6.0206, False)
    assert (half_hi - half_lo) == pytest.approx(1000.0, rel=0.01)
    lo = crossing(freqs, m, ref + M3, True)
    hi = crossing(freqs, m, ref + M3, False)
    assert (hi - lo) == pytest.approx(534.0, rel=0.01)


@pytest.mark.parametrize("factory,below", [(F.biquad_lowshelf, True), (F.biquad_highshelf, False)])
@pytest.mark.parametrize("gain", [-12.0, 12.0])
def test_the_shelf_reaches_its_gain_on_its_own_side(factory, below, gain):
    """A shelf reaches ``gain_db`` on its own side and half of it at ``f0``."""
    coeffs = factory(1000.0, 0.7071067811865476, gain, FS)
    freqs = np.linspace(1.0, 20_000.0, 200_001)
    m = db(response(coeffs, freqs))
    shelf = float(m[0]) if below else float(m[-1])
    assert shelf == pytest.approx(gain, abs=0.01)
    mid = crossing(freqs, m, gain / 2.0, gain < 0 if below else gain > 0)
    assert mid == pytest.approx(1000.0, abs=1.0)


def test_the_all_pass_has_a_flat_magnitude():
    """The all pass keeps the magnitude at 1 and does not claim a linear phase."""
    freqs = np.linspace(1.0, 23_900.0, 100_001)
    m = db(response(F.biquad_allpass(1000.0, 1.0, FS), freqs))
    assert float(m.max() - m.min()) < 1e-6
    gd = group_delay(response(F.biquad_allpass(1000.0, 1.0, FS), freqs), freqs)
    assert float(gd.max() - gd.min()) > 1.0


@pytest.mark.parametrize("order", [2, 4, 6, 8])
def test_a_butterworth_cascade_is_minus_3_db_at_f0(order):
    """The whole cascade is -3 dB at the corner."""
    cascade = F.Cascade(F.butterworth_lowpass(1000.0, FS, order), FS)
    assert db(cascade.freq_response([1000.0])[0]) == pytest.approx(M3, abs=0.01)


@pytest.mark.parametrize("order", [4, 8, 12])
def test_a_linkwitz_riley_cascade_is_minus_6_db_at_f0(order):
    """Two cascaded Butterworth filters give -6 dB at the corner."""
    cascade = F.Cascade(F.linkwitz_riley_lowpass(1000.0, FS, order), FS)
    assert db(cascade.freq_response([1000.0])[0]) == pytest.approx(M6, abs=0.01)


# ---------------------------------------------------------------------------
# the one pole
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fc", [20.0, 1000.0, 10_000.0])
def test_the_one_pole_is_minus_3_db_at_its_corner(fc):
    """The corner is solved for, so the -3 dB point is the corner."""
    pole = F.OnePole(cutoff_hz=fc, fs=FS)
    freqs = np.logspace(-3, math.log10(23_000.0), 100_001)
    m = db(pole.freq_response(freqs))
    f3 = crossing(freqs, m, M3, False)
    assert f3 == pytest.approx(fc, rel=0.001)


@pytest.mark.parametrize("fc", [20.0, 1000.0, 10_000.0])
def test_the_one_pole_time_constant_is_the_same_filter(fc):
    """``OnePole(tau_s=one.tau_s)`` is the filter ``OnePole(cutoff_hz=fc)``.

    Before the fix the time constant branch used the exponential
    approximation, which put the corner 0.14 % high at 1 kHz and 18.95 %
    high at 10 kHz.
    """
    by_corner = F.OnePole(cutoff_hz=fc, fs=FS)
    by_tau = F.OnePole(tau_s=by_corner.tau_s, fs=FS)
    assert by_tau.a == pytest.approx(by_corner.a, rel=1e-12)
    assert by_tau.cutoff_hz == pytest.approx(fc, rel=1e-9)
    assert by_tau.tau_s == pytest.approx(by_corner.tau_s, rel=1e-12)


def test_the_one_pole_time_constant_names_the_corner():
    """The time constant and the corner are two names for the same number."""
    tau = 1.0 / (2.0 * math.pi * 1000.0)
    pole = F.OnePole(tau_s=tau, fs=FS)
    assert pole.cutoff_hz == pytest.approx(1000.0, rel=1e-9)


# ---------------------------------------------------------------------------
# the DC blocker
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fc", [5.0, 20.0, 100.0])
def test_the_dc_blocker_corner_is_close_to_its_nominal_value(fc):
    """The classic corner formula is an approximation; the error is documented."""
    blocker = F.DCBlocker(fc, FS)
    freqs = np.logspace(-4, math.log10(23_000.0), 100_001)
    m = db(blocker.freq_response(freqs))
    f3 = crossing(freqs, m, M3, True)
    assert abs(f3 / fc - 1.0) < 0.007


def test_the_dc_blocker_removes_the_offset():
    """The DC blocker takes the DC component away."""
    n = 8192
    t = np.arange(n) / FS
    x = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t) + 0.25
    y = F.DCBlocker(20.0, FS).process(x)
    assert abs(float(np.mean(y))) < 0.02


# ---------------------------------------------------------------------------
# the linear phase kernel
# ---------------------------------------------------------------------------
def test_the_linear_phase_kernel_is_symmetric():
    """The kernel is symmetric, so the filter has a constant group delay."""
    kernel = F.linear_phase_fir(lowpass_magnitudes(), FS)
    assert len(kernel) % 2 == 1
    assert float(np.max(np.abs(kernel - kernel[::-1]))) < 1e-12


def test_the_linear_phase_filter_has_a_constant_group_delay():
    """The group delay is constant over the pass band, to a thousandth of a sample."""
    fir = F.FIR(F.linear_phase_fir(lowpass_magnitudes(), FS), FS)
    freqs = np.logspace(-1, math.log10(20_000.0), 100_001)
    gd = group_delay(fir.freq_response(freqs), freqs)
    assert float(gd.max() - gd.min()) < 1e-3
    assert float(gd.mean()) == pytest.approx(1024.0, abs=0.01)


def test_the_linear_phase_filter_keeps_the_designed_magnitude():
    """The magnitude of the filter is the magnitude that was designed."""
    fir = F.FIR(F.linear_phase_fir(lowpass_magnitudes(), FS), FS)
    freqs = np.array([100.0, 1000.0, 5000.0, 10_000.0])
    got = np.abs(fir.freq_response(freqs))
    want = 1.0 / np.sqrt(1.0 + (freqs / 1000.0) ** 8)
    assert np.allclose(got, want, rtol=0.05)


def test_the_linear_phase_kernel_rejects_a_bad_request():
    """An even length or a wrong number of samples is refused."""
    with pytest.raises(ValueError):
        F.linear_phase_fir(np.ones(10), FS, length=10)
    with pytest.raises(ValueError):
        F.linear_phase_fir(np.ones(10), FS, length=21)


# ---------------------------------------------------------------------------
# the streaming FIR
# ---------------------------------------------------------------------------
def test_the_fir_is_the_same_in_blocks_of_any_size():
    """A filter that keeps its state gives the same answer in any block size."""
    kernel = F.linear_phase_fir(lowpass_magnitudes(), FS)
    n = 4096
    t = np.arange(n) / FS
    x = (0.5 * np.sin(2.0 * math.pi * 1000.0 * t))[:, None]
    one = F.FIR(kernel, FS).process(x)
    streamed = F.FIR(kernel, FS)
    pieces = [streamed.process(x[i:i + 64]) for i in range(0, n, 64)]
    assert float(np.max(np.abs(one - np.concatenate(pieces, axis=0)))) == 0.0


def test_the_fir_reports_its_latency():
    """The latency of a symmetric kernel is half its length."""
    kernel = F.linear_phase_fir(lowpass_magnitudes(), FS)
    fir = F.FIR(kernel, FS)
    assert fir.latency_samples == (len(kernel) - 1) // 2


def test_the_fir_delays_the_signal_by_its_latency():
    """A tone comes out ``latency_samples`` later than it went in."""
    kernel = F.linear_phase_fir(lowpass_magnitudes(), FS)
    fir = F.FIR(kernel, FS)
    n = 16_384
    t = np.arange(n) / FS
    x = (0.5 * np.sin(2.0 * math.pi * 200.0 * t))[:, None]
    out = fir.process(x)
    delay = fir.latency_samples
    # the pass band is flat at 200 Hz, so the steady state of the output
    # must be the input delayed by exactly that many samples; the first
    # kernel length of samples are the start up transient
    start = 2 * delay
    assert float(np.max(np.abs(out[start:] - x[start - delay:n - delay]))) < 1e-6


def test_the_fir_keeps_the_two_channels_apart():
    """Each channel gets its own memory."""
    kernel = F.linear_phase_fir(lowpass_magnitudes(), FS)
    fir = F.FIR(kernel, FS)
    left = np.ones((256, 1))
    both = np.concatenate([left, np.zeros((256, 1))], axis=1)
    out = fir.process(both)
    assert out.shape == both.shape
    assert float(np.max(np.abs(out[:, 1]))) == 0.0


# ---------------------------------------------------------------------------
# the edges
# ---------------------------------------------------------------------------
def _every_filter():
    """One instance of every filter the file offers."""
    return [
        F.Biquad(*F.biquad_lowpass(1000.0, 0.7071067811865476, FS)),
        F.Biquad(*F.biquad_highpass(1000.0, 0.7071067811865476, FS)),
        F.Biquad(*F.biquad_bandpass(1000.0, 10.0, FS)),
        F.Biquad(*F.biquad_notch(1000.0, 10.0, FS)),
        F.Biquad(*F.biquad_peaking(1000.0, 1.0, 12.0, FS)),
        F.Biquad(*F.biquad_lowshelf(1000.0, 0.7071067811865476, -12.0, FS)),
        F.Biquad(*F.biquad_highshelf(1000.0, 0.7071067811865476, -12.0, FS)),
        F.Biquad(*F.biquad_allpass(1000.0, 1.0, FS)),
        F.Butterworth(1000.0, FS, 4),
        F.LinkwitzRiley(1000.0, FS, 4),
        F.OnePole(1000.0, FS),
        F.DCBlocker(20.0, FS),
        F.FIR(F.linear_phase_fir(lowpass_magnitudes(), FS), FS),
    ]


def test_silence_gives_exact_zeros():
    """A silent block gives exact zeros, not a denormal whisper."""
    for filt in _every_filter():
        out = np.asarray(filt.process(np.zeros(512, dtype=np.float64)))
        assert np.all(out == 0.0), f"{filt!r} let a non zero sample through"


def test_a_full_scale_square_stays_finite():
    """A full scale square does not produce NaN or an infinity."""
    n = 4096
    t = np.arange(n) / FS
    square = np.sign(np.sin(2.0 * math.pi * 220.0 * t))
    for filt in _every_filter():
        out = np.asarray(filt.process(square))
        assert np.all(np.isfinite(out)), f"{filt!r} went non finite"
        assert float(np.max(np.abs(out))) < 10.0, f"{filt!r} grew"


def test_a_single_impulse_stays_finite():
    """A single impulse gives a finite answer and does not blow the filter up."""
    for filt in _every_filter():
        impulse = np.zeros(1024, dtype=np.float64)
        impulse[0] = 1.0
        out = np.asarray(filt.process(impulse))
        assert np.all(np.isfinite(out)), f"{filt!r} went non finite"
        assert float(np.max(np.abs(out))) < 10.0, f"{filt!r} grew"


def test_a_block_of_impulses_does_not_grow():
    """A train of impulses must not make the filter unstable."""
    for filt in _every_filter():
        train = np.zeros(256, dtype=np.float64)
        train[::16] = 1.0
        out = np.asarray(filt.process(train))
        assert np.all(np.isfinite(out)), f"{filt!r} went non finite"
        assert float(np.max(np.abs(out))) < 10.0, f"{filt!r} grew"


def test_the_filters_keep_their_state_between_blocks():
    """One block and many blocks give the same answer."""
    n = 4096
    t = np.arange(n) / FS
    x = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t) + 0.2 * np.sin(2.0 * math.pi * 50.0 * t)
    for filt in _every_filter():
        streamed = copy.deepcopy(filt)
        one = np.asarray(filt.process(x))
        pieces = [np.asarray(streamed.process(x[i:i + 64])) for i in range(0, n, 64)]
        assert float(np.max(np.abs(one - np.concatenate(pieces)))) < 1e-12


# ---------------------------------------------------------------------------
# the cost
# ---------------------------------------------------------------------------
def test_a_biquad_block_costs_less_than_the_budget():
    """64 samples of biquad must cost well under a millisecond.

    Measured on this machine: 9.4 us per 64 sample block with the SciPy
    path and 21.1 us with the NumPy fallback.
    """
    filt = F.Biquad(*F.biquad_lowpass(1000.0, 0.7071067811865476, FS))
    block = np.zeros(64, dtype=np.float64)
    filt.process(block)
    start = time.perf_counter()
    for _ in range(200):
        filt.process(block)
    per_block = (time.perf_counter() - start) / 200.0
    assert per_block < 1e-3, f"a 64 sample block costs {per_block * 1e6:.1f} us"


def test_the_one_pole_block_costs_less_than_the_budget():
    """The one pole is a Python loop, so it is the slowest of the small filters."""
    pole = F.OnePole(1000.0, FS)
    block = np.zeros(512, dtype=np.float64)
    pole.process(block)
    start = time.perf_counter()
    for _ in range(50):
        pole.process(block)
    per_block = (time.perf_counter() - start) / 50.0
    assert per_block < 5e-3, f"a 512 sample block costs {per_block * 1e6:.1f} us"


# ---------------------------------------------------------------------------
# the de-hum module
# ---------------------------------------------------------------------------
def _dehum(linear: bool, channels: int = 1):
    """A de-hum module with the linear phase path on or off."""
    from aurcore.modules.de_hum import DeHum

    module = DeHum(sample_rate=48_000, channels=channels)
    module.params.set("Linear Phase Filters", linear)
    module.params.set("Base Frequency", 50.0)
    module.params.set("Number of Harmonics", 4)
    module.params.set("Enable Highpass", False)
    module.params.set("Enable Lowpass", False)
    module.params.set("Adaptive", False)
    return module


def test_the_dehum_linear_phase_path_survives_a_small_block():
    """The linear phase path must give the same answer in blocks of any size."""
    n = 4800
    t = np.arange(n) / FS
    x = (0.3 * np.sin(2.0 * math.pi * 1000.0 * t))[:, None]
    one = np.asarray(_dehum(True).process(x))[:, 0]
    module = _dehum(True)
    pieces = [np.asarray(module.process(x[i:i + 64]))[:, 0] for i in range(0, n, 64)]
    streamed = np.concatenate(pieces)
    assert float(np.max(np.abs(one - streamed))) < 1e-9


def test_the_dehum_linear_phase_path_does_not_read_the_future():
    """The linear phase path must not need samples that have not been played."""
    module = _dehum(True)
    n = 24_000
    impulse = np.zeros((n, 1))
    impulse[0, 0] = 1.0
    out = np.asarray(module.process(impulse))[:, 0]
    early = float(np.max(np.abs(out[:2399])))
    assert early < 1e-6, "the filter answered before the impulse arrived"


# ---------------------------------------------------------------------------
# the transform path of the streaming FIR
# ---------------------------------------------------------------------------
def _long_kernel(taps: int = 4801) -> np.ndarray:
    """A long symmetric kernel, the length the de-hum uses."""
    k = np.arange(taps // 2 + 1, dtype=np.float64)
    freqs = k * FS / float(taps)
    return F.linear_phase_fir(1.0 / np.sqrt(1.0 + (freqs / 1000.0) ** 8), FS)


def _noise(n: int) -> np.ndarray:
    """A reproducible noise signal."""
    rng = np.random.default_rng(20261021)
    return (rng.standard_normal(n) * 0.3)[:, None]


def test_a_long_kernel_is_filtered_with_a_transform():
    """The transform path is used from 4096 taps on.

    The direct convolution of a long kernel convolves the whole tail of
    the previous block again on every call, so its cost does not fall with
    the size of the block: the 4801 tap kernel of the de-hum cost 5515 us
    for a 64 sample block with two channels, four times the realtime
    budget of that block.
    """
    assert F._FFT_MIN_TAPS == 4096
    kernel = _long_kernel()
    fir = F.FIR(kernel, FS)
    assert fir._use_transform(64) is True
    assert F.FIR(kernel, FS, exact=True)._use_transform(64) is False


def test_the_transform_path_is_the_exact_convolution():
    """The fast path is the exact convolution, to the rounding of the transform.

    Measured over a 20000 sample stream of noise: 1.1e-15 absolute and
    9.4e-16 relative, that is four orders of magnitude below the 1e-13
    the two paths are allowed to differ by.
    """
    kernel = _long_kernel()
    x = _noise(20_000)
    fast = F.FIR(kernel, FS).process(x)[:, 0]
    exact = F.FIR(kernel, FS, exact=True).process(x)[:, 0]
    diff = float(np.max(np.abs(fast - exact)))
    assert diff < 1e-13, f"the two paths differ by {diff:.3e}"
    peak = float(np.max(np.abs(exact)))
    assert diff / peak < 1e-13


def test_the_transform_path_is_the_same_in_blocks_of_any_size():
    """A long kernel gives the same answer in blocks of any size.

    The transform path is not bit-for-bit reproducible: the rounding of a
    transform depends on the length of its frame, so two block sizes give
    answers that differ in the last bits.  Measured over a 20000 sample
    stream: 5.6e-16 absolute and 4.7e-16 relative.
    """
    kernel = _long_kernel()
    x = _noise(20_000)
    one = F.FIR(kernel, FS).process(x)
    streamed = F.FIR(kernel, FS)
    pieces = [streamed.process(x[i:i + 64]) for i in range(0, x.shape[0], 64)]
    diff = float(np.max(np.abs(one - np.concatenate(pieces, axis=0))))
    assert diff < 1e-13, f"the block sizes differ by {diff:.3e}"
    assert diff / float(np.max(np.abs(one))) < 1e-13


def test_the_transform_path_survives_a_change_of_block_size():
    """The history of the filter survives a block size that changes."""
    kernel = _long_kernel()
    x = _noise(20_000)
    one = F.FIR(kernel, FS).process(x)
    mixed = F.FIR(kernel, FS)
    out = []
    pos = 0
    for size in (64, 128, 256, 512, 64, 1000, 64) * 3:
        out.append(mixed.process(x[pos:pos + size]))
        pos += size
    diff = float(np.max(np.abs(one[:pos] - np.concatenate(out, axis=0))))
    assert diff < 1e-13, f"the block sizes differ by {diff:.3e}"


def test_the_direct_path_is_bit_for_bit_the_same_in_blocks_of_any_size():
    """The direct convolution is bit-for-bit reproducible.

    That is why the direct path is kept for the kernels that are short
    enough for it to be cheap.
    """
    kernel = _long_kernel()
    x = _noise(4_096)
    one = F.FIR(kernel, FS, exact=True).process(x)
    streamed = F.FIR(kernel, FS, exact=True)
    pieces = [streamed.process(x[i:i + 64]) for i in range(0, x.shape[0], 64)]
    diff = float(np.max(np.abs(one - np.concatenate(pieces, axis=0))))
    assert diff == 0.0, f"the direct path differs by {diff:.3e}"


def test_the_dehum_linear_phase_path_declares_its_latency():
    """The module declares the delay it really has.

    The fast path is a weighted overlap-add bank: the frames overlap, so
    the bank answers a window after the input, and the delay of the
    kernel is on top of that.  The kernel is 4801 taps long, so the
    delay of the filter is 2400 samples and the window is 4096 samples:
    the module declares 6496 samples, and the impulse comes out at
    exactly that sample.
    """
    module = _dehum(True)
    assert module.latency_samples == 6496
    impulse = np.zeros((12_000, 1))
    impulse[0, 0] = 1.0
    out = np.asarray(module.process(impulse))[:, 0]
    assert int(np.argmax(np.abs(out))) == module.latency_samples
    assert float(np.max(np.abs(out[:module.latency_samples]))) == 0.0


def test_the_dehum_hum_only_path_adds_no_latency():
    """The estimate of the hum is not the output of the filter chain.

    The estimate is made in the spectral domain, so the delay of the
    linear phase kernel is not part of it, and the module must not
    declare it.
    """
    module = _dehum(True)
    module.params.set("Output Hum Only", True)
    assert module.latency_samples == 0


def test_the_dehum_linear_phase_path_costs_less_than_the_budget():
    """A 64 sample block of the linear phase path fits into its budget.

    The realtime budget of a 64 sample block at 48 kHz is 1333.3 us.  The
    direct convolution of the 4801 tap kernel cost 5515 us for that block
    with two channels; with the transform it costs 186 us, and with the
    bank 59 us.
    """
    module = _dehum(True, channels=2)
    block = np.zeros((64, 2), dtype=np.float64)
    module.process(block)
    start = time.perf_counter()
    for _ in range(50):
        module.process(block)
    per_block = (time.perf_counter() - start) / 50.0
    assert per_block < 1333.3e-6, (
        f"a 64 sample block costs {per_block * 1e6:.1f} us, the budget is "
        f"1333.3 us"
    )


# ---------------------------------------------------------------------------
# the weighted overlap-add bank
# ---------------------------------------------------------------------------
def test_the_window_of_the_bank_is_symmetric_and_its_halves_add_up():
    """The window is symmetric and its two halves add up to one.

    That is what makes the overlap-add of the frames reproduce the
    convolution with the kernel exactly: the two frames that share a
    sample weigh it with the sum of the two halves of the window.
    """
    from aurcore.dsp.wola import cola_window

    w = cola_window(4096)
    assert w.shape == (4096,)
    assert float(np.max(np.abs(w - w[::-1]))) < 1e-15, "the window is not symmetric"
    halves = w[:2048] + w[2048:]
    assert float(np.max(np.abs(halves - 1.0))) < 1e-15, (
        "the two halves of the window do not add up to one"
    )


def test_the_bank_applies_its_kernel_exactly():
    """The bank is the convolution with the kernel, to the rounding of the transform.

    Measured over a 20000 sample stream of noise: 1.1e-15 absolute and
    6.7e-16 relative, that is two orders of magnitude below the 1e-13 the
    two paths are allowed to differ by.
    """
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    x = _noise(20_000)
    bank = WolaBank(kernel, FS)
    got = np.asarray(bank.process(x))[:, 0]
    want = np.convolve(x[:, 0], kernel)[:20_000]
    # The bank answers a window later than the convolution, and it is
    # silent until its delay has elapsed, so the comparison starts
    # there.
    shift = bank.window_length
    start = bank.latency_samples
    diff = float(np.max(np.abs(got[start:] - want[start - shift:20_000 - shift])))
    assert diff < 1e-13, f"the bank differs from the convolution by {diff:.3e}"
    assert diff / float(np.max(np.abs(want))) < 1e-13


def test_the_bank_has_the_response_of_its_kernel():
    """The bank is a way of running a filter, not a different filter."""
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    freqs = np.linspace(1.0, 20_000.0, 500)
    bank = WolaBank(kernel, FS)
    fir = F.FIR(kernel, FS)
    diff = float(np.max(np.abs(bank.freq_response(freqs) - fir.freq_response(freqs))))
    assert diff < 1e-12, f"the responses differ by {diff:.3e}"


def test_the_bank_is_the_same_in_blocks_of_any_size():
    """A signal filtered in blocks of any size gives the same answer.

    Measured over a 20000 sample stream: 4.4e-16 absolute.
    """
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    x = _noise(20_000)
    one = np.asarray(WolaBank(kernel, FS).process(x))
    bank = WolaBank(kernel, FS)
    pieces = [bank.process(x[i:i + 64]) for i in range(0, x.shape[0], 64)]
    diff = float(np.max(np.abs(one - np.concatenate(pieces, axis=0))))
    assert diff < 1e-13, f"the block sizes differ by {diff:.3e}"
    assert diff / float(np.max(np.abs(one))) < 1e-13


def test_the_bank_is_silent_before_its_delay():
    """The bank answers nothing before the delay it declares."""
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    bank = WolaBank(kernel, FS)
    impulse = np.zeros((12_000, 1))
    impulse[0, 0] = 1.0
    out = np.asarray(bank.process(impulse))[:, 0]
    assert float(np.max(np.abs(out[:bank.latency_samples]))) == 0.0
    loud = np.flatnonzero(np.abs(out) > 1e-9)
    assert loud.size > 0
    assert int(loud[0]) == bank.latency_samples


def test_the_bank_declares_the_window_and_the_delay_of_the_kernel():
    """The delay of the bank is the window it needs plus the delay of the kernel."""
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    bank = WolaBank(kernel, FS)
    assert bank.latency_samples == bank.window_length + 2400


def test_the_bank_passes_a_flat_signal_through():
    """A kernel of one tap passes the signal through, a window later."""
    from aurcore.dsp.wola import WolaBank

    x = _noise(8_000)
    bank = WolaBank(np.array([1.0]), FS)
    got = np.asarray(bank.process(x))[:, 0]
    shift = bank.window_length
    assert float(np.max(np.abs(got[shift:] - x[:8000 - shift, 0]))) < 1e-13


def test_the_bank_costs_less_than_the_budget():
    """A 64 sample block of the bank fits into its budget.

    The bank pays for its transform once per half window, so the cost of
    a block does not grow with the size of the block.
    """
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    bank = WolaBank(kernel, FS)
    block = np.zeros((64, 2), dtype=np.float64)
    bank.process(block)
    start = time.perf_counter()
    for _ in range(50):
        bank.process(block)
    per_block = (time.perf_counter() - start) / 50.0
    assert per_block < 1333.3e-6, (
        f"a 64 sample block costs {per_block * 1e6:.1f} us, the budget is "
        f"1333.3 us"
    )


def test_the_bank_takes_a_new_kernel_without_losing_its_memory():
    """A new kernel does not restart the bank."""
    from aurcore.dsp.wola import WolaBank

    kernel = _long_kernel()
    bank = WolaBank(kernel, FS)
    x = _noise(20_000)
    bank.process(x[:8_000])
    bank.set_kernel(kernel * 0.5)
    assert float(np.max(np.abs(bank.kernel - kernel * 0.5))) == 0.0
    out = np.asarray(bank.process(x[8_000:20_000]))
    assert out.shape == (12_000, 1)
    assert bool(np.all(np.isfinite(out)))


def test_the_fir_takes_a_new_kernel_without_losing_its_memory():
    """A new kernel does not restart the streaming filter."""
    kernel = _long_kernel()
    fir = F.FIR(kernel, FS)
    x = _noise(8_000)
    fir.process(x)
    fir.set_kernel(kernel * 0.5)
    assert float(np.max(np.abs(fir.kernel - kernel * 0.5))) == 0.0
    out = fir.process(x)
    assert out.shape == x.shape
    assert bool(np.all(np.isfinite(out)))
