"""
Numeric tests for the measurement service behind the Analysis tab
(``aurcore.analysis``).

Every test states the numeric property it checks and the tolerance it uses.

Conventions used here
---------------------
* a *stereo* signal carries the same tone in both channels; the BS.1770-4
  channel sum then makes the loudness of a sine equal to its dBFS level
  (the reference: a 1 kHz sine at -23 dBFS in both channels reads
  -23 LUFS).  A **mono** sine reads 3 LU below its dBFS level, because the
  energy of one channel is summed without the second channel — this is
  checked explicitly so the convention cannot drift unnoticed;
* levels are compared in dB with the tolerance written next to the
  assertion, linear amplitudes with an absolute tolerance;
* no test touches the network or the file system.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from aurcore.analysis import (
    Analysis,
    analyze,
    inter_channel_correlation,
    magnitude_spectrum,
    peak_histogram,
    selection_bounds,
    waveform_statistics,
)

SR = 48_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def tone(freq: float, seconds: float, amplitude: float, sr: int = SR) -> np.ndarray:
    """A mono sine of ``amplitude`` (``1.0`` = 0 dBFS)."""
    t = np.arange(int(round(seconds * sr)), dtype=np.float64) / float(sr)
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def stereo(mono: np.ndarray) -> np.ndarray:
    """The same signal in both channels (the BS.1770-4 reference case)."""
    return np.repeat(np.asarray(mono, dtype=np.float64)[:, None], 2, axis=1)


def service(sr: int = SR, channels: int = 2) -> Analysis:
    """A measurement service for the tests."""
    return Analysis(sample_rate=sr, channels=channels)


# ---------------------------------------------------------------------------
# loudness
# ---------------------------------------------------------------------------
def test_minus20_dbfs_tone_reads_minus20_lufs():
    """A -20 dBFS 1 kHz tone reads -20 LUFS integrated (tolerance 0.3 LU)."""
    signal = stereo(tone(1000.0, 5.0, 10.0 ** (-20.0 / 20.0)))

    result = service().measure(signal)

    integrated = result["loudness"]["integrated_lufs"]
    assert abs(integrated - (-20.0)) <= 0.3, f"integrated loudness {integrated} LUFS"


def test_minus20_dbfs_tone_momentary_and_short_term_match_integrated():
    """A steady tone: momentary, short term and integrated agree (0.3 LU)."""
    signal = stereo(tone(1000.0, 5.0, 10.0 ** (-20.0 / 20.0)))

    loudness = service().measure(signal)["loudness"]

    assert abs(loudness["momentary_lufs"] - (-20.0)) <= 0.3
    assert abs(loudness["short_term_lufs"] - (-20.0)) <= 0.3
    assert loudness["loudness_range_lu"] <= 0.3


def test_mono_tone_reads_three_lu_below_its_dbfs_level():
    """A mono sine reads 3 LU below its dBFS level (the channel sum)."""
    signal = tone(1000.0, 5.0, 10.0 ** (-20.0 / 20.0))

    integrated = service(channels=1).measure(signal)["loudness"]["integrated_lufs"]

    assert abs(integrated - (-23.0)) <= 0.3, f"integrated loudness {integrated} LUFS"


def test_true_peak_of_a_clipped_signal_is_above_the_sample_peak():
    """Hard clipping produces inter-sample peaks above the sample peak."""
    clipped = np.clip(4.0 * tone(1000.0, 2.0, 1.0), -1.0, 1.0)

    stats = service().measure(stereo(clipped))["waveform_stats"]

    assert stats["sample_peak_db"] == pytest.approx(0.0, abs=1e-6)
    assert stats["true_peak_db"] > stats["sample_peak_db"], (
        f"true peak {stats['true_peak_db']} dBTP is not above the sample peak "
        f"{stats['sample_peak_db']} dBFS"
    )
    assert stats["true_peak_db"] - stats["sample_peak_db"] > 0.1


def test_true_peak_of_a_clean_tone_equals_the_sample_peak():
    """A tone whose peaks land on a sample has no inter-sample overshoot."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    stats = service().measure(signal)["waveform_stats"]

    assert stats["true_peak_db"] == pytest.approx(stats["sample_peak_db"], abs=0.01)


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------
def test_correlation_of_identical_channels_is_one():
    """Two identical channels are perfectly correlated."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    assert inter_channel_correlation(signal) == pytest.approx(1.0, abs=1e-9)


def test_correlation_of_inverted_channels_is_minus_one():
    """Two channels with opposite polarity are perfectly anti-correlated."""
    signal = stereo(tone(1000.0, 1.0, 0.5))
    inverted = signal.copy()
    inverted[:, 1] *= -1.0

    assert inter_channel_correlation(inverted) == pytest.approx(-1.0, abs=1e-9)


def test_correlation_of_uncorrelated_noise_is_about_zero():
    """Independent noise in the two channels is uncorrelated (|r| < 0.05)."""
    rng = np.random.default_rng(20_260_101)
    noise = rng.standard_normal((SR, 2))

    value = inter_channel_correlation(noise)

    assert abs(value) < 0.05, f"correlation {value}"


def test_correlation_of_a_mono_signal_is_one():
    """A mono signal is perfectly correlated with itself."""
    assert inter_channel_correlation(tone(1000.0, 0.5, 0.5)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# waveform statistics
# ---------------------------------------------------------------------------
def test_statistics_of_a_known_signal():
    """DC offset, RMS, peaks and mean of a known signal are correct."""
    t = np.arange(SR, dtype=np.float64) / float(SR)
    signal = (0.25 + 0.5 * np.sin(2.0 * np.pi * 1000.0 * t))[:, None]

    stats = waveform_statistics(signal)

    assert stats["dc_offset"] == pytest.approx(0.25, abs=1e-9)
    assert stats["mean"] == pytest.approx(0.25, abs=1e-9)
    assert stats["max"] == pytest.approx(0.75, abs=1e-9)
    assert stats["min"] == pytest.approx(-0.25, abs=1e-9)
    assert stats["sample_peak"] == pytest.approx(0.75, abs=1e-9)
    # RMS of 0.25 + 0.5*sin -> sqrt(0.25**2 + 0.5**2/2)
    assert stats["rms"] == pytest.approx(math.sqrt(0.0625 + 0.125), abs=1e-9)
    assert stats["rms_db"] == pytest.approx(20.0 * math.log10(math.sqrt(0.1875)), abs=1e-9)
    # a mono signal has no second channel to add to the total RMS
    assert stats["total_rms"] == pytest.approx(stats["rms"], abs=1e-9)


def test_total_rms_sums_the_channels():
    """The total RMS of two identical channels is the mono RMS times sqrt(2)."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    stats = waveform_statistics(signal)

    assert stats["rms"] == pytest.approx(0.5 / math.sqrt(2.0), abs=1e-9)
    assert stats["total_rms"] == pytest.approx(stats["rms"] * math.sqrt(2.0), abs=1e-9)


def test_possibly_clipped_samples_are_counted():
    """Samples at full scale are counted as possibly clipped."""
    signal = np.concatenate(
        [
            np.full(100, 0.5, dtype=np.float64),
            np.full(7, 1.0, dtype=np.float64),
            np.full(100, -0.5, dtype=np.float64),
        ]
    )[:, None]

    stats = waveform_statistics(signal, clip_threshold=0.999)

    assert stats["possibly_clipped"] == 7
    assert stats["sample_peak"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def test_selection_bounds_the_measurement():
    """A selection only measures the samples it covers."""
    t = np.arange(3 * SR, dtype=np.float64) / float(SR)
    amplitude = np.where(t < 1.0, 0.01, 0.5)
    signal = stereo(amplitude * np.sin(2.0 * np.pi * 1000.0 * t))
    analyser = service()

    quiet = analyser.measure(signal, 0.0, 1.0)
    loud = analyser.measure(signal, 1.0, 3.0)
    whole = analyser.measure(signal)

    assert quiet["loudness"]["integrated_lufs"] == pytest.approx(-40.0, abs=0.3)
    assert loud["loudness"]["integrated_lufs"] == pytest.approx(-6.0, abs=0.3)
    assert quiet["waveform_stats"]["rms"] == pytest.approx(0.01 / math.sqrt(2.0), abs=1e-6)
    assert loud["waveform_stats"]["rms"] == pytest.approx(0.5 / math.sqrt(2.0), abs=1e-6)
    # the whole signal is louder than the quiet part and quieter than the
    # loud part
    assert whole["loudness"]["integrated_lufs"] > quiet["loudness"]["integrated_lufs"]
    assert whole["loudness"]["integrated_lufs"] < loud["loudness"]["integrated_lufs"]
    assert quiet["selection"]["samples"] == SR
    assert loud["selection"]["samples"] == 2 * SR
    assert whole["selection"]["samples"] == 3 * SR
    assert whole["selection"]["bounded"] is False
    assert quiet["selection"]["bounded"] is True


def test_selection_bounds_are_clamped_to_the_signal():
    """A selection beyond the signal is clamped, not an error."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    result = service().measure(signal, -5.0, 99.0)

    assert result["selection"]["samples"] == SR
    assert result["selection"]["start"] == pytest.approx(0.0)
    assert result["selection"]["end"] == pytest.approx(1.0)


def test_selection_bounds_helper():
    """The sample range of a selection is derived from the sample rate."""
    assert selection_bounds(96000, SR, 0.5, 1.5) == (24000, 72000)
    assert selection_bounds(48000, SR, None, None) == (0, 48000)
    assert selection_bounds(96000, SR, 1.5, 0.5) == (24000, 72000)
    assert selection_bounds(48000, SR, 2.0, 1.0) == (48000, 48000)
    # out of range bounds are clamped to the signal
    assert selection_bounds(48000, SR, 0.5, 99.0) == (24000, 48000)


# ---------------------------------------------------------------------------
# spectrum and histogram
# ---------------------------------------------------------------------------
def test_spectrum_finds_the_tone():
    """The magnitude spectrum peaks at the frequency of the tone."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    spectrum = magnitude_spectrum(signal, SR)

    assert spectrum["peak_hz"] == pytest.approx(1000.0, abs=SR / 4096.0)
    assert spectrum["peak_db"] == pytest.approx(-6.02, abs=1.5)
    assert len(spectrum["frequencies"]) == len(spectrum["magnitudes_db"])
    assert spectrum["frequencies"][0] == pytest.approx(0.0)
    assert spectrum["frequencies"][-1] == pytest.approx(SR / 2.0)


def test_histogram_marks_the_clipping_region():
    """The peak histogram reports the bins that hold clipped samples."""
    signal = np.concatenate(
        [
            np.full(50, 0.5, dtype=np.float64),
            np.full(10, 1.0, dtype=np.float64),
            np.full(50, -0.5, dtype=np.float64),
        ]
    )[:, None]

    histogram = peak_histogram(signal, bins=200, clip_threshold=0.999)

    assert len(histogram["counts"]) == 200
    assert len(histogram["bin_edges"]) == 201
    assert sum(histogram["counts"]) == 110
    assert histogram["clip_bin_start"] == 199
    assert histogram["clip_bin_end"] == 200
    assert histogram["counts"][199] == 10
    assert histogram["out_of_range"] == 0
    # the mirror of the clipping bins holds the negative clipping samples
    assert histogram["counts"][0] == 0


def test_histogram_counts_samples_beyond_full_scale():
    """Samples beyond full scale are counted in the outermost bin."""
    signal = np.array([[1.5], [-1.5]], dtype=np.float64)

    histogram = peak_histogram(signal, bins=200)

    assert histogram["out_of_range"] == 2
    assert histogram["counts"][199] == 1
    assert histogram["counts"][0] == 1


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------
def test_measure_returns_the_documented_sections():
    """The payload carries every section the Analysis tab renders."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    result = service().measure(signal)

    for key in ("spectrum", "waveform_stats", "loudness", "correlation", "histogram"):
        assert key in result, f"missing section {key}"
    assert result["sample_rate"] == SR
    assert result["channels"] == 2
    assert result["frames"] == SR
    assert result["duration"] == pytest.approx(1.0)
    for key in (
        "integrated_lufs",
        "short_term_lufs",
        "momentary_lufs",
        "loudness_range_lu",
        "true_peak_dbtp",
    ):
        assert key in result["loudness"], f"missing loudness figure {key}"


def test_payload_is_json_safe():
    """The payload contains no infinity and no NaN, so it survives JSON."""
    signal = stereo(tone(1000.0, 1.0, 0.5))
    silence = np.zeros((100, 2), dtype=np.float64)

    for data in (signal, silence):
        payload = service().measure(data)
        text = json.dumps(payload, allow_nan=False)
        assert "Infinity" not in text and "NaN" not in text
        assert json.loads(text)["sample_rate"] == SR


def test_analyze_shortcut_matches_the_service():
    """The one-call shortcut measures the same as the service."""
    signal = stereo(tone(1000.0, 1.0, 0.5))

    shortcut = analyze(signal, SR)
    direct = service().measure(signal)

    assert shortcut["loudness"]["integrated_lufs"] == pytest.approx(
        direct["loudness"]["integrated_lufs"]
    )
    assert shortcut["waveform_stats"]["rms"] == pytest.approx(direct["waveform_stats"]["rms"])


def test_empty_signal_is_measured_without_an_error():
    """An empty selection produces a well formed, finite payload."""
    result = service().measure(np.zeros((0, 2), dtype=np.float64))

    assert result["waveform_stats"]["samples"] == 0
    assert result["waveform_stats"]["possibly_clipped"] == 0
    assert result["spectrum"]["magnitudes_db"] == []
    assert result["loudness"]["integrated_lufs"] == pytest.approx(-120.0)
    assert math.isfinite(result["correlation"])
    json.dumps(result, allow_nan=False)


def test_mono_and_stereo_give_the_same_histogram_and_stats():
    """The histogram counts every sample of every channel."""
    mono = tone(1000.0, 1.0, 0.5)

    single = service(channels=1).measure(mono)
    pair = service(channels=2).measure(stereo(mono))

    assert sum(single["histogram"]["counts"]) == len(mono)
    assert sum(pair["histogram"]["counts"]) == 2 * len(mono)
    assert pair["waveform_stats"]["sample_peak"] == pytest.approx(
        single["waveform_stats"]["sample_peak"]
    )
