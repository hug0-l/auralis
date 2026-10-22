"""
tests/test_analysis_payload.py — the contract of the analysis payload.

``POST /api/analyze`` answers with the payload the composite view draws.
The interface turns a row index into a frequency by multiplying it with
``bin_hz`` (``aurui/js/view.js`` maps the canvas onto the rows linearly,
``qa/journey/journey.py`` checks the same arithmetic), so the payload is a
contract: when the axis does not match the matrix, the whole spectrogram
is drawn at the wrong height.

The properties checked here are the ones a consumer relies on:

* a tone at a known frequency is reported at that frequency, and it falls
  inside the band the payload names for its row;
* the rows tile the range from 0 Hz to the Nyquist frequency exactly, so
  the row index and the frequency axis can never disagree;
* every row carries its own band — no row is a copy of another, and the
  matrix is not padded;
* a row holds the loudest level inside its own band;
* the waveform envelope has one min/max pair per bucket, and its peak is
  the peak of the signal.

Every number in this file is measured from the payload itself; nothing is
taken on trust.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from aurserver import audio as au

SR = 48_000

#: The programme tone the payload has to report at its real frequency.
TONE_HZ = 1000.0

#: The seed of the noise generator, so a failure is always reproducible.
SEED = 20_260_121


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def tone(freq: float, seconds: float = 2.0, amplitude: float = 0.5, sr: int = SR):
    """A mono sine of ``amplitude`` (``1.0`` = 0 dBFS)."""
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / float(sr)
    return (amplitude * np.sin(2.0 * math.pi * freq * t))[:, None]


def programme(seconds: float = 2.0, sr: int = SR):
    """A tone, mains hum and a little broadband noise.

    The noise matters: it gives every band its own level, so two rows can
    only be identical when the payload repeats a band.
    """
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / float(sr)
    rng = np.random.default_rng(SEED)
    signal = (
        0.25 * np.sin(2.0 * math.pi * TONE_HZ * t)
        + 0.05 * np.sin(2.0 * math.pi * 50.0 * t)
        + 0.002 * rng.standard_normal(n)
    )
    return np.stack([signal, 0.98 * signal], axis=1)


def matrix_of(payload: dict) -> np.ndarray:
    """The spectrogram of a payload as a ``uint8`` matrix."""
    return np.asarray(payload["spectrogram"], dtype=np.uint8)


def peak_row(payload: dict) -> int:
    """The row that carries the most energy."""
    matrix = matrix_of(payload)
    return int(np.argmax(matrix.mean(axis=1)))


def tail_run(payload: dict) -> int:
    """How many of the bottom rows are identical to the last one."""
    matrix = matrix_of(payload)
    same = np.all(matrix == matrix[-1], axis=1)
    run = 0
    for flag in same[::-1]:
        if not flag:
            break
        run += 1
    return run


def reference_bands(samples: np.ndarray, payload: dict) -> np.ndarray:
    """The band maximum of the signal, computed from the payload's own axis.

    The bands are built one FFT bin at a time — the bin goes into the band
    that contains its frequency — which is a different route to the same
    number than the one the server takes.
    """
    sr = int(payload["sample_rate"])
    n_fft = int(payload["fft_size"])
    hop = int(payload["frame_hop_samples"])
    min_db = float(payload["min_db"])
    max_db = float(payload["max_db"])
    bin_hz = float(payload["bin_hz"])
    n_rows = int(payload["freq_bins"])

    mono = np.asarray(samples, dtype=np.float64)
    mono = mono.mean(axis=1) if mono.ndim > 1 else mono
    window = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
    spectrum = np.fft.rfft(frames * window, axis=1)
    magnitude = np.abs(spectrum) / max(window.sum() / 2.0, 1e-9)
    decibels = 20.0 * np.log10(np.maximum(magnitude, 1e-12))
    decibels = np.clip(decibels, min_db, max_db).T  # -> (bins, frames)

    out = np.full((n_rows, decibels.shape[1]), min_db, dtype=np.float64)
    for index in range(decibels.shape[0]):
        frequency = index * sr / float(n_fft)
        row = min(int(frequency / bin_hz), n_rows - 1)
        out[row] = np.maximum(out[row], decibels[index])
    return out


# ---------------------------------------------------------------------------
# the frequency axis
# ---------------------------------------------------------------------------
def test_a_tone_is_reported_at_the_frequency_it_really_has():
    """A 1 kHz tone is reported at 1 kHz, not at a third of it."""
    payload = au.analyse(tone(TONE_HZ), SR)

    bin_hz = float(payload["bin_hz"])
    reported = peak_row(payload) * bin_hz

    assert abs(reported - TONE_HZ) <= bin_hz, (
        f"the tone is reported at {reported:.2f} Hz, which is "
        f"{abs(reported - TONE_HZ):.2f} Hz away from {TONE_HZ:.0f} Hz "
        f"(the row spacing is {bin_hz} Hz)"
    )


def test_the_tone_falls_inside_the_band_the_payload_names():
    """The row that holds the tone names the band the tone is really in."""
    payload = au.analyse(tone(TONE_HZ), SR)

    bin_hz = float(payload["bin_hz"])
    row = peak_row(payload)
    low = row * bin_hz
    high = (row + 1) * bin_hz

    assert low <= TONE_HZ <= high, (
        f"the tone is in the band [{low:.2f}, {high:.2f}) Hz, but the "
        f"payload reports the band of its loudest row as that one"
    )


def test_the_rows_tile_the_range_up_to_the_nyquist_frequency():
    """The row index and the frequency axis agree over the whole range."""
    payload = au.analyse(tone(TONE_HZ), SR)

    assert payload["freq_min_hz"] == 0.0
    assert payload["freq_max_hz"] == pytest.approx(SR / 2.0)
    assert payload["bin_hz"] * payload["freq_bins"] == pytest.approx(
        payload["freq_max_hz"]
    )
    assert payload["bin_hz"] > 0.0


def test_the_composite_view_draws_the_tone_on_its_own_row():
    """The mapping of the interface lands on the row of the tone.

    ``aurui/js/view.js`` turns a frequency into a row index with
    ``(f / nyquist) * rows - 0.5``; the row it picks has to be the row
    that carries the tone.
    """
    payload = au.analyse(tone(TONE_HZ), SR)

    rows = int(payload["freq_bins"])
    nyquist = float(payload["freq_max_hz"])
    position = (TONE_HZ / nyquist) * rows - 0.5
    drawn = int(round(position))

    assert abs(drawn - peak_row(payload)) <= 1, (
        f"the interface draws {TONE_HZ:.0f} Hz on row {drawn}, but the "
        f"loudest row is {peak_row(payload)}"
    )


# ---------------------------------------------------------------------------
# every row carries its own band
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sample_rate", [8_000, 22_050, 44_100, 48_000, 96_000])
@pytest.mark.parametrize("max_bins", [1, 3, 100, 512, 1024])
def test_the_axis_holds_for_every_shape(sample_rate: int, max_bins: int) -> None:
    """The rows tile the Nyquist range whatever the shape of the matrix is."""
    payload = au.analyse(
        tone(TONE_HZ, 0.5, sr=sample_rate), sample_rate, max_bins=max_bins
    )

    assert payload["freq_bins"] == len(payload["spectrogram"])
    assert payload["freq_max_hz"] == pytest.approx(sample_rate / 2.0)
    assert payload["bin_hz"] * payload["freq_bins"] == pytest.approx(
        payload["freq_max_hz"]
    )


def test_no_row_is_a_copy_of_another():
    """Two rows only look alike when their bands really are alike."""
    payload = au.analyse(programme(), SR)

    matrix = matrix_of(payload)
    unique = {row.tobytes() for row in matrix}

    assert len(unique) == matrix.shape[0], (
        f"{matrix.shape[0] - len(unique)} of the {matrix.shape[0]} rows are "
        "copies of another row"
    )


def test_the_bottom_of_the_matrix_is_not_a_padded_band():
    """The matrix is not padded: the bottom rows are real bands."""
    payload = au.analyse(programme(), SR)

    run = tail_run(payload)

    assert run <= 2, (
        f"{run} of the {payload['freq_bins']} bottom rows are identical to "
        "the last one — the matrix is padded"
    )


def test_a_row_holds_the_loudest_level_of_its_own_band():
    """The content of a row is the maximum over the bins of its band."""
    samples = programme()
    payload = au.analyse(samples, SR)

    expected = reference_bands(samples, payload)
    min_db = float(payload["min_db"])
    max_db = float(payload["max_db"])
    quantised = np.clip(
        np.round((expected - min_db) * (255.0 / (max_db - min_db))), 0, 255
    ).astype(np.uint8)

    assert np.array_equal(matrix_of(payload), quantised), (
        "the rows do not hold the maximum of the band they name"
    )


def test_the_payload_describes_its_own_matrix():
    """``freq_bins`` and ``frames`` are the shape of the matrix."""
    payload = au.analyse(programme(), SR)

    matrix = payload["spectrogram"]
    assert payload["freq_bins"] == len(matrix)
    assert payload["frames"] == len(matrix[0])
    assert all(len(row) == payload["frames"] for row in matrix)
    assert all(0 <= value <= 255 for row in matrix for value in row)


def test_the_payload_is_json_safe():
    """The payload survives a JSON round trip without a NaN or an infinity."""
    payload = au.analyse(programme(), SR)

    text = json.dumps(payload, allow_nan=False)
    assert "Infinity" not in text and "NaN" not in text
    assert json.loads(text)["freq_bins"] == payload["freq_bins"]


# ---------------------------------------------------------------------------
# the waveform envelope
# ---------------------------------------------------------------------------
def test_the_envelope_has_one_min_max_pair_per_bucket():
    """The envelope carries 2000 buckets, each with a minimum and a maximum."""
    samples = programme()
    payload = au.analyse(samples, SR)

    envelope = payload["waveform"]

    assert payload["waveform_buckets"] == 2000
    assert len(envelope) == 2000
    for low, high in envelope:
        assert low <= high, f"a bucket has min {low} > max {high}"


def test_the_envelope_peak_is_the_peak_of_the_signal():
    """The envelope does not lose the peak of the signal."""
    samples = programme()
    payload = au.analyse(samples, SR)

    peak = max(abs(value) for pair in payload["waveform"] for value in pair)

    assert peak == pytest.approx(float(np.max(np.abs(samples))), abs=1e-6)


def test_the_envelope_covers_every_sample():
    """The buckets of the envelope add up to the whole signal.

    The payload rounds the envelope to six decimals, so the extremes are
    only good to that resolution.
    """
    samples = programme()
    payload = au.analyse(samples, SR)

    envelope = payload["waveform"]
    assert min(pair[0] for pair in envelope) <= float(samples.min()) + 1e-6
    assert max(pair[1] for pair in envelope) >= float(samples.max()) - 1e-6


# ---------------------------------------------------------------------------
# the defect this file guards against
# ---------------------------------------------------------------------------
def test_the_padded_grouping_would_report_the_tone_too_low():
    """The old grouping (three bins per row) fails the axis contract.

    The defect this file guards against padded the matrix to a multiple of
    the row count and took the maximum of every group.  The rows then held
    three FFT bins while the payload claimed one, and the whole spectrogram
    was drawn a third of an octave too low.  The test below rebuilds that
    matrix and shows that the payload contract is broken by it — so the
    assertions above really do catch the defect.
    """
    samples = programme()
    payload = au.analyse(samples, SR)
    n_fft = int(payload["fft_size"])
    hop = int(payload["frame_hop_samples"])
    min_db = float(payload["min_db"])
    max_db = float(payload["max_db"])

    mono = samples.mean(axis=1)
    window = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
    spectrum = np.fft.rfft(frames * window, axis=1)
    magnitude = np.abs(spectrum) / max(window.sum() / 2.0, 1e-9)
    decibels = 20.0 * np.log10(np.maximum(magnitude, 1e-12))
    decibels = np.clip(decibels, min_db, max_db).T

    n_bins = int(decibels.shape[0])
    max_bins = int(payload["freq_bins"])
    group = int(math.ceil(n_bins / float(max_bins)))
    padded = int(max_bins * group)
    if padded > n_bins:
        decibels = np.pad(decibels, ((0, padded - n_bins), (0, 0)), mode="edge")
    old = decibels.reshape(max_bins, group, decibels.shape[1]).max(axis=1)

    # the old payload reported the row spacing as nyquist / rows
    old_bin_hz = (SR / 2.0) / max_bins
    old_reported = int(np.argmax(old.mean(axis=1))) * old_bin_hz

    assert abs(old_reported - TONE_HZ) > old_bin_hz, (
        "the padded grouping does not reproduce the defect any more"
    )
    # and the padded matrix repeats its last row, which is the other half
    # of the defect
    same = np.all(old == old[-1], axis=1)
    run = 0
    for flag in same[::-1]:
        if not flag:
            break
        run += 1
    assert run > 2, "the padded grouping does not repeat its bottom rows any more"
