"""
The loudness meter against the published standards.

The meter in :mod:`aurcore.dsp.loudness` is what the batch runner reports for
every delivered file, so a change in it moves every number the project
delivers.  The tests in this file pin the meter to the published values:

* ITU-R BS.1770-4 Annex 1  -- the K-weighting filter, the channel weights
  and the gating of the integrated loudness;
* ITU-R BS.1770-4 Annex 2  -- the true peak;
* EBU Tech 3341 Table 1    -- the 'EBU mode' compliance tests and their
  tolerances (0.1 LU for the loudness, +0.2 / -0.4 dBTP for the true peak);
* EBU Tech 3342 Table 1    -- the loudness range compliance tests and their
  tolerance (1 LU).

The reference level is the important one: a 1 kHz sine at the reference
level must read -23.0 LUFS, and the reading is pinned to a tenth of a
decibel so that a change in the meter cannot pass unnoticed.

The measurement harness lives in ``qa/loudness/`` and is shared with the
report (``qa/loudness/REPORT.md``), so the numbers in the failure messages
are the numbers of the report.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from aurcore.dsp import loudness

#: The sample rate the published test signals are defined at.
SAMPLE_RATE = 48_000

#: The K-weighting coefficients of ITU-R BS.1770-4, Tables 1 and 2.
PUBLISHED_SHELF = (
    (1.53512485958697, -2.69169618940638, 1.19839281085285),
    (1.0, -1.69065929318241, 0.73248077421585),
)
PUBLISHED_RLB = (
    (1.0, -2.0, 1.0),
    (1.0, -1.99004745483398, 0.99007225036621),
)

#: The loudness the reference level reads, measured with this meter.  The
#: published tolerance is 0.1 LU; this value is much tighter on purpose.
REFERENCE_READING = -22.9933


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
def tone(
    seconds: float,
    peak_dbfs: float = -23.0,
    freq: float = 1000.0,
    channels: int = 1,
    phase: float = 0.0,
    sr: int = SAMPLE_RATE,
) -> np.ndarray:
    """A sine wave of a given peak level.

    ``peak_dbfs`` is the peak level of one channel, which is how the EBU
    mode test signals are specified.  The channels carry the same wave, in
    phase.
    """
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / sr
    wave = 10.0 ** (peak_dbfs / 20.0) * np.sin(2.0 * math.pi * freq * t + phase)
    return np.repeat(wave[:, None], channels, axis=1)


def segments(*parts, channels: int = 1, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Join tone segments of ``(seconds, peak_dbfs, freq)`` into one signal."""
    pieces = [tone(seconds, peak_dbfs=level, freq=freq, sr=sr, channels=channels)
              for seconds, level, freq in parts]
    return np.concatenate(pieces, axis=0)


def fade(x: np.ndarray, ms: float = 10.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Apply a raised cosine fade in and fade out."""
    n = int(round(ms * 1e-3 * sr))
    if n <= 0 or 2 * n >= x.shape[0]:
        return x
    ramp = 0.5 * (1.0 - np.cos(math.pi * np.arange(n) / n))
    window = np.ones(x.shape[0])
    window[:n] = ramp
    window[-n:] = ramp[::-1]
    return x * window[:, None]


# ---------------------------------------------------------------------------
# the K-weighting filter
# ---------------------------------------------------------------------------
def test_the_k_weighting_filter_is_the_published_one():
    """The 48 kHz coefficients are the ones of BS.1770-4, Tables 1 and 2."""
    coeffs = loudness.k_weighting_coefficients(SAMPLE_RATE)
    for name, published in (("shelf", PUBLISHED_SHELF), ("rlb", PUBLISHED_RLB)):
        b, a = coeffs[name]
        for got, want in zip(b, published[0]):
            assert got == pytest.approx(want, abs=1e-12)
        for got, want in zip(a, published[1]):
            assert got == pytest.approx(want, abs=1e-12)


def test_the_cascade_is_the_fourth_order_combination():
    """The cascade entry is both stages, not one numerator and one denominator."""
    coeffs = loudness.k_weighting_coefficients(SAMPLE_RATE)
    b, a = coeffs["cascade"]
    shelf_b, shelf_a = coeffs["shelf"]
    rlb_b, rlb_a = coeffs["rlb"]
    assert len(b) == len(shelf_b) + len(rlb_b) - 1
    assert len(a) == len(shelf_a) + len(rlb_a) - 1
    # the cascade of two second order stages is a fourth order filter
    assert b[0] == pytest.approx(shelf_b[0] * rlb_b[0])
    assert a[0] == pytest.approx(shelf_a[0] * rlb_a[0])


# ---------------------------------------------------------------------------
# the reference level
# ---------------------------------------------------------------------------
def test_the_reference_level_reads_minus_23_lufs():
    """A 1 kHz sine at the reference level reads -23.0 LUFS.

    This is the level every delivery target is written against, so the
    tolerance is the published one: 0.1 LU (EBU Tech 3341, test 1).
    """
    x = tone(20.0, peak_dbfs=-23.0, channels=2)
    measured = loudness.integrated_loudness(x, SAMPLE_RATE)
    assert measured == pytest.approx(-23.0, abs=0.1)


def test_the_reference_level_does_not_move():
    """The reading of the reference level is pinned to a tenth of a decibel.

    A change in the filter, in the offset, in the gating or in the channel
    weights moves this number, and with it every delivered file.
    """
    x = tone(20.0, peak_dbfs=-23.0, channels=2)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        REFERENCE_READING, abs=0.005)


def test_a_mono_sine_at_minus_23_dbfs_rms_reads_the_same():
    """The same level written as an RMS level reads the same.

    A 1 kHz sine whose root mean square is -23 dBFS carries the same energy
    as a stereo pair whose peak is -23 dBFS, so both must read -23.0 LUFS.
    """
    x = tone(20.0, peak_dbfs=-23.0 + 20.0 * math.log10(math.sqrt(2.0)))
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)


def test_the_momentary_and_short_term_readings_at_the_reference_level():
    """The three time scales of EBU mode all read -23.0 LUFS."""
    x = tone(20.0, peak_dbfs=-23.0, channels=2)
    assert loudness.momentary_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)
    assert loudness.short_term_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)


def test_the_meter_reads_the_same_at_44100_hz():
    """The filter is redesigned for the rate, and the reading does not move."""
    x = tone(20.0, peak_dbfs=-23.0, channels=2, sr=44_100)
    assert loudness.integrated_loudness(x, 44_100) == pytest.approx(
        -23.0, abs=0.1)


# ---------------------------------------------------------------------------
# the gating
# ---------------------------------------------------------------------------
def test_a_pause_does_not_pull_the_integrated_loudness_down():
    """A pause is gated out, so it does not change the integrated loudness."""
    loud = tone(10.0, peak_dbfs=-23.0, channels=2)
    for pause in (np.zeros((5 * SAMPLE_RATE, 2)),
                  tone(5.0, peak_dbfs=-70.0, channels=2)):
        x = np.concatenate([loud, pause, loud], axis=0)
        assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
            -23.0, abs=0.1)


def test_the_quiet_passages_are_gated_out():
    """EBU Tech 3341, test 3 and test 4: the quiet parts do not count."""
    x = segments((10.0, -36.0, 1000.0), (60.0, -23.0, 1000.0),
                 (10.0, -36.0, 1000.0), channels=2)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)
    x = segments((10.0, -72.0, 1000.0), (10.0, -36.0, 1000.0),
                 (60.0, -23.0, 1000.0), (10.0, -36.0, 1000.0),
                 (10.0, -72.0, 1000.0), channels=2)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)


def test_the_quiet_passage_at_three_decibels_below_still_counts():
    """A passage inside the relative gate counts, as the standard says.

    The relative gate sits 10 LU below the absolute gated loudness level,
    so a passage 3 dB below the programme is inside the gate and counts:
    two thirds of the signal at -23 LUFS and one third at -26 LUFS read
    -23.79 LUFS, the energy mean of the two.
    """
    x = segments((20.0, -23.0, 1000.0), (20.0, -26.0, 1000.0),
                 (20.0, -23.0, 1000.0), channels=2)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.79, abs=0.05)


# ---------------------------------------------------------------------------
# the channel weights
# ---------------------------------------------------------------------------
def test_the_channel_weights_follow_table_3():
    """The centre channel weighs 1.0 and the LFE channel is left out."""
    assert loudness.channel_weights(1).tolist() == [1.0]
    assert loudness.channel_weights(2).tolist() == [1.0, 1.0]
    assert loudness.channel_weights(3).tolist() == [1.0, 1.0, 1.0]
    assert loudness.channel_weights(4).tolist() == [1.0, 1.0, 1.0, 1.41]
    assert loudness.channel_weights(5).tolist() == [1.0, 1.0, 1.0, 1.41, 1.41]
    assert loudness.channel_weights(6).tolist() == [1.0, 1.0, 1.0, 0.0, 1.41, 1.41]
    assert loudness.channel_weights(7).tolist() == [
        1.0, 1.0, 1.0, 0.0, 1.41, 1.41, 1.41]
    assert loudness.channel_weights(8).tolist() == [
        1.0, 1.0, 1.0, 0.0, 1.41, 1.41, 1.41, 1.41]


def test_the_five_channel_programme_reads_minus_23_lufs():
    """EBU Tech 3341, test 6: a five channel programme reads -23.0 LUFS.

    The signal carries -28 dBFS in left and right, -24 dBFS in the centre
    and -30 dBFS in the two surrounds; the published answer of -23.0 LUFS
    only holds with the channel weights of Table 3.
    """
    x = np.concatenate([
        tone(20.0, peak_dbfs=-28.0),
        tone(20.0, peak_dbfs=-28.0),
        tone(20.0, peak_dbfs=-24.0),
        tone(20.0, peak_dbfs=-30.0),
        tone(20.0, peak_dbfs=-30.0),
    ], axis=1)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)


# ---------------------------------------------------------------------------
# the true peak
# ---------------------------------------------------------------------------
def test_the_true_peak_of_a_full_scale_signal_is_zero():
    """A signal that is full scale in the samples has a true peak of 0 dBTP.

    The reconstruction of an alternating full scale signal is a cosine of
    amplitude 1.0, so the true peak is 0 dBTP.  The peak of the file must
    not be read from the interpolation at the two ends of the buffer, where
    the filter sees the silence that lies outside the file.
    """
    x = np.sign(np.cos(math.pi * np.arange(SAMPLE_RATE))).astype(np.float64)
    assert loudness.true_peak(x[:, None], SAMPLE_RATE) == pytest.approx(
        0.0, abs=0.1)


@pytest.mark.parametrize(
    "freq, amplitude, phase, expected",
    [
        (12_000.0, 0.50, 0.0, -6.0),
        (12_000.0, 0.50, 45.0, -6.0),
        (8_000.0, 0.50, 60.0, -6.0),
        (6_000.0, 0.50, 67.5, -6.0),
        (12_000.0, 1.41, 45.0, 3.0),
    ],
)
def test_the_true_peak_of_the_published_test_signals(freq, amplitude, phase, expected):
    """EBU Tech 3341, test 15 to 19: the true peak with its published tolerance.

    The tolerance of the published measure is +0.2 / -0.4 dBTP; the test
    uses the wider of the two.
    """
    x = fade(tone(2.0, peak_dbfs=20.0 * math.log10(amplitude), freq=freq,
                  phase=math.radians(phase), channels=2))
    measured = loudness.true_peak(x, SAMPLE_RATE)
    assert measured <= expected + 0.2 + 1e-9
    assert measured >= expected - 0.4 - 1e-9


def test_the_true_peak_sees_a_peak_between_two_samples():
    """A tone whose samples are below full scale still has its true peak.

    A 12 kHz sine at 45 degrees has a peak of -9.03 dBFS in the samples and
    a true peak of -6.02 dBTP, because the waveform reaches its maximum
    between two samples.
    """
    x = fade(tone(2.0, peak_dbfs=20.0 * math.log10(0.5), freq=12_000.0,
                  phase=math.radians(45.0)))
    sample_peak = 20.0 * math.log10(float(np.max(np.abs(x))))
    assert sample_peak == pytest.approx(-9.03, abs=0.05)
    assert loudness.true_peak(x, SAMPLE_RATE) == pytest.approx(-6.0, abs=0.2)


# ---------------------------------------------------------------------------
# the loudness range
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "parts, expected",
    [
        (((10.0, -20.0, 1000.0), (10.0, -30.0, 1000.0)), 10.0),
        (((10.0, -20.0, 1000.0), (10.0, -15.0, 1000.0)), 5.0),
        (((10.0, -40.0, 1000.0), (10.0, -20.0, 1000.0)), 20.0),
    ],
)
def test_the_loudness_range_of_the_published_test_signals(parts, expected):
    """EBU Tech 3342, test 1 to 3: the loudness range with its tolerance."""
    x = segments(*parts, channels=2)
    lra, low, high = loudness.loudness_range(x, SAMPLE_RATE)
    assert lra == pytest.approx(expected, abs=1.0)
    assert low <= high


def test_the_loudness_range_gate_excludes_the_quiet_passage():
    """EBU Tech 3342, test 4: the quiet passages are gated out.

    The relative gate of the loudness range sits 20 LU below the absolute
    gated loudness level.  A gate that used the arithmetic mean of the
    loudness values instead would let the -50 dBFS passages through and
    report a range of 30 LU instead of 15 LU.
    """
    x = segments((20.0, -50.0, 1000.0), (20.0, -35.0, 1000.0),
                 (20.0, -20.0, 1000.0), (20.0, -35.0, 1000.0),
                 (20.0, -50.0, 1000.0), channels=2)
    lra, low, high = loudness.loudness_range(x, SAMPLE_RATE)
    assert lra == pytest.approx(15.0, abs=1.0)
    assert low == pytest.approx(-35.0, abs=0.2)
    assert high == pytest.approx(-20.0, abs=0.2)


def test_the_loudness_range_of_a_steady_programme_is_zero():
    """A programme of one level has a loudness range of zero."""
    x = tone(20.0, peak_dbfs=-23.0, channels=2)
    lra, low, high = loudness.loudness_range(x, SAMPLE_RATE)
    assert lra == pytest.approx(0.0, abs=0.1)
    assert low == pytest.approx(high, abs=0.1)


# ---------------------------------------------------------------------------
# the edges
# ---------------------------------------------------------------------------
def test_silence_has_no_loudness():
    """A silent file has no loudness at all."""
    x = np.zeros((SAMPLE_RATE, 2))
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == float("-inf")
    assert loudness.true_peak(x, SAMPLE_RATE) == float("-inf")


def test_a_short_item_is_measured_with_a_filled_block():
    """An item shorter than the 400 ms block is measured with silence.

    The block is filled up with silence, so the reading falls by the
    fraction of the block the item fills.  The batch runner applies the
    gain that follows from this reading, so a short item is delivered
    quieter than it was measured.
    """
    for seconds, expected in ((0.1, -29.02), (0.2, -26.01), (0.3, -24.25)):
        x = tone(seconds, peak_dbfs=-23.0, channels=2)
        assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
            expected, abs=0.05)


def test_an_item_of_one_gating_block_is_measured_on_its_level():
    """From 400 ms on, the reading is the level of the item."""
    x = tone(0.4, peak_dbfs=-23.0, channels=2)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        -23.0, abs=0.1)


def test_a_constant_carries_no_programme_loudness():
    """The high pass of the weighting removes a constant.

    Only the start-up transient of the filter is left, so the reading
    stays more than 20 dB below the reading of a full scale tone.
    """
    x = np.full((SAMPLE_RATE, 2), 0.5)
    tone_reading = loudness.integrated_loudness(tone(20.0, peak_dbfs=0.0), SAMPLE_RATE)
    assert loudness.integrated_loudness(x, SAMPLE_RATE) < tone_reading - 20.0


def test_an_impulse_carries_its_energy_in_the_block():
    """The energy of an impulse is spread over the gating block.

    The expected value comes from the energy of the K-weighting filter in
    the frequency domain, which is the other way round from the meter:
    the meter filters and then squares, the reference integrates the
    squared magnitude response.
    """
    x = np.zeros((SAMPLE_RATE, 1))
    x[SAMPLE_RATE // 2, 0] = 1.0
    n = 1 << 16
    k = np.arange(n)
    z = np.exp(-2j * math.pi * k / n)
    magnitude = np.ones(n)
    coeffs = loudness.k_weighting_coefficients(SAMPLE_RATE)
    for name in ("shelf", "rlb"):
        b, a = coeffs[name]
        num = b[0] + b[1] * z + b[2] * z ** 2
        den = 1.0 + a[1] * z + a[2] * z ** 2
        magnitude *= np.abs(num / den)
    energy = float(np.mean(magnitude ** 2))
    expected = -0.691 + 10.0 * math.log10(energy / (0.4 * SAMPLE_RATE))
    assert loudness.integrated_loudness(x, SAMPLE_RATE) == pytest.approx(
        expected, abs=0.01)


# ---------------------------------------------------------------------------
# the combined measurement
# ---------------------------------------------------------------------------
def test_the_measurements_carry_every_figure():
    """The combined measurement reports the figures the interface shows."""
    x = tone(20.0, peak_dbfs=-23.0, channels=2)
    figures = loudness.loudness_measurements(x, SAMPLE_RATE)
    assert figures.integrated_lufs == pytest.approx(-23.0, abs=0.1)
    assert figures.momentary_lufs == pytest.approx(-23.0, abs=0.1)
    assert figures.short_term_lufs == pytest.approx(-23.0, abs=0.1)
    assert figures.loudness_range_lu == pytest.approx(0.0, abs=0.1)
    assert figures.true_peak_dbtp == pytest.approx(-23.0, abs=0.05)
    assert figures.sample_peak_dbfs == pytest.approx(-23.0, abs=0.05)
    assert figures.channels == 2
    assert figures.sample_rate == SAMPLE_RATE
    assert figures.samples == x.shape[0]
    record = figures.to_dict()
    for key in ("integrated_lufs", "momentary_lufs", "short_term_lufs",
                "loudness_range_lu", "true_peak_dbtp", "sample_peak_dbfs"):
        assert key in record
        assert isinstance(record[key], float)
