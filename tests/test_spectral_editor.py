"""
tests/test_spectral_editor.py — the non destructive Spectral Editor.

The Spectral Editor edits a *region* — a rectangle in the time/frequency
plane — and leaves everything outside that region untouched.  These tests
check the three promises the module makes:

* the region survives a serialisation round trip (and the chain document),
* every mode changes the audio inside the region and nothing outside it,
* the length of the signal is preserved and the output stays finite, for mono
  and for stereo.

The measurement is a band energy: the energy of the signal inside the
frequency band of the region, measured over the time span of the region and
over a time span outside of it.
"""
from __future__ import annotations

import numpy as np
import pytest

from aurcore import registry
from aurcore.api import create_engine
from aurcore.modules.spectral_editor import SpectralEditor
from aurcore.serial import typed_value as tv

SAMPLE_RATE = 48_000
CODE = "AUR_SPECTRALEDITOR"
MODES = ("Attenuate", "Replace", "Gain", "Pattern", "Partials")


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def make_editor(mode: str = "Attenuate", strength: float = 1.0, channels: int = 2):
    """Return an editor with the given mode, strength and channel count."""
    editor = SpectralEditor(SAMPLE_RATE, channels)
    editor.params.set("Mode", mode)
    editor.params.set("Reduction", strength)
    return editor


def region_burst_signal(channels: int = 2, duration: float = 1.0, seed: int = 20260101):
    """A quiet noise bed with a loud 1 kHz burst between 0.30 s and 0.50 s."""
    rng = np.random.default_rng(seed)
    n = int(duration * SAMPLE_RATE)
    bed = rng.standard_normal(n) * 0.01
    burst = 0.4 * np.sin(2.0 * np.pi * 1000.0 * np.arange(9600) / SAMPLE_RATE)
    bed[14400:24000] += burst
    return np.repeat(bed[:, None], channels, axis=1)


def harmonic_burst_signal(channels: int = 2, duration: float = 1.0, seed: int = 20260101):
    """A quiet noise bed with a harmonic burst (200 Hz fundamental)."""
    rng = np.random.default_rng(seed)
    n = int(duration * SAMPLE_RATE)
    bed = rng.standard_normal(n) * 0.01
    burst = np.zeros(9600)
    for k in range(1, 9):
        burst += (0.4 / k) * np.sin(2.0 * np.pi * 200.0 * k * np.arange(9600) / SAMPLE_RATE)
    bed[14400:24000] += burst
    return np.repeat(bed[:, None], channels, axis=1)


def repeated_burst_signal(channels: int = 2, duration: float = 1.0, seed: int = 20260101):
    """A noise bed with the same 800 Hz burst twice: loud once, quiet once."""
    rng = np.random.default_rng(seed)
    n = int(duration * SAMPLE_RATE)
    bed = rng.standard_normal(n) * 0.01
    bed[14400:24000] += 0.4 * np.sin(2.0 * np.pi * 800.0 * np.arange(9600) / SAMPLE_RATE)
    bed[33600:43200] += 0.1 * np.sin(2.0 * np.pi * 800.0 * np.arange(9600) / SAMPLE_RATE)
    return np.repeat(bed[:, None], channels, axis=1)


def band_energy(x: np.ndarray, start_s: float, end_s: float, low_hz: float,
                high_hz: float) -> float:
    """Energy of ``x`` inside a time span and a frequency band."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    s0 = int(round(start_s * SAMPLE_RATE))
    s1 = int(round(end_s * SAMPLE_RATE))
    segment = arr[max(0, s0):min(arr.shape[0], s1), :]
    if segment.size == 0:
        return 0.0
    spectrum = np.abs(np.fft.rfft(segment, axis=0)) ** 2
    freqs = np.fft.rfftfreq(segment.shape[0], 1.0 / SAMPLE_RATE)
    band = (freqs >= low_hz) & (freqs <= high_hz)
    return float(np.sum(spectrum[band, :]))


def db(ratio: float) -> float:
    """A ratio in decibels."""
    return 10.0 * np.log10(max(ratio, 1e-30))


# ---------------------------------------------------------------------------
# registration and parameters
# ---------------------------------------------------------------------------
def test_the_module_is_registered():
    """The editor is part of the registry under its own code."""
    assert registry.has(CODE)
    assert registry.get(CODE).display_name == "Spectral Editor"
    assert registry.get(CODE).element == "SpectralEditor"
    assert registry.get(CODE).category == "Repair"
    assert registry.get(CODE).edition == "standard"


def test_the_parameters_are_the_documented_ones():
    """The module exposes exactly the documented parameters."""
    editor = SpectralEditor(SAMPLE_RATE, 2)
    descs = {d.name: d for d in editor.describe()}
    assert set(descs) == {
        "Mode",
        "Reduction",
        "Region Length",
        "Direction",
        "Multi Resolution",
        "Before Weighting",
        "After Weighting",
    }
    assert descs["Mode"].type == "enum"
    assert tuple(descs["Mode"].choices) == MODES
    assert descs["Reduction"].minimum == 0.0 and descs["Reduction"].maximum == 1.0
    assert descs["Region Length"].unit == "s"
    assert descs["Region Length"].minimum == 0.02
    assert descs["Region Length"].maximum == 10.0
    assert descs["Direction"].type == "enum"
    assert tuple(descs["Direction"].choices) == ("Horizontal", "Vertical", "2D")
    assert descs["Multi Resolution"].type == "bool"
    assert descs["Before Weighting"].maximum == 1.0
    assert descs["After Weighting"].maximum == 1.0


# ---------------------------------------------------------------------------
# the region state
# ---------------------------------------------------------------------------
def test_the_region_round_trips_through_serialize_and_deserialize():
    """The region survives a serialisation round trip."""
    editor = make_editor()
    assert editor.region is None
    editor.set_region(0.25, 0.75, 800.0, 6400.0)

    state = editor.serialize()
    assert state["region"]["start"] == pytest.approx(0.25)
    assert state["region"]["end"] == pytest.approx(0.75)
    assert state["region"]["low"] == pytest.approx(800.0)
    assert state["region"]["high"] == pytest.approx(6400.0)

    other = make_editor()
    other.deserialize(state)
    assert other.get_region() == editor.get_region()


def test_the_region_round_trips_through_the_chain_document():
    """The region is carried by the chain document, so a session keeps it."""
    engine = create_engine(SAMPLE_RATE, 2)
    handle = engine.add_module(CODE)
    engine.set_param(handle, "Mode", "Gain")
    engine.set_param(handle, "Reduction", 0.75)
    engine.set_param(handle, "Region Length", 0.4)
    handle.module.set_region(1.0, 2.0, 100.0, 500.0)

    document = engine.serialize()
    step = document["steps"][0]
    assert step["code"] == CODE
    assert tv.unwrap(step["params"]["Reduction"]) == pytest.approx(0.75)

    restored = create_engine(SAMPLE_RATE, 2)
    restored.deserialize(document)
    module = restored.chain[0].module
    region = module.get_region()
    assert region is not None
    assert region["start"] == pytest.approx(1.0)
    assert region["end"] == pytest.approx(2.0)
    assert region["low"] == pytest.approx(100.0)
    assert region["high"] == pytest.approx(500.0)
    assert module.params.get("Reduction") == pytest.approx(0.75)


def test_setting_the_region_updates_the_region_length_parameter():
    """The drawn selection and the Region Length parameter agree."""
    editor = make_editor()
    editor.set_region(0.25, 0.75, 100.0, 1000.0)
    assert editor.params.get("Region Length") == pytest.approx(0.5)
    editor.set_region(0.25, 0.75, 100.0, 1000.0)
    assert editor.params.get("Region Length") == pytest.approx(0.5)


def test_a_region_is_normalised():
    """A reversed or out of range region is corrected, never rejected."""
    editor = make_editor()
    editor.set_region(0.75, 0.25, 6400.0, 800.0)
    region = editor.get_region()
    assert region["start"] == pytest.approx(0.25)
    assert region["end"] == pytest.approx(0.75)
    assert region["low"] == pytest.approx(800.0)
    assert region["high"] == pytest.approx(6400.0)


# ---------------------------------------------------------------------------
# the modes
# ---------------------------------------------------------------------------
MODE_CASES = (
    ("Attenuate", region_burst_signal, (0.30, 0.50, 700.0, 1400.0), -3.0),
    ("Gain", region_burst_signal, (0.30, 0.50, 700.0, 1400.0), 3.0),
    ("Replace", region_burst_signal, (0.30, 0.50, 700.0, 1400.0), -3.0),
    ("Partials", harmonic_burst_signal, (0.30, 0.50, 100.0, 2000.0), -3.0),
    ("Pattern", repeated_burst_signal, (0.30, 0.50, 400.0, 1200.0), -3.0),
)


@pytest.mark.parametrize("mode,builder,region,expected_db", MODE_CASES)
def test_every_mode_changes_the_region_and_leaves_the_surroundings_alone(
    mode, builder, region, expected_db
):
    """The edit is visible inside the region and nowhere else."""
    editor = make_editor(mode=mode)
    editor.set_region(*region)
    signal = builder()
    edited = np.asarray(editor.process(signal))

    inside_before = band_energy(signal, *region)
    inside_after = band_energy(edited, *region)
    assert inside_before > 0.0
    change = db(inside_after / inside_before)
    assert change < expected_db if expected_db < 0 else change > expected_db, (
        f"{mode}: the band energy inside the region only changed by {change:.2f} dB"
    )

    # the surroundings are bit identical
    for start, end in ((0.02, 0.25), (0.60, 0.95)):
        a = signal[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        b = edited[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        assert np.array_equal(a, b), f"{mode}: the surroundings changed"


@pytest.mark.parametrize("mode,builder,region,expected_db", MODE_CASES)
def test_every_mode_keeps_the_length_and_stays_finite(mode, builder, region, expected_db):
    """The length of the signal is preserved and no sample turns non finite."""
    editor = make_editor(mode=mode)
    editor.set_region(*region)
    signal = builder()
    edited = np.asarray(editor.process(signal))
    assert edited.shape == signal.shape
    assert np.all(np.isfinite(edited))


def test_a_zero_strength_leaves_the_signal_alone():
    """Strength 0 does nothing at all."""
    editor = make_editor(mode="Attenuate", strength=0.0)
    editor.set_region(0.30, 0.50, 700.0, 1400.0)
    signal = region_burst_signal()
    edited = np.asarray(editor.process(signal))
    assert np.array_equal(edited, signal)


def test_without_a_region_the_signal_is_passed_through():
    """The module does nothing until a region is selected."""
    editor = make_editor(mode="Attenuate")
    signal = region_burst_signal()
    edited = np.asarray(editor.process(signal))
    assert np.array_equal(edited, signal)


def test_the_attenuation_is_monotonic_in_strength():
    """A stronger attenuation removes more energy."""
    signal = region_burst_signal()
    region = (0.30, 0.50, 700.0, 1400.0)
    drops = []
    for strength in (0.25, 0.5, 1.0):
        editor = make_editor(mode="Attenuate", strength=strength)
        editor.set_region(*region)
        edited = np.asarray(editor.process(signal))
        drops.append(
            db(band_energy(edited, *region) / band_energy(signal, *region))
        )
    assert drops[0] > drops[1] > drops[2]


# ---------------------------------------------------------------------------
# mono and stereo
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("channels", (1, 2))
def test_mono_and_stereo_both_work(channels):
    """The editor works on a mono and on a stereo signal."""
    editor = make_editor(mode="Attenuate")
    editor.set_region(0.30, 0.50, 700.0, 1400.0)
    signal = region_burst_signal(channels=channels)
    edited = np.asarray(editor.process(signal))
    assert edited.shape == (signal.shape[0], channels)
    assert np.all(np.isfinite(edited))
    assert band_energy(edited, 0.30, 0.50, 700.0, 1400.0) < band_energy(
        signal, 0.30, 0.50, 700.0, 1400.0
    )
    # the channels are treated independently
    assert not np.array_equal(edited[:, 0], signal[:, 0])


def test_the_engine_runs_the_editor():
    """The editor works inside a chain."""
    engine = create_engine(SAMPLE_RATE, 2)
    handle = engine.add_module(CODE)
    engine.set_param(handle, "Mode", "Attenuate")
    engine.set_param(handle, "Reduction", 1.0)
    handle.module.set_region(0.30, 0.50, 700.0, 1400.0)
    signal = region_burst_signal()
    out = np.asarray(engine.process(signal))
    assert out.shape == signal.shape
    assert band_energy(out, 0.30, 0.50, 700.0, 1400.0) < band_energy(
        signal, 0.30, 0.50, 700.0, 1400.0
    )


def test_processing_in_blocks_keeps_the_length():
    """The editor can be driven block by block."""
    editor = make_editor(mode="Gain")
    editor.set_region(0.30, 0.50, 700.0, 1400.0)
    signal = region_burst_signal()
    produced = 0
    for start in range(0, signal.shape[0], 4096):
        chunk = signal[start:start + 4096]
        produced += np.asarray(editor.process(chunk.copy())).shape[0]
    assert produced == signal.shape[0]
